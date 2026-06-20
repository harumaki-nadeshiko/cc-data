# FV-8: Invalidate Barrier Verification

**Invariant**: `InvalidationAck` is only transmitted *after* the `startCleanUnique` CHI transaction (CleanUnique → Comp_UC → CompAck) fully completes — the ack must never be sent while the HN-F still holds an open transaction for this line.

---

## 1. Full Callback Chain

| Step | Function | File:Line | Role |
|------|----------|-----------|------|
| ① | `handleInvalidationRequest` | `EPBackend.cc:1530-1599` | Receives outer invalidation, calls `startCleanUnique` with a lambda callback |
| ② | `startCleanUnique` | `EPRNFController.cc:1183-1234` | Inserts `PendingChiTxn{op=CleanUnique, needsCompAck=true}`, sends `CleanUnique` + `InvalidateOnly` proxy-op to HN-F |
| ③ | Comp_UC received | `EPRNFController.cc:401-454` | HN-F replies with `Comp_UC`. Controller sends `CompAck` then calls `finishChiTxn(linePa, true)` |
| ④ | `finishChiTxn` | `EPRNFController.cc:900-943` | Erases pending txn, invokes `txn.onComplete(success)` |
| ⑤ | **Callback lambda** | `EPBackend.cc:1572-1583` | Builds `OuterInvalidationAck` and calls `sendInvalidationAck(ack)` |
| ⑥ | `sendInvalidationAck` | `EPBackend.cc:1603-1630` | Dispatches via `UBAdapter::sendInvalidateAck` to home UBCC |
| ⑦ | `processInvalidationAck` | `UBCCController.cc:1196-1306` | Home directory records the ack, clears sharer bit, updates state |

**Key sequencing in `finishChiTxn`** (line 900-928):

```
PendingChiTxn erased  ──→  onComplete callback invoked  ──→  ack sent
```

The callback is *never* invoked while the `PendingChiTxn` still exists in the map; the transaction is erased at line 923 *before* the callback runs at line 927. This guarantees the CHI transaction is fully retired before the ack is constructed.

---

## 2. Proof: No Early-Ack Codepath

Every code path through `handleInvalidationRequest` is enumerated below:

### 2a. Normal path (production) — SAFE

```
handleInvalidationRequest (line 1565-1584)
  └── _epRnfCtrl->startCleanUnique(pa, callback)   ← CHI txn starts
        └── sendChiRequest(CleanUnique) → HN-F
        └── (returns, pending txn in map)
              ... wait for Comp_UC ...
              finishChiTxn(pa, true)                 ← CHI txn ends
                └── _pendingChiTxns.erase(it)        ← txn removed
                └── callback(true)                   ← ack sent here
```

**Ack is sent strictly after `finishChiTxn` erases the pending transaction.** No window exists where the HN-F sees an ack while holding an open CleanUnique reservation.

### 2b. Duplicate-pending failure — SAFE (no txn started)

```
startCleanUnique (line 1191-1200)
  └── duplicate PA found in _pendingChiTxns
  └── onComplete(false) invoked SYNCHRONOUSLY   ← ack sent, but...
  └── return (without ever calling sendChiRequest)
```

No CHI transaction was started, so there is no barrier to violate. The ack carries `success=false`, informing the HN-F that invalidation failed.

### 2c. Send-failure — SAFE (no txn started)

```
startCleanUnique (line 1226-1233)
  └── sendChiRequest returned false
  └── _pendingChiTxns.erase(linePa)
  └── onComplete(false) invoked SYNCHRONOUSLY   ← ack sent
```

Same reasoning as 2b — no CHI request was issued, no HN-F transaction exists.

### 2d. Fallback: no EPRNFController — *GAP* (prototype/debug only)

```
handleInvalidationRequest (line 1585-1598)
  └── _epRnfCtrl is nullptr
  └── sendInvalidationAck(ack) DIRECTLY         ← ack sent, NO CleanUnique!
```

This path emits `warn("...no EP-RNF controller, sending invalidation ack directly (bypasses HN-F)...")`. It exists only when the `EPRNFController` was never instantiated (prototype/diagnostic build). In production, `EPRNFController::init()` calls `_backend->setEpRnfController(this)` at line 238, making this path dead code.

---

## 3. Barrier Integrity Summary

| Condition | Ack sent? | CHI txn ordered before ack? | Violation? |
|-----------|-----------|-----------------------------|------------|
| Normal Comp_UC receipt | Yes | Yes (finishChiTxn → callback) | ❌ No |
| Duplicate pending txn | Yes (failure) | N/A — no txn started | ❌ No |
| Send failure | Yes (failure) | N/A — no txn started | ❌ No |
| No EPRNFController | Yes (direct) | No | ⚠️ **Yes** (prototype only, warns) |

**The barrier is intact in all production paths.** The prototype fallback (2d) is the sole gap, gated by a `warn()` and unreachable in configurations where `EPRNFController` is initialized.

---

## 4. Additional Observations

- **`needsCompAck = true`** (line 1213): The `PendingChiTxn` for CleanUnique requires sending `CompAck` before `finishChiTxn`. This unblocks the HN-F `WaitCompAck` state. The `CompAck` send must succeed — if it fails (line 441-451), the controller retries via `scheduleEvent(Cycles(1))` and does **not** invoke the callback until CompAck is sent. This prevents the ack from racing ahead of the HN-F state machine.

- **`_chiRequestInFlight` guard** (line 954-965): `sendChiRequest` defers the request if another CHI request is in flight. The callback can only fire after the deferred request eventually completes. No early-ack window is introduced by deferral.

- **`processRetryQueue` / `processDeferredChiReqs`** (lines 939-942): Called *after* the callback in `finishChiTxn`. The ack is already sent by this point, so these cannot interfere.

- **`processInvalidationAck` idempotency** (UBCC line 1230-1237): If the ack arrives after the outstanding request has already been cleaned up, it is treated as idempotent. This ensures that even in edge cases, duplicate or delayed acks are harmless.
