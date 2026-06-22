----------------------------- MODULE ep_intra_node -----------------------------
EXTENDS Integers, Naturals, FiniteSets, TLC

(***************************************************************************)
(* EP intra-node abstraction for one PA and two CPUs.                      *)
(* Models only CHI-facing controller ordering, not data payloads.          *)
(***************************************************************************)

CONSTANTS Nodes, MaxTxn

ASSUME Nodes = {0}
ASSUME MaxTxn \in Nat
ASSUME MaxTxn > 0

CPUs == {0, 1}
RNFState == {"IDLE", "HAVE_SC", "HAVE_UC", "HAVE_UD", "PENDING_RS", "PENDING_CU", "PENDING_RU"}
HNFState == {"H_IDLE", "H_WAIT_SNF", "H_WAIT_SNP", "H_WAIT_COMP", "H_WAIT_WB"}
SnoopTypes == {"SnpCleanInvalid", "SnpUnique", "SnpOnce"}
TxnOp == {"NONE", "RS", "RU", "CU"}
MaxTick == MaxTxn + 8

VARIABLES rnf, hnfState, inflight, queuedSnoop, deferredReq, callbackPending, postFinish, upgradeWait, tick

Vars == <<rnf, hnfState, inflight, queuedSnoop, deferredReq, callbackPending, postFinish, upgradeWait, tick>>

UniqueLike(st) == st \in {"HAVE_UC", "HAVE_UD", "PENDING_CU", "PENDING_RU"}

ApplyReadSharedSnoop(r, cpu) ==
    [c \in CPUs |-> IF c = cpu
                    THEN "PENDING_RS"
                    ELSE IF r[c] \in {"HAVE_UC", "HAVE_UD"}
                            THEN "HAVE_SC"
                            ELSE r[c]]

ApplyUniqueSnoop(r, cpu, pendingState) ==
    [c \in CPUs |-> IF c = cpu
                    THEN pendingState
                    ELSE IF r[c] \in {"HAVE_SC", "HAVE_UC", "HAVE_UD"}
                            THEN "IDLE"
                            ELSE r[c]]

ApplyIncomingSnoop(r, typ) ==
    IF typ = "SnpOnce"
       THEN [c \in CPUs |-> IF r[c] \in {"HAVE_UC", "HAVE_UD"} THEN "HAVE_SC" ELSE r[c]]
       ELSE [c \in CPUs |-> IF r[c] \in {"HAVE_SC", "HAVE_UC", "HAVE_UD"} THEN "IDLE" ELSE r[c]]

Init ==
    /\ rnf = [c \in CPUs |-> "IDLE"]
    /\ hnfState = "H_IDLE"
    /\ inflight = [valid |-> FALSE, cpu |-> 0, op |-> "NONE"]
    /\ queuedSnoop = [valid |-> FALSE, typ |-> "SnpOnce"]
    /\ deferredReq = [valid |-> FALSE, cpu |-> 0, op |-> "NONE"]
    /\ callbackPending = [valid |-> FALSE, cpu |-> 0]
    /\ postFinish = FALSE
    /\ upgradeWait = FALSE
    /\ tick = 0

StartReadShared(cpu) ==
    /\ tick < MaxTick
    /\ cpu \in CPUs
    /\ ~inflight.valid
    /\ ~postFinish
    /\ rnf[cpu] = "IDLE"
    /\ rnf' = ApplyReadSharedSnoop(rnf, cpu)
    /\ hnfState' = "H_WAIT_SNF"
    /\ inflight' = [valid |-> TRUE, cpu |-> cpu, op |-> "RS"]
    /\ UNCHANGED <<queuedSnoop, deferredReq, callbackPending, postFinish, upgradeWait>>
    /\ tick' = tick + 1

StartReadUnique(cpu) ==
    /\ tick < MaxTick
    /\ cpu \in CPUs
    /\ ~inflight.valid
    /\ ~postFinish
    /\ rnf[cpu] = "IDLE"
    /\ rnf' = ApplyUniqueSnoop(rnf, cpu, "PENDING_RU")
    /\ hnfState' = "H_WAIT_SNF"
    /\ inflight' = [valid |-> TRUE, cpu |-> cpu, op |-> "RU"]
    /\ UNCHANGED <<queuedSnoop, deferredReq, callbackPending, postFinish, upgradeWait>>
    /\ tick' = tick + 1

StartCleanUnique(cpu) ==
    /\ tick < MaxTick
    /\ cpu \in CPUs
    /\ ~inflight.valid
    /\ ~postFinish
    /\ rnf[cpu] = "HAVE_SC"
    /\ rnf' = ApplyUniqueSnoop(rnf, cpu, "PENDING_CU")
    /\ hnfState' = "H_WAIT_SNP"
    /\ inflight' = [valid |-> TRUE, cpu |-> cpu, op |-> "CU"]
    /\ queuedSnoop' = queuedSnoop
    /\ deferredReq' = deferredReq
    /\ callbackPending' = callbackPending
    /\ postFinish' = FALSE
    /\ upgradeWait' = TRUE
    /\ tick' = tick + 1

QueueDeferred(cpu, op) ==
    /\ tick < MaxTick
    /\ cpu \in CPUs
    /\ op \in {"RS", "RU"}
    /\ inflight.valid
    /\ ~deferredReq.valid
    /\ deferredReq' = [valid |-> TRUE, cpu |-> cpu, op |-> op]
    /\ UNCHANGED <<rnf, hnfState, inflight, queuedSnoop, callbackPending, postFinish, upgradeWait>>
    /\ tick' = tick + 1

RecvSnoopQueued(typ) ==
    /\ tick < MaxTick
    /\ typ \in SnoopTypes
    /\ inflight.valid
    /\ ~queuedSnoop.valid
    /\ queuedSnoop' = [valid |-> TRUE, typ |-> typ]
    /\ UNCHANGED <<rnf, hnfState, inflight, deferredReq, callbackPending, postFinish, upgradeWait>>
    /\ tick' = tick + 1

RecvSnoopImmediate(typ) ==
    /\ tick < MaxTick
    /\ typ \in SnoopTypes
    /\ ~inflight.valid
    /\ ~postFinish
    /\ queuedSnoop' = queuedSnoop
    /\ inflight' = inflight
    /\ deferredReq' = deferredReq
    /\ callbackPending' = callbackPending
    /\ rnf' = ApplyIncomingSnoop(rnf, typ)
    /\ hnfState' = IF typ = "SnpCleanInvalid" /\ upgradeWait THEN "H_WAIT_COMP" ELSE "H_IDLE"
    /\ postFinish' = postFinish
    /\ upgradeWait' = upgradeWait
    /\ tick' = tick + 1

FinishChiTxn ==
    /\ tick < MaxTick
    /\ inflight.valid
    /\ LET cpu == inflight.cpu IN
       /\ rnf' = [rnf EXCEPT ![cpu] = CASE inflight.op = "RS" -> "HAVE_SC"
                                              [] inflight.op = "RU" -> "HAVE_UC"
                                              [] OTHER -> "HAVE_UC"]
       /\ hnfState' = "H_IDLE"
       /\ inflight' = [valid |-> FALSE, cpu |-> cpu, op |-> "NONE"]
       /\ callbackPending' = [valid |-> TRUE, cpu |-> cpu]
       /\ queuedSnoop' = queuedSnoop
       /\ deferredReq' = deferredReq
       /\ postFinish' = TRUE
       /\ upgradeWait' = upgradeWait
       /\ tick' = tick + 1

ProcessQueuedSnoop ==
    /\ tick < MaxTick
    /\ postFinish
    /\ queuedSnoop.valid
    /\ rnf' = ApplyIncomingSnoop(rnf, queuedSnoop.typ)
    /\ inflight' = inflight
    /\ deferredReq' = deferredReq
    /\ callbackPending' = callbackPending
    /\ queuedSnoop' = [valid |-> FALSE, typ |-> queuedSnoop.typ]
    /\ hnfState' = IF queuedSnoop.typ = "SnpCleanInvalid" THEN "H_WAIT_COMP" ELSE "H_IDLE"
    /\ postFinish' = TRUE
    /\ upgradeWait' = IF queuedSnoop.typ = "SnpCleanInvalid" THEN TRUE ELSE upgradeWait
    /\ tick' = tick + 1

LaunchDeferredReq ==
    /\ tick < MaxTick
    /\ postFinish
    /\ ~queuedSnoop.valid
    /\ deferredReq.valid
    /\ rnf' = IF deferredReq.op = "RS"
                 THEN ApplyReadSharedSnoop(rnf, deferredReq.cpu)
                 ELSE ApplyUniqueSnoop(rnf, deferredReq.cpu, "PENDING_RU")
    /\ hnfState' = "H_WAIT_SNF"
    /\ inflight' = [valid |-> TRUE, cpu |-> deferredReq.cpu, op |-> deferredReq.op]
    /\ deferredReq' = [valid |-> FALSE, cpu |-> deferredReq.cpu, op |-> "NONE"]
    /\ UNCHANGED <<queuedSnoop, callbackPending, upgradeWait>>
    /\ postFinish' = FALSE
    /\ tick' = tick + 1

RunCallback ==
    /\ tick < MaxTick
    /\ postFinish
    /\ hnfState = "H_IDLE"
    /\ ~upgradeWait
    /\ ~queuedSnoop.valid
    /\ ~deferredReq.valid
    /\ callbackPending.valid
    /\ UNCHANGED <<rnf, hnfState, inflight, queuedSnoop, deferredReq, upgradeWait>>
    /\ callbackPending' = [valid |-> FALSE, cpu |-> callbackPending.cpu]
    /\ postFinish' = FALSE
    /\ tick' = tick + 1

ReceiveUpgradeAck ==
    /\ tick < MaxTick
    /\ hnfState = "H_WAIT_COMP"
    /\ UNCHANGED <<rnf, inflight, queuedSnoop, deferredReq, callbackPending, postFinish>>
    /\ hnfState' = "H_IDLE"
    /\ upgradeWait' = FALSE
    /\ tick' = tick + 1

TickOnly ==
    /\ tick < MaxTick
    /\ UNCHANGED <<rnf, hnfState, inflight, queuedSnoop, deferredReq, callbackPending, postFinish, upgradeWait>>
    /\ tick' = tick + 1

Stutter == /\ tick = MaxTick /\ UNCHANGED Vars

Next ==
    \/ \E c \in CPUs : StartReadShared(c)
    \/ \E c \in CPUs : StartReadUnique(c)
    \/ \E c \in CPUs : StartCleanUnique(c)
    \/ \E c \in CPUs : \E op \in {"RS", "RU"} : QueueDeferred(c, op)
    \/ \E t \in SnoopTypes : RecvSnoopQueued(t)
    \/ \E t \in SnoopTypes : RecvSnoopImmediate(t)
    \/ FinishChiTxn
    \/ ProcessQueuedSnoop
    \/ LaunchDeferredReq
    \/ RunCallback
    \/ ReceiveUpgradeAck
    \/ TickOnly
    \/ Stutter

Spec == Init /\ [][Next]_Vars

NoDeadlock ==
    ~(~inflight.valid /\ ~postFinish /\ hnfState # "H_IDLE" /\ ~queuedSnoop.valid /\ ~deferredReq.valid /\ ~callbackPending.valid)

DataIntegrity ==
    Cardinality({c \in CPUs : rnf[c] \in {"HAVE_UC", "HAVE_UD", "PENDING_CU", "PENDING_RU"}}) <= 1

SnoopCorrectness == queuedSnoop.valid => queuedSnoop.typ \in SnoopTypes

CallbackOrdering == postFinish /\ queuedSnoop.valid => callbackPending.valid

=============================================================================
