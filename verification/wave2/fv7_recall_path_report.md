# FV-7: Recall Data Path End-to-End Verification

**Summary**: Two-path trace (Read Recall / Write Recall) from `initiateRecall()` through outer message, CHI snoop, data capture, recall response, and home installation. All hops verified for epoch/reqId preservation, dataBlk integrity, and owner/target identity.

---

## 1. Read Recall Trace (owner has G_E → downgrade to shared)

| Hop | Component | Function | Lines | Data Flow | Epoch Check | reqId Check |
|-----|-----------|----------|-------|-----------|-------------|-------------|
| 1 | UBCCController | `processOuterRequest` (G_E/G_M case) | 708–872 | Detects existingOwner != requester → sets `outRecallNeeded=true`, `outRecallOwnerNode=existingOwner` | `committedEpoch` stored as `baseEpoch` in RECALL OutstandingRequest | `reqId` stored in `recallOreq->reqId` |
| 2 | UBCCController | `initiateRecall` | 1053–1065 | Creates empty `OutstandingRequest` with `opType=RECALL`, `stage=WAITING_TARGET_RESP` | N/A (just init) | reqId passed through |
| 3 | EPBackend | `handleRemoteMiss` | 663–689 | Builds `OuterRecallMsg{linePa=homePa, ownerLocalPa, isReadRequest=true, dataNeeded=true}` | Uses `committedEpoch` from UBCC response | `reqIdVal` from requester entry |
| 4 | EPBackend | OuterRecallMsg construction | 672–681 | `recallMsg.epoch = committedEpoch; recallMsg.reqId = reqIdVal; recallMsg.isReadRequest = (reqType==GlobalReadShared)` | `committedEpoch` stored | Same `reqIdVal` throughout |
| 5 | UBAdapter | `sendRecallReqToOwner` | 550–590 | Packs RecallReq UBMsg: `h.epoch=recallMsg.epoch, h.reqId=recallMsg.reqId, h.homeLinePa, h.localLinePa` | `msg.h.epoch = recallMsg.epoch` | `msg.h.reqId = recallMsg.reqId` |
| 6 | UBRouter | RecallReq routing | 215–216, 229 | Routes `RecallReq` to **destination node's** local UBAdapter via `deliverToAdapter(msg)` | Forwarded in UBMsg header | Forwarded in UBMsg header |
| 7 | UBAdapter | RecallReq delivery | 762–782 | Reconstructs `OuterRecallMsg{linePa, ownerLocalPa, epoch, reqId}` from UBMsg fields | `recallMsg.epoch = msg.h.epoch` | `recallMsg.reqId = msg.h.reqId` |
| 8 | EPBackend | `handleRecallRequest` | 1178–1298 | Validates `recallMsg.ownerNode == _nodeId`, stores `_lastRecallMsg`; calls `_epRnfCtrl->startReadShared(ownerLocalPa, callback)` | `capturedMsg.epoch` saved in lambda | `capturedMsg.reqId` saved in lambda |
| 9 | EP-RNF | `startReadShared` | 1088–1130 | Creates `PendingChiTxn{op=ReadShared}`, sends CHI `ReadShared` to HN-F via `sendChiRequest` | `txn.epoch=0` (filled by caller) | `txn.reqId=0` |
| 10 | EP-RNF | `recvDataMsg` (CompData_SC/CompData_I) | 470–517 | Receives `CompData`, stores `it->second.recallDataBlk = msg->getdataBlk()`, sets `recallDataValid = true` | N/A (CHI-level) | N/A (CHI-level) |
| 11 | EP-RNF | `finishChiTxn` | 899–942 | **Transfers recall data**: `_backend->setRecallCaptureData(txn.recallDataBlk, true)`, then erases txn, invokes callback | N/A (data transfer) | N/A (data transfer) |
| 12 | EPBackend | ReadShared callback (lambda) | 1249–1267 | Builds `OuterRecallResponse{linePa, ownerNode, homeNode, epoch, reqId}` from `capturedMsg`; sets `resp.dataPayload = _recallCaptureDataBlock` if `dataNeeded && success` | `resp.epoch = capturedMsg.epoch` | `resp.reqId = capturedMsg.reqId` |
| 13 | EPBackend | `sendRecallResponse` | 1301–1344 | Optionally writes data to home physical memory via `HomeMemoryService::write(response.linePa, buf, 64)`; then calls `adapter->sendRecallResp(...)` | `response.epoch` passed | `response.reqId` passed |
| 14 | UBAdapter | `sendRecallResp` | 462–506 | Packs RecallResp UBMsg: `h.epoch=epoch, h.reqId=reqId`, `memcpy(req.b.recallResp.data, dataBlk->getData(0,64), 64)` | `msg.h.epoch = epoch` | `msg.h.reqId = reqId` |
| 15 | UBRouter | RecallResp routing | 187, 197–199, 461–479 | Routes to home UBCC; in `deliverToUbcc` → creates `DataBlock` from `msg.b.recallResp.data`, calls `_localUbcc->processRecallResponse(...)` | `msg.h.epoch` forwarded | `msg.h.reqId` forwarded |
| 16 | UBCCController | `processRecallResponse` | 1068–1167 | Validates epoch (half-range), looks up `OutstandingRequest` with `opType==RECALL`, checks `targetNode == ownerNode`, checks `reqId == ost->reqId`; **copies `dataBlk->getData(0,64)` into `ost->dataBuf[64]`**, sets `ost->dataValid=true` | `checkEpochForLine(line_pa, responseEpoch)` — rejects stale | `ost->reqId != 0 && ost->reqId != reqId` — rejects mismatch |
| 17 | UBCCController | processOuterRequest (G_E/G_M retry) | 708–778 | Finds RECALL `stage=DONE`, removes it via `removeOutstanding`, creates new `GRANT_HANDSHAKE`; **copies `memcpy(grantOreq->dataBuf, recallData.dataBuf, 64)`** → F3 data pipeline | `grantOreq->baseEpoch = recallData.baseEpoch` | `grantOreq->reqId = recallData.reqId` |
| 18 | UBCCController | GRANT_HANDSHAKE creation | 731–778 | Sets `grantOreq->dataSource = GrantDataSource::RecallBuffer`; returns grant decision | Same baseEpoch | Same reqId |
| 19 | UBRouter | ReadResp construction | 316–337 | If `dataSource == RecallBuffer`, calls `copyOutstandingGrantData(linePa, grantData)` → reads `ost.dataBuf` | `response.h.epoch = msg.h.epoch` | `response.h.reqId = msg.h.reqId` |
| 20 | EPBackend | `handleRemoteMiss` (grant receipt) | 648–652, 823–828 | Receives `routedGrantData, routedGrantDataValid`; if `dataSource == RecallBuffer`, calls `setRecallCaptureData(routedGrantData, routedGrantDataValid)`; then calls `populateGrantData(homePa, dataSource)` | Uses `entry.epoch` / `authEpoch` for Clear | `reqIdVal` used for Clear |
| 21 | EPBackend | `populateGrantData` (RecallBuffer) | 925–946 | Reads `_recallCaptureDataBlock` into `_lastGrantDataBlock`; sets `_lastGrantDataValid=true`; consumes buffer (`_recallCaptureDataValid=false`) | N/A (data source) | N/A (data source) |
| 22 | EPBackend | `sendClear` | 830 | Sends Clear with `grantEnv.epoch, grantEnv.reqId` | `grantBaseEpoch` used | `reqIdVal` used |
| 23 | EPBackend/EP-RNF | READY state (F3) | 900–941 | Grant data from `_lastGrantDataBlock` used by `EPSNFController` to create `CompData` response for requesting CPU | epoch/reqId carried in Clear | reqId carried in Clear |

---

## 2. Write Recall Trace (owner has G_M → invalidate owner)

| Hop | Component | Function | Lines | Data Flow | Epoch Check | reqId Check |
|-----|-----------|----------|-------|-----------|-------------|-------------|
| 1 | UBCCController | `processOuterRequest` (G_M case) | 708–872 | Detects `dirty=true`, existingOwner != requester, `isReadRequest=false` → recall with `dataNeeded=true` | Same epoch | Same reqId |
| 2 | EPBackend | OuterRecallMsg construction | 672–681 | `isReadRequest = (reqType==GlobalReadShared)` → **false**; `dataNeeded = true` | `committedEpoch` | `reqIdVal` |
| 3 | UBAdapter | `sendRecallReqToOwner` | 550–590 | `UB_FLAG_IS_READ_RECALL` NOT set; `UB_FLAG_HAS_DATA` set | `msg.h.epoch` | `msg.h.reqId` |
| 4 | UBAdapter | RecallReq delivery | 762–782 | `recallMsg.isReadRequest = (flags & UB_FLAG_IS_READ_RECALL) != 0` → **false**; `recallMsg.dataNeeded = (flags & UB_FLAG_HAS_DATA) != 0` → true | `recallMsg.epoch = msg.h.epoch` | `recallMsg.reqId = msg.h.reqId` |
| 5 | EPBackend | `handleRecallRequest` | 1268–1294 | `isReadRequest` is false → calls `_epRnfCtrl->startReadUnique(ownerLocalPa, callback)` with `RecallUnique` proxy op | `capturedMsg.epoch` | `capturedMsg.reqId` |
| 6 | EP-RNF | `startReadUnique` | 1134–1180 | Creates `PendingChiTxn{op=ReadUnique, proxyOp=EpProxyOp_RecallUnique}`; sends CHI `ReadUnique` (with `RecallUnique` proxy) | `txn.epoch=0` (caller fills) | `txn.reqId=0` |
| 7 | EP-RNF | `recvDataMsg` (CompData_UD_PD) | 526–547 | Receives `CompData_UD_PD` (dirty+unique), stores `recallDataBlk = msg->getdataBlk()`, `recallDataValid = true` | N/A (CHI-level) | N/A (CHI-level) |
| 8 | EP-RNF | `finishChiTxn` | 899–942 | Same data transfer: `_backend->setRecallCaptureData(txn.recallDataBlk, true)` | N/A | N/A |
| 9 | EPBackend | ReadUnique callback (lambda) | 1275–1293 | Builds `OuterRecallResponse{}`; `resp.dataPayload = _recallCaptureDataBlock` if `dataNeeded && success && _recallCaptureDataValid` | `resp.epoch = capturedMsg.epoch` | `resp.reqId = capturedMsg.reqId` |
| 10 | EPBackend | `sendRecallResponse` | 1301–1344 | Installs to home memory, sends `sendRecallResp` with data | Same | Same |
| 11 | UBAdapter | `sendRecallResp` | 462–506 | `memcpy(req.b.recallResp.data, dataBlk, 64)` — captures dirty bytes | `msg.h.epoch` | `msg.h.reqId` |
| 12 | UBRouter | RecallResp → UBCC | 197, 461–479 | Reconstructs `DataBlock`; calls `processRecallResponse(linePa, requesterNode, dataReturned, msg.h.epoch, msg.h.reqId, dataPtr)` | epoch forwarded | reqId forwarded |
| 13 | UBCCController | `processRecallResponse` | 1068–1167 | **Copies data**: `memcpy(ost->dataBuf, dataBlk->getData(0,64), 64)`; epoch and reqId validated before accepting | `checkEpochForLine` | `ost->reqId != reqId` rejection |
| 14 | UBCCController | RECALL→GRANT_HANDSHAKE (G_M retry) | 708–778 | Removes RECALL, creates GRANT_HANDSHAKE; `memcpy(grantOreq->dataBuf, recallData.dataBuf, 64)`, `dataSource=RecallBuffer` | `grantOreq->baseEpoch = recallData.baseEpoch` | `grantOreq->reqId = recallData.reqId` |
| 15 | UBRouter | ReadResp with dirty data | 316–337 | `copyOutstandingGrantData` reads `ost.dataBuf` (now has dirty owner data) | epoch/reqId in header | epoch/reqId in header |
| 16 | EPBackend | `populateGrantData` (RecallBuffer) | 925–946 | Dirty data from owner arrives as `_recallCaptureDataBlock` → `_lastGrantDataBlock`; consumed once | N/A | N/A |

---

## 3. Data Integrity Verification: processRecallResponse → OutstandingRequest

**`UBCCController::processRecallResponse`** (lines 1151–1153):
```cpp
if (dataBlk && dataReceived) {
    memcpy(ost->dataBuf, dataBlk->getData(0, 64), 64);
    ost->dataValid = true;
}
```

**`UBCCController::copyOutstandingGrantData`** (lines 2146–2165):
```cpp
if (!ost.dataValid) return false;
if (ost.opType != GRANT_HANDSHAKE && ost.opType != RECALL) return false;
outBlk.setData(ost.dataBuf, 0, 64);
return true;
```

**Integrity chain**: `dataPayload` (OuterRecallResponse) → `msg.b.recallResp.data` (UBMsg) → `DataBlock` (UBRouter) → `memcpy(ost->dataBuf)` → `ost.dataBuf` → `outBlk.setData(ost.dataBuf)` → `routedGrantData` (EPBackend). No data truncation or byte-swap between hops. All copies are raw `memcpy` of 64 bytes.

---

## 4. F3 Data Source Verification: RecallBuffer wiring

**RecallBuffer path is wired through** `populateGrantData`:

| Step | Location | Lines | What happens |
|------|----------|-------|-------------|
| 1 | UBCCController sets dataSource | 746, 856, 868 | `grantOreq->dataSource = GrantDataSource::RecallBuffer` |
| 2 | UBRouter detects RecallBuffer | 316–320 | `if (dataSource == RecallBuffer) { copyOutstandingGrantData(...) }` → reads data from ost.dataBuf |
| 3 | UBRouter packs into ReadResp | 335–337 | Sets `UB_FLAG_HAS_DATA` flag, data goes to `msg.b.recallResp.data` |
| 4 | EPBackend receives routedGrantData | 648, 823–825 | `if (dataSource == RecallBuffer) setRecallCaptureData(routedGrantData, routedGrantDataValid)` |
| 5 | EPBackend.populateGrantData | 925–946 | `case RecallBuffer: _lastGrantDataBlock = _recallCaptureDataBlock; consume once` |

**Guards against stale data**:
- `_recallCaptureDataValid` is consumed (`set false`) after `populateGrantData` reads it (line 930)
- `_recallCaptureDataValid` is cleared before each recall initiation (lines 1247, 1273)
- `populateGrantData` with `RecallBuffer` but invalid buffer → warning + `_lastGrantDataValid=false` (line 941)

---

## 5. Owner/Target Identity Verification

| Check | Location | Lines | Mechanism |
|-------|----------|-------|-----------|
| Recall target mismatch | `EPBackend::handleRecallRequest` | 1194–1198 | `fatal()` if `recallMsg.ownerNode != _nodeId` |
| Recall owner mismatch | `UBCCController::processRecallResponse` | 1108–1113 | `ost->targetNode != ownerNode` → reject with warning |
| Snoop identity | CHI-cache-actions `UpdateDirState_FromSnpRespData` | 2443–2478 | Asserts `tbe.dir_owner == in_msg.responder` for PD types |
| OutstandingRequest target | `UBCCController::processOuterRequest` (recall creation) | 842, 860 | `createOutstanding(line_pa, RECALL, requesterNode, existingOwner)` — target set to existing owner |
| OuterRecallMsg ownerLocalPa | `EPBackend::handleRemoteMiss` | 674–675 | `_addrMap.buildDsmPA(recallOwnerNode, homeNode, offset)` — correctly maps owner's local PA |

---

## 6. Epoch/ReqId Preservation Summary

All hops preserve both `epoch` and `reqId`:

| Boundary | Epoch Source | reqId Source | Propagation |
|----------|-------------|--------------|-------------|
| UBCC → OuterRecallMsg | `committedEpoch` | `reqIdVal` | Direct assignment |
| OuterRecallMsg → UBMsg RecallReq | `msg->epoch` | `msg->reqId` | Header field copy |
| UBMsg → UBAdapter (target) | `msg.h.epoch` | `msg.h.reqId` | 1:1 field reconstruction |
| UBAdapter → handleRecallRequest | `recallMsg.epoch` | `recallMsg.reqId` | Struct field |
| handleRecallRequest → lambda | `capturedMsg.epoch` | `capturedMsg.reqId` | Captured by value |
| lambda → OuterRecallResponse | `capturedMsg.epoch` | `capturedMsg.reqId` | Direct assignment |
| OuterRecallResponse → UBMsg RecallResp | `response.epoch` | `response.reqId` | Header field |
| UBMsg → UBRouter → UBCC | `msg.h.epoch` | `msg.h.reqId` | Header field |
| UBCC processRecallResponse validation | `checkEpochForLine(line_pa, responseEpoch)` | `ost->reqId != 0 && ost->reqId != reqId` | Half-range epoch check + exact reqId match |
| RECALL→GRANT_HANDSHAKE transition | `grantOreq->baseEpoch = recallData.baseEpoch` | `grantOreq->reqId = recallData.reqId` | Field copy across outstanding objects |
| Clear message | `grantEnv.epoch` | `grantEnv.reqId` | Payload envelope |

---

## 7. Read vs Write Recall Key Differences

| Aspect | Read Recall (G_E owner) | Write Recall (G_M owner) |
|--------|------------------------|-------------------------|
| CHI request | `ReadShared` | `ReadUnique` |
| CHI proxy op | `EpProxyOp_NoProxyOp` | `EpProxyOp_RecallUnique` (§4.5.4) |
| CompData type | `CompData_SC` / `CompData_I` | `CompData_UD_PD` (dirty data with push-drop) |
| Owner final state | Downgraded to shared (R_S) | Invalidated (R_I) |
| Data always returned? | Only if `dataNeeded=true` (read recall of clean G_E may skip) | Yes — dirty data must be captured |
| UB_FLAG_IS_READ_RECALL | Set in UBMsg RecallReq | NOT set |
| _requesterLines update | `it->second.state = R_S` (line 1218) | `it->second.state = R_I` (line 1221) |

---

## 8. Identified Risks & Observations

1. **Epoch zero in PendingChiTxn**: `startReadShared` (line 1105) and `startReadUnique` (line 1153) set `txn.epoch=0, txn.reqId=0`. Comment says "filled by caller via EPBackend". The epoch/reqId are not needed within EP-RNF for the CHI protocol — they are preserved by the outer message capture (OuterRecallMsg captured by value in the lambda) and restored in the callback. This is correct.

2. **`populateGrantData` uses `_recallCaptureDataBlock` not `routedGrantData`**: At line 823, if `dataSource == RecallBuffer`, `setRecallCaptureData(routedGrantData, ...)` is called. Then at line 828, `populateGrantData(homePa, dataSource)` reads `_recallCaptureDataBlock`. This means `routedGrantData` is the **same data** that flowed through `ost->dataBuf` → `copyOutstandingGrantData`, and it is re-deposited into `_recallCaptureDataBlock`, then read back out. This double-buffering is safe but redundant — the intermediate `setRecallCaptureData` call is the bridge from the UBCC's ost->dataBuf to EPBackend's recall capture slot.

3. **Double-buffer consumption**: `_recallCaptureDataValid` is set true in `finishChiTxn` (line 914). Then in the callback (lines 1259–1265), it's used to populate the response. Then later in `populateGrantData` (line 930), it's consumed again. Between callback (which sends the response) and `populateGrantData` (which builds the grant data), the buffer must remain valid. The callback fires synchronously from `finishChiTxn`, so both happen before the outer request grant processing. Correct.

4. **FIRE-AND-FORGET for RecallResp**: UBRouter lines 197–199 and 478 confirm RecallResp is fire-and-forget — no response message follows. The UBCC processes it inline in `deliverToUbcc`. This means the router must not drop the message; if lost, the recall deadlocks. No retry mechanism is visible.

5. **processRecallResponse epoch check is half-range**: Uses `normalizeEpoch` + `checkEpochForLine` which applies `isNewerEpoch` half-range comparison (line 2170). This guards against stale responses from delayed delivery across the recall path.

---

## 9. Push-Grant Delivery (2026-07-10 change)

The Read grant path was converted from **pull+retry** to **home-pushed grant**. This
affects hops 19–20 of §1/§2 (the "grant back to requester" leg), not the
RECALL data-capture path (hops 1–18), which is unchanged.

### 9.1 What changed
- **Before (pull)**: after `processRecallResponse` armed the grant (`replayArmed=true`),
  the requester had to *re-issue* a ReadReq and pull the grant. If the requester was
  the home node (requester==home), no network ReadResp was ever produced, so the
  requester only rediscovered the grant on its next EP-SNF retry-timer tick (~8.4µs).
- **After (push)**: at the three `replayArmed=true` points (RECALL→GRANT
  `UBCCController.cc` ~1212, INVALIDATE→GRANT ~1496, queue-replay ~2737), the home now
  builds a complete ReadResp from the `grantOst` fields (`buildGrantResponse`) and
  pushes it to the requester via the existing `_outbound` channel (`sendGrantPush`),
  reusing the same delivery route as RecallReq/InvalidateReq/UpgradeAckNotify.

### 9.2 Correctness / fault argument (why formal model is unaffected)
- **No new commit path.** A grant only becomes committed on **Clear**, which is already
  the fault-modeled message in `ubcc_transport_faults.tla` (explicit
  Deliver/Drop/Duplicate queue). Push changes *when/how the requester learns the grant
  is ready*, not *when the directory commits*. The commit invariants
  (`NoDoubleCommit`, `EpochMonotonic`, `SharersCanonical`, `ReserveNotCommit`) are
  therefore untouched.
- **Idempotent delivery.** The pushed ReadResp lands in the requester UBAdapter's
  `_readyResponses[(ReadResp,reqId)]` and is consumed once (`erase` on hit,
  `UBAdapter.cc:356`). A racing self-retry sees the same key → no double grant.
- **Fallback preserved.** If `sendGrantPush` fails, `replayArmed` remains set and the
  20000-cycle EP-SNF retry timer still drives the requester to pull — i.e. push is a
  latency optimization layered on top of the pull fallback, not a new single point of
  failure. (Contrast §8.4: RecallResp is fire-and-forget; the *grant* now has a pull
  fallback behind it.)
- **TLA+ result.** No spec change was required. The push is a transport-layer delivery
  detail, and `ubcc_protocol_core.tla` abstracts the transport
  (`ubcc_protocol.tla` `NetWellFormed == TRUE`). All modeled transitions
  (`RecallToGrant`, `BarrierAck` INVALIDATE branch) are identical.

### 9.3 TLC re-run (2026-07-10, post-change, specs unchanged)
| Config | Spec | Result |
|---|---|---|
| `ubcc_config` | Spec (safety, MaxEpoch=4) | **PASS** — 20.98M distinct states, no error |
| `ubcc_multi_pa` | Spec | **PASS** — 45,760 states |
| `ubcc_multi_socket` | Spec | **PASS** — 58,561 states |
| `ubcc_liveness` | FairSpec (P1–P4) | **PASS** — no error |
| `ubcc_transport_faults` | TFSpec (safety) | **PASS** — 23.24M distinct states |
| `ubcc_transport_faults_liveness` | TFFairSpec | **PASS** (FaultRecallProgress) |
| `ubcc_liveness_nocleanup` | FairSpecNoCleanup | **FAIL as designed** — RecallProgress counterexample (negative control: proves the model detects the orphan wedge) |
| `ep_intra_node` / `ep_intra_node_dual` | Spec / DualSpec | **PASS** |
| `ep_intra_node_single` | Spec | Inconclusive — state explosion (>125M states, 96% depth, **no violation found**); pre-existing scale issue, unchanged by push. |
