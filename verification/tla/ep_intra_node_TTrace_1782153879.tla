---- MODULE ep_intra_node_TTrace_1782153879 ----
EXTENDS Sequences, TLCExt, ep_intra_node, Toolbox, Naturals, TLC

_expression ==
    LET ep_intra_node_TEExpression == INSTANCE ep_intra_node_TEExpression
    IN ep_intra_node_TEExpression!expression
----

_trace ==
    LET ep_intra_node_TETrace == INSTANCE ep_intra_node_TETrace
    IN ep_intra_node_TETrace!trace
----

_inv ==
    ~(
        TLCGet("level") = Len(_TETrace)
        /\
        cpuReq = ((0 :> "NONE"))
        /\
        rnfCompAckSent = ((0 :> FALSE))
        /\
        hnfState = ((0 :> "H_WAIT_WB"))
        /\
        reqMsgs = (<<>>)
        /\
        rnfCompUCSeen = ((0 :> FALSE))
        /\
        rnfState = ((0 :> "IDLE"))
        /\
        rspMsgs = (<<>>)
        /\
        datMsgs = (<<[src |-> 0, dst |-> 0, kind |-> "WB", txn |-> 0, ver |-> 1]>>)
        /\
        snpMsgs = (<<[src |-> 0, dst |-> 0, kind |-> "SNP_RS", txn |-> 0, ver |-> 0]>>)
        /\
        snfBusy = ((0 :> FALSE))
        /\
        hnfPendingReq = ((0 :> "RS"))
        /\
        dataVer = ((0 :> 1))
        /\
        hnfAwaiting = ((0 :> [snf |-> FALSE, snp |-> TRUE, comp |-> FALSE, wb |-> TRUE]))
    )
----

_init ==
    /\ rspMsgs = _TETrace[1].rspMsgs
    /\ hnfAwaiting = _TETrace[1].hnfAwaiting
    /\ dataVer = _TETrace[1].dataVer
    /\ rnfState = _TETrace[1].rnfState
    /\ hnfState = _TETrace[1].hnfState
    /\ snpMsgs = _TETrace[1].snpMsgs
    /\ rnfCompAckSent = _TETrace[1].rnfCompAckSent
    /\ datMsgs = _TETrace[1].datMsgs
    /\ rnfCompUCSeen = _TETrace[1].rnfCompUCSeen
    /\ reqMsgs = _TETrace[1].reqMsgs
    /\ snfBusy = _TETrace[1].snfBusy
    /\ cpuReq = _TETrace[1].cpuReq
    /\ hnfPendingReq = _TETrace[1].hnfPendingReq
----

_next ==
    /\ \E i,j \in DOMAIN _TETrace:
        /\ \/ /\ j = i + 1
              /\ i = TLCGet("level")
        /\ rspMsgs  = _TETrace[i].rspMsgs
        /\ rspMsgs' = _TETrace[j].rspMsgs
        /\ hnfAwaiting  = _TETrace[i].hnfAwaiting
        /\ hnfAwaiting' = _TETrace[j].hnfAwaiting
        /\ dataVer  = _TETrace[i].dataVer
        /\ dataVer' = _TETrace[j].dataVer
        /\ rnfState  = _TETrace[i].rnfState
        /\ rnfState' = _TETrace[j].rnfState
        /\ hnfState  = _TETrace[i].hnfState
        /\ hnfState' = _TETrace[j].hnfState
        /\ snpMsgs  = _TETrace[i].snpMsgs
        /\ snpMsgs' = _TETrace[j].snpMsgs
        /\ rnfCompAckSent  = _TETrace[i].rnfCompAckSent
        /\ rnfCompAckSent' = _TETrace[j].rnfCompAckSent
        /\ datMsgs  = _TETrace[i].datMsgs
        /\ datMsgs' = _TETrace[j].datMsgs
        /\ rnfCompUCSeen  = _TETrace[i].rnfCompUCSeen
        /\ rnfCompUCSeen' = _TETrace[j].rnfCompUCSeen
        /\ reqMsgs  = _TETrace[i].reqMsgs
        /\ reqMsgs' = _TETrace[j].reqMsgs
        /\ snfBusy  = _TETrace[i].snfBusy
        /\ snfBusy' = _TETrace[j].snfBusy
        /\ cpuReq  = _TETrace[i].cpuReq
        /\ cpuReq' = _TETrace[j].cpuReq
        /\ hnfPendingReq  = _TETrace[i].hnfPendingReq
        /\ hnfPendingReq' = _TETrace[j].hnfPendingReq

\* Uncomment the ASSUME below to write the states of the error trace
\* to the given file in Json format. Note that you can pass any tuple
\* to `JsonSerialize`. For example, a sub-sequence of _TETrace.
    \* ASSUME
    \*     LET J == INSTANCE Json
    \*         IN J!JsonSerialize("ep_intra_node_TTrace_1782153879.json", _TETrace)

=============================================================================

 Note that you can extract this module `ep_intra_node_TEExpression`
  to a dedicated file to reuse `expression` (the module in the 
  dedicated `ep_intra_node_TEExpression.tla` file takes precedence 
  over the module `ep_intra_node_TEExpression` below).

---- MODULE ep_intra_node_TEExpression ----
EXTENDS Sequences, TLCExt, ep_intra_node, Toolbox, Naturals, TLC

expression == 
    [
        \* To hide variables of the `ep_intra_node` spec from the error trace,
        \* remove the variables below.  The trace will be written in the order
        \* of the fields of this record.
        rspMsgs |-> rspMsgs
        ,hnfAwaiting |-> hnfAwaiting
        ,dataVer |-> dataVer
        ,rnfState |-> rnfState
        ,hnfState |-> hnfState
        ,snpMsgs |-> snpMsgs
        ,rnfCompAckSent |-> rnfCompAckSent
        ,datMsgs |-> datMsgs
        ,rnfCompUCSeen |-> rnfCompUCSeen
        ,reqMsgs |-> reqMsgs
        ,snfBusy |-> snfBusy
        ,cpuReq |-> cpuReq
        ,hnfPendingReq |-> hnfPendingReq
        
        \* Put additional constant-, state-, and action-level expressions here:
        \* ,_stateNumber |-> _TEPosition
        \* ,_rspMsgsUnchanged |-> rspMsgs = rspMsgs'
        
        \* Format the `rspMsgs` variable as Json value.
        \* ,_rspMsgsJson |->
        \*     LET J == INSTANCE Json
        \*     IN J!ToJson(rspMsgs)
        
        \* Lastly, you may build expressions over arbitrary sets of states by
        \* leveraging the _TETrace operator.  For example, this is how to
        \* count the number of times a spec variable changed up to the current
        \* state in the trace.
        \* ,_rspMsgsModCount |->
        \*     LET F[s \in DOMAIN _TETrace] ==
        \*         IF s = 1 THEN 0
        \*         ELSE IF _TETrace[s].rspMsgs # _TETrace[s-1].rspMsgs
        \*             THEN 1 + F[s-1] ELSE F[s-1]
        \*     IN F[_TEPosition - 1]
    ]

=============================================================================



Parsing and semantic processing can take forever if the trace below is long.
 In this case, it is advised to uncomment the module below to deserialize the
 trace from a generated binary file.

\*
\*---- MODULE ep_intra_node_TETrace ----
\*EXTENDS IOUtils, ep_intra_node, TLC
\*
\*trace == IODeserialize("ep_intra_node_TTrace_1782153879.bin", TRUE)
\*
\*=============================================================================
\*

---- MODULE ep_intra_node_TETrace ----
EXTENDS ep_intra_node, TLC

trace == 
    <<
    ([cpuReq |-> (0 :> "NONE"),rnfCompAckSent |-> (0 :> FALSE),hnfState |-> (0 :> "H_IDLE"),reqMsgs |-> <<>>,rnfCompUCSeen |-> (0 :> FALSE),rnfState |-> (0 :> "IDLE"),rspMsgs |-> <<>>,datMsgs |-> <<>>,snpMsgs |-> <<>>,snfBusy |-> (0 :> FALSE),hnfPendingReq |-> (0 :> "NONE"),dataVer |-> (0 :> 0),hnfAwaiting |-> (0 :> [snf |-> FALSE, snp |-> FALSE, comp |-> FALSE, wb |-> FALSE])]),
    ([cpuReq |-> (0 :> "RU"),rnfCompAckSent |-> (0 :> FALSE),hnfState |-> (0 :> "H_IDLE"),reqMsgs |-> <<>>,rnfCompUCSeen |-> (0 :> FALSE),rnfState |-> (0 :> "IDLE"),rspMsgs |-> <<>>,datMsgs |-> <<>>,snpMsgs |-> <<>>,snfBusy |-> (0 :> FALSE),hnfPendingReq |-> (0 :> "NONE"),dataVer |-> (0 :> 0),hnfAwaiting |-> (0 :> [snf |-> FALSE, snp |-> FALSE, comp |-> FALSE, wb |-> FALSE])]),
    ([cpuReq |-> (0 :> "NONE"),rnfCompAckSent |-> (0 :> FALSE),hnfState |-> (0 :> "H_WAIT_SNF"),reqMsgs |-> <<[src |-> 0, dst |-> 0, kind |-> "MISS_RU", txn |-> 0, ver |-> 0]>>,rnfCompUCSeen |-> (0 :> FALSE),rnfState |-> (0 :> "IDLE"),rspMsgs |-> <<>>,datMsgs |-> <<>>,snpMsgs |-> <<>>,snfBusy |-> (0 :> FALSE),hnfPendingReq |-> (0 :> "RU"),dataVer |-> (0 :> 0),hnfAwaiting |-> (0 :> [snf |-> TRUE, snp |-> FALSE, comp |-> FALSE, wb |-> FALSE])]),
    ([cpuReq |-> (0 :> "NONE"),rnfCompAckSent |-> (0 :> FALSE),hnfState |-> (0 :> "H_WAIT_SNP"),reqMsgs |-> <<[src |-> 0, dst |-> 0, kind |-> "FWD_RU", txn |-> 0, ver |-> 0]>>,rnfCompUCSeen |-> (0 :> FALSE),rnfState |-> (0 :> "IDLE"),rspMsgs |-> <<>>,datMsgs |-> <<>>,snpMsgs |-> <<>>,snfBusy |-> (0 :> TRUE),hnfPendingReq |-> (0 :> "RU"),dataVer |-> (0 :> 0),hnfAwaiting |-> (0 :> [snf |-> FALSE, snp |-> FALSE, comp |-> FALSE, wb |-> FALSE])]),
    ([cpuReq |-> (0 :> "NONE"),rnfCompAckSent |-> (0 :> FALSE),hnfState |-> (0 :> "H_WAIT_SNP"),reqMsgs |-> <<>>,rnfCompUCSeen |-> (0 :> FALSE),rnfState |-> (0 :> "IDLE"),rspMsgs |-> <<>>,datMsgs |-> <<>>,snpMsgs |-> <<[src |-> 0, dst |-> 0, kind |-> "SNP_RU", txn |-> 0, ver |-> 0]>>,snfBusy |-> (0 :> FALSE),hnfPendingReq |-> (0 :> "RU"),dataVer |-> (0 :> 0),hnfAwaiting |-> (0 :> [snf |-> FALSE, snp |-> TRUE, comp |-> FALSE, wb |-> FALSE])]),
    ([cpuReq |-> (0 :> "NONE"),rnfCompAckSent |-> (0 :> FALSE),hnfState |-> (0 :> "H_WAIT_COMP"),reqMsgs |-> <<>>,rnfCompUCSeen |-> (0 :> FALSE),rnfState |-> (0 :> "PENDING_RU"),rspMsgs |-> <<[src |-> 0, dst |-> 0, kind |-> "COMP_UC", txn |-> 0, ver |-> 0]>>,datMsgs |-> <<>>,snpMsgs |-> <<>>,snfBusy |-> (0 :> FALSE),hnfPendingReq |-> (0 :> "RU"),dataVer |-> (0 :> 0),hnfAwaiting |-> (0 :> [snf |-> FALSE, snp |-> FALSE, comp |-> TRUE, wb |-> FALSE])]),
    ([cpuReq |-> (0 :> "NONE"),rnfCompAckSent |-> (0 :> FALSE),hnfState |-> (0 :> "H_WAIT_COMP"),reqMsgs |-> <<>>,rnfCompUCSeen |-> (0 :> TRUE),rnfState |-> (0 :> "PENDING_RU"),rspMsgs |-> <<>>,datMsgs |-> <<>>,snpMsgs |-> <<>>,snfBusy |-> (0 :> FALSE),hnfPendingReq |-> (0 :> "RU"),dataVer |-> (0 :> 0),hnfAwaiting |-> (0 :> [snf |-> FALSE, snp |-> FALSE, comp |-> FALSE, wb |-> FALSE])]),
    ([cpuReq |-> (0 :> "NONE"),rnfCompAckSent |-> (0 :> TRUE),hnfState |-> (0 :> "H_IDLE"),reqMsgs |-> <<[src |-> 0, dst |-> 0, kind |-> "COMP_ACK", txn |-> 0, ver |-> 0]>>,rnfCompUCSeen |-> (0 :> TRUE),rnfState |-> (0 :> "PENDING_RU"),rspMsgs |-> <<>>,datMsgs |-> <<>>,snpMsgs |-> <<>>,snfBusy |-> (0 :> FALSE),hnfPendingReq |-> (0 :> "NONE"),dataVer |-> (0 :> 0),hnfAwaiting |-> (0 :> [snf |-> FALSE, snp |-> FALSE, comp |-> FALSE, wb |-> FALSE])]),
    ([cpuReq |-> (0 :> "RS"),rnfCompAckSent |-> (0 :> TRUE),hnfState |-> (0 :> "H_IDLE"),reqMsgs |-> <<[src |-> 0, dst |-> 0, kind |-> "COMP_ACK", txn |-> 0, ver |-> 0]>>,rnfCompUCSeen |-> (0 :> TRUE),rnfState |-> (0 :> "PENDING_RU"),rspMsgs |-> <<>>,datMsgs |-> <<>>,snpMsgs |-> <<>>,snfBusy |-> (0 :> FALSE),hnfPendingReq |-> (0 :> "NONE"),dataVer |-> (0 :> 0),hnfAwaiting |-> (0 :> [snf |-> FALSE, snp |-> FALSE, comp |-> FALSE, wb |-> FALSE])]),
    ([cpuReq |-> (0 :> "NONE"),rnfCompAckSent |-> (0 :> TRUE),hnfState |-> (0 :> "H_WAIT_SNF"),reqMsgs |-> <<[src |-> 0, dst |-> 0, kind |-> "COMP_ACK", txn |-> 0, ver |-> 0], [src |-> 0, dst |-> 0, kind |-> "MISS_RS", txn |-> 0, ver |-> 0]>>,rnfCompUCSeen |-> (0 :> TRUE),rnfState |-> (0 :> "PENDING_RU"),rspMsgs |-> <<>>,datMsgs |-> <<>>,snpMsgs |-> <<>>,snfBusy |-> (0 :> FALSE),hnfPendingReq |-> (0 :> "RS"),dataVer |-> (0 :> 0),hnfAwaiting |-> (0 :> [snf |-> TRUE, snp |-> FALSE, comp |-> FALSE, wb |-> FALSE])]),
    ([cpuReq |-> (0 :> "NONE"),rnfCompAckSent |-> (0 :> FALSE),hnfState |-> (0 :> "H_WAIT_SNF"),reqMsgs |-> <<[src |-> 0, dst |-> 0, kind |-> "MISS_RS", txn |-> 0, ver |-> 0]>>,rnfCompUCSeen |-> (0 :> FALSE),rnfState |-> (0 :> "HAVE_UD"),rspMsgs |-> <<>>,datMsgs |-> <<>>,snpMsgs |-> <<>>,snfBusy |-> (0 :> FALSE),hnfPendingReq |-> (0 :> "RS"),dataVer |-> (0 :> 0),hnfAwaiting |-> (0 :> [snf |-> TRUE, snp |-> FALSE, comp |-> FALSE, wb |-> FALSE])]),
    ([cpuReq |-> (0 :> "NONE"),rnfCompAckSent |-> (0 :> FALSE),hnfState |-> (0 :> "H_WAIT_SNP"),reqMsgs |-> <<[src |-> 0, dst |-> 0, kind |-> "FWD_RS", txn |-> 0, ver |-> 0]>>,rnfCompUCSeen |-> (0 :> FALSE),rnfState |-> (0 :> "HAVE_UD"),rspMsgs |-> <<>>,datMsgs |-> <<>>,snpMsgs |-> <<>>,snfBusy |-> (0 :> TRUE),hnfPendingReq |-> (0 :> "RS"),dataVer |-> (0 :> 0),hnfAwaiting |-> (0 :> [snf |-> FALSE, snp |-> FALSE, comp |-> FALSE, wb |-> FALSE])]),
    ([cpuReq |-> (0 :> "NONE"),rnfCompAckSent |-> (0 :> FALSE),hnfState |-> (0 :> "H_WAIT_SNP"),reqMsgs |-> <<>>,rnfCompUCSeen |-> (0 :> FALSE),rnfState |-> (0 :> "HAVE_UD"),rspMsgs |-> <<>>,datMsgs |-> <<>>,snpMsgs |-> <<[src |-> 0, dst |-> 0, kind |-> "SNP_RS", txn |-> 0, ver |-> 0]>>,snfBusy |-> (0 :> FALSE),hnfPendingReq |-> (0 :> "RS"),dataVer |-> (0 :> 0),hnfAwaiting |-> (0 :> [snf |-> FALSE, snp |-> TRUE, comp |-> FALSE, wb |-> FALSE])]),
    ([cpuReq |-> (0 :> "NONE"),rnfCompAckSent |-> (0 :> FALSE),hnfState |-> (0 :> "H_WAIT_WB"),reqMsgs |-> <<>>,rnfCompUCSeen |-> (0 :> FALSE),rnfState |-> (0 :> "IDLE"),rspMsgs |-> <<>>,datMsgs |-> <<[src |-> 0, dst |-> 0, kind |-> "WB", txn |-> 0, ver |-> 1]>>,snpMsgs |-> <<[src |-> 0, dst |-> 0, kind |-> "SNP_RS", txn |-> 0, ver |-> 0]>>,snfBusy |-> (0 :> FALSE),hnfPendingReq |-> (0 :> "RS"),dataVer |-> (0 :> 1),hnfAwaiting |-> (0 :> [snf |-> FALSE, snp |-> TRUE, comp |-> FALSE, wb |-> TRUE])])
    >>
----


=============================================================================

---- CONFIG ep_intra_node_TTrace_1782153879 ----
CONSTANTS
    Nodes = { 0 }
    MaxTxn = 3

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
\* Generated on Tue Jun 23 02:44:39 CST 2026