# 当前状态与待解决问题

> 更新：2026-07-14（本轮修复：barrier per-node 语义、TC42 recall stale 标记、TC80/82/84/85/91 primary check）
> HEAD: `8451ad8` (+ 未提交本轮改动)

---

## 本轮修复摘要（2026-07-14）

| 问题 | 之前的（错误）诊断 | 实测确认的真实根因 | 修复 | 结果 |
|------|-------------------|-------------------|------|------|
| **TC80/82/91 TIMEOUT** | "纯 PDES 性能 / 热点竞争，数据正确" | barrier **per-thread 发送 vs ubio per-node 聚合** 语义不匹配：无 primary check 的 workload 4 个 CPU 都进 barrier，第一个到达的线程即触发 BarrierReached，release 提前，generation 错位 → home 节点在末尾 barrier 死等 | (1) `sync_wait` 改本地聚齐再发一次 + release 带 seq 校验；(2) TC80/82/91 workload 加 primary CPU check | **全 PASS** |
| **TC84/85 TIMEOUT** | "纯 PDES 性能瓶颈" | 同上 barrier bug（TC84/85 缺 cpu_index primary check，node0 的 4 CPU 都进 barrier） | workload 加 primary CPU check | **PASS**（1200s→~1-2min） |
| **TC42 MISMATCH** | "EP-RNF 一次性注册，第二次 CleanUnique 静默升级" | 实测 DPRINTF 证伪：EP-RNF 注册正常。真因：**`_activeRecallPAs` stale 标记**——node1 被 **ReadUnique**-recall 后标记未清理（旧代码只清 ReadShared recall），后续真正的 store-upgrade CleanUnique snoop 被 RECALL-SNOOP guard 误判为 recall-induced，静默回 SnpResp_I，跳过 OuterUpgradeReq | `EPRNFController::finishChiTxn` 对 ReadShared **和** ReadUnique recall 都清理 activeRecall | **PASS** |
| **~~fix_tc98 Fix A/B~~** | 待实施（requesterNode 修正 + 指数退避） | 代码追踪证伪：requesterNode 已正确=真实请求节点，8 节点产生 8 个不同 requester，无 same-requester BUSY 风暴 | 不实施（挪到性能优化 TODO） | — |

---

## 一、已完成修复

| 问题 | 根因 | 修复 | 涉及文件 |
|------|------|------|---------|
| **TC90 barrier 死锁** | ubio 聚合 key 只有 `mask`，不同 generation 的 BarrierReached 交错污染 | `UBBarrierBody` 加 `uint32_t seq`；`SyncWaitManager::BarrierState` 加 `generation`；ubio 聚合 key 改为 `(mask, seq)`；workload 加 primary CPU check | `protocol/CoherenceMessage.hh`, `sync_wait.hh/.cc`, `UBAdapter.cc/.hh`, `ubio_main.cc`, `e2e_tc90_8node_all_to_all.c` |
| **TC39/TC90/TC101 MISMATCH** (DSM data path) | `GrantDataSource::HomeMemory` 从 gem5 本地 physMem 读取——分割模式下 physMem 从不被 DSM 更新；G_S+RS 路径不查 `_lineDataCache` | Fix 1: WritebackReq 携带 64B dirty data → ubio 写入 DsmDataStore + `_lineDataCache`。Fix 2: 所有 grant data 统一从 ubio 侧获取，通过 ReadResp payload 传递；gem5 端废弃 HomeMemory/physMem 读 | `CoherenceMessage.hh`, `UBAdapter.cc/.hh`, `EPBackend.cc/.hh`, `EPSNFController.cc/.hh`, `ubio_main.cc`, `UBCCController.hh` |
| **ResidentDir 重构** | 开放寻址 Robin Hood hash table：tag 单独 vector 占 512KB（不在 SRAM 预算内），eviction 全表扫描 O(n) | 组相联 set-associative + tree-based pseudo-LRU + Bloom filter。运行时搜索最优 (ways, sets)。当前 auto-search: 8192 sets × 7 ways = 57,344 entries（dir 427KB + bloom 60KB = 487KB / 512KB SRAM） | `ResidentDir.hh/.cc` |
| **TC98 优化（部分）** | BUSY 日志爆炸（625K 行/120s），队列满导致 push-grant 路径失效 | `MAX_PENDING_PER_PA` 16→32，8 处 BUSY 日志 rate-limit（前 3 次 + 每 1000 次），TC98 测试超时升至 1500s | `UBCCController.hh/.cc`, `run_multi.sh` |
| **TC102 新测试** | — | 验证 WritebackReq dirty data 持久化 + set-conflict L2 eviction 后跨节点可读 | `e2e_tc102_writeback_data_persist.c`, `test_e2e.py` |
| **UBAdapter pendingT 死代码修复** | `pendingT > curT && pendingT < nextT`（nextT==curT when stalled）永假 | 去掉 `pendingT < nextT` 条件 | `UBAdapter.cc` |

---

## 二、已验证生效的机制

### EP-RNF Co-Sharer 注册（RegisterEPRNF_OnSharedHint）

HN-F 的 `Initiate_CleanUnique` 中包含 `RegisterEPRNF_OnSharedHint`，在 `BUSY_BLKD/BUSY_INTR` 状态收到 `CompData(m_shared_hint=true)` 时，把 EP-RNF 的 MachineID 加入 `dir_sharers`。后续 L1 CleanUnique(S→M) 时 `dir_sharers.count() ≥ 2`（含 EP-RNF），触发 `SendSnpCleanInvalidNoReq` → EP-RNF → `OuterUpgradeReq` → UBCC 升级目录。

**已验证路径**（TC42 debug 日志）：

```
node=1 EP-SNF: neededPerm=0 writeIntent=0 → ReadNoSnp+ReadShared → UBCC grant G_S
    → EP-SNF CompData(m_shared_hint=true) → HN-F RegisterEPRNF_OnSharedHint
    → dir_sharers += EP-RNF

node=1 L1: dsm_store → CleanUnique(S→M)
    → HN-F Initiate_CleanUnique: dir_sharers.count()=2 (L2+EPRNF)
    → SendSnpCleanInvalidNoReq → EP-RNF → handleSnpCleanInvalid
    → OuterUpgradeReq → UBCC → processOuterUpgradeReq → G_M committed  ← ✅
```

**但这只在第一次 CleanUnique 时生效**。升级后 HN-F 收到 `SnpResp_I`，`dir_sharers` 被更新（EP-RNF 被移除）。后续如果 L1 仍有数据（未被 evict），再次写又走 CleanUnique 静默升级 → UBCC 收不到通知。

---

## 三、回归测试状态（2026-07-14 本轮实测）

| 拓扑 | 通过 | 失败 | 详情 |
|------|-----|------|------|
| 1s   | TC2/3/5/8/42/80/84/85 全 PASS | — | TC42、TC80、TC84、TC85 本轮全部修复 |
| 2s   | TC32/33/34/35/39 PASS | — | — |
| 8n1s | TC82/90/91/92/93/94 PASS | — | **TC82/91 本轮修复** |
| 8n2s | TC99 PASS | **TC2/95/96/97/100/101 TIMEOUT，TC98 TIMEOUT** | 8n2s 拓扑系统性 home 死锁 / 全局死锁（见 P0） |

> 说明：8n2s（16-plane 双 socket）的失败是**独立的、系统性的 home 节点死锁 / PDES 全局死锁**，与本轮修的 barrier/TC42 无关（本轮修复未引入回归——同类 TC 在 1s/2s/8n1s 下全过）。这是本轮之后的**下一优先级**。

---

## 四、待解决问题

### P0: 8n2s 拓扑系统性死锁（下一优先级）

**现象**（`TIMEOUT_SEC=600`，TC98 给到 1800s）：
- **home 节点死锁**：TC2/96/100/101 —— 其余 7 节点正常 `doExitCleanup` 退出后，home(node0) 卡在 CLK-SYNC 无限处理 Recall/Clear，无法自行终止。
- **全局死锁**：TC95/97 —— 全 8 节点卡在 CLK-SYNC，PDES 零进展。
- **全局活锁**：TC98 —— 全 8 节点持续 ReadReq 洪泛到 node0 + Recall，无终止。
- **唯一通过**：TC99（per-plane slot contention，访问模式不触发 home 死锁）。

**性质**：全部无 crash / 无 panic / 无 IPC bind 失败，是纯协议/PDES 死锁。初步方向：
1. home 节点在所有 requester 退出后无法收敛终止（可能需要"所有 requester 已 TERMINATE 则 home 自终止"的检测）；
2. 跨 socket conservative sync 对齐（`EP_SYNC_INTERVAL_PS=25000` 对 16-plane 可能不足）；
3. TC98 曾疑似的 BUSY retry 风暴（注意：fix_tc98 的 Fix A/B 已证伪，见下）。
**尚未深入定位，留待下一轮。**

---

### P0（已否决）：fix_tc98_retry_storm.md 的 Fix A/B

**结论：不实施，判据已被代码追踪证伪。**
- Fix A 前提"`requesterNode = _nodeId` 始终 = home node"错误。实际每节点有独立 EPBackend（`_nodeId` 各不相同），请求由**发起节点自己的** EP-SNF 处理，`requesterNode` 已正确=真实发起节点 X，经 `req.h.requesterNode` 原样传到 home UBCC（`UBAdapter.cc:311` → `ubio_main.cc:581`）。8 节点读同一行 → UBCC 看到 8 个不同 requester → 走 enqueue，**无 same-requester BUSY 风暴**。最新日志实测 BUSY 计数 = 0。
- Fix B（指数退避）当前无 retry 风暴可退，属过度设计。
- 两者作为**性能优化候选**保留在 TODO（见第六节），本轮及短期不实施。

---

### P2: TC42 后续 —— epoch wrap 验证（已随本轮 PASS）

TC42（epoch 0xFFFFFE→FF→0→1 序列）本轮已 PASS，node0/1/2 均读到 v4=0x42A00001。recall stale 标记修复后 epoch wrap 未见异常。若后续 8n2s 死锁修复涉及 epoch 逻辑，需再回归一次 TC42。

---

## 四之二、性能优化 TODO（非正确性问题，暂缓）

| 项 | 内容 | 来源 |
|----|------|------|
| perf-1 | EP-SNF retry 指数退避（`EP_RETRY_MIN/MAX_CYCLES`）—— 防御性，防止未来 retry 风暴 | 原 fix_tc98 Fix B |
| perf-2 | PDES lookahead 优化 —— IDLE 进程拖慢全局 safeTs，长期需要 | 原 TC80/84/85 分析（注：TC80/84/85 已由 barrier 修复解决，此项仅剩理论优化空间） |
| perf-3 | 8n2s 跨 socket conservative sync 参数调优 | 本轮 8n2s 死锁分析 |

---

## 五、关键文件变更清单

### 已修改文件（当前 HEAD: `8451ad8`）

```
protocol/CoherenceMessage.hh              — UBBarrierBody.seq, UBWritebackReqBody.data
gem5/src/sim/sync_wait.hh                 — BarrierSendFn + BarrierState.generation
gem5/src/sim/sync_wait.cc                 — barrierArrive/releaseBarrier generation logic
gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc — sendBarrierReached seq, busy-wait log, pendingT fix, sendWritebackReq data
gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.hh — sendBarrierReached + sendWritebackReq signatures
gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc — handleRemoteMiss payload path, handleWriteback data
gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh — handleWriteback signature
gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc — WriteNoSnp passes dirtyData, PendingWriteback carries data, EP-SNF-DEBUG
gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.hh — PendingWriteback +data field
modules/ubiomodule/ResidentDir.hh         — set-assoc rewrite (full)
modules/ubiomodule/ResidentDir.cc         — set-assoc rewrite (full)
modules/ubiomodule/UBCCController.cc      — hasFreeSlotForPa, readDsmData path, BUSY rate-limit
modules/ubiomodule/UBCCController.hh      — updateLineDataCache, copyLineDataCache
modules/ubiomodule/ubio_main.cc           — barrier (mask,seq) key, writeback data persist, grant payload unification
tests/e2e/run_multi.sh                    — per-TC timeout override
tests/e2e/test_e2e.py                     — TC102 registration
tests/e2e/workloads/e2e_tc90_8node_all_to_all.c — primary CPU check
tests/e2e/workloads/e2e_tc102_writeback_data_persist.c — new test
docs/fix_tc98_retry_storm.md              — TC98 fix plan (Fix A requesterNode + Fix B exponential backoff)
scripts/inject_debug.py                  — temp debug injection (可删除)
```

### 本轮（2026-07-14）新增/修改文件

```
gem5/src/sim/sync_wait.hh       — BarrierState +localExpected/+reachedSent；releaseBarrier(mask,seq)
gem5/src/sim/sync_wait.cc       — barrierArrive 改本地聚齐再发一次(per-node)；releaseBarrier seq 校验 + generation 推进修复
gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc — 两处 releaseBarrier 调用传入 bc->b.barrier.seq
gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc — finishChiTxn 对 ReadShared+ReadUnique recall 都清 activeRecall (TC42 修复)
tests/e2e/workloads/e2e_tc80_cross_node_latency.c   — 加 cpu_index primary CPU check
tests/e2e/workloads/e2e_tc82_8node_ring_latency.c   — 加 cpu_index primary CPU check
tests/e2e/workloads/e2e_tc84_cacheline_capacity.c   — 加 cpu_index primary CPU check (TC84/85 共用)
tests/e2e/workloads/e2e_tc91_8node_hotspot.c        — 加 cpu_index primary CPU check
```

### 已否决 / 不再需要的改动
```
CHI-cache-actions.sm  — 原计划"EP-RNF 重新注册"：证伪，EP-RNF 注册本无问题，勿改
EPSNFController.*      — 原 Fix A requesterNode：证伪，勿改
EPSNFController.*      — 原 Fix B 指数退避：挪到 perf TODO
```

---

## 六、编译命令

```bash
# ubio
docker run --rm -v $(pwd):/workspace -w /workspace ubcc-dev:ubuntu20.04 \
  bash -c 'bash scripts/build_ubio.sh'

# gem5
docker run --rm -v $(pwd):/workspace -w /workspace ubcc-dev:ubuntu20.04 \
  bash -c 'cd /workspace/gem5 && scons build/ARM/gem5.opt -j32'
```

## 七、测试命令

```bash
# 回归测试
TIMEOUT_SEC=120 bash tests/e2e/run_multi.sh --1s 2 3 8 10 102 22 23 28
TIMEOUT_SEC=120 bash tests/e2e/run_multi.sh --2s 32 33 34 35 39
TIMEOUT_SEC=300 bash tests/e2e/run_multi.sh --8n1s 90 91
TIMEOUT_SEC=600 bash tests/e2e/run_multi.sh --8n2s 2 95 96 97 99 100 101

# 问题测试（等待修复后）
TIMEOUT_SEC=120 bash tests/e2e/run_multi.sh --1s 42   # TC42
TIMEOUT_SEC=1200 bash tests/e2e/run_multi.sh --1s 80 84 85  # 性能测试
TIMEOUT_SEC=3600 bash tests/e2e/run_multi.sh --8n2s 98  # TC98
```
