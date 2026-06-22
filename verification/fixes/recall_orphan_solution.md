# Recall Orphan Cleanup 方案（冻结版）

本方案针对 UBCC `RECALL` orphan：若 `RECALL` 长时间停留在 `WAITING_TARGET_RESP` 或 `DONE`，则直接删除 outstanding，不回滚、不提交任何保留结果；`reservedEpoch` 与 `dataBuf` 一并丢弃，目录继续保持当前已提交的 `G_E/G_M` 等稳定态。实现采用“双层清理”：**lazy cleanup** 在新请求到达该 PA 时先尝试过期回收，**timer cleanup** 在 `wakeup()` 周期扫描时兜底释放长期 orphan，并在定时路径上立刻 `replayPendingRequesters(linePa)` 解除排队阻塞。为满足最小 TLOC，**不新增 stage，不复用 CANCELLED/TIMED_OUT，不修改 committed DirEntry**。

## 1. 冻结决策表

| 项 | 冻结结论 | 含义 |
|---|---|---|
| 1A | orphan cleanup 纯丢弃 recall result | 不改已提交 `G_E/G_M` |
| 2C | 双层方案 | 新请求触发 lazy + 定时器长期兜底 |
| 3A | cleanup 时丢弃 `reservedEpoch` + `dataBuf` | `reservedEpoch` 仅为保留号 |
| 4A | timeout 内保留 `recall_done_fix.md` 语义 | timeout 后 fresh retry |
| Q1=C | `WAITING_TARGET_RESP` 与 `DONE` 共用同一 `_recallTimeout` | 基于 `createTick` 统一判定 |
| Q2=A | 直接 `removeOutstanding()` | 不引入新 stage |
| Q3=A | cleanup 后立即 `replayPendingRequesters(linePa)` | 仅定时清理路径必须解堵 |
| Q4=A | TLA+ 建模为“stale recall may disappear before retry” | 最小状态空间增量 |
| Priority | 最小 TLOC | 优先少改代码 |

## 2. 完整伪代码

### 2.1 核心判定

```cpp
bool UBCCController::isExpiredRecall(const OutstandingRequest& ost) const
{
    if (ost.opType != OpType::RECALL) {
        return false;
    }
    if (ost.stage != OpStage::WAITING_TARGET_RESP &&
        ost.stage != OpStage::DONE) {
        return false;
    }
    return curTick() > ost.createTick + _recallTimeout;
}
```

### 2.2 Lazy cleanup（新请求到达前置清理）

```cpp
bool UBCCController::cleanupExpiredRecallIfNeeded(uint64_t linePa,
                                                  bool replayWaiters)
{
    OutstandingRequest* ost = findOutstanding(linePa);
    if (!ost || !isExpiredRecall(*ost)) {
        return false;
    }

    DPRINTF(RubyEP,
            "UBCC node_id=%d: expired RECALL cleanup PA=0x%lx stage=%d "
            "age=%lu replayWaiters=%d\n",
            _nodeId, linePa, static_cast<int>(ost->stage),
            curTick() - ost->createTick, replayWaiters ? 1 : 0);

    // 纯丢弃：reservedEpoch/dataBuf 随 outstanding 生命周期结束而消失
    removeOutstanding(linePa);

    if (replayWaiters) {
        replayPendingRequesters(linePa);
    }
    return true;
}
```

插入 `processOuterRequest()` 开头的顺序：

```cpp
// 在 findOutstanding(line_pa) 之前
cleanupExpiredRecallIfNeeded(line_pa, false);

OutstandingRequest* existing = findOutstanding(line_pa);
// 后续保持现有仲裁/排队/创建 RECALL/创建 GRANT_HANDSHAKE 逻辑不变
```

语义：
- 若 orphan 已过期，则当前请求直接基于 **当前 committed DirEntry** 重新仲裁。
- 若随后仍需 recall，则创建一个全新的 `RECALL`，其 `reservedEpoch/baseEpoch/reqId` 重新绑定当前请求。
- 若晚到 `RecallResp` 到达，现有 `processRecallResponse()` 会因 `!ost || ost->opType != RECALL` 自然拒收。

### 2.3 Timer cleanup（周期兜底）

```cpp
void UBCCController::cleanupExpiredRecalls()
{
    std::vector<uint64_t> expired;

    for (const auto& kv : _outstandingReqs) {
        const uint64_t linePa = kv.first;
        const OutstandingRequest& ost = kv.second;
        if (isExpiredRecall(ost)) {
            expired.push_back(linePa);
        }
    }

    for (uint64_t linePa : expired) {
        cleanupExpiredRecallIfNeeded(linePa, true);
    }
}
```

插入 `wakeup()`：

```cpp
void UBCCController::wakeup()
{
    cleanupTombstones();
    cleanupExpiredRecalls();
}
```

### 2.4 双层协同规则

```cpp
Rule 1: lazy cleanup 不主动 replay 全局队列；它只为“当前抵达的新请求”让路。
Rule 2: timer cleanup 必须 replayPendingRequesters(linePa)，避免无新流量时永久阻塞。
Rule 3: 两层都只删除 RECALL outstanding，不触碰 committed DirEntry。
Rule 4: timeout 基于 createTick，而不是 DONE 时刻；RECALL.DONE 不延寿。
Rule 5: cleanup 后若旧 RecallResp 晚到，按 stale/no-outstanding 丢弃。
Rule 6: 不推进 epoch，不安装 tombstone，不产生补偿提交。
```

## 3. 代码变更计划（精确到文件 / 函数 / 现有行号）

### 3.1 必改文件

#### A. `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh`

1. **Outstanding / cleanup 配置区**  
   - 现有位置：`_tombstoneWindowW` 在 **L613-L614**。  
   - 修改：紧邻其后新增：
     - `Tick _recallTimeout = 10 * _tombstoneWindowW;`
   - 目的：复用已有 tombstone 时间尺度，保持最小改动。

2. **私有 helper 声明区**  
   - 现有位置：`cleanupTombstones()` / `replayPendingRequesters()` 在 **L717-L725**。  
   - 修改：新增声明：
     - `bool isExpiredRecall(const OutstandingRequest &ost) const;`
     - `bool cleanupExpiredRecallIfNeeded(uint64_t linePa, bool replayWaiters);`
     - `void cleanupExpiredRecalls();`

#### B. `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc`

1. **`wakeup()`**  
   - 现有位置：**L117-L121**。  
   - 修改：在 `cleanupTombstones();` 之后增加 `cleanupExpiredRecalls();`。

2. **`processOuterRequest(...)`**  
   - 现有关键区域：existing outstanding 检查在 **L427-L525**，`findOutstanding(line_pa)` 在 **L430**。  
   - 修改：在 **L430 前**插入 `cleanupExpiredRecallIfNeeded(line_pa, false);`。  
   - 目的：lazy cleanup 先清走过期 `RECALL`，再沿用原有排队/回放/新建流程。

3. **helper 实现插入点**  
   - 推荐插入在 `cleanupTombstones()` 附近，即 **L2309-L2329** 后。  
   - 新增三个 helper 的实现：
     - `isExpiredRecall(...)`
     - `cleanupExpiredRecallIfNeeded(...)`
     - `cleanupExpiredRecalls()`

4. **`createOutstanding(...)`**  
   - 现有位置：**L2589-L2623**，`createTick` 在 **L2612** 设置。  
   - 修改：**无需改动**；当前已有 `createTick`，满足统一 timeout 判定前提。  
   - 说明：为最小 TLOC，不强行复用 `deadlineTick`。

5. **`removeOutstanding(...)`**  
   - 现有位置：**L2626-L2629**。  
   - 修改：**无需改动**；继续作为唯一删除入口。

6. **`replayPendingRequesters(...)`**  
   - 现有位置：**L2512-L2576**。  
   - 修改：**无需改动**；timer cleanup 直接调用现有逻辑。

7. **`processRecallResponse(...)`**  
   - 现有位置：**L1069-L1167**。  
   - 修改：**可不改**。晚到响应在 **L1097-L1105** 会因无 `RECALL` outstanding 被自然丢弃。若希望更强诊断，可仅补一条 DPRINTF。

### 3.2 TLA+ 文件

#### C. `verification/tla/ubcc_transport_faults.tla`

1. **新增 orphan cleanup 动作**  
   - 位置：建议加在 `DeliverMessage` 后、`Next` 前，即 **L125-L153** 区域。  
   - 新动作形态：
     - `ostOpType = "RECALL"`
     - `ostStage \in {"WAITING_TARGET_RESP", "DONE"}`
     - 非确定性清除 outstanding
     - `committedState/Sharers/Owner/Dirty/Epoch` 全部不变
   - 备注：不新增状态变量，不显式建模计时器，只表达“过期 recall 可能在 retry 前消失”。

2. **`Next` 扩展**  
   - 现有位置：**L145-L152**。  
   - 修改：增加 `\/ RecallOrphanDisappears`。

### 3.3 测试文件（建议新增）

#### D. `tests/e2e/test_e2e.py`

- 用例映射表：文件顶部 **L61-L62** 附近已有 TC40/TC41。  
- verifier 分发表：**L1086** 附近。  
- 建议新增：
  - `TC63 e2e_tc63_recall_orphan_timer_cleanup`
  - `TC64 e2e_tc64_recall_done_orphan_lazy_cleanup`

#### E. 新 workload 文件

- `tests/e2e/workloads/e2e_tc63_recall_orphan_timer_cleanup.c`
- `tests/e2e/workloads/e2e_tc64_recall_done_orphan_lazy_cleanup.c`

## 4. TLOC 估算

| 文件 | 预计新增/修改 TLOC | 说明 |
|---|---:|---|
| `UBCCController.hh` | 4-8 | 1 个字段 + 3 个声明 |
| `UBCCController.cc` | 28-45 | 3 个 helper + 2 个调用点 |
| `ubcc_transport_faults.tla` | 10-18 | 1 个动作 + `Next` 扩展 |
| `test_e2e.py` | 12-24 | 2 个 verifier 注册 |
| 2 个新 workload | 60-110 | 视 fault 注入脚本复用程度 |
| **合计（不含新 TC）** | **42-71** | 核心修复 |
| **合计（含新 TC）** | **114-205** | 完整交付 |

结论：若只做核心协议修复，TLOC 很小，符合“最小 TLOC”优先级。

## 5. TLA+ 影响

### 5.1 需要更新的模型

- **主更新模型**：`verification/tla/ubcc_transport_faults.tla`
- **`ubcc_protocol_core.tla` 可不改**：本冻结决策不引入新阶段/新变量，只需在 fault 模型中允许 `RECALL` outstanding 非确定性消失。

### 5.2 建模方式

推荐新增：

```tla
RecallOrphanDisappears ==
    /\ ostOpType = "RECALL"
    /\ ostStage \in {"WAITING_TARGET_RESP", "DONE"}
    /\ ostOpType' = "NONE"
    /\ ostStage' = "CREATED"
    /\ UNCHANGED <<committedState, committedSharers, committedOwner,
                   committedDirty, committedEpoch, ostBaseEpoch,
                   ostReservedEpoch, ostReqId, ostRequester,
                   ostTargetMask, ostAckMask, ostIntendedState,
                   ostIntendedOwner, ostIntendedSharers, ostRecallDone,
                   ostInvalidateDone, ostAccepted, tombstone, commitLog>>
    /\ tick' = tick + 1
    /\ transportRecord' = [kind |-> "RECALL_ORPHAN_CLEANUP", ...]
```

说明：
- 不表示具体 deadline 数值；
- 只表达“stale recall may disappear before retry”；
- 与冻结决策 Q4=A 一致。

### 5.3 状态空间增量预估

- **状态变量数**：不变
- **新增动作分支**：+1
- **distinct states**：预计 **+0% ~ +5%**
- **transitions**：预计 **+5% ~ +15%**

这是最小增量方案；若把 timeout 计数器显式入模，状态空间会明显放大，不符合当前冻结目标。

## 6. TC 影响

### 6.1 现有 TC 的风险面

| TC | 关联路径 | 风险 | 预期 |
|---|---|---|---|
| TC40 | recall timeout/retry | 行为会从“等待旧 recall”变为“过期后 fresh retry” | 仍应通过，但 marker 可能需要更精确区分 lazy/timer cleanup |
| TC41 | recall + invalidate overlap | 过期 cleanup 可能改变中间排队长度 | 最终收敛不应变 |
| TC15 | credit storm / pending queue | timer cleanup 会更早解堵队列 | 应更稳，不应回归 |
| TC25 | invalidate/clear cycling | 与 tombstone cleanup 相邻 | 应无协议变化，但要防误删非-RECALL outstanding |
| TC43 | rapid owner cycles | 更容易触发 stale late RecallResp | 应维持最终一致 |
| TC47 | drop Clear fault | 理论上无直接影响 | 回归确认 removeOutstanding 不误触发 GRANT_HANDSHAKE |

### 6.2 建议新增 TC

#### TC63: `recall_orphan_timer_cleanup`
- 场景：`RECALL.WAITING_TARGET_RESP` 丢响应，无新请求到达。
- 观察点：超过 `_recallTimeout` 后，timer cleanup 删除 outstanding，并 `replayPendingRequesters()`。
- 断言：
  - 无死锁；
  - 后续请求能重新发起 fresh recall；
  - 最终值收敛；
  - 日志有 `[TC63_ORPHAN] cleanup=timer`。

#### TC64: `recall_done_orphan_lazy_cleanup`
- 场景：`RECALL.DONE` 已形成，但原 requester 不再 retry；另一个 requester 到达同一 PA。
- 观察点：新请求先触发 lazy cleanup，再按当前 committed `G_E/G_M` 发起 fresh arbitration。
- 断言：
  - 旧 `dataBuf/reservedEpoch` 未被复用；
  - 新请求不消费过期 `RECALL.DONE`；
  - 最终读到 committed owner 数据；
  - 日志有 `[TC64_ORPHAN] cleanup=lazy`。

## 7. 分步实施顺序

1. **先改 `UBCCController.hh`**  
   加 `_recallTimeout` 字段与 3 个 helper 声明。

2. **再改 `UBCCController.cc` helper**  
   先实现 `isExpiredRecall()`、`cleanupExpiredRecallIfNeeded()`、`cleanupExpiredRecalls()`。

3. **接入 timer cleanup**  
   修改 `wakeup()`，保证系统即使无新流量也能清走 orphan。

4. **接入 lazy cleanup**  
   在 `processOuterRequest()` 里、`findOutstanding()` 前插入清理调用。

5. **仅做最小诊断增强（可选）**  
   在 `processRecallResponse()` 的“无 RECALL outstanding”分支补 DPRINTF，便于区分晚到包与普通非法包。

6. **更新 TLA+ fault 模型**  
   在 `ubcc_transport_faults.tla` 增加 `RecallOrphanDisappears`，不改 core 模型。

7. **回归现有高风险 TC**  
   先跑：`TC40 / TC41 / TC15 / TC25 / TC43 / TC47`。

8. **补新 TC63/TC64**  
   分别覆盖 timer cleanup 与 lazy cleanup 两条路径。

9. **最后做日志/marker 收口**  
   统一输出 cleanup 原因、阶段、PA、年龄，便于后续定位 orphan 与晚到 RecallResp。
