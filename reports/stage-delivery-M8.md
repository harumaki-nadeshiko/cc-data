# M8 阶段交付报告

- **阶段：** M8 — 共享读取加固与升级/失效闭环
- **状态：** PASS
- **完成日期：** 2026-05-27
- **审查轮次：** 3（初次 + 修复轮 + 最终修复）
- **编排器判定：** PASS

---

## 1. 阶段摘要

### 1.1 阶段目标

将共享读取路径从"能工作"加固到"在所有边界情况下可验证正确"：多 sharer mask 维护、本地升级触发对远程 sharer 的 `GlobalInvalidate`、grant 前的 ack 收集、默认启用共享路径（不依赖 `force_grant_m`）。

### 1.2 完成状态

| 标准 | 结果 |
|---|---|
| 多 sharer mask 正确维护 | PASS |
| 本地升级遇到外部 sharer → `GlobalInvalidate` | PASS |
| 远程 sharer ack 已收集 → 本地 unique 完成 | PASS |
| 默认启用共享路径 | PASS |
| `force_grant_m` 仅作为调试开关 | PASS |
| 两个请求者可同时持有共享 | PASS |
| SharerMask 正确性已验证 | PASS |

### 1.3 审查轮次

| 轮次 | 日期 | 关键发现 | 解决方案 |
|---|---|---|---|
| R1（初次） | 2026-05-27 | 完整 M8 实现已提交 | 等待 validator 审查 |
| 修复轮 | 2026-05-27 | P0-1：用于失效的 home epoch；P0-2：无操作重新进入返回（幂等失效 ack）；P1-4：ackNode 边界检查（防止越界节点 ID） | 所有 P0/P1 已解决 |
| 最终修复 | 2026-05-27 | ackNode 边界检查重新定位到 `processInvalidationAck` 中所有提前返回之前的入口处 | 最终 commit |

---

## 2. 代码变更

### 2.1 gem5 子模块

| 文件 | 变更 | 描述 |
|---|---|---|
| `src/mem/ruby/protocol/chi/ep/UBCCController.hh` | 扩展 | `processOuterRequest()` 扩展用于失效触发路径；`processInvalidationAck()` 用于收集每个 sharer 的 ack；`GlobalInvalidate` 消息类型；`pendingInvalidations` 集合用于跟踪未完成的 ack；`ackNode` 边界验证 |
| `src/mem/ruby/protocol/chi/ep/UBCCController.cc` | 扩展 | **GlobalInvalidate 流程**：当升级请求（`GlobalReadUnique`）遇到 `G_S` 状态时，home UBCC 向每个 sharer（请求者除外）发送 `GlobalInvalidate` → 每个 sharer 的 EP_RNF 失效本地副本 → 向 home 发回 ack → `processInvalidationAck()` 收集 ack → 当所有 ack 收到后，home 继续向请求者进行 grant。**Home epoch**：失效与 home epoch 关联，用于过期 ack 过滤。**重新进入保护**：对同一 sharer 的重复/重试 ack 被接受为无操作（幂等）。**SharerMask 管理**：mask 在 grant（添加请求者）、驱逐（移除 sharer）、失效（移除 sharer）、降级（owner → sharer）时正确更新。**共享默认路径**：`GlobalReadShared` + `writeIntent=false` → `GlobalGrantShared`（默认）；无无条件 `GrantM` 回退 |
| `src/mem/ruby/protocol/chi/ep/EPBackend.hh` | 扩展 | `handleInvalidate()`：接收来自 home 的失效，通过 EP_RNF 触发本地失效；`handleInvalidationAck()`：向 home 发回 ack；失效计数器；`inspectSharerMaskForTest()` |
| `src/mem/ruby/protocol/chi/ep/EPBackend.cc` | 扩展 | 失效处理：home 触发 `GlobalInvalidate` → sharer EPBackend 失效本地行 → 发送 ack → home `processInvalidationAck` |
| `src/mem/ruby/protocol/chi/ep/M8SelfTest.cc` | 新增 | 61 个三元检查：TC-M8-1 两个请求者持有共享（9 个检查：两者均添加到 sharers mask、两者均可读取、并发共享访问）、TC-M8-2 本地升级失效 sharers（跨 3 个子场景的 24 个检查：共享→升级失效流程、所有 sharer 被失效、ack 收集、G_S→G_E/M 转换）、TC-M8-3 共享默认路径（7 个检查：Shared 请求 → GrantShared 而非 GrantModified、默认无 force_grant_m）、TC-M8-4 sharerMask 正确性（10 个检查：添加/移除 sharers、并发添加、最大 mask、空 mask→移除条目）、加 busy-line 检查、ackNode 边界、挂起失效生命周期测试 |

**gem5 commit 历史（M8 相关）：**

| Commit | 描述 |
|---|---|
| `4a9a672335` | M8 修复轮：P0-1 用于失效的 home epoch、P0-2 无操作重新进入返回、P1-4 ackNode 边界检查、添加 M8SelfTest.cc |
| `ad782435d6` | M8：在 `processInvalidationAck` 中所有提前返回之前将 ackNode 边界检查移至入口处（P1-4） |
| `d1f6ec4947` | M8 修复：在目录查找之前移动 ackNode 边界检查 |

### 2.2 超项目

| 文件 | 变更 | 描述 |
|---|---|---|
| `tests/phase8/test_shared_hardening.py` | 新增 | PY_INJECT harness：完整 CHI+UBCC 拓扑，在实例化时运行 M4/M5/M6/M7/M8 所有自检，捕获 C++ stdout，解析所有五个阶段的 PASS/FAIL，回归门控（M4/M5/M6/M7 失败阻止 M8），所有 4 个 M8 测试用例的测试用例覆盖 |

**超项目 commit 历史：**

| Commit | 描述 |
|---|---|
| `16c1780` | M8 修复轮：P0-1/P0-2/P1-4 修复；添加 tests/phase8/test_shared_hardening.py；更新 gem5 子模块 |
| `1ae8c4a` | M8：更新 gem5 子模块（ackNode 边界检查重新定位） |
| `6e966e6` | M8 修复：更新 gem5 子模块（ackNode 边界） |

---

## 3. 与原计划差异

### 3.1 与 `plan/03-phase-plan.md` 的对齐

| 计划 | 实际 | 备注 |
|---|---|---|
| 多 sharer mask 正确维护 | 已完成 | `sharersMask`（64 位）在 grant/添加/移除/失效时原子更新 |
| 本地升级遇到外部 sharer → `GlobalInvalidate` | 已完成 | Home UBCC 检测 `G_S` + `GlobalReadUnique` → 向每个 sharer 发送 `GlobalInvalidate` |
| 远程 sharer ack 已收集 → 本地 unique 完成 | 已完成 | `pendingInvalidations` 集合；`processInvalidationAck()` 递减；仅在所有 ack 后 grant |
| 默认启用共享路径 | 已完成 | `GlobalReadShared` → `GlobalGrantShared`；无无条件 `GrantM` 重新路由 |
| `force_grant_m` 仅调试 | 已完成 | 保留但非默认；MESI 正确路径是主要的 |
| 两个请求者同时共享 | 已完成 | 两者均在 sharers mask 中；两者均收到 `GrantShared` |
| 升级正确失效其他 sharers | 已完成 | 失效流程在 M8SelfTest 子场景中证明 |

### 3.2 关键设计决策

| 决策 | 理由 |
|---|---|
| 用于失效的 Home epoch | 防止过期失效 ack 污染同一行上的新事务 |
| 幂等失效 ack（无操作重新进入） | 如果对已计数的 sharer 到达重试/重复 ack，它被接受为无操作 — 防止消息重放导致的死锁 |
| 入口处的 `ackNode` 边界检查 | 保护 `sharersMask` 中越界节点 ID；在所有提前返回之前检查以尽早捕获错误 |
| `G_S` + `GlobalReadUnique` → grant 前失效 | 序列化升级：首先失效所有 sharers，然后向请求者 grant exclusive/modified |
| SharerMask 空时自动清理 | 当最后一个 sharer 被移除时（通过驱逐或失效），目录条目被清理 |

### 3.3 GlobalInvalidate 流程

```
请求者发送 GlobalReadUnique → home UBCC（处于 G_S 状态）
  → home 将行标记为 G_BUSY，设置 pendingOp=INVALIDATE
  → home 计算目标 = sharersMask & ~(1 << 请求者)
  → 对每个目标：发送 GlobalInvalidate
    → 目标 EPBackend.handleInvalidate()
      → EP_RNF 失效本地副本
      → 发送带 home epoch 的 ack
    → home processInvalidationAck(请求者, epoch)
      → 验证 epoch 匹配当前事务
      → 从 pendingInvalidations 集合中移除 sharer
      → 如果 pendingInvalidations 为空：完成 grant
  → 向请求者 grant GlobalGrantExclusive/Modified
  → 更新目录：state = G_E 或 G_M，sharersMask = 0
```

### 3.4 范围边界

| 范围内（已实现） | 尚未实现 |
|---|---|
| 多 sharer 共享访问 | — |
| 升级 → 失效 → Grant | — |
| 共享路径默认 | — |
| SharerMask 完整生命周期 | — |
| AckNode 边界保护 | — |

### 3.5 与 `plan/02-external-proxy-spec.md` 的一致性

| 规格要求 | 实现 | 状态 |
|---|---|---|
| 本地升级失效外部 sharers（§7.3） | Home 检测 `G_S` + `GlobalReadUnique` → `GlobalInvalidate` → 等待 acks → grant | PASS |
| 远程 sharer ack 收集后在 unique 之前（§7.3） | `pendingInvalidations` 集合；grant 被所有 acks 收到的条件门控 | PASS |
| 默认启用共享路径（§9.1） | `GlobalReadShared` → `GlobalGrantShared` | PASS |
| 多 sharer mask 维护（§9.1） | 64 位 `sharersMask` 含位添加/移除操作 | PASS |

---

## 4. 测试用例

### 4.1 TC-M8-1：两个请求者持有共享

| 属性 | 值 |
|---|---|
| **ID** | TC-M8-1（M8-1-1 到 M8-1-9） |
| **名称** | 两个请求者持有共享 |
| **类型** | PY_INJECT（C++ 自检） |
| **断言数** | 9 |
| **预期** | 第一个请求者添加到 sharers mask，收到 Shared grant；第二个请求者也添加，收到 Shared grant；两者同时在 mask 中；目录状态保持 `G_S`；无 owner；dirty=false |
| **实际** | PASS |
| **负面测试** | 不在 exclusive/modified 状态；未设置 owner 字段 |

### 4.2 TC-M8-2：本地升级失效其他 Sharers

| 属性 | 值 |
|---|---|
| **ID** | TC-M8-2（M8-2a-1..2、M8-2b-1..14、M8-2c-1..8） |
| **名称** | 本地升级失效其他 Sharers |
| **类型** | PY_INJECT（C++ 自检） |
| **断言数** | 24 |
| **预期** | 子场景 2a：结构失效路径存在。子场景 2b：完整升级流程 — shared→Unique 触发对其他 sharers 的 `GlobalInvalidate`，ack 已收集，行转换到 `G_E`/`G_M`，旧 sharers 从 mask 中移除。子场景 2c：升级后状态验证 — 新 owner 具有独占访问，旧 sharers 已失效，失效后 sharer mask 为空 |
| **实际** | PASS |
| **负面测试** | 旧 sharers 不保留访问；在 acks 之前无过早 grant |

### 4.3 TC-M8-3：共享默认路径

| 属性 | 值 |
|---|---|
| **ID** | TC-M8-3（M8-3-1 到 M8-3-7） |
| **名称** | 共享默认路径 |
| **类型** | PY_INJECT（C++ 自检） |
| **断言数** | 7 |
| **预期** | `GlobalReadShared` 请求产生 `GrantShared`；行变为 `G_S`；无 `force_grant_m` 旁路；默认配置使用 MESI 正确路径；`GlobalReadShared` ≠ `GrantModified`；`GrantShared` 与 `GrantExclusive` 和 `GrantModified` 不同 |
| **实际** | PASS |
| **负面测试** | 在默认配置下 Shared 请求不产生 Modified grant |

### 4.4 TC-M8-4：SharerMask 正确性

| 属性 | 值 |
|---|---|
| **ID** | TC-M8-4（M8-4a、4b、4c、4d 系列） |
| **名称** | SharerMask 正确性 |
| **类型** | PY_INJECT（C++ 自检） |
| **断言数** | 10 |
| **预期** | 子场景 4a：添加单个 sharer，mask 具有正确位；4b：添加多个 sharers，所有位已设置；4c：驱逐 sharer，位已清除，mask 缩小；4d：所有 sharers 已驱逐，mask 为空 → 条目已移除 |
| **实际** | PASS |
| **负面测试** | 驱逐后无过期位残留；空 mask 触发清理 |

### 4.5 附加自检

| 测试组 | 检查数 | 目的 |
|---|---|---|
| M8-5（失效期间的 busy 行） | 3 | `G_BUSY` 在失效期间设置，之后清除 |
| M8-6（失效 ack 发送计数器） | 1 | 计数器已初始化并递增 |
| M8-7（挂起失效生命周期） | 4 | 挂起失效已跟踪，事务期间活动，完成后清除，新请求可在已释放的行上进行 |
| M8-REENTRY | 3 | 重复 ack 被接受（无操作）；幂等重新进入有效 |

### 4.6 汇总

| 测试组 | 检查数 | PASS | FAIL | SKIP |
|---|---|---|---|---|
| TC-M8-1（两个请求者共享） | 9 | 9 | 0 | 0 |
| TC-M8-2（升级失效） | 24 | 24 | 0 | 0 |
| TC-M8-3（共享默认路径） | 7 | 7 | 0 | 0 |
| TC-M8-4（SharerMask 正确性） | 10 | 10 | 0 | 0 |
| M8-5（Busy 行） | 3 | 3 | 0 | 0 |
| M8-6（Ack 计数器） | 1 | 1 | 0 | 0 |
| M8-7（挂起失效） | 4 | 4 | 0 | 0 |
| M8-REENTRY（幂等 ack） | 3 | 3 | 0 | 0 |
| **合计** | **61** | **61** | **0** | **0** |

---

## 5. 回归结果

| 测试 | 状态 | 备注 |
|---|---|---|
| TC1–TC5 | 预先存在的 PASS | 不受影响 |
| M4 自检（M8 内） | 0 FAIL | 无回归 |
| M5 自检（M8 内） | 0 FAIL | 无回归 |
| M6 自检（M8 内） | 0 FAIL | 无回归 |
| M7 自检（M8 内） | 0 FAIL | 无回归 |
| M8 自检 | 0 FAIL | 所有 61 个检查通过 |

> M8 测试 harness（`test_shared_hardening.py`）包括 M4/M5/M6/M7 的累积回归检测。任何先前阶段的任何 FAIL 都会阻止 M8 门控。所有阶段一致通过。

---

## 6. 未完成 / 待办

| 事项 | 状态 | 备注 |
|---|---|---|
| 多 sharer 的 ARM_SYNC 端到端工作负载 | 尚未 | M8 使用 PY_INJECT（C++ 自检）；ARM 工作负载将在真实 CHI 协议路径下验证时序 |
| 元数据模型（M9） | 推迟到 M9 | 容量模型、外部协议 ABI 抽象 |
| 多 gem5 准备（M9） | 推迟到 M9 | 多实例部署假设 |

### 6.1 已知限制

1. **GlobalInvalidate** 在单 gem5 原型中按顺序向每个 sharer 发送失效。在真正的多 gem5 部署中，失效将跨外部网络广播。
2. **Ack 收集**使用进程内 `pendingInvalidations` 集合。对于真实硬件，需要超时或重传机制。
3. **`force_grant_m`** 调试标志仍然存在；它不是默认的，但可能被意外启用。
4. **SharerMask** 是 64 位的 — 对 N=3 足够，但可能需要对非常大的节点数进行扩展。

### 6.2 后续阶段回填

| 事项 | 目标阶段 | 优先级 |
|---|---|---|
| 多 sharer 场景的 ARM_SYNC 工作负载端到端 | M8 后 | P1 |
| 外部协议 ABI 抽象 | M9 | P2 |
| 元数据容量模型 | M9 | P2 |
| 多 gem5 / ns-3 时间假设 | M9 | P3 |
| 失效广播优化 | M9 后 | P3 |

---

## 7. 子模块状态

| 属性 | 值 |
|---|---|
| gem5 子模块已变更 | 是 |
| gem5 修复轮 commit | `4a9a672335`（P0-1/P0-2/P1-4 + M8SelfTest） |
| gem5 ackNode 重新定位 commit | `ad782435d6`（将边界检查移至入口） |
| gem5 最终 commit | `d1f6ec4947`（在目录查找之前移动边界检查） |
| 超项目最终 commit | `6e966e6`（M8 修复：更新 gem5 子模块） |

---

## 8. 构建与测试命令链

```bash
# 构建 gem5
docker run --rm -v $(pwd):/workspace -w /workspace/gem5 \
    ubcc-dev:ubuntu20.04 bash -c "scons build/ARM/gem5.opt -j20 PROTOCOL=CHI"

# 运行 M8 测试（包括 M4/M5/M6/M7 回归）
docker run --rm -v $(pwd):/workspace -w /workspace \
    ubcc-dev:ubuntu20.04 bash -c \
    "./gem5/build/ARM/gem5.opt tests/phase8/test_shared_hardening.py <arm_binary>"

# 预期：EXIT CODE 0, M8_SELF_TEST_PASSED=1,
#           M4:0 FAIL, M5:0 FAIL, M6:0 FAIL, M7:0 FAIL, M8:0 FAIL
```
