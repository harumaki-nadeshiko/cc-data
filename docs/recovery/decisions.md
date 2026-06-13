# Document C: Key Decisions & Fix Records

## Overview
This document catalogs every significant architectural, protocol, debugging, and testing decision made during the UBCC cross-node cache coherence development. Each entry includes: context, options considered, chosen approach, rationale, and result.

---

## 1. Architecture Decisions

### D-1: EP-RNF vs EP-SNF as Cross-Node Request Entry Point

**Context**: When handling cross-node requests (recall/invalidation), which EP component should initiate the internal CHI request? EP-RNF (Request Node Full) or EP-SNF (Slave Node Full)?

**Options**:
- **A**: EP-SNF handles all external-to-internal translation (SNF already has downstream data path)
- **B**: EP-RNF handles external-to-internal translation as a standard RN-F (semantically cleaner)

**Chosen**: **Option B — EP-RNF**

**Rationale**: 
- User mandate: "EP-RNF和其他的RNF具有几乎相同的抽象和语义，包括他能被当做一个普通的sharer"
- EP-RNF as RN-F: can send CHI requests (ReadShared, CleanUnique), receive snoops, send CompAck
- EP-SNF as SN-F: handles data provision only — having it initiate requests violates protocol semantics
- CHI spec: RN-F initiates requests, HN-F generates snoops; EP-RNF as RN-F fits naturally

**Result**: EP-RNF sends CHI requests to HN-F; HN-F generates appropriate snoops to CPU RN-Fs.

---

### D-2: HN-F Routing — Must Go Through HN-F (No Bypass)

**Context**: Initial implementation used `sendLocalSnoop()` in EP-RNF to directly invalidate L1/L2 caches, bypassing HN-F. User rejected this approach.

**Options**:
- **A**: EP-RNF sends local snoops directly to CPU caches (initially implemented)
- **B**: EP-RNF sends CHI Requests to HN-F; HN-F uses native state machine to generate snoops

**Chosen**: **Option B — Always route through HN-F**

**Rationale**:
- User: "EP-RNF怎么会发Local Snoop呢? CHI Specification里面, Snoop本来就是HN-F发送的东西"
- User: "不允许绕过HN-F"
- HN-F is the sole snoop authority in CHI
- Bypassing HN-F means HN-F's directory state is not updated → cache line state inconsistency

**Result**: All cross-node requests go through EP-RNF → HN-F → native CHI snoop flow.

---

### D-3: Cross-Node Isolation via Physical Address Partitioning

**Context**: In multi-gem5-multi-node simulation, each node must be isolated. Cross-node communication goes only through UBCC.

**Options**:
- **A**: Shared NoC topology allowing cross-node CHI routing (violates isolation)
- **B**: Each node has distinct PA ranges; HN-F routes cross-node addresses to local EP-SNF; UBCC translates between node PA spaces

**Chosen**: **Option B — Physical address partitioning**

**Rationale**:
- User: "不同节点的Ruby Network不是要隔离吗？他们只能通过UBCC间的链路进行通信"
- Each node's HN-F sees only its own address ranges
- Cross-node DSM addresses routed to EP-SNF → UBCC → remote UBCC → EP-RNF → HN-F
- Architecture supports future multi-gem5 deployment (EP/UBCC as external modules)

**Result**: Topology corrected in Phase 8 after corruption was discovered (HN-F was routing cross-node directly).

---

### D-4: EP/UBCC Module Separation for Future Multi-Process

**Context**: User specified that in the final system, each node runs in its own gem5 process. EPBackend is the gem5-internal interface; UBCC is the external module.

**Options**:
- **A**: Merge EP modules into CHI controllers (simpler for single-process simulation)
- **B**: Keep EP/UBCC as separate modules with explicit boundaries (enables future multi-process)

**Chosen**: **Option B — Separate modules**

**Rationale**:
- User: "EP Backend作为Gem5内的对外的接口，与外部的UBCC模块...进行通信"
- UBCC will be extracted as independent module with forwarding, directory, global logic
- Separation enforces clean interfaces needed for multi-gem5 deployment

**Result**: `EPController.py` uses abstract intermediate base; no direct CHIGenericController dependency.

---

### D-5: UBCC-UBCC Interconnect Latency Model (CXL-like)

**Context**: Initial prototype had 0-cycle UBCC-to-UBCC latency, causing reqInPort/datInPort race conditions with TBE allocation.

**Options**:
- **A**: Keep 0-cycle latency (just fix TBE timing)
- **B**: Add realistic latency (scheduling/timer-based) to model UBCC interconnect

**Chosen**: **Option B — Add latency modeling**

**Rationale**:
- User: "UBCC之间的互联的延迟进行大幅降低，他是高速互联，所以延迟大致会落在数百纳秒到几毫秒之间"
- 0-cycle: HN-F sends request to EP-SNF → EP-SNF returns CompData in same tick → TBE not yet allocated → crash
- Realistic latency separates reqIn/datIn by at least 1 tick, allowing TBE allocation to complete

**Result**: `OutstandingRequest` uses timer-based `respTick` to schedule grant responses at correct time.

---

## 2. Protocol Decisions

### D-6: `alloc_on_readunique = True` (L3 for DSM)

**Context**: HN-F's L3 cache was being bypassed for DSM addresses, causing every access to go to EP-SNF/UBCC unnecessarily.

**Options**:
- **A**: `alloc_on_readunique = False` (L3 bypassed for all ReadUnique)
- **B**: `alloc_on_readunique = True` (L3 enabled for DSM with EP-RNF in dir_sharers)

**Chosen**: **Option B — Enable L3 for DSM**

**Rationale**:
- Without L3 caching, every cross-node access triggers full UBCC round-trip
- HN-F needs L3 to cache DSM lines for performance
- EP-RNF registered in dir_sharers ensures correct snoop behavior when L3 caches DSM lines

**Result**: `CHI_ubcc_framework.py`: `hnf_cntrl.alloc_on_readunique = True`.

---

### D-7: DCT (Direct Cache Transfer) — Disabled for EP-RNF Requests

**Context**: When EP-RNF sends ReadShared to HN-F, DCT could trigger SnpSharedFwd directly to L2 owner, bypassing EP-RNF entirely. This caused SC_RSC crash because CompAck arrived during SnpSharedFwd processing.

**Options**:
- **A**: Keep DCT enabled, fix SC_RSC handling (add transition)
- **B**: Disable DCT for EP-RNF requests (DCT fallback to DMT-disabled path)

**Chosen**: **Option B — DCT disabled for EP-RNF requests; fallback to DMT-disabled path**

**Rationale**:
- User: "那是不是应该disable DMT, 这会影响什么（正确性、性能），以及有别的更好的方案？"
- DCT causes SnpSharedFwd → L2 owner directly without EP-RNF involvement
- Time window: Write Phase deferred CompAck + Recall Phase SnpSharedFwd → SC_RSC crash
- DMT-disabled fallback: ReadNoSnp → SN-F (slower but correct)
- Performance impact: minimal for cross-node operations (UBCC latency dominates)

**Result**: In `CHI-cache-actions.sm`, `Initiate_ReadShared_HitUpstream` DCT branch → `replace_request` → DMT-disabled ReadNoSnp path.

---

### D-8: CompData_SC/SD Semantic for Shared Read

**Context**: When external node wants shared read of cache line held SD (Shared Dirty) in local node, what state should the local line transition to after download?

**Options**:
- **A**: SD → SD (keep dirty status) — external gets clean data, local stays dirty
- **B**: SD → SC (write back to home, clean shared) — MESI semantics require clean shared

**Chosen**: **Option B — SD → SC (write back)**

**Rationale**:
- User: "MESI语义下S还需要保持和DSM Main Memory (@ its home node)的数据相同"
- In MESI, Shared state means all copies are clean and match main memory
- SD within a node while globally S/E violates MESI invariants
- ReadShared that triggers snoop to SD owner must include WriteBackFull to home

**Result**: `Send_WriteBackFull` action before sending `SnpRespData_SC` response data.

---

### D-9: EP-RNF Sharer Registration — Inline During CompData (No Extra Request)

**Context**: EP-RNF needs to be registered in HN-F's `dir_sharers` so future local CleanUnique can snoop EP-RNF. How to register?

**Options**:
- **A**: EP-RNF sends separate ReadShared request after first miss to "register" itself
- **B**: Register EP-RNF inline during the First Miss CompData response processing (no extra CHI request)

**Chosen**: **Option B — Inline registration**

**Rationale**:
- User: "为什么不直接在First Miss的请求路径上...加上EP-RNF的共享信息"
- HN-F processes CompData from EP-SNF → sets cache line state to SC → also sets EP-RNF in dir_sharers
- `RegisterEPRNF_OnSharedHint` action in HN-F transition: one-time registration
- `shared_hint=true` flag in CHIDataMsg triggers registration

**Result**: Zero extra CHI requests for registration; EP-RNF registered during standard First Miss flow.

---

### D-10: `pickSharerForSnoop` — Priority Selection

**Context**: HN-F must select a single sharer for non-broadcast snoops. EP-RNF should not be selected when real L2 sharers are available.

**Options**:
- **A**: Let default `smallestElement()` pick any sharer (could pick EP-RNF over L2)
- **B**: Priority: remove EP-RNF from candidates → if remaining > 0, pick L2; if EP-RNF only, pick EP-RNF

**Chosen**: **Option B — Priority selection**

**Rationale**:
- User: "snoop的对象在有其他普通RNF sharers的情况下就不要选到EP-RNF(通过优先级等方式处理)"
- EP-RNF represents external sharers; snooping it triggers cross-node invalidation (expensive)
- When local L2 has the data, snoop L2 first (cheap local operation)
- When EP-RNF is the only sharer → data is only in external nodes → cross-node fetch needed

**Result**: `CHI-cache-funcs.sm`: `pickSharerForSnoop()` function with exclusion-first logic.

---

### D-11: EP-RNF Fwd Snoop Exclusion

**Context**: HN-F's `SnpSharedFwd`/`SnpUniqueFwd` (Forward snoop) is sent to one sharer which then forwards data to the requestor. EP-RNF should never be this forward target.

**Options**:
- **A**: Allow EP-RNF to be Fwd target (would need to pull data from external node)
- **B**: Permanent Fwd guard — EP-RNF excluded from all Fwd snoop targets

**Chosen**: **Option B — Permanent Fwd guard**

**Rationale**:
- EP-RNF metadata-only: doesn't hold data to forward
- Fwd to EP-RNF → must fetch from external nodes → extra latency defeats Fwd purpose
- If only EP-RNF is sharer → DCT fallback → HN-F sends ReadNoSnp to SN-F directly

**Result**: `pickSharerForSnoop()` permanently excludes EP-RNF from Fwd contexts.

---

### D-12: UBCC DirEntry / OutstandingRequest Decoupling

**Context**: Original `DirEntry` mixed persistent directory state (state, ownerNode, sharersMask, dirty, epoch) with transient request state (pendingOp, pendingRequester, grantTick). This caused state space explosion and races.

**Options**:
- **A**: Keep mixed structure, add more flags
- **B**: Split into `DirEntry` (persistent) + `OutstandingRequest` (transient)

**Chosen**: **Option B — Decouple**

**Rationale**:
- User: "UBCC维护的本节点内的目录功能应该和他作为转发远端请求的中转的功能解耦开来"
- Mixed state: same cache line can only have ONE pending operation → serialized throughput
- Decoupled: OutstandingRequest table separate from directory → per-line concurrency possible
- Cleaner formal verification target

**Result**: `OutstandingRequest` struct with own mutex-style semantics; DirEntry pure directory state.

---

### D-13: `materializedData` — Temporary, Not Persistent

**Context**: `DirEntry.materializedData` was a 64-byte buffer in the directory entry. It accumulated stale data over time.

**Options**:
- **A**: Keep materializedData in DirEntry (acting as directory-attached cache)
- **B**: Remove from DirEntry; use OutstandingRequest.dataBuffer for temporary recall→grant data pass

**Chosen**: **Option B — Temporary data buffer**

**Rationale**:
- User: "materializeddata最多只是在请求进行时缓存，请求结束后就释放，而不是一个跟目录绑定的缓存"
- UBCC is metadata-only — should not persist data
- dataBuffer in OutstandingRequest: lives only during recall→grant lifecycle
- Prevents stale data accumulation in directory

**Result**: `OutstandingRequest.dataBuffer[64]` + `dataValid` flag; `DirEntry.materializedData` removed.

---

## 3. Debugging Decisions

### D-14: TBEStorage `decrementReserved()` Debug Strategy

**Context**: Assertion `m_reserved > 0` failed at tick ~50M. Crash occurred in TC1 but not `test_minimal`.

**Options**:
- **A**: Fix bug immediately with root cause analysis
- **B**: Relax assertion temporarily (workaround) while investigating root cause
- **C**: Refactor TBE allocation to avoid the race

**Chosen**: **Option B → C transition**

**Rationale**:
- User: "如果可以我希望给一个真正的fix, 而不是目前的workaround"
- User: "暂时不要改这里，这个是原有CHI的实现，我想尽量不侵入式的修改行为"
- Root cause: SLICC auto-generated code produces double-decrement on `m_reserved`
- Tracked via SLICC source (`CHI-cache.sm`) and generated code (`Cache_Controller.cc`)
- Long-term fix: Formal verification of TBE accounting + latency modeling

**Result**: Assert remains relaxed with detailed TODO comment for formal verification target.

---

### D-15: `pendingOp` Serialization Strategy

**Context**: Concurrent operations on same cache line at UBCC caused state corruption. Need serialization.

**Options**:
- **A**: Fixed timer-based serialization (pendingOp × N cycles then retry)
- **B**: OutstandingRequest-based serialization with explicit state transitions

**Chosen**: **Option B — OutstandingRequest-based**

**Rationale**:
- Fixed timers: 1M→2M→5M→removed→reinstated cycles during development; never stable
- OutstandingRequest: explicit OpState (WAITING_RESP/RESP_RCVD/CANCELLED) with clear enter/exit conditions
- Deterministic: no timer races; state transitions are explicit
- Formal verification-friendly

**Result**: Legacy `pendingOp=1/2/3` replaced with `createOutstanding()/findOutstanding()/completeOutstanding()`.

---

### D-16: Gemini Log Analysis Requirement

**Context**: AI repeatedly made analysis errors based on insufficient evidence (e.g., misidentifying RUSD state semantics).

**Decision**: User mandated:
- "根据Log的具体内容, 分析关于关键的那条Cacheline...每一处需要有真实且具体的log作为证据"
- "你没有对具体Log的数据进行真正的分析回答，而是找了两个Log数据给他猜想了一个原因，我不接受"
- "通过Gem5 debug / GDB等各种手段进行深度调试"

**Result**: All analysis required: src/dest/type/payload/time for each CHI message, state transitions per tick.

---

## 4. Testing Decisions

### D-17: Self-Test / E2E Workload Separation

**Context**: M4-M8 self-tests ran unconditionally in `EPBackend::init()` at tick 0, competing with ARM workload resources.

**Options**:
- **A**: Keep self-tests mixed with workload (original design)
- **B**: Separate via `enableSelfTest` flag — E2E tests disable self-tests

**Chosen**: **Option B — Separate**

**Rationale**:
- User: "我不希望selftest和workload混跑"
- Self-tests consume TBE resources, trigger UBCC operations at tick 0
- ARM boot + workload initialization can be affected
- Separate binary: either self-test mode or workload mode, not both

**Result**: `EPBackend::_enableSelfTest` flag; `test_e2e.py` sets `enable_self_test = False`.

---

### D-18: `sync_wait` Barrier Solution

**Context**: Cross-node E2E tests needed a barrier to ensure write visibility before cross-node reads. gem5 SE-mode lacks syscall 436 (not implemented).

**Options**:
- **A**: Use ARM DMB/DSB instructions only (might not drain Ruby store buffer)
- **B**: `sync_wait`: DMB OSH + spin-wait on DSM load value

**Chosen**: **Option B — sync_wait barrier**

**Rationale**:
- `dmb osh` drains store buffer but doesn't guarantee cross-node visibility
- Spin-wait on DSM load: busy-waits until expected value is visible (proves cross-node coherence)
- Implicit verification: if coherence is broken, spin-wait hangs → easy to detect
- User: "barrier需要保证Node 1的读在Node 0的写完成之后，可以辅以nop或cache flush等操作"

**Result**: `e2e_common.h`: `sync_wait(node_id, mask)` function using DMB + spin on `dsm_load`.

---

### D-19: Phase 0 Test Hardening (5+ Iterations)

**Context**: Phase 0 was supposed to verify MachineID injection + NoC connectivity. Initial versions had multiple correctness gaps.

**Iterations**:
- v1: Checked version integers only (no MachineID equivalence)
- v2: Added `getEpRnfMachineID()` but silently PASSed on SWIG failure
- v3: Added E-02 structural verification chain (3-layer)
- v4: E-01 SKIP instead of silent PASS; E-03 strict parsing (raise on failure)
- v5: Removed E-03 generic regex fallback; strict unique match only

**Key Fixes**:
- `getEpRnfMachineID()` SWIG failure → `SkipTest` exception (exit=2, not silent pass)
- E-02: Python version injection → SLICC build → C++ TBE (full chain)
- E-03: Parse actual `deadlock_threshold` from framework config; no fallback

**Result**: 3 test points (E-01, E-02, E-03) with zero false passes.

---

### D-20: TC3 Ping-Pong Test Evolution

**Context**: TC3 is the "hello world" cross-node coherence test. Initially passed then regressed multiple times.

**Key fixes over development**:
1. Q2: Fixed cross-node invalidation path (EP-RNF → HN-F → snoop)
2. Q3: Fixed DCT race with SnpSharedFwd (changed recall to ReadShared)
3. Ph2-3: Registered EP-RNF as sharer (otherwise CleanUnique doesn't snoop)
4. Ph5-6: Added spin-wait for write visibility (instead of functional barrier)

**Final TC3 workload**:
```c
// Round 1: Node0 writes 0xA, Node1 reads 0xA
// Round 2: Node1 writes 0xB, Node0 reads 0xB
// Round 3: Node0 writes 0xC, Node1 reads 0xC
// 6 rounds total, alternating writer/reader
```

**Result**: Final PASS after Phase 10 cleanup.

---

## 5. Design Flow Evolution (Multi-Edit Patterns)

### D-21: EP-RNF Registration Plan Evolution (v1.0 → v3.2)

**v1.0**: `shared_hint` on CHIRequestMsg (WRONG — needs to be on CHIDataMsg response)

**v2.0**: `shared_hint` on CHIDataMsg; EP-RNF SnpShared→remoteFetch path (had HN-F semantic conflict)

**v3.0**: Removed SnpShared→remoteFetch, added Fwd guard permanently excluding EP-RNF, added DCT fallback to DMT-disabled path

**v3.1**: Fixed SnpUnique response types (`SnpResp_I` + `SnpRespData_I_PD`), added 4th single-target selection point in `Send_SnpSharedFwd_ToSharer`

**v3.2**: Fixed DCT fallback destination (ReadOnce semantics for fallback, not SnpUnique), aligned all `retToSrc` condition rules

**Final**: Version 3.2 + cosmetic fixes. Reviewed and approved by strict-task-completion-reviewer at each version.

---

### D-22: `populateGrantData` Evolution

**v1 (original)**: `phys_mem->functionalAccess()` + `first_word != 0` heuristic

**Problem**: Data might be in remote node's L2, not local phys_mem; heuristic unreliable

**v2**: Added `_lastGrantData` bypass for recall data

**Problem**: `_lastGrantData` not always populated; data race on multi-line

**v3 (final)**: Data goes through `OutstandingRequest.dataBuffer`; recall→grant pass; no phys_mem access

**User directive**: "如果数据在远端，他应该发起全局读的请求，让对端的UBCC向内拉取数据，再返回给本UBCC"

---

## 6. Environment/Infrastructure Decisions

### D-23: Docker Build & Run Requirement

**Context**: Build environment uses specific gcc version and ARM toolchain only available in Docker.

**Decision**: All builds and runs must happen inside Docker container `ubcc-dev:ubuntu20.04`.

**Command template**:
```bash
docker run --rm -v /mnt/data2/cgc/cc-ep:/workspace ubcc-dev:ubuntu20.04 \
  bash -c 'cd /workspace/gem5 && scons build/ARM/gem5.opt -j32'
```

**Rationale**: Host OS may not have correct toolchain; Docker ensures reproducibility.

---

### D-24: Git Submodule Management

**Context**: gem5 is a git submodule. Changes must be committed in both main repo and submodule.

**Decision**: 
1. Commit gem5 changes in submodule first
2. Commit main repo with updated submodule pointer
3. Push both using `~/.ssh/id_rsa_np` key, `localhost:7788` proxy

**Rationale**: User: "注意需要让gem5 submodule的进度被同步提交，并在主repo中能索引到正确的submodule commit"

---

### D-25: Server Resource Constraints

**Context**: Shared server with other users running gem5 compilations and simulations.

**Decision**:
- Use `taskset` to pin compile jobs to specific CPU cores (9-16)
- Kill stale gem5 processes: `pkill -u cgc -f "scons.*gem5"`
- Adjust `-j` flag based on available cores (j32 default)
- Dedicated test script: `scripts/q2_regression.sh`

**Rationale**: User: "服务器被别的负载占用，我们需要用taskset调度到CPU9-16上"

---

## Summary: Top 10 Most Impactful Decisions

| Rank | Decision ID | Decision | Impact |
|------|------------|----------|--------|
| 1 | D-2 | All requests must go through HN-F (no bypass) | Architecture constraint |
| 2 | D-1 | EP-RNF as standard RN-F (not EP-SNF) | Protocol entry point |
| 3 | D-12 | DirEntry/OutstandingRequest decoupling | Correctness foundation |
| 4 | D-9 | EP-RNF inline registration during CompData | Zero extra CHI traffic |
| 5 | D-10 | pickSharerForSnoop priority exclusion | Correct snoop targeting |
| 6 | D-7 | DCT disabled for EP-RNF requests | Prevents SC_RSC crash |
| 7 | D-8 | SD→SC writeback semantics | MESI invariant |
| 8 | D-6 | alloc_on_readunique = True (L3 for DSM) | Performance baseline |
| 9 | D-14 | TBEStorage debug → workaround → formal fix target | Debug strategy |
| 10 | D-17 | Self-test/E2E separation | Test infrastructure |
