# FV-11: State-Edge to TC Coverage Matrix

> Mapping protocol state edges (UBCCController.cc) to E2E test cases (test_e2e.py TC1-54).
> Each edge = UBCC/EP-RNF function + DirEntry transition. Coverage type: **Direct** (assert-based
> verifier checks edge-specific output) or **Indirect** (side-effect through data correctness).

## 1. Coverage Matrix

### 1.1 RECALL Path (`processOuterRequest` → `initiateRecall` → `processRecallResponse`)

| # | Edge | Category | DirEntry Δ | Covering TC | Coverage Type | Notes |
|---|------|----------|-----------|-------------|---------------|-------|
| R1 | G_E/G_M + other requester → RECALL CREATED→WAITING_TARGET_RESP | recall | entry unchanged (reserved only) | TC2, TC3, TC4, TC5, TC6, TC17, TC41, TC44 | Direct (TC2/TC3/TC4 assert remote read convergence) | TC2 is baseline remote-read recall; TC3/TC4 add multi-node cycling |
| R2 | RECALL WAITING_TARGET_RESP → DONE (processRecallResponse) | recall | entry unchanged (recallBarrierDone) | TC2, TC3, TC4, TC5, TC6, TC15, TC40, TC41, TC43 | Indirect (verified via final data correctness) | TC40 specifically asserts retry_count≥1 marker for timeout path |
| R3 | RECALL.DONE → GRANT_HANDSHAKE WAITING_CLEAR (same requester retry hits terminal RECALL) | recall | entry unchanged → GRANT_HANDSHAKE created | TC2, TC3, TC5, TC43 | Indirect (retry path; TC5 asserts tombstone replay) | §4.2 recall_done_fix; F2 lifecycle split |
| R4 | RECALL.DONE for different requester → enqueue pending (not consumed) | recall | entry unchanged | TC4, TC15, TC24 | Indirect (pending queue depth stress) | §4.2 Case B |
| R5 | RECALL → ReadShared callback → SnpResp_SC → data to home memory | recall | data written to home DDR | TC3, TC17, TC46 | Indirect (data convergence) | F2 async recall via EP-RNF → HN-F |
| R6 | RECALL → ReadUnique callback → data capture | recall | data captured in recall buffer | TC17, TC40, TC41 | Direct (TC17 asserts pre/post DMA values) | Write recall path |

### 1.2 INVALIDATE Path (`processOuterRequest` → `processInvalidationAck`)

| # | Edge | Category | DirEntry Δ | Covering TC | Coverage Type | Notes |
|---|------|----------|-----------|-------------|---------------|-------|
| I1 | G_S + RU with other sharers → INVALIDATE CREATED→WAITING_ALL_ACKS | invalidate | entry unchanged (outstanding created) | TC8, TC25, TC44 | Direct (TC8 asserts Node1 sees 0xBBB after inv) | INVALIDATE created for non-requester sharers |
| I2 | INVALIDATE WAITING_ALL_ACKS → ack received (processInvalidationAck) | invalidate | sharersMask &= ~ackNode | TC8, TC25, TC41, TC44, TC48 | Indirect (TC48 injects dup InvalidateAck) | TC48 specifically tests idempotent ack handling |
| I3 | INVALIDATE WAITING_ALL_ACKS → DONE (all acks received) | invalidate | INVALIDATE→GRANT_HANDSHAKE in-place | TC8, TC25, TC44 | Direct (TC25 asserts no drift after 512 cycles) | INVALIDATE→GRANT_HANDSHAKE conversion |
| I4 | INVALIDATE DONE → GRANT_HANDSHAKE WAITING_CLEAR (replayArmed=true) | invalidate | entry unchanged | TC8, TC25, TC41 | Indirect (grant matched on requester retry) | replayArmed allows retry to hit grant directly |
| I5 | Stale InvalidateAck (epoch mismatch) → REJECTED | invalidate | entry unchanged; staleRejectedCount++ | TC27, TC30, TC38 | Direct (TC27 asserts wrap marker, TC30 asserts stale/replay) | epoch half-range check |

### 1.3 GRANT_HANDSHAKE Path (`processOuterRequest` grant creation → `processClear` commit)

| # | Edge | Category | DirEntry Δ | Covering TC | Coverage Type | Notes |
|---|------|----------|-----------|-------------|---------------|-------|
| G1 | G_I + RS → GRANT_HANDSHAKE G_S WAITING_CLEAR | grant | entry unchanged | TC1, TC6, TC11, TC18 | Direct (TC1 asserts 0xCAFE; TC6 asserts 0xDEADBEEF) | First miss shared; DataSource=HomeMemory |
| G2 | G_I + RU(!WI) → GRANT_HANDSHAKE G_E WAITING_CLEAR | grant | entry unchanged | TC29, TC36 | Direct (TC29 asserts [TC29_UPG] exclusive→modified) | |
| G3 | G_I + RU(WI) → GRANT_HANDSHAKE G_M WAITING_CLEAR | grant | entry unchanged | TC1, TC5, TC7, TC19 | Direct (TC1/TC5/TC7 all assert specific final values) | First miss write |
| G4 | G_S + RS (add sharer) → GRANT_HANDSHAKE G_S WAITING_CLEAR | grant | entry unchanged | TC6, TC11, TC14 | Indirect (TC6 asserts both Node1 and Node2 read 0xDEADBEEF) | Shared fanout |
| G5 | G_S + RU (no other sharers, self-upgrade) → GRANT_HANDSHAKE G_E/G_M | grant | entry unchanged | TC11, TC29, TC36 | Direct (TC11 asserts snoop chain; TC29 asserts [TC29_UPG]) | No invalidation needed |
| G6 | G_E/G_M + same-owner re-access → GRANT_HANDSHAKE | grant | entry unchanged | TC3, TC5, TC43 | Indirect (ownership cycling) | Same owner reuse |
| G7 | GRANT_HANDSHAKE WAITING_CLEAR → Clear accepted → commitIntendedResult (processClear) | grant | state=intendedState, epoch=reservedEpoch, sharersMask committed | TC1–TC8, TC10–TC44 | Direct (every TC that asserts a final value) | The universal commit edge |
| G8 | GRANT_HANDSHAKE WAITING_CLEAR → stale Clear (epoch mismatch) → retireToTombstone | grant | entry unchanged; tombstone created | TC25, TC27, TC30 | Direct (TC30 asserts stale/replay marker) | D-18 stale GRANT_HANDSHAKE retirement |
| G9 | GRANT_HANDSHAKE WAITING_CLEAR → Clear accepted → tombstone created (retireToTombstone) | grant | entry committed + tombstone recorded | TC5, TC25, TC30, TC47 | Direct (TC25 asserts 512 cycle stability with tombstone replay) | Duplicate Clear protection |

### 1.4 UPGRADE_PENDING Path (`processOuterUpgradeReq` → `processOuterUpgradeDone`)

| # | Edge | Category | DirEntry Δ | Covering TC | Coverage Type | Notes |
|---|------|----------|-----------|-------------|---------------|-------|
| U1 | G_S + existing sharer upgrade (no other sharers) → UPGRADE_PENDING WAITING_LOCAL_DONE | upgrade | entry unchanged | TC11, TC29, TC36 | Direct (TC11 asserts snoop chain; TC29 assert [TC29_UPG]) | Fast path; no invalidation needed |
| U2 | G_S + existing sharer upgrade (other sharers exist) → UPGRADE_PENDING WAITING_ALL_ACKS | upgrade | entry unchanged | TC8, TC16, TC44 | Direct (TC8 asserts 0xBBB after upgrade+invalidate) | Requires invalidation of other sharers |
| U3 | UPGRADE_PENDING WAITING_ALL_ACKS → invalidation ack received | upgrade | sharersMask &= ~ackNode | TC8, TC16, TC44 | Indirect (verified via final value) | upgrade_invalidate_fix D2 |
| U4 | UPGRADE_PENDING WAITING_ALL_ACKS → all acks done → WAITING_LOCAL_DONE (UpgradeAckNotify sent) | upgrade | entry unchanged | TC8, TC16 | Direct (TC16 asserts upgrade-path evidence in log) | UpgradeAckNotify triggers deferred SnpResp_I |
| U5 | UPGRADE_PENDING WAITING_ALL_ACKS → UpgradeDone arrives before acks (TENTATIVE cache) | upgrade | entry unchanged; upgradeDoneArrived=true | TC8, TC16 | Indirect (upgrade_invalidate_fix D4 TENTATIVE) | Done cached; commit deferred until all acks |
| U6 | UPGRADE_PENDING WAITING_LOCAL_DONE → UpgradeDone → commitIntendedResult | upgrade | state=G_M, epoch=reservedEpoch, sharersMask=0 | TC8, TC11, TC16, TC29, TC36, TC37, TC44 | Direct (all assert final converged value) | Primary upgrade commit edge |
| U7 | Concurrent UPGRADE_PENDING arbitration (second upgrade rejected when first live) | upgrade | entry unchanged | TC16, TC37 | Direct (TC16 asserts upgrade-path evidence for serialized upgrades) | Only one UPGRADE_PENDING per line |

### 1.5 Writeback Path (`processWriteback`)

| # | Edge | Category | DirEntry Δ | Covering TC | Coverage Type | Notes |
|---|------|----------|-----------|-------------|---------------|-------|
| W1 | G_M → Writeback (keepAsClean=true) → G_E | writeback | state=G_E, sharersMask=1<<owner, residentDirty=true | TC7, TC26 | Direct (TC7 asserts 0x55667788 after writeback+read) | Owner retains clean exclusive |
| W2 | G_M → Writeback (keepAsClean=false) → G_I | writeback | state=G_I, sharersMask=0, residentDirty=true | TC7, TC17, TC33 | Direct (TC33 asserts cross-socket writeback reached home socket 0) | Owner drops line |
| W3 | G_E/G_M → HomeWritebackComplete → G_I (notifyHomeWritebackComplete) | writeback | state=G_I, sharersMask=0 | TC19, TC22, TC26 | Indirect (backstore pressure; TC19 asserts dirty persist) | HN-F→EP-SNF→DRAM writeback completion |
| W4 | Cross-socket writeback routing | writeback | state=G_I, homeSocket routing | TC33 | Direct (TC33 asserts [TC33_WB] homeSocket=0) | Dual-socket writeback path |

### 1.6 Evict Path (`processEvict`)

| # | Edge | Category | DirEntry Δ | Covering TC | Coverage Type | Notes |
|---|------|----------|-----------|-------------|---------------|-------|
| E1 | G_E (clean owner) → Evict → G_I | evict | state=G_I, sharersMask=0 | TC7, TC22, TC26 | Indirect (TC22: capacity pressure evicts lines) | Clean owner eviction |
| E2 | G_S → Evict (sharer removed) → G_S/G_I | evict | sharersMask &= ~node; if empty→G_I | TC22, TC26 | Indirect (TC22: 3072 lines create sharer eviction) | Sharer-only eviction |
| E3 | Dirty owner evict → REJECTED | evict | entry unchanged | TC22 | Indirect (dirty lines must writeback first) | processEvict rejects G_M eviction |
| E4 | Evict: node neither owner nor sharer → REJECTED | evict | entry unchanged | TC22 | Indirect (negative test embedded) | Stale evict rejection |

### 1.7 Backstore Fill/Evict Path

| # | Edge | Category | DirEntry Δ | Covering TC | Coverage Type | Notes |
|---|------|----------|-----------|-------------|---------------|-------|
| B1 | handleResidentMiss → bloom MayContain → setFillPending → issueBackstoreRead | backstore | placeholder G_I inserted, fillPending=true | TC18, TC19, TC23, TC28 | Direct (TC18 asserts fill/replay value 0x18181818) | Bloom filter positive → backstore read needed |
| B2 | onBackstoreFillComplete → state restored → replayResidentWaiters | backstore | state/sharers/epoch from backstore; fillPending=false | TC18, TC19, TC23, TC28, TC45 | Direct (TC23 asserts first miss=0, refill=0x23ABCDEF) | Replay queued waiters after fill |
| B3 | evictOneVictim → dirty victim → setWbPending → scheduleBackstoreWrite | backstore | victim residentDirty=true → wbPending | TC19, TC22, TC28 | Direct (TC19 asserts 0xABCD1234 survives dirty evict) | Dirty metadata write to backstore |
| B4 | evictOneVictim → clean victim → forceRemove | backstore | entry removed from ResidentDir | TC22 | Indirect (capacity pressure cleanup) | Clean victim immediate removal |
| B5 | onBackstoreWriteAck → wbPending=false → forceRemove if evictionPendingRemoval | backstore | entry removed after writeback ack | TC19, TC22, TC28 | Indirect (backstore metadata consistency) | Eviction completion |
| B6 | Bloom false positive → handleResidentMiss → fill pending → backstore read → not found → G_I | backstore | G_I insertion, fill pending, backstore miss → G_I | TC23 | Direct (TC23 asserts first read = 0, refill back to MAGIC) | False positive fallback path |

### 1.8 Tombstone Replay Path

| # | Edge | Category | DirEntry Δ | Covering TC | Coverage Type | Notes |
|---|------|----------|-----------|-------------|---------------|-------|
| T1 | Duplicate Clear → tombstone hit → return idempotent accepted | tombstone | entry unchanged | TC5, TC25, TC30, TC38, TC47 | Direct (TC25/TC30 assert stale_clear/replay markers) | Idempotent replay within window W |
| T2 | Duplicate Clear → tombstone cleaned up (expired) → rejected | tombstone | entry unchanged | TC27, TC30 | Indirect (long-running test with tombstone expiry) | Window W expiration |
| T3 | processOuterRequest → tombstone hit → idempotent grant | tombstone | entry unchanged | TC5, TC25 | Indirect (repeated read after commit) | Conservative grant for duplicated requests |
| T4 | Stale tombstone → processClear with wrong epoch → rejected | tombstone | entry unchanged | TC27, TC30 | Direct (TC30 asserts stale=1 replay=1 marker) | epoch mismatch rejection |

### 1.9 Epoch / Stale Protection Path

| # | Edge | Category | DirEntry Δ | Covering TC | Coverage Type | Notes |
|---|------|----------|-----------|-------------|---------------|-------|
| S1 | isNewerEpoch: committed > response → REJECT | epoch | entry unchanged; staleRejectedCount++ | TC27, TC30, TC38 | Direct (TC27 asserts epoch wrap marker) | Half-range comparison |
| S2 | Epoch wrap-around (24-bit) → wrap marker emitted | epoch | entry epoch wraps through 0 | TC27, TC42 | Direct (TC42 asserts ffffff,0 boundary) | 24b epoch wrap stress |
| S3 | allocateReservedEpoch = commitEpoch + 1 (wrapping) | epoch | reservedEpoch computed, committed unchanged | TC27, TC42 | Indirect (wrapping correctness via protocol stability) | Reserve-before-commit invariant |

### 1.10 Pending Queue / Replay Path

| # | Edge | Category | DirEntry Δ | Covering TC | Coverage Type | Notes |
|---|------|----------|-----------|-------------|---------------|-------|
| P1 | Existing outstanding + same requester → BUSY | queue | entry unchanged | TC5, TC15, TC24 | Indirect (stress retry path) | No self-queue |
| P2 | Existing outstanding + different requester → enqueue PendingRequester | queue | entry unchanged | TC4, TC14, TC15, TC24 | Indirect (pending queue builds depth) | Queue depth ≤ MAX_PENDING_PER_PA |
| P3 | Clear/UpgradeDone → replayPendingRequesters (chained replay) | queue | entry unchanged; replay triggers new outstanding | TC14, TC15, TC25 | Indirect (serialization under pressure) | recall_done_fix §5 |
| P4 | replayResidentWaiters → replay queued backstore waiters | queue | entry may change via replayed request | TC18, TC23 | Direct (TC18 asserts value after fill+replay) | Waiters unblocked after fill/WB complete |

### 1.11 Fault Injection Paths

| # | Edge | Category | DirEntry Δ | Covering TC | Coverage Type | Notes |
|---|------|----------|-----------|-------------|---------------|-------|
| F1 | Dropped Clear → automatic retry → tombstone replay | fault | same as T1 | TC47 | Direct (TC47 asserts fault evidence + convergence) | Drop-Clear fault injection |
| F2 | Duplicate InvalidateAck → idempotent handling | fault | entry unchanged | TC48 | Direct (TC48 asserts idempotent ack handling) | Dup-InvAck fault injection |
| F3 | Reordered acks → eventual convergence | fault | entry unchanged | TC49 | Direct (TC49 asserts convergence under perturbation) | Ack reordering fault |
| F4 | Non-DSM access → fatal/panic | fault | N/A | TC9 | Direct (TC9 asserts [FATAL] or page fault) | Negative test |

---

## 2. Coverage Summary by Edge Category

| Category | Total Edges | Covered | Uncovered | Coverage % | Notes |
|----------|------------|---------|-----------|-----------|-------|
| RECALL | 6 | 6 | 0 | 100% | All edges mapped |
| INVALIDATE | 5 | 5 | 0 | 100% | All edges mapped |
| GRANT_HANDSHAKE | 9 | 9 | 0 | 100% | Universal commit edge G7 covered by every value-asserting TC |
| UPGRADE_PENDING | 7 | 7 | 0 | 100% | All edges mapped |
| Writeback | 4 | 4 | 0 | 100% | All edges mapped |
| Evict | 4 | 4 | 0 | 100% | All edges mapped |
| Backstore Fill/Evict | 6 | 6 | 0 | 100% | All edges mapped |
| Tombstone Replay | 4 | 4 | 0 | 100% | All edges mapped |
| Epoch/Stale Protection | 3 | 3 | 0 | 100% | All edges mapped |
| Pending Queue/Replay | 4 | 4 | 0 | 100% | All edges mapped |
| Fault Injection | 4 | 4 | 0 | 100% | All edges mapped |
| **Total** | **56** | **56** | **0** | **100%** | |

---

## 3. Minimum Required TC Check (per task spec)

| TC | Name | Edge(s) Covered | Coverage Type | Verdict |
|----|------|----------------|---------------|---------|
| TC5 | single_writer | R1, R3, G3, G7, G9, T1, T3, P1 | Direct (final value convergence) | ✅ COVERED |
| TC6 | multi_sharer | R1, R2, G1, G4, G7 | Direct (Node1/Node2 both read 0xDEADBEEF) | ✅ COVERED |
| TC8 | upgrade_invalidate | I1, I2, I3, I4, U2, U4, U5, U6 | Direct (asserts Node1 reads 0xBBB after inv) | ✅ COVERED |
| TC10 | concurrent_atomic | G7, R1, R2 (indirect), P1, P2 | Direct (no torn reads in legal range) | ✅ COVERED |
| TC11 | local_upgrade | G1, G5, U1, U6, G7 | Direct (asserts snoop chain: Node B→C→A values) | ✅ COVERED |
| TC15 | credit_storm | R2, R4, I2 (indirect), P1, P2, P3 | Direct (no deadlock; convergence check) | ✅ COVERED |
| TC16 | dual_upgrade_race | U2, U4, U5, U6, U7 | Direct (upgrade-path evidence + convergence) | ✅ COVERED |
| TC17 | writeback_dma | R1, R6, W2, B1 (indirect) | Direct (pre-DMA=0x12345678, post-DMA=0x87654321) | ✅ COVERED |
| TC18 | directory_fill_replay | G1, G7, B1, B2, P4 | Direct (asserts 0x18181818 on fill/replay) | ✅ COVERED |
| TC19 | directory_dirty_persist | G3, G7, B1, B2, B3, B5, W3 | Direct (asserts 0xABCD1234 after dirty persist) | ✅ COVERED |
| TC22 | resident_capacity_pressure | W3, B3, B4, B5, E1, E2, E3, E4 | Direct (≥9 probe reads, all MATCH) | ✅ COVERED |

---

## 4. Priority-Ranked Uncovered Edges

All 56 identified edges have at least one covering TC. The following represent **partial coverage** areas where risk remains higher:

| Priority | Edge ID | Edge Description | Risk | Gap | Suggested TC |
|----------|---------|-----------------|------|-----|-------------|
| P0 | — | **Concurrent RECALL + INVALIDATE on same line** (TC41 tests overlay but not deep interleaving) | RECALL.WAITING_TARGET_RESP + second requester triggers INVALIDATE → opType collision | No TC specifically asserts RECALL and INVALIDATE outstanding simultaneously for the same PA at different stage progressions | TC55: `recall_inv_deep_interleave` — owner holds line while 2nd node triggers invalidation-waiting grant |
| P0 | Epoch half-range | **Wrap exactly at half-range boundary** (isNewerEpoch with delta = half_range - 1) | Ambiguous comparison when epochs differ by exactly half the range | TC27 stresses 24b wrap but does not assert boundary behavior at exact 2^(bits-1) | TC56: `epoch_half_range_edge` — force epoch to exactly half-range delta |
| P1 | P2 queue | **MAX_PENDING_PER_PA saturation** (queue full → drop) | Deadlock potential if all requesters retry simultaneously | No TC explicitly fills the pending queue to capacity and asserts drop behavior | TC57: `pending_queue_saturation` — N+1 requesters for same line, verify last is dropped not stuck |
| P1 | R4 | **RECALL.DONE + different requester REQUEST → enqueue, then Clear → replay** | The chain: existing RECALL.DONE for requester A → B's request enqueued → A's Clear commits → replay B | Covered indirectly by TC4/TC15, but no single TC asserts the exact RECALL.DONE→enqueue→Clear→replay sequence with markers | TC58: `recall_done_pending_replay` — A holds, B requests (enqueued), A's recall completes but not consumed by B, A's Clear commits, B replayed |
| P1 | I5 | **Stale InvalidateAck after epoch wrap** | Invalidation ack arrives with epoch that wrapped around → must be rejected | TC27 wraps epoch during ownership churn but does not specifically inject stale InvalidateAck | TC59: `stale_invack_after_wrap` — force epoch wrap, then inject InvalidateAck with pre-wrap epoch |
| P2 | W1/W2 | **Writeback with backstore pending (dirty+bloom conflict)** | Writeback arrives while another fill is pending for same line → race | No TC specifically writes back a dirty line while backstore fill is in progress for same PA | TC60: `writeback_fill_race` — trigger backstore miss+fill for line A, then writeback A before fill completes |
| P2 | T2 | **Tombstone expiry exactly at Clear arrival** | Clear arrives at tick == tombstone.expireTick | No TC validates boundary behavior of tombstone cleanup at exact expiry | TC61: `tombstone_expiry_edge` — create tombstone, delay Clear until expiry tick, verify rejection |
| P2 | S3 | **allocateReservedEpoch double-wrap (64-bit epoch overflow)** | Reserved epoch computation (epoch+1) could overflow at 2^64-1 | Only 24b wrap tested (TC27, TC42); 64-bit overflow path not exercised | TC62: `epoch_64b_wrap_overflow` — requires 64-bit epoch config |

---

## 5. Edge Coverage by OpType (verification_plan.md §5.4 requirement)

| OpType | Required (plan) | Actual Coverage | Vertex TC |
|--------|----------------|----------------|-----------|
| RECALL | ✅ | TC2, TC3, TC4, TC5, TC6, TC15, TC17, TC40, TC41, TC43, TC44, TC46 | TC2 (baseline remote read recall) |
| INVALIDATE | ✅ | TC8, TC25, TC41, TC44, TC48 | TC8 (upgrade + invalidate sharers) |
| GRANT_HANDSHAKE | ✅ | TC1–TC8, TC10–TC44 (universal) | TC1 (first miss grant) |
| UPGRADE_PENDING | ✅ | TC8, TC11, TC16, TC29, TC36, TC37, TC44 | TC11 (local upgrade snoop chain) |

**All four OpTypes are covered.** P0 TC requirements per plan:
- ✅ TC1 (G_I→GRANT_HANDSHAKE→Clear→G_M/G_E)
- ✅ TC2 (G_M→RECALL→GRANT_SHARED→G_S)
- ✅ TC8 (G_S→UPGRADE_PENDING→G_M + SnpCleanInvalid defer)
- ✅ TC11 (shared_hint registration → local upgrade snoop chain)
- ✅ TC16 (concurrent UPGRADE_PENDING arbitration)
- ✅ TC18 (ResidentDir fill/backstore replay)
- ✅ TC19 (dirty committed metadata persist)
- ✅ TC22 (ResidentDir capacity pressure / victim selection)
- ✅ TC23 (bloom false positive fallback)
- ✅ TC25 (INVALIDATE→Clear→tombstone replay cycling)
- ✅ TC27 (epoch wrap + stale reject)
- ✅ TC28 (backstore metadata consistency)

---

## 6. Key Findings

1. **All 56 mapped edges have at least one covering TC** — the current test suite provides baseline coverage for every protocol path in UBCCController.cc.

2. **The universal commit edge (G7:** `GRANT_HANDSHAKE WAITING_CLEAR → Clear → commitIntendedResult`) is the most exercised edge, hit by every TC that asserts a final read value.

3. **Fault injection TCs (TC47-TC49)** specifically target edge cases that normal functional tests miss:
   - TC47 (dropped Clear) → exercises tombstone replay without Clear
   - TC48 (duplicate InvalidateAck) → exercises idempotent ack tracking
   - TC49 (reordered acks) → exercises eventual convergence

4. **8 partially-covered edge areas** identified (Section 4) — none are completely uncovered, but the listed scenarios represent deeper interleaving or boundary conditions not explicitly targeted.

5. **Graceful degradation paths** (pending queue full drop, stale epoch reject after wrap, tombstone expiry boundary) rely on implicit stress testing rather than direct assertion — these are the highest-ROI targets for new test cases.
