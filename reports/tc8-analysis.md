# TC8 Detailed Analysis

## Workload

```
Phase 1: Node0 writes 0xAAA to DSM_2 (home=Node2)
Phase 2: Node2 + Node1 shared-read DSM_2
Phase 3: Node0 writes 0xBBB to DSM_2 (upgrade)
Phase 4: Node1 reads DSM_2 → expects 0xBBB, gets 0xAAA
```

## Full Request Chain

### Phase 1: Node0 writes 0xAAA

```
T=48447500
  Node0_CPU(VA→PA=0x20000000) → HN-F_0: ReadUnique(0x20000000)
  HN-F_0 → EP-SNF_0: ReadNoSnp(addr=0x20000000)
  EP-SNF_0 → EPBackend_0: handleRemoteMiss(0x20000000, neededPerm=1, writeIntent=1)
    translate: homeNode=2, homePA=0x20020000000
    EPBackend_0 → UBCC_2: processOuterRequest(ReadUnique, writeIntent=true, requester=0)
      UBCC_2 directory: G_I → G_M, owner=0, dirty=true
      grant=GlobalGrantModified (grant=2)
      populateGrantData → 0x00000000 (first write, no prior data)
    isPostGrantShared: state=R_M → false (GrantModified, NOT shared)
    → shared_hint NOT set ← EP-RNF_0 NOT registered in HN-F_0 dir_sharers!
  HN-F_0 ← CompData_UC(data=0x0) [HN-F overlays 0xAAA from store buffer]
  Node0 L2: UD, data=0xAAA ✓

UBCC state: G_M, owner=0, dirty=true
EP-RNF registration: NONE (all nodes' EP-RNF unregistered)
```

### Phase 2: Node2 reads (triggers recall)

```
T=235743000
  Node2_CPU → HN-F_2: ReadShared(PA=0x20020000000)  [Node2 is home]
  HN-F_2 → EP-SNF_2: ReadNoSnp(0x20020000000)
  EP-SNF_2 → EPBackend_2: handleRemoteMiss(0x20020000000, neededPerm=0, writeIntent=0)
    EPBackend_2 → UBCC_2: processOuterRequest(ReadShared, requester=2)
      UBCC_2: state=G_M, owner=0, dirty=true
      owner(0) ≠ requester(2) → recall needed
  
  [Recall]
    EPBackend_2 → EPBackend_0: handleRecallRequest(homePA=0x20020000000)
      EPBackend_0: funcRead Node0 RubySystem @ localPA=0x20000000
      → first_word=0x00000aaa ✓ (found in Node0 L2!)
      data broadcast to phys_mem
    UBCC_2: recall complete, G_M→G_S, owner=-1

  [Retry → Grant]
    T=235743500: EP-SNF_2 retry → UBCC_2 grant G_S→G_S
    OutstandingRequest.dataBuffer → 0x00000aaa
    handleGrant → RequesterLineEntry.state = R_S
    isPostGrantShared → true → shared_hint=true
    EPBackend_2 → setRegistrationDone(0x20020000000) 
    → EP-RNF_2 registered in HN-F_2 dir_sharers ✓

UBCC state: G_S, owner=-1
EP-RNF registration: EP-RNF_2 (Node2) registered
                      EP-RNF_0 (Node0) NOT registered ← writer was never registered!
```

### Phase 2: Node1 reads

```
T=235979000
  Node1_CPU → HN-F_1: ReadShared(PA=0x10020000000)
  HN-F_1 → EP-SNF_1: ReadNoSnp(0x10020000000)
  EP-SNF_1 → EPBackend_1: handleRemoteMiss(...)
    EPBackend_1 → UBCC_2: processOuterRequest(ReadShared, requester=1)
      UBCC_2: state=G_S, owner=-1 → no recall
      grant G_S→G_S
      handleGrant → state=R_S → shared_hint=true
    EPBackend_1 → setRegistrationDone → EP-RNF_1 registered ✓
  populateGrantData → phys_mem → 0x00000aaa ✓

UBCC state: G_S, owner=-1
EP-RNF registration: EP-RNF_1 (Node1), EP-RNF_2 (Node2)
                      EP-RNF_0 (Node0) STILL NOT registered!
```

### Phase 3: Node0 writes 0xBBB (UPGRADE — the failure)

```
T≈300000000
  Node0_CPU → HN-F_0: CleanUnique(PA=0x20000000)
    L2_0 has line in SC state (downgraded by Phase 2 recall)
    HN-F_0: SC→UD (LOCAL upgrade)
    HN-F_0 dir_sharers: {L2_0}  ← EP-RNF_0 NOT in dir_sharers!
    → SnpCleanInvalid sent to L2_0 only (no other sharers)
    → NO SnpCleanInvalid sent to EP-RNF_0!
    → UBCC_2 never gets updateOwner
    → UBCC_2 still thinks: G_S, owner=-1
  
  L2_0: UD, data=0xBBB ✓ (local data is correct)
```

### Phase 4: Node1 reads (gets STALE)

```
T≈400000000 (after sync_wait barrier)
  Node1_CPU → L2_1: SC, data=0xAAA ← L2 hit! No miss!
  → returns 0xAAA ✗ (expected 0xBBB)
```

## Root Cause

```
EP-RNF_0 was NEVER registered in HN-F_0's dir_sharers.

Why?
  1. Node0's write (Phase 1) is ReadUnique → grant G_M → shared_hint=false
  2. Phase 2 recall (G_M→G_S) happens on Node2's EPBackend → registers EP-RNF_2
  3. Phase 2 read from Node1 → registers EP-RNF_1
  4. Node0 never makes a ReadShared request → EP-RNF_0 never registered

When Phase 3 CleanUnique fires on HN-F_0:
  - dir_sharers has only L2_0 (no EP-RNF_0)
  - No snoop → no notify → UBCC_2 never updated
```

## Fix

When recall completes (G_M→G_S), the previous owner's EP-RNF must be registered.
In `EPBackend::handleRecallRequest`, after recall completes and data is captured,
call `_epRnfCtrl->setRegistrationDone` on the OWNER node's EPBackend.

Or: in UBCC's `processRecallResponse`, when state transitions G_M→G_S, trigger
registration on the previous owner node.

Simplest: after the recall data is broadcast and UBCC state changed to G_S,
the EPBackend on the home node that initiated the recall should call
`ownerBackend->_epRnfCtrl->setRegistrationDone(lookupPa)` on the owner node.
