# CC-EP 全量性能指标报告

> 报告日期：2026-07-29
> 数据范围：TC80-TC85、TC90-TC140、TC210-TC219，以及指标 1/2 专项结果
> 计时源：Arm `CNTVCT_EL0=25,165,824 Hz`，约 `39.736 ns/tick`

## 1. 执行摘要

| 验收项 | 正式口径 | 当前结果 | 判定 |
|---|---|---:|---|
| 指标 1：追踪容量 | spill 等效追踪容量不低于 naive ResidentDir 的 150% | 102,656 / 65,536 = **156.64%** | PASS（最新协议修复前正式矩阵，待复跑确认） |
| 指标 1：压力后协议成本 | spill-noopt Outer mean 相对 naive 增量不超过 25 ns（50 cycles @ 2 GHz） | 原始精度差值 **6.03 ns**，约 12.1 cycles | PASS（最新协议修复前正式矩阵，待复跑确认） |
| 指标 2：同步时延 | baseline guest-visible 均值大于 500 ns 的适用 case，optimized 相对 naive 的 case-level 降幅等权平均不低于 10% | 6 个适用 case 平均降低 **54.32%** | PASS |
| HA portable 性能场景 | 相同操作序列、barrier、seed、JSONL schema 下比较 guest-visible 指标 | HA10 降低 **47.24%**，吞吐提升 **89.54%** | PASS |
| 正确性门禁 | 所有纳入性能结论的运行必须先通过 testcase verifier | TC135-TC140 18/18；TC217 3/3 | PASS |

当前数据支持两个明确结论：spill 在固定片上容量下达到 1.566 倍的等效追踪容量；在具有目录压力和状态复用的 guest-visible 路径中，optimized 相对 naive 的平均时延降幅明显超过 10%。同时必须保留边界结论：dirty-owner handoff（TC138）和 checkpoint recover（TC132）存在额外成本，`spill-opt` 尚未表现出相对 `spill-noopt` 的稳定独立优势。

## 2. 指标口径

本报告严格区分以下三类数据：

| 数据类型 | 来源 | 可用于什么结论 |
|---|---|---|
| guest-visible 路径指标 | `[GUEST-TIMER]`、`[PERF-LATENCY]`、portable JSONL sample | 正式延迟、分布、吞吐和跨 CC/HA 对照 |
| 完整 scenario 指标 | `workload_total` | 比较包含 setup、barrier、fault recovery 和 validation 的整体成本；不能解释为单次访存延迟 |
| Outer protocol diagnostic | `[EP-PERF] kind=outer` | 协议路径诊断及指标 1 的既定附加成本门限；不能替代 guest-visible 指标 |

相对变化定义：

```text
latency_reduction = (naive - optimized) / naive * 100%
throughput_gain   = (optimized - naive) / naive * 100%
```

指标 2 对每个适用 case 先独立计算百分比，再做等权平均。不把 16-op batch 的绝对纳秒数与单操作 testcase 直接相加，也不要求每个适用 case 单独降低 10%。

## 3. 指标 1：容量与压力成本

TC131 使用 8N1S catalog full-scan workload 和 512 KiB 长期片上状态预算。

| 项目 | naive | spill-noopt | 结果 |
|---|---:|---:|---:|
| 等效追踪 cacheline 数 | 65,536 | 102,656 | +56.64% |
| 容量要求 | 98,304 | 102,656 | 超出 4,352 lines |
| Outer mean | 162.72 ns | 168.76 ns | +6.03 ns |
| Outer median | 11 ns | 11 ns | 无变化 |

容量比例：

```text
102,656 / 65,536 = 1.5664
```

压力成本换算：

```text
6.03 ns * 2 GHz = 12.06 cycles
```

因此容量 `>=150%` 和平均额外成本 `<=25 ns / 50 cycles` 两项均通过。该结果来自 deferred UpgradeResp 和 shared-release 修复前的正式矩阵；协议修复后的 TC131 三 profile 复跑仍列为最终复核项，复跑前不扩大本结论的适用范围。

## 4. 指标 2：大于 500 ns 场景

适用集合由 naive guest-visible mean `>500 ns` 决定：

| TC | 场景 | naive mean | optimized mean | 时延降幅 |
|---:|---|---:|---:|---:|
| 135 | preserved sharer first load | 2,543.13 ns | 238.42 ns | 90.63% |
| 136 | preserved owner store completion | 2,622.60 ns | 317.89 ns | 87.88% |
| 137 | new requester first load | 2,582.87 ns | 2,026.56 ns | 21.54% |
| 138 | dirty-owner handoff store | 2,622.60 ns | 2,940.50 ns | -12.12% |
| 139 | mixed 16-operation batch | 45,220.06 ns | 4,172.33 ns | 90.77% |
| 217 / HA10 | skewed catalog，按 useful op | 500.74 ns/op | 264.19 ns/op | 47.24% |

等权平均：

```text
(90.63 + 87.88 + 21.54 - 12.12 + 90.77 + 47.24) / 6
= 54.32%
```

TC140 baseline 为 357.63 ns，不进入该集合。TC138 是已知局部退化项，必须披露；它不改变指标 2 按适用 case 平均值验收的正式结果。

## 5. 3N1S 机制性能矩阵

| TC | 核心路径 | naive mean | spill-noopt mean | spill-opt mean | optimized vs naive |
|---:|---|---:|---:|---:|---:|
| 135 | preserved sharer revisit | 2,543.13 ns | 238.42 ns | 238.42 ns | -90.63% |
| 136 | preserved owner store | 2,622.60 ns | 317.89 ns | 317.89 ns | -87.88% |
| 137 | new requester load | 2,582.87 ns | 2,026.56 ns | 2,026.56 ns | -21.54% |
| 138 | dirty owner handoff | 2,622.60 ns | 2,940.50 ns | 2,940.50 ns | +12.12% |
| 139 | mixed 16-op batch | 45,220.06 ns | 4,172.33 ns | 4,172.33 ns | -90.77% |
| 140 | cross-L2 owner store | 357.63 ns | 357.63 ns | 357.63 ns | 0.00% |

TC139 独立吞吐测量覆盖 256 个 mixed operations：

| profile | operations/s | 相对 naive |
|---|---:|---:|
| naive | 353,069 | baseline |
| spill-noopt | 3,741,261 | +959.64% |
| spill-opt | 3,743,435 | +960.26% |

这些收益主要来自 spill 后保留的 sharer/owner copy，不应归因于尚未观测到稳定事件证据的 batch-RS 或 silent-upgrade 命中。

## 6. 容量压力 workload

以下采用各 workload 的路径级 phase，而非把 `workload_total` 当作单操作延迟：

| TC / phase | operations | naive ticks/op | spill-noopt ticks/op | spill-opt ticks/op | optimized vs naive |
|---|---:|---:|---:|---:|---:|
| TC130 post-pressure hot reuse | 96 | 110.64 | 46.82 | 46.82 | -57.68% |
| TC131 catalog reuse | 8,192 | 6.54 | 6.40 | 6.42 | -1.84% |
| TC131 exclusive upgrade | 256 | 9,358.80 | 9,429.41 | 9,429.93 | +0.76% |
| TC132 checkpoint recover | 8,192 | 48.73 | 67.97 | 67.98 | +39.49% |
| TC133 frontier reuse | 4,096 | 7.09 | 6.60 | 6.58 | -7.17% |
| TC134 window reuse | 4,096 | 41.67 | 9.78 | 9.83 | -76.42% |

结果表明 spill 的优势与访问模式相关：复用型 pressure workload 通常受益明显；涉及 dirty metadata 恢复或 exclusive upgrade 的路径可能承担额外工作。

## 7. HA 2N1S Portable Workload

HA01-HA09 的完整场景差异多数低于 0.03%。HA05/HA06 的路径级数据更有解释力：

| 场景 / phase | naive ticks/op | spill-noopt ticks/op | 解释 |
|---|---:|---:|---|
| HA05 first revisit | 4.64 | 3.95 | spill 快约 14.8% |
| HA06 first revisit | 4.59 | 3.91 | spill 快约 15.0% |
| HA06 eviction admission | 6.69 | 8.00 | spill 慢约 19.6% |
| HA08 barrier | 5,108.44 | 5,108.47 | 无可分辨差异 |
| HA08 sequence-lock handoff | 10,325.06 | 10,325.03 | 无可分辨差异 |

HA10 是当前主 portable 性能场景：2 nodes x 1 socket、512-entry ResidentDir、16 catalog lines、640 pressure lines、8 个 batch，每 batch 14 reads + 2 updates。

| profile | mean 16-op batch | P50 | P99 | mean ns/op | useful ops/s |
|---|---:|---:|---:|---:|---:|
| naive | 7,987.02 ns | 7,748.60 ns | 13,391.18 ns | 500.74 | 1,997,040 |
| spill-noopt | 4,212.06 ns | 4,172.33 ns | 4,450.48 ns | 263.88 | 3,789,677 |
| optimized | 4,212.06 ns | 4,172.33 ns | 4,490.22 ns | 264.19 | 3,785,224 |

optimized 相对 naive 的 useful latency 降低 47.24%，独立测量吞吐提升 89.54%。三 profile 均通过双节点 JSON validation 和最终 update key 校验。

## 8. 全矩阵覆盖

| 数据集 | 覆盖 | 结果 |
|---|---|---|
| TC90+ 原始三配置矩阵 | 86 个适用组合，10 个语义 SKIP | 86 PASS，0 FAIL |
| phase-timer 重测 | TC80-TC85、TC90-TC134、TC200-TC203、HA01-HA09 | 128 PASS，10 SKIP，0 FAIL |
| TC135-TC140 分布/吞吐矩阵 | 6 TC x 3 profiles | 18 PASS，0 FAIL |
| TC217 HA10 final | 3 profiles | 3 PASS，0 FAIL |

普通小型场景大多没有形成目录容量压力，因此三 profile 接近是预期结果，不能据此否定容量压力路径上的收益。

## 9. 风险与后续复核

- TC131 正式容量数据需在最新 deferred UpgradeResp/shared-release 修复后复跑三 profile。
- TC217 当前是单轮 final 数据；建议至少 3 个独立 `E2E_RUN_ID`，报告跨 run mean、stdev 和 CV。
- TC138 dirty-owner handoff 和 TC132 checkpoint recover 是明确退化路径，应继续做消息流和长尾拆分。
- `spill-opt` 与 `spill-noopt` 当前大多接近；在确认优化事件消费者和稳定 marker 前，不宣称独立优化收益。
- Outer P99 受 setup 和混合协议流量影响，只用于诊断，不作为 HA/CC 跨平台性能结论。

## 10. 数据与复现入口

- `docs/measure/tc90_perf_matrix_20260728f_zh.md`
- `docs/measure/tc135_tc140_perf_matrix_20260728.md`
- `docs/measure/tc217_ha10_2n1s_perf_20260728.md`
- `logs/tc90_perf_matrix_20260728f/summary.json`
- `logs/tc135_perf_matrix_20260728_all/summary_merged.json`
- `logs/tc217_ha10_final_{naive,spill-noopt,optimized}/guest_summary.jsonl`
- `scripts/evaluate_capacity_latency.py`
- `scripts/summarize_tc90_perf_matrix.py`
- `scripts/summarize_tc135_perf_matrix.py`
- `scripts/summarize_2n1s_guest.py`
