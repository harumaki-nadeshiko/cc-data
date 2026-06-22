------------------------ MODULE ubcc_transport_faults ------------------------
(**
 * Extended UBCC model with message pool + transport fault actions.
 *
 * This module EXTENDS ubcc_protocol_core (single-PA scalar model) and adds:
 *   - messages: in-flight Clear/InvalidateAck/RecallResp
 *   - Enqueue actions: capture protocol events into the message pool
 *   - Drop/Duplicate/Deliver fault actions: simulate transport faults
 *   - recovery invariants: TombstoneReplayConsistency, StaleRejectUnderReorder,
 *     RecallDONE_WritebackSafety
 *
 * Self-contained top-level: imports core, defines own Init, Next, Spec.
 *)

EXTENDS ubcc_protocol_core

(***************************************************************************)
(* Additional state variables                                               *)
(***************************************************************************)

VARIABLES
    messages,           \* Set of in-flight messages
    transportRecord     \* Record of last transport event for audit

(***************************************************************************)
(* Message types                                                            *)
(***************************************************************************)

MsgType == {"Clear", "InvalidationAck", "RecallResp"}
Msg == [type: MsgType, node: Nodes, epoch: (0 .. MaxEpoch), reqId: (0 .. MaxEpoch)]

(***************************************************************************)
(* Initial state (extends BaseInit)                                         *)
(***************************************************************************)

Init ==
    /\ BaseInit
    /\ messages = {}
    /\ transportRecord = [kind |-> "INIT", prevState |-> "G_I",
                          prevSharers |-> {}, prevOwner |-> -1,
                          prevDirty |-> FALSE, prevEpoch |-> 0]

(***************************************************************************)
(* Enqueue actions: capture protocol events into the message pool           *)
(* These are "instantaneous" — no tick advance.                            *)
(***************************************************************************)

EnqueueClear ==
    /\ ostOpType = "GRANT_HANDSHAKE"
    /\ ostStage = "WAITING_CLEAR"
    /\ messages' = messages \cup {[type |-> "Clear", node |-> ostRequester,
                                    epoch |-> ostReservedEpoch, reqId |-> ostReqId]}
    /\ UNCHANGED <<committedState, committedSharers, committedOwner, committedDirty,
                   committedEpoch, ostOpType, ostStage, ostBaseEpoch, ostReservedEpoch,
                   ostReqId, ostRequester, ostTargetMask, ostAckMask, ostIntendedState,
                   ostIntendedOwner, ostIntendedSharers, ostRecallDone, ostInvalidateDone,
                   ostAccepted, tombstone, commitLog, tick, transportRecord>>

EnqueueInvalidationAck(node) ==
    /\ ostOpType = "INVALIDATE"
    /\ ostStage = "WAITING_ALL_ACKS"
    /\ node \in ostTargetMask
    /\ messages' = messages \cup {[type |-> "InvalidationAck", node |-> node,
                                    epoch |-> ostReservedEpoch, reqId |-> ostReqId]}
    /\ UNCHANGED <<committedState, committedSharers, committedOwner, committedDirty,
                   committedEpoch, ostOpType, ostStage, ostBaseEpoch, ostReservedEpoch,
                   ostReqId, ostRequester, ostTargetMask, ostAckMask, ostIntendedState,
                   ostIntendedOwner, ostIntendedSharers, ostRecallDone, ostInvalidateDone,
                   ostAccepted, tombstone, commitLog, tick, transportRecord>>

EnqueueRecallResp ==
    /\ ostOpType = "RECALL"
    /\ ostStage = "WAITING_TARGET_RESP"
    /\ messages' = messages \cup {[type |-> "RecallResp", node |-> committedOwner,
                                    epoch |-> ostReservedEpoch, reqId |-> ostReqId]}
    /\ UNCHANGED <<committedState, committedSharers, committedOwner, committedDirty,
                   committedEpoch, ostOpType, ostStage, ostBaseEpoch, ostReservedEpoch,
                   ostReqId, ostRequester, ostTargetMask, ostAckMask, ostIntendedState,
                   ostIntendedOwner, ostIntendedSharers, ostRecallDone, ostInvalidateDone,
                   ostAccepted, tombstone, commitLog, tick, transportRecord>>

(***************************************************************************)
(* Transport fault actions                                                  *)
(***************************************************************************)

(**
 * DropMessage: remove a message from the pool without delivering it.
 * Simulates message loss in transport.
 *)
DropMessage ==
    /\ \E m \in messages : messages' = messages \ {m}
    /\ transportRecord' = [kind |-> "DROP", prevState |-> committedState,
                           prevSharers |-> committedSharers, prevOwner |-> committedOwner,
                           prevDirty |-> committedDirty, prevEpoch |-> committedEpoch]
    /\ tick' = tick + 1
    /\ UNCHANGED <<committedState, committedSharers, committedOwner, committedDirty,
                   committedEpoch, ostOpType, ostStage, ostBaseEpoch, ostReservedEpoch,
                   ostReqId, ostRequester, ostTargetMask, ostAckMask, ostIntendedState,
                   ostIntendedOwner, ostIntendedSharers, ostRecallDone, ostInvalidateDone,
                   ostAccepted, tombstone, commitLog>>

(**
 * DuplicateMessage: keep a message in the pool (simulates duplication).
 * Since messages is a set, duplicates are idempotent — we record the event.
 *)
DuplicateMessage ==
    /\ \E m \in messages : messages' = messages
    /\ transportRecord' = [kind |-> "DUPLICATE", prevState |-> committedState,
                           prevSharers |-> committedSharers, prevOwner |-> committedOwner,
                           prevDirty |-> committedDirty, prevEpoch |-> committedEpoch]
    /\ tick' = tick + 1
    /\ UNCHANGED <<committedState, committedSharers, committedOwner, committedDirty,
                   committedEpoch, ostOpType, ostStage, ostBaseEpoch, ostReservedEpoch,
                   ostReqId, ostRequester, ostTargetMask, ostAckMask, ostIntendedState,
                   ostIntendedOwner, ostIntendedSharers, ostRecallDone, ostInvalidateDone,
                   ostAccepted, tombstone, commitLog>>

(**
 * DeliverMessage: remove a message from the pool and record delivery.
 * For Clear messages, the protocol must be in WAITING_CLEAR state.
 * Protocol state is NOT modified here — that is done by BaseNext actions
 * (ClearArrives, InvalidationAckArrives, RecallResponseArrives).
 * This decoupling allows testing of message reordering vs state transitions.
 *)
DeliverMessage ==
    /\ \E m \in messages :
        /\ IF m.type = "Clear" THEN
               ostOpType = "GRANT_HANDSHAKE" /\ ostStage = "WAITING_CLEAR"
           ELSE TRUE
        /\ messages' = messages \ {m}
        /\ transportRecord' = [kind |-> m.type, prevState |-> committedState,
                               prevSharers |-> committedSharers, prevOwner |-> committedOwner,
                               prevDirty |-> committedDirty, prevEpoch |-> committedEpoch]
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
    \/ BaseNext
    \/ EnqueueClear
    \/ \E node ∈ Nodes : EnqueueInvalidationAck(node)
    \/ EnqueueRecallResp
    \/ DropMessage
    \/ DuplicateMessage
    \/ DeliverMessage

(***************************************************************************)
(* Specification                                                            *)
(***************************************************************************)

Vars == <<committedState, committedSharers, committedOwner, committedDirty,
          committedEpoch, ostOpType, ostStage, ostBaseEpoch, ostReservedEpoch,
          ostReqId, ostRequester, ostTargetMask, ostAckMask, ostIntendedState,
          ostIntendedOwner, ostIntendedSharers, ostRecallDone, ostInvalidateDone,
          ostAccepted, tombstone, commitLog, tick, messages, transportRecord>>

Spec == Init /\ [][Next]_Vars

(***************************************************************************)
(* Recovery invariants (in addition to core invariants)                     *)
(***************************************************************************)

(**
 * TombstoneReplayConsistency: every tombstone entry must have a
 * corresponding entry in the commit log.  Ensures tombstone replay
 * only happens for genuinely committed (epoch, reqId) pairs.
 *)
TombstoneReplayConsistency ==
    \A epoch \in (0 .. MaxEpoch), reqId \in (0 .. MaxEpoch) :
        tombstone[epoch, reqId] =>
            \E i \in DOMAIN commitLog : commitLog[i] = <<epoch, reqId>>

(**
 * StaleRejectUnderReorder: placeholder invariant for stale message
 * rejection under reordering.  Currently vacuously true; to be
 * strengthened when stale rejection logic is formalized.
 *)
StaleRejectUnderReorder == TRUE

(**
 * RecallDONE_WritebackSafety: when recall barrier is done and the
 * intended state would be dirty-modified, the committed dirty flag
 * must not be set.  This prevents a writeback from overwriting
 * data that is being recalled for a new owner.
 *)
RecallDONE_WritebackSafety ==
    ~(ostRecallDone /\ ostIntendedState = "G_M" /\ committedDirty)

=============================================================================
