# FV-9: UBMsg Validation Table

**Source:** `gem5/src/mem/ruby/protocol/chi/ep/UBMsg.hh` (276 lines), `UBAdapter.cc` (812 lines)  
**Envelope:** `UBMsgHeader` (18 fields) + `UBMsgBody` (tagged union of 20 bodies)  
**Coverage:** All 20 `UBMsgType` enumerators × header fields × body fields × flag assignments

---

## 1. Message Type Inventory & Body Fields

| # | Message Type | Direction | Body Struct | Body Fields | Wire Size (bytes) |
|---|-------------|-----------|-------------|-------------|-------------------|
| 1 | `ReadReq` | Req → Home | `UBReadReqBody` | `neededPerm` (u8): 0=Shared, 1=Unique | 1 |
| 2 | `ReadResp` | Home → Req | `UBReadRespBody` | `grantType`(i8), `dataSource`(i8), `pendingInvCount`(i16), `grantVisibleTick`(Tick), `sentinelVisibleTick`(Tick), `recallNeeded`(bool), `recallOwnerNode`(int), `authEpoch`(u64), `committedEpoch`(u64), `pendingInvMask`(u64), `grantData[64]`(u8×64) | 180 |
| 3 | `RecallReq` | Home → Owner | `UBRecallReqBody` | *(none)* | 0 |
| 4 | `RecallResp` | Owner → Home | `UBRecallRespBody` | `data[64]`(u8×64) | 64 |
| 5 | `InvalidateReq` | Home → Sharer | `UBInvalidateReqBody` | *(none)* | 0 |
| 6 | `InvalidateAck` | Sharer → Home | `UBInvalidateAckBody` | *(none)* | 0 |
| 7 | `WritebackReq` | Req → Home | `UBWritebackReqBody` | *(none)* | 0 |
| 8 | `WritebackResp` | Home → Req | `UBWritebackRespBody` | `success`(bool) | 1 |
| 9 | `EvictReq` | Req → Home | `UBEvictReqBody` | *(none)* | 0 |
| 10 | `EvictResp` | Home → Req | `UBEvictRespBody` | `success`(bool) | 1 |
| 11 | `UpgradeReq` | Req → Home | `UBUpgradeReqBody` | `desiredPerm`(u8), `cause`(u8): 0=LocalCleanUnique, 1=LocalStoreUpgrade | 2 |
| 12 | `UpgradeResp` | Home → Req | `UBUpgradeRespBody` | `upgradeTargetMask`(u64), `committedEpoch`(u64) | 16 |
| 13 | `UpgradeDoneReq` | Req → Home | `UBUpgradeDoneReqBody` | *(none)* | 0 |
| 14 | `UpgradeDoneResp` | Home → Req | `UBUpgradeDoneRespBody` | `accepted`(bool) | 1 |
| 15 | `ClearReq` | Req → Home | `UBClearReqBody` | `reason`(u8): 0=GrantHandshake | 1 |
| 16 | `ClearResp` | Home → Req | `UBClearRespBody` | `accepted`(bool) | 1 |
| 17 | `UpgradeAckNotify` | Home → Req | `UBUpgradeAckNotifyBody` | *(none — header-only)* | 0 |
| 18 | `QueryLineMetaReq` | Req → Home | `UBQueryLineMetaReqBody` | `homePa`(u64) | 8 |
| 19 | `QueryLineMetaResp` | Home → Req | `UBQueryLineMetaRespBody` | `found`(bool), `epoch`(u64), `ownerNode`(int) | 16 |
| 20 | `HomeWritebackNotify` | HN-F → Home | `UBHomeWritebackNotifyBody` | `homePa`(u64) | 8 |

---

## 2. Header Field Requirements Matrix

`●` = Required (always set by sender), `○` = Optional/conditional, `—` = never set (default 0)

| Header Field | Type | ReadReq | ReadResp | RecallReq | RecallResp | InvalReq | InvalAck | WbReq | WbResp | EvictReq | EvictResp | UpgReq | UpgResp | UpgDoneReq | UpgDoneResp | ClearReq | ClearResp | UpgAckNotify | QueryMetaReq | QueryMetaResp | HomeWbNotify |
|-------------|------|---------|----------|-----------|------------|----------|----------|-------|--------|----------|-----------|--------|---------|------------|-------------|----------|-----------|--------------|--------------|---------------|-------------|
| `type` | UBMsgType | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● |
| `srcNode` | u16 | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● |
| `srcSocket` | u16 | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● |
| `dstNode` | u16 | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● |
| `dstSocket` | u16 | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● |
| `homeNode` | u16 | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● |
| `homeSocket` | u16 | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● |
| `ingressSocket` | u16 | ● | ● | ● | ● | ● | ● | ● | — | ● | — | ● | — | ● | — | ● | — | — | ● | — | ● |
| `requesterNode` | u16 | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | — | — | — |
| `targetNode` | u16 | — | — | ● | — | ● | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| `flags` | u32 | ● | ● | ● | ● | — | — | ● | ● | — | ● | — | ● | — | ● | — | ● | ● | — | ● | — |
| `homeLinePa` | u64 | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● |
| `localLinePa` | u64 | — | — | ● | — | ● | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| `epoch` | u64 | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | — | ● | ● |
| `reqId` | u64 | ● | ● | ● | ● | ● | ● | — | — | — | — | ● | ● | ● | ● | ● | ● | ● | — | — | — |
| `seqNum` | u64 | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● |
| `enqueueTick` | Tick | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● |
| `readyTick` | Tick | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● | ● |

**Notes on conditionality:**
- `ingressSocket` is set in all requests; in responses it is copied from the original request header at the router (not shown as set in adapter recv handlers, but preserved in router reply path at `UBRouter.cc:303`).
- `targetNode` is only populated for cross-node `RecallReq` and `InvalidateReq` — all other messages leave it default 0.
- `localLinePa` is only populated for cross-node `RecallReq` and `InvalidateReq` (carries the node-local PA for the remote adapter).
- `requesterNode` is NOT set for `QueryLineMetaReq`, `QueryLineMetaResp`, or `HomeWritebackNotify` — these use header-only routing.
- `reqId` is NOT set for `WritebackReq/Resp`, `EvictReq/Resp`, `QueryLineMetaReq/Resp`, `HomeWritebackNotify` — these are either fire-and-forget or use epoch-only matching.

---

## 3. Flag Definition & Per-Message Assignment

| Flag | Bit | Set On Messages | Checked On Messages |
|------|-----|----------------|---------------------|
| `UB_FLAG_WRITE_INTENT` | 0 | `ReadReq` (adapter, line 98) | `ReadReq` in router (line 261/275) |
| `UB_FLAG_KEEP_AS_CLEAN` | 1 | `WritebackReq` (adapter, line 206) | `WritebackReq` in router (line 337) |
| `UB_FLAG_ACCEPTED` | 2 | `UpgradeResp` (router, line 394), `UpgradeAckNotify` (UBCC, line 1375) | `UpgradeResp` in adapter (line 336) |
| `UB_FLAG_DATA_RETURNED` | 3 | `RecallResp` (adapter, line 497) | `RecallResp` in router (line 437) |
| `UB_FLAG_HAS_DATA` | 4 | `ReadResp` (router, line 310), `RecallResp` (adapter, line 499), `RecallReq` (adapter, line 586) | `ReadResp` in adapter (lines 155,158), `RecallResp` in router (line 439), `RecallReq` in adapter (line 774) |
| `UB_FLAG_IS_READ_RECALL` | 5 | `RecallReq` (adapter, line 584) | `RecallReq` in adapter (line 772) |
| `UB_FLAG_BUSY` | 6 | **Never set anywhere** | **Never checked anywhere** |

---

## 4. Forbidden / Orphaned Flag Combinations

| Combination | Status | Concern |
|-------------|--------|---------|
| `WRITE_INTENT \| KEEP_AS_CLEAN` | No message sets both | Semantically contradictory, but no guard exists |
| `HAS_DATA` without corresponding body data | Router sets on `ReadResp` only when grantData valid (line 310); adapter sets on `RecallResp` only when `dataBlk && dataReturned` (line 498-499); logically consistent | No runtime validation at receiver |
| `HAS_DATA \| IS_READ_RECALL` both set on `RecallReq` | Allowed — means "data needed" (line 586) | Semantics overloaded: same flag means "body has data" on responses but "data needed" on requests |
| `DATA_RETURNED` without `HAS_DATA` on `RecallResp` | Possible if adapter sets DATA_RETURNED but dataBlk is null (line 497-500: DATA_RETURNED set first, HAS_DATA only if blk non-null) | Router checks both independently (lines 437, 439) — valid but asymmetric |
| `UB_FLAG_BUSY` (bit 6) | **Defined but never set or checked** | Orphaned flag — dead code in enum |
| `flags = 0` on `InvalidateReq/Ack`, `EvictReq`, `UpgradeReq`, `ClearReq`, `HomeWritebackNotify`, responses | These messages are sent with `flags=0` (via default header) | All intentional: these message types don't use flags |

---

## 5. Semantic / Routing / Runtime-Local Classification

| Classification | Messages | Rationale |
|---------------|----------|-----------|
| **Semantic (protocol state machine)** | `ReadReq/Resp`, `RecallReq/Resp`, `InvalidateReq/Ack`, `WritebackReq/Resp`, `EvictReq/Resp`, `UpgradeReq/Resp`, `UpgradeDoneReq/Resp`, `ClearReq/Resp`, `UpgradeAckNotify` | Drive CHI-EP coherence transitions; processed by `EPBackend` or `UBCCController` |
| **Semantic (v4-dual-socket query)** | `QueryLineMetaReq/Resp` | UBCC line metadata queries from EPBackend |
| **Semantic (v4-dual-socket notify)** | `HomeWritebackNotify` | HN-F→UBCC notification of completed DDR4 writeback |
| **Routing (all messages)** | Every message | All carry `srcNode/Socket`, `dstNode/Socket`; routed through `UBRouter` queues |
| **Runtime-local (no wire egress)** | *(none)* | All 20 message types traverse the router — no purely local messages |

**Direction sub-classification (in UBAdapter.cc):**
- **Synchronous (request/response)** — adapter waits for `_lastResponseValid`:
  `ReadReq`, `WritebackReq`, `EvictReq`, `UpgradeReq`, `UpgradeDoneReq`, `ClearReq`, `QueryLineMetaReq`
- **Fire-and-forget (no response)** — adapter returns immediately:
  `RecallReq`, `RecallResp`, `InvalidateReq`, `InvalidateAck`, `HomeWritebackNotify`
- **Async notification** — handled via `recvFromRouter`:
  `UpgradeAckNotify`

---

## 6. Identified Inconsistencies & Gaps

| # | Severity | Location | Issue |
|---|----------|----------|-------|
| I1 | **Low** | `UBMsg.hh:48` | `UB_FLAG_BUSY` defined but never set or checked anywhere — orphaned enum value |
| I2 | **Low** | `UBCCController.cc:1376` | `UpgradeAckNotify` sets `seqNum=0` instead of using `_nextSeq++` like all other sends in adapter — breaks sequencing continuity |
| I3 | **Medium** | Header `targetNode` | Only populated for `RecallReq` / `InvalidateReq`; defaults to 0 for all other messages. Could cause confusion in debugging / log analysis |
| I4 | **Medium** | `UBQueryLineMetaReqBody.homePa` / `UBHomeWritebackNotifyBody.homePa` | Duplicates `header.homeLinePa`. The body field is redundant — router sends the request using header.homeLinePa (UBRouter.cc:464-487) and the body copy is never read |
| I5 | **Low** | `ingressSocket` semantics | In `sendReadReq` it's a parameter (from EPBackend); in all other sends it's hardcoded to `_socketId`. No documentation explains this asymmetry |
| I6 | **Info** | `UBMsg.hh:188` | `UBUpgradeAckNotifyBody` comment: `"v4-P0 fix: FV-9 gap"` — confirms this struct was added to address a prior validation gap. Body is empty (header-only) which is correct |
| I7 | **Low** | `pendingInvCount` type | Declared as `int16_t` but sourced from `getPendingInvalidationCount()` whose return type is `int` (implicit narrowing). Valid for small-scale systems but could overflow |
| I8 | **Info** | Flag semantic overload | `UB_FLAG_HAS_DATA` means "body carries data" on `ReadResp`/`RecallResp` but "data needed from recall target" on `RecallReq`. Same bit, different semantics depending on message type |
| I9 | **Low** | No forbidden-flag validation | No `assert`, `panic`, or `warn` anywhere in the send path checks for invalid flag combinations — purely convention-based |
| I10 | **Info** | `UBMsg.hh:191-214` | Union `UBMsgBody` includes all 20 body structs. `UBUpgradeAckNotifyBody` (empty) is present as member `upgradeAckNotify` — consistent with header-only treatment |
