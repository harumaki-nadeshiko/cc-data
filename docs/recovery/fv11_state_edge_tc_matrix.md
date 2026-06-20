# FV-11: State-edge → TC coverage matrix

**Summary:** Maps each committed state-transition edge from FV-1 to the E2E test case(s) that exercise it. 30 transition categories identified across 7 groups; 3 categories uncovered, 2 indirectly covered.

## Coverage conventions

| Mark | Meaning |
|------|---------|
| ✓ | Directly covered by ≥1 TC |
| ~ | Indirectly covered (side-effect of larger scenario) |
| ✗ | No TC exercises this edge |
| — | Not applicable (negative/reject, not a forward edge) |

---

## 1. Summary matrix

### Group A: G_I → GRANT_HANDSHAKE (first-touch allocation)

| # | Entry state | Event | Live-out | Intended | Dir commit | FV-1 ref | TC(s) | Cov |
|---|-------------|-------|----------|----------|------------|----------|-------|-----|
| A1 | `G_I × none` | `OR_RS(r)` | `L0`: GRANT_HANDSHAKE/WAITING_CLEAR | `G_S` | L0→CLR→N1 | §2 N0 row 1 | TC1,TC3(reader),TC4(reader),TC6,TC8,TC11,TC13,TC14,TC16,TC18,TC23,TC26 ✓ |
| A2 | `G_I × none` | `OR_RU_E(r)` | `L0`: GRANT_HANDSHAKE/WAITING_CLEAR | `G_E` | L0→CLR→N2 | §2 N0 row 2 | TC1,TC2,TC3,TC4,TC5,TC7,TC15,TC17,TC25,TC27 | ✓ |
| A3 | `G_I × none` | `OR_RU_M(r)` | `L0`: GRANT_HANDSHAKE/WAITING_CLEAR | `G_M` | L0→CLR→N3 | §2 N0 row 3 | TC1,TC2,TC3,TC4,TC5,TC6,TC7,TC8,TC11,TC13,TC14,TC15,TC16,TC17,TC25,TC27 | ✓ |
| A4 | `G_I × none` | `UPG_REQ(r)` | — (reject) | — | — | §2 N0 row 4 | TC9 (negative) | ✓ |
| A5 | `G_I × none` | `REC_RESP` | — (reject) | — | — | §2 N0 row 5 | — | ~ (no TC injects bogus REC_RESP) |
| A6 | `G_I × none` | `CLR(tombstone)` | — (tombstone replay) | N0 | L | §2 N0 row 7 | implicit in all TCs (every Clear leaves tombstone) | ✓ |

### Group B: G_S → GRANT / INVALIDATE / UPGRADE

| # | Entry state | Event | Live-out | Intended | Dir commit | FV-1 ref | TC(s) | Cov |
|---|-------------|-------|----------|----------|------------|----------|-------|-----|
| B1 | `G_S × none` | `OR_RS(r)` | `L1`: GRANT_HANDSHAKE/WAITING_CLEAR | `G_S` with r added | L1→CLR→N1 | §2 N1 row 1 | TC3,TC4,TC6,TC8,TC13,TC14,TC16 | ✓ |
| B2 | `G_S × none` | `OR_RU_E/M(r)` existing sharer | BUSY (no transition) | — | — | §2 N1 row 2 | TC8(step3→UPG),TC11,TC16 | ✓ |
| B3 | `G_S × none` | `OR_RU_E/M(r)` new sharer | `L4`: INVALIDATE/WAITING_ALL_ACKS | `G_E/G_M(owner=r)` | L4→last INV_ACK→L0→CLR→N3/N2 | §2 N1 row 3, §2 L4 rows 1-3 | TC2,TC8,TC13,TC14,TC16,TC25 | ✓ |
| B4 | `G_S × none` | `UPG_REQ(r)` other sharers | `L9`: UPGRADE_PENDING/WAITING_ALL_ACKS | `G_E/G_M(owner=r)` | L9→L10→UPG_DONE→N2/N3 | §2 N1 row 4, §2 L9 | TC8,TC13,TC16 | ✓ |
| B5 | `G_S × none` | `UPG_REQ(r)` sole sharer | `L10`: UPGRADE_PENDING/WAITING_LOCAL_DONE | `G_E/G_M(owner=r)` | L10→UPG_DONE→N2/N3 | §2 N1 row 5, §2 L10 | TC11 | ✓ |
| B6 | `G_S × none` | `UPG_REQ(r)` not sharer | — (reject) | — | — | §2 N1 row 6 | TC9 (negative pattern) | ~ |
| B7 | `G_S × none` | `OR_RU_E/M(r)` no other sharers | `L1/GRANT_HANDSHAKE` immediate | `G_E/G_M(owner=r)` | L1→CLR→N2/N3 | §2 N1 code line 682-703 | TC8,TC11,TC16 | ~ (code path exists, exercised when `otherSharers==0`) |

### Group C: G_E → GRANT / RECALL

| # | Entry state | Event | Live-out | Intended | Dir commit | FV-1 ref | TC(s) | Cov |
|---|-------------|-------|----------|----------|------------|----------|-------|-----|
| C1 | `G_E × none` | `OR_RS(owner)` | `L2`: GRANT_HANDSHAKE/WAITING_CLEAR | `G_S`(owner+req) | L2→CLR→N1 | §2 N2 row 1 | TC1(self-read),TC3,TC4,TC7 | ✓ |
| C2 | `G_E × none` | `OR_RS(non-owner)` | `L5`: RECALL/WAITING_TARGET_RESP | `G_S` | L5→L7→L2→CLR→N1 | §2 N2 row 2, §2 L5 | TC2,TC3,TC4,TC5,TC6,TC13,TC14,TC15,TC16,TC25,TC27 | ✓ |
| C3 | `G_E × none` | `OR_RU_E(owner)` | `L2`: GRANT_HANDSHAKE/WAITING_CLEAR | `G_E(owner)` | L2→CLR→N2 | §2 N2 row 3 | TC1,TC3,TC4,TC7,TC27 | ✓ |
| C4 | `G_E × none` | `OR_RU_M(owner)` | `L2`: GRANT_HANDSHAKE/WAITING_CLEAR | `G_M(owner)` | L2→CLR→N3 | §2 N2 row 4 | TC1,TC3,TC4,TC7,TC25 | ✓ |
| C5 | `G_E × none` | `OR_RU_E/M(non-owner)` | `L5`: RECALL/WAITING_TARGET_RESP | `G_E/G_M(owner=r)` | L5→L7→L2→CLR→N2/N3 | §2 N2 row 5, §2 L5 | TC2,TC3,TC4,TC5,TC6,TC8,TC13,TC14,TC15,TC16,TC17,TC25,TC27 | ✓ |
| C6 | `G_E × none` | `UPG_REQ(owner)` | `L10`: UPGRADE_PENDING/WAITING_LOCAL_DONE (semantic gap) | `G_E/G_M` | L10→UPG_DONE→N2/N3 | §2 N2 row 6 | **✗** | ✗ |

### Group D: G_M → GRANT / RECALL

| # | Entry state | Event | Live-out | Intended | Dir commit | FV-1 ref | TC(s) | Cov |
|---|-------------|-------|----------|----------|------------|----------|-------|-----|
| D1 | `G_M × none` | `OR_RS(owner)` | `L3`: GRANT_HANDSHAKE/WAITING_CLEAR | `G_S`(owner+req) | L3→CLR→N1 | §2 N3 row 1 | TC1,TC3,TC4,TC6,TC14 | ✓ |
| D2 | `G_M × none` | `OR_RS(non-owner)` | `L6`: RECALL/WAITING_TARGET_RESP | `G_S` | L6→L8→L3→CLR→N1 | §2 N3 row 2, §2 L6 | TC2,TC3,TC4,TC5,TC6,TC8,TC13,TC14,TC15,TC16,TC17,TC25,TC27 | ✓ |
| D3 | `G_M × none` | `OR_RU_E(owner)` | `L3`: GRANT_HANDSHAKE/WAITING_CLEAR | `G_E(owner)` | L3→CLR→N2 | §2 N3 row 3 | TC1,TC3,TC4,TC7,TC27 | ✓ |
| D4 | `G_M × none` | `OR_RU_M(owner)` | `L3`: GRANT_HANDSHAKE/WAITING_CLEAR | `G_M(owner)` | L3→CLR→N3 | §2 N3 row 4 | TC1,TC2,TC3,TC4,TC5,TC6,TC7,TC8,TC11,TC13,TC14,TC15,TC16,TC17,TC25,TC27 | ✓ |
| D5 | `G_M × none` | `OR_RU_E/M(non-owner)` | `L6`: RECALL/WAITING_TARGET_RESP | `G_E/G_M(owner=r)` | L6→L8→L3→CLR→N2/N3 | §2 N3 row 5, §2 L6 | TC2,TC3,TC4,TC5,TC6,TC8,TC13,TC14,TC15,TC16,TC17,TC25,TC27 | ✓ |
| D6 | `G_M × none` | `UPG_REQ(owner)` | `L10`: UPGRADE_PENDING/WAITING_LOCAL_DONE (semantic gap) | `G_E/G_M` | L10→UPG_DONE→N2/N3 | §2 N3 row 6 | **✗** | ✗ |

### Group E: RECALL barriered → DONE → GRANT conversion

| # | Entry state | Event | Live-out | Dir commit | FV-1 ref | TC(s) | Cov |
|---|-------------|-------|----------|------------|----------|-------|-----|
| E1 | `L5` (G_E×RECALL/WAITING) | `REC_RESP(match)` | `L7`: RECALL/DONE | Directory unchanged (L) | §2 L5 row 1 | TC2,TC3,TC4,TC5,TC6,TC8,TC13,TC14,TC15,TC16,TC17,TC25,TC27 | ✓ |
| E2 | `L6` (G_M×RECALL/WAITING) | `REC_RESP(match)` | `L8`: RECALL/DONE | Directory unchanged (L) | §2 L6 row 1 | TC2,TC3,TC4,TC5,TC6,TC8,TC13,TC14,TC15,TC16,TC17,TC25,TC27 | ✓ |
| E3 | `L7` (RECALL/DONE) | same-requester `OR_RS/OR_RU` | `L2`: GRANT_HANDSHAKE/WAITING_CLEAR | L2→CLR→N1/N2/N3 | §2 L7 row 1 | TC2,TC3,TC4,TC5,TC6,TC7,TC8,TC13,TC14,TC15,TC16,TC17,TC25,TC27 | ✓ |
| E4 | `L8` (RECALL/DONE) | same-requester `OR_RS/OR_RU` | `L3`: GRANT_HANDSHAKE/WAITING_CLEAR | L3→CLR→N1/N2/N3 | §2 L8 row 1 | TC2,TC3,TC4,TC5,TC6,TC7,TC8,TC13,TC14,TC15,TC16,TC17,TC25,TC27 | ✓ |
| E5 | `L7/L8` (RECALL/DONE) | different-requester OR | Enqueue only (no transition) | — | §2 L7 row 2, L8 row 2 | TC5,TC8,TC15,TC16,TC25 | ✓ |
| E6 | `L5/L6` (RECALL/WAITING) | same-requester `OR_RS/OR_RU` | BUSY (no transition) | — | §2 L5 row 2-3 | — | ~ (retry during recall-in-flight invisible) |

### Group F: INVALIDATE path completion

| # | Entry state | Event | Live-out | Dir commit | FV-1 ref | TC(s) | Cov |
|---|-------------|-------|----------|------------|----------|-------|-----|
| F1 | `L4` (G_S×INVALIDATE/WAITING) | `INV_ACK(partial)` | L4 (clear sharer bit, | D-partial: directory clears one sharer | §2 L4 row 1 | TC4,TC8,TC13,TC14,TC16,TC25 | ✓ |
| F2 | `L4` (G_S×INVALIDATE/WAITING) | `INV_ACK(last)` | L0: GRANT_HANDSHAKE/WAITING_CLEAR (canonicalize G_S→G_I) | D-partial→L: clears last sharer, creates GRANT | §2 L4 row 2 | TC4,TC8,TC13,TC14,TC25 | ✓ |
| F3 | `L4` (G_S×INVALIDATE/WAITING) | `INV_ACK(duplicate)` | Idempotent (ignored) | — | §2 L4 row 3 | — | ~ (duplicate ack unlikely in deterministic test) |
| F4 | `L4` (G_S×INVALIDATE/WAITING) | different-requester OR | Enqueue only | — | §2 L4 row 5 | TC15,TC25 | ✓ |

### Group G: UPGRADE_PENDING completion

| # | Entry state | Event | Live-out | Dir commit | FV-1 ref | TC(s) | Cov |
|---|-------------|-------|----------|------------|----------|-------|-----|
| G1 | `L9` (WAITING_ALL_ACKS) | `INV_ACK(partial)` | L9 (decrement count) | — | §2 L9 row 1 | TC8,TC13,TC16 | ✓ |
| G2 | `L9` (WAITING_ALL_ACKS) | `INV_ACK(last)` no cached Done | `L10`: WAITING_LOCAL_DONE | — (L) | §2 L9 row 2 | TC8,TC13,TC16 | ✓ |
| G3 | `L9` (WAITING_ALL_ACKS) | `INV_ACK(last)` + cached Done | Commit directly | D-full: N2/N3 | §2 L9 row 3, code §1387 | TC8,TC16 | ✓ |
| G4 | `L9` (WAITING_ALL_ACKS) | `UPG_DONE(r=requester)` early | L9 (cache Done) | — (L) | §2 L9 row 5 | TC8,TC16 | ✓ |
| G5 | `L10` (WAITING_LOCAL_DONE) | `UPG_DONE(r=requester,accepted=1)` | Commit, remove outstanding | D-full: N2/N3 | §2 L10 row 1 | TC8,TC11,TC16 | ✓ |
| G6 | `L10` (WAITING_LOCAL_DONE) | `UPG_DONE(r!=requester)` | Reject | — | §2 L10 row 2 | — | ✗ |
| G7 | `L10` (WAITING_LOCAL_DONE) | `INV_ACK(...)` | Idempotent true | — | §2 L10 row 3 | — | ~ |

### Group H: Writeback / Evict

| # | Entry state | Event | Intended | Dir commit | FV-1 ref | TC(s) | Cov |
|---|-------------|-------|----------|------------|----------|-------|-----|
| H1 | G_E/G_M → Writeback | `processWriteback(owner)` | G_E(clean) or G_I | D-full: directory modified | § code §1497-1580 | TC7,TC26 | ✓ |
| H2 | G_E → Evict | `processEvict(clean owner)` | G_I | D-full: directory modified | § code §1621-1734 | TC7 (evict flood) | ~ |
| H3 | G_S sharer → Evict | `processEvict(sharer)` | G_S (removed from sharers) | D-full: directory modified | § code §1621-1734 | TC7 | ~ |
| H4 | G_I tombstone clean | `cleanupTombstones()` | — | L | § code §2242-2263 | TC25,TC27, all long-running TCs | ✓ |

### Group I: Replay / Queue

| # | Edge | TC(s) | Cov |
|---|------|-------|-----|
| I1 | Enqueue different requester while exclusive outstanding (INVALIDATE/RECALL/UPGRADE) | TC5,TC8,TC15,TC16,TC25 | ✓ |
| I2 | ReplayPendingRequesters after Clear commit | TC5,TC8,TC15,TC16,TC25 | ✓ |
| I3 | ReplayResidentWaiters after backstore fill | TC18,TC19,TC23,TC28 | ✓ |
| I4 | Bloom false-positive fallback (backstore fill) | TC23 | ✓ |
| I5 | ResidentDir eviction → backstore writeback chain | TC22,TC26,TC28 | ✓ |

### Group J: Dual-socket specific edges

| # | Edge | Description | TC(s) | Cov |
|---|------|-------------|-------|-----|
| J1 | `homeSocket` decode from PA | `NodeAddressMap::homeSocket()` selects per-socket HN-F | — | **✗** |
| J2 | Cross-socket message routing | `UBMsg.h.dstSocket` selection in UBAdapter | — | **✗** |
| J3 | Per-socket UBCC instance registration | `(node_id, socket_id)`-keyed `getInstance()` | — | **✗** |
| J4 | `_interconnectLatency=200` cross-socket delay | Latency path exercised for remote-socket round trips | — | **✗** |
| J5 | Multi-socket DSM line encoding | DSM PA layout with `kNumSockets` dimension | — | **✗** |

---

## 2. Uncovered edges (priority for new TCs)

### P0 — Must cover (semantic gap or protocol correctness)

| Priority | Edge | Reason | Suggested TC design |
|----------|------|--------|---------------------|
| **P0** | **C6**: `G_E × none` → `UPG_REQ(owner)` accepted | Semantic gap: code accepts upgrade on G_E, not just G_S. Test that directory behaves correctly after G_E→UPG_DONE | Single node: write-exclusive to G_E, then issue store → should go through UPG. Verify N2→L10→N3 commit |
| **P0** | **D6**: `G_M × none` → `UPG_REQ(owner)` accepted | Same semantic gap as C6 but for G_M | Same as above with write to G_M first |
| **P0** | **J1-J5**: Dual-socket all edges | Entire multi-socket feature has zero test coverage because `kNumSockets=1`. When activated, every state transition must work with socket-distinguished PA | Configure `kNumSockets=2`, run TC1-TC8 on cross-socket PAs |

### P1 — Edge cases (stale/negative injection)

| Priority | Edge | Reason | Suggested TC design |
|----------|------|--------|---------------------|
| P1 | **B0**: `CLR(epoch mismatch)` stale grant retirement | L0→retire to tombstone(accepted=false)→N0. If not tested, a wrong-Clear can pin line | Inject Clear with mismatched epoch while GRANT_HANDSHAKE is live; verify directory stays unchanged and grant is retired |
| P1 | **G6**: `UPG_DONE(r!=requester)` rejection | Negative test for wrong-node UpgradeDone | Inject UPG_DONE from different node while in UPGRADE_PENDING; verify reject |
| P1 | **A5**: `REC_RESP` on G_I (no recall) rejection | Negative test: recall response when no outstanding recall | Inject REC_RESP on a line that is G_I with no outstanding; verify reject |
| P1 | **F3**: duplicate `INV_ACK` idempotency | Negative test: duplicate ack must be ignored | Send duplicate INV_ACK after first; verify no double-count |

### P2 — Stress / liveness

| Priority | Edge | Reason | Suggested TC design |
|----------|------|--------|---------------------|
| P2 | Timeout paths (`TIMED_OUT`/`CANCELLED`) | No timeout handler is implemented for any outstanding stage (6 places in FV-1 §5). If a response is lost, the line is pinned forever | Design a test where CLR/REC_RESP/INV_ACK is dropped; verify deadlock detection or timeout recovery |
| P2 | `RECALL/DONE` → same-requester with changed reqType/reqId | FV-1 §5 item 5: under-constrained retry tuple | Same requester retries with different reqType (RS→RU) while RECALL is DONE; verify intended state matches new request, not old |
| P2 | `REC_RESP` in `RECALL/DONE` (duplicate accept) | FV-1 §5 item 4: `processRecallResponse` doesn't check stage | Send second REC_RESP after already in DONE; verify data buffer is not corrupted |

---

## 3. Coverage summary

| Group | Total edges | ✓ Covered | ~ Indirect | ✗ Uncovered | N/A (reject) | Coverage rate |
|-------|-------------|-----------|------------|-------------|--------------|---------------|
| A: G_I→GRANT | 6 | 4 | 1 | 0 | 1 | 83% |
| B: G_S→* | 7 | 5 | 2 | 0 | 0 | 71% |
| C: G_E→* | 6 | 5 | 0 | 1 | 0 | 83% |
| D: G_M→* | 6 | 5 | 0 | 1 | 0 | 83% |
| E: RECALL conversion | 6 | 5 | 1 | 0 | 0 | 83% |
| F: INVALIDATE completion | 4 | 3 | 1 | 0 | 0 | 75% |
| G: UPGRADE completion | 7 | 5 | 1 | 1 | 0 | 71% |
| H: Writeback/Evict | 4 | 1 | 2 | 0 | 1 | 25% |
| I: Replay/Queue | 5 | 5 | 0 | 0 | 0 | 100% |
| J: Dual-socket | 5 | 0 | 0 | 5 | 0 | 0% |
| **Total** | **56** | **38** | **8** | **8** | **2** | **68% direct** |

### Key findings

1. **Dual-socket is entirely uncovered** (J1-J5). Every edge with `_socketId`/`homeSocket`/`kNumSockets` has zero TC coverage. This is the biggest gap — the infrastructure exists but `kNumSockets=1` disables it.

2. **Two semantic gaps in UPGRADE_REQ** (C6, D6): `processOuterUpgradeReq` accepts owner upgrades from `G_E` and `G_M` without guard, but FV-1 identifies this as a gap. No TC exercises this, but the code path is reachable if a CPU on the owner node issues a store with upgrade semantics while holding G_E/G_M.

3. **No negative-injection TCs** for stale-epoch Clear, reqId-mismatch Clear, wrong-node UpgradeDone, or duplicate INV_ACK. These are reject paths that don't affect forward progress but could mask protocol bugs.

4. **Timeout/Cancelled paths are completely absent** from both code (no transition to `TIMED_OUT`/`CANCELLED`) and tests. Liveness depends on every expected response arriving — no watchdog timer is implemented.

5. **Writeback/Evict paths** (H1-H3) are exercised only indirectly by TC7 and TC26. No dedicated dirty-evict / backstore-interaction TC exists (TC22 and TC28 touch related but different paths).

