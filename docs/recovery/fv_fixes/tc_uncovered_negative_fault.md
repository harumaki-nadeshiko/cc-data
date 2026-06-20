# TC design for uncovered edges, direct-I coverage, negative paths, and fault injection

## 1. Debug hook infrastructure first

These TCs need two debug-only mechanisms:

1. **UBRouter transport-fault hook** for deterministic `delay` / `drop` / `duplicate` / `reorder`.
2. **Protocol-message injection helper** for malformed messages that normal workloads cannot legally emit (`wrong epoch`, `wrong reqId`, `wrong src`, stale `RecallResp`).

### 1.1 UBRouter transport-fault hook

**Build guard**
- Compile only in debug/test builds, e.g. `#ifdef CC_EP_DEBUG_FAULTS`.
- Disabled by default.

**Hook point**
- At UBRouter enqueue/send path for outer protocol messages.
- Apply after message construction, before network delivery.

**Rule schema**

```cpp
struct DebugFaultRule {
    bool enabled = false;
    std::string ruleId;
    MsgKind msgKind;              // Clear, InvalidateAck, RecallResp, any
    Addr linePa = MaxAddr;
    int srcNode = -1;
    int dstNode = -1;
    int reqId = -1;
    int epoch = -1;
    int nthMatch = 1;             // apply on Nth matching packet
    int repeat = 1;               // number of applications
    FaultAction action;           // Delay, Drop, Duplicate, ReorderHold, ReorderRelease
    Tick delayCycles = 0;         // for Delay
    std::string groupKey;         // for paired reorder rules
};
```

**Supported actions**
- `Delay`: hold matching packet for `delayCycles`, then release unchanged.
- `Drop`: discard matching packet once or `repeat` times.
- `Duplicate`: deliver one original plus one cloned packet.
- `ReorderHold`: buffer packet in a per-`groupKey` FIFO.
- `ReorderRelease`: release buffered packets in reverse or explicit configured order.

**Required observability**
- Log every match:
  - `[UBFAULT] rule=<id> action=<a> kind=<k> pa=<pa> src=<s> dst=<d> epoch=<e> reqId=<id>`
- Per-rule counters: `matched`, `dropped`, `duplicated`, `delayed`, `released`.
- Debug dump endpoint to query buffered packets by `groupKey`.

**Safety rules**
- Match must be exact-once with `nthMatch` semantics.
- Delayed packets must auto-release on simulation teardown.
- Reorder buffer key should include at least `(msgKind, linePa, dstNode)` to avoid cross-line corruption.

### 1.2 Protocol-message injection helper

Needed for malformed packets that transport faults cannot create.

**Recommended API**

```cpp
debugInjectOuterMsg({
    .msgKind = Clear | InvalidateAck | RecallResp | UpgradeReq,
    .linePa = X,
    .srcNode = n,
    .dstNode = h,
    .epoch = e,
    .reqId = id,
    .accepted = true,
    .data = optionalData,
});
```

**Use cases**
- `Clear` with wrong epoch
- `Clear` with wrong reqId
- `Clear` with wrong `srcNode`
- stale `RecallResp` replayed after line already advanced
- non-sharer `UpgradeReq`

**Observability**
- Log injections as `[UBINJECT] ...`.
- Expose result at receiver: accepted/rejected, outstanding retained/retired, tombstone written or not.

---

## 2. Proposed TC set summary

| TC name | Targets | Injection needed |
|---|---|---|
| `tc_recall_done_ge_owner_evict_leak` | E#37 | Yes - timing hold after `RECALL/DONE` |
| `tc_recall_done_gm_owner_wbdrop_leak` | E#38 | Yes - timing hold after `RECALL/DONE` |
| `tc_gs_evict_notlast_then_last` | D#29, D#30 | No |
| `tc_ge_owner_evict_direct` | D#33 | No |
| `tc_gm_dirty_owner_evict_negative` | D#36 | No |
| `tc_non_sharer_upgrade_reject` | E#39 | Yes - debug `UpgradeReq` inject |
| `tc_clear_wrong_epoch_tombstone` | E#40 | Yes - debug `Clear` inject |
| `tc_clear_invalid_reqid_retains_outstanding` | E#41 | Yes - debug `Clear` inject |
| `tc_live_busy_reject_matrix` | E#42 | Yes - deterministic pause/window hook |
| `tc_l0_different_requester_enqueue` | E#44 | Yes - delay first `Clear` |
| `tc_duplicate_invalidate_ack_ignored` | direct duplicate-ack negative on L4/L9 | Yes - dup hook or inject |
| `tc_stale_recall_resp_rejected` | direct stale-response negative on L7/L8 | Yes - debug `RecallResp` inject |
| `tc_fault_delay_clear_then_recover` | fault injection / forward progress | Yes - delay hook |
| `tc_fault_drop_clear_watchdog` | fault injection / liveness sentinel | Yes - drop hook |
| `tc_fault_reorder_invalidate_acks` | fault injection / order-independence | Yes - reorder hook |

---

## 3. Detailed TCs

### TC: `tc_recall_done_ge_owner_evict_leak`

- **Scenario**: direct coverage of `L7 (G_E × RECALL/DONE) + EVICT(owner)` semantic leak.
- **Nodes involved**:
  - Node0 = owner
  - Node1 = requester
  - Node2 = home
- **Protocol steps**:
  1. Node0 acquires `X` in `G_E` on home Node2.
  2. Node1 issues `ReadShared(X)` or `ReadUnique(X)` so home enters `L5`, then accepts `RecallResp` and reaches `L7`.
  3. Hold requester retry so `RECALL/DONE` stays resident.
  4. While home is in `L7`, Node0 issues `EVICT(owner)` on `X`.
  5. Release requester retry or issue a fresh read from Node1.
- **Verification criteria**:
  - Fixed design must not leave stale terminal outstanding behind the evict.
  - Acceptable outcomes:
    - evict is rejected/BUSY while `RECALL/DONE` exists, or
    - terminal outstanding is consumed/retired before stable-state commit.
  - After step 5, Node1 must complete and read correct data.
  - Home must not keep `_outstandingReqs[X] = RECALL/DONE` after directory moved to `G_I`.
  - No deadlock / no permanently queued requester.
- **Injection needed**: **Yes** - deterministic pause between `REC_RESP(match)` and same-requester retry.

### TC: `tc_recall_done_gm_owner_wbdrop_leak`

- **Scenario**: direct coverage of `L8 (G_M × RECALL/DONE) + WB_DROP(owner)` semantic leak without conflating stable dirty-owner-evict behavior.
- **Nodes involved**:
  - Node0 = dirty owner
  - Node1 = requester
  - Node2 = home
- **Protocol steps**:
  1. Node0 acquires `X` in `G_M` and stores `V1`.
  2. Node1 issues `ReadShared(X)` or `ReadUnique(X)`; home reaches `L6`, then `L8` after matching `RecallResp`.
  3. Hold requester retry.
  4. Node0 issues `WB_DROP(owner)` while home is still `L8`.
  5. Release requester retry and complete the pending read.
- **Verification criteria**:
  - Fixed design must not commit stable `G_E/G_I` while stale `RECALL/DONE` remains in `_outstandingReqs`.
  - Requester must complete; no orphan outstanding remains.
  - Returned data must be `V1` or the architecturally correct post-writeback value; never zero/stale pre-write value.
  - No line remains permanently blocked behind terminal recall metadata.
- **Injection needed**: **Yes** - deterministic pause after `RECALL/DONE`.

### TC: `tc_gs_evict_notlast_then_last`

- **Scenario**: direct coverage of both `G_S` evict edges.
- **Nodes involved**:
  - Node0 = sharer A
  - Node1 = sharer B
  - Node2 = home
- **Protocol steps**:
  1. Node0 reads `X` shared.
  2. Node1 reads `X` shared; home now holds sharers `{0,1}`.
  3. Node0 issues `EVICT(X)` -> should remove only sharer 0 (`D#29`).
  4. Verify Node1 still reads `X` without refill anomaly.
  5. Node1 issues `EVICT(X)` -> should transition to `G_I` (`D#30`).
  6. Node0 reads `X` again to prove clean refill from `G_I`.
- **Verification criteria**:
  - After step 3: committed state remains `G_S`, sharers mask contains only Node1.
  - After step 5: committed state becomes `G_I`, sharers mask is zero.
  - No sharer-set corruption, no panic on canonicality checks.
- **Injection needed**: **No**.

### TC: `tc_ge_owner_evict_direct`

- **Scenario**: direct coverage of `N2 (G_E) -> G_I` via `EVICT(owner)`.
- **Nodes involved**:
  - Node0 = exclusive owner
  - Node1 = later reader
  - Node2 = home
- **Protocol steps**:
  1. Node0 gets `X` in `G_E`.
  2. Node0 issues `EVICT(X)`.
  3. Node1 reads `X`.
- **Verification criteria**:
  - Home commits `G_I` at step 2.
  - No stale owner bit or sharer bit remains.
  - Node1 refills successfully from home and gets correct data.
- **Injection needed**: **No**.

### TC: `tc_gm_dirty_owner_evict_negative`

- **Scenario**: direct coverage of `D#36` dirty-owner evict bug from stable `N3`.
- **Nodes involved**:
  - Node0 = dirty owner
  - Node1 = validation reader
  - Node2 = home
- **Protocol steps**:
  1. Node0 gets `X` in `G_M` and writes `V1`.
  2. Node0 issues clean `EVICT(X)` without prior writeback.
  3. Node1 reads `X`.
- **Verification criteria**:
  - Required fixed behavior: either reject the dirty-owner evict, or preserve `V1` via explicit writeback path.
  - Forbidden behavior: silent `G_M -> G_I` commit losing `V1`.
  - If evict is rejected, line must remain readable as `V1` after proper recovery.
  - If implementation auto-converts to writeback, logs must show that path explicitly.
- **Injection needed**: **No**.

### TC: `tc_non_sharer_upgrade_reject`

- **Scenario**: direct coverage of `E#39`, non-sharer `UPG_REQ` reject.
- **Nodes involved**:
  - Node0 = sharer
  - Node1 = non-sharer attacker/requester
  - Node2 = home
- **Protocol steps**:
  1. Node0 reads `X` shared so home is `G_S` with sharers `{0}`.
  2. Inject debug `UpgradeReq` from Node1 for `X` without Node1 holding a shared copy.
  3. Node0 and Node1 then perform normal accesses to confirm line remains usable.
- **Verification criteria**:
  - Home rejects `UPG_REQ` from non-sharer.
  - No crash, no UB, no negative-index/range bug.
  - Committed sharers remain unchanged after rejection.
- **Injection needed**: **Yes** - debug `UpgradeReq` inject.

### TC: `tc_clear_wrong_epoch_tombstone`

- **Scenario**: negative test for stale `Clear` with wrong epoch.
- **Nodes involved**:
  - Node0 = grantee/requester
  - Node1 = later retrier
  - Node2 = home
- **Protocol steps**:
  1. Node0 issues request on `X` creating `GRANT_HANDSHAKE/WAITING_CLEAR` (`L0/L1/L2/L3` is acceptable; prefer `L0`).
  2. Inject `Clear(src=Node0, reqId=match, epoch=wrong)`.
  3. Probe home debug state.
  4. Retry the stale tuple once, then issue a fresh legal request.
- **Verification criteria**:
  - Home must retire the outstanding, keep committed directory unchanged, and write a tombstone with `accepted=false`.
  - Stale replay must be rejected from tombstone.
  - Fresh later request must succeed.
  - No orphan outstanding remains after wrong-epoch `Clear`.
- **Injection needed**: **Yes** - debug `Clear` inject.

### TC: `tc_clear_invalid_reqid_retains_outstanding`

- **Scenario**: negative test for invalid `reqId` on `Clear`; direct coverage of `E#41`.
- **Nodes involved**:
  - Node0 = grantee/requester
  - Node1 = contending requester
  - Node2 = home
- **Protocol steps**:
  1. Node0 creates `GRANT_HANDSHAKE/WAITING_CLEAR` on `X`.
  2. Inject `Clear(src=Node0, epoch=match, reqId=bad)`.
  3. While outstanding remains live, have Node1 issue a normal request to `X`.
  4. Finally send the correct `Clear` from Node0.
  5. Let Node1 complete.
- **Verification criteria**:
  - Bad-`reqId` `Clear` must be rejected.
  - Outstanding must remain live after rejection.
  - Node1 must queue behind the live outstanding, not bypass it.
  - After correct `Clear`, queued Node1 request must complete.
  - Optional variant: repeat with wrong `srcNode` to cover the full `reqId/src mismatch` edge.
- **Injection needed**: **Yes** - debug `Clear` inject.

### TC: `tc_live_busy_reject_matrix`

- **Scenario**: direct dedicated BUSY coverage for live states in `E#42`.
- **Nodes involved**:
  - Node0 = current requester/owner
  - Node1 = peer sharer/requester
  - Node2 = home
- **Protocol steps**:
  1. Subcase A: create `L0`, then issue `WB_*` and `EVICT` from Node0 before `Clear`.
  2. Subcase B: create `L1`, then issue `WB_*` and `EVICT`.
  3. Subcase C: create `L2`, then issue `WB_*` and `EVICT`.
  4. Subcase D: create `L3`, then issue `WB_*` and `EVICT`.
  5. Subcase E: create `L9`, then issue `WB_*` and `EVICT`.
  6. Subcase F: create `L10`, then issue `WB_*` and `EVICT`.
  7. In each subcase, finish the original live transaction normally.
- **Verification criteria**:
  - Every injected `WB_*`/`EVICT` during these live states must return `BUSY` or equivalent explicit reject.
  - No committed directory mutation may occur before the original live transaction completes.
  - Original transaction still completes correctly after the rejected attempt.
- **Injection needed**: **Yes** - deterministic pause/window control to hit each live state before completion.

### TC: `tc_l0_different_requester_enqueue`

- **Scenario**: direct coverage of `E#44`, different requester enqueue/merge while line is in `L0`.
- **Nodes involved**:
  - Node0 = first requester
  - Node1 = second requester
  - Node2 = home
- **Protocol steps**:
  1. Node0 issues request on `X`, creating `L0`.
  2. Delay Node0's `Clear` using the router delay hook.
  3. While `L0` is live, Node1 issues `ReadShared(X)` or `ReadUnique(X)`.
  4. Release delayed `Clear`.
  5. Observe Node1 service.
- **Verification criteria**:
  - Node1 request must be queued/merged, not dropped incorrectly and not allowed to bypass Node0.
  - `L0` must remain `L0` until Node0's `Clear` commits.
  - After `Clear`, queued Node1 request must replay and complete.
- **Injection needed**: **Yes** - delay first `Clear`.

### TC: `tc_duplicate_invalidate_ack_ignored`

- **Scenario**: negative duplicate `InvalidateAck` on an invalidate or upgrade barrier.
- **Nodes involved**:
  - Node0 = requester/upgrader
  - Node1 = sharer A
  - Node2 = sharer B or home CPU participant
  - Home = any node that owns the directory for `X`
- **Protocol steps**:
  1. Create `L4` or `L9` with at least two invalidate targets.
  2. Deliver `INV_ACK` from one target.
  3. Duplicate the same `INV_ACK` once.
  4. Deliver the remaining unique `INV_ACK`.
  5. Complete grant/upgrade.
- **Verification criteria**:
  - Duplicate ack must be ignored exactly once.
  - Ack count / target mask must not underflow or advance twice.
  - Completion must wait for all unique targets, not for duplicates.
  - Final owner/data result must be correct.
- **Injection needed**: **Yes** - `Duplicate` hook or debug `INV_ACK` inject.

### TC: `tc_stale_recall_resp_rejected`

- **Scenario**: negative stale `RecallResp` after the line has already advanced.
- **Nodes involved**:
  - Node0 = old owner
  - Node1 = requester
  - Node2 = home
- **Protocol steps**:
  1. Create a recall (`L5` or `L6`) on `X` and accept the valid `RecallResp`.
  2. Advance the line beyond the old recall instance (consume `RECALL/DONE`, complete grant/clear).
  3. Inject the old `RecallResp` again with stale `(epoch, reqId)`.
  4. Perform a normal read on `X`.
- **Verification criteria**:
  - Stale `RecallResp` must be rejected.
  - Cached recall data must not be overwritten.
  - No new outstanding may be created by the stale packet.
  - Final read must match the architecturally current value.
- **Injection needed**: **Yes** - debug `RecallResp` inject.

### TC: `tc_fault_delay_clear_then_recover`

- **Scenario**: transport fault where a legal `Clear` is delayed, not corrupted.
- **Nodes involved**:
  - Node0 = first requester
  - Node1 = second requester
  - Node2 = home
- **Protocol steps**:
  1. Install router rule: `Delay` the matching `Clear` for `X` by `N` cycles.
  2. Node0 issues request on `X`; its `Clear` is delayed.
  3. While delayed, Node1 issues another request on `X`.
  4. Release delayed `Clear` automatically after `N` cycles.
  5. Let Node1 complete.
- **Verification criteria**:
  - During the delay window, line stays live/busy and later requesters queue.
  - After delayed `Clear` arrives, system makes forward progress without manual cleanup.
  - No tombstone misuse, no duplicate commit, no lost queued requester.
- **Injection needed**: **Yes** - router `Delay` hook.

### TC: `tc_fault_drop_clear_watchdog`

- **Scenario**: transport fault where a legal `Clear` is lost once.
- **Nodes involved**:
  - Node0 = requester
  - Node1 = blocked follower
  - Node2 = home
- **Protocol steps**:
  1. Install router rule: `Drop` the first matching `Clear` for `X`.
  2. Node0 issues request on `X`; its `Clear` is dropped.
  3. Node1 issues a later request to `X`.
  4. Run until either recovery logic fires or watchdog timeout expires.
- **Verification criteria**:
  - If timeout/cancel recovery exists, it must retire or recover the stuck outstanding and permit later progress.
  - If timeout/cancel is still unimplemented, this TC should be marked **expected-fail/XFAIL** and act as a liveness sentinel.
  - Logs must clearly distinguish `dropped Clear` from ordinary starvation.
- **Injection needed**: **Yes** - router `Drop` hook.

### TC: `tc_fault_reorder_invalidate_acks`

- **Scenario**: force reverse arrival order of invalidate acknowledgements.
- **Nodes involved**:
  - Node0 = requester/upgrader
  - Node1 = sharer A
  - Node2 = sharer B
  - Home = directory node for `X`
- **Protocol steps**:
  1. Create `L4` or `L9` with two invalidate targets.
  2. Install `ReorderHold` on both `INV_ACK`s for `X`.
  3. Release them in reverse order with `ReorderRelease`.
  4. Complete the pending request/upgrade.
- **Verification criteria**:
  - Final result must be independent of ack arrival order.
  - Target-mask clearing / ack-count tracking must not assume a fixed order.
  - No duplicate grant, no missed final-ack transition, no stuck barrier.
- **Injection needed**: **Yes** - router `ReorderHold/ReorderRelease` hook.

---

## 4. Recommended execution order

1. `tc_recall_done_ge_owner_evict_leak`
2. `tc_recall_done_gm_owner_wbdrop_leak`
3. `tc_gm_dirty_owner_evict_negative`
4. `tc_gs_evict_notlast_then_last`
5. `tc_ge_owner_evict_direct`
6. `tc_clear_invalid_reqid_retains_outstanding`
7. `tc_clear_wrong_epoch_tombstone`
8. `tc_l0_different_requester_enqueue`
9. `tc_live_busy_reject_matrix`
10. `tc_duplicate_invalidate_ack_ignored`
11. `tc_stale_recall_resp_rejected`
12. `tc_fault_delay_clear_then_recover`
13. `tc_fault_reorder_invalidate_acks`
14. `tc_fault_drop_clear_watchdog`

---

## 5. Coverage mapping back to FV-11

| FV-11 edge | Proposed direct TC |
|---|---|
| D#29 | `tc_gs_evict_notlast_then_last` |
| D#30 | `tc_gs_evict_notlast_then_last` |
| D#33 | `tc_ge_owner_evict_direct` |
| D#36 | `tc_gm_dirty_owner_evict_negative` |
| E#37 | `tc_recall_done_ge_owner_evict_leak` |
| E#38 | `tc_recall_done_gm_owner_wbdrop_leak` |
| E#39 | `tc_non_sharer_upgrade_reject` |
| E#40 | `tc_clear_wrong_epoch_tombstone` |
| E#41 | `tc_clear_invalid_reqid_retains_outstanding` |
| E#42 | `tc_live_busy_reject_matrix` |
| E#44 | `tc_l0_different_requester_enqueue` |

This set converts all currently relevant uncovered or indirect FV-11 edges into direct, deterministic TCs, while also adding malformed-message negatives and router-level fault injection coverage.
