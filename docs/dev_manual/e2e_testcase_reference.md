# E2E Test Case Reference

> 最后更新: 2026-07-17
> 基于 gem5 v4-selfsnoop-fix-clean (commit `4d1cdbf`)
> 分支: `v4`

## 快速索引

| TC | 名称 | 拓扑 | 特殊配置 | 目的 | 通过标准 |
|----|------|------|---------|------|---------|
| 1 | e2e_tc1_dsm_local | 1s | — | 单节点本地 DSM 读写冒烟测试 | 单个 READ_VAL=0xCAFE, MATCH |
| 2 | e2e_tc2_remote_read | 1s | — | 跨节点远程读（Node0 写 Node1 读） | Node1 READ_VAL=0x11223344, MATCH |
| 3 | e2e_tc3_pingpong | 1s | — | 双节点 Ping-Pong owner 转移（3 轮） | 3 个 READ_VAL 全部 MATCH |
| 4 | e2e_tc4_three_node_ring | 1s | — | 三节点环形 owner 转移 | 4 个 READ_VAL，node0/1/2 值分别为 {0x1,0x3}/{0x2}/{0x3} |
| 5 | e2e_tc5_single_writer | 1s | — | 三节点并发写一致性 | 所有节点最终值一致且 ∈ {0xAA000001, 0xBB000002, 0xCC000003} |
| 6 | e2e_tc6_multi_sharer | 1s | — | 多 sharer 读一致性 | Node1+Node2 读 =0xDEADBEEF |
| 7 | e2e_tc7_writeback_evict | 1s | — | Writeback/eviction 路径数据持久性 | 1 个 READ_VAL=0x55667788 |
| 8 | e2e_tc8_upgrade_invalidate | 1s | — | Shared→Upgrade→Invalidate 其他 sharer | Node1 最终读=0xBBB（非 stale 0xAAA） |
| 9 | e2e_tc9_non_dsm_negative | 1s | — | 非 DSM 地址访问必须被拒绝（预期 crash） | **[FATAL] 或 page-fault**，无 READ_VAL |
| 10 | e2e_tc10_concurrent_atomic | 1s | — | 并发读写原子性（无 torn read） | 所有读取值 ∈ [0xA0000000, 0xA0000000+100)，且不为 0 |
| 11 | e2e_tc_local_upgrade | 1s | — | Local write upgrade snoop 通知链 | 三节点均读到升级后的 0xCA01 |
| 12 | e2e_tc12_sync_barrier | 1s | — | sync_wait barrier 正确性（10 iters×3 segs） | 所有 node×iter×seg 都有 SYNC 标记，单调递增 |
| 13 | e2e_tc13_remote_release_acquire | 1s | — | 跨节点 release/acquire 内存序（DATA+FLAG） | FLAG=1 后 DATA=0x2222（不陈旧） |
| 14 | e2e_tc14_multi_sharer_wave | 1s | — | 三节点混合读写 wave（G_M↔G_S 切换） | 每 wave 两个 reader 看到 writer 的值 |
| 15 | e2e_tc15_credit_storm | 1s | — | Credit 压力下 RetryAck/PCrdGrant 恢复路径 | 3 节点 8 条 DSM line 收敛，无 deadlock |
| 16 | e2e_tc16_dual_upgrade_race | 1s | — | 双 sharer 并发升级竞争 | 3 节点一致收敛到 0xA0A0 或 0xB0B0 |
| 17 | e2e_tc17_writeback_dma | 1s | — | Writeback+DMA+remote read 重叠交互 | pre-DMA=0x12345678, post-DMA=0x87654321 |
| 18 | e2e_tc18_directory_fill_replay | 1s | — | Directory fill/replay 路径 | Node1+Node2 最后读=0x18181818 |
| 19 | e2e_tc19_directory_dirty_persist | 1s | — | Directory dirty 持久化 | Node2 读=0xABCD1234 |
| 20 | e2e_tc20_offload_smoke_a | 1s | — | Offload 冒烟测试 A | 所有读=0x20202020 |
| 21 | e2e_tc21_offload_smoke_b | 1s | — | Offload 冒烟测试 B | 所有读=0x21212121 |
| 22 | e2e_tc22_resident_capacity_pressure | 1s | — | ResidentDir 容量压力后数据完整性 | ≥9 个 READ_VAL 全部 MATCH |
| 23 | e2e_tc23_bloom_false_positive_fallback | 1s | — | Bloom filter 假阳性容忍 (miss→0, refill→MAGIC) | 首次=0, 末次=0x23ABCDEF |
| 24 | e2e_tc24_multinode_pressure_stress | 1s | — | 三节点并发压力 anchor 值全局一致 | 所有 anchor 值 MATCH, {0x24A00001, 0x24B00002, 0x24C00003} |
| 25 | e2e_tc25_invalidate_clear_cycle | 1s | — | 高频 ownership 切换无漂移 | 最终值=0x25000000\|31, 3 节点一致 |
| 26 | e2e_tc26_l3_eviction_writeback_chain | 1s | — | L3 eviction 压力下目标 line 保持 | Node1+Node2 读=0x26ABCDEF |
| 27 | e2e_tc27_epoch_wrap_stress | 1s | — | Epoch 回绕压力 | wraps≥1, 最终值 3 节点一致 |
| 28 | e2e_tc28_backstore_metadata_consistency | 1s | — | Backstore 驱逐后数据+元数据镜像一致 | 0x28AA55AA + 0x2855AA55 都存在, [META_REL] ok=1 |
| 29 | e2e_tc29_local_upgrade_from_exclusive | 1s | — | Local Exclusive→Modified upgrade | Node1 读=0x2900F111, [TC29_UPG] 标记 |
| 30 | e2e_tc30_stale_clear_tombstone | 1s | — | Stale clear/tombstone replay 序列 | Node2 读=0x30BB0022, stale/replay=1 标记 |
| 31 | e2e_tc31_multicpu_concurrent_isolation | 1s | — | 多 CPU per-node 并发隔离 | ≥12 个验证读全部 MATCH |
| 32 | e2e_tc32_cross_socket_read_miss | **2s** | — | 跨 socket read miss | Node0 读=0x3200BB02, [TC32_LAT] 标记 |
| 33 | e2e_tc33_cross_socket_writeback | **2s** | — | 跨 socket dirty writeback 路由 | Node0 读=0x33DD0011, homeSocket=0 标记 |
| 34 | e2e_tc34_dual_socket_pingpong | **2s** | — | 双 socket pingpong | 两个 socket plane 值各自收敛 (0xCAFE0000, 0xBEEF0000) |
| 35 | e2e_tc35_numa_latency_stress | **2s** | — | NUMA 混合压力前向推进 | done-lines {0x35DD0000, 0x35DD0001, 0x35DD0002} |
| 36 | e2e_tc36_owner_upgrade_ge_window | 1s | — | G_E 窗口内 owner upgrade（不应触发 recall/inv） | 3 节点收敛到 0x3600BB22, 无 recall/inv 标记 |
| 37 | e2e_tc37_owner_upgrade_gm_window | 1s | — | G_M 窗口内 owner second write | 3 节点收敛到 0x3700D222, gm_before_second=1 |
| 38 | e2e_tc38_stale_clear_tombstone_storm | 1s | — | Stale clear 风暴 | stale_clear_seen≥2, replay_ok=1, 最终值一致 |
| 39 | e2e_tc39_dual_socket_same_pa_interference | **2s** | — | 双 socket 同一 PA 干扰 | 3 节点在家 socket 1 收敛到 0x3900B022 |
| 40 | e2e_tc40_recall_timeout_retry | 1s | — | RECALL 超时重试 | retry_count≥1, 最终值收敛到 0x4000D1A1 |
| 41 | e2e_tc41_recall_invalidate_overlap | 1s | — | RECALL 和 Invalidate 重叠序列化 | recall+invalidate 标记均出现, 收敛到 0x4100B222 |
| 42 | e2e_tc42_exact_epoch_wrap_24b | 1s | — | 24-bit epoch 精确回绕边界 (0xffffff→0) | wrap 标记出现, 收敛到 0x42A00001 |
| 43 | e2e_tc43_rapid_owner_cycle | 1s | — | 快速 owner 循环 (64 轮) | 最终值=0x43000000\|63, 3 节点一致 |
| 44 | e2e_tc44_full_protocol_matrix | 1s | — | 全协议路径矩阵 (upgrade/writeback_fill/recall/invalidate_unique) | 4 路径标记 + 每个节点 4 个 line 终值正确 |
| 45 | e2e_tc45_fill_conflict_bloom_sat | 1s | — | Fill 冲突 + Bloom 饱和 | sat_count≥1, fill_conflict=1, 最终值收敛 |
| 46 | e2e_tc46_multibeat_recall | 1s | — | 多 beat RECALL (64 字节完整性) | 64 byte-check 全部 MATCH, checked=64 mismatches=0 |
| 47 | e2e_tc47_drop_clear | 1s | **fault:** ClearReq dup | Clear 丢包后 tombstone 恢复 | Node1+Node2 读=0x47AA0011, [UBFAULT] 证据 |
| 48 | e2e_tc48_dup_inv_ack | 1s | **fault:** InvalidateAck dup (Node2) | 重复 InvalidateAck 幂等处理 | 3 节点读=0x48BB0022 |
| 49 | e2e_tc49_reorder_acks | 1s | **fault:** InvalidateAck dup (Node1) | 重复 InvalidateAck 重新排序收敛 | 3 节点读=0x49CC0033 |
| 50 | e2e_tc50_producer_consumer_ring | 1s | — | 3 节点 producer-consumer 环形 token | 3 个 READ_VAL 全部 MATCH |
| 51 | e2e_tc51_bank_ledger | 1s | — | Bank ledger 不变量（并发转账总额不变） | total=4×100000 |
| 52 | e2e_tc52_mapreduce_scatter_gather | 1s | — | Scatter-map-gather 结果一致性 | 4 个 Node2 gather 读全部 MATCH |
| 53 | e2e_tc53_cache_contention_storm | 1s | — | Cache 争用风暴公平性 | 每个节点完成全部 round, fairness counter 匹配 |
| 54 | e2e_tc54_numa_tiled_matmul | 1s | — | 2×2 tiled matmul (NUMA-aware) | 4 个 Node2 读全部 MATCH |
| 63 | e2e_tc63_recall_orphan_timer_cleanup | 1s | — | RECALL 孤儿 timer 清理 | [TC63_ORPHAN] cleanup=timer, Node0 最终读 MATCH |
| 64 | e2e_tc64_recall_done_orphan_lazy_cleanup | 1s | — | RECALL.DONE 孤儿 lazy 清理 (新请求触发) | [TC64_ORPHAN] cleanup=lazy, 最终读 MATCH |
| 80 | e2e_tc80_cross_node_latency | 1s | — | 跨节点延迟采样 | [LATENCY] 标记, 最终读 MATCH |
| 81 | e2e_tc81_cross_socket_latency | **2s** | — | 跨 socket 延迟采样 | same/cross [LATENCY] 统计, 最终读 MATCH |
| 82 | e2e_tc82_8node_ring_latency | 8n1s | — | 8 节点环形延迟采样 | [LATENCY] 标记, 最终读 MATCH |
| 84/85 | e2e_tc84_cacheline_capacity | 1s | — | Cacheline 容量测试 | [CAPACITY] 标记 (84/85 共用 workload) |
| 90 | e2e_tc90_8node_all_to_all | **8n1s** | — | 8 节点 all-to-all DSM 读写 | 64 个 READ_VAL (8×8) 全部 MATCH |
| 91 | e2e_tc91_8node_hotspot | **8n1s** | — | 8 节点热点争用 | 所有 READ_VAL MATCH |
| 92 | e2e_tc92_8node_butterfly | **8n1s** | — | 8 节点 butterfly 数据迁移 | 所有 READ_VAL MATCH |
| 93 | e2e_tc93_8node_pairwise_pingpong | **8n1s** | — | 8 节点成对 pingpong | 所有 READ_VAL MATCH |
| 94 | e2e_tc94_8node_barrier_stress | **8n1s** | — | 8 节点 barrier 压力 (8 轮) | 所有 READ_VAL MATCH |
| 95 | e2e_tc95_8n2s_barrier_stress | **8n2s** | — | 8n2s per-socket barrier 压力 | 所有 READ_VAL MATCH |
| 96 | e2e_tc96_8n2s_cross_socket_read | **8n2s** | — | 8n2s 跨 socket read | 16 个 READ_VAL 全部 MATCH |
| 97 | e2e_tc97_8n2s_pingpong | **8n2s** | — | 8n2s ownership ping-pong | 所有 READ_VAL MATCH |
| 98 | e2e_tc98_8n2s_hotspot | **8n2s** | `TIMEOUT_SEC_TC98≥1500` | 8n2s 同一 PA 热点写争用 | 16 个 done marker MATCH |
| 99 | e2e_tc99_8n2s_perplane_slots | **8n2s** | — | 8n2s per-plane slot 争用 (TC98 温和版) | 16 个 done marker MATCH |
| 100 | e2e_tc100_8n2s_batch_rs | **8n2s** | — | 8n2s batch RS (同一 cache line 16 读) | 1 个最终 MATCH, BATCH-RS grants 统计 |
| 101 | e2e_tc101_8n2s_direct_fwd | **8n2s** | — | 8n2s direct-forward 链路 | 16 个 READ_VAL MATCH, C4-FORWARD 事件统计 |
| 102 | e2e_tc102_writeback_data_persist | 1s | — | Writeback dirty data 持久化 (eviction 后跨节点读) | ≥1 个 READ_VAL MATCH |
| 110 | e2e_tc110_drop_clear | 1s | **fault:** ClearReq drop | 丢 ClearReq fault injection (3.1 P1) | 3 节点收敛, [UBFAULT] 证据 |
| 111 | e2e_tc111_silent_upgrade_drop | 1s | **fault:** OuterUpgradeReq drop; `EP_SILENT_UPGRADE` 控制 | Silent upgrade fault 免疫 (3.2 P1) | 全部读 MATCH, Post-upgrade 收敛到 0x1110BBB2 |
| 112 | e2e_tc112_tbe_interference | 1s | — | TBE 干扰 (3.6 P1) | 跨节点收敛, ≥3 个 [TC112_LOCAL] 标记 |
| 113 | e2e_tc113_silent_upgrade_micro | 1s | — | Silent upgrade micro-bench (4.5 P2) | 1000 迭代后 =0x113003E7 |
| 114 | e2e_tc114_silent_upgrade_minimal | 1s | — | 最小化 silent upgrade (R_M→M) | 读=0x1140B000 |
| 115 | e2e_tc115_cross_cpu_silent_upgrade | 1s | — | 跨 CPU silent upgrade (不同 L2 cluster) | 读=0x1150B000 |
| 116 | e2e_tc116_directory_eviction_stress | 1s | — | ResidentDir DRAM offload/onload 压力 | Node1 首末值正确, dir_evictions 统计 |
| 117 | e2e_tc117_clear_reorder | 1s | **fault:** ClearReq reorder | ClearReq 乱序恢复 (3.3 P1) | 全部读 MATCH, [UBFAULT] 证据 |
| 118 | e2e_tc118_mixed_fault | 1s | **fault:** drop+delay ClearReq (同一 home) | 混合故障双 Clear (3.3 P1) | 全部读 MATCH, [UBFAULT] 证据 |
| 119 | e2e_tc119_triple_fault | 1s | **fault:** drop+dup+delay ClearReq (同一 home) | 三合一故障 (3.3 P1) | ≥3 读全部 MATCH, [UBFAULT] 证据 |

---

## 拓扑说明

| 拓扑 | 命令 | 节点数 | sockets/node | NMOD |
|------|------|--------|-------------|------|
| 3n1s | `--1s` | 3 | 1 | 3 |
| 3n2s | `--2s` | 3 | 2 | 6 |
| 8n1s | `--8n1s` | 8 | 1 | 8 |
| 8n2s | `--8n2s` | 8 | 2 | 16 |

---

## 环境变量与参数汇总

### 延迟参数
| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `EP_SYNC_INTERVAL_PS` | 2500 | PDES 同步间隔 (picoseconds) |
| `EP_LINK_LATENCY_PS` | 2500 | ZMQ link latency (picoseconds) |

### 重试参数
| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `EP_RETRY_CYCLES` | 1600000 | EPSNFController retry cycle |
| `EPRN_COMPACK_RETRY_CYCLES` | 100000 | EPRNF CompAck retry cycle |
| `EPRN_WAKEUP_RETRY_CYCLES` | 1000000 | EPRNF wakeup retry cycle |
| `UB_WAIT_CAP` | 2000000 | UBAdapter spin-wait cap |

### 功能开关
| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `EP_SILENT_UPGRADE` | — | 0=禁用(发 OuterUpgradeReq), 1=启用(local upgrade 无声) |
| `UBCC_BLOOM_BYTES` | — | Bloom filter 字节大小 |
| `UBCC_BATCH_RS` | — | Batch RS 开关 |

### Timeout 设置
| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `TIMEOUT_SEC` | 600 | 全局超时 (秒) |
| `TIMEOUT_SEC_TC98` | 1500 | TC98 专用超时 |

---

## 详细测试用例说明

### TC1: 单节点本地 DSM 读写冒烟测试
- **Workload**: `e2e_tc1_dsm_local.c`
- **拓扑**: 3n1s，仅 Node0 参与
- **目的**: 验证最短路径 CHI→EP→UBCC 的 DSM 读写
- **工作流**:
  1. Node0 写入 0xCAFE 到 DSM home=0, offset=0
  2. Node0 读取同一个 DSM 地址
  3. 验证读回值等于写入值
- **通过标准**: 1 个 `[READ_VAL]`, actual=0xCAFE, verdict=MATCH
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC2: 跨节点远程读
- **Workload**: `e2e_tc2_remote_read.c`
- **拓扑**: 3n1s, Node0(写入)+Node1(读取), Node2 闲置
- **目的**: 验证跨节点远程 DSM 读路径（ReadShared → HN-F → CompData）
- **工作流**:
  1. Node0 写入 0x11223344 到 DSM_1 (home=Node1)
  2. Node1 读取 DSM_1，验证值
- **通过标准**: Node1 READ_VAL=0x11223344, MATCH
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC3: Ping-Pong owner 转移
- **Workload**: `e2e_tc3_pingpong.c`
- **拓扑**: 3n1s, Node0+Node1 参与
- **目的**: 验证双节点 Ping-Pong owner 转移 (SC→UD→SC→UD 状态切换)
- **工作流**:
  1. Round 1: Node0 写 0xA, Node1 读
  2. Round 2: Node1 写 0xB, Node0 读
  3. Round 3: Node0 写 0xC, Node1 读
- **通过标准**: 3 个 `[READ_VAL]`, 全部 MATCH
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC4: 三节点环形 owner 转移
- **Workload**: `e2e_tc4_three_node_ring.c`
- **拓扑**: 3n1s, 所有节点参与
- **目的**: 验证三节点环形 owner 转移 (node0→node1→node2→node0)
- **工作流**:
  1. Node0 写 0x1, Node0 读 (expect 0x1)
  2. Node1 写 0x2, Node1 读 (expect 0x2)
  3. Node2 写 0x3, Node2 读 (expect 0x3)
  4. Node0 读 (expect 0x3, 最新值)
- **通过标准**: 4 个 READ_VAL, 验证每个节点读到预期值
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC5: 单写者一致性
- **Workload**: `e2e_tc5_single_writer.c`
- **拓扑**: 3n1s, 全部节点参与
- **目的**: 验证三个节点并发写同一 DSM line 时协议能正确串行化，防止数据丢失
- **工作流**:
  1. 三节点各自写不同的值到 DSM_1
  2. 所有节点读取最终值
  3. 所有节点必须读到**同一个**合法值
- **通过标准**: 3 个节点最终值完全一致且为合法值之一 (0xAA000001/0xBB000002/0xCC000003)
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC6: 多 sharer 读一致性
- **Workload**: `e2e_tc6_multi_sharer.c`
- **拓扑**: 3n1s, Node0 写 Node1/Node2 读
- **目的**: 验证 shared state propagation 和多 sharer 共享后写入 invalidate
- **工作流**:
  1. Node0 写 0xDEADBEEF 到 DSM_2
  2. Node1 和 Node2 同时读 DSM_2
- **通过标准**: Node1+Node2 都读=0xDEADBEEF
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC7: Writeback/Eviction 路径
- **Workload**: `e2e_tc7_writeback_evict.c`
- **拓扑**: 3n1s
- **目的**: 验证 CPU cache line eviction 后 dirty data 通过 writeback 正确保存到 HN-F/L3
- **工作流**:
  1. Node0 写 0x55667788 到 DSM_1
  2. Node0 用大量写入 flood L1D/L2 cache 强制 evict DSM line
  3. Node1 读 DSM_1，必须看到原始数据
- **通过标准**: 1 个 READ_VAL=0x55667788
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC8: Upgrade Invalidate
- **Workload**: `e2e_tc8_upgrade_invalidate.c`
- **拓扑**: 3n1s
- **目的**: 验证 Shared→Exclusive Upgrade 时正确 invalidate 其他 sharer (GlobalInvalidate 路径)
- **工作流**:
  1. Phase 1: Node0 写 0xAAA 到 DSM_2
  2. Phase 2: Node1+Node2 shared-read DSM_2 (成为 sharer)
  3. Phase 3: Node0 写 0xBBB (触发 upgrade, 应 invalidate Node1/Node2)
  4. Phase 4: Node1 读 — 必须见 0xBBB 而非 stale 0xAAA
- **通过标准**: Node1 最终读=0xBBB
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC9: 非 DSM 地址负面测试 ⚠️
- **Workload**: `e2e_tc9_non_dsm_negative.c`
- **拓扑**: 3n1s
- **目的**: 验证访问不存在的 DSM 节点 (node3) 时系统正确拒绝
- **工作流**:
  1. Node0 尝试访问 `dsm_addr(3, 0)`（超出映射范围的非法 VA）
  2. 预期系统产生 page fault 或 [FATAL] 标记
- **通过标准**: [FATAL] 标记 **或** page-fault/panic，**且无** READ_VAL 输出
- **Timeout**: 600s (默认)。多进程模式下 run_multi.sh 会自动检测 node0 的 crash evidence (Aborted/SIGSEGV) 并判定 PASS
- **状态**: **XFAIL** (Expected crash — 正常的预期崩溃)
- **关联**: TC9 在 gem5 的 config 代码中有特殊处理：检测到 tc_id==9 时提前打印 "TC9 PASSED: expected fatal" 并退出

### TC10: 并发读写原子性
- **Workload**: `e2e_tc10_concurrent_atomic.c`
- **拓扑**: 3n1s, Node0 写 Node1 读
- **目的**: 验证并发读写时不会出现 torn read (部分写入的中间值)
- **工作流**:
  1. Node0 循环写递增值 (0xA0000000 + i)
  2. Node1 并发读同一 line
  3. 验证每个读取值都是完整写入的合法值
- **通过标准**: 所有读取值在 [0xA0000000, 0xA0000000+ROUNDS) 范围内，且不为 0
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC11: Local Write Upgrade 通知链
- **Workload**: `e2e_tc_local_upgrade.c`
- **拓扑**: 3n1s, A=0 B=1 C=2
- **目的**: 验证共享读后的 local write upgrade 端到端 snoop 通知链
- **协议路径**: ReadShared (FirstMiss shared_hint) → REG_DONE → CleanUnique → SnpCleanInvalid → UBCC updateOwner
- **工作流**:
  1. Phase 1: Node B (node1) 读 DSM_C (home=node2), 获得 shared copy
  2. Phase 2: Node B 写 DSM_C (触发 local upgrade)
  3. Phase 3: Node C (home) 读 DSM_C 验证
  4. Phase 4: Node A 读 DSM_C 验证
- **通过标准**: Node C 和 Node A 都读到升级后的 0xCA01
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC12: Barrier 正确性
- **Workload**: `e2e_tc12_sync_barrier.c`
- **拓扑**: 3n1s
- **目的**: 验证 sync_wait barrier 的全局同步和分段排序
- **工作流**: 10 iterations × 3 segments，每个 segment 内所有节点到达后才继续
- **通过标准**:
  - 每个 (iter,seg) 有来自所有节点的 SYNC 标记
  - 每个节点标记序列严格单调递增
  - 总共 3×10×3 = 90 个 SYNC 标记
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC13: Release/Acquire 内存序
- **Workload**: `e2e_tc13_remote_release_acquire.c`
- **拓扑**: 3n1s, home=Node2
- **目的**: 验证跨节点的 release/acquire 内存序 (DATA+FLAG 两条线)
- **工作流**:
  1. Node0 写 DATA=0x1111, FLAG=0 (home=2)
  2. Node1 读 DATA 获取 shared copy
  3. Node0: dmb sy, 写 DATA=0x2222, dmb sy, 写 FLAG=1
  4. Node1: 读 FLAG (见 1), dmb sy, 读 DATA
- **通过标准**: FLAG=1 后 DATA 必须是 0x2222 (不是 stale 0x1111)
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC14: 多 sharer 混合读写 wave
- **Workload**: `e2e_tc14_multi_sharer_wave.c`
- **拓扑**: 3n1s, home=Node2
- **目的**: 验证 G_M→G_S→G_M→G_S 多次切换 (owner 在多个 node 间轮转)
- **工作流**: 3 轮 wave — 每轮一个 writer 写, 另两个 reader 读
  - Wave 1: Node0 写 0x1001 → Node1+Node2 读
  - Wave 2: Node1 写 0x2002 → Node0+Node2 读
  - Wave 3: Node2 写 0x3003 → Node0+Node1 读
- **通过标准**: 6 个 [PHASE_RD] 值精确匹配期望值
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC15: Credit 压力恢复
- **Workload**: `e2e_tc15_credit_storm.c`
- **拓扑**: 3n1s
- **目的**: 压力测试 RetryAck/PCrdGrant/credit 恢复路径。每个节点顺序暴打 home node2 的 8 条 DSM line
- **工作流**:
  1. 三个节点依次对 home=2 的 8 条 line 做 load/store 循环 (200 轮)
  2. 每个节点结束后其他节点读取并验证 8 条 line 收敛
- **通过标准**: 8 条 line 在所有节点上收敛值一致, 无 deadlock/panic
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC16: 双 sharer 升级竞争
- **Workload**: `e2e_tc16_dual_upgrade_race.c`
- **拓扑**: 3n1s
- **目的**: 验证两个 sharer 同时竞相升级同一 shared line 时协议正确串行化
- **工作流**:
  1. Node2 初始化 DSM_2=0x55
  2. Node0+Node1 shared-read → 成为 sharer
  3. Barrier 释放时 Node0 存 0xA0A0, Node1 存 0xB0B0 (并发)
  4. 所有节点读回
- **通过标准**: 3 节点一致收敛到 0xA0A0 或 0xB0B0
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC17: Writeback + DMA + Remote Read 交互
- **Workload**: `e2e_tc17_writeback_dma.c`
- **拓扑**: 3n1s
- **目的**: 验证 writeback、DMA 和 remote read 三种操作正确交互
- **工作流**:
  1. Pre-DMA: 多个节点读 0x12345678
  2. DMA 写入 0x87654321
  3. Post-DMA: 多个节点读，验证新值
- **通过标准**: pre-DMA=0x12345678, post-DMA=0x87654321
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC18: Directory Fill/Replay
- **Workload**: `e2e_tc18_directory_fill_replay.c`
- **拓扑**: 3n1s
- **目的**: 验证 directory fill 和 replay 路径的数据正确性
- **通过标准**: Node1+Node2 末次读=0x18181818
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC19: Directory Dirty 持久化
- **Workload**: `e2e_tc19_directory_dirty_persist.c`
- **拓扑**: 3n1s
- **目的**: 验证 directory dirty 状态数据持久化
- **通过标准**: Node2 读=0xABCD1234
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC20/TC21: Offload 冒烟测试
- **Workload**: `e2e_tc20_offload_smoke_a.c` / `e2e_tc21_offload_smoke_b.c`
- **拓扑**: 3n1s
- **目的**: 验证 offload 基础功能
- **通过标准**: TC20: 所有读=0x20202020; TC21: 所有读=0x21212121
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC22: ResidentDir 容量压力
- **Workload**: `e2e_tc22_resident_capacity_pressure.c`
- **拓扑**: 3n1s
- **目的**: 验证 ResidentDir 容量极限压力后数据完整性
- **通过标准**: ≥9 个 READ_VAL 全部 MATCH
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC23: Bloom Filter 假阳性容错
- **Workload**: `e2e_tc23_bloom_false_positive_fallback.c`
- **拓扑**: 3n1s
- **目的**: 验证 BF 假阳性容忍 — miss 返回 0, 随后 refill 命中 MAGIC 值
- **通过标准**: Node0 首次读=0, 末次读=0x23ABCDEF
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC24: 多节点并发压力
- **Workload**: `e2e_tc24_multinode_pressure_stress.c`
- **拓扑**: 3n1s
- **目的**: 三节点并发压力下 anchor 值全局一致
- **通过标准**: 所有 anchor 值 MATCH, {0x24A00001, 0x24B00002, 0x24C00003} 均出现
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC25: Invalidate/Clear 循环
- **Workload**: `e2e_tc25_invalidate_clear_cycle.c`
- **拓扑**: 3n1s
- **目的**: 高频 ownership 切换 (invalidate + clear 循环) 后无数据漂移
- **通过标准**: 最终值=0x25000000|31, 3 节点一致
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC26: L3 Eviction Writeback Chain
- **Workload**: `e2e_tc26_l3_eviction_writeback_chain.c`
- **拓扑**: 3n1s
- **目的**: L3 eviction 压力下目标 cache line 保持不被破坏
- **通过标准**: Node1+Node2 读=0x26ABCDEF
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC27: Epoch 回绕压力
- **Workload**: `e2e_tc27_epoch_wrap_stress.c`
- **拓扑**: 3n1s
- **目的**: 高频数据 churn 触发 epoch wrap, 验证 wrap 后数据一致性
- **通过标准**: wraps≥1, 最终值 3 节点一致
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC28: Backstore 元数据一致性
- **Workload**: `e2e_tc28_backstore_metadata_consistency.c`
- **拓扑**: 3n1s
- **目的**: ResidentDir 驱逐到 backstore 后数据与元数据镜像一致
- **通过标准**: 0x28AA55AA 和 0x2855AA55 都存在, [META_REL] ok=1
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC29: Local E->M Upgrade
- **Workload**: `e2e_tc29_local_upgrade_from_exclusive.c`
- **拓扑**: 3n1s
- **目的**: 验证本地 Exclusive→Modified upgrade 模式
- **通过标准**: Node1 读=0x2900F111, [TC29_UPG] 标记
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC30: Stale Clear / Tombstone Replay
- **Workload**: `e2e_tc30_stale_clear_tombstone.c`
- **拓扑**: 3n1s
- **目的**: 验证 stale clear 和 tombstone replay 序列
- **通过标准**: Node2 读=0x30BB0022, stale=1 + replay=1 标记
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC31: 多 CPU per-node 并发隔离
- **Workload**: `e2e_tc31_multicpu_concurrent_isolation.c`
- **拓扑**: 3n1s
- **目的**: 验证同一 node 的多个 CPU 并发访问 DSM 时 per-cache-line 一致性
- **通过标准**: ≥12 个验证读全部 MATCH
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC32: 跨 Socket Read Miss
- **Workload**: `e2e_tc32_cross_socket_read_miss.c`
- **拓扑**: **3n2s** (dual-socket)
- **目的**: 验证跨 socket read miss 的数据路径正确性
- **通过标准**: Node0 读=0x3200BB02, [TC32_LAT] same/cross 延迟标记
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC33: 跨 Socket Writeback
- **Workload**: `e2e_tc33_cross_socket_writeback.c`
- **拓扑**: **3n2s** (dual-socket)
- **目的**: 验证跨 socket dirty writeback 正确到达 home socket
- **通过标准**: Node0 读=0x33DD0011, homeSocket=0 标记
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC34: 双 Socket Pingpong
- **Workload**: `e2e_tc34_dual_socket_pingpong.c`
- **拓扑**: **3n2s** (dual-socket)
- **目的**: 验证两个 socket plane 的 pingpong 各自收敛
- **通过标准**: Node2 读到 0xCAFE0000 (socket0) + 0xBEEF0000 (socket1)
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC35: NUMA 延迟压力
- **Workload**: `e2e_tc35_numa_latency_stress.c`
- **拓扑**: **3n2s** (dual-socket)
- **目的**: 验证 NUMA 混合压力下有前向推进
- **通过标准**: done-lines = {0x35DD0000, 0x35DD0001, 0x35DD0002}; 所有节点有 progress 标记
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC36: Owner Upgrade (G_E 窗口)
- **Workload**: `e2e_tc36_owner_upgrade_ge_window.c`
- **拓扑**: 3n1s
- **目的**: 验证 G_E 窗口内 owner upgrade 不应触发 recall/invalidate
- **通过标准**: [TC36_GE] ge=1+upg_owner=1 标记, **无** recall/inv 标记, 收敛到 0x3600BB22
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC37: Owner Upgrade (G_M 窗口)
- **Workload**: `e2e_tc37_owner_upgrade_gm_window.c`
- **拓扑**: 3n1s
- **目的**: 验证 G_M 窗口内 owner second write 合法收敛
- **通过标准**: [TC37_GM] gm_before_second=1, 收敛到 0x3700D222
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC38: Stale Clear 风暴
- **Workload**: `e2e_tc38_stale_clear_tombstone_storm.c`
- **拓扑**: 3n1s
- **目的**: 验证大量 stale clear/tombstone 风暴后最终值不变
- **通过标准**: stale_clear_seen≥2, replay_ok=1, 最终值 0x38CC0033
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC39: 双 Socket 同一 PA 干扰
- **Workload**: `e2e_tc39_dual_socket_same_pa_interference.c`
- **拓扑**: **3n2s** (dual-socket)
- **目的**: 验证双 socket 访问同一 PA 时 home socket 侧正确收敛
- **通过标准**: 3 节点在家 socket 1 收敛到 0x3900B022
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC40: RECALL 超时重试
- **Workload**: `e2e_tc40_recall_timeout_retry.c`
- **拓扑**: 3n1s
- **目的**: 验证 RECALL 超时后重试机制和最终 completion
- **通过标准**: [TC40_RECALL] retry_count≥1, Node2+Node0 最终读=0x4000D1A1
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC41: RECALL + Invalidate 重叠
- **Workload**: `e2e_tc41_recall_invalidate_overlap.c`
- **拓扑**: 3n1s
- **目的**: 验证 RECALL 和 Invalidate 重叠时的正确串行化
- **通过标准**: recall + invalidate 两个 [TC41_PHASE] 均出现, 收敛到 0x4100B222
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC42: 24-bit Epoch 精确回绕
- **Workload**: `e2e_tc42_exact_epoch_wrap_24b.c`
- **拓扑**: 3n1s
- **目的**: 验证 24-bit epoch 在 0xffffff → 0 边界上正确工作
- **通过标准**: [TC42_EPOCH] ffffff,0 标记, 收敛到 0x42A00001
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC43: 快速 Owner 循环
- **Workload**: `e2e_tc43_rapid_owner_cycle.c`
- **拓扑**: 3n1s
- **目的**: 验证 64 轮快速 owner 循环后 liveness 和最终收敛
- **通过标准**: ≥4 个 [TC43_ROUND] 标记, 3 节点一致收敛到 0x43000000|63
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC44: 全协议路径矩阵
- **Workload**: `e2e_tc44_full_protocol_matrix.c`
- **拓扑**: 3n1s, home=Node0
- **目的**: 验证 4 条核心协议路径的完整矩阵回归
  - **upgrade**: Shared → Exclusive/Modified 升级
  - **writeback_fill**: eviction → fill 重建
  - **recall**: Owner 被 recall 出让 line
  - **invalidate_unique**: invalidate 其他 sharer 后获 exclusive
- **工作流**: 4 条 DSM line (A/B/C/D offset spaced 64 bytes) 分别经历 init→shared expansion→upgrade/writeback/recall→invalidate→final read
- **通过标准**: 4 个 [TC44_PATH] 标记全部出现, 每个节点的 4 个终值正确
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC45: Fill 冲突 + Bloom 饱和
- **Workload**: `e2e_tc45_fill_conflict_bloom_sat.c`
- **拓扑**: 3n1s
- **目的**: 验证 fill 冲突和 bloom filter 饱和场景下的数据完整性
- **通过标准**: [TC45_STRESS] sat_count≥1 + fill_conflict=1, 最终值收敛
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC46: 多 Beat RECALL 完整性
- **Workload**: `e2e_tc46_multibeat_recall.c`
- **拓扑**: 3n1s
- **目的**: 验证 64-byte multi-beat RECALL 数据的逐字节完整性
- **工作流**: 写入 64 字节数据, 触发 RECALL, 逐字节验证
- **通过标准**: 64 个 [TC46_BYTE] 全部 MATCH, [TC46_SUMMARY] checked=64 mismatches=0
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC47: Fault Injection — Drop Clear ⚡
- **Workload**: `e2e_tc47_drop_clear.c`
- **拓扑**: 3n1s
- **Fault 规则**: `ClearReq:1:0:0:dup::1` (duplicate once)
- **目的**: 验证 ClearReq 被丢包/重复后 tombstone replay 机制能够恢复
- **工作流**:
  1. Node0 写 0x47AA0011 到 DSM home=0
  2. Node1 远程读 (触发 ClearReq — 可能被 fault 规则干扰)
  3. Node2 验证
- **通过标准**: Node1+Node2 读=0x47AA0011, [UBFAULT] fault 证据出现
- **Timeout**: 600s (默认)
- **状态**: PASS ✅
- **关联**: 与 TC110 对应不同的 fault 策略；此 TC 使用 `dup`, TC110 使用 `drop`

### TC48: Fault Injection — Duplicate InvalidateAck (Node2) ⚡
- **Workload**: `e2e_tc48_dup_inv_ack.c`
- **拓扑**: 3n1s
- **Fault 规则**: `InvalidateAck:2:0:0:dup::1`
- **目的**: 验证重复 InvalidateAck (来自 Node2) 的幂等处理 (dup to same tick, copies=2)
- **通过标准**: 3 节点最终读=0x48BB0022, [UBFAULT] 证据
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC49: Fault Injection — Duplicate InvalidateAck (Node1) ⚡
- **Workload**: `e2e_tc49_reorder_acks.c`
- **拓扑**: 3n1s
- **Fault 规则**: `InvalidateAck:1:0:0:dup::1`
- **目的**: 验证重复 InvalidateAck (来自 Node1) 的幂等处理。与 TC48 的差异在于 ack 来自不同 node，可能导致不同的重排序
- **通过标准**: 3 节点最终读=0x49CC0033, [UBFAULT] 证据
- **Timeout**: 600s (默认)
- **状态**: PASS ✅ (已知在多进程 split 模式下曾因 ack 去重竞争而 TIMEOUT，已修复)
- **关联 Issue**: `docs/issues/tc49_dup_ack_timeout.md`

### TC50: Producer-Consumer Ring
- **Workload**: `e2e_tc50_producer_consumer_ring.c`
- **拓扑**: 3n1s
- **目的**: 验证 3 节点环形 producer-consumer 模式的最终 token 一致性
- **通过标准**: 3 个节点各有 1 个 READ_VAL, 全部 MATCH
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC51: Bank Ledger 不变量
- **Workload**: `e2e_tc51_bank_ledger.c`
- **拓扑**: 3n1s
- **目的**: 验证并发转账场景下总账不变 (4 个账户各转入 100000)
- **通过标准**: Node0 读 total=4×100000=400000
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC52: Scatter-Map-Gather
- **Workload**: `e2e_tc52_mapreduce_scatter_gather.c`
- **拓扑**: 3n1s
- **目的**: 验证 scatter→map→gather 分布式计算模型的并发一致性
- **通过标准**: Node2 的 4 个 gather 读全部 MATCH
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC53: Cache 争用风暴公平性
- **Workload**: `e2e_tc53_cache_contention_storm.c`
- **拓扑**: 3n1s
- **目的**: 验证高争用下所有节点均能完成全部 rounds (无 starvation)
- **通过标准**: 每个节点达到全部 rounds, fairness counter 匹配预期
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC54: NUMA Tiled Matmul
- **Workload**: `e2e_tc54_numa_tiled_matmul.c`
- **拓扑**: 3n1s
- **目的**: 验证 NUMA-aware 2×2 tiled matmul 输出矩阵正确
- **通过标准**: 4 个 Node2 输出读全部 MATCH
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC63: RECALL 孤儿 Timer 清理
- **Workload**: `e2e_tc63_recall_orphan_timer_cleanup.c`
- **拓扑**: 3n1s
- **目的**: 验证 RECALL 发送后 owner 永不响应时 timer 清理机制能回收孤儿 RECALL
- **工作流**:
  1. Node1 写数据
  2. Node2 读 (触发 RECALL to Node1)
  3. Node1 主动放弃不响应 (owner 侧 abandon)
  4. Timer cleanup 回收后 Node0 再次访问
- **通过标准**: [TC63_ORPHAN] cleanup=timer, Node0 最终读 MATCH
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC64: RECALL.DONE 孤儿 Lazy 清理
- **Workload**: `e2e_tc64_recall_done_orphan_lazy_cleanup.c`
- **拓扑**: 3n1s
- **目的**: 验证新请求触发 lazy cleanup 移除 RECALL.DONE 孤儿 (新 requester 触发清理)
- **工作流**:
  1. Node2 请求 RECALL
  2. 在 RECALL.DONE 孤儿状态下新请求到达
  3. Lazy cleanup 触发清理后服务新请求
- **通过标准**: [TC64_ORPHAN] cleanup=lazy, Node2 先完成, Node0 最终读 MATCH
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC80: 跨节点延迟采样
- **Workload**: `e2e_tc80_cross_node_latency.c`
- **拓扑**: 3n1s
- **目的**: 采样跨节点延迟数据
- **通过标准**: [LATENCY] 标记, 最终读 MATCH
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC81: 跨 Socket 延迟采样
- **Workload**: `e2e_tc81_cross_socket_latency.c`
- **拓扑**: **3n2s**
- **目的**: 采样同 socket 和跨 socket 延迟
- **通过标准**: same/cross [LATENCY] 统计, 最终读 MATCH
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC82: 8 节点环形延迟
- **Workload**: `e2e_tc82_8node_ring_latency.c`
- **拓扑**: **8n1s**
- **目的**: 采样 8 节点环拓扑延迟
- **通过标准**: [LATENCY] 标记, 最终读 MATCH
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC84/TC85: Cacheline 容量测试
- **Workload**: `e2e_tc84_cacheline_capacity.c` (TC84 和 TC85 共用)
- **拓扑**: 3n1s
- **目的**: 测试 cacheline 容量压力
- **通过标准**: [CAPACITY] 标记存在
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC90: 8 节点 All-to-All 🔶
- **Workload**: `e2e_tc90_8node_all_to_all.c`
- **拓扑**: **8n1s**
- **目的**: 验证 8 节点全对等 DSM 读写冒烟 (每个节点读写其他所有节点的 DSM)
- **工作流**:
  1. 每个节点写 sentinel 到自己的 DSM segment
  2. barrier 后每个节点读所有 8 个 segment
- **通过标准**: 64 个 READ_VAL (8×8) 全部 MATCH
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC91: 8 节点热点争用
- **Workload**: `e2e_tc91_8node_hotspot.c`
- **拓扑**: **8n1s**
- **目的**: 验证 8 节点对同一 hotspot DSM line 争用下的正确性
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC92: 8 节点 Butterfly 数据迁移
- **Workload**: `e2e_tc92_8node_butterfly.c`
- **拓扑**: **8n1s**
- **目的**: 验证 butterfly 模式数据迁移路径
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC93: 8 节点成对 Pingpong
- **Workload**: `e2e_tc93_8node_pairwise_pingpong.c`
- **拓扑**: **8n1s**
- **目的**: 验证 8 节点两两成对 pingpong
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC94: 8 节点 Barrier 压力
- **Workload**: `e2e_tc94_8node_barrier_stress.c`
- **拓扑**: **8n1s**
- **目的**: 验证 8 节点 8 轮 barrier 压力下的同步正确性
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC95: 8n2s Barrier 压力
- **Workload**: `e2e_tc95_8n2s_barrier_stress.c`
- **拓扑**: **8n2s**
- **目的**: 验证 8 节点 dual-socket per-socket barrier 压力
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC96: 8n2s 跨 Socket Read
- **Workload**: `e2e_tc96_8n2s_cross_socket_read.c`
- **拓扑**: **8n2s**
- **目的**: 验证 8n2s 配置下跨 socket read 路径
- **通过标准**: 16 个 READ_VAL 全部 MATCH (16 socket-planes)
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC97: 8n2s Ownership Ping-Pong
- **Workload**: `e2e_tc97_8n2s_pingpong.c`
- **拓扑**: **8n2s**
- **目的**: 验证 8n2s 配置下 ownership ping-pong
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC98: 8n2s 同 PA 热点 🔴
- **Workload**: `e2e_tc98_8n2s_hotspot.c`
- **拓扑**: **8n2s**
- **目的**: 验证 16 个 socket-plane 对同一 PA 并发写时 UBCC 的正确串行化
- **配置**: `TIMEOUT_SEC_TC98≥1500` (极端争用，PDES 串行化开销大)
- **工作流**:
  - 16 个 requestor 轮流向同一 cache line 写 16 轮
  - 最后每个 socket 写 done marker
  - barrier 后 Node0 读取所有 done marker
- **通过标准**: 16 个 done marker MATCH
- **Timeout**: 1500s (默认 600s 不够)
- **已知问题**: 曾因 EP-RNF snoop 排队引发跨节点写-写竞争死锁 (死锁#2), 详见 `docs/issues/tc98_deadlock2_eprnf_snoop_conflict.md`
- **注意**: 此 TC 争用为 O(n²) 级别, 适合验证 directory 串行化正确性, 但不适合性能测试。TC99 为温和版。
- **状态**: PASS ✅ (已知死锁已修复)

### TC99: 8n2s Per-Plane Slot 争用
- **Workload**: `e2e_tc99_8n2s_perplane_slots.c`
- **拓扑**: **8n2s**
- **目的**: TC98 的温和变体 — 验证 per-plane slot 争用而无需同 PA 极端争用
- **通过标准**: 16 个 done marker MATCH
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC100: 8n2s Batch RS
- **Workload**: `e2e_tc100_8n2s_batch_rs.c`
- **拓扑**: **8n2s**
- **目的**: 验证 batch RS — 16 个读者对同一 cache line 发 ReadShared, 由 batch RS 合并响应
- **通过标准**: 1 个最终 MATCH, 统计 BATCH-RS grants
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC101: 8n2s Direct-Forward 链
- **Workload**: `e2e_tc101_8n2s_direct_fwd.c`
- **拓扑**: **8n2s**
- **目的**: 验证 direct-forward (C4) 链路 — 16 个 socket-plane 的直接转发
- **通过标准**: 16 个 READ_VAL MATCH, 统计 C4-FORWARD 事件
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC102: Writeback Dirty Data 持久化
- **Workload**: `e2e_tc102_writeback_data_persist.c`
- **拓扑**: 3n1s
- **目的**: 验证 eviction 后 dirty data 通过 writeback 持久化到 backstore, 跨节点读取可获取
- **通过标准**: ≥1 个 READ_VAL MATCH
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC110: Fault Injection — Drop ClearReq ⚡
- **Workload**: `e2e_tc110_drop_clear.c`
- **拓扑**: 3n1s
- **Fault 规则**: `ClearReq:1:1:0:drop::1` (drop once, 与 TC47 的 dup 策略不同)
- **目的**: 3.1 P1 — 验证 ClearReq 被完全丢弃 (drop) 后协议的 self-heal 能力
- **工作流**: 类似 TC5 三节点并发写模式，但加入了 ClearReq 丢包 fault
- **通过标准**: 3 节点收敛到同一个合法值 (0x11000001/0x11000002/0x11000003)，[UBFAULT] 证据
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC111: Silent Upgrade Fault 免疫 ⚡
- **Workload**: `e2e_tc111_silent_upgrade_drop.c`
- **拓扑**: 3n1s
- **Fault 规则**: `OuterUpgradeReq:1:1:0:drop::1`
- **环境变量**: `EP_SILENT_UPGRADE`
- **目的**: 3.2 P1 — 验证两种模式下的 fault 免疫
  - **EP_SILENT_UPGRADE=0** (disable): 升级需要 OuterUpgradeReq, fault 规则丢包 → 协议重试自愈。应出现 [UBFAULT]
  - **EP_SILENT_UPGRADE=1** (enable): 升级完全 silent (无跨节点消息), fault 规则永远不命中。零 fault surface
- **通过标准**: Post-upgrade 读收敛到 0x1110BBB2 (两种模式均正确)
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC112: TBE 干扰
- **Workload**: `e2e_tc112_tbe_interference.c`
- **拓扑**: 3n1s
- **目的**: 3.6 P1 — 验证 TBE 干扰下跨节点 DSM 写收敛且本地推进标记完整
- **通过标准**: ≥3 个 [TC112_LOCAL] 本地 progress 标记, 跨节点读 MATCH
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC113: Silent Upgrade Micro-Bench
- **Workload**: `e2e_tc113_silent_upgrade_micro.c`
- **拓扑**: 3n1s
- **目的**: 4.5 P2 — 验证 silent upgrade 在 1000 次迭代微测试下正确性
- **通过标准**: 最终值=0x113003E7 (ITERS=1000, last iter=999)
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC114: 最小化 Silent Upgrade
- **Workload**: `e2e_tc114_silent_upgrade_minimal.c`
- **拓扑**: 3n1s
- **目的**: 验证最小化 silent upgrade 路径 (R_M → M, 无其他节点参与)
- **通过标准**: 读=0x1140B000
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC115: 跨 CPU Silent Upgrade
- **Workload**: `e2e_tc115_cross_cpu_silent_upgrade.c`
- **拓扑**: 3n1s
- **目的**: 验证不同 L2 cluster 之间的跨 CPU silent upgrade
- **通过标准**: 读=0x1150B000, [TC115_CPU0] + [TC115_CPU2] 诊断标记
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC116: ResidentDir Eviction 压力
- **Workload**: `e2e_tc116_directory_eviction_stress.c`
- **拓扑**: 3n1s
- **目的**: 验证 ResidentDir DRAM offload/onload 压力下数据完整性
- **工作流**:
  1. Node0 连续 fill 大量线 (256 entries) 压满 ResidentDir
  2. 部分 entry 被 evict 到 backstore DRAM
  3. Node1 读回首/末 line 验证
- **通过标准**: Node1 首值=0x11600000 末值=0x116000FF; [ResidentDirStats] dir_evictions 统计
- **Timeout**: 600s (默认)
- **状态**: PASS ✅

### TC117: ClearReq 乱序故障
- **Workload**: `e2e_tc117_clear_reorder.c`
- **拓扑**: 3n1s
- **目的**: 测试 ClearReq 被 Reorder（延迟后到达）时协议的乱序恢复能力——首个 reorder fault 测试，覆盖之前仅有的 drop/dup 之外的 fault 类型
- **故障规则**: `tc117_reorder_clear:ClearReq:0:1:0:reorder:100000:1` — 将 node0→home1 的第一个 ClearReq 缓冲 100µs 后投递
- **通过标准**: 两条 DSM line 全部 MATCH; [UBFAULT] 证据
- **状态**: PASS ✅

### TC118: 混合故障 — Drop + Delay (同 home)
- **Workload**: `e2e_tc118_mixed_fault.c`
- **拓扑**: 3n1s
- **目的**: 测试 Drop + Delay 双故障同时作用于同一 UBCC 进程——epoch 单调性 + tombstone 重放下并发故障正确性
- **故障规则**: 分号分隔 `tc118_drop:ClearReq:0:1:0x10018011800:drop::1;tc118_delay:ClearReq:0:1:0x10018011900:delay:100000:1`
- **覆盖**: Drop→RECALL 恢复; Delay→epoch 提交顺序保护
- **通过标准**: 两条 DSM line 全部 MATCH; [UBFAULT] 证据
- **状态**: PASS ✅

### TC119: 三合一故障 — Drop + Dup + Delay (同 home)
- **Workload**: `e2e_tc119_triple_fault.c`
- **拓扑**: 3n1s
- **目的**: 测试 drop/duplicate/delay 三种故障同时作用于同一 UBCC——最严苛的多类型并发故障场景
- **故障规则**: 分号分隔三规则（三个不同 PA 匹配）`tc119_drop:ClearReq:0:1:...:drop::1;tc119_dup:ClearReq:0:1:...:dup::1;tc119_delay:ClearReq:0:1:...:delay:100000:1`
- **覆盖**: Drop→RECALL 恢复; Dup→tombstone 幂等拒绝; Delay→epoch 提交乱序保护
- **通过标准**: ≥3 读全部 MATCH; [UBFAULT] 证据
- **状态**: PASS ✅

---

## Fault Injection 策略汇总

| TC | Fault 规则 | 消息类型 | 策略 | 验证目标 |
|----|-----------|---------|------|---------|
| 47 | `tc47_dup_clear:ClearReq:1:0:0:dup::1` | ClearReq | duplicate (copies=2) | Tombstone replay 恢复 |
| 48 | `tc48_dup_inv_ack:InvalidateAck:2:0:0:dup::1` | InvalidateAck (Node2) | duplicate | 幂等 ack 处理 |
| 49 | `tc49_dup_inv_ack:InvalidateAck:1:0:0:dup::1` | InvalidateAck (Node1) | duplicate | 重排序 ack 收敛 |
| 110 | `tc110_drop_clear:ClearReq:1:1:0:drop::1` | ClearReq | drop (完全丢弃) | Self-heal 恢复 |
| 111 | `tc111_silent_upgrade_drop:UpgradeReq:1:1:0:drop::1` | UpgradeReq | drop | UpgradeReq 丢包 watchdog 恢复 |
| 117 | `tc117_reorder_clear:ClearReq:0:1:0:reorder:100000:1` | ClearReq | reorder (延迟投递) | 乱序恢复——首个 reorder 覆盖 |
| 118 | `tc118_drop:...:drop::1;tc118_delay:...:delay:100000:1` | ClearReq ×2 | drop+delay (双故障) | epoch 单调性 + tombstone |
| 119 | `tc119_drop/dup/delay:...` (三规则) | ClearReq ×3 | drop+dup+delay (三合一) | 最严苛多类型并发故障 |

**Fault 规则格式**: `name:msgType:srcNode:dstNode:plane:action:reserved:maxCount`

---

## 关联 Issue 文档

| Issue | 文件 | 关联 TC | 说明 |
|-------|------|---------|------|
| TC49 死锁 | `docs/issues/tc49_dup_ack_timeout.md` | 49 | 多进程 split 模式下 duplicate InvalidateAck 导致 timeout |
| TC98 死锁#2 | `docs/issues/tc98_deadlock2_eprnf_snoop_conflict.md` | 98 | EP-RNF snoop 排队引发跨节点写-写竞争死锁 |

---

## 相关设计文档

| 文档 | 路径 | 说明 |
|------|------|------|
| E2E 使用手册 | `docs/dev_manual/e2e_test_manual.md` | 运行、拓扑、参数配置 |
| 协议总览 | `docs/design/scheme_v4.md` | v4 协议设计 |
| RECALL 规范 | `docs/design/recall_spec_v4.md` | RECALL 协议详细规范 |
| 本地 DSM 路由 | `docs/design/local_dsm_routing_v4.md` | v4 本地 DSM 路由设计 |
| Gap 分析 | `docs/recovery/gap_analysis_and_fix_plan.md` | 协议鸿沟分析与修复 |
| SnpShared Fix | `docs/recovery/snp_shared_fix_plan.md` | SnpShared 修复方案 |
| 新测试用例设计 | `docs/recovery/new_testcase_design.md` | 测试用例设计文档 |
| TC45 协议原语 | `docs/recovery/tc45_protocol_primitives.md` | TC45 相关协议分析 |
