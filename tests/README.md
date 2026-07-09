# E2E Test Suite — Test Case Reference

## Topology Mapping

| 拓扑 | 命令 | 节点×Socket | 适用 TC |
|------|------|------------|---------|
| 3n1s | `--1s` | 3×1 | TC1-31,36-38,40-54,63-64,80,84-85 |
| 3n2s | `--2s` | 3×2 | TC32-35,39,81 (dual-socket) |
| 8n1s | `--8n1s` | 8×1 | TC90-94,82 |
| 8n2s | `--8n2s` | 8×2 | 待实现 |

## Test Case List

### Basic Protocol (TC1-10) — 3n1s

| TC | 名称 | 描述 |
|----|------|------|
| 1 | dsm_local | 单节点本地 DSM 读写 smoke test |
| 2 | remote_read | 跨节点远程读 |
| 3 | pingpong | 双节点乒乓 owner 转移 (3 rounds) |
| 4 | three_node_ring | 三节点环形 owner 转移 |
| 5 | single_writer | 单写者多读者一致性 |
| 6 | multi_sharer | 多 reader 共享一致性 |
| 7 | writeback_evict | 写回/逐出路径 — 数据持久 |
| 8 | upgrade_invalidate | Shared→Upgrade 无效化其他 sharer |
| 9 | non_dsm_negative | 非 DSM 地址负面测试 |
| 10 | concurrent_atomic | 并发读写原子性 (无 torn read) |

### Concurrency & Races (TC11-19) — 3n1s

| TC | 名称 | 描述 |
|----|------|------|
| 11 | local_upgrade | 本地写升级 snoop 通知链 |
| 12 | sync_barrier | sync_wait barrier 正确性 (10 iterations) |
| 13 | remote_release_acquire | 跨节点 invalidate + fence 排序 |
| 14 | multi_sharer_wave | 三节点混合读写波 + 重共享 |
| 15 | credit_storm | 信用压力下资源恢复 |
| 16 | dual_upgrade_race | 双 shared-upgrade 竞态 |
| 17 | writeback_dma | Writeback + home 侧覆写 |
| 18 | dir_fill_replay | 目录填充/重放 smoke |
| 19 | dir_dirty_persist | 目录 dirty 持久化 smoke |

### Directory/Backstore/Bloom (TC20-31) — 3n1s

| TC | 名称 | 描述 |
|----|------|------|
| 20-21 | offload_smoke_a/b | Schema A/C 卸载冒烟 |
| 22 | resident_capacity_pressure | ResidentDir >60K lines 容量压力 |
| 23 | bloom_false_positive | Bloom Filter 假阳性容忍 |
| 24 | multinode_pressure_stress | 三节点并发 + 目录压力 |
| 25 | invalidate_clear_cycle | 快速 INVALIDATE/Clear 循环 |
| 26 | l3_eviction_writeback_chain | L3 容量逐出链 |
| 27 | epoch_wrap_stress | epoch 回绕 (24-bit wrap) |
| 28 | backstore_metadata_consistency | dirty 数据 + 元数据镜像一致性 |
| 29 | local_upgrade_from_exclusive | Exclusive 本地升级 |
| 30 | stale_clear_tombstone | 过期 tombstone 重放 |
| 31 | multicpu_concurrent_isolation | 4 CPUs/node 并发隔离 |

### Dual-Socket (TC32-39) — 3n2s

| TC | 名称 | 描述 |
|----|------|------|
| 32 | cross_socket_read_miss | 跨 socket 读缺失 |
| 33 | cross_socket_writeback | 跨 socket 写回 |
| 34 | dual_socket_pingpong | 双 socket 乒乓 |
| 35 | numa_latency_stress | NUMA 跨 socket/node 混合压力 |
| 36-37 | owner_upgrade_ge/gm | G_E/G_M 窗口 owner 升级 |
| 38 | stale_clear_tombstone_storm | 高频 stale Clear storm |
| 39 | dual_socket_same_pa_interference | 同 PA 跨 socket 干扰 |

### Recall/Orphan/Timeout (TC40-46) — 3n1s

| TC | 名称 | 描述 |
|----|------|------|
| 40 | recall_timeout_retry | RECALL 超时重试 |
| 41 | recall_invalidate_overlap | RECALL+Invalidate 重叠 |
| 42 | exact_epoch_wrap_24b | 24-bit epoch 精确回绕 |
| 43 | rapid_owner_cycle | 三节点快速 owner 循环 |
| 44 | full_protocol_matrix | 密集多 PA 协议矩阵回归 |
| 45 | fill_conflict_bloom_sat | fill 冲突 + bloom 饱和 |
| 46 | multibeat_recall | 多拍 recall 数据完整性 (64B) |

### Fault Injection (TC47-49) — 3n1s

| TC | 名称 | 描述 |
|----|------|------|
| 47 | drop_clear | 丢 Clear — tombstone recovery |
| 48 | dup_inv_ack | 重复 InvalidateAck — 幂等 |
| 49 | reorder_acks | InvalidateAck 重排 |

### Complex Applications (TC50-54) — 3n1s

| TC | 名称 | 描述 |
|----|------|------|
| 50 | producer_consumer_ring | 生产者-消费者环形 |
| 51 | bank_ledger | 银行账本 |
| 52 | mapreduce_scatter_gather | MapReduce scatter/gather |
| 53 | cache_contention_storm | 缓存争用风暴 |
| 54 | numa_tiled_matmul | NUMA 分块矩阵乘 |

### Recall Orphan (TC63-64) — 3n1s

| TC | 名称 | 描述 |
|----|------|------|
| 63 | recall_orphan_timer_cleanup | timer cleanup 清理 orphan |
| 64 | recall_done_orphan_lazy_cleanup | lazy cleanup 清理 orphan |

### Latency Measurement (TC80-82)

| TC | 名称 | 拓扑 | 描述 |
|----|------|------|------|
| 80 | cross_node_latency | 3n1s | 跨节点单 PA 重复读, cntvct_el0 计时 |
| 81 | cross_socket_latency | 3n2s | 同 node 跨 socket vs 同 socket 读延迟对比 |
| 82 | 8node_ring_latency | 8n1s | 8 节点环形读延迟 |

### Capacity Comparison (TC84-85) — 3n1s

| TC | 名称 | 描述 | 配置差异 |
|----|------|------|---------|
| 84 | cacheline_capacity_vanilla | baseline: 无 BF/backstore | `UBCC_BF_BYTES=0` |
| 85 | cacheline_capacity_optimized | BF+backstore 优化 | 默认配置 |

TC84/85 运行同一 workload 代码，区别在 gem5 CLI 参数。

### 8-Node Workloads (TC90-94) — 8n1s

| TC | 名称 | 描述 |
|----|------|------|
| 90 | 8node_all_to_all | 8 节点全对全 DSM 读写 (8×8=64 reads) |
| 91 | 8node_hotspot | 8 节点争用同一个 PA |
| 92 | 8node_butterfly | 8 节点环形数据迁移 (i→(i+1)%8) |
| 93 | 8node_pairwise_pingpong | 4 对同时乒乓 (0↔1,2↔3,4↔5,6↔7) |
| 94 | 8node_barrier_stress | 8 节点单轮 barrier + 读写 |

## 运行示例

```bash
# 单个 TC
TIMEOUT_SEC=600 bash tests/e2e/run_multi.sh --1s 3

# 8 节点
TIMEOUT_SEC=600 bash tests/e2e/run_multi.sh --8n1s 90

# 多个 TC
TIMEOUT_SEC=600 bash tests/e2e/run_multi.sh --1s 1 2 3 4 5
```

## 延迟 Trace 分析

```bash
grep -h 'TRACE-PERF' logs/*/gem5_tc*_node*/stderr.log \
  logs/*/ubio_n*/stderr.log logs/*/nsim_tc*.log \
  | sort -t'|' -k1 -n | python3 scripts/trace2chain.py > chains.json
python3 scripts/chain2html.py --target-ns 415 chains.json > tc.html
```

## 参数外部化

- `chi_params.json`: gem5 延迟/容量参数
- `EP_LINK_LATENCY_PS` / `EP_SYNC_INTERVAL_PS`: ZMQ 延迟
- `EP_RETRY_CYCLES` / `EPRN_COMPACK_RETRY_CYCLES` 等: C++ 重试周期
- `scripts/solve_latency_params.py --x-ns 20`: 参数求解器
