# E2E TC 矩阵执行权威目录

> 状态：当前源码基线（authoritative execution catalog）
> 基准日期：2026-08-05
> 支持 TC 数：**146**
> 适用入口：`tests/e2e/run_multi.sh` 及本文列出的现行矩阵 runner

## 1. 范围、状态与事实源

本文把“源码现在实际会做什么”与历史报告、建议超时、旧 runner 行为分开记录。
发生冲突时，按以下优先级解释执行语义：

1. `tests/e2e/run_multi.sh`：拓扑约束、编译、启动顺序、参数注入、超时、进程回收和最终判定。
2. `tests/e2e/test_e2e.py`：TC 注册表、workload 映射、gem5 参数解析和 verifier 实现。
3. `tests/e2e/verify.py`：聚合输入、附加协议证据和最终 sentinel。
4. `configs/topo_*.json`：模块命令模板、节点/路数和进程布局。
5. `scripts/gen_topo.py`：networksim full-mesh 链路及实际延迟默认值。
6. `scripts/run_*matrix*`、`tests/e2e/run_*`：批处理矩阵、profile、压力、并发和外层超时。
7. 已归档测量文档：仅作为 O（observed）证据；不能覆盖当前源码。

支持 ID 范围恰为：`1-54,63-64,80-82,84-85,90-102,110-159,200-203,210-227`。
合计为 `54+2+3+2+13+50+4+18=146`。

不支持的空洞为：`55-62,65-79,83,86-89,103-109,160-199,204-209`，以及
`<1`、`>227`。这些 ID 不在 `TESTCASES`/`VERIFIERS` 的共同有效集合中，不应通过
“相邻 TC 应该也能跑”来推断支持。

## 2. 拓扑 selector、plane 与进程数

`run_multi.sh` 读取固定 JSON；`NMOD = num_nodes * num_sockets`，每个 plane 对应
一个 UBIO，global plane id 为 `node * num_sockets + socket`。总受管主进程数为
`N gem5 + N*K UBIO + 1 networksim`。

| selector | JSON | 节点×socket | gem5 | UBIO/NMOD | networksim | 合计 |
|---|---|---:|---:|---:|---:|---:|
| `--1s` | `topo_1s.json` | 3×1 | 3 | 3 | 1 | 7 |
| `--1s-tinydir` | `topo_1s_tinydir.json` | 3×1 | 3 | 3 | 1 | 7 |
| `--2s` | `topo_2s.json` | 3×2 | 3 | 6 | 1 | 10 |
| `--8n1s` | `topo_8n1s.json` | 8×1 | 8 | 8 | 1 | 17 |
| `--8n2s` | `topo_8n2s.json` | 8×2 | 8 | 16 | 1 | 25 |
| `--2n1s` | `topo_2n1s.json` | 2×1 | 2 | 2 | 1 | 5 |

当前 E2E **没有 `barrier_manager` 进程**。barrier 由 gem5 guest/adapter 与 UBIO
携带、转发和完成；不要再按旧架构额外启动 barrier manager。

源码强制拓扑如下：`32-35,39,81 -> 2s`；`82,90-94,133 -> 8n1s`；
`95-101,134 -> 8n2s`；`135-141 -> 1s`；`210-227 -> 2n1s`。
其余 TC 没有 runner 级硬约束，目录中的 canonical topology 是推荐和 workload
语义基线。portable TC142-147 可由专用矩阵以 2N1S、3N1S、3N2S、8N1S、8N2S
编译运行。

## 3. 单 case 的实际启动契约

### 3.1 公共环境、目录和产物

| 项目 | 实际值/默认值 |
|---|---|
| 根目录 | `ROOT_DIR=/mnt/data2/cgc/cc-ep`（脚本按自身位置计算） |
| 二进制 | `gem5/build/ARM/gem5.opt`、`build/bin/ubio`、`build/bin/networksim` |
| run id | `E2E_RUN_ID`，缺省为时间戳加 runner PID |
| run 私有目录 | `build/runs/$E2E_RUN_ID` |
| workload | `build/runs/$E2E_RUN_ID/workload.elf`，每 TC 重新编译 |
| generated topo | `build/runs/$E2E_RUN_ID/topo.json` |
| IPC | `build/runs/$E2E_RUN_ID/ipc`；导出 `UBCC_IPC_DIR` |
| 日志根 | `LOG_BASE`；缺省 `logs/<timestamp>_<topo>_<run-id>` |
| case gem5 out | `build/runs/$E2E_RUN_ID/tc<TC>/m5out/node<N>` |
| simout | 上述 node 目录中的 `simout_n<N>`，并复制为 `LOG_BASE/simout_tc<TC>_node<N>.log` |
| verify | `LOG_BASE/verify_tc<TC>.log` |
| child status | `LOG_BASE/child_status_tc<TC>/*.exit` |
| trace chain | PASS 后尽力写 `LOG_BASE/trace_chains_tc<TC>.json` |

公共默认环境：`TIMEOUT_SEC=600`，`EP_SYNC_INTERVAL_PS=2500`，
`EP_LINK_LATENCY_PS=2500`，`UBCC_METADATA_SIZE=134217728`，
`EP_TRACE_PERF=sample`，`EP_TRACE_PERF_FIRST_N=500`，`EP_TRACE_PERF_MAX=2000`，
`EP_TRACE_PERF_EVERY=0`，`EP_PORT_HWM=8192`，`EP_NSIM_MAX_PENDING=65536`。
supervisor 缺省关闭；启用后默认 interval 30 秒、日志上限 20 GiB、磁盘余量
50 GiB、无进展 600 秒。

`UBCC_METADATA_SIZE` 当前不是完全有效的两侧 single source of truth。runner 会把它作为
`--metadata-dram-bytes` 传给 UBIO，也会把 `--ubcc_metadata_size` 追加到 gem5 命令；但
`test_e2e.py` 没有注册后一个参数，`parse_known_args()` 后也没有把它写入手工构造的
`options`。因此 gem5 当前仍由配置层回退到 128 MiB。默认值恰好一致；设置非默认值时，
UBIO 会变化而 gem5 不会同步变化，必须先修 parser 才能安全使用。

### 3.2 不可更改的公共顺序

1. 清理本 runner 记录的上一 TC PID，不使用名字匹配的 `pkill`。
2. 以 `NUM_NODES`、`NUM_SOCKETS`、`WORKLOAD_OUT` 调用
   `scripts/compile_workload.sh <TC>`，生成 run-private ELF。
3. 调用 `scripts/gen_topo.py --nodes N --sockets K --out topo.json`。
4. 清空并重建本 run 的 IPC 目录。
5. 启动 networksim，记录真实 child PID，固定等待 1 秒。
6. 启动每个 node 的 gem5；逐 node 等待 stdout 中出现 K 个
   `STEP5.*Port enabled`，每 node 最多 300 秒。
7. gem5 全部 bind 后，启动 N×K 个 UBIO；故障规则和 TC/config 参数在此注入。
8. 可选启动 memory monitor 和 supervisor。
9. 等待所有 gem5 完成，受 CASE timeout 和可选 progress watchdog 约束。
10. 等 UBIO 最多 15 秒自行退出，再 TERM networksim；收集全部 exit 文件。
11. 聚合 simout 与 UBIO stdout/stderr，运行独立 `verify.py`。
12. **只认 verify 日志最后一行**精确等于 `>>> TC<TC> PASSED <<<`；这是当前
    runner 的 exact final sentinel。TC9 是唯一可由预期 page fault 提前判 PASS 的例外。

### 3.3 有效启动模板

networksim 实际不是 JSON 中的单参数模板，而是 runner 直接执行：

```bash
build/bin/networksim "$RUN_DIR/topo.json" "$NUM_NODES" "$NUM_SOCKETS" \
  >"$LOG_BASE/nsim_tc${TC}.log" 2>&1
```

gem5 每 node 的公共有效模板如下；profile 和实验参数附加在末尾：

```bash
gem5/build/ARM/gem5.opt --outdir="$RUN_DIR/tc${TC}/m5out/node${N}" \
  tests/e2e/test_e2e.py --node-id="$N" --num-nodes="$NUM_NODES" \
  --num-sockets="$NUM_SOCKETS" --workload="$RUN_DIR/workload.elf" \
  [profile gem5 args] [EP_GEM5_OPTS] \
  --ubcc_metadata_size="$UBCC_METADATA_SIZE" \
  >"$LOG_BASE/gem5_tc${TC}_node${N}/stdout.log" \
  2>"$LOG_BASE/gem5_tc${TC}_node${N}/stderr.log"
```

若设置 `GEM5_DEBUG_FLAGS`，runner 在 gem5 binary 后插入
`--debug-flags=... --debug-file=<gdir>/gem5_debug.log`，并可插入
`--debug-start=$GEM5_DEBUG_START`。

UBIO 每 plane 的公共有效模板：

```bash
build/bin/ubio --node="$N" --socket="$S" \
  --num-sockets="$NUM_SOCKETS" --num-nodes="$NUM_NODES" \
  [--fault-rules='<rules>'] [TC/config UBIO args] \
  --metadata-dram-bytes="$UBCC_METADATA_SIZE" \
  >"$LOG_BASE/ubio_n${N}_s${S}/stdout.log" \
  2>"$LOG_BASE/ubio_n${N}_s${S}/stderr.log"
```

`topo_1s_tinydir.json` 是例外：其 UBIO 模板硬编码
`--bloom-bytes=0 --sram-bytes=6144 --ways=1`，但遗漏
`{ubio_extra_args}`，因此 runner 计算出的 TC 参数和
`--metadata-dram-bytes=...` **不会进入实际命令**；详见一致性风险。

### 3.4 full-mesh 延迟

`gen_topo.py` 对全部 NMOD 两两连边。实际 argparse 默认值为：跨 node 同 socket
`410000 ps`，同 node 跨 socket `220000 ps`，跨 node 且跨 socket临时使用单跳相加
`630000 ps`。文件 docstring 仍写 `405000/25000 ps`，这是陈旧说明，不是有效值。

## 4. 超时术语、证据等级和推荐政策

| 术语 | 定义 |
|---|---|
| CASE / TIMEOUT | `TIMEOUT_SEC` 或 TC 专用 override；从等待 gem5 完成阶段累计，达到即杀本 TC。 |
| STALL | supervisor 的 `EP_SUPERVISOR_PROGRESS_STALL_SEC`；guest simout 字节或 UBIO protocol tick 均不前进才触发。旧 `PROGRESS_WATCHDOG_SEC` 看输出大小，通常保持 0。 |
| bind 300s | 每个 gem5 node 等 `STEP5 Port enabled` 的独立启动门限；不等同 CASE。 |
| outer margin | Docker/Python coordinator 的 `subprocess timeout`，通常为 CASE+120 或 CASE+300，用于兜住 runner/容器退出。 |

推荐来源：`S` 为当前 script 默认/硬编码；`O` 为归档 PASS、timeout 或明确运行记录；
`C` 为保守估算。`C` 绝不表示测得运行时间。

一般政策：普通 3n1s correctness 用 CASE 600-900、STALL 600；3n2s/8n1s correctness
用 CASE 900、STALL 600；普通 8n2s 用 CASE 1200、STALL 600；多 profile 小目录矩阵
用 CASE 3600、STALL 600；fault qualification 用 CASE 1200、STALL 600。容量矩阵应按
拓扑和已观察 PASS elapsed 分级，不应统一套 10800。慢例外包括 TC98 建议21600/1800，
TC134 naive 建议18000/1800，portable 512K 8n2s 建议21600/1800，full-scale TC224
建议28800/1800。
CASE 必须大于 STALL，外层至少 CASE+120，容器冷启动/8N 推荐 CASE+300。

推荐调用示例：

```bash
env E2E_RUN_ID=tc42_check LOG_BASE=logs/tc42_check \
  TIMEOUT_SEC=900 EP_SUPERVISOR=1 EP_SUPERVISOR_INTERVAL=60 \
  EP_SUPERVISOR_PROGRESS_STALL_SEC=600 EP_TRACE_PERF=off \
  bash tests/e2e/run_multi.sh --1s 42

env TIMEOUT_SEC=10800 EP_SUPERVISOR=1 EP_SUPERVISOR_INTERVAL=60 \
  EP_SUPERVISOR_PROGRESS_STALL_SEC=1800 \
  bash tests/e2e/run_multi.sh --8n1s 133
```

## 5. 可复用配置代码

| 代码 | UBIO 有效参数 | gem5/profile 行为 | 适用说明 |
|---|---|---|---|
| `default` | 仅公共 `--metadata-dram-bytes` | 无 TC 强制 flag；可由 `EP_GEM5_OPTS` 追加 | 大多数 correctness |
| `DIR116` | `--bloom-bytes=512 --sram-bytes=6144 --ways=2`，policy 可由 `UBCC_POLICY` 加入 | 默认 | TC116 |
| `PERF5000` | bloom 512、SRAM 5000、ways 2、set-bits 2；naive=`naive,batch0`，spill-noopt=`spill,batch0`，optimized=`spill,batch1` | naive/noopt=`silent0 direct0 batch0`；opt=`silent1 direct0 batch1` | TC120-121 |
| `PERF6144` | bloom 512、SRAM 6144、ways 2；三 profile 同上 | 同上 | TC122-123 |
| `PERF124` | naive/spill-noopt `--batch-rs=0`，optimized `--batch-rs=1` | direct-fwd 始终 0；其余按三 profile | TC124 |
| `SPILL1` | bloom 512、SRAM 6144、ways 1、policy spill；noopt batch0，opt batch1 | noopt/opt 对应；naive 不适用 | TC125-129 |
| `STRESS5000` | bloom=`UBCC_BLOOM_BYTES` 缺省512、SRAM5000、ways2、set-bits2；三 profile | TC130、135-139、141 强制 direct0；opt silent1/batch1 | benchmark/mechanism |
| `REALCAP` | TC131-134 的 policy 均由 `UBCC_POLICY` 独立选择；spill: bloom61440、SRAM524288、ways0、set-bits0；naive: bloom0；UBIO batch0 | gem5 profile 由 `EP_PERF_PROFILE` 独立选择：naive/noopt=`silent0 direct0 batch0`，optimized=`silent1 direct0 batch1` | TC131-134 |
| `TC140P` | 只切 batch0/batch1 | noopt 或 opt，direct0；目录本身不施压 | TC140 |
| `PORTABLE legacy` | bloom128、SRAM4352、ways1、set-bits0；naive/batch0、spill/batch0、spill/batch1 | noopt/opt 强制 direct0 | TC142-147 旧 tiny-directory stress |
| `PORTABLE_512K p150` | `PORTABLE_512K_DIR=1`；SRAM524288、ways0、set-bits0；naive bloom0，spill bloom61440；batch0/1 | 三 profile；`WORKLOAD_CFLAGS` 使总 footprint=98304 lines、naive capacity=65536、32 batches | P0 portable |
| `A3` | bloom0、SRAM64、ways1、set-bits0、naive、batch0 | no optimization | TC200，仅 naive |
| `A5` | bloom128、SRAM4352、ways1、set-bits3、spill、batch0 | noopt/opt 可跑，语义是 spill | TC201 |
| `C1/D1` | bloom128、SRAM4352、ways1、set-bits0、spill、batch0 | noopt/opt | TC202/203 |
| `HA2N` | bloom128、SRAM4352、ways1、set-bits0；`EP_PERF_PROFILE` 自动选择 UBIO naive/spill/batch | `run_multi.sh` 不为 TC210-227 自动设置 gem5 profile；必须由矩阵或操作者通过 `EP_GEM5_OPTS` 注入。HA formal runner 已显式注入 | TC210-227 |
| `ways98` | `--ways=1` | 默认或显式 profile | TC98 高争用 |

`UBCC_OPTS` 始终拼在 TC 参数之后，矩阵通常再传
`UBCC_OPTS=--dir-overflow-policy=<policy>`。这允许实验覆盖，但也可能造成重复参数；
归档时必须保存最终命令。`UBCC_POLICY` 与 `EP_PERF_PROFILE` 在 TC131-134 都是可分离轴。
矩阵中的三 profile 只是约定映射：naive→naive/noopt、spill-noopt→spill/noopt、
optimized→spill/optimized；源码没有强制两轴绑定。

## 6. 故障规则事实表

| TC | 当前实际 rule | verifier 要求/备注 |
|---:|---|---|
| 47 | `tc47_dup_clear:ClearReq:1:0:0:dup::1` | registry/workload 名称写 drop；实际是 **dup ClearReq**，verifier 文案兼容 dropped/duplicated。 |
| 48 | `tc48_dup_inv_ack:InvalidateAck:2:0:0:dup::1` | 值收敛且有 fault evidence。 |
| 49 | `tc49_dup_inv_ack:InvalidateAck:1:0:0:dup::1` | registry 名称写 reorder；实际是 **dup InvalidateAck**。 |
| 110 | `tc110_drop_clear:ClearReq:1:1:0:drop::1` | drop recovery。 |
| 111 | `tc111_silent_upgrade_drop:UpgradeReq:1:1:0:drop::1` | dropped UpgradeReq recovery。 |
| 117 | `tc117_reorder_clear:ClearReq:0:1:0:reorder:100000:1` | reorder evidence。 |
| 118 | 指定 PA 的一个 drop ClearReq + 一个 delay 100000 ps ClearReq | 两 line MATCH、有 fault evidence。 |
| 119 | 指定 PA 的 drop、dup、delay 100000 ps 各一条 ClearReq | 三 line MATCH、有 fault evidence。 |
| 148 | 32 条 ClearReq：drop/dup/delay20k/reorder100k 各8 | 32 reads MATCH，32 rule 各命中恰一次。 |
| 149 | 8 条 UpgradeReq drop | 规则各命中恰一次。 |
| 150 | node1/2 共16条 InvalidateAck dup | 同上。 |
| 151 | node1/2 共16条 InvalidateAck delay20k | 同上。 |
| 152 | node1/2 共16条 InvalidateAck reorder100k | 同上。 |
| 153 | 16条 RecallResp dup | 同上。 |
| 154 | 16条 RecallResp delay20k | 同上。 |
| 155 | 16条 RecallResp reorder100k | 同上。 |
| 156 | 16条 RecallResp drop | 同上。 |
| 157 | node1/2 共16条 InvalidateAck drop | 同上。 |
| 158 | 8条 UpgradeResp 1→0 drop | 同上。 |
| 159 | 8条 UpgradeAckNotify 1→0 drop | 同上。 |

## 7. 每 TC 执行目录（146 行）

表内 `CASE/STALL` 单位为秒。`script` 列仅列特殊默认；其余为 common 600。
`workload` 是 `TESTCASES` 映射的源文件 stem；“分支”表示同一源通过 TC ID 选场景。
pass 摘要以当前 verifier 为准。

### 7.1 基础、协议与故障 correctness

| TC | scenario/name | 类 | topo | workload/分支 | config | pass 摘要 | script | 推荐 CASE/STALL | 源 | 特殊说明 |
|---:|---|---|---|---|---|---|---|---|:---:|---|
| 1 | dsm local | correctness | 1s | `e2e_tc1_dsm_local` | default | 单读=CAFE | common | 900/600 | C | standalone |
| 2 | remote read | correctness | 1s | `e2e_tc2_remote_read` | default | node1=11223344 | common | 900/600 | C | legacy sweep 可改 eviction env |
| 3 | pingpong | correctness | 1s | `e2e_tc3_pingpong` | default | 3 reads 全 MATCH | common | 900/600 | C | — |
| 4 | three-node ring | correctness | 1s | `e2e_tc4_three_node_ring` | default | 各 node 值集合正确 | common | 900/600 | C | — |
| 5 | single writer convergence | correctness | 1s | `e2e_tc5_single_writer` | default | 三 node 收敛到合法值 | common | 900/600 | C | — |
| 6 | multi sharer | correctness | 1s | `e2e_tc6_multi_sharer` | default | node1/2=DEADBEEF | common | 900/600 | C | — |
| 7 | writeback evict | correctness | 1s | `e2e_tc7_writeback_evict` | default | 精确55667788 | common | 900/600 | C | — |
| 8 | upgrade invalidate | correctness | 1s | `e2e_tc8_upgrade_invalidate` | default | node1末读BBB | common | 900/600 | C | — |
| 9 | non-DSM rejection | negative | 1s | `e2e_tc9_non_dsm_negative` | default | 预期 page fault 且无 READ_VAL | common | 900/600 | C | 特殊 crash PASS 路径 |
| 10 | concurrent atomic | correctness | 1s | `e2e_tc10_concurrent_atomic` | default | 所有读在合法100轮值域且非0 | common | 900/600 | C | — |
| 11 | local upgrade | correctness | 1s | `e2e_tc_local_upgrade` | default | node2/0末读CA01 | common | 900/600 | C | — |
| 12 | sync barrier | correctness | 1s | `e2e_tc12_sync_barrier` | default | 全 participant×30 marker、单调 | common | 900/600 | C | barrier由gem5/UBIO |
| 13 | release/acquire | correctness | 1s | `e2e_tc13_remote_release_acquire` | default | FLAG后DATA=2222 | common | 900/600 | C | — |
| 14 | sharer waves | correctness | 1s | `e2e_tc14_multi_sharer_wave` | default | 3 wave 六读正确 | common | 900/600 | C | — |
| 15 | credit storm | correctness | 1s | `e2e_tc15_credit_storm` | default | 8 lines三节点收敛、无panic/deadlock | common | 1200/600 | C | retry证据 advisory |
| 16 | dual upgrade race | correctness | 1s | `e2e_tc16_dual_upgrade_race` | default | 三节点同一合法终值 | common | 900/600 | C | — |
| 17 | writeback DMA overlap | correctness | 1s | `e2e_tc17_writeback_dma` | default | pre/post DMA值正确 | common | 900/600 | C | — |
| 18 | directory fill replay | correctness | 1s | `e2e_tc18_directory_fill_replay` | default | node1/2=18181818 | common | 900/600 | C | — |
| 19 | dirty persist | correctness | 1s | `e2e_tc19_directory_dirty_persist` | default | node2=ABCD1234 | common | 900/600 | C | — |
| 20 | offload smoke A | correctness | 1s | `e2e_tc20_offload_smoke_a` | default | 非空且全20202020 | common | 900/600 | C | — |
| 21 | offload smoke B | correctness | 1s | `e2e_tc21_offload_smoke_b` | default | 非空且全21212121 | common | 900/600 | C | — |
| 22 | resident pressure | correctness | 1s | `e2e_tc22_resident_capacity_pressure` | default | ≥9 probes全MATCH | common | 1200/600 | C | — |
| 23 | bloom false positive | correctness | 1s | `e2e_tc23_bloom_false_positive_fallback` | default | 首读0、回填23ABCDEF | common | 900/600 | C | 零读是明确期望 |
| 24 | multinode pressure | correctness | 1s | `e2e_tc24_multinode_pressure_stress` | default | ≥9 MATCH且anchor齐 | common | 1200/600 | C | — |
| 25 | invalidate/clear cycle | correctness | 1s | `e2e_tc25_invalidate_clear_cycle` | default | 无mismatch、三节点终值一致 | common | 1200/600 | C | — |
| 26 | L3 eviction chain | correctness | 1s | `e2e_tc26_l3_eviction_writeback_chain` | default | node1/2目标值保留 | common | 1200/600 | C | — |
| 27 | epoch wrap stress | correctness | 1s | `e2e_tc27_epoch_wrap_stress` | default | wrap marker且终值收敛 | common | 1200/600 | C | — |
| 28 | backstore metadata | correctness | 1s | `e2e_tc28_backstore_metadata_consistency` | default | data/meta值及关系marker | common | 1200/600 | C | — |
| 29 | exclusive local upgrade | correctness | 1s | `e2e_tc29_local_upgrade_from_exclusive` | default | 2900F111+TC29_UPG | common | 900/600 | C | — |
| 30 | stale clear tombstone | correctness | 1s | `e2e_tc30_stale_clear_tombstone` | default | 30BB0022+stale/replay | common | 900/600 | C | — |
| 31 | multicpu isolation | correctness | 1s | `e2e_tc31_multicpu_concurrent_isolation` | default | node0≥12全MATCH | common | 1200/600 | C | — |
| 32 | cross-socket read miss | correctness | 2s | `e2e_tc32_cross_socket_read_miss` | default | 值+TC32_LAT | common | 1200/600 | O | 强制2s |
| 33 | cross-socket writeback | correctness | 2s | `e2e_tc33_cross_socket_writeback` | default | 值+homeSocket0 marker | common | 1200/600 | O | 强制2s |
| 34 | dual-socket pingpong | correctness | 2s | `e2e_tc34_dual_socket_pingpong` | default | node2读两socket值 | common | 1200/600 | O | 强制2s |
| 35 | NUMA stress | correctness | 2s | `e2e_tc35_numa_latency_stress` | default | 三done值+三node progress | common | 1500/600 | O | 强制2s |
| 36 | owner upgrade G_E | correctness | 1s | `e2e_tc36_owner_upgrade_ge_window` | default | GE marker、无提前recall、终值 | common | 900/600 | C | — |
| 37 | owner upgrade G_M | correctness | 1s | `e2e_tc37_owner_upgrade_gm_window` | default | GM marker、无非法转移、终值 | common | 900/600 | C | — |
| 38 | stale clear storm | correctness | 1s | `e2e_tc38_stale_clear_tombstone_storm` | default | stale≥2、replay、终值 | common | 1200/600 | C | — |
| 39 | dual-socket same PA | correctness | 2s | `e2e_tc39_dual_socket_same_pa_interference` | default | route marker、三node收敛 | common | 1500/600 | O | 强制2s |
| 40 | recall retry | correctness | 1s | `e2e_tc40_recall_timeout_retry` | default | retry≥1、最终收敛 | common | 1200/600 | O | — |
| 41 | recall/invalidate overlap | correctness | 1s | `e2e_tc41_recall_invalidate_overlap` | default | 两phase marker+三node终值 | common | 1200/600 | C | — |
| 42 | exact 24b wrap | correctness | 1s | `e2e_tc42_exact_epoch_wrap_24b` | default | ffffff→0 marker+终值 | common | 1200/600 | O | — |
| 43 | rapid owner cycle | correctness | 1s | `e2e_tc43_rapid_owner_cycle` | default | progress≥4、无mismatch、终值 | common | 1200/600 | C | — |
| 44 | full protocol matrix | correctness | 1s | `e2e_tc44_full_protocol_matrix` | default | 四path+三node四值 | common | 1500/600 | C | — |
| 45 | bloom saturation | correctness | 1s | `e2e_tc45_fill_conflict_bloom_sat` | default | stress marker+终值 | common | 1200/600 | C | — |
| 46 | multibeat recall | correctness | 1s | `e2e_tc46_multibeat_recall` | default | 64 byte逐项+summary零错 | common | 1200/600 | C | — |
| 47 | registry drop_clear | negative | 1s | `e2e_tc47_drop_clear` | default | 值收敛+fault evidence | common | 1200/600 | S | 实际dup Clear，不是drop |
| 48 | dup invalidate ack | negative | 1s | `e2e_tc48_dup_inv_ack` | default | 值收敛+fault evidence | common | 1200/600 | S | dup |
| 49 | registry reorder_acks | negative | 1s | `e2e_tc49_reorder_acks` | default | 值收敛+fault evidence | common | 1200/600 | S | 实际dup InvAck |
| 50 | producer-consumer ring | correctness | 1s | `e2e_tc50_producer_consumer_ring` | default | workload专用marker/reads | common | 1200/600 | C | verifier专用 |
| 51 | bank ledger | correctness | 1s | `e2e_tc51_bank_ledger` | default | 守恒与专用读校验 | common | 1200/600 | C | — |
| 52 | mapreduce scatter/gather | correctness | 1s | `e2e_tc52_mapreduce_scatter_gather` | default | map/reduce结果校验 | common | 1200/600 | C | — |
| 53 | cache contention storm | correctness | 1s | `e2e_tc53_cache_contention_storm` | default | 专用收敛校验 | common | 1500/600 | C | — |
| 54 | NUMA tiled matmul | correctness | 1s | `e2e_tc54_numa_tiled_matmul` | default | 矩阵结果校验 | common | 1500/600 | C | — |
| 63 | recall orphan cleanup | correctness | 1s | `e2e_tc63_recall_orphan_timer_cleanup` | default | orphan timer清理证据与值 | common | 1200/600 | C | — |
| 64 | recall-done orphan | correctness | 1s | `e2e_tc64_recall_done_orphan_lazy_cleanup` | default | lazy cleanup证据与值 | common | 1200/600 | C | — |

### 7.2 拓扑、性能、容量与 HA

| TC | scenario/name | 类 | topo | workload/分支 | config | pass 摘要 | script | 推荐 CASE/STALL | 源 | 特殊说明 |
|---:|---|---|---|---|---|---|---|---|:---:|---|
| 80 | cross-node latency | perf | 1s | `e2e_tc80_cross_node_latency` | default | final MATCH；latency数只报告 | common | 1200/600 | C | correctness不设性能门限 |
| 81 | cross-socket latency | perf | 2s | `e2e_tc81_cross_socket_latency` | default | final MATCH；same/cross计数 | common | 1200/600 | O | — |
| 82 | 8-node ring latency | perf | 8n1s | `e2e_tc82_8node_ring_latency` | default | final MATCH | common | 1800/600 | O | — |
| 84 | cacheline capacity A | perf | 1s | `e2e_tc84_cacheline_capacity` | default | verifier无条件PASS并仅计marker | common | 1200/600 | O | permissive |
| 85 | cacheline capacity B | perf | 1s | 同TC84源/TC85分支 | default | 同TC84 | common | 1200/600 | O | permissive |
| 90 | 8N all-to-all | perf | 8n1s | `e2e_tc90_8node_all_to_all` | default/profile | 专用reads+phase timer | common | 3600/600 | S | 三profile矩阵 |
| 91 | 8N hotspot | perf | 8n1s | `e2e_tc91_8node_hotspot` | default/profile | 专用收敛+timer | common | 3600/600 | S | — |
| 92 | 8N butterfly | perf | 8n1s | `e2e_tc92_8node_butterfly` | default/profile | 专用拓扑读校验 | common | 3600/600 | S | — |
| 93 | pairwise pingpong | perf | 8n1s | `e2e_tc93_8node_pairwise_pingpong` | default/profile | pair值与timer | common | 3600/600 | S | — |
| 94 | 8N barrier stress | perf | 8n1s | `e2e_tc94_8node_barrier_stress` | default/profile | barrier/phase完整 | common | 3600/600 | S | — |
| 95 | 8N2S barrier | perf | 8n2s | `e2e_tc95_8n2s_barrier_stress` | default/profile | 16 plane barrier完整 | common | 3600/600 | S | — |
| 96 | 8N2S cross-socket read | perf | 8n2s | `e2e_tc96_8n2s_cross_socket_read` | default/profile | 读值+timer | common | 3600/600 | S | — |
| 97 | 8N2S pingpong | perf | 8n2s | `e2e_tc97_8n2s_pingpong` | default/profile | plane值收敛 | common | 3600/600 | S | — |
| 98 | 8N2S hotspot | perf | 8n2s | `e2e_tc98_8n2s_hotspot` | ways98 | 高争用专用校验 | 1500 | 21600/1800 | O/C | 已观察>1800 timeout；无当前PASS elapsed，按约6小时热点保守估算 |
| 99 | per-plane slots | perf | 8n2s | `e2e_tc99_8n2s_perplane_slots` | default/profile | 16/16 MATCH | common | 1800/600 | O | 文档记录<5min PASS |
| 100 | batch-RS advisory | perf | 8n2s | `e2e_tc100_8n2s_batch_rs` | default/profile | correctness+timer，优化证据advisory | common | 3600/600 | S | 不保证实际命中 |
| 101 | direct-fwd advisory | perf | 8n2s | `e2e_tc101_8n2s_direct_fwd` | default/profile | correctness+timer，优化证据advisory | common | 3600/600 | S | 不保证实际命中 |
| 102 | writeback data persist | correctness | 1s | `e2e_tc102_writeback_data_persist` | default/profile | payload与persist路径 | common | 1800/600 | S | — |
| 110 | drop Clear | negative | 1s | `e2e_tc110_drop_clear` | default | fault evidence+收敛 | common | 1200/600 | S | rule表 |
| 111 | drop Upgrade | negative | 1s | `e2e_tc111_silent_upgrade_drop` | default | fault evidence+收敛 | common | 1200/600 | S | — |
| 112 | TBE interference | correctness | 1s | `e2e_tc112_tbe_interference` | default/profile | 专用路径timer与值 | common | 1800/600 | O | 最终单-primary版本 |
| 113 | silent-upgrade micro | perf | 1s | `e2e_tc113_silent_upgrade_micro` | default/profile | 值/phase；无强制enable | common | 1800/600 | C | 名称不等于开关已开 |
| 114 | silent-upgrade minimal | perf | 1s | `e2e_tc114_silent_upgrade_minimal` | default/profile | 值/phase；无强制enable | common | 1800/600 | C | 同上 |
| 115 | cross-CPU silent upgrade | perf | 1s | `e2e_tc115_cross_cpu_silent_upgrade` | default/profile | 值/phase；无强制enable | common | 1800/600 | C | 同上 |
| 116 | directory eviction stress | correctness | 1s | `e2e_tc116_directory_eviction_stress` | DIR116 | 专用eviction marker/reads | common | 1800/600 | S | — |
| 117 | Clear reorder | negative | 1s | `e2e_tc117_clear_reorder` | default | ≥2 MATCH+reorder evidence | common | 1200/600 | S | fault smoke |
| 118 | mixed fault | negative | 1s | `e2e_tc118_mixed_fault` | default | ≥2 MATCH+fault evidence | common | 1200/600 | S | — |
| 119 | triple fault | negative | 1s | `e2e_tc119_triple_fault` | default | ≥3 MATCH+fault evidence | common | 1200/600 | S | — |
| 120 | baseline perf mix | perf | 1s | `e2e_tc120_baseline_perf_mix` | PERF5000 | 只拒绝mismatch；phase/stats弱报告 | common | 3600/600 | S | phase_done不强制 |
| 121 | cold stream | perf | 1s | `e2e_tc121_perf_cold_stream` | PERF5000 | ≥1读、无错、≥2 phases | common | 3600/600 | S | 三profile |
| 122 | hot reuse | perf | 1s | `e2e_tc122_perf_hot_reuse` | PERF6144 | 同perf通用校验 | common | 3600/600 | S | — |
| 123 | shared upgrade | perf | 1s | `e2e_tc123_perf_shared_upgrade` | PERF6144 | 同perf通用校验 | common | 3600/600 | S | — |
| 124 | direct-fwd scenario | perf | 1s | `e2e_tc124_perf_direct_fwd` | PERF124 | 同perf通用校验 | common | 3600/600 | S | runner固定direct-fwd=0 |
| 125 | read offload/onload | correctness | 1s | `e2e_tc125_read_offload_onload` | SPILL1 | V0→V1、offload+fill | common | 3600/600 | S | naive SKIP |
| 126 | resident upgrade replay | correctness | 1s | `e2e_tc126_resident_upgrade_replay` | SPILL1 | waiter/fill/恰一commit/无降级 | common | 3600/600 | S | naive SKIP |
| 127 | writeback onload | correctness | 1s | `e2e_tc127_writeback_offload_onload` | SPILL1 | payload、WB persist、spill/fill | common | 3600/600 | S | naive SKIP |
| 128 | clean evict onload | correctness | 1s | `e2e_tc128_clean_evict_offload_onload` | SPILL1 | payload、offload+fill；EvictReq软证据 | 1800 | 3600/600 | S | naive SKIP |
| 129 | two-cycle integration | correctness | 1s | `e2e_tc129_long_mixed_integration` | SPILL1 | 两offload/fill、V0→V1 | common | 3600/600 | S | naive SKIP |
| 130 | overflow benchmark | perf | 1s | `e2e_tc130_directory_overflow_benchmark` | STRESS5000 | ≥24 MATCH、四phase、timer | common | 1800/600 | S | focused脚本默认900 |
| 131 | catalog fullscan | perf | 8n1s | `e2e_tc131_catalog_fullscan` | REALCAP | phases+≥8 reads+timer；naive禁spill | 7200 | 3600/1800 | O | 三profilePASS 2684-2910s |
| 132 | dirty checkpoint | perf | 1s | `e2e_tc132_dirty_checkpoint_stream` | REALCAP | phases+≥16 reads+timer；naive禁spill | 7200 | 3600/1800 | O/C | 三profilePASS 1347-1487s；保留收尾余量 |
| 133 | shared frontier | perf | 8n1s | `e2e_tc133_8n1s_shared_frontier` | REALCAP | phases+≥7 reads+timer；naive禁spill | 7200 | 7200/1800 | O | 三profilePASS 3292-4728s |
| 134 | sliding window | perf | 8n2s | `e2e_tc134_8n2s_sliding_window` | REALCAP | phases+≥7 reads+timer；naive禁spill | 7200 | naive 18000/1800；spill/opt 10800/1800 | O/C | profile展开见8.3 |
| 135 | preserved sharer | perf | 1s | `e2e_tc135_preserved_sharer_revisit` | STRESS5000 | 48 reads、24 sample percentile | common | 3600/600 | O | 三profile |
| 136 | preserved owner | perf | 1s | `e2e_tc136_preserved_owner_store` | STRESS5000 | 24 reads、24 sample percentile | common | 3600/600 | O | — |
| 137 | new requester | perf | 1s | `e2e_tc137_new_requester_load` | STRESS5000 | 48 reads、24 sample percentile | common | 3600/600 | O | — |
| 138 | dirty handoff | perf | 1s | `e2e_tc138_dirty_handoff_store` | STRESS5000 | 24 reads、24 store samples | common | 3600/600 | O | spill可慢于naive |
| 139 | mixed throughput | perf | 1s | `e2e_tc139_mixed_batch_throughput` | STRESS5000 | 24 reads、16 batch samples、256-op timer | common | 3600/600 | O | 历史失败版不等于现版 |
| 140 | cross-L2 owner store | perf | 1s | `e2e_tc140_cross_l2_owner_store` | TC140P | 24 reads、24 samples | common | 3600/600 | O | 目录无压力 |
| 141 | spill writer recovery | correctness | 1s | `e2e_tc141_spill_shared_writer_recovery` | STRESS5000 | 32 reads+spill/fill/release证据 | common | 3600/600 | O | naive语义不适用 |
| 142 | OLTP buffer pool | perf | portable | `e2e_tc142_db_oltp_buffer_pool` | PORTABLE/512K | 每plane 5读、32 samples、双timer | common | 21600/1800 | O/C | 最大支持拓扑8n2s；见扩展表 |
| 143 | B-tree traversal | perf | portable | `e2e_tc143_db_btree_traversal` | PORTABLE/512K | 每plane 5读、32 samples、双timer | common | 21600/1800 | O/C | 最大支持拓扑8n2s |
| 144 | WAL checkpoint | perf | portable | `e2e_tc144_db_wal_checkpoint` | PORTABLE/512K | 每plane 17读、32 samples、双timer | common | 21600/1800 | O/C | 最大支持拓扑8n2s |
| 145 | FaaS warm | perf | portable | `e2e_tc145_faas_warm_invocation` | PORTABLE/512K | 每plane 9读、32 samples、双timer | common | 21600/1800 | O/C | 最大支持拓扑8n2s |
| 146 | graph frontier | perf | portable | `e2e_tc146_graph_frontier` | PORTABLE/512K | 每plane 5读、32 samples、双timer | common | 21600/1800 | O/C | 最大支持拓扑8n2s |
| 147 | feature store | perf | portable | `e2e_tc147_feature_store` | PORTABLE/512K | 每plane 9读、32 samples、双timer | common | 21600/1800 | O/C | 最大支持拓扑8n2s |
| 148 | Clear fault qualification | negative | 1s | `e2e_tc148_fault_qualification` | default | 32/32 reads及rules | common | 1200/600 | S | 32 rules |
| 149 | UpgradeReq loss | negative | 1s | `e2e_tc149_upgrade_invalidate_fault_qualification`/149 | default | 32 reads、8 rules恰一次 | common | 1200/600 | S | — |
| 150 | InvAck dup | negative | 1s | 同源/150 | default | 32 reads、16 rules恰一次 | common | 1200/600 | S | — |
| 151 | InvAck delay | negative | 1s | 同源/151 | default | 32 reads、16 rules恰一次 | common | 1200/600 | S | — |
| 152 | InvAck reorder | negative | 1s | 同源/152 | default | 32 reads、16 rules恰一次 | common | 1200/600 | S | — |
| 153 | RecallResp dup | negative | 1s | `e2e_tc153_recallresp_fault_qualification`/153 | default | 16 reads、16 rules恰一次 | common | 1200/600 | S | — |
| 154 | RecallResp delay | negative | 1s | 同源/154 | default | 16 reads、16 rules恰一次 | common | 1200/600 | S | — |
| 155 | RecallResp reorder | negative | 1s | 同源/155 | default | 16 reads、16 rules恰一次 | common | 1200/600 | S | — |
| 156 | RecallResp drop | negative | 1s | 同源/156 | default | 16 reads、16 rules恰一次 | common | 1200/600 | S | — |
| 157 | InvAck drop | negative | 1s | TC149共享源/157 | default | 32 reads、16 rules恰一次 | common | 1200/600 | S | — |
| 158 | UpgradeResp drop | negative | 1s | TC149共享源/158 | default | 32 reads、8 rules恰一次 | common | 1200/600 | S | — |
| 159 | UpgradeAck drop | negative | 1s | TC149共享源/159 | default | 32 reads、8 rules恰一次 | common | 1200/600 | S | — |
| 200 | A3 naive recall | correctness | 1s | `e2e_a3_naive_recall` | A3 | payload+BEEFCAFE及3 naive markers | common | 1800/600 | S | 仅naive |
| 201 | A5 spill recall | correctness | 1s | `e2e_a5_spill_recall` | A5 | MATCH+spill/fill markers | common | 1800/600 | S | naive SKIP |
| 202 | C1 cache push | correctness | 1s | `e2e_c1_spill_cache_push` | C1/D1 | MATCH+spill completion | common | 1800/600 | S | naive SKIP |
| 203 | D1 H64 overflow | correctness | 1s | `e2e_d1_overflow` | C1/D1 | node2 MATCH+spill/fill | common | 1800/600 | S | naive SKIP；非Schema A |
| 210 | HA01 local reuse | HA | 2n1s | `e2e_ha_2n1s_core`/HA01 | HA2N | 两node JSON validation | common | 900/600 | O/C | formal三profile33-41s；gem5 flags需显式注入 |
| 211 | HA02 remote read | HA | 2n1s | 同源/HA02 | HA2N | 两node validation | common | 900/600 | O/C | 同上 |
| 212 | HA03 ownership | HA | 2n1s | 同源/HA03 | HA2N | 两node validation | common | 900/600 | O/C | — |
| 213 | HA04 shared writer | HA | 2n1s | 同源/HA04 | HA2N | 两node validation | common | 900/600 | O/C | — |
| 214 | HA07 producer consumer | HA | 2n1s | 同源/HA07 | HA2N | 两node validation | common | 900/600 | O/C | formal150-210s |
| 215 | HA05 clean victim | HA | 2n1s | 同源/HA05 | HA2N | 两node validation | common | 900/600 | O/C | capacity path |
| 216 | HA06 dirty lifecycle | HA | 2n1s | 同源/HA06 | HA2N | 两node validation | common | 900/600 | O/C | capacity path |
| 217 | HA10 catalog | HA/perf | 2n1s | 同源/HA10 | HA2N | 2 reads、8 samples、128-op timer | common | 900/600 | O/C | formal116-131s |
| 218 | HA08 lock/barrier | HA | 2n1s | 同源/HA08 | HA2N | 两node validation | common | 900/600 | O/C | formal175-218s |
| 219 | HA09 mixed pressure | HA | 2n1s | 同源/HA09 | HA2N | 两node validation | common | 900/600 | O/C | — |
| 220 | HA11 exact150 clean | HA/perf | 2n1s | 同源/HA11 | HA2N | 64 reads、精确768/1.5、timers | common | 900/600 | O/C | formal47-66s |
| 221 | HA12 exact150 dirty | HA/perf | 2n1s | 同源/HA12 | HA2N | 64 reads、精确768/1.5、3 timers | common | 900/600 | O/C | formal47-68s |
| 222 | C123-HA | HA | 2n1s | `e2e_ha_cgroup_2n1s`/C123 | HA2N | 4 reads、timer+latency、双manifest | common | 900/600 | C | optimized smoke；三profile见扩展 |
| 223 | C130-HA | HA/perf | 2n1s | 同源/C130 | HA2N | 24 reads、96-op timer、24 samples | common | 900/600 | C | optimized smoke |
| 224 | C132-HA checkpoint | HA | 2n1s | 同源/C132 | HA2N | 两node config一致、抽样全MATCH、双timer | common | 28800/1800 | C | full-scale上界；compact见扩展 |
| 225 | C135-HA | HA/perf | 2n1s | 同源/C135 | HA2N | 48 reads、24 samples | common | 900/600 | C | optimized smoke |
| 226 | C138-HA | HA/perf | 2n1s | 同源/C138 | HA2N | 24 reads、24 samples | common | 900/600 | C | optimized smoke |
| 227 | C139-HA | HA/perf | 2n1s | 同源/C139 | HA2N | 9 reads、16 samples、256-op timer | common | 900/600 | C | optimized smoke |

## 8. 多配置族展开

### 8.1 TC120-TC124

| TC | profile | UBIO/profile | gem5 | CASE/STALL | applicability |
|---|---|---|---|---:|---|
| 120-121 | naive | PERF5000 naive batch0 | silent0 direct0 batch0 | 3600/600 | RUN |
| 120-121 | spill-noopt | PERF5000 spill batch0 | silent0 direct0 batch0 | 3600/600 | RUN |
| 120-121 | optimized | PERF5000 spill batch1 | silent1 direct0 batch1 | 3600/600 | RUN |
| 122-123 | naive | PERF6144 naive batch0 | silent0 direct0 batch0 | 3600/600 | RUN |
| 122-123 | spill-noopt | PERF6144 spill batch0 | silent0 direct0 batch0 | 3600/600 | RUN |
| 122-123 | optimized | PERF6144 spill batch1 | silent1 direct0 batch1 | 3600/600 | RUN |
| 124 | naive | PERF124 batch0 | silent0 direct0 batch0 | 3600/600 | RUN |
| 124 | spill-noopt | PERF124 batch0 | silent0 direct0 batch0 | 3600/600 | RUN |
| 124 | optimized | PERF124 batch1 | silent1 direct0 batch1 | 3600/600 | RUN；direct-fwd仍为0 |

### 8.2 TC125-TC130 的适用性

| TC | naive | spill-noopt | optimized | CASE/STALL |
|---|---|---|---|---:|
| 125-129 | SKIP：verifier断言spill/onload路径 | RUN，SPILL1 | RUN，SPILL1+opt | 3600/600 |
| 130 | RUN，STRESS5000 naive | RUN，spill batch0 | RUN，spill batch1 | 1800/600；大矩阵常用3600/600 |

### 8.3 TC131-TC134：每 profile 与 topology

| TC | topology | profile | `UBCC_POLICY` | gem5 profile | CASE/STALL |
|---|---|---|---|---|---:|
| 131 | 8n1s | naive | naive | baseline/noopt | 3600/1800 |
| 131 | 8n1s | spill-noopt | spill | noopt | 3600/1800 |
| 131 | 8n1s | optimized | spill | silent1,batch1,direct0 | 3600/1800 |
| 132 | 1s | naive | naive | noopt | 3600/1800 |
| 132 | 1s | spill-noopt | spill | noopt | 3600/1800 |
| 132 | 1s | optimized | spill | silent1,batch1,direct0 | 3600/1800 |
| 133 | 8n1s | naive | naive | noopt | 7200/1800 |
| 133 | 8n1s | spill-noopt | spill | noopt | 7200/1800 |
| 133 | 8n1s | optimized | spill | silent1,batch1,direct0 | 7200/1800 |
| 134 | 8n2s | naive | naive | noopt | 18000/1800 |
| 134 | 8n2s | spill-noopt | spill | noopt | 10800/1800 |
| 134 | 8n2s | optimized | spill | silent1,batch1,direct0 | 10800/1800 |

### 8.4 TC135-TC141 profile 展开

| TC | naive | spill-noopt | optimized | CASE/STALL |
|---|---|---|---|---:|
| 135-139 | RUN：STRESS5000 naive | RUN：spill batch0 | RUN：spill batch1 | 3600/600 |
| 140 | RUN：default dir batch0 | RUN：batch0 | RUN：batch1 | 3600/600 |
| 141 | SKIP：要求spill/fill/release证据 | RUN：STRESS5000 spill | RUN：STRESS5000 spill+opt | 3600/600 |

### 8.5 TC142-TC147 legacy tiny-directory

下表 **逐一适用于 TC142、143、144、145、146、147**。

| topology | naive | spill-noopt | optimized |
|---|---:|---:|---:|
| 1s/3n1s | 3600/600 | 3600/600 | 3600/600 |
| 2s/3n2s | 3600/600 | 3600/600 | 3600/600 |
| 8n1s | 5400/900 | 5400/900 | 5400/900 |
| 8n2s | 7200/1200 | 7200/1200 | 7200/1200 |

这里的 legacy 是 TC 自带 `PORTABLE legacy` 参数，不推荐使用 `--1s-tinydir`，
因为该 JSON 模板会吞掉 `ubio_extra_args` 和 metadata capacity 参数。

### 8.6 TC142-TC147 PORTABLE_512K p150

下表同样 **逐一适用于 TC142-147**；每格均为 `CASE/STALL`。所有组合设置
`PORTABLE_512K_DIR=1`、p150、32 batches，并分别运行 naive、spill-noopt、optimized。

| topology | naive | spill-noopt | optimized | outer margin |
|---|---:|---:|---:|---:|
| 3n1s | 3600/1800 | 3600/1800 | 3600/1800 | +300 |
| 2n1s | 7200/1800 | 7200/1800 | 7200/1800 | +300 |
| 3n2s | 7200/1800 | 7200/1800 | 7200/1800 | +300 |
| 8n1s | 10800/1800 | 10800/1800 | 10800/1800 | +300 |
| 8n2s | 21600/1800 | 21600/1800 | 21600/1800 | +300 |

现行 `run_p0_512k_matrix.py` 采用统一 CASE 10800、STALL 1800、outer +300。上表是按
topology分级后的目录建议：8n2s 提高到21600，依据是已观察到13199秒PASS，且还有
15540-18267秒的长失败运行。失败时长不是PASS runtime，但说明18000仍缺乏充分调度余量。
该建议尚未回写 runner 默认；如需保持其他拓扑较短预算，应拆分8n2s单独运行。
早期旧 portable（非512K p150）TC142 3n1s naive 曾两次3600秒未完成；随后
512K p150 round2 的 TC142 3n1s naive 以1823秒PASS。二者配置不同，不能互相覆盖。
历史失败、磁盘门禁、bind 路径过长和被 watchdog 杀死的 outlier 都不能当作 PASS elapsed。

### 8.7 TC200-TC203

| TC | naive | spill-noopt | optimized | CASE/STALL |
|---|---|---|---|---:|
| 200 | RUN：A3 | SKIP | SKIP | 1800/600 |
| 201 | SKIP | RUN：A5 | RUN：A5，允许gem5 opt | 1800/600 |
| 202 | SKIP | RUN：C1 | RUN：C1，允许gem5 opt | 1800/600 |
| 203 | SKIP | RUN：D1 | RUN：D1，允许gem5 opt | 1800/600 |

### 8.8 TC210-TC221 formal150

`run_ha_formal_150_matrix.py` 对每个 TC210-221 跑三 profile，固定 `--2n1s` 和
`WORKLOAD_CFLAGS=-DHA_FORMAL_CAPACITY_LINES=768`。当前脚本 CASE=10800、STALL=600、
outer=CASE+120；结构化PASS elapsed仅33-218秒，因此日常qualification建议900/600，
无需把10800解释为实际case需求。

| TC范围 | naive | spill-noopt | optimized | formal pass附加条件 |
|---|---:|---:|---:|---|
| 210-219 | 900/600 | 900/600 | 900/600 | 唯一 capacity JSON：resident512、unique768、ratio1.5 |
| 220-221 | 900/600 | 900/600 | 900/600 | verifier 本身还强制 exact150 phase/reads |

表中的完整 gem5 profile 仅在矩阵同时注入对应 `EP_GEM5_OPTS` 时成立；单独设置
`EP_PERF_PROFILE` 只会改变 UBIO 参数，不会自动改变 TC210-227 的 gem5 flags。

### 8.9 TC222-TC227 smoke/qualification

| TC | profile | workload scale | CASE/STALL | 状态口径 |
|---|---|---|---:|---|
| 222/223/225/226/227 | optimized smoke | 源码默认规模 | 900/600 | C：verifier PASS，未记录elapsed |
| 222/223/225/226/227 | 三profile qualification | 默认规模 | 1800/600 | C，建议补齐 |
| 224 compact | naive | active512、pressure4096、stride64 | 5400/900 | C：源码可运行，未声明已测 |
| 224 compact | spill-noopt | 同上 | 5400/900 | C：源码可运行，未声明已测 |
| 224 compact | optimized | 同上 | 5400/900 | O：PASS，elapsed未记录 |
| 224 full-scale | optimized | active8192、pressure65536、默认抽样 | 28800/1800 | C：已有约21600 timeout；修复后PASS elapsed未记录 |
| 224 三profile full-scale | naive/spill-noopt/optimized | 同上 | 28800/1800 | C：尚不能声称三者都测得 |

同样，三profile的gem5 flags必须通过 `EP_GEM5_OPTS` 显式注入。

### 8.10 fault suite 配置

| suite | TC | topology/profile | CASE/STALL | CPU |
|---|---|---|---:|---|
| smoke | 117-119 | 1s/default | 1200/600 | Docker cpuset缺省6-9 |
| qualification | 148 | 1s/default | 1200/600 | 同上 |
| level2 | 149-155 | 1s/default | 1200/600 | 同上 |
| loss | 156-159 | 1s/default | 1200/600 | 同上 |
| all | 117-119,148-159 | 1s/default，串行同runner | 1200/600 | 同上 |

### 8.11 TC90-TC102、TC110-TC115 外层 profile 约定

这些 TC 没有 `run_multi.sh` 内建的 UBIO 目录 profile 切换。旧 TC90 phase/perf
矩阵通过 `EP_GEM5_OPTS` 构造三种 gem5 profile；仅设置 `EP_PERF_PROFILE` 不足以
形成完整配置。下表的 CASE/STALL 逐 TC 仍以第7节为准。

| TC范围 | profile | UBIO | 必需 `EP_GEM5_OPTS` | timeout |
|---|---|---|---|---|
| 90-102、110-115 | naive | default；TC98额外ways1 | `--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0` | 第7节对应值 |
| 同上 | spill-noopt | default；并不自动启用spill目录 | `--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0` | 第7节对应值 |
| 同上 | optimized | default；并不自动启用spill目录 | `--silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1` | 第7节对应值 |

因此这些旧矩阵中的 `naive`/`spill-noopt` 主要是 gem5优化标签，不等价于
TC120以后那种明确的 UBIO naive/spill policy 对照。报告中必须同时列出最终UBIO参数，
不能只写 profile 名。

## 9. elapsed 证据与推荐边界

本节严格区分 `PASS elapsed`、`timeout observation` 和 `C estimate`。下列秒数来自
当前工作树保留的 matrix TSV 的 `elapsed_sec`，是 coordinator 的 wall-clock elapsed，
不是 guest counter，也不是协议模拟时间。没有 TSV elapsed 的项目仍标“未记录”。

| 范围 | O：可审计观察 | PASS elapsed 范围 | 建议 |
|---|---|---|---|
| TC131 | 2026-08-02 P0 round1 三profile PASS | **2684-2910s PASS** | CASE3600/STALL1800，O |
| TC132 | 同矩阵三profile PASS | **1347-1487s PASS** | CASE3600/STALL1800，O+C；为收尾保留余量 |
| TC133 | 同矩阵三profile PASS | **3292-4728s PASS** | CASE7200/STALL1800，O |
| TC134 | spill-noopt/optimized PASS；naive outer timeout | **8992-9252s PASS**；naive **11107s outer timeout** | spill/opt 10800；naive 18000；O+C |
| portable legacy 3n1s/3n2s/8n1s/8n2s | spill矩阵记录主要组合PASS；TC146/147 8n2s原矩阵为FAIL，后续文档称清盘后重跑PASS | TSV无elapsed | 3600/600、3600/600、5400/900、7200/1200，C |
| portable 512K p150 3n1s | P0 round2 多数三profile PASS；TC143两候选有timeout/TERM异常 | **1823-1904s PASS**；另有11102s outer timeout和957s FAIL | 3600/1800，O+C |
| portable 512K p150 2n1s | 多数三profile PASS；TC145/146 naive exit2 | **1929-4196s PASS**；失败1990/2054s不计范围 | 7200/1800，O+C |
| portable 512K p150 3n2s | 多数三profile PASS；部分naive manifest/exit失败 | **3097-3265s PASS**；4222/4705s FAIL不计范围 | 7200/1800，O+C |
| portable 512K p150 8n1s | round3 TSV仍全部PENDING | **无PASS elapsed** | 10800/1800，C |
| portable 512K p150 8n2s | round3仅TC142 naive、TC147 naive记录PASS；其余含timeout/FAIL/PENDING | **9922-13199s PASS**；11106s outer timeout及更长FAIL不计范围 | CASE21600/STALL1800，O+C；10800已不足覆盖已观察PASS上界 |
| HA210-219 formal150 | `ha_formal150_20260731/matrix.tsv` 三profile全部PASS | **33-218s PASS** | qualification 900/600，O+C |
| HA220-221 formal150 | 同TSV三profile全部PASS | **47-68s PASS** | qualification 900/600，O+C |
| TC98 | 文档记录曾 `>1800s` timeout；专用默认1500 | 无当前 PASS elapsed | CASE21600/STALL1800，O/C；按约6小时文档保守上界 |
| TC224 compact | 2026-08-02 文档明确 PASS | 秒数未记录 | 5400/900，O+C |
| TC224 full-scale | 两次约21600 timeout；修复后同日明确 PASS | 最终 PASS 秒数未记录；timeout 21600明确 | CASE28800/STALL1800，C；21600是失败下界而非安全推荐 |
| TC84/85 | 当前状态文档称修复后约1-2分钟 PASS | 约60-120秒，O | 1200/600保留大余量 |
| TC99 | 文档称16/16 MATCH且<5分钟 PASS | <300秒，O | 1800/600 |

若需要真正的 observed range，应在新矩阵 `result.json` 中记录 monotonic elapsed，至少
3 个独立 PASS run，再报告 min/median/max。timeout、FAIL 和 watchdog kill 必须单列，
不得混入 successful runtime range。

## 10. 现行 batch/matrix runner 目录

### 10.1 `scripts/run_p0_512k_matrix.py`

| 项 | 内容 |
|---|---|
| 目的 | 完整 512KiB P0：legacy TC131-134 + portable TC142-147 p150 |
| TC/topology | 131→8n1s、132→3n1s、133→8n1s、134→8n2s；portable→3n1s、2n1s、3n2s、8n1s、8n2s |
| profiles/pressure | naive、spill-noopt、optimized；portable 65536 naive capacity、150%、32 batches |
| timeout | 脚本默认 CASE10800、STALL1800、outer+300；包含8n2s的正式复跑建议覆盖CASE21600，或拆分8n2s单独运行 |
| 并发/CPU | `MAX_PARALLEL=5`；可传 `CPU_SETS`；最多2个8n2s、3个8n1s并发；磁盘门限80GiB |
| 输出 | `manifest.json`、`progress.json`、`matrix.tsv`、heartbeat、每case `result.json`/runner log |
| summarizer | round wrapper 使用 `summarize_p0_512k_round.py` |
| 状态/ caveat | 当前主 P0 runner；TSV schema 为 status/group/tc/topology/profile/level/elapsed/log/reason；并行结果不作正式 latency 对比 |

```bash
MAX_PARALLEL=3 CPU_SETS='0-7 8-15 16-23' \
  python3 scripts/run_p0_512k_matrix.py
```

### 10.2 `scripts/run_p0_512k_three_rounds.sh`

| 项 | 内容 |
|---|---|
| 目的 | 将 P0 分为 TC131-134、portable 2N/3N、portable 8N 三轮，并通知 |
| profile/压力 | 三profile、p150 |
| timeout/并发 | 脚本默认 CASE10800、STALL1800、每轮MAX_PARALLEL=3；round3含8n2s时建议覆盖CASE21600 |
| 输出 | `logs/$BASE_TAG/round{1,2,3}`、coordinator.log、round summary |
| caveat | 默认 `NTFY_URL` 是历史手工通知端点；生产环境应覆盖或禁用，避免泄露运行状态 |

```bash
NTFY_URL=https://ntfy.sh/<owned-topic> \
  bash scripts/run_p0_512k_three_rounds.sh
```

### 10.3 `scripts/run_ha_formal_150_matrix.py`

| 项 | 内容 |
|---|---|
| 目的 | TC210-221 三profile formal150 |
| topology/profile/pressure | 2n1s；三profile；`HA_FORMAL_CAPACITY_LINES=768` |
| timeout | CASE10800；脚本 STALL600；outer+120 |
| 并发/CPU | 5 worker；默认 cpuset `0-5 ... 24-29` |
| 输出 | matrix.tsv、progress.json、heartbeat、profile/tc日志 |
| caveat | 默认 `DEADLINE_CST=2026-07-31 11:00:00` 已陈旧；不覆盖会全部 SKIP。TSV 每完成追加，重跑同目录会先重写 header。 |

```bash
DEADLINE_CST='2026-08-06 23:59:00' MAX_PARALLEL=3 \
  python3 scripts/run_ha_formal_150_matrix.py
```

### 10.4 `scripts/run_database_perf_matrix.sh`

| 项 | 内容 |
|---|---|
| 目的 | TC142-147 旧 portable large 多拓扑/profile |
| 默认 | topology `1s 2s 8n1s 8n2s`；TC142-147；仅 spill-noopt |
| timeout/stall | CASE3600、STALL600；串行，无外层 timeout |
| 输出/汇总 | matrix.tsv、case日志、`summarize_database_perf_matrix.py`→summary.json |
| caveat | 不是 512K p150 runner；profile 名使用 `spill-opt`，TSV 无 elapsed/reason；历史/manual 状态 |

```bash
PROFILE_LIST='naive spill-noopt spill-opt' \
  bash scripts/run_database_perf_matrix.sh
```

### 10.5 `scripts/run_tc135_perf_matrix.sh`

| 项 | 内容 |
|---|---|
| 目的 | TC135-140 三profile机制矩阵 |
| topology/timeout | 固定1s；CASE3600、STALL600；串行 |
| 输出 | matrix.tsv、case日志、`summarize_tc135_perf_matrix.py` |
| caveat | 文件名仍叫TC135但范围至140；TSV 行首混用 RUN/PASS/FAIL 且列头是 tc/profile/topology/status/log，schema 实际错位 |

```bash
bash scripts/run_tc135_perf_matrix.sh
```

### 10.6 `scripts/run_tc90_perf_matrix.sh`

| 项 | 内容 |
|---|---|
| 目的 | TC116+ 三profile及适用性 SKIP 主矩阵 |
| TC | 116-134、200-203、210-216、218-219；不含217、220-227 |
| topology | 1s、8n1s、8n2s、2n1s按脚本分组 |
| timeout/stall | CASE3600、STALL600；串行、首失败退出 |
| 输出 | matrix.tsv + `summarize_tc90_perf_matrix.py` |
| caveat | profile label `spill-opt` 映射 optimized；TSV RUN/PASS 行与header错位；脚本还把TC131放在`--1s`组，而当前guard要求`--8n1s`，执行到TC131会FATAL并提前退出；修复前不可作为现行可完成矩阵 |

```bash
bash scripts/run_tc90_perf_matrix.sh
```

### 10.7 `scripts/run_tc90_phase_timer_matrix.sh`

| 项 | 内容 |
|---|---|
| 目的 | 重测 TC90-129、200-203、部分HA的 phase timer |
| TC/topology | 90-94/8n1s；95-101/8n2s；102、110-129、200-203/1s；210-216、218-219/2n1s |
| profile/timeout | 三profile及语义SKIP；CASE3600、STALL600；串行 |
| 输出 | matrix.tsv + TC90 summarizer |
| caveat | 不覆盖130-159、217、220-227；TSV schema 同样含 RUN/PASS 错位 |

```bash
bash scripts/run_tc90_phase_timer_matrix.sh
```

### 10.8 `scripts/run_tc90_default_sweep.sh`

| 项 | 内容 |
|---|---|
| 目的 | 注册表中部分 TC≥90 的默认 profile correctness sweep |
| TC | 90-134、200-203、210-216、218-219 的脚本静态子集 |
| timeout/stall | CASE3600、STALL600；串行、首失败退出 |
| 输出 | `sweep.tsv` |
| caveat | 名称声称 every registered TC≥90，但实际漏135-159、217、220-227；还把TC131放在`--1s`组，当前guard会FATAL并提前退出；历史/manual |

```bash
bash scripts/run_tc90_default_sweep.sh
```

### 10.9 `scripts/run_low_frequency_correctness_queue.sh`

| 项 | 内容 |
|---|---|
| 目的 | 历史 PASS sentinel 少于3次的低频 correctness 队列 |
| TC | 1-54、63-64、80-82、84-85，加128和141两profile；manifest期望64 targets |
| topology/profile | optimized；双socket集合正确分到2s；128/141专用profile |
| timeout/stall | CASE900、STALL600；容器内串行 |
| 输出 | targets.tsv、matrix.tsv、queue_manifest.txt |
| caveat | selection 是脚本创建时的历史扫描概念；现实现会无条件注册静态集合，不是动态只跑<3；TSV保留RUNNING和最终行 |

```bash
bash scripts/run_low_frequency_correctness_queue.sh
```

### 10.10 `scripts/run_fault_tests.sh`

目的和配置见 8.10。单个 Docker、cpuset 4核、`--init`、CASE1200；它设置
`E2E_STALL_TIMEOUT_SEC=600`，但 `run_multi.sh` 不读取该名字，且 wrapper 没有把宿主的
`EP_SUPERVISOR`、`EP_SUPERVISOR_PROGRESS_STALL_SEC` 用 Docker `-e` 或容器内 `env`
透传。因此仅在脚本命令前添加这些变量也**不会生效**。当前 wrapper 实际只有
CASE1200，无有效 STALL。要得到推荐1200/600，必须修改 wrapper 透传以下容器环境，
或直接展开等价 `docker run`：`EP_SUPERVISOR=1`、`EP_SUPERVISOR_INTERVAL=60`、
`EP_SUPERVISOR_PROGRESS_STALL_SEC=600`。

### 10.11 `tests/e2e/run_full_regression.sh`

| 项 | 内容 |
|---|---|
| 目的 | 除TC9、131-134外的注册 TC 全回归 |
| timeout | common900，TC98/128=1800 |
| 分组 | 1s、2s、8n1s、8n2s；容器内串行 batch |
| 输出 | batch log和summary.log；无机器可读逐case TSV |
| caveat | **缺少2n1s分组**：TC210-227 会落入ONE_SOCKET并以 `--1s` 运行，随后被 topology guard FATAL；因此当前不能称全回归可用。 |

```bash
bash tests/e2e/run_full_regression.sh
```

### 10.12 real-capacity 与 TC130 focused

`tests/e2e/run_real_capacity_benchmarks.sh` 串行跑131/132的1s、133的8n1s、134的
8n2s，naive/spill，CASE7200；它把 TC131 错跑1s，而当前 guard要求8n1s，因此脚本
已部分陈旧。`run_capacity_after_tc131.sh` 等待外部 TC131 container，再串行132-134，
CASE7200、关闭 progress watchdog、可开 memory monitor；属于人工调度脚本。

`tests/e2e/run_directory_overflow_benchmark.sh` 聚焦TC130：naive bloom512、spill bloom256、
spill bloom512，CASE默认900，随后用 trace visualizer 强制检查 naive 96次outer reuse、
spill 0次。它是机制 benchmark，不替代 correctness矩阵。

```bash
bash tests/e2e/run_directory_overflow_benchmark.sh
```

### 10.13 legacy `run_all_e2e.sh` 与 `sweep_tc2.sh`

`run_all_e2e.sh` 是旧单进程 gem5 路径，只覆盖TC1-11，直接把 ELF 写入 workload
目录，不使用当前 networksim/UBIO split 启动、独立 verify 或 per-run isolation。
`sweep_tc2.sh` 还假设 `/workspace/gem5`、调用 `run_multi.sh 2` 的旧位置参数形式、
写 `/tmp`，并用 grep 非最终 sentinel 判定。二者仅供历史/manual 诊断，不是验收入口。

## 11. 已知覆盖与配置不一致

| 问题 | 当前事实与操作影响 |
|---|---|
| TC47 naming | registry/workload/verifier称 drop Clear；实际 rule 是 dup ClearReq。报告必须写实际动作。 |
| TC49 naming | registry称 reorder ack；实际 rule 是 dup InvalidateAck。 |
| TC84/85 permissive | verifier无条件返回PASS，只报告capacity marker数量；零marker也PASS。 |
| zero-read pass gaps | TC120 在无 mismatch 时可零读PASS；TC84/85也不要求读。其他通用 verifier 多数要求读，但审计新增TC时必须显式检查。 |
| TC100/101 | optimization event 是 advisory；correctness PASS不证明 batch-RS/direct-fwd 被消费或更快。 |
| TC113-115 | 名称含silent upgrade，但 runner没有专用强制enable；必须由 profile/`EP_GEM5_OPTS` 明确打开。 |
| TC120 | `phase_done` 和 stats 只进入PASS消息，不是硬条件；phase enforcement弱。 |
| TC124 | 所有 runner profile都设置 `--direct-fwd=0`；它不是 direct-fwd A/B。 |
| TC131 axes | `UBCC_POLICY` 选目录策略，`EP_PERF_PROFILE` 选gem5优化；二者可分离，manifest必须同时记录。 |
| tiny-dir template | `topo_1s_tinydir.json` UBIO cmd遗漏 `{ubio_extra_args}`，导致TC参数和metadata-dram-bytes未注入。 |
| metadata parser | runner追加 `--ubcc_metadata_size`，但 `test_e2e.py` 未注册该选项，`parse_known_args()` 后也没有写入手工构造的 `options`。当前gem5侧会回退到128MiB；非默认 `UBCC_METADATA_SIZE` 只会改变UBIO，造成两侧不一致。修复前不得声称它是有效single source of truth。 |
| exact sentinel | 当前最终判定只看 verify log **最后一行**精确 sentinel；在其后追加任何输出都可使 runner判FAIL。 |
| fault stall env | `E2E_STALL_TIMEOUT_SEC` 不是 `run_multi.sh` 的有效 supervisor变量。 |
| TSV schemas | 多个旧 shell矩阵 header 与 RUN/PASS 实际列次序不一致；读取时按runner逐项解析，不可盲目concat。 |

## 12. Operator recipes

### 12.1 standalone correctness

```bash
env E2E_RUN_ID=op_tc42 LOG_BASE=logs/op_tc42 \
  TIMEOUT_SEC=900 EP_SUPERVISOR=1 EP_SUPERVISOR_INTERVAL=60 \
  EP_SUPERVISOR_PROGRESS_STALL_SEC=600 EP_TRACE_PERF=off \
  bash tests/e2e/run_multi.sh --1s 42
```

验收：runner exit 0；`verify_tc42.log` 最后一行精确为 PASS sentinel；child status 文件数
等于 `N*K + N + 1` 且全部0。

### 12.2 fault qualification

当前 wrapper 可直接运行，但只有 CASE1200 生效：

```bash
FAULT_CPU_SET=6-9 bash scripts/run_fault_tests.sh qualification
```

若需要推荐的 STALL600，先修改 `run_fault_tests.sh` 的 Docker 启动，把
`EP_SUPERVISOR=1`、`EP_SUPERVISOR_INTERVAL=60`、
`EP_SUPERVISOR_PROGRESS_STALL_SEC=600` 显式传入容器；只设置宿主环境变量不够。
除 sentinel 外，检查 verifier 对 rule 名、命中次数恰一次和 read 数的硬约束。

### 12.3 P0 512K

```bash
env RUN_TAG=p0_512k_20260805 MAX_PARALLEL=3 \
  CPU_SETS='0-7 8-15 16-23' CASE_TIMEOUT_SEC=21600 \
  STALL_TIMEOUT_SEC=1800 DISK_FLOOR_GB=80 \
  python3 scripts/run_p0_512k_matrix.py
```

正式 latency 比较应串行重跑目标 case；qualification 并发 wall elapsed 只用于运维。

### 12.4 HA formal150

```bash
env RUN_TAG=ha_formal150_20260805 \
  DEADLINE_CST='2026-08-07 23:59:00' MAX_PARALLEL=3 \
  CPU_SETS='0-7 8-15 16-23' CASE_TIMEOUT_SEC=900 \
  python3 scripts/run_ha_formal_150_matrix.py
```

注意现行脚本仍硬编码 supervisor stall=600；若场景会长时间只推进协议，应先采用
等价手工命令将 `EP_SUPERVISOR_PROGRESS_STALL_SEC=1800`，或在后续变更中显式参数化。

## 13. 新增 TC 或矩阵配置检查表

1. 在 `TESTCASES` 注册唯一 ID，并确保源文件存在、`compile_workload.sh` 能按拓扑宏编译。
2. 在 `VERIFIERS` 注册 verifier；至少硬检查 participant、非零/精确 read 数、MISMATCH、
   关键 phase、timer/sample 和必要协议证据，避免“零读即PASS”。
3. 决定 canonical topology；若 workload 固定 barrier mask/participant 数，在
   `required_topology_for_tc` 加硬约束。
4. 如需 UBIO 参数，加入可审计 config code；确认所选 topology JSON 含
   `{ubio_extra_args}`，并记录重复参数的最终覆盖顺序。
5. 如需 gem5 参数，明确 naive/spill-noopt/optimized 三者的 silent-upgrade、direct-fwd、
   batch-RS；不要用 scenario 名称代替实际开关证据。
6. 故障 TC 必须给 rule 唯一名、消息类型、src/dst/PA、action、delay、limit，并由
   verifier 检查每条命中次数；registry 名称必须与实际动作一致。
7. 先做 standalone smoke，再做每个适用 profile；不适用组合写明确 SKIP 原因。
8. 记录 CASE、STALL、bind、outer margin 四个独立门限，并在 manifest 中写来源
   `S/O/C`。C 值不得表述为测得。
9. 收集至少3个独立 PASS 的 monotonic elapsed，报告 min/median/max；FAIL、timeout、
   bind failure、disk gate、watchdog kill 单列，不进入成功范围。
10. 保存最终有效命令、环境、源码 commit、binary hash、topology、pressure、capacity、
    CPU/cpuset、并发度和磁盘门限。
11. 机器可读结果必须包含 status、tc、topology、profile、pressure、elapsed、reason、
    log_dir；不要复用已知错位的旧 TSV schema。
12. 最后确认 verify log 的 final sentinel 是文件最后一行，并确认全部 managed child
    status 为0、数量完整、无残留 PID。
