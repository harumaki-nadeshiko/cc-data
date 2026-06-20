# FV-3: OutstandingRequest Lifecycle Audit

Source: `UBCCController.hh:80-161`, `UBCCController.cc` — grep + sed read audit.

---

## 1. Create → Remove Lifecycle Table

| # | Entry Point | `opType` | Stage Sequence | Remove Site | Terminal Stage |
|---|-------------|----------|----------------|-------------|----------------|
| R1 | `processOuterRequest` RECALL path | `RECALL` | `CREATED` → `WAITING_TARGET_RESP` → `DONE` (on recall response) → then removed to free slot for GRANT_HANDSHAKE | `removeOutstanding` at `.cc:726` | `DONE` (then immediate recreation as GRANT_HANDSHAKE) |
| R2 | `processOuterRequest` INVALIDATE path (`.cc:660`) | `INVALIDATE` | `CREATED` → `WAITING_ALL_ACKS` → `DONE` (all acks, `.cc:1405`) → **in-place convert** to `GRANT_HANDSHAKE` (`opType=GRANT_HANDSHAKE`, `stage=WAITING_CLEAR`, `.cc:1411-1412`) | `removeOutstanding` on Clear (`.cc:2107`) | `DONE` |
| R3 | `processOuterRequest` direct grant (`.cc:688,879,899`) | `GRANT_HANDSHAKE` | `CREATED` → `WAITING_CLEAR` (immediate) | `removeOutstanding` on Clear (`.cc:2107`) | `DONE` |
| R4 | `processOuterUpgradeReq` with sharers (`.cc:1830`) | `UPGRADE_PENDING` | `CREATED` → `WAITING_ALL_ACKS` (if other sharers) → `WAITING_LOCAL_DONE` → `DONE` + remove | `removeOutstanding` at `.cc:1392` (TENTATIVE early commit) or `.cc:1954` (normal commit) | `DONE` |
| R5 | `processOuterUpgradeReq` fast/no sharers (`.cc:1849`) | `UPGRADE_PENDING` | `CREATED` → `WAITING_LOCAL_DONE` (no other sharers) → `DONE` + remove | `removeOutstanding` at `.cc:1954` | `DONE` |
| R6 | `processClear` stale epoch mismatch (`.cc:2037`) | any (found by `findOutstanding`) | any live stage → **forced tombstone retire + remove** | `removeOutstanding` at `.cc:2049` | n/a (forced cleanup) |

**RemoveOutstanding call sites** (all in `.cc`):

| Call Site | Line | Context |
|-----------|------|---------|
| RECALL done → free slot for GRANT_HANDSHAKE | `726` | RECALL reaches DONE, removed before createOutstanding(GRANT_HANDSHAKE) |
| UPGRADE_PENDING TENTATIVE early commit | `1392` | Done arrived before acks complete, commit and remove |
| UPGRADE_PENDING normal commit | `1954` | processOuterUpgradeDone commits intended result |
| Stale Clear forces retirement | `2049` | epoch/reqId mismatch → retireToTombstone + remove |
| GRANT_HANDSHAKE Clear commit | `2107` | processClear success → retireToTombstone + remove |

---

## 2. Structural Lifecycle: Stage Flow

```
                    ┌──────────────────────────────────────────────┐
                    │          processOuterRequest                  │
                    │  (or processOuterUpgradeReq for UPGRADE)      │
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

Non-terminal stages that count as **busy** (`isLineBusy`, `.cc:1172`):
`CREATED`, `WAITING_TARGET_RESP`, `WAITING_ALL_ACKS`, `WAITING_LOCAL_DONE`, `WAITING_CLEAR`, `PERSISTENT_BUSY`

Terminal stages (`DONE`, `CANCELLED`, `TIMED_OUT`) → **not busy**.

---

## 3. Leak Check: Written-Never-Read Fields

| Field | Written (set sites) | Read Sites | Verdict |
|-------|---------------------|------------|---------|
| `homeNode` (`.hh:89`) | Declared, init `-1`, never set to non-default in `.cc` | **NONE** in runtime logic | ⚠️ **DORMANT** — stored but never consumed; may be used by serialization but no read found |
| `accepted` (`.hh:113`) | Init `false`, set `true` at `.cc:1350` and `.cc:1839` | Used in processOuterUpgradeDone stage gates | ✅ Live |
| `upgradeDoneArrived` / `upgradeDoneEpoch` / `upgradeDoneReqId` / `upgradeSavedStage` (`.hh:135-138`) | Init `false/0/0/CREATED`, set at `.cc:1910-1913` | Read at `.cc:1382` for TENTATIVE early-commit path | ✅ Live |

**Note**: `clearAckCached` has been **removed** from the struct — no longer present in current code. Previous dead-field finding is resolved.

---

## 4. ackMask Monotonic Proof

**Invariant**: `ackMask` (and `upgradeAckMask`) are **strictly monotonic** — bits are only ever set, never cleared or decremented.

| Operation | Location | Effect on `ackMask` / `upgradeAckMask` |
|-----------|----------|----------------------------------------|
| Init (INVALIDATE create) | `.cc:670` | `invOreq->ackMask = 0` |
| Init (generic createOutstanding) | `.cc:2608` | `req.ackMask = 0` |
| Init (UPGRADE_PENDING create) | `.cc:1836` | `oreq->upgradeAckMask = 0` |
| On each InvalidationAck | `.cc:1291` | `effAckMask \|= nodeBit` — **only write, strictly monotonic** |
| On all-acks-done | `.cc:1333-1334` | Only **reads** `pendingAckCount == 0` — mask never modified |
| Convert INVALIDATE→GRANT_HANDSHAKE | `.cc:1411-1412` | `ackMask` not touched (carried into GRANT_HANDSHAKE) |
| `getPendingInvalidationMask` | `.cc:1449` | Read-only: `totalMask & ~ackMask` |
| Serialization | `.cc:983` | Read-only: `"invalidatedAckMask"` in JSON dump |

**Corollary**: `pendingAckCount` monotonically **decreases** (decremented at `.cc:1293,1295`), while `ackMask` monotonically **increases**. `pendingAckCount == 0` iff `ackMask == totalMask` (all expected acks received).

---

## 5. replayArmed Conditions

| Set Site | File & Line | Condition / Trigger |
|----------|-------------|---------------------|
| S1 | `.cc:1413` | INVALIDATE all-acks-complete → in-place convert to GRANT_HANDSHAKE: `ost->replayArmed = true` |
| S2 | `.cc:2553` | `replayPendingRequesters` creates a new outstanding from queue replay: `ost->replayArmed = true` |

| Read/Test Site | File & Line | Logic |
|----------------|-------------|-------|
| T1 | `.cc:448-465` (`processOuterRequest`) | If `existing->replayArmed && stage == WAITING_CLEAR && reqId match && reqType match && writeIntent match` → **grant retry-hit directly** (bypasses BUSY) |
| T2 | `.cc:439` | Debug log prints `replayArmed=1` for any existing outstanding found |

**Default**: `false` (set in struct init `.hh:148` and `createOutstanding` default init).

**Guarantee**: `replayArmed` is never cleared once set — it is sticky for the lifetime of the `OutstandingRequest` struct.

---

## 6. Tombstone Transition (retireToTombstone + checkTombstone)

| Operation | Location | Mechanism |
|-----------|----------|-----------|
| Retire GRANT_HANDSHAKE | `.cc:2222-2233` | Copies `(linePa, baseEpoch, reqId, opType, accepted)` into `GrantHandshakeTombstone` with `expireTick = curTick() + tombstoneWindowW` |
| Store | `.cc:2233` | Appended to `_tombstones[linePa]` deque (multi-entry per PA) |
| Lookup | `checkTombstone()` | cleanupTombstones() then scan deque for matching `(epoch, reqId)` |
| Replay guard | `.cc:528-538` | Tombstone hit → return idempotent grant without creating new outstanding |
| Clear guard | `.cc:1992-1998` | Tombstone hit → `tsAccepted` reflects original Clear outcome |

---

## 7. Summary of Red Flags

1. **`homeNode`**: Stored in struct (`.hh:89`), initialized `-1`, never consumed in `.cc` runtime logic. Possibly serialization-only or legacy.
2. **RECALL→GRANT_HANDSHAKE split**: RECALL reaches `DONE` then is **removed** (`.cc:726`) and a *new* `GRANT_HANDSHAKE` is created via `createOutstanding`. INVALIDATE does an **in-place conversion** (`.cc:1411-1412`). This inconsistency is intentional (avoids PA-key collision) but creates two different code paths for the same conceptual transition.
3. **No `removeOutstanding` guard**: `removeOutstanding` unconditionally erases — no check that the outstanding is in a terminal stage. Callers must ensure this.
4. **`clearAckCached` removed**: Previously flagged dead field no longer present — resolution confirmed.
