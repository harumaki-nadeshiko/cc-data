# 性能 Tracing 与延迟校准指南

## 1. 时间单位

**gem5 / ubio / nsim 全部使用 ps（picosecond）作为 `tick` 单位**，无需换算。

- gem5：`curTick()` → `SimClock::Int::ps = 1`
- ubio：`tick` 变量（通过 `safeTs()` 与 gem5/nsim 对齐）
- nsim：`_tick` 变量（同样的对齐机制）

**例外**：Ruby cache controller 内部的 `dataAccessLatency`、`tagAccessLatency` 等参数单位是 **cycles**（@2GHz，1cy = 500ps），由 gem5 内部在 `schedule()` 时自动换算为 ps。

---

## 2. TRACE-PERF 打点方案

### 2.1 格式

```
[TRACE-PERF] <tick>|<node>|<component>|<reqId>|<pa>|<event>|<extra>
```

| 字段 | gem5 | ubio | nsim |
|------|------|------|------|
| tick | `curTick()` (ps) | `tick` (ps) | `_tick` (ps) |
| node | `_nodeId` (0..N) | `nid` | module id (= node × num_sockets + socket) |
| component | `gem5` | `ubio` | `nsim` |
| reqId | `msg.h.reqId` | `coh->h.reqId` | `m->hdr.req_id` |
| pa | `msg.h.homeLinePa` | `coh->h.homeLinePa` | `0x0` (不解析 payload) |
| event | `SEND` / `RECV` | `SEND_GEM5` / `SEND_NET` / `RECV_GEM5` / `RECV_NET` | `RECV` / `FWD` |
| extra | `dst=<n>` / `src=<n>` | 消息类型名 | `src=<n> dst=<n>` / `dst=<n>` |

### 2.2 打点位置

| # | 文件 | 行 | 事件 | 含义 |
|---|------|-----|------|------|
| ① | `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc` | `transportSend()` 中 `_port->send()` 成功后 | `gem5:SEND` | gem5 发消息到 ubio |
| ② | `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc` | `wakeup()` 中 `getPayload()` 成功后，Barrier 跳过之后 | `gem5:RECV` | gem5 从 ubio 收到消息 |
| ③ | `modules/ubiomodule/ubio_main.cc` | `pollAndProcess` 中收到 PAYLOAD 消息后 | `ubio:RECV_GEM5` / `ubio:RECV_NET` | ubio 收到 gem5/net 消息 |
| ④ | `modules/ubiomodule/ubio_main.cc` | `sendCoh()` 中 `port->send()` 成功后 | `ubio:SEND_GEM5` / `ubio:SEND_NET` | ubio 发消息到 gem5/net |
| ⑤ | `modules/networksim/networksim_main.cc` | `NetworkSim::step()` 中 `port->recv()` 后 | `nsim:RECV` | nsim 从 ubio 收到消息并入 FIFO |
| ⑥ | `modules/networksim/networksim_main.cc` | `NetworkSim::step()` 中 FIFO 出队 `send()` 后 | `nsim:FWD` | nsim 转发消息到目标 ubio |

### 2.3 输出文件

| 进程 | 输出路径 |
|------|---------|
| gem5 node N | `logs/.../gem5_tcXX_nodeN/stderr.log` |
| ubio node N socket S | `logs/.../ubio_nN_sS/stderr.log` |
| nsim | `logs/.../nsim_tcXX.log` |

### 2.4 解析方式

```bash
# 收集所有 TRACE-PERF 行，按 tick 排序
grep -h 'TRACE-PERF' logs/*/gem5_tc*_node*/stderr.log \
                        logs/*/ubio_n*/stderr.log \
                        logs/*/nsim_tc*.log \
  | sort -t'|' -k1 -n > all_traces.txt
```

---

## 3. 通信路径分段与延迟构成

### 3.1 系统端到端路径

```
  [CPU] ─(L1)─> [L2] ─(L3/HN-F)─> [EP_SNF]
                                    │
                           UBAdapter::transportSend()
                                    │  ← Tq (ZMQ IPC, 目标~100ns)
                                    ▼
                                [ubio]
                                  │  │
                         gem5Port │  │ netPort
                                  ▼  ▼
                    [UBCC Controller] ──── [networksim]
                                    ▲
                                    │  ← L_nsim (FIFO forwarding latency)
                                    ▼
                                [ubio]  (远端)
                                    │
                                    │  ← Tq (ZMQ IPC)
                                    ▼
                           UBAdapter::wakeup()
                                    │
                                [EP_SNF]
                                    │
                                  [HN-F] ──> [L3] ──> [DRAM]
```

### 3.2 延迟分段

| 段号 | 名称 | 路径 | 控制参数 |
|------|------|------|---------|
| A | L1+L2 命中 | sequencer → L1 → L2 hit | `CHI_config.py` L63-74 |
| B | L3 命中 | L2 miss → HN-F(L3) hit | `CHI_ubcc_framework.py` L203-205 HNFCache |
| C | Local DRAM | HN-F miss → DL_SNF → MemCtrl → DDR4 | `to_memory_controller_latency`; `DDR4_2400_8x8` 内部 timing |
| D | Remote DRAM (跨Socket) | = C + EP_SNF → ubio → nsim → ubio → EP_SNF | D = C + (2×Tq + L_nsim_跨socket) |
| E | Cross-node IO hop | ubio → nsim → 远端 ubio | `gen_topo.py` L28 link latency |
| F | Cross-socket IO hop | ubio → nsim → 同node另一socket的ubio | `gen_topo.py` 同node link latency |
| G | Tq (ZMQ IPC) | gem5 ↔ ubio 或 ubio ↔ nsim 单程 | ZMQ 内部，全局统一 |

---

## 4. 延迟目标与当前值

### 4.1 甲方参考值（目标）

| 指标 | 目标(ns) | 目标(ps) |
|------|---------|---------|
| core→Local Socket L3 | 15 | 15000 |
| core→Local Socket DRAM | 100 | 100000 |
| core→Remote Socket DRAM | 110 | 110000 |
| 跨Node同Socket IOModule 一跳 | 415 | 415000 |
| 同Node跨Socket IOModule 一跳 | 210~240 | 210000~240000 |

### 4.2 当前实测值（TC5，2026-07-07）

基于 reqId=144115188075855873 (Node2 读 Node1 DSM) 的 ReadReq 请求路径：

| 段 | 测量方法 | 当前值(ps) | 当前值(ns) | 可比目标 |
|----|---------|-----------|-----------|---------|
| Tq (gem5→ubio) | ubio RECV_GEM5 − gem5 SEND | 100000 | 100 | Tq: ~100ns ✓ |
| Tq (ubio→nsim) | nsim RECV − ubio SEND_NET | 100000 | 100 | Tq: ~100ns ✓ |
| L_nsim (FIFO+转发) | nsim FWD − nsim RECV | 187500 | 187.5 | — |
| Tq (nsim→ubio) | ubio RECV_NET − nsim FWD | 100000 | 100 | Tq: ~100ns ✓ |
| **总 IO hop** | ubio RECV_NET − gem5 SEND | **387500** | **387.5** | 跨Node: 415ns |
| ubio 本地处理(ubio→gem5) | gem5 RECV − ubio SEND_GEM5 | ~100000 | ~100 | Tq |

**关键发现**：
- Tq ≈ 100ns（100000ps），与甲方给的目标一致
- 当前 nsim 内部延迟 ~187.5ns，加上两边 Tq 各 100ns，**总 IO hop ≈ 387.5ns，接近目标的 415ns**
- nsim link latency 当前在 `gen_topo.py` 中统一设为 100000ps(100ns)，但实际 FIFO delay 约为 187500ps。这额外的 ~87.5ns 来自 `safeTs` 同步等待和 ZMQ polling 间隙

### 4.3 各段 Tq 验证

| 跳跃 | 事件对 | Δ(ps) | Δ(ns) |
|------|-------|-------|-------|
| gem5→ubio | ubio RECV_GEM5 − gem5 SEND | 100000 | 100 |
| ubio→nsim | nsim RECV − ubio SEND_NET | 100000 | 100 |
| nsim→ubio | ubio RECV_NET − nsim FWD | 100000 | 100 |
| ubio→gem5 | gem5 RECV − ubio SEND_GEM5 | ~100000 | ~100 |

**结论**：ZMQ 链路确实全局统一为 ~100ns，与甲方数据 Tq=100ns 吻合。3.1 中的 `2×Tq + L_nsim` 模型成立。

---

## 5. 如何测量各段延迟

### 5.1 收集数据

```bash
# 跑 TC 并收集 trace（以 TC5 为例）
TIMEOUT_SEC=120 bash tests/e2e/run_multi.sh --1s 5
LOG_DIR=$(ls -td logs/*_1s | head -1)

# 提取所有 TRACE-PERF，按 tick 排序
grep -h 'TRACE-PERF' $LOG_DIR/gem5_tc5_node*/stderr.log \
                       $LOG_DIR/ubio_n*_s*/stderr.log \
                       $LOG_DIR/nsim_tc5.log \
  | sort -t'|' -k1 -n > /tmp/tc5_traces.txt
```

### 5.2 按 reqId 分链

```bash
# 列出所有唯一的 reqId（排除 barrier reqId=7 和内部 reqId=1）
awk -F'|' '$3=="ubio" && $4!=1 && $4!=7 {print $4}' /tmp/tc5_traces.txt | sort -u

# 提取特定 reqId 的完整链路
grep 'reqId=72057594037927937' /tmp/tc5_traces.txt  # Node1 的请求
```

### 5.3 逐段测量（手工示例）

以一次 ReadReq（reqId=X, pa=Y）为例：

```
1. gem5_S SEND ReadReq  tick=t0
2. ubio_S RECV_GEM5     tick=t1     → Tq     = t1−t0
3. ubio_S SEND_NET      tick=t2     → ubio处理 = t2−t1 (≈0)
4. nsim RECV src=S      tick=t3     → Tq     = t3−t2
5. nsim FWD dst=D       tick=t4     → nsim处理 = t4−t3
6. ubio_D RECV_NET      tick=t5     → Tq     = t5−t4
7. ubio_D SEND_GEM5     tick=t6     → ubio处理 = t6−t5
8. gem5_D RECV          tick=t7     → Tq     = t7−t6

总IO hop = t5 − t0 = 2×Tq + nsim_processing
          = (t1−t0) + (t3−t2) + (t4−t3) + (t5−t4)  ≈ 387500ps ≈ 387.5ns
```

### 5.4 目标文件对照

| 调参目标 | 文件路径 | 参数 | 当前值 | 目标对应关系 |
|---------|---------|------|-------|------------|
| L1 latency | `gem5/configs/ruby/CHI_config.py:63-64` | `dataAccessLatency=1`, `tagAccessLatency=1` | 2cy(1ns) | 总和 L1+L2+L3 ≤ 15ns |
| L2 latency | `gem5/configs/ruby/CHI_config.py:68-69` | `dataAccessLatency=2`, `tagAccessLatency=1` | 3cy(1.5ns) | — |
| L3 latency | `gem5/configs/ruby/CHI_ubcc_framework.py:203-205` | `dataAccessLatency=30`, `tagAccessLatency=6` | 36cy(18ns) | 须大幅降低 |
| DRAM latency | `gem5/src/mem/DRAMCtrl.py` (DDR4_2400 timings) | tCAS, tRCD 等 | gem5 默认 | ±100ns → 调 MemCtrl 参数 |
| Cross-node IO hop | `scripts/gen_topo.py:28` | `latency = 100000` | 100ns(link) + extra | → ~215000ps (配合 Tq 后到 415ns) |
| Ruby NoC | `gem5/configs/ruby/CHI_config.py:109-111` | `router_link_latency=1`, `router_latency=1` | 2cy(1ns) | 保持 1cycle |

---

## 6. 后续校准步骤

1. **降低 L3 cache latency**：`CHI_ubcc_framework.py` HNFCache 的 `dataAccessLatency` 30→10, `tagAccessLatency` 6→5
2. **区分 nsim link latency**：`gen_topo.py` 按同 node/跨 node 设置不同的 `latency` 参数
3. **跑 TC1/T2/TC5 验证**：用 TRACE-PERF 工具测量每段 Δ，对比目标值
4. **迭代微调**：差值 >10% 则微调对应参数，重复 3
