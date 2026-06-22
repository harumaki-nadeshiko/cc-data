# FV-1: UBCC Single-Line State Machine — Reachable Combinations, Legal Edges, Illegal Edges

**Method**: static analysis of `UBCCController.hh/.cc`, `ResidentDir.hh/.cc`.

**Scope**:
- `UBCCController.hh:28-179, 646-651`
- `UBCCController.cc:146-240, 360-1030, 1054-1428, 1514-1754, 1761-2145, 2197-2660, 2719-2765`
- `ResidentDir.hh:14-43`
- `ResidentDir.cc:31-49`

---

## 0. Normalized notation

- `I(rd,e)` = committed `G_I`, `sharersMask=0`, `residentDirty=rd`, epoch `e`
- `S(P,rd,e)` = committed `G_S`, `P!=0`, no owner, epoch `e`
- `E(o,rd,e)` = committed `G_E`, `P={o}` one-hot
- `M(o,rd,e)` = committed `G_M`, `P={o}` one-hot
- `e+1` means `reservedEpoch = allocateReservedEpoch(entry)`; committed epoch changes only on commit rows unless otherwise noted
- `LO` = live outstanding for this line; one live entry max (`createOutstanding()` / `_outstandingReqs` is single-keyed)
- `Dir write?` is the required binary classification:
  - `No` = only live state / queue / tombstone changes
  - `Yes` = committed `DirEntry` is updated

`residentDirty` is **not a control guard** in these handlers. It is a silent dimension for legality; successful directory writes in this scope set `residentDirty=true`.

---

## 1. Reachable single-line state inventory

### 1.1 Stable committed states

| Symbol | Canonical constraints | Reachable? |
|---|---|---|
| `I(rd,e)` | `state=G_I`, `sharersMask=0` | Yes |
| `S(P,rd,e)` | `state=G_S`, `P!=0` | Yes |
| `E(o,rd,e)` | `state=G_E`, `popcount(sharersMask)=1`, owner=`o` | Yes |
| `M(o,rd,e)` | `state=G_M`, `popcount(sharersMask)=1`, owner=`o` | Yes |

### 1.2 Reachable live outstanding combinations

| LO symbol | Concrete fields | Reachable precondition |
|---|---|---|
| `∅` | no outstanding | any committed state |
| `R_WAIT(r,o,reqType,wi,e+1,id)` | `RECALL/WAITING_TARGET_RESP`, `replayArmed=0` | committed `E(o)` or `M(o)`, incoming requester `r!=o` |
| `R_DONE(r,o,origReqType,origWi,e+1,id)` | `RECALL/DONE`, `recallBarrierDone=1`, data may be buffered | only after accepted `RecallResp` |
| `INV_WAIT(r,T,A,e+1,id,wi)` | `INVALIDATE/WAITING_ALL_ACKS`, `targetMask=T!=0`, `ackMask=A⊂T`, `pendingAckCount=|T-A|` | committed `S(P)`, requester `r∉P`, `T=P` |
| `GH_WAIT(intended,replayArmed,e+1,id)` | `GRANT_HANDSHAKE/WAITING_CLEAR` | created directly, from `R_DONE`, or by `INV_WAIT` completion |
| `UPG_WAIT_ACKS(r,T,A,e+1,id)` | `UPGRADE_PENDING/WAITING_ALL_ACKS`, `accepted=0`, `upgradeTargetMask=T!=0`, `upgradeAckMask=A⊂T` | committed `S(P)`, requester `r∈P`, `T=P\{r}` |
| `UPG_WAIT_DONE(r,e+1,id)` | `UPGRADE_PENDING/WAITING_LOCAL_DONE`, `accepted=1` | `UPG_WAIT_ACKS` after all acks, or immediate no-other-sharer fast path |

### 1.3 Unreachable / dead combinations

| Combination | Why unreachable |
|---|---|
| `RECALL` over committed `I` or `S` | created only from `G_E/G_M` (`processOuterRequest` G_E/G_M branch) |
| `INVALIDATE` over committed `I/E/M` | created only from `G_S + GlobalReadUnique + requester not existing sharer + otherSharers!=0` |
| `UPGRADE_PENDING` over committed `I/E/M` | `processOuterUpgradeReq` requires requester already in committed sharers mask |
| externally visible `CREATED` stage | every creator immediately overwrites stage before function return |
| externally visible `CANCELLED`, `TIMED_OUT`, `PERSISTENT_BUSY` | enums exist but no assignment sites in analyzed `.cc` |
| `G_S + RU` immediate `GRANT_HANDSHAKE` fast path with valid requester | unreachable under canonical `G_S`: if requester is the sole sharer, code exits earlier to `UPGRADE_PENDING`; otherwise `otherSharers!=0` |

---

## 2. Golden transition tables by critical sub-machine

## 2.1 RECALL lifecycle (`G_E/G_M -> RECALL.CREATED -> DONE -> GRANT_HANDSHAKE`)

### 2.1.1 Entry / wait / completion table

| Committed state | LO before | Input event | Guard | Next state / LO after | Actions | Dir write? | Legality |
|---|---|---|---|---|---|---|---|
| `E(o,rd,e)` or `M(o,rd,e)` | `∅` | `OuterReq(RS or RU, requester=r!=o)` | none | same committed state, `R_WAIT(r,o,reqType,wi,e+1,id)` | `initiateRecall()`, create `RECALL`, capture `reqType/writeIntent/baseEpoch/reqId/reservedEpoch`, return BUSY | No | **LEGAL** |
| `E/M` | `R_WAIT` | `RecallResp(owner=o, fresh epoch, reqId match)` | `targetNode` matches, `reqId` matches, not stale | same committed state, `R_DONE(...)` | `recallBarrierDone=true`, `stage=DONE`, optional `dataBuf` capture | No | **LEGAL** |
| `E/M` | `R_WAIT` | `RecallResp(wrong owner)` | `ownerNode != targetNode` | unchanged | reject | No | **ILLEGAL-WITH-REASON**: target mismatch |
| `E/M` | `R_WAIT` | `RecallResp(wrong reqId)` | `reqId != ost.reqId` | unchanged | reject | No | **ILLEGAL-WITH-REASON**: reqId mismatch |
| `E/M` | `R_WAIT` | `RecallResp(stale epoch)` | `checkEpochForLine()==false` | unchanged | reject, `staleRejectedCount++` | No | **ILLEGAL-WITH-REASON**: stale epoch |
| `E/M` | `R_WAIT` | `OuterReq(same requester)` | live outstanding exists, not replay hit | unchanged | return BUSY | No | **LEGAL** rejection |
| `E/M` | `R_WAIT` | `OuterReq(different requester)` | queue space / RS-merge rules | unchanged | enqueue / merge / drop-full | No | **LEGAL** |
| `E/M` | `R_WAIT` | `Writeback` or `Evict` | `isLineBusy()==true` | unchanged | reject BUSY | No | **ILLEGAL-WITH-REASON**: non-terminal outstanding blocks local mutation |
| `E/M` | `R_WAIT` | `Clear` | no `GRANT_HANDSHAKE` | unchanged | drop | No | **ILLEGAL-WITH-REASON** |
| `E/M` | `R_WAIT` | `InvalidationAck` | wrong op type | unchanged | idempotent `true` | No | **LEGAL** no-op |
| `E/M` | `R_WAIT` | `OuterUpgradeReq` / `UpgradeDone` | outstanding exists / wrong op type | unchanged | reject | No | **ILLEGAL-WITH-REASON** |

### 2.1.2 `RECALL.DONE` consumption table

| Committed state | LO before | Input event | Guard | Next state / LO after | Actions | Dir write? | Legality |
|---|---|---|---|---|---|---|---|
| current committed `E/M/I/S` snapshot | `R_DONE(r,o,origReqType,origWi,e+1,id)` | `OuterReq(same requester=r)` | **code checks requester only** | same committed state, `GH_WAIT(intended from *incoming* reqType/wi, replayArmed=0, e+1, id)` | remove `RECALL`, create new `GRANT_HANDSHAKE`, copy recall data buffer, `dataSource=RecallBuffer` | No | **NEEDS-INVARIANT-CHECK**: code does **not** validate retry `reqId`, `reqType`, `writeIntent`, or `baseEpoch`; same requester can consume `RECALL.DONE` with a different tuple |
| current committed snapshot | `R_DONE(...)` | `OuterReq(different requester)` | queue rules | unchanged (`R_DONE` remains live) | enqueue / merge / drop-full | No | **LEGAL**, but progress depends on original requester retrying |
| current committed snapshot | `R_DONE(...)` | duplicate `RecallResp` | `recallBarrierDone==true` | unchanged | idempotent `true` | No | **LEGAL** no-op |
| current committed snapshot | `R_DONE(...)` | `Writeback` / `Evict` | `isLineBusy()==false` because `DONE` is treated as terminal | committed state may mutate while `R_DONE` still occupies `_outstandingReqs` | normal WB/evict handler runs | **Yes** | **NEEDS-INVARIANT-CHECK**: `RECALL.DONE` is not busy, so local mutation can race with later same-requester retry |
| current committed snapshot | `R_DONE(...)` | no further matching retry | none | orphan `R_DONE` persists indefinitely | no timeout / cleanup path | No | **ILLEGAL-WITH-REASON**: live-slot leak; blocks new outstanding creation forever |

**Sub-machine verdict**:
- Final directory commit does **not** happen in RECALL itself.
- RECALL is live-only until consumed into `GRANT_HANDSHAKE`.
- Highest-risk edge: same-requester `RECALL.DONE` consumption is under-validated.

---

## 2.2 INVALIDATE lifecycle (`G_S -> INVALIDATE.CREATED -> WAITING_ALL_ACKS -> GRANT_HANDSHAKE`)

### 2.2.1 Creation and per-ack table

| Committed state | LO before | Input event | Guard | Next state / LO after | Actions | Dir write? | Legality |
|---|---|---|---|---|---|---|---|
| `S(P,rd,e)`, requester `r∉P`, `P!=0` | `∅` | `OuterReq(RU, requester=r, wi∈{0,1})` | `otherSharers=P!=0` | committed still `S(P,rd,e)`, `INV_WAIT(r,T=P,A=0,e+1,id,wi)` | create `INVALIDATE`, freeze intended result `G_E/G_M` for `r`, return BUSY | No | **LEGAL** |
| `S(P_rem,rd,e)` or `I(rd,e)` after prior acks | `INV_WAIT(r,T,A,e+1,id,wi)` | `InvalidationAck(node∈T\A, fresh epoch)` | node targeted, not duplicate | if not final ack: stay `INV_WAIT` with `A'=A∪{node}`; committed sharers remove `node`; if last sharer removed, committed state may become `I` | `ackMask|=bit`, `pendingAckCount--`, `_directory.update(entry)` | **Yes** | **NEEDS-INVARIANT-CHECK**: handler ignores `reqId` parameter completely; acceptance relies on external guarantee that ack belongs to this invalidate instance |
| `S(P_last,rd,e)` | `INV_WAIT(r,T,A,e+1,id,wi)` | final `InvalidationAck(last node)` | `pendingAckCount` reaches 0 | committed state reflects all acked removals; LO becomes `GH_WAIT(intended=G_E/G_M for r, replayArmed=1, e+1,id)` | mark invalidate barrier done, mutate outstanding in place to `GRANT_HANDSHAKE/WAITING_CLEAR` | **Yes** | **NEEDS-INVARIANT-CHECK**: same missing `reqId` validation |
| any | `INV_WAIT` | duplicate `InvalidationAck(node∈A)` | duplicate bit already set | unchanged | return true | No | **LEGAL** idempotent |
| any | `INV_WAIT` | `InvalidationAck(node∉T)` | not targeted | unchanged | reject false | No | **ILLEGAL-WITH-REASON**: ack from non-target |
| any | `INV_WAIT` | `InvalidationAck(stale epoch)` | stale | unchanged | reject false | No | **ILLEGAL-WITH-REASON** |
| any | `INV_WAIT` | `OuterReq(same requester)` | live outstanding exists | unchanged | BUSY | No | **LEGAL** rejection |
| any | `INV_WAIT` | `OuterReq(different requester)` | queue rules | unchanged | enqueue / merge / drop-full | No | **LEGAL** |
| any | `INV_WAIT` | `Writeback` / `Evict` | `isLineBusy()==true` | unchanged | reject BUSY | No | **ILLEGAL-WITH-REASON** |
| any | `INV_WAIT` | `Clear` / `RecallResp` / `UpgradeDone` | wrong op type | unchanged | drop / reject / idempotent | No | **ILLEGAL-WITH-REASON** |

### 2.2.2 Notes on commit classification

- `InvalidationAck` is **not** the final reserve-then-commit point, but it **does write the committed directory** by shrinking `sharersMask` early.
- Therefore the binary classification is `Dir write? = Yes` for accepted invalidate acks.
- This means a later failed `Clear` does **not** roll back already-committed sharer removals.

---

## 2.3 GRANT_HANDSHAKE lifecycle (`RECALL.DONE` or `INVALIDATE.DONE` or direct grant -> WAITING_CLEAR -> Clear -> retire`)

### 2.3.1 Grant creation matrix

| Committed state | LO before | Input event | Next LO | Intended committed result on later Clear | Dir write now? | Legality |
|---|---|---|---|---|---|---|
| `I(rd,e)` | `∅` | `OuterReq(RS,r)` | `GH_WAIT(G_S, replayArmed=0, e+1,id)` | `S({r},true,e+1)` | No | **LEGAL** |
| `I(rd,e)` | `∅` | `OuterReq(RU,r,wi=0)` | `GH_WAIT(G_E, replayArmed=0, e+1,id)` | `E(r,true,e+1)` | No | **LEGAL** |
| `I(rd,e)` | `∅` | `OuterReq(RU,r,wi=1)` | `GH_WAIT(G_M, replayArmed=0, e+1,id)` | `M(r,true,e+1)` | No | **LEGAL** |
| `S(P,rd,e)` | `∅` | `OuterReq(RS,r)` | `GH_WAIT(G_S, replayArmed=0, e+1,id)` | `S(P∪{r},true,e+1)` | No | **LEGAL** |
| `E(o,rd,e)` or `M(o,rd,e)` | `∅` | `OuterReq(RS,r=o)` | `GH_WAIT(G_S, replayArmed=0, e+1,id)` | `S({o},true,e+1)` | No | **LEGAL** |
| `E(o,rd,e)` or `M(o,rd,e)` | `∅` | `OuterReq(RU,r=o,wi∈{0,1})` | `GH_WAIT(G_E/G_M, replayArmed=0, e+1,id)` | owner stays `o` | No | **LEGAL** |
| any committed snapshot | `R_DONE(...)` | `OuterReq(same requester)` | `GH_WAIT(..., replayArmed=0)` | depends on incoming `reqType/wi` | No | **NEEDS-INVARIANT-CHECK** (same issue as §2.1.2) |
| current committed snapshot after all acks | `INV_WAIT(... pendingAckCount=1)` | final `InvalidationAck` | `GH_WAIT(..., replayArmed=1)` | later `E/M(requester)` | **Yes** (partial sharer removal) | **NEEDS-INVARIANT-CHECK** |
| current committed snapshot after replay | `∅` | replayed `OuterReq(...)` from queue | `GH_WAIT(..., replayArmed=1)` | depends on replayed request | No | **LEGAL** |

### 2.3.2 `WAITING_CLEAR` table

| Committed state | LO before | Input event | Guard | Next state / LO after | Actions | Dir write? | Legality |
|---|---|---|---|---|---|---|---|
| any | `GH_WAIT(intended,replayArmed,e+1,id)` | `Clear(src=requester, epoch=baseEpoch, reqId=id)` | requester, epoch, reqId, stage all match | commit intended result; LO=`∅`; tombstone accepted=true | `commitIntendedResult()`, `_directory.update()`, `retireToTombstone(true)`, remove outstanding | **Yes** | **LEGAL** |
| any | `GH_WAIT(...)` | `Clear(epoch mismatch)` | `clearEpoch != ost.baseEpoch` | committed state unchanged; LO=`∅`; tombstone accepted=false | `retireToTombstone(false)`, remove outstanding | No | **ILLEGAL-WITH-REASON**: stale/foreign Clear |
| any | `GH_WAIT(...)` | `Clear(reqId mismatch)` | mismatch | unchanged | drop | No | **ILLEGAL-WITH-REASON** |
| any | `GH_WAIT(...)` | `Clear(src mismatch)` | mismatch | unchanged | drop | No | **ILLEGAL-WITH-REASON** |
| any | `GH_WAIT(...)` | `OuterReq(same requester, exact tuple, replayArmed=1)` | replay hit | unchanged | return grant directly | No | **LEGAL** |
| any | `GH_WAIT(...)` | `OuterReq(same requester, not replay-hit)` | no replay match | unchanged | BUSY | No | **LEGAL** rejection |
| any | `GH_WAIT(...)` | `OuterReq(different requester)` | queue rules | unchanged | enqueue / merge / drop-full | No | **LEGAL** |
| any | `GH_WAIT(...)` | `Writeback` / `Evict` | `isLineBusy()==true` | unchanged | reject BUSY | No | **ILLEGAL-WITH-REASON** |
| any | `GH_WAIT(...)` | `RecallResp` / `InvalidationAck` / `UpgradeDone` | wrong op type | unchanged | reject / idempotent | No | **LEGAL** no-op or explicit reject |

### 2.3.3 Tombstone side-effect edges (out-of-band but required for correctness)

| State after previous Clear | Input event | Actual code | Assessment |
|---|---|---|---|
| tombstone `accepted=true` | duplicate `Clear` same `(line,epoch,reqId)` | returns cached `accepted=true` | **LEGAL** |
| tombstone `accepted=false` | duplicate `Clear` same tuple | returns cached `accepted=false` | **LEGAL** |
| tombstone any accepted bit | `processOuterRequest()` same `(line,baseEpoch,reqId)` | returns `GlobalGrantShared` unconditionally, ignoring `accepted` and original grant type | **ILLEGAL-WITH-REASON**: requester can get a grant from a rejected tombstone, and unique grants replay as shared |

---

## 2.4 UPGRADE_PENDING lifecycle (`G_S -> UPGRADE_PENDING.CREATED -> WAITING_ALL_ACKS / WAITING_LOCAL_DONE -> commit`)

### 2.4.1 Creation table

| Committed state | LO before | Input event | Guard | Next state / LO after | Actions | Dir write? | Legality |
|---|---|---|---|---|---|---|---|
| `S(P,rd,e)`, requester `r∈P`, `T=P\{r} != 0` | `∅` | `OuterUpgradeReq(r,desiredPerm)` | no existing outstanding | committed unchanged, `UPG_WAIT_ACKS(r,T,A=0,e+1,id)` | create `UPGRADE_PENDING`, freeze `upgradeTargetMask=T`, intended `G_E/G_M`, `accepted=false` | No | **LEGAL** |
| `S({r},rd,e)` | `∅` | `OuterUpgradeReq(r,desiredPerm)` | no existing outstanding | committed unchanged, `UPG_WAIT_DONE(r,e+1,id)` | create `UPGRADE_PENDING`, `accepted=true`, no invalidations needed | No | **LEGAL** |
| `S(P,rd,e)`, requester `r∉P` | `∅` | `OuterUpgradeReq(r,...)` | requester not a committed sharer | unchanged | reject false | No | **ILLEGAL-WITH-REASON** |
| any | non-empty LO | `OuterUpgradeReq(...)` | `findOutstanding()!=nullptr` | unchanged | reject false | No | **ILLEGAL-WITH-REASON** |

### 2.4.2 `WAITING_ALL_ACKS` / `WAITING_LOCAL_DONE` table

| Committed state | LO before | Input event | Guard | Next state / LO after | Actions | Dir write? | Legality |
|---|---|---|---|---|---|---|---|
| `S(P,rd,e)` | `UPG_WAIT_ACKS(r,T,A,e+1,id)` | `InvalidationAck(node∈T\A, fresh epoch)` | targeted, not duplicate | same committed `S(P,rd,e)`; if not final ack stay `UPG_WAIT_ACKS` with `A'`; if final ack then `UPG_WAIT_DONE` | `upgradeAckMask|=bit`, `upgradePendingAckCount--`; **no directory update** on upgrade path | No | **NEEDS-INVARIANT-CHECK**: `reqId` parameter is ignored here too |
| `S(P,rd,e)` | `UPG_WAIT_ACKS(...)` | final `InvalidationAck` and `upgradeDoneArrived==false` | all acks complete | committed unchanged, `UPG_WAIT_DONE(r,e+1,id)` | `accepted=true`, `stage=WAITING_LOCAL_DONE`, send `UpgradeAckNotify` | No | **NEEDS-INVARIANT-CHECK** |
| `S(P,rd,e)` | `UPG_WAIT_ACKS(...)` | final `InvalidationAck` and `upgradeDoneArrived==true` | all acks complete | commit intended `E/M(r,true,e+1)`; LO=`∅` | `commitIntendedResult()`, `_directory.update()`, remove outstanding | **Yes** | **NEEDS-INVARIANT-CHECK**: cached early `UpgradeDone` was never tuple-validated |
| `S(P,rd,e)` | `UPG_WAIT_ACKS(...)` | `UpgradeDone(src=requester)` | **code checks requester only** | committed unchanged, same `UPG_WAIT_ACKS`, but `upgradeDoneArrived=true` | cache `epoch/reqId` only; no validation | No | **NEEDS-INVARIANT-CHECK**: stale or wrong-reqId `UpgradeDone` can arm later commit |
| `S(P,rd,e)` | `UPG_WAIT_ACKS(...)` | `UpgradeDone(wrong requester)` | requester mismatch | unchanged | reject false | No | **ILLEGAL-WITH-REASON** |
| `S(P,rd,e)` | `UPG_WAIT_DONE(r,e+1,id)` | `UpgradeDone(src=requester)` | `accepted==true`, stage correct | commit intended `E/M(r,true,e+1)`; LO=`∅` | `commitIntendedResult()`, `_directory.update()`, remove outstanding | **Yes** | **NEEDS-INVARIANT-CHECK**: commit path still does not validate `epoch` or `reqId` |
| any | `UPG_WAIT_DONE` | `InvalidationAck(...)` | wrong stage for upgrade path | unchanged | idempotent `true` | No | **LEGAL** no-op |
| any | `UPG_WAIT_ACKS` or `UPG_WAIT_DONE` | `OuterReq(same requester)` | live outstanding exists | unchanged | BUSY | No | **LEGAL** rejection |
| any | `UPG_WAIT_ACKS` or `UPG_WAIT_DONE` | `OuterReq(different requester)` | queue rules | unchanged | enqueue / merge / drop-full | No | **LEGAL** |
| any | `UPG_WAIT_ACKS` or `UPG_WAIT_DONE` | `Writeback` / `Evict` | `isLineBusy()==true` | unchanged | reject BUSY | No | **ILLEGAL-WITH-REASON** |
| any | `UPG_WAIT_ACKS` or `UPG_WAIT_DONE` | `Clear` / `RecallResp` | wrong op type | unchanged | drop / reject | No | **ILLEGAL-WITH-REASON** |

---

## 2.5 Writeback / Evict stable-state machine (`G_M/G_E -> writeback -> G_I or G_E`, plus `G_S` sharer evict)

### 2.5.1 Writeback table

| Committed state | LO before | Input event | Guard | Next committed state | Actions | Dir write? | Legality |
|---|---|---|---|---|---|---|---|
| `M(o,rd,e)` | `∅` | `Writeback(src=o, keepAsClean=true)` | owner matches, not busy, fresh epoch | `E(o,true,e)` | owner retains clean exclusive | **Yes** | **LEGAL** |
| `M(o,rd,e)` or `E(o,rd,e)` | `∅` | `Writeback(src=o, keepAsClean=false)` | owner matches, not busy, fresh epoch | `I(true,e)` | owner drops line | **Yes** | **LEGAL** |
| `E(o,rd,e)` | `∅` | `Writeback(src=o, keepAsClean=true)` | owner matches | `E(o,true,e)` | effectively clean self-writeback / retain ownership | **Yes** | **LEGAL** |
| `E(o,rd,e)` or `M(o,rd,e)` | `∅` | `Writeback(src!=o, ...)` | owner mismatch | unchanged | reject false | No | **ILLEGAL-WITH-REASON** |
| `S(P,rd,e)` | `∅` | `Writeback(src=x, keepAsClean=true/false)` | owner lookup returns `-1`, so mismatch check is bypassed | `keepAsClean=true -> E(x,true,e)`; `keepAsClean=false -> I(true,e)` | handler accepts even though source is only a sharer or foreign node | **Yes** | **ILLEGAL-WITH-REASON**: writeback should require unique owner; current code allows `G_S` writeback |
| `I(rd,e)` | `∅` | `Writeback(src=x, keepAsClean=true/false)` | owner lookup returns `-1`, so mismatch check is bypassed | `keepAsClean=true -> E(x,true,e)`; `keepAsClean=false -> I(true,e)` | line can be conjured into ownership from `G_I` | **Yes** | **ILLEGAL-WITH-REASON**: current code accepts writeback on invalid line |
| any | non-terminal LO | `Writeback(...)` | `isLineBusy()==true` | unchanged | reject false | No | **ILLEGAL-WITH-REASON** |
| any | `R_DONE(...)` | `Writeback(...)` | `isLineBusy()==false` for terminal `DONE` | WB may proceed despite live terminal recall entry | normal writeback path | **Yes** | **NEEDS-INVARIANT-CHECK** |

### 2.5.2 Evict table

| Committed state | LO before | Input event | Guard | Next committed state | Actions | Dir write? | Legality |
|---|---|---|---|---|---|---|---|
| `S(P,rd,e)`, `s∈P`, `|P|>1` | `∅` | `Evict(src=s)` | fresh epoch, not busy | `S(P\{s},true,e)` | remove one sharer | **Yes** | **LEGAL** |
| `S({s},rd,e)` | `∅` | `Evict(src=s)` | fresh epoch, not busy | `I(true,e)` | last sharer removed | **Yes** | **LEGAL** |
| `E(o,rd,e)` | `∅` | `Evict(src=o)` | fresh epoch, not busy | `I(true,e)` | owner bit is removed; line becomes invalid | **Yes** | **LEGAL** |
| `M(o,rd,e)` | `∅` | `Evict(src=o)` | **should reject dirty owner** | **actual code drops to `I(true,e)`** | bug: code clears sharer bit **before** recomputing owner, so dirty-owner rejection never fires | **Yes** | **ILLEGAL-WITH-REASON**: critical implementation bug; dirty owner evict is erroneously accepted |
| any stable state | `∅` | `Evict(src not owner/sharer)` | source absent from sharers / owner | unchanged | reject false | No | **ILLEGAL-WITH-REASON** |
| any | non-terminal LO | `Evict(...)` | `isLineBusy()==true` | unchanged | reject false | No | **ILLEGAL-WITH-REASON** |
| any | `R_DONE(...)` | `Evict(...)` | `isLineBusy()==false` for terminal `DONE` | evict may proceed despite live terminal recall entry | normal evict path | **Yes** | **NEEDS-INVARIANT-CHECK** |

---

## 3. Illegal edge list (ranked)

### Critical

1. **WB-1: `G_I` / `G_S` writeback accepted**
   - Code path: `processWriteback()` (`UBCCController.cc:1514-1596`)
   - Trace:
     1. committed state = `I` or `S`
     2. `ownerNode = ownerFromSharers(entry) = -1`
     3. owner-mismatch guard does not fire
     4. handler accepts writeback
     5. `keepAsClean=true` creates `G_E(requester)` from `G_I/G_S`, or `keepAsClean=false` forces `G_I`
   - Why illegal: writeback must come from current exclusive owner; code permits sharer/foreign source.

2. **EV-1: dirty-owner evict is erroneously accepted**
   - Code path: `processEvict()` (`UBCCController.cc:1642-1754`)
   - Trace:
     1. committed state = `M(o)`
     2. `entry.sharersMask &= ~bit(o)` happens first
     3. `ownerFromSharers(entry)` now returns `-1`
     4. dirty-owner rejection block is skipped
     5. handler commits `G_I`
   - Why illegal: dirty owner must write back first.

3. **UPG-1: `UpgradeDone` commits without `(epoch,reqId)` validation**
   - Code path: `processOuterUpgradeDone()` (`UBCCController.cc:1879-1979`)
   - A same-requester `UpgradeDone` in `WAITING_LOCAL_DONE` commits immediately; an early `UpgradeDone` in `WAITING_ALL_ACKS` arms later auto-commit.
   - Why illegal: stale / duplicated / mismatched Done can commit a new owner.

### High

4. **REC-1: same-requester `RECALL.DONE` consumption lacks tuple validation**
   - Code path: `processOuterRequest()` G_E/G_M branch (`UBCCController.cc:718-778`)
   - Same requester can consume `RECALL.DONE` with different `reqId`, `reqType`, `writeIntent`, `baseEpoch`.
   - Why illegal: retry identity is assumed, not checked.

5. **TS-1: tombstone replay in `processOuterRequest()` ignores `accepted` and grant type**
   - Code path: `processOuterRequest()` (`UBCCController.cc:529-539`)
   - Any matching tombstone causes `GlobalGrantShared` return, even if tombstone came from rejected `Clear` (`accepted=false`) or from unique grant.

6. **REC-2: orphan `RECALL.DONE` leak**
   - No timeout / cleanup path exists; `RECALL.DONE` occupies `_outstandingReqs[linePa]` indefinitely if requester never retries.
   - This blocks new outstanding allocation on the line.

### Medium

7. **INV-1 / UPG-2: `InvalidationAck` ignores `reqId`**
   - Code path: `processInvalidationAck()` (`UBCCController.cc:1210-1428`)
   - Acceptance is keyed only by epoch freshness + target membership + duplicate mask.

8. **REC-3: `RECALL.DONE` is not busy**
   - `isLineBusy()` treats `DONE` as non-busy.
   - Writeback / evict can mutate committed state while terminal recall metadata still awaits same-requester retry.

---

## 4. Edge classification summary: live-only vs committed-directory write

### Only modifies live state (`Dir write? = No`)

- `RECALL` creation and `RecallResp -> DONE`
- direct `GRANT_HANDSHAKE` creation
- `INVALIDATE` creation
- `UPGRADE_PENDING` creation
- `UPGRADE_PENDING` ack accumulation before final commit
- queue enqueue / merge / drop-full
- replay-armed grant hits
- tombstone creation / lookup

### Commits directory (`Dir write? = Yes`)

- `processClear()` success path: final reserve-then-commit point
- accepted `processInvalidationAck()` on `INVALIDATE`: partial committed sharer removal
- accepted `processOuterUpgradeDone()` commit path
- auto-commit after early cached `UpgradeDone` + final upgrade ack
- `processWriteback()` success path
- `processEvict()` success path

---

## 5. Bottom line

The reachable UBCC single-line machine is structurally small and mostly well-serialized, but the current implementation has **four correctness-critical under-validated edges**:

1. `G_I/G_S` writeback acceptance
2. dirty-owner evict acceptance
3. upgrade commit without `(epoch,reqId)` validation
4. `RECALL.DONE` consumption without retry-tuple validation

These must be treated as **golden-table violations**, not merely test gaps.
