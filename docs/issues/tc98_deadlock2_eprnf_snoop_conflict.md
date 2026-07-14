# TC98 死锁#2：EP-RNF snoop 排队引发的跨节点写-写竞争死锁

日期：2026-07-14
状态：已定位根因，修复方向待定（本文档记录分析，供后续实现参考）

## 1. 现象

在 TC98（8n2s 单行热点压测）中，前置修复（TC98 ReadReq 去重、home 侧统一 invalidate
fanout、无本地副本立即 ack、fanout 按发送时目录状态）解除了**死锁#1**后，测试从"9 次
ownership 转移"跃进到"约 115 次"，但随后在 sim-tick ≈ 19878550000（约 19.88ms）再次
卡死。curT 仍推进但 RECALL-CREATE / invalidation-ack 计数不再增长（真死锁，非慢）。

热点行：home PA `0x10007800`（home=node0），node1 本地视图 `0x10010007800`。

## 2. 死锁闭环（均有带时间戳的 ProtocolTrace/RubyGenerated 日志佐证）

```
tick 19878550000  node1 EP-RNF(Cache-6) startCleanUnique → sendChiRequest
                  type=CleanUnique proxyOp=InvalidateOnly → node1 HN-F(Cache-2)
                  （由 home 为 node7 的写下发的 OuterInvalidateReq epoch=276 触发）
tick 19878548500  HN-F 开始处理 cpu0.l2(Cache-11) 的 CleanUnique(NoProxyOp) → BUSY_BLKD
tick 19878554500  HN-F 把 EP-RNF 的 CleanUnique StallRequest（自己 BUSY_BLKD 中）
tick 19878559500  HN-F 为 cpu0 的事务扇出 SnpCleanInvalid → {cpu1.l2, EP-RNF}
tick 19878563500  EP-RNF 收到 SnpCleanInvalid（[EPRNF-SNOOP-RECV] type=30）
                  → 因"本行有 in-flight CHI txn（它自己的 InvalidateOnly CleanUnique）"
                  → [EPRNF-SNOOP-QUEUED] 排进 1-entry slot、return true、永不处理
tick 19878591500  cpu1.l2 回 SnpResp_I，HN-F remain=1（仍等 EP-RNF）
                  ↓ 四方循环死锁
```

四方循环等待：
1. HN-F 等 EP-RNF 的 SnpResp_I（remain=1）
2. EP-RNF 等 HN-F 的 Comp_UC（它自己的 InvalidateOnly CleanUnique 未完成）
3. EP-RNF 排队的 snoop 永不出队（因为它的 CleanUnique 未完成）
4. HN-F 处理不了 EP-RNF 的 CleanUnique（自己 BUSY_BLKD 在 cpu0 的事务上）

## 3. 冲突性质：真·跨节点写-写竞争（已证实为"甲"，非自我冲突）

死锁时刻 node1 上有 3 个并发写意图，抢同一行：

| 来源 | CHI 类型 | 触发 |
|------|---------|------|
| cpu0.l2 (本机 ST) | CleanUnique(NoProxyOp) | node1 自己要写 |
| **EP-RNF(Cache-6)** | CleanUnique(InvalidateOnly) | **node7（远程）要写**，home 令 node1 让出 |
| cpu1.l2 (本机 ST) | CleanUnique(NoProxyOp) | node1 自己要写（稍晚到达） |

决定性证据：触发 EP-RNF InvalidateOnly 的是
`[UBCC-FANOUT] home=0 pa=0x10007800 target=1 epoch=276 reqId=504403158265495606`，
reqId 解码 → requester = **node7**。全局序里 node7 的写 epoch=276 已成立，而 cpu0 的
本机写此刻还停在 node1 本地 HN-F 层、尚未提交到 UBCC 拿全局 epoch。

即：node7 在全局已定序，cpu0 在本地抢跑，二者在 node1 本地互等 → 死锁。

## 4. Root Cause：`EPRNFController::recvSnoopMsg` 无差别排队

`gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:367-391`：

```cpp
auto txnIt = _pendingChiTxns.find(msg->m_addr);
bool inflight = (txnIt != _pendingChiTxns.end());
if (inflight) {
    // 本行有 in-flight CHI 事务 → 把 snoop 排进 1-entry slot，return true
    txnIt->second.snoopSlotValid = true; ...
    return true;   // ← 违反 CHI：RN-F 有 outstanding request 时收到同址 snoop
                   //    必须立即响应，不能 stall；这里却排队且永不出队
}
return processSnoopImmediate(msg);
```

CHI 前提：RN-F 在有 outstanding request 时收到同址 snoop 必须立即处理（响应），以保证
请求-snoop 无环、无死锁。该前提对真实 RN-F 成立（invalidate 本地副本是即时的）。EP-RNF
是 RN-F，但它把 snoop 排队了 → 破坏该前提 → 死锁。

关键观察：EP-RNF 发自己的 CleanUnique/ReadUnique 时，HN-F 会把 requestor（EP-RNF 自己）
从 snoop 目标排除。因此**在 EP-RNF 有 in-flight 写类事务期间到达的 snoop，本质不可能是
自身事务引发的 self-snoop，一定是别的（冲突）事务引发的**——对写类事务，这个"排队"逻辑
几乎总是错的（在排一个需要仲裁的冲突，而非会自然出队的良性 self-snoop）。`handleSnpCleanInvalid`
里本有的 self-snoop / recall / upgrade-hold 守卫（能即时或正确地处理），被 recvSnoopMsg
的排队抢先拦掉了。

## 5. 这是通用问题，不止 InvalidateOnly 一条路径

`recvSnoopMsg` 的"有 in-flight txn 就排队"是无差别的。EP-RNF 有 3 类 in-flight 事务（都需
外层往返、都"慢"）：

| EP-RNF in-flight 事务 | 场景 | 撞上的冲突 snoop | 同结构死锁 |
|------|------|------|------|
| CleanUnique(InvalidateOnly) | 远程写 → 本节点失效 | 本地 cpu 写的 SnpCleanInvalid/SnpUnique | 本次死锁 |
| ReadUnique(RecallUnique) | 远程要独占/recall 脏数据 | 本地 cpu 写的 invalidating snoop | 会 |
| ReadShared(NoProxyOp) | 本地读 miss，EP-RNF 去外层取数 | 期间来的 invalidating snoop | 会 |

## 6. 修复方向（待定，记录以供实现）

原则：**修复落点在 EP-RNF 的 snoop 响应路径（RN-F↔HN-F snoop 接口），不在 EPBackend 高层
逻辑；语义是"全局已定序者胜、本地未定序抢跑者 stale-retry"，绝不能谎报（回 SnpResp_I 会
让本地写错误拿到独占 → split-brain）。**

`recvSnoopMsg` 收到同址 snoop 且本行有 in-flight CHI txn 时，不再无差别排队，而是：
- self-snoop / 良性（recall/pending 守卫覆盖，能即时满足）→ 立即走 `handleSnp*` 响应；
- 冲突（in-flight 代表全局已定序的写、来的 snoop 是本地/较晚抢占）→ 走仲裁：以全局序为准，
  让"输者"收到 stale / abort-retry，不制造环。复用 `EPBackend.cc:1550` 的
  "TC16 dual-upgrade race LOSER path (abandon-and-downgrade)" stale-SnpResp 机制，
  从"CPU held-upgrade 冲突"推广到"EP-RNF in-flight 事务 vs 冲突 snoop"。

待确认的可行性前提：
- EP-RNF 的 snoop 响应能否携带 stale 标志、`handleSnpCleanInvalid` 的 stale-SnpResp 路径
  能否直接复用；
- 仲裁判定依据：用 UBCC epoch/reqId 比较，还是简化规则"EP-RNF 有 in-flight 事务即让位
  （本地 CleanUnique 尚未定序，天然应让路给已进入全局流程的操作）"。

## 7. 相关前置修复（已在工作树，配合本问题）

- `UBAdapter.cc`：TC98 ReadReq 去重（`_inflightReadReqs` 补 insert，修 322MB→44KB 风暴）
- `UBCCController.cc/.hh`：home 侧统一 invalidate fanout（processOuterUpgradeReq）+ fanout
  按发送时目录状态重算 effectiveMask + 空 mask 直接转 grant
- `EPBackend.cc`：收到 InvalidateReq 时若无本地副本立即 ack（stale sharer 幂等）
- `e2e_tc95_8n2s_barrier_stress.c`：barrier activeThreads 改为 NUM_SOCKETS（修 TC95）

以上解除了死锁#1；本文档的死锁#2（EP-RNF snoop 排队）尚未修复。
