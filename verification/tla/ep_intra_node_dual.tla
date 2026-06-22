--------------------------- MODULE ep_intra_node_dual ---------------------------
EXTENDS Integers, Naturals, FiniteSets

(* ************************************************************************* *)
(* Dual-socket abstraction with explicit per-socket/per-cpu state vars.    *)
(* Avoids function-typed state variables so TLC can serialize every state.  *)
(* ************************************************************************* *)

CONSTANTS Nodes, MaxTxn, Sockets

ASSUME Nodes = {0}
ASSUME MaxTxn \in Nat
ASSUME MaxTxn > 0
ASSUME Sockets = {0, 1}

LocalCPUs == {0, 1}
RNFState == {"IDLE", "HAVE_SC", "HAVE_UC", "HAVE_UD", "PENDING_RS", "PENDING_CU", "PENDING_RU"}
NoneNode == -1
DualTickMax == MaxTxn + 8

VARIABLES s0c0, s0c1, s1c0, s1c1, busy0, busy1, ownerSocket, dtick

DualVars == <<s0c0, s0c1, s1c0, s1c1, busy0, busy1, ownerSocket, dtick>>

SocketCpuState(sock, cpu) ==
    CASE sock = 0 /\ cpu = 0 -> s0c0
      [] sock = 0 /\ cpu = 1 -> s0c1
      [] sock = 1 /\ cpu = 0 -> s1c0
      [] OTHER -> s1c1

SetSocketCpu(sock, cpu, st) ==
    /\ s0c0' = IF sock = 0 /\ cpu = 0 THEN st ELSE s0c0
    /\ s0c1' = IF sock = 0 /\ cpu = 1 THEN st ELSE s0c1
    /\ s1c0' = IF sock = 1 /\ cpu = 0 THEN st ELSE s1c0
    /\ s1c1' = IF sock = 1 /\ cpu = 1 THEN st ELSE s1c1

SetAllExcept(sock, cpu, stOther, stSelf) ==
    /\ s0c0' = IF sock = 0 /\ cpu = 0 THEN stSelf ELSE stOther
    /\ s0c1' = IF sock = 0 /\ cpu = 1 THEN stSelf ELSE stOther
    /\ s1c0' = IF sock = 1 /\ cpu = 0 THEN stSelf ELSE stOther
    /\ s1c1' = IF sock = 1 /\ cpu = 1 THEN stSelf ELSE stOther

DualInit ==
    /\ s0c0 = "IDLE"
    /\ s0c1 = "IDLE"
    /\ s1c0 = "IDLE"
    /\ s1c1 = "IDLE"
    /\ busy0 = FALSE
    /\ busy1 = FALSE
    /\ ownerSocket = NoneNode
    /\ dtick = 0

GrantShared(sock, cpu) ==
    /\ dtick < DualTickMax
    /\ sock \in Sockets /\ cpu \in LocalCPUs
    /\ ~(IF sock = 0 THEN busy0 ELSE busy1)
    /\ SetSocketCpu(sock, cpu, "HAVE_SC")
    /\ busy0' = busy0
    /\ busy1' = busy1
    /\ ownerSocket' = IF ownerSocket = sock THEN NoneNode ELSE ownerSocket
    /\ dtick' = dtick + 1

GrantUnique(sock, cpu, dirty) ==
    /\ dtick < DualTickMax
    /\ sock \in Sockets /\ cpu \in LocalCPUs /\ dirty \in BOOLEAN
    /\ ~busy0 /\ ~busy1
    /\ SetAllExcept(sock, cpu, "IDLE", IF dirty THEN "HAVE_UD" ELSE "HAVE_UC")
    /\ busy0' = FALSE
    /\ busy1' = FALSE
    /\ ownerSocket' = sock
    /\ dtick' = dtick + 1

CrossSocketInvalidate(sock) ==
    /\ dtick < DualTickMax
    /\ sock \in Sockets
    /\ s0c0' = IF sock = 0 THEN "IDLE" ELSE s0c0
    /\ s0c1' = IF sock = 0 THEN "IDLE" ELSE s0c1
    /\ s1c0' = IF sock = 1 THEN "IDLE" ELSE s1c0
    /\ s1c1' = IF sock = 1 THEN "IDLE" ELSE s1c1
    /\ busy0' = busy0
    /\ busy1' = busy1
    /\ ownerSocket' = IF ownerSocket = sock THEN NoneNode ELSE ownerSocket
    /\ dtick' = dtick + 1

MarkBusy(sock) ==
    /\ dtick < DualTickMax
    /\ sock \in Sockets
    /\ IF sock = 0
          THEN /\ busy0' = TRUE
               /\ busy1' = busy1
          ELSE /\ busy0' = busy0
               /\ busy1' = TRUE
    /\ UNCHANGED <<s0c0, s0c1, s1c0, s1c1, ownerSocket>>
    /\ dtick' = dtick + 1

ClearBusy(sock) ==
    /\ dtick < DualTickMax
    /\ sock \in Sockets
    /\ IF sock = 0
          THEN /\ busy0' = FALSE
               /\ busy1' = busy1
          ELSE /\ busy0' = busy0
               /\ busy1' = FALSE
    /\ UNCHANGED <<s0c0, s0c1, s1c0, s1c1, ownerSocket>>
    /\ dtick' = dtick + 1

TickDual ==
    /\ dtick < DualTickMax
    /\ UNCHANGED <<s0c0, s0c1, s1c0, s1c1, busy0, busy1, ownerSocket>>
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
    Cardinality({<<s, c>> \in (Sockets \times LocalCPUs) : SocketCpuState(s, c) \in {"HAVE_UC", "HAVE_UD"}}) <= 1

CrossSocketOwnerCoherence ==
    /\ ownerSocket = NoneNode \/ ownerSocket \in Sockets
    /\ ownerSocket = 0 => \E c \in LocalCPUs : SocketCpuState(0, c) \in {"HAVE_UC", "HAVE_UD"}
    /\ ownerSocket = 1 => \E c \in LocalCPUs : SocketCpuState(1, c) \in {"HAVE_UC", "HAVE_UD"}

=============================================================================
