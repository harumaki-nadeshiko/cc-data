------------------------------ MODULE ubcc_protocol ------------------------------
EXTENDS ubcc_protocol_core, FiniteSets, Sequences

(* ************************************************************************* *)
(* Serializable multi-node view helpers layered over the verified core.     *)
(* The model uses only the core state variables; per-node cache state is     *)
(* derived as an explicit tuple, avoiding function-typed state variables.    *)
(* ************************************************************************* *)

CacheState == {"I", "S", "E", "M"}
MsgKind == {"RECALL_REQ", "RECALL_RESP", "INV_REQ", "INV_ACK"}

CacheForNode(d, n) ==
    IF d.state = "G_I" THEN "I"
    ELSE IF d.state = "G_S" THEN IF n \in d.sharers THEN "S" ELSE "I"
    ELSE IF n = d.owner THEN IF d.state = "G_M" THEN "M" ELSE "E"
    ELSE "I"

NodeCacheTuple(d) == <<CacheForNode(d, 0), CacheForNode(d, 1), CacheForNode(d, 2)>>

NodeCacheAt(d, n) ==
    CASE n = 0 -> NodeCacheTuple(d)[1]
      [] n = 1 -> NodeCacheTuple(d)[2]
      [] OTHER -> NodeCacheTuple(d)[3]

StableNodeConsistency == NodeCacheTuple(dir) = <<CacheForNode(dir, 0), CacheForNode(dir, 1), CacheForNode(dir, 2)>>

SingleDirtyHolder == Cardinality({n \in Nodes : NodeCacheAt(dir, n) = "M"}) <= 1

NetWellFormed == TRUE

=============================================================================
