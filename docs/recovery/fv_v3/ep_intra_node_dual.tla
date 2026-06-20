---------------------------- MODULE ep_intra_node_dual ----------------------------
EXTENDS Naturals, Integers, FiniteSets, Sequences, TLC

(***************************************************************************)
(* Constants & derived sets                                                *)
(***************************************************************************)
CONSTANTS NumCPUs, MaxDataVersion, NumSockets
ASSUME NumCPUs = 2
ASSUME NumSockets = 2
ASSUME MaxDataVersion >= 1

Sockets == 0 .. (NumSockets - 1)
CPU     == 0 .. (NumCPUs - 1)
DataV   == 0 .. MaxDataVersion

LineHome == 0
RemoteLatency == 1

CpuSocket(c) == c
RemoteSock(s) == 1 - s

NONE == -1
EPRNF0 == NumCPUs
EPRNF1 == NumCPUs + 1
EPRNF(s) == IF s = 0 THEN EPRNF0 ELSE EPRNF1
Sharer == CPU \cup {EPRNF0, EPRNF1}

IsEPRNF(x) == x \in {EPRNF0, EPRNF1}
EPRNFSocket(x) == IF x = EPRNF0 THEN 0 ELSE 1

CpuSt == {"I", "SC", "UC", "UD", "P_RS", "P_RU", "P_EVICT"}
HnfSt == {"I","SC","UC","UD",
          "TBE_ALLOC","WAIT_SNF","WAIT_BACKEND",
          "WAIT_SNP_RS","WAIT_SNP_CU","WAIT_SNP_RU",
          "WAIT_COMP_UC","WAIT_COMP_ACK","WAIT_GRANT","WAIT_WB"}
RnfSt == {"IDLE","HAVE_SC","HAVE_UC","HAVE_UD",
          "PENDING_RS","PENDING_CU","PENDING_RU"}
SnfSt == {"IDLE","FORWARDING"}
BkndSt == {"IDLE","WAITING_GRANT","WAITING_CLEAR"}

ReqKind == {"RS","RU","EVICT"}
RspKind == {"COMP_UC","COMP_ACK"}
SnpKind == {"SNP_CU"}

TailSeq(q) == IF Len(q) <= 1 THEN <<>> ELSE SubSeq(q, 2, Len(q))

RemoveAt(q, i) ==
    IF i = 1 THEN TailSeq(q)
    ELSE IF i = Len(q) THEN SubSeq(q, 1, Len(q)-1)
    ELSE SubSeq(q, 1, i-1) \o SubSeq(q, i+1, Len(q))

Msg(kind, dst, data, srcSock) ==
    [kind |-> kind, dst |-> dst, data |-> data, srcSock |-> srcSock]

IMsg(kind, srcSock, dstSock, cpu, op, data, lat) ==
    [kind |-> kind, srcSock |-> srcSock, dstSock |-> dstSock,
     cpu |-> cpu, op |-> op, data |-> data, lat |-> lat]

(***************************************************************************)
(* Variables                                                               *)
(***************************************************************************)
VARIABLES
    cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
    hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
    hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
    hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
    rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
    snfState, backendState, backendGrantData,
    dramData, dramWritten, latestGlobalWrite,
    reqQ, snpQ, rspQ, datQ, interSocketQ

vars == <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
          hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
          hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
          hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
          rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
          snfState, backendState, backendGrantData,
          dramData, dramWritten, latestGlobalWrite,
          reqQ, snpQ, rspQ, datQ, interSocketQ>>

(***************************************************************************)
(* Init                                                                    *)
(***************************************************************************)
Init ==
    /\ cpuState       = [c \in CPU |-> "I"]
    /\ cpuData        = [c \in CPU |-> 0]
    /\ cpuPendingData = [c \in CPU |-> 0]
    /\ cpuTargetSock  = [c \in CPU |-> LineHome]
    /\ cpuPendingKind = [c \in CPU |-> "NONE"]
    /\ hnfState       = [s \in Sockets |-> "I"]
    /\ hnfData        = [s \in Sockets |-> 0]
    /\ hnfCacheLine   = [s \in Sockets |-> FALSE]
    /\ hnfOwner       = [s \in Sockets |-> NONE]
    /\ hnfSharers     = [s \in Sockets |-> {}]
    /\ hnfTbeValid    = [s \in Sockets |-> FALSE]
    /\ hnfTbeOp       = [s \in Sockets |-> "NONE"]
    /\ hnfTbePhase    = [s \in Sockets |-> "NONE"]
    /\ hnfTbeRequester= [s \in Sockets |-> NONE]
    /\ hnfTbeNeedData = [s \in Sockets |-> FALSE]
    /\ hnfTbeGrantData= [s \in Sockets |-> 0]
    /\ hnfPendingOwnerUpdate = [s \in Sockets |-> FALSE]
    /\ rnfState       = [s \in Sockets |-> "IDLE"]
    /\ rnfCompUCSeen  = [s \in Sockets |-> FALSE]
    /\ rnfCompAckSent = [s \in Sockets |-> FALSE]
    /\ rnfCallbackArmed = [s \in Sockets |-> FALSE]
    /\ snfState       = [s \in Sockets |-> "IDLE"]
    /\ backendState   = [s \in Sockets |-> "IDLE"]
    /\ backendGrantData = [s \in Sockets |-> 0]
    /\ dramData       = [s \in Sockets |-> 0]
    /\ dramWritten    = [s \in Sockets |-> FALSE]
    /\ latestGlobalWrite = 0
    /\ reqQ = [s \in Sockets |-> <<>>]
    /\ snpQ = [s \in Sockets |-> <<>>]
    /\ rspQ = [s \in Sockets |-> <<>>]
    /\ datQ = [s \in Sockets |-> <<>>]
    /\ interSocketQ = <<>>

(***************************************************************************)
(* CPU issue actions                                                       *)
(***************************************************************************)
CpuLocalLoad(cpu) ==
    /\ CpuSocket(cpu) = LineHome
    /\ cpuState[cpu] = "I"
    /\ cpuState' = [cpuState EXCEPT ![cpu] = "P_RS"]
    /\ cpuTargetSock' = [cpuTargetSock EXCEPT ![cpu] = LineHome]
    /\ cpuPendingKind' = [cpuPendingKind EXCEPT ![cpu] = "RS"]
    /\ reqQ' = [reqQ EXCEPT ![LineHome] = Append(@, Msg("RS", cpu, 0, LineHome))]
    /\ UNCHANGED <<cpuData, cpuPendingData,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   snpQ, rspQ, datQ, interSocketQ>>

CpuRemoteLoad(cpu) ==
    /\ CpuSocket(cpu) # LineHome
    /\ cpuState[cpu] = "I"
    /\ Len(datQ[CpuSocket(cpu)]) = 0
    /\ cpuState' = [cpuState EXCEPT ![cpu] = "P_RS"]
    /\ cpuTargetSock' = [cpuTargetSock EXCEPT ![cpu] = LineHome]
    /\ cpuPendingKind' = [cpuPendingKind EXCEPT ![cpu] = "RS"]
    /\ interSocketQ' = Append(interSocketQ,
        IMsg("REMOTE_REQ", CpuSocket(cpu), LineHome, cpu, "RS", 0, RemoteLatency))
    /\ UNCHANGED <<cpuData, cpuPendingData,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, datQ>>

CpuLocalStore(cpu, data) ==
    /\ data \in DataV
    /\ CpuSocket(cpu) = LineHome
    /\ cpuState[cpu] \in {"I","SC"}
    /\ cpuState' = [cpuState EXCEPT ![cpu] = "P_RU"]
    /\ cpuPendingData' = [cpuPendingData EXCEPT ![cpu] = data]
    /\ cpuTargetSock' = [cpuTargetSock EXCEPT ![cpu] = LineHome]
    /\ cpuPendingKind' = [cpuPendingKind EXCEPT ![cpu] = "RU"]
    /\ reqQ' = [reqQ EXCEPT ![LineHome] = Append(@, Msg("RU", cpu, data, LineHome))]
    /\ UNCHANGED <<cpuData,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   snpQ, rspQ, datQ, interSocketQ>>

CpuRemoteStore(cpu, data) ==
    /\ data \in DataV
    /\ CpuSocket(cpu) # LineHome
    /\ cpuState[cpu] \in {"I","SC"}
    /\ Len(datQ[CpuSocket(cpu)]) = 0
    /\ cpuState' = [cpuState EXCEPT ![cpu] = "P_RU"]
    /\ cpuPendingData' = [cpuPendingData EXCEPT ![cpu] = data]
    /\ cpuTargetSock' = [cpuTargetSock EXCEPT ![cpu] = LineHome]
    /\ cpuPendingKind' = [cpuPendingKind EXCEPT ![cpu] = "RU"]
    /\ interSocketQ' = Append(interSocketQ,
        IMsg("REMOTE_REQ", CpuSocket(cpu), LineHome, cpu, "RU", data, RemoteLatency))
    /\ UNCHANGED <<cpuData,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, datQ>>

CpuLocalEvict(cpu) ==
    /\ CpuSocket(cpu) = LineHome
    /\ cpuState[cpu] \in {"SC","UC","UD"}
    /\ cpuState' = [cpuState EXCEPT ![cpu] = "P_EVICT"]
    /\ cpuPendingKind' = [cpuPendingKind EXCEPT ![cpu] = "EVICT"]
    /\ cpuTargetSock' = [cpuTargetSock EXCEPT ![cpu] = LineHome]
    /\ reqQ' = [reqQ EXCEPT ![LineHome] = Append(@, Msg("EVICT", cpu, cpuData[cpu], LineHome))]
    /\ UNCHANGED <<cpuData, cpuPendingData,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   snpQ, rspQ, datQ, interSocketQ>>

CpuRemoteEvict(cpu) ==
    /\ CpuSocket(cpu) # LineHome
    /\ cpuState[cpu] \in {"SC","UC","UD"}
    /\ Len(datQ[CpuSocket(cpu)]) = 0
    /\ cpuState' = [cpuState EXCEPT ![cpu] = "P_EVICT"]
    /\ cpuPendingKind' = [cpuPendingKind EXCEPT ![cpu] = "EVICT"]
    /\ cpuTargetSock' = [cpuTargetSock EXCEPT ![cpu] = LineHome]
    /\ interSocketQ' = Append(interSocketQ,
        IMsg("REMOTE_REQ", CpuSocket(cpu), LineHome, cpu, "EVICT", cpuData[cpu], RemoteLatency))
    /\ UNCHANGED <<cpuData, cpuPendingData,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, datQ>>

CpuStoreHit(cpu, data) ==
    /\ data \in DataV
    /\ cpuState[cpu] \in {"UC","UD"}
    /\ cpuState' = [cpuState EXCEPT ![cpu] = "UD"]
    /\ cpuData'  = [cpuData EXCEPT ![cpu] = data]
    /\ hnfData'  = [hnfData EXCEPT ![LineHome] = data]
    /\ latestGlobalWrite' = data
    /\ UNCHANGED <<cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfState, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, reqQ, snpQ, rspQ, datQ, interSocketQ>>

(***************************************************************************)
(* Cross-socket transport                                                   *)
(***************************************************************************)
InterSocketTick ==
    /\ \E i \in 1..Len(interSocketQ) : interSocketQ[i].lat > 0
    /\ \E i \in 1..Len(interSocketQ) :
        /\ interSocketQ[i].lat > 0
        /\ interSocketQ' = [interSocketQ EXCEPT ![i].lat = @ - 1]
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, datQ>>

InterSocketDeliverReq ==
    /\ \E i \in 1..Len(interSocketQ) :
        /\ interSocketQ[i].kind = "REMOTE_REQ"
        /\ interSocketQ[i].lat = 0
        /\ LET m == interSocketQ[i] IN
           /\ reqQ' = [reqQ EXCEPT ![m.dstSock] = Append(@, Msg(m.op, m.cpu, m.data, m.srcSock))]
           /\ interSocketQ' = RemoveAt(interSocketQ, i)
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   snpQ, rspQ, datQ>>

InterSocketDeliverGrant ==
    /\ \E i \in 1..Len(interSocketQ) :
        /\ interSocketQ[i].kind = "REMOTE_GRANT"
        /\ interSocketQ[i].lat = 0
        /\ LET m == interSocketQ[i] IN
           /\ datQ' = [datQ EXCEPT ![m.dstSock] =
                 Append(@, Msg(IF m.op = "RS" THEN "CPU_GRANT_RS"
                               ELSE IF m.op = "RU" THEN "CPU_GRANT_RU"
                               ELSE "CPU_GRANT_EVICT",
                               m.cpu, m.data, m.srcSock))]
           /\ interSocketQ' = RemoveAt(interSocketQ, i)
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ>>

InterSocketDeliverSnp ==
    /\ \E i \in 1..Len(interSocketQ) :
        /\ interSocketQ[i].kind = "REMOTE_SNP"
        /\ interSocketQ[i].lat = 0
        /\ LET m == interSocketQ[i] IN
           /\ snpQ' = [snpQ EXCEPT ![m.dstSock] = Append(@, Msg(m.op, m.cpu, 0, m.srcSock))]
           /\ interSocketQ' = RemoveAt(interSocketQ, i)
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, rspQ, datQ>>

InterSocketDeliverRsp ==
    /\ \E i \in 1..Len(interSocketQ) :
        /\ interSocketQ[i].kind = "REMOTE_RSP"
        /\ interSocketQ[i].lat = 0
        /\ LET m == interSocketQ[i] IN
           /\ rspQ' = [rspQ EXCEPT ![m.dstSock] = Append(@, Msg(m.op, m.cpu, 0, m.srcSock))]
           /\ interSocketQ' = RemoveAt(interSocketQ, i)
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, datQ>>

InterSocketDeliverWb ==
    /\ \E i \in 1..Len(interSocketQ) :
        /\ interSocketQ[i].kind = "REMOTE_WB"
        /\ interSocketQ[i].lat = 0
        /\ LET m == interSocketQ[i] IN
           /\ datQ' = [datQ EXCEPT ![m.dstSock] = Append(@, Msg("WB", m.cpu, m.data, m.srcSock))]
           /\ interSocketQ' = RemoveAt(interSocketQ, i)
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ>>

(***************************************************************************)
(* Home HN-F / SNF / backend / DRAM actions                                 *)
(***************************************************************************)
HnfAcceptReq(s) ==
    /\ s = LineHome
    /\ Len(reqQ[s]) > 0
    /\ ~hnfTbeValid[s]
    /\ LET m == reqQ[s][1]
           cpu == m.dst
       IN /\ (m.kind = "RS"    => cpuState[cpu] = "P_RS")
          /\ (m.kind = "RU"    => cpuState[cpu] = "P_RU")
          /\ (m.kind = "EVICT" => cpuState[cpu] = "P_EVICT")
          /\ hnfTbeValid' = [hnfTbeValid EXCEPT ![s] = TRUE]
          /\ hnfTbeOp'    = [hnfTbeOp EXCEPT ![s] = m.kind]
          /\ hnfTbeRequester' = [hnfTbeRequester EXCEPT ![s] = cpu]
          /\ hnfTbeNeedData'  = [hnfTbeNeedData EXCEPT ![s] = TRUE]
          /\ hnfTbeGrantData' = [hnfTbeGrantData EXCEPT ![s] = IF m.kind = "RU" THEN m.data ELSE 0]
          /\ reqQ' = [reqQ EXCEPT ![s] = TailSeq(@)]
          /\ IF (hnfState[s] = "I") \/ (hnfState[s] = "SC" /\ m.kind = "RS" /\ hnfOwner[s] = NONE)
             THEN /\ hnfTbePhase' = [hnfTbePhase EXCEPT ![s] = "WAIT_SNF"]
                  /\ hnfState' = hnfState
             ELSE IF hnfState[s] \in {"SC","UC","UD"} /\ m.kind \in {"RS","RU"} /\ cpu = hnfOwner[s]
             THEN /\ hnfTbePhase' = [hnfTbePhase EXCEPT ![s] = "WAIT_GRANT"]
                  /\ hnfState' = hnfState
             ELSE IF hnfState[s] = "SC" /\ m.kind = "RU" /\ (hnfSharers[s] \ {cpu}) /= {}
             THEN /\ hnfTbePhase' = [hnfTbePhase EXCEPT ![s] = "WAIT_SNP_CU"]
                  /\ hnfState' = hnfState
             ELSE IF hnfState[s] = "SC" /\ m.kind = "RU" /\ (hnfSharers[s] \ {cpu}) = {}
             THEN /\ hnfTbePhase' = [hnfTbePhase EXCEPT ![s] = "WAIT_GRANT"]
                  /\ hnfState' = hnfState
             ELSE IF hnfState[s] \in {"UC","UD"} /\ m.kind \in {"RS","RU"} /\ cpu /= hnfOwner[s]
             THEN /\ hnfTbePhase' = [hnfTbePhase EXCEPT ![s] = "WAIT_SNP_RU"]
                  /\ hnfState' = hnfState
             ELSE IF m.kind = "EVICT"
             THEN /\ hnfTbePhase' = [hnfTbePhase EXCEPT ![s] =
                     IF hnfState[s] \in {"UC","UD"} THEN "WAIT_WB" ELSE "WAIT_GRANT"]
                  /\ hnfState' = hnfState
             ELSE /\ hnfTbePhase' = [hnfTbePhase EXCEPT ![s] = "WAIT_SNF"]
                  /\ hnfState' = hnfState
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   snpQ, rspQ, datQ, interSocketQ>>

HnfDropStaleReq(s) ==
    /\ s = LineHome
    /\ Len(reqQ[s]) > 0
    /\ ~hnfTbeValid[s]
    /\ LET m == reqQ[s][1]
           cpu == m.dst
       IN /\ \/ (m.kind = "RS" /\ cpuState[cpu] /= "P_RS")
              \/ (m.kind = "RU" /\ cpuState[cpu] /= "P_RU")
              \/ (m.kind = "EVICT" /\ cpuState[cpu] /= "P_EVICT")
          /\ reqQ' = [reqQ EXCEPT ![s] = TailSeq(@)]
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   snpQ, rspQ, datQ, interSocketQ>>

HnfHitServeLocal(s) ==
    /\ s = LineHome
    /\ hnfTbeValid[s]
    /\ hnfTbePhase[s] = "WAIT_GRANT"
    /\ hnfTbeOp[s] \in {"RS","RU"}
    /\ hnfState[s] \in {"SC","UC","UD"}
    /\ LET cpu == hnfTbeRequester[s]
       IN /\ CpuSocket(cpu) = s
          /\ cpuState' = [cpuState EXCEPT ![cpu] =
               IF hnfTbeOp[s] = "RS" THEN hnfState[s]
               ELSE IF hnfState[s] \in {"UC","UD"} /\ cpu = hnfOwner[s] THEN "UD" ELSE "I"]
          /\ cpuData' = [cpuData EXCEPT ![cpu] = hnfData[s]]
          /\ hnfTbeValid' = [hnfTbeValid EXCEPT ![s] = FALSE]
          /\ hnfTbeOp' = [hnfTbeOp EXCEPT ![s] = "NONE"]
          /\ hnfTbePhase' = [hnfTbePhase EXCEPT ![s] = "NONE"]
          /\ hnfTbeRequester' = [hnfTbeRequester EXCEPT ![s] = NONE]
          /\ hnfTbeNeedData' = [hnfTbeNeedData EXCEPT ![s] = FALSE]
          /\ cpuPendingKind' = [cpuPendingKind EXCEPT ![cpu] = "NONE"]
    /\ UNCHANGED <<cpuPendingData, cpuTargetSock,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, datQ, interSocketQ>>

HnfHitServeRemote(s) ==
    /\ s = LineHome
    /\ hnfTbeValid[s]
    /\ hnfTbePhase[s] = "WAIT_GRANT"
    /\ hnfTbeOp[s] \in {"RS","RU"}
    /\ hnfState[s] \in {"SC","UC","UD"}
    /\ LET cpu == hnfTbeRequester[s]
           rs == CpuSocket(cpu)
       IN /\ rs # s
          /\ interSocketQ' = Append(interSocketQ,
                IMsg("REMOTE_GRANT", s, rs, cpu, hnfTbeOp[s], hnfData[s], RemoteLatency))
          /\ hnfTbeValid' = [hnfTbeValid EXCEPT ![s] = FALSE]
          /\ hnfTbeOp' = [hnfTbeOp EXCEPT ![s] = "NONE"]
          /\ hnfTbePhase' = [hnfTbePhase EXCEPT ![s] = "NONE"]
          /\ hnfTbeRequester' = [hnfTbeRequester EXCEPT ![s] = NONE]
          /\ hnfTbeNeedData' = [hnfTbeNeedData EXCEPT ![s] = FALSE]
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, datQ>>

HnfMissToSnf(s) ==
    /\ s = LineHome
    /\ hnfTbeValid[s]
    /\ hnfTbePhase[s] = "WAIT_SNF"
    /\ hnfTbePhase' = [hnfTbePhase EXCEPT ![s] = "WAIT_BACKEND"]
    /\ snfState' = [snfState EXCEPT ![s] = "FORWARDING"]
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, datQ, interSocketQ>>

SnfForward(s) ==
    /\ s = LineHome
    /\ snfState[s] = "FORWARDING"
    /\ backendState[s] = "IDLE"
    /\ backendState' = [backendState EXCEPT ![s] = "WAITING_GRANT"]
    /\ snfState' = [snfState EXCEPT ![s] = "IDLE"]
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, datQ, interSocketQ>>

BackendGrant(s) ==
    /\ s = LineHome
    /\ backendState[s] = "WAITING_GRANT"
    /\ \E gd \in DataV :
        /\ backendGrantData' = [backendGrantData EXCEPT ![s] = gd]
        /\ datQ' = [datQ EXCEPT ![s] = Append(@, Msg("SNF_GRANT", 0, gd, s))]
    /\ backendState' = [backendState EXCEPT ![s] = "IDLE"]
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, interSocketQ>>

HnfInstallGrantLocal(s) ==
    /\ s = LineHome
    /\ hnfTbeValid[s]
    /\ hnfTbePhase[s] = "WAIT_BACKEND"
    /\ hnfTbeOp[s] \in {"RS","RU"}
    /\ Len(datQ[s]) > 0
    /\ datQ[s][1].kind = "SNF_GRANT"
    /\ LET gd == datQ[s][1].data
           cpu == hnfTbeRequester[s]
       IN /\ CpuSocket(cpu) = s
          /\ datQ' = [datQ EXCEPT ![s] = TailSeq(@)]
          /\ hnfCacheLine' = [hnfCacheLine EXCEPT ![s] = TRUE]
          /\ hnfData' = [hnfData EXCEPT ![s] = gd]
          /\ IF hnfTbeOp[s] = "RS"
             THEN /\ hnfState' = [hnfState EXCEPT ![s] = "SC"]
                  /\ hnfOwner' = [hnfOwner EXCEPT ![s] = NONE]
                  /\ hnfSharers' = [hnfSharers EXCEPT ![s] = @ \cup {cpu}]
                  /\ cpuState' = [cpuState EXCEPT ![cpu] = "SC"]
                  /\ cpuData' = [cpuData EXCEPT ![cpu] = gd]
                  /\ latestGlobalWrite' = latestGlobalWrite
             ELSE /\ hnfState' = [hnfState EXCEPT ![s] = "UD"]
                  /\ hnfOwner' = [hnfOwner EXCEPT ![s] = cpu]
                  /\ hnfSharers' = [hnfSharers EXCEPT ![s] = {cpu}]
                  /\ cpuState' = [cpuState EXCEPT ![cpu] = "UD"]
                  /\ cpuData' = [cpuData EXCEPT ![cpu] = gd]
                  /\ latestGlobalWrite' = gd
          /\ cpuPendingKind' = [cpuPendingKind EXCEPT ![cpu] = "NONE"]
          /\ hnfTbeValid' = [hnfTbeValid EXCEPT ![s] = FALSE]
          /\ hnfTbeOp' = [hnfTbeOp EXCEPT ![s] = "NONE"]
          /\ hnfTbePhase' = [hnfTbePhase EXCEPT ![s] = "NONE"]
          /\ hnfTbeRequester' = [hnfTbeRequester EXCEPT ![s] = NONE]
          /\ hnfTbeNeedData' = [hnfTbeNeedData EXCEPT ![s] = FALSE]
    /\ UNCHANGED <<cpuPendingData, cpuTargetSock,
                   hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten,
                   reqQ, snpQ, rspQ, interSocketQ>>

HnfInstallGrantRemote(s) ==
    /\ s = LineHome
    /\ hnfTbeValid[s]
    /\ hnfTbePhase[s] \in {"WAIT_SNF", "WAIT_BACKEND"}
    /\ hnfTbeOp[s] \in {"RS","RU"}
    /\ Len(datQ[s]) > 0
    /\ datQ[s][1].kind = "SNF_GRANT"
    /\ LET gd == datQ[s][1].data
           cpu == hnfTbeRequester[s]
           rs == CpuSocket(cpu)
           tok == EPRNF(rs)
       IN /\ rs # s
          /\ datQ' = [datQ EXCEPT ![s] = TailSeq(@)]
          /\ hnfCacheLine' = [hnfCacheLine EXCEPT ![s] = TRUE]
          /\ hnfData' = [hnfData EXCEPT ![s] = gd]
          /\ IF hnfTbeOp[s] = "RS"
             THEN /\ hnfState' = [hnfState EXCEPT ![s] = "SC"]
                  /\ hnfOwner' = [hnfOwner EXCEPT ![s] = NONE]
                  /\ hnfSharers' = [hnfSharers EXCEPT ![s] = @ \cup {tok}]
                  /\ latestGlobalWrite' = latestGlobalWrite
             ELSE /\ hnfState' = [hnfState EXCEPT ![s] = "UD"]
                  /\ hnfOwner' = [hnfOwner EXCEPT ![s] = tok]
                  /\ hnfSharers' = [hnfSharers EXCEPT ![s] = {tok}]
                  /\ latestGlobalWrite' = gd
          /\ interSocketQ' = Append(interSocketQ,
                IMsg("REMOTE_GRANT", s, rs, cpu, hnfTbeOp[s], gd, RemoteLatency))
          /\ cpuState' = [cpuState EXCEPT ![cpu] =
                IF hnfTbeOp[s] = "RS" THEN "SC" ELSE "UD"]
          /\ cpuData' = [cpuData EXCEPT ![cpu] = gd]
          /\ cpuPendingKind' = [cpuPendingKind EXCEPT ![cpu] = "NONE"]
          /\ hnfTbeValid' = [hnfTbeValid EXCEPT ![s] = FALSE]
          /\ hnfTbeOp' = [hnfTbeOp EXCEPT ![s] = "NONE"]
          /\ hnfTbePhase' = [hnfTbePhase EXCEPT ![s] = "NONE"]
          /\ hnfTbeRequester' = [hnfTbeRequester EXCEPT ![s] = NONE]
          /\ hnfTbeNeedData' = [hnfTbeNeedData EXCEPT ![s] = FALSE]
    /\ UNCHANGED <<cpuPendingData, cpuTargetSock,
                   hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten,
                   reqQ, snpQ, rspQ>>

HnfSnoopOwnerRU(s) ==
    /\ s = LineHome
    /\ hnfTbeValid[s]
    /\ hnfTbePhase[s] = "WAIT_SNP_RU"
    /\ hnfOwner[s] \in Sharer
    /\ LET own == hnfOwner[s]
           victimCpu == IF own \in CPU THEN own ELSE EPRNFSocket(own)
       IN /\ cpuState' = [cpuState EXCEPT ![victimCpu] = "I"]
          /\ hnfState' = [hnfState EXCEPT ![s] = "I"]
          /\ hnfSharers' = [hnfSharers EXCEPT ![s] = {}]
          /\ hnfOwner' = [hnfOwner EXCEPT ![s] = NONE]
          /\ hnfCacheLine' = [hnfCacheLine EXCEPT ![s] = FALSE]
          /\ hnfTbePhase' = [hnfTbePhase EXCEPT ![s] = "WAIT_SNF"]
    /\ UNCHANGED <<cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfData, hnfTbeValid, hnfTbeOp, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, datQ, interSocketQ>>

HnfSnoopRnfCleanUnique(s) ==
    /\ s = LineHome
    /\ hnfTbeValid[s]
    /\ hnfTbePhase[s] = "WAIT_SNP_CU"
    /\ \E tok \in hnfSharers[s] : IsEPRNF(tok)
    /\ LET tok == CHOOSE t \in hnfSharers[s] : IsEPRNF(t)
           rs == EPRNFSocket(tok)
       IN /\ interSocketQ' = Append(interSocketQ,
               IMsg("REMOTE_SNP", s, rs, rs, "SNP_CU", 0, RemoteLatency))
          /\ hnfTbePhase' = [hnfTbePhase EXCEPT ![s] = "WAIT_COMP_UC"]
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, datQ>>

HnfInvalidateCpuSharers(s) ==
    /\ s = LineHome
    /\ hnfTbeValid[s]
    /\ hnfTbePhase[s] = "WAIT_SNP_CU"
    /\ ~(\E tok \in hnfSharers[s] : IsEPRNF(tok))
    /\ LET others == hnfSharers[s] \ {hnfTbeRequester[s]}
           newSh == hnfSharers[s] \ others
       IN /\ cpuState' = [c \in CPU |-> IF c \in others THEN "I" ELSE cpuState[c]]
          /\ hnfSharers' = [hnfSharers EXCEPT ![s] = newSh]
          /\ hnfState' = [hnfState EXCEPT ![s] = IF newSh = {} THEN "I" ELSE @]
          /\ hnfOwner' = [hnfOwner EXCEPT ![s] = IF newSh = {} THEN NONE ELSE @]
          /\ hnfCacheLine' = [hnfCacheLine EXCEPT ![s] = (newSh /= {})]
          /\ hnfTbePhase' = [hnfTbePhase EXCEPT ![s] = IF newSh = {} THEN "WAIT_SNF" ELSE "WAIT_GRANT"]
    /\ UNCHANGED <<cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfData, hnfTbeValid, hnfTbeOp, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, datQ, interSocketQ>>

EpRnfStartCleanUnique(reqSock) ==
    /\ reqSock # LineHome
    /\ Len(snpQ[reqSock]) > 0
    /\ snpQ[reqSock][1].kind = "SNP_CU"
    /\ rnfState[reqSock] = "HAVE_SC"
    /\ rnfState' = [rnfState EXCEPT ![reqSock] = "PENDING_CU"]
    /\ rnfCallbackArmed' = [rnfCallbackArmed EXCEPT ![reqSock] = TRUE]
    /\ snpQ' = [snpQ EXCEPT ![reqSock] = TailSeq(@)]
    /\ interSocketQ' = Append(interSocketQ,
         IMsg("REMOTE_RSP", reqSock, LineHome, reqSock, "COMP_UC", 0, RemoteLatency))
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfCompUCSeen, rnfCompAckSent,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, rspQ, datQ>>

HnfRecvCompUC(s) ==
    /\ s = LineHome
    /\ hnfTbeValid[s]
    /\ hnfTbePhase[s] = "WAIT_COMP_UC"
    /\ Len(rspQ[s]) > 0
    /\ rspQ[s][1].kind = "COMP_UC"
    /\ LET reqSock == rspQ[s][1].srcSock IN
       /\ rspQ' = [rspQ EXCEPT ![s] = TailSeq(@)]
       /\ hnfTbePhase' = [hnfTbePhase EXCEPT ![s] = "WAIT_COMP_ACK"]
       /\ rnfCompUCSeen' = [rnfCompUCSeen EXCEPT ![reqSock] = TRUE]
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, datQ, interSocketQ>>

EpRnfSendCompAck(reqSock) ==
    /\ reqSock # LineHome
    /\ rnfCompUCSeen[reqSock]
    /\ ~rnfCompAckSent[reqSock]
    /\ rnfCompAckSent' = [rnfCompAckSent EXCEPT ![reqSock] = TRUE]
    /\ interSocketQ' = Append(interSocketQ,
         IMsg("REMOTE_RSP", reqSock, LineHome, reqSock, "COMP_ACK", 0, RemoteLatency))
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, datQ>>

HnfRecvCompAck(s) ==
    /\ s = LineHome
    /\ hnfTbeValid[s]
    /\ hnfTbePhase[s] = "WAIT_COMP_ACK"
    /\ Len(rspQ[s]) > 0
    /\ rspQ[s][1].kind = "COMP_ACK"
    /\ LET reqSock == rspQ[s][1].srcSock
           tok == EPRNF(reqSock)
       IN /\ rspQ' = [rspQ EXCEPT ![s] = TailSeq(@)]
          /\ hnfSharers' = [hnfSharers EXCEPT ![s] = @ \ {tok}]
          /\ hnfTbePhase' = [hnfTbePhase EXCEPT ![s] = "WAIT_GRANT"]
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfState, hnfData, hnfCacheLine, hnfOwner,
                   hnfTbeValid, hnfTbeOp, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, datQ, interSocketQ>>

EpRnfCallback(reqSock) ==
    /\ reqSock # LineHome
    /\ rnfCompUCSeen[reqSock]
    /\ rnfCompAckSent[reqSock]
    /\ rnfCallbackArmed[reqSock]
    /\ rnfState[reqSock] \in {"PENDING_RS","PENDING_CU","PENDING_RU"}
    /\ rnfState' = [rnfState EXCEPT ![reqSock] =
         IF @ = "PENDING_RS" THEN "HAVE_SC"
         ELSE IF @ = "PENDING_CU" THEN "HAVE_UC"
         ELSE "HAVE_UD"]
    /\ rnfCompUCSeen' = [rnfCompUCSeen EXCEPT ![reqSock] = FALSE]
    /\ rnfCompAckSent' = [rnfCompAckSent EXCEPT ![reqSock] = FALSE]
    /\ rnfCallbackArmed' = [rnfCallbackArmed EXCEPT ![reqSock] = FALSE]
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, datQ, interSocketQ>>

HnfWritebackToDram(s) ==
    /\ s = LineHome
    /\ hnfTbeValid[s]
    /\ hnfTbePhase[s] = "WAIT_WB"
    /\ hnfTbeOp[s] = "EVICT"
    /\ \E v \in DataV :
        /\ v = cpuData[hnfTbeRequester[s]]
        /\ datQ' = [datQ EXCEPT ![s] = Append(@, Msg("WB", 0, v, s))]
        /\ hnfTbePhase' = [hnfTbePhase EXCEPT ![s] = "WAIT_GRANT"]
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, interSocketQ>>

DramAcceptWriteback(s) ==
    /\ s = LineHome
    /\ Len(datQ[s]) > 0
    /\ datQ[s][1].kind = "WB"
    /\ dramData' = [dramData EXCEPT ![s] = datQ[s][1].data]
    /\ dramWritten' = [dramWritten EXCEPT ![s] = TRUE]
    /\ datQ' = [datQ EXCEPT ![s] = TailSeq(@)]
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   latestGlobalWrite, reqQ, snpQ, rspQ, interSocketQ>>

HnfFinishWritebackLocal(s) ==
    /\ s = LineHome
    /\ hnfTbeValid[s]
    /\ hnfTbePhase[s] = "WAIT_GRANT"
    /\ hnfTbeOp[s] = "EVICT"
    /\ (dramWritten[s] \/ hnfState[s] \in {"I","SC"})
    /\ LET cpu == hnfTbeRequester[s] IN
       /\ CpuSocket(cpu) = s
       /\ hnfCacheLine' = [hnfCacheLine EXCEPT ![s] = IF hnfState[s] = "SC" /\ hnfSharers[s] /= {cpu} THEN TRUE ELSE FALSE]
       /\ hnfSharers' = [hnfSharers EXCEPT ![s] = @ \ {cpu}]
       /\ hnfState' = [hnfState EXCEPT ![s] = IF hnfSharers'[s] = {} THEN "I" ELSE @]
       /\ hnfOwner' = [hnfOwner EXCEPT ![s] = IF hnfState'[s] = "I" THEN NONE ELSE @]
       /\ cpuState' = [cpuState EXCEPT ![cpu] = "I"]
       /\ cpuPendingKind' = [cpuPendingKind EXCEPT ![cpu] = "NONE"]
       /\ hnfTbeValid' = [hnfTbeValid EXCEPT ![s] = FALSE]
       /\ hnfTbeOp' = [hnfTbeOp EXCEPT ![s] = "NONE"]
       /\ hnfTbePhase' = [hnfTbePhase EXCEPT ![s] = "NONE"]
       /\ hnfTbeRequester' = [hnfTbeRequester EXCEPT ![s] = NONE]
       /\ dramWritten' = [dramWritten EXCEPT ![s] = FALSE]
    /\ UNCHANGED <<cpuData, cpuPendingData, cpuTargetSock,
                   hnfData, hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, latestGlobalWrite,
                   reqQ, snpQ, rspQ, datQ, interSocketQ>>

HnfFinishWritebackRemote(s) ==
    /\ s = LineHome
    /\ hnfTbeValid[s]
    /\ hnfTbePhase[s] = "WAIT_GRANT"
    /\ hnfTbeOp[s] = "EVICT"
    /\ (dramWritten[s] \/ hnfState[s] \in {"I","SC"})
    /\ LET cpu == hnfTbeRequester[s]
           rs == CpuSocket(cpu)
           tok == EPRNF(rs)
       IN /\ rs # s
          /\ hnfCacheLine' = [hnfCacheLine EXCEPT ![s] = IF hnfState[s] = "SC" /\ hnfSharers[s] /= {tok} THEN TRUE ELSE FALSE]
          /\ hnfSharers' = [hnfSharers EXCEPT ![s] = @ \ {tok}]
          /\ hnfState' = [hnfState EXCEPT ![s] = IF hnfSharers'[s] = {} THEN "I" ELSE @]
          /\ hnfOwner' = [hnfOwner EXCEPT ![s] = IF hnfState'[s] = "I" THEN NONE ELSE @]
          /\ interSocketQ' = Append(interSocketQ,
                IMsg("REMOTE_GRANT", s, rs, cpu, "EVICT", 0, RemoteLatency))
          /\ hnfTbeValid' = [hnfTbeValid EXCEPT ![s] = FALSE]
          /\ hnfTbeOp' = [hnfTbeOp EXCEPT ![s] = "NONE"]
          /\ hnfTbePhase' = [hnfTbePhase EXCEPT ![s] = "NONE"]
          /\ hnfTbeRequester' = [hnfTbeRequester EXCEPT ![s] = NONE]
          /\ dramWritten' = [dramWritten EXCEPT ![s] = FALSE]
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfData, hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, latestGlobalWrite,
                   reqQ, snpQ, rspQ, datQ>>

HnfGrantAfterSnoopLocal(s) ==
    /\ s = LineHome
    /\ hnfTbeValid[s]
    /\ hnfTbePhase[s] = "WAIT_GRANT"
    /\ hnfTbeOp[s] \in {"RS","RU"}
    /\ hnfState[s] \in {"SC","UC","UD"}
    /\ LET cpu == hnfTbeRequester[s]
       IN /\ CpuSocket(cpu) = s
          /\ cpuState' = [cpuState EXCEPT ![cpu] = IF hnfTbeOp[s] = "RU" THEN "UD" ELSE hnfState[s]]
          /\ cpuData' = [cpuData EXCEPT ![cpu] = hnfData[s]]
          /\ IF hnfTbeOp[s] = "RU"
             THEN /\ hnfState' = [hnfState EXCEPT ![s] = "UD"]
                  /\ hnfOwner' = [hnfOwner EXCEPT ![s] = cpu]
                  /\ hnfSharers' = [hnfSharers EXCEPT ![s] = {cpu}]
                  /\ latestGlobalWrite' = hnfData[s]
             ELSE /\ hnfState' = hnfState
                  /\ hnfOwner' = hnfOwner
                  /\ hnfSharers' = [hnfSharers EXCEPT ![s] = @ \cup {cpu}]
                  /\ latestGlobalWrite' = latestGlobalWrite
          /\ cpuPendingKind' = [cpuPendingKind EXCEPT ![cpu] = "NONE"]
          /\ hnfTbeValid' = [hnfTbeValid EXCEPT ![s] = FALSE]
          /\ hnfTbeOp' = [hnfTbeOp EXCEPT ![s] = "NONE"]
          /\ hnfTbePhase' = [hnfTbePhase EXCEPT ![s] = "NONE"]
          /\ hnfTbeRequester' = [hnfTbeRequester EXCEPT ![s] = NONE]
          /\ hnfTbeNeedData' = [hnfTbeNeedData EXCEPT ![s] = FALSE]
    /\ UNCHANGED <<cpuPendingData, cpuTargetSock,
                   hnfData, hnfCacheLine, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten,
                   reqQ, snpQ, rspQ, datQ, interSocketQ>>

HnfGrantAfterSnoopRemote(s) ==
    /\ s = LineHome
    /\ hnfTbeValid[s]
    /\ hnfTbePhase[s] = "WAIT_GRANT"
    /\ hnfTbeOp[s] \in {"RS","RU"}
    /\ hnfState[s] \in {"SC","UC","UD"}
    /\ LET cpu == hnfTbeRequester[s]
           rs == CpuSocket(cpu)
           tok == EPRNF(rs)
       IN /\ rs # s
          /\ IF hnfTbeOp[s] = "RU"
             THEN /\ hnfState' = [hnfState EXCEPT ![s] = "UD"]
                  /\ hnfOwner' = [hnfOwner EXCEPT ![s] = tok]
                  /\ hnfSharers' = [hnfSharers EXCEPT ![s] = {tok}]
                  /\ latestGlobalWrite' = hnfData[s]
             ELSE /\ hnfState' = hnfState
                  /\ hnfOwner' = hnfOwner
                  /\ hnfSharers' = [hnfSharers EXCEPT ![s] = @ \cup {tok}]
                  /\ latestGlobalWrite' = latestGlobalWrite
          /\ interSocketQ' = Append(interSocketQ,
               IMsg("REMOTE_GRANT", s, rs, cpu, hnfTbeOp[s], hnfData[s], RemoteLatency))
          /\ hnfTbeValid' = [hnfTbeValid EXCEPT ![s] = FALSE]
          /\ hnfTbeOp' = [hnfTbeOp EXCEPT ![s] = "NONE"]
          /\ hnfTbePhase' = [hnfTbePhase EXCEPT ![s] = "NONE"]
          /\ hnfTbeRequester' = [hnfTbeRequester EXCEPT ![s] = NONE]
          /\ hnfTbeNeedData' = [hnfTbeNeedData EXCEPT ![s] = FALSE]
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfData, hnfCacheLine, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten,
                   reqQ, snpQ, rspQ, datQ>>

BackendSendClear(s) ==
    /\ s = LineHome
    /\ backendState[s] = "IDLE"
    /\ backendGrantData[s] > 0
    /\ backendState' = [backendState EXCEPT ![s] = "WAITING_CLEAR"]
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, datQ, interSocketQ>>

BackendRecvClearAck(s) ==
    /\ s = LineHome
    /\ backendState[s] = "WAITING_CLEAR"
    /\ backendState' = [backendState EXCEPT ![s] = "IDLE"]
    /\ backendGrantData' = [backendGrantData EXCEPT ![s] = 0]
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, datQ, interSocketQ>>

(***************************************************************************)
(* Requester CPU completion actions                                         *)
(***************************************************************************)
CpuAcceptRemoteGrant(cpu) ==
    /\ CpuSocket(cpu) # LineHome
    /\ Len(datQ[CpuSocket(cpu)]) > 0
    /\ datQ[CpuSocket(cpu)][1].kind \in {"CPU_GRANT_RS", "CPU_GRANT_RU"}
    /\ datQ[CpuSocket(cpu)][1].dst = cpu
    /\ cpuPendingKind[cpu] \in {"RS","RU"}
    /\ datQ[CpuSocket(cpu)][1].kind = IF cpuPendingKind[cpu] = "RS" THEN "CPU_GRANT_RS" ELSE "CPU_GRANT_RU"
    /\ cpuState[cpu] = IF cpuPendingKind[cpu] = "RS" THEN "P_RS" ELSE "P_RU"
    /\ LET rs == CpuSocket(cpu)
           tok == EPRNF(rs)
       IN IF cpuPendingKind[cpu] = "RS"
          THEN tok \in hnfSharers[LineHome]
          ELSE hnfOwner[LineHome] = tok
    /\ LET rs == CpuSocket(cpu)
           gd == datQ[rs][1].data
       IN /\ datQ' = [datQ EXCEPT ![rs] = TailSeq(@)]
          /\ IF cpuPendingKind[cpu] = "RS"
             THEN /\ cpuState' = [cpuState EXCEPT ![cpu] = "SC"]
                  /\ cpuData'  = [cpuData EXCEPT ![cpu] = gd]
                  /\ rnfState' = [rnfState EXCEPT ![rs] = "HAVE_SC"]
             ELSE /\ cpuState' = [cpuState EXCEPT ![cpu] = "UD"]
                  /\ cpuData'  = [cpuData EXCEPT ![cpu] = gd]
                  /\ rnfState' = [rnfState EXCEPT ![rs] = "HAVE_UD"]
          /\ cpuPendingKind' = [cpuPendingKind EXCEPT ![cpu] = "NONE"]
    /\ UNCHANGED <<cpuPendingData, cpuTargetSock,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, interSocketQ>>

CpuDropStaleRemoteGrant(cpu) ==
    /\ CpuSocket(cpu) # LineHome
    /\ Len(datQ[CpuSocket(cpu)]) > 0
    /\ datQ[CpuSocket(cpu)][1].kind \in {"CPU_GRANT_RS", "CPU_GRANT_RU", "CPU_GRANT_EVICT"}
    /\ datQ[CpuSocket(cpu)][1].dst = cpu
    /\ cpuPendingKind[cpu] \in {"NONE","RS","RU"}
    /\ LET rs == CpuSocket(cpu)
           k == datQ[rs][1].kind
           tok == EPRNF(rs)
           expect == IF k = "CPU_GRANT_RS" THEN "P_RS"
                     ELSE IF k = "CPU_GRANT_RU" THEN "P_RU"
                     ELSE "P_EVICT"
           dirMatch == IF k = "CPU_GRANT_RS" THEN tok \in hnfSharers[LineHome]
                       ELSE IF k = "CPU_GRANT_RU" THEN hnfOwner[LineHome] = tok
                       ELSE TRUE
       IN /\ cpuState[cpu] # expect \/ ~dirMatch
          /\ datQ' = [datQ EXCEPT ![rs] = TailSeq(@)]
    /\ UNCHANGED <<cpuState, cpuData, cpuPendingData, cpuTargetSock, cpuPendingKind,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfState, rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, interSocketQ>>

CpuCompleteRemoteEvict(cpu) ==
    /\ CpuSocket(cpu) # LineHome
    /\ Len(datQ[CpuSocket(cpu)]) > 0
    /\ datQ[CpuSocket(cpu)][1].kind = "CPU_GRANT_EVICT"
    /\ datQ[CpuSocket(cpu)][1].dst = cpu
    /\ cpuPendingKind[cpu] = "EVICT"
    /\ LET rs == CpuSocket(cpu) IN
       /\ datQ' = [datQ EXCEPT ![rs] = TailSeq(@)]
       /\ cpuState' = [cpuState EXCEPT ![cpu] = "I"]
       /\ cpuPendingKind' = [cpuPendingKind EXCEPT ![cpu] = "NONE"]
       /\ rnfState' = [rnfState EXCEPT ![rs] = "IDLE"]
    /\ UNCHANGED <<cpuData, cpuPendingData, cpuTargetSock,
                   hnfState, hnfData, hnfCacheLine, hnfOwner, hnfSharers,
                   hnfTbeValid, hnfTbeOp, hnfTbePhase, hnfTbeRequester,
                   hnfTbeNeedData, hnfTbeGrantData, hnfPendingOwnerUpdate,
                   rnfCompUCSeen, rnfCompAckSent, rnfCallbackArmed,
                   snfState, backendState, backendGrantData,
                   dramData, dramWritten, latestGlobalWrite,
                   reqQ, snpQ, rspQ, interSocketQ>>

(***************************************************************************)
(* Next / Spec                                                              *)
(***************************************************************************)
SocketActions(s) ==
    \/ HnfAcceptReq(s)
    \/ HnfDropStaleReq(s)
    \/ HnfHitServeLocal(s)
    \/ HnfHitServeRemote(s)
    \/ HnfMissToSnf(s)
    \/ SnfForward(s)
    \/ BackendGrant(s)
    \/ HnfInstallGrantLocal(s)
    \/ HnfInstallGrantRemote(s)
    \/ HnfSnoopOwnerRU(s)
    \/ HnfSnoopRnfCleanUnique(s)
    \/ HnfInvalidateCpuSharers(s)
    \/ HnfRecvCompUC(s)
    \/ HnfRecvCompAck(s)
    \/ HnfWritebackToDram(s)
    \/ DramAcceptWriteback(s)
    \/ HnfFinishWritebackLocal(s)
    \/ HnfFinishWritebackRemote(s)
    \/ HnfGrantAfterSnoopLocal(s)
    \/ HnfGrantAfterSnoopRemote(s)
    \/ BackendSendClear(s)
    \/ BackendRecvClearAck(s)

Next ==
    \/ \E cpu \in CPU : CpuLocalLoad(cpu) \/ CpuRemoteLoad(cpu)
    \/ \E cpu \in CPU, d \in DataV : CpuLocalStore(cpu, d) \/ CpuRemoteStore(cpu, d)
    \/ \E cpu \in CPU, d \in DataV : CpuStoreHit(cpu, d)
    \/ \E cpu \in CPU : CpuLocalEvict(cpu) \/ CpuRemoteEvict(cpu)
    \/ InterSocketTick
    \/ InterSocketDeliverReq
    \/ InterSocketDeliverGrant
    \/ InterSocketDeliverSnp
    \/ InterSocketDeliverRsp
    \/ InterSocketDeliverWb
    \/ \E s \in Sockets : SocketActions(s)
    \/ \E rs \in Sockets : EpRnfStartCleanUnique(rs) \/ EpRnfSendCompAck(rs) \/ EpRnfCallback(rs)
    \/ \E cpu \in CPU : CpuAcceptRemoteGrant(cpu) \/ CpuDropStaleRemoteGrant(cpu) \/ CpuCompleteRemoteEvict(cpu)

Spec == Init /\ [][Next]_vars

(***************************************************************************)
(* Invariants                                                               *)
(***************************************************************************)
TypeOK ==
    /\ \A c \in CPU : cpuState[c] \in CpuSt
    /\ \A c \in CPU : cpuData[c] \in DataV
    /\ \A c \in CPU : cpuPendingData[c] \in DataV
    /\ \A c \in CPU : cpuTargetSock[c] \in Sockets
    /\ \A c \in CPU : cpuPendingKind[c] \in {"NONE","RS","RU","EVICT"}
    /\ \A s \in Sockets : hnfState[s] \in HnfSt
    /\ \A s \in Sockets : hnfData[s] \in DataV
    /\ \A s \in Sockets : hnfCacheLine[s] \in BOOLEAN
    /\ \A s \in Sockets : hnfOwner[s] \in Sharer \cup {NONE}
    /\ \A s \in Sockets : hnfSharers[s] \subseteq Sharer
    /\ \A s \in Sockets : rnfState[s] \in RnfSt
    /\ \A s \in Sockets : snfState[s] \in SnfSt
    /\ \A s \in Sockets : backendState[s] \in BkndSt
    /\ \A s \in Sockets : dramData[s] \in DataV
    /\ latestGlobalWrite \in DataV

DataIntegrity ==
    \A c \in CPU :
        (cpuState[c] \in {"UC","UD"}) => (cpuData[c] = latestGlobalWrite)

NoTwoDirtyUniques ==
    Cardinality({c \in CPU : cpuState[c] = "UD"}) <= 1

CallbackOrdering ==
    \A s \in Sockets :
        /\ rnfCompAckSent[s] => rnfCompUCSeen[s]
        /\ (rnfState[s] \in {"HAVE_SC","HAVE_UC","HAVE_UD","IDLE"}) => ~rnfCallbackArmed[s]

WritebackPersistence ==
    \A s \in Sockets : dramWritten[s] => (dramData[s] = latestGlobalWrite)

NoLeakedGrant ==
    \A s \in Sockets : (backendState[s] /= "WAITING_CLEAR") \/ (backendGrantData[s] > 0)

SocketIsolation ==
    \A s \in Sockets :
        s # LineHome =>
            /\ hnfState[s] = "I"
            /\ hnfSharers[s] = {}
            /\ hnfOwner[s] = NONE
            /\ ~hnfTbeValid[s]

CrossSocketDataIntegrity ==
    \A c \in CPU :
        CpuSocket(c) # LineHome
        /\ cpuState[c] \in {"SC","UC","UD"}
        /\ rnfState[CpuSocket(c)] \in {"HAVE_SC","HAVE_UC","HAVE_UD"} =>
            /\ IF cpuState[c] = "SC"
               THEN EPRNF(CpuSocket(c)) \in hnfSharers[LineHome]
               ELSE /\ hnfOwner[LineHome] = EPRNF(CpuSocket(c))
                    /\ cpuData[c] = hnfData[LineHome]

=============================================================================
