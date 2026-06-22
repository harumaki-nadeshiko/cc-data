# FV-9: UBMsg Field Validation Table

Static schema audit of all 20 UBMsgType enumeration values. Required/Optional/Forbidden fields
derived from `UBMsg.hh` schema definitions cross-referenced against construction paths in
`UBAdapter.cc` (send/construct) and dispatch paths in `UBRouter.cc` (routing + response building).

**Legend**: `H:field` = header field, `B:field` = body field. Tick fields (`enqueueTick`,
`readyTick`) are always set by the Adapter constructor — omitted per-row for brevity.

---

## Message Type: Field Summary Table

| Type | Required Fields | Optional Fields | Forbidden Flags | Value Ranges |
|---|---|---|---|---|
| **ReadReq** | `H:type`, `H:srcNode`, `H:srcSocket`, `H:dstNode`, `H:dstSocket`, `H:homeNode`, `H:homeSocket`, `H:ingressSocket`, `H:requesterNode`, `H:targetNode`, `H:homeLinePa`, `H:epoch`, `H:reqId`, `H:seqNum`, `B:neededPerm` | `H:flags` (WRITE_INTENT), `H:localLinePa` (set to 0) | WRITE_INTENT+any other flag; `HAS_DATA`; `DATA_RETURNED`; `ACCEPTED`; `IS_READ_RECALL`; `KEEP_AS_CLEAN` | `neededPerm` ∈ {0,1}; `homeLinePa` any 64b PA; `src/dst/homeNode` ≤ 65535 |
| **ReadResp** | `H:type`, `H:srcNode`, `H:dstNode`, `H:dstSocket`, `H:homeNode`, `H:homeSocket`, `H:ingressSocket`, `H:requesterNode`, `H:homeLinePa`, `H:epoch`, `H:reqId`, `B:grantType`, `B:dataSource`, `B:pendingInvCount`, `B:grantVisibleTick`, `B:sentinelVisibleTick`, `B:recallNeeded`, `B:recallOwnerNode`, `B:authEpoch`, `B:committedEpoch`, `B:pendingInvMask` | `H:flags` (HAS_DATA), `B:grantData[64]` | WRITE_INTENT; `KEEP_AS_CLEAN`; `DATA_RETURNED`; `IS_READ_RECALL`; `ACCEPTED` | `grantType` ∈ {-1,0,1,2}; `dataSource` ∈ {0,1,2}; `pendingInvCount` ≥ -1; `recallOwnerNode` ≥ -1; `grantVisibleTick` ≤ `sentinelVisibleTick` |
| **RecallReq** | `H:type`, `H:srcNode`, `H:srcSocket`, `H:dstNode`, `H:dstSocket`, `H:homeNode`, `H:homeSocket`, `H:ingressSocket`, `H:requesterNode`, `H:targetNode`, `H:homeLinePa`, `H:localLinePa`, `H:epoch`, `H:reqId`, `H:seqNum` | `H:flags` (IS_READ_RECALL, HAS_DATA) | WRITE_INTENT; `KEEP_AS_CLEAN`; `ACCEPTED`; `DATA_RETURNED` | `localLinePa` = owner's local PA; flags only IS_READ_RECALL ± HAS_DATA; `targetNode` = owner node |
| **RecallResp** | `H:type`, `H:srcNode`, `H:srcSocket`, `H:dstNode`, `H:dstSocket`, `H:homeNode`, `H:homeSocket`, `H:ingressSocket`, `H:requesterNode(=ownerNode)`, `H:homeLinePa`, `H:epoch`, `H:reqId`, `H:seqNum` | `H:flags` (DATA_RETURNED, HAS_DATA), `B:data[64]` | WRITE_INTENT; `KEEP_AS_CLEAN`; `ACCEPTED`; `IS_READ_RECALL` | `data[64]` valid only when `HAS_DATA` set; `DATA_RETURNED` without `HAS_DATA` is suspicious (caller sets both or neither) |
| **InvalidateReq** | `H:type`, `H:srcNode`, `H:srcSocket`, `H:dstNode`, `H:dstSocket`, `H:homeNode`, `H:homeSocket`, `H:ingressSocket`, `H:requesterNode`, `H:targetNode`, `H:homeLinePa`, `H:localLinePa`, `H:epoch`, `H:reqId`, `H:seqNum` | none | Any flag set (all disallowed) | `localLinePa` = sharer's local PA; `targetNode` = sharer node |
| **InvalidateAck** | `H:type`, `H:srcNode`, `H:srcSocket`, `H:dstNode`, `H:dstSocket`, `H:homeNode`, `H:homeSocket`, `H:ingressSocket`, `H:requesterNode`, `H:homeLinePa`, `H:epoch`, `H:reqId`, `H:seqNum` | none | Any flag set (all disallowed) | Fire-and-forget; no body; `requesterNode` = acking node |
| **WritebackReq** | `H:type`, `H:srcNode`, `H:srcSocket`, `H:dstNode`, `H:dstSocket`, `H:homeNode`, `H:homeSocket`, `H:ingressSocket`, `H:requesterNode`, `H:homeLinePa`, `H:epoch`, `H:seqNum` | `H:flags` (KEEP_AS_CLEAN) | Any flag except KEEP_AS_CLEAN; WRITE_INTENT; HAS_DATA; DATA_RETURNED; ACCEPTED | ⚠️ `reqId` **not set** in send path; `targetNode`, `localLinePa` omitted; `epoch` must match home epoch |
| **WritebackResp** | `H:type`, `H:srcNode`, `H:dstNode`, `H:dstSocket`, `H:homeLinePa`, `H:epoch`, `H:reqId`, `B:success` | none | Any flag set | `success` ∈ {true, false} |
| **EvictReq** | `H:type`, `H:srcNode`, `H:srcSocket`, `H:dstNode`, `H:dstSocket`, `H:homeNode`, `H:homeSocket`, `H:ingressSocket`, `H:requesterNode`, `H:homeLinePa`, `H:epoch`, `H:seqNum` | none | Any flag set | ⚠️ `reqId` **not set**; `targetNode`, `localLinePa` omitted; no body |
| **EvictResp** | `H:type`, `H:srcNode`, `H:dstNode`, `H:dstSocket`, `H:homeLinePa`, `H:epoch`, `H:reqId`, `B:success` | none | Any flag set | `success` ∈ {true, false} |
| **UpgradeReq** | `H:type`, `H:srcNode`, `H:srcSocket`, `H:dstNode`, `H:dstSocket`, `H:homeNode`, `H:homeSocket`, `H:ingressSocket`, `H:requesterNode`, `H:homeLinePa`, `H:epoch`, `H:reqId`, `H:seqNum`, `B:desiredPerm`, `B:cause` | none | Any flag set (no flags used) | `desiredPerm` ∈ {0,1,2,...} (0=Shared, 1=Unique); `cause` ∈ {0,1}; omissions: `targetNode`, `localLinePa` |
| **UpgradeResp** | `H:type`, `H:srcNode`, `H:dstNode`, `H:dstSocket`, `H:homeLinePa`, `H:epoch`, `H:reqId`, `B:upgradeTargetMask`, `B:committedEpoch` | `H:flags` (ACCEPTED) | Any flag except ACCEPTED; WRITE_INTENT; HAS_DATA; KEEP_AS_CLEAN | `upgradeTargetMask` = 64b sharer bitmask; `committedEpoch` = home epoch |
| **UpgradeDoneReq** | `H:type`, `H:srcNode`, `H:srcSocket`, `H:dstNode`, `H:dstSocket`, `H:homeNode`, `H:homeSocket`, `H:ingressSocket`, `H:requesterNode`, `H:homeLinePa`, `H:epoch`, `H:reqId`, `H:seqNum` | none | Any flag set | omissions: `targetNode`, `localLinePa`; no body |
| **UpgradeDoneResp** | `H:type`, `H:srcNode`, `H:dstNode`, `H:dstSocket`, `H:homeLinePa`, `H:epoch`, `H:reqId`, `B:accepted` | none | Any flag set | `accepted` ∈ {true, false} |
| **ClearReq** | `H:type`, `H:srcNode`, `H:srcSocket`, `H:dstNode`, `H:dstSocket`, `H:homeNode`, `H:homeSocket`, `H:ingressSocket`, `H:requesterNode`, `H:homeLinePa`, `H:epoch`, `H:reqId`, `H:seqNum`, `B:reason` | none | Any flag set | `reason` = 0 (GrantHandshake); omissions: `targetNode`, `localLinePa` |
| **ClearResp** | `H:type`, `H:srcNode`, `H:dstNode`, `H:dstSocket`, `H:homeLinePa`, `H:epoch`, `H:reqId`, `B:accepted` | none | Any flag set | `accepted` ∈ {true, false} |
| **UpgradeAckNotify** | `H:type`, `H:homeLinePa` | none (header-only) | Any flag set | ⚠️ No construction path found in Adapter; generated internally by UBCC; no body struct (header-only); `src/dst/homeNode/Socket` unverified |
| **QueryLineMetaReq** | `H:type`, `H:srcNode`, `H:srcSocket`, `H:dstNode`, `H:dstSocket`, `H:homeNode`, `H:homeSocket`, `H:ingressSocket`, `H:homeLinePa`, `H:seqNum`, `B:homePa` | none | Any flag set | ⚠️ `requesterNode`, `targetNode`, `localLinePa`, `epoch`, `reqId` **not set**; body `homePa` duplicates header `homeLinePa` |
| **QueryLineMetaResp** | `H:type`, `H:srcNode`, `H:dstNode`, `H:dstSocket`, `H:homeLinePa`, `H:epoch`, `H:reqId`, `B:found`, `B:epoch`, `B:ownerNode` | none | Any flag set | `ownerNode` ≥ -1; `found` ∈ {true, false}; `epoch` returned from UBCC |
| **HomeWritebackNotify** | `H:type`, `H:srcNode`, `H:srcSocket`, `H:dstNode`, `H:dstSocket`, `H:homeNode`, `H:homeSocket`, `H:ingressSocket`, `H:homeLinePa`, `H:epoch`, `H:seqNum`, `B:homePa` | none | Any flag set | ⚠️ `requesterNode`, `targetNode`, `localLinePa`, `reqId` **not set**; body `homePa` duplicates header `homeLinePa`; fire-and-forget |

---

## Flag-to-Message Type Matrix

| Flag | Permitted On | Forbidden On |
|---|---|---|
| `UB_FLAG_WRITE_INTENT` (0x01) | ReadReq | All other types |
| `UB_FLAG_KEEP_AS_CLEAN` (0x02) | WritebackReq | All other types |
| `UB_FLAG_ACCEPTED` (0x04) | UpgradeResp | All other types |
| `UB_FLAG_DATA_RETURNED` (0x08) | RecallResp | All other types |
| `UB_FLAG_HAS_DATA` (0x10) | ReadResp, RecallResp, RecallReq | All other types |
| `UB_FLAG_IS_READ_RECALL` (0x20) | RecallReq | All other types |
| `UB_FLAG_BUSY` (0x40) | **Nowhere** (unused in all code paths) | All types — undefined behavior if set |

---

## Field-Level Integrity Concerns

1. **WritebackReq & EvictReq omit `reqId`** — The `sendWritebackReq` and `sendEvictReq` paths do not set `req.h.reqId`. The `UBMsgHeader` default constructor sets it to 0. The router response path (`deliverToUbcc`) copies `msg.h.reqId` into the response header, meaning the response carries `reqId=0`. This works if the adapter matches by PA+epoch only, but breaks any protocol-level request/response correlation by reqId.

2. **QueryLineMetaReq omits `requesterNode`, `targetNode`, `localLinePa`, `epoch`, `reqId`** — Only 7 of 18 header fields are populated. The router doesn't dereference these in `deliverToUbcc` for this type, but the response copies `epoch` and `reqId` from the request — both remain 0. The response `epoch` field from `queryLineMeta()` (line 508) is the UBCC epoch, not the request epoch, so this is probably safe but inconsistent.

3. **HomeWritebackNotify omits `requesterNode`, `targetNode`, `localLinePa`, `reqId`** — Fire-and-forget, so these are genuinely unused. `epoch` is set (used by UBCC).

4. **UpgradeAckNotify has no construction path in Adapter** — It is generated internally by UBCC and delivered via the router to the local adapter. There is no `sendUpgradeAckNotify()` method. The body is marked `/* no extra fields — header-only notification */` (line 188 of UBMsg.hh). The body union member `upgradeAckNotify` exists but is structurally empty. **Risk**: The header fields `srcNode`, `dstNode`, etc. are not initialized through a normal send path — they depend entirely on how UBCC constructs the message internally.

5. **`UB_FLAG_BUSY` (0x40) is never set or checked** — This flag is defined in the enum but absent from every send and dispatch path. If it appears on any message, it will be silently ignored/forwarded. This is either planned future use or dead code.

6. **`targetNode` is only populated on RecallReq, InvalidateReq, and ReadReq** — Most request types leave it uninitialized (0 from default constructor). This is correct because only cross-node messages (Recall, Invalidate) need `targetNode`, but a future message routing change that relies on `targetNode` being valid for other types would silently malfunction.

7. **`localLinePa` is only populated on RecallReq and InvalidateReq** — All other messages leave it 0. Correct by design (only cross-node ownership transfers need the owner's local PA).

8. **`WritebackReq` sets `ingressSocket = _socketId`** (adapter line 198), which differs from other request types that use the `homeSocket` from their parameter. This is not necessarily wrong, but it's inconsistent — the ingress socket is the requester's socket, while the dstSocket is the home's socket.

9. **Response messages (ReadResp, WritebackResp, EvictResp, UpgradeResp, UpgradeDoneResp, ClearResp, QueryLineMetaResp) do not set `seqNum`** — Responses built in `deliverToUbcc` do not populate `seqNum`, leaving it 0. The adapter response-matching logic does not use `seqNum`, so this is safe, but the field is technically absent on all response types.

10. **`QueryLineMetaReq` body `homePa` duplicates header `homeLinePa`** — The body contains `uint64_t homePa` which is set to the same `homeLinePa` from the header. This is redundant and a potential source of inconsistency if the two diverge.
