# transportRecv 架构重构方案

## 1. 问题分析

### 1.1 当前 busy-poll 的问题

`UBAdapter::transportRecv` 用 2M 次 `_port->recv(visible)` 循环等待匹配的响应：

```cpp
for (int i = 0; i < kMaxPollIters; ++i) {
    MemMessage *m = _port->recv(visible);
    if (!m) continue;
    recvFromRouter(*coh);
    if (_lastResponseValid && reqId matches) return true;
}
return false; // timeout
```

**三个致命缺陷**：

1. **重试频率 >> 链路延迟**：一次 `sendReadReq` 调用 poll 200 万次（~2 秒墙钟），如果没有匹配就返回 -1。EPBackend 立刻重试——又是一轮 200 万次 poll + 新的 ReadReq 发送。在实际链路延迟 ~30µs 的情况下，2 秒内能产生数百条重试消息。每一条都经过 ubio→networksim→对端 ubio→gem5 全路径，把 Port 淹没。

2. **阻塞 gem5 事件循环**：gem5 单线程卡在 poll 循环里，其他 gem5 节点的 CPU/协议事件无法推进。node1 的 ReadReq（回收所有权）无法被正确处理，因为它的处理需要 gem5 node1 和 node0 双方的事件推进。

3. **破坏单进程顺序保证**：旧架构中 node0 的整个跨节点流在单次事件中同步完成。Port 路径打破了这一保证——node0 和 node1 的请求在 ZMQ 通道中交织，产生"两个节点互发 ReadReq 等对方释放"的活锁。

### 1.2 为什么旧设计没问题

旧设计的跨节点通信全部在同一 gem5 进程内：
```
gem5 node0 EPBackend → UBAdapter → UBIOModule(sendMessage→enqueue→drainReadyQueues)
  → cross-node queue → UBIOModule(node1) → UBCC(node1) → response
  → UBIOModule(node1).deliverToAdapter → UBAdapter(node0)._lastResponse
```
以上流程全部在**单次 gem5 事件回调中同步完成**。node1 的 CPU 此时还没执行到。

---

## 2. 新设计

### 2.1 核心原则

1. **gem5 永远不 busy-poll**：所有 Port 操作通过 `schedule(Event, tick)` 进行
2. **synced_receive 双边参与**：gem5 的 UBAdapter 和 ubio 的 handleIncoming 都执行 emitSync + recv + safeTick 推进
3. **fire-and-forget + callback**：sendReadReq 不再同步返回 grantInt，而是 fire-and-forget + 注册 callback
4. **EPBackend 适配**：所有 `_ubcc->` 调用改为 UBAdapter → Port 异步路径

### 2.2 gem5 侧事件循环

```
UBAdapter::wakeup()  // 新增，gem5 Event 驱动
  ├── transportRecv()  // drain 当前 safeTick 内的所有消息
  │   └── recvFromRouter(msg)  // 处理或缓存
  ├── emitSync(curTick())  // 向对端 ubio 发送 CONTROL_SYNC
  ├── checkResponseCallbacks()  // 匹配 pending txn，调用 callback
  └── schedule(_wakeupEvent, curTick() + 1000)  // 周期性再调度
```

### 2.3 sendReadReq 改为 fire-and-forget

```cpp
void UBAdapter::sendReadReqAsync(params, callback) {
    // 1. 构建 CoherenceMessage
    // 2. transportSend(msg)  // 立即发送，不等待
    // 3. 存储 PendingTxn{output_pointers, callback, deadline} → _pendingByReqId[reqId]
    // 4. 返回 void（不阻塞）
}
```

callback 在 `checkResponseCallbacks` 中匹配到 ReadResp 时被调用。

### 2.4 EPBackend 适配

```cpp
// 旧：
int grantInt = adapter->sendReadReq(params, &out1, &out2, ...);
ubccGrant = static_cast<UBCC_OuterGrantType>(grantInt);
// 使用 grantInt 继续后续逻辑...

// 新：
adapter->sendReadReqAsync(params, [this, ...](bool ok, int grantInt, ...) {
    if (!ok) { /* handle timeout */ return; }
    ubccGrant = static_cast<UBCC_OuterGrantType>(grantInt);
    // 继续后续逻辑...
});
```

### 2.5 重试控制

PendingTxn 增加 `deadlineTick` 和 `retryCount`：
- `deadlineTick = curTick() + RETRY_TIMEOUT`（例如 100000000 ticks = 100µs 仿真时间）
- 如果 deadline 到期且 callback 未触发，调用 callback(false)
- retryCount 限制在 3 次以内

---

## 3. 实施计划

### Step A：UBAdapter 事件化（~80 LOC）
1. 添加 `EventFunctionWrapper _wakeupEvent`、`_safeTick`
2. 实现 `UBAdapter::wakeup()`：transportRecv + emitSync + checkCallbacks + schedule
3. `transportRecv` 改为非阻塞 drain（保持现有实现，只是不再有 busy-poll 的 "timeout after N iterations"）

### Step B：sendReadReq → fire-and-forget（~100 LOC）
1. `PendingTxn` 扩展：添加 callback、deadline、output pointers
2. `sendReadReqAsync` 实现
3. `checkResponseCallbacks`：遍历 `_pendingByReqId`，匹配 ReadResp，调用 callback

### Step C：EPBackend 回调化（~150 LOC）
1. `handleRemoteMiss`：提取 callback lambda
2. `sendClear`、`sendWriteback`、`sendEvict` 等同理
3. 删除 `_ubcc` 指针

### Step D：synced_receive 双边对接（~50 LOC）
1. gem5 UBAdapter::wakeup 中调用 `emitSync(curTick())`
2. `transportRecv` 使用 safeTick 控制可见性
3. 对齐两端的 tick 粒度

### Step E：测试（~50 LOC）
1. TC1（单节点）验证
2. TC2（跨节点）验证
3. 回归 TC(1-7, 23, 28, 47, 50, 54)

---

## 4. TLOC 估算

| 文件 | 改动 | TLOC |
|------|------|------|
| UBAdapter.hh | PendingTxn 扩展 + wakeup + callback | +40 |
| UBAdapter.cc | transportRecv 去 busy-poll + sendReadReqAsync + checkCallbacks | +100 |
| EPBackend.cc | handleRemoteMiss 等 → callback 化 | +120 |
| EPBackend.hh | 删除 _ubcc | -5 |
| ubio_main.cc | synced_receive 双边对齐 | +30 |

总计 ~290 LOC。

---

## 5. 依赖关系

```
Step A (事件化) → Step D (synced_receive)
                    ↓
Step B (fire-and-forget) → Step C (EPBackend 回调化) → Step E (测试)
```

Step A 和 Step B 可并行。
