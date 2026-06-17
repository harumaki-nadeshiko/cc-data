# Message Passing Refactor Plan

## 1. 目标与硬约束

### 1.1 已确认决策
- **Q1**：按“语义边”计数；同一 `msg type + src→dst` 算 1 条边。
- **Q2**：只有跨 `UBAdapter→UBRouter` 的调用才算 message；但**凡是访问 UBCC，不论 home 是本地还是远端，都必须走**：

```text
EPBackend → UBAdapter.sendMessage() → MsgQ → UBRouter → UBCC
```

- **Q3**：消息格式采用**固定 envelope + tagged union**。
- **Q4**：**UBRouter 负责路由决策**（local/remote 判断、入队、投递）。

### 1.2 本次重构要消灭的现状
当前代码仍存在大量同步直连：
- `EPBackend.cc:443` 直接 `homeUbcc->processOuterRequest(...)`
- `EPBackend.cc:494` 直接 `ownerBackend->handleRecallRequest(...)`
- `EPBackend.cc:558` 直接 `sharerBackend->handleInvalidationRequest(...)`
- `EPBackend.cc:1067` 直接 `homeUbcc->processRecallResponse(...)`
- `EPBackend.cc:1137` 直接 `homeUbcc->processWriteback(...)`
- `EPBackend.cc:1219` 直接 `homeUbcc->processEvict(...)`
- `EPBackend.cc:1368` 直接 `homeUbcc->processInvalidationAck(...)`
- `EPBackend.cc:1430` 直接 `homeUbcc->processOuterUpgradeReq(...)`
- `EPBackend.cc:1559` 直接 `homeUbcc->processOuterUpgradeDone(...)`
- `EPBackend.cc:1622` 直接 `homeUbcc->processClear(...)`
- `UBCCController.cc:1039` 直接 `reqBackend->notifyUpgradeAckReady(...)`

这些都要改成消息驱动。

---

## 2. 重构后的总拓扑

每个 node 新增两层：

```text
EPSNF / EPRNF
    ↓
 EPBackend
    ↓
 UBAdapter(node N)
    ↓
 MsgQueue(src=N,dst=M)
    ↓
 UBRouter(node M)
    ↓
 目标端：UBCC 或 UBAdapter.localDelivery(...)
```

其中：
- **UBAdapter**：EPBackend 的消息化门面，负责封包、注册回调、收包解包、回调本地 EPBackend。
- **UBRouter**：节点级路由器，负责决定消息落到本地 UBCC 还是本地 UBAdapter，再通过 `MsgQueue` 施加延迟。
- **MsgQueue**：每个 `srcNode→dstNode` 一条 FIFO，统一承载所有 UB 消息；`src==dst` 也必须排队。

---

## 3. 具体类设计

## 3.1 `UBMsg`：固定 envelope + tagged union

建议新增 `gem5/src/mem/ruby/protocol/chi/ep/UBMsg.hh`。

```c++
enum class UBMsgType : uint16_t {
    ReadReq,
    ReadResp,
    RecallReq,
    RecallResp,
    InvalidateReq,
    InvalidateAck,
    WritebackReq,
    WritebackResp,
    EvictReq,
    EvictResp,
    UpgradeReq,
    UpgradeResp,
    UpgradeDoneReq,
    UpgradeDoneResp,
    ClearReq,
    ClearResp,
};

enum UBMsgFlags : uint32_t {
    UB_FLAG_WRITE_INTENT   = 1u << 0,
    UB_FLAG_KEEP_AS_CLEAN  = 1u << 1,
    UB_FLAG_ACCEPTED       = 1u << 2,
    UB_FLAG_DATA_RETURNED  = 1u << 3,
    UB_FLAG_HAS_DATA       = 1u << 4,
    UB_FLAG_IS_READ_RECALL = 1u << 5,
    UB_FLAG_BUSY           = 1u << 6,
};

struct UBMsgHeader {
    UBMsgType type;
    uint16_t srcNode;
    uint16_t dstNode;
    uint16_t homeNode;
    uint16_t requesterNode;
    uint16_t targetNode;
    uint32_t flags;
    uint64_t homeLinePa;
    uint64_t localLinePa;
    uint64_t epoch;
    uint64_t reqId;
    uint64_t seqNum;
    Tick enqueueTick;
    Tick readyTick;
};

struct UBReadReqBody      { uint8_t neededPerm; };
struct UBReadRespBody     { int8_t grantType; int8_t dataSource; Tick grantVisibleTick; Tick sentinelVisibleTick; };
struct UBRecallReqBody    { };
struct UBRecallRespBody   { DataBlock data; };
struct UBInvalidateReqBody{ };
struct UBInvalidateAckBody{ };
struct UBWritebackReqBody { };
struct UBWritebackRespBody{ };
struct UBEvictReqBody     { };
struct UBEvictRespBody    { };
struct UBUpgradeReqBody   { uint8_t desiredPerm; uint8_t cause; };
struct UBUpgradeRespBody  { };
struct UBUpgradeDoneReqBody { };
struct UBUpgradeDoneRespBody { };
struct UBClearReqBody     { uint8_t reason; };
struct UBClearRespBody    { };

union UBMsgBody {
    UBReadReqBody readReq;
    UBReadRespBody readResp;
    UBRecallReqBody recallReq;
    UBRecallRespBody recallResp;
    UBInvalidateReqBody invalidateReq;
    UBInvalidateAckBody invalidateAck;
    UBWritebackReqBody writebackReq;
    UBWritebackRespBody writebackResp;
    UBEvictReqBody evictReq;
    UBEvictRespBody evictResp;
    UBUpgradeReqBody upgradeReq;
    UBUpgradeRespBody upgradeResp;
    UBUpgradeDoneReqBody upgradeDoneReq;
    UBUpgradeDoneRespBody upgradeDoneResp;
    UBClearReqBody clearReq;
    UBClearRespBody clearResp;
};

struct UBMsg {
    UBMsgHeader h;
    UBMsgBody b;
};
```

### 字段约束
- `homeLinePa`：**唯一 canonical key**，UBCC / tombstone / outstanding 全部继续用它。
- `localLinePa`：只在 recall / invalidate / requester-side callback 中使用，避免重复地址重建。
- `reqId + requesterNode + homeLinePa`：事务主键。
- `seqNum`：每个 `srcNode` 单调递增，用于 FIFO 调试和重放审计。

### 为什么这样定义
- 兼容当前 `Outer*Msg` 全家桶，不必把 10+ 种 struct 在第一步全部删掉。
- `DataBlock` 只在 `RecallResp` / `ReadResp(hasData)` 路径占用。
- 未来多进程化时，这个结构可以直接变成 wire-format 原型。

---

## 3.2 `MsgQueue` 设计

建议新增：
- `UBMsgQueue.hh`
- `UBMsgQueue.cc`

### 设计原则
- **粒度**：每个 `srcNode→dstNode` 一条 FIFO。
- **延迟**：每条队列都有可配置 `T`；默认统一参数 `ub_msg_latency`，也允许后续扩展为矩阵。
- **本地 home 也排队**：`src==dst` 时仍进入 `(N,N)` 队列，绝不 0-tick 短路到 UBCC。
- **投递顺序**：同一队列按 `(readyTick, seqNum)` 保序。
- **承载范围**：只承载 `UBAdapter↔UBRouter` 跨边消息；UBAdapter 内部本地函数调用不进队列。

### 建议接口

```c++
class UBMsgQueue
{
  public:
    struct Entry {
        UBMsg msg;
        Tick readyTick;
    };

    void enqueue(const UBMsg &msg, Tick now, Tick latency);
    bool hasReady(Tick now) const;
    UBMsg popReady(Tick now);
    size_t size() const;

  private:
    std::deque<Entry> _fifo;
};
```

### 事件调度
- `enqueue()` 后，如果新头元素更早到达，则 `schedule(routerEvent, readyTick)`。
- `routerEvent` 每次 drain 当前 router 上所有 ready 的 ingress queue。
- 为避免 same-tick 风暴，单次事件可设 `maxDrainPerWakeup`，剩余下 tick 继续。

---

## 3.3 `UBAdapter` 设计

建议新增：
- `UBAdapter.hh`
- `UBAdapter.cc`
- `UBAdapter.py`

### 角色
`UBAdapter` 是 **EPBackend 面向消息层的唯一出口**，以及 **Router 投递到本地 EPBackend 的唯一入口**。

### 关键成员

```c++
class UBAdapter : public SimObject
{
  private:
    int _nodeId;
    EPBackend *_backend;
    UBRouter *_router;
    NodeAddressMap _addrMap;
    uint64_t _nextSeq = 1;

    struct PendingTxn {
        UBMsgType reqType;
        uint64_t homeLinePa;
        uint64_t localLinePa;
        int homeNode;
        std::function<void(const UBMsg&)> onResp;
    };

    std::map<uint64_t, PendingTxn> _pendingByReqId;
```

### 关键方法

```c++
// requester/home 发起
void sendReadReq(..., std::function<void(const UBMsg&)> cb);
void sendWritebackReq(..., std::function<void(const UBMsg&)> cb);
void sendEvictReq(..., std::function<void(const UBMsg&)> cb);
void sendUpgradeReq(..., std::function<void(const UBMsg&)> cb);
void sendUpgradeDoneReq(..., std::function<void(const UBMsg&)> cb);
void sendClearReq(..., std::function<void(const UBMsg&)> cb);
void sendRecallResp(...);
void sendInvalidateAck(...);

// Router 投递入口
void recvFromRouter(const UBMsg &msg);

// 本地分发
void handleRecallReq(const UBMsg &msg);
void handleInvalidateReq(const UBMsg &msg);
void handleReadResp(const UBMsg &msg);
void handleWritebackResp(const UBMsg &msg);
void handleEvictResp(const UBMsg &msg);
void handleUpgradeResp(const UBMsg &msg);
void handleUpgradeDoneResp(const UBMsg &msg);
void handleClearResp(const UBMsg &msg);
```

### 适配策略
1. **EPBackend 不再直接碰 `UBCCController::getInstance()` 或 `EPBackend::getBackendInstance()`**。
2. EPBackend 只调用 `UBAdapter`。
3. `UBAdapter` 在发起侧登记 callback；返回消息到达后按 `reqId` 触发。
4. recall / invalidation 这类 home→owner/sharer 的“下行消息”，由 `UBAdapter.recvFromRouter()` 直接回调 EPBackend 现有逻辑。

### EPBackend 内部的推荐回调拆分
- `onReadGrant(const UBMsg&)`
- `onRecallReq(const UBMsg&)`
- `onInvalidateReq(const UBMsg&)`
- `onWritebackAck(const UBMsg&)`
- `onEvictAck(const UBMsg&)`
- `onUpgradeAck(const UBMsg&)`
- `onUpgradeDoneAck(const UBMsg&)`
- `onClearAck(const UBMsg&)`

这样可以最大限度复用现在的 `handleGrant / sendRecallResponse / sendInvalidationAck / notifyUpgradeAckReady` 逻辑。

---

## 3.4 `UBRouter` 设计

建议新增：
- `UBRouter.hh`
- `UBRouter.cc`
- `UBRouter.py`

### 角色
- 接收本 node 的 `UBAdapter.sendMessage()`。
- 进行 **local / remote** 路由决策。
- 将消息压入 `MsgQueue(src,dst)`。
- 消息 ready 后，投递给：
  - 本地 `UBCCController`
  - 或本地 `UBAdapter.recvFromRouter()`

### 关键成员

```c++
class UBRouter : public SimObject
{
  private:
    int _nodeId;
    Tick _defaultLatency;
    UBAdapter *_localAdapter;
    UBCCController *_localUbcc;

    static std::map<int, UBRouter*> _routers;
    std::map<std::pair<int,int>, UBMsgQueue> _pairQueues;
    EventFunctionWrapper _drainEvent;
};
```

### 关键方法

```c++
void bindAdapter(UBAdapter *adapter);
void bindUbcc(UBCCController *ubcc);

void sendMessage(const UBMsg &msg);         // 入口：Adapter 调用
void enqueueForPair(const UBMsg &msg);      // 选队列并施加 T
void drainReadyQueues();                    // 事件驱动出队
void deliverLocal(const UBMsg &msg);        // dst==_nodeId
void deliverRemote(const UBMsg &msg);       // 查 dst router 并本地投递

// 目的端处理
void routeToUbcc(const UBMsg &msg);
void routeToAdapter(const UBMsg &msg);
```

### 路由规则
- `ReadReq / WritebackReq / EvictReq / UpgradeReq / UpgradeDoneReq / ClearReq / RecallResp / InvalidateAck`
  → **目标是 home node 的 UBCC**。
- `RecallReq / InvalidateReq`
  → **目标是 owner/sharer node 的 UBAdapter**。
- `ReadResp / WritebackResp / EvictResp / UpgradeResp / UpgradeDoneResp / ClearResp`
  → **目标是 requester node 的 UBAdapter**。

### 为什么 Router 不让 Adapter 自己决定本地/远端
这是 Q4 的明确结论。否则会把：
- 地址翻译策略
- 本地 loopback 入队策略
- 本地 UBCC 强制排队规则

散落在每个调用点，后续很难验证“local-home 也一定进 MsgQ”。

---

## 4. 12+ 调用点到消息类型的映射

> 说明：表中“当前调用点”是现有代码基线；“未来消息”是重构后唯一合法路径。

| # | 当前调用点 | 语义 | 未来消息 | 回调/完成动作 |
|---|---|---|---|---|
| 1 | `EPSNFController.cc:200` `handleRemoteMiss()` | 首次 `ReadNoSnp` miss | `ReadReq` | `ReadResp` 到 requester adapter；触发 `EPBackend::onReadGrant()`，再由 EPSNF 发 `CompData` |
| 2 | `EPSNFController.cc:74` `handleRemoteMiss()` | retry miss | `ReadReq` | 同 #1，但 callback 走 retry queue 出队路径 |
| 3 | `EPBackend.cc:443` `processOuterRequest()` | requester→home UBCC 读请求 | `ReadReq` | home UBCC 处理后发 `ReadResp(Grant/Busy, grant ticks, dataSource)` |
| 4 | `EPBackend.cc:494` `ownerBackend->handleRecallRequest()` | home→owner recall | `RecallReq` | owner adapter 回调 EPBackend，CHI 完成后发 `RecallResp` |
| 5 | `EPBackend.cc:558` `sharerBackend->handleInvalidationRequest()` | home→sharer invalidate | `InvalidateReq` | sharer adapter 回调 EPBackend，CHI 完成后发 `InvalidateAck` |
| 6 | `EPBackend.cc:1067` `processRecallResponse()` | owner→home recall 完成 | `RecallResp` | home UBCC release recall barrier，必要时转 `GRANT_HANDSHAKE` |
| 7 | `EPBackend.cc:1137` `processWriteback()` | requester→home dirty writeback | `WritebackReq` | `WritebackResp(success)` 回 requester，更新 requester state |
| 8 | `EPBackend.cc:1219` `processEvict()` | requester→home clean evict | `EvictReq` | `EvictResp(success)` 回 requester |
| 9 | `EPBackend.cc:1368` `processInvalidationAck()` | sharer→home invalidate ack | `InvalidateAck` | home UBCC 递减 ack 计数；若全到齐则推进 invalidate/upgrade |
| 10 | `EPRNFController.cc:673` `notifyLocalWriteUpgrade()` | sharer 本地升级请求 home | `UpgradeReq` | `UpgradeResp(accepted/reservedEpoch)` 回 requester adapter |
| 11 | `EPBackend.cc:1476` 升级路径 fanout invalidation | home→other sharers invalidate | `InvalidateReq` | `InvalidateAck` 回 home；全部到齐后再发 `UpgradeResp(accepted=true)` |
| 12 | `UBCCController.cc:1039` `notifyUpgradeAckReady()` | home→requester upgrade ack ready | `UpgradeResp` | requester adapter 调 `EPBackend::notifyUpgradeAckReady()`，再触发 `EPRNFController::receiveUpgradeAck()` |
| 13 | `EPRNFController.cc:1371` `sendUpgradeDone()` | requester→home upgrade 完成 | `UpgradeDoneReq` | `UpgradeDoneResp(success)` 回 requester |
| 14 | `EPBackend.cc:1559` `processOuterUpgradeDone()` | upgrade done 到 home UBCC | `UpgradeDoneReq` | home UBCC commit intended result，回 `UpgradeDoneResp` |
| 15 | `EPBackend.cc:1622` `processClear()` | requester→home clear grant handshake | `ClearReq` | `ClearResp(accepted)` 回 requester |
| 16 | `EPBackend.cc:681` `sendClear()` | grant 后提交 commit | `ClearReq` | `ClearResp` callback 清理 pending grant tuple |

### 需要显式删除的“非法捷径”
- `EPBackend.cc:575-576`：缺失 sharer backend 时直接 `processInvalidationAck(...)`
- `EPBackend.cc:1483`：升级 fanout 中直接 `processInvalidationAck(...)`
- 所有 `UBCCController::getInstance(...)` / `EPBackend::getBackendInstance(...)` 的跨 node 同步直连

这些都违反消息层封装，应全部由 router registry + queue 替代。

---

## 5. 迁移顺序

### Phase 0：基础设施先落地
1. 新增 `UBMsg / UBMsgQueue / UBAdapter / UBRouter`。
2. 在 `SConscript`、Python SimObject、`CHI_ubcc_framework.py` 中把对象接起来。
3. 仅做“空转”自测：adapter 发 dummy msg，经 queue 后回本地 adapter。

### Phase 1：先改 requester→home 的同步 UBCC 调用
优先级最高，因为这是 Q2 的硬要求。
1. `processOuterRequest` 路径（`ReadReq/ReadResp`）
2. `processWriteback` 路径（`WritebackReq/Resp`）
3. `processEvict` 路径（`EvictReq/Resp`）
4. `processClear` 路径（`ClearReq/Resp`）
5. `processOuterUpgradeReq/Done` 路径（`UpgradeReq/Resp`, `UpgradeDoneReq/Resp`）

### Phase 2：再改 home→owner/sharer 的下行 fanout
1. `RecallReq/RecallResp`
2. `InvalidateReq/InvalidateAck`
3. 升级路径复用 invalidate fanout

### Phase 3：收口 callback 与异步化
1. `EPSNFController` 改成完全等待 `ReadResp` callback，不再假定 `handleRemoteMiss()` 同步返回。
2. `EPBackend` 切分 `handleXxx()` 为“发请求”和“响应回调”两段。
3. `EPRNFController` 保持 CHI 内部状态机不变，只改与 backend 的交互接口。

### Phase 4：删旧注册表和旧直连接口
1. 删除 `EPBackend::_backendInstances`（`EPBackend.cc:69-76`, `EPBackend.hh:719`）
2. 删除 `UBCCController::_instances`（`UBCCController.cc:20-34`, `UBCCController.hh:579`）
3. 删除所有 fallback 直调逻辑

---

## 6. 各文件修改建议

## 6.1 新增文件

### `gem5/src/mem/ruby/protocol/chi/ep/UBMsg.hh`
- 定义 `UBMsgType / UBMsgHeader / UBMsgBody / UBMsg`
- 提供 debug stringify helper

### `gem5/src/mem/ruby/protocol/chi/ep/UBMsgQueue.hh`
### `gem5/src/mem/ruby/protocol/chi/ep/UBMsgQueue.cc`
- per-node-pair FIFO
- enqueue / popReady / stats

### `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.hh`
### `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc`
- request/response callback registry
- 与 EPBackend 的桥接

### `gem5/src/mem/ruby/protocol/chi/ep/UBRouter.hh`
### `gem5/src/mem/ruby/protocol/chi/ep/UBRouter.cc`
- router registry
- pair queue matrix
- routing + delivery

### `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.py`
### `gem5/src/mem/ruby/protocol/chi/ep/UBRouter.py`
- SimObject 参数：`node_id`, `latency`, peer bindings

## 6.2 修改文件

### `gem5/src/mem/ruby/protocol/chi/ep/SConscript`
- 增加新源文件和新 SimObject。

### `gem5/configs/ruby/CHI_ubcc_framework.py`
- 在每 node 创建 `UBAdapter`、`UBRouter`。
- `EPBackend` 构造后绑定 `adapter/router/ubcc`。
- 暴露统一 `ub_msg_latency` 参数。

### `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh`
- 增加 `UBAdapter* _ubAdapter`。
- 删除/废弃跨节点 registry API。
- 增加异步响应入口 `onReadResp/onWritebackResp/...`。
- 保留 `Outer*Msg` 作为过渡观测结构，后续逐步迁移到 `UBMsg`。

### `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc`
- `handleRemoteMiss()` 改为发 `ReadReq`，不再直接拿到 grant。
- `sendRecallResponse()/sendInvalidationAck()/sendClear()/sendUpgradeDone()/handleWriteback()/handleEvict()` 全部改成 adapter 发送。
- 删除 `getBackendInstance()` 静态注册表逻辑。

### `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh`
- 增加 router callback 入口：`handleUbMsg(const UBMsg&)` 或分类型入口。
- 删除 `getInstance()` 暴露面。

### `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc`
- `processOuterRequest/processWriteback/processEvict/processRecallResponse/processInvalidationAck/processOuterUpgradeReq/processOuterUpgradeDone/processClear`
  从“同步 API”改为“本地被 router 调用后，生成 response message”。
- `notifyUpgradeAckReady()` 改为发 `UpgradeResp`，不再直调 requester backend。

### `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc`
- `recvRequestMsg()` 和 retry queue 改为等待异步 `ReadResp`。
- 发送 `CompData` 的责任保留，但时机改为 callback 驱动。

### `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc`
- 与 `notifyLocalWriteUpgrade()/sendUpgradeDone()` 的接口改成异步完成。
- `receiveUpgradeAck()` 改成由 adapter callback 触发，而不是 UBCC 直调。

### 自测/测试文件
- `M5SelfTest.cc`, `M6SelfTest.cc`, `M7SelfTest.cc`, `M8SelfTest.cc`
- `tests/ubcc/ep-rnf/*`

重点补：
- local-home 访问是否真的经过 `(N,N)` queue
- recall / invalidate / upgrade ack 的消息顺序
- Busy / retry 是否仍正确

---

## 7. LOC 估算

| 文件 | 类型 | 估算 LOC |
|---|---:|---:|
| `UBMsg.hh` | 新增 | 150-220 |
| `UBMsgQueue.hh/.cc` | 新增 | 180-260 |
| `UBAdapter.hh/.cc` | 新增 | 320-450 |
| `UBRouter.hh/.cc` | 新增 | 260-360 |
| `UBAdapter.py`, `UBRouter.py` | 新增 | 30-60 |
| `SConscript` | 修改 | 10-20 |
| `CHI_ubcc_framework.py` | 修改 | 40-90 |
| `EPBackend.hh/.cc` | 重点修改 | 250-420 |
| `UBCCController.hh/.cc` | 重点修改 | 220-360 |
| `EPSNFController.cc` | 修改 | 80-150 |
| `EPRNFController.cc` | 修改 | 60-120 |
| selftests + python tests | 修改 | 150-280 |
| **总计** |  | **1750-2790 LOC** |

保守估计：**核心协议代码净新增/重写约 1.4k-2.0k LOC**。

---

## 8. 推荐落地顺序（最小风险版）

1. **先只打通 `ReadReq/ReadResp`**，并验证 local-home 也排队。
2. 再接 `Writeback/Evict/Clear`，因为这些都是 requester→home 单往返。
3. 再接 `Recall/Invalidate`，因为它们牵涉 home→owner/sharer fanout。
4. 最后接 `UpgradeReq/UpgradeResp/UpgradeDone`，因为它叠加了 invalidation barrier。
5. 全路径通过后，再删除旧 registry/fallback 逻辑。

这个顺序能最快验证 **Q2 的核心约束**，同时把最复杂的 upgrade 留到最后。

---

## 9. 结论

最终方案应当把现有“同步函数调用网络”收敛为：

```text
EPBackend ↔ UBAdapter ↔ MsgQueue ↔ UBRouter ↔ { UBCC | UBAdapter }
```

并满足三条不可破坏的性质：
- **所有 UBCC 访问都必须消息化，包含 local-home。**
- **所有跨 node 交互都不再使用静态 registry + 直接函数调用。**
- **所有同步返回值都改成 response message + callback 完成。**

这份方案与当前代码基线对齐，且能最小化对 CHI 内部状态机的扰动：CHI 侧保持原样，重构集中在 `EPBackend/UBCC` 之间的外层协议边界。
