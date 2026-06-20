# FV-6: Snoop Handling Correctness Matrix

> **Source code baseline**: `EPRNFController.cc:331-795, 866-943, 1348-1407`, `EPBackend.cc:1530-1600, 1635-1892`, `EPRNFController.hh:241-443`
> **Date**: 2026-06-20

---

## 1. Snoop Response Matrix

| Snoop Type | Condition | Response Sent | Code Location | Classification (Q3=B) | Match? |
|---|---|---|---|---|---|
| **SnpCleanInvalid** | Upgrade pending (`_upgradePending.valid == true`) | `SnpResp_I` (deferred — sent via `receiveUpgradeAck()`) | `L669-680` | **Deferred** (until OuterUpgradeAck(true)) | ✅ |
| | First arrival, DSM line, `lastUpgradeAck().accepted == true` (fast path) | `SnpResp_I` (deferred — `receiveUpgradeAck()` called immediately) | `L695-732`, `L723-725` | **Deferred** (fast ack) | ✅ |
| | First arrival, DSM line, `lastUpgradeAck().accepted == false` (slow path) | `SnpResp_I` (deferred — wait `notifyUpgradeAckReady()`) | `L695-732`, `L727-731` | **Deferred** (wait invalidation acks) | ✅ |
| | First arrival, DSM line, `notifyLocalWriteUpgrade() == false` | `false` (snoop NOT consumed — retried later) | `L700-708` | **Deferred** (retry on rejection) | ✅ |
| | Non-upgrade (not DSM line, no pending) | `SnpResp_I` (immediate via `sendSnpRespI()`) | `L735-743` | **Immediate** | ✅ |
| | Queued (CHI txn in-flight), then processed | Same as above (dependent on context at process time) | `L349-368`, `L866-894` | **Queued until CHI txn completes** | ✅ |
| **SnpUnique** | `m_retToSrc == true` | `SnpResp_I` (NO data — _code mismatch with comment_) | `L768-783` | **Immediate** | ⚠️ (see §2.1) |
| | `m_retToSrc == false` | `SnpResp_I` (immediate via `sendSnpRespI()`) | `L781-783` | **Immediate** | ✅ |
| | Queued (CHI txn in-flight) | Same as above (processed after CHI txn) | `L349-368`, `L866-894` | **Queued** | ✅ |
| **SnpOnce** | Always | `SnpRespData_SC` (response + zero data beats) | `L789-795`, `L830-861` | **Immediate** | ✅ |
| | Queued (CHI txn in-flight) | Same as above (processed after CHI txn) | `L349-368`, `L866-894` | **Queued** | ✅ |
| **SnpShared** | Always (F4 diagnostic — should be unreachable) | `SnpResp_SC` (defensive preserving response) | `L634-644` | **Immediate** (error path) | ⚠️ (see §2.2) |
| **SnpSharedFwd** | Always (F4 diagnostic — should be unreachable) | `SnpResp_SC` (defensive preserving response) | `L634-644` | **Immediate** (error path) | ⚠️ (see §2.2) |
| **Unknown snoop type** | Default | `SnpResp_I` (fallback) | `L645-653` | **Immediate** | ✅ |

### Legend
- ✅ — Actual behavior matches the classified delay design (Q3=B: upgrade-deferred, recall-queued, others-immediate)
- ⚠️ — Deviations or risks requiring attention

---

## 2. Flagged Deviations & Code Path Risks

### 2.1 `handleSnpUnique`: Comment says `SnpRespData_I`, Code sends `SnpResp_I`

**File**: `EPRNFController.cc:756-782`

```
// Comment L756-L762:  retToSrc && hasData && !isDirty → SnpRespData_I
//                     retToSrc && !hasData → SnpResp_I
//                     !retToSrc → SnpResp_I
// Code  L768-L783:  if retToSrc → SnpResp_I     <-- mismatches comment
//                    else → SnpResp_I            <-- matches
```

The comment at L771 states *"We return `SnpRespData_I` to indicate successful invalidation without dirty data (PD=pass dirty=false)"*, but the actual message constructed at L776-779 uses `CHIResponseType_SnpResp_I`. The `CHIResponseType_SnpRespData_I` type exists in `CHI-msg.sm:220` (`CHIDataType`) but is never sent by EP-RNF.

**Risk**: When HN-F sends SnpUnique with `retToSrc=true`, it expects the snoopee to return data. EP-RNF sends `SnpResp_I` (no data). The HN-F may hang waiting for `SnpRespData` beats that never arrive, or may correctly treat EP-RNF's data-less response as completion. The CHI spec (§4.6.3) requires `SnpRespData_I` (or `_PD`) for `retToSrc=true` cases.

**Diagnosis**: This is intentional ("EP-RNF has no data of its own", L765). However, it relies on HN-F tolerating `SnpResp_I` where `SnpRespData_I` is spec-mandated. If HN-F transitions are strict, this will cause a protocol deadlock.

### 2.2 SnpShared/SnpSharedFwd: Defensive `SnpResp_SC` (F4 diagnostic)

**File**: `EPRNFController.cc:634-644`

These snoop types are explicitly labeled as F4 diagnostics — "should be unreachable but init-phase page-table setup triggers them." The preserving response `SnpResp_SC` is an acknowledged workaround pending root-cause fix.

**Risk**: If a legitimate SnpShared arrives during normal operation (not init-phase), the EP-RNF returns `SnpResp_SC` (Shared Clean) instead of the correct `SnpResp_I` (Invalid). This means the HN-F believes the EP-RNF still holds the line in SC state, potentially causing coherence violations.

### 2.3 Upgrade Path / CHI Txn Interaction — Snoop Queue Context Loss

**File**: `EPRNFController.cc:346-368`, `EPRNFController.cc:657-743`

When a CHI transaction is in-flight for a PA (e.g., a `CleanUnique` from invalidation fanout), incoming snoops are queued in the 1-entry per-PA slot. When the CHI txn completes and the queued snoop is processed, the `handleSnpCleanInvalid` handler re-evaluates `_upgradePending` and `isDsmLine`. There are two edge cases:

| Scenario | Sequence | Outcome |
|---|---|---|
| **A**: First SnpCleanInvalid initiates upgrade → `_upgradePending` set → invalidation fanout creates CleanUnique CHI txn → second SnpCleanInvalid arrives | CHI txn in-flight → snoop queued → CleanUnique completes → processQueuedSnoop → `_upgradePending` found | ✅ Correct — deferred SnpResp_I |
| **B**: CleanUnique CHI txn from unrelated cause (e.g., remote sharer invalidation) is in-flight → SnpCleanInvalid arrives | CHI txn in-flight → snoop queued → CleanUnique completes → processQueuedSnoop → NO `_upgradePending`, `isDsmLine` may trigger new upgrade | ⚠️ May attempt spurious OuterUpgradeReq on a line already in transition |

**Scenario B risk**: If `isDsmLine` is true but the CleanUnique was triggered by a remote sharer's invalidation (not a local upgrade), `handleSnpCleanInvalid` will call `notifyLocalWriteUpgrade()` at L695-698. The UBCC should reject this (line already being upgraded by another node), causing `accepted=false` → `handleSnpCleanInvalid` returns `false` → snoop is NOT consumed and will be retried. This is technically safe but wastes a round-trip.

**Worse case**: If `isDsmLine` is false (non-cross-node line) but `_upgradePending` is also absent, the non-upgrade path fires (L735-743) with a warning *"local upgrade path is disconnected"*. This is a correctness concern: the SnpCleanInvalid is acknowledged with `SnpResp_I` even though the line may NOT have been properly transitioned through the outer protocol.

### 2.4 ReceiveUpgradeAck Context Loss Warning

**File**: `EPRNFController.cc:1354-1363`

```
if (upIt == _upgradePending.end() || !upIt->second.valid) {
    warn("EP_RNF node_id=%d: receiveUpgradeAck PA=0x%lx lost upgrade "
         "context before deferred SnpResp_I\n", ...);
    return;
}
```

If `_upgradePending` is erased between SnpCleanInvalid deferral and `receiveUpgradeAck()` arrival, the deferred `SnpResp_I` is **never sent**. The `_upgradePending` is only erased in `receiveUpgradeAck()` itself (L1406), so this should not happen in normal operation. However, if any other code path clears it (e.g., a reset or timeout), the HN-F will hang waiting for the snoop response.

### 2.5 Snoop Queue Overflow Fatal (1-Entry Slot)

**File**: `EPRNFController.cc:352-357`

```
if (txnIt->second.snoopSlotValid) {
    fatal("... second snoop for PA=0x%lx while snoop slot already occupied "
          "— protocol violation (HN-F single-flight assumption broken)");
}
```

The 1-entry per-PA snoop slot is a hard limit. If HN-F violates the single-flight assumption and sends a second snoop before the first queued snoop is processed, the simulation fatals. This is by design but means any multi-snoop scenario will crash.

---

## 3. Comparison Against Classified Delay Design (Q3=B)

The Q3=B design classifies snoop handling into three delay categories:

| Category | Design Rule | Implementation Status |
|---|---|---|
| **Upgrade-deferred** | SnpCleanInvalid deferred until `OuterUpgradeAck(true)` received | ✅ Implemented via `_upgradePending` → `receiveUpgradeAck()` flow. Two sub-paths: fast (immediate ack, L723-725) and slow (wait `notifyUpgradeAckReady`, L727-731) |
| **Recall-queued** | Snoops arriving during in-flight CHI recall txn are queued in 1-entry per-PA slot | ✅ Implemented via `recvSnoopMsg` L346-368, processed by `processQueuedSnoop` L866-894 after `finishChiTxn` |
| **Others-immediate** | SnpUnique, SnpOnce processed immediately (no deferral) | ✅ Match for both SnpUnique and SnpOnce (with SnpUnique retToSrc caveat, see §2.1) |

**Overall compliance**: The implementation matches the Q3=B classification design intent, with the one code-comment mismatch for SnpUnique/retToSrc being the main concern.

---

## 4. Recommended Instrumentation Points for Runtime Verification

Insert `printf` / `DPRINTF` probes at the following points to enable test assertions:

| # | Location (file:line) | Instrumentation | Purpose |
|---|---|---|---|
| **P1** | `EPRNFController.cc:669` | `printf("[SNOOP-DIAG] node=%d SnpCleanInvalid upgrade_pending PA=0x%lx\\n", ...)` | Confirm upgrade path taken |
| **P2** | `EPRNFController.cc:735` | `printf("[SNOOP-DIAG] node=%d SnpCleanInvalid non-upgrade PA=0x%lx\\n", ...)` | Confirm non-upgrade path (also verify `isDsmLine` result) |
| **P3** | `EPRNFController.cc:723` | `printf("[SNOOP-DIAG] node=%d SnpCleanInvalid fast-ack PA=0x%lx\\n", ...)` | Fast path: immediate `receiveUpgradeAck()` |
| **P4** | `EPRNFController.cc:727-731` | `printf("[SNOOP-DIAG] node=%d SnpCleanInvalid deferred-ack PA=0x%lx\\n", ...)` | Slow path: wait for `notifyUpgradeAckReady()` |
| **P5** | `EPRNFController.cc:700-708` | `printf("[SNOOP-DIAG] node=%d SnpCleanInvalid REJECTED PA=0x%lx — retrying\\n", ...)` | Upgrade req rejected — snoop not consumed |
| **P6** | `EPRNFController.cc:768-782` | `printf("[SNOOP-DIAG] node=%d SnpUnique retToSrc=%d PA=0x%lx sends SnpResp_I (NOT SnpRespData_I)\\n", ...)` | Verify retToSrc behavior (flag if HN-F expects data) |
| **P7** | `EPRNFController.cc:789-795` | `printf("[SNOOP-DIAG] node=%d SnpOnce PA=0x%lx SnpRespData_SC\\n", ...)` | Confirm SnpOnce always returns data |
| **P8** | `EPRNFController.cc:352-357` | `printf("[SNOOP-DIAG] node=%d FATAL snoop-queue overflow PA=0x%lx\\n", ...)` | Detect HN-F single-flight violation (currently fatal — demote to warn for test) |
| **P9** | `EPRNFController.cc:1354-1363` | `printf("[SNOOP-DIAG] node=%d receiveUpgradeAck MISSING context PA=0x%lx\\n", ...)` | Detect orphaned `receiveUpgradeAck()` — would hang HN-F |
| **P10** | `EPBackend.cc:1570-1583` | `printf("[SNOOP-DIAG] node=%d startCleanUnique PA=0x%lx callback ok=%d — sending OuterInvalidationAck\\n", ...)` | Verify invalidation ack path from CleanUnique callback |
| **P11** | `EPBackend.cc:1865-1892` | `printf("[SNOOP-DIAG] node=%d notifyUpgradeAckReady PA=0x%lx -> receiveUpgradeAck(0x%lx)\\n", ...)` | Confirm upgrade ack notification → deferred SnpResp_I trigger |
| **P12** | `EPRNFController.cc:866-894` | `printf("[SNOOP-DIAG] node=%d processing queued snoop type=%d PA=0x%lx\\n", ...)` | Verify queued snoop replayed after CHI txn completion |
| **P13** | `EPRNFController.cc:634-644` | `printf("[SNOOP-DIAG] node=%d SnpShared/SnpSharedFwd F4 defensive SnpResp_SC PA=0x%lx\\n", ...)` | Track F4 diagnostic hits |
| **P14** | `EPRNFController.cc:900-943` | `printf("[SNOOP-DIAG] node=%d finishChiTxn PA=0x%lx success=%d hadQueuedSnoop=%d\\n", ...)` | Entry/exit of `finishChiTxn` with queued-snoop status |

### Test assertions for automated verification

```
# Expected invariants (pseudocode — place in test harness):
assert(   snoop_type == SnpCleanInvalid
       && (_upgradePending[addr].valid || isDsmLine(addr))
   → SnpResp_I is deferred, never immediate
)

assert(   snoop_type == SnpSnpUnique && msg->m_retToSrc
   → response is SnpResp_I           # NOTE: spec says SnpRespData_I — see §2.1
)

assert(   snoop_type == SnpOnce
   → response == SnpRespData_SC
)

assert(   count_pendingChiTxns_for_addr(addr) == 0
       OR snoop_was_queued(addr)
   → no fatal from snoop queue overflow
)
```

---

## 5. Summary

| Aspect | Verdict |
|---|---|
| **SnpCleanInvalid upgrade path** | ✅ Matches Q3=B: deferred until OuterUpgradeAck(true). Both fast and slow ack paths implemented. |
| **SnpUnique immediate** | ✅ Matches Q3=B: always immediate. ⚠️ Comment/code mismatch for retToSrc case — code sends `SnpResp_I` where spec says `SnpRespData_I`. |
| **SnpOnce immediate** | ✅ Always `SnpRespData_SC` with zero-fill data. |
| **Recall snoop queuing** | ✅ 1-entry per-PA slot during CHI txn in-flight. Fatal on overflow. |
| **Upgrade + CHI txn overlap** | ⚠️ Edge case B (§2.3) may trigger spurious upgrade attempt if SnpCleanInvalid arrives during unrelated CleanUnique. Safe but wasteful. |
| **Lost deferred SnpResp_I** | ⚠️ (§2.4) `receiveUpgradeAck()` with no `_upgradePending` context silently drops the deferred response. |
| **SnpShared/SnpSharedFwd** | ⚠️ Known F4 gap — defensive `SnpResp_SC` masks potential coherence violation. |
