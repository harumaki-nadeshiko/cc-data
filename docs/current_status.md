# 当前状态与待解决问题

> 更新：2026-07-14
> HEAD: `8451ad8`

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

## 三、回归测试状态

全量回归（排除 TC9、TC98），`TIMEOUT_SEC=300/600` 下：

| 拓扑 | 通过 | 失败 | 详情 |
|------|-----|------|------|
| 1s (51 TC) | 47 | 4 | **TC42 FAIL** (epoch wrap), TC80/84/85 TIMEOUT（纯性能，数据正确） |
| 2s (9 TC) | 9 | 0 | 全通过（含之前失败的 TC39） |
| 8n1s (6 TC) | 5 | 1 | **TC91 TIMEOUT**（热点竞争，数据正确） |
| 8n2s (6 TC) | 6 | 0 | 全通过（含之前失败的 TC101） |
| **合计 (72 TC)** | **67** | **5** | |

---

## 四、待解决问题

### P0: TC42 — CleanUnique 静默升级（数据正确性 bug）

**现象**：node 1 读到 `0x42A00000`（v3）而非 `0x42A00001`（v4）。EP-SNF 日志显示 v4 的 WriteUnique 从未发出——node 0 的 CleanUnique 走了 HN-F 静默 auto-upgrade。

**根因**：`RegisterEPRNF_OnSharedHint` 在第一次 ReadShared grant 时把 EP-RNF 注册为 HN-F co-sharer，第一次 CleanUnique 能正确触发 snoop 通知 UBCC。但升级后 EP-RNF 从 `dir_sharers` 被清除。后续再写又走 CleanUnique 静默升级，UBCC 收不到通知。

**修复方向**：确保 EP-RNF 在每次需要时都重新注册为 sharer。即在被清掉后，下次 HN-F CompData 响应路径重新 `dir_sharers.add(epRnfMachineID)`。需要确认：G_S 已在目录中时，后续 ReadShared 是否经过同一个 `RegisterEPRNF_OnSharedHint` transition。

---

### P0: TC91 / TC98 — BUSY retry 风暴

**现象**：TC91 8 路竞争 600s 超时（8 READ_VAL 全 MATCH，数据正确），TC98 16 路竞争 1500s 超时（0/16 round 完成）。

**根因链**：

```
EP-SNF requesterNode = _nodeId（始终 home node）
    → UBCC "same requester" → BUSY（不入队 _pendingRequesters）
    → push-grant 路径不工作
    → 每 20000 cycle retry × N 路 → 海量跨进程 ReadReq
    → 淹没 networksim PDES 带宽
    → InvalidateAck / RecallResp 延迟 → outstanding 永远不 clear
    → 恶性循环
```

**修复 plan**：已写入 `docs/fix_tc98_retry_storm.md`，两个 Fix（均未实施）：

| | 内容 | 涉及文件 |
|---|------|---------|
| **Fix A** | 修正 `requesterNode`：从 CHI `m_requestor` (MachineID) 推导原始请求节点，不再用 `_nodeId` | `EPSNFController.cc`, `EPBackend.hh/.cc`, `EPSNFController.hh` |
| **Fix B** | EP-SNF retry 指数退避：`EP_RETRY_MIN_CYCLES`（默认 20K）、`EP_RETRY_MAX_CYCLES`（默认 2M）、指数因子 ×2 | `EPSNFController.cc/.hh` |

---

### P1: TC80 / TC84 / TC85 — PDES 性能瓶颈

| TC | 最大超时 | 进展 | 数据正确性 |
|----|---------|------|-----------|
| TC80 | 1200s | 8 次 latency 采样 + 1 READ_VAL MATCH 全部产出 | 数据全对 |
| TC84 | 1200s | 1/50 条 MATCH 产出 | 数据对但未完成 |
| TC85 | 1200s | 同 TC84（同一 workload 映射） | 同上 |

全部是纯 PDES 性能问题：IDLE 进程拖慢全局 safeTs 取 min，导致 networksim 时钟推进极慢。无逻辑 bug。短期可通过继续增大超时绕过，长期需要 PDES lookahead 优化。

---

### P2: TC42 后续：epoch wrap 验证

TC42 的 epoch field 从 0xFFFFFE → 0xFFFFFF → 0 → 1 序列中，CleanUnique 静默升级可能导致 epoch wrap 时 UBCC 目录状态不一致。CleanUnique 修复后 TC42 需重新验证。

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

### 待修改文件（TC42 + TC91/98）

```
CHI-cache-actions.sm          — Initiate_CleanUnique: 确保 EP-RNF 重新注册为 sharer
EPSNFController.cc            — Fix A: requesterNode 修正
EPBackend.cc/.hh              — Fix A: handleRemoteMiss originNode 参数
EPSNFController.hh            — Fix A: RetryEntry + originNode
                               Fix B: RetryEntry + retryCount, exponential backoff
EPSNFController.cc            — Fix B: retryInterval() + schedule 修改 (3 处)
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
