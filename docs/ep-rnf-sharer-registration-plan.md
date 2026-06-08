# EP-RNF Sharer Registration Plan

Version 3.2 — 2026-06-08

## Change Log

| Ver | Changes |
|---|---|
| 3.2 | 补第 4 处单目标选点(`Send_SnpSharedFwd_ToSharer`)+对应 DCT fallback; SnpUnique 响应类型修正为条件矩阵(对齐 `setExpectedForInvSnoop`); pendingOwnerUpdate 活性约束 |
| 3.1 | 统一"EP-RNF 非数据源优先级"规则覆盖所有单目标 snoop; SnpUnique 响应类型对齐 CHI 标准; 新增 per-path EP-RNF-only 场景分析; DCT 回退规则; SnpCleanInvalid 窗口期保护; B1-B5 验收矩阵 |
| 3.0 | 采用 方案 A'（阻塞完成）; 补充 HB1-HB3; 修正 CHI 消息类型; 否决 timeout→I; 清单现有缺陷 |
| 2.0 | shared_hint→CHIDataMsg; snoop 响应矩阵; EP-RNF="external directory participant" |
| 1.0 | 初版 |

---

## 1. Problem

TC3-TC8 fail because the home UBCC directory is not updated after a local write upgrade (SC→UD).
Root cause: when a local core writes a DSM line via CleanUnique, HN-F transitions SC→UD locally
but the home UBCC (on another node) still thinks the line is SC with no exclusive owner.

```
Node B, Home=Node A:

Step 1 (First Miss):  B0→HN-F_B→EP-SNF_B→UBCC_A: ReadShared(X)
                      UBCC_A: I→SC, sharers={B}, sends CompData back
                      HN-F_B: dir_sharers={L2_B}, state=SC    (EP-RNF NOT in dir_sharers!)

Step 2 (Local Write): B0→L2_B→HN-F_B: CleanUnique(X)
                      HN-F_B: SC→UD, snoops dir_sharers members
                      dir_sharers={L2_B} ← no EP-RNF, no snoop → EPBackend doesn't know!
                      Result: UBCC_A still thinks SC/sharers={B}, no owner recorded.
```

## 2. Core Design

### 2.1 Terminology

EP-RNF = **external directory participant** — a logical sharer in HN-F's `dir_sharers`
representing that UBCC holds metadata tracking for this line. EP-RNF does **not** hold data.

### 2.2 Registration via shared_hint on CompData

UBCC piggybacks `shared_hint=true` on the First Miss CompData (`CHIDataMsg`).
HN-F, on receiving it, adds EP-RNF to `dir_sharers` and enters SC.

### 2.3 EP-RNF Snoop Handling Overview

EP-RNF may receive snoops from HN-F as a `dir_sharers` member:

| Snoop Type | Multicast / Single | EP-RNF Has L2 Peers? | EP-RNF Behavior |
|---|---|---|---|
| `SnpCleanInvalid` (any) | Multicast (all sharers) | Either | Immediate `SnpResp_I`. If local upgrade → notify EPBackend async. |
| `SnpUnique` | Multicast (all sharers) | Either | **Blocking globalInvalidate**. See §2.6.1 for response type matrix. |
| `SnpUnique_RetToSrc` | **Single** (prioritized) | Has L2 → EP-RNF skipped | N/A — not selected |
| `SnpUnique_RetToSrc` | **Single** (EP-RNF only) | No L2 peers | Same as SnpUnique above |
| `SnpOnce` | **Single** (prioritized) | Has L2 → EP-RNF skipped | N/A — not selected |
| `SnpOnce` | **Single** (EP-RNF only) | No L2 peers | **Remote data fetch** via UBCC. Respond: `SnpRespData_SC` (Data). No Resp. |
| `SnpSharedFwd` (DCT→Sharer) | **Single** (prioritized) | Has L2 → EP-RNF skipped | N/A — not selected |
| `SnpSharedFwd` (DCT→Sharer) | **Single** (EP-RNF only) | No L2 peers | Forced DCT-off fallback (EP-RNF can't Fwd data to requester) |
| `SnpOnceFwd` (DCT) | **Single** (prioritized) | Either | Forced DCT-off fallback |
| `SnpUniqueFwd` (DCT) | Multicast (count==1) | EP-RNF only | Forced DCT-off fallback |

### 2.4 HN-F Single-Target Selection: EP-RNF Priority Exclusion

All HN-F snoop actions that select a single target from `dir_sharers` use a priority function
instead of raw `smallestElement()`:

```
MachineID pickSharerForSnoop(NetDest dir_sharers, MachineID epRnfId):
    candidates = dir_sharers;
    candidates.remove(epRnfId);        // Step 1: exclude EP-RNF
    if (candidates.count() > 0):
        return candidates.smallestElement();  // Step 2: pick best L2
    else:
        return epRnfId;                 // Step 3: EP-RNF is the ONLY sharer
```

This replaces `smallestElement()` in **four** actions:
- `Send_SnpUnique_RetToSrc` (line 1906)
- `Send_SnpSharedFwd_ToSharer` (line 2042)
- `Send_SnpOnce` (line 2075)
- `Send_SnpOnceFwd` (line 2105)

### 2.5 DCT Fallback When EP-RNF Is the Only Target

When `dir_sharers = {EP-RNF}` (EP-RNF only) and the selected snoop protocol uses DCT
(Direct Cache Transfer — the Fwd variant where the snoop target sends CompData directly
to the requester), DCT is forced-off because EP-RNF cannot forward data to an arbitrary
L2 requester.

In all three initiators that may dispatch DCT-based snoops when EP-RNF is in `dir_sharers`:
```
// Initiate_ReadUnique_HitUpstream, Initiate_ReadOnce_HitUpstream,
// Initiate_ReadShared_HitUpstream_NoOwner:
if ( (dir_sharers.count() == 1) && dir_sharers.has(epRnfMachineID) ):
    tbe.use_DCT := false;   // force non-DCT path; EP-RNF responds to HN-F
```
After `use_DCT:=false`, the initiator dispatches the non-DCT variant:
- ReadUnique → `Send_SnpUnique_RetToSrc`
- ReadOnce → `Send_SnpOnce`
- ReadShared (NoOwner) → `Send_SnpOnce`

### 2.6 Correctness Model

#### 2.6.1 SnpUnique Response Type Matrix (aligned with setExpectedForInvSnoop)

HN-F's `setExpectedForInvSnoop(tbe, expectCleanWB)` (`CHI-cache-funcs.sm:1065-1082`)
generates expected types conditionally:

```
Expected Data types:
  if expectCleanWB:              → SnpRespData_I
  if dataMaybeDirtyUpstream:     → SnpRespData_I_PD
Expected Resp types:
  if dataMaybeDirtyUpstream:
      if !expectCleanWB || sharers>1: → SnpResp_I
  else:                               → SnpResp_I
```

EP-RNF determines its response from the snoop message context and globalInvalidate result:

**Decision rule** (in EP-RNF `recvSnoopMsg`):
```
// The snoop message's retToSrc field matches HN-F's expectCleanWB:
//   Send_SnpUnique          → retToSrc=false  (= expectCleanWB=false) → always need Resp_I
//   Send_SnpUnique_RetToSrc:
//     single target         → retToSrc=true   (= expectCleanWB=true)  → may NOT need Resp_I
//     remaining (multicast) → retToSrc=false  (= expectCleanWB=false) → always need Resp_I

sendRespI = (msg.retToSrc == false);  // only skip when we're the explicit single data-source

// Data channel: always send what we collected
if (dirtyCollected):     send SnpRespData_I_PD on Data
else if (dataCollected): send SnpRespData_I   on Data
// else: no Data (remote was clean, no data to return)
```

| Scenario | retToSrc | Dirty Collected | EP-RNF Sends | Matches HN-F Expected |
|---|---|---|---|---|
| SnpUnique multicast | false | no | `Resp_I` | `Resp_I` ✓ |
| SnpUnique multicast | false | yes | `Resp_I` + `Data_I_PD` | `Resp_I` + `Data_I_PD` ✓ |
| SnpUnique_RetToSrc (single, pickSharerForSnoop chose EP-RNF) | true | no | `Data_I` (no Resp) | `Data_I` ✓ (cleanWB, no Resp expected) |
| SnpUnique_RetToSrc (single, dirty remote) | true | yes | `Data_I_PD` (no Resp) | `Data_I_PD` ✓ (`cleanWB&&dirty`: Data_I_PD matches dirty set; no Resp needed for single retToSrc) |

This rule guarantees **every message type EP-RNF sends is in HN-F's expected set**.

#### 2.6.2 SnpCleanInvalid

SnpCleanInvalid: Non-blocking. SnpResp_I is sent immediately. The UBCC `ownerUpdate`
is asynchronous. Correctness: HN-F has already committed SC→UD — any cross-node access
during the async window finds correct data from HN-F via recall path (§4.2). To prevent
races during this window, UBCC marks the line `pendingOwnerUpdate` — conflicting
operations (cross-node Unique) are deferred until the update completes (§9.3).

**SnpUnique / SnpUnique_RetToSrc**: Blocking (方案 A'). Three Happens-Before invariants:

```
HB1: remote_invalidate_ack(all_sharers)  happens-before  EP-RNF→HN-F SnpResp
HB2: dirty_data_visible_at_home          happens-before  SnpRespData_I_PD
HB3: SnpResp                             happens-before  HN-F grants Unique (expected_snp_resp==0)
```

Deadlock-free proof: Linear dependency chain.
```
HN-F ──[SnpUnique*]──→ EP-RNF ──[globalInvalidate]──→ UBCC(home)
                                                              │
                                                 [invalidate snoop per sharer]
                                                              ↓
                                                       remote HN-F ──→ remote L2
                                                              │
                                                 [SnpResp_I or SnpRespData_I_PD]
                                                              ↓
                                                       UBCC ──[allAck]──→ EP-RNF
                                                              │
                                                EP-RNF ──[SnpResp*]──→ HN-F
```
Preconditions: per-line single-flight, callback single-fire, rspOut retry-guaranteed,
UBCC per-line FIFO. Remote HN-F does NOT wait for local HN-F (different line/TBE).

## 3. Per-Path Analysis: EP-RNF Only Scenarios

### 3.1 Path A: Send_SnpUnique_RetToSrc → EP-RNF Only

**HN-F side** (`CHI-cache-actions.sm:1894-1924`):
```
Modified selection:
    dest := pickSharerForSnoop(dir_sharers, epRnfId);
    // If EP-RNF is the only sharer → dest == EP-RNF
expected (via setExpectedForInvSnoop, expectCleanWB=true):
    Resp: SnpResp_I  (if multi-sharer or not dirty-upstream)
    Data: SnpRespData_I (always, cleanWB) + SnpRespData_I_PD (if dirty upstream)
```
EP-RNF response rule: per §2.6.1 — `sendRespI = (msg.retToSrc == false)`.
Since this is the single retToSrc target: `retToSrc=true` → no Resp_I.
Only Data: `SnpRespData_I` (if clean) or `SnpRespData_I_PD` (if dirty collected).

**Trigger conditions (EP-RNF only)**:
- `Initiate_CleanUnique` / `Initiate_ReadUnique_Upgrade` (line 644-672):
  L2 wants CleanUnique, dir_sharers={EP-RNF}, no owner → `dataMaybeDirtyUpstream=false`
  → dispatches `SendSnpUniqueRetToSrc`. EP-RNF is the only candidate.
- `Initiate_ReadUnique_HitUpstream` (line 682-704):
  ReadUnique on cached line, no owner, no dirty → dispatches `SendSnpUniqueRetToSrc`.
- `Initiate_InvalidationSnoop` (line 1397-1428):
  Propagating incoming SnpUnique from remote UBCC. `snpNeedsData && !dataMaybeDirtyUpstream`
  → dispatches `SendSnpUniqueRetToSrc`. EP-RNF only candidate.

**EP-RNF side**: Blocking globalInvalidate (reuses SnpUnique multicast logic).
Response determined by §2.6.1: `retToSrc=true` → Data-only; `retToSrc=false` → Resp_I + Data if dirty.

### 3.2 Path B: Send_SnpOnce → EP-RNF Only

**HN-F side** (`CHI-cache-actions.sm:2048-2080`):
```
Modified selection:
    dest := pickSharerForSnoop(dir_sharers, epRnfId);
expected (no owner, no exclusive):
    Data: SnpRespData_SC
```

**Trigger conditions (EP-RNF only)**:
- `Initiate_ReadShared_HitUpstream_NoOwner` (line 504-516):
  ReadShared on cached line, no owner, DCT off → dispatches `SendSnpOnce`.
- `Initiate_ReadOnce_HitUpstream` (line 591-612):
  ReadOnce on cached line, no owner, DCT off → dispatches `SendSnpOnce`.
- `Initiate_SnpOnce` propagation (line 1430-1473):
  Incoming SnpOnce from remote being propagated to local sharers.

**EP-RNF side**: Remote data fetch (shared, NOT invalidate).
EP-RNF → EPBackend → UBCC(home) → find sharer node → read data → respond `SnpRespData_SC` (Data, no Resp).

### 3.3 Path C: Send_SnpSharedFwd_ToSharer (DCT) → EP-RNF Only

**HN-F side** (`CHI-cache-actions.sm:2019-2046`):
```
Modified selection:
    dest := pickSharerForSnoop(dir_sharers, epRnfId);
Assertions: dataMaybeDirtyUpstream==false, dir_ownerExists==false
Expected (DCT, retToSrc):
    Data: SnpRespData_SC_Fwded_SC or SnpRespData_I_Fwded_SC
```

**Trigger**: `Initiate_ReadShared_HitUpstream_NoOwner` with DCT. `dir_sharers={EP-RNF}`,
no owner, no dirty upstream → HN-F would pick EP-RNF as the Fwd target, expecting it to
forward shared data directly to the requester.

**EP-RNF handling**: Cannot do direct Fwd. DCT fallback (§2.5) forces `use_DCT:=false`
in the initiator → falls to non-DCT `Send_SnpOnce` → EP-RNF handles via Path B (remoteFetch).

### 3.4 Path D: Send_SnpOnceFwd (DCT) → EP-RNF Only

**HN-F side** (`CHI-cache-actions.sm:2082-2110`):
```
// DCT fallback: if EP-RNF only → force use_DCT = false
Modified initiator (Initiate_ReadOnce_HitUpstream):
    if (dir_sharers.count()==1 && dir_sharers.has(epRnfId)):
        tbe.use_DCT := false;  // → falls back to Path B (Send_SnpOnce)
```

**Trigger**: ReadOnce with DCT, EP-RNF only candidate. EP-RNF cannot forward data to requester.
Fallback to non-DCT Path B.

### 3.5 Path E: Send_SnpUniqueFwd (DCT) → EP-RNF Only

**HN-F side** (`CHI-cache-actions.sm:1926-1943`):
```
// DCT fallback: if EP-RNF only → force use_DCT = false
Modified initiator (Initiate_ReadUnique_HitUpstream):
    if (dir_sharers.count()==1 && dir_sharers.has(epRnfId)):
        tbe.use_DCT := false;  // → falls back to SnpUnique or SnpUnique_RetToSrc
```

**Trigger**: ReadUnique with DCT, EP-RNF only candidate. Falls back to non-DCT Path A.

---

## 4. Message Flow Diagrams

### 4.1 First Miss with shared_hint

```
Node B (local requester)                    Node A (home)

B0→L2_B→HN-F_B: ReadShared(X)
         │
         ↓
HN-F_B → EP-SNF_B → NoC → EP-SNF_A → UBCC_A: ReadShared(X)
         │ UBCC: I→SC, sharers={B}
         ↓ data=0xABCD
UBCC_A: CHIDataMsg { CompData_SC, data=0xABCD, shared_hint=true }
         │
         ↓
UBCC_A → EP-SNF_A → NoC → EP-SNF_B → HN-F_B:
  CHIDataMsg { CompData_SC, 0xABCD, shared_hint=true }
         │
         ↓ HN-F: TBE setup (dataInPort transition)
         │   if (in_msg.shared_hint && isDSM(addr)):
         │     tbe.dir_sharers.add(epRnfMachineID)
         │     tbe.dataUnique = false
         │     RegistrationContext.set(linePa, REG_DONE)
         │
         ↓ makeFinalState():
         │   dir_sharers={L2_B, EP-RNF}, dir_state=RSC, cache_state=SC
         │
HN-F_B → L2_B → B0: CompData(X, 0xABCD)
```

### 4.2 Local Write Upgrade → SnpCleanInvalid (Multicast → EP-RNF Included)

```
B0→L2_B→HN-F_B: CleanUnique(X)
         │
         ↓ HN-F: SC→UD, dir_sharers={L2_B, EP-RNF}
         │
         ├──→ L2_B:  SnpCleanInvalid  (standard)
         └──→ EP-RNF: SnpCleanInvalid  (multicast — EP-RNF is a sharer)
                │
                ↓ EP-RNF: recvSnoopMsg(SnpCleanInvalid, context=REG_DONE)
                │ → IMMEDIATE SnpResp_I
                │ → ASYNC: notify EPBackend → updateOwner(X, B, UD)
                │
         HN-F: expected_snp_resp==0 → grant Unique → L2_B: UD
```

### 4.3 SnpUnique → Blocking Global Invalidation (Multicast → EP-RNF Included)

```
HN-F → EP-RNF: SnpUnique(X)   [multicast to all dir_sharers]
         │
         ↓ EP-RNF: recvSnoopMsg(SnpUnique)
         │   Allocate PendingSnoopTxn { type=SnpUnique, dest=HN-F_MachineID }
         │   → EPBackend::globalInvalidate(X, nodeId, epoch, callback)
         │   → BLOCK — do not respond yet
         │
         ↓ EPBackend → UBCC(home):
         │   globalInvalidate(X, requesterNode=B, epoch=N)
         │
         ↓ UBCC:
         │   for each sharer except B:
         │     send CHI invalidation → wait for SnpResp_I (or SnpRespData_I_PD)
         │     collect dirtyData if any
         │   sharersMask={}, ownerNode=B, state=UD
         │   → callback: allAck(X, dirtyData?, epoch=N)
         │
         ↓ EP-RNF: callback fires
         │   Resp channel: SnpResp_I
         │   Data channel: SnpRespData_I_PD (if dirty data collected)
         │
HN-F: receives SnpResp [+ SnpRespData] → expected_snp_resp==0 → grants Unique
```

### 4.4 SnpOnce → Remote Data Fetch (EP-RNF Only, No L2 Peers)

```
dir_sharers = {EP-RNF}  (all L2 copies evicted)

New core → HN-F: ReadShared(X)
         │
         ↓ HN-F: pickSharerForSnoop(dir_sharers, epRnfId) → EP-RNF (only option)
         │
HN-F → EP-RNF: SnpOnce(X)
         │
         ↓ EP-RNF: recvSnoopMsg(SnpOnce)
         │   → EPBackend::remoteFetch(X, Shared, callback)
         │   → BLOCK until data arrives
         │
         ↓ EPBackend → UBCC(home):
         │   findSharer(X) → remoteNode C has data
         │   → read data from C's HN-F/L2 via recall or shared read path
         │
         ↓ UBCC → EPBackend → EP-RNF:
         │   callback: { data=0xABCD }
         │
EP-RNF → HN-F: SnpRespData_SC [Data channel only, no Resp]

HN-F: → L2 → new core: CompData(X, 0xABCD)
```

## 5. Phase Plan

### Phase 0: MachineID Injection & Snoop Connectivity

**Goal**: Verify EP-RNF MachineID is valid, NoC-reachable, injected into HN-F.

**Tasks**:
1. Verify EP-RNF `snpIn` → `network.out_port` (confirmed: `CHI_config.py:191`).
2. Verify EP-RNF is in `network_nodes` (confirmed: `_make_ep_node`).
3. Add `epRnfMachineID` constructor parameter to `CHI_HNFController`.
4. In `CHI_ubcc_framework.py`, inject `epRnfMachineID` when constructing HN-F.
5. Add `getEpRnfMachineID()` test hook to EPBackend.
6. Write tests E-01 (validity), E-02 (injection match).

**Files Modified**:
- `gem5/configs/ruby/CHI_ubcc_framework.py`: inject `epRnfMachineID` into HN-F
- `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh/cc`: `getEpRnfMachineID()`
- `gem5/src/mem/ruby/protocol/chi/CHI-cache.sm`: add `epRnfMachineID` param
- `tests/ubcc/ep-rnf/test_phase0_machineid.py`: E-01, E-02

**Exit Criteria**:
- E-01 PASS: EP-RNF MachineID type=Cache, num>0
- E-02 PASS: HN-F's `epRnfMachineID` == EP-RNF's MachineID

---

### Phase 1: shared_hint CompData Path

**Goal**: UBCC grants CompData with `shared_hint=true`, HN-F registers EP-RNF, enters SC.

**Task 1: shared_hint on CHIDataMsg**
- In `CHI-msg.sm`: `bool shared_hint, default="false"` on `CHIDataMsg`.
- Verify field survives NoC serialization.

**Task 2: EPBackend sets shared_hint**
- In EPBackend grant handling: `if (addr∈DSM && ubccPostState==SC) msg.shared_hint=true`.
- Set BEFORE forwarding through EP-SNF.

**Task 3: HN-F CompData path**
- In `CHI-cache-actions.sm`, CompData transition:
  - `if (in_msg.shared_hint && isDSM(addr)) tbe.dir_sharers.add(epRnfMachineID)`.
  - `tbe.dataUnique = false`.
  - Runs before `makeFinalState()`.

**Task 4: HN-F single-target priority selection**
- Replace `smallestElement()` with `pickSharerForSnoop()` in:
  - `Send_SnpUnique_RetToSrc` (line 1906)
  - `Send_SnpSharedFwd_ToSharer` (line 2042)
  - `Send_SnpOnce` (line 2075)
  - `Send_SnpOnceFwd` (line 2105)
- Logic: exclude EP-RNF first → pick L2 if any → else EP-RNF.

**Task 5: DCT fallback**
- In three initiators: `Initiate_ReadUnique_HitUpstream`, `Initiate_ReadOnce_HitUpstream`,
  `Initiate_ReadShared_HitUpstream_NoOwner`:
  - `if (dir_sharers.count()==1 && has(epRnfId)) use_DCT:=false`.

**Files Modified**:
- `gem5/src/mem/ruby/protocol/chi/CHI-msg.sm`: `shared_hint` field
- `gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm`: shared_hint, pickSharerForSnoop, DCT fallback
- `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh/cc`: state query
- `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh/cc`: set shared_hint
- `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.hh/cc`: passthrough

**Exit Criteria**:
- TC-3 PASS: HN-F SC, EP-RNF ∈ dir_sharers after First Miss
- shared_hint propagates end-to-end
- pickSharerForSnoop: L2 selected when available, EP-RNF only when sole sharer

---

### Phase 2: EP-RNF Snoop Matrix & Operations

**Goal**: EP-RNF handles all snoop types per §2.3. Implements blocking globalInvalidate (SnpUnique)
and remoteFetch (SnpOnce). Fixes existing Phase 3 code defects (B1-B5).

**Task 1: Registration context**
- `map<linePa, Rctx { state, epoch }>`. `REG_IDLE → REG_DONE` on shared_hint CompData.

**Task 2: recvSnoopMsg — type dispatch**
```
recvSnoopMsg(msg):
  switch (msg->m_type):
    case SnpCleanInvalid:
      if (ctx==REG_DONE) notifyLocalWriteUpgrade();
      sendResponse(SnpResp_I);  // immediate
      return;

    case SnpUnique: case SnpUniqueFwd:
      allocate PendingSnoopTxn;
      EPBackend::globalInvalidate(addr, nodeId, epoch, callback);
      return; // BLOCK — response in callback

    case SnpOnce:
      allocate PendingSnoopTxn;
      EPBackend::remoteFetch(addr, Shared, callback);
      return; // BLOCK — response in callback

    case SnpOnceFwd: case SnpUniqueFwd_DCT:
      // Should never arrive (DCT fallback prevents it)
      // Defensive: SnpResp_I + fatal warning
      return;

    case SnpShared: case SnpSharedFwd:
      // Should never arrive (Send_SnpShared targets dir_owner; Fwd guarded)
      return;
```

**Task 3: EPBackend::globalInvalidate**
- Signature: `globalInvalidate(linePa, requesterNodeId, epoch, callback)`
- → UBCC: invalidate all remote sharers, collect dirty data
- Callback: `{ok, dirtyData?}` → EP-RNF sends response per §2.6.1 (`retToSrc` rule)
- **HB1**: callback fires only after all remote invalidated
- **HB2**: dirtyData included in SnpRespData_I_PD

**Task 4: EPBackend::remoteFetch**
- Signature: `remoteFetch(linePa, Shared, callback)`
- → UBCC: find remote sharer, read data
- Callback: `{ok, data}` → EP-RNF sends `SnpRespData_SC`

**Task 5: Fix existing implementation defects (B1-B5)**
See §7 for defect list and acceptance criteria.

**Task 6: PendingSnoopTxn retry**
- If `rspOut` full on callback: retain pending entry, schedule retry event (1 tick)
- B3 fix: replace fire-and-forget `sendResponseMsg` with retry loop

**Files Modified**:
- `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.hh/cc`: type dispatch, PendingSnoopTxn, retry, context
- `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh/cc`: globalInvalidate, remoteFetch, B1-B5 fixes

**Exit Criteria**:
- TC-4 PASS: SnpCleanInvalid → notify EPBackend
- TC-5 PASS: Remote SnpCleanInvalid → no notification
- TC-9 PASS: SnpUnique → blocking globalInvalidate → response per §2.6.1 matrix (verified: no unexpected message error)
- TC-10 PASS: SnpOnce EP-RNF only → remoteFetch → SnpRespData_SC
- TC-12 PASS: rspOut full → retry succeeds
- TC-13 PASS: outerTxnPending cleanup on Busy
- TC-14 PASS: B4 real CHI invalidation chain verified
- TC-15 PASS: B5 missing backend → hard-fail (not direct ack)

---

### Phase 3: UBCC globalInvalidate & updateOwner

**Goal**: UBCC supports global invalidation, remote data fetch, and owner update.

**Task 1: globalInvalidate**
- Iterate sharersMask, send CHI invalidation to each (exclude requesterNode)
- Wait for all acks, collect SnpRespData_I_PD if any
- Update: `sharersMask={}`, `ownerNode=requester`, `state=UD`
- Epoch validated

**Task 2: remoteFetch (find and read from remote sharer)**
- UBCC finds sharer node from sharersMask
- Sends shared read to sharer's HN-F, gets data back
- Returns data to EPBackend

**Task 3: updateOwner**
- `updateOwner(addr, nodeId, UD)`: set ownerNode, clear sharersMask, state=UD
- `pendingOwnerUpdate` flag: set during async window, clear after update completes (§9.3)

**Files Modified**:
- `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh/cc`: globalInvalidate, remoteFetch, updateOwner
- `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh/cc`: dispatch

**Exit Criteria**:
- TC-6 PASS: UBCC SC→UD on updateOwner
- TC-11 PASS: globalInvalidate invalidates all remotes, collects dirty

---

### Phase 4: End-to-End Regression

**Goal**: All testcases pass.

**Exit Criteria**:
- TC1-TC15 all PASS
- No fatal/assert
- No deadlock > 10ms
- All cross-node data values correct

---

## 6. File Modification Summary

| File | Phase | Change |
|---|---|---|
| `CHI_ubcc_framework.py` | 0 | Inject `epRnfMachineID` into HN-F |
| `CHI-msg.sm` | 1 | `shared_hint` on `CHIDataMsg` |
| `CHI-cache-actions.sm` | 1 | shared_hint; pickSharerForSnoop; DCT fallback |
| `CHI-cache.sm` | 1 | `epRnfMachineID` param |
| `EPBackend.hh/cc` | 0-3 | getEpRnfMachineID; shared_hint; globalInvalidate; remoteFetch; B1-B5; pendingOwnerUpdate |
| `UBCCController.hh/cc` | 1,3 | State query; globalInvalidate; remoteFetch; updateOwner; pendingOwnerUpdate |
| `EPSNFController.hh/cc` | 1 | shared_hint passthrough |
| `EPRNFController.hh/cc` | 2 | Type dispatch; PendingSnoopTxn; retry; context; B2/B3 fixes |

---

## 7. Existing Phase 3 Defects (B1-B5) + Acceptance Matrix

| ID | Defect | File:Line | Fix | Verification Test | Observable Assertion |
|---|---|---|---|---|---|
| B1 | recvSnoopMsg treats all snoop types identically | `EPRNFController.cc:307-361` | Type dispatch (Phase 2 Task 2) | TC-4, TC-5, TC-9, TC-10 | Per-type hit counter increments; response type matches expected; no "unhandled snoop type" fatal |
| B2 | outerTxnPending not cleaned on Busy/error | `EPBackend.cc:342-347` | RAII/finally-path clear | TC-13 | Flag cleared after Busy; subsequent snoop proceeds |
| B3 | Delayed HN response send not retried | `EPRNFController.cc:533-542` | Retry event loop | TC-12 | rspOut full → retry → message delivered; HN TBE released |
| B4 | handleInvalidationRequest updates bookkeeping only, no real CHI invalidation | `EPBackend.cc:1421-1472` | Real CHI invalidation via HN→L2 before ack | TC-14 | Remote L2 snoop count increments before UBCC ack; remote read after ack returns stale-fault or new data |
| B5 | Direct ack fallback when sharerBackend missing | `EPBackend.cc:478-490` | Remove fallback; fail/retry | TC-15 | Missing backend → fatal or retry; NO silent ack |

---

## 8. Test Cases

### E-01 / E-02: Phase 0 (unchanged)
EP-RNF MachineID validity and HN-F injection verification.

### TC-3: First Miss → SC + EP-RNF in dir_sharers [Phase 1]

| Field | Value |
|---|---|
| Purpose | shared_hint → HN-F SC, EP-RNF registered |
| Harness | ARM_SYNC |
| Inputs | Node B: `ldr x0, [DSM_BASE+X]` |
| Pass | cache_state=SC, EP-RNF ∈ dir_sharers |
| Negative | UC/UD or EP-RNF ∉ dir_sharers |

### TC-4: SnpCleanInvalid → EPBackend Notified [Phase 2]

| Field | Value |
|---|---|
| Purpose | CleanUnique snoops EP-RNF (multicast), EPBackend notified |
| Harness | ARM_SYNC |
| Preconditions | Line in SC from TC-3 |
| Inputs | `str x1, [DSM_BASE+X]` |
| Pass | SnpCleanInvalid received; localWriteUpgrade called; SnpResp_I sent |
| Negative | No snoop or wrong branch |

### TC-5: Remote Invalidation → No EPBackend Notification [Phase 2]

| Field | Value |
|---|---|
| Purpose | Cross-node invalidation SnpCleanInvalid ≠ local upgrade |
| Harness | ARM_SYNC |
| Inputs | Node C: `str x2, [X]` |
| Pass | SnpResp_I only; NO localWriteUpgrade |
| Negative | EPBackend incorrectly notified |

### TC-9: SnpUnique → Blocking globalInvalidate [Phase 2]

| Field | Value |
|---|---|
| Purpose | SnpUnique (multicast) → globalInvalidate all remotes, collect dirty, respond correctly |
| Harness | ARM_SYNC |
| Preconditions | 3 nodes; remote sharer with dirty data; local ReadUnique |
| Inputs | Local ReadUnique on shared line |
| Observables | Before/after UBCC sharersMask; EP-RNF response types; SnpRespData_I_PD present |
| Pass | All remote sharers invalidated; dirty data returned per §2.6.1 (retToSrc=false → Resp_I; Data_I_PD if dirty); UBCC UD |
| Negative | Stale remote copies; wrong response type; dirty data lost |

### TC-10: SnpOnce EP-RNF Only → remoteFetch [Phase 2]

| Field | Value |
|---|---|
| Purpose | EP-RNF only sharer → SnpOnce → fetch remote data |
| Harness | ARM_SYNC + eviction trigger |
| Preconditions | L2 evicts → dir_sharers={EP-RNF}; new core reads same line |
| Inputs | ReadShared → SnpOnce to EP-RNF |
| Pass | EP-RNF: remoteFetch called; SnpRespData_SC returned; HN-F gets data; core reads correct value |
| Negative | SNF fallback (should NOT happen — EP-RNF is selected); wrong data |

### TC-11: UBCC globalInvalidate Correct [Phase 3]

| Field | Value |
|---|---|
| Purpose | UBCC invalidates all remotes, collects dirty |
| Harness | ARM_SYNC |
| Preconditions | 3 nodes (A home, B requester, C sharer dirty) |
| Inputs | B: ReadUnique |
| Pass | C invalidated; dirty returned; UBCC state=UD, owner=B |
| Negative | C stale; dirty lost |

### TC-12: Delayed Response Retry [Phase 2]

| Field | Value |
|---|---|
| Purpose | rspOut full → retry → HN-F receives response |
| Harness | PY_INJECT (fill rspOut) |
| Inputs | SnpUnique callback; rspOut at capacity |
| Pass | Response sent after retry; no lost message; HN-F TBE released |
| Negative | Response dropped; HN-F stall permanent |

### TC-13: outerTxnPending Cleanup [Phase 2]

| Field | Value |
|---|---|
| Purpose | Busy return → pending flag cleared |
| Harness | PY_INJECT (inject UBCC Busy) |
| Pass | Flag cleared; next snoop on same line proceeds |
| Negative | Flag leaked; subsequent snoop blocked |

### TC-14: B4 — Real CHI Invalidation Chain [Phase 2]

| Field | Value |
|---|---|
| Purpose | handleInvalidationRequest actually invalidates via HN→L2 |
| Harness | ARM_SYNC |
| Preconditions | Remote L2 has valid copy |
| Inputs | UBCC sends invalidation to local node |
| Observables | Remote L2 snoop count increments; L2 cache state transitions to I |
| Pass | L2 invalidated BEFORE UBCC receives ack |
| Negative | UBCC gets ack but L2 still SC |

### TC-15: B5 — Missing Backend Hard-Fail [Phase 2]

| Field | Value |
|---|---|
| Purpose | sharerBackend null → fatal/retry, not silent ack |
| Harness | PY_INJECT (null out backend) |
| Inputs | Invalidation request with missing backend |
| Pass | Fatal assertion or retry; NO silent SnpResp_I |
| Negative | Direct ack passed through |

### TC-6 / TC-7 / TC-8: Phase 3-4 (unchanged)
updateOwner; cross-node read after write; multi-write stress.

---

## 9. Timing & Correctness

### 9.1 Happens-Before Invariants

```
HB1: remote_invalidate_ack(all)  hb  EP-RNF→HN-F SnpResp
     Code point: UBCCController::globalInvalidate allAck callback fires → EPBackend callback → EP-RNF sends resp

HB2: dirty_data_visible_at_home  hb  SnpRespData_I_PD
     Code point: UBCC copies dirtyData from SnpRespData_I_PD ack → stores in callback payload → EP-RNF puts in out_msg

HB3: SnpResp  hb  HN-F expected_snp_resp==0 → grant Unique
     Code point: SnpResp arrives at HN-F snpRespPort → UpdateDirState_FromSnpResp → decrement expectation
```

### 9.2 SnpCleanInvalid Timing

```
A: HN-F commits SC→UD (directory)
B: HN-F sends SnpCleanInvalid to EP-RNF
C: EP-RNF sends SnpResp_I immediately
D: EP-RNF notifies EPBackend (async, after C)
E: EPBackend→UBCC updateOwner completes

A→B→C→D→E (sequential, no overlap)
```

### 9.3 Async updateOwner Window Protection

Between D and E (async window), UBCC still shows SC state. Cross-node requests arriving
in this window may see stale SC. Protection:

1. UBCC marks line `pendingOwnerUpdate = true` when `updateOwner` is dispatched.
2. Any cross-node Unique (ReadUnique/CleanUnique) arriving while `pendingOwnerUpdate==true`:
   - UBCC defers the request (retry queue or NACK) until `pendingOwnerUpdate` clears.
   - UBCC can also proactively satisfy from local HN-F data (recall path) if dirty-safe.
3. Cross-node ReadShared during window: UBCC can satisfy from local HN-F recall path,
   which always finds the latest data.

**Liveness guarantee**: deferred requests are queued per-line FIFO. `pendingOwnerUpdate`
clears within bounded time (one UBCC→EPBackend→UBCC round-trip, typically < 500 ticks).
Maximum concurrent deferred requests per line ≤ 1 (UBCC serialization). No starvation:
each deferred request gets retried and guaranteed to complete because `pendingOwnerUpdate`
is transient, not a persistent lock.

**Timeout safety net**: If `pendingOwnerUpdate` is not cleared within `MAX_PENDING_OWNER_TICKS`
(default 5000 ticks), UBCC **quarantines** the line (marks `state=QUARANTINE`), aborts all
deferred requests with NACK, and logs a fatal diagnostic. The quarantine ensures no silent
corruption — cross-node access to the line triggers a known-error path until manual reset.
This is a safety net, not a normal code path; arrival at QUARANTINE indicates a bug in the
updateOwner callback chain.

### 9.4 SnpUnique Deadlock-Free Conditions

| Condition | Enforcement |
|---|---|
| Per-line single-flight | EP-RNF `PendingSnoopTxn` — only one active per linePa |
| Callback single-fire | EPBackend ensures callback fires exactly once |
| rspOut retry guaranteed | Phase 2 Task 6: retry event on send failure |
| UBCC per-line FIFO | UBCC outstanding request queue serializes per line |
| Remote HN-F does not wait for local HN-F | Different line/TBE — verified by dependency chain |

---

## 10. Concurrency & Arbitration

### 10.1 EP-RNF Per-Line Serialization

| Snoop Type | Concurrency Handling |
|---|---|
| SnpCleanInvalid | Immediate — no blocking, no queuing |
| SnpUnique / SnpOnce | Allocate PendingSnoopTxn. Second snoop for same line while first pending → queued FIFO |
| SnpUniqueFwd / SnpOnceFwd | Should never arrive (DCT fallback) |

### 10.2 UBCC Per-Line Serialization

UBCC serializes all operations per line (I→SC, SC→UD, globalInvalidate, updateOwner).
No two operations on the same line execute concurrently.

### 10.3 globalInvalidate vs Recall Priority

```
If globalInvalidate active for (linePa, epoch):
  → Recall for same (linePa, epoch) is superseded
  → UBCC satisfies recall from post-invalidate UD state
```

---

## 11. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| shared_hint lost in NoC | Medium | High | Phase 1 per-hop log assertion |
| EP-RNF MachineID collision | Low | Medium | Phase 0 verification |
| Remote TBE exhaustion blocking globalInvalidate | Medium | Medium | UBCC retry; blocking waits (correctness > latency) |
| outerTxnPending leaked | Medium | High | Phase 2 B2 fix; TC-13 |
| Delayed response lost (rspOut full) | Medium | High | Phase 2 B3 fix (retry); TC-12 |
| handleInvalidationRequest no real CHI inval | High | High | Phase 2 B4 fix; TC-14 |
| Direct ack fallback | Medium | High | Phase 2 B5 fix; TC-15 |
| SnpCleanInvalid async window stale | Low | Medium | pendingOwnerUpdate barrier; §9.3 |
| DCT path not falling back | Low | Medium | Forced use_DCT:=false; Phase 1 Task 5 |

## 12. Design Decisions

| Decision | Rationale |
|---|---|
| EP-RNF = external directory participant | Aligns with UBCC metadata-only |
| shared_hint on CHIDataMsg | CompData travels on data channel |
| pickSharerForSnoop priority: L2 > EP-RNF | EP-RNF is metadata-only; L2 holds real data |
| EP-RNF handles SnpOnce by remoteFetch | EP-RNF only scenario: data is remote, fetch via UBCC |
| DCT always falls back when EP-RNF only | EP-RNF can't forward data to arbitrary requestor |
| SnpUnique: blocking globalInvalidate | HB1-HB3 guarantee correctness; timeout→I is incorrect |
| SnpCleanInvalid: async updateOwner + pendingOwnerUpdate barrier | Immediate SnpResp_I keeps HN-F flowing; barrier prevents stale concurrent ops |
| SnpUnique response: per §2.6.1 retToSrc conditional rule | EP-RNF adapts to HN-F expected set dynamically |
| globalInvalidate > recall priority | Per-line UBCC serialization; dedup via epoch |
