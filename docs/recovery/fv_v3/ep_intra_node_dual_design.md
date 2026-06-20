# EP intra-node dual-socket TLA+ model design

## 1. Variable list (per-socket ×2)

### 1.1 Topology constants and derived sets

Use the single-socket model as the base and add explicit socket topology:

```tla
CONSTANTS MaxDataVersion, LocalLatency, RemoteLatency

Sockets == {0, 1}
CPU == {0, 1}
CpuSocket(c) == c
LineHome \in Sockets
RemoteSock(s) == 1 - s
DataV == 0 .. MaxDataVersion
```

Keep **one cache line total** as in `ep_intra_node_single.tla`; `LineHome` selects whether the tracked line is homed on socket 0 or socket 1. This preserves the single-line state-space discipline while still exercising cross-socket routing.

### 1.2 CPU variables

Keep CPU state per CPU, not per socketed controller replica:

```tla
cpuState        \in [CPU -> {"I","SC","UC","UD","P_RS","P_RU","P_EVICT"}]
cpuData         \in [CPU -> DataV]
cpuPendingData  \in [CPU -> DataV]
cpuTargetSock   \in [CPU -> Sockets]      \* socket whose DSM is being accessed
cpuPendingKind  \in [CPU -> {"NONE","RS","RU","EVICT"}]
```

`cpuTargetSock[c] = LineHome` for any active request.

### 1.3 HN-F variables (replicated per socket)

Direct lift of the single-socket 14-state structure:

```tla
hnfState             \in [Sockets -> HnfSt]
hnfData              \in [Sockets -> DataV]
hnfCacheLine         \in [Sockets -> BOOLEAN]
hnfOwner             \in [Sockets -> (CPU \cup {EPRNF0, EPRNF1, NONE})]
hnfSharers           \in [Sockets -> SUBSET (CPU \cup {EPRNF0, EPRNF1})]

hnfTbeValid          \in [Sockets -> BOOLEAN]
hnfTbeOp             \in [Sockets -> {"NONE","RS","RU","EVICT"}]
hnfTbePhase          \in [Sockets -> {"NONE",
                                        "TBE_ALLOC","WAIT_SNF","WAIT_BACKEND",
                                        "WAIT_SNP_RS","WAIT_SNP_CU","WAIT_SNP_RU",
                                        "WAIT_COMP_UC","WAIT_COMP_ACK",
                                        "WAIT_GRANT","WAIT_WB"}]
hnfTbeRequester      \in [Sockets -> (CPU \cup {NONE})]
hnfTbeNeedData       \in [Sockets -> BOOLEAN]
hnfTbeGrantData      \in [Sockets -> DataV]
hnfPendingOwnerUpdate\in [Sockets -> BOOLEAN]
```

Design rule:

- `hnfState[LineHome]` is the authoritative home directory.
- `hnfState[RemoteSock(LineHome)]` normally stays `"I"` and acts only as a structural duplicate for symmetry/future extension.

### 1.4 EP-RNF variables (replicated per socket)

Each socket has one EP-RNF tracking whether that socket currently exports a copy of the line to the home socket:

```tla
rnfState         \in [Sockets -> {"IDLE","HAVE_SC","HAVE_UC","HAVE_UD",
                                   "PENDING_RS","PENDING_CU","PENDING_RU"}]
rnfCompUCSeen    \in [Sockets -> BOOLEAN]
rnfCompAckSent   \in [Sockets -> BOOLEAN]
rnfCallbackArmed \in [Sockets -> BOOLEAN]
```

Interpretation:

- `rnfState[s] = IDLE` if socket `s` has no exported copy of the tracked line.
- If `s # LineHome`, `rnfState[s]` mirrors the requester socket's remote presence.
- If `s = LineHome`, `rnfState[s]` should remain `IDLE` in the minimal dual-socket model.

### 1.5 EP-SNF / Backend / DRAM variables (replicated per socket)

```tla
snfState         \in [Sockets -> {"IDLE","FORWARDING"}]
backendState     \in [Sockets -> {"IDLE","WAITING_GRANT","WAITING_CLEAR"}]
backendGrantData \in [Sockets -> DataV]

dramData         \in [Sockets -> DataV]
dramWritten      \in [Sockets -> BOOLEAN]
latestGlobalWrite\in DataV
```

Only `dramData[LineHome]` is authoritative for the tracked line; the other DRAM replica remains unchanged.

### 1.6 Queue variables

Parameterize the single-socket queues by socket and add explicit inter-socket transport:

```tla
reqQ             \in [Sockets -> Seq(MsgT)]      \* requests accepted by home HN-F
snpQ             \in [Sockets -> Seq(MsgT)]      \* home HN-F -> requester EP-RNF snoops
rspQ             \in [Sockets -> Seq(MsgT)]      \* requester EP-RNF -> home HN-F completions
datQ             \in [Sockets -> Seq(MsgT)]      \* SNF grants / writebacks / local grants

interSocketQ     \in Seq([kind    : {"REMOTE_REQ","REMOTE_GRANT","REMOTE_WB","REMOTE_SNP","REMOTE_RSP"},
                           srcSock : Sockets,
                           dstSock : Sockets,
                           cpu     : CPU,
                           op      : {"NONE","RS","RU","EVICT","COMP_UC","COMP_ACK"},
                           data    : DataV,
                           lat     : 0 .. RemoteLatency])
```

Recommended modeling rule:

- local home requests enter `reqQ[LineHome]` directly;
- remote home requests are first enqueued in `interSocketQ` and only become visible to `reqQ[LineHome]` when latency expires.

---

## 2. Action list (local + cross-socket)

### 2.1 CPU issue actions

Reuse the single-socket CPU actions, split by local vs remote home:

```tla
CpuLoadLocal(cpu)
CpuStoreLocal(cpu, data)
CpuEvictLocal(cpu)

CpuLoadRemote(cpu)
CpuStoreRemote(cpu, data)
CpuEvictRemote(cpu)
```

Rules:

- `CpuSocket(cpu) = LineHome` enables the `Local` actions.
- `CpuSocket(cpu) # LineHome` enables the `Remote` actions.
- Local actions append to `reqQ[LineHome]`.
- Remote actions append `REMOTE_REQ` messages to `interSocketQ` with `lat = RemoteLatency`.

### 2.2 Cross-socket transport actions

Add a dedicated transport layer:

```tla
InterSocketTick
InterSocketDeliverReq
InterSocketDeliverGrant
InterSocketDeliverRsp
InterSocketDeliverWb
```

Required semantics:

1. `InterSocketTick` decrements `lat` for every in-flight remote message with `lat > 0`.
2. Delivery actions are enabled only when the selected message has `lat = 0`.
3. Delivery inserts the message into the destination socket's local queue (`reqQ`, `datQ`, `snpQ`, or `rspQ`) and removes it from `interSocketQ`.

### 2.3 Home HN-F actions

Lift the single-socket operators into socket-indexed form:

```tla
HnfAcceptReq(s)
HnfDropStaleReq(s)
HnfHitServeLocal(s)
HnfMissToSnf(s)
SnfForward(s)
BackendGrant(s)
HnfInstallGrantLocal(s)
HnfSnoopOwnerRU(s)
HnfSnoopRnfCleanUnique(s, reqSock)
HnfInvalidateCpuSharers(s)
HnfRecvCompUC(s, reqSock)
HnfRecvCompAck(s, reqSock)
HnfWritebackToDram(s)
DramAcceptWriteback(s)
HnfFinishWriteback(s)
HnfGrantAfterSnoopLocal(s)
BackendSendClear(s)
BackendRecvClearAck(s)
```

For the minimal dual-socket model, only `s = LineHome` should be enabled for coherence-changing actions.

### 2.4 Remote-home specific HN-F actions

Split the single-socket grant path into local-return and remote-return subcases:

```tla
HnfHitServeRemote(home)
HnfInstallGrantRemote(home)
HnfGrantAfterSnoopRemote(home)
```

These actions:

- keep home HN-F state transitions identical to the single-socket case;
- place `REMOTE_GRANT` on `interSocketQ` instead of updating the requester CPU directly;
- register the requester socket's EP-RNF token (`EPRNF0` or `EPRNF1`) in `hnfSharers[home]` / `hnfOwner[home]`.

### 2.5 Requester-side EP-RNF actions

Requester socket EP-RNF replaces the single external participant:

```tla
EpRnfInstallRemoteShared(reqSock)
EpRnfInstallRemoteUnique(reqSock)
EpRnfStartCleanUnique(reqSock)
EpRnfStartReadShared(reqSock)
EpRnfStartReadUnique(reqSock)
EpRnfSendCompAck(reqSock)
EpRnfCallback(reqSock)
```

Use the same barrier discipline as the single-socket model:

- `CompAck` only after `Comp_UC`;
- callback only after both `Comp_UC` and `CompAck`;
- callback performs the final requester-socket state transition.

### 2.6 Requester CPU completion actions

Add explicit CPU completion after remote grant delivery:

```tla
CpuAcceptRemoteGrant(cpu)
CpuCommitRemoteStoreHit(cpu, data)
CpuCompleteRemoteEvict(cpu)
```

This keeps CPU state changes on the requester socket, not inside the remote home HN-F action.

### 2.7 `Next` structure

Recommended shape:

```tla
Next ==
    \/ \E cpu \in CPU : CpuLoadLocal(cpu) \/ CpuLoadRemote(cpu)
    \/ \E cpu \in CPU, d \in DataV : CpuStoreLocal(cpu, d) \/ CpuStoreRemote(cpu, d)
    \/ \E cpu \in CPU : CpuEvictLocal(cpu) \/ CpuEvictRemote(cpu)
    \/ InterSocketTick
    \/ InterSocketDeliverReq
    \/ InterSocketDeliverGrant
    \/ InterSocketDeliverRsp
    \/ InterSocketDeliverWb
    \/ \E s \in Sockets : SocketActions(s)
```

where `SocketActions(s)` is the disjunction of all per-socket HN-F / SNF / backend / DRAM / EP-RNF actions.

---

## 3. Invariant specifications

### 3.1 Reuse all single-socket invariants, socket-indexed

Lift the existing invariants from `ep_intra_node_single.tla` to quantify over `s \in Sockets`:

```tla
TypeOK
PerSocketHnfDirectoryConsistent
PerSocketCallbackOrdering
PerSocketWritebackPersistence
PerSocketTbeValidGuard
PerSocketOwnerUpdateBlocksUnique
NoTwoDirtyUniques
```

Recommended forms:

```tla
PerSocketHnfDirectoryConsistent ==
    \A s \in Sockets :
        /\ (hnfState[s] = "I")  => (hnfSharers[s] = {} /\ hnfOwner[s] = NONE /\ ~hnfCacheLine[s])
        /\ (hnfState[s] = "SC") => (hnfOwner[s] = NONE /\ hnfSharers[s] # {})
        /\ (hnfState[s] \in {"UC","UD"}) => hnfSharers[s] = {hnfOwner[s]}

PerSocketCallbackOrdering ==
    \A s \in Sockets :
        /\ rnfCompAckSent[s] => rnfCompUCSeen[s]
        /\ (rnfState[s] \in {"IDLE","HAVE_SC","HAVE_UC","HAVE_UD"}) => ~rnfCallbackArmed[s]
```

### 3.2 Local correctness invariants

```tla
DataIntegrity ==
    \A c \in CPU :
        (cpuState[c] \in {"UC","UD"}) => cpuData[c] = latestGlobalWrite

NoTwoDirtyUniques ==
    Cardinality({c \in CPU : cpuState[c] = "UD"}) <= 1
```

### 3.3 New dual-socket invariants

#### SocketIsolation

The non-home socket must not mutate the tracked line as if it were home:

```tla
SocketIsolation ==
    \A s \in Sockets :
        s # LineHome =>
            /\ hnfState[s] = "I"
            /\ hnfSharers[s] = {}
            /\ hnfOwner[s] = NONE
            /\ ~hnfTbeValid[s]
```

#### CrossSocketDataIntegrity

If the requester socket holds the remote line, the home directory must agree:

```tla
CrossSocketDataIntegrity ==
    \A c \in CPU :
        CpuSocket(c) # LineHome /\ cpuState[c] \in {"SC","UC","UD"} =>
            /\ IF cpuState[c] = "SC"
               THEN EPRNF(CpuSocket(c)) \in hnfSharers[LineHome]
               ELSE hnfOwner[LineHome] = EPRNF(CpuSocket(c))
            /\ cpuData[c] = hnfData[LineHome]
```

#### RemoteRequestsReachHome

```tla
RemoteRequestsReachHome ==
    \A i \in 1..Len(interSocketQ) :
        interSocketQ[i].kind = "REMOTE_REQ" => interSocketQ[i].dstSock = LineHome
```

#### NoDirectHnfBypass

```tla
NoDirectHnfBypass ==
    \A i \in 1..Len(interSocketQ) :
        interSocketQ[i].kind \notin {"DIRECT_HNF_SNP", "DIRECT_HNF_GRANT"}
```

#### RemoteCallbackOrdering

```tla
RemoteCallbackOrdering ==
    \A s \in Sockets :
        s # LineHome =>
            /\ rnfCompAckSent[s] => rnfCompUCSeen[s]
            /\ rnfCallbackArmed[s] => rnfState[s] \in {"PENDING_RS","PENDING_CU","PENDING_RU"}
```

### 3.4 Historical-hazard invariants to preserve

Carry the same protections into the dual model:

```tla
NoResponseWithoutHomeTbe ==
    \A s \in Sockets :
        (\E i \in 1..Len(datQ[s]) : datQ[s][i].kind = "SNF_GRANT") => hnfTbeValid[s]

PendingOwnerUpdateBlocksUnique ==
    \A s \in Sockets :
        hnfPendingOwnerUpdate[s] =>
            \A c \in CPU : ~(CpuSocket(c) # s /\ cpuState[c] = "P_RU")

NoLeakedGrant ==
    \A s \in Sockets :
        (backendState[s] /= "WAITING_CLEAR") \/ (backendGrantData[s] > 0)
```

---

## 4. Migration plan from `ep_intra_node_single.tla`

### 4.1 Duplicate directly

Convert these scalar single-socket variables into `[Sockets -> ...]` functions with no semantic change:

- `hnfState`
- `hnfData`
- `hnfCacheLine`
- `hnfOwner`
- `hnfSharers`
- `hnfTbeValid`
- `hnfTbeOp`
- `hnfTbePhase`
- `hnfTbeRequester`
- `hnfTbeNeedData`
- `hnfTbeGrantData`
- `hnfPendingOwnerUpdate`
- `rnfState`
- `rnfCompUCSeen`
- `rnfCompAckSent`
- `rnfCallbackArmed`
- `snfState`
- `backendState`
- `backendGrantData`
- `dramData`
- `dramWritten`
- `reqQ`, `snpQ`, `rspQ`, `datQ`

### 4.2 Keep CPU variables global, add routing metadata

Retain:

- `cpuState`
- `cpuData`
- `cpuPendingData`
- `latestGlobalWrite`

Add:

- `cpuTargetSock`
- `cpuPendingKind`
- `interSocketQ`

### 4.3 Parameterize existing actions

The following operators from the current single-socket file should become `(..., s)` or `(s)` forms with their bodies mechanically indexed by `s`:

- `HnfAcceptReq`
- `HnfDropStaleReq`
- `HnfMissToSnf`
- `SnfForward`
- `BackendGrant`
- `HnfSnoopOwnerRU`
- `HnfSnoopRnfCleanUnique`
- `HnfInvalidateCpuSharers`
- `EpRnfStartCleanUnique`
- `HnfRecvCompUC`
- `EpRnfSendCompAck`
- `HnfRecvCompAck`
- `EpRnfCallback`
- `HnfWritebackToDram`
- `DramAcceptWriteback`
- `HnfFinishWriteback`
- `BackendSendClear`
- `BackendRecvClearAck`

### 4.4 Split actions that directly update CPU state

These single-socket actions currently complete the CPU side inline and must be split into local-return and remote-return forms:

- `HnfHitServe`
- `HnfInstallGrant`
- `HnfGrantAfterSnoop`

Migration rule:

1. **Local requester**: keep current direct CPU update semantics.
2. **Remote requester**: home HN-F updates only home directory state and emits `REMOTE_GRANT` to `interSocketQ`.
3. Requester socket performs the CPU state/data update in `CpuAcceptRemoteGrant`.

### 4.5 Replace single sentinel `EPRNF`

Single-socket uses one sentinel `EPRNF = NumCPUs`.

Dual-socket should use socket-qualified sentinels:

```tla
EPRNF0, EPRNF1
EPRNF(s) == IF s = 0 THEN EPRNF0 ELSE EPRNF1
```

Only `EPRNF(RemoteSock(LineHome))` may appear in `hnfSharers[LineHome]` / `hnfOwner[LineHome]`.

### 4.6 `Init` rules

Initialize all per-socket state to idle values. Then enforce:

```tla
hnfState[RemoteSock(LineHome)] = "I"
rnfState[LineHome] = "IDLE"
dramData[LineHome] = 0
dramData[RemoteSock(LineHome)] = 0
```

### 4.7 TLC rollout plan

1. Start with `RemoteLatency = 1`, `LocalLatency = 0`, `MaxDataVersion = 1`.
2. Run safety only.
3. Add `RemoteLatency = 2` after invariants stabilize.
4. Add weak fairness only for:
   - `InterSocketTick`
   - `BackendGrant(s)`
   - `EpRnfSendCompAck(s)`
   - `BackendRecvClearAck(s)`

For the full intended model size, expect roughly **110M-150M states** once both local and remote paths plus per-socket duplication are enabled.

---

## 5. Reference-based implementation notes from `ep_intra_node_single.tla`

Review of the current single-socket file via `grep -n` and `sed -n` shows the exact reuse points:

- constants / sets: lines `17-39`
- variables / `vars`: lines `45-56`
- `Init`: lines `69-92`
- CPU actions: lines `112-169`
- HN-F request/grant path: lines `171-333`
- EP-RNF completion path: lines `436-539`
- writeback / clear handshake: lines `545-655`
- `Next`: lines `673-698`
- invariants: lines `706-757`

Implementation guidance:

1. First clone the single-socket file into `ep_intra_node_dual.tla`.
2. Replace every scalar controller variable with a socket-indexed function.
3. Introduce `LineHome` and guard all home-authoritative actions with `s = LineHome`.
4. Add `interSocketQ` and the delivery/tick actions before changing coherence logic.
5. Only after transport is stable, split `HnfHitServe`, `HnfInstallGrant`, and `HnfGrantAfterSnoop` into local/remote variants.
6. Re-check invariants after each split; most bugs will appear in the remote grant-return path or in incorrect `EPRNF(s)` registration.

This migration order minimizes diff size while preserving the single-socket proof structure.
