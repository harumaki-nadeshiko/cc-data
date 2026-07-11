# TC98 Single-PA Contention: Architecture Analysis & Optimization Options

> Date: 2026-07-11 | Consolidates analysis of 2.1-2.4

---

## 1. MAX_PENDING_PER_PA: Queue Location & Scaling Analysis

### 1.1 Queue is on Outstanding (volatile), not persistent directory

The `_pendingRequesters` queue is a **per-PA volatile `std::deque`** inside
`UBCCController` (`UBCCController.hh:654`):

```cpp
std::map<uint64_t, std::deque<PendingRequester>> _pendingRequesters;
```

It is **NOT** part of the persistent `ResidentDir` directory. It lives alongside
`_outstandingReqs` (`UBCCController.hh:642`) in the controller's runtime state.

Key properties:
- Created on-demand when a foreign requester arrives while a live outstanding exists
- Drained by `replayPendingRequesters()` after Clear commit
- Capped at `MAX_PENDING_PER_PA = 4` per PA (`UBCCController.hh:232`)
- Overflow: `drop_full` -> requester gets BUSY(-1), relies on retry timer (~20000 cycles)

### 1.2 Impact of increasing MAX_PENDING_PER_PA

**TC98 scenario**: 16 requesters, same PA, 16 rounds each.

Current (MAX=4): At any moment, 1 active + 4 queued + 11 dropped.
- Dropped requesters wait for 20000-cycle retry timer (~20us at 1GHz)
- Each retry may also be dropped if queue is still full
- **Effective throughput**: ~1 grant per (RECALL+Clear RTT + retry alignment)

With MAX=16: At any moment, 1 active + 15 queued + 0 dropped.
- No retry timer waste
- Queue replay is chained: each Clear commit immediately dequeues next
- **Effective throughput**: ~1 grant per RECALL+Clear RTT (no retry overhead)

**Estimated improvement for TC98**:

| Metric | MAX=4 | MAX=16 | Improvement |
|--------|-------|--------|-------------|
| Retry overhead per dropped req | ~20us | 0 | Eliminated |
| Requests needing retry (per round) | 11 of 16 | 0 of 16 | 100% |
| Total time for 16 rounds of 16 reqs | ~256 * (RTT + avg_retry) | ~256 * RTT | ~2-5x faster |
| Memory overhead per PA | 4 * sizeof(PendingRequester) ~160B | 16 * ~40B = 640B | +480B/PA |

**Memory cost**: `PendingRequester` is ~40 bytes (node, socket, reqType, writeIntent,
epoch, reqId). Even MAX=64 would be only 2.5KB per hot PA. This is negligible compared
to the 64B cache line data + directory entry overhead.

**Recommendation**: Increase `MAX_PENDING_PER_PA` to at least 16 (or make it a
constructor parameter). The memory cost is trivial. The performance gain in high-contention
scenarios is significant because it eliminates the retry timer penalty entirely.

**Caveat**: This only helps the *protocol-level* serialization. The PDES sync
amplification is a separate issue (see section 3).

---

## 2. Is Clear Still Necessary?

### 2.1 Clear's current roles (9 identified)

| # | Role | Can be moved to grant-time? |
|---|------|---------------------------|
| 1 | **Unique commit point** (DirEntry write) | Yes - eager commit |
| 2 | **Epoch advancement** | Yes - reservedEpoch already allocated at grant time |
| 3 | **Outstanding slot release** | Yes - release at grant emission |
| 4 | **Pending requester chain replay** | Yes - replay at grant emission |
| 5 | **Tombstone creation** (idempotent replay) | Semantics change - no duplicate Clear without Clear |
| 6 | **Epoch gate validation** | Unnecessary without Clear |
| 7 | **Data persistence** (_lineDataCache write) | Yes - data available at grant time |
| 8 | **Bloom filter update** | Yes |
| 9 | **Audit log** | Yes |

### 2.2 Critical finding

At `WAITING_CLEAR` stage, **all prerequisites are already satisfied**:
- RECALL barrier done (data received from previous owner)
- INVALIDATE barrier done (all sharers acked)
- Intended state fully computed

Clear is purely an ACK saying "requester received the grant". In the gem5
simulation environment (reliable transport, no message loss), this ACK
carries no additional correctness information.

### 2.3 Eager Commit proposal

Replace the 4-leg handshake:
```
Requester -> Home: Request
Home -> Owner: RECALL        (leg 1)
Owner -> Home: RecallResp    (leg 2)
Home -> Requester: GRANT     (leg 3)
Requester -> Home: Clear     (leg 4)  <-- REMOVE THIS
```

With 3-leg:
```
Requester -> Home: Request
Home -> Owner: RECALL        (leg 1)
Owner -> Home: RecallResp    (leg 2)
Home -> Requester: GRANT     (leg 3, commit happens here)
```

**Impact on TC98**: Eliminates 1 cross-node RTT per grant. Per grant:
saves ~810ns (405ns each way). For 256 serial grants: saves ~207us total.

### 2.4 Risks and mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| Grant lost in transit -> home already committed, requester never got data | High (in real HW) / None (gem5) | Compile-time switch `UBCC_EAGER_COMMIT`; keep Clear for fault-tolerant mode |
| TLA+ invariants I7/I13 violated | Medium | Re-verify with modified ClearCommit action; eager commit = commit at grant creation |
| Duplicate Clear test infrastructure (`dup_clear_req`) broken | Low | Only affects fault injection tests; skip in eager mode |
| `_pendingGrantTxns` mechanism in EPBackend becomes unnecessary | Low | Simplify: requester no longer needs to track pending-then-clear |

### 2.5 Recommendation

Implement `UBCC_EAGER_COMMIT` as a compile-time flag:
- **OFF (default)**: current 4-leg handshake, full fault tolerance
- **ON**: 3-leg eager commit, ~25-40% latency reduction per write-miss on critical path

---

## 3. Latency Breakdown: Why 2-3us per RECALL+Clear?

### 3.1 Network topology parameters

From `scripts/gen_topo.py`:
- Cross-node same-socket link latency: **405ns** (405000 ps)
- Same-node cross-socket link latency: **25ns** (25000 ps)
- Cross-node + cross-socket: **430ns** (405000 + 25000 ps)

From `framework/Port.hh`:
- Default `syncInterval`: **100ns** (100000 ps)
- Default `linkLatency`: **100ns** (100000 ps)
- Configurable via `EP_SYNC_INTERVAL_PS` / `EP_LINK_LATENCY_PS` env vars

### 3.2 Full critical path for a write-miss (RU) to foreign home

```
Step                                          Latency (ns)    Cumulative
----                                          ------------    ----------
1. CPU L1 miss -> HN-F lookup                 ~10-50          ~50
2. HN-F -> EP-SNF -> EPBackend handleRemoteMiss  ~10          ~60
3. EPBackend -> UBAdapter -> nsim TX (request)    ~5           ~65
4. nsim link: requester -> home                   405          ~470
5. PDES sync alignment (sender side)              0-100        ~570
6. PDES sync alignment (receiver side)            0-100        ~670
7. Home UBCC processOuterRequest                  ~5           ~675
8. Home UBCC -> nsim TX (RECALL to owner)         ~5           ~680
9. nsim link: home -> owner                       405          ~1085
10. PDES sync alignment (2x)                      0-200        ~1285
11. Owner processes RECALL, sends RecallResp      ~10          ~1295
12. nsim link: owner -> home                      405          ~1700
13. PDES sync alignment (2x)                      0-200        ~1900
14. Home receives RecallResp, creates GRANT       ~5           ~1905
15. nsim link: home -> requester (GRANT)          405          ~2310
16. PDES sync alignment (2x)                      0-200        ~2510
17. Requester processes GRANT, sends Clear         ~5           ~2515
18. nsim link: requester -> home (Clear)          405          ~2920
19. PDES sync alignment (2x)                      0-200        ~3120
20. Home processClear, commit, replay             ~5           ~3125
```

### 3.3 Summary

| Component | Best case | Worst case | % of total |
|-----------|-----------|------------|------------|
| Network hops (4x 405ns) | 1620ns | 1620ns | **52%** |
| PDES sync alignment (8 boundaries) | 0ns | 800ns | **0-26%** |
| Gem5 internal processing | ~100ns | ~200ns | **3-6%** |
| Clear RTT (legs 4+5) | 810ns | 1210ns | **26-39%** |

**The network hop latency (405ns per hop) dominates.** PDES sync alignment is
the second largest contributor, adding 0-100ns per sync boundary crossing.

### 3.4 Critical path optimizations (ranked by impact)

| # | Optimization | Latency saved | Complexity | Notes |
|---|-------------|---------------|------------|-------|
| 1 | **Eager Commit (eliminate Clear)** | ~810-1210ns (26-39%) | Low-Medium | Removes 2 hops + 2 sync boundaries. Biggest single win. |
| 2 | **Push-grant (already implemented)** | ~0-200ns | Done | Eliminates requester retry timer wait. Already in code at `UBCCController.cc:2783`. |
| 3 | **Reduce cross-node latency** | Proportional | External | Depends on physical network. 405ns is a design parameter for the simulated interconnect. |
| 4 | **Reduce syncInterval** | Up to 800ns total | Config only | `EP_SYNC_INTERVAL_PS=50000` (50ns). Tradeoff: more IPC overhead, slower wall-clock sim. |
| 5 | **Owner-direct-forward** | ~810ns (skip home on data path) | High | Owner sends data directly to requester (like MOESI forward). Requires protocol redesign. |
| 6 | **Speculative grant** | ~405ns | High | Home grants before RECALL completes, using stale data + invalidation in parallel. Complex correctness. |

### 3.5 For TC98 specifically

TC98's 16-way single-PA contention means 256 serial operations. The total time is:
- Current: 256 * ~2.5us (avg) = **~640us** protocol time, amplified by PDES to >>1800s
- With Eager Commit: 256 * ~1.7us = **~435us** protocol time (32% reduction)
- With MAX_PENDING=16: eliminates ~20us retry penalty per dropped request
- Combined: meaningful improvement but PDES amplification remains the dominant bottleneck

**The real TC98 bottleneck is PDES conservative sync**: 8 gem5 processes doing
lock-step time advancement with 100ns quantum. Each network hop forces a global
barrier. This is a simulation framework limitation, not a protocol design issue.

---

## 4. Batch RS Grant: Modification Complexity Assessment

### 4.1 Concept

When multiple queued requesters want ReadShared (RS) on the same PA, instead of
granting one-at-a-time (each requiring a full RECALL+Clear cycle), grant all RS
requesters simultaneously after the first RECALL completes.

Current flow (RS after RS):
```
Requester_A: RS -> RECALL owner -> GRANT_A -> Clear_A
Requester_B: RS -> (queued) -> replay -> GRANT_B -> Clear_B   # sequential
Requester_C: RS -> (queued) -> replay -> GRANT_C -> Clear_C   # sequential
```

Batch flow:
```
Requester_A: RS -> RECALL owner -> GRANT_A + GRANT_B + GRANT_C -> Clear_A (only)
                                                                   ^ all granted at once
```

### 4.2 What already exists

The code already has **RS merge** logic (`UBCCController.cc:504-516`):
```cpp
// section 6 Q3=C: RS merge RS -- if incoming is RS and queue already has RS, skip
if (isRS && alreadyHasRS) {
    printf("[UBCC-QUEUE] pa=0x%lx action=merge ...");
    return static_cast<UBCC_OuterGrantType>(-1);
}
```

This **deduplicates** RS requests in the queue (only keeps one RS entry). But it
does NOT batch-grant: the deduplicated RS requesters get BUSY and must retry.

### 4.3 Required changes for batch RS grant

**UBCCController changes** (Medium complexity):

1. **`replayPendingRequesters()`** (`UBCCController.cc:2724-2806`):
   Currently dequeues one, calls `processOuterRequest`, breaks if outstanding created.
   Change: after commit to G_S state, scan remaining queue for RS entries, grant
   all of them in a batch without creating individual outstandings.

   ```cpp
   // After first RS grant committed to G_S:
   while (!qit->second.empty()) {
       PendingRequester &next = qit->second.front();
       if (next.reqType == UBCC_OuterReqType::GlobalReadShared) {
           // Direct grant: add to sharers mask, send push-grant, no outstanding
           entry.sharersMask |= (1ULL << next.node);
           sendDirectShareGrant(linePa, next.node, next.socket, entry.epoch);
           qit->second.pop_front();
       } else {
           break;  // RU request: must create outstanding, stop batching
       }
   }
   ```

2. **Directory state update**: The batch grant must update `intendedSharersMask`
   to include ALL batch recipients. Currently each grant updates sharers one-at-a-time.

3. **Clear handling**: With batch RS, who sends Clear? Options:
   - **No Clear needed for shared grants** (in eager commit mode)
   - **First requester's Clear covers all** (batch Clear)
   - **Each requester sends own Clear** (current model, most compatible)

4. **Tombstone**: Each batched grant needs its own tombstone entry for idempotent
   duplicate handling. The existing `std::deque<GrantHandshakeTombstone>` per PA
   supports this.

**EPBackend changes** (Low complexity):
- No changes needed. Each requester independently receives a grant push and
  processes it through `handleGrant()` as before.

**UBAdapter changes** (Low complexity):
- The `sendGrantPush()` path is already used. Just called multiple times.

### 4.4 Complexity assessment

| Component | Lines to change | Risk |
|-----------|----------------|------|
| `replayPendingRequesters()` | ~30-50 lines | Medium: must handle mixed RS/RU queue correctly |
| `processOuterRequest()` RS path | ~10-20 lines (add direct-share-grant helper) | Low: new function, doesn't touch existing paths |
| Directory sharers mask | ~5 lines | Low: just OR in additional bits |
| Clear handling | 0-20 lines (depends on strategy) | Low if using per-requester Clear |
| TLA+ model update | ~20-30 lines | Medium: need to verify batch grant preserves safety |
| **Total** | **~65-120 lines** | **Medium overall** |

### 4.5 Performance impact

For TC98 (all RU/write): **Zero improvement** -- batch RS only helps read-shared contention.

For mixed read workloads (e.g., 16 readers of a hot counter):
- Current: 16 serial grants = 16 * ~2.5us = ~40us
- Batch: 1 RECALL + 1 batch grant = ~1.7us + ~0.5us = **~2.2us** (18x improvement)

### 4.6 Recommendation

Batch RS grant is a good optimization for **read-heavy contention** but does NOT help
TC98 (which is all-write). Implement it as a P2 optimization after Eager Commit (P0)
and MAX_PENDING increase (P1).

---

## 5. Priority-Ordered Action Items

| Priority | Action | Impact on TC98 | Complexity |
|----------|--------|---------------|------------|
| **P0** | Increase `MAX_PENDING_PER_PA` to 16+ | Eliminates retry timer waste (2-5x for high contention) | Trivial (1 line) |
| **P1** | Implement `UBCC_EAGER_COMMIT` | Removes 1 RTT per grant (~26-39% latency reduction) | Medium (~200 lines) |
| **P2** | Batch RS grant | No impact on TC98 (write-only); 10-18x for read contention | Medium (~100 lines) |
| **P3** | Tune `EP_SYNC_INTERVAL_PS` for 8n2s | Reduces PDES alignment overhead | Config-only |
| **P4** | Owner-direct-forward | Removes 1 RTT on data path | High (protocol redesign) |
