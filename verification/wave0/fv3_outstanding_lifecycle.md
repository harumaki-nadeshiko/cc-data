# FV-3: OutstandingRequest Lifecycle Audit

**Method**: Static path tracing through `UBCCController.cc` / `.hh`.
**Scope**: All 4 OpTypes × all reachable OpStage transitions.

---

## 1. Per-OpType Lifecycle Tables

### 1.1 RECALL

| Stage | Next Stage(s) | Trigger | Actions | Commit? |
|---|---|---|---|---|
| `CREATED` | `WAITING_TARGET_RESP` | `processOuterRequest` G_E/G_M case, `initiateRecall` + `createOutstanding` (L859-870) | Allocates epoch, sets targetNode=owner, reqType/writeIntent captured | No |
| `WAITING_TARGET_RESP` | `DONE` | `processRecallResponse` (L1140-1141) | `recallBarrierDone=true`, stores dataBuf if present (L1151-1154) | No — DirEntry NOT modified |
| `DONE` | *consumed* → `GRANT_HANDSHAKE.WAITING_CLEAR` | **Same requester retry**: `processOuterRequest` L714-778 | `removeOutstanding(RECALL)` (L725), `createOutstanding(GRANT_HANDSHAKE)` (L731), copies dataBuf, sets `recallBarrierDone=true` | No (grant commit deferred to Clear) |
| `DONE` | *stays DONE, not consumed* | **Different requester**: L783-835 | New requester enqueued, RECALL remains in map | N/A |
| `DONE` | `CANCELLED` | **NEVER ASSIGNED** (dead enum) | — | — |
| `DONE` | `TIMED_OUT` | **NEVER ASSIGNED** (dead enum) | — | — |

**Terminal reachability**: RECALL terminates at `DONE`. The `DONE` entry persists in `_outstandingReqs` until consumed by same-requester retry (L714-778) or **leaked** (see §2).

---

### 1.2 INVALIDATE

| Stage | Next Stage(s) | Trigger | Actions | Commit? |
|---|---|---|---|---|
| `CREATED` | `WAITING_ALL_ACKS` | `processOuterRequest` G_S → RU with otherSharers ≠ 0 (L658-677) | Sets `targetMask=otherSharers`, `pendingAckCount`, `ackMask=0`, intended result fields | No |
| `WAITING_ALL_ACKS` | `DONE` | `processInvalidationAck` all acks done (L1404-1406) | `invalidateBarrierDone=true`, stage=DONE, then **in-place converts** to `GRANT_HANDSHAKE.WAITING_CLEAR` (L1410-1413) | No (conversion, not commit) |
| `WAITING_ALL_ACKS` | `CANCELLED` | **NEVER ASSIGNED** | — | — |
| `WAITING_ALL_ACKS` | `TIMED_OUT` | **NEVER ASSIGNED** | — | — |

**Note**: INVALIDATE never terminates as itself — it always converts in-place to `GRANT_HANDSHAKE.WAITING_CLEAR` (L1410-1413). The `opType` is mutated, `replayArmed=true` is set (L1414). The `ackMask` and `pendingAckCount` fields become inert after conversion.

---

### 1.3 GRANT_HANDSHAKE

| Stage | Next Stage(s) | Trigger | Actions | Commit? |
|---|---|---|---|---|
| `CREATED` | `WAITING_CLEAR` | Any `processOuterRequest` fast path (G_I, G_S+RS, G_S+RU if otherSharers==0, G_E/G_M same-owner) | Sets intendedState/Owner/Sharers/Dirty, epoch, reqId | No |
| `CREATED` | `WAITING_CLEAR` | RECALL.DONE consumption (L731-777) | Transfers dataBuf, sets `recallBarrierDone=true`, `dataSource=RecallBuffer` | No |
| `WAITING_CLEAR` | `DONE` → **retired** | `processClear` with matching (epoch, reqId, requester, stage) (L2107-2124) | `commitIntendedResult()` → DirEntry updated, epoch advanced; `retireToTombstone()`; `removeOutstanding()` | **YES** — DirEntry committed |
| `WAITING_CLEAR` | *stale → `DONE`* → **retired** | `processClear` epoch/reqId mismatch (L2052-2066) | `retireToTombstone(accepted=false)`; `removeOutstanding()` | No (rejected) |
| `WAITING_CLEAR` | *none (stays)* | Clear for wrong stage / no grant (L2092-2100, L2037-2047) | Warning logged, Clear dropped | No |
| `CREATED/WAITING_CLEAR` | `CANCELLED` | **NEVER ASSIGNED** | — | — |
| `CREATED/WAITING_CLEAR` | `TIMED_OUT` | **NEVER ASSIGNED** | — | — |

**Terminal state**: GRANT_HANDSHAKE always terminates via `removeOutstanding()` at L2124 (success) or L2064 (stale retire). Tombstone replaces it for window W.

---

### 1.4 UPGRADE_PENDING

| Stage | Next Stage(s) | Trigger | Actions | Commit? |
|---|---|---|---|---|
| `CREATED` | `WAITING_ALL_ACKS` | `processOuterUpgradeReq` with non-zero targetMask (L1836-1854) | Sets upgrade target/ack fields, `accepted=false` | No |
| `CREATED` | `WAITING_LOCAL_DONE` | `processOuterUpgradeReq` with zero targetMask (L1855-1871) | `accepted=true`, immediate Ack(true) | No |
| `WAITING_ALL_ACKS` | `WAITING_LOCAL_DONE` | `processInvalidationAck` all acks done (L1348-1353) | `invalidateBarrierDone=true`, `accepted=true`, sends UpgradeAckNotify | No |
| `WAITING_ALL_ACKS` | *cached* (stays WAITING_ALL_ACKS) | `processOuterUpgradeDone` arrives before all acks (L1915-1932) | `upgradeDoneArrived=true`, caches Done tuple, deferred commit | No |
| `WAITING_LOCAL_DONE` | `DONE` | `processOuterUpgradeDone` (L1961-1963) | `commitIntendedResult()` → DirEntry updated; `removeOutstanding()` | **YES** — DirEntry committed |
| `WAITING_ALL_ACKS` + `upgradeDoneArrived=true` | `DONE` | `processInvalidationAck` all acks done (L1383-1401) | `commitIntendedResult()`; `removeOutstanding()` | **YES** — DirEntry committed |
| any | `CANCELLED` | **NEVER ASSIGNED** | — | — |
| any | `TIMED_OUT` | **NEVER ASSIGNED** | — | — |

**Terminal state**: UPGRADE_PENDING always terminates at `DONE` → `removeOutstanding()` (L1393, L1963).

---

## 2. Leak Checklist

| Condition | `createOutstanding()` paired? | Analysis | Status |
|---|---|---|---|
| **GRANT_HANDSHAKE** L2124 (Clear accept) | Yes: `retireToTombstone()` + `removeOutstanding()` | Clean | ✅ |
| **GRANT_HANDSHAKE** L2064 (stale Clear) | Yes: `retireToTombstone(false)` + `removeOutstanding()` | Clean | ✅ |
| **UPGRADE_PENDING** commit (L1963) | Yes: `removeOutstanding()` after commit | Clean | ✅ |
| **UPGRADE_PENDING** early Done path (L1393) | Yes: `removeOutstanding()` after deferred commit | Clean | ✅ |
| **INVALIDATE → GRANT_HANDSHAKE** (L1410-1413) | No removal — in-place conversion. No leak. | Intentional — same object reused | ✅ |
| **RECALL.DONE** consumed by same requester (L725) | Yes: `removeOutstanding(RECALL)` | Clean | ✅ |
| **RECALL.DONE** *not consumed* | **No**: stays in `_outstandingReqs` forever | **ORPHAN LEAK**: no timeout, no cleanup mechanism | ❌ |

**Leak finding FV3-LEAK-001**: A `RECALL.DONE` entry for requester A that is never followed by A's retry remains in `_outstandingReqs` indefinitely. Because `createOutstanding()` (L2594) returns `nullptr` when a key already exists, no new outstanding (GRANT_HANDSHAKE, RECALL, INVALIDATE, UPGRADE_PENDING) can be created for that `linePa`. The `TIMED_OUT` and `CANCELLED` stages are defined but never assigned, so no timer-based eviction exists.

**Mitigation in practice**: The enqueue logic (L783-835) handles the case of a different requester arriving while RECALL.DONE exists, but the enqueued request will never be replayed because `replayPendingRequesters()` requires a preceding `removeOutstanding()` + commit cycle. Add epoch-stall detection or periodic cleanup for orphan DONE entries.

---

## 3. `replayArmed` Trigger/Clear Conditions

| Set To | Location | Condition | Rationale |
|---|---|---|---|
| `true` | L1414 | `processInvalidationAck`: INVALIDATE all acks done → convert to GRANT_HANDSHAKE | The resulting GRANT_HANDSHAKE was not created by a direct outer request; requester will retry with same grant tuple. `replayArmed` allows the retry to match (L447-465). |
| `true` | L2564 | `replayPendingRequesters`: after dequeuing and calling `processOuterRequest`, if a new outstanding was created | The outstanding was created by replaying a queued entry — the requester's subsequent retry must be recognized as a hit, not a duplicate (F24). |
| `false` | Init (L147, L2614) | Constructor / `createOutstanding()` | Default: not a replay-created grant. |
| `false` | *Never cleared after being set* | — | Once `replayArmed` is set on a GRANT_HANDSHAKE or INVALIDATE→GRANT_HANDSHAKE conversion, it remains `true` for the lifetime of that outstanding object. |

**Verification**: `replayArmed` is checked only in the outer-request dispatch (L447): if `replayArmed && stage==WAITING_CLEAR && matching tuple`, the grant is returned directly instead of returning BUSY. This is correct because:
- Non-replay grants already delivered their grant; if the requester retries, it's either a duplicate (BUSY) or a replayArmed hit.
- `replayArmed` is never cleared after being set — this is safe because the grant object is removed on Clear (L2124) before any requester could exploit a stale `replayArmed`.

**No missing clear** — benefit of single-use grant objects.

---

## 4. `ackMask` Monotonicity Verification

### INVALIDATE path (`ackMask`, `pendingAckCount`)

| Operation | Line | Effect | Monotonic? |
|---|---|---|---|
| Initialize `ackMask=0` | L669 | fresh | ✅ baseline |
| Set bit: `effAckMask \|= nodeBit` | L1290 | bits only added | ✅ monotonic |
| Decrement `pendingAckCount` | L1294 | — | N/A (counter, not mask) |
| In-place conversion → GRANT_HANDSHAKE | L1410-1413 | `ackMask` NOT cleared, but GRANT_HANDSHAKE ignores it | ✅ safe (fields become inert) |

### UPGRADE_PENDING path (`upgradeAckMask`, `upgradePendingAckCount`)

| Operation | Line | Effect | Monotonic? |
|---|---|---|---|
| Initialize `upgradeAckMask=0` | L1843/L1861 | fresh | ✅ baseline |
| Set bit: `effAckMask \|= nodeBit` | L1290 (via `effAckMask = ost->upgradeAckMask`) | bits only added | ✅ monotonic |
| Decrement `upgradePendingAckCount` | L1292 | — | N/A |
| Commit → `removeOutstanding()` | L1393/L1963 | object destroyed | ✅ |

**Both masks are strictly monotonic**: bits are only ever set (OR-assigned), never cleared. The only code that writes to these masks outside initialization is line 1290:

```cpp
effAckMask |= nodeBit;
```

No path clears, resets, or XORs the mask. No path loses set bits.

**Verification of all writes to `ackMask` / `upgradeAckMask`**:

| Variable | Write sites | Analysis |
|---|---|---|
| `ost->ackMask` | L670 (init=0), L1290 (`\|=`) | Monotonic ✅ |
| `ost->upgradeAckMask` | L1843 (init=0), L1861 (init=0), L1290 (`\|=`) | Monotonic ✅ |
| `ost->totalMask` | L667 (init=otherSharers), L1842 (init=targetMask) | Set once, never modified ✅ |
| `ost->upgradeTargetMask` | L1840 (init=targetMask) | Set once, never modified ✅ |

**Conclusion**: Ack masks are monotonic. No violation found.

---

## 5. Summary of Findings

| ID | Severity | Category | Description |
|---|---|---|---|
| FV3-LEAK-001 | **Medium** | Leak | `RECALL.DONE` entries are never cleaned up if the requester does not retry. `TIMED_OUT`/`CANCELLED` defined but never assigned — no timeout mechanism for orphan RECALLs. |
| FV3-DEAD-001 | Low | Dead code | `OpStage::CANCELLED`, `OpStage::TIMED_OUT`, `OpStage::PERSISTENT_BUSY` are defined in the enum but never assigned anywhere in the .cc file. Only checked as exclusion conditions (`isLineBusy`, existing-outstanding guard). |
| FV3-MONO-001 | ✅ Pass | Ack monotonicity | Both `ackMask` (INVALIDATE) and `upgradeAckMask` (UPGRADE_PENDING) are strictly OR-accumulated; no code path clears set bits. |
| FV3-REARM-001 | ✅ Pass | replayArmed | Set only at L1414 and L2564; never cleared (not needed — grant object removed on Clear). Correctly gated with `stage==WAITING_CLEAR` and full tuple match. |
| FV3-TERM-001 | ✅ Pass | Terminal convergence | GRANT_HANDSHAKE: always reaches `removeOutstanding()` (success L2124 or stale L2064). UPGRADE_PENDING: always reaches `removeOutstanding()` (L1963 or L1393). INVALIDATE: always converts in-place to GRANT_HANDSHAKE (no leak). RECALL: reaches DONE but may orphan. |
