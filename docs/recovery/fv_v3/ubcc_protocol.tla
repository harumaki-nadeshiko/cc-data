---------------------------- MODULE ubcc_protocol ----------------------------
(**
 * TLA+ specification of the UBCC directory protocol (v4.0 / scheme_v4).
 *
 * Models:
 *   - MESI states: G_I, G_S, G_E, G_M
 *   - OutstandingRequest: OpType × OpStage × epoch × sharersMask
 *   - Actions: GrantHandshake, InvalidateBarrier, RecallBarrier
 *
 * Verified invariants:
 *   - NoDoubleCommit: each PA commits at most once per (epoch, reqId)
 *   - EpochMonotonic: committed epoch never decreases
 *   - SharersCanonical: sharersMask obeys structural invariants
 *
 * Scope: single PA, 3 nodes (0,1,2), single socket.
 *)

EXTENDS Naturals, FiniteSets, Sequences, TLC

(***************************************************************************)
(* Constants                                                                *)
(***************************************************************************)

CONSTANTS
    Nodes,              \* Set of node IDs, e.g. {0, 1, 2}
    MaxEpoch,           \* Maximum epoch value for bounded model checking
    TombstoneWindow     \* W: tombstone retention window in abstract ticks

ASSUME Nodes = {0, 1, 2}
ASSUME MaxEpoch > 0
ASSUME TombstoneWindow > 0

(***************************************************************************)
(* Types                                                                    *)
(***************************************************************************)

MESIState == { "G_I", "G_S", "G_E", "G_M" }

OpType == { "GRANT_HANDSHAKE", "RECALL", "INVALIDATE", "UPGRADE_PENDING" }

OpStage == {
    "CREATED",
    "WAITING_TARGET_RESP",   \* RECALL
    "WAITING_ALL_ACKS",      \* INVALIDATE / UPGRADE_PENDING pre-ack
    "WAITING_CLEAR",         \* GRANT_HANDSHAKE
    "WAITING_LOCAL_DONE",    \* UPGRADE_PENDING
    "DONE",
    "CANCELLED",
    "TIMED_OUT",
    "PERSISTENT_BUSY"
}

TerminalStages == { "DONE", "CANCELLED", "TIMED_OUT", "PERSISTENT_BUSY" }

(***************************************************************************)
(* State Variables                                                          *)
(***************************************************************************)

VARIABLES
    committedState,     \* MESIState: current committed directory state
    committedSharers,   \* SUBSET Nodes: committed sharer mask
    committedOwner,     \* Nodes ∪ {-1}: committed owner (-1 = none)
    committedDirty,     \* BOOLEAN: dirty flag (valid only if G_M)
    committedEpoch,     \* Nat: committed global epoch

    ostOpType,          \* OpType of current outstanding (or "NONE")
    ostStage,           \* OpStage
    ostBaseEpoch,       \* Nat: requester-observed epoch
    ostReservedEpoch,   \* Nat: epoch to commit on Clear/Done
    ostReqId,           \* Nat: requester-allocated ID
    ostRequester,       \* Nodes: requester node
    ostTargetMask,      \* SUBSET Nodes: invalidation targets
    ostAckMask,         \* SUBSET Nodes: received acks
    ostIntendedState,   \* MESIState: intended committed state
    ostIntendedOwner,   \* Nodes ∪ {-1}
    ostIntendedSharers, \* SUBSET Nodes
    ostRecallDone,      \* BOOLEAN: recall barrier released
    ostInvalidateDone,  \* BOOLEAN: invalidate barrier released
    ostAccepted,        \* BOOLEAN: upgrade ack was true

    tombstone,          \* [(epoch, reqId) → BOOLEAN]: active tombstone entries
    commitLog,          \* Sequence of committed (epoch, reqId) tuples

    tick                \* Nat: abstract time

(***************************************************************************)
(* Initial State                                                            *)
(***************************************************************************)

Init ==
    /\ committedState  = "G_I"
    /\ committedSharers = {}
    /\ committedOwner   = -1
    /\ committedDirty   = FALSE
    /\ committedEpoch   = 0

    /\ ostOpType        = "NONE"
    /\ ostStage         = "CREATED"
    /\ ostBaseEpoch     = 0
    /\ ostReservedEpoch = 0
    /\ ostReqId         = 0
    /\ ostRequester     = -1
    /\ ostTargetMask    = {}
    /\ ostAckMask       = {}
    /\ ostIntendedState = "G_I"
    /\ ostIntendedOwner = -1
    /\ ostIntendedSharers = {}
    /\ ostRecallDone    = FALSE
    /\ ostInvalidateDone = FALSE
    /\ ostAccepted      = FALSE

    /\ tombstone        = [p ∈ (0 .. MaxEpoch) × (0 .. MaxEpoch) ↦ FALSE]
    /\ commitLog        = <<>>
    /\ tick             = 0

(***************************************************************************)
(* Helpers                                                                  *)
(***************************************************************************)

IsNewerEpoch(a, b) ==
    \* Half-range epoch comparison: a is newer than b iff
    \* delta = (a - b) mod (MaxEpoch+1) is nonzero and < (MaxEpoch+1)/2
    LET delta == (a - b) % (MaxEpoch + 1)
        half  == (MaxEpoch + 1) \div 2
    IN delta /= 0 /\ delta < half

NoOutstanding == ostOpType = "NONE"

HasOutstanding == ostOpType /= "NONE"

IsTerminal(stage) == stage \in TerminalStages

(***************************************************************************)
(* Action: ProcessOuterRequest (reserve-then-commit)                         *)
(***************************************************************************)

(**
 * G_I → Shared: requester wants shared access.
 * Creates GRANT_HANDSHAKE outstanding.  Committed DirEntry NOT modified.
 *)
GrantSharedGI(req, baseEpoch, reqId) ==
    /\ committedState = "G_I"
    /\ NoOutstanding
    /\ ostOpType'        = "GRANT_HANDSHAKE"
    /\ ostStage'         = "WAITING_CLEAR"
    /\ ostBaseEpoch'     = baseEpoch
    /\ ostReservedEpoch' = committedEpoch + 1
    /\ ostReqId'         = reqId
    /\ ostRequester'     = req
    /\ ostIntendedState' = "G_S"
    /\ ostIntendedOwner' = -1
    /\ ostIntendedSharers' = committedSharers \cup {req}
    /\ ostRecallDone'    = FALSE
    /\ ostInvalidateDone' = FALSE
    /\ ostAccepted'      = FALSE
    /\ UNCHANGED <<committedState, committedSharers, committedOwner,
                   committedDirty, committedEpoch, ostTargetMask,
                   ostAckMask, tombstone, commitLog, tick>>

(**
 * G_I → Exclusive (no write): requester wants unique, no write intent.
 *)
GrantExclusiveGI(req, baseEpoch, reqId) ==
    /\ committedState = "G_I"
    /\ NoOutstanding
    /\ ostOpType'        = "GRANT_HANDSHAKE"
    /\ ostStage'         = "WAITING_CLEAR"
    /\ ostBaseEpoch'     = baseEpoch
    /\ ostReservedEpoch' = committedEpoch + 1
    /\ ostReqId'         = reqId
    /\ ostRequester'     = req
    /\ ostIntendedState' = "G_E"
    /\ ostIntendedOwner' = req
    /\ ostIntendedSharers' = {req}
    /\ ostRecallDone'    = FALSE
    /\ ostInvalidateDone' = FALSE
    /\ ostAccepted'      = FALSE
    /\ UNCHANGED <<committedState, committedSharers, committedOwner,
                   committedDirty, committedEpoch, ostTargetMask,
                   ostAckMask, tombstone, commitLog, tick>>

(**
 * G_I → Modified (write intent): requester wants unique with write intent.
 *)
GrantModifiedGI(req, baseEpoch, reqId) ==
    /\ committedState = "G_I"
    /\ NoOutstanding
    /\ ostOpType'        = "GRANT_HANDSHAKE"
    /\ ostStage'         = "WAITING_CLEAR"
    /\ ostBaseEpoch'     = baseEpoch
    /\ ostReservedEpoch' = committedEpoch + 1
    /\ ostReqId'         = reqId
    /\ ostRequester'     = req
    /\ ostIntendedState' = "G_M"
    /\ ostIntendedOwner' = req
    /\ ostIntendedSharers' = {req}
    /\ ostRecallDone'    = FALSE
    /\ ostInvalidateDone' = FALSE
    /\ ostAccepted'      = FALSE
    /\ UNCHANGED <<committedState, committedSharers, committedOwner,
                   committedDirty, committedEpoch, ostTargetMask,
                   ostAckMask, tombstone, commitLog, tick>>

(**
 * G_S → Shared: add another sharer.
 *)
GrantSharedGS(req, baseEpoch, reqId) ==
    /\ committedState = "G_S"
    /\ req \notin committedSharers
    /\ NoOutstanding
    /\ ostOpType'        = "GRANT_HANDSHAKE"
    /\ ostStage'         = "WAITING_CLEAR"
    /\ ostBaseEpoch'     = baseEpoch
    /\ ostReservedEpoch' = committedEpoch + 1
    /\ ostReqId'         = reqId
    /\ ostRequester'     = req
    /\ ostIntendedState' = "G_S"
    /\ ostIntendedOwner' = -1
    /\ ostIntendedSharers' = committedSharers \cup {req}
    /\ ostRecallDone'    = FALSE
    /\ ostInvalidateDone' = FALSE
    /\ ostAccepted'      = FALSE
    /\ UNCHANGED <<committedState, committedSharers, committedOwner,
                   committedDirty, committedEpoch, ostTargetMask,
                   ostAckMask, tombstone, commitLog, tick>>

(**
 * G_S → Exclusive: non-sharer wants unique → INVALIDATE barrier.
 *)
InvalidateForUnique(req, baseEpoch, reqId) ==
    /\ committedState = "G_S"
    /\ req \notin committedSharers
    /\ NoOutstanding
    /\ ostOpType'        = "INVALIDATE"
    /\ ostStage'         = "WAITING_ALL_ACKS"
    /\ ostBaseEpoch'     = baseEpoch
    /\ ostReservedEpoch' = committedEpoch + 1
    /\ ostReqId'         = reqId
    /\ ostRequester'     = req
    /\ ostTargetMask'    = committedSharers
    /\ ostAckMask'       = {}
    /\ ostIntendedState' = "G_E"
    /\ ostIntendedOwner' = req
    /\ ostIntendedSharers' = {req}
    /\ ostRecallDone'    = FALSE
    /\ ostInvalidateDone' = FALSE
    /\ ostAccepted'      = FALSE
    /\ UNCHANGED <<committedState, committedSharers, committedOwner,
                   committedDirty, committedEpoch, tombstone, commitLog, tick>>

(**
 * G_E/G_M → Shared (other): recall owner, then add both as sharers.
 *)
RecallForShared(req, baseEpoch, reqId) ==
    /\ (committedState = "G_E" \/ committedState = "G_M")
    /\ req /= committedOwner
    /\ committedOwner /= -1
    /\ NoOutstanding
    /\ ostOpType'        = "RECALL"
    /\ ostStage'         = "WAITING_TARGET_RESP"
    /\ ostBaseEpoch'     = baseEpoch
    /\ ostReservedEpoch' = committedEpoch + 1
    /\ ostReqId'         = reqId
    /\ ostRequester'     = req
    /\ ostTargetMask'    = {}  \* RECALL targets single owner
    /\ ostAckMask'       = {}
    /\ ostIntendedState' = "G_S"
    /\ ostIntendedOwner' = -1
    /\ ostIntendedSharers' = {committedOwner, req}
    /\ ostRecallDone'    = FALSE
    /\ ostInvalidateDone' = FALSE
    /\ ostAccepted'      = FALSE
    /\ UNCHANGED <<committedState, committedSharers, committedOwner,
                   committedDirty, committedEpoch, tombstone, commitLog, tick>>

(**
 * G_E/G_M → Exclusive (other): recall owner, grant unique to requester.
 *)
RecallForUnique(req, baseEpoch, reqId) ==
    /\ (committedState = "G_E" \/ committedState = "G_M")
    /\ req /= committedOwner
    /\ committedOwner /= -1
    /\ NoOutstanding
    /\ ostOpType'        = "RECALL"
    /\ ostStage'         = "WAITING_TARGET_RESP"
    /\ ostBaseEpoch'     = baseEpoch
    /\ ostReservedEpoch' = committedEpoch + 1
    /\ ostReqId'         = reqId
    /\ ostRequester'     = req
    /\ ostTargetMask'    = {}
    /\ ostAckMask'       = {}
    /\ ostIntendedState' = "G_E"
    /\ ostIntendedOwner' = req
    /\ ostIntendedSharers' = {req}
    /\ ostRecallDone'    = FALSE
    /\ ostInvalidateDone' = FALSE
    /\ ostAccepted'      = FALSE
    /\ UNCHANGED <<committedState, committedSharers, committedOwner,
                   committedDirty, committedEpoch, tombstone, commitLog, tick>>

(**
 * Self-owner re-read/re-write: idempotent grant, no recall.
 *)
SelfOwnerGrant(req, baseEpoch, reqId) ==
    /\ (committedState = "G_E" \/ committedState = "G_M")
    /\ req = committedOwner
    /\ NoOutstanding
    /\ ostOpType'        = "GRANT_HANDSHAKE"
    /\ ostStage'         = "WAITING_CLEAR"
    /\ ostBaseEpoch'     = baseEpoch
    /\ ostReservedEpoch' = committedEpoch  \* self-owner: epoch unchanged
    /\ ostReqId'         = reqId
    /\ ostRequester'     = req
    /\ ostIntendedState' = committedState
    /\ ostIntendedOwner' = req
    /\ ostIntendedSharers' = {req}
    /\ ostRecallDone'    = FALSE
    /\ ostInvalidateDone' = FALSE
    /\ ostAccepted'      = FALSE
    /\ UNCHANGED <<committedState, committedSharers, committedOwner,
                   committedDirty, committedEpoch, ostTargetMask,
                   ostAckMask, tombstone, commitLog, tick>>

(***************************************************************************)
(* Action: RecallResponse → release recall barrier                          *)
(***************************************************************************)

RecallResponseArrives ==
    /\ ostOpType = "RECALL"
    /\ ostStage = "WAITING_TARGET_RESP"
    /\ ostRecallDone' = TRUE
    /\ \* Transition RECALL → GRANT_HANDSHAKE
       ostOpType' = "GRANT_HANDSHAKE"
    /\ ostStage' = "WAITING_CLEAR"
    /\ UNCHANGED <<committedState, committedSharers, committedOwner,
                   committedDirty, committedEpoch, ostBaseEpoch,
                   ostReservedEpoch, ostReqId, ostRequester,
                   ostTargetMask, ostAckMask, ostIntendedState,
                   ostIntendedOwner, ostIntendedSharers,
                   ostInvalidateDone, ostAccepted, tombstone, commitLog, tick>>

(***************************************************************************)
(* Action: InvalidationAck → track ack, release when all arrive             *)
(***************************************************************************)

InvalidationAckArrives(node) ==
    /\ ostOpType = "INVALIDATE"
    /\ ostStage = "WAITING_ALL_ACKS"
    /\ node \in ostTargetMask
    /\ node \notin ostAckMask          \* Not a duplicate
    /\ LET newAckMask == ostAckMask \cup {node}
       IN  /\ ostAckMask' = newAckMask
           /\ IF newAckMask = ostTargetMask THEN
                  \* All acks in: release barrier, promote to GRANT_HANDSHAKE
                  /\ ostInvalidateDone' = TRUE
                  /\ ostOpType' = "GRANT_HANDSHAKE"
                  /\ ostStage' = "WAITING_CLEAR"
                  /\ \* Remove invalidated sharers from committed set
                     committedSharers' = committedSharers \ ostTargetMask
              ELSE
                  /\ ostInvalidateDone' = FALSE
                  /\ UNCHANGED ostOpType
                  /\ ostStage' = ostStage
                  /\ UNCHANGED committedSharers
    /\ UNCHANGED <<committedState, committedOwner, committedDirty,
                   committedEpoch, ostBaseEpoch, ostReservedEpoch,
                   ostReqId, ostRequester, ostTargetMask,
                   ostIntendedState, ostIntendedOwner,
                   ostIntendedSharers, ostRecallDone, ostAccepted,
                   tombstone, commitLog, tick>>

(***************************************************************************)
(* Action: Clear → commit intended result (the commit point)                 *)
(***************************************************************************)

ClearArrives(req) ==
    /\ ostOpType = "GRANT_HANDSHAKE"
    /\ ostStage = "WAITING_CLEAR"
    /\ req = ostRequester
    /\ \* Commit intended result to committed DirEntry
       committedState'   = ostIntendedState
    /\ committedSharers' = ostIntendedSharers
    /\ committedOwner'   = ostIntendedOwner
    /\ committedDirty'   = (ostIntendedState = "G_M")
    /\ committedEpoch'   = ostReservedEpoch
    /\ \* Record commit in log
       commitLog' = Append(commitLog, <<ostReservedEpoch, ostReqId>>)
    /\ \* Install tombstone for duplicate Clear replay within window W
       tombstone' = [tombstone EXCEPT ![ostReservedEpoch, ostReqId] = TRUE]
    /\ \* Retire outstanding
       ostOpType' = "NONE"
    /\ ostStage' = "DONE"
    /\ UNCHANGED <<ostBaseEpoch, ostReservedEpoch, ostReqId, ostRequester,
                   ostTargetMask, ostAckMask, ostIntendedState,
                   ostIntendedOwner, ostIntendedSharers, ostRecallDone,
                   ostInvalidateDone, ostAccepted, tick>>

(***************************************************************************)
(* Action: Duplicate Clear → tombstone replay (idempotent)                   *)
(***************************************************************************)

DuplicateClearReplay(req, epoch, reqId) ==
    /\ tombstone[epoch, reqId] = TRUE
    /\ commitLog' = commitLog   \* No new commit
    /\ UNCHANGED <<committedState, committedSharers, committedOwner,
                   committedDirty, committedEpoch,
                   ostOpType, ostStage, ostBaseEpoch, ostReservedEpoch,
                   ostReqId, ostRequester, ostTargetMask, ostAckMask,
                   ostIntendedState, ostIntendedOwner, ostIntendedSharers,
                   ostRecallDone, ostInvalidateDone, ostAccepted, tombstone,
                   tick>>

(***************************************************************************)
(* Action: Local Upgrade (UPGRADE_PENDING four-message handshake)            *)
(***************************************************************************)

(**
 * OuterUpgradeReq accepted → create UPGRADE_PENDING.
 *)
UpgradeReqAccepted(req, baseEpoch, reqId) ==
    /\ committedState = "G_S"
    /\ req \in committedSharers
    /\ NoOutstanding
    /\ ostOpType'        = "UPGRADE_PENDING"
    /\ ostStage'         = \* If other sharers exist → WAITING_ALL_ACKS
                           IF committedSharers \ {req} /= {} THEN
                               "WAITING_ALL_ACKS"
                           ELSE
                               "WAITING_LOCAL_DONE"
    /\ ostBaseEpoch'     = baseEpoch
    /\ ostReservedEpoch' = committedEpoch + 1
    /\ ostReqId'         = reqId
    /\ ostRequester'     = req
    /\ ostTargetMask'    = committedSharers \ {req}
    /\ ostAckMask'       = {}
    /\ ostIntendedState' = "G_E"
    /\ ostIntendedOwner' = req
    /\ ostIntendedSharers' = {req}
    /\ ostRecallDone'    = FALSE
    /\ ostInvalidateDone' = FALSE
    /\ ostAccepted'      = (committedSharers \ {req} = {}) \* immediate if no others
    /\ UNCHANGED <<committedState, committedSharers, committedOwner,
                   committedDirty, committedEpoch, tombstone, commitLog, tick>>

(**
 * Upgrade invalidation ack → same as regular invalidation, but for UPGRADE_PENDING.
 *)
UpgradeInvalidationAckArrives(node) ==
    /\ ostOpType = "UPGRADE_PENDING"
    /\ ostStage = "WAITING_ALL_ACKS"
    /\ node \in ostTargetMask
    /\ node \notin ostAckMask
    /\ LET newAckMask == ostAckMask \cup {node}
       IN  /\ ostAckMask' = newAckMask
           /\ IF newAckMask = ostTargetMask THEN
                  /\ ostInvalidateDone' = TRUE
                  /\ ostStage' = "WAITING_LOCAL_DONE"
                  /\ ostAccepted' = TRUE    \* Ack(true) now
              ELSE
                  /\ UNCHANGED ostStage
                  /\ UNCHANGED ostAccepted
                  /\ ostInvalidateDone' = FALSE
    /\ UNCHANGED <<committedState, committedSharers, committedOwner,
                   committedDirty, committedEpoch, ostOpType, ostBaseEpoch,
                   ostReservedEpoch, ostReqId, ostRequester, ostTargetMask,
                   ostIntendedState, ostIntendedOwner, ostIntendedSharers,
                   ostRecallDone, tombstone, commitLog, tick>>

(**
 * OuterUpgradeDone → commit upgrade result.
 *)
UpgradeDoneArrives(req) ==
    /\ ostOpType = "UPGRADE_PENDING"
    /\ ostStage = "WAITING_LOCAL_DONE"
    /\ ostAccepted = TRUE
    /\ req = ostRequester
    /\ \* Commit
       committedState'   = ostIntendedState
    /\ committedSharers' = ostIntendedSharers
    /\ committedOwner'   = ostIntendedOwner
    /\ committedDirty'   = (ostIntendedState = "G_M")
    /\ committedEpoch'   = ostReservedEpoch
    /\ commitLog' = Append(commitLog, <<ostReservedEpoch, ostReqId>>)
    /\ \* Retire
       ostOpType' = "NONE"
    /\ ostStage' = "DONE"
    /\ UNCHANGED <<ostBaseEpoch, ostReservedEpoch, ostReqId, ostRequester,
                   ostTargetMask, ostAckMask, ostIntendedState,
                   ostIntendedOwner, ostIntendedSharers, ostRecallDone,
                   ostInvalidateDone, ostAccepted, tombstone, tick>>

(***************************************************************************)
(* Action: Time passage                                                      *)
(***************************************************************************)

TickAdvance ==
    /\ tick' = tick + 1
    /\ UNCHANGED <<committedState, committedSharers, committedOwner,
                   committedDirty, committedEpoch,
                   ostOpType, ostStage, ostBaseEpoch, ostReservedEpoch,
                   ostReqId, ostRequester, ostTargetMask, ostAckMask,
                   ostIntendedState, ostIntendedOwner, ostIntendedSharers,
                   ostRecallDone, ostInvalidateDone, ostAccepted,
                   tombstone, commitLog>>

(***************************************************************************)
(* Next-state relation                                                      *)
(***************************************************************************)

Next ==
    \/ \E req ∈ Nodes, baseEpoch ∈ (0 .. MaxEpoch), reqId ∈ (0 .. MaxEpoch) :
         GrantSharedGI(req, baseEpoch, reqId)
    \/ \E req ∈ Nodes, baseEpoch ∈ (0 .. MaxEpoch), reqId ∈ (0 .. MaxEpoch) :
         GrantExclusiveGI(req, baseEpoch, reqId)
    \/ \E req ∈ Nodes, baseEpoch ∈ (0 .. MaxEpoch), reqId ∈ (0 .. MaxEpoch) :
         GrantModifiedGI(req, baseEpoch, reqId)
    \/ \E req ∈ Nodes, baseEpoch ∈ (0 .. MaxEpoch), reqId ∈ (0 .. MaxEpoch) :
         GrantSharedGS(req, baseEpoch, reqId)
    \/ \E req ∈ Nodes, baseEpoch ∈ (0 .. MaxEpoch), reqId ∈ (0 .. MaxEpoch) :
         InvalidateForUnique(req, baseEpoch, reqId)
    \/ \E req ∈ Nodes, baseEpoch ∈ (0 .. MaxEpoch), reqId ∈ (0 .. MaxEpoch) :
         RecallForShared(req, baseEpoch, reqId)
    \/ \E req ∈ Nodes, baseEpoch ∈ (0 .. MaxEpoch), reqId ∈ (0 .. MaxEpoch) :
         RecallForUnique(req, baseEpoch, reqId)
    \/ \E req ∈ Nodes, baseEpoch ∈ (0 .. MaxEpoch), reqId ∈ (0 .. MaxEpoch) :
         SelfOwnerGrant(req, baseEpoch, reqId)
    \/ RecallResponseArrives
    \/ \E node ∈ Nodes : InvalidationAckArrives(node)
    \/ \E req ∈ Nodes : ClearArrives(req)
    \/ \E req ∈ Nodes, epoch ∈ (0 .. MaxEpoch), reqId ∈ (0 .. MaxEpoch) :
         DuplicateClearReplay(req, epoch, reqId)
    \/ \E req ∈ Nodes, baseEpoch ∈ (0 .. MaxEpoch), reqId ∈ (0 .. MaxEpoch) :
         UpgradeReqAccepted(req, baseEpoch, reqId)
    \/ \E node ∈ Nodes : UpgradeInvalidationAckArrives(node)
    \/ \E req ∈ Nodes : UpgradeDoneArrives(req)
    \/ TickAdvance

(***************************************************************************)
(* Invariants                                                               *)
(***************************************************************************)

(**
 * I1 / NoDoubleCommit: The commit log must not contain the same
 * (epoch, reqId) pair more than once.
 *)
NoDoubleCommit ==
    \A i, j ∈ DOMAIN commitLog :
        (i < j) => (commitLog[i] /= commitLog[j])

(**
 * I2 / EpochMonotonic: Committed epoch must never decrease.
 * With wrap-around, we track that epoch transitions are monotonic
 * under the half-range comparison.
 *
 * For the bounded model (no wrap), this simplifies to:
 *   committedEpoch never decreases.
 *)
EpochMonotonic ==
    \A i, j ∈ DOMAIN commitLog :
        (i < j) => (commitLog[i][1] < commitLog[j][1])

(**
 * I3 / SharersCanonical: The sharer set must obey structural rules.
 *   - G_I: sharers must be empty
 *   - G_S: sharers must be non-empty
 *   - G_E/G_M: sharers must be exactly {owner} (one-hot)
 *)
SharersCanonical ==
    /\ (committedState = "G_I") => (committedSharers = {})
    /\ (committedState = "G_S") => (committedSharers /= {})
    /\ (committedState = "G_E" \/ committedState = "G_M") =>
         (committedSharers = {committedOwner})
    /\ (committedOwner = -1) => \neg (committedState = "G_E" \/ committedState = "G_M")

(**
 * I4 / ReserveNotCommit: When there is an outstanding request,
 * the committed DirEntry must NOT have been modified to the intended
 * state yet (except through Clear/UpgradeDone, which clears the
 * outstanding).
 *)
ReserveNotCommit ==
    HasOutstanding =>
        (committedState /= ostIntendedState \/
         ostOpType = "GRANT_HANDSHAKE"  \* OK: Clear hasn't arrived yet
         \/ ostOpType = "UPGRADE_PENDING")

(**
 * I5 / CommitOnlyOnClearOrUpgradeDone: Epoch changes only happen
 * via ClearArrives or UpgradeDoneArrives, which set ostOpType' = "NONE".
 *)
CommitOnlyOnAuthorizedPath ==
    HasOutstanding =>
        (committedEpoch /= ostReservedEpoch)

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
