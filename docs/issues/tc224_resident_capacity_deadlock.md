# TC224 ResidentDir Capacity Waiter Deadlock

Date: 2026-08-02

Status: resolved; focused formal model and full-scale regression PASS

## 1. Symptom

TC224 runs the C132-HA checkpoint workload with 8,192 dirty active lines and
65,536 pressure lines against a 512-entry spill-policy ResidentDir.  The compact
512/4,096 configuration passes.  The full-scale run stops after 51,386 pressure
lines (78.4 percent) and remains in the following state indefinitely:

```text
dir=512 outstanding=0 tombstones=0 resident_waiters=254
pending_requesters=0 capacity=512 policy=spill
```

The state is unchanged from tick 43,000,000,000 through tick 301,900,000,000 in
`logs/tc224_fullscale_detached_20260801/ubio_n0_s0/stderr.log`.  This is a static
controller liveness failure, not a slow network transaction.

## 2. Workload Trigger

`tests/e2e/workloads/e2e_ha_cgroup_2n1s.c::run_c132` performs:

1. Node 1 writes 8,192 dirty active lines homed at node 0.
2. Node 0 writes 65,536 unique pressure lines to the same home.
3. Cache displacement produces writebacks whose metadata may already have been
   spilled, so reads, writes, metadata fills, metadata spills, and data
   writebacks all compete for the same set-associative ResidentDir.

The direct trigger is a resident miss whose target set is full.  A whole
directory need not be full; `ResidentDir::hasFreeSlotForPa` and `pickVictim`
operate on the target PA's set.

## 3. Deadlock Cycle

The capacity-miss path in `UBCCController::handleResidentMiss` currently:

1. Enqueues the original operation as a `ResidentWaitReason::Capacity` waiter.
2. Attempts eviction only when that call inserted a new waiter.
3. Returns Busy if no entry was removed immediately.

`ResidentDir::pickVictim` skips every pinned way.  A resident entry is derived as
pinned from outstanding operations, pending requesters, resident waiters,
metadata fill/writeback, asynchronous metadata snapshots, and spill-policy dirty
invalid metadata.

The defect that closes the cycle is a lost capacity wakeup:

1. A duplicate retry is not newly enqueued, so it does not retry victim
   selection.  If the first attempt found no victim and no completion remains in
   flight, no event can call the set-local replay path.

A capacity waiter normally targets a non-resident PA and therefore pins no
entry. If that PA becomes resident before the retained operation completes, the
waiter must pin it: victim removal erases the victim PA's waiter queue, which may
contain a deferred writeback payload. An attempted implementation that excluded
all capacity waiters from pinning completed full-scale TC224 but returned zero
for all 17 checkpoint samples, confirming this safety requirement.

The resulting wait graph is:

```text
capacity waiter -> free way -> evictable resident -> pin release
       ^                                             |
       +------------- capacity replay --------------+
```

At the terminal TC224 state there is no outstanding request or backstore
completion owner left to break the cycle.

## 4. Correctness Requirements

The fix must preserve the following distinctions:

- `Capacity` waiters normally have no resident entry to pin. If their PA becomes
  resident while the operation remains retained, they must protect that entry.
- `BackstoreFill` waiters depend on a resident placeholder and must pin it.
- `MetadataWriteback` waiters depend on resident metadata and must pin it.
- Writeback payloads, operation kind, epoch, requester, and request ID must remain
  queued exactly as today.
- A duplicate waiter means the operation is already retained, not that capacity
  progress should stop.
- A full waiter queue means the new operation was not retained and must receive
  Busy/retry behavior, while existing capacity work should still be driven.
- Victim choice remains set-local and must continue to reject entries protected
  by real protocol, fill, writeback, recall, or invalidation state.

## 5. Bounded Fix

The implementation uses the existing bounded waiter queues and set-local replay
mechanism. It makes three focused changes:

1. Replace the enqueue boolean with an explicit result that distinguishes a new
   waiter, a duplicate, and a full queue.
2. Replace the eviction boolean with an explicit result that distinguishes an
   immediately freed slot, asynchronous progress that owns a future completion,
   and no progress.
3. On every capacity miss, including duplicate retries and queue-full retries,
   drive one bounded same-set eviction attempt.  Immediate removal replays the
   target waiter; asynchronous progress relies on its existing completion hook.

`refreshPinnedBit` retains the existing rule that any waiter for an already
resident PA protects that entry. Existing outstanding/fill/writeback/snapshot
and dirty tombstone pin rules remain unchanged.

This keeps memory bounded by the existing limits:

```text
MAX_RESIDENT_WAITERS_TOTAL = 256
MAX_PENDING_PER_PA = 32
```

Each drive performs at most one victim scan over the target set (at most 32 ways)
and at most one bounded waiter replay pass.  No timer polling or unbounded retry
loop is introduced.

## 6. Eviction Result Contract

The eviction result has the following meaning:

- `Removed`: a way was freed synchronously and a waiter may be replayed now.
- `Armed`: a spill, delete, recall, or invalidation was successfully initiated;
  its completion path owns future set-local replay.
- `Blocked`: no way was freed and no new completion owner was created.

Returning `Armed` requires concrete protocol state such as `wbPending`, an
outstanding recall/invalidation, or another explicit completion owner.  It must
not mean that a future periodic scan might happen to make progress.

## 7. Verification Plan

Focused tests cover:

- unrelated-set completion does not replay a capacity waiter;
- matching-set completion releases and replays the waiter;
- a capacity waiter protects its PA if that PA becomes resident before replay;
- duplicate retry drives capacity again instead of becoming a lost wakeup;
- held fill/writeback protocol state remains pinned;
- synchronous removal and asynchronous spill both preserve one-operation
  admission semantics;
- queue limits remain enforced.

Regression runs then cover the existing UBCC/ResidentDir tools, TC224 compact,
and TC224 full scale.  Full-scale acceptance requires all 65,536 pressure lines,
checkpoint recovery completion, matching sampled values, and final draining of
resident waiters and outstanding requests.

## 8. Rejected Alternatives

- Increasing the waiter limit only delays the same cycle.
- Periodically replaying every waiter adds polling overhead and cannot break an
  incorrect pin dependency.
- Removing or weakening resident-waiter pins is unsafe for metadata fill,
  writeback payload retention, and capacity waiters whose PA becomes resident.
- Returning Busy without retaining capacity operations causes retry storms and
  complicates writeback payload ownership.
- Reserving a way permanently reduces associativity and does not repair the
  duplicate-retry lost wakeup.

## 9. Full-Scale Follow-on: Spilled Metadata Query

After the capacity lost-wakeup was fixed, TC224 reached completion but all 17
checkpoint samples read zero. The deadlock had hidden a separate full-scale data
durability defect:

1. A dirty owner evicted an active line after its metadata had spilled from the
   512-entry ResidentDir.
2. EPSNF issued `QueryLineMetaReq` to recover the epoch and owner needed by the
   writeback.
3. `UBCCController::queryLineMeta` checked only ResidentDir and returned
   `found=false`, although H64 contained `G_M`, owner 1, epoch 1.
4. EPSNF consumed the negative response, then polled the same reqId until its
   retry limit and discarded the dirty writeback.
5. The later recall returned no data, and unwritten HomeMemory supplied zero.

The fix keeps `queryLineMeta` read-only but makes the ubio message handler
backstore-aware: a resident miss starts an asynchronous H64 lookup and sends the
original `QueryLineMetaResp` only when that lookup completes. EPSNF treats a
completed negative response as terminal instead of continuing to poll an already
consumed reqId. No epoch is fabricated and no ResidentDir entry is allocated for
the query.

## 10. Async Snapshot Pin Release

The first backstore-aware full-scale run exposed another liveness edge. An
asynchronous metadata snapshot is a derived pin source. Snapshot creation must
refresh the resident pin immediately, and every completion must release that
derived ownership. Previously the epoch-mismatch completion erased
`_asyncWbSnapshots[linePa]` but did not call `refreshPinnedBit` or wake same-set
capacity waiters. Under concurrent recovery reads, stale pinned bits accumulated
until a set had no victim although no snapshot completion remained pending.

`doAsyncWriteback` now refreshes the pin after creating the snapshot. Both the
matching and mismatching ack paths refresh the pin and drive same-set capacity
replay. An epoch mismatch keeps `residentDirty` set, so a later async round or an
explicit spill persists the newer image; it does not retain ownership from the
completed old snapshot.

## 11. Test Results (2026-08-02)

### Passed

- Host-only capacity waiter liveness regression, including same-set wakeup,
  unrelated-set isolation, waiter pin safety, and duplicate-retry drive.
- H64 host production suite: 15/15 tests.
- Joint H64 Bloom rebuild regression.
- ResidentDir eviction micro-test.
- Production `build/bin/ubio` build.
- `gem5/build/ARM/gem5.opt` rebuild after EPSNF changes.
- TC224 compact 512 active / 4,096 pressure / stride 64:
  `logs/20260802_010032_2n1s_20260802_010032_2881116`, verifier PASS.

### Full-scale progression

1. Capacity lost-wakeup fix alone completed 8,192/65,536 but returned zero for
   all 17 checkpoint samples. This exposed spilled-metadata QLM data loss.
   Log: `logs/20260802_003556_2n1s_20260802_003556_2871525`.
2. Backstore-aware QLM preserved 2,087 dirty-owner writebacks with no
   `EPSNF-WB-FATAL`, and recovery returned correct data through PA
   `0x10410b40` (1,070 completed active-line reads). It then reached a new stable
   state: `dir=512 outstanding=19 resident_waiters=42`.
   Log: `logs/20260802_010118_2n1s_20260802_010118_2881460`.
3. The async snapshot pin symmetry fix produced the same deterministic terminal
   state and recovery stop point, so it was not the remaining root cause.
   Log: `logs/20260802_070942_2n1s_20260802_070942_3000041`.

Both final runs remained unchanged through approximately tick 703,600,000,000
and hit the 21,600-second total timeout. Full-scale TC224 therefore remains FAIL.

## 12. Remaining Blocker

The original zero-outstanding capacity deadlock is removed: the final state now
contains 19 live outstanding operations, and dirty data is no longer silently
dropped. The remaining blocker is a second liveness cycle reached during
checkpoint recovery after 1,070 active-line reads. The next request for
`0x10410b80` is retained as a capacity waiter while 19 prior outstanding entries
and 42 resident waiters keep the full 512-entry directory from admitting it.

The current state log does not expose outstanding operation type/stage or pin
reason per set, so the remaining cycle cannot be assigned safely from aggregate
counts alone. The next implementation step is diagnostic, not another timeout:

1. Extend the liveness dump with outstanding `(PA, opType, stage, target,
   reqId)` and per-way pin reasons for sets with capacity waiters.
2. Capture the 19 outstanding owners at the first unchanged-state interval.
3. Add a focused regression for the resulting exact wait graph before changing
   pin, recall, or grant semantics.

This distinction matters: weakening waiter pins previously allowed completion
but lost data, while retaining dirty writebacks exposed the deeper live-
outstanding cycle. The remaining fix must preserve both properties.

## 13. Stable-Block Diagnostic Run (1.5 Hours)

Run:

```bash
TIMEOUT_SEC=5400 E2E_STALL_TIMEOUT_SEC=5400 \
  bash tests/e2e/run_multi.sh --2n1s 224
```

Log:

`logs/20260802_133541_2n1s_20260802_133541_3120957`

The added stable-state dump fired at tick 52,200,000,000 after 20 unchanged
heartbeat samples. It proved:

- all 19 outstanding entries are `GRANT_HANDSHAKE / WAITING_CLEAR`;
- all requesters are node 0;
- all 19 conflicting one-way sets are pinned only by their outstanding entry;
- no conflicting way is pinned by a pending requester, resident waiter,
  async snapshot, fill, metadata writeback, or dirty tombstone;
- each handshake is approximately 1.95-2.26 billion ticks old when dumped;
- the full aggregate state remains `dir=512, outstanding=19,
  resident_waiters=42` through the 5,400-second timeout.

Request correlation for representative reqIds `66095` and `66145`, and then
for all 19 entries, shows the exact lifecycle:

1. Recall completes and UBCC creates the first grant.
2. Node 0 sends Clear; Home0 receives and accepts it.
3. Home0 commits epoch 2, retires the grant to a tombstone, and removes the
   outstanding entry.
4. `replayResidentWaiters` later replays a stale waiter with the same
   `(PA, requester, reqId)`.
5. After the first tombstone expires, the stale waiter creates a second
   `GRANT_HANDSHAKE`, now reserving epoch 3.
6. UBCC pushes the second grant, but the requester Clear cache recognizes the
   already-completed reqId and returns its cached result without transmitting a
   second Clear.
7. The second handshake remains `WAITING_CLEAR` forever and pins the only way
   in its set. Capacity waiters mapped to those sets can no longer progress.

The remaining fix should therefore retire matching resident waiters when a
Clear commits, or reject a replay whose operation tuple has already committed.
It must not rely solely on the short-lived tombstone window, because a retained
waiter can replay after that window expires. A focused regression should keep a
same-tuple waiter across the first Clear/tombstone expiry and assert that no
second `GRANT_HANDSHAKE` is created.

## 14. Resolution

The Clear commit path now retires only resident Read waiters matching the
committed `(PA, requester node, requester socket, reqId)` tuple. Legacy
`reqId==0` matching additionally requires the original base epoch. Different
requesters, sockets, reqIds, and non-Read waiters remain queued, preserving
writeback payload ownership.

Validation completed on 2026-08-02:

- host capacity waiter lifecycle regression: PASS;
- H64 production host suite: 15/15 PASS;
- joint H64 Bloom rebuild regression: PASS;
- production `build/bin/ubio`: rebuilt successfully;
- TC224 compact 512/4096: PASS at
  `logs/20260802_163401_2n1s_20260802_163401_3156834`;
- TC224 full-scale 8,192/65,536: PASS at
  `logs/20260802_163502_2n1s_20260802_163502_3157238`;
- all 17 checkpoint samples matched and the C132-HA verifier passed;
- the full-scale run emitted 6,104 committed-waiter retirement events,
  including the previously blocked reqIds 66095 and 66145, with no stable
  `WAITING_CLEAR` block diagnostic.
