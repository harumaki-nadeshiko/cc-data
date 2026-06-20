# FV-10: Serialization Round-Trip Schema

**Summary:** UBMsg is a plain C++ struct (no explicit serialize/deserialize). Round-trip fidelity is by-value via `UBRouter::sendMessage`. This document catalogs the survival matrix across send→route→recv hops, a JSON schema for the wire format, and all instrument points where messages are constructed (serialized) and consumed (deserialized).

---

## 1. Survival matrix: fields that survive Adapter→UBRouter→UBCC→UBRouter→Adapter

Rows = message class. Columns = header fields. Cell = survives the full round-trip (✓), lost (—), or partially preserved (~).

| # | Message class (req→resp pair) | `type` | `srcNode` | `srcSocket` | `dstNode` | `dstSocket` | `homeNode` | `homeSocket` | `ingressSocket` | `requesterNode` | `targetNode` | `flags` | `homeLinePa` | `localLinePa` | `epoch` | `reqId` | `seqNum` | `enqueueTick` | `readyTick` | Body survives? |
|---|-------------------------------|--------|-----------|-------------|-----------|-------------|------------|--------------|-----------------|-----------------|--------------|---------|-------------|--------------|---------|---------|----------|--------------|-------------|----------------|
| 1 | ReadReq → ReadResp | ✓ | ✓(swapped) | ✓(swapped) | ✓(swapped) | ✓(swapped) | ✓(set) | ✓(set) | ~(echoed) | ✓ | — | ✓ | ✓ | — | ✓ | ✓ | —(0) | —(0) | —(0) | ✓(body reconstructed) |
| 2 | WritebackReq → WritebackResp | ✓ | ✓(swapped) | ✓(swapped) | ✓(swapped) | ✓(swapped) | — | — | — | — | — | ~(KEEP_AS_CLEAN read, not echoed) | ✓ | — | ✓ | ✓(echoed) | —(0) | —(0) | —(0) | ✓(`success` from UBCC) |
| 3 | EvictReq → EvictResp | ✓ | ✓(swapped) | ✓(swapped) | ✓(swapped) | ✓(swapped) | — | — | — | — | — | — | ✓ | — | ✓ | ✓(echoed) | —(0) | —(0) | —(0) | ✓(`success` from UBCC) |
| 4 | UpgradeReq → UpgradeResp | ✓ | ✓(swapped) | ✓(swapped) | ✓(swapped) | ✓(swapped) | — | — | — | — | — | ~(ACCEPTED set on resp) | ✓ | — | ✓ | ✓(echoed) | —(0) | —(0) | —(0) | ✓(body reconstructed) |
| 5 | UpgradeDoneReq → UpgradeDoneResp | ✓ | ✓(swapped) | ✓(swapped) | ✓(swapped) | ✓(swapped) | — | — | — | — | — | — | ✓ | — | ✓ | ✓(echoed) | —(0) | —(0) | —(0) | ✓(`accepted` from UBCC) |
| 6 | ClearReq → ClearResp | ✓ | ✓(swapped) | ✓(swapped) | ✓(swapped) | ✓(swapped) | — | — | — | — | — | — | ✓ | — | ✓ | ✓(echoed) | —(0) | —(0) | —(0) | ✓(`accepted` from UBCC) |
| 7 | QueryLineMetaReq → QueryLineMetaResp | ✓ | ✓(swapped) | ✓(swapped) | ✓(swapped) | ✓(swapped) | — | — | — | — | — | — | ✓ | — | ✓(echoed) | ✓(echoed) | —(0) | —(0) | —(0) | ✓(body reconstructed) |
| 8 | RecallReq (fire-and-forget) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓(IS_READ_RECALL,HAS_DATA) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | N/A (empty body) |
| 9 | RecallResp (fire-and-forget) | ✓ | ✓(swapped src↔home) | ✓(swapped) | ✓(swapped) | ✓(swapped) | ✓ | ✓ | ✓ | ✓ | — | ✓(DATA_RETURNED,HAS_DATA) | ✓ | — | ✓ | ✓ | ✓ | ✓ | ✓ | ✓(`data[64]`) |
|10 | InvalidateReq (fire-and-forget) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | N/A (empty body) |
|11 | InvalidateAck (fire-and-forget) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — | ✓ | — | ✓ | ✓ | ✓ | ✓ | ✓ | N/A (empty body) |
|12 | HomeWritebackNotify (fire-and-forget) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — | — | ✓ | — | ✓ | — | ✓ | ✓ | ✓ | N/A (body not read) |
|13 | UpgradeAckNotify (async, UBCC→Adapter) | ✓ | ✓ | — | ✓ | — | ✓ | — | — | ✓ | — | ✓(ACCEPTED) | ✓ | — | ✓ | ✓ | ✓(0) | ✓ | ✓ | N/A (empty body) |

> **Legend:** ✓ = survives; ✓(swapped) = field value swapped between request and response (e.g. src↔dst). — = field left at default (0) in response/fire-and-forget. ✓(echoed) = field copied from request to response verbatim. ~ = partially preserved.

### Round-trip field loss summary

| Lost field | Affected messages | Severity | Root cause |
|------------|-------------------|----------|------------|
| `seqNum` | All responses (ReadResp→ClearResp, QueryLineMetaResp) | 🟡 Medium | Not echo-copied; runtime-local |
| `enqueueTick`, `readyTick` | All responses | 🟡 Medium | Not echo-copied; runtime-local |
| `homeNode`, `homeSocket` | WritebackResp, EvictResp, UpgradeResp, UpgradeDoneResp, ClearResp, QueryLineMetaResp | 🟡 Medium | Omitted from response construction (I3) |
| `ingressSocket` | All responses | 🟡 Medium | Not copied to response (I4) |
| `localLinePa` | All responses, fire-and-forget recall/inval | 🟢 Low | Not echoed |
| `targetNode` | All sync responses | 🟢 Low | Not echoed |
| `requesterNode` | WritebackResp, EvictResp | 🟢 Low | Not echoed |
| `reqId` | WritebackReq, EvictReq | 🟢 Low | Never set on request (I5) |

---

## 2. JSON schema (UBMsg wire format)

No explicit serialization exists. Below is the logical schema representing the full struct as if serialized to JSON.

```jsonc
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "UBMsg",
  "description": "Coherent chiplet protocol message — plain C++ struct, passed by value through UBRouter",
  "type": "object",
  "properties": {
    "header": {
      "type": "object",
      "properties": {
        "type": {
          "type": "string",
          "enum": [
            "ReadReq", "ReadResp", "RecallReq", "RecallResp",
            "InvalidateReq", "InvalidateAck", "WritebackReq", "WritebackResp",
            "EvictReq", "EvictResp", "UpgradeReq", "UpgradeResp",
            "UpgradeDoneReq", "UpgradeDoneResp", "ClearReq", "ClearResp",
            "UpgradeAckNotify", "QueryLineMetaReq", "QueryLineMetaResp",
            "HomeWritebackNotify"
          ]
        },
        "srcNode":        { "type": "integer", "minimum": 0, "maximum": 65535 },
        "srcSocket":      { "type": "integer", "minimum": 0, "maximum": 65535 },
        "dstNode":        { "type": "integer", "minimum": 0, "maximum": 65535 },
        "dstSocket":      { "type": "integer", "minimum": 0, "maximum": 65535 },
        "homeNode":       { "type": "integer", "minimum": 0, "maximum": 65535 },
        "homeSocket":     { "type": "integer", "minimum": 0, "maximum": 65535 },
        "ingressSocket":  { "type": "integer", "minimum": 0, "maximum": 65535 },
        "requesterNode":  { "type": "integer", "minimum": 0, "maximum": 65535 },
        "targetNode":     { "type": "integer", "minimum": 0, "maximum": 65535 },
        "flags": {
          "type": "object",
          "description": "Bitmask; 7 defined flags",
          "properties": {
            "WRITE_INTENT":   { "type": "boolean", "description": "bit 0" },
            "KEEP_AS_CLEAN":  { "type": "boolean", "description": "bit 1" },
            "ACCEPTED":       { "type": "boolean", "description": "bit 2" },
            "DATA_RETURNED":  { "type": "boolean", "description": "bit 3" },
            "HAS_DATA":       { "type": "boolean", "description": "bit 4" },
            "IS_READ_RECALL": { "type": "boolean", "description": "bit 5" },
            "BUSY":           { "type": "boolean", "description": "bit 6, never set" }
          }
        },
        "homeLinePa":     { "type": "integer", "minimum": 0, "maximum": 18446744073709551615 },
        "localLinePa":    { "type": "integer", "minimum": 0, "maximum": 18446744073709551615 },
        "epoch":          { "type": "integer", "minimum": 0 },
        "reqId":          { "type": "integer", "minimum": 0 },
        "seqNum":         { "type": "integer", "minimum": 0, "description": "runtime-local, not preserved across hops" },
        "enqueueTick":    { "type": "integer", "description": "runtime-local" },
        "readyTick":      { "type": "integer", "description": "runtime-local" }
      },
      "required": ["type", "srcNode", "dstNode", "homeLinePa"]
    },
    "body": {
      "type": "object",
      "description": "Tagged union; the active member is determined by header.type",
      "oneOf": [
        {
          "properties": {
            "type": { "const": "ReadReq" },
            "body": {
              "type": "object",
              "properties": {
                "neededPerm": { "type": "integer", "minimum": 0, "maximum": 1 }
              },
              "required": ["neededPerm"]
            }
          }
        },
        {
          "properties": {
            "type": { "const": "ReadResp" },
            "body": {
              "type": "object",
              "properties": {
                "grantType":          { "type": "integer", "minimum": -1, "maximum": 2 },
                "dataSource":         { "type": "integer", "minimum": 0, "maximum": 2 },
                "pendingInvCount":    { "type": "integer", "minimum": -1 },
                "grantVisibleTick":   { "type": "integer" },
                "sentinelVisibleTick":{ "type": "integer" },
                "recallNeeded":       { "type": "boolean" },
                "recallOwnerNode":    { "type": "integer", "minimum": -1 },
                "authEpoch":          { "type": "integer" },
                "committedEpoch":     { "type": "integer" },
                "pendingInvMask":     { "type": "integer" },
                "grantData":          { "type": "array", "items": { "type": "integer", "minimum": 0, "maximum": 255 }, "minItems": 64, "maxItems": 64 }
              },
              "required": ["grantType"]
            }
          }
        },
        {
          "properties": {
            "type": { "enum": ["RecallReq", "InvalidateReq", "InvalidateAck", "WritebackReq", "EvictReq", "UpgradeDoneReq", "UpgradeAckNotify"] },
            "body": { "type": "object", "properties": {}, "description": "Empty — no extra body fields" }
          }
        },
        {
          "properties": {
            "type": { "const": "RecallResp" },
            "body": {
              "type": "object",
              "properties": {
                "data": { "type": "array", "items": { "type": "integer", "minimum": 0, "maximum": 255 }, "minItems": 64, "maxItems": 64 }
              },
              "required": ["data"]
            }
          }
        },
        {
          "properties": {
            "type": { "enum": ["WritebackResp", "EvictResp", "UpgradeDoneResp", "ClearResp"] },
            "body": {
              "type": "object",
              "properties": {
                "accepted": { "type": "boolean" },
                "success": { "type": "boolean" }
              }
            }
          }
        },
        {
          "properties": {
            "type": { "const": "UpgradeReq" },
            "body": {
              "type": "object",
              "properties": {
                "desiredPerm": { "type": "integer", "minimum": 0, "maximum": 255 },
                "cause":       { "type": "integer", "minimum": 0, "maximum": 255 }
              },
              "required": ["desiredPerm", "cause"]
            }
          }
        },
        {
          "properties": {
            "type": { "const": "UpgradeResp" },
            "body": {
              "type": "object",
              "properties": {
                "upgradeTargetMask": { "type": "integer" },
                "committedEpoch":    { "type": "integer" }
              },
              "required": ["upgradeTargetMask", "committedEpoch"]
            }
          }
        },
        {
          "properties": {
            "type": { "const": "ClearReq" },
            "body": {
              "type": "object",
              "properties": {
                "reason": { "type": "integer", "minimum": 0, "maximum": 255 }
              },
              "required": ["reason"]
            }
          }
        },
        {
          "properties": {
            "type": { "enum": ["QueryLineMetaReq", "HomeWritebackNotify"] },
            "body": {
              "type": "object",
              "properties": {
                "homePa": { "type": "integer", "description": "redundant with header.homeLinePa" }
              }
            }
          }
        },
        {
          "properties": {
            "type": { "const": "QueryLineMetaResp" },
            "body": {
              "type": "object",
              "properties": {
                "found":     { "type": "boolean" },
                "epoch":     { "type": "integer" },
                "ownerNode": { "type": "integer", "minimum": -1 }
              },
              "required": ["found", "epoch", "ownerNode"]
            }
          }
        }
      ]
    }
  },
  "required": ["header"]
}
```

### Byte-level layout (for binary serialization)

| Offset | Size | Field | Notes |
|--------|------|-------|-------|
| 0 | 2 | `type` (uint16_t enum) | 20 values, enum class UBMsgType |
| 2 | 2 | `srcNode` | uint16_t |
| 4 | 2 | `srcSocket` | uint16_t |
| 6 | 2 | `dstNode` | uint16_t |
| 8 | 2 | `dstSocket` | uint16_t |
|10 | 2 | `homeNode` | uint16_t |
|12 | 2 | `homeSocket` | uint16_t |
|14 | 2 | `ingressSocket` | uint16_t |
|16 | 2 | `requesterNode` | uint16_t |
|18 | 2 | `targetNode` | uint16_t |
|20 | 4 | `flags` | uint32_t |
|24 | 8 | `homeLinePa` | uint64_t |
|32 | 8 | `localLinePa` | uint64_t |
|40 | 8 | `epoch` | uint64_t |
|48 | 8 | `reqId` | uint64_t |
|56 | 8 | `seqNum` | uint64_t |
|64 | 8 | `enqueueTick` | Tick (uint64_t) |
|72 | 8 | `readyTick` | Tick (uint64_t) |
|80 | — | **Header end (80 bytes)** | |
|80 | varies | Body union | max member = ReadRespBody (~136 B) |

> **Total struct size:** ~216 bytes (header 80 + body union ~136). ReadResp with grantData[64] is the largest variant.

---

## 3. Send (serialization) instrument points

All send sites construct `UBMsg` on the stack, populate header+body fields, then call `_router->sendMessage(msg)`.

| # | Function (file:line) | Message type | Connector | Body fields set | Fire-and-forget? |
|---|----------------------|-------------|-----------|-----------------|-----------------|
| 1 | `UBAdapter::sendReadReq` (UBAdapter.cc:87–114) | ReadReq | Adapter→UBCC | `b.readReq.neededPerm` | No (waits for ReadResp) |
| 2 | `UBAdapter::sendWritebackReq` (UBAdapter.cc:190–209) | WritebackReq | Adapter→UBCC | *(empty)* | No (waits for WritebackResp) |
| 3 | `UBAdapter::sendEvictReq` (UBAdapter.cc:244–261) | EvictReq | Adapter→UBCC | *(empty)* | No (waits for EvictResp) |
| 4 | `UBAdapter::sendUpgradeReq` (UBAdapter.cc:300–321) | UpgradeReq | Adapter→UBCC | `b.upgradeReq.desiredPerm`, `b.upgradeReq.cause` | No (waits for UpgradeResp) |
| 5 | `UBAdapter::sendUpgradeDoneReq` (UBAdapter.cc:367–385) | UpgradeDoneReq | Adapter→UBCC | *(empty)* | No (waits for UpgradeDoneResp) |
| 6 | `UBAdapter::sendClearReq` (UBAdapter.cc:421–441) | ClearReq | Adapter→UBCC | `b.clearReq.reason` | No (waits for ClearResp) |
| 7 | `UBAdapter::sendRecallResp` (UBAdapter.cc:479–504) | RecallResp | Adapter→UBCC | `b.recallResp.data[64]` | ✅ Yes |
| 8 | `UBAdapter::sendInvalidateAck` (UBAdapter.cc:526–544) | InvalidateAck | Adapter→UBCC | *(empty)* | ✅ Yes |
| 9 | `UBAdapter::sendRecallReqToOwner` (UBAdapter.cc:564–589) | RecallReq | Adapter→Sharer | *(empty)* | ✅ Yes |
|10 | `UBAdapter::sendInvalidateReqToSharer` (UBAdapter.cc:608–628) | InvalidateReq | Adapter→Sharer | *(empty)* | ✅ Yes |
|11 | `UBAdapter::sendQueryLineMetaReq` (UBAdapter.cc:648–663) | QueryLineMetaReq | Adapter→UBCC | *(empty; body ignored)* | No (waits for QueryLineMetaResp) |
|12 | `UBAdapter::sendHomeWritebackNotify` (UBAdapter.cc:700–716) | HomeWritebackNotify | HN-F→UBCC | *(empty; body ignored)* | ✅ Yes |
|13 | `UBCCController::sendUpgradeAckNotify` (UBCCController.cc:1353–1367) | UpgradeAckNotify | UBCC→Adapter | *(empty)* | ✅ Yes |

### UBRouter response construction (re-serialization)

`UBRouter::deliverToUbcc` (UBRouter.cc:228–481) constructs response messages from UBCC results:

| # | Handler (UBRouter.cc line) | Request → Response | Body fields populated |
|---|----------------------------|-------------------|----------------------|
| R1 | ReadReq handler (line 278–314) | ReadReq → ReadResp | `grantType`, `dataSource`, `pendingInvCount`, `grantVisibleTick`, `sentinelVisibleTick`, `recallNeeded`, `recallOwnerNode`, `authEpoch`, `committedEpoch`, `pendingInvMask`, `grantData[64]` |
| R2 | WritebackReq handler (line 324–333) | WritebackReq → WritebackResp | `success` |
| R3 | EvictReq handler (line 341–350) | EvictReq → EvictResp | `success` |
| R4 | UpgradeReq handler (line 367–380) | UpgradeReq → UpgradeResp | `upgradeTargetMask`, `committedEpoch` |
| R5 | UpgradeDoneReq handler (line 388–397) | UpgradeDoneReq → UpgradeDoneResp | `accepted` |
| R6 | ClearReq handler (line 405–414) | ClearReq → ClearResp | `accepted` |
| R7 | QueryLineMetaReq handler (line 455–466) | QueryLineMetaReq → QueryLineMetaResp | `found`, `epoch`, `ownerNode` |

> **Note:** RecallResp, InvalidateAck, HomeWritebackNotify are fire-and-forget — no response is constructed.

---

## 4. Receive (deserialization) instrument points

All receive sites read `msg.h` fields and (for responses) `msg.b.*` fields from a previously sent request's matched response.

| # | Function (file:line) | Message types consumed | Fields read from `msg.h` | Fields read from `msg.b` |
|---|----------------------|----------------------|--------------------------|---------------------------|
| 1 | `UBAdapter::recvFromRouter` (UBAdapter.cc:722–808) | ReadResp | `h.type`, `h.homeLinePa`, `h.srcNode`, `h.epoch`, `h.reqId` | `b.readResp.grantType` |
| 2 | *same* | WritebackResp, EvictResp, UpgradeResp, UpgradeDoneResp, ClearResp, QueryLineMetaResp | `h.type` | *(stored whole to `_lastResponse`, caller reads specific fields)* |
| 3 | *same* | UpgradeAckNotify | `h.type`, `h.homeLinePa` | *(unused)* |
| 4 | *same* | RecallReq | `h.type`, `h.homeLinePa`, `h.localLinePa`, `h.targetNode`, `h.homeNode`, `h.epoch`, `h.reqId`, `h.flags` | *(none — reconstructed OuterRecallMsg)* |
| 5 | *same* | InvalidateReq | `h.type`, `h.homeLinePa`, `h.localLinePa`, `h.targetNode`, `h.homeNode`, `h.epoch`, `h.reqId` | *(none — reconstructed OuterInvalidateMsg)* |
| 6 | `UBAdapter::sendReadReq` (UBAdapter.cc:124–160) | ReadResp (via `_lastResponse`) | `h.type`, `h.flags` | `b.readResp.grantType`, `grantVisibleTick`, `sentinelVisibleTick`, `recallNeeded`, `recallOwnerNode`, `dataSource`, `authEpoch`, `pendingInvCount`, `pendingInvMask`, `committedEpoch`, `grantData[64]` |
| 7 | `UBAdapter::sendWritebackReq` (UBAdapter.cc:217–224) | WritebackResp | `h.type` | `b.writebackResp.success` |
| 8 | `UBAdapter::sendEvictReq` (UBAdapter.cc:269–276) | EvictResp | `h.type` | `b.evictResp.success` |
| 9 | `UBAdapter::sendUpgradeReq` (UBAdapter.cc:329–340) | UpgradeResp | `h.type`, `h.flags` | `b.upgradeResp.upgradeTargetMask`, `b.upgradeResp.committedEpoch` |
|10 | `UBAdapter::sendUpgradeDoneReq` (UBAdapter.cc:393–400) | UpgradeDoneResp | `h.type` | `b.upgradeDoneResp.accepted` |
|11 | `UBAdapter::sendClearReq` (UBAdapter.cc:449–456) | ClearResp | `h.type` | `b.clearResp.accepted` |
|12 | `UBAdapter::sendQueryLineMetaReq` (UBAdapter.cc:671–681) | QueryLineMetaResp | `h.type` | `b.queryLineMetaResp.found`, `b.queryLineMetaResp.epoch`, `b.queryLineMetaResp.ownerNode` |
|13 | `UBRouter::deliverToUbcc` (UBRouter.cc:240–481) | All requests | `h.type`, `h.homeLinePa`, `h.srcNode`, `h.srcSocket`, `h.requesterNode`, `h.epoch`, `h.reqId`, `h.flags` | ReadReq: `b.readReq.neededPerm`; UpgradeReq: `b.upgradeReq.desiredPerm`, `b.upgradeReq.cause`; RecallResp: `b.recallResp.data[64]` |
|14 | `UBCCController::sendUpgradeAckNotify` (UBCCController.cc:1353–1367) | *(constructs, not recv)* | — | — |

---

## 5. Round-trip field flow — per message class (detailed)

### ReadReq → ReadResp (synchronous)

```
Adapter::sendReadReq          UBRouter::deliverToUbcc          Adapter::recvFromRouter
┌─────────────────────┐       ┌────────────────────────┐       ┌──────────────────────┐
│ h.type = ReadReq    │──────→│ reads: type, homeLinePa, │──────→│ stores _lastResponse │
│ h.srcNode = self    │       │   srcNode, srcSocket,    │       │ reads: type,          │
│ h.srcSocket = self  │       │   requesterNode, epoch,  │       │   homeLinePa, epoch,  │
│ h.dstNode = home    │       │   reqId, flags           │       │   reqId               │
│ h.dstSocket = home  │       │ reads: b.readReq.needed  │       │                       │
│ h.homeNode = home   │       │   Perm                   │       │ sendReadReq reads:    │
│ h.homeSocket = home  │       │                          │       │   b.readResp.* (11    │
│ h.ingressSocket = X │       │ constructs response:      │       │   fields + grantData) │
│ h.requesterNode = X │       │   h.type = ReadResp      │       │                      │
│ h.targetNode = home │       │   srcNode,dstNode swapped │       │                      │
│ h.flags = WRITE_    │       │   homeNode = self         │       │                      │
│   INTENT\|0         │       │   homeSocket = self       │       │                      │
│ h.homeLinePa = PA   │       │   ingressSocket echoed    │       │                      │
│ h.epoch = epoch     │       │   requesterNode echoed    │       │                      │
│ h.reqId = reqId     │       │   homeLinePa echoed       │       │                      │
│ h.seqNum = ++seq    │       │   epoch echoed            │       │                      │
│ h.enqueueTick = cur │       │   reqId echoed            │       │                      │
│ h.readyTick = cur   │       │   flags = HAS_DATA?       │       │                      │
│ b.readReq.needed    │       │   *seqNum NOT SET*        │       │                      │
│   Perm = 0\|1       │       │   *enqueueTick NOT SET*   │       │                      │
└─────────────────────┘       │   *readyTick NOT SET*     │       └──────────────────────┘
                              └────────────────────────┘
```

### Fire-and-forget (no response)

```
Adapter::sendRecallResp          UBRouter::deliverToUbcc
┌─────────────────────────────┐  ┌──────────────────────────────────┐
│ h.type = RecallResp         │─→│ reads: homeLinePa, requesterNode, │
│ h.srcNode = self            │  │   flags(DATA_RETURNED,HAS_DATA),  │
│ h.dstNode = homeNode        │  │   epoch, reqId                    │
│ h.homeNode = homeNode       │  │ reads: b.recallResp.data[64]     │
│ h.requesterNode = owner     │  │ → processRecallResponse()        │
│ h.flags = DATA_RETURNED\|   │  │ *No response message generated*   │
│           HAS_DATA          │  └──────────────────────────────────┘
│ h.homeLinePa = PA           │
│ h.epoch = epoch             │
│ h.reqId = reqId             │
│ h.seqNum = ++seq            │
│ h.enqueueTick = curTick     │
│ h.readyTick = curTick       │
│ b.recallResp.data[64] = blk │
└─────────────────────────────┘
```

---

## 6. Missing round-trip fidelity — gap analysis

| Gap | Field(s) | Affects | Impact |
|-----|----------|---------|--------|
| G1 | `seqNum`, `enqueueTick`, `readyTick` | All sync responses | Low — runtime-local fields, not consumed on response path |
| G2 | `homeNode`, `homeSocket` | WritebackResp, EvictResp, UpgradeResp, UpgradeDoneResp, ClearResp, QueryLineMetaResp | Low — adapter routes by dstNode/dstSocket only |
| G3 | `ingressSocket` | All responses | Low — NUMA hint not needed on response path |
| G4 | `targetNode` | All sync responses | Low — not used by adapter on response receive |
| G5 | `reqId` not set | WritebackReq, EvictReq | Low — fire-and-forget semantics (already noted as I5) |
| G6 | Body `homePa` duplicates header `homeLinePa` | QueryLineMetaReq, HomeWritebackNotify | Low — 8B waste, potential divergence (already noted as I7) |

No functional data-loss bugs found — all field omissions are runtime-local or non-essential for the consumer.
