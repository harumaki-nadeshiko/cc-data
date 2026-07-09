# E2E Test Suite — Test Case Reference

> 最后更新: 2026-07-09
> 测试环境: Docker, ZMQ 100ns, 8-32 CPUs
> 预估时间基于 ZMQ=100ns。10ns ZMQ 会显著延长测试时间。

## Topology Mapping

| 拓扑 | 命令 | 节点×Socket | 适用 TC |
|------|------|------------|---------|
| 3n1s | `--1s` | 3×1 | TC1-31,36-38,40-54,63-64,80,84-85 |
| 3n2s | `--2s` | 3×2 | TC32-35,39,81 (dual-socket) |
| 8n1s | `--8n1s` | 8×1 | TC90-94,82 |
| 8n2s | `--8n2s` | 8×2 | 待实现 |

## Test Case List

### Basic Protocol (TC1-10) — 3n1s · 预估 <60s 每个

| TC | 名称 | 描述 | 时间(100ns ZMQ) |
|----|------|------|----------------|
| 1 | dsm_local | 单节点本地 DSM 读写 | ~10s |
| 2 | remote_read | 跨节点远程读 | ~15s |
| 3 | pingpong | 双节点乒乓 owner 转移 | ~20s |
| 4 | three_node_ring | 三节点环形 owner 转移 | ~30s |
| 5 | single_writer | 单写者多读者一致性 | ~30s |
| 6 | multi_sharer | 多 reader 共享一致性 | ~20s |
| 7 | writeback_evict | 写回/逐出路径 | ~20s |
| 8 | upgrade_invalidate | Shared→Upgrade 无效化 | ~30s |
| 9 | non_dsm_negative | 非 DSM 地址负面测试 | ~10s |
| 10 | concurrent_atomic | 并发读写原子性 | ~30s |

### Concurrency & Races (TC11-19) — 3n1s · 预估 <60s 每个

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

### Dual-Socket (TC32-39) — 3n2s · 预估 <180s 每个

| TC | 名称 | 时间 | 描述 |
|----|------|------|------|
| 32 | cross_socket_read_miss | ~60s | 跨 socket 读缺失 |
| 33 | cross_socket_writeback | ~120s | 跨 socket 写回 |
| 34 | dual_socket_pingpong | ~60s | 双 socket 乒乓 |
| 35 | numa_latency_stress | ~180s | 跨 socket/node 混合压力 |
| 36-37 | owner_upgrade_ge/gm | ~30s | G_E/G_M 窗口 owner 升级 |
| 38 | stale_clear_tombstone_storm | ~60s | 高频 stale Clear |
| 39 | dual_socket_same_pa_interference | ~60s | 同 PA 跨 socket 干扰 |

**注意**: TC32-35,39 禁止在 1s 拓扑运行。barrier(`sync_wait`)在 2s 下有已知问题,PASS 的 TC 均未使用 barrier。

### Latency / Capacity / 8-Node (TC80-94) · 预估时间变化大

| TC | 名称 | 拓扑 | 时间 | 描述 |
|----|------|------|------|------|
| 80 | cross_node_latency | 3n1s | ~120s | 跨节点读延迟 cntvct 计时 |
| 81 | cross_socket_latency | 3n2s | ~60s | 同/跨 socket 读对比 |
| 82 | 8node_ring_latency | 8n1s | ~300s | 8 节点环形读 |
| 84 | cacheline_capacity | 3n1s | ~600s | 50 行容量, baseline (BF=0) |
| 85 | cacheline_capacity | 3n1s | ~600s | 同 TC84, BF 启用 |
| 90 | 8node_all_to_all | 8n1s | ~120s | 8×8=64 reads |
| 91 | 8node_hotspot | 8n1s | ~600s | 8 节点争同一 PA |
| 92 | 8node_butterfly | 8n1s | ~120s | 环形数据迁移 |
| 93 | 8node_pairwise_pingpong | 8n1s | ~120s | 4 对乒乓 |
| 94 | 8node_barrier | 8n1s | ~300s | 单轮 8 节点 barrier |

### 预估时间说明

- 以上时间基于 ZMQ=100ns(100000 ps)。ZMQ=10ns 会更慢(同步频率更高)
- 8 节点测试受限 PDES 保守同步开销,单次运行可能超时;日志仍有 trace 可验证进展
- 如测试超时,在日志中检查 nsim `FWD` 事件数和 gem5 `CLK-SYNC` 确认是否在推进

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
