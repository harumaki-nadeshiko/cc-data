# FV-9: UBMsg Field Validation Table

**Summary:** Catalog of UBMsg header/body fields across all 20 message types, flag constraints, field classifications, and identified inconsistencies.

---

## 1. Required header fields per message type

| # | Message Type | Connector | `type` | `srcNode` | `srcSocket` | `dstNode` | `dstSocket` | `homeNode` | `homeSocket` | `ingressSocket` | `requesterNode` | `targetNode` | `flags` | `homeLinePa` | `localLinePa` | `epoch` | `reqId` | `seqNum` | `enqueueTick` | `readyTick` |
|---|--------------|-----------|--------|-----------|-------------|----------|-------------|------------|--------------|-----------------|-----------------|--------------|---------|-------------|--------------|---------|---------|----------|--------------|-------------|
| 1 | **ReadReq** | Adapter→UBCC | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y(homeNode) | Y(WRITE_INTENT) | Y | — | Y | Y | Y | Y | Y |
| 2 | **ReadResp** | UBCC→Adapter | Y | Y | Y | Y | Y | Y | Y | Y | — | Y | — | Y(HAS_DATA) | Y | — | Y | Y | — | — |
| 3 | **RecallReq** | UBCC→Sharer | Y | Y | Y(homeSocket) | Y | Y(homeSocket) | Y | Y | Y(homeSocket) | Y | Y(target) | Y(IS_READ_RECALL\|HAS_DATA) | Y | Y | Y | Y | Y | Y |
| 4 | **RecallResp** | Sharer→UBCC | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | — | Y(DATA_RETURNED\|HAS_DATA) | Y | — | Y | Y | Y | Y |
| 5 | **InvalidateReq** | UBCC→Sharer | Y | Y | Y(homeSocket) | Y | Y(homeSocket) | Y | Y | Y(homeSocket) | Y | Y(target) | — | Y | Y | Y | Y | Y | Y |
| 6 | **InvalidateAck** | Sharer→UBCC | Y | Y | Y | Y | Y | Y | Y | Y | Y | — | — | Y | — | Y | Y | Y | Y |
| 7 | **WritebackReq** | Adapter→UBCC | Y | Y | Y | Y | Y | Y | Y | Y | Y | — | — | Y(KEEP_AS_CLEAN) | Y | — | Y | — | Y | Y |
| 8 | **WritebackResp** | UBCC→Adapter | Y | Y | Y | Y | Y | — | — | — | — | — | — | — | Y | — | Y | Y | — | — |
| 9 | **EvictReq** | Adapter→UBCC | Y | Y | Y | Y | Y | Y | Y | Y | Y | — | — | — | Y | — | Y | — | Y | Y |
|10 | **EvictResp** | UBCC→Adapter | Y | Y | Y | Y | Y | — | — | — | — | — | — | — | Y | — | Y | Y | — | — |
|11 | **UpgradeReq** | Adapter→UBCC | Y | Y | Y | Y | Y | Y | Y | Y | Y | — | — | — | Y | — | Y | Y | Y | Y |
|12 | **UpgradeResp** | UBCC→Adapter | Y | Y | Y | Y | Y | — | — | — | — | — | — | Y(ACCEPTED) | Y | — | Y | Y | — | — |
|13 | **UpgradeDoneReq** | Adapter→UBCC | Y | Y | Y | Y | Y | Y | Y | Y | Y | — | — | — | Y | — | Y | Y | Y | Y |
|14 | **UpgradeDoneResp** | UBCC→Adapter | Y | Y | Y | Y | Y | — | — | — | — | — | — | — | Y | — | Y | Y | — | — |
|15 | **ClearReq** | Adapter→UBCC | Y | Y | Y | Y | Y | Y | Y | Y | Y | — | — | — | Y | — | Y | Y | Y | Y |
|16 | **ClearResp** | UBCC→Adapter | Y | Y | Y | Y | Y | — | — | — | — | — | — | — | Y | — | Y | Y | — | — |
|17 | **UpgradeAckNotify** | UBCC→Adapter | Y | Y | — | Y | — | Y | — | — | Y | — | — | Y(ACCEPTED) | Y | — | Y | Y | Y | Y |
|18 | **QueryLineMetaReq** | Adapter→UBCC | Y | Y | Y | Y | Y | Y | Y | Y | — | — | — | — | Y | — | — | — | Y | Y |
|19 | **QueryLineMetaResp** | UBCC→Adapter | Y | Y | Y | Y | Y | — | — | — | — | — | — | — | Y | — | Y | Y | — | — |
|20 | **HomeWritebackNotify** | HN-F→UBCC | Y | Y | Y | Y | Y | Y | Y | Y | — | — | — | — | Y | — | Y | — | Y | Y |

> **Legend:** Y = explicitly set, — = left default (0). Cells with *(value)* indicate the field is set to a fixed or derived value (not the raw caller parameter). Responses constructed in `UBRouter::deliverToUbcc` systematically omit `seqNum`, `enqueueTick`, `readyTick` (runtime-local fields not needed on the response path).

---

## 2. Body fields per message type

| # | Message Type | Body struct | Body class | Fields | Details |
|---|--------------|-------------|------------|--------|---------|
| 1 | **ReadReq** | `UBReadReqBody` | ✅ Rich | `neededPerm (uint8_t)` | 0=Shared, 1=Unique |
| 2 | **ReadResp** | `UBReadRespBody` | ✅ Rich | `grantType (int8_t)`, `dataSource (int8_t)`, `pendingInvCount (int16_t)`, `grantVisibleTick`, `sentinelVisibleTick`, `recallNeeded (bool)`, `recallOwnerNode (int)`, `authEpoch (uint64_t)`, `committedEpoch (uint64_t)`, `pendingInvMask (uint64_t)`, `grantData[64] (uint8_t[64])` | 11 fields + 64B payload |
| 3 | **RecallReq** | `UBRecallReqBody` | ⬜ Empty | *(no fields)* | Comment: `/* no extra fields beyond header */` |
| 4 | **RecallResp** | `UBRecallRespBody` | ✅ Rich | `data[64] (uint8_t[64])` | 64B cache-line payload |
| 5 | **InvalidateReq** | `UBInvalidateReqBody` | ⬜ Empty | *(no fields)* | |
| 6 | **InvalidateAck** | `UBInvalidateAckBody` | ⬜ Empty | *(no fields)* | |
| 7 | **WritebackReq** | `UBWritebackReqBody` | ⬜ Empty | *(no fields)* | |
| 8 | **WritebackResp** | `UBWritebackRespBody` | 🔹 Lean | `success (bool)` | 1 field |
| 9 | **EvictReq** | `UBEvictReqBody` | ⬜ Empty | *(no fields)* | |
|10 | **EvictResp** | `UBEvictRespBody` | 🔹 Lean | `success (bool)` | 1 field |
|11 | **UpgradeReq** | `UBUpgradeReqBody` | 🔹 Lean | `desiredPerm (uint8_t)`, `cause (uint8_t)` | 2 uint8_t fields |
|12 | **UpgradeResp** | `UBUpgradeRespBody` | 🔹 Lean | `upgradeTargetMask (uint64_t)`, `committedEpoch (uint64_t)` | 2 uint64_t fields |
|13 | **UpgradeDoneReq** | `UBUpgradeDoneReqBody` | ⬜ Empty | *(no fields)* | |
|14 | **UpgradeDoneResp** | `UBUpgradeDoneRespBody` | 🔹 Lean | `accepted (bool)` | 1 field |
|15 | **ClearReq** | `UBClearReqBody` | 🔹 Lean | `reason (uint8_t)` | 0=GrantHandshake |
|16 | **ClearResp** | `UBClearRespBody` | 🔹 Lean | `accepted (bool)` | 1 field |
|17 | **UpgradeAckNotify** | *(none)* 🔴 | ❌ Missing | **No body struct defined** | Not present in `union UBMsgBody` (see §6) |
|18 | **QueryLineMetaReq** | `UBQueryLineMetaReqBody` | 🔹 Lean | `homePa (uint64_t)` | 1 field |
|19 | **QueryLineMetaResp** | `UBQueryLineMetaRespBody` | 🔹 Lean | `found (bool)`, `epoch (uint64_t)`, `ownerNode (int)` | 3 fields |
|20 | **HomeWritebackNotify** | `UBHomeWritebackNotifyBody` | 🔹 Lean | `homePa (uint64_t)` | 1 field |

> **Body class:** ⬜ Empty = no fields; 🔹 Lean = 1–3 trivial fields; ✅ Rich = ≥4 fields or data payload.

---

## 3. Forbidden flag combinations

| Pattern | Forbidden on | Rationale |
|---------|-------------|-----------|
| `WRITE_INTENT` (bit 0) + any request type other than **ReadReq** | WritebackReq, EvictReq, UpgradeReq, etc. | Only ReadReq uses `neededPerm` / write-intent semantics |
| `KEEP_AS_CLEAN` (bit 1) on any message except **WritebackReq** | ReadReq, EvictReq, UpgradeReq, etc. | Semantics are writeback-specific ("keep line as clean after WB") |
| `ACCEPTED` (bit 2) on a **request** message | ReadReq, WritebackReq, EvictReq, UpgradeReq, UpgradeDoneReq, ClearReq, RecallReq, InvalidateReq | ACCEPTED is a response/sideband acknowledgment flag |
| `DATA_RETURNED` (bit 3) on any message except **RecallResp** | ReadResp, InvalidateAck, etc. | Indicates recall buffer data was returned to home |
| `IS_READ_RECALL` (bit 5) on any message except **RecallReq** | InvalidateReq, ReadResp, etc. | Distinguishes read-recall vs writeback-recall |
| `WRITE_INTENT` + `KEEP_AS_CLEAN` together | Any message | Contradictory: one requests write permission, the other requests clean retention |
| `ACCEPTED` + `WRITE_INTENT` on the same message | Any message | ACCEPTED is for upgrade/clear responses; WRITE_INTENT is for read requests |
| `BUSY` (bit 6) is **defined but never set** in code | All types | Bit 6 is reserved but unused — should remain 0 in all messages |

**Runtime flag assertions (not enforced by type system):**
- `HAS_DATA` (bit 4) must be accompanied by a populated `data[]` or `grantData[]` in the body (RecallResp, ReadResp) or by `dataNeeded=true` semantics (RecallReq).
- `DATA_RETURNED` (bit 3) only makes sense when `HAS_DATA` is also set (RecallResp).
- `IS_READ_RECALL` (bit 5) should never appear on an `InvalidateReq` or any non-RecallReq type.

---

## 4. Field classifications

| Classification | Fields | Description |
|----------------|--------|-------------|
| **Semantic** (must preserve across routing hops) | `type`, `requesterNode`, `targetNode`, `flags`, `homeLinePa`, `localLinePa`, `epoch`, `reqId` | Define the coherence operation, its target address, epoch, request identity, and qualifiers |
| **Routing** (used only for message delivery) | `srcNode`, `srcSocket`, `dstNode`, `dstSocket`, `homeNode`, `homeSocket`, `ingressSocket` | Determine source/destination routing through the UBRouter mesh; `ingressSocket` is a NUMA hint used only on the request path |
| **Runtime-local** (set per-hop, not meaningful cross-hop) | `seqNum` (Tx ordering), `enqueueTick`, `readyTick` (scheduling) | Set by the sender; responses from UBRouter omit these (left as 0) — they are only meaningful for the original sender's local queue |

**Cross-classification notes:**
- `homeNode`/`homeSocket` serve dual purpose — they are **routing** fields (used by UBRouter to find the home directory node) but also carry **semantic** meaning (identify which home directory owns the line).
- `flags` is **semantic** because individual bits carry coherence protocol meaning (e.g., WRITE_INTENT, ACCEPTED) and must be preserved end-to-end.
- `reqId` is **semantic** because it ties request-response pairs across nodes.
- `seqNum` is **runtime-local** — never set on responses, only consumed locally by UBRouter for order preservation.

---

## 5. Body access patterns by consumer

| Consumer | Message types consumed | Body fields accessed |
|----------|----------------------|---------------------|
| **UBRouter::deliverToUbcc** | ReadReq, WritebackReq, EvictReq, UpgradeReq, UpgradeDoneReq, ClearReq, RecallResp, InvalidateAck, QueryLineMetaReq, HomeWritebackNotify | `readReq.neededPerm`, `recallResp.data`, `upgradeReq.desiredPerm`/`cause`, `queryLineMetaReq.homePa` (set at sender, not read), others read via flags |
| **UBRouter::deliverToUbcc** (constructing responses) | ReadResp, WritebackResp, EvictResp, UpgradeResp, UpgradeDoneResp, ClearResp, QueryLineMetaResp | Sets body fields on the `response` message: `readResp.*`, `writebackResp.success`, `evictResp.success`, `upgradeResp.*`, `upgradeDoneResp.accepted`, `clearResp.accepted`, `queryLineMetaResp.*` |
| **UBAdapter::sendReadReq** (response read) | ReadResp | `readResp.grantType`, `.grantVisibleTick`, `.sentinelVisibleTick`, `.recallNeeded`, `.recallOwnerNode`, `.dataSource`, `.authEpoch`, `.pendingInvCount`, `.pendingInvMask`, `.committedEpoch`, `.grantData` |
| **UBAdapter::sendWritebackReq** (response read) | WritebackResp | `writebackResp.success` |
| **UBAdapter::sendEvictReq** (response read) | EvictResp | `evictResp.success` |
| **UBAdapter::sendUpgradeReq** (response read) | UpgradeResp | `upgradeResp.upgradeTargetMask`, `.committedEpoch` |
| **UBAdapter::sendUpgradeDoneReq** (response read) | UpgradeDoneResp | `upgradeDoneResp.accepted` |
| **UBAdapter::sendClearReq** (response read) | ClearResp | `clearResp.accepted` |
| **UBAdapter::sendQueryLineMetaReq** (response read) | QueryLineMetaResp | `queryLineMetaResp.found`, `.epoch`, `.ownerNode` |
| **UBAdapter::recvFromRouter** | ReadResp, WritebackResp, EvictResp, UpgradeResp, UpgradeDoneResp, ClearResp, QueryLineMetaResp — plus async: UpgradeAckNotify, RecallReq, InvalidateReq | Reads `readResp.grantType`, `homeLinePa`, `epoch`, `reqId` from header; for RecallReq/InvalidateReq reads flags and header fields only. **UpgradeAckNotify has no body read.** |
| **UBCCController::sendUpgradeAckNotify** | UpgradeAckNotify (constructs) | Only sets header fields; never touches `msg.b` |

---

## 6. Inconsistencies

| # | Severity | Description | Impact |
|---|----------|-------------|--------|
| 🔴 **I1** | **High** | **`UpgradeAckNotify` has no body struct and no `union UBMsgBody` entry.** The type `UBMsgType::UpgradeAckNotify` is defined (line 34) and constructed in `UBCCController.cc` (line 1350), but there is no corresponding `UBUpgradeAckNotifyBody` struct and no member `upgradeAckNotify` in `union UBMsgBody` (lines 187–209). Code works only because the sender never writes to `msg.b` and the receiver (`recvFromRouter` case) reads only header fields. | Any future code that accesses `msg.b.upgradeAckNotify` would silently read garbage from the union's uninitialized memory. |
| 🟡 **I2** | **Medium** | **Response messages systematically omit `seqNum`, `enqueueTick`, `readyTick`.** All responses constructed in `UBRouter::deliverToUbcc` (ReadResp, WritebackResp, EvictResp, UpgradeResp, UpgradeDoneResp, ClearResp, QueryLineMetaResp) do not set these three runtime-local fields. | These fields are runtime-local and not read on the response path, so no functional issue today. However, if any future code inspects `seqNum` on a received response (e.g., for ordering checks), it would see 0. |
| 🟡 **I3** | **Medium** | **`homeNode` and `homeSocket` are omitted from most response messages.** Of all responses, only ReadResp sets `homeNode`/`homeSocket` (to the home UBCC node). WritebackResp, EvictResp, UpgradeResp, UpgradeDoneResp, ClearResp, QueryLineMetaResp omit them entirely. | UBRouter delivers responses via `deliverToAdapter` which does not need `homeNode`/`homeSocket` for routing (dstNode/dstSocket suffice). But the field is part of the spec and may confuse debug traces. |
| 🟡 **I4** | **Medium** | **`ingressSocket` omitted from all responses.** Only requests set `ingressSocket`; responses never carry it. | This is intentional (NUMA hint only needed on request path), but inconsistent with the header struct which always allocates the field. |
| 🟢 **I5** | **Low** | **`reqId` not set on WritebackReq or EvictReq.** These two request types leave `reqId = 0`. | Writeback/Evict are fire-and-forget with no response matching by reqId, so this is harmless but inconsistent with other request types. |
| 🟢 **I6** | **Low** | **`QueryLineMetaReq` does not set `epoch`, `reqId`, `requesterNode`, or `targetNode`.** It only carries `homeLinePa` in the header (plus the body `homePa` field which duplicates it). | The query is a simple lookup needing only the PA, so unused fields are harmless. The body `homePa` field is redundant with `header.homeLinePa`. |
| 🟢 **I7** | **Low** | **`homeLinePa` is duplicated in body for QueryLineMetaReq and HomeWritebackNotify.** Both `UBQueryLineMetaReqBody::homePa` and `UBHomeWritebackNotifyBody::homePa` store the same value as `UBMsgHeader::homeLinePa`. | Wasted memory (8 bytes per message) and a potential source of divergence if one copy is updated but not the other. |

---

## 7. Summary statistics

| Metric | Count |
|--------|-------|
| Total message types | 20 |
| Body structs defined | 19 (all except UpgradeAckNotify) |
| Body structs in union | 19 |
| Empty body types (no fields) | 7 (RecallReq, InvalidateReq, InvalidateAck, WritebackReq, EvictReq, UpgradeDoneReq, **UpgradeAckNotify**) |
| Lean body types (1–3 fields) | 10 (WritebackResp, EvictResp, UpgradeReq, UpgradeResp, UpgradeDoneResp, ClearReq, ClearResp, QueryLineMetaReq, QueryLineMetaResp, HomeWritebackNotify) |
| Rich body types (≥4 fields) | 2 (ReadReq, ReadResp) |
| Body types with data payload (≥64B) | 2 (ReadResp: `grantData[64]`, RecallResp: `data[64]`) |
| Flags defined | 7 (bits 0–6); bit 6 (BUSY) never set |
| Inconsistencies found | 7 (1 high, 3 medium, 3 low) |
