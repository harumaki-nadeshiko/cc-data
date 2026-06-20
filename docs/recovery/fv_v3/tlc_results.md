# TLC Model Checking Results

## ubcc_protocol.tla (Pure Directory Model)

**Status**: ✅ PASSED

| Metric | Value |
|--------|-------|
| States generated | 9,916 |
| Distinct states | 2,935 |
| State graph depth | 5 |
| Time | < 1 second |
| Deadlock | None detected |

**Invariants checked** (all passed):
- `NoDoubleCommit` — no double commit per (epoch, reqId)
- `EpochMonotonic` — committed epoch never decreases
- `SharersCanonical` — G_I→sharers=∅, G_S→sharers≠∅, G_E/G_M→|sharers|=1

**Configuration**: MaxEpoch=4, TombstoneWindow=10, Nodes={0,1,2}

## ubcc_transport_faults.tla (Transport Fault Model)

**Status**: ⚠️ Parse error (needs module restructuring)

Root cause: transport model has conflicting operator names with base model and minor syntax issues. The base model is verified; transport model needs a refactored module structure to separate concerns.

