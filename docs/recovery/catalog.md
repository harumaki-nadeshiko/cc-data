# Document A: Complete Modification Catalog

## Overview
- **Total files tracked**: 39
- **Total edit operations across all files**: ~800+
- **Development chunks (sessions)**: 0–11 (12 total)
- **Experimental patterns**: 7 (all reverted or superseded)
- **Final (current) changes**: 39 files

---

## Phase Breakdown

### Pre-Phase: Q2 — Cross-Node Invalidation + TBE Debug (Chunk 0)

**Goal**: Diagnose and fix TC3 pingpong test failure; find root cause of cross-node cache invalidation not working.

#### Modified Files:

| File | Edits | Intent Evolution | Final Status | Experimental? |
|------|-------|-----------------|--------------|---------------|
| `scripts/q2_regression.sh` | 1 | General: Regression runner script | **FINAL** (current) | No |
| `configs/ruby/CHI_ubcc_framework.py` | 35 total (Chunk 0) | TBE/Reservation → Testing/Debug → General → ... | Superseded by Chunk 11 | No |
| `src/mem/ruby/protocol/chi/ep/EPBackend.cc` | 136 total (Chunk 0) | EPBackend → CHI Protocol (SLICC) → Cross-Node Coordination → ... | Superseded by Chunk 11 | No |
| `src/mem/ruby/protocol/chi/ep/EPRNFController.cc` | 112 total (Chunk 0) | CHI Protocol (SLICC) → Testing/Debug → ... | Superseded by Chunk 11 | No |
| `src/mem/ruby/protocol/chi/ep/EPRNFController.hh` | 49 total (Chunk 0) | CHI Protocol (SLICC) → Testing/Debug → ... | Superseded by later chunks | No |
| `src/mem/ruby/protocol/chi/ep/EPSNFController.cc` | 30 total (Chunk 0) | EP-SNF Controller → EPBackend → ... | Superseded by Chunk 11 | No |

#### Key Decision Dependencies:
- **Decision**: EP-RNF must wrap external requests into standard CHI Requests forwarded to HN-F (not bypass HN-F)
- **Decision**: Cross-node invalidation path goes through: EP-RNF → HN-F request → HN-F generates Snoop → CPU RN-F
- **Known trap**: `sendLocalSnoop()` approach was wrong — EP-RNF is an RN-F, not an HN-F; snoops originate from HN-F

#### Final Correct State (Q2 baseline):
- User mandate: EP-RNF wraps external requests into CHI Requests sent to local HN-F
- HN-F uses native state machine to generate snoops
- Node isolation maintained through physical address partitioning

---

### Phase: Q3 — CHI Request-Based Snoop + SC_RSC Timing (Chunk 1)

**Goal**: Fix SC_RSC crash when CompAck arrives during SnpSharedFwd processing. Implement proper CHI request-based invalidation.

#### Modified Files:

| File | Edits | Intent Evolution | Final Status | Experimental? |
|------|-------|-----------------|--------------|---------------|
| `src/mem/ruby/protocol/chi/CHI-cache-actions.sm` | 124 total (Chunk 1) | CHI Protocol (SLICC) | Superseded by Chunk 11 | No |
| `src/mem/ruby/protocol/chi/CHI-cache-ports.sm` | 1 (Chunk 1 ONLY) | General: DPRINTF in RSPIN port | **FINAL** (current) | No |
| `src/mem/ruby/protocol/chi/CHI-cache-transitions.sm` | 15 total (Chunk 1) | CHI Protocol (SLICC): CompAck transition for composite states | Superseded by Chunk 11 | No |
| `src/mem/ruby/protocol/chi/ep/EPRNFController.py` | 4 total (Chunk 1) | General: EPRNFController SimObject params | Superseded by Chunk 2 | No |
| `src/mem/ruby/protocol/chi/ep/UBCCController.cc` | 139 total (Chunk 1) | CHI Protocol (SLICC) → UBCC Controller → ... | Superseded by Chunk 11 | No |
| `src/mem/ruby/protocol/chi/ep/UBCCController.hh` | 36 total (Chunk 1) | EPBackend | Superseded by Chunk 11 | No |

#### Key Decision Dependencies:
- **Decision**: DISABLE_DMT (Disable Direct Memory Transfer) for cross-node reads to prevent SC_RSC crash
- **Decision**: DCT fallback for ReadShared initiated by EP-RNF — if DCT fails, HN-F falls back to DMT-disabled path
- **Decision**: `ReadOnce` approach REJECTED by user — must use `ReadShared` sent to HN-F
- **Decision**: Need to distinguish EP-RNF from normal CPU RNF in HN-F logic

#### Final Correct State (Q3):
- EP-RNF sends ReadShared to HN-F for recall (NOT ReadOnce, NOT sendLocalSnoop)
- DCT disabled for EP-RNF-initiated requests
- CompAck transition added: `({SC_RSC, UD_RU, UC_RU, RSC, RUSC, SD_RSC, UD_RSC, UD_RSD, SD_RSD}, CompAck) → Pop_RespInQueue`

---

### Phase 1: Deadlock Analysis + SLICC Fixes (Chunk 2)

**Goal**: Analyze and fix deadlock scenarios in cross-node request patterns. Design OutstandingRequest-based serialization.

#### Modified Files:

| File | Edits | Intent Evolution | Final Status | Experimental? |
|------|-------|-----------------|--------------|---------------|
| `configs/ruby/CHI_ubcc_framework.py` | 35 total (Chunk 2) | General → TBE/Reservation → Testing/Debug → ... | Superseded | No |
| `src/mem/ruby/protocol/chi/CHI-cache-actions.sm` | 124 total (Chunk 2) | CHI Protocol (SLICC) | Superseded | No |
| `src/mem/ruby/protocol/chi/CHI-cache-funcs.sm` | 15 total (Chunk 2) | General | Superseded by Chunk 11 | No |
| `src/mem/ruby/protocol/chi/CHI-cache-transitions.sm` | 15 total (Chunk 2) | CHI Protocol (SLICC) | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPBackend.cc` | 136 total (Chunk 2) | Cross-Node Coordination → General → EPBackend → ... | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPBackend.hh` | 20 total (Chunk 2) | General | Superseded by Chunk 11 | No |
| `src/mem/ruby/protocol/chi/ep/EPRNFController.cc` | 112 total (Chunk 2) | CHI Protocol (SLICC) | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPRNFController.py` | 4 total (Chunk 2) | EP-RNF Controller: EPRNFController SimObject params | **FINAL** (current) | No |
| `src/mem/ruby/protocol/chi/ep/EPSNFController.cc` | 30 total (Chunk 2) | General → CHI Protocol (SLICC) → ... | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPSNFController.hh` | 3 total (Chunk 2) | EP-SNF Controller: RetryEntry + retry queue | Superseded by Chunk 3 | No |
| `src/mem/ruby/protocol/chi/ep/UBCCController.cc` | 139 total (Chunk 2) | CHI Protocol (SLICC) → UBCC Controller → ... | Superseded | No |
| `src/mem/ruby/structures/TBEStorage.hh` | 46 total (Chunk 2) | TBE/Reservation | Superseded by Chunk 4 | No |

#### Key Decision Dependencies:
- **Decision**: UBCC must serialize same-cacheline requests (pendingOp/OutstandingRequest) to prevent race conditions
- **Decision**: EP modules must remain separate from internal CHI objects — different gem5 instances in final system
- **Decision**: UBCC-UBCC interconnect modeled as CXL-like high-speed link (hundreds of ns to few ms latency)
- **Decision**: Need DirEntry/OutstandingRequest decoupling for correctness

#### Design Flow Evolution:
1. Initial: DirEntry mixes persistent directory state + transient request state
2. User feedback: must decouple "global directory state" from "ongoing request buffer"
3. Final: OutstandingRequest struct added alongside DirEntry

---

### Phase 1-2: DirEntry/OutstandingRequest Decoupling (Chunk 3)

**Goal**: Decouple UBCC persistent directory state (DirEntry) from transient request tracking (OutstandingRequest).

#### Modified Files:

| File | Edits | Intent Evolution | Final Status | Experimental? |
|------|-------|-----------------|--------------|---------------|
| `build/ARM/mem/ruby/protocol/CHI/Cache_Controller.cc` | 9 total (Chunk 3) | Testing/Debug | Superseded by Chunk 4 | No |
| `configs/ruby/CHI_ubcc_framework.py` | 35 total (Chunk 3) | CHI Protocol (SLICC) | Superseded | No |
| `src/mem/ruby/protocol/chi/CHI-cache-actions.sm` | 124 total (Chunk 3) | General | Superseded | No |
| `src/mem/ruby/protocol/chi/CHI-cache-funcs.sm` | 15 total (Chunk 3) | CHI Protocol (SLICC) | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPBackend.cc` | 136 total (Chunk 3) | Cross-Node Coordination → EPBackend → ... | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPBackend.hh` | 20 total (Chunk 3) | General | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPSNFController.cc` | 30 total (Chunk 3) | CHI Protocol (SLICC) | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPSNFController.hh` | 3 total (Chunk 3) | General: RetryEntry struct finalized | **FINAL** (current) | No |
| `src/mem/ruby/protocol/chi/ep/UBCCController.cc` | 139 total (Chunk 3) | UBCC Controller → Testing/Debug → Cross-Node Coordination → ... | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/UBCCController.hh` | 36 total (Chunk 3) | UBCC Controller | Superseded by Chunk 11 | No |
| `src/mem/ruby/structures/TBEStorage.cc` | 10 total (Chunk 3) | General | Superseded by Chunk 4 | No |
| `src/mem/ruby/structures/TBEStorage.hh` | 46 total (Chunk 3) | Testing/Debug (debug prints) | Superseded by Chunk 4 | No |

#### Key Decision Dependencies:
- **Decision**: OutstandingRequest holds transient states: OpState (WAITING_RESP, RESP_RCVD, CANCELLED), OpType (RECALL, INVALIDATE, GRANT_HANDSHAKE)
- **Decision**: P0: `TBEStorage::decrementReserved()` underflow workaround temporarily relaxed until root cause found
- **Decision**: P1: `populateGrantData` "first word ≠ 0" heuristic is wrong — needs true cross-node data fetch
- **Decision**: Local write (SC→UD) must notify home UBCC via SnpCleanInvalid→EP-RNF→updateOwner chain
- **Decision**: EP-SNF→UBCC grant path already handles I→UD/UC/SC owner notification
- **Decision**: Need latency modeling between UBCC nodes (0-cycle recall causes TBE race with reqIn/datIn ports)

#### Known Traps:
- **TBEStorage assertion**: `decrementReserved()` underflow from SLICC auto-generated double-decrement
- **0-cycle recall**: HN-F sends request to EP-SNF → EP-SNF returns in same tick → TBE not yet allocated
- **populateGrantData**: uses `phys_mem->functionalAccess()` which may return stale data; uses `first_word != 0` heuristic

#### Oscillating Patterns:
- `deadlock_threshold` oscillated between `"10ms"` (string) and `20000000` (integer) multiple times
- `pendingOp transition timer` adjusted: 1M → 2M → 5M → removed → reinstated cycles

---

### Phase 2-3: EP-RNF Sharer Registration Design (Chunk 4)

**Goal**: Design and implement EP-RNF registration as a proper HN-F sharer, enabling local write-upgrade snoop path to notify home UBCC.

#### Modified Files:

| File | Edits | Intent Evolution | Final Status | Experimental? |
|------|-------|-----------------|--------------|---------------|
| `build/ARM/mem/ruby/protocol/CHI/Cache_Controller.cc` | 9 total (Chunk 4) | General (includes headers) | **FINAL** (current) | No |
| `configs/ruby/CHI_ubcc_framework.py` | 35 total (Chunk 4) | General | Superseded | No |
| `src/mem/ruby/protocol/chi/CHI-cache.sm` | 6 total (Chunk 4) | General: epRnfMachineVersion parameter | Superseded by Chunk 11 | No |
| `src/mem/ruby/protocol/chi/CHI-cache-funcs.sm` | 15 total (Chunk 4) | CHI Protocol (SLICC): pickSharerForSnoop | Superseded by Chunk 11 | No |
| `src/mem/ruby/protocol/chi/ep/EPBackend.cc` | 136 total (Chunk 4) | General | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPBackend.hh` | 20 total (Chunk 4) | UBCC Controller | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPBackend.py` | 3 total (Chunk 4) | General | Superseded by Chunk 7 | No |
| `src/mem/ruby/protocol/chi/ep/EPController.py` | 3 (Chunk 4 ONLY) | EPBackend: EP Controller base SimObject | **FINAL** (current) | No |
| `src/mem/ruby/protocol/chi/ep/EPSNFController.cc` | 30 total (Chunk 4) | TBE/Reservation | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/M6SelfTest.cc` | 4 total (Chunk 4) | General: processRecallResponse stub | Superseded by Chunk 11 | No |
| `src/mem/ruby/protocol/chi/ep/M7SelfTest.cc` | 1 (Chunk 4 ONLY) | General: DirEntry sizeof check | **FINAL** (current) | No |
| `src/mem/ruby/protocol/chi/ep/UBCCController.cc` | 139 total (Chunk 4) | Testing/Debug | Superseded | No |
| `src/mem/ruby/structures/TBEStorage.cc` | 10 total (Chunk 4) | Testing/Debug: decrementReserved relaxed assert with note | **FINAL** (current) | No |
| `src/mem/ruby/structures/TBEStorage.hh` | 46 total (Chunk 4) | TBE/Reservation: inline functions | **FINAL** (current) | No |
| `tests/ubcc/ep-rnf/test_phase0_machineid.py` | 32 total (Chunk 4) | EPBackend: MachineID verification test | Superseded by Chunk 5 | No |
| `src/mem/ruby/protocol/chi/ep/EPRNFController.cc` | 112 total (Chunk 4) | General → EP-RNF snoop handling | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPRNFController.hh` | 49 total (Chunk 4) | CHI Protocol (SLICC) → EP-RNF Controller → ... | Superseded | No |

#### Key Decision Dependencies:
- **Decision**: EP-RNF registered as sharer in HN-F dir_sharers during First Miss CompData (no extra CHI request)
- **Decision**: `shared_hint=true` flag added to CHIDataMsg for HN-F to know to register EP-RNF
- **Decision**: `pickSharerForSnoop()` function selects snoop target with priority: L2 RN-F first, EP-RNF only when sole sharer
- **Decision**: EP-RNF treated as standard CHI RN-F (Same MachineType, same sharer semantics, same snoop response contract)
- **Decision**: On SnpUnique → globalInvalidate with dirty data collection; on SnpCleanInvalid → no remote data needed
- **Decision**: Exclude EP-RNF from Fwd-snoop paths permanently (no SnpSharedFwd, SnpUniqueFwd)

#### Experiment vs Final:
- **`sendLocalSnoop` → CHI Request-Based Snoop**: ORIGINAL `sendLocalSnoop()` bypassed HN-F. REVERTED. Final: ReadShared/CleanUnique sent to HN-F.
- **`ReadOnce` → `ReadShared` recall**: ORIGINAL used ReadOnce. User REJECTED. Final: ReadShared sent to HN-F.
- **`CompAck destination`**: Experimented with responder-based, HN-F MachineID-based. Final: `mapAddressToDownstreamMachine` matching standard RN-F.

#### Design Flow Evolution:
1. Initial plan v1.0: shared_hint on CHIRequestMsg (wrong — needs to be on CHIDataMsg response)
2. Plan v2.0: shared_hint on CHIDataMsg, EP-RNF SnpShared→remoteFetch path (had HN-F semantic conflict)
3. Plan v3.0: Removed SnpShared→remoteFetch, added Fwd guard to permanently exclude EP-RNF, added DCT fallback
4. Plan v3.1: Fixed SnpUnique response types, added 4th single-target selection point
5. Plan v3.2: Fixed DCT fallback destination, aligned SnpOnce→fallback to ReadOnce semantics

---

### Phase 0: MachineID Injection & Snoop Connectivity Verification (Chunk 5)

**Goal**: Verify EP-RNF MachineID is valid, NoC-reachable, and perceived by HN-F. Test infrastructure hardening.

#### Modified Files:

| File | Edits | Intent Evolution | Final Status | Experimental? |
|------|-------|-----------------|--------------|---------------|
| `src/mem/ruby/protocol/chi/CHI-cache-actions.sm` | 124 total (Chunk 5) | CHI Protocol (SLICC): RegisterEPRNF_OnSharedHint action | Superseded | No |
| `src/mem/ruby/protocol/chi/CHI-cache-funcs.sm` | 15 total (Chunk 5) | General: pickSharerForSnoop impl + initializeTBE | Superseded | No |
| `src/mem/ruby/protocol/chi/CHI-cache-transitions.sm` | 15 total (Chunk 5) | CHI Protocol (SLICC) | Superseded | No |
| `src/mem/ruby/protocol/chi/CHI-msg.sm` | 2 total (Chunk 5) | General: CHIDataMsg shared_hint field | Superseded by Chunk 11 | No |
| `src/mem/ruby/protocol/chi/ep/EPBackend.cc` | 136 total (Chunk 5) | EPBackend: setRegistrationDone call | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPBackend.hh` | 20 total (Chunk 5) | EPBackend: getEpRnfMachineID, setEpRnfController | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPRNFController.cc` | 112 total (Chunk 5) | CHI Protocol (SLICC): recvSnoopMsg rewrite, PendingSnoopTxn | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPRNFController.hh` | 49 total (Chunk 5) | EP-RNF Controller: RegState, RegistrationContext, PendingSnoopTxn, retry struct | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/UBCCController.cc` | 139 total (Chunk 5) | UBCC Controller | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/UBCCController.hh` | 36 total (Chunk 5) | UBCC Controller | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPSNFController.cc` | 30 total (Chunk 5) | CHI Protocol (SLICC): shared_hint propagation | Superseded | No |
| `tests/ubcc/ep-rnf/test_phase0_machineid.py` | 32 total (Chunk 5) | General: MachineID verification final version (v4+) | **FINAL** (current) | No |
| `tests/ubcc/ep-rnf/test_phase1_tc3.py` | 2 (Chunk 5 ONLY) | Testing/Debug: TC3 shared_hint chain verification | **FINAL** (current) | No |

#### Key Decision Dependencies:
- **Decision**: Phase 0 test hardened over 5+ iterations (v1→v5) to be strict verification
- **Decision**: E-01 (MachineID validation): SKIP on SWIG failure, FAIL if wrong, PASS if correct
- **Decision**: E-02 (structural verification): verify 3-layer chain: Python version injection → SLICC build → C++ TBE
- **Decision**: E-03 (framework config): parse actual `deadlock_threshold` from framework, no fallback

#### Known Traps:
- Phase 0 test had "fails silently passes" bug in v1-v3: fallback to PASS when `getEpRnfMachineID()` fails
- `getEpRnfMachineID()` may not be SWIG-accessible in gem5 v25.1
- E-03 initially had generic regex fallback → removed to be strict

---

### Phase 3: UBCC Stub → Real Implementation (Chunk 6)

**Goal**: Replace Phase 2 UBCC stub (globalInvalidate, remoteFetch, updateOwner) with real implementations using OutstandingRequest and directory state.

#### Modified Files:

| File | Edits | Intent Evolution | Final Status | Experimental? |
|------|-------|-----------------|--------------|---------------|
| `configs/ruby/CHI_config.py` | 2 total (Chunk 6) | General: epRnfMachineVersion=-1 | Superseded by Chunk 11 | No |
| `configs/ruby/CHI_ubcc_framework.py` | 35 total (Chunk 6) | Testing/Debug → UBCC Controller → EPBackend → ... | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPBackend.cc` | 136 total (Chunk 6) | EPBackend: grant-related paths | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPBackend.hh` | 20 total (Chunk 6) | General: pendingOwnerUpdate + clear | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPRNFController.cc` | 112 total (Chunk 6) | CHI Protocol (SLICC): DeferredBarrierClears, sendOrRetry | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPRNFController.hh` | 49 total (Chunk 6) | General: QueuedImmediateResponse, needBarrierClear | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/UBCCController.cc` | 139 total (Chunk 6) | Cross-Node Coordination: globalInvalidate real impl, updateOwner, pendingOwnerUpdate | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/UBCCController.hh` | 36 total (Chunk 6) | UBCC Controller: MESIState::UD, DirEntry.pendingOwnerUpdate | Superseded | No |

#### Key Decision Dependencies:
- **Decision**: `globalInvalidate()` uses UBCC directory `ownerNode` + `dirty` flag to determine if dirty data exists
- **Decision**: `updateOwner()` sets `pendingOwnerUpdate=true` → cleared by EPBackend after HN-F+EP-RNF registration
- **Decision**: `populateGrantData` must fetch data from remote owner through UBCC, not `phys_mem->functionalAccess()`
- **Decision**: materializedData removed from DirEntry → data goes through OutstandingRequest.dataBuffer temporarily
- **Decision**: `grantHandshakeComplete` timing fix: moved from EPBackend pre-release to CompAck path

#### Known Traps:
- GRANT_HANDSHAKE OutstandingRequest permanent residue causes TC2 deadlock
- `hasData=true` + zero-fill path deleted — only true materialized data marks `hasData=true`
- Home-node check added for clearPendingOwnerUpdate (only home UBCC should clear)

---

### Phase 4: Build/Test/Regression (Chunk 7)

**Goal**: Clean build, full regression, fix TC failures. Self-test/workload separation.

#### Modified Files:

| File | Edits | Intent Evolution | Final Status | Experimental? |
|------|-------|-----------------|--------------|---------------|
| `configs/ruby/CHI_ubcc_framework.py` | 35 total (Chunk 7) | UBCC Controller: deadlock_threshold = 20000000 | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPBackend.cc` | 136 total (Chunk 7) | EPBackend: grantHandshakeComplete, populateGrantData rewrite | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPBackend.hh` | 20 total (Chunk 7) | EPBackend: enableSelfTest flag | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPBackend.py` | 3 total (Chunk 7) | EPBackend: enable_self_test Param | **FINAL** (current) | No |
| `src/mem/ruby/protocol/chi/ep/EPSNFController.cc` | 30 total (Chunk 7) | CHI Protocol (SLICC): shared_hint propagation complete | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/M6SelfTest.cc` | 4 total (Chunk 7) | General: M6 self-test update | Superseded by Chunk 11 | No |
| `src/mem/ruby/protocol/chi/ep/UBCCController.cc` | 139 total (Chunk 7) | UBCC Controller: GRANT_HANDSHAKE fix, OutstandingRequest dataBuffer | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/UBCCController.hh` | 36 total (Chunk 7) | UBCC Controller: dataBuffer removal, OutstandingRequest refactor | Superseded | No |
| `tests/e2e/run_all_e2e.sh` | 3 (Chunk 7 ONLY) | General: TC number regex for 1-11 | **FINAL** (current) | No |
| `tests/e2e/test_e2e.py` | 8 total (Chunk 7) | General: self-test disable + E2E config | Superseded by Chunk 11 | No |
| `tests/e2e/workloads/e2e_tc_local_upgrade.c` | 1 (Chunk 7 ONLY) | Testing/Debug: sync_wait + dsm_load in Phase 1 | **FINAL** (current) | No |
| `tests/ubcc/ep-rnf/test_phase4_local_upgrade.py` | 1 (Chunk 7 ONLY) | General: SnpCleanInvalid case extraction for local upgrade test | **FINAL** (current) | No |

#### Key Decision Dependencies:
- **Decision**: Self-test (M4-M8) separated from E2E workload via `enableSelfTest` flag — set False in E2E tests
- **Decision**: `populateGrantData` needs rewrite: when data is at remote node, must initiate global read via UBCC
- **Decision**: `materializedData` removed from DirEntry → data passes through `OutstandingRequest.dataBuffer`
- **Decision**: `sync_wait` barrier for E2E workloads: spin on DSM load instead of unimplemented syscall 436

#### Known Traps:
- Server resource contention: need `taskset` to pin to specific CPU cores
- Docker required for build/run with specific flags (-j32)
- TC3-TC8 all FAIL because `populateGrantData` returns `0x0` from functionalRead

---

### Phase 5-6: Regression Debug + TC Fixing (Chunk 8)

**Goal**: Debug and fix all failing TCs (TC3, TC6, TC7, TC8). Detailed request-chain analysis.

#### Modified Files:

| File | Edits | Intent Evolution | Final Status | Experimental? |
|------|-------|-----------------|--------------|---------------|
| `src/mem/ruby/protocol/chi/ep/EPBackend.cc` | 136 total (Chunk 8) | CHI Protocol (SLICC): TC8 recall fix | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPBackend.hh` | 20 total (Chunk 8) | EPBackend: hook changes | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPRNFController.cc` | 112 total (Chunk 8) | CHI Protocol (SLICC): multi-beat CompData handling | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPRNFController.hh` | 49 total (Chunk 8) | EP-RNF Controller: PendingChiTxn beatsExpected/beatsReceived | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/UBCCController.cc` | 139 total (Chunk 8) | UBCC Controller: pendingInvalidation, sharersToInvalidate | Superseded | No |
| `tests/e2e/workloads/e2e_common.h` | 13 total (Chunk 8) | Testing/Debug: sync_wait barrier implementation | Superseded by Chunk 10 | No |
| `tests/e2e/workloads/e2e_tc3_pingpong.c` | 6 (Chunk 8 ONLY) | CHI Protocol (SLICC): dsm_store+dsm_load with spin-wait | **FINAL** (current) | No |
| `tests/e2e/workloads/e2e_tc6_multi_sharer.c` | 1 (Chunk 8 ONLY) | General: sync_wait + emit_before_rd | **FINAL** (current) | No |

#### Key Decision Dependencies:
- **Decision**: TC8 recall must go through CHI ReadShared (not functionalRead) to register EP-RNF in HN-F dir_sharers
- **Decision**: Multi-beat CompData handling: PendingChiTxn needs `beatsExpected` + `beatsReceived` counters
- **Decision**: Wait for all beats before triggering callback
- **Decision**: EP-RNF CompAck routing destination: must use `mapAddressToDownstreamMachine` (HN-F), not responder

#### Design Flow Evolution (TC8 analysis):
1. Initial analysis: TC8 failure = recall via functionalRead doesn't register EP-RNF
2. Fix: recall → CHI ReadShared via EP-RNF
3. Secondary bug: multi-beat CompData — first beat triggers callback, second beat lost
4. Secondary bug: CompAck routing to wrong destination
5. Tertiary bug: HN-F uses ReadUnique for CleanUnique, leading to data/compack mismatch

---

### Phase 7-8: CompAck Routing Fix + Stabilization (Chunk 9)

**Goal**: Fix CompAck routing, SN-F topology issues, node isolation violations.

#### Modified Files:

| File | Edits | Intent Evolution | Final Status | Experimental? |
|------|-------|-----------------|--------------|---------------|
| `configs/ruby/CHI_ubcc_framework.py` | 35 total (Chunk 9) | EPBackend → CHI Framework Config | Superseded | No |
| `src/mem/ruby/protocol/chi/CHI-cache-actions.sm` | 124 total (Chunk 9) | CHI Protocol (SLICC): DCT fallback, SendCompData fixes, epRnf registration in SC entry | Superseded | No |
| `src/mem/ruby/protocol/chi/CHI-cache-funcs.sm` | 15 total (Chunk 9) | General | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPRNFController.cc` | 112 total (Chunk 9) | General: CompAck routing via mapAddressToDownstreamMachine | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPRNFController.hh` | 49 total (Chunk 9) | General | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPSNFController.cc` | 30 total (Chunk 9) | General | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/UBCCController.cc` | 139 total (Chunk 9) | General → UBCC Controller | Superseded | No |
| `tests/e2e/workloads/e2e_common.h` | 13 total (Chunk 9) | Testing/Debug: sync_wait barrier refinement | Superseded by Chunk 10 | No |

#### Key Decision Dependencies:
- **Decision**: Critical topology bug discovered — Node 1 HN-F was routing DSM requests from other nodes directly to EP-SNF of wrong node (node isolation violated)
- **Decision**: Each node must use ONLY its own physical address range; cross-node access goes through UBCC only
- **Decision**: DCT must be disabled for EP-RNF ReadShared requests to avoid SC_RSC crash
- **Decision**: SC→UD write must issue CleanUnique (not ReadUnique) so data comes from core, not SN-F
- **Decision**: `alloc_on_readunique = True` to enable L3 for DSM (with EP-RNF in dir_sharers)

#### Known Traps:
- Topology was corrupted: "Node 1's L2 directly routed to Node 2's HN-F" — node isolation broken
- Multi-node pa-layout must be followed: each node owns distinct physical address ranges
- `WriteBackFull` path needed for SC→SD writeback to home node before EP-RNF invalidation
- CompAck routing: must use HN-F's `mapAddressToDownstreamMachine`, not responder ID

---

### Phase 9: WriteRecall ReadUnique + Debug (Chunk 10)

**Goal**: Fix WriteRecall path using ReadUnique semantics. Debug remaining TC failures.

#### Modified Files:

| File | Edits | Intent Evolution | Final Status | Experimental? |
|------|-------|-----------------|--------------|---------------|
| `configs/ruby/CHI_ubcc_framework.py` | 35 total (Chunk 10) | CHI Framework Config: alloc_on_readunique=True | Superseded | No |
| `src/mem/ruby/protocol/chi/CHI-cache-actions.sm` | 124 total (Chunk 10) | CHI Protocol (SLICC): WriteRecall ReadUnique handling | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPBackend.cc` | 136 total (Chunk 10) | EPBackend: recall/grant paths | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPBackend.hh` | 20 total (Chunk 10) | General | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPRNFController.cc` | 112 total (Chunk 10) | EP-RNF Controller: WriteRecall, retry mechanism | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/EPRNFController.hh` | 49 total (Chunk 10) | EP-RNF Controller (experimental → reverted) | **REVERTED** (Chunk 10 only) | **Yes** |
| `src/mem/ruby/protocol/chi/ep/EPSNFController.cc` | 30 total (Chunk 10) | EP-SNF Controller: grant adjustments | Superseded | No |
| `src/mem/ruby/protocol/chi/ep/UBCCController.cc` | 139 total (Chunk 10) | General: recall + invalidation paths | Superseded | No |
| `tests/e2e/workloads/e2e_common.h` | 13 total (Chunk 10) | UBCC Controller: final sync_wait barrier version | **FINAL** (current) | No |
| `tests/e2e/workloads/e2e_tc5_single_writer.c` | 1 (Chunk 10 ONLY) | Cross-Node Coordination: sync_wait barrier before Phase 4 | **FINAL** (current) | No |
| `tests/e2e/workloads/e2e_tc8_upgrade_invalidate.c` | 2 (Chunk 10 ONLY) | EP-SNF Controller: emit_before_rd + dsm_load + sync_wait | **FINAL** (current) | No |

#### Key Decision Dependencies:
- **Decision**: WriteRecall path sends ReadUnique to HN-F when ownership transfer requires invalidation
- **Decision**: `alloc_on_readunique = True` for L3 cache in DSM to prevent L3 bypass
- **Decision**: TC5 + TC8 workloads updated with `sync_wait` barriers for cross-node synchronization
- **Decision**: EPRNFController.hh Chunk 10 experimental changes reverted in Chunk 11

#### Experiment vs Final:
- **`EP-RNF CompAck SendSnpResp race condition fix`**: Iterated through immediate clear → deferred clear → retry queue before stabilizing
- **`pendingOp timer value tuning`**: Multiple iterations (1M→2M→5M→removed→reinstated) until OutstandingRequest-based serialization stabilized

---

### Phase 10: Rollback + Final Cleanup (Chunk 11)

**Goal**: Final rollback of all experimental changes; consolidate correct state; final regression pass.

#### Modified Files (ALL FINAL):

| File | Edits | Intent Evolution | Final Status |
|------|-------|-----------------|--------------|
| `configs/ruby/CHI_config.py` | 2 total | EP-RNF Controller: epRnfMachineVersion=-1, alloc_on_readunique=True | **FINAL** |
| `configs/ruby/CHI_ubcc_framework.py` | 35 total | EP-RNF Controller: alloc_on_readunique=True | **FINAL** |
| `src/mem/ruby/protocol/chi/CHI-cache-actions.sm` | 124 total | EP-RNF Controller: final transition logic | **FINAL** |
| `src/mem/ruby/protocol/chi/CHI-cache-funcs.sm` | 15 total | CHI Protocol (SLICC): pickSharerForSnoop final | **FINAL** |
| `src/mem/ruby/protocol/chi/CHI-cache-transitions.sm` | 15 total | CHI Protocol (SLICC): final transitions + epRnf registration | **FINAL** |
| `src/mem/ruby/protocol/chi/CHI-cache.sm` | 6 total | EP-RNF Controller: epRnfMachineVersion final | **FINAL** |
| `src/mem/ruby/protocol/chi/CHI-msg.sm` | 2 total | General: CHIDataMsg final (shared_hint field) | **FINAL** |
| `src/mem/ruby/protocol/chi/ep/EPBackend.cc` | 136 total | EPBackend: clearPendingOwnerUpdate final | **FINAL** |
| `src/mem/ruby/protocol/chi/ep/EPBackend.hh` | 20 total | EPBackend: setEpRnfController final | **FINAL** |
| `src/mem/ruby/protocol/chi/ep/EPRNFController.cc` | 112 total | EP-RNF Controller: globalInvalidate dispatch final | **FINAL** |
| `src/mem/ruby/protocol/chi/ep/EPRNFController.hh` | 49 total | EP-RNF Controller: QueuedImmediateResponse final | **FINAL** |
| `src/mem/ruby/protocol/chi/ep/EPSNFController.cc` | 30 total | EP-SNF Controller: home-node DDR4 routing final | **FINAL** |
| `src/mem/ruby/protocol/chi/ep/M6SelfTest.cc` | 4 total | General: processRecallResponse final | **FINAL** |
| `src/mem/ruby/protocol/chi/ep/UBCCController.cc` | 139 total | UBCC Controller: epoch check, OutstandingRequest final | **FINAL** |
| `src/mem/ruby/protocol/chi/ep/UBCCController.hh` | 36 total | UBCC Controller: globalInvalidate final signature | **FINAL** |
| `src/mem/ruby/slicc_interface/AbstractController.cc` | 2 total | General: makeMachineID final | **FINAL** |
| `src/mem/ruby/slicc_interface/AbstractController.hh` | 1 total | General: makeMachineID declaration final | **FINAL** |
| `tests/e2e/test_e2e.py` | 8 total | Testing/Debug: enableSelfTest=False final | **FINAL** |
| `tools/bisect_apply_edits.py` | 1 total | General: bisect tool | **FINAL** |

---

## Oscillating Files (Most Frequently Modified)

These 12 files had the most edit churn across multiple chunks:

| File | Chunks Touched | Total Edits | Key Phases |
|------|---------------|-------------|------------|
| `UBCCController.cc` | 10 (1-11) | 139 | CHI Protocol, UBCC Controller, Cross-Node Coordination |
| `EPBackend.cc` | 11 (0-11) | 136 | EPBackend, CHI Protocol, Cross-Node Coordination |
| `CHI-cache-actions.sm` | 7 (1-11) | 124 | CHI Protocol (SLICC), EP-RNF Controller |
| `EPRNFController.cc` | 9 (0-11) | 112 | CHI Protocol (SLICC), EP-RNF Controller |
| `EPRNFController.hh` | 9 (0-11) | 49 | EP-RNF Controller, CHI Protocol (SLICC) |
| `TBEStorage.hh` | 3 (2-4) | 46 | TBE/Reservation, Testing/Debug |
| `CHI_ubcc_framework.py` | 10 (0-11) | 35 | General, TBE, Testing, UBCC, EPBackend |
| `UBCCController.hh` | 6 (1-11) | 36 | EPBackend, UBCC Controller |
| `EPSNFController.cc` | 10 (0-11) | 30 | EP-SNF, EPBackend, General |
| `EPBackend.hh` | 9 (2-11) | 20 | General, UBCC Controller, EPBackend |
| `CHI-cache-funcs.sm` | 6 (2-11) | 15 | General, CHI Protocol (SLICC) |
| `CHI-cache-transitions.sm` | 4 (1-11) | 15 | CHI Protocol (SLICC) |

---

## Experimental Changes (All Reverted/Superseded)

| Pattern | Files | Why Reverted | Final Alternative |
|---------|-------|-------------|-------------------|
| TBEStorage debug prints | TBEStorage.hh | Root cause identified (double-decrement from SLICC auto-generated code) | Removed |
| Deadlock threshold oscillation | CHI_ubcc_framework.py | Values oscillated as deadlock symptoms changed | 20000000 (integer) fixed |
| pendingOp timer value tuning | UBCCController.cc/.hh | Timer values iterated; final uses OutstandingRequest-based serialization | OutstandingRequest struct |
| sendLocalSnoop → CHI Request-Based Snoop | EPRNFController.cc/.hh | User mandated: must go through HN-F for correct CHI protocol semantics | ReadShared/CleanUnique → HN-F |
| ReadOnce recall → HN-F ReadShared | EPRNFController.cc | User rejected: "我跟你说过多少遍不能用ReadOnce" | ReadShared sent to HN-F |
| CompAck dest: responder → mapAddressToDownstreamMachine | EPRNFController.cc | Standard CHI routing via mapAddressToDownstreamMachine is correct | mapAddressToDownstreamMachine |
| EP-RNF CompAck SendSnpResp race condition fix | EPRNFController.cc | Iterated through several approaches before stabilizing | QueuedImmediateResponse with needBarrierClear |
