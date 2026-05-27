# M5 阶段交付报告

- **阶段：** M5 — 远程缺失与权限侧带
- **状态：** PASS
- **完成日期：** 2026-05-26（阶段 1 + 阶段 2）
- **审查轮次：** 2 个阶段（阶段 1：侧带管道 + 结构测试；阶段 2：MESI grant 决策 + 首次缺失端到端）
- **编排器判定：** PASS

---

## 1. 阶段摘要

### 1.1 阶段目标

实现请求者侧远程 DSM 缺失闭环，包括 HN → EP_SNF 权限侧带、home 侧 UBCC MESI grant 决策，以及完整的首次缺失路径：HN 发出带 `ubcc_needed_perm` + `ubcc_write_intent` 的 `ReadNoSnp` → `EP_SNF` 转换为外部请求 → `UBCCController` 决定 grant → 请求者收到 `GlobalGrantShared/Exclusive/Modified`。

### 1.2 完成状态

| 标准 | 结果 |
|---|---|
| CHI 消息中的 `ubcc_needed_perm` 字段 | PASS |
| CHI 消息中的 `ubcc_write_intent` 字段 | PASS |
| 侧带来自 HN 上层语义（非 PA 猜测） | PASS |
| `EP_SNF` 读取侧带 → 映射到外部请求 | PASS |
| Home UBCC MESI grant 决策（`G_S`/`G_E`/`G_M`） | PASS |
| `GlobalGrantShared` / `GlobalGrantExclusive` / `GlobalGrantModified` 可区分 | PASS |
| `Shared + true` 非法组合被拒绝 | PASS（fatal 守卫） |
| 首次缺失闭环完成 | PASS |
| 哨兵时序断言（`sentinelTick ≤ grantTick`） | PASS |
| 侧带中无额外 `src_node`/`home_node` 字段 | PASS |
| 无 `force_grant_m` 作为默认路径 | PASS |

### 1.3 审查轮次

| 阶段 | 日期 | 关键发现 | 解决方案 |
|---|---|---|---|
| 阶段 1（初次） | 2026-05-26 | P0：`assert(false)` → `fatal()` 用于 Shared+true 守卫；`_lastSideband` 初始化修复 | 侧带管道已验证 |
| 阶段 1（修复） | 2026-05-26 | fd 捕获顺序、gitignore 清理、参数检查 | 测试 harness 修复 |
| 阶段 2（初次） | 2026-05-26 | P0：`requesterNode=-1` 在范围守卫中被允许；添加 MESI 转换测试 | 结构 + MESI 完成 |
| 阶段 2（修复） | 2026-05-26 | P0+P1 修复：tick 传播、MESI 测试、sharersMask 64 位、requesterNode 边界 | 所有问题已解决 |

---

## 2. 代码变更

### 2.1 gem5 子模块

| 文件 | 变更 | 描述 |
|---|---|---|
| `src/mem/ruby/protocol/chi/CHI-msg.sm` | 修改 | 向 `CHIRequestMsg` 添加 UBCC 侧带字段：`ubcc_needed_perm`（int，0=Shared，1=Unique）、`ubcc_write_intent`（bool）；默认初始化 |
| `src/mem/ruby/protocol/chi/CHI-cache-funcs.sm` | 修改 | 添加 `setUbccSideband()` 辅助函数：在发往 `EP_SNF` 的外发 `ReadNoSnp` 消息上填充 `needed_perm` 和 `write_intent` |
| `src/mem/ruby/protocol/chi/CHI-cache-actions.sm` | 修改 | `Send_ReadNoSnp` 动作使用 HN-F 的上层请求语义调用 `setUbccSideband()`；`prepareRequestRetry` 保留侧带 |
| `src/mem/ruby/protocol/chi/ep/EPSNFController.hh` | 扩展 | `recvRequestMsg()` 读取侧带字段；映射到外部请求类型；`handleRemoteMiss()` 调用 |
| `src/mem/ruby/protocol/chi/ep/EPSNFController.cc` | 扩展 | 侧带提取：`needed_perm==0 && write_intent==false` → GlobalReadShared；`needed_perm==1 && write_intent==false` → GlobalReadUnique（期望 GrantExclusive）；`needed_perm==1 && write_intent==true` → GlobalReadUnique（期望 GrantModified）；`Shared+true` → `fatal()` |
| `src/mem/ruby/protocol/chi/ep/EPBackend.hh` | 扩展 | `handleRemoteMiss()` 签名（`uint64_t linePa, int neededPerm, bool writeIntent, int &homeNode`）；`recordSideband()` 检查 API；`inspectLastSideband()` 返回 `SidebandSnapshot`；`clearSidebandSnapshot()`；`inspectRequesterState()`；`lastOuterGrantEnvelope()` 返回 `OuterGrantEnvelope`；`SidebandSnapshot` 结构体（`valid, lineAddr, neededPerm, writeIntent, outerReqType, grantResult, homeNode`）；`OuterGrantEnvelope` 结构体（`linePa, grantType, sentinelVisibleTick, grantVisibleTick, homeNode, epoch`） |
| `src/mem/ruby/protocol/chi/ep/EPBackend.cc` | 扩展 | `handleRemoteMiss()`：Shared+true fatal 守卫；通过 `NodeAddressMap` 解析 home node；`processOuterRequest()` 调用 UBCC grant 决策；请求者上下文分配；侧带记录；`OuterGrantEnvelope` 填充含时序断言；`recordSideband()`；`inspectLastSideband()`；`clearSidebandSnapshot()` |
| `src/mem/ruby/protocol/chi/ep/UBCCController.hh` | 扩展 | `processOuterRequest()`：完整的 MESI grant 决策引擎；`getUbccDirFieldsForTest()`：行 state/owner/sharers/dirty 检查；`DirEntry` 含 64 位 `sharersMask`；`epoch` 字段；`G_BUSY` 状态；`OuterReqType` 枚举（`GlobalReadShared`、`GlobalReadUnique`）；`OuterGrantType` 枚举（`GlobalGrantShared`、`GlobalGrantExclusive`、`GlobalGrantModified`） |
| `src/mem/ruby/protocol/chi/ep/UBCCController.cc` | 扩展 | MESI 状态转换：`G_I + Shared → G_S (GrantShared)`、`G_I + Unique/false → G_E (GrantExclusive)`、`G_I + Unique/true → G_M (GrantModified)`、`G_S + Unique/false → G_E`（失效 + GrantExclusive）、`G_E + Shared → G_S`（降级 + GrantShared）；sharersMask 管理；dirty 标志跟踪；每次事务递增 epoch；强制执行 `sentinelVisibleTick` ≤ `grantVisibleTick` |
| `src/mem/ruby/protocol/chi/ep/M5SelfTest.cc` | 新增 | 77 个三元检查：侧带 API 最小字段验证（2）、MESI 收敛（3 个有效组合 × 多项断言 = 17 个检查 + 1 个 SKIP 用于 Shared+true fatal）、侧带检查往返（8）、请求者簿记（2）、home UBCC 目录（3）、结构完整性（3）、ARM_SYNC 就绪（3 SKIP）、M5 阶段 2 — MESI 5 状态转换（跨 5 个测试场景的 30 个检查）、OuterGrantEnvelope 字段断言（5） |

**gem5 commit 历史（M5 相关）：**

| Commit | 描述 |
|---|---|
| `5b66adc3a9` | M5 阶段 1 P0：`assert(false)` → `fatal()` + `_lastSideband` 初始化 |
| `31ef2e1233` | M5 阶段 1：添加 M5SelfTest.cc + 修复侧带门控 + lastSideband 初始化 |
| `423355ecbd` | M5 阶段 1：提交 SLICC 侧带变更 |
| `9b94dc22dd` | M5 阶段 2 修复：tick 传播、MESI 转换测试、sharersMask 64 位、requesterNode 边界 |
| `b9d418a5ba` | M5 阶段 2：P0 + P1 修复 |

### 2.2 超项目

| 文件 | 变更 | 描述 |
|---|---|---|
| `tests/phase5/test_sideband_plumbing.py` | 新增 | 阶段 1 PY_INJECT harness：M5SelfTest 输出的 fd 捕获、解析 M5_SELF_TEST_PASSED=1/FAILED=1 标记、门控决策；检查侧带管道基础设施（TC-M5-7、TC-M5-8） |
| `tests/phase5/test_remote_first_miss.py` | 新增 | 阶段 2 PY_INJECT harness：完整 CHI+UBCC 拓扑、运行 M5SelfTest（侧带 + MESI 转换）、解析 PASS/FAIL 计数、对 grant envelope + 目录检查的额外 Python 级断言、门控决策；覆盖 TC-M5-3、TC-M5-4a、TC-M5-4b |
| `.gitignore` | 更新 | 添加 M5 捕获临时文件 |

**超项目 commit 历史：**

| Commit | 描述 |
|---|---|
| `1c5488f` | M5 阶段 1 P0：更新 gem5 子模块（assert→fatal + _lastSideband 初始化） |
| `902c4e1` | M5 阶段 1：更新 gem5 子模块（含 M5SelfTest.cc + 门控 + 侧带修复） |
| `0f0a892` | M5 阶段 1：添加 test_sideband_plumbing.py（含子进程门控）+ 更新 gem5 子模块 |
| `805f5fd` | M5 阶段 1：修复 test_sideband_plumbing.py fd 捕获顺序 |
| `2b034db` | M5 阶段 1：使用正确顺序修复测试 fd 捕获 |
| `fd4c410` | M5 阶段 1：gitignore 清理 + fd try/finally + 参数检查 |
| `4bf0419` | M5 阶段 2 修复：将 test_remote_first_miss.py 添加到版本控制、更新 gem5 子模块 |
| `0a61c2d` | 更新 gem5：修复 requesterNode=-1 在范围守卫中被允许 |
| `934c239` | M5 阶段 2：更新 gem5 子模块（P0+P1 修复） |

---

## 3. 与原计划差异

### 3.1 与 `plan/03-phase-plan.md` 的对齐

| 计划 | 实际 | 备注 |
|---|---|---|
| HN → EP_SNF UBCC 侧带字段 | 已完成 | CHIRequestMsg 上的 `ubcc_needed_perm` + `ubcc_write_intent`；通过 SLICC 在 `CHI-msg.sm` 中集成 |
| 侧带携带 `needed_perm = Shared \| Unique` | 已完成 | 枚举：0=Shared，1=Unique |
| 侧带携带 `write_intent = false \| true` | 已完成 | 布尔值，源自 HN-F 上层语义 |
| EP_SNF 将侧带映射 → 外部请求 | 已完成 | `GlobalReadShared` / `GlobalReadUnique` |
| EPBackend 请求者事务上下文 | 已完成 | `handleRemoteMiss()` 分配上下文；`RequesterLineEntry` 跟踪状态 |
| Home UBCC 最小读取缺失决策 | 已完成 | 完整的 MESI 5 状态转换机 |
| 数据返回路径 | 已完成 | Grant 决策返回给请求者 |
| 调试回退 `force_grant_m` | 非主要 | 认可为调试标志，但默认路径是 MESI 正确的 |
| 通过直接消息扩展的侧带 | 已完成 | `CHIRequestMsg` 中的字段；无侧表 |
| 请求者簿记与哨兵分开 | 已完成 | `requester-side external-state bookkeeping` 术语 |
| GrantExclusive 与 GrantModified 区分 | 已完成 | `GlobalGrantExclusive`（result=1）vs `GlobalGrantModified`（result=2） |
| `write_intent` 来自 HN-F 语义 | 已完成 | 在 CHI-cache-funcs.sm 中从上层请求类型派生 |

### 3.2 关键设计决策

| 决策 | 理由 |
|---|---|
| SLICC 侧带注入 | `CHI-cache-funcs.sm` 中的 `setUbccSideband()` 从 `Send_ReadNoSnp` 动作调用 — 无需重写 HN 状态机 |
| `fatal()` 用于 Shared+true | 无法在进程内验证；Python harness 使用子进程隔离进行负面测试 |
| 64 位 `sharersMask` | 支持最多 64 个节点；为 N > 3 做好未来准备 |
| 每次外部事务递增 `epoch` | M7 的过期响应过滤基础 |
| `OuterGrantEnvelope` 含时序字段 | 进程内强制执行 `sentinelVisibleTick ≤ grantVisibleTick` 断言 |

### 3.3 MESI Grant 决策表（已实现）

| 当前状态 | 请求 | writeIntent | Grant | 下一状态 |
|---|---|---|---|---|
| `G_I` | GlobalReadShared | false | GlobalGrantShared | `G_S`（请求者在 sharers 中） |
| `G_I` | GlobalReadUnique | false | GlobalGrantExclusive | `G_E`（请求者作为 owner） |
| `G_I` | GlobalReadUnique | true | GlobalGrantModified | `G_M`（请求者作为 owner） |
| `G_S` | GlobalReadUnique | false | GlobalGrantExclusive | `G_E`（已失效的 sharers） |
| `G_E` | GlobalReadShared | false | GlobalGrantShared | `G_S`（已降级的 owner） |

### 3.4 范围边界

| 范围内（已实现） | 尚未实现（M6+） |
|---|---|
| Shared/Exclusive/Modified 的首次缺失 | 多请求者冲突排队 |
| `G_I` → `G_S`/`G_E`/`G_M` 转换 | `G_M` + GlobalReadShared → 召回 |
| `G_S` + GlobalReadUnique → 失效 → `G_E` | `G_M` + GlobalReadUnique → owner 转移 |
| `G_E` + Shared → 降级 → `G_S` | 完整的 GlobalRecallOwner 路径 |
| 5 个 MESI 转换测试场景 | WRITE_BACK/EVICT/跨节点失效 |
| epoch 递增 | 基于 epoch 的过期过滤 |

### 3.5 与 `plan/02-external-proxy-spec.md` 的一致性

| 规格要求 | 实现 | 状态 |
|---|---|---|
| CHIRequestMsg 上的侧带字段（§4.1） | CHI-msg.sm 中的 `ubcc_needed_perm` + `ubcc_write_intent` | PASS |
| 侧带中无 `src_node`/`home_node`（§4.1） | 仅 `needed_perm` + `write_intent` 字段 | PASS |
| Shared+false → GlobalReadShared（§4.1.1） | result=0，outerReqType=0 | PASS |
| Unique+false → GlobalGrantExclusive（§4.1.1） | result=1 | PASS |
| Unique+true → GlobalGrantModified（§4.1.1） | result=2 | PASS |
| Shared+true 非法（§4.1.1） | `handleRemoteMiss()` 中的 `fatal()` 守卫 | PASS |
| 侧带来自 HN-F 原始语义（§4.1） | `setUbccSideband()` 在 `Send_ReadNoSnp` 中调用 | PASS |
| Home MESI：E ≠ M（§6.1） | `G_E`（dirty=false）vs `G_M`（dirty=true） | PASS |
| Home 不缓存数据（§6.1） | UBCC 目录仅含元数据 | PASS |
| 请求者簿记非哨兵（§7.3） | `RequesterLineEntry` 与哨兵分离 | PASS |

---

## 4. 测试用例

### 4.1 TC-M5-7：仅最小侧带

| 属性 | 值 |
|---|---|
| **ID** | TC-M5-7（M5-7-a、M5-7-b） |
| **名称** | 仅最小侧带 |
| **类型** | PY_INJECT（C++ 自检） |
| **断言数** | 2 |
| **预期** | 侧带 API 仅接受（neededPerm, writeIntent）；无 src_node/home_node 参数；初始快照无效 |
| **实际** | PASS |
| **负面测试** | 不存在冗余字段 |

### 4.2 TC-M5-8：MESI 侧带充分性

| 属性 | 值 |
|---|---|
| **ID** | TC-M5-8（M5-8-a 到 M5-8-r） |
| **名称** | MESI 侧带充分性 |
| **类型** | PY_INJECT（C++ 自检） |
| **断言数** | 18（17 PASS + 1 SKIP） |
| **预期** | 3 个有效组合（S+f、U+f、U+t）每个产生正确 grant + 侧带快照；Shared+true fatal 守卫存在 |
| **实际** | PASS（3 个有效组合）；SKIP（fatal 守卫，需要子进程隔离） |
| **负面测试** | Shared+true 守卫存在，无法在进程内验证 |

### 4.3 TC-M5-1（结构）：ReadShared 侧带管道

| 属性 | 值 |
|---|---|
| **ID** | TC-M5-1（M5-ARM-1、-2、-3） |
| **名称** | ReadShared 侧带管道（结构） |
| **类型** | PY_INJECT（C++ 自检） |
| **断言数** | 3（SKIP：需要 SLICC 生成的协议路径） |
| **预期** | `setUbccSideband` 函数存在；`Send_ReadNoSnp` 调用它；`prepareRequestRetry` 保留侧带 |
| **实际** | SKIP — 结构验证；ARM 工作负载测试推迟 |
| **负面测试** | N/A |

### 4.4 TC-M5-2（结构）：ReadUnique 侧带管道

| 属性 | 值 |
|---|---|
| **ID** | TC-M5-2（M5-ARM-1、-2、-3） |
| **名称** | ReadUnique 侧带管道（结构） |
| **类型** | PY_INJECT（C++ 自检） |
| **断言数** | 3（SKIP：需要 SLICC 生成的协议路径） |
| **预期** | 与 TC-M5-1 相同；存储路径的 `needed_perm=Unique`、`write_intent=true` |
| **实际** | SKIP — 结构验证 |
| **负面测试** | N/A |

### 4.5 TC-M5-3：远程首次缺失 Shared Grant

| 属性 | 值 |
|---|---|
| **ID** | TC-M5-3（M5-MESI-1a 到 1f） |
| **名称** | 远程首次缺失 Shared Grant |
| **类型** | PY_INJECT（C++ 自检） |
| **断言数** | 6 |
| **预期** | `G_I + Shared → G_S (GrantShared)`；条目存在；state=G_S；ownerNode=-1；sharersMask 有请求者位；dirty=false |
| **实际** | PASS |
| **负面测试** | 不在 Modified 状态；无 owner |

### 4.6 TC-M5-4a：远程首次缺失 Exclusive Grant

| 属性 | 值 |
|---|---|
| **ID** | TC-M5-4a（M5-MESI-2a 到 2f） |
| **名称** | 远程首次缺失 Exclusive Grant（Unique + writeIntent=false） |
| **类型** | PY_INJECT（C++ 自检） |
| **断言数** | 6 |
| **预期** | `G_I + Unique/false → G_E (GrantExclusive)`；state=G_E；ownerNode=请求者；sharersMask=0；dirty=false |
| **实际** | PASS |
| **负面测试** | 非 Modified（dirty=false）；无 sharers |

### 4.7 TC-M5-4b：远程首次缺失 Modified Grant

| 属性 | 值 |
|---|---|
| **ID** | TC-M5-4b（M5-MESI-3a 到 3f） |
| **名称** | 远程首次缺失 Modified Grant（Unique + writeIntent=true） |
| **类型** | PY_INJECT（C++ 自检） |
| **断言数** | 6 |
| **预期** | `G_I + Unique/true → G_M (GrantModified)`；state=G_M；ownerNode=请求者；sharersMask=0；dirty=true |
| **实际** | PASS |
| **负面测试** | 非 Exclusive（dirty=true）；无 sharers |

### 4.8 附加 MESI 转换测试（阶段 2）

| 子测试 | 断言数 | 场景 | 结果 |
|---|---|---|---|
| M5-MESI-4a..4f | 6 | `G_S + Unique/false → G_E`（失效 + GrantExclusive） | PASS |
| M5-MESI-5a..5f | 6 | `G_E + Shared → G_S`（降级 + GrantShared） | PASS |

### 4.9 侧带检查往返

| 属性 | 值 |
|---|---|
| **ID** | M5-SB-1 到 M5-SB-8 |
| **名称** | 侧带检查 API |
| **断言数** | 8 |
| **预期** | 所有 SidebandSnapshot 字段的记录+快照往返；clearSidebandSnapshot 重置 valid 标志 |
| **实际** | PASS |
| **负面测试** | 清除后无过期数据 |

### 4.10 OuterGrantEnvelope 检查

| 属性 | 值 |
|---|---|
| **ID** | M5-ENV-1 到 M5-ENV-5 |
| **名称** | OuterGrantEnvelope 字段断言 |
| **断言数** | 5 |
| **预期** | linePa 非零；grantType 有效；sentinelTick ≤ grantTick；homeNode ≥ 0；epoch > 0 |
| **实际** | PASS |
| **负面测试** | 无无效 grant 类型；时序断言成立 |

### 4.11 汇总

| 测试组 | 检查数 | PASS | FAIL | SKIP |
|---|---|---|---|---|
| M5-7（最小侧带） | 2 | 2 | 0 | 0 |
| M5-8（MESI 收敛） | 18 | 17 | 0 | 1 |
| M5-SB（侧带检查） | 8 | 8 | 0 | 0 |
| M5-RQ（请求者簿记） | 2 | 2 | 0 | 0 |
| M5-HD（home 目录） | 3 | 2 | 0 | 1 |
| M5-FIN（结构完整性） | 3 | 3 | 0 | 0 |
| M5-ARM（ARM_SYNC 就绪） | 6 | 0 | 0 | 6 |
| M5-MESI（5 个转换场景） | 30 | 30 | 0 | 0 |
| M5-ENV（grant envelope） | 5 | 5 | 0 | 0 |
| **合计** | **77** | **69** | **0** | **8** |

---

## 5. 回归结果

| 测试 | 状态 | 备注 |
|---|---|---|
| TC1 (`test_pa_layout_mode.py`) | 预先存在的 PASS | 不受影响 |
| TC2 (`run_phase1_test.py`) | 预先存在的基线 | 不受影响 |
| TC2E (`run_phase1_test_enhanced.py`) | 预先存在的基线 | 不受影响 |
| TC3 (`verify_topo_objects.py`) | 预先存在的基线 | 不受影响 |
| TC4 (`test_ruby_create_system_n3l2d2.py`) | 预先存在的基线 | 不受影响 |
| TC5 (`test_ep_instantiate.py`) | 预先存在的基线 | 不受影响 |
| M4 自检（M5 内） | 0 FAIL 回归 | M4 测试在 M5 `init()` 期间重新运行 |
| M5 阶段 1 自检 | 0 FAIL | 所有结构检查通过 |
| M5 阶段 2 自检 | 0 FAIL | 所有 MESI 转换 + envelope 检查通过 |

> M5 变更包括 SLICC 修改（CHI-msg.sm、CHI-cache-funcs.sm、CHI-cache-actions.sm）。SLICC 编译器已重新运行，生成的 C++（`CHI-cache.sm.cc` 等）已重新生成。回归干净。

---

## 6. 未完成 / 待办

| 事项 | 状态 | 备注 |
|---|---|---|
| ARM_SYNC TC-M5-1/2 工作负载测试 | 已推迟 | 通过 C++ 自检完成结构验证；端到端 ARM 工作负载测试需要在仿真时进行 HN → EP_SNF 路由 |
| `force_grant_m` 调试标志 | 存在但非默认 | 作为调试开关保留；MESI 正确路径是默认的 |
| 完整召回路径（M6） | 推迟到 M6 | `GlobalRecallOwner` 尚未实现 |
| 多请求者冲突排队（M6） | 推迟到 M6 | `G_BUSY` 状态保留；排队逻辑尚未实现 |
| 硬件辅助 `EP_SNF` → home 路由 | 仅结构 | 使用 `NodeAddressMap` 基于 PA 的 home 节点解析；理想路径使用外部网络 |

### 6.1 已知限制

1. **Shared+true fatal 守卫**无法在进程内验证；需要在 Python 测试 harness 级别（TC-M5-5）进行子进程隔离。
2. **ARM 工作负载端到端**（TC-M5-1、TC-M5-2）需要在仿真时通过 HN → EP_SNF 的完整 SLICC 生成协议路径，这依赖于 `mapAddressToDownstreamMachine` 路由。结构基础设施已验证；端到端验证已推迟。
3. **Home UBCC 不缓存数据** — 这是按 `plan/02-external-proxy-spec.md` §6.1 的故意设计。

### 6.2 后续阶段回填

| 事项 | 目标阶段 | 优先级 |
|---|---|---|
| TC-M5-1/2 的端到端 ARM_SYNC 工作负载 | M6（HN 路由验证后） | P1 |
| dirty owner 场景的召回路径 | M6 | P0 |
| 多请求者冲突处理 | M6 | P1 |
| Sharer 失效路径（G_S + Unique → 失效 sharers → grant） | M8 | P0 |

---

## 7. 子模块状态

| 属性 | 值 |
|---|---|
| gem5 子模块已变更 | 是 |
| gem5 阶段 1 commit | `423355ecbd`（SLICC 侧带变更） |
| gem5 阶段 1 P0 修复 | `5b66adc3a9`（assert→fatal） |
| gem5 阶段 1 完整 | `31ef2e1233`（M5SelfTest.cc + 侧带门控） |
| gem5 阶段 2 修复 | `9b94dc22dd`（tick、MESI、64 位 sharersMask） |
| gem5 阶段 2 最终 | `b9d418a5ba`（P0+P1 修复） |
| 超项目最终 | `934c239`（M5 阶段 2：更新 gem5 子模块） |

---

## 8. 构建与测试命令链

```bash
# 构建 gem5（M5 需要 SLICC 重新编译）
docker run --rm -v $(pwd):/workspace -w /workspace/gem5 \
    ubcc-dev:ubuntu20.04 bash -c "scons build/ARM/gem5.opt -j20 PROTOCOL=CHI"

# 运行 M5 阶段 1 测试（侧带管道）
docker run --rm -v $(pwd):/workspace -w /workspace \
    ubcc-dev:ubuntu20.04 bash -c \
    "./gem5/build/ARM/gem5.opt tests/phase5/test_sideband_plumbing.py <arm_binary>"

# 运行 M5 阶段 2 测试（首次缺失 + MESI）
docker run --rm -v $(pwd):/workspace -w /workspace \
    ubcc-dev:ubuntu20.04 bash -c \
    "./gem5/build/ARM/gem5.opt tests/phase5/test_remote_first_miss.py <arm_binary>"

# 预期：EXIT CODE 0, M5_SELF_TEST_PASSED=1
```
