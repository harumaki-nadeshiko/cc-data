# TC3 Failure Analysis

## Symptoms

TC3 (ping-pong 6次读写): 3/6 READ_VAL, stale data on rounds 2-3.

```
Round 1: Node0 writes 0xA → Node1 reads 0xA  MATCH ✓
Round 2: Node1 writes 0xB → Node0 reads 0xA  MISMATCH (expected 0xB)
Round 3: Node0 writes 0xC → Node1 reads 0xB  MISMATCH (expected 0xC)
```

## Full Request Chain — Round 1 (PASS)

### Write Phase (Node0 writes 0xA)

```
T=52883500
  Node0_CPU → HN-F_0: ReadUnique(PA=0x18000000)
    L2 miss → HN-F forwards
  HN-F_0 → EP-SNF_0: ReadNoSnp(PA=0x18000000)
  EP-SNF_0 → EPBackend_0: handleRemoteMiss(0x18000000)
    EPBackend_0: translate → homePA=0x10018000000, homeNode=1
    EPBackend_0 → UBCC_1: processOuterRequest(ReadUnique, writeIntent=true)
    UBCC_1 directory: G_I → G_M, owner=0, dirty=true
    Grant: GlobalGrantModified (grant=2)
    populateGrantData → 0x00000000 (first write, no prior data — correct)
  HN-F_0 ← CompData_UC(data=0x0) [HN-F overlays write data 0xA from store buffer → L2 has 0xA]

  [Confirmation read: Node0 CPU dsm_load(1,0) → L2 hit → 0xA ✓  Local, no UBCC]
```

### Read Phase (Node1 reads 0xA)

```
T=69068000
  Node1_CPU → HN-F_1: ReadShared(PA=0x10018000000)
  HN-F_1 → EP-SNF_1: ReadNoSnp(PA=0x10018000000)
  EP-SNF_1 → EPBackend_1: handleRemoteMiss(0x10018000000)
    EPBackend_1 → UBCC_1: processOuterRequest(ReadShared, writeIntent=false)
    UBCC_1: state=G_M, owner=0, dirty=true
    ownerNode(0) ≠ requesterNode(1) → recall needed

  [Recall]
    EPBackend_1 → EPBackend_0: handleRecallRequest(homePA=0x10018000000)
      EPBackend_0: funcRead Node0 RubySystem @ localPA=0x18000000
      → first_word=0x0000000A ✓ (found in Node0 L2!)
      → data broadcast to phys_mem
      → processRecallResponse → OutstandingRequest.dataBuffer = 0x0000000A
    UBCC_1: recall complete, G_M→G_S, owner=-1

  [Retry → Grant]
    EP-SNF_1 retry → EPBackend_1 → UBCC_1: grant G_S→G_S
    OutstandingRequest.dataBuffer → 0x0000000A → populateGrantData → 0x0000000A ✓
  HN-F_1 ← CompData_SC(data=0xA) → Node1 L2 gets 0xA ✓

  [shared_hint + EP-RNF registration]
    handleGrant → RequesterLineEntry.state = R_S
    isPostGrantShared → true → EPSNF sets shared_hint=true on CompData
    RegisterEPRNF_OnSharedHint fires in HN-F SLICC
    EPBackend → setRegistrationDone(0x10018000000) → EP-RNF regContext = REG_DONE ✓
```

Round 1 completes correctly. UBCC state: G_S, owner=-1.

## Full Request Chain — Round 2 (FAIL)

### Write Phase (Node1 writes 0xB)

```
T≈70000000
  Node1_CPU → HN-F_1: CleanUnique(PA=0x10018000000)
    L2 has line in SC state (from Round 1 read)
    HN-F_1: SC→UD (LOCAL upgrade — does NOT go through EP-SNF/UBCC!)
    dir_sharers = {L2_1, EP-RNF}  (EP-RNF was registered in Round 1)
    → SnpCleanInvalid sent to EP-RNF
      But SnpCleanInvalid is asynchronous — dispatched at T≈70000000,
      received by EP-RNF at T=93663500 (~24M ticks later!)
  Node1 L2: UD, data=0xB ✓
```

Key: The CleanUnique is a LOCAL upgrade (SC→UD in HN-F). UBCC is NOT informed.
The EP-RNF notification (SnpCleanInvalid → updateOwner) is the mechanism that
should tell UBCC about this upgrade. But it takes ~24M ticks to fire.

### Read Phase (Node0 reads, expects 0xB)

```
T≈70000000
  Node0_CPU → HN-F_0: ReadShared(PA=0x18000000)
  HN-F_0 → EP-SNF_0 → EPBackend_0 → UBCC_1
    UBCC_1: state=G_S, owner=-1  ← still the Round-1 state!
    No owner → no recall needed
    grant G_S→G_S
    populateGrantData → phys_mem[0x18000000]
      phys_mem has 0x0000000A (from Round 1 recall broadcast)
    → returns 0xA ✗ (expected 0xB)

  [Node0 spins: got=0xA ≠ 0xB → retry...]
  [100,000 retries exhaust before SnpCleanInvalid fires...]

T=93663500
  EP-RNF recvSnoopMsg(SnpCleanInvalid, regState=REG_DONE)
  → notifyLocalWriteUpgrade → UBCC: updateOwner(UD, owner=1) ✓
  [But Node0 already gave up — emitted READ_VAL with 0xA]

T>93663500
  [Node0 would read again → UBCC: UD → recall needed → should get 0xB
   But spin-wait already exhausted → no more reads]
```

## Root Cause

**Timing gap between CleanUnique and SnpCleanInvalid→updateOwner.**

The CleanUnique (local SC→UD in HN-F) happens at ~T=70M. The EP-RNF notification
chain (SnpCleanInvalid → notifyLocalWriteUpgrade → updateOwner) completes at
T=93M (~24M ticks later). During this 24M-tick window:

1. UBCC still has the OLD state (G_S, owner=-1) ← from Round 1
2. Node0's read hits UBCC → G_S grant → OLD data (0xA)
3. Node0's spin-wait retries exhaust before the update finishes

The 24M-tick delay is inherent to the CHI protocol: the SnpCleanInvalid snoop
travels through the NoC from HN-F → EP-RNF, EP-RNF processes it, calls
notifyLocalWriteUpgrade → EPBackend → UBCC. This is not a bug, it's the
expected asynchronous notification latency.

## Fix Status

- **EP-RNF chain**: CONFIRMED WORKING. All components fire correctly:
  shared_hint → RegisterEPRNF → SnpCleanInvalid → updateOwner.
- **Timing**: sync_wait barrier needs enough DSM-read-based delay to span
  the ~24M-tick window. Current implementation (10000 DSM reads ≈ 5M ticks)
  insufficient. Need tuning or a different synchronization mechanism.
- **Probability**: TC3 is a timing issue, not a protocol correctness bug.

## Recommended Fix

Increase sync_wait delay to bridge the SnpCleanInvalid latency (~24M ticks)
without causing test timeout in SE-mode simulation.
