# UBCC 形式化可靠性结果报告（2026-08-07）

> 版本基线与免责声明：本报告只记录 2026-08-07 在指定工作区上实际执行的
> TLC 模型检查结果。所有 PASS 均是在**有限、聚焦**的抽象状态空间内成立，
> 不代表完整生产代码的形式证明。模型文件当前位于未提交的工作区
> （`git status` 显示 `??`），主仓 HEAD `a8dcd41` 只是基础提交，
> 不包含本次新增的 focused 模型与结果日志。

---

## 1. 执行环境与资源限制

| 项 | 值 |
|----|----|
| 工作目录 | `/mnt/data2/cgc/cc-ep` |
| 主仓 HEAD | `a8dcd41`（`docs: define acceptance and research handoff`） |
| gem5 子模块 | `fc1a97134361fddc09370e59578b4713e0278aae`（`refactor: isolate framework transport backend`） |
| JDK | OpenJDK 11.0.27（build `11.0.27+6-post-Ubuntu-0ubuntu120.04`，64-Bit Server VM） |
| 主机 | Linux `5.15.0-58-generic` amd64，32 逻辑核，TLC 可见堆 25022MB |
| TLC 工具 | `tla2tools.jar`，TLC2 Version `2026.05.26.235334`（rev `4ba7d88`） |
| TLC jar SHA256 | `237332bdcc79a35c7d26efa7b82c77c85c2744591c5598673a8a45085ff2a4fb` |
| Worker 限制 | **`TLC_WORKERS=4`，单次计算最多 4 个逻辑核**（遵守 `verification/formal_reliability_followup_plan_20260807_zh.md` 第 5 节资源规则）；所有日志均显示 `with 4 workers on 32 cores` |
| 执行器 | `verification/tla/run_tlc.sh <model.tla> <config.cfg> <timeout>`（SHA256 `c851140ea06294edf82f26ee5c43f847210180a34b3f8e5bb88586f7f8d024e6`） |
| 原始日志目录 | `verification/results/`；machine-readable 索引为 `formal_run_manifest_20260807.tsv` |

约束说明：所有运行的 worker 数均为 4；调试期间只允许更低（1/2），
禁止 8/16/28 核。日志保留原始 stdout/stderr，未做任何删改。

---

## 2. 模型、配置、性质、状态数与 depth 汇总

四个模型文件及其 SHA256（均在未提交工作区）：

| 模型文件 | SHA256 |
|----------|--------|
| `verification/tla/ubcc_tc157_partial_ack_redrive.tla` | `992e510db3bb3cc3d94d97697a6847b27ff90e53850b47471382ccb81365c9d1` |
| `verification/tla/ubcc_tc159_upgrade_replay.tla` | `efd9bdd007d67861968dc7d864884ba658c6c95b23f1e7608aa054d1a83324b5` |
| `verification/tla/ubcc_tc159_tuple_guards.tla` | `46dd9d7c2abdd2042bed55ffd086900cec11a975fc2bc8c21ef93bf138bfd507` |
| `verification/tla/ubcc_retry_exhaustion.tla` | `09e1835fc2b59a95a3f816823c17972241edede6a4471da2ee02a0cbedf81184` |

### 2.1 运行结果总表

| # | 模型 | cfg | SPEC | 关键常量/开关 | 性质 | generated | distinct | depth | 结论 |
|---|------|-----|------|----------------|------|-----------|----------|-------|------|
| 1 | tc157 | `ubcc_tc157_partial_ack_redrive.cfg` | `Spec` | `Targets={1,2}`, `MaxRetries=2`, stable `ReqId/Epoch` | 11 INVARIANT | 290 | **171** | **17** | **PASS** |
| 2 | tc157 | `ubcc_tc157_partial_ack_redrive_liveness.cfg` | `FairSpec` | 同上 | 11 INVARIANT + `EventuallyCommitted` | 290 | **171** | **17** | **PASS** |
| 3 | tc159 | `ubcc_tc159_upgrade_replay.cfg` | `Spec` | `Targets={1}`, `MaxResends=2`, `ReqId=7`, `OtherReqId=8`, `BaseEpoch=3`；`Strict*Tuple=FALSE`, `RequireNotifyDrop=FALSE` | 11 INVARIANT | 167 | **99** | **19** | **PASS** |
| 4 | tc159 | `ubcc_tc159_upgrade_replay_liveness.cfg` | `FairSpec` | 同上但 `RequireNotifyDrop=TRUE`, `ReserveFinalReplay=TRUE` | 11 INVARIANT + `EventuallyDrained` | 64 | **41** | **16** | **PASS** |
| 5 | tc159 | `ubcc_tc159_upgrade_replay_strengthened.cfg` | `Spec` | `StrictNotifyTuple=TRUE`, `StrictDoneTuple=TRUE`, `EnableMismatchTraffic=TRUE`, `RequireNotifyDrop=TRUE`, `ReserveFinalReplay=TRUE` | 13 INVARIANT | 1785 | **521** | **24** | **PASS** |
| 6 | retry | `ubcc_retry_exhaustion_recover.cfg` | `FairSpec` | `RetryBudget=3`, `MaxDrops=2`, `FaultMode="RECOVER"`, `ReqId=11`, `Epoch=5` | 8 INVARIANT + `EventuallyTerminates` + `RecoveryOutcome` | 8 | **7** | **7** | **PASS** |
| 7 | retry | `ubcc_retry_exhaustion_permanent.cfg` | `FairSpec` | 同上但 `FaultMode="PERMANENT"` | 8 INVARIANT + `EventuallyTerminates` + `PermanentLossOutcome` | 11 | **8** | **8** | **PASS** |
| 8 | tc159 | `ubcc_tc159_upgrade_replay_current_gaps.cfg` | `Spec` | `EnableMismatchTraffic=TRUE`, `Strict*Tuple=FALSE`, `RequireNotifyDrop=FALSE`, `ReserveFinalReplay=FALSE` | 含 `MismatchDoneNoCommit` | 83 | 58 | 8 | **VIOLATED（预期）**：`MismatchDoneNoCommit` |
| 9 | tc159 | `ubcc_tc159_upgrade_replay_current_notify_gap.cfg` | `Spec` | 同上 | 含 `MismatchNotifyNoCompletion` | 170 | 103 | 9 | **VIOLATED（预期）**：`MismatchNotifyNoCompletion` |
| 10 | tc159 | `ubcc_tc159_upgrade_replay_current_budget_gap.cfg` | `FairSpec` | `RequireNotifyDrop=TRUE`, `ReserveFinalReplay=FALSE` | `EventuallyDrained` | 95 | 57 | 17 | **VIOLATED（预期）**：预算耗尽后无限 repoll 死循环 |
| 11 | tuple guard | `ubcc_tc159_tuple_guards_strengthened.cfg` | `Spec` | 每个 PA/node/socket/epoch/reqId 维度取 2 值；`StrictGuards=TRUE` | 5 INVARIANT | 19457 | **896** | **10** | **PASS** |
| 12 | tuple guard | `ubcc_tc159_tuple_guards_current.cfg` | `Spec` | `StrictGuards=FALSE` | `MismatchNotifyNoCompletion` | 497 | 108 | 4 | **VIOLATED（预期）** |
| 13 | tuple guard | `ubcc_tc159_tuple_guards_current_early_done.cfg` | `Spec` | `StrictGuards=FALSE` | `MismatchDoneNoCache` | 513 | 116 | 5 | **VIOLATED（预期）** |
| 14 | tuple guard | `ubcc_tc159_tuple_guards_current_early_commit.cfg` | `Spec` | `StrictGuards=FALSE` | `MismatchDoneNoCommit` | 3438 | 477 | 6 | **VIOLATED（预期）** |

指纹碰撞概率（乐观估计）：本轮日志约为 `9.0E-13` 至 `1.3E-19`，见各日志。

> 说明：`*_expected_violation.log` 是**阴性模型证据**，用于证明“当前实现语义下
> 该性质确实不成立”，按 `verification/results/README.md` 约定，**不得**报告为
> 通过的不变量检查。

---

## 3. TC157 partial Ack redrive：safety + liveness PASS

### 3.1 模型范围

1 个 home、1 个 requester、2 个 invalidate target。每个 target 的首个 Ack 至多
drop 一次，且可注入 duplicate Ack。Home 冻结初始 `targetMask`，单调记录已见
`ackMask`，超时后只重发 `PendingMask = targetMask \ ackMask`。模型现在显式保留固定
`ReqId/Epoch` 并在 retry audit 中记录 target/acked/sent/tuple。target 在产生 Ack 前已本地失效，因此重传的 InvalidateReq
可幂等 Ack。模型显式抽象 CHI 载荷、Bloom/H64 容量、socket 路由与早期
UpgradeDone——这些细节不参与 partial-Ack 记账与有界 redrive 契约。

### 3.2 性质清单（11 INVARIANT + 1 PROPERTY）

| 性质 | 含义 | C++ 对应意图 |
|------|------|--------------|
| `TypeOK` | 类型安全 | 各字段域正确 |
| `AckAccounting` | `ackMask <= targetMask`，`PendingMask` 基数守恒 | `processInvalidationAck` 的 `upgradeAckMask`/`ackMask` 记账 |
| `AckImpliesLocalInvalid` | Ack 者必须已失效本地副本 | `DeliverInvalidate` 先失效后回 Ack |
| `RetryOnlyPending` | 只重发 `targetMask \ ackMask` | `cleanupExpiredInvalidations()` 的 `pendingMask` |
| `NoAcceptBeforeAllAcks` | 全部 Ack 前不 accepted | `invalidateBarrierDone` 前置 |
| `AcceptedOnlyAfterBarrier` | accepted 蕴含 barrier 达成 | 同上 |
| `NoCommitBeforeBarrier` | 全部 Ack 前不 commit | `commitIntendedResult` 前置 |
| `CommitAtMostOnce` | commit 至多一次 | `removeOutstanding` 后幂等 |
| `NoFatalUnderSingleDrop` | 单次 drop 不会 RETRY_EXHAUSTED | 8 次 retry 预算覆盖单 drop |
| `StableRetryTuple` | retry audit 中 reqId/epoch 不变化 | resend 复用 outstanding tuple |
| `CompletionStopsRetry` | barrier 完成后无 invalidate request 在途 | completion 后停止 retry |
| `EventuallyCommitted`（PROPERTY） | 公平下最终 COMMITTED | `WAITING_ALL_ACKS -> WAITING_LOCAL_DONE -> COMMITTED` |

### 3.3 结果

- **safety**（#1）：290 states generated，**171 distinct**，完整状态图 depth **17**，
  `Model checking completed. No error has been found.`，碰撞概率 `1.1E-15`。
- **liveness**（#2）：同空间（171 distinct，depth 17）上检查
  `EventuallyCommitted`，`No error has been found.`。
- 原始日志：
  - `verification/results/tlc_ubcc_tc157_partial_ack_redrive__ubcc_tc157_partial_ack_redrive.log`
  - `verification/results/tlc_ubcc_tc157_partial_ack_redrive__ubcc_tc157_partial_ack_redrive_liveness.log`

---

## 4. TC159 stable tuple / exact replay：safety + bounded liveness + strengthened PASS

### 4.1 Replay/recovery 控制流模型范围

1 个抽象 PA、1 个 requester、1 个 home。该模型将完整 tuple 比较结果抽象为
`EXACT/MISMATCH` 标签，重点检查 same-tuple force-wire replay、Notify-drop recovery、
accepted 单调、commit/complete 至多一次和 drain 控制流。它不单独证明逐字段比较；逐字段
模型见 4.4。Home 阶段：`IDLE -> WAITING_ALL_ACKS ->
WAITING_LOCAL_DONE -> COMMITTED`；requester 阶段：`IDLE -> WAIT_RESP ->
WAIT_ACK -> ACK_READY -> DONE`。`WAITING_LOCAL_DONE` 编码为
`accepted=true` 且 `targetMask=0` 的 UpgradeResp。

### 4.2 三种配置

- **safety（#3）**：`StrictNotifyTuple=FALSE, StrictDoneTuple=FALSE,
  RequireNotifyDrop=FALSE` —— 当前实现语义（Notify 按 PA 匹配、Done 按 node 匹配），
  11 个不变量 PASS。167 generated / **99 distinct** / depth **19**。
- **bounded Notify-drop liveness（#4）**：前提 **`RequireNotifyDrop=true`** 与
  **`ReserveFinalReplay=true`**（要求最后一个重发必须等到 home 进入
  `WAITING_LOCAL_DONE` 才动用），`FairSpec` 下 `EventuallyDrained` PASS。
  64 generated / **41 distinct** / depth **16**。
- **标签级 strengthened guard safety（#5）**：`StrictNotifyTuple=TRUE, StrictDoneTuple=TRUE,
  EnableMismatchTraffic=TRUE, RequireNotifyDrop=TRUE, ReserveFinalReplay=TRUE`，
  在外部已正确分类 `EXACT/MISMATCH` 的前提下新增两个 guard 不变量，
  共 13 个不变量全部 PASS。1785 generated / **521 distinct** / depth **24**。

### 4.3 性质清单

| 性质 | 含义 |
|------|------|
| `TypeOK` | 类型安全 |
| `StableRequesterTuple` | 整个事务中 `(reqId, baseEpoch, reservedEpoch)` 不变 |
| `ForceWireUsesStableTuple` | force-wire 重发必须使用 EXACT 稳定元组 |
| `HomeAcceptedMonotonicShape` | `WAITING_LOCAL_DONE` 蕴含 `homeAccepted` |
| `ReadyReplayOnlyAfterHomeAccepted` | `READY` 响应只出现在 accepted 之后 |
| `NoCommitBeforeHomeReady` | 未 ready 不 commit |
| `CommitAtMostOnce` | commit 至多一次 |
| `RequesterCompletesAtMostOnce` | requester 完成至多一次（`snpRespCount`/`doneSendCount`） |
| `CompletionRequiresAck` | DONE 必须已收 Ack |
| `CommittedDrainsOutstanding` | COMMITTED 后无 outstanding |
| `DoneDisablesWatchdog` | DONE 后 watchdog 失能 |
| `MismatchNotifyNoCompletion`（strengthened） | mismatched Notify 不得完成 requester |
| `MismatchDoneNoCommit`（strengthened） | mismatched Done 不得触发 commit |
| `EventuallyDrained`（PROPERTY） | 公平下最终 `SuccessDrained` |

### 4.4 原始日志

- `verification/results/tlc_ubcc_tc159_upgrade_replay__ubcc_tc159_upgrade_replay.log`（safety）
- `verification/results/tlc_ubcc_tc159_upgrade_replay__ubcc_tc159_upgrade_replay_liveness.log`（bounded liveness）
- `verification/results/tlc_ubcc_tc159_upgrade_replay__ubcc_tc159_upgrade_replay_strengthened.log`（strengthened）

### 4.5 逐字段 tuple guard 与 early Done

新增 `ubcc_tc159_tuple_guards.tla`，机械检查：

- UpgradeAckNotify/UpgradeDone：`(PA,node,reservedEpoch,reqId)`。
- early Done：`WAITING_ALL_ACKS` 时缓存，最后 Ack 后自动 commit。

Home request record 仍保存 `(PA,node,socket,baseEpoch,reqId)`，但该模型不机械检查 UpgradeReq
逐字段 replay guard；该部分依据 `processOuterUpgradeReq` 的 C++ fidelity mapping，并仍需后续
独立 request-guard model。`StrictGuards=TRUE` 在每个 control 字段二值域中检查 896 distinct
states、depth 10，全部 5 个
不变量 PASS。当前弱匹配语义分别复现：mismatched Notify 完成 requester、mismatched early
Done 被缓存、最后 Ack 后该缓存触发 commit。对应日志和精确 hash见 manifest。

---

## 5. Retry exhaustion：recover PASS 与 permanent PASS

### 5.1 模型范围

单逻辑事务的 fail-safe retry 终端契约抽象。阶段：`IDLE ->
MESSAGE_IN_FLIGHT -> WAITING_RETRY -> COMPLETED | EXHAUSTED`。
`RECOVER` 模式先 drop `MaxDrops` 次响应再交付一次；`PERMANENT` 模式永远 drop。
每次重发保持原 tuple（`stableReqId`/`stableEpoch` 不变）。EXHAUSTED 为显式终端：
fence PA、不提交 intended 状态、只发一次 terminal 结果、禁用后续 retry。

### 5.2 性质清单（8 INVARIANT + 2 PROPERTY）

| 性质 | 含义 |
|------|------|
| `TypeOK` | 类型安全 |
| `StableTuple` | tuple 不变、`sendTupleChanged` 恒假 |
| `AttemptBound` | `attempts <= RetryBudget` |
| `CompletionExactlyOnce` | 完成至多一次 |
| `TerminalExactlyOnce` | terminal 结果至多一次 |
| `TerminalExclusive` | 完成与耗尽互斥 |
| `ExhaustedIsSafe` | EXHAUSTED 时 fenced、无 in-flight、无提交、`attempts=RetryBudget` |
| `CompletedIsCommitted` | COMPLETED 时确实提交、无 retry |
| `EventuallyTerminates` | 公平下必到 `COMPLETED` 或 `EXHAUSTED` |
| `RecoveryOutcome` / `PermanentLossOutcome` | RECOVER 必 COMPLETED / PERMANENT 必 EXHAUSTED |

### 5.3 结果

- **recover**（#6）：8 generated / **7 distinct** / depth **7**，PASS。
- **permanent**（#7）：11 generated / **8 distinct** / depth **8**，PASS。
- 原始日志：
  - `verification/results/tlc_ubcc_retry_exhaustion__ubcc_retry_exhaustion_recover.log`
  - `verification/results/tlc_ubcc_retry_exhaustion__ubcc_retry_exhaustion_permanent.log`

---

## 6. 当前实现的三个 expected counterexample（阴性证据）

以下运行对应**当前 C++ 代码语义**（非 strengthened 契约），TLC 按
预期给出反例。这些是当前实现缺口的形式化刻画，不是故障。

### 6.1 `MismatchDoneNoCommit`（#8，cfg `current_gaps`）

- 反例路径：`StartUpgrade -> HomeAcceptFresh -> HomeCompleteAcks ->
  InjectMismatchDone -> HomeReceiveDone`。注入的 `doneWire="MISMATCH"`
  在 `StrictDoneTuple=FALSE`（当前匹配语义）下被 Home 接受，`commitCount=1`
  且 `mismatchDoneCommitted=TRUE`。
- 含义：当前 `processOuterUpgradeDone` 只校验 `requesterNode`，不校验
  `reqId`/`epoch` tuple；一个同 node 但元组不匹配的 Done 也能触发 commit。
- 日志：`verification/results/tlc_ubcc_tc159_upgrade_replay__current_gaps_expected_violation.log`
  （83 generated / 58 distinct / depth 8）。

### 6.2 `MismatchNotifyNoCompletion`（#9，cfg `current_notify_gap`）

- 反例路径：`... -> HomeCompleteAcks -> DropNotify -> InjectMismatchNotify ->
  ReceiveNotify`。`notifyWire="MISMATCH"` 在 `StrictNotifyTuple=FALSE` 下被
  requester 接受，`ackReceived=TRUE`、`mismatchNotifyCompleted=TRUE`，requester
  被错误完成。
- 含义：当前 `EPBackend::notifyUpgradeAckReady(linePa)` 只按 PA 匹配
  `UpgradeAckNotify`，不校验 `reqId`/`epoch`；陈旧/错配 Notify 可欺骗 requester。
- 日志：`verification/results/tlc_ubcc_tc159_upgrade_replay__current_notify_gap_expected_violation.log`
  （170 generated / 103 distinct / depth 9）。

### 6.3 budget exhausted 后无限 repoll（#10，cfg `current_budget_gap`）

- 反例路径（lasso）：`WatchdogTick <-> CurrentRepollAfterBudget` 死循环。
  `resendCount >= MaxResends` 后 `CurrentRepollAfterBudget` 只置
  `exhausted=TRUE` 并把 `watchdogAge` 归零，随后 `WatchdogTick` 再次把
  `watchdogAge` 拨到 1，回到 `CurrentRepollAfterBudget`——**不产生任何终端结果、
  不发出 Done**，`EventuallyDrained` 违反。
- 含义：当前 `EPRNFController::processUpgradeRetries` 在 drop-resend 次数耗尽后
  “只 re-poll 不重发”（`forceResend=false`），不存在 `EXHAUSTED` 终端态。
- 日志：`verification/results/tlc_ubcc_tc159_upgrade_replay__current_budget_gap_expected_violation.log`
  （95 generated / 57 distinct / depth 17）。

### 6.4 early Done 逐字段反例

逐字段模型证明当前只按 requester node 接收 Done 会允许错误 PA、reserved epoch 或 reqId 的
early Done 在 `WAITING_ALL_ACKS` 被缓存，并在最后 Ack 到达后提交。`MismatchDoneNoCache` 和
`MismatchDoneNoCommit` 两个反例分别归档于
`tlc_ubcc_tc159_tuple_guards__current_early_done_expected_violation.log` 和
`tlc_ubcc_tc159_tuple_guards__current_early_commit_expected_violation.log`。

---

## 7. C++ symbol fidelity mapping

映射基于本会话已确认的源码位置（`modules/ubiomodule` 与
`gem5/src/mem/ruby/protocol/chi/ep`）。`UBCCController` 为 home 侧目录控制器，
`EPBackend`/`EPRNFController` 为 requester 侧 RNF 控制器，`UBAdapter` 为消息适配。

### 7.1 TC157 partial Ack redrive 映射

| TLA 符号/动作 | C++ 符号 | 位置 |
|---------------|----------|------|
| `stage = "WAITING_ALL_ACKS"` | `OpStage::WAITING_ALL_ACKS` | `UBCCController.hh:176` |
| `stage = "WAITING_LOCAL_DONE"`（all-acks barrier 之后） | `upgrade_invalidate_fix` 后 `OpStage::WAITING_LOCAL_DONE`（UPGRADE_PENDING 全 Ack 后） | `UBCCController.cc:2652` |
| `targetMask`, `ackMask`, `PendingMask = targetMask \ ackMask` | `upgradeTargetMask`/`upgradeAckMask`（UPGRADE_PENDING）或 `totalMask`/`ackMask`（INVALIDATE）；`pendingMask = totalMask & ~ackMask` | `UBCCController.hh:229-243`；`UBCCController.cc:4165-4169` |
| `DeliverInvalidate(t)` | `fanoutInvalidateTargets(...)` 发出的 `InvalidateReq`；target 本地失效后回 Ack | `UBCCController.cc:4738` |
| `DropAck(t)`（单次 drop） | UBRouter/UBFault 注入 drop 钩子（fault 测试侧），模型抽象为每 target 一次 drop | fault 注入框架 |
| `ReceiveAck(t)`：单调记账 + duplicate 忽略 | `processInvalidationAck`：`effAckMask |= nodeBit`；重复 Ack 由 `effAckMask & nodeBit` 忽略 | `UBCCController.cc:2506,2579-2591` |
| `RetryPending`（只重发 PendingMask，保持 reqId） | `cleanupExpiredInvalidations()`：超时后 `fanoutInvalidateTargets(linePa, pendingMask, entry.epoch, ost->reqId, ...)` | `UBCCController.cc:4146-4200` |
| `MaxRetries`（模型取 2） | `kMaxInvalidateRetries = 8`（真实预算） | `UBCCController.cc:4148` |
| `ExhaustRetries -> FATAL` | 预算耗尽路径 `fatal("UBCC node_id={}: invalidation timed out after retries ...")` | `UBCCController.cc:4172-4175` |
| `CompleteUpgrade`（commit 至多一次） | `commitIntendedResult` + `removeOutstanding`（UpgradeDone 提交路径）；INVALIDATE 路径转 GRANT_HANDSHAKE 后 `retireToTombstone` | `UBCCController.cc:3927,4006` |
| `EventuallyCommitted`（liveness） | 全 Ack 后 `invalidateBarrierDone=true; accepted=true; stage=WAITING_LOCAL_DONE` | `UBCCController.cc:2650-2653` |

### 7.2 TC159 Upgrade exact replay 映射

| TLA 符号/动作 | C++ 符号 | 位置 |
|---------------|----------|------|
| `stableReqId`, `stableBaseEpoch`, `reservedEpoch` | home 侧 `OutstandingRequest.{reqId, baseEpoch, reservedEpoch}`；requester 侧 `PendingUpgradeTxn.{epoch, reqId}` 与 `UpgradePending.{epoch, reqId}` | `UBCCController.hh:186-267`；`EPBackend.hh:911-916`；`EPRNFController.hh:489-519` |
| `StartUpgrade`（发布稳定 tuple） | `EPBackend::sendUpgradeReq(...)` 前预注册 `_pendingUpgradeTxns[line_pa]`，避免 re-enter 造成 fresh-reqId churn | `EPBackend.cc:1980-1997` |
| `HomeAcceptFresh` | `UBCCController::processOuterUpgradeReq`：创建 UPGRADE_PENDING，冻结 `upgradeTargetMask`，分配 reservedEpoch | `UBCCController.cc:3268,3348-3371` |
| `HomeCompleteAcks`（全 Ack -> accepted -> Notify） | 全 Ack 后 `accepted=true; stage=WAITING_LOCAL_DONE` 并发送 `CoherenceMessageType::UpgradeAckNotify`（带 `ost->reqId`） | `UBCCController.cc:2648-2685`；`protocol/CoherenceMessage.hh:64` |
| `DropNotify`（Notify 单次 drop） | UBRouter 注入 drop；requester 侧由 drop-watchdog 兜底 | fault 注入框架 |
| `ReceiveNotify`（当前按 PA 匹配） | `UBAdapter` UpgradeAckNotify case -> `EPBackend::notifyUpgradeAckReady(linePa)` -> `_epRnfCtrl->receiveUpgradeAck(callbackPa)`；只按 `homeLinePa` 匹配 | `UBAdapter.cc:1261-1271`；`EPBackend.cc:2292-2323` |
| `WatchdogTick` / `ForceWireReplay`（同 reqId force-resend） | `EPRNFController::processUpgradeRetries` drop-watchdog 分支：`dropWatchdogArmed`/`dropResendCount`/`retryReadyTick`；`EPBackend::sendUpgradeReq(..., forceResend)` 复用同一 reqId，并先 `clearReadyResponsesForLine` | `EPRNFController.cc:1593-1668`；`EPBackend.cc:2001-2022` |
| `MaxResends`（模型取 2） | `s_upgrade_drop_max_resends = 8` | `EPRNFController.cc:31` |
| `HomeReplayExact`（exact tuple 幂等 replay） | `processOuterUpgradeReq` 中 exact tuple 命中 `existing` 直接返回 cached grant；`WAITING_LOCAL_DONE` 时 `getUpgradePendingTargetMask()` 返回 0（targetMask=0 完成 requester） | `UBCCController.cc:3320-3337,2781-2795` |
| `respWire = "READY"`（replay 报告 targetMask=0） | `upgradeResp.upgradeTargetMask` 由 `getUpgradePendingTargetMask` 填充；requester 收 `targetMask=0` 视为 ready | `ubio_main.cc:1797-1798`；`EPBackend.cc:2088-2106` |
| `RequesterComplete` / `doneWire` | `EPBackend::sendUpgradeDone` -> `UBAdapter::sendUpgradeDoneReq` -> home `processOuterUpgradeDone` | `EPBackend.cc:2160-2208`；`UBCCController.cc:3447` |
| `HomeReceiveDone`（commit 一次 + drain） | `processOuterUpgradeDone` 的 `WAITING_LOCAL_DONE` 路径：`commitIntendedResult` + `_directory.update` + `stage=DONE` + `removeOutstanding` + `replayPendingRequesters`/`replayResidentWaiters` | `UBCCController.cc:3520-3547` |
| `StrictNotifyTuple=FALSE`（当前：Notify 按 PA 匹配） | `notifyUpgradeAckReady(linePa)` 只传 PA，不校验 reqId/epoch | `EPBackend.cc:2292-2317` |
| `StrictDoneTuple=FALSE`（当前：Done 按 node 匹配） | `processOuterUpgradeDone` 只校验 `ost->requesterNode`，不校验 epoch/reqId tuple | `UBCCController.cc:3476-3480` |
| `InjectMismatchReq` / `HomeRejectMismatchReq` | 非匹配 requester 的 upgrade：`existing outstanding` 冲突 -> reject；requester `NotSharer` 走 `ReadUnique` 重取 | `UBCCController.cc:3339-3346,3304-3317` |
| `RequesterIgnoreRejectAfterAccepted` | `[EP-UPGRADE-STALE-REJECT]`：accepted-pending 后忽略同 reqId 的陈旧 reject | `EPBackend.cc:2046-2056` |
| `EventuallyDrained`（liveness） | UPGRADE_PENDING DONE + `removeOutstanding`；requester `ackReceived` 后 `sendUpgradeDone` 完成 | 上述路径联合 |
| `ForceWireUsesStableTuple` | forceResend 复用同一 reqId（“keep the home's dedup idempotent”） | `EPBackend.cc:2001-2009` |

### 7.3 Retry exhaustion 映射（提出契约 vs 当前实现）

| TLA 符号 | C++ 对应 | 位置 |
|----------|----------|------|
| `RetryBudget`（模型取 3） | 三个真实预算：`s_upgrade_drop_max_resends=8`、`kMaxInvalidateRetries=8`、`kMaxRecallRetries=3` | `EPRNFController.cc:31`；`UBCCController.cc:4148,4108` |
| `RetrySameTuple`（保持 reqId/epoch） | drop-recovery resend 复用同一 reqId/epoch | `EPBackend.cc:2001-2022` |
| `EXHAUSTED` 终端态（fenced, ~retryEnabled, terminal 一次） | **当前不存在**：invalidate/recall 耗尽走 `fatal()`；upgrade drop 耗尽后仅 re-poll（`forceResend=false`） | `UBCCController.cc:4172-4175,4109-4112`；`EPRNFController.cc:1636-1651` |
| `TerminalExactlyOnce` / `TerminalExclusive` | 提出的终端结果一次性与互斥；当前无对应实体 | 契约目标 |
| `ExhaustedIsSafe`（~committed, ~intendedCommitted） | 当前耗尽路径不提交 intended 目录状态（fatal 前未 commit），方向一致但未建模为显式契约 | — |
| `CompletionExactlyOnce` | `commitIntendedResult` + `removeOutstanding` 保证 commit 一次；requester `_lastUpgradeDone`/`_lastUpgradeDoneAck` 单次发送 | `UBCCController.cc:3523-3532`；`EPBackend.cc:2186-2205` |

---

## 8. Scope、Assumptions、Gaps 与下一步

### 8.1 Scope（本次覆盖）

- 三个**聚焦模型**的 bounded state-space TLC 结果：TC157 partial-Ack redrive、
  TC159 stable-tuple exact replay、retry-exhaustion 终端契约可行性。
- focused PASS 与 current-semantics expected counterexample；精确运行清单以 manifest 为准。
- C++ symbol fidelity mapping（第 7 节）与版本/hash 记录（第 1 节）。

### 8.2 Assumptions（明确前提）

1. **只覆盖抽象状态机语义**，不是完整生产代码证明。模型刻意省略 CHI 载荷、
   Bloom/H64 容量、socket 路由细节、真实时延/定时器、ARM memory order。
2. TC157：`Targets={1,2}`, `MaxRetries=2`；每个 target 首 Ack 至多 drop 一次，
   duplicate Ack 至多一次；网络到达顺序任意（抽象为不确定性）。
3. TC159 liveness PASS 依赖两个明确前提：**`RequireNotifyDrop=true`**（开启
   Notify-drop 以激活 watchdog 恢复路径）与 **`ReserveFinalReplay=true`**
   （预算需为 WAITING_LOCAL_DONE 阶段保留一次最终 replay）。关闭后者即出现
   第 6.3 节预算耗尽死循环；这正对应当前 C++ `s_upgrade_drop_max_resends`
   耗尽后只 re-poll 的行为。
4. TC159 replay 模型的 `EXACT/MISMATCH` 是标签级抽象；逐字段 guard 可行性由独立
   `ubcc_tc159_tuple_guards.tla` 支撑，并包括 early Done。当前 C++ 未实现该严格校验。
5. Retry-exhaustion 模型是**提出的生产契约**的可行性验证；当前 C++ 仍
   `fatal()` 或无限 re-poll（模型头注释原话），因此不能把 PASS 外推为现状。
6. 模型与 cfg 文件位于未提交工作区；主仓 HEAD `a8dcd41` 仅为基础提交。

### 8.3 Gaps（已知未覆盖）

- 组合/重复故障（Q2/Q3）：repeated loss（连 drop 2~3 次、ordinals 1,3）、
  UpgradeResp+UpgradeAckNotify 双 drop 等尚未建模。
- 单次 UpgradeReq Drop 和单次 UpgradeResp Drop 也未进入本轮 TC159 模型；当前 liveness
  PASS 严格限定为 UpgradeAckNotify Drop。
- 并发/burst（Q4）、3N2S/8N2S 拓扑（Q5）尚未覆盖。
- tuple 校验、terminal 契约尚未在 C++ 中落地；落地前 strengthened
  结果只是可行性证据。
- 未做 `TLC_WORKERS=1/2` 复跑交叉验证（当前仅 4 workers 证据）。
- 未覆盖 ARM memory-order litmus（第 3.4 节计划项）。

### 8.4 下一步

1. 按 `formal_reliability_followup_plan_20260807_zh.md` 第 6 节顺序补齐 Q2/Q3
   composed/repeated fault 模型与 3N2S 代表性 Q5。
2. 在 C++ 落地 strengthened 契约：Notify 与 Done 增加 `reqId`/`epoch` tuple
   校验（消除 6.1/6.2 反例），并把 retry-exhaustion 收敛到显式 `EXHAUSTED`
   终端态（消除 6.3 死循环）。
3. C++ 变更后重跑 manifest 中全部适用运行，作为回归对照。
4. 为每个 PASS 补充 `QUALIFIED/FAILED/EXPECTED_RETRY_EXHAUSTION/NOT IN SCOPE`
   状态标注（见计划第 7 节完成定义）。
5. 保持 `states/`、`*TTrace_*` 等临时工件不入库；durable log 与 manifest 入库。

本轮运行的 model/cfg/log hash、workers、timeout、rc、状态数和结论已写入
`verification/results/formal_run_manifest_20260807.tsv`。

---

## 9. 原始日志索引

所有日志位于 `verification/results/`，文件名即
`tlc_<model>__<cfg>.log`，命令为
`TLC_WORKERS=4 bash verification/tla/run_tlc.sh <model.tla> <cfg> <timeout>`：

| 日志文件 | 结果 |
|----------|------|
| `tlc_ubcc_tc157_partial_ack_redrive__ubcc_tc157_partial_ack_redrive.log` | PASS（171/depth17） |
| `tlc_ubcc_tc157_partial_ack_redrive__ubcc_tc157_partial_ack_redrive_liveness.log` | PASS（171/depth17） |
| `tlc_ubcc_tc159_upgrade_replay__ubcc_tc159_upgrade_replay.log` | PASS（99/depth19） |
| `tlc_ubcc_tc159_upgrade_replay__ubcc_tc159_upgrade_replay_liveness.log` | PASS（41/depth16） |
| `tlc_ubcc_tc159_upgrade_replay__ubcc_tc159_upgrade_replay_strengthened.log` | PASS（521/depth24） |
| `tlc_ubcc_retry_exhaustion__ubcc_retry_exhaustion_recover.log` | PASS（7/depth7） |
| `tlc_ubcc_retry_exhaustion__ubcc_retry_exhaustion_permanent.log` | PASS（8/depth8） |
| `tlc_ubcc_tc159_upgrade_replay__current_gaps_expected_violation.log` | VIOLATED-expected（MismatchDoneNoCommit） |
| `tlc_ubcc_tc159_upgrade_replay__current_notify_gap_expected_violation.log` | VIOLATED-expected（MismatchNotifyNoCompletion） |
| `tlc_ubcc_tc159_upgrade_replay__current_budget_gap_expected_violation.log` | VIOLATED-expected（EventuallyDrained） |
| `tlc_ubcc_tc159_tuple_guards__ubcc_tc159_tuple_guards_strengthened.log` | PASS（896/depth10） |
| `tlc_ubcc_tc159_tuple_guards__current_expected_violation.log` | VIOLATED-expected（MismatchNotifyNoCompletion） |
| `tlc_ubcc_tc159_tuple_guards__current_early_done_expected_violation.log` | VIOLATED-expected（MismatchDoneNoCache） |
| `tlc_ubcc_tc159_tuple_guards__current_early_commit_expected_violation.log` | VIOLATED-expected（MismatchDoneNoCommit） |

---

## 10. 一句话结论

在 `TLC_WORKERS=4` 的受限计算环境下，TC157 partial-Ack redrive、TC159 标签级
Notify-drop replay、逐字段 Notify/Done guard（含 early Done）与 retry-exhaustion 合同
自洽性在各自有限抽象空间内机器检查 PASS；expected counterexample 则刻画了当前 C++ 的
mismatched Done/Notify 和预算耗尽 re-poll 缺口。UpgradeReq/UpgradeResp Drop、并发、真实
queue 和生产 terminal state 仍未证明。以上均为聚焦模型
证据，**不构成完整生产证明**。

---

## 11. 2026-08-10 ArmO3CPU addendum

O3 不改变本报告 UBCC Home/目录层模型的抽象 transition relation，但新增
CPU/Ruby/EP refinement obligations。`ep_o3_completion_backpressure.tla` 已对两条 line、
两 beats、Data/`Comp_UC` 任意顺序、显式 no-data、`CompAck` 注入后 callback 和
temporary rsp/dat backpressure 做完整有限状态检查：safety/liveness 均 PASS，17,505
generated / 4,564 distinct / depth 23。ArmO3CPU + Sequencer outstanding=16 的原有
回归 146/146 PASS，TC300-303 4/4 PASS。

完整命令、TLC/JDK 版本、日志 SHA256、fairness 前提和 ARM ISA 未证明边界见
`verification/formal_reliability_o3_addendum_20260810_zh.md`。

## 12. 2026-08-10 EP single 状态空间闭合 addendum

`ep_intra_node_single.tla` 修复前存在无限 retry queue、错误 `TailSeq`、矛盾
`txnCount` assignment、不可达 RNF completion 和零值 grant sentinel 等模型缺陷。28-worker
运行在 720,387,925 distinct states 后耗尽磁盘。按阶段修复后，状态空间由 transition
semantics 天然有限。最终语义复核又关闭 dirty-to-shared stale backing、普通 load oracle
缺口和 pending-eviction dead end，并加入 `TerminalStateConsistent`。最终最大
8-CPU/3-transaction safety 为 203,174 distinct / depth 22 PASS；normal liveness 542
distinct PASS；3-CPU/3-transaction max liveness 10,184 distinct PASS；单次 request drop +
单次 `Comp_UC` drop 的 safety/liveness 1,436 distinct PASS。完整变更、coverage、hash 和
bounded proof claim 见
`verification/ep_intra_node_single_closure_20260810_zh.md`。
