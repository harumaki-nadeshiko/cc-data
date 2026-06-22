--------------------------- MODULE ep_intra_node_dual ---------------------------
EXTENDS ep_intra_node, FiniteSets

(***************************************************************************)
(* Dual-socket abstraction reusing RNF/HNF state vocab from ep_intra_node. *)
(* Each socket has two CPUs; cross-socket safety is reduced to permission   *)
(* ownership and invalidation consistency.                                  *)
(***************************************************************************)

CONSTANT Sockets

ASSUME Sockets = {0, 1}

LocalCPUs == {0, 1}
NoneNode == -1
DualTickMax == MaxTxn + 8

VARIABLES socketRnf, socketBusy, ownerSocket, dtick

DualVars == <<socketRnf, socketBusy, ownerSocket, dtick>>

DualInit ==
    /\ socketRnf = [s \in Sockets |-> [c \in LocalCPUs |-> "IDLE"]]
    /\ socketBusy = [s \in Sockets |-> FALSE]
    /\ ownerSocket = NoneNode
    /\ dtick = 0

GrantShared(sock, cpu) ==
    /\ dtick < DualTickMax
    /\ sock \in Sockets /\ cpu \in LocalCPUs
    /\ ~socketBusy[sock]
    /\ socketRnf' = [socketRnf EXCEPT ![sock][cpu] = "HAVE_SC"]
    /\ socketBusy' = socketBusy
    /\ ownerSocket' = ownerSocket
    /\ dtick' = dtick + 1

GrantUnique(sock, cpu, dirty) ==
    /\ dtick < DualTickMax
    /\ sock \in Sockets /\ cpu \in LocalCPUs /\ dirty \in BOOLEAN
    /\ \A s \in Sockets : ~socketBusy[s]
    /\ socketRnf' = [s \in Sockets |->
          [c \in LocalCPUs |-> IF s = sock /\ c = cpu THEN IF dirty THEN "HAVE_UD" ELSE "HAVE_UC" ELSE "IDLE"]]
    /\ socketBusy' = [s \in Sockets |-> FALSE]
    /\ ownerSocket' = sock
    /\ dtick' = dtick + 1

CrossSocketInvalidate(sock) ==
    /\ dtick < DualTickMax
    /\ sock \in Sockets
    /\ socketRnf' = [socketRnf EXCEPT ![sock] = [c \in LocalCPUs |-> "IDLE"]]
    /\ socketBusy' = socketBusy
    /\ ownerSocket' = ownerSocket
    /\ dtick' = dtick + 1

MarkBusy(sock) ==
    /\ dtick < DualTickMax
    /\ sock \in Sockets
    /\ socketBusy' = [socketBusy EXCEPT ![sock] = TRUE]
    /\ UNCHANGED <<socketRnf, ownerSocket>>
    /\ dtick' = dtick + 1

ClearBusy(sock) ==
    /\ dtick < DualTickMax
    /\ sock \in Sockets
    /\ socketBusy' = [socketBusy EXCEPT ![sock] = FALSE]
    /\ UNCHANGED <<socketRnf, ownerSocket>>
    /\ dtick' = dtick + 1

TickDual ==
    /\ dtick < DualTickMax
    /\ UNCHANGED <<socketRnf, socketBusy, ownerSocket>>
    /\ dtick' = dtick + 1

StutterDual == /\ dtick = DualTickMax /\ UNCHANGED DualVars

DualNext ==
    \/ \E s \in Sockets : \E c \in LocalCPUs : GrantShared(s, c)
    \/ \E s \in Sockets : \E c \in LocalCPUs : \E d \in BOOLEAN : GrantUnique(s, c, d)
    \/ \E s \in Sockets : CrossSocketInvalidate(s)
    \/ \E s \in Sockets : MarkBusy(s)
    \/ \E s \in Sockets : ClearBusy(s)
    \/ TickDual
    \/ StutterDual

DualSpec == DualInit /\ [][DualNext]_DualVars

CrossSocketDataIntegrity ==
    Cardinality({<<s, c>> \in (Sockets \times LocalCPUs) : socketRnf[s][c] \in {"HAVE_UC", "HAVE_UD"}}) <= 1

CrossSocketOwnerCoherence ==
    ownerSocket = NoneNode \/ ownerSocket \in Sockets

=============================================================================
