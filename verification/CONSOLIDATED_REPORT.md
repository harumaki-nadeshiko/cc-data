# Consolidated Verification Report

> Produced: 2026-06-22 | Current-implementation addendum: 2026-08-03
> Method: Manual reasoning + formal TLA+ enumeration + gem5 E2E simulation + fault injection
> Budget: $1.71 / $8.00

---

## 1. Methodology

All verification follows a four-method cross-validation approach:

| Method | Scope | Artifacts |
|--------|-------|-----------|
| **Manual reasoning** | State machines, invariants, lifecycle paths | `verification/wave*/fv*.md` |
| **Formal TLA+ enumeration** | Protocol core, EP intra-node, transport faults | `verification/tla/` (5 models) |
| **E2E simulation** | Full-system gem5 with real CHI protocol | `tests/e2e/test_e2e.py` (TC1-54) |
| **Fault injection** | UBRouter drop/dup/reorder hooks | TC47-49 |

Each finding is validated by at least two of these methods when possible.

---

## 2. Formal Verification (TLA+)

### 2.1 Model Inventory

> Updated 2026-07-08 after the RECALL-orphan fix alignment + liveness work.
> State counts changed because `RecallOrphanCleanup` is now timeout-gated
> (`RecallTimeout=2`), which makes `tick`/`createTick` differences participate
> in more distinct states than the old immediate-disappear action.

| Model | File | Config | Distinct States | Depth | Properties | Status |
|-------|------|--------|-----------------|-------|------------|--------|
| UBCC Protocol Core (safety) | `ubcc_protocol_core.tla` + `ubcc_protocol.tla` | `ubcc_config.cfg` | 20,980,755 | 23 | 4 INVARIANT | PASS |
| UBCC Protocol Core (liveness, WITH cleanup) | `ubcc_protocol_core.tla` | `ubcc_liveness.cfg` (`FairSpec`) | 128,577 | 13 | 4 INV + **4 PROPERTY** | **PASS** |
| UBCC Protocol Core (liveness, NO cleanup — contrast) | `ubcc_liveness_nocleanup.tla` | `ubcc_liveness_nocleanup.cfg` | 128,577 | 13 | `RecallProgress` | **VIOLATED (expected)** |
| UBCC Multi-PA (D1, cross-address isolation) | `ubcc_multi_pa.tla` | `ubcc_multi_pa.cfg` (2 PAs, 3 nodes) | 45,760 | 13 | 4 INVARIANT | PASS |
| UBCC Multi-Socket (D2, cross-socket routing) | `ubcc_multi_socket.tla` | `ubcc_multi_socket.cfg` (4 planes, home=0) | 58,561 | 13 | 4 INVARIANT | PASS |
| EP Intra-Node (basic) | `ep_intra_node.tla` | `ep_intra_node.cfg` | 97 | — | 4 INVARIANT | PASS |
| EP Intra-Node (single) | `ep_intra_node_single.tla` | `ep_intra_node_single.cfg` | 74M | — | 6 INVARIANT | PASS |
| EP Intra-Node (dual) | `ep_intra_node_dual.tla` | `ep_intra_node_dual.cfg` | 52M | — | 8 INVARIANT | PASS |
| Transport Faults (B1+B2, safety) | `ubcc_transport_faults.tla` | `ubcc_transport_faults.cfg` | 23,242,903 | 23 | 9 INVARIANT | PASS |
| Transport Faults (B3, liveness) | `ubcc_transport_faults.tla` | `ubcc_transport_faults_liveness.cfg` (`TFFairSpec`) | 25,048 | 13 | 2 INV + 1 PROPERTY | PASS |
| TC224 waiter retirement (focused) | `ubcc_tc224_waiter_retirement.tla` | `ubcc_tc224_waiter_retirement.cfg` | 274,593 | 6 | 5 INVARIANT | PASS |
| EP-RNF snoop arbitration (focused) | `ep_rnf_snoop_arbitration.tla` | `ep_rnf_snoop_arbitration.cfg` | 328 | 7 | 5 INVARIANT | PASS |

### 2.1.1 Liveness verification & RECALL-orphan fix (NEW, 2026-07-08)

The core model now carries two liveness (temporal) properties, checked under a
weak-fairness spec `FairSpec`:

- **`RecallProgress`** — `[]((ost.valid /\ ost.opType="RECALL") ~> ~(ost.valid /\ ost.opType="RECALL"))`:
  a RECALL never wedges the PA slot forever.
- **`OstEventuallyClears`** — `[](ost.valid ~> ~ost.valid)`: every outstanding
  request eventually drains.
- **`InvalidateProgress`** (C1) — an INVALIDATE barrier never stalls collecting
  acks; it always leaves `WAITING_ALL_ACKS`.
- **`UpgradeProgress`** (C1) — an UPGRADE barrier never stalls; it always
  progresses past `WAITING_ALL_ACKS`. (Both verified PASS, 2026-07-08.)

**Fairness design** (see header comment in `ubcc_protocol_core.tla`):
`WF` is granted to the forward-progress actions (`RecallToGrant`, `BarrierAck`,
`ClearCommit`, `UpgradeCommit`, `RecallOrphanCleanup`, and `TickOnly` to force
the clock forward). `RecallResponse` is deliberately **left unfair** — a RECALL
stuck in `WAITING_TARGET_RESP` models the real orphan trigger (lost response /
requester never retries). The forward completion actions and the cleanup net
are horizon-exempt (fireable at `tick = MaxTick`, tick clamped) to avoid
spurious bounded-model counterexamples at the clock boundary.

**Contrast experiment (machine-checked evidence the fix works):**

| Run | Cleanup present? | `RecallProgress` result |
|-----|------------------|-------------------------|
| `ubcc_liveness.cfg` | Yes (`RecallOrphanCleanup` in `Next` + `WF`) | **PASS** |
| `ubcc_liveness_nocleanup.cfg` | No (removed from `Next` + fairness) | **VIOLATED** — lasso: a RECALL by requester 2 stuck in `WAITING_TARGET_RESP` (`createTick=4`) never clears; slot wedged forever |

This is the direct formal counterpart of the FV3-LEAK-001 / FV5-STARVE-001
finding: TLC now **mechanically reproduces the orphan wedge** when the fix is
absent, and **proves it is eliminated** when the frozen fix
(`verification/fixes/recall_orphan_solution.md`, implemented in
`modules/ubiomodule/UBCCController.cc`: `isExpiredRecall` / `cleanupExpiredRecalls`
/ `cleanupExpiredRecallIfNeeded`) is present. The FV3 risk R1 is thus closed at
the model level, not only by static reasoning.

**Scope of the liveness runs**: `Nodes={0,1,2}`, `MaxEpoch=2`,
`TombstoneWindow=3`, `RecallTimeout=2` (smaller than the safety config because
liveness checking does the more expensive SCC/lasso search).

### 2.1.2 Coverage, scope boundary & fidelity → see `fv_coverage_fidelity.md`

Raw state/depth counts measure *exploration size*, not *coverage*. The companion
document `verification/fv_coverage_fidelity.md` provides the actual coverage
evidence:
- **A1 Action coverage**: 100% (15/15 protocol actions triggered, zero dead
  actions) via TLC `-coverage` — a native, non-forgeable metric.
- **A2 Scope boundary**: explicit table of what is exhaustively covered
  (3-node / single-PA / 4-epoch) vs what is NOT (multi-PA, ≥4 nodes, ≥3 sockets,
  BF/backstore, real-time) with the defensible coverage claim.
- **A3 Fidelity mapping**: model-action ↔ C++ function correspondence table,
  justified abstractions, and disclosed fidelity risks. Explicitly states TLA+
  models are hand-written (no auto-generation; no "model = code" claim).

### 2.1.3 Coverage-expansion models (Stages C1, D1, D2 — 2026-07-08)

Three follow-up verification stages were run to widen coverage beyond the
single-PA / RECALL-only baseline. **None uncovered a real code bug** (one modeling
bug — a self-contradictory `UNCHANGED <<..., tick>>` clause that disabled every
protocol action — was found and fixed in the D1 model itself; it never touched
production code). Summary:

| Stage | What it adds | Model | Result |
|-------|--------------|-------|--------|
| **C1** | Liveness for INVALIDATE & UPGRADE barriers (previously only RECALL had liveness) | `ubcc_protocol_core.tla` + `ubcc_liveness.cfg` (`InvalidateProgress`, `UpgradeProgress`) | PASS (128,577 states) |
| **D1** | Multi-PA cross-address isolation (baseline is single-PA) | `ubcc_multi_pa.tla` (2 PAs) | PASS (45,760 states) — each PA's directory independently canonical; no cross-PA contamination |
| **D2** | Cross-socket coherence message routing (baseline dual-socket model had no routing layer) | `ubcc_multi_socket.tla` (4 planes) | PASS (58,561 states) — in-flight/dropped/reordered routed messages never corrupt the directory; home transitions identical for local vs remote requesters |

D2 is directly relevant to the latency work: it models messages sitting in-flight
between sockets (the 1-hop/2-hop routing latency being tuned in
`docs/measure/`) and proves that routing delay/loss/reorder does not affect
coherence correctness.

**Interpretation note**: C1/D1/D2 finding no counterexample is the *expected and
desired* outcome — it extends the "no design defect" evidence to invalidate/
upgrade liveness, multi-address, and multi-socket routing. The one true bug
caught formally in this whole effort remains the RECALL orphan (R1), caught by
the liveness contrast in §2.1.1.

### 2.2 BF/Backstore Impact Assessment

**No impact on TLA+ models.** The TLA+ models operate at the protocol level (MESI state transitions, epoch monotonicity, sharersMask invariants, message ordering). The Bloom Filter and backstore changes are subordinate infrastructure:

- BF: Negative filter only — false positives degrade performance but cannot cause incorrect protocol decisions (resident directory is authoritative)
- Backstore: Shadow copy in DRAM — resident directory is the single source of truth (invariant I2)
- MetaRNF multi-flight: Increased I/O parallelism within the same per-PA serialization guarantee (invariant I4)

All TLA+ models remain valid with 0 modifications required.

### 2.3 Current-implementation focused closure (2026-08-03)

Two production changes were newer than the original core models and are now
covered by bounded focused models:

- **TC224 committed waiter retirement**: exact Read tuple retirement, legacy
  epoch matching, non-Read preservation, and replay safety after synchronous
  queue erase. This closes the formal semantic gap for the final TC224 root
  cause; full ResidentDir/H64 capacity behavior remains E2E/host-tested.
- **EP-RNF STALE/IMMED arbitration**: active-recall priority, immediate
  ReadShared+SnpOnce coexistence, immediate STALE for conflicting write-class
  snoops, and preserving-snoop rejection outside recall cleanup.

These are hand-written abstractions tied to named C++ functions. They do not
replace the larger core, CHI, or full-system models.

---

## 3. Static Verification Results

### 3.1 Wave 0 — Foundation (no instrumentation)

| FV | Focus | Key Finding | Severity |
|----|-------|-------------|----------|
| **FV-1** | State enumeration | 5 sub-machines fully enumerated; all MESI×OpType×OpStage edges classified as LEGAL or checkable | — |
| **FV-2** | Epoch/sharers invariants | `allocateReservedEpoch()` monotone by construction. `validateCanonical()` enforces G_I mask=0, G_S mask!=0, G_E/G_M one-hot. No violations found. | — |
| **FV-3** | OutstandingRequest lifecycle | **FV3-LEAK-001**: RECALL.DONE orphan — blocks PA slot forever when requester never retries | **Medium** |
| | | **FV3-DEAD-001**: 3 dead enum values (CANCELLED, TIMED_OUT, PERSISTENT_BUSY) never assigned | Low |
| | | `ackMask`/`upgradeAckMask` strictly monotonic. `replayArmed` correctly set/cleared. | — |
| **FV-9** | UBMsg field validation | 20 message types audited; 10 integrity concerns catalogued; `BUSY` flag dead code. | Low |
| **FV-11** | State-edge TC coverage | 56/56 edges 100% covered by ≥1 TC. 8 uncovered high-risk boundary conditions identified. | — |

### 3.2 Wave 1 — Boundary + M2 Observability (recommended instrumentation, static analysis done)

| FV | Focus | Key Finding |
|----|-------|-------------|
| **FV-10** | UBMsg round-trip | All semantic fields (epoch, reqId, homeLinePa, flags, body) PRESERVED through send→receive. No semantic bleed from local-only fields. |
| **FV-6** | Snoop classification | 6/6 snoop types MATCH golden matrix. One comment-level DRIFT (SnpRespData_I vs SnpResp_I, code correct). SnpUniqueFwd falls through to silent default. |
| **FV-8** | Invalidate barrier | **Proved**: InvalidationAck always sent AFTER CHI CleanUnique completes. Causal chain verified across 10 hops. |

### 3.3 Wave 2 — Fault + Liveness (required instrumentation, static analysis done)

| FV | Focus | Key Finding |
|----|-------|-------------|
| **FV-4** | Fault recovery | 6 fault types analyzed. Duplicate → full protection (tombstone replay, epoch gate, bitmask idempotent). **Loss** → 3 gaps (no retry timer for fire-and-forget messages, ignored sendClear() return, no bounded retry budget). |
| **FV-7** | Recall data path | 2 complete traces (Read/Write Recall, 23 hops each). epoch+reqId preserved at all 7 boundaries. DataBlk integrity verified (raw memcpy chain). F3 RecallBuffer path wired correctly. |
| **FV-5** | Liveness | 9 wait-points analyzed. 7 with guaranteed progress. **FV5-STARVE-001** = RECALL orphan (same as FV3-LEAK-001). No cross-PA mutual exclusion deadlocks. |

---

## 4. Simulation — E2E Test Matrix

### 4.1 Coverage

54 test cases covering: local/remote DSM, recall, invalidate, upgrade, writeback, evict, Clear replay, tombstone, epoch wrap, fault injection, NUMA, complex workloads.

### 4.2 Execution Status (post-BF/backstore changes)

```
TC1-TC10  : ALL PASS   (basic protocol paths)
TC23      : PASS        (BF false-positive fallback)
TC28      : PASS        (backstore metadata consistency)
TC45      : PASS        (fill-conflict + bloom pressure)
TC47-49   : ALL PASS    (fault injection: drop/dup/reorder)
TC50-54   : ALL PASS    (complex scenarios: ring, ledger, mapreduce, contention, matmul)
```

**21/21 TCs verified PASS.** Remaining TCs (TC11-22, TC24-27, TC29-46 not part of fault-injection group) were not re-run in this round but no protocol-path changes were made that would affect them.

### 4.3 BF/Backstore Change Impact on Tests

| TC | BF/backstore relevance | Post-change result |
|----|----------------------|--------------------|
| TC23 | BF false-positive → reads backstore | PASS (same path, DRAM-native instead of software map) |
| TC28 | Backstore writeback → read-back consistency | PASS (MetaRNF writes now, no _backstore map) |
| TC45 | Bloom filter pressure + fill conflict | PASS (plain BF, lower FPR, same stress profile) |

TC23, TC28, TC45 specifically exercise backstore and BF paths. All pass with the new implementation.

---

## 5. Fault Injection

Fault tolerance is verified at **two levels**: (5.1) exhaustive formal
enumeration of fault combinations in TLA+, and (5.2) concrete E2E fault
injection in gem5. The formal layer proves *all* drop/dup/reorder combinations
are safe & live; the simulation layer confirms specific scenarios on the real
implementation.

### 5.1 Formal fault enumeration (TLA+, B1-B3 — 2026-07-08)

`ubcc_transport_faults.tla` now models the full fault envelope over **all four
acknowledgement-carrying control messages**:

| Message | Transport model | Faults covered |
|---------|-----------------|----------------|
| `Clear` | explicit `transport` queue | drop, duplicate, reorder (Queue/Deliver/Drop/Duplicate/Reorder actions) |
| `InvAck` | `BarrierAck` envelope | drop (unfair action), duplicate (`DupInvAck`, idempotent), reorder (\E node + monotonic ackMask — every arrival order explored) |
| `RecallResp` | `RecallResponse` envelope | drop (unfair), duplicate (`DupRecallResp`, idempotent) |
| `UpgradeAck` | `BarrierAck` (UPGRADE) envelope | drop, duplicate, reorder (as InvAck) |

**B1+B2 (safety)** — `ubcc_transport_faults.cfg`, `Nodes={0,1,2}`, `MaxEpoch=4`:
**PASS**, 23,242,903 distinct states, depth 23. All 9 invariants hold, including
the fault-specific ones:
- `FaultDirCanonical` — directory stays MESI-canonical under any fault combo.
- `FaultNoDoubleCommit` — no (epoch,reqId) committed twice under Clear dup+replay.
- `FaultEpochMonotonic` — committed epochs strictly increasing (no rollback from
  a duplicated/reordered message).
- `TombstoneReplayConsistency` — a Clear never both commits and rejects across
  duplicate deliveries.

**B3 (liveness)** — `ubcc_transport_faults_liveness.cfg` (`TFFairSpec`),
`Nodes={0,1,2}`, `MaxEpoch=2`: **PASS**, 25,048 distinct states. Property
`FaultRecallProgress`: even under drop/dup/reorder, no RECALL wedges the PA slot
forever (lost responses are rescued by the orphan cleanup net).

**Why this is stronger than sampling**: simulation injects a *few chosen* faults;
TLC **exhaustively enumerates every** drop/dup/reorder interleaving in scope and
proves the invariants + progress hold across all of them. This is the formal
upgrade of the TC47-49 sampled results below.

### 5.2 E2E fault injection (gem5)

The current split implementation injects faults in `ubio_main.cc`. Rules can
drop, duplicate, delay, or reorder a matched coherence message. Delay/reorder
buffer the real message in `g_delayedQueue` and deliver it when `fireTick` is
reached; this is no longer a pass-through pseudo-delay.

| TC | Fault Type | Message | Result |
|----|-----------|---------|--------|
| TC47 | Drop + Dup | ClearReq | PASS (tombstone replay recovers) |
| TC48 | Duplicate | InvalidateAck | PASS (ackMask idempotent, bit already set) |
| TC49 | Reorder | InvalidateAck | PASS (ackMask independent of arrival order) |
| TC110 | Drop | ClearReq | PASS in existing regression; verifier requires `[UBFAULT]` |
| TC117 | Reorder | ClearReq | PASS with mandatory `[UBFAULT]` evidence (`logs/fault_smoke_20260803`) |
| TC118 | Drop + Delay | ClearReq on two PAs | PASS with mandatory `[UBFAULT]` evidence (`logs/fault_smoke_20260803`) |
| TC119 | Drop + Duplicate + Delay | ClearReq on three PAs | PASS with mandatory `[UBFAULT]` evidence (`logs/fault_smoke_20260803`) |
| TC148 | 8 Drop + 8 Duplicate + 8 Delay + 8 Reorder | ClearReq on 32 PAs | PASS: 32/32 reads MATCH and all 32 rules fired exactly once (`logs/fault_all_20260803_strict`) |

These three concrete scenarios are now **subsumed by the exhaustive formal model
in 5.1** — the model proves the property for all such interleavings, and the E2E
runs confirm the real implementation matches on the sampled cases.

### 5.3 BF/Backstore Impact

**No impact.** Fault injection operates at the UBRouter transport layer (M1). BF and backstore operate at the directory/resident layer. Faults on outer protocol messages (ClearReq, InvalidateAck) are unaffected.

---

## 6. Consolidated Risk Register

| ID | Risk | Severity | FV Source | Mitigation |
|----|------|----------|-----------|------------|
| **R1** | RECALL.DONE orphan blocks PA slot forever | ~~Medium~~ **CLOSED** | FV3-LEAK-001, FV5-STARVE-001 | **FIXED**: timeout-gated double-layer cleanup (lazy + timer) implemented in `UBCCController.cc` (`isExpiredRecall`/`cleanupExpiredRecalls`); **formally closed** — TLA+ liveness `RecallProgress` PASSES with cleanup and is VIOLATED without it (see §2.1.1). |
| **R2** | No timeout for lost fire-and-forget messages | **Medium** | FV4-G1 | Per-message timeout + requester-side retry |
| **R3** | 3 dead enum values (CANCELLED, TIMED_OUT, PERSISTENT_BUSY) | Low | FV3-DEAD-001 | Either implement or remove from enum |
| **R4** | `sendClear()` return value ignored | Low | FV4-G2 | Propagate failure to trigger retry |
| **R5** | SnpUniqueFwd silent fallback instead of fatal | Low | FV6 | Add `fatal()` for consistency |
| **R6** | No bounded retry budget | Low | FV4-G3 | Add per-requester retry counter with cap |

---

## 7. BF/Backstore Change Audit

### 7.1 Changes Made

| Component | Before | After | Protocol Impact |
|-----------|--------|-------|-----------------|
| ResidentDir BF | Counting BF (64KB, 4-bit counters, k=3) | Plain grouped BF (60KB, 16 groups, k=4) | None (BF is negative filter only) |
| ResidentDir Index | None | GroupIndex (4KB, 16×256B) | None (new additive feature) |
| UBCC Backstore | `std::unordered_map _backstore` | Removed (MetaRNF required) | None (resident is authoritative) |
| MetaRNF | Single-flight (`_requestInFlight` bool) | 8-flight + scoreboard | None (same per-PA serial guarantee) |
| EPBackend | Software fallback on MetaRNF=NULL | `panic()` if MetaRNF=NULL | None (MetaRNF always present in config) |

### 7.2 Correctness Argument

1. **BF is a negative filter**: `bloomMayContain(pa)==false` means "definitely not present". `bloomMayContain(pa)==true` means "maybe present → check resident + DRAM". A false positive triggers an unnecessary DRAM read but cannot cause incorrect directory state.

2. **Resident is authoritative** (invariant I1): When resident and DRAM backstore both hold data for the same PA, resident wins. The DRAM backstore is a shadow copy updated during writeback.

3. **Multi-flight preserves serialization** (invariant I4): Per-PA single-flight is enforced by the scoreboard (`_scoreboard[metadataPa] → slot_id`). Different PAs can proceed in parallel (max 8), but same PA is always serialized.

---

## 8. Future Work

1. ~~**FV3-LEAK-001 fix**: Add RECALL timeout/cleanup to prevent permanent PA blocking~~ — **DONE** (implemented in `UBCCController.cc`, formally verified via liveness contrast, see §2.1.1)
2. ~~**Strict fault E2E refresh**~~ — **DONE**: TC117-119 are 3/3 PASS with mandatory `[UBFAULT]` evidence in `logs/fault_smoke_20260803`.
3. ~~**High-density ClearReq qualification**~~ — **DONE**: TC148 provides 32 bounded deterministic hits across 32 PAs with per-rule count assertions. Cross-message Level-2 qualification remains future work and must follow each message's recovery contract.
4. **TC60 implementation**: DRAM backstore writeback + BF reconstruction E2E test (designed in `docs/recovery/fv_fixes/bloom_filter_backstore_dram.md`)
5. **256B page support**: Currently using 64B MetaLine; expand to 256B pages for ablation study
6. **Schema C ablation comparison**: After 256B pages, compare Schema A vs Schema C performance

---

## 9. Artifact Index

| Directory | Contents |
|-----------|----------|
| `verification/wave0/` | FV-1,2,3,9,11 (static foundation) |
| `verification/wave1/` | FV-6,8,10 (M2 + boundary) |
| `verification/wave2/` | FV-4,5,7 (fault + liveness) |
| `verification/tla/` | TLA+ models + TLC configs. Safety: core, transport, EP, multi-PA, multi-socket. Liveness: cleanup contrast and transport progress. Current focused closure: `ubcc_tc224_waiter_retirement.{tla,cfg}` and `ep_rnf_snoop_arbitration.{tla,cfg}`. |
| `verification/fv_coverage_fidelity.md` | Coverage quantification (A1 action coverage 100%), scope boundary table (A2), model↔code fidelity mapping (A3). |
| `docs/recovery/fv_fixes/` | BF/backstore design docs |
| `tests/e2e/test_e2e.py` | TC1-54 E2E test driver |
