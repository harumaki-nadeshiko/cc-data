--------------------- MODULE ubcc_tc224_waiter_retirement ---------------------
EXTENDS Integers, Naturals, Sequences, TLC

(***************************************************************************)
(* Focused model for the TC224 commit-time resident-waiter fix.            *)
(*                                                                         *)
(* The production Clear path commits the GRANT_HANDSHAKE, then retires     *)
(* only the stale Read waiter matching                                     *)
(*   (PA, requester node, requester socket, reqId).                        *)
(* Legacy reqId=0 additionally requires the waiter's epoch to equal the    *)
(* outstanding base epoch. Writeback/Upgrade/Evict and every non-matching  *)
(* waiter must survive. An empty queue is erased; replay must reacquire it. *)
(***************************************************************************)

CONSTANTS Nodes, Sockets, ReqIds, Epochs, MaxWaiters

ASSUME Nodes # {}
ASSUME Sockets # {}
ASSUME 0 \in ReqIds
ASSUME Epochs # {}
ASSUME MaxWaiters \in Nat
ASSUME MaxWaiters > 0

OpKinds == {"Read", "Writeback", "Upgrade", "Evict"}

Waiter(node, socket, reqId, epoch, opKind) ==
    [node |-> node, socket |-> socket, reqId |-> reqId,
     epoch |-> epoch, opKind |-> opKind]

MatchesCommitted(w, o) ==
    /\ w.opKind = "Read"
    /\ w.node = o.node
    /\ w.socket = o.socket
    /\ w.reqId = o.reqId
    /\ (o.reqId # 0 \/ w.epoch = o.baseEpoch)

RetainAfterCommit(q, o) ==
    SelectSeq(q, LAMBDA w : ~MatchesCommitted(w, o))

EmptyOutstanding ==
    [valid |-> FALSE, node |-> CHOOSE n \in Nodes : TRUE,
     socket |-> CHOOSE s \in Sockets : TRUE,
     reqId |-> 0, baseEpoch |-> CHOOSE e \in Epochs : TRUE]

VARIABLES waiters, waiterHistory, outstanding, committed, replayActive,
          queuePresent

Vars == <<waiters, waiterHistory, outstanding, committed, replayActive,
          queuePresent>>

Init ==
    /\ waiters = <<>>
    /\ waiterHistory = <<>>
    /\ outstanding = EmptyOutstanding
    /\ committed = <<>>
    /\ replayActive = FALSE
    /\ queuePresent = FALSE

StartGrant(node, socket, reqId, baseEpoch) ==
    /\ ~outstanding.valid
    /\ Len(committed) = 0
    /\ outstanding' = [valid |-> TRUE, node |-> node, socket |-> socket,
                        reqId |-> reqId, baseEpoch |-> baseEpoch]
    /\ UNCHANGED <<waiters, waiterHistory, committed, replayActive,
                    queuePresent>>

Enqueue(node, socket, reqId, epoch, opKind) ==
    /\ Len(waiters) < MaxWaiters
    /\ Len(committed) = 0
    /\ LET w == Waiter(node, socket, reqId, epoch, opKind) IN
       /\ \A i \in 1..Len(committed) : ~MatchesCommitted(w, committed[i])
       /\ waiters' = Append(waiters, w)
       /\ waiterHistory' = Append(waiterHistory, w)
    /\ queuePresent' = TRUE
    /\ UNCHANGED <<outstanding, committed, replayActive>>

ClearCommit ==
    /\ outstanding.valid
    /\ LET survivor == RetainAfterCommit(waiters, outstanding) IN
       /\ waiters' = survivor
       /\ queuePresent' = (Len(survivor) # 0)
    /\ committed' = Append(committed, outstanding)
    /\ outstanding' = EmptyOutstanding
    /\ UNCHANGED <<waiterHistory, replayActive>>

BeginReplay ==
    /\ queuePresent
    /\ ~replayActive
    /\ replayActive' = TRUE
    /\ UNCHANGED <<waiters, waiterHistory, outstanding, committed,
                    queuePresent>>

(* A synchronous Clear may erase the queue while replay is on the stack.    *)
(* EndReplay models iterator reacquisition: it does not assume the queue is  *)
(* still present and never dereferences erased queue state.                  *)
EndReplay ==
    /\ replayActive
    /\ replayActive' = FALSE
    /\ queuePresent' = (Len(waiters) # 0)
    /\ UNCHANGED <<waiters, waiterHistory, outstanding, committed>>

Done ==
    /\ Len(committed) = 1
    /\ ~replayActive
    /\ UNCHANGED Vars

Next ==
    \/ \E n \in Nodes, s \in Sockets, r \in ReqIds, e \in Epochs :
          StartGrant(n, s, r, e)
    \/ \E n \in Nodes, s \in Sockets, r \in ReqIds,
          e \in Epochs, k \in OpKinds : Enqueue(n, s, r, e, k)
    \/ ClearCommit
    \/ BeginReplay
    \/ EndReplay
    \/ Done

Spec == Init /\ [][Next]_Vars

WaitersWellTyped ==
    /\ Len(waiters) <= MaxWaiters
    /\ \A i \in 1..Len(waiters) :
          /\ waiters[i].node \in Nodes
          /\ waiters[i].socket \in Sockets
          /\ waiters[i].reqId \in ReqIds
          /\ waiters[i].epoch \in Epochs
          /\ waiters[i].opKind \in OpKinds

NoCommittedReadWaiter ==
    \A i \in 1..Len(waiters), j \in 1..Len(committed) :
        ~MatchesCommitted(waiters[i], committed[j])

NonMatchingWaitersPreserved ==
    \A i \in 1..Len(waiterHistory) :
        (\A j \in 1..Len(committed) :
            ~MatchesCommitted(waiterHistory[i], committed[j])) =>
        (\E k \in 1..Len(waiters) : waiters[k] = waiterHistory[i])

QueuePresenceConsistent == queuePresent = (Len(waiters) # 0)

ReplaySafeAfterErase == replayActive /\ ~queuePresent => Len(waiters) = 0

=============================================================================
