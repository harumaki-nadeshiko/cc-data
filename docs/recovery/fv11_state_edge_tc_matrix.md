# FV-11: State-edge → TC coverage matrix

Maps the 43 reachable composite-state edges (§1.1–1.2 of FV-1) against the TC1–45 e2e test suite. Coverage flag: **`C`** = directly covered, **`I`** = indirect/partial, **`U`** = uncovered (gap), **`N/A`** = not applicable (negative/reject path).

## 1. Edge-to-TC matrix

| # | From | To | Trigger | Transition kind | Coverage | Covering TC(s) |
|---|------|----|---------|----------------|----------|----------------|
| **A. Stable → Live (outer-request / grant creation)** |
| 1 | N0 (G_I) | L0 | `OR_RS(r)` → intended G_S | L | **C** | TC1, TC2, TC4, TC6, TC7, TC8, TC11, TC14, TC15, TC16, TC18, TC23, TC25, TC32, TC34, TC40, TC41, TC43, TC44 |
| 2 | N0 (G_I) | L0 | `OR_RU_E/M(r)` → intended G_E/G_M | L | **C** | TC1, TC3, TC5, TC7, TC8, TC11, TC15, TC17, TC19, TC25, TC34, TC36, TC37, TC41, TC43, TC44 |
| 3 | N1 (G_S) | L1 | `OR_RS(r)` shared read | L | **C** | TC4, TC6, TC8(2), TC11(2), TC14, TC16(2), TC25 |
| 4 | N1 (G_S) | L4 | `OR_RU_E/M(r∉sharers)` invalidate | L | **C** | TC8, TC14, TC25, TC44 |
| 5 | N1 (G_S) | L9 | `UPG_REQ(sharer,perm)` other sharers exist | L | **C** | TC11, TC16 |
| 6 | N1 (G_S) | L10 | `UPG_REQ(sharer,sole)` no other sharers | L | **C** | TC8, TC11 |
| 7 | N2 (G_E) | L2 | `OR_RS/OR_RU(owner)` self-request | L | **C** | TC3, TC7, TC36 |
| 8 | N2 (G_E) | L5 | `OR_RS/OR_RU(r≠owner)` recall | L | **C** | TC3, TC34, TC40, TC41, TC43 |
| 9 | N3 (G_M) | L3 | `OR_RS/OR_RU(owner)` self-request | L | **C** | TC3, TC7, TC19, TC37 |
| 10 | N3 (G_M) | L6 | `OR_RS/OR_RU(r≠owner)` recall | L | **C** | TC3, TC5, TC34, TC40, TC41, TC43 |
| 11 | N2 (G_E) | L10 | `UPG_REQ(owner,perm)` **semantic gap** | L | **C** | TC36 |
| 12 | N3 (G_M) | L10 | `UPG_REQ(owner,perm)` **semantic gap** | L | **C** | TC37 |
| **B. Live → Live (internal state progress)** |
| 13 | L4 | L0 | `INV_ACK(last)` → canonicalize G_S→G_I → GRANT_HANDSHAKE | D-partial + L | **C** | TC8, TC14, TC25, TC44 |
| 14 | L5 | L7 | `REC_RESP(match)` → RECALL/WAITING→DONE | L | **C** | TC3, TC34, TC40, TC41, TC43 |
| 15 | L6 | L8 | `REC_RESP(match)` → RECALL/WAITING→DONE | L | **C** | TC3, TC34, TC40, TC41, TC43 |
| 16 | L7 | L2 | `OR_RS/OR_RU(same-requester)` retry → GRANT_HANDSHAKE | L | **C** | TC3, TC34, TC40, TC43 |
| 17 | L8 | L3 | `OR_RS/OR_RU(same-requester)` retry → GRANT_HANDSHAKE | L | **C** | TC3, TC34, TC40, TC43 |
| 18 | L9 | L10 | `INV_ACK(last)` → WAITING_ALL_ACKS→WAITING_LOCAL_DONE | L | **C** | TC11, TC16 |
| **C. Live → Stable (CLR / commit)** |
| 19 | L0 | N0/N1/N2/N3 | `CLR(match)` → commit intended state | D-full | **C** | TC1, TC2, TC3, TC4, TC5, TC6, TC7, TC8, TC11, TC14, TC15, TC16, TC18, TC23, TC25, TC32, TC34, TC40, TC41, TC43, TC44 |
| 20 | L1 | N1 | `CLR(match)` → commit G_S | D-full | **C** | TC4, TC6, TC8 |
| 21 | L2 | N1/N2/N3 | `CLR(match)` → commit intended | D-full | **C** | TC3, TC7, TC36 |
| 22 | L3 | N1/N2/N3 | `CLR(match)` → commit intended | D-full | **C** | TC3, TC19, TC37 |
| 23 | L9 | N2/N3 | `INV_ACK(last)` + cached `UPG_DONE` → commit | D-full | **C** | TC11, TC16, TC44 |
| 24 | L10 | N2/N3 | `UPG_DONE(match)` → commit | D-full | **C** | TC11, TC16, TC36, TC37, TC44 |
| **D. Stable → Stable (direct writeback / evict commits)** |
| 25 | N0 (G_I) | G_E | `WB_CLEAN(r)` **semantic gap** | D-full | **C** | TC7, TC17, TC33 |
| 26 | N0 (G_I) | G_I | `WB_DROP(r)` sets residentDirty=1 | D-full | **C** | TC28 |
| 27 | N1 (G_S) | G_E | `WB_CLEAN(r)` **semantic gap** | D-full | **C** | TC7, TC17 |
| 28 | N1 (G_S) | G_I | `WB_DROP(r)` **semantic gap** | D-full | **C** | TC7, TC28 |
| 29 | N1 (G_S) | N1 | `EVICT(sharer not last)` → remove sharer | D-full | **I** | TC22, TC26 |
| 30 | N1 (G_S) | G_I | `EVICT(last sharer)` → commit I | D-full | **I** | TC22, TC26 |
| 31 | N2 (G_E) | G_E | `WB_CLEAN(owner)` | D-full | **C** | TC7, TC17, TC33 |
| 32 | N2 (G_E) | G_I | `WB_DROP(owner)` | D-full | **C** | TC7, TC28 |
| 33 | N2 (G_E) | G_I | `EVICT(owner)` | D-full | **I** | TC22, TC26 |
| 34 | N3 (G_M) | G_E | `WB_CLEAN(owner)` → downgrade | D-full | **C** | TC7, TC17, TC19 |
| 35 | N3 (G_M) | G_I | `WB_DROP(owner)` | D-full | **C** | TC7, TC28 |
| 36 | N3 (G_M) | G_I | `EVICT(owner)` **dirty-owner evict bug** | D-full | **I** | TC22, TC26 (may trigger) |
| **E. Semantic leaks / hazard edges** |
| 37 | L7 | N2/N0 | `WB_*/EVICT(owner)` through RECALL/DONE leak | D-full | **U** | — |
| 38 | L8 | N3/N2/N0 | `WB_*/EVICT(owner)` through RECALL/DONE leak | D-full | **U** | — |
| 39 | N1 (G_S) | — | `UPG_REQ(r∉sharers)` reject (range check gap) | R | **I** | TC9 (neg) |
| 40 | any live | — | `CLR(epoch mismatch)` → tombstone rejected | L | **C** | TC27, TC30, TC38, TC42 |
| 41 | any live | — | `CLR(reqId/src mismatch)` → reject, outstanding kept | R | **I** | TC30, TC38 |
| 42 | L0–L3, L9–L10 | — | `WB_*/EVICT` → BUSY (`isLineBusy()==true`) | R | **I** | TC15 (credit storm may hit BUSY) |
| **F. Cross-cutting / special coverage** |
| 43 | L0 | L0 | `OR_RS/OR_RU` same requester `replayArmed=1` → direct grant | L | **C** | TC18 |
| 44 | L0 | L0 | `OR_RS/OR_RU` different requester → enqueue/merge | L | **I** | TC15, TC22, TC43, TC45 |
| 45 | — | — | Non-DSM address → [FATAL]/page-fault | N/A | **C** | TC9 |
| 46 | — | — | Credit backpressure (RetryAck/PCrdGrant) | L | **C** | TC15 |
| 47 | — | — | Epoch wrap 24-bit boundary | L | **C** | TC27, TC42 |
| 48 | — | — | Cross-socket routing (2-socket NUMA) | — | **C** | TC32, TC33, TC34, TC35, TC39 |
| 49 | — | — | Bloom false-positive → miss → refill | — | **C** | TC23 |
| 50 | — | — | Full protocol matrix (upgrade/wb/recall/inv) | — | **C** | TC44 |

## 2. Uncovered / Priority gaps

| Priority | Gap ID | Description | Risk | Suggested new TC |
|----------|--------|-------------|------|------------------|
| **P0** | E#37–38 | `RECALL/DONE` + `WB/EVICT` semantic leak — directory commit while terminal outstanding blocks requesters | Protocol deadlock / lost request | Stress L7/L8 owner, then issue `WB_DROP` or `EVICT(owner)` before requester retry; verify outstanding is cleaned up |
| **P1** | D#29–30 | `EVICT` on `G_S` (not-last and last sharer) only indirectly tested via capacity pressure | Sharer-set corruption | Explicit TC: wire 3+ sharers then evict one; verify remaining mask and eventual last-sharer evict |
| **P2** | D#33 | `EVICT(owner)` on `G_E` only indirectly tested | Owner loss | Dedicated TC: Node0 owns G_E, then evicts; verify G_I and replay works |
| **P3** | D#36 | `EVICT(owner)` on `G_M` **dirty-owner bug** only indirectly reachable | Data loss / silent dirty-drop | TC forcing Node0 (G_M owner) evict without writeback; verify either rejection or data preservation |
| **P4** | E#39 | `UPG_REQ(r∉sharers)` negative path (range-check gap) | Crash on negative `requesterNode` | Inject `UPG_REQ` from non-sharer node; verify BUSY/FATAL, not UB |
| **P5** | E#41 | `CLR(reqId/src mismatch)` reject → outstanding retained | Orphan outstanding | Mismatch `reqId`/`src` in `CLR`; verify outstanding is NOT retired and subsequent retry still works |
| **P6** | E#42 | `WB`/`EVICT` BUSY rejection on live states only via credit storm | Missing dedicated negative | Hit each live state (L0-L10) with `WB`/`EVICT`; verify BUSY is returned |
| **P7** | E#40 | Tombstone replay paths only tested for CLR with epoch mismatch | Replay of stale data | Variant: tombstone accepted=true vs false; replay with stale data |

## 3. Coverage summary

| Category | Total edges | Covered (C) | Indirect (I) | Uncovered (U) | Coverage % |
|----------|-------------|-------------|--------------|---------------|------------|
| A. Stable→Live | 12 | 12 | 0 | 0 | 100% |
| B. Live→Live | 6 | 6 | 0 | 0 | 100% |
| C. Live→Stable | 6 | 6 | 0 | 0 | 100% |
| D. Stable→Stable | 12 | 8 | 4 | 0 | 100% (67% direct) |
| E. Leaks/hazards | 6 | 1 | 3 | 2 | 67% |
| F. Cross-cutting | 8 | 6 | 2 | 0 | 100% |
| **Total** | **50** | **39** | **9** | **2** | **96% (78% direct)** |

**Bottom line:** The main uncovered edges are the **`RECALL/DONE` semantic leak** (E#37–38) — no TC pushes a writeback or evict through the terminal `RECALL.DONE` barrier. All other state-machine edges are at least indirectly covered. The **P0 gap** should be addressed by a dedicated TC that issues `WB_DROP` or `EVICT(owner)` while the directory still holds a `RECALL/DONE` outstanding.
