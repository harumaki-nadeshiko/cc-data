# FV-7 Recall data path verification

> 核查范围：`EPBackend.hh:108-138`、`EPBackend.cc:500-816,1154-1315`、`EPRNFController.cc:470-547,899-943,1088-1180`、`UBAdapter.cc:462-590,762-783`、`UBRouter.cc:417-435`、`UBCCController.cc:1058-1154`、`CHI-cache-actions.sm:2436-2512`。
> 辅助交叉核查：`UBCCController.cc:721-746,839-868,2107-2124`、`UBRouter.cc:248-313`、`EPSNFController.cc:90-111,257-280`、`CHI-cache-actions.sm:2366-2388`。

## 1. 结论先行

- **主链路存在且闭环完整**：`OuterRecallMsg -> startReadShared/Unique -> CompData -> PendingChiTxn.recallDataBlk -> EPBackend callback capture -> OuterRecallResponse -> UBCC outstanding.dataBuf -> GRANT_HANDSHAKE -> grant CompData`。
- **ReadShared / ReadUnique 两条 recall 路径都能把数据送回 home UBCC，并在后续 grant 中再次发给 requester。**
- **但存在 3 个明确风险点**：
  1. `EPRNFController::recvDataMsg()` 对多 beat `CompData` 只做 `recallDataBlk = msg->getdataBlk()`，**没有按 `bitMask/offset` 组装**，高概率会只保留最后一拍数据。
  2. `startReadShared/startReadUnique` 的**发送失败快路径不会清空旧 `_recallCaptureDataValid`**；随后 `sendRecallResponse()` 可能把**旧 recall 的 stale data 写回 home memory**。
  3. `ReadUnique` 注释声明应等待 `Comp_UC` 完成，但实现却在最后一个 `CompData` beat 就 `finishChiTxn()`，存在**完成时序前冲**。

---

## 2. 全链路追踪

### 2.1 requester 侧触发 recall：`EPBackend::handleRemoteMiss`

`EPBackend.cc:624-667`：home UBCC 返回 `recallNeeded=true` 时，请求侧 EPBackend 构造 `OuterRecallMsg`：

- `linePa = homePa`
- `ownerLocalPa = buildDsmPA(recallOwnerNode, homeNode, offset)`
- `ownerNode = recallOwnerNode`
- `homeNode = homeNode`
- `epoch = committedEpoch`
- `reqId = reqIdVal`
- `isReadRequest = (reqType == GlobalReadShared)`
- `dataNeeded = true`

然后通过 `getUBAdapter(0)->sendRecallReqToOwner(...)` 发给 owner 节点。

### 2.2 wire 形态：`OuterRecallMsg / OuterRecallResponse`

`EPBackend.hh:108-138` 定义两类对象：

- `OuterRecallMsg`：带 `linePa / ownerLocalPa / ownerNode / homeNode / epoch / reqId / isReadRequest / dataNeeded`
- `OuterRecallResponse`：带 `dataPayload` 与 `hasDataPayload`

这里已经明确：**协议层允许 recall 返回真实 64B cache line。**

### 2.3 owner 侧入口：`UBAdapter -> EPBackend::handleRecallRequest`

- `UBAdapter.cc:560-590`：`RecallReq` 被重建成 `OuterRecallMsg`，转交 `_backend->handleRecallRequest(recallMsg)`。
- `EPBackend.cc:1154-1268`：`handleRecallRequest()` 先校验 `recallMsg.ownerNode == _nodeId`，然后：
  - `isReadRequest=true`：本地 requester 状态先降到 `R_S`，调用 `startReadShared(ownerLocalPa, cb)`。
  - `isReadRequest=false`：本地 requester 状态先置 `R_I`，调用 `startReadUnique(ownerLocalPa, cb)`。

回调统一构造 `OuterRecallResponse`：

- `ackReceived = success`
- `dataReturned = dataNeeded && success && _recallCaptureDataValid`
- 若 `_recallCaptureDataValid` 为真，则 `dataPayload = _recallCaptureDataBlock`、`hasDataPayload = true`

### 2.4 owner 侧 CHI recall 发起：`startReadShared / startReadUnique`

`EPRNFController.cc:1088-1180`：两条路径都会创建 `PendingChiTxn`，关键字段一致：

- `beatsExpected = dataMsgsPerLine`
- `beatsReceived = 0`
- `recallDataValid = false`
- `onComplete = cb`

差异：

- `ReadShared`：`op = ReadShared`，`proxyOp = NoProxyOp`，发 `CHIRequestType_ReadShared`
- `ReadUnique`：`op = ReadUnique`，`proxyOp = RecallUnique`，发 `CHIRequestType_ReadUnique`

### 2.5 CHI `CompData` 捕获：`recvDataMsg`

`EPRNFController.cc:470-547`：接受 `CompData_I/SC/UC/UD_PD/SD_PD`。命中 pending txn 后，当前代码执行：

- `beatsReceived++`
- `recallDataBlk = msg->getdataBlk()`
- `recallDataValid = true`

最后一个 beat 到达时，两条路径都会：

- 给 responder 发一个 `CompAck`
- `finishChiTxn(linePa, true)`

### 2.6 `recallDataBlk -> backend capture -> callback`

`EPRNFController.cc:899-943`：`finishChiTxn()` 在擦除 txn 之前：

- 若 `recallDataValid`，调用 `_backend->setRecallCaptureData(txn.recallDataBlk, true)`
- 否则调用 `_backend->setRecallCaptureData(DataBlock(cacheLineSize), false)` 清空有效位

之后才：

- `_pendingChiTxns.erase(txnIt)`
- 调用原始 callback

因此真正的 callback 稳定点不是 `callbackPayloadStable`，而是 **`EPBackend::_recallCaptureDataBlock/_recallCaptureDataValid`**。

### 2.7 callback -> `OuterRecallResponse` -> wire

`EPBackend.cc:1271-1308`：`sendRecallResponse()` 做两件事：

1. 若 `response.hasDataPayload`，先把 `response.dataPayload` 写入 home 节点 `HomeMemoryService`
2. 调 `UBAdapter::sendRecallResp(...)`

`UBAdapter.cc:462-502`：

- `dataReturned` 置 `UB_FLAG_DATA_RETURNED`
- 仅当 `dataBlk && dataReturned` 时才置 `UB_FLAG_HAS_DATA` 并 `memcpy(..., 64)` 到 `req.b.recallResp.data`

### 2.8 wire -> home UBCC：`UBRouter -> UBCCController::processRecallResponse`

- `UBRouter.cc:417-435`：若 `RecallResp` 带 `UB_FLAG_HAS_DATA`，就重建 `DataBlock dataBlk(64)` 并传给 `_localUbcc->processRecallResponse(...)`
- `UBCCController.cc:1058-1154`：
  - 校验 directory / epoch / outstanding / ownerNode / reqId
  - `ost->recallBarrierDone = true`
  - `ost->stage = DONE`
  - 若 `dataBlk && dataReceived`，执行 `memcpy(ost->dataBuf, dataBlk->getData(0,64), 64)` 且 `ost->dataValid = true`

**结论**：home UBCC 的 recall 数据落点是 `OutstandingRequest::dataBuf`。

### 2.9 `UBCC.dataBuf -> GRANT_HANDSHAKE -> requester grant`

这一步不在用户给的 1058-1154 片段里，但若不补这一段，就无法证明“UBCC.dataBuf -> grant”。

#### (a) `RECALL -> GRANT_HANDSHAKE`

`UBCCController.cc:721-746`：当重试请求看到同一 PA 上 `RECALL` 已 DONE：

- 删除旧 `RECALL` outstanding
- 新建 `GRANT_HANDSHAKE`
- `grantOreq->dataValid = recallData.dataValid`
- 若有效，`memcpy(grantOreq->dataBuf, recallData.dataBuf, 64)`
- `grantOreq->dataSource = RecallBuffer`

即：**UBCC.dataBuf 被明确搬到 grant outstanding。**

#### (b) `GRANT_HANDSHAKE -> router read response`

`UBRouter.cc:248-313`：当 `dataSource == RecallBuffer` 时：

- `hasGrantData = _localUbcc->copyOutstandingGrantData(msg.h.homeLinePa, grantData)`
- 若成功，`memcpy(response.b.readResp.grantData, grantData.getData(0,64), 64)`

#### (c) requester 侧 EPBackend 再落地为 grant data

`EPBackend.cc:801-910`：

- 若 `dataSource == RecallBuffer`，先 `setRecallCaptureData(routedGrantData, routedGrantDataValid)`
- `populateGrantData(homePa, RecallBuffer)` 将 `_recallCaptureDataBlock` 消费到 `_lastGrantDataBlock`

#### (d) requester 最终发 `CompData`

`EPSNFController.cc:90-111,257-280`：真正给 requester 侧 CPU/L1 的 `CompData` 数据来自 `_backend->lastGrantData()`。

**因此，`UBCC.dataBuf -> grant` 这段链路是成立的。**

---

## 3. ReadShared / ReadUnique 差异

| 路径 | owner 本地账本 | 发出的 CHI 请求 | 数据完成点 | 语义目标 |
|---|---|---|---|---|
| ReadShared recall | `R_S` | `ReadShared` | 最后一个 `CompData` beat | owner 降级为 shared |
| ReadUnique recall | `R_I` | `ReadUnique + RecallUnique` | **当前实现也是最后一个 `CompData` beat** | owner 应失效为 invalid |

### 3.1 ReadShared

- `EPBackend.cc:1218-1238`
- `EPRNFController.cc:1088-1122,470-509`

路径一致：发 `ReadShared`，数据 beat 到齐后 `CompAck + finishChiTxn()`，然后 callback 发 `RecallResponse`。

### 3.2 ReadUnique

- `EPBackend.cc:1241-1261`
- `EPRNFController.cc:1125-1180,397-458,521-541`

代码注释说：`ReadUnique` 应是“`CompData` 先到，`Comp_UC` 后到，`Comp_UC` 才是 completion token”。

但实际实现是：

- `recvDataMsg()` 在最后一个 `CompData` beat 就直接 `finishChiTxn()`
- `recvResponseMsg()` 收到 `Comp_UC` 时，如果 txn 还是 `ReadUnique`，直接 `return true`，不再触发完成

因此 **ReadUnique 的数据返回是通的，但完成时序并没有真的被 `Comp_UC` 门控。**

---

## 4. `CHI-cache-actions.sm` 对数据拼装语义的启示

用户指定的 `CHI-cache-actions.sm:2436-2512` 片段显示：

- `UpdateDirState_FromSnpDataResp` 只会在 `tbe.expected_snp_resp.hasReceivedData()` 后执行
- 且先 `assert(tbe.dataBlkValid.isFull())`

再往前看 `CHI-cache-actions.sm:2366-2388`：

- HN-F 对 `SnpRespData_*` 是通过 `tbe.dataBlk.copyPartial(in_msg.dataBlk, in_msg.bitMask)`
- 再用 `tbe.dataBlkValid.orMask(in_msg.bitMask)` 累积整行

这说明 **CHI 上游的标准模式是“按 beat/bitMask 拼装整行”**，不是简单覆盖。

而 `EPRNFController::recvDataMsg()` 对 recall 数据没有采用同样的 `copyPartial + bitMask` 组装方式，只是简单 `recallDataBlk = msg->getdataBlk()`。这正是本次核查发现的最大数据丢失风险。

---

## 5. 数据丢失 / 污染风险清单

### R1. 多 beat 覆盖风险（高，且很可能是真问题）

**位置**：`EPRNFController.cc:494-499,523-528`

当前代码：

```cpp
it->second.recallDataBlk = msg->getdataBlk();
it->second.recallDataValid = true;
```

问题：没有使用 `bitMask`，没有 `copyPartial()`，也没有 offset merge。

为什么这很危险：

- `EPSNFController.cc:97-110` 构造 `CompData` 时就是“每拍只填 chunk + WriteMask”
- `CHI-cache-actions.sm:2366-2388` 对 snoop data 也是按 `bitMask` 拼装

所以若 recall `CompData` 同样是分拍 partial payload，`recallDataBlk` 最终只会保留**最后一拍**，前面 56B/48B/... 被覆盖或保留为零。

### R2. 发送失败快路径的 stale recall data 污染（高）

**相关位置**：

- `EPRNFController.cc:1105-1122,1169-1180`：`sendChiRequest()` 失败时直接 `onComplete(false)`
- `EPBackend.cc:1225-1237,1248-1260`：callback 只要 `_recallCaptureDataValid` 仍为真，就会设置 `hasDataPayload=true`
- `EPBackend.cc:1284-1294`：`sendRecallResponse()` 只看 `hasDataPayload` 就会把 `dataPayload` 安装到 home memory

问题链条：

1. 新 recall 发起失败，`finishChiTxn()` 没有机会运行
2. `_recallCaptureDataValid` 可能仍保留上一次 recall 的旧值
3. callback 因旧 valid 位把 stale `_recallCaptureDataBlock` 填进 `OuterRecallResponse`
4. 虽然 `UBAdapter::sendRecallResp()` 不会把这份 stale data 发到 UB wire（因为 `dataReturned=false`），但 `sendRecallResponse()` 前面的 `HomeMemoryService.write()` 已经可能把旧数据写进 home memory

这不是“丢数”，而是**错数污染**，严重性同样高。

### R3. ReadUnique 未等待 `Comp_UC`（中）

**位置**：`EPRNFController.cc:397-425,521-541`

影响：可能在 owner 还没完成 `scrub_to_I` 之前，就提前向 home UBCC 报告 recall 完成并释放 barrier。更偏向协议/时序风险，但也可能造成“先拿到数据、后才真正失效”的短暂不一致窗口。

### R4. `callbackPayloadStable` 为死字段（低）

**位置**：`EPRNFController.hh:263-285`、`EPRNFController.cc:1115-1116,1163-1164`

字段只初始化、不写真、不消费。当前 callback 稳定性完全依赖 `finishChiTxn()->setRecallCaptureData()`，该字段本身不提供任何保护。

### R5. `hasDataPayload` / `dataReturned` 语义分裂（低）

**位置**：`EPBackend.cc:1231-1237,1254-1260` 与 `UBAdapter.cc:483-488`

`OuterRecallResponse` 内允许 `hasDataPayload=true` 但 `dataReturned=false`。wire 层最终只按 `dataReturned` 决定是否发送，功能上不一定出错，但对象语义不自洽，也放大了 R2 中的 stale-data home-install 风险。

---

## 6. 最终判断

1. **链路闭环已确认**：`OuterRecallMsg -> owner CHI recall -> callback -> RecallResponse -> UBCC.dataBuf -> GRANT_HANDSHAKE -> requester grant` 是真实存在的，不是“只做了控制流、没搬数据”。
2. **ReadShared / ReadUnique 两条路径都能把 recall data 推到 grant。**
3. **最危险问题是 owner 侧 EPRNF 对 recall `CompData` 未做多 beat 拼装。** 按当前 CHI 其他路径的编码习惯，这很可能导致 recall 只保留最后一拍数据。
4. **第二危险问题是 recall 发起失败时的 stale capture 污染 home memory。** 这条风险不在 UB wire，而在 `sendRecallResponse()` 的本地 `HomeMemoryService.write()`。
5. **ReadUnique 的 `Comp_UC` 门控缺失** 更像协议完成时序缺口，建议单独列入后续修复项，但它不是本链路里最直接的数据丢失点。
