---------------------------- MODULE ep_intra_node_single ----------------------------
(**
 * TLA+ spec of the EP intra-node single-socket coherence protocol (scheme_v4).
 *
 * Scope: one cache line, bounded CPUs, HN-F as sole snoop authority.
 * Covers: CPU→HN-F→SNF→Backend→grant, HN-F→EP-RNF→CompUC→CompAck→callback,
 *         eviction→writeback→DRAM persistence.
 *
 * EPRNF = NumCPUs is used as a sentinel integer for remote sharer registration.
 * hnfOwner = -1 means no owner (NONE sentinel).
 *)
EXTENDS Naturals, Integers, FiniteSets, Sequences, TLC

(***************************************************************************)
(* Constants & derived sets                                                 *)
(***************************************************************************)
CONSTANTS NumCPUs, MaxDataVersion
ASSUME NumCPUs >= 2
ASSUME MaxDataVersion >= 1

CPU       == 0 .. (NumCPUs - 1)
EPRNF     == NumCPUs
Sharer    == CPU \cup {EPRNF}
DataV     == 0 .. MaxDataVersion

(***************************************************************************)
(* Type sets                                                                *)
(***************************************************************************)
CpuSt == {"I", "SC", "UC", "UD", "P_RS", "P_RU", "P_EVICT"}
HnfSt == {"I","SC","UC","UD",
          "TBE_ALLOC","WAIT_SNF","WAIT_BACKEND",
          "WAIT_SNP_RS","WAIT_SNP_CU","WAIT_SNP_RU",
          "WAIT_COMP_UC","WAIT_COMP_ACK","WAIT_GRANT","WAIT_WB"}
RnfSt == {"IDLE","HAVE_SC","HAVE_UC","HAVE_UD",
          "PENDING_RS","PENDING_CU","PENDING_RU"}
SnfSt == {"IDLE","FORWARDING"}
BkndSt == {"IDLE","WAITING_GRANT","WAITING_CLEAR"}

MsgKind == {"RS","RU","EVICT","SNP_RS","SNP_CU","SNP_RU",
            "COMP_UC","COMP_ACK","CPU_GRANT","SNF_GRANT","WB","CLEAR"}

(***************************************************************************)
(* Variables                                                                *)
(***************************************************************************)
VARIABLES
    cpuState, cpuData, cpuPendingData,
    hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
    hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
    hnfTbeNeedData, hnfTbeGrantData,
    hnfPendingOwnerUpdate,
    rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
    snfState, backendState, backendGrantData,
    dramData, dramWritten, latestGlobalWrite,
    reqQ, snpQ, rspQ, datQ

vars == <<cpuState, cpuData, cpuPendingData,
          hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
          hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
          hnfTbeNeedData, hnfTbeGrantData,
          hnfPendingOwnerUpdate,
          rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
          snfState, backendState, backendGrantData,
          dramData, dramWritten, latestGlobalWrite,
          reqQ, snpQ, rspQ, datQ>>

(***************************************************************************)
(* Init                                                                     *)
(***************************************************************************)
Init ==
    /\ cpuState    = [c \in CPU |-> "I"]
    /\ cpuData     = [c \in CPU |-> 0]
    /\ cpuPendingData = [c \in CPU |-> 0]
    /\ hnfState    = "I"
    /\ hnfData     = 0
    /\ hnfCacheLine = FALSE
    /\ hnfOwner    = -1
    /\ hnfSharers  = {}
    /\ hnfTbeValid = FALSE
    /\ hnfTbeOp    = "NONE"
    /\ hnfTbePhase = "NONE"
    /\ hnfTbeRequester = -1
    /\ hnfTbeNeedData = FALSE
    /\ hnfTbeGrantData = 0
    /\ hnfPendingOwnerUpdate = FALSE
    /\ rnfState    = "IDLE"
    /\ rnfCompUCSeen = FALSE
    /\ rnfCompAckSent = FALSE
    /\ rnfCallbackArmed = FALSE
    /\ snfState    = "IDLE"
    /\ backendState = "IDLE"
    /\ backendGrantData = 0
    /\ dramData    = 0
    /\ dramWritten = FALSE
    /\ latestGlobalWrite = 0
    /\ reqQ = <<>>
    /\ snpQ = <<>>
    /\ rspQ = <<>>
    /\ datQ = <<>>

(***************************************************************************)
(* Message helpers                                                          *)
(***************************************************************************)
TailSeq(q) == IF Len(q) <= 1 THEN <<>> ELSE SubSeq(q, 2, Len(q))

Msg(kind, dst, data) == [kind |-> kind, dst |-> dst, data |-> data]
SnpMsg(kind, dst) == [kind |-> kind, dst |-> dst, data |-> 0]

(***************************************************************************)
(* CPU actions                                                              *)
(***************************************************************************)

CpuLoad(cpu) ==
    /\ cpuState[cpu] = "I"
    /\ cpuState' = [cpuState EXCEPT ![cpu] = "P_RS"]
    /\ reqQ' = Append(reqQ, Msg("RS", cpu, 0))
    /\ UNCHANGED <<cpuData, cpuPendingData, hnfState, hnfData, hnfCacheLine,
                   hnfOwner, hnfSharers, hnfTbeValid, hnfTbeOp, hnfTbePhase,
                   hnfTbeRequester, hnfTbeNeedData, hnfTbeGrantData,
                   hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite, snpQ, rspQ, datQ>>

CpuStore(cpu, data) ==
    /\ data \in DataV
    /\ cpuState[cpu] \in {"I","SC"}
    /\ cpuState'        = [cpuState EXCEPT ![cpu] = "P_RU"]
    /\ cpuPendingData'  = [cpuPendingData EXCEPT ![cpu] = data]
    /\ reqQ' = Append(reqQ, Msg("RU", cpu, data))
    /\ UNCHANGED <<cpuData, hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData,
                   hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite, snpQ, rspQ, datQ>>

CpuStoreHit(cpu, data) ==
    \* Direct upgrade: CPU already has UC/UD, commits store locally.
    \* Updates both cpuData and hnfData (owner store is globally visible).
    /\ data \in DataV
    /\ cpuState[cpu] \in {"UC","UD"}
    /\ cpuState'        = [cpuState EXCEPT ![cpu] = "UD"]
    /\ cpuData'         = [cpuData  EXCEPT ![cpu] = data]
    /\ hnfData'         = data
    /\ latestGlobalWrite' = data
    /\ UNCHANGED <<cpuPendingData, hnfState, hnfCacheLine, hnfOwner,
                   hnfSharers, hnfTbeValid, hnfTbeOp, hnfTbePhase,
                   hnfTbeRequester, hnfTbeNeedData, hnfTbeGrantData,
                   hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, reqQ, snpQ, rspQ, datQ>>

CpuEvict(cpu) ==
    /\ cpuState[cpu] \in {"SC","UC","UD"}
    /\ cpuState' = [cpuState EXCEPT ![cpu] = "P_EVICT"]
    /\ reqQ' = Append(reqQ, Msg("EVICT", cpu, cpuData[cpu]))
    /\ UNCHANGED <<cpuData, cpuPendingData, hnfState, hnfData, hnfCacheLine,
                   hnfOwner, hnfSharers, hnfTbeValid, hnfTbeOp, hnfTbePhase,
                   hnfTbeRequester, hnfTbeNeedData, hnfTbeGrantData,
                   hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite, snpQ, rspQ, datQ>>

(***************************************************************************)
(* HN-F actions: allocate TBE, hit/miss dispatch                            *)
(***************************************************************************)

HnfAcceptReq ==
    /\ Len(reqQ) > 0
    /\ ~hnfTbeValid
    /\ LET m == reqQ[1]
           cpu == m.dst
       IN  /\ \* Guard: CPU must still be in the expected pending state.
              (m.kind = "RS"    => cpuState[cpu] = "P_RS")
           /\ (m.kind = "RU"    => cpuState[cpu] = "P_RU")
           /\ (m.kind = "EVICT" => cpuState[cpu] = "P_EVICT")
           /\ hnfTbeValid' = TRUE
           /\ hnfTbeOp'    = IF m.kind = "EVICT" THEN "EVICT"
                             ELSE IF m.kind = "RS" THEN "RS" ELSE "RU"
           /\ hnfTbeRequester' = cpu
           /\ hnfTbeNeedData'  = TRUE
           /\ hnfTbeGrantData' = IF m.kind = "RU" THEN m.data ELSE 0
           /\ reqQ' = TailSeq(reqQ)
           /\ IF (hnfState = "I") \/ (hnfState = "SC" /\ m.kind = "RS" /\ hnfOwner = -1)
              THEN /\ hnfTbePhase' = "WAIT_SNF"
                   /\ hnfState' = hnfState
              ELSE IF hnfState \in {"SC","UC","UD"} /\ m.kind \in {"RS","RU"} /\ cpu = hnfOwner
              THEN /\ hnfTbePhase' = "WAIT_GRANT"   \* hit: serve directly
                   /\ hnfState' = hnfState
              ELSE IF hnfState = "SC" /\ m.kind = "RU"
                   /\ (hnfSharers \ {cpu}) /= {}
              THEN /\ hnfTbePhase' = "WAIT_SNP_CU"  \* need to invalidate other sharers
                   /\ hnfState' = hnfState
              ELSE IF hnfState = "SC" /\ m.kind = "RU"
                   /\ (hnfSharers \ {cpu}) = {}
              THEN /\ hnfTbePhase' = "WAIT_GRANT"   \* requester is only sharer
                   /\ hnfState' = hnfState
              ELSE IF hnfState \in {"UC","UD"} /\ m.kind \in {"RS","RU"} /\ cpu /= hnfOwner
              THEN /\ hnfTbePhase' = "WAIT_SNP_RU"   \* snoop current owner
                   /\ hnfState' = hnfState
              ELSE IF m.kind = "EVICT"
              THEN /\ hnfTbePhase' = IF hnfState \in {"UC","UD"} /\ cpu = hnfOwner
                                    THEN "WAIT_WB" ELSE "WAIT_GRANT"
                   /\ hnfState' = hnfState
              ELSE /\ hnfTbePhase' = "WAIT_SNF"
                   /\ hnfState' = hnfState
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData,
                   hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite, snpQ, rspQ, datQ>>

HnfDropStaleReq ==
    \* Drop a request whose CPU is no longer in the expected pending state.
    /\ Len(reqQ) > 0
    /\ ~hnfTbeValid
    /\ LET m == reqQ[1]
           cpu == m.dst
       IN  /\ \/ (m.kind = "RS"    /\ cpuState[cpu] /= "P_RS")
              \/ (m.kind = "RU"    /\ cpuState[cpu] /= "P_RU")
              \/ (m.kind = "EVICT" /\ cpuState[cpu] /= "P_EVICT")
           /\ reqQ' = TailSeq(reqQ)
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData,
                   hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite, snpQ, rspQ, datQ>>

(***************************************************************************)
(* HN-F hit: serve data to CPU directly                                     *)
(***************************************************************************)

HnfHitServe ==
    /\ hnfTbeValid
    /\ hnfTbePhase = "WAIT_GRANT"
    /\ hnfTbeOp \in {"RS","RU"}
    /\ hnfState \in {"SC","UC","UD"}
    /\ LET cpu == hnfTbeRequester
       IN  /\ cpuState' = [cpuState EXCEPT ![cpu] =
                IF hnfTbeOp = "RS" THEN hnfState
                ELSE IF hnfState \in {"UC","UD"} /\ cpu = hnfOwner THEN "UD"
                ELSE "I"]   \* should not happen if guards correct
           /\ cpuData'  = [cpuData  EXCEPT ![cpu] = hnfData]
           /\ hnfTbeValid' = FALSE
           /\ hnfTbeOp'    = "NONE"
           /\ hnfTbePhase' = "NONE"
           /\ hnfTbeRequester' = -1
           /\ hnfTbeNeedData'  = FALSE
           /\ UNCHANGED <<cpuPendingData, hnfState, hnfData, hnfCacheLine,
                          hnfOwner, hnfSharers, hnfTbeGrantData,
                          hnfPendingOwnerUpdate,
                          rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                          snfState, backendState, backendGrantData,
                          dramData, dramWritten, latestGlobalWrite,
                          reqQ, snpQ, rspQ, datQ>>

(***************************************************************************)
(* HN-F miss: enqueue to SNF                                                *)
(***************************************************************************)

HnfMissToSnf ==
    \* HN-F miss path: transition to waiting for backend grant.
    \* SNF forwarding is captured by snfState → FORWARDING.
    /\ hnfTbeValid
    /\ hnfTbePhase = "WAIT_SNF"
    /\ hnfTbePhase' = "WAIT_BACKEND"
    /\ snfState' = "FORWARDING"
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData,
                   hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite, snpQ, rspQ, datQ, reqQ>>

(***************************************************************************)
(* SNF forward to backend                                                   *)
(***************************************************************************)

SnfForward ==
    /\ snfState = "FORWARDING"
    /\ backendState = "IDLE"
    /\ backendState' = "WAITING_GRANT"
    /\ snfState' = "IDLE"
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData,
                   hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, datQ>>

(***************************************************************************)
(* Backend grant arrives                                                    *)
(***************************************************************************)

BackendGrant ==
    /\ backendState = "WAITING_GRANT"
    /\ \E gd \in DataV : /\ backendGrantData' = gd
                          /\ datQ' = Append(datQ, Msg("SNF_GRANT", 0, gd))
    /\ backendState' = "IDLE"
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData,
                   hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ>>

(***************************************************************************)
(* HN-F installs SNF grant → fill cache line, deliver to CPU                *)
(***************************************************************************)

HnfInstallGrant ==
    /\ hnfTbeValid
    /\ hnfTbePhase = "WAIT_BACKEND"
    /\ hnfTbeOp \in {"RS","RU"}       \* EVICT goes through writeback path
    /\ Len(datQ) > 0
    /\ datQ[1].kind = "SNF_GRANT"
    /\ LET gd == datQ[1].data
           cpu == hnfTbeRequester
       IN  /\ datQ' = TailSeq(datQ)
           /\ hnfCacheLine' = TRUE
           /\ hnfData' = gd
           /\ IF hnfTbeOp = "RS"
              THEN /\ hnfState'   = "SC"
                   /\ hnfOwner'   = -1
                   /\ hnfSharers' = hnfSharers \cup {cpu}
                   /\ cpuState'   = [cpuState EXCEPT ![cpu] = "SC"]
                   /\ cpuData'    = [cpuData  EXCEPT ![cpu] = gd]
                   /\ latestGlobalWrite' = latestGlobalWrite
              ELSE /\ hnfState'   = "UD"
                   /\ hnfOwner'   = cpu
                   /\ hnfSharers' = {cpu}
                   /\ cpuState'   = [cpuState EXCEPT ![cpu] = "UD"]
                   /\ cpuData'    = [cpuData  EXCEPT ![cpu] = gd]
                   /\ latestGlobalWrite' = gd
           /\ hnfTbeValid' = FALSE
           /\ hnfTbeOp'    = "NONE"
           /\ hnfTbePhase' = "NONE"
           /\ hnfTbeRequester' = -1
           /\ hnfTbeNeedData'  = FALSE
           /\ UNCHANGED <<cpuPendingData, hnfTbeGrantData,
                          hnfPendingOwnerUpdate,
                          rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                          snfState, backendState, backendGrantData,
                          dramData, dramWritten,
                          reqQ, snpQ, rspQ>>

(***************************************************************************)
(* HN-F snoops current owner (RU from non-owner while line UC/UD)           *)
(***************************************************************************)

HnfSnoopOwnerRU ==
    /\ hnfTbeValid
    /\ hnfTbePhase = "WAIT_SNP_RU"
    /\ hnfOwner \in CPU
    /\ LET owner == hnfOwner
       IN  /\ cpuState' = [cpuState EXCEPT ![owner] = "I"]
           /\ hnfState' = "I"
           /\ hnfSharers' = {}
           /\ hnfOwner'   = -1
           /\ hnfCacheLine' = FALSE
           /\ hnfTbePhase' = "WAIT_SNF"
    /\ UNCHANGED <<cpuData, cpuPendingData,
                   hnfData,
                   hnfTbeValid, hnfTbeOp, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData,
                   hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, datQ>>

(***************************************************************************)
(* HN-F snoop to EP-RNF (CU path: CleanUnique invalidate)                   *)
(***************************************************************************)

HnfSnoopRnfCleanUnique ==
    /\ hnfTbeValid
    /\ hnfTbePhase = "WAIT_SNP_CU"
    /\ EPRNF \in hnfSharers
    /\ snpQ' = Append(snpQ, SnpMsg("SNP_CU", 0))
    /\ hnfTbePhase' = "WAIT_COMP_UC"
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData,
                   hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, rspQ, datQ>>

HnfInvalidateCpuSharers ==
    \* Invalidate CPU sharers (except requester) during SC→RU upgrade.
    \* If EPRNF is also a sharer, that must be handled first by HnfSnoopRnfCleanUnique.
    /\ hnfTbeValid
    /\ hnfTbePhase = "WAIT_SNP_CU"
    /\ ~(EPRNF \in hnfSharers)
    /\ LET others == hnfSharers \ {hnfTbeRequester}
           newSh == hnfSharers \ others
       IN  /\ cpuState' = [c \in CPU |->
               IF c \in others THEN "I" ELSE cpuState[c]]
           /\ hnfSharers' = newSh
           /\ hnfState' = IF newSh = {} THEN "I" ELSE hnfState
           /\ hnfOwner' = IF newSh = {} THEN -1 ELSE hnfOwner
           /\ hnfCacheLine' = (newSh /= {})
           /\ hnfTbePhase' = IF newSh = {} THEN "WAIT_SNF" ELSE "WAIT_GRANT"
    /\ UNCHANGED <<cpuData, cpuPendingData,
                   hnfData,
                   hnfTbeValid, hnfTbeOp, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData,
                   hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, datQ>>

(***************************************************************************)
(* EP-RNF receives snoop and starts CleanUnique                             *)
(***************************************************************************)

EpRnfStartCleanUnique ==
    /\ Len(snpQ) > 0
    /\ snpQ[1].kind = "SNP_CU"
    /\ rnfState = "HAVE_SC"
    /\ rnfState' = "PENDING_CU"
    /\ rnfCallbackArmed' = TRUE
    /\ rspQ' = Append(rspQ, Msg("COMP_UC", 0, 0))
    /\ snpQ' = TailSeq(snpQ)
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData,
                   hnfPendingOwnerUpdate,
                   rnfCompUCSeen, rnfCompAckSent,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, datQ>>

(***************************************************************************)
(* HN-F receives CompUC from EP-RNF                                         *)
(***************************************************************************)

HnfRecvCompUC ==
    /\ hnfTbeValid
    /\ hnfTbePhase = "WAIT_COMP_UC"
    /\ Len(rspQ) > 0
    /\ rspQ[1].kind = "COMP_UC"
    /\ rspQ' = TailSeq(rspQ)
    /\ hnfTbePhase' = "WAIT_COMP_ACK"
    /\ rnfCompUCSeen' = TRUE
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData,
                   hnfPendingOwnerUpdate,
                   rnfState, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, datQ>>

(***************************************************************************)
(* EP-RNF sends CompAck                                                     *)
(***************************************************************************)

EpRnfSendCompAck ==
    /\ rnfCompUCSeen
    /\ ~rnfCompAckSent
    /\ rnfCompAckSent' = TRUE
    /\ rspQ' = Append(rspQ, Msg("COMP_ACK", 0, 0))
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData,
                   hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, datQ>>

(***************************************************************************)
(* HN-F receives CompAck                                                    *)
(***************************************************************************)

HnfRecvCompAck ==
    /\ hnfTbeValid
    /\ hnfTbePhase = "WAIT_COMP_ACK"
    /\ Len(rspQ) > 0
    /\ rspQ[1].kind = "COMP_ACK"
    /\ rspQ' = TailSeq(rspQ)
    /\ hnfSharers' = hnfSharers \ {EPRNF}
    /\ hnfTbePhase' = "WAIT_GRANT"
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData,
                   hnfState, hnfData, hnfCacheLine, hnfOwner,
                   hnfTbeValid, hnfTbeOp, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData,
                   hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, datQ>>

(***************************************************************************)
(* EP-RNF callback: after CompUC + CompAck, state transitions to target     *)
(***************************************************************************)

EpRnfCallback ==
    /\ rnfCompUCSeen
    /\ rnfCompAckSent
    /\ rnfCallbackArmed
    /\ rnfState \in {"PENDING_RS","PENDING_CU","PENDING_RU"}
    /\ rnfState' = IF rnfState = "PENDING_RS" THEN "HAVE_SC"
                   ELSE IF rnfState = "PENDING_CU" THEN "HAVE_UC"
                   ELSE "HAVE_UD"
    /\ rnfCompUCSeen'    = FALSE
    /\ rnfCompAckSent'   = FALSE
    /\ rnfCallbackArmed' = FALSE
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData,
                   hnfPendingOwnerUpdate,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, datQ>>

(***************************************************************************)
(* HN-F writeback to DRAM (eviction from UC/UD owner)                       *)
(***************************************************************************)

HnfWritebackToDram ==
    /\ hnfTbeValid
    /\ hnfTbePhase = "WAIT_WB"
    /\ hnfTbeOp = "EVICT"
    /\ \E v \in DataV : v = cpuData[hnfTbeRequester]
        /\ datQ' = Append(datQ, Msg("WB", 0, v))
        /\ hnfTbePhase' = "WAIT_GRANT"
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData,
                   hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ>>

DramAcceptWriteback ==
    /\ Len(datQ) > 0
    /\ datQ[1].kind = "WB"
    /\ dramData' = datQ[1].data
    /\ dramWritten' = TRUE
    /\ datQ' = TailSeq(datQ)
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData,
                   hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   latestGlobalWrite, reqQ, snpQ, rspQ>>

HnfFinishWriteback ==
    \* Completes eviction: removes CPU from sharers, clears line if empty.
    \* For dirty evictions, dramWritten must be TRUE (data persisted).
    /\ hnfTbeValid
    /\ hnfTbePhase = "WAIT_GRANT"
    /\ hnfTbeOp = "EVICT"
    /\ (dramWritten \/ hnfState \in {"I","SC"})  \* clean eviction or dirty writeback done
    /\ LET cpu == hnfTbeRequester
       IN  /\ hnfCacheLine' = IF hnfState = "SC" /\ hnfSharers /= {cpu} THEN TRUE ELSE FALSE
           /\ hnfSharers'   = hnfSharers \ {cpu}
           /\ hnfState'     = IF hnfSharers' = {} THEN "I" ELSE hnfState
           /\ hnfOwner'     = IF hnfState' = "I" THEN -1 ELSE hnfOwner
           /\ cpuState'     = [cpuState EXCEPT ![cpu] = "I"]
           /\ hnfTbeValid'  = FALSE
           /\ hnfTbeOp'     = "NONE"
           /\ hnfTbePhase'  = "NONE"
           /\ hnfTbeRequester' = -1
           /\ dramWritten'  = FALSE   \* reset after eviction completes
    /\ UNCHANGED <<cpuData, cpuPendingData, hnfData,
                   hnfTbeNeedData, hnfTbeGrantData,
                   hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, latestGlobalWrite,
                   reqQ, snpQ, rspQ, datQ>>

(***************************************************************************)
(* HN-F grant serve from snoop path (after CompAck, line already present)   *)
(***************************************************************************)

HnfGrantAfterSnoop ==
    /\ hnfTbeValid
    /\ hnfTbePhase = "WAIT_GRANT"
    /\ hnfTbeOp \in {"RS","RU"}
    /\ hnfState \in {"SC","UC","UD"}
    /\ LET cpu == hnfTbeRequester
       IN  /\ cpuState' = [cpuState EXCEPT ![cpu] =
                IF hnfTbeOp = "RU" THEN "UD" ELSE hnfState]
           /\ cpuData'  = [cpuData  EXCEPT ![cpu] = hnfData]
           /\ IF hnfTbeOp = "RU"
              THEN /\ hnfState' = "UD"
                   /\ hnfOwner' = cpu
                   /\ hnfSharers' = {cpu}
                   /\ latestGlobalWrite' = hnfData
              ELSE /\ hnfState' = hnfState
                   /\ hnfOwner' = hnfOwner
                   /\ hnfSharers' = hnfSharers \cup {cpu}
                   /\ latestGlobalWrite' = latestGlobalWrite
           /\ hnfTbeValid' = FALSE
           /\ hnfTbeOp'    = "NONE"
           /\ hnfTbePhase' = "NONE"
           /\ hnfTbeRequester' = -1
           /\ hnfTbeNeedData'  = FALSE
    /\ UNCHANGED <<cpuPendingData, hnfData, hnfCacheLine,
                   hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten,
                   reqQ, snpQ, rspQ, datQ>>

(***************************************************************************)
(* Backend Clear handshake (grant retirement)                               *)
(***************************************************************************)

BackendSendClear ==
    /\ backendState = "IDLE"
    /\ backendGrantData > 0
    /\ backendState' = "WAITING_CLEAR"
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData,
                   hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, datQ>>

BackendRecvClearAck ==
    /\ backendState = "WAITING_CLEAR"
    /\ backendState' = "IDLE"
    /\ backendGrantData' = 0
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData,
                   hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, datQ>>

(***************************************************************************)
(* Next / Spec                                                              *)
(***************************************************************************)

Next ==
    \/ \E cpu \in CPU : CpuLoad(cpu)
    \/ \E cpu \in CPU, data \in DataV : CpuStore(cpu, data)
    \/ \E cpu \in CPU, data \in DataV : CpuStoreHit(cpu, data)
    \/ \E cpu \in CPU : CpuEvict(cpu)
    \/ HnfAcceptReq
    \/ HnfDropStaleReq
    \/ HnfHitServe
    \/ HnfMissToSnf
    \/ SnfForward
    \/ BackendGrant
    \/ HnfInstallGrant
    \/ HnfSnoopOwnerRU
    \/ HnfSnoopRnfCleanUnique
    \/ HnfInvalidateCpuSharers
    \/ EpRnfStartCleanUnique
    \/ HnfRecvCompUC
    \/ EpRnfSendCompAck
    \/ HnfRecvCompAck
    \/ EpRnfCallback
    \/ HnfWritebackToDram
    \/ DramAcceptWriteback
    \/ HnfFinishWriteback
    \/ HnfGrantAfterSnoop
    \/ BackendSendClear
    \/ BackendRecvClearAck

Spec == Init /\ [][Next]_vars

(***************************************************************************)
(* Invariants                                                               *)
(***************************************************************************)

TypeOK ==
    /\ \A c \in CPU : cpuState[c] \in CpuSt
    /\ \A c \in CPU : cpuData[c] \in DataV
    /\ hnfState \in HnfSt
    /\ hnfData \in DataV
    /\ hnfCacheLine \in BOOLEAN
    /\ hnfOwner \in Sharer \cup {-1}
    /\ hnfSharers \subseteq Sharer
    /\ rnfState \in RnfSt
    /\ snfState \in SnfSt
    /\ backendState \in BkndSt
    /\ dramData \in DataV
    /\ latestGlobalWrite \in DataV

(* DataIntegrity: after a store, any subsequent load on the same CPU returns
   the stored value. *)
DataIntegrity ==
    \A c \in CPU :
        (cpuState[c] \in {"UC","UD"}) => (cpuData[c] = latestGlobalWrite)

(* Coherence: no two CPUs hold the line in dirty-unique state simultaneously. *)
NoTwoDirtyUniques ==
    Cardinality({c \in CPU : cpuState[c] = "UD"}) <= 1

(* HN-F directory consistency. *)
HnfDirectoryConsistent ==
    /\ (hnfState = "I") => (hnfSharers = {} /\ hnfOwner = -1 /\ ~hnfCacheLine)
    /\ (hnfState = "SC") => (hnfOwner = -1 /\ hnfSharers /= {})
    /\ (hnfState \in {"UC","UD"}) => (hnfSharers = {hnfOwner} /\ hnfOwner \in Sharer)

(* Callback ordering: CompAck only after CompUC; callback only after both. *)
CallbackOrdering ==
    /\ rnfCompAckSent => rnfCompUCSeen
    /\ (rnfState \in {"HAVE_SC","HAVE_UC","HAVE_UD","IDLE"}) => ~rnfCallbackArmed

(* WritebackPersistence: if DRAM has been written, its data equals the latest
   global write (dirty data reached persistence). *)
WritebackPersistence ==
    dramWritten => (dramData = latestGlobalWrite)

(* TBE guard: no fill/snoop processing without valid TBE. *)
TbeValidGuard ==
    (hnfTbePhase \in {"WAIT_BACKEND","WAIT_COMP_UC","WAIT_COMP_ACK","WAIT_GRANT"})
        => hnfTbeValid

(* PendingOwnerUpdate blocks new unique operations. *)
OwnerUpdateBlocksUnique ==
    hnfPendingOwnerUpdate => (\A c \in CPU : cpuState[c] \notin {"P_RU"})

(* No leaked grant: WAITING_CLEAR only when grant data outstanding.
   Being IDLE or WAITING_GRANT with grantData=0 is legitimate. *)
NoLeakedGrant ==
    (backendState /= "WAITING_CLEAR") \/ (backendGrantData > 0)

=============================================================================
