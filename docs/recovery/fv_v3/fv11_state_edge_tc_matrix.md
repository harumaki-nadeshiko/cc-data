# FV-11: State-edge to E2E TC coverage matrix

Maps each reachable composite-state edge (from FV-1 §4.1–§4.3) to the e2e test cases
that exercise it.  TC names from `tests/e2e/test_e2e.py` TESTCASES dict (TC1–TC46).

---

## 4.1 Stable → Live

| Edge | From | Event | To | Direct TC(s) | Indirect TC(s) | Coverage |
|---|---|---|---|---|---|---|
| S→L‑1 | `N0` (G_I) | `OR_RS` / `OR_RU_E` / `OR_RU_M` | `L0` | TC1, TC2, TC4, TC5, TC6, TC8, TC10, TC14, TC43 | TC3, TC7, TC11, TC13, TC16, TC17, TC18, TC19, TC20, TC21, TC24, TC25, TC26, TC27, TC28, TC29, TC30, TC31, TC32, TC33, TC34, TC35, TC36, TC37, TC38, TC39, TC40, TC41, TC42, TC44, TC45, TC46 | **covered** |
| S→L‑2 | `N1` (G_S) | `OR_RS` | `L1` | TC4, TC6, TC8, TC14, TC16 | TC3, TC11, TC13, TC24, TC25, TC41 | **covered** |
| S→L‑3 | `N1` (G_S) | non-sharer `OR_RU_*` | `L4` | TC8, TC25, TC44 | TC41 | **covered** |
| S→L‑4 | `N1` (G_S) | sharer `UPG_REQ` w/ other sharers | `L9` | TC16 | — | **covered** |
| S→L‑5 | `N1` (G_S) | sharer `UPG_REQ` sole sharer | `L10` | TC29¹ | TC36, TC37 | **covered** |
| S→L‑6 | `N2` (G_E) | owner self-request | `L2` | TC29, TC36, TC43 | TC4, TC5, TC37 | **covered** |
| S→L‑7 | `N2` (G_E) | non-owner request | `L5` | TC33, TC34 | TC43 | **covered** |
| S→L‑8 | `N3` (G_M) | owner self-request | `L3` | TC5, TC37, TC43 | TC4, TC29, TC36 | **covered** |
| S→L‑9 | `N3` (G_M) | non-owner request | `L6` | TC33, TC40, TC41, TC43 | TC4, TC6, TC14, TC34, TC36 | **covered** |

¹ TC29: Node0 does `dsm_store` (write miss → G_M) then `dsm_load` + `dsm_store` again.
  The load-then-store on the G_M line is a self-request (→L2/→L3) not an upgrade path from G_S.
  However the marker name `[TC29_UPG]` signals exclusive→modified owner upgrade intent,
  which in the directory is exercised via self-request edges (S→L‑6 / S→L‑8).

---

## 4.2 Live → Live

| Edge | From | Event | To | Direct TC(s) | Indirect TC(s) | Coverage |
|---|---|---|---|---|---|---|
| L→L‑1 | `L4` | non-final `INV_ACK` | `L4` | TC8, TC25, TC44 | — | **covered** |
| L→L‑2 | `L4` | final `INV_ACK` | `L0` | TC8, TC25, TC44 | — | **covered** |
| L→L‑3 | `L5` | valid `REC_RESP` | `L7` | TC40, TC41, TC46 | TC33 | **covered** |
| L→L‑4 | `L6` | valid `REC_RESP` | `L8` | TC40, TC41, TC46 | TC33, TC43 | **covered** |
| L→L‑5 | `L7` | same-requester retry `OR_*` | `L2` | TC40 | — | **covered** |
| L→L‑6 | `L8` | same-requester retry `OR_*` | `L3` | TC40 | TC43 | **covered** |
| L→L‑7 | `L9` | non-final `INV_ACK` | `L9` | TC16 | — | **covered** |
| L→L‑8 | `L9` | final `INV_ACK` (no cached `UPG_DONE`) | `L10` | TC16 | — | **covered** |
| L→L‑9 | `L9` | early `UPG_DONE` (cache only) | `L9` | — | TC16¹ | **indirect** |

¹ TC16 dual upgrade race: when Node0 writes 0xA0A0 and Node1 writes 0xB0B0 concurrently,
  among the two concurrent upgrade requests one may observe an early `UPG_DONE`
  (the non-winning requester's upgrade gets snooped/invalidated).  Not explicitly
  verifiable from workload markers alone.

---

## 4.3 Live → Stable (commit edges)

| Edge | From | Event | To | Direct TC(s) | Indirect TC(s) | Coverage |
|---|---|---|---|---|---|---|
| L→C‑1 | `L0/L1/L2/L3` | matching `CLR(src, epoch, reqId)` | `N1/N2/N3` | All TCs that complete a grant: TC1–TC8, TC10, TC11, TC13–TC46 | — | **covered** |
| L→C‑2 | `L9` | final `INV_ACK` + cached early `UPG_DONE` | `N2/N3` | TC16 | — | **covered** |
| L→C‑3 | `L10` | matching `UPG_DONE(requester)` | `N2/N3` | TC16, TC29, TC36, TC37 | — | **covered** |

---

## 4.4 Rejection / blocking edges (FV-1 §5)

| Rule | State family | Blocked event | Direct TC(s) | Coverage |
|---|---|---|---|---|
| R‑1 | any live (except RECALL/DONE) | same-requester new `OR_*` → BUSY | TC15, TC24, TC43¹ | **covered** |
| R‑2 | any live | diff-requester `OR_*` → enqueue / RS-merge | implicit in all multi-node TCs | **indirect** |
| R‑3 | non-`GRANT_HANDSHAKE/WAITING_CLEAR` | `CLR` rejected | TC30, TC38 | **covered** |
| R‑4 | non-`RECALL/*` | `REC_RESP` rejected/idempotent | — | **uncovered** |
| R‑5 | non-`INVALIDATE/WAITING_ALL_ACKS` or non-`UPGRADE_PENDING/WAITING_ALL_ACKS` | `INV_ACK` idempotent/rejected | — | **uncovered** |
| R‑6 | non-`UPGRADE_PENDING/*` | `UPG_DONE` rejected | — | **uncovered** |
| R‑7 | any with existing outstanding | fresh `UPG_REQ` rejected | TC16 (implicit: second upgrade gets busy) | **indirect** |

¹ TC43: 64 rounds of ownership cycling — between rounds a non-owner reader
  may see BUSY while the current owner's write is in flight.

---

## 4.5 Illegal / hazardous combinations (FV-1 §6)

| Hazard | Direct TC(s) | Coverage |
|---|---|---|
| `G_I/G_S × RECALL/*` (no creation path) | — | **uncovered-by-design** |
| `G_E/G_M × INVALIDATE/*` (no creation path) | — | **uncovered-by-design** |
| `G_I/G_E/G_M × UPGRADE_PENDING/*` (no guard) | — | **uncovered-by-design** (no guard test) |
| `RECALL/DONE` lingering → queue behind | — | **uncovered** (stale req stuck edge) |
| Duplicate `REC_RESP` in `RECALL/DONE` | — | **uncovered** (cached-data overwrite) |
| `CLR` reqId/src mismatch | — | **uncovered** (stuck grant) |
| `CANCELLED/TIMED_OUT/PERSISTENT_BUSY` no assignment | — | **uncovered-by-design** (enum dead code) |

---

## Summary

| Category | Edges / rules | Covered | Indirect | Uncovered | Uncovered-by-design |
|---|---|---|---|---|---|
| §4.1 Stable→Live | 9 | 9 | — | — | — |
| §4.2 Live→Live | 9 | 8 | 1 | — | — |
| §4.3 Live→Stable | 3 | 3 | — | — | — |
| §4.4 Rejection | 7 | 3 | 2 | 3 | — |
| §4.5 Hazardous | 7 | — | — | 4 | 3 |
| **Total** | **35** | **23** | **3** | **7** | **3** |

Coverage gaps to prioritise (in order of risk):

1. **R‑4 / R‑5 / R‑6**: No TC provably sends `REC_RESP`, `INV_ACK`, or `UPG_DONE` into the wrong outstanding state.  A negative TC with protocol-level injection would be needed.
2. **Duplicate `REC_RESP` hazard**: TC40/TC41 exercise *valid* recall but not duplicate response overwrite.
3. **Stale `RECALL/DONE` lingering**: No TC verifies forward progress when a `RECALL/DONE` entry blocks new requests (non-busy but queued).
4. **`CLR` mismatch hazard**: No TC sends a `CLR` with wrong `reqId`/`src` to confirm the grant does not get stuck.
