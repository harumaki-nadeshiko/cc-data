# transportRecv 重构方案（event-driven + synced_receive + callback）

## 1. 问题分析

### 1.1 现状

当前 `UBAdapter::transportRecv()` 在 `_port != nullptr` 时采用 busy-poll：

```cpp
for (int i = 0; i < kMaxPollIters; ++i) {
    framework::MemMessage *m = _port->recv(visible);
    if (!m) {
        continue;
    }
    recvFromRouter(*coh);
    if (_lastResponseValid && match(expectedType, expectedReqId)) {
        return true;
    }
}
return false;
```

该模型在单 gem5 进程内还能“碰巧工作”，但在多进程 `gem5 ↔ ubio ↔ networksim ↔ ubio ↔ gem5` 路径下已经与体系结构不兼容。

### 1.2 busy-poll 的 3 条致命缺陷

#### 缺陷 1：阻塞 gem5 事件循环，破坏分布式前进条件

旧单进程模型中，跨节点消息的发送、处理、返回都在同一事件循环里顺序推进；而多进程模型中，真正的推进条件变成：

- 本地 gem5 继续运行
- 对端 ubio 继续 drain
- networksim 继续转发
- 对端 gem5 继续执行协议事件

busy-poll 把本地 gem5 卡死在 `sendReadReq()->transportRecv()` 内，导致：

- 本地无法继续消费来自 Port 的其他协议消息
- 无法推进与该事务相关的后续 CHI/EP 事件
- 无法给对端提供“我已前进到某 tick”的同步信息

结果不是“等待响应”，而是“自己阻止响应产生所需的系统前进”。

#### 缺陷 2：把合法的并发请求误当作“未匹配噪音”，诱发重试风暴

在 TC2 场景中：

1. node0 发 `ReadReq(reqId=1)` 后 busy-poll 等 `ReadResp`
2. node1 合法地发来另一条 `ReadReq`，要求回收 node0 的所有权
3. 该消息到达 node0 时，`transportRecv()` 会先 `recvFromRouter()` 处理它
4. 但它不是 node0 当前期待的 `ReadResp(reqId=1)`
5. `sendReadReq()` 最终返回失败，EPBackend 立刻再次发新的 `ReadReq`

于是系统从“一个未完成外层事务”退化为“同一 PA 上连续生成数百个新 reqId 的外发请求”，把 Port 与 networksim FIFO 队列淹没，真实 `ReadResp` 反而被埋在队尾。

#### 缺陷 3：与 `synced_receive` 的可见性模型冲突

Port/ubio 侧已经具备 `emitSync()` / `CONTROL_SYNC` / `synced_receive_lower_bound()` 的同步接口，目标是让多进程按照 safeTick 暴露消息。busy-poll 却直接使用：

- `visible = UINT64_MAX`
- 收到就立刻消费
- 不等待全局 lower-bound

这会带来两个问题：

1. **过早消费**：消息虽然到了本地 socket，但其因果前驱未必已经全局可见
2. **无同步参与**：gem5 侧没有稳定发 `CONTROL_SYNC`，对端 safeTick 也无法正确推进

结论：busy-poll 不是“性能差一点”的实现，而是**与多进程分布式仿真时序模型根本矛盾**。

### 1.3 为什么旧顺序保证失效了

旧模型：

```text
node0 gem5 event
  -> UBIOModule enqueue
  -> node1 UBIOModule drain
  -> UBCC process
  -> response 回到 node0 adapter
  -> sendReadReq 同步返回
```

该链路在单进程内是同步调用栈，不存在“另一节点也在同时执行独立事件循环”的问题。

新模型：

```text
node0 gem5 -> ZMQ -> ubio0 -> networksim -> ubio1 -> node1 gem5/UBCC
                                          <- 反向 6-hop 返回 <-
```

现在 node0 与 node1 是两个独立事件源；node1 的回收请求、node0 的等待、networksim 的转发都可能交织。旧的“发出请求后同步阻塞直到返回”假设已经不成立。

---

## 2. 新架构全景

### 2.1 目标

本次重构建立以下统一模型：

1. **所有 request/response 型事务在 `_port` 路径上统一改为异步 pending-callback**
2. **UBAdapter 自己拥有 gem5 Event，并负责 emitSync + drain + callback dispatch**
3. **callback 仅在 `resp.timestamp <= safeTick` 时执行**
4. **EPBackend 与 UBCC 的 request/response 交互全部经 UBAdapter/Port，不再直连 `_ubcc`**
5. **旧 `_router == legacy single-process` 路径保持不变，Port 路径 opt-in**

### 2.2 新事件循环图

```text
gem5(UBAdapter::_responseCheckEvent)
  ├─ emitSync(curTick)
  ├─ drain Port.recv(curVisibleTick)
  │    ├─ CONTROL_SYNC -> 更新 _nextVisibleTick / safeTick 输入
  │    ├─ COH_MSG(resp) -> 挂入 _readyResponsesByReqId
  │    └─ COH_MSG(async ingress) -> recvFromRouter() -> EPBackend/EPRNF
  ├─ recompute safeTick via synced_receive_lower_bound-compatible 语义
  ├─ 对所有 ready response 做校验
  ├─ 仅对 resp.timestamp <= safeTick 的事务执行 callback
  ├─ 处理 timeout / resend / deferred retry 触发
  └─ schedule(_responseCheckEvent, curTick + pollInterval)
```

### 2.3 消息生命周期

#### ReadReq / ReadResp 路径

```text
EPSNFController::recvRequestMsg
  -> EPBackend::beginRemoteMiss(linePa,...)
  -> UBAdapter::sendReadReqAsync(..., onReadResp)
  -> _pendingByReqId[reqId] = PendingTxn
  -> transportSend(req)

后续某个 event tick:
UBAdapter::_responseCheckEvent
  -> emitSync + drain
  -> 收到 ReadResp(reqId)
  -> 放入 ready 队列
  -> safeTick >= resp.timestamp 时执行 onReadResp(resp)
  -> EPBackend::completeRemoteMiss(ctx, resp)
  -> recall/invalidation/Clear/grant 安装继续推进
```

#### Recall / Invalidate 等异步 ingress 路径

```text
Port 收到 RecallReq / InvalidateReq
  -> UBAdapter::recvFromRouter(msg)
  -> EPBackend::handleRecallRequest / handleInvalidationRequest
  -> EP-RNF 发本地 CHI 请求
  -> 完成后通过 UBAdapter 发送 RecallResp / InvalidateAck
```

该类消息**不进入 request/response callback 等待队列**，而是作为异步协议事件直接处理。

### 2.4 pending 状态机

每个 Port request/response 事务统一采用如下本地状态：

```text
ALLOCATED
  -> SEND_PENDING         // 已建 PendingTxn，尚未成功出站
  -> IN_FLIGHT            // 已出站，等待 response
  -> RESP_ARRIVED         // 响应已到本地，但未过 safeTick
  -> CALLBACK_READY       // safeTick 已满足，可调用 completion
  -> COMPLETED            // callback 已执行并清理
  -> RESEND_SCHEDULED     // 仅 send fail/未出站 场景，可复用 reqId
  -> RETRY_DEFERRED       // 已出站后失败/超时/BUSY，生成新 reqId 的下一次尝试
  -> FAILED               // retry budget 用尽或校验失败
```

---

## 3. UBAdapter 改造

### 3.1 核心职责调整

`UBAdapter` 从“同步 helper”升级为 Port 模式下的**事务调度器**，职责包括：

1. 持有 `_responseCheckEvent`
2. 周期执行 `emitSync + drain + safeTick 推进 + callback dispatch`
3. 维护 `_pendingByReqId`
4. 维护 per-PA active/deferred retry 账本
5. 将 Recall/Invalidate 等异步 ingress 继续路由到 `recvFromRouter()`

### 3.2 单事件循环设计

新增：

- `EventFunctionWrapper _responseCheckEvent`
- `Tick _portPollInterval`
- `uint64_t _safeTick`
- `std::deque<CoherenceMessage> _readyResponses` 或 `std::map<uint64_t, CoherenceMessage>`
- `std::map<uint64_t, PendingTxn> _pendingByReqId`

单事件循环伪码：

```cpp
void UBAdapter::checkPortProgress()
{
    if (!_port) {
        return;
    }

    const Tick now = curTick();
    _port->emitSync(now);

    // 1. 先 drain CONTROL_SYNC / COH_MSG 到本地缓存
    drainPort(now);

    // 2. 依据 Port::_nextVisibleTick 计算当前 safeTick
    updateSafeTick(now);

    // 3. 对 resp.timestamp <= safeTick 的 pending 执行 callback
    dispatchReadyCallbacks();

    // 4. timeout / resend / deferred retry
    processRetryAndTimeout();

    // 5. 周期重调度
    schedule(_responseCheckEvent, now + _portPollInterval);
}
```

### 3.3 `transportRecv()` 的新角色

旧 `transportRecv(expectedType, expectedReqId)` 是同步等待 API，必须废弃其 Port busy-poll 语义。

改造后建议：

- `_port == nullptr`：保持原旧路径逻辑，供 legacy router 继续使用
- `_port != nullptr`：`transportRecv()` 不再作为“阻塞等待”使用；仅保留为内部 drain helper，或直接拆成：
  - `drainPortMessages(visibleTick)`
  - `bufferOrDispatchMessage(msg)`

重点是：**Port 模式下，任何调用点都不得再依赖 `transportRecv()` 的同步返回值。**

### 3.4 safeTick 门控回调

冻结决策要求 callback 在 `resp.timestamp <= safeTick` 后执行，而不是“刚收到就执行”。因此 UBAdapter 必须分离：

1. **响应到达本地 socket**
2. **响应在全局同步语义上可消费**

实现要求：

- drain 阶段收到匹配 response 时，仅将其挂到 `PendingTxn::response` 并标记 `RESP_ARRIVED`
- `dispatchReadyCallbacks()` 阶段检查：
  - `txn.hasResponse == true`
  - `txn.responseTimestamp <= _safeTick`
  - `txn.validationPassed == true`
- 满足后才调用 `txn.onResp(txn.response)`

这保证上层 EPBackend 看到的是**已过 safeTick 的协议响应**。

---

## 4. sendReadReq → fire-and-forget

### 4.1 API 重构方向

当前同步接口：

```cpp
int sendReadReq(..., Tick *outGrantVisibleTick, ..., DataBlock *outGrantData,...)
```

改为异步接口：

```cpp
using ReadRespCallback = std::function<void(bool ok,
                                            FailureReason why,
                                            const CoherenceMessage *resp)>;

bool sendReadReqAsync(..., ReadRespCallback cb);
```

返回值 `bool` 仅表示“PendingTxn 是否成功建账并进入发送流程”；真正 grant 结果通过 callback 返回。

### 4.2 PendingTxn 完整字段

建议 `PendingTxn` 最少包含：

```cpp
struct PendingTxn {
    // identity
    uint64_t reqId;
    CoherenceMessageType reqType;
    CoherenceMessageType expectedRespType;
    uint64_t homeLinePa;
    uint64_t localLinePa;
    uint64_t epoch;
    int homeNode;
    int homeSocket;

    // send / visibility lifecycle
    bool sendIssued;          // 是否已成功出站
    bool hasResponse;
    bool callbackDone;
    Tick createTick;
    Tick firstSendTick;
    Tick responseTick;
    Tick deadlineTick;
    Tick nextRetryTick;

    // retry / budget
    uint32_t resendCount;     // 未出站重发，可复用 reqId
    uint32_t retryCount;      // 已出站后新 reqId retry 次数
    FailureReason lastFailure;

    // response buffer
    CoherenceMessage response;

    // callback
    std::function<void(bool, FailureReason, const CoherenceMessage*)> onResp;

    // validation / bookkeeping
    uint64_t grantBaseEpochHint;
    uint64_t owningPaKey;     // per-PA retry账本索引
};
```

### 4.3 校验规则

冻结决策要求 `reqId` 为主键，但必须强校验。匹配流程应为：

1. 先按 `reqId` 查 `_pendingByReqId`
2. 再验证：
   - `msg.h.type == txn.expectedRespType`
   - `msg.h.homeLinePa == txn.homeLinePa`
   - `msg.h.epoch == txn.epoch` 或满足 grantBaseEpoch 重绑定规则
   - `msg.h.srcNode == txn.homeNode`
3. 任一失败：
   - 打印高优先级 warn/fatal-ready 诊断
   - 标记 `FAILED`
   - 不得把错误响应误交给其他事务

### 4.4 resend vs retry

按冻结决策明确区分：

#### resend（复用 reqId）

仅当请求**尚未成功出站**时允许，例如：

- `transportSend()` 失败
- send buffer 分配失败
- socket 暂不可写

此时：

- 保留同一个 `reqId`
- `PendingTxn` 留在原位
- 增加 `resendCount`
- 按 send-fail 指数退避

#### retry（新 reqId）

当请求已经出站后，不管失败原因是 timeout、BUSY、远端拒绝还是需要下一轮尝试，都必须：

- 生成新 `reqId`
- 继承 per-PA 上下文
- 保留原 `grantBaseEpoch` / requester line 语义
- 将旧 `PendingTxn` 收尾为 `FAILED` 或 `COMPLETED(BUSY)`
- 在 deferred slot 中排入下一次尝试

这消除了“一个已出站事务被本地逻辑偷偷复用同 reqId 再发一遍”的歧义。

---

## 5. EPBackend 改造

### 5.1 `handleRemoteMiss` 拆分

当前 `EPBackend::handleRemoteMiss()` 同时做了：

1. requester line 建账
2. `outerTxnPending=true`
3. 同步 `sendReadReq()`
4. 收到 grant 后立刻继续 recall/invalidation/Clear/grant envelope

这必须拆成：

#### `beginRemoteMiss(...)`

负责：

- 计算 `homePa/homeNode/homeSocket`
- requester line 建账
- 分配或更新 per-PA active context
- 设置 `outerReqPending=true`
- 注册 `sendReadReqAsync(..., callback)`
- 立即返回 `PENDING` / `BUSY` / `LOCAL_RETRY`

#### `completeRemoteMiss(ctx, resp)`

负责：

- 从 `ReadResp` 提取 `grantVisibleTick/sentinelVisibleTick`
- 提取 `recallNeeded/pendingInvMask/committedEpoch/authEpoch/grantData`
- 计算 `grantBaseEpoch`
- 更新 `_requesterLines[line_pa]`
- 保存 `_pendingGrantTxns[homePa]`
- 如需 recall / invalidation：进入 `outerCommitPending=true`
- 如需 Clear：继续异步 `sendClearAsync`
- 完成最终 grant 安装和 EP-RNF 通知后，清理 `outerReqPending/outerCommitPending`

### 5.2 回调注册模式

`beginRemoteMiss()` 中不再等待同步返回，而是注册：

```cpp
adapter->sendReadReqAsync(...,
    [this, ctx](bool ok, FailureReason why, const CoherenceMessage *resp) {
        if (!ok) {
            onRemoteMissFailure(ctx, why);
            return;
        }
        completeRemoteMiss(ctx, *resp);
    });
```

同类 request/response 型事务统一改造：

- `sendWritebackReq` → `sendWritebackReqAsync`
- `sendEvictReq` → `sendEvictReqAsync`
- `sendUpgradeReq` → `sendUpgradeReqAsync`
- `sendUpgradeDoneReq` → `sendUpgradeDoneReqAsync`
- `sendClearReq` → `sendClearReqAsync`
- `sendQueryLineMetaReq` → `sendQueryLineMetaReqAsync`

### 5.3 `_ubcc` 删除

用户要求 EPBackend ↔ UBCC request/response 交互全部经 Port-only，因此计划上应删除 EPBackend 对 `_ubcc` 的直接依赖：

1. 移除 `EPBackend` 内用于同步协议调用的 `_ubcc` 成员和直接调用路径
2. 保留必要的 inspection/test hooks 时，改为：
   - 通过 UBAdapter request/response 拿结果
   - 或通过显式 debug-only 查询消息
3. 若 Python test 仍需 `getUBCC()` 观测，应单独标注为临时调试接口，不再参与功能路径

即：**功能面删除 `_ubcc`，调试面若暂留必须隔离。**

---

## 6. Port sync 语义补全

### 6.1 现有缺口

`Port.cc` 当前存在关键缺口：

- `emitSync(curTick)` 会发 `CONTROL_SYNC`
- 但 `recv()` 在收到 `CONTROL_SYNC` 时没有把其 timestamp 变成 `_nextVisibleTick`
- `synced_receive_lower_bound()` 却依赖 `nextVisibleTick()` 计算 safeTick

因此当前接口“像是有 synced_receive”，但实际上 lower-bound 信息没有闭环。

### 6.2 必要修正

当 `Port::recv(visibleTick)` 收到 `CONTROL_SYNC(ts)` 时，必须：

1. 更新该 Port 的 lower-bound 观测值
2. 使 `nextVisibleTick()` 能反映“对端至少已推进到 ts”
3. 不把 `CONTROL_SYNC` 作为普通 payload 消息返回给协议层

建议语义：

```cpp
if (tmp.hdr.type == CONTROL_SYNC) {
    advanceVisibleTick(tmp.hdr.timestamp);
    return nullptr;
}
```

若需避免本地可见性回退，可采用：

```cpp
_nextVisibleTick = std::max(_nextVisibleTick, tmp.hdr.timestamp);
```

### 6.3 与 `synced_receive_lower_bound()` 对接

gem5 侧 UBAdapter 单事件循环不必直接调用全局 helper，但语义必须等价：

```text
emitSync(now)
drain 所有端口上当前可收消息
读取 each-port nextVisibleTick
safeTick = min(all nextVisibleTick)
advanceVisibleTick(now)
```

本轮因为 UBAdapter 只有单个 `_port` 到本地 ubio，可先实现单端口版 safeTick：

- `safeTick = _port->nextVisibleTick()`
- 若尚无对端 sync，safeTick 保守地不超过当前已确认下界

如果后续 gem5 端出现多 Port，则直接扩展为 `synced_receive_lower_bound()` 多端口聚合。

### 6.4 gem5 侧必须主动 emitSync

ubio 已经在主循环中 `emitSync(tick)`；若 gem5 不发，对端只能单边心跳，safeTick 会长期停滞。因此 UBAdapter 的周期事件必须稳定执行：

- `emitSync(curTick())`
- `recv(curTick or safeTick)`
- 更新 `_safeTick`

这一步是让 gem5 真正“参与” synced_receive，而非被动 consumer。

---

## 7. retry / backoff 机制

### 7.1 双层预算

冻结决策要求双层 budget：

#### per-reqId budget

约束单笔事务本身的 send/resend：

- `maxResendPerReqId`
- 只覆盖“未出站”的 resend
- 超过上限后，该 reqId 失败并转交 per-PA deferred retry 或整体失败

#### per-PA budget

约束同一 cache line 的总重试风暴：

- `maxRetryPerPa`
- 统计该 PA 在一个外层事务窗口内产生过多少个新 reqId retry
- 超过上限后直接上报失败，阻止无限重试

### 7.2 1 active + 1 deferred

每个 PA 最多允许：

- **1 个 active**：已建账且可能已出站，等待 response / commit 完成
- **1 个 deferred**：下一次可尝试的新事务描述，不立即出站

规则：

1. 若该 PA 无 active，则新的 begin 请求可直接成为 active
2. 若已有 active，则新 retry 不创建第二个 active
3. 若 active 存在且 deferred 为空，则可存 1 个 deferred
4. 若 active 与 deferred 都存在，再来的同 PA 请求直接拒绝或合并为更强操作

本轮按冻结决策，不做多项排队；核心目标是**止血，防 flood**。

### 7.3 三级 backoff

#### A. send fail → 指数退避（exp backoff）

适用：

- `sendAllocateBuffer()` 失败
- `transportSend()` 失败
- socket 暂时不可发

策略：

- 使用同 reqId resend
- `delay = base << resendCount`
- 受 `maxResendPerReqId` 限制

#### B. BUSY / grant not ready → 固定延迟

适用：

- 远端明确返回 BUSY
- 本地检测到同 PA active 尚未完成

策略：

- 不立即重打
- 进入 deferred slot
- `delay = fixedBusyRetryDelay`

#### C. commitPending → 等完成信号，不走时间退避

适用：

- 已拿到 grant，但 recall / invalidate / Clear 尚未收尾
- 当前处于 `outerCommitPending=true`

策略：

- 后续 retry 不以时间驱动
- 等待 completion signal / callback 清除 commitPending
- commit 完成后再评估 deferred 是否要出站

### 7.4 `_pendingByReqId` / `_requesterLines` 的交互

交互规则：

1. `_pendingByReqId` 负责**运输层事务身份与回调**
2. `_requesterLines[line_pa]` 负责**协议层 requester 状态与 epoch/grantBaseEpoch**
3. retry 生成新 reqId 时：
   - `_pendingByReqId` 新建条目
   - `_requesterLines` 延续同一 requester 语义
   - `grantBaseEpoch` 不丢失
4. resend 不新建 requester 语义，仅对原 pending 做未出站重发

---

## 8. outerTxnPending 两层标志

### 8.1 标志定义

按冻结决策拆成：

#### `outerReqPending(linePa)`

表示：

- 外层 `ReadReq/UpgradeReq/ClearReq/...` 已发出
- 正等待其直接 response

生命周期：

```text
beginRemoteMiss/send*Async 置位
收到并消费对应 response 后清除
```

#### `outerCommitPending(linePa)`

表示：

- grant 已返回
- 但 recall、invalidation、Clear、UpgradeDone 等提交后处理尚未完成

生命周期：

```text
completeRemoteMiss / completeUpgrade... 发现需要收尾动作时置位
所有 commit-side acks 收齐后清除
```

### 8.2 snoop 策略

冻结决策要求：pending 期间 snoop 使用 **1-entry slot**。

具体策略：

1. 若 `outerReqPending || outerCommitPending`：
   - 第一条针对该 PA 的 snoop 放入 1-entry slot
   - 不立即完成
2. 若 slot 已占，再来第二条 snoop：
   - 直接 NACK / retry
   - 不允许静默覆盖已有 slot
3. pending 清除后：
   - 重新调度处理 slot 中的 snoop

该策略牺牲并发度，但能保证 per-PA single-flight，不再与外层事务交错污染状态。

### 8.3 eviction 策略

冻结决策倾向 A：pending 期间禁止 eviction。

本轮方案采用：

- 当 `outerReqPending || outerCommitPending` 时，禁止该 PA 的 eviction
- 不实现 shadow context / clean-only 例外
- 若后续确有容量压力，再单独评估 C 方案（只允许 clean eviction）

原因：

1. 当前重构主目标是去除 busy-poll 与同步化假设
2. 若此时再允许 eviction，会引入 context 生命周期、grant data 保存、Clear tuple 丢失等更高阶风险
3. “先硬阻塞 eviction” 是最小正确集

---

## 9. 实施步骤（6 步、每步可编译测试）

### Step 1：补齐 Port sync 语义

修改文件：

- `framework/Port.hh`
- `framework/Port.cc`

内容：

- 让 `CONTROL_SYNC` 更新 `_nextVisibleTick`
- 明确 `recv()` 遇到 sync 的处理语义
- 校正 `synced_receive_lower_bound()` 与单端口 safeTick 的一致性

验收：

- framework 独立编译通过
- ubio / networksim / gem5 都能链接
- 单元打印确认 `_nextVisibleTick` 随 sync 前进

### Step 2：UBAdapter 引入单事件循环

修改文件：

- `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.hh`
- `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc`

内容：

- 新增 `_responseCheckEvent`
- 新增 `_safeTick`、response 缓冲、pending 表基础字段
- 实现 `emitSync + drain + dispatch + retry` 周期事件
- Port 路径不再允许 busy-poll

验收：

- gem5 编译通过
- TC1 legacy path 不回归
- Port 打开后能持续 emitSync / drain，但业务 API 先不改语义

### Step 3：request/response API 异步化（先从 ReadReq 开始）

修改文件：

- `UBAdapter.hh/.cc`
- `EPBackend.hh/.cc`

内容：

- `sendReadReq` 改为 `sendReadReqAsync`
- `handleRemoteMiss` 拆为 `beginRemoteMiss + completeRemoteMiss`
- 实现 `PendingTxn` 校验、callback、response safeTick 门控

验收：

- 编译通过
- TC1 Port path 仍通过
- TC2 不再出现 poll 导致的请求洪泛

### Step 4：把其余 request/response 型事务统一迁移到 pending-callback

修改文件：

- `UBAdapter.hh/.cc`
- `EPBackend.hh/.cc`
- 可能涉及 `EPRNFController.cc`, `EPSNFController.cc`

内容：

- `Writeback/Evict/Upgrade/UpgradeDone/Clear/QueryLineMeta` 全部异步化
- 删除 Port 路径上对 `_lastResponse` 的同步依赖

验收：

- 编译通过
- 与 Upgrade/Clear 相关 TC 能跑到协议后半段

### Step 5：引入双层 retry/backoff + 两层 pending 标志

修改文件：

- `UBAdapter.hh/.cc`
- `EPBackend.hh/.cc`
- `EPRNFController.hh/.cc`
- `EPSNFController.cc`

内容：

- per-PA + per-reqId budget
- 1 active + 1 deferred
- `outerReqPending + outerCommitPending`
- snoop 1-entry slot + 第二条 NACK
- eviction 禁止

验收：

- TC2 稳定
- 多节点场景不再出现无限重试
- 延迟 snoop / commit 后处理可收敛

### Step 6：删除 EPBackend 功能路径 `_ubcc` 依赖并回归测试

修改文件：

- `EPBackend.hh/.cc`
- `UBCCProtocolIF.hh`（若需精简接口）
- 相关测试/调试辅助

内容：

- 功能路径只走 UBAdapter/Port
- `_ubcc` 若暂留，仅作为 debug inspection，不参与协议执行
- 清理同步假设残留

验收：

- 全量编译通过
- TC1、TC2 必过
- 旧 path TCs 不回归
- Port path 在多节点下无 flood、无 stuck busy-poll

---

## 10. TLOC 估算

| 文件 | 主要改动 | 估算 TLOC |
|---|---|---:|
| `framework/Port.hh` | sync 可见性字段/注释补全 | 10-20 |
| `framework/Port.cc` | `CONTROL_SYNC -> _nextVisibleTick`、recv 语义修正 | 25-45 |
| `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.hh` | Event、PendingTxn、retry 账本、回调类型 | 60-100 |
| `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc` | 单事件循环、drain、safeTick、dispatch、async send APIs | 220-320 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh` | `begin/completeRemoteMiss`、pending context、异步接口声明 | 40-80 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc` | remote miss 拆分、callback 注册、Clear/Upgrade 等异步续执行 | 220-340 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.hh` | 两层 pending 标志、slot/eviction 约束字段 | 20-40 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc` | snoop slot/NACK、pending 窗口行为、completion signal 对接 | 80-140 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc` | 从同步 `handleRemoteMiss` 返回值迁移到 pending/回调触发 | 60-120 |
| `tools/ubio/ubio_main.cc` | 与 gem5 sync 语义对齐、必要诊断增强 | 20-50 |
| 相关测试/日志辅助 | trace、断言、调试开关 | 30-80 |

**总计：约 785 - 1335 TLOC。**

建议按“先最小闭环、后统一铺开”的节奏实施：

1. 先打通 `Port sync + UBAdapter event + ReadReq async`
2. 再迁移其余 request/response
3. 最后做 `_ubcc` 功能路径删除与预算/slot 完整化

---

## 11. 关键风险与对应约束

### 风险 1：safeTick 已接入，但 callback 仍在 drain 阶段误执行

约束：response 先缓存，后 dispatch；禁止在 `recvFromRouter()` 内直接对 response 型事务执行 completion。

### 风险 2：retry 与 resend 混淆，重新制造同 reqId 多次出站

约束：只有 `sendIssued=false` 才允许复用 reqId；一旦成功出站，后续尝试必须新 reqId。

### 风险 3：`outerReqPending` 与 `outerCommitPending` 生命周期泄漏

约束：所有 callback 收尾路径必须成对清除标志；失败、timeout、BUSY、commit 完成都要覆盖。

### 风险 4：旧 `_lastResponse` 同步语义残留

约束：Port 模式下逐步删除所有“发请求后立刻读 `_lastResponse`”路径；仅 legacy router path 暂留。

### 风险 5：snoop slot 与 deferred retry 双重排队造成死锁

约束：一个 PA 同时最多 1 active + 1 deferred + 1 snoop slot；超出即 NACK，不做无界缓存。
