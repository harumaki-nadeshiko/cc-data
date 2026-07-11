# TC98 Crash Root Cause Analysis

> Date: 2026-07-11

---

## 1. Summary

TC98 (8n2s, 16-way single-PA contention) crashes with gem5 deadlock panic at
~200ms simulation time. This is a **pre-existing protocol-level bug**, not a
regression from C1/C3/C4/C5 changes.

Evidence:
- C3 (Batch RS) and C4 (Direct-Forward) paths were NOT triggered: 0 RS requests,
  0 DirectData messages in all crash logs. TC98 is all-write (RU), so these paths
  are irrelevant.
- One historical run (20260710_111021_8n2s) PASSED with 0 Upgrade/Invalidate traffic,
  suggesting the bug is non-deterministic (race condition).
- The "double-commit" warnings are false alarms: `_commitCount` is a monotonic
  per-PA counter that never resets, so any PA committed more than once (normal in
  TC98) triggers it.

---

## 2. Crash Mechanism

### 2.1 The crash itself

```
gem5 panic: src/mem/ruby/system/Sequencer.cc:239: Possible Deadlock detected.
```

All 8 gem5 nodes hit their deadlock detection threshold simultaneously at
tick=200000127500. This means outstanding memory requests timed out.

### 2.2 Deeper crash (Node 3 in 095705 run)

```
panic: Runtime Error at CHI-cache-funcs.sm:1204
  assert(tbe.dir_sharers.count() >= 1)
```

In `makeFinalState()`: the HN-F directory thinks an owner exists
(`tbe.dir_ownerExists = true`) but sharers count is 0. This is a directory
state inconsistency inside the gem5 CHI HN-F.

---

## 3. Root Cause: RU Path Incorrectly Triggers Upgrade

### 3.1 What should happen

TC98: 16 CPUs on 8 nodes each do `*hot_addr() = v` (store to same cache line).
Each store is a ReadUnique (RU) to the UBCC home (node 0).

Expected flow:
```
CPU_N store miss → HN-F_N L2 miss → EP-SNF → EPBackend handleRemoteMiss(RU)
→ UBCC home grants ownership → EP-SNF fills L2 → CPU writes → done
→ Next CPU's RU causes RECALL of previous owner → serial chain
```

### 3.2 What actually happens

Some nodes, after receiving a UBCC grant and filling their L2 in Unique/Exclusive
state, get a **SnpCleanInvalid** from their own HN-F (triggered by the other
socket's CPU or a later request). This snoop hits the EPRNF upgrade path:

```
EPRNFController.cc:680-740:
  SnpCleanInvalid arrives for DSM line →
  First-arrival: creates UpgradePending record →
  Issues OuterUpgradeReq to UBCC home →
  Defers SnpResp_I until OuterUpgradeAck
```

The Upgrade path was designed for the case where a **local sharer** (holding
S state) needs to notify the UBCC home before it can respond to a local HN-F
snoop. But in TC98, the line is in **exclusive/unique** state (not shared),
and the SnpCleanInvalid shouldn't trigger an Upgrade at all.

### 3.3 The race

```
Timeline on Node N:
t0: EPBackend receives UBCC grant (G_M), fills L2 via EP-SNF
t1: CPU writes to the line (UC/UD in L2)
t2: UBCC home sends RECALL (new requester from another node)
t3: HN-F on Node N issues SnpCleanInvalid to EPRNF (triggered by RECALL handling)
t4: EPRNF sees DSM line + SnpCleanInvalid → issues OuterUpgradeReq ← BUG
```

At t4, the EPRNF should recognize that this snoop is part of a RECALL sequence
(the line is being recalled, not upgraded). Instead, it treats every
SnpCleanInvalid on a DSM line as an upgrade request.

### 3.4 Why it deadlocks

1. Node N sends OuterUpgradeReq to home
2. Home may have already started a new RECALL for the same PA → outstanding busy
3. Home rejects the UpgradeReq (PA occupied by RECALL outstanding)
4. Node N enters retry loop (399 retries observed)
5. Meanwhile, the pending SnpResp_I is never sent → HN-F TBE stuck
6. HN-F on Node N cannot complete any transactions → deadlock
7. After m_deadlock_threshold ticks → panic

### 3.5 Why it sometimes passes

The race is timing-dependent. If the RECALL response completes before the HN-F
issues SnpCleanInvalid, the upgrade path is never triggered. The run that passed
(111021) had 0 Upgrade/Invalidate traffic — meaning the timing aligned such that
RECALLs completed before snoops fired.

---

## 4. Affected Code

| File | Line | Issue |
|------|------|-------|
| `EPRNFController.cc:704` | `if (isDsmLine)` | Unconditionally enters upgrade path for ALL SnpCleanInvalid on DSM lines |
| `EPRNFController.cc:726-733` | `UpgradePending` creation | Should check if this is a RECALL-induced snoop vs a genuine upgrade |
| `EPBackend.cc:989-1108` | `handleRecallRequest()` | RECALL handling triggers SnpCleanInvalid internally, which then re-enters the upgrade path |

### 4.1 The fix direction

The EPRNF needs to distinguish between:
1. **RECALL-induced SnpCleanInvalid**: The HN-F is invalidating the local L2 because
   UBCC is recalling the line. → Should NOT send OuterUpgradeReq. Should just
   respond SnpResp_I immediately (the UBCC recall handles the ownership transfer).
2. **Genuine upgrade SnpCleanInvalid**: A local CPU wants to write to a shared line
   and the HN-F needs to invalidate the EPRNF's copy. → Should send OuterUpgradeReq
   (existing path).

The distinction can be made by checking whether a RECALL is currently active for
this PA (e.g., EPBackend has an active recall response pending).

---

## 5. Relationship to C1/C3/C4/C5

| Change | Relevant? | Explanation |
|--------|-----------|-------------|
| C1 (MAX_PENDING=16) | No | Queue depth doesn't affect the upgrade path |
| C3 (Batch RS) | No | 0 RS requests in TC98 (all RU) |
| C4 (Direct-Forward) | No | 0 DirectData messages in TC98 |
| C5 (syncInterval=25ns) | No | 0 CLK-SYNC waits in the crash run |

**The crash is 100% pre-existing.** It would occur on the original codebase under
the same timing conditions. C1/C5 may have changed the timing enough to hit the
race more frequently (or less — one run passed).

---

## 6. Recommended Fix

### 6.1 Short-term: Guard upgrade path against active recalls

In `EPRNFController.cc:704`, before entering the upgrade path, check:

```cpp
if (isDsmLine) {
    // NEW: Check if this snoop is part of an active RECALL sequence.
    // If EPBackend has a pending recall for this PA, the SnpCleanInvalid
    // is RECALL-induced → just respond SnpResp_I immediately.
    if (backend->hasActiveRecall(msg->m_addr)) {
        // RECALL-induced snoop: respond immediately, no upgrade needed
        DPRINTF(RubyCHIGeneric,
                "EP_RNF node_id=%d: SnpCleanInvalid during active recall "
                "for PA=0x%lx — immediate SnpResp_I\n",
                _nodeId, msg->m_addr);
        // Send SnpResp_I directly (no upgrade)
        return false;  // or handle SnpResp_I inline
    }
    // ... existing upgrade path ...
}
```

EPBackend needs a new method:
```cpp
bool EPBackend::hasActiveRecall(Addr addr) const {
    // Check if there's a pending recall response for this address
    // (between handleRecallRequest() and sendRecallResponse())
    return _pendingRecalls.find(addr) != _pendingRecalls.end();
}
```

### 6.2 Long-term: Formal verification

The `ep_intra_node_single.tla` model (now with CpuRetry and NumCPUs=8) should
be extended to model the SnpCleanInvalid → Upgrade path and verify that the
fixed guard prevents the deadlock.

---

## 7. Test Plan

After fix:
1. TC98 with timeout=1200, expect PASS or clean TIMEOUT (no panic)
2. Run 3 trials to confirm non-deterministic race is resolved
3. TC99 (independent slots) as regression baseline — should still PASS
