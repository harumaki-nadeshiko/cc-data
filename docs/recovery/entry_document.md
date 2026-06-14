# CC-EP: Cross-Node Cache Coherence Protocol — Entry Document for Plan Formulation

**Version**: 1.0 — Synthesized from 12-chunk conversation analysis + 5 recovery documents  
**Baseline**: gem5 submodule @ `c665e76a58` (Phase 1-5 final: OutstandingRequest decoupling + non-fatal TBEStorage)  
**Audience**: GPT-5.4 / Claude Opus — Use this document as the sole input for guided plan formulation.

---

## 1. Project Overview

### 1.1 Goal

Implement a cross-node cache coherence protocol on gem5 Ruby CHI, enabling **Distributed Shared Memory (DSM)** across 3 nodes. Each node is an independent CHI domain. The coherence directory is maintained by an external **UBCC** (Universal Bus Cache Coherence) controller, connected via EP (Endpoint) proxy layers.

### 1.2 Architecture

```
Node i:
  CPU RN-F (L1/L2)
       │
  HN-F_i (CHI Home Node, L3)
       │
  ┌────┴────────────────────┐
  │ DL_SNF_i  │  EP_SNF_i   │ ← Physical address determines routing
  │ (local     │  (remote    │
  │  DRAM)     │  DSM proxy) │
  └───────────┴─────────────┘
       │              │
  [DDR4]     EPBackend_i ──── UBCC_i ──── UBCC Link ──── UBCC_j
                   │
              EP_RNF_i (receives snoops from HN-F_i)
```

- **3 nodes**, each with 4 CPUs, L1 (32KB) + L2 (256KB) + HN-F L3
- **PA isolation**: `PHY_BASE_i = i << 40` (1TB per node). Same DSM logical index maps to different PAs on different nodes.
- **Node communication**: Only through `UBCC_i ↔ UBCC_j` links (not HN-F ↔ HN-F direct)

### 1.3 Physical Address Layout

```
NODE_ADDR_SHIFT = 40
PHY_BASE_i      = i << 40
SEG_SIZE        = 128 MB (0x800_0000)

Per-Node Segments (offset from PHY_BASE_i):
  [0*SEG, 1*SEG): LocalPrivate  → DL_SNF_i (local DRAM)
  [1*SEG, 2*SEG): UbccExclusive → DL_SNF_i (UBCC metadata)
  [2*SEG, 3*SEG): DSM_0 window  → i==0? DL_SNF : EP_SNF (remote proxy)
  [3*SEG, 4*SEG): DSM_1 window  → i==1? DL_SNF : EP_SNF
  [4*SEG, 5*SEG): DSM_2 window  → i==2? DL_SNF : EP_SNF
```

## 2. Key Components

### 2.1 HN-F (Home Node — Cache Controller)

- **Type**: SLICC-generated CHI Cache Controller (L3 level)
- **Config**: `alloc_on_readshared=true`, `alloc_on_readunique=true`, `alloc_on_readonce=false`
- **TBE limit**: 256
- **Address ranges**: ALL nodes' DSM segments (cross-node routing via PA isolation)

**Protocol-visible state**:
- `tbe.dir_sharers` — set of sharer MachineIDs (includes L2 + EP-RNF)
- `tbe.dir_owner` — current owner MachineID
- `tbe.epRnfMachineID` — local EP-RNF's MachineID (injected via `epRnfMachineVersion` config)
- Standard CHI states: I, SC, UC, UD, SD, and transient states

### 2.2 EP-RNF (External Proxy — Request Node Forward)

- **Type**: Custom C++ controller (`EPRNFController`), extending `EPController`
- **Machine type**: `MachineType_Cache` (same as RN-F/L2 — treated as standard CHI sharer by HN-F)
- **Ports**: `reqOut` → HN-F, `snpIn` ← HN-F

**Key methods**:
| Method | Purpose |
|--------|---------|
| `sendChiRequest(pa, type)` | Send ReadShared/ReadOnce/CleanUnique/ReadUnique to local HN-F via reqOut |
| `recvSnoopMsg(msg)` | Handle 8 types of snoops from HN-F (SnpCleanInvalid, SnpUnique, SnpOnce, SnpShared, etc.) |
| `startReadShared(pa, cb)` | Initiate ReadShared for read recall |
| `startReadUnique(pa, cb)` | Initiate ReadUnique for write recall (invalidates old owner L2) |
| `startCleanUnique(pa, cb)` | Initiate CleanUnique for invalidation |

**Snoop handling matrix** (simplified):

| Snoop Type | Behavior | Blocking? |
|------------|----------|-----------|
| SnpCleanInvalid | Immediate SnpResp_I; optionally notify EPBackend (local upgrade) | No |
| SnpUnique | Blocking: globalInvalidate via UBCC → callback sends SnpResp_I (± data) | Yes |
| SnpOnce | Blocking: remoteFetch via UBCC → callback sends SnpRespData_SC | Yes |
| SnpShared/SharedFwd | Defensive SnpResp_I (EP-RNF is never owner, DCT fallback guards) | No |
| SnpOnceFwd | Forced DCT-off fallback | Yes |

### 2.3 EP-SNF (External Proxy — Slave Node Forward)

- **Type**: Custom C++ controller (`EPSNFController`), extending `EPController`
- **Receives**: ReadNoSnp, WriteNoSnp from HN-F for remote DSM addresses
- **Routes**: Through EPBackend to home-node UBCC
- **Writes NCBWrData** to the HOME node's DDR4 (not local DDR4 for cross-node writes)

### 2.4 UBCC Controller (Global Directory)

- **Type**: Pure C++ singleton, one instance per node, cross-node accessible
- **Maintains**: Global MESI state for each DSM cache line

**DirEntry fields** (persistent directory state):
```
state, ownerNode, sharersMask, epoch, dirty,
pendingOwnerUpdate  (barrier for async owner update completion)
```

**OutstandingRequest fields** (transient request state, separated from directory):
```
linePa, opType (RECALL/INVALIDATE/GRANT_HANDSHAKE), state (WAITING_RESP/RESP_RCVD/CANCELLED),
requesterNode, targetNode, epochAtCreate, dataBuf[64], startTick, respTick
```

**Key methods**:
| Method | Purpose |
|--------|---------|
| `processOuterRequest(pa, reqType, writeIntent, requester)` | Main entry: handles G_I→G_S→G_E→G_M transitions, recall triggering |
| `globalInvalidate(pa, requester, epoch, result)` | Invalidate all remote sharers, collect dirty data |
| `updateOwner(pa, ownerNode)` | Record new owner, set pendingOwnerUpdate barrier |
| `clearPendingOwnerUpdate(pa)` | Release barrier after ack round-trip |
| `processRecallResponse(pa, ownerNode, dataReceived, dataBuf, epoch)` | Complete recall: downgrade/invalidate old owner |
| `createOutstanding / findOutstanding / removeOutstanding` | Request lifecycle management |

### 2.5 EPBackend (Shared Engine)

- **Type**: C++ engine shared by EP-RNF and EP-SNF
- **Handles**: PA translation (NodeAddressMap), cross-node routing, recall execution

**Key methods**:
| Method | Purpose |
|--------|---------|
| `handleRemoteMiss(pa, neededPerm, writeIntent, homeNode)` | Process ReadNoSnp: route to home UBCC, populate grant data |
| `handleRecallRequest(msg)` | Execute recall: for read recall → functionalRead (downgrade); for write recall → startReadUnique (invalidate) |
| `populateGrantData(reqPa, homePa, homeNode)` | Fill grant data from recall buffer or home-node phys_mem |
| `notifyLocalWriteUpgrade(pa)` | Called after SnpCleanInvalid: update UBCC owner |
| `clearPendingOwnerUpdate(pa)` | Route barrier clear to home UBCC |

## 3. Protocol Semantics

### 3.1 EP-RNF Registration Flow

```
First Miss ReadShared(X):
  HN-F → ReadNoSnp → EP-SNF → EPBackend → UBBC(home)
  UBCC: I→G_S, sharersMask |= requester
  EP-SNF sends CompData_UC with m_shared_hint=true
  HN-F recvs CompData → RegisterEPRNF_OnSharedHint action
    → tbe.dir_sharers.add(epRnfMachineID)
```

### 3.2 Snoop Target Selection

`pickSharerForSnoop(dir_sharers, epRnfMachineID)`:
1. Remove EP-RNF from candidates
2. If candidates remain → pick `smallestElement()` (L2 priority)
3. Else → return EP-RNF (only sharer)

Used in 4 snoop actions: `Send_SnpUnique_RetToSrc`, `Send_SnpSharedFwd_ToSharer`, `Send_SnpOnce`, `Send_SnpOnceFwd`.

### 3.3 DCT Fallback

When `dir_sharers = {EP-RNF}` (EP-RNF only) and the selected snoop protocol uses DCT (Direct Cache Transfer), DCT is forced off:
```
// In Initiate_ReadShared_HitUpstream, Initiate_ReadUnique_HitUpstream, Initiate_ReadOnce_HitUpstream:
if ((dir_sharers.count() == 1) && dir_sharers.isElement(epRnfMachineID)):
    tbe.use_DCT := false
```

This prevents the Fwd variant (where the snoop target sends CompData directly to requester) since EP-RNF cannot forward data to an arbitrary L2.

### 3.4 CompData Semantics for EP-RNF

When HN-F sends ReadShared response to EP-RNF:
- **Always CompData_SC** (Clean Shared) — never CompData_SD_PD (Dirty Shared)
- EP-RNF is metadata-only — it cannot become `dir_owner`
- In `UpdateDirState_FromReqResp`: skip owner promotion when `responder == epRnfMachineID`
- In `Send_CompData`: clear `dataMaybeDirtyUpstream` and `dataDirty` when `requestor == epRnfMachineID`

### 3.5 SnpUnique Response Matrix

| retToSrc | hasData | isDirty | Resp Channel | Data Channel |
|----------|---------|---------|-------------|-------------|
| false | any | any | SnpResp_I | — |
| false | true | true | SnpResp_I | SnpRespData_I_PD |
| false | true | false | SnpResp_I | — (clean, no data needed) |
| false | false | — | SnpResp_I | — |
| true | true | true | — | SnpRespData_I_PD |
| true | true | false | — | SnpRespData_I |
| true | false | — | SnpResp_I (fallback) | — |

### 3.6 Recall Paths

**Read Recall** (G_M → G_S downgrade):
1. UBCC detects existing owner != requester
2. Initiates recall to owner node's EPBackend
3. EPBackend executes `functionalRead` on owner's cache hierarchy → captures data
4. Data returned via `_lastGrantDataBlock`
5. Owner downgraded to shared, requester granted shared access

**Write Recall** (G_M → invalidate for new writer):
1. Same as read recall, but owner must be INVALIDATED (not just downgraded)
2. EPBackend calls `startReadUnique(ownerLocalPa)` → HN-F sends ReadUnique → triggers SnpUnique → owner L2 goes I + dirty data returned
3. Data flows back through recall response
4. Key difference from read recall: uses CHI ReadUnique (invalidates old owner L2), NOT functionalRead

### 3.7 NCBWrData Cross-Node Routing

When EP-SNF receives `NCBWrData` for a remote DSM address:
1. Determine home node from PA
2. Route write to HOME node's DDR4 (via `EPBackend::getBackendInstance(homeNode) → getRubySystem() → getPhysMem()`)
3. NOT local DDR4 — this was the bug that caused TC2 cross-node writes to never persist

### 3.8 WriteNoSnp / ReadNoSnp Sideband

HN-F includes UBCC sideband fields in ReadNoSnp/WriteNoSnp requests:

```
out_msg.ubcc_needed_perm:  0 (Shared) | 1 (Unique)
out_msg.ubcc_write_intent: false | true
```

Derived from `tbe.reqType`: Load/ReadShared → (0, false); CleanUnique → (1, false); ReadUnique/Store → (1, true).

EP-SNF reads these fields to determine whether to request Shared, Exclusive, or Modified grant from UBCC.

## 4. Baseline Code State (c665e76a58)

### 4.1 What EXISTS in the baseline

| Component | File | State |
|-----------|------|-------|
| OutstandingRequest | `UBCCController.hh:OpType/OpState/OutstandingRequest` + `.cc` APIs | ✅ Ready |
| TBEStorage non-fatal | `TBEStorage.hh:decrementReserved()` — non-fatal saturation instead of assert | ✅ Ready |
| EP-RNF skeleton | `EPRNFController.cc/hh` — `sendChiRequest`, `startReadShared`, `startReadOnce`, `startCleanUnique`, `recvSnoopMsg` (basic dispatch), `recvDataMsg` | ⚠️ Partial |
| EP-SNF skeleton | `EPSNFController.cc/hh` — `recvRequestMsg` (ReadNoSnp/WriteNoSnp), `recvDataMsg` (NCBWrData to DDR4), deferred CompData send | ⚠️ Partial |
| EPBackend skeleton | `EPBackend.cc/hh` — `handleRemoteMiss`, `handleRecallRequest`, `populateGrantData`, `handleInvalidationRequest`, `notifyLocalWriteUpgrade` | ⚠️ Partial |
| Test framework | `tests/e2e/test_e2e.py` — TC1-TC11 infrastructure, `tests/e2e/workloads/dsm_access.h` | ✅ Ready |
| Self-test | M4-M8 self-tests in `EPBackend::init()` | ⚠️ Disabled by default (stale tests) |

### 4.2 What is MISSING from the baseline (must be implemented)

| Component | What's Missing |
|-----------|---------------|
| HN-F SLICC | `epRnfMachineVersion` param → `tbe.epRnfMachineID` in `initializeTBE`; `RegisterEPRNF_OnSharedHint` action; `pickSharerForSnoop` function; DCT fallback in 3 initiators; `UpdateDirState_FromReqResp` EP-RNF guard; `Send_CompData` EP-RNF SC semantics |
| CHI messages | `CHIDataMsg::m_shared_hint` field |
| EP-RNF controller | `startReadUnique`, `recvSnoopMsg` full type dispatch (8 snoop types), `onGlobalInvalidateComplete`, `onRemoteFetchComplete`, `sendOrRetry` with retry queue, `registrationDone` tracking |
| EP-SNF controller | Cross-node NCBWrData DDR4 routing, `shared_hint` setting on CompData, `grantHandshakeComplete` via CompAck |
| UBCC controller | `globalInvalidate` real implementation, `updateOwner`, `clearPendingOwnerUpdate`, `processRecallResponse` with dataBuf, `processWriteback` |
| EPBackend | `handleRecallRequest` write recall via `startReadUnique`, `globalInvalidate` dispatch, `clearPendingOwnerUpdate` home-node routing, `isPostGrantShared` |
| Config | `deadlock_threshold = 20000000` (integer, not `"10ms"` string), `alloc_on_readunique=true` |

### 4.3 Must NOT be changed (HN Minimal Modification Principle)

- Original CHI protocol state machine (except the specifically listed EP-RNF additions)
- Original SLICC actions (Send_ReadNoSnp, Send_CompData core logic)
- L1/L2 behavior
- Ruby network topology (node isolation already correct)

## 5. Test Matrix

### 5.1 Test Cases

| TC | Name | Description | Priority | Protocol Paths Covered |
|----|------|-------------|----------|----------------------|
| TC1 | dsm_local | Single-node local DSM read/write | P0 | Local only: L2 → HN-F → DL_SNF → DDR4 |
| TC2 | remote_read | Node0 writes DSM_1, Node1 reads it | P0 | Write: ReadUnique → ReadNoSnp → UBCC(G_M); Read: ReadShared → ReadNoSnp → UBCC(G_S via recall) |
| TC5 | single_writer | Single writer, multi-node read verification | P0 | Write recall via ReadUnique, shared_hint registration |
| TC6 | multi_sharer | Multiple nodes share same line, then one writes | P1 | shared_hint ×N, SnpCleanInvalid multicast, updateOwner |
| TC7 | writeback_evict | L2 eviction triggers writeback to UBCC | P1 | WriteBackFull → EP-SNF → DDR4, UBCC directory update |
| TC11 | local_upgrade | Local CleanUnique while EP-RNF is registered sharer | P1 | SnpCleanInvalid → notifyLocalWriteUpgrade → updateOwner |
| TC3 | pingpong | Two nodes alternate writes to same line | P2 | Multiple read/write recalls, G_M↔G_S transitions |
| TC8 | upgrade_invalidate | Three-node upgrade + invalidation chain | P2 | SnpUnique → globalInvalidate, EP-RNF snoop notification |
| TC4 | three_node_ring | Ring-style ownership transfer across 3 nodes | P2 | Complex recall chains, timing-sensitive |

### 5.2 Test Execution

```bash
# Single TC (60s timeout)
docker run --rm -v $(pwd):/workspace ubcc-dev:ubuntu20.04 bash -c '
  timeout 600 /workspace/gem5/build/ARM/gem5.opt \
    --outdir=/workspace/m5out/e2e/tcN \
    /workspace/tests/e2e/test_e2e.py --tc=N 2>&1'
```

### 5.3 Build

```bash
docker run --rm -v $(pwd):/workspace ubcc-dev:ubuntu20.04 bash -c '
  cd /workspace/gem5 && scons build/ARM/gem5.opt -j32 2>&1 | tail -30'
```

## 6. Lessons from Previous Version

### 6.1 Design Decisions That Were Validated

| Decision | Rationale |
|----------|-----------|
| EP-RNF as standard CHI RN-F (MachineType_Cache) | HN-F treats EP-RNF identically to L2 for snoop/registration; no new CHI message types needed |
| `shared_hint` on CHIDataMsg (not CHIRequestMsg) | CompData is the correct carrier — piggyback on first-miss grant response |
| `pickSharerForSnoop` with L2 priority | Single-target snoops prefer L2 (which has data) over EP-RNF (metadata-only) |
| DCT disabled when EP-RNF is only sharer | EP-RNF cannot forward data to requester via DCT; non-DCT path works correctly |
| CompData_SC for EP-RNF (not SD_PD) | MESI constraint: shared copies match home memory |
| `alloc_on_readunique=true` | EP-RNF in dir_sharers means HN-F L3 caching no longer bypasses UBCC path; any read/write/upgrade notifies EP-RNF via snoop |
| `deadlock_threshold = 20000000` | Integer cycles, not `"10ms"` string (gem5 v25.1 requires integer) |

### 6.2 Design Decisions That Were Reverted

| Decision | Why Reverted |
|----------|-------------|
| EP-RNF sends `sendLocalSnoop` (direct broadcast) | Violates CHI spec — snoops originate from HN-F, not RN-F |
| `ReadOnce` for recall | User mandate: must use ReadShared (maintains coherence semantics) |
| Timer-based `pendingOp` (tuning 200K→1B→5K→...→200K) | Oscillated 7 times; replaced by OutstandingRequest state machine |
| `dataToBeInvalid` removal for TC8 fix | Didn't work; cleaner fix was `alloc_on_readunique=true` |
| Syscall 436 barrier | Not implemented in gem5 SE-mode; replaced by spin-wait on DSM load |
| Relaxed `assert(tbe.dataValid)` | Compromised safety; root fix was ensuring dataValid is always set |

### 6.3 Known Hazards (from previous implementation)

| Hazard | Description | Mitigation |
|--------|-------------|------------|
| TBE race | HN-F receives ReadNoSnp CompData on same tick as reqIn TBE allocation → `decrementReserved` underflow | `m_allowRetry=true` on EP-RNF requests + non-fatal TBEStorage saturation |
| pendingOwnerUpdate leak | Barrier set but never cleared if SnpResp not sent → permanent BUSY | Clear barrier unconditionally after SnpResp send; retry queue with `needBarrierClear` |
| GRANT_HANDSHAKE leak | `createOutstanding(GRANT_HANDSHAKE)` creates WAITING_RESP that never transitions | Start as `RESP_RCVD` state; release after `_interconnectLatency` ticks |
| NCBWrData routing | Write data goes to LOCAL DDR4 regardless of DSM home | Route to HOME node's DDR4 |
| CompAck destination | EP-RNF sends CompAck to wrong MachineID (responder vs HN-F) | Use `mapAddressToDownstreamMachine(addr)` |

### 6.4 Oscillating Patterns (avoid repeating)

| File | Pattern | Final Resolution |
|------|---------|-----------------|
| `UBCCController.cc` pendingOp | 7 timer value changes (200K→1B→5K→150K→500K→5M→200K) | Replaced by OutstandingRequest state machine |
| `EPRNFController.cc` recall type | ReadShared↔ReadOnce↔ReadShared×6 reversals | ReadShared (for read recall); ReadUnique (for write recall) |
| `CHI-cache-actions.sm` Send_CompData | assert(dataValid) → relaxed → debug → relaxed → assert×5 cycles | Kept strict assert; root fix in `alloc_on_readunique=true` |
| `EPBackend.cc` populateGrantData | 3 data source rewrites (phys_mem→_lastGrantData→dataBuffer) | UBCC dataBuffer from OutstandingRequest |

## 7. Implementation Strategy

### 7.1 Layered Build Plan

```
Layer 3a: Infrastructure (~50 lines, 5 files)
  └─ Config params, MSG field, SLICC data declarations
     Files: CHI_config.py, CHI_ubcc_framework.py, CHI-msg.sm, CHI-cache.sm, EPRNFController.py

Layer 3b: SLICC Protocol (~300 lines, 4 files)
  └─ Actions, functions, transitions
     Files: CHI-cache-actions.sm, CHI-cache-funcs.sm, CHI-cache-transitions.sm, CHI-cache-ports.sm

Layer 3c: EP Controllers (~500 lines, 4 files)
  └─ EPRNFController, EPSNFController
     Files: EPRNFController.cc/hh, EPSNFController.cc/hh

Layer 3d: Backend Logic (~800 lines, 4 files)
  └─ EPBackend, UBCCController
     Files: EPBackend.cc/hh, UBCCController.cc/hh

Layer 3e: Integration Verify
  └─ scons build + TC1 test
```

### 7.2 Prohibited Modifications

- ❌ Do NOT modify original CHI state machine logic
- ❌ Do NOT add new field to DirEntry (add to OutstandingRequest instead)
- ❌ Do NOT use `printf`/`fprintf` for permanent logging (use DPRINTF only)
- ❌ Do NOT modify TBEStorage.hh (non-fatal saturation is correct baseline)
- ❌ Do NOT set `alloc_on_readunique=false`

### 7.3 Commit Protocol

After each layer compiles:
```bash
cd gem5 && git add -A && git commit -m "phase3X: <description>" && cd ..
git add gem5 && git commit -m "phase3X: submodule @ <hash>"
```

## 8. Your Task

You are GPT-5.4. Read this document fully, then begin **Phase A: Gap Discovery** with guided questioning.

Ask 3-5 questions per round. Each question should:
1. Identify one underspecified aspect of the protocol design
2. Provide 2+ candidate answers with trade-off analysis
3. Be answerable with a specific choice

Focus your questions on the areas not yet specified in this document:
- State machine edge cases (what happens when...?)
- Message flow boundary conditions
- Concurrency and race window analysis
- Test coverage gaps

Do NOT output a complete plan yet — only questions. After all questions are answered, you will be asked to synthesize the final `scheme_v4.md`.

---

## Appendix A: Key File Index

| File | Lines | Purpose |
|------|-------|---------|
| `gem5/configs/ruby/CHI_ubcc_framework.py` | ~350 | System creation: node config, HN-F params, EP params, deadlock_threshold |
| `gem5/configs/ruby/CHI_config.py` | ~100 | CHI protocol parameter definitions |
| `gem5/src/mem/ruby/protocol/chi/CHI-cache.sm` | ~500 | SLICC machine definition: TBE fields, state enum |
| `gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm` | ~4300 | All protocol actions (initiators, snoops, responses) |
| `gem5/src/mem/ruby/protocol/chi/CHI-cache-funcs.sm` | ~1600 | Helper functions (setExpected, prepareRequest, pickSharer) |
| `gem5/src/mem/ruby/protocol/chi/CHI-cache-transitions.sm` | ~1800 | All state transitions |
| `gem5/src/mem/ruby/protocol/chi/CHI-msg.sm` | ~300 | Message type definitions and fields |
| `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc` | ~800 | EP-RNF implementation |
| `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.hh` | ~340 | EP-RNF declarations, PendingChiTxn, PendingSnoopTxn |
| `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc` | ~380 | EP-SNF implementation |
| `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.hh` | ~60 | EP-SNF declarations, RetryEntry, deferred data queue |
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc` | ~1500 | Backend engine: recall, grant, invalidation, PA translation |
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh` | ~520 | Backend declarations, RequesterLineEntry, OuterGrantType |
| `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc` | ~1300 | UBCC directory: processOuterRequest, recall, invalidation, OutstandingRequest |
| `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh` | ~440 | UBCC declarations, DirEntry, OutstandingRequest, OpType/OpState |
| `gem5/src/mem/ruby/structures/TBEStorage.hh` | ~160 | TBE reservation management |
| `tests/e2e/test_e2e.py` | ~800 | E2E test driver |
| `tests/e2e/workloads/e2e_common.h` | ~200 | sync_wait barrier, DSM access macros |

## Appendix B: Abbreviations

| Abbrev | Full |
|--------|------|
| HN-F | Home Node — Fully coherent request agent (CHI L3 cache controller) |
| RN-F | Request Node — Fully coherent request agent (CHI L1/L2) |
| SN-F | Slave Node — Memory controller agent |
| EP-RNF | External Proxy — Request Node Forward (UBCC-to-HN-F bridge) |
| EP-SNF | External Proxy — Slave Node Forward (HN-F-to-UBCC bridge) |
| UBCC | Universal Bus Cache Coherence (global directory controller) |
| DCT | Direct Cache Transfer (CHI snoop protocol variant) |
| DMT | Direct Memory Transfer (CHI read protocol variant) |
| TBE | Transaction Buffer Entry |
| DSM | Distributed Shared Memory |
| SC/SD/UC/UD | CHI cache states (Shared Clean/Dirty, Unique Clean/Dirty) |
| G_I/G_S/G_E/G_M | UBCC global MESI states |
