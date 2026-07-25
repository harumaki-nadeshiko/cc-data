# ResidentDir to DRAM Backstore Rearchitecture Plan

**Status:** Proposed implementation baseline. No code in this document is
implemented merely because it is described here.

**Scope:** This document replaces the current spill-path implementation plan
for future work. It does not change the already-verified NaiveEvict behavior.
The legacy document `ubcc_directory_offload_design.md` remains historical
context: its stated final design and the current Schema A/C implementation do
not agree, so it must not be used as an implementation contract without this
document.

## 1. Goals and Non-Negotiable Rules

The rearchitecture covers this path:

```text
ResidentDir -> UBCCController -> BackstoreHost -> MetaRNFClient
-> UBAdapter -> MetaRNFController -> HN-F/L3 -> metadata DRAM
```

It has five goals:

1. Preserve directory correctness across ResidentDir eviction and refill.
2. Make every spill write and delete have one explicit, verifiable completion
   point before UBCC can release its resident copy.
3. Remove unbounded UBCC-side metadata mirrors and page caches.
4. Make the common metadata lookup a single 64B MetaRNF operation whenever
   the entry is in its home bucket.
5. Keep all long-lived on-chip state within a strict 512 KiB budget.

The following rules are mandatory.

```text
R1. UBCC is the only ordering point for one line PA.
R2. Clear / OuterUpgradeDone remain the coherence commit points.
R3. A ResidentDir hit never reads Backstore.
R4. Bloom and performance hints are advisory only, never metadata truth.
R5. ResidentDir-external long-lived directory metadata exists only in DRAM
    Backstore. No PA-keyed shadow directory is allowed in UBCC or Host.
R6. A Backstore I/O error, queue-full result, timeout, malformed response, or
    checksum failure must never be converted to Backstore NotFound or G_I.
R7. Spill may remove a ResidentDir entry only after BackstoreCommitted.
R8. NaiveEvict does not issue Backstore or MetaRNF metadata operations.
R9. A spilled G_M record persists state/owner/epoch, not the dirty 64B data.
    On refill, normal coherence must Recall the one-hot owner.
R10. All temporary queues and transaction tables have both per-key and global
     hard limits.
```

## 2. Current-State Findings

The current implementation must not be incrementally treated as a reliable
Backstore. These are factual blockers, not style preferences.

| Current component | Problem | Consequence |
|---|---|---|
| `BackstoreSchemaA` | Page-chain head/tail/directory-slot semantics overlap. | Overflow pages can be allocated but not reliably discoverable. |
| `BackstoreSchemaC` | Is not the active Host schema and has incomplete index integration. | It is not a drop-in fallback. |
| `UbioBackstoreHost::_pages` | Unbounded `map<pagePa, BackstorePage>`. | A long-lived software page mirror becomes a second storage tier. |
| `UBCCController::_backstoreMetadataPAs` | Unbounded exact PA set. | It is a forbidden shadow directory. |
| `_lineDataCache` | Unbounded PA-to-64B map. | Dirty-data correctness is coupled to a long-lived software cache. |
| Cold-page write | Existing write path may fail when the page is absent from `_pages`. | Correctness depends on a cache hit instead of DRAM RMW. |
| Overflow link write | The predecessor may use fire-and-forget `writePage()`. | A new page may be ACKed before the chain is durable/visible. |
| Delete | Existing delete uses fire-and-forget page write. | UBCC may release a resident delete tombstone too early. |
| MetaRNF API | A 256B logical operation is split into four 64B operations. | One page operation consumes four of eight flight slots. |

Relevant current locations are:

```text
modules/ubiomodule/ubio_main.cc
modules/ubiomodule/BackstoreSchemaA.cc
modules/ubiomodule/BackstoreSchemaC.cc
modules/ubiomodule/UBCCController.cc
gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc
gem5/src/mem/ruby/protocol/chi/ep/MetaRNFController.cc
```

## 3. Authority and Resident Semantics

### 3.1 Resident hit

For every ResidentDir hit, ResidentDir is the current committed metadata
authority. `residentDirty` only says whether that resident metadata is newer
than its DRAM Backstore copy.

```text
ResidentDir hit, G_M, residentDirty = false
-> Resident metadata and Backstore metadata agree
-> Do not inspect Bloom
-> Do not read Backstore
-> Recall the one-hot owner through the normal coherence path
```

### 3.2 Resident miss

```text
ResidentDir miss
-> Bloom negative and filter-valid: materialize resident G_I
-> Bloom positive: create a pinned Fill transaction and read Backstore
-> Backstore Found: install state/sharers/epoch, then replay request
-> Backstore NotFound: install G_I, then replay request
```

`NotFound` is legal only after a complete valid lookup terminates at an EMPTY
hash slot. No transport or storage failure may take that branch.

### 3.3 Dirty data

For a spilled line in `G_M`:

```text
Backstore = G_M + one-hot owner + epoch
Owner cache = potentially only current 64B data copy
```

Backstore is not a dirty-data store. A refill restores only directory metadata;
the subsequent protocol operation obtains data with `RecallReq` / `RecallResp`.
Recall payload belongs to its bounded `OutstandingRequest::dataBuf`, then is
written to the home data store when coherence requires it. No unbounded
`_lineDataCache` remains in the final architecture.

## 4. Storage Budgets

### 4.1 On-chip UBCC budget

All long-lived UBCC-side storage, including performance hints, is limited to
512 KiB. Heap allocation does not exempt a structure from this accounting.

#### Phase 0 legacy profile (active, TC132/133/134 baseline)

| Long-lived structure | Budget | Role |
|---|---|---:|---|
| Set-associative ResidentDir, tags, control bits, PLRU | ~427 KiB | 57,344 entries (7-way × 8192-set, 60-bit entry). |
| Grouped Bloom filter | **60 KiB** | Advisory negative filter (legacy). |
| ResidentDir GroupIndex[16] (in-object) | 4 KiB | Group metadata index. |
| Host legacy GroupIndex[16] duplicate (transitional) | 4 KiB | Phase 3 will eliminate; see §4.1.1. |
| BLC | 0 KiB | Not a legacy capability. |
| Group descriptors / scratch | 0 KiB | Not a legacy capability. |
| **Total** | **~495 KiB** | Verified at startup (≤ 512 KiB). |

#### Future H64 target profile (not active; H64 rejected at startup)

| Long-lived structure | Budget |
|---|---|---:|
| ResidentDir | ~457 KiB |
| Bloom | **40 KiB** |
| ResidentDir GroupIndex[16] | 4 KiB |
| BLC (reserved) | 2 KiB |
| Group descriptors / scratch (reserved) | 2 KiB |
| Host GroupIndex duplicate | **0 KiB** (eliminated in Phase 3) |
| **Total** | **~505 KiB** |

#### §4.1.1 Host legacy GroupIndex duplicate (Phase 0 transitional)

`UbioBackstoreHost` currently maintains its own `_groupIdx[16]` (4 KiB)
separate from `ResidentDir::_groupIndex[16]`.  The two serve different roles:
- ResidentDir copy: Bloom filter statistics and group reconstruction triggers.
- Host copy: Schema A page-directory tracking (upsert, lookup, delete plans).

Both are long-lived on-chip state totalling 8 KiB.  Phase 3 will eliminate
the host copy by refactoring Host to use a single per-PA metadata index
(Schema H64 bucket table); Bloom statistics will remain in ResidentDir.

The naive-evict layout (bloom=0) yields 65,536 entries with the current
58-bit physical entry layout (2-way × 32,768-set, 468 KiB dir + 4 KiB
resident GroupIndex + 4 KiB host GroupIndex = 476 KiB ≤ 512 KiB).

Forbidden long-lived Host/UBCC structures include:

```text
PA -> BackstoreEntry maps or sets
PA -> BackstorePage maps
PA -> 64B data maps
unbounded page caches
unbounded exact membership indexes
```

### 4.2 Metadata DRAM capacity

Metadata DRAM capacity is independent of the 512 KiB on-chip budget.

The current configuration allocates 16 MiB per node before socket splitting
(`gem5/configs/ruby/CHI_basic_framework_config.py`). This is a legacy default,
not a Backstore architectural limit. The new Backstore range must be a
configuration parameter with these rules:

```text
Default for redesign validation: 128 MiB per metadata Backstore instance.
Permitted deployment value: 128 MiB or larger.
Minimum supported value: explicitly validated by configuration, never inferred.
Per-socket capacity: total range divided by active sockets, with alignment.
```

For a 128 MiB single-socket range and 64B buckets:

```text
2,097,152 buckets
5 slots/bucket
10,485,760 physical slots before reserved control/scratch space
```

The implementation must reserve and report:

```text
superblock/format area
per-group table regions
rebuild scratch region
usable bucket count
configured load high-water mark
```

The initial load high-water mark is 60-65 percent live occupancy. Capacity
exhaustion is an explicit backpressure/error condition, never an overwrite or
fabricated miss.

## 5. Schema H64: Fixed 64B Bucket Hash Table

### 5.1 Why this schema

Schema H64 replaces page chains with fixed-address open addressing:

```text
line PA -> hash -> group -> home 64B bucket -> bounded probe sequence
```

It removes:

```text
page allocator
head/tail pointers
next_page_ptr
overflow publication transactions
page-directory pointer authority
256B page cache correctness dependency
```

This matches the native MetaRNF cache-line granularity. One metadata bucket is
one 64B CHI line and normally consumes one MetaRNF flight slot.

### 5.2 Bucket layout

Each 64B bucket contains a 4B header and five 12B slots:

```text
64B BucketLine
  header: 4B
  slot[0..4]: 5 * 12B
```

A slot contains:

| Field | Bits | Meaning |
|---|---:|---|
| line PA | 44 | Full cache-line address key. |
| MESI state | 2 | `G_I/G_S/G_E/G_M`. |
| sharers mask | 16 | One-hot owner for `G_E/G_M`; sharers for `G_S`. |
| epoch | 24 | Committed directory version. |
| slot state | 2 | `EMPTY`, `LIVE`, `HASH_TOMBSTONE`, `RESERVED/CORRUPT`. |
| integrity/reserved | 8 | Format/integrity extension space. |

The `HASH_TOMBSTONE` state is a hash-table implementation marker. It is not
the ResidentDir protocol state `G_I + residentDirty=1`, which must be named
`ResidentDeletePending` in new code and documentation.

The header stores format version, live count, tombstone count, and a bounded
generation/check field. Exact bit allocation is an implementation task, but
the structure must remain exactly 64B with static assertions.

### 5.3 Lookup

```text
Read home bucket
-> matching LIVE full PA: Found(entry)
-> any valid EMPTY slot: NotFound
-> otherwise probe next bucket
-> repeat until a valid terminating EMPTY or bounded valid table boundary
```

`HASH_TOMBSTONE` does not terminate a probe. A checksum/format error, failed
read, unavailable transaction slot, or rebuild state returns `RetryableBusy`,
`IoError`, or `Corrupt`, never `NotFound`.

### 5.4 Upsert

```text
Probe for matching LIVE slot, first reusable HASH_TOMBSTONE, or EMPTY slot
-> read target 64B bucket
-> update one local 64B transaction buffer
-> write that 64B bucket
-> wait for ordered write visibility response
-> BackstoreCommitted
```

An update overwrites the matching PA's slot. An insert selects the first
reusable tombstone or a terminal empty slot. No operation assumes a bucket is
cached locally; all cold buckets use read-modify-write through MetaRNF.

### 5.5 Delete

```text
Probe for PA
-> if storedEpoch > deleteEpoch: reject stale delete
-> otherwise change LIVE to HASH_TOMBSTONE
-> write 64B bucket and wait for completion
-> return Deleted or AlreadyAbsent
```

`AlreadyAbsent` is an idempotent success only after a complete valid lookup.
The first implementation deliberately uses physical tombstones and group
rebuild instead of foreground Robin Hood backward shifts. This avoids a
multi-bucket atomic update in the correctness-critical path.

### 5.6 Group rebuild

Groups are fixed address ranges. A rebuild triggers on a configurable
combination of:

```text
tombstone ratio > 25 percent
probe high-water threshold exceeded
live occupancy > 60-65 percent
```

The initial, correctness-first rebuild is:

```text
block one group
-> scan its DRAM buckets
-> reinsert only LIVE slots into its reserved DRAM scratch area
-> build a replacement Bloom slice
-> copy/swap rebuilt buckets into the group region
-> increment group generation
-> publish filter-valid generation
-> unblock group
```

Requests for that group are placed in a bounded group queue or receive
`RetryableBusy`. Later work may make this incremental, but must preserve the
same published-generation contract.

### 5.7 BLC hint

The optional 2 KiB BLC has 512 4B entries:

```text
PA fingerprint (16b), predicted probe distance (8b), group generation (8b)
```

It may let lookup read a predicted displaced bucket first. It must validate
the full 44-bit PA in the DRAM slot. A false BLC hit adds a read; it can never
change Found/NotFound semantics. Rebuild generation invalidates stale hints.

The first functional implementation may omit BLC behavior while reserving the
budget; correctness never depends on it.

## 6. Bloom Filter Contract

Bloom remains a grouped advisory negative filter.

```text
Backstore upsert committed -> insert PA into Bloom
Backstore delete committed -> retain stale bits and increment group stale count
Group rebuild -> scan real DRAM LIVE records and replace that Bloom slice
```

The current implementation rebuilds from ResidentDir only. That is invalid:
spilled entries absent from ResidentDir would become Bloom false negatives.

A Bloom-negative result may skip Backstore only when the corresponding group:

```text
is not rebuilding
has a valid published Bloom generation
has no recorded filter corruption
```

Otherwise the request must perform a real Backstore lookup. No exact PA shadow
set is permitted to compensate for Bloom maintenance errors.

## 7. Transaction, Ordering, and ACK Contract

### 7.1 ACK levels

The word "durable" is reserved for a separately proven persistence guarantee.
Current CHI completion code establishes a hierarchy completion point, not
necessarily power-loss persistence in DRAM. New code must use these terms:

| Level | Meaning | Releases ResidentDir victim? |
|---|---|---|
| `TransportAccepted` | UBIO successfully sent the request. | No. |
| `HierarchyVisible` | The 64B write completed through the defined MetaRNF/CHI path and a later ordered metadata read observes it. | Not by itself. |
| `BackstoreCommitted` | The requested key's write/delete is hierarchy-visible and reachable by Schema H64 lookup. | Yes. |
| `PowerLossDurable` | Optional future guarantee after actual HN-F/DRAM writeback proof. | Not required for current coherence semantics. |

### 7.2 Required completion object

Backstore completion must be typed, not encoded as a generic flag bit:

```cpp
enum class BackstoreStatus {
    Ok,
    AlreadyAbsent,
    RetryableBusy,
    IoError,
    Corrupt,
    StaleEpoch,
    CapacityExhausted,
};

struct BackstoreCompletion {
    uint64_t linePa;
    uint64_t snapshotEpoch;
    BackstoreOp op;
    BackstoreStatus status;
};
```

An eviction write captures the resident snapshot before issuing I/O. UBCC may
clear `residentDirty` only if the ACK epoch equals the current resident epoch.
It may `forceRemove` an eviction victim only after `status == Ok` and
`BackstoreCommitted`.

### 7.3 Per-bucket serialization

The Host transaction engine owns a finite table of active bucket operations.
For a bucket with an active RMW:

```text
same bucket -> bounded FIFO queue
different bucket -> independent transaction if global capacity exists
```

The MetaRNF controller continues to serialize a physical 64B address through
its scoreboard, but Host-level serialization is required to avoid stale RMW
lost updates before requests reach that scoreboard.

### 7.4 Required limits

Initial limits, all configurable and reported at startup:

| Resource | Initial hard limit |
|---|---:|
| MetaRNF active line flights | 8 |
| MetaRNF total pending line operations | 128 |
| Active Backstore transactions | 32 |
| Total Host queued Backstore operations | 128 |
| Per-bucket queued operations | 8 |
| UBCC live outstanding requests | 64 |
| UBCC total resident waiters | 256 |
| UBCC per-PA waiters | 32 initially; must count toward total |
| Grant handshake tombstones | 256 total |

Before an operation enters an irreversible write stage, it reserves the
response context and completion resources it needs. Queue-full before that
stage returns `RetryableBusy`; queue-full after that stage must not discard the
operation.

## 8. MetaRNF Interface Changes

Schema H64 uses native 64B metadata line I/O:

```text
MetaRNFLineReadReq / MetaRNFLineReadResp
MetaRNFLineWriteReq / MetaRNFLineWriteResp
```

Equivalent extension of the current message payload is acceptable only if it
has an explicit `validBytes = 64` and typed status. The API must preserve:

```text
unique reqId
64B address
64B payload for writes/read responses
ordered same-address behavior
success/failure status
```

`UBAdapter` must stop expanding each ordinary Backstore operation into four
64B operations. The existing 256B page path may remain solely as an isolated
legacy experimental path; it must not be used by the new spill path.

Read slot exhaustion must queue or return `RetryableBusy` before UBCC treats
the operation as issued. It must not silently call a read callback with an
empty/zero line. Existing unbounded per-address wait queues must receive global
and per-address limits.

## 9. Required UBCC Integration

Replace the synchronous page-plan interface:

```text
candidatePagesForLookup
planUpsert
applyUpsert
updateIndexAfterWrite
```

with an asynchronous Backstore service interface:

```cpp
lookup(linePa, completion)
upsert(linePa, entry, snapshotEpoch, completion)
erase(linePa, deleteEpoch, completion)
```

The service owns probing, RMW buffers, retries, bucket serialization, and
BackstoreCommitted. UBCC only owns protocol state, resident pinning, waiter
replay, and epoch validation.

The integration removes from the production spill path:

```text
BackstoreSchemaA
BackstoreSchemaC
_pages
_pagesDirty
_chainCtx / _chainPages / _chainGroup
_deferredReadsByPage
_backstoreMetadataPAs
_lineDataCache
MetaRNFClient::writePage() fire-and-forget API
MetaRNFClient::writePageD1()
```

Schema A/C may be retained under an explicitly experimental build option for
ablation only. They may not share production callbacks or Host state with
Schema H64.

## 10. Logging Governance

Log markers have three distinct contracts. Future changes must classify every
new marker before adding it.

| Class | Purpose | Naming rule | Compatibility rule |
|---|---|---|---|
| Correctness verification | E2E/test assertions, invariant evidence, PASS/FAIL evidence | Preserve the existing marker exactly. New permanent markers use a stable non-debug domain prefix such as `[UBCC-...]`, `[BACKSTORE-...]`, or `[METARNF-...]`. | Never rename, remove, or change fields/order of markers consumed by verifiers. |
| Latency measurement | Timed event boundaries, benchmark parsers, performance counters | Preserve the existing metric marker exactly. New markers use the established metric namespace and include deterministic tick/reqId/PA fields as appropriate. | Never add unconditional verbose output on the measured hot path if it perturbs timing/log parsing. |
| Debugging | Human investigation, temporary phase tracing, extra state dumps | Every new debugging-only marker begins exactly with `[DEBUG-`. Examples: `[DEBUG-BACKSTORE-PROBE]`, `[DEBUG-METARNF-QUEUE]`. | Must be runtime-gated and disabled by default; cannot be a test oracle or latency metric input. |

### 10.1 Migration policy

The log cleanup is a dedicated implementation phase after functional
correctness and before final long regressions.

1. Inventory all existing markers and classify each as verification, latency,
   or debug.
2. Freeze all existing verification and latency marker spelling, payload
   fields, and ordering unless their consuming tests are intentionally changed
   in the same reviewed patch.
3. Rename only debugging markers to `[DEBUG-...]`, preserving sufficient
   payload for manual trace migration.
4. Add runtime debug gating, for example `UBCC_DEBUG_LOG=1`; the default is
   off.
5. Update manual debugging documentation and no verifier to depend on a debug
   marker.
6. Add a CI/static check that rejects a new debugging-only marker lacking the
   `[DEBUG-` prefix.

Examples of markers that must be treated carefully because existing tests or
analysis may consume them include:

```text
[RESIDENT-SPILL-START]
[BACKSTORE-WRITE-DURABLE]
[RESIDENT-FILL-ISSUED]
[UBCC-NAIVE-DIRTY-RECALL-PAYLOAD]
[METARNF-WRITE-QUEUE]
```

Their classification must be established by searching `tests/`, scripts, and
measurement tooling before renaming. Until then, preserve them unchanged.

## 11. Implementation Phases

No phase begins its long E2E test until the listed acceptance criteria pass.

### Phase 0: Freeze and remove ambiguity

* Freeze Schema H64 as the production target and mark Schema A/C experimental.
* Add the Backstore capacity configuration, with 128 MiB default validation
  range and correct per-socket range reporting.
* Add startup accounting for every long-lived on-chip byte.
* Do not change TC132/133/134 behavior in this phase.

**Phase 0 implementation status (2026-07-23):**

- [x] Metadata DRAM capacity: 128 MiB default (configurable via
  `--ubcc_metadata_size` in gem5, `--metadata-dram-bytes=` in ubio).
  Per-socket split verified.  Legacy 16 MiB available via explicit override.
- [x] On-chip budget accounting: ResidentDir layout, Bloom, GroupIndex[16]
  (resident + host legacy duplicate), BLC, descriptors all tracked.
  Startup assertion enforces total ≤ 512 KiB for production configs.
  Tiny test configs (sram < 64 KiB) exempt from assertion.
- [x] Legacy profile: Bloom=60 KiB, BLC=0, desc_scratch=0.  Preserves
  TC132/133/134 ResidentDir capacity (57,344 entries, 7-way × 8192-set).
- [x] Future H64 profile defined (Bloom=40 KiB, BLC=2 KiB, desc=2 KiB)
  but NOT active.  H64 startup rejected with fatal error.
- [x] Schema selection: `--backstore-schema=legacy_schema_a` (active),
  `disabled`.  `h64` and `experimental_schema_c` reject with clear errors.
  Auto mode preserves legacy behavior.
- [x] Host legacy GroupIndex duplicate (4 KiB) explicitly counted,
  documented as Phase 3 removal target.
- [x] Startup manifest: `[UBIO-MANIFEST]` and `[EPBACKEND-MANIFEST]`
  report schema mode, metadata DRAM ranges, on-chip budget breakdown.
- [x] No changes to BackstoreSchemaA/C, spill/fill protocol state machines,
  or existing correctness/latency log markers.
- [x] Schema H64 implementation (Phase 1 — reference validation).  See below.
- [ ] BLC functionality (Phase 3+)
- [ ] Host GroupIndex duplicate removal (Phase 3)

### Phase 1: Standalone Schema H64 reference validation

Implement the 64B bucket codec, hash/probe rules, insert/update/delete, and
group rebuild behind a standalone in-memory asynchronous line-device test.

Required tests:

```text
codec round-trip
randomized reference-map comparison
forced collisions and long probes
delete/reinsert correctness
stale epoch update/delete rejection
failure and queue-full injection
group rebuild set equivalence
Bloom rebuild: no false negatives for all LIVE DRAM entries
```

Run at least one million randomized operations across multiple hash seeds and
explicitly record probe histograms.

**Phase 1 implementation status (2026-07-23):**

All items below verified by standalone unit test with no gem5, UBCC, MetaRNF,
or E2E dependencies.

- [x] New files: `modules/ubiomodule/BackstoreSchemaH64.{hh,cc}`,
  `tests/phase1/test_schema_h64.cc`.  No existing source files modified.
- [x] BucketLine layout: exactly 64 B = 4 B header + 5 × 12 B slots.
  `static_assert` enforced.
- [x] Slot bit layout (96 bits = 3 × uint32_t LE):
  `PA[43:0]` (44b), `MESI` (2b), `SlotState` (2b), `Sharers` (16b),
  `Epoch` (24b), `Integrity` (8b).
- [x] Header bit layout (32 bits = 1 × uint32_t):
  `format_version` (8b), `generation` (8b), `live_count` (4b),
  `tombstone_count` (4b), `reserved` (8b).
- [x] Slot states: `EMPTY(0)`, `LIVE(1)`, `HASH_TOMBSTONE(2)`,
  `RESERVED(3)`.
- [x] Status codes: `Found`, `NotFound`, `AlreadyAbsent`, `StaleEpoch`,
  `CapacityExhausted`, `Corrupt`, `RetryableBusy`.
- [x] Core API: `lookup`, `upsert`, `erase`, `rebuildGroup`.
- [x] Hash: splitmix64 → group; splitmix64(different seed) → home bucket.
  Linear probe within group; `EMPTY` terminates, `HASH_TOMBSTONE` does not.
- [x] Upsert: epoch guard (smaller epoch rejected, same epoch idempotent).
- [x] Erase: `LIVE` → `HASH_TOMBSTONE`; smaller delete-epoch → `StaleEpoch`;
  absent → `AlreadyAbsent`.  `HASH_TOMBSTONE` never becomes `NotFound`.
- [x] Insert: prefers first reusable tombstone, otherwise first `EMPTY`.
- [x] Group rebuild: collect `LIVE` entries, clear buckets, increment
  generation, re-insert.  Set equivalence preserved; no entry lost.
- [x] No unbounded map as storage; fixed `std::vector<H64BucketLine>`
  pre-allocated from `H64Config`.
- [x] Configurable: `num_groups` (default 256), `buckets_per_group`
  (default 1024).  Tests use tiny configs (e.g., 1 group × 2 buckets)
  to force collisions and capacity exhaustion.
- [x] All debug-only log markers use `[DEBUG-H64-...]` prefix and are
  disabled by default.  (Phase 1 has no debug logs; placeholder policy
  established.)
- [x] No E2E-consumed markers, no timeout/functional-cache fallbacks,
  no shadow directory.
- [x] No Schema A/C, `_pages`, `_lineDataCache`, `_backstoreMetadataPAs`,
  UBAdapter, MetaRNF, or gem5 dependency in any new file.

**Test commands and results:**

```bash
# Build (from project root, inside ubcc-dev:ubuntu20.04):
g++ -std=c++17 -O2 -Wall \
    -I modules/ubiomodule -I modules/ubiomodule/mem/ruby -I . \
    tests/phase1/test_schema_h64.cc modules/ubiomodule/BackstoreSchemaH64.cc \
    -o build/test_schema_h64

# Run:
build/test_schema_h64 --verbose
```

**Test results (2026-07-23):**

```text
=== Schema H64 Phase 1 Reference Validation ===
C++17, no external test framework
BucketLine: 64 bytes (4 header + 5 slots × 12 bytes)

  TEST codec round-trip                             ... OK
  TEST bucket layout static assertions              ... OK
  TEST basic CRUD                                  ... OK
  TEST same-PA update / idempotent                 ... OK
  TEST stale update / delete rejection             ... OK
  TEST tombstone cluster lookup                    ... OK
  TEST delete / reinsert (tombstone reuse)         ... OK
  TEST collision + capacity exhaustion             ... OK
  TEST per-group rebuild                           ... OK
  TEST corrupt / input validation                  ... OK
  TEST randomized ops seed=0x2A                    ... OK  (250,000 ops)
  TEST randomized ops seed=0x3039                  ... OK  (250,000 ops)
  TEST randomized ops seed=0xDEADBEEF              ... OK  (250,000 ops)
  TEST randomized ops seed=0xCAFEBABE              ... OK  (250,000 ops)
  TEST probe measurement                           ... OK  (1,500 / 2,560 slots)
  TEST hash seed sensitivity                       ... OK
  TEST failure status propagation                  ... OK
  TEST epoch edge cases                            ... OK

=== Results ===
Passed: 18
Failed: 0
*** ALL TESTS PASSED ***
```

Randomized test configuration: 64 groups × 256 buckets/group = 81,920 slots.
Total 1,000,000 operations (4 seeds × 250,000).  Load factor ~81.7%
(~67,000 live entries at steady state).  Every operation cross-checked
against `std::unordered_map` reference model with strict epoch semantics.
`diff --check` clean.

Probe statistics (16 groups × 32 buckets/group, 1,500 entries, 58.6% load):
maxProbe=4 buckets, avgProbe=1.05 buckets.

**Not yet done (Phase 2/3):**
- Bloom rebuild validation (requires Bloom integration — Phase 3).
- Probe histograms (instrumentation reserved for Phase 3 integration test).
- MetaRNF line transport, bounded queues, BackstoreHost integration.
- Production spill-path replacement.

### Phase 2: 64B MetaRNF line transport

Implement typed 64B request/response messages and bounded queues. Verify:

```text
8 concurrent flights
queue behavior for the ninth request
same-address FIFO ordering
write then ordered read observes identical data
failure status round trip
no callback turns failure into zero-filled success
```

### Phase 3: Backstore Host integration

Replace the production page-chain Host path in one focused patch. Add bounded
Fill, Upsert, and Delete transactions; snapshot-epoch ACK validation; resident
pin/unpin; and Bloom publication. Delete `_pages` and the exact PA shadow set
as part of this phase, not later.

Required focused tests:

```text
Resident miss -> Found -> G_M owner Recall
Resident miss -> verified NotFound -> G_I
spill victim held until BackstoreCommitted
delete held until BackstoreCommitted
async writeback changed epoch retains residentDirty
MetaRNF failure retains pinned state and exposes explicit error
```

### Phase 4: Logging governance migration

This phase is intentionally separate from protocol changes.

* Produce the marker inventory and ownership table.
* Preserve correctness and latency marker compatibility.
* Rename/gate debugging-only markers with `[DEBUG-...]`.
* Add automated checks for the prefix and debug gating.
* Re-run marker-dependent verifiers and latency parsers unchanged.

### Phase 5: Full regression and performance evaluation

Run in this order:

```text
TC200: Naive isolation
TC201: spilled G_M -> fill -> Recall
TC202: recalled payload grant path
new TC203: forced hash collisions, probe, delete, rebuild
TC132 spill
TC133 spill
TC134 spill
TC131 timing variants
```

TC203 replaces page-overflow-chain criteria with hash-table criteria:

```text
all first lookups Found when expected
valid NotFound only at valid EMPTY termination
delete does not truncate a probe cluster
rebuild preserves all LIVE records
no unbounded Host metadata structures
```

## 12. Verification Invariants

The final implementation must assert or test the following.

```text
I1. Resident hit has zero Backstore I/O.
I2. Bloom-negative shortcut is used only for a valid, non-rebuilding group.
I3. Bloom and BLC never decide metadata truth.
I4. No I/O failure becomes NotFound or G_I.
I5. Spill eviction cannot release before BackstoreCommitted.
I6. Delete cannot release ResidentDeletePending before BackstoreCommitted.
I7. G_E/G_M records have one-hot sharers/owner masks.
I8. G_M refill follows normal Recall and never reads stale home data as a
    shortcut.
I9. Async writeback only clears residentDirty for the captured matching epoch.
I10. No production long-lived PA map, page cache, or data cache exists outside
     ResidentDir/Bloom/BLC/DRAM Backstore.
I11. All queues remain within local and global hard bounds.
I12. Naive emits no Backstore/MetaRNF metadata traffic.
I13. Long-lived on-chip bytes are at most 512 KiB.
I14. Every debug-only log starts `[DEBUG-` and is disabled by default.
```

## 13. Explicit Non-Goals

This plan does not claim:

```text
power-loss persistence beyond the implemented CHI hierarchy completion point
transparent migration of existing Schema A/C DRAM contents
unbounded Backstore operation queues
functional cache reads for metadata or dirty line recovery
timeout-based G_I fallback
an immediate high-performance incremental rebuild implementation
```

The first objective is a correct, bounded, directly verifiable spill path.
Only after that path passes TC132-134 should BLC tuning, write combining, and
incremental rebuild be considered.
