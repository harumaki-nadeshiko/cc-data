# CC-EP 性能指标总账与目标判定

> 汇报日期：2026-07-31
> 正式跨方案口径：guest/target-visible counter
> CC 内部诊断口径：`EP-PERF kind=outer`，不得替代 guest-visible 结果

## 1. 执行摘要

两个正式性能目标当前均满足：

| 正式目标 | 验收条件 | 当前证据 | 判定 |
|---|---|---:|---|
| 目标 1A：512 KiB 下等效追踪容量 | spill ≥ naive 的 150% | 102,656 / 65,536 = **156.64%** | PASS |
| 目标 1B：压力后附加成本 | spill-noopt 相对 naive ≤50 cycles，即 2 GHz 下 ≤25 ns | **+6.03 ns = 12.06 cycles** | PASS，最新协议代码待复跑确认 |
| 目标 2：CC 端到端时延 | 对 naive guest mean `>500 ns` 的适用 case，optimized 降幅等权平均 ≥10% | **54.32%** | PASS |

面向 HA 对比的主结论：HA10 在相同 seed、地址、操作序列、barrier 和计时边界下，
CC optimized 相对 CC naive 的 useful latency 降低 **47.24%**，useful throughput
提升 **89.54%**。它是交给未知 HA target 复现的首选 workload。

证据等级提示：

- 目标 1 的 TC131 容量/Outer 结果是后续协议修复前完成的正式矩阵，最新代码待复跑。
- HA10 三 profile 均通过 correctness，但当前每 profile 只有一轮 final run，尚无 CV。
- 上述限制不改变当前验收公式下的 PASS 判定，但必须出现在汇报页脚或口头说明中。

## 2. 测量口径

### 2.1 三种不能混用的时间

| 口径 | 来源 | 可用于 |
|---|---|---|
| guest/target-visible phase | CNTVCT 或 target counter | 正式 latency、percentile、throughput、CC/HA 对比 |
| `workload_total` | 程序生命周期 timer | setup+barrier+validation 的完整场景成本 |
| CC Outer diagnostic | `[EP-PERF] kind=outer` | CC 请求链拆分和目标 1 的既定附加成本门限 |

公式：

```text
latency_reduction = (baseline - candidate) / baseline * 100%
throughput_gain   = (candidate - baseline) / baseline * 100%
ns_per_op         = latency_ticks / operations * 1e9 / timer_frequency_hz
```

目标 2 是 case-level percentage 等权平均，不把不同 operation count 的绝对纳秒
相加。latency 降低与固定工作量下 throughput 增加互为倒数表达，不算两份独立证据。

## 3. 目标 1：容量与附加成本

TC131 使用 8N1S catalog full scan、512 KiB 长期片上状态预算：

| 项目 | naive | spill-noopt | 结果 |
|---|---:|---:|---:|
| unique tracked cachelines | 65,536 | 102,656 | +56.64% |
| 150% 要求 | 98,304 | 102,656 | 超出 4,352 lines |
| Outer mean | 162.72 ns | 168.76 ns | +6.03 ns |
| Outer median | 11 ns | 11 ns | 无变化 |

```text
capacity ratio = 102656 / 65536 = 1.5664
extra cycles   = 6.03 ns * 2 GHz = 12.06 cycles
```

结论：容量和 50-cycle 上限均 PASS。

证据边界：该正式矩阵完成于 deferred UpgradeResp/shared-release 等后续协议修复前。
它仍是当前已有的正式验收结果，但在明早材料中应带脚注：最新代码上的 TC131
`naive/spill-noopt/optimized` 三 profile 复跑尚未完成，不能扩大结论适用范围。

## 4. 目标 2：适用场景总表

适用集合由 naive guest-visible mean 严格大于 500 ns 决定：

| TC | 场景 | naive | optimized | 时延降幅 |
|---:|---|---:|---:|---:|
| 135 | preserved sharer first load | 2,543.13 ns | 238.42 ns | 90.63% |
| 136 | preserved owner store completion | 2,622.60 ns | 317.89 ns | 87.88% |
| 137 | new requester first load | 2,582.87 ns | 2,026.56 ns | 21.54% |
| 138 | dirty-owner handoff store | 2,622.60 ns | 2,940.50 ns | **-12.12%** |
| 139 | mixed 16-operation batch | 45,220.06 ns | 4,172.33 ns | 90.77% |
| 217 | HA10 catalog useful op | 500.74 ns/op | 264.19 ns/op | 47.24% |

```text
(90.63 + 87.88 + 21.54 - 12.12 + 90.77 + 47.24) / 6
= 54.32%
```

TC140 baseline 为 357.63 ns，不进入适用集合。TC138 是明确退化项，但按预先定义
的正式验收公式，目标 2 仍超过 10% 门限。

## 5. 机制性能矩阵 TC135-TC140

拓扑为 3N1S；三 profile 共 18/18 correctness PASS。

| TC | 核心路径 | naive mean | spill-noopt | spill-opt | optimized vs naive |
|---:|---|---:|---:|---:|---:|
| 135 | preserved sharer revisit | 2,543.13 ns | 238.42 ns | 238.42 ns | -90.63% |
| 136 | preserved owner store | 2,622.60 ns | 317.89 ns | 317.89 ns | -87.88% |
| 137 | new requester load | 2,582.87 ns | 2,026.56 ns | 2,026.56 ns | -21.54% |
| 138 | dirty owner handoff | 2,622.60 ns | 2,940.50 ns | 2,940.50 ns | +12.12% |
| 139 | mixed 16-op batch | 45,220.06 ns | 4,172.33 ns | 4,172.33 ns | -90.77% |
| 140 | cross-L2 owner store | 357.63 ns | 357.63 ns | 357.63 ns | 0.00% |

TC139 的 256-op 独立吞吐：

| profile | operations/s | 相对 naive |
|---|---:|---:|
| naive | 353,069 | baseline |
| spill-noopt | 3,741,261 | +959.64% |
| spill-opt | 3,743,435 | +960.26% |

该巨大数字只代表 TC139 timed mixed service region。完整程序 node1 时间约改善
27.1%，不能把 `+960%` 表述为整个系统吞吐提升。

## 6. Capacity Workload

| TC / phase | naive ticks/op | spill-noopt | optimized vs naive | 解释 |
|---|---:|---:|---:|---|
| TC130 post-pressure hot reuse | 110.64 | 46.82 | -57.68% | 保留 hot copy |
| TC131 catalog reuse | 6.54 | 6.40 | -2.12% | 大 full scan 后小幅受益 |
| TC131 exclusive upgrade | 9,358.80 | 9,429.41 | +0.75% | upgrade 轻微增加 |
| TC132 checkpoint recover | 48.73 | 67.97 | +39.48% | dirty metadata/recovery 成本 |
| TC133 frontier reuse | 7.09 | 6.60 | -6.89% | 8N1S shared frontier |
| TC134 window reuse | 41.67 | 9.78 | -76.54% | 8N2S sliding window |

完整场景中 TC134 改善约 20.34%；TC133 完整场景反而慢约 1.47%。因此压力后
reuse phase 和 scenario total 必须同时披露。

## 7. HA01-HA12

### 7.1 HA01-HA09

| 场景 | 路径结论 |
|---|---|
| HA01 local reuse | 三 profile 无可分辨差异 |
| HA02 remote read | 三 profile完整场景差异 <0.03% |
| HA03 ownership handoff | 三 profile完整场景差异 <0.03% |
| HA04 shared-to-writer | 三 profile完整场景差异 <0.03% |
| HA05 shared victim revisit | first revisit 快约 14.8%，但 scenario total 慢约 3.1% |
| HA06 dirty-owner lifecycle | first revisit 快约 15.0%；admission 慢约 19.6%；total 慢约 4.2% |
| HA07 producer-consumer | 无可分辨差异 |
| HA08 barrier/seq-lock | barrier 和 handoff 均无可分辨差异 |
| HA09 local/remote pressure | 无可分辨差异 |

### 7.2 HA10

配置：2N1S、512-entry ResidentDir、16 catalog lines、640 pressure lines、8 batch，
每 batch 14 reads + 2 updates。

| profile | mean 16-op batch | P50 | P99 | mean ns/op | useful ops/s |
|---|---:|---:|---:|---:|---:|
| naive | 7,987.02 ns | 7,748.60 ns | 13,391.18 ns | 500.74 | 1,997,040 |
| spill-noopt | 4,212.06 ns | 4,172.33 ns | 4,450.48 ns | 263.88 | 3,789,677 |
| optimized | 4,212.06 ns | 4,172.33 ns | 4,490.22 ns | 264.19 | 3,785,224 |

三 profile 3/3 correctness PASS。当前是单轮正式结果，尚缺至少 3 个独立 run ID
的 mean/stdev/CV，因此明早可作为功能完整的参考结果，但不要宣称统计置信区间。

### 7.3 HA11/HA12 精确 150% capacity

HA11/HA12 使用 512-entry ResidentDir、64 hot lines 和 704 pressure lines，总计
768 unique lines，capacity ratio 精确为 1.500000。以下为单轮 guest-visible phase
结果；数值是 phase 内平均 ns/op，不是独立重复运行的统计区间。

| 场景/phase | naive | spill-noopt | optimized |
|---|---:|---:|---:|
| HA11 clean admission | 443.9 ns/op | 426.8 ns/op | 426.8 ns/op |
| HA11 clean first revisit | 1,988.3 ns/op | 205.5 ns/op | 205.5 ns/op |
| HA12 dirty admission | 445.2 ns/op | 427.5 ns/op | 427.5 ns/op |
| HA12 dirty first revisit | 1,969.3 ns/op | 187.5 ns/op | 188.7 ns/op |
| HA12 dirty handoff | 398.6 ns/op | 1,269.3 ns/op | 1,268.1 ns/op |

结果体现明确 trade-off：spill 保留原 clean/dirty copy，因此 first revisit 显著缩短；
dirty ownership 真正转移时，需要 metadata restore/owner recall 的路径可能比 naive
更慢。正式矩阵路径为 `logs/ha_exact150_formal_20260731/matrix.tsv`。

## 8. 数据库与实际大场景

### 8.1 原数据库三 TC 的 naive/spill 对比

| TC | 场景 | Service latency | End-to-end latency |
|---:|---|---:|---:|
| 142 | OLTP buffer pool | -40.38% | -1.88% |
| 143 | B-tree traversal | -59.60% | -4.87% |
| 144 | WAL/checkpoint | -70.66% | -9.24% |

service timer 不含 pressure；end-to-end 包含 pressure/barrier。正式汇报必须同时给出，
不能用 service 的大幅收益代替完整 workload 收益。

### 8.2 Portable TC142-TC147

场景覆盖 OLTP、B-tree、WAL、FaaS、图计算、feature store。四种 topology：
3N1S、3N2S、8N1S、8N2S，共 24/24 correctness PASS。

源码和 dynamic verifier 可适配 2N1S，但当前 24/24 结果不包含 2N1S，不能将下表
当作已有 HA target 或 2N1S 性能数据。

| TC | 3N1S service ns/op | 8N2S service ns/op | 8N2S aggregate Mops/s |
|---:|---:|---:|---:|
| 142 | 269.27 | 271.61 | 58.89 |
| 143 | 185.98 | 188.42 | 84.90 |
| 144 | 248.07 | 248.88 | 64.11 |
| 145 | 259.93 | 262.29 | 60.99 |
| 146 | 198.25 | 200.50 | 79.61 |
| 147 | 243.70 | 246.13 | 64.99 |

3→16 planes 时 mean plane service latency 变化约 0.5%-2%，aggregate throughput
随 active planes 扩展。该矩阵只证明 spill 模式的正确性和绝对扩展性；portable
naive 大 workload 在 3,600 秒内未完成，不能据此计算加速百分比。

## 9. 测试覆盖门禁

| 数据集 | 结果 |
|---|---|
| TC90+ phase-timer matrix | 128 PASS + 10 semantic SKIP + 0 FAIL |
| TC135-TC140 三 profile | 18/18 PASS |
| TC217 HA10 三 profile | 3/3 PASS |
| HA01-HA12 exact-150 三 profile | 36/36 PASS |
| TC142-TC147 四拓扑 spill | 24/24 PASS |
| 低频正确性队列 | 64/64 PASS |
| 2S 修复回归 TC32/33/34/35/39/81 | 全部 PASS |

## 10. 明早可讲与不可讲

可讲：

- 固定 SRAM 预算下等效追踪容量为 naive 的 1.566 倍。
- 正式目标 2 的适用集合平均时延降低 54.32%。
- HA10 是可交付、可跨平台复现的主要性能 workload。
- spill 的主要机制收益是 capacity overflow 后保留已有 cache copy。

不可讲：

- 不说所有路径都更快。
- 不把 Outer diagnostic 当成 HA 实机延迟。
- 不把 TC139 `+960%` 当完整系统吞吐。
- 不把 `spill-opt` 与 `spill-noopt` 的近似相同结果归因于 silent-upgrade/batch-RS。
- 不把单轮 HA10 结果包装成有统计置信区间的结论。

## 11. 权威证据入口

- `docs/measure/full_performance_metrics_report_20260729_zh.md`
- `docs/measure/tc135_tc140_perf_matrix_20260728.md`
- `docs/measure/tc217_ha10_2n1s_perf_20260728.md`
- `docs/measure/portable_large_workload_matrix_20260730_zh.md`
- `logs/tc135_perf_matrix_20260728_all/summary_merged.json`
- `logs/tc217_ha10_final_{naive,spill-noopt,optimized}/guest_summary.jsonl`
- `logs/portable_large_dualsocket_final_20260730/summary.json`
