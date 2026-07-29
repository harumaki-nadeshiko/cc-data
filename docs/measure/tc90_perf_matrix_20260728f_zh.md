# TC90+ 三配置性能对比报告

> 更新（2026-07-28）：初版矩阵主要覆盖 `workload_total` 与 TC130-TC134 的既有 phase marker。随后已为全部原先缺少目标路径 timer 的 workload 增加 `[GUEST-TIMER]`，并完成重测。路径级数据应优先采用 `logs/tc90_phase_timer_matrix_20260728/summary.json`；TC112 的最终单-primary 测量在 `logs/tc112_phasefinal_{naive,spillnoopt,spillopt}_20260728/`。本报告早先只基于 `workload_total` 的 TC90-TC129、TC200-TC203、HA 横向结论不再作为路径级性能结论。

## 结论

- 性能矩阵 `tc90_perf_matrix_20260728f` 的 86 个适用组合全部通过；另外 10 个组合按测试语义显式跳过，没有失败项。
- `spill-noopt` 与 `spill-opt` 在大多数小型完整场景上与 `naive` 接近；这符合这些场景未形成明显目录容量压力的预期。
- 容量压力场景中，spill 的价值主要体现在压力后的可复用数据路径。TC130、TC133、TC134 的 post-pressure reuse 均明显优于 naive。
- spill 不是所有访问模式的绝对低延迟替代。TC131 的独占升级、TC132 的 checkpoint recover，以及 HA TC215/TC216 的完整场景，spill 有可测量的额外成本。
- `spill-opt` 相对 `spill-noopt` 的差异普遍很小。本次数据不足以把这一差异归因于 batch-RS；现有运行中尚未确认 `--ubcc-batch-rs=1` 存在实际的 gem5/EP 消费者。

## 实验范围

| 项目 | 内容 |
| --- | --- |
| 数据目录 | `logs/tc90_perf_matrix_20260728f/` |
| 配置 | naive、spill-noopt、spill-opt |
| 有效组合 | 86 PASS |
| 显式跳过 | 10 SKIP |
| 失败组合 | 0 |
| 计时源 | Arm `CNTVCT_EL0`，频率 `CNTFRQ_EL0=25,165,824 Hz` |
| 单位 | `counter_ticks`，约 39.74 ns/tick |
| 样本 | 每个 guest node 的 marker 各一个样本；`workload_total` 的样本数等于参与节点数 |

profile 定义如下。

| profile | directory overflow | silent upgrade | batch-RS |
| --- | --- | --- | --- |
| naive | naive | 0 | 0 |
| spill-noopt | spill | 0 | 0 |
| spill-opt | spill | 1 | 1 |

测试语义限制如下。

| testcase | 未运行 profile | 原因 |
| --- | --- | --- |
| TC125-TC129、TC201-TC203 | naive | 这些是 spill path regression；naive 不经过相同语义路径，不能横向比较 |
| TC200 | spill-noopt、spill-opt | 这是 naive dirty `RecallReq -> RecallResp` eviction regression |

## 计量口径

`[GUEST-TIMER]` 由 guest 在 `CNTVCT_EL0` 上读取，属于 guest-visible 时间。所有百分比使用以下定义：

```text
relative_latency_change = (candidate_ticks_per_operation / naive_ticks_per_operation - 1) * 100%
relative_throughput_change = (naive_ticks_per_operation / candidate_ticks_per_operation - 1) * 100%
```

负的 latency change 表示候选配置更快。`workload_total` 的 `operations=1`，测量的是完整 scenario，总时间包含该 scenario 固有的 barrier、fault、初始化及验证；它不是单次 DSM access latency。带 operations 的 phase marker 才能用于相应路径的每操作比较。

本报告不将 `[EP-PERF] kind=outer` 当作 guest-visible latency。它是 protocol-only diagnostic，不具备跨平台可比性，且不替代 guest 端计时。

## Phase Timer 重测覆盖

为避免把含 barrier、setup、fault recovery 的 `workload_total` 误解为单次数据路径延迟，后续重测在每个原先缺少路径计时的 workload 中，围绕实际的 load/store/upgrade/revisit/admission 循环增加了 `[GUEST-TIMER]`。计时开始与结束都在 guest 中执行；marker 输出发生在测量循环结束后，不计入 elapsed ticks。

| 范围 | 重测结果 | 目标 phase 示例 |
| --- | --- | --- |
| TC80、TC81、TC82、TC84、TC85 | 5/5 PASS | `cross_node_read`、`cross_socket_read`、`ring_read`、`cacheline_capacity` |
| TC90-TC101 | 全部适用 profile PASS | `all_to_all_read`、`cross_socket_read`、`batch_rs_reads`、`direct_fwd_chain` |
| TC102、TC110-TC124 | 全部适用 profile PASS | `writeback_evict`、`cross_node_stress`、`cold_stream_sample`、`hot_reuse`、`direct_fwd_reads` |
| TC125-TC129、TC201-TC203 | 16 spill profile PASS，8 naive profile 按语义 SKIP | `read_onload`、`resident_upgrade_store`、`spill_recall_verify`、`d1_verify_readback` |
| TC200 | 1 naive profile PASS；2 spill profile 按语义 SKIP | `naive_evict_recall`、`naive_recall_verify` |
| HA TC210-TC216、TC218-TC219 | 27 profile PASS | `remote_read`、`ownership_write`、`first_revisit`、`eviction_admission`、`remote_pressure` |

TC90+ phase-timer matrix 的结论是 `128 PASS + 10 SKIP + 0 FAIL`。TC112 在该大矩阵完成后发现缺少 primary CPU guard 的最终审计问题，已单独按最终版本重测三 profile，三者均 PASS。最终 TC112 只保留两个实际工作路径 timer：`local_stress`（256 次 local load/store）和 `cross_node_stress`（32 次 DSM store/load）；没有 `workload_total`，因为为保持该并发 workload 的协议时序，不引入会产生额外 guest 指令的 timer selftest/lifecycle marker。

TC112 最终测量的每操作均值如下。

| phase | naive ticks/op | spill-noopt ticks/op | spill-opt ticks/op |
| --- | ---: | ---: | ---: |
| `local_stress`，256 ops | 10.337 | 10.336 | 10.339 |
| `cross_node_stress`，32 ops | 38.552 | 38.552 | 38.552 |

这说明 TC112 在最终、单-primary participant 的测量下没有 profile 可分辨差异；同时其 correctness verifier 在三 profile 下均通过。

## 容量压力关键结果

### TC130：directory overflow 后 hot reuse

| phase | naive ticks/op | spill-noopt ticks/op | spill-noopt vs naive | spill-opt ticks/op | spill-opt vs naive |
| --- | ---: | ---: | ---: | ---: | ---: |
| workload_total | 38,616.67 | 32,331.67 | -16.28% | 32,329.67 | -16.28% |
| post_pressure_hot_reuse，96 ops | 110.64 | 46.82 | -57.68% | 46.82 | -57.68% |

spill 在完整场景和压力后 hot reuse 中均明显降低 guest-visible ticks。该项是 spill 容量路径收益最直接的证据之一。

### TC131：catalog capacity、reuse 与独占升级

| phase | naive ticks/op | spill-noopt ticks/op | spill-noopt vs naive | spill-opt ticks/op | spill-opt vs naive |
| --- | ---: | ---: | ---: | ---: | ---: |
| workload_total | 2,401,101.00 | 2,419,188.67 | +0.75% | 2,419,319.00 | +0.76% |
| post_pressure_catalog_reuse，8,192 ops | 6.54 | 6.40 | -2.12% | 6.42 | -1.84% |
| exclusive_upgrade，256 ops | 9,358.80 | 9,429.41 | +0.75% | 9,429.93 | +0.76% |

容量验收结果：naive resident capacity 为 65,536；spill 的 H64 exact live 为 102,656。要求的 unique capacity 是 naive 的 1.5 倍，即 98,304；spill 达标，超过要求 4,352 条记录，约 4.43%。

仅作 protocol diagnostic 的 outer 数据如下，不能解释为 guest-visible latency：naive mean 162.72 ns，spill-noopt mean 168.76 ns，差值 +6.03 ns，仍满足该诊断的 +25 ns 限制；spill-opt mean 168.86 ns。outer 中位数三个 profile 都是 11 ns，均值受长尾影响。

### TC132：dirty checkpoint stream

| phase | naive ticks/op | spill-noopt ticks/op | spill-noopt vs naive | spill-opt ticks/op | spill-opt vs naive |
| --- | ---: | ---: | ---: | ---: | ---: |
| workload_total | 1,393,393.67 | 1,430,794.33 | +2.68% | 1,430,796.00 | +2.68% |
| post_pressure_checkpoint_recover，8,192 ops | 48.73 | 67.97 | +39.48% | 67.98 | +39.49% |

spill 在 recover 路径上较慢，符合 backstore metadata/recovery 的额外工作。该结果说明 spill 的容量优势不等价于所有路径的延迟优势。

### TC133：8N1S shared frontier

| phase | naive ticks/op | spill-noopt ticks/op | spill-noopt vs naive | spill-opt ticks/op | spill-opt vs naive |
| --- | ---: | ---: | ---: | ---: | ---: |
| workload_total | 725,467.38 | 736,140.75 | +1.47% | 736,044.00 | +1.46% |
| post_pressure_frontier_reuse，4,096 ops | 7.09 | 6.60 | -6.89% | 6.58 | -7.17% |

完整场景中 spill 略慢，但压力后 frontier reuse 已恢复并超过 naive。spill-opt 在该 reuse phase 比 spill-noopt 再低约 0.31%。

### TC134：8N2S sliding window

| phase | naive ticks/op | spill-noopt ticks/op | spill-noopt vs naive | spill-opt ticks/op | spill-opt vs naive |
| --- | ---: | ---: | ---: | ---: | ---: |
| workload_total | 932,512.13 | 742,830.69 | -20.34% | 742,591.44 | -20.37% |
| post_pressure_window_reuse，4,096 ops | 41.67 | 9.78 | -76.54% | 9.83 | -76.42% |

这是本矩阵最显著的容量压力收益：spill 将完整 scenario 缩短约五分之一，压力后 window reuse 每操作 ticks 降至 naive 的约 23.5%。本次 spill-opt 没有超过 spill-noopt，差异只有约 0.53%。

## 普通 1S 场景

TC116-TC124 在所有三种 profile 下均通过。除 TC120 与 TC121 外，完整场景差异接近 0%。

| testcase | spill-noopt 相对 naive | spill-opt 相对 naive | 说明 |
| --- | ---: | ---: | --- |
| TC116 | -0.01% | +0.00% | directory eviction stress marker scenario |
| TC117 | -0.01% | -0.00% | reordered ClearReq recovery |
| TC118 | -0.01% | 0.00% | dropped/delayed ClearReq recovery |
| TC119 | +0.00% | 0.00% | 近似相同 |
| TC120 | -5.37% | -5.34% | spill 较快 |
| TC121 | -0.86% | -0.85% | cold streaming overflow |
| TC122 | +0.02% | +0.01% | hot reuse after directory pressure |
| TC123 | +0.00% | +0.01% | shared hotset periodic upgrade |
| TC124 | +0.04% | +0.00% | owner/home/requester split |

### TC121-TC124 逐项结果

这些 testcase 使用 `workload_total` marker，每种 profile 有 3 个 guest 样本。下表是完整 scenario 的 guest-visible 平均 counter ticks；负值表示比 naive 更短。

| TC | 场景与主要验证路径 | naive ticks | spill-noopt ticks | spill-noopt vs naive | spill-opt ticks | spill-opt vs naive |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| TC121 | cold streaming overflow；低复用连续写入后由 node1 跨节点抽样读取，给 naive eviction 较有利的低 reuse 条件 | 29,147.33 | 28,897.67 | -0.86% | 28,898.67 | -0.85% |
| TC122 | hot lines 先共享、再写入 cold lines 造成目录压力、最后 node2 重读 hot lines；验证 metadata spill/load 是否保留共享信息 | 25,265.33 | 25,270.33 | +0.02% | 25,266.67 | +0.01% |
| TC123 | shared hotset；node1/node2 共享读取、node0 加入目录压力、node1 周期性升级写入、node2 验证新值 | 29,124.33 | 29,125.00 | +0.00% | 29,126.67 | +0.01% |
| TC124 | owner=node2、home=node1、requester=node0；验证 owner/home/requester 分离下的读取收敛 | 15,031.67 | 15,038.33 | +0.04% | 15,032.33 | +0.00% |

结论：TC121 中 spill 在低复用 overflow 下仍有约 0.85% 的完整场景收益。TC122-TC124 的差异都低于 0.05%，不能从这轮场景级计时中得出 silent upgrade 或 direct-forward 的可测量性能收益。尤其 TC124 的 matrix profile 固定 `--direct-fwd=0`，因此它验证 owner/home/requester coherence contract，不是 direct-forward 开关的 A/B 性能实验。

TC125-TC129 与 TC201-TC203 是 spill-only path regression。它们验证了 spill/offload/onload/replay/push-grant 等路径的完成性，不应以缺失 naive 数据推导性能优劣。TC126 和 TC202 在当前 marker 汇总中只捕获到 spill-opt 的 `workload_total`，因此也不做 noopt/opt 性能结论。

## HA 2N1S 场景

HA TC210-TC216、TC218-TC219 使用 2 nodes x 1 socket。每个 profile 有两个 `workload_total` guest 样本；完整场景时间包含同步屏障、初始化和验证。下表中的负值表示比 naive 更短。

| TC | HA 场景 | naive ticks | spill-noopt ticks | spill-noopt vs naive | spill-opt ticks | spill-opt vs naive |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| TC210 / HA01 | node0 local reuse | 9,139.00 | 9,137.00 | -0.02% | 9,139.00 | 0.00% |
| TC211 / HA02 | node1 remote read node0 数据 | 9,164.00 | 9,161.50 | -0.03% | 9,163.50 | -0.01% |
| TC212 / HA03 | ownership transfer：node0 写，node1 写，node0 读回 | 14,315.50 | 14,311.50 | -0.03% | 14,314.50 | -0.01% |
| TC213 / HA04 | shared-to-writer：共享读后 node1 写入 | 19,448.50 | 19,445.50 | -0.02% | 19,447.00 | -0.01% |
| TC214 / HA07 | producer-consumer；16 次跨节点生产/消费与同步 | 168,574.00 | 168,571.50 | -0.00% | 168,573.50 | -0.00% |
| TC215 / HA05 | capacity shared-victim revisit；640 lines 压力后 node1 重读共享 victim | 23,981.00 | 24,719.50 | +3.08% | 24,729.50 | +3.12% |
| TC216 / HA06 | dirty-owner capacity lifecycle；640 lines admission 后重访 dirty owner | 19,202.50 | 20,013.50 | +4.22% | 20,014.50 | +4.23% |
| TC218 / HA08 | 16 次 barrier 与顺序 lock handoff | 251,505.00 | 251,503.50 | -0.00% | 251,505.50 | +0.00% |
| TC219 / HA09 | local/remote mixed pressure | 9,925.50 | 9,922.50 | -0.03% | 9,922.50 | -0.03% |

HA01-HA04、HA07-HA09 的完整场景差异低于 0.03%，可视为本轮场景级测量分辨率内无明显差异。HA05 与 HA06 的总场景成本分别增加约 3.1% 和 4.2%，但这不表示 victim revisit 本身变慢：原始 HA JSON phase marker 显示以下结果。

| phase marker | naive ticks/op | spill-noopt ticks/op | 解释 |
| --- | ---: | ---: | --- |
| HA05 first_revisit，64 ops | 4.64 | 3.95 | spill 重访更快约 14.8% |
| HA06 first_revisit，64 ops | 4.59 | 3.91 | spill 重访更快约 15.0% |
| HA06 eviction_admission，640 ops | 6.69 | 8.00 | spill admission 更慢约 19.6% |
| HA08 barrier，16 ops | 5,108.44 | 5,108.47 | 近似相同 |
| HA08 seq_lock_handoff，16 ops | 10,325.06 | 10,325.03 | 近似相同 |

因此 HA05/HA06 的解释应是：spill 在 capacity admission 阶段有额外 metadata/backstore 工作，但在压力后的 victim revisit 上保留了可复用状态并更快。HA08 的主要成本来自屏障和顺序交接，两种策略在这些 phase 上没有差异。

这些 HA workload 的 `workload_total` 仍不能当作单次 remote memory operation 的绝对延迟；对 HA05/HA06/HA08，优先使用上表的 JSON phase marker 进行路径级解释。

## 汇总统计与注意事项

对 29 个同时拥有 naive 与候选 profile 的 phase 进行简单、未加权平均，spill-noopt 的平均相对延迟变化为 -4.61%，spill-opt 为 -4.60%；两者中位数分别为 -0.006% 与 0.00%。该平均值受 TC134 等少数强容量压力 phase 影响明显，不能代表所有 workload 的普适加速比。

建议读取结果时遵守以下原则。

- 优先使用 workload 对应的 phase marker，而不是把所有 `workload_total` 混合平均。
- 对 TC130-TC134，应同时观察完整 scenario 与 post-pressure reuse，二者揭示的性能侧面不同。
- 对无 naive 对照的 spill regression，只报告功能通过和绝对 marker，避免虚构横向百分比。
- 对 `spill-opt` 与 `spill-noopt` 的微小差异，不在未确认实际 batch-RS consumer 前作机制归因。
- 对 outer protocol 指标，仅用于协议诊断和既有阈值验收；guest-visible 性能结论以 `[GUEST-TIMER]` 为准。

## 可复现性

原始 manifest：`logs/tc90_perf_matrix_20260728f/matrix.tsv`。

机器可读汇总：`logs/tc90_perf_matrix_20260728f/summary.json`。

生成命令：

```bash
python3 scripts/summarize_tc90_perf_matrix.py \
  logs/tc90_perf_matrix_20260728f \
  > logs/tc90_perf_matrix_20260728f/summary.json
```

TC131 capacity/outer diagnostic 命令：

```bash
python3 scripts/evaluate_capacity_latency.py \
  --baseline-log-dir logs/tc90_perf_matrix_20260728f/naive/tc131 \
  --spill-no-opt-log-dir logs/tc90_perf_matrix_20260728f/spill-noopt/tc131 \
  --optimized-log-dir logs/tc90_perf_matrix_20260728f/spill-opt/tc131
```
