# CC-EP 独立化迁移总方案（migration_plan）

> 文档整合说明（2026-06-19）：`c2c_migration_assessment.md` 的“未来从 home-centric 向 direct owner→requester C2C 演进评估”已并入本文的迁移路线与风险章节，避免迁移文档分裂。

> 目标回答：**哪些模块分别在哪些文件中，我如果要移植要怎么移植，关键接口有哪些。**
>
> 本方案基于当前仓库代码、`docs/recovery/*` 恢复文档、以及用户已固定选择：
> - **Q1 = B**：采用 **protocol-aware host interface**：`issueBackstoreRead/Write/Delete`、`notifyGrantVisible`
> - **Q2 = B**：采用 **固定 header + version/payloadLen + type-specific body** 的 wire format
> - **Q3 = A**：采用 **Ns3UB / 外部调度器提供的全局逻辑时钟**
> - **Q4 = C**：采用 **两级时延模型**：Router 保留本地 per-pair FIFO+latency；Ns3UB 注入跨进程链路时延

---

## 1. 结论先行

### 1.1 C2C 演进定位（并入自 c2c_migration_assessment）

当前实现与迁移主线保持 **home-centric recall**（home 控序、home 供数真值入口），
`direct owner→requester C2C` 仅作为后续演进分支，不进入本轮“独立化落地”的必选范围。

原因：

1. 现阶段优先收敛排序点与恢复语义，避免同时引入“跨 owner 直达数据面”变量；
2. C2C 会显著抬高故障注入与重放恢复复杂度（重排、重传、双向确认）；
3. 在 Ns3UB/外部调度器阶段，先固化 home-centric wire/API 更利于跨进程调试与可观测性。

因此迁移策略为：**先完成 standalone UBCC 的 home-centric 可验证闭环，再评估 C2C 增量收益与风险。**

这次迁移不应该把整个 `ep/` 目录直接“搬出去”，而应该按 **边界清晰化 → 协议固化 → 进程解耦 → 时钟/链路外置 → 全量回归** 五个阶段推进。

**要独立出去的核心只有 5 个模块：**

1. `UBCCController.{hh,cc}`：home/global metadata 权威、Outstanding/Tombstone/Queue 核心
2. `ResidentDir.{hh,cc}`：resident metadata SRAM + BF + victim/offload
3. `UBRouter.{hh,cc}`：消息路由、per-pair FIFO、local/remote 分发
4. `UBMsgQueue.hh`：per-pair FIFO 抽象
5. `UBMsg.hh`：UB wire message 语义定义

**必须留在 gem5 的模块：**

1. `UBAdapter.{hh,cc}`：gem5 内 EPBackend 的消息门面
2. `EPBackend.{hh,cc}`：CHI ↔ outer protocol 主控，仍与 EPRNF/EPSNF 强耦合
3. `EPSNFController.{hh,cc}`：SNF 数据路径
4. `EPRNFController.{hh,cc}`：RNF snoop/recall/upgrade 协议路径
5. `MetaRNFController.{hh,cc}`：metadata backstore stub / bridge

**最终形态不是“UBCC 在 gem5 内部编成动态库”，而是：**

- gem5 进程保留 CHI、EP、Adapter
- standalone 进程承载 UBCC/Router/ResidentDir
- 二者通过 **UBMsg 二进制协议** + **Ns3UB channel** 通信
- 所有 `Tick/readyTick/deadlineTick` 改由 **外部全局逻辑时钟** 驱动

---

## 2. 迁移前后架构图

## 2.1 迁移前（当前单进程）

```text
CPU/L2/HN-F
   │
EPSNF / EPRNF
   │
EPBackend
   │
UBAdapter
   │
UBRouter
   │
UBCCController
   │
ResidentDir + backstore stub
```

关键特征：

- `UBAdapter`、`UBRouter`、`UBCCController` 都在同一 gem5 进程
- `sendMessage()` 目前仍是“同步入队 + 立即 drain”风格（`UBRouter.cc:77-90`）
- `curTick()` 直接作为队列与协议时序基础
- backstore 通过 `EPBackend::issueBackstoreRead/Write/Delete()` 回调（`EPBackend.hh:609-611`）

## 2.2 迁移后（目标多进程）

```text
┌──────────────────────── gem5 process (per node) ────────────────────────┐
│ CPU/L2/HN-F                                                             │
│    │                                                                    │
│ EPSNF / EPRNF                                                           │
│    │                                                                    │
│ EPBackend                                                               │
│    │                                                                    │
│ UBAdapter  ───── UBMsg serializer / IPC client ─────► Ns3UB endpoint    │
└──────────────────────────────────────────────────────────────────────────┘
                                          │
                              global logical clock / link delay
                                          │
┌────────────────────── standalone UBCC process (per node) ───────────────┐
│ Ns3UB endpoint                                                           │
│    │                                                                    │
│ UBRouter(runtime)                                                        │
│    │                                                                    │
│ UBCCController                                                           │
│    │                                                                    │
│ ResidentDir + metadata backstore client                                  │
└──────────────────────────────────────────────────────────────────────────┘
```

## 2.3 两级时延模型（Q4=C）

```text
gem5/UBAdapter send
   ↓
standalone UBRouter: per-(src,dst) FIFO + local queue latency
   ↓
Ns3UB: inter-process / inter-node link delay injection
   ↓
destination UBRouter delivery
```

含义：

- **本地顺序性** 仍由 Router 的 `_pairQueues` 保证
- **跨进程传播时间** 由 Ns3UB 注入
- 这样可以最大化复用当前 `UBRouter + UBMsgQueue` 语义，同时不把网络模型硬编码进 UBCC 核心

---

## 3. 模块-文件清单与依赖关系

## 3.1 要迁移为 standalone 的文件

| 文件 | 当前职责 | 直接依赖 | 迁移策略 |
|---|---|---|---|
| `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh` | 定义 `OutstandingRequest`/`GrantHandshakeTombstone`/`PendingRequester`、对外 API | `EPBackend.hh`, `ResidentDir.hh`, `DataBlock.hh` | **拆 gem5 依赖**，保留协议核心；把 `EPBackend` 指针改为 host callback 接口 |
| `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc` | home grant、recall、invalidate、upgrade、clear、backstore replay | `curTick()`, `UBRouter`, `EPBackend`, `NodeAddressMap` | **第一优先级迁移**；把时间、发送、backstore 都抽象化 |
| `gem5/src/mem/ruby/protocol/chi/ep/ResidentDir.hh` | resident 元数据结构、BF/control byte API | 无 gem5 强耦合 | 几乎可原样迁移 |
| `gem5/src/mem/ruby/protocol/chi/ep/ResidentDir.cc` | packed56 编码、Robin Hood、BF/victim 实现 | `base/logging.hh` | 换成独立 logging/assert 宏 |
| `gem5/src/mem/ruby/protocol/chi/ep/UBRouter.hh` | 路由器抽象、`_pairQueues`、router registry | `SimObject`, `EventFunctionWrapper`, `UBAdapter`, `UBCCController` | 改成 runtime service，不再继承 `SimObject` |
| `gem5/src/mem/ruby/protocol/chi/ep/UBRouter.cc` | send/drain/local deliver/remote forward | `curTick()`, router static registry | 改成 event loop + transport plugin |
| `gem5/src/mem/ruby/protocol/chi/ep/UBMsgQueue.hh` | per-pair FIFO | `Tick`, `UBMsg` | 改为 standalone `uint64_t logical_tick` |
| `gem5/src/mem/ruby/protocol/chi/ep/UBMsg.hh` | UB 消息语义结构 | `Tick`, `DataBlock` 风格数据 | 保留语义，新增序列化层 header(version/payloadLen) |

## 3.2 保留在 gem5 的文件

| 文件 | 保留原因 | 迁移后职责 |
|---|---|---|
| `UBAdapter.{hh,cc}` | 它是 EPBackend 唯一合法外边界 | 从“进程内门面”变为“IPC/Ns3UB client stub” |
| `EPBackend.{hh,cc}` | 深度依赖 CHI controller、RubySystem、EPRNF/EPSNF | 继续负责 remote miss / grant / recall / invalidate / writeback / clear 的上半部 |
| `EPRNFController.{hh,cc}` | CHI snoop 和 CompAck 路径仍在 gem5 协议机里 | 不迁移，只改与 EPBackend 的调用边 |
| `EPSNFController.{hh,cc}` | HN-F/EP-SNF 数据路径与 CompData 组织都在 gem5 | 不迁移 |
| `MetaRNFController.{hh,cc}` | 仍是 gem5 内部 metadata backstore stub | 通过 host callback 服务 standalone UBCC |

## 3.3 关键耦合点

### A. UBCC ↔ EPBackend

当前：

- `UBCCController` 直接持有 `_backend`（`UBCCController.hh:549-550`）
- 通过 `issueBackstoreRead/Write/Delete()` 反向调用 gem5（`UBCCController.cc:204,278,288`）

迁移后：

- 改为 `IUbccHost` 抽象接口
- standalone UBCC 不再包含 `EPBackend.hh`

### B. UBCC ↔ UBRouter

当前：

- `UBCCController` 直接持有 `_router` 发送 `UpgradeAckNotify`（`UBCCController.cc:1343-1357`）

迁移后：

- 改为 `IRouterEgress` / `ITransport` 注入

### C. Router ↔ 时间系统

当前：

- `UBRouter` 使用 `curTick()` 与 `EventFunctionWrapper`（`UBRouter.hh:84-87`, `UBRouter.cc:37,97,190`）

迁移后：

- 改为外部 `ILogicalClock`
- 所有 `enqueueTick/readyTick/deadlineTick` 使用全局逻辑时钟（Q3=A）

---

## 4. 关键接口规范

## 4.1 UBMsg wire format 规范

## 4.1.1 语义来源（当前代码）

- `UBMsgType`：`UBMsg.hh:17-35`
- `UBMsgFlags`：`UBMsg.hh:38-46`
- `UBMsgHeader`：`UBMsg.hh:49-73`
- `UBReadRespBody`：`UBMsg.hh:82-103`
- `UBUpgradeRespBody`：`UBMsg.hh:137-141`

## 4.1.2 迁移后的二进制 envelope（Q2=B）

当前 `UBMsg` 是进程内 C++ struct；迁移后必须显式定义 **稳定 wire ABI**：

```c
struct UbWireHeaderV1 {
    uint32_t magic;          // 'UBM1'
    uint16_t version;        // =1
    uint16_t headerBytes;    // sizeof(UbWireHeaderV1)
    uint16_t type;           // UBMsgType
    uint16_t reserved0;
    uint32_t flags;          // UBMsgFlags
    uint32_t payloadLen;     // body bytes

    uint16_t srcNode;
    uint16_t dstNode;
    uint16_t homeNode;
    uint16_t requesterNode;
    uint16_t targetNode;
    uint16_t reserved1;

    uint64_t homeLinePa;
    uint64_t localLinePa;
    uint64_t epoch;
    uint64_t reqId;
    uint64_t seqNum;
    uint64_t enqueueTick;
    uint64_t readyTick;
};
```

body 采用 **type-specific body**：

- `ReadReq` → `neededPerm`
- `ReadResp` → `grantType/dataSource/pendingInvCount/grantVisibleTick/sentinelVisibleTick/recallNeeded/.../grantData[64]`
- `RecallResp` → `data[64]`
- `UpgradeResp` → `upgradeTargetMask/committedEpoch`
- 其余按当前 `UBMsg.hh` 语义映射

## 4.1.3 不变约束

迁移前后必须保持：

1. `homeLinePa` 是唯一 canonical key
2. 主事务键仍是 `(homeLinePa, requesterNode, reqId)`
3. `epoch` 仍按 half-range 规则比较
4. `seqNum` 仍是每个发送端单调递增
5. `UB_FLAG_WRITE_INTENT / KEEP_AS_CLEAN / ACCEPTED / DATA_RETURNED / HAS_DATA / IS_READ_RECALL / BUSY` 语义不变

## 4.1.4 实施建议

- 保留当前 `UBMsg.hh` 作为 **内部语义 struct**
- 新增 `ubmsg_wire.h/.cc` 做 serialize/deserialize
- gem5 `UBAdapter` 与 standalone `Ns3UB endpoint` 只传 wire bytes，不共享 C++ ABI

这能避免未来跨编译器/跨进程/跨机器的 ABI 漂移。

---

## 4.2 UBCC ←→ gem5 host callback 接口（Q1=B）

## 4.2.1 当前最小可见接口

当前已存在：

- `issueBackstoreRead(uint64_t homePa)`
- `issueBackstoreWrite(uint64_t homePa)`
- `issueBackstoreDelete(uint64_t homePa)`

对应位置：

- `EPBackend.hh:609-611`
- `EPBackend.cc:943-1003`
- `UBCCController.cc:204,278,288`

## 4.2.2 迁移后 host API

建议定义：

```c++
struct GrantVisibleEvent {
    uint64_t homeLinePa;
    uint64_t baseEpoch;
    uint64_t reservedEpoch;
    uint64_t reqId;
    int requesterNode;
    int grantType;
    uint64_t grantVisibleTick;
    uint64_t sentinelVisibleTick;
};

class IUbccHost {
  public:
    virtual void issueBackstoreRead(uint64_t homePa) = 0;
    virtual void issueBackstoreWrite(uint64_t homePa) = 0;
    virtual void issueBackstoreDelete(uint64_t homePa) = 0;
    virtual void notifyGrantVisible(const GrantVisibleEvent&) = 0;
    virtual ~IUbccHost() = default;
};
```

### 为什么要补 `notifyGrantVisible`

当前 grant visible 主要通过 `ReadResp.grantVisibleTick/sentinelVisibleTick` 回传（`UBRouter.cc:216-278`, `UBAdapter.cc:129-146`）。

迁移到多进程后，单靠响应消息不利于：

- metrics / tracing 对齐
- Clear deadline 观测
- gem5 与 standalone 的 grant 可见性审计

所以建议将其提升为 **协议感知 sideband callback**，但不改变协议语义。

---

## 4.3 UBAdapter ←→ standalone transport 接口

建议将当前 `UBAdapter` 从“函数调用门面”改成“传输端点”：

```c++
class IUbTransportClient {
  public:
    virtual bool send(const uint8_t *buf, size_t len) = 0;
    virtual void registerRecvHandler(std::function<void(const uint8_t*, size_t)>) = 0;
    virtual ~IUbTransportClient() = default;
};
```

`UBAdapter` 内部职责：

1. `UBMsg` ←→ wire bytes
2. `reqId`/`seqNum` 分配
3. 同步 API 保持不变（对 EPBackend 来说）
4. 底层从“直接 `_router->sendMessage(req)`”替换为 `transport->send(bytes)`

这保证 **EPBackend/EPRNF/EPSNF 几乎不需要感知 UBCC 已出进程**。

---

## 4.4 UBRouter ↔ UBCC 本地接口

当前 `UBRouter::deliverToUbcc()` 直接调用多个细分方法：

- `processOuterRequest()`
- `processWriteback()`
- `processEvict()`
- `processOuterUpgradeReq()`
- `processOuterUpgradeDone()`
- `processClear()`
- `processRecallResponse()`
- `processInvalidationAck()`

建议 standalone 内统一成单入口：

```c++
class IUbccRequestProcessor {
  public:
    virtual UBMsg processRequest(const UBMsg &req) = 0;  // 对 fire-and-forget 返回空响应
};
```

然后 `UBRouter` 只做：

- decode
- 路由
- 投递
- 如需响应则反向发 `UBMsg`

好处是 Router 不再知道 UBCC 具体 API 细节，便于未来替换实现或多线程化。

---

## 4.5 全局逻辑时钟接口（Q3=A）

当前所有关键时序都直接绑在 gem5 `curTick()` 上。迁移后应改成：

```c++
class ILogicalClock {
  public:
    virtual uint64_t now() const = 0;
    virtual void schedule(uint64_t tick, std::function<void()> fn) = 0;
    virtual ~ILogicalClock() = default;
};
```

用途覆盖：

- `UBMsg.enqueueTick/readyTick`
- `OutstandingRequest.createTick/respTick/deadlineTick`
- tombstone `expireTick`
- Router drain event

**原则：** standalone UBCC 永远不直接读 gem5 tick。

---

## 5. 数据结构生命周期

## 5.1 OutstandingRequest

### 定义位置

- `UBCCController.hh:80-161`

### 创建点

1. `processOuterRequest()` 创建
   - `GRANT_HANDSHAKE`
   - `INVALIDATE`
   - `RECALL`
2. `processOuterUpgradeReq()` 创建
   - `UPGRADE_PENDING`

### 关键阶段

- `CREATED`
- `WAITING_TARGET_RESP`
- `WAITING_ALL_ACKS`
- `WAITING_LOCAL_DONE`
- `WAITING_CLEAR`
- `DONE/CANCELLED/TIMED_OUT/PERSISTENT_BUSY`

### 生命周期规则

| opType | 创建 | 中间状态 | 提交/删除 |
|---|---|---|---|
| `RECALL` | `processOuterRequest()` | 等 `RecallResp` | `DONE` 后转 `GRANT_HANDSHAKE` 或被 replay 消费 |
| `INVALIDATE` | `processOuterRequest()` | 等所有 `InvalidateAck` | 完成后原位转 `GRANT_HANDSHAKE` |
| `GRANT_HANDSHAKE` | 直接 grant 或 recall/invalidate 完成后 | 等 `Clear` | `processClear()` 成功后 retire to tombstone |
| `UPGRADE_PENDING` | `processOuterUpgradeReq()` | 等 acks / 等 `UpgradeDone` | `processOuterUpgradeDone()` 提交后删除 |

### 迁移时必须保持的字段语义

- `baseEpoch`：requester 观测到的 committed epoch
- `reservedEpoch`：真正提交时写入目录的新 epoch
- `dataBuf[64]`：只做请求期临时缓存，绝不持久化
- `replayArmed`：允许 replay requester 命中挂起 grant

## 5.2 tombstone

### 定义位置

- `GrantHandshakeTombstone`：`UBCCController.hh:163-179`
- 容器：`_tombstones`：`UBCCController.hh:565-570`

### 创建/清理

- `processClear()` 成功后 `retireToTombstone()`（`UBCCController.cc:2073-2075,2190-2208`）
- `wakeup()` 和 `checkTombstone()` 时 `cleanupTombstones()`（`UBCCController.cc:108-109,2216,2236-2256`）

### 迁移原则

1. tombstone 可以软持久化，也可以仅内存存在
2. 但 `(linePa, epoch, reqId) -> accepted` 的幂等语义必须保留
3. 多进程重启后即便丢 tombstone，也不能影响安全性，只影响 duplicate Clear 体验

## 5.3 pendingRequester

### 定义位置

- `PendingRequester`：`UBCCController.hh:184-197`
- `_pendingRequesters`：`UBCCController.hh:572-576`

### 作用

同一 PA 已存在 live outstanding 时，后到请求入队，等待前一个 Clear/UpgradeDone 后 replay。

### 迁移原则

- 队列必须与 `homeLinePa` 绑定
- replay 时必须 **rebased epoch = 最新 committed epoch**（`UBCCController.cc:2477-2501`）
- 不能简单保留原请求 epoch 原样重放

## 5.4 `_pairQueues`

### 定义位置

- `UBRouter.hh:80-81`

### 生命周期

1. 第一次 `sendMessage(src,dst)` 时由 `getOrCreateQueue()` 创建（`UBRouter.cc:61-73`）
2. Router 析构时统一释放（`UBRouter.cc:44-51`）

### 迁移原则

- standalone 中改为 `unordered_map<pair<Node,Node>, Queue>` 或 slab/arena 管理
- 队列必须是 **持久 runtime 对象**，不是每条消息临时创建
- `src==dst` 也必须入队，保持“本地 home 不走旁路”的不变量

---

## 6. 文件级迁移分析（按优先级）

## 6.1 `UBMsg.hh`

### 当前基线

- 语义定义完整，约 235 行
- 无显式 wire ABI、无 version/payloadLen

### 迁移动作

1. 原文件保留为 `ubmsg_semantic.h`
2. 新增 `ubmsg_wire.h/.cc`
3. 增加：`magic/version/headerBytes/payloadLen`
4. 固定大小字段、固定字节序（建议 little-endian）
5. 增加 roundtrip 单元测试

### 依赖

- `UBAdapter`
- `UBRouter`
- standalone endpoint

### 风险

- 不能直接跨进程 memcpy 当前 C++ union

## 6.2 `UBMsgQueue.hh`

### 当前基线

- header-only，约 124 行
- 依赖 `Tick`
- 当前 `enqueue(..., 0)` 实际仍是同步风格

### 迁移动作

1. 把 `Tick` 改为 `uint64_t logical_tick`
2. 拆出 `.cc` 不是必须，但推荐
3. 增加最大深度 / watermark / dump API
4. 增加“只 drain ready，不递归 re-enter”的 event-loop 约束

### 风险

- 当前 `dstRouter->sendMessage(msg)` 递归投递方式在多进程中必须删除

## 6.3 `UBRouter.{hh,cc}`

### 当前基线

- 路由、队列、local deliver、remote forward 全在一起
- 继承 `SimObject`
- 使用 static `_routers`

### 迁移动作

1. 去掉 `SimObject`
2. 去掉 static `_routers` 全局注册表
3. 抽出：
   - `IRouterLocalDelivery`
   - `ITransport`
   - `ILogicalClock`
4. 本地 delivery 仍保留 `deliverToUbcc()/deliverToAdapter()` 语义
5. 远端 forward 改成 `transport.send(dstNode, bytes)`
6. `drainReadyQueues()` 改为单线程事件循环，不允许递归 `sendMessage`

### 必改原因

这是当前最强的“单进程假设”载体。

## 6.4 `ResidentDir.{hh,cc}`

### 当前基线

- 已基本独立
- `SramBytes=512KB`、`EntryBytes=7`、`DefaultBloomBytes=64KB`
- packed56 + BF + ctrl + victim 都已在位

### 迁移动作

1. 保留算法与编码不变
2. 替换 gem5 `panic_if/warn` 为 standalone 断言/log 宏
3. 增加 snapshot/dump API，便于 Ns3UB 外部 debug

### 这是最容易迁出的部分。

## 6.5 `UBCCController.{hh,cc}`

### 当前基线

- 协议核心最完整，但 gem5 耦合最多
- 关键依赖：
  - `EPBackend.hh`（`GrantDataSource`）
  - `DataBlock`
  - `UBRouter`
  - `curTick()`

### 迁移动作

1. 新建 standalone 头：`ubcc_controller.h`
2. 将 `GrantDataSource`、`BackstoreEntry`、`OutstandingRequest` 等提升到 standalone 公共类型
3. 把 `_backend` 改为 `IUbccHost*`
4. 把 `_router` 改为 `IRouterEgress*`
5. 把 `curTick()` 改为 `clock->now()`
6. `DataBlock` 改为独立 `std::array<uint8_t,64>` 或自定义 `LineData64`
7. 保留所有状态机逻辑，不重写协议语义

### 迁移边界

**UBCCController 是“迁语义，不迁表现层”。**

不能改的东西：

- `processOuterRequest()` reserve-then-commit 语义
- `processClear()` 提交与 tombstone 语义
- `processRecallResponse()` / `processInvalidationAck()` 的 stale 检查
- `processOuterUpgradeReq/Done()` 的四消息升级语义

## 6.6 `UBAdapter.{hh,cc}`

### 当前基线

- 当前仍直接 `_router->sendMessage(req)`
- 同步接口已封好，最适合作为兼容层

### 迁移动作

1. 不迁出 gem5
2. 改底层 transport
3. 保持上层 API 不变：
   - `sendReadReq`
   - `sendWritebackReq`
   - `sendEvictReq`
   - `sendUpgradeReq`
   - `sendUpgradeDoneReq`
   - `sendClearReq`
   - `sendRecallResp`
   - `sendInvalidateAck`
4. `recvFromRouter()` 改为 `recvFromTransport()` 也可，但建议先保留命名避免扩散修改

---

## 7. 迁移实施步骤（Phase 1-5）

## Phase 1：协议冻结与 standalone 骨架搭建

### 目标

先冻结协议边界，而不是先搬代码。

### 具体工作

1. 在新目录建立 standalone 工程，例如：
   - `standalone/ubcc/include/ubcc/...`
   - `standalone/ubcc/src/...`
2. 复制并重命名：
   - `UBMsg.hh`
   - `UBMsgQueue.hh`
   - `ResidentDir.{hh,cc}`
3. 新增：
   - `ubmsg_wire.h/.cc`
   - `logical_clock.h`
   - `router_transport.h`
   - `ubcc_host_if.h`
4. 为 `UBMsg` 写 3 类测试：
   - encode/decode roundtrip
   - flags/union payload 对齐
   - 跨版本拒绝策略

### 本阶段不做

- 不改 CHI
- 不改 EPBackend 语义
- 不让 gem5 真的连 standalone

### 退出条件

- standalone 可独立编译
- wire format roundtrip 测试通过
- ResidentDir 单测通过

## Phase 2：把 UBRouter 从 SimObject 改成 runtime service

### 目标

消灭最核心的单进程假设。

### 具体工作

1. 把 `UBRouter` 改造成 standalone runtime 类
2. 去掉：
   - `SimObject`
   - `EventFunctionWrapper`
   - static `_routers`
3. 引入：
   - `ILogicalClock`
   - `ITransport`
   - `IRouterLocalDelivery`
4. 保留 `_pairQueues` 语义
5. 实现两级时延：
   - local queue latency 由 Router 处理
   - inter-process delay 由 Ns3UB 插件处理

### 关键注意

当前 `UBRouter.cc:173-175` 的 `dstRouter->sendMessage(msg)` 递归模式必须改掉，否则多进程时会变成重入和死循环风险源。

### 退出条件

- standalone Router 可在无 gem5 环境下跑消息回环测试
- `(src,dst)` FIFO、same-tick drain、跨 endpoint forward 都可验证

## Phase 3：抽离 UBCCController 核心并切断 gem5 依赖

### 目标

把真正的 home 权威逻辑移出 gem5。

### 具体工作

1. 将 `UBCCController` 复制到 standalone
2. 处理依赖替换：
   - `EPBackend.hh` → standalone `GrantDataSource` / `IUbccHost`
   - `DataBlock` → `LineData64`
   - `curTick()` → `ILogicalClock`
   - `UBRouter*` → `IRouterEgress*`
3. 保留以下核心路径不改语义：
   - `processOuterRequest`
   - `processRecallResponse`
   - `processInvalidationAck`
   - `processOuterUpgradeReq`
   - `processOuterUpgradeDone`
   - `processClear`
4. 增加 host callback：
   - `issueBackstoreRead/Write/Delete`
   - `notifyGrantVisible`
5. 增加 standalone 单测：
   - recall→grant→clear
   - invalidate→grant→clear
   - upgrade_pending→done
   - tombstone duplicate clear replay

### 退出条件

- UBCC-only directed tests 全通过
- 与当前 gem5 版同输入同输出对比一致

## Phase 4：gem5 侧切换到 IPC/Ns3UB

### 目标

让 gem5 不再直接持有 in-proc UBCC/Router。

### 具体工作

1. `UBAdapter` 改成 transport client
2. `EPBackend` 保持现有调用方式不变
3. 建立每节点一个 standalone UBCC 进程
4. 引入全局逻辑时钟：
   - gem5 发消息时附带逻辑 tick
   - standalone 以 Ns3UB scheduler tick 为准
5. 本地回调桥：
   - backstore 请求由 gem5 host 实现
   - response/notify 仍回到 UBAdapter

### 兼容策略

建议保留一个编译开关：

- `UBCC_INPROC=1`：旧路径，用于 A/B 对照
- `UBCC_INPROC=0`：新路径，走 Ns3UB

### 退出条件

- TC1-TC16 在 `inproc` 与 `standalone` 模式结果一致
- trace 上 `homeLinePa/epoch/reqId` 完全对齐

## Phase 5：全量验证、恢复与性能收敛

### 目标

完成 TC1-28、故障注入、A/B 性能对比。

### 具体工作

1. 跑完 TC1-28
2. 加 fault injection：
   - duplicate
   - reorder
   - delayed ack
   - dropped message
3. 校验 crash/restart 语义：
   - outstanding 不持久化
   - tombstone 可软持久化
   - committed metadata 持久化
4. 做 benchmark：
   - local DSM
   - remote shared read
   - remote unique/upgrade
   - stress multi-node pressure

### 退出条件

- TC1-28 全过
- 关键 race 全有证据覆盖
- 性能在可接受窗口内

---

## 8. 验证策略：TC1-28 全量覆盖

## 8.1 建议验证分层

### 第一层：wire / runtime 单元测试

- `UBMsg` encode/decode
- `UBRouter` FIFO/order
- `ResidentDir` lookup/victim/BF
- `UBCCController` directed recall/invalidate/clear/upgrade

### 第二层：A/B 等价测试

- `in-proc UBRouter+UBCC` vs `standalone UBRouter+UBCC`
- 同 workload、同 seed、同 trace key：
  - `homeLinePa`
  - `epoch`
  - `reqId`
  - `grantType`
  - `pendingInvMask`

### 第三层：系统级回归 TC1-28

| TC 范围 | 覆盖重点 |
|---|---|
| TC1-TC4 | 基础 local/remote read、ring、拓扑正确性 |
| TC5-TC8 | single-writer、多 sharer、writeback/evict、upgrade-invalidate |
| TC9-TC13 | 非 DSM 拒绝、atomic、barrier、release/acquire |
| TC14-TC17 | 多波次 sharer、credit storm、dual upgrade、writeback DMA |
| TC18-TC21 | directory fill replay、dirty persist、offload smoke A/B |
| TC22-TC24 | resident capacity、BF false positive、多节点压力 |
| TC25-TC28 | invalidate-clear cycle、L3 eviction chain、epoch wrap、backstore metadata consistency |

## 8.2 必做的迁移验收点

1. **TC3**：验证 ping-pong coherence 没被多进程化破坏
2. **TC8**：验证 upgrade/invalidate/clear 顺序没漂移
3. **TC16**：验证 dual upgrade race 仍被串行化
4. **TC22/23**：验证 ResidentDir offload 行为未因进程边界失真
5. **TC27**：验证 epoch wrap 与 half-range 比较仍正确
6. **TC28**：验证 resident/backstore 镜像一致性

---

## 9. 多进程环境下的竞态窗口分析

## 9.1 RecallResp 晚到 / 重复到达

### 风险

- owner 节点响应跨进程延迟
- duplicate/reorder 导致旧 `RecallResp` 晚于新事务到达

### 现有防线

- `checkEpochForLine()`
- `reqId` 匹配
- `targetNode` 匹配

### 迁移要求

- wire 中必须保留 `epoch + reqId + requesterNode + ownerNode`
- standalone 不得仅凭 `linePa` 接受 recall completion

## 9.2 InvalidateAck 乱序/重复

### 风险

- 多 sharer ack 跨链路返回
- 某些 ack 晚于 `GRANT_HANDSHAKE` 创建

### 现有防线

- `ackMask` 单调增长
- 非 target 节点 ack 直接拒绝
- duplicate ack 忽略

### 迁移要求

- 绝不能依赖 FIFO 假设完成安全性
- FIFO 只优化本地可解释性，不是正确性前提

## 9.3 UpgradeDone 早于所有 invalidate acks

### 风险

- 当前已经有 `upgradeDoneArrived` 临时缓存逻辑（`UBCCController.hh:135-138`, `UBCCController.cc:1885-1904`）

### 迁移要求

- 这一逻辑必须原样保留
- 不能因为跨进程化而假定 `Done` 一定晚于 acks

## 9.4 Clear replay / tombstone 窗口

### 风险

- gem5 重试或 IPC duplicate 导致重复 `ClearReq`

### 迁移要求

- tombstone 仍按 `(linePa, baseEpoch, reqId)` 查
- duplicate Clear 返回同一 accepted
- tombstone window 到期后的重复 Clear 可拒绝，但不得污染 committed state

## 9.5 backstore 回调与 resident eviction 交错

### 风险

- `issueBackstoreWrite/Delete` 发出后，resident slot 可能已被 victim 流程标记待回收

### 迁移要求

- ack 回调必须按 `linePa` 幂等处理
- `onBackstoreWriteAck()` / `onBackstoreDeleteAck()` 必须允许 “条目已不存在” 场景

## 9.6 全局时钟漂移

### 风险

- 若仍各进程各自读本地时间，则 `readyTick/deadlineTick/expireTick` 不可比较

### 迁移要求

- 必须执行 Q3=A：全局逻辑时钟统一授权
- 所有协议定时字段来自同一 scheduler 域

## 9.7 Router 本地 FIFO 与 Ns3UB 链路 delay 的叠加

### 风险

- 若两边都做重排，调试困难；若两边都不做保序，行为不稳定

### 迁移要求

- Router 只负责 per-pair FIFO
- Ns3UB 只负责 link delay / fault injection
- 不让 Router 同时承担网络故障模拟

---

## 10. 性能基准预期

> 以下是**迁移预期窗口**，不是现网实测值。

## 10.1 本地路径（TC1 / TC11 局部）

- 预期影响：**0% ~ 3%**
- 原因：本地 CHI/HN-F/L2 仍在 gem5 内，新增开销仅在真正走 UB path 的事件

## 10.2 远端共享读（TC2 / TC3 / TC6 / TC14）

- 预期影响：**+5% ~ +20% latency**
- 原因：
  - UBMsg serialize/deserialize
  - IPC/Ns3UB hop
  - global scheduler 驱动
- 但相对总跨节点开销，协议语义与链路传播本来就是主导项，所以增幅应受控

## 10.3 远端 unique / upgrade / invalidate（TC8 / TC16 / TC25）

- 预期影响：**+10% ~ +30% latency**
- 原因：
  - ack fanout/fanin 路径变成长
  - cross-process reorder 使 debug 与 replay 成本增加
- 可接受前提：不引入额外协议轮次

## 10.4 offload / resident 压力（TC22-TC24 / TC28）

- 吞吐关键取决于：
  - ResidentDir victim 选择
  - backstore callback 并发度
  - Router 队列深度与 watermark

### 目标窗口

- ResidentDir 容量内：与 in-proc 模式相比 **退化不超过 10%**
- 频繁 offload 场景：**退化不超过 25%**
- 若超过该窗口，应优先优化：
  1. wire 序列化
  2. Router drain 批处理
  3. backstore callback batching

## 10.5 调试开销

- 建议把 trace/debug 分级
- 默认回归关闭大部分逐消息日志
- 否则 standalone 模式性能退化会被日志 IO 淹没，测不到真实链路开销

---

## 11. 推荐目录重组方案

```text
standalone/
  ubcc/
    include/ubcc/
      ubmsg_semantic.h
      ubmsg_wire.h
      ubmsg_queue.h
      resident_dir.h
      ubcc_controller.h
      ubcc_host_if.h
      router_transport.h
      logical_clock.h
    src/
      ubmsg_wire.cc
      ubmsg_queue.cc
      resident_dir.cc
      ubcc_controller.cc
      ub_router.cc
      ns3ub_endpoint.cc
    tests/
      test_ubmsg_wire.cc
      test_router_fifo.cc
      test_ubcc_recall.cc
      test_ubcc_invalidate.cc
      test_ubcc_upgrade.cc
```

gem5 保留：

```text
gem5/src/mem/ruby/protocol/chi/ep/
  UBAdapter.{hh,cc}
  EPBackend.{hh,cc}
  EPRNFController.{hh,cc}
  EPSNFController.{hh,cc}
  MetaRNFController.{hh,cc}
```

---

## 12. 最小改动原则

如果你的目标是“先移植成功，再优化架构”，最稳妥做法是：

1. **先复制，不先移动**：在 `standalone/ubcc/` 建镜像代码
2. **先抽接口，不先改语义**：先抽 `IUbccHost / ILogicalClock / ITransport`
3. **先 A/B，再切主路**：保留 in-proc 路径直到 TC1-28 等价
4. **先同步兼容，再异步增强**：先让 `UBAdapter` API 不变，底层再换 IPC
5. **先保序，再故障注入**：先验证 FIFO 等价，再启用 Ns3UB reorder/dup/loss

---

## 13. 最终迁移清单（实际执行顺序）

### 第 1 批：协议冻结

- `UBMsg.hh` → 拆 semantic / wire
- 新增 serializer/deserializer

### 第 2 批：runtime 外提

- `UBMsgQueue.hh`
- `UBRouter.{hh,cc}`

### 第 3 批：状态机外提

- `ResidentDir.{hh,cc}`
- `UBCCController.{hh,cc}`

### 第 4 批：gem5 适配

- `UBAdapter.{hh,cc}` 改 IPC client
- `EPBackend.{hh,cc}` 接 host callbacks

### 第 5 批：全量验收

- TC1-28
- race/fault/recovery
- benchmark

---

## 14. 一句话建议

**真正该迁的是“UBCC 的协议权威层 + resident metadata 层 + message runtime 层”，而不是 EP/CHI 控制器。**

如果按这个边界迁，你要改的关键接口只有 4 组：

1. **UBMsg wire format**
2. **UBCC host callback**（`issueBackstore*`, `notifyGrantVisible`）
3. **UBAdapter transport client**
4. **global logical clock + two-level latency model**

这样迁移风险最小，且最贴合你后续的 Ns3UB / 多进程部署目标。
