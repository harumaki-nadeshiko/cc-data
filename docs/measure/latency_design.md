# 延迟测量、校准与可视化 — 总体方案文档

> 状态：方案设计期 | 最后更新：2026-07-07

---

## 1. 系统架构

### 1.1 进程拓扑（以 1s = 3 nodes × 1 socket 为例）

```
  gem5_0 ──(ZMQ IPC)── ubio_0 ──(ZMQ IPC)──┐
  gem5_1 ──(ZMQ IPC)── ubio_1 ──(ZMQ IPC)──┤  networksim
  gem5_2 ──(ZMQ IPC)── ubio_2 ──(ZMQ IPC)──┘   (全交叉 mesh)
```

- **gem5**（X86 native binary `gem5.opt`）：ARM 全系统仿真 + CHI 缓存一致性协议
- **ubio**（`build/bin/ubio`）：UBCC 目录控制器 + 一致性消息路由，每个 (node, socket) plane 一个进程
- **networksim**（`build/bin/networksim`）：全交叉 mesh 模拟器，模拟节点间链路延迟

IPC 端点：硬编码为 `ipc:///workspace/gem5/shared_ipc/ipc_*`，底层 ZeroMQ REQ/REP 模式。

### 1.2 gem5 内部拓扑

```
CPU → L1(D) → L2 → HN-F(L3) → ┌→ DL_SNF → MemCtrl → DDR4 DRAM
                               ├→ EP_SNF → UBAdapter → ZMQ IPC → ubio
                               └→ L_SNF  → MemCtrl → DDR4 DRAM
```

- **EP_SNF / EP_RNF**：跨节点一致性消息的出入端口
- **UBAdapter**：gem5 与 ubio 进程之间的 ZMQ 通道封装

---

## 2. 时间单位体系

| 组件 | tick 含义 | 单位 | 说明 |
|------|----------|------|------|
| gem5 整体 | `curTick()` | **ps** | `SimClock::Int::ps = 1` |
| ubio 主循环 | `tick` 变量 | **ps** | 通过 `safeTs()` 与 gem5/nsim 双向对齐 |
| nsim 主循环 | `_tick` 变量 | **ps** | 同上 |

**三者统一 ps 单位，trace 数据可直接对齐比较。**

**例外**：Ruby cache controller 参数（`dataAccessLatency`、`tagAccessLatency`）单位为 **cycles**（@2GHz，1 cy = 500 ps），由 gem5 内部 `schedule()` 自动换算。

---

## 3. 延迟模型

### 3.1 跨节点消息的端到端路径

```
源gem5 ─(Tq)─> 源ubio ─(Tq)─> nsim ─(L_nsim + FIFO)─> 目标ubio ─(Tq)─> 目标gem5
      100ns         100ns       ~~~~~~~~可调的~~~~~~~~       100ns
```

| 段 | 名称 | 含义 | 当前值(ps) | 当前值(ns) |
|----|------|------|-----------|-----------|
| Tq | ZMQ IPC 单跳 | gem5↔ubio 或 ubio↔nsim 的 ZMQ send/recv 往返 | ~100000 | ~100 |
| L_nsim | nsim link latency | `gen_topo.py` 中的 `latency` 字段 | 100000 | 100 |
| FIFO_extra | nsim 排队等待 | `safeTs` 对齐导致的消息在 nsim FIFO 中额外等待 | 0~88000 | 0~88 |

**一条跨节点消息的单向 IO hop** = Tq(源gem5→源ubio) + Tq(源ubio→nsim) + L_nsim + FIFO_extra + Tq(nsim→目标ubio) + Tq(目标ubio→目标gem5)

### 3.2 gem5 内部路径

```
CPU → L1 → L2 → HN-F(L3) → ┌→ DL_SNF → MemCtrl → DDR4  (本地DRAM)
                            └→ EP_SNF → UBAdapter         (跨节点出口)
```

| 段 | 控制参数 | 文件 |
|----|---------|------|
| L1/L2 cache access | `dataAccessLatency`, `tagAccessLatency` | `gem5/configs/ruby/CHI_config.py:63-74` |
| L3(HN-F) cache access | `dataAccessLatency=30`, `tagAccessLatency=6` | `gem5/configs/ruby/CHI_ubcc_framework.py:203-205` |
| HN-F → SN-F → MemCtrl | `to_memory_controller_latency` | `gem5/configs/ruby/CHI_config.py` 中 SNF 创建处 |
| DRAM 内部 | DDR4_2400_8x8 timing (tCAS, tRCD, tRP...) | gem5 内置 DRAM 模型 |

### 3.3 ZMQ 等量偏移特性

ZMQ IPC 每跳约 100ns，全程叠加 200~400ns。但**所有消息方向对称地经过相同 ZMQ 跳数**：

- 远程读请求方向（requester→home）：4×Tq ≈ 400ns
- 远程读响应方向（home→requester）：4×Tq ≈ 400ns
- 本地操作：2×Tq ≈ 200ns（gem5↔ubio 往返）

**ZMQ 不改变消息之间的因果顺序**（同向消息 ZMQ 偏移等量，交叉方向对称），只增加绝对延迟基线。甲方关注的链路延迟是 nsim 段的 `L_nsim + FIFO_extra`，不包含 ZMQ。

---

## 4. 甲方延迟目标

| 指标 | 目标(ns) | 目标(ps) | 对应的可调参数 |
|------|---------|---------|-------------|
| core → Local Socket L3 | 15 | 15000 | L1/L2/L3 cache latency |
| core → Local Socket DRAM | 100 | 100000 | HN-F→DL_SNF→MemCtrl latency + DDR4 timing |
| core → Remote Socket DRAM | 110 | 110000 | 同上 + EP-SNF 跨 socket 路径 |
| **跨Node同Socket IOModule 一跳** | **415** | **415000** | **`gen_topo.py` link latency（cross-node）** |
| **同Node跨Socket IOModule 一跳** | **210~240** | **210000~240000** | **`gen_topo.py` link latency（same-node cross-socket）** |

---

## 5. 当前实测值（TC1+TC2+TC5，2026-07-07）

### 5.1 测量方法

在所有进程的 ZMQ 边界埋点 `[TRACE-PERF]`（六点方案，详见 §8），收集→按 reqId 分组→计算相邻同向事件间 Δt。

### 5.2 Tq（ZMQ IPC 跳）

| 统计量 | 值 |
|--------|-----|
| 样本数 | 54 |
| **平均值** | **98.7ns**（98472 ps）|
| P50 中位数 | 100.0ns |
| 最小值 | 32.0ns |
| 最大值 | 100.0ns |

**结论：Tq ≈ 100ns，无需调整。**

### 5.3 nsim FIFO 转发延迟

| Link Pair | 方向 | 样本数 | avg(ns) | p50(ns) | min(ns) | max(ns) |
|-----------|------|--------|---------|---------|---------|---------|
| mod0→mod1 | cross-node | 4 | 134 | 148 | 100 | 188 |
| mod1→mod0 | cross-node | 4 | 115 | 100 | 100 | 162 |
| mod2→mod1 | cross-node | 7 | **173** | 162 | 162 | 188 |
| **cross-node 汇总** | | **19** | **137** | 148 | 100 | 188 |

nsim 延迟 = `gen_topo.py latency`（当前 100000ps = 100ns）+ FIFO 排队等待。

### 5.4 与目标的差距

| 指标 | 目标(ns) | 实测(ns) | 差距(ns) | 调参方向 |
|------|---------|---------|---------|---------|
| 跨Node同Socket IO hop (nsim段) | 415 | ~137 | **+278** | `gen_topo.py` 跨节点 link: 100ns → ~415ns |
| 同Node跨Socket IO hop (nsim段) | 210~240 | ~100 | **+110~140** | `gen_topo.py` 跨socket link: 100ns → ~25ns(?) |
| core→L3 | 15 | 未测 | — | 须加 gem5 内部 trace 点 |
| core→Local DRAM | 100 | 未测 | — | 同上 |
| core→Remote DRAM | 110 | 未测 | — | 同上 |

**注**：L3/DRAM 无法从当前边界 TRACE-PERF 测量，因为 TC1 是纯 gem5 内部操作，不经过 ubio/nsim。需要新增 gem5 Ruby 控制器内部的打点。

---

## 6. 调参方案

### 6.1 networksim link latency（`scripts/gen_topo.py`）

**当前**：所有 link 统一 `latency = 100000`（ps）。
**目标**：按 link 类型区分。

```python
# 当前 gen_topo.py 的核心逻辑
latency = 100000  # 所有 link 相同

# 需改为
CROSS_NODE_LATENCY  = 415000   # 跨节点: 415ns
CROSS_SOCKET_LATENCY = 225000  # 同节点跨Socket: ~225ns

for a in range(nmod):
    for b in range(a+1, nmod):
        node_a = a // num_sockets
        node_b = b // num_sockets
        lat = CROSS_NODE_LATENCY if node_a != node_b else CROSS_SOCKET_LATENCY
        links.append([a, 1, b, 1, lat])
```

**需要先增加 `gen_topo.py` 的参数**：当前只有 `--type 1s/2s`，需加 `--nodes N` `--sockets K`，或者从 topo JSON 读取 node/socket 数量。

**待确认**：同Node跨Socket IO hop 的目标 210~240ns 是否真的就是 nsim 段的 link latency，还是包含额外开销？

### 6.2 L3 cache latency（`gem5/configs/ruby/CHI_ubcc_framework.py:203-205`）

| 参数 | 当前(cycles) | 当前(ns) | 建议(cycles) | 建议(ns) |
|------|------------|---------|-------------|---------|
| HNFCache.dataAccessLatency | 30 | 15.0 | ~10 | ~5.0 |
| HNFCache.tagAccessLatency | 6 | 3.0 | ~4 | ~2.0 |

当前 L1(2cy)+L2(3cy)+L3(36cy) = 41cy = 20.5ns，超出 15ns 目标。需实测后迭代。

### 6.3 DRAM latency

DDR4_2400_8x8 的 tCAS/tRCD 由 gem5 内置，约 28cy(CAS) + 排队。如果实测本地 DRAM 偏离 100ns，调整 `CHI_config.py` 中 SN-F 的 `to_memory_controller_latency` 参数。

### 6.4 其他 CHI 协议参数

| 参数 | 当前 | 文件:行 | 说明 |
|------|------|--------|------|
| `NoC_Params.router_latency` | 1 cycle | `CHI_config.py:111` | gem5 内 ruby 路由，保持 1cy |
| `NoC_Params.node_link_latency` | 1 cycle | `CHI_config.py:110` | 同上 |
| `to_memory_controller_latency` | (默认) | SN-F 构造处 | 调 DRAM 延迟用 |

---

## 7. TRACE-PERF 打点方案

### 7.1 格式

```
[TRACE-PERF] <tick>|<node>|<component>|<reqId>|<pa>|<event>|<extra>
```

**所有组件 tick 为 ps 单位。**

### 7.2 打点位置

| # | 文件 | 代码位置 | 事件名 | 输出文件 |
|---|------|---------|--------|---------|
| ① | `gem5/src/.../UBAdapter.cc` | `transportSend()` 中 `port->send()` 成功后 | `gem5:SEND` | `gem5_tcXX_nodeN/stderr.log` |
| ② | `gem5/src/.../UBAdapter.cc` | `wakeup()` 中 Barrier 跳过之后 | `gem5:RECV` | 同上 |
| ③ | `modules/ubiomodule/ubio_main.cc` | `pollAndProcess` 收到 PAYLOAD 后 | `ubio:RECV_GEM5` / `ubio:RECV_NET` | `ubio_nN_sS/stderr.log` |
| ④ | `modules/ubiomodule/ubio_main.cc` | `sendCoh()` 中 `port->send()` 成功后 | `ubio:SEND_GEM5` / `ubio:SEND_NET` | 同上 |
| ⑤ | `modules/networksim/networksim_main.cc` | `step()` 中 `port->recv()` 后 | `nsim:RECV` | `nsim_tcXX.log` |
| ⑥ | `modules/networksim/networksim_main.cc` | `step()` 中 FIFO 出队 `send()` 后 | `nsim:FWD` | 同上 |

### 7.3 局限

**当前只能测量 ZMQ 边界事件**。L3/DRAM 等 gem5 内部路径无法测量（TC1 纯 gem5 内部，trace 为空）。

**需要新增 gem5 内部打点**（待定）：
- HMController（L2）的请求发出/收到响应
- HN-F（L3）的 cache hit/miss
- EPBackend 的请求处理开始/完成
- 这些点的 tick 源是 `curTick()`（ps），与现有 trace 直接对齐

---

## 8. 可视化工具链

### 8.1 trace2chain.py

```
grep -h 'TRACE-PERF' logs/*/gem5_tc*_node*/stderr.log \
                       logs/*/ubio_n*/stderr.log \
                       logs/*/nsim_tc*.log \
  | python3 scripts/trace2chain.py > chains.json
```

- 解析 TRACE-PERF 行 → 按 reqId 分组 → 按 tick 排序
- 过滤内部消息（reqId≤7）
- 提取主消息类型（ReadReq/UpgradeReq/RecallReq/...）
- 输出 JSON

### 8.2 chain2html.py

```
python3 scripts/chain2html.py --target-ns 415 chains.json > tc.html
```

- 白色背景 + 高饱和色块：
  - 蓝色 = gem5↔ubio (ZMQ Tq)
  - 绿色 = ubio→gem5 (ZMQ Tq)
  - 橙色 = ubio→nsim (ZMQ Tq)
  - 紫色 = nsim→ubio (ZMQ Tq)
  - 灰色 = nsim FIFO 排队转发
  - 黄色 = ubio 本地处理
- 每条 reqId 一个泳道，左侧显示类型 badge、rid、端到端延迟
- 悬停 segment 显示详情
- 点击展开完整事件列表
- 红色虚线 = 目标延迟参考线
- 顶部 filter：按 PA / rid / 最小 Tq 跳数
- 底部 Legend 说明

---

## 9. 待专家评审的问题

1. **同Node跨Socket IO hop 的目标值**：210~240ns 是否就是 nsim link latency 本身？还是包含额外的 gem5 内部处理？（当前实验中 1s topology 没有跨 socket link，无法实测）

2. **gen_topo.py link latency 的语义**：当前 `latency=100000` 在 nsim 中作为 `readyTick = _tick + lat` 使用，即消息到达 nsim 后延时 `lat` ps 再出队。这个语义是否符合甲方模型中 "IO Module 一跳" 的定义？

3. **nsim FIFO extra 的构成**：实测 nsim 段延迟 avg=137ns，其中 100ns 来自 `latency` 参数，额外 37ns 来自 `safeTs` 同步等待和 FIFO 排队。在目标系统里这个额外的 37ns 是否也应该计入？（如果计入，则 `gen_topo.py` 的 `latency` 应设为 415−37=378ns；如果不计入，则应设为 415ns 让 nsim 段直接等于目标值）

4. **ZMQ 偏移对 coherence 正确性的影响**：虽然理论上 ZMQ 是等量 pipeline delay 不改变因果序，但仿真里消息到达时间与 gem5 内部 `curTick()` 的关系是否可能产生微妙的反直觉行为？（例如：一条消息的 gem5 send 时间戳相对远端 clock 有 ZMQ 偏移，导致远端 controller 在"未来"收到消息）

5. **gen_topo.py 的改造方案**：目前 `--type 1s/2s` 隐式参与 `NMOD=3/6` 的推导。如果要区分 cross-node vs same-node link，建议是加 `--nodes`/`--sockets` 参数还是从 topo JSON 或环境变量推断？在多 socket topology 下，mod_id = node × K + socket 这个约定如何传递到 gen_topo.py？

6. **L3/DRAM 测量**：是否需要深入 gem5 Ruby controller 内部加 `[TRACE-PERF]` 打点？如果加，加在哪些状态转移处最合适（比如 CHI_HNFController 的 action transition，MemCtrl 的 read/write callback）？还是有其他更简洁的测量方法（如 gem5 stats dump）？

## 10. 相关文件索引

| 用途 | 文件 |
|------|------|
| 编译与启动手册 | `docs/dev_manual/split_mode_ops.md` |
| 性能 trace 技术参考 | `docs/dev_manual/perf_trace_guide.md` |
| 初始延迟分析报告 | `docs/dev_manual/latency_tuning_report.md` |
| 跨节点 link latency | `scripts/gen_topo.py` |
| nsim 主循环 | `modules/networksim/networksim_main.cc` |
| ubio 主循环 | `modules/ubiomodule/ubio_main.cc` |
| gem5↔ubio 边界 | `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc` |
| L3 cache 参数 | `gem5/configs/ruby/CHI_ubcc_framework.py:203-205` |
| CHI 协议延迟参数 | `gem5/configs/ruby/CHI_config.py:56-116` |
| TRACE-PERF 收集脚本 | `scripts/trace2chain.py` |
| 可视化渲染 | `scripts/chain2html.py` |
