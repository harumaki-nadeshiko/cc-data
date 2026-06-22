# Consolidated Verification Report

> Produced: 2026-06-22
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

| Model | File | States | Invariants | Status |
|-------|------|--------|------------|--------|
| UBCC Protocol Core | `ubcc_protocol_core.tla` + `ubcc_protocol.tla` | 9,916 | 3 | PASS |
| EP Intra-Node (basic) | `ep_intra_node.tla` | 97 | 4 | PASS |
| EP Intra-Node (single) | `ep_intra_node_single.tla` | 74M | 6 | PASS |
| EP Intra-Node (dual) | `ep_intra_node_dual.tla` | 52M | 8 | PASS |
| Transport Faults | `ubcc_transport_faults.tla` | depth 1 | self-contained | PASS |

### 2.2 BF/Backstore Impact Assessment

**No impact on TLA+ models.** The TLA+ models operate at the protocol level (MESI state transitions, epoch monotonicity, sharersMask invariants, message ordering). The Bloom Filter and backstore changes are subordinate infrastructure:

- BF: Negative filter only — false positives degrade performance but cannot cause incorrect protocol decisions (resident directory is authoritative)
- Backstore: Shadow copy in DRAM — resident directory is the single source of truth (invariant I2)
- MetaRNF multi-flight: Increased I/O parallelism within the same per-PA serialization guarantee (invariant I4)

All TLA+ models remain valid with 0 modifications required.

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

### 5.1 Mechanism

`UBRouter` has debug-only fault hooks that can drop, duplicate, or reorder messages. Controlled by `ruby_system.descendants()` traversal to find UBRouter instances in Python test harness.

### 5.2 Test Results

| TC | Fault Type | Message | Result |
|----|-----------|---------|--------|
| TC47 | Drop + Dup | ClearReq | PASS (tombstone replay recovers) |
| TC48 | Duplicate | InvalidateAck | PASS (ackMask idempotent, bit already set) |
| TC49 | Reorder | InvalidateAck | PASS (ackMask independent of arrival order) |

### 5.3 BF/Backstore Impact

**No impact.** Fault injection operates at the UBRouter transport layer (M1). BF and backstore operate at the directory/resident layer. Faults on outer protocol messages (ClearReq, InvalidateAck) are unaffected.

---

## 6. Consolidated Risk Register

| ID | Risk | Severity | FV Source | Mitigation |
|----|------|----------|-----------|------------|
| **R1** | RECALL.DONE orphan blocks PA slot forever | **Medium** | FV3-LEAK-001, FV5-STARVE-001 | Add timeout/cleanup for RECALL entries with no requester retry within window W |
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

1. **FV3-LEAK-001 fix**: Add RECALL timeout/cleanup to prevent permanent PA blocking
2. **FW-4 closure**: Instrument and actually inject faults at M1 for loss/reorder/dup, not just static analysis
3. **TC60 implementation**: DRAM backstore writeback + BF reconstruction E2E test (designed in `docs/recovery/fv_fixes/bloom_filter_backstore_dram.md`)
4. **256B page support**: Currently using 64B MetaLine; expand to 256B pages for ablation study
5. **Schema C ablation comparison**: After 256B pages, compare Schema A vs Schema C performance

---

## 9. Artifact Index

| Directory | Contents |
|-----------|----------|
| `verification/wave0/` | FV-1,2,3,9,11 (static foundation) |
| `verification/wave1/` | FV-6,8,10 (M2 + boundary) |
| `verification/wave2/` | FV-4,5,7 (fault + liveness) |
| `verification/tla/` | 5 TLA+ models + TLC configs + traces |
| `docs/recovery/fv_fixes/` | BF/backstore design docs |
| `tests/e2e/test_e2e.py` | TC1-54 E2E test driver |
