---- MODULE ep_intra_node_dual_TTrace_1781969408 ----
EXTENDS Sequences, TLCExt, Toolbox, Naturals, TLC, ep_intra_node_dual

_expression ==
    LET ep_intra_node_dual_TEExpression == INSTANCE ep_intra_node_dual_TEExpression
    IN ep_intra_node_dual_TEExpression!expression
----

_trace ==
    LET ep_intra_node_dual_TETrace == INSTANCE ep_intra_node_dual_TETrace
    IN ep_intra_node_dual_TETrace!trace
----

_inv ==
    ~(
        TLCGet("level") = Len(_TETrace)
        /\
        cpuTargetSock = ((0 :> 0 @@ 1 :> 0))
        /\
        hnfTbeGrantData = ((0 :> 0 @@ 1 :> 0))
        /\
        reqQ = ((0 :> <<>> @@ 1 :> <<>>))
        /\
        hnfTbeRequester = ((0 :> -1 @@ 1 :> -1))
        /\
        hnfTbeValid = ((0 :> FALSE @@ 1 :> FALSE))
        /\
        backendState = ((0 :> "IDLE" @@ 1 :> "IDLE"))
        /\
        snpQ = ((0 :> <<>> @@ 1 :> <<>>))
        /\
        cpuPendingKind = ((0 :> "NONE" @@ 1 :> "NONE"))
        /\
        rnfCompAckSent = ((0 :> FALSE @@ 1 :> FALSE))
        /\
        cpuData = ((0 :> 1 @@ 1 :> 0))
        /\
        snfState = ((0 :> "IDLE" @@ 1 :> "IDLE"))
        /\
        rnfState = ((0 :> "IDLE" @@ 1 :> "HAVE_SC"))
        /\
        hnfData = ((0 :> 1 @@ 1 :> 0))
        /\
        rspQ = ((0 :> <<>> @@ 1 :> <<>>))
        /\
        cpuPendingData = ((0 :> 0 @@ 1 :> 0))
        /\
        dramWritten = ((0 :> FALSE @@ 1 :> FALSE))
        /\
        interSocketQ = (<<>>)
        /\
        dramData = ((0 :> 0 @@ 1 :> 0))
        /\
        hnfPendingOwnerUpdate = ((0 :> FALSE @@ 1 :> FALSE))
        /\
        hnfState = ((0 :> "SC" @@ 1 :> "I"))
        /\
        rnfCompUCSeen = ((0 :> FALSE @@ 1 :> FALSE))
        /\
        backendGrantData = ((0 :> 1 @@ 1 :> 0))
        /\
        latestGlobalWrite = (0)
        /\
        hnfCacheLine = ((0 :> TRUE @@ 1 :> FALSE))
        /\
        datQ = ((0 :> <<>> @@ 1 :> <<>>))
        /\
        hnfOwner = ((0 :> -1 @@ 1 :> -1))
        /\
        cpuState = ((0 :> "SC" @@ 1 :> "SC"))
        /\
        hnfTbeNeedData = ((0 :> FALSE @@ 1 :> FALSE))
        /\
        rnfCallbackArmed = ((0 :> FALSE @@ 1 :> FALSE))
        /\
        hnfTbePhase = ((0 :> "NONE" @@ 1 :> "NONE"))
        /\
        hnfTbeOp = ((0 :> "NONE" @@ 1 :> "NONE"))
        /\
        hnfSharers = ((0 :> {0, 3} @@ 1 :> {}))
    )
----

_init ==
    /\ hnfData = _TETrace[1].hnfData
    /\ reqQ = _TETrace[1].reqQ
    /\ interSocketQ = _TETrace[1].interSocketQ
    /\ cpuPendingData = _TETrace[1].cpuPendingData
    /\ rspQ = _TETrace[1].rspQ
    /\ hnfTbeRequester = _TETrace[1].hnfTbeRequester
    /\ hnfCacheLine = _TETrace[1].hnfCacheLine
    /\ hnfTbeGrantData = _TETrace[1].hnfTbeGrantData
    /\ dramWritten = _TETrace[1].dramWritten
    /\ cpuPendingKind = _TETrace[1].cpuPendingKind
    /\ latestGlobalWrite = _TETrace[1].latestGlobalWrite
    /\ rnfState = _TETrace[1].rnfState
    /\ hnfState = _TETrace[1].hnfState
    /\ backendGrantData = _TETrace[1].backendGrantData
    /\ datQ = _TETrace[1].datQ
    /\ snpQ = _TETrace[1].snpQ
    /\ rnfCompAckSent = _TETrace[1].rnfCompAckSent
    /\ hnfTbeNeedData = _TETrace[1].hnfTbeNeedData
    /\ hnfTbeOp = _TETrace[1].hnfTbeOp
    /\ dramData = _TETrace[1].dramData
    /\ rnfCallbackArmed = _TETrace[1].rnfCallbackArmed
    /\ backendState = _TETrace[1].backendState
    /\ rnfCompUCSeen = _TETrace[1].rnfCompUCSeen
    /\ hnfPendingOwnerUpdate = _TETrace[1].hnfPendingOwnerUpdate
    /\ cpuData = _TETrace[1].cpuData
    /\ hnfTbeValid = _TETrace[1].hnfTbeValid
    /\ hnfTbePhase = _TETrace[1].hnfTbePhase
    /\ hnfSharers = _TETrace[1].hnfSharers
    /\ hnfOwner = _TETrace[1].hnfOwner
    /\ cpuState = _TETrace[1].cpuState
    /\ snfState = _TETrace[1].snfState
    /\ cpuTargetSock = _TETrace[1].cpuTargetSock
----

_next ==
    /\ \E i,j \in DOMAIN _TETrace:
        /\ \/ /\ j = i + 1
              /\ i = TLCGet("level")
        /\ hnfData  = _TETrace[i].hnfData
        /\ hnfData' = _TETrace[j].hnfData
        /\ reqQ  = _TETrace[i].reqQ
        /\ reqQ' = _TETrace[j].reqQ
        /\ interSocketQ  = _TETrace[i].interSocketQ
        /\ interSocketQ' = _TETrace[j].interSocketQ
        /\ cpuPendingData  = _TETrace[i].cpuPendingData
        /\ cpuPendingData' = _TETrace[j].cpuPendingData
        /\ rspQ  = _TETrace[i].rspQ
        /\ rspQ' = _TETrace[j].rspQ
        /\ hnfTbeRequester  = _TETrace[i].hnfTbeRequester
        /\ hnfTbeRequester' = _TETrace[j].hnfTbeRequester
        /\ hnfCacheLine  = _TETrace[i].hnfCacheLine
        /\ hnfCacheLine' = _TETrace[j].hnfCacheLine
        /\ hnfTbeGrantData  = _TETrace[i].hnfTbeGrantData
        /\ hnfTbeGrantData' = _TETrace[j].hnfTbeGrantData
        /\ dramWritten  = _TETrace[i].dramWritten
        /\ dramWritten' = _TETrace[j].dramWritten
        /\ cpuPendingKind  = _TETrace[i].cpuPendingKind
        /\ cpuPendingKind' = _TETrace[j].cpuPendingKind
        /\ latestGlobalWrite  = _TETrace[i].latestGlobalWrite
        /\ latestGlobalWrite' = _TETrace[j].latestGlobalWrite
        /\ rnfState  = _TETrace[i].rnfState
        /\ rnfState' = _TETrace[j].rnfState
        /\ hnfState  = _TETrace[i].hnfState
        /\ hnfState' = _TETrace[j].hnfState
        /\ backendGrantData  = _TETrace[i].backendGrantData
        /\ backendGrantData' = _TETrace[j].backendGrantData
        /\ datQ  = _TETrace[i].datQ
        /\ datQ' = _TETrace[j].datQ
        /\ snpQ  = _TETrace[i].snpQ
        /\ snpQ' = _TETrace[j].snpQ
        /\ rnfCompAckSent  = _TETrace[i].rnfCompAckSent
        /\ rnfCompAckSent' = _TETrace[j].rnfCompAckSent
        /\ hnfTbeNeedData  = _TETrace[i].hnfTbeNeedData
        /\ hnfTbeNeedData' = _TETrace[j].hnfTbeNeedData
        /\ hnfTbeOp  = _TETrace[i].hnfTbeOp
        /\ hnfTbeOp' = _TETrace[j].hnfTbeOp
        /\ dramData  = _TETrace[i].dramData
        /\ dramData' = _TETrace[j].dramData
        /\ rnfCallbackArmed  = _TETrace[i].rnfCallbackArmed
        /\ rnfCallbackArmed' = _TETrace[j].rnfCallbackArmed
        /\ backendState  = _TETrace[i].backendState
        /\ backendState' = _TETrace[j].backendState
        /\ rnfCompUCSeen  = _TETrace[i].rnfCompUCSeen
        /\ rnfCompUCSeen' = _TETrace[j].rnfCompUCSeen
        /\ hnfPendingOwnerUpdate  = _TETrace[i].hnfPendingOwnerUpdate
        /\ hnfPendingOwnerUpdate' = _TETrace[j].hnfPendingOwnerUpdate
        /\ cpuData  = _TETrace[i].cpuData
        /\ cpuData' = _TETrace[j].cpuData
        /\ hnfTbeValid  = _TETrace[i].hnfTbeValid
        /\ hnfTbeValid' = _TETrace[j].hnfTbeValid
        /\ hnfTbePhase  = _TETrace[i].hnfTbePhase
        /\ hnfTbePhase' = _TETrace[j].hnfTbePhase
        /\ hnfSharers  = _TETrace[i].hnfSharers
        /\ hnfSharers' = _TETrace[j].hnfSharers
        /\ hnfOwner  = _TETrace[i].hnfOwner
        /\ hnfOwner' = _TETrace[j].hnfOwner
        /\ cpuState  = _TETrace[i].cpuState
        /\ cpuState' = _TETrace[j].cpuState
        /\ snfState  = _TETrace[i].snfState
        /\ snfState' = _TETrace[j].snfState
        /\ cpuTargetSock  = _TETrace[i].cpuTargetSock
        /\ cpuTargetSock' = _TETrace[j].cpuTargetSock

\* Uncomment the ASSUME below to write the states of the error trace
\* to the given file in Json format. Note that you can pass any tuple
\* to `JsonSerialize`. For example, a sub-sequence of _TETrace.
    \* ASSUME
    \*     LET J == INSTANCE Json
    \*         IN J!JsonSerialize("ep_intra_node_dual_TTrace_1781969408.json", _TETrace)

=============================================================================

 Note that you can extract this module `ep_intra_node_dual_TEExpression`
  to a dedicated file to reuse `expression` (the module in the 
  dedicated `ep_intra_node_dual_TEExpression.tla` file takes precedence 
  over the module `ep_intra_node_dual_TEExpression` below).

---- MODULE ep_intra_node_dual_TEExpression ----
EXTENDS Sequences, TLCExt, Toolbox, Naturals, TLC, ep_intra_node_dual

expression == 
    [
        \* To hide variables of the `ep_intra_node_dual` spec from the error trace,
        \* remove the variables below.  The trace will be written in the order
        \* of the fields of this record.
        hnfData |-> hnfData
        ,reqQ |-> reqQ
        ,interSocketQ |-> interSocketQ
        ,cpuPendingData |-> cpuPendingData
        ,rspQ |-> rspQ
        ,hnfTbeRequester |-> hnfTbeRequester
        ,hnfCacheLine |-> hnfCacheLine
        ,hnfTbeGrantData |-> hnfTbeGrantData
        ,dramWritten |-> dramWritten
        ,cpuPendingKind |-> cpuPendingKind
        ,latestGlobalWrite |-> latestGlobalWrite
        ,rnfState |-> rnfState
        ,hnfState |-> hnfState
        ,backendGrantData |-> backendGrantData
        ,datQ |-> datQ
        ,snpQ |-> snpQ
        ,rnfCompAckSent |-> rnfCompAckSent
        ,hnfTbeNeedData |-> hnfTbeNeedData
        ,hnfTbeOp |-> hnfTbeOp
        ,dramData |-> dramData
        ,rnfCallbackArmed |-> rnfCallbackArmed
        ,backendState |-> backendState
        ,rnfCompUCSeen |-> rnfCompUCSeen
        ,hnfPendingOwnerUpdate |-> hnfPendingOwnerUpdate
        ,cpuData |-> cpuData
        ,hnfTbeValid |-> hnfTbeValid
        ,hnfTbePhase |-> hnfTbePhase
        ,hnfSharers |-> hnfSharers
        ,hnfOwner |-> hnfOwner
        ,cpuState |-> cpuState
        ,snfState |-> snfState
        ,cpuTargetSock |-> cpuTargetSock
        
        \* Put additional constant-, state-, and action-level expressions here:
        \* ,_stateNumber |-> _TEPosition
        \* ,_hnfDataUnchanged |-> hnfData = hnfData'
        
        \* Format the `hnfData` variable as Json value.
        \* ,_hnfDataJson |->
        \*     LET J == INSTANCE Json
        \*     IN J!ToJson(hnfData)
        
        \* Lastly, you may build expressions over arbitrary sets of states by
        \* leveraging the _TETrace operator.  For example, this is how to
        \* count the number of times a spec variable changed up to the current
        \* state in the trace.
        \* ,_hnfDataModCount |->
        \*     LET F[s \in DOMAIN _TETrace] ==
        \*         IF s = 1 THEN 0
        \*         ELSE IF _TETrace[s].hnfData # _TETrace[s-1].hnfData
        \*             THEN 1 + F[s-1] ELSE F[s-1]
        \*     IN F[_TEPosition - 1]
    ]

=============================================================================



Parsing and semantic processing can take forever if the trace below is long.
 In this case, it is advised to uncomment the module below to deserialize the
 trace from a generated binary file.

\*
\*---- MODULE ep_intra_node_dual_TETrace ----
\*EXTENDS IOUtils, TLC, ep_intra_node_dual
\*
\*trace == IODeserialize("ep_intra_node_dual_TTrace_1781969408.bin", TRUE)
\*
\*=============================================================================
\*

---- MODULE ep_intra_node_dual_TETrace ----
EXTENDS TLC, ep_intra_node_dual

trace == 
    <<
    ([cpuTargetSock |-> (0 :> 0 @@ 1 :> 0),hnfTbeGrantData |-> (0 :> 0 @@ 1 :> 0),reqQ |-> (0 :> <<>> @@ 1 :> <<>>),hnfTbeRequester |-> (0 :> -1 @@ 1 :> -1),hnfTbeValid |-> (0 :> FALSE @@ 1 :> FALSE),backendState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),snpQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingKind |-> (0 :> "NONE" @@ 1 :> "NONE"),rnfCompAckSent |-> (0 :> FALSE @@ 1 :> FALSE),cpuData |-> (0 :> 0 @@ 1 :> 0),snfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),rnfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),hnfData |-> (0 :> 0 @@ 1 :> 0),rspQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingData |-> (0 :> 0 @@ 1 :> 0),dramWritten |-> (0 :> FALSE @@ 1 :> FALSE),interSocketQ |-> <<>>,dramData |-> (0 :> 0 @@ 1 :> 0),hnfPendingOwnerUpdate |-> (0 :> FALSE @@ 1 :> FALSE),hnfState |-> (0 :> "I" @@ 1 :> "I"),rnfCompUCSeen |-> (0 :> FALSE @@ 1 :> FALSE),backendGrantData |-> (0 :> 0 @@ 1 :> 0),latestGlobalWrite |-> 0,hnfCacheLine |-> (0 :> FALSE @@ 1 :> FALSE),datQ |-> (0 :> <<>> @@ 1 :> <<>>),hnfOwner |-> (0 :> -1 @@ 1 :> -1),cpuState |-> (0 :> "I" @@ 1 :> "I"),hnfTbeNeedData |-> (0 :> FALSE @@ 1 :> FALSE),rnfCallbackArmed |-> (0 :> FALSE @@ 1 :> FALSE),hnfTbePhase |-> (0 :> "NONE" @@ 1 :> "NONE"),hnfTbeOp |-> (0 :> "NONE" @@ 1 :> "NONE"),hnfSharers |-> (0 :> {} @@ 1 :> {})]),
    ([cpuTargetSock |-> (0 :> 0 @@ 1 :> 0),hnfTbeGrantData |-> (0 :> 0 @@ 1 :> 0),reqQ |-> (0 :> <<>> @@ 1 :> <<>>),hnfTbeRequester |-> (0 :> -1 @@ 1 :> -1),hnfTbeValid |-> (0 :> FALSE @@ 1 :> FALSE),backendState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),snpQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingKind |-> (0 :> "NONE" @@ 1 :> "RS"),rnfCompAckSent |-> (0 :> FALSE @@ 1 :> FALSE),cpuData |-> (0 :> 0 @@ 1 :> 0),snfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),rnfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),hnfData |-> (0 :> 0 @@ 1 :> 0),rspQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingData |-> (0 :> 0 @@ 1 :> 0),dramWritten |-> (0 :> FALSE @@ 1 :> FALSE),interSocketQ |-> <<[kind |-> "REMOTE_REQ", data |-> 0, srcSock |-> 1, dstSock |-> 0, cpu |-> 1, op |-> "RS", lat |-> 1]>>,dramData |-> (0 :> 0 @@ 1 :> 0),hnfPendingOwnerUpdate |-> (0 :> FALSE @@ 1 :> FALSE),hnfState |-> (0 :> "I" @@ 1 :> "I"),rnfCompUCSeen |-> (0 :> FALSE @@ 1 :> FALSE),backendGrantData |-> (0 :> 0 @@ 1 :> 0),latestGlobalWrite |-> 0,hnfCacheLine |-> (0 :> FALSE @@ 1 :> FALSE),datQ |-> (0 :> <<>> @@ 1 :> <<>>),hnfOwner |-> (0 :> -1 @@ 1 :> -1),cpuState |-> (0 :> "I" @@ 1 :> "P_RS"),hnfTbeNeedData |-> (0 :> FALSE @@ 1 :> FALSE),rnfCallbackArmed |-> (0 :> FALSE @@ 1 :> FALSE),hnfTbePhase |-> (0 :> "NONE" @@ 1 :> "NONE"),hnfTbeOp |-> (0 :> "NONE" @@ 1 :> "NONE"),hnfSharers |-> (0 :> {} @@ 1 :> {})]),
    ([cpuTargetSock |-> (0 :> 0 @@ 1 :> 0),hnfTbeGrantData |-> (0 :> 0 @@ 1 :> 0),reqQ |-> (0 :> <<>> @@ 1 :> <<>>),hnfTbeRequester |-> (0 :> -1 @@ 1 :> -1),hnfTbeValid |-> (0 :> FALSE @@ 1 :> FALSE),backendState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),snpQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingKind |-> (0 :> "NONE" @@ 1 :> "RS"),rnfCompAckSent |-> (0 :> FALSE @@ 1 :> FALSE),cpuData |-> (0 :> 0 @@ 1 :> 0),snfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),rnfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),hnfData |-> (0 :> 0 @@ 1 :> 0),rspQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingData |-> (0 :> 0 @@ 1 :> 0),dramWritten |-> (0 :> FALSE @@ 1 :> FALSE),interSocketQ |-> <<[kind |-> "REMOTE_REQ", data |-> 0, srcSock |-> 1, dstSock |-> 0, cpu |-> 1, op |-> "RS", lat |-> 0]>>,dramData |-> (0 :> 0 @@ 1 :> 0),hnfPendingOwnerUpdate |-> (0 :> FALSE @@ 1 :> FALSE),hnfState |-> (0 :> "I" @@ 1 :> "I"),rnfCompUCSeen |-> (0 :> FALSE @@ 1 :> FALSE),backendGrantData |-> (0 :> 0 @@ 1 :> 0),latestGlobalWrite |-> 0,hnfCacheLine |-> (0 :> FALSE @@ 1 :> FALSE),datQ |-> (0 :> <<>> @@ 1 :> <<>>),hnfOwner |-> (0 :> -1 @@ 1 :> -1),cpuState |-> (0 :> "I" @@ 1 :> "P_RS"),hnfTbeNeedData |-> (0 :> FALSE @@ 1 :> FALSE),rnfCallbackArmed |-> (0 :> FALSE @@ 1 :> FALSE),hnfTbePhase |-> (0 :> "NONE" @@ 1 :> "NONE"),hnfTbeOp |-> (0 :> "NONE" @@ 1 :> "NONE"),hnfSharers |-> (0 :> {} @@ 1 :> {})]),
    ([cpuTargetSock |-> (0 :> 0 @@ 1 :> 0),hnfTbeGrantData |-> (0 :> 0 @@ 1 :> 0),reqQ |-> (0 :> <<[kind |-> "RS", dst |-> 1, data |-> 0, srcSock |-> 1]>> @@ 1 :> <<>>),hnfTbeRequester |-> (0 :> -1 @@ 1 :> -1),hnfTbeValid |-> (0 :> FALSE @@ 1 :> FALSE),backendState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),snpQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingKind |-> (0 :> "NONE" @@ 1 :> "RS"),rnfCompAckSent |-> (0 :> FALSE @@ 1 :> FALSE),cpuData |-> (0 :> 0 @@ 1 :> 0),snfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),rnfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),hnfData |-> (0 :> 0 @@ 1 :> 0),rspQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingData |-> (0 :> 0 @@ 1 :> 0),dramWritten |-> (0 :> FALSE @@ 1 :> FALSE),interSocketQ |-> <<>>,dramData |-> (0 :> 0 @@ 1 :> 0),hnfPendingOwnerUpdate |-> (0 :> FALSE @@ 1 :> FALSE),hnfState |-> (0 :> "I" @@ 1 :> "I"),rnfCompUCSeen |-> (0 :> FALSE @@ 1 :> FALSE),backendGrantData |-> (0 :> 0 @@ 1 :> 0),latestGlobalWrite |-> 0,hnfCacheLine |-> (0 :> FALSE @@ 1 :> FALSE),datQ |-> (0 :> <<>> @@ 1 :> <<>>),hnfOwner |-> (0 :> -1 @@ 1 :> -1),cpuState |-> (0 :> "I" @@ 1 :> "P_RS"),hnfTbeNeedData |-> (0 :> FALSE @@ 1 :> FALSE),rnfCallbackArmed |-> (0 :> FALSE @@ 1 :> FALSE),hnfTbePhase |-> (0 :> "NONE" @@ 1 :> "NONE"),hnfTbeOp |-> (0 :> "NONE" @@ 1 :> "NONE"),hnfSharers |-> (0 :> {} @@ 1 :> {})]),
    ([cpuTargetSock |-> (0 :> 0 @@ 1 :> 0),hnfTbeGrantData |-> (0 :> 0 @@ 1 :> 0),reqQ |-> (0 :> <<[kind |-> "RS", dst |-> 1, data |-> 0, srcSock |-> 1], [kind |-> "RS", dst |-> 0, data |-> 0, srcSock |-> 0]>> @@ 1 :> <<>>),hnfTbeRequester |-> (0 :> -1 @@ 1 :> -1),hnfTbeValid |-> (0 :> FALSE @@ 1 :> FALSE),backendState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),snpQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingKind |-> (0 :> "RS" @@ 1 :> "RS"),rnfCompAckSent |-> (0 :> FALSE @@ 1 :> FALSE),cpuData |-> (0 :> 0 @@ 1 :> 0),snfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),rnfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),hnfData |-> (0 :> 0 @@ 1 :> 0),rspQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingData |-> (0 :> 0 @@ 1 :> 0),dramWritten |-> (0 :> FALSE @@ 1 :> FALSE),interSocketQ |-> <<>>,dramData |-> (0 :> 0 @@ 1 :> 0),hnfPendingOwnerUpdate |-> (0 :> FALSE @@ 1 :> FALSE),hnfState |-> (0 :> "I" @@ 1 :> "I"),rnfCompUCSeen |-> (0 :> FALSE @@ 1 :> FALSE),backendGrantData |-> (0 :> 0 @@ 1 :> 0),latestGlobalWrite |-> 0,hnfCacheLine |-> (0 :> FALSE @@ 1 :> FALSE),datQ |-> (0 :> <<>> @@ 1 :> <<>>),hnfOwner |-> (0 :> -1 @@ 1 :> -1),cpuState |-> (0 :> "P_RS" @@ 1 :> "P_RS"),hnfTbeNeedData |-> (0 :> FALSE @@ 1 :> FALSE),rnfCallbackArmed |-> (0 :> FALSE @@ 1 :> FALSE),hnfTbePhase |-> (0 :> "NONE" @@ 1 :> "NONE"),hnfTbeOp |-> (0 :> "NONE" @@ 1 :> "NONE"),hnfSharers |-> (0 :> {} @@ 1 :> {})]),
    ([cpuTargetSock |-> (0 :> 0 @@ 1 :> 0),hnfTbeGrantData |-> (0 :> 0 @@ 1 :> 0),reqQ |-> (0 :> <<[kind |-> "RS", dst |-> 0, data |-> 0, srcSock |-> 0]>> @@ 1 :> <<>>),hnfTbeRequester |-> (0 :> 1 @@ 1 :> -1),hnfTbeValid |-> (0 :> TRUE @@ 1 :> FALSE),backendState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),snpQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingKind |-> (0 :> "RS" @@ 1 :> "RS"),rnfCompAckSent |-> (0 :> FALSE @@ 1 :> FALSE),cpuData |-> (0 :> 0 @@ 1 :> 0),snfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),rnfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),hnfData |-> (0 :> 0 @@ 1 :> 0),rspQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingData |-> (0 :> 0 @@ 1 :> 0),dramWritten |-> (0 :> FALSE @@ 1 :> FALSE),interSocketQ |-> <<>>,dramData |-> (0 :> 0 @@ 1 :> 0),hnfPendingOwnerUpdate |-> (0 :> FALSE @@ 1 :> FALSE),hnfState |-> (0 :> "I" @@ 1 :> "I"),rnfCompUCSeen |-> (0 :> FALSE @@ 1 :> FALSE),backendGrantData |-> (0 :> 0 @@ 1 :> 0),latestGlobalWrite |-> 0,hnfCacheLine |-> (0 :> FALSE @@ 1 :> FALSE),datQ |-> (0 :> <<>> @@ 1 :> <<>>),hnfOwner |-> (0 :> -1 @@ 1 :> -1),cpuState |-> (0 :> "P_RS" @@ 1 :> "P_RS"),hnfTbeNeedData |-> (0 :> TRUE @@ 1 :> FALSE),rnfCallbackArmed |-> (0 :> FALSE @@ 1 :> FALSE),hnfTbePhase |-> (0 :> "WAIT_SNF" @@ 1 :> "NONE"),hnfTbeOp |-> (0 :> "RS" @@ 1 :> "NONE"),hnfSharers |-> (0 :> {} @@ 1 :> {})]),
    ([cpuTargetSock |-> (0 :> 0 @@ 1 :> 0),hnfTbeGrantData |-> (0 :> 0 @@ 1 :> 0),reqQ |-> (0 :> <<[kind |-> "RS", dst |-> 0, data |-> 0, srcSock |-> 0]>> @@ 1 :> <<>>),hnfTbeRequester |-> (0 :> 1 @@ 1 :> -1),hnfTbeValid |-> (0 :> TRUE @@ 1 :> FALSE),backendState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),snpQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingKind |-> (0 :> "RS" @@ 1 :> "RS"),rnfCompAckSent |-> (0 :> FALSE @@ 1 :> FALSE),cpuData |-> (0 :> 0 @@ 1 :> 0),snfState |-> (0 :> "FORWARDING" @@ 1 :> "IDLE"),rnfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),hnfData |-> (0 :> 0 @@ 1 :> 0),rspQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingData |-> (0 :> 0 @@ 1 :> 0),dramWritten |-> (0 :> FALSE @@ 1 :> FALSE),interSocketQ |-> <<>>,dramData |-> (0 :> 0 @@ 1 :> 0),hnfPendingOwnerUpdate |-> (0 :> FALSE @@ 1 :> FALSE),hnfState |-> (0 :> "I" @@ 1 :> "I"),rnfCompUCSeen |-> (0 :> FALSE @@ 1 :> FALSE),backendGrantData |-> (0 :> 0 @@ 1 :> 0),latestGlobalWrite |-> 0,hnfCacheLine |-> (0 :> FALSE @@ 1 :> FALSE),datQ |-> (0 :> <<>> @@ 1 :> <<>>),hnfOwner |-> (0 :> -1 @@ 1 :> -1),cpuState |-> (0 :> "P_RS" @@ 1 :> "P_RS"),hnfTbeNeedData |-> (0 :> TRUE @@ 1 :> FALSE),rnfCallbackArmed |-> (0 :> FALSE @@ 1 :> FALSE),hnfTbePhase |-> (0 :> "WAIT_BACKEND" @@ 1 :> "NONE"),hnfTbeOp |-> (0 :> "RS" @@ 1 :> "NONE"),hnfSharers |-> (0 :> {} @@ 1 :> {})]),
    ([cpuTargetSock |-> (0 :> 0 @@ 1 :> 0),hnfTbeGrantData |-> (0 :> 0 @@ 1 :> 0),reqQ |-> (0 :> <<[kind |-> "RS", dst |-> 0, data |-> 0, srcSock |-> 0]>> @@ 1 :> <<>>),hnfTbeRequester |-> (0 :> 1 @@ 1 :> -1),hnfTbeValid |-> (0 :> TRUE @@ 1 :> FALSE),backendState |-> (0 :> "WAITING_GRANT" @@ 1 :> "IDLE"),snpQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingKind |-> (0 :> "RS" @@ 1 :> "RS"),rnfCompAckSent |-> (0 :> FALSE @@ 1 :> FALSE),cpuData |-> (0 :> 0 @@ 1 :> 0),snfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),rnfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),hnfData |-> (0 :> 0 @@ 1 :> 0),rspQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingData |-> (0 :> 0 @@ 1 :> 0),dramWritten |-> (0 :> FALSE @@ 1 :> FALSE),interSocketQ |-> <<>>,dramData |-> (0 :> 0 @@ 1 :> 0),hnfPendingOwnerUpdate |-> (0 :> FALSE @@ 1 :> FALSE),hnfState |-> (0 :> "I" @@ 1 :> "I"),rnfCompUCSeen |-> (0 :> FALSE @@ 1 :> FALSE),backendGrantData |-> (0 :> 0 @@ 1 :> 0),latestGlobalWrite |-> 0,hnfCacheLine |-> (0 :> FALSE @@ 1 :> FALSE),datQ |-> (0 :> <<>> @@ 1 :> <<>>),hnfOwner |-> (0 :> -1 @@ 1 :> -1),cpuState |-> (0 :> "P_RS" @@ 1 :> "P_RS"),hnfTbeNeedData |-> (0 :> TRUE @@ 1 :> FALSE),rnfCallbackArmed |-> (0 :> FALSE @@ 1 :> FALSE),hnfTbePhase |-> (0 :> "WAIT_BACKEND" @@ 1 :> "NONE"),hnfTbeOp |-> (0 :> "RS" @@ 1 :> "NONE"),hnfSharers |-> (0 :> {} @@ 1 :> {})]),
    ([cpuTargetSock |-> (0 :> 0 @@ 1 :> 0),hnfTbeGrantData |-> (0 :> 0 @@ 1 :> 0),reqQ |-> (0 :> <<[kind |-> "RS", dst |-> 0, data |-> 0, srcSock |-> 0]>> @@ 1 :> <<>>),hnfTbeRequester |-> (0 :> 1 @@ 1 :> -1),hnfTbeValid |-> (0 :> TRUE @@ 1 :> FALSE),backendState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),snpQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingKind |-> (0 :> "RS" @@ 1 :> "RS"),rnfCompAckSent |-> (0 :> FALSE @@ 1 :> FALSE),cpuData |-> (0 :> 0 @@ 1 :> 0),snfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),rnfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),hnfData |-> (0 :> 0 @@ 1 :> 0),rspQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingData |-> (0 :> 0 @@ 1 :> 0),dramWritten |-> (0 :> FALSE @@ 1 :> FALSE),interSocketQ |-> <<>>,dramData |-> (0 :> 0 @@ 1 :> 0),hnfPendingOwnerUpdate |-> (0 :> FALSE @@ 1 :> FALSE),hnfState |-> (0 :> "I" @@ 1 :> "I"),rnfCompUCSeen |-> (0 :> FALSE @@ 1 :> FALSE),backendGrantData |-> (0 :> 0 @@ 1 :> 0),latestGlobalWrite |-> 0,hnfCacheLine |-> (0 :> FALSE @@ 1 :> FALSE),datQ |-> (0 :> <<[kind |-> "SNF_GRANT", dst |-> 0, data |-> 0, srcSock |-> 0]>> @@ 1 :> <<>>),hnfOwner |-> (0 :> -1 @@ 1 :> -1),cpuState |-> (0 :> "P_RS" @@ 1 :> "P_RS"),hnfTbeNeedData |-> (0 :> TRUE @@ 1 :> FALSE),rnfCallbackArmed |-> (0 :> FALSE @@ 1 :> FALSE),hnfTbePhase |-> (0 :> "WAIT_BACKEND" @@ 1 :> "NONE"),hnfTbeOp |-> (0 :> "RS" @@ 1 :> "NONE"),hnfSharers |-> (0 :> {} @@ 1 :> {})]),
    ([cpuTargetSock |-> (0 :> 0 @@ 1 :> 0),hnfTbeGrantData |-> (0 :> 0 @@ 1 :> 0),reqQ |-> (0 :> <<[kind |-> "RS", dst |-> 0, data |-> 0, srcSock |-> 0]>> @@ 1 :> <<>>),hnfTbeRequester |-> (0 :> -1 @@ 1 :> -1),hnfTbeValid |-> (0 :> FALSE @@ 1 :> FALSE),backendState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),snpQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingKind |-> (0 :> "RS" @@ 1 :> "RS"),rnfCompAckSent |-> (0 :> FALSE @@ 1 :> FALSE),cpuData |-> (0 :> 0 @@ 1 :> 0),snfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),rnfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),hnfData |-> (0 :> 0 @@ 1 :> 0),rspQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingData |-> (0 :> 0 @@ 1 :> 0),dramWritten |-> (0 :> FALSE @@ 1 :> FALSE),interSocketQ |-> <<[kind |-> "REMOTE_GRANT", data |-> 0, srcSock |-> 0, dstSock |-> 1, cpu |-> 1, op |-> "RS", lat |-> 1]>>,dramData |-> (0 :> 0 @@ 1 :> 0),hnfPendingOwnerUpdate |-> (0 :> FALSE @@ 1 :> FALSE),hnfState |-> (0 :> "SC" @@ 1 :> "I"),rnfCompUCSeen |-> (0 :> FALSE @@ 1 :> FALSE),backendGrantData |-> (0 :> 0 @@ 1 :> 0),latestGlobalWrite |-> 0,hnfCacheLine |-> (0 :> TRUE @@ 1 :> FALSE),datQ |-> (0 :> <<>> @@ 1 :> <<>>),hnfOwner |-> (0 :> -1 @@ 1 :> -1),cpuState |-> (0 :> "P_RS" @@ 1 :> "P_RS"),hnfTbeNeedData |-> (0 :> FALSE @@ 1 :> FALSE),rnfCallbackArmed |-> (0 :> FALSE @@ 1 :> FALSE),hnfTbePhase |-> (0 :> "NONE" @@ 1 :> "NONE"),hnfTbeOp |-> (0 :> "NONE" @@ 1 :> "NONE"),hnfSharers |-> (0 :> {3} @@ 1 :> {})]),
    ([cpuTargetSock |-> (0 :> 0 @@ 1 :> 0),hnfTbeGrantData |-> (0 :> 0 @@ 1 :> 0),reqQ |-> (0 :> <<[kind |-> "RS", dst |-> 0, data |-> 0, srcSock |-> 0]>> @@ 1 :> <<>>),hnfTbeRequester |-> (0 :> -1 @@ 1 :> -1),hnfTbeValid |-> (0 :> FALSE @@ 1 :> FALSE),backendState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),snpQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingKind |-> (0 :> "RS" @@ 1 :> "RS"),rnfCompAckSent |-> (0 :> FALSE @@ 1 :> FALSE),cpuData |-> (0 :> 0 @@ 1 :> 0),snfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),rnfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),hnfData |-> (0 :> 0 @@ 1 :> 0),rspQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingData |-> (0 :> 0 @@ 1 :> 0),dramWritten |-> (0 :> FALSE @@ 1 :> FALSE),interSocketQ |-> <<[kind |-> "REMOTE_GRANT", data |-> 0, srcSock |-> 0, dstSock |-> 1, cpu |-> 1, op |-> "RS", lat |-> 0]>>,dramData |-> (0 :> 0 @@ 1 :> 0),hnfPendingOwnerUpdate |-> (0 :> FALSE @@ 1 :> FALSE),hnfState |-> (0 :> "SC" @@ 1 :> "I"),rnfCompUCSeen |-> (0 :> FALSE @@ 1 :> FALSE),backendGrantData |-> (0 :> 0 @@ 1 :> 0),latestGlobalWrite |-> 0,hnfCacheLine |-> (0 :> TRUE @@ 1 :> FALSE),datQ |-> (0 :> <<>> @@ 1 :> <<>>),hnfOwner |-> (0 :> -1 @@ 1 :> -1),cpuState |-> (0 :> "P_RS" @@ 1 :> "P_RS"),hnfTbeNeedData |-> (0 :> FALSE @@ 1 :> FALSE),rnfCallbackArmed |-> (0 :> FALSE @@ 1 :> FALSE),hnfTbePhase |-> (0 :> "NONE" @@ 1 :> "NONE"),hnfTbeOp |-> (0 :> "NONE" @@ 1 :> "NONE"),hnfSharers |-> (0 :> {3} @@ 1 :> {})]),
    ([cpuTargetSock |-> (0 :> 0 @@ 1 :> 0),hnfTbeGrantData |-> (0 :> 0 @@ 1 :> 0),reqQ |-> (0 :> <<[kind |-> "RS", dst |-> 0, data |-> 0, srcSock |-> 0]>> @@ 1 :> <<>>),hnfTbeRequester |-> (0 :> -1 @@ 1 :> -1),hnfTbeValid |-> (0 :> FALSE @@ 1 :> FALSE),backendState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),snpQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingKind |-> (0 :> "RS" @@ 1 :> "RS"),rnfCompAckSent |-> (0 :> FALSE @@ 1 :> FALSE),cpuData |-> (0 :> 0 @@ 1 :> 0),snfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),rnfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),hnfData |-> (0 :> 0 @@ 1 :> 0),rspQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingData |-> (0 :> 0 @@ 1 :> 0),dramWritten |-> (0 :> FALSE @@ 1 :> FALSE),interSocketQ |-> <<>>,dramData |-> (0 :> 0 @@ 1 :> 0),hnfPendingOwnerUpdate |-> (0 :> FALSE @@ 1 :> FALSE),hnfState |-> (0 :> "SC" @@ 1 :> "I"),rnfCompUCSeen |-> (0 :> FALSE @@ 1 :> FALSE),backendGrantData |-> (0 :> 0 @@ 1 :> 0),latestGlobalWrite |-> 0,hnfCacheLine |-> (0 :> TRUE @@ 1 :> FALSE),datQ |-> (0 :> <<>> @@ 1 :> <<[kind |-> "CPU_GRANT_RS", dst |-> 1, data |-> 0, srcSock |-> 0]>>),hnfOwner |-> (0 :> -1 @@ 1 :> -1),cpuState |-> (0 :> "P_RS" @@ 1 :> "P_RS"),hnfTbeNeedData |-> (0 :> FALSE @@ 1 :> FALSE),rnfCallbackArmed |-> (0 :> FALSE @@ 1 :> FALSE),hnfTbePhase |-> (0 :> "NONE" @@ 1 :> "NONE"),hnfTbeOp |-> (0 :> "NONE" @@ 1 :> "NONE"),hnfSharers |-> (0 :> {3} @@ 1 :> {})]),
    ([cpuTargetSock |-> (0 :> 0 @@ 1 :> 0),hnfTbeGrantData |-> (0 :> 0 @@ 1 :> 0),reqQ |-> (0 :> <<>> @@ 1 :> <<>>),hnfTbeRequester |-> (0 :> 0 @@ 1 :> -1),hnfTbeValid |-> (0 :> TRUE @@ 1 :> FALSE),backendState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),snpQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingKind |-> (0 :> "RS" @@ 1 :> "RS"),rnfCompAckSent |-> (0 :> FALSE @@ 1 :> FALSE),cpuData |-> (0 :> 0 @@ 1 :> 0),snfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),rnfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),hnfData |-> (0 :> 0 @@ 1 :> 0),rspQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingData |-> (0 :> 0 @@ 1 :> 0),dramWritten |-> (0 :> FALSE @@ 1 :> FALSE),interSocketQ |-> <<>>,dramData |-> (0 :> 0 @@ 1 :> 0),hnfPendingOwnerUpdate |-> (0 :> FALSE @@ 1 :> FALSE),hnfState |-> (0 :> "SC" @@ 1 :> "I"),rnfCompUCSeen |-> (0 :> FALSE @@ 1 :> FALSE),backendGrantData |-> (0 :> 0 @@ 1 :> 0),latestGlobalWrite |-> 0,hnfCacheLine |-> (0 :> TRUE @@ 1 :> FALSE),datQ |-> (0 :> <<>> @@ 1 :> <<[kind |-> "CPU_GRANT_RS", dst |-> 1, data |-> 0, srcSock |-> 0]>>),hnfOwner |-> (0 :> -1 @@ 1 :> -1),cpuState |-> (0 :> "P_RS" @@ 1 :> "P_RS"),hnfTbeNeedData |-> (0 :> TRUE @@ 1 :> FALSE),rnfCallbackArmed |-> (0 :> FALSE @@ 1 :> FALSE),hnfTbePhase |-> (0 :> "WAIT_SNF" @@ 1 :> "NONE"),hnfTbeOp |-> (0 :> "RS" @@ 1 :> "NONE"),hnfSharers |-> (0 :> {3} @@ 1 :> {})]),
    ([cpuTargetSock |-> (0 :> 0 @@ 1 :> 0),hnfTbeGrantData |-> (0 :> 0 @@ 1 :> 0),reqQ |-> (0 :> <<>> @@ 1 :> <<>>),hnfTbeRequester |-> (0 :> 0 @@ 1 :> -1),hnfTbeValid |-> (0 :> TRUE @@ 1 :> FALSE),backendState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),snpQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingKind |-> (0 :> "RS" @@ 1 :> "RS"),rnfCompAckSent |-> (0 :> FALSE @@ 1 :> FALSE),cpuData |-> (0 :> 0 @@ 1 :> 0),snfState |-> (0 :> "FORWARDING" @@ 1 :> "IDLE"),rnfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),hnfData |-> (0 :> 0 @@ 1 :> 0),rspQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingData |-> (0 :> 0 @@ 1 :> 0),dramWritten |-> (0 :> FALSE @@ 1 :> FALSE),interSocketQ |-> <<>>,dramData |-> (0 :> 0 @@ 1 :> 0),hnfPendingOwnerUpdate |-> (0 :> FALSE @@ 1 :> FALSE),hnfState |-> (0 :> "SC" @@ 1 :> "I"),rnfCompUCSeen |-> (0 :> FALSE @@ 1 :> FALSE),backendGrantData |-> (0 :> 0 @@ 1 :> 0),latestGlobalWrite |-> 0,hnfCacheLine |-> (0 :> TRUE @@ 1 :> FALSE),datQ |-> (0 :> <<>> @@ 1 :> <<[kind |-> "CPU_GRANT_RS", dst |-> 1, data |-> 0, srcSock |-> 0]>>),hnfOwner |-> (0 :> -1 @@ 1 :> -1),cpuState |-> (0 :> "P_RS" @@ 1 :> "P_RS"),hnfTbeNeedData |-> (0 :> TRUE @@ 1 :> FALSE),rnfCallbackArmed |-> (0 :> FALSE @@ 1 :> FALSE),hnfTbePhase |-> (0 :> "WAIT_BACKEND" @@ 1 :> "NONE"),hnfTbeOp |-> (0 :> "RS" @@ 1 :> "NONE"),hnfSharers |-> (0 :> {3} @@ 1 :> {})]),
    ([cpuTargetSock |-> (0 :> 0 @@ 1 :> 0),hnfTbeGrantData |-> (0 :> 0 @@ 1 :> 0),reqQ |-> (0 :> <<>> @@ 1 :> <<>>),hnfTbeRequester |-> (0 :> 0 @@ 1 :> -1),hnfTbeValid |-> (0 :> TRUE @@ 1 :> FALSE),backendState |-> (0 :> "WAITING_GRANT" @@ 1 :> "IDLE"),snpQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingKind |-> (0 :> "RS" @@ 1 :> "RS"),rnfCompAckSent |-> (0 :> FALSE @@ 1 :> FALSE),cpuData |-> (0 :> 0 @@ 1 :> 0),snfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),rnfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),hnfData |-> (0 :> 0 @@ 1 :> 0),rspQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingData |-> (0 :> 0 @@ 1 :> 0),dramWritten |-> (0 :> FALSE @@ 1 :> FALSE),interSocketQ |-> <<>>,dramData |-> (0 :> 0 @@ 1 :> 0),hnfPendingOwnerUpdate |-> (0 :> FALSE @@ 1 :> FALSE),hnfState |-> (0 :> "SC" @@ 1 :> "I"),rnfCompUCSeen |-> (0 :> FALSE @@ 1 :> FALSE),backendGrantData |-> (0 :> 0 @@ 1 :> 0),latestGlobalWrite |-> 0,hnfCacheLine |-> (0 :> TRUE @@ 1 :> FALSE),datQ |-> (0 :> <<>> @@ 1 :> <<[kind |-> "CPU_GRANT_RS", dst |-> 1, data |-> 0, srcSock |-> 0]>>),hnfOwner |-> (0 :> -1 @@ 1 :> -1),cpuState |-> (0 :> "P_RS" @@ 1 :> "P_RS"),hnfTbeNeedData |-> (0 :> TRUE @@ 1 :> FALSE),rnfCallbackArmed |-> (0 :> FALSE @@ 1 :> FALSE),hnfTbePhase |-> (0 :> "WAIT_BACKEND" @@ 1 :> "NONE"),hnfTbeOp |-> (0 :> "RS" @@ 1 :> "NONE"),hnfSharers |-> (0 :> {3} @@ 1 :> {})]),
    ([cpuTargetSock |-> (0 :> 0 @@ 1 :> 0),hnfTbeGrantData |-> (0 :> 0 @@ 1 :> 0),reqQ |-> (0 :> <<>> @@ 1 :> <<>>),hnfTbeRequester |-> (0 :> 0 @@ 1 :> -1),hnfTbeValid |-> (0 :> TRUE @@ 1 :> FALSE),backendState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),snpQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingKind |-> (0 :> "RS" @@ 1 :> "RS"),rnfCompAckSent |-> (0 :> FALSE @@ 1 :> FALSE),cpuData |-> (0 :> 0 @@ 1 :> 0),snfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),rnfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),hnfData |-> (0 :> 0 @@ 1 :> 0),rspQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingData |-> (0 :> 0 @@ 1 :> 0),dramWritten |-> (0 :> FALSE @@ 1 :> FALSE),interSocketQ |-> <<>>,dramData |-> (0 :> 0 @@ 1 :> 0),hnfPendingOwnerUpdate |-> (0 :> FALSE @@ 1 :> FALSE),hnfState |-> (0 :> "SC" @@ 1 :> "I"),rnfCompUCSeen |-> (0 :> FALSE @@ 1 :> FALSE),backendGrantData |-> (0 :> 1 @@ 1 :> 0),latestGlobalWrite |-> 0,hnfCacheLine |-> (0 :> TRUE @@ 1 :> FALSE),datQ |-> (0 :> <<[kind |-> "SNF_GRANT", dst |-> 0, data |-> 1, srcSock |-> 0]>> @@ 1 :> <<[kind |-> "CPU_GRANT_RS", dst |-> 1, data |-> 0, srcSock |-> 0]>>),hnfOwner |-> (0 :> -1 @@ 1 :> -1),cpuState |-> (0 :> "P_RS" @@ 1 :> "P_RS"),hnfTbeNeedData |-> (0 :> TRUE @@ 1 :> FALSE),rnfCallbackArmed |-> (0 :> FALSE @@ 1 :> FALSE),hnfTbePhase |-> (0 :> "WAIT_BACKEND" @@ 1 :> "NONE"),hnfTbeOp |-> (0 :> "RS" @@ 1 :> "NONE"),hnfSharers |-> (0 :> {3} @@ 1 :> {})]),
    ([cpuTargetSock |-> (0 :> 0 @@ 1 :> 0),hnfTbeGrantData |-> (0 :> 0 @@ 1 :> 0),reqQ |-> (0 :> <<>> @@ 1 :> <<>>),hnfTbeRequester |-> (0 :> -1 @@ 1 :> -1),hnfTbeValid |-> (0 :> FALSE @@ 1 :> FALSE),backendState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),snpQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingKind |-> (0 :> "NONE" @@ 1 :> "RS"),rnfCompAckSent |-> (0 :> FALSE @@ 1 :> FALSE),cpuData |-> (0 :> 1 @@ 1 :> 0),snfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),rnfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),hnfData |-> (0 :> 1 @@ 1 :> 0),rspQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingData |-> (0 :> 0 @@ 1 :> 0),dramWritten |-> (0 :> FALSE @@ 1 :> FALSE),interSocketQ |-> <<>>,dramData |-> (0 :> 0 @@ 1 :> 0),hnfPendingOwnerUpdate |-> (0 :> FALSE @@ 1 :> FALSE),hnfState |-> (0 :> "SC" @@ 1 :> "I"),rnfCompUCSeen |-> (0 :> FALSE @@ 1 :> FALSE),backendGrantData |-> (0 :> 1 @@ 1 :> 0),latestGlobalWrite |-> 0,hnfCacheLine |-> (0 :> TRUE @@ 1 :> FALSE),datQ |-> (0 :> <<>> @@ 1 :> <<[kind |-> "CPU_GRANT_RS", dst |-> 1, data |-> 0, srcSock |-> 0]>>),hnfOwner |-> (0 :> -1 @@ 1 :> -1),cpuState |-> (0 :> "SC" @@ 1 :> "P_RS"),hnfTbeNeedData |-> (0 :> FALSE @@ 1 :> FALSE),rnfCallbackArmed |-> (0 :> FALSE @@ 1 :> FALSE),hnfTbePhase |-> (0 :> "NONE" @@ 1 :> "NONE"),hnfTbeOp |-> (0 :> "NONE" @@ 1 :> "NONE"),hnfSharers |-> (0 :> {0, 3} @@ 1 :> {})]),
    ([cpuTargetSock |-> (0 :> 0 @@ 1 :> 0),hnfTbeGrantData |-> (0 :> 0 @@ 1 :> 0),reqQ |-> (0 :> <<>> @@ 1 :> <<>>),hnfTbeRequester |-> (0 :> -1 @@ 1 :> -1),hnfTbeValid |-> (0 :> FALSE @@ 1 :> FALSE),backendState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),snpQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingKind |-> (0 :> "NONE" @@ 1 :> "NONE"),rnfCompAckSent |-> (0 :> FALSE @@ 1 :> FALSE),cpuData |-> (0 :> 1 @@ 1 :> 0),snfState |-> (0 :> "IDLE" @@ 1 :> "IDLE"),rnfState |-> (0 :> "IDLE" @@ 1 :> "HAVE_SC"),hnfData |-> (0 :> 1 @@ 1 :> 0),rspQ |-> (0 :> <<>> @@ 1 :> <<>>),cpuPendingData |-> (0 :> 0 @@ 1 :> 0),dramWritten |-> (0 :> FALSE @@ 1 :> FALSE),interSocketQ |-> <<>>,dramData |-> (0 :> 0 @@ 1 :> 0),hnfPendingOwnerUpdate |-> (0 :> FALSE @@ 1 :> FALSE),hnfState |-> (0 :> "SC" @@ 1 :> "I"),rnfCompUCSeen |-> (0 :> FALSE @@ 1 :> FALSE),backendGrantData |-> (0 :> 1 @@ 1 :> 0),latestGlobalWrite |-> 0,hnfCacheLine |-> (0 :> TRUE @@ 1 :> FALSE),datQ |-> (0 :> <<>> @@ 1 :> <<>>),hnfOwner |-> (0 :> -1 @@ 1 :> -1),cpuState |-> (0 :> "SC" @@ 1 :> "SC"),hnfTbeNeedData |-> (0 :> FALSE @@ 1 :> FALSE),rnfCallbackArmed |-> (0 :> FALSE @@ 1 :> FALSE),hnfTbePhase |-> (0 :> "NONE" @@ 1 :> "NONE"),hnfTbeOp |-> (0 :> "NONE" @@ 1 :> "NONE"),hnfSharers |-> (0 :> {0, 3} @@ 1 :> {})])
    >>
----


=============================================================================

---- CONFIG ep_intra_node_dual_TTrace_1781969408 ----
CONSTANTS
    NumCPUs = 2
    MaxDataVersion = 1
    NumSockets = 2

INVARIANT
    _inv

CHECK_DEADLOCK
    \* CHECK_DEADLOCK off because of PROPERTY or INVARIANT above.
    FALSE

INIT
    _init

NEXT
    _next

CONSTANT
    _TETrace <- _trace

ALIAS
    _expression
=============================================================================
\* Generated on Sat Jun 20 23:30:09 CST 2026