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

RecallToGrant ==
    /\ tick < MaxTick
    /\ ost.valid
    /\ ost.opType = "RECALL"
    /\ ost.stage = "DONE"
    /\ dir' = dir
    /\ ost' = [ost EXCEPT !.opType = "GRANT_HANDSHAKE",
                          !.stage = "WAITING_CLEAR",
                          !.replayArmed = FALSE]
    /\ tombstone' = AgeTombstone(tombstone)
    /\ UNCHANGED <<commitLog, epochLog>>
    /\ tick' = tick + 1

RecallOrphanDisappears ==
    /\ tick < MaxTick
    /\ ost.valid
    /\ ost.opType = "RECALL"
    /\ ost.stage \in {"WAITING_TARGET_RESP", "DONE"}
    /\ tick > ost.createTick
    /\ dir' = dir
    /\ ost' = EmptyOutstanding
    /\ tombstone' = AgeTombstone(tombstone)
    /\ UNCHANGED <<commitLog, epochLog>>
    /\ tick' = tick + 1

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

BarrierAck(node) ==
    /\ tick < MaxTick
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
    /\ tick' = tick + 1

ClearCommit ==
    /\ tick < MaxTick
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
    /\ tick' = tick + 1

UpgradeCommit ==
    /\ tick < MaxTick
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
    /\ tick' = tick + 1

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
    \/ RecallOrphanDisappears
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
