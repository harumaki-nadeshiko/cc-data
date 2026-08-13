--------------------------- MODULE peer_exit_reliable ---------------------------
EXTENDS Naturals, Sequences, FiniteSets, TLC

(***************************************************************************)
(* Reliable PeerExit with production-aligned bounded quiescence.           *)
(*                                                                         *)
(* Reliability is sender retry-Notify plus an immediate Ack for every      *)
(* first or duplicate Notify.  There is deliberately no Ack timer and no   *)
(* persistent Ack obligation.  The wait set is frozen when local exit      *)
(* starts, excluding peers whose Notify was already observed.              *)
(*                                                                         *)
(* This is a bounded transient-loss/delay contract, not partition          *)
(* tolerance. Drops and duplicates are finite. Tick models wall-clock      *)
(* progress independently of protocol state: it decrements sender retry,   *)
(* message delivery, and responder quiescence timers. The checked positive *)
(* configurations require QuiesceRounds > RetryRounds + DeliveryRounds.    *)
(* delivered duplicate resets the responder's complete quiesce window.     *)
(*                                                                         *)
(* ImmediateClose removes that bounded responder window. The negative cfg  *)
(* enables it and is expected to violate liveness after a dropped Ack.     *)
(***************************************************************************)

CONSTANTS Nodes, MaxDrops, MaxDups, MaxStaleAcks, MaxQueue, RetryRounds,
          DeliveryRounds, QuiesceRounds, ImmediateClose, ExitMode

ASSUME Nodes \in {{0, 1}, {0, 1, 2}}
ASSUME MaxDrops \in Nat
ASSUME MaxDups \in Nat
ASSUME MaxStaleAcks \in Nat
ASSUME MaxQueue \in Nat \ {0}
ASSUME RetryRounds \in Nat \ {0}
ASSUME DeliveryRounds \in Nat
ASSUME QuiesceRounds \in Nat
ASSUME ImmediateClose \in BOOLEAN
ASSUME ExitMode \in {"Concurrent", "Simultaneous", "Sequential"}
ASSUME ImmediateClose \/ QuiesceRounds > RetryRounds + DeliveryRounds

Phases == {"Running", "Exiting", "Quiesced", "Exited"}
Kinds == {"Notify", "Ack"}
ExitId(n) == n + 1
ExitIds == {ExitId(n) : n \in Nodes}
Messages ==
    {[kind |-> k, src |-> s, dst |-> d, exitId |-> e, delay |-> t] :
        k \in Kinds, s \in Nodes, d \in Nodes, e \in ExitIds,
        t \in 0..DeliveryRounds}

VARIABLES phase, waitSet, frozenKnown, acked, knownExited, seenNotify,
          retryLeft, quietLeft, net, dropsLeft, dupsLeft, staleAcksLeft

vars == <<phase, waitSet, frozenKnown, acked, knownExited, seenNotify,
          retryLeft, quietLeft, net, dropsLeft, dupsLeft, staleAcksLeft>>

RemoveAt(q, i) == SubSeq(q, 1, i - 1) \o SubSeq(q, i + 1, Len(q))
Msg(kind, src, dst, exitId) ==
    [kind |-> kind, src |-> src, dst |-> dst, exitId |-> exitId,
     delay |-> DeliveryRounds]
InFlight(kind, src, dst, exitId) ==
    \E i \in 1..Len(net) :
        /\ net[i].kind = kind
        /\ net[i].src = src
        /\ net[i].dst = dst
        /\ net[i].exitId = exitId
InitialNotifies(n) ==
    CASE n = 0 ->
      (IF 1 \in knownExited[0] THEN <<>>
           ELSE <<Msg("Notify", 0, 1, ExitId(0))>>) \o
          (IF 2 \notin Nodes \/ 2 \in knownExited[0] THEN <<>>
           ELSE <<Msg("Notify", 0, 2, ExitId(0))>>)
      [] n = 1 ->
          (IF 0 \in knownExited[1] THEN <<>>
           ELSE <<Msg("Notify", 1, 0, ExitId(1))>>) \o
          (IF 2 \notin Nodes \/ 2 \in knownExited[1] THEN <<>>
           ELSE <<Msg("Notify", 1, 2, ExitId(1))>>)
      [] OTHER ->
          (IF 0 \in knownExited[2] THEN <<>>
           ELSE <<Msg("Notify", 2, 0, ExitId(2))>>) \o
          (IF 1 \in knownExited[2] THEN <<>>
           ELSE <<Msg("Notify", 2, 1, ExitId(2))>>)
AllInitialNotifies ==
    <<Msg("Notify", 0, 1, ExitId(0)),
      Msg("Notify", 1, 0, ExitId(1))>> \o
    (IF 2 \in Nodes
     THEN <<Msg("Notify", 0, 2, ExitId(0)),
            Msg("Notify", 1, 2, ExitId(1)),
            Msg("Notify", 2, 0, ExitId(2)),
            Msg("Notify", 2, 1, ExitId(2))>>
     ELSE <<>>)

Init ==
    /\ phase = [n \in Nodes |-> "Running"]
    /\ waitSet = [n \in Nodes |-> {}]
    /\ frozenKnown = [n \in Nodes |-> {}]
    /\ acked = [n \in Nodes |-> {}]
    /\ knownExited = [n \in Nodes |-> {}]
    /\ seenNotify = [n \in Nodes |-> {}]
    /\ retryLeft = [s \in Nodes |-> [d \in Nodes |-> 0]]
    /\ quietLeft = [n \in Nodes |-> 0]
    /\ net = <<>>
    /\ dropsLeft = MaxDrops
    /\ dupsLeft = MaxDups
    /\ staleAcksLeft = MaxStaleAcks

CanStart(n) ==
    /\ phase[n] = "Running"
    /\ IF ExitMode = "Sequential"
          THEN \A m \in Nodes : m < n => phase[m] = "Exited"
          ELSE ExitMode = "Concurrent"

StartExit(n) ==
    /\ CanStart(n)
    /\ Len(net) + Len(InitialNotifies(n)) <= MaxQueue
    /\ phase' = [phase EXCEPT ![n] = "Exiting"]
    /\ waitSet' = [waitSet EXCEPT ![n] = Nodes \ ({n} \cup knownExited[n])]
    /\ frozenKnown' = [frozenKnown EXCEPT ![n] = knownExited[n]]
    /\ retryLeft' = [s \in Nodes |-> [d \in Nodes |->
          IF s = n /\ d \in waitSet'[n]
          THEN RetryRounds ELSE retryLeft[s][d]]]
    /\ net' = net \o InitialNotifies(n)
    /\ UNCHANGED <<acked, knownExited, seenNotify, quietLeft,
                    dropsLeft, dupsLeft, staleAcksLeft>>

StartAll ==
    /\ ExitMode = "Simultaneous"
    /\ \A n \in Nodes : phase[n] = "Running"
    /\ Len(net) + Len(AllInitialNotifies) <= MaxQueue
    /\ phase' = [n \in Nodes |-> "Exiting"]
    /\ waitSet' = [n \in Nodes |-> Nodes \ ({n} \cup knownExited[n])]
    /\ frozenKnown' = knownExited
    /\ retryLeft' = [s \in Nodes |-> [d \in Nodes |->
          IF d \in waitSet'[s] THEN RetryRounds ELSE 0]]
    /\ net' = net \o AllInitialNotifies
    /\ UNCHANGED <<acked, knownExited, seenNotify, quietLeft,
                    dropsLeft, dupsLeft, staleAcksLeft>>

RetryNotify(s, d) ==
    /\ phase[s] = "Exiting"
    /\ d \in waitSet[s] \ acked[s]
    /\ retryLeft[s][d] = 0
    /\ ~InFlight("Notify", s, d, ExitId(s))
    /\ Len(net) < MaxQueue
    /\ net' = Append(net, Msg("Notify", s, d, ExitId(s)))
    /\ retryLeft' = [retryLeft EXCEPT ![s][d] = RetryRounds]
    /\ UNCHANGED <<phase, waitSet, frozenKnown, acked, knownExited,
                    seenNotify, quietLeft, dropsLeft, dupsLeft,
                    staleAcksLeft>>

CanRespond(n) == phase[n] # "Exited"

DeliverNotify(s, d) ==
    /\ CanRespond(d)
    /\ \E i \in 1..Len(net) :
        /\ net[i].kind = "Notify"
        /\ net[i].src = s
        /\ net[i].dst = d
        /\ net[i].exitId = ExitId(s)
        /\ net[i].delay = 0
        /\ net' = Append(RemoveAt(net, i),
                         Msg("Ack", d, s, net[i].exitId))
    /\ knownExited' = [knownExited EXCEPT ![d] = @ \cup {s}]
    /\ seenNotify' = [seenNotify EXCEPT ![d] = @ \cup {s}]
    /\ quietLeft' = IF phase[d] = "Quiesced"
                        THEN [quietLeft EXCEPT ![d] = QuiesceRounds]
                        ELSE quietLeft
    /\ UNCHANGED <<phase, waitSet, frozenKnown, acked, retryLeft, dropsLeft,
                    dupsLeft, staleAcksLeft>>

DeliverAck(s, d) ==
    /\ \E i \in 1..Len(net) :
        /\ net[i].kind = "Ack"
        /\ net[i].src = s
        /\ net[i].dst = d
        /\ net[i].delay = 0
        /\ net' = RemoveAt(net, i)
        /\ acked' = IF phase[d] = "Exiting" /\ s \in waitSet[d] /\
                           net[i].exitId = ExitId(d)
                        THEN [acked EXCEPT ![d] = @ \cup {s}]
                        ELSE acked
    /\ UNCHANGED <<phase, waitSet, frozenKnown, knownExited, seenNotify,
                    retryLeft, quietLeft, dropsLeft, dupsLeft, staleAcksLeft>>

DropMessage ==
    /\ dropsLeft > 0
    /\ Len(net) > 0
    /\ \E i \in 1..Len(net) : net' = RemoveAt(net, i)
    /\ dropsLeft' = dropsLeft - 1
    /\ UNCHANGED <<phase, waitSet, frozenKnown, acked, knownExited,
                    seenNotify, retryLeft, quietLeft, dupsLeft, staleAcksLeft>>

DuplicateMessage ==
    /\ dupsLeft > 0
    /\ Len(net) > 0
    /\ Len(net) < MaxQueue
    /\ \E i \in 1..Len(net) : net' = Append(net, net[i])
    /\ dupsLeft' = dupsLeft - 1
    /\ UNCHANGED <<phase, waitSet, frozenKnown, acked, knownExited,
                    seenNotify, retryLeft, quietLeft, dropsLeft, staleAcksLeft>>

InjectStaleAck(s, d) ==
    /\ staleAcksLeft > 0
    /\ s # d
    /\ Len(net) < MaxQueue
    /\ net' = Append(net, Msg("Ack", s, d, ExitId(s)))
    /\ staleAcksLeft' = staleAcksLeft - 1
    /\ UNCHANGED <<phase, waitSet, frozenKnown, acked, knownExited,
                    seenNotify, retryLeft, quietLeft, dropsLeft, dupsLeft>>

EnterQuiesce(n) ==
    /\ phase[n] = "Exiting"
    /\ waitSet[n] \subseteq acked[n]
    /\ phase' = [phase EXCEPT ![n] = "Quiesced"]
    /\ quietLeft' = [quietLeft EXCEPT ![n] = QuiesceRounds]
    /\ UNCHANGED <<waitSet, frozenKnown, acked, knownExited, seenNotify,
                    retryLeft, net, dropsLeft, dupsLeft, staleAcksLeft>>

TickEnabled ==
    /\ ~\E i \in 1..Len(net) : net[i].delay = 0
    /\ ~\E s \in Nodes : \E d \in Nodes \ {s} :
          /\ phase[s] = "Exiting"
          /\ d \in waitSet[s] \ acked[s]
          /\ retryLeft[s][d] = 0
          /\ ~InFlight("Notify", s, d, ExitId(s))
          /\ Len(net) < MaxQueue
    /\ \/ \E s \in Nodes : \E d \in Nodes : retryLeft[s][d] > 0
       \/ \E n \in Nodes : quietLeft[n] > 0
       \/ \E i \in 1..Len(net) : net[i].delay > 0

Tick ==
    /\ TickEnabled
    /\ retryLeft' = [s \in Nodes |-> [d \in Nodes |->
          IF retryLeft[s][d] > 0 THEN retryLeft[s][d] - 1
          ELSE retryLeft[s][d]]]
    /\ quietLeft' = [n \in Nodes |->
          IF quietLeft[n] > 0 THEN quietLeft[n] - 1 ELSE quietLeft[n]]
    /\ net' = [i \in 1..Len(net) |->
          [net[i] EXCEPT !.delay = IF @ > 0 THEN @ - 1 ELSE @]]
    /\ UNCHANGED <<phase, waitSet, frozenKnown, acked, knownExited,
                    seenNotify, dropsLeft, dupsLeft, staleAcksLeft>>

FinishExit(n) ==
    /\ phase[n] = "Quiesced"
    /\ IF ImmediateClose THEN TRUE ELSE quietLeft[n] = 0
    /\ ~\E i \in 1..Len(net) :
          net[i].kind = "Notify" /\ net[i].dst = n /\ net[i].delay = 0
    /\ phase' = [phase EXCEPT ![n] = "Exited"]
    /\ UNCHANGED <<waitSet, frozenKnown, acked, knownExited, seenNotify,
                    retryLeft, quietLeft, net, dropsLeft, dupsLeft,
                    staleAcksLeft>>

StartAny == \E n \in Nodes : StartExit(n)
RetryAny == \E s \in Nodes : \E d \in Nodes \ {s} : RetryNotify(s, d)
DeliverNotifyAny == \E s \in Nodes : \E d \in Nodes \ {s} : DeliverNotify(s, d)
DeliverAckAny == \E s \in Nodes : \E d \in Nodes \ {s} : DeliverAck(s, d)
QuiesceAny == \E n \in Nodes : EnterQuiesce(n)
FinishAny == \E n \in Nodes : FinishExit(n)
InjectStaleAckAny ==
    \E s \in Nodes : \E d \in Nodes \ {s} : InjectStaleAck(s, d)

Next ==
    \/ StartAll \/ StartAny \/ RetryAny
    \/ DeliverNotifyAny \/ DeliverAckAny
    \/ DropMessage \/ DuplicateMessage \/ InjectStaleAckAny
    \/ QuiesceAny \/ Tick \/ FinishAny

SafetySpec == Init /\ [][Next]_vars
Fairness ==
    /\ IF ExitMode = "Simultaneous"
          THEN WF_vars(StartAll)
          ELSE \A n \in Nodes : WF_vars(StartExit(n))
    /\ \A s \in Nodes : \A d \in Nodes \ {s} :
          /\ WF_vars(RetryNotify(s, d))
          /\ WF_vars(DeliverNotify(s, d))
          /\ WF_vars(DeliverAck(s, d))
    /\ \A n \in Nodes :
          /\ WF_vars(EnterQuiesce(n))
          /\ WF_vars(FinishExit(n))
    /\ WF_vars(Tick)
FairSpec == SafetySpec /\ Fairness

TypeOK ==
    /\ phase \in [Nodes -> Phases]
    /\ waitSet \in [Nodes -> SUBSET Nodes]
    /\ frozenKnown \in [Nodes -> SUBSET Nodes]
    /\ acked \in [Nodes -> SUBSET Nodes]
    /\ knownExited \in [Nodes -> SUBSET Nodes]
    /\ seenNotify \in [Nodes -> SUBSET Nodes]
    /\ retryLeft \in [Nodes -> [Nodes -> 0..RetryRounds]]
    /\ quietLeft \in [Nodes -> 0..QuiesceRounds]
    /\ net \in Seq(Messages)
    /\ dropsLeft \in 0..MaxDrops
    /\ dupsLeft \in 0..MaxDups
    /\ staleAcksLeft \in 0..MaxStaleAcks

QueueBound == Len(net) <= MaxQueue
StableGeneration ==
    /\ \A n \in Nodes :
          /\ ExitId(n) > 0
          /\ n \notin waitSet[n]
          /\ acked[n] \subseteq waitSet[n]
    /\ \A a \in Nodes : \A b \in Nodes :
          ExitId(a) = ExitId(b) => a = b
FrozenWaitSetSound ==
    \A n \in Nodes : phase[n] # "Running" =>
        waitSet[n] = Nodes \ ({n} \cup frozenKnown[n])
NoPrematureExit ==
    \A n \in Nodes : phase[n] \in {"Quiesced", "Exited"} =>
        waitSet[n] \subseteq acked[n]
AckAuthentic == \A n \in Nodes : \A p \in acked[n] : n \in seenNotify[p]
DuplicateIdempotence ==
    /\ \A n \in Nodes : Cardinality(acked[n]) <= Cardinality(waitSet[n])
    /\ \A n \in Nodes : acked[n] \subseteq Nodes \ {n}

AllExit == <> (\A n \in Nodes : phase[n] = "Exited")
StartedExitProgress ==
    \A n \in Nodes : (phase[n] # "Running") ~> (phase[n] = "Exited")

=============================================================================
