# Design Drift Log — In-Progress Deviations from scheme_v4.md

> **规则**: 任何与 `scheme_v4.md` 不符的自行修改与决定，都必须追加记录到此文件。
> 每条记录包含：时间、位置、偏离内容、偏离原因、状态。

---

## D-1: SnpShared/SnpSharedFwd fatal → defensive SnpResp_I

| 字段 | 内容 |
|------|------|
| **时间** | Layer 3e (Phase 3 integration) |
| **位置** | `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:631-637` |
| **scheme_v4 原文** | §4.3.3: `SnpShared/SnpSharedFwd` → unreachable, `fatal/panic + audit` |
| **实际实现** | `warn()` + 立即 `SnpResp_I`（defensive） |
| **偏离原因** | TC1（本地 DSM 测试）中 EP-RNF 收到了 SnpShared。fatal 直接终止了测试。 |
| **根因分析** | TC1 是纯本地测试（L2→HN-F→DL_SNF→DDR4），不应涉及 EP-RNF。但 EP-RNF 被注册到 `dir_sharers` 后，本地后续访问触发了 SnpShared→EP-RNF。DCT fallback 未能阻止此路径。 |
| **状态** | ⚠️ 临时 workaround。需修复 DCT fallback / pickSharerForSnoop 后恢复 fatal。 |

---

## D-2: Self-test 文件禁用 (M4-M8)

| 字段 | 内容 |
|------|------|
| **时间** | Layer 3d (Backend Logic) |
| **位置** | `gem5/src/mem/ruby/protocol/chi/ep/M[4-8]SelfTest.cc` |
| **scheme_v4 原文** | entry doc §4.1: M4-M8 self-test "⚠️ Disabled by default (stale tests)" |
| **实际实现** | 文件内容被包裹在 `#if 0 ... #endif` 中，API 调用更新为 v4 签名（`MESIState` 去类前缀，`processOuterRequest` 参数更新，`nullptr→0`） |
| **偏离原因** | `processOuterRequest` 签名变化（增加 `baseEpoch`/`reqId` 参数），`MESIState` 从类内移到文件作用域。self-test 代码调用旧 API 导致编译失败。 |
| **状态** | ⚠️ self-test 逻辑已更新但仍被 `#if 0` 禁用。待 v4 API 稳定后重新启用。 |

---

## D-3: MESIState 从 UBCCController 类内移到文件作用域

| 字段 | 内容 |
|------|------|
| **时间** | Layer 3d (Backend Logic) |
| **位置** | `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh:50-55` |
| **scheme_v4 原文** | MESIState 原在 `UBCCController` 类内部定义（§4.1） |
| **实际实现** | 移到类外、`gem5::ruby` 命名空间，在 `OutstandingRequest` 结构体之前 |
| **偏离原因** | `OutstandingRequest` 结构体（类外定义）需要 `MESIState intendedState` 字段，但 MESIState 在类内不可见。 |
| **状态** | ✅ 已修复。所有引用从 `UBCCController::MESIState` 改为 `MESIState`。 |

---

## D-4: epRnfMachineVersion SLICC 语法：`default=` → `:=`

| 字段 | 内容 |
|------|------|
| **时间** | Layer 3e (Integration) |
| **位置** | `gem5/src/mem/ruby/protocol/chi/CHI-cache.sm:170` |
| **scheme_v4 原文** | `int epRnfMachineVersion, default="-1"` |
| **实际实现** | `int epRnfMachineVersion := -1;` |
| **偏离原因** | SLICC 机器级 param 用 `:= value` 语法（如 `sc_lock_base_latency_cy := 4;`），`default=` 不传播到 Python param wrapper。导致 gem5 启动时报 `without default or user set value`。 |
| **状态** | ✅ 已修复。`Param.Int(-1, ...)` 正确生成。 |

---

## D-5: EpProxyOp 枚举命名冲突：`None` → `NoProxyOp`

| 字段 | 内容 |
|------|------|
| **时间** | Layer 3a (Infrastructure) |
| **位置** | `gem5/src/mem/ruby/protocol/chi/CHI-msg.sm:48` |
| **scheme_v4 原文** | `EpProxyOp { None, InvalidateOnly, RecallUnique }` |
| **实际实现** | `EpProxyOp { NoProxyOp, InvalidateOnly, RecallUnique }` |
| **偏离原因** | X11 头文件中 `#define None 0L` 污染 gem5 编译环境，导致 `EpProxyOp::None` 宏展开为 `EpProxyOp::0L`。 |
| **状态** | ✅ 已修复。 |

---

## D-6: createMachineID 函数声明添加到 CHI 协议

| 字段 | 内容 |
|------|------|
| **时间** | Layer 3b (SLICC Protocol) |
| **位置** | `gem5/src/mem/ruby/protocol/chi/CHI-cache-funcs.sm:56` |
| **scheme_v4 原文** | 未提及此函数 |
| **实际实现** | 添加 `MachineID createMachineID(MachineType t, NodeID i);` 外部函数声明（来自 `RubySlicc_ComponentMapping.sm`） |
| **偏离原因** | CHI 协议未引入 `createMachineID`，但 `initializeTBE` 需要它构造 EP-RNF 的 MachineID。MESI/MOESI 协议有此函数。 |
| **状态** | ✅ 已修复。 |

---

## D-7: EpProxyOp 枚举值在不同上下文的引用语法差异

| 字段 | 内容 |
|------|------|
| **时间** | Layer 3b/3c (编译修复) |
| **位置** | 多处 |
| **scheme_v4 原文** | 未指定（实现细节） |
| **实际实现** | 三种引用方式：SLICC .sm 文件中用 `EpProxyOp:NoProxyOp`（单冒号）；SLICC 默认值用 `EpProxyOp_NoProxyOp`（下划线）；C++ 非 CHI 命名空间用 `CHI::EpProxyOp_NoProxyOp` |
| **偏离原因** | SLICC 代码生成器的命名约定与 C++ 作用域规则不一致。 |
| **状态** | ✅ 已修复。各文件使用正确语法。 |

---

## D-9: alloc_on_readshared/unique 从 baseline 的 False 改为 True

| 字段 | 内容 |
|------|------|
| **时间** | Layer 3a (Infrastructure), 诊断于 Layer 3e |
| **位置** | `gem5/configs/ruby/CHI_ubcc_framework.py:119-121` |
| **baseline 原文** | `alloc_on_readshared=False, alloc_on_readunique=False, alloc_on_readonce=False` (注释: "No L3 caching for DSM") |
| **v4 实现** | `alloc_on_readshared=True, alloc_on_readunique=True, alloc_on_readonce=False` (注释: "enable shared/unique DSM caching") |
| **偏离原因** | scheme_v4.md 和 entry doc §6.1 要求 `alloc_on_readunique=true` 以保证 EP-RNF 在 dir_sharers 后 HN-F L3 缓存不绕过 UBCC 路径。code-implementer 同时将 `alloc_on_readshared` 也改为 True。 |
| **影响** | 开启了 HN-F L3 对 DSM 地址的缓存。可能触发了本地 DSM 填充路径中的 EP-RNF 注册（通过 shared_hint 或其他机制），导致 TC1（纯本地测试）在本地读时死锁。 |
| **状态** | ⚠️ 待诊断确认。若确认是死锁根因，需重新评估 `alloc_on_readshared` 的取值。 |

---

## D-10: EP_SNF addr_ranges 包含了本地 DSM 窗口 → TC1 死锁根因

| 字段 | 内容 |
|------|------|
| **时间** | Layer 3e (Integration, failure-analyst 深度诊断) |
| **位置** | `gem5/configs/ruby/CHI_ubcc_framework.py:234` |
| **scheme_v4 原文** | TC1 应走 DL_SNF→DDR4 本地路径，不应触发 outer/UBCC |
| **实际实现** | `addr_ranges=[NodeConfig.dsm_range_for(nid, ...) for nid in range(num_nodes)]` — 包含了 node_id 自己的本地 DSM 窗口 |
| **偏离原因** | EP_SNF 的 addr_ranges 包含本地 DSM 窗口，导致本地 DSM 访问被 mapAddressToDownstreamMachine 路由到 EP_SNF→EPBackend→UBCC。EP_SNF 设置 shared_hint=true → HN-F 注册 EP-RNF → SnpShared→EP-RNF → 死锁。 |
| **修复** | 改为 `for nid in range(num_nodes) if nid != node_id` — 排除本地 DSM 窗口 |
| **状态** | ✅ 已修复。alloc_on_readshared/unique 还原为 True，deadlock_threshold 还原为 20000000。 |

---

## D-8: 主 Agent 自主决策修改 SnpShared fatal → defensive（未问用户）

| 字段 | 内容 |
|------|------|
| **时间** | Layer 3e (TC1 调试) |
| **位置** | `EPRNFController.cc:631-637` |
| **偏离内容** | 主 Agent 在用户未回复 A/B 选项时，自行决定将 `fatal` 改为 `defensive SnpResp_I`，未留档记录。 |
| **影响** | SnpShared 到达 EP-RNF 的真正根因（DCT fallback 不工作 / EP-RNF 在本地 DSM 路径中被注册）尚未定位。workaround 掩盖了问题。 |
| **状态** | ⚠️ 已通过 D-1 记录。后续必须修复根因。 |
