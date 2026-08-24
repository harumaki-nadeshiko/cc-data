---------------------- MODULE chi_hnf_capacity_refill ----------------------
EXTENDS Naturals, Sequences, FiniteSets, TLC

(***************************************************************************)
(* Focused CHI HN-F model for the metadata-only EP-RNF fallback.           *)
(*                                                                         *)
(* CacheCapacity is deliberately one: B initially occupies the only data  *)
(* slot, so filling A demonstrates finite-capacity competition. Directory  *)
(* metadata is independent of cacheData; replacing A can therefore leave   *)
(* the sole EP sentinel without claiming that the HN-F still has A's data. *)
(* Request and replacement TBEs have independent data-valid bits, and the  *)
(* actionQ models the order in which protocol actions execute.             *)
(***************************************************************************)

CONSTANTS A, B, EP, CPU, CacheCapacity, TBECapacity, BuggySoleEP

Addresses == {A, B}
Agents == {EP, CPU}
Kinds == {"None", "Request", "Replacement"}
Phases == {"FillAStart", "FillAActive", "ReplaceAStart", "ReplaceAActive",
           "ReadUniqueAStart", "ReadUniqueAActive", "Done"}
Actions == {"SendDownstreamRead", "PublishSharedGrant", "UpgradeBarrier",
             "SendCompData", "CheckCacheFill", "ReplacementWriteback"}
LastActions == Actions \cup {"None", "ReceiveDownstreamData", "FinishFillA", "StartReplaceA",
                              "FinishReplaceA", "StartReadUniqueAWithSoleEP",
                              "FinishReadUniqueA"}

VARIABLES cacheData, dirMeta, activeKind, activeAddr, actionQ,
          reqDataValid, reqDataFull, replDataValid, awaitingDownstreamData,
          activeReadUnique, sharedGrantPublished, upgradeAuthorized,
          phase, lastAction, lastSendHadData, lastSendHadFull,
          lastSendHadUniqueAuth, sentCompData

Vars == <<cacheData, dirMeta, activeKind, activeAddr, actionQ,
          reqDataValid, reqDataFull, replDataValid, awaitingDownstreamData,
          activeReadUnique, sharedGrantPublished, upgradeAuthorized,
          phase, lastAction, lastSendHadData, lastSendHadFull,
          lastSendHadUniqueAuth, sentCompData>>

NoAddr == "NoAddr"

Init ==
    /\ A # B
    /\ cacheData = {B}
    /\ dirMeta = [a \in Addresses |-> IF a = B THEN {CPU} ELSE {}]
    /\ activeKind = "None"
    /\ activeAddr = NoAddr
    /\ actionQ = <<>>
    /\ reqDataValid = [a \in Addresses |-> FALSE]
    /\ reqDataFull = [a \in Addresses |-> FALSE]
    /\ replDataValid = [a \in Addresses |-> FALSE]
    /\ awaitingDownstreamData = FALSE
    /\ activeReadUnique = FALSE
    /\ sharedGrantPublished = FALSE
    /\ upgradeAuthorized = FALSE
    /\ phase = "FillAStart"
    /\ lastAction = "None"
    /\ lastSendHadData = TRUE
    /\ lastSendHadFull = TRUE
    /\ lastSendHadUniqueAuth = TRUE
    /\ sentCompData = 0

StartFillA ==
    /\ phase = "FillAStart"
    /\ activeKind = "None"
    /\ Cardinality(cacheData) = CacheCapacity
    /\ B \in cacheData
    (* A and B compete for the sole data slot; B is the fill victim. *)
    /\ cacheData' = (cacheData \ {B})
    /\ dirMeta' = [dirMeta EXCEPT ![B] = {}, ![A] = {CPU, EP}]
    /\ activeKind' = "Request"
    /\ activeAddr' = A
    /\ actionQ' = <<"SendDownstreamRead", "SendCompData", "CheckCacheFill">>
    /\ reqDataValid' = [reqDataValid EXCEPT ![A] = FALSE]
    /\ reqDataFull' = [reqDataFull EXCEPT ![A] = FALSE]
    /\ phase' = "FillAActive"
    /\ activeReadUnique' = FALSE
    /\ sharedGrantPublished' = FALSE
    /\ upgradeAuthorized' = FALSE
    /\ UNCHANGED <<replDataValid, awaitingDownstreamData, lastAction,
                    lastSendHadData, lastSendHadFull,
                    lastSendHadUniqueAuth, sentCompData>>

RunSendDownstreamRead ==
    /\ activeKind = "Request"
    /\ actionQ # <<>>
    /\ Head(actionQ) = "SendDownstreamRead"
    /\ ~awaitingDownstreamData
    /\ actionQ' = Tail(actionQ)
    /\ awaitingDownstreamData' = TRUE
    /\ lastAction' = "SendDownstreamRead"
    /\ UNCHANGED <<cacheData, dirMeta, activeKind, activeAddr, reqDataValid,
                    reqDataFull, replDataValid, activeReadUnique,
                    sharedGrantPublished, upgradeAuthorized, phase,
                    lastSendHadData, lastSendHadFull,
                    lastSendHadUniqueAuth, sentCompData>>

ReceiveDownstreamData ==
    /\ activeKind = "Request"
    /\ awaitingDownstreamData
    /\ reqDataValid' = [reqDataValid EXCEPT ![activeAddr] = TRUE]
    /\ reqDataFull' = [reqDataFull EXCEPT ![activeAddr] = TRUE]
    /\ awaitingDownstreamData' = FALSE
    /\ lastAction' = "ReceiveDownstreamData"
    /\ UNCHANGED <<cacheData, dirMeta, activeKind, activeAddr, actionQ,
                    replDataValid, activeReadUnique, sharedGrantPublished,
                    upgradeAuthorized, phase, lastSendHadData,
                    lastSendHadFull, lastSendHadUniqueAuth, sentCompData>>

RunPublishSharedGrant ==
    /\ activeKind = "Request"
    /\ activeReadUnique
    /\ actionQ # <<>>
    /\ Head(actionQ) = "PublishSharedGrant"
    /\ reqDataValid[activeAddr]
    /\ reqDataFull[activeAddr]
    /\ ~sharedGrantPublished
    /\ actionQ' = Tail(actionQ)
    /\ sharedGrantPublished' = TRUE
    /\ lastAction' = "PublishSharedGrant"
    /\ UNCHANGED <<cacheData, dirMeta, activeKind, activeAddr, reqDataValid,
                    reqDataFull, replDataValid, awaitingDownstreamData,
                    activeReadUnique, upgradeAuthorized, phase,
                    lastSendHadData, lastSendHadFull,
                    lastSendHadUniqueAuth, sentCompData>>

RunUpgradeBarrier ==
    /\ activeKind = "Request"
    /\ activeReadUnique
    /\ actionQ # <<>>
    /\ Head(actionQ) = "UpgradeBarrier"
    /\ sharedGrantPublished
    /\ ~upgradeAuthorized
    /\ actionQ' = Tail(actionQ)
    /\ upgradeAuthorized' = TRUE
    /\ lastAction' = "UpgradeBarrier"
    /\ UNCHANGED <<cacheData, dirMeta, activeKind, activeAddr, reqDataValid,
                    reqDataFull, replDataValid, awaitingDownstreamData,
                    activeReadUnique, sharedGrantPublished, phase,
                    lastSendHadData, lastSendHadFull,
                    lastSendHadUniqueAuth, sentCompData>>

RunSendCompData ==
    /\ activeKind = "Request"
    /\ actionQ # <<>>
    /\ Head(actionQ) = "SendCompData"
    (* Completion consumes full-line data from the request TBE, not the L3. *)
    /\ reqDataValid[activeAddr]
    /\ reqDataFull[activeAddr]
    /\ (~activeReadUnique \/ upgradeAuthorized)
    /\ actionQ' = Tail(actionQ)
    /\ lastAction' = "SendCompData"
    /\ lastSendHadData' = reqDataValid[activeAddr]
    /\ lastSendHadFull' = reqDataFull[activeAddr]
    /\ lastSendHadUniqueAuth' = (~activeReadUnique \/ upgradeAuthorized)
    /\ sentCompData' = sentCompData + 1
    /\ UNCHANGED <<cacheData, dirMeta, activeKind, activeAddr, reqDataValid,
                    reqDataFull, replDataValid, awaitingDownstreamData,
                    activeReadUnique, sharedGrantPublished, upgradeAuthorized,
                    phase>>

RunCheckCacheFill ==
    /\ activeKind = "Request"
    /\ actionQ # <<>>
    /\ Head(actionQ) = "CheckCacheFill"
    /\ reqDataValid[activeAddr]
    /\ reqDataFull[activeAddr]
    /\ Cardinality(cacheData) < CacheCapacity
    /\ cacheData' = cacheData \cup {activeAddr}
    /\ actionQ' = Tail(actionQ)
    /\ lastAction' = "CheckCacheFill"
    /\ UNCHANGED <<dirMeta, activeKind, activeAddr, reqDataValid, reqDataFull,
                    replDataValid, awaitingDownstreamData, phase,
                    activeReadUnique, sharedGrantPublished, upgradeAuthorized,
                    lastSendHadData, lastSendHadFull,
                    lastSendHadUniqueAuth, sentCompData>>

FinishFillA ==
    /\ phase = "FillAActive"
    /\ activeKind = "Request"
    /\ actionQ = <<>>
    /\ activeKind' = "None"
    /\ activeAddr' = NoAddr
    /\ phase' = "ReplaceAStart"
    /\ lastAction' = "FinishFillA"
    /\ UNCHANGED <<cacheData, dirMeta, actionQ, reqDataValid, reqDataFull,
                    replDataValid, awaitingDownstreamData, lastSendHadData,
                    lastSendHadFull, lastSendHadUniqueAuth, sentCompData,
                    activeReadUnique, sharedGrantPublished, upgradeAuthorized>>

StartReplaceA ==
    /\ phase = "ReplaceAStart"
    /\ activeKind = "None"
    /\ A \in cacheData
    /\ activeKind' = "Replacement"
    /\ activeAddr' = A
    /\ actionQ' = <<"ReplacementWriteback">>
    (* Replacement TBE captures data before the cache-data slot is removed. *)
    /\ replDataValid' = [replDataValid EXCEPT ![A] = TRUE]
    /\ cacheData' = cacheData \ {A}
    (* EP remains as metadata-only external-state bookkeeping. *)
    /\ dirMeta' = [dirMeta EXCEPT ![A] = {EP}]
    /\ phase' = "ReplaceAActive"
    /\ lastAction' = "StartReplaceA"
    /\ UNCHANGED <<reqDataValid, reqDataFull, awaitingDownstreamData,
                    lastSendHadData, lastSendHadFull,
                    lastSendHadUniqueAuth, sentCompData, activeReadUnique,
                    sharedGrantPublished, upgradeAuthorized>>

RunReplacementWriteback ==
    /\ activeKind = "Replacement"
    /\ actionQ # <<>>
    /\ Head(actionQ) = "ReplacementWriteback"
    /\ replDataValid[activeAddr]
    /\ actionQ' = Tail(actionQ)
    /\ lastAction' = "ReplacementWriteback"
    /\ UNCHANGED <<cacheData, dirMeta, activeKind, activeAddr, reqDataValid,
                    reqDataFull, replDataValid, awaitingDownstreamData, phase,
                    lastSendHadData, lastSendHadFull,
                    lastSendHadUniqueAuth, sentCompData, activeReadUnique,
                    sharedGrantPublished, upgradeAuthorized>>

FinishReplaceA ==
    /\ phase = "ReplaceAActive"
    /\ activeKind = "Replacement"
    /\ actionQ = <<>>
    /\ activeKind' = "None"
    /\ activeAddr' = NoAddr
    /\ replDataValid' = [replDataValid EXCEPT ![A] = FALSE]
    /\ phase' = "ReadUniqueAStart"
    /\ lastAction' = "FinishReplaceA"
    /\ UNCHANGED <<cacheData, dirMeta, actionQ, reqDataValid, reqDataFull,
                    awaitingDownstreamData, lastSendHadData, lastSendHadFull,
                    lastSendHadUniqueAuth, sentCompData, activeReadUnique,
                    sharedGrantPublished, upgradeAuthorized>>

StartReadUniqueAWithSoleEP ==
    /\ phase = "ReadUniqueAStart"
    /\ activeKind = "None"
    /\ dirMeta[A] = {EP}
    /\ A \notin cacheData
    /\ activeKind' = "Request"
    /\ activeAddr' = A
    /\ reqDataValid' = [reqDataValid EXCEPT ![A] = FALSE]
    /\ reqDataFull' = [reqDataFull EXCEPT ![A] = FALSE]
    /\ activeReadUnique' = TRUE
    /\ sharedGrantPublished' = FALSE
    /\ upgradeAuthorized' = FALSE
    (* The switch lets the same model demonstrate the old invalid schedule. *)
    /\ actionQ' = IF BuggySoleEP
                  THEN <<"SendCompData">>
                  ELSE <<"SendDownstreamRead", "PublishSharedGrant",
                         "UpgradeBarrier", "SendCompData", "CheckCacheFill">>
    /\ dirMeta' = [dirMeta EXCEPT ![A] = {}]
    /\ phase' = "ReadUniqueAActive"
    /\ lastAction' = "StartReadUniqueAWithSoleEP"
    /\ UNCHANGED <<cacheData, replDataValid, awaitingDownstreamData,
                    lastSendHadData, lastSendHadFull,
                    lastSendHadUniqueAuth, sentCompData>>

FinishReadUniqueA ==
    /\ phase = "ReadUniqueAActive"
    /\ activeKind = "Request"
    /\ actionQ = <<>>
    /\ reqDataValid[A]
    /\ activeKind' = "None"
    /\ activeAddr' = NoAddr
    /\ phase' = "Done"
    /\ lastAction' = "FinishReadUniqueA"
    /\ UNCHANGED <<cacheData, dirMeta, actionQ, reqDataValid, reqDataFull,
                    replDataValid, awaitingDownstreamData, lastSendHadData,
                    lastSendHadFull, lastSendHadUniqueAuth, sentCompData,
                    activeReadUnique, sharedGrantPublished, upgradeAuthorized>>

DoneStutter ==
    /\ phase = "Done"
    /\ UNCHANGED Vars

RunBuggySendCompData ==
    /\ BuggySoleEP
    /\ activeKind = "Request"
    /\ activeReadUnique
    /\ actionQ # <<>>
    /\ Head(actionQ) = "SendCompData"
    /\ ~reqDataValid[activeAddr]
    /\ actionQ' = Tail(actionQ)
    /\ lastAction' = "SendCompData"
    /\ lastSendHadData' = FALSE
    /\ lastSendHadFull' = reqDataFull[activeAddr]
    /\ lastSendHadUniqueAuth' = FALSE
    /\ sentCompData' = sentCompData + 1
    /\ UNCHANGED <<cacheData, dirMeta, activeKind, activeAddr, reqDataValid,
                    reqDataFull, replDataValid, awaitingDownstreamData,
                    activeReadUnique, sharedGrantPublished, upgradeAuthorized,
                    phase>>

Next ==
    \/ StartFillA
    \/ RunSendDownstreamRead
    \/ ReceiveDownstreamData
    \/ RunPublishSharedGrant
    \/ RunUpgradeBarrier
    \/ RunSendCompData
    \/ RunCheckCacheFill
    \/ FinishFillA
    \/ StartReplaceA
    \/ RunReplacementWriteback
    \/ FinishReplaceA
    \/ StartReadUniqueAWithSoleEP
    \/ RunBuggySendCompData
    \/ FinishReadUniqueA
    \/ DoneStutter

Spec == Init /\ [][Next]_Vars /\ WF_Vars(Next)

TypeOK ==
    /\ cacheData \subseteq Addresses
    /\ Cardinality(cacheData) <= CacheCapacity
    /\ dirMeta \in [Addresses -> SUBSET Agents]
    /\ activeKind \in Kinds
    /\ activeAddr \in Addresses \cup {NoAddr}
    /\ actionQ \in Seq(Actions)
    /\ reqDataValid \in [Addresses -> BOOLEAN]
    /\ reqDataFull \in [Addresses -> BOOLEAN]
    /\ replDataValid \in [Addresses -> BOOLEAN]
    /\ awaitingDownstreamData \in BOOLEAN
    /\ activeReadUnique \in BOOLEAN
    /\ sharedGrantPublished \in BOOLEAN
    /\ upgradeAuthorized \in BOOLEAN
    /\ phase \in Phases
    /\ lastAction \in LastActions
    /\ lastSendHadData \in BOOLEAN
    /\ lastSendHadFull \in BOOLEAN
    /\ lastSendHadUniqueAuth \in BOOLEAN
    /\ sentCompData \in Nat
    /\ TBECapacity = 1
    /\ (activeKind = "None" \/ TBECapacity >= 1)

SoleEPWithoutLocalDataNeedsRefill ==
    phase = "ReadUniqueAActive" /\ dirMeta[A] = {} /\ A \notin cacheData
    => BuggySoleEP \/
       (IF actionQ = <<>> THEN FALSE ELSE Head(actionQ) = "SendDownstreamRead") \/
       awaitingDownstreamData \/ reqDataValid[A]

SendCompDataHasCompleteData ==
    lastAction = "SendCompData" =>
        lastSendHadData /\ lastSendHadFull /\ lastSendHadUniqueAuth

DirectedPathCover == <> (phase = "Done" /\ sentCompData = 2 /\ reqDataValid[A])

(***************************************************************************)
(* BuggySoleEP=TRUE makes the old invalid SendCompData schedule reachable.  *)
(* The buggy cfg must fail SendCompDataHasCompleteData; fixed must pass.    *)
(***************************************************************************)

=============================================================================
