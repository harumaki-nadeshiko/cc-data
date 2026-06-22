--------------------------- MODULE ep_intra_node ---------------------------
(**
 * TLA+ specification of the intra-node EP path.
 *
 * Answered design points:
 *   - Q1: multi-channel transport (reqMsgs / snpMsgs / rspMsgs / datMsgs)
 *   - Q2: single node index domain for HN-F / EP-RNF / EP-SNF
 *   - Q3: RNF stable states + pending RS/CU/RU transients
 *   - Q4: RNF local closure callback on CompUCSeen /\ CompAckSent
 *   - Q5: data version number for writeback integrity
 *
 * Scope: bounded intra-node path only, independent of ubcc_protocol.
 *)

EXTENDS Naturals, FiniteSets, Sequences, TLC

(***************************************************************************)
(* Constants                                                               *)
(***************************************************************************)

CONSTANTS
    Nodes,
    MaxTxn

ASSUME Cardinality(Nodes) > 0
ASSUME MaxTxn > 0

(***************************************************************************)
(* Types                                                                   *)
(***************************************************************************)

HnfStates == {
    "H_IDLE", "H_WAIT_SNF", "H_WAIT_SNP", "H_WAIT_COMP", "H_WAIT_WB"
}

ReqStates == { "NONE", "RS", "RU" }

RnfStates == {
    "IDLE",
    "HAVE_SC", "HAVE_UC", "HAVE_UD",
    "PENDING_RS", "PENDING_CU", "PENDING_RU"
}

StableRnfStates == { "IDLE", "HAVE_SC", "HAVE_UC", "HAVE_UD" }
PendingRnfStates == { "PENDING_RS", "PENDING_CU", "PENDING_RU" }

ReqKinds == { "MISS_RS", "MISS_RU", "FWD_RS", "FWD_RU", "COMP_ACK" }
SnpKinds == { "SNP_RS", "SNP_CU", "SNP_RU" }
RspKinds == { "COMP_UC" }
DatKinds == { "WB" }

AwaitNone == [snf |-> FALSE, snp |-> FALSE, comp |-> FALSE, wb |-> FALSE]

(***************************************************************************)
(* Variables                                                               *)
(***************************************************************************)

VARIABLES
    hnfState,
    hnfPendingReq,
    hnfAwaiting,
    rnfState,
    rnfCompUCSeen,
    rnfCompAckSent,
    snfBusy,
    cpuReq,
    reqMsgs,
    snpMsgs,
    rspMsgs,
    datMsgs,
    dataVer

vars == <<
    hnfState, hnfPendingReq, hnfAwaiting,
    rnfState, rnfCompUCSeen, rnfCompAckSent,
    snfBusy, cpuReq,
    reqMsgs, snpMsgs, rspMsgs, datMsgs,
    dataVer
>>

(***************************************************************************)
(* Initial State                                                           *)
(***************************************************************************)

Init ==
    /\ hnfState         = [n \in Nodes |-> "H_IDLE"]
    /\ hnfPendingReq    = [n \in Nodes |-> "NONE"]
    /\ hnfAwaiting      = [n \in Nodes |-> AwaitNone]
    /\ rnfState         = [n \in Nodes |-> "IDLE"]
    /\ rnfCompUCSeen    = [n \in Nodes |-> FALSE]
    /\ rnfCompAckSent   = [n \in Nodes |-> FALSE]
    /\ snfBusy          = [n \in Nodes |-> FALSE]
    /\ cpuReq           = [n \in Nodes |-> "NONE"]
    /\ reqMsgs          = <<>>
    /\ snpMsgs          = <<>>
    /\ rspMsgs          = <<>>
    /\ datMsgs          = <<>>
    /\ dataVer          = [n \in Nodes |-> 0]

(***************************************************************************)
(* Helpers                                                                 *)
(***************************************************************************)

TailSeq(q) == IF Len(q) <= 1 THEN <<>> ELSE SubSeq(q, 2, Len(q))

ReqMsg(k, n, t) == [src |-> n, dst |-> n, kind |-> k, txn |-> t, ver |-> 0]
SnpMsg(k, n, t) == [src |-> n, dst |-> n, kind |-> k, txn |-> t, ver |-> 0]
RspMsg(k, n, t) == [src |-> n, dst |-> n, kind |-> k, txn |-> t, ver |-> 0]
DatMsg(k, n, t, v) == [src |-> n, dst |-> n, kind |-> k, txn |-> t, ver |-> v]

Quiescent ==
    /\ reqMsgs = <<>>
    /\ snpMsgs = <<>>
    /\ rspMsgs = <<>>
    /\ datMsgs = <<>>
    /\ \A n \in Nodes:
        /\ hnfState[n] = "H_IDLE"
        /\ hnfPendingReq[n] = "NONE"
        /\ hnfAwaiting[n] = AwaitNone
        /\ rnfState[n] \in StableRnfStates
        /\ ~snfBusy[n]
        /\ cpuReq[n] = "NONE"

(***************************************************************************)
(* Actions: CPU injection                                                  *)
(***************************************************************************)

CpuReadShared(n) ==
    /\ n \in Nodes
    /\ cpuReq[n] = "NONE"
    /\ hnfState[n] = "H_IDLE"
    /\ cpuReq' = [cpuReq EXCEPT ![n] = "RS"]
    /\ UNCHANGED <<hnfState, hnfPendingReq, hnfAwaiting, rnfState,
                   rnfCompUCSeen, rnfCompAckSent, snfBusy,
                   reqMsgs, snpMsgs, rspMsgs, datMsgs, dataVer>>

CpuReadUnique(n) ==
    /\ n \in Nodes
    /\ cpuReq[n] = "NONE"
    /\ hnfState[n] = "H_IDLE"
    /\ cpuReq' = [cpuReq EXCEPT ![n] = "RU"]
    /\ UNCHANGED <<hnfState, hnfPendingReq, hnfAwaiting, rnfState,
                   rnfCompUCSeen, rnfCompAckSent, snfBusy,
                   reqMsgs, snpMsgs, rspMsgs, datMsgs, dataVer>>

(***************************************************************************)
(* Actions: HN-F miss path                                                 *)
(***************************************************************************)

HnfMissToEpSnf(n) ==
    /\ n \in Nodes
    /\ cpuReq[n] \in {"RS", "RU"}
    /\ hnfState[n] = "H_IDLE"
    /\ LET k == IF cpuReq[n] = "RS" THEN "MISS_RS" ELSE "MISS_RU"
           t == dataVer[n]
       IN /\ hnfState' = [hnfState EXCEPT ![n] = "H_WAIT_SNF"]
          /\ hnfPendingReq' = [hnfPendingReq EXCEPT ![n] = cpuReq[n]]
          /\ hnfAwaiting' = [hnfAwaiting EXCEPT ![n] = [@ EXCEPT !.snf = TRUE]]
          /\ cpuReq' = [cpuReq EXCEPT ![n] = "NONE"]
          /\ reqMsgs' = Append(reqMsgs, ReqMsg(k, n, t))
    /\ UNCHANGED <<rnfState, rnfCompUCSeen, rnfCompAckSent,
                   snfBusy, snpMsgs, rspMsgs, datMsgs, dataVer>>

EpSnfForward(n) ==
    /\ n \in Nodes
    /\ Len(reqMsgs) > 0
    /\ hnfAwaiting[n].snf
    /\ ~snfBusy[n]
    /\ reqMsgs[1].dst = n
    /\ reqMsgs[1].kind \in {"MISS_RS", "MISS_RU"}
    /\ LET m == reqMsgs[1]
           fk == IF m.kind = "MISS_RS" THEN "FWD_RS" ELSE "FWD_RU"
       IN /\ reqMsgs' = Append(TailSeq(reqMsgs), ReqMsg(fk, n, m.txn))
          /\ hnfState' = [hnfState EXCEPT ![n] = "H_WAIT_SNP"]
          /\ hnfAwaiting' = [hnfAwaiting EXCEPT ![n] = [@ EXCEPT !.snf = FALSE]]
          /\ snfBusy' = [snfBusy EXCEPT ![n] = TRUE]
    /\ UNCHANGED <<hnfPendingReq, rnfState, rnfCompUCSeen, rnfCompAckSent,
                   cpuReq, snpMsgs, rspMsgs, datMsgs, dataVer>>

HnfSnoopEpRnf(n) ==
    /\ n \in Nodes
    /\ Len(reqMsgs) > 0
    /\ snfBusy[n]
    /\ hnfState[n] = "H_WAIT_SNP"
    /\ reqMsgs[1].dst = n
    /\ reqMsgs[1].kind \in {"FWD_RS", "FWD_RU"}
    /\ LET m == reqMsgs[1]
           sk == IF m.kind = "FWD_RS" THEN "SNP_RS"
                 ELSE IF rnfState[n] = "HAVE_SC" THEN "SNP_CU"
                 ELSE "SNP_RU"
       IN /\ reqMsgs' = TailSeq(reqMsgs)
          /\ snpMsgs' = Append(snpMsgs, SnpMsg(sk, n, m.txn))
          /\ hnfAwaiting' = [hnfAwaiting EXCEPT ![n] = [@ EXCEPT !.snp = TRUE]]
          /\ snfBusy' = [snfBusy EXCEPT ![n] = FALSE]
    /\ UNCHANGED <<hnfState, hnfPendingReq, rnfState, rnfCompUCSeen,
                   rnfCompAckSent, cpuReq, rspMsgs, datMsgs, dataVer>>

(***************************************************************************)
(* Actions: EP-RNF start / closure                                         *)
(***************************************************************************)

EpRnfStartRS(n) ==
    /\ n \in Nodes
    /\ Len(snpMsgs) > 0
    /\ snpMsgs[1].dst = n
    /\ snpMsgs[1].kind = "SNP_RS"
    /\ rnfState[n] \in StableRnfStates
    /\ LET m == snpMsgs[1]
       IN /\ snpMsgs' = TailSeq(snpMsgs)
          /\ rspMsgs' = Append(rspMsgs, RspMsg("COMP_UC", n, m.txn))
          /\ rnfState' = [rnfState EXCEPT ![n] = "PENDING_RS"]
          /\ hnfState' = [hnfState EXCEPT ![n] = "H_WAIT_COMP"]
          /\ hnfAwaiting' = [hnfAwaiting EXCEPT ![n] = [@ EXCEPT !.snp = FALSE, !.comp = TRUE]]
    /\ UNCHANGED <<hnfPendingReq, rnfCompUCSeen, rnfCompAckSent,
                   snfBusy, cpuReq, reqMsgs, datMsgs, dataVer>>

EpRnfStartCU(n) ==
    /\ n \in Nodes
    /\ Len(snpMsgs) > 0
    /\ snpMsgs[1].dst = n
    /\ snpMsgs[1].kind = "SNP_CU"
    /\ rnfState[n] = "HAVE_SC"
    /\ LET m == snpMsgs[1]
       IN /\ snpMsgs' = TailSeq(snpMsgs)
          /\ rspMsgs' = Append(rspMsgs, RspMsg("COMP_UC", n, m.txn))
          /\ rnfState' = [rnfState EXCEPT ![n] = "PENDING_CU"]
          /\ hnfState' = [hnfState EXCEPT ![n] = "H_WAIT_COMP"]
          /\ hnfAwaiting' = [hnfAwaiting EXCEPT ![n] = [@ EXCEPT !.snp = FALSE, !.comp = TRUE]]
    /\ UNCHANGED <<hnfPendingReq, rnfCompUCSeen, rnfCompAckSent,
                   snfBusy, cpuReq, reqMsgs, datMsgs, dataVer>>

EpRnfStartRU(n) ==
    /\ n \in Nodes
    /\ Len(snpMsgs) > 0
    /\ snpMsgs[1].dst = n
    /\ snpMsgs[1].kind = "SNP_RU"
    /\ rnfState[n] \in {"IDLE", "HAVE_UC", "HAVE_UD"}
    /\ LET m == snpMsgs[1]
       IN /\ snpMsgs' = TailSeq(snpMsgs)
          /\ rspMsgs' = Append(rspMsgs, RspMsg("COMP_UC", n, m.txn))
          /\ rnfState' = [rnfState EXCEPT ![n] = "PENDING_RU"]
          /\ hnfState' = [hnfState EXCEPT ![n] = "H_WAIT_COMP"]
          /\ hnfAwaiting' = [hnfAwaiting EXCEPT ![n] = [@ EXCEPT !.snp = FALSE, !.comp = TRUE]]
    /\ UNCHANGED <<hnfPendingReq, rnfCompUCSeen, rnfCompAckSent,
                   snfBusy, cpuReq, reqMsgs, datMsgs, dataVer>>

CompUCArrives(n) ==
    /\ n \in Nodes
    /\ Len(rspMsgs) > 0
    /\ rspMsgs[1].dst = n
    /\ rspMsgs[1].kind = "COMP_UC"
    /\ hnfAwaiting[n].comp
    /\ rspMsgs' = TailSeq(rspMsgs)
    /\ rnfCompUCSeen' = [rnfCompUCSeen EXCEPT ![n] = TRUE]
    /\ hnfAwaiting' = [hnfAwaiting EXCEPT ![n] = [@ EXCEPT !.comp = FALSE]]
    /\ UNCHANGED <<hnfState, hnfPendingReq, rnfState, rnfCompAckSent,
                   snfBusy, cpuReq, reqMsgs, snpMsgs, datMsgs, dataVer>>

CompAckSent(n) ==
    /\ n \in Nodes
    /\ rnfCompUCSeen[n]
    /\ ~rnfCompAckSent[n]
    /\ reqMsgs' = Append(reqMsgs, ReqMsg("COMP_ACK", n, dataVer[n]))
    /\ rnfCompAckSent' = [rnfCompAckSent EXCEPT ![n] = TRUE]
    /\ hnfState' = [hnfState EXCEPT ![n] = "H_IDLE"]
    /\ hnfPendingReq' = [hnfPendingReq EXCEPT ![n] = "NONE"]
    /\ hnfAwaiting' = [hnfAwaiting EXCEPT ![n] = AwaitNone]
    /\ UNCHANGED <<rnfState, rnfCompUCSeen, snfBusy, cpuReq,
                   snpMsgs, rspMsgs, datMsgs, dataVer>>

EpRnfCallback(n) ==
    /\ n \in Nodes
    /\ Len(reqMsgs) > 0
    /\ reqMsgs[1].dst = n
    /\ reqMsgs[1].kind = "COMP_ACK"
    /\ rnfCompUCSeen[n]
    /\ rnfCompAckSent[n]
    /\ rnfState[n] \in PendingRnfStates
    /\ reqMsgs' = TailSeq(reqMsgs)
    /\ rnfState' = [rnfState EXCEPT ![n] =
          IF @ = "PENDING_RS" THEN "HAVE_SC"
          ELSE IF @ = "PENDING_CU" THEN "HAVE_UC"
          ELSE "HAVE_UD"]
    /\ rnfCompUCSeen' = [rnfCompUCSeen EXCEPT ![n] = FALSE]
    /\ rnfCompAckSent' = [rnfCompAckSent EXCEPT ![n] = FALSE]
    /\ UNCHANGED <<hnfState, hnfPendingReq, hnfAwaiting, snfBusy,
                   cpuReq, snpMsgs, rspMsgs, datMsgs, dataVer>>

(***************************************************************************)
(* Actions: writeback                                                      *)
(***************************************************************************)

WriteBackRnf(n) ==
    /\ n \in Nodes
    /\ rnfState[n] = "HAVE_UD"
    /\ ~hnfAwaiting[n].wb
    /\ dataVer[n] < MaxTxn
    /\ datMsgs' = Append(datMsgs, DatMsg("WB", n, dataVer[n], dataVer[n] + 1))
    /\ dataVer' = [dataVer EXCEPT ![n] = @ + 1]
    /\ rnfState' = [rnfState EXCEPT ![n] = "IDLE"]
    /\ hnfState' = [hnfState EXCEPT ![n] = "H_WAIT_WB"]
    /\ hnfAwaiting' = [hnfAwaiting EXCEPT ![n] = [@ EXCEPT !.wb = TRUE]]
    /\ UNCHANGED <<hnfPendingReq, rnfCompUCSeen, rnfCompAckSent,
                   snfBusy, cpuReq, reqMsgs, snpMsgs, rspMsgs>>

HnfCompleteWriteBack(n) ==
    /\ n \in Nodes
    /\ Len(datMsgs) > 0
    /\ datMsgs[1].dst = n
    /\ datMsgs[1].kind = "WB"
    /\ hnfAwaiting[n].wb
    /\ datMsgs[1].ver = dataVer[n]
    /\ datMsgs' = TailSeq(datMsgs)
    /\ hnfState' = [hnfState EXCEPT ![n] = "H_IDLE"]
    /\ hnfAwaiting' = [hnfAwaiting EXCEPT ![n] = [@ EXCEPT !.wb = FALSE]]
    /\ UNCHANGED <<hnfPendingReq, rnfState, rnfCompUCSeen, rnfCompAckSent,
                   snfBusy, cpuReq, reqMsgs, snpMsgs, rspMsgs, dataVer>>

(***************************************************************************)
(* Next / Spec                                                              *)
(***************************************************************************)

Next ==
    \E n \in Nodes:
        CpuReadShared(n)
     \/ CpuReadUnique(n)
     \/ HnfMissToEpSnf(n)
     \/ EpSnfForward(n)
     \/ HnfSnoopEpRnf(n)
     \/ EpRnfStartRS(n)
     \/ EpRnfStartCU(n)
     \/ EpRnfStartRU(n)
     \/ CompUCArrives(n)
     \/ CompAckSent(n)
     \/ EpRnfCallback(n)
     \/ WriteBackRnf(n)
     \/ HnfCompleteWriteBack(n)

Spec == Init /\ [][Next]_vars

(***************************************************************************)
(* Invariants                                                              *)
(***************************************************************************)

NodeCanStep(n) ==
       ENABLED CpuReadShared(n)
    \/ ENABLED CpuReadUnique(n)
    \/ ENABLED HnfMissToEpSnf(n)
    \/ ENABLED EpSnfForward(n)
    \/ ENABLED HnfSnoopEpRnf(n)
    \/ ENABLED EpRnfStartRS(n)
    \/ ENABLED EpRnfStartCU(n)
    \/ ENABLED EpRnfStartRU(n)
    \/ ENABLED CompUCArrives(n)
    \/ ENABLED CompAckSent(n)
    \/ ENABLED EpRnfCallback(n)
    \/ ENABLED WriteBackRnf(n)
    \/ ENABLED HnfCompleteWriteBack(n)

NoDeadlock ==
    Quiescent \/ (\E n \in Nodes: NodeCanStep(n))

DataIntegrity ==
    /\ \A n \in Nodes: dataVer[n] \in 0..MaxTxn
    /\ \A i \in 1..Len(datMsgs):
        LET m == datMsgs[i]
        IN /\ m.kind = "WB"
           /\ m.src = m.dst
           /\ m.ver \in 1..MaxTxn
           /\ m.ver = dataVer[m.src]

SnoopCorrectness ==
    \A i \in 1..Len(snpMsgs):
        LET m == snpMsgs[i]
        IN /\ m.kind \in SnpKinds
           /\ m.src = m.dst
           /\ hnfState[m.dst] = "H_WAIT_SNP"
           /\ hnfPendingReq[m.dst] /= "NONE"
           /\ (m.kind = "SNP_RS" => hnfPendingReq[m.dst] = "RS")
           /\ (m.kind \in {"SNP_CU", "SNP_RU"} => hnfPendingReq[m.dst] = "RU")

CallbackOrdering ==
    \A n \in Nodes:
        /\ (rnfCompAckSent[n] => rnfCompUCSeen[n])
        /\ ((rnfState[n] \in {"HAVE_SC", "HAVE_UC", "HAVE_UD"} /\ rnfCompUCSeen[n])
             => rnfCompAckSent[n])

THEOREM Spec => []NoDeadlock
THEOREM Spec => []DataIntegrity
THEOREM Spec => []SnoopCorrectness
THEOREM Spec => []CallbackOrdering

=============================================================================
