----------------------- MODULE ep_rnf_snoop_arbitration -----------------------
EXTENDS Naturals, TLC

(***************************************************************************)
(* Current EPRNFController::recvSnoopMsg arbitration for one PA.            *)
(*                                                                         *)
(* Priority: active recall -> immediate self-snoop handling. Otherwise:     *)
(*   no in-flight transaction        -> IMMED                              *)
(*   SnpOnce + ReadShared in-flight  -> IMMED_DATA                         *)
(*   write-intent snoop + any flight -> STALE                              *)
(*   SnpOnce + RU/CU in-flight       -> STALE                              *)
(* Preserving SnpShared/Fwd targeting EP-RNF is a routing error.            *)
(***************************************************************************)

TxnOps == {"NONE", "ReadShared", "ReadUnique", "CleanUnique"}
SnoopTypes == {"SnpCleanInvalid", "SnpUnique", "SnpOnce",
               "SnpShared", "SnpSharedFwd"}
Outcomes == {"NONE", "IMMED", "IMMED_DATA", "STALE", "FATAL"}

ExpectedOutcome(op, activeRecall, snoop) ==
    IF activeRecall THEN "IMMED"
    ELSE IF snoop \in {"SnpShared", "SnpSharedFwd"} THEN "FATAL"
    ELSE IF op = "NONE" THEN "IMMED"
    ELSE IF snoop = "SnpOnce" /\ op = "ReadShared" THEN "IMMED_DATA"
    ELSE "STALE"

VARIABLES inflightOp, activeRecall, lastSnoop, lastOutcome, lastOp,
          lastHadRecall, staleQueued

Vars == <<inflightOp, activeRecall, lastSnoop, lastOutcome, lastOp,
          lastHadRecall, staleQueued>>

Init ==
    /\ inflightOp = "NONE"
    /\ activeRecall = FALSE
    /\ lastSnoop = "SnpOnce"
    /\ lastOutcome = "NONE"
    /\ lastOp = "NONE"
    /\ lastHadRecall = FALSE
    /\ staleQueued = FALSE

StartTxn(op) ==
    /\ op \in TxnOps \ {"NONE"}
    /\ inflightOp = "NONE"
    /\ inflightOp' = op
    /\ UNCHANGED <<activeRecall, lastSnoop, lastOutcome, lastOp,
                    lastHadRecall, staleQueued>>

FinishTxn ==
    /\ inflightOp # "NONE"
    /\ inflightOp' = "NONE"
    /\ UNCHANGED <<activeRecall, lastSnoop, lastOutcome, lastOp,
                    lastHadRecall, staleQueued>>

MarkRecall ==
    /\ ~activeRecall
    /\ activeRecall' = TRUE
    /\ UNCHANGED <<inflightOp, lastSnoop, lastOutcome, lastOp,
                    lastHadRecall, staleQueued>>

ReceiveSnoop(snoop) ==
    /\ snoop \in SnoopTypes
    /\ lastSnoop' = snoop
    /\ lastOutcome' = ExpectedOutcome(inflightOp, activeRecall, snoop)
    /\ lastOp' = inflightOp
    /\ lastHadRecall' = activeRecall
    /\ activeRecall' = IF activeRecall THEN FALSE ELSE activeRecall
    (* Current C++ sends STALE immediately; it never queues the stale snoop. *)
    /\ staleQueued' = FALSE
    /\ UNCHANGED inflightOp

Next ==
    \/ \E op \in TxnOps \ {"NONE"} : StartTxn(op)
    \/ FinishTxn
    \/ MarkRecall
    \/ \E snoop \in SnoopTypes : ReceiveSnoop(snoop)

Spec == Init /\ [][Next]_Vars

TypeOK ==
    /\ inflightOp \in TxnOps
    /\ activeRecall \in BOOLEAN
    /\ lastSnoop \in SnoopTypes
    /\ lastOutcome \in Outcomes
    /\ lastOp \in TxnOps
    /\ lastHadRecall \in BOOLEAN
    /\ staleQueued \in BOOLEAN

MatrixCorrect ==
    lastOutcome = "NONE" \/
    lastOutcome = ExpectedOutcome(lastOp, lastHadRecall, lastSnoop)

NoStaleQueue == ~staleQueued

ReadReadCoexists ==
    lastOp = "ReadShared" /\ ~lastHadRecall /\ lastSnoop = "SnpOnce" /\
    lastOutcome # "NONE" => lastOutcome = "IMMED_DATA"

PreservingSnoopRejected ==
    lastSnoop \in {"SnpShared", "SnpSharedFwd"} /\
    ~lastHadRecall /\ lastOutcome # "NONE" =>
        lastOutcome = "FATAL"

=============================================================================
