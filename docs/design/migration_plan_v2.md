# CC-EP 目标架构迁移总方案 v2（migration_plan_v2）

> **本文档定位**：本文是 `docs/legacy/migration_plan.md`（2026-06-19）的**修订继任版**。
> legacy 版基于"Ns3UB channel + 外部全局逻辑时钟 + gem5 侧 backstore 回调"的设想；
> 其后仓库已按**不同技术路线**完成大部分独立化落地。本文以**当前代码与设计文档为权威基线**，
> 重写目标架构、模块边界、关键接口，并补全 legacy 版缺失的"架构详细介绍"与"各模块编译/运行具体方式"。
>
> **与 legacy 版的关系**：legacy 版不再作为实施依据，仅保留为决策历史。所有"迁什么、怎么迁、怎么编译、怎么跑"
> 以本文为准。
>
> **权威输入**：`docs/expert_entrypoint.md`、`docs/design/{scheme_v4,system_topology,current_topology,
> multi_node_split,decoupling,build_system,port_refactoring,refactoring_master_plan,dual_socket_design}.md`、
> 以及 `framework/`、`modules/`、`tools/`、`gem5/src/mem/ruby/protocol/chi/ep/`、`scripts/`、`tests/e2e/` 的实际代码。

---

## 1. 结论先行

### 1.1 目标架构已基本落地

legacy 版把"独立化"当成未来计划；**当前仓库已经把 UBCC 协议权威层、resident metadata 层、message runtime 层
物理外移出 gem5**，并以 **ZeroMQ IPC + framework::Port + safeTs 时钟同步** 的多进程拓扑运行。因此本方案的核心
不再是"要不要迁"，而是"**目标架构长什么样、各模块如何编译与运行、还剩什么收尾**"。

**已完成的独立化（对照 legacy §1.1 的 5 个核心模块）**：

| legacy 计划要外移的模块 | 当前位置 | 状态 |
|---|---|---|
| `UBCCController.{hh,cc}` | `modules/ubiomodule/UBCCController.{hh,cc}` | ✅ 已外移，不再是 SimObject |
| `ResidentDir.{hh,cc}` | `modules/ubiomodule/ResidentDir.{hh,cc}` | ✅ 已外移，内嵌于 UBCCController |
| `UBRouter` 语义 | `modules/ubiomodule/UBIOModule.{hh,cc}` + ubio 进程内路由 | ✅ 已外移为 runtime 路由 |
| `UBMsgQueue.hh` | `modules/ubiomodule/CoherenceMessageQueue.hh` | ✅ 已外移并改名 |
| `UBMsg.hh` | `modules/ubiomodule/CoherenceMessage.hh` | ✅ 已外移并改名 |

**保留在 gem5 的模块（与 legacy §1.1 一致，已确认）**：
`UBAdapter`、`EPBackend`、`EPRNFController`、`EPSNFController`、`MetaRNFController` —— 均在
`gem5/src/mem/ruby/protocol/chi/ep/`，且 gem5 侧已**不再包含** `UBCCController/UBIOModule/ResidentDir`
的任何源码（`SConscript` 只编译 EP* + UBAdapter + NodeAddressMap + BackstoreSchemaA/C + SelfTest）。

### 1.2 与 legacy 设想的关键技术路线差异

| 维度 | legacy 设想（Q1–Q4） | 当前实际落地 | 差异性质 |
|---|---|---|---|
| 跨进程信道 | Ns3UB channel | **ZeroMQ IPC PAIR socket**（`framework/Port`） | 路线变更 |
| 全局时钟 | 外部全局逻辑时钟（Q3=A） | **CONTROL_SYNC 心跳 + `safeTs` 滑动窗口**（涌现式同步） | 路线变更 |
| 两级时延 | Router 本地 FIFO + Ns3UB 注入（Q4=C） | **ubio 进程内路由 + networksim 链路 FIFO 延迟** | 形式保留 |
| wire format | `UbWireHeaderV1` 固定 envelope（Q2=B） | **`MemMessage`（40B header + ≤1024B payload）+ `CoherenceMessage` payload** | 形式变更 |
| backstore 回调 | gem5 侧 `IUbccHost` 回调（Q1=B） | **ubio 进程内 `UbioBackstoreHost`（内存 map）** | 路线变更 |
| host/outbound 接口 | `IUbccHost` + `IRouterEgress` | **`UBCCHostIf` + `UBCCOutboundIf`**，均由 ubio 实现 | 形式变更 |
| 进程粒度 | gem5 1 进程 + standalone UBCC 1 进程 | **每节点 1 gem5 进程 + 每 (node,socket) 1 ubio 进程 + nsim + barrier** | 大幅扩展 |

> **结论**：legacy 的"边界清晰化 → 协议固化 → 进程解耦 → 时钟/链路外置 → 全量回归"五阶段方向正确，
> 但落地手段全部替换为 ZMQ/safeTs 路线。下文以**实际落地**为准重写。

### 1.3 剩余收尾工作（不是从零迁移）

1. backstore 消息化（当前 `UbioBackstoreHost` 是 ubio 进程内 `std::map`，未做跨进程持久化/消息化）
2. dual-socket 完整收敛（TC32–35/39 已支持 `num_sockets=2`，NUMA latency 元信息仍待补）
3. gem5 侧残留 `BackstoreSchemaA/C`、`NodeAddressMap`、`CoherenceMessage.hh` 的去重/物理清理
4. networksim → 真 ns-3 替换（当前是最小 FIFO 路由器）
5. TC 全量回归 + 故障注入 + A/B 等价收敛

---

## 2. 目标架构详细介绍

### 2.1 三层架构总览

```text
┌─────────────────────────────────────────────────────────────────────────┐
│  Layer 1：gem5 仿真进程（每节点 1 个，--node-id=N）                        │
│                                                                          │
│  CPU/L1/L2 ──CHI── HN-F(i,k) ──ReadNoSnp/WriteNoSnp── EP-SNF(i,k)        │
│                                   │                                      │
│                                   └── EPBackend(i) ── EP-RNF(i)          │
│                                          │                               │
│                                       UBAdapter(i,k)                     │
│                                          │ framework::Port (ZMQ PAIR)    │
└──────────────────────────────────────────┼──────────────────────────────┘
                                           │ ipc://...（同主机，延迟≈0 边界）
┌──────────────────────────────────────────┼──────────────────────────────┐
│  Layer 2：ubio 进程（每 (node,socket) 1 个） │                              │
│                                          ▼                               │
│  framework::Port ── UBIOModule(runtime 路由) ── UBCCController             │
│                                                   │  ResidentDir(内嵌)    │
│                                                   │  UbioBackstoreHost    │
│                                                   │  UBCCHostIf/OutboundIf│
│                                          │                               │
│                                       framework::Port (ZMQ PAIR)         │
└──────────────────────────────────────────┼──────────────────────────────┘
                                           │ ipc://...
┌──────────────────────────────────────────┼──────────────────────────────┐
│  Layer 3：networksim 进程（1 个，跨节点链路 FIFO）                          │
│  ForwardTable + per-link latency FIFO ── 按 dst_module 路由               │
└──────────────────────────────────────────────────────────────────────────┘
                                           +
┌──────────────────────────────────────────────────────────────────────────┐
│  barrier_manager 进程（1 个，跨节点 barrier 协调）                          │
└──────────────────────────────────────────────────────────────────────────┘
```

### 2.2 进程拓扑

**N 节点 × K socket 的完整进程拓扑**（`tests/e2e/run_multi.sh:7-11` 的实际实现）：

```text
barrier_manager   (1)
networksim        (1)            ← 按 (node,socket) 全 mesh，latency 统一 100000 ticks
ubio_n{0..N-1}_s{0..K-1}        (N×K)   ← 每 (node,socket) 平面 1 个，gid = node*K + socket
gem5 --node-id={0..N-1}         (N)     ← 每节点 1 个进程，内部含 K 个 UBAdapter
                                  ─────────────
                    总计: N*K + N + 2 个进程
```

**启动顺序（强制约束，`run_multi.sh:100-191`）**：

```text
1) barrier_manager   (必须先 bind)
2) networksim        (必须先 bind)
3) gem5 --node-id=i  (N 个，各自 bind 本节点 Port，等待 STEP5 "Port enabled")
4) ubio --node=i --socket=k  (N×K 个，在所有 gem5 bind 完成后启动，connect 到已 bind 的 endpoint)
```

> 注意：**gem5 先于 ubio 启动**（gem5 是 bind 侧，ubio 是 connect 侧）。`run_multi.sh` 会等待每个 gem5
> 打印 `STEP5.*Port enabled` 后才启动 ubio（`run_multi.sh:148-166`）。

**关闭顺序**：gem5 完成 workload 后退出 → ubio 通过 `peerStaleMs(5000)` 检测到本地 gem5 沉默后
`markPeerDone()` 解除时钟约束 → 全部 gem5 退出后 launcher kill ubio/nsim/barrier。

### 2.3 节点内架构（dual-socket）

每节点（`gem5 --node-id=i` 进程内）按 `num_sockets=K` 构建（`docs/design/current_topology.md`、
`docs/design/dual_socket_design.md`）：

```text
Node i (单 gem5 进程):
  CPU clusters (each cluster has explicit socket_id)
    │ addr_range 路由
    ├── DSM(*,0)/local(0)/meta(0) ──→ HN-F(i,0) ──→ EP-SNF(i,0)
    └── DSM(*,1)/local(1)/meta(1) ──→ HN-F(i,1) ──→ EP-SNF(i,1)
                                          │
                                   EPBackend(i)  [单实例，_epSnfs[K], _ubAdapters[K]]
                                          │
                                   EP-RNF(i)     [单实例，_hnfVersions[K]，按 PA.homeSocket 选 HN-F]
                                          │
                              UBAdapter(i,0) / UBAdapter(i,1)   [每 socket 1 个，各持 1 个 Port]
```

**关键约束**（`dual_socket_design.md` §0 冻结决策）：
- EP-RNF 保持**全局单个** `_chiRequestInFlight` 串行化，不做 per-socket 并发
- `num_sockets>1` 时 EP-RNF 必须拿到完整 per-socket HN-F 版本槽位，缺失即 `fatal`
- `num_sockets=1` 必须严格退化为单 HN-F/单 EP-SNF 行为

### 2.4 跨节点拓扑与路由

```text
           Node 0                              Node 1
   ┌──────────────────┐               ┌──────────────────┐
   │ UBRouter(0,0) ───lat_inter_node──→ UBRouter(1,0)   │
   │      │                                  │          │
   │      │ (同节点跨 socket: lat_numa)        │          │
   │ UBRouter(0,1) ───lat_inter_node──→ UBRouter(1,1)   │
   └──────────────────┘               └──────────────────┘

   UBRouter 连通规则：
     (i,k) ↔ (j,k)  当 i≠j    (同 socket 平面，跨节点)
     (i,0) ↔ (i,1)            (同节点，跨 socket)
   gid = node*K + socket 是 networksim 的路由键
```

跨节点 DSM 请求链（`current_topology.md:90-130`）：

```text
CPU(socket=r) → HN-F(i,homeSocket=k) → EP-SNF(i,k) → EPBackend(i)
  → UBAdapter(i, srcSocket=r) → [ubio(i,r) 路由]
    → UBRouter(i,r) → UBRouter(j,r) [lat_inter_node]
      → UBRouter(j,r) → UBRouter(j,k) [lat_numa, 若 r≠k]
        → UBCC(j,k) 本地投递 → processOuterRequest → grant
  → 响应沿同 socket 平面反向返回
```

### 2.5 PA 布局与地址归属

```text
NODE_ADDR_SHIFT = 40
PHY_BASE_i      = i << 40
SEG_SIZE        = 128 MB

Per-node 窗口（dual-socket 后）：
  [0*SEG, 1*SEG) : local_private total window   (按 socket 均分)
  [1*SEG, 2*SEG) : metadata_private total window (按 socket 均分，原 ubcc_exclusive 删除)
  [2*SEG, ...)   : DSM(*, socket)               (per-socket DSM 段)
  [metadata_backstore_base, ...) : 16MB backstore (按 socket 切分)
```

- `homeSocket` 由 PA 编码决定；`homeNode` 由 PA 编码决定
- `UBCC(j,k)` 是 `DSM(*,k)` 在 Node j 上的 home directory
- `HN-F(i,k)` 覆盖 `local_private(k) + metadata_private(k) + DSM(*,k)`

### 2.6 消息层（MemMessage + CoherenceMessage）

**传输层** `framework/MemMessage.hh`：

```text
MemMessageHeader (40B，固定):
  uint64_t timestamp;     // 发送时刻 + linkLatency
  uint32_t size;          // header + payload 总长
  uint32_t type;          // MemMessageType
  uint32_t src_module;    // launcher 分配的 module id（gid）
  uint32_t src_port;
  uint32_t dst_module;
  uint32_t dst_port;
  uint64_t req_id;        // txn matching id

MemMessage:
  MemMessageHeader hdr;
  uint8_t payload[1024];  // kMaxPayloadSize = 1024

MemMessageType:
  CONTROL_SYNC=0, TERMINATE=1, COH_MSG=2,
  BARRIER_REACHED=3, BARRIER_RELEASE=4, PORT_HELLO=5, PORT_HELLO_ACK=6
```

**协议层** `CoherenceMessage`（原 `UBMsg`，承载在 `COH_MSG` 的 payload 中）：
保留 legacy §4.1.3 的不变约束 —— `homeLinePa` 是唯一 canonical key；主事务键
`(homeLinePa, requesterNode, reqId)`；`epoch` 按 half-range 比较；`seqNum` 单调递增；
`CFLAG_*`（WRITE_INTENT/KEEP_AS_CLEAN/ACCEPTED/DATA_RETURNED/HAS_DATA/IS_READ_RECALL/BUSY）语义不变。

消息类型集（`tools/ubio/ubio_main.cc:29-66` 的 ingress 分类）：
- **UBCC ingress**（发往 home 目录）：`ReadReq, WritebackReq, EvictReq, UpgradeReq, UpgradeDoneReq,
  ClearReq, RecallResp, InvalidateAck, QueryLineMetaReq, HomeWritebackNotify`
- **gem5 ingress**（发往 EP 侧）：`RecallReq, InvalidateReq, ReadResp, WritebackResp, EvictResp,
  UpgradeResp, UpgradeDoneResp, ClearResp, UpgradeAckNotify, QueryLineMetaResp`

### 2.7 时钟同步模型（替代 legacy Q3=A 外部全局逻辑时钟）

**这是与 legacy 版最大的路线差异。** 当前不引入外部全局逻辑时钟，而是用
**CONTROL_SYNC 心跳 + `safeTs` 滑动窗口**让多进程相互约束、涌现出全局时序。

**`Port::sendAllocateBuffer(timestamp)`**：每条消息打 `timestamp + _linkLatency`。
**`safeTs(curT)`**（`framework/Port.hh:94`，`expert_entrypoint.md:177-179`）：

```cpp
safeTs(curT) = min(receiveTimestamp(), lastSyncTs + syncInterval)
// receiveTimestamp() = _pending ? _pendingT : _lastRxT
```

**CONTROL_SYNC 心跳**（`emitSync(tick)`）：发送 `timestamp = tick + linkLatency` 的同步消息；
对端收到后 `_lastSyncTs = max(_lastSyncTs, msg.timestamp)`；按 `syncInterval` 限速。

**各进程主循环时钟推进规则**（统一模式）：

| 进程 | 推进规则 | 代码位置 |
|---|---|---|
| gem5 (UBAdapter) | `safeT = port->safeTs(curTick()); nextT = max(safeT, curTick()); reschedule(event, nextT)` | `expert_entrypoint.md:188-198` |
| ubio | `minTs = min(gem5Port.safeTs, netPort.safeTs); tick = minTs if minTs>tick else yield` | `ubio_main.cc:874-901` |
| networksim | `minTs = min over ports safeTs(_tick); _tick = minTs if minTs>_tick else yield` | `networksim_main.cc:170-181` |
| barrier | `tick = max(minSafeTs, tick) else tick++` | `barrier_main.cc:92-97` |

**核心原则**：**任何进程都不用 `++tick` 漂移前进**；被对端约束时 `yield()` 等待，保持
clock-locked 到最慢对端。这避免了 legacy 版担忧的"各进程读本地时间导致定时字段不可比"。

**liveness**：`peerStaleMs(thresholdMs)`（`Port.hh:109`）检测对端 wall-clock 沉默超阈值，
调用 `markPeerDone()` → `failClosed()` → `safeTs()` 返回 `UINT64_MAX`（不再构成时钟约束）。
ubio 用此机制处理"本地 gem5 完成 workload 退出但未发 TERMINATE"的场景（`ubio_main.cc:883-895`）。

### 2.8 两级时延模型（legacy Q4=C 的实际落地形式）

```text
gem5 UBAdapter send (timestamp = curTick + linkLatency)
   ↓ framework::Port (ZMQ, 同主机，≈0 物理延迟，逻辑延迟由 timestamp 体现)
ubio 进程内路由（按 dstNode/dstSocket 路由，本地立即处理或转发）
   ↓ framework::Port → networksim
networksim: per-link FIFO + latency（topo.json 中 100000 ticks = 100ns）
   ↓ framework::Port
目标 ubio 进程本地投递到 UBCCController
```

- **本地顺序性**：由 ubio 进程单线程主循环 + Port 单发送槽（`TxHandle` RAII）保证
- **跨进程传播时间**：由 `networksim` 的 `_fifo`（`PendingFwd.readyTick = _tick + lat`）注入
- **时钟对齐**：`safeTs` 保证消息不会在被对端时钟约束之前"提前可见"

---

## 3. 模块-文件清单与依赖关系（实际落地版）

### 3.1 已外移到 `modules/ubiomodule/` 的文件

| 文件 | 职责 | 备注 |
|---|---|---|
| `UBCCController.{hh,cc}` | home/global metadata 权威、Outstanding/Tombstone/Queue 核心；实现 `UBCCHostIf`/`UBCCOutboundIf` 消费者侧 | 不再继承 SimObject |
| `UBIOModule.{hh,cc}` | runtime 路由、端口管理、transit forwarding | 不再继承 SimObject |
| `ResidentDir.{hh,cc}` | resident metadata SRAM + BF + victim/offload，内嵌于 UBCCController | 算法/编码不变 |
| `CoherenceMessage.hh` | UB wire message 语义（原 `UBMsg.hh`） | 改名 |
| `CoherenceMessageQueue.hh` | per-pair FIFO（原 `UBMsgQueue.hh`） | 改名 |
| `BackstoreTypes.hh` / `BackstoreOrganization.hh` / `BackstoreSchema{A,C}.{hh,cc}` | backstore schema | gem5 侧仍有同名副本（待清理，见 §3.4） |
| `NodeAddressMap.{hh,cc}` | PA → (homeNode, homeSocket) 映射 | gem5 侧仍有同名副本（待清理） |
| `gem5_shim.hh` | 替换 gem5 依赖的最小 shim | 供独立编译 |

### 3.2 保留在 `gem5/src/mem/ruby/protocol/chi/ep/` 的文件

| 文件 | 职责 | `SConscript` 编译 |
|---|---|---|
| `EPBackend.{cc,hh,py}` | CHI ↔ outer protocol 主控，txn 级消息桥 | ✅ |
| `EPRNFController.{cc,hh,py}` | RN-F snoop/recall/upgrade CHI 路径 | ✅ |
| `EPSNFController.{cc,hh,py}` | SNF 数据路径 | ✅ |
| `MetaRNFController.{cc,hh,py}` | metadata backstore 的 gem5 侧 CHI I/O 代理 | ✅ |
| `UBAdapter.{cc,hh,py}` | gem5 边界：消息编解码 + pending completion + Port 轮询 | ✅ |
| `NodeAddressMap.{cc,hh}` | gem5 侧 PA 映射（与 ubiomodule 副本重复） | ✅ |
| `BackstoreSchema{A,C}.{cc,hh}` | gem5 侧 backstore schema（与 ubiomodule 副本重复） | ✅ |
| `CoherenceMessage.hh` | gem5 侧消息语义头（与 ubiomodule 副本重复） | 头文件 |
| `M{4..8}SelfTest.cc` | EP 自测 | ✅ |

### 3.3 共享框架层 `framework/`

| 文件 | 职责 |
|---|---|
| `Port.{hh,cc}` | ZMQ PAIR 双工端口：`init/send/recv/allocateSendBuffer/emitSync/safeTs/terminate/peerStaleMs` |
| `MemMessage.hh` | 传输层消息（40B header + 1024B payload）+ `MemMessageType` |
| `PortEnvLoader`（`Port.hh:148`） | endpoint 命名生成器（`ubioGem5Port/ubioNetPort/nsimUbioPort/barrierPort`） |
| `TxHandle`（`Port.hh:52`） | RAII 发送句柄，替代 `_sendBufInUse` |
| `ZMQChannel.{hh,cc}` / `ZMQTransport.{hh,cc}` | legacy/兼容封装 |
| `PseudoMemPort.*` / `PseudoManager.*` / `PseudoMemPacket.hh` | 早期本地队列抽象（迁移_guide 记载，运行态已被 Port 取代） |

### 3.4 独立进程入口

| 进程 | 入口源 | 产物 |
|---|---|---|
| ubio | `tools/ubio/ubio_main.cc` | `build/bin/ubio` |
| networksim | `modules/networksim/networksim_main.cc` | `build/bin/networksim` |
| barrier_manager | `tools/barrier/barrier_main.cc` | `build/bin/barrier_manager` |
| gem5 | `tests/e2e/test_e2e.py`（配置入口）+ `configs/ruby/CHI_ubcc_framework.py` | `gem5/build/ARM/gem5.opt` |

### 3.5 待清理的重复（§1.3 收尾项 3）

`BackstoreSchemaA/C`、`NodeAddressMap`、`CoherenceMessage.hh` 在 `gem5/.../ep/` 与
`modules/ubiomodule/` 各有一份。gem5 侧是否还需保留取决于 `EPBackend/UBAdapter/MetaRNF` 是否
仍直接引用；收尾时应让 gem5 侧只通过 `framework/` 公共头 + Port 消息语义访问，物理删除 gem5 侧副本
（对应 `refactoring_master_plan.md` Task 5 方案 B 的"物理删除"）。

### 3.6 关键耦合点（当前边界）

#### A. UBCC ↔ host/outbound（ubio 进程内）

```cpp
// tools/ubio/ubio_main.cc:299
struct UbioBackstoreHost : public UBCCHostIf, public UBCCOutboundIf {
    // UBCCHostIf:  hostIssueBackstoreRead/Write/Delete  → 进程内 std::map + ResidentDir.bloomInsert/Remove
    // UBCCOutboundIf: sendRecallReq/sendInvalidateReq/sendUpgradeAckNotify → routeControlToTarget via Port
};
UBCCController ubcc(nid, sid, nullptr);
UbioBackstoreHost host(ubcc, gem5Port, netPort, nid, sid, tick);
ubcc.setHost(&host);       // backstore 回调
ubcc.setOutbound(&host);   // recall/invalidate/upgradeAckNotify 发送
```

> **与 legacy §3.3.A 的差异**：legacy 把 backstore 当 gem5 侧 `IUbccHost` 回调；当前 backstore 完全在
> ubio 进程内（内存 map），不回 gem5。`notifyGrantVisible` 未作为独立 callback，grant 可见性 tick 通过
> `ReadResp` 的 `grantVisibleTick/sentinelVisibleTick` 字段回传。

#### B. UBAdapter ↔ framework::Port（gem5 ↔ ubio 边界）

`UBAdapter` 持有 `framework::Port*`，发送走 `Port::allocateSendBuffer + TxHandle::send`，
接收在 `wakeup()` 周期事件里 `Port::recv` → 解码 `CoherenceMessage` → 回调 EPBackend。
EPBackend/EPRNF/EPSNF 不感知 UBCC 已出进程。

#### C. 各进程 ↔ 时钟系统

无外部时钟注入；所有进程通过 `Port::emitSync` + `Port::safeTs` 相互约束（§2.7）。

---

## 4. 关键接口规范（实际落地版）

### 4.1 framework::Port 接口（`framework/Port.hh`）

```cpp
class Port {
  public:
    bool init(const PortParams& params, const PortRuntime& runtime = PortRuntime());
    void terminate();          // best-effort TERMINATE + 立即本地清理
    void closeLocal();         // 仅本地清理
    TxHandle* allocateSendBuffer(uint64_t timestamp);  // timestamp = curTick + linkLatency
    MemMessage* recv(uint64_t curT, ReceiveStatus* status = nullptr);
    uint64_t receiveTimestamp() const;
    uint64_t safeTs(uint64_t curT) const;              // = min(receiveTimestamp(), lastSyncTs + syncInterval)
    bool emitSync(uint64_t curTick);                   // 限速发 CONTROL_SYNC
    bool peerStaleMs(uint64_t thresholdMs) const;      // liveness
    void markPeerDone(const char* reason);
};

struct PortParams { std::string name; uint32_t moduleId; uint32_t portId;
                    std::string localRxEndpoint; std::string peerRxEndpoint; };  // 完整 ipc:// URL
struct PortRuntime { uint64_t syncInterval = 100000; uint64_t linkLatency = 100000; };
```

默认值（`Port.hh:20-21`）：`kDefaultSyncInterval = 100000`（100ns），`kDefaultLinkLatency = 100000`。

### 4.2 PortEnvLoader endpoint 命名（`framework/Port.cc:296-336`）

所有 endpoint 在统一目录 `/workspace/gem5/shared_ipc/`，命名规则：

```text
gem5 ↔ ubio (per node n):
  gem5 侧 rx: ipc://.../ipc_ubio_n_to_gem5_n     tx: ipc://.../ipc_gem5_n_to_ubio_n
  ubio  侧 rx: ipc://.../ipc_gem5_n_to_ubio_n     tx: ipc://.../ipc_ubio_n_to_gem5_n

ubio ↔ networksim (per module m = node*K+socket):
  ubio  侧 rx: ipc://.../ipc_networksim_m_to_ubio_m   tx: 对端 nsim
  nsim  侧 rx: ipc://.../ipc_networksim_m_to_ubio_m   tx: 对端 ubio

barrier (per node n): ipc://.../ipc_barrier_n   (bind 侧)
```

> `IPC_BASE = "/workspace/gem5/shared_ipc/ipc"`（Docker 挂载点）。每次运行前必须
> `rm -rf shared_ipc/ipc_*`（`run_multi.sh:96-98`）。

### 4.3 UBCCController host/outbound 接口（`modules/ubiomodule/UBCCController.hh`）

```cpp
class UBCCHostIf {
  public:
    virtual void hostIssueBackstoreRead(uint64_t pa) = 0;
    virtual void hostIssueBackstoreWrite(uint64_t pa) = 0;
    virtual void hostIssueBackstoreDelete(uint64_t pa) = 0;
    virtual ~UBCCHostIf() = default;
};
class UBCCOutboundIf {
  public:
    virtual bool sendRecallReq(const CoherenceMessage&) = 0;
    virtual bool sendInvalidateReq(const CoherenceMessage&) = 0;
    virtual bool sendUpgradeAckNotify(const CoherenceMessage&) = 0;
    virtual ~UBCCOutboundIf() = default;
};
// UBCCController::setHost(UBCCHostIf*); setOutbound(UBCCOutboundIf*);
```

核心入口（`ubio_main.cc:362-388` 的 dispatch）：
`processOuterRequest / processOuterUpgradeReq / processOuterUpgradeDone /
processRecallResponse / processInvalidationAck / processClear / processWriteback /
processEvict / processHomeWritebackNotify` —— 语义与 `scheme_v4.md §4.1` 规范一致
（reserve-then-commit、Clear 唯一 commit point、UPGRADE_PENDING irrevocable-after-ack）。

### 4.4 MemMessage / CoherenceMessage wire（替代 legacy UbWireHeaderV1）

- `MemMessage` 是稳定传输 ABI：固定 40B header + ≤1024B payload，小端字节序由宿主决定（当前同主机同编译器）
- `CoherenceMessage` 作为 `COH_MSG` 的 payload，`Port` 不解析其内部结构
- gem5 `UBAdapter` 与 ubio 只传 `MemMessage` 字节，不共享 C++ ABI（除 `CoherenceMessage.hh` 头文件共用）

### 4.5 时钟同步接口（§2.7 已述）

无独立 `ILogicalClock`；时钟由 `Port::safeTs/emitSync/peerStaleMs` 三方法 + 各进程主循环推进规则共同实现。

---

## 5. 数据结构生命周期（更新自 legacy §5）

> 完整规范见 `docs/design/scheme_v4.md §7`。此处只列迁移相关要点。

### 5.1 OutstandingRequest（`UBCCController.hh`）

- 创建点：`processOuterRequest`（GRANT_HANDSHAKE/INVALIDATE/RECALL）、`processOuterUpgradeReq`（UPGRADE_PENDING）
- 阶段：`CREATED → WAITING_TARGET_RESP / WAITING_ALL_ACKS / WAITING_LOCAL_DONE / WAITING_CLEAR → DONE/CANCELLED/TIMED_OUT/PERSISTENT_BUSY`
- **迁移不变量**：`baseEpoch/reservedEpoch/dataBuf[64]/replayArmed` 语义不变；GRANT_HANDSHAKE `DONE` 后转 tombstone

### 5.2 tombstone / pendingRequester

- tombstone：`processClear` 成功后 `retireToTombstone`，窗口 `W` 内 duplicate Clear 返回相同 ClearAck
- pendingRequester：同 PA 已有 live outstanding 时入队；replay 时 **rebased epoch = 最新 committed epoch**
- **多进程不变量**：tombstone 可仅内存存在；多进程重启丢 tombstone 不影响安全性，只影响 duplicate Clear 体验

### 5.3 per-pair FIFO（ubio 进程内）

- ubio 单线程主循环保证 per-(src,dst) 顺序性
- `src==dst`（同节点同 socket）也必须经本地处理，不走旁路
- networksim 的 `_fifo` 是跨节点延迟注入，不是 UBCC 排序点

---

## 6. 各模块编译具体方式

> 前置：所有 native 编译在 Docker `ubcc-dev:ubuntu20.04` 内进行；gem5 用 scons。
> 宿主挂载：`-v /mnt/data2/cgc/cc-ep:/workspace/gem5 -w /workspace/gem5`。

### 6.1 前置：ZeroMQ（一次性，产物在 `thirdparty/zeromq/`）

```bash
cd thirdparty/zeromq && bash build.sh
# 产物：include/zmq.h, include/zmq.hpp, lib/libzmq.a
```

### 6.2 framework 静态库（`scripts/build_framework.sh`）

```bash
bash scripts/build_framework.sh
```

产物：
```text
build/framework/
  include/framework/{Port.hh, MemMessage.hh}   # 公共头
  lib/libframework.a                            # Port.o (+ ZMQChannel.o)
  obj/{Port.o, ZMQChannel.o}
  manifest.txt
```

编译命令（脚本内展开）：
```bash
g++ -std=c++17 -O2 -Wall -pthread \
    -I$ROOT -I$FW -I$ROOT/thirdparty/zeromq/include \
    -c framework/Port.cc -o build/framework/obj/Port.o
ar rcs build/framework/lib/libframework.a build/framework/obj/*.o
```

### 6.3 ubio（`scripts/build_ubio.sh`）

```bash
bash scripts/build_ubio.sh
# 前置：build/framework/lib/libframework.a 必须存在，否则报错退出
```

产物：`build/bin/ubio`

编译命令（脚本内展开）：
```bash
MOD=modules/ubiomodule
CXXFLAGS="-std=c++17 -O2 -Wall -pthread -I$MOD -I$MOD/mem/ruby -Ibuild/framework/include -I$ROOT -I$ROOT/thirdparty/zeromq/include"
LDFLAGS="-L$ROOT/thirdparty/zeromq/lib -lzmq -lpthread"
SRCS="$MOD/UBCCController.cc $MOD/ResidentDir.cc $MOD/BackstoreSchemaA.cc $MOD/BackstoreSchemaC.cc $MOD/NodeAddressMap.cc"
g++ $CXXFLAGS tools/ubio/ubio_main.cc $SRCS build/framework/lib/libframework.a $LDFLAGS -o build/bin/ubio
```

> 注意：`UBIOModule.cc` 当前未被 `build_ubio.sh` 链接 —— ubio 进程的路由逻辑直接写在 `ubio_main.cc`
> 里（`pollAndProcess` + `routeControlToTarget`）。`UBIOModule.{hh,cc}` 是独立模块测试用。

### 6.4 networksim（`scripts/build_networksim.sh`）

```bash
bash scripts/build_networksim.sh
```

产物：`build/bin/networksim`

```bash
g++ -std=c++17 -O2 -Wall -pthread \
    -Ibuild/framework/include -I$ROOT -I$ROOT/thirdparty/zeromq/include \
    modules/networksim/networksim_main.cc build/framework/lib/libframework.a \
    -L$ROOT/thirdparty/zeromq/lib -lzmq -lpthread -o build/bin/networksim
```

### 6.5 barrier_manager（`scripts/build_barrier.sh`）

```bash
bash scripts/build_barrier.sh
```

产物：`build/bin/barrier_manager`

```bash
g++ -std=c++17 -O2 -Wall -pthread \
    -Ibuild/framework/include -I$ROOT -I$ROOT/thirdparty/zeromq/include \
    tools/barrier/barrier_main.cc build/framework/lib/libframework.a \
    -L$ROOT/thirdparty/zeromq/lib -lzmq -lpthread -o build/bin/barrier_manager
```

### 6.6 gem5（scons，链接 libframework.a）

```bash
docker run --rm -v /mnt/data2/cgc/cc-ep:/workspace/gem5 -w /workspace/gem5 \
  ubcc-dev:ubuntu20.04 bash -c '
cd /workspace/gem5/gem5
# 改过 framework/Port.cc 后强制重建对应对象：
# rm -f build/ARM/gem5.build/mem/ruby/protocol/chi/ep/UBAdapter.o
scons build/ARM/gem5.opt -j32
'
```

`gem5/src/mem/ruby/protocol/chi/ep/SConscript` 关键改动（已落地）：
- 检查 `build/framework/lib/libframework.a` 存在，否则 scons 报错
- `CPPPATH += [repo_root, zmq_include, build/framework/include]`
- `LIBPATH += [zmq_lib, build/framework/lib]`
- `LIBS += ['framework', 'zmq']`
- **不再** `Source(framework/Port.cc)`，改为链接预构建的 `libframework.a`

### 6.7 workload 交叉编译（aarch64）

```bash
cd tests/e2e/workloads
aarch64-linux-gnu-gcc -static -O0 -g -I. -o e2e_tc2_remote_read.elf e2e_tc2_remote_read.c
```

`run_multi.sh:64-72` 会自动编译 `e2e_tc[1-9]*_*.c` / `e2e_tc10_*.c` / `e2e_tc_local_upgrade.c`。

### 6.8 一键构建

```bash
bash scripts/build_all.sh
# 等价于：[libframework.a 缺失则 build_framework.sh] && build_ubio.sh && build_networksim.sh && build_barrier.sh
# 产物：build/bin/{ubio, networksim, barrier_manager}
```

---

## 7. 各模块运行具体方式

### 7.1 启动顺序与 IPC 清理（强制）

```bash
# 每次运行前必须清理 IPC endpoint
mkdir -p shared_ipc && rm -rf shared_ipc/ipc_*
rm -rf /tmp/ubio_n* /tmp/networksim_* /tmp/barrier_* 2>/dev/null || true
```

启动顺序：**barrier → networksim → gem5(per node) → ubio(per (node,socket))**。

### 7.2 推荐方式：`tests/e2e/run_multi.sh`（自动编排）

```bash
# 单 TC（默认 NUM_NODES=3）
NUM_NODES=3 bash tests/e2e/run_multi.sh 2

# 全量回归（脚本内置 TC 列表：1 2 3 4 5 6 7 8 10 11）
bash tests/e2e/run_multi.sh --all

# dual-socket TC（脚本按 TC 自动设 NUM_SOCKETS=2：TC32/33/34/35/39）
bash tests/e2e/run_multi.sh 32
```

`run_multi.sh` 内部做了：
1. 编译 workload
2. 检查 4 个二进制存在（`build/bin/{ubio,networksim,barrier_manager}` + `gem5/build/ARM/gem5.opt`）
3. 生成 nsim topology（全 mesh，latency=100000 ticks）
4. 按顺序启动 barrier → nsim → N×gem5（等 `STEP5.*Port enabled`）→ N×K×ubio
5. 等所有 gem5 退出（timeout 600s），收集退出码
6. kill ubio/nsim/barrier
7. 调 `test_e2e.py --verify-split` 聚合各节点 simout 判定 PASS/FAIL

环境变量：
- `NUM_NODES`（默认 3）
- `UBCC_NUM_SOCKETS` 由 `test_e2e.py` 按 `--num-sockets` 或 TC 默认值设置
- `UBIO_FAULT_RULES` 由 `run_multi.sh:fault_rules_for_tc` 设置（见 §7.5）

### 7.3 手动逐步启动各进程（调试用）

以 N=3、K=1 为例（endpoint 由 `PortEnvLoader` 按 `shared_ipc/` 命名生成）：

```bash
# 0) 清理
mkdir -p shared_ipc && rm -rf shared_ipc/ipc_*

# 1) barrier_manager（bind 侧，3 节点）
build/bin/barrier_manager 3 >logs/barrier.log 2>&1 &
sleep 1

# 2) networksim（bind 侧；topo.json 全 mesh 3 模块，latency=100000）
#    run_multi.sh 会动态生成 topo.json；这里示意
build/bin/networksim topo.json >logs/nsim.log 2>&1 &
sleep 1

# 3) 3 个 gem5（每个 bind 本节点 Port；--node-id 决定本进程负责的节点）
for nid in 0 1 2; do
  gem5/build/ARM/gem5.opt --outdir=m5out/e2e_mp/tc2/node$nid \
    tests/e2e/test_e2e.py --tc=2 --node-id=$nid --num-nodes=3 --num-sockets=1 \
    >logs/gem5_tc2_node$nid/stdout.log 2>logs/gem5_tc2_node$nid/stderr.log &
done
# 等待每个 gem5 打印 "STEP5.*Port enabled"

# 4) 3 个 ubio（connect 侧；gid = node*K + socket）
for nid in 0 1 2; do
  UBCC_NUM_NODES=3 UBCC_NUM_SOCKETS=1 \
  build/bin/ubio --node=$nid --socket=0 \
    >logs/ubio_n$nid/stdout.log 2>logs/ubio_n$nid/stderr.log &
done
```

> gem5 是 bind 侧、ubio 是 connect 侧，故 **gem5 必须先启动并 bind 完成**，ubio 才能连上。

### 7.4 单 TC 运行 / 全量回归

```bash
# 单 TC
bash tests/e2e/run_multi.sh 2          # TC2: remote read

# 指定 TC 列表
bash tests/e2e/run_multi.sh 1 2 3 8

# 全量（脚本内置列表）
bash tests/e2e/run_multi.sh --all
```

结果：`=== Results: <PASS> pass, <FAIL> fail ===`，日志在 `logs/<timestamp>/`。

### 7.5 dual-socket 与故障注入运行

**dual-socket**：`run_multi.sh:sockets_for_tc` 对 TC32/33/34/35/39 强制 `NUM_SOCKETS=2`，
启动 `N×2` 个 ubio（每 (node,socket) 平面一个），gem5 内部构建 2 个 HN-F/EP-SNF/UBAdapter。

**故障注入**（`run_multi.sh:fault_rules_for_tc` + `ubio_main.cc:75-114`）：

```bash
# TC47: duplicate Clear；TC48/49: duplicate InvalidateAck
bash tests/e2e/run_multi.sh 47

# 规则格式：name:type:src:dst:pa:action[:delayTicks[:matchCount]]
# action ∈ {drop, dup, delay}；由 UBIO_FAULT_RULES 环境变量传入 ubio
# ubio 命中规则时打印 [UBFAULT]，verifier 扫描作为故障证据
```

### 7.6 日志位置与 grep 模式

```text
logs/<timestamp>/
├── barrier.log
├── nsim.log
├── gem5_tc<N>_node{i}/
│   ├── stdout.log     # gem5 仿真输出（EPSNF-RECV, CLEAR, simout_n{i}）
│   └── stderr.log     # gem5 诊断（GEM5-SEND, CLK-SYNC, CLEAR-RESP, PORT-SEND/RECV）
├── ubio_n{i}_s{k}/
│   ├── stdout.log
│   └── stderr.log     # UBIO-START, UBIO-IPC, [ubio:i] recv ..., TRACE-2/3/4, UBIO-LOOP, UBFAULT
└── verify_tc<N>.log
```

关键 grep（`expert_entrypoint.md:153-168`）：
```text
GEM5-SEND / stored ReadResp / CLEAR-SEND / CLEAR-RESP
PORT-SEND / PORT-RECV / CLK-SYNC
UBIO-START / UBIO-IPC / UBIO-LOOP / UBIO-RR-PATH / UBIO-CLEAR
NSIM-STAT / NSIM-NOBUF / NSIM-MISS
BARRIER_REACHED / BARRIER_RELEASE
emit_after_wr / READ_VAL / PASS / FAIL
Deadlock
```

---

## 8. 剩余迁移工作与阶段（更新自 legacy §7）

### Phase A — 已完成（独立化 + 多进程 + 时钟同步）

- ✅ UBCC/UBIOModule/ResidentDir/CoherenceMessage 物理外移到 `modules/ubiomodule/`
- ✅ gem5 侧 `SConscript` 不再编译 UBCC/UBIOModule
- ✅ framework::Port (ZMQ) + MemMessage/CoherenceMessage wire
- ✅ 多进程拓扑：barrier + nsim + N×K ubio + N gem5
- ✅ safeTs 涌现式时钟同步 + peerStaleMs liveness
- ✅ dual-socket 拓扑（per-(node,socket) ubio，gid 寻址）
- ✅ ubio 侧故障注入（drop/dup/delay）

### Phase B — 进行中 / 待完成

1. **backstore 消息化与持久化**：当前 `UbioBackstoreHost` 是 ubio 进程内 `std::map`（`ubio_main.cc:306`），
   未做跨进程持久化。若要支持 ubio crash/restart 不丢 committed metadata，需把 backstore 读写改为
   消息化（或接真实持久层）。安全性目前依赖"committed metadata 在 ubio 内存"——crash 后状态全丢。
2. **gem5 侧副本物理清理**（`refactoring_master_plan.md` Task 5 方案 B）：
   `BackstoreSchemaA/C`、`NodeAddressMap`、`CoherenceMessage.hh` 在 gem5 与 ubiomodule 各一份，
   需让 gem5 仅通过 framework 公共头访问，物理删除 gem5 侧副本。
3. **dual-socket NUMA latency 元信息**：`cluster.socket_id` / `hnf.socket_id` / `lat_local` / `lat_numa`
   已在 `dual_socket_design.md §3.2.10` 定义但 topology builder 尚未消费；当前 nsim 用统一 latency。
4. **networksim → ns-3 替换**（`migration_guide.md §8` P3 项）：当前 `networksim_main.cc` 是最小 FIFO
   路由器（191 行），未来替换为真 ns-3 网络模型；`framework::Port` 抽象使该替换不波及 UBCC/gem5。
5. **EP-RNF recovery 路径收敛**（`refactoring_master_plan.md §3.4.1`）：`SnpShared/SnpSharedFwd/SnpOnceFwd`
   的 fatal-grade unreachable 校验、recall 改真实 ReadShared/ReadUnique 闭环的最终验证。

### Phase C — 验证收敛

1. **TC 全量回归**：`run_multi.sh --all`（当前内置 1 2 3 4 5 6 7 8 10 11），扩展到全 TC 集
2. **A/B 等价**：legacy §8.1 第二层（in-proc vs standalone 同 seed 同 trace key 对齐）
3. **故障注入**：drop/dup/delay 全路径（当前 TC47/48/49 已覆盖 dup）
4. **crash/restart 语义**：依赖 Phase B-1 完成后才可验证
5. **性能基准**：legacy §10 的预期窗口仍适用（本地 0–3%、远端共享读 +5–20%、远端 unique +10–30%、offload ≤25%）

---

## 9. 多进程竞态窗口与不变量（更新自 legacy §9）

legacy §9.1–9.7 的竞态分析整体仍适用（RecallResp 晚到/重复、InvalidateAck 乱序、UpgradeDone 早于 acks、
Clear replay/tombstone、backstore 回调与 eviction 交错、时钟漂移、Router FIFO 与链路 delay 叠加）。
**当前实现带来的增量约束**：

1. **时钟不漂移**：所有进程 `tick = min(safeTs)`，被约束时 `yield`，不允许 `++tick` 前进
   （`ubio_main.cc:896-900`、`networksim_main.cc:175-181`）。legacy §9.6 的"全局时钟漂移"风险被该规则消除。
2. **liveness 解锁**：gem5 退出后 ubio 用 `peerStaleMs(5000)` + `markPeerDone` 解除时钟约束
   （`ubio_main.cc:883-895`），避免已完成节点冻结整个仿真。
3. **路由区分 ingress/transit**：ubio 对 `isUbccIngress` 类型按 `homeLinePa` 强制本地处理，
   对非 ingress（transit 控制：RecallReq/InvalidateReq/UpgradeAckNotify/各 *Resp）按 `dstNode` 路由
   （`ubio_main.cc:756-800`）。乱序跨进程到达时，UBCC 仍只凭 `(linePa, epoch, reqId, targetNode)` 校验，
   **不依赖 FIFO 假设**完成安全性（与 legacy §9.2 一致）。
4. **单发送槽**：`Port` 单 `TxHandle`，发送面天然 per-port 串行；跨进程 reorder 由 networksim FIFO 与
   ZMQ 调度共同决定，UBCC 不得假设跨节点 FIFO。
5. **backstore 幂等**：`onBackstoreWriteAck/DeleteAck` 必须允许"条目已不存在"场景（legacy §9.5 仍适用）。

---

## 10. 验证策略（更新自 legacy §8）

### 10.1 分层

| 层 | 内容 | 工具 |
|---|---|---|
| wire/runtime 单测 | Port sync smoke、CoherenceMessage round-trip、ResidentDir lookup/victim/BF | `framework/tests/`、`modules/ubiomodule/main_test.cc`、`modules/networksim/main_test.cc` |
| A/B 等价 | in-proc vs 多进程同 seed/trace key 对齐（homeLinePa/epoch/reqId/grantType/pendingInvMask） | `run_multi.sh` + `test_e2e.py --verify-split` |
| 系统级回归 | TC1–TC46+（local/remote、single-writer、upgrade、barrier、directory replay、resident 压力、epoch wrap、backstore 一致性） | `run_multi.sh` |
| 故障注入 | drop/dup/delay（TC47/48/49 已覆盖 dup） | `UBIO_FAULT_RULES` |

### 10.2 必做验收点（legacy §8.2 仍适用）

TC3（ping-pong coherence）、TC8（upgrade/invalidate/clear 顺序）、TC16（dual upgrade race 串行化）、
TC22/23（ResidentDir offload）、TC27（epoch wrap + half-range）、TC28（resident/backstore 镜像一致性）。

### 10.3 判定准则（`run_multi.sh:239-279`）

- 任一 gem5 进程非零退出 → TC 硬 FAIL（即使部分 simout 看似满足内容检查）
- timeout（600s）→ FAIL 但不阻塞
- verifier 聚合各节点 `simout_n{i}` + ubio `[UBFAULT]` 证据综合判定

---

## 11. 目录结构总览（实际落地版）

```text
cc-ep/
├── framework/                      # 共享传输层（libframework.a 源）
│   ├── Port.{hh,cc}                # ZMQ PAIR 端口 + safeTs + emitSync + peerStaleMs
│   ├── MemMessage.hh               # 传输层消息（40B header + 1024B payload）
│   ├── ZMQChannel.{hh,cc} / ZMQTransport.{hh,cc}
│   ├── PseudoMemPort.* / PseudoManager.* / PseudoMemPacket.hh  # 早期本地队列抽象
│   ├── Makefile / tests/
│   └── libframework.a              # 预构建产物
├── modules/
│   ├── ubiomodule/                 # UBCC 协议权威层（外移自 gem5/.../ep/）
│   │   ├── UBCCController.{hh,cc}  # 目录 + outstanding + tombstone + host/outbound 接口
│   │   ├── UBIOModule.{hh,cc}      # runtime 路由
│   │   ├── ResidentDir.{hh,cc}     # resident metadata SRAM + BF
│   │   ├── CoherenceMessage.hh / CoherenceMessageQueue.hh   # 原 UBMsg/UBMsgQueue
│   │   ├── BackstoreTypes.hh / BackstoreOrganization.hh / BackstoreSchema{A,C}.{hh,cc}
│   │   ├── NodeAddressMap.{hh,cc}
│   │   ├── gem5_shim.hh            # gem5 依赖 shim
│   │   └── main_test.cc
│   └── networksim/                 # 跨节点链路 FIFO 路由器
│       ├── networksim_main.cc / NetworkSim.{hh,cc} / ForwardTable.{hh,cc}
│       └── main_test.cc
├── tools/
│   ├── ubio/ubio_main.cc           # standalone UBCC 进程入口（→ build/bin/ubio）
│   ├── barrier/barrier_main.cc     # barrier 协调（→ build/bin/barrier_manager）
│   ├── launcher.py
│   └── networksim/
├── gem5/
│   ├── src/mem/ruby/protocol/chi/ep/   # 保留在 gem5 的 EP 层
│   │   ├── EPBackend.{cc,hh,py}
│   │   ├── EPRNFController.{cc,hh,py}
│   │   ├── EPSNFController.{cc,hh,py}
│   │   ├── MetaRNFController.{cc,hh,py}
│   │   ├── UBAdapter.{cc,hh,py}        # gem5 ↔ Port 边界
│   │   ├── NodeAddressMap.{cc,hh}      # 副本（待清理）
│   │   ├── BackstoreSchema{A,C}.{cc,hh}# 副本（待清理）
│   │   ├── CoherenceMessage.hh         # 副本（待清理）
│   │   ├── SConscript                  # 链接 libframework.a，不编译 UBCC/UBIOModule
│   │   └── M{4..8}SelfTest.cc
│   └── build/ARM/gem5.opt
├── scripts/
│   ├── build_framework.sh           # → build/framework/lib/libframework.a
│   ├── build_ubio.sh                # → build/bin/ubio
│   ├── build_networksim.sh          # → build/bin/networksim
│   ├── build_barrier.sh             # → build/bin/barrier_manager
│   └── build_all.sh                 # 聚合
├── tests/e2e/
│   ├── run_multi.sh                 # 多进程编排（barrier→nsim→gem5×N→ubio×N×K）
│   ├── test_e2e.py                  # gem5 配置入口 + --verify-split 判定
│   └── workloads/                   # aarch64 交叉编译 workload
├── shared_ipc/                      # ZMQ ipc:// endpoint 目录（运行时清理）
├── thirdparty/zeromq/               # ZeroMQ 构建
├── config/topology.json             # nsim 拓扑示例
├── docker/                          # ubcc-dev 镜像
└── docs/{design,legacy,recovery}/   # 设计/历史/恢复文档
```

---

## 12. 一句话建议

**legacy 版"迁 UBCC 协议权威层 + resident metadata 层 + message runtime 层"的方向已按 ZMQ/safeTs 路线落地；
后续只需做 backstore 消息化、gem5 侧副本物理清理、dual-socket NUMA latency、ns-3 替换与 TC 全量回归收敛。**

要改的关键接口只有 4 组（已落地，对应 legacy §14）：

1. **framework::Port**（ZMQ 传输 + safeTs 时钟 + peerStaleMs liveness）—— 替代 legacy 的 transport + ILogicalClock
2. **UBCCHostIf / UBCCOutboundIf**（ubio 进程内实现）—— 替代 legacy 的 IUbccHost gem5 回调
3. **MemMessage / CoherenceMessage wire**（Port payload）—— 替代 legacy 的 UbWireHeaderV1
4. **两级时延**（ubio 进程内路由 + networksim 链路 FIFO）—— 保留 legacy Q4=C 的精神

按此边界，迁移风险最小，且与已落地的 N×K ubio + N gem5 + nsim + barrier 多进程部署目标完全一致。
