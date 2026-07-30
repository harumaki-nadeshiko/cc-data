# TC142-TC147 可移植大 Workload 多拓扑结果

> 日期：2026-07-30
> policy：`spill-noopt`
> ResidentDir：约 512 entries、1-way
> topology：3n1s、3n2s、8n1s、8n2s

## 场景

| TC | 场景 | 每 plane useful operations |
|---|---|---:|
| 142 | OLTP buffer pool，28 reads + 4 updates/batch | 1,024 |
| 143 | B-tree root/internal/leaf/record traversal + 25% update | 2,048 |
| 144 | WAL append + dirty page update | 1,024 |
| 145 | FaaS warm runtime/tenant state + invocation result | 2,048 |
| 146 | 图 frontier/adjacency/property expansion | 2,048 |
| 147 | Feature-store embedding lookup + sparse update | 2,048 |

所有 TC 每 topology 保持全局 768 条 pressure line；plane 数为 3、6、8、16。
初始化按 plane 串行，业务 batch 并发执行。每个 plane 必须输出完整 phase、32 个
latency samples、service/end-to-end timer 和最终数据校验。

## Correctness 结果

正式日志：

- `logs/portable_large_final_spill_20260730/matrix.tsv`
- `logs/portable_large_final_spill_20260730/summary.json`

| Topology | TC142 | TC143 | TC144 | TC145 | TC146 | TC147 |
|---|---:|---:|---:|---:|---:|---:|
| 3n1s | PASS | PASS | PASS | PASS | PASS | PASS |
| 3n2s | PASS | PASS | PASS | PASS | PASS | PASS |
| 8n1s | PASS | PASS | PASS | PASS | PASS | PASS |
| 8n2s | PASS | PASS | PASS | PASS | PASS | PASS |

最终源码正式完成 24/24 case。双 socket 修正后的结果记录在：

- `logs/portable_large_dualsocket_final_20260730/matrix.tsv`
- `logs/portable_large_dualsocket_final_20260730/summary.json`

期间曾有 TC146/147 8n2s 被磁盘余量门禁中止。清理冗余 `build/runs`
恢复空间后重新运行，两者均 PASS。双 socket worker 使用物理 CPU0/socket0 和
CPU2/socket1，不再把 CPU0/1 错误当成两个 socket。

## Spill Absolute Metrics

数值为 guest CNTVCT。`service ns/op` 是 plane latency 均值；aggregate throughput
使用总 operations 除以最慢 plane elapsed time。end-to-end 包含固定 pressure
和 batch barriers。

### 3n1s

| TC | Service ns/op | Service Mops/s | End-to-end ns/op | End-to-end Mops/s |
|---|---:|---:|---:|---:|
| 142 | 269.27 | 11.13 | 13,476.17 | 0.223 |
| 143 | 185.98 | 16.13 | 6,795.73 | 0.441 |
| 144 | 248.07 | 12.09 | 13,471.54 | 0.223 |
| 145 | 259.93 | 11.54 | 6,869.90 | 0.437 |
| 146 | 198.25 | 15.13 | 6,809.79 | 0.441 |
| 147 | 243.70 | 12.31 | 6,853.80 | 0.438 |

### 3n2s

| TC | Service ns/op | Service Mops/s | End-to-end ns/op | End-to-end Mops/s |
|---|---:|---:|---:|---:|
| 142 | 271.75 | 22.08 | 13,240.20 | 0.453 |
| 143 | 188.43 | 31.84 | 6,675.43 | 0.899 |
| 144 | 249.31 | 24.01 | 13,224.06 | 0.454 |
| 145 | 262.31 | 22.87 | 6,749.21 | 0.889 |
| 146 | 200.73 | 29.83 | 6,688.11 | 0.897 |
| 147 | 246.14 | 24.37 | 6,733.15 | 0.891 |

### 8n1s

| TC | Service ns/op | Service Mops/s | End-to-end ns/op | End-to-end Mops/s |
|---|---:|---:|---:|---:|
| 142 | 271.70 | 29.44 | 13,164.08 | 0.608 |
| 143 | 188.45 | 42.45 | 6,635.93 | 1.206 |
| 144 | 249.58 | 32.04 | 13,144.61 | 0.609 |
| 145 | 262.35 | 30.49 | 6,709.87 | 1.192 |
| 146 | 200.30 | 39.93 | 6,647.99 | 1.203 |
| 147 | 246.18 | 32.49 | 6,693.94 | 1.195 |

### 8n2s

| TC | Service ns/op | Service Mops/s | End-to-end ns/op | End-to-end Mops/s |
|---|---:|---:|---:|---:|
| 142 | 271.61 | 58.89 | 13,112.36 | 1.220 |
| 143 | 188.42 | 84.90 | 6,609.07 | 2.421 |
| 144 | 248.88 | 64.11 | 13,091.06 | 1.222 |
| 145 | 262.29 | 60.99 | 6,682.98 | 2.394 |
| 146 | 200.50 | 79.61 | 6,621.61 | 2.416 |
| 147 | 246.13 | 64.99 | 6,666.93 | 2.400 |

## 扩展性结论

- 24/24 case 的 correctness checks 均通过。
- 3→16 planes 时，各已完成场景的 mean plane service latency 基本稳定，变化约
  0.5%-2%，没有随 topology 扩大出现数量级恶化。
- aggregate service throughput 基本随 active plane 数增长。例如 TC145 从
  3n1s 的 11.54 Mops/s 增至 8n2s 的 60.97 Mops/s。
- 固定 768-line pressure 被更多 plane 分片，因此 end-to-end per-plane time 略降；
  该趋势不能解释成单次 coherence operation 本身变快。
- 双 socket 使用 `plane=node*NUM_SOCKETS+socket`，socket1 实际参与业务访存、
  pressure 和 barrier，不是 idle participant。

## Naive Baseline 限制

portable TC142 的 3n1s naive 在两次独立 3,600 秒尝试中仍未
完成，protocol tick 持续推进，但 guest 未完成 workload。串行 seed 和全局固定
pressure 后仍复现。因此不把 timeout 伪造成 latency 或“无限加速”，也不继续缩小
workload 以人为制造数字。

数据库原版单-worker/固定三节点矩阵仍提供可用的 policy 定量对比：

| TC | Spill service latency | Spill end-to-end latency |
|---|---:|---:|
| 142 | -40.38% | -1.88% |
| 143 | -59.60% | -4.87% |
| 144 | -70.66% | -9.24% |

这组旧数据证明 spill retained-copy policy 的局部与完整窗口收益；本次 portable
矩阵证明 spill 模式可跨 3n1s、3n2s、8n1s 和主要 8n2s 场景扩展。两类证据不能
混合成同一加速百分比。

## 运行基础设施发现

首次 8-node smoke 因 Unix-domain socket endpoint 超长全部在 bind 阶段失败：

```text
File name too long
```

runner 已将 human-readable `LOG_ROOT` 与固定短 `E2E_RUN_ID` 分离，避免约 108-byte
Unix socket path 上限。该失败不是 workload 或 heartbeat 问题。
