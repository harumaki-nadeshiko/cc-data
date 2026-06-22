------------------------------ MODULE ubcc_protocol ------------------------------
EXTENDS ubcc_protocol_core, FiniteSets, Sequences

(***************************************************************************)
(* Multi-node refinement of the core model.                                *)
(* Adds:                                                                   *)
(*   - per-node cache view                                                 *)
(*   - explicit recall / invalidate message flow                           *)
(*                                                                         *)
(* Still abstracted away: transport latency, backstore, BF, MetaRNF.      *)
(***************************************************************************)

VARIABLES ncache0, ncache1, ncache2, net

NCacheVars == <<ncache0, ncache1, ncache2>>

CacheState == {"I", "S", "E", "M"}
MsgKind == {"RECALL_REQ", "RECALL_RESP", "INV_REQ", "INV_ACK"}

Msg(k, s, d) == [kind |-> k, src |-> s, dst |-> d]

\* Per-node cache state record (serializable by TLC)
NodeCache == ["0" |-> ncache0, "1" |-> ncache1, "2" |-> ncache2]

\* Derive one node's cache state from the directory state
CacheForNode(d, n) ==
    IF d.state = "G_I" THEN "I"
    ELSE IF d.state = "G_S" THEN IF n \in d.sharers THEN "S" ELSE "I"
    ELSE IF n = d.owner THEN IF d.state = "G_M" THEN "M" ELSE "E"
    ELSE "I"

MNInit ==
    /\ Init
    /\ ncache0 = CacheForNode(dir, "0")
    /\ ncache1 = CacheForNode(dir, "1")
    /\ ncache2 = CacheForNode(dir, "2")
    /\ net = {}

CoreGrantShared ==
    /\ \E req \in Nodes : \E reqId \in ReqIds : GrantShared(req, reqId)
    /\ UNCHANGED <<ncache0, ncache1, ncache2, net>>

CoreGrantExclusive ==
    /\ \E req \in Nodes : \E wi \in BOOLEAN : \E reqId \in ReqIds : GrantExclusive(req, wi, reqId)
    /\ UNCHANGED <<nodeCache, net>>

CoreRecallBarrier ==
    /\ \E req \in Nodes : \E rt \in ReqType : \E wi \in BOOLEAN : \E reqId \in ReqIds : RecallBarrier(req, rt, wi, reqId)
    /\ UNCHANGED <<nodeCache, net>>

CoreInvalidateBarrier ==
    /\ \E req \in Nodes : \E wi \in BOOLEAN : \E reqId \in ReqIds : InvalidationBarrier(req, wi, reqId)
    /\ UNCHANGED <<nodeCache, net>>

CoreUpgradeBarrier ==
    /\ \E req \in Nodes : \E wi \in BOOLEAN : \E reqId \in ReqIds : UpgradeBarrier(req, wi, reqId)
    /\ UNCHANGED <<nodeCache, net>>

SendRecallReq ==
    /\ tick < MaxTick
    /\ ost.valid
    /\ ost.opType = "RECALL"
    /\ ost.stage = "WAITING_TARGET_RESP"
    /\ LET target == CHOOSE n \in ost.target : TRUE IN
       /\ Msg("RECALL_REQ", NoneNode, target) \notin net
       /\ UNCHANGED <<dir, ost, commitLog, epochLog, nodeCache>>
       /\ tombstone' = AgeTombstone(tombstone)
       /\ net' = net \cup {Msg("RECALL_REQ", NoneNode, target)}
       /\ tick' = tick + 1

RecallTargetReplies ==
    /\ tick < MaxTick
    /\ \E m \in net :
        /\ m.kind = "RECALL_REQ"
        /\ ost.valid
        /\ ost.opType = "RECALL"
        /\ ost.stage = "WAITING_TARGET_RESP"
        /\ UNCHANGED <<dir, ost, commitLog, epochLog>>
        /\ tombstone' = AgeTombstone(tombstone)
        /\ net' = (net \ {m}) \cup {Msg("RECALL_RESP", m.dst, NoneNode)}
        /\ nodeCache' = [nodeCache EXCEPT ![m.dst] = IF ost.reqType = "RS" THEN "S" ELSE "I"]
        /\ tick' = tick + 1

DeliverRecallResp ==
    /\ \E m \in net :
        /\ m.kind = "RECALL_RESP"
        /\ RecallResponse
        /\ net' = net \ {m}
        /\ UNCHANGED nodeCache

SendInvReq ==
    /\ tick < MaxTick
    /\ ost.valid
    /\ ost.stage = "WAITING_ALL_ACKS"
    /\ ost.opType \in {"INVALIDATE", "UPGRADE_PENDING"}
    /\ \E n \in (ost.target \ ost.acked) : Msg("INV_REQ", NoneNode, n) \notin net /\ Msg("INV_ACK", n, NoneNode) \notin net
    /\ LET n == CHOOSE x \in (ost.target \ ost.acked) : Msg("INV_REQ", NoneNode, x) \notin net /\ Msg("INV_ACK", x, NoneNode) \notin net IN
       /\ UNCHANGED <<dir, ost, commitLog, epochLog, nodeCache>>
       /\ tombstone' = AgeTombstone(tombstone)
       /\ net' = net \cup {Msg("INV_REQ", NoneNode, n)}
       /\ tick' = tick + 1

InvalidateTargetReplies ==
    /\ tick < MaxTick
    /\ \E m \in net :
        /\ m.kind = "INV_REQ"
        /\ UNCHANGED <<dir, ost, commitLog, epochLog>>
        /\ tombstone' = AgeTombstone(tombstone)
        /\ net' = (net \ {m}) \cup {Msg("INV_ACK", m.dst, NoneNode)}
        /\ nodeCache' = [nodeCache EXCEPT ![m.dst] = "I"]
        /\ tick' = tick + 1

DeliverInvAck ==
    /\ \E m \in net :
        /\ m.kind = "INV_ACK"
        /\ BarrierAck(m.src)
        /\ net' = net \ {m}
        /\ UNCHANGED nodeCache

CommitClearWithNodes ==
    /\ ClearCommit
    /\ nodeCache' = CacheFromDir(dir')
    /\ UNCHANGED net

CommitUpgradeWithNodes ==
    /\ UpgradeCommit
    /\ nodeCache' = CacheFromDir(dir')
    /\ UNCHANGED net

CoreWriteback ==
    /\ \E n \in Nodes : \E keep \in BOOLEAN : Writeback(n, keep)
    /\ nodeCache' = CacheFromDir(dir')
    /\ UNCHANGED net

CoreEvict ==
    /\ \E n \in Nodes : Evict(n)
    /\ nodeCache' = CacheFromDir(dir')
    /\ UNCHANGED net

CoreRecallToGrant == RecallToGrant /\ UNCHANGED <<nodeCache, net>>
CoreRecallOrphan == RecallOrphanDisappears /\ UNCHANGED <<nodeCache, net>>
CoreTick == TickOnly /\ UNCHANGED <<nodeCache, net>>

MNStutter == /\ tick = MaxTick /\ UNCHANGED MVars

MNNext ==
    \/ CoreGrantShared
    \/ CoreGrantExclusive
    \/ CoreRecallBarrier
    \/ CoreInvalidateBarrier
    \/ CoreUpgradeBarrier
    \/ SendRecallReq
    \/ RecallTargetReplies
    \/ DeliverRecallResp
    \/ CoreRecallToGrant
    \/ SendInvReq
    \/ InvalidateTargetReplies
    \/ DeliverInvAck
    \/ CommitClearWithNodes
    \/ CommitUpgradeWithNodes
    \/ CoreRecallOrphan
    \/ CoreWriteback
    \/ CoreEvict
    \/ CoreTick
    \/ MNStutter

MNSpec == MNInit /\ [][MNNext]_MVars

StableNodeConsistency == (~ost.valid /\ net = {}) => nodeCache = CacheFromDir(dir)

SingleDirtyHolder == Cardinality({n \in Nodes : nodeCache[n] = "M"}) <= 1

NetWellFormed == \A m \in net : /\ m.kind \in MsgKind /\ m.dst \in (Nodes \cup {NoneNode})

=============================================================================
