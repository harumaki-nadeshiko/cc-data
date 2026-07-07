---------------------------- MODULE ubcc_protocol_core ----------------------------
EXTENDS Integers, Naturals, FiniteSets, Sequences, TLC

(***************************************************************************)
(* UBCC single-PA protocol core.                                           *)
(*                                                                         *)
(* Abstracted away by design:                                              *)
(*   - backstore / DRAM shadow                                             *)
(*   - Bloom filter                                                        *)
(*   - MetaRNF and global flight budgeting                                 *)
(*                                                                         *)
(* Modeled directly from current UBCC/EP protocol behavior:                *)
(*   - reserve-then-commit                                                  *)
(*   - requester-private RECALL.DONE                                       *)
(*   - INVALIDATE ack path with mandatory G_S -> G_I intermediate          *)
(*   - UPGRADE_PENDING dual-stage acceptance                               *)
(*   - recall orphan disappearance without committed-directory mutation     *)
(***************************************************************************)

CONSTANTS Nodes, MaxEpoch, TombstoneWindow

ASSUME Nodes = {0, 1, 2}
ASSUME MaxEpoch \in Nat
ASSUME MaxEpoch > 0
ASSUME TombstoneWindow \in Nat
ASSUME TombstoneWindow > 0

NoneNode == -1
ReqIds == 0..2
MaxTick == TombstoneWindow + MaxEpoch + 6

(* Recall-orphan cleanup timeout (frozen solution recall_orphan_solution.md).  *)
(* A RECALL stuck in WAITING_TARGET_RESP / DONE past createTick + RecallTimeout *)
(* is a leaked (orphan) entry that permanently blocks the PA slot. The frozen   *)
(* fix ("double-layer lazy + timer cleanup") discards it: no rollback, no       *)
(* committed-directory mutation, epoch/dataBuf dropped. Modeled abstractly as   *)
(* a timeout-gated cleanup that frees the outstanding slot. Kept small so the   *)
(* timeout is reachable inside the bounded MaxTick horizon.                     *)
RecallTimeout == 2

MESIState == {"G_I", "G_S", "G_E", "G_M"}
OpType == {"NONE", "RECALL", "INVALIDATE", "GRANT_HANDSHAKE", "UPGRADE_PENDING"}
OpStage == {"CREATED", "WAITING_TARGET_RESP", "WAITING_ALL_ACKS", "WAITING_CLEAR",
            "WAITING_LOCAL_DONE", "DONE"}
ReqType == {"RS", "RU"}

VARIABLES dir, ost, tombstone, commitLog, epochLog, tick

Vars == <<dir, ost, tombstone, commitLog, epochLog, tick>>

EmptyOutstanding ==
    [ valid           |-> FALSE,
      opType          |-> "NONE",
      stage           |-> "CREATED",
      requester       |-> NoneNode,
      target          |-> {},
      acked           |-> {},
      baseEpoch       |-> 0,
      reservedEpoch   |-> 0,
      reqId           |-> 0,
      reqType         |-> "RS",
      writeIntent     |-> FALSE,
      intendedState   |-> "G_I",
      intendedOwner   |-> NoneNode,
      intendedSharers |-> {},
      accepted        |-> FALSE,
      replayArmed     |-> FALSE,
      recallDone      |-> FALSE,
      invalidateDone  |-> FALSE,
      createTick      |-> 0 ]

EmptyTombstone ==
    [ valid    |-> FALSE,
      epoch    |-> 0,
      reqId    |-> 0,
      accepted |-> FALSE,
      age      |-> 0 ]

CanAllocate == dir.epoch < MaxEpoch

ReserveEpoch(e) == e + 1

Canonical(d) ==
    /\ d.state \in MESIState
    /\ d.sharers \subseteq Nodes
    /\ d.owner \in (Nodes \cup {NoneNode})
    /\ d.epoch \in 0..MaxEpoch
    /\ IF d.state = "G_I"
          THEN /\ d.sharers = {}
               /\ d.owner = NoneNode
         ELSE IF d.state = "G_S"
          THEN /\ d.sharers # {}
               /\ d.owner = NoneNode
         ELSE /\ Cardinality(d.sharers) = 1
              /\ d.owner \in d.sharers

CommitTuple(o) == [epoch |-> o.reservedEpoch, reqId |-> o.reqId]

CommitDir(o) ==
    [ state   |-> o.intendedState,
      sharers |-> IF o.intendedState \in {"G_E", "G_M"}
                    THEN {o.intendedOwner}
                    ELSE o.intendedSharers,
      owner   |-> IF o.intendedState \in {"G_E", "G_M"}
                    THEN o.intendedOwner
                    ELSE NoneNode,
      dirty   |-> (o.intendedState = "G_M"),
      epoch   |-> o.reservedEpoch ]

AgeTombstone(ts) ==
    IF ~ts.valid THEN ts
    ELSE IF ts.age < TombstoneWindow THEN [ts EXCEPT !.age = @ + 1]
    ELSE EmptyTombstone

Init ==
    /\ dir = [state |-> "G_I", sharers |-> {}, owner |-> NoneNode, dirty |-> FALSE, epoch |-> 0]
    /\ ost = EmptyOutstanding
    /\ tombstone = EmptyTombstone
    /\ commitLog = <<>>
    /\ epochLog = <<0>>
    /\ tick = 0

GrantShared(req, reqId) ==
    /\ tick < MaxTick
    /\ req \in Nodes
    /\ reqId \in ReqIds
    /\ ~ost.valid
    /\ CanAllocate
    /\ dir.state \in {"G_I", "G_S"} \cup IF dir.owner = req THEN {"G_E", "G_M"} ELSE {}
    /\ dir' = dir
    /\ ost' =
        [ valid           |-> TRUE,
          opType          |-> "GRANT_HANDSHAKE",
          stage           |-> "WAITING_CLEAR",
          requester       |-> req,
          target          |-> {},
          acked           |-> {},
          baseEpoch       |-> dir.epoch,
          reservedEpoch   |-> ReserveEpoch(dir.epoch),
          reqId           |-> reqId,
          reqType         |-> "RS",
          writeIntent     |-> FALSE,
          intendedState   |-> "G_S",
          intendedOwner   |-> NoneNode,
          intendedSharers |-> IF dir.state \in {"G_E", "G_M"}
                                THEN {req}
                                ELSE dir.sharers \cup {req},
          accepted        |-> FALSE,
          replayArmed     |-> FALSE,
          recallDone      |-> FALSE,
          invalidateDone  |-> FALSE,
          createTick      |-> tick + 1 ]
    /\ tombstone' = AgeTombstone(tombstone)
    /\ UNCHANGED <<commitLog, epochLog>>
    /\ tick' = tick + 1

GrantExclusive(req, writeIntent, reqId) ==
    /\ tick < MaxTick
    /\ req \in Nodes
    /\ writeIntent \in BOOLEAN
    /\ reqId \in ReqIds
    /\ ~ost.valid
    /\ CanAllocate
    /\ dir.state = "G_I" \/ (dir.state \in {"G_E", "G_M"} /\ dir.owner = req)
    /\ dir' = dir
    /\ ost' =
        [ valid           |-> TRUE,
          opType          |-> "GRANT_HANDSHAKE",
          stage           |-> "WAITING_CLEAR",
          requester       |-> req,
          target          |-> {},
          acked           |-> {},
          baseEpoch       |-> dir.epoch,
          reservedEpoch   |-> ReserveEpoch(dir.epoch),
          reqId           |-> reqId,
          reqType         |-> "RU",
          writeIntent     |-> writeIntent,
          intendedState   |-> IF writeIntent THEN "G_M" ELSE "G_E",
          intendedOwner   |-> req,
          intendedSharers |-> {req},
          accepted        |-> FALSE,
          replayArmed     |-> FALSE,
          recallDone      |-> FALSE,
          invalidateDone  |-> FALSE,
          createTick      |-> tick + 1 ]
    /\ tombstone' = AgeTombstone(tombstone)
    /\ UNCHANGED <<commitLog, epochLog>>
    /\ tick' = tick + 1

RecallBarrier(req, reqType, writeIntent, reqId) ==
    /\ tick < MaxTick
    /\ req \in Nodes
    /\ reqType \in ReqType
    /\ writeIntent \in BOOLEAN
    /\ reqId \in ReqIds
    /\ ~ost.valid
    /\ CanAllocate
    /\ dir.state \in {"G_E", "G_M"}
    /\ dir.owner \in Nodes
    /\ req # dir.owner
    /\ dir' = dir
    /\ ost' =
        [ valid           |-> TRUE,
          opType          |-> "RECALL",
          stage           |-> "WAITING_TARGET_RESP",
          requester       |-> req,
          target          |-> {dir.owner},
          acked           |-> {},
          baseEpoch       |-> dir.epoch,
          reservedEpoch   |-> ReserveEpoch(dir.epoch),
          reqId           |-> reqId,
          reqType         |-> reqType,
          writeIntent     |-> writeIntent,
          intendedState   |-> IF reqType = "RS"
                                THEN "G_S"
                                ELSE IF writeIntent THEN "G_M" ELSE "G_E",
          intendedOwner   |-> IF reqType = "RS" THEN NoneNode ELSE req,
          intendedSharers |-> IF reqType = "RS" THEN {dir.owner, req} ELSE {req},
          accepted        |-> FALSE,
          replayArmed     |-> FALSE,
          recallDone      |-> FALSE,
          invalidateDone  |-> FALSE,
          createTick      |-> tick + 1 ]
    /\ tombstone' = AgeTombstone(tombstone)
    /\ UNCHANGED <<commitLog, epochLog>>
    /\ tick' = tick + 1

RecallResponse ==
    /\ tick < MaxTick
    /\ ost.valid
    /\ ost.opType = "RECALL"
    /\ ost.stage = "WAITING_TARGET_RESP"
    /\ dir' = dir
    /\ ost' = [ost EXCEPT !.stage = "DONE", !.recallDone = TRUE]
    /\ tombstone' = AgeTombstone(tombstone)
    /\ UNCHANGED <<commitLog, epochLog>>
    /\ tick' = tick + 1

(* Horizon-exempt forward step (see ClearCommit note): consuming a           *)
(* RECALL.DONE into a GRANT_HANDSHAKE must be able to happen at MaxTick.      *)
RecallToGrant ==
    /\ ost.valid
    /\ ost.opType = "RECALL"
    /\ ost.stage = "DONE"
    /\ dir' = dir
    /\ ost' = [ost EXCEPT !.opType = "GRANT_HANDSHAKE",
                          !.stage = "WAITING_CLEAR",
                          !.replayArmed = FALSE]
    /\ tombstone' = AgeTombstone(tombstone)
    /\ UNCHANGED <<commitLog, epochLog>>
    /\ tick' = IF tick < MaxTick THEN tick + 1 ELSE tick

(* Frozen fix: expired RECALL orphan is discarded (timer/lazy cleanup).        *)
(*   - Only fires once the orphan has aged past RecallTimeout (Rule 4: timeout  *)
(*     is based on createTick, not the DONE moment; DONE does not extend life). *)
(*   - Rule 3: committed DirEntry is NOT touched — dir stays at its current     *)
(*     stable G_E/G_M (or whatever) state.                                      *)
(*   - Rule 6: epoch not advanced, no tombstone installed, no compensating      *)
(*     commit; reservedEpoch/dataBuf simply vanish with the outstanding.        *)
(*   - Freeing ost (ost' = EmptyOutstanding) is exactly what unblocks the PA    *)
(*     slot: a new outstanding can now be created (Rule 2 replay effect —       *)
(*     modeled here as the slot simply becoming allocatable again).             *)
(* Note: unlike the other actions this is NOT guarded by tick < MaxTick.      *)
(* The cleanup safety net must always be able to drain an aged orphan, even   *)
(* at the clock horizon; otherwise a RECALL that ages into DONE exactly at    *)
(* MaxTick would be frozen by the Stutter step and produce a SPURIOUS         *)
(* liveness counterexample (bounded-model artifact, not a real wedge). It     *)
(* does not advance tick past MaxTick (clamped), so it never enlarges the     *)
(* horizon for new work — it only frees the slot.                            *)
RecallOrphanCleanup ==
    /\ ost.valid
    /\ ost.opType = "RECALL"
    /\ ost.stage \in {"WAITING_TARGET_RESP", "DONE"}
    (* Eligible once aged past the timeout, OR unconditionally at the clock    *)
    (* horizon: a RECALL still outstanding at MaxTick has, by construction,    *)
    (* exhausted all available time and is definitionally an expired orphan.   *)
    (* The second disjunct removes the bounded-model artifact where a RECALL   *)
    (* created just before MaxTick could never numerically reach               *)
    (* createTick + RecallTimeout inside the horizon.                          *)
    /\ (tick > ost.createTick + RecallTimeout \/ tick = MaxTick)
    /\ dir' = dir
    /\ ost' = EmptyOutstanding
    /\ tombstone' = AgeTombstone(tombstone)
    /\ UNCHANGED <<commitLog, epochLog>>
    /\ tick' = IF tick < MaxTick THEN tick + 1 ELSE tick

(* Backward-compatible alias: ubcc_transport_faults.tla references the old      *)
(* name. The fix retimes it (timeout-gated) but the shape is identical.         *)
RecallOrphanDisappears == RecallOrphanCleanup

InvalidationBarrier(req, writeIntent, reqId) ==
    /\ tick < MaxTick
    /\ req \in Nodes
    /\ writeIntent \in BOOLEAN
    /\ reqId \in ReqIds
    /\ ~ost.valid
    /\ CanAllocate
    /\ dir.state = "G_S"
    /\ dir.sharers # {}
    /\ req \notin dir.sharers
    /\ dir' = dir
    /\ ost' =
        [ valid           |-> TRUE,
          opType          |-> "INVALIDATE",
          stage           |-> "WAITING_ALL_ACKS",
          requester       |-> req,
          target          |-> dir.sharers,
          acked           |-> {},
          baseEpoch       |-> dir.epoch,
          reservedEpoch   |-> ReserveEpoch(dir.epoch),
          reqId           |-> reqId,
          reqType         |-> "RU",
          writeIntent     |-> writeIntent,
          intendedState   |-> IF writeIntent THEN "G_M" ELSE "G_E",
          intendedOwner   |-> req,
          intendedSharers |-> {req},
          accepted        |-> FALSE,
          replayArmed     |-> FALSE,
          recallDone      |-> FALSE,
          invalidateDone  |-> FALSE,
          createTick      |-> tick + 1 ]
    /\ tombstone' = AgeTombstone(tombstone)
    /\ UNCHANGED <<commitLog, epochLog>>
    /\ tick' = tick + 1

UpgradeBarrier(req, writeIntent, reqId) ==
    /\ tick < MaxTick
    /\ req \in Nodes
    /\ writeIntent \in BOOLEAN
    /\ reqId \in ReqIds
    /\ ~ost.valid
    /\ CanAllocate
    /\ dir.state = "G_S"
    /\ req \in dir.sharers
    /\ LET targets == dir.sharers \ {req} IN
       /\ dir' = dir
       /\ ost' =
           [ valid           |-> TRUE,
             opType          |-> "UPGRADE_PENDING",
             stage           |-> IF targets = {} THEN "WAITING_LOCAL_DONE" ELSE "WAITING_ALL_ACKS",
             requester       |-> req,
             target          |-> targets,
             acked           |-> {},
             baseEpoch       |-> dir.epoch,
             reservedEpoch   |-> ReserveEpoch(dir.epoch),
             reqId           |-> reqId,
             reqType         |-> "RU",
             writeIntent     |-> writeIntent,
             intendedState   |-> IF writeIntent THEN "G_M" ELSE "G_E",
             intendedOwner   |-> req,
             intendedSharers |-> {req},
             accepted        |-> (targets = {}),
             replayArmed     |-> FALSE,
             recallDone      |-> FALSE,
             invalidateDone  |-> (targets = {}),
             createTick      |-> tick + 1 ]
    /\ tombstone' = AgeTombstone(tombstone)
    /\ UNCHANGED <<commitLog, epochLog>>
    /\ tick' = tick + 1

(* Horizon-exempt forward step (see ClearCommit note): collecting the last   *)
(* invalidation/upgrade ack must be able to happen at MaxTick.               *)
BarrierAck(node) ==
    /\ ost.valid
    /\ ost.stage = "WAITING_ALL_ACKS"
    /\ ost.opType \in {"INVALIDATE", "UPGRADE_PENDING"}
    /\ node \in (ost.target \ ost.acked)
    /\ LET newAcked == ost.acked \cup {node} IN
       IF ost.opType = "INVALIDATE"
       THEN LET newSharers == dir.sharers \ {node} IN
            /\ dir' = [dir EXCEPT !.sharers = newSharers,
                                 !.state = IF newSharers = {} THEN "G_I" ELSE "G_S",
                                 !.owner = NoneNode,
                                 !.dirty = FALSE]
            /\ ost' = IF newAcked = ost.target
                        THEN [ost EXCEPT !.opType = "GRANT_HANDSHAKE",
                                         !.stage = "WAITING_CLEAR",
                                         !.acked = newAcked,
                                         !.replayArmed = TRUE,
                                         !.invalidateDone = TRUE]
                        ELSE [ost EXCEPT !.acked = newAcked]
       ELSE /\ dir' = dir
            /\ ost' = IF newAcked = ost.target
                        THEN [ost EXCEPT !.acked = newAcked,
                                         !.stage = "WAITING_LOCAL_DONE",
                                         !.accepted = TRUE,
                                         !.invalidateDone = TRUE]
                        ELSE [ost EXCEPT !.acked = newAcked]
    /\ tombstone' = AgeTombstone(tombstone)
    /\ UNCHANGED <<commitLog, epochLog>>
    /\ tick' = IF tick < MaxTick THEN tick + 1 ELSE tick

(* ClearCommit / UpgradeCommit are forward-progress COMPLETIONS: an           *)
(* already-accepted grant/upgrade finalizing its commit. Like                 *)
(* RecallOrphanCleanup they are horizon-exempt (no tick < MaxTick guard, tick *)
(* clamped at MaxTick) so the last in-flight request can always drain to      *)
(* ~ost.valid — otherwise OstEventuallyClears sees a spurious wedge caused    *)
(* purely by the clock bound. They still enter fresh work into commitLog only *)
(* under the NoDoubleCommit guard, so correctness is unchanged.               *)
ClearCommit ==
    /\ ost.valid
    /\ ost.opType = "GRANT_HANDSHAKE"
    /\ ost.stage = "WAITING_CLEAR"
    /\ \A i \in 1..Len(commitLog) : commitLog[i] # CommitTuple(ost)
    /\ dir' = CommitDir(ost)
    /\ ost' = EmptyOutstanding
    /\ tombstone' = [valid |-> TRUE, epoch |-> ost.baseEpoch, reqId |-> ost.reqId,
                      accepted |-> TRUE, age |-> 0]
    /\ commitLog' = Append(commitLog, CommitTuple(ost))
    /\ epochLog' = Append(epochLog, ost.reservedEpoch)
    /\ tick' = IF tick < MaxTick THEN tick + 1 ELSE tick

UpgradeCommit ==
    /\ ost.valid
    /\ ost.opType = "UPGRADE_PENDING"
    /\ ost.stage = "WAITING_LOCAL_DONE"
    /\ ost.accepted
    /\ \A i \in 1..Len(commitLog) : commitLog[i] # CommitTuple(ost)
    /\ dir' = CommitDir(ost)
    /\ ost' = EmptyOutstanding
    /\ tombstone' = AgeTombstone(tombstone)
    /\ commitLog' = Append(commitLog, CommitTuple(ost))
    /\ epochLog' = Append(epochLog, ost.reservedEpoch)
    /\ tick' = IF tick < MaxTick THEN tick + 1 ELSE tick

Writeback(node, keepAsClean) ==
    /\ tick < MaxTick
    /\ node \in Nodes
    /\ keepAsClean \in BOOLEAN
    /\ ~ost.valid
    /\ dir.state \in {"G_E", "G_M"}
    /\ dir.owner = node
    /\ dir' = IF keepAsClean
                 THEN [state |-> "G_E", sharers |-> {node}, owner |-> node,
                       dirty |-> FALSE, epoch |-> dir.epoch]
                 ELSE [state |-> "G_I", sharers |-> {}, owner |-> NoneNode,
                       dirty |-> FALSE, epoch |-> dir.epoch]
    /\ ost' = ost
    /\ tombstone' = AgeTombstone(tombstone)
    /\ UNCHANGED <<commitLog, epochLog>>
    /\ tick' = tick + 1

Evict(node) ==
    /\ tick < MaxTick
    /\ node \in Nodes
    /\ ~ost.valid
    /\ node \in dir.sharers
    /\ ~(dir.state = "G_M" /\ dir.owner = node)
    /\ dir' =
        IF dir.state = "G_S"
        THEN LET newSharers == dir.sharers \ {node} IN
             [ state   |-> IF newSharers = {} THEN "G_I" ELSE "G_S",
               sharers |-> newSharers,
               owner   |-> NoneNode,
               dirty   |-> FALSE,
               epoch   |-> dir.epoch ]
        ELSE [state |-> "G_I", sharers |-> {}, owner |-> NoneNode,
              dirty |-> FALSE, epoch |-> dir.epoch]
    /\ ost' = ost
    /\ tombstone' = AgeTombstone(tombstone)
    /\ UNCHANGED <<commitLog, epochLog>>
    /\ tick' = tick + 1

TickOnly ==
    /\ tick < MaxTick
    /\ dir' = dir
    /\ ost' = ost
    /\ tombstone' = AgeTombstone(tombstone)
    /\ UNCHANGED <<commitLog, epochLog>>
    /\ tick' = tick + 1

Stutter ==
    /\ tick = MaxTick
    /\ UNCHANGED Vars

Next ==
    \/ \E req \in Nodes : \E reqId \in ReqIds : GrantShared(req, reqId)
    \/ \E req \in Nodes : \E wi \in BOOLEAN : \E reqId \in ReqIds : GrantExclusive(req, wi, reqId)
    \/ \E req \in Nodes : \E rt \in ReqType : \E wi \in BOOLEAN : \E reqId \in ReqIds : RecallBarrier(req, rt, wi, reqId)
    \/ RecallResponse
    \/ RecallToGrant
    \/ RecallOrphanCleanup
    \/ \E req \in Nodes : \E wi \in BOOLEAN : \E reqId \in ReqIds : InvalidationBarrier(req, wi, reqId)
    \/ \E req \in Nodes : \E wi \in BOOLEAN : \E reqId \in ReqIds : UpgradeBarrier(req, wi, reqId)
    \/ \E node \in Nodes : BarrierAck(node)
    \/ ClearCommit
    \/ UpgradeCommit
    \/ \E node \in Nodes : \E keepAsClean \in BOOLEAN : Writeback(node, keepAsClean)
    \/ \E node \in Nodes : Evict(node)
    \/ TickOnly
    \/ Stutter

Spec == Init /\ [][Next]_Vars

(***************************************************************************)
(* Liveness spec (FairSpec) and progress properties.                       *)
(*                                                                          *)
(* Fairness design rationale:                                               *)
(*  - We give weak fairness to every FORWARD-progress action that, once     *)
(*    enabled, must eventually fire for the protocol to make progress:      *)
(*    RecallToGrant, BarrierAck, ClearCommit, UpgradeCommit, and the        *)
(*    orphan safety net RecallOrphanCleanup.                                *)
(*  - We DELIBERATELY do NOT give fairness to RecallResponse. A RECALL      *)
(*    stuck in WAITING_TARGET_RESP models the real-world orphan trigger:    *)
(*    the target's RecallResp is LOST / the requester never drives it, so   *)
(*    the response may never arrive. In that world the ONLY thing that can  *)
(*    free the blocked PA slot is the timeout-gated RecallOrphanCleanup.    *)
(*    Hence: with cleanup -> progress guaranteed (property holds);          *)
(*          without cleanup -> orphan wedges the slot forever (property     *)
(*          fails with a lasso counterexample). This is the crisp contrast  *)
(*    that shows the fix's value.                                           *)
(*  - We also do NOT give fairness to the request-INITIATING actions        *)
(*    (GrantShared/GrantExclusive/RecallBarrier/...). They are environment  *)
(*    inputs; the protocol is not obliged to keep issuing new requests.     *)
(*    Their WF is unnecessary for the progress properties and would only    *)
(*    blow up the fairness burden.                                          *)
(***************************************************************************)
FairSpec ==
    /\ Init
    /\ [][Next]_Vars
    /\ WF_Vars(RecallToGrant)
    /\ WF_Vars(RecallOrphanCleanup)
    /\ WF_Vars(ClearCommit)
    /\ WF_Vars(UpgradeCommit)
    /\ \A n \in Nodes : WF_Vars(BarrierAck(n))
    (* Clock must keep advancing (up to MaxTick), otherwise a behavior could   *)
    (* stutter forever just below the RecallTimeout threshold and starve the   *)
    (* time-gated RecallOrphanCleanup — a modeling artifact, not a real bug.   *)
    (* WF on TickOnly forces time to progress so cleanup becomes enabled.      *)
    /\ WF_Vars(TickOnly)

(* --- Liveness / progress properties (used as PROPERTY in the cfg) ------- *)

(* P1 (headline): a RECALL outstanding never wedges forever. Whenever a     *)
(* RECALL is in flight, the slot eventually stops being a live RECALL —     *)
(* either it is consumed (converted to GRANT_HANDSHAKE) or the orphan is    *)
(* cleaned up. WITHOUT RecallOrphanCleanup this FAILS for the lost-response *)
(* orphan (RECALL stuck in WAITING_TARGET_RESP with no fair RecallResponse).*)
RecallProgress ==
    [](  (ost.valid /\ ost.opType = "RECALL")
       ~> (~(ost.valid /\ ost.opType = "RECALL")) )

(* P2: every outstanding request eventually clears (commit or cleanup).     *)
(* This is the general no-permanent-block property. Because request-issuing *)
(* actions are unfair, once no more new requests are issued the in-flight   *)
(* one must drain to ~ost.valid.                                            *)
OstEventuallyClears ==
    [](ost.valid ~> ~ost.valid)

(* P3 (C1): an INVALIDATE barrier never stalls collecting acks. Once an     *)
(* INVALIDATE is in WAITING_ALL_ACKS, it eventually leaves that stage — all *)
(* sharer acks are collected (BarrierAck is fair) and it converts to a      *)
(* GRANT_HANDSHAKE. Guards against a lost/never-arriving invalidation ack    *)
(* wedging the barrier.                                                     *)
InvalidateProgress ==
    [](  (ost.valid /\ ost.opType = "INVALIDATE" /\ ost.stage = "WAITING_ALL_ACKS")
       ~> (~(ost.valid /\ ost.opType = "INVALIDATE" /\ ost.stage = "WAITING_ALL_ACKS")) )

(* P4 (C1): an UPGRADE barrier never stalls. Once UPGRADE_PENDING is        *)
(* WAITING_ALL_ACKS it eventually progresses (collects acks -> WAITING_     *)
(* LOCAL_DONE -> commit), or otherwise leaves that stage.                   *)
UpgradeProgress ==
    [](  (ost.valid /\ ost.opType = "UPGRADE_PENDING" /\ ost.stage = "WAITING_ALL_ACKS")
       ~> (~(ost.valid /\ ost.opType = "UPGRADE_PENDING" /\ ost.stage = "WAITING_ALL_ACKS")) )


SharersCanonical == Canonical(dir)

EpochMonotonic ==
    /\ Len(epochLog) >= 1
    /\ epochLog[1] = 0
    /\ epochLog[Len(epochLog)] = dir.epoch
    /\ IF Len(epochLog) = 1
          THEN TRUE
          ELSE \A i \in 1..(Len(epochLog) - 1) : epochLog[i] < epochLog[i + 1]

NoDoubleCommit ==
    \A i, j \in 1..Len(commitLog) : i # j => commitLog[i] # commitLog[j]

ReserveNotCommit ==
    ~ost.valid \/
    /\ ost.reservedEpoch = dir.epoch + 1
    /\ ost.baseEpoch = dir.epoch
    /\ ost.reservedEpoch <= MaxEpoch

=============================================================================
