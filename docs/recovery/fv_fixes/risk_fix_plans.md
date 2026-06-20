# FV 风险修复计划（按严重度排序）

## 已冻结的用户决策

1. **`clearAckCached` → A**：删除死字段；不做第二层 ClearAck cache。
2. **`InvalidateAck.success` → B**：从 `OuterInvalidationAck` 删除该字段；invalidaton barrier 不允许“失败 ack 上线”。
3. **`ReadUnique recall` → A**：仅修 `ReadUnique`，严格等待 **最后一个 data beat + `Comp_UC` + `CompAck` 成功发送** 后再回调；`ReadShared` 保持不变。
4. **`SnpShared` → A**：只修 init-phase 路由问题并恢复 `fatal`；不扩展为完整 sharer-registration 审计。

---

## 🔴 P0

### 1. Recall 多 beat 数据覆盖

1. **问题摘要**：`EPRNFController::recvDataMsg()` 对每个 recall `CompData` beat 做整块覆盖，4-beat/2-beat 返回时会丢前面 beat 的字节。
2. **受影响文件与行号**：
   - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:494-515`
   - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:524-545`
3. **具体代码修复（old → new）**：

   ```cpp
   // EPRNFController.cc: ReadShared path
   -        it->second.recallDataBlk = msg->getdataBlk();
   +        it->second.recallDataBlk.copyPartial(
   +            msg->getdataBlk(), msg->m_bitMask);
            it->second.recallDataValid = true;

   // EPRNFController.cc: ReadUnique path
   -        it->second.recallDataBlk = msg->getdataBlk();
   +        it->second.recallDataBlk.copyPartial(
   +            msg->getdataBlk(), msg->m_bitMask);
            it->second.recallDataValid = true;
   ```
4. **风险评估**：若 `bitMask`/offset 理解错，会把单-beat 正常路径改坏，导致整行拼装错位或部分字节保持零值。
5. **所需测试覆盖**：
   - **TC46**：`e2e_tc46_multibeat_recall_readshared`
   - **TC47**：镜像 `ReadUnique` recall 多 beat
   - 日志检查：owner EP-RNF 必须看到 `beatsReceived == beatsExpected` 且最终 `recallDataBlk` digest 完整。

### 2. Recall 发送失败快路径会把旧 capture data 安装回 home memory

1. **问题摘要**：新 recall 发起失败时，旧 `_recallCaptureDataValid` 可能残留，随后 `sendRecallResponse()` 会在本地把 stale data 写回 home memory。
2. **受影响文件与行号**：
   - `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc:1221-1238`
   - `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc:1244-1261`
   - `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc:1285-1296`
   - `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh:423-426`
3. **具体代码修复（old → new）**：

   ```cpp
   // EPBackend.cc: startReadShared / startReadUnique 前先清空 capture
   +    setRecallCaptureData(DataBlock(64), false);
        _epRnfCtrl->startReadShared(...)

   +    setRecallCaptureData(DataBlock(64), false);
        _epRnfCtrl->startReadUnique(...)

   // EPBackend.cc: callback 中只有 dataReturned=true 才携带 payload
   -                if (_recallCaptureDataValid) {
   +                if (resp.dataReturned) {
                        resp.dataPayload = _recallCaptureDataBlock;
                        resp.hasDataPayload = true;
                    }

   -                if (_recallCaptureDataValid) {
   +                if (resp.dataReturned) {
                        resp.dataPayload = _recallCaptureDataBlock;
                        resp.hasDataPayload = true;
                    }

   // EPBackend.cc: home-memory install 必须同时满足 returned+payload
   -    if (response.hasDataPayload) {
   +    if (response.dataReturned && response.hasDataPayload) {
            EPBackend *homeBackend = EPBackend::getBackendInstance(response.homeNode);
            ...
        }
   ```
4. **风险评估**：若 gating 过严，可能把本应返回的数据 recall 误当成 no-data，导致 requester 读到旧 home memory。
5. **所需测试覆盖**：
   - 新增 fault/negative：强制 `startReadShared()` / `startReadUnique()` send-fail，验证 **home memory 不被改写**
   - **TC46/TC47**：确保成功路径仍会写回正确 data payload
   - `tc_stale_recall_resp_rejected`：验证旧 recall data 不覆盖新事务。

### 3. `SnpShared/SnpSharedFwd` 到达 EP-RNF 会发错响应并可能挂死 HN-F

1. **问题摘要**：当前 `EP-RNF` 对不该到达的 preserving snoop 走 `warn + SnpResp_SC` 诊断路径，HN-F 可能继续等待数据 beat。
2. **受影响文件与行号**：
   - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:634-644`
   - `gem5/configs/ruby/CHI_ubcc_framework.py:426-435`
3. **具体代码修复（old → new）**：

   ```cpp
   // EPRNFController.cc: 恢复 fatal
   -        case CHIRequestType_SnpShared:
   -        case CHIRequestType_SnpSharedFwd:
   -            warn("EP_RNF node_id=%d: SnpShared/SnpSharedFwd at PA=0x%lx "
   -                  "— defensive SnpResp_SC (F4 diagnostic)\n",
   -                  _nodeId, msg->m_addr);
   -            sendSnpRespSC(msg);
   -            return true;
   +        case CHIRequestType_SnpShared:
   +        case CHIRequestType_SnpSharedFwd:
   +            fatal("EP_RNF node_id=%d: unexpected SnpShared/SnpSharedFwd "
   +                  "at PA=0x%lx (routing bug; preserving snoops must not "
   +                  "target EP-RNF)\n",
   +                  _nodeId, msg->m_addr);

   # CHI_ubcc_framework.py: 锁定 init-phase 路由不回退到错误拓扑
            snf_dests.extend(nd['l_snf'].getAllControllers())
            snf_dests.append(nd['ep_snf_cntrls'][sid])
   +        assert nd['dl_snf'] not in snf_dests
            nd['hnf_wrappers'][sid].setDownstream(snf_dests)
   ```
4. **风险评估**：恢复 `fatal` 后，如果 init-phase 路由仍有残留问题，系统会更早暴露真实 bug；这是预期行为，但会让 TC1/boot 比现在更“硬失败”。
5. **所需测试覆盖**：
   - **TC1、TC11**：确认不再出现 `SnpShared/SnpSharedFwd at EP_RNF`
   - 负测：人工注入 `SnpShared -> EP-RNF`，必须 `fatal`
   - 启动期拓扑检查：HN-F 的 DSM downstream 只含 `EP_SNF`，不含 `DL_SNF`。

---

## 🟠 P1

### 4. 无 EP-RNF 时直接发送 InvalidateAck 会绕过 CHI barrier

1. **问题摘要**：`EPBackend::handleInvalidationRequest()` 在 `_epRnfCtrl == nullptr` 时直接发送 `InvalidateAck`，违反“必须先经过 `CleanUnique -> Comp_UC -> callback`”的不变量。
2. **受影响文件与行号**：
   - `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc:1565-1598`
3. **具体代码修复（old → new）**：

   ```cpp
   -    } else {
   -        // Fallback: if no EP-RNF controller, ack directly (prototype mode)
   -        warn("EPBackend node_id=%d: no EP-RNF controller, "
   -             "sending invalidation ack directly (bypasses HN-F)\n",
   -             _nodeId);
   -        OuterInvalidationAck ack;
   -        ack.linePa = invMsg.linePa;
   -        ack.ackNode = _nodeId;
   -        ack.homeNode = invMsg.homeNode;
   -        ack.epoch = invMsg.epoch;
   -        ack.reqId = invMsg.reqId;
   -        ack.success = true;
   -        sendInvalidationAck(ack);
   -        return true;
   -    }
   +    } else {
   +        fatal("EPBackend node_id=%d: invalidation path requires EP-RNF "
   +              "controller; direct InvalidateAck would bypass CHI barrier "
   +              "for PA=0x%lx\n",
   +              _nodeId, invMsg.linePa);
   +    }
   ```
4. **风险评估**：历史 prototype/半初始化配置会直接在启动或第一次 invalidation 时中止；但这比静默违反协议更安全。
5. **所需测试覆盖**：
   - **TC7 barrier** 回归
   - 配置负测：故意不绑定 `_epRnfCtrl`，第一次 invalidation 必须 `fatal`
   - 正常配置 smoke：primary path 仍经 `Comp_UC -> CompAck -> callback -> InvalidateAck`。

### 5. `ReadUnique` recall 完成时序前冲

1. **问题摘要**：当前 `ReadUnique` 在最后一个 `CompData` beat 就 `finishChiTxn()`，没有严格等待 `Comp_UC` 和 `CompAck` 成功发送。
2. **受影响文件与行号**：
   - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.hh:253-285`
   - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:397-455`
   - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:521-545`
   - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:899-943`
   - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:1030-1058`
   - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:1134-1180`
3. **具体代码修复（old → new）**：

   ```cpp
   // EPRNFController.hh: 用显式状态替代“稳定性”空壳字段
   -        bool callbackPayloadStable; // v4: callback data stabilized
   +        bool readUniqueDataComplete; // 最后一个 data beat 已到齐
   +        bool readUniqueCompUCSeen;   // completion token 已到齐

   -              needsCompAck(false), outerTxnPending(false),
   -              callbackPayloadStable(false),
   +              needsCompAck(false), outerTxnPending(false),
   +              readUniqueDataComplete(false), readUniqueCompUCSeen(false),

   // EPRNFController.cc: ReadUnique 收到 Comp_UC 时不再直接忽略
   -            if (it->second.op == PendingChiOp::ReadUnique) {
   -                return true;
   -            }
   +            if (it->second.op == PendingChiOp::ReadUnique) {
   +                it->second.readUniqueCompUCSeen = true;
   +                if (it->second.readUniqueDataComplete &&
   +                    !it->second.needsCompAck) {
   +                    finishChiTxn(msg->m_addr, true);
   +                }
   +                return true;
   +            }

   // EPRNFController.cc: ReadUnique 最后一个 data beat 只标记，不立即 finish
   -            if (!sendResponseMsg(ack)) {
   -                it->second.needsCompAck = true;
   -                scheduleEvent(Cycles(1));
   -                return true;
   -            }
   -            finishChiTxn(msg->m_addr, true);
   +            it->second.readUniqueDataComplete = true;
   +            if (!sendResponseMsg(ack)) {
   +                it->second.needsCompAck = true;
   +                scheduleEvent(Cycles(1));
   +                return true;
   +            }
   +            it->second.needsCompAck = false;
   +            if (it->second.readUniqueCompUCSeen) {
   +                finishChiTxn(msg->m_addr, true);
   +            }

   // EPRNFController.cc: CompAck retry 成功后，ReadUnique 也要等 Comp_UC
   -            uint64_t linePa = it->second.linePa;
   -            ++it;
   -            finishChiTxn(linePa, true);
   +            uint64_t linePa = it->second.linePa;
   +            bool canFinish = (it->second.op != PendingChiOp::ReadUnique) ||
   +                             it->second.readUniqueCompUCSeen;
   +            it->second.needsCompAck = false;
   +            ++it;
   +            if (canFinish) {
   +                finishChiTxn(linePa, true);
   +            }
   ```
4. **风险评估**：若 `Comp_UC` 或 retry 路径状态机接线不全，会把 `ReadUnique` 卡在“data 已到但永不 finish”的新死锁窗口。
5. **所需测试覆盖**：
   - **TC47**：`ReadUnique` recall 多 beat + completion token
   - `CompAck` send-fail 注入：验证 retry 后仍要等 `Comp_UC`
   - 日志断言：callback 必须晚于 `last data beat`、晚于 `Comp_UC`、晚于 `CompAck sent`。

---

## 🟡 P2

### 6. `clearAckCached` 是死字段

1. **问题摘要**：`clearAckCached` 只声明和初始化，从未被读取；继续保留只会让 tombstone replay 语义看起来像“半实现”。
2. **受影响文件与行号**：
   - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh:103-107`
   - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh:148-150`
   - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:2568-2571`
3. **具体代码修复（old → new）**：

   ```cpp
   // UBCCController.hh
   -    bool     clearAckCached;     // True if ClearAck has been cached for tombstone replay
        bool     replayArmed;        // True if this grant was created by replay (retry-hit allowed)

   -          recallBarrierDone(false), invalidateBarrierDone(false),
   -          clearAckCached(false), replayArmed(false),
   +          recallBarrierDone(false), invalidateBarrierDone(false),
   +          replayArmed(false),

   // UBCCController.cc
   -    req.clearAckCached = false;
   ```
4. **风险评估**：若有 out-of-tree 调试脚本/JSON dump 还在读这个字段，会在编译期或解析期暴露兼容性问题。
5. **所需测试覆盖**：
   - build + unit compile
   - Clear replay / tombstone 回归（TC38 类场景）
   - `processOuterRequest()` tombstone-hit 路径 smoke。

### 7. `OuterInvalidationAck.success` 是死字段

1. **问题摘要**：`ack.success` 在 callback 中被写入，但 UB wire、router、UBCC 全链路都不消费它，属于误导性状态位。
2. **受影响文件与行号**：
   - `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh:96-106`
   - `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc:1575-1582`
   - `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc:1590-1597`
3. **具体代码修复（old → new）**：

   ```cpp
   // EPBackend.hh
   -    bool success;            // True if invalidation succeeded
   -
   -    OuterInvalidationAck() : linePa(0), ackNode(-1), homeNode(-1),
   -                              epoch(0), reqId(0), success(false) {}
   +    OuterInvalidationAck() : linePa(0), ackNode(-1), homeNode(-1),
   +                              epoch(0), reqId(0) {}

   // EPBackend.cc
   -                ack.success = ok;
                    sendInvalidationAck(ack);

   -        ack.success = true;
            sendInvalidationAck(ack);
   ```
4. **风险评估**：如果有人误以为这个字段已经有跨节点语义，删掉后会暴露出上层“失败 ack”假设不成立；但这正符合当前协议决议。
5. **所需测试覆盖**：
   - **TC7 barrier**
   - `tc_duplicate_invalidate_ack_ignored`
   - 编译检查：`OuterInvalidationAck` 不再有 `success` 访问残留。

### 8. `callbackPayloadStable` 是死字段，应删除并改为显式完成条件

1. **问题摘要**：`callbackPayloadStable` 既不置真也不消费，当前 callback 稳定性完全不依赖它。
2. **受影响文件与行号**：
   - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.hh:260-285`
   - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:1113-1118`
   - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:1161-1166`
   - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:1213-1218`
3. **具体代码修复（old → new）**：

   ```cpp
   // EPRNFController.hh
   -        bool callbackPayloadStable; // v4: callback data stabilized
   +        bool readUniqueDataComplete;
   +        bool readUniqueCompUCSeen;

   // 三个 start* 初始化点统一删旧字段、改初始化新字段
   -    txn.callbackPayloadStable = false;
   +    txn.readUniqueDataComplete = false;
   +    txn.readUniqueCompUCSeen = false;

   -    txn.callbackPayloadStable = false;
   +    txn.readUniqueDataComplete = false;
   +    txn.readUniqueCompUCSeen = false;

   -    txn.callbackPayloadStable = false;
   +    txn.readUniqueDataComplete = false;
   +    txn.readUniqueCompUCSeen = false;
   ```
4. **风险评估**：这项改动与上面的 `ReadUnique` 完成修复共用结构体字段；若拆分提交不当，容易出现 header/cc 不一致的编译错误。
5. **所需测试覆盖**：
   - build + `grep` 零引用检查
   - **TC47**：证明新的显式状态确实驱动 `ReadUnique` 完成
   - `ReadShared` / `CleanUnique` smoke：确认未被误套上额外 gate。

---

## 建议落地顺序

1. **先修 P0 数据面**：R1 → R2
2. **再修 P0 协议错误出口**：R3
3. **再修 P1 时序/barrier**：R4 → R5
4. **最后清理 P2 死字段**：R6 → R7 → R8

这样可以先把“错数/挂死”类风险收掉，再做结构清理，减少回归噪音。
