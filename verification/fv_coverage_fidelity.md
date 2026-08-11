# Formal Verification — Coverage, Scope & Fidelity

> Produced: 2026-07-08 | Current-implementation sync: 2026-08-03
> Companion to `CONSOLIDATED_REPORT.md`
> Purpose: answer three review questions that raw state/depth counts do NOT:
>   (A1) Is coverage quantified?  (A2) What scope is covered vs not?
>   (A3) How do we argue the model corresponds to the code?
>
> Headline: state counts and search depth measure *exploration size*, not
> *coverage*. The metrics below are the actual coverage evidence.

---

## A1. Action Coverage (TLC `-coverage`)

**Method**: `tlc2.TLC -coverage 30` on the core protocol under `ubcc_config.cfg`
(`Nodes={0,1,2}`, `MaxEpoch=4`, `TombstoneWindow=10`). Coverage counts are
`distinct states : transitions generated` per action, taken directly from the
TLC coverage report (`/tmp/tlc_coverage_core.log`, run 2026-07-08).

| Protocol action | distinct states | transitions | Covered? |
|-----------------|----------------:|------------:|:--------:|
| `Init`                 | 1          | 1           | ✅ |
| `GrantShared`          | 429,723    | 448,110     | ✅ |
| `GrantExclusive`       | 381,870    | 402,444     | ✅ |
| `RecallBarrier`        | 1,070,064  | 1,124,928   | ✅ |
| `RecallResponse`       | 1,255,129  | 4,413,312   | ✅ |
| `RecallToGrant`        | 1,167,008  | 3,921,696   | ✅ |
| `RecallOrphanCleanup`  | 16,042     | 5,543,856   | ✅ |
| `InvalidationBarrier`  | 218,592    | 226,152     | ✅ |
| `UpgradeBarrier`       | 258,984    | 267,624     | ✅ |
| `BarrierAck`           | 795,061    | 3,550,284   | ✅ |
| `ClearCommit`          | 21,303     | 7,760,988   | ✅ |
| `UpgradeCommit`        | 39,071     | 1,097,982   | ✅ |
| `Writeback`            | 41,672     | 241,488     | ✅ |
| `Evict`                | 10,208     | 180,792     | ✅ |
| `TickOnly`             | 15,276,027 | 18,240,374  | ✅ |
| `Stutter`              | 0*         | 2,740,381   | ✅* |

**Result: 15/15 protocol actions triggered — action coverage = 100%, zero dead
actions.** Every protocol transition path is exercised by the exhaustive search.

Notes:
- `*Stutter` has `distinct = 0` because it is `UNCHANGED Vars` (a terminal
  self-loop at `tick = MaxTick`): it generates transitions (2.74M) but never a
  *new* distinct state, by design. Not a dead action.
- `RecallOrphanCleanup` (the RECALL-orphan fix, added 2026-07-08) is exercised
  across 5.5M transitions — it is not dead code; the fix is thoroughly explored.
- Action coverage is a **native, non-forgeable TLC metric**. It answers "does the
  model actually reach every protocol path?" — yes, within this scope.

**What this does and does not claim**: it proves *complete action coverage inside
the checked scope*. It does NOT claim production-scale coverage — that boundary
is stated explicitly in A2.

---

## A2. Parameter / Scope Coverage Boundary

Model checking is bounded by construction (infinite state variables like `epoch`,
`tick`, request ids must be capped or TLC cannot terminate). This table states,
honestly, exactly what has been *exhaustively* covered and what has NOT.

### Covered (exhaustively enumerated, zero counterexamples)

| Dimension | UBCC core (`ubcc_config`) | EP single | EP dual | Transport faults |
|-----------|---------------------------|-----------|---------|------------------|
| Nodes / CPUs | 3 nodes | 8 CPUs max config (1 node); 2 CPUs focused configs | 2 CPUs × 2 sockets | 3 nodes |
| Physical addresses (PA) | **1** | 1 | 1 | 1 |
| Epoch bound | `MaxEpoch=4` | — | — | `MaxEpoch=4` |
| Tombstone window | 10 | — | — | 10 |
| Request ids | `0..2` | — | — | `0..2` |
| Data versions | (abstracted) | `MaxDataVersion=1` | — | (abstracted) |
| Distinct states | 20,980,755 | 203,174 max safety; 542 normal liveness; 1,436 bounded-fault; 10,184 max liveness | 52M | 66,766 |
| Search depth | 23 | 22 max safety/max liveness; 18 fault | — | — |
| Properties | 4 safety + 2 liveness | strengthened safety + 5 liveness; single request/CompUC drop | 8 safety | 6 safety |

### NOT covered (explicit boundaries — future work / simulation territory)

| Uncovered dimension | Why it matters | How addressed instead |
|---------------------|----------------|-----------------------|
| **Arbitrary multiple PAs / cross-address interleaving** | Directory-slot contention across many lines, Bloom-filter collisions | Stage D1 covers 2 PAs; O3 focused model covers 2 lines; larger sets remain E2E |
| **≥4 nodes** | Larger sharer sets | small-scope hypothesis: 3 nodes exposes design races; E2E TC50-54 at scale |
| **≥3 sockets & arbitrary cross-socket routing scale** | Multi-socket coherence routing (1-hop vs 2-hop latency) | Stage D2 covers 4 routing planes; larger scale remains E2E |
| **Bloom filter / backstore / MetaRNF** | Performance/infra layer | Abstracted by design (see A3); resident-dir is authoritative — argued in CONSOLIDATED_REPORT §7.2 |
| **Real time / latency / ZMQ timing** | Timeout tuning, message ordering under delay | Not a formal target — E2E simulation (`docs/measure/`) |
| **EP-RNF snoop conflict arbitration (STALE/IMMED matrix)** | Per-cacheline conflict between ReadShared/ReadUnique/CleanUnique and SnpCleanInvalid/SnpUnique/SnpOnce | **DONE (focused model)**: `ep_rnf_snoop_arbitration.tla` exhaustively checks active-recall priority, immediate ReadShared+SnpOnce data response, immediate STALE responses for conflicting write-class snoops, and rejection of preserving snoops. PASS: 328 distinct states, depth 7 (2026-08-03). |
| **ResidentDir capacity and TC224 committed waiter lifecycle** | Capacity waiters, exact tuple retirement, non-Read payload ownership, replay after synchronous queue erase | **PARTIAL FORMAL / FULL E2E**: `ubcc_tc224_waiter_retirement.tla` proves exact commit retirement and preservation in a bounded focused model (274,593 states, depth 6, PASS). Capacity victim selection, H64 fill/writeback, and set pressure remain implementation/E2E territory; TC224 8,192/65,536 full-scale PASS. |
| ~~**Fault types beyond Clear drop/dup**~~ | InvAck/RecallResp/UpgradeAck loss/reorder | **DONE (Stage B1-B3)**: `ubcc_transport_faults.tla` now exhaustively enumerates Clear/InvAck/RecallResp/UpgradeAck × drop/dup/reorder; safety PASS (23.2M states) + liveness PASS. See CONSOLIDATED_REPORT §5.1 |

### Coverage claim (defensible wording)

> "Within a small but precisely-defined scope (3 nodes, single PA, 4 epochs), the
> UBCC protocol core is **exhaustively enumerated with zero safety or liveness
> counterexamples and 100% action coverage**. This is not production-scale
> coverage; the value follows the *small-scope hypothesis* — protocol-design
> defects overwhelmingly manifest in small configurations. Evidence: the
> Medium-severity RECALL-orphan bug (FV3-LEAK-001) was caught and its fix proven
> in exactly this 3-node model. Production-scale behaviour is covered by E2E
> simulation, including the ArmO3CPU 146/146 regression and TC300-303 focused
> architectural cases."

---

## A3. Fidelity Mapping (Model ↔ Code)

**Honest statement first**: TLA+ models are **hand-written mathematical
abstractions**, not machine-extracted from C++. There is no tool that
auto-generates a *useful* TLA+ model from gem5 C++ (auto-translation would
reproduce the code's complexity, not abstract it). Model-code correspondence is
therefore maintained by **manual modelling + review**, and can be further
strengthened by **trace validation** (planned Stage E, out of current scope).

We do NOT claim "model = code". We claim: "the model captures the protocol core's
invariants and progress, and the correspondence below is auditable."

### A3.1 UBCC core action → C++ correspondence

Model actions in `ubcc_protocol_core.tla` map to `modules/ubiomodule/UBCCController.cc`:

| TLA+ action | Models this C++ behaviour | C++ anchor (function) |
|-------------|---------------------------|-----------------------|
| `GrantShared` / `GrantExclusive` | outer request → grant fast paths (G_I, G_S+RS, same-owner) | `processOuterRequest` |
| `RecallBarrier` | G_E/G_M owner ≠ requester → recall initiation | `processOuterRequest` → `initiateRecall` |
| `RecallResponse` | target responds, RECALL → DONE (dataBuf stored) | `processRecallResponse` |
| `RecallToGrant` | same-requester retry consumes RECALL.DONE → GRANT_HANDSHAKE | `processOuterRequest` (RECALL.DONE branch) |
| `RecallOrphanCleanup` | timeout-gated orphan discard (lazy + timer) | `isExpiredRecall` / `cleanupExpiredRecallIfNeeded` / `cleanupExpiredRecalls` (wired in `wakeup()` + `processOuterRequest`) |
| `InvalidationBarrier` | G_S + RU, other sharers → invalidate barrier | `processOuterRequest` (invalidate branch) |
| `UpgradeBarrier` | G_S requester in sharers → upgrade pending | `processOuterUpgradeReq` |
| `BarrierAck` | invalidation/upgrade ack accumulation | `processInvalidationAck` |
| `ClearCommit` | Clear match → commit + tombstone + remove | `processClear` → `commitIntendedResult` / `retireToTombstone` |
| `UpgradeCommit` | upgrade done → commit | `processOuterUpgradeDone` → `commitIntendedResult` |
| `Writeback` / `Evict` | owner writeback / sharer eviction | writeback / evict handlers |
| epoch reserve/commit | `allocateReservedEpoch` monotonicity | `allocateReservedEpoch`, `validateCanonical` |

### A3.2 Focused current-implementation models

| Model | C++ behavior | Exact anchors | Checked invariants |
|-------|--------------|---------------|--------------------|
| `ubcc_tc224_waiter_retirement.tla` | Clear commit removes only the committed stale Read waiter; legacy `reqId=0` also matches base epoch; nonmatching and Writeback/Upgrade/Evict waiters survive; replay tolerates queue erase | `UBCCController.cc`: `retireCommittedResidentWaiters`, `processClear`, `replayResidentWaiters` | no committed Read waiter remains; non-Read preservation; queue-presence consistency; replay safe after erase |
| `ep_rnf_snoop_arbitration.tla` | Active recall has priority; no-inflight snoops are immediate; ReadShared+SnpOnce coexists; conflicting write-class snoops receive immediate STALE; SnpShared/Fwd is rejected outside recall cleanup | `EPRNFController.cc`: `recvSnoopMsg`, `finishChiTxn` | matrix correctness; no stale snoop queue; read/read coexistence; preserving-snoop rejection |
| `ep_o3_completion_backpressure.tla` | Two O3-issued lines enter an EP-RNF global proxy; ReadUnique waits for complete Data or explicit no-data, `Comp_UC`, and injected `CompAck`; rsp/dat output retries preserve pending state | `EPRNFController.cc`: ReadUnique bookkeeping/output retry; `EPSNFController.cc` and `MetaRNFController.cc`: reliable response/data output | strict callback ordering; Ack after Data+Comp; data-beat conservation; no cross-line completion; fair temporary-backpressure progress |
| `ep_intra_node_single.tla` closed suite | Bounded CPU operations, action-level queue capacity, request retry coalescing, RNF CompUC/CompAck/callback, bounded request/CompUC drop, payload/writeback and grant lifetime | EP/HNF/SNF/backend abstract boundary; exact closure mapping and limitations in `ep_intra_node_single_closure_20260810_zh.md` | TypeOK; finite queue semantics; data/dirty consistency; callback ordering; CPU termination; watchdog progress; bounded-drop recovery |

### A3.3 What is deliberately abstracted away (and why it's sound)

| Abstracted | Reason it does not affect protocol correctness |
|------------|-----------------------------------------------|
| Bloom filter | Negative filter only; false positive → extra DRAM read, never wrong directory decision (resident dir authoritative) |
| Backstore / DRAM shadow | Shadow copy; resident dir is single source of truth (invariant I1) |
| MetaRNF multi-flight | Per-PA serialization preserved by scoreboard; different PAs parallel (I4) |
| Real time / ZMQ / latency | Protocol correctness is timing-independent; timing handled by E2E |
| Data payload bytes | Modelled as version/owner, not raw bytes; integrity checked in E2E (FV-7 memcpy chain) |
| ArmO3CPU pipeline/LSQ internals | The focused model starts at the CPU/Ruby/EP request boundary; TC300-303 check architectural outcomes on real `ArmO3CPU` |
| Permanent rsp/dat blockage | O3 liveness assumes temporary backpressure eventually clears; permanent blockage is a platform failure outside this property |

### A3.4 Fidelity risks (known, disclosed)

1. **Hand-modelling drift**: a model edit may lag a code change. Mitigation:
   this mapping table + review; ideally trace validation (Stage E).
2. **`RecallOrphanCleanup` timeout value**: model uses `RecallTimeout=2` (bounded
   scope); code uses `_recallTimeout = 1000000` (hardcoded 1µs). The *shape*
   matches (timeout-gated discard); the *numeric* value differs and should be
   re-evaluated after the ZMQ-latency change (10ns time-scale).
3. **Cleanup liveness depends on `wakeup()` being scheduled**: the model gives
   `WF` to `TickOnly` (time advances); the code assumes `wakeup()` is called
   periodically even when idle. This assumption is not itself verified in code —
   flagged as a follow-up (see CONSOLIDATED_REPORT liveness notes / Stage C2).
4. **Focused models are not a proof of the complete production implementation**:
   the TC224 model abstracts away ResidentDir victim selection and H64 timing;
   the EP arbitration model abstracts CHI payload/TBE behavior. Their purpose is
   to close the exact semantic drift identified in the current C++ anchors.
5. **O3 proof boundary**: `ep_o3_completion_backpressure.tla` exhaustively checks
   2 lines and 2 beats, not arbitrary outstanding depth, the full Arm pipeline,
   complete Ruby/CHI, or the ARMv8 axiomatic memory model. O3 E2E 146/146 plus
   TC300-303 4/4 are executable evidence, not an ISA-level proof.
6. **EP single closure boundary**: the repaired model is finite by transition
   semantics, not a TLC state constraint, but the proof remains parameter-bounded:
   max config is 8 CPUs/3 fresh transactions with `ReqQ=3`, other channels=2;
   fault configs allow one request drop and one `Comp_UC` drop. Permanent loss,
   arbitrary depths, and the full production transport are not proved.

---

## Summary for review

| Question | Answer |
|----------|--------|
| Is coverage quantified? | Yes — 100% action coverage (15/15, zero dead), native TLC metric (A1). |
| What scope? | Exhaustive in 3-node/single-PA/4-epoch; explicit uncovered boundary listed (A2). |
| Model ↔ code? | Hand-modelled with auditable mapping table; abstractions justified; risks disclosed (A3). No claim of auto-generation or model=code. |
