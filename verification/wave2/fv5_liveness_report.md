# FV-5: Liveness — Prove No Permanent Deadlock (Static Analysis)

**Analysis date**: 2026-06-22
**Method**: Static path tracing of wait-for cycles across UBCCController, EPRNFController, and UBRouter.
**Assumptions**: Fair retry at EPBackend layer; reliable message delivery within interconnect (loss only on full buffers, which retry).

---

## 1. Per-Wait-Point Progress Analysis

### 1.1 UBCCController Wait Points

| Wait Point | Blocked By | Unblock Condition | Retry Mechanism | Verdict |
|---|---|---|---|---|
| `processOuterRequest` — new requester blocked by active outstanding (L431-523) | Existing `OutstandingRequest` with non-terminal stage (RECALL/INVALIDATE/GRANT_HANDSHAKE/UPGRADE_PENDING) | Existing op completes → `removeOutstanding()` + `replayPendingRequesters()` | Enqueued in `_pendingRequesters[linePa]` (L511); replayed by `replayPendingRequesters()` after Clear (L2129), UpgradeDone commit (L1976), or early UPGRADE completion (L1400). Chained replay (L2558-2566) ensures queue drains one-at-a-time. | ✅ Progress guaranteed: each Clear/UpgradeDone triggers replay of exactly one pending requester. Chain bounded by queue depth. |
| `processOuterRequest` — same requester blocked by own active outstanding (L466-472) | Own `OutstandingRequest` with non-terminal stage | That outstanding completes → `removeOutstanding()` | Requester must retry from EPBackend layer (isRetry flag at EPBackend L567-588). On retry, either `replayArmed` hit (L447-465) or fresh path. | ✅ Progress: retry driven by EPBackend `_requesterLines` state `R_WAIT_GRANT`. Requester re-uses same reqId for tuple matching. |
| **RECALL `WAITING_TARGET_RESP`** (L865-866) | Owner node — must send RecallResp with data | `processRecallResponse()` (L1069) — matches (linePa, targetNode, epoch, reqId) | None in UBCC. Relies on reliable message delivery via UBRouter. | ⚠️ **FV3-LEAK-001**: If recall response is lost or owner never responds, RECALL stays `WAITING_TARGET_RESP` forever. No timeout → permanent block. See §4. |
| **RECALL `DONE`** (orphan, L1140-1141) | Requester — must retry to consume the DONE entry (L714-778) | Same-requester retry hits L714-778 → `removeOutstanding(RECALL)`, creates GRANT_HANDSHAKE | EPBackend-driven retry (L567-588). If EPBackend already completed (e.g., got BUSY and gave up), no retry comes. | ⚠️ **FV3-LEAK-001**: Orphan RECALL.DONE permanently blocks PA slot. No cleanup path (CANCELLED/TIMED_OUT never assigned). See §4. |
| **INVALIDATE `WAITING_ALL_ACKS`** (L658-677) | Sharers in `targetMask` — each must send InvalidateAck | All acks arrive → `pendingAckCount == 0` (L1334-1335) → all-acks-done handling (L1337-1428) | None in UBCC. Relies on reliable message delivery. INVALIDATE→GRANT_HANDSHAKE conversion at L1410-1413. | ✅ Progress: bounded by number of sharers (`__builtin_popcountll(targetMask)`). Each ack decrements count. Monotonic ackMask prevents double-count (L1281-1287). |
| **GRANT_HANDSHAKE `WAITING_CLEAR`** (L559-915) | Requester — must send Clear after receiving grant | `processClear()` (L1985) — matches (linePa, baseEpoch, reqId, requesterNode, stage) | EPBackend `sendClear()` always sent after grant (L830). Tombstone window W for duplicate Clear replay (L2254-2265). | ✅ Progress: Clear is always sent by EPBackend (L830) after `handleGrant()`. The only stall is if Clear messages are lost — handled by tombstone replay (L2000-2014). |
| **UPGRADE_PENDING `WAITING_ALL_ACKS`** (L1836-1854) | Non-requester sharers — each must send InvalidateAck; plus Done from requester | All acks arrive → `upgradePendingAckCount == 0` → `WAITING_LOCAL_DONE`; then `processOuterUpgradeDone` (L1879) | Early Done caching (L1915-1932): if Done arrives before all acks, cached and auto-committed when acks complete (L1383-1401). | ✅ Progress: two independent conditions; TENTATIVE caching prevents ordering deadlock. |
| **UPGRADE_PENDING `WAITING_LOCAL_DONE`** (L1857-1871) | Requester — must send OuterUpgradeDone | `processOuterUpgradeDone()` (L1879) — matches requester, epoch, reqId | UpgradeAckNotify sent via router (L1366-1380) triggers EPBackend to send UpgradeDone. | ✅ Progress: UpgradeAckNotify routed reliably; EPBackend `receiveUpgradeAck()` (EPRNF L1350) sends UpgradeDone. |
| Clear epoch/reqId mismatch → stale retire (L2052-2066) | Stale GRANT_HANDSHAKE from earlier epoch | Mismatch detected → `retireToTombstone(false)` + `removeOutstanding()` | Tombstone records the rejection for window W (L2262). Requester retries with fresh epoch. | ✅ Progress: stale entries are retired, not leaked. |
| Clear for missing GRANT_HANDSHAKE (L2037-2047) | No GRANT_HANDSHAKE outstanding | — | Logged and dropped. Requester will retry with fresh reqId. | ✅ Progress: idempotent drop. |
| Pending requester queue full (L516-521) | `_pendingRequesters[linePa]` at `MAX_PENDING_PER_PA` | Queue must drain first | None — request silently dropped. Requester must retry from EPBackend. | ⚠️ **Potential starvation if retry rate > drain rate.** Queue depth finite (MAX_PENDING_PER_PA). See §2. |

### 1.2 EPRNFController Wait Points

| Wait Point | Blocked By | Unblock Condition | Retry Mechanism | Verdict |
|---|---|---|---|---|
| Queued snoop (1-entry slot) while CHI txn in-flight (L349-368) | In-flight CHI transaction for same `linePa` | `finishChiTxn()` (L899) → `processQueuedSnoop()` (L933-934) | Automatic: `finishChiTxn` calls `processQueuedSnoop` after erasing completed txn (L921-935). | ✅ Progress: snoop slot processed immediately after CHI txn completes. |
| Deferred CHI request while `_chiRequestInFlight` (L953-963) | In-flight CHI request (any PA) | `finishChiTxn()` → `_chiRequestInFlight = false` (L930) → `processDeferredChiReqs()` (L941) | Automatic: `processDeferredChiReqs` drains queue one-at-a-time (L1073-1084), maintaining single-flight. | ✅ Progress: each `finishChiTxn` processes the deferred queue. |
| CompAck send failure (L1022-1024, L1048-1061) | Full `rspOut` buffer | Buffer space becomes available | `retryPendingCompAcks()` (L1029-1067) schedules event (Cycles(1)) on failure. Called from `wakeup()` (L305) and `finishChiTxn` (L938). | ✅ Progress: event-driven retry; cannot starve because `scheduleEvent` is called with bounded backoff. |
| Retry queue entries with CHI txn already in-flight (L1315-1320) | In-flight CHI txn for same PA | That CHI txn completes → `finishChiTxn` → `processRetryQueue()` (L938) | Automatic via `finishChiTxn` callback. | ✅ Progress: each finishChiTxn retries one entry. |
| Stale retry epoch discarded (L1265-1271) | Retry entry with epoch < current | Newer retry replaces; stale is ignored | Requester must create a new CHI request with updated epoch. | ✅ Progress: epoch monotonicity prevents infinite stale retries. |
| `pendingHnResponse` waiting for outer txn complete (L560-565) | Outer transaction to UBCC not yet finished | `signalOuterTxnComplete()` — called from EPBackend (L771, L835) on grant/BUSY | EPBackend always signals completion after UBCC returns, whether grant or BUSY. | ✅ Progress: UBCC always returns a result (grant or <0). |

### 1.3 UBRouter Wait Points

| Wait Point | Blocked By | Unblock Condition | Retry Mechanism | Verdict |
|---|---|---|---|---|
| Message in queue not yet ready (hasReady check L160) | Time-based latency (forcedLatency or defaultLatency) | `curTick() >= readyTick` | `drainReadyQueues` rescheduled if pending messages remain (L260-265). | ✅ Progress: bounded latency; drain rescheduled every tick until empty. |
| Drain budget exhausted (maxDrainPerWakeup=128, L152-155) | Per-wakeup throughput cap | Next tick reschedule (L265) | `schedule(_drainEvent, curTick() + 1)` | ✅ Progress: bounded drain per tick prevents live-lock of event loop. |
| Queue full (UBMsgQueue has finite capacity) | Downstream consumer not draining | Downstream consumer drains messages | Message send fails; caller must retry (EPBackend/UBCC can re-queue). | ⚠️ Requires caller-level retry. See §2. |

---

## 2. Identified Permanent-Starvation Scenarios

### S-001: RECALL.DONE Orphan (FV3-LEAK-001 cross-reference)
**Severity**: Medium | **Type**: Live-slot leak → permanent PA block

**Scenario**:
1. Requester A sends ReadUnique to G_E/G_M line → UBCC creates RECALL outstanding (L859-870), sends RecallReq to owner.
2. Owner responds with RecallResp → UBCC advances RECALL to `DONE` (L1140-1141).
3. Requester A never retries (e.g., EPBackend already forwarded data via recall proxy and completed the line, or EPBackend crashed/timed out).
4. RECALL.DONE remains in `_outstandingReqs[linePa]` indefinitely.
5. No new outstanding can be created for this PA (`createOutstanding` returns nullptr at L2594 if key exists).
6. Requester B arrives → enqueued in `_pendingRequesters` (L783-835 path for G_E/G_M state).
7. B's enqueued entry is **never replayed**: `replayPendingRequesters` is only called after Clear or UpgradeDone commit, both of which require a GRANT_HANDSHAKE or UPGRADE_PENDING outstanding — which can't be created.

**Root cause**: `TIMED_OUT`/`CANCELLED` stages are defined (L73-75) but **never assigned** (FV3-DEAD-001). No epoch-stall detection or watchdog timer exists for orphan DONE entries.

**Trigger probability**: Low. Requires RECALL to complete but retry to be lost or skipped. The `R_WAIT_GRANT` state in EPBackend should trigger retry on the next wakeup cycle.

**Mitigation in code paths**:
- EXISTS: Different-requester path (L783-835) correctly enqueues rather than failing.
- MISSING: No replay mechanism for pending requesters when DONE is terminal and not consumed.

### S-002: Pending Requester Queue Overflow
**Severity**: Low | **Type**: Silent drop

**Scenario**:
1. Multiple requesters contend for the same PA with an active outstanding.
2. `_pendingRequesters[linePa]` reaches `MAX_PENDING_PER_PA`.
3. New requesters are dropped silently (L516-521).
4. Dropped requesters must retry independently from EPBackend.

**Risk**: If retry rate exceeds drain rate for extended period, drops cascade. However, the replay mechanism is per-Clear (one replay per commit), and each EPBackend must wait for a grant response before re-issuing. Practical contention bound is low.

### S-003: Deferred CHI Request Deadlock (EPRNF)
**Severity**: Low | **Type**: Forward progress stall

**Scenario**:
1. `sendChiRequest(L1)` sets `_chiRequestInFlight = true`.
2. `sendChiRequest(L2)` tries to send → deferred (L953-963).
3. HN-F for L1 crashes or never responds → `finishChiTxn` never called.
4. `_chiRequestInFlight` stays true permanently → all deferred requests starve.

**Mitigation**: HN-F is a reliable component in the gem5 model. No real HN-F crash scenario. In simulation, this is not a concern.

---

## 3. Cross-PA Mutual Exclusion Analysis

No cross-PA dependencies exist in any of the analyzed code paths. Each PA's outstanding request state machine, pending requester queue, and EPRNF transaction state are independently keyed by `linePa`. The UBCC router processes each message independently across different PAs. There is no A-waits-B / B-waits-A cycle across different addresses.

| Dependency Type | Evidence | Deadlock Risk |
|---|---|---|
| Same PA, intra-request | Single-flight: one outstanding per PA | ✅ Designed |
| Same PA, inter-requester | Enqueue + replay pattern | ✅ Linearized |
| Cross-PA | No shared locks, no cross-PA backpressure | ✅ None |
| Router queues | Per (src,dst) pair; independent drain | ✅ None |
| CHI request in-flight | Per-PA via `_pendingChiTxns` map | ✅ Single-flight per PA |

---

## 4. FV3-LEAK-001 Cross-Reference

**FV3-LEAK-001** (from FV-3 report, §2 finding L151):

> `RECALL.DONE` entries are never cleaned up if the requester does not retry. `TIMED_OUT`/`CANCELLED` defined but never assigned — no timeout mechanism for orphan RECALLs.

**Liveness impact under FV-5**:
- When RECALL.DONE is orphaned, the PA slot is **permanently blocked**.
- No GRANT_HANDSHAKE can be created → no Clear can happen → no `replayPendingRequesters` can fire.
- Pending requesters queued during the orphan period are **permanently starved**.
- This is the only identified permanent-starvation scenario in the analyzed code paths.

**Affected test case correlation** (from `tc_uncovered_negative_fault.md`):
- TC `tc_recall_done_ge_owner_evict_leak` (E#37)
- TC `tc_recall_done_gm_owner_wbdrop_leak` (E#38)

Both test cases cover the scenario where RECALL.DONE is reached but the requester does not follow up, leaking the slot.

**Recommendation**: Implement an epoch-stall detector or periodic sweep that transitions orphan RECALL.DONE entries to `CANCELLED` after a timeout, freeing the slot.

---

## 5. Dead-Enum Cross-Reference

| Enum Value | Defined | Assigned | Impact on Liveness |
|---|---|---|---|
| `OpStage::CANCELLED` | L74 | Never (FV3-DEAD-001) | No cleanup path for orphan RECALL.DONE |
| `OpStage::TIMED_OUT` | L75 | Never (FV3-DEAD-001) | No timeout eviction for stalled WAITING_TARGET_RESP |
| `OpStage::PERSISTENT_BUSY` | L76 | Never (FV3-DEAD-001) | Used only as exclusion in `isLineBusy`; no impact |

---

## 6. Summary

| ID | Category | Verdict | Description |
|---|---|---|---|
| FV5-WAIT-001 | UBCC: Outstanding → replay | ✅ Progress | Chained replay per Clear/UpgradeDone |
| FV5-WAIT-002 | UBCC: RECALL → owner response | ⚠️ **Orphan risk** | No timeout for WAITING_TARGET_RESP (FV3-LEAK-001) |
| FV5-WAIT-003 | UBCC: INVALIDATE → sharer acks | ✅ Progress | Bounded ack count, monotonic mask |
| FV5-WAIT-004 | UBCC: GRANT_HANDSHAKE → Clear | ✅ Progress | Clear always sent by EPBackend |
| FV5-WAIT-005 | UBCC: UPGRADE → acks + Done | ✅ Progress | TENTATIVE Done caching prevents ordering deadlock |
| FV5-WAIT-006 | EPRNF: Queued snoop | ✅ Progress | Processed immediately after CHI txn done |
| FV5-WAIT-007 | EPRNF: Deferred CHI req | ✅ Progress | Drained one-per-finishChiTxn |
| FV5-WAIT-008 | EPRNF: CompAck retry | ✅ Progress | Event-driven with bounded backoff |
| FV5-WAIT-009 | Router: queue drain | ✅ Progress | Bounded per tick, rescheduled |
| FV5-STARVE-001 | **Permanent starvation** | ⚠️ **FV3-LEAK-001** | RECALL.DONE orphan blocks PA forever |
| FV5-STARVE-002 | Queue overflow drop | Low risk | Silent drop, EPBackend retry independently |
| FV5-DEAD-001 | Cross-PA deadlock | ✅ None | No cross-PA dependencies exist |
