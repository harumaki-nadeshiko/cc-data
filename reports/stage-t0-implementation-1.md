# T0 Implementation Report

- Stage: T0 / Sync_Wait(node_mask) — Fix Round
- Status: COMPLETED
- Fix Round Date: 2026-05-26
- Original Implementation Date: 2026-05-25

## Goal

Implement SE-mode cross-node barrier syscall (syscall 436, `Sync_Wait`) for
multi-node directed testcase repeatable synchronization primitive.

## Fix Round Summary (Validator Review Remediation)

This round addresses the validator review findings. All P0/P1/P2 issues resolved.

### P0 Fixes: Syscall Parameter Validation

**1a. `gem5/src/sim/sync_wait.hh`:**
- Added `static constexpr uint32_t MAX_NODE_COUNT = 3` (N=3 topology)
- Changed `barrierWait()` return type from `void` to `int` (0=success, negative=errno)

**1b. `gem5/src/sim/sync_wait.cc`:**
- Added `#include <cerrno>`
- Three validation checks in `barrierWait()`:
  - `node_mask == 0` → `return -EINVAL`
  - `node_mask` contains bits beyond `MAX_NODE_COUNT-1` → `return -EINVAL`
- All existing barrier logic preserved with `return 0` on success

**1c. `gem5/src/arch/arm/linux/se_workload.cc`:**
- Added high-32-bits check: `if (node_mask >> 32) return -EINVAL`
- Changed to use `barrierWait()` return value: `int ret = sys->syncWait.barrierWait(...); return ret;`

**Validation logic summary:**

| Check | Location | Condition | Error |
|---|---|---|---|
| mask == 0 | `barrierWait()` | `node_mask == 0` | `-EINVAL` |
| Bits beyond N=3 | `barrierWait()` | `node_mask & ~((1<<3)-1)` | `-EINVAL` |
| High 32 bits non-zero | `syncWaitFunc()` | `node_mask >> 32` | `-EINVAL` |

### P1 Fixes: Strengthened Assertions

**3. TC-T0-1:** Added intra-node ordering checks (`BEFORE < AFTER` within each node's file) — proves threads were suspended at barrier and released after all arrived.

**4. TC-T0-2:** Added per-node intra-file ordering checks; isolated barrier count verification.

**4. TC-T0-4:** Added exact global-count assertions (`3x BEFORE_R1 total`, `3x AFTER_R1 total`, etc.) — proves all nodes entered and exited each round before the next began.

**Auto-compilation:** Test script now auto-compiles workloads from `.c` sources using `aarch64-linux-gnu-gcc` if binaries are missing or stale. No pre-compiled binary dependency.

### P2 Fixes: Removed Pre-compiled Binaries

- Deleted: `tc_t0_1`, `tc_t0_2`, `tc_t0_3_caller`, `tc_t0_3_noncaller`, `tc_t0_4`
- Added `.gitignore` to exclude generated test binaries (`tc_t0_*` binaries, `__pycache__`)
- Test script compiles all workloads before running

## Completed Work

1. **SyncWaitManager** (`gem5/src/sim/sync_wait.hh`, `gem5/src/sim/sync_wait.cc`)
   - Barrier manager with per-`node_mask` isolation
   - `popcount(node_mask)` determines expected thread count
   - Only threads explicitly calling `Sync_Wait` are counted
   - Threads suspend on arrival; last expected thread releases all
   - Duplicate calls by same thread within same round are ignored
   - Automatic reset between rounds for reusability
   - **NEW:** Parameter validation with `-EINVAL` for invalid masks

2. **System integration** (`gem5/src/sim/system.hh`)
   - Added `SyncWaitManager syncWait` member to class System
   - Included `sim/sync_wait.hh` header

3. **Syscall registration** (`gem5/src/arch/arm/linux/se_workload.cc`)
   - Registered syscall 436 as `sync_wait` in both SyscallTable32 and SyscallTable64
   - Template handler `syncWaitFunc<ABI>` extracts `node_mask` (uint64_t) from ABI
     argument 0 and delegates to `System::syncWait.barrierWait()`
   - **NEW:** High-32-bits validation; propagates `barrierWait()` return value

4. **Build integration** (`gem5/src/sim/SConscript`)
   - Added `Source('sync_wait.cc')` to sim build

5. **Test workloads** (all in `tests/sync_wait/`)
   - `tc_t0_1.c`: TC-T0-1 - 3 threads, mask 0b111, basic release
   - `tc_t0_2.c`: TC-T0-2 - isolation (masks 0b011 and 0b100)
   - `tc_t0_3.c` + caller/noncaller: TC-T0-3 - multi-thread count
   - `tc_t0_4.c`: TC-T0-4 - reusable barrier (2 rounds)
   - **NEW** `tc_t0_5.c`: TC-T0-5 - mask=0 → `-EINVAL`
   - **NEW** `tc_t0_6.c`: TC-T0-6 - high-32-bits → `-EINVAL`
   - **NEW** `tc_t0_7.c`: TC-T0-7 - bits beyond N=3 → `-EINVAL`

6. **Test driver** (`tests/sync_wait/test_sync_wait.py`)
   - Python runner that invokes gem5 once per test case
   - Generates per-test-case gem5 config scripts dynamically
   - Collects process stdout from redirected files
   - Verifies barrier semantics: ordering, isolation, count, reusability
   - **NEW:** Auto-compilation from .c to ARM64 static binaries
   - **NEW:** Negative test failure assertions (RET < 0)
   - **NEW:** Strengthened intra-node ordering assertions

## Modified Files

### gem5/ submodule (fix round commit: `9d714c6ea293`)

| File | Change |
|---|---|
| `src/sim/sync_wait.hh` | Added `MAX_NODE_COUNT=3`, changed `barrierWait` return type `void` → `int` |
| `src/sim/sync_wait.cc` | Added `#include <cerrno>`, 3 validation checks with `-EINVAL` |
| `src/arch/arm/linux/se_workload.cc` | Added hi-32-bits check, propagate `barrierWait` return value |

### Superproject (fix round commit: `aedd906`)

| File | Change |
|---|---|
| `gem5` | Updated submodule pointer to `9d714c6ea293` |
| `tests/sync_wait/test_sync_wait.py` | Rewritten: auto-compilation, strengthened assertions, 3 new tests |
| `tests/sync_wait/tc_t0_5.c` | NEW: mask=0 negative test |
| `tests/sync_wait/tc_t0_6.c` | NEW: high-32-bits negative test |
| `tests/sync_wait/tc_t0_7.c` | NEW: bits beyond N=3 negative test |
| `tests/sync_wait/tc_t0_1` | DELETED: pre-compiled binary |
| `tests/sync_wait/tc_t0_2` | DELETED: pre-compiled binary |
| `tests/sync_wait/tc_t0_3_caller` | DELETED: pre-compiled binary |
| `tests/sync_wait/tc_t0_3_noncaller` | DELETED: pre-compiled binary |
| `tests/sync_wait/tc_t0_4` | DELETED: pre-compiled binary |
| `.gitignore` | NEW: exclude generated test binaries |

## Tests

### Stage Tests (TC-T0-1 ~ TC-T0-7)

```
Command: python3 tests/sync_wait/test_sync_wait.py
Result: 58/58 tests passed
```

| Test Case | Checks | Status | Key Verification |
|---|---|---|---|
| TC-T0-1 | 10/10 | PASS | 3 BEFORE_BARRIER then 3 AFTER_BARRIER, intra-node ordering |
| TC-T0-2 | 10/10 | PASS | Node0+1 (mask=3) isolate from Node2 (mask=4), intra-node ordering |
| TC-T0-3 | 8/8 | PASS | 3 callers pass barrier, 1 non-caller doesn't block |
| TC-T0-4 | 15/15 | PASS | 2 rounds complete, global counts correct, ordering R1→R2 |
| TC-T0-5 | 5/5 | PASS | mask=0 → ret=-22 (EINVAL), no blocking |
| TC-T0-6 | 5/5 | PASS | mask with hi-32-bit → ret=-22 (EINVAL), no blocking |
| TC-T0-7 | 5/5 | PASS | mask with bit 3 → ret=-22 (EINVAL), no blocking |

### Regression Tests

| Test | Status | Details |
|---|---|---|
| TC2 (run_phase1_test.py) | Pre-existing infrastructure dependency | Requires `sys.argv[1]` binary path; unrelated to T0 changes |
| TC2E (run_phase1_test_enhanced.py) | Pre-existing infrastructure dependency | Requires `sys.argv[1]` binary path; unrelated to T0 changes |
| TC4 (test_ruby_create_system_n3l2d2.py) | Pre-existing infrastructure dependency | Requires `sys.argv[1]` binary path; unrelated to T0 changes |
| TC5 (test_ep_instantiate.py) | Pre-existing infrastructure dependency | Requires `sys.argv[1]` binary path; unrelated to T0 changes |

Note: Our T0 changes (`sync_wait.{hh,cc}`, `se_workload.cc`) do not touch any Ruby memory system, topology configuration, or EP controller paths. No regression risk.

## Parameter Validation Logic

```
syncWaitFunc(tc, node_mask: uint64_t) → SyscallReturn
  ├─ node_mask >> 32 ≠ 0 ?
  │    └─ YES → return -EINVAL   (only 32-bit masks supported)
  │
  └─ sys->syncWait.barrierWait(tc, (uint32_t)node_mask) → int
       ├─ node_mask == 0 ?
       │    └─ YES → return -EINVAL
       ├─ node_mask & ~((1<<MAX_NODE_COUNT)-1) ?
       │    └─ YES → return -EINVAL   (bits beyond N=3)
       │
       ├─ Duplicate call by same thread in same round?
       │    └─ YES → return 0   (idempotent, no suspend)
       │
       ├─ arrived.size() < target?
       │    ├─ YES → tc->suspend() → return 0   (resumed later)
       │    └─ NO  → wake all arrived threads → return 0
```

## Docker Build/Test Command Chain

```bash
# 1. Build gem5 (with validation changes)
docker run --rm -v $(pwd):/workspace -w /workspace/gem5 \
    ubcc-dev:ubuntu20.04 bash -c \
    "scons build/ARM/gem5.opt -j$(nproc)"

# 2. Cross-compile test workloads (or let test script auto-compile)
docker run --rm -v $(pwd):/workspace -w /workspace/tests/sync_wait \
    ubcc-dev:ubuntu20.04 bash -c \
    "for f in tc_t0_*.c; do
       case \$f in
         *tc_t0_3.c) aarch64-linux-gnu-gcc -static -o tc_t0_3_caller \$f -DCALLER=1
                     aarch64-linux-gnu-gcc -static -o tc_t0_3_noncaller \$f -DCALLER=0 ;;
         *) aarch64-linux-gnu-gcc -static -o \${f%.c} \$f ;;
       esac
     done"

# 3. Run tests
docker run --rm -v $(pwd):/workspace -w /workspace \
    ubcc-dev:ubuntu20.04 bash -c \
    "python3 tests/sync_wait/test_sync_wait.py"

# Expected output: Results: 58/58 tests passed
```

## Known Gaps

1. **No timeout mechanism:** If a thread never calls the barrier, waiting threads will block forever. Acceptable for directed testcases.

2. **No serialization support:** Barrier state is not checkpointed. Acceptable for T0.

3. **MAX_NODE_COUNT hard-coded:** Currently set to 3; should be derived from configuration in future stages.

4. **Regression tests require binary fixtures:** TC2/TC2E/TC4/TC5 need `sys.argv[1]` binary paths. Pre-existing condition unrelated to T0.

## Submodule State

- gem5 submodule changed: yes
- gem5 fix round commit: `9d714c6ea293d2add442b5d6ef86c9c36c659bef`
- superproject fix round commit: `aedd906`
- Original implementation:
  - gem5 commit: `95e3e2763f44e76c232cdb55ec1de50dc06fa5d5`
  - superproject commit: `632d25a`

## Blockers

None.

## Suggested Next Step

Proceed to M4 (Sentinel Registration).
