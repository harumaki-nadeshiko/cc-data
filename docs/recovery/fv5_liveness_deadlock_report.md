# FV-5：Liveness / 无死锁审计报告

## 0. 方法与前提

- 依据：`fv4_fault_recovery_report.md`、`fv3_outstanding_lifecycle.md`、`verification_plan.md`、`intra_inter_verification_plan.md`。
- 代码抽取：对 `UBCCController.cc` 使用 `grep` + `sed -n` 审查阻塞点，重点覆盖 `findOutstanding` / `isLineBusy` / `WAITING_*` / `replayArmed` / `replayPendingRequesters` / `processClear` / `processInvalidationAck` / `processRecallResponse`。
- 建模策略：按 **单 line wait-for graph** 归纳，而不是做全局状态穷举；这与验证计划中对活性证明采用 `wait-for graph + 活性 trace`、避免状态爆炸/内存失控的方向一致（`intra_inter_verification_plan.md:285-309`）。
- 公平性假设：
  1. requester 收到 `BUSY/retry` 后最终会重试；
  2. owner/sharer 最终会送达 `RecallResp/InvalidateAck`，或被外层 timeout/retry 替代；
  3. router/backstore 最终会排空 ready 队列并回调 `fill/write/delete ack`。

---

## 1. UBCC 中所有 wait-for 依赖

| 等待体 | 被什么阻塞 | 证据 | 解除事件 |
|---|---|---|---|
| 新的 outer request | 同 PA live outstanding | `UBCCController.cc:430-523`, `1157-1171` | live outstanding 退休 / 转终态 |
| 不同 requester 的请求 | `_pendingRequesters[linePa]` 前驱事务 | `427-523`, `2467-2531` | 前驱事务 `Clear/UpgradeDone` 后 replay |
| resident waiter | `fillPending/wbPending` | `141-168`, `200-208`, `302-344` | `onBackstoreFillComplete/WriteAck/DeleteAck` |
| `RECALL/WAITING_TARGET_RESP` | owner 的 `RecallResp` | `859-871`, `1058-1153` | `processRecallResponse()` |
| `RECALL/DONE` | **同 requester retry** 把 DONE recall 消费成 grant | `712-759`, `fv3_outstanding_lifecycle.md:60-63` | requester 重试同 PA |
| `INVALIDATE/WAITING_ALL_ACKS` | 所有目标 sharer 的 `InvalidateAck` | `658-680`, `1196-1415` | 最后一个 ack 到达 |
| `GRANT_HANDSHAKE/WAITING_CLEAR` | requester 的 `Clear` | `688-701`, `731-759`, `879-907`, `1961-2100` | `processClear()` 接受匹配 tuple |
| `UPGRADE_PENDING/WAITING_ALL_ACKS` | 其他 sharer 的 `InvalidateAck` | `1783-1833`, `1196-1386` | 最后一个 ack 到达 |
| `UPGRADE_PENDING/WAITING_LOCAL_DONE` | requester 的 `OuterUpgradeDone` | `1833-1853`, `1857-1955` | `processOuterUpgradeDone()` |
| home writeback completion notify | 同 PA live outstanding | `1596-1603` | `isLineBusy()==false` |
| writeback / evict | 同 PA live outstanding | `1518-1525`, `1650-1657` | 前驱 outstanding 退休 |

补充：`createOutstanding()` 明确每条 line 只允许一个 live outstanding（`2545-2578`），因此所有等待最终都会汇聚到“当前 line 的唯一前驱对象”。

---

## 2. wait-for graph（按 Outstanding 阶段）

```text
ResidentWaiter
  -> Fill/WB callback
  -> processOuterRequest/processWriteback/processEvict

QueuedRequester
  -> current live Outstanding
  -> replayPendingRequesters
  -> fresh processOuterRequest

RECALL/WAITING_TARGET_RESP
  -> owner RecallResp
  -> RECALL/DONE
  -> same-requester retry
  -> GRANT_HANDSHAKE/WAITING_CLEAR
  -> requester Clear
  -> COMMIT/REMOVE

INVALIDATE/WAITING_ALL_ACKS
  -> all InvalidateAck
  -> GRANT_HANDSHAKE/WAITING_CLEAR (in-place, replayArmed=1)
  -> requester retry hit grant
  -> requester Clear
  -> COMMIT/REMOVE

UPGRADE_PENDING/WAITING_ALL_ACKS
  -> all InvalidateAck
  -> UPGRADE_PENDING/WAITING_LOCAL_DONE
  -> requester UpgradeDone
  -> COMMIT/REMOVE
```

关键实现锚点：

- same-requester + `replayArmed` + `WAITING_CLEAR` 精确 tuple 命中时直接返回 grant，而不是继续 BUSY（`447-464`）。
- `INVALIDATE` 在最后一个 ack 后**原地**转为 `GRANT_HANDSHAKE/WAITING_CLEAR`，并置 `replayArmed=1`（`1388-1403`）。
- `RECALL` 收到响应后只到 `DONE`，随后由同 requester retry 消费为新的 `GRANT_HANDSHAKE`（`712-759`, `1124-1143`）。
- `UPGRADE_PENDING` 采用 `WAITING_ALL_ACKS -> WAITING_LOCAL_DONE -> DONE`，并允许 early-Done 缓存（`1332-1386`, `1892-1910`）。

---

## 3. 无环性检查

### 3.1 结论

在上述公平性假设下，**UBCC 的单-line wait-for graph 无内部环**。存在的“等待链”都是单调前进链，而不是互相反压回到前一阶段的环。

### 3.2 为什么无环

可用以下偏序理解：

`Resident/Queue 前置等待` → `Barrier 等待(RECALL/INVALIDATE/UPGRADE_ACKS)` → `Completion 等待(Clear/UpgradeDone/requester retry)` → `Commit/Remove` → `Replay 下一请求`

代码上没有以下回边：

1. **`WAITING_CLEAR -> WAITING_ALL_ACKS/WAITING_TARGET_RESP` 不存在**。`processClear()` 一旦成功直接 commit+remove（`2076-2088`）。
2. **`WAITING_LOCAL_DONE -> WAITING_ALL_ACKS` 不存在**。upgrade 只允许前进到 commit；early Done 只是缓存，不回退阶段（`1892-1910`）。
3. **`INVALIDATE` 不重新创建第二个 live outstanding**。最后一个 ack 后原地改成 `GRANT_HANDSHAKE`（`1393-1398`），避免 create/remove race 形成自锁。
4. **`RECALL.DONE` 不再把 same-requester retry 重新送回新的 `RECALL`**。当前代码先 remove terminal recall，再创建 grant handshake（`721-759`）。
5. **每条 line 最多一个 live outstanding**（`2548-2550`），排除了“两个 outstanding 互等”。

### 3.3 需要公平性而非代码自闭环的边

- `RECALL/DONE -> same-requester retry`
- `GRANT_HANDSHAKE/WAITING_CLEAR -> requester Clear`
- `RECALL/WAITING_TARGET_RESP -> owner RecallResp`
- `INVALIDATE/WAITING_ALL_ACKS -> all sharer acks`
- `UPGRADE_PENDING/WAITING_LOCAL_DONE -> requester UpgradeDone`
- `ResidentWaiter -> backstore callback`

这些边都依赖外部 actor；但它们**不互相形成协议内闭环**。

---

## 4. 已修死锁场景复核

### 4.1 TC2：`RECALL.DONE` / `replayArmed` 闭环

旧问题见 `drift_in_progress.md:D-11`、`fv3_outstanding_lifecycle.md:79-82`：`processRecallResponse()` 把 outstanding 置 `DONE`，但 retry 若再看到旧 owner，会重新走 recall/busy，导致“home 等 retry，requester 一直拿不到可消费 grant”。

当前代码已打断该环：

- `RECALL.DONE` 被 same-requester retry 命中时，先 `removeOutstanding(line_pa)`，再创建 `GRANT_HANDSHAKE/WAITING_CLEAR`（`721-759`）；
- replay 创建出的 grant 会打 `replayArmed=1`（`2513-2519`）；
- same-requester 对 `WAITING_CLEAR` 的精确重试直接返回 grant（`447-464`）。

结论：**TC2 的协议内死锁环已解除**；剩余风险只在“requester 永不 retry”这一公平性外部假设之外。

### 4.2 TC7：barrier 伪死锁不属于 UBCC wait-for 环

`drift_in_progress.md:D-34` 与 `error_root_cause_v4.md:128-192` 表明，TC7 的主要问题是：

- `sync_wait()` 按线程数而非节点数计数；
- 同时它只提供线程同步，不保证 cache writeback / DDR4 可见性。

这会制造“测试先后关系错乱”或“读旧值”，但**不是 UBCC outstanding 之间互等形成的协议死锁环**。D-34 将 barrier 参与者限制到 primary CPU 后，这个外部伪环已解除；协议内 wait-for graph 本身无新增回边。

### 4.3 TC10：upgrade barrier 环已解除

`upgrade_invalidate_fix.md:65-177` 的旧问题是：`OuterUpgradeAck(true)` 过早发出，home / requester / HN-F 的 barrier 顺序混乱，可能形成“home 等 Done、requester 还未被合法放行”的卡死。

当前代码：

- 先冻结 `targetMask`，进入 `WAITING_ALL_ACKS`（`1783-1823`）；
- 最后一个 ack 后才 `accepted=true`、发送 `UpgradeAckNotify`、转 `WAITING_LOCAL_DONE`（`1332-1364`）；
- `Done` 早到则只缓存，不提前提交（`1892-1910`）；
- 真正 commit 仅发生在 `WAITING_LOCAL_DONE`（`1921-1953`）。

结论：**TC10 对应的 upgrade barrier 环已被顺序化拆开**。

---

## 5. 剩余 livelock / 无限重试风险

1. **`RECALL.DONE` 无超时回收**：`fv3_outstanding_lifecycle.md:79-82` 已指出，若 requester 崩溃或停止 retry，DONE 对象会长期留在 map 中并 pin 住该 line。这是公平性之外的真实活性缺口。  
2. **`RecallResp/InvalidateAck` 丢失后，controller 内无 timeout handler**：`createOutstanding()` 虽设置了 `deadlineTick`（`2568-2570`），但本文件没有统一超时扫描/撤销逻辑。若 completion 永不到达，请求者可能无限 retry 而无进展。  
3. **`GRANT_HANDSHAKE/WAITING_CLEAR` 依赖 requester 最终发 `Clear`**：虽然 stale Clear 会退休旧 handshake（`2022-2034`），但“完全不来 Clear”仍会一直 pin line。  
4. **resident/backstore 路径也可饿死**：`fillPending/wbPending` 若回调缺失，`residentWaiters` 会一直停在队首（`302-344`, `2291-2348`）。  
5. **队列公平性依赖外层 actor，不是强证明**：`replayPendingRequesters()` 每次只推进到“创建了一个新 live outstanding”为止（`2510-2521`）；若这个新 outstanding 长期不完成，后继请求会持续饥饿。

---

## 6. 建议的 liveness 插桩点

最小建议：

| 位置 | 建议记录 |
|---|---|
| `UBCCController.cc:430-523` | `BUSY/enqueue/dup_retry/merge`，含 `requester, reqId, depth, existing(stage/opType)` |
| `712-759` | `RECALL.DONE -> GRANT_HANDSHAKE` 消费事件；是否复制 `dataBuf` |
| `1124-1127` | recall barrier 释放时间；`createTick -> respTick` 延迟 |
| `1275-1415` | ack bitset、剩余 ack 数、`INVALIDATE->GRANT` / `UPGRADE_ACK_READY` 转移 |
| `1892-1910` | early `UpgradeDone` 缓存次数与停留时长 |
| `1961-2088` | `Clear` 接受/拒绝原因；`WAITING_CLEAR` 停留时长 |
| `2467-2531` | pending queue replay 次数、头阻塞时长、被谁卡住 |
| `2545-2585` | per-line outstanding create/remove live-count、age、deadline overrun |
| `302-344`, `2291-2348` | resident waiter / fillPending / wbPending 积压时长 |
| `UBRouter.cc:115-223` | ready 队列停留时长，区分“未 ready”与“已 ready 未送达” |

建议新增两个报警阈值：

- `outstanding_age > deadlineTick` 时打印 `PA, opType, stage, requester, reqId`；
- `pendingRequesters/residentWaiters` 队首停留超过阈值时打印它所等待的前驱对象。

---

## 7. 最终结论

1. **协议内 wait-for graph（按单 line）当前是无环的。**
2. **TC2 的 replay/recall 闭环、TC10 的 upgrade barrier 环已在代码中打断。**
3. **TC7 的 barrier 问题属于 workload/可见性层，不属于 UBCC 协议内死锁环。**
4. 真正剩余的活性风险主要不是“环”，而是**缺少超时/回收导致的永久等待或无限 retry**：`RECALL.DONE` 泄漏、`RecallResp/InvalidateAck/Clear` 丢失、backstore callback 缺失。
5. 因此，FV-5 的结论应表述为：**在公平 retry + completion 最终到达假设下，UBCC 无协议内死锁；但当前实现仍缺少若干 timeout/GC 机制，故对 crash/loss 场景只能给出条件式活性结论。**
