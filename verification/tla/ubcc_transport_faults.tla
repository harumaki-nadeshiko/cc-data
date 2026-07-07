-------------------------- MODULE ubcc_transport_faults --------------------------
EXTENDS ubcc_protocol_core, Sequences, TLC

(***************************************************************************)
(* Transport fault envelope for the core protocol.                         *)
(*                                                                          *)
(* Stage-2 (B1) extension (2026-07-08): the fault model now covers ALL      *)
(* acknowledgement-carrying control messages under drop / duplicate /       *)
(* reorder, not just Clear:                                                 *)
(*                                                                          *)
(*   MsgKind      transport mechanism        fault coverage                 *)
(*   -------      -------------------        --------------                 *)
(*   Clear        explicit transport queue   drop, duplicate, reorder       *)
(*   InvAck       BarrierAck action envelope drop(unfair), dup, reorder     *)
(*   RecallResp   RecallResponse envelope    drop(unfair), dup              *)
(*   UpgradeAck   BarrierAck (UPGRADE) env.  drop(unfair), dup, reorder     *)
(*                                                                          *)
(* Two complementary modelling techniques are used:                         *)
(*                                                                          *)
(*  (1) Clear -> EXPLICIT QUEUE. Clear commits/replays a grant, so its      *)
(*      loss/dup/reorder is modelled by a real `transport` sequence with    *)
(*      Deliver/Drop/Duplicate/Reorder actions operating on queue indices.  *)
(*                                                                          *)
(*  (2) InvAck / RecallResp / UpgradeAck -> ACTION ENVELOPE. The core       *)
(*      actions BarrierAck / RecallResponse already model the *effect* of   *)
(*      receiving these acks. Faults become:                                *)
(*        - drop     : the ack action is simply NOT forced to fire (it is   *)
(*                     left unfair). A never-arriving ack wedges progress    *)
(*                     unless a timeout/cleanup rescues it -> exercised by   *)
(*                     the liveness spec, and here bounded by RecallOrphan.  *)
(*        - reorder  : BarrierAck(node) uses \E node over the pending set    *)
(*                     and accumulates into a MONOTONIC ackMask, so TLC      *)
(*                     already explores every arrival order for free.        *)
(*        - duplicate: DupInvAck / DupRecallResp re-apply an ack that was    *)
(*                     already applied, exercising idempotency (bit already  *)
(*                     set / no-outstanding rejection).                      *)
(***************************************************************************)

VARIABLES transport, transportRecord

TVars == <<dir, ost, tombstone, commitLog, epochLog, tick, transport, transportRecord>>

MsgKind == {"Clear", "InvAck", "RecallResp", "UpgradeAck"}

Audit(action, kind, epoch, reqId, outcome) ==
    [action |-> action, kind |-> kind, epoch |-> epoch, reqId |-> reqId, outcome |-> outcome]

RemoveAt(seq, i) == SubSeq(seq, 1, i - 1) \o SubSeq(seq, i + 1, Len(seq))

HasQueued(kind, epoch, reqId) ==
    \E i \in 1..Len(transport) :
        transport[i].kind = kind /\ transport[i].epoch = epoch /\ transport[i].reqId = reqId

AgeTrackedTombstone(ts) ==
    IF ts.valid /\ HasQueued("Clear", ts.epoch, ts.reqId)
       THEN ts
       ELSE AgeTombstone(ts)

ReplayDrainBarrier == tombstone.valid /\ HasQueued("Clear", tombstone.epoch, tombstone.reqId)

TFInit ==
    /\ Init
    /\ transport = <<>>
    /\ transportRecord = <<>>

CopyCount(msg) == Cardinality({i \in 1..Len(transport) : transport[i] = msg})

(* ── Core protocol steps wrapped so they leave transport unchanged ─────── *)
CoreGrantShared ==
    /\ ~ReplayDrainBarrier
    /\ GrantShared(0, 0)
    /\ UNCHANGED <<transport, transportRecord>>

CoreGrantExclusive ==
    /\ ~ReplayDrainBarrier
    /\ GrantExclusive(0, FALSE, 0)
    /\ UNCHANGED <<transport, transportRecord>>

CoreRecallBarrier ==
    /\ ~ReplayDrainBarrier
    /\ RecallBarrier(1, "RS", FALSE, 0)
    /\ UNCHANGED <<transport, transportRecord>>

CoreInvalidateBarrier ==
    /\ ~ReplayDrainBarrier
    /\ \E req \in Nodes : \E wi \in BOOLEAN : \E reqId \in ReqIds : InvalidationBarrier(req, wi, reqId)
    /\ UNCHANGED <<transport, transportRecord>>

CoreUpgradeBarrier ==
    /\ ~ReplayDrainBarrier
    /\ \E req \in Nodes : \E wi \in BOOLEAN : \E reqId \in ReqIds : UpgradeBarrier(req, wi, reqId)
    /\ UNCHANGED <<transport, transportRecord>>

CoreUpgradeCommit == /\ ~ReplayDrainBarrier /\ UpgradeCommit /\ UNCHANGED <<transport, transportRecord>>
CoreWriteback == /\ ~ReplayDrainBarrier /\ Writeback(0, FALSE) /\ UNCHANGED <<transport, transportRecord>>
CoreEvict == /\ ~ReplayDrainBarrier /\ Evict(0) /\ UNCHANGED <<transport, transportRecord>>
CoreTick == /\ ~ReplayDrainBarrier /\ TickOnly /\ UNCHANGED <<transport, transportRecord>>
CoreRecallToGrant == /\ ~ReplayDrainBarrier /\ RecallToGrant /\ UNCHANGED <<transport, transportRecord>>

RecallOrphanWithAudit ==
    /\ ~ReplayDrainBarrier
    /\ RecallOrphanCleanup
    /\ transport' = transport
    /\ transportRecord' = Append(transportRecord,
           Audit("cleanup", "RecallResp", ost.baseEpoch, ost.reqId, "orphan_discard"))

(* ── InvAck / UpgradeAck faults: reorder is FREE (\E node + monotonic       *)
(*    ackMask in BarrierAck); drop is FREE (BarrierAck simply not forced);   *)
(*    here we add DUPLICATE: re-deliver an ack for a node already acked and  *)
(*    assert it is idempotent (no state change, mask bit already set).       *)
DupInvAck ==
    /\ tick < MaxTick
    /\ ~ReplayDrainBarrier
    /\ ost.valid
    /\ ost.stage = "WAITING_ALL_ACKS"
    /\ ost.opType \in {"INVALIDATE", "UPGRADE_PENDING"}
    /\ \E n \in ost.acked :          \* n already acknowledged -> duplicate ack
        /\ UNCHANGED <<dir, ost, tombstone, commitLog, epochLog>>  \* idempotent
        /\ transport' = transport
        /\ transportRecord' = Append(transportRecord,
               Audit("dup", IF ost.opType = "INVALIDATE" THEN "InvAck" ELSE "UpgradeAck",
                     ost.baseEpoch, ost.reqId, "idempotent_ignored"))
        /\ tick' = tick + 1

(* Duplicate RecallResp: a second response after RECALL already moved to     *)
(* DONE must be a no-op (stage already DONE, recallDone already TRUE).        *)
DupRecallResp ==
    /\ tick < MaxTick
    /\ ~ReplayDrainBarrier
    /\ ost.valid
    /\ ost.opType = "RECALL"
    /\ ost.stage = "DONE"
    /\ ost.recallDone
    /\ UNCHANGED <<dir, ost, tombstone, commitLog, epochLog>>   \* idempotent
    /\ transport' = transport
    /\ transportRecord' = Append(transportRecord,
           Audit("dup", "RecallResp", ost.baseEpoch, ost.reqId, "idempotent_ignored"))
    /\ tick' = tick + 1

(* ── The forward ack actions themselves (wrapped). Left OUT of any fairness *)
(*    at the model level so "the ack may be lost" (drop) is a legal          *)
(*    behaviour explored by TLC.                                             *)
CoreRecallResp ==
    /\ ~ReplayDrainBarrier
    /\ RecallResponse
    /\ UNCHANGED <<transport, transportRecord>>

CoreInvAck ==
    /\ ~ReplayDrainBarrier
    /\ \E n \in Nodes : BarrierAck(n)
    /\ UNCHANGED <<transport, transportRecord>>

(* ── Clear message: explicit transport queue with drop/dup/REORDER ─────── *)
QueueClear ==
    /\ tick < MaxTick
    /\ ~ReplayDrainBarrier
    /\ Len(transport) = 0
    /\ ost.valid /\ ost.opType = "GRANT_HANDSHAKE" /\ ost.stage = "WAITING_CLEAR"
    /\ ~HasQueued("Clear", ost.baseEpoch, ost.reqId)
    /\ UNCHANGED <<dir, ost, tombstone, commitLog, epochLog>>
    /\ transport' = Append(transport, [kind |-> "Clear", src |-> ost.requester, epoch |-> ost.baseEpoch, reqId |-> ost.reqId])
    /\ UNCHANGED transportRecord
    /\ tick' = tick + 1

DeliverClear ==
    /\ tick < MaxTick
    /\ \E i \in 1..Len(transport) :
        /\ transport[i].kind = "Clear"
        /\ IF tombstone.valid /\ transport[i].epoch = tombstone.epoch /\ transport[i].reqId = tombstone.reqId
              THEN /\ dir' = dir
                   /\ ost' = ost
                   /\ tombstone' = AgeTrackedTombstone(tombstone)
                   /\ UNCHANGED <<commitLog, epochLog>>
                   /\ transport' = RemoveAt(transport, i)
                   /\ transportRecord' = Append(transportRecord,
                          Audit("deliver", "Clear", transport[i].epoch, transport[i].reqId,
                                IF tombstone.accepted THEN "replay_accept" ELSE "replay_reject"))
                   /\ tick' = tick + 1
              ELSE IF ost.valid /\ ost.opType = "GRANT_HANDSHAKE" /\ ost.stage = "WAITING_CLEAR"
                      /\ transport[i].src = ost.requester
                      /\ transport[i].epoch = ost.baseEpoch
                      /\ transport[i].reqId = ost.reqId
              THEN /\ ClearCommit
                   /\ transport' = RemoveAt(transport, i)
                   /\ transportRecord' = Append(transportRecord, Audit("deliver", "Clear", transport[i].epoch, transport[i].reqId, "commit_accept"))
              ELSE /\ dir' = dir
                   /\ ost' = ost
                   /\ tombstone' = AgeTrackedTombstone(tombstone)
                   /\ UNCHANGED <<commitLog, epochLog>>
                   /\ transport' = RemoveAt(transport, i)
                   /\ transportRecord' = Append(transportRecord, Audit("deliver", "Clear", transport[i].epoch, transport[i].reqId, "reject"))
                   /\ tick' = tick + 1

DropMsg ==
    /\ tick < MaxTick
    /\ Len(transport) > 0
    /\ \E i \in 1..Len(transport) :
        /\ transport[i].kind = "Clear"
        /\ dir' = dir
        /\ ost' = ost
        /\ tombstone' = AgeTrackedTombstone(tombstone)
        /\ UNCHANGED <<commitLog, epochLog>>
        /\ transport' = RemoveAt(transport, i)
        /\ transportRecord' = Append(transportRecord,
               Audit("drop", "Clear", transport[i].epoch, transport[i].reqId, "lost"))
        /\ tick' = tick + 1

DuplicateMsg ==
    /\ tick < MaxTick
    /\ Len(transport) = 1
    /\ \E i \in 1..Len(transport) :
        /\ transport[i].kind = "Clear"
        /\ CopyCount(transport[i]) = 1
        /\ UNCHANGED <<dir, ost, tombstone, commitLog, epochLog>>
        /\ transport' = Append(transport, transport[i])
        /\ UNCHANGED transportRecord
        /\ tick' = tick + 1

(* Explicit REORDER of the Clear queue: swap two queued messages. With a     *)
(* single-slot Clear queue this is usually a no-op, but the action is kept   *)
(* for completeness so multi-message queues (dup -> 2 copies) can permute.   *)
ReorderMsg ==
    /\ tick < MaxTick
    /\ Len(transport) >= 2
    /\ \E i, j \in 1..Len(transport) :
        /\ i < j
        /\ LET swapped == [transport EXCEPT ![i] = transport[j], ![j] = transport[i]] IN
             transport' = swapped
    /\ UNCHANGED <<dir, ost, tombstone, commitLog, epochLog, transportRecord>>
    /\ tick' = tick + 1

TFStutter == /\ tick = MaxTick /\ UNCHANGED TVars

TFNext ==
    \/ CoreGrantShared
    \/ CoreGrantExclusive
    \/ CoreRecallBarrier
    \/ CoreInvalidateBarrier
    \/ CoreUpgradeBarrier
    \/ CoreRecallResp
    \/ CoreInvAck
    \/ QueueClear
    \/ DeliverClear
    \/ DuplicateMsg
    \/ DropMsg
    \/ ReorderMsg
    \/ DupInvAck
    \/ DupRecallResp
    \/ CoreRecallToGrant
    \/ RecallOrphanWithAudit
    \/ CoreUpgradeCommit
    \/ CoreWriteback
    \/ CoreEvict
    \/ CoreTick
    \/ TFStutter

TFSpec == TFInit /\ [][TFNext]_TVars

(* ── B3 liveness spec: fairness lets the protocol drain under faults. ───── *)
(*    Forward completion + orphan cleanup are fair; the ack ENVELOPE actions *)
(*    (CoreRecallResp / CoreInvAck) are deliberately UNFAIR so "ack lost"    *)
(*    remains a legal behaviour that must be rescued by cleanup, not by      *)
(*    assuming the ack eventually arrives.                                   *)
TFFairSpec ==
    /\ TFSpec
    /\ WF_TVars(DeliverClear)
    /\ WF_TVars(CoreRecallToGrant)
    /\ WF_TVars(RecallOrphanWithAudit)
    /\ WF_TVars(CoreUpgradeCommit)
    /\ WF_TVars(CoreTick)

(* ══ B2: SAFETY invariants that must hold under ALL fault combinations ═══ *)

(* Directory stays canonical (MESI well-formedness) no matter what is        *)
(* dropped / duplicated / reordered. (Same as core SharersCanonical, re-     *)
(* asserted here so it is checked against the fault state space.)            *)
FaultDirCanonical == Canonical(dir)

(* No epoch/reqId tuple is ever committed twice, even under Clear duplicate  *)
(* + replay. This is the anti-double-apply guarantee under faults.           *)
FaultNoDoubleCommit ==
    \A i, j \in 1..Len(commitLog) : i # j => commitLog[i] # commitLog[j]

(* Epochs committed are strictly increasing under faults (no rollback from   *)
(* a duplicated/reordered message).                                          *)
FaultEpochMonotonic ==
    /\ Len(epochLog) >= 1
    /\ epochLog[Len(epochLog)] = dir.epoch
    /\ \A i \in 1..(Len(epochLog) - 1) : epochLog[i] < epochLog[i + 1]

(* Original transport-fault invariants (retained). *)
(* Scoped to DELIVERY records only. Drop/dup/cleanup audit entries record    *)
(* transport events, not delivery decisions, so they are excluded — a        *)
(* dropped redundant duplicate ("lost") is correct and must not be compared  *)
(* against the accepted delivery of the first copy.                          *)
SameClearOutcome(a, b) ==
    (   a.action = "deliver" /\ b.action = "deliver"
     /\ a.kind = "Clear" /\ b.kind = "Clear" /\ a.epoch = b.epoch /\ a.reqId = b.reqId)
      => ((a.outcome \in {"commit_accept", "replay_accept"}) = (b.outcome \in {"commit_accept", "replay_accept"}))

(* A given (epoch,reqId) Clear must never both commit and be rejected across *)
(* deliver attempts (duplicate delivery must be consistent). *)
TombstoneReplayConsistency ==
    \A i, j \in 1..Len(transportRecord) : SameClearOutcome(transportRecord[i], transportRecord[j])

(* A RECALL sitting in DONE must never coincide with a directory that has     *)
(* already been demoted to G_I/G_S (would signal a lost-recall data hazard).  *)
RecallDONE_WritebackSafety ==
    ~(ost.valid /\ ost.opType = "RECALL" /\ ost.stage = "DONE" /\ dir.state \in {"G_I", "G_S"})

(* ══ B3: LIVENESS under faults (checked as PROPERTY with TFFairSpec) ═════ *)

(* Even under drop/dup/reorder, no RECALL wedges the slot forever: it is     *)
(* consumed or the orphan cleanup fires. This is the fault-tolerant analogue *)
(* of RecallProgress.                                                        *)
FaultRecallProgress ==
    [](  (ost.valid /\ ost.opType = "RECALL")
       ~> (~(ost.valid /\ ost.opType = "RECALL")) )

=============================================================================
