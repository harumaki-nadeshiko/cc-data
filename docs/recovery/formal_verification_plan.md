# CC-EP Formal Verification Plan

## 1. Purpose

This plan defines how to verify the CC-EP cross-node coherence framework for:

1. **Correctness**: single-writer/multiple-reader, ownership consistency, data consistency.
2. **Deadlock freedom**: no reachable state where all enabled protocol participants wait forever.
3. **Livelock freedom**: under fair retry/timer scheduling, requests eventually commit or enter an explicit fail-stop state.
4. **Network fault tolerance**: correctness under UBCC-link reordering, duplication, and packet loss.

The verification target is the protocol described in `docs/recovery/scheme_v4.md`, with concrete implementation anchors in:

- `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc`
- `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh`
- `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc`
- `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh`
- `docs/recovery/tc45_protocol_primitives.md`

---

## 2. Recommended Formal Methods

No single method is sufficient. Use a **three-layer strategy**:

### 2.1 TLA+ as the primary protocol model

Use **TLA+** for the end-to-end control protocol because it naturally expresses:

- asynchronous message passing,
- retries and loss,
- per-line outstanding objects,
- fairness/liveness properties,
- wait-for and replay/tombstone behavior.

Use:

- **TLC** for explicit-state exploration on very small instances,
- **Apalache** for symbolic bounded checking of invariants and temporal properties.

**Why TLA+ fits CC-EP best**

- UBCC `reserve-then-commit` is a temporal protocol, not just a local FSM.
- `RECALL`, `INVALIDATE`, `GRANT_HANDSHAKE`, and `UPGRADE_PENDING` are naturally modeled as actions over shared state and message sets.
- Network reordering/loss is easy to model by nondeterministically reordering or dropping UBCC messages.

### 2.2 Rumur/Murphi for finite-state safety and deadlock search

Build a reduced **Murphi/Rumur** model for exhaustive small-instance checking:

- 2-3 nodes,
- 1-2 cache lines,
- bounded retry counters,
- bounded message buffers.

Use it to aggressively search for:

- deadlocks,
- illegal transitions,
- duplicate-ack corner cases,
- race windows around `Clear`, `RecallResponse`, and `OuterUpgradeDone`.

**Why Rumur/Murphi is useful here**

- The protocol is still directory/coherence-like and finite after abstraction.
- Murphi-style tools are very effective for coherence safety bugs and deadlock counterexamples.

### 2.3 CBMC + assertion instrumentation for code-adjacent checks

Use **CBMC** selectively on C++ helpers and safety-critical local routines, not on full gem5:

- epoch comparison helper,
- tuple matching logic,
- tombstone replay acceptance,
- request replay rebasing,
- `processClear()` validation logic,
- `processOuterUpgradeDone()` acceptance and commit gates.

This is not a replacement for protocol model checking; it is a way to prove local helper correctness and prevent implementation drift.

---

## 3. Verification Scope

### 3.1 Controllers to model

#### UBCC

Abstract the committed directory and outstanding lifecycle:

- committed `DirEntry.state ∈ {G_I, G_S, G_E, G_M}`
- committed `owner`, `sharers`, `dirty`, `epoch`
- live `OutstandingRequest` with:
  - `opType ∈ {RECALL, INVALIDATE, GRANT_HANDSHAKE, UPGRADE_PENDING}`
  - `stage ∈ {CREATED, WAITING_TARGET_RESP, WAITING_ALL_ACKS, WAITING_CLEAR, WAITING_LOCAL_DONE, DONE, CANCELLED, TIMED_OUT, PERSISTENT_BUSY}`
  - `baseEpoch`, `reservedEpoch`, `reqId`
  - intended result fields
  - tombstone window `W`
  - pending requester queue

#### EPBackend

Model only coherence-relevant requester-side state:

- requester line state `R_I/R_WAIT_GRANT/R_S/R_E/R_M`
- pending grant tuple `(linePa, homeNode, baseEpoch, reqId, grantType)`
- retry/retransmit behavior for `Clear`, `OuterUpgradeReq`, `OuterUpgradeDone`
- routing for recall and invalidation

#### EP-RNF

Use an abstract CHI proxy model:

- one inflight CHI transaction per PA,
- one snoop slot per PA,
- pending strongest-op retry ordering,
- proxy op type `{None, RecallUnique, InvalidateOnly}`
- local response gating on `OuterUpgradeAck(true)`.

For HN-F-facing correctness, EP-RNF only needs to model whether it can:

- issue `ReadShared`, `ReadUnique`, `CleanUnique`,
- defer `SnpResp_I`,
- return callback data/token,
- send `CompAck`.

#### HN-F

Do **not** model full SLICC internals. Use an abstract line model with the states named in the protocol docs:

- stable: `I`, `SC`, `UC`, `UD`, `SD`
- selected transients: `RSC`, `RSD`, `UC_RU`, `UD_RU`, `SC_RSC`
- per-line TBE allocated or not
- sharer set including EP-RNF registration bit
- owner/dirty metadata needed to decide snoops

This abstraction is enough to check the EP/HN boundary rules without reimplementing all CHI.

#### EP-SNF

Model minimally:

- `S_IDLE`
- `S_WAIT_OUTER` for remote miss
- `S_WAIT_WRDATA` for `WriteNoSnp`

EP-SNF is mostly a pass-through and should not dominate the state space.

### 3.2 Protocol paths to verify first

1. **ReadShared**
2. **ReadUnique**
3. **CleanUnique**-based invalidation
4. **WriteNoSnp** to remote DDR4
5. **Local write upgrade** via `OuterUpgradeReq/Ack/Done/DoneAck`

### 3.3 Fault model

Model faults only on the **UBCC outer network**, not on the local CHI fabric:

- message reordering,
- message duplication,
- message loss,
- arbitrary finite delay.

Apply this to:

- `OuterReq`
- `OuterRecallMsg` / `RecallResponse`
- `OuterInvalidateMsg` / `InvalidationAck`
- `Clear` / `ClearAck`
- `OuterUpgradeReq/Ack/Done/DoneAck`

Assume local CHI is reliable but asynchronous.

---

## 4. Core Invariants to Verify

### 4.1 Coherence safety invariants

1. **Single writer / multiple reader**  
   For each line, at all times:
   - at most one global writer exists,
   - if any node holds write permission, no other node holds shared or exclusive permission.

2. **Owner-directory consistency**  
   - `UBCC.state = G_E or G_M` iff `owner != None` and committed sharer set excludes all non-owner nodes.
   - `UBCC.state = G_S` iff `owner = None` and sharer set is non-empty.

3. **Committed state changes only at legal linearization points**  
   - normal miss grants commit only on accepted matching `Clear`
   - local upgrades commit only on accepted matching `OuterUpgradeDone`
   - `RECALL` and `INVALIDATE` completion alone never commit the directory.

4. **Epoch monotonicity**  
   Committed `epoch` is monotonic per line; `reservedEpoch` may be allocated speculatively but becomes committed only at the legal commit point.

5. **No stale tuple commit**  
   A `Clear` or `OuterUpgradeDone` can modify a line only if `(linePa, requester/src, baseEpoch/epoch, reqId, opType, stage)` matches the unique live object expected by UBCC.

6. **Data-source correctness**  
   The data returned in a grant equals either:
   - home DDR4 data for clean/shared cases,
   - recalled dirty owner data when a dirty owner existed,
   - zero-fill only when the line is architecturally uninitialized.

### 4.2 Lifecycle invariants

7. **At most one live commit object per PA**  
   For a line, there is at most one live `OutstandingRequest` that can eventually commit state.

8. **Outstanding terminality**  
   `DONE/CANCELLED/TIMED_OUT/PERSISTENT_BUSY` are terminal with respect to directory rewrite.

9. **Tombstone idempotence**  
   After `GRANT_HANDSHAKE -> DONE`, duplicate `Clear` within window `W` produces the same acceptance result and must not re-commit the directory.

10. **Replay rebasing correctness**  
    Any replayed requester must use the current committed epoch as its new `baseEpoch`; otherwise a later `Clear` cannot be accepted.

11. **No self-recall / no illegal Fwd target**  
    The requester is never selected as recall target for itself, and EP-RNF is never used as an illegal Fwd data source.

12. **Upgrade ack irrevocability**  
    Once `OuterUpgradeAck(true)` is sent, the corresponding `UPGRADE_PENDING` cannot be cancelled or bypassed by any conflicting grant.

### 4.3 Liveness-oriented invariants

13. **No wait cycle across controllers**  
    The wait-for graph per line must remain acyclic:
    - HN-F may wait on EP-RNF,
    - EP-RNF may wait on EPBackend/UBCC ack,
    - UBCC may wait on requester `Clear` or owner/sharer responses,
    - but no reachable state may form a cycle where each dependency requires the next to act first.

14. **Grant handshake eventual resolution under fairness**  
    If prerequisites are complete and the requester continues retransmitting `Clear`, then eventually either:
    - UBCC accepts the `Clear`, or
    - the line enters explicit `TIMED_OUT/PERSISTENT_BUSY` fault state.

15. **Upgrade eventual resolution under fairness**  
    If `OuterUpgradeAck(true)` was issued and the requester continues retransmitting `OuterUpgradeDone`, eventually UBCC accepts it or remains in explicit `PERSISTENT_BUSY` without violating safety.

---

## 5. UBCC OutstandingRequest Deadlock Analysis Plan

This is the highest-priority proof target.

### 5.1 Wait-for graph to model

For each PA, define waits-on edges:

- `RECALL(WAITING_TARGET_RESP)` → owner EPBackend / owner EP-RNF / owner HN-F completion
- `INVALIDATE(WAITING_ALL_ACKS)` → each targeted sharer ack
- `GRANT_HANDSHAKE(WAITING_CLEAR)` → requester EPBackend `Clear`
- `UPGRADE_PENDING(WAITING_LOCAL_DONE)` → requester-side local HN-F upgrade completion + `OuterUpgradeDone`
- HN-F transient/TBE → expected CHI response or `CompAck`
- EP-RNF deferred snoop response → `OuterUpgradeAck(true)` or invalidate completion

### 5.2 Deadlock hypotheses to check

1. **Recall/Grant/Clear cycle**  
   UBCC waits for recall; requester has already moved on and waits for grant; owner never sends response.

2. **Upgrade gating cycle**  
   HN-F waits for `SnpResp_I`, EP-RNF waits for `OuterUpgradeAck`, UBCC waits for sharer invalidations that require HN-F progress.

3. **CompAck/TBE retention cycle**  
   HN-F retains TBE waiting for `CompAck`; EP-RNF cannot issue `CompAck` because some outer callback is blocked on the same PA.

4. **Queue starvation cycle**  
   live requester never reaches commit, so `pendingRequesters` are never replayed.

### 5.3 Expected proof argument

The protocol should be deadlock-free **if** the following hold:

- one live outstanding per PA,
- conflicting requests receive `BUSY/RETRY` rather than blocking resources,
- `RECALL` and `INVALIDATE` completion strictly precede `GRANT_HANDSHAKE`,
- `Clear`/`UpgradeDone` retries are idempotent,
- `UPGRADE_PENDING` after ack is fenced and cannot be bypassed.

The model checker must confirm there is no reachable cycle contradicting those assumptions.

---

## 6. Modeling Approach

### 6.1 Abstraction level

Model **one cache line first**. Most protocol hazards are per-PA. Add a second line only after the single-line model is clean.

Recommended staged instance sizes:

- **Stage A**: 2 nodes, 1 line, 1 requester each
- **Stage B**: 3 nodes, 1 line, 2 requesters, reorder/loss enabled
- **Stage C**: 3 nodes, 2 lines, bounded queues and retries

### 6.2 State variables

For each line:

- UBCC committed directory state
- live outstanding object or none
- tombstone set
- pending requester queue
- per-node requester permission state
- abstract HN-F line state and TBE bit
- EP-RNF inflight operation and snoop-slot state
- network channel contents as multisets or bounded sequences

### 6.3 Message semantics

Represent each outer message as a record with at least:

- `pa`
- `src`
- `dst`
- `epoch`
- `reqId`
- `kind`

Network actions:

- `Send(msg)`
- `Deliver(msg)`
- `Drop(msg)`
- `Duplicate(msg)`
- `Reorder(channel)`

### 6.4 Fairness assumptions

For liveness claims, explicitly assume:

- weak fairness on retry timers,
- weak fairness on message delivery for messages not permanently dropped,
- fair scheduling among pending requesters,
- finite loss bursts, not adversarial infinite loss.

Without these assumptions, packet loss can trivially violate liveness for any retry-based protocol.

---

## 7. Specific Properties to Prove

### 7.1 Correctness properties

- No two nodes simultaneously hold `R_M/R_E`-equivalent global write ownership.
- If a node holds global shared permission, the committed UBCC line is not committed to a conflicting owner.
- Any committed owner change is preceded by either recall completion or invalidation completion as required.
- HN-F local state after proxy completion must not incorrectly install EP-RNF as owner.
- DCT fallback when EP-RNF is sole sharer never uses a Fwd-only path.

### 7.2 Deadlock properties

- No reachable global state exists with no enabled action except stuttering, unless all requests are complete or explicitly faulted.
- No per-line wait cycle exists in the wait-for graph.
- `pendingRequesters` cannot remain blocked forever behind a live object that has an enabled retry/delivery path under fairness.

### 7.3 Livelock properties

- Under fair delivery and retry, a stable request is not perpetually requeued without commit.
- Strongest-op retry ordering in EP-RNF does not starve weaker but still valid operations forever.
- Repeated duplicate `Clear` or duplicate `OuterUpgradeReq/Done` cannot prevent eventual retirement.

### 7.4 Fault-tolerance properties

- Reordered `Clear`/`RecallResponse`/`InvalidationAck` messages do not cause incorrect directory commits.
- Lost `ClearAck` or `OuterUpgradeDoneAck` may hurt performance but do not violate safety; duplicate requests remain idempotent.
- Duplicate `Clear` inside tombstone window `W` is harmless.
- Dropped messages cannot resurrect stale ownership.

---

## 8. Practical Tooling for This C++ Codebase

### 8.1 Most practical stack

1. **TLA+ spec in `docs/recovery/formal/`**  
   Best for architecture-level protocol proofs.

2. **Apalache in CI for bounded checks**  
   Fast enough for regression checks on the abstract model.

3. **Rumur for small exhaustive deadlock runs**  
   Best counterexample generator for coherence-style finite models.

4. **CBMC for helper routines**  
   Apply only to isolated C++ functions or extracted harnesses.

5. **Runtime assertion monitors in gem5**  
   Mirror formal invariants in implementation logs and assertions.

### 8.2 Not recommended as the primary method

- Full-code C++ model checking of gem5: impractical state explosion.
- Pure theorem proving first: too expensive before protocol stabilizes.
- Relying only on simulation/random tests: insufficient for deadlock/livelock proofs.

---

## 9. Execution Plan

### Phase 1: Spec extraction

Create a protocol state inventory from `scheme_v4.md` and `tc45_protocol_primitives.md`:

- controller states,
- message types,
- legal transitions,
- linearization points,
- wait points.

**Deliverable**: state/event matrix.

### Phase 2: Abstract TLA+ model

Build `CCEP.tla` for one line with:

- UBCC committed state,
- outstanding lifecycle,
- EPBackend requester state,
- EP-RNF proxy state,
- abstract HN-F/TBE,
- lossy reordered outer network.

**Deliverable**: initial invariant suite and safety checks.

### Phase 3: Deadlock/livelock model

Add wait-for graph variables and fairness clauses.

**Deliverable**: temporal properties for eventual `Clear` / `UpgradeDone` retirement.

### Phase 4: Rumur/Murphi reduction

Encode the same per-line protocol in a smaller finite model to exhaustively search:

- deadlocks,
- queue starvation,
- duplicate replay hazards,
- race windows.

**Deliverable**: counterexample traces or proof on bounded topology.

### Phase 5: Code-linked checking

Add implementation assertions for the proven invariants, especially in:

- `UBCCController::processOuterRequest`
- `UBCCController::processClear`
- `UBCCController::processOuterUpgradeReq`
- `UBCCController::processOuterUpgradeDone`
- `EPBackend::handleRemoteMiss`
- `EPBackend::sendClear`

**Deliverable**: invariant/assertion checklist for code review and regression tests.

### Phase 6: Fault-injection validation in gem5

Instrument the outer network shim to inject:

- delayed delivery,
- reordering,
- drop,
- duplicate.

Compare traces against formal invariants.

**Deliverable**: model-to-code conformance report.

---

## 10. Highest-Risk Properties to Verify First

1. `RECALL -> GRANT_HANDSHAKE -> Clear` closure
2. `UPGRADE_PENDING` irrevocable-after-ack behavior
3. replay rebasing of `baseEpoch`
4. tombstone/idempotent `Clear` replay
5. EP-RNF-only sharer with forced DCT fallback
6. HN-F TBE / `CompAck` interactions around same-tick events

These are the most likely sources of correctness loss or deadlock.

---

## 11. Success Criteria

The protocol is considered formally validated when:

1. TLA+ safety invariants hold for all bounded configurations explored.
2. TLA+/Apalache liveness properties hold under explicit fairness assumptions.
3. Rumur finds no deadlock in the reduced exhaustive model.
4. Fault-model runs show no safety violation under reorder/drop/duplicate behavior.
5. Implementation assertions corresponding to the formal invariants remain silent on stress tests.

---

## 12. Recommended Immediate Next Steps

1. Write a one-line, three-node TLA+ spec for UBCC + EPBackend + abstract EP-RNF/HN-F.
2. Encode the `OutstandingRequest` lifecycle first; everything else should compose around it.
3. Prove the linearization-point invariants before attempting full liveness.
4. Add a reduced Rumur model specifically for `RECALL`, `INVALIDATE`, `GRANT_HANDSHAKE`, and `UPGRADE_PENDING`.
5. Mirror the proven tuple-matching and tombstone invariants as C++ assertions in UBCC.
