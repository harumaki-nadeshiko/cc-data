# CC-EP 性能对比 Brief Summary

> 日期：2026-07-29
> 对比配置：`naive`、`spill-noopt`、`optimized`
> 正式性能口径：guest-visible `CNTVCT_EL0`；`EP-PERF kind=outer` 仅作协议诊断

## 核心结论

| 指标 | 结果 | 判定 |
|---|---:|---|
| 等效 cacheline 追踪容量 | spill 102,656 vs naive 65,536，达到 **156.64%** | 达到 `>=150%` 目标 |
| 压力后 Outer 平均额外成本 | spill-noopt 相对 naive **+6.03 ns**，约 12.1 cycles @ 2 GHz | 低于 25 ns / 50 cycles 上限 |
| baseline `>500 ns` 适用场景平均时延降幅 | optimized 相对 naive **54.32%** | 超过 `>=10%` 目标 |
| HA10 useful latency | 500.74 ns/op 降至 264.19 ns/op，**降低 47.24%** | PASS |
| HA10 useful throughput | 1.997M 提升至 3.785M ops/s，**提升 89.54%** | PASS |

TC131 容量和 Outer 数据来自最新 2S/recall 修复前的正式矩阵，当前仍作为已通过结果引用，但需要在最新代码上复跑三 profile 做最终确认。

## 代表性收益

| 场景 | naive | optimized | 变化 |
|---|---:|---:|---:|
| TC135 preserved sharer first load | 2,543.13 ns | 238.42 ns | **-90.63%** |
| TC136 preserved owner store | 2,622.60 ns | 317.89 ns | **-87.88%** |
| TC137 new requester first load | 2,582.87 ns | 2,026.56 ns | **-21.54%** |
| TC139 mixed 16-op batch | 45,220.06 ns | 4,172.33 ns | **-90.77%** |
| TC139 mixed throughput | 353,069 ops/s | 3,743,435 ops/s | **+960.26%** |
| TC217 / HA10 catalog | 500.74 ns/op | 264.19 ns/op | **-47.24%** |

主要收益来自目录压力后保留的 sharer/owner copy，避免 naive forced eviction 后重新建立全局状态。当前数据不足以把 `spill-opt` 与 `spill-noopt` 的微小差异归因于 silent-upgrade 或 batch-RS。

## 容量压力表现

| 路径 | optimized 相对 naive | 说明 |
|---|---:|---|
| TC130 post-pressure hot reuse | **-57.68%** | hot line 在压力后继续复用 |
| TC131 catalog reuse | **-1.84%** | 大容量 full-scan 下小幅改善 |
| TC133 frontier reuse | **-7.17%** | shared frontier 压力后复用 |
| TC134 window reuse | **-76.42%** | sliding-window 容量收益最明显 |
| HA05 first revisit | 约 **-14.8%** | shared victim 重访更快 |
| HA06 first revisit | 约 **-15.0%** | dirty-owner victim 重访更快 |

## 已知成本与边界

| 场景 | 变化 | 解释 |
|---|---:|---|
| TC138 dirty-owner handoff | **+12.12%** | dirty ownership 转交增加 metadata/onload 工作 |
| TC132 checkpoint recover | **+39.49%** | dirty checkpoint 恢复路径承担额外 backstore 工作 |
| TC131 exclusive upgrade | **+0.76%** | 大容量场景下升级路径轻微增加 |
| HA06 eviction admission | 约 **+19.6%** | spill admission 比 naive eviction 更重 |
| TC140 cross-L2 owner store | **0.00%** | 本轮无可分辨性能差异 |

因此不能声称 spill 对所有路径都更快。它的主要优势是提高固定 SRAM 预算下的追踪容量，并改善压力后的高复用路径；dirty recovery、handoff 和 admission 可能产生额外成本。

## HA 对比结论

HA01-HA09 的大多数完整场景差异低于本轮测量分辨率。HA10 是当前最具代表性的 portable 性能场景：

- 2 nodes x 1 socket。
- 512-entry ResidentDir。
- 16 条 catalog line 和 640 条 pressure line。
- 8 个 batch，每 batch 14 reads + 2 updates。
- 双节点 validation 和最终 update key 全部正确。

HA10 optimized 相对 naive：

```text
Mean latency:  -47.24%
P99 batch:     13.39 us -> 4.49 us
Throughput:    +89.54%
```

该 workload 使用 portable JSONL、相同 seed、相同 barrier 和相同操作序列，可用于后续 CC 与 HA target 的 guest-visible 对比。

## 验证状态

- TC135-TC140：18/18 profile/test 组合 PASS。
- TC217 / HA10：3/3 profiles PASS。
- 低频正确性队列：修复后 **64/64 PASS**。
- 2S 回归 TC32/33/34/35/39/81：全部 PASS。
- 后续 TC82/84/85/128/141：全部重新运行并 PASS。

## 一句话结论

在固定片上状态预算下，spill 将等效追踪容量提升到 naive 的 **1.566 倍**；对 baseline 大于 500 ns 的适用 guest-visible 场景，optimized 平均降低 **54.32%**，并在 HA10 realistic catalog workload 中实现 **47.24% 延迟下降和 89.54% 吞吐提升**，代价是部分 dirty recovery、handoff 和 admission 路径存在可测额外成本。
