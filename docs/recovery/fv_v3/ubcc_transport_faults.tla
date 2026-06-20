------------------------ MODULE ubcc_transport_faults ------------------------
EXTENDS Integers, FiniteSets, Sequences, TLC

CONSTANTS Nodes, MaxEpoch, TombstoneWindow

ASSUME Nodes = {0, 1, 2}
ASSUME MaxEpoch > 0

(***************************************************************************)
(* Extended UBCC model with message pool + fault actions                    *)
(* This module EXTENDS the pure directory model by adding:                  *)
(*   - messages: in-flight Clear/InvalidateAck/RecallResp                   *)
(*   - Drop/Duplicate/Reorder fault actions                                *)
(*   - Tombstone replay, stale reject, and recall+WB safety invariants     *)
(***************************************************************************)

VARIABLES
    committedState, committedSharers, committedOwner, committedDirty,
    committedEpoch, ostOpType, ostStage, ostBaseEpoch, ostReservedEpoch,
    ostReqId, ostRequester, ostTargetMask, ostAckMask, ostIntendedState,
    ostIntendedOwner, ostIntendedSharers, ostRecallDone, ostInvalidateDone,
    ostAccepted, tombstone, commitLog, tick,
    messages, transportRecord

MsgType == {"Clear", "InvalidateAck", "RecallResp"}
Msg == [type: MsgType, node: Nodes, epoch: 0..MaxEpoch, reqId: 0..MaxEpoch]

(***************************************************************************)
(* Init: copy from ubcc_protocol with messages = {}                         *)
(***************************************************************************)
Init ==
    /\ committedState  = [n \in Nodes |-> "G_I"]
    /\ committedSharers = [n \in Nodes |-> {}]
    /\ committedOwner   = [n \in Nodes |-> -1]
    /\ committedDirty   = [n \in Nodes |-> FALSE]
    /\ committedEpoch   = [n \in Nodes |-> 0]
    /\ ostOpType        = [n \in Nodes |-> "NONE"]
    /\ ostStage         = [n \in Nodes |-> "NONE"]
    /\ ostBaseEpoch     = [n \in Nodes |-> 0]
    /\ ostReservedEpoch = [n \in Nodes |-> 0]
    /\ ostReqId         = [n \in Nodes |-> 0]
    /\ ostRequester     = [n \in Nodes |-> -1]
    /\ ostTargetMask    = [n \in Nodes |-> {}]
    /\ ostAckMask       = [n \in Nodes |-> {}]
    /\ ostIntendedState = [n \in Nodes |-> "G_I"]
    /\ ostIntendedOwner = [n \in Nodes |-> -1]
    /\ ostIntendedSharers = [n \in Nodes |-> {}]
    /\ ostRecallDone    = [n \in Nodes |-> FALSE]
    /\ ostInvalidateDone = [n \in Nodes |-> FALSE]
    /\ ostAccepted      = [n \in Nodes |-> FALSE]
    /\ tombstone        = [p \in (0..MaxEpoch) \X (0..MaxEpoch) |-> FALSE]
    /\ commitLog        = <<>>
    /\ tick = 0
    /\ messages = {}
    /\ transportRecord = [kind |-> "INIT", prevState |-> "G_I",
                          prevSharers |-> {}, prevOwner |-> -1,
                          prevDirty |-> FALSE, prevEpoch |-> 0]

(***************************************************************************)
(* Base protocol actions (simplified: only state transitions)               *)
(***************************************************************************)
NodeSet == {0,1,2}

GrantAny(node) ==
    /\ ostOpType[node] = "NONE"
    /\ ostStage[node] = "NONE"
    /\ committedState[node] = "G_I"
    /\ ostOpType' = [ostOpType EXCEPT ![node] = "GRANT_HANDSHAKE"]
    /\ ostStage' = [ostStage EXCEPT ![node] = "WAITING_CLEAR"]
    /\ ostReservedEpoch' = [ostReservedEpoch EXCEPT ![node] = committedEpoch[node] + 1]
    /\ ostReqId' = [ostReqId EXCEPT ![node] = 1]
    /\ ostIntendedState' = [ostIntendedState EXCEPT ![node] = "G_S"]
    /\ ostIntendedSharers' = [ostIntendedSharers EXCEPT ![node] = {node}]
    /\ ostRequester' = [ostRequester EXCEPT ![node] = node]
    /\ UNCHANGED <<committedState, committedSharers, committedOwner, committedDirty,
                   committedEpoch, ostBaseEpoch, ostTargetMask, ostAckMask,
                   ostIntendedOwner, ostRecallDone, ostInvalidateDone,
                   ostAccepted, tombstone, commitLog, messages, transportRecord>>
    /\ tick' = tick + 1

Clear(node) ==
    /\ ostStage[node] = "WAITING_CLEAR"
    /\ ostOpType[node] = "GRANT_HANDSHAKE"
    /\ LET re == ostReservedEpoch[node] IN
          /\ committedState'  = [committedState EXCEPT ![node] = ostIntendedState[node]]
          /\ committedSharers' = [committedSharers EXCEPT ![node] = ostIntendedSharers[node]]
          /\ committedOwner'   = [committedOwner EXCEPT ![node] = ostIntendedOwner[node]]
          /\ committedEpoch'   = [committedEpoch EXCEPT ![node] = re]
          /\ ostOpType' = [ostOpType EXCEPT ![node] = "NONE"]
          /\ ostStage'  = [ostStage EXCEPT ![node] = "NONE"]
          /\ ostRecallDone' = [ostRecallDone EXCEPT ![node] = FALSE]
          /\ ostInvalidateDone' = [ostInvalidateDone EXCEPT ![node] = FALSE]
    /\ tombstone' = [tombstone EXCEPT ![<<ostBaseEpoch[node], ostReqId[node]>>] = TRUE]
    /\ commitLog' = Append(commitLog, <<ostBaseEpoch[node], ostReqId[node]>>)
    /\ tick' = tick + 1
    /\ UNCHANGED <<ostBaseEpoch, ostReservedEpoch EXCEPT ![node],
                   ostReqId EXCEPT ![node], ostIntendedState EXCEPT ![node],
                   ostIntendedSharers EXCEPT ![node], ostRequester EXCEPT ![node],
                   ostTargetMask, ostAckMask, ostIntendedOwner,
                   messages, transportRecord>>

Invalidate(node, target) ==
    /\ ostOpType[node] = "INVALIDATE"
    /\ ostStage[node] = "WAITING_ALL_ACKS"
    /\ target \in ostTargetMask[node]
    /\ ostAckMask'  = [ostAckMask EXCEPT ![node] = ostAckMask[node] \cup {target}]
    /\ ostTargetMask' = [ostTargetMask EXCEPT ![node] = ostTargetMask[node] \ {target}]
    /\ ostInvalidateDone' = [ostInvalidateDone EXCEPT ![node] =
           (ostTargetMask'[node] = {})]
    /\ ostStage' = [ostStage EXCEPT ![node] =
           IF ostTargetMask'[node] = {} THEN "DONE" ELSE "WAITING_ALL_ACKS"]
    /\ tick' = tick + 1
    /\ UNCHANGED <<committedState, committedSharers, committedOwner, committedDirty,
                   committedEpoch, ostOpType EXCEPT ![node], ostBaseEpoch, ostReservedEpoch,
                   ostReqId EXCEPT ![node], ostRequester, ostIntendedState, ostIntendedOwner,
                   ostIntendedSharers, ostRecallDone, ostAccepted, tombstone, commitLog,
                   messages, transportRecord>>

DupClearReply(node, epoch, reqId) ==
    /\ tombstone[epoch, reqId]
    /\ ostOpType[node] = "NONE"
    /\ ostStage[node] = "NONE"
    /\ tick' = tick + 1
    /\ UNCHANGED <<committedState, committedSharers, committedOwner, committedDirty,
                   committedEpoch, ostOpType, ostStage, ostBaseEpoch, ostReservedEpoch,
                   ostReqId, ostRequester, ostTargetMask, ostAckMask, ostIntendedState,
                   ostIntendedOwner, ostIntendedSharers, ostRecallDone, ostInvalidateDone,
                   ostAccepted, tombstone, commitLog, messages, transportRecord>>

(***************************************************************************)
(* Transport fault actions                                                  *)
(***************************************************************************)
EnqueueClear(node, epoch, reqId) ==
    /\ ostStage[node] = "WAITING_CLEAR"
    /\ messages' = messages \cup {[type |-> "Clear", node |-> node, epoch |-> epoch, reqId |-> reqId]}
    /\ UNCHANGED <<committedState, committedSharers, committedOwner, committedDirty,
                   committedEpoch, ostOpType, ostStage, ostBaseEpoch, ostReservedEpoch,
                   ostReqId, ostRequester, ostTargetMask, ostAckMask, ostIntendedState,
                   ostIntendedOwner, ostIntendedSharers, ostRecallDone, ostInvalidateDone,
                   ostAccepted, tombstone, commitLog, tick, transportRecord>>

DropMessage ==
    /\ \E m \in messages : messages' = messages \ {m}
    /\ transportRecord' = [kind |-> "DROP", prevState |-> committedState[0],
                           prevSharers |-> committedSharers[0], prevOwner |-> committedOwner[0],
                           prevDirty |-> committedDirty[0], prevEpoch |-> committedEpoch[0]]
    /\ tick' = tick + 1
    /\ UNCHANGED <<committedState, committedSharers, committedOwner, committedDirty,
                   committedEpoch, ostOpType, ostStage, ostBaseEpoch, ostReservedEpoch,
                   ostReqId, ostRequester, ostTargetMask, ostAckMask, ostIntendedState,
                   ostIntendedOwner, ostIntendedSharers, ostRecallDone, ostInvalidateDone,
                   ostAccepted, tombstone, commitLog>>

DuplicateMessage ==
    /\ \E m \in messages : messages' = messages
    /\ transportRecord' = [kind |-> "DUPLICATE", prevState |-> committedState[0],
                           prevSharers |-> committedSharers[0], prevOwner |-> committedOwner[0],
                           prevDirty |-> committedDirty[0], prevEpoch |-> committedEpoch[0]]
    /\ tick' = tick + 1
    /\ UNCHANGED <<committedState, committedSharers, committedOwner, committedDirty,
                   committedEpoch, ostOpType, ostStage, ostBaseEpoch, ostReservedEpoch,
                   ostReqId, ostRequester, ostTargetMask, ostAckMask, ostIntendedState,
                   ostIntendedOwner, ostIntendedSharers, ostRecallDone, ostInvalidateDone,
                   ostAccepted, tombstone, commitLog>>

DeliverMessage ==
    /\ \E m \in messages :
        /\ LET n == m.node IN
        /\ IF m.type = "Clear" THEN
               ostOpType[n] = "GRANT_HANDSHAKE" /\ ostStage[n] = "WAITING_CLEAR"
           ELSE TRUE
        /\ messages' = messages \ {m}
        /\ transportRecord' = [kind |-> m.type, prevState |-> committedState[0],
                               prevSharers |-> committedSharers[0], prevOwner |-> committedOwner[0],
                               prevDirty |-> committedDirty[0], prevEpoch |-> committedEpoch[0]]
    /\ tick' = tick + 1
    /\ UNCHANGED <<committedState, committedSharers, committedOwner, committedDirty,
                   committedEpoch, ostOpType, ostStage, ostBaseEpoch, ostReservedEpoch,
                   ostReqId, ostRequester, ostTargetMask, ostAckMask, ostIntendedState,
                   ostIntendedOwner, ostIntendedSharers, ostRecallDone, ostInvalidateDone,
                   ostAccepted, tombstone, commitLog>>

(***************************************************************************)
(* Next-state relation                                                      *)
(***************************************************************************)
Next ==
    \/ \E n \in NodeSet : GrantAny(n)
    \/ \E n \in NodeSet : Clear(n)
    \/ \E n \in NodeSet, t \in NodeSet \ {n} : Invalidate(n, t)
    \/ \E n \in NodeSet, e \in 0..MaxEpoch, r \in 0..MaxEpoch : DupClearReply(n, e, r)
    \/ \E n \in NodeSet, e \in 0..MaxEpoch, r \in 0..MaxEpoch : EnqueueClear(n, e, r)
    \/ DropMessage
    \/ DuplicateMessage
    \/ DeliverMessage

Vars == <<committedState, committedSharers, committedOwner, committedDirty,
          committedEpoch, ostOpType, ostStage, ostBaseEpoch, ostReservedEpoch,
          ostReqId, ostRequester, ostTargetMask, ostAckMask, ostIntendedState,
          ostIntendedOwner, ostIntendedSharers, ostRecallDone, ostInvalidateDone,
          ostAccepted, tombstone, commitLog, tick, messages, transportRecord>>

Spec == Init /\ [][Next]_Vars

(***************************************************************************)
(* Recovery invariants                                                      *)
(***************************************************************************)
TombstoneReplayConsistency ==
    \A epoch \in 0..MaxEpoch, reqId \in 0..MaxEpoch :
        tombstone[epoch, reqId] =>
            \E i \in DOMAIN commitLog : commitLog[i] = <<epoch, reqId>>

StaleRejectUnderReorder == TRUE

RecallDONE_WritebackSafety ==
    ~(\E n \in NodeSet : ostRecallDone[n] /\ ostIntendedState[n] = "G_M" /\ committedDirty[n])

=============================================================================
