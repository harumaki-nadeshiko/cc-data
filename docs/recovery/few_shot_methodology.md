# 方案拟定→开发→测试 成本可控方法论

## 0. 现状评估

### 0.1 历史教训

上一轮开发（Jun 1-12）暴露的核心问题：

| 问题 | 表现 | 根因 |
|------|------|------|
| **大量无效迭代** | 7 个实验性修改回退，12 个文件反复摇摆 | 边改边测，缺乏预先的协议状态空间分析 |
| **昂贵的编译-测试循环** | 14 万秒+构建测试（估计） | 每次修改后立即触发 `scons build/ARM/gem5.opt + 跑 TC` |
| **设计流失** | 20 个设计主题中 8 个过时/未覆盖 | 设计文档未及时更新，后期实现偏离 |
| **灾难性回退** | `git checkout -- .` 导致 2447 行蒸发 | 未提交代码 + 破坏性操作未确认 |
| **盲目的恢复尝试** | 二分回溯、全量重放均失败 | 裸 commit 本身不完整，无法零改重建 |

### 0.2 当前资产

| 资产 | 位置 | 质量 |
|------|------|------|
| 设计文档 v3.2 | `docs/ep-rnf-sharer-registration-plan.md` | 9/20 仍然有效，需更新 |
| Phase 0-10 恢复计划 | `docs/recovery/phase_plan.md` | 完整的按文件修改列表 |
| 决策记录 | `docs/recovery/decisions.md` | 25 个关键决策的上下文和理由 |
| 设计流变对比 | `docs/recovery/design_drift.md` | 文档 vs 实现的差异分析 |
| 修改目录 | `docs/recovery/catalog.md` | 39 个文件的修改时间线 |
| 工作流审计 | `docs/recovery/env_workflow_audit.md` | 环境规则及违规记录 |
| 测试基线 | `gem5@c665e76a58` | 不含 Phase 0-4 实现，但有 OutstandingRequest/TBE 骨架 |

### 0.3 开发开销模型

```
单次迭代耗时 = 编辑代码 (30s) + 编译 (3-5min) + 运行测试 (30s-2min) ≈ 5-8min/iteration
总迭代次数 (上一轮) ≈ 347 次决策触发 → 139 次 UBCCController 编辑 → 估计 200+ 次编译-测试循环
总耗时 ≈ 200 × 6min ≈ 20 小时纯编译-测试开销
预编译发现的问题 (通过更严谨的方案) ≈ 可避免 60-80% 的迭代
```

## 1. 四阶段方法论

```
阶段1: 方案拟定 (GPT-5/Opus, ~3轮)  → 产出: 精确的方案文档
阶段2: 状态空间分析 (GPT-5/Opus, ~2轮) → 产出: 状态转移矩阵
阶段3: 批量实现 (DeepSeek, ~5轮编译)  → 产出: 编译通过的二进制
   3a: 基础设施 → 编译 (1次)
   3b: SLICC 协议 → 编译 (1次)  
   3c: EP 控制器 → 编译 (1次)
   3d: UBCC/EPBackend → 编译 (1次)
   3e: 集成测试 → 运行测试 (1次)
阶段4: 验证测试 (DeepSeek, ~2轮)     → 产出: 通过所有 TC 的二进制
```

### 1.1 阶段1: 方案拟定（强化模型，离线分析）

**目标**：产出一份无歧义、可逐文件执行的精确方案文档。

**输入**：
- 现有设计文档 v3.2 和恢复文档
- 设计流变对比（避免重蹈覆辙）
- 最终的 `c665e76a58` 基线代码

**模型选择**: GPT-5.5 或 Claude Opus 4.x（推理能力最强，适合协议状态空间分析）

**产出要求**：

```markdown
## scheme_v4.md

### 1. 架构总览
- 完整的节点拓扑图 (Node→HN-F→EP-SNF→EPBackend→UBBC→UBBC Link)
- 消息流图 (ReadShared/ReadUnique/CleanUnique/WriteNoSnp 的所有路径)
- 数据结构定义 (DirEntry, OutstandingRequest, Config params)

### 2. 按文件精确修改清单
每个文件：
  - 当前的基线状态 (行号 + 内容)
  - 需要修改为的状态 (完整 diff)
  - 修改意图和协议语义
  - 该修改依赖的其他文件修改

### 3. 协议状态转移矩阵
- HN-F 所有状态 × 所有事件 → 下一状态 + 动作
- EP-RNF 所有 snoop 类型 × (retToSrc, hasData, isDirty) → 响应类型
- UBCC MESI 所有状态 × 请求类型 → 授权 + 新状态

### 4. 测试矩阵
- 每个 TC 覆盖的协议路径
- 预期通过的 TC 列表及优先级
```

### 1.2 阶段2: 状态空间分析（强化模型，离线验证）

**目标**: 在写任何一行代码之前，穷尽协议状态空间，发现所有潜在问题。

**方法**：
1. 列出所有控制器的所有稳定态和 transient 态
2. 枚举所有可能的事件组合（包括并发事件）
3. 追踪每个 (state, event) 对应的 actions 和 next_state
4. 标记可能的非法状态转移、死锁窗口、数据竞争

**产出**：
- 完整的 `(controller, state, event) → (next_state, actions)` 矩阵
- 已知的危险窗口 (如 TBE allocation + CompData 同 tick 竞争)
- 已知的不变量 (如 pendingOwnerUpdate 生命周期)

### 1.3 阶段3: 批量实现（DeepSeek，分层编译）

**目标**：用最少的编译次数完成全部代码修改。

**分层策略**：

| 子阶段 | 文件类别 | 文件数 | 预估修改行 | 编译次数 |
|--------|----------|--------|-----------|---------|
| 3a: 基础设施 | Config (.py), CHI params (.sm), test files | 5 | ~50 | 1 |
| 3b: SLICC 协议 | CHI-cache-actions/funcs/transitions.sm, CHI-msg.sm | 4 | ~300 | 1 |
| 3c: EP 控制器 | EPRNFController.cc/hh, EPSNFController.cc/hh | 4 | ~500 | 1 |
| 3d: 后端逻辑 | EPBackend.cc/hh, UBCCController.cc/hh | 4 | ~800 | 1 |
| 3e: 集成 | 验证编译 + 运行 TC1 | — | — | 1 |

**关键原则**：
- 每个子阶段内所有文件**一次修改**、**一次编译**
- 编译失败时，**不允许逐文件调试**——必须回退整个子阶段的修改，重新分析依赖后**整体重做**
- 每个子阶段编译通过后**立即 commit**（保护工作成果）

**每层验证**：
```bash
# 3a 完成后验证: params 加载成功，Ruby 系统可创建
docker run --rm -v $(pwd):/workspace ubcc-dev:ubuntu20.04 \
  bash -c 'cd /workspace/gem5 && scons build/ARM/gem5.opt -j32 2>&1 | tail -3'

# 3b 完成后验证: SLICC 自动生成代码正确
# 3c 完成后验证: 编译通过 + EP 控制器对象创建成功  
# 3d 完成后验证: 编译通过 + UBCC 初始化成功
# 3e 完成后验证: TC1 PASS (单节点本地读写)
```

### 1.4 阶段4: 验证测试（DeepSeek，增量测试）

**目标**：按优先级逐步测试，每个失败最多 2 次修复尝试。

**测试优先级**：

| 优先级 | TC | 覆盖协议路径 | 预期难度 |
|--------|-----|-------------|---------|
| P0 | TC1 | 本地 DSM 读写 | 最低 |
| P0 | TC2 | 跨节点 Remote Read | 中 |
| P0 | TC5 | Single Writer | 中 |
| P1 | TC6 | Multi Sharer | 中高 |
| P1 | TC7 | Writeback/Evict | 中高 |
| P1 | TC11 | Local Upgrade | 高 |
| P2 | TC3 | Ping Pong | 最高 |
| P2 | TC8 | Upgrade Invalidate | 最高 |
| P2 | TC4 | Three Node Ring | 最高 (时序敏感) |

**失败处理协议**：
```
For P0 测试失败:
  1. 读取 simout/simerr 日志
  2. 定位到具体的协议路径 (哪个 controller, 哪个状态, 哪个消息)
  3. 修复 ← 最多 2 次 (2 次未解决 → 标注为 Open Question, 继续其他 P0)
  
For P1/P2 测试失败:
  最多 1 次修复尝试 (1 次未解决 → 标注为 Open Question, 继续)
```

## 2. 模型使用策略

| 任务 | 模型 | 原因 | 预估成本 |
|------|------|------|---------|
| 方案拟定 (阶段1) | GPT-5.5 / Opus 4.6 | 协议状态空间穷举需最强推理 | ~$3-5/轮 × 3轮 |
| 状态分析 (阶段2) | GPT-5.5 / Opus 4.6 | 同上，需系统性思维 | ~$2-3/轮 × 2轮 |
| 编译修复 (阶段3 失败时) | GPT-5.5 / Opus 4.6 | 编译错误诊断需跨文件分析 | ~$1/次 (仅失败时) |
| 代码实现 (阶段3) | DeepSeek v4 | 按照方案精确执行即可 | ~$0.5/层 × 5层 |
| 测试调试 (阶段4) | DeepSeek v4 | 日常模式下处理 | ~$0.5/失败 |
| 批量分片扫描 | DeepSeek v4-Flash | 高吞吐扫描 | 极低 |

**GPT/Opus 总使用预估**：
- 阶段1: 3 轮方案打磨 = ~$15
- 阶段2: 2 轮状态分析 = ~$6
- 阶段3 失败修复 (预估 2-3 次) = ~$3
- **总计: ~$25**

## 3. 风险控制

### 3.1 已知风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 编译依赖错误 | 60% | 中型 (需回退分析) | 分层 build + commit 保护 |
| 协议死锁 | 40% | 高 (需深入调试) | 阶段2 状态空间分析提前发现 |
| TBE 竞争 | 30% | 高 | 已知根因，阶段2 显式标注 |
| alloc_on_readunique 决策重演 | 20% | 中 | 已在决策文档中记录 |

### 3.2 最大尝试次数

| 操作 | 最大次数 | 超出后 |
|------|---------|--------|
| 单层编译 | 2 次 | 回退整层，重新分析 |
| P0 测试修复 | 2 次 | 标注 Open Question |
| P1/P2 测试修复 | 1 次 | 标注 Open Question |
| 方案打磨 | 3 轮 | 以当前版本启动实施 |

## 4. 执行命令模板

### 编译（仅做一次编译检查，不带测试）
```bash
docker run --rm -v $(pwd):/workspace ubcc-dev:ubuntu20.04 \
  bash -c 'cd /workspace/gem5 && scons build/ARM/gem5.opt -j32 2>&1 | grep -E "error:|LINK|done building" | head -20'
```

### 测试（单个 TC，timeout 60 秒）
```bash
docker run --rm -v $(pwd):/workspace ubcc-dev:ubuntu20.04 \
  bash -c 'timeout 60 /workspace/gem5/build/ARM/gem5.opt \
    --outdir=/workspace/m5out/e2e/tcN \
    /workspace/tests/e2e/test_e2e.py --tc=N 2>&1 | \
    grep -E "PASSED|FAILED|panic|deadlock|READ_VAL|BEFORE|AFTER"'
```

### 保护性提交（每个子阶段编译通过后）
```bash
cd gem5 && git add -A && git commit -m "phase3X: <layer description>" && cd -
```

### 回退到上一个子阶段
```bash
cd gem5 && git reset --hard HEAD~1 && cd -
```

## 5. 成功标准

| 里程碑 | 标准 | 估值编译次数 |
|--------|------|------------|
| M1: 方案就绪 | scheme_v4.md 通过审核 | 0 |
| M2: 状态空间验证 | 矩阵无已知死锁/竞争 | 0 |
| M3: 基础设施编译 | config + params 可加载 | 1 |
| M4: SLICC 编译 | 自动生成代码正确 | 1 |
| M5: EP 层编译 | EP 控制器编译通过 | 1 |
| M6: 后端编译 | UBCC/EPBackend 编译通过 | 1 |
| M7: TC1 PASS | 单节点本地 DSM 读写 | 1 |
| M8: P0 全部 PASS | TC1/2/5 通过 | 1-2 |
| M9: 综合通过 | 所有 TC 通过或已标注 | 1-2 |

**目标总编译次数: ≤8 次**  
**目标总测试次数: ≤15 次**

## 6. 下一步行动

1. **更新设计文档** — 基于 `design_drift.md` 更新 `ep-rnf-sharer-registration-plan.md` 到 v4.0
2. **启动阶段1** — 用 GPT-5/Opus 进行方案拟定（输入: 更新后的 plan v4.0 + recovery 文档集）
3. **阶段2分析** — 同一模型进行状态空间穷举
4. **阶段3实施** — DeepSeek v4 分层实现
5. **阶段4验证** — 增量测试 + 标注 Open Question
