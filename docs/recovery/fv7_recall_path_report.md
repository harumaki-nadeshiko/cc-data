# FV-7 Recall data path verification

## 1. 全链路追踪

### 1.1 OuterRecallMsg → EPBackend.handleRecallRequest
- 入口在 `EPBackend.cc:1154-1268`。
- `handleRecallRequest()` 先校验 `recallMsg.ownerNode == _nodeId`，再按 `isReadRequest` 分两条路径：
  - `true`：发 `startReadShared(ownerLocalPa, cb)`，旧 owner 本地状态先降为 `R_S`。
  - `false`：发 `startReadUnique(ownerLocalPa, cb)`，旧 owner 本地状态先置为 `R_I`。
- 回调里统一构造 `OuterRecallResponse`：`dataReturned = dataNeeded && success && _recallCaptureDataValid`；若 `_recallCaptureDataValid` 为真，则把 `_recallCaptureDataBlock` 填到 `dataPayload`。

### 1.2 startReadShared / startReadUnique → CHI 请求
- `EPRNFController.cc:1088-1180`。
- 两者都会创建 `PendingChiTxn`：
  - `op` 分别为 `ReadShared` / `ReadUnique`
  - `beatsExpected = dataMsgsPerLine`
  - `recallDataValid = false`
  - `callbackPayloadStable = false`
- 差异：
  - `ReadShared` 使用 `CHIRequestType_ReadShared`，`proxyOp = NoProxyOp`
  - `ReadUnique` 使用 `CHIRequestType_ReadUnique`，`proxyOp = RecallUnique`

### 1.3 CHI data beats → recallDataBlk
- `EPRNFController.cc:470-547`。
- `recvDataMsg()` 接受 `CompData_I/SC/UC/UD_PD/SD_PD`。
- 找到对应 `PendingChiTxn` 后：
  - `beatsReceived++`
  - `recallDataBlk = msg->getdataBlk()`
  - `recallDataValid = true`
- 最后一个 beat 到达时，两条路径都会：
  - 给 HN-F 发一个 `CompAck`
  - 调 `finishChiTxn(linePa, true)`

### 1.4 recallDataBlk → callbackPayloadStable/回调稳定点
- 实际稳定点在 `EPRNFController.cc:899-943`。
- `finishChiTxn()` 在 erase txn 之前：
  - 若 `recallDataValid`，调用 `_backend->setRecallCaptureData(txn.recallDataBlk, true)`
  - 否则显式清空 backend 侧 capture valid
- 然后删除 `_pendingChiTxns[linePa]`，再执行回调。
- **结论**：代码真正依赖的是 `EPBackend::_recallCaptureDataBlock/_recallCaptureDataValid` 这对 backend-side 缓冲，而不是 `callbackPayloadStable`。

### 1.5 callback → RecallResponse(data)
- `EPBackend.cc:1221-1261, 1271-1308`。
- 回调读取 backend 已稳定好的 `_recallCaptureDataBlock`，生成 `OuterRecallResponse`。
- `sendRecallResponse()` 再调用 `UBAdapter::sendRecallResp()`，把 `DataBlock` 指针带出去。

### 1.6 RecallResponse(data) → UBCC.processRecallResponse
- `UBAdapter.cc:462-502`：
  - 若 `dataReturned`，置 `UB_FLAG_DATA_RETURNED`
  - 若 `dataBlk && dataReturned`，再置 `UB_FLAG_HAS_DATA` 并拷贝 64B 到 `req.b.recallResp.data`
- `UBRouter.cc:417-433`：
  - 若 `HAS_DATA`，重建 `DataBlock dataBlk(64)`
  - 调 `_localUbcc->processRecallResponse(..., dataPtr)`
- `UBCCController.cc:1058-1154`：
  - 校验目录项、epoch、outstanding、ownerNode、reqId
  - `ost->recallBarrierDone = true`
  - 若 `dataBlk && dataReceived`，`memcpy(ost->dataBuf, dataBlk->getData(0,64), 64)`，并令 `ost->dataValid = true`

### 1.7 UBCC.processRecallResponse → dataBuf → grant
- `UBCCController.cc:721-746`：当同一 PA 上的 `RECALL` outstanding 已 `DONE` 时，删除旧 `RECALL`，新建 `GRANT_HANDSHAKE`。
- 在这个转换点：
  - `grantOreq->dataValid = recallData.dataValid`
  - 若有效，`memcpy(grantOreq->dataBuf, recallData.dataBuf, 64)`
  - `grantOreq->dataSource = GrantDataSource::RecallBuffer`
- `EPBackend.cc:801-910`：收到 grant 且 `dataSource == RecallBuffer` 时，把路由带来的 grant data 再写入 `_recallCaptureDataBlock`，`populateGrantData()` 从 RecallBuffer 消费到 `_lastGrantDataBlock`。
- `EPSNFController.cc:233-290`：`CompData` 发送给 requester 前，直接读取 `_backend->lastGrantData()` 作为 grant 数据源。

---

## 2. 数据捕获正确性核查

### 2.1 已确认成立的链路
- `CompData` 到达后，`EPRNFController` 的确把数据写进 `PendingChiTxn.recallDataBlk`。
- `finishChiTxn()` 在 txn 删除前，把 `recallDataBlk` 转存到 `EPBackend::_recallCaptureDataBlock`。
- `EPBackend` 回调构造 `OuterRecallResponse` 时，确实从 `_recallCaptureDataBlock` 取数。
- `UBAdapter/UBRouter/UBCC` 继续把这 64B 数据搬运到 `OutstandingRequest::dataBuf`。
- `RECALL -> GRANT_HANDSHAKE` 转换时，`dataBuf` 会被完整复制到新的 grant outstanding。
- 最终 `EPSNFController` 发给 requester 的 `CompData` 读取的是这条 grant data 路径。

### 2.2 对“assembled into recallDataBlk”的静态结论
- 当前实现**没有显式按 beat offset 拼装**，只有：`recallDataBlk = msg->getdataBlk()`。
- 因此该链路正确性的前提是：**`CHIDataMsg::getdataBlk()` 在每个 beat 上都返回“整行已组装好的 DataBlock”，或者至少最后一个 beat 返回完整 64B 行**。
- 若 `getdataBlk()` 只代表当前 beat 片段，则前面 beat 会被后一个 beat 覆盖，`recallDataBlk` 会发生数据丢失。

---

## 3. ReadShared vs ReadUnique 路径核查

### 3.1 ReadShared（owner 降级到 shared）
- `EPBackend.cc:1219-1238` 走 `startReadShared()`。
- 本地 requester bookkeeping 先把 owner 行状态改成 `R_S`。
- `EPRNFController` 发 `ReadShared`，收到 `CompData_*` 后在最后一个 beat 上 `CompAck + finishChiTxn()`。
- 回调发送 `RecallResponse`，home UBCC 释放 recall barrier，后续 grant 会把 owner 保留在 sharers 集合中。

### 3.2 ReadUnique（owner 驱逐到 invalid）
- `EPBackend.cc:1242-1261` 走 `startReadUnique()`。
- 本地 requester bookkeeping 先把 owner 行状态改成 `R_I`。
- `EPRNFController` 发 `ReadUnique`，代码注释声称该路径应为“`CompData` 先到，`Comp_UC` 后完成”。
- 但真实实现是：
  - `recvDataMsg()` 在最后一个 `CompData` beat 就 `CompAck + finishChiTxn()`。
  - `recvResponseMsg()` 对 `ReadUnique` 的 `Comp_UC` 仅 `return true`，不会再驱动完成。
- **结论**：ReadUnique 的数据返回路径是通的，但“owner 真正 scrub_to_I 的完成时刻”并未由 `Comp_UC` 严格门控。

---

## 4. 可疑数据丢失点 / 风险点

1. **多 beat 覆盖风险（高）**
   - 位置：`EPRNFController.cc:498, 527`
   - 现象：每个 beat 都执行 `recallDataBlk = msg->getdataBlk()`，没有按 offset merge。
   - 风险：若 `getdataBlk()` 不是“完整行快照”，则只会留下最后一次赋值的数据。

2. **`callbackPayloadStable` 是死字段（中）**
   - 位置：`EPRNFController.hh:264`，`EPRNFController.cc:1116, 1164`
   - 现象：只初始化为 `false`，未见任何 `true` 写入，也未见读取。
   - 影响：文义上要求“callback 前 payload 已稳定”，但实现实际依赖 `finishChiTxn()->setRecallCaptureData()`；字段本身不起保护作用。

3. **ReadUnique 未等待 `Comp_UC`（中）**
   - 位置：`EPRNFController.cc:400-425, 521-541`
   - 现象：代码注释说 `Comp_UC` 才是 `ReadUnique` completion token，但实现提前在最后一个 data beat 完成回调。
   - 影响：更像是权限/完成时序风险，不一定丢数据，但可能让 `RecallResponse` 早于 owner 彻底失效化。

4. **`OuterRecallResponse.hasDataPayload` 与 `dataReturned` 可短暂不一致（低）**
   - 位置：`EPBackend.cc:1233-1236, 1256-1259`
   - 现象：只要 `_recallCaptureDataValid` 为真，就会设置 `hasDataPayload=true`；但 `dataReturned` 还受 `dataNeeded && success` 约束。
   - 影响：当前 `UBAdapter::sendRecallResp()` 只在 `dataReturned` 为真时真正打包数据，所以功能上不会误传，但对象内语义不完全一致。

---

## 5. 总结

- **主链路是连通的**：`OuterRecallMsg -> startReadShared/Unique -> CompData -> recallDataBlk -> EPBackend capture -> RecallResponse(data) -> UBCC.processRecallResponse -> outstanding.dataBuf -> GRANT_HANDSHAKE -> requester grant CompData`。
- **数据确实被一路搬运到 grant**，不存在明显的“回调后彻底丢失”问题。
- **最需要警惕的点**是 `recallDataBlk = msg->getdataBlk()` 的多 beat 语义：如果底层 `getdataBlk()` 不是完整行，当前实现会覆盖丢数。
- **ReadUnique 路径**在功能上能回传数据，但完成时序没有按注释那样等 `Comp_UC`，这是额外协议风险。
