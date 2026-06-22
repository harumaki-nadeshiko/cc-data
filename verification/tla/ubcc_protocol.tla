---------------------------- MODULE ubcc_protocol ----------------------------
(**
 * Top-level TLA+ specification of the UBCC directory protocol (v4.0).
 *
 * Self-contained wrapper: imports ubcc_protocol_core and defines
 * Init = BaseInit, Next = BaseNext, Spec.
 *
 * Behaviorally identical to the pre-refactoring single-module version.
 *)

EXTENDS ubcc_protocol_core

(***************************************************************************)
(* Top-level definitions                                                    *)
(***************************************************************************)

Init == BaseInit

Next == BaseNext

(***************************************************************************)
(* Specification                                                            *)
(***************************************************************************)

Spec == Init /\ [][Next]_<<committedState, committedSharers, committedOwner,
                           committedDirty, committedEpoch,
                           ostOpType, ostStage, ostBaseEpoch,
                           ostReservedEpoch, ostReqId, ostRequester,
                           ostTargetMask, ostAckMask, ostIntendedState,
                           ostIntendedOwner, ostIntendedSharers,
                           ostRecallDone, ostInvalidateDone, ostAccepted,
                           tombstone, commitLog, tick>>

=============================================================================
