# M7 Stage Delivery Report

- **Stage:** M7 — Writeback / Evict / Owner Transfer
- **Status:** PASS
- **Completion Date:** 2026-05-27
- **Review Rounds:** 2 (initial + Fix Round)
- **Orchestrator Verdict:** PASS

---

## 1. Stage Summary

### 1.1 Stage Goal

Complete the writeback/evict/owner transfer loop to support full three-node coherence: dirty writeback updates home metadata, clean eviction updates sharer masks, owner transfer between nodes is serialized, stale epochs are rejected, and recall results are correctly split (read→shared downgrade, write→invalidate).

### 1.2 Completion Status

| Criterion | Result |
|---|---|
| Dirty writeback → home ack | PASS |
| Clean evict → sharer mask update | PASS |
| Owner transfer (single owner invariant) | PASS |
| Epoch stale filtering | PASS |
| Home UBCC still metadata-only | PASS |
| Recall result splitting (read→shared, write→invalid) | PASS |
| Single global owner at any time | PASS |

### 1.3 Review Rounds

| Round | Date | Key Findings | Resolution |
|---|---|---|---|
| R1 (initial) | 2026-05-26 | Full M7 implementation submitted | Pending validator review |
| Fix Round | 2026-05-27 | P0: owner mismatch reject (non-owner writeback rejected), P0: evict dirty guard (dirty owner evict blocked until writeback), P0: evict non-owner reject; P1: recall split PA fix, epoch 0 remove guard | All P0/P1 resolved |

---

## 2. Code Changes

### 2.1 gem5 Submodule

| File | Change | Description |
|---|---|---|
| `src/mem/ruby/protocol/chi/ep/UBCCController.hh` | Extended | `processWriteback()`: handles `GlobalWriteback` from dirty owner; `processEvict()`: handles `GlobalEvict` from clean sharer/owner; `processOwnerTransfer()`: serializes owner handoff; epoch management: per-line `epoch` counter, `validateEpoch()` stale check |
| `src/mem/ruby/protocol/chi/ep/UBCCController.cc` | Extended | **Writeback path**: owner sends `GlobalWriteback` with data + dirty flag → home updates metadata (dirty=false, may transition to `G_I`/`G_S`/`G_E`), sends `GlobalAck` → owner can then evict. **Evict path**: sharer sends `GlobalEvict` → home removes sharer from mask → if mask becomes empty, line goes to `G_I`. **Owner transfer**: old owner is recalled/invalidated, new owner is installed — single-owner invariant enforced via epoch. **Epoch filtering**: stale responses (epoch < current) are rejected and logged; epoch=0 entries removed. **Recall result splitting**: read recall → old owner downgraded to shared (stays in sharers mask), write/unique recall → old owner invalidated (removed from sharers mask) |
| `src/mem/ruby/protocol/chi/ep/EPBackend.hh` | Extended | `handleWriteback()`: requester-side writeback initiation; `handleEvict()`: requester-side eviction; writeback counter + evict counter for test observation |
| `src/mem/ruby/protocol/chi/ep/EPBackend.cc` | Extended | Writeback flow: requester detects HN eviction/writeback of dirty remote line → sends `GlobalWriteback` to home → waits for `GlobalAck` → completes. Eviction flow: requester detects clean eviction → sends `GlobalEvict` → home acknowledges. Owner transfer: home UBCC coordinates old owner invalidation and new owner installation |
| `src/mem/ruby/protocol/chi/ep/M7SelfTest.cc` | New | 52 ternary checks: TC-M7-1 dirty writeback (6 checks: writeback updates state, dirty cleared, subsequent read sees correct state), TC-M7-2 clean evict (6 checks: evict removes sharer, dirty not set, non-owner evict rejected, dirty owner evict blocked), TC-M7-3 single global owner (6 checks: owner transfer between nodes, never dual-owner), TC-M7-4 stale epoch rejected (8 checks: stale ack/data rejected, not contaminating current transaction, epoch mismatch detection), TC-M7-5 metadata-only (4 checks: writeback/evict/transfer don't add data storage), TC-M7-6 recall result split (10 checks: read→shared downgrade, write→invalid), plus EPBackend counters and structural checks |

**gem5 commit history (M7-related):**

| Commit | Description |
|---|---|
| `b41fe6012c` | M7 Fix Round: P0 (owner mismatch reject, evict dirty guard, evict non-owner reject) + P1 (recall split PA, epoch 0 remove) |

### 2.2 Superproject

| File | Change | Description |
|---|---|---|
| `tests/phase7/test_m7.py` | Local-only verification script (not committed to repo) | PY_INJECT harness: full CHI+UBCC topology, runs M4/M5/M6/M7 all self-tests at instantiation, captures C++ stdout, parses PASS/FAIL from all four stages, regression gate (M4/M5/M6 failures block M7), test case coverage report for all 6 M7 test cases |

**Superproject commit history:**

| Commit | Description |
|---|---|
| `7e5a1d4` | M7 Fix Round: bump gem5 submodule (P0 owner mismatch reject, evict guard, P1 recall PA, epoch) |

---

## 3. Deviations from Original Plan

### 3.1 Alignment with `plan/03-phase-plan.md`

| Planned | Actual | Notes |
|---|---|---|
| Requester dirty writeback → home ack | Done | `GlobalWriteback` path: owner sends data → home updates → `GlobalAck` → requester completes |
| Requester clean evict → sharer mask update | Done | `GlobalEvict` path: sharer removed from mask; auto-transition to `G_I` if mask empty |
| Owner transfer | Done | Serialized via epoch; single-owner invariant enforced |
| Epoch or equivalent stale protection | Done | Per-line epoch counter; stale responses (epoch < current) rejected |
| Home UBCC still metadata-only | Done | No line data storage added in M7; all data flows through owner nodes |
| Recall result splitting (read→shared, write→invalid) | Done | Read recall: owner downgraded to shared; Write/unique recall: owner invalidated |

### 3.2 Key Design Decisions

| Decision | Rationale |
|---|---|
| Non-owner writeback rejection | Only the current owner (verified by `ownerNode` in directory) can write back; mismatched writebacks are rejected |
| Dirty owner evict blocking | A dirty owner must write back before eviction; evict on a dirty owner line is rejected |
| Epoch 0 entry removal | Entries with epoch=0 are treated as invalid/removed; prevents stale-zero snafus |
| Sharer eviction → auto `G_I` | When last sharer evicts, line returns to `G_I` |
| Recall PA verification | Recall response PA must match the line PA being recalled; mismatch → fatal |

### 3.3 Writeback/Evict/Transfer State Table

| Current Home State | Event | Guard | Actions | Next State |
|---|---|---|---|---|
| `G_M` | `GlobalWriteback` from owner | `epoch` matches, `requester == ownerNode` | Metadata update: dirty=false | `G_I` (if no sharers) or `G_S` |
| `G_S` | `GlobalEvict` from sharer | `sharer in mask`, not dirty | Remove sharer from mask | `G_S` or `G_I` |
| `G_E` | `GlobalEvict` from clean owner | `requester == ownerNode`, `dirty==false` | Clear owner | `G_I` |
| `G_E/G_M` | Owner transfer request | Competing unique/write | Recall old owner → install new owner | `G_E` or `G_M` |

### 3.4 Epoch Filtering

| Current Epoch | Incoming Response Epoch | Action |
|---|---|---|
| N | N | Accept (if txn context matches) |
| N | < N | Reject as stale (do not mutate state) |
| N | > N | Reject (unless forward epoch creation is explicitly supported — M7 does not) |

### 3.5 Recall Result Split

| Trigger | Old Owner Result |
|---|---|
| Remote read recalls owner | Old owner downgraded to shared (`G_S`, remains in sharers mask) |
| Remote unique/write recalls owner | Old owner invalidated (removed from all masks, line becomes `G_I` or new owner gets `G_E`/`G_M`) |

### 3.6 Scope Boundaries

| In Scope (Implemented) | Not Yet Implemented (M8) |
|---|---|
| Single global owner invariant | Multi-sharer management (M8) |
| Dirty writeback → home → ack | Shared hardening (M8) |
| Clean evict → sharer mask update | GlobalInvalidate for upgrade (M8) |
| Stale epoch filtering | — |
| Recall result split (downgrade vs invalidate) | — |

### 3.7 Consistency with `plan/02-external-proxy-spec.md`

| Spec Requirement | Implementation | Status |
|---|---|---|
| Owner writeback updates home (§7.2) | `processWriteback()`: metadata update, `GlobalAck` | PASS |
| Clean evict updates sharer mask (§7.2) | `processEvict()`: sharer removed, mask cleanup | PASS |
| Single owner invariant (§8) | Epoch serialization, owner mismatch rejection | PASS |
| Home metadata-only continues (§6.1) | No data storage; writeback data routed through | PASS |
| Recall result split (§8.1-8.2) | Read → downgrade shared; Unique/write → invalidate | PASS |

---

## 4. Test Cases

### 4.1 TC-M7-1: Dirty Writeback Updates Home

| Attribute | Value |
|---|---|
| **ID** | TC-M7-1 (M7-1-1 through M7-1-6) |
| **Name** | Dirty Writeback Updates Home |
| **Type** | PY_INJECT (C++ self-test) |
| **Assertions** | 6 |
| **Expected** | Writeback from dirty owner updates directory state; dirty flag cleared; subsequent read sees correct state; data not lost; writeback counter increments |
| **Actual** | PASS |
| **Negative** | Writeback from non-owner rejected |

### 4.2 TC-M7-2: Clean Evict Updates Sharer Mask

| Attribute | Value |
|---|---|
| **ID** | TC-M7-2 (M7-2-1 through M7-2-5, M7-2-ext) |
| **Name** | Clean Evict Updates Sharer Mask |
| **Type** | PY_INJECT (C++ self-test) |
| **Assertions** | 6 |
| **Expected** | Evict removes sharer from mask; dirty not set by evict; non-owner evict rejected; dirty owner evict blocked; sharer mask correctly reflects remaining nodes |
| **Actual** | PASS |
| **Negative** | Non-owner evict rejected; dirty owner evict blocked |

### 4.3 TC-M7-3: Single Global Owner In Ping-Pong

| Attribute | Value |
|---|---|
| **ID** | TC-M7-3 (M7-3-1 through M7-3-6) |
| **Name** | Single Global Owner In Ping-Pong |
| **Type** | PY_INJECT (C++ self-test) |
| **Assertions** | 6 |
| **Expected** | At every snapshot during owner transfer, `ownerNode` is unique; no dual-owner state; owner transfer serialized via epoch |
| **Actual** | PASS |
| **Negative** | No intermediate state with two owners |

### 4.4 TC-M7-4: Stale Epoch Rejected

| Attribute | Value |
|---|---|
| **ID** | TC-M7-4 (M7-4-1 through M7-4-8) |
| **Name** | Stale Epoch Rejected |
| **Type** | PY_INJECT (C++ self-test) |
| **Assertions** | 8 |
| **Expected** | Stale response (epoch < current) rejected; no state mutation; current transaction uses correct epoch; epoch mismatch detection works; stale ack dropped; stale data dropped |
| **Actual** | PASS |
| **Negative** | Stale response does NOT contaminate directory state |

### 4.5 TC-M7-5: Metadata-Only Home Still Correct

| Attribute | Value |
|---|---|
| **ID** | TC-M7-5 (M7-5-1 through M7-5-4) |
| **Name** | Metadata-Only Home Still Correct |
| **Type** | PY_INJECT (C++ self-test) |
| **Assertions** | 4 |
| **Expected** | Writeback/evict/transfer operations do not require home UBCC to store line data; directory state consistently matches subsequent reads |
| **Actual** | PASS |
| **Negative** | No line data field added to DirEntry |

### 4.6 TC-M7-6: Recall Result Split

| Attribute | Value |
|---|---|
| **ID** | TC-M7-6 (M7-6a-1..5, M7-6b-1..5) |
| **Name** | Recall Result Split |
| **Type** | PY_INJECT (C++ self-test) |
| **Assertions** | 10 |
| **Expected** | Sub-scenario A (read recall): old owner downgraded to shared; Sub-scenario B (write/unique recall): old owner invalidated; both produce distinct observable states |
| **Actual** | PASS |
| **Negative** | Both scenarios do not produce same result state |

### 4.7 Summary

| Test Group | Checks | PASS | FAIL | SKIP |
|---|---|---|---|---|
| TC-M7-1 (Dirty writeback) | 6 | 6 | 0 | 0 |
| TC-M7-2 (Clean evict) | 6 | 6 | 0 | 0 |
| TC-M7-3 (Single global owner) | 6 | 6 | 0 | 0 |
| TC-M7-4 (Stale epoch) | 8 | 8 | 0 | 0 |
| TC-M7-5 (Metadata-only) | 4 | 4 | 0 | 0 |
| TC-M7-6 (Recall split) | 10 | 10 | 0 | 0 |
| M7-INFRA + counters | 12 | 12 | 0 | 0 |
| **Total** | **52** | **52** | **0** | **0** |

---

## 5. Regression Results

| Test | Status | Notes |
|---|---|---|
| TC1–TC5 | Pre-existing PASS | Unaffected |
| M4 Self-Test (within M7) | 0 FAIL | No regression from M4 |
| M5 Self-Test (within M7) | 0 FAIL | No regression from M5 |
| M6 Self-Test (within M7) | 0 FAIL | No regression from M6 |
| M7 Self-Test | 0 FAIL | All 52 checks pass |

> M7 test harness (`test_m7.py`) includes cumulative regression detection for M4/M5/M6. All stages report 0 FAIL.

---

## 6. Incomplete / TODO

| Item | Status | Notes |
|---|---|---|
| Multi-sharer shared path hardening | Deferred to M8 | Sharer mask correctness under concurrent access |
| GlobalInvalidate for upgrade (M8) | Deferred to M8 | When local upgrade hits external sharer |
| ARM_SYNC end-to-end workload | Deferred | M7 uses PY_INJECT (C++ self-test); ARM workloads deferred |

### 6.1 Known Limitations

1. **Writeback data flow** is structurally verified; in the single-gem5 prototype, `GlobalWriteback` data is passed via in-process method calls. In a real multi-gem5 deployment, data must traverse the outer network.
2. **Owner transfer** serialization works but does not handle cascading multi-hop transfers optimally — the focus is correctness, not latency.
3. **Epoch management** is per-line and monotonic; no global epoch counter.

### 6.2 Later Stage Backfill

| Item | Target Stage | Priority |
|---|---|---|
| Multi-sharer shared hardening | M8 | P0 |
| GlobalInvalidate / upgrade path | M8 | P0 |
| Owner transfer latency optimization | Post-M8 | P2 |
| ARM_SYNC workload E2E | Post-M8 | P1 |

---

## 7. Submodule State

| Attribute | Value |
|---|---|
| gem5 submodule changed | Yes |
| gem5 Fix Round commit | `b41fe6012c` (P0 + P1 fixes) |
| superproject final commit | `7e5a1d4` (M7 Fix Round: bump gem5 submodule) |

---

## 8. Build & Test Command Chain

```bash
# Build gem5
docker run --rm -v $(pwd):/workspace -w /workspace/gem5 \
    ubcc-dev:ubuntu20.04 bash -c "scons build/ARM/gem5.opt -j20 PROTOCOL=CHI"

# Run M7 tests (includes M4/M5/M6 regression)
docker run --rm -v $(pwd):/workspace -w /workspace \
    ubcc-dev:ubuntu20.04 bash -c \
    "./gem5/build/ARM/gem5.opt tests/phase7/test_m7.py <arm_binary>"

# Expected: EXIT CODE 0, M7_SELF_TEST_PASSED=1,
#           M4:0 FAIL, M5:0 FAIL, M6:0 FAIL, M7:0 FAIL
```
