# TC127、TC135-141远端裸启动合同

## 拓扑

这些workload的正式同步参与者都是node0、node1、node2，使用固定
`sync_wait(0b111)`。TC135-141的本机runner还会强制`--1s`。

```text
num_nodes=3
num_sockets=1
networksim=1
UBIO=3: node 0..2, socket 0
gem5=3: node 0..2
每个gem5保留4个CPU
supervisor=无要求
```

TC135-141若按8n2s启动属于绕过正式合同的实验，额外node会改变barrier和退出语义，
不能与本地正式结果比较。TC127虽然runner没有强制guard，workload同样只定义3节点参与。

## 公共环境

```bash
export UBCC_IPC_DIR=/本次运行独立IPC目录
export EP_LINK_LATENCY_PS=2500
export EP_SYNC_INTERVAL_PS=2500
export EP_PORT_HWM=8192
export EP_NSIM_MAX_PENDING=65536
export EP_TRACE_PERF=off
export EP_HA_PROFILE=ubcc
export OURCC_CLEAR_PROFILE=ack
unset UBCC_OPTS EP_GEM5_OPTS
```

正确性优先推荐：

```text
CPU=o3
Sequencer=16
profile=spill-noopt
gem5 silent/direct/batch=0/0/0
```

Timing可作为第二轮调度敏感性复测；不要先用optimized掩盖正确性问题。

## Networksim

先生成3n1s full-mesh topology：

```bash
python3 scripts/gen_topo.py --nodes 3 --sockets 1 --out "$TOPO_JSON"
```

启动：

```bash
"$NSIM_BIN" "$TOPO_JSON" 3 1
```

## UBIO通用模板

对`N=0,1,2`各启动一个：

```bash
"$UBIO_BIN" \
  --node="$N" --socket=0 --num-sockets=1 --num-nodes=3 \
  <TC对应目录参数> \
  --metadata-dram-bytes=134217728
```

不得带`--fault-rules`，不得再通过`UBCC_OPTS`追加重复参数。

### TC127

```text
--bloom-bytes=512
--sram-bytes=6144
--ways=1
--dir-overflow-policy=spill
--batch-rs=0
```

`set_bits`不显式设置，由6144B、ways=1布局自动求解。

### TC135、136、137、138、139、141

```text
--bloom-bytes=512
--sram-bytes=5000
--ways=2
--set-bits=2
--dir-overflow-policy=spill
--batch-rs=0
```

### TC140

```text
--batch-rs=0
```

其余使用默认512KiB ResidentDir、61440B Bloom、spill/H64。

## Gem5模板

对`N=0,1,2`各启动：

```bash
"$GEM5_BIN" --outdir="$M5OUT/node$N" \
  "$REMOTE_TEST_E2E" \
  --node-id="$N" --num-nodes=3 --num-sockets=1 \
  --workload="$WORKLOAD_ELF" \
  --silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0 \
  --ubcc-metadata-size=134217728 \
  --ha-profile=ubcc --clear-profile=ack \
  --cpu-model=o3 --sequencer-max-outstanding=16
```

TC127本机旧runner没有显式写gem5三个优化开关，导致UBIO batch=0而gem5默认batch=1。
远端正确性复测使用上面的normalized `0/0/0`合同，并在结果中注明该归一化。

## Workload映射

```text
127 e2e_tc127_writeback_offload_onload.c
135 e2e_tc135_preserved_sharer_revisit.c
136 e2e_tc136_preserved_owner_store.c
137 e2e_tc137_new_requester_load.c
138 e2e_tc138_dirty_handoff_store.c
139 e2e_tc139_mixed_batch_throughput.c
140 e2e_tc140_cross_l2_owner_store.c
141 e2e_tc141_spill_shared_writer_recovery.c
```

编译参数：

```bash
aarch64-linux-gnu-gcc -static -O0 -g \
  -DNUM_NODES=3 -DNUM_SOCKETS=1 \
  -I"$WORKLOAD_INCLUDE" -o "$WORKLOAD_ELF" "$WORKLOAD_SOURCE"
```

## 本地复测结果

在`revision=20260821-writeback-wakeup-v1`、O3+seq16、spill-noopt下：

```text
TC127 PASS
TC135 PASS
TC136 PASS
TC137 PASS
TC138 PASS
TC139 PASS
TC140 PASS
TC141 PASS
```

每项均为3/3 PeerExit及NetworkExit完成。
