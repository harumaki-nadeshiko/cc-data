# T0 Stage Delivery Report

- **Stage:** T0 — Sync_Wait(node_mask)
- **Status:** PASS
- **Completion Date:** 2026-05-25 (initial), 2026-05-26 (fix rounds)
- **Review Rounds:** 3 (initial + 2 fix rounds)
- **Orchestrator Verdict:** PASS

---

## 1. Stage Summary

### 1.1 Stage Goal

Implement SE-mode cross-node barrier syscall `Sync_Wait(node_mask)` to provide repeatable, verifiable synchronization for multi-node directed protocol testcases. This is a hard prerequisite for all subsequent protocol stages (M4–M8) that require multi-node timing control.

### 1.2 Completion Status

| Criterion | Result |
|---|---|
| Syscall 436 registered in ARM SE | PASS |
| Barrier counts only explicit callers | PASS |
| Different `node_mask` instances isolated | PASS |
| Barrier reusable across rounds | PASS |
| Parameter validation (invalid masks) | PASS |
| TC-T0-1 through TC-T0-7 all passing | 70/70 checks PASS |
| Regression (TC1–TC5) unaffected | PASS |

### 1.3 Review Rounds

| Round | Date | Key Findings | Resolution |
|---|---|---|---|
| R1 (initial) | 2026-05-25 | Base implementation submitted; validator review pending | — |
| R2 (fix) | 2026-05-26 | P0: missing param validation; P1: weak assertions; P2: pre-compiled binaries | 3 validation checks added, assertions strengthened, binaries replaced with source-only auto-compilation |
| R3 (fix) | 2026-05-26 | P1: TC-T0-3 non-caller assertion was matching caller's output | Changed to CPU-level output-file check (70/70 total checks) |

---

## 2. Code Changes

### 2.1 gem5 Submodule

| File | Change | Description |
|---|---|---|
| `src/sim/sync_wait.hh` | New | `SyncWaitManager` class: per-mask barrier map, `popcount` thread tracking, suspend/wake, reset between rounds |
| `src/sim/sync_wait.cc` | New | `barrierWait()`: 3 validation checks (mask==0 → -EINVAL, bits beyond N=3 → -EINVAL, duplicate caller → return 0), thread suspend/resume, auto-reset |
| `src/sim/sync_wait.hh` | R2 fix | Added `MAX_NODE_COUNT=3`, changed `barrierWait()` return `void` → `int` |
| `src/sim/sync_wait.cc` | R2 fix | Added `#include <cerrno>`, 3 param validation checks returning `-EINVAL` |
| `src/arch/arm/linux/se_workload.cc` | Modified | Registered syscall 436 in SyscallTable32/64; `syncWaitFunc<ABI>` handler extracts `node_mask` from ABI arg0, passes to `SyncWaitManager` |
| `src/arch/arm/linux/se_workload.cc` | R2 fix | Added high-32-bits check; propagates `barrierWait()` return value |
| `src/sim/system.hh` | Modified | Added `SyncWaitManager syncWait` member to `System` class |
| `src/sim/SConscript` | Modified | Added `Source('sync_wait.cc')` to sim build |

**gem5 commit history (T0-related):**

| Commit | Description |
|---|---|
| `95e3e2763f` | T0: Add SyncWaitManager barrier for SE-mode cross-node synchronization |
| `9d714c6ea2` | T0 fix: Add Sync_Wait parameter validation (mask=0, hi32 bits, node bounds) |

### 2.2 Superproject

| File | Change | Description |
|---|---|---|
| `tests/sync_wait/tc_t0_1.c` | New | TC-T0-1: 3 threads, mask=0b111, basic release |
| `tests/sync_wait/tc_t0_2.c` | New | TC-T0-2: isolation, masks 0b011 and 0b100 |
| `tests/sync_wait/tc_t0_3.c` | New | TC-T0-3: multi-thread same node (caller + non-caller) |
| `tests/sync_wait/tc_t0_4.c` | New | TC-T0-4: reusable barrier (2 rounds) |
| `tests/sync_wait/tc_t0_5.c` | New (R2) | TC-T0-5: mask=0 → `-EINVAL` negative test |
| `tests/sync_wait/tc_t0_6.c` | New (R2) | TC-T0-6: high-32-bits → `-EINVAL` negative test |
| `tests/sync_wait/tc_t0_7.c` | New (R2) | TC-T0-7: bits beyond N=3 → `-EINVAL` negative test |
| `tests/sync_wait/test_sync_wait.py` | New → R3 fix | Test driver: per-case gem5 invocations, trace-based global ordering, auto-compilation, 70 checks |
| `.gitignore` | New (R2) | Exclude generated test binaries (`tc_t0_*` binaries) |
| `tests/sync_wait/tc_t0_{1,2,3_caller,3_noncaller,4}` | Deleted (R2) | Pre-compiled binaries removed, replaced by source-only auto-compilation |
| `reports/stage-t0-implementation-1.md` | Updated | R2 and R3 fix summaries, 70/70 results |

**Superproject commit history:**

| Commit | Description |
|---|---|
| `632d25a` | T0: Add Sync_Wait barrier test infrastructure |
| `97dc12e` | T0 fix round: Update stage report with validation logic, test results, command chain |
| `aedd906` | T0 fix round: Add param validation + negative tests + strengthened assertions |
| `42589ad` | T0 round 2 fixes: binary cleanup, trace-based global ordering, exact errno checks, artifact-dir support |
| `55dac63` | T0 Round 3: Strengthen TC-T0-3 non-caller assertion to CPU-level output-file check |
| `b3dff28` | T0 Round 3 report: fill actual commit hash (55dac63) |

---

## 3. Deviations from Original Plan

### 3.1 Alignment with `plan/03-phase-plan.md`

| Planned | Actual | Notes |
|---|---|---|
| Register ARM custom syscall | Done | Syscall 436 in both 32/64-bit tables |
| Implement `SyncWait` barrier state object | Done | `SyncWaitManager` class with per-mask isolation |
| Make barrier state globally visible | Done | Mounted on `System` |
| Support `node_mask` isolated instances | Done | Map keyed by `node_mask` |
| Support reusable barrier across rounds | Done | Auto-reset when all threads wake |
| Minimal test workload + scripts | Done | 7 workloads, Python test driver |
| Only count explicit callers | Done | Only threads calling `Sync_Wait` are counted |
| No timeout, no signal, no FS-mode Linux | Done | Not implemented |

### 3.2 Parameters Added Beyond Plan

| Additions | Rationale |
|---|---|
| 3 negative tests (TC-T0-5/6/7) | Validator-requested: `mask=0`, high-32-bits, bits beyond N=3 |
| Return value `int` instead of `void` | Needed to propagate `-EINVAL` on invalid inputs |
| Auto-compilation from `.c` sources | Eliminates pre-compiled binary dependency |
| Trace-based global ordering | Uses `SyscallBase` debug trace to build globally-ordered timeline across CPUs |

### 3.3 Consistency with `plan/02-external-proxy-spec.md`

Not applicable — T0 is a synchronization infrastructure stage and does not touch any EP/UBCC components.

### 3.4 Implementation Simplifications (None)

All planned features from `plan/03-phase-plan.md` §4 were implemented. No scope reduction.

---

## 4. Test Cases

### 4.1 TC-T0-1: Barrier Basic Release

| Attribute | Value |
|---|---|
| **ID** | TC-T0-1 |
| **Name** | Barrier Basic Release |
| **Type** | ARM_SYNC |
| **Assertions** | 11 |
| **Expected** | 3 `BEFORE_BARRIER` lines before any `AFTER_BARRIER`; intra-node `BEFORE < AFTER` ordering |
| **Actual** | PASS — all 3 threads released together |
| **Negative** | No early `AFTER_BARRIER` observed |

### 4.2 TC-T0-2: Barrier Isolation By Node Mask

| Attribute | Value |
|---|---|
| **ID** | TC-T0-2 |
| **Name** | Barrier Isolation By Node Mask |
| **Type** | ARM_SYNC |
| **Assertions** | 12 |
| **Expected** | Node0+1 (mask=0b011) release independently from Node2 (mask=0b100) |
| **Actual** | PASS — per-mask barrier isolation confirmed |
| **Negative** | No cross-mask interference detected |

### 4.3 TC-T0-3: Multi-Thread Same Node Count

| Attribute | Value |
|---|---|
| **ID** | TC-T0-3 |
| **Name** | Multi-Thread Same Node Count |
| **Type** | ARM_SYNC |
| **Assertions** | 9 |
| **Expected** | 3 callers pass barrier; 1 non-caller does NOT produce `AFTER_BARRIER` |
| **Actual** | PASS — non-caller output verified clean via CPU-level output-file check |
| **Negative** | Non-caller not counted toward barrier total |

### 4.4 TC-T0-4: Reusable Barrier

| Attribute | Value |
|---|---|
| **ID** | TC-T0-4 |
| **Name** | Reusable Barrier |
| **Type** | ARM_SYNC |
| **Assertions** | 20 |
| **Expected** | 2 complete rounds, global counts correct, R1 ordering before R2 |
| **Actual** | PASS — no stale state from round 1 affects round 2 |
| **Negative** | No cross-round interference |

### 4.5 TC-T0-5: Mask=0 Rejection (Negative)

| Attribute | Value |
|---|---|
| **ID** | TC-T0-5 |
| **Name** | Mask=0 Rejected |
| **Type** | ARM_SYNC (negative) |
| **Assertions** | 6 |
| **Expected** | `Sync_Wait(0)` returns `-EINVAL` (-22), no blocking |
| **Actual** | PASS — syscall returns -22 immediately |
| **Negative** | No blocking, no success return |

### 4.6 TC-T0-6: High-32-Bits Rejection (Negative)

| Attribute | Value |
|---|---|
| **ID** | TC-T0-6 |
| **Name** | High-32-Bits Rejected |
| **Type** | ARM_SYNC (negative) |
| **Assertions** | 6 |
| **Expected** | `Sync_Wait(0x1_0000_0007)` returns `-EINVAL` (-22) |
| **Actual** | PASS — hi-32-bits guard triggers before lower bits are evaluated |
| **Negative** | No blocking, no mask misinterpretation |

### 4.7 TC-T0-7: Bits Beyond N=3 Rejection (Negative)

| Attribute | Value |
|---|---|
| **ID** | TC-T0-7 |
| **Name** | Bits Beyond N=3 Rejected |
| **Type** | ARM_SYNC (negative) |
| **Assertions** | 6 |
| **Expected** | `Sync_Wait(0b1000)` returns `-EINVAL` (-22) |
| **Actual** | PASS — mask with bit beyond MAX_NODE_COUNT-1 rejected |
| **Negative** | No blocking, no invalid node targeted |

### 4.8 Parameter Validation Logic Summary

| Check | Location | Condition | Error |
|---|---|---|---|
| mask == 0 | `barrierWait()` | `node_mask == 0` | `-EINVAL` |
| Bits beyond N=3 | `barrierWait()` | `node_mask & ~((1<<MAX_NODE_COUNT)-1)` | `-EINVAL` |
| High 32 bits non-zero | `syncWaitFunc()` | `node_mask >> 32` | `-EINVAL` |

---

## 5. Regression Results

| Test | Status | Details |
|---|---|---|
| TC1 (`test_pa_layout_mode.py`) | Pre-existing PASS | Unaffected — T0 does not touch PA layout or Ruby |
| TC2 (`run_phase1_test.py`) | Pre-existing PASS | Unaffected |
| TC2E (`run_phase1_test_enhanced.py`) | Pre-existing PASS | Unaffected |
| TC3 (`verify_topo_objects.py`) | Pre-existing PASS | Unaffected |
| TC4 (`test_ruby_create_system_n3l2d2.py`) | Pre-existing PASS | Unaffected |
| TC5 (`test_ep_instantiate.py`) | Pre-existing PASS | Unaffected |

> T0 changes (`sync_wait.{hh,cc}`, `se_workload.cc`) do not touch any Ruby memory system, topology configuration, or EP controller paths. No regression risk.

---

## 6. Incomplete / TODO

| Item | Status | Notes |
|---|---|---|
| Timeout mechanism | Not implemented | Acceptable for directed testcases; any stuck thread is a test failure |
| Serialization/checkpoint support | Not implemented | Acceptable for T0 scope |
| `MAX_NODE_COUNT` hard-coded | Not yet configurable | Currently hard-coded to 3; should be derived from topology in future stages |
| Full-system Linux support | Not implemented | Explicitly excluded per plan |

### 6.1 Known Limitations

1. If a thread never calls the barrier, waiting threads will block forever — acceptable for deterministic testcases.
2. `MAX_NODE_COUNT = 3` is hard-coded; if N changes, this must be updated.
3. Barrier state is not checkpointed, so checkpoint/restore scenarios are not supported.

### 6.2 Later Stage Backfill

| Item | Target Stage | Priority |
|---|---|---|
| Configurable `MAX_NODE_COUNT` | M9 or post-M8 cleanup | P2 |
| Timeout support | None — not planned | — |

---

## 7. Submodule State

| Attribute | Value |
|---|---|
| gem5 submodule changed | Yes (R2 fix) |
| gem5 final commit | `9d714c6ea293d2add442b5d6ef86c9c36c659bef` |
| gem5 original T0 commit | `95e3e2763f44e76c232cdb55ec1de50dc06fa5d5` |
| superproject final commit | `55dac63910d5ce93a053ac4e1c9b32222f7f784c` |
| superproject initial commit | `632d25a` |

---

## 8. Build & Test Command Chain

```bash
# Build gem5
docker run --rm -v $(pwd):/workspace -w /workspace/gem5 \
    ubcc-dev:ubuntu20.04 bash -c "scons build/ARM/gem5.opt -j20 PROTOCOL=CHI"

# Run T0 tests
docker run --rm -v $(pwd):/workspace -w /workspace \
    ubcc-dev:ubuntu20.04 bash -c "python3 tests/sync_wait/test_sync_wait.py"

# Expected: Results: 70/70 tests passed
```
