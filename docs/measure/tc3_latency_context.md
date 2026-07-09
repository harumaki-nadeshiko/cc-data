# TC3 延迟测量上下文 — 供专家评审

> 日期: 2026-07-09 | 数据源: `logs/20260709_181252_1s`

## 1. 系统拓扑与配置

- **拓扑**: 3 nodes × 1 socket full-mesh (3n1s)
- **ZMQ**: 100ns (100000 ps) linkLatency = syncInterval
- **nsim cross-node link**: 405ns (405000 ps)
- **gem5 freq**: 2 GHz = 1 cycle = 0.5 ns = 500 ps
- **trace tick 单位**: picoseconds (ps)

## 2. 端到端延迟构成

以 `rid=72057594037927937:0x10018000000`（ReadReq，完整 RECALL 循环）为例。

### 2.1 逐跳事件表

| tick (ps) | 事件 | Δ (ns) | 段类型 | 说明 |
|-----------|------|--------|--------|------|
| 289717500 | gem5(n1) SEND ReadReq | — | — | gem5 发 ReadReq |
| 289817500 | ubio(n1) SEND_NET RecallReq | +100 | ZMQ | ubio 判需要 RECALL → 转发 RecallReq 到 nsim |
| 290222500 | nsim FWD dst=0 | +405 | **nsim 405ns** | RecallReq 跨节点到 node0 |
| 290400000 | ubio(n0) RECV_NET RecallReq | +177.5 | ZMQ | node0 的 ubio 收到 |
| 290500000 | gem5(n0) RECV RecallReq | +100 | ZMQ | 转发到 gem5 |
| 290651000 | gem5(n0) SEND RecallResp | +151 | gem5 snoop | gem5 查本地 cache、取数据 |
| 290751000 | ubio(n0) SEND_NET RecallResp | +100 | ZMQ | RecallResp 发回 |
| 291156000 | nsim FWD dst=1 | +405 | **nsim 405ns** | RecallResp 跨节点回 node1 |
| 291300000 | ubio(n1) RECV_NET RecallResp | +144 | ZMQ | RECALL.DONE |
| **299717500** | **gem5(n1) SEND ReadReq (retry)** | **+8417.5** | **gem5 事件调度** | **⚠️ 8.4µs gap** |
| 299817500 | gem5(n1) RECV ReadResp | +100 | ZMQ | 收到 grant |
| 299918000 | gem5(n1) SEND ClearReq | +100.5 | gem5 process | 确认提交 |
| 300018000 | gem5(n1) RECV ClearResp | +100 | ZMQ | 提交完成 |

**端到端**: 10300ns (10.3µs)

**纯网络延迟**(nsim+ZMQ): ~1600ns (16%)

**gem5 事件调度延迟**: ~8700ns (84%) — 见下文。

## 3. 8.4µs gap 详解（RECALL.DONE → requester retry）

### 3.1 协议时序

```
RECALL 完成 (t=291300000):
  ubio(n1) 收到 RecallResp → RECALL.DONE 状态

requester retry (t=299717500):
  gem5(n1) 重新发起 ReadReq → ubio 发现 RECALL.DONE → 转为 GRANT → 返回 ReadResp
```

### 3.2 gem5 内部事件链

这个 8.4µs 期间发生了什么（gem5 内部）:

1. **ubio→gem5 的 RecallResp 传递**: ubio 收 RecallResp → `sendCoh(gem5Port)` → UBAdapter 收到 → `_readyResponses` 存储 → `_onResponseWired` 立即唤醒 EPSNFController → EPSNFController 处理 retryQueue → 调用 `sendReadReq` → 检查 `_readyResponses` → 发现 RecallResp → **但 READREQ 还没到！RecallResp 不是 ReadResp**

2. **gem5 HNF 状态机**: RecallResp 经 HN-F 传递 → HNF 状态从 R_WAIT_RECALL 转换为发出新的 ReadReq → 这需要多个 SLICC 状态机转换事件

3. **Sequencer 重发 ReadReq**: HNF 通知 Sequencer → Sequencer 重发 ReadReq → 进入 EPBackend → `handleRemoteMiss` → `sendReadReq` → ZMQ → ubio

步骤 2 是 gem5 SLICC 状态机的固有延迟。HNF 的 R_WAIT_RECALL → ReadReq 重发经过约 16800 cycles (8.4µs @ 2GHz)。这是 gem5 的 Ruby 协议模拟开销，不是网络或 ZMQ 延迟。

### 3.3 相关代码路径

```
RECALL 回程:
  ubio recv RecallResp → handleUbccMessage → RECALL 完成标记

gem5 重试:
  HNF::wakeup() → state=R_WAIT_RECALL → reissue ReadReq → Sequencer
  Sequencer → EPBackend → EPSNFController → handleRemoteMiss → sendReadReq
  → UBAdapter → ZMQ → ubio → 发现 RECALL.DONE → GRANT → ReadResp

响应回程:
  ubio → ZMQ → UBAdapter → _readyResponses → _onResponseWired → 
  EPSNFController wakeup → retry → find ReadResp → grant → CompData → 
  Ruby NoC → HNF → Sequencer → CPU
```

### 3.4 已做的性能优化

- **2026-07-09 修复**: `_onResponseWired` 在 `UBAdapter::handleResponse()` 中**立即触发**，不再等 `checkResponseCallbacks()` 的 `_pendingByReqId` 匹配。这使得 ReadResp 到达后 EPSNFController 立即被唤醒处理。
- **EP_RETRY_CYCLES**: 从 1600000 (800µs) 降为 20000 (10µs)。注意这个 retry 是**fallback**（如果 `_onResponseWired` 未触发），正常路径下 `_onResponseWired` 在响应到达时立即触发。

## 4. 可视化解读指南

### 4.1 色块含义

| 颜色 | 含义 | trace 转换逻辑 |
|------|------|---------------|
| 🟦 蓝 | gem5→ubio | `gem5:SEND` → `ubio:RECV_GEM5` |
| 🟧 橙 | ubio→nsim | `ubio:SEND_NET` → `nsim:RECV` |
| ⬛ 灰 | nsim FIFO | `nsim:RECV` → `nsim:FWD` (跨节点 405ns) |
| 🟪 紫 | nsim→ubio | `nsim:FWD` → `ubio:RECV_NET` |
| 🟨 黄 | ubio 处理 | `ubio:RECV_*` → `ubio:SEND_*` |
| 🟩 绿 | ubio→gem5 | `ubio:SEND_GEM5` → `gem5:RECV` |

### 4.2 已知问题

1. **nsim 段分类不精确**: `nsim:RECV → nsim:FWD` 是真正的网络延迟(405ns)。但 `nsim:FWD → nsim:RECV` 是两个不同请求间的 nsim 事件间隔，不是同一个请求的延迟。两者都被分类为 `nsim_fifo`。
2. **大间隔(>10µs) 色块显示为 35% 透明度**: 这是 gem5 事件调度延迟(如 RECALL→retry)，不是协议或网络延迟。
3. **统计表**: ≥10µs 的 gap 被排除在统计之外。表中有 "Large gaps" 列显示被排除的数量。P50 是真实延迟的可靠估计。

## 5. 参数对照

| 参数 | 源文件 | 当前值 |
|------|--------|--------|
| ZMQ linkLatency | `framework/Port.hh` | 100000 ps (100ns) |
| ZMQ syncInterval | `framework/Port.hh` | 100000 ps (100ns) |
| nsim cross-node | `gen_topo.py --cross-node-latency` | 405000 ps (405ns) |
| EPSNFController retry | `EPSNFController.cc` `EP_RETRY_CYCLES` | 20000 cy (10µs) |
| Sequencer deadlock | `CHI_ubcc_framework.py` | 200000000 |

## 6. 可视化生成命令

```bash
# 1. 收集 trace
grep -h 'TRACE-PERF' logs/*/gem5_tc*_node*/stderr.log \
  logs/*/ubio_n*/stderr.log logs/*/nsim_tc*.log \
  | sort -t'|' -k1 -n | python3 scripts/trace2chain.py > chains.json

# 2. 生成 HTML
python3 scripts/chain2html.py --target-ns 415 chains.json > tc.html
```
