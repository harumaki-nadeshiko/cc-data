# Metric1 TC131、TC142-TC147 裸参数

## 实验范围

- TC131：仅正式 `8n1s`。
- TC142-TC147：`2n1s、3n1s、3n2s、8n1s、8n2s、16n1s`。
- 每个 `TC/topology` 跑三个独立角色：`naive、spill、ideal`。
- 不使用 optimized 代替 spill；Metric1 的第三角色必须是 IdealDir。

## Workload 编译

TC131：

```bash
NUM_NODES=8 NUM_SOCKETS=1 \
WORKLOAD_OUT=/tmp/tc131_8n1s.elf \
bash scripts/compile_workload.sh 131
```

TC142-TC147 通用：

```bash
NUM_NODES=<N> NUM_SOCKETS=<S> \
WORKLOAD_CFLAGS='-DPORTABLE_PRESSURE_LINES=<PRESSURE> -DPORTABLE_TARGET_FOOTPRINT_LINES=98304 -DPORTABLE_NAIVE_CAPACITY_LINES=65536 -DPORTABLE_PRESSURE_LEVEL_PCT=150 -DPORTABLE_BATCHES=32' \
WORKLOAD_OUT=/tmp/tc<TC>_<TOPO>.elf \
bash scripts/compile_workload.sh <TC>
```

以上编译必须在项目 Docker 镜像中运行。

## Pressure 表

| TC | 2n1s | 3n1s | 3n2s | 8n1s | 8n2s | 16n1s |
|---:|---:|---:|---:|---:|---:|---:|
| 142 | 98240 | 98208 | 98112 | 98048 | 97792 | 97792 |
| 143 | 98030 | 97893 | 97482 | 97208 | 96112 | 96112 |
| 144 | 97920 | 97728 | 97152 | 96768 | 95232 | 95232 |
| 145 | 98032 | 97896 | 97488 | 97216 | 96128 | 96128 |
| 146 | 97920 | 97728 | 97152 | 96768 | 95232 | 95232 |
| 147 | 98032 | 97896 | 97488 | 97216 | 96128 | 96128 |

拓扑参数：

| Topology | N | S | Active planes | run_multi flag |
|---|---:|---:|---:|---|
| 2n1s | 2 | 1 | 2 | `--2n1s` |
| 3n1s | 3 | 1 | 3 | `--3n1s` |
| 3n2s | 3 | 2 | 6 | `--3n2s` |
| 8n1s | 8 | 1 | 8 | `--8n1s` |
| 8n2s | 8 | 2 | 16 | `--8n2s` |
| 16n1s | 16 | 1 | 16 | `--16n1s` |

## gem5 每节点参数

每个 node 启动一个 gem5：

```text
gem5/build/ARM/gem5.opt
--outdir=<GEM5_OUT>
tests/e2e/test_e2e.py
--node-id=<NODE>
--num-nodes=<N>
--num-sockets=<S>
--workload=<WORKLOAD_ELF>
--tc=<TC>
--cpu-model=o3
--sequencer-max-outstanding=16
--l3-size=256kB
--l3-assoc=16
--ha-profile=ubcc
--clear-profile=ack
--silent-upgrade=0
--direct-fwd=0
--ubcc-batch-rs=0
--ubcc-metadata-size=134217728
```

三个角色的 gem5 参数完全相同，只替换输出目录和 workload 路径。

## UBIO 每 plane 公共参数

每个 `(node,socket)` 启动一个 UBIO：

```text
build/bin/ubio
--node=<NODE>
--socket=<SOCKET>
--num-nodes=<N>
--num-sockets=<S>
--tc=<TC>
--metadata-dram-bytes=134217728
--batch-rs=0
<ROLE_ARGS>
```

### naive

```text
--bloom-bytes=0
--sram-bytes=524288
--ways=0
--set-bits=0
--dir-overflow-policy=naive
```

Manifest 身份：

```text
profile=naive
metric1_role=naive
```

### spill

```text
--bloom-bytes=61440
--sram-bytes=524288
--ways=0
--set-bits=0
--dir-overflow-policy=spill
```

Manifest 身份：

```text
profile=spill-noopt
metric1_role=spill
```

### ideal

```text
--bloom-bytes=61440
--sram-bytes=2097152
--ways=32
--set-bits=0
--dir-overflow-policy=spill
--allow-oversized-resident-dir-for-test
```

Manifest 身份：

```text
profile=spill-noopt
metric1_role=ideal
```

Ideal 必须在日志中满足：

```text
experimental_oversized_resident_dir=1
resident_capacity>=102656
backstore_found_fills=0
h64ExactLiveCount=0
completed Outer samples>=1
```

## run_multi 快速模板

TC142-TC147 必须设置：

```text
PORTABLE_512K_DIR=1
WORKLOAD_CFLAGS=<上表对应宏>
```

naive：

```bash
EP_CPU_MODEL=o3 EP_SEQUENCER_MAX_OUTSTANDING=16 \
EP_HA_PROFILE=ubcc OURCC_CLEAR_PROFILE=ack \
EP_PERF_PROFILE=naive UBCC_POLICY=naive \
EP_GEM5_OPTS='--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0' \
PORTABLE_512K_DIR=1 WORKLOAD_CFLAGS='<PORTABLE_MACROS>' \
LOG_BASE=<NAIVE_LOG> bash tests/e2e/run_multi.sh --<TOPO> <TC>
```

spill：

```bash
EP_CPU_MODEL=o3 EP_SEQUENCER_MAX_OUTSTANDING=16 \
EP_HA_PROFILE=ubcc OURCC_CLEAR_PROFILE=ack \
EP_PERF_PROFILE=spill-noopt UBCC_POLICY=spill \
EP_GEM5_OPTS='--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0' \
PORTABLE_512K_DIR=1 WORKLOAD_CFLAGS='<PORTABLE_MACROS>' \
LOG_BASE=<SPILL_LOG> bash tests/e2e/run_multi.sh --<TOPO> <TC>
```

ideal：

```bash
EP_CPU_MODEL=o3 EP_SEQUENCER_MAX_OUTSTANDING=16 \
EP_HA_PROFILE=ubcc OURCC_CLEAR_PROFILE=ack \
EP_PERF_PROFILE=spill-noopt UBCC_POLICY=spill \
METRIC1_ROLE=ideal \
UBCC_OPTS='--bloom-bytes=61440 --sram-bytes=2097152 --ways=32 --set-bits=0 --dir-overflow-policy=spill --batch-rs=0 --allow-oversized-resident-dir-for-test' \
EP_GEM5_OPTS='--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0' \
PORTABLE_512K_DIR=1 WORKLOAD_CFLAGS='<PORTABLE_MACROS>' \
LOG_BASE=<IDEAL_LOG> bash tests/e2e/run_multi.sh --<TOPO> <TC>
```

TC131 使用同样三个角色，但删除`PORTABLE_512K_DIR`和`WORKLOAD_CFLAGS`，固定：

```text
run_multi.sh --8n1s 131
```

## Extractor 输入

每个 run 必须明确写：

```json
{
  "metric": 1,
  "tc": 142,
  "topology": "3n1s",
  "profile": "spill-noopt",
  "metric1_role": "spill",
  "home_node": 0,
  "home_socket": 0,
  "simulator_log_dir": "...",
  "simout_dir": "..."
}
```

TC142-TC147 要进入正式资格视图，必须加载 planner 的：

```text
qualification_sets.json -> extractor_requirements
```

Standard matrix 仍只包含 TC131/8n1s；TC142-TC147 的值在 qualification/detail 视图中查看。
