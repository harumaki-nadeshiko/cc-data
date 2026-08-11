# Retry Exhaustion 可靠性契约可行性分析（2026-08-07）

- 日期：2026-08-07
- 性质：**静态可行性分析（只读）**，不修改生产代码，不运行 E2E
- 证据口径：本文件全部为仓库内静态事实（源码 / 配置 / runner / 测试 / 原始日志 / TLC 原始输出）与
  已提交/未提交模型的有限状态空间结果，证据等级 **E0/E1/E2（form model-scope）**（等级定义见
  `docs/delivery/acceptance_metrics_deliverables_todo_20260807_zh.md:37-49`：E1=静态代码审阅、接口或
  路径分析；E2=单元测试、focused test 或小规模仿真；形式化结论只能在明确小模型范围内视为强证据）。
  所有结论不得被当作生产规模 E3+ 运行证据。
- 前置状态引用：
  - `docs/delivery/acceptance_metrics_deliverables_todo_20260807_zh.md:235`（Q6 retry-exhaustion）、`:249`
    （“正常退出或明确的 `EXPECTED_RETRY_EXHAUSTION`，禁止 silent timeout”）、`:429`（fault qualification
    四态标注）。
  - `verification/formal_reliability_followup_plan_20260807_zh.md:86-100`（§3.3 retry exhaustion 通用模型）
    与 `:159-166`（§4.6 Q6 exhaustion：retry budget 建议 3，结果必须为 `EXPECTED_RETRY_EXHAUSTION`，
    外层 TIMEOUT 一律失败）。
  - `verification/formal_reliability_results_20260807_zh.md:154-186`（§5 retry-exhaustion recover/permanent
    小模型 PASS）与 `:216-226`（§6.3 TC159 budget-gap 无限 repoll 阴性证据）。
  - 新建模型 `verification/tla/ubcc_retry_exhaustion.tla` 与两份 cfg、两份 TLC 原始日志
    `verification/results/tlc_ubcc_retry_exhaustion__ubcc_retry_exhaustion_{recover,permanent}.log`。

---

## 1. 结论摘要

| # | 结论 | 证据等级 |
|---|---|---|
| C1 | 当前实现**已有有界 retry**：Recall（含 dirty-capacity recall 与 recall timeout）预算 3、Invalidate（含 upgrade 的 invalidation timeout）预算 8、EP-RNF upgrade DROP-recovery 预算 8、EP-SNF pending-writeback 上限 128 | E1 |
| C2 | 但**多数 exhaustion 没有统一 terminal contract**：Recall/Invalidate 预算耗尽走 `fatal()`（gem5 PANIC），EP-RNF upgrade drop 预算耗尽后“只 re-poll 不重发”无限续命，EP-SNF writeback 128 次后 warn+静默丢弃条目 | E1 |
| C3 | **尚无 `EXPECTED_RETRY_EXHAUSTION` 代码实体或 E2E runner/verifier 判定**；当前 fault case 没有“预期安全耗尽”专用通道，不能区分预期 terminal failure 与意外 crash/timeout，与 Q6 验收口径（`:235,:249`）不符 | E1 |
| C4 | 拟议的 `EXHAUSTED` terminal contract（fence PA、不提交 intended 状态、terminal 结果至多一次、禁用后续 retry）已在 `ubcc_retry_exhaustion.tla` 中**小模型可满足**：RECOVER 与 PERMANENT 两模式各 8 个不变量 + 2 条时序性质全部 **PASS**（recover 7 distinct/depth 7；permanent 8 distinct/depth 8，`TLC_WORKERS=4`） | E2（model-scope） |
| C5 | 同一模型族的阴性证据 `ubcc_tc159_upgrade_replay_current_budget_gap` 精确刻画了当前实现缺口：预算耗尽后 `CurrentRepollAfterBudget` 与 `WatchdogTick` 构成 lasso，`EventuallyDrained` **VIOLATED（预期）** | E2（model-scope） |
| C6 | 落地需要：统一状态机/字段、结构化 JSON result、per-PA fenced 与 global fail-stop 的范围决策、fault rule schema 扩展、E2E exhaustion 矩阵；每项有明确 C++ 锚点（第 6 节） | E1 |
| C7 | **本工作不改变合同目标 3（OurCC 相对甲方 HA 时延）的状态**：目标 3 保持 `UNPROVEN`（`acceptance_metrics_deliverables_todo_20260807_zh.md:59,:842`；`docs/delivery/ourcc_vs_customer_ha_target3_benchmark_and_delivery_20260804_zh.md:20`） | E0 |

**总判断**：retry-exhaustion 从“fatal / 无限 re-poll / 静默丢弃”收敛到统一 `EXPECTED_RETRY_EXHAUSTION`
terminal contract 是**可行且可落地**的工程项：形式化可行性已经小模型验证，落地主要风险在
    per-PA fenced 的队列/waiter/late-response 语义、结构化 result 与 runner 判定耦合、以及 regression
    范围。这些仍是协议正确性与生命周期的核心风险。**在代码落地并重跑 matrix 之前，本文件所有
    “可行”结论均不得外推为现状已修复**。

---

## 2. 当前实现盘点（静态事实）

### 2.1 已有有界 retry

| 路径 | 预算 | 常量定义 | 耗尽行为 | 原始证据 |
|---|---|---|---|---|
| Home dirty-capacity recall（GlobalInvalidate 命中 dirty owner 且 no-data） | `kMaxRecallRetries = 3` | `modules/ubiomodule/UBCCController.cc:2296` | `fatal(...)`（`:2313-2315`） | `logs/u5_tc143_8n2s_naive_diag_deferred_20260804/cases/tc143_8n2s_naive_p150/ubio_n0_s0/stderr.log:965480` PANIC |
| Home recall timeout（`cleanupExpiredRecallIfNeeded`） | `kMaxRecallRetries = 3` | `UBCCController.cc:4108` | `fatal(...)`（`:4110-4112`） | — |
| Home invalidation timeout（`cleanupExpiredInvalidations`，含 INVALIDATE/NAIVE_EVICT_INVALIDATE/UPGRADE_PENDING） | `kMaxInvalidateRetries = 8` | `UBCCController.cc:4148` | `fatal(...)`（`:4173-4175`） | — |
| EP-RNF upgrade DROP/NO-RESP recovery resend | `s_upgrade_drop_max_resends = 8` | `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:31` | 预算耗尽后 `warn(... re-polling only)`，`forceResend=false` 无限续命（`:1648-1651`） | `logs/fault_loss_156_157_20260803/gem5_tc157_node0/stderr.log`：`re-polling only` 出现 **268 次** |
| EP-SNF pending writeback（QueryLineMeta / 写回前序） | `MAX_RETRIES = 128` | `EPSNFController.cc:682` | warn + `_pendingWritebacks.erase`（静默丢弃条目，`:777-781`） | — |

关键点：

1. retry 路径保持同一 reqId/epoch tuple（`RetrySameTuple` 语义已在
   `EPBackend.cc:2001-2022` force-resend 与 `UBCCController.cc:4165-4169` pendingMask 重发中体现），
   因此“重发不 churn”不是缺口；缺口在**耗尽后的归宿**。
2. `UBCCController` 耗尽全部走 `fatal()`，等价于 gem5 PANIC 杀死仿真；E2E runner 对 child 非零退出
   只能判 FAIL，没有“预期 exhaustion”通道。
3. EP-RNF drop 预算耗尽后的 re-poll 每 watchdog 周期重复一次且**不产生任何 terminal 信号**（见 §4.2
   与原始日志 268 次重复），在没有外层 timeout 的场景会无限运行，有外层 timeout 则退化为
   “外层 TIMEOUT 判失败”——正是 Q6 明文禁止的口径（`formal_reliability_followup_plan_20260807_zh.md:162-163`）。
4. EP-SNF writeback 128 次后直接 erase：若该写回含脏数据，等于**静默丢数据**，是四类行为中
   安全属性最弱的一条；同样无 terminal 结果。
5. `OpStage` 枚举（`UBCCController.hh:173-183`）已有 `TIMED_OUT` 与 `PERSISTENT_BUSY` 名义
   terminal 状态；当前代码会识别/读取它们，但本轮静态审阅未找到明确 assignment site，仍需在
   落地前做全仓 transition inventory，不能直接假定为已实现出口。

### 2.2 原始日志证据摘录

1. **TC157（InvalidateAck 单次 Drop 导致 Home barrier 延迟）**：
   `logs/fault_loss_156_157_20260803/gem5_tc157_node0/stderr.log:515-547`：
   ```
   warn: EP_RNF node_id=0: upgrade DROP-recovery exhausted (8 resends) PA=0x18014900 — re-polling only
   ```
   连续 268 行，同一个 PA/reqId。TC157 注入的是 InvalidateAck Drop；Home 在等待
   invalidation barrier 时没有及时产生 UpgradeAckNotify，requester watchdog 因而耗尽并持续
   re-poll。该日志说明 requester budget 与 Home barrier 完成时序之间缺少确定性 terminal 合同。
2. **TC143 8N2S naive**：
   `logs/u5_tc143_8n2s_naive_diag_deferred_20260804/cases/tc143_8n2s_naive_p150/ubio_n0_s0/stderr.log:965480`：
   ```
   PANIC: UBCC node_id=0: dirty capacity recall exhausted retries PA=0x14012040 owner=0 reqId=1
   ```
   即 Recall=3 耗尽直接 PANIC。
3. **TC159 budget-gap 阴性 TLC 证据**（§4.2）。

---

## 3. 问题定义与验收缺口

1. **无统一 terminal contract**：四类 exhaustion 行为（fatal×3 路径、re-poll、silent erase）互不相同，
   无单一“耗尽即安全失败”的协议状态。
2. **无 `EXPECTED_RETRY_EXHAUSTION` 实体**：仓库中该字符串只出现在文档/计划
   （`formal_reliability_followup_plan_20260807_zh.md:163,:229` 与
   `acceptance_metrics_deliverables_todo_20260807_zh.md:429`），无代码枚举、无 runner 判定、无 verifier
   通道（`tests/e2e/test_e2e.py` 按 TC 硬编码判据，`scripts/run_fault_tests.sh` 只编排 TC 列表）。
3. **Q6 无法闭环**：Q6 要求“持续 Drop 必须确定性达到 retry budget 并安全失败，不得以外层 timeout
   代替”（`acceptance_metrics_deliverables_todo_20260807_zh.md:235`）。当前：
   - 单次/双次 drop 有恢复能力（TC156-159 已有），但**持续 drop 到耗尽**后没有确定性失败出口；
   - runner 的 `TIMEOUT_SEC=1200 / EP_SUPERVISOR_PROGRESS_STALL_SEC=600`
     （`scripts/run_fault_tests.sh:45-46`）会把 exhaustion 场景判成 timeout/FAIL，与 Q6 冲突。
4. **E2E verifier 结果缺少 exhaustion 四态**：现有 testcase verifier/runner 没有
   `EXPECTED_RETRY_EXHAUSTION` 专用结果，缺少
   `QUALIFIED / FAILED / EXPECTED_RETRY_EXHAUSTION / NOT IN SCOPE` 四态
   （`acceptance_metrics_deliverables_todo_20260807_zh.md:425-430`）。

---

## 4. 形式化结果（已运行）

### 4.1 拟议 terminal contract 小模型：`ubcc_retry_exhaustion.tla`

模型结构（`verification/tla/ubcc_retry_exhaustion.tla`，186 行）：

- 常量：`RetryBudget`（模型取 3）、`MaxDrops`（模型取 2）、`FaultMode ∈ {RECOVER, PERMANENT}`、
  `ReqId`、`Epoch`。
- 阶段（Stages）：`IDLE → MESSAGE_IN_FLIGHT → WAITING_RETRY → COMPLETED | EXHAUSTED`。
- `RECOVER` 模式：drop `MaxDrops` 次后允许一次交付 → `COMPLETED`；
  `PERMANENT` 模式：永远 drop → 预算耗尽进入 `EXHAUSTED`。
- `EnterExhausted`（`:102-112`）：`EXHAUSTED` 要求 `fenced=TRUE`、`retryEnabled'=FALSE`、
  `terminalCount'=+1`、不提交 intended 状态、不动 attempts 以外的字段。
- `Done` 自锁（`:114-116`）：terminal 后不再演进。

性质（8 不变量 + 2 时序性质）：

| 性质 | 含义 |
|---|---|
| `TypeOK` | 类型安全 |
| `StableTuple` | tuple 不变、`sendTupleChanged` 恒假 |
| `AttemptBound` | `attempts <= RetryBudget` |
| `CompletionExactlyOnce` | 完成至多一次 |
| `TerminalExactlyOnce` | terminal 结果至多一次 |
| `TerminalExclusive` | COMPLETED 与 EXHAUSTED 互斥 |
| `ExhaustedIsSafe` | EXHAUSTED ⇒ fenced ∧ ¬retryEnabled ∧ ¬in-flight ∧ ¬committed ∧ ¬intendedCommitted ∧ terminalCount=1 ∧ attempts=RetryBudget |
| `CompletedIsCommitted` | COMPLETED ⇒ committed ∧ ¬retryEnabled ∧ completionCount=1 ∧ terminalCount=0 |
| `EventuallyTerminates` | 公平下必达 COMPLETED 或 EXHAUSTED |
| `RecoveryOutcome` / `PermanentLossOutcome` | RECOVER 必 COMPLETED / PERMANENT 必 EXHAUSTED |

TLC 原始结果（`TLC_WORKERS=4`，命令
`TLC_WORKERS=4 bash verification/tla/run_tlc.sh <model> <cfg> <timeout>`）：

| cfg | 状态数 | depth | 结论 |
|---|---|---|---|
| `ubcc_retry_exhaustion_recover.cfg` | 8 generated / **7 distinct** | 7 | **PASS**（`No error has been found.`，碰撞概率 3.8E-19） |
| `ubcc_retry_exhaustion_permanent.cfg` | 11 generated / **8 distinct** | 8 | **PASS**（碰撞概率 1.3E-18） |

原始日志：

- `verification/results/tlc_ubcc_retry_exhaustion__ubcc_retry_exhaustion_recover.log`
- `verification/results/tlc_ubcc_retry_exhaustion__ubcc_retry_exhaustion_permanent.log`

**解读**：PASS 证明的是“上述拟议契约在该最小抽象下自洽且满足安全/活性质”，是**契约可行性**
证据；不是当前 C++ 的行为证据（当前行为见 §2 与 §4.2）。

### 4.2 当前实现缺口的阴性证据：TC159 budget-gap lasso

`verification/tla/ubcc_tc159_upgrade_replay.tla:226-242` 的 `CurrentRepollAfterBudget` 精确建模当前
`EPRNFController::processUpgradeRetries` 的耗尽分支（`:1648-1651` 只置 `exhausted` 标记并把
`watchdogAge` 归零，随后 `WatchdogTick` 再次拨回 1）：

```
WatchdogTick ⇄ CurrentRepollAfterBudget  (lasso，无终端结果、不发出 Done)
```

TLC 结果：`EventuallyDrained` **VIOLATED（预期）**，95 generated / 57 distinct / depth 17
（`verification/results/tlc_ubcc_tc159_upgrade_replay__current_budget_gap_expected_violation.log`）。
该阴性证据与 §2.2.1 的 268 次 `re-polling only` 原始日志互为印证。

### 4.3 形式化证据的边界（不可外推）

- 小模型状态空间 ≤ 16 个 distinct 状态，只覆盖单逻辑事务的抽象（无 Bloom/H64 容量、无 socket
  路由、无真实定时器、无并发/burst、无 ARM memory order）。
- `RetryBudget=3 / MaxDrops=2` 是参数点，不代表 C++ 各预算（3/8/8/128）的联合证明。
- liveness 均依赖 `FairSpec`（WF_*），生产调度器是否有相应公平性需另行论证。
- 遵守 `formal_reliability_followup_plan_20260807_zh.md:177-196` 的资源规则：`TLC_WORKERS=4`，
  无一次超过 4 逻辑核。

---

## 5. 拟议 terminal contract 设计

### 5.1 统一状态机（home 侧，映射 `OpStage`）

建议在 `UBCCController` 的 `OpStage`（`UBCCController.hh:173-183`）内收敛，废除三处就地
`fatal()`，统一迁移到显式 terminal：

```
RECALL:      CREATED → WAITING_TARGET_RESP →(budget=3)→ EXHAUSTED_RECALL
INVALIDATE:  WAITING_ALL_ACKS →(budget=8)→ EXHAUSTED_INVALIDATE
UPGRADE:     WAITING_ALL_ACKS → WAITING_LOCAL_DONE →(budget)→ EXHAUSTED_UPGRADE
GRANT:       WAITING_CLEAR →(clear timeout/terminal)→ EXHAUSTED_CLEAR  [可选]
```

- `EXHAUSTED_*` 为 terminal：`fenced=TRUE`、`retryEnabled=FALSE`、不提交
  `intendedState/intendedSharersMask/intendedOwnerNode/intendedDirty`、发送一次结构化
  terminal 结果、随后 `removeOutstanding`（或保留 tombstone 以幂等处理迟到消息）。
- 复用现有 `TIMED_OUT`/`PERSISTENT_BUSY` 枚举语义：建议以 `EXHAUSTED_*` 子类型或
  `retireReason` 字段区分“RECALL/INVALIDATE/UPGRADE/CLEAR”四类耗尽，避免与既有 TIMED_OUT
  路径（如果将来启用）混淆。
- requester 侧（EP-RNF upgrade）同样需要 terminal：`dropResendCount >= s_upgrade_drop_max_resends`
  后不再无限 re-poll，而是向上报告 `EXHAUSTED_UPGRADE` 并释放 held snoop / pending txn
  （决策见 §5.3），同时消除 268 次重复 re-poll。

### 5.2 字段扩展

home 侧 `OutstandingRequest`（`UBCCController.hh:186-267`）新增建议字段：

| 字段 | 类型 | 语义 |
|---|---|---|
| `uint8_t terminalResult` / `enum RetireReason` | 枚举 | `NONE / RECALL / INVALIDATE / UPGRADE / CLEAR`，缺省 NONE |
| `bool fenced` | bool | 耗尽后该 PA 进入 fenced 状态（见 §5.3 决策） |
| `uint8_t terminalCount` | uint8 | terminal 结果至多一次（对 `TerminalExactlyOnce`） |
| `Tick exhaustTick` | Tick | 到达 EXHAUSTED 的 tick，写入 result 用于 audit |
| `uint64_t terminalReqId / terminalEpoch` | 同 reqId/epoch | 冻结的 tuple 快照，保证 result 可审计 |
| `uint64_t pendingMaskAtExhaust` | uint64 | INVALIDATE 耗尽时的 pendingMask（诊断） |

requester 侧 `UpgradePending`（`EPRNFController.hh:489-519`）新增建议字段：

| 字段 | 类型 | 语义 |
|---|---|---|
| `bool exhausted` | bool | drop 预算耗尽，禁止再 re-poll |
| `Tick exhaustTick` | Tick | 同上 |
| `int terminalState` | 枚举 | 目前只有 re-poll / forceResend 两态，追加 `EXHAUSTED` |

`OpStage` 枚举本身保持向后兼容：现有 `TIMED_OUT`/`PERSISTENT_BUSY` 不复用为“耗尽”的正式出口，
避免枚举漂移扩大化；正式出口使用新 `EXHAUSTED_*` 值并在 `TypeOK` 等价断言中同步。

### 5.3 per-PA fenced vs global fail-stop：决策选项

| 选项 | 语义 | 优点 | 代价/风险 | 建议 |
|---|---|---|---|---|
| A. per-PA fenced | 仅耗尽 PA 冻结（新请求返回显式 ERROR/REFUSED，waiter 收到 terminal result 后重试/失败），其余 PA 继续服务 | 故障域最小，符合 TLA `ExhaustedIsSafe` 的 `fenced` 语义；与目录行粒度一致 | 需要 waiter/held-upgrade/queue 对“PA 被 fence”的显式处理，否则可能形成二次 stuck；`replayPendingRequesters`/`replayResidentWaiters` 必须感知 fence | **首选**（对齐 TLA 模型字段 `fenced` 与计划 §3.3 “error result 可由 verifier 确定性识别”） |
| B. global fail-stop | 任意 PA exhaustion 即整节点/整 home 停止（保留 fatal 或升级为可控 fail-stop + terminal result） | 实现最简（复用现有 fatal 位置），不会产生半服务状态 | 单条故障拖垮全节点；与 HA 目标 3 的可用性主张冲突；Q6 的“确定性安全失败”会扩大为全节点失败 | 仅作 PERMANENT/毒化环境下的兜底或显式配置 |
| C. 混合 | exhaustion 默认 per-PA fenced；若同一 home 在窗口内耗尽超过阈值（如 4）则升级 fail-stop | 兼顾可用性与毒性识别 | 阈值引入新决策面，需额外验证 | 二期可选 |

**推荐 A（per-PA fenced）为主路径**，理由：

1. TLA `ExhaustedIsSafe` 已把 `fenced` 建模为契约字段，A 与模型零语义偏差。
2. per-PA 与目录行粒度一致，`OutstandingRequest` 本来就按 PA 管理。
3. 对 E2E fault 矩阵最友好：一个 PA 的持续 drop 只应影响该 PA 的结果，其余 case 的 oracle
   不受污染。

B 仅保留为**毒化/永久故障场景**的显式逃生门，且必须产生与 A 相同 schema 的 terminal result
（`scope="global"` 字段区分），不能无声 PANIC。

### 5.4 结构化 JSON result

建议在每个 exhaustion 出口（当前四处 `fatal` 位置 + EP-RNF re-poll 分支）改为 emit 一条
**结构化 result**（文件或 stderr JSON 行），并新增 runner/verifier 判定通道：

```json
{
  "schema_version": 1,
  "kind": "EXPECTED_RETRY_EXHAUSTION",
  "retire_reason": "RECALL|INVALIDATE|UPGRADE|CLEAR",
  "scope": "per_pa",
  "node": 0,
  "socket": 0,
  "pa": "0x14012040",
  "req_id": 1,
  "base_epoch": 1,
  "reserved_epoch": 2,
  "budget": 3,
  "attempts": 3,
  "pending_mask": "0x0",
  "committed": false,
  "fenced": true,
  "exhaust_tick": 965480,
  "drain_ok": true
}
```

- `drain_ok`：表示该 PA 的 outstanding/held/waiter/queue 已按 §5.3-A 语义排空或显式标记，
  由 verifier 复算（对 `formal_reliability_followup_plan_20260807_zh.md:165-166` 的
  “没有 queue 泄漏、没有 completion 后 retry”）。
- verifier 判定规则：case 若声明 `expect=EXPECTED_RETRY_EXHAUSTION`，则只接受
  `kind=EXPECTED_RETRY_EXHAUSTION` 且 `committed=false` 且 `drain_ok=true` 的 result；
  其余路径（child 非零退出、外层 TIMEOUT、无 result 行）一律 FAIL。
- 与 `verification/results/README.md:13-14` 的“expected-violation”命名区分：那是 TLC 阴性证据
  命名，此处是 E2E 结果的四态之一。

### 5.5 fault schema 扩展

当前 schema（`ubio_main.cc:82-83`）：

```
name:type:src:dst:pa:action[:delayTicks[:matchCount]]
action ∈ {drop, dup, delay, reorder}    （applyUbioFault 在 :316 起按序匹配）
```

Q6 exhaustion 需要“持续 drop 直到耗尽”，现有 `matchCount` 是“命中几次”（`:96`），且 drop 路径
直接返回 0 次处理（丢弃），不足以表达“对同一逻辑消息连续丢 N 次、然后放行/继续丢”。
建议扩展（向后兼容，旧规则按缺省解析）：

```
name:type:src:dst:pa:action:delayTicks:matchCount[:dropBurst[:dropStrategy]]
dropBurst:     连续 drop 的同一规则命中上限（对同一 pa/reqId 计，0=不限制）
dropStrategy:  per_pa | per_rule | until_retry_budget    （缺省 per_rule 兼容现状）
```

- `dropBurst=until_retry_budget` 时，注入器需要感知 retry 计数或至少按 reqId 计数，
  打到 `budget`（home 侧 3/8、EP-RNF 8）即停——这样 E2E 可以“确定性制造耗尽但不过冲”，
  是 Q2/Q3/Q6 的统一机制（`formal_reliability_followup_plan_20260807_zh.md:128-157,159-166`）。
- 新增 action 可选值 `poison_pa`（对某 PA 持续 drop，模拟 per-PA 永久故障），作为 §5.3-B 的
  注入通道。
- `parseFaultRules`（`ubio_main.cc:195-300`）按字段数向后兼容；新增字段必须登记到
  `[UBFAULT-LOAD]` 日志与 fault qualification 的 `rules.json`
  （`acceptance_metrics_deliverables_todo_20260807_zh.md:411`）。

### 5.6 E2E exhaustion 矩阵

| 组 | 场景 | fault 规则 | 预期 |
|---|---|---|---|
| E1 | RecallResp 持续 drop 至耗尽 | `RecallResp` `dropBurst=until_retry_budget`（budget=3） | `EXPECTED_RETRY_EXHAUSTION / RECALL`，无提交 |
| E2 | InvalidateAck 持续 drop（单 target） | `InvalidateAck` 同（budget=8） | `EXPECTED_RETRY_EXHAUSTION / INVALIDATE` |
| E3 | InvalidateAck 持续 drop（多 target，partial ack 后耗尽） | `InvalidateAck` 对 pending 子集 | 同上 + `pending_mask` 非空 |
| E4 | UpgradeResp / UpgradeAckNotify 持续 drop | `UpgradeResp`/`UpgradeAckNotify`（budget=8） | `EXPECTED_RETRY_EXHAUSTION / UPGRADE`，无 repoll lasso |
| E5 | UpgradeAckNotify 先 drop 后恢复（对照组） | `dropBurst=1` | `QUALIFIED`（与 TC158/159 现状对齐） |
| E6 | per-PA fenced 后他 PA 继续服务 | 规则限定单 PA | fenced PA 出 exhaustion，其余 PA oracle 全对 |
| E7 | poison_pa 全局逃生门（若启用 B） | `poison_pa` | `scope="global"` exhaustion result，非无声 PANIC |
| E8 | no-fault regression | 无规则 | Q7 回归 100% PASS（`formal_reliability_followup_plan_20260807_zh.md:167-173`） |

每组 case 声明 `expect`，verifier 按 §5.4 判定；`EXPECTED_RETRY_EXHAUSTION` case 的
`drain_ok`、无 commit、无 completion 后 retry 必须逐项断言。

---

## 6. C++ 落地锚点

| 工作项 | 锚点（现状） | 变更点 |
|---|---|---|
| Home recall exhaustion → EXHAUSTED_RECALL | `UBCCController.cc:2296-2315`（dirty-capacity recall fatal）、`:4108-4112`（recall timeout fatal） | 不再 `fatal()`；置 `stage=EXHAUSTED_RECALL`、`fenced=true`、emit JSON result、按 §5.3-A 处理 waiter |
| Home invalidate exhaustion → EXHAUSTED_INVALIDATE | `UBCCController.cc:4148-4175` | 同上；`pendingMaskAtExhaust` 落盘 |
| Upgrade invalidation 分支 | `UBCCController.cc:4172-4175` 与 UPGRADE_PENDING 共用 `recallRetries` 计数（`:4165-4188`） | 独立计数或共用预算时明确语义；`EXHAUSTED_UPGRADE` 后 home 进入 fenced，replay 感知 |
| EP-RNF drop 耗尽 terminal | `EPRNFController.cc:1636-1651`（re-poll only）、`:31`（预算） | 耗尽后 emit result、释放 held snoop/`_upgradePending`，删除 re-poll 循环；与 home fenced 语义对齐 |
| EP-SNF writeback 静默 erase | `EPSNFController.cc:777-781` | 脏数据场景禁止静默丢弃；至少 emit terminal result 并保持条目为可审计终态 |
| 状态机枚举 | `UBCCController.hh:173-183`（`OpStage`，`TIMED_OUT`/`PERSISTENT_BUSY` 未用） | 增加 `EXHAUSTED_*` 或激活 TIMED_OUT 作为正式出口 |
| 字段 | `UBCCController.hh:186-267`、`EPRNFController.hh:489-519` | §5.2 表 |
| Fault schema | `ubio_main.cc:82-98,195-300`（`UbioFaultRule`/`parseFaultRules`） | §5.5 扩展 |
| Runner/verifier | `scripts/run_fault_tests.sh:10-47`、`tests/e2e/run_multi.sh:104-199`（`fault_rules_for_tc`）、`tests/e2e/test_e2e.py`（按 TC 硬编码） | `expect=EXPECTED_RETRY_EXHAUSTION` 通道 + §5.4 判定 |
| TLA 回归对照 | `ubcc_retry_exhaustion.tla`、`ubcc_tc159_upgrade_replay.tla`、`ubcc_tc159_tuple_guards.tla` | C++ 变更后重跑 formal manifest 中全部适用运行，作为回归对照 |

---

## 7. 风险

1. **fence 语义的队列涟漪**：per-PA fenced 后，`_outstandingReqs` 中同 PA 的其他 op、
   `replayPendingRequesters`、`replayResidentWaiters`、held-upgrade 与 deferred fault queue 必须
   显式感知 fence；遗漏任何一处会从“fatal”退化为“stuck/false-livelock”（比现状更差）。
   需在 TC224 式的 waiter 压力与 Q4 burst 场景验证（`docs/issues/tc224_resident_capacity_deadlock.md`）。
2. **result 与 runner 判定耦合**：JSON result 落在 stderr 需与“机器 artifact 不与人类日志混写”
   的输出治理原则（`acceptance_metrics_deliverables_todo_20260807_zh.md:321-327`）对齐；runner
   必须同时检查 rc 与结构化 result（`:204,:325`），不能回归到“最后一行 sentinel”。
3. **行为回归面**：把三处 `fatal()` 改为 terminal 会改变“任何一次真实协议 bug 都在耗尽时 PANIC”
   的现状安全网——诊断价值下降，必须用 result 的完整现场字段（tuple/tick/pendingMask）补偿。
4. **budget 语义混淆**：`recallRetries` 字段同时被 recall 与 invalidation 复用
   （`UBCCController.cc:4172,4183`），扩展预算时必须拆分或明确同一计数器语义，防止
   “Invalidate 用掉 recall 预算”的错误。
5. **EPSNF 静默 erase 的历史负担**：writeback 条目可能是脏数据，改造为 terminal 前需确认
   QueryLineMeta 失败不会导致数据面错乱；宁可保持 warn+erase 并单独开 P0，也不要在未验证
   前改动（本文件不改变现状）。
6. **模型-代码漂移**：TLA 的 `EXHAUSTED` 与 C++ `EXHAUSTED_*` 必须双向映射（
   `formal_reliability_results_20260807_zh.md:275-284`），否则 PASS 失去回归意义。
7. **per-PA fenced 与 HA 目标 3 的边界**：fence 是可靠性语义，不能与目标 3 的时延理论模型混用；
   HA 理论仍保持 lossless 基线（`docs/research/customer_ha_coherence_research_handoff_20260807_zh.md:174`），
   本工作不改变目标 3 状态（§9）。

---

## 8. 资源范围

- **本文件**：只读分析，不修改生产代码，不运行 E2E；唯一写操作是本文档自身。
- **后续落地（另行立项）**：若批准实施，单次计算仍遵守
  `formal_reliability_followup_plan_20260807_zh.md:177-196` 与
  `acceptance_metrics_deliverables_todo_20260807_zh.md:570`：
  - TLC：`TLC_WORKERS=4` 上限；
  - E2E/fault：`FAULT_CPU_SET` 单次 ≤ 4 logical cores；
  - 并行任务合计 ≤ 16 logical cores；
  - 每次运行归档 command/cpuset/timeout/hash/原始 stdout/stderr/return code/summary。
- **工期粗估**（区间，非承诺）：契约状态机 + 字段 + result schema + runner/verifier 通道
  约 1–2 人周；fault schema 扩展与 E1-E8 矩阵约 1 周；fence 语义的 Q4/TC224 验证约 1–2 周；
  合计约 **3–5 人周**（不含 EPSNF 静默 erase 的独立 P0 决策与目标 3 相关工作）。

---

## 9. 不得声称的内容

1. 不得声称“当前实现已具备 `EXPECTED_RETRY_EXHAUSTION` 或统一 terminal contract”——仓库中
   该字符串只存在于计划/验收文档，代码无对应实体（§2、§3）。
2. 不得把 `ubcc_retry_exhaustion.tla` 的 PASS 声称成“当前代码行为已证明安全”——模型头注释与
   §4.3 明确其为拟议契约的可行性验证，当前 C++ 仍 `fatal()` / re-poll / erase。
3. 不得声称“预算耗尽不再导致 PANIC / 无限 re-poll”——本文件未修改任何代码；第 5、6 节是
   提案，不是现状。
4. 不得把 `EXPECTED_RETRY_EXHAUSTION` 写成一次额外的“容忍机制”或“提升可用性”的能力——它
   是**确定性安全失败契约**，语义是“此处放弃重试并显式报告”，不是恢复成功。
5. **不得声称本工作改变合同目标 3**：目标 3（OurCC 相对甲方 HA 跨节点 CC 同步理论时延
   `< 甲方理论平均时延`）当前为 `UNPROVEN`
   （`acceptance_metrics_deliverables_todo_20260807_zh.md:59,:842,:177-192`；
   `docs/delivery/ourcc_vs_customer_ha_target3_benchmark_and_delivery_20260804_zh.md:20`），
   本可靠性工作不提供任何 HA 时延证据，也不改变其状态。
6. 不得把 `EXPECTED_RETRY_EXHAUSTION` case 的 PASS 计入“successful fault case”的成功率
   （`acceptance_metrics_deliverables_todo_20260807_zh.md:238-249` 的 drain/oracle 门槛只适用于
   successful case；exhaustion case 是独立的四态之一）。
7. 不得声称 4.2 的 TC159 budget-gap 阴性反例之外的任何路径也已被形式化覆盖（Q2/Q3/Q4/Q5
   尚未建模，见 `formal_reliability_results_20260807_zh.md:317-325`）。

---

## 10. 建议决策门

- **Gate 0（范围）**：确认 §5.3 采用 per-PA fenced（A）还是需要 global fail-stop（B）逃生门；
  确认 EPSNF writeback erase（§7.5）是否独立立项。
- **Gate 1（契约落地）**：三处 `fatal()` 收敛为 `EXHAUSTED_*` + JSON result；EP-RNF 删除
  re-poll 循环；E1-E4 矩阵 PASS。
- **Gate 2（fence 语义验证）**：E6 + Q4 burst + TC224 式 waiter 压力证明无二次 stuck。
- **Gate 3（回归与证据）**：C++ 变更后重跑 §4 全部 TLC 运行；E5-E8 完整；fault qualification
  四态标注落地。
- **Gate 4（冻结）**：E5 manifest（`acceptance_metrics_deliverables_todo_20260807_zh.md:848`）。

---

## 11. 对当前项目状态的影响

- 在 Gate 1 完成前，Q6（`acceptance_metrics_deliverables_todo_20260807_zh.md:235`）保持
  `TODO/PARTIAL`，fault qualification 整体保持 `PARTIAL`（`:56`），与 §3 缺口一致。
- 本文件不修改任何代码、配置或运行环境；若批准实施，按第 6 节锚点与第 8 节资源另行立项。
- 形式化部分（`ubcc_retry_exhaustion.tla` + 两份 PASS 日志）已经属于工作区可复现证据，
  但它只支撑“契约可行”，不支撑“现状达标”。
