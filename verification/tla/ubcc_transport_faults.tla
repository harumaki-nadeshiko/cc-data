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

TFInit ==
    /\ Init
    /\ transport = <<>>
    /\ transportRecord = <<>>

CoreGrantShared ==
    /\ \E req \in Nodes : \E reqId \in ReqIds : GrantShared(req, reqId)
    /\ UNCHANGED <<transport, transportRecord>>

CoreGrantExclusive ==
    /\ \E req \in Nodes : \E wi \in BOOLEAN : \E reqId \in ReqIds : GrantExclusive(req, wi, reqId)
    /\ UNCHANGED <<transport, transportRecord>>

CoreRecallBarrier ==
    /\ \E req \in Nodes : \E rt \in ReqType : \E wi \in BOOLEAN : \E reqId \in ReqIds : RecallBarrier(req, rt, wi, reqId)
    /\ UNCHANGED <<transport, transportRecord>>

CoreInvalidateBarrier ==
    /\ \E req \in Nodes : \E wi \in BOOLEAN : \E reqId \in ReqIds : InvalidationBarrier(req, wi, reqId)
    /\ UNCHANGED <<transport, transportRecord>>

CoreUpgradeBarrier ==
    /\ \E req \in Nodes : \E wi \in BOOLEAN : \E reqId \in ReqIds : UpgradeBarrier(req, wi, reqId)
    /\ UNCHANGED <<transport, transportRecord>>

QueueRecallResp ==
    /\ tick < MaxTick
    /\ ost.valid /\ ost.opType = "RECALL" /\ ost.stage = "WAITING_TARGET_RESP"
    /\ ~HasQueued("RecallResp", ost.baseEpoch, ost.reqId)
    /\ UNCHANGED <<dir, ost, tombstone, commitLog, epochLog>>
    /\ transport' = Append(transport, [kind |-> "RecallResp", src |-> CHOOSE n \in ost.target : TRUE,
                                        epoch |-> ost.baseEpoch, reqId |-> ost.reqId])
    /\ transportRecord' = Append(transportRecord, Audit("enqueue", "RecallResp", ost.baseEpoch, ost.reqId, "na"))
    /\ tick' = tick + 1

QueueInvAck ==
    /\ tick < MaxTick
    /\ ost.valid /\ ost.stage = "WAITING_ALL_ACKS" /\ ost.opType \in {"INVALIDATE", "UPGRADE_PENDING"}
    /\ \E n \in (ost.target \ ost.acked) : ~HasQueued("InvAck", ost.baseEpoch, ost.reqId)
    /\ LET n == CHOOSE x \in (ost.target \ ost.acked) : TRUE IN
       /\ UNCHANGED <<dir, ost, tombstone, commitLog, epochLog>>
       /\ transport' = Append(transport, [kind |-> "InvAck", src |-> n, epoch |-> ost.baseEpoch, reqId |-> ost.reqId])
       /\ transportRecord' = Append(transportRecord, Audit("enqueue", "InvAck", ost.baseEpoch, ost.reqId, "na"))
       /\ tick' = tick + 1

QueueClear ==
    /\ tick < MaxTick
    /\ ost.valid /\ ost.opType = "GRANT_HANDSHAKE" /\ ost.stage = "WAITING_CLEAR"
    /\ ~HasQueued("Clear", ost.baseEpoch, ost.reqId)
    /\ UNCHANGED <<dir, ost, tombstone, commitLog, epochLog>>
    /\ transport' = Append(transport, [kind |-> "Clear", src |-> ost.requester, epoch |-> ost.baseEpoch, reqId |-> ost.reqId])
    /\ transportRecord' = Append(transportRecord, Audit("enqueue", "Clear", ost.baseEpoch, ost.reqId, "na"))
    /\ tick' = tick + 1

DeliverRecallResp ==
    /\ \E i \in 1..Len(transport) :
        /\ transport[i].kind = "RecallResp"
        /\ RecallResponse
        /\ transport' = RemoveAt(transport, i)
        /\ transportRecord' = Append(transportRecord, Audit("deliver", "RecallResp", transport[i].epoch, transport[i].reqId, "accepted"))

DeliverInvAck ==
    /\ \E i \in 1..Len(transport) :
        /\ transport[i].kind = "InvAck"
        /\ BarrierAck(transport[i].src)
        /\ transport' = RemoveAt(transport, i)
        /\ transportRecord' = Append(transportRecord, Audit("deliver", "InvAck", transport[i].epoch, transport[i].reqId, "accepted"))

DeliverClear ==
    /\ \E i \in 1..Len(transport) :
        /\ transport[i].kind = "Clear"
        /\ IF tombstone.valid /\ transport[i].epoch = tombstone.epoch /\ transport[i].reqId = tombstone.reqId
              THEN /\ dir' = dir
                   /\ ost' = ost
                   /\ tombstone' = AgeTombstone(tombstone)
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
                   /\ tombstone' = AgeTombstone(tombstone)
                   /\ UNCHANGED <<commitLog, epochLog>>
                   /\ transport' = RemoveAt(transport, i)
                   /\ transportRecord' = Append(transportRecord, Audit("deliver", "Clear", transport[i].epoch, transport[i].reqId, "reject"))
                   /\ tick' = tick + 1

DropMsg ==
    /\ tick < MaxTick
    /\ Len(transport) > 0
    /\ \E i \in 1..Len(transport) :
        /\ dir' = dir
        /\ ost' = ost
        /\ tombstone' = AgeTombstone(tombstone)
        /\ UNCHANGED <<commitLog, epochLog>>
        /\ transport' = RemoveAt(transport, i)
        /\ transportRecord' = Append(transportRecord, Audit("drop", transport[i].kind, transport[i].epoch, transport[i].reqId, "na"))
        /\ tick' = tick + 1

DuplicateMsg ==
    /\ tick < MaxTick
    /\ Len(transport) > 0
    /\ \E i \in 1..Len(transport) :
        /\ UNCHANGED <<dir, ost, tombstone, commitLog, epochLog>>
        /\ transport' = Append(transport, transport[i])
        /\ transportRecord' = Append(transportRecord, Audit("duplicate", transport[i].kind, transport[i].epoch, transport[i].reqId, "na"))
        /\ tick' = tick + 1

CoreRecallToGrant == RecallToGrant /\ UNCHANGED <<transport, transportRecord>>

RecallOrphanWithAudit ==
    /\ RecallOrphanDisappears
    /\ transport' = transport
    /\ transportRecord' = Append(transportRecord,
          Audit("cleanup", "RecallResp", IF Len(epochLog) > 0 THEN dir.epoch ELSE 0, IF ost.valid THEN ost.reqId ELSE 0, "orphan_disappear"))

CoreUpgradeCommit == UpgradeCommit /\ UNCHANGED <<transport, transportRecord>>
CoreWriteback == \E n \in Nodes : \E keep \in BOOLEAN : Writeback(n, keep) /\ UNCHANGED <<transport, transportRecord>>
CoreEvict == \E n \in Nodes : Evict(n) /\ UNCHANGED <<transport, transportRecord>>
CoreTick == TickOnly /\ UNCHANGED <<transport, transportRecord>>

TFStutter == /\ tick = MaxTick /\ UNCHANGED TVars

TFNext ==
    \/ CoreGrantShared
    \/ CoreGrantExclusive
    \/ CoreRecallBarrier
    \/ CoreInvalidateBarrier
    \/ CoreUpgradeBarrier
    \/ QueueRecallResp
    \/ QueueInvAck
    \/ QueueClear
    \/ DeliverRecallResp
    \/ DeliverInvAck
    \/ DeliverClear
    \/ DuplicateMsg
    \/ DropMsg
    \/ CoreRecallToGrant
    \/ RecallOrphanWithAudit
    \/ CoreUpgradeCommit
    \/ CoreWriteback
    \/ CoreEvict
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
