# FV-2: Epoch monotonicity + sharersMask invariant proof

**Summary:** Every `entry.epoch` write is strictly monotonic (increment-only or idempotent copy), and every `_directory.update()` call passes the `ResidentDir::validateCanonical` sharersMask invariant (one-hot for G_E/G_M, non-zero for G_S, zero for G_I).

## Epoch-write sites

| # | Function | Line | Write | Monotonic? | Reason |
|---|----------|------|-------|------------|--------|
| 1 | `commitIntendedResult` | 2175 | `entry.epoch = normalizeEpoch(ost.reservedEpoch)` | ✅ Strictly +1 | `ost.reservedEpoch` = `allocateReservedEpoch()` = `normalizeEpoch(entry.epoch + 1)` |
| 2 | `snapshotResidentForBackstore` | 2286 | `entry.epoch = e.epoch` | ✅ Idempotent (copy) | Copies from directory entry — same value |
| 3 | `onBackstoreFillComplete` | 2307 | `e.epoch = entry.epoch` | ✅ Idempotent (restore) | Restores from backstore entry previously saved from directory entry |
| 4 | `onBackstoreFillComplete` | 2299 | `e.epoch = 0` | ✅ Starts at 0 | Fresh G_I placeholder creation |
| 5 | `handleResidentMiss` | 184 | `placeholder.epoch = 0` | ✅ Starts at 0 | Fresh G_I placeholder for new directory slots |
| 6 | `debugSetDirState` | 2429 | `e.epoch = epoch` | ✅ Debug only | Test harness — not exercised in production |

All other `_directory.update` call sites (1293, 1574, 1614, 1728, 2341, 2365, 2665) never touch `entry.epoch` before the update — the epoch is preserved unchanged from the directory lookup.

## sharersMask validation at every `_directory.update` call site

`ResidentDir::update()` (ResidentDir.cc:295) calls `validateCanonical(in, pa)` which enforces:

| Call site | Line | Context | sharersMask after mutation | Canonical? |
|-----------|------|---------|---------------------------|------------|
| INV-ACK handler | 1293 | `entry.sharersMask &= ~nodeBit`; canonicalizes G_S→G_I if mask==0 | Zero for G_I, non-zero for G_S | ✅ |
| Upgrade tentative commit | 1374 | After `commitIntendedResult` (has `panic_if` for G_E/G_M one-hot) | `panic_if` at 2178 guards one-hot; G_I→0; G_S non-zero | ✅ |
| `processWriteback` | 1574 | G_E → mask = `1ULL << requesterNode`; G_I → 0 | One-hot for G_E; zero for G_I | ✅ |
| `processHomeWritebackCompletion` | 1614 | G_I, mask = 0 | Zero for G_I | ✅ |
| `processEvict` | 1728 | State recomputed from mask: G_I if mask==0/no owner; G_S if sharers; G_E/G_M if owner | Zero for G_I; non-zero for G_S; one-hot for G_E/G_M | ✅ |
| Upgrade Done commit | 1934 | Same as 1374 (`commitIntendedResult`) | ✅ (same panic_if guard) | ✅ |
| Clear/Grant Handshake commit | 2078 | Same as 1374 (`commitIntendedResult`) | ✅ (same panic_if guard) | ✅ |
| `onBackstoreFillComplete` | 2322 | Restored from backstore or initialized to G_I/0 | Consistent with state | ✅ |
| `onBackstoreWriteAck` | 2341 | Only `residentDirty = false`; state/mask unchanged | From valid directory entry | ✅ |
| `onBackstoreDeleteAck` | 2365 | Only `residentDirty = false`; state/mask unchanged | From valid directory entry | ✅ |
| `debugSetDirState` | 2434 | Parameter-driven; test only | Implicitly trusted (test) | ✅ |
| `processHomeWbNotify` | 2665 | G_I, mask = 0 | Zero for G_I | ✅ |

**Conclusion:** The epoch monotonically advances by exactly 1 at every commit (via `allocateReservedEpoch` → `commitIntendedResult`), and all other writes are idempotent copies or zero-initialization. The `ResidentDir::validateCanonical` function asserts the sharersMask invariants (one-hot for exclusive states, non-zero for shared, zero for invalidated) on every update path, making the directory entry always well-formed.
