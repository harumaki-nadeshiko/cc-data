---- MODULE ubcc_protocol_TTrace_1782101305 ----
EXTENDS ubcc_protocol, Sequences, TLCExt, ubcc_protocol_TEConstants, Toolbox, Naturals, TLC

_expression ==
    LET ubcc_protocol_TEExpression == INSTANCE ubcc_protocol_TEExpression
    IN ubcc_protocol_TEExpression!expression
----

_trace ==
    LET ubcc_protocol_TETrace == INSTANCE ubcc_protocol_TETrace
    IN ubcc_protocol_TETrace!trace
----

_inv ==
    ~(
        TLCGet("level") = Len(_TETrace)
        /\
        ostTargetMask = ({2})
        /\
        ostReservedEpoch = (2)
        /\
        tombstone = ((<<0, 0>> :> FALSE @@ <<0, 1>> :> FALSE @@ <<0, 2>> :> FALSE @@ <<0, 3>> :> FALSE @@ <<0, 4>> :> FALSE @@ <<1, 0>> :> TRUE @@ <<1, 1>> :> FALSE @@ <<1, 2>> :> FALSE @@ <<1, 3>> :> FALSE @@ <<1, 4>> :> FALSE @@ <<2, 0>> :> FALSE @@ <<2, 1>> :> FALSE @@ <<2, 2>> :> FALSE @@ <<2, 3>> :> FALSE @@ <<2, 4>> :> FALSE @@ <<3, 0>> :> FALSE @@ <<3, 1>> :> FALSE @@ <<3, 2>> :> FALSE @@ <<3, 3>> :> FALSE @@ <<3, 4>> :> FALSE @@ <<4, 0>> :> FALSE @@ <<4, 1>> :> FALSE @@ <<4, 2>> :> FALSE @@ <<4, 3>> :> FALSE @@ <<4, 4>> :> FALSE))
        /\
        ostAccepted = (FALSE)
        /\
        ostStage = ("WAITING_CLEAR")
        /\
        ostIntendedSharers = ({0})
        /\
        tick = (0)
        /\
        ostOpType = ("GRANT_HANDSHAKE")
        /\
        committedSharers = ({})
        /\
        ostIntendedState = ("G_E")
        /\
        ostAckMask = ({2})
        /\
        ostBaseEpoch = (0)
        /\
        committedEpoch = (1)
        /\
        ostRecallDone = (FALSE)
        /\
        committedState = ("G_S")
        /\
        commitLog = (<<<<1, 0>>>>)
        /\
        ostRequester = (0)
        /\
        committedOwner = (-1)
        /\
        committedDirty = (FALSE)
        /\
        ostIntendedOwner = (0)
        /\
        ostInvalidateDone = (TRUE)
        /\
        ostReqId = (0)
    )
----

_init ==
    /\ ostIntendedSharers = _TETrace[1].ostIntendedSharers
    /\ ostAckMask = _TETrace[1].ostAckMask
    /\ ostAccepted = _TETrace[1].ostAccepted
    /\ commitLog = _TETrace[1].commitLog
    /\ ostInvalidateDone = _TETrace[1].ostInvalidateDone
    /\ ostReqId = _TETrace[1].ostReqId
    /\ committedOwner = _TETrace[1].committedOwner
    /\ committedEpoch = _TETrace[1].committedEpoch
    /\ ostBaseEpoch = _TETrace[1].ostBaseEpoch
    /\ committedDirty = _TETrace[1].committedDirty
    /\ ostRecallDone = _TETrace[1].ostRecallDone
    /\ ostStage = _TETrace[1].ostStage
    /\ tick = _TETrace[1].tick
    /\ committedState = _TETrace[1].committedState
    /\ ostTargetMask = _TETrace[1].ostTargetMask
    /\ ostIntendedOwner = _TETrace[1].ostIntendedOwner
    /\ ostOpType = _TETrace[1].ostOpType
    /\ committedSharers = _TETrace[1].committedSharers
    /\ ostReservedEpoch = _TETrace[1].ostReservedEpoch
    /\ tombstone = _TETrace[1].tombstone
    /\ ostRequester = _TETrace[1].ostRequester
    /\ ostIntendedState = _TETrace[1].ostIntendedState
----

_next ==
    /\ \E i,j \in DOMAIN _TETrace:
        /\ \/ /\ j = i + 1
              /\ i = TLCGet("level")
        /\ ostIntendedSharers  = _TETrace[i].ostIntendedSharers
        /\ ostIntendedSharers' = _TETrace[j].ostIntendedSharers
        /\ ostAckMask  = _TETrace[i].ostAckMask
        /\ ostAckMask' = _TETrace[j].ostAckMask
        /\ ostAccepted  = _TETrace[i].ostAccepted
        /\ ostAccepted' = _TETrace[j].ostAccepted
        /\ commitLog  = _TETrace[i].commitLog
        /\ commitLog' = _TETrace[j].commitLog
        /\ ostInvalidateDone  = _TETrace[i].ostInvalidateDone
        /\ ostInvalidateDone' = _TETrace[j].ostInvalidateDone
        /\ ostReqId  = _TETrace[i].ostReqId
        /\ ostReqId' = _TETrace[j].ostReqId
        /\ committedOwner  = _TETrace[i].committedOwner
        /\ committedOwner' = _TETrace[j].committedOwner
        /\ committedEpoch  = _TETrace[i].committedEpoch
        /\ committedEpoch' = _TETrace[j].committedEpoch
        /\ ostBaseEpoch  = _TETrace[i].ostBaseEpoch
        /\ ostBaseEpoch' = _TETrace[j].ostBaseEpoch
        /\ committedDirty  = _TETrace[i].committedDirty
        /\ committedDirty' = _TETrace[j].committedDirty
        /\ ostRecallDone  = _TETrace[i].ostRecallDone
        /\ ostRecallDone' = _TETrace[j].ostRecallDone
        /\ ostStage  = _TETrace[i].ostStage
        /\ ostStage' = _TETrace[j].ostStage
        /\ tick  = _TETrace[i].tick
        /\ tick' = _TETrace[j].tick
        /\ committedState  = _TETrace[i].committedState
        /\ committedState' = _TETrace[j].committedState
        /\ ostTargetMask  = _TETrace[i].ostTargetMask
        /\ ostTargetMask' = _TETrace[j].ostTargetMask
        /\ ostIntendedOwner  = _TETrace[i].ostIntendedOwner
        /\ ostIntendedOwner' = _TETrace[j].ostIntendedOwner
        /\ ostOpType  = _TETrace[i].ostOpType
        /\ ostOpType' = _TETrace[j].ostOpType
        /\ committedSharers  = _TETrace[i].committedSharers
        /\ committedSharers' = _TETrace[j].committedSharers
        /\ ostReservedEpoch  = _TETrace[i].ostReservedEpoch
        /\ ostReservedEpoch' = _TETrace[j].ostReservedEpoch
        /\ tombstone  = _TETrace[i].tombstone
        /\ tombstone' = _TETrace[j].tombstone
        /\ ostRequester  = _TETrace[i].ostRequester
        /\ ostRequester' = _TETrace[j].ostRequester
        /\ ostIntendedState  = _TETrace[i].ostIntendedState
        /\ ostIntendedState' = _TETrace[j].ostIntendedState

\* Uncomment the ASSUME below to write the states of the error trace
\* to the given file in Json format. Note that you can pass any tuple
\* to `JsonSerialize`. For example, a sub-sequence of _TETrace.
    \* ASSUME
    \*     LET J == INSTANCE Json
    \*         IN J!JsonSerialize("ubcc_protocol_TTrace_1782101305.json", _TETrace)

=============================================================================

 Note that you can extract this module `ubcc_protocol_TEExpression`
  to a dedicated file to reuse `expression` (the module in the 
  dedicated `ubcc_protocol_TEExpression.tla` file takes precedence 
  over the module `ubcc_protocol_TEExpression` below).

---- MODULE ubcc_protocol_TEExpression ----
EXTENDS ubcc_protocol, Sequences, TLCExt, ubcc_protocol_TEConstants, Toolbox, Naturals, TLC

expression == 
    [
        \* To hide variables of the `ubcc_protocol` spec from the error trace,
        \* remove the variables below.  The trace will be written in the order
        \* of the fields of this record.
        ostIntendedSharers |-> ostIntendedSharers
        ,ostAckMask |-> ostAckMask
        ,ostAccepted |-> ostAccepted
        ,commitLog |-> commitLog
        ,ostInvalidateDone |-> ostInvalidateDone
        ,ostReqId |-> ostReqId
        ,committedOwner |-> committedOwner
        ,committedEpoch |-> committedEpoch
        ,ostBaseEpoch |-> ostBaseEpoch
        ,committedDirty |-> committedDirty
        ,ostRecallDone |-> ostRecallDone
        ,ostStage |-> ostStage
        ,tick |-> tick
        ,committedState |-> committedState
        ,ostTargetMask |-> ostTargetMask
        ,ostIntendedOwner |-> ostIntendedOwner
        ,ostOpType |-> ostOpType
        ,committedSharers |-> committedSharers
        ,ostReservedEpoch |-> ostReservedEpoch
        ,tombstone |-> tombstone
        ,ostRequester |-> ostRequester
        ,ostIntendedState |-> ostIntendedState
        
        \* Put additional constant-, state-, and action-level expressions here:
        \* ,_stateNumber |-> _TEPosition
        \* ,_ostIntendedSharersUnchanged |-> ostIntendedSharers = ostIntendedSharers'
        
        \* Format the `ostIntendedSharers` variable as Json value.
        \* ,_ostIntendedSharersJson |->
        \*     LET J == INSTANCE Json
        \*     IN J!ToJson(ostIntendedSharers)
        
        \* Lastly, you may build expressions over arbitrary sets of states by
        \* leveraging the _TETrace operator.  For example, this is how to
        \* count the number of times a spec variable changed up to the current
        \* state in the trace.
        \* ,_ostIntendedSharersModCount |->
        \*     LET F[s \in DOMAIN _TETrace] ==
        \*         IF s = 1 THEN 0
        \*         ELSE IF _TETrace[s].ostIntendedSharers # _TETrace[s-1].ostIntendedSharers
        \*             THEN 1 + F[s-1] ELSE F[s-1]
        \*     IN F[_TEPosition - 1]
    ]

=============================================================================



Parsing and semantic processing can take forever if the trace below is long.
 In this case, it is advised to uncomment the module below to deserialize the
 trace from a generated binary file.

\*
\*---- MODULE ubcc_protocol_TETrace ----
\*EXTENDS ubcc_protocol, IOUtils, ubcc_protocol_TEConstants, TLC
\*
\*trace == IODeserialize("ubcc_protocol_TTrace_1782101305.bin", TRUE)
\*
\*=============================================================================
\*

---- MODULE ubcc_protocol_TETrace ----
EXTENDS ubcc_protocol, ubcc_protocol_TEConstants, TLC

trace == 
    <<
    ([ostTargetMask |-> {},ostReservedEpoch |-> 0,tombstone |-> (<<0, 0>> :> FALSE @@ <<0, 1>> :> FALSE @@ <<0, 2>> :> FALSE @@ <<0, 3>> :> FALSE @@ <<0, 4>> :> FALSE @@ <<1, 0>> :> FALSE @@ <<1, 1>> :> FALSE @@ <<1, 2>> :> FALSE @@ <<1, 3>> :> FALSE @@ <<1, 4>> :> FALSE @@ <<2, 0>> :> FALSE @@ <<2, 1>> :> FALSE @@ <<2, 2>> :> FALSE @@ <<2, 3>> :> FALSE @@ <<2, 4>> :> FALSE @@ <<3, 0>> :> FALSE @@ <<3, 1>> :> FALSE @@ <<3, 2>> :> FALSE @@ <<3, 3>> :> FALSE @@ <<3, 4>> :> FALSE @@ <<4, 0>> :> FALSE @@ <<4, 1>> :> FALSE @@ <<4, 2>> :> FALSE @@ <<4, 3>> :> FALSE @@ <<4, 4>> :> FALSE),ostAccepted |-> FALSE,ostStage |-> "CREATED",ostIntendedSharers |-> {},tick |-> 0,ostOpType |-> "NONE",committedSharers |-> {},ostIntendedState |-> "G_I",ostAckMask |-> {},ostBaseEpoch |-> 0,committedEpoch |-> 0,ostRecallDone |-> FALSE,committedState |-> "G_I",commitLog |-> <<>>,ostRequester |-> -1,committedOwner |-> -1,committedDirty |-> FALSE,ostIntendedOwner |-> -1,ostInvalidateDone |-> FALSE,ostReqId |-> 0]),
    ([ostTargetMask |-> {},ostReservedEpoch |-> 1,tombstone |-> (<<0, 0>> :> FALSE @@ <<0, 1>> :> FALSE @@ <<0, 2>> :> FALSE @@ <<0, 3>> :> FALSE @@ <<0, 4>> :> FALSE @@ <<1, 0>> :> FALSE @@ <<1, 1>> :> FALSE @@ <<1, 2>> :> FALSE @@ <<1, 3>> :> FALSE @@ <<1, 4>> :> FALSE @@ <<2, 0>> :> FALSE @@ <<2, 1>> :> FALSE @@ <<2, 2>> :> FALSE @@ <<2, 3>> :> FALSE @@ <<2, 4>> :> FALSE @@ <<3, 0>> :> FALSE @@ <<3, 1>> :> FALSE @@ <<3, 2>> :> FALSE @@ <<3, 3>> :> FALSE @@ <<3, 4>> :> FALSE @@ <<4, 0>> :> FALSE @@ <<4, 1>> :> FALSE @@ <<4, 2>> :> FALSE @@ <<4, 3>> :> FALSE @@ <<4, 4>> :> FALSE),ostAccepted |-> FALSE,ostStage |-> "WAITING_CLEAR",ostIntendedSharers |-> {2},tick |-> 0,ostOpType |-> "GRANT_HANDSHAKE",committedSharers |-> {},ostIntendedState |-> "G_S",ostAckMask |-> {},ostBaseEpoch |-> 0,committedEpoch |-> 0,ostRecallDone |-> FALSE,committedState |-> "G_I",commitLog |-> <<>>,ostRequester |-> 2,committedOwner |-> -1,committedDirty |-> FALSE,ostIntendedOwner |-> -1,ostInvalidateDone |-> FALSE,ostReqId |-> 0]),
    ([ostTargetMask |-> {},ostReservedEpoch |-> 1,tombstone |-> (<<0, 0>> :> FALSE @@ <<0, 1>> :> FALSE @@ <<0, 2>> :> FALSE @@ <<0, 3>> :> FALSE @@ <<0, 4>> :> FALSE @@ <<1, 0>> :> TRUE @@ <<1, 1>> :> FALSE @@ <<1, 2>> :> FALSE @@ <<1, 3>> :> FALSE @@ <<1, 4>> :> FALSE @@ <<2, 0>> :> FALSE @@ <<2, 1>> :> FALSE @@ <<2, 2>> :> FALSE @@ <<2, 3>> :> FALSE @@ <<2, 4>> :> FALSE @@ <<3, 0>> :> FALSE @@ <<3, 1>> :> FALSE @@ <<3, 2>> :> FALSE @@ <<3, 3>> :> FALSE @@ <<3, 4>> :> FALSE @@ <<4, 0>> :> FALSE @@ <<4, 1>> :> FALSE @@ <<4, 2>> :> FALSE @@ <<4, 3>> :> FALSE @@ <<4, 4>> :> FALSE),ostAccepted |-> FALSE,ostStage |-> "DONE",ostIntendedSharers |-> {2},tick |-> 0,ostOpType |-> "NONE",committedSharers |-> {2},ostIntendedState |-> "G_S",ostAckMask |-> {},ostBaseEpoch |-> 0,committedEpoch |-> 1,ostRecallDone |-> FALSE,committedState |-> "G_S",commitLog |-> <<<<1, 0>>>>,ostRequester |-> 2,committedOwner |-> -1,committedDirty |-> FALSE,ostIntendedOwner |-> -1,ostInvalidateDone |-> FALSE,ostReqId |-> 0]),
    ([ostTargetMask |-> {2},ostReservedEpoch |-> 2,tombstone |-> (<<0, 0>> :> FALSE @@ <<0, 1>> :> FALSE @@ <<0, 2>> :> FALSE @@ <<0, 3>> :> FALSE @@ <<0, 4>> :> FALSE @@ <<1, 0>> :> TRUE @@ <<1, 1>> :> FALSE @@ <<1, 2>> :> FALSE @@ <<1, 3>> :> FALSE @@ <<1, 4>> :> FALSE @@ <<2, 0>> :> FALSE @@ <<2, 1>> :> FALSE @@ <<2, 2>> :> FALSE @@ <<2, 3>> :> FALSE @@ <<2, 4>> :> FALSE @@ <<3, 0>> :> FALSE @@ <<3, 1>> :> FALSE @@ <<3, 2>> :> FALSE @@ <<3, 3>> :> FALSE @@ <<3, 4>> :> FALSE @@ <<4, 0>> :> FALSE @@ <<4, 1>> :> FALSE @@ <<4, 2>> :> FALSE @@ <<4, 3>> :> FALSE @@ <<4, 4>> :> FALSE),ostAccepted |-> FALSE,ostStage |-> "WAITING_ALL_ACKS",ostIntendedSharers |-> {0},tick |-> 0,ostOpType |-> "INVALIDATE",committedSharers |-> {2},ostIntendedState |-> "G_E",ostAckMask |-> {},ostBaseEpoch |-> 0,committedEpoch |-> 1,ostRecallDone |-> FALSE,committedState |-> "G_S",commitLog |-> <<<<1, 0>>>>,ostRequester |-> 0,committedOwner |-> -1,committedDirty |-> FALSE,ostIntendedOwner |-> 0,ostInvalidateDone |-> FALSE,ostReqId |-> 0]),
    ([ostTargetMask |-> {2},ostReservedEpoch |-> 2,tombstone |-> (<<0, 0>> :> FALSE @@ <<0, 1>> :> FALSE @@ <<0, 2>> :> FALSE @@ <<0, 3>> :> FALSE @@ <<0, 4>> :> FALSE @@ <<1, 0>> :> TRUE @@ <<1, 1>> :> FALSE @@ <<1, 2>> :> FALSE @@ <<1, 3>> :> FALSE @@ <<1, 4>> :> FALSE @@ <<2, 0>> :> FALSE @@ <<2, 1>> :> FALSE @@ <<2, 2>> :> FALSE @@ <<2, 3>> :> FALSE @@ <<2, 4>> :> FALSE @@ <<3, 0>> :> FALSE @@ <<3, 1>> :> FALSE @@ <<3, 2>> :> FALSE @@ <<3, 3>> :> FALSE @@ <<3, 4>> :> FALSE @@ <<4, 0>> :> FALSE @@ <<4, 1>> :> FALSE @@ <<4, 2>> :> FALSE @@ <<4, 3>> :> FALSE @@ <<4, 4>> :> FALSE),ostAccepted |-> FALSE,ostStage |-> "WAITING_CLEAR",ostIntendedSharers |-> {0},tick |-> 0,ostOpType |-> "GRANT_HANDSHAKE",committedSharers |-> {},ostIntendedState |-> "G_E",ostAckMask |-> {2},ostBaseEpoch |-> 0,committedEpoch |-> 1,ostRecallDone |-> FALSE,committedState |-> "G_S",commitLog |-> <<<<1, 0>>>>,ostRequester |-> 0,committedOwner |-> -1,committedDirty |-> FALSE,ostIntendedOwner |-> 0,ostInvalidateDone |-> TRUE,ostReqId |-> 0])
    >>
----


=============================================================================

---- MODULE ubcc_protocol_TEConstants ----
EXTENDS ubcc_protocol

CONSTANTS G_I, G_S, G_E, G_M

=============================================================================

---- CONFIG ubcc_protocol_TTrace_1782101305 ----
CONSTANTS
    Nodes = { 0 , 1 , 2 }
    MaxEpoch = 4
    TombstoneWindow = 10
    G_I = G_I
    G_S = G_S
    G_E = G_E
    G_M = G_M
    G_M = G_M
    G_I = G_I
    G_E = G_E
    G_S = G_S

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
\* Generated on Mon Jun 22 12:08:26 CST 2026