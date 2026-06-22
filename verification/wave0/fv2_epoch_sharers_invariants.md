# FV-2: Epoch Monotonicity + SharersMask Invariant Proof

**Method**: Static proof over `ResidentDir.{hh,cc}` and `UBCCController.{hh,cc}`.
**Scope**: committed `epoch`, `sharersMask`, and `residentDirty` write-points touched by Clear, UpgradeDone, InvalidationAck, Writeback, and Evict.

---

## 1. Write-Point Audit

| Write-Point | Line | Old State | New State | Epoch Check | Sharers Check | Proof Status |
|---|---|---|---|---|---|---|
| `allocateReservedEpoch()` | `UBCCController.cc` L2197-L2200 | committed `entry.epoch = E` | no commit; returns `normalize(E+1)` | Monotone by construction **only before wrap/truncation** | N/A | **Conditional** |
| `commitIntendedResult()` via `processClear()` | `processClear` L2107-L2111, `commitIntendedResult` L2204-L2233 | committed directory snapshot | `state/sharers/epoch/residentDirty` overwritten from `OutstandingRequest.intended*` | `validateEpochMonotonic(old,reserved)` at L2218-L2219, then `entry.epoch=normalize(reserved)` at L2232 | `G_E/G_M` repaired to one-hot at L2222-L2228; all updates rechecked by `ResidentDir::update()` → `validateCanonical()` (`ResidentDir.cc` L341-L343, L197-L213) | **Conditional / FAIL if epoch width mismatch** |
| `commitIntendedResult()` via `processOuterUpgradeDone()` | `processOuterUpgradeDone` L1952-L1958, `commitIntendedResult` L2204-L2233 | committed directory snapshot | owner-only `G_E/G_M` result from `UPGRADE_PENDING.intended*` | Same as above; source is `oreq->reservedEpoch` set at L1820 after `allocateReservedEpoch()` L1802-L1803 | `intendedSharersMask=0`, `intendedOwnerNode=requester`; commit repairs to one-hot owner bit | **Conditional / FAIL if epoch width mismatch** |
| `commitIntendedResult()` via cached early `UpgradeDone` | `processInvalidationAck` L1383-L1390, `commitIntendedResult` L2204-L2233 | committed shared snapshot after final ack | owner-only `G_E/G_M` result from same `UPGRADE_PENDING.intended*` | Same as above | Same as above; `ResidentDir::update()` still enforces canonical form | **Conditional / FAIL if epoch width mismatch** |
| `processInvalidationAck()` — INVALIDATE path | L1297-L1309 | committed `G_S` with pending sharers | `sharersMask &= ~ackBit`; if empty, `G_S -> G_I` | No epoch write; stale acks rejected by `checkEpochForLine()` L1232-L1240 | Empty shared set is immediately canonicalized to `G_I` at L1302-L1306, then `update()` validates | **Proved** |
| `processInvalidationAck()` — UPGRADE path | L1253-L1272, L1334-L1401 | committed `G_S` | unchanged until final commit | No epoch write; stale acks rejected by `checkEpochForLine()` L1232-L1240 | No committed sharer mutation before final `commitIntendedResult()` | **Proved** |
| `processWriteback()` | L1543-L1594 | owner-held `G_M/G_E` | `G_E` with requester one-hot, or `G_I` with zero sharers | No epoch write; stale epoch rejected at L1543-L1550 | `keepAsClean` sets one-hot mask at L1570-L1574; drop path sets `G_I, mask=0` at L1574-L1578 | **Proved** |
| `processEvict()` | L1660-L1752 | `G_S/G_E` (dirty `G_M` owner evict rejected) | `G_S` non-empty, or `G_I` empty, or unchanged exclusive owner | No epoch write; stale epoch rejected at L1660-L1667 | Sharer bit removed at L1683-L1689; dirty owner eviction rejected at L1698-L1703; empty result canonicalized to `G_I` at L1722-L1725 | **Proved** |
| Canonical sink | `ResidentDir.cc` L197-L213, L296-L343 | any caller-supplied entry | stored entry | No epoch relation checked here | Enforces: `G_S => mask!=0`, `G_I => mask==0`, `G_E/G_M => one-hot` | **Proved for mask only** |

---

## 2. Per-Invariant Proof Sketches

### I1. Committed epoch must not move backward
1. Every successful Clear/Upgrade commit writes `entry.epoch` only in `commitIntendedResult()` (`UBCCController.cc` L2232).
2. The committed value comes only from `OutstandingRequest.reservedEpoch`; every creator sets that field from `allocateReservedEpoch(entry)` (`UBCCController.cc` L546, L1803).
3. `allocateReservedEpoch()` computes `normalize(entry.epoch + 1)` (`UBCCController.cc` L2197-L2200), and `commitIntendedResult()` checks `validateEpochMonotonic(old,reserved)` before overwrite (L2218-L2219, L2719-L2725).
4. Therefore the **logical pre-store** transition is forward-only **if** `normalize()` does not wrap and if the stored representation preserves all epoch bits.
5. This proof fails globally because `ResidentDir::encodeEntry()` truncates epoch to 24 bits (`ResidentDir.cc` L222-L228) while `_epochBits` is allowed up to 64 and defaults to 64 (`UBCCController.hh` L617, `UBCCController.cc` L87-L90).

### I2. Clear commits preserve sharers canonical form
1. `processClear()` accepts only a live `GRANT_HANDSHAKE` with matching `(baseEpoch, reqId, requester, stage)` (`UBCCController.cc` L2037-L2100), so the commit source is a previously constructed `OutstandingRequest`.
2. All `GRANT_HANDSHAKE.intended*` fields are initialized in `processOuterRequest()` (`UBCCController.cc` L559-L569, L577-L587, L594-L604, L617-L627, L688-L698, L731-L763, L879-L911) or inherited from INVALIDATE after all acks (`UBCCController.cc` L1410-L1418).
3. Shared commits always provide non-zero masks (`1<<requester`, `entry.sharersMask | 1<<requester`, or `requester|existingOwner`). Unique commits provide `intendedOwnerNode=requester` and `intendedSharersMask=0`.
4. `commitIntendedResult()` repairs exclusive states to one-hot owner form when needed (`UBCCController.cc` L2222-L2228), then `ResidentDir::update()` re-validates canonical constraints (`ResidentDir.cc` L341-L343, L197-L213).

### I3. UpgradeDone commits preserve sharers canonical form
1. `processOuterUpgradeReq()` creates `UPGRADE_PENDING` only after confirming the requester is already in the committed sharer set (`UBCCController.cc` L1781-L1789) and sets `intendedState` to `G_E/G_M`, `intendedOwnerNode=requester`, `intendedSharersMask=0` (`UBCCController.cc` L1825-L1834).
2. `processOuterUpgradeDone()` commits only from `WAITING_LOCAL_DONE` and only when `accepted==true` (`UBCCController.cc` L1935-L1955); early Done is cached, not committed (`UBCCController.cc` L1914-L1932).
3. The final commit again flows through `commitIntendedResult()`, which synthesizes a one-hot owner mask for `G_E/G_M` (`UBCCController.cc` L2222-L2228).
4. The cached-early-Done fast path in `processInvalidationAck()` also uses `commitIntendedResult()` (`UBCCController.cc` L1383-L1390), so the same argument applies.

### I4. InvalidationAck never leaves an illegal committed sharers mask
1. INVALIDATE acks are accepted only for a matching outstanding INVALIDATE or `UPGRADE_PENDING` in `WAITING_ALL_ACKS` (`UBCCController.cc` L1253-L1266).
2. On the plain INVALIDATE path, the committed directory drops only the acked bit (`entry.sharersMask &= ~nodeBit`, L1301) and immediately rewrites `G_S,mask=0` to `G_I,mask=0` (`UBCCController.cc` L1302-L1306).
3. On the UPGRADE path, committed sharers are not modified per-ack at all; only ack counters change until the final owner-only commit (`UBCCController.cc` L1334-L1401).
4. Every committed mutation goes through `_directory.update()` and then `validateSharersCanonical()` on the plain INVALIDATE path (`UBCCController.cc` L1307-L1309).

### I5. Writeback/Evict preserve canonical sharers and protocol-dirty semantics
1. `processWriteback()` and `processEvict()` both reject stale epochs before mutation (`UBCCController.cc` L1543-L1550, L1660-L1667).
2. `processWriteback()` clears **protocol dirty** by moving state out of `G_M` (`DirEntry::protoDirty()` is state-derived, `ResidentDir.cc` L43-L46), but intentionally sets `residentDirty=true` because the resident directory has changed and must later be written back (`UBCCController.cc` L1570-L1594).
3. `processEvict()` either removes one sharer and stays `G_S`, or empties the set and converts to `G_I`; dirty-owner clean-evict is rejected (`UBCCController.cc` L1683-L1752).
4. Thus the sharers mask stays canonical, and `residentDirty` tracks backstore divergence rather than cache-line data dirtiness.

### I6. `validateCanonical()` coverage audit
1. `ResidentDir::validateCanonical()` checks exactly the canonical mask constraints requested: one-hot for `G_E/G_M`, non-zero for `G_S`, zero for `G_I` (`ResidentDir.cc` L197-L213).
2. Both `ResidentDir::insert()` and `ResidentDir::update()` call it before storage (`ResidentDir.cc` L296-L343), so every resident commit is checked.
3. It does **not** check epoch monotonicity, `residentDirty`, or any relation between `state` and `epoch`; those must be proven in the controller.
4. Therefore S1/S2/S3 are fully covered for mask shape; epoch and dirty semantics are outside this function's coverage.

---

## 3. ResidentDirty Transition Rules

- `commitIntendedResult()` always sets `residentDirty=true` on committed Clear/UpgradeDone results (`UBCCController.cc` L2233).
- `processWriteback()` and `processEvict()` also set `residentDirty=true` after mutating resident state (`UBCCController.cc` L1579, L1737).
- Backstore reconciliation clears the flag only in fill/ack handlers: `onBackstoreFillComplete()` (`UBCCController.cc` L2359-L2367), `onBackstoreWriteAck()` (L2391-L2395), and `onBackstoreDeleteAck()` (L2414-L2418).
- Therefore `residentDirty` is a **resident/backstore synchronization bit**, not the MESI dirty bit; protocol dirty remains `state == G_M` (`ResidentDir.cc` L43-L46).

---

## 4. TOCTOU / Interleaving Concerns

1. **Critical — epoch width mismatch / silent truncation**  
   - `_epochBits` may be any value in `1..64` and defaults to `64` (`UBCCController.hh` L617, `UBCCController.cc` L87-L90).  
   - `ResidentDir::encodeEntry()` stores only 24 epoch bits (`ResidentDir.cc` L222-L228).  
   - Counterexample trace: old committed epoch `0x00ffffff` → `allocateReservedEpoch()` returns `0x01000000` → `validateEpochMonotonic()` passes → `ResidentDir::update()` stores `0x000000` → next lookup observes backward epoch.  
   - **Result**: absolute epoch-monotonicity is **not proved** unless the design adds `epochBits <= 24` enforcement or widens resident storage.

2. **Low — cached early UpgradeDone commit skips explicit `validateSharersCanonical()` call**  
   - The deferred commit inside `processInvalidationAck()` (`UBCCController.cc` L1383-L1390) updates the directory without the extra UBCC-side checker used by the normal Clear/UpgradeDone paths.  
   - Safety is still preserved because `ResidentDir::update()` runs `validateCanonical()` before storage.  
   - This is a checker-coverage gap, not a current sharers safety bug.

3. **Audited-safe interleaving — in-flight outstanding blocks competing mutators**  
   - `isLineBusy()` treats non-terminal outstanding states as busy (`UBCCController.cc` L1171-L1185).  
   - `processWriteback()` and `processEvict()` reject when busy (`UBCCController.cc` L1534-L1541, L1670-L1677), and Clear/UpgradeDone are tuple/stage-validated before commit.  
   - So no second mutator can race in and rewrite `epoch`/`sharersMask` between reserved-epoch allocation and final commit.

---

## 5. Final Verdict

- **SharersMask invariants (S1/S2/S3)**: **proved** for Clear, UpgradeDone, InvalidationAck, Writeback, and Evict. The controller constructs canonical masks, and `ResidentDir::update()/insert()` enforce the same canonical rules again.
- **Committed epoch monotonicity**: **not proved unconditionally**. The controller-side logic is forward-only before storage, but resident storage truncates epochs to 24 bits while the controller admits up to 64-bit epochs. A concrete backward-epoch trace exists unless the implementation constrains `_epochBits <= 24` or widens the resident encoding.
