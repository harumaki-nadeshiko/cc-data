# Recall Specification v4：CC-EP Recall Protocol 完整规范

**状态**：Phase C 合成版  
**适用范围**：`scheme_v4.md` + `local_dsm_routing_v4.md` 下的 home-centric recall 实现  
**约束来源**：Q1-Q20 全量决策

---

## 1. Recall 语义定义

### 1.1 Recall 是什么

在 CC-EP v4 中，**Recall 不是单纯“把数据拿回来”**，也不是单纯“撤销旧 owner 权限”。  
**Recall = 权限重配置（permission reconfiguration） + 数据取回（data retrieval）的同一个原子协议事件。**

它的规范含义是：

1. home UBCC 发现当前 committed 目录状态为 `G_E/G_M`，且有其他节点请求该行；
2. home 发起对旧 owner 的回调；
3. 旧 owner 必须通过 **EP-RNF → HN-F → 本地 CHI/L2** 正规路径完成失权；
4. 若该行可能含最新数据，则数据必须沿外部链路返回；
5. home 只有在**权限已按 CHI 语义回收，且数据已进入 home 侧可服务的数据缓冲层**后，才可视为 recall 完成。

### 1.2 Recall 何时触发

Recall 只在 committed 状态为 `G_E` 或 `G_M` 且请求者不是当前 owner 时触发：

- **read recall**：`ReadShared × (G_E/G_M)`
- **write recall**：`ReadUnique × (G_E/G_M)`

以下情形**不是 recall**：

- `G_S × ReadUnique` 的 sharer invalidation
- 普通 writeback / evict
- requester 自己就是 owner 的 self-shortcut

### 1.3 Recall 保证什么

Recall 完成后，协议必须保证：

1. **旧 owner 权限已重配置完成**；
2. **最新数据已被取回到 home 侧 EPBackend 缓冲层**；
3. 后续 grant 不再依赖旧 owner cache 仍然保留最新值；
4. grant 的数据源变成 **home buffer / HomeMemoryService**；
5. committed 目录**仍然不在 recall 点提交**，提交点仍是后续 `Clear` 被 home 接受。

---

## 2. Recall 子类型

## 2.1 Read Recall

### 触发条件

- committed：`G_E` 或 `G_M`
- 新请求：其他节点 `ReadShared`

### 语义动作

旧 owner 被降级，目标结果是：

- 旧 owner：`Owner -> Shared`
- 新 requester：`Shared`
- home DDR4 / home buffer：持有可作为 shared 真值源的 clean 副本

### 规范结果

- 若旧 owner 持 dirty 数据，必须先把数据回流到 home 侧
- recall 完成后 intended 目录结果为：
  - `state = G_S`
  - `sharers = {oldOwner, requester}`
  - `owner = -1`
  - `dirty = false`

### 核心要求

**read recall 必须更新 home DDR4 可见值。**  
工程上通过“先写入 home EPBackend buffer，buffer eviction 再写回 DRAM”实现；语义上等价于“home memory 被刷新到最新值”。

## 2.2 Write Recall

### 触发条件

- committed：`G_E` 或 `G_M`
- 新请求：其他节点 `ReadUnique`

### 语义动作

旧 owner 被失效，新写者获得唯一副本：

- 旧 owner：`Owner -> I`
- 新 requester：`Exclusive/Modified`
- 最新数据通过 recall 返回，并最终由新写者接管

### 规范结果

recall 完成后 intended 目录结果为：

- `ReadUnique + writeIntent=false`：`G_E(owner=requester, dirty=false)`
- `ReadUnique + writeIntent=true`：`G_M(owner=requester, dirty=true)`

### 核心要求

**write recall 不是 functionalRead。**  
它必须通过 `EP-RNF.startReadUnique()` 进入 HN-F，由 HN-F 对旧 owner/L2 发起 invalidating 流程，真正撤销旧 owner 权限。

---

## 3. 消息时序图

以下示例采用：

- Node0：home 节点，且当前 owner 位于 Node0 本地 cache hierarchy
- Node1：requester 节点
- 地址：`DSM_0`，home 在 Node0

## 3.1 Read Recall 时序

```text
Node1 CPU/L2
   │ ReadShared(DSM_0)
   ▼
HN-F1
   │ ReadNoSnp + sideband(Shared, !writeIntent)
   ▼
EP-SNF1
   ▼
EPBackend1 ---------------------------> UBCC0(home)
                                          │
                                          │ Dir: G_E/G_M, owner=Node0
                                          │ create RECALL
                                          ▼
                                     EPBackend0(home-side)
                                          ▼
                                     EP-RNF0
                                          ▼
                                      HN-F0
                                          ▼
                                       L2_0(owner)
                                          │
                                          │ downgrade to Shared
                                          │ return latest data
                                          ▲
                                      HN-F0/EP-RNF0
                                          ▲
                              RecallResponse(data, success)
                                          │
EPBackend0(home buffer install) <---------┘
   │
   │ forward recall payload / grant data
   ▼
UBCC0(home) -------- UBCC link --------> UBCC1(requester-side transit)
                                           ▼
                                       EPBackend1(grant staging)
                                           ▼
                                       EP-SNF1
                                           ▼
                                        HN-F1
                                           ▼
                                      CPU/L2_1 gets CompData_SC

EPBackend1 ---- Clear(pa, epoch, reqId) ----> UBCC0
UBCC0 commit intended result: G_S{Node0, Node1}
```

### 语义说明

1. recall 目标是 **Node0 的 cache hierarchy**，不是 direct memory probe；
2. 数据先进入 **home EPBackend buffer**；
3. requester 侧可有瞬时 grant staging，但权威缓冲层在 home；
4. `Clear` 到达前 committed 目录仍保持旧值；
5. `Clear` 被接受后才真正提交为 `G_S`。

## 3.2 Write Recall 时序

```text
Node1 CPU/L2
   │ ReadUnique / write miss(DSM_0)
   ▼
HN-F1
   │ ReadNoSnp + sideband(Unique, writeIntent?)
   ▼
EP-SNF1
   ▼
EPBackend1 ---------------------------> UBCC0(home)
                                          │
                                          │ Dir: G_E/G_M, owner=Node0
                                          │ create RECALL
                                          ▼
                                     EPBackend0(home-side)
                                          ▼
                                     EP-RNF0.startReadUnique()
                                          ▼
                                      HN-F0
                                          ▼
                                       L2_0(owner)
                                          │
                                          │ invalidate old owner
                                          │ return latest data
                                          ▲
                                      HN-F0/EP-RNF0
                                          ▲
                              RecallResponse(data, success)
                                          │
EPBackend0(home buffer install) <---------┘
   │
   │ forward grant payload
   ▼
UBCC0(home) -------- UBCC link --------> UBCC1(requester-side transit)
                                           ▼
                                       EPBackend1(grant staging)
                                           ▼
                                       EP-SNF1
                                           ▼
                                        HN-F1
                                           ▼
                                     CPU/L2_1 gets CompData_UC

EPBackend1 ---- Clear(pa, epoch, reqId) ----> UBCC0
UBCC0 commit intended result: G_E/G_M(owner=Node1)
```

### 语义说明

1. write recall 的关键不是“取数”，而是**旧 owner 真的失效**；
2. `ReadUnique` 是 write recall 的标准入口；
3. grant 发出后，最新数据来源已经不再依赖旧 owner，而依赖 home buffer；
4. committed owner 的更新仍在 `Clear` 点完成。

---

## 4. Home EPBackend Buffer 规范

## 4.1 设计定位

home 节点的 `EPBackend` 持有一个**多项（multi-entry）recall/writeback buffer**，它是 local DSM backing store 的上层缓冲，语义上相当于：

- inclusive writeback cache
- home-side recall landing zone
- grant data 的首选来源

它属于 `EPBackend` 管理域，而不是 HN-F 可见的 downstream memory target。

## 4.2 HomeMemoryService 定义

`HomeMemoryService` 物理上位于 `EPBackend` 层，可以：

- 作为 `EPBackend` 的内部子模块；或
- 作为独立模块，但与 `EPBackend` 紧耦合

其语义接口为：

```text
HomeMemoryService.read(pa):
    1) 先查 EPBackend buffer
    2) 未命中再读本地 DSM DRAM

HomeMemoryService.write(pa, data):
    1) 更新/分配 EPBackend buffer entry
    2) 脏行后写回 DRAM
```

因此：

- **buffer lookup 优先于 DRAM**
- DRAM 是 backing store，不是 recall 完成前的唯一真值源

## 4.3 Buffer entry 字段

每个 entry 至少包含：

- `linePa`
- `valid`
- `dirty`
- `data[64B]`
- `lastAccessTick / lruCounter`
- `source`（Recall / Writeback / HomeFill）
- `epochTag`（调试/审计用途，非 committed 目录替代）

## 4.4 Buffer 行为规范

1. **多项缓存**，不可退化为 single-entry scratch buffer；
2. **LRU eviction**；
3. eviction 前若 `dirty=true`，必须先写回 home DRAM；
4. recall response 到达后，先 install 到 home buffer，再允许后续 grant materialize；
5. writeback 也写入同一 buffer，形成 read recall / write recall / writeback 的统一数据平面。

## 4.5 权威性边界

Q9/Q20 的收敛定义是：

- **Shared 真值源优先级**：`Home EPBackend Buffer > Home DRAM`
- HomeMemoryService 是共享读的统一入口
- UBCC 只负责 order + directory，不直接摸 DDR4

---

## 5. RECALL DONE 语义

在 v4 中，`RECALL.DONE` 的规范条件是：

> **匹配的 RecallResponse 已被 home 接收，且返回数据已成功安装到 home EPBackend buffer。**

这一定义包含两个必要条件：

1. **控制面完成**：tuple 匹配成功  
   - `linePa`
   - `ownerNode`
   - `epoch`
   - `reqId`

2. **数据面完成**：若 `dataNeeded=true`，则数据已进入 home buffer

仅有控制面 ack、没有 buffer install，**不能视为 DONE**。

### 5.1 与 grant 的关系

- `RECALL.DONE`：只表示 prerequisite barrier 已释放
- `GRANT_HANDSHAKE`：表示可以开始发 grant / 等待 Clear
- `Clear accepted`：才是 committed 目录提交点

---

## 6. Recall 生命周期状态机

## 6.1 主状态机

```text
CREATED
  │  home 发出 OuterRecallMsg
  ▼
WAITING_TARGET_RESP
  │
  ├─ 收到匹配 RecallResponse + data install success ──> DONE
  │
  ├─ timeout + retry budget 未耗尽 ────────────────> WAITING_TARGET_RESP
  │      (重发给同一 owner)
  │
  └─ timeout + retry budget 耗尽 ─────────────────> TIMED_OUT
```

## 6.2 DONE 后的握手语义

`DONE` 不是最终 grant 提交态，而是：

```text
RECALL.DONE
   │ requester retry / home re-evaluate
   ▼
GRANT_HANDSHAKE(WAITING_CLEAR)
   │
   └─ Clear accepted -> commit intended DirEntry
```

即：

- recall 负责释放旧 owner 依赖；
- grant_handshake 负责把新权限提交到 committed 目录。

## 6.3 状态说明表

| 状态 | 含义 |
|---|---|
| `CREATED` | 已创建 recall outstanding，尚未发往 target |
| `WAITING_TARGET_RESP` | 已发给旧 owner，等待响应与数据安装 |
| `DONE` | recall prerequisite 已满足，可进入 grant 阶段 |
| `TIMED_OUT` | 重试预算耗尽，PA 进入故障阻塞态 |

---

## 7. 竞态窗口与处理规则

## 7.1 Q16：requester 在 recall 期间重试

规则：**BUSY until RECALL→DONE**。

含义：

- 当同一 PA 已存在 `RECALL` outstanding 且未到 `DONE`，后续 requester retry 一律收到 `BUSY/RETRY`；
- 不做 merge，不做旁路 grant，不重新创建第二个 recall。

## 7.2 Q17：GRANT_HANDSHAKE 期间 buffer eviction

规则：**允许 eviction，但必须先写回 DRAM**。

含义：

- home buffer 在 handshake 窗口不是 pin 住不可驱逐；
- 但 eviction dirty entry 前必须完成 DRAM writeback；
- 因此 grant 数据源即使从 buffer 退回 DRAM，也仍保持正确。

## 7.3 Q18：第一次 recall 尚未 Clear，第二次 recall 到达

规则：**PA 级串行阻塞（PA serial block）**。

含义：

- 同一 PA 只允许一个 recall/grant 生命周期活跃；
- 第二个 recall 不得与第一个并行；
- 返回 `BUSY/RETRY`，直到前一条生命周期退休。

## 7.4 Q19：owner 超时

规则：**timeout + retry N 次，之后 TIMED_OUT**。

含义：

- home UBCC 对同一 owner 重发 recall；
- 复用同一 `(linePa, epoch, reqId)`；
- 超预算后转 `TIMED_OUT`，该 PA 持续 busy/fenced，并输出 fatal-grade 审计。

## 7.5 Q20：本地写与 buffer/DRAM 的可见性竞争

规则：**HomeMemoryService.read() 先查 buffer，再查 DRAM**。

含义：

- 刚完成 recall/writeback 的数据可能尚未 eviction 到 DRAM；
- 若直接查 DRAM，会看到 stale 值；
- 因此 local DSM 读在 EPBackend 层必须先查 buffer。

---

## 8. 与现有代码的集成修改

本节描述的是**规范性改造方向**，用于替换当前 prototype 中的临时路径。

## 8.1 `EPBackend::handleRecallRequest()`

### 当前问题

当前 `ep/EPBackend.cc` 的 `handleRecallRequest()` 仍以：

- `functionalRead()` 抽取数据
- 向所有节点 `phys_mem` 广播写入
- 直接 `sendRecallResponse()`

来模拟 recall 完成。该实现绕过了 HN-F，不满足 write recall 的权限回收语义。

### 必要修改

1. **删除 recall 正式路径中的 `functionalRead + phys_mem broadcast`**；
2. 根据 recall 类型改为：
   - read recall → `EP-RNF.startReadShared(ownerLocalPa, cb)`
   - write recall → `EP-RNF.startReadUnique(ownerLocalPa, cb)`
3. callback 返回后再组装 `OuterRecallResponse`；
4. response 需携带数据 payload，而不是只有 `dataReturned` 布尔；
5. home 侧 `EPBackend` 在收到 response 后，将数据 install 到 home buffer；
6. `populateGrantData()` 从“全系统 probe”重写为“按 `GrantDataSource` 取 `RecallBuffer/HomeMemoryService`”。

### 结果

`handleRecallRequest()` 将从“伪造 recall 完成”改成“发起真实 CHI recall 并等待完成”。

## 8.2 `EPRNFController`

### 当前问题

`startReadShared()` / `startReadUnique()` 已存在，但 callback 只返回 `bool success`，不足以承载 recall 数据。

### 必要修改

1. 将 recall completion callback 扩展为**可携带数据的回调**，至少包括：
   - `success`
   - `dataValid`
   - `DataBlock/byte[64]`
   - `isDirty / wasDirty`（可选，但建议显式）
2. 在 `recvDataMsg()` 中把 `ReadShared` / `ReadUnique` 的数据 beat 聚合到 `PendingChiTxn`；
3. 在 `finishChiTxn()` 中把聚合好的 payload 交给 callback；
4. `ReadUnique` 完成条件必须是：
   - 所有 data beat 到齐
   - `Comp_UC` 到齐
5. 对 `outerTxnPending` 保持保守：当外部事务未完成时，延迟向 HN-F 返回影响目录推进的响应。

### 结果

EP-RNF 成为 recall 的真实 owner-side CHI 执行器，而不是仅发控制请求的壳层。

## 8.3 `UBCCController`

### 当前问题

当前 `processRecallResponse()` 只把 `RECALL` 标记为 `DONE`，并记录 `dataValid=bool`，没有把“数据已安装到 home buffer”纳入完成条件。

### 必要修改

1. `OutstandingRequest(RECALL)` 增加/明确：
   - `recallType`
   - `retryBudget`
   - `bufferInstallDone`
   - `dataLen / dataBufRef` 或跨模块引用
2. `processRecallResponse()` 的完成条件改为：
   - tuple 匹配成功
   - 若 `dataNeeded`，则 home buffer install 成功
3. 在 `RECALL.DONE` 后允许：
   - requester retry 命中 `DONE` recall，转入 `GRANT_HANDSHAKE`
   - 或 home 主动推进 grant-ready 状态
4. timeout/retry 明确使用原 `(epoch, reqId)`；
5. `TIMED_OUT` 后保持该 PA busy/fenced，不允许错误放行后续 grant。

### 结果

UBCC 的 recall 生命周期从“只看控制面”升级为“控制面 + 数据面共同闭环”。

## 8.4 `EPSNFController`

### 当前问题

当前 `EPSNFController` 仍允许在 grant data 不可靠时 fallback 到零数据或临时数据来源，这不适合 recall-heavy 路径。

### 必要修改

1. 引入明确的数据源枚举：
   - `GrantDataSource::RecallBuffer`
   - `GrantDataSource::HomeMemory`
   - `GrantDataSource::NoData`
2. shared/unique grant 发数前，必须从 EPBackend 获得**已 materialize 的 grant 数据**；
3. `HomeMemory` 路径通过 `HomeMemoryService.read()` 取数；
4. `RecallBuffer` 路径直接取 home buffer / requester grant staging 中的 recall payload；
5. 保留 deferred CompData 机制，但不再允许用 `functionalRead` 或全局 probe 兜底。

### 结果

EPSNFController 只负责把**已经确定的数据源**封装成 CHI `CompData_*` 发回 HN-F。

---

## 9. 最终收敛结论

v4 recall 的最终定义可归纳为一句话：

> **Recall 是 home 发起的、经 owner 节点 HN-F 正式执行的权限回收与数据回收原子事件；它先把最新数据稳定到 home EPBackend buffer，再由后续 grant/clear 完成目录提交。**

这一定义同时满足：

- Q1：权限重配置 + 数据 retrieval 同一事件
- Q5/Q6/Q12/Q20：home buffer / HomeMemoryService 为数据真值入口
- Q7/Q13：数据通过 `EPBackend ↔ UBCC ↔ EPBackend` 外部链路返回
- Q8：DONE = RecallResponse + home buffer install
- Q16-Q19：recall 窗口内保持 BUSY、串行化、超时重试、失败 fenced
