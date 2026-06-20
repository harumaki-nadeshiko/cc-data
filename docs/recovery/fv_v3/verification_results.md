# Simulation + Fault Injection + Formal Verification Results

## Part A: Runtime Invariant Checker

**Status**: ✅ Implemented + Verified

**Implementation**:
- `UBCCController.hh/cc` — `validateEpochMonotonic()`, `validateSharersCanonical()`
- 8 call sites at every `_directory.update()` and epoch write
- Debug flag: `--debug-flags=UBInvariant`

**Test Results** (TC1-7 with UBInvariant enabled):
```
TC1 PASSED  TC2 PASSED  TC3 PASSED  TC4 PASSED
TC5 PASSED  TC6 PASSED  TC7 PASSED
```
No invariant violations detected. All 3 invariants (epoch monotonicity, sharersMask canonical, no double commit) hold.

**Invariants verified**:
| Invariant | Check | Result |
|-----------|-------|--------|
| Epoch monotonicity | `newEpoch >= oldEpoch` at every write | ✅ No violations |
| SharersMask canonical | G_I→sharers=0, G_S→sharers≠0, G_E/G_M→popcount=1 | ✅ No violations |
| No double commit | Per-PA commit counter ≤ 1 | ✅ No violations |

---

## Part B: Fault Injection

**Status**: ⚠️ Implemented, needs Python wiring fix

**Implementation**:
- `UBRouter.hh/cc` — `DebugFaultRule`, `applyFaultRules()`, `parseFaultRules()`
- Actions: Drop (return copies=0), Duplicate (copies=2), Delay (framework, partial)
- `UBRouter.py` — `fault_rules` VectorParam
- TC47-49 workloads created

**Build**: Compiles clean (after removing `#ifndef NDEBUG` guards)

**Known issue**: `root.descendants()` in `test_e2e.py` line 1497 does NOT find UBRouter SimObjects. The UBRouter objects are created in `CHI_ubcc_framework.py` but may not be parented under `root`. Fix needed: ensure UBRouter objects are `root`-accessible from m5's SimObject tree.

**TC47-49 workloads** (can run without fault injection):
- TC47: drop ClearReq → should trigger tombstone replay
- TC48: duplicate InvalidateAck → idempotent handling
- TC49: dropped ack → retry/reorder

Once Python wiring is fixed, these TCs will exercise fault recovery paths.

---

## Part C: TLA+ Formal Model

**Status**: ✅ Created

**Model location**: `docs/recovery/fv_v3/ubcc_protocol.tla`

**Model scope**:
- States: MESI (G_I, G_S, G_E, G_M) × OutstandingRequest(OpType, OpStage) × epoch × sharersMask
- 14 actions covering: GrantShared, GrantExclusive/Modified, InvalidateForUnique, RecallForShared/Unique, SelfOwnerGrant, RecallResponse, InvalidationAck, Clear, DuplicateClearReplay, UpgradeReq, UpgradeAck, UpgradeDone, TickAdvance

**Invariants specified**:
1. `NoDoubleCommit` — committed epoch increments by exactly 1 per commit
2. `EpochMonotonic` — epoch never decreases
3. `SharersCanonical` — G_I→0, G_S→≠0, G_E/G_M→popcount=1
4. `ReserveNotCommit` — reserved epoch may be larger but never equals committed epoch concurrently
5. `CommitOnlyOnAuthorizedPath` — commit only via Clear or UpgradeDone

**TLC check**: Not yet run (`tlc` not installed in this environment).
Install: `wget https://github.com/tlaplus/tlaplus/releases/download/v1.8.0/tla2tools.jar`
Run: `java -cp tla2tools.jar tlc2.TLC -config ubcc_config.cfg ubcc_protocol.tla`

---

## Summary

| Part | Status | Key Finding |
|------|--------|-------------|
| A: Runtime invariants | ✅ Done | 7 TCs pass, no violations |
| B: Fault injection | ⚠️ Blocked | Code ready, Python wiring needs fix |
| C: TLA+ model | ✅ Created | 14 actions, 5 invariants, needs `tlc` to check |

---

## Future Work (Q4=B mandatory)

After Part C TLC verification passes:
1. Create `ubcc_transport_faults.tla` — add nondet message pool with drop/dup/reorder
2. Verify tombstone replay consistency under message loss
3. Verify stale-epoch rejection under reorder
4. Verify that `RECALL.DONE + WB/EVICT` race does NOT result in directory corruption
