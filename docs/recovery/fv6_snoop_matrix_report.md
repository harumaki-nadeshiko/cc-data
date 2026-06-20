# FV-6: Snoop Handling Correctness Matrix

> **Source code baseline**: `EPRNFController.cc:331-795, 866-943, 1348-1407`, `EPBackend.cc:1530-1600, 1635-1892`, `EPRNFController.hh:241-443`, `UBCCController.cc:1739-1955`, `CHI-cache-actions.sm:1973-2228`
> **Date**: 2026-06-20

---

## 1. Snoop Response Matrix

Each row shows: snoop type → HN-F expected response (from CHI-cache-actions.sm) → EP-RNF actual response → classification.

| # | Snoop Type | HN-F Expected Response (SLICC) | EP-RNF Actual Response | Code Location | Classification | Match? |
|---|---|---|---|---|---|---|
| **1** | **SnpCleanInvalid** — non-upgrade | `SnpResp_I` (via `setExpectedForInvSnoop(tbe,false)`) | `SnpResp_I` via `sendSnpRespI()` | `EPRNFController.cc:735-743` | **Immediate** | ✅ |
| **2** | **SnpCleanInvalid** — upgrade pending | `SnpResp_I` | `SnpResp_I` (deferred until `receiveUpgradeAck()`) | `EPRNFController.cc:669-680` | **Deferred** (upgrade) | ✅ |
| **3** | **SnpCleanInvalid** — upgrade first arrival, fast ack (targetMask=0) | `SnpResp_I` | `SnpResp_I` (deferred, `receiveUpgradeAck()` called inline) | `EPRNFController.cc:721-725` | **Deferred** (upgrade, immediate ack) | ✅ |
| **4** | **SnpCleanInvalid** — upgrade first arrival, slow ack (targetMask≠0) | `SnpResp_I` | `SnpResp_I` (deferred, wait `notifyUpgradeAckReady()`) | `EPRNFController.cc:727-731` | **Deferred** (upgrade, wait inval) | ✅ |
| **5** | **SnpCleanInvalid** — upgrade rejected by UBCC | `SnpResp_I` | `false` (snoop not consumed, retried later) | `EPRNFController.cc:700-708` | **Deferred** (retry on reject) | ✅ |
| **6** | **SnpUnique** — `retToSrc=false` (via `Send_SnpUnique` or `Send_SnpUnique_RetToSrc` with F1 mitigation) | `SnpResp_I` or `SnpRespData_I` (via `setExpectedForInvSnoop(tbe,true)`) | `SnpResp_I` via `sendSnpRespI()` | `EPRNFController.cc:781-783` | **Immediate** | ✅ |
| **7** | **SnpUnique** — `retToSrc=true` (should be impossible for EP-RNF due to F1 mitigation, see §2.1) | `SnpRespData_I` (expectCleanWB) | `SnpResp_I` (dead code — F1 prevents this path) | `EPRNFController.cc:768-780` | **Immediate** (dead) | ⚠️ (see §2.1) |
| **8** | **SnpOnce** (non-owner sharer fetch, or EP-RNF sole sharer via DCT fallback) | `SnpRespData_SC` | `SnpResp_SC` + `SnpRespData_SC` (zero data) | `EPRNFController.cc:789-795, 830-861` | **Immediate** | ✅ |
| **9** | **SnpOnceFwd** (Fwd variant) | `SnpResp_SC_Fwded_I` | Never received — EP-RNF excluded via assert in `Send_SnpOnceFwd` | `CHI-cache-actions.sm:2188-2190` | N/A | ✅ |
| **10** | **SnpShared** (preserving snoop — should be unreachable) | `SnpRespData_SC` / `SnpRespData_SC_PD` | `SnpResp_SC` (defensive, F4 diagnostic) | `EPRNFController.cc:634-644` | **Immediate** (error path) | ⚠️ (see §2.2) |
| **11** | **SnpSharedFwd** (forwarding variant) | Various `SnpResp*_Fwded_*` types | Never received — assert in `Send_SnpSharedFwd_ToSharer`: `fwdDest != epRnfMachineID` | `CHI-cache-actions.sm:2117-2119` | N/A | ✅ |
| **12** | **SnpUniqueFwd** (forwarding invalidation) | `SnpResp_I_Fwded_UC` / `SnpResp_I_Fwded_UD_PD` | Never received — `Send_SnpUniqueFwd` sends to `dir_sharers` via non-Fwd DCT fallback | `CHI-cache-actions.sm:2018-2029` | N/A | ✅ |
| **13** | **Unknown snoop** | Varies | `SnpResp_I` (fallback) | `EPRNFController.cc:645-653` | **Immediate** | ✅ |

### Helper functions

| Helper | Response Sent | Used For |
|---|---|---|
| `sendSnpRespI()` | `CHIResponseType_SnpResp_I` | SnpCleanInvalid (non-upgrade), SnpUnique (retToSrc=false), unknown |
| `sendSnpRespSC()` | `CHIResponseType_SnpResp_SC` | SnpShared/SnpSharedFwd defensive (F4 diagnostic) |
| `sendSnpRespDataSC()` | `CHIResponseType_SnpResp_SC` + `CHIDataType_SnpRespData_SC` (zero data) | SnpOnce |

### Expected vs Actual Response Detail per `setExpectedForInvSnoop()`

The HN-F uses `setExpectedForInvSnoop(tbe, expectCleanWB)` (defined in `CHI-cache-funcs.sm:1101`) to set expectations:

| `expectCleanWB` | `dataMaybeDirtyUpstream` | Expected Types | Count |
|---|---|---|---|
| `false` (SnpCleanInvalid) | `true` (owner exists) | `SnpRespData_I_PD` + possibly `SnpResp_I` | `dir_sharers.count()` |
| `false` (SnpCleanInvalid) | `false` (no owner, sharers only) | `SnpResp_I` | `dir_sharers.count()` |
| `true` (SnpUnique) | `true` (owner exists) | `SnpRespData_I` + `SnpRespData_I_PD` + possibly `SnpResp_I` | `dir_sharers.count()` |
| `true` (SnpUnique) | `false` (no owner, sharers only) | `SnpRespData_I` + `SnpResp_I` | `dir_sharers.count()` |

**Key observation**: When EP-RNF is sole sharer and `dataMaybeDirtyUpstream==false`, the HN-F accepts either `SnpRespData_I` or `SnpResp_I`. EP-RNF sends `SnpResp_I`, which is in the union. ✅

---

## 2. Flagged Deviations & Code Path Risks

### 2.1 `handleSnpUnique` — `retToSrc=true` Dead Code (F1 Mitigation)

**File**: `EPRNFController.cc:747-783`, `CHI-cache-actions.sm:1973-2007`

The `handleSnpUnique()` handler has a branch for `msg->m_retToSrc == true` that sends `SnpResp_I` (response only, no data beat). The comment on lines 756-762 states the CHI spec requires `SnpRespData_I` for `retToSrc=true && hasData && !isDirty`, yet the code at L776-779 sends `CHIResponseType_SnpResp_I`.

**However**, this path is **dead code** due to the **F1 mitigation** in `Send_SnpUnique_RetToSrc` (CHI-cache-actions.sm:1993-1998):

```
bool useRetToSrc := true;
if (epRnfMachineVersion >= 0 && dest == tbe.epRnfMachineID) {
    useRetToSrc := false;  // EP-RNF never gets retToSrc=true
}
```

Additionally, when EP-RNF is the *sole* sharer and `snpNeedsData` triggers the RetToSrc path, `useRetToSrc=false` is sent. When EP-RNF is one of *multiple* sharers, the secondary message (L2007-2014) sends `retToSrc=false` to all remaining sharers including EP-RNF.

**Verdict**: The `retToSrc=true` branch is never executed for EP-RNF. The misleading comment/code should be cleaned up but poses no runtime risk.

### 2.2 SnpShared/SnpSharedFwd — Defensive SnpResp_SC (F4 Diagnostic)

**File**: `EPRNFController.cc:634-644`, `CHI-cache-actions.sm:2039-2060`

The F4 diagnostic path fires when `SnpShared` or `SnpSharedFwd` reaches EP-RNF. The HN-F has an assert preventing this (`assert(tbe.dir_owner != tbe.epRnfMachineID)` at L2054), but init-phase page-table setup triggers it.

**Response mismatch**:
- HN-F expects: `SnpRespData_SC` or `SnpRespData_SC_PD` (with data)
- EP-RNF sends: `SnpResp_SC` (response only, **no data beat**)

The `SnpResp_SC` response (without data) may cause the HN-F to wait indefinitely for data beats that never arrive. The F4 comment acknowledges this is a diagnostic workaround pending root-cause fix.

### 2.3 Snoop Queue Overflow — 1-Entry Per-PA Slot Fatal

**File**: `EPRNFController.cc:352-357`

```
if (txnIt->second.snoopSlotValid) {
    fatal("EP_RNF node_id=%d: second snoop for PA=0x%lx while "
          "snoop slot already occupied — protocol violation "
          "(HN-F single-flight assumption broken)");
}
```

The design relies on HN-F serializing snoops per-PA. If a CHI txn (ReadShared/CleanUnique/ReadUnique) is in-flight and a snoop arrives, it's queued. If a **second** snoop arrives before the first is processed, the simulation fatals. This is a hard limit — not a recoverable error.

**Risk**: Any scenario where HN-F issues multiple snoops to EP-RNF for the same PA while a CHI txn is active will crash.

### 2.4 Upgrade + CHI Txn Interaction — Spurious OuterUpgradeReq

**File**: `EPRNFController.cc:346-368, 657-743`

When a SnpCleanInvalid arrives while a CHI txn (e.g., CleanUnique from remote invalidation) is in-flight, the snoop is queued. After the CHI txn completes, `processQueuedSnoop()` re-evaluates. If `isDsmLine` is true but the CHI txn was unrelated to local upgrade, `handleSnpCleanInvalid` issues `notifyLocalWriteUpgrade()` — a spurious OuterUpgradeReq that UBCC will likely reject (duplicate upgrade). The snoop returns `false` in that case and must be retried.

**Worse case**: If `isDsmLine` is false (non-cross-node line) and `_upgradePending` is absent, the non-upgrade path fires with a warning *"local upgrade path is disconnected"* and sends immediate `SnpResp_I`. The outer protocol state may be inconsistent.

### 2.5 Upgrade Path — `receiveUpgradeAck()` Context Loss

**File**: `EPRNFController.cc:1348-1407`

If `_upgradePending` is erased between SnpCleanInvalid deferral and `receiveUpgradeAck()` arrival, the deferred `SnpResp_I` is **never sent**. The erase only occurs in `receiveUpgradeAck()` itself (L1406), so this should not happen in normal operation. However, a premature erase would cause HN-F to hang.

### 2.6 UBCC Upgrade Flow — Tentative Done Caching (D4)

**File**: `UBCCController.cc:1895-1927`

The `processOuterUpgradeDone()` handler has a TENTATIVE path for when `UpgradeDone` arrives before all invalidation acks complete:

```
if (ost->stage == OpStage::WAITING_ALL_ACKS) {
    // TENTATIVE: cache the Done tuple, do NOT commit yet
    ost->upgradeDoneArrived = true;
    ost->upgradeDoneEpoch = epoch;
    ost->upgradeDoneReqId = reqId;
    ost->upgradeSavedStage = ost->stage;
    return true; // accepted but not committed
}
```

When all acks later arrive (in `processInvalidationAck()`, L1366-1376), the cached Done is auto-committed:

```
if (ost->upgradeDoneArrived) {
    commitIntendedResult(entry, *ost);
    _directory.update(line_pa, entry);
    ...
    removeOutstanding(line_pa);
}
```

**Risk**: The tentative caching means the upgrade directory state transition is deferred. If the requester (EP-RNF) sends `UpgradeDone` early and then crashes before the invalidation acks complete, the UBCC has tentative state that will be committed once acks arrive — but the requester may have already moved on. This is marked TENTATIVE in the code.

### 2.7 `Send_SnpUnique_RetToSrc` — RetToSrc and EP-RNF Interaction (F1)

**File**: `CHI-cache-actions.sm:1973-2014`

When `Send_SnpUnique_RetToSrc` sends to EP-RNF:
1. If EP-RNF is sole sharer: `useRetToSrc=false` (L1996-1998), only one message sent
2. If EP-RNF is sole sharer but `Send_SnpUnique` (non-RetToSrc) is used instead: `retToSrc=false`, `setExpectedForInvSnoop(tbe,true)` expects `SnpRespData_I` or `SnpResp_I` — EP-RNF sends `SnpResp_I` ✅
3. If EP-RNF is one of multiple sharers and selected as primary dest: `useRetToSrc=false` for EP-RNF, separate `retToSrc=true` to actual owner ✅
4. If EP-RNF is one of multiple sharers but NOT selected as primary dest: `retToSrc=false` to remaining sharers including EP-RNF ✅

**The `setExpectedForInvSnoop(tbe, true)` always expects `SnpRespData_I` (via `expectCleanWB=true`)** regardless of `useRetToSrc`. Since EP-RNF sends `SnpResp_I` for `retToSrc=false`, and `SnpResp_I` is also in the expected union (when `dataMaybeDirtyUpstream==false`), this works. But it's relying on the union match.

### 2.8 CleanUnique Invalidation Callback — EPBackend `handleInvalidationRequest`

**File**: `EPBackend.cc:1530-1600`

The `handleInvalidationRequest()` correctly serializes with HN-F via `startCleanUnique()`: the invalidation ack is only sent after the CleanUnique callback fires. This is the fix for §4.2.4 (previously acked directly, bypassing HN-F).

**Callback flow**: `startCleanUnique` → CHI CleanUnique to HN-F → HN-F sends SnpCleanInvalid to other agents → HW collects responses → HN-F returns Comp_UC → EP-RNF callback fires → invalidation ack sent to UBCC.

**Edge case**: If `startCleanUnique` callback receives `ok=false`, the invalidation ack is still sent with `success=ok`. The UBCC receives a negative ack. The UBCC `processInvalidationAck` does not check `success` flag — it only checks node presence in targetMask. A negative ack with `success=false` is treated as a valid ack.

---

## 3. UBCC Upgrade Path — Full Sequence

The complete upgrade sequence involves four components: HN-F, EP-RNF, EPBackend, UBCC.

```
Step 1: HN-F sends SnpCleanInvalid → EP-RNF
Step 2: EP-RNF detects DSM line, calls EPBackend::notifyLocalWriteUpgrade()
Step 3: EPBackend sends OuterUpgradeReq → UBCC via UBAdapter
Step 4: UBCC::processOuterUpgradeReq():
        - Checks requester is committed sharer
        - Checks no outstanding for this PA
        - Allocates reservedEpoch
        - If targetMask==0: stage=WAITING_LOCAL_DONE, accepted=true
        - If targetMask!=0: stage=WAITING_ALL_ACKS, accepted=false, fans out invalidations
Step 5a (fast): targetMask==0 → EPBackend sees accepted=true → receiveUpgradeAck() → SnpResp_I → UpgradeDone
Step 5b (slow): targetMask!=0 → invalidations fan out → all acks arrive →
                processInvalidationAck() → UpgradeAckNotify → EPBackend::notifyUpgradeAckReady() →
                receiveUpgradeAck() → SnpResp_I → UpgradeDone
Step 6: UBCC::processOuterUpgradeDone() → commitIntendedResult()
```

**PB machine state transition at UBCC**:
| Stage | Meaning |
|---|---|
| `WAITING_ALL_ACKS` | Invalidation acks pending from other sharers |
| `WAITING_LOCAL_DONE` | All acks in (or no sharers), waiting for UpgradeDone |
| `DONE` | Committed; removed from outstanding map |

**Potential D4 issue**: If UpgradeDone arrives during `WAITING_ALL_ACKS` (before all invalidation acks), it's cached tentatively and committed later. The directory is NOT updated until all acks arrive and the cached Done is applied. Between the cached Done arrival and full ack completion, the UBCC's directory reflects the pre-upgrade state.

---

## 4. Comparison Against Classified Delay Design (Q3=B)

| Category | Design Rule | Implementation Status |
|---|---|---|
| **Upgrade-deferred** | SnpCleanInvalid deferred until `OuterUpgradeAck(true)` received | ✅ Two sub-paths: fast (immediate ack) and slow (wait invalidation acks via `notifyUpgradeAckReady`) |
| **Recall-queued** | Snoops arriving during in-flight CHI recall txn queued in 1-entry per-PA slot | ✅ `recvSnoopMsg` L346-368, `processQueuedSnoop` L866-894. Fatal on overflow. |
| **Others-immediate** | SnpUnique, SnpOnce processed immediately (no deferral) | ✅ SnpUnique: immediate `SnpResp_I`. SnpOnce: immediate `SnpRespData_SC`. |
| **HN-F F1 mitigations** | EP-RNF excluded from Fwd snoops and retToSrc=true paths | ✅ DCT fallback, `useRetToSrc=false` override, asserts in `Send_SnpOnceFwd`, `Send_SnpSharedFwd_ToSharer` |

---

## 5. Issues Summary

| ID | Severity | Component | Description | Status |
|---|---|---|---|---|
| **F1** | Low | CHI-cache-actions.sm / EPRNFController | EP-RNF excluded from retToSrc=true / Fwd snoops via mitigation. Dead code in `handleSnpUnique` retainToSrc branch. | Mitigated — asserts and DCT fallback active |
| **F4** | **High** | EPRNFController.cc | SnpShared/SnpSharedFwd reaches EP-RNF during init. Defensive `SnpResp_SC` without data may hang HN-F. | Open — root cause under investigation |
| **F6-1** | Medium | EPRNFController.cc | 1-entry snoop slot overflow fatal. If HN-F violates single-flight, simulation crashes. | By design — documented limitation |
| **F6-2** | Medium | EPRNFController.cc | Upgrade + CHI txn overlap may trigger spurious OuterUpgradeReq or disconnected non-upgrade path. | Safe but wasteful in most cases |
| **F6-3** | Low | EPRNFController.cc | `receiveUpgradeAck()` context loss silently drops deferred `SnpResp_I`. | Low probability — only erased in same function |
| **D4** | **High** | UBCCController.cc | Tentative Done caching before invalidation acks complete. Directory state out-of-sync until acks arrive. | Marked TENTATIVE — needs review |
| **F6-4** | Low | EPRNFController.cc | `handleSnpUnique` comment says `SnpRespData_I` but code sends `SnpResp_I` for `retToSrc=true` path. Dead code per F1. | Cosmetic — no runtime impact |
| **F6-5** | Low | EPBackend.cc | Invalidation ack sent with `success=false` if `startCleanUnique` callback fails. UBCC ignores success flag. | Potential invisible failure |

---

## 6. Recommended Instrumentation Points

| # | Location | Instrumentation | Purpose |
|---|---|---|---|
| P1 | `EPRNFController.cc:669` | Log upgrade_pending path taken | Confirm deferred SnpResp_I flow |
| P2 | `EPRNFController.cc:735` | Log non-upgrade path | Verify `isDsmLine` result |
| P3 | `EPRNFController.cc:721-725` | Log fast-ack receiveUpgradeAck() | Fast upgrade path tracking |
| P4 | `EPRNFController.cc:727-731` | Log deferred-ack wait | Slow upgrade path tracking |
| P5 | `EPRNFController.cc:700-708` | Log REJECTED snoop | Detect upgrade req rejection |
| P6 | `EPRNFController.cc:768-783` | Log SnpUnique retToSrc flag | Verify F1 mitigation (expect retToSrc=false always) |
| P7 | `EPRNFController.cc:789-795` | Log SnpOnce path | Confirm SnpRespData_SC |
| P8 | `EPRNFController.cc:352-357` | Log snoop queue overflow | Detect HN-F single-flight violation |
| P9 | `EPRNFController.cc:1354-1363` | Log receiveUpgradeAck context loss | Detect orphaned deferred SnpResp_I |
| P10 | `EPBackend.cc:1570-1583` | Log startCleanUnique callback | Verify invalidation ack serialization |
| P11 | `EPBackend.cc:1865-1892` | Log notifyUpgradeAckReady | Confirm upgrade ack notification |
| P12 | `EPRNFController.cc:866-894` | Log queued snoop processing | Verify queued snoop replay after CHI txn |
| P13 | `EPRNFController.cc:634-644` | Log F4 defensive SnpResp_SC | Track SnpShared diagnostic hits |
| P14 | `EPRNFController.cc:900-943` | Log finishChiTxn with queued-snoop status | Entry/exit of finishChiTxn |
| P15 | `UBCCController.cc:1895-1927` | Log tentative Done caching | Track D4 tentative Done path |
| P16 | `CHI-cache-actions.sm:1993-1998` | Log EP-RNF useRetToSrc override | Verify F1 mitigation activation |

---

## 7. Overall Verdict

| Aspect | Verdict |
|---|---|
| **SnpCleanInvalid upgrade path** | ✅ Deferred until OuterUpgradeAck(true). Fast and slow ack paths implemented. |
| **SnpUnique immediate** | ✅ Always immediate. F1 mitigation ensures retToSrc=false always. Dead code in retToSrc=true branch (cosmetic). |
| **SnpOnce immediate** | ✅ `SnpRespData_SC` with zero-fill data. |
| **SnpShared/SnpSharedFwd** | ⚠️ F4 diagnostic — defensive `SnpResp_SC` mask. Should never reach EP-RNF in normal operation. |
| **Recall snoop queuing** | ✅ 1-entry per-PA slot. Fatal on overflow. |
| **F1 mitigations (Fwd/retToSrc exclusion)** | ✅ DCT fallback, `useRetToSrc=false`, asserts in `Send_SnpOnceFwd`, `Send_SnpSharedFwd_ToSharer`. |
| **Upgrade + CHI txn overlap** | ⚠️ Edge case may trigger spurious upgrade or disconnected non-upgrade path. |
| **Lost deferred SnpResp_I** | ⚠️ `receiveUpgradeAck()` context loss silently drops response. |
| **D4 tentative Done caching** | ⚠️ UBCC caches UpgradeDone arriving before invalidation acks complete. Marks TENTATIVE. |
| **Overall design compliance** | ✅ Matches Q3=B classification for delay categories. |
