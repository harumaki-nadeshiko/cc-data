# TC120-TC147、TC210-TC227 全展开执行契约（2026-08-06，当前工作树）

## 目的、适用范围与事实源优先级

本文是 TC120-TC147 与 TC210-TC227 的单一执行、参数、矩阵和验收契约，覆盖恰好 46 个 TC。目的不是复述历史结果，而是让操作者能够从当前工作树重建每个 case 的编译、拓扑、进程、有效 argv、输入证据和 verifier 门禁。

事实冲突时按以下优先级裁决：

1. 当前实现源码和配置：`tests/e2e/run_multi.sh`、`scripts/compile_workload.sh`、`tests/e2e/test_e2e.py`、`tests/e2e/verify.py`、workload C 源、`configs/topo_*.json`、`scripts/gen_topo.py`。
2. 当前矩阵 runner：本文件“矩阵目录”列出的 8 个脚本。
3. 本文的归纳和操作配方。
4. 历史报告、历史日志和注释；它们不能覆盖当前可执行代码。

源码行号均指 2026-08-06 当前工作树。后续源码变更后应重新核对。注册表唯一事实源是 `tests/e2e/test_e2e.py:20-168` 的 `TESTCASES`；编译脚本动态导入该字典（`scripts/compile_workload.sh:24-37`）。

## 主索引（46 项，每个 TC 恰一行）

| TC | registry/scenario | source + compile macro | canonical topology | configuration/profile axes | verifier | key expected counts/markers |
|---:|---|---|---|---|---|---|
| 120 | `e2e_tc120_baseline_perf_mix` | 同名 `.c`；无附加宏 | 3N1S | naive/spill-noopt/optimized | `verify_tc120` | READ_VAL 只要求全 MATCH；`tc120_done` 诊断 |
| 121 | `e2e_tc121_perf_cold_stream` | 同名 `.c`；无附加宏 | 3N1S | 三 profile | `verify_perf_workload` | reads>=1；done phase>=2 |
| 122 | `e2e_tc122_perf_hot_reuse` | 同名 `.c`；无附加宏 | 3N1S | 三 profile | `verify_perf_workload` | reads>=1；done phase>=2 |
| 123 | `e2e_tc123_perf_shared_upgrade` | 同名 `.c`；无附加宏 | 3N1S | 三 profile | `verify_perf_workload` | reads>=1；done phase>=2 |
| 124 | `e2e_tc124_perf_direct_fwd` | 同名 `.c`；无附加宏 | 3N1S | 三 profile；direct-fwd=0 | `verify_perf_workload` | reads>=1；done phase>=2 |
| 125 | `e2e_tc125_read_offload_onload` | 同名 `.c`；无附加宏 | 3N1S | spill-noopt/optimized | `verify_tc125` | reads>=4；spill/fill target 证据 |
| 126 | `e2e_tc126_resident_upgrade_replay` | 同名 `.c`；无附加宏 | 3N1S | spill-noopt/optimized | `verify_tc126` | upgrade commit=1；replay queued=1 |
| 127 | `e2e_tc127_writeback_offload_onload` | 同名 `.c`；无附加宏 | 3N1S | spill-noopt/optimized | `verify_tc127` | reads>=2；WB request/persist |
| 128 | `e2e_tc128_clean_evict_offload_onload` | 同名 `.c`；无附加宏 | 3N1S | spill-noopt/optimized | `verify_tc128` | reads>=2；spill/remove + fill |
| 129 | `e2e_tc129_long_mixed_integration` | 同名 `.c`；无附加宏 | 3N1S | spill-noopt/optimized | `verify_tc129` | reads>=3；spill/remove>=2；fill>=2 |
| 130 | `e2e_tc130_directory_overflow_benchmark` | 同名 `.c`；无附加宏 | 3N1S | 三 profile | `verify_tc130` | reads>=24；4 phases；timer selftest |
| 131 | `e2e_tc131_catalog_fullscan` | 同名 `.c`；无附加宏 | 8N1S | gem5 profile + 独立 `UBCC_POLICY` | `verify_real_capacity_workload` | reads>=8；5 phases；timer selftest |
| 132 | `e2e_tc132_dirty_checkpoint_stream` | 同名 `.c`；可选规模宏 | 3N1S | gem5 profile + 独立 policy | `verify_tc132` | reads>=16；3 phases；timer selftest |
| 133 | `e2e_tc133_8n1s_shared_frontier` | 同名 `.c`；无附加宏 | 8N1S | gem5 profile + 独立 policy | `verify_tc133` | reads>=7；4 phases；timer selftest |
| 134 | `e2e_tc134_8n2s_sliding_window` | 同名 `.c`；无附加宏 | 8N2S | gem5 profile + 独立 policy | `verify_tc134` | reads>=7；4 phases；timer selftest |
| 135 | `e2e_tc135_preserved_sharer_revisit` | 同名 `.c`；无附加宏 | 3N1S | 三 profile | latency-distribution verifier | reads=48；samples=24 |
| 136 | `e2e_tc136_preserved_owner_store` | 同名 `.c`；无附加宏 | 3N1S | 三 profile | `verify_tc136` | reads=24 node2；samples=24 |
| 137 | `e2e_tc137_new_requester_load` | 同名 `.c`；无附加宏 | 3N1S | 三 profile | `verify_tc137` | reads=48；node1=24,node2=24 |
| 138 | `e2e_tc138_dirty_handoff_store` | 同名 `.c`；无附加宏 | 3N1S | 三 profile | `verify_tc138` | reads=24 node0；samples=24 |
| 139 | `e2e_tc139_mixed_batch_throughput` | 同名 `.c`；无附加宏 | 3N1S | 三 profile | `verify_tc139` | reads=24；throughput 256 ops；samples=16 |
| 140 | `e2e_tc140_cross_l2_owner_store` | 同名 `.c`；无附加宏 | 3N1S | 三 profile | `verify_tc140` | reads=24 node2；samples=24 |
| 141 | `e2e_tc141_spill_shared_writer_recovery` | 同名 `.c`；无附加宏 | 3N1S | spill-noopt/optimized | `verify_tc141` | reads=32；spill/fill/shared-release |
| 142 | `e2e_tc142_db_oltp_buffer_pool` | 同名 `.c`；portable 规模宏 | multi；配方主用 3N1S/8N2S | legacy 或 512K；三 profile | portable-large verifier | reads=5P；1024 ops；32 samples |
| 143 | `e2e_tc143_db_btree_traversal` | 同名 `.c`；portable 规模宏 | multi | legacy 或 512K；三 profile | portable-large verifier | reads=5P；2048 ops；32 samples |
| 144 | `e2e_tc144_db_wal_checkpoint` | 同名 `.c`；portable 规模宏 | multi | legacy 或 512K；三 profile | portable-large verifier | reads=17P；1024 ops；32 samples |
| 145 | `e2e_tc145_faas_warm_invocation` | 同名 `.c`；portable 规模宏 | multi | legacy 或 512K；三 profile | portable-large verifier | reads=9P；2048 ops；32 samples |
| 146 | `e2e_tc146_graph_frontier` | 同名 `.c`；portable 规模宏 | multi | legacy 或 512K；三 profile | portable-large verifier | reads=5P；2048 ops；32 samples |
| 147 | `e2e_tc147_feature_store` | 同名 `.c`；portable 规模宏 | multi | legacy 或 512K；三 profile | portable-large verifier | reads=9P；2048 ops；32 samples |
| 210 | HA01 Local Reuse | `e2e_ha_2n1s_core.c -DHA_SCENARIO=1` | 2N1S | ordinary/formal150；三 profile | `verify_ha_2n1s` | validation nodes={0,1}; formal capacity=1 |
| 211 | HA02 Remote Read | core；`-DHA_SCENARIO=2` | 2N1S | ordinary/formal150；三 profile | `verify_ha_2n1s` | 两 node validation；formal exact record |
| 212 | HA03 Ownership Handoff | core；`-DHA_SCENARIO=3` | 2N1S | ordinary/formal150；三 profile | `verify_ha_2n1s` | ownership write/readback；validation |
| 213 | HA04 Shared To Writer | core；`-DHA_SCENARIO=4` | 2N1S | ordinary/formal150；三 profile | `verify_ha_2n1s` | shared_read x2；writer/readback |
| 214 | HA07 Producer Consumer | core；`-DHA_SCENARIO=7` | 2N1S | ordinary/formal150；三 profile | `verify_ha_2n1s` | producer=16；consumer=16 |
| 215 | HA05 Clean Shared Victim | core；`-DHA_SCENARIO=5` | 2N1S | ordinary/formal150；三 profile | `verify_ha_2n1s` | pressure=640；revisit=64 |
| 216 | HA06 Dirty Owner Lifecycle | core；`-DHA_SCENARIO=6` | 2N1S | ordinary/formal150；三 profile | `verify_ha_2n1s` | admission=640；revisit=64 |
| 217 | HA10 Read-mostly Catalog | core；`-DHA_SCENARIO=10` | 2N1S | ordinary/formal150；三 profile | `verify_tc217` | reads=2；batches=8；timer=128 |
| 218 | HA08 Barrier/Seq-lock | core；`-DHA_SCENARIO=8` | 2N1S | ordinary/formal150；三 profile | `verify_ha_2n1s` | barrier=16/node；handoff=16/node |
| 219 | HA09 Concurrent Pressure | core；`-DHA_SCENARIO=9` | 2N1S | ordinary/formal150；三 profile | `verify_ha_2n1s` | local=64；remote=16 |
| 220 | HA11 Exact-150 Clean | core；`-DHA_SCENARIO=11` | 2N1S | 内建 exact-150；三 profile | `verify_tc220` | reads=64；capacity exact；704/64 ops |
| 221 | HA12 Exact-150 Dirty | core；`-DHA_SCENARIO=12` | 2N1S | 内建 exact-150；三 profile | `verify_tc221` | reads=64；capacity exact；704/32/32 |
| 222 | C123-HA | `e2e_ha_cgroup_2n1s.c -DHA_CGROUP_SCENARIO=1` | 2N1S | ordinary；三 profile | `verify_tc222` | reads=4；timer=4；samples=4 |
| 223 | C130-HA | C-group；`-DHA_CGROUP_SCENARIO=2` | 2N1S | ordinary；三 profile | `verify_tc223` | reads=24；reuse=96；samples=24 |
| 224 | C132-HA Dirty Recovery | C-group；`-DHA_CGROUP_SCENARIO=3` | 2N1S | compact/full；三 profile | `verify_tc224` | config x2；reads=sample_count；2 timers |
| 225 | C135-HA | C-group；`-DHA_CGROUP_SCENARIO=4` | 2N1S | ordinary；三 profile | `verify_tc225` | reads=48；samples=24 |
| 226 | C138-HA | C-group；`-DHA_CGROUP_SCENARIO=5` | 2N1S | ordinary；三 profile | `verify_tc226` | reads=24；samples=24 |
| 227 | C139-HA | C-group；`-DHA_CGROUP_SCENARIO=6` | 2N1S | ordinary；三 profile | `verify_tc227` | reads=9；timer=256；samples=16 |

## 公共执行契约

### 路径、编译和启动/关闭顺序

令下列 shell 变量为唯一允许保留的运行时路径变量：

```bash
ROOT=/mnt/data2/cgc/cc-ep
RID="${E2E_RUN_ID:-$(date +%Y%m%d_%H%M%S)_$$}"
RUN="$ROOT/build/runs/$RID"
LOG="${LOG_BASE:-$ROOT/logs/$(date +%Y%m%d_%H%M%S)_${TOPO_KIND}_${RID}}"
ELF="$RUN/workload.elf"
TOPO="$RUN/topo.json"
IPC="$RUN/ipc"
M="${UBCC_METADATA_SIZE:-134217728}"
```

普通编译的有效命令为（`WORKLOAD_CFLAGS` 位于 include 之前，HA 场景宏位于 include 之后）：

```text
aarch64-linux-gnu-gcc -static -O0 -g -DNUM_NODES=N -DNUM_SOCKETS=K [已明确列出的 WORKLOAD_CFLAGS] -I/mnt/data2/cgc/cc-ep/tests/e2e/workloads [已明确列出的场景宏] -o $RUN/workload.elf /mnt/data2/cgc/cc-ep/tests/e2e/workloads/<registry>.c
```

依据 `scripts/compile_workload.sh:39-80`。执行顺序严格为（`tests/e2e/run_multi.sh:612-1059`）：

1. 只终止本 runner 已记录的旧 PID，不做按进程名清理。
2. 编译到 `$RUN/workload.elf`。
3. `python3 $ROOT/scripts/gen_topo.py --nodes N --sockets K --out $RUN/topo.json`；删除并重建 `$RUN/ipc`，导出 `UBCC_IPC_DIR=$RUN/ipc`。
4. 启动 `networksim $RUN/topo.json N K`，等待 PID 文件，睡眠 1 秒；若 wrapper 已退出，立即 fail-fast（`run_multi.sh:653-689`）。
5. 启动 N 个 gem5；每个 node 最多 300 秒等待 stdout 中至少 K 条 `STEP5.*Port enabled`。gem5 提前退出或 bind 超时立即失败（`:692-773`）。
6. 启动 N*K 个 UBIO，睡眠 1 秒；逐个执行 `kill -0`，任一 UBIO 启动期退出立即失败（`:776-819`）。这是当前 UBIO fail-fast gate。
7. 可选启动 supervisor；等待全部 gem5，在 TC wall timeout、progress watchdog、supervisor liveness/progress/log/disk 任一门禁失败时终止本 case（`:453-603,822-908`）。
8. reap gem5；给 UBIO 最多 15 秒自行退出。若成功，再向真实 NSIM child 发 TERM，并最多等待 15 秒。任一收尾超时失败（`:911-963`）。
9. 必须存在 `N*K + N + 1` 个 `.exit`，且每个值为 0；然后才调用 verifier（`:965-1005`）。
10. verifier 日志最后一行必须精确为 `>>> TCx PASSED <<<`；成功后 trace2chain 失败不影响 TC（`:1045-1058`）。

默认环境见 `run_multi.sh:56-80`：`TIMEOUT_SEC=600`、`EP_SYNC_INTERVAL_PS=2500`、`EP_LINK_LATENCY_PS=2500`、`M=134217728`、`EP_TRACE_PERF=sample`、first=500、max=2000、every=0、`EP_PORT_HWM=8192`、`EP_NSIM_MAX_PENDING=65536`。

### 拓扑、进程数、GID、full-mesh 和延迟类

UBIO plane/GID 恒为 `gid=node*K+socket`（`scripts/gen_topo.py:26-27`）。网络模块数 `P=N*K`，full-mesh link 数为 `P*(P-1)/2`。当前生成器默认延迟实际是跨 node 同 socket 410000 ps、同 node 跨 socket 220000 ps、跨 node 且跨 socket 630000 ps；文件头旧注释的 405000/25000 不是 argparse 当前默认值，以 `gen_topo.py:39-42,68-98` 为准。

| topology | N,K,P | gem5+UBIO+NSIM | exit 文件 | links | 延迟类计数 |
|---|---:|---:|---:|---:|---|
| `1s` | 3,1,3 | 3+3+1=7 | 7 | 3 | 410000ps x3 |
| `2s` | 3,2,6 | 3+6+1=10 | 10 | 15 | 220000ps x3；410000ps x6；630000ps x6 |
| `8n1s` | 8,1,8 | 8+8+1=17 | 17 | 28 | 410000ps x28 |
| `8n2s` | 8,2,16 | 8+16+1=25 | 25 | 120 | 220000ps x8；410000ps x56；630000ps x56 |
| `2n1s` | 2,1,2 | 2+2+1=5 | 5 | 1 | 410000ps x1 |

### 精确 argv 构造定义

下列定义是完整枚举规则，不含未绑定尾参数。对每个 `n in 0..N-1`，gem5 命令为：

```text
/mnt/data2/cgc/cc-ep/gem5/build/ARM/gem5.opt --outdir=$RUN/tcTC/m5out/noden /mnt/data2/cgc/cc-ep/tests/e2e/test_e2e.py --node-id=n --num-nodes=N --num-sockets=K --workload=$RUN/workload.elf [本 TC 绑定的 GTAIL，若为空则无参数] --ubcc_metadata_size=$M
```

对每个 `(n,s)`，其中 `n in 0..N-1,s in 0..K-1`，UBIO 命令为：

```text
/mnt/data2/cgc/cc-ep/build/bin/ubio --node=n --socket=s --num-sockets=K --num-nodes=N [本 TC 绑定的 UTAIL] --metadata-dram-bytes=$M
```

NSIM 对所有 case 均为：

```text
/mnt/data2/cgc/cc-ep/build/bin/networksim $RUN/topo.json N K
```

精确 GTAIL 名称：`G0`=空；`GN`=`--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0`；`GO`=`--silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1`；`GON`=`GO GN`；`GNN`=`GN GN`；`GOO`=`GO GO`。因此这些名称已经是完整参数序列，不是待展开变量。

精确 UTAIL 名称：

* `U5000N`=`--bloom-bytes=512 --sram-bytes=5000 --ways=2 --set-bits=2 --dir-overflow-policy=naive --batch-rs=0`；`U5000S` 将 policy 改为 spill；`U5000O` 为 spill 且 batch=1。
* `U6144N/S/O`=`--bloom-bytes=512 --sram-bytes=6144 --ways=2 --dir-overflow-policy=naive|spill --batch-rs=0|1`，O=spill,batch1。
* `UH64S/O`=`--bloom-bytes=512 --sram-bytes=6144 --ways=1 --dir-overflow-policy=spill --batch-rs=0|1`。
* `U512KN/S/O`=`--bloom-bytes=0|61440 --sram-bytes=524288 --ways=0 --set-bits=0 --dir-overflow-policy=naive|spill --batch-rs=0|1`，N=0/naive/0，S=61440/spill/0，O=61440/spill/1。
* `UTINYN/S/O`=`--bloom-bytes=128 --sram-bytes=4352 --ways=1 --set-bits=0 --dir-overflow-policy=naive|spill --batch-rs=0|1`。
* `UBN/UBS/UBO`=`--batch-rs=0|0|1 --dir-overflow-policy=naive|spill|spill`。

普通直接运行与矩阵必须区分：普通 `run_multi.sh` 只追加其内建 flags，且没有 `UBCC_OPTS` 时不会重复 policy。矩阵 runner 还设置 `EP_GEM5_OPTS` 和 `UBCC_OPTS=--dir-overflow-policy=...`；构造顺序是内建 gem5 flags -> `EP_GEM5_OPTS` -> metadata，以及内建 UBIO extra -> `UBCC_OPTS` -> metadata（`run_multi.sh:709-745,782-785`）。所以只有矩阵路径会出现同名重复项；命令行按左到右保留。矩阵传入值与内建目标一致。TC210-TC227 没有内建 gem5 profile flags，formal/其他矩阵的 gem5 只出现一组；但 UBIO policy 会出现两次。不要把这种重复描述成普通 direct run 的默认行为。

### verifier 输入契约

真实调用为 `python3 tests/e2e/verify.py --tc=TC --simout` 加全部 N 个 `$RUN/tcTC/m5out/noden/simout_n`，再加全部 N*K 个 `$LOG/ubio_tcTC_nn_ss/stdout.log`（`run_multi.sh:1007-1047`）。**UBIO 只传 stdout，不传 stderr。**原因是 backend 日志语义把 Warn/Error 同文镜像到 stdout 与 stderr（`framework/Log.cc:108-123`；契约测试 `framework/tests/iface_contract_test.cc:57-87,201`），若两边都传会重复证据。

`verify.py:39-86` 完整读取所有 simout，只从 UBIO stdout 保留白名单：UBFAULT trigger/deliver、ResidentDir/UBCC stats、naive evict、policy/manifest、BATCH-RS/SILENT/C4/DIRECT-FWD、waiter/backstore/spill/fill/miss/replay、upgrade/clear/writeback、BACKSTORE read/write、EvictReq、shared-release 等。任一 simout 缺失立即失败（`:88-95`）。TC131-TC134 检测到 naive 后禁止 `BACKSTORE-WRITE`、`BACKSTORE-READ`、`RESIDENT-SPILL-` 和非零 `backstoreIndex`（`:99-132`）。最终 sentinel 必须是日志最后一行的精确 PASS 文本。

## 矩阵 runner 精确目录

| runner | scope/topology | env 与有效 argv | concurrency/CPU/timeout | outputs/retry/caveats |
|---|---|---|---|---|
| `run_tc90_perf_matrix.sh` | 本文覆盖 120-124,130-132=`--1s`；125-129 仅 spill；133=`--8n1s`；134=`--8n2s`；210-216,218-219=`--2n1s`（`:44-75`） | 三 profile 设置 `EP_PERF_PROFILE`、`UBCC_POLICY`、`EP_GEM5_OPTS`、重复同值 `UBCC_OPTS`；trace off，supervisor on（`:15-36`） | 串行；CASE=3600，stall=600；无 outer subprocess timeout | `matrix.tsv`、最终 summary；首个失败停止；125-129 naive 明确 SKIP；脚本没有 217/220-227 |
| `run_tc90_phase_timer_matrix.sh` | 本文覆盖 120-124、125-129、210-216、218-219；拓扑与上项一致（`:62-72`） | 参数注入与上项相同 | 串行；3600/600；无 outer | 首失败停止；matrix+summary；用途是 phase timer 重测，不是全注册表 |
| `run_tc90_default_sweep.sh` | 本文覆盖 120-132=`--1s`、133=`--8n1s`、134=`--8n2s`、210-216,218-219=`--2n1s`（`:40-43`） | 不设置 profile/policy/gem5 opts；因此使用普通 runner 默认，不产生矩阵重复 flags | 串行；CASE=3600，stall=600；无 outer | `sweep.tsv`；首失败停止；名字虽称全部 TC>=90，当前列表并不含 135-147、217、220-227 |
| `run_tc135_perf_matrix.sh` | 135-140，固定 `--1s`，默认 6x3（`:70-75`） | 三 profile；matrix UBIO policy 重复；内建 gem5 + matrix gem5 重复（`:17-50`） | 串行；CASE=3600，stall=600；继续执行失败项 | `matrix.tsv`、summary；每 case 二次检查 verifier 最后一行；无 retry；exit 为失败数 |
| `run_database_perf_matrix.sh` | 142-147；默认 `1s 2s 8n1s 8n2s` x spill-noopt（`:71-78`） | legacy 模式，不设 `PORTABLE_512K_DIR`；矩阵 gem5/policy 产生同值重复（`:27-54`） | 串行；CASE=3600，stall=600，disk floor默认50GiB；无 outer | `matrix.tsv`、summary；继续失败项；可用列表 env 扩轴；不是 512K qualification |
| `run_p0_512k_matrix.py` | legacy 131-134 canonical；portable 142-147 x 3N1S(可选)+2N1S/3N2S/8N1S/8N2S，默认三 profile、150%（`:27-53,86-106`） | portable 设置 `PORTABLE_512K_DIR=1` 和五个精确压力宏；全部设置 profile/policy/gem5，policy 重复（`:169-180,233-263`） | 默认并发5；可选 CPU_SETS，否则 unrestricted；8N2S最多2、8N1S最多3；CASE=10800，stall=1800，outer=CASE+300；磁盘低于80GiB暂停（`:22-25,265-275,318-327,372-409`） | 每 case `result.json`、runner stdout；全局 manifest/matrix/progress/heartbeat；已 PASS 自动跳过，失败可由再次运行协调器重做，因此具恢复性但单次 case 无内部 retry；前后停删同名容器；verify sentinel 后再查 pressure manifest |
| `run_p0_512k_three_rounds.sh` | round1 131-134；round2 portable 2N1S/3N1S/3N2S；round3 portable 8N1S/8N2S（`:41-54`） | 透传三 profile、150%、CASE/STALL；调用上项 | 每轮 `MAX_PARALLEL=3`，CPU affinity unrestricted；三轮串行 | 每轮 coordinator.log+summary+通知；round 失败仍继续后续轮；底层 PASS 恢复语义；通知 URL 有默认公网地址，应按环境覆盖 |
| `run_ha_formal_150_matrix.py` | **仅 210-221**，2N1S，3x12=36（`:17-19,134`） | `WORKLOAD_CFLAGS=-DHA_FORMAL_CAPACITY_LINES=768`；三 profile；UBIO policy 同值重复；trace off（`:52-91`） | 默认并发5，CPU slots 0-5...24-29；CASE=10800，实际 min(CASE,deadline余量-60)，outer+120；stall=600（`:20-29,62-104`） | **每 case 一轮、无 retry**；matrix/progress/heartbeat/runner stdout；默认 deadline 已过期，必须覆盖；PASS 后 exact postcheck：唯一 formal capacity，TC210-219 scenario=FORMAL150，220=HA11，221=HA12，resident=512,unique=768,ratio=1.5（`:105-131`）；SKIP 不计 failure，验收须要求36 PASS、0 SKIP |

## 可直接复制的操作配方

每段先显式赋值，不留占位符。普通 direct run 不设置 `UBCC_OPTS`，因此没有 UBIO 重复 policy。

### TC120 naive、spill-noopt、optimized

```bash
ROOT=/mnt/data2/cgc/cc-ep; TAG=tc120_manual_20260806; mkdir -p "$ROOT/logs/$TAG"; \
env E2E_RUN_ID=${TAG}_naive LOG_BASE="$ROOT/logs/$TAG/naive" TIMEOUT_SEC=3600 EP_SUPERVISOR=1 EP_SUPERVISOR_INTERVAL=60 EP_SUPERVISOR_PROGRESS_STALL_SEC=600 EP_TRACE_PERF=off EP_PERF_PROFILE=naive UBCC_POLICY=naive EP_GEM5_OPTS='--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0' bash "$ROOT/tests/e2e/run_multi.sh" --1s 120
env E2E_RUN_ID=${TAG}_spill_noopt LOG_BASE="$ROOT/logs/$TAG/spill-noopt" TIMEOUT_SEC=3600 EP_SUPERVISOR=1 EP_SUPERVISOR_INTERVAL=60 EP_SUPERVISOR_PROGRESS_STALL_SEC=600 EP_TRACE_PERF=off EP_PERF_PROFILE=spill-noopt UBCC_POLICY=spill EP_GEM5_OPTS='--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0' bash "$ROOT/tests/e2e/run_multi.sh" --1s 120
env E2E_RUN_ID=${TAG}_optimized LOG_BASE="$ROOT/logs/$TAG/optimized" TIMEOUT_SEC=3600 EP_SUPERVISOR=1 EP_SUPERVISOR_INTERVAL=60 EP_SUPERVISOR_PROGRESS_STALL_SEC=600 EP_TRACE_PERF=off EP_PERF_PROFILE=optimized UBCC_POLICY=spill EP_GEM5_OPTS='--silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1' bash "$ROOT/tests/e2e/run_multi.sh" --1s 120
```

### TC131 naive 与 spill optimized

```bash
ROOT=/mnt/data2/cgc/cc-ep; TAG=tc131_manual_20260806; mkdir -p "$ROOT/logs/$TAG"; \
env E2E_RUN_ID=${TAG}_naive LOG_BASE="$ROOT/logs/$TAG/naive" TIMEOUT_SEC=10800 EP_SUPERVISOR=1 EP_SUPERVISOR_INTERVAL=60 EP_SUPERVISOR_PROGRESS_STALL_SEC=1800 EP_TRACE_PERF=off EP_PERF_PROFILE=naive UBCC_POLICY=naive EP_GEM5_OPTS='--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0' bash "$ROOT/tests/e2e/run_multi.sh" --8n1s 131
env E2E_RUN_ID=${TAG}_optimized LOG_BASE="$ROOT/logs/$TAG/optimized" TIMEOUT_SEC=10800 EP_SUPERVISOR=1 EP_SUPERVISOR_INTERVAL=60 EP_SUPERVISOR_PROGRESS_STALL_SEC=1800 EP_TRACE_PERF=off EP_PERF_PROFILE=optimized UBCC_POLICY=spill EP_GEM5_OPTS='--silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1' bash "$ROOT/tests/e2e/run_multi.sh" --8n1s 131
```

### TC134 optimized 8N2S

```bash
ROOT=/mnt/data2/cgc/cc-ep; TAG=tc134_8n2s_opt_20260806; mkdir -p "$ROOT/logs/$TAG"; \
env E2E_RUN_ID=$TAG LOG_BASE="$ROOT/logs/$TAG" TIMEOUT_SEC=10800 EP_SUPERVISOR=1 EP_SUPERVISOR_INTERVAL=60 EP_SUPERVISOR_PROGRESS_STALL_SEC=1800 EP_TRACE_PERF=off EP_PERF_PROFILE=optimized UBCC_POLICY=spill EP_GEM5_OPTS='--silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1' bash "$ROOT/tests/e2e/run_multi.sh" --8n2s 134
```

### TC142 portable512K 3N1S 与 8N2S（精确压力值）

```bash
ROOT=/mnt/data2/cgc/cc-ep; TAG=tc142_p150_20260806; mkdir -p "$ROOT/logs/$TAG"; \
env E2E_RUN_ID=${TAG}_3n1s LOG_BASE="$ROOT/logs/$TAG/3n1s" TIMEOUT_SEC=10800 EP_SUPERVISOR=1 EP_SUPERVISOR_INTERVAL=60 EP_SUPERVISOR_PROGRESS_STALL_SEC=1800 EP_TRACE_PERF=off EP_PERF_PROFILE=optimized UBCC_POLICY=spill EP_GEM5_OPTS='--silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1' PORTABLE_512K_DIR=1 WORKLOAD_CFLAGS='-DPORTABLE_PRESSURE_LINES=98208 -DPORTABLE_TARGET_FOOTPRINT_LINES=98304 -DPORTABLE_NAIVE_CAPACITY_LINES=65536 -DPORTABLE_PRESSURE_LEVEL_PCT=150 -DPORTABLE_BATCHES=32' bash "$ROOT/tests/e2e/run_multi.sh" --1s 142
env E2E_RUN_ID=${TAG}_8n2s LOG_BASE="$ROOT/logs/$TAG/8n2s" TIMEOUT_SEC=10800 EP_SUPERVISOR=1 EP_SUPERVISOR_INTERVAL=60 EP_SUPERVISOR_PROGRESS_STALL_SEC=1800 EP_TRACE_PERF=off EP_PERF_PROFILE=optimized UBCC_POLICY=spill EP_GEM5_OPTS='--silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1' PORTABLE_512K_DIR=1 WORKLOAD_CFLAGS='-DPORTABLE_PRESSURE_LINES=97792 -DPORTABLE_TARGET_FOOTPRINT_LINES=98304 -DPORTABLE_NAIVE_CAPACITY_LINES=65536 -DPORTABLE_PRESSURE_LEVEL_PCT=150 -DPORTABLE_BATCHES=32' bash "$ROOT/tests/e2e/run_multi.sh" --8n2s 142
```

### TC210 formal-like 三个 profile

```bash
ROOT=/mnt/data2/cgc/cc-ep; TAG=tc210_formal_like_20260806; mkdir -p "$ROOT/logs/$TAG"; \
env E2E_RUN_ID=${TAG}_naive LOG_BASE="$ROOT/logs/$TAG/naive" TIMEOUT_SEC=10800 EP_SUPERVISOR=1 EP_SUPERVISOR_INTERVAL=60 EP_SUPERVISOR_PROGRESS_STALL_SEC=600 EP_TRACE_PERF=off EP_PERF_PROFILE=naive UBCC_POLICY=naive EP_GEM5_OPTS='--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0' WORKLOAD_CFLAGS='-DHA_FORMAL_CAPACITY_LINES=768' bash "$ROOT/tests/e2e/run_multi.sh" --2n1s 210
env E2E_RUN_ID=${TAG}_spill_noopt LOG_BASE="$ROOT/logs/$TAG/spill-noopt" TIMEOUT_SEC=10800 EP_SUPERVISOR=1 EP_SUPERVISOR_INTERVAL=60 EP_SUPERVISOR_PROGRESS_STALL_SEC=600 EP_TRACE_PERF=off EP_PERF_PROFILE=spill-noopt UBCC_POLICY=spill EP_GEM5_OPTS='--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0' WORKLOAD_CFLAGS='-DHA_FORMAL_CAPACITY_LINES=768' bash "$ROOT/tests/e2e/run_multi.sh" --2n1s 210
env E2E_RUN_ID=${TAG}_optimized LOG_BASE="$ROOT/logs/$TAG/optimized" TIMEOUT_SEC=10800 EP_SUPERVISOR=1 EP_SUPERVISOR_INTERVAL=60 EP_SUPERVISOR_PROGRESS_STALL_SEC=600 EP_TRACE_PERF=off EP_PERF_PROFILE=optimized UBCC_POLICY=spill EP_GEM5_OPTS='--silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1' WORKLOAD_CFLAGS='-DHA_FORMAL_CAPACITY_LINES=768' bash "$ROOT/tests/e2e/run_multi.sh" --2n1s 210
```

### TC224 compact 与 full

```bash
ROOT=/mnt/data2/cgc/cc-ep; TAG=tc224_20260806; mkdir -p "$ROOT/logs/$TAG"; \
env E2E_RUN_ID=${TAG}_compact LOG_BASE="$ROOT/logs/$TAG/compact" TIMEOUT_SEC=5400 EP_SUPERVISOR=1 EP_SUPERVISOR_INTERVAL=60 EP_SUPERVISOR_PROGRESS_STALL_SEC=900 EP_TRACE_PERF=off EP_PERF_PROFILE=optimized UBCC_POLICY=spill EP_GEM5_OPTS='--silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1' WORKLOAD_CFLAGS='-DC224_ACTIVE_LINES=512 -DC224_PRESSURE_LINES=4096 -DC224_READ_STRIDE=64' bash "$ROOT/tests/e2e/run_multi.sh" --2n1s 224
env E2E_RUN_ID=${TAG}_full LOG_BASE="$ROOT/logs/$TAG/full" TIMEOUT_SEC=28800 EP_SUPERVISOR=1 EP_SUPERVISOR_INTERVAL=60 EP_SUPERVISOR_PROGRESS_STALL_SEC=1800 EP_TRACE_PERF=off EP_PERF_PROFILE=optimized UBCC_POLICY=spill EP_GEM5_OPTS='--silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1' bash "$ROOT/tests/e2e/run_multi.sh" --2n1s 224
```

## 逐 TC 详细契约

## TC120 - baseline performance mix

注册行 `test_e2e.py:106`；源码常量 LINES=12、HOT=6、BASE=0x12000000、stride=0x10000。绑定：N=3,K=1；naive matrix `GTAIL=GON,UTAIL=U5000N + --dir-overflow-policy=naive`；spill-noopt `GTAIL=GNN,UTAIL=U5000S + --dir-overflow-policy=spill`；optimized `GTAIL=GOO,UTAIL=U5000O + --dir-overflow-policy=spill`。额外 policy 是矩阵 `UBCC_OPTS`，普通 direct run 分别只有 GO/GN/GO 与 U5000N/S/O。naive 的内建首组 GO 是因为 `run_multi.sh:725-735` 不把字符串 naive 归入 baseline，随后 matrix GN 位于后方。

node0 写12条并完成 `populate`；node1 四轮读6 hot，首轮 i=0,3 发2条 READ_VAL，并发24-op `shared_hot_reads` timer；node2 更新偶数 hot、`owner_migration`；node1 复读并再发2条 READ_VAL，完成 `tc120_done`（workload `:39-86`）。`verify_tc120`（`test_e2e.py:1584-1594`）只硬性拒绝 MISMATCH；READ_VAL 可以为0，done/stats仅报告。输入3 simout+3 stdout。

## TC121 - cold streaming overflow

注册 `test_e2e.py:107`；N=3,K=1，无宏，默认 LINES=64。naive matrix 明确绑定 `GTAIL=GON,UTAIL=U5000N + --dir-overflow-policy=naive`；spill-noopt 为 `GNN,U5000S + --dir-overflow-policy=spill`；optimized 为 `GOO,U5000O + --dir-overflow-policy=spill`；普通 direct 分别为 GO/GN/GO 与 U5000N/S/O，来源 `run_multi.sh:212-217,725-735`。node0 写64冲突线；node1 每4条抽样，共16 READ_VAL和16 `[LATENCY]`，发16-op timer；两次 barrier（workload `:47-71`）。`verify_perf_workload` 要求 reads>=1、全 MATCH、done phase>=2（`test_e2e.py:1597-1615`）。

## TC122 - hot reuse after directory eviction

注册 `test_e2e.py:108`；N=3,K=1；HOT=24,COLD=128。naive `GTAIL=GON,UTAIL=U6144N + naive policy`；spill-noopt `GNN,U6144S + spill policy`；optimized `GOO,U6144O + spill policy`。普通 direct 去掉每个末尾重复 policy。node0 hot populate；node1 24 reads、3 READ_VAL、timer；node0 128 cold pressure；node2 24 hot reuse、3 READ_VAL、timer（workload `:22-61`）。verifier 要 reads>=1、全 MATCH、done phases>=2。

## TC123 - shared hotset periodic upgrade

注册 `test_e2e.py:109`；N=3,K=1；HOT=16,COLD=96。naive matrix 明确绑定 `GTAIL=GON,UTAIL=U6144N + --dir-overflow-policy=naive`；spill-noopt 为 `GNN,U6144S + --dir-overflow-policy=spill`；optimized 为 `GOO,U6144O + --dir-overflow-policy=spill`；普通 direct 分别为 GO/GN/GO 与 U6144N/S/O。node0 初始化16；node1/2各读16并各抽样2条；node0写96 cold；node1每4条升级；node2校验4条。五 phase 为 `init_hot,shared_read,dir_pressure,periodic_upgrade,verify_upgrade`（workload `:22-65`）。verifier 条件为至少1 read、全 MATCH、done phase>=2。

## TC124 - owner/home/requester split

注册 `test_e2e.py:110`；N=3,K=1；LINES=32。naive `GTAIL=GON,UTAIL=UBN`，spill-noopt `GNN,UBS`，optimized `GOO,UBO`；matrix 时 UTAIL 最后再重复同值 policy。所有 profile direct-fwd=0（`run_multi.sh:226-230,725-741`）。node2 向 home1 写32；node0读32并发4 READ_VAL与32-op timer（workload `:19-37`）。verifier 要 reads>=1、全 MATCH、done phase>=2。

## TC125 - read offload/onload

注册 `test_e2e.py:111`；N=3,K=1。普通 direct spill-noopt `GTAIL=G0,UTAIL=UH64S`，optimized `G0,UH64O`；性能矩阵追加后分别为 GN/GO，UBIO 再追加同值 spill policy。naive 在矩阵明确 SKIP（`run_tc90_perf_matrix.sh:54-61`）。阶段为 init V0；node1/2共享读；drain；node0写2 cold；node1本地L2驱逐后 onload V0；node2写V1；node0读V1（workload `:75-132`）。`verify_tc125` 要全 MATCH、reads>=4、node1 V0、node0 V1，并要求 target spill 或 dirty-persist+safe-remove，以及 fill-issued（`test_e2e.py:2132-2195`）。

## TC126 - resident waiter upgrade replay

注册 `test_e2e.py:112`；N=3,K=1；普通 direct spill-noopt 明确绑定 `GTAIL=G0,UTAIL=UH64S`，optimized 为 `G0,UH64O`；矩阵绑定分别为 `GN,UH64S + --dir-overflow-policy=spill` 和 `GO,UH64O + --dir-overflow-policy=spill`；naive SKIP。node0 V0；node1/2共享；drain；2 cold 引发 spill；node1 upgrade V1；node2终读（workload `:67-120`）。`verify_tc126` 要 node2 最终0x1260BEEF、全 MATCH、target waiter enqueue opKind=1、spill/fill、upgrade commit恰1、replay-upgrade-queued恰1、outer req=1 不超过1（`test_e2e.py:2053-2129`）。

## TC127 - writeback offload/onload

注册 `test_e2e.py:113`；N=3,K=1；普通 direct spill-noopt 明确绑定 `GTAIL=G0,UTAIL=UH64S`，optimized 为 `G0,UH64O`；矩阵分别为 `GN,UH64S + spill policy` 和 `GO,UH64O + spill policy`；naive SKIP。node0 dirty V0，2 cold，flush，node1/2各读（workload `:52-92`）。verifier 要 reads>=2、值均为0x1270C0DE、spill/remove、fill、WB request 和 `WB-DATA-PERSIST`（`test_e2e.py:2198-2256`）。

## TC128 - clean evict offload/onload

注册 `test_e2e.py:114`；N=3,K=1；普通 direct spill-noopt 明确绑定 `GTAIL=G0,UTAIL=UH64S`，optimized 为 `G0,UH64O`；矩阵分别为 `GN,UH64S + spill policy` 和 `GO,UH64O + spill policy`；naive SKIP。内建 timeout 下界1800（`run_multi.sh:617-631`）。node0 V0；三 node shared；drain；cold pressure；node1 flush clean copy并计时重读（workload `:55-106`）。verifier 要 reads>=2、值正确、spill/remove 和 fill；EvictReq 缺失只诊断（`test_e2e.py:2259-2311`）。

## TC129 - two spill/fill cycles

注册 `test_e2e.py:115`；N=3,K=1；普通 direct spill-noopt 明确绑定 `GTAIL=G0,UTAIL=UH64S`，optimized 为 `G0,UH64O`；矩阵分别为 `GN,UH64S + spill policy` 和 `GO,UH64O + spill policy`；naive SKIP。V0 -> spill1 -> node1 onload -> node1 V1 -> spill2 -> node2 onload -> node0终读（workload `:56-118`）。verifier 要 reads>=3、node1 V0、node2/node0 V1、spill/remove总数>=2、fill>=2（`test_e2e.py:2314-2371`）。

## TC130 - high-footprint overflow benchmark

注册 `test_e2e.py:116`；N=3,K=1；HOT=24,PRESSURE=192,ROUNDS=4。naive `GTAIL=GNN,UTAIL=U5000N+naive policy`；spill-noopt `GNN,U5000S+spill policy`；optimized `GOO,U5000O+spill policy`。普通 direct 仅一组 G 和单 policy。阶段 selftest、hot populate/share、192 pressure、4轮复用，首轮24 READ_VAL，96-op timer（workload `:31-68`）。verifier 要 reads>=24、全 MATCH、四 phase、有效 selftest（`test_e2e.py:1630-1657`）。

## TC131 - real-capacity catalog fullscan

注册 `test_e2e.py:117`；canonical N=8,K=1；HOT=4096,PRESSURE=98304。naive matrix `GTAIL=GNN,UTAIL=U512KN+naive policy`；spill-noopt `GNN,U512KS+spill policy`；optimized `GOO,U512KS+spill policy`，注意 TC131 UBIO batch 固定0，policy 只由 `UBCC_POLICY` 控制（`run_multi.sh:268-282`）。普通 direct 为单 GN/GO 与单 U512KN/S。node0 seed；node1/2全读；node0 pressure；node1/2两遍复用共16 READ_VAL、8192-op timers；node1跨L2 256 upgrades（workload `:23-83`）。verifier 要 reads>=8、五 phase、selftest；naive 另受禁止 spill/backstore 前置检查。内建 timeout 7200。

## TC132 - dirty checkpoint stream

注册 `test_e2e.py:118`；canonical N=3,K=1；默认 ACTIVE=8192,PRESSURE=65536,STRIDE=512。三 profile G/U 绑定与 TC131 相同。node1 seed8192；node0写65536；node2读8192，每512一条，共16 reads，8192-op recover timer（workload `:20-24`）。verifier 要16 reads、三 phase、selftest；naive禁止项生效。这里 checkpoint 是 workload phase 名。

## TC133 - 8N1S shared frontier

注册 `test_e2e.py:119`；强制 N=8,K=1（`run_multi.sh:1075-1081`）；HOT=4096,PRESSURE=65536。profile argv 与 TC131 相同。node0 seed/pressure；node1..7 share/reuse，各发1 READ_VAL，合计7（workload `:13-18`）。verifier 要 reads>=7、四 phase、selftest；naive禁止项生效。

## TC134 - 8N2S sliding window

注册 `test_e2e.py:120`；强制 N=8,K=2；P=16。naive `GTAIL=GNN,UTAIL=U512KN+naive policy`；spill-noopt `GNN,U512KS+spill policy`；optimized `GOO,U512KS+spill policy`，每个 `(node,socket)` 均同尾串。plane0 seed；所有 socket1 share；8个 socket0 各写8192；socket1 reuse，node1..7共7 reads，每个 socket1 发4096-op timer（workload `:15-20`）。verifier 要 reads>=7、四 phase、selftest；输入8 simout+16 stdout；内建 timeout7200。

## TC135 - preserved sharer revisit

注册 `test_e2e.py:121`；N=3,K=1；HOT=24,PRESSURE=192。naive `GTAIL=GNN,UTAIL=U5000N+naive policy`；spill-noopt `GNN,U5000S+spill policy`；optimized `GOO,U5000O+spill policy`。node0 seed；node1 share并发24 reads；pressure；node1测24 samples并再发24 reads（workload `:32-71`）。verifier 要总 reads=48、全 node1、四 phase、selftest、唯一 latency marker samples=24且分位/均值合法（`test_e2e.py:1697-1765`）。

## TC136 - preserved owner store

注册 `test_e2e.py:122`；N=3,K=1；naive matrix 明确绑定 `GTAIL=GNN,UTAIL=U5000N + naive policy`；spill-noopt 为 `GNN,U5000S + spill policy`；optimized 为 `GOO,U5000O + spill policy`；普通 direct 为 GN/GN/GO 与 U5000N/S/O。node1 dirty seed；node0 pressure；node1 24 owner stores；node2读24终值（workload `:31-63`）。verifier 要 reads=24且全 node2、四 phase、selftest、唯一 samples=24 marker（`test_e2e.py:1768-1772`）。

## TC137 - new requester load

注册 `test_e2e.py:123`；N=3,K=1；naive matrix 明确绑定 `GTAIL=GNN,UTAIL=U5000N + naive policy`；spill-noopt 为 `GNN,U5000S + spill policy`；optimized 为 `GOO,U5000O + spill policy`；普通 direct 为 GN/GN/GO 与 U5000N/S/O。node0 seed；node1 share 24 reads；pressure；node2测首次load并发24 reads（workload `:30-64`）。verifier 要 reads=48，node1=24,node2=24，唯一 node2 latency samples=24（`test_e2e.py:1775-1779`）。

## TC138 - dirty owner handoff store

注册 `test_e2e.py:124`；N=3,K=1；naive matrix 明确绑定 `GTAIL=GNN,UTAIL=U5000N + naive policy`；spill-noopt 为 `GNN,U5000S + spill policy`；optimized 为 `GOO,U5000O + spill policy`；普通 direct 为 GN/GN/GO 与 U5000N/S/O。node1 dirty seed；node0 pressure；node2 24 handoff stores；node0读24终值（workload `:31-63`）。verifier 要24 reads全 node0、四 phase、唯一 node2 samples=24（`test_e2e.py:1782-1786`）。

## TC139 - mixed batch throughput

注册 `test_e2e.py:125`；N=3,K=1；naive matrix 明确绑定 `GTAIL=GNN,UTAIL=U5000N + naive policy`；spill-noopt 为 `GNN,U5000S + spill policy`；optimized 为 `GOO,U5000O + spill policy`；普通 direct 为 GN/GN/GO 与 U5000N/S/O。node0 seed16；node1 share16、取 owner；pressure；node1 16x16=256 ops，256-op timer、16 samples；node2终验8 reads（workload `:31-90`）。verifier 要 reads=24（node1=16,node2=8）、六 phase、throughput timer、唯一 samples=16（`test_e2e.py:1789-1795`）。

## TC140 - cross-L2 owner store

注册 `test_e2e.py:126`；N=3,K=1。naive matrix `GTAIL=GON,UTAIL=UBN`；spill-noopt `GNN,UBS`；optimized `GOO,UBO`，matrix UBIO policy 位于 batch 后。普通 direct 分别 GO/GN/GO 和仅 `--batch-rs=0|0|1`，若不设 `UBCC_OPTS` 则 policy 使用 UBIO 默认。node0 两个 L2 cluster 协作24 stores；node2读24（workload `:53-84`）。verifier 要 reads=24全 node2、`cross_l2_store,verify_final`、selftest、唯一 node0 samples=24（`test_e2e.py:1798-1802`）。

## TC141 - spill shared-to-writer recovery

注册 `test_e2e.py:127`；强制 N=3,K=1。spill-noopt `GTAIL=GNN,UTAIL=U5000S+spill policy`；optimized `GOO,U5000O+spill policy`；普通 direct 去掉重复组。naive 不属于语义矩阵，因为 verifier 强制 spill 证据。node0 seed16；node1 share16；pressure192；node1写16；node2验16（workload `:26-72`）。verifier 要 reads=32、node1/node2各16、五 phase、spill-done/fill-done/shared-release，且禁止 waiter drop-not-sharer（`test_e2e.py:1805-1832`）。

## TC142 - portable OLTP buffer pool

注册 `test_e2e.py:128`。可用拓扑绑定为 2N1S(N=2,K=1,P=2)、3N1S(3,1,3)、3N2S(3,2,6)、8N1S(8,1,8)、8N2S(8,2,16)。legacy profile：naive `GTAIL=GNN,UTAIL=UTINYN+naive policy`；spill-noopt `GNN,UTINYS+spill policy`；optimized `GOO,UTINYO+spill policy`。portable512K profile 把 UTAIL 改为 U512KN/S/O 加同值 policy，GTAIL不变；普通 direct 各减少一组重复 G 和 policy。

HOT/plane=32，32 batches x32=1024 ops；五 phase，reads=5P，service/end-to-end timers各1024，latency samples=32（workload `:4-81`）。legacy 不加规模宏，header 默认 pressure=768,target=0,capacity=65536,pct=0,batches=32。`PORTABLE_512K_DIR=1` 时五宏为 target=98304,capacity=65536,pct=150,batches=32，pressure 按 3N1S/2N1S/3N2S/8N1S/8N2S 依次为 **98208/98240/98112/98048/97792**。

portable verifier（`test_e2e.py:1835-1972`）严格检查 participant/topology/plane集合、每 plane pressure config、total=hot+pressure、非零 target/pct 等式、reads=5P且每 plane=5、每 phase、每种 timer 恰1且1024 ops、e2e>=service、每 plane latency恰1且samples=32和统计合法。

## TC143 - portable B-tree traversal

注册 `test_e2e.py:129`；明确支持 N/K/P=2/1/2、3/1/3、3/2/6、8/1/8、8/2/16。legacy naive/spill-noopt/optimized 明确绑定 `GTAIL=GNN/GNN/GOO`、`UTAIL=UTINYN+naive policy / UTINYS+spill policy / UTINYO+spill policy`；512K 将三项 UTAIL 明确替换为 `U512KN+naive policy / U512KS+spill policy / U512KO+spill policy`。HOT/plane=137；五 phase；reads=5P；2048 ops；latency samples=32（workload `:4-95`）。512K pressure 按 3N1S/2N1S/3N2S/8N1S/8N2S 为 **97893/98030/97482/97208/96112**，其余四宏固定 98304/65536/150/32。verifier 使用同一严格结构，绑定 B-tree phase/timer 名和2048 ops（`test_e2e.py:1975-1981`）。

## TC144 - portable WAL checkpoint

注册 `test_e2e.py:130`；明确支持 N/K/P=2/1/2、3/1/3、3/2/6、8/1/8、8/2/16。legacy 三 profile 明确绑定 `GTAIL=GNN/GNN/GOO`、`UTAIL=UTINYN+naive policy / UTINYS+spill policy / UTINYO+spill policy`；512K 明确绑定同一 GTAIL 与 `U512KN+naive policy / U512KS+spill policy / U512KO+spill policy`。HOT/plane=192；五 phase；reads=17P；1024 ops；samples=32（workload `:4-86`）。512K pressure 为 **97728/97920/97152/96768/95232**。verifier 绑定 WAL phase/timer 名、17P reads、1024 ops（`test_e2e.py:1984-1990`）。这里 checkpoint 仍是 workload 语义，不表示 simulator checkpoint。

## TC145 - portable FaaS warm invocation

注册 `test_e2e.py:131`；明确支持 N/K/P=2/1/2、3/1/3、3/2/6、8/1/8、8/2/16。legacy 三 profile 明确绑定 `GTAIL=GNN/GNN/GOO`、`UTAIL=UTINYN+naive policy / UTINYS+spill policy / UTINYO+spill policy`；512K 明确绑定同一 GTAIL 与 `U512KN+naive policy / U512KS+spill policy / U512KO+spill policy`。HOT/plane=136；四 phase；reads=9P；2048 ops；samples=32（workload `:4-87`）。512K pressure 为 **97896/98032/97488/97216/96128**。verifier 绑定 FaaS marker（`test_e2e.py:1993-1998`）。

## TC146 - portable graph frontier

注册 `test_e2e.py:132`；明确支持 N/K/P=2/1/2、3/1/3、3/2/6、8/1/8、8/2/16。legacy 三 profile 明确绑定 `GTAIL=GNN/GNN/GOO`、`UTAIL=UTINYN+naive policy / UTINYS+spill policy / UTINYO+spill policy`；512K 明确绑定同一 GTAIL 与 `U512KN+naive policy / U512KS+spill policy / U512KO+spill policy`。HOT/plane=192；四 phase；reads=5P；2048 ops；samples=32（workload `:4-90`）。512K pressure 为 **97728/97920/97152/96768/95232**。verifier 绑定 graph marker（`test_e2e.py:2001-2006`）。

## TC147 - portable feature store

注册 `test_e2e.py:133`；明确支持 N/K/P=2/1/2、3/1/3、3/2/6、8/1/8、8/2/16。legacy 三 profile 明确绑定 `GTAIL=GNN/GNN/GOO`、`UTAIL=UTINYN+naive policy / UTINYS+spill policy / UTINYO+spill policy`；512K 明确绑定同一 GTAIL 与 `U512KN+naive policy / U512KS+spill policy / U512KO+spill policy`。HOT/plane=136；四 phase；reads=9P；2048 ops；samples=32（workload `:4-81`）。512K pressure 为 **97896/98032/97488/97216/96128**。verifier 绑定 feature marker（`test_e2e.py:2009-2015`）。

### TC142-TC147 legacy 与 portable512K 的边界

legacy `run_database_perf_matrix.sh` 不设置 `PORTABLE_512K_DIR`，使用 tiny directory UTAIL 和 header 默认 pressure；P0 设置 `PORTABLE_512K_DIR=1`，使用512KiB ResidentDir 与精确五宏。150% 目标按所有 plane 的 hot 总量计算：`pressure=98304-HOT_PER_PLANE*P`（`run_p0_512k_matrix.py:169-180`）。因此压力宏不可跨 topology 复用。所有 topology 的精确 pressure 已在六个 TC 节列出；2N1S/3N1S/3N2S/8N1S/8N2S 的 P 分别2/3/6/8/16。

## TC210 - HA01 Local Reuse

注册 `test_e2e.py:150`；N=2,K=1；普通编译 `-DHA_SCENARIO=1`，formal150 再在 include 前加 `-DHA_FORMAL_CAPACITY_LINES=768`。ordinary naive/spill-noopt/optimized 分别绑定 `GTAIL=GN/GN/GO`，`UTAIL=UTINYN/UTINYS/UTINYO`；formal matrix GTAIL相同，UTAIL在 metadata 前再追加同值 policy。offset=0x6100；node0 写0x101并计时本地 load，node1同步；无 READ_VAL（core `:147-154`）。共同 verifier 只强制两 node 零错误 validation；formal postcheck 另要求唯一 FORMAL150 512/768/1.5。

## TC211 - HA02 Remote Read

注册 `test_e2e.py:151`；N=2,K=1；宏 `HA_SCENARIO=2`；ordinary naive/spill-noopt/optimized 明确绑定 `GTAIL=GN/GN/GO`、`UTAIL=UTINYN/UTINYS/UTINYO`；formal matrix 的三项 UTAIL 分别在末尾追加同值 naive/spill/spill policy。offset=0x6200；node0写0x202，node1远端读并计时（core `:155-162`）。无 READ_VAL；共同 validation 是功能门禁；formal exact postcheck生效。

## TC212 - HA03 Ownership Handoff

注册 `test_e2e.py:152`；N=2,K=1；宏3；ordinary 三 profile 明确绑定 `GTAIL=GN/GN/GO`、`UTAIL=UTINYN/UTINYS/UTINYO`；formal matrix 分别追加同值 naive/spill/spill policy。node0写0x303；node1写0x304并发 `ownership_write`；node0读回并发 `ownership_readback`（core `:163-176`）。错误进入 validation，verifier不单独计 phase。

## TC213 - HA04 Shared To Writer

注册 `test_e2e.py:153`；N=2,K=1；宏4；ordinary 三 profile明确绑定 `GTAIL=GN/GN/GO`、`UTAIL=UTINYN/UTINYS/UTINYO`；formal matrix 分别追加同值 naive/spill/spill policy。node0写0x404；两 node各读并发 `shared_read`；node1写0x405；node0读回（core `:177-196`）。共同 validation 加 formal postcheck。

## TC214 - HA07 Producer Consumer Stream

注册 `test_e2e.py:154`；N=2,K=1；非顺序宏 `HA_SCENARIO=7`（`compile_workload.sh:63`）；ordinary 三 profile明确绑定 `GTAIL=GN/GN/GO`、`UTAIL=UTINYN/UTINYS/UTINYO`；formal matrix 分别追加同值 naive/spill/spill policy。16条 line 逐条 publish/ack，每项双 barrier；node0 producer=16 ops，node1 consumer=16（core `:232-249`）。不是整批写完再消费。

## TC215 - HA05 Clean Shared Victim Revisit

注册 `test_e2e.py:155`；N=2,K=1；宏5；ordinary 三 profile明确绑定 `GTAIL=GN/GN/GO`、`UTAIL=UTINYN/UTINYS/UTINYO`；formal matrix 分别追加同值 naive/spill/spill policy。node0 hot 0x505；node1首读；node0写640 pressure；node1连续64 revisit并终读检查（core `:197-213`）。普通场景的640条与 formal 的独立768-line precondition 都会执行；共同 verifier 不直接计640/64。

## TC216 - HA06 Dirty Owner Capacity Lifecycle

注册 `test_e2e.py:156`；N=2,K=1；宏6；ordinary 三 profile明确绑定 `GTAIL=GN/GN/GO`、`UTAIL=UTINYN/UTINYS/UTINYO`；formal matrix 分别追加同值 naive/spill/spill policy。node1 dirty owner 0x606；node0写640 pressure并发 admission=640；node1 revisit=64并检查（core `:214-231`）。formal 前置使用独立地址区。

## TC217 - HA10 Read-mostly Catalog

注册 `test_e2e.py:157`；N=2,K=1；宏10；ordinary 三 profile明确绑定 `GTAIL=GN/GN/GO`、`UTAIL=UTINYN/UTINYS/UTINYO`；formal matrix 分别追加同值 naive/spill/spill policy。16 keys，8 batches，每批80 pressure+16 useful ops；node1发8条 catalog_batch、128-op timer、samples=8；node0最终对 key1/key3 发2 READ_VAL（core `:287-365`）。`verify_tc217` 要共同 validation、reads=2全 MATCH、唯一 latency samples=8、timer operations=128且 ticks/frequency非0、iteration集合0..7（`test_e2e.py:2765-2803`）。formal 前置和 exact postcheck生效。

## TC218 - HA08 Barrier And Sequence-lock Handoff

注册 `test_e2e.py:158`；N=2,K=1；宏8；ordinary 三 profile明确绑定 `GTAIL=GN/GN/GO`、`UTAIL=UTINYN/UTINYS/UTINYO`；formal matrix 分别追加同值 naive/spill/spill policy。两 node先各16 barrier；再16轮奇偶 sequence handoff，各发16-op marker；末值32（core `:250-272`）。无 READ_VAL；逐轮错误进入 validation。

## TC219 - HA09 Concurrent Local And Remote Pressure

注册 `test_e2e.py:159`；N=2,K=1；宏9；ordinary 三 profile明确绑定 `GTAIL=GN/GN/GO`、`UTAIL=UTINYN/UTINYS/UTINYO`；formal matrix 分别追加同值 naive/spill/spill policy。node0无前置 barrier 连续64 stores覆盖16 lines；node1并发写16 remote pressure；末尾一次 barrier后node0检查（core `:273-286`）。无 READ_VAL；validation门禁。

## TC220 - HA11 Exact-150 Clean Capacity

注册 `test_e2e.py:160`；N=2,K=1；宏11；ordinary 三 profile明确绑定 `GTAIL=GN/GN/GO`、`UTAIL=UTINYN/UTINYS/UTINYO`；formal matrix 分别追加同值 naive/spill/spill policy。formal 宏即使存在也因 scenario>=11 跳过通用 precondition（core `:104-107`）。node0写64 hot；node1初读；node0写704 pressure并发 admission=704；node1 revisit64并发64 READ_VAL；node0发唯一 HA11 capacity：resident512,hot64,pressure704,unique768,ratio1.5,formal=true（core `:366-409`）。`verify_tc220` 要共同 validation、64 MATCH、capacity六字段 exact、timers 704/64（`test_e2e.py:2588-2637`）。

## TC221 - HA12 Exact-150 Dirty Capacity And Handoff

注册 `test_e2e.py:161`；N=2,K=1；宏12；ordinary 三 profile明确绑定 `GTAIL=GN/GN/GO`、`UTAIL=UTINYN/UTINYS/UTINYO`；formal matrix 分别追加同值 naive/spill/spill policy，且通用 precondition跳过。node1写64 hot；node0写704 pressure；node1 revisit前32；node0 handoff后32；node1读64并发 READ_VAL；唯一 HA12 exact capacity（core `:410-464`）。`verify_tc221` 要64 MATCH、capacity exact、timers 704/32/32（`test_e2e.py:2640-2645`）。

### TC210-TC221 formal150 共同语义

core 每 node仅 `cpu%4==0` 执行；先 E2E_META/selftest/manifest，最后 validation（core `:135-146,471-473`）。`verify_ha_2n1s` 要零错误 validation node集合恰为 `{0,1}`、scenario唯一；若有 formal capacity 记录则恰1条且 unique>=768（`test_e2e.py:2552-2585`）。通用 formal precondition 仅 `HA_FORMAL_CAPACITY_LINES!=0 && scenario<11`：node0写768 lines，node1查首尾，发 FORMAL150 capacity（core `:104-132`）。正式矩阵仅 TC210-TC221、每 case一轮无 retry，并执行前述 exact postcheck。

## TC222 - C123-HA Shared-to-writer Batch

注册 `test_e2e.py:162`；N=2,K=1；宏 `HA_CGROUP_SCENARIO=1`。三 profile ordinary `GTAIL=GN/GN/GO`，`UTAIL=UTINYN/UTINYS/UTINYO`；若由通用矩阵显式传 `UBCC_OPTS` 才重复 policy。node0 seed16；node1 share16；pressure96；node1对4条 store并发4-sample latency和4-op timer；node0发4 reads（C-group `:60-115`）。verifier 要2条 implemented manifest、2 validation、reads=4、timer=4、samples=4（`test_e2e.py:2648-2696`）。不属于 formal150。

## TC223 - C130-HA Overflow Hot Reuse

注册 `test_e2e.py:163`；N=2,K=1；宏2；ordinary naive/spill-noopt/optimized 明确绑定 `GTAIL=GN/GN/GO`、`UTAIL=UTINYN/UTINYS/UTINYO`；矩阵显式设置 `UBCC_OPTS` 时分别追加同值 naive/spill/spill policy。24 hot、192 conflict pressure、4x24 reuse；第一轮24 samples，保存值后发24 READ_VAL，timer=96（C-group `:117-173`）。verifier 要共同 C-group 门禁、reads=24、timer=96、samples=24（`test_e2e.py:2699-2702`）。

## TC224 - C132-HA Dirty Checkpoint Recovery

注册 `test_e2e.py:164`；N=2,K=1；宏3；ordinary naive/spill-noopt/optimized 明确绑定 `GTAIL=GN/GN/GO`、`UTAIL=UTINYN/UTINYS/UTINYO`；矩阵显式设置 `UBCC_OPTS` 时分别追加同值 naive/spill/spill policy。full 编译不加规模宏，active=8192,pressure=65536,stride=512,sample_count=17；compact 精确宏为 active=512,pressure=4096,stride=64,sample_count=9（C-group `:16-29`）。node1写 active；node0写 pressure，随后读取全部 active，但只保存 stride 样本和必要的末行样本；recover/end-to-end timers operations 均为 active；逐样本 READ_VAL（`:175-221`）。

**TC224 不是 simulator checkpoint/restart。**“checkpoint”只是脏数据恢复阶段名；不调用 gem5 checkpoint/save/restore API，不生成或加载模拟器 checkpoint，每次均冷启动两个 gem5。verifier 要两 node config完全一致、sample_count公式精确、reads=sample_count且 MATCH、两个 timer恰各一条且 operations一致正数；当前不要求 operations 等于 config.active（`test_e2e.py:2705-2746`）。

## TC225 - C135-HA Preserved Sharer Revisit

注册 `test_e2e.py:165`；N=2,K=1；宏4；ordinary naive/spill-noopt/optimized 明确绑定 `GTAIL=GN/GN/GO`、`UTAIL=UTINYN/UTINYS/UTINYO`；矩阵显式设置 `UBCC_OPTS` 时分别追加同值 naive/spill/spill policy。node0 hot24；node1初读并发24 reads；pressure192；node1逐条 first-load 测24 samples并再发24 reads（C-group `:223-269`）。verifier 要 reads=48全 MATCH、samples=24（`test_e2e.py:2749-2751`）。

## TC226 - C138-HA Dirty Owner Handoff

注册 `test_e2e.py:166`；N=2,K=1；宏5；ordinary naive/spill-noopt/optimized 明确绑定 `GTAIL=GN/GN/GO`、`UTAIL=UTINYN/UTINYS/UTINYO`；矩阵显式设置 `UBCC_OPTS` 时分别追加同值 naive/spill/spill policy。node1写24 hot；node0 pressure192并 timed handoff stores 24；node1读24（C-group `:271-310`）。verifier 要 reads=24全 MATCH、handoff samples=24（`test_e2e.py:2754-2756`）。

## TC227 - C139-HA Mixed Batch Throughput

注册 `test_e2e.py:167`；N=2,K=1；宏6；ordinary naive/spill-noopt/optimized 明确绑定 `GTAIL=GN/GN/GO`、`UTAIL=UTINYN/UTINYS/UTINYO`；矩阵显式设置 `UBCC_OPTS` 时分别追加同值 naive/spill/spill policy。node0 seed16；node1校验并取得8 odd owner；pressure192；node1做16x16=256 ops，timer256、samples16；node1校验8 odd并写 checksum，node0验 checksum，合计9 reads（C-group `:312-386`）。verifier 要 reads=9全 MATCH、timer operations=256、samples=16（`test_e2e.py:2759-2762`）；不额外要求 timer ticks/frequency非0。

## 超时、输入规模与验收边界摘要

* 普通 `run_multi.sh` 默认600秒；TC128内建下界1800，TC131-TC134内建7200；更大的 `TIMEOUT_SEC` 覆盖下界（`run_multi.sh:617-631`）。矩阵预算以矩阵目录为准。
* 输入数量：3N1S=3 simout+3 UBIO stdout；3N2S=3+6；8N1S=8+8；8N2S=8+16；2N1S=2+2。
* TC131-TC134 的 policy 轴与 gem5 profile 机制上独立；默认矩阵把 naive-naive、spill-noopt-spill、optimized-spill 配对，但 direct run 可以交叉组合。
* TC142-TC147 的 workload marker/verifier 不随 profile 改变；profile 只改变 directory policy/batch 和显式 gem5 optimization flags。
* TC222-TC227 是独立 2N1S C-group adaptation，使用 `HA_CGROUP_SCENARIO`，没有 FORMAL150 record，也不属于 formal150 runner。
