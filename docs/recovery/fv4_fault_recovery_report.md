# FV-4: Fault model — reorder + dup + loss verification

## 0. 审查范围

本报告基于 `grep` + `sed -n` 对以下片段做静态核查：

| 文件 | 代码范围 | 本次关注点 |
|---|---:|---|
| `UBRouter.cc` | 91-223 | 队列、出队、本地/远端投递，天然故障注入锚点 |
| `UBAdapter.cc` | 421-629, 722-809 | `ClearReq` / `RecallResp` / `InvalidateAck` 发送与接收 |
| `UBCCController.cc` | 527-539 | tombstone 命中后的 idempotent grant |
| `UBCCController.cc` | 1058-1110 | `RecallResp` stale/tuple 校验 |
| `UBCCController.cc` | 1196-1273 | `InvalidateAck` stale/dup 校验 |
| `UBCCController.cc` | 1961-2240 | `Clear` tombstone replay、严格 tuple 校验、tombstone 生命周期 |
| `EPBackend.cc` | 599-604, 746-751, 810-814 | outerTxnPending 的前后栅栏与完成点 |

结论先行：

1. **tombstone replay 幂等性：成立。**
2. **`RecallResp` / `InvalidateAck` stale-epoch rejection：成立。**
3. **`Clear` 的“stale”不是 half-range 校验，而是 `tombstone replay + active GRANT_HANDSHAKE tuple match`。**
4. **reorder/dup 的保护总体够用；loss 只有部分闭环，`Clear` 尤其存在 requester 侧未消费失败返回的缺口。**

---

## 1. 结论 1：tombstone replay 是否幂等

### 1.1 `Clear` replay：**是，窗口 `W` 内幂等**

证据链：

1. `UBAdapter::sendClearReq()` 固定携带 `linePa/homeNode/homeSocket/srcNode/epoch/reqId` 发送 `ClearReq`，并同步等待 `ClearResp.accepted`（`UBAdapter.cc:421-456`）。
2. `UBCCController::processClear()` 入口第一件事就是 `checkTombstone(line_pa, epoch, reqId, tsAccepted)`（`UBCCController.cc:1976-1983`）。
3. 首次 `Clear` 成功后，home 侧执行：
   - `commitIntendedResult(entry, *ost)`
   - `retireToTombstone(*ost, true)`
   - `removeOutstanding(line_pa)`
   （`UBCCController.cc:2076-2082`）
4. `retireToTombstone()` 保存的是 `(linePa, baseEpoch, reqId, accepted, expireTick)`（`UBCCController.cc:2193-2208`）。
5. duplicate `Clear` 命中 tombstone 后直接 `return tsAccepted`，不会再次改目录（`UBCCController.cc:1976-1983, 2219-2236`）。
6. `UBRouter::deliverToUbcc()` 再生 `ClearResp` 时原样回显 `epoch/reqId`，只把 `accepted=processClear(...)` 填回（`UBRouter.cc:397-410`）。

**结论：** 对同一 `(PA, epoch, reqId)`，窗口 `W` 内 duplicate `Clear` 返回相同 `accepted`，目录不被二次提交，满足 idempotent replay。

### 1.2 `processOuterRequest()` 的 tombstone 命中：**存在保守型 idempotent grant**

`UBCCController.cc:527-539` 还在 `processOuterRequest()` 中对 `(line_pa, baseEpoch, reqId)` 做了一次 tombstone 检查；命中后返回一个**保守的** idempotent grant：

- `grantVisibleTick = now`
- `sentinelVisibleTick = now`
- `dataSource = HomeMemory`
- grant 类型固定回 `GlobalGrantShared`

这说明实现不仅支持 duplicate `Clear` replay，也试图吸收“请求者因 `ClearAck` 丢失而重入 outer request”的场景；但这里返回的是**保守授权**，不是精确复刻原始 grant 类型，因此它更像“安全吸收”，不是强语义重放。

---

## 2. 结论 2：`Clear / InvalidateAck / RecallResp` 是否拒绝 stale epoch

## 2.1 `RecallResp`：**是，half-range stale reject 成立**

证据：

1. `processRecallResponse()` 先 `normalizeEpoch(responseEpoch)`（`UBCCController.cc:1058-1064`）。
2. 然后调用 `checkEpochForLine()`；若当前 committed epoch 更新，则直接记 `STALE epoch ... REJECTED` 并 `_staleRejectedCount++`（`UBCCController.cc:1077-1083`）。
3. 通过 stale 检查后，还继续校验：
   - outstanding 必须存在且 `opType == RECALL`
   - `targetNode` 必须匹配 owner
   - `reqId` 必须匹配
   （`UBCCController.cc:1086-1109`）
4. 补充读取可见：若 `recallBarrierDone` 已置位，则直接 `return true`，作为 duplicate guard（`UBCCController.cc:1117-1134`）。

**结论：** `RecallResp` 既有 stale-epoch reject，也有 tuple 校验和 duplicate guard。

## 2.2 `InvalidateAck`：**是，half-range stale reject 成立**

证据：

1. `processInvalidationAck()` 先 `normalizeEpoch(responseEpoch)`（`UBCCController.cc:1199-1203`）。
2. 对 `ackNode` 做边界校验（`1205-1211`）。
3. 然后执行 `checkEpochForLine()`；旧 epoch 直接 `REJECTED` 并 `_staleRejectedCount++`（`1222-1228`）。
4. 之后继续做：
   - `!ost` → idempotent true（`1231-1238`）
   - 仅接受 `INVALIDATE` 或 `UPGRADE_PENDING+WAITING_ALL_ACKS`（`1241-1253`）
   - `ackNode` 必须在 `targetMask` 中（`1261-1267`）
   - `ackMask` 已含该 bit → duplicate ignore（`1269-1273` 及后续相邻代码）

**结论：** `InvalidateAck` 的 stale / wrong-stage / duplicate 防护是三者里最完整的。

## 2.3 `Clear`：**是，但实现模型不是 half-range，而是“tombstone + active tuple strict match”**

`Clear` 路径没有调用 `checkEpochForLine()`；它的 stale 处理更严格：

1. 先查 tombstone：
   - 若匹配 `(PA, epoch, reqId)` 且仍在 `W` 内 → replay 旧结果（`UBCCController.cc:1976-1983`）。
2. 若没有 tombstone：
   - 必须存在 live outstanding，且 `opType == GRANT_HANDSHAKE`（`2004-2019`）
   - `epoch` 必须**精确等于** `ost->baseEpoch`（`2022-2035`）
   - `reqId` 必须匹配（`2038-2046`）
   - `srcNode == requesterNode`（`2049-2057`）
   - `stage == WAITING_CLEAR`（`2060-2069`）

因此：

- **窗口 `W` 内** 的旧 `Clear`：不是 reject，而是 replay。
- **窗口 `W` 外** 的旧 `Clear`：不会通过 active tuple strict match，因此会被 drop。

### 2.3.1 重要 caveat

`epoch mismatch` 分支除了 `return false` 外，还会：

- `retireToTombstone(*ost, false)`
- `removeOutstanding(line_pa)`

见 `UBCCController.cc:2028-2035`。

这意味着：如果一个**真正陈旧**的 `Clear` 在 tombstone 已过期后，恰好撞上了一个**新的 live `GRANT_HANDSHAKE`**，当前实现不是单纯“拒绝旧 Clear”，而是会把**当前 live outstanding 也退休掉**。

**结论：** `Clear` 的 stale tuple 不会错误提交目录，但这里有一个恢复语义风险：`stale Clear after W` 可能破坏当前活跃 handshake，而不只是被动拒绝。

---

## 3. 结论 3：三类故障下的现有保护

## 3.1 Reorder

### 3.1.1 天然可乱序点

`UBRouter::sendMessage()` / `drainReadyQueues()` 的行为决定了 UB fabric **不是全局 FIFO**：

- 每个 `(srcNode,srcSocket,dstNode,dstSocket)` 一条独立队列（`UBRouter.cc:54-77, 91-109`）
- ready 消息按 `_pairQueues` 轮询出队（`111-223`）
- 远端再经 `dstRouter->sendMessage(msg, 0)` 二次入队（`194-201`）

所以 `RecallResp` / `InvalidateAck` / `ClearReq` / `ClearResp` 都允许跨队列、跨 hop 乱序。

### 3.1.2 现有保护

| 消息 | reorder 后的保护 | 结论 |
|---|---|---|
| `RecallResp` | `checkEpochForLine` + `opType==RECALL` + owner/reqId 校验 + `recallBarrierDone` idempotent guard | **安全** |
| `InvalidateAck` | `checkEpochForLine` + `targetMask` + `ackMask` 去重 + wrong-stage idempotent | **安全** |
| `ClearReq` | `tombstone replay` 或 `baseEpoch/reqId/requester/stage` 精确匹配 | **安全，但见 §2.3.1 caveat** |
| `ClearResp` | requester 侧只消费当前 `_lastResponse`，home 侧响应由当前 `ClearReq` 直接再生 | **部分安全**，但无 message-id 级去重 |

### 3.1.3 本地 side-effect 栅栏

`EPBackend.cc:599-604, 746-751, 810-814` 还提供了一个**本地 reorder 栅栏**：

- 发起 outer miss 前：`setOuterTxnPending(line_pa, true)`
- 忙返回（`ubccGrant < 0`）时：清掉 pending 并 `signalOuterTxnComplete()`
- `sendClear()` 后：再次清掉 pending 并 `signalOuterTxnComplete()`

这能减少“outer 事务未完成时本地 snoop/后续事务穿透”的风险；但它只覆盖**请求者本地窗口**，不是网络级 reorder 保护。

## 3.2 Duplicate

| 消息 | 保护 | 结论 |
|---|---|---|
| `ClearReq` | tombstone replay identical result | **完整** |
| `InvalidateAck` | `ackMask` 位图去重；`!ost` idempotent | **完整** |
| `RecallResp` | `recallBarrierDone` 为 true 时直接 `return true`；若 outstanding 已转移/消失则因 `!ost || opType!=RECALL` 被拒 | **基本完整** |
| `ClearResp` | `UBAdapter::recvFromRouter()` 仅覆盖 `_lastResponse`，无额外 dedup | **可接受但较薄** |

## 3.3 Loss

| 消息 | 当前实现 | 结论 |
|---|---|---|
| `ClearReq` / `ClearResp` | `UBAdapter::sendClearReq()` 同步等待 `ClearResp`，无响应则 `warn + false`（`UBAdapter.cc:440-456`） | **局部可观测** |
| `RecallResp` | `sendRecallResp()` fire-and-forget；router 对其也不回包（`UBAdapter.cc:459-505`, `UBRouter.cc:414-436`） | **无本地闭环** |
| `InvalidateAck` | `sendInvalidateAck()` fire-and-forget；router 不回包（`UBAdapter.cc:508-546`, `UBRouter.cc:438-444`） | **无本地闭环** |

### 3.3.1 `Clear` loss 的关键缺口

`EPBackend::handleRemoteMiss()` 调用 `sendClear(homePa, homeNode, grantEnv.epoch, grantEnv.reqId)` 后，**没有检查返回值**，随后无条件执行：

- `setOuterTxnPending(line_pa, false)`
- `signalOuterTxnComplete(line_pa)`

见 `EPBackend.cc:808, 810-814`。

而 `sendClear()` 内部明明可以因为 `ClearResp` 丢失/拒绝而返回 `false`（`EPBackend.cc:1805-1858`, `UBAdapter.cc:440-456`）。

**结论：**

- `Clear` loss/拒绝在 adapter 层是可见的；
- 但在 `handleRemoteMiss()` 这条主路径上，失败结果没有参与上层控制流；
- 因而本片段内**没有形成真正的 requester-side retry 闭环**。

这是 FV-4 下最明显的 loss gap。

---

## 4. 按故障类型汇总保护矩阵

| 故障 | `RecallResp` | `InvalidateAck` | `ClearReq/ClearResp` | 总评 |
|---|---|---|---|---|
| reorder | stale + tuple + done-bit | stale + targetMask + ackMask | tombstone / strict tuple match | **可安全吸收** |
| duplicate | `recallBarrierDone` + opType 检查 | `ackMask` 位图去重 | tombstone identical replay | **总体成立** |
| loss | 依赖外层 timeout/retry | 依赖外层 timeout/retry | adapter 可发现，但 EPBackend 主路径未消费失败返回 | **仅部分闭环** |

---

## 5. 故障注入点设计

## 5.1 设计原则

1. **真正的网络故障注入应集中在 `UBRouter.cc`。**
2. `UBAdapter` / `EPBackend` / `UBCCController` 只做：
   - 消息选择条件
   - 计数器/日志观测
   - 结果断言
3. 注入目标优先选：`ClearReq`、`ClearResp`、`RecallResp`、`InvalidateAck`。

## 5.2 具体锚点

### A. enqueue 前注入（最适合 reorder）

**位置：** `UBRouter.cc:98-108`

可做：

- 对目标消息增加 `forcedLatency`
- 对不同 fault campaign 注入 deterministic jitter
- 仅对 `srcNode != dstNode` 开启 inter-node fault

适用：`reorder`

### B. `popReady()` 后、投递前注入（最通用）

**位置：** `UBRouter.cc:127-166`

可做：

- **drop**：直接丢弃消息，不进入 local UBCC
- **dup**：复制当前 `msg` 再次入队
- **hold/release**：暂存一拍，制造“后到先投”

优先消息：`ClearReq`、`RecallResp`、`InvalidateAck`

### C. adapter 本地投递前注入

**位置：** `UBRouter.cc:171-186`

可做：

- drop / dup `ClearResp`
- reorder response 与后续 async notify

优先消息：`ClearResp`

### D. remote hop 前注入

**位置：** `UBRouter.cc:194-201`

可做：

- drop inter-node 消息
- dup 并再次 `dstRouter->sendMessage(msg, 0)`
- 对指定 hop 增加额外延迟

优先消息：`RecallResp`、`InvalidateAck`、`ClearReq`

## 5.3 建议注入接口

建议把 fault spec 设计成：

```cpp
struct UBFaultSpec {
    UBMsgType type;
    int srcNode;
    int dstNode;
    int nthMatch;
    enum Action { Delay, Drop, Duplicate, HoldOneCycle } action;
    Tick extraLatency;
};
```

匹配键至少包含：`type + srcNode + dstNode + reqId + epoch`。

---

## 6. 观测点与验收信号

| 位置 | 期望信号 |
|---|---|
| `UBCCController.cc:1077-1083` | `RecallResp` stale reject + `_staleRejectedCount++` |
| `UBCCController.cc:1222-1228` | `InvalidateAck` stale reject + `_staleRejectedCount++` |
| `UBCCController.cc:1231-1238` | `InvalidateAck` no-outstanding idempotent |
| `UBCCController.cc:1269-1273` | duplicate ack ignore |
| `UBCCController.cc:1976-1983` | tombstone HIT |
| `UBCCController.cc:2022-2069` | `Clear` strict tuple reject |
| `UBCCController.cc:2193-2236` | tombstone create / hit / expire |
| `UBAdapter.cc:440-456` | `sendClearReq: no response` / unexpected response |
| `EPBackend.cc:808, 810-814` | `sendClear()` 失败是否被上层忽略 |

---

## 7. 最终结论

1. **tombstone replay idempotent：成立。** `Clear` 在窗口 `W` 内会 replay identical `accepted`，且不重复提交目录。  
2. **`RecallResp` stale-epoch rejected：成立。** half-range epoch + outstanding/owner/reqId 校验齐全。  
3. **`InvalidateAck` stale-epoch rejected：成立。** 且 duplicate/wrong-stage 防护最强。  
4. **`Clear` stale rejection：成立，但模型是 strict tuple match，不是 half-range compare。**  
5. **主要薄弱点不是 reorder/dup，而是 loss。** `RecallResp/InvalidateAck` 依赖外层 retry；`Clear` 虽能在 adapter 层发现失败，但 `EPBackend::handleRemoteMiss()` 当前没有消费 `sendClear()` 的失败返回。  
6. **额外风险：** `processClear()` 的 `epoch mismatch` 分支会退休当前 live `GRANT_HANDSHAKE`；对“超晚旧 Clear 撞上新 handshake”的场景，恢复语义偏强，需要后续专门验证或修正。  
7. **故障注入应放在 `UBRouter.cc` 四个点：enqueue 前、popReady 后、adapter 投递前、remote hop 前。**
