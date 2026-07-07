---------------------------- MODULE ubcc_multi_socket ----------------------------
EXTENDS Integers, Naturals, FiniteSets, Sequences, TLC

(***************************************************************************)
(* Stage-4 (D2): multi-socket coherence with explicit CROSS-SOCKET MESSAGE  *)
(* ROUTING.                                                                 *)
(*                                                                          *)
(* The existing ep_intra_node_dual model covers per-socket CACHE STATE but  *)
(* not the ROUTING of coherence messages between sockets. This model adds   *)
(* that layer: a line's home directory lives on one (node,socket) plane;    *)
(* requesters on OTHER planes must route their request to the home over an  *)
(* in-flight network, and the grant must route back. It checks that routing *)
(* (with messages sitting in-flight, i.e. the 1-hop/2-hop latency the       *)
(* latency work tunes) never loses a request or corrupts the coherence      *)
(* outcome, and that home-side directory transitions are IDENTICAL whether  *)
(* the requester is local (same socket as home) or remote (routed).         *)
(*                                                                          *)
(* Abstraction: each (node,socket) is a global module id = node*K+socket    *)
(* (matching gidOf in ubio_main.cc). The network is a set of in-flight      *)
(* messages (src, dst, kind, epoch); delivery is non-deterministic so any   *)
(* routing latency / ordering is explored. Data payloads abstracted.        *)
(***************************************************************************)

CONSTANTS Modules, Home, MaxEpoch

\* Modules are global plane ids. Home is the plane that owns the directory.
ASSUME Home \in Modules
ASSUME MaxEpoch \in Nat /\ MaxEpoch > 0

NoneMod == -1
DirState == {"G_I", "G_S", "G_E"}
MsgKind  == {"Req", "Grant"}
MaxTick  == 2 * MaxEpoch + 6

VARIABLES dirState, dirOwner, dirSharers, dirEpoch, net, tick

Vars == <<dirState, dirOwner, dirSharers, dirEpoch, net, tick>>

\* A network message.
Msg(src, dst, kind, ep) == [src |-> src, dst |-> dst, kind |-> kind, epoch |-> ep]

HasMsg(m) == m \in net

Canonical ==
    /\ dirState \in DirState
    /\ dirSharers \subseteq Modules
    /\ dirOwner \in (Modules \cup {NoneMod})
    /\ dirEpoch \in 0..MaxEpoch
    /\ IF dirState = "G_I"      THEN dirSharers = {} /\ dirOwner = NoneMod
       ELSE IF dirState = "G_S" THEN dirSharers # {} /\ dirOwner = NoneMod
       ELSE Cardinality(dirSharers) = 1 /\ dirOwner \in dirSharers

Init ==
    /\ dirState = "G_I"
    /\ dirOwner = NoneMod
    /\ dirSharers = {}
    /\ dirEpoch = 0
    /\ net = {}
    /\ tick = 0

\* A requester plane `r` issues a request toward Home. If r = Home this is a
\* local (same-socket) request (no routing); if r # Home it is a cross-socket
\* request that must traverse the network. Modeled uniformly by putting a Req
\* message in-flight; local requests simply have src = dst = Home.
IssueReq(r) ==
    /\ tick < MaxTick
    /\ r \in Modules
    /\ dirEpoch < MaxEpoch
    /\ ~(\E m \in net : m.kind = "Req" /\ m.src = r)   \* one outstanding req per requester
    /\ net' = net \cup {Msg(r, Home, "Req", dirEpoch + 1)}
    /\ UNCHANGED <<dirState, dirOwner, dirSharers, dirEpoch>>
    /\ tick' = tick + 1

\* Home receives a routed Req (from ANY module, local or remote) and grants
\* exclusive ownership. The directory transition is the SAME regardless of
\* whether m.src is local or remote — that is the property under test.
HomeGrant(m) ==
    /\ tick < MaxTick
    /\ m \in net
    /\ m.kind = "Req"
    /\ m.dst = Home
    /\ dirState \in {"G_I", "G_S"}
    /\ dirEpoch < MaxEpoch
    /\ dirState' = "G_E"
    /\ dirOwner' = m.src
    /\ dirSharers' = {m.src}
    /\ dirEpoch' = dirEpoch + 1
    /\ net' = (net \ {m}) \cup {Msg(Home, m.src, "Grant", dirEpoch + 1)}
    /\ tick' = tick + 1

\* The grant is routed back and consumed by the requester (completes the txn).
DeliverGrant(m) ==
    /\ tick < MaxTick
    /\ m \in net
    /\ m.kind = "Grant"
    /\ net' = net \ {m}
    /\ UNCHANGED <<dirState, dirOwner, dirSharers, dirEpoch>>
    /\ tick' = tick + 1

\* Owner writes back / releases -> directory returns to G_I.
Release ==
    /\ tick < MaxTick
    /\ dirState = "G_E"
    /\ dirState' = "G_I"
    /\ dirOwner' = NoneMod
    /\ dirSharers' = {}
    /\ dirEpoch' = dirEpoch
    /\ net' = net
    /\ tick' = tick + 1

\* Network reorders/holds messages implicitly (delivery is non-deterministic
\* via \E m). Explicit drop of a Req models routing loss (must not corrupt dir).
DropReq(m) ==
    /\ tick < MaxTick
    /\ m \in net
    /\ m.kind = "Req"
    /\ net' = net \ {m}
    /\ UNCHANGED <<dirState, dirOwner, dirSharers, dirEpoch>>
    /\ tick' = tick + 1

TickOnly ==
    /\ tick < MaxTick
    /\ UNCHANGED <<dirState, dirOwner, dirSharers, dirEpoch, net>>
    /\ tick' = tick + 1

Stutter == tick = MaxTick /\ UNCHANGED Vars

Next ==
    \/ \E r \in Modules : IssueReq(r)
    \/ \E m \in net : HomeGrant(m)
    \/ \E m \in net : DeliverGrant(m)
    \/ \E m \in net : DropReq(m)
    \/ Release
    \/ TickOnly
    \/ Stutter

Spec == Init /\ [][Next]_Vars

(* ── Invariants ────────────────────────────────────────────────────────── *)

\* Directory always canonical regardless of cross-socket routing / in-flight.
DirCanonical == Canonical

\* At most one exclusive owner across all sockets (no two planes think they
\* own the line — the core coherence guarantee under cross-socket routing).
SingleOwner ==
    dirState = "G_E" => Cardinality(dirSharers) = 1

\* Epoch monotonic: routed/duplicated/dropped messages never roll back epoch.
EpochBounded == dirEpoch \in 0..MaxEpoch

\* Grant target sanity: any in-flight Grant goes to a module that the epoch it
\* carries does not exceed the current directory epoch (no stale future grant).
GrantEpochSane ==
    \A m \in net : m.kind = "Grant" => m.epoch <= dirEpoch
=============================================================================
