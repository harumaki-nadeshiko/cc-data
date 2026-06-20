------------------------ MODULE ubcc_transport_faults ------------------------
(**
 * Future-work transport-fault model built on top of `ubcc_protocol`.
 *
 * Adds a nondeterministic in-flight message pool for the three UB response
 * classes reviewed in FV-4:
 *   - Clear
 *   - InvalidateAck
 *   - RecallResp
 *
 * Fault model:
 *   - DropMessage      : remove any in-flight message
 *   - DuplicateMessage : clone a logical message into another copy slot
 *   - ReorderMessages  : explicit reorder event; delivery is already from an
 *                        unordered pool, so reorder is modeled as a pure
 *                        interleaving/fault marker
 *
 * Recovery invariants:
 *   - TombstoneReplayConsistency
 *   - StaleRejectUnderReorder
 *   - RecallDONE_WritebackSafety
 *)

EXTENDS ubcc_protocol, TLC

BaseInit == Init

(***************************************************************************)
(* Message pool                                                            *)
(***************************************************************************)

MsgKinds == {"Clear", "InvalidateAck", "RecallResp"}
CopySlots == {0, 1, 2}

PairSpace == {<<epoch, reqId>> : epoch \in (0 .. MaxEpoch), reqId \in (0 .. MaxEpoch)}

NoAck == [state |-> "G_I", owner |-> -1, sharers |-> {}, epoch |-> 0]
NoMsg == [kind |-> "Clear", node |-> -1, epoch |-> 0, reqId |-> 0, copy |-> 0]
NoDir == [state |-> "G_I", sharers |-> {}, owner |-> -1, dirty |-> FALSE, epoch |-> 0]

Ack(state, owner, sharers, epoch) ==
    [state |-> state, owner |-> owner, sharers |-> sharers, epoch |-> epoch]

DirView ==
    [state |-> committedState,
     sharers |-> committedSharers,
     owner |-> committedOwner,
     dirty |-> committedDirty,
     epoch |-> committedEpoch]

LiveClearAck ==
    Ack(ostIntendedState, ostIntendedOwner, ostIntendedSharers, ostReservedEpoch)

LiveClearMsg ==
    [kind |-> "Clear", node |-> ostRequester, epoch |-> ostReservedEpoch,
     reqId |-> ostReqId, copy |-> 0]

LiveRecallRespMsg ==
    [kind |-> "RecallResp", node |-> committedOwner, epoch |-> ostReservedEpoch,
     reqId |-> ostReqId, copy |-> 0]

LiveInvalidateAckMsg(node) ==
    [kind |-> "InvalidateAck", node |-> node, epoch |-> ostReservedEpoch,
     reqId |-> ostReqId, copy |-> 0]

WithinReplayWindow(epoch, reqId) ==
    tick >= clearAckTick[<<epoch, reqId>>] /\
    tick - clearAckTick[<<epoch, reqId>>] <= TombstoneWindow

ValidRecallResp(m) ==
    /\ ostOpType = "RECALL"
    /\ ostStage = "WAITING_TARGET_RESP"
    /\ m.node = committedOwner
    /\ m.epoch = ostReservedEpoch
    /\ m.reqId = ostReqId

ValidInvalidateAck(m) ==
    /\ m.node \in ostTargetMask
    /\ m.epoch = ostReservedEpoch
    /\ m.reqId = ostReqId
    /\ ((ostOpType = "INVALIDATE" /\ ostStage = "WAITING_ALL_ACKS") \/
        (ostOpType = "UPGRADE_PENDING" /\ ostStage = "WAITING_ALL_ACKS"))

ValidLiveClear(m) ==
    /\ ostOpType = "GRANT_HANDSHAKE"
    /\ ostStage = "WAITING_CLEAR"
    /\ m.node = ostRequester
    /\ m.epoch = ostReservedEpoch
    /\ m.reqId = ostReqId

ValidTombstoneReplay(m) ==
    /\ tombstone[m.epoch, m.reqId]
    /\ WithinReplayWindow(m.epoch, m.reqId)

VARIABLES
    messages,
    clearAckCache,
    clearAckTick,
    lastClearAck,
    lastReplayPair,
    lastRejectedMsg,
    rejectedDirSnapshot,
    recallDoneSnapshot,
    recallDoneActive,
    transportEvent

Init ==
    /\ BaseInit
    /\ messages = {}
    /\ clearAckCache = [p \in PairSpace |-> NoAck]
    /\ clearAckTick = [p \in PairSpace |-> 0]
    /\ lastClearAck = NoAck
    /\ lastReplayPair = <<0, 0>>
    /\ lastRejectedMsg = NoMsg
    /\ rejectedDirSnapshot = NoDir
    /\ recallDoneSnapshot = NoDir
    /\ recallDoneActive = FALSE
    /\ transportEvent = "INIT"

(***************************************************************************)
(* Lifting helper for unmodified base actions                              *)
(***************************************************************************)

LiftBase(pred, tag) ==
    /\ pred
    /\ UNCHANGED <<messages, clearAckCache, clearAckTick, lastClearAck,
                   lastReplayPair, lastRejectedMsg, rejectedDirSnapshot,
                   recallDoneSnapshot, recallDoneActive>>
    /\ transportEvent' = tag

(***************************************************************************)
(* Network enqueue / fault / delivery actions                              *)
(***************************************************************************)

EnqueueClear ==
    /\ ostOpType = "GRANT_HANDSHAKE"
    /\ ostStage = "WAITING_CLEAR"
    /\ LiveClearMsg \notin messages
    /\ messages' = messages \cup {LiveClearMsg}
    /\ UNCHANGED <<committedState, committedSharers, committedOwner,
                   committedDirty, committedEpoch,
                   ostOpType, ostStage, ostBaseEpoch, ostReservedEpoch,
                   ostReqId, ostRequester, ostTargetMask, ostAckMask,
                   ostIntendedState, ostIntendedOwner, ostIntendedSharers,
                   ostRecallDone, ostInvalidateDone, ostAccepted,
                   tombstone, commitLog, tick,
                   clearAckCache, clearAckTick, lastClearAck, lastReplayPair,
                   lastRejectedMsg, rejectedDirSnapshot,
                   recallDoneSnapshot, recallDoneActive>>
    /\ transportEvent' = "ENQ_CLEAR"

EnqueueRecallResp ==
    /\ ostOpType = "RECALL"
    /\ ostStage = "WAITING_TARGET_RESP"
    /\ committedOwner /= -1
    /\ LiveRecallRespMsg \notin messages
    /\ messages' = messages \cup {LiveRecallRespMsg}
    /\ UNCHANGED <<committedState, committedSharers, committedOwner,
                   committedDirty, committedEpoch,
                   ostOpType, ostStage, ostBaseEpoch, ostReservedEpoch,
                   ostReqId, ostRequester, ostTargetMask, ostAckMask,
                   ostIntendedState, ostIntendedOwner, ostIntendedSharers,
                   ostRecallDone, ostInvalidateDone, ostAccepted,
                   tombstone, commitLog, tick,
                   clearAckCache, clearAckTick, lastClearAck, lastReplayPair,
                   lastRejectedMsg, rejectedDirSnapshot,
                   recallDoneSnapshot, recallDoneActive>>
    /\ transportEvent' = "ENQ_RECALL"

EnqueueInvalidateAck(node) ==
    /\ ostOpType \in {"INVALIDATE", "UPGRADE_PENDING"}
    /\ ostStage = "WAITING_ALL_ACKS"
    /\ node \in ostTargetMask
    /\ LiveInvalidateAckMsg(node) \notin messages
    /\ messages' = messages \cup {LiveInvalidateAckMsg(node)}
    /\ UNCHANGED <<committedState, committedSharers, committedOwner,
                   committedDirty, committedEpoch,
                   ostOpType, ostStage, ostBaseEpoch, ostReservedEpoch,
                   ostReqId, ostRequester, ostTargetMask, ostAckMask,
                   ostIntendedState, ostIntendedOwner, ostIntendedSharers,
                   ostRecallDone, ostInvalidateDone, ostAccepted,
                   tombstone, commitLog, tick,
                   clearAckCache, clearAckTick, lastClearAck, lastReplayPair,
                   lastRejectedMsg, rejectedDirSnapshot,
                   recallDoneSnapshot, recallDoneActive>>
    /\ transportEvent' = "ENQ_INVACK"

DropMessage ==
    /\ \E m \in messages :
         /\ messages' = messages \ {m}
         /\ UNCHANGED <<committedState, committedSharers, committedOwner,
                        committedDirty, committedEpoch,
                        ostOpType, ostStage, ostBaseEpoch, ostReservedEpoch,
                        ostReqId, ostRequester, ostTargetMask, ostAckMask,
                        ostIntendedState, ostIntendedOwner, ostIntendedSharers,
                        ostRecallDone, ostInvalidateDone, ostAccepted,
                        tombstone, commitLog, tick,
                        clearAckCache, clearAckTick, lastClearAck,
                        lastReplayPair, lastRejectedMsg, rejectedDirSnapshot,
                        recallDoneSnapshot, recallDoneActive>>
         /\ transportEvent' = "DROP"

DuplicateMessage ==
    /\ \E m \in messages, copy \in CopySlots :
         LET dup == [m EXCEPT !.copy = copy]
         IN  /\ copy /= m.copy
             /\ dup \notin messages
             /\ messages' = messages \cup {dup}
             /\ UNCHANGED <<committedState, committedSharers, committedOwner,
                            committedDirty, committedEpoch,
                            ostOpType, ostStage, ostBaseEpoch, ostReservedEpoch,
                            ostReqId, ostRequester, ostTargetMask, ostAckMask,
                            ostIntendedState, ostIntendedOwner, ostIntendedSharers,
                            ostRecallDone, ostInvalidateDone, ostAccepted,
                            tombstone, commitLog, tick,
                            clearAckCache, clearAckTick, lastClearAck,
                            lastReplayPair, lastRejectedMsg, rejectedDirSnapshot,
                            recallDoneSnapshot, recallDoneActive>>
             /\ transportEvent' = "DUP"

ReorderMessages ==
    /\ messages /= {}
    /\ UNCHANGED <<committedState, committedSharers, committedOwner,
                   committedDirty, committedEpoch,
                   ostOpType, ostStage, ostBaseEpoch, ostReservedEpoch,
                   ostReqId, ostRequester, ostTargetMask, ostAckMask,
                   ostIntendedState, ostIntendedOwner, ostIntendedSharers,
                   ostRecallDone, ostInvalidateDone, ostAccepted,
                   tombstone, commitLog, tick,
                   messages, clearAckCache, clearAckTick, lastClearAck,
                   lastReplayPair, lastRejectedMsg, rejectedDirSnapshot,
                   recallDoneSnapshot, recallDoneActive>>
    /\ transportEvent' = "REORDER"

RejectStale(m) ==
    /\ messages' = messages \ {m}
    /\ UNCHANGED <<committedState, committedSharers, committedOwner,
                   committedDirty, committedEpoch,
                   ostOpType, ostStage, ostBaseEpoch, ostReservedEpoch,
                   ostReqId, ostRequester, ostTargetMask, ostAckMask,
                   ostIntendedState, ostIntendedOwner, ostIntendedSharers,
                   ostRecallDone, ostInvalidateDone, ostAccepted,
                   tombstone, commitLog, tick,
                   clearAckCache, clearAckTick, lastClearAck, lastReplayPair,
                   recallDoneSnapshot, recallDoneActive>>
    /\ lastRejectedMsg' = m
    /\ rejectedDirSnapshot' = DirView
    /\ transportEvent' = "STALE_REJECT"

DeliverRecallResp(m) ==
    /\ m \in messages
    /\ m.kind = "RecallResp"
    /\ IF ValidRecallResp(m)
       THEN
         /\ RecallResponseArrives
         /\ messages' = messages \ {m}
         /\ UNCHANGED <<clearAckCache, clearAckTick, lastClearAck,
                        lastReplayPair, lastRejectedMsg, rejectedDirSnapshot>>
         /\ recallDoneSnapshot' = DirView
         /\ recallDoneActive' = TRUE
         /\ transportEvent' = "DELIVER_RECALL"
       ELSE RejectStale(m)

DeliverInvalidateAck(m) ==
    /\ m \in messages
    /\ m.kind = "InvalidateAck"
    /\ IF ostOpType = "INVALIDATE" /\ ostStage = "WAITING_ALL_ACKS" /\ ValidInvalidateAck(m)
       THEN
         /\ InvalidationAckArrives(m.node)
         /\ messages' = messages \ {m}
         /\ UNCHANGED <<clearAckCache, clearAckTick, lastClearAck,
                        lastReplayPair, lastRejectedMsg, rejectedDirSnapshot,
                        recallDoneSnapshot, recallDoneActive>>
         /\ transportEvent' = "DELIVER_INVACK"
       ELSE IF ostOpType = "UPGRADE_PENDING" /\ ostStage = "WAITING_ALL_ACKS" /\ ValidInvalidateAck(m)
            THEN
              /\ UpgradeInvalidationAckArrives(m.node)
              /\ messages' = messages \ {m}
              /\ UNCHANGED <<clearAckCache, clearAckTick, lastClearAck,
                             lastReplayPair, lastRejectedMsg, rejectedDirSnapshot,
                             recallDoneSnapshot, recallDoneActive>>
              /\ transportEvent' = "DELIVER_INVACK"
            ELSE RejectStale(m)

DeliverClear(m) ==
    /\ m \in messages
    /\ m.kind = "Clear"
    /\ IF ValidLiveClear(m)
       THEN
         /\ ClearArrives(m.node)
         /\ messages' = messages \ {m}
         /\ clearAckCache' = [clearAckCache EXCEPT ![<<m.epoch, m.reqId>>] = LiveClearAck]
         /\ clearAckTick' = [clearAckTick EXCEPT ![<<m.epoch, m.reqId>>] = tick]
         /\ lastClearAck' = LiveClearAck
         /\ lastReplayPair' = <<m.epoch, m.reqId>>
         /\ UNCHANGED <<lastRejectedMsg, rejectedDirSnapshot, recallDoneSnapshot>>
         /\ recallDoneActive' = FALSE
         /\ transportEvent' = "DELIVER_CLEAR"
       ELSE IF ValidTombstoneReplay(m)
            THEN
              /\ DuplicateClearReplay(m.node, m.epoch, m.reqId)
              /\ messages' = messages \ {m}
              /\ UNCHANGED <<clearAckCache, clearAckTick, lastRejectedMsg,
                             rejectedDirSnapshot, recallDoneSnapshot,
                             recallDoneActive>>
              /\ lastClearAck' = clearAckCache[<<m.epoch, m.reqId>>]
              /\ lastReplayPair' = <<m.epoch, m.reqId>>
              /\ transportEvent' = "DUP_CLEAR_REPLAY"
            ELSE RejectStale(m)

WritebackOrEvictDuringRecallDone(req, op) ==
    /\ req \in Nodes
    /\ op \in {"WB", "EVICT"}
    /\ recallDoneActive
    /\ ostOpType = "GRANT_HANDSHAKE"
    /\ ostStage = "WAITING_CLEAR"
    /\ ostRecallDone
    /\ UNCHANGED <<committedState, committedSharers, committedOwner,
                   committedDirty, committedEpoch,
                   ostOpType, ostStage, ostBaseEpoch, ostReservedEpoch,
                   ostReqId, ostRequester, ostTargetMask, ostAckMask,
                   ostIntendedState, ostIntendedOwner, ostIntendedSharers,
                   ostRecallDone, ostInvalidateDone, ostAccepted,
                   tombstone, commitLog, tick,
                   messages, clearAckCache, clearAckTick, lastClearAck,
                   lastReplayPair, lastRejectedMsg, rejectedDirSnapshot,
                   recallDoneSnapshot, recallDoneActive>>
    /\ transportEvent' = "WB_EVICT_REJECT"

(***************************************************************************)
(* Next-state relation                                                     *)
(***************************************************************************)

Next ==
    \/ \E req \in Nodes, baseEpoch \in (0 .. MaxEpoch), reqId \in (0 .. MaxEpoch) :
         LiftBase(GrantSharedGI(req, baseEpoch, reqId), "BASE")
    \/ \E req \in Nodes, baseEpoch \in (0 .. MaxEpoch), reqId \in (0 .. MaxEpoch) :
         LiftBase(GrantExclusiveGI(req, baseEpoch, reqId), "BASE")
    \/ \E req \in Nodes, baseEpoch \in (0 .. MaxEpoch), reqId \in (0 .. MaxEpoch) :
         LiftBase(GrantModifiedGI(req, baseEpoch, reqId), "BASE")
    \/ \E req \in Nodes, baseEpoch \in (0 .. MaxEpoch), reqId \in (0 .. MaxEpoch) :
         LiftBase(GrantSharedGS(req, baseEpoch, reqId), "BASE")
    \/ \E req \in Nodes, baseEpoch \in (0 .. MaxEpoch), reqId \in (0 .. MaxEpoch) :
         LiftBase(InvalidateForUnique(req, baseEpoch, reqId), "BASE")
    \/ \E req \in Nodes, baseEpoch \in (0 .. MaxEpoch), reqId \in (0 .. MaxEpoch) :
         LiftBase(RecallForShared(req, baseEpoch, reqId), "BASE")
    \/ \E req \in Nodes, baseEpoch \in (0 .. MaxEpoch), reqId \in (0 .. MaxEpoch) :
         LiftBase(RecallForUnique(req, baseEpoch, reqId), "BASE")
    \/ \E req \in Nodes, baseEpoch \in (0 .. MaxEpoch), reqId \in (0 .. MaxEpoch) :
         LiftBase(SelfOwnerGrant(req, baseEpoch, reqId), "BASE")
    \/ \E req \in Nodes, baseEpoch \in (0 .. MaxEpoch), reqId \in (0 .. MaxEpoch) :
         LiftBase(UpgradeReqAccepted(req, baseEpoch, reqId), "BASE")
    \/ \E req \in Nodes : LiftBase(UpgradeDoneArrives(req), "BASE")
    \/ EnqueueClear
    \/ EnqueueRecallResp
    \/ \E node \in Nodes : EnqueueInvalidateAck(node)
    \/ \E m \in messages : DeliverRecallResp(m)
    \/ \E m \in messages : DeliverInvalidateAck(m)
    \/ \E m \in messages : DeliverClear(m)
    \/ DropMessage
    \/ DuplicateMessage
    \/ ReorderMessages
    \/ \E req \in Nodes, op \in {"WB", "EVICT"} : WritebackOrEvictDuringRecallDone(req, op)
    \/ LiftBase(TickAdvance, "TICK")

(***************************************************************************)
(* Recovery invariants                                                     *)
(***************************************************************************)

TombstoneReplayConsistency ==
    (transportEvent = "DUP_CLEAR_REPLAY") =>
        /\ tombstone[lastReplayPair[1], lastReplayPair[2]]
        /\ WithinReplayWindow(lastReplayPair[1], lastReplayPair[2])
        /\ lastClearAck = clearAckCache[lastReplayPair]

StaleRejectUnderReorder ==
    (transportEvent = "STALE_REJECT") =>
        /\ lastRejectedMsg.kind \in MsgKinds
        /\ ~ValidRecallResp(lastRejectedMsg)
        /\ ~ValidInvalidateAck(lastRejectedMsg)
        /\ ~ValidLiveClear(lastRejectedMsg)
        /\ ~ValidTombstoneReplay(lastRejectedMsg)
        /\ rejectedDirSnapshot = DirView

RecallDONE_WritebackSafety ==
    (transportEvent = "WB_EVICT_REJECT") =>
        /\ recallDoneActive
        /\ DirView = recallDoneSnapshot

RecoveryInvariants ==
    /\ NoDoubleCommit
    /\ EpochMonotonic
    /\ SharersCanonical
    /\ TombstoneReplayConsistency
    /\ StaleRejectUnderReorder
    /\ RecallDONE_WritebackSafety

Vars == <<committedState, committedSharers, committedOwner,
          committedDirty, committedEpoch,
          ostOpType, ostStage, ostBaseEpoch, ostReservedEpoch,
          ostReqId, ostRequester, ostTargetMask, ostAckMask,
          ostIntendedState, ostIntendedOwner, ostIntendedSharers,
          ostRecallDone, ostInvalidateDone, ostAccepted,
          tombstone, commitLog, tick,
          messages, clearAckCache, clearAckTick, lastClearAck,
          lastReplayPair, lastRejectedMsg, rejectedDirSnapshot,
          recallDoneSnapshot, recallDoneActive, transportEvent>>

Spec == Init /\ [][Next]_Vars

=============================================================================
