# FV-3: OutstandingRequest Lifecycle Audit

File: `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc` / `.hh`

---

## 1. OutstandingRequest — Field Definitions

| Field | Type | Init | Purpose |
|-------|------|------|---------|
| `linePa` | uint64_t | 0 | Cache-line physical address |
| `opType` | OpType | GRANT_HANDSHAKE | RECALL / INVALIDATE / GRANT_HANDSHAKE / UPGRADE_PENDING |
| `stage` | OpStage | CREATED | Lifecycle stage |
| `requesterNode` | int | -1 | Who originated the request |
| `targetNode` | int | -1 | Recall target / upgrade requester |
| `targetMask` | uint64_t | 0 | Invalidation target mask |
| `totalMask` | uint64_t | 0 | Full invalidation mask (frozen at create) |
| `pendingAckCount` | int | 0 | Remaining invalidation acks |
| `ackMask` | uint64_t | 0 | Received invalidation ack bits (monotonic OR) |
| `replayArmed` | bool | false | Retry-from-replay allowed to match this grant |
| `recallBarrierDone` | bool | false | Recall response received |
| `invalidateBarrierDone` | bool | false | All invalidation acks received |
| `clearAckCached` | bool | false | ClearAck cached for tombstone replay |
| `reservedEpoch` | uint64_t | 0 | Intent reservation (committed epoch + 1) |
| `baseEpoch` | uint64_t | 0 | Epoch at request time |
| `reqId` | uint64_t | 0 | Request ID |
| `intendedState/SharersMask/OwnerNode/Dirty` | various | defaults | Intended directory result |
| `upgradeTargetMask/AckMask/PendingAckCount` | uint64/int | 0 | Upgrade-specific invalidation tracking |

---

## 2. Lifecycle Table: Create → Stage Transitions → Remove

### 2a. GRANT_HANDSHAKE

| # | Create line | Trigger | Stage sequence | Remove line | Trigger |
|---|-------------|---------|---------------|-------------|---------|
| 1 | 559 | G_I + RS | WAITING_CLEAR → [Clear] → DONE/TOMBSTONE | 2082 | processClear accepted → retireToTombstone + erase |
| 2 | 577 | G_I + RU (no WI) | WAITING_CLEAR → [Clear] → DONE | 2082 | same |
| 3 | 594 | G_I + RU (WI) | WAITING_CLEAR → [Clear] → DONE | 2082 | same |
| 4 | 617 | G_S + RS | WAITING_CLEAR → [Clear] → DONE | 2082 | same |
| 5 | 688 | G_S + RU (no other sharers) | WAITING_CLEAR → [Clear] → DONE | 2082 | same |
| 6 | 731 | After removing RECALL.DONE retry hit | WAITING_CLEAR → [Clear] → DONE | 2082 | same |
| 7 | 879 | G_E/G_M + RS (no recall) | WAITING_CLEAR → [Clear] → DONE | 2082 | same |
| 8 | 901 | G_E/G_M + RU (no recall) | WAITING_CLEAR → [Clear] → DONE | 2082 | same |
| — | 1396 | INVALIDATE→GRANT_HANDSHAKE (in-place conversion) | WAITING_CLEAR → [Clear] → DONE | 2082 | same |

**Stale removal:** line 2034 — epoch mismatch → retireToTombstone(false) + `removeOutstanding` (no commit).

### 2b. INVALIDATE

| # | Create line | Trigger | Stage sequence | Remove line | Trigger |
|---|-------------|---------|---------------|-------------|---------|
| 1 | 658 | G_S + RU with other sharers | WAITING_ALL_ACKS → [all acks] → DONE | 1396 | In-place conversion to GRANT_HANDSHAKE (`opType=GRANT_HANDSHAKE`, `stage=WAITING_CLEAR`). No `removeOutstanding` call. Final removal deferred to GRANT_HANDSHAKE Clear. |

### 2c. RECALL

| # | Create line | Trigger | Stage sequence | Remove line | Trigger |
|---|-------------|---------|---------------|-------------|---------|
| 1 | 859 | G_E/G_M + recall needed | WAITING_TARGET_RESP → [RecallResponse] → DONE → waits for retry | 725 | Same-requester retry in `processOuterRequest` → `removeOutstanding` then create GRANT_HANDSHAKE |

**Note:** RECALL.DONE stays in `_outstandingReqs` indefinitely until the original requester retries. No timeout-based eviction. If the requester never retries the RECALL.DONE entry leaks, keeping the line pinned via `refreshPinnedBit` (line 243).

### 2d. UPGRADE_PENDING

| # | Create line | Trigger | Stage sequence | Remove line | Trigger |
|---|-------------|---------|---------------|-------------|---------|
| 1 | 1788 | processOuterUpgradeReq | WAITING_ALL_ACKS → [all acks] → WAITING_LOCAL_DONE → [Done] → DONE | 1939 | processOuterUpgradeDone → `commitIntendedResult` + `removeOutstanding` |
| — | — | (early-Done variant) | WAITING_ALL_ACKS → [Done arrives early] → cached → [all acks] → DONE | 1377 | processInvalidationAck (allAcksDone) + upgradeDoneArrived → commit + `removeOutstanding` |

---

## 3. Create/Remove Balance (Leak Check)

| OpType | Create sites | Remove sites | Balanced? |
|--------|-------------|-------------|-----------|
| GRANT_HANDSHAKE | 559, 577, 594, 617, 688, 731, 879, 901, (+1396 conversion from INVALIDATE) | 2034 (stale), 2082 (success) | **YES** — every GRANT_HANDSHAKE is removed on Clear (accepted or epoch-mismatch) |
| INVALIDATE | 658 | 1396 (converted in-place to GRANT_HANDSHAKE, not erased) | **YES** — no leak; object lives on as GRANT_HANDSHAKE |
| RECALL | 859 | 725 (same-requester retry consumes it) | **PARTIAL** — removed only if requester retries; no timeout/GC. Terminal DONE stays in map if no retry. |
| UPGRADE_PENDING | 1788 | 1377 (early-Done), 1939 (normal Done) | **YES** — always removed on commit |

**Finding:** RECALL.DONE objects have no background eviction. If the requester never retries (e.g., crashes), the entry leaks in `_outstandingReqs` and keeps the line pinned. A periodic tombstone-style cleanup for RECALL.DONE entries may be needed.

---

## 4. replayArmed Set/Clear Conditions

| Line | Action | Context |
|------|--------|---------|
| 149 (hh) | `replayArmed(false)` | Constructor default — all new outstanding start unarmed |
| 1398 | `ost->replayArmed = true` | INVALIDATE→GRANT_HANDSHAKE conversion (all acks received). Allows the requester's retry to match without duplication. |
| 2519 | `ost->replayArmed = true` | During `replayPendingRequesters`, after replay creates a new outstanding. Marks the grant so the replayed requester's subsequent Clear retry hits the match path. |

**Check site** (line 447):
```
if (existing->replayArmed &&
    existing->stage == OpStage::WAITING_CLEAR &&
    existing->reqId == reqId &&
    existing->reqType == reqType &&
    existing->writeIntent == writeIntent)
```
→ Returns the grant directly instead of BUSY.

**Property:** `replayArmed` is **set-only** (never cleared back to false for a live object). Once armed, remains armed until object destruction. This is safe because:
- The flag only gates the duplicate-retry fast path
- Once the GRANT_HANDSHAKE is removed (Clear), the object is gone
- No scenario requires disarming a live outstanding

---

## 5. ackMask Monotonicity Verification

| Field | Init | Mutation | Monotonic? |
|-------|------|----------|------------|
| `ackMask` (INVALIDATE) | 0 (line 2575, 669) | `effAckMask \|= nodeBit` (line 1276) | **YES** — only OR; bits never cleared |
| `upgradeAckMask` (UPGRADE_PENDING) | 0 (line 1821, 2575) | same line 1276 via reference | **YES** — only OR |
| `pendingAckCount` | 0 (line 2574) | `pendingAckCount--` (line 1280) | **YES** — only decrements (by construction, never negative due to duplicate-ack guard at line 1267) |
| `upgradePendingAckCount` | 0 (line 1820) | `upgradePendingAckCount--` (line 1278) | **YES** — only decrements |

**Duplicate-ack guard** (line 1267–1273):
```cpp
if (effAckMask & nodeBit) {
    // duplicate ack — ignore
    return true;
}
```
Ensures no double-counting on `pendingAckCount` decrement and no double-set on `ackMask`.

**Invariant:** For INVALIDATE and UPGRADE_PENDING:
```
ackMask ⊆ totalMask   (all acked bits are a subset of targeted bits)
pendingAckCount == popcount(totalMask) - popcount(ackMask)
```

---

## 6. Summary of Findings

| Check | Status | Detail |
|-------|--------|--------|
| Every create has a matching remove | ✅ GRANT_HANDSHAKE, INVALIDATE, UPGRADE_PENDING | ⚠️ RECALL.DONE has removal path but relies on requester retry; no timeout GC |
| replayArmed set/clear balanced | ✅ set-only, never cleared (intentional) | Safe because only gates retry-fast-path, object destroyed at Clear |
| ackMask monotonic (only grows) | ✅ OR-only, never cleared | Duplicate-ack guard prevents double-counting |
| `pendingAckCount` never goes negative | ✅ | Duplicate guard at line 1267; decrement only on first ack from each node |

**Recommendation:** Consider adding a timeout/eviction for RECALL.DONE entries that stay too long, to prevent pinned-line leaks.
