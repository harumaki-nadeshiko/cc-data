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

## 5. Track C：可移植 2N1S HA 对比套件

### 5.1 对比矩阵

每个场景均在以下三种模式中执行：

| 模式 | 说明 |
|---|---|
| `ha-native` | 客户的双节点、每节点单 socket HA 机器。 |
| `cc-naive` | 使用 naive overflow、关闭延迟优化的 CC-EP 2N1S。 |
| `cc-opt` | 使用 spill、有效 Bloom shortcut 及已声明延迟优化的 CC-EP 2N1S。 |

可选的第四种模式 `cc-spill-no-opt` 用于区分 spill 架构成本和协议优化收益。
它建议用于内部诊断，但客户可见的三列表汇总不强制要求。

### 5.2 可移植 workload 结构

交付物由平台无关的 workload 源码和已文档化的目标端 shim 边界组成。本项目
提供 CC 参考 shim；构建 FPGA 目标时，客户只需替换与架构相关的访问和同步原语：

```text
tests/e2e/workloads/e2e_ha_2n1s_core.c
tests/e2e/workloads/dsm_access.h          # CC reference access shim
tests/ha_2n1s/README.md                   # target replacement contract
```

目标端替换刻意采用行为契约，而不是 API 契约。客户无需披露专有 SDK 细节：

```c
shared_range_load/store(home_node, offset)
two_participant_barrier()
node/thread identity
JSONL result emission
```

核心算法、操作顺序、随机种子、数据规模、验证、预热与样本输出必须完全一致。
客户可在内部使用任意受支持的分配、放置、亲和性、计时器和 cache-control 机制；
本项目既不要求也不请求这些平台细节。

CC adapter 使用 2N1S DSM 映射，但不得向 workload core 暴露 gem5 专有行为。
gem5 协议 marker 仅是补充诊断信息，不是跨平台延迟的主要来源。

### 5.3 计时边界

跨平台主指标是需求可见的耗时：

```text
在被测 load/store/atomic 或操作批次之前立即开始
至
架构完成之后立即结束
```

规则：

- 单独测量并报告 timer-call overhead。
- 对接近计时器分辨率的操作，测量固定批次后除以操作数量。
- 微基准在运行时间允许时，应在预热后至少采集 1,000 个测量样本。
- 报告 p50、p95、p99、均值、最小值、最大值、样本数和吞吐量。
- 仅 CC 可见的 Outer/Recall/MetaRNF trace 用于路径归因，绝不能作为唯一的
  HA 对比计时边界。

### 5.4 必需的 2N1S 场景

#### HA01: Local Reuse Baseline

目的：建立 timer overhead 以及本地 cache/memory 基线。

```text
node0 allocates local hot set
warmup repeated reads/writes
measure repeated local reuse
```

指标：

- 本地读/写 p50/p99；
- 带宽；
- timer overhead；
- 验证 checksum。

这是归一化基线，不用于声称 optimized 相比 naive 的优势。

#### HA02: Remote Cold and Remote Hot Read

目的：在无容量驱逐的条件下对比基础双节点远程访问。

```text
node0 owns data
node1 reads a cold pass
node1 repeats a hot pass fitting in the selected cache working set
```

指标：

- remote cold latency/bandwidth;
- remote hot latency/bandwidth;
- hot/cold ratio;
- CC Outer count for attribution.

#### HA03: Ownership Ping-Pong

目的：测量脏所有权转移和 recall 行为。

```text
one cache line or a small line set alternates writers between node0 and node1
each handoff validates sequence and value
```

变体：

- one line, serialized latency;
- 64 or 256 lines, pipelined throughput;
- read-mostly followed by writer takeover.

指标：

- handoff p50/p99;
- transfers/second;
- retry count;
- CC recall path breakdown.

#### HA04: Shared-Read Then Writer Invalidation

目的：暴露双节点拓扑中的 invalidation 成本。

```text
node0 initializes a line set
node1 reads and retains shared copies
node0 writes the same set
```

指标：

- first writer-after-share latency;
- steady writer latency;
- invalidations per operation;
- CC invalidate request/ack latency and retry count.

在双节点中 fanout 宽度为一，因此该 workload 测量的是 invalidation 延迟，
而非多节点 fanout 扩展性。这是每种模式必须经历的一致性状态转换，确定不可避免的
invalidation 下限；不得将其表述为 spill 专属优化。

#### HA05: Capacity Shared-Victim Revisit

目的：对比 spill 保留的本地复用与 naive invalidation 后的再次访问。

```text
node1 caches a hot set owned by node0
node0 creates conflicting capacity pressure
node1 revisits the hot set
```

`cc-naive` 和 `cc-opt` 使用相同的压力地址和 resident-capacity 比例。在 HA 上，
数据 footprint 应按已发布的 HA directory/cache 容量目标缩放，或作为工作集扫描运行。

指标：

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

目的：对比 dirty-owner 成本的支付时机。

```text
node1 creates dirty remote-owned lines
node0 creates capacity pressure
node0 or node1 revisits the evicted hot lines
```

报告以下两个阶段：

```text
eviction/admission latency
first revisit latency
```

这可防止 spill 因延后 recall 而显得虚假地快，也可防止 naive 因只计入 revisit
而显得虚假地快。

#### HA07: Producer-Consumer Stream

目的：代表应用程序风格的通信模式。

```text
node0 produces fixed-size records into a home-placed ring
node1 consumes and validates sequence/checksum
```

变体：64 B、256 B、4 KiB record；单 outstanding 和窗口化模式。

指标：

- one-way item latency;
- sustained throughput;
- p99 queueing latency;
- validation failures.

#### HA08: Lock and Barrier Contention

目的：对比同步，而非大块内存流量。

```text
two-node ticket lock or sequence lock
two-party barrier
configurable local work between synchronization points
```

指标：

- lock handoff latency;
- barrier latency;
- operations/second;
- fairness between nodes.

#### HA09: Mixed Local Compute and Remote Directory Pressure

目的：评估 UBCC 与共享 HN-F 资源的 HA directory 之间所声称的隔离性。

```text
one thread performs latency-sensitive local memory accesses
another thread or phase creates remote ownership/directory traffic
```

指标：

- local-access p50/p99 with no pressure;
- local-access p50/p99 under remote pressure;
- degradation ratio;
- remote throughput.

该场景对结构性 HA 对比十分重要，但结果应作为观察到的行为报告，而不能预设
其有利于 CC-EP。

#### CC-Only Diagnostic: Metadata L3 Locality

此诊断不在 HA 上运行，因为 HA 不暴露 CC-EP 的 H64 metadata 格式。它用于诊断
对比中所使用的实现：

```text
repeat one bucket
scan a metadata working set below 256 KiB
scan a metadata working set above 256 KiB
```

指标：MetaRNF/HN-F hit、miss、ReadOnce latency、WriteUnique latency 以及
working-set-size 曲线。

### 5.5 数据集规模

每个 workload 至少支持三类工作集：

| 类别 | 定义 |
|---|---|
| `cache-resident` | 可容纳在相关私有 cache 或 HN-F cache 中。 |
| `directory-pressure` | 超过 CC ResidentDir 容量 25-75%。 |
| `memory-streaming` | 超过本地 cache，且时间局部性低。 |

绝对规模写入结果 manifest。HA 容量差异应通过工作集扫描处理，不能只悄然变更
HA workload。

### 5.6 结果格式

每个进程输出机器可读的 JSON Lines。必需记录如下：

```json
{"kind":"manifest","scenario":"HA05","mode":"cc-opt","nodes":2,"sockets_per_node":1,"threads_per_node":1,"working_set_bytes":4194304,"iterations":1000,"seed":131}
{"kind":"sample","scenario":"HA05","phase":"first_revisit","node":1,"iteration":0,"latency_ns":1234}
{"kind":"summary","scenario":"HA05","phase":"first_revisit","samples":1000,"p50_ns":1234,"p95_ns":1500,"p99_ns":1800,"mean_ns":1270,"throughput_ops_s":787401}
{"kind":"validation","scenario":"HA05","errors":0,"checksum":"0x..."}
```

CC 运行还额外输出 sidecar 协议摘要：

```text
Outer requests
Bloom shortcuts
H64 lookups found/not-found
MetaRNF HN-F hits/misses
forced invalidations
dirty recalls
retry/busy counts
```

### 5.7 公平性控制

以下项目必须记录，并保持不变或显式归一化：

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

应报告 clock frequency、memory technology、interconnect speed 与 cache size，
而不是假设它们相同。结果应包括原始延迟，以及在适用时相对各平台本地基线的归一化比值。

### 5.8 次日最小交付物

完整协议重构完成前要求的客户可见包分为可立即移植子集和临时容量子集。

#### 可立即移植并报告

这些场景不依赖 H64 Bloom rebuild 的正确性：

| Priority | Scenario | Customer-facing purpose |
|---|---|---|
| P0 | HA01 local reuse | Establish each platform's local baseline and timer overhead. |
| P0 | HA02 remote cold/hot read | Compare basic two-node remote access and cache reuse. |
| P0 | HA03 ownership ping-pong | Compare dirty ownership handoff latency and throughput. |
| P0 | HA04 shared-read then writer | Compare the mandatory one-peer invalidation floor. |
| P1 | HA07 producer-consumer, 64 B and 4 KiB | Provide an application-style latency/throughput result. |

当前 CC E2E 映射为：HA01/02/03/04/07 对应 TC210/211/212/213/214；HA05/06/08/09
对应 TC215/216/218/219。所有这些场景使用同一 portable core，并以 guest `CNTVCT`
JSONL sample 标注计时来源；CC protocol trace 仍仅用于路径归因。

截至 2026-07-27，TC210-216、TC218、TC219 均已在相同 2N1S topology 与 workload
core 下完成一次 CC naive 和一次 CC optimized 的 strict functional run。该证据只证明
功能正确性和 profile 可执行性：当前 gem5 配置对短 guest `CNTVCT` batch 返回零增量时，
guest summary 必须输出 `timer_resolution_limited=true`，不得将该零值作为性能结果发布。
HA native 的五次重复、可用的 target-side timer 校准和最终跨平台对比表仍是外部交付项。

HA03 包含两个需独立报告的变体：

```text
HA03A: alternating writer ownership between nodes
HA03B: acquire remote exclusive permission once, then measure repeated local
       writes, exposing whether an avoidable outer upgrade remains
```

HA03B 是初始的 naive 与 optimized 区分项。HA 结果报告为 native machine 的行为；
CC naive 关闭 silent upgrade，CC optimized 则启用已声明的本地 upgrade 优化。

#### 仅限临时诊断

HA05 capacity shared-victim revisit 可以立即实现和运行；但在联合 ResidentDir/H64
Bloom rebuild 完成前，当前 CC optimized 结果含有已知的 all-H64-miss 性能退化，
必须标记为：

```text
PROVISIONAL: pre-Bloom-rebuild diagnostic, not final acceptance data
```

HA06 dirty-owner capacity lifecycle 安排在移除 `_lineDataCache` 后，因为当前
naive path 仍使用该兼容 cache。更早发布会比较两个不同的数据权威模型。

#### 必需包内容

```text
source/
  common workload cores
  CC reference shim
  target adaptation notes
bin/
  target-specific binaries or reproducible build scripts
configs/
  exact scenario parameters and random seeds
results/
  raw JSONL, summary CSV, validation records, and platform manifests
README.md
  build, placement, affinity, execution, and interpretation instructions
```

每个可立即报告的场景应执行：

```text
HA native: at least 5 process repetitions
CC naive: at least 5 simulation repetitions when runtime permits
CC optimized: at least 5 simulation repetitions when runtime permits
```

若 simulation cost 导致交付前无法完成五次运行，应发布已完成的运行次数和置信度限制，
而不是将部分完成或超时的运行混入样本集。

当前限制：CC naive/optimized 的单次 strict functional matrix 已完成，但尚未完成每模式
五次 simulation repetition；HA native 运行需要客户目标机、target shim 和校准后的
guest-visible timer，不能由 gem5 protocol trace 替代。

split SE 当前没有实例化 Arm `SystemCounter` / `GenericTimer`，因此 `CNTVCT` sample
仅作为 guest timer health probe 保留；当 batch sample 为零时，summary 标记
`timer_resolution_limited=true`，不得用于 latency 或 throughput 比较。对于 CC 内部
路径分析，可使用 `scripts/analyze_2n1s_cc.py` 汇总 `EP-PERF kind=outer` 的
protocol latency 和 simulated-time throughput；该结果必须保持
`guest_visible=false` 与 `cross_platform_comparable=false`。

2026-07-27 的 CC protocol 诊断结果记录于
`docs/measure/ha_2n1s_cc_protocol_analysis_20260727.md`。在 125% ResidentDir
pressure 下，optimized 已证明走 spill 而 naive 走 destructive eviction；其 protocol
throughput 更高，但 `EP-PERF outer` 的单请求 mean/p95 更高。因此不得将该结果概括为
“optimized 普遍更快”，且仍不能替代 demand-visible latency 的正式验收。

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

- Freeze JSONL result schema and scenario parameters.
- Capture current CC functional baselines.

### Phase 1: Portable 2N1S Core

- Implement HA01-HA04 first because they do not depend on H64 rebuild.
- Produce a CC reference binary and ship the same source for target compilation.

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
