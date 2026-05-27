# M8 Stage Delivery Report

- **Stage:** M8 — Shared-Read Hardening And Upgrade/Invalidate Closure
- **Status:** PASS
- **Completion Date:** 2026-05-27
- **Review Rounds:** 3 (initial + Fix Round + Final fix)
- **Orchestrator Verdict:** PASS

---

## 1. Stage Summary

### 1.1 Stage Goal

Harden the shared-read path from "it works" to "verifiably correct across all edge cases": multi-sharer mask maintenance, local upgrade triggering `GlobalInvalidate` to remote sharers, ack collection before grant, shared path enabled by default (no `force_grant_m` dependency).

### 1.2 Completion Status

| Criterion | Result |
|---|---|
| Multi-sharer mask correctly maintained | PASS |
| Local upgrade hits external sharer → `GlobalInvalidate` | PASS |
| Remote sharer acks collected → local unique completes | PASS |
| Shared path enabled by default | PASS |
| `force_grant_m` debug switch only | PASS |
| Two requesters can simultaneously hold shared | PASS |
| SharerMask correctness verified | PASS |

### 1.3 Review Rounds

| Round | Date | Key Findings | Resolution |
|---|---|---|---|
| R1 (initial) | 2026-05-27 | Full M8 implementation submitted | Pending validator review |
| Fix Round | 2026-05-27 | P0-1: home epoch for invalidation; P0-2: no-op reentry return (idempotent invalidation ack); P1-4: ackNode boundary check (protection against out-of-range node IDs) | All P0/P1 resolved |
| Final fix | 2026-05-27 | ackNode boundary check relocated to entry in `processInvalidationAck` before all early-returns | Final commit |

---

## 2. Code Changes

### 2.1 gem5 Submodule

| File | Change | Description |
|---|---|---|
| `src/mem/ruby/protocol/chi/ep/UBCCController.hh` | Extended | `processOuterRequest()` extended for invalidation-triggered paths; `processInvalidationAck()` for collecting per-sharer acks; `GlobalInvalidate` message type; `pendingInvalidations` set for tracking outstanding acks; `ackNode` boundary validation |
| `src/mem/ruby/protocol/chi/ep/UBCCController.cc` | Extended | **GlobalInvalidate flow**: when upgrade request (`GlobalReadUnique`) hits `G_S` state, home UBCC sends `GlobalInvalidate` to each sharer (except requester) → each sharer's EP_RNF invalidates local copy → sends ack back to home → `processInvalidationAck()` collects acks → when all acks received, home proceeds with grant to requester. **Home epoch**: invalidation is associated with home epoch for stale ack filtering. **Reentry protection**: duplicate/retried ack for same sharer is accepted as no-op (idempotent). **SharerMask management**: mask correctly updated on grant (add requester), evict (remove sharer), invalidate (remove sharer), downgrade (owner → sharer). **Shared default path**: `GlobalReadShared` + `writeIntent=false` → `GlobalGrantShared` (default); no unconditional `GrantM` fallback |
| `src/mem/ruby/protocol/chi/ep/EPBackend.hh` | Extended | `handleInvalidate()`: receives invalidation from home, triggers local invalidation via EP_RNF; `handleInvalidationAck()`: sends ack back to home; invalidation counter; `inspectSharerMaskForTest()` |
| `src/mem/ruby/protocol/chi/ep/EPBackend.cc` | Extended | Invalidation handling: home triggers `GlobalInvalidate` → sharer EPBackend invalidates local line → sends ack → home `processInvalidationAck` |
| `src/mem/ruby/protocol/chi/ep/M8SelfTest.cc` | New | 61 ternary checks: TC-M8-1 two requesters hold shared (9 checks: both added to sharers mask, both can read, concurrent shared access), TC-M8-2 local upgrade invalidates sharers (24 checks across 3 sub-scenarios: shared→upgrade invalidation flow, all sharers invalidated, ack collection, G_S→G_E/M transition), TC-M8-3 shared default path (7 checks: Shared request → GrantShared not GrantModified, no force_grant_m by default), TC-M8-4 sharerMask correctness (10 checks: add/remove sharers, concurrent add, max mask, empty→remove entry), plus busy-line checks, ackNode bounds, pending invalidation lifecycle tests |

**gem5 commit history (M8-related):**

| Commit | Description |
|---|---|
| `4a9a672335` | M8 Fix Round: P0-1 home epoch for invalidation, P0-2 no-op reentry return, P1-4 ackNode bounds check, add M8SelfTest.cc |
| `ad782435d6` | M8: move ackNode boundary check to entry in processInvalidationAck before all early-returns (P1-4) |
| `d1f6ec4947` | M8 fix: move ackNode boundary check before directory lookup |

### 2.2 Superproject

| File | Change | Description |
|---|---|---|
| `tests/phase8/test_shared_hardening.py` | New | PY_INJECT harness: full CHI+UBCC topology, runs M4/M5/M6/M7/M8 all self-tests at instantiation, captures C++ stdout, parses PASS/FAIL from all five stages, regression gate (M4/M5/M6/M7 failures block M8), test case coverage for all 4 M8 test cases |

**Superproject commit history:**

| Commit | Description |
|---|---|
| `16c1780` | M8 Fix Round: P0-1/P0-2/P1-4 fixes; add tests/phase8/test_shared_hardening.py; bump gem5 submodule |
| `1ae8c4a` | M8: bump gem5 submodule (ackNode boundary check relocation) |
| `6e966e6` | M8 fix: bump gem5 submodule for ackNode bounds |

---

## 3. Deviations from Original Plan

### 3.1 Alignment with `plan/03-phase-plan.md`

| Planned | Actual | Notes |
|---|---|---|
| Multi-sharer mask correctly maintained | Done | `sharersMask` (64-bit) updated atomically on grant/add/remove/invalidate |
| Local upgrade hits external sharer → `GlobalInvalidate` | Done | Home UBCC detects `G_S` + `GlobalReadUnique` → sends `GlobalInvalidate` to each sharer |
| Remote sharer ack collected → local unique completes | Done | `pendingInvalidations` set; `processInvalidationAck()` decrements; grant only after all acks |
| Shared path default enabled | Done | `GlobalReadShared` → `GlobalGrantShared`; no unconditional `GrantM` reroute |
| `force_grant_m` debug only | Done | Retained but not the default; MESI-correct path is primary |
| Two requesters simultaneous shared | Done | Both in sharers mask; both receive `GrantShared` |
| Upgrade properly invalidates other sharers | Done | Invalidation flow proven in M8SelfTest subscenarios |

### 3.2 Key Design Decisions

| Decision | Rationale |
|---|---|
| Home epoch for invalidation | Prevents stale invalidation acks from contaminating a new transaction on the same line |
| Idempotent invalidation ack (no-op reentry) | If a retried/duplicate ack arrives for an already-counted sharer, it's accepted as no-op — prevents deadlock from message replay |
| `ackNode` boundary check at entry | Protects against out-of-range node IDs in `sharersMask`; checked before all early-returns to catch bugs early |
| `G_S` + `GlobalReadUnique` → invalidation before grant | Serializes the upgrade: first invalidate all sharers, then grant exclusive/modified to the requester |
| SharerMask auto-cleanup on empty | When last sharer is removed (via evict or invalidate), the directory entry is cleaned up |

### 3.3 GlobalInvalidate Flow

```
Requester sends GlobalReadUnique → home UBCC (in G_S state)
  → home marks line G_BUSY, sets pendingOp=INVALIDATE
  → home computes targets = sharersMask & ~(1 << requester)
  → for each target: send GlobalInvalidate
    → target EPBackend.handleInvalidate()
      → EP_RNF invalidates local copy
      → sends ack with home epoch
    → home processInvalidationAck(requester, epoch)
      → validate epoch matches current transaction
      → remove sharer from pendingInvalidations set
      → if pendingInvalidations empty: complete grant
  → grant GlobalGrantExclusive/Modified to requester
  → update directory: state = G_E or G_M, sharersMask = 0
```

### 3.4 Scope Boundaries

| In Scope (Implemented) | Not Yet Implemented |
|---|---|
| Multi-sharer shared access | — |
| Upgrade → Invalidate → Grant | — |
| Shared path default | — |
| SharerMask full lifecycle | — |
| AckNode bounds protection | — |

### 3.5 Consistency with `plan/02-external-proxy-spec.md`

| Spec Requirement | Implementation | Status |
|---|---|---|
| Local upgrade invalidates external sharers (§7.3) | Home detects `G_S` + `GlobalReadUnique` → `GlobalInvalidate` → wait for acks → grant | PASS |
| Remote sharer ack collection before unique (§7.3) | `pendingInvalidations` set; grant gated on all acks received | PASS |
| Shared path default enabled (§9.1) | `GlobalReadShared` → `GlobalGrantShared` | PASS |
| Multi-sharer mask maintenance (§9.1) | 64-bit `sharersMask` with bit-add/remove operations | PASS |

---

## 4. Test Cases

### 4.1 TC-M8-1: Two Requesters Hold Shared

| Attribute | Value |
|---|---|
| **ID** | TC-M8-1 (M8-1-1 through M8-1-9) |
| **Name** | Two Requesters Hold Shared |
| **Type** | PY_INJECT (C++ self-test) |
| **Assertions** | 9 |
| **Expected** | First requester added to sharers mask, receives Shared grant; Second requester also added, receives Shared grant; both in mask simultaneously; directory state stays `G_S`; no owner; dirty=false |
| **Actual** | PASS |
| **Negative** | Not in exclusive/modified state; no owner field set |

### 4.2 TC-M8-2: Local Upgrade Invalidates Other Sharers

| Attribute | Value |
|---|---|
| **ID** | TC-M8-2 (M8-2a-1..2, M8-2b-1..14, M8-2c-1..8) |
| **Name** | Local Upgrade Invalidates Other Sharers |
| **Type** | PY_INJECT (C++ self-test) |
| **Assertions** | 24 |
| **Expected** | Sub-scenario 2a: structural invalidation path exists. Sub-scenario 2b: full upgrade flow — shared→Unique triggers `GlobalInvalidate` to other sharers, acks collected, line transitions to `G_E`/`G_M`, old sharers removed from mask. Sub-scenario 2c: post-upgrade state verification — new owner has exclusive access, old sharers invalidated, sharer mask empty after invalidation |
| **Actual** | PASS |
| **Negative** | Old sharers do not retain access; no premature grant before acks |

### 4.3 TC-M8-3: Shared Default Path

| Attribute | Value |
|---|---|
| **ID** | TC-M8-3 (M8-3-1 through M8-3-7) |
| **Name** | Shared Default Path |
| **Type** | PY_INJECT (C++ self-test) |
| **Assertions** | 7 |
| **Expected** | `GlobalReadShared` request produces `GrantShared`; line goes to `G_S`; no `force_grant_m` bypass; default configuration uses MESI-correct path; `GlobalReadShared` ≠ `GrantModified`; `GrantShared` is distinct from `GrantExclusive` and `GrantModified` |
| **Actual** | PASS |
| **Negative** | Shared request does NOT produce Modified grant under default config |

### 4.4 TC-M8-4: SharerMask Correctness

| Attribute | Value |
|---|---|
| **ID** | TC-M8-4 (M8-4a, 4b, 4c, 4d series) |
| **Name** | SharerMask Correctness |
| **Type** | PY_INJECT (C++ self-test) |
| **Assertions** | 10 |
| **Expected** | Sub-scenario 4a: add single sharer, mask has correct bit; 4b: add multiple sharers, all bits set; 4c: evict sharer, bit cleared, mask shrinks; 4d: all sharers evicted, mask empty → entry removed |
| **Actual** | PASS |
| **Negative** | No stale bits remain after eviction; empty mask triggers cleanup |

### 4.5 Additional Self-Test Checks

| Test Group | Checks | Purpose |
|---|---|---|
| M8-5 (busy line during invalidation) | 3 | `G_BUSY` is set during invalidation, cleared after |
| M8-6 (invalidation ack sent counter) | 1 | Counter initialized and incremented |
| M8-7 (pending invalidation lifecycle) | 4 | Pending invalidations tracked, active during transaction, cleared after completion, new request can proceed on freed line |
| M8-REENTRY | 3 | Duplicate ack accepted (no-op); idempotent reentry working |

### 4.6 Summary

| Test Group | Checks | PASS | FAIL | SKIP |
|---|---|---|---|---|
| TC-M8-1 (Two requesters shared) | 9 | 9 | 0 | 0 |
| TC-M8-2 (Upgrade invalidates) | 24 | 24 | 0 | 0 |
| TC-M8-3 (Shared default path) | 7 | 7 | 0 | 0 |
| TC-M8-4 (SharerMask correctness) | 10 | 10 | 0 | 0 |
| M8-5 (Busy line) | 3 | 3 | 0 | 0 |
| M8-6 (Ack counter) | 1 | 1 | 0 | 0 |
| M8-7 (Pending invalidation) | 4 | 4 | 0 | 0 |
| M8-REENTRY (Idempotent ack) | 3 | 3 | 0 | 0 |
| **Total** | **61** | **61** | **0** | **0** |

---

## 5. Regression Results

| Test | Status | Notes |
|---|---|---|
| TC1–TC5 | Pre-existing PASS | Unaffected |
| M4 Self-Test (within M8) | 0 FAIL | No regression |
| M5 Self-Test (within M8) | 0 FAIL | No regression |
| M6 Self-Test (within M8) | 0 FAIL | No regression |
| M7 Self-Test (within M8) | 0 FAIL | No regression |
| M8 Self-Test | 0 FAIL | All 61 checks pass |

> M8 test harness (`test_shared_hardening.py`) includes cumulative regression detection for M4/M5/M6/M7. Any FAIL in any prior stage blocks the M8 gate. All stages consistently pass.

---

## 6. Incomplete / TODO

| Item | Status | Notes |
|---|---|---|
| ARM_SYNC end-to-end workload for multi-sharer | Not yet | M8 uses PY_INJECT (C++ self-test); ARM workload would validate timing under real CHI protocol paths |
| Metadata model (M9) | Deferred to M9 | Capacity model, outer protocol ABI abstraction |
| Multi-gem5 preparation (M9) | Deferred to M9 | Multi-instance deployment assumptions |

### 6.1 Known Limitations

1. **GlobalInvalidate** sends invalidations sequentially to each sharer in the single-gem5 prototype. In a real multi-gem5 deployment, invalidation would be broadcast across the outer network.
2. **Ack collection** uses an in-process `pendingInvalidations` set. For real hardware, a timeout or retransmission mechanism would be needed.
3. **`force_grant_m`** debug flag is still present; it is not the default but could be accidentally enabled.
4. **SharerMask** is 64-bit — adequate for N=3 but may need extension for very large node counts.

### 6.2 Later Stage Backfill

| Item | Target Stage | Priority |
|---|---|---|
| ARM_SYNC workload E2E for multi-sharer scenarios | Post-M8 | P1 |
| Outer protocol ABI abstraction | M9 | P2 |
| Metadata capacity model | M9 | P2 |
| Multi-gem5 / ns-3 time assumptions | M9 | P3 |
| Invalidation broadcast optimization | Post-M9 | P3 |

---

## 7. Submodule State

| Attribute | Value |
|---|---|
| gem5 submodule changed | Yes |
| gem5 Fix Round commit | `4a9a672335` (P0-1/P0-2/P1-4 + M8SelfTest) |
| gem5 ackNode relocation commit | `ad782435d6` (move boundary check to entry) |
| gem5 final commit | `d1f6ec4947` (move boundary check before directory lookup) |
| superproject final commit | `6e966e6` (M8 fix: bump gem5 submodule) |

---

## 8. Build & Test Command Chain

```bash
# Build gem5
docker run --rm -v $(pwd):/workspace -w /workspace/gem5 \
    ubcc-dev:ubuntu20.04 bash -c "scons build/ARM/gem5.opt -j20 PROTOCOL=CHI"

# Run M8 tests (includes M4/M5/M6/M7 regression)
docker run --rm -v $(pwd):/workspace -w /workspace \
    ubcc-dev:ubuntu20.04 bash -c \
    "./gem5/build/ARM/gem5.opt tests/phase8/test_shared_hardening.py <arm_binary>"

# Expected: EXIT CODE 0, M8_SELF_TEST_PASSED=1,
#           M4:0 FAIL, M5:0 FAIL, M6:0 FAIL, M7:0 FAIL, M8:0 FAIL
```
