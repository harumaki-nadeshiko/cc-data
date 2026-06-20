# FV-5：Liveness / deadlock-free 复核

## 0. 依据

- 文档：`fv4_fault_recovery_report.md`、`fv3_outstanding_lifecycle.md`、`fv_overview.md`、`scheme_v4.md`。
- 代码：`UBCCController.cc`、`EPBackend.cc`，以 `grep` + `sed -n` 复核 `replayArmed`、`WAITING_*`、`processRecallResponse()`、`processInvalidationAck()`、`processClear()`、`processOuterUpgradeDone()`。
- 假设：**fair retry**——`BUSY/RETRY` 最终会重试；`RecallResp/InvalidateAck/Clear/UpgradeDone` 最终会到达，或由外层超时-重试替代。

---

## 1. wait-for graph（单 line）

从 `fv3_outstanding_lifecycle.md` 的阶段流和 `UBCCController.cc` 代码看，单 PA 的等待边只有这几类：

```text
QueuedRequester -> live Outstanding

RECALL/WAITING_TARGET_RESP
  -> owner RecallResp
  -> RECALL/DONE
  -> same-requester retry
  -> GRANT_HANDSHAKE/WAITING_CLEAR

INVALIDATE/WAITING_ALL_ACKS
  -> all sharer InvalidateAck
  -> GRANT_HANDSHAKE/WAITING_CLEAR

GRANT_HANDSHAKE/WAITING_CLEAR
  -> requester Clear
  -> commit/remove

UPGRADE_PENDING/WAITING_ALL_ACKS
  -> all sharer InvalidateAck
  -> UPGRADE_PENDING/WAITING_LOCAL_DONE
  -> requester UpgradeDone
  -> commit/remove
```

关键锚点：

- 每条 line 同时最多一个 live outstanding（`createOutstanding()` 单槽）。
- `INVALIDATE -> GRANT_HANDSHAKE` 是**原地前进**，不是新建第二个 live object。
- `RECALL.DONE` 由 same-requester retry 消费成 `GRANT_HANDSHAKE`。
- `GRANT_HANDSHAKE` 只等 `Clear`；`UPGRADE_PENDING` 只等 `Ack/Done`，都没有回退边。

---

## 2. 为什么无环

可按偏序看成：

`队列等待` → `barrier 等待` → `completion 等待` → `commit/remove` → `replay 下一请求`

无环证据：

1. `WAITING_CLEAR` 成功后直接 `commit + retireToTombstone + removeOutstanding`，不会回到 `WAITING_ALL_ACKS/WAITING_TARGET_RESP`。  
2. `UPGRADE_PENDING` 只允许 `WAITING_ALL_ACKS -> WAITING_LOCAL_DONE -> DONE`；early `Done` 只缓存，不回退。  
3. `INVALIDATE` 最后一个 ack 后原地转 `GRANT_HANDSHAKE/WAITING_CLEAR`，避免双 outstanding 互等。  
4. `RECALL.DONE` 的 retry 消费路径是“删旧 recall、建新 grant”，不是再次发起 recall。  
5. `EPBackend` 的 `outerTxnPending` 在 `BUSY` 返回时立即清除，在 `sendClear()` 后也清除；它是本地 fence，不构成协议环。  

结论：**在 fair retry 下，wait-for graph 无 cycle。** 这与 `fv_overview.md` 对 FV-5 的摘要一致。

---

## 3. 三个 spot-check

### TC2：`replayArmed`

依据 `fv3_outstanding_lifecycle.md` 与 `UBCCController.cc`：

- `RECALL` 收到响应后进入 `DONE`；
- same-requester retry 会先移除 terminal recall，再创建 `GRANT_HANDSHAKE`；
- `replayArmed=1` 且 tuple 精确匹配时，retry 直接命中 grant，不再继续 `BUSY`。

因此 **TC2 不再存在 “home 等 retry、retry 又被旧 barrier 挡回去” 的闭环**。

### TC7：barrier

`scheme_v4.md` 和 `drift_in_progress.md:D-34` 已说明，TC7 的历史问题主要是 workload barrier / 可见性伪环，而不是 UBCC outstanding 互等。协议内看：

- writeback/evict/新 outer request 只会观察到 `isLineBusy()` 然后 `BUSY/RETRY`；
- 不会创建第二个 live outstanding 反压当前 barrier；
- 当前前驱完成后，后继请求再 replay/重试。

因此 **TC7 对应的是外部测试同步问题，不是 wait-for graph cycle**。

### TC10：upgrade barrier

按 `fv_overview.md` 的 FV-5 摘要继续复核 upgrade barrier 路径：

- `UPGRADE_PENDING` 先冻结 `targetMask` 并停在 `WAITING_ALL_ACKS`；
- 最后一个 `InvalidateAck` 到达后，才 `accepted=true` 并转 `WAITING_LOCAL_DONE`；
- `UpgradeDone` 早到只缓存；
- 真正 commit 只发生在 `WAITING_LOCAL_DONE`。

因此 **upgrade barrier 已被串成单向链：Ack-barrier -> Done-barrier -> Commit**，没有回边。

> 注：`verification_plan.md` 将 TC10 标为 `concurrent_atomic`；这里沿用 `fv_overview.md`/用户问题里的“TC10 upgrade barrier”称呼，复核的实际代码对象是 `UPGRADE_PENDING` 活性路径。

---

## 4. 剩余风险

无环 ≠ 无等待风险。现存问题仍主要是**条件式活性**：

1. `RECALL.DONE` 缺少 timeout/GC；若 requester 永不 retry，该 line 会长期 pin 住。  
2. `RecallResp/InvalidateAck/Clear/UpgradeDone` 丢失时，controller 内没有完整 timeout 扫描器。  
3. resident/backstore callback 若缺失，`residentWaiters` 也可能长期堵塞。  

这些更像 **livelock / indefinite wait**，不是协议内 cycle。

---

## 5. 结论

1. **在 fair retry 假设下，UBCC 单-line wait-for graph 无环。**
2. **TC2 (`replayArmed`)、TC7（barrier 伪环）、TC10（upgrade barrier）本轮复核通过。**
3. **剩余风险是 timeout/GC 缺失导致的永久等待，不是 wait-for cycle。**
