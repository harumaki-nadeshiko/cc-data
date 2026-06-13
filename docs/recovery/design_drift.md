# 设计与实现的流变对比

> 生成时间: 2026-06-12
> 基线设计文档: `docs/ep-rnf-sharer-registration-plan.md` (v3.2, 2026-06-08), `docs/multi-node-pa-layout.md` (v1.0, 2026-05-25), `docs/high-speed-interconnect-design.md` (2026-06-04 重写)
> 实际实现数据: `tmp_logs/phase2/` (per_file_timeline, decision_graph, experiment_vs_final, test_evolution)
> 恢复基准: `docs/recovery/catalog.md`, `docs/recovery/phase_plan.md`, `docs/recovery/decisions.md`

---

## 1. 概述

### 1.1 原始设计文档的版本和时间

| 文档 | 版本 | 日期 | 范围 |
|------|------|------|------|
| `ep-rnf-sharer-registration-plan.md` | v3.2 | 2026-06-08 | EP-RNF 注册、snoop 矩阵、DCT fallback、Phase 0-4 |
| `multi-node-pa-layout.md` | v1.0 | 2026-05-25 | Per-node PA 分区、三元组地址转换 |
| `01-current-state-and-requirements.md` | — | — | N=3/L=2/D=2 架构、EP_RNF sentinel 策略 |
| `03-phase-plan.md` | — | — | M3.5→T0→M4→M5→M6→M7→M8→M9 阶段序列 |
| `04-test-plan.md` | — | — | T0, M4-M8, M9 testcase 规范 |
| `high-speed-interconnect-design.md` | 重写 | 2026-06-04 | 三层架构 (gem5/UBCC/互联) 延迟模型 |
| `15-fix-to-pass-tests.md` | — | — | TC2-TC8 修复计划 (TBE 断言、pendingOp 参数化) |

### 1.2 实际实现的时间跨度

开发分为 12 个 session (Chunk 0–11)，逻辑分组为 Phase 0–10：

| Session | 逻辑阶段 | 主要内容 |
|---------|----------|----------|
| Chunk 0 | Q2 — Cross-Node Invalidation + TBE Debug | 诊断 TC3 pingpong、sendLocalSnoop→CHI request 路径修正 |
| Chunk 1 | Q3 — CHI Request-Based Snoop + SC_RSC | CompAck 时序修复、ReadOnce→ReadShared |
| Chunk 2 | Phase 1 — Deadlock Analysis + SLICC Fixes | 死锁分析、OutstandingRequest 设计 |
| Chunk 3 | Phase 1-2 — DirEntry/OutstandingRequest 解耦 | 持久目录 vs 暂态请求分离 |
| Chunk 4 | Phase 2-3 — EP-RNF Sharer Registration Design | shared_hint 注册、pickSharerForSnoop、snoop 矩阵 |
| Chunk 5 | Phase 0 — Test Harden + Phase 2 Review | MachineID 测试加固 (v1→v5, 5 轮迭代) |
| Chunk 6 | Phase 3 — UBCC Stub → Real Implementation | globalInvalidate/remoteFetch/updateOwner 真实实现 |
| Chunk 7 | Phase 4 — Build/Test/Regression | Self-test 分离、populateGrantData 重写 |
| Chunk 8 | Phase 5-6 — Regression Debug + TC Fixing | TC3/TC6/TC8 修复、multi-beat CompData |
| Chunk 9 | Phase 7-8 — CompAck Routing + Topology Fix | CompAck 目标地址修正、节点隔离拓扑修复 |
| Chunk 10 | Phase 9 — WriteRecall ReadUnique + Debug | WriteRecall 路径、sync_wait barrier |
| Chunk 11 | Phase 10 — Rollback + Final Cleanup | 全部实验性修改回退、终版代码合并 |

### 1.3 总体流变评估

| 维度 | 评估 |
|------|------|
| 设计忠实度 | **中等**。核心架构决策被严格遵守 (EP-RNF 经 HN-F、节点隔离)，但大量实现细节经历反复 |
| 阶段对齐度 | **低**。实际开发阶段 (Q2/Q3/Phase 0-10) 不匹配设计计划 (M3.5→M4→M5→M6→M7)，发生交叉和重排 |
| 文档同步度 | **低**。设计文档编写于实现之前/前期，多数未随实现迭代更新 |
| 振荡规模 | **严重**。12 个核心文件在多个 chunk 间反复修改 (`deadlock_threshold`、`pendingOp` 定时器、`sendLocalSnoop→CHI request`、`ReadOnce→ReadShared`、CompAck 路由) |
| 废弃代码量 | **中等**。7 个实验性模式 (sendLocalSnoop、ReadOnce recall、TBEStorage debug prints) 被回退或取代 |
| 未覆盖设计 | **显著**。M5 (permission sideband)、M6 (完整 UBCC directory)、M7 (writeback/evict/owner transfer)、M8-M9 设计文档齐全但实现仅达部分 |

---

## 2. 按主题对比

### 2.1 EP-RNF 注册方案 — shared_hint on CompData

#### 文档中的设计
> `ep-rnf-sharer-registration-plan.md` §2.2: "UBCC piggybacks `shared_hint=true` on the First Miss CompData (`CHIDataMsg`). HN-F, on receiving it, adds EP-RNF to `dir_sharers` and enters SC."

- `shared_hint` 字段添加在 `CHIDataMsg` 结构体上 (非 `CHIRequestMsg`)
- v1.0 曾错误地放在 `CHIRequestMsg` 上，经过 v2.0→v3.2 迭代修正
- `RegisterEPRNF_OnSharedHint` 动作在 HN-F 转换表中触发一次性注册

#### 实现中的实际状态
- ✅ **完全一致**。`CHIDataMsg` 包含 `shared_hint` 字段 (`CHI-msg.sm` 最终状态)
- ✅ HN-F 转换表添加了 `RegisterEPRNF_OnSharedHint` 动作 (`CHI-cache-transitions.sm`)
- ✅ 仅对 `CompData_SC`/`_SD_PD`/`_UC`/`_UD_PD` 触发注册
- ✅ 注册上下文使用 `RegState::UNREGISTERED → REG_DONE` 状态机

#### 流变记录
- **v1.0 (废弃)**: `shared_hint` 在 `CHIRequestMsg` 上 → 被分析发现语义错误，回退
- **v2.0**: 修正为 `CHIDataMsg`，但 EP-RNF SnpShared→remoteFetch 路径有 HN-F 语义冲突
- **v3.0**: 移除 SnpShared→remoteFetch，添加 Fwd guard 永久排除 EP-RNF
- **v3.1**: 修正 SnpUnique response types
- **v3.2**: 修正 DCT fallback 目标地址，对齐 `retToSrc` 条件规则

#### 过时评估
- ✅ **仍然有效**。最终实现严格按照 v3.2 描述工作

---

### 2.2 pickSharerForSnoop 优先级策略

#### 文档中的设计
> `ep-rnf-sharer-registration-plan.md` §2.4: "All HN-F snoop actions that select a single target from `dir_sharers` use a priority function instead of raw `smallestElement()`: exclude EP-RNF first → pick L2 if any → else EP-RNF."

覆盖 4 个 action:
- `Send_SnpUnique_RetToSrc` (line 1906)
- `Send_SnpSharedFwd_ToSharer` (line 2042)
- `Send_SnpOnce` (line 2075)
- `Send_SnpOnceFwd` (line 2105)

#### 实现中的实际状态
- ✅ `pickSharerForSnoop()` 实现在 `CHI-cache-funcs.sm` 中，逻辑与设计一致
- ✅ 4 个单目标选择点全部替换为 `pickSharerForSnoop()`
- ✅ EP-RNF 被永久排除在 Fwd snoop 目标之外

#### 流变记录
- 无振荡。从设计到实现路径清晰，仅需在 Chunk 4 一次性实现。

#### 过时评估
- ✅ **仍然有效**

---

### 2.3 DCT Fallback 触发条件

#### 文档中的设计
> §2.5: "When `dir_sharers = {EP-RNF}` (EP-RNF only) and the selected snoop protocol uses DCT, DCT is forced-off because EP-RNF cannot forward data to an arbitrary L2 requester."

在 3 个 initiator 中检查:
- `Initiate_ReadUnique_HitUpstream`
- `Initiate_ReadOnce_HitUpstream`
- `Initiate_ReadShared_HitUpstream_NoOwner`

#### 实现中的实际状态
- ✅ DCT fallback 实现在 `CHI-cache-actions.sm` 中
- ✅ 当 `dir_sharers.count()==1 && dir_sharers.has(epRnfId)` 时设置 `use_DCT:=false`
- ✅ 回退到 DMT-disabled 路径 (ReadNoSnp → SN-F)
- ⚠️ 一个偏差：在 `Initiate_ReadShared_HitUpstream` 中，DCT fallback 从设计文档的 "falls to non-DCT `Send_SnpOnce`" 变为实际实现中的 "DCT 分支 → `replace_request` → DMT-disabled ReadNoSnp 路径"。这是 Chunk 9 期间基于 SC_RSC crash 调试结果的修正，更安全但偏离原始设计。

#### 流变记录
- v3.0: DCT fallback 首次写入设计，目标是回退到 `Send_SnpOnce`/`Send_SnpUnique_RetToSrc`
- Chunk 9: 实际实现中改为直接回退到 DMT-disabled ReadNoSnp 路径，原因是 DCT 在 RN-F 启用了但 HN-F 行为预期 DCT-off → 导致 CompAck 时序错误 → SC_RSC crash

#### 过时评估
- ⚠️ **部分过时**：设计文档 §2.5 描述的回退目标路径与实际实现不一致。实际采用了更安全的 DMT-disabled 回退，但文档未及更新。

---

### 2.4 CompData_SC vs SD_PD 语义

#### 文档中的设计
> `plan/03-phase-plan.md` M5 §6.1 暗示 MESI 下必须区分 E/M
> `decisions.md` D-8: "SD → SC (write back to home, clean shared) — MESI semantics require clean shared"

设计预期：Shared 状态下，外部读取 dirty 本地副本时，需先 WriteBackFull 到 home，本地降级为 SC。

#### 实现中的实际状态
- ✅ `Send_WriteBackFull` 动作在发送 `SnpRespData_SC` 前执行
- ✅ `alloc_on_readunique = True` 确保 L3 缓存 DSM 数据 (发现于 Chunk 9)
- ✅ 节点隔离确保 cross-node 不通过 HN-F 直接路由

#### 流变记录
- Chunk 9 的关键发现：SC→UD write 必须使用 CleanUnique (非 ReadUnique)，使得数据来自 core 而非 SN-F。此细节在设计文档的 §4.2 消息流图中有隐含提及但未显式强调。
- Chunk 9 发现另一个拓扑 bug: Node 1 HN-F 错误地将 DSM 请求路由到其他节点的 EP-SNF (违反节点隔离)。

#### 过时评估
- ✅ **仍然有效**，但实现中暴露了设计文档未充分预见的 HN-F 状态机细节 (CleanUnique vs ReadUnique 的选择)

---

### 2.5 alloc_on_readunique / alloc_on_readshared 启用策略

#### 文档中的设计
设计文档 `03-phase-plan.md` 和 `01-current-state-and-requirements.md` 均未显式提及 `alloc_on_readunique` 或 `alloc_on_readshared` 参数。这些是 CHI HN-F 的 gem5 内置参数。

#### 实现中的实际状态
- ✅ `alloc_on_readunique = True` 在 `CHI_ubcc_framework.py` 的 Chunk 11 最终版本中设置
- ✅ `CHI_config.py` 中同样设置 `alloc_on_readunique = True`
- 理由 (Chunk 9 发现): 无 L3 缓存时，每次跨节点访问都触发完整 UBCC 往返；HN-F 需要 L3 缓存 DSM 行以提升性能；EP-RNF 在 dir_sharers 中注册确保正确 snoop 行为
- `alloc_on_readshared` 未显式设置 (使用默认值)

#### 流变记录
- Chunk 9: 首个实现中 `alloc_on_readunique = False` (gem5 default)，导致 L3 bypass → 性能极差 → 改为 True
- `alloc_on_readshared` 在设计中未被提及，实现中也使用默认值

#### 过时评估
- ➕ **文档未覆盖**。`alloc_on_readunique` 对系统正确性和性能至关重要 (确保 L3 参与 DSM 缓存)，但原始设计文档完全未提及此参数。建议在 `plan/01-current-state-and-requirements.md` 或新 HN-F 配置文档中补充。

---

### 2.6 UBCC DirEntry 数据结构

#### 文档中的设计
> `01-current-state-and-requirements.md` §6.4: "home UBCC 的状态机要求使用 MESI，而不是把 E 和 M 混成一个 owner 态"
> `03-phase-plan.md` M6 §7.1: "home UBCC 的 per-line state 必须使用 MESI，显式区分 E 和 M"

设计预期:
- 持久目录状态 (DirEntry) 与暂态请求缓冲 (OutstandingRequest) 解耦
- MESI 严格区分 `E` (clean exclusive owner) 和 `M` (dirty modified owner)
- DirEntry 不应包含 `materializedData`

#### 实现中的实际状态
- ⚠️ **部分实现**。DirEntry 包含状态字段: `state` (I, SC, UD, UC, SD)，其中 owner 态的 `UD`/`UC` 结合 `dirty` flag 来区分
- ✅ DirEntry 与 OutstandingRequest 已分离: `OutstandingRequest` 使用独立的 `std::unordered_map`
- ✅ `materializedData` 已从 DirEntry 移除，数据通过 `OutstandingRequest.dataBuffer[64]` 临时传递
- ⚠️ 当前状态枚举未显式定义 `G_E` / `G_M` 命名，而是使用 `UC`/`UD` 配合 `dirty` flag 表达。这与 M7SelfTest.cc 中 `M7_CHECK("DirEntry sizeof < 256 (no data buffer)", sizeof(UBCCController::DirEntry) < 256)` 一致

#### 流变记录
- Phase 1-2 (Chunk 3): 最初 DirEntry 混合持久状态和暂态状态 → 用户要求解耦 → 添加 OutstandingRequest 结构体
- Phase 3 (Chunk 6): `materializedData` 从 DirEntry 移除 → 数据经过 OutstandingRequest.dataBuffer
- Chunk 11 最终确认: `DirEntry` 不包含数据缓冲区

#### 过时评估
- ⚠️ **部分过时**：设计文档多处强调 MESI 状态使用 `G_E`/`G_M` 命名 (如 `TC-M6-4`, `TC-M5-4`)，但实际代码使用 `UC`/`UD` + `dirty` flag。语义等价但命名约定不同。测试 `TC-M7-6` (recall result state split) 检查原 owner 降级/失效，映射正确。

---

### 2.7 pendingOp 串行化机制

#### 文档中的设计
> `high-speed-interconnect-design.md` §4: "pendingOp 将在外部 UBCC 中管理，EP 侧不再需要它"
> `15-fix-to-pass-tests.md` Step 3: 超时参数化 (1us × 5 = 5us = 5000 ticks)

设计预期: 从固定定时器演化到 OutstandingRequest-based 串行化。

#### 实现中的实际状态
- ✅ 最终版本使用 OutstandingRequest-based 串行化
- ✅ `OutstandingRequest` 包含 `OpType { RECALL, INVALIDATE, GRANT_HANDSHAKE }` 和 `OpState { WAITING_RESP, RESP_RCVD, CANCELLED }`
- ✅ `isLineBusy()` 检查 OutstandingRequest 中非 CANCELLED 条目
- ✅ 遗留 `pendingOp=1/2/3` 标记已被 `createOutstanding()/findOutstanding()/completeOutstanding()` 替代
- ❌ 设计文档 `15-fix-to-pass-tests.md` 中的参数化超时方案 (pendOpTimeout = 5000 ticks) **未被直接采用**；实际采用了 OutstandingRequest 状态机方案

#### 流变记录 (振荡)
- `pendingOp transition timer` 调整轨迹: **1M → 2M → 5M → 移除 → 恢复** 周期 (`catalog.md` 确认)
- 反复原因: 不同测试场景暴露不同竞态窗口，定时器值无法同时满足所有场景
- 最终方案: OutstandingRequest 显式状态转换取代固定定时器

#### 过时评估
- ❌ **已废弃**: `15-fix-to-pass-tests.md` Step 3 的参数化超时方案已被 OutstandingRequest 状态机方案取代。该文档仍然是针对 Chunk 6-7 阶段问题的历史记录，但不再反映最终架构。

---

### 2.8 OutstandingRequest 解耦

#### 文档中的设计
> `decisions.md` D-12: "DirEntry (persistent directory state) vs OutstandingRequest (transient request buffer) — 必须解耦"

设计预期:
- DirEntry: 持久目录状态 (state, ownerNode, sharersMask, dirty, epoch)
- OutstandingRequest: 暂态请求跟踪 (opType, opState, dataBuffer, callback, epochAtCreate)

#### 实现中的实际状态
- ✅ 完全按设计实现
- ✅ `UBCCController.hh` 包含独立 `std::unordered_map<uint64_t, OutstandingRequest> _outstanding`
- ✅ OpType 枚举: `RECALL, INVALIDATE, GRANT_HANDSHAKE`
- ✅ OpState 枚举: `WAITING_RESP, RESP_RCVD, CANCELLED`
- ✅ dataBuffer[64] + dataValid 标志

#### 流变记录
- Phase 1-2 (Chunk 3): Phase 1 添加结构体无行为变更 → Phase 2 recall 迁移 → Phase 3 invalidation 迁移 → Phase 4 grant_handshake 迁移
- Chunk 7 关键修复: GRANT_HANDSHAKE 必须使用 `RESP_RCVD` 初始状态 (非 `WAITING_RESP`)

#### 过时评估
- ✅ **仍然有效**

---

### 2.9 pendingOwnerUpdate barrier 生命周期

#### 文档中的设计
> `ep-rnf-sharer-registration-plan.md` §9.3: "Async updateOwner Window Protection"
> - `pendingOwnerUpdate = true` 在 `updateOwner` 被 dispatch 时设置
> - Cross-node Unique 在 `pendingOwnerUpdate==true` 期间被 defer (retry queue 或 NACK)
> - `MAX_PENDING_OWNER_TICKS = 5000` ticks 保护
> - 超时后 quarantines 该 line (`state=QUARANTINE`), abort 所有 deferred 请求

#### 实现中的实际状态
- ✅ `pendingOwnerUpdate` 标志存在于 UBCC DirEntry 中
- ✅ `clearPendingOwnerUpdate()` 方法由 EPBackend 在 HN-F+EP-RNF 注册完成后调用
- ✅ 仅 home node UBCC 应 clear pendingOwnerUpdate (Chunk 6 修复)
- ⚠️ **未知**: `MAX_PENDING_OWNER_TICKS = 5000` 定时器和 `QUARANTINE` 回退是否已实现？恢复文档和实现工件中未见明确证据
- ⚠️ pendingOwnerUpdate 期间的 deferred request 队列 (per-line FIFO) 实现细节未在 catalog 或 phase_plan 中明确追踪

#### 流变记录
- Chunk 6 (Phase 3): `updateOwner()` 设置 `pendingOwnerUpdate=true`，`clearPendingOwnerUpdate()` 添加
- Chunk 7: 修复了 "only HOME node UBCC should clear pendingOwnerUpdate"

#### 过时评估
- ⚠️ **部分过时**: 核心 barrier 机制已实现，但设计文档描述的 QUARANTINE 安全网和 MAX_PENDING_OWNER_TICKS 参数尚未确认实现。建议将其转为 TODO 或标记为 "Phase 4+ 延迟实现"。

---

### 2.10 NCBWrData DDR4 路由

#### 文档中的设计
设计文档未显式描述写数据应路由到 home 节点的 DDR4。

#### 实现中的实际状态
- ✅ `EPSNFController.cc` 最终状态包含明确注释: "Route write data to HOME node's DDR4, not local DDR4"
- ✅ 实现逻辑: 通过 `_backend->homeNodeCrossNode()` 确定 home node，然后路由到对应的 DDR4 控制器
- ➕ 此实现细节在设计文档中完全未覆盖，但符合 `multi-node-pa-layout.md` 中定义的地址分区原则

#### 过时评估
- ➕ **文档未覆盖**。这是一个重要的跨节点数据路径决策，建议补充到 `multi-node-pa-layout.md` 或新建数据路径设计文档。

---

### 2.11 populateGrantData 数据源

#### 文档中的设计
> `decisions.md` D-22: 用户要求 "如果数据在远端，他应该发起全局读的请求，让对端的UBCC向内拉取数据，再返回给本UBCC"

设计预期: v1 (phys_mem functionalAccess) → v2 (_lastGrantData bypass) → v3 (OutstandingRequest.dataBuffer via recall→grant)

#### 实现中的实际状态
- ✅ 最终版本: 数据通过 `OutstandingRequest.dataBuffer` 从 recall→grant 路径传递
- ✅ 不再使用 `phys_mem->functionalAccess()` 作为主数据源
- ✅ `_lastGrantDataBlock` 旧逻辑已完全移除 (Chunk 11 确认)
- ⚠️ `populateGrantData` 经历了 3 个版本迭代，Chunk 7 重写

#### 流变记录
- v1 (Q2-Q3): `phys_mem->functionalAccess()` + `first_word != 0` 启发式 → 可能返回 stale 数据
- v2 (Chunk 4-6): 增加 `_lastGrantData` bypass → 不一定被填充，多行场景有数据竞争
- v3 (Chunk 7+): OutstandingRequest.dataBuffer 传递 → 这是当前方案

#### 过时评估
- ✅ **仍然有效**。实现与用户指令完全一致。

---

### 2.12 Recall 路径 — ReadShared vs ReadUnique vs functionalRead

#### 文档中的设计
> `ep-rnf-sharer-registration-plan.md` §4.3-4.4: SnpUnique→globalInvalidate, SnpOnce→remoteFetch
> 用户指令 (Chunk 0-1 多次强调): "EP-RNF 将外部请求包装成标准 CHI Request 发送给 HN-F"，"我跟你说过多少遍不能用 ReadOnce"

设计预期:
- Read-only recall → EP-RNF 发送 ReadShared 到 HN-F
- Write recall (需要 invalidate) → EP-RNF 发送 CleanUnique 或 ReadUnique 到 HN-F
- 不允许 functionalRead bypass
- 不允许 sendLocalSnoop bypass

#### 实现中的实际状态
- ✅ Recall 路径经 EP-RNF→HN-F 发送 CHI 请求 (ReadShared 或 CleanUnique/ReadUnique)
- ❌ `sendLocalSnoop()` 方法已被用户强制回退 (Chunk 0)
- ❌ `ReadOnce` 方法已被用户强制回退 (Chunk 1)
- ✅ `functionalRead` bypass 已修复，TC8 recall 现在使用 ReadShared

#### 流变记录 (严重振荡)
1. **sendLocalSnoop()** (Q2 原始实现): EP-RNF 直接发本地 snoop 到 CPU caches → 用户: "EP-RNF怎么会发Local Snoop呢? Snoop本来就是HN-F发送的东西" → **回退**
2. **ReadOnce recall** (Q3): 实现使用 ReadOnce → 用户: "我跟你说过多少遍不能用ReadOnce, 你把修改给我回退回去" → **回退**
3. **functionalRead** (Phase 5-6): recall 使用 functionalRead → 不注册 EP-RNF 到 HN-F dir_sharers → TC8 失败 → **修复**
4. **CHI Request-Based** (Final): ReadShared 用于 shared recall, CleanUnique/ReadUnique 用于 write recall → **当前方案**

#### 过时评估
- ⚠️ **部分过时**: 设计文档 §4 消息流图不再提及 ReadOnce 或 functionalRead，反映了最终正确路径。但原始实现中的振荡轨迹表明设计意图和初始实现之间存在显著差距 — 设计文档是正确的，但 implementer 初始未能正确执行。此差距暴露了计划文档可能在约束表达上过于间接。

---

### 2.13 CompAck 目标地址

#### 文档中的设计
> `catalog.md` Chunk 9 description: "CompAck routing: must use HN-F's mapAddressToDownstreamMachine, not responder ID"

#### 实现中的实际状态
- ✅ 最终使用 `mapAddressToDownstreamMachine` 确定 CompAck 目标
- ✅ 标准 CHI 语义: RN-F 收到 CompData 后发送 CompAck 到 HN-F
- ✅ EP-RNF 遵循相同协议

#### 流变记录 (振荡)
- `CompAck destination`: **responder-based → HN-F MachineID-based → mapAddressToDownstreamMachine** (`catalog.md` 确认)
- 迭代原因: 前两种方式在某些拓扑配置下路由到错误目标
- 最终方案匹配标准 RN-F 行为

#### 过时评估
- ✅ **仍然有效**

---

### 2.14 Self-test 与 Workload 分离

#### 文档中的设计
> `plan/04-test-plan.md` §0.3: M4 允许少量强注入 helper; M5-M7 转向路径驱动
> 用户指令 (Chunk 7): "我不希望selftest和workload混跑"

#### 实现中的实际状态
- ✅ `EPBackend.py` 包含 `enable_self_test` Param (默认 True)
- ✅ `test_e2e.py` 对所有节点设置 `enable_self_test = False`
- ✅ M4-M8 self-tests 在 `_enableSelfTest==true` 时在 init() 中运行
- ✅ 分离保证: self-test 模式和 workload 模式不会同时运行

#### 流变记录
- 原始设计: Self-tests 在 `EPBackend::init()` 无条件运行
- Chunk 7: 用户要求分离 → 添加 `enable_self_test` 标志

#### 过时评估
- ✅ **仍然有效**。但原始设计文档 `04-test-plan.md` 未提及 self-test/workload 分离需求 — 这是实现过程中由用户发现的改进。

---

### 2.15 sync_wait barrier 实现

#### 文档中的设计
> `plan/03-phase-plan.md` T0 §4: "实现 SE-mode 下的跨 node barrier syscall，为后续多节点 directed testcase 提供可重复、可验证的同步原语"
> `plan/04-test-plan.md` TC-T0-1~TC-T0-4: 4 个 barrier testcase 规范

设计预期:
- 注册 ARM 自定义 syscall (436)
- 实现 `SyncWait` barrier 状态对象
- 支持 `node_mask` 区分 barrier 实例
- 只统计显式调用 syscall 的线程
- 支持重复使用

#### 实现中的实际状态
- ❌ **Syscall 436 未被实现**。注释明确说明: "Syscall 436 is NOT implemented in gem5 SE-mode"
- ✅ 替代方案: `sync_wait()` C 函数使用 `dmb osh` + spin on DSM load
- ✅ Spin-wait 方案确保了写可见性验证 (如果 coherence 断裂则自旋挂起 → 易于检测)
- ✅ 支持 `node_mask` 参数 (虽然 spin-wait 实现不直接使用它进行 barrier 计数)
- ⚠️ 实际行为: 不是真正的 barrier (线程计数+统一释放)，而是 spin-wait on value + 隐式依赖 coherence 传播延迟

#### 流变记录
- T0 设计阶段: 预期 syscall-based barrier，但从未被实现
- Chunk 8-10: `sync_wait()` spin 版本在 `e2e_common.h` 中演化 (3 个 chunk 迭代)
- Chunk 10 最终版本: `dmb osh` 排空 store buffer + spin on `dsm_load` 返回值

#### 过时评估
- ❌ **已废弃**: 设计文档 `03-phase-plan.md` T0 节和 `04-test-plan.md` TC-T0-1~TC-T0-4 描述的 syscall-based barrier 被 spin-wait 替代方案取代。原 T0 testcase 规范 (TC-T0-1~TC-T0-4) 从未被执行。建议:
  1. 更新 `03-phase-plan.md` T0 节，标注 syscall barrier 为 "deferred" 或 "replaced by spin-wait"
  2. 保留 TC-T0-1~TC-T0-4 规范作为 future work

---

### 2.16 deadlock_threshold 配置

#### 文档中的设计
> `15-fix-to-pass-tests.md`: 目标是将默认 deadlock 检测值设为合理值
> `high-speed-interconnect-design.md` §7: 参数化 interconnected latency

#### 实现中的实际状态
- ⚠️ `deadlock_threshold` 在开发过程中经历了 **多次振荡** (catalog 确认)
- 值轨迹: `"10ms"` (string) ↔ `20000000` (integer)，在多个 chunk 间反复
- 最终值: `20000000` (integer, 最终稳定在 Chunk 7)
- 原因: 不同阶段的调试、不同的 deadlock 症状导致反复调整阈值

#### 流变记录 (振荡)
- `deadlock_threshold` 是 catalog 中记录的 **12 个最多振荡文件** 之一
- 振荡可能也与 gem5 版本兼容性有关 (string vs integer 格式)

#### 过时评估
- ⚠️ **部分过时**: 设计文档 `high-speed-interconnect-design.md` §7 建议的参数化方案 (interconnect_latency + pending_op_timeout_multiplier) 未完全实现。当前 `deadlock_threshold=20000000` 是硬编码值。建议实现该文档提出的完整参数化方案。

---

### 2.17 节点隔离方案

#### 文档中的设计
> `multi-node-pa-layout.md` §6: 每个 node 有独立 PA 范围; HN-F 只路由到本 node 的 EP-SNF
> `01-current-state-and-requirements.md` §2.1: "ordinary CHI traffic 必须限制在 node 内"

#### 实现中的实际状态
- ✅ 最终拓扑正确实现节点隔离
- ⚠️ **关键发现 (Chunk 9)**: 实现中期发现拓扑被破坏 — "Node 1's L2 directly routed to Node 2's HN-F" (节点隔离被违反)
- ✅ 修复: HN-F addr_ranges 和 downstream_destinations 验证并纠正
- ✅ 跨节点通信仅通过 UBCC (EP-SNF → UBCC → remote EP-RNF → HN-F)

#### 流变记录
- 初始 (Q2): 实现假设节点隔离已由 PA 分区保证
- Chunk 9 发现: 拓扑配置中实际存在跨节点 CHI 路由 (bug)
- 修复后: 严格遵守 `multi-node-pa-layout.md` 的地址分区

#### 过时评估
- ✅ **仍然有效**。但此 bug 的存在表明仅靠 PA 分区不足以保证节点隔离 — 需要 topology wiring 层的显式验证。建议在 `multi-node-pa-layout.md` 中增加 topology verification checklist。

---

### 2.18 EP-RNF 作为普通 CHI RN-F 的定位

#### 文档中的设计
> `01-current-state-and-requirements.md` §6.3: "EP_RNF 作为 RNF 抽象，应尽量使用 HN-F 已有 RNF/directory 语义来表达"
> "sentinel 与普通 CPU cluster RNF 不应走两套平行的 HN 目录格式"

设计预期:
- EP-RNF 使用与 CPU RN-F 相同的 MachineType
- EP-RNF 共享 HN-F 原生 owner/sharer/transient 表达
- 不新增 HN-F 特殊状态
- 如果 EP-RNF 状态超出 HN-F 可表达范围，必须先创建 `OhNo_EP_RNF_NotGooOod.md`

#### 实现中的实际状态
- ✅ EP-RNF 使用 MachineType=Cache (与 CPU RN-F 相同)
- ✅ Version 号区分: `epRnfMachineVersion` 参数 (默认 -1 表示不存在)
- ✅ EPController.py 作为抽象中间基类，不导入 CHIGenericController (避免构建依赖问题)
- ✅ `OhNo_EP_RNF_NotGooOod.md` **未被创建** — EP-RNF 状态始终未超出 HN-F 可表达范围
- ✅ 无平行 sentinel 专用目录格式

#### 流变记录
- Chunk 4: `epRnfMachineVersion` 参数和 pickSharerForSnoop 实现
- Chunk 11: EPController.py 最终版本确认 "Does NOT import CHIGenericController to avoid build-time dependency issues"

#### 过时评估
- ✅ **仍然有效**。EP-RNF 成功作为标准 RN-F 融入现有 HN-F 框架，未触发 OhNo 文档条件。

---

### 2.19 HN-F 地址路由拓扑

#### 文档中的设计
> `multi-node-pa-layout.md` §6: HN-F 只连接 local SN-F + DL_SNF + EP_SNF
> `01-current-state-and-requirements.md` §3.1: Cluster 只连本 node HN 的 downstream 约束

设计预期: 严格 per-node CHI domain 隔离。

#### 实现中的实际状态
- ✅ 最终实现: HN-F downstream_destinations 包括 local EP-SNF + EP-RNF
- ✅ 每个 node 的 EP-RNF 连接本地 HN-F
- ⚠️ 拓扑 bug (Chunk 9, §2.17 已描述) 被修复
- ⚠️ EP-RNF 的 downstream_destinations 在 framework 中设置: `hnf_cntrl.downstream_destinations = [dn_cntrl, ep_rnf]`

#### 过时评估
- ✅ **仍然有效**。但文档 `multi-node-pa-layout.md` §6 仅描述了 SN-F 路由，未提及 HN-F→EP-RNF 连接。建议补充。

---

### 2.20 高速互联假设 (CXL 级延迟)

#### 文档中的设计
> `high-speed-interconnect-design.md` §2.2: "UBCC↔UBCC 延迟 300ns ~ 3ms，折合 300 ~ 3,000,000 ticks (@1GHz)"
> pendingOp 超时从 5M ticks 降至 10K ~ 500K ticks

设计预期:
- EP↔UBCC 延迟 ≈0 (同一进程/主机)
- UBCC↔UBCC 通过高速互联 (CXL 级)
- 事件驱动 + 轻量级轮询兜底

#### 实现中的实际状态
- ⚠️ **混合状态**:
  - EP↔UBCC 在当前单 gem5 进程中延迟 ≈0 (C++ 直接函数调用) — 与设计一致
  - UBCC↔UBCC 延迟: 当前在同一进程内，0-cycle (无真实互联模拟)
  - Chunk 2 分析中提到 0-cycle 导致 reqInPort/datInPort 竞态 (TBE 未分配时 CompData 已到达)
  - OutstandingRequest 使用 timer-based `respTick` 模拟延迟
- ✅ 架构分离准备就绪 (UBCCInterface 抽象类设计完成)
- ❌ 完整互联延迟模拟 (ns-3 集成) 未实现

#### 流变记录
- Phase 2 (Chunk 2): 用户确认 "UBCC之间的互联的延迟进行大幅降低...延迟大致会落在数百纳秒到几毫秒之间"
- 设计方案已制定但互联延迟模拟未完全落地

#### 过时评估
- ⚠️ **部分过时**: 设计文档描述的完整三层架构 (gem5/UBCC/互联) 在当前单进程实现中简化为两层 (gem5 内所有组件)。OutstandingRequest 的 timer-based 延迟是临时方案。`high-speed-interconnect-design.md` 的 Phase 3/4 演进计划 (UBCC 摘出为共享库/独立进程) 未执行。

---

## 3. 总结

### 3.1 最重要的设计流变 (影响最大的 5 个)

| 排名 | 主题 | 流变性质 | 影响 |
|------|------|----------|------|
| 1 | **EP-RNF→HN-F 请求路径** | `sendLocalSnoop` → `ReadOnce` → `CHI ReadShared` | 根本性架构修正。原始实现两次违背用户明确指令和 CHI 规范。最终路径是正确的，但振荡消耗了大量调试时间。 |
| 2 | **sync_wait barrier** | Syscall barrier → spin-wait on DSM load | T0 作为硬前置被绕过，所有多节点 testcase 现依赖 spin-wait。降低了 barrier 语义精度 (无计数、无统一释放)，但在当前场景中足够可靠。 |
| 3 | **pendingOp 串行化** | Fixed timer (1M→2M→5M) → OutstandingRequest 状态机 | 从"调参竞赛"到正确架构的转变。OutstandingRequest 设计提供了确定性并发控制基础。 |
| 4 | **populateGrantData 数据源** | phys_mem functionalAccess → _lastGrantData → OutstandingRequest.dataBuffer | 数据正确性关键修复。前两个版本都可能返回 stale 数据，最终方案确保 recall→grant 数据路径的原子性。 |
| 5 | **DCT Fallback 机制** | 设计: fall to non-DCT snoop → 实际: fall to DMT-disabled ReadNoSnp | 基于 SC_RSC crash 调试的保守决策。偏离设计但提升了安全性。 |

### 3.2 文档过时的根本原因分析

1. **设计在前，实现在后，文档未随迭代更新**
   - `ep-rnf-sharer-registration-plan.md` v3.2 编写于 2026-06-08，代表了那时的最佳理解，但后续 4 个 chunk (8-11) 的修改未被回溯合并。
   - `15-fix-to-pass-tests.md` 是针对 Chunk 6-7 阶段问题的修复计划，其建议的参数化方案被 OutstandingRequest 方案取代后未标注废弃。

2. **实现路径与计划阶段不匹配**
   - 设计计划 `03-phase-plan.md` 采用线性 M3.5→T0→M4→M5→M6→M7 序列
   - 实际开发是非线性、需求驱动的: Q2→Q3→Phase 1→1-2→2-3→3→4→5-6→7-8→9→10
   - 多个阶段交叉执行 (如 Phase 0 MachineID 测试在 Phase 2-3 设计之后才加固)

3. **用户反馈驱动的剧烈方向修正**
   - `sendLocalSnoop` 和 `ReadOnce` 的实现违背了设计文档中明确表述的意图
   - 这些修正暴露了 implementer 和 reviewer 对设计文档理解的不一致
   - 修正后设计文档未更新以强调相关约束

4. **调试发现的设计演进未回传**
   - SC_RSC crash → DCT fallback 目标改为 DMT-disabled ReadNoSnp (Chunk 9)
   - 节点隔离拓扑 bug (Chunk 9)
   - CleanUnique vs ReadUnique 选择 (Chunk 9)
   - 这些发现改变了实现但未更新原始设计文档

5. **测试基础设施演进独立于设计**
   - spin-wait barrier 替代 syscall barrier
   - self-test/workload 分离
   - Phase 0 测试 5 轮加固
   - 这些变更在 `04-test-plan.md` 中未体现

### 3.3 文档更新建议清单

#### 高优先级 (影响后续 M8-M9 和外部协作)

| # | 文档 | 更新内容 |
|---|------|----------|
| 1 | `03-phase-plan.md` T0 节 | 标注 syscall barrier 为 "replaced by spin-wait in e2e_common.h"，保留原设计作为 "future work" |
| 2 | `15-fix-to-pass-tests.md` | 顶部添加 "OBSOLETE" 水印，说明 pendingOp 参数化方案已被 OutstandingRequest 状态机取代 |
| 3 | `ep-rnf-sharer-registration-plan.md` §2.5 | 更新 DCT fallback 描述，反映实际选择 (DMT-disabled ReadNoSnp 而非 non-DCT snoop) |
| 4 | `high-speed-interconnect-design.md` §6 | 更新演进路径，标注当前状态为 "Phase 1 (UBCC 在 gem5 内)"，明确 Phase 2-4 为未执行 |
| 5 | `multi-node-pa-layout.md` §6 | 补充 HN-F→EP-RNF 连接、NCBWrData home DDR4 路由、topology verification checklist |
| 6 | 新建 `docs/alloc-strategy.md` | 记录 `alloc_on_readunique=True` 决策及其对 DSM L3 缓存的影响 |
| 7 | `01-current-state-and-requirements.md` §6.1 | 补充 `alloc_on_readunique` / `alloc_on_readshared` 决策 |

#### 中优先级 (方便后续维护)

| # | 文档 | 更新内容 |
|---|------|----------|
| 8 | `04-test-plan.md` T0 节 | 标注 TC-T0-1~TC-T0-4 为 "not executed — replaced by spin-wait"，保留作为 future work |
| 9 | `ep-rnf-sharer-registration-plan.md` §9.3 | 评估 QUARANTINE 安全网实现状态，若未实现添加 TODO 标记 |
| 10 | `04-test-plan.md` §5-8 | 更新 M6-M8 testcases 状态，标注哪些已通过、哪些部分通过 |
| 11 | 新建 `docs/lessons-learned.md` | 记录 sendLocalSnoop/ReadOnce 振荡教训：当设计文档已明确表达意图时，implementer 必须严格遵循 |
| 12 | `ep-rnf-sharer-registration-plan.md` §4 | 补充 CleanUnique vs ReadUnique 选择说明 (SC→UD write 路径) |

#### 低优先级 (文档质量提升)

| # | 文档 | 更新内容 |
|---|------|----------|
| 13 | 所有设计文档 | 添加 "Last verified against implementation: 2026-06-12" 或等效版本标记 |
| 14 | `docs/recovery/decisions.md` | 补充 D-5 (互联延迟) 当前实现状态说明 |
| 15 | `03-phase-plan.md` | 添加实际执行阶段 vs 计划阶段的对照表 |
| 16 | `01-current-state-and-requirements.md` | 更新 "已通过测试" 列表以反映 Phase 10 后的最终状态 |

---

## 附录 A: 文件修改热度图

以下 12 个文件经历了最多的变更震荡 (按总编辑操作次数排序):

| 文件 | 涉及 Chunk 数 | 编辑次数 | 最终状态 |
|------|-------------|---------|----------|
| `UBCCController.cc` | 10 (1-11) | 139 | ✅ FINAL |
| `EPBackend.cc` | 11 (0-11) | 136 | ✅ FINAL |
| `CHI-cache-actions.sm` | 7 (1-11) | 124 | ✅ FINAL |
| `EPRNFController.cc` | 9 (0-11) | 112 | ✅ FINAL |
| `EPRNFController.hh` | 9 (0-11) | 49 | ✅ FINAL |
| `TBEStorage.hh` | 3 (2-4) | 46 | ✅ FINAL |
| `CHI_ubcc_framework.py` | 10 (0-11) | 35 | ✅ FINAL |
| `UBCCController.hh` | 6 (1-11) | 36 | ✅ FINAL |
| `EPSNFController.cc` | 10 (0-11) | 30 | ✅ FINAL |
| `EPBackend.hh` | 9 (2-11) | 20 | ✅ FINAL |
| `CHI-cache-funcs.sm` | 6 (2-11) | 15 | ✅ FINAL |
| `CHI-cache-transitions.sm` | 4 (1-11) | 15 | ✅ FINAL |

## 附录 B: 实验性修改 (已全部回退或取代)

| 模式 | 涉及文件 | 回退原因 | 最终替代 |
|------|----------|----------|----------|
| `sendLocalSnoop` | `EPRNFController.cc/.hh` | 用户要求: 不允许绕过 HN-F | CHI ReadShared → HN-F |
| `ReadOnce` recall | `EPRNFController.cc` | 用户明确禁止: "不能用ReadOnce" | ReadShared → HN-F |
| TBEStorage debug prints | `TBEStorage.hh` | 根因已查明 (SLICC 双释放) | 移除 |
| Deadlock threshold oscillation | `CHI_ubcc_framework.py` | 值在两个 format 间反复 | 20000000 (integer) |
| pendingOp timer tuning | `UBCCController.cc/.hh` | 定时器无法同时满足所有场景 | OutstandingRequest 状态机 |
| CompAck routing experiments | `EPRNFController.cc` | 前两种方式路由错误 | mapAddressToDownstreamMachine |
| EP-RNF CompAck race fix | `EPRNFController.cc` | 多次方案迭代 | QueuedImmediateResponse + needBarrierClear |

## 附录 C: 测试状态最终快照 (Phase 10 结束)

| TC | 描述 | 最终状态 | 备注 |
|----|------|---------|------|
| TC1 | 单节点读写 | ✅ PASS | 多轮振荡后最终稳定 |
| TC2 | 跨节点读 | ✅ PASS | TBE 断言修复后稳定 |
| TC3 | Ping-pong 交替写 | ✅ PASS | 核心 coherence 测试 |
| TC4 | 单节点 crash-test | ✅ PASS | — |
| TC5 | 单节点 crash-test | ✅ PASS | — |
| TC6 | 多节点同时读 | ❌ FAIL | Phase 10 仍失败 |
| TC7 | — | ❌ FAIL | — |
| TC8 | Upgrade + Invalidate | ✅ PASS | Phase 9 修复后通过 |
| TC9 | Non-DSM negative | ✅ PASS | 负例测试 |
| TC10 | — | ✅ PASS | — |
| TC11 | — | ✅ PASS | — |
