# FV-5（v3）：wait-for graph 活性 / 死锁复核

## 依据

- 前置报告：`fv3_outstanding_lifecycle.md`、`fv4_fault_recovery_report.md`
- 代码复核：`UBCCController.cc:428-526, 713-769, 838-874, 1136-1144, 1332-1427, 1907-1954, 1991-2112, 2501-2556`
- 假设：**fair retry**——`BUSY/RETRY` 最终会重试；`RecallResp / InvalidateAck / Clear / UpgradeDone` 最终会到达。

## 结论

**单 line wait-for graph 在 fair retry 下无环。**

原因只需看 4 条等待链：

```text
QueuedRequester -> live Outstanding

RECALL/WAITING_TARGET_RESP
  -> RecallResp
  -> RECALL/DONE
  -> same-requester retry
  -> GRANT_HANDSHAKE/WAITING_CLEAR

INVALIDATE/WAITING_ALL_ACKS
  -> all InvalidateAck
  -> GRANT_HANDSHAKE/WAITING_CLEAR

GRANT_HANDSHAKE/WAITING_CLEAR
  -> Clear
  -> commit/remove

UPGRADE_PENDING/WAITING_ALL_ACKS
  -> all InvalidateAck
  -> WAITING_LOCAL_DONE
  -> UpgradeDone
  -> commit/remove
```

## 无环证据

1. **单槽 outstanding**：`createOutstanding()` 保证每个 PA 同时最多 1 个 live outstanding。不存在两个 live object 互等。  
2. **`INVALIDATE -> GRANT_HANDSHAKE` 是原地前进**：最后一个 `InvalidateAck` 后直接改成 `WAITING_CLEAR`，不是再造第二个等待点。  
3. **`RECALL/DONE` 只会被消费，不会回退**：same-requester retry 会先删 terminal recall，再建 `GRANT_HANDSHAKE`。  
4. **`GRANT_HANDSHAKE` 只等 `Clear`，成功后立即 `retireToTombstone + removeOutstanding`**；不会回到 `WAITING_ALL_ACKS/WAITING_TARGET_RESP`。  
5. **`UPGRADE_PENDING` 只允许单向流动**：`WAITING_ALL_ACKS -> WAITING_LOCAL_DONE -> DONE/remove`；early `Done` 仅缓存，不形成回边。  
6. **排队请求不彼此等待**：`replayPendingRequesters()` 每次只重放队首；一旦生成新的 live outstanding 就停止，剩余请求继续等这个前驱，不会形成队列内环。  

## 与 FV-3 / FV-4 的对应

- 与 `fv3_outstanding_lifecycle.md` 一致：四类 `OutstandingRequest` 的 stage flow 都是单向的；`replayArmed` 允许 retry 直接命中 `WAITING_CLEAR` grant。  
- 与 `fv4_fault_recovery_report.md` 一致：`Clear` 有 tombstone replay；`RecallResp/InvalidateAck` 有 stale reject。它们会丢弃旧消息，但不会制造新的 wait-for 回边。  

## 剩余风险（非 cycle）

1. `RECALL.DONE` 仍依赖 requester retry；若永不 retry，会长期占住该 PA。  
2. `Clear(reqId/src mismatch)` 会被拒绝且不退休 live grant；这是**永久等待风险**，但不是 wait-for cycle。  
3. `RecallResp/InvalidateAck/Clear/UpgradeDone` 丢失仍依赖外层 timeout/retry。  

## 最终判定

- **wait-for graph 无 cycles（fair retry 下）**：**是**。  
- **剩余问题是否属于协议内死锁环**：**否**；它们属于 timeout/GC 缺失导致的条件式活性风险。
