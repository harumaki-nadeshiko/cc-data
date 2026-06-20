# FV-4: Fault model — reorder + dup + loss verification

## 0. 范围与方法

- 本报告基于 `grep` + `sed -n` 对以下代码做静态路径审查：
  - `UBCCController.cc:1058-1110, 1196-1307, 1961-2240`
  - `UBRouter.cc:91-223`
  - `UBAdapter.cc:421-629`
- 结论分两层：
  - **M1 / inter-node**：允许 `reorder + dup + loss`
  - **M2 / intra-node**：只允许 `reorder`，**不允许** `dup/loss`

---

## 1. 三类故障 × 易受影响消息 × 现有保护

### 1.1 Reorder

| 消息类型 | 易受影响点 | 现有保护 | 结论 |
|---|---|---|---|
| `RecallResp` | `UBRouter::drainReadyQueues()` 按 `_pairQueues` 逐队列出队，不提供全局 FIFO；旧 `RecallResp` 可晚于新事务到达（`UBRouter.cc:116-223`） | `checkEpochForLine()` 半范围 epoch 拒绝（`UBCCController.cc:1077-1083, 1451-1463`）；还校验 outstanding/opType/owner/reqId（`1086-1109`） | **安全性有保护**；但**无显式 duplicate/stage 去重**，重复同 tuple 在 outstanding 仍存活时可再次进入处理 |
| `InvalidateAck` | 同上，ack 可乱序晚到 | stale epoch 拒绝（`1219-1225`）；`!ost` 幂等（`1230-1236`）；错误阶段幂等（`1244-1251`）；ack bit 去重（`1267-1272`） | **保护最完整** |
| `ClearReq` | 旧 `Clear` 可晚于新 grant 到达 | 先查 tombstone（`1976-1983, 2219-2236`）；否则严格校验 `baseEpoch/reqId/srcNode/stage`（`2022-2069`） | **窗口 W 内安全且幂等**；`W` 外只能按 stale/drop 处理 |
| `ClearResp` | `ClearReq`/`ClearResp` 往返都经过 router 队列，可乱序返回 | `ClearResp` 内容完全由当前 `ClearReq` tuple + `accepted` 再生（`UBRouter.cc:400-413`） | 只要 home 侧判定正确，返回语义稳定 |

### 1.2 Duplicate

| 消息类型 | 现有保护 | 结论 |
|---|---|---|
| `ClearReq` | 首次完成后 `retireToTombstone()` 记录 `(linePa, baseEpoch, reqId, accepted, expire)`（`2197-2208`）；重复 `Clear` 命中 `checkTombstone()` 直接返回同一 `accepted`（`1976-1983, 2219-2236`） | **已覆盖** |
| `InvalidateAck` | `effAckMask` 已置位时直接 `duplicate ack ... ignoring`（`1267-1272`）；若 outstanding 已清除也返回 idempotent（`1230-1236`） | **已覆盖** |
| `RecallResp` | 仅有 outstanding/opType/owner/reqId 校验（`1086-1109`）；未见显式“duplicate resp”分支 | **部分覆盖**：事务结束后重复包会因 `!ost` 被拒；事务未清理前缺少显式 dedup |

### 1.3 Loss

| 消息类型 | 现有保护 | 结论 |
|---|---|---|
| `ClearReq` / `ClearResp` | `sendClearReq()` 要求同步拿到 `ClearResp`，否则返回 false（`UBAdapter.cc:440-456`）；若首次 `Clear` 已在 home 提交，重试同 tuple 可由 tombstone 回放同一结果（`1976-1983`） | **部分覆盖**：语义上支持 retry+tombstone replay；但重试循环不在本次片段内闭合 |
| `RecallResp` | 发送端是 fire-and-forget（`UBAdapter.cc:503-505`）；router 本地交付也无响应（`UBRouter.cc:158-160, 417-436`） | **安全性依赖 stale reject，活性依赖外层 timeout/retry**；本片段内无自闭环恢复 |
| `InvalidateAck` | 发送端 fire-and-forget（`UBAdapter.cc:543-545`）；router 也无响应（`UBRouter.cc:158-160, 438-443`） | **安全性有幂等保护，活性仍依赖外层 retry** |

---

## 2. Tombstone replay 幂等性验证

结论：**成立**。`W` 窗口内 duplicate `Clear` 会返回同一 `ClearAck` 语义结果。

证明链路：

1. `ClearReq` 由 adapter 携带固定 tuple：`(homeLinePa, epoch, reqId, requesterNode)` 发送（`UBAdapter.cc:421-441`）。
2. 首次匹配成功的 `Clear` 在 `processClear()` 中：
   - 提交 intended result（`2076-2078`）
   - `retireToTombstone(*ost, true)`（`2080-2082`）
3. `retireToTombstone()` 保存的是 **`baseEpoch` + `reqId` + accepted**（`2199-2205`）。
4. duplicate `Clear` 在 `processClear()` 入口先查 tombstone；命中后直接 `return tsAccepted`（`1976-1983`）。
5. `UBRouter::deliverToUbcc()` 再生 `ClearResp`，其字段为：
   - `epoch = msg.h.epoch`
   - `reqId = msg.h.reqId`
   - `accepted = processClear(...)` 返回值（`UBRouter.cc:400-413`）
6. `UBAdapter::sendClearReq()` 最终只消费这个 `accepted`（`449-456`）。

因此，**同一 `(PA, epoch, reqId)` 的 duplicate Clear 在 W 内会得到相同的 `accepted`，且 `epoch/reqId` 也被原样回显**。

补充：

- 若首次 `Clear` 因 epoch mismatch 被拒，代码会 `retireToTombstone(*ost, false)`（`2031-2034`）。
- 因而该 tuple 的后续 duplicate 也会稳定回放 `accepted=false`。

---

## 3. stale-epoch rejection 验证

### 3.1 通用 epoch 比较

- `checkEpochForLine()` 使用 half-range 比较：若 `committed epoch` 比消息 epoch 更新，则判 stale（`1451-1463`）。
- `commitIntendedResult()` 仅在 `Clear` 提交点把目录 epoch 前推到 `reservedEpoch`（`2175`）。
- 因此，**旧事务的 `RecallResp/InvalidateAck` 在新 `Clear` 提交后会自动变成 stale**。

### 3.2 分消息验证

| 消息 | 拒绝点 | 结果 |
|---|---|---|
| `RecallResp` | `processRecallResponse()` → `!checkEpochForLine()`（`1077-1083`） | **旧 epoch 被拒绝** |
| `InvalidateAck` | `processInvalidationAck()` → `!checkEpochForLine()`（`1219-1225`） | **旧 epoch 被拒绝** |
| `ClearReq` | 特殊处理：不是 half-range，而是要求 `epoch == ost->baseEpoch`（`2022-2035`）；若命中 tombstone 则视为 duplicate replay，不算 stale reject（`1976-1983`） | **旧 epoch 会被拒绝或回放旧结果** |

结论：

- 对 `RecallResp` / `InvalidateAck`：**old epoch rejected** 已静态成立。
- 对 `ClearReq`：语义更严格，属于 **tuple match / tombstone replay** 模型，而非单纯 half-range reject。

---

## 4. 故障注入插桩点设计（file:line）

> 依据 `intra_inter_verification_plan.md:503`，**M1 的唯一合法网络故障注入锚点是 `UBRouter.cc`**。`UBAdapter/UBCCController` 只作为消息选择与结果观测点，不做真正的 M1 网络注入。

### 4.1 Reorder 注入

| 位置 | 用法 | 适用 |
|---|---|---|
| `UBRouter.cc:98-108` | 在 `q->enqueue(msg, curTick(), lat)` 前后对选中消息增加测试性 latency/jitter，制造 ready 次序变化 | **M1/M2** |
| `UBRouter.cc:127-130` | 在 `popReady()` 前增加 hold/release 逻辑，允许把“先 ready 的消息”暂存一拍再放出 | **M1/M2** |
| `UBRouter.cc:211-221` | 对 pending queue 延迟下一次 drain，扩大乱序窗口 | **M1/M2** |

建议目标消息：`ClearReq`、`RecallResp`、`InvalidateAck`。

### 4.2 Duplicate 注入

| 位置 | 用法 | 适用 |
|---|---|---|
| `UBRouter.cc:130-166` | 对将要本地投递给 UBCC 的消息 clone 一份重新入队，制造 duplicate `RecallResp` / `InvalidateAck` / `ClearReq` | **仅 M1** |
| `UBRouter.cc:171-186` | 对投递给 adapter 的 `ClearResp` 也可 clone，验证 requester 端重入/重复响应处理 | **仅 M1** |
| `UBRouter.cc:194-201` | 对 remote delivery 前的 inter-node 消息 clone 并再次 `dstRouter->sendMessage(msg, 0)` | **仅 M1** |

### 4.3 Loss 注入

| 位置 | 用法 | 适用 |
|---|---|---|
| `UBRouter.cc:139-166` | 在本地投递给 UBCC 前直接 drop，验证 `RecallResp` / `InvalidateAck` / `ClearReq` 丢失 | **仅 M1** |
| `UBRouter.cc:171-186` | 在投递给 adapter 前 drop `ClearResp`，验证 requester 侧 retry 路径 | **仅 M1** |
| `UBRouter.cc:194-205` | 在 remote delivery 前 drop inter-node 消息，模拟链路丢包 | **仅 M1** |

### 4.4 观测/校验点

| 位置 | 观察内容 |
|---|---|
| `UBCCController.cc:1077-1083` | `RecallResp` stale reject |
| `UBCCController.cc:1219-1225` | `InvalidateAck` stale reject |
| `UBCCController.cc:1230-1236` | `InvalidateAck` no-outstanding idempotent |
| `UBCCController.cc:1267-1272` | `InvalidateAck` duplicate ignore |
| `UBCCController.cc:1976-1983` | `Clear` tombstone replay 命中 |
| `UBCCController.cc:2022-2035` | `Clear` epoch mismatch / stale drop |
| `UBCCController.cc:2197-2236` | tombstone 退休与命中 |
| `UBAdapter.cc:440-456` | `ClearResp` 丢失/异常类型对 requester 的直接影响 |
| `UBAdapter.cc:479-505, 526-545` | fire-and-forget `RecallResp/InvalidateAck` 发包边界 |

---

## 5. M1 / M2 适用范围说明

- **M1（inter-node）**：在 `UBRouter.cc` 上允许对 `ClearReq/ClearResp/RecallResp/InvalidateAck` 做 `reorder + dup + loss`。
- **M2（intra-node）**：仍可使用 `UBRouter.cc:98-108, 127-130, 211-221` 做 **reorder-only**；必须显式禁止 duplicate/loss 注入。
- 实现建议：以 `msg.h.srcNode != msg.h.dstNode` 作为 M1 过滤条件；`srcNode == dstNode` 时只允许 reorder 钩子生效。

---

## 6. 总结

1. **duplicate Clear within W → same ClearAck**：已静态成立。
2. **old-epoch RecallResp / InvalidateAck rejected**：已静态成立。
3. `InvalidateAck` 的 duplicate/stale 防护最完整。 
4. `Clear` 的去重依赖 tombstone，设计完整。 
5. **残余薄弱点**：`RecallResp` 缺少显式 duplicate/stage 去重；`RecallResp/InvalidateAck` 的 loss 恢复不在本次边界代码内闭合，需依赖外层 timeout/retry。 
6. 故障注入应集中在 `UBRouter.cc`；其中 **M2 只做 reorder，M1 才做 reorder+dup+loss**。
