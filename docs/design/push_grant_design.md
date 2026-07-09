# Push-Grant 改造设计（消除 Read 路径 pull+retry gap）

> 状态: 待 review | 目标: 把 ReadShared/ReadUnique 的 "BUSY→barrier→requester 重试拉取"
> pull 模型改为 "home 主动 push grant"，根治 RECALL/INVALIDATE 完成后的 ~8.4µs 空等。

## 1. 问题回顾

当前 Read 路径是 **pull+retry**：

1. requester 发 ReadReq → home 发现需 RECALL/INVALIDATE → 返回 BUSY(-1)，**不回任何响应包**
   (`ubio_main.cc:393-398`)。
2. requester 侧 EP-SNF 把请求塞进 `_retryQueue` + `scheduleEvent(Cycles(20000))`
   (`EPSNFController.cc:228-240`)。
3. home 后台完成 RECALL/INVALIDATE，把 grant 备好（`replayArmed=true`）。
4. requester 靠 **定时器到点** 或 `_onResponseWired` 事件重发 ReadReq，命中 replayArmed → 拿 grant。

gap 来源：步骤 3→4 之间 requester **不知道 grant 备好了**。

- **node1-requester（RecallResp 经网络回到 requester）**：之前打的补丁借 RecallResp 到达
  触发 `_onResponseWired`，gap 8417ns→200ns，但仍是 pull（多一次重试往返 = 200ns）。
- **node0-requester==home（grant 在本地 ubio 进程内产生）**：没有网络 ReadResp 落地
  `_readyResponses`，也没有 `_onResponseWired`，**gap 仍是完整的 20000cy（~9µs）**。

## 2. 关键洞察：复用现成的接收/填充机制

pull 路径里，requester 的 grant 最终来自这里（`UBAdapter.cc:326-358`）：

- home 的 ReadResp 到达网络 → `handleResponse` 缓存进 `_readyResponses[(ReadResp,reqId)]`
  (`UBAdapter.cc:1385`)，并对 ReadResp 触发 `_onResponseWired`（`:1390-1398`）唤醒 EP-SNF retry。
- EP-SNF 重跑 `handleRemoteMiss` → `sendReadReq` 发现 `_readyResponses` 有缓存 →
  直接返回 grant + grant data（`:330-357`）→ EP-SNF 把它切成 CompData 灌进 Ruby。

> **结论：只要让 home 在 grant 备好时，主动构造一个完整 ReadResp 投递到 requester 的
> UBAdapter，使它落进 `_readyResponses` 并触发 `_onResponseWired`，就能 100% 复用现有
> 接收→CompData 填充链路，无需新建任何 fill 路径。** 这正是 pull 与 push 的唯一差别：
> ReadResp 是"requester 拉来的"还是"home 推来的"。

这也解释了 node0 为何卡死：requester==home 时 grant 在同进程产生，从不经过"投递 ReadResp
到 UBAdapter"这一步。push 改造顺带把这个不对称也补平了。

## 3. 设计

### 3.1 新增 home→requester 的 grant push

在 `UBCCOutboundIf` 增加一个方法（与现有 `sendRecallReq`/`sendInvalidateReq`/
`sendUpgradeAckNotify` 同构）：

```cpp
// UBCCOutboundIf
virtual bool sendGrantPush(const CoherenceMessage &msg) = 0;
```

`ubio_main.cc` 的 `UbioBackstoreHost` 实现同样走 `routeControlToTarget`
（`ubio_main.cc:317-323`）——它已经能正确路由到"本地 gem5 port"或"跨节点 net"，
因此 node0(本地) 和 node1(跨节点) 两种拓扑自动都覆盖。

### 3.2 grant 消息的构造（用 outstanding，不依赖入站 msg）

push 时没有入站 ReadReq 可借字段，必须从 `grantOst`（GRANT_HANDSHAKE outstanding）取。
所需字段 `processRecallResponse`/INV-DONE 已全部存好：

| ReadResp 字段 | 来源 |
|---|---|
| `h.type` | `ReadResp` |
| `h.dstNode/dstSocket` | `grantOst->requesterNode` / 其 socket |
| `h.requesterNode` | `grantOst->requesterNode` |
| `h.homeNode` | `_nodeId` |
| `h.homeLinePa` | `line_pa` |
| `h.epoch` | `grantOst->baseEpoch`（authEpoch） |
| `h.reqId` | `grantOst->reqId` |
| `h.flags` | `CFLAG_HAS_DATA`（若 `dataValid`） |
| `b.readResp.grantType` | 由 `grantOst->intendedState` 映射（复用 `grantTypeFromIntended`） |
| `b.readResp.dataSource` | `grantOst->dataSource` |
| `b.readResp.grantData` | `grantOst->dataBuf`（若 dataValid） |
| `b.readResp.grantVisibleTick/...` | curTick / 现有语义 |

构造逻辑与 `ubio_main.cc:408-424` 的 pull ReadResp **完全一致**，只是字段源从
入站 `msg` 换成 `grantOst`。建议抽一个 `UBCCController::buildGrantResponse(const
OutstandingRequest&, CoherenceMessage&)` 复用给两条路径，避免重复。

### 3.3 三个 push 落点

在每个 `replayArmed = true` 之后，紧接着 push grant：

1. **RECALL 完成** `UBCCController.cc:1210-1212`
   （ReadUnique 需 recall owner）
2. **INVALIDATE 完成** `UBCCController.cc:1494-1496`
   （ReadUnique 需先失效其它 sharer）
3. **队列 replay** `UBCCController.cc:2735-2738`
   （前一个 Clear 提交后链式唤醒排队 requester）

三处都已持有 `grantOst`（或 `ost`），直接：
```cpp
grantOst->replayArmed = true;   // 保留：作为 push 丢失时的 pull fallback
CoherenceMessage push;
buildGrantResponse(*grantOst, push);
_outbound->sendGrantPush(push);
```

### 3.4 requester 侧接收

push 的 ReadResp 到达 requester UBAdapter，走的路径取决于它被当成"响应"还是"控制消息"：

- **首选（改动最小）**：让 push 的 ReadResp 走和网络 ReadResp **完全相同** 的
  `handleResponse` 路径——即落进 `_readyResponses` + 触发 `_onResponseWired`。
  这样 requester 侧 **零改动**，EP-SNF 被唤醒后重试一次即命中缓存。
  - 注意：这仍保留了"一次本地重试"，但那只是 gem5 内部 1-tick 的 `handleRemoteMiss`
    重跑（`scheduleEvent(Cycles(1))`），**不再有跨节点/ZMQ 往返**，gap → ~0。
- **可选（更彻底，后续再做）**：新增 `handleControlMessage` 的 grant 分支，直接把
  CompData 灌进 Ruby，省掉那一次本地重试。改动更大，先不做。

> 采用首选方案：**requester 侧不改代码**，只需保证 push 的 ReadResp 能进
> `handleResponse`（可能需要在 `isGem5Ingress`/dispatch 里放行 ReadResp 作为 push 入口——
> 待实现时确认 ReadResp 当前是否已被 requester UBAdapter 正常接收）。

## 4. 幂等 / 竞态处理（关键风险）

pull 的 retry 定时器**不删除**，仍作 fallback。因此可能出现：push 的 grant 和
requester 自发的重试**都命中**同一个 replayArmed grant → 重复 grant / 重复 CompData。

处理：
1. **`_readyResponses` 覆盖语义**：push 的 ReadResp 进 `_readyResponses[(ReadResp,reqId)]`，
   若 requester 的重试也从网络收到一份，key 相同会覆盖，`sendReadReq` 命中后 `erase`
   （`UBAdapter.cc:356`）——单次消费，天然幂等。需确认 push 与网络两份不会同时残留。
2. **home 侧去重**：`replayArmed` grant 是单个 outstanding，requester 的后续 Clear 会
   `retireToTombstone`（`UBCCController.cc:2236`）→ 之后重复请求命中 tombstone 被丢弃。
   push 不改变这个提交语义。
3. **push 发送失败**：`sendGrantPush` 返回 false 时，`replayArmed` 仍在 →
   自动退回 pull（定时器兜底）。**这是保留 fallback 的意义。**

## 5. 不改动的部分（明确边界）

- **直接命中路径**（G_S 读、无 recall/invalidate）：现在就是一次同步 RPC 回 ReadResp
  （`ubio_main.cc:408`），无 gap，**不动**。
- **Upgrade 路径**：已是事件/push 驱动（UpgradeAckNotify + `onUpgradeRespArrived`），**不动**。
- **Writeback/Evict/Clear**：本次不改，仍 pull（gap 影响小，后续单独评估）。
- **EP-SNF 的 `_retryQueue` + 20000cy 定时器**：**保留**为 fallback。
- 之前的两处改动（`EPSNFController.cc:353` → Cycles(1)；agent 的 RecallResp wake）保留，
  它们与 push 不冲突；push 生效后 RecallResp-wake 那条补丁基本不再触发（可后续清理）。

## 6. 验证计划

1. Docker 内重编 gem5 + native 模块。
2. 跑 TC3（Docker 内），确认 PASS。
3. 从 trace 提取 `rid=...937937`(node1-req) 和 `rid=2`(node0-req) 两条：
   确认 **两个方向** RECALL/grant 后到 ReadResp 的 gap 都降到 ~0（≤1 cycle 级别，
   不再有 200ns 重试往返，也不再有 9µs 定时器空等）。
4. 核心回归：TC1 2 3 5 6 11 16（+ upgrade/invalidate 相关 TC 如 25/53）全 PASS。
5. 重新生成 HTML，确认 RECALL 读 chain 时长从 ~2µs 进一步下降，且无透明大块。

## 7. 待实现时需确认的开放问题

- Q1: requester UBAdapter 当前是否已能接收"非本节点发起的 ReadResp"？（push 的 ReadResp
  dstNode=requester，需确认 dispatch 不会因"无匹配 inflight reqId"而丢弃——node0
  requester==home 时尤其要验证。）
- Q2: `grantOst` 在 push 时 dataValid 的两种来源（RecallBuffer vs HomeMemory）数据是否
  都已就位？（HomeMemory 情况下 grantData 可能需从 backstore 读，确认 push 时机数据已 ready。）
- Q3: socket 号：`requesterNode` 的目标 socket 从哪个字段取（`grantOst` 是否存了
  requesterSocket）？

## 8. 形式化验证（verification/）影响分析

`verification/tla/` 是 TLA+/TLC 模型检查套件，`run_tlc.sh` 跑各 `.cfg`。核心 spec：
`ubcc_protocol_core.tla`（单 PA 目录状态机）+ 派生的 multi_pa/multi_socket/liveness/
transport_faults 变体。

### 8.1 关键判断：push 是"交付机制"，模型抽象掉了传输层 → 安全不变式不受影响

`ubcc_protocol_core.tla` 只建模 UBCC **目录状态机**（`dir/ost/tombstone/commitLog/
epochLog`），**显式抽象掉了传输/消息层**（core 头注释 + `ubcc_protocol.tla:30
NetWellFormed == TRUE`）。也就是说：

- grant 是 pull 还是 push，是"requester 如何得知 grant 已就绪"的**传输层**问题；
- 模型里对应的动作 `RecallToGrant`（`core:241`）、`BarrierAck`(INVALIDATE 分支
  `core:375-380`)只关心 `ost` 从 RECALL/INVALIDATE **转成 GRANT_HANDSHAKE** 这个
  状态迁移本身，不关心 requester 怎么被通知。

→ **push 改造不改变任何被建模的状态迁移、epoch/commit 语义**，因此下列安全不变式
天然保持，无需改 spec：`SharersCanonical`、`EpochMonotonic`、`NoDoubleCommit`、
`ReserveNotCommit`、`SingleDirtyHolder`、`StableNodeConsistency`。

### 8.2 需要复核 / 可能微调的点

1. **`replayArmed` 语义在模型里的处置**（**重点**）：
   - `RecallToGrant`（`core:246-248`）把 RECALL→GRANT 时设 `replayArmed = FALSE`；
     而 `BarrierAck` INVALIDATE 分支（`core:379`）设 `replayArmed = TRUE`。两者不一致，
     本来就是"模型没把 replayArmed 当关键状态"的信号。
   - push 改造后，`replayArmed` 从"requester 拉取的钥匙"降级为"push 失败的 fallback 标记"。
     **模型层面 push 不依赖 replayArmed**，所以无需为 push 在 spec 里加动作。但建议：
     加一条**不变式**明确"grant 就绪（GRANT_HANDSHAKE/WAITING_CLEAR）后，无论 replayArmed
     真假，最终都能 ClearCommit 清空"——这其实已被 `OstEventuallyClears`(P2) 覆盖。
   - 结论：**不需要新增 push 动作**；模型对交付方式不敏感，pull/push 都归约到同一个
     "ost 转 GRANT_HANDSHAKE → ClearCommit" 路径。

2. **liveness 属性 P1 `RecallProgress` / P2 `OstEventuallyClears`**：
   - push 只会让"grant 就绪→requester 认领"**更快**（去掉 pull 重试延迟），不改变
     "最终会不会清空"这个 liveness 结论。属性仍成立。
   - `fv7_recall_path_report.md §8.4` 明确指出 "RecallResp 是 fire-and-forget，无 retry，
     丢失则 recall 死锁"。push 不改变 RecallResp 这段（owner→home），只改变 home→requester
     的 grant 交付。**但要新增说明**：push 的 grant 若丢失，`replayArmed` fallback +
     EP-SNF retry 定时器仍能兜底（这正是保留定时器的价值），需在报告里记一笔，避免
     "又引入一个 fire-and-forget 单点"的疑虑。

3. **`transport_faults` 变体**：如果它建模了 grant/ReadResp 消息的丢失/重复，push 新增
   了一条 home→requester 的 grant 消息路径，可能需要在 fault 模型里补一个"push grant
   丢失→fallback 到 pull retry"的场景，验证幂等（§4）。**这是最可能需要实际改 spec 的地方**，
   待确认 transport_faults.tla 是否建模到 grant 交付层。

### 8.3 验证套件的执行计划（纳入总验证）

改完代码后，除 E2E 回归外，还需：

1. 跑全部 TLC 配置确认**安全属性仍 PASS**（不变式不受 push 影响，应无回归）：
   ```bash
   cd verification/tla
   ./run_tlc.sh ubcc_protocol.tla ubcc_config.cfg
   ./run_tlc.sh ubcc_protocol.tla ubcc_multi_pa.cfg
   ./run_tlc.sh ubcc_protocol.tla ubcc_multi_socket.cfg
   ./run_tlc.sh ubcc_protocol.tla ubcc_liveness.cfg          # P1-P4 liveness
   ./run_tlc.sh ubcc_transport_faults.tla ubcc_transport_faults.cfg
   ./run_tlc.sh ubcc_transport_faults.tla ubcc_transport_faults_liveness.cfg
   ```
2. 若 transport_faults 建模到 grant 交付层：新增/微调"push grant 丢失→pull fallback"
   场景，重跑并确认 liveness 仍 PASS（fallback 保证不 wedge）。
3. 更新受影响文档：`fv7_recall_path_report.md`（补 §hop 19-20 的 push 交付变体 +
   §8 加一条 push-grant fallback 说明）、`fv3_outstanding_lifecycle.md`（replayArmed
   语义从"pull 钥匙"改为"fallback 标记"）、`CONSOLIDATED_REPORT.md` 顶层记一笔。

### 8.4 结论

- **安全不变式**：push 不触碰被建模的状态机 → 预期零回归，只需重跑确认。
- **liveness**：push 让完成更快、且保留 pull fallback → 属性仍成立，只需重跑确认。
- **唯一可能实际改 spec 的**：`transport_faults` 若建模 grant 交付层，需补 push-grant
  丢失的 fault + fallback 场景。
- **文档需更新**：fv7 / fv3 / CONSOLIDATED_REPORT 记录 pull→push 的交付语义变化。
