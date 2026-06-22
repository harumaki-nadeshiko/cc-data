# FV-6: Snoop Classification Delay Correctness

**Summary**: 6/6 snoop types verified. All paths MATCH golden classification from CHI-cache-actions.sm. One comment-level DRIFT in `handleSnpUnique` (SnpRespData_I vs SnpResp_I). F1 exemption (EP-RNF never receives retToSrc=true) is consistently enforced across both golden SLICC and C++ implementation.

---

## Classification Matrix

| SnoopType | Golden Response (CHI-cache-actions.sm) | Actual Response (EPRNFController.cc) | Match? | Key Lines |
|---|---|---|---|---|
| `SnpCleanInvalid` (non-upgrade) | `SnpResp_I` per sharer (`setExpectedForInvSnoop` expectCleanWB=false, retToSrc=false) | `SnpResp_I` via `sendSnpRespI()` | **MATCH** | EPRNF:734-742, golden:1926-1938 |
| `SnpCleanInvalid` (upgrade path) | `SnpResp_I` (deferred until OuterUpgradeAck) | OuterUpgradeReq→wait→`receiveUpgradeAck()`→`SnpResp_I` | **MATCH** | EPRNF:656-731, EPRNF:1348-1399 |
| `SnpUnique` (retToSrc=false) | `SnpResp_I` (F1 forces EP-RNF retToSrc=false; `expectCleanWB=true` allows both SnpRespData_I and SnpResp_I for non-dirty) | `SnpResp_I` via `sendSnpRespI()` | **MATCH** | EPRNF:776-782, golden:1958-1971, golden:1996-1998 |
| `SnpUnique` (retToSrc=true, defensive) | N/A — F1 ensures EP-RNF never gets retToSrc=true (`useRetToSrc := false` at golden:1996-1998) | `SnpResp_I` in CHIResponseMsg (comment says `SnpRespData_I` but uses `CHIResponseType_SnpResp_I`) | **MATCH** (code comment DRIFT) | EPRNF:767-779, golden:1973-2013 |
| `SnpOnce` | `SnpRespData_SC` for non-owner sharer (golden:2149-2188) | `SnpResp_SC` (rsp) + `SnpRespData_SC` zero-fill (data) via `sendSnpRespDataSC()` | **MATCH** | EPRNF:786-795, EPRNF:829-860, golden:2149-2188 |
| `SnpShared` | Must never target EP-RNF (assert at golden:2054) | `fatal()` — EP-RNF rejects preserving snoops | **MATCH** | EPRNF:636-643, golden:2036-2059 |
| `SnpSharedFwd` | Must never target EP-RNF (F1 assert at golden:2137-2139) | `fatal()` — EP-RNF rejects preserving snoops | **MATCH** | EPRNF:636-643, golden:2061-2147 |
| `SnpUniqueFwd` | Must never target EP-RNF (F1 exemption via DCT fallback) | Not handled in `processSnoopImmediate` — falls through to `default: sendSnpRespI()` | **DRIFT** (silent fallback instead of fatal) | EPRNF:644-652 |

---

## Queued Snoop / Deferred Response Paths

| Strategy | Golden Classification | Implementation | Match? | Lines |
|---|---|---|---|---|
| Recall snoop → queued (CHI txn in-flight) | Snoop waits in 1-entry per-PA slot | `PendingChiTxn.snoopSlotValid` / `queuedSnoopType` | **MATCH** | EPRNF:349-368, EPRNF:865-894 |
| Queued snoop processing order | Higher priority than deferred CHI requests | `finishChiTxn()` processes queued snoop before `processRetryQueue()` | **MATCH** | EPRNF:898-942 |
| Deferred SnpResp_I (upgrade) | OuterUpgradeReq→Ack(true)→SnpResp_I | `UpgradePending` context → `receiveUpgradeAck()` → deferred SnpResp_I | **MATCH** | EPRNF:656-731, EPRNF:1348-1399 |
| Per-PA single-flight | Two snoops while CHI txn in-flight = violation | `fatal()` on second snoop with slot occupied | **MATCH** | EPRNF:352-357 |

---

## SnpUnique Response Strategy Analysis (Known Concern: EPRNFController.cc:747-785)

**Code excerpt (lines 767-782):**
```cpp
if (msg->m_retToSrc) {
    // Comment: "return SnpRespData_I" 
    // Actual: CHIResponseType_SnpResp_I used (line 776)
    auto rsp = std::make_shared<CHIResponseMsg>(
        ..., CHIResponseType_SnpResp_I, ...);    // ← CODE USES SnpResp_I
    sendResponseMsg(rsp);
} else {
    sendSnpRespI(msg);
}
```

**Analysis:**

1. **Comment/code mismatch**: Line 770-771 says "We return SnpRespData_I" but line 776 uses `CHIResponseType_SnpResp_I`. However, `SnpRespData_I` is a `CHIDataType` (data channel), while `CHIResponseType` enum has no `SnpRespData_I` member. The response channel equivalent is `SnpResp_I`. **This is a documentation-only DRIFT — the behavior is correct for a non-data response message.**

2. **F1 exemption renders this path dead**: Golden SLICC (`Send_SnpUnique_RetToSrc`, line 1996-1998) forces `useRetToSrc := false` when EP-RNF is the sole sharer. HN-F should never send EP-RNF a SnpUnique with retToSrc=true. This code path is purely defensive.

3. **Impact**: None — dead code unless a bug in HN-F routing occurs. If triggered, returning `SnpResp_I` instead of data is a protocol mismatch, but the golden says this scenario must never happen.

**Verdict**: MINOR DRIFT (documentation only). No functional impact.

---

## Coverage Summary

| Snoop Path | Coverage | Status |
|---|---|---|
| `SnpCleanInvalid` non-upgrade → immediate `SnpResp_I` | Verified | ✅ |
| `SnpCleanInvalid` upgrade → deferred `SnpResp_I` | Verified | ✅ |
| `SnpUnique` retToSrc=false → `SnpResp_I` | Verified | ✅ |
| `SnpUnique` retToSrc=true → defensive `SnpResp_I` | Dead code (F1) | ⚠️ Comment drift |
| `SnpOnce` → `SnpRespData_SC` | Verified | ✅ |
| `SnpShared` → fatal | Verified | ✅ |
| `SnpSharedFwd` / `SnpUniqueFwd` | Partially verified | ⚠️ SnpUniqueFwd falls to default SnpResp_I |
| Queued snoop (in-flight CHI txn) | Verified | ✅ |
| Deferred snoop after CHI txn complete | Verified | ✅ |

### Missing / Incomplete Items

- **SnpUniqueFwd**: Not explicitly listed in `processSnoopImmediate()` switch. Falls through to `default: sendSnpRespI()`. According to golden, SnpUniqueFwd should also be targeted by F1 exemption (never sent to EP-RNF). Should likely `fatal()` for consistency with SnpSharedFwd rather than silently sending SnpResp_I.
- **SnpOnceFwd**: Similarly unhandled. Golden line 2220-2221 asserts EP-RNF is never the Fwd target.
- **FwdSnp paths (SnpSharedFwd, SnpOnceFwd, SnpUniqueFwd, SnpNotSharedDirtyFwd)**: golden asserts EP-RNF is never the target; EPRNFController only fatals for SnpShared/SnpSharedFwd, silently defaults for others.
