-------------------------- MODULE ubcc_transport_faults --------------------------
EXTENDS ubcc_protocol_core, Sequences, TLC

(***************************************************************************)
(* Transport fault envelope for the core protocol.                         *)
(* Adds explicit messages for Clear / InvalidationAck / RecallResp and     *)
(* fault actions Drop / Duplicate / Deliver.                                *)
(***************************************************************************)

VARIABLES transport, transportRecord

TVars == <<dir, ost, tombstone, commitLog, epochLog, tick, transport, transportRecord>>

MsgKind == {"Clear", "InvAck", "RecallResp"}

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

CoreRecallResp ==
    /\ ~ReplayDrainBarrier
    /\ RecallResponse
    /\ UNCHANGED <<transport, transportRecord>>

CoreInvAck ==
    /\ ~ReplayDrainBarrier
    /\ \E n \in Nodes : BarrierAck(n)
    /\ UNCHANGED <<transport, transportRecord>>

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
        /\ UNCHANGED transportRecord
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

CoreRecallToGrant == /\ ~ReplayDrainBarrier /\ RecallToGrant /\ UNCHANGED <<transport, transportRecord>>

RecallOrphanWithAudit ==
    /\ ~ReplayDrainBarrier
    /\ RecallOrphanDisappears
    /\ transport' = transport
    /\ UNCHANGED transportRecord

CoreUpgradeCommit == /\ ~ReplayDrainBarrier /\ UpgradeCommit /\ UNCHANGED <<transport, transportRecord>>
CoreWriteback == /\ ~ReplayDrainBarrier /\ Writeback(0, FALSE) /\ UNCHANGED <<transport, transportRecord>>
CoreEvict == /\ ~ReplayDrainBarrier /\ Evict(0) /\ UNCHANGED <<transport, transportRecord>>
CoreTick == /\ ~ReplayDrainBarrier /\ TickOnly /\ UNCHANGED <<transport, transportRecord>>

TFStutter == /\ tick = MaxTick /\ UNCHANGED TVars

TFNext ==
    \/ CoreGrantExclusive
    \/ CoreRecallBarrier
    \/ CoreRecallResp
    \/ QueueClear
    \/ DeliverClear
    \/ DuplicateMsg
    \/ DropMsg
    \/ CoreRecallToGrant
    \/ RecallOrphanWithAudit
    \/ CoreWriteback
    \/ CoreTick
    \/ TFStutter

TFSpec == TFInit /\ [][TFNext]_TVars

SameClearOutcome(a, b) ==
    (a.kind = "Clear" /\ b.kind = "Clear" /\ a.epoch = b.epoch /\ a.reqId = b.reqId)
      => ((a.outcome \in {"commit_accept", "replay_accept"}) = (b.outcome \in {"commit_accept", "replay_accept"}))

TombstoneReplayConsistency ==
    \A i, j \in 1..Len(transportRecord) : SameClearOutcome(transportRecord[i], transportRecord[j])

RecallDONE_WritebackSafety ==
    ~(ost.valid /\ ost.opType = "RECALL" /\ ost.stage = "DONE" /\ dir.state \in {"G_I", "G_S"})

=============================================================================
