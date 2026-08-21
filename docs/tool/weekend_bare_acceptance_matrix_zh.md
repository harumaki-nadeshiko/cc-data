# 周末裸启动验收矩阵

## 1. 范围与约束

本文用于远端只能分别裸启动networksim、UBIO和gem5的环境。远端不执行
`run_multi.sh`，不使用supervisor，也不要求设置本仓库的framework参数。IPC、backend
连接和库搜索继续沿用远端现有环境。

所有命令中的以下路径由操作者替换：

```text
NSIM_BIN          networksim可执行文件
UBIO_BIN          UBIO可执行文件
GEM5_BIN          gem5.opt
TEST_E2E          远端实际微调版test_e2e.py
TOPO_JSON         本次networksim拓扑JSON
WORKLOAD_ELF      本TC的AArch64静态workload
LOG_ROOT          本次独立日志目录
M5OUT             本次独立gem5输出目录
```

所有TC必须使用包含以下marker的新UBIO：

```text
[UBCC-PROTOCOL-BUILD] revision=20260821-writeback-wakeup-v1
batchRsCompletionIdentity=1
ordinaryWritebackWakeup=1
```

## 2. 启动和退出顺序

每个TC使用独立的IPC目录、日志目录、topology和workload文件。远端既有framework所需
配置保持不变。

1. 启动1个networksim，保存PID，stdout/stderr写独立文件。
2. 等networksim完成bind。
3. 启动全部UBIO，逐个保存PID和stdout/stderr。
4. 等每个UBIO出现`[UBIO-IPC]`和`[PROCESS-MANIFEST]`。
5. 启动全部gem5，逐个保存PID和stdout/stderr。
6. 等所有gem5结束；记录退出码。
7. 等UBIO完成PeerExit并自行结束；记录退出码。
8. UBIO全部退出后向本次networksim PID发送`SIGTERM`，不得使用`pkill`。
9. PASS必须同时满足workload结果、协议判据及所有进程正常退出。

无supervisor时由远端调度器设置单项wall-clock硬超时。超时必须终止本次保存的PID集合，
不能影响其他运行。

## 3. Workload编译

```bash
aarch64-linux-gnu-gcc -static -O0 -g \
  -DNUM_NODES=<NODES> -DNUM_SOCKETS=<SOCKETS> \
  -I"$WORKLOAD_INCLUDE" \
  -o "$WORKLOAD_ELF" "$WORKLOAD_SOURCE"
```

TC98、TC134源码内部固定8n2s；TC35内部固定3n2s；其他3n1s workload固定node0-2参与。
不要额外覆盖workload规模，除非明确记录为非正式实验。

## 4. 拓扑A：3n1s

适用：TC16、TC127、TC135-141。

生成network topology：

```bash
python3 scripts/gen_topo.py --nodes 3 --sockets 1 --out "$TOPO_JSON"
```

networksim：

```bash
"$NSIM_BIN" "$TOPO_JSON" 3 1
```

对`N=0,1,2`各启动一个UBIO：

```bash
"$UBIO_BIN" \
  --node="$N" --socket=0 --num-sockets=1 --num-nodes=3 \
  <UBIO_TC_ARGS> \
  --metadata-dram-bytes=134217728
```

对`N=0,1,2`各启动一个gem5：

```bash
"$GEM5_BIN" --outdir="$M5OUT/node$N" \
  "$TEST_E2E" \
  --node-id="$N" --num-nodes=3 --num-sockets=1 \
  --workload="$WORKLOAD_ELF" \
  <GEM5_PROFILE_ARGS> \
  --ubcc-metadata-size=134217728 \
  --ha-profile=ubcc --clear-profile=ack \
  --cpu-model=o3 --sequencer-max-outstanding=16
```

## 5. 拓扑B：3n2s

适用：TC35。

```bash
python3 scripts/gen_topo.py --nodes 3 --sockets 2 --out "$TOPO_JSON"
"$NSIM_BIN" "$TOPO_JSON" 3 2
```

对`N=0,1,2`和`S=0,1`启动6个UBIO：

```bash
"$UBIO_BIN" \
  --node="$N" --socket="$S" --num-sockets=2 --num-nodes=3 \
  --batch-rs=0 \
  --metadata-dram-bytes=134217728
```

对`N=0,1,2`启动3个gem5：

```bash
"$GEM5_BIN" --outdir="$M5OUT/node$N" \
  "$TEST_E2E" \
  --node-id="$N" --num-nodes=3 --num-sockets=2 \
  --workload="$WORKLOAD_ELF" \
  --silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0 \
  --ubcc-metadata-size=134217728 \
  --ha-profile=ubcc --clear-profile=ack \
  --cpu-model=o3 --sequencer-max-outstanding=16
```

## 6. 拓扑C：8n2s

适用：TC98、TC134。

```bash
python3 scripts/gen_topo.py --nodes 8 --sockets 2 --out "$TOPO_JSON"
"$NSIM_BIN" "$TOPO_JSON" 8 2
```

对`N=0..7`、`S=0,1`启动16个UBIO：

```bash
"$UBIO_BIN" \
  --node="$N" --socket="$S" --num-sockets=2 --num-nodes=8 \
  <UBIO_TC_ARGS> \
  --metadata-dram-bytes=134217728
```

对`N=0..7`启动8个gem5：

```bash
"$GEM5_BIN" --outdir="$M5OUT/node$N" \
  "$TEST_E2E" \
  --node-id="$N" --num-nodes=8 --num-sockets=2 \
  --workload="$WORKLOAD_ELF" \
  <GEM5_PROFILE_ARGS> \
  --ubcc-metadata-size=134217728 \
  --ha-profile=ubcc --clear-profile=ack \
  --cpu-model=<timing|o3> --sequencer-max-outstanding=16
```

## 7. P0必须矩阵

### Focused controller，本机门禁

远端不必运行；正式代码合入前本机必须通过：

```text
tools/capacity_waiter_liveness_test.cc
```

它直接覆盖Batch-RS completed identity和ordinary writeback wakeup两个根因。

### TC16：并发升级快速门禁

```text
Topology: 3n1s
Source: e2e_tc16_dual_upgrade_race.c
UBIO_TC_ARGS: --batch-rs=1
GEM5_PROFILE_ARGS: --silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1
Timeout: 900s
PASS: 三个node最终值一致，且只能为0xA0A0或0xB0B0；3/3 PeerExit
```

### TC35：跨socket reqId/tuple门禁

使用第5节完整参数。

```text
Source: e2e_tc35_numa_latency_stress.c
Timeout: 1200s
PASS: done-line为0x35DD0000/1/2，三个node均有TC35_PROGRESS，6/6 PeerExit
```

### TC98 Timing

```text
Topology: 8n2s
Source: e2e_tc98_8n2s_hotspot.c
UBIO_TC_ARGS: --ways=1 --batch-rs=1
GEM5_PROFILE_ARGS: --silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=1
CPU: timing
Seq: 16
Timeout: 21600s
PASS: 16/16 r12，16/16 MATCH，epoch单调，无tuple mismatch，25/25退出
```

### TC98 O3

参数同TC98 Timing，仅：

```text
CPU: o3
Timeout: 21600s
```

### TC134 spill-noopt Timing

```text
Topology: 8n2s
Source: e2e_tc134_8n2s_sliding_window.c
UBIO_TC_ARGS:
  --bloom-bytes=61440 --sram-bytes=524288 --ways=0 --set-bits=0
  --dir-overflow-policy=spill --batch-rs=0
GEM5_PROFILE_ARGS:
  --silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0
CPU: timing
Seq: 16
Timeout: 14400s
PASS: 8个writer均8192/8192，8个sharer均done，reuse完成，READ_VAL全MATCH，25/25退出
```

### TC127：writeback offload/onload

```text
Topology: 3n1s
Source: e2e_tc127_writeback_offload_onload.c
UBIO_TC_ARGS:
  --bloom-bytes=512 --sram-bytes=6144 --ways=1
  --dir-overflow-policy=spill --batch-rs=0
GEM5_PROFILE_ARGS:
  --silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0
Timeout: 1800s
PASS: 两个远端读均0x1270C0DE，并有spill、fill、WritebackReq、WB-DATA-PERSIST证据
```

TC127这里使用normalized `0/0/0`；它比旧runner隐式的gem5 batch=1更适合正确性验收。

### TC135、136、137、138、139、141

共同参数：

```text
Topology: 3n1s
UBIO_TC_ARGS:
  --bloom-bytes=512 --sram-bytes=5000 --ways=2 --set-bits=2
  --dir-overflow-policy=spill --batch-rs=0
GEM5_PROFILE_ARGS:
  --silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0
CPU/Seq: o3/16
Timeout: 1800s/项
```

| TC | workload | PASS重点 |
|---:|---|---|
|135|`e2e_tc135_preserved_sharer_revisit.c`|48 MATCH，24个preserved sharer latency samples|
|136|`e2e_tc136_preserved_owner_store.c`|24 MATCH，24个owner store samples|
|137|`e2e_tc137_new_requester_load.c`|node1/node2各24 MATCH，新requester 24 samples|
|138|`e2e_tc138_dirty_handoff_store.c`|24 MATCH，dirty handoff 24 samples|
|139|`e2e_tc139_mixed_batch_throughput.c`|node1=16/node2=8 MATCH，16 batch samples及256-op timer|
|141|`e2e_tc141_spill_shared_writer_recovery.c`|32 MATCH，spill/fill/shared-release，禁止not-sharer waiter drop|

### TC140：无tiny-dir控制组

```text
Topology: 3n1s
Source: e2e_tc140_cross_l2_owner_store.c
UBIO_TC_ARGS: --batch-rs=0
GEM5_PROFILE_ARGS: --silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0
CPU/Seq: o3/16
Timeout: 1800s
PASS: node2 24 MATCH，cross-L2 owner store 24 samples
```

## 8. P1推荐矩阵

### TC134 optimized O3

```text
UBIO_TC_ARGS与spill-noopt相同，仍为--batch-rs=0
GEM5_PROFILE_ARGS: --silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1
CPU/Seq: o3/16
Timeout: 14400s
```

如果optimized是交付默认，则此项升级为P0。

### Optimized shared-read子集

有余量时运行TC135、137、139、141 optimized：

```text
UBIO --batch-rs=1
gem5 --silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1
CPU/Seq: o3/16
```

不要为TC135-140重复完整naive/spill/optimized三profile矩阵。

## 9. 48小时排程

```text
0-1h      TC16、TC35、TC127
1-7h      TC98 Timing
7-13h     TC98 O3
13-17h    TC134 spill-noopt Timing
17-20h    TC135-141 spill-noopt串行
20-24h    TC134 optimized O3
24-30h    TC135/137/139/141 optimized
30-48h    为失败项重跑和日志取证预留
```

长8n2s项必须串行。TC135-141包含性能样本，也应串行，避免宿主竞争污染结果。

## 10. 性能验收与debug/TracePerf

### 不需要gem5 debug flags

正式正确性和性能验收默认不要添加：

```text
--debug-flags=RubyEP
--debug-flags=RubyEPVerbose
--debug-flags=UBLatency
```

这些flag只用于失败后的短时定向复跑，会大幅增加日志和wall-clock扰动。

### 默认常驻的性能marker

以下marker不依赖RubyEP，也不依赖TRACE-PERF：

```text
[GUEST-TIMER]    guest阶段吞吐和总计时
[PERF-LATENCY]   guest逐操作分位数
[EP-PERF]        outer/upgrade协议延迟
[PROCESS-MANIFEST]
[UBCC-PROTOCOL-BUILD]
```

性能验收只需要这些常驻marker。代码默认已经开启，不需要额外参数。

### TRACE-PERF是什么

`TRACE-PERF`用于逐跳事务链和故障取证，不是性能分数来源。无法设置环境变量时，其
程序默认策略为：

```text
mode=sample
firstN=500
everyK=0
max=2000
```

每个进程启动时检查：

```text
[TRACE-PERF-MANIFEST] mode=sample firstN=500 everyK=0 max=2000 maxExplicit=0
```

退出时检查：

```text
[TRACE-PERF-SUMMARY] policy=...
```

若无`TRACE-PERF-MANIFEST`，说明二进制没有包含最新策略marker；若manifest为`off`，
只影响逐跳诊断，不影响`GUEST-TIMER/PERF-LATENCY/EP-PERF`性能验收。

## 11. 失败时最小取证

TC98：

```bash
python3 scripts/analyze_tc98_logs.py "$LOG_ROOT" --strict
python3 scripts/extract_ubcc_key_state.py "$LOG_ROOT" --compact
```

TC134：

```bash
python3 scripts/diagnose_tc134_timeout.py "$LOG_ROOT" \
  --simout-dir "$SIMOUT_ROOT" --compact
python3 scripts/extract_ubcc_key_state.py "$LOG_ROOT" --compact
```

手机信道只需回传compact输出、TC号、profile和进程退出码摘要。
