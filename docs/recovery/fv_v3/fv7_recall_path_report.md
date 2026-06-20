# FV-7 v3 Recall 数据链路复核报告

> 方法：按用户要求使用 `grep -n` + `sed -n` 静态复核。  
> 主核查范围：
> - `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc:1154-1315`
> - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:470-547,899-943,1088-1180`
> - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:1058-1154`
> - 交叉补点：`EPRNFController.cc:397-418`、`UBCCController.cc:722-748,2137-2146`

## 1. 结论

- **Recall 主数据链是通的**：`EPBackend(handleRecallRequest)` → `EP-RNF startReadShared/ReadUnique` → `recvDataMsg` 捕获 `CompData` → `finishChiTxn` 回填到 `EPBackend` → `sendRecallResponse` 回传 home → `UBCC.processRecallResponse` 落到 `OutstandingRequest::dataBuf`。
- **ReadShared / ReadUnique 两条路径都走同一套数据回传骨架**，区别只在：
  - ReadShared：owner 本地状态降到 `R_S`，发 `CHIRequestType_ReadShared`
  - ReadUnique：owner 本地状态置 `R_I`，发 `CHIRequestType_ReadUnique`
- **明确的数据丢失风险点在 `EPRNFController::recvDataMsg()`**：对多 beat `CompData` 仅做整块覆盖赋值，没有按 beat/bitmask 合并；若一条 line 分多拍返回，早到的数据可能被后到 beat 覆盖。
- **ReadUnique 还存在完成时序前冲风险**：注释说应等待 `Comp_UC`，但当前代码在最后一个 `CompData` beat 就 `finishChiTxn()`；这是时序风险，不是当前主数据丢失点。

## 2. Recall 数据链分段

| 阶段 | 代码证据 | 数据状态 |
|---|---|---|
| owner 收到 RecallReq | `EPBackend.cc:1154-1215` | 解析 `OuterRecallMsg`，确定 `ownerLocalPa` |
| 发起本地 CHI recall | `EPBackend.cc:1217-1269` + `EPRNFController.cc:1088-1180` | ReadShared/ReadUnique 二选一 |
| HN-F 返回 `CompData` | `EPRNFController.cc:470-547` | 数据先进入 `PendingChiTxn.recallDataBlk` |
| recall 完成回填 backend | `EPRNFController.cc:899-918` | `recallDataBlk -> EPBackend::_recallCaptureDataBlock` |
| owner 回传 RecallResp | `EPBackend.cc:1224-1241,1250-1267,1277-1315` | `dataReturned/hasDataPayload` 门控后发回 home |
| home UBCC 接收 | `UBCCController.cc:1070-1154` | 数据落到 `OutstandingRequest::dataBuf` |
| 后续 grant 延续 | `UBCCController.cc:722-748,2137-2146` | `RECALL.dataBuf -> GRANT_HANDSHAKE.dataBuf -> outBlk` |

## 3. ReadShared 路径

### 3.1 owner 侧入口

`EPBackend.cc:1217-1242`：

- 先 `setRecallCaptureData(DataBlock(64), false)` 清空旧捕获；
- 调 `_epRnfCtrl->startReadShared(ownerLocalPa, cb)`；
- callback 内用：
  - `resp.dataReturned = capturedMsg.dataNeeded && success && _recallCaptureDataValid`
  - 若为真，则把 `_recallCaptureDataBlock` 填入 `resp.dataPayload`。

### 3.2 EP-RNF 发起与接收

`EPRNFController.cc:1088-1129`：

- `txn.op = PendingChiOp::ReadShared`
- `txn.proxyOp = EpProxyOp_NoProxyOp`
- `sendChiRequest(..., CHIRequestType_ReadShared, ...)`

`EPRNFController.cc:494-517`：

- 每收到一个 `CompData`：
  - `beatsReceived++`
  - `recallDataBlk = msg->getdataBlk()`
  - `recallDataValid = true`
- 最后一个 beat：发送 `CompAck`，随后 `finishChiTxn()`。

### 3.3 home 落点

`UBCCController.cc:1150-1154`：

- 若 `dataBlk && dataReceived`，执行 `memcpy(ost->dataBuf, dataBlk->getData(0, 64), 64)`。

## 4. ReadUnique 路径

### 4.1 owner 侧入口

`EPBackend.cc:1243-1269`：

- 同样先清空 `_recallCaptureDataValid`；
- 调 `_epRnfCtrl->startReadUnique(ownerLocalPa, cb)`；
- callback 仍以 `_recallCaptureDataValid` 决定是否携带 payload。

### 4.2 EP-RNF 发起与接收

`EPRNFController.cc:1132-1180`：

- `txn.op = PendingChiOp::ReadUnique`
- `txn.proxyOp = EpProxyOp_RecallUnique`
- `sendChiRequest(..., CHIRequestType_ReadUnique, EpProxyOp_RecallUnique)`

`EPRNFController.cc:520-547`：

- 数据 beat 处理和 ReadShared 基本相同：
  - `beatsReceived++`
  - `recallDataBlk = msg->getdataBlk()`
  - `recallDataValid = true`
- 最后一个 beat 直接 `CompAck + finishChiTxn()`。

### 4.3 ReadUnique 的额外时序问题

`EPRNFController.cc:397-418` 明写：`Comp_UC` 是 `ReadUnique` 的 completion token；但实现里：

- `recvDataMsg()` 已在最后一个 `CompData` beat 直接完成；
- `recvResponseMsg()` 收到 `Comp_UC` 时，若 txn 还是 `ReadUnique`，直接 `return true`。

因此 **ReadUnique 现在是“数据到齐即完成”，并未真正被 `Comp_UC` 门控**。

## 5. 明确标记：数据丢失点

### 5.1 主风险：多 beat `CompData` 覆盖

证据：

- `EPRNFController.cc:498`：`it->second.recallDataBlk = msg->getdataBlk();`
- `EPRNFController.cc:529`：`it->second.recallDataBlk = msg->getdataBlk();`
- 同时 `startReadShared/startReadUnique` 都设置 `beatsExpected = dataMsgsPerLine`，说明设计上允许多 beat。

结论：

- 当前实现**没有** `copyPartial()`、`bitMask` merge、offset merge；
- 所以在 `dataMsgsPerLine > 1` 时，`recallDataBlk` 会被后到 beat 反复整块覆盖；
- `finishChiTxn()` 最终只把“最后一次覆盖后的块”送到 `EPBackend`；
- 这就是当前 recall 路径里最明确的**数据丢失/数据不完整风险**。

### 5.2 非主损失点：ReadUnique 完成前冲

这不会直接改写数据内容，但会让：

- `OuterRecallResponse` 的发送时刻早于协议注释宣称的 `Comp_UC` 完成点；
- 若后续需要以 `Comp_UC` 作为唯一完成屏障，则当前回调时序偏早。

## 6. 链路末端是否继续保留数据

是。`UBCCController.cc:722-748` 显示 DONE 的 `RECALL` 会被转换成新的 `GRANT_HANDSHAKE`，并执行：

- `grantOreq->dataValid = recallData.dataValid`
- `memcpy(grantOreq->dataBuf, recallData.dataBuf, 64)`
- `grantOreq->dataSource = GrantDataSource::RecallBuffer`

随后 `UBCCController.cc:2137-2146` 可把该 `dataBuf` 再导出为 `outBlk`。因此 **home UBCC 不是链路终点，recall data 还能继续进入后续 grant 路径**。

## 7. 最终判断

1. **ReadShared 路径：逻辑闭环成立，但有 multi-beat 覆盖导致的数据丢失风险。**
2. **ReadUnique 路径：逻辑闭环也成立，同样有 multi-beat 覆盖风险；另有 `Comp_UC` 未门控完成的时序风险。**
3. **当前最该优先修的不是 UBCC 存储段，而是 `EPRNFController::recvDataMsg()` 的 recall 数据拼装方式。**
