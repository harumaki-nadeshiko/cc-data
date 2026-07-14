# EP-RNF Snoop 冲突仲裁方案（Snoop-vs-Outstanding-Transaction Arbitration）

版本 0.1（草案，待决策确认）— 2026-07-14
关联问题记录：`docs/issues/tc98_deadlock2_eprnf_snoop_conflict.md`
关联既有设计：`docs/design/ep-rnf-sharer-registration-plan.md`

---

## 0. 本文范围

本方案统一处理两件相关的事：

1. **死锁#2 修复**：EP-RNF 在有 in-flight CHI 事务时，对同址 snoop 无差别排队且永不出队，
   导致跨节点写-写竞争死锁（TC98，约 115 次 ownership 转移后卡死）。
2. **通用机制补全**：EP-RNF 作为"外部世界代理"，在 hold 一个外传 invalidation、等待外层
   response 的窗口期内，节点内 CHI 域并发到达相关请求时的正确处理（仲裁）。

二者是同一根问题的两面：**EP-RNF 的每个事务都需要一次"外层往返"（慢），而外层往返窗口
足够长，本地 CHI 域与外部世界都会继续产生对同一行的活动；当前的"per-PA 单飞 + 排队/fatal"
没有冲突仲裁能力。**

---

## 1. 名词解释（Terminology）

| 名词 | 含义 |
|------|------|
| **Inner / 本地 CHI 域** | 单个节点内部的标准 gem5 CHI 一致性域：`cpuN.l1*`、`cpuN.l2`、本节点 `hnf_nodeN_sS`（HN-F，本地 Point of Coherence）。 |
| **Outer / 外层（全局）域** | 跨节点的 EP 一致性域：由 home 节点的 **UBCC**（`modules/ubiomodule/UBCCController.cc`）做全局目录与序列化，通过 EP 协议消息（OuterUpgradeReq/OuterInvalidateReq/Recall/…）驱动。 |
| **EP-RNF** | `EPRNFController`（machine type `Cache`，是本地 HN-F 的一个 RN-F/sharer）。它是**外部世界在本地 CHI 域里的代理**：本地 HN-F 把"外部世界"当作一个 sharer，通过 snoop EP-RNF 来向外传播/查询一致性。 |
| **EP-SNF** | `EPSNFController`，home 侧响应器，服务 ReadNoSnp/Writeback，与本文关系较小。 |
| **UBCC epoch** | 外层全局序的单调时间戳。每个被全局定序的写/操作分配一个 epoch。**epoch 是全局序的唯一权威。** |
| **in-flight CHI txn (`_pendingChiTxns[pa]`)** | EP-RNF 向本地 HN-F 发起、尚未完成的一次 CHI 请求（ReadShared / ReadUnique / CleanUnique）。每种都需外层往返才能完成。 |
| **held snoop / `_upgradePending[pa]`** | EP-RNF 收到本地 HN-F 的 SnpCleanInvalid 后，为向外传播 invalidation 而 hold 住的 snoop：已"接受"snoop 但**推迟 SnpResp_I**，直到外层 OuterUpgradeAck(true) 回来。 |
| **proxyOp（EpProxyOp）** | EP-RNF 发给 HN-F 的 CHI 请求的语义标签：`NoProxyOp`（本地自身语义/取数）、`InvalidateOnly`（代远程写去失效本节点）、`RecallUnique`（代远程独占/recall）。 |
| **Outbound（外传）** | 本地写 → snoop EP-RNF → EP-RNF 向 home 发 OuterUpgradeReq 去 invalidate 其他节点。方向：本地→外。 |
| **Inbound（内收）** | 远程写 → home 下发 OuterInvalidateReq → EPBackend → EP-RNF 发 InvalidateOnly CleanUnique 去失效本节点本地缓存。方向：外→本地。 |
| **stale SnpResp_I** | 带 `stale=1` 的 SnpResp_I。语义："被 snoop 的请求方在竞争中输了，HN-F 应把发起者的事务作为 stale 完成，发起者须回退重试"。已有实现：`sendSnpRespI(linePa, hnfDest, staleMark=true)`。 |
| **abandon-and-retry / loser path** | 竞争中输的一方放弃当前本地事务、经全局序重新申请（重试）。已有实现雏形见 `EPBackend.cc:1550` "TC16 dual-upgrade race LOSER path"。 |

---

## 2. 现有实现（Quote 现状）

### 2.1 外传路径存在且正确（hold snoop 等外层）

`gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:729-769`（`handleSnpCleanInvalid` first-arrival）：

> ```cpp
> // ---- first-arrival upgrade path ----
> // CHI §4.3.3 / §5.5: a snoop whose upgrade is not yet complete must be
> // *held* (accepted, response deferred), NOT NACKed.
> UpgradePending pending; pending.valid = true; ...
> _upgradePending[msg->m_addr] = pending;               // :758
> completeHeldUpgrade(msg->m_addr);                      // :767 发 OuterUpgradeReq；未完成则 hold
> return true;                                           // :768 SnpResp_I 推迟
> ```

回程：`receiveUpgradeAck`（:1623）在 OuterUpgradeAck(true) 到来时才发 SnpResp_I。

**结论：本地写通过 snoop EP-RNF 向外传播 invalidation 的路径存在，且"hold 住 snoop 等外层"
是有意的正确设计（不能即时响应，因为外层未确认前无法诚实声明外部世界已失效）。**

### 2.2 冲突处理点 = EP-RNF 的 snoop 入口（当前是无差别排队）

`EPRNFController.cc:367-394`（`recvSnoopMsg`）：

> ```cpp
> // ---- Per-PA single-flight check (§4.3.3) ----
> auto txnIt = _pendingChiTxns.find(msg->m_addr);
> bool inflight = (txnIt != _pendingChiTxns.end());
> if (inflight) {
>   if (txnIt->second.snoopSlotValid) {
>     fatal("... second snoop ... HN-F single-flight assumption broken");  // :375
>   }
>   txnIt->second.snoopSlotValid = true;                 // :382 排进 1-entry slot
>   txnIt->second.queuedSnoopType = msg->m_type;
>   return true;                                         // :390 排队，不响应
> }
> return processSnoopImmediate(msg);                      // :394
> ```

排队的 snoop 仅在 in-flight txn 完成时出队：`finishChiTxn`（:936）末尾
`if (hadQueuedSnoop) processQueuedSnoop(linePa);`（:975-976）。

**问题：若 in-flight txn 因跨节点冲突而永不完成，排队 snoop 永不出队 → 死锁#2。**

### 2.3 stale/abandon 机制已存在，可复用

- `sendSnpRespI(linePa, hnfDest, staleMark)`（:1406）— 能发 stale SnpResp_I。
- `EPBackend.cc:1550` — "TC16 dual-upgrade race LOSER path (abandon-and-downgrade)"，
  已用 stale SnpResp_I + 直接 ack winner 打破双 upgrade 竞争。
- `scheduleUpgradeRetryAfterRejection`（:1426）— 输者用新 reqId/epoch 重发的雏形。

---

## 3. 根因（Root Cause）

**EP-RNF 作为外部世界代理，其所有事务都需外层往返（慢）；`recvSnoopMsg` 的"有 in-flight
txn 就排队 snoop"假设了"外层往返窗口内不会再来一条需要 EP-RNF 立即处置的冲突请求"，该假设
在热点行的跨节点写-写竞争下不成立。**

关键子事实：EP-RNF 发自己的 CleanUnique/ReadUnique 时，HN-F 会把 requestor（EP-RNF 自己）
从 snoop 目标排除。因此**在 EP-RNF 有 in-flight 写类事务期间到达的 snoop，本质不可能是自身
事务引发的 self-snoop，一定来自另一个（冲突）事务** → 对写类事务，"排队"几乎总是错的
（在排一个需要仲裁的冲突，而非会自然出队的良性 self-snoop）。

---

## 4. 设计原则（Invariants）

1. **全局序权威**：UBCC epoch 是唯一权威。已获全局 epoch 的操作（Inbound 远程写）必须能推进；
   尚未提交全局序的本地操作（Outbound 本地抢跑）在冲突时让路、abort-retry。
2. **不谎报**：EP-RNF 绝不在外层未确认前回普通 SnpResp_I（会让本地写错误拿到独占 → split-brain）。
   让路只能通过 **stale SnpResp_I**（=abort-retry 信号），不能通过 clean SnpResp_I。
3. **守约慢 RN-F**：EP-RNF 对本地 HN-F 的 snoop，响应可以延迟（等外层），但**必须最终到达**，
   绝不能"排队且永不出队"。冲突时给 stale-retry，而非本地死锁或 fatal。
4. **外层往返独立可完成**：Outbound 的外层往返（home 去 invalidate 其他节点）不得依赖被本地
   BUSY_BLKD 卡住的本地事务；否则外层无法推进 → 死锁。
5. **不大改 CHI 核心状态机**：修复落点在 EP 层（EP-RNF snoop 入口 / EPBackend 仲裁），不改
   `CHI-cache-transitions.sm` 的 BUSY_BLKD/StallSnoop 语义。

---

## 5. 方案（分层，落点明确）

### 5.1 修复落点：`EPRNFController::recvSnoopMsg`（RN-F↔HN-F snoop 接口）

把无差别排队改为**冲突分类 + 仲裁**：

```
recvSnoopMsg(snoop for pa):
  txn = _pendingChiTxns[pa]        // EP-RNF 自己 in-flight 的 CHI 事务（若有）
  if (no txn):
     return processSnoopImmediate(snoop)          // 现状，正确

  // 有 in-flight txn —— 分类：
  if (snoop 是良性 self-snoop):                    // recall/pending 守卫覆盖，能即时满足
     return processSnoopImmediate(snoop)           // 让 handleSnp* 的守卫即时响应
  else:                                            // 冲突：本地 snoop vs 已进入全局流程的 in-flight txn
     ARBITRATE(txn, snoop)                          // 见 5.2
```

「良性 self-snoop」的判据（复用 `handleSnpCleanInvalid` 现有守卫的条件）：
本行有 active recall（`backend->hasActiveRecall(pa)`）或 pending ChiTxn 是 recall 引发的
自 snoop。这些分支本就即时 `sendSnpRespI`，只是被 recvSnoopMsg 的排队抢先拦掉。

### 5.2 仲裁（ARBITRATE）：全局序判负者 stale-retry

冲突情形：EP-RNF 有 in-flight txn（代表某个**已进入全局流程**的操作，如 InvalidateOnly=远程
写、RecallUnique=远程独占），又收到本地 HN-F 的同址 invalidating snoop（代表本地写 cpu 的
抢跑）。裁决：

- **让本地 snoop 输**：EP-RNF 回 **stale SnpResp_I**（`sendSnpRespI(pa, hnfDest, true)`）。
  HN-F 把本地 cpu 的 CleanUnique 作为 stale 完成 → 本地 cpu 的 L2 检测 stale → **不进独占、
  回退重发**（重试时经 EPBackend→home 走完整全局申请，排到已定序操作之后）。
- **EP-RNF 自己的 in-flight txn 照常推进**（HN-F stall 解除后处理它），完成后 node1 让出该行。

这样：解死锁（snoop 得到响应、in-flight txn 能推进）+ 正确（不谎报、本地写回退经全局序重排）。

### 5.3 hold 期间并发请求的通用规则（回答"外层等待窗口的并发处理"）

- **情形 A（HN-F 排队即可）**：另一本地 CPU 同址写/读 → HN-F `StallRequest` 排队等当前
  事务完成。无需 EP-RNF 介入，天然正确（只要 EP-RNF 最终会响应，队列会推进）。
- **情形 B（需仲裁 = 5.2）**：hold outbound upgrade 期间来了冲突的 inbound OuterInvalidateReq
  （或反之，先 inbound 后 outbound snoop —— 即死锁#2 实测顺序）。→ 走 5.2 仲裁。
- 取消 `recvSnoopMsg:375` 的 `fatal("second snoop ... single-flight broken")`：并发冲突是
  合法场景，不应 fatal；应进入仲裁。

---

## 6. 正确性论证（为何不破坏已通过的 TC）

- **self-snoop / recall 路径不变**：良性分支仍走 `processSnoopImmediate → handleSnp*` 即时响应，
  行为与现状一致（TC16/25/53/42 等依赖的路径不变）。
- **stale-retry 是 CHI 处理写-写竞争的标准手段**：输者 stale 重试，不丢数据、不脏读。
- **仲裁只在"有 in-flight txn 且冲突"时触发**：无 in-flight txn 时（绝大多数 TC 的快路径）
  完全走原逻辑，零影响。
- **回归范围**：需重点验证 TC16/23/25/41/42/53（recall / 多 sharer / dual-upgrade）+ TC95/96/97
  + 1s/2s/8n1s 全量，确认无回归；TC98 验证死锁#2 解除且能跑完（理想 sim-time ~0.66ms）。

---

## 7. 决策记录（Q1/Q2/Q3/Q5 已确认；Q4 见 §9 待确认）

### Q1. 仲裁判定依据 —— 【已定：(b) 简化规则】
"EP-RNF 有 in-flight 外层事务（已进入全局流程）时，本地冲突 snoop 一律 stale-retry 让位。"
理由：本地 CleanUnique 尚未提交全局序、无全局 epoch；已在全局流程中的操作天然在先。

### Q2. 让路方向 —— 【已定：恒定本地让路】
恒定"本地写让路给 EP-RNF 的 in-flight 外层事务"，不做双向仲裁。
**"让路"的具体实现（mechanically）**（全链已在代码中确认）：
1. EP-RNF 对本地 snoop 回 **stale SnpResp_I**：`sendSnpRespI(pa, hnfDest, staleMark=true)`
   （`EPRNFController.cc:1406`，第 5 个构造参数 `stale=true`）。与 `EPBackend.cc:1584`
   `sendSnpRespIForRejected` 在 TC16 loser path 用的是同一机制。
2. 本地 HN-F 收到"CleanUnique 的终结 snoop 响应带 stale" → 把该 CleanUnique 完成为
   **`Comp_UC(stale=1)`**（`CHI-cache-actions.sm:838 SendCompUCRespStale`；
   `CHI-cache-transitions.sm:1190`），并把 requestor 移出 dir_sharers。
   **关键：requestor 不进入 UC/独占**（`EPBackend.cc:1571-1573` 注释："never enters UC on the
   stale completion" → 无 split-brain）。
3. 本地 CPU 的 L2 检测到 `Comp_UC && stale`（`CHI-cache-actions.sm:2874`）→ 知道未获独占、
   本地副本已失效 → **重新发起获取（fresh ReadUnique/CleanUnique）**。重试经 EPBackend→home
   走完整全局申请，排到已定序操作之后。
即"让路" = **abort-and-retry**：本地写这次作废（stale），自动重发并经全局序重排。

### Q3. `fatal` 的处理 —— 【已澄清：可安全移除/降级，但非核心】
澄清两个不同的"单"：
- `snoopSlotValid`（1-entry incoming-snoop slot）：HN-F 对同址严格单事务串行
  （active 事务收齐 SnpResp 前不处理排队 pendReq），因此**不会对 EP-RNF 同址并发发第 2 个
  incoming snoop** → `recvSnoopMsg:375` 的 `fatal("second snoop...")` 在 HN-F single-flight 下
  基本不可达（实测死锁时未触发）。移除/降级它是安全的，**但它不是死锁主因**。
- `_pendingChiTxns[pa]`（EP-RNF 自己向 HN-F 发起的 CHI 事务，per-PA `std::map`）：死锁的真正冲突
  是"EP-RNF 自己的 in-flight txn" ↔ "它 slot 里那个 incoming snoop"互锁——二者共用同一条
  `_pendingChiTxns[pa]` 记录。修复核心是**不把冲突 snoop 排进 slot 干等，而走仲裁**，不是改 fatal。

### Q5. 实施与验证节奏 —— 【已定】
1. 先跑一个**小 TC 集**回归（1s/2s + TC16/25/42/53 等 recall/upgrade 相关），确认无破坏；
2. 再跑 **TC98** 验证死锁#2 解除，**同时开 DebugFlag（ProtocolTrace,RubyGenerated,RubyCHIGeneric）
   验证状态变化**（确认 stale-retry 后本地 cpu 确实回退重试且最终成功，无 split-brain）；
3. 最后全量回归（8n1s/8n2s + TC95/96/97）。

---

## 8. 附：不采用的方案及理由

- **EP-RNF 即时回普通 SnpResp_I**：违反原则 2（谎报外部世界已失效 → split-brain）。否决。
- **在 HN-F 层调度 InvalidateOnly 优先**：需改 CHI 核心调度/优先级，违反原则 5。否决（作为
  备选保留，若 EP 层仲裁不足）。
- **本地写发 CleanUnique 前先向 home 拿全局 epoch（全局定序前置）**：改动面最大，涉及本地写
  关键路径延迟；作为长期方向记录，本次不做。
- **EPBackend 高层拦截**：间接、延后，不在死锁环上，救不了 HN-F 上的物理冲突。否决为主修点
  （EPBackend 仅承担"输者重试经全局序重排"的配合角色）。

---

## 9. Q4 深度分析：三类 in-flight 事务 × 冲突 snoop 的语义矩阵【待你确认】

> 本节是 Q4 的详细推演。结论以"待确认"呈现，请审阅每格的处理规则后拍板。

### 9.1 建立语义模型

#### 9.1.1 EP-RNF 三类 in-flight 事务代表的"外部世界意图"（触发源已在代码确认）

| 事务 (proxyOp) | 触发源 | 外部世界意图 | node1 目标终态 | 是否需数据回外 |
|------|------|------|------|------|
| **ReadShared (NoProxyOp)** | `EPBackend.cc:1091`，read-recall（`recallMsg.isReadRequest==true`） | **远程 reader** 要读该行 | node1 降级为 **Shared**（仍保留只读副本） | 是（把 node1 的 dirty 数据下放/回给 reader） |
| **ReadUnique (RecallUnique)** | `EPBackend.cc:1151`，write-recall（`isReadRequest==false`） | **远程 writer** 要独占 | node1 **完全失效**（交出该行+数据） | 是（把 dirty 数据交给 writer） |
| **CleanUnique (InvalidateOnly)** | `EPBackend.cc:1641`，`handleInvalidationRequest`（收到 OuterInvalidateReq） | **远程 writer** 要独占（node1 是无脏数据的 sharer） | node1 **完全失效**（只需失效，无需交数据） | 否 |

要点：**三类都由"外层 home 已定序的操作"触发**（read-recall / write-recall / invalidate 都来自
home 的全局决策）。所以按 Q1(b)/Q2，它们相对本地抢跑写**恒定在先**。

#### 9.1.2 冲突 incoming snoop 代表的"本地请求者意图"（EP-RNF 实际处理的 snoop 类型）

EP-RNF 的 `processSnoopImmediate`（`EPRNFController.cc:649`）只处理三种，且对
**SnpShared/SnpSharedFwd 直接 `fatal`（保序 snoop 不得指向 EP-RNF）**：

| incoming snoop | 本地请求者意图 | 对 EP-RNF（外部世界）的要求 |
|------|------|------|
| **SnpCleanInvalid** | 本地 CPU 要 **Unique（写）** | 让外部世界**失效**该行 |
| **SnpUnique** | 本地 CPU 要 **Unique（写）+ 数据** | 让外部世界失效 + 若持脏则回数据 |
| **SnpOnce** | 本地 CPU 要 **一次性读快照** | 只读、**不改变**外部世界 sharer 状态、不失效 |

关键区分：**SnpCleanInvalid/SnpUnique 是"写意图"（invalidating），SnpOnce 是"读意图"（non-invalidating）。**

### 9.2 冲突判定的前置过滤（哪些"看似冲突"其实不是冲突）

在进入仲裁前，`recvSnoopMsg` 必须先分流掉**良性/非冲突**的 snoop（沿用 `handleSnpCleanInvalid`
现有守卫的判据），只有"真冲突"才走 Q2 的 stale-retry：

1. **self-snoop（自身事务引发）** → 即时正常响应，**非冲突**：
   - active-recall 守卫：`backend->hasActiveRecall(pa)`（`handleSnpCleanInvalid:721`）。
   - pending-ChiTxn recall 守卫：`_pendingChiTxns[pa]` 是 recall 引发（`:707-715`）。
   - 这些是 EP-RNF 自己的 ReadShared/ReadUnique(recall) 在 home 侧引发的回流 snoop，应即时
     `sendSnpRespI`（clean，不是 stale），因为它们与自身事务是**同一全局操作**，不是竞争。
2. **SnpOnce（读快照）** → 见 9.3 分析，多数情况**非冲突**，可即时快照响应。
3. 其余（写意图 snoop 且非 self-snoop）→ **真冲突** → Q2 stale-retry。

### 9.3 语义矩阵（3 类 in-flight × 3 类 snoop = 9 格）

记号：**STALE** = 回 stale SnpResp_I 让本地写 abort-retry（Q2）；**IMMED** = 即时正常响应；
**N/A-fatal** = 该组合不应出现（保序 snoop 不指向 EP-RNF，`fatal` 保留）。

#### 行 1：in-flight = **CleanUnique (InvalidateOnly)** —— 远程 writer，node1 将失效（无数据）

| incoming snoop | 判定 | 处理 | 理由 |
|------|------|------|------|
| SnpCleanInvalid | **真冲突** | **STALE** | 本次死锁#2 正是此格。远程 writer 已定序在先、node1 正被要求失效；本地写必须让路、abort-retry。 |
| SnpUnique | **真冲突** | **STALE** | 同上；本地写要独占+数据，但 node1 正被远程 writer 夺走该行，本地写让路。EP-RNF 无脏数据（InvalidateOnly 语义），无需回数据。 |
| SnpOnce | **需分析** | **IMMED（SnpRespData_SC 快照）** *或* **STALE** | SnpOnce 是本地一次性读，不改 sharer 状态、不与"失效"根本冲突。倾向 IMMED：让本地读拿到当前快照（该行即将失效，但一次性读旧值在 CHI 语义下是允许的弱序读）。**风险点见 9.4-①**。 |

#### 行 2：in-flight = **ReadUnique (RecallUnique)** —— 远程 writer，node1 将完全失效（交数据）

| incoming snoop | 判定 | 处理 | 理由 |
|------|------|------|------|
| SnpCleanInvalid | **真冲突** | **STALE** | 与行1同构：远程 writer 定序在先，node1 交出该行；本地写让路 abort-retry。 |
| SnpUnique | **真冲突** | **STALE** | 同上。注意 ReadUnique 正在把 node1 的**脏数据**交给远程 writer；本地写此刻拿数据会拿到"即将被远程覆盖"的旧值，必须让路。 |
| SnpOnce | **需分析** | **IMMED 快照** *或* **STALE** | 同行1-SnpOnce，但更微妙：ReadUnique 会**改变数据归属**（数据要交给远程 writer）。若允许本地 SnpOnce 快照，读到的是"交出前"的值——在弱序下可接受，但需确认不破坏 recall 的数据捕获（`recallCaptureData`）。**风险点见 9.4-①②**。 |

#### 行 3：in-flight = **ReadShared (NoProxyOp)** —— 远程 reader，node1 降级为 Shared（保留只读）

| incoming snoop | 判定 | 处理 | 理由 |
|------|------|------|------|
| SnpCleanInvalid | **真冲突** | **STALE** | 微妙但仍冲突：远程 reader 的 read-recall 已定序在先，正在把 node1 降级为 Shared（node1 将只保留只读副本，且外部有 reader 副本）。本地写要 Unique 必须失效"外部所有副本"——包括正在建立的远程 reader 副本。二者冲突：本地写不能在 read-recall 进行中拿到 Unique（会使刚建立的远程 reader 副本立即失效、违反 read-recall 的语义 in-flight）。→ 本地写让路 abort-retry，重试时经全局序在 read-recall 之后申请 Unique（届时会正常 recall 掉远程 reader）。**风险点见 9.4-③**。 |
| SnpUnique | **真冲突** | **STALE** | 同上。 |
| SnpOnce | **非冲突** | **IMMED 快照** | 两者都是读：ReadShared（远程读）与 SnpOnce（本地一次性读）可并存，不改独占性。EP-RNF 回 SnpRespData_SC 快照即可。 |

#### 全 9 格汇总

| in-flight ↓ \ snoop → | SnpCleanInvalid | SnpUnique | SnpOnce |
|------|------|------|------|
| **CleanUnique (InvalidateOnly)** | STALE | STALE | IMMED*（或 STALE，待定） |
| **ReadUnique (RecallUnique)** | STALE | STALE | IMMED*（或 STALE，待定） |
| **ReadShared (NoProxyOp)** | STALE | STALE | IMMED |
| **SnpShared / SnpSharedFwd（任一行）** | \multicolumn N/A-fatal（保序 snoop 不得指向 EP-RNF，保留 `fatal`，见 `:664`） |||

### 9.4 遗留风险点 / 需你拍板的细节

- **① SnpOnce 在写类 in-flight（行1/行2）下 IMMED 是否安全？**
  SnpOnce 是弱序一次性读。若在"该行即将失效/被夺走"期间允许本地 SnpOnce 读到旧快照，CHI 弱序
  模型允许，但需确认本设计的 workload 语义（是否有对 SnpOnce 读到旧值敏感的正确性测试）。
  **保守选择**：行1/行2 的 SnpOnce 也走 **STALE**（统一"写类 in-flight 期间一切本地访问让路"），
  最简单、最安全，代价是本地 SnpOnce 也被 retry（性能损失，但热点场景本就慢）。
  **倾向**：先用保守的 **STALE 统一**（三类 in-flight 遇 SnpCleanInvalid/SnpUnique/SnpOnce 全 STALE），
  把 IMMED 快照作为后续优化。→ **请确认**。

- **② ReadUnique 在途时的数据捕获**：ReadUnique 正在捕获 node1 脏数据回给远程 writer
  （`recallCaptureData`）。任何在途 snoop 的处理都不得干扰这次数据捕获。若行2 全走 STALE，
  本地写不参与，天然不干扰。→ 若 ① 采纳"STALE 统一"，本点自动满足。

- **③ ReadShared(远程读) 期间本地写 STALE 的正确性**：本地写 abort-retry 后重发，需保证重发的
  Unique 申请在全局序里确实落在 read-recall 之后（否则可能又抢跑）。这依赖 home UBCC 的全局
  定序正确性——即"本地写重试必须经 EPBackend→home 拿新 epoch"，不能本地直接再抢。→ 需在实现里
  确保 stale-retry 后本地写走的是"完整全局申请"路径，而非本地快路径。

- **④ self-snoop 判据的完备性**：9.2 的良性分流依赖 `hasActiveRecall` / recall-pending 守卫。
  需确认这些守卫能覆盖所有"自身事务引发的回流 snoop"，不把良性 self-snoop 误判为冲突而 STALE
  （否则会把 EP-RNF 自己的 recall 也 abort，引入活性问题）。→ 实现前需针对性验证（可加诊断日志）。

### 9.5 Q4 决策记录【已确认】

- **写类 snoop（SnpCleanInvalid/SnpUnique）在三类 in-flight 下一律 STALE**——已定。
- **SnpOnce**：ReadShared 行下 IMMED（读读共存）；**CleanUnique/ReadUnique 行下 = STALE**
  【Q4-a 已定：保守 STALE 统一】。实现时在这两格的代码处**标注 `// TODO: SnpOnce 可优化为
  IMMED 快照（弱序读），当前保守 STALE 以求最简/最安全`**。
- **SnpShared/SnpSharedFwd 不得指向 EP-RNF，保留 `fatal`**（`EPRNFController.cc:664`）。
- 良性 self-snoop（recall 引发）走 IMMED clean SnpResp_I，不进仲裁。
- **Q4-b 已定：认可整套矩阵**。

最终矩阵（已定稿）：

| in-flight ↓ \ snoop → | SnpCleanInvalid | SnpUnique | SnpOnce | SnpShared/Fwd |
|------|------|------|------|------|
| CleanUnique (InvalidateOnly) | STALE | STALE | STALE (TODO:可优化 IMMED) | fatal |
| ReadUnique (RecallUnique) | STALE | STALE | STALE (TODO:可优化 IMMED) | fatal |
| ReadShared (NoProxyOp) | STALE | STALE | IMMED | fatal |
| （前置）良性 self-snoop = recall 引发 | IMMED clean SnpResp_I（不进仲裁） ||||

---

## 10. ③④ 实现期验证结论（我已做代码级验证）

### 10.1 ③ —— 本地写 STALE-retry 后能否正确经全局序重排？【结论：安全，你的理解正确】

**场景（你的描述）**：远端 read-recall 进入 CHI 域变成 EP-RNF 的 ReadShared（EP-RNF 刚获共享权），
在 ReadShared 完成前，本地某 CPU 想写 → 需用新的 Unique 把 EP-RNF 刚拿的共享权收回。这个
Unique 是否天然触发全局的抢占并被正确定序？

**验证结论：是，且被 home UBCC 的 outstanding 串行化天然保证顺序，无时序 race。**

链路（代码确认）：
1. 本地写 STALE 后重发 CleanUnique → 本地 HN-F → HN-F snoop EP-RNF（此刻远端 op 若仍在途，
   EP-RNF 仍有 in-flight txn → 再次 STALE；若已完成，则无 in-flight）。
2. 当 EP-RNF 无 in-flight 冲突时，snoop 命中 `handleSnpCleanInvalid` 的
   **first-arrival upgrade path**（`EPRNFController.cc:729-769`）→ 发 **OuterUpgradeReq 到 home**
   → **天然进入全局流程**（正是你说的"Unique 触发全局抢占"）。
3. **时序安全性由 home 保证**：home UBCC 对同址用 `findOutstanding` 串行化
   （`UBCCController.cc:456`）——若那个抢先的远端 op 在 home 仍有 live outstanding，则本地写的
   OuterUpgradeReq 会被 **BUSY / enqueue 进 `_pendingRequesters`**（`:264`），**无法插到远端 op
   之前**。远端 op 完成（拿到 epoch、commit）后，本地写的申请才被 replay，拿到**更晚的 epoch**。
   → 本地写重试确实落在远端 op 之后，无抢跑、无 race。

**唯一实现约束（记为实现须知，非阻塞）**：STALE-retry 后本地 CPU 的 L2 必须走"重新发起获取
（fresh ReadUnique/CleanUnique → 经 EP-RNF → home）"的完整路径，而非本地快路径直接判 hit。
这是 CHI Comp_UC(stale) 语义本身保证的（`CHI-cache-actions.sm:2874` L2 检测 stale 后 re-fetch，
不进 UC）。实施后需用 DebugFlag 确认此重发确实经 home（见 Q5 第 2 步）。

### 10.2 ④ —— self-snoop 守卫完备性【结论：发现一处关键点，修复必须处理】

**验证发现（重要）**：`handleSnpCleanInvalid` 里有两个 self-snoop 守卫，但当前**行为不同**：

1. **`_pendingChiTxns` 守卫（`EPRNFController.cc:707-716`）= 当前是死代码**：
   `recvSnoopMsg`（:367-394）在 `_pendingChiTxns` 存在时**先行排队并 return**，根本不会走到
   `processSnoopImmediate → handleSnpCleanInvalid`。所以 :707 的 `if (chiIt != end())` 在到达时
   恒为空 → **不可达**。这正是死锁#2 的机制（该排队没有仲裁）。

2. **`hasActiveRecall` 守卫（:721-727）= 真实且必要**：处理一种**真正的 self-snoop**——
   node1 作为被 recall 的 owner，发出 RecallResponse、交出该行后，home/HN-F 会补发一个
   SnpCleanInvalid 来失效 node1 的旧副本（`EPBackend.cc:1226-1230` 注释确认此 snoop
   "arrives AFTER sendRecallResponse"）。这属于 node1 自己 recall 流程的一部分，**必须即时回
   clean SnpResp_I（并 `clearActiveRecall`），绝不能 STALE**（STALE 会错误 abort node1 自己
   已完成的 recall → 活性/正确性 bug）。

**对修复的强制要求（写入实现规范）**：
新的 `recvSnoopMsg` 分类逻辑，在判"冲突→STALE"之前，**必须先检查 `hasActiveRecall(pa)`
（及 recall 引发的 pending 场景），命中则走 IMMED clean SnpResp_I + clearActiveRecall**，
只有**非 recall 引发**的 in-flight 冲突才进 STALE 仲裁。即：良性分流（§9.2）不是可选优化，
而是正确性必需——否则会把 recall self-snoop 误 STALE。

**尚需实现期用 DebugFlag 确认的点**：`hasActiveRecall` + recall-pending 两个条件是否**完整覆盖**
所有 recall 引发的回流 snoop（不存在"recall 引发但两个条件都为假"的窗口）。若存在覆盖缺口，
需补判据。实施时对 TC98 + recall 相关 TC（TC16/25/42/53）开 DebugFlag 逐一核对
"[SELF-SNOOP]/[RECALL-SNOOP] 命中 vs STALE 命中"的分类正确性。

### 10.3 对 §5 修复方案的补正

§5.1 的 `recvSnoopMsg` 分类逻辑据 10.2 修正为：

```
recvSnoopMsg(snoop for pa):
  if (no in-flight txn && !hasActiveRecall(pa)):
     return processSnoopImmediate(snoop)          // 快路径，原逻辑
  // 有 in-flight txn 或 active recall：
  if (hasActiveRecall(pa) || snoop 是 recall 引发的 self-snoop):
     clearActiveRecall(pa) (如适用)
     return processSnoopImmediate(snoop)           // IMMED clean SnpResp_I（良性 self-snoop）
  else:  // 非 recall 的 in-flight 冲突
     if (snoop ∈ {SnpCleanInvalid, SnpUnique}):     STALE (Q2)
     else if (snoop == SnpOnce):                    STALE  // Q4-a, TODO 可优化 IMMED
     else if (snoop ∈ {SnpShared, SnpSharedFwd}):   fatal  // 保序 snoop 不得指向 EP-RNF
```

（注：`processSnoopImmediate` 内部的 `handleSnpCleanInvalid` 已有 hasActiveRecall 分支，可复用；
关键是把"是否 STALE 仲裁"的判定上移到 recvSnoopMsg，并保证良性 self-snoop 优先于 STALE。）
