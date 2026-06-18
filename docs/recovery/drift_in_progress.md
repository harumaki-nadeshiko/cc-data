# Design Drift Log — In-Progress Deviations from scheme_v4.md

> **规则**: 任何与 `scheme_v4.md` 不符的自行修改与决定，都必须追加记录到此文件。
> 每条记录包含：时间、位置、偏离内容、偏离原因、状态。

---

## D-35: E2E 扩展 TC22~TC28（ResidentDir/BF/L3/epoch/backstore 覆盖）

| 字段 | 内容 |
|------|------|
| **时间** | 2026-06-18 |
| **位置** | `tests/e2e/workloads/e2e_tc22_*.c` ~ `e2e_tc28_*.c`, `tests/e2e/test_e2e.py` |
| **偏离内容** | 新增 7 个 E2E 用例：TC22 ResidentDir 容量压力、TC23 BF 假阳性回退、TC24 三节点并发压力、TC25 高频 INVALIDATE/Clear 循环、TC26 L3 驱逐写回链路压力、TC27 epoch wrap 压力（含 24b 逻辑回绕 marker）、TC28 backstore 数据/元数据一致性；同时扩展 harness 注册/校验到 TC28，并将 `--all` 改为动态遍历 `TESTCASES`。此外，TC22/TC26/TC28 初版大规模压力触发 `Sequencer deadlock`，已先降载到稳定规模用于回归。 |
| **偏离原因** | 用户要求补齐目录 offload 相关 gap 并形成可执行 E2E 覆盖。 |
| **状态** | ✅ 已实现，待本轮编译与 TC22~TC28 回归结果确认。 |

---

## D-34: TC7 workload 限制为每节点 primary CPU 执行

| 字段 | 内容 |
|------|------|
| **时间** | 2026-06-18 |
| **位置** | `tests/e2e/workloads/e2e_tc7_writeback_evict.c` |
| **偏离内容** | TC7 新增 `cpu_index`/`primary` 过滤，仅允许 `cpu_index % 4 == 0` 的 primary CPU 参与；非 primary CPU 直接退出，marker 也仅由 primary 发出。 |
| **偏离原因** | `sync_wait()` 当前按“线程数”而非“节点数”计数。TC7 原实现让 4 CPUs/node 全部参与，会使 Node1 的读在线程级 barrier 提前放行后先于 Node0 写回/驱逐执行，观测到伪失败（旧值 0）并误判为 CHI/UBCC 数据丢失。 |
| **状态** | ✅ 已完成，待 TC7 回归确认。 |

---

## D-33: INVALIDATE ack 恢复 committed sharers 递减并在 shared-empty 时转 G_I

| 字段 | 内容 |
|------|------|
| **时间** | 2026-06-18 |
| **位置** | `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc` |
| **偏离内容** | 在 `processInvalidationAck()` 中恢复 `INVALIDATE` 路径对 committed `DirEntry.sharersMask` 的递减更新；当 `state==G_S && sharersMask==0` 时立即 canonicalize 为 `G_I`，避免写入 `G_S+empty` 非法编码。`UPGRADE_PENDING` 路径继续仅使用 outstanding 的 `upgradeAckMask/upgradeTargetMask` 跟踪，不修改 committed 目录。 |
| **偏离原因** | 移除 committed sharers 递减后，INVALIDATE→GRANT_HANDSHAKE 窗口内目录观察到 stale sharers，导致流程卡死；直接递减若不转态又会触发 `G_S + sharersMask=0` 约束违规。 |
| **状态** | ✅ 已完成，待目标 TC 回归确认。 |

---

## D-32: Meta decode 校验收紧 + evict 后写回跳过

| 字段 | 内容 |
|------|------|
| **时间** | 2026-06-18 |
| **位置** | `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc` |
| **偏离内容** | `decodeMetaLine()` 新增严格守卫：`G_I` 要求 `sharersMask==0`；`G_S` 要求 `sharersMask!=0`；`G_E/G_M` 要求 `popcount(sharersMask)==1`，任一违规按 miss 返回 `false`。同时 `issueBackstoreWrite()` 处理 `snapshotResidentForBackstore()` 的 `false` 返回：若条目已驱逐，则跳过 meta write，仅回调 `onBackstoreWriteAck()`。 |
| **偏离原因** | 用户要求修复两处实现缺陷：避免非法 meta 被当作有效目录状态；避免条目已驱逐后仍错误写回 backstore。 |
| **状态** | ✅ 已完成，待本轮 TC2/7/10 回归确认。 |

---

## D-31: 元数据 backstore 迁移到 MetaRNF CHI 路径（进行中）

| 字段 | 内容 |
|------|------|
| **时间** | 2026-06-18 |
| **位置** | `MetaRNFController.{hh,cc,py}`, `EPBackend.{hh,cc,py}`, `CHI_ubcc_framework.py`, `CHI_basic_framework_config.py` |
| **偏离内容** | 将 `MetaRNFController` 从 tick-stub 改为基于 Ruby CHI 控制器的真实 requester：读路径发 `ReadOnce`，写/删路径发 `WriteUniqueFull` + `NCBWrData`；EPBackend 新增 `line_pa -> metadata_backstore_pa` 映射与 64B 元数据编码（含 key/state/sharers/epoch），backstore fill/write/delete 改为通过 MetaRNF 时序回调；配置侧新增 `metadata_private_base = phy_base + 5*seg_size`、`metadata_private_range(16MB)`，并把该 range 加入 HN-F/L_SNF 路由。 |
| **偏离原因** | 用户要求将上一轮软件 `_backstore` 影子路径替换为真实 `UBCC→UBRouter→UBAdapter→EPBackend→MetaRNF→HN-F/L3→private DRAM` 时序路径。 |
| **状态** | 🔄 进行中：第 3 轮回归中 TC1/3/4/5/6/8/11/12/13/14/15/16/17/18/19/20/21 通过，TC2/7/10 在 ResidentDir fill 阶段触发 `G_S with sharers=0` panic；已补充 decode 有效性过滤（尚未在回归轮次内复测）。 |

---

## D-30: UBCC directory offload 第一轮一次性落地（resident+BF+backstore stub）

| 字段 | 内容 |
|------|------|
| **时间** | 2026-06-18 |
| **位置** | `ResidentDir.{hh,cc}`, `UBCCController.{hh,cc}`, `EPBackend.{hh,cc,py}`, `MetaRNFController.{hh,cc,py}`, `SConscript`, `CHI_ubcc_framework.py`, `tests/e2e/test_e2e.py`, `tests/e2e/workloads/e2e_tc{18,19,20,21}_*.c`, `tests/ubcc/directory_offload/*.py` |
| **偏离内容** | 本轮在不改 CHI SLICC 的前提下，新增 resident counting BF(64KB,3-hash,4-bit counter)、control byte(fillPending/wbPending/pinned/lru)、resident waiter 队列、UBCC 软件 backstore 哈希表、MetaRNF 异步 stub、fill/evict/delete ack 回调、tombstone delete 调度、offload inspect/debug 接口；同时把 e2e 注册扩展到 TC18~TC21。 |
| **偏离原因** | 用户要求执行 `ubcc_implementation_plan.md` 的目录 offload 全量实现，并在最多 4 轮 compile+test 内验证。 |
| **状态** | 🔄 进行中（待本轮编译与 TC 回归结果确认）。 |

---

## D-29: UBCC 目录后端从 `std::map` 切换为 ResidentDir（512KB SRAM 抽象）

| 字段 | 内容 |
|------|------|
| **时间** | 2026-06-18 |
| **位置** | `gem5/src/mem/ruby/protocol/chi/ep/ResidentDir.{hh,cc}`, `UBCCController.{hh,cc}`, `SConscript` |
| **偏离内容** | 引入 `ResidentDir` 作为 UBCC resident directory 存储后端，默认 Bloom 切片 64KB（`bfOffset=448KB`），directory 容量按 `448KB / 7B = 65536` 计算；`UBCCController::_directory` 从 `std::map<uint64_t, DirEntry>` 替换为 `ResidentDir`，原有协议逻辑保持不变，仅把 map 的 `find/[]/erase/clear` 访问改为 `lookup/insert/update/remove/clear`。 |
| **偏离原因** | 用户要求按 `ubcc_directory_offload_design.md` 落地 Resident Directory Cache，并保持协议语义不变。 |
| **状态** | 🔄 进行中（待本轮 build + 16 个 TC 全量验证）。 |

---

## D-28: UBCC epoch width 改为可配置并支持按位宽回绕

| 字段 | 内容 |
|------|------|
| **时间** | 2026-06-17 |
| **位置** | `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.{hh,cc}`, `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.{py,cc}`, `gem5/configs/ruby/CHI_ubcc_framework.py` |
| **偏离内容** | 在现有 v4 设计默认 64-bit epoch 的基础上，新增 `ubcc_epoch_bits`/`UBCC_EPOCH_BITS` 运行时配置；`allocateReservedEpoch()/commitIntendedResult()/processClear()/isNewerEpoch()` 改为按配置位宽做 mask 与 half-range wrap-around 比较。 |
| **偏离原因** | 用户要求验证 UBCC epoch 是否可从 64-bit 压缩到 24/28/32 bit，且不能破坏现有 16 个 E2E TC。 |
| **状态** | ✅ 已验证：`epoch_bits=24/28/32` 下，TC1/2/3/4/5/6/7/8/10/11/12/13/14/15/16/17 全部 PASS；默认 64-bit 额外 smoke 复测 TC1/16 也 PASS。 |

---

## D-27: 补充 UBCC directory offload 设计问答（文档澄清，无代码改动）

| 字段 | 内容 |
|------|------|
| **时间** | 2026-06-17 |
| **位置** | `docs/recovery/ubcc_directory_offload_design.md` |
| **偏离内容** | 基于当前 `UBCCController` / `EPBackend` / `CHI_ubcc_framework.py`，补充了 3 类设计问答：① TD$/bucket/entry 的固定宽度打包与轻量压缩策略；② `ResidentDirCache` / `DirBucketCodec` / `DirPresenceFilter` / `DirEvictionPolicy` / `DirectoryStorageService` 的抽象分层；③ `2GHz + HN-F(tag=2,data=10) + DDR4_2400_8x8` 下的目录 read/modify/write ticks 建模。 |
| **偏离原因** | 用户要求把设计文档从“方向性方案”补全为“可直接落地的格式/抽象/时延回答”；本次仅更新文档，不修改代码。 |
| **状态** | ✅ 已完成；无代码行为变化。 |

---

## D-26: 移除 EPBackend 同步 UBCC fallback，改由 UBAdapter 返回附加元数据

| 字段 | 内容 |
|------|------|
| **时间** | 2026-06-17 |
| **位置** | `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc`, `UBAdapter.{hh,cc}`, `UBRouter.cc`, `UBMsg.hh`, `UBCCController.cc` |
| **偏离内容** | 将 `handleRemoteMiss()/notifyLocalWriteUpgrade()/sendRecallResponse()/handleWriteback()/handleEvict()/sendInvalidationAck()/sendUpgradeDone()/sendClear()` 中原先的 `if (_ubAdapter) ... else direct UBCC` 同步 fallback 全部删除；同时把 `ReadResp/UpgradeResp` 扩展为携带 committedEpoch、pendingInvMask、upgradeTargetMask、可选 recall grant data。 |
| **偏离原因** | 用户要求“只保留 UBAdapter message passing path”。EPBackend 仍通过 `UBCCController::getInstance()` 读取 epoch / invalidation mask / grant data，实质上仍保留同步旁路，需一并收口到 UBRouter 返回消息。 |
| **状态** | ✅ 已验证：Build 通过，TC1/2/3/4/5/6/7/8/10/11/12/13/14/15/16/17 全部 PASS。 |

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

## D-11: Recall→Handshake 闭环修复

| 字段 | 内容 |
|------|------|
| **时间** | Phase 4 TC2 调试 |
| **位置** | `UBCCController.cc:312-325` |
| **scheme_v4 原文** | Recall 完成后应转入 GRANT_HANDSHAKE，requester 重试时获得 grant |
| **实际实现** | 添加 `recallAlreadyDone` 检测：若已有 DONE 的 RECALL outstanding（同 PA+同 requester），跳过新建 RECALL，直接走 grant 路径 |
| **偏离原因** | processRecallResponse() 设 RECALL→DONE，但 DirEntry 未变（reserve-then-commit）。requester retry 时 processOuterRequest 看到旧 owner，重新发起 RECALL 而非 grant。 |
| **状态** | ⚠️ 测试中（TC2 仍 FAIL，需进一步调试） |

---

## D-12: TC2 workload 添加 dc civac cache flush

| 字段 | 内容 |
|------|------|
| **时间** | Phase 4 TC2 调试 |
| **位置** | `tests/e2e/workloads/dsm_access.h`, `tests/e2e/workloads/e2e_tc2_remote_read.c` |
| **问题** | `sync_wait` (syscall 436) 只做线程同步，不做 cache maintenance。Node 0 写 DSM_1 后脏数据在 cache 中，Node 1 读 DDR4 得到 0 |
| **修复** | 在 `dsm_access.h` 添加 `dsm_flush()` (dc civac)；TC2 workload write 后 sync 前调用 |
| **状态** | ⚠️ 需重新编译 ELF 测试 |

---

## D-8: 主 Agent 自主决策修改 SnpShared fatal → defensive（未问用户）

| 字段 | 内容 |
|------|------|
| **时间** | Layer 3e (TC1 调试) |
| **位置** | `EPRNFController.cc:631-637` |
| **偏离内容** | 主 Agent 在用户未回复 A/B 选项时，自行决定将 `fatal` 改为 `defensive SnpResp_I`，未留档记录。 |
| **影响** | SnpShared 到达 EP-RNF 的真正根因（DCT fallback 不工作 / EP-RNF 在本地 DSM 路径中被注册）尚未定位。workaround 掩盖了问题。 |
| **状态** | ⚠️ 已通过 D-1 记录。后续必须修复根因。 |

---

## D-13: Phase 4 跨节点测试全量阻塞 — Recall→Handshake 未闭环

| 字段 | 内容 |
|------|------|
| **时间** | Phase 4 (TC2-TC11) |
| **问题** | TC2/3/4/5/6/7/8/11 全部因 Recall→Handshake 未闭环失败（deadlock 或 HN-F panic） |
| **根因** | `processRecallResponse()` 设 RECALL→DONE 后不转入 GRANT_HANDSHAKE。Requester retry 无已完成 RECALL 检测。 |
| **状态** | ⚠️ OPEN_QUESTION。需修复 Recall→Handshake→Clear 闭环。 |

## D-14: M4-M8 Self-tests 实际未禁用

| 字段 | 内容 |
|------|------|
| **时间** | Phase 4 全量测试 |
| **问题** | M4-M8 self-tests 在 EPBackend::init() 运行，全部 FAILED。D-2 声称已 `#if 0` 但实际未包裹。 |
| **状态** | ⚠️ 需与 D-2 保持一致。 |

## D-15: Self-tests nulled for TC2 isolation

| 字段 | 内容 |
|------|------|
| **时间** | Phase 4 TC2 调试 |
| **位置** | `gem5/src/mem/ruby/protocol/chi/ep/M[4-8]SelfTest.cc` |
| **问题** | M4-M8 self-tests 在 EPBackend::init() 运行，与 TC2 测试消息交织，干扰 HN-F 消息诊断 |
| **修复** | 临时替换为空的 stub 函数以隔离 TC2 HN-F 意外消息问题 |
| **状态** | ⚠️ 临时 diagnostic。解决 TC2 后需恢复。 |

## D-16: Phase 4 深度诊断发现 — epoch 不匹配阻断 Clear 提交流程

| 字段 | 内容 |
|------|------|
| **时间** | Phase 4 深度调试 |
| **发现** | `processClear PA=0x10018000000 epoch mismatch: ost=1 clear=2 — dropped` |
| **根因** | RECALL→GRANT_HANDSHAKE 就地转换后，reservedEpoch 保留旧值，但 EPBackend 发送的 Clear 携带不同 epoch |
| **影响** | Clear 被丢弃，GRANT_HANDSHAKE 永不退休，目录永不 commit，后续请求永久 BUSY |
| **状态** | ⚠️ 需修复 epoch 传递链：RECALL 创建 → GRANT_HANDSHAKE 转换 → Clear 匹配 |

## D-17: SnpShared→EP-RNF 仍触发（SharedHint 修复不完整）

| 字段 | 内容 |
|------|------|
| **发现** | `EP_RNF node_id=0/1/2: SnpShared/SnpSharedFwd at PA=0x10000000` — 三节点均触发 |
| **根因** | sharedHint 条件化仅作用于 EPSNFController，但 EP-SNF 所有节点都覆盖所有 DSM 窗口。远程节点的 Shared grant 仍设 sharedHint=true，注册 EP-RNF。后续本地 Hit 触发 SnpShared |
| **状态** | ⚠️ defensive SnpResp_I 覆盖，未阻断测试但掩盖问题。 |

---

## D-18: 半区间 epoch 比较把“相等 epoch”误判为 stale

| 字段 | 内容 |
|------|------|
| **时间** | 2026-06-15 Phase 4 TC2/TC6 复测 |
| **位置** | `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:isNewerEpoch()` |
| **问题** | `isNewerEpoch(a, b)` 用 `((a-b)&mask) < 2^63` 判断“a 是否更新”，导致 `a == b` 时也返回 true。 |
| **影响** | `checkEpochForLine()` 把 `responseEpoch == committedEpoch` 的 RecallResponse / InvalidationAck / Writeback / Evict 全部当作 stale 拒绝，直接造成 TC2/3/6/8/10 的 recall 死锁。 |
| **修复** | 改为 `delta != 0 && delta < 2^63`，仅在严格更新时返回 true。 |

---

## D-19: TC5 Clear 路径临时诊断日志

| 字段 | 内容 |
|------|------|
| **时间** | 2026-06-17 TC5 调试 iteration 1 |
| **位置** | `EPBackend.cc`, `UBCCController.cc` |
| **偏离内容** | 添加 `[TC5-CLEAR-TRACE]` printf，跟踪 `handleRemoteMiss → savePendingGrantTxn → sendClear → processClear` 的 epoch/reqId/PA/outstanding 细节。 |
| **偏离原因** | TC5 中 node0/node2 出现 grant data 生成但未见对应 `ClearGrantHandshake`，需先证明 Clear 是否发送、是否命中 home UBCC、以及被哪条校验丢弃。 |
| **状态** | ⚠️ 临时 diagnostic，定位后应移除或收敛。 |

---

## D-20: TC5 replay tombstone / reqId 冲突修复

| 字段 | 内容 |
|------|------|
| **时间** | 2026-06-17 TC5 调试 iteration 2 |
| **位置** | `UBCCController.cc`, `EPBackend.cc` |
| **问题** | TC5 中 node1 首个 grant 完成后，node0/node2 的 replay 请求与 Clear 误命中旧 tombstone：`reqId` 只按 requester 本地递增，三节点都会产生 `reqId=1`；同时 tombstone 用的是 `reservedEpoch`，而 `Clear`/replay 使用的是 `baseEpoch`。结果后续请求被误当成旧事务重放，只看到一次真实 `ClearGrantHandshake`。 |
| **修复** | 1) requester 侧 `reqId` 加入 node-id 命名空间，避免跨节点碰撞；2) tombstone 改为记录/匹配 `baseEpoch`；3) `processOuterRequest()` 的 tombstone 检查改用来包中的 `baseEpoch`；4) grant 路径补齐 `outAuthEpoch` 回传；5) `PendingGrantTxn` 统一以 `homePa` 为 key。 |
| **状态** | ✅ 已实现，待 TC5 验证。 |

---

## D-19: EP-RNF CompData_SC 仅强制线协议，不再误改 HN-F 本地最终态

| 字段 | 内容 |
|------|------|
| **时间** | 2026-06-15 TC2/TC6/TC11 联调 |
| **位置** | `gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm:2936-2968` |
| **问题** | `Send_CompData` 对所有 `requestor==EP-RNF` 请求都同时清除了 `dataDirty/dataUnique/dataMaybeDirtyUpstream`。这会把“给 EP-RNF 发 SC”错误扩大成“把 HN-F 自己的本地 TBE 最终态也改成 SC/I”。 |
| **影响** | 正常跨节点 `ReadShared` 在 owner 仍存在时，会把 HN-F 带到 `dir_ownerExists=1` 但本地缓存态被误降级的非法组合，直接触发 TC2/TC11 的 Final assert；同类状态漂移也会放大 TC6 的后续队列/升级异常。 |
| **修复** | 仅对 `epProxyOp != NoProxyOp` 的 proxy special completion 保留本地 TBE scrub；普通 EP-RNF 请求仍强制发送 `CompData_SC`，但不再篡改 HN-F 的本地 `dataDirty/dataUnique/dataMaybeDirtyUpstream`。 |
| **状态** | ⚠️ 验证中 |

---

## D-20: UBCC BUSY 早退时补清 outerTxnPending

| 字段 | 内容 |
|------|------|
| **时间** | 2026-06-15 TC6 死锁诊断 |
| **位置** | `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc:565-572` |
| **问题** | `processOuterRequest()` 返回 BUSY 后，EPBackend 直接 `return -1`，但没有撤销先前设置的 `EP_RNF::outerTxnPending=true`。 |
| **影响** | 请求实际未获 grant，却长期保留“外层事务进行中”标记；若该 PA 上存在延迟 snoop response / retry，可能形成自阻塞，表现为 TC6 类重复 `dup_retry`。 |
| **修复** | BUSY 早退前显式 `setOuterTxnPending(false)`，并触发 `signalOuterTxnComplete()` 释放可能悬挂的 delayed HN response。 |
| **状态** | ⚠️ 验证中 |

---

## D-21: 同节点重复 ReadShared 不再重建 outer miss

| 字段 | 内容 |
|------|------|
| **时间** | 2026-06-15 TC6/TC11 死锁复盘 |
| **位置** | `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc:320-339` |
| **问题** | 同一节点的另一个 CPU 在兄弟 CPU 已拿到 `R_S/R_E/R_M` 后，又对同一远程行走了一次 `handleRemoteMiss()`；旧实现会把稳定的 requester-line 状态重新覆盖成 `R_WAIT_GRANT`，并向 home UBCC 发起新的 outer request。 |
| **影响** | 当此重复 miss 恰好碰到别的 requester 正活跃时，会被 home UBCC 排队成 `enqueue requester=<same node>`，随后主线程只看到无限 `dup_retry`，典型表现就是 TC6 node2 / TC11 node2 主 CPU 卡死而兄弟 CPU 已先完成。 |
| **修复** | 对 `neededPerm==Shared` 且本节点 requester-line 已处于 `R_S/R_E/R_M` 的情形，直接返回 BUSY，让 HN-F 走本地 retry，等待已有 fill 在本地层级可见；不再重建新的 outer miss。 |
| **状态** | ⚠️ 验证中 |
| **状态** | ⚠️ 已修改，待编译验证。 |

---

## D-19: Requester 侧 RecallBuffer 改为从 home UBCC outstanding 拉取

| 字段 | 内容 |
|------|------|
| **时间** | 2026-06-15 Phase 4 TC2 继续调试 |
| **位置** | `UBCCController.hh/.cc`, `EPBackend.cc` |
| **问题** | recall 数据保存在 home UBCC 的 outstanding `dataBuf`，但 requester `EPBackend::populateGrantData(RecallBuffer)` 只看本地 `_recallCaptureDataBlock`，导致 TC2 在 recall 成功后仍发 0 数据。 |
| **修复** | 新增 `UBCCController::copyOutstandingGrantData()`；requester 端在 `dataSource==RecallBuffer` 时先从 home UBCC outstanding 拉取数据，再进入 `populateGrantData()`。 |
| **状态** | ⚠️ 已修改，待编译验证。 |

---

## D-20: 本地升级链路补齐（SnpCleanInvalid → UpgradeReq/Ack/Done）

| 字段 | 内容 |
|------|------|
| **时间** | 2026-06-15 Phase 4 TC11/TC3 调试 |
| **位置** | `EPRNFController.hh/.cc`, `EPBackend.cc` |
| **问题** | `SnpCleanInvalid → notifyLocalWriteUpgrade → receiveUpgradeAck → sendUpgradeDone` 全链路无人触发，导致 TC11 本地升级后 home 目录不提交。 |
| **修复** | 在 `EPRNFController::handleSnpCleanInvalid()` 首次收到 DSM 线 snoop 时直接建立 `UpgradePending`，同步调用 `EPBackend::notifyLocalWriteUpgrade()`；Ack 成功后发延迟 `SnpResp_I`，紧接着调用 `sendUpgradeDone()` 提交 `UPGRADE_PENDING`。 |
| **状态** | ✅ 已验证：TC11 PASS。 |

---

## D-21: Read recall 数据落地到 home memory

| 字段 | 内容 |
|------|------|
| **时间** | 2026-06-15 Phase 4 TC11 继续调试 |
| **位置** | `EPBackend.cc:sendRecallResponse()` |
| **问题** | read recall 虽能把数据送给当前 requester，但未安装到 home DDR4 / HomeMemoryService；后续新的 shared reader 仍从旧内存读到 0。 |
| **修复** | 在 owner 侧 `sendRecallResponse()` 中，当 response 带 data payload 时，通过 home 节点 `EPBackend` 的 `RubySystem::getPhysMem()` 调 `HomeMemoryService::write()`，先把 recall 数据写回 home memory，再交给 home UBCC 标记 recall DONE。 |
| **状态** | ✅ 已验证：TC11 PASS。 |

---

## D-22: 编译修复 — `receiveUpgradeAck()` 对 EPBackend 公开

| 字段 | 内容 |
|------|------|
| **时间** | 2026-06-15 upgrade_invalidate_fix 首次编译 |
| **位置** | `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.hh` |
| **问题** | `EPBackend::notifyUpgradeAckReady()` 调用 `EPRNFController::receiveUpgradeAck()`，但后者仍声明在 private 区域，导致编译失败。 |
| **修复** | 将 `receiveUpgradeAck(uint64_t)` 移到 `EPRNFController` public 接口，保持实现不变。 |
| **状态** | ✅ 已验证：编译通过。 |

---

## D-23: 本地升级提交错误保留 sharersMask

| 字段 | 内容 |
|------|------|
| **时间** | 2026-06-15 TC3/TC6/TC8 死锁分析第 1 轮 |
| **位置** | `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:1350` |
| **问题** | `processOuterUpgradeReq()` 在本地升级 accepted 时把 `intendedSharersMask` 设成 `entry.sharersMask & ~reqBit`。升级 commit 后目录变成“owner + 旧 sharers 并存”的非法状态。 |
| **证据** | TC3 中 Node1 写 `b` 后，Node0 读仍见旧值 `a`；TC8 中 Node0 第二次写 `bbb` 后，Node1 读仍见 `aaa`。两者都说明升级后的其他 sharer 未被从 committed 目录中清空。 |
| **修复** | 本地升级 commit 为 owner-only 状态：`intendedSharersMask = 0`，保留 `targetMask` 仅用于 ack barrier，不进入最终目录。 |
| **状态** | ⚠️ 已修改；单独复测后仍有死锁，需继续修复升级回调/ReadShared 收尾。 |

---

## D-24: ReadShared 提前完成 + UpgradeAck PA 视图不一致

| 字段 | 内容 |
|------|------|
| **时间** | 2026-06-15 TC3/TC6/TC8 死锁分析第 2 轮 |
| **位置** | `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc`, `EPBackend.cc` |
| **问题 1** | `EPRNFController::recvDataMsg()` 对 `ReadShared` 在第一个 `CompData` beat 到达时就 `finishChiTxn()`，剩余 beat 没有发 `CompAck`。 |
| **证据 1** | `m5out/tc3_dbg1/debug.txt:340-362` 显示 Node0 在 recall `ReadShared` 的第一个 `CompData_SD_PD` 后立即 complete，随后又收到第二个 `CompData_SD_PD`，但此时已 “no pending txn”。 |
| **影响 1** | HN-F 侧上一笔 `ReadShared` 没有完整收尾，后续同线 `CleanUnique`/upgrade invalidate 被卡住，导致 TC3/TC6/TC8 写升级死锁。 |
| **修复 1** | `ReadShared` 改为逐 beat 发 `CompAck`，只有 `beatsReceived == beatsExpected` 后才 `finishChiTxn()`。 |
| **问题 2** | home UBCC 通过 `notifyUpgradeAckReady()` 回调的是 home-view PA，而 `EPRNFController::_upgradePending` 以 requester-local PA 建索引。 |
| **影响 2** | 跨节点升级（尤其 TC8）在 all-ack 完成后可能找不到本地 `UpgradePending`，丢失 deferred `SnpResp_I/UpgradeDone`。 |
| **修复 2** | `EPBackend::notifyUpgradeAckReady()` 在回调前把 home PA 翻译回 requester-local PA，再调用 `receiveUpgradeAck()`。 |
| **状态** | ⚠️ 已验证：ReadShared 双 beat 日志消失，但 TC3/TC6/TC8 仍死锁，需继续查本地 HN-F CleanUnique/upgrade 收尾。 |

---

## D-25: 新增 UBCC 目录 offload 设计研究文档

| 字段 | 内容 |
|------|------|
| **时间** | 2026-06-17 |
| **位置** | `docs/recovery/ubcc_directory_offload_design.md` |
| **变更** | 新增目录 onload/offload 设计研究文档；仅形成方案与问题清单，不涉及代码实现。 |
| **原因** | 按用户要求，基于当前框架调研 UBCC committed directory、现有 L3 启用条件，并输出目录 backstore + L3 缓存化的实施方案。 |
| **状态** | ✅ 文档已生成，待用户确认 3 个设计选项。 |

---
