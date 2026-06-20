# FV-Driven Code Modification Suggestions

## 🔴 P0: UpgradeAckNotify 在 union UBMsgBody 中无条目 (FV-9)

**风险**: `UBMsg.hh` 中 `UpgradeAckNotify` 有 enum 枚举但 `union UBMsgBody` 无对应条目。当前 send/recv 两端都不碰 `msg.b` 所以无 bug，但未来任何访问 `msg.b` 的代码都会读到未定义内存。

**修改**:
```cpp
// UBMsg.hh — 在 union UBMsgBody 中添加
struct UBUpgradeAckNotifyBody { /* no extra fields */ };
// union 中:
UBUpgradeAckNotifyBody upgradeAckNotify;
```

## 🟠 P1: Recall 多 beat 数据覆盖风险 (FV-7)

**风险**: `EPRNFController::recvDataMsg()` 行 502 调用 `recallDataBlk = msg->getdataBlk()` 对每个 beat 做**全量替换**而非按 beat mask 合并。若 CHI 协议返回多个 data beat，最后一个 beat 可能不是完整 64B 行，前面的 beat 数据会被覆盖丢失。

**修改** (`EPRNFController.cc:490-510`):
```cpp
// 当前:
recallDataBlk = msg->getdataBlk();
// 改为按 writeMask 合并:
const WriteMask &wm = msg->m_bitMask;
for (int i = 0; i < cacheLineSize; i++) {
    if (wm.test(i)) recallDataBlk.setByte(i, msg->getdataBlk().getByte(i));
}
```

## 🟠 P2: callbackPayloadStable 死字段 (FV-7)

**风险**: `callbackPayloadStable` 字段定义在 `PendingChiTxn` 但从未被设为 true 也从未被读取。ReadUnique 回调可能在最后一个 CompData beat 就触发，而此时 HN-F 可能还未确认。

**修改** (`EPRNFController.cc`, finishChiTxn 路径):
```cpp
// 在 Comp_UC 或 CompAck 到达后设置:
txn.callbackPayloadStable = true;
// 在回调触发前检查:
if (!txn.callbackPayloadStable) return; // 推迟
```

## 🟠 P3: SnpUnique retToSrc 注释/代码不一致 (FV-6)

**风险**: `EPRNFController.cc` 中 `handleSnpUnique` 注释写 `SnpRespData_I`(retToSrc=true) 但代码实际发的是 `SnpResp_I`。HN-F 如果期望 `SnpRespData_I` 会一直等数据 beats 而挂起。

**修改** (`EPRNFController.cc:747-785`):
```cpp
// 检查 retToSrc 分支 — 若需要数据回复则发送 SnpRespData_I
if (retToSrc) {
    sendSnpRespDataI(msg);  // 带数据
} else {
    sendSnpRespI(msg);      // 确认即可
}
```

## 🟡 P4: RECALL.DONE 无 GC (FV-3)

**风险**: RECALL 完成后 `stage=DONE`，条目留在 `_outstandingReqs` 直到原请求者重试并创建 GRANT_HANDSHAKE。若请求者永不重试（如节点宕机），条目永久占用，阻塞后续请求。

**修改** (`UBCCController.cc`):
```cpp
// 在 wakeup() 或周期性清理中添加 RECALL.DONE 超时移除
if (ost->opType == RECALL && ost->stage == DONE &&
    curTick() - ost->respTick > recallTimeout) {
    retireToTombstone(*ost, false);
    removeOutstanding(linePa);
}
```

## 🟡 P5: ReadUnique recall 未等 Comp_UC (FV-7)

**风险**: ReadUnique 召回路径在最后一个 CompData beat 就触发回调，但 CHI 协议要求等 Comp_UC 确认 HN-F 已完成所有权转移。提前回调可能导致 HN-F 仍视 EP-RNF 为 owner。

**修改** (`EPRNFController.cc:540-547`):
```cpp
// 当前: 最后一个 data beat 触发回调
// 改为: 等 Comp_UC 到达后再触发
if (txn.op == ReadUnique && txn.beatsReceived >= txn.beatsExpected) {
    // 不要立即回调 — 等 Comp_UC
    txn.waitingForCompUC = true;
}
// 在 Comp_UC handler 中:
if (txn.waitingForCompUC) { callback(true); }
```

## 🟡 P6: RecallResp 缺 duplicate 去重 (FV-4)

**风险**: `processRecallResponse` 对 duplicate 没有显式去重（如 Clear 的 tombstone 和 InvalidateAck 的 ackMask 防护）。重复 RecallResp 可能触发重复的 `recallBarrierDone = true`（虽然语义幂等但缺少防护层）。

**修改** (`UBCCController.cc:1120`):
```cpp
// 在 processRecallResponse 中, 设置 recallBarrierDone 前:
if (ost->recallBarrierDone) {
    return true;  // 幂等，已处理过
}
```

## 🟢 P7: Dual-Socket 零测试覆盖 (FV-11)

**风险**: 双 socket 架构有完整的代码实现但无运行时测试（`num_sockets=1` 永远退化）。`homeSocket decode`、`cross-socket routing`、`per-socket UBCC` 等关键路径从未被触达。

**修改方案**: 新增 TC29-TC35，配置 `UBCC_NUM_SOCKETS=2`。见下方 Task 3。

---

## 优先级排序

| 优先级 | ID | 修改规模 | 风险 |
|--------|-----|---------|------|
| P0 | P1 | 1 行 UBMsg.hh | 未来崩溃 |
| P0 | P3 | ~20 行 EPRNFController.cc | HN-F 挂死 |
| P1 | P1 | ~5 行 EPRNFController.cc | 多 beat 丢数据 |
| P1 | P5 | ~10 行 EPRNFController.cc | 所有权未完全转移 |
| P2 | P4 | ~15 行 UBCCController.cc | 条目泄漏 |
| P2 | P6 | ~5 行 UBCCController.cc | 重复消息防护 |
| P2 | P2 | ~15 行 EPRNFController.cc | 死字段激活 |
| P3 | P7 | 新增 TC29-35 | 双 socket 验证 |
