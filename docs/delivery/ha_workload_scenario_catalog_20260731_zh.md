# HA Workload 场景规范目录

> 日期：2026-07-31
> 适用范围：CC reference、bare-metal Arm HA target、FPGA/SoC HA 验收

## 1. 交付范围

本目录是 HA workload 的权威场景说明。交付场景分为三组：

| 组 | 场景 | 状态 | 交付内容 |
|---|---|---|---|
| A | HA01-HA12 / TC210-TC221 | 2N1S reference 已实现 | `e2e_ha_2n1s_core.c`、verifier、JSONL contract |
| B | TC142-TC147 | topology-portable 源码已实现 | 原 workload 源码、公共 helper、verifier、业务性能 contract |
| C | TC222-TC227 | `e2e_ha_cgroup_2n1s.c` | TC123/130/132/135/138/139 的独立 2N1S portable adaptation |

组 C 不能直接将原 `.c` 文件以 `--2n1s` 运行。原文件含三参与者 barrier 或 node2
角色；本文保留其架构语义并定义两节点角色合并方法。交付报告必须标明
原 TC123/130/132/135/138/139 仍保持 3N1S 语义；2N1S 实现使用新 TC222-TC227，
不会悄悄修改原 verifier。manifest 使用 `implementation_status=implemented_2n1s`。

以下 TC 不纳入当前 2N1S HA 交付：TC120-TC122、TC124-TC129、TC131、TC133、
TC134、TC136、TC137、TC140、TC141。主要原因是被推荐场景覆盖、依赖第三个独立
角色、依赖固定 8-node/2-socket 拓扑、依赖 cache geometry/私有 marker，或属于 CC
内部回归而不是外部架构 contract。

## 2. 统一术语

| 术语 | 定义 |
|---|---|
| root operation | workload 中一次 load、带完成语义的 store，或明确定义的固定 batch |
| seed | 建立初始数据和 coherence 状态，不进入正式 service timer |
| warm/share | 由后续访问者建立 cache copy、shared 状态或 owner 状态 |
| pressure/admission | 加入新 line，并在目录满载时处理 victim 的过程 |
| first revisit | pressure 后原持有者对 hot line 的第一次重新访问 |
| handoff | ownership 从旧 owner 转交给另一个 writer |
| service | 只覆盖业务 load/store 和必要完成 barrier |
| end-to-end | 覆盖 pressure、跨 participant barrier 和业务 service 的完整固定工作量 |

跨平台正式计时使用 target-visible counter：

```text
T_root = counter(root_complete) - counter(root_issue)
ns/op = latency_ticks / operations * 1e9 / timer_frequency_hz
```

`EP-PERF kind=outer`、Global message 和 CHI event 只作 CC 诊断，不作为 HA/CC 正式
对比边界。

## 3. 统一平台契约

所有场景都遵循以下规则：

- 业务数据是 64-byte line spacing 上的对齐 32-bit load/store。
- `store_complete` 表示 architectural store 后执行目标平台等价的 completion barrier；
  CC reference 为 `str; dsb sy`。
- timed region 内不能输出、动态分配、做 JSON serialization 或调用 UART/syscall。
- 两 participant barrier 必须带 generation；失败 fatal，不能 timeout 后继续。
- 每个 node 只有一个 workload primary 输出正式 sample。
- 所有 readback 必须匹配；缺失 participant、sample 或 validation 均为失败。
- correctness verifier 不以某个 policy 必须更快作为通过条件。

Global/CHI 的典型映射如下：

| workload 行为 | Global/UB 抽象 | 常见 CHI 行为 |
|---|---|---|
| remote/shared load miss | GlobalReadShared，ReadReq/ReadResp | ReadShared，CompData_SC |
| store 获取 ownership | GlobalReadUnique(write intent) | ReadUnique，CompData_UC |
| 原 owner 为新 reader 提供数据 | RecallReq(read)/RecallResp | owner ReadShared，M/E→S |
| dirty owner 转交新 writer | RecallReq(unique)/RecallResp(data) | owner ReadUnique，M→I |
| writer 排除其他 sharer | InvalidateReq/InvalidateAck | target CleanUnique，S→I |
| metadata onload | MetaRNFLineReadReq/Resp | metadata ReadOnce |
| grant commit | ClearReq/ClearResp | Global transaction 尾部 commit |

具体 HA 实现可以使用不同消息名，但必须保留等价的可见性、ownership 和 completion
语义。

## 4. A 组：HA01-HA12

### 4.1 HA01 / TC210：Local Reuse

| 项目 | 规范 |
|---|---|
| 目的 | 验证 Home0 line 在 node0 本地写入后可被同一 participant 正确复用，并验证最小 timer/JSONL 路径 |
| 角色 | node0=home/requester；node1=barrier participant |
| 操作 | node0 store `0x101`；barrier；node0 timed load 同一 line |
| 计时 | `local_reuse`，1 operation |
| 预期协议 | 允许 local cache hit；若平台仍经过 HA home lookup，也必须保持相同值和完成语义 |
| 验收 | node0 读到 `0x101`；两 node validation=0；sample 非零且 timer frequency 有效 |

### 4.2 HA02 / TC211：Remote Read

| 项目 | 规范 |
|---|---|
| 目的 | 最小跨 node 数据可见性和 remote read latency |
| 角色 | node0=Home0 seed writer；node1=remote requester |
| 操作 | node0 store `0x202`；barrier；node1 timed load |
| 计时 | `remote_read`，1 operation |
| 预期协议 | node1 miss 通常映射为 GlobalReadShared；若 node0 持有 dirty data，允许 read recall |
| 验收 | node1 读到 `0x202`；无 zero-data fallback；双 validation=0 |

### 4.3 HA03 / TC212：Ownership Handoff

| 项目 | 规范 |
|---|---|
| 目的 | 验证 writer ownership 从 node0 转交 node1，并由 node0 读回新值 |
| 角色 | node0=initial writer/home/final reader；node1=new writer |
| 操作 | node0 store `0x303`；barrier；node1 timed store `0x304`；barrier；node0 timed readback |
| 计时 | `ownership_write`、`ownership_readback`，各 1 operation |
| 预期协议 | node1 获取 unique/modified 权限；必要时 recall node0；node0 readback 可能反向 recall node1 |
| 验收 | 最终值 `0x304`；两个 phase 完整；双 validation=0 |

### 4.4 HA04 / TC213：Shared To Writer

| 项目 | 规范 |
|---|---|
| 目的 | 验证 line 被双方读取成为 shared 后，node1 转为 writer 的 invalidation/upgrade 路径 |
| 角色 | node0=seed/home/sharer；node1=sharer/new writer |
| 操作 | node0 store `0x404`；双方 load；barrier；node1 timed store `0x405`；node0 readback |
| 计时 | 每 node `shared_read`；node1 `shared_to_writer`；node0 `writer_readback` |
| 预期协议 | shared writer 通常需要 Upgrade 或 GlobalReadUnique；其他 sharer 被 invalidate 后才能 commit |
| 验收 | node0 最终读到 `0x405`；不能在 invalidate 未完成时提前报告 store complete |

### 4.5 HA05 / TC215：Clean Shared Victim Revisit

| 项目 | 规范 |
|---|---|
| 目的 | 观察 clean shared copy 在 capacity pressure 后是被销毁还是被保留 |
| 角色 | node0=seed/home/pressure writer；node1=original sharer/revisitor |
| 操作 | node0 seed hot line `0x505`；node1 warm read；node0 写 640 pressure lines；node1 连续 load hot line 64 次 |
| 计时 | `first_revisit`，64 operations |
| 预期协议 | destructive policy 下 first load 可能重新 GlobalReadShared；retained-copy policy 下可全部 local hit |
| 验收 | 64 次后最终 load 仍为 `0x505`；不能以 local hit 缺少 Outer trace 判失败 |

### 4.6 HA06 / TC216：Dirty Owner Capacity Lifecycle

| 项目 | 规范 |
|---|---|
| 目的 | 观察 remote dirty owner 在 capacity admission 后的保留、回收和再次访问 |
| 角色 | node1=dirty owner/revisitor；node0=Home0/pressure writer |
| 操作 | node1 store `0x606`；node0 timed 写 640 pressure lines；node1 timed load hot line 64 次 |
| 计时 | `eviction_admission`=640 operations；`first_revisit`=64 operations |
| 预期协议 | destructive policy 可在 admission 中 recall dirty data；spill policy 可保留 owner copy并只转移 metadata |
| 验收 | 最终值 `0x606`；dirty data 不能由 metadata store 或零值伪造 |

### 4.7 HA07 / TC214：Producer Consumer Stream

| 项目 | 规范 |
|---|---|
| 目的 | 验证 16 条 record 的生产、跨 node 消费和 barrier generation |
| 角色 | node0=producer；node1=consumer |
| 操作 | 对每个 record：node0 store；barrier；node1 load/verify；barrier |
| 计时 | `producer`、`consumer`，各 16 operations，包含该角色参与的同步等待 |
| 预期协议 | 每条 record 可能产生 remote shared read；实现可缓存，但不能跨 generation 读取旧值 |
| 验收 | 16 条值全部匹配；32 次配对 barrier 无丢代或死锁 |

### 4.8 HA08 / TC218：Barrier And Sequence-lock Handoff

| 项目 | 规范 |
|---|---|
| 目的 | 分离测量 barrier 成本和单 line 双向 ownership ping-pong |
| 角色 | node0/node1 对称 participant |
| 操作 | 先执行 16 次 barrier；再执行 16 轮奇数值/偶数值交替 store、load 和双 barrier |
| 计时 | 每 node `barrier`=16；`seq_lock_handoff`=16 rounds |
| 预期协议 | hot line ownership 在两 node 间重复转移；每轮必须观察前一方新值 |
| 验收 | 每轮奇偶值严格递增；无 barrier timeout；双 validation=0 |

### 4.9 HA09 / TC219：Concurrent Local And Remote Pressure

| 项目 | 规范 |
|---|---|
| 目的 | 验证 node0 hot-local 更新与 node1 remote pressure 并发时的数据稳定性 |
| 角色 | node0=hot updater；node1=remote pressure writer |
| 操作 | node0 对 16 条 hot line 做 64 次 store；node1 同时写 16 条另一 offset pressure line；barrier 后 node0 验证 hot[0] |
| 计时 | `local_under_pressure`=64；`remote_pressure`=16 |
| 预期协议 | 两地址集不得错误别名；并发 directory activity 不能破坏 node0 最终值 |
| 验收 | node0 hot[0] 为 `0x9000`；两个 timer 和双 validation 完整 |

### 4.10 HA10 / TC217：Read-mostly Catalog

| 项目 | 规范 |
|---|---|
| 目的 | 实际 read-mostly catalog workload，在分批 pressure 下测量 hot lookup/update |
| 角色 | node0=seed/pressure/final verifier；node1=catalog worker |
| 工作集 | 16 catalog lines；640 pressure lines；8 batches |
| 操作 | 每 batch node0 写 80 pressure lines；node1 timed 执行 14 次 skewed read + 2 次 completed update |
| 计时 | 8 条 `catalog_batch`，每条 16 operations；1 条 `catalog_useful_throughput`=128 operations |
| 预期协议 | retained copy 可减少后续 batch 的 Global request；update key 仍需正确 ownership |
| 验收 | 8 个 iteration 齐全；两个 update key 为最后 batch 值；双 validation=0 |

### 4.11 HA11 / TC220：Exact-150 Clean Capacity

| 项目 | 规范 |
|---|---|
| 目的 | 在精确 150% footprint 下测量 clean/shared admission 和 first revisit |
| 角色 | node0=seed/home/pressure；node1=original sharer/revisitor |
| 工作集 | Resident capacity=512；64 hot + 704 pressure = 768 unique lines，49,152 bytes |
| 操作 | node0 seed 64 hot；node1 share 64 hot；node0 timed admission 704 pressure；node1 timed revisit 64 hot |
| 计时 | `clean_capacity_admission`=704；`clean_first_revisit`=64 |
| 预期协议 | naive 可 invalidate clean sharer；spill 可保留 copy，因此 revisit 可能没有任何 Outer transaction |
| 验收 | 64 final reads 全 MATCH；capacity record 精确为 ratio=1.500000 |

### 4.12 HA12 / TC221：Exact-150 Dirty Capacity And Handoff

| 项目 | 规范 |
|---|---|
| 目的 | 在精确 150% footprint 下同时测量 dirty admission、owner revisit 和 ownership handoff |
| 角色 | node1=initial dirty owner/revisitor/final verifier；node0=Home0/pressure/new writer |
| 工作集 | 64 hot + 704 pressure = 768 unique lines |
| 操作 | node1 dirty-seed 64 hot；node0 timed admission 704；node1 revisit 前 32；node0 completed-store 后 32；node1 验证全部 64 |
| 计时 | `dirty_capacity_admission`=704；`dirty_first_revisit`=32；`dirty_handoff`=32 |
| 预期协议 | naive 可能 admission 时提前 recall dirty data；spill 保留 owner copy，但 handoff 时可能 metadata fill + owner ReadUnique recall |
| 验收 | 前 32 保留原值、后 32 为 node0 新值；64 reads MATCH；ratio=1.500000 |

## 5. B 组：直接交付的 TC142-TC147

六个 workload 使用相同公共规则：

- `plane = node * sockets_per_node + socket`。
- 每个 plane 有独立 64 KiB hot shard，全部映射到 Home0。
- 32 batches，每 batch 全局加入 24 条 pressure line，总计 768 条。
- 在 2N1S 下有 plane0/node0 和 plane1/node1 两个 participant。
- seed 按 plane 串行，业务 batch 并发。
- 每个 TC 同时输出 service、end-to-end 和 32 个 batch latency samples。

当前源码和 verifier 对 2N1S 是结构可移植的，但截至本文档日期，正式记录仅覆盖
3N1S、3N2S、8N1S、8N2S 的 spill 矩阵。将其交付为 2N1S workload 前，应至少完成
一次目标编译和一次双 participant correctness smoke；在此之前不能把四拓扑结果
表述成已有 2N1S 性能结果。

### 5.1 TC142：OLTP Buffer Pool

| 项目 | 规范 |
|---|---|
| 业务模型 | 32-page hot buffer-pool shard，读多写少的事务 batch |
| 每 batch | 24 global pressure；28 deterministic reads；4 updates；共 32 useful ops |
| 总工作量 | 每 plane 1,024 useful ops，32 batch samples |
| 热度 | 大部分读落在前 8 page，少量读覆盖全部 32 page |
| 计时 | `db_oltp_service`、`db_oltp_end_to_end`、`db_oltp_batch_32ops` |
| Global/CHI | cold/reacquire load→ReadShared；update→ReadUnique/Upgrade；retained copy 可减少 pressure 后 reacquire |
| 验收 | warm read + 4 final update reads MATCH；每 plane participant、timer 和 sample 数精确 |

### 5.2 TC143：B-tree Traversal

| 项目 | 规范 |
|---|---|
| 业务模型 | root、8 internal、64 leaf、64 record 的四层 deterministic traversal |
| 每 batch | 16 transactions；每 transaction 访问 root/internal/leaf/record；每第 4 个 transaction 更新 record |
| 总工作量 | 每 plane 2,048 memory ops，32 个 64-op batch |
| 热度 | root 极热，internal 高复用，leaf/record 分散 |
| 计时 | `db_btree_service`、`db_btree_end_to_end`、`db_btree_batch_64ops` |
| Global/CHI | shared metadata 层强调 retained read copy；record update 强调 shared-to-writer/owner reuse |
| 验收 | warm record + 4 final updated record reads MATCH；操作数和 sample 数精确 |

### 5.3 TC144：WAL And Checkpoint

| 项目 | 规范 |
|---|---|
| 业务模型 | 64 data pages + 128 WAL lines；每次 update 先写 WAL，再写 data page |
| 每 batch | 24 pressure；16 updates；每 update 两个 stores，共 32 operations |
| 总工作量 | 每 plane 1,024 stores，32 batch samples |
| 顺序要求 | `WAL store` 必须在对应 `data store` 之前；batch 尾 completion barrier 不得删除 |
| 计时 | `db_wal_service`、`db_wal_end_to_end`、`db_wal_batch_32ops` |
| Global/CHI | 两类 dirty line 产生 ownership acquisition/reuse；capacity policy 可能改变 writeback/recall 时点 |
| 验收 | 最后 batch 的 8 个 WAL/data pair 共 16 reads 全部匹配同一 version |

### 5.4 TC145：FaaS Warm Invocation

| 项目 | 规范 |
|---|---|
| 业务模型 | 64 runtime lines、64 tenant lines、8 result lines |
| 每 batch | 24 package pressure；48 skewed runtime reads；8 tenant reads；8 result writes |
| 总工作量 | 每 plane 2,048 operations，32 invocation samples |
| 热度 | runtime 前 16 line 为主要 hot set；tenant 访问更分散 |
| 计时 | `faas_service`、`faas_end_to_end`、`faas_batch_64ops` |
| Global/CHI | runtime/tenant 主要为 ReadShared；result line 为重复 owner update |
| 验收 | warm runtime + 8 最终 result reads MATCH；每 plane 9 reads |

### 5.5 TC146：Graph Frontier

| 项目 | 规范 |
|---|---|
| 业务模型 | 64 frontier、64 adjacency、64 property lines |
| 每 batch | 16 vertices；每 vertex 访问 frontier、两条 adjacency、一个 property；25% property update |
| 总工作量 | 每 plane 2,048 operations，32 graph iteration samples |
| 热度 | adjacency 有重复邻接访问；property 混合 shared read 和 sparse write |
| 计时 | `graph_service`、`graph_end_to_end`、`graph_batch_64ops` |
| Global/CHI | frontier/adjacency 体现 shared reuse；property update 体现 writer acquisition |
| 验收 | warm adjacency + 4 final property reads MATCH |

### 5.6 TC147：Feature Store

| 项目 | 规范 |
|---|---|
| 业务模型 | 128 embedding lines + 8 accumulator lines |
| 每 batch | 24 pressure；56 skewed embedding lookups；8 accumulator updates |
| 总工作量 | 每 plane 2,048 operations，32 batch samples |
| 热度 | 7/8 lookup 映射到前 32 embedding，1/8 覆盖完整 128-line table |
| 计时 | `feature_service`、`feature_end_to_end`、`feature_batch_64ops` |
| Global/CHI | embedding 主要为 ReadShared/local hit；accumulator 为重复 unique ownership |
| 验收 | warm embedding + 8 最终 accumulator reads MATCH |

## 6. C 组：2N1S Portable Implementation

本组保留原 TC 的核心架构语义，但重新定义角色以消除 node2。除非另有说明，Home0
位于 node0，barrier mask 为 `0x3`。实现时应分配新的 HA scenario ID 或独立 target
binary，不能悄悄修改原 TC 的 3N1S verifier。

实现映射与 2026-07-31 optimized 验证状态：

| 新 TC | 来源语义 | 2N1S 状态 |
|---:|---|---|
| 222 | TC123-HA | 默认规模 smoke PASS |
| 223 | TC130-HA | 默认规模 smoke PASS |
| 224 | TC132-HA | 512 active + 4,096 pressure qualification PASS；原 8,192 + 65,536 profile 在 600 秒无进展时 FAIL |
| 225 | TC135-HA | 默认规模 smoke PASS |
| 226 | TC138-HA | 默认规模 smoke PASS |
| 227 | TC139-HA | 默认规模 smoke PASS |

PASS 日志位于 `logs/cgroup_2n1s_smoke_20260731/`、
`logs/cgroup_2n1s_qualification_20260731/` 和
`logs/cgroup_2n1s_final_20260731/`，不进入交付 commit。

### 6.1 TC123-HA：Shared To Writer Batch

| 项目 | 规范 |
|---|---|
| 来源 | `e2e_tc123_perf_shared_upgrade.c`；原源码固定 3N1S |
| 角色适配 | node0=seed/home/final verifier；node1=initial sharer/new writer |
| 工作集 | 16 hot lines；96 pressure lines；更新 hot[0,4,8,12] |
| 操作 | node0 seed；node1 share；node0 pressure；node1 对 4 条 line 做 completed stores；node0 readback |
| 计时 | 必须新增 `shared_to_writer_store`，4 个逐 store samples 或 4-op aggregate；初始 shared read 可另报 |
| Global/CHI | writer 若仍是 sharer：UpgradeReq + invalidate node0 + CleanUnique；若 copy 已失效：GlobalReadUnique |
| 验收 | 4 final values MATCH；不能只计 initial read 而不计核心 writer transition |

### 6.2 TC130-HA：Overflow Hot Reuse

| 项目 | 规范 |
|---|---|
| 来源 | `e2e_tc130_directory_overflow_benchmark.c`；原 node2 仅参与 barrier |
| 角色适配 | node0=seed/home/pressure；node1=warm/reuse reader |
| 工作集 | 24 hot；192 pressure；4 revisit rounds |
| 操作 | seed→share→pressure→node1 4×24 loads |
| 计时 | `post_pressure_hot_reuse`=96 operations；建议另输出首轮 24-sample distribution |
| 地址规则 | target 不要求复制 CC 的 4-set×2-way hash；必须保证 pressure working set 足以超过被测 tracking capacity，并在 manifest 报告 ratio |
| 验收 | 第一轮 24 reads MATCH；96-op timer 完整；双 validation=0 |

### 6.3 TC132-HA：Dirty Checkpoint Recovery

| 项目 | 规范 |
|---|---|
| 来源 | `e2e_tc132_dirty_checkpoint_stream.c`；原 node2 是 recovery reader |
| 角色适配 | node1=dirty checkpoint writer；node0=Home0/pressure/recovery reader |
| 默认规模 | 8,192 active dirty lines；65,536 pressure lines；可按 target capacity 参数化，但两端必须相同 |
| 操作 | node1 写 active set；node0 写 pressure stream；node0 timed 读取全部 active set |
| 计时 | `post_pressure_checkpoint_recover`=active lines；必须同时报告 end-to-end pressure+recover |
| Global/CHI | naive 可在 pressure 中提前 recall dirty owner；spill 可在 recovery 时 metadata fill + owner recall |
| 验收 | 至少每 512 条抽样且首末 line 必查；所有抽样值 MATCH；禁止只验证一条 line |

### 6.4 TC135-HA：Preserved Sharer First Revisit

| 项目 | 规范 |
|---|---|
| 来源 | `e2e_tc135_preserved_sharer_revisit.c`；原 node2 仅参与 barrier |
| 角色适配 | node0=seed/home/pressure；node1=original sharer/revisitor |
| 工作集 | 24 hot；192 pressure |
| 操作 | node0 completed seed；node1 share 24；node0 completed pressure；node1 逐 line first load |
| 计时 | `preserved_sharer_first_load`，24 个独立 serialized samples |
| Global/CHI | retained copy 命中时无 Global request；destructive eviction 时重新 GlobalReadShared |
| 验收 | seed 后 24 reads + revisit 后 24 reads 共 48 MATCH；min≤P50≤P95≤P99≤max |

### 6.5 TC138-HA：Dirty Owner Handoff

| 项目 | 规范 |
|---|---|
| 来源 | `e2e_tc138_dirty_handoff_store.c`；原 node2 是 new writer |
| 角色适配 | node1=initial dirty owner；node0=Home0/pressure/new writer；node1=final verifier |
| 工作集 | 24 hot；192 pressure |
| 操作 | node1 completed dirty seed；node0 completed pressure；node0 对 24 hot 做 timed completed stores；node1 readback |
| 计时 | `dirty_owner_handoff_store`，24 个独立 store-complete samples |
| Global/CHI | retained owner 时 node0 GlobalReadUnique，可能 H64 fill，随后 node1 ReadUnique recall；destructive policy 可能已在 pressure 中回收数据 |
| 验收 | node1 读到全部 24 final values；不得把 pressure 阶段成本从 end-to-end 报告中隐藏 |

### 6.6 TC139-HA：Mixed Batch Throughput

| 项目 | 规范 |
|---|---|
| 来源 | `e2e_tc139_mixed_batch_throughput.c`；原 node2 是 final verifier |
| 角色适配 | node0=seed/home/pressure/final verifier；node1=shared reader/odd-line owner/batch worker |
| 工作集 | 16 hot；192 pressure；16 batches |
| 每 batch | even line 8 loads + odd line 8 stores；batch 尾 completion barrier；16 operations |
| 计时 | `mixed_batch_16ops`=16 samples；`mixed_batch_throughput`=256 operations |
| Global/CHI | even lines 测 shared reuse；odd lines 测 retained owner write reuse；policy 差异会改变 Outer request 数量 |
| 验收 | node1 本地验证 8 条 odd line 为 batch15 值并向 node0 交接 checksum；node0 验证 summary；16 samples、256-op timer 完整 |

## 7. 交付优先级

推荐目标方按以下顺序 bring-up：

1. HA01-HA04：基础可见性、ownership、shared-to-writer。
2. HA07-HA09：barrier、stream、并发压力。
3. HA11/HA12：精确 150% clean/dirty capacity。
4. HA10：实际 read-mostly catalog。
5. TC135-HA、TC138-HA：机制级 retained-copy 与 dirty handoff。
6. TC142、TC144、TC147：OLTP、WAL、feature-store 三个首要业务 workload。
7. TC143、TC145、TC146、TC123-HA、TC130-HA、TC132-HA、TC139-HA：扩展覆盖。

## 8. 每场景交付结果

每个 scenario/profile/run 至少归档：

- manifest：scenario、implementation status、topology、seed、working set、capacity ratio、
  iterations、timer frequency、binary hash。
- phase samples：原始 ticks、operations、node、iteration。
- latency distribution：samples、min、mean、P50、P95、P99、max。
- correctness：每个规定 readback、双 participant validation、fatal/timeout 状态。
- 若 HA 可提供内部 trace：root ID、data source、child request count、抽象 Global/CHI
  operation；不要求披露私有模块名。
- 至少 3 个独立 run ID，推荐 5 个，并报告 run-level mean、stdev、CV。
