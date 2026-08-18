# 16N1S 测试矩阵增量与裸启动指南

## 1. 合入主线后的能力增量

16N 合入以 `v5-o3-ha-integrated` 为基础，保留原有 HA controller、socket-qualified
路由和 HA completion 语义，同时增加以下传统 UBCC/H64 能力：

| 范围 | 增量 |
|---|---|
| 拓扑 | 新增真正 `16 nodes x 1 socket`，共 16 个 gem5、16 个 UBIO 和 1 个 networksim |
| 地址 | ResidentDir PA 宽度按拓扑派生；16N 使用 44-bit effective PA |
| sharer | sharer mask 从历史最小 8 bit 扩展到 16 bit |
| 网络 | networksim 校验 `num_nodes/num_sockets`、重复 link、零延迟 link 和 full mesh 路由 |
| H64 | 初次 Lookup `RetryableBusy` 使用固定容量表、每 wakeup 有界重试，不再永久保留 fill pin |
| Grant | push-grant admission、DSM data read 和网络发送均保留原 tuple 并有界重试 |
| Requester | ReadReq 使用固定 64 项 in-flight 表和同 reqId retry window |
| Recall | RecallUnique proxy coalesce；ReadUnique completion 同时包含 data 和 `Comp_UC` |
| 退出 | 16/16 PeerExit，正式 case 要求 33 个受管 child 全部 `exit=0` |
| 性能 | TC142-TC147 支持 16N1S、512 KiB ResidentDir、p150 和三 profile |
| smoke | 新增 TC160，覆盖 16-way share、node-0 writer invalidation 和 16-way reread |

HA profile 与 Clear profile 继续保留。`tests/e2e/test_e2e.py` 接受：

```text
--ha-profile=ubcc|ha-vi|ha
--clear-profile=ack|lossless-oneway
```

这两个参数在 gem5 配置创建前分别写入：

```text
EP_HA_PROFILE
OURCC_CLEAR_PROFILE
```

`run_multi.sh` 会显式把当前 profile 作为 argv 传给每个 gem5 进程，远端不需要依赖
Python 进程继承外层 shell 环境的偶然行为。

## 2. 测试矩阵增量

### 2.1 16N 功能 smoke

| TC | Profile | 目的 | PASS 门槛 |
|---:|---|---|---|
| 160 | spill/H64 | 16 节点共享 node-15 Home line，node 0 写入并失效全部旧 sharer | 32 个 `READ_VAL` MATCH；16/16 PeerExit；33/33 child exit 0 |

### 2.2 16N portable p150 性能矩阵

| TC | 场景 | 每轮操作数 | Batch phase |
|---:|---|---:|---|
| 142 | OLTP buffer pool | 16,384 | `db_oltp_batch_32ops` |
| 143 | B-tree traversal | 32,768 | `db_btree_batch_64ops` |
| 144 | WAL/checkpoint | 16,384 | `db_wal_batch_32ops` |
| 145 | FaaS warm invocation | 32,768 | `faas_batch_64ops` |
| 146 | Graph frontier | 32,768 | `graph_batch_64ops` |
| 147 | Feature store | 32,768 | `feature_batch_64ops` |

每个 TC 有三个 profile：

| Profile | UBCC policy | gem5 flags |
|---|---|---|
| `naive` | `--dir-overflow-policy=naive` | `--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0` |
| `spill-noopt` | `--dir-overflow-policy=spill` | `--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0` |
| `optimized` | `--dir-overflow-policy=spill` | `--silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1` |

一轮正式矩阵为：

```text
6 TC x 3 profiles = 18 cases
```

当前接受一轮作为必要复测；若需要报告跨轮 CV，可把 `REPEATS` 提高到 3。

### 2.3 p150 固定参数

```text
ResidentDir SRAM budget:       524288 bytes
target footprint:              98304 lines
naive capacity reference:      65536 lines
pressure level:                150%
batches:                       32
CPU model:                     o3
Sequencer max outstanding:     16
cpuset:                        0-31
```

每 TC 的 pressure lines：

| TC | Hot lines/plane | Pressure lines |
|---:|---:|---:|
| 142 | 32 | 97,792 |
| 143 | 137 | 96,112 |
| 144 | 192 | 95,232 |
| 145 | 136 | 96,128 |
| 146 | 192 | 95,232 |
| 147 | 136 | 96,128 |

正式协调器还检查：

```text
verifier最后一行为 >>> TCx PASSED <<<
16个PORTABLE-PRESSURE记录完整且数值精确
16个gem5 + 16个UBIO + networksim，共33个child exit=0
运行期间cc-data、gem5和三个二进制fingerprint不变
```

## 3. 构建

项目要求构建和仿真全部在 Docker 中执行。

### 3.1 UBIO

```bash
docker run --rm --network none \
  --cpuset-cpus=0-31 \
  -v "$PWD:/workspace" \
  -v "/path/to/zeromq/lib:/workspace/thirdparty/zeromq/lib:ro" \
  -w /workspace \
  ubcc-dev:ubuntu20.04 \
  bash scripts/build_ubio.sh
```

### 3.2 networksim

```bash
docker run --rm --network none \
  --cpuset-cpus=0-31 \
  -v "$PWD:/workspace" \
  -v "/path/to/zeromq/lib:/workspace/thirdparty/zeromq/lib:ro" \
  -e LIBRARY_PATH=/workspace/thirdparty/zeromq/lib \
  -e LD_LIBRARY_PATH=/workspace/thirdparty/zeromq/lib \
  -w /workspace \
  ubcc-dev:ubuntu20.04 \
  bash scripts/build_networksim.sh
```

### 3.3 gem5

```bash
docker run --rm --network none \
  --cpuset-cpus=0-31 \
  -v "$PWD:/workspace" \
  -v "/path/to/external-gem5-build:/external-build" \
  -v "/path/to/framework-build:/workspace/build/framework:ro" \
  -v "/path/to/zeromq/lib:/workspace/thirdparty/zeromq/lib:ro" \
  -e LIBRARY_PATH=/workspace/thirdparty/zeromq/lib \
  -e LD_LIBRARY_PATH=/workspace/thirdparty/zeromq/lib \
  -w /workspace/gem5 \
  ubcc-dev:ubuntu20.04 \
  scons build/ARM/gem5.opt -j32
```

如果 `gem5/build/ARM` 是外部软链接，容器内挂载点必须与软链接目标一致。

## 4. 裸启动

“裸启动”表示不使用 `run_n16_formal_perf_matrix.py`，直接调用 `run_multi.sh`。它仍会：

```text
编译本次 workload ELF
生成 networksim topo.json
启动并管理全部 child
运行独立 verifier
保存 child exit 状态
```

### 4.1 TC160 smoke

```bash
docker run --rm --network none \
  --cpuset-cpus=0-31 \
  -v "$PWD:/workspace" \
  -v "/path/to/gem5-build:/gem5-build" \
  -v "/path/to/zeromq/lib:/workspace/thirdparty/zeromq/lib:ro" \
  -v "/path/to/logs:/logs" \
  -w /workspace \
  ubcc-dev:ubuntu20.04 \
  env \
    E2E_RUN_ID=tc160_manual \
    LOG_BASE=/logs/tc160 \
    TIMEOUT_SEC=1800 \
    EP_CPU_MODEL=o3 \
    EP_SEQUENCER_MAX_OUTSTANDING=16 \
    EP_HA_PROFILE=ubcc \
    OURCC_CLEAR_PROFILE=ack \
    EP_TRACE_PERF=off \
    LD_LIBRARY_PATH=/workspace/thirdparty/zeromq/lib \
    bash tests/e2e/run_multi.sh --16n1s 160
```

### 4.2 单个性能 case

下面以 TC147 `spill-noopt` 为例：

```bash
docker run --rm --network none \
  --cpuset-cpus=0-31 \
  -v "$PWD:/workspace" \
  -v "/path/to/gem5-build:/gem5-build" \
  -v "/path/to/zeromq/lib:/workspace/thirdparty/zeromq/lib:ro" \
  -v "/path/to/logs:/logs" \
  -w /workspace \
  ubcc-dev:ubuntu20.04 \
  env \
    E2E_RUN_ID=tc147_spill_noopt_manual \
    LOG_BASE=/logs/tc147_spill_noopt \
    TIMEOUT_SEC=21600 \
    EP_CPU_MODEL=o3 \
    EP_SEQUENCER_MAX_OUTSTANDING=16 \
    EP_HA_PROFILE=ubcc \
    OURCC_CLEAR_PROFILE=ack \
    PORTABLE_512K_DIR=1 \
    EP_PERF_PROFILE=spill-noopt \
    UBCC_POLICY=spill \
    UBCC_OPTS='--dir-overflow-policy=spill' \
    EP_GEM5_OPTS='--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0' \
    WORKLOAD_CFLAGS='-DPORTABLE_PRESSURE_LINES=96128 -DPORTABLE_TARGET_FOOTPRINT_LINES=98304 -DPORTABLE_NAIVE_CAPACITY_LINES=65536 -DPORTABLE_PRESSURE_LEVEL_PCT=150 -DPORTABLE_BATCHES=32' \
    EP_TRACE_PERF=off \
    LD_LIBRARY_PATH=/workspace/thirdparty/zeromq/lib \
    bash tests/e2e/run_multi.sh --16n1s 147
```

切换 profile 时只修改：

```text
EP_PERF_PROFILE
UBCC_POLICY
UBCC_OPTS
EP_GEM5_OPTS
```

### 4.3 手工串行一轮

最简方式仍使用正式协调器，但只跑一轮：

```bash
docker run --rm --network none \
  --cpuset-cpus=0-31 \
  -v "$PWD:/workspace" \
  -v "/path/to/gem5-build:/gem5-build" \
  -v "/path/to/zeromq/lib:/workspace/thirdparty/zeromq/lib:ro" \
  -v "/path/to/formal-root:/formal-root" \
  -w /workspace \
  ubcc-dev:ubuntu20.04 \
  env \
    RUN_TAG=n16_manual_r1 \
    LOG_ROOT=/formal-root/n16_manual_r1 \
    REPEATS=1 \
    TC_LIST='142 143 144 145 146 147' \
    PROFILE_LIST='naive spill-noopt optimized' \
    CONTINUE_ON_FAIL=1 \
    CPUSET=0-31 \
    LD_LIBRARY_PATH=/workspace/thirdparty/zeromq/lib \
    python3 scripts/run_n16_formal_perf_matrix.py
```

该命令是“无后台调度器”的直接矩阵启动，但仍保留正式fingerprint和压力校验。

## 5. Profile 裸启动

### UBCC + Clear Ack

```text
EP_HA_PROFILE=ubcc
OURCC_CLEAR_PROFILE=ack
```

### UBCC + lossless one-way Clear

```text
EP_HA_PROFILE=ubcc
OURCC_CLEAR_PROFILE=lossless-oneway
```

该profile不能与ClearReq fault injection同时使用。

### HA-VI + Ack

```text
EP_HA_PROFILE=ha-vi
OURCC_CLEAR_PROFILE=ack
```

`run_multi.sh`会自动给UBIO附加：

```text
--home-controller=ha-vi
--ha-exact-bytes=<HA_EXACT_BYTES>
--ha-max-active=<HA_MAX_ACTIVE>
--ha-max-queue=<HA_MAX_QUEUE>
```

## 6. 最小验收检查

单个case结束后至少检查：

```bash
tail -n 1 /path/to/logs/verify_tc160.log
wc -l /path/to/logs/child_status_tc160/*.exit
grep -L '^0$' /path/to/logs/child_status_tc160/*.exit
```

16N预期：

```text
verify sentinel: >>> TCx PASSED <<<
child status files: 33
non-zero status files: 0
PeerExit: 16/16
```

性能结果由：

```bash
python3 scripts/summarize_n16_formal_perf.py /path/to/matrix-root \
  > /path/to/matrix-root/summary.json
```

生成。正式报告不要只引用service timer；必须同时报告service、end-to-end、batch P99和
aggregate throughput。
