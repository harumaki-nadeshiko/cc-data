# New E2E Testcase Design v3 (TC36-TC45)

## Scope

This document defines **10 design-only E2E tests** for the next coverage wave.

Common constraints assumed for all TCs:

- One testcase = one C file under `tests/e2e/workloads/`
- Use only `dsm_store()` / `dsm_load()` from `dsm_access.h` for workload memory ops
- Keep the standard primary filter:
  `int primary = (cpu_index % 4 == 0); if (!primary) _exit_program(0);`
- Use `sync_wait(mask)` for phase barriers
- Use `emit_read_val()` for pass/fail-visible data checks
- Where a testcase needs protocol fault injection or dual-socket enablement, that is called out explicitly as a **test-harness requirement**, not workload code

---

## Coverage summary

| TC | Name | Primary gap targeted |
|---|---|---|
| TC36 | `e2e_tc36_owner_upgrade_ge_window` | `G_E × UPG_REQ(owner)` |
| TC37 | `e2e_tc37_owner_upgrade_gm_window` | `G_M × UPG_REQ(owner)` |
| TC38 | `e2e_tc38_stale_clear_tombstone_storm` | stronger stale Clear / tombstone replay |
| TC39 | `e2e_tc39_dual_socket_same_pa_interference` | dual-socket cross-plane same-PA interference |
| TC40 | `e2e_tc40_recall_timeout_retry` | RECALL timeout / retry / eventual completion |
| TC41 | `e2e_tc41_recall_invalidate_overlap` | concurrent RECALL + INVALIDATE on same PA |
| TC42 | `e2e_tc42_exact_epoch_wrap_24b` | exact 24-bit epoch overflow / wrap |
| TC43 | `e2e_tc43_rapid_owner_cycle` | rapid ownership cycling |
| TC44 | `e2e_tc44_full_protocol_matrix` | all major MESI/optype combinations in one run |
| TC45 | `e2e_tc45_fill_conflict_bloom_sat` | backstore fill conflict + bloom saturation |

---

## TC36 — `e2e_tc36_owner_upgrade_ge_window`

1. **Goal**  
   Prove that an owner-local write while the committed outer state is `G_E` takes the accepted owner-upgrade path and does not accidentally trigger recall, invalidate, or ownership loss.

2. **Protocol scenario**  
   Exercise `G_I -> G_E`, then `G_E × UPG_REQ(owner) -> L10(WAITING_LOCAL_DONE) -> G_M`; add a queued remote `ReadShared` behind the local upgrade so the testcase also checks replay ordering around the semantic-gap edge C6.

3. **Node operations**  
   - Home line `X` is on Node0.
   - Node1 first acquires `X` in the clean-exclusive path (same protocol intent as FV-11 C6; test may require a debug marker proving committed `G_E` before phase 2).
   - After a barrier, Node1 performs a store to `X` to trigger owner-local upgrade.
   - In the same phase, Node2 issues a load to `X`; this request must queue until the owner upgrade resolves.
   - Final phase: Node0/Node1/Node2 all read `X`.

4. **Verification criteria**  
   - A testcase marker or protocol log confirms `state=G_E` before the owner store and confirms `UPG_REQ(owner)` was accepted.
   - No recall/invalidate markers appear before Node1’s upgrade completes.
   - Node2 does not observe the pre-upgrade value after the upgrade phase.
   - Final value is identical on all nodes and equals Node1’s upgraded write.

---

## TC37 — `e2e_tc37_owner_upgrade_gm_window`

1. **Goal**  
   Stress the analogous owner-local upgrade/refresh path while the line is already globally `G_M`, ensuring the implementation neither deadlocks nor corrupts ownership metadata.

2. **Protocol scenario**  
   Exercise `G_I -> G_M`, then force the owner-side upgrade/write-notify path again while still owner-dirty; this targets FV-11 D6 and checks that `G_M × UPG_REQ(owner)` resolves to a legal `G_M` completion rather than a spurious recall or rejected transaction.

3. **Node operations**  
   - Home line `X` is on Node0.
   - Node1 stores `V1` to `X`, becoming dirty owner.
   - Without any intervening sharer creation, Node1 performs a second store `V2` to the same `X` in a phase where Node2 also starts a load to `X` late enough to create pressure on the owner path.
   - Node2 must either wait for the dirty-owner path to settle or trigger the normal recall path afterward; it must not observe a half-updated value.
   - Final phase: Node0/Node1/Node2 read `X`.

4. **Verification criteria**  
   - A marker/log proves the line was in committed `G_M` when the second owner-side write occurred.
   - No illegal transition (`reject`, stuck outstanding, duplicate owner) is observed.
   - Node2 reads either the pre-store value before the second phase starts or the fully committed `V2` after completion, but never a torn/intermediate value.
   - Final value converges to `V2` on all nodes.

---

## TC38 — `e2e_tc38_stale_clear_tombstone_storm`

1. **Goal**  
   Strengthen TC30 by validating stale-Clear rejection under a longer async window with multiple later epochs and tombstone cleanup pressure.

2. **Protocol scenario**  
   Exercise `GRANT_HANDSHAKE`, accepted `Clear`, stale `Clear(epoch mismatch)`, stale `Clear(reqId mismatch)`, tombstone replay, and subsequent fresh grant on the same line after additional commits.

3. **Node operations**  
   - Home line `X` is on Node0.
   - Node1 issues the first miss and receives a grant for `X`, but the harness intentionally delays its `Clear` delivery.
   - Before the delayed `Clear` is replayed, Node2 performs a conflicting request and commits a later epoch on `X`; then Node1 re-requests `X` and completes a valid later transaction.
   - The harness then injects the original delayed `Clear`, plus one wrong-`reqId` `Clear`, after the line has already moved forward by at least two commits.
   - Final phase: Node2 reads the line, then Node1 reads again after tombstone cleanup opportunity.

4. **Verification criteria**  
   - Stale `Clear` deliveries are explicitly rejected/retired and do not change the committed value.
   - Tombstone replay remains idempotent: no duplicate grant, no line pin, no wrong owner resurrection.
   - Final committed value is the newest legal writer’s value on both readers.
   - A marker reports `stale_clear_seen>=2` and `replay_ok=1`.

---

## TC39 — `e2e_tc39_dual_socket_same_pa_interference`

1. **Goal**  
   Validate that concurrent accesses to the **same DSM PA** from different requester sockets do not split state across socket planes or route to the wrong per-socket UBCC/HN-F instance.

2. **Protocol scenario**  
   Dual-socket version of same-line contention: same `linePa`, fixed `homeSocket`, concurrent requesters from two different sockets; targets J1-J5 plus the user-identified same-home-node/same-PA cross-plane interference gap.

3. **Node operations**  
   - **Harness requirement:** run with `UBCC_NUM_SOCKETS=2`, reusing the TC32-TC35 dual-socket address pattern.
   - Choose one PA `X` homed at `Node0/socket1`.
   - Node1 primary CPU (socket0 path) loads `X` while Node1 lane1 secondary CPU (socket1 path) also loads `X` in the setup phase.
   - After a barrier, Node2 writes `V2` to `X` while Node1 primary re-reads and Node0 performs an unrelated access on `Node0/socket0` to create cross-plane traffic.
   - Final phase: Node1 primary and Node2 primary read `X`; Node0 reads `X` from the home side.

4. **Verification criteria**  
   - Socket-routing markers show that accesses to the same `X` hit the same `homeSocket=1` instance regardless of requester socket.
   - No split-brain final state: all final reads of `X` equal `V2`.
   - No request is misrouted to `socket0` for `X`.
   - Optional latency marker shows cross-socket path executed at least once.

---

## TC40 — `e2e_tc40_recall_timeout_retry`

1. **Goal**  
   Verify that a lost or non-responding RECALL target triggers retry and still eventually completes without pinning the line forever.

2. **Protocol scenario**  
   `G_M(owner=A)` with requester `B` causes `RECALL/WAITING_TARGET_RESP`; first recall attempt is dropped or stalled, timeout fires, recall retries, then transitions through `RECALL/DONE -> GRANT_HANDSHAKE -> Clear`.

3. **Node operations**  
   - **Harness requirement:** inject a one-shot fault that drops or indefinitely delays the first `RecallResponse` (or blocks the owner EP-RNF callback once) for target line `X`.
   - Home line `X` is on Node0; Node1 stores dirty value `V1` to become owner.
   - Node2 then loads `X`, forcing read-recall from Node1.
   - The first recall attempt is faulted; the second attempt must succeed.
   - Final phase: Node2 reads `X`, then Node0 reads `X`.

4. **Verification criteria**  
   - A timeout/retry marker is observed (`retry_count>=1`).
   - The line is not permanently stuck in `RECALL/WAITING_TARGET_RESP`.
   - Node2 eventually receives `V1`, and Node0 later reads the same `V1`.
   - If timeout recovery is not implemented yet, this TC should be registered as **xfail-by-design** until the retry path exists.

---

## TC41 — `e2e_tc41_recall_invalidate_overlap`

1. **Goal**  
   Validate correct serialization when one requester triggers RECALL and a second requester, on the same PA, later forces INVALIDATE after the recall result converts the line to shared.

2. **Protocol scenario**  
   Start from `G_M(owner=A)`. Requester `B` issues `ReadShared` causing `RECALL`. While that is in flight, requester `C` issues `ReadUnique/write`, which must queue. After recall commits intended `G_S`, the queued unique request must launch `INVALIDATE/WAITING_ALL_ACKS` on the recalled sharers and only then grant ownership to `C`.

3. **Node operations**  
   - Home line `X` is on Node0.
   - Node1 stores `V1` to `X` and becomes dirty owner.
   - Barrier release: Node2 issues `dsm_load(X)` to trigger read recall.
   - Before Node2’s read phase fully settles, Node0 local primary (or a designated third requester path) stores `V2` to `X`, creating the queued unique request.
   - Final phase: Node0/Node1/Node2 all read `X`.

4. **Verification criteria**  
   - Logs/markers show both a recall phase and a later invalidate phase on the same `X`.
   - The queued unique request does not bypass the recall barrier.
   - Final value on all nodes is `V2`.
   - No stale `V1` survives after the invalidate/grant sequence completes.

---

## TC42 — `e2e_tc42_exact_epoch_wrap_24b`

1. **Goal**  
   Prove correct behavior at the **exact** 24-bit epoch boundary, not just “many commits eventually wrapped.”

2. **Protocol scenario**  
   Force one line’s committed epoch to `0xFFFFFD`, then perform enough legal commits to observe `0xFFFFFE -> 0xFFFFFF -> 0x000000 -> 0x000001`, while verifying that stale/old completions are still rejected by exact tuple matching.

3. **Node operations**  
   - **Harness requirement:** seed the target line `X` with epoch near max (`0xFFFFFD`) or expose a debug knob that sets the initial epoch for the testcase.
   - Use three nodes in a short, deterministic sequence: Node1 read-shared, Node2 unique-write, Node1 read-shared, Node0 unique-write.
   - Each phase must commit the line once, so the wrap point is crossed in a predictable 4-step window.
   - Final phase: all nodes read `X`.

4. **Verification criteria**  
   - Marker prints the exact epoch sequence across the boundary, including `0xFFFFFF -> 0x000000`.
   - No stale completion from the pre-wrap epoch family is accepted after wrap.
   - Final value equals the last writer’s value on all nodes.
   - No line pin or replay confusion occurs at the wrap boundary.

---

## TC43 — `e2e_tc43_rapid_owner_cycle`

1. **Goal**  
   Stress liveness and metadata cleanup by repeatedly transferring unique ownership of one hot line among all nodes for many rounds.

2. **Protocol scenario**  
   Repeated `G_M(owner=A) -> recall/invalidate -> G_M(owner=B)` cycles, with intermittent shared reads to force `G_S` re-expansion; targets owner-update lifecycle, outstanding cleanup, and replay correctness under repeated churn.

3. **Node operations**  
   - Home line `X` is on Node0.
   - Run 64-256 rounds. In each round, exactly one node writes a round-tagged value to `X`; the next node reads it, then upgrades/writes the next value, then the third node repeats.
   - Every 8 or 16 rounds, insert a full shared phase where the non-owner nodes both load `X` before the next writer takes unique again.
   - Final phase: all nodes read the last round’s value.

4. **Verification criteria**  
   - All rounds complete; no forward-progress stall.
   - Final value equals the last round tag on all nodes.
   - No earlier round value reappears after a later round has committed.
   - Optional progress markers (`round=`) prove sustained ownership transfer without leaks.

---

## TC44 — `e2e_tc44_full_protocol_matrix`

1. **Goal**  
   Provide one dense regression testcase that exercises nearly the full protocol surface in one run: shared, exclusive, modified, recall, invalidate, upgrade, writeback/evict, and refill/replay interactions.

2. **Protocol scenario**  
   Multi-line phased matrix:
   - Line A: `G_I -> G_S -> INVALIDATE -> G_M`
   - Line B: `G_I -> G_E -> owner upgrade -> G_M`
   - Line C: `G_M -> writeback/evict -> refill`
   - Line D: `G_M(owner) -> RECALL -> G_S -> another unique`
   This gives one testcase that covers the widest combined set of steady and transient edges.

3. **Node operations**  
   - Use 4 DSM lines (`A/B/C/D`) under the same home node.
   - Phase 1: initialize all four lines from different nodes.
   - Phase 2: create two-sharer and three-sharer patterns on `A/D`.
   - Phase 3: perform owner-local upgrade on `B`, dirty writeback pressure on `C`, and remote read recall on `D`.
   - Phase 4: convert `A` from shared to unique via invalidation; refill `C`; then perform final reads of all four lines from all nodes.

4. **Verification criteria**  
   - Per-line final values match the phase script exactly.
   - No line retains a stale earlier-phase value.
   - Logs show evidence of upgrade, invalidate, recall, and writeback/fill paths all within the same run.
   - This testcase should fail if any major protocol primitive silently regresses, even when simpler single-purpose TCs still pass.

---

## TC45 — `e2e_tc45_fill_conflict_bloom_sat`

1. **Goal**  
   Combine the two remaining directory-offload stress gaps: a target-line backstore fill conflict while bloom counters are heavily saturated.

2. **Protocol scenario**  
   One target line `X` is forced into a resident-dir/backstore fill path while many background lines flood the bloom structure toward saturation, causing false positives, evictions, and metadata pressure during the fill window.

3. **Node operations**  
   - **Harness requirement:** run with a stress profile that keeps ResidentDir/Bloom capacities intentionally small.
   - Home node initializes target line `X` and a large background line set `B[0..N)`.
   - Background CPUs/secondaries churn through `B[]` with mixed loads/stores to drive bloom counters near max and induce resident eviction.
   - While that pressure is active, Node1 requests `X` through a path that requires backstore fill/replay; before fill completes, Node2 accesses `X` and Node0 forces additional eviction pressure.
   - Final phase: Node1 and Node2 read `X`; one node also reads a small sampled subset of `B[]` for sanity.

4. **Verification criteria**  
   - Marker/log proves bloom saturation activity occurred (`sat_count>0` or equivalent).
   - Marker/log proves the target line saw fill-in-progress conflict handling rather than silent overwrite.
   - Final value of `X` is correct on all readers despite eviction/fill overlap.
   - No target-line corruption, no stuck replay waiter, and no metadata/data divergence after the stress window.

---

## Recommended execution order

1. **TC39** — highest architecture-risk uncovered area still mostly untested under same-PA pressure.  
2. **TC36 / TC37** — closes the explicit FV-11 semantic gaps.  
3. **TC41** — strongest cross-controller serialization hazard.  
4. **TC42** — exact-boundary correctness.  
5. **TC43 / TC44 / TC45** — broad regression/stress wave.  
6. **TC40** — add once timeout/retry hook exists, or register as xfail until then.
