# FV-1 v3: UBCC MESI × OpType × OpStage enumeration

Sources read with `grep -n` + `sed -n`:
- `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh:59-179`
- `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:371-1030, 1030-1465, 1760-2225`
- `gem5/src/mem/ruby/protocol/chi/ep/ResidentDir.hh:14-43`
- `gem5/src/mem/ruby/protocol/chi/ep/ResidentDir.cc:30-58, 151-166`

Legend:
- `L` = live-state-only; committed `ResidentDir` entry is not fully updated yet.
- `D-partial` = acks mutate committed sharer bits before final grant/upgrade commit.
- `D-full` = `commitIntendedResult()` or stable write/update point commits final directory state.

## 1. Directory MESI base states

`ResidentDir` canonical rules:

| MESI | Canonical rule | Owner interpretation |
|---|---|---|
| `G_I` | `sharersMask == 0` | none |
| `G_S` | `sharersMask != 0` | none |
| `G_E` | `popcount(sharersMask) == 1` | `ownerFromSharers()` valid |
| `G_M` | `popcount(sharersMask) == 1` | `ownerFromSharers()` valid |

## 2. Reachable composite states

### 2.1 Stable states (`MESI × none`)

| ID | Composite state | Reachable | Class | Notes |
|---|---|---:|---|---|
| `N0` | `G_I × none` | Yes | stable | empty or resident tombstone (`residentDirty` may remain set) |
| `N1` | `G_S × none` | Yes | stable | shared sharer-set committed |
| `N2` | `G_E × none` | Yes | stable | one-hot owner committed |
| `N3` | `G_M × none` | Yes | stable | one-hot dirty owner committed |

### 2.2 Live outstanding states

| ID | Composite state | Reachable | Class | Entry path | Exit / commit point |
|---|---|---:|---|---|---|
| `L0` | `G_I × GRANT_HANDSHAKE/WAITING_CLEAR` | Yes | `L` -> `D-full` on `CLR` | direct grant from `G_I`; or last `INVALIDATE` ack canonicalizes `G_S -> G_I` then converts to grant | matching `processClear()` commits intended `G_S/G_E/G_M` |
| `L1` | `G_S × GRANT_HANDSHAKE/WAITING_CLEAR` | Yes | `L` -> `D-full` on `CLR` | direct shared grant from `G_S` | matching `CLR` commits final `G_S` |
| `L2` | `G_E × GRANT_HANDSHAKE/WAITING_CLEAR` | Yes | `L` -> `D-full` on `CLR` | owner self-request, or `RECALL/DONE` consumed by same requester retry | matching `CLR` commits intended `G_S/G_E/G_M` |
| `L3` | `G_M × GRANT_HANDSHAKE/WAITING_CLEAR` | Yes | `L` -> `D-full` on `CLR` | same as `L2`, but dirty owner base state | matching `CLR` commits intended `G_S/G_E/G_M` |
| `L4` | `G_S × INVALIDATE/WAITING_ALL_ACKS` | Yes | `D-partial` | non-sharer unique request on shared line | each `INV_ACK` clears committed sharer bits; last ack converts to `GRANT_HANDSHAKE/WAITING_CLEAR` |
| `L5` | `G_E × RECALL/WAITING_TARGET_RESP` | Yes | `L` | non-owner request to exclusive owner | `REC_RESP` marks recall done; directory unchanged |
| `L6` | `G_M × RECALL/WAITING_TARGET_RESP` | Yes | `L` | non-owner request to modified owner | `REC_RESP` marks recall done; directory unchanged |
| `L7` | `G_E × RECALL/DONE` | Yes | `L` | `L5 + REC_RESP` | same-requester retry converts to `GRANT_HANDSHAKE/WAITING_CLEAR` |
| `L8` | `G_M × RECALL/DONE` | Yes | `L` | `L6 + REC_RESP` | same-requester retry converts to `GRANT_HANDSHAKE/WAITING_CLEAR` |
| `L9` | `G_S × UPGRADE_PENDING/WAITING_ALL_ACKS` | Yes | `L` | sharer upgrade with other sharers present | last `INV_ACK` moves to `WAITING_LOCAL_DONE`; if early `UPG_DONE` cached, commit may happen immediately |
| `L10` | `G_S × UPGRADE_PENDING/WAITING_LOCAL_DONE` | Yes | `L` -> `D-full` on `UPG_DONE` | sole-sharer upgrade fast path, or `L9` after all acks | matching `processOuterUpgradeDone()` commits `G_E/G_M` |

## 3. Enum combinations defined but not persistent/reachable

| Combination | Status |
|---|---|
| `*/CREATED` | transient constructor default only; every created outstanding is advanced in same call |
| `GRANT_HANDSHAKE/DONE` | not persistent; successful `CLR` retires to tombstone and removes outstanding |
| `INVALIDATE/DONE` | not persistent; final ack immediately rewrites op to `GRANT_HANDSHAKE/WAITING_CLEAR` |
| `UPGRADE_PENDING/DONE` | not persistent; commit removes outstanding |
| `*/CANCELLED` | enum exists, no assignment found in reviewed source |
| `*/TIMED_OUT` | enum exists, no assignment found in reviewed source |
| `*/PERSISTENT_BUSY` | enum exists, mentioned in comment only; no assignment found |

## 4. Edge summary

### 4.1 Stable -> live

| From | Event | To | Class |
|---|---|---|---|
| `N0` | `OR_RS` / `OR_RU_E` / `OR_RU_M` | `L0` | `L` |
| `N1` | `OR_RS` | `L1` | `L` |
| `N1` | non-sharer `OR_RU_*` | `L4` | `L` |
| `N1` | sharer `UPG_REQ` with other sharers | `L9` | `L` |
| `N1` | sharer `UPG_REQ` as sole sharer | `L10` | `L` |
| `N2` | owner self-request | `L2` | `L` |
| `N2` | non-owner request | `L5` | `L` |
| `N3` | owner self-request | `L3` | `L` |
| `N3` | non-owner request | `L6` | `L` |

### 4.2 Live -> live

| From | Event | To | Class |
|---|---|---|---|
| `L4` | non-final `INV_ACK` | `L4` | `D-partial` |
| `L4` | final `INV_ACK` | `L0` (`G_S` sharers drain to canonical `G_I`, then op rewrites to `GRANT_HANDSHAKE/WAITING_CLEAR`) | `D-partial` + `L` |
| `L5` | valid `REC_RESP` | `L7` | `L` |
| `L6` | valid `REC_RESP` | `L8` | `L` |
| `L7` | same-requester retry `OR_*` | `L2` | `L` |
| `L8` | same-requester retry `OR_*` | `L3` | `L` |
| `L9` | non-final `INV_ACK` | `L9` | `L` |
| `L9` | final `INV_ACK`, no cached `UPG_DONE` | `L10` | `L` |
| `L9` | early `UPG_DONE` | `L9` (cache only) | `L` |

### 4.3 Live -> stable commit edges

| From | Event | To | Class |
|---|---|---|---|
| `L0/L1/L2/L3` | matching `CLR(src=requester, epoch=baseEpoch, reqId)` | `N1/N2/N3` | `D-full` |
| `L9` | final `INV_ACK` with cached early `UPG_DONE` | `N2/N3` | `D-full` |
| `L10` | matching `UPG_DONE(requester)` | `N2/N3` | `D-full` |

## 5. Explicit rejection / blocking rules

| State family | Rejected / blocked events |
|---|---|
| any live outstanding except `RECALL/DONE` retry case | same-requester new `OR_*` -> BUSY |
| any live outstanding | different-requester `OR_*` -> enqueue / RS-merge / drop-full, not immediate transition |
| non-`GRANT_HANDSHAKE/WAITING_CLEAR` | `CLR` rejected |
| non-`RECALL/*` | `REC_RESP` rejected or idempotent-false |
| non-`INVALIDATE/WAITING_ALL_ACKS`, non-`UPGRADE_PENDING/WAITING_ALL_ACKS` | `INV_ACK` idempotent / rejected |
| non-`UPGRADE_PENDING/*` | `UPG_DONE` rejected |
| any state with existing outstanding | fresh `UPG_REQ` rejected |

## 6. Illegal / hazardous combinations and edges

| Item | Why illegal / hazardous |
|---|---|
| `G_I/G_S × RECALL/*` | no creation path; recall only starts from committed exclusive owner (`G_E/G_M`) |
| `G_E/G_M × INVALIDATE/*` | no creation path; invalidate path is only spawned from shared state |
| `G_I/G_E/G_M × UPGRADE_PENDING/*` | protocol intent says upgrade is sharer-only from `G_S`; code does not explicitly guard `entry.state == G_S` in `processOuterUpgradeReq()` |
| `RECALL/DONE` lingering in `_outstandingReqs` | `isLineBusy()` treats `DONE` as not busy, but `processOuterRequest()` still sees the object and queues behind it; forward progress depends on requester retry |
| duplicate `REC_RESP` in `RECALL/DONE` | handler checks `opType == RECALL` but not `stage == WAITING_TARGET_RESP`; cached data can be overwritten |
| `CLR` reqId/src mismatch on `GRANT_HANDSHAKE/WAITING_CLEAR` | rejected without retirement; live grant can remain stuck forever |
| `CANCELLED/TIMED_OUT/PERSISTENT_BUSY` | enums exist but no transition path assigns them; lost `REC_RESP`, `INV_ACK`, `CLR`, or `UPG_DONE` has no coded timeout exit |

## 7. Bottom line

Reachable state space in the reviewed slice is:
- **4 stable committed states**: `N0..N3`
- **11 live outstanding states**: `L0..L10`

The only state with **partial committed-directory mutation before final commit** is `G_S × INVALIDATE/WAITING_ALL_ACKS`.
All other live states are **live-state-only** until `processClear()` or `processOuterUpgradeDone()` executes the final `commitIntendedResult()`.
