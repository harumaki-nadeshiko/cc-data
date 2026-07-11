# TLA+ Model Fix Specification

> Date: 2026-07-11 | For implementation by futsu-guider agent

---

## Overview

Two TLA+ models need fixes identified during formal verification stress testing.
This document provides implementation-ready specifications.

---

## Fix 1: `ep_intra_node_single.tla` — CpuRetry + MaxTxn + Data Simplification

### 1.1 Background

File: `verification/tla/ep_intra_node_single.tla`

Two problems:
1. **State space explosion**: unbounded message queues + `\E gd \in DataV` non-deterministic
   data selection -> 4.5B+ states, never terminates
2. **Spurious deadlock**: `HnfDropStaleReq` (line 217-234) drops stale request but model has
   no `CpuRetry` action -> CPU stuck permanently in P_RS/P_RU/P_EVICT

### 1.2 Other model coverage assessment

| Model | Covers retry? | Covers concurrent? | Verdict |
|-------|--------------|-------------------|---------|
| `ep_intra_node.tla` | No | Yes (MaxTxn=3, 2 CPUs) | Different abstraction (no SNF/Backend/DRAM). Does NOT cover single's issues |
| `ubcc_protocol_core.tla` | Yes (RecallOrphanCleanup) | Yes (3 nodes, MaxEpoch=4) | Covers *logical equivalence class* of retry pattern, but at directory level, not CHI transaction level |
| `ubcc_liveness_nocleanup.tla` | No (deliberately removed) | N/A | Proves the pattern: "no recovery => deadlock". Confirms fix direction |
| `ubcc_transport_faults.tla` | Yes (full fault model) | Yes | Most complete retry coverage, but at UBCC level, not EP/CHI level |

**Conclusion**: `ep_intra_node_single` is the ONLY model with full 5-component CHI flow
(CPU -> HN-F -> SNF -> Backend -> EP-RNF -> DRAM). Its issues are NOT covered by other
models. Fixing it is necessary.

### 1.3 Fix P0: Add `CpuRetry(cpu)` action

**Specification**:

```tla
CpuRetry(cpu) ==
    /\ cpuState[cpu] \in {"P_RS", "P_RU", "P_EVICT"}
    /\ \* Re-issue the same type of request
       LET reqType == CASE cpuState[cpu] = "P_RS"    -> "RS"
                        [] cpuState[cpu] = "P_RU"    -> "RU"
                        [] cpuState[cpu] = "P_EVICT" -> "EVICT"
       IN reqQ' = Append(reqQ, [cpu |-> cpu, type |-> reqType])
    /\ UNCHANGED <<cpuState, cpuData, ...other vars...>>
```

**Key points**:
- Guard: CPU must be in a pending state (`P_RS`, `P_RU`, `P_EVICT`)
- Action: re-enqueue request into `reqQ` without changing `cpuState` (CPU stays pending)
- `cpuState` transitions out of pending only when HN-F completes the request (existing actions)
- This mirrors the real implementation's `_retryQueue` + timer mechanism in EPRNF/SNF

**Add to `Next`**:

```tla
Next == \/ \E cpu \in CPUs: CpuStore(cpu)
        \/ \E cpu \in CPUs: CpuLoad(cpu)
        \/ \E cpu \in CPUs: CpuRetry(cpu)   \* NEW
        \/ HnfAcceptReq
        \/ ... (rest unchanged)
```

**Add to `FairSpec`**:

```tla
\* Retry should be weakly fair per CPU (eventually retries if continuously enabled)
FairSpec == Spec /\ ... /\ \A cpu \in CPUs: WF_vars(CpuRetry(cpu))
```

### 1.4 Fix P1: Add `MaxTxn` transaction counter

**Specification**:

Add a variable `txnCount` that counts completed transactions. CPU actions
(`CpuStore`, `CpuLoad`) increment it. Add type constraint:

```tla
CONSTANT MaxTxn   \* Suggest MaxTxn = 3 (matches ep_intra_node.tla)

VARIABLE txnCount

TypeOK == ... /\ txnCount \in 0..MaxTxn

CpuStore(cpu) ==
    /\ txnCount < MaxTxn
    /\ ... (existing guard)
    /\ txnCount' = txnCount + 1
    /\ ...
```

This replaces the `QueueBounded` CONSTRAINT with a semantically correct bound
on protocol exploration depth.

### 1.5 Fix P2: Simplify `BackendGrant` data selection

**Specification**:

Replace:
```tla
BackendGrant == \E gd \in DataV: ...
```

With:
```tla
BackendGrant == LET gd == CHOOSE d \in DataV : TRUE IN ...
```

Or use a fixed non-zero value:
```tla
BackendGrant == LET gd == 1 IN ...
```

**Caution**: Keep at least one `CpuStore` writing a non-zero value to maintain
`DataIntegrity` invariant discriminatory power. If both `BackendGrant` and
`CpuStore` always produce value `0`, the invariant degenerates.

### 1.6 Implementation order

1. P2 first (trivial, immediate state space reduction)
2. P1 second (add MaxTxn, remove QueueBounded CONSTRAINT from .cfg)
3. P0 last (add CpuRetry, then increase MaxTxn to explore concurrent paths)

### 1.7 Verification plan

After all fixes:
1. Run with MaxTxn=3, verify safety invariants complete in <1 min
2. Increase MaxTxn=5, verify FairSpec with liveness (no deadlock)
3. If MaxTxn=5 + FairSpec passes, the model covers 2-transaction concurrent scenarios

---

## Fix 2: `ubcc_transport_faults.tla` — Add push-grant fault injection

### 2.1 Background

File: `verification/tla/ubcc_transport_faults.tla`

`RecallToGrant` is modeled as an **atomic state transition** (line ~241-251 in core model).
This means the model assumes push-grant messages are **never lost**. The transport fault
model only covers:
- Clear: drop/duplicate/reorder
- RecallResp: duplicate
- InvAck: duplicate

Push-grant (home -> requester) has no fault injection.

### 2.2 Why it matters

The current model conflates two distinct failure modes:
1. Push-grant delivered but Clear lost -> retry Clear (covered)
2. Push-grant itself lost -> requester never sees grant -> must fallback to pull via
   retry timer (NOT explicitly covered, though implicitly safe via orphan cleanup)

### 2.3 Fix specification

**Step 1**: Split `RecallToGrant` into two actions:

```tla
\* Home sends push-grant message (enqueue into grantQ)
HomeSendPushGrant ==
    /\ ost.valid
    /\ ost.stage = "WAITING_TARGET_RESP"
    /\ \* recall barrier done
    /\ grantQ' = Append(grantQ, [type |-> "PUSH_GRANT", ...])
    /\ ost' = [ost EXCEPT !.stage = "WAITING_CLEAR"]

\* Requester receives push-grant (dequeue from grantQ)
RequesterReceivePushGrant ==
    /\ Len(grantQ) > 0
    /\ Head(grantQ).type = "PUSH_GRANT"
    /\ \* requester processes grant
    /\ grantQ' = Tail(grantQ)
```

**Step 2**: Add fault actions:

```tla
\* Drop push-grant
FaultDropPushGrant ==
    /\ Len(grantQ) > 0
    /\ Head(grantQ).type = "PUSH_GRANT"
    /\ grantQ' = Tail(grantQ)
    /\ UNCHANGED <<ost, ...>>

\* Duplicate push-grant
FaultDupPushGrant ==
    /\ Len(grantQ) > 0
    /\ Head(grantQ).type = "PUSH_GRANT"
    /\ grantQ' = Append(grantQ, Head(grantQ))
    /\ UNCHANGED <<ost, ...>>
```

**Step 3**: Verify existing liveness properties still hold:
- `FaultRecallProgress`: RECALL eventually completes or times out
- `OstEventuallyClears`: outstanding eventually clears
- Safety: no double-commit, no stale grant

### 2.4 Expected outcome

- Push-grant drop: orphan cleanup fires after `RecallTimeout` -> slot released ->
  requester re-requests -> fallback to pull path. **Liveness preserved.**
- Push-grant duplicate: requester receives two grants for same reqId -> second grant
  should be idempotent (requester already in granted state). **Need to verify no
  double-commit edge case.**

### 2.5 Priority

P1 — after Part A fixes. This is a coverage precision improvement, not a critical
correctness gap. The implicit safety via orphan cleanup provides a safety net.

---

## File index

| File | Action |
|------|--------|
| `verification/tla/ep_intra_node_single.tla` | Apply Fix 1 (P0+P1+P2) |
| `verification/tla/ep_intra_node_single.cfg` | Update: remove CONSTRAINT QueueBounded, add CONSTANT MaxTxn=3 |
| `verification/tla/ubcc_transport_faults.tla` | Apply Fix 2 (split RecallToGrant + fault injection) |
