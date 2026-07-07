-------------------------- MODULE ubcc_liveness_nocleanup --------------------------
(***************************************************************************)
(* Contrast experiment for the RECALL-orphan fix.                          *)
(*                                                                          *)
(* This module is the SAME protocol as ubcc_protocol_core EXCEPT the        *)
(* RecallOrphanCleanup safety net is REMOVED from both the transition       *)
(* relation and the fairness. It models the PRE-fix (buggy) system.         *)
(*                                                                          *)
(* Expected result: the liveness property RecallProgress FAILS with a       *)
(* lasso counterexample — a RECALL that reaches WAITING_TARGET_RESP whose    *)
(* RecallResponse never arrives (RecallResponse is intentionally unfair)     *)
(* wedges the PA slot forever, since nothing can free it.                    *)
(*                                                                          *)
(* Contrast with ubcc_liveness.cfg (cleanup present) which PASSES. The two   *)
(* runs together are the machine-checked evidence that the frozen fix        *)
(* (recall_orphan_solution.md) eliminates the FV3-LEAK-001 orphan wedge.     *)
(***************************************************************************)
EXTENDS ubcc_protocol_core

(* Next WITHOUT RecallOrphanCleanup. *)
NextNoCleanup ==
    \/ \E req \in Nodes : \E reqId \in ReqIds : GrantShared(req, reqId)
    \/ \E req \in Nodes : \E wi \in BOOLEAN : \E reqId \in ReqIds : GrantExclusive(req, wi, reqId)
    \/ \E req \in Nodes : \E rt \in ReqType : \E wi \in BOOLEAN : \E reqId \in ReqIds : RecallBarrier(req, rt, wi, reqId)
    \/ RecallResponse
    \/ RecallToGrant
    \* RecallOrphanCleanup deliberately omitted (pre-fix system).
    \/ \E req \in Nodes : \E wi \in BOOLEAN : \E reqId \in ReqIds : InvalidationBarrier(req, wi, reqId)
    \/ \E req \in Nodes : \E wi \in BOOLEAN : \E reqId \in ReqIds : UpgradeBarrier(req, wi, reqId)
    \/ \E node \in Nodes : BarrierAck(node)
    \/ ClearCommit
    \/ UpgradeCommit
    \/ \E node \in Nodes : \E keepAsClean \in BOOLEAN : Writeback(node, keepAsClean)
    \/ \E node \in Nodes : Evict(node)
    \/ TickOnly
    \/ Stutter

(* Same fairness as FairSpec but WITHOUT WF on RecallOrphanCleanup. *)
FairSpecNoCleanup ==
    /\ Init
    /\ [][NextNoCleanup]_Vars
    /\ WF_Vars(RecallToGrant)
    /\ WF_Vars(ClearCommit)
    /\ WF_Vars(UpgradeCommit)
    /\ \A n \in Nodes : WF_Vars(BarrierAck(n))
    /\ WF_Vars(TickOnly)

=============================================================================
