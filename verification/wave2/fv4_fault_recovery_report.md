# FV-4: Fault Model — Reorder+Dup+Loss Recovery Verification (Static Analysis)

**Objective**: Statically verify M1 transport layer tombstone replay, Clear dedup, and stale-epoch rejection wiring.
**Scope**: UBRouter, UBAdapter, UBCCController, EPBackend — message send/recv, boundary encode/decode, tombstone lifecycle, epoch-gated commit.

---

## 1. Per-Fault-Type Protection Matrix

| Fault | Message Type | Protection Mechanism | Code Path | Verdict |
|---|---|---|---|---|
| **Duplicate** | ClearReq | Tombstone (baseEpoch+reqId match key, window W expiry) | `processClear()` → `checkTombstone()` → cached `tsAccepted` | ✅ **Full** — hit returns idempotent grant; window W via `_tombstoneWindowW` + `cleanupTombstones()` |
| **Duplicate** | InvalidateAck | Epoch gate + duplicate ack bitmask (`ackMask`/`upgradeAckMask`) | `processInvalidationAck()` → `checkEpochForLine()` → bit already set → idempotent `true` | ✅ **Full** — 64-bit bitmask per outstanding; also handles missing outstanding |
| **Duplicate** | RecallResp | Epoch gate + outstanding RECALL existence + targetNode match | `processRecallResponse()` → `checkEpochForLine()` → `findOutstanding(OpType::RECALL)` → target check | ✅ **Full** — epoch stale-reject + outstanding scope guards |
| **Duplicate** | ReadReq / outer request | `dup_retry` check in `processOuterRequest()`: same (requester, reqId) already queued → BUSY | `_pendingRequesters` deque scan (lines 479-487, 790-798) | ✅ **Full** — per-PA deque with O(n) linear scan; MAX_PENDING_PER_PA cap |
| **Reorder** | Commit (any) | Half-range epoch comparison (`isNewerEpoch`) gates commit, not arrival order | `checkEpochForLine()` → `isNewerEpoch(entry.epoch, responseEpoch)` — rejects older responses | ✅ **Full** — epoch is logical clock; delta < half-range means newer; wrap-safe for `_epochBits ≤ 64` |
| **Reorder** | Requester queue | Chained replay in FIFO order via `replayPendingRequesters()` | Clear commit → `replayPendingRequesters()` processes deque front; if new outstanding created, breaks (remainder replayed later) | ✅ **Partial** — within-PA ordering preserved; cross-PA unordered (expected) |
| **Loss** | ClearReq | No automatic retry — synchronous `sendClearReq()` returns false if response never arrives; EPBackend caller ignores return value | `sendClearReq()` waits for `_lastResponseValid`; `handleRemoteMiss()` continues regardless | ❌ **Gap** — ClearReq loss leaves GRANT_HANDSHAKE pending forever; no retry timer |
| **Loss** | RecallResp | Fire-and-forget — no retry or timeout on missing response | Router marks `RecallResp` as fire-and-forget (line 197-200); UBCC `processRecallResponse()` only called on arrival | ❌ **Gap** — RECALL outstanding stuck in WAITING forever; `TIMED_OUT` stage defined but never set |
| **Loss** | InvalidateAck | Fire-and-forget — no retry or timeout on missing ack | Router marks `InvalidateAck` as fire-and-forget; UBCC `processInvalidationAck()` only called on arrival; ack bitmask tracks received acks | ❌ **Gap** — INVALIDATE outstanding stuck in WAITING_ALL_ACKS if ack lost |
| **Loss** | Outer request | `ensureResidentForAccess()` + pending queue; BUSY return triggers caller retry (EPBackend returns -1, EP-RNF retries later) | `processOuterRequest()` → BUSY returns -1; EPBackend `handleRemoteMiss()` clears `outerTxnPending` and returns -1 | ✅ **Partial** — retry by EP-RNF polling, but no bounded retry count |

---

## 2. Tombstone Lifecycle: Create → Hit → Expire → Cleanup

```
    ┌─────────────────────────────────────────────────────────────┐
    │  CREATE (retireToTombstone)                                 │
    │  Trigger: Clear accepted OR epoch-mismatch retirement       │
    │  Key:     (linePa, normalizeEpoch(baseEpoch), reqId)        │
    │  Data:    accepted=true/false, expireTick=now+W             │
    │  Store:   _tombstones[linePa].push_back()  (FIFO deque)    │
    └─────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
    ┌─────────────────────────────────────────────────────────────┐
    │  HIT (checkTombstone)                                       │
    │  1. cleanupTombstones() — purge expired entries             │
    │  2. Scan deque for (epoch == ts.epoch && reqId == ts.reqId) │
    │  3. HIT  → return ts.accepted (cached result)               │
    │  4. MISS → continue with normal processClear()              │
    │  Called from: processClear() AND processOuterRequest()      │
    └─────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
    ┌─────────────────────────────────────────────────────────────┐
    │  EXPIRE (cleanupTombstones)                                 │
    │  Check: deq.front().expireTick <= curTick()                 │
    │  Action: pop_front() — oldest entries expire first (FIFO)   │
    │  If deque empty: erase _tombstones[linePa] from map         │
    │  Called: on every checkTombstone() invocation               │
    └─────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
    ┌─────────────────────────────────────────────────────────────┐
    │  CLEANUP — map entry erased when deque empty                │
    │  After window W, tombstone is gone; a duplicate Clear with  │
    │  same (epoch, reqId) will fall through to normal validation │
    │  and be rejected (epoch mismatch vs committed entry).       │
    └─────────────────────────────────────────────────────────────┘
```

### Tombstone creation call sites

| Site | Condition | accepted |
|---|---|---|
| `processClear()` line 2123 | Clear accepted, commit succeeded | `true` |
| `processClear()` line 2063 | epoch mismatch — stale GRANT_HANDSHAKE retired | `false` |
| `processOuterRequest()` via processClear tombstone hit | N/A (read-only check) | N/A |

---

## 3. Detailed Code Path Analysis

### 3.1 Duplicate Clear (TC47)

```
EPBackend::handleRemoteMiss()
  └─ adapter->sendReadReq()         → UBCC::processOuterRequest()
  └─ sendClear()                    → UBAdapter::sendClearReq()
       └─ UBRouter::sendMessage()   ← Fault: duplicate (copies=2)
            └─ drainReadyQueues()
                 └─ deliverToUbcc( ClearReq )
                      └─ UBCC::processClear()
                           ├─ [copy 1] checkTombstone() → MISS
                           │   → validate epoch, reqId, stage, requesterNode
                           │   → commitIntendedResult()
                           │   → retireToTombstone(accepted=true)  ← CREATE
                           │   → replayPendingRequesters()
                           │   → return accepted=true (ClearResp)
                           │
                           └─ [copy 2] checkTombstone() → HIT
                               → return ts.accepted (cached true)
                               → ClearResp(accepted=true) returned
```

**Verdict**: ✅ Tombstone correctly returns idempotent grant for duplicate Clear within window W.

### 3.2 Duplicate InvalidateAck (TC48/TC49)

```
EPBackend::handleRecallRequest / handleInvalidationRequest (sharer-side)
  └─ sendInvalidateAck()            → UBAdapter::sendInvalidateAck()
       └─ UBRouter::sendMessage()   ← Fault: duplicate (copies=2)
            └─ drainReadyQueues()
                 └─ deliverToUbcc( InvalidateAck )
                      └─ UBCC::processInvalidationAck()
                           ├─ [copy 1] checkEpochForLine() → ACCEPT
                           │   → findOutstanding(INVALIDATE or UPGRADE_PENDING)
                           │   → check ackMask bit → not set → set it
                           │   → if all acks received → transition to DONE
                           │   → return true
                           │
                           └─ [copy 2] checkEpochForLine() → ACCEPT
                               → findOutstanding(INVALIDATE or UPGRADE_PENDING)
                               → check ackMask bit → ALREADY SET
                               → return true (idempotent)
```

**Verdict**: ✅ Ack bitmask correctly prevents double-counting. Missing outstanding (already completed) also returns idempotent `true`.

### 3.3 Stale Epoch Rejection (Reorder Protection)

```
processRecallResponse(epoch=responseEpoch)
  └─ checkEpochForLine(line_pa, responseEpoch)
       └─ isNewerEpoch(committedEpoch, responseEpoch)
            → delta = (committedEpoch - responseEpoch) & mask
            → if delta ∈ (0, half_range) → committed IS newer → STALE → REJECT ✅
            → else → responseEpoch >= committed → ACCEPT

processInvalidationAck(epoch=responseEpoch) — SAME check ✅
processClear(epoch=clearEpoch) — compares vs ost->baseEpoch directly ✅
processOuterRequest(epoch=baseEpoch) — tombstone check first; then allocateReservedEpoch ✅
```

**Key invariant**: All state transitions commit to epoch `reservedEpoch = committedEpoch + 1`. Any response with an epoch older than `committedEpoch` is rejected via half-range comparison.

### 3.4 Fire-and-Forget Loss (Gap Analysis)

**RecallResp** (UBRouter lines 197-200, 461-478):
```
deliverToUbcc(RecallResp) → processRecallResponse()
  → epoch check, outstanding check, target match
  → success: commit GRANT_HANDSHAKE (in RECALL→DONE→GRANT_HANDSHAKE transition)
  → failure: LOG and return false
  ← NO RESPONSE MESSAGE SENT BACK
```
If RecallResp is lost: RECALL stays in WAITING. Future requests for this PA see busy outstanding.
**No timeout.** `OpStage::TIMED_OUT` is defined (line 442, 1179) but **never set** in the code.

**InvalidateAck** (UBRouter lines 197-200, 482-488):
```
deliverToUbcc(InvalidateAck) → processInvalidationAck()
  → epoch check, outstanding check, ackMask update
  → if all acks received: transition INVALIDATE to DONE → create GRANT_HANDSHAKE
  ← NO RESPONSE MESSAGE SENT BACK
```
If one InvalidateAck is lost: INVALIDATE never completes. GRANT_HANDSHAKE never created.
**No retry mechanism.** The stuck outstanding must be manually timed out.

**ClearReq loss** (TC47 dup case is covered; true drop case):
```
adapter->sendClearReq() → router sendMessage()
  ← if dropped: no ClearResp → _lastResponseValid stays false
  → returns false
  EPBackend::sendClear() returns false (accepted=false)
  EPBackend::handleRemoteMiss() IGNORES return value → returns grant
```
The home UBCC has a GRANT_HANDSHAKE in WAITING_CLEAR that never commits. The requester has data in its cache but the home hasn't committed the new owner/sharers. A future requester will see the stuck outstanding and get BUSY.

---

## 4. Replay Path: Pending Requester Queue

```
Clear committed (or UpgradeDone)
  → replayPendingRequesters(line_pa)
       → pop front of _pendingRequesters[line_pa] deque
       → call processOuterRequest() with rebased epoch (= committed epoch)
       → if new outstanding created: break (rest of queue replayed later)
       → if no outstanding (immediate BUSY/merge): continue to next
  → replayResidentWaiters(line_pa) — writeback/evict pending
```

Chained replay ensures that queued requesters are served in order after each Clear commits.

---

## 5. Cross-Reference to Fault Injection TCs

| TC | Fault Rule | Fault Type | Message | Protection Exercised | Assertion |
|---|---|---|---|---|---|
| **TC47** | `ClearReq:1:0:0:dup` | Duplicate | ClearReq | Tombstone HIT → cached accepted=true | Final value `0x47AA0011` readable on all nodes; `[UBFAULT]` or `[E2E-FAULT]` evidence required |
| **TC48** | `InvalidateAck:2:0:0:dup` | Duplicate | InvalidateAck | Ack bitmask idempotent handling | Final value `0x48BB0022` converged; no data corruption despite double-ack |
| **TC49** | `InvalidateAck:1:0:0:dup` | Duplicate | InvalidateAck | Ack bitmask idempotent handling (same mechanism as TC48, different target node) | Final value `0x49CC0033` converged; perturbation absorbed |

Note: All three TCs use **duplicate** (`action=dup`), not **drop** (`action=drop`). The `Delay` action exists in `applyFaultRules()` but is marked as `TODO: implement deferred enqueue via event` (line 690-691 of UBRouter.cc) — it passes through with `copies=1` (no-op).

---

## 6. Known Gaps

| # | Gap | Impact | Suggested Fix |
|---|---|---|---|
| **G1** | No timeout on outstanding RECALL/INVALIDATE/GRANT_HANDSHAKE — `OpStage::TIMED_OUT` defined but never set | Lost fire-and-forget message (RecallResp/InvalidateAck) stalls protocol permanently | Add per-outstanding deadline tick; on expiry, retire to tombstone with accepted=false and replay pending requesters |
| **G2** | `handleRemoteMiss()` ignores `sendClear()` return value | ClearReq loss not propagated — caller thinks grant succeeded but home never committed | Check return value; if false, don't return grant success to EP-RNF; schedule retry |
| **G3** | No bounded retry budget for any message type | Infinite retry or silent dropping; no backpressure signal | Add retry counter to `PendingRequester` / outstanding; drop after N retries |
| **G4** | `Delay` fault action is unimplemented (exists in `applyFaultRules` but pass-through) | Delay-based reorder testing not available | Implement deferred enqueue via scheduled event |
| **G5** | Fault injection guarded by `#ifndef NDEBUG` | Production builds have no fault injection capability | Acceptable for debugging; document that fault injection is non-product |
| **G6** | `_pendingRequesters` per-PA queue has `MAX_PENDING_PER_PA` cap but no fairness | Queue-full drops without notification | Return BUSY instead of dropping when queue is full; let callers retry |
| **G7** | `processInvalidationAck` with missing outstanding returns `true` silently | Lost ack after completion not detectable | Acceptable for idempotent correctness; consider logging for debugging |
| **G8** | No cross-PA ordering guarantees | Messages for different PAs can be reordered freely | Acceptable per design (PA-level isolation); document as intentional |

---

## 7. Summary of Protection Coverage

| Area | Coverage | Confidence |
|---|---|---|
| **Duplicate Clear within window W** | Full — tombstone + replayArmed + pending queue dedup | ✅ High |
| **Duplicate InvalidateAck** | Full — bitmask + epoch gate + missing-outstanding idempotent | ✅ High |
| **Duplicate RecallResp** | Full — epoch gate + outstanding scope + target match | ✅ High |
| **Duplicate outer request** | Full — pending queue `dup_retry` scan | ✅ High |
| **Stale epoch rejection** | Full — `isNewerEpoch` half-range on all commit paths | ✅ High |
| **Chained replay ordering** | Partial — FIFO within PA, deferred to chained commits | ✅ Medium |
| **Lost ClearReq recovery** | **None** — no retry or timeout path | ❌ Low |
| **Lost RecallResp recovery** | **None** — TIMED_OUT defined but unused | ❌ Low |
| **Lost InvalidateAck recovery** | **None** — no retransmit mechanism | ❌ Low |
| **Bounded retry budget** | **None** — no retry counters | ❌ Low |
