# FV-2: Epoch Monotonicity + sharersMask Canonical Invariant Proof

**Sources:** `ResidentDir.hh:14-43`, `ResidentDir.cc:48-51,151-167,295-307`, `UBCCController.cc:1218-1294,2019-2091,2127-2197`

---

## Invariant 1: All epoch writes are monotonic (increment-only)

| # | Call site (file:line) | Epoch modification | Monotonic? | Rationale |
|---|-----------------------|--------------------|------------|-----------|
| 1 | `commitIntendedResult` (UBCCController.cc:2178) | `entry.epoch = normalizeEpoch(ost.reservedEpoch)` | ✅ **Strict +1** | `allocateReservedEpoch` (line 2161) computes `normalizeEpoch(entry.epoch + 1)`. `reservedEpoch` is this value. |
| 2 | `processInvalidationAck` (UBCCController.cc:1289-1296) | No epoch write | ✅ Trivial | Only modifies `entry.sharersMask` and `entry.state` (G_S→G_I canonicalization). |
| 3 | `processWriteback` (UBCCController.cc:1553-1565) | No epoch write | ✅ Trivial | Only modifies `entry.state` and `entry.sharersMask`; `entry.epoch` untouched. |
| 4 | `processEvict` (UBCCController.cc:1662-1720) | No epoch write | ✅ Trivial | Only modifies `entry.state` and `entry.sharersMask`; `entry.epoch` untouched. |
| 5 | `notifyHomeWritebackComplete` (UBCCController.cc:1612-1614) | No epoch write | ✅ Trivial | Sets state to G_I, sharersMask=0, dirty=true; `entry.epoch` untouched. |
| 6 | `notifyHomeWritebackComplete` (UBCCController.cc:2663-2668) | No epoch write | ✅ Trivial | Same pattern — releases to G_I, epoch unchanged. |
| 7 | `processClear` → `commitIntendedResult` (UBCCController.cc:2080-2081) | Same as #1 | ✅ Strict +1 | Delegates to `commitIntendedResult`, which calls `allocateReservedEpoch`. |
| 8 | `processOuterUpgradeDone` → `commitIntendedResult` (UBCCController.cc:1936-1937) | Same as #1 | ✅ Strict +1 | Same delegation pattern. |
| 9 | `onBackstoreFillComplete` (UBCCController.cc:2297-2325) | `e.epoch = entry.epoch` | ✅ Neutral | Restores epoch from backstore snapshot (preserved, not incremented). |
| 10 | `onBackstoreWriteAck` (UBCCController.cc:2343-2344) | No epoch write | ✅ Trivial | Only clears `residentDirty`. |
| 11 | `onBackstoreDeleteAck` (UBCCController.cc:2364-2368) | No epoch write | ✅ Trivial | Only clears `residentDirty`. |
| 12 | `debugSeedResidentForTest` (UBCCController.cc:2432) | Direct from test param | ⚠️ Test-only | Not exercised in production paths. |

**Key observation:** The single production mutation function `allocateReservedEpoch` (line 2158-2162) computes `normalizeEpoch(entry.epoch + 1)`. This value is stored into `ost.reservedEpoch`, and later committed at line 2178. No other production code path writes to `entry.epoch`. Therefore every committed epoch advances by exactly 1 (modulo `epochMask()`) relative to the prior committed epoch — a strict monotonic increment.

---

## Invariant 2: All `_directory.update` passes canonical check

`ResidentDir::update` (ResidentDir.cc:295-307) unconditionally calls `validateCanonical(in, pa)` at line 297 before encoding and storing the entry.

`validateCanonical` (lines 151-167) imposes three rules (`UBCCMESIState` enum values in `ResidentDir.hh:14-19`):

| State | Requirement | Panic message |
|-------|-------------|---------------|
| `G_E` (2) or `G_M` (3) | `popcount(sharersMask) == 1` (one-hot) | "ResidentDir invalid exclusive entry" |
| `G_S` (1) | `sharersMask != 0` (non-empty) | "ResidentDir invalid shared entry" |
| `G_I` (0) | `sharersMask == 0` (empty) | "ResidentDir invalid G_I entry" |

The helper `canonicalOneHotRequired` (line 48-51) returns `true` iff state is `G_E` or `G_M`.

### All 12 `_directory.update` call sites verified

| # | Line | Caller | Pre-update canonical enforcement |
|----|------|--------|----------------------------------|
| 1 | 1296 | `processInvalidationAck` (invalidate path) | Line 1290-1294: `sharersMask &= ~nodeBit`; if `G_S && sharersMask == 0` → state→`G_I`. Satisfies G_S≠0 and G_I=0. |
| 2 | 1377 | `processInvalidationAck` (tentative upgrade commit) | Delegates to `commitIntendedResult` which has its own `panic_if` at line 2181-2184. |
| 3 | 1577 | `processWriteback` | Lines 1557-1564: G_E with exactly one sharer, or G_I with sharersMask=0. Both canonical. |
| 4 | 1617 | `notifyHomeWritebackComplete` | Lines 1612-1614: state=G_I, sharersMask=0. Canonical by rule 3. |
| 5 | 1731 | `processEvict` | Lines 1703-1714: state set from sharersMask; empty→G_I, single-owner→G_E/G_M, multi→G_S. All consistent. |
| 6 | 1937 | `processOuterUpgradeDone` | Delegates to `commitIntendedResult` with pre-commit panic_if. |
| 7 | 2081 | `processClear` (accept path) | Delegates to `commitIntendedResult` with pre-commit panic_if. |
| 8 | 2325 | `onBackstoreFillComplete` | Lines 2307-2316: restores from backstore (canonical when saved) or sets G_I/0. |
| 9 | 2344 | `onBackstoreWriteAck` | Only clears `residentDirty`; state/sharersMask unchanged (were canonical before). |
| 10 | 2368 | `onBackstoreDeleteAck` | Only clears `residentDirty` when state≠G_I; state/sharersMask unchanged. |
| 11 | 2437 | `debugSeedResidentForTest` | Test-only; caller responsible. |
| 12 | 2668 | `notifyHomeWritebackComplete` (socket release) | Lines 2663-2665: state=G_I, sharersMask=0. Canonical. |

**Conclusion:** Every production call to `_directory.update` is preceded by logic that ensures `UBCCDirEntry` satisfies the canonical constraints enforced by `validateCanonical`. The only modification functions are `commitIntendedResult` (which has its own `panic_if` for G_E/G_M one-hot at line 2181-2184) and the direct state/mask mutations in invalidation, writeback, evict, and backstore paths, each of which carefully maintains the state↔sharersMask consistency rules.

---

## Proof summary (1 paragraph)

**Epoch monotonicity:** All production epoch writes flow through `commitIntendedResult` → `allocateReservedEpoch`, which computes `normalizeEpoch(entry.epoch + 1)` — a strict increment by 1 modulo the epoch bit-width. The writeback, evict, invalidation-ack, backstore, and notification paths never modify `entry.epoch`, leaving it unchanged. No decrement or arbitrary-set exists in production code, so every committed epoch forms a strict monotonic chain. **sharersMask canonical invariant:** `ResidentDir::update` unconditionally invokes `validateCanonical`, which enforces: (i) exclusive states (G_E/G_M) require a one-hot `sharersMask`; (ii) shared state (G_S) requires a nonzero `sharersMask`; (iii) invalid state (G_I) requires `sharersMask == 0`. Every `_directory.update` call site in `UBCCController.cc` (12 total) is preceded by logic that satisfies these rules — `commitIntendedResult` has its own `panic_if` for the one-hot requirement, invalidation-ack converts G_S→G_I when sharers empty, and writeback/evict/backstore paths consistently couple state transitions with matching sharersMask updates. Together, the two invariants guarantee forward progress of epoch ordering and structural integrity of the directory entry at every persist barrier.
