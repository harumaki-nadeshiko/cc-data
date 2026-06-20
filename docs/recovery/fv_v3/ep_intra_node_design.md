# EP intra-node TLA+ replacement design

## 1. Scope

Replace `docs/recovery/fv_v3/ep_intra_node.tla` with two new models:

1. `ep_intra_node_single.tla` — complete single-socket intra-node model.
2. `ep_intra_node_dual.tla` — dual-socket extension with explicit NUMA routing and cross-socket latency.

This design follows the style of the current `ep_intra_node.tla` and `ubcc_protocol.tla`:

- explicit enum state sets;
- bounded message channels as sequences of records;
- one-line abstraction for exhaustive TLC runs;
- `Init / Next / Spec / TypeOK / safety invariants / liveness checks` layout;
- no hidden controller-local magic transitions.

It also incorporates the protocol constraints from `scheme_v4.md`, `ep-rnf-sharer-registration-plan.md`, and `decisions.md`:

- HN-F is the only snoop authority;
- EP-RNF is modeled as an external-directory participant, not as a direct snooper;
- no bypass around HN-F;
- strict per-line single-flight TBE behavior;
- DCT/Fwd is forced off when EP-RNF is the only sharer;
- `CleanUnique -> Comp_UC -> CompAck -> callback` ordering is explicit.

---

## 2. Common modeling conventions

### 2.1 Abstraction level

- Model **one cache line** by default (`LineAddr = 0`).
- Model data as a **monotonic abstract version** (`DataVersion`).
- Model a bounded set of CPUs.
- Model only the intra-node path; UBCC remains abstracted behind `EPBackend` grant/recall/invalidate responses.

### 2.2 Shared type sets

Recommended common definitions:

```tla
CpuStates == {"I", "SC", "UC", "UD", "P_RS", "P_RU", "P_EVICT"}
HnfStable == {"I", "SC", "UC", "UD"}
HnfTransient == {
    "TBE_ALLOC",
    "WAIT_SNF_REQ",
    "WAIT_BACKEND_GRANT",
    "WAIT_RNF_SNP_RS",
    "WAIT_RNF_SNP_CU",
    "WAIT_RNF_SNP_RU",
    "WAIT_RNF_COMP_UC",
    "WAIT_RNF_COMP_ACK",
    "WAIT_CPU_GRANT",
    "WAIT_WB"
}
RnfStates == {
    "IDLE", "HAVE_SC", "HAVE_UC", "HAVE_UD",
    "PENDING_RS", "PENDING_CU", "PENDING_RU"
}
SnfStates == {"IDLE", "FORWARDING"}
BackendStates == {"IDLE", "WAITING_GRANT", "WAITING_CLEAR_ACK"}
ReqKinds == {"RS", "RU", "WNS", "EVICT", "CU"}
SnpKinds == {"SNP_RS", "SNP_CU", "SNP_RU", "SNP_CINV"}
RspKinds == {"COMP_UC", "COMP_ACK", "CPU_GRANT", "RETRY"}
DatKinds == {"SNF_GRANT", "WB", "DRAM_DATA"}
```

### 2.3 Message-channel style

Keep the current style of explicit channels, but expand them:

```tla
cpuReqQ, cpuRspQ,
hnfToSnfQ, snfToBackendQ, backendToSnfQ,
hnfToRnfSnpQ, rnfToHnfRspQ,
hnfDatQ, dramReqQ, dramRspQ,
interSocketQ    \* dual-socket only
```

Each message is a record with at least:

```tla
[src |-> ..., dst |-> ..., kind |-> ..., addr |-> LineAddr,
 data |-> version, reqId |-> Nat, op |-> ..., lat |-> Nat]
```

### 2.4 Single-flight discipline

The model must use one explicit HN-F TBE record:

```tla
hnfTbe == [
    valid      : BOOLEAN,
    op         : {"NONE", "RS", "RU", "WNS", "CU", "RECALL_RS", "RECALL_RU"},
    phase      : HnfTransient \cup {"NONE"},
    requester  : Cpu \cup {"EPRNF", "NONE"},
    needData   : BOOLEAN,
    needCompUC : BOOLEAN,
    needCompAck: BOOLEAN,
    ownerSnap  : Cpu \cup {"EPRNF", "NONE"},
    sharerSnap : SUBSET (Cpu \cup {"EPRNF"}),
    grantData  : DataVersion
]
```

All asynchronous arrivals must be guarded by `hnfTbe.valid` and matching `hnfTbe.phase`. This is the main protection against the historical same-tick TBE race.

---

## 3. Single-socket model: `ep_intra_node_single.tla`

## 3.1 Constants and variables

Recommended constants:

```tla
CONSTANTS NumCPUs, MaxDataVersion, MaxLatency
```

Derived sets:

```tla
CPU == 0 .. (NumCPUs - 1)
LineAddr == 0
DataVersion == 0 .. MaxDataVersion
Sharer == CPU \cup {"EPRNF"}
```

Recommended variables:

```tla
cpuState,       \* [CPU -> CpuStates]
cpuData,        \* [CPU -> DataVersion]
cpuPendingOp,   \* [CPU -> {"NONE","LOAD","STORE","EVICT"}]
cpuPendingData, \* [CPU -> DataVersion]

hnfState,       \* HnfStable
hnfData,        \* DataVersion
hnfTag,         \* BOOLEAN (line present)
hnfOwner,       \* CPU \cup {"EPRNF","NONE"}
hnfSharers,     \* SUBSET Sharer
hnfTbe,
hnfPendingOwnerUpdate,

rnfState,
rnfPendingOp,
rnfCompUCSeen,
rnfCompAckSent,
rnfCallbackArmed,
rnfQueuedSnp,

snfState,
snfPendingReq,

backendState,
backendPendingReq,
backendGrant,
backendGrantData,
backendClearPending,

dramData,
dramWritten,
latestGlobalWrite,

cpuReqQ, cpuRspQ,
hnfToSnfQ, snfToBackendQ, backendToSnfQ,
hnfToRnfSnpQ, rnfToHnfRspQ,
dramReqQ, dramRspQ
```

`latestGlobalWrite` is the reference value for `Store -> Load returns latest value` safety checking.

## 3.2 Component state enumeration

### 3.2.1 CPU-RNFs

Stable states:

- `I` — no copy.
- `SC` — clean shared copy.
- `UC` — clean unique copy.
- `UD` — dirty unique copy.

Transient states:

- `P_RS` — waiting for `ReadShared` grant.
- `P_RU` — waiting for `ReadUnique` grant.
- `P_EVICT` — eviction sent, waiting for HN-F accept / writeback consume.

### 3.2.2 HN-F

Stable states:

- `I` — line absent.
- `SC` — clean shared, `hnfOwner = NONE`, `hnfSharers /= {}`.
- `UC` — clean unique, one owner.
- `UD` — dirty unique, one owner and dirty data.

Transient/TBE phases:

- `TBE_ALLOC`
- `WAIT_SNF_REQ`
- `WAIT_BACKEND_GRANT`
- `WAIT_RNF_SNP_RS`
- `WAIT_RNF_SNP_CU`
- `WAIT_RNF_SNP_RU`
- `WAIT_RNF_COMP_UC`
- `WAIT_RNF_COMP_ACK`
- `WAIT_CPU_GRANT`
- `WAIT_WB`

### 3.2.3 EP-RNF

Stable states:

- `IDLE`
- `HAVE_SC`
- `HAVE_UC`
- `HAVE_UD`

Transient states:

- `PENDING_RS`
- `PENDING_CU`
- `PENDING_RU`

### 3.2.4 EP-SNF

Stable states:

- `IDLE`

Transient states:

- `FORWARDING`

### 3.2.5 EPBackend

Stable states:

- `IDLE`

Transient states:

- `WAITING_GRANT`
- `WAITING_CLEAR_ACK`

### 3.2.6 Home DRAM

- no protocol-control state;
- `dramData` stores the clean persisted version;
- `dramWritten` is a Boolean marker that the latest dirty value has been written back.

---

## 3.3 State responsibilities

### CPU-RNF

- initiates `Load`, `Store`, `Evict`;
- holds only per-CPU cache state and data version;
- never snoops directly;
- only talks to HN-F.

### HN-F

- owns the node-local coherence point;
- arbitrates hits vs misses;
- sends all snoops;
- enforces one in-flight TBE per line;
- performs owner/sharer bookkeeping including `EPRNF` registration.

### EP-RNF

- acts as a sharer/owner placeholder for remote copies;
- receives snoops from HN-F;
- issues `ReadShared`, `ReadUnique`, `CleanUnique` semantics back into HN-F;
- only fires callback after `Comp_UC` and `CompAck` closure.

### EP-SNF

- forwards HN-F misses to backend;
- returns abstracted data grants back to HN-F.

### EPBackend

- abstracts UBCC-facing handshake;
- converts forwarded misses to abstract grant arrivals;
- optionally models `Clear`/ack retirement to cover grant-leak hazards.

### Home DRAM

- receives writeback data from HN-F or EP-SNF-side fill completion;
- is the persistence reference for clean data invariants.

---

## 3.4 Action list with pre/post conditions

The final single-socket module should contain the following actions.

| Action | Preconditions | Postconditions |
|---|---|---|
| `CpuLoad(cpu)` | `cpuState[cpu] = "I" /\ cpuPendingOp[cpu] = "NONE"` | `cpuState' = P_RS`; enqueue `RS` request to HN-F |
| `CpuLoadHit(cpu)` | `cpuState[cpu] \in {"SC","UC","UD"}` | no coherence state change; assert returned data equals latest visible value |
| `CpuStore(cpu, data)` | `cpuState[cpu] \in {"I","SC"}` | `cpuPendingOp' = "STORE"`; state becomes `P_RU`; enqueue `RU` |
| `CpuStoreHit(cpu, data)` | `cpuState[cpu] \in {"UC","UD"}` | `cpuState' = "UD"`; `cpuData' = data`; `latestGlobalWrite' = data` |
| `CpuEvict(cpu)` | `cpuState[cpu] \in {"SC","UC","UD"}` | `cpuState' = "P_EVICT"`; enqueue evict/writeback semantic |
| `HnfAcceptCpuReq(cpu)` | request at head of `cpuReqQ`; `~hnfTbe.valid` | allocate `hnfTbe`; choose hit/miss branch |
| `HnfHitShared(cpu)` | `hnfState = "SC"` and compatible read | add CPU to sharers; send CPU grant |
| `HnfHitUniqueOwner(cpu)` | `hnfState \in {"UC","UD"}` and same owner rerequest | send direct grant; preserve ownership |
| `HnfUpgradeNeedSnoop(cpu)` | `hnfState = "SC"` and store/unique needed | set `hnfTbe.phase = WAIT_RNF_SNP_CU` or `WAIT_RNF_SNP_RU`; enqueue snoop to EP-RNF if registered |
| `HnfMissToEpSnf(cpu)` | miss path chosen | `hnfTbe.phase' = WAIT_SNF_REQ`; enqueue miss to EP-SNF |
| `EpSnfForward` | `snfState = IDLE` and pending HN-F miss | `snfState' = FORWARDING`; enqueue backend request |
| `EpBackendAcceptReq` | backend idle and request pending | `backendState' = WAITING_GRANT`; latch request |
| `EpBackendGrantArrives` | `backendState = WAITING_GRANT` | set `backendGrant = TRUE`; `backendGrantData` valid |
| `EpSnfReturnGrant` | backend grant ready | deliver data to HN-F; `snfState' = IDLE` |
| `HnfInstallSnfGrant(cpu)` | `hnfTbe.phase = WAIT_BACKEND_GRANT` or equivalent fill phase | install `hnfState/hnfData/hnfSharers/hnfOwner`; optionally register `EPRNF`; enqueue CPU grant |
| `HnfSendSnpRsToRnf` | request needs remote shared recall | enqueue `SNP_RS`; move TBE to `WAIT_RNF_COMP_ACK` after `Comp_UC` |
| `HnfSendSnpCuToRnf` | request is `CleanUnique` invalidate-only | enqueue `SNP_CU`; wait for `Comp_UC` then `CompAck` |
| `HnfSendSnpRuToRnf` | request needs unique/recall | enqueue `SNP_RU`; wait for `Comp_UC` then `CompAck` |
| `EpRnfStartReadShared` | head snoop is `SNP_RS`; `rnfState` stable | `rnfState' = PENDING_RS`; arm callback |
| `EpRnfStartCleanUnique` | head snoop is `SNP_CU`; `rnfState = HAVE_SC` | `rnfState' = PENDING_CU`; arm callback |
| `EpRnfStartReadUnique` | head snoop is `SNP_RU`; `rnfState \in {IDLE,HAVE_UC,HAVE_UD}` | `rnfState' = PENDING_RU`; arm callback |
| `EpRnfRecvCompUC` | pending RNF op active | `rnfCompUCSeen' = TRUE`; enqueue response token to HN-F |
| `EpRnfSendCompAck` | `rnfCompUCSeen /\ ~rnfCompAckSent` | `rnfCompAckSent' = TRUE`; send `COMP_ACK` |
| `EpRnfCallback` | `rnfCompUCSeen /\ rnfCompAckSent /\ rnfCallbackArmed` | transition pending state to `HAVE_SC/HAVE_UC/HAVE_UD` or `IDLE`; clear completion flags |
| `HnfAcceptRnfCompUC` | `hnfTbe.phase \in {WAIT_RNF_COMP_UC, WAIT_RNF_COMP_ACK}` | advance to `WAIT_RNF_COMP_ACK` |
| `HnfAcceptRnfCompAck` | `hnfTbe.phase = WAIT_RNF_COMP_ACK` | finalize snoop side effects; complete CPU grant or writeback |
| `HnfWritebackToDram` | line dirty and eviction/clean-share requires persist | enqueue DRAM write; `hnfTbe.phase' = WAIT_WB` |
| `DramAcceptWriteback` | DRAM request present | `dramData' = writeback.data`; `dramWritten' = TRUE` |
| `HnfFinishWriteback` | `hnfTbe.phase = WAIT_WB` and DRAM ack/data visible | clean or invalidate line; clear TBE |
| `BackendSendClear` | backend grant consumed | `backendState' = WAITING_CLEAR_ACK`; mark retirement pending |
| `BackendRecvClearAck` | `backendState = WAITING_CLEAR_ACK` | clear outstanding grant token; `backendState' = IDLE` |

The implementation should keep `UNCHANGED` blocks explicit, mirroring the current style.

---

## 3.5 Required control rules in the model

### 3.5.1 HN-F directory rules

- `hnfState = "I"  => hnfSharers = {} /\ hnfOwner = "NONE"`
- `hnfState = "SC" => hnfOwner = "NONE" /\ hnfSharers /= {}`
- `hnfState \in {"UC","UD"} => hnfSharers = {hnfOwner}`
- `"EPRNF" \in hnfSharers` is allowed only when remote metadata exists.

### 3.5.2 EP-RNF registration

After a remote shared fill with `shared_hint = TRUE`, HN-F must add `"EPRNF"` to `hnfSharers` without giving it data ownership.

### 3.5.3 DCT fallback

If `hnfSharers = {"EPRNF"}`, then every would-be forward/DCT path must be modeled as non-DCT:

```tla
(hnfSharers = {"EPRNF"}) => ~useDCT
```

Equivalent operationally: HN-F sends a snoop to EP-RNF and expects a response back to HN-F, never direct-forwarded to a CPU.

### 3.5.4 Callback barrier

For all EP-RNF operations:

```tla
rnfCallbackArmed => rnfState \in {"PENDING_RS", "PENDING_CU", "PENDING_RU"}
rnfCompAckSent => rnfCompUCSeen
EpRnfCallback enabled => rnfCompUCSeen /\ rnfCompAckSent
```

### 3.5.5 `pendingOwnerUpdate`

The single-socket model should include:

```tla
hnfPendingOwnerUpdate \in BOOLEAN
```

Set it when a `CleanUnique`-driven remote owner update begins; clear it only after the abstract backend acknowledgement arrives. During this window, new unique/upgrade actions for the same line must be blocked.

---

## 3.6 Safety invariants

Minimum required invariants:

### 3.6.1 Type and structural

```tla
TypeOK
CpuStateLegal
HnfStateLegal
RnfStateLegal
QueueTypeOK
```

### 3.6.2 Coherence

```tla
NoTwoDirtyUniques ==
    Cardinality({c \in CPU : cpuState[c] = "UD"}) <= 1

UniqueOwnerMatchesHnf ==
    (hnfState \in {"UC", "UD"}) =>
        /\ hnfOwner \in Sharer
        /\ hnfSharers = {hnfOwner}
```

### 3.6.3 Data integrity

```tla
LatestStoreVisible ==
    \A c \in CPU :
        cpuState[c] \in {"UC", "UD"} => cpuData[c] = latestGlobalWrite
```

For shared copies, require equality with `hnfData` / `dramData` whenever the line is clean.

### 3.6.4 Callback ordering

```tla
CallbackOrdering ==
    /\ rnfCompAckSent => rnfCompUCSeen
    /\ rnfState \in {"HAVE_SC", "HAVE_UC", "HAVE_UD", "IDLE"}
         => ~rnfCallbackArmed
```

### 3.6.5 Writeback persistence

```tla
WritebackPersistence ==
    dramWritten => dramData = latestGlobalWrite
```

### 3.6.6 TBE race guard

```tla
NoResponseWithoutTbe ==
    \A i \in 1..Len(backendToSnfQ) : hnfTbe.valid
```

Better form: every fill/snoop response action has precondition `hnfTbe.valid /\ hnfTbe.phase = expectedPhase`.

### 3.6.7 Owner-update barrier

```tla
PendingOwnerUpdateBlocksUnique ==
    hnfPendingOwnerUpdate =>
      ~ (\E c \in CPU : ENABLED HnfUpgradeNeedSnoop(c))
```

### 3.6.8 Grant-handshake retirement

```tla
NoLeakedGrant ==
    backendState = "IDLE" <=> ~backendClearPending
```

With liveness:

```tla
GrantEventuallyRetires ==
    backendClearPending ~> ~backendClearPending
```

---

## 3.7 Historical hazards to model explicitly

1. **TBE race**
   - allocate TBE in one action before any grant can arrive;
   - no same-step grant acceptance unless `hnfTbe.valid = TRUE`.

2. **`pendingOwnerUpdate` lifecycle**
   - set on `CleanUnique` callback start;
   - clear only on backend ack;
   - unique/upgrade blocked while set.

3. **GRANT_HANDSHAKE leak**
   - backend must model `grant -> clear -> clearAck` retirement;
   - add liveness or bounded watchdog counter.

4. **DCT fallback correctness**
   - when EP-RNF is sole sharer, `useDCT = FALSE` by guard;
   - non-DCT path must still terminate through HN-F response.

---

## 3.8 Suggested module skeleton and line budget

Recommended file structure for `ep_intra_node_single.tla`:

| Section | Approx. lines |
|---|---:|
| Header / comments / extends | 20 |
| Constants / sets / records | 60 |
| Variables / `vars` tuple | 40 |
| `Init` / helpers | 70 |
| CPU actions | 45 |
| HN-F actions | 110 |
| EP-RNF actions | 70 |
| EP-SNF + backend + DRAM actions | 50 |
| `Next` / `Spec` / fairness | 15 |
| Invariants / theorems | 35 |
| **Total** | **~515 raw / ~400 logical** |

The current `ep_intra_node.tla` can be retired once this replacement exists.

---

## 4. Dual-socket model: `ep_intra_node_dual.tla`

## 4.1 Modeling goal

Extend the single-socket design to `Socket = {0,1}` with:

- per-socket HN-F / EP-RNF / EP-SNF / backend / DRAM;
- per-socket CPU clusters;
- explicit local-vs-remote routing;
- bounded inter-socket latency;
- no direct HN-F-to-HN-F coherence bypass.

## 4.2 Additional constants and mappings

```tla
CONSTANTS Sockets, CpuHomeSocket, LocalLatency, RemoteLatency
ASSUME Sockets = {0, 1}
```

Recommended derived sets:

```tla
CPUOnSocket(s) == {c \in CPU : CpuHomeSocket[c] = s}
HomeSocket(addr) == addr % 2   \* bounded NUMA home function
```

## 4.3 State factorization

All single-socket controller variables become socket-indexed functions:

```tla
cpuState, cpuData, cpuPendingOp,
hnfState, hnfData, hnfOwner, hnfSharers, hnfTbe,
rnfState, rnfPendingOp, rnfCompUCSeen, rnfCompAckSent,
snfState, backendState,
dramData, dramWritten
```

Where appropriate:

```tla
hnfState      \in [Sockets -> HnfStable]
hnfTbe        \in [Sockets -> HnfTbeType]
rnfState      \in [Sockets -> RnfStates]
dramData      \in [Sockets -> DataVersion]
```

## 4.4 Additional message channels

Dual-socket needs one explicit inter-socket transport queue:

```tla
interSocketQ
```

Message record:

```tla
[srcSock |-> s0, dstSock |-> s1, kind |-> ..., addr |-> a,
 data |-> v, reqId |-> id, lat |-> 0..RemoteLatency]
```

The model should use a countdown action:

```tla
InterSocketTick ==
    \* decrement lat until delivery becomes enabled
```

## 4.5 Dual-socket action groups

### Local CPU side

- `CpuIssueLocalLoad(cpu)`
- `CpuIssueLocalStore(cpu)`
- `CpuIssueRemoteLoad(cpu)`
- `CpuIssueRemoteStore(cpu)`

### Routing

- `HnfRouteLocalMiss(sock)` — local home -> local EP-SNF/DRAM path.
- `HnfRouteRemoteMiss(sock)` — remote home -> backend/inter-socket path.
- `BackendSendRemoteReq(srcSock, dstSock)`
- `InterSocketAdvance(msg)`
- `BackendRecvRemoteGrant(dstSock)`

### Remote owner interactions

- `RemoteHnfSendSnoopToRemoteRnf(homeSock)`
- `RemoteRnfRecallReadShared(sock)`
- `RemoteRnfRecallReadUnique(sock)`
- `RemoteRnfCleanUniqueInvalidate(sock)`

### Completion

- `HomeBackendCommitGrant(sock)`
- `RequesterBackendSendClear(sock)`
- `RequesterBackendRecvClearAck(sock)`

## 4.6 Additional invariants

### Socket isolation

```tla
SocketIsolation ==
    \A s \in Sockets :
      \A t \in Sockets :
        s /= t =>
          ~(hnfOwner[s] \in CPUOnSocket(t) /\ HomeSocket(LineAddr) = s)
```

Meaning: ownership changes for a home line occur only through the modeled cross-socket protocol, never by a direct local state mutation on the wrong socket.

### No inter-socket bypass

```tla
NoDirectHnfBypass ==
    \A m \in SeqToSet(interSocketQ) : m.kind \notin {"DIRECT_HNF_SNP", "DIRECT_HNF_GRANT"}
```

### Cross-socket data preservation

```tla
CrossSocketNoDataLoss ==
    \A s \in Sockets :
    \A t \in Sockets :
      s /= t =>
        (dramWritten[s] /\ HomeSocket(LineAddr) = s) => dramData[s] = latestGlobalWrite
```

### Remote callback ordering

The single-socket callback barrier must hold independently for each socket.

### NUMA routing correctness

```tla
RemoteRequestsReachHome ==
    \A m \in SeqToSet(interSocketQ) : m.dstSock = HomeSocket(m.addr)
```

## 4.7 Dual-socket line budget

Recommended file structure for `ep_intra_node_dual.tla`:

| Section | Approx. lines |
|---|---:|
| Header / comments / shared imports | 20 |
| Constants / topology mappings | 45 |
| State definitions | 75 |
| Init / helpers | 80 |
| Local CPU/HN-F actions | 120 |
| Remote routing / inter-socket transport | 120 |
| EP-RNF / EP-SNF / backend actions | 110 |
| DRAM / completion / clear-ack | 40 |
| `Next` / fairness | 20 |
| Invariants / liveness | 45 |
| **Total** | **~675 raw / ~600 logical** |

If reuse is preferred, shared helper definitions may be factored into a tiny utility module, but copying is acceptable if it keeps TLC-friendly readability.

---

## 5. Recommended implementation shape

## 5.1 `ep_intra_node_single.tla`

Use this section order:

1. module header comment;
2. `EXTENDS Naturals, FiniteSets, Sequences, TLC`;
3. constants and assumptions;
4. type sets and record constructors;
5. variables;
6. `Init`;
7. helper operators (`TailSeq`, queue constructors, `CpuCanLoad`, `HnfCanGrant`, etc.);
8. CPU actions;
9. HN-F actions;
10. EP-RNF actions;
11. EP-SNF / backend / DRAM actions;
12. `Next`;
13. `Spec` + fairness;
14. invariants and theorems.

## 5.2 `ep_intra_node_dual.tla`

Same order, but all controller state is socket-indexed and all remote routing actions are grouped after local HN-F actions.

---

## 6. TLC strategy

## 6.1 Single-socket exhaustive profile

Start with:

- `NumCPUs = 2`
- `MaxDataVersion = 2`
- one line only
- bounded queue lengths implicitly kept small by single-flight guards

Check:

- `TypeOK`
- `NoTwoDirtyUniques`
- `UniqueOwnerMatchesHnf`
- `LatestStoreVisible`
- `CallbackOrdering`
- `WritebackPersistence`
- `PendingOwnerUpdateBlocksUnique`

Expected state count: **~2e5 to 1e6** depending on how many queue heads are made explicit.

## 6.2 Single-socket stress profile

- `NumCPUs = 3`
- `MaxDataVersion = 2`
- fairness enabled for callback and clear-ack actions

Expected state count: **~3e6 to 1.5e7**.

## 6.3 Dual-socket exhaustive profile

Start with:

- `Sockets = {0,1}`
- `1 CPU per socket` or `2 total CPUs`
- `RemoteLatency = 1`
- `MaxDataVersion = 2`

Check all single-socket invariants per socket plus:

- `SocketIsolation`
- `NoDirectHnfBypass`
- `CrossSocketNoDataLoss`
- `RemoteRequestsReachHome`

Expected state count: **~1e6 to 8e6**.

## 6.4 Dual-socket stress profile

- `2 CPUs per socket`
- `RemoteLatency = 2`
- optional `LocalLatency = 0`

Expected state count: **~1e7 to 5e7**; use targeted configs and separate liveness runs.

## 6.5 Fairness recommendations

Use weak fairness only on actions that represent guaranteed eventual service:

- `EpBackendGrantArrives`
- `EpRnfRecvCompUC`
- `EpRnfSendCompAck`
- `BackendRecvClearAck`
- `InterSocketAdvance` (dual)

Avoid blanket fairness over all actions; it will distort performance and enlarge liveness cost.

---

## 7. Deliverables

Recommended replacement set:

1. `docs/recovery/fv_v3/ep_intra_node_single.tla`
2. `docs/recovery/fv_v3/ep_intra_node_single.cfg`
3. `docs/recovery/fv_v3/ep_intra_node_dual.tla`
4. `docs/recovery/fv_v3/ep_intra_node_dual.cfg`
5. keep this design note as the implementation contract.

## 8. Bottom line

The replacement model must stop being a minimal `HN-F <-> EP-RNF` sketch and instead model the full intra-node chain:

`CPU-RNF -> HN-F -> EP-SNF -> EPBackend -> (abstract UBCC grant)`

plus the reverse snoop/recall path:

`HN-F -> EP-RNF -> Comp_UC -> CompAck -> callback`

with explicit data persistence through DRAM and explicit dual-socket transport in the extended model.
