# 多进程拆分模式操作手册

## 架构概述

```
           gem5_0 ──(IPC)── ubio_0 ──(IPC)──┐
           gem5_1 ──(IPC)── ubio_1 ──(IPC)──┤ networksim
           gem5_2 ──(IPC)── ubio_2 ──(IPC)──┘
```

- **gem5**（`gem5/build/ARM/gem5.opt`）：每个节点一个进程，运行 ARM 全系统仿真 + CHI 缓存一致性协议（EP-RNF/EPBackend）
- **ubio**（`build/bin/ubio`）：每个 `(node, socket)` plane 一个进程，承载 UBCC 目录控制器 + 一致性消息路由
- **networksim**（`build/bin/networksim`）：全交叉 mesh 网络模拟器，所有 ubio 进程通过它互连

所有进程通过 Unix domain socket（`ipc://`）通信，端点硬编码为 `/workspace/gem5/shared_ipc/ipc_*`。

---

## 1. 编译

所有命令在 Docker 容器内执行（`ubcc-dev:ubuntu20.04`）：

### 1.1 一键构建所有原生二进制

```bash
docker run --rm -v $(pwd):/workspace -w /workspace ubcc-dev:ubuntu20.04 \
  bash -c "bash scripts/build_framework.sh && bash scripts/build_all.sh"
```

产物：
- `build/bin/ubio`
- `build/bin/networksim`
- `build/bin/barrier_manager`
- `build/framework/lib/libframework.a`（依赖库）

### 1.2 单独编译 ubio

```bash
bash scripts/build_framework.sh     # 只需一次
bash scripts/build_ubio.sh          # 源码：modules/ubiomodule/
```

### 1.3 单独编译 networksim

```bash
bash scripts/build_networksim.sh    # 源码：modules/networksim/
```

### 1.4 编译 gem5

gem5 用 scons 构建，镜像中已预编译好的 `gem5/build/ARM/gem5.opt` 通常可直接使用。若需重编：
```bash
cd gem5 && scons build/ARM/gem5.opt -j$(nproc)
```

### 1.5 编译 E2E workload

```bash
bash scripts/compile_workload.sh <tc_id>
# 例子：bash scripts/compile_workload.sh 49
# 产物：tests/e2e/workloads/workload.elf
```

workload 是纯 C 的 ARM bare-metal 程序，交叉编译器为 `aarch64-linux-gnu-gcc`（`-static -O0 -g`）。

---

## 2. 配置：topo JSON

配置文件位于 `configs/` 目录，定义了拓扑元数据、各进程启动命令和连接关系。

### 2.1 单 socket（`topo_1s.json`）

```json
{
  "num_nodes": 3,
  "num_sockets": 1,
  "modules": [
    {"id": "gem5_0",  "cmd": "{gem5_bin} --outdir={node_outdir} {test_e2e} --node-id=0 --num-nodes=3 --num-sockets=1 --workload={workload}"},
    {"id": "gem5_1",  "cmd": "..."},
    {"id": "gem5_2",  "cmd": "..."},
    {"id": "ubio_0",  "cmd": "{ubio_bin} --node=0 --socket=0 --num-sockets=1 --num-nodes=3 {fault_rules_args}"},
    {"id": "ubio_1",  "cmd": "..."},
    {"id": "ubio_2",  "cmd": "..."},
    {"id": "networksim", "cmd": "{nsim_bin} {topo_json}"}
  ],
  "links": [
    ["gem5_0", "mem-0", "ubio_0", "mem-0"],
    ["gem5_1", "mem-0", "ubio_1", "mem-0"],
    ["gem5_2", "mem-0", "ubio_2", "mem-0"],
    ["ubio_0", "mem-1", "networksim", "mem-0"],
    ["ubio_1", "mem-1", "networksim", "mem-1"],
    ["ubio_2", "mem-1", "networksim", "mem-2"]
  ]
}
```

### 2.2 双 socket（`topo_2s.json`）

类似，但每个 node 有 `ubio_N_s0` 和 `ubio_N_s1` 两个 plane（6 个 ubio 进程），gem5 也有两个 mem port 分别连到两个 ubio plane。

### 2.3 占位符说明

`cmd` 字段中的 `{...}` 由 `run_multi.sh` 的 `expand_cmd()` 函数替换：

| 占位符 | 替换为 |
|--------|--------|
| `{root}` | repo 根目录 |
| `{gem5_bin}` | `gem5/build/ARM/gem5.opt` |
| `{ubio_bin}` | `build/bin/ubio` |
| `{nsim_bin}` | `build/bin/networksim` |
| `{test_e2e}` | `tests/e2e/test_e2e.py` |
| `{workload}` | `tests/e2e/workloads/workload.elf` |
| `{topo_json}` | `build/run/topo.json` |
| `{node_outdir}` | gem5 的 `--outdir` 路径 |
| `{fault_rules_args}` | `--fault-rules=...` 或空 |

### 2.4 生成 networksim 拓扑

```bash
python3 scripts/gen_topo.py --type 1s --out build/run/topo.json
# --type 取值：1s（NMOD=3，3条全交叉link）或 2s（NMOD=6，15条link）
```

---

## 3. 启动流程

IPC 端点硬编码在 `/workspace/gem5/shared_ipc/ipc_*`，每次运行前必须清理残留。

### 3.1 启动前清理

```bash
rm -rf /workspace/gem5/shared_ipc/ipc_* /tmp/ubio_n* /tmp/networksim_* 2>/dev/null
mkdir -p /workspace/gem5/shared_ipc
```

### 3.2 第一步：启动 networksim

```bash
# 先生成 topo.json
python3 scripts/gen_topo.py --type 1s --out build/run/topo.json

# 启动 nsim（必须第一个起，等待它绑定端口）
build/bin/networksim build/run/topo.json \
  > logs/nsim.log 2>&1 &
```

### 3.3 第二步：启动 gem5（等待绑定）

```bash
# 每个节点一个 gem5 进程
gem5/build/ARM/gem5.opt \
  --outdir=build/run/m5out/node0 \
  tests/e2e/test_e2e.py \
  --node-id=0 --num-nodes=3 --num-sockets=1 \
  --workload=tests/e2e/workloads/workload.elf \
  > logs/gem5_node0.log 2>&1 &
```

对于 node=1、node=2 同理，`--node-id` 改为对应值。

**关键**：必须等待每个 gem5 打印出 `STEP5...Port enabled` 后才能启动 ubio：
```bash
# 轮询等待
while ! grep -q "STEP5.*Port enabled" logs/gem5_node0.log 2>/dev/null; do sleep 1; done
echo "gem5 node=0 bound"
```

### 3.4 第三步：启动 ubio

```bash
# 不带故障注入
build/bin/ubio \
  --node=0 --socket=0 --num-sockets=1 --num-nodes=3 \
  > logs/ubio_n0.log 2>&1 &

# 带故障注入（TC47-49）
build/bin/ubio \
  --node=0 --socket=0 --num-sockets=1 --num-nodes=3 \
  --fault-rules="tc49_dup_inv_ack:InvalidateAck:1:0:0:dup::1" \
  > logs/ubio_n0.log 2>&1 &
```

所有 ubio 进程都启动后，gem5 收到同步释放信号，开始仿真。

### 3.5 命令参考

| 参数 | 说明 |
|------|------|
| `--node=<N>` | ubio 所在节点编号（0-31） |
| `--socket=<S>` | socket plane 编号（0 起，< `--num-sockets`） |
| `--num-sockets=<K>` | 每节点 socket 数（1 或 2） |
| `--num-nodes=<N>` | 集群总节点数 |
| `--fault-rules=<RULES>` | 故障注入规则（见 §5） |

---

## 4. 故障注入规则

通过 `--fault-rules` 参数注入，格式：

```
<name>:<type>:<src>:<dst>:<pa>:<action>[:<delayTicks>[:<matchCount>]]
```

多个规则用 `;` 分隔。

| 字段 | 说明 | 示例 |
|------|------|------|
| `name` | 规则名称（自由文本） | `tc49_dup_inv_ack` |
| `type` | 消息类型（见下） | `InvalidateAck` |
| `src` | 匹配源节点，`-1` = 任意 | `1` |
| `dst` | 匹配目标节点，`-1` = 任意 | `0` |
| `pa` | 匹配物理地址，`0` = 任意 | `0` |
| `action` | `dup` / `drop` / `delay` | `dup` |
| `delayTicks` | delay 动作的时钟数 | `1000` |
| `matchCount` | 触发次数限制，0 = 不限 | `1` |

支持的消息类型：`ReadReq`, `ReadResp`, `RecallReq`, `RecallResp`, `InvalidateReq`, `InvalidateAck`, `WritebackReq`, `WritebackResp`, `EvictReq`, `EvictResp`, `UpgradeReq`, `UpgradeResp`, `UpgradeDoneReq`, `UpgradeDoneResp`, `ClearReq`, `ClearResp`, `UpgradeAckNotify`.

### 示例

```bash
# TC49: 在 ubio nid=0 上复制来自 Node1 的 InvalidateAck 一次
--fault-rules="tc49_dup_inv_ack:InvalidateAck:1:0:0:dup::1"

# TC47: 复制来自 Node1 的 ClearReq 一次
--fault-rules="tc47_dup_clear:ClearReq:1:0:0:dup::1"

# 组合多个规则
--fault-rules="r1:ReadReq:1:0:0x1000:drop:;r2:RecallResp:2:0:0:dup::1"
```

**注意**：规则只在 `coh->h.dstNode == nid` 的 ubio 进程上生效，即故障匹配的是 **该 ubio 收到的消息**。

---

## 5. 验证

### 5.1 输出文件

每个 gem5 进程写入 `{outdir}/simout_n{N}`（每节点一个），内容为 workload 输出的 `[READ_VAL]` 标记行。格式：
```
[READ_VAL] node=N home=0 off=0x4900 expected=0x49CC0033 got=0x49CC0033 match=1
```

### 5.2 使用 verify.py（推荐）

```bash
# 聚合所有节点的 simout
python3 tests/e2e/verify.py \
  --tc 49 \
  --simout build/run/m5out/node0/simout_n0 \
           build/run/m5out/node1/simout_n1 \
           build/run/m5out/node2/simout_n2 \
  --fault-log logs/ubio_n0_s0/stderr.log  # 用于扫描 [UBFAULT] 证据
```

退出码 0 = PASS，非 0 = FAIL。输出最后一行包含 `>>> TC49 PASSED <<<` 或 `>>> TC49 FAILED <<<`。

### 5.3 使用 test_e2e.py（单进程模式）

```bash
# 所有节点在同一 gem5 进程中（无需 ubio/networksim）
gem5/build/ARM/gem5.opt \
  --outdir=m5out/tc49 \
  tests/e2e/test_e2e.py \
  --tc=49
```

此模式不适用于故障注入 TC（47-49），但适用于验证基本协议正确性。

### 5.4 完整自动化：run_multi.sh

```bash
# 跑单个 TC
TIMEOUT_SEC=600 bash tests/e2e/run_multi.sh --1s 49

# 跑多个 TC 序列
TIMEOUT_SEC=600 bash tests/e2e/run_multi.sh --1s 1 2 5 10 48 49

# 双 socket
TIMEOUT_SEC=600 bash tests/e2e/run_multi.sh --2s 32
```

脚本自动完成：编译 workload → 生成 topo → 启动 nsim → 启动 gem5 → 等待绑定 → 启动 ubio → 等 gem5 退出 → 调用 `verify.py` 验证。

---

## 6. IPC 端点命名规则

所有端点都从 `PortEnvLoader` 自动派生，格式：

```
ipc:///workspace/gem5/shared_ipc/ipc_<a>_to_<b>
```

具体映射：

| 通信对 | 端点 |
|--------|------|
| gem5_N ↔ ubio_N | `ipc_gem5_N_to_ubio_N` / `ipc_ubio_N_to_gem5_N` |
| ubio_N ↔ networksim | `ipc_ubio_N_to_networksim_mN` / `ipc_networksim_mN_to_ubio_N` |
| networksim 监听 | `ipc_networksim_mN_to_ubio_N`（mN 对应 module N） |

**关键约束**：端点路径在 `framework/Port.cc:237` 硬编码为 `/workspace/gem5/shared_ipc/`，所有进程运行在同一台机器上，且 IPC 目录必须可读写。
