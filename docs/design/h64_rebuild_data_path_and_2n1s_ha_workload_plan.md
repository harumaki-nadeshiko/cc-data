# H64 Bloom Rebuild, Data-Path Cleanup, and 2N1S HA Comparison Plan

**Status:** Implementation plan. Nothing in this document is considered
implemented merely because it is specified here.

**Scope:** This plan adds three coordinated workstreams:

1. Correct H64-aware Bloom reconstruction over both ResidentDir and DRAM
   Backstore, followed by restoration of the Bloom-negative shortcut.
2. Complete removal of `UBCCController::_lineDataCache`, with data correctness
   moved to transaction payloads and authoritative home memory.
3. A portable two-node, one-socket workload suite that can run on the
   customer's HA machine and on CC-EP in naive and optimized modes.

The protocol work and the HA workload work may be developed in parallel, but
performance claims that depend on Bloom or spill behavior are not accepted
until the protocol work has passed its correctness gates.

## 1. Non-Negotiable Rules

```text
P1. Bloom negative is authoritative only for a valid, non-rebuilding slice.
P2. Bloom rebuild scans both ResidentDir and all mapped H64 Backstore groups.
P3. Rebuild, upsert, and delete concurrency must never create a false negative.
P4. A rebuild error leaves the slice invalid; it never publishes partial state.
P5. No long-lived PA-keyed shadow directory, delta map, or software data cache.
P6. _lineDataCache is removed, not retained as a legacy production fallback.
P7. Dirty data is authoritative only in a live transaction, a coherent owner,
    or the direct-indexed home data store after its persistence completion.
P8. Queue-full and I/O failure return explicit Busy/Error; no zero fill,
    functional-cache bypass, timeout-to-G_I fallback, or fire-and-forget ACK.
P9. All persistent on-chip state remains within 512 KiB.
P10. HA, naive, and optimized runs use the same workload core, topology,
     dataset, operation mix, warm-up, sample boundary, and validation rules.
```

## 2. Delivery Tracks

| Track | Goal | Immediate output |
|---|---|---|
| A | Remove `_lineDataCache` safely. | One authoritative home-data path with bounded pending operations. |
| B | Rebuild Bloom from ResidentDir plus H64. | Per-slice validity and correct negative shortcut. |
| C | Build portable 2N1S workloads. | HA/native, CC naive, and CC optimized comparable result bundles. |
| D | Replace mixed latency claims. | Scenario-specific latency, traffic, and throughput reports. |

Recommended implementation order for protocol changes:

```text
bounded home-data API
-> migrate all grant/writeback/recall users
-> delete _lineDataCache
-> add Bloom slice state and H64 mapping
-> add joint background rebuild
-> restore Bloom-negative shortcut
-> run capacity/performance acceptance
```

## 3. Track A: Remove `_lineDataCache`

### 3.1 Current Problem

`_lineDataCache` is a PA-keyed `std::map` with an 8,192-entry limit. Its data
payload alone is 512 KiB, while tree nodes, keys, pointers, allocator metadata,
and alignment consume additional memory. It therefore cannot represent a
strict 512 KiB hardware structure. It is volatile, not persistent, and couples
dirty-data correctness to a software cache.

The removal target includes:

```text
_lineDataCache
kMaxLineDataCacheLines
updateLineDataCache()
copyLineDataCache()
all direct find()/erase() users
all comments and tests that require this cache
```

Static completion criterion:

```bash
rg "_lineDataCache|updateLineDataCache|copyLineDataCache" modules gem5 docs
```

Production code must have zero matches. Historical documents may retain a
clearly marked historical reference.

### 3.2 Authoritative Data Sources

After removal, grant data is selected in this order:

```text
1. Current OutstandingRequest::dataBuf.
2. Bounded immediate-grant transaction data.
3. Direct-indexed authoritative home DsmDataStore.
4. Recall from the current coherent owner.
5. Bounded asynchronous home-memory read.
```

No new long-lived PA-keyed cache is allowed.

`DsmDataStore` is the modeled home memory, not on-chip state. Its 128 MiB data
array is direct-indexed by DSM offset. It must provide bounded asynchronous
operations and typed completion status.

Proposed interface:

```cpp
enum class DsmDataStatus {
    Ok,
    NotWritten,
    RetryableBusy,
    IoError,
};

void readDsmData(
    uint64_t pa,
    std::function<void(DsmDataStatus, const uint8_t*)> completion);

void writeDsmDataAsync(
    uint64_t pa,
    const uint8_t* data,
    std::function<void(DsmDataStatus)> completion);
```

The current unbounded `std::vector<PendingDataOp>` must become a fixed slot
table. A full table returns `RetryableBusy` and keeps the coherence request
pending through existing bounded retry/replay mechanisms.

### 3.3 Path Migration

| Existing path | Required replacement |
|---|---|
| Dirty recall | Keep data in the recall outstanding, issue async home write, and release dependent waiters only after data is visible. |
| Writeback | Persist to home memory asynchronously; owner release and future grants observe a persistence gate. |
| `commitIntendedResult` with data | Persist transaction data; the original requester may consume its transaction payload, while other requesters wait for authoritative visibility. |
| UBIO ReadResp | Use outstanding/immediate data or an async home read. Never return zeros because a software cache missed. |
| Batch-RS replay | Use bounded transaction data or async home read; do not keep a permanent PA cache. |
| Push grant | Source from current transaction or authoritative home data only. |

Grant building should return a typed result:

```cpp
enum class GrantBuildResult {
    Ready,
    DataReadPending,
    RetryableBusy,
    Error,
};
```

### 3.4 Track A Acceptance

- Dirty recall data cannot be observed before its home write completion.
- Queue exhaustion returns Busy and eventually makes progress.
- No data path fabricates zero data for a previously written line.
- No functional Ruby cache read is used as protocol truth.
- TC102, TC125, TC127, TC129, TC200, TC201, TC202, TC203, TC132, and TC134
  pass with all managed child statuses equal to zero.
- TC202 is renamed/reframed as an authoritative-home-data push-grant test; it
  must no longer mention `_lineDataCache` as a requirement.

## 4. Track B: ResidentDir and H64 Joint Bloom Rebuild

### 4.1 Group Mapping

ResidentDir currently has 16 Bloom slices and H64 has 256 groups. The mapping
must be explicit and shared by both components:

```text
h64_group = H64 hash(PA) mod 256
bloom_slice = h64_group mod 16
```

One Bloom slice therefore covers 16 H64 groups. A shared helper in
`BackstoreSchemaH64.hh` must be the only implementation of this mapping.

### 4.2 Slice State

ResidentDir owns 16 fixed controls:

```cpp
enum class BloomSliceState : uint8_t {
    Invalid,
    Rebuilding,
    Valid,
};

struct BloomSliceControl {
    BloomSliceState state;
    uint32_t rebuildEpoch;
    uint16_t pendingGroups;
    bool retryRequired;
};
```

Bloom negative can skip H64 only when:

```text
slice.state == Valid
and retryRequired == false
and bloomMayContain(PA) == false
```

Invalid or rebuilding slices always query H64.

### 4.3 Bounded Rebuild State Machine

Only one slice rebuilds at a time:

```text
SelectSlice
-> MarkRebuilding
-> Clear one-slice scratch
-> ScanResidentDir
-> Scan 16 H64 group controls and active buckets
-> Re-scan ResidentDir
-> Publish slice bytes
-> MarkValid
```

The implementation processes a fixed number of resident entries or metadata
buckets per event-loop turn. It must not synchronously scan the entire
directory or 128 MiB metadata range.

The scratch allocation is exactly one Bloom slice. It is budgeted as on-chip
state. If a 60 KiB Bloom is retained, the scratch reservation must be at least
3.75 KiB, rounded to 4 KiB. Alternatively, a 32 KiB Bloom uses the existing
2 KiB per-slice scratch target. The final ResidentDir capacity must be
recomputed after this reservation.

### 4.4 Scan Rules

Insert into rebuild scratch:

- ResidentDir non-`G_I` entries.
- ResidentDir entries whose metadata remains dirty or pending persistence.
- Every valid H64 `LIVE` slot in the 16 mapped H64 groups.

Do not insert:

- H64 `EMPTY`, `HASH_TOMBSTONE`, or `RESERVED` slots.
- Reclaimable resident `G_I` entries with no pending persistence.

Every control record, bucket header, slot state, and integrity byte is
validated before publication. Corruption or I/O failure leaves the slice
`Invalid`.

### 4.5 Concurrent Mutation

All live metadata publication goes through one controller method:

```cpp
publishBloomLive(pa)
```

On successful durable upsert or new resident live metadata:

```text
insert into active Bloom
if the slice is Rebuilding, also insert into rebuild scratch
```

Delete never clears Bloom bits. It records a stale positive and lets the next
rebuild remove it. This avoids collision-induced false negatives.

Each H64 group has fixed runtime mutation state:

```cpp
uint32_t mutationSeq[256];
uint16_t activeWriters[256];
```

A group scan reads `seqBefore`, scans the group, and then reads `seqAfter`. If
they differ, that group is rescanned. Retry is bounded. Exhaustion leaves the
slice invalid and schedules a later rebuild; it never publishes partial data.

This is fixed group state, not a PA-keyed delta map.

### 4.6 Startup and Periodic Rebuild

At startup all slices are `Invalid`. Protocol service starts immediately, and
invalid-slice misses query H64. Background reconstruction publishes slices one
at a time. A 16-bit request mask can prioritize slices that receive misses.

Periodic triggers include:

- stale-delete ratio;
- insert period;
- estimated false-positive rate;
- explicit test/diagnostic request.

Only the rebuilding slice loses the negative shortcut.

### 4.7 Track B Acceptance

- Resident-only, H64-only, and duplicate entries all appear in the rebuilt
  Bloom slice.
- Concurrent upsert cannot be lost.
- Concurrent delete may create a stale positive but never a false negative.
- MetaRNF Busy/Error, corrupt control, corrupt bucket, or corrupt slot prevents
  publication.
- Startup queries remain correct before any slice becomes valid.
- After publication, unseen PA negative lookups issue zero H64 requests.
- A spilled PA is never treated as authoritative negative.
- Existing TC200-TC203 and TC131-TC134 correctness regressions pass.

## 5. Track C: Portable 2N1S HA Comparison Suite

### 5.1 Comparison Matrix

Every scenario is run in three modes:

| Mode | Description |
|---|---|
| `ha-native` | Customer two-node, one-socket HA machine. |
| `cc-naive` | CC-EP 2N1S with naive overflow and latency optimizations disabled. |
| `cc-opt` | CC-EP 2N1S with spill, valid Bloom shortcut, and declared latency optimizations enabled. |

An optional fourth mode, `cc-spill-no-opt`, separates spill architecture cost
from protocol optimization benefit. It is recommended for internal diagnosis
but is not required in the customer-facing three-column summary.

### 5.2 Portable Workload Structure

Workloads are split into a platform-independent core and a small adapter:

```text
workloads/ha_compare/common/workload_core.c
workloads/ha_compare/include/ha_port.h
workloads/ha_compare/platform/cc_ep_port.c
workloads/ha_compare/platform/ha_native_port.c
```

Required adapter contract:

```c
int platform_node_id(void);
int platform_thread_id(void);
void *platform_alloc_home(int node, size_t bytes, size_t alignment);
void platform_barrier(void);
uint64_t platform_time_ns(void);
void platform_flush_range(void *ptr, size_t bytes);   /* scenario-controlled */
void platform_fence(void);
```

The core algorithm, operation order, random seed, data size, validation, warmup,
and sample emission are identical. Only allocation, topology discovery,
barrier, clock, and optional cache-control primitives are platform adapters.

The HA adapter should use the customer's supported NUMA/HA allocation API. If
standard Linux facilities are available, the preferred implementation is
`mbind`/`set_mempolicy` or `libnuma`, CPU affinity, `pthread_barrier`, and
`clock_gettime(CLOCK_MONOTONIC_RAW)`. The final API is confirmed on the target
machine before freezing the binary.

The CC adapter uses the 2N1S DSM mapping but must not expose gem5-only behavior
to the workload core. Gem5 protocol markers are supplemental diagnostics, not
the primary cross-platform latency source.

### 5.3 Timing Boundary

The cross-platform primary metric is demand-visible elapsed time:

```text
immediately before the measured load/store/atomic or operation batch
to
immediately after architectural completion
```

Rules:

- Measure timer-call overhead separately and report it.
- For operations near timer resolution, time a fixed batch and divide by the
  number of operations.
- Use at least 1,000 measured samples after warmup for microbenchmarks when
  runtime permits.
- Report p50, p95, p99, mean, minimum, maximum, sample count, and throughput.
- CC-only Outer/Recall/MetaRNF traces are used for attribution, never as the
  sole HA comparison boundary.

### 5.4 Required 2N1S Scenarios

#### HA01: Local Reuse Baseline

Purpose: establish timer overhead and local cache/memory baseline.

```text
node0 allocates local hot set
warmup repeated reads/writes
measure repeated local reuse
```

Metrics:

- local read/write p50/p99;
- bandwidth;
- timer overhead;
- validation checksum.

This is a normalization baseline, not an optimized-vs-naive claim.

#### HA02: Remote Cold and Remote Hot Read

Purpose: compare basic two-node remote access without capacity eviction.

```text
node0 owns data
node1 reads a cold pass
node1 repeats a hot pass fitting in the selected cache working set
```

Metrics:

- remote cold latency/bandwidth;
- remote hot latency/bandwidth;
- hot/cold ratio;
- CC Outer count for attribution.

#### HA03: Ownership Ping-Pong

Purpose: measure dirty ownership transfer and recall behavior.

```text
one cache line or a small line set alternates writers between node0 and node1
each handoff validates sequence and value
```

Variants:

- one line, serialized latency;
- 64 or 256 lines, pipelined throughput;
- read-mostly followed by writer takeover.

Metrics:

- handoff p50/p99;
- transfers/second;
- retry count;
- CC recall path breakdown.

#### HA04: Shared-Read Then Writer Invalidation

Purpose: expose invalidation cost in the two-node topology.

```text
node0 initializes a line set
node1 reads and retains shared copies
node0 writes the same set
```

Metrics:

- first writer-after-share latency;
- steady writer latency;
- invalidations per operation;
- CC invalidate request/ack latency and retry count.

With two nodes the fanout width is one, so this workload measures invalidation
latency rather than multi-node fanout scaling. This is a mandatory coherence
transition in every mode; it establishes the unavoidable invalidation floor and
must not be presented as a spill-specific optimization.

#### HA05: Capacity Shared-Victim Revisit

Purpose: compare local reuse preserved by spill with revisit after naive
invalidation.

```text
node1 caches a hot set owned by node0
node0 creates conflicting capacity pressure
node1 revisits the hot set
```

The same pressure addresses and resident-capacity ratio are used for
`cc-naive` and `cc-opt`. On HA, the data footprint is scaled to the published
HA directory/cache capacity target or run as a working-set sweep.

Metrics:

- pre-pressure local reuse latency;
- first post-pressure revisit latency;
- steady post-pressure reuse latency;
- post-pressure local-hit percentage;
- CC Outer count: naive revisit should show global transactions when the copy
  was invalidated; optimized preserved reuse should show zero Outer traffic.

Primary derived metric:

```text
invalidated_revisit_penalty = first_revisit - local_reuse
preservation_gain = naive_first_revisit - opt_first_revisit
```

#### HA06: Dirty-Owner Capacity Lifecycle

Purpose: compare when dirty-owner cost is paid.

```text
node1 creates dirty remote-owned lines
node0 creates capacity pressure
node0 or node1 revisits the evicted hot lines
```

Report both stages:

```text
eviction/admission latency
first revisit latency
```

This prevents spill from appearing artificially fast by deferring recall and
prevents naive from appearing artificially fast by charging only revisit.

#### HA07: Producer-Consumer Stream

Purpose: represent an application-style communication pattern.

```text
node0 produces fixed-size records into a home-placed ring
node1 consumes and validates sequence/checksum
```

Variants: 64 B, 256 B, 4 KiB records; single outstanding and windowed.

Metrics:

- one-way item latency;
- sustained throughput;
- p99 queueing latency;
- validation failures.

#### HA08: Lock and Barrier Contention

Purpose: compare synchronization rather than bulk memory traffic.

```text
two-node ticket lock or sequence lock
two-party barrier
configurable local work between synchronization points
```

Metrics:

- lock handoff latency;
- barrier latency;
- operations/second;
- fairness between nodes.

#### HA09: Mixed Local Compute and Remote Directory Pressure

Purpose: evaluate the claimed separation between UBCC and an HA directory that
shares HN-F resources.

```text
one thread performs latency-sensitive local memory accesses
another thread or phase creates remote ownership/directory traffic
```

Metrics:

- local-access p50/p99 with no pressure;
- local-access p50/p99 under remote pressure;
- degradation ratio;
- remote throughput.

This scenario is important for the structural HA comparison, but the result is
reported as observed behavior, not assumed to favor CC-EP.

#### CC-Only Diagnostic: Metadata L3 Locality

This is not run on HA because HA does not expose CC-EP's H64 metadata format.
It diagnoses the implementation used in the comparison:

```text
repeat one bucket
scan a metadata working set below 256 KiB
scan a metadata working set above 256 KiB
```

Metrics: MetaRNF/HN-F hits, misses, ReadOnce latency, WriteUnique latency, and
working-set-size curves.

### 5.5 Dataset Sizes

Each workload supports at least three working-set classes:

| Class | Definition |
|---|---|
| `cache-resident` | Fits in the relevant private or HN-F cache. |
| `directory-pressure` | Exceeds CC ResidentDir capacity by 25-75%. |
| `memory-streaming` | Exceeds local caches and has low temporal locality. |

Absolute sizes are emitted in the result manifest. HA capacity differences are
handled by a working-set sweep, not by silently changing only the HA workload.

### 5.6 Result Format

Every process emits machine-readable JSON Lines. Required records:

```json
{"kind":"manifest","scenario":"HA05","mode":"cc-opt","nodes":2,"sockets_per_node":1,"threads_per_node":1,"working_set_bytes":4194304,"iterations":1000,"seed":131}
{"kind":"sample","scenario":"HA05","phase":"first_revisit","node":1,"iteration":0,"latency_ns":1234}
{"kind":"summary","scenario":"HA05","phase":"first_revisit","samples":1000,"p50_ns":1234,"p95_ns":1500,"p99_ns":1800,"mean_ns":1270,"throughput_ops_s":787401}
{"kind":"validation","scenario":"HA05","errors":0,"checksum":"0x..."}
```

CC runs additionally emit a sidecar protocol summary:

```text
Outer requests
Bloom shortcuts
H64 lookups found/not-found
MetaRNF HN-F hits/misses
forced invalidations
dirty recalls
retry/busy counts
```

### 5.7 Fairness Controls

The following must be recorded and held constant or explicitly normalized:

- two nodes, one socket per node;
- CPU/thread affinity;
- one primary thread per node for latency tests;
- memory placement and first-touch policy;
- cacheline size;
- dataset and random seed;
- warmup and measured iteration count;
- compiler and optimization flags;
- synchronization algorithm;
- operation batch size;
- validation enabled in all modes;
- no debug logging in timed regions;
- no CC-only `coherence_settle()` inside timed regions;
- no HA-only sleep or polling delay;
- no timeout result counted as a sample.

Clock frequencies, memory technology, interconnect speed, and cache sizes are
reported rather than assumed equal. Results include both raw latency and
normalized ratios to each platform's local baseline where appropriate.

### 5.8 Next-Day Minimum Deliverable

The customer-facing package required before the full protocol rework is split
into an immediately portable subset and a provisional capacity subset.

#### Immediately Portable and Reportable

These scenarios do not depend on H64 Bloom rebuild correctness:

| Priority | Scenario | Customer-facing purpose |
|---|---|---|
| P0 | HA01 local reuse | Establish each platform's local baseline and timer overhead. |
| P0 | HA02 remote cold/hot read | Compare basic two-node remote access and cache reuse. |
| P0 | HA03 ownership ping-pong | Compare dirty ownership handoff latency and throughput. |
| P0 | HA04 shared-read then writer | Compare the mandatory one-peer invalidation floor. |
| P1 | HA07 producer-consumer, 64 B and 4 KiB | Provide an application-style latency/throughput result. |

HA03 includes two separately reported variants:

```text
HA03A: alternating writer ownership between nodes
HA03B: acquire remote exclusive permission once, then measure repeated local
       writes, exposing whether an avoidable outer upgrade remains
```

HA03B is the initial naive-versus-optimized differentiator. The HA result is
reported as the native machine's behavior; CC naive disables silent upgrade,
while CC optimized enables the declared local upgrade optimization.

#### Provisional Diagnostic Only

HA05 capacity shared-victim revisit may be implemented and run immediately,
but until joint ResidentDir/H64 Bloom rebuild is complete, the current CC
optimized result includes the known all-H64-miss degradation. It must be
labelled:

```text
PROVISIONAL: pre-Bloom-rebuild diagnostic, not final acceptance data
```

HA06 dirty-owner capacity lifecycle is scheduled after `_lineDataCache` removal
because the current naive path still uses that compatibility cache. Publishing
it earlier would compare two different data-authority models.

#### Required Package Contents

```text
source/
  common workload cores
  HA native adapter
  CC-EP adapter
bin/
  target-specific binaries or reproducible build scripts
configs/
  exact scenario parameters and random seeds
results/
  raw JSONL, summary CSV, validation records, and platform manifests
README.md
  build, placement, affinity, execution, and interpretation instructions
```

For every immediately reportable scenario, run:

```text
HA native: at least 5 process repetitions
CC naive: at least 5 simulation repetitions when runtime permits
CC optimized: at least 5 simulation repetitions when runtime permits
```

If simulation cost prevents five completed runs before delivery, publish the
completed run count and confidence limitation rather than mixing partial or
timed-out runs into the sample set.

## 6. Track D: Acceptance and Reporting

### 6.1 Protocol-Correctness Gate

No performance result is accepted unless:

- all validation checks pass;
- all CC managed child exit statuses are zero;
- Bloom rebuild reports no false negative or partial publication;
- `_lineDataCache` is absent;
- no fallback-zero, functional-cache bypass, or timeout recovery occurred;
- queue high-water remains below configured hard bounds.

### 6.2 Scenario-Specific Metrics

Do not use one all-Outer arithmetic mean. Report separately:

```text
local reuse
remote cold read
remote hot read
ownership handoff
shared-to-writer invalidation
capacity admission
first post-pressure revisit
steady post-pressure reuse
dirty lifecycle total
producer-consumer throughput
lock/barrier handoff
local degradation under remote pressure
```

### 6.3 Comparison Tables

Customer-facing output contains one row per scenario and working-set class:

| Scenario | Metric | HA native | CC naive | CC optimized | Opt vs naive | Opt vs HA |
|---|---|---:|---:|---:|---:|---:|
| HA05 | first revisit p50 | TBD | TBD | TBD | TBD | TBD |
| HA06 | total lifecycle p50 | TBD | TBD | TBD | TBD | TBD |
| HA07 | throughput | TBD | TBD | TBD | TBD | TBD |

No statement such as "CC is faster than HA" is made from a scenario that uses
different topology, data placement, sample boundaries, or workload semantics.

## 7. Implementation Phases

### Phase 0: Freeze Contracts

- Confirm customer HA allocation, affinity, barrier, timer, and cache-control
  APIs.
- Freeze JSONL result schema and scenario parameters.
- Capture current CC functional baselines.

### Phase 1: Portable 2N1S Core

- Implement platform adapter interface.
- Implement HA01-HA04 first because they do not depend on H64 rebuild.
- Produce HA native and CC naive/optimized binaries from the same core source.

### Phase 2: Authoritative Data Path

- Bound `DsmDataStore` pending operations.
- Migrate recall, writeback, grant, and batch-RS paths.
- Remove `_lineDataCache` completely.

### Phase 3: Joint Bloom Rebuild

- Implement shared group mapping and slice state.
- Implement bounded ResidentDir plus H64 scan.
- Add concurrent mutation handling and atomic publication.

### Phase 4: Restore Shortcut

- Enable negative shortcut only for valid slices.
- Add H64/Bloom counters and fault tests.

### Phase 5: Capacity Workloads

- Implement HA05 and HA06.
- Add CC-only metadata L3 locality diagnostic.
- Run naive, spill-no-opt, and optimized comparisons.

### Phase 6: Application and Contention Workloads

- Implement HA07-HA09.
- Run the customer-facing HA/native, CC naive, and CC optimized matrix.

## 8. Commit and Review Boundaries

Recommended commits:

1. `docs: define 2n1s HA comparison contract`
2. `test: add portable 2n1s workload adapter`
3. `test: add basic remote and ownership scenarios`
4. `refactor: bound authoritative DSM data operations`
5. `refactor: remove UBCC line data cache`
6. `feat: add H64-aligned Bloom slice state`
7. `feat: rebuild Bloom from resident and H64 metadata`
8. `feat: restore authoritative Bloom negative shortcut`
9. `test: add capacity revisit and dirty lifecycle scenarios`
10. `test: add producer consumer and interference scenarios`

Each logical code commit requires focused tests plus the strict completion
review before proceeding to the next phase.
