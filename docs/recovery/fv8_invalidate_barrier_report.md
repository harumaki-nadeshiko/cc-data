# FV-8: Invalidate Barrier Verification — InvalidateAck Sent Only After CleanUnique Completion

**Summary:** The pipeline guarantees InvalidateAck is emitted only after CleanUnique's CHI transaction fully completes (Comp_UC → CompAck → callback). Two gaps found: (1) a null-EP-RNF fallback bypasses the barrier entirely; (2) the `success` field is silently dropped on the wire.

---

## Verified Call Chain

| Step | File | Lines | Action |
|------|------|-------|--------|
| 1 | `UBAdapter.cc` | 785–801 | `InvalidateReq` received; calls `EPBackend::handleInvalidationRequest` |
| 2 | `EPBackend.cc` | 1530–1583 | Captures invMsg in lambda; calls `EPRNFController::startCleanUnique(pa, callback)` |
| 3 | `EPRNFController.cc` | 1183–1235 | Creates `PendingChiTxn` with `onComplete = callback`; sends `CleanUnique` to HN-F |
| 4 | `EPRNFController.cc` | 397–455 | HN-F responds `Comp_UC` → sends `CompAck` → calls `finishChiTxn(linePa, true)` |
| 5 | `EPRNFController.cc` | 900–928 | `finishChiTxn`: grabs callback, erases txn, **invokes** `cb(success)` |
| 6 | `EPBackend.cc` | 1572–1582 | Lambda fires → builds `OuterInvalidationAck` → calls `sendInvalidationAck` |
| 7 | `EPBackend.cc` | 1603–1630 | Delegates to `UBAdapter::sendInvalidateAck` |
| 8 | `UBAdapter.cc` | 511–546 | Encodes `UBMsg{type=InvalidateAck}` → fire-and-forget via router |
| 9 | `UBRouter.cc` | 438–444 | Routes to `UBCCController::processInvalidationAck` |
| 10 | `UBCCController.cc` | 1198–1307 | Validates epoch, records ack, updates sharer mask |

**Barrier invariant holds for the primary path:** InvalidateAck is never constructed or sent until `finishChiTxn` invokes the callback (step 5 → 6). The callback is the exclusive producer of `OuterInvalidationAck`.

---

## Gap 1: Null EP-RNF Fallback Bypass

| File | Lines | Issue |
|------|-------|-------|
| `EPBackend.cc` | 1585–1598 | If `_epRnfCtrl == nullptr`, InvalidateAck is sent **immediately without any CleanUnique/CHI transaction**. The comment says "prototype mode". |

```cpp
// Line 1585-1598:
} else {
    // Fallback: if no EP-RNF controller, ack directly (prototype mode)
    warn("...sending invalidation ack directly (bypasses HN-F)\n");
    // ... constructs OuterInvalidationAck ...
    sendInvalidationAck(ack);    // <--- NO CleanUnique barrier
}
```

**Fix required:** Either guarantee `_epRnfCtrl` is never null in production, or fatal-out instead of bypassing.

---

## Gap 2: `success` Field Silently Dropped on Wire

| Location | Lines | Observation |
|----------|-------|-------------|
| `OuterInvalidationAck` struct | `EPBackend.hh:96-106` | Has `bool success` field |
| Lambda sets it | `EPBackend.cc:1581` | `ack.success = ok;` |
| `UBAdapter::sendInvalidateAck` signature | `UBAdapter.cc:511-513` | Takes `(linePa, ackNode, epoch, reqId, homeNode, homeSocket)` — **no success parameter** |
| `UBInvalidateAckBody` | `UBMsg.hh:122` | `struct UBInvalidateAckBody { /* no extra fields */ };` — empty, no success field |
| `UBRouter.cc` dispatch | `UBRouter.cc:438-444` | Calls `processInvalidationAck(linePa, requesterNode, epoch, reqId)` — no success argument |
| `UBCCController::processInvalidationAck` | `UBCCController.cc:1199` | Signature `(uint64_t line_pa, int ackNode, uint64_t responseEpoch, uint64_t reqId)` — **no success parameter** |

**Impact:** When `startCleanUnique` fails (e.g. duplicate line or send failure at `EPRNFController.cc:1226-1234`), the callback fires with `ok=false`, and `ack.success = false` is set. This flag is *never serialized* into the UB fabric. The home UBCC unconditionally treats every InvalidateAck as success.

**Fix required:** Either (a) add `success` to `UBMsgHeader`/`UBInvalidateAckBody` and propagate it through `sendInvalidateAck` → `UBRouter` → `processInvalidationAck`, or (b) remove the unused `success` field from `OuterInvalidationAck` to avoid dead code.

---

## CompAck Retry Path (Correct)

| File | Lines | Mechanism |
|------|-------|-----------|
| `EPRNFController.cc` | 1029–1068 | `retryPendingCompAcks`: iterates txns with `needsCompAck`, retries CompAck, on success calls `finishChiTxn` → callback → InvalidateAck. |
| `EPRNFController.cc` | 440–452 | Primary path: if CompAck send fails, sets `needsCompAck=true`, schedules retry event. |

The retry path correctly defers the callback until CompAck is sent, preserving the barrier.

---

## Summary

| Path | Barrier Intact? | Notes |
|------|----------------|-------|
| Primary (Comp_UC → CompAck → callback) | ✅ Yes | Full CHI transaction completes before ack |
| CompAck retry (`needsCompAck`) | ✅ Yes | Retry loop still gates callback behind CompAck |
| CleanUnique send failure `onComplete(false)` | ⚠️ Partial | Callback fires immediately, success=false, but wire drops it |
| Null `_epRnfCtrl` fallback | ❌ No | No CleanUnique transaction at all |
