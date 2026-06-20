# FV-3: OutstandingRequest Lifecycle Audit

Source: `UBCCController.hh:61-161`, `UBCCController.cc` — grep + read audit.

---

## 1. Create → Remove Lifecycle Table

| # | Entry Point | `opType` | `stage` Sequence | Remove Site | Terminal Stage |
|---|---|---|---|---|---|
| R1 | `processOuterRequest` RECALL path (`.cc:859`) | `RECALL` | `CREATED` → `WAITING_TARGET_RESP` → `DONE` (on recall response, `.cc:1129`) → then **removed** at `.cc:725` to free slot for GRANT_HANDSHAKE | `removeOutstanding` at `.cc:725` | `DONE` (then immediate recreation as GRANT_HANDSHAKE) |
| R2 | `processOuterRequest` INVALIDATE path (`.cc:660`) | `INVALIDATE` | `CREATED` → `WAITING_ALL_ACKS` → `DONE` (all acks, `.cc:1393`) → **in-place convert** to `GRANT_HANDSHAKE` (`opType=GRANT_HANDSHAKE`, `stage=WAITING_CLEAR`, `.cc:1399-1400`) | `removeOutstanding` on Clear (`.cc:2085`) | `DONE` |
| R3 | `processOuterRequest` direct grant (`.cc:688,879,899`) | `GRANT_HANDSHAKE` | `CREATED` → `WAITING_CLEAR` (immediate) | `removeOutstanding` on Clear (`.cc:2085`) | `DONE` |
| R4 | `handleLocalUpgrade` UPGRADE_PENDING path (`.cc:1791`) | `UPGRADE_PENDING` | `CREATED` → `WAITING_ALL_ACKS` (if other sharers) → `WAITING_LOCAL_DONE` → `DONE` + remove | `removeOutstanding` at `.cc:1380` (early if Done cached), or `.cc:1942` (normal commit) | `DONE` |
| R5 | `handleLocalUpgrade` UPGRADE_PENDING fast (`.cc:1837`) | `UPGRADE_PENDING` | `CREATED` → `WAITING_LOCAL_DONE` (no other sharers) → `DONE` + remove | `removeOutstanding` at `.cc:1942` | `DONE` |
| R6 | `processClear` stale epoch mismatch (`.cc:2037`) | any (found by `findOutstanding`) | any live stage → **forced tombstone retire + remove** | `removeOutstanding` at `.cc:2037` | n/a (forced cleanup) |

**Remove-outstanding call sites** (all in `.cc`):
- `725` — RECALL done, free slot for new GRANT_HANDSHAKE
- `1380` — UPGRADE_PENDING Done arrived before acks (TENTATIVE)
- `1942` — UPGRADE_PENDING normal commit
- `2037` — stale Clear forces retirement
- `2085` — GRANT_HANDSHAKE Clear commit

---

## 2. Structural Lifecycle: Stage Flow

```
                    ┌──────────────────────────────────────────────┐
                    │          processOuterRequest                  │
                    │  (or handleLocalUpgrade for UPGRADE_PENDING)  │
                    └──────────────┬───────────────────────────────┘
                                   │
              ┌────────────────────┼────────────────────┬──────────────────────┐
              ▼                    ▼                    ▼                      ▼
         RECALL              INVALIDATE          GRANT_HANDSHAKE        UPGRADE_PENDING
         CREATED              CREATED              CREATED                CREATED
              │                    │                    │                     │
              ▼                    ▼                    ▼                     ▼
     WAITING_TARGET_RESP   WAITING_ALL_ACKS      WAITING_CLEAR       WAITING_ALL_ACKS
              │                    │                    │            (if other sharers)
              ▼                    ▼                    ▼                     │
            DONE                DONE                  Clear            WAITING_LOCAL_DONE
              │                    │                    │                     │
              │  remove + create   │ in-place convert   │                     ▼
              │  GRANT_HANDSHAKE   │ GRANT_HANDSHAKE    │                   DONE
              ▼                    ▼                    ▼                     │
        WAITING_CLEAR        WAITING_CLEAR          DONE               removeOutstanding
              │                    │                    │
              ▼                    ▼                    ▼
            Clear                Clear               DONE
              │                    │
              ▼                    ▼
            DONE                 DONE
         removeOutstanding   removeOutstanding
```

Non-terminal stages that count as **busy** (`isLineBusy`, `.cc:1160-1175`):
`CREATED`, `WAITING_TARGET_RESP`, `WAITING_ALL_ACKS`, `WAITING_LOCAL_DONE`, `WAITING_CLEAR`, `PERSISTENT_BUSY`

Terminal stages (`DONE`, `CANCELLED`, `TIMED_OUT`) → **not busy**.

---

## 3. Leak Check: Written-Never-Read Fields

| Field | Written (set sites) | Read sites | Verdict |
|---|---|---|---|
| `clearAckCached` | `.hh:106` declared, `.hh:149` init `false`, `.cc:2570` `req.clearAckCached = false` in `createOutstanding` | **NONE** | 🔴 **DEAD FIELD** — set twice but never tested or branched on. Comment says "True if ClearAck has been cached for tombstone replay" but feature is unimplemented. |
| `accepted` | `.hh:114`, `.hh:151` init `false`, `.cc:1338` set `true`, `.cc:1839` set `true` | Used in `handleLocalUpgrade` (stage gates) | ✅ Live |
| `homeNode` | `.hh:89` declared, init `-1` | **NONE in `.cc`** — stored but never consumed in runtime logic | ⚠️ **DORMANT** — may be used by serialization or debug print; not dead but check intent |
| `upgradeDoneArrived` / `upgradeDoneEpoch` / `upgradeDoneReqId` / `upgradeSavedStage` | `.hh:135-138`, init `false/0/0/CREATED` | Read at `.cc:1370` for early-commit TENTATIVE path | ✅ Used |

---

## 4. ackMask Monotonic Proof

**Invariant**: `ackMask` (and `upgradeAckMask`) are **strictly monotonic** — bits are only ever set, never cleared or decremented.

| Operation | Location | Effect on `ackMask` / `upgradeAckMask` |
|---|---|---|
| Init (INVALIDATE create) | `.cc:669` | `invOreq->ackMask = 0` |
| Init (generic createOutstanding) | `.cc:2578` | `req.ackMask = 0` |
| Init (UPGRADE_PENDING create) | `.cc:1824` | `oreq->upgradeAckMask = 0` |
| On each ack | `.cc:1279` | `effAckMask \|= nodeBit` — **only write, strictly monotonic** |
| On all-acks-done | `.cc:1321-1322` | Only **reads** `pendingAckCount == 0` — mask never modified |
| Convert INVALIDATE→GRANT_HANDSHAKE | `.cc:1393-1400` | `ackMask` not touched (carried into GRANT_HANDSHAKE) |
| `getPendingInvalidationMask` | `.cc:1437` | Read-only: `totalMask & ~ackMask` |
| Serialization | `.cc:983` | Read-only: `"invalidatedAckMask"` in JSON dump |

**Corollary**: `pendingAckCount` monotonically **decreases** (decremented at `.cc:1281,1283`), while `ackMask` monotonically **increases**. `pendingAckCount == 0` iff `ackMask == totalMask` (all expected acks received).

---

## 5. replayArmed Conditions

| Set Site | File & Line | Condition / Trigger |
|---|---|---|
| S1 | `.cc:1401` | INVALIDATE all-acks-complete → in-place convert to GRANT_HANDSHAKE: `ost->replayArmed = true` |
| S2 | `.cc:2522` | `replayPendingRequesters` creates a new outstanding from queue replay: `ost->replayArmed = true` |

| Read/Test Site | File & Line | Logic |
|---|---|---|
| T1 | `.cc:430-465` (`processOuterRequest`) | If `existing->replayArmed && stage == WAITING_CLEAR && reqId match && reqType match && writeIntent match` → **grant retry-hit directly** (bypasses BUSY) |
| T2 | `.cc:435-438` | Debug log prints `replayArmed=1` for any existing outstanding found |

**Default**: `false` (set in struct init `.hh:149` and `createOutstanding` `.cc:2571`).

**Guarantee**: `replayArmed` is never cleared once set — it is sticky for the lifetime of the `OutstandingRequest` struct.

---

## 6. Tombstone Transition (retireToTombstone + checkTombstone)

| Operation | Location | Mechanism |
|---|---|---|
| Retire GRANT_HANDSHAKE | `.cc:2200-2219` | Copies `(linePa, baseEpoch, reqId, opType, accepted)` into `GrantHandshakeTombstone` with `expireTick = curTick() + tombstoneWindowW` |
| Store | `.cc:2211` | Appended to `_tombstones[linePa]` deque (multi-entry per PA) |
| Lookup | `.cc:2221-2240` | `cleanupTombstones()` then scan deque for matching `(epoch, reqId)` |
| Replay guard | `.cc:528-538` | Tombstone hit → return idempotent grant without creating new outstanding |
| Clear guard | `.cc:1981-1987` | Tombstone hit → `tsAccepted` reflects original Clear outcome |

---

## 7. Summary of Red Flags

1. **`clearAckCached`**: Declared, initialized in constructor and `createOutstanding`, but **never read** — dead field, 3 LoC of technical debt.
2. **`homeNode`**: Stored in struct, initialized `-1`, never consumed in `.cc` runtime logic.
3. **RECALL→GRANT_HANDSHAKE split**: RECALL reaches `DONE` then is **removed** (`.cc:725`) and a *new* `GRANT_HANDSHAKE` is created. INVALIDATE does an **in-place conversion** (`.cc:1399`). This inconsistency is intentional (avoiding PA-key collision) but creates two different code paths for the same conceptual transition.
4. **No `removeOutstanding` guard**: `removeOutstanding` unconditionally erases — no check that the outstanding is in a terminal stage. Callers must ensure this.
