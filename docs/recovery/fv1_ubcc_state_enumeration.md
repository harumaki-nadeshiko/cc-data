# FV-1: UBCC MESI × OpType × OpStage state enumeration

Scope:
- `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh:59-179`
- `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:371-1030, 1058-1307, 1501-1736, 1739-2100`
- `gem5/src/mem/ruby/protocol/chi/ep/ResidentDir.hh:14-43`
- `gem5/src/mem/ruby/protocol/chi/ep/ResidentDir.cc:30-56, 149-166`

Event alphabet used below:
- `OR_RS(r)` = `processOuterRequest(GlobalReadShared, writeIntent=0, requester=r)`
- `OR_RU_E(r)` = `processOuterRequest(GlobalReadUnique, writeIntent=0, requester=r)`
- `OR_RU_M(r)` = `processOuterRequest(GlobalReadUnique, writeIntent=1, requester=r)`
- `UPG_REQ(r,E|M)` = `processOuterUpgradeReq(... desiredPerm=0|1 ...)`
- `UPG_DONE(r,e,id)` = `processOuterUpgradeDone(...)`
- `REC_RESP(owner,e,id,data)` = `processRecallResponse(...)`
- `INV_ACK(node,e,id)` = `processInvalidationAck(...)`
- `CLR(src,e,id)` = `processClear(...)`
- `WB_CLEAN(r,e)` = `processWriteback(... keepAsClean=true)`
- `WB_DROP(r,e)` = `processWriteback(... keepAsClean=false)`
- `EVICT(r,e)` = `processEvict(...)`

Transition kind:
- `L` = live-state-only (`_outstandingReqs` / queue metadata only; committed directory unchanged)
- `D-partial` = committed directory updated partially, but final reserved result not yet committed
- `D-full` = committed directory updated to final stable result
- `R` = explicit reject / BUSY / false / idempotent-no-op

Orthogonal note: `fillPending` / `wbPending` / resident miss handling live outside the `committedState × liveOutstanding` axis. `ensureResidentForAccess()` can therefore return `Queued/Busy` before the state-specific logic below runs.

## 1. Reachable composite states

Auxiliary note: tombstones are side metadata, not a live outstanding state. A stable committed state may coexist with tombstone replay metadata for window `W`.

### 1.1 Stable committed states with no live outstanding

| ID | Committed state | Canonical constraints | Reachable? |
|---|---|---|---|
| N0 | `G_I × none` | `sharersMask=0`; `residentDirty` may be `0` (empty) or `1` (resident tombstone) | Yes |
| N1 | `G_S × none` | `sharersMask!=0` | Yes |
| N2 | `G_E × none` | `popcount(sharersMask)=1` | Yes |
| N3 | `G_M × none` | `popcount(sharersMask)=1` | Yes |

### 1.2 Reachable live-outstanding combinations

| ID | Committed MESI | Live outstanding | Reachable? | How reached |
|---|---|---|---|---|
| L0 | `G_I` | `GRANT_HANDSHAKE / WAITING_CLEAR` | Yes | Direct grant from `G_I`, or `INVALIDATE` final ack canonicalizes `G_S→G_I` then converts to grant |
| L1 | `G_S` | `GRANT_HANDSHAKE / WAITING_CLEAR` | Yes | Direct `OR_RS` on shared line |
| L2 | `G_E` | `GRANT_HANDSHAKE / WAITING_CLEAR` | Yes | Owner self-request, or `RECALL.DONE` consumed by same requester retry |
| L3 | `G_M` | `GRANT_HANDSHAKE / WAITING_CLEAR` | Yes | Owner self-request, or `RECALL.DONE` consumed by same requester retry |
| L4 | `G_S` | `INVALIDATE / WAITING_ALL_ACKS` | Yes | Non-sharer unique request on shared line |
| L5 | `G_E` | `RECALL / WAITING_TARGET_RESP` | Yes | Non-owner request to exclusive owner |
| L6 | `G_M` | `RECALL / WAITING_TARGET_RESP` | Yes | Non-owner request to modified owner |
| L7 | `G_E` | `RECALL / DONE` | Yes | Accepted recall response; directory still uncommitted |
| L8 | `G_M` | `RECALL / DONE` | Yes | Accepted recall response; directory still uncommitted |
| L9 | `G_S` | `UPGRADE_PENDING / WAITING_ALL_ACKS` | Yes | Sharer upgrade with other sharers present |
| L10 | `G_S` | `UPGRADE_PENDING / WAITING_LOCAL_DONE` | Yes | Sole-sharer upgrade fast path, or all upgrade invalidation acks complete |

### 1.3 Enum combinations defined but not reached by this code slice

| Combination | Status |
|---|---|
| Any `*/CREATED` after function return | Not externally persistent; created and immediately advanced in same call |
| `GRANT_HANDSHAKE/DONE` | Removed to tombstone immediately on accepted `CLR` |
| `INVALIDATE/DONE` | Immediately converted in-place to `GRANT_HANDSHAKE/WAITING_CLEAR` |
| `UPGRADE_PENDING/DONE` | Removed immediately on commit |
| Any `*/CANCELLED` | Enum exists, no assignment in reviewed code |
| Any `*/TIMED_OUT` | Enum exists, no assignment in reviewed code |
| Any `*/PERSISTENT_BUSY` | Enum exists, no assignment in reviewed code |

## 2. Per-state event coverage

### N0: `G_I × none`

| Event | Transition |
|---|---|
| `OR_RS(r)` | Create `GRANT_HANDSHAKE/WAITING_CLEAR`, intended `G_S`, reservedEpoch=`epoch+1` -> `L0` (`L`) |
| `OR_RU_E(r)` | Create `GRANT_HANDSHAKE/WAITING_CLEAR`, intended `G_E(owner=r)` -> `L0` (`L`) |
| `OR_RU_M(r)` | Create `GRANT_HANDSHAKE/WAITING_CLEAR`, intended `G_M(owner=r)` -> `L0` (`L`) |
| `UPG_REQ(r,perm)` | Reject because requester is not a committed sharer (`R`); gap: negative `requesterNode` is not range-checked here |
| `REC_RESP(...)` | Reject: no `RECALL` outstanding (`R`) |
| `INV_ACK(...)` | If entry exists and no outstanding: idempotent `true`, no state change (`R`) |
| `CLR(...)` | If tombstone matches `(epoch,reqId)`, replay accepted/rejected from tombstone; else stale/no grant -> `false` (`R`) |
| `UPG_DONE(...)` | Reject: no `UPGRADE_PENDING` outstanding (`R`) |
| `WB_CLEAN(r,e)` | Accepts even with no committed owner; commits `G_E(owner=r)`, `residentDirty=1` (`D-full`, semantic gap) |
| `WB_DROP(r,e)` | Accepts; stays `G_I`, sets `residentDirty=1` (`D-full`) |
| `EVICT(r,e)` | Reject: requester is neither sharer nor owner (`R`) |

### N1: `G_S × none`

| Event | Transition |
|---|---|
| `OR_RS(r)` | Create `GRANT_HANDSHAKE/WAITING_CLEAR`, intended `G_S` with sharers `old|r` -> `L1` (`L`) |
| `OR_RU_E/M(r)` where `r` is existing sharer | Return BUSY and defer protocol to `UPG_REQ`; no state change (`R`) |
| `OR_RU_E/M(r)` where `r` is not sharer | Create `INVALIDATE/WAITING_ALL_ACKS`, targetMask=`all committed sharers except requester`, intended `G_E/G_M(owner=r)` -> `L4` (`L`) |
| `UPG_REQ(r,perm)` where `r` is sharer and other sharers exist | Create `UPGRADE_PENDING/WAITING_ALL_ACKS` -> `L9` (`L`) |
| `UPG_REQ(r,perm)` where `r` is sole sharer | Create `UPGRADE_PENDING/WAITING_LOCAL_DONE` -> `L10` (`L`) |
| `UPG_REQ(r,perm)` where `r` not in sharers | Reject (`R`) |
| `REC_RESP(...)` | Reject: no `RECALL` outstanding (`R`) |
| `INV_ACK(...)` | If no outstanding: idempotent `true`, no state change (`R`) |
| `CLR(...)` | Tombstone replay if present, else reject (`R`) |
| `UPG_DONE(...)` | Reject: no `UPGRADE_PENDING` outstanding (`R`) |
| `WB_CLEAN(r,e)` | Accepts from any requester; commits `G_E(owner=r)`, discarding all other sharers (`D-full`, semantic gap) |
| `WB_DROP(r,e)` | Accepts from any requester; commits `G_I`, discarding whole sharer set (`D-full`, semantic gap) |
| `EVICT(r,e)` where `r∈sharers` and others remain | Clear `r` from committed sharers, remain `G_S` (`D-full`) |
| `EVICT(r,e)` where `r` is last sharer | Commit `G_I` (`D-full`) |
| `EVICT(r,e)` where `r∉sharers` | Reject (`R`) |

### N2: `G_E × none`

| Event | Transition |
|---|---|
| `OR_RS(r=owner)` | Create `GRANT_HANDSHAKE/WAITING_CLEAR`, intended `G_S(owner+requester sharers)` -> `L2` (`L`) |
| `OR_RS(r!=owner)` | Create `RECALL/WAITING_TARGET_RESP` -> `L5` (`L`) |
| `OR_RU_E(owner)` | Create `GRANT_HANDSHAKE/WAITING_CLEAR`, intended `G_E(owner)` -> `L2` (`L`) |
| `OR_RU_M(owner)` | Create `GRANT_HANDSHAKE/WAITING_CLEAR`, intended `G_M(owner)` -> `L2` (`L`) |
| `OR_RU_E/M(r!=owner)` | Create `RECALL/WAITING_TARGET_RESP` -> `L5` (`L`) |
| `UPG_REQ(owner,perm)` | Semantic gap: code accepts and creates `UPGRADE_PENDING/WAITING_LOCAL_DONE`, although upgrade is intended for `G_S` sharer->unique only |
| `REC_RESP(...)` | Reject: no `RECALL` outstanding (`R`) |
| `INV_ACK(...)` | If no outstanding: idempotent `true`, no state change (`R`) |
| `CLR(...)` | Tombstone replay if present, else reject (`R`) |
| `UPG_DONE(...)` | Reject: no `UPGRADE_PENDING` outstanding (`R`) |
| `WB_CLEAN(owner,e)` | Commit `G_E(owner)` with `residentDirty=1` (`D-full`) |
| `WB_DROP(owner,e)` | Commit `G_I`, `residentDirty=1` (`D-full`) |
| `WB_*(r!=owner,e)` | Reject owner mismatch (`R`) |
| `EVICT(owner,e)` | Commit `G_I` (`D-full`) |
| `EVICT(r!=owner,e)` | Reject (`R`) |

### N3: `G_M × none`

| Event | Transition |
|---|---|
| `OR_RS(r=owner)` | Create `GRANT_HANDSHAKE/WAITING_CLEAR`, intended `G_S(owner+requester sharers)` -> `L3` (`L`) |
| `OR_RS(r!=owner)` | Create `RECALL/WAITING_TARGET_RESP` -> `L6` (`L`) |
| `OR_RU_E(owner)` | Create `GRANT_HANDSHAKE/WAITING_CLEAR`, intended `G_E(owner)` -> `L3` (`L`) |
| `OR_RU_M(owner)` | Create `GRANT_HANDSHAKE/WAITING_CLEAR`, intended `G_M(owner)` -> `L3` (`L`) |
| `OR_RU_E/M(r!=owner)` | Create `RECALL/WAITING_TARGET_RESP` -> `L6` (`L`) |
| `UPG_REQ(owner,perm)` | Semantic gap: code accepts and creates `UPGRADE_PENDING/WAITING_LOCAL_DONE`, although upgrade is intended for `G_S` sharer->unique only |
| `REC_RESP(...)` | Reject: no `RECALL` outstanding (`R`) |
| `INV_ACK(...)` | If no outstanding: idempotent `true`, no state change (`R`) |
| `CLR(...)` | Tombstone replay if present, else reject (`R`) |
| `UPG_DONE(...)` | Reject: no `UPGRADE_PENDING` outstanding (`R`) |
| `WB_CLEAN(owner,e)` | Commit `G_E(owner)`, clearing dirty (`D-full`) |
| `WB_DROP(owner,e)` | Commit `G_I` (`D-full`) |
| `WB_*(r!=owner,e)` | Reject owner mismatch (`R`) |
| `EVICT(owner,e)` | **Code bug:** accepted and commits `G_I`; dirty-owner guard is bypassed because sharer bit is cleared before owner check (`D-full`, illegal edge) |
| `EVICT(r!=owner,e)` | Reject (`R`) |

### L0: `G_I × GRANT_HANDSHAKE/WAITING_CLEAR`

| Event | Transition |
|---|---|
| `CLR(src=requester, e=baseEpoch, id=match)` | Commit intended result and epoch, retire tombstone, remove outstanding -> `N0/N1/N2/N3` depending on intended state (`D-full`) |
| `CLR(epoch mismatch)` | Retire stale grant to tombstone(`accepted=false`), remove outstanding, directory unchanged -> `N0` (`L`) |
| `CLR(reqId mismatch or src mismatch)` | Reject, outstanding retained (`R`) |
| `OR_RS/OR_RU` same requester exact tuple and `replayArmed=1` | Return grant directly, no state change (`L`) |
| `OR_RS/OR_RU` same requester otherwise | BUSY, no state change (`R`) |
| `OR_RS/OR_RU` different requester | Enqueue / RS-merge / drop-full, no state change (`L`) |
| `UPG_REQ(...)` | Reject because outstanding exists (`R`) |
| `REC_RESP(...)` | Reject: wrong opType (`R`) |
| `INV_ACK(...)` | Idempotent `true`: opType/stage not applicable (`R`) |
| `UPG_DONE(...)` | Reject: wrong opType (`R`) |
| `WB_CLEAN/WB_DROP(...)` | Reject BUSY because `isLineBusy()` is true (`R`) |
| `EVICT(fresh)` | Reject BUSY after stale check (`R`) |

### L1: `G_S × GRANT_HANDSHAKE/WAITING_CLEAR`

| Event | Transition |
|---|---|
| `CLR(src=requester, e=baseEpoch, id=match)` | Commit final `G_S` sharers set and epoch, retire tombstone, remove outstanding -> `N1` (`D-full`) |
| `CLR(epoch mismatch)` | Retire stale grant to tombstone(`accepted=false`), remove outstanding, directory unchanged -> `N1` (`L`) |
| `CLR(reqId mismatch or src mismatch)` | Reject, outstanding retained (`R`) |
| `OR_RS/OR_RU` same requester exact tuple and `replayArmed=1` | Return grant directly, no state change (`L`) |
| `OR_RS/OR_RU` same requester otherwise | BUSY (`R`) |
| `OR_RS/OR_RU` different requester | Enqueue / RS-merge / drop-full (`L`) |
| `UPG_REQ(...)` | Reject because outstanding exists (`R`) |
| `REC_RESP(...)` | Reject (`R`) |
| `INV_ACK(...)` | Idempotent `true` (`R`) |
| `UPG_DONE(...)` | Reject (`R`) |
| `WB_CLEAN/WB_DROP(...)` | Reject BUSY (`R`) |
| `EVICT(fresh)` | Reject BUSY after stale check (`R`) |

### L2: `G_E × GRANT_HANDSHAKE/WAITING_CLEAR`

| Event | Transition |
|---|---|
| `CLR(src=requester, e=baseEpoch, id=match)` | Commit intended `G_S/G_E/G_M`, retire tombstone, remove outstanding -> `N1/N2/N3` (`D-full`) |
| `CLR(epoch mismatch)` | Retire stale grant to tombstone(`accepted=false`), remove outstanding, directory stays `G_E` -> `N2` (`L`) |
| `CLR(reqId mismatch or src mismatch)` | Reject, outstanding retained (`R`) |
| `OR_RS/OR_RU` same requester exact tuple and `replayArmed=1` | Return grant directly (`L`) |
| `OR_RS/OR_RU` same requester otherwise | BUSY (`R`) |
| `OR_RS/OR_RU` different requester | Enqueue / RS-merge / drop-full (`L`) |
| `UPG_REQ(...)` | Reject because outstanding exists (`R`) |
| `REC_RESP(...)` | Reject (`R`) |
| `INV_ACK(...)` | Idempotent `true` (`R`) |
| `UPG_DONE(...)` | Reject (`R`) |
| `WB_CLEAN/WB_DROP(...)` | Reject BUSY (`R`) |
| `EVICT(fresh)` | Reject BUSY after stale check (`R`) |

### L3: `G_M × GRANT_HANDSHAKE/WAITING_CLEAR`

| Event | Transition |
|---|---|
| `CLR(src=requester, e=baseEpoch, id=match)` | Commit intended `G_S/G_E/G_M`, retire tombstone, remove outstanding -> `N1/N2/N3` (`D-full`) |
| `CLR(epoch mismatch)` | Retire stale grant to tombstone(`accepted=false`), remove outstanding, directory stays `G_M` -> `N3` (`L`) |
| `CLR(reqId mismatch or src mismatch)` | Reject, outstanding retained (`R`) |
| `OR_RS/OR_RU` same requester exact tuple and `replayArmed=1` | Return grant directly (`L`) |
| `OR_RS/OR_RU` same requester otherwise | BUSY (`R`) |
| `OR_RS/OR_RU` different requester | Enqueue / RS-merge / drop-full (`L`) |
| `UPG_REQ(...)` | Reject because outstanding exists (`R`) |
| `REC_RESP(...)` | Reject (`R`) |
| `INV_ACK(...)` | Idempotent `true` (`R`) |
| `UPG_DONE(...)` | Reject (`R`) |
| `WB_CLEAN/WB_DROP(...)` | Reject BUSY (`R`) |
| `EVICT(fresh)` | Reject BUSY after stale check (`R`) |

### L4: `G_S × INVALIDATE/WAITING_ALL_ACKS`

| Event | Transition |
|---|---|
| `INV_ACK(node in targetMask, fresh epoch)` and not last ack | Clear `node` from committed sharers, remain `INVALIDATE/WAITING_ALL_ACKS` -> `L4` (`D-partial`) |
| `INV_ACK(last remaining target)` | Clear last sharer, canonicalize `G_S→G_I`, then mutate outstanding in-place to `GRANT_HANDSHAKE/WAITING_CLEAR`, `replayArmed=1` -> `L0` (`D-partial + L`) |
| `INV_ACK(duplicate)` | Ignore, no state change (`R`) |
| `INV_ACK(node not in targetMask or stale epoch)` | Reject (`R`) |
| `OR_RS/OR_RU` same requester | BUSY (`R`) |
| `OR_RS/OR_RU` different requester | Enqueue / RS-merge / drop-full (`L`) |
| `UPG_REQ(...)` | Reject because outstanding exists (`R`) |
| `REC_RESP(...)` | Reject: wrong opType (`R`) |
| `CLR(...)` | Reject: no `GRANT_HANDSHAKE` yet (`R`) |
| `UPG_DONE(...)` | Reject: wrong opType (`R`) |
| `WB_CLEAN/WB_DROP(...)` | Reject BUSY (`R`) |
| `EVICT(fresh)` | Reject BUSY after stale check (`R`) |

### L5: `G_E × RECALL/WAITING_TARGET_RESP`

| Event | Transition |
|---|---|
| `REC_RESP(owner=target, id match, fresh epoch)` | Mark `recallBarrierDone=1`, cache data if present, `stage=DONE`, directory unchanged -> `L7` (`L`) |
| `REC_RESP(owner mismatch / reqId mismatch / stale epoch)` | Reject (`R`) |
| `OR_RS/OR_RU` same requester | BUSY (`R`) |
| `OR_RS/OR_RU` different requester | Enqueue / RS-merge / drop-full (`L`) |
| `UPG_REQ(...)` | Reject because outstanding exists (`R`) |
| `INV_ACK(...)` | Idempotent `true` (`R`) |
| `CLR(...)` | Reject: no `GRANT_HANDSHAKE` (`R`) |
| `UPG_DONE(...)` | Reject (`R`) |
| `WB_CLEAN/WB_DROP(...)` | Reject BUSY (`R`) |
| `EVICT(fresh)` | Reject BUSY after stale check (`R`) |

### L6: `G_M × RECALL/WAITING_TARGET_RESP`

| Event | Transition |
|---|---|
| `REC_RESP(owner=target, id match, fresh epoch)` | Mark `recallBarrierDone=1`, cache data if present, `stage=DONE`, directory unchanged -> `L8` (`L`) |
| `REC_RESP(owner mismatch / reqId mismatch / stale epoch)` | Reject (`R`) |
| `OR_RS/OR_RU` same requester | BUSY (`R`) |
| `OR_RS/OR_RU` different requester | Enqueue / RS-merge / drop-full (`L`) |
| `UPG_REQ(...)` | Reject because outstanding exists (`R`) |
| `INV_ACK(...)` | Idempotent `true` (`R`) |
| `CLR(...)` | Reject (`R`) |
| `UPG_DONE(...)` | Reject (`R`) |
| `WB_CLEAN/WB_DROP(...)` | Reject BUSY (`R`) |
| `EVICT(fresh)` | Reject BUSY after stale check (`R`) |

### L7: `G_E × RECALL/DONE`

| Event | Transition |
|---|---|
| `OR_RS/OR_RU` from same requester | Remove terminal `RECALL`, create `GRANT_HANDSHAKE/WAITING_CLEAR`, preserve cached recall data, directory unchanged -> `L2` (`L`); retry is keyed only by requester, not by incoming `reqId`/`writeIntent` |
| `OR_RS/OR_RU` from different requester | Enqueue / RS-merge / drop-full, do not consume `RECALL.DONE` (`L`) |
| `REC_RESP(...)` | Accepted again because handler checks `opType` but not `stage`; `stage` remains `DONE`, data buffer may be overwritten (`L`, gap) |
| `UPG_REQ(...)` | Reject because outstanding exists (`R`) |
| `INV_ACK(...)` | Idempotent `true` (`R`) |
| `CLR(...)` | Reject: no `GRANT_HANDSHAKE` (`R`) |
| `UPG_DONE(...)` | Reject (`R`) |
| `WB_CLEAN/WB_DROP(owner,...)` | `isLineBusy()==false` because `DONE` is terminal, so writeback proceeds against committed `G_E` as if no outstanding existed (`D-full`, semantic leak) |
| `WB_*(r!=owner,...)` | Owner-mismatch reject, but stale `RECALL.DONE` still remains (`R`) |
| `EVICT(owner,fresh)` | `isLineBusy()==false`; clean owner evict can commit `G_I` while `RECALL.DONE` still blocks queued requesters (`D-full`, semantic leak) |
| `EVICT(r!=owner,fresh)` | Reject, stale `RECALL.DONE` retained (`R`) |

### L8: `G_M × RECALL/DONE`

| Event | Transition |
|---|---|
| `OR_RS/OR_RU` from same requester | Remove terminal `RECALL`, create `GRANT_HANDSHAKE/WAITING_CLEAR`, preserve cached recall data, directory unchanged -> `L3` (`L`); retry is keyed only by requester, not by incoming `reqId`/`writeIntent` |
| `OR_RS/OR_RU` from different requester | Enqueue / RS-merge / drop-full (`L`) |
| `REC_RESP(...)` | Accepted again because handler checks `opType` but not `stage`; `stage` remains `DONE`, data buffer may be overwritten (`L`, gap) |
| `UPG_REQ(...)` | Reject because outstanding exists (`R`) |
| `INV_ACK(...)` | Idempotent `true` (`R`) |
| `CLR(...)` | Reject (`R`) |
| `UPG_DONE(...)` | Reject (`R`) |
| `WB_CLEAN/WB_DROP(owner,...)` | `isLineBusy()==false`; owner writeback commits `G_E` or `G_I` while stale `RECALL.DONE` object remains (`D-full`, semantic leak) |
| `WB_*(r!=owner,...)` | Owner-mismatch reject, stale `RECALL.DONE` retained (`R`) |
| `EVICT(owner,fresh)` | `isLineBusy()==false`; **same dirty-owner evict bug remains reachable** while `RECALL.DONE` still occupies `_outstandingReqs` (`D-full`, illegal edge) |
| `EVICT(r!=owner,fresh)` | Reject, stale `RECALL.DONE` retained (`R`) |

### L9: `G_S × UPGRADE_PENDING/WAITING_ALL_ACKS`

| Event | Transition |
|---|---|
| `INV_ACK(node in upgradeTargetMask)` and not last ack | Decrement `upgradePendingAckCount`, no directory update, remain `L9` (`L`) |
| `INV_ACK(last remaining target)` and no early `UPG_DONE` cached | Set `accepted=1`, `invalidateBarrierDone=1`, advance to `WAITING_LOCAL_DONE` -> `L10` (`L`) |
| `INV_ACK(last remaining target)` with cached early `UPG_DONE` | Commit intended `G_E/G_M`, remove outstanding -> `N2/N3` (`D-full`) |
| `INV_ACK(duplicate)` | Ignore (`R`) |
| `INV_ACK(node not in targetMask or stale epoch)` | Reject (`R`) |
| `UPG_DONE(r=requester)` | Cache tuple (`upgradeDoneArrived=1`) and stay in `L9` until all acks finish (`L`) |
| `UPG_DONE(r!=requester)` | Reject (`R`) |
| `OR_RS/OR_RU` same requester | BUSY (`R`) |
| `OR_RS/OR_RU` different requester | Enqueue / RS-merge / drop-full (`L`) |
| `UPG_REQ(...)` | Reject because outstanding exists (`R`) |
| `REC_RESP(...)` | Reject (`R`) |
| `CLR(...)` | Reject (`R`) |
| `WB_CLEAN/WB_DROP(...)` | Reject BUSY (`R`) |
| `EVICT(fresh)` | Reject BUSY after stale check (`R`) |

### L10: `G_S × UPGRADE_PENDING/WAITING_LOCAL_DONE`

| Event | Transition |
|---|---|
| `UPG_DONE(r=requester, accepted=1)` | Commit intended `G_E/G_M`, remove outstanding, replay queued requesters -> `N2/N3` (`D-full`) |
| `UPG_DONE(r!=requester)` | Reject (`R`) |
| `INV_ACK(...)` | Idempotent `true` because `UPGRADE_PENDING` only consumes acks in `WAITING_ALL_ACKS` (`R`) |
| `OR_RS/OR_RU` same requester | BUSY (`R`) |
| `OR_RS/OR_RU` different requester | Enqueue / RS-merge / drop-full (`L`) |
| `UPG_REQ(...)` | Reject because outstanding exists (`R`) |
| `REC_RESP(...)` | Reject (`R`) |
| `CLR(...)` | Reject (`R`) |
| `WB_CLEAN/WB_DROP(...)` | Reject BUSY (`R`) |
| `EVICT(fresh)` | Reject BUSY after stale check (`R`) |

## 3. Directory-committing vs live-state-only transitions

### Final directory commits (`D-full`)
- `GRANT_HANDSHAKE/WAITING_CLEAR + matching CLR` -> `commitIntendedResult()` updates `state`, `sharersMask`, `epoch`, `residentDirty=1`.
- `UPGRADE_PENDING/WAITING_LOCAL_DONE + matching UPG_DONE` -> `commitIntendedResult()`.
- `UPGRADE_PENDING/WAITING_ALL_ACKS + final INV_ACK + cached early UPG_DONE` -> `commitIntendedResult()` immediately after ack completion.
- Stable-state `WB_CLEAN/WB_DROP` transitions in `processWriteback()`.
- Stable-state `EVICT` transitions in `processEvict()`.
- `RECALL/DONE + WB/EVICT` leaks: code treats terminal `DONE` as not busy, so stable-directory commits can occur while stale terminal outstanding persists.

### Partial directory commits (`D-partial`)
- `INVALIDATE/WAITING_ALL_ACKS + INV_ACK` clears acknowledged sharer bit from committed directory immediately.
- Last invalidate ack may canonicalize `G_S` with zero sharers into committed `G_I` before grant clear.

### Pure live-state-only (`L`)
- All `processOuterRequest()` grant/recall/invalidate/upgrade creation paths.
- `RECALL/WAITING_TARGET_RESP + REC_RESP`.
- `RECALL/DONE + same-requester OR_*` conversion to `GRANT_HANDSHAKE`.
- `UPGRADE_PENDING/WAITING_ALL_ACKS + early UPG_DONE` caching.
- `UPGRADE_PENDING/WAITING_ALL_ACKS + final INV_ACK` when it only moves to `WAITING_LOCAL_DONE`.
- `GRANT_HANDSHAKE/WAITING_CLEAR + replayArmed exact retry`.
- Queueing/merging/dropping competing requesters.

## 4. Illegal / panic / canonical states

### ResidentDir hard canonical checks
- `G_S` with `sharersMask==0` -> panic (`ResidentDir::validateCanonical`).
- `G_I` with `sharersMask!=0` -> panic.
- `G_E` or `G_M` with `popcount(sharersMask)!=1` -> panic.

### UBCC commit-time canonical repair/assertion
- `commitIntendedResult()` repairs `G_E/G_M` mask to one-hot owner if `intendedOwnerNode>=0` and mask is not one-hot.
- After repair, `panic_if(popcount(entry.sharersMask)!=1)` still fires for `G_E/G_M` if canonicality is not restored.

### Unreachable-under-intended-protocol combinations
- `G_I/G_S + RECALL/*` should not occur.
- `G_E/G_M + INVALIDATE/*` should not occur.
- `G_I/G_E/G_M + UPGRADE_PENDING/*` should not occur under intended sharer-upgrade semantics.
- `GRANT_HANDSHAKE` in any stage other than `WAITING_CLEAR` should not persist.
- `RECALL/DONE` on `G_I/G_S` should not occur.

### Code-admitted illegal / hazardous edges
- `processOuterUpgradeReq()` does not guard `entry.state==G_S`; owner on `G_E/G_M` is accepted into `UPGRADE_PENDING/WAITING_LOCAL_DONE`.
- `processOuterUpgradeReq()` does not validate `requesterNode` range; negative values bypass sharer-membership check.
- `RECALL/DONE` consumption checks only `requesterNode`; same-requester retry with changed `reqType`/`writeIntent` is accepted, while preserved `reqId/baseEpoch` come from the old recall outstanding.
- `processWriteback()` has no sharer/owner-membership check when committed state is `G_I` or `G_S`; `WB_CLEAN` can fabricate `G_E`, and `WB_DROP` can drop a shared line with an arbitrary requester.
- `processEvict()` clears the sharer bit before dirty-owner validation; on `G_M` the owner can clean-evict illegally and commit `G_I`.
- `RECALL/DONE` is treated as not busy by `isLineBusy()`, so `WB_*` and `EVICT` can mutate the committed directory while a terminal outstanding still blocks `processOuterRequest()`/`UPG_REQ()`.

## 5. Coverage and liveness gaps

1. **No timeout/cancel path is implemented despite enum support**
   - `RECALL/WAITING_TARGET_RESP`, `INVALIDATE/WAITING_ALL_ACKS`, `GRANT_HANDSHAKE/WAITING_CLEAR`, `UPGRADE_PENDING/WAITING_ALL_ACKS`, and `UPGRADE_PENDING/WAITING_LOCAL_DONE` never transition to `TIMED_OUT` or `CANCELLED`.
   - Lost `REC_RESP`, `INV_ACK`, `CLR`, or `UPG_DONE` can pin a line forever.

2. **`RECALL/DONE` depends on requester retry for forward progress**
   - No autonomous transition exists from `RECALL/DONE` to `GRANT_HANDSHAKE`.
   - If requester never retries, the terminal outstanding remains forever and later requesters only enqueue.

3. **`GRANT_HANDSHAKE/WAITING_CLEAR` mismatch handling is asymmetric**
   - `CLR(epoch mismatch)` retires the outstanding to tombstone(`accepted=false`) and unblocks the PA.
   - `CLR(reqId mismatch)` and `CLR(src mismatch)` only reject and keep the outstanding live.

4. **`REC_RESP` is accepted again in `RECALL/DONE`**
   - `processRecallResponse()` validates `opType==RECALL` but does not require `stage==WAITING_TARGET_RESP`.
   - Duplicate/late responses can overwrite cached recall data.

5. **Writeback and evict are not total with respect to live terminal metadata**
   - `isLineBusy()` ignores `DONE/CANCELLED/TIMED_OUT`, but `_outstandingReqs` still contains `RECALL/DONE`.
   - Result: `WB_*`/`EVICT` can commit the directory while `processOuterRequest()` still sees an existing outstanding and queues later requesters behind it.

6. **Outer unique from `G_S` for an existing sharer has no direct in-function transition**
   - `processOuterRequest()` returns BUSY and requires the separate `processOuterUpgradeReq()` path.

7. **Writeback membership validation is missing**
   - `G_I` and `G_S` accept `WB_*` from arbitrary nodes.

8. **Dirty-owner evict bug is reachable from both `N3` and `L8`**
   - The code path intended to reject dirty owner eviction is bypassed by mutation order.

## 6. Bottom line

The reviewed slice exposes **15 composite states**: **4 stable no-outstanding states** plus **11 reachable live-outstanding states**. Across these states, every event family (`OR_*`, `UPG_*`, `REC_RESP`, `INV_ACK`, `CLR`, `WB_*`, `EVICT`) either has a defined transition or an explicit rejection. The main hazards are not missing dispatch cases; they are **semantic leaks**: terminal `RECALL.DONE` lingering in `_outstandingReqs`, missing membership checks for writeback, and the dirty-owner evict bug.
