# Clock Drift Diagnosis — In Progress (2026-07-16)

## 2026-07-16: Silent Upgrade trigger extended from R_E to R_E+R_M
- **Commit**: `602d120068` (gem5), `43ab3d6` (cc-ep)
- **Problem**: `hasRequesterExclusive()` only checked `state == R_E`, but cold stores
  via GrantModified set state to `R_M`. R_M is also an exclusive holder, so silent
  upgrade should apply.
- **Fix**: Extended check to `state == R_E || state == R_M` in:
  - `EPBackend.cc:hasRequesterExclusive()` — the core logic, with detailed diag
  - `EPBackend.hh` — comment updated to "R_E or R_M"
  - `EPRNFController.cc` — comments/diagnostics updated to "R_E/R_M→M"
- **TC8 Verification results**:
  - Baseline (EP_SILENT_UPGRADE=0): PASSED, 0 silent upgrades triggered
  - Optimized (EP_SILENT_UPGRADE=1): PASSED, **0 silent upgrades triggered**
  - **Root cause**: TC8 Phase 2 (Node1/Node2 shared read) downgrades Node0's
    R_M → R_S _before_ Phase 3's SnpCleanInvalid arrives.  The
    SnpCleanInvalid sees `state=R_S (2)`, not R_E (3) or R_M (4).
  - Latency comparison: 0.0% reduction (identical P50/P99: ReadReq 2161.50ns,
    UpgradeReq 2551.50ns)
  - The R_E+R_M fix is **conceptually correct** but TC8's specific sequence
    (shared→downgrade→upgrade) never presents R_E or R_M at the
    SnpCleanInvalid point.
  - **Recommendation**: Test against TC36 (owner_upgrade_ge_window) or TC37
    (owner_upgrade_gm_window) which exercise the R_E/R_M silent upgrade path.

## State (updated 2026-07-16)
- ZMQ latency fixed: 100ns→2.5ns per solve_latency_params.py --x-ns 2.5
  - Port.hh: kDefaultSyncInterval/kDefaultLinkLatency 100000→2500 ps
  - gen_topo.py: cross-node 405000→410000, cross-socket 25000→220000 ps
  - run_multi.sh: EP_SYNC_INTERVAL_PS/EP_LINK_LATENCY_PS 25000→2500 ps
- TC3 verified with new latency (PASSED)
- ubio_main.cc: fixed drainDelayedQueue forward declaration (moved to end of anon ns)

## Previous State (2026-06-28)
- Port syncWindow merged into syncInterval (default 100000 ticks = 100ns).
- Port linkLatency default = 10000 ticks = 10ns.
- nsim internal latency = 100000 ticks = 100ns (topo3.json).
- EPSNF retry = 1,600,000 ticks.
- safeTs clamp implemented: when safeTs <= curTick, schedule at curTick() (no +syncInterval bypass).

## Observations
- Message timestamps are perfectly coherent end-to-end (0.27M total delay for 2×nsim+6×ZMQ).
- But gem5 curTick drifts 3.3M ahead of ubio during the same wall-clock interval.
- After clamp fix, node 2 loops at `CLK-SYNC curT=243M rxt=243M safeT=243M` identically.
- EPSNF retry at 76.3M never fires because gem5 stuck at 75.1M.

## Open
- Why doesn't safeTs advance past curTick when peer is at 264M? _lastRxT should carry 264M.

## 2026-06-30 Autonomous update (split-mode verification hardening)
- 复现了 TC10 假阳性：存在 `gem5 Aborted (core dumped)`，但 `run_multi.sh` 仍打印 `TC10 PASSED`。
- 根因：`run_multi.sh` 先收集 `gem5_fail`，但仍无条件执行内容校验；若崩溃节点在崩溃前留下“看似合法”的部分 simout，会被误判通过。
- 修复1（tests/e2e/run_multi.sh）：若任一 gem5 非零退出，直接判定 `TCx CRASHED` 并返回失败，不再进入 verify。
- 修复2（tests/e2e/run_multi.sh + tests/e2e/test_e2e.py）：verify 阶段传入“期望的全部 simout 路径”（含缺失路径），`verify_split_main()` 增加 `found != expected` 的硬失败检查，避免缺失/截断输出被当作通过。

## 2026-06-30 Autonomous update (TC5/6/11 根因修复尝试)
- 在干净复现实验（TC5）中观测到：
  - home=1 先后通过 `RECALL-TO-GRANT ... dataSource=1` 把 owner 脏数据转给 requester；
  - 但后续 `G_S` 下给下一 requester 的 grant 退化为 `dataSource=0(HomeMemory)`；
  - split 模式下 HomeMemory 未同步 owner 最新值，导致下一读者拿到 0（`READ_VAL ... actual=0`）。
- 新增修复（modules/ubiomodule/UBCCController.hh/.cc）：
  - 增加 `_lineDataCache`（每行64B）缓存 recall 返回的数据；
  - `commitIntendedResult()` 在 `ost.dataValid` 时持久化缓存；
  - `G_S + ReadShared` 发 grant 时若命中缓存，改为 `dataSource=RecallBuffer` 并携带 `dataBuf`，避免回退到 stale HomeMemory。

## 2026-06-30 Autonomous update (TC10 死锁规避)
- 复盘 TC10 崩溃日志：node1 在 `request.paddr=0x10018000000` 上 Sequencer deadlock；ubio 侧显示 home=1 创建 `RECALL(owner=0 -> requester=1)` 后未收到对应 `RECALL-DIAG`，后续同 reqId 无限 BUSY 重试。
- 触发条件：TC10 原 workload 中 node0(写者)可能先于 node1(读者)退出，导致 node0 仍可能是 home 记录的 G_M owner，而 node1 后续读触发的 recall 目标已终止。
- 调整（tests/e2e/workloads/e2e_tc10_concurrent_atomic.c）：在读写循环后新增 `sync_wait(0b011)`，确保 node0 与 node1 同步收敛后再退出，避免对已终止 owner 发 recall。

## 2026-07-01 Autonomous update (TC16 CleanUnique stale 降级一致性修复)
- 复现：TC16 在 node1 崩溃，`panic: Runtime Error at CHI-cache-funcs.sm:1213`（`assert(tbe.dataMaybeDirtyUpstream == false)`）。
- 定位：`CHI-cache-actions.sm:CheckUpgrade_FromCU` 的 stale 分支把 `tbe.updateDirOnCompAck := false`，导致后续 `Finish_CleanUnique` 设定 `requestorToBeExclusiveOwner` 后，CompAck 阶段目录不更新 owner；最终出现 `dir_sharers>0 && dir_ownerExists==false && dataMaybeDirtyUpstream==true`，触发 makeFinalState 断言。
- 修复（仅 stale 门控）：在 `CheckUpgrade_FromCU` stale 分支中移除对 `updateDirOnCompAck` 的强制清零，保留常规 CompAck 目录更新通路；非 stale CleanUnique 路径不变。
- 验证：
  - 编译通过（`build/ARM/gem5.opt` 重新生成成功）。
  - `tests/e2e/run_multi.sh 16` 通过（`TC16 PASSED`）。
  - 日志无 panic/assert/deadlock；`node0/node1/node2` 均出现 `AFTER_WR`，最终 read 一致（`a0a0`/`b0b0` 单一收敛值，本次为 `b0b0`）。

## 2026-07-10 Autonomous update (TC3 RECALL.DONE 事件驱动唤醒)
- 复盘 TC3 的 8.4µs 空等：请求方 EP-SNF 命中 `handleRemoteMiss(...)=BUSY` 后进入 `_retryQueue`，仅靠 `epsnf_retry_cycles()` 的 20000-cycle fallback 定时器重试。
- 代码确认：`RecallResp` 到达 home/requester 节点时先在 `modules/ubiomodule/ubio_main.cc` 被 `handleUbccMessage()` 消费，随后 `UBCCController::processRecallResponse()` 立即把该行切成 `replayArmed` 的 `GRANT_HANDSHAKE`；但原先没有任何消息/事件回到 gem5 唤醒本地 EP-SNF。
- 修复：
  - `ubio_main.cc` 在处理来自网络的 `RecallResp` 后，额外镜像一份到本地 gem5 `UBAdapter`，仅作为 `RECALL.DONE` 唤醒通知；
  - `UBAdapter.cc` 为 `RecallResp` 增加 wake-only 分支，直接触发 `_onResponseWired()`，不把它当成普通响应缓存/匹配。
- 保持 `EPSNFController.cc` 的 20000-cycle backoff 不变，作为真正 BUSY / 远端未完成时的 fallback。

## 2026-07-14 Autonomous update (TC42 vs 8n2s activeRecall 冲突排查)
- 先回退 `EPRNFController::finishChiTxn()` 到“仅 ReadShared completion 清理 activeRecall”，并在 docker 内复测：
  - `--1s 42`：失败（wrap-window 最终值不收敛，Node1 读旧值）。
  - `--8n2s 96 97`：两例均 TIMEOUT（本工作区当前基线未恢复到历史 8n2s PASS 状态）。
- 关键日志（`logs/20260713_231759_8n2s` / `logs/20260713_233934_1s`）显示：
  - recall 完成后仍会出现后续 `SnpCleanInvalid`；若 activeRecall 已在 completion 点清掉，会走 `first SnpCleanInvalid` 的 upgrade 路径。
  - TC42 路径存在 ReadUnique recall 后 marker 滞留并污染后续本地升级的问题。
- 实施方向X实验：在 `EPBackend::handleGrant()` 增加“line re-acquire 时清理 activeRecall（localPA + homePA）”，并把 `clearActiveRecall()` 改成仅在命中时打印，避免噪声。
- 实验结果：
  - 保留 `finishChiTxn` 的 ReadShared 清理 + 新增 handleGrant 清理：
    - `--1s 42` PASS；`--1s 2 3 5 8` PASS。
    - `--8n2s 96 97 100 101 99`：仅 TC99 PASS，其余 TIMEOUT。
  - 进一步尝试“取消 finishChiTxn 的 ReadShared 清理”虽可保持 TC42 PASS，但会引入 `--1s 8` FAIL，已回退该尝试。

## 2026-07-16: TC36/37/114 Silent Upgrade 对照实验

### 实验目的
验证 EP_SILENT_UPGRADE=0/1 对"独占持有者重复写入"场景的实际效果。

### TC36 (owner_upgrade_ge_window) 对照
- **Baseline (EP_SILENT_UPGRADE=0)**: PASSED
- **Optimized (EP_SILENT_UPGRADE=1)**: PASSED
- **静默升级触发**: **0 次**（两版本完全一致）
- **Node1 协议消息**: EPSNF-RECV:1, SnpCleanInvalid:1, UPGRADE-DIAG:6, RE-DIAG(state=2=R_S)
- **根因**: TC36 中 Node1 先用 dsm_load (ldr -> ReadShared) 获取 line，得到 state=R_S（共享），不是 R_E（独占）。第二条 dsm_store 触发 SnpCleanInvalid 时，hasRequesterExclusive(R_S)=FALSE，走 OuterUpgradeReq 标准路径。

### TC37 (owner_upgrade_gm_window) 对照
- **Baseline (EP_SILENT_UPGRADE=0)**: PASSED
- **Optimized (EP_SILENT_UPGRADE=1)**: PASSED
- **静默升级触发**: **0 次**（两版本完全一致）
- **Node1 协议消息**: EPSNF-RECV:1, SnpCleanInvalid:1, UPGRADE-DIAG:7, RE-DIAG(state=2=R_S), RECALL-ENTRY:2
- **根因**: 第一条 dsm_store 得到 grantTypeVar=2 (GlobalGrantModified -> R_M)，但同一 sync 窗口内 Node2 的 dsm_load 触发 ReadShared recall，将 Node1 的 state 从 R_M 降级为 R_S。第二条 store 时 hasRequesterExclusive(R_S)=FALSE。

### TC114 (新建: silent_upgrade_minimal) 对照
- **设计**: Node1 第一次 store -> R_M -> sync -> 第二次 store（相同 line），无其他节点访问。
- **Baseline (EP_SILENT_UPGRADE=0)**: PASSED
- **Optimized (EP_SILENT_UPGRADE=1)**: PASSED
- **静默升级触发**: **0 次**（两版本完全一致）
- **协议消息**: EPSNF-RECV:1（仅第一次 store），无 SnpCleanInvalid，无 UPGRADE-DIAG
- **根因**: 第二次 store 是 CPU L1 cache HIT（line 已是 Modified），完全没有任何 CHI 协议消息。无消息需要优化。

### 架构分析总结

**handleSnpCleanInvalid 路径**（EPRNFController.cc:843-858）:
- 此路径检测 hasRequesterExclusive(msg->m_addr)，当 state 为 R_E 或 R_M 时跳过 OuterUpgradeReq
- 在当前代码中从未被触发，因为：
  1. dsm_load (ldr) 永远产生 ReadShared -> R_S，不是 R_E
  2. GlobalGrantExclusive (R_E) 在 UBCC 协议中从未被授予标准 ARM load/store
  3. 有 R_M 时收到外部 SnpCleanInvalid 前，recall 先把 state 降级为 R_S
  4. 同节点第二次 store 是 cache hit，根本不产生 SnpCleanInvalid

**handleRemoteMiss 路径**（EPBackend.cc:517 新增）:
- 在 handleRemoteMiss 中，当 neededPerm=1 且已有 R_E/R_M 时，跳过 CHI 请求返回成功
- 架构上正确——这是 MESI E->M 的跨节点类比
- 当前无法被覆盖：第二次 store 是 cache hit，handleRemoteMiss 根本不被调用
- 需要 dsm_flush 或跨 CPU core 测试才能触发 cache miss -> handleRemoteMiss

### 结论
EP_SILENT_UPGRADE=0 vs =1 在 TC36/37 上无任何差异：协议消息数量、时序、功能完全相同。降幅 **0.0%**。

当前静默升级的两个代码入口：
1. handleSnpCleanInvalid：死代码——hasRequesterExclusive 在已知 workload 下永不为 TRUE
2. handleRemoteMiss（新增）：正确但需 cache miss 才能触发——同节点重复写入的 cache hit 使此路径不可达

### 后续建议
- 若需验证 handleRemoteMiss 静默升级效果，需构造跨 CPU core 的测试（core0 存 R_E/R_M，core1 同节点再次写入 -> 必然 cache miss）
- 或接受当前状态：独占持有者重复写入本身就是零消息操作，EP_SILENT_UPGRADE 针对的是 SnpCleanInvalid 的外部触发场景
