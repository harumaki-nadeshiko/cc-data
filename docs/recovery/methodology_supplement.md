# 补充方法论：失败恢复、引导式提问、仓库管理、Agent 部署

## Q1: 中间阶段失败的补救与审核

### 问题
- 实现者（DeepSeek）不能审核自己的方案
- 审核者不能用 GPT-5（成本不可控）
- 但故障发生时必须有人做决策

### 方案：三层审核体系

```
┌─────────────────────────────────────┐
│  层级0: 自检 (实现者, 无外部调用)     │  ← 最便宜
│  - 编译错误: 读取 error 行 → 定位文件 │
│  - 最多 1 次自修复，失败 → 升级       │
├─────────────────────────────────────┤
│  层级1: 快速审核 (DeepSeek v4-Flash)  │  ← ~$0.01/次
│  - 检查编译失败是否由简单错误引起      │
│  - (缺失 include, 签名不匹配, etc.)    │
│  - 给出修复建议或升级理由              │
├─────────────────────────────────────┤
│  层级2: 用户决策 (人类)               │  ← 免费, 最可靠
│  - 层级0+1 均失败时升级到用户          │
│  - 用户判断: 接受方案修正 / 回退该层   │
│  - 这是唯一的「方案修正」授权点         │
├─────────────────────────────────────┤
│  层级3: 深度分析 (GPT-5/Opus)          │  ← 最贵, 备用
│  - 仅当用户要求时才调用               │
│  - 用于: 协议级死锁分析 / 状态空间遗漏 │
│  - 单次调用预算上限: $3                │
└─────────────────────────────────────┘
```

### 故障升级协议

```python
class FailureProtocol:
    def handle_compile_failure(self, layer, attempt_count):
        """
        layer: '3a'/'3b'/'3c'/'3d'/'3e'
        attempt_count: 当前层已尝试次数
        """
        if attempt_count == 0:
            # 层级0: 自检
            return self.auto_fix()  # DeepSeek 读错误自行修复
        elif attempt_count == 1:
            # 层级1: 快速审核
            review = task(
                subagent_type='general',  # DeepSeek Flash
                prompt=f'编译失败: {layer}, 错误日志: {errors}'
            )
            if review.suggests_fix:
                return self.apply_fix(review.fix)
            else:
                return self.escalate_to_user(review.reason)
        else:
            # 层级2: 用户决策
            return self.ask_user(f'''
            层 {layer} 编译失败, 已尝试 2 次修复无效。
            选项:
              A) 回退本层到 git commit, 重新分析方案
              B) 调用 GPT-5 做深度分析 ($3)
              C) 标注为 Open Question, 跳过本层继续
            你的选择?''')

    def handle_test_failure(self, tc_id, tc_priority, attempt_count):
        if tc_priority == 'P0':
            max_attempts = 2
        else:
            max_attempts = 1
        
        if attempt_count >= max_attempts:
            return self.mark_open_question(tc_id)
        # ... normal debug flow
```

### 方案修正的触发条件

**只有以下情况允许方案修正**（由用户授权）：

| 触发条件 | 修正范围 | 谁执行 |
|----------|---------|--------|
| 编译失败 2 次且快速审核无法修复 | 当前子阶段 | 用户 + GPT-5 |
| 测试失败 2 次（P0）或 1 次（P1+） | 相关文件 | 用户 + GPT-5 |
| 发现协议级死锁 | 全局方案 | GPT-5 深度分析 → 用户审核 |
| 发现状态空间遗漏 | 状态矩阵 | GPT-5 补充 → 用户审核 |

---

## Q2: 引导式提问方案 (Guided Questioning)

### 目标
不让大模型一次性输出方案，而是通过**结构化提问**引导用户补充和完善方案。

### 实现：三阶段提问法

#### 阶段 A: 模板填充阶段

```
系统提示:
「你是一个协议方案引导者。不要直接输出方案，而是通过一系列结构化的
问题来引导用户逐步完善设计。每次只问 3-5 个问题，问题应该聚焦在
单个决策点上，提供候选选项并分析每个选项的优劣。

输入材料包括:
- 基线代码 (c665e76a58)
- 恢复文档目录 (docs/recovery/)
- 设计流变对比 (design_drift.md)

你需要覆盖以下维度:
1. 数据结构: 每个结构体的字段、大小、生命周期
2. 消息流: 每种请求的完整路径 (src→dst→actions)
3. 状态机: 每个控制器的稳定态、transient 态、事件响应
4. 竞争窗口: 已知的并发风险点和缓解方案
5. 测试覆盖: 每个 TC 覆盖的协议路径」
```

**示例问题**：

```markdown
## 问题 3/25: EP-RNF SnpUnique 响应语义

当 EP-RNF 收到 SnpUnique 时，需要根据以下条件决定响应类型:
- retToSrc (单目标 retToSrc vs 多播)
- hasData (UBCC 全局 invalidate 是否收集到脏数据)
- isDirty (数据是否脏)

| 选项 | retToSrc=true | retToSrc=false |
|------|-------------|----------------|
| hasData=true, isDirty=true | SnpRespData_I_PD | SnpResp_I + SnpRespData_I_PD |
| hasData=true, isDirty=false | SnpRespData_I | SnpResp_I (仅控制) |
| hasData=false | SnpResp_I (回退) | SnpResp_I |

这个矩阵是否正确？如果不正确，请修正。
```

#### 阶段 B: 深度追问

当用户给出初步回答后，追问潜在的边缘情况：

```markdown
## 追问 3a: 并发风险

你确认的 SnpUnique 响应矩阵中，当 retToSrc=false + hasData=true + isDirty=true 时，
EP-RNF 发送 SnpResp_I + SnpRespData_I_PD。

但这里有一个并发窗口：EP-RNF 发送 SnpResp_I 后、SnpRespData_I_PD 发送前，
如果另一个节点发起了 WriteNoSnp 并更新了 DDR4，SnpRespData_I_PD 中的数据
是否可能已经是过时的？

请分析这个场景的时序并确定是否需要额外的串行化保护。
```

#### 阶段 C: 方案合成

在提问完成后（约 30-50 轮），模型输出最终方案文档。用户回答过的问题直接填充到方案中，未明确回答的标注为 `[DECISION_PENDING]`。

### 成本控制

```
每轮提问: ~500 tokens 输出 → ~$0.005 (DeepSeek v4) 或 ~$0.05 (GPT-5)
30-50 轮总计: $0.15-2.50

仅在阶段 C 方案合成时使用 GPT/Opus: ~$3-5/次
总引导方案拟定成本: < $8
```

---

## Q3: 仓库管理与 submodule 同步

### 仓库结构抉择

| 方案 | 优点 | 缺点 |
|------|------|------|
| **A: gem5 fork** | 简单, 直接改 | 与上游合并困难, 测试文件无处放 |
| **B: gem5 submodule** ✅ | 主仓存测试/文档, 干净分离 | 需要严格同步机制 |

**推荐方案 B**。理由：测试文件和设计文档与 gem5 代码解耦，可以独立版本化。

### 从零开始的仓库方法论

#### 3.1 初始设置

```bash
# 1. 创建主仓库
mkdir cc-ep && cd cc-ep && git init
git remote add origin git@github.com:your-org/cc-ep.git

# 2. 添加 gem5 为 submodule
git submodule add -b v25.1.0.0 git@github.com:your-org/gem5-fork.git gem5
cd gem5
git remote add upstream https://github.com/gem5/gem5.git

# 3. 创建项目的 commit 基线
cd gem5 && git checkout -b ep-dev
# （你的第一个修改 commit）

cd .. && git add gem5 tests/ docs/ tools/
git commit -m "init: gem5 submodule + test infra + design docs"

# 4. 推送到远程
git push origin main
```

#### 3.2 submodule 同步强制规则

**核心原则**：gem5 的每次编译通过 = 一次 commit。主仓库的子模块指针自动跟随。

```bash
# === 每次编译通过后必须执行的同步流程 ===
# Step 1: 在 gem5 子模块中 commit
cd gem5
git add -A
git commit -m "phase3X: <description>"
GEM5_COMMIT=$(git rev-parse HEAD)

# Step 2: 回到主仓库，更新 submodule 指针
cd ..
git add gem5  # 这会更新 .gitmodules 中的 submodule hash
git add tests/ docs/ tools/  # 如果测试/文档有改动
git commit -m "phase3X: submodule $GEM5_COMMIT + tests"

# Step 3: 同时推送到两个仓库
cd gem5 && git push origin ep-dev && cd ..
git push origin main
```

#### 3.3 防破坏机制

```bash
# === 主仓库 pre-commit hook (不可跳过) ===
# 文件: .git/hooks/pre-commit (或 .githooks/pre-commit)
#!/bin/bash
# 检查 gem5 子模块是否有未提交的改动

cd gem5
if ! git diff --quiet HEAD 2>/dev/null; then
    echo "❌ ERROR: gem5 submodule has UNCOMMITTED changes!"
    echo "   Please commit gem5 changes FIRST, then commit the main repo."
    echo "   Use: cd gem5 && git add -A && git commit -m '...' && cd .. && git add gem5"
    exit 1
fi

if ! git diff --cached --quiet 2>/dev/null; then
    echo "❌ ERROR: gem5 submodule has STAGED but uncommitted changes!"
    exit 1
fi

echo "✅ gem5 submodule is clean, proceeding with commit."
exit 0
```

**启用**: `git config core.hooksPath .githooks`

#### 3.4 灾难恢复流程

```
如果出现 gem5 working tree 修改丢失:
  1. 不要 panic
  2. git log --oneline gem5  # 查看子模块的所有 commit
  3. cd gem5 && git reset --hard <last_good_commit>
  4. cd .. && git submodule update
  
如果连 git log 都没有（从未 commit 过 gem5）:
  1. 停止所有操作
  2. 检查 git stash list
  3. 检查 reflog: cd gem5 && git reflog
  4. 如果都没有: 从对话历史恢复（我们有完整 JSONL 存档）
```

#### 3.5 .gitmodules 模板

```ini
[submodule "gem5"]
    path = gem5
    url = git@github.com:your-org/gem5-fork.git
    branch = ep-dev
```

### 规则清单

- ✅ **每条 commit message 必须包含**: `phase identifier + submodule commit hash`
- ✅ **编译通过后 立即 commit** —— 保护工作成果
- ✅ **禁止在主仓库中直接修改 gem5 文件** —— 一切通过子仓库
- ✅ **测试文件和设计文档放在主仓库** —— `tests/`, `docs/`
- ❌ **禁止在 gem5 working tree 中 `git checkout -- .` 不先确认** —— 需要用户明确授权
- ❌ **禁止 `git clean -fd` 不先 `git stash` 备份**

---

## Q4: Opencode Agent 部署与开发方法论

### 4.1 Agent 目录结构

```
~/.config/opencode/agents/          # 全局 agent (跨项目可用)
  ├── agent-flash.md                # DeepSeek v4-Flash, thinking=default
  └── agent-pro.md                  # DeepSeek v4-Pro, thinking=max

cc-ep/.opencode/agents/             # 项目 agent (本项目管理)
  ├── cache-coherence-implementer.md # 协议实现专用
  ├── strict-task-completion-reviewer.md # 严格审核专用
  ├── coder-validator-orchestrator.md # 编排调度专用
  ├── flash-scanner.md              # 快速扫描 (继承 agent-flash 模板)
  ├── pro-designer.md               # 方案设计 (继承 agent-pro 模板)
  └── build-runner.md               # 编译运行专用 agent
```

### 4.2 四个项目 agent 的完整定义

**build-runner.md** (编译/测试执行专用，DeepSeek v4):
```
---
description: >-
  编译与测试执行 agent。使用 Docker 编译 gem5，运行 E2E 测试，
  并报告结果。不修改代码，只做执行和结果整理。
mode: subagent
model: opencode-go/deepseek-v4-pro
---

系统提示:
"你是编译与测试执行员。你的唯一职责是:
1. 在 Docker 中编译 gem5
2. 运行指定的 E2E test case
3. 收集编译错误和测试输出
4. 返回结构化结果

命令模板:
- 编译: docker run --rm -v /mnt/data2/cgc/cc-ep:/workspace ubcc-dev:ubuntu20.04 \
    bash -c 'cd /workspace/gem5 && scons build/ARM/gem5.opt -j32 2>&1 | tail -30'
- 测试: docker run --rm -v /mnt/data2/cgc/cc-ep:/workspace ubcc-dev:ubuntu20.04 \
    bash -c 'timeout 60 /workspace/gem5/build/ARM/gem5.opt \
    --outdir=/workspace/m5out/e2e/tcN /workspace/tests/e2e/test_e2e.py --tc=N 2>&1'
- 测试输出: cat /workspace/m5out/e2e/tcN/simout_n*

输出格式:
{
  'build': {'status': 'pass'|'fail', 'errors': [...], 'warnings': [...]},
  'test': {'tc_id': N, 'status': 'pass'|'fail'|'deadlock'|'crash',
           'simout': '...', 'key_lines': [...]}
}

不要做任何推理或分析，只执行和报告。"
```

**flash-scanner.md** (快速扫描，DeepSeek v4-Flash):
```
同 agent-flash，但 prompt 针对项目定制:
- 知道 gem5/ 目录结构
- 知道测试文件位置
- 知道常用错误模式 (编译错误、死锁、panic)
```

**pro-designer.md** (方案设计，DeepSeek v4-Pro，thinking=max):
```
继承 agent-pro，但添加:
- 知道 CHI 协议规范和 gem5 CHI 实现细节
- 存取 docs/recovery/ 目录的所有文档（自动读取为上下文）
- 输出格式: scheme_v4.md 的指定模板
```

### 4.3 开发流程中的 agent 调用拓扑

```
阶段1: 方案拟定
  ┌─────────────┐
  │   用户       │ ← 回答引导式提问
  └──────┬──────┘
         │ 对话
  ┌──────▼──────┐
  │ pro-designer │ ← DeepSeek v4-Pro (thinking=max)，执行引导提问
  │ (方案设计)   │    调用 GPT-5 仅用于阶段 C 最终合成
  └──────┬──────┘
         │ 产出 scheme_v4.md

阶段3: 批量实现
  ┌─────────────┐
  │   用户       │ ← 批准分层计划
  └──────┬──────┘
         │ dispatch
  ┌──────▼──────────┐    编译     ┌──────────────┐
  │ cache-coherence │ ──────────→ │ build-runner │
  │ -implementer   │ ←────────── │ (编译执行)    │
  │ (代码实现)      │   结果      └──────────────┘
  └──────┬──────────┘
         │ 编译通过         │ 编译失败(1次)    │ 编译失败(2次+)
         ▼                  ▼                  ▼
  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
  │ build-runner │  │ flash-scanner│  │   用户        │
  │ (运行测试)    │  │ (错误分析)    │  │ (决策升级)    │
  └──────────────┘  └──────────────┘  └──────────────┘
```

### 4.4 成本优化的 agent 选择矩阵

| 场景 | Agent | 模型 | 成本/次 |
|------|-------|------|---------|
| 编译 | build-runner | DeepSeek v4 | $0.01 |
| 运行测试 | build-runner | DeepSeek v4 | $0.02 |
| 代码实现 (单层) | cache-coherence-implementer | DeepSeek v4-Pro | $0.50 |
| 编译错误分析 | flash-scanner | DeepSeek v4-Flash | $0.01 |
| 方案引导提问 (每轮) | pro-designer | DeepSeek v4-Pro | $0.05 |
| 方案最终合成 | GPT-5 (via user) | GPT-5.5 | $3-5 |
| 深度死锁分析 | GPT-5 (via user) | GPT-5.5 | $3 |

---

## Q5: 入口文档 (Entry Document)

### 设计

入口文档是一个**自包含的、包含所有上下文信息的单一 Markdown 文件**。
强大模型只需读取这一个文件，就可以开始方案拟定，无需读取任何外部文件。

### 内容结构

```markdown
# CC-EP: 跨节点缓存一致性 — 方案拟定入口

## 1. 项目概述
- 目标: 在 gem5 上实现多节点分布式共享内存 (DSM) 的缓存一致性协议
- 架构: Node → HN-F → EP-SNF → EPBackend → UBCC → UBCC Interconnect
- 节点数: 3 (可配置)
- PA 布局: PHY_BASE_i = i << 40, 每节点 5 个 SEG (LocalPrivate, UbccExcl, DSM_0/1/2)

## 2. 关键组件

[精简的组件描述，每个组件一段]

### 2.1 HN-F (Home Node Forward)
- SLICC 自动生成的 CHI 缓存控制器 (L3 级别)
- 扩展: epRnfMachineVersion → tbe.epRnfMachineID
- 关键 action: RegisterEPRNF_OnSharedHint, pickSharerForSnoop, DCT fallback

### 2.2 EP-RNF (External Proxy — Request Node Forward)
- 自定义 C++ 控制器，实现 CHI RN-F 语义
- 接口: reqOut (→ HN-F), snpIn (← HN-F)
- 方法: sendChiRequest, recvSnoopMsg, startReadShared/ReadOnce/CleanUnique/ReadUnique

### 2.3 EP-SNF (External Proxy — Slave Node Forward)
- 自定义 C++ 控制器，接收 HN-F 的 ReadNoSnp/WriteNoSnp 并路由到 UBCC

### 2.4 UBCCController (跨节点目录)
- 纯 C++ 单例，每个节点一个实例
- DirEntry: state, ownerNode, sharersMask, epoch, dirty, pendingOp, pendingOwnerUpdate
- OutstandingRequest: 延迟提交模型，管理 recall/invalidation/grant 事务

### 2.5 EPBackend
- EP-RNF 和 EP-SNF 的共享引擎
- 处理 cross-node PA 转换 (NodeAddressMap)
- 方法: handleRemoteMiss, handleRecallRequest, populateGrantData, globalInvalidate

## 3. 基线代码状态 (c665e76a58)

[列出每个关键文件在基线中的状态，以及需要替代的内容]

| 文件 | 基线状态 | 需要替代的内容 |
|------|---------|--------------|
| CHI-cache-actions.sm | 原始 SLICC, 无 epRnfMachineID | 添加 RegisterEPRNF, pickSharer, DCT fallback |
| EPRNFController.cc | 存在骨架, sendLocation Snoop 已废弃 | 完整的 recvSnoopMsg 分发, start* 方法 |
| UBCCController.cc | OutstandingRequest 骨架 | globalInvalidate, updateOwner, clearPendingOwnerUpdate |
| EPBackend.cc | 部分方法已实现 | handleRecall data capture, populateGrantData 重写 |

## 4. 协议语义总结

[精简版的协议规范]

### 4.1 EP-RNF 注册链路
CompData from EP-SNF (shared_hint=true) → HN-F (RegisterEPRNF_OnSharedHint) → dir_sharers.add(epRnf)

### 4.2 Snoop 处理矩阵
[表格: snoop type × (retToSrc, hasData, isDirty) → response type]

### 4.3 数据路径
- Read: HN-F → ReadNoSnp → EP-SNF → UBCC → Grant → CompData 返回
- Write: HN-F → ReadUnique → ReadNoSnp → EP-SNF → UBCC(G_M) → NCBWrData → 写入 HOME DDR4

### 4.4 已知竞争窗口
1. TBE allocation + CompData 同 tick 竞争
2. pendingOwnerUpdate barrier 生命周期
3. GRANT_HANDSHAKE OutstandingRequest 泄漏

## 5. 前一版本的教训

[精简版教训]

- 不要使用 ReadOnce (用户明确拒绝)
- pendingOp timer 值不要再调整 (直接使用 OutstandingRequest 状态机)
- Self-test 默认 disable (与 workload 冲突)
- deadlock_threshold = 20000000 (整数, 不是 "10ms" 字符串)
- alloc_on_readunique = True (有 EP-RNF 在 dir_sharers 后可以安全启用)

## 6. 测试矩阵

[表格: TC → 覆盖的协议路径 → 优先级 → 预期难度]

## 7. 你的任务

你是 GPT-5 / Claude Opus。请基于以上全部信息，执行以下任务:

### 任务 A: 引导式提问 (不要输出方案)
请针对以下维度的未明确部分，向我提出 3-5 个引导性问题:
- 数据结构完整性和大小约束
- 消息流的边界情况
- 状态机的 concurrency 分析
- 测试覆盖的完整度

### 任务 B: 输出精确的方案
在我回答完所有问题后，请按以下格式输出完整方案:
...

## 附录: 所有修改文件的基线状态

[每个文件的当前状态 (行号 + 关键代码段)]
```

### 入口文档大小预估

| 章节 | 行数 |
|------|------|
| 项目概述 | ~20 |
| 关键组件 | ~80 |
| 基线代码状态 | ~60 |
| 协议语义总结 | ~100 |
| 前一版本的教训 | ~30 |
| 测试矩阵 | ~30 |
| 附录: 文件基线状态 | ~200 |
| **总计** | **~520 行** |

约 15-20K tokens → 适合 GPT-5/Opus 一次性读取
