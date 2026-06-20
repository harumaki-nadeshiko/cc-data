# FV8: InvalidateAck Only After CleanUnique Callback

**Claim:** `InvalidateAck` is never sent directly from `handleInvalidationRequest`.
It is deferred until the `CleanUnique` → `Comp_UC` → `CompAck` → `finishChiTxn` chain completes,
ensuring HN-F `WaitCompAck` is unblocked before the invalidation ack is issued.

## Call Chain

| Step | File | Lines | Event |
|------|------|-------|-------|
| 1 | `EPBackend.cc` | 1562–1575 | `handleInvalidationRequest` calls `startCleanUnique(pa, [lambda])` — the lambda **only** calls `sendInvalidationAck`. No other InvalidateAck path exists. |
| 2 | `EPRNFController.cc` | 1183–1235 | `startCleanUnique` stores the lambda in `txn.onComplete` (line 1220), then sends `CleanUnique` to HN-F. On send failure, `onComplete(false)` is called directly (line 1235) — still through the callback. |
| 3 | `EPRNFController.cc` | 397–455 | `Comp_UC` arrives from HN-F. Code sends `CompAck` (line 414), then calls `finishChiTxn(msg->m_addr, true)` (line 440). |
| 4 | `EPRNFController.cc` | 899–940 | `finishChiTxn` retrieves `cb = txn.onComplete` (line 908), erases the pending entry (line 923), invokes `cb(success)` (line 927). |

## Proof — No Direct-Ack Path

```
EPBackend.cc:1562-1575 (handleInvalidationRequest)
  └─ _epRnfCtrl->startCleanUnique(pa, [this, capturedMsg](bool ok) {
  ┊    └─ sendInvalidationAck(ack);          // <── InvalidateAck sent HERE
  ┊   });
  └─ return true;                            // returns immediately, no ack sent inline
```

- `startCleanUnique` stores the lambda in `_pendingChiTxns[pa].onComplete` (EPRNFController.cc:1220).
- The lambda is only reachable via `finishChiTxn` (EPRNFController.cc:927), which is only
  triggered after `Comp_UC` arrives (step 3) and `CompAck` is sent (step 3, line 414).

## Barrier Guarantee

```
HN-F                         EP-RNF                     EPBackend
 │                            │                            │
 ├── OuterInvalidateMsg ──────┤                            │
 │                            │                            │
 │                            ├── startCleanUnique ────────┤
 │                            │    (stores onComplete)     │
 │                            │                            │
 ├── CleanUnique ─────────────┤                            │
 │                            │                            │
 ├── Comp_UC ─────────────────┤                            │
 │                            │                            │
 │    [HN-F enters WaitCompAck]                             │
 │                            │                            │
 │                            ├── CompAck ─────────────────┤
 │                            │    (unblocks HN-F)         │
 │                            │                            │
 │                            ├── finishChiTxn()           │
 │                            │    └─ onComplete()         │
 │                            │         └─ sendInvalidationAck ──► HN-F
```

The `InvalidateAck` is **barriered** behind `CompAck`. HN-F cannot receive `InvalidateAck`
until it has exited `WaitCompAck`, preserving CHI's grant/invalidation serialization
(§4.2.4 fix, v4+).
