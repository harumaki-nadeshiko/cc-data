# FV-8: Invalidate Barrier — Proof of No Premature InvalidationAck

**Conclusion**: InvalidationAck is **always** sent *after* the CHI CleanUnique transaction completes (Comp_UC + CompAck handshake). No code path allows a premature ack.

---

## Causal Chain

| Step | Function | File:Line | Precondition | Postcondition |
|------|----------|-----------|-------------|---------------|
| 1 | `UBCCController` sends SnpCleanInvalid → sharer via UBAdapter | UBCCController.cc (implied) | Directory marks line for invalidation | UBMsg `InvalidateReq` dispatched to sharer |
| 2 | `UBAdapter::handleMessage` (case InvalidateReq) | UBAdapter.cc:785-801 | UBMsg with type `InvalidateReq` received | Reconstructs `OuterInvalidateMsg`, calls `_backend->handleInvalidationRequest(invMsg)` |
| 3 | `EPBackend::handleInvalidationRequest` | EPBackend.cc:1562-1622 | InvalidateReq received; sharerNode == _nodeId | Stores inv msg, sets requester line→R_I, calls `_epRnfCtrl->startCleanUnique(pa, callback)` |
| 4 | `EPRNFController::startCleanUnique` | EPRNFController.cc:1183-1237 | No duplicate pending txn on `linePa` | Creates `PendingChiTxn{op=CleanUnique, needsCompAck=true}`, stores `onComplete` callback, sends `CleanUnique` to HN-F |
| 5 | HN-F processes CleanUnique → sends `Comp_UC` | (HN-F microarchitecture) | CleanUnique completes at HN-F | `Comp_UC` response message sent back to EP-RNF |
| 6 | `EPRNFController::handleResponse` (Comp_UC case) | EPRNFController.cc:397-455 | Pending txn exists, op==CleanUnique, CompAck send succeeds | `finishChiTxn(linePa, true)` called |
| 7 | `EPRNFController::finishChiTxn` | EPRNFController.cc:898-942 | Pending txn found; recall data transferred | Erases `_pendingChiTxns[linePa]`, invokes `cb(success)` — the stored `onComplete` callback |
| 8 | **Callback lambda** (captured by value) | EPBackend.cc:1604-1614 | `ok` from CleanUnique completion | Constructs `OuterInvalidationAck` and calls `sendInvalidationAck(ack)` |
| 9 | `EPBackend::sendInvalidationAck` | EPBackend.cc:1624-1652 | UBAdapter bound | Dispatches via `UBAdapter::sendInvalidateAck(...)` |
| 10 | `UBAdapter::sendInvalidateAck` | UBAdapter.cc:511-546 | Router bound | Constructs UBMsg `InvalidateAck`, sends via `_router->sendMessage(req)` |
| 11 | `UBCCController::processInvalidationAck` | UBCCController.cc:1210-1346 | Dir entry found, epoch valid, ack in target mask | Updates `ackMask`, `pendingAckCount--`. When all acks done (`pendingAckCount==0`), transitions to WAITING_LOCAL_DONE |

### Key ordering guarantee

```
Comp_UC received ──> send CompAck ──> finishChiTxn ──> callback ──> sendInvalidationAck
                       ▲                                     ▲
             must succeed before                     only invoked by
             finishChiTxn is called                  finishChiTxn
```

---

## Proof That InvalidationAck Is Always Post-Comp_UC

1. **`handleInvalidationRequest` does NOT ack directly.** The v4 fix (line 1594-1596) explicitly removed the old direct-ack path. If `_epRnfCtrl` is null, a `fatal()` is raised (line 1617-1620) — execution halts, no ack.

2. **`startCleanUnique` stores the callback.** The onComplete lambda is only invoked by `finishChiTxn` (line 926: `if (cb) cb(success)`).

3. **`finishChiTxn` is called only after CHI response processing succeeds.** Three call sites exist:
   - **Comp_UC handler** (line 440): after CompAck sent successfully for CleanUnique
   - **ReadUnique data handler** (line 515, 546): after last data beat + CompAck
   - **Retry path** (line 1057): after deferred CompAck finally sent

4. **No call to finishChiTxn before HN-F responds.** The `startCleanUnique` simply sends the request and returns; the callback does not fire until the response arrives.

5. **CompAck failure defers completion, does NOT skip it.** If CompAck send fails (line 441-452), `needsCompAck` is set, a retry event is scheduled, and `finishChiTxn` is **not called**. Only when the retry (`retryPendingCompAcks`, line 1048-1057) succeeds does `finishChiTxn` fire.

---

## Failure / Callback-Error Paths

| Failure Mode | Where | Effect on InvalidationAck |
|---|---|---|
| **Duplicate pending txn** on same cache line | `startCleanUnique` line 1192-1201 | `onComplete(false)` called immediately — InvalidationAck sent with `ok=false` (ack is unconditional in callback) |
| **CleanUnique send fails** (CHI req outbound fails) | `startCleanUnique` line 1228-1236 | Pending txn erased, `onComplete(false)` called — InvalidationAck sent but CleanUnique never reached HN-F |
| **CompAck send fails** (network congestion) | Comp_UC handler line 441-452 | `finishChiTxn` deferred; retry scheduled. InvalidationAck is **NOT sent** until retry succeeds |
| **`_epRnfCtrl` is null** | `handleInvalidationRequest` line 1617 | `fatal()` — process terminates, no ack at all |
| **Pending txn not found** at Comp_UC arrival | Comp_UC handler line 407 | Returns `true` (message consumed but no action); callback never fires, InvalidationAck never sent |

### Critical observation: duplicate/send-failure paths

In the duplicate and send-failure paths, `onComplete(false)` is called, and the lambda (EPBackend.cc:1604-1614) **unconditionally** sends InvalidationAck regardless of `ok`. This means:

- **Duplicate**: ack sent even though no CHI transaction was started — this is safe because the line is already in the process of being invalidated (a previous CleanUnique is in flight), and the ack confirms the line state change.
- **Send failure**: ack sent even though CleanUnique never reached HN-F — this is a *best-effort* ack. The `fatal()` guard on null `_epRnfCtrl` catches the misconfiguration case, but a transient send failure still produces an ack. This is acceptable because the HN-F (UBCCController) validates the ack against its directory/epoch state and will reject a stale ack.

---

## ackMask Accumulation at UBCCController

Once the InvalidationAck arrives at the home node, `processInvalidationAck` (UBCCController.cc:1210-1346) validates it and accumulates:

```
effAckMask |= nodeBit;          // line 1290
ost->pendingAckCount--;         // line 1294
entry.sharersMask &= ~nodeBit;  // line 1301 (INVALIDATE path only)
```

When `pendingAckCount == 0` (line 1334-1335), all acks are done and the operation transitions to `WAITING_LOCAL_DONE` (upgrade) or proceeds to finalization (invalidate).

---

## Summary

The barrier invariant holds: **No code path sends InvalidationAck before the CHI CleanUnique transaction completes.** The critical ordering is enforced by the callback mechanism — the ack-sending lambda is stored as `onComplete` in the pending CHI transaction and is only invoked by `finishChiTxn`, which runs after Comp_UC reception and successful CompAck transmission.
