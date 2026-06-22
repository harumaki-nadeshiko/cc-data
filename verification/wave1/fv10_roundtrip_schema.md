# FV-10: UBMsg Serialization Round-Trip — Semantic + Routing Field Integrity

**Summary**: Tracks every UBMsg header/body field through send-side construction and receive-side reconstruction for all 12 message flows. Fields are classified as PRESERVED (identical value round-trips), LOCAL-ONLY (set per-side from local state), or UNCHECKED (sent but never inspected on receive).

---

## ReadReq → ReadResp (UBAdapter line 86→UBRouter line 285→UBAdapter line 730)

| Field | Category | Send-Side Line | Recv-Side Line | Preserved? |
|---|---|---|---|---|
| **Header** | | | | |
| `type` | LOCAL-ONLY | L88: `= UBMsgType::ReadReq` | L285: switch dispatch | N/A (resp uses ReadResp) |
| `srcNode` | PRESERVED | L89: `= _nodeId` | L325: `resp.dstNode = msg.h.srcNode` | Yes (as dst) |
| `srcSocket` | PRESERVED | L90: `= _socketId` | L326: `resp.dstSocket = msg.h.srcSocket` | Yes (as dst) |
| `dstNode` | LOCAL-ONLY | L91: `= homeNode` | routing use only | Not reconstructed |
| `dstSocket` | LOCAL-ONLY | L92: `= homeSocket` | routing use only | Not reconstructed |
| `homeNode` | UNCHECKED | L93: `= homeNode` | not read | N/A |
| `homeSocket` | UNCHECKED | L94: `= homeSocket` | not read | N/A |
| `ingressSocket` | PRESERVED | L95: `= ingressSocket` | L329: `resp.ingressSocket = msg.h.ingressSocket` | Yes |
| `requesterNode` | PRESERVED | L96: `= requesterNode` | L302: `msg.h.requesterNode`→`processOuterRequest` | Yes |
| `targetNode` | UNCHECKED | L97: `= homeNode` | not read | N/A |
| `flags` | PRESERVED | L98: `= writeIntent ? UB_FLAG_WRITE_INTENT : 0` | L287: `msg.h.flags & UB_FLAG_WRITE_INTENT` | Yes (flag) |
| `homeLinePa` | PRESERVED | L99: `= homePa` | L300: `msg.h.homeLinePa`→`processOuterRequest` | Yes |
| `localLinePa` | UNCHECKED | L100: `= 0` | not read | N/A |
| `epoch` | PRESERVED | L101: `= epoch` | L303: `msg.h.epoch`→`processOuterRequest` | Yes |
| `reqId` | PRESERVED | L102: `= reqId` | L303: `msg.h.reqId`→`processOuterRequest` | Yes |
| `seqNum` | UNCHECKED | L103: `= _nextSeq++` | not read | N/A (debug only) |
| `enqueueTick` | LOCAL-ONLY | L104: `= curTick()` | not read | N/A |
| `readyTick` | LOCAL-ONLY | L105: `= curTick()` | not read | N/A |
| **Body** | | | | |
| `b.readReq.neededPerm` | PRESERVED | L107: `= (reqType==0) ? 0 : 1` | L287: `msg.b.readReq.neededPerm == 1` check | Yes |

---

## WritebackReq → WritebackResp (UBAdapter line 190→UBRouter line 361→UBAdapter line 739)

| Field | Category | Send-Side Line | Recv-Side Line | Preserved? |
|---|---|---|---|---|
| `type` | LOCAL-ONLY | L191: `= WritebackReq` | L361: switch | N/A |
| `srcNode` | PRESERVED | L192: `= _nodeId` | L371: `resp.dstNode = msg.h.srcNode` | Yes (as dst) |
| `srcSocket` | PRESERVED | L193: `= _socketId` | L372: `resp.dstSocket = msg.h.srcSocket` | Yes (as dst) |
| `dstNode` | LOCAL-ONLY | L194: `= homeNode` | routing only | Not reconstructed |
| `dstSocket` | LOCAL-ONLY | L195: `= homeSocket` | routing only | Not reconstructed |
| `homeNode` | UNCHECKED | L196: `= homeNode` | not read | N/A |
| `homeSocket` | UNCHECKED | L197: `= homeSocket` | not read | N/A |
| `ingressSocket` | LOCAL-ONLY | L198: `= _socketId` | not read | N/A |
| `requesterNode` | PRESERVED | L199: `= requesterNode` | L365: `msg.h.requesterNode`→`processWriteback` | Yes |
| `targetNode` | UNCHECKED | not set | not read | N/A |
| `flags` | PRESERVED | L205-206: `|= UB_FLAG_KEEP_AS_CLEAN` | L362-363: `msg.h.flags & UB_FLAG_KEEP_AS_CLEAN` | Yes |
| `homeLinePa` | PRESERVED | L200: `= homePa` | L365: `msg.h.homeLinePa`→`processWriteback` | Yes |
| `localLinePa` | UNCHECKED | not set | not read | N/A |
| `epoch` | PRESERVED | L201: `= epochVal` | L366: `msg.h.epoch`→`processWriteback` | Yes |
| `reqId` | UNCHECKED | not set | not read | N/A |
| `seqNum` | UNCHECKED | L202: `= _nextSeq++` | not read | N/A |
| `enqueueTick` | LOCAL-ONLY | L203: `= curTick()` | not read | N/A |
| `readyTick` | LOCAL-ONLY | L204: `= curTick()` | not read | N/A |
| **Body** | (empty body struct) | | | N/A |

---

## EvictReq → EvictResp (UBAdapter line 244→UBRouter line 380→UBAdapter line 740)

| Field | Category | Send-Side Line | Recv-Side Line | Preserved? |
|---|---|---|---|---|
| `srcNode` | PRESERVED | L246: `= _nodeId` | L387: `resp.dstNode = msg.h.srcNode` | Yes (as dst) |
| `srcSocket` | PRESERVED | L247: `= _socketId` | L388: `resp.dstSocket = msg.h.srcSocket` | Yes (as dst) |
| `dstNode` | LOCAL-ONLY | L248: `= homeNode` | routing only | Not reconstructed |
| `dstSocket` | LOCAL-ONLY | L249: `= homeSocket` | routing only | Not reconstructed |
| `homeNode` | UNCHECKED | L250: `= homeNode` | not read | N/A |
| `homeSocket` | UNCHECKED | L251: `= homeSocket` | not read | N/A |
| `ingressSocket` | LOCAL-ONLY | L252: `= _socketId` | not read | N/A |
| `requesterNode` | PRESERVED | L253: `= evictingNode` | L382: `msg.h.requesterNode`→`processEvict` | Yes |
| `homeLinePa` | PRESERVED | L254: `= homePa` | L382: `msg.h.homeLinePa`→`processEvict` | Yes |
| `epoch` | PRESERVED | L255: `= epochVal` | L382: `msg.h.epoch`→`processEvict` | Yes |
| `seqNum` | UNCHECKED | L256: `= _nextSeq++` | not read | N/A |
| `enqueueTick` | LOCAL-ONLY | L257: `= curTick()` | not read | N/A |
| `readyTick` | LOCAL-ONLY | L258: `= curTick()` | not read | N/A |
| `reqId` | UNCHECKED | not set | L392: `msg.h.reqId` NOT READ (← gap: response carries 0) | **No** — reqId not sent |

---

## UpgradeReq → UpgradeResp (UBAdapter line 300→UBRouter line 397→UBAdapter line 741)

| Field | Category | Send-Side Line | Recv-Side Line | Preserved? |
|---|---|---|---|---|
| `srcNode` | PRESERVED | L302: `= _nodeId` | L414: `resp.dstNode = msg.h.srcNode` | Yes (as dst) |
| `srcSocket` | PRESERVED | L303: `= _socketId` | L415: `resp.dstSocket = msg.h.srcSocket` | Yes (as dst) |
| `dstNode` | LOCAL-ONLY | L304: `= homeNode` | routing only | Not reconstructed |
| `dstSocket` | LOCAL-ONLY | L305: `= homeSocket` | routing only | Not reconstructed |
| `homeNode` | UNCHECKED | L306: `= homeNode` | not read | N/A |
| `homeSocket` | UNCHECKED | L307: `= homeSocket` | not read | N/A |
| `ingressSocket` | LOCAL-ONLY | L308: `= _socketId` | not read | N/A |
| `requesterNode` | PRESERVED | L309: `= requesterNode` | L404: `msg.h.requesterNode`→`processOuterUpgradeReq` | Yes |
| `homeLinePa` | PRESERVED | L310: `= homePa` | L404: `msg.h.homeLinePa`→`processOuterUpgradeReq` | Yes |
| `epoch` | PRESERVED | L311: `= epoch` | L405: `msg.h.epoch`→`processOuterUpgradeReq` | Yes |
| `reqId` | PRESERVED | L312: `= reqId` | L405: `msg.h.reqId`→`processOuterUpgradeReq` | Yes |
| `seqNum` | UNCHECKED | L313: `= _nextSeq++` | not read | N/A |
| `enqueueTick` | LOCAL-ONLY | L314: `= curTick()` | not read | N/A |
| `readyTick` | LOCAL-ONLY | L315: `= curTick()` | not read | N/A |
| `flags` | PARTIAL | not set on send | L419: reused for `UB_FLAG_ACCEPTED` in resp | No — send-side zero, resp sets accepted |
| **Body** | | | | |
| `b.upgradeReq.desiredPerm` | PRESERVED | L317: `= desiredPerm` | L406: `msg.b.upgradeReq.desiredPerm` | Yes |
| `b.upgradeReq.cause` | PRESERVED | L318: `= cause` | L399: `msg.b.upgradeReq.cause` | Yes |

---

## UpgradeDoneReq → UpgradeDoneResp (UBAdapter line 367→UBRouter line 427→UBAdapter line 742)

| Field | Category | Send-Side Line | Recv-Side Line | Preserved? |
|---|---|---|---|---|
| `srcNode` | PRESERVED | L369: `= _nodeId` | L434: `resp.dstNode = msg.h.srcNode` | Yes (as dst) |
| `srcSocket` | PRESERVED | L370: `= _socketId` | L435: `resp.dstSocket = msg.h.srcSocket` | Yes (as dst) |
| `dstNode` | LOCAL-ONLY | L371: `= homeNode` | routing only | Not reconstructed |
| `dstSocket` | LOCAL-ONLY | L372: `= homeSocket` | routing only | Not reconstructed |
| `homeNode` | UNCHECKED | L373: `= homeNode` | not read | N/A |
| `homeSocket` | UNCHECKED | L374: `= homeSocket` | not read | N/A |
| `ingressSocket` | LOCAL-ONLY | L375: `= _socketId` | not read | N/A |
| `requesterNode` | PRESERVED | L376: `= requesterNode` | L429: `msg.h.requesterNode`→`processOuterUpgradeDone` | Yes |
| `homeLinePa` | PRESERVED | L377: `= homePa` | L429: `msg.h.homeLinePa`→`processOuterUpgradeDone` | Yes |
| `epoch` | PRESERVED | L378: `= epoch` | L430: `msg.h.epoch`→`processOuterUpgradeDone` | Yes |
| `reqId` | PRESERVED | L379: `= reqId` | L430: `msg.h.reqId`→`processOuterUpgradeDone` | Yes |
| `seqNum` | UNCHECKED | L380: `= _nextSeq++` | not read | N/A |
| `enqueueTick` | LOCAL-ONLY | L381: `= curTick()` | not read | N/A |
| `readyTick` | LOCAL-ONLY | L382: `= curTick()` | not read | N/A |
| **Body** | (empty body struct) | | | N/A |

---

## ClearReq → ClearResp (UBAdapter line 421→UBRouter line 444→UBAdapter line 743)

| Field | Category | Send-Side Line | Recv-Side Line | Preserved? |
|---|---|---|---|---|
| `srcNode` | PRESERVED | L423: `= _nodeId` | L452: `resp.dstNode = msg.h.srcNode` | Yes (as dst) |
| `srcSocket` | PRESERVED | L424: `= _socketId` | L453: `resp.dstSocket = msg.h.srcSocket` | Yes (as dst) |
| `dstNode` | LOCAL-ONLY | L425: `= homeNode` | routing only | Not reconstructed |
| `dstSocket` | LOCAL-ONLY | L426: `= homeSocket` | routing only | Not reconstructed |
| `homeNode` | UNCHECKED | L427: `= homeNode` | not read | N/A |
| `homeSocket` | UNCHECKED | L428: `= homeSocket` | not read | N/A |
| `ingressSocket` | LOCAL-ONLY | L429: `= _socketId` | not read | N/A |
| `requesterNode` | PRESERVED | L430: `= srcNode` | L446: `msg.h.requesterNode`→`processClear` | Yes |
| `homeLinePa` | PRESERVED | L431: `= linePa` | L446: `msg.h.homeLinePa`→`processClear` | Yes |
| `epoch` | PRESERVED | L432: `= epoch` | L447: `msg.h.epoch`→`processClear` | Yes |
| `reqId` | PRESERVED | L433: `= reqId` | L447: `msg.h.reqId`→`processClear` | Yes |
| `seqNum` | UNCHECKED | L434: `= _nextSeq++` | not read | N/A |
| `enqueueTick` | LOCAL-ONLY | L435: `= curTick()` | not read | N/A |
| `readyTick` | LOCAL-ONLY | L436: `= curTick()` | not read | N/A |
| **Body** | | | | |
| `b.clearReq.reason` | UNCHECKED | L438: `= 0` (GrantHandshake) | not read | N/A |

---

## RecallResp (Fire-and-Forget: UBAdapter line 479→UBRouter line 461)

| Field | Category | Send-Side Line | Recv-Side Line | Preserved? |
|---|---|---|---|---|
| `srcNode` | PRESERVED | L481: `= _nodeId` | routing only (dst forwarded) | Used by router |
| `requesterNode` | PRESERVED | L488: `= ownerNode` | L474-475: `msg.h.requesterNode`→`processRecallResponse` | Yes |
| `homeLinePa` | PRESERVED | L489: `= linePa` | L474: `msg.h.homeLinePa`→`processRecallResponse` | Yes |
| `epoch` | PRESERVED | L490: `= epoch` | L476: `msg.h.epoch`→`processRecallResponse` | Yes |
| `reqId` | PRESERVED | L491: `= reqId` | L476: `msg.h.reqId`→`processRecallResponse` | Yes |
| `flags` (UB_FLAG_DATA_RETURNED) | PRESERVED | L497: `|= UB_FLAG_DATA_RETURNED` | L462-463: `msg.h.flags & UB_FLAG_DATA_RETURNED` | Yes |
| `flags` (UB_FLAG_HAS_DATA) | PRESERVED | L499: `|= UB_FLAG_HAS_DATA` | L464-465: `msg.h.flags & UB_FLAG_HAS_DATA` | Yes |
| `b.recallResp.data` | PRESERVED | L500: `memcpy` from dataBlk | L470: `msg.b.recallResp.data`→`dataBlk.setData` | Yes |
| `seqNum` | UNCHECKED | L492: `= _nextSeq++` | not read | N/A |
| `enqueueTick` | LOCAL-ONLY | L493: `= curTick()` | not read | N/A |
| `readyTick` | LOCAL-ONLY | L494: `= curTick()` | not read | N/A |

---

## InvalidateAck (Fire-and-Forget: UBAdapter line 526→UBRouter line 482)

| Field | Category | Send-Side Line | Recv-Side Line | Preserved? |
|---|---|---|---|---|
| `requesterNode` | PRESERVED | L535: `= ackNode` | L483-484: `msg.h.requesterNode`→`processInvalidationAck` | Yes |
| `homeLinePa` | PRESERVED | L536: `= linePa` | L483: `msg.h.homeLinePa`→`processInvalidationAck` | Yes |
| `epoch` | PRESERVED | L537: `= epoch` | L485: `msg.h.epoch`→`processInvalidationAck` | Yes |
| `reqId` | PRESERVED | L538: `= reqId` | L485: `msg.h.reqId`→`processInvalidationAck` | Yes |
| `seqNum` | UNCHECKED | L539: `= _nextSeq++` | not read | N/A |
| `enqueueTick` | LOCAL-ONLY | L540: `= curTick()` | not read | N/A |
| `readyTick` | LOCAL-ONLY | L541: `= curTick()` | not read | N/A |

---

## RecallReq (Home→Sharer Fire-and-Forget: UBAdapter line 564→UBAdapter line 762)

| Field | Category | Send-Side Line | Recv-Side Line | Preserved? |
|---|---|---|---|---|
| `type` | LOCAL-ONLY | L565: `= RecallReq` | L762: switch | N/A |
| `srcNode` | PRESERVED | L566: `= _nodeId` | (not read by recv) | N/A (routing field) |
| `srcSocket` | LOCAL-ONLY | L567: `= homeSocket` | (not read) | N/A |
| `dstNode` | LOCAL-ONLY | L568: `= targetNode` | routing only | Not reconstructed |
| `dstSocket` | LOCAL-ONLY | L569: `= homeSocket` | routing only | Not reconstructed |
| `homeNode` | PRESERVED | L570: `= recallMsg.homeNode` | L768: `recallMsg.homeNode = msg.h.homeNode` | **Yes** |
| `homeSocket` | LOCAL-ONLY | L571: `= homeSocket` | not read | N/A |
| `ingressSocket` | LOCAL-ONLY | L572: `= homeSocket` | not read | N/A |
| `requesterNode` | PRESERVED | L573: `= _nodeId` | (not read by recv for recallMsg) | N/A |
| `targetNode` | PRESERVED | L574: `= targetNode` | L767: `recallMsg.ownerNode = msg.h.targetNode` | **Yes** |
| `homeLinePa` | PRESERVED | L575: `= recallMsg.linePa` | L765: `recallMsg.linePa = msg.h.homeLinePa` | **Yes** |
| `localLinePa` | PRESERVED | L576: `= recallMsg.ownerLocalPa` | L766: `recallMsg.ownerLocalPa = msg.h.localLinePa` | **Yes** |
| `epoch` | PRESERVED | L577: `= recallMsg.epoch` | L769: `recallMsg.epoch = msg.h.epoch` | **Yes** |
| `reqId` | PRESERVED | L578: `= recallMsg.reqId` | L770: `recallMsg.reqId = msg.h.reqId` | **Yes** |
| `flags` (UB_FLAG_IS_READ_RECALL) | PRESERVED | L583-584: `|= UB_FLAG_IS_READ_RECALL` | L771-772: `msg.h.flags & UB_FLAG_IS_READ_RECALL` | **Yes** |
| `flags` (UB_FLAG_HAS_DATA) | PRESERVED | L585-586: `|= UB_FLAG_HAS_DATA` | L773-774: `msg.h.flags & UB_FLAG_HAS_DATA` | **Yes** |
| `seqNum` | UNCHECKED | L579: `= _nextSeq++` | not read | N/A |
| `enqueueTick` | LOCAL-ONLY | L580: `= curTick()` | not read | N/A |
| `readyTick` | LOCAL-ONLY | L581: `= curTick()` | not read | N/A |
| **Body** | (empty body struct) | | | N/A |

---

## InvalidateReq (Home→Sharer Fire-and-Forget: UBAdapter line 608→UBAdapter line 785)

| Field | Category | Send-Side Line | Recv-Side Line | Preserved? |
|---|---|---|---|---|
| `type` | LOCAL-ONLY | L609: `= InvalidateReq` | L785: switch | N/A |
| `srcNode` | PRESERVED | L610: `= _nodeId` | (not read by recv) | N/A |
| `srcSocket` | LOCAL-ONLY | L611: `= homeSocket` | (not read) | N/A |
| `dstNode` | LOCAL-ONLY | L612: `= targetNode` | routing only | Not reconstructed |
| `dstSocket` | LOCAL-ONLY | L613: `= homeSocket` | routing only | Not reconstructed |
| `homeNode` | PRESERVED | L614: `= invMsg.homeNode` | L791: `invMsg.homeNode = msg.h.homeNode` | **Yes** |
| `homeSocket` | LOCAL-ONLY | L615: `= homeSocket` | not read | N/A |
| `ingressSocket` | LOCAL-ONLY | L616: `= homeSocket` | not read | N/A |
| `requesterNode` | PRESERVED | L617: `= _nodeId` | (not read by recv) | N/A |
| `targetNode` | PRESERVED | L618: `= targetNode` | L790: `invMsg.sharerNode = msg.h.targetNode` | **Yes** |
| `homeLinePa` | PRESERVED | L619: `= invMsg.linePa` | L788: `invMsg.linePa = msg.h.homeLinePa` | **Yes** |
| `localLinePa` | PRESERVED | L620: `= invMsg.sharerLocalPa` | L789: `invMsg.sharerLocalPa = msg.h.localLinePa` | **Yes** |
| `epoch` | PRESERVED | L621: `= invMsg.epoch` | L792: `invMsg.epoch = msg.h.epoch` | **Yes** |
| `reqId` | PRESERVED | L622: `= invMsg.reqId` | L793: `invMsg.reqId = msg.h.reqId` | **Yes** |
| `seqNum` | UNCHECKED | L623: `= _nextSeq++` | not read | N/A |
| `enqueueTick` | LOCAL-ONLY | L624: `= curTick()` | not read | N/A |
| `readyTick` | LOCAL-ONLY | L625: `= curTick()` | not read | N/A |
| **Body** | (empty body struct) | | | N/A |

---

## QueryLineMetaReq → QueryLineMetaResp (UBAdapter line 648→UBRouter line 490→UBAdapter line 744)

| Field | Category | Send-Side Line | Recv-Side Line | Preserved? |
|---|---|---|---|---|
| `srcNode` | PRESERVED | L650: `= _nodeId` | L502: `resp.dstNode = msg.h.srcNode` | Yes (as dst) |
| `srcSocket` | PRESERVED | L651: `= _socketId` | L503: `resp.dstSocket = msg.h.srcSocket` | Yes (as dst) |
| `dstNode` | LOCAL-ONLY | L652: `= homeNode` | routing only | Not reconstructed |
| `dstSocket` | LOCAL-ONLY | L653: `= homeSocket` | routing only | Not reconstructed |
| `homeNode` | UNCHECKED | L654: `= homeNode` | not read | N/A |
| `homeSocket` | UNCHECKED | L655: `= homeSocket` | not read | N/A |
| `ingressSocket` | LOCAL-ONLY | L656: `= _socketId` | not read | N/A |
| `homeLinePa` | PRESERVED | L657: `= homePa` | L496: `msg.h.homeLinePa`→`queryLineMeta` | Yes |
| `seqNum` | UNCHECKED | L658: `= _nextSeq++` | not read | N/A |
| `enqueueTick` | LOCAL-ONLY | L659: `= curTick()` | not read | N/A |
| `readyTick` | LOCAL-ONLY | L660: `= curTick()` | not read | N/A |
| `epoch` | UNCHECKED | not set | L505: `resp.epoch = msg.h.epoch` (carries 0) | **No** — epoch not sent |
| `reqId` | UNCHECKED | not set | L506: `resp.reqId = msg.h.reqId` (carries 0) | **No** — reqId not sent |
| **Body** | (empty body struct) | | | N/A |

---

## HomeWritebackNotify (Fire-and-Forget: UBAdapter line 700→UBRouter line 513)

| Field | Category | Send-Side Line | Recv-Side Line | Preserved? |
|---|---|---|---|---|
| `srcNode` | PRESERVED | L702: `= _nodeId` | routing only | N/A |
| `homeLinePa` | PRESERVED | L709: `= homePa` | L515-516: `msg.h.homeLinePa`→`processHomeWritebackNotify` | **Yes** |
| `epoch` | PRESERVED | L710: `= epoch` | L515-516: `msg.h.epoch`→`processHomeWritebackNotify` | **Yes** |
| `seqNum` | UNCHECKED | L711: `= _nextSeq++` | not read | N/A |
| `enqueueTick` | LOCAL-ONLY | L712: `= curTick()` | not read | N/A |
| `readyTick` | LOCAL-ONLY | L713: `= curTick()` | not read | N/A |
| **Body** | (empty body struct) | | | N/A |

---

## Semantic Bleed Analysis

### 1. LOCAL-ONLY fields leaking into protocol decisions: **None detected**
All fields used in protocol decisions (epoch, reqId, homeLinePa, requesterNode, flags, body fields) are properly PRESERVED and explicitly extracted on the receive side. No LOCAL-ONLY field (enqueueTick, readyTick, seqNum) is consulted in any protocol branch in `deliverToUbcc()` or `recvFromRouter()`.

### 2. UNCHECKED fields (sent but never read):
| Field | Used By | Risk |
|---|---|---|
| `homeNode` (request types) | UBRouter.cc:285-523 | Never read in `deliverToUbcc` — informational only |
| `homeSocket` (request types) | Same | Never read |
| `targetNode` (request types sent to UBCC) | Same | Never read on the UBCC side |
| `localLinePa` (request types) | Same | Only read for RecallReq/InvalidateReq (meaningfully) |
| `seqNum` (all types) | Debug-only in `ubMsgToString` | No protocol impact |
| `b.clearReq.reason` | UBRouter.cc:444-458 | Never read — ClearReq ignores reason |

### 3. Missing fields (should be sent but aren't):
| Message Type | Missing Field | Impact |
|---|---|---|
| **EvictReq** | `reqId` | Response carries `reqId` (line 392) but it's always 0 because sender never sets it. **Low risk** — EvictResp matched via `_lastResponse` ordering, not reqId. |
| **QueryLineMetaReq** | `epoch`, `reqId` | Response echoes back `msg.h.epoch` and `msg.h.reqId` (lines 505-506) but sender never populated them (both are 0). **Low risk** — resp only used synchronously via `_lastResponse`. |

### 4. Flags integrity
| Flag | Where Set | Where Read | Bleed Risk |
|---|---|---|---|
| `UB_FLAG_WRITE_INTENT` (0x1) | ReadReq send L98 | ReadReq recv L287 | None — consumed in same message |
| `UB_FLAG_KEEP_AS_CLEAN` (0x2) | WritebackReq send L206 | WritebackReq recv L362-363 | None — consumed in same message |
| `UB_FLAG_ACCEPTED` (0x4) | UpgradeResp/U.ClearResp build | Adapter recv (via `_lastResponse`) | None — response-only flag |
| `UB_FLAG_DATA_RETURNED` (0x8) | RecallResp send L497 | RecallResp recv L462-463 | None — consumed in same message |
| `UB_FLAG_HAS_DATA` (0x10) | RecallResp L499, RecallReq L586 | RecallResp L464, RecallReq L773 | **Cross-type concern**: `UB_FLAG_HAS_DATA` is set on RecallReq send (L586) and checked on RecallReq recv (L773). Same flag also used on ReadResp (L336) and checked on ReadResp recv (not shown, but stored). These are different message types so no collision. |
| `UB_FLAG_IS_READ_RECALL` (0x20) | RecallReq send L584 | RecallReq recv L771-772 | None — consumed in same message |
| `UB_FLAG_BUSY` (0x40) | Defined | **Never set or read in any traced path** | Dead code? |

### 5. Receive-side field extraction completeness
| Recv Path | Fields Extracted | Missing Reconstructions |
|---|---|---|
| `recvFromRouter()` for **RecallReq** | homeLinePa, localLinePa, targetNode, homeNode, epoch, reqId, flags (2) | `srcNode` (home identity) not stored in OuterRecallMsg — **cosmetic** |
| `recvFromRouter()` for **InvalidateReq** | homeLinePa, localLinePa, targetNode, homeNode, epoch, reqId | `srcNode` not stored in OuterInvalidateMsg — **cosmetic** |
| `deliverToUbcc()` for any request | homeLinePa, requesterNode, epoch, reqId, flags | All semantic fields correctly extracted |

---

## Per-Message-Type Preservation Summary

| Msg Type Pair | Preserved Fields | LOCAL-ONLY | UNCHECKED | Missing |
|---|---|---|---|---|
| ReadReq→ReadResp | 11 | 4 | 5 | 0 |
| WritebackReq→WritebackResp | 7 | 5 | 6 | 0 |
| EvictReq→EvictResp | 6 | 5 | 6 | **1 (reqId)** |
| UpgradeReq→UpgradeResp | 11 | 4 | 5 | 0 |
| UpgradeDoneReq→UpgradeDoneResp | 7 | 5 | 6 | 0 |
| ClearReq→ClearResp | 7 | 5 | 7 | 0 |
| RecallResp (F&F) | 8 | 3 | 2 | 0 |
| InvalidateAck (F&F) | 4 | 3 | 2 | 0 |
| RecallReq (F&F) | 9 | 7 | 2 | 0 |
| InvalidateReq (F&F) | 9 | 7 | 2 | 0 |
| QueryLineMetaReq→MetaResp | 4 | 5 | 7 | **2 (epoch, reqId)** |
| HomeWritebackNotify (F&F) | 3 | 3 | 2 | 0 |

**Conclusion**: FV-10 PASS with 2 minor findings (EvictReq missing reqId, QueryLineMetaReq missing epoch/reqId — both non-critical since they use synchronous `_lastResponse` matching). No semantic bleed of local-only fields into protocol decisions.
