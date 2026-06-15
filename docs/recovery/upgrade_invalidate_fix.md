# Upgrade + Invalidate 修复规范（强序列化升级版）

**状态**：Phase C 定稿  
**适用基线**：`docs/recovery/scheme_v4.md`、`docs/recovery/recall_done_fix.md`  
**输入决策**：用户 Round 2 的 Q1/Q2/Q3/Q4/Q5 结论  
**目标**：修复本地 sharer 升级到 unique 时的 home-side invalidation/ack 时序错误，以及 replay/queued grant 的 `Clear` tuple 错绑问题。

---

## 1. 本次定稿的五条规范性结论

### D1. `UPGRADE_PENDING` 采用**单对象、双阶段**

同一 PA 的本地升级只允许一个 live outstanding，对象类型固定为 `UPGRADE_PENDING`，阶段机为：

```text
CREATED -> WAITING_ALL_ACKS -> WAITING_LOCAL_DONE -> DONE
```

若无其他 sharer，可直接跳过 `WAITING_ALL_ACKS` 进入 `WAITING_LOCAL_DONE`。

### D2. `OuterUpgradeAck(true)` 必须**延迟到所有 InvalidationAck 收齐之后**

这是本修复最重要的约束。home UBCC 不得在 invalidation fanout 刚发出时就回 Ack(true)。只有当：

```text
ackMask == targetMask
```

时，才允许向 requester 发送 `OuterUpgradeAck(true)`。

### D3. `targetMask` 在 upgrade acceptance 时冻结

冻结规则：

```text
targetMask = entry.sharersMask & ~reqBit
```

冻结后整个升级过程都以该快照为准；后续 committed 目录中的 sharer 变化不得回写影响本次升级需要等待的 ack 集合。

### D4. `OuterUpgradeDone` 可先接收后缓存，但仅在最后一个 ack 到达时提交

**该条在本文中标为 TENTATIVE。**

若 `Done` 早于最后一个 `InvalidationAck` 抵达，home UBCC：

1. 不拒绝该 `Done`；
2. 缓存其 tuple；
3. 等最后一个 `InvalidationAck` 到达后再真正 commit。

按 D2 的强序列化正常路径，这种情况理论上不应成为常态；它只是防御式实现。

### D5. `Clear` 必须使用**独立 pending grant transaction context** 里的 `baseEpoch`

`Clear` 不得再读取 `RequesterLineEntry.epoch` 作为发送 tuple。  
必须使用与当前 live `GRANT_HANDSHAKE` 一一绑定的 grant context 中保存的 `baseEpoch`。

这条同时覆盖 replay 场景：queued/replayed request 的 `baseEpoch` 可能已被 home rebase，不能继续沿用 requester 本地旧 entry 里的 epoch。

---

## 2. 需要修复的当前错误

### 2.1 升级路径当前错误

当前代码中：

- `UBCCController::processOuterUpgradeReq()`（`gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:1201+`）创建 `UPGRADE_PENDING` 后直接进入 `WAITING_LOCAL_DONE`；
- 没有显式 fanout/等待“其他 sharer 的 invalidation 全部完成”；
- `EPRNFController::handleSnpCleanInvalid()`（`.../EPRNFController.cc:664+`）在 `notifyLocalWriteUpgrade()` 返回成功后立即调用 `receiveUpgradeAck()`；
- 等价于把 `OuterUpgradeAck(true)` 提前发给 requester；
- 这违反了 D2。

### 2.2 ack 处理对象类型错误

当前 `processInvalidationAck()`（`UBCCController.cc:854+`）只接受 `OpType::INVALIDATE`，不接受 `UPGRADE_PENDING`，因此本地升级 fanout 无法复用同一套 ack barrier。

### 2.3 target 集合不稳定

当前升级路径未把“本次必须等待哪些 sharer ack”冻结为快照，后续目录变化可能污染完成判定。

### 2.4 replay/queued grant 的 `Clear` tuple 取值错误

当前 `EPBackend.cc:537-608` 中 `OuterGrantEnvelope.epoch` 来源仍是 requester 侧局部 entry，随后 `sendClear()` 也沿用这一值。  
对于 replay/rebase 后的 grant，这会造成：

```text
home live GRANT_HANDSHAKE.baseEpoch != requester 发出的 Clear.epoch
```

从而触发假阴性的 epoch mismatch。

---

## 3. 新的规范语义

## 3.1 升级消息流（最终）

```text
1. Node1 CleanUnique -> HN-F sends SnpCleanInvalid to EP-RNF
2. EP-RNF -> OuterUpgradeReq -> home UBCC
3. home UBCC creates one UPGRADE_PENDING object
4. freeze targetMask = sharersMask & ~reqBit
5. home UBCC fans out invalidations to all nodes in targetMask
6. wait until all InvalidationAck arrive
7. only then: home UBCC -> OuterUpgradeAck(true)
8. EP-RNF -> deferred SnpResp_I -> local HN-F
9. local HN-F completes local upgrade
10. EP-RNF -> OuterUpgradeDone
11. home UBCC commits owner/state/epoch and retires UPGRADE_PENDING
```

### 3.1.1 关键 happens-before

```text
all remote InvalidationAck
    happens-before
OuterUpgradeAck(true)
    happens-before
EP-RNF sends deferred SnpResp_I to local HN-F
    happens-before
OuterUpgradeDone
    happens-before
home commit(owner/state/epoch)
```

### 3.1.2 线性化点

本地升级的线性化点仍然是：

```text
home UBCC 接受匹配的 OuterUpgradeDone
```

不是 `OuterUpgradeReq`，也不是 `OuterUpgradeAck(true)`。

---

## 3.2 `UPGRADE_PENDING` 状态机

### 3.2.1 正常路径

```text
CREATED
  -> (targetMask!=0) WAITING_ALL_ACKS
  -> (all ack received) WAITING_LOCAL_DONE
  -> (matching Done received) DONE
```

### 3.2.2 无其他 sharer 的快路径

```text
CREATED
  -> (targetMask==0) WAITING_LOCAL_DONE
  -> (matching Done received) DONE
```

### 3.2.3 TENTATIVE 提前 Done 路径

```text
WAITING_ALL_ACKS
  -> (Done early) WAITING_ALL_ACKS + doneCached=true   [TENTATIVE]
  -> (last ack arrives) DONE / commit immediately
```

### 3.2.4 不变量

对任一 PA，必须满足：

1. 仅 1 个 live outstanding；
2. `UPGRADE_PENDING` 不得与 `INVALIDATE` 并存；
3. `targetMask` 一经创建不可修改；
4. `ackMask` 只能单调增加；
5. `OuterUpgradeAck(true)` 最多发送一次；
6. commit 前 committed `DirEntry.owner/state/epoch` 不得更新为新 owner；
7. `Done` 只有在 tuple 匹配时才可消费。

---

## 3.3 数据结构修订

## 3.3.1 `UBCCController::OutstandingRequest`

现有结构可沿用，但对 `UPGRADE_PENDING` 必须把以下字段视为**规范必填**：

```cpp
opType              = UPGRADE_PENDING
stage               = WAITING_ALL_ACKS or WAITING_LOCAL_DONE
requesterNode       = upgrader
baseEpoch           = requester-observed committed epoch
reservedEpoch       = entry.epoch + 1
reqId               = requester allocated
targetMask          = frozen sharers snapshot without requester
totalMask           = targetMask
ackMask             = 0 initially
pendingAckCount     = popcount(targetMask)
intendedState       = G_E or G_M
intendedOwnerNode   = requesterNode
intendedSharersMask = 0
intendedDirty       = (writeIntent==true)
accepted            = false initially, true only after all acks
```

### 3.3.2 `UPGRADE_PENDING` 的新增字段

建议新增：

```cpp
bool upgradeAckSent;

// TENTATIVE: only if Q4 is kept
bool doneCached;
uint64_t doneEpoch;
uint64_t doneReqId;
int doneSrcNode;
```

若不想污染通用结构，也可收敛到 `union/optional upgrade-only sidecar`，但语义必须等价。

### 3.3.3 requester 侧独立 grant tuple 上下文

`EPBackend` 必须新增独立于 `RequesterLineEntry` 的 pending grant context，例如：

```cpp
struct PendingGrantTxn {
    bool valid;
    uint64_t linePa;
    int homeNode;
    uint64_t baseEpoch;   // MUST be the home-approved GRANT_HANDSHAKE baseEpoch
    uint64_t reqId;
    OuterGrantType grantType;
};
```

用途：

- 保存“当前这次 grant 对应的 Clear tuple”；
- `sendClear()` 只能读它；
- `RequesterLineEntry.epoch` 退化为 requester 本地观察状态/审计字段，不再是 commit tuple 真值源。

---

## 4. 算法规范

## 4.1 `processOuterUpgradeReq()`

### 4.1.1 输入校验

必须检查：

1. `requesterNode` 当前是 committed sharer；
2. 当前 PA 无其他 live outstanding；
3. `baseEpoch` 与 committed `entry.epoch` 匹配（允许使用现有 half-range 校验框架，但语义上必须验证“不是旧事务重放”）；
4. `desiredPerm` 只能是 unique/exclusive 升级路径允许的值。

### 4.1.2 创建对象

```text
reqBit      = 1 << requesterNode
targetMask  = entry.sharersMask & ~reqBit
reserved    = allocateReservedEpoch(entry)
```

创建 1 个 `UPGRADE_PENDING`：

- `baseEpoch = requester 携带的 epoch`
- `reservedEpoch = reserved`
- `targetMask/totalMask = frozen targetMask`
- `pendingAckCount = popcount(targetMask)`
- `stage = WAITING_ALL_ACKS`（若 `targetMask!=0`）
- `stage = WAITING_LOCAL_DONE`（若 `targetMask==0`）

### 4.1.3 fanout

若 `targetMask != 0`：

- 立刻向每个 target node 派发 invalidation；
- **不得**创建第二个 `INVALIDATE` outstanding；
- **不得**在此时回 `OuterUpgradeAck(true)`。

### 4.1.4 ack 发送规则

- `targetMask == 0`：可立即发送 `OuterUpgradeAck(true)`；
- `targetMask != 0`：只有最后一个 `InvalidationAck` 抵达时才发送 `OuterUpgradeAck(true)`；
- hard reject 才发送/返回 `accepted=false`。

### 4.1.5 committed 目录保护

在 `OuterUpgradeDone` commit 之前：

- 不得把 `ownerNode` 改成 requester；
- 不得把 `state` 改成 `G_E/G_M`；
- 不得把 `epoch` 改成 `reservedEpoch`；
- 不得因为单个 ack 到达就破坏 committed owner/state。

**推荐**：commit 前连 `entry.sharersMask` 也不要边走边删，统一依赖 `ackMask/targetMask` 跟踪进度。

---

## 4.2 `processInvalidationAck()`

当前实现只处理 `OpType::INVALIDATE`；本修复后必须扩展为同时处理：

```text
INVALIDATE
UPGRADE_PENDING (when stage == WAITING_ALL_ACKS)
```

### 4.2.1 针对 `UPGRADE_PENDING` 的行为

1. 校验 `ackNode` 属于 `totalMask`；
2. duplicate ack 命中 `ackMask` 时幂等返回 true；
3. `ackMask |= nodeBit`；
4. `pendingAckCount--`；
5. 当 `pendingAckCount == 0`：
   - `invalidateBarrierDone = true`
   - `accepted = true`
   - `stage = WAITING_LOCAL_DONE`
   - 向 requester 发送 `OuterUpgradeAck(true)`
   - 若 `doneCached==true`（TENTATIVE）则立刻按缓存 Done 尝试 commit

### 4.2.2 禁止的旧行为

`UBCCController.cc:918-920` 当前在 ack 到达时直接：

```cpp
entry.sharersMask &= ~nodeBit;
```

本修复中，对 `UPGRADE_PENDING` 路径**不应再用 committed `entry.sharersMask` 作为实时 ack 进度存储**。

---

## 4.3 `processOuterUpgradeDone()`

### 4.3.1 匹配条件

必须匹配：

```text
opType == UPGRADE_PENDING
requesterNode == srcNode
baseEpoch == done.epoch   // 若协议保留 baseEpoch 语义
reqId == done.reqId
```

若协议实现保留 `Done.epoch` 为 reservedEpoch，也必须全文统一；本文推荐继续沿用“tuple 用 requester/baseEpoch，commit 写 reservedEpoch”的 v4 语义，不再混用。

### 4.3.2 正常提交条件

只有在：

```text
stage == WAITING_LOCAL_DONE
accepted == true
invalidateBarrierDone == true
```

时才允许：

```text
commitIntendedResult(entry, ost)
stage = DONE
removeOutstanding(linePa)
```

### 4.3.3 TENTATIVE：提前 Done

若 `stage == WAITING_ALL_ACKS` 且 tuple 匹配：

- **接受但不提交**；
- 缓存 `Done`；
- 等最后一个 ack 抵达后再 commit。

该行为必须在文档和日志中标记为：

```text
[UPGRADE-TENTATIVE-DONE-CACHED]
```

若最终决定放弃 Q4，该分支应改为 reject。

---

## 4.4 EP-RNF 侧行为

## 4.4.1 `handleSnpCleanInvalid()`

第一次收到本地升级触发的 `SnpCleanInvalid` 时：

1. 建立/更新本地 `UpgradePending` 上下文；
2. 记录 `hnfDest`；
3. 调用 `EPBackend::notifyLocalWriteUpgrade()` 发出 `OuterUpgradeReq`；
4. **不得**立即 `receiveUpgradeAck()`；
5. 必须等待真正的 `OuterUpgradeAck(true)` 回调再向 HN-F 发送 `SnpResp_I`。

### 4.4.2 `receiveUpgradeAck()`

`receiveUpgradeAck()` 的语义改成：

> 只在 home UBCC 已确认“所有其他 sharer 均已 invalidated”之后触发。

触发后：

1. 发送 deferred `SnpResp_I` 给本地 HN-F；
2. 等本地升级完成；
3. 调用 `sendUpgradeDone()`。

### 4.4.3 不变量

- 对同一 PA，EP-RNF 只能有 1 个 upgradePending；
- 没有 `UpgradeAck(true)` 不得发 `SnpResp_I`；
- `SnpResp_I` 发送后才能进入 `Done` 路径。

---

## 4.5 EPBackend 侧行为

## 4.5.1 upgrade 请求接口语义

`notifyLocalWriteUpgrade()` 不能再把“home 接受进队列”和“home 已完成 invalidate barrier”混为一个 `bool accepted`。

本规范要求至少区分三种结果：

```text
Rejected        // 未受理
AcceptedPending // 已建立 UPGRADE_PENDING，但尚未 Ack(true)
AckReady        // targetMask==0 或 ack 已同步收齐
```

实现方式可选：

1. 改返回枚举；或
2. 保持同步函数只表示“已受理”，真正 Ack(true) 通过 callback/消息回到 EPRNF；

但语义必须满足 D2。

### 4.5.2 grant tuple 修复（Q5）

remote miss grant 路径中：

- `OuterGrantEnvelope` 的 `epoch` 字段必须重新定义为 `baseEpoch`；
- 更推荐直接改名为 `baseEpoch`，避免继续歧义；
- `sendClear()` 必须使用 pending grant tx context 中保存的 `baseEpoch` 与 `reqId`；
- 不得再从 `RequesterLineEntry.epoch` 取值。

### 4.5.3 replay 场景要求

若 queued request 被 home replay 且 `baseEpoch` 已 rebase：

- home 必须把 rebased `baseEpoch` 通过 grant envelope 返回 requester；
- requester 必须覆盖/刷新本次 pending grant ctx；
- 之后 Clear 必须使用 rebased 值。

---

## 5. 逐文件修改目录

## 5.1 `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh`

### 当前基线

- 已定义 `OpType::UPGRADE_PENDING`、`OpStage::WAITING_ALL_ACKS/WAITING_LOCAL_DONE`；
- 但字段语义尚未完整支撑“单对象双阶段 + cached done + delayed ack”。

### 必改项

1. 明确 `UPGRADE_PENDING` 复用 `targetMask/totalMask/ackMask/pendingAckCount`；
2. 增加 `upgradeAckSent`；
3. **TENTATIVE**：增加 `doneCached/doneEpoch/doneReqId/doneSrcNode`；
4. 若保留 `OuterGrantEnvelope.epoch` 名称，则在注释中明确它是 `baseEpoch`，不是 reservedEpoch。

---

## 5.2 `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc`

### 当前基线问题点

- `processOuterUpgradeReq()`：`1201-1270`
- `processInvalidationAck()`：`854-951`
- `processOuterUpgradeDone()`：`1274-1323`

### 必改项

1. `processOuterUpgradeReq()`：
   - 冻结 `targetMask = entry.sharersMask & ~reqBit`；
   - `targetMask!=0` 时进入 `WAITING_ALL_ACKS`；
   - 启动 invalidation fanout；
   - 不立即 Ack(true)；
   - `targetMask==0` 时才可直接进入 `WAITING_LOCAL_DONE` 并 Ack(true)。
2. `processInvalidationAck()`：
   - 接受 `UPGRADE_PENDING/WAITING_ALL_ACKS`；
   - 最后一个 ack 到达时发送 `OuterUpgradeAck(true)`；
   - 不再把 committed `entry.sharersMask` 当 ack bookkeeping。
3. `processOuterUpgradeDone()`：
   - 仅在 `WAITING_LOCAL_DONE` 且 barrier 已完成时 commit；
   - **TENTATIVE**：支持 early Done cache。
4. 增加专用审计日志：

```text
[UBCC-UPGRADE] pa=<pa> requester=<n> stage=<stage> targetMask=<m> ackMask=<m>
[UBCC-UPGRADE-ACK] pa=<pa> requester=<n> accepted=<0|1>
[UBCC-UPGRADE-COMMIT] pa=<pa> owner=<n> reservedEpoch=<e>
```

---

## 5.3 `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.hh`

### 必改项

1. `UpgradePending` 上下文增加显式阶段/ack 状态位；
2. 保证保存 `linePa/homeNode/baseEpoch-or-ackEpoch/reqId/hnfDest`；
3. 若支持 TENTATIVE，可加 `localDoneIssued` 或 `waitingDoneAck` 审计字段。

---

## 5.4 `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc`

### 当前基线问题点

- `handleSnpCleanInvalid()`：`664-739`
- `receiveUpgradeAck()`：`1335-1394`

### 必改项

1. 删除/回退 `handleSnpCleanInvalid()` 中“请求一成功就立刻 `receiveUpgradeAck()`”的逻辑；
2. 改为真正等待来自 home 的 `OuterUpgradeAck(true)`；
3. `receiveUpgradeAck()` 的注释与实现统一为“all invalidations complete 后才调用”；
4. `sendUpgradeDone()` 只在 deferred `SnpResp_I` 已发出且本地升级已完成后调用。

---

## 5.5 `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh`

### 必改项

1. 为 upgrade req 结果引入三态语义（枚举或等价机制）；
2. 新增 `PendingGrantTxn`（或等价 sidecar）保存本次 grant 的 `baseEpoch/reqId`；
3. `OuterGrantEnvelope` 注释改成 `baseEpoch` 语义；更推荐直接改字段名。

---

## 5.6 `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc`

### 当前基线问题点

- remote miss 请求建 tuple：`300-345`
- `processOuterRequest` 调用：`403-408`
- grant envelope / Clear：`537-608`
- upgrade 请求：`1302-1371`

### 必改项

1. remote miss grant：
   - 从 home UBCC 接收“本次 live `GRANT_HANDSHAKE` 的真实 `baseEpoch`”；
   - 写入 `PendingGrantTxn`；
   - `sendClear()` 只读 `PendingGrantTxn.baseEpoch`。
2. 不再让 `RequesterLineEntry.epoch` 直接决定 Clear tuple；
3. upgrade 路径：
   - 区分“受理成功”与“Ack(true) 已可发”；
   - 通过 callback/显式通知把真正的 `OuterUpgradeAck(true)` 送到 EPRNF。

---

## 5.7 测试文件

至少更新：

- `tests/ubcc/ep-rnf/test_phase4_local_upgrade.py`

建议新增：

- `tests/ubcc/ep-rnf/test_upgrade_delayed_ack.py`
- `tests/ubcc/ep-rnf/test_clear_replay_baseepoch.py`

---

## 6. 测试矩阵

## 6.1 必测路径

### TC-U1：双 sharer 升级，Ack 延迟

场景：`G_S, sharers={Node1, Node2}, requester=Node1`  
期望：

1. home 建 `UPGRADE_PENDING(WAITING_ALL_ACKS)`；
2. 向 Node2 发 invalidate；
3. 未收到 Node2 ack 前，不得出现 `OuterUpgradeAck(true)`；
4. 收到最后一个 ack 后才允许 Ack(true)。

### TC-U2：冻结 targetMask

场景：upgrade 已 accepted 后，目录侧发生无关 sharer 变化/重试。  
期望：完成判定只看 acceptance 时冻结的 `targetMask`。

### TC-U3：无其他 sharer 快路径

场景：`G_S, sharers={requester}`。  
期望：直接 `WAITING_LOCAL_DONE`，允许立即 Ack(true)，但仍只在 `Done` 时 commit。

### TC-U4：提前 Done 缓存（TENTATIVE）

场景：人为构造 `Done` 先于最后一个 ack。  
期望：

- 记录 `[UPGRADE-TENTATIVE-DONE-CACHED]`；
- 不提前 commit；
- 最后一个 ack 到达后再 commit。

### TC-G1：replay grant 的 Clear tuple

场景：queued request 被 replay，home rebase 了 `baseEpoch`。  
期望：requester 发出的 `Clear.epoch == home live GRANT_HANDSHAKE.baseEpoch`。

### TC-G2：旧 `RequesterLineEntry.epoch` 不得污染 Clear

场景：在同一 requester line 上制造旧 epoch 残留。  
期望：`sendClear()` 仍使用 pending grant tx context，而非旧 entry。

---

## 7. 风险与缓解

## 7.1 风险：upgrade 与 invalidate 混成两个 live 对象

**缓解**：明确“本地升级只允许 1 个 `UPGRADE_PENDING`，不得另建 `INVALIDATE`”。

## 7.2 风险：边 ack 边改 committed sharers，破坏 reserve-then-commit

**缓解**：对 upgrade 路径，ack 进度只写 `ackMask/targetMask`；最终目录只在 `Done` commit。

## 7.3 风险：EPRNF 提前发 `SnpResp_I`

**缓解**：把 `receiveUpgradeAck()` 从“本地立即调用”改成“真正收到 home Ack(true) 才调用”。

## 7.4 风险：replay 后 Clear epoch 继续取本地旧 entry

**缓解**：引入 `PendingGrantTxn.baseEpoch` 作为唯一 Clear tuple 来源。

## 7.5 风险：Q4 的 early Done cache 语义不稳定

**缓解**：本文已明确标记为 **TENTATIVE**；实现时必须：

- 单独日志；
- 单独测试；
- 若后续验证不通过，可退回“early Done reject”策略，而不影响 D1/D2/D3/D5。

---

## 8. 最终落地准则

实现完成后，必须能回答以下三个问题且答案均为“是”：

1. **本地升级时，是否只有在所有其他 sharer 都确认失效后，HN-F 才能收到 EP-RNF 的 `SnpResp_I`？**
2. **同一 PA 的升级过程，是否始终只有一个 `UPGRADE_PENDING` live object？**
3. **任何 replay/queued grant 的 `Clear`，是否都严格使用 home 这次 grant 绑定的 `baseEpoch`，而不是 requester 本地旧 entry.epoch？**

若任一答案为“否”，则本修复未完成。
