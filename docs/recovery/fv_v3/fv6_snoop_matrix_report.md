# FV6: Snoop Type → Response Matrix Report

> Generated from `EPRNFController.cc:625-795, 1348-1399` and `EPBackend.cc:1530-1600, 1634-1759`.
> Classifies each snoop as **immediate** (returns response in same tick) or **delayed** (waits for upstream ack/CHI txn completion), reports the exact `CHIResponseType`, and flags design issues.

## Snoop Dispatch Table (`processSnoopImmediate`, lines 627–653)

| Snoop Request | Handler | Response Msg Type(s) | Timing | Conditions / Notes |
|---|---|---|---|---|
| `SnpCleanInvalid` | `handleSnpCleanInvalid` | `CHIResponseType_SnpResp_I` | **Delayed** (upgrade path) / **Immediate** (non-upgrade) | **Non-upgrade** (no upgradePending, !isDsmLine): immediate `SnpResp_I` + warning about disconnected path. **First-arrival upgrade** (isDsmLine, no existing `_upgradePending`): sends `OuterUpgradeReq` → deferred until `OuterUpgradeAck(true)`. **Repeat upgrade** (isDsmLine, existing `_upgradePending`): defers `SnpResp_I` until existing `OuterUpgradeAck` arrives. |
| `SnpUnique` | `handleSnpUnique` | `CHIResponseType_SnpResp_I` | **Immediate** | If `retToSrc`: sends `SnpResp_I` directly (no data, EP-RNF has no dirty data). If `!retToSrc`: calls `sendSnpRespI()`. Always immediate `SnpResp_I`. |
| `SnpOnce` | `handleSnpOnce` | `CHIResponseType_SnpResp_SC` + `CHIDataType_SnpRespData_SC` | **Immediate** | Sends `SnpResp_SC` (response beat) followed by `SnpRespData_SC` (zero-filled data, EP-RNF has no cached data) — see `sendSnpRespDataSC()` lines 829–860. |
| `SnpShared` | **fatal** | — | N/A | Fatal error: "preserving snoops must not target EP-RNF" (lines 639–643). Formerly masked via diagnostic `SnpResp_SC` path. |
| `SnpSharedFwd` | **fatal** | — | N/A | Same fatal as `SnpShared`. |
| Unknown/default | `sendSnpRespI` | `CHIResponseType_SnpResp_I` | **Immediate** | Fallback path for unrecognized snoop types — unconditional `SnpResp_I` (lines 644–652). |

### Response Timing Decision Tree (SnpCleanInvalid as the only delayed snoop)

```
SnpCleanInvalid
  ├── isDsmLine == true ─────────────────────────────────┐
  │   ├── _upgradePending already valid (repeat snoop)   │
  │   │   └── Defer SnpResp_I until outer ack arrives    │
  │   │       (receiveUpgradeAck triggered by            │
  │   │        notifyUpgradeAckReady from UBCC)          │
  │   ├── first arrival: notifyLocalWriteUpgrade()       │
  │   │   ├── !accepted → return false (retry later)     │
  │   │   └── accepted →                                 │
  │   │       ├── lastUpgradeAck().accepted (targetMask  │
  │   │       │   ==0): immediate receiveUpgradeAck()   │
  │   │       │   → SnpResp_I sent NOW                  │
  │   │       └── targetMask !=0: deferred until all     │
  │   │           invalidation acks arrive →             │
  │   │           notifyUpgradeAckReady →                │
  │   │           receiveUpgradeAck() → SnpResp_I       │
  │   └── (end)                                          │
  └── isDsmLine == false ────────────────────────────────┐
      └── Immediate SnpResp_I + warning about             │
          disconnected upgrade path                       │
```

## Immediate vs Delayed Classification Summary

| Timing Class | Snoop Types | Count |
|---|---|---|
| **Immediate** | `SnpUnique`, `SnpOnce`, Unknown/default | 3 |
| **Delayed** | `SnpCleanInvalid` (upgrade path only) | 1 |
| **Fatal** | `SnpShared`, `SnpSharedFwd` | 2 |

## Upgrade Path Flow (SnpCleanInvalid, delayed)

The upgrade path (`§5.5`) chains:

1. **`notifyLocalWriteUpgrade()`** (EPBackend.cc:1634–1756)
   - Translates local PA → home PA
   - Allocates `epoch`, `reqId`
   - Sends `OuterUpgradeReq` via `UBAdapter::sendUpgradeReq()`
   - If `upgradeTargetMask != 0`: fans out `OuterInvalidateMsg` to each sharer bit
   - Sets `_lastUpgradeAck.accepted = false` (deferred)
   - If `upgradeTargetMask == 0`: immediate `_lastUpgradeAck.accepted = true`

2. **`receiveUpgradeAck()`** (EPRNFController.cc:1349–1399)
   - Looks up `_upgradePending` by PA
   - Sends deferred `CHIResponseType_SnpResp_I` to `hnfDest`
   - Calls `EPBackend::sendUpgradeDone()` to commit the epoch

3. **`notifyUpgradeAckReady()`** (EPBackend.cc:1862–1889)
   - Called by home UBCC when all invalidation acks collected
   - Translates home PA → local PA via address map
   - Triggers `_epRnfCtrl->receiveUpgradeAck(callbackPa)`

## Invalidation Path (CleanUnique for invalidation acks)

| Step | Location | Description |
|---|---|---|
| `handleInvalidationRequest()` | EPBackend.cc:1537–1597 | Receives `OuterInvalidateMsg` from fanout |
| `startCleanUnique()` | EPRNFController.cc:1184–1237 | Sends `CHIRequestType_CleanUnique` to HN-F with `EpProxyOp_InvalidateOnly` |
| Callback `[this, capturedMsg](bool ok)` | EPBackend.cc:1579–1588 | Sends `OuterInvalidationAck` to home |
| CleanUnique completion | EPRNFController.cc:898–942 (`finishChiTxn`) | Cleans up pending txn, then processes queued snoop |

## Queued Snoop Handling

When a CHI txn is in-flight for the same PA, the snoop is **queued** (stored in `PendingChiTxn::snoopSlotValid`). After `finishChiTxn()`, `processQueuedSnoop()` replays it through `processSnoopImmediate()`. This applies generically to any snoop type that arrives during an in-flight transaction.

## Issues and Anomalies

| # | Severity | Issue | Location | Description |
|---|---|---|---|---|
| **F1** | **Medium** | `SnpResp_SC` helper retained for non-fwd use only | `sendSnpRespSC()` lines 812–826 | Comment says "SnpResp_SC is only used for legitimate non-Fwd snoop responses" — but `sendSnpRespSC()` is never called from any snoop handler; `SnpOnce` uses `sendSnpRespDataSC()` instead, which sends both `SnpResp_SC` + `SnpRespData_SC`. Dead code or latent. |
| **F2** | **Low** | `snopSlot` spelling error | `PendingChiTxn::snoopSlotValid` (header) | Cosmetic typo in field name (`snop` vs `snoop`). |
| **F3** | **High** | Non-upgrade `SnpCleanInvalid` without `upgradePending` context | Line 739–742 | Warns "local upgrade path is disconnected" — implies a topology/configuration error where EP-RNF receives `SnpCleanInvalid` but DSM address map doesn't treat it as cross-node. Could indicate misconfigured address map. |
| **F4** | **High** | `SnpShared`/`SnpSharedFwd` silently injected in `selfTest()` | Lines 250, 637–643 | Self-test injects `SnpShared` (line 250) but the handler does `fatal()` — any node reaching self-test with a real `SnpShared` will crash. Was previously masked with diagnostic `SnpResp_SC` fallback. |
| **F5** | **Medium** | Only one snoop slot per pending txn | `PendingChiTxn::snoopSlotValid` (bool, lines 862–893) | If multiple snoops arrive for the same PA while a CHI txn is in flight, only the *last* one is retained. Earlier snoops are silently dropped. |
| **F6** | **Low** | Deferred `SnpResp_I` after `OuterUpgradeReq` rejection | Lines 699–706 | If `sendUpgradeReq` returns `!accepted`, the snoop returns `false` (not consumed). The controller must retry — relies on a retry mechanism. If `processSnoopImmediate` return value is ignored, the snoop is dropped. |
| **F7** | **Low** | `default` fallback for unknown snoops | Lines 644–652 | Sends `SnpResp_I` silently for any unrecognized snoop type — could mask protocol bugs in HN-F. |

## Response Message Types Used

| CHI Response Type | Used By | With Data? |
|---|---|---|
| `CHIResponseType_SnpResp_I` | `SnpCleanInvalid`, `SnpUnique`, unknown/default, deferred upgrade ack | No data |
| `CHIResponseType_SnpResp_SC` | `SnpOnce` (via `sendSnpRespDataSC` — response beat) | No data (control beat) |
| `CHIDataType_SnpRespData_SC` | `SnpOnce` (data beat, zero-filled) | Yes (zero-filled `DataBlock`) |
| `CHIResponseType_SnpRespData_I_PD` | Defined in spec but **not used** by EP-RNF | N/A |
| `CHIResponseType_SnpRespData_I` | Defined in spec but **not used** by EP-RNF | N/A |
