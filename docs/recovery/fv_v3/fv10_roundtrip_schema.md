# FV-10: Roundtrip Schema — Survival Matrix & Instrument Points

**Source:** `UBMsg.hh` (276 lines), `UBAdapter.cc` (812 lines), `UBRouter.cc` (521 lines), `UBCCController.cc` (2704 lines)  
**Scope:** All 20 `UBMsgType` enumerators traced through send → route → deliver → receive → (optional) response → reverse route → adapter  
**Coverage:** 7 synchronous req/resp pairs, 3 fire-and-forget-to-UBCC, 2 cross-node adapter-to-adapter, 1 async UBCC notification

---

## 1. Roundtrip Pattern Classification

| # | Pattern | Description | Messages | Response Expected |
|---|---------|-------------|----------|-------------------|
| A | **Sync Req→UBCC→Resp** | Adapter send → Router → UBCC handler → response → reverse Router → Adapter `recvFromRouter` | `ReadReq/Resp`, `WritebackReq/Resp`, `EvictReq/Resp`, `UpgradeReq/Resp`, `UpgradeDoneReq/Resp`, `ClearReq/Resp`, `QueryLineMetaReq/Resp` | Yes |
| B | **Fire-and-forget to UBCC** | Adapter send → Router → UBCC handler → no response | `RecallResp`, `InvalidateAck`, `HomeWritebackNotify` | No |
| C | **Cross-node Adapter→Adapter** | Adapter send → Router → remote Router → remote Adapter `recvFromRouter` → fire-and-forget | `RecallReq`, `InvalidateReq` | No |
| D | **Async UBCC→Adapter** | UBCC `sendMessage` → Router → Adapter `recvFromRouter` → `EPBackend::notifyUpgradeAckReady` | `UpgradeAckNotify` | No |

---

## 2. Full-Path Trace Per Message

### 2a. Pattern A — Synchronous Req/Resp

**Generic path (7 pairs):**

| Leg | Component | Function | File:Line |
|-----|-----------|----------|-----------|
| 1 | Adapter builds & sends `*Req` | `sendXxxReq()` | `UBAdapter.cc` |
| 2 | Router enqueues by `(src,dst)` pair | `sendMessage()` → `getOrCreateQueue()` | `UBRouter.cc:92-116` |
| 3 | Router drain loop, local delivery branch | `drainReadyQueues()` | `UBRouter.cc:134-167` |
| 4 | UBCC handler processes request | `deliverToUbcc()` → `processOuter*() ` | `UBRouter.cc:258-499` |
| 5 | Router builds `*Resp` from scratch | `response.h.* = ...` | `UBRouter.cc` per-case |
| 6 | Router enqueues response on reverse queue | `getOrCreateQueue(dst→src)` then `enqueue()` | `UBRouter.cc:174-185` |
| 7 | Router dequeue, adapter delivery branch | `drainReadyQueues()` → `deliverToAdapter()` | `UBRouter.cc:191-203` |
| 8 | Adapter stores `_lastResponse` | `recvFromRouter()` → `case XxxResp` | `UBAdapter.cc:729-748` |

**Per-pair instrument points:**

| Pair | Send (Adapter) | UBCC Handler (Router) | Resp Build (Router) | Recv (Adapter) |
|------|---------------|----------------------|---------------------|----------------|
| `ReadReq/Resp` | `UBAdapter.cc:87-114` | `UBRouter.cc:259-333` | `UBRouter.cc:296-331` | `UBAdapter.cc:730-737` |
| `WritebackReq/Resp` | `UBAdapter.cc:190-209` | `UBRouter.cc:335-351` | `UBRouter.cc:342-351` | `UBAdapter.cc:739` |
| `EvictReq/Resp` | `UBAdapter.cc:244-261` | `UBRouter.cc:354-368` | `UBRouter.cc:359-368` | `UBAdapter.cc:740` |
| `UpgradeReq/Resp` | `UBAdapter.cc:300-321` | `UBRouter.cc:371-398` | `UBRouter.cc:385-398` | `UBAdapter.cc:741` |
| `UpgradeDoneReq/Resp` | `UBAdapter.cc:367-385` | `UBRouter.cc:401-415` | `UBRouter.cc:406-415` | `UBAdapter.cc:742` |
| `ClearReq/Resp` | `UBAdapter.cc:421-441` | `UBRouter.cc:418-432` | `UBRouter.cc:423-432` | `UBAdapter.cc:743` |
| `QueryLineMetaReq/Resp` | `UBAdapter.cc:648-663` | `UBRouter.cc:464-484` | `UBRouter.cc:473-484` | `UBAdapter.cc:744` |

### 2b. Pattern B — Fire-and-forget to UBCC

| Leg | Component | Function | File:Line |
|-----|-----------|----------|-----------|
| 1 | Adapter builds & sends | `sendRecallResp()` / `sendInvalidateAck()` / `sendHomeWritebackNotify()` | `UBAdapter.cc:479-505` / `526-544` / `700-716` |
| 2 | Router enqueue → drain | `sendMessage()` → `drainReadyQueues()` | `UBRouter.cc:92-116, 134-164` |
| 3 | UBCC handler (no response) | `processRecallResponse()` / `processInvalidationAck()` / `processHomeWritebackNotify()` | `UBRouter.cc:435-453` / `456-461` / `487-492` |

### 2c. Pattern C — Cross-node Adapter→Adapter

| Leg | Component | Function | File:Line |
|-----|-----------|----------|-----------|
| 1 | Home Adapter builds & sends | `sendRecallReqToOwner()` / `sendInvalidateReqToSharer()` | `UBAdapter.cc:564-589` / `608-628` |
| 2 | Source Router enqueue | `sendMessage()` → queue | `UBRouter.cc:92-116` |
| 3 | Source Router drain, remote delivery | `drainReadyQueues()` → `getRouter(dst)→sendMessage()` | `UBRouter.cc:213-223` |
| 4 | Destination Router enqueue | `sendMessage()` | `UBRouter.cc:219` |
| 5 | Destination Router drain, adapter delivery | `drainReadyQueues()` → `deliverToAdapter()` | `UBRouter.cc:189-190, 504-517` |
| 6 | Remote Adapter `recvFromRouter` | reconstructs `OuterRecallMsg` / `OuterInvalidateMsg` → `EPBackend` | `UBAdapter.cc:762-801` |

### 2d. Pattern D — Async UBCC→Adapter Notification

| Leg | Component | Function | File:Line |
|-----|-----------|----------|-----------|
| 1 | UBCC builds `UpgradeAckNotify` | `commitIntendedResult()` → `notifyMsg` | `UBCCController.cc:1365-1379` |
| 2 | Router enqueue → drain → adapter delivery | `sendMessage()` → `drainReadyQueues()` → `deliverToAdapter()` | `UBRouter.cc:92-116, 197, 504-517` |
| 3 | Adapter `recvFromRouter` | `EPBackend::notifyUpgradeAckReady()` | `UBAdapter.cc:750-759` |

---

## 3. Header Field Survival Matrix (Synchronous Req→Resp)

Shows which request header fields are **present in the response** when it arrives back at the adapter.  
`✓` = field explicitly copied from request to response, `⚡` = reset conditionally, `(…)` = special case, `✗` = default 0 in response.

### 3a. ReadReq → ReadResp

| Field | Req Value | Response Set? | Notes |
|-------|-----------|---------------|-------|
| `type` | `ReadReq` | ⚡ → `ReadResp` | Changed by router |
| `srcNode` | `_nodeId` | ✗ — response.srcNode = home node | Routing swap |
| `srcSocket` | `_socketId` | ✗ — response.srcSocket = home socket | Routing swap |
| `dstNode` | `homeNode` | ✗ — response.dstNode = req.srcNode | Routing swap |
| `dstSocket` | `homeSocket` | ✗ — response.dstSocket = req.srcSocket | Routing swap |
| `homeNode` | `homeNode` | ✗ — response.homeNode = `_nodeId` | Rebuilt from router identity |
| `homeSocket` | `homeSocket` | ✗ — response.homeSocket = `_socketId` | Rebuilt from router identity |
| `ingressSocket` | caller param | ✓ — `msg.h.ingressSocket` | **Only ReadResp copies this** |
| `requesterNode` | `requesterNode` | ✓ — `msg.h.requesterNode` | Copied in ReadResp only |
| `targetNode` | `homeNode` | ✗ — default 0 | Not preserved |
| `flags` | `WRITE_INTENT` or 0 | ⚡ cleared, may set `HAS_DATA` | Original flags lost |
| `homeLinePa` | `homePa` | ✓ — `msg.h.homeLinePa` | **Always preserved** |
| `localLinePa` | 0 | ✗ — default 0 | Never set in ReadReq path anyway |
| `epoch` | param | ✓ — `msg.h.epoch` | **Preserved in all 7 sync pairs** |
| `reqId` | param | ✓ — `msg.h.reqId` | **Preserved in all 7 sync pairs** |
| `seqNum` | `_nextSeq++` | ✗ — default 0 | Not copied; sequencing is request-local |
| `enqueueTick` | `curTick()` | ✗ — default 0 | Fresh set on response in reverse enqueue |
| `readyTick` | `curTick()` | ✗ — default 0 | Fresh set on response in reverse enqueue |

### 3b. WritebackReq → WritebackResp, EvictReq → EvictResp, UpgradeDoneReq → UpgradeDoneResp, ClearReq → ClearResp, QueryLineMetaReq → QueryLineMetaResp

All five share an identical survival pattern (minimal copy). UpgradeResp is slightly richer.

| Field | All 5 Pairs | UpgradeReq→Resp | Notes |
|-------|-------------|-----------------|-------|
| `homeLinePa` | ✓ | ✓ | Universal survivor |
| `epoch` | ✓ | ✓ | Universal survivor |
| `reqId` | ✓ | ✓ | Not set in WritebackReq/EvictReq/QueryLineMetaReq (0), but response echoes 0 back — technically preserved |
| `flags` | ✗ — default 0 | ⚡ — `ACCEPTED` if accepted | UpgradeResp is the only non-ReadResp that sets flags |
| `type` | ⚡ — `Req→Resp` | ⚡ — `UpgradeReq→UpgradeResp` | Always changes per pair |
| `srcNode` | ✗ — home node | ✗ — home node | Routing rebuild |
| `srcSocket` | ✗ — home socket | ✗ — home socket | Routing rebuild |
| `dstNode` | ✗ — req.srcNode | ✗ — req.srcNode | Routing rebuild |
| `dstSocket` | ✗ — req.srcSocket | ✗ — req.srcSocket | Routing rebuild |
| `ingressSocket` | ✗ — default 0 | ✗ — default 0 | Only ReadResp preserves this |
| `requesterNode` | ✗ — default 0 | ✗ — default 0 | Only ReadResp preserves this |
| All other header fields | ✗ — default 0 | ✗ — default 0 | Not copied |

### 3c. UpgradeResp special fields

| Response Field | Set From | UBRouter.cc:393-397 |
|----------------|----------|---------------------|
| `flags` | `UB_FLAG_ACCEPTED` if `accepted` else 0 | Line 393-394 |
| `upgradeTargetMask` | `_localUbcc->getUpgradePendingTargetMask()` | Line 395 |
| `committedEpoch` | `_localUbcc->getEpochForLine()` | Line 396-397 |

---

## 4. Body Field Survival Matrix

| Pair | Req Body Fields | Resp Body Fields | Body Cross-Reference |
|------|----------------|------------------|---------------------|
| `ReadReq/Resp` | `neededPerm` | `grantType`, `dataSource`, `pendingInvCount`, `grantVisibleTick`, `sentinelVisibleTick`, `recallNeeded`, `recallOwnerNode`, `authEpoch`, `committedEpoch`, `pendingInvMask`, `grantData[64]` | No req body field appears in resp body |
| `WritebackReq/Resp` | *(none)* | `success` | N/A |
| `EvictReq/Resp` | *(none)* | `success` | N/A |
| `UpgradeReq/Resp` | `desiredPerm`, `cause` | `upgradeTargetMask`, `committedEpoch` | No req body field appears in resp body |
| `UpgradeDoneReq/Resp` | *(none)* | `accepted` | N/A |
| `ClearReq/Resp` | `reason` | `accepted` | No req body field appears in resp body |
| `QueryLineMetaReq/Resp` | `homePa` | `found`, `epoch`, `ownerNode` | `homePa` duplicates `header.homeLinePa` (FV-9 I4) |

**Conclusion:** No body data from the request is ever echoed back in the response body. All response bodies are freshly populated by UBCC.

---

## 5. Survival Matrix Summary (Collapsed)

| Field | Read Req/Resp | Wb/Evict/UpgDone/Clear/QMeta | Upg Req/Resp |
|-------|--------------|------------------------------|--------------|
| `type` | ⚡ changes | ⚡ changes | ⚡ changes |
| `srcNode` | ✗ | ✗ | ✗ |
| `srcSocket` | ✗ | ✗ | ✗ |
| `dstNode` | ✗ | ✗ | ✗ |
| `dstSocket` | ✗ | ✗ | ✗ |
| `homeNode` | ✗ | ✗ | ✗ |
| `homeSocket` | ✗ | ✗ | ✗ |
| `ingressSocket` | ✓ | ✗ | ✗ |
| `requesterNode` | ✓ | ✗ | ✗ |
| `targetNode` | ✗ | ✗ | ✗ |
| `flags` | ⚡ | ⚡ | ⚡ |
| `homeLinePa` | ✓ | ✓ | ✓ |
| `localLinePa` | ✗ | ✗ | ✗ |
| `epoch` | ✓ | ✓ | ✓ |
| `reqId` | ✓ | ✓ⁱ | ✓ |
| `seqNum` | ✗ | ✗ | ✗ |
| `enqueueTick` | ✗ | ✗ | ✗ |
| `readyTick` | ✗ | ✗ | ✗ |

ⁱ `reqId` is 0 in WritebackReq/EvictReq/QueryLineMetaReq sends (not set by adapter), so the response's 0 is trivially "preserved".

---

## 6. Response-Build Code Inventory

Every response is constructed in `UBRouter::deliverToUbcc()`. The fields each handler sets:

| Resp Type | `src.*` | `dst.*` | `homeLinePa` | `epoch` | `reqId` | `flags` | `homeNode/Socket` | `ingressSocket` | `requesterNode` |
|-----------|---------|---------|--------------|---------|---------|---------|-----------------|----------------|----------------|
| `ReadResp` | ✓ | ✓ | ✓ | ✓ | ✓ | ⚡ `HAS_DATA` | ✓ (home) | ✓ | ✓ |
| `WritebackResp` | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ |
| `EvictResp` | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ |
| `UpgradeResp` | ✓ | ✓ | ✓ | ✓ | ✓ | ⚡ `ACCEPTED` | ✗ | ✗ | ✗ |
| `UpgradeDoneResp` | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ |
| `ClearResp` | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ |
| `QueryLineMetaResp` | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ |

All seven responses set: `type`, `srcNode`, `srcSocket`, `dstNode`, `dstSocket`, `homeLinePa`, `epoch`, `reqId`.  
Only `ReadResp` additionally sets: `homeNode`, `homeSocket`, `ingressSocket`, `requesterNode`, `flags(HAS_DATA)`.  
Only `UpgradeResp` additionally sets: `flags(ACCEPTED)`.

---

## 7. Identified Roundtrip Gaps

| # | Severity | Gap | Location |
|---|----------|-----|----------|
| G1 | **Low** | `seqNum` is never echoed in responses — all `*Resp` messages carry `seqNum=0`. Any receiver-side ordering that depends on response `seqNum` would break | All `deliverToUbcc` handlers |
| G2 | **Info** | `ingressSocket` is only preserved in `ReadResp` — all other responses lose it (default 0). The adapter does not read `ingressSocket` from non-Read responses, so this is harmless | `UBRouter.cc:303` vs all other handlers |
| G3 | **Low** | `requesterNode` is only preserved in `ReadResp`. For `UpgradeResp`, `_lastResponse.h.requesterNode` is 0 — but the adapter never reads it after response validation | `UBRouter.cc:304` vs other handlers |
| G4 | **Info** | `homeNode`/`homeSocket` are only set in `ReadResp` response header. For all other responses, these fields are 0 — the adapter does not inspect them | `UBRouter.cc:301-302` vs all other handlers |
| G5 | **Low** | `UpgradeAckNotify` sent by UBCC sets `seqNum=0` (hardcoded) instead of incrementing. No sequencing issue because it is a fire-and-forget notification, but breaks the `_nextSeq` convention | `UBCCController.cc:1376` |
| G6 | **Info** | `targetNode` is set on `RecallReq` and `InvalidateReq` cross-node sends but never echoed in any response path — these are fire-and-forget, so no echo is expected | `UBAdapter.cc:574,618` |

(End of file)
