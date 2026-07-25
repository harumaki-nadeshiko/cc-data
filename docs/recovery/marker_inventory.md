# Marker Inventory & Ownership — Phase 4 Logging Governance

**Date**: 2026-07-24
**Status**: Phase 4 implementation inventory
**Scope**: UBCCController, EPBackend, EPSNFController, UBAdapter, EPRNFController, MetaRNFController
**Authority**: `docs/design/resident_backstore_rearchitecture_plan.md` Phase 4 + `docs/design/scheme_v4.md` §3.6

---

## 1. Categorization Legend

| Category | Tag | Default | Gating | Description |
|---|---|---|---|---|
| **Correctness** | `CORR` | **Always ON** | None (or framework gated) | Fault evidence, invariant violations, mandatory audit logs |
| **Latency/Perf** | `PERF` | **Always ON** | None | TRACE-PERF pipeline, latency measurements |
| **Stats** | `STAT` | **Always ON** | None | Periodic stats dumps consumed by CI/analysis |
| **Test Evidence** | `TEST` | **Always ON** | None | Markers consumed by verify.py / test_e2e.py for test assertions |
| **Debug/Diagnostic** | `DEBUG` | **OFF by default** | `_debugLog`, `_debugClearTrace`, or `_verboseLog` | Developer debugging, hot-path instrumentation |
| **Operational** | `OP` | **Always ON** | Framework (`LogInfo`/`warn`/`fatal`) | Lifecycle events, warnings, errors |

## 2. Invariant I14

> **I14**: Every debug-only log starts `[DEBUG-` and is disabled by default.

Implementation: `bool _verboseLog = false;` gates all `[DEBUG-` prefixed markers. Already-existing `_debugLog` and `_debugClearTrace` continue to gate their respective subsets. The convention for new debug markers is:
- Prefix: `[DEBUG-<module>-<detail>]`
- Gate: `if (_verboseLog)` (or a specialized gate like `_debugClearTrace`)
- Stream: `stderr` (via `fprintf`)

---

## 3. UBCCController — `/mnt/data2/cgc/cc-ep/modules/ubiomodule/UBCCController.*`

### 3.1 Correctness / Operational (Always ON)

| Marker | Stream | File:Line | Gate | Consumer |
|---|---|---|---|---|
| `[UBFAULT]` | stderr | UBCCController (via fault injection) | None | test_e2e.py TC47-49 |
| `[UBCC-NAIVE-EVICT]` | stdout | UBCCController.cc:491 | None | test_e2e.py naive_count, verify.py |
| `[UBCC-NAIVE-EVICT-DONE]` | stderr | UBCCController.cc (implied) | None | verify.py, test_e2e.py |
| `[UBCC-OUTER-REQ]` | stdout | UBCCController.cc:886,962 | None | verify.py evidence |
| `[UBCC-WB-ENTER]` | stdout | UBCCController.cc:2336 | None | Operational |
| `[UBCC-WB-REQ]` | stderr | UBCCController.cc:2338 | None | verify.py evidence |
| `[UBCC-HOME-WB]` | stdout | UBCCController.cc:2476,2491,2497 | None | Operational |
| `[UBCC-ASYNC-WB]` | stdout | UBCCController.cc:653,656 | None | Operational |
| `[UBCC-UPGRADE-COMMIT]` | stdout | UBCCController.cc:2187,2884 | None | test_e2e workload comment |
| `[UBCC-NAIVE-DIRTY-RECALL-HOLD]` | stderr | UBCCController.cc:515 | None | Operational |

### 3.2 Stats (Always ON)

| Marker | Stream | File:Line | Gate | Consumer |
|---|---|---|---|---|
| `[UBCC-STATS]` | stdout | UBCCController (serialized) | None | verify.py, test_e2e.py, evaluate_capacity_latency.py |
| `[ResidentDirStats]` | stdout | UBCCController (serialized) | None | verify.py, test_e2e.py |

### 3.3 Test Evidence / Debug

Markers consumed by `verify.py` or `test_e2e.py` remain always on. The
remaining markers in this section are debugging-only and are gated behind
`_verboseLog` (default `false`).

| Marker Prefix | Approx Lines | Description |
|---|---|---|
| `[RESIDENT-MISS]` | 226 | Test evidence: resident directory miss |
| `[RESIDENT-MISS-BUSY]` | 252 | Capacity-full busy |
| `[RESIDENT-MISS-READY]` | 277 | Miss resolved ready |
| `[RESIDENT-FILL-ISSUED]` | 298 | Test evidence: backstore fill issued |
| `[RESIDENT-FILL-DONE]` | 3498 | Test evidence: backstore fill completed |
| `[RESIDENT-WAITER-DROP]` | 319,326 | Waiter dropped (queue full) |
| `[RESIDENT-WAITER-DEDUP]` | 340,347 | Waiter deduplicated |
| `[RESIDENT-WAITER-ENQ]` | 356 | Test evidence: waiter enqueued |
| `[RESIDENT-WAITER-REPLAY]` | 698 | Test evidence: waiter replayed |
| `[RESIDENT-WAITER-REPLAY-UPGRADE-*]` | 745,750 | Upgrade replay specifics |
| `[RESIDENT-EVICT-PICK-FAIL]` | 393 | Eviction pick failure |
| `[RESIDENT-EVICT-PICK]` | 399 | Eviction pick success |
| `[RESIDENT-SPILL-START]` | 453 | Test evidence: spill eviction start |
| `[RESIDENT-SPILL-DONE]` | 3572 | Test evidence: spill eviction complete |
| `[RESIDENT-REPLAY-PUSH]` | 796 | Test evidence: replay push grant |
| `[RESIDENT-CAPACITY-REPLAY]` | 838 | Capacity replay |
| `[UBCC-QUEUE]` | 1038,1062,1077,1409,1431,1438 | Request queuing actions |
| `[UBCC-RECALL-WAIT]` | 1069 | Recall wait state |
| `[UBCC-GSRS-FAST]` | 1196 | G_S+RS fast path |
| `[UBCC-SHARER-UPGRADE]` | 1211 | Sharer upgrade path |
| `[UBCC-INVALIDATE-CREATE]` | 1218 | Invalidation created |
| `[UBCC-INVALIDATE-EMPTY]` | 1263 | Empty invalidation set |
| `[RECALL-CREATE]` | 1449 | Recall created |
| `[UBCC-GRANT-READY]` | 1566 | Grant ready for dispatch |
| `[RECALL-TRACE-A]` | 1740 | Recall trace |
| `[RECALL-DIAG]` | 1760,1782 | Recall diagnostic |
| `[RECALL-TO-GRANT]` | 1942 | Recall→Grant transition |
| `[PUSH-GRANT]` | 1276,1933,2224,3872,3910 | Push grant dispatch |
| `[UBCC-INV-ACK]` | 2108 | Invalidation ack received |
| `[UBCC-INV-DONE]` | 2127 | Invalidation complete |
| `[UBCC-UPGRADE-ACK]` | 2141 | Upgrade acknowledge |
| `[UPGRADE-TENTATIVE-DONE-CACHED]` | 2175,2840 | Tentative upgrade completion |
| `[UBCC-INV-TO-GRANT]` | 2231 | Invalidate→Grant transition |
| `[UBCC-UPGRADE]` | 2764,2781 | Upgrade stage tracking |
| `[UBCC-QUEUE-REPLAY]` | 3814 | Queue replay entry |
| `[UBCC-QUEUE-REPLAY-BATCH]` | 3827 | Batch queue replay |

### 3.4 Already-Gated Debug Markers

| Marker | Gate | Lines |
|---|---|---|
| `[DEBUG-H64-DSM-*]` | `_debugLog` | 922,929,945,3197,3221 |
| `[BACKSTORE-H64-*]` | `_debugLog` | 3639,3664,3678,3683,3695 |
| `[DEBUG-TC5-CLEAR-TRACE]` | `_debugClearTrace` | 2961,2979,2998,3021,3102 |
| `[DEBUG-UBCC-CLEAR]` | `_debugClearTrace` | 2949,2983,3003,3025,3112 |
| `[DEBUG-UBCC-ORDER]` | `_debugClearTrace` | 3108 |

---

## 4. EPBackend — `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.*`

### 4.1 Correctness / Operational (Always ON)

| Marker | Stream | Category |
|---|---|---|
| (framework::warn, fatal, inform) | simout | OP |
| Test-inspection accessors (no markers) | — | TEST |

### 4.2 Latency/Perf (Always ON)

| Marker | Stream | File | Gate |
|---|---|---|---|
| `[TRACE-PERF]` | stderr | UBAdapter.cc:214,1562 | None (critical perf pipeline) |

### 4.3 Debug/Diagnostic (Gated — `_verboseLog`)

All markers below fire on EP-side request processing. Gating them behind `_verboseLog` prevents hot-path log flooding.

| Marker Prefix | File | Description |
|---|---|---|
| `[RECALL-ENTRY]` | EPBackend.cc:1113 | Recall entry |
| `[RECALL-DIAG]` | EPBackend.cc:1150,1194,1204,1264,1281,1390,1430 | Recall diagnostics |
| `[RECALL-RESP]` | EPBackend.cc:1348 | Recall response sent |
| `[RECALL-RESP-DETAIL]` | EPBackend.cc:1350 | Recall response detail |
| `[C4-FORWARD]` | EPBackend.cc:1255,1331 | C4 data forward |
| `[UPGRADE-DIAG]` | EPBackend.cc:552,2036 | Upgrade diagnostic |
| `[INVAL-DIAG]` | EPBackend.cc:1711,1818,1841,1846 | Invalidation diagnostic |
| `[RE-DIAG]` | EPBackend.cc:892,1440,1448,1612 | Requester entry diagnostic |
| `[EP-HOME-WB]` | EPBackend.cc:1460 | Home writeback |
| `[EP-HANDLE-WB]` | EPBackend.cc:1572 | Writeback handling |
| `[EP-HOME-WB-NOTIFY]` | EPBackend.cc:2355 | Writeback notify |
| `[EP-QLM-RESP]` | EPBackend.cc:2413 | QLM response |
| `[WIRE]` | EPBackend.cc:177 | Wiring event |
| `[RSP-FIRE]` | EPBackend.cc:180,254 | Response firing |
| Other unlabeled fprintf | EPBackend.cc:various | Miscellaneous debug |
| `[DEBUG-UPGRADE-*]` | EPBackend.cc:555 | Already partially gated |

### 4.4 UBAdapter Diagnostics (Gated)

| Marker Prefix | File | Description |
|---|---|---|
| `[GEM5-SEND]` | UBAdapter.cc:211 | Gem5 send event |
| `[CLR-CACHE-HIT]` | UBAdapter.cc:737 | Clear cache hit |
| `[CLR-CACHE-MISS]` | UBAdapter.cc:747 | Clear cache miss |
| `[CLR-TX]` | UBAdapter.cc:774 | Clear transmit |
| `[ADAPTER-GOT-RESP]` | UBAdapter.cc:1192 | Adapter response |
| `[WAKEUP-NONCOH]` | UBAdapter.cc:1537 | Noncoherent wakeup |
| `[WAKEUP-COH]` | UBAdapter.cc:1570 | Coherent wakeup |
| `[RSP-WIRED]` | UBAdapter.cc:1707,1804 | Response wiring |

---

## 5. EPSNFController — `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.*`

| Marker | Category | Notes |
|---|---|---|
| All fprintf(stderr, ...) calls | DEBUG | All are diagnostic; gated via `_verboseLog` |

---

## 6. EPRNFController — `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.*`

| Marker | Category | Notes |
|---|---|---|
| All fprintf(stderr, ...) calls | DEBUG | All are diagnostic; gated via `_verboseLog` |

---

## 7. MetaRNFController — `gem5/src/mem/ruby/protocol/chi/ep/MetaRNFController.*`

| Marker | Category | Notes |
|---|---|---|
| fprintf(stderr, ...) at line 124 | DEBUG | Diagnostic; gated via existing mechanism |

---

## 8. Tests & Script Consumers

| Consumer | Markers Required |
|---|---|
| `tests/e2e/verify.py` | `[UBFAULT]`, `[ResidentDirStats]`, `[UBCC-STATS]`, `[UBCC-NAIVE-EVICT]`, `[UBCC-NAIVE-EVICT-DONE]`, `[UBIO-POLICY]`, `[RUNNER-MANIFEST]`, `RESIDENT-WAITER`, `RESIDENT-BACKSTORE-KNOWN`, `RESIDENT-SPILL-START`, `RESIDENT-SPILL-DONE`, `RESIDENT-FILL-ISSUED`, `RESIDENT-FILL-DONE`, `RESIDENT-MISS`, `RESIDENT-REPLAY`, `UBCC-UPGRADE-COMMIT`, `UBCC-OUTER-REQ`, `UBCC-WB-REQ`, `UBCC-UPGRADE`, `UBCC-CLEAR`, `WB-DATA-PERSIST`, `BACKSTORE-WRITE`, `BACKSTORE-READ` |
| `tests/e2e/test_e2e.py` | `[UBCC-NAIVE-EVICT]` (count), `[UBCC-UPGRADE-COMMIT]`, `[UBFAULT]` |
| `scripts/evaluate_capacity_latency.py` | `[UBCC-STATS]` |
| `scripts/trace2chain.py` | `[TRACE-PERF]` |
| `scripts/trace_visualizer.py` | `[TRACE-PERF]` |

**Important**: The verify.py/test_e2e.py consumers pull MARKERS from aggregating log files. These markers are preserved ungated even though some (like RESIDENT-FILL-ISSUED, RESIDENT-SPILL-*) are primarily diagnostic — the test framework uses their presence/absence for verification, making them TEST evidence.

---

## 9. Gate Variables

| Variable | Location | Default | Scope |
|---|---|---|---|
| `_verboseLog` | UBCCController.hh (new), EPBackend.hh (new), EPSNFController.hh (new) | `false` | All `[DEBUG-*]` and general diagnostic markers |
| `_debugLog` | UBCCController.hh:802 | `false` | `[DEBUG-H64-*]` subset |
| `_debugClearTrace` | UBCCController.hh:803 | `false` | `[DEBUG-TC5-CLEAR-TRACE]`, `[DEBUG-UBCC-CLEAR]`, `[DEBUG-UBCC-ORDER]` subset |

---

## 10. Compliance Rules

1. **All new debug markers MUST** start with `[DEBUG-<module>-<detail>]`.
2. **All `[DEBUG-*]` markers MUST** be gated behind a boolean gate variable (never unconditional).
3. **Test-consumed markers** (Section 8) **MUST NOT** be gated or renamed without updating all consumers.
4. **Correctness/Operational markers** (`[UBFAULT]`, `[UBCC-STATS]`, etc.) **MUST** remain always-ON.
5. **Latency pipeline** (`[TRACE-PERF]`) **MUST** remain always-ON.

Automated enforcement: `tests/logging/test_marker_compliance.py`.
