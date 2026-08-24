# 周末裸启动性能优先验收矩阵

## 1. 目标与边界

本文用于远端只能分别裸启动networksim、UBIO和gem5的环境。远端不执行
`run_multi.sh`，不使用supervisor，也不要求传入framework参数。IPC、backend连接、
link/sync和HWM继续沿用远端既有framework环境及默认配置。

周末主目标按优先级是：

1. 完成Metric 1 TC131三profile三轮矩阵。
2. 完成Metric 2正式三轮矩阵：TC135-140和TC217。
3. 完成Metric 3 TC228-235的OurCC/HA-VI五组配对；按已冻结双tier判定。
4. 完成HA TC210-227三profile矩阵。
5. 完成TC142-147 portable-512K p150现实业务矩阵。
6. 用TC127、TC141和长TC98/TC134做正确性资格门禁。
7. 资源有余量时把高价值case从3轮补到5轮。

TC16已经稳定通过，降为更新二进制后的可选smoke，不占周末主矩阵。TC35仅在双socket
路径有修改或出现相关失败时补跑。

Metric 3 已冻结 core/representative 两层，并判定 **PASS
(EXECUTABLE-REFERENCE-MODEL SCOPE)**。HA-VI 是可执行参考模型，不是 proxy，也不
代表甲方物理芯片实测。

## 2. 版本门禁

所有运行必须使用同一组二进制。UBIO stdout必须出现：

```text
[UBCC-PROTOCOL-BUILD] revision=20260821-writeback-wakeup-v1
batchRsCompletionIdentity=1
ordinaryWritebackWakeup=1
```

gem5仓库版本至少为：

```text
253ddbda50 perf: reduce endpoint trace volume
```

每个进程首次使用TracePerf时应出现：

```text
[TRACE-PERF-MANIFEST] mode=sample firstN=500 everyK=0 max=2000 maxExplicit=0
```

无法设置环境变量时，TracePerf默认就是上述有界sample模式。

## 3. 路径占位符

以下模板中的路径由操作者替换：

```text
NSIM_BIN       networksim
UBIO_BIN       UBIO
GEM5_BIN       gem5.opt
TEST_E2E       远端实际微调版test_e2e.py
TOPO_JSON      本run独立networksim topology
WORKLOAD_ELF   本run独立AArch64 workload
WORKLOAD_SRC   workload C源
WORKLOAD_INC   tests/e2e/workloads或远端等价include目录
LOG_ROOT       本run独立日志目录
M5OUT          本run独立gem5输出目录
```

## 4. 裸启动和退出顺序

1. 为本run创建全新的IPC、日志、topology、workload和m5out路径。
2. 启动1个networksim，保存PID和stdout/stderr。
3. 等networksim完成bind。
4. 启动全部UBIO，逐个保存PID和stdout/stderr。
5. 等每个UBIO出现`[UBIO-IPC]`和`[PROCESS-MANIFEST]`。
6. 启动全部gem5，逐个保存PID和stdout/stderr。
7. 等全部gem5退出，保存各自退出码。
8. 等UBIO通过PeerExit自行退出，保存各自退出码。
9. UBIO全部退出后，仅向本run保存的networksim PID发送`SIGTERM`。
10. 不使用`pkill`，不影响其他run。

远端调度器负责wall-clock硬超时。超时时只能终止该run保存的PID集合。

PASS必须同时满足：

```text
testcase verifier PASS sentinel
所有READ_VAL/validation正确
所有性能marker结构合法
全部gem5和UBIO退出码为0
PeerExit完整
networksim受控结束
无fatal/panic/assert/timeout
```

## 5. Profile展开

所有性能run统一使用：

```text
CPU=o3
Sequencer=16
metadata=134217728 bytes
HA profile=ubcc
Clear profile=ack
```

gem5 profile参数：

```text
naive:
  --silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0

spill-noopt:
  --silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0

optimized:
  --silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1
```

完整gem5模板：

```bash
"$GEM5_BIN" --outdir="$M5OUT/node$N" \
  "$TEST_E2E" \
  --node-id="$N" --num-nodes="$NUM_NODES" --num-sockets="$NUM_SOCKETS" \
  --workload="$WORKLOAD_ELF" \
  <GEM5_PROFILE_ARGS> \
  --ubcc-metadata-size=134217728 \
  --ha-profile=ubcc --clear-profile=ack \
  --cpu-model=o3 --sequencer-max-outstanding=16
```

每个profile参数只出现一次，不使用`EP_GEM5_OPTS`或其他重复覆盖。

## 6. 拓扑模板

### 3n1s：TC127、TC135-141

```bash
python3 scripts/gen_topo.py --nodes 3 --sockets 1 --out "$TOPO_JSON"
"$NSIM_BIN" "$TOPO_JSON" 3 1
```

对`N=0,1,2`各启动一个UBIO：

```bash
"$UBIO_BIN" \
  --node="$N" --socket=0 --num-sockets=1 --num-nodes=3 \
  <UBIO_TC_ARGS> \
  --metadata-dram-bytes=134217728
```

对`N=0,1,2`按第5节模板启动gem5，令：

```text
NUM_NODES=3
NUM_SOCKETS=1
```

### 2n1s：HA TC210-227、real workload TC142-147

```bash
python3 scripts/gen_topo.py --nodes 2 --sockets 1 --out "$TOPO_JSON"
"$NSIM_BIN" "$TOPO_JSON" 2 1
```

对`N=0,1`各启动一个UBIO：

```bash
"$UBIO_BIN" \
  --node="$N" --socket=0 --num-sockets=1 --num-nodes=2 \
  <UBIO_TC_ARGS> \
  --metadata-dram-bytes=134217728
```

对`N=0,1`按第5节模板启动gem5，令：

```text
NUM_NODES=2
NUM_SOCKETS=1
```

每个2n1s run共5个进程：1 networksim、2 UBIO、2 gem5。

### 8n2s：TC98、TC134长正确性

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

对`N=0..7`启动8个gem5，令`NUM_NODES=8, NUM_SOCKETS=2`。

## 7. 性能资格门禁

只占用少量时间：

```text
TC127 spill-noopt O3
TC141 spill-noopt O3
TC141 optimized O3
```

TC127 UBIO：

```text
--bloom-bytes=512 --sram-bytes=6144 --ways=1
--dir-overflow-policy=spill --batch-rs=0
```

TC127 gem5使用spill-noopt `0/0/0`。

TC141 spill-noopt UBIO：

```text
--bloom-bytes=512 --sram-bytes=5000 --ways=2 --set-bits=2
--dir-overflow-policy=spill --batch-rs=0
```

TC141 optimized UBIO只把`--batch-rs=0`改为`--batch-rs=1`，gem5改为`1/0/1`。

任一门禁失败时，后续性能样本只能标为诊断数据，不能纳入正式统计。

TC16已稳定通过，仅在更新二进制后需要快速smoke时可选运行一次。

## 8. 正式Metric 1矩阵：TC131

正式合同：

```text
TC131
8n1s
naive/spill-noopt/optimized
至少3轮完整重复
O3/16
```

生成拓扑：

```bash
python3 scripts/gen_topo.py --nodes 8 --sockets 1 --out "$TOPO_JSON"
"$NSIM_BIN" "$TOPO_JSON" 8 1
```

对`N=0..7`启动8个UBIO，拓扑参数：

```text
--node=N --socket=0 --num-sockets=1 --num-nodes=8
```

Profile：

```text
naive UBIO:
  --bloom-bytes=0 --sram-bytes=524288 --ways=0 --set-bits=0
  --dir-overflow-policy=naive --batch-rs=0

spill-noopt UBIO:
  --bloom-bytes=61440 --sram-bytes=524288 --ways=0 --set-bits=0
  --dir-overflow-policy=spill --batch-rs=0

optimized UBIO:
  与spill-noopt UBIO相同，UBIO batch仍为0
```

gem5：naive/spill-noopt使用`0/0/0`；optimized使用`1/0/1`。对`N=0..7`启动8个
gem5，`--num-nodes=8 --num-sockets=1 --cpu-model=o3 --sequencer-max-outstanding=16`。

Workload：

```text
e2e_tc131_catalog_fullscan.c
HOT=4096
PRESSURE=98304
UPGRADE_SAMPLES=256
```

正式性能phase：

```text
post_pressure_catalog_reuse
node1和node2各一条
operations=8192
```

Metric 1门槛：

```text
capacity_ratio = effective_unique_spill-noopt / effective_unique_naive >= 1.5
delta_cycles = (spill-noopt_ns/op - naive_ns/op) * 2.0 < 50 cycles
```

每轮都必须满足两个门槛。optimized作为第三profile提供correctness和性能证据，但额外
成本门槛比较的是spill-noopt与naive。

最低总量：

```text
1 case × 3 profiles × 3 rounds = 9 runs
```

## 9. 正式Metric 2矩阵

正式case：

```text
TC135 TC136 TC137 TC138 TC139 TC140 TC217
```

最低矩阵：

```text
7 cases × 3 profiles × 3 rounds = 63 physical runs
```

### TC135-139

共同UBIO参数：

```text
naive:
  --bloom-bytes=512 --sram-bytes=5000 --ways=2 --set-bits=2
  --dir-overflow-policy=naive --batch-rs=0

spill-noopt:
  --bloom-bytes=512 --sram-bytes=5000 --ways=2 --set-bits=2
  --dir-overflow-policy=spill --batch-rs=0

optimized:
  --bloom-bytes=512 --sram-bytes=5000 --ways=2 --set-bits=2
  --dir-overflow-policy=spill --batch-rs=1
```

workload与正式phase：

| TC | source | Metric 2 phase | node | samples |
|---:|---|---|---:|---:|
|135|`e2e_tc135_preserved_sharer_revisit.c`|`preserved_sharer_first_load`|1|24|
|136|`e2e_tc136_preserved_owner_store.c`|`preserved_owner_store_complete`|1|24|
|137|`e2e_tc137_new_requester_load.c`|`new_requester_first_load`|2|24|
|138|`e2e_tc138_dirty_handoff_store.c`|`dirty_owner_handoff_store`|2|24|
|139|`e2e_tc139_mixed_batch_throughput.c`|`mixed_batch_16ops`|1|16|

TC139另要求：

```text
[GUEST-TIMER] phase=mixed_batch_throughput operations=256
```

### TC140

使用默认512KiB目录：

```text
naive:       --dir-overflow-policy=naive --batch-rs=0
spill-noopt: --dir-overflow-policy=spill --batch-rs=0
optimized:   --dir-overflow-policy=spill --batch-rs=1
```

正式phase：

```text
cross_l2_owner_store，node0，samples=24
```

TC140通常naive低于500ns，不进入最终平均，但必须运行作为低时延中性控制。

### TC217 / HA10

编译：

```bash
aarch64-linux-gnu-gcc -static -O0 -g \
  -DNUM_NODES=2 -DNUM_SOCKETS=1 -DHA_SCENARIO=10 \
  -I"$WORKLOAD_INC" -o "$WORKLOAD_ELF" \
  "$WORKLOAD_INC/e2e_ha_2n1s_core.c"
```

UBIO参数：

```text
naive:
  --bloom-bytes=128 --sram-bytes=4352 --ways=1 --set-bits=0
  --dir-overflow-policy=naive --batch-rs=0

spill-noopt:
  --bloom-bytes=128 --sram-bytes=4352 --ways=1 --set-bits=0
  --dir-overflow-policy=spill --batch-rs=0

optimized:
  --bloom-bytes=128 --sram-bytes=4352 --ways=1 --set-bits=0
  --dir-overflow-policy=spill --batch-rs=1
```

正式性能输出：

```text
[PERF-LATENCY] phase=ha10_catalog_batch_16ops node=1 samples=8
[GUEST-TIMER] phase=catalog_useful_throughput operations=128
```

### Metric 2聚合规则

每轮每case：

```text
mean_ns = mean_ticks * 1e9 / counter_frequency_hz
optimized_reduction_pct = (naive_ns - optimized_ns) / naive_ns * 100
applicable = naive_ns >= 500ns
```

对适用case的百分比等权平均。正式PASS要求：

```text
每轮equal-weight reduction >= 10%
跨轮适用集合稳定
全部计划槽位correctness PASS
```

TC138负收益不得删除。TC140即使不适用也必须保留。

## 10. HA矩阵TC210-227

### Profile参数

所有HA210-227均使用2n1s和下列UBIO profile：

```text
naive:
  --bloom-bytes=128 --sram-bytes=4352 --ways=1 --set-bits=0
  --dir-overflow-policy=naive --batch-rs=0

spill-noopt:
  --bloom-bytes=128 --sram-bytes=4352 --ways=1 --set-bits=0
  --dir-overflow-policy=spill --batch-rs=0

optimized:
  --bloom-bytes=128 --sram-bytes=4352 --ways=1 --set-bits=0
  --dir-overflow-policy=spill --batch-rs=1
```

gem5 profile使用第5节`0/0/0`和`1/0/1`。

### TC210-221

共同源：

```text
e2e_ha_2n1s_core.c
```

编译时增加：

| TC | macro | 场景 |
|---:|---:|---|
|210|`-DHA_SCENARIO=1`|local reuse|
|211|`-DHA_SCENARIO=2`|remote read|
|212|`-DHA_SCENARIO=3`|ownership handoff|
|213|`-DHA_SCENARIO=4`|shared to writer|
|214|`-DHA_SCENARIO=7`|producer consumer|
|215|`-DHA_SCENARIO=5`|clean shared victim revisit|
|216|`-DHA_SCENARIO=6`|dirty owner lifecycle|
|217|`-DHA_SCENARIO=10`|read-mostly catalog|
|218|`-DHA_SCENARIO=8`|barrier/seq-lock|
|219|`-DHA_SCENARIO=9`|local/remote pressure|
|220|`-DHA_SCENARIO=11`|exact-150 clean|
|221|`-DHA_SCENARIO=12`|exact-150 dirty|

如做formal150 qualification，TC210-219另加：

```text
-DHA_FORMAL_CAPACITY_LINES=768
```

TC220/221自身已经是exact-150，不需要该通用前置宏。

### TC222-227

共同源：

```text
e2e_ha_cgroup_2n1s.c
```

| TC | macro | 场景 |
|---:|---:|---|
|222|`-DHA_CGROUP_SCENARIO=1`|shared-to-writer batch|
|223|`-DHA_CGROUP_SCENARIO=2`|overflow hot reuse|
|224|`-DHA_CGROUP_SCENARIO=3`|dirty recovery|
|225|`-DHA_CGROUP_SCENARIO=4`|preserved sharer revisit|
|226|`-DHA_CGROUP_SCENARIO=5`|dirty owner handoff|
|227|`-DHA_CGROUP_SCENARIO=6`|mixed batch throughput|

TC224周末只跑compact：

```text
-DC224_ACTIVE_LINES=512
-DC224_PRESSURE_LINES=4096
-DC224_READ_STRIDE=64
```

不在48小时主矩阵中跑TC224 full。

### HA统计边界

TC210-227没有冻结统一业务权重。每个phase独立报告mean/stdev/CV，不制造总分。

以下case优先补到5轮：

```text
TC217 TC215 TC216 TC220 TC221 TC224-compact TC227
```

历史预跑仍按其原标签保存；当前正式证据已由
`results/metric3-l3-only-v4` 的 TC228-235 paired 矩阵取代。

## 11. Metric 3 paired矩阵：TC228-235

正式执行拓扑：

```text
2n1s
O3/16
每TC至少5个complete pairs
A=OurCC lossless-oneway
B=HA-VI
```

### Workload编译

```text
TC228 e2e_ha_topology.c -DHA_TOPOLOGY_SCENARIO=1
TC229 e2e_ha_topology.c -DHA_TOPOLOGY_SCENARIO=2
TC230 e2e_ha_topology.c -DHA_TOPOLOGY_SCENARIO=3
TC231 e2e_ha_extended.c -DHA_EXT_SCENARIO=1
TC232 e2e_ha_extended.c -DHA_EXT_SCENARIO=2
TC233 e2e_ha_extended.c -DHA_EXT_SCENARIO=3
TC234 e2e_ha_extended.c -DHA_EXT_SCENARIO=4
TC235 e2e_ha_extended.c -DHA_EXT_SCENARIO=5
```

共同编译宏：

```text
-DNUM_NODES=2 -DNUM_SOCKETS=1
```

### Arm A：OurCC lossless-oneway

UBIO：

```text
--node=N --socket=0 --num-sockets=1 --num-nodes=2
--dir-overflow-policy=spill
--metadata-dram-bytes=134217728
```

不得出现HA-VI参数。

gem5：

```text
--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0
--ubcc-metadata-size=134217728
--ha-profile=ubcc
--clear-profile=lossless-oneway
--cpu-model=o3 --sequencer-max-outstanding=16
```

必须出现：

```text
ha_endpoint_profile=ubcc
clear_profile=lossless-oneway
reliability=eventual-delivery
```

### Arm B：HA-VI

UBIO：

```text
--node=N --socket=0 --num-sockets=1 --num-nodes=2
--dir-overflow-policy=spill
--home-controller=ha-vi
--ha-exact-bytes=134217728
--ha-max-active=256
--ha-max-queue=8
--metadata-dram-bytes=134217728
```

gem5：

```text
--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0
--ubcc-metadata-size=134217728
--ha-profile=ha-vi
--clear-profile=ack
--cpu-model=o3 --sequencer-max-outstanding=16
```

必须出现：

```text
ha_endpoint_profile=ha-vi
clear_profile=ack
reliability=clear-ack
[UBIO-HA-MANIFEST] controller=ha-vi exact_bytes=134217728
max_active=256 per_address_queue=8
```

### AB/BA与重复

每TC至少5 pairs。定义：

```text
A=OurCC
B=HA-VI
```

顺序：

```text
if ((pair-1 + tc_index) % 2 == 0): AB
else: BA
```

其中TC228的`tc_index=0`，TC235为7。

总量：

```text
8 TCs × 5 pairs × 2 arms = 80 arm runs
```

TC228-230 是 core 等权 tier；TC231-235 是 representative 等权 tier，其中 TC232
按 2/3 read + 1/3 write，TC233/234/235 分别取 producer_consumer_service、
queued_token_end_to_end、catalog_kv_end_to_end。每个arm必须
verifier PASS、5个2n1s进程退出码为0、profile marker匹配。

Metric 3当前为：

```text
PASS (EXECUTABLE-REFERENCE-MODEL SCOPE)
HA-VI executable reference model
not physical customer-silicon measurement
```

不得用历史示例权重覆盖当前冻结双tier，也不得把该 PASS 外推到物理硅。

## 12. Real workload矩阵TC142-147

周末固定拓扑和容量合同：

```text
2n1s
portable-512K
150% pressure
O3/16
```

共同编译宏：

```text
-DNUM_NODES=2
-DNUM_SOCKETS=1
-DPORTABLE_TARGET_FOOTPRINT_LINES=98304
-DPORTABLE_NAIVE_CAPACITY_LINES=65536
-DPORTABLE_PRESSURE_LEVEL_PCT=150
-DPORTABLE_BATCHES=32
```

每TC源和pressure：

| TC | pressure | source |
|---:|---:|---|
|142|98240|`e2e_tc142_db_oltp_buffer_pool.c`|
|143|98030|`e2e_tc143_db_btree_traversal.c`|
|144|97920|`e2e_tc144_db_wal_checkpoint.c`|
|145|98032|`e2e_tc145_faas_warm_invocation.c`|
|146|97920|`e2e_tc146_graph_frontier.c`|
|147|98032|`e2e_tc147_feature_store.c`|

另加：

```text
-DPORTABLE_PRESSURE_LINES=<表中值>
```

UBIO profile：

```text
naive:
  --bloom-bytes=0 --sram-bytes=524288 --ways=0 --set-bits=0
  --dir-overflow-policy=naive --batch-rs=0

spill-noopt:
  --bloom-bytes=61440 --sram-bytes=524288 --ways=0 --set-bits=0
  --dir-overflow-policy=spill --batch-rs=0

optimized:
  --bloom-bytes=61440 --sram-bytes=524288 --ways=0 --set-bits=0
  --dir-overflow-policy=spill --batch-rs=1
```

gem5 profile仍为`0/0/0`和`1/0/1`。

最低矩阵：

```text
6 cases × 3 profiles × 3 rounds = 54 runs
```

每个run报告：

```text
service ns/op
end-to-end ns/op
batch mean/P50/P95/P99/max
aggregate throughput
correctness和退出码
```

不能只报service改善。每plane timer和latency summary必须唯一，READ_VAL必须全MATCH。

TC142-147不进入正式Metric 2或Metric 3平均，单独报告为real-workload性能矩阵。

## 13. 长正确性矩阵

性能矩阵外各跑一次：

```text
TC98 Timing 8n2s
TC98 O3 8n2s
TC134 spill-noopt Timing 8n2s
TC134 optimized O3 8n2s（若optimized为交付默认）
```

TC98 UBIO：

```text
--ways=1 --batch-rs=1
```

TC98 gem5：

```text
--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=1
```

TC134 spill-noopt UBIO：

```text
--bloom-bytes=61440 --sram-bytes=524288 --ways=0 --set-bits=0
--dir-overflow-policy=spill --batch-rs=0
```

TC134 spill-noopt gem5：`0/0/0`；optimized gem5：`1/0/1`，UBIO仍batch=0。

## 14. 48小时排程

### 单lane串行

如果只能一次跑一个case：

```text
0-2h     TC127、TC141 spill-noopt/optimized资格门禁
2-8h     Metric1 TC131三轮9 runs
8-18h    Metric2三轮63 runs；微基准通常远短于硬timeout
18-30h   Metric3 core TC228-230五pairs，共30 arm runs
30-42h   TC142-147 real workload：先完整Round1，再补Round2/3
42-48h   TC217额外重复、HA高价值子集或失败项重跑
```

如果本机15-20秒级微基准速度在远端也成立，节省时间优先用于real workload三轮，再扩展
HA完整矩阵。

### 六个隔离lane

只有每lane具备独立CPU集合、IPC、日志和m5out时才并发：

```text
0-1h     qualification
1-5h     Metric1三轮 + Metric2三轮
5-17h    HA210-227 + real142-147 Round1
17-29h   Round2，轮换profile顺序
29-41h   Round3，再次轮换
41-47h   Metric3 paired 5 pairs及高价值case补轮
47-48h   verifier、退出码、coverage和统计汇总
```

HA210-227 + real142-147一轮共72 runs，三轮216 runs。profile顺序轮换：

```text
Round1 naive -> spill-noopt -> optimized
Round2 optimized -> naive -> spill-noopt
Round3 spill-noopt -> optimized -> naive
```

如果并发lane无法隔离CPU或宿主负载不稳定，不得把并发wall-clock用于正式比较。

## 15. 性能marker与debug/TracePerf

### 正式性能不需要debug flags

不要添加：

```text
--debug-flags=RubyEP
--debug-flags=RubyEPVerbose
--debug-flags=UBLatency
```

这些flag只用于失败后的短时定向复跑，会放大日志并扰动性能。

### 默认常驻性能marker

```text
[GUEST-TIMER]    guest吞吐和阶段总时间
[PERF-LATENCY]   guest逐操作延迟分布
[EP-PERF]        协议outer/upgrade延迟诊断
[PROCESS-MANIFEST]
[UBCC-PROTOCOL-BUILD]
```

这些marker不依赖RubyEP，也不依赖TRACE-PERF，默认已经开启。

### 检查TracePerf是否开启

TracePerf只用于逐跳事务链诊断，不是性能分数来源。默认：

```text
mode=sample
firstN=500
everyK=0
max=2000
```

启动时检查：

```text
[TRACE-PERF-MANIFEST] mode=sample firstN=500 everyK=0 max=2000 maxExplicit=0
```

退出时检查：

```text
[TRACE-PERF-SUMMARY] policy=...
```

`mode=off`只关闭逐跳trace，不影响常驻性能marker。`mode=full`会显著放大日志，不应作为
正式性能run。

## 16. 统计与证据

每个物理run保存：

```text
唯一run ID、TC/profile/round、执行顺序
workload源和全部defines、topology、完整argv
UBIO/gem5/networksim/workload hash
所有simout和进程stdout/stderr
verifier sentinel及全部退出码
原始counter ticks和frequency
```

每TC/profile跨重复报告：

```text
有效run数
mean、sample stdev、CV、min、median、max
失败和timeout单列
round内paired reduction，再跨round汇总
```

Metric3相关结果必须标记：

```text
PASS (EXECUTABLE-REFERENCE-MODEL SCOPE)
2N1S/O3, frozen two-tier aggregate
dirty-worktree provenance; not physical-silicon measurement
```

## 17. 失败时最小取证

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

手机信道只回传compact输出、TC、profile、round和退出码摘要。
