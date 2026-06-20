# FV-10: Serialization Round-Trip Schema

**Summary:** Defines which fields must survive UBAdapter→UBMsg→network→UBMsg→UBAdapter round-trip
unchanged per message type, provides a minimal JSON schema covering only semantic+routing fields,
and recommends instrumentation points for automated round-trip verification.

---

## 1. Round-trip field survival per message type

### 1.1 Request–Response pairs (Adapter↔UBCC)

| # | Pair | Send site (Adapter builds) | Recv site (Adapter consumes) | **Must survive unchanged** | Notes |
|---|------|---------------------------|------------------------------|---------------------------|-------|
| 1 | **ReadReq → ReadResp** | `UBAdapter::sendReadReq` (line 87–107) → `UBRouter::deliverToUbcc` (line 241–314) → `UBAdapter::recvFromRouter` (line 729–738) | `req.h.{homeLinePa, epoch, reqId, requesterNode, flags, type}` → echoed into `resp.h.{homeLinePa, epoch, reqId}` + `resp.b.readResp.*` | **Request side:** `homeLinePa`, `epoch`, `reqId`, `requesterNode`, `flags(WRITE_INTENT)`, `neededPerm` (body) → all must arrive at UBCC unchanged. **Response side:** `homeLinePa`, `epoch`, `reqId` echoed back; `grantType`, `grantVisibleTick`, `sentinelVisibleTick`, `recallNeeded`, `recallOwnerNode`, `dataSource`, `authEpoch`, `pendingInvCount`, `pendingInvMask`, `committedEpoch`, `grantData[64]` must all arrive at Adapter unchanged. | `flags(HAS_DATA)` indicates `grantData` is populated. |
| 2 | **WritebackReq → WritebackResp** | `UBAdapter::sendWritebackReq` (line 190–206) → `deliverToUbcc` (line 317–333) → `recvFromRouter` (line 739) | `req.h.{homeLinePa, epoch, requesterNode, flags(KEEP_AS_CLEAN)}` → `resp.h.{homeLinePa, epoch, reqId}` + `resp.b.writebackResp.success` | **Request:** `homeLinePa`, `epoch`, `requesterNode`, `flags(KEEP_AS_CLEAN)`. **Response:** `homeLinePa`, `epoch` echoed; `success`. | `reqId=0` on request (I5 — fire-and-forget semantics). |
| 3 | **EvictReq → EvictResp** | `UBAdapter::sendEvictReq` (line 244–258) → `deliverToUbcc` (line 336–350) → `recvFromRouter` (line 740) | `req.h.{homeLinePa, epoch, requesterNode}` → `resp.h.{homeLinePa, epoch, reqId}` + `resp.b.evictResp.success` | **Request:** `homeLinePa`, `epoch`, `requesterNode`. **Response:** `homeLinePa`, `epoch` echoed; `success`. | `reqId=0` on request (I5). |
| 4 | **UpgradeReq → UpgradeResp** | `UBAdapter::sendUpgradeReq` (line 300–318) → `deliverToUbcc` (line 353–380) → `recvFromRouter` (line 741) | `req.h.{homeLinePa, epoch, reqId, requesterNode}` + `req.b.upgradeReq.{desiredPerm, cause}` → `resp.h.{homeLinePa, epoch, reqId, flags(ACCEPTED)}` + `resp.b.upgradeResp.{upgradeTargetMask, committedEpoch}` | **Request:** `homeLinePa`, `epoch`, `reqId`, `requesterNode`, `desiredPerm`, `cause`. **Response:** `homeLinePa`, `epoch`, `reqId` echoed; `flags(ACCEPTED)`; `upgradeTargetMask`, `committedEpoch`. | |
| 5 | **UpgradeDoneReq → UpgradeDoneResp** | `UBAdapter::sendUpgradeDoneReq` (line 367–382) → `deliverToUbcc` (line 383–397) → `recvFromRouter` (line 742) | `req.h.{homeLinePa, epoch, reqId, requesterNode}` → `resp.h.{homeLinePa, epoch, reqId}` + `resp.b.upgradeDoneResp.accepted` | **Request:** `homeLinePa`, `epoch`, `reqId`, `requesterNode`. **Response:** `homeLinePa`, `epoch`, `reqId` echoed; `accepted`. | |
| 6 | **ClearReq → ClearResp** | `UBAdapter::sendClearReq` (line 421–441) → `deliverToUbcc` (line 400–414) → `recvFromRouter` (line 743) | `req.h.{homeLinePa, epoch, reqId, requesterNode}` + `req.b.clearReq.reason` → `resp.h.{homeLinePa, epoch, reqId}` + `resp.b.clearResp.accepted` | **Request:** `homeLinePa`, `epoch`, `reqId`, `requesterNode`. **Response:** `homeLinePa`, `epoch`, `reqId` echoed; `accepted`. | `reason` always 0 (GrantHandshake). |
| 7 | **QueryLineMetaReq → QueryLineMetaResp** | `UBAdapter::sendQueryLineMetaReq` (line 648–661) → `deliverToUbcc` (line 446–466) → `recvFromRouter` (line 744) | `req.h.{homeLinePa}` → `resp.h.{homeLinePa, epoch, reqId}` + `resp.b.queryLineMetaResp.{found, epoch, ownerNode}` | **Request:** `homeLinePa`. **Response:** `homeLinePa` echoed; `found`, `epoch`, `ownerNode`. | `req.h.homeLinePa` is the only semantic field set (I6). |

### 1.2 Fire-and-forget (Adapter→UBCC, no response)

| # | Type | Send site | UBCC consumes | **Must survive unchanged** | Notes |
|---|------|-----------|---------------|---------------------------|-------|
| 8 | **RecallResp** | `UBAdapter::sendRecallResp` (line 479–504) | `deliverToUbcc` (line 417–435) | `homeLinePa`, `requesterNode` (owner), `epoch`, `reqId`, `flags(DATA_RETURNED\|HAS_DATA)`, `b.recallResp.data[64]` | Fire-and-forget; no response expected. Data payload must survive if `HAS_DATA` set. |
| 9 | **InvalidateAck** | `UBAdapter::sendInvalidateAck` (line 526–544) | `deliverToUbcc` (line 438–443) | `homeLinePa`, `requesterNode` (acker), `epoch`, `reqId` | Body is empty. |
| 10 | **HomeWritebackNotify** | `UBAdapter::sendHomeWritebackNotify` (line 700–716) | `deliverToUbcc` (line 469–474) | `homeLinePa`, `epoch` | Body `homePa` duplicates header `homeLinePa` (I7). |

### 1.3 Fire-and-forget (Adapter→Sharer Adapter, via home UBCC)

| # | Type | Send site | Recv site | **Must survive unchanged** | Notes |
|---|------|-----------|-----------|---------------------------|-------|
| 11 | **RecallReq** | `UBAdapter::sendRecallReqToOwner` (line 564–589) | `UBAdapter::recvFromRouter` (line 762–782) | `homeLinePa`, `localLinePa`, `epoch`, `reqId`, `requesterNode`, `targetNode`, `homeNode`, `flags(IS_READ_RECALL\|HAS_DATA)`, `type` | Routed via home UBCC (no body fields). `srcSocket`/`dstSocket` = homeSocket for sharer routing. |
| 12 | **InvalidateReq** | `UBAdapter::sendInvalidateReqToSharer` (line 608–628) | `UBAdapter::recvFromRouter` (line 785–801) | `homeLinePa`, `localLinePa`, `epoch`, `reqId`, `requesterNode`, `targetNode`, `homeNode`, `type` | Routed via home UBCC. Body empty. `localLinePa` carries sharer's local PA for address translation. |

### 1.4 Async notification (UBCC→Adapter)

| # | Type | Send site | Recv site | **Must survive unchanged** | Notes |
|---|------|-----------|-----------|---------------------------|-------|
| 13 | **UpgradeAckNotify** | `UBCCController::(line 1350–1364)` | `UBAdapter::recvFromRouter` (line 750–759) | `homeLinePa`, `epoch`, `reqId`, `flags(ACCEPTED)`, `type` | No body struct defined (I1 — 🔴). Only header fields carry meaning. `seqNum=0` explicitly. |

---

## 2. Minimal JSON schema (semantic + routing fields only)

This schema excludes runtime-local fields (`seqNum`, `enqueueTick`, `readyTick`) which are per-hop
and not meaningful across a round-trip.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "UBMsg Round-Trip Schema",
  "description": "Minimal schema for semantic+routing fields that must survive UBAdapter↔UBMsg→network→UBMsg→UBAdapter round-trip. Excludes runtime-local fields (seqNum, enqueueTick, readyTick).",

  "definitions": {
    "ubMsgHeader": {
      "type": "object",
      "properties": {
        "type": {
          "type": "string",
          "enum": [
            "ReadReq", "ReadResp", "RecallReq", "RecallResp",
            "InvalidateReq", "InvalidateAck",
            "WritebackReq", "WritebackResp",
            "EvictReq", "EvictResp",
            "UpgradeReq", "UpgradeResp",
            "UpgradeDoneReq", "UpgradeDoneResp",
            "ClearReq", "ClearResp",
            "UpgradeAckNotify",
            "QueryLineMetaReq", "QueryLineMetaResp",
            "HomeWritebackNotify"
          ],
          "description": "Message type — must match at sender and receiver."
        },
        "srcNode":    { "type": "integer", "minimum": 0, "maximum": 65535, "description": "Routing: source node ID" },
        "srcSocket":  { "type": "integer", "minimum": 0, "maximum": 65535, "description": "Routing: source socket ID" },
        "dstNode":    { "type": "integer", "minimum": 0, "maximum": 65535, "description": "Routing: destination node ID" },
        "dstSocket":  { "type": "integer", "minimum": 0, "maximum": 65535, "description": "Routing: destination socket ID" },
        "homeNode":   { "type": "integer", "minimum": 0, "maximum": 65535, "description": "Routing+Semantic: home directory node" },
        "homeSocket": { "type": "integer", "minimum": 0, "maximum": 65535, "description": "Routing+Semantic: home directory socket" },
        "ingressSocket": { "type": "integer", "minimum": 0, "maximum": 65535, "description": "Routing: NUMA hint (request path only)" },
        "requesterNode": { "type": "integer", "minimum": 0, "maximum": 65535, "description": "Semantic: requesting node ID" },
        "targetNode":    { "type": "integer", "minimum": -1, "maximum": 65535, "description": "Semantic: target node (recall/invalidate target, or homeNode aliased)" },
        "flags": {
          "type": "integer",
          "minimum": 0,
          "maximum": 127,
          "description": "Semantic: bitmask — WRITE_INTENT(0), KEEP_AS_CLEAN(1), ACCEPTED(2), DATA_RETURNED(3), HAS_DATA(4), IS_READ_RECALL(5), BUSY(6)"
        },
        "homeLinePa": { "type": "integer", "minimum": 0, "description": "Semantic: home physical address (target cache line)" },
        "localLinePa": { "type": "integer", "minimum": 0, "description": "Semantic: sharer-local PA translation (RecallReq/InvalidateReq only)" },
        "epoch":      { "type": "integer", "minimum": 0, "description": "Semantic: coherence epoch" },
        "reqId":      { "type": "integer", "minimum": 0, "description": "Semantic: request-response correlation ID" }
      },
      "required": ["type", "srcNode", "srcSocket", "dstNode", "dstSocket",
                    "homeNode", "homeSocket", "requesterNode", "flags",
                    "homeLinePa", "epoch"],
      "description": "All fields except targetNode, ingressSocket, localLinePa, reqId are always present. The per-type required sets are refined below."
    },

    "bodyReadReq": {
      "type": "object",
      "properties": {
        "neededPerm": { "type": "integer", "minimum": 0, "maximum": 1, "description": "0=Shared, 1=Unique" }
      },
      "required": ["neededPerm"]
    },

    "bodyReadResp": {
      "type": "object",
      "properties": {
        "grantType":           { "type": "integer", "minimum": -1, "maximum": 2, "description": "-1=BUSY, 0=Shared, 1=Exclusive, 2=Modified" },
        "dataSource":          { "type": "integer", "minimum": 0, "maximum": 2, "description": "0=HomeMemory, 1=RecallBuffer, 2=NoData" },
        "pendingInvCount":     { "type": "integer", "minimum": -1, "description": "-1 if none outstanding" },
        "grantVisibleTick":    { "type": "integer", "minimum": 0 },
        "sentinelVisibleTick": { "type": "integer", "minimum": 0 },
        "recallNeeded":        { "type": "boolean" },
        "recallOwnerNode":     { "type": "integer", "minimum": -1 },
        "authEpoch":           { "type": "integer", "minimum": 0 },
        "committedEpoch":      { "type": "integer", "minimum": 0 },
        "pendingInvMask":      { "type": "integer", "minimum": 0 },
        "grantData":           { "type": "array", "items": { "type": "integer", "minimum": 0, "maximum": 255 }, "minItems": 64, "maxItems": 64, "description": "64-byte data payload (valid when flags.HAS_DATA=1)" }
      },
      "required": ["grantType", "dataSource", "pendingInvCount",
                    "grantVisibleTick", "sentinelVisibleTick",
                    "recallNeeded", "recallOwnerNode", "authEpoch",
                    "committedEpoch", "pendingInvMask"]
    },

    "bodyRecallResp": {
      "type": "object",
      "properties": {
        "data": { "type": "array", "items": { "type": "integer", "minimum": 0, "maximum": 255 }, "minItems": 64, "maxItems": 64, "description": "64-byte cache-line data payload" }
      },
      "required": ["data"]
    },

    "bodyWritebackResp": {
      "type": "object",
      "properties": { "success": { "type": "boolean" } },
      "required": ["success"]
    },

    "bodyEvictResp": {
      "type": "object",
      "properties": { "success": { "type": "boolean" } },
      "required": ["success"]
    },

    "bodyUpgradeReq": {
      "type": "object",
      "properties": {
        "desiredPerm": { "type": "integer", "minimum": 0, "maximum": 255 },
        "cause":       { "type": "integer", "minimum": 0, "maximum": 1, "description": "0=LocalCleanUnique, 1=LocalStoreUpgrade" }
      },
      "required": ["desiredPerm", "cause"]
    },

    "bodyUpgradeResp": {
      "type": "object",
      "properties": {
        "upgradeTargetMask": { "type": "integer", "minimum": 0, "description": "Frozen sharers bitmap for invalidation fanout" },
        "committedEpoch":    { "type": "integer", "minimum": 0 }
      },
      "required": ["upgradeTargetMask", "committedEpoch"]
    },

    "bodyUpgradeDoneResp": {
      "type": "object",
      "properties": { "accepted": { "type": "boolean" } },
      "required": ["accepted"]
    },

    "bodyClearReq": {
      "type": "object",
      "properties": { "reason": { "type": "integer", "minimum": 0, "maximum": 0, "description": "0=GrantHandshake (only value used)" } },
      "required": ["reason"]
    },

    "bodyClearResp": {
      "type": "object",
      "properties": { "accepted": { "type": "boolean" } },
      "required": ["accepted"]
    },

    "bodyQueryLineMetaResp": {
      "type": "object",
      "properties": {
        "found":     { "type": "boolean" },
        "epoch":     { "type": "integer", "minimum": 0 },
        "ownerNode": { "type": "integer", "minimum": -1 }
      },
      "required": ["found", "epoch", "ownerNode"]
    }
  },

  "oneOf": [
    {
      "properties": {
        "header": { "$ref": "#/definitions/ubMsgHeader" },
        "body": { "$ref": "#/definitions/bodyReadReq" }
      },
      "required": ["header", "body"],
      "description": "ReadReq: header + neededPerm"
    },
    {
      "properties": {
        "header": {
          "allOf": [
            { "$ref": "#/definitions/ubMsgHeader" },
            { "properties": { "type": { "const": "ReadResp" }, "flags": { "pattern": "^(..|..)$" } } }
          ]
        },
        "body": { "$ref": "#/definitions/bodyReadResp" }
      },
      "required": ["header", "body"],
      "description": "ReadResp: header + 11 body fields + optional grantData[64]"
    },
    {
      "properties": {
        "header": {
          "allOf": [
            { "$ref": "#/definitions/ubMsgHeader" },
            { "properties": { "type": { "const": "RecallReq" } } }
          ]
        }
      },
      "required": ["header"],
      "description": "RecallReq: header only, no body fields"
    },
    {
      "properties": {
        "header": {
          "allOf": [
            { "$ref": "#/definitions/ubMsgHeader" },
            { "properties": { "type": { "const": "RecallResp" } } }
          ]
        },
        "body": { "$ref": "#/definitions/bodyRecallResp" }
      },
      "required": ["header", "body"],
      "description": "RecallResp: header + data[64]"
    },
    {
      "properties": {
        "header": {
          "allOf": [
            { "$ref": "#/definitions/ubMsgHeader" },
            { "properties": { "type": { "const": "InvalidateReq" } } }
          ]
        }
      },
      "required": ["header"],
      "description": "InvalidateReq: header only"
    },
    {
      "properties": {
        "header": {
          "allOf": [
            { "$ref": "#/definitions/ubMsgHeader" },
            { "properties": { "type": { "const": "InvalidateAck" } } }
          ]
        }
      },
      "required": ["header"],
      "description": "InvalidateAck: header only"
    },
    {
      "properties": {
        "header": {
          "allOf": [
            { "$ref": "#/definitions/ubMsgHeader" },
            { "properties": { "type": { "const": "WritebackReq" } } }
          ]
        }
      },
      "required": ["header"],
      "description": "WritebackReq: header only"
    },
    {
      "properties": {
        "header": {
          "allOf": [
            { "$ref": "#/definitions/ubMsgHeader" },
            { "properties": { "type": { "const": "WritebackResp" } } }
          ]
        },
        "body": { "$ref": "#/definitions/bodyWritebackResp" }
      },
      "required": ["header", "body"],
      "description": "WritebackResp: header + success"
    },
    {
      "properties": {
        "header": {
          "allOf": [
            { "$ref": "#/definitions/ubMsgHeader" },
            { "properties": { "type": { "const": "EvictReq" } } }
          ]
        }
      },
      "required": ["header"],
      "description": "EvictReq: header only"
    },
    {
      "properties": {
        "header": {
          "allOf": [
            { "$ref": "#/definitions/ubMsgHeader" },
            { "properties": { "type": { "const": "EvictResp" } } }
          ]
        },
        "body": { "$ref": "#/definitions/bodyEvictResp" }
      },
      "required": ["header", "body"],
      "description": "EvictResp: header + success"
    },
    {
      "properties": {
        "header": {
          "allOf": [
            { "$ref": "#/definitions/ubMsgHeader" },
            { "properties": { "type": { "const": "UpgradeReq" } } }
          ]
        },
        "body": { "$ref": "#/definitions/bodyUpgradeReq" }
      },
      "required": ["header", "body"],
      "description": "UpgradeReq: header + desiredPerm + cause"
    },
    {
      "properties": {
        "header": {
          "allOf": [
            { "$ref": "#/definitions/ubMsgHeader" },
            { "properties": { "type": { "const": "UpgradeResp" } } }
          ]
        },
        "body": { "$ref": "#/definitions/bodyUpgradeResp" }
      },
      "required": ["header", "body"],
      "description": "UpgradeResp: header + upgradeTargetMask + committedEpoch"
    },
    {
      "properties": {
        "header": {
          "allOf": [
            { "$ref": "#/definitions/ubMsgHeader" },
            { "properties": { "type": { "const": "UpgradeDoneReq" } } }
          ]
        }
      },
      "required": ["header"],
      "description": "UpgradeDoneReq: header only"
    },
    {
      "properties": {
        "header": {
          "allOf": [
            { "$ref": "#/definitions/ubMsgHeader" },
            { "properties": { "type": { "const": "UpgradeDoneResp" } } }
          ]
        },
        "body": { "$ref": "#/definitions/bodyUpgradeDoneResp" }
      },
      "required": ["header", "body"],
      "description": "UpgradeDoneResp: header + accepted"
    },
    {
      "properties": {
        "header": {
          "allOf": [
            { "$ref": "#/definitions/ubMsgHeader" },
            { "properties": { "type": { "const": "ClearReq" } } }
          ]
        },
        "body": { "$ref": "#/definitions/bodyClearReq" }
      },
      "required": ["header", "body"],
      "description": "ClearReq: header + reason"
    },
    {
      "properties": {
        "header": {
          "allOf": [
            { "$ref": "#/definitions/ubMsgHeader" },
            { "properties": { "type": { "const": "ClearResp" } } }
          ]
        },
        "body": { "$ref": "#/definitions/bodyClearResp" }
      },
      "required": ["header", "body"],
      "description": "ClearResp: header + accepted"
    },
    {
      "properties": {
        "header": {
          "allOf": [
            { "$ref": "#/definitions/ubMsgHeader" },
            { "properties": { "type": { "const": "UpgradeAckNotify" } } }
          ]
        }
      },
      "required": ["header"],
      "description": "UpgradeAckNotify: header only — no body struct defined (I1)"
    },
    {
      "properties": {
        "header": {
          "allOf": [
            { "$ref": "#/definitions/ubMsgHeader" },
            { "properties": { "type": { "const": "QueryLineMetaReq" } } }
          ]
        }
      },
      "required": ["header"],
      "description": "QueryLineMetaReq: header only (body homePa duplicates homeLinePa, excluded per §3)"
    },
    {
      "properties": {
        "header": {
          "allOf": [
            { "$ref": "#/definitions/ubMsgHeader" },
            { "properties": { "type": { "const": "QueryLineMetaResp" } } }
          ]
        },
        "body": { "$ref": "#/definitions/bodyQueryLineMetaResp" }
      },
      "required": ["header", "body"],
      "description": "QueryLineMetaResp: header + found + epoch + ownerNode"
    },
    {
      "properties": {
        "header": {
          "allOf": [
            { "$ref": "#/definitions/ubMsgHeader" },
            { "properties": { "type": { "const": "HomeWritebackNotify" } } }
          ]
        }
      },
      "required": ["header"],
      "description": "HomeWritebackNotify: header only (body homePa duplicates homeLinePa, excluded per §3)"
    }
  ]
}
```

> **Schema notes:**
> - `targetNode` is set only on RecallReq/InvalidateReq/ReadReq. On ReadReq it aliases `homeNode`.
> - `ingressSocket` is set only on requests (NUMA hint); responses omit it.
> - `localLinePa` is set only on RecallReq/InvalidateReq (sharer-local address).
> - Runtime-local fields `seqNum`, `enqueueTick`, `readyTick` are excluded — they are set per-hop
>   and not part of the round-trip contract.
> - `QueryLineMetaReq` and `HomeWritebackNotify` have a `homePa` body field that duplicates
>   `header.homeLinePa` (I7) — excluded from the schema because it is redundant.

---

## 3. Recommended instrumentation points

### 3.1 Send-capture points (snapshot the message *just before* `_router->sendMessage()`)

| # | Message type | File | Line | Snippet to annotate |
|---|-------------|------|------|---------------------|
| S1 | ReadReq | `UBAdapter.cc` | 113 | `// FV10-CAPTURE-SEND` before `_router->sendMessage(req)` |
| S2 | WritebackReq | `UBAdapter.cc` | 208 | `// FV10-CAPTURE-SEND` before `_router->sendMessage(req)` |
| S3 | EvictReq | `UBAdapter.cc` | 260 | `// FV10-CAPTURE-SEND` before `_router->sendMessage(req)` |
| S4 | UpgradeReq | `UBAdapter.cc` | 320 | `// FV10-CAPTURE-SEND` before `_router->sendMessage(req)` |
| S5 | UpgradeDoneReq | `UBAdapter.cc` | 384 | `// FV10-CAPTURE-SEND` before `_router->sendMessage(req)` |
| S6 | ClearReq | `UBAdapter.cc` | 440 | `// FV10-CAPTURE-SEND` before `_router->sendMessage(req)` |
| S7 | QueryLineMetaReq | `UBAdapter.cc` | 662 | `// FV10-CAPTURE-SEND` before `_router->sendMessage(req)` |
| S8 | RecallResp | `UBAdapter.cc` | 503 | `// FV10-CAPTURE-SEND` before `_router->sendMessage(req)` |
| S9 | InvalidateAck | `UBAdapter.cc` | 543 | `// FV10-CAPTURE-SEND` before `_router->sendMessage(req)` |
| S10 | HomeWritebackNotify | `UBAdapter.cc` | 715 | `// FV10-CAPTURE-SEND` before `_router->sendMessage(req)` |
| S11 | RecallReq | `UBAdapter.cc` | 588 | `// FV10-CAPTURE-SEND` before `_router->sendMessage(req)` |
| S12 | InvalidateReq | `UBAdapter.cc` | 627 | `// FV10-CAPTURE-SEND` before `_router->sendMessage(req)` |
| S13 | UpgradeAckNotify | `UBCCController.cc` | 1363 | `// FV10-CAPTURE-SEND` before `_router->sendMessage(notifyMsg)` |

### 3.2 Receive-capture points (snapshot the message *just after* it arrives at the consumer)

| # | Message type | File | Line | Snippet to annotate |
|---|-------------|------|------|---------------------|
| R1 | ReadResp | `UBAdapter.cc` | 730 | `// FV10-CAPTURE-RECV` at top of `case UBMsgType::ReadResp:` |
| R2 | WritebackResp | `UBAdapter.cc` | 739 | `// FV10-CAPTURE-RECV` at top of `case UBMsgType::WritebackResp:` |
| R3 | EvictResp | `UBAdapter.cc` | 740 | `// FV10-CAPTURE-RECV` at top of `case UBMsgType::EvictResp:` |
| R4 | UpgradeResp | `UBAdapter.cc` | 741 | `// FV10-CAPTURE-RECV` at top of `case UBMsgType::UpgradeResp:` |
| R5 | UpgradeDoneResp | `UBAdapter.cc` | 742 | `// FV10-CAPTURE-RECV` at top of `case UBMsgType::UpgradeDoneResp:` |
| R6 | ClearResp | `UBAdapter.cc` | 743 | `// FV10-CAPTURE-RECV` at top of `case UBMsgType::ClearResp:` |
| R7 | QueryLineMetaResp | `UBAdapter.cc` | 744 | `// FV10-CAPTURE-RECV` at top of `case UBMsgType::QueryLineMetaResp:` |
| R8 | UpgradeAckNotify | `UBAdapter.cc` | 750 | `// FV10-CAPTURE-RECV` at top of `case UBMsgType::UpgradeAckNotify:` |
| R9 | RecallReq | `UBAdapter.cc` | 762 | `// FV10-CAPTURE-RECV` at top of `case UBMsgType::RecallReq:` |
| R10 | InvalidateReq | `UBAdapter.cc` | 785 | `// FV10-CAPTURE-RECV` at top of `case UBMsgType::InvalidateReq:` |

### 3.3 Router cross-check points (UBCC-side verification)

| # | Point | File | Line | Purpose |
|---|-------|------|------|---------|
| X1 | **After dequeue, before dispatch** — log semantic fields | `UBRouter.cc` | 130 (after `q->popReady`) | Verify request fields before they are consumed by `deliverToUbcc` or forwarded to remote router |
| X2 | **After response constructed** — log response before enqueue | `UBRouter.cc` | 166 (before `revQ->enqueue`) | Verify response fields match echoed request fields |
| X3 | **Remote forward** — log before `dstRouter->sendMessage` | `UBRouter.cc` | 201 | Verify forwarded message preserves semantic fields |

### 3.4 Recommended verification procedure

```
For each synchronous request–response pair (1–7):
  1. At S{n}, capture JSON snapshot of outbound request msg
  2. At X1, log received request fields (or compare to snapshot if same-node)
  3. At X2, log response fields (verify homeLinePa, epoch, reqId echoed correctly)
  4. At R{n}, capture JSON snapshot of inbound response msg
  5. Compare S{n} request snapshot with R{n} response snapshot:
     - homeLinePa must match (echo identity)
     - epoch must match unless UBCC modified it
     - reqId must match
     - Response body fields must be non-default per type

For fire-and-forget messages (8–10):
  1. At S{n}, capture snapshot
  2. At X1, verify fields arrived at UBCC (log after deliverToUbcc processing)

For cross-node fan-out messages (11–12):
  1. At S{n}, capture snapshot
  2. At X3, verify forwarded message preserves routing+semantic fields
  3. At R{n}, verify reconstructed message matches original (recallMsg / invMsg fields)

For async UpgradeAckNotify (13):
  1. At S13, capture snapshot
  2. At X1/X3, verify routing delivers to correct dstNode
  3. At R8, verify homeLinePa, epoch, reqId, flags(ACCEPTED) match expectations
```

---

## 4. Per-type round-trip survival matrix

| Field classification | ReadReq→Resp | WbReq→Resp | EvictReq→Resp | UpgReq→Resp | UpgDoneReq→Resp | ClearReq→Resp | QLMetaReq→Resp | RecallReq | InvReq | RecallResp | InvAck | HWN | UpgAckN |
|---------------------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Semantic** | | | | | | | | | | | | | |
| `type` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `requesterNode` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | ✓ | — | ✓ |
| `targetNode` | ✓(alias) | — | — | — | — | — | — | ✓ | ✓ | — | — | — | — |
| `flags` | ✓ | ✓(KAC) | — | ✓(ACC) | — | — | — | ✓(IRR\|HD) | — | ✓(DR\|HD) | — | — | ✓(ACC) |
| `homeLinePa` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `localLinePa` | — | — | — | — | — | — | — | ✓ | ✓ | — | — | — | — |
| `epoch` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `reqId` | ✓ | — | — | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | ✓ | — | ✓ |
| **Routing** | | | | | | | | | | | | | |
| `srcNode` | — | — | — | — | — | — | — | — | — | — | — | — | — |
| `srcSocket` | — | — | — | — | — | — | — | — | — | — | — | — | — |
| `dstNode` | — | — | — | — | — | — | — | — | — | — | — | — | — |
| `dstSocket` | — | — | — | — | — | — | — | — | — | — | — | — | — |
| `homeNode` | — | — | — | — | — | — | — | — | — | — | — | — | — |
| `homeSocket` | — | — | — | — | — | — | — | — | — | — | — | — | — |
| `ingressSocket` | — | — | — | — | — | — | — | — | — | — | — | — | — |
| **Body** | | | | | | | | | | | | | |
| `neededPerm` | ✓ | — | — | — | — | — | — | — | — | — | — | — | — |
| ReadResp body (11f) | ✓ | — | — | — | — | — | — | — | — | — | — | — | — |
| `success` | — | ✓ | ✓ | — | — | — | — | — | — | — | — | — | — |
| `desiredPerm`/`cause` | — | — | — | ✓ | — | — | — | — | — | — | — | — | — |
| `upgradeTargetMask`/`committedEpoch` | — | — | — | ✓ | — | — | — | — | — | — | — | — | — |
| `accepted` (UpgDone/Clear) | — | — | — | — | ✓ | ✓ | — | — | — | — | — | — | — |
| `data[64]` | — | — | — | — | — | — | — | — | — | ✓ | — | — | — |
| `found`/`epoch`/`ownerNode` | — | — | — | — | — | — | ✓ | — | — | — | — | — | — |

> **Legend:** ✓ = must survive round-trip; — = not set or not meaningful on this path.
> Routing fields (src/dst/home/ingress) are set by each hop and **mutated** — they do not survive
> round-trip unchanged. They are verified per-hop (not end-to-end).

---

## 5. Round-trip verification test plan

| Test | Pair | What to verify |
|------|------|----------------|
| **RT-1** | ReadReq→ReadResp | `homeLinePa`, `epoch`, `reqId` echoed; `grantType` ∈ {-1,0,1,2}; `pendingInvCount` ≥ -1; if `flags.HAS_DATA` then `grantData[64]` nonzero |
| **RT-2** | WritebackReq→WritebackResp | `homeLinePa`, `epoch` echoed; `success` ∈ {true,false}; `flags.KEEP_AS_CLEAN` preserved |
| **RT-3** | EvictReq→EvictResp | `homeLinePa`, `epoch` echoed; `success` ∈ {true,false} |
| **RT-4** | UpgradeReq→UpgradeResp | `homeLinePa`, `epoch`, `reqId` echoed; `flags.ACCEPTED` set on accept; `upgradeTargetMask` ≥ 0 |
| **RT-5** | UpgradeDoneReq→UpgradeDoneResp | `homeLinePa`, `epoch`, `reqId` echoed; `accepted` ∈ {true,false} |
| **RT-6** | ClearReq→ClearResp | `homeLinePa`, `epoch`, `reqId` echoed; `accepted` ∈ {true,false} |
| **RT-7** | QueryLineMetaReq→QueryLineMetaResp | `homeLinePa` echoed; `found` ∈ {true,false}; if found, `epoch` ≥ 0, `ownerNode` ≥ 0 |
| **RT-8** | RecallResp (fire-and-forget) | `homeLinePa`, `epoch`, `reqId`, `flags(DATA_RETURNED\|HAS_DATA)` delivered to UBCC; if `HAS_DATA` then `data[64]` preserved |
| **RT-9** | InvalidateAck (fire-and-forget) | `homeLinePa`, `epoch`, `reqId`, `requesterNode` delivered to UBCC |
| **RT-10** | HomeWritebackNotify (fire-and-forget) | `homeLinePa`, `epoch` delivered to UBCC |
| **RT-11** | RecallReq (cross-node) | `homeLinePa`, `localLinePa`, `epoch`, `reqId`, `requesterNode`, `targetNode`, `flags(IS_READ_RECALL\|HAS_DATA)` preserved end-to-end |
| **RT-12** | InvalidateReq (cross-node) | `homeLinePa`, `localLinePa`, `epoch`, `reqId`, `requesterNode`, `targetNode` preserved end-to-end |
| **RT-13** | UpgradeAckNotify (async) | `homeLinePa`, `epoch`, `reqId`, `flags(ACCEPTED)` delivered to adapter; `type=UpgradeAckNotify` |

---

## 6. Excluded fields rationale

| Excluded field | Reason |
|----------------|--------|
| `seqNum` | Per-hop transmit ordering; set by sender's `_nextSeq++`, never read on receive. Response messages leave it 0 (I2). |
| `enqueueTick` | Runtime-local scheduling timestamp; set per queue enqueue. Not meaningful across hops. |
| `readyTick` | Runtime-local scheduling timestamp; set per queue enqueue. Not meaningful across hops. |
| `ingressSocket` | NUMA hint used only on request path; responses omit it (I4). Not a round-trip invariant. |
| Body `homePa` (QLM/HWN) | Duplicates `header.homeLinePa` (I7). Excluded to avoid redundant verification. |
