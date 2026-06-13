# Scheme v4.0：CC-EP 跨节点缓存一致性协议实施方案

**状态**：Phase C 合成版 / 代码实施权威基线  
**适用范围**：3 节点 DSM、gem5 Ruby CHI、EP-RNF / EP-SNF / EPBackend / UBCC 全链路  
**输入来源**：`docs/recovery/entry_document.md`、Phase A 全量决策、状态分析器 H/M 问题闭环  
**目标读者**：code-implementer subagent

---

## 1. Architecture Overview

### 1.1 系统目标

在 gem5 Ruby CHI 上实现 **3 节点 DSM（Distributed Shared Memory）跨节点一致性协议**。每个节点内部仍是独立 CHI 域；跨节点一致性由 **UBCC 全局目录** 管理，经 **EP 边界层** 转换为本地 CHI 事务。

### 1.2 拓扑与边界

```text
Node i

  CPU/L1/L2 (RN-F)
       │
     HN-F_i (L3 / CHI Home)
       │
  ┌────┴───────────────────────────────┐
  │ DL_SNF_i   │   EP_SNF_i            │
  │ local DRAM │   remote DSM proxy    │
  └────────────┴───────────────────────┘
                      │
                  EPBackend_i
                  │       │
               EP-RNF_i   UBCC_i  ───── UBCC Link ───── UBCC_j
```

### 1.3 边界决策（规范性）

| 决策 | 结论 |
|---|---|
| 外部可见性边界 | **EP（EP-RNF + EP-SNF）是外部可见性边界** |
| 全局排序点 | **UBCC 是全局总排序点** |
| CHI 语义承载 | UBCC 传递意图；EP 负责翻译为 CHI |
| HN-F 地位 | HN-F L3 只是加速缓存；真权威是 `UBCC DirEntry + epoch` |
| 跨节点路径 | **只允许 UBCC 链路跨节点**；禁止 HN-F↔HN-F 直连 |
| CHI 侵入程度 | 保持 HN-F 原状态机主体不变，只做 EP-RNF 必要增量 |

### 1.4 PA 布局

```text
NODE_ADDR_SHIFT = 40
PHY_BASE_i      = i << 40
SEG_SIZE        = 128 MB

Per-node offsets from PHY_BASE_i:
  [0*SEG, 1*SEG): LocalPrivate
  [1*SEG, 2*SEG): UbccExclusive
  [2*SEG, 3*SEG): DSM_0
  [3*SEG, 4*SEG): DSM_1
  [4*SEG, 5*SEG): DSM_2
```

| 访问者节点 i | 访问窗口 | 目标 |
|---|---|---|
| `DSM_i` | 本地 home 窗口 | `DL_SNF_i` |
| `DSM_j, j!=i` | 远端 DSM 窗口 | `EP_SNF_i → EPBackend_i → UBCC_j` |

### 1.5 关键总图

```text
          CHI domain (node-local)                  Outer protocol domain

CPU/L2 ──CHI── HN-F ──ReadNoSnp/WriteNoSnp── EP-SNF ──┐
  ▲                 │                                  │
  │                 └──── snoop/CompData ─── EP-RNF ──┤ EPBackend ── UBCC(home)
  │                                                    │
  └──────────────────────── local coherence ───────────┘
```

---

## 2. Design Principles

### 2.1 EP 边界原则

1. **CHI 域内只看 CHI，不看 epoch/reqId。**
2. **Outer 域内只看全局意图，不直接发 CHI。**
3. EP 是唯一翻译层：
   - `NeededPerm + WriteIntent + RecallMode` → CHI request type
   - outer ack / clear / recall response / upgrade completion → 本地 HN-F 完成信号

### 2.2 UBCC 排序原则

1. **所有 remote miss 与 local upgrade 统一采用 reserve-then-commit。**
2. `UBCC::processOuterRequest()` / `processOuterUpgradeReq()` 只负责：
   - 为该 PA 建立 outstanding；
   - 预留 `reservedEpoch`；
   - 记录 intended `(state, owner, sharers, dirty)`；
   - 建立 `RECALL` / `INVALIDATE` / `GRANT_HANDSHAKE` / `UPGRADE_PENDING` barrier。
3. `processOuterRequest()` **不得**直接修改 committed `DirEntry.state/owner/sharers/epoch`。
4. 普通 miss 只允许在 **所有 prerequisite barriers 已 DONE 且 home 接受匹配 `Clear(pa, epoch, reqId)`** 时提交 intended 目录结果。
5. local upgrade 只允许在 **`OuterUpgradeDone` 被 home 接受** 时提交 intended 目录结果。

### 2.3 HN 最小修改原则

HN-F 只允许以下类型的修改：

- 注入 `epRnfMachineVersion` / `epRnfMachineID`
- `shared_hint` 驱动 EP-RNF 注册
- `pickSharerForSnoop()` 替代裸 `smallestElement()`
- DCT fallback
- EP-RNF `CompData_SC` 语义修正
- `UpdateDirState_FromReqResp` 中对 EP-RNF 的 owner 晋升保护
- 对 `requestor==epRnfMachineID && ep_proxy_op!=None` 的 EP-RNF proxy unique-flow，复用 baseline prefix、完成阶段执行本地 `scrub_to_I`

**禁止新增 HN-F 专用“外部协议状态机”。**

### 2.4 序列化原则

| 层级 | 规则 |
|---|---|
| UBCC | 每 PA 全局总排序；并发冲突统一 `BUSY/RETRY` |
| HN-F | strict single-flight TBE；同 PA 后续请求 RETRY |
| EP-RNF | 每 PA 单 inflight CHI 请求；无多 entry CHI 队列 |
| EP-SNF | 允许 deferred CompData 发送，但不重判 epoch |

### 2.5 No-Bypass 原则

以下路径一律禁止：

- EP-RNF 直接 local snoop
- EPBackend 直接跳过 HN-F 发 invalidation ack
- ReadOnce 充当 recall 主路径
- 用 `functionalRead` 代替 write recall invalidate
- sole-EP-RNF 情况下用 `ReadNoSnp` miss path 代替 DCT fallback snoop path

---

## 3. Global Ordering Model

### 3.1 `epoch` / `reqId` 规则

#### 3.1.1 `epoch`

- 每个 `DirEntry` 持有单一 **64-bit committed epoch**。
- 只有 **“UBCC 形成新的全局意图”** 时才允许分配新 epoch；该值先作为 outstanding 的 **`reservedEpoch`** 保存。
- 对普通 remote miss grant：`processOuterRequest()` 决策时分配 `reservedEpoch`，但 **不得**立即写入 `DirEntry.epoch`；仅在匹配 `Clear` 被 home 接受时将 `DirEntry.epoch := reservedEpoch`。
- 对 `UPGRADE_PENDING`：`OuterUpgradeReq.epoch` 表示 requester 观察到的 **committed epoch 前置条件**；home UBCC 在请求接受时为 outstanding **预留 `reservedEpoch`**，但 **不得**立即写入 `DirEntry.epoch`；仅在 `OuterUpgradeDone` 被接受时将 `DirEntry.epoch := reservedEpoch`。
- 超时重传、Clear 重传、UpgradeAck/DoneAck 重传 **复用原 `(epoch, reqId)`**，不得自增。

#### 3.1.2 wrap 判定

```cpp
bool is_newer(uint64_t a, uint64_t b) {
    return ((a - b) & 0xffffffffffffffffULL) < (1ULL << 63);
}
```

#### 3.1.3 `reqId`

- 每个 `OutstandingRequest` 由**创建该 outstanding 的发起侧**独立分配 `reqId`；upgrade 路径中 requester 先带上 `reqId` 发 `OuterUpgradeReq`，home 接受后原样绑定到 `UPGRADE_PENDING`。
- 用途：
  - 去重
  - retransmit / idempotency
  - Clear/ClearAck 绑定
  - UpgradeReq/Ack/Done/DoneAck 绑定
  - 日志审计

### 3.2 可见域

| 字段 | 可见域 |
|---|---|
| `epoch`, `reqId` | **仅 EP/UBCC outer 层可见** |
| `ubcc_needed_perm`, `ubcc_write_intent` | HN-F → EP-SNF sideband |
| `m_shared_hint` | EP-SNF → HN-F CompData sideband |
| `ep_proxy_op` | **仅 gem5 内部 HN-F↔EP-RNF bookkeeping 可见**；不属于架构层 CHI 互操作字段 |

**HN-F / L2 / 普通 CHI message 不得携带 epoch/reqId。**

### 3.3 线性化点表

| 场景 | 线性化点 |
|---|---|
| ReadShared grant | **home 接受匹配 `Clear(pa, epoch, reqId)`** |
| ReadUnique / write-intent grant | **home 接受匹配 `Clear(pa, epoch, reqId)`** |
| Recall 结果 | 不改变线性化点；只补足数据/释放 recall barrier |
| Invalidation ack | 不改变线性化点；只释放 invalidate barrier |
| Clear/ClearAck | `Clear` 被 home 接受是普通 grant commit point；`ClearAck` 只是返回确认 |
| Local upgrade | **`OuterUpgradeDone` 被 home 接受时**；不是 `OuterUpgradeReq/Ack` 时刻 |

补充：

- 普通 grant 若带 `RECALL` / `INVALIDATE` prerequisite，则这些 barrier 必须先到 `DONE`，但 **barrier DONE 仍不是 commit point**。
- `G_I→G_S/G_E/G_M` 这类无 prerequisite 的 miss，也仍然只能在 **Clear 被 home 接受** 时提交 committed `DirEntry`。

### 3.4 BUSY / RETRY 策略

1. **并发同 PA `ReadUnique`：第二个返回 BUSY/RETRY。**
2. **同 PA duplicate OutstandingRequest：不 merge，直接 BUSY/RETRY。**
3. **`UPGRADE_PENDING` 窗口内所有冲突请求统一 BUSY/RETRY。**
4. **非 owner 请求者按 PA 维度 fair queue。**
5. BUSY 响应带 **backoff hint**。

### 3.5 Clear / ClearAck 模型

```text
Requester-side EPBackend ── Clear(pa, epoch, reqId, reason) ──> Home UBCC
Home UBCC               ── ClearAck(pa, epoch, reqId) ───────> Requester EPBackend
```

- Clear/ClearAck 绑定 `OutstandingRequest`，**不绑定 DirEntry 布尔位**。
- `GRANT_HANDSHAKE` 通过 Clear/ClearAck 提交并退休：home 仅在 **(a) prerequisite barriers 全部 DONE，且 (b) 接受匹配 `Clear(pa, epoch, reqId)`** 时提交 intended `DirEntry`。
- `UPGRADE_PENDING` **不使用** Clear/ClearAck；其退休协议固定为 `OuterUpgradeDone/OuterUpgradeDoneAck`。
- 丢包 / 超时：使用相同 `(pa, epoch, reqId)` 重发，必须幂等。
- 当 `GRANT_HANDSHAKE` 到达 `DONE` 后，home 必须保留 `(linePa, epoch, reqId, opType)` 的 replay tombstone，窗口为固定配置值 `W`（每次测试运行显式指定）。
- 在 `W` 内收到 duplicate `Clear`：必须返回与首次完全相同的缓存 `ClearAck`。
- `W` 过期后 tombstone 可回收；若之后再收到同 tuple 的 `Clear`，按 stale 处理：记录日志、丢弃、**不响应**。

### 3.6 审计日志要求

所有 grant、recall、invalidate、clear、upgrade 必须打出：

```text
[UBCC-ORDER] pa=<pa> epoch=<epoch> reqId=<reqId> op=<op> requester=<n> grant=<g>
```

这是 TC3 / TC8 / TC11 / 顺序验证用的强制 instrumentation。

---

## 4. Component Specifications

### 4.1 UBCC

#### 4.1.1 职责

- 维护全局 MESI 目录
- 作为同 PA 的总排序点
- 记录 committed owner / sharers / epoch
- 维护 outstanding/barrier 生命周期
- 驱动 recall / invalidation / clear / local-upgrade handshake

#### 4.1.2 目录状态机（规范态）

| 状态 | 语义 |
|---|---|
| `G_I` | 无 sharer、无 owner |
| `G_S` | 一个或多个 sharer，无 owner |
| `G_E` | 单 clean owner |
| `G_M` | 单 dirty owner |

#### 4.1.3 `processOuterRequest()` 的规范行为

**必须采用统一 reserve-then-commit 模型；`processOuterRequest()` 绝不直接修改 committed `DirEntry`。**

| 当前 committed 状态 | 请求 | intended 目录结果（仅记入 outstanding） | prerequisite / outstanding | committed 何时变化 |
|---|---|---|---|---|
| `G_I` | Shared | `G_S`, sharers+=req | `GRANT_HANDSHAKE` | matching `Clear` 被接受 |
| `G_I` | Unique(no write) | `G_E`, owner=req | `GRANT_HANDSHAKE` | matching `Clear` 被接受 |
| `G_I` | Unique(write) | `G_M`, owner=req | `GRANT_HANDSHAKE` | matching `Clear` 被接受 |
| `G_S` | Shared | `G_S`, sharers+=req | `GRANT_HANDSHAKE` | matching `Clear` 被接受 |
| `G_S` | Unique* by non-sharer | `G_E/G_M`, owner=req, old sharers 逻辑移除 | `INVALIDATE` + `GRANT_HANDSHAKE` | invalidate `DONE` 后再等 matching `Clear` |
| `G_S` | Unique* by existing sharer with local upgrade | **保持旧 committed `G_S`** | `UPGRADE_PENDING` | matching `OuterUpgradeDone` 被接受 |
| `G_E/G_M` | Shared by other | `G_S`, sharers={oldOwner,req}, owner=-1 | `RECALL` + `GRANT_HANDSHAKE` | recall `DONE` 后再等 matching `Clear` |
| `G_E/G_M` | Unique by other | `G_E/G_M`, owner=req | `RECALL` + `GRANT_HANDSHAKE` | recall `DONE` 后再等 matching `Clear` |
| `G_E/G_M` | ReadShared(self-owner) | 可实现为本地短路；若走 outer，则 intended 保持 owner=req | `GRANT_HANDSHAKE` 或本地短路命中 | 若走 outer，则 matching `Clear` 被接受 |
| `G_E/G_M` | ReadUnique(self-owner) | 可实现为本地短路；若走 outer，则 intended 保持 owner=req | `GRANT_HANDSHAKE` 或本地短路命中 | 若走 outer，则 matching `Clear` 被接受 |

说明：

- `processOuterRequest()` SHALL NOT 修改 `DirEntry.state/owner/sharers/epoch`；它只创建 `OutstandingRequest{reservedEpoch, intendedState, intendedOwner, intendedSharers}`。
- grant data **不得**在 prerequisite barrier 未完成前发给 requester；即：`RECALL/INVALIDATE` 未到 `DONE` 时，`GRANT_HANDSHAKE` 只能保留，不得发 grant。
- 普通 miss 的 committed `DirEntry` 只允许在 **所有 prerequisite barriers 已 `DONE` 且 home 接受 matching `Clear`** 时提交。
- `RECALL` / `INVALIDATE` 只负责物理完成与 barrier 释放，**不构成 commit point**。
- **`UPGRADE_PENDING` 绝不 eager-commit。** home 只预留 `reservedEpoch`、阻塞冲突者、等待 `OuterUpgradeDone`。

补充（no-self-recall generalization）：

- recall / invalidation 目标选择时，请求者自身不得作为“回调给自己”的候选；实现上先构造 `candidates_excluding_requester`。
- 对必须命中 dirty owner 的路径，`need_dirty_owner && candidates_excluding_requester.empty()` 在本方案可达路径中应为**不可达**。
- 因此该条件只保留为 **debug-only assertion**，用于捕捉实现偏移；release build **不需要**额外 fatal/panic 分支，也不需要单独 fallback 分支。

#### 4.1.4 local upgrade 四消息握手（规范）

1. `EP-RNF` 收到本地 `SnpCleanInvalid` 且识别为 remote-sharer local upgrade 场景时，必须先发 `OuterUpgradeReq`。
2. home UBCC 若接受：
   - 校验 `OuterUpgradeReq` 携带的 `(linePa, epoch, reqId)`
   - 预留 `reservedEpoch = nextEpoch`
   - 建立 `OutstandingRequest{opType=UPGRADE_PENDING, reqId=req.reqId, baseEpoch=req.epoch, reservedEpoch=reservedEpoch}`
   - **不修改 `DirEntry.state/owner/sharers/epoch`**
   - 回 `OuterUpgradeAck{epoch=reservedEpoch, reqId=req.reqId, accepted=true}`
3. `EP-RNF` **只有在收到 `OuterUpgradeAck{accepted=true}` 后** 才能回本地 HN-F `SnpResp_I`。
4. 本地 HN-F 完成 upgrade 后，请求侧 `EPBackend` 发 `OuterUpgradeDone`。
5. home UBCC 在 `OuterUpgradeDone` 被接受时：
   - 校验 `(linePa, epoch, reqId)` 与 outstanding 匹配
    - 提交 `ownerNode := requesterNode`
    - 从 committed sharers 中移除 requester
    - `state := G_E` 或 `G_M`（由 `desiredPerm + cause` 决定）
    - `epoch := reservedEpoch`
    - 回 `OuterUpgradeDoneAck{accepted=true}`
    - 退休 `UPGRADE_PENDING`
6. 若 `OuterUpgradeReq` 被拒绝或超时，EP-RNF 不得提前回 `SnpResp_I`；必须将本地 upgrade 失败化为 retry。

**irrevocable-after-ack 规则：**

- 一旦 home UBCC 发出 `OuterUpgradeAck{accepted=true}`，该 `UPGRADE_PENDING` 立即进入 **irrevocable-after-ack** 区间。
- 在该区间内，home **不得**取消该 upgrade，**不得**回退到旧 committed `DirEntry` 供新 grant 使用，**不得**释放该 PA 的冲突阻塞。
- 若 `OuterUpgradeDone` 超时：home 只允许三类动作：
  1. 重发缓存的 `OuterUpgradeAck(true)`；
  2. 接受匹配的 `OuterUpgradeDone`；
  3. 将该 PA 标记为 `PERSISTENT_BUSY`，并继续对冲突请求返回 `BUSY/RETRY`。

#### 4.1.5 `OutstandingRequest` 规范状态机（H1）

以下表为 **规范性真值表**。实现可内部拆分，但外部语义必须等价。

| opType | 当前 stage | 事件 | 下一 stage | 动作 |
|---|---|---|---|---|
| `RECALL` | `CREATED` | home 发出 `OuterRecallMsg` | `WAITING_TARGET_RESP` | 记录 target、deadline |
| `RECALL` | `WAITING_TARGET_RESP` | 收到匹配 `RecallResponse(success,data?)` | `DONE` | 校验 tuple；如有 data 写入 dataBuf；释放 recall barrier |
| `RECALL` | `WAITING_TARGET_RESP` | duplicate `RecallResponse` | `DONE` | 幂等忽略或仅记日志 |
| `RECALL` | `WAITING_TARGET_RESP` | stale / mismatched tuple | `WAITING_TARGET_RESP` | 丢弃并告警 |
| `RECALL` | `WAITING_TARGET_RESP` | owner timeout, retry budget 未耗尽 | `WAITING_TARGET_RESP` | 对原 owner 重发 recall |
| `RECALL` | `WAITING_TARGET_RESP` | owner timeout, retry budget 耗尽 | `TIMED_OUT` | 维持冲突请求 BUSY；打 fatal-grade audit |
| `INVALIDATE` | `CREATED` | invalidation fanout 发出 | `WAITING_ALL_ACKS` | 初始化 pendingMask |
| `INVALIDATE` | `WAITING_ALL_ACKS` | 收到匹配 `InvalidationAck(success=true)` 且非最后一个 | `WAITING_ALL_ACKS` | 清除对应 ack bit |
| `INVALIDATE` | `WAITING_ALL_ACKS` | 收到最后一个匹配 `InvalidationAck(success=true)` | `DONE` | 释放 invalidate barrier |
| `INVALIDATE` | `WAITING_ALL_ACKS` | duplicate ack | `WAITING_ALL_ACKS` 或 `DONE` | 幂等忽略 |
| `INVALIDATE` | `WAITING_ALL_ACKS` | target writeback/evict 先到 | `WAITING_ALL_ACKS` | 接收数据维护，但 **不得**代替 ack bit 清除 |
| `INVALIDATE` | `WAITING_ALL_ACKS` | timeout, retry budget 未耗尽 | `WAITING_ALL_ACKS` | 仅对剩余 mask 重发 |
| `INVALIDATE` | `WAITING_ALL_ACKS` | timeout, retry budget 耗尽 | `TIMED_OUT` | 维持该 PA BUSY；等待人工/故障路径 |
| `GRANT_HANDSHAKE` | `CREATED` | prerequisite barriers 全部 `DONE`，grant 已下发 requester | `WAITING_CLEAR` | 等待 Clear |
| `GRANT_HANDSHAKE` | `WAITING_CLEAR` | 收到匹配 `Clear` 且 prerequisite barriers 已 `DONE` | `DONE` | 提交 intended `DirEntry`；回 `ClearAck`；退休 handshake 并转 tombstone |
| `GRANT_HANDSHAKE` | `WAITING_CLEAR` | duplicate `Clear`（tombstone 窗口 `W` 内） | `DONE` | 重发相同 `ClearAck` |
| `GRANT_HANDSHAKE` | `WAITING_CLEAR` | timeout, retry budget 未耗尽 | `WAITING_CLEAR` | requester 侧重发 Clear |
| `GRANT_HANDSHAKE` | `WAITING_CLEAR` | timeout, retry budget 耗尽 | `TIMED_OUT` | intended 结果仍保留未提交；该 PA 继续 fenced/BUSY，直到 Clear 或故障恢复 |
| `UPGRADE_PENDING` | `CREATED` | `OuterUpgradeReq` 验证通过 | `WAITING_LOCAL_DONE` | 分配 `reservedEpoch`；发 `OuterUpgradeAck(true)` |
| `UPGRADE_PENDING` | `CREATED` | `OuterUpgradeReq` 验证失败 | `CANCELLED` | 发 `OuterUpgradeAck(false)` |
| `UPGRADE_PENDING` | `WAITING_LOCAL_DONE` | duplicate `OuterUpgradeReq` | `WAITING_LOCAL_DONE` | 重发缓存的 `OuterUpgradeAck` |
| `UPGRADE_PENDING` | `WAITING_LOCAL_DONE` | 冲突 miss/upgrade/writeback/evict | `WAITING_LOCAL_DONE` | 返回 `BUSY/RETRY`；不得重写目录 |
| `UPGRADE_PENDING` | `WAITING_LOCAL_DONE` | 收到匹配 `OuterUpgradeDone` | `DONE` | 提交 owner/state/epoch；发 `OuterUpgradeDoneAck(true)` |
| `UPGRADE_PENDING` | `WAITING_LOCAL_DONE` | duplicate `OuterUpgradeDone` | `DONE` | 重发缓存的 `OuterUpgradeDoneAck` |
| `UPGRADE_PENDING` | `WAITING_LOCAL_DONE` | timeout, retry budget 未耗尽 | `WAITING_LOCAL_DONE` | 重发 `OuterUpgradeAck(true)` |
| `UPGRADE_PENDING` | `WAITING_LOCAL_DONE` | timeout, retry budget 耗尽 | `PERSISTENT_BUSY` | must not cancel; keep PA fenced; resend cached `OuterUpgradeAck(true)`; only accept matching `OuterUpgradeDone` |

**终态定义**：`DONE`、`CANCELLED`、`TIMED_OUT`、`PERSISTENT_BUSY` 是唯一终态；终态后 duplicate 必须幂等，不得重写目录。

补充：`GRANT_HANDSHAKE` 在 `DONE` 之后不再保留为 live outstanding，而是转入 replay tombstone；tombstone 生命周期由固定窗口 `W` 控制。

#### 4.1.6 需要的方法签名（目标）

```cpp
GrantDecision processOuterRequest(
    uint64_t linePa,
    NeededPerm neededPerm,
    bool writeIntent,
    int requesterNode,
    uint64_t baseEpoch,
    uint64_t reqId);

UpgradeDecision processOuterUpgradeReq(
    uint64_t linePa,
    int requesterNode,
    uint64_t epoch,
    uint64_t reqId,
    NeededPerm desiredPerm,
    UpgradeCause cause);

bool processOuterUpgradeDone(
    uint64_t linePa,
    int requesterNode,
    uint64_t epoch,
    uint64_t reqId);

bool processRecallResponse(
    uint64_t linePa,
    int ownerNode,
    uint64_t epoch,
    uint64_t reqId,
    bool dataReturned,
    const uint8_t* dataBuf,
    size_t dataLen);

bool processInvalidationAck(
    uint64_t linePa,
    int ackNode,
    uint64_t epoch,
    uint64_t reqId,
    bool success);

bool processClear(
    uint64_t linePa,
    int srcNode,
    uint64_t epoch,
    uint64_t reqId,
    ClearReason reason);
```

#### 4.1.7 必删项

- `DirEntry.pendingOp`
- `DirEntry.materializedData*`
- “一个 PA 只允许一个 outstanding map entry”的实现假设
- `epoch == current_epoch` 的等值比较；改为 half-range + exact tuple 校验

### 4.2 EPBackend

#### 4.2.1 职责

- outer 路由与 PA 视图转换
- requester-side bookkeeping
- recall / invalidation dispatch
- grant data 组织与 Clear 生命周期管理
- local upgrade 的 `OuterUpgradeReq/Ack/Done/DoneAck` 管理

#### 4.2.2 requester-side 状态

| 状态 | 语义 |
|---|---|
| `R_I` | 无远端权限 |
| `R_WAIT_GRANT` | 已发起 miss，等待 outer 决策/CHI 安装 |
| `R_S` | 共享权限 |
| `R_E` | clean exclusive |
| `R_M` | dirty modified |

#### 4.2.3 关键要求

1. `handleRemoteMiss()`：
    - 构造 `OuterReqEnvelope(epoch, reqId)`
    - 调 home UBCC
    - 绑定 grant buffer 到该 `(epoch, reqId)`
    - 仅在 home 侧 prerequisite barriers 已完成后接收/转发 grant data
2. `handleRecallRequest()`：
    - read recall → `EP-RNF.startReadShared()`
    - write recall → `EP-RNF.startReadUnique(..., EpProxyOp::RecallUnique)`
    - **禁止以 functionalRead 代替 write recall invalidate**
3. `handleInvalidationRequest()`：
    - **必须走 `EP-RNF.startCleanUnique(..., EpProxyOp::InvalidateOnly)`，等 HN-F 完成后再 ack**
4. `notifyLocalWriteUpgrade()`：
   - 创建 `UPGRADE_PENDING` 事务上下文
   - 向 home UBCC 发送 `OuterUpgradeReq`
   - 收到 `OuterUpgradeAck(true)` 后才允许 EP-RNF 回本地 `SnpResp_I`
   - 本地 upgrade 完成后发送 `OuterUpgradeDone`
5. 若本地 writeback/evict 命中 `RECALL/INVALIDATE/UPGRADE_PENDING`：
    - **不得直接下沉到 UBCC 改写目录**
    - 必须在 requester/EP 侧 pin 住该行，待 outstanding 终态后再重试
6. 若本地 writeback/evict 命中 `GRANT_HANDSHAKE`：
   - 无论请求来自 **旧 committed owner/sharer** 还是 **新 requester（Clear 尚未被 home 接受）**，均必须返回 `BUSY/RETRY`
   - requester/EP 侧必须 pin line；home 不得执行任何目录更新

#### 4.2.4 本方案最关键修正

当前 `EPBackend.cc::handleInvalidationRequest()` 在基线代码中直接 `sendInvalidationAck()`，绕过 HN-F。  
**Scheme v4.0 规定必须改为：**

```cpp
_epRnfCtrl->startCleanUnique(
    invMsg.sharerLocalPa,
    EpProxyOp::InvalidateOnly,
    [this, invMsg](bool ok) {
    OuterInvalidationAck ack;
    ack.linePa = invMsg.linePa;
    ack.ackNode = _nodeId;
    ack.homeNode = invMsg.homeNode;
    ack.epoch = invMsg.epoch;
    ack.reqId = invMsg.reqId;
    ack.success = ok;
    sendInvalidationAck(ack);
});
```

### 4.3 EP-RNF

#### 4.3.1 职责

- 作为标准 `MachineType_Cache` RN-F 被 HN-F 识别
- 接收 HN-F snoop
- 将 outer recall/invalidate/upgrade 翻译成 CHI request
- 管理每 PA 单 inflight CHI 事务

#### 4.3.2 CHI 请求集

| outer 意图 | EP-RNF CHI 请求 | internal `EpProxyOp` |
|---|---|---|
| read recall | `ReadShared` | `None` |
| write recall | `ReadUnique` | `RecallUnique` |
| sharer invalidation | `CleanUnique` | `InvalidateOnly` |

**禁止 `ReadOnce` 作为 recall 路径。**

其中 `EpProxyOp` 是 gem5 内部 sideband，仅用于区分 EP-RNF proxy 流的 special completion；不改变对外可见的 CHI 协议语义。

#### 4.3.3 snoop 响应矩阵（规范）

| snoop | 行为 | 是否阻塞 |
|---|---|---|
| `SnpCleanInvalid` | 非 upgrade 场景立即 `SnpResp_I`；**local upgrade 场景必须先发 `OuterUpgradeReq`，收到 `OuterUpgradeAck(true)` 后才回 `SnpResp_I`** | upgrade 时是 |
| `SnpUnique` | `globalInvalidate`，完成后回 `SnpResp_I` / `SnpRespData_I(_PD)` | 是 |
| `SnpOnce` | 阻塞式 `remoteFetch` 到 home UBCC，回 `SnpRespData_SC` | 是 |
| `SnpShared` / `SnpSharedFwd` | **按设计不可达**；若到达 EP-RNF，必须 `fatal/panic` + audit | 是（终止） |
| `SnpOnceFwd` | **按设计不可达**；sole-EP-RNF 时必须先被 DCT fallback 改写为 `SnpOnce`；若仍到达 EP-RNF，必须 `fatal/panic` + audit | 是（终止） |

未来若引入 preserving-query snoop，则对 EP-RNF 的响应必须是 **SC-like**，而不是 **I-like**；当前方案明确禁止提前实现该分支，因为 `SnpResp_I` 会把 EP-RNF 从 `dir_sharers` 中移除。

**Per-PA snoop serialization：**

- 当 EP-RNF 对某 PA 已有 in-flight CHI transaction 时，后续到达的同 PA HN-F snoop 只能进入 **1-entry per-PA snoop slot**。
- 若 slot 已占用又到达第二个 snoop，则说明 HN-F 违反 single-flight 假设：必须 `fatal/panic`。
- 当前 transaction 完成后，queued snoop 的处理优先级高于 deferred outbound CHI request；必须先释放已入场的 HN-F transaction。
- queued snoop 必须产出合法 CHI `SnpResp_*`，**不得**向 HN-F 返回抽象 `BUSY/RETRY`：
  - `SnpCleanInvalid`：当前 txn 完成后执行本地 invalidate，返回 `SnpResp_I`
  - `SnpUnique`：当前 txn 完成后按 `retToSrc/hasData/isDirty` 选择 `SnpResp_I` / `SnpRespData_I` / `SnpRespData_I_PD`
  - `SnpOnce`：当前 txn 完成后返回 `SnpRespData_SC`
- queued snoop **不做 epoch 过滤**；CHI snoop 不携带 `epoch/reqId`，epoch 过滤只适用于 outer-layer messages。

#### 4.3.4 per-PA retry 规则

同 epoch 下保留最强 op：

```text
ReadUnique > CleanUnique > ReadShared
```

- stale epoch：直接丢弃
- 同 PA 已有 inflight：不再新建第二个 CHI txn
- 若 `outerTxnPending=true` 且收到新 snoop：
  - 同 tuple duplicate → 幂等吸收
  - HN-F snoop → 进入 1-entry snoop slot；slot overflow 直接 `fatal/panic`
  - defer 的 outbound CHI request → 保留 strongest-op retry，不做本地并行

### 4.4 EP-SNF

#### 4.4.1 职责

- 接收 HN-F 的 `ReadNoSnp` / `WriteNoSnp`
- 解析 UBCC sideband
- 调用 EPBackend outer miss
- 把 CompData / NCBWrData 送到正确目的地

#### 4.4.2 关键要求

1. `ReadNoSnp/ReadNoSnpSep`：
   - 读取 `ubcc_needed_perm`, `ubcc_write_intent`
   - grant blocked → BUSY/重试
2. `CompData`：
   - deferred 1 tick 发送可保留
   - **不做 epoch 二次重判**
3. `NCBWrData`：
   - **必须路由到 home node DDR4，而非 local DDR4**
4. Shared grant：
   - `m_shared_hint = true`
   - 数据类型对 EP-RNF 语义必须是 `CompData_SC`
5. 若 interconnect 可产生“请求与返回同 tick”：
   - **必须启用 deferred CompData**；否则需由系统保证请求/响应 separation ≥ 1 tick

### 4.5 HN-F / SLICC

#### 4.5.1 保持不变

- 原 CHI 稳态：`I / SC / UC / UD / SD`
- 原 transient state graph
- 原 L1/L2 语义

#### 4.5.2 必做增量

1. `epRnfMachineVersion` 参数 + `tbe.epRnfMachineID`
2. `RegisterEPRNF_OnSharedHint`
3. `pickSharerForSnoop()`
4. 4 个单目标 snoop action 改用 `pickSharerForSnoop()`
5. 3 个 DCT initiator 在 “sole sharer = EP-RNF” 时强制 `use_DCT=false`
6. `UpdateDirState_FromReqResp` 中 responder==EP-RNF 时不得 owner 晋升
7. `Send_CompData` 面向 EP-RNF 的 shared 响应固定为 clean shared 语义
8. CHI 内部 gem5 sideband 增加 `EpProxyOp`；对 EP-RNF proxy invalidation / recall-unique 使用 baseline prefix + completion `scrub_to_I`

#### 4.5.3 EP-touching HN-F 状态/事件表（H2）

本节**只定义 EP-touching 的 HN-F 状态/事件子集**。所有 **未显式列出** 的 HN-F 稳态、暂态、事件与转移，均保持 **baseline gem5 CHI implementation** 不变。不得从未列项推断新的 EP 专用行为；如发生冲突，优先保持 baseline，并仅做最小增量补充。

该表只枚举 **EP 实际触碰到的 HN-F 子集**；未列项继续沿用 baseline CHI。

| HN-F 稳态/暂态 | EP 相关事件 | HN-F 行为 / prefix | 对 EP/本地返回 | 最终状态 |
|---|---|---|---|---|
| `I` | EP-SNF 返回 `CompData_SC(shared_hint=true)` | 分配 TBE，进入 `RSC`；`tbe.dir_sharers += epRnfMachineID` | 向 requester 返回 shared data | `SC` |
| `I` | EP-SNF 返回 `CompData_UC/UD(shared_hint=false)` | baseline unique fill | 向 requester 返回 unique data | `UC/UD` |
| `SC/UC/UD/SD` | EP-RNF 发 `ReadShared`（read recall） | baseline shared handling；按 baseline 进入 `SC_RSC / UC_RSC / UD_RSC / SD_RSC` 等暂态 | `CompData_SC` 给 EP-RNF | baseline |
| `SC` | 本地升级请求且 `dir_sharers` 含 EP-RNF | 进入 `SC_RU`；向 EP-RNF 发 `SnpCleanInvalid` | 仅在收齐本地 sharer + EP-RNF `SnpResp_I` 后完成本地 upgrade | `UC/UD` |
| `SC` | EP-RNF 发 `CleanUnique(InvalidateOnly)` | baseline sharer invalidation fanout | `Comp_UC`（completion token only） | `I` |
| `SD` | EP-RNF 发 `CleanUnique(InvalidateOnly)` | baseline sharer invalidation fanout | `Comp_UC`（completion token only） | `I` |
| `UC` | EP-RNF 发 `ReadUnique(RecallUnique)` | baseline `SnpUnique` invalidate owner | `Comp_UC`（token）+ callback data | `I` |
| `UD` | EP-RNF 发 `ReadUnique(RecallUnique)` | baseline `SnpUnique` + dirty data collect | `Comp_UC`（token）+ callback data | `I` |
| 任意 | DCT 选择结果 sole target = EP-RNF | **先执行 DCT fallback**，改写为 non-DCT snoop initiator | EP-RNF 只会看到 `SnpOnce` / `SnpUnique` / `SnpCleanInvalid` | baseline non-DCT 终态 |

#### 4.5.4 EP-RNF proxy unique-flow special completion

```cpp
enum class EpProxyOp : uint8_t { None = 0, InvalidateOnly, RecallUnique };
```

对满足以下谓词的 **EP-RNF 发起 proxy 操作**：

```text
requestor == epRnfMachineID && ep_proxy_op != None
```

HN-F 采用：**baseline unique-flow prefix + special completion scrub**。

`CleanUnique + InvalidateOnly`：

- prefix：复用 baseline sharer invalidation fanout（对其他 sharer 发 `SnpCleanInvalid`）
- completion：收敛到 `I`，**不是** `UC owner=EP-RNF`
- `dir_sharers.remove(epRnfMachineID)`，`dir_ownerExists=false`，`state=I`
- `Comp_UC` 只作为 **completion token**，**不是** ownership grant

`ReadUnique + RecallUnique`：

- prefix：复用 baseline `SnpUnique` 到旧 owner，保留 dirty data collection
- completion：dirty data 进入 callback buffer 并稳定后，收敛到 `I`
- `dir_sharers.remove(epRnfMachineID)`，`dir_ownerExists=false`，`state=I`
- `dataValid=false` 只能在 callback payload stabilized **之后** 清除

正确性保证：

1. special path **只**由 `(requestor==epRnfMachineID && ep_proxy_op!=None)` 触发，与 baseline path 判定互斥。
2. prefix 与 baseline 完全一致，因此 invalidation fanout 与 dirty data collection 的正确性直接继承 baseline。
3. completion 收敛到 `I`，表示本节点不保留任何本地权限；后续访问必须经 UBCC 重新授权。
4. `scrub_to_I` 只是本地状态改写，不引入新的消息依赖或等待边，因此不会新增 deadlock edge。

| HN-F state | EP event | prefix | return | final state |
|---|---|---|---|---|
| `SC` | EP-RNF `CleanUnique(InvalidateOnly)` | baseline sharer invalidation fanout | `Comp_UC` (token) | `I` |
| `SD` | EP-RNF `CleanUnique(InvalidateOnly)` | baseline sharer invalidation fanout | `Comp_UC` (token) | `I` |
| `UC` | EP-RNF `ReadUnique(RecallUnique)` | baseline `SnpUnique` invalidate owner | `Comp_UC` (token) + callback data | `I` |
| `UD` | EP-RNF `ReadUnique(RecallUnique)` | baseline `SnpUnique` + dirty data collect | `Comp_UC` (token) + callback data | `I` |
| `*` | EP-RNF `ReadShared` | baseline shared handling | `CompData_SC` | baseline |

局部正确性论证（为避免与 `§8` 全局不变量编号冲突，此处记为 `P1-P7`）：

- `P1 / Isolation`：special path predicate `(requestor==epRnfMachineID && ep_proxy_op!=None)` 与所有 baseline path 判定互斥。
- `P2 / Fanout correctness`：prefix 不变，因此 fanout 正确性直接继承 baseline。
- `P3 / No data loss`：`ReadUnique` 路径在 `scrub_to_I` 前保留 callback buffer 中的 dirty data。
- `P4 / No local rights`：完成后 HN-F=`I`，后续访问必须经 UBCC 重授权。
- `P5 / Outer consistency`：`I` 与 outer 协议“该节点已无本地权限”的预期一致。
- `P6 / No deadlock`：`scrub_to_I` 为本地状态改写，不新增等待边。
- `P7 / Re-entrancy safe`：TBE 退休、目录清空、无残余 owner/sharer 状态。

仍需在实现中显式文档化的 5 个 proof gaps：

- `G1`：`Comp_UC` 明确是 **non-authoritative completion token**；权限来源于目录状态，而不是消息名。
- `G2`：`scrub_to_I` 必须在 callback payload stabilized 之后、TBE retirement 之前/期间执行。
- `G3`：证明依赖 same-PA exclusion：HN-F single-flight、EP-RNF single-inflight、UBCC serialization。
- `G4`：必须同时清空 **directory metadata** 与 **cache array footprint**；不能只做 `dataValid=false`。
- `G5`：证明范围是 coherence correctness + authorization correctness + no-loss；**不**承诺 message-level trace equivalence。

#### 4.5.5 sole-EP-RNF 的唯一规范 fallback（H3）

依据 `docs/recovery/entry_document.md`：

- §3.3：当 `dir_sharers={EP-RNF}` 且 initiator 原本会选 DCT 时，**强制 `use_DCT=false`**；随后走 **non-DCT snoop path**。
- §6.1 validated decision：**“DCT disabled when EP-RNF is only sharer; non-DCT path works correctly.”**
- §6.2 reverted decisions：已否定用其他非 CHI 正统路径替代 snoop 的做法。

**因此，本方案唯一规范 fallback 是：强制 `use_DCT=false`，继续走 baseline non-DCT snoop path；绝不改走所谓 “DMT-disabled ReadNoSnp path”。**

具体化：

| initiator 原意图 | sole-EP-RNF 时唯一允许 fallback |
|---|---|
| `ReadShared` / `ReadOnce` 无 owner | `Send_SnpOnce` |
| `ReadUnique` | `Send_SnpUnique` 或 `Send_SnpUnique_RetToSrc`（按 baseline `retToSrc` 判定） |
| `SnpSharedFwd` / `SnpOnceFwd` / `SnpUniqueFwd` | 先关 DCT，再降到上表 non-DCT 变体 |

### 4.6 协议状态转移矩阵（实施摘要）

#### 4.6.1 UBCC 基本 grant matrix

| committed state × req | committed DirEntry during outstanding | intended result | actions | commit point |
|---|---|---|---|---|
| `G_I × RS` | 保持 `G_I` | `G_S, sharers+=req` | `GRANT_HANDSHAKE` | matching `Clear` |
| `G_I × RU(no write)` | 保持 `G_I` | `G_E, owner=req` | `GRANT_HANDSHAKE` | matching `Clear` |
| `G_I × RU(write)` | 保持 `G_I` | `G_M, owner=req` | `GRANT_HANDSHAKE` | matching `Clear` |
| `G_S × RS` | 保持 `G_S` | `G_S, sharers+=req` | `GRANT_HANDSHAKE` | matching `Clear` |
| `G_S × RU(*) by non-sharer` | 保持 `G_S` | `G_E/G_M, owner=req, old sharers remove` | `INVALIDATE` + `GRANT_HANDSHAKE` | invalidate `DONE` + matching `Clear` |
| `G_S × RU(*) by existing sharer(local upgrade)` | 保持 committed `G_S` | `G_E/G_M, owner=req, sharers-=req` | `UPGRADE_PENDING` | matching `OuterUpgradeDone` |
| `G_E/G_M × RS(other)` | 保持 `G_E/G_M` | `G_S, sharers={oldOwner,req}, owner=-1` | `RECALL` + `GRANT_HANDSHAKE` | recall `DONE` + matching `Clear` |
| `G_E/G_M × RU(other)` | 保持 `G_E/G_M` | `G_E/G_M, owner=req` | `RECALL` + `GRANT_HANDSHAKE` | recall `DONE` + matching `Clear` |

#### 4.6.2 UBCC edge matrix（M2）

| state × req | next state | actions |
|---|---|---|
| `G_E/G_M × ReadShared(self-owner)` | 不变 | idempotent grant；无 recall |
| `G_E/G_M × ReadUnique(self-owner)` | 不变 | idempotent grant；无 recall |
| `any × Writeback/Evict` 且 **无 outstanding** | baseline | 正常 `processWriteback/processEvict` |
| `any × Writeback/Evict` 且 `RECALL` outstanding | 不变 | 返回 `BUSY/RETRY`；本地 pin line；若 writeback data 先到只允许并入 recall dataBuf，**不得**推进目录提交 |
| `any × Writeback/Evict` 且 `INVALIDATE` outstanding | 不变 | 返回 `BUSY/RETRY`；本地 pin line；不得代替 invalidate ack |
| `any × Writeback/Evict` 且 `UPGRADE_PENDING` outstanding | 不变 | 返回 `BUSY/RETRY`；upgrade requester 与被升级 line 都必须 pin 住；不得取消 upgrade |
| `any × Writeback/Evict` 且 `GRANT_HANDSHAKE` outstanding，来源=旧 committed owner/sharer | 不变 | 返回 `BUSY/RETRY`；source pin line；不得做目录更新 |
| `any × Writeback/Evict` 且 `GRANT_HANDSHAKE` outstanding，来源=新 requester（其 Clear 尚未被接受） | 不变 | 返回 `BUSY/RETRY`；source pin line；不得做目录更新 |
| `any × Writeback/Evict` 且 `GRANT_HANDSHAKE` outstanding，Clear 已退休为 tombstone | baseline | 仅在 matching `Clear` 已退休后，writeback/evict 才可正常进入 baseline 处理 |

#### 4.6.3 EP-RNF SnpUnique 响应

| retToSrc | hasData | isDirty | resp |
|---|---|---|---|
| false | * | * | `SnpResp_I`；若 dirty 且需要数据则附 `SnpRespData_I_PD` |
| true | true | true | `SnpRespData_I_PD` |
| true | true | false | `SnpRespData_I` |
| true | false | * | `SnpResp_I` fallback |

#### 4.6.4 HN-F 单目标 snoop

| action | 目标选择 |
|---|---|
| `Send_SnpUnique_RetToSrc` | `pickSharerForSnoop()` |
| `Send_SnpSharedFwd_ToSharer` | `pickSharerForSnoop()` |
| `Send_SnpOnce` | `pickSharerForSnoop()` |
| `Send_SnpOnceFwd` | `pickSharerForSnoop()` |

---

## 5. Protocol Flows

### 5.1 场景 A：首次远端 Shared miss

```text
CPU_i/L2_i
  → HN-F_i(ReadShared)
  → EP-SNF_i(ReadNoSnp + neededPerm=Shared, writeIntent=false)
  → EPBackend_i
  → UBCC_home.processOuterRequest()
  => reserve intended G_I→G_S, sharers+=i, reservedEpoch=e1; committed DirEntry 仍为旧值
  → EPBackend_i(handleGrant)
  → EP-SNF_i(CompData_SC, m_shared_hint=true)
  → HN-F_i(RegisterEPRNF_OnSharedHint)
  → EPBackend_i 发送 Clear(pa, e1, reqId)
  → UBCC_home 接受 matching Clear，提交 G_I→G_S, sharers+=i, epoch:=e1
  → UBCC_home 返回 ClearAck；`GRANT_HANDSHAKE` 退休为 tombstone(W)
```

### 5.2 场景 B：远端 Shared 读命中旧 owner（read recall）

```text
UBCC_home: state=G_M owner=j, requester=i wants Shared
  => reserve intended state:=G_S, sharers={i,j}, owner=-1, reservedEpoch=e2
  => create RECALL(reqId=r1, mode=ReadRecall) + GRANT_HANDSHAKE(reqId=r1g)
  → EPBackend_j.handleRecallRequest()
  → EP-RNF_j.startReadShared(localPa)
  → HN-F_j snoop owner L2_j, collect clean/dirty data
  → EPBackend_j.sendRecallResponse(epoch, reqId=r1, data)
  → UBCC_home.processRecallResponse()  // only fills data buffer / verifies tuple; release recall barrier
  → prerequisite DONE 后，EP-SNF_i sends CompData_SC
  → requester i 发送 Clear(pa, e2, reqId=r1g)
  → UBCC_home 接受 Clear，提交 reserved intended result
  → ClearAck + tombstone(W)
```

### 5.3 场景 C：远端 Unique/Write 命中旧 owner（write recall）

```text
UBCC_home: state=G_E/G_M owner=j, requester=i wants Unique
  => reserve intended state:=G_E or G_M, owner:=i, reservedEpoch=e3
  => create RECALL(reqId=r2, mode=WriteRecall) + GRANT_HANDSHAKE(reqId=r2g)
  → EPBackend_j.handleRecallRequest()
  → EP-RNF_j.startReadUnique(localPa)
  → HN-F_j 发 SnpUnique / invalidation 到本地 owner L2_j
  → owner L2_j 失效，必要时返回 dirty data；EP-RNF_j 仅接收 `Comp_UC` completion token + callback data
  → HN-F_j special completion：本地 `scrub_to_I`，不得在本地目录中把 EP-RNF_j 记为 owner
  → response 回 home UBCC（按 epoch+reqId 验证）；仅释放 recall barrier
  → recall barrier DONE 后，EP-SNF_i 发 grant data
  → requester i 发送 Clear(pa, e3, reqId=r2g)
  → UBCC_home 接受 Clear，提交 intended owner:=i, epoch:=e3
  → ClearAck + tombstone(W)
```

### 5.4 场景 D：`G_S` 上的升级失效链

```text
requester=i wants Unique on shared line, and i is NOT already a committed sharer doing local upgrade
UBCC_home:
  reserve intended owner:=i, state:=G_E/G_M, reservedEpoch=e4
  create INVALIDATE(reqId=r3, targetMask=oldSharers-{i})
  create GRANT_HANDSHAKE(reqId=r4)

for each sharer s:
  home EPBackend → sharer EPBackend.handleInvalidationRequest()
  sharer EPBackend → EP-RNF.startCleanUnique(sharerLocalPa, EpProxyOp::InvalidateOnly)
  EP-RNF → HN-F_s → snoop local sharers/L2
  completion callback：`Comp_UC` 仅作 token，HN-F_s 收敛到 `I`
  → sendInvalidationAck(epoch, reqId=r3)

 home UBCC 在最后一个 ack 后仅释放 INVALIDATE，不重写 owner
 prerequisite DONE 后才允许向 i 下发 grant
 i 安装完成后发 Clear(pa, e4, reqId=r4)
 home UBCC 接受 Clear 时才提交 owner:=i/state:=G_E/G_M/epoch:=e4
```

### 5.5 场景 E：local upgrade（TC11 关键路径，t0-t9）

```text
t0  Node B 已是 remote sharer；UBCC_home committed state = G_S，sharers 含 B；EP-RNF_B 已在 HN-F_B dir_sharers 中注册
t1  L2_B 发 CleanUnique/upgrade 给 HN-F_B
t2  HN-F_B 对 EP-RNF_B 发 SnpCleanInvalid，并进入本地 upgrade transient
t3  EP-RNF_B → EPBackend_B → UBCC_home: OuterUpgradeReq{linePa, srcNode=B, epoch=currentCommittedEpoch, reqId=r5, desiredPerm=Unique, cause=LocalCleanUnique}
t4  UBCC_home 验证 B 仍是 committed sharer 且 epoch 匹配，创建 UPGRADE_PENDING，预留 reservedEpoch；回复 OuterUpgradeAck{epoch=reservedEpoch, accepted=true}
t4.5 从此刻起进入 irrevocable-after-ack：home 只能重发 Ack、接受 Done，或在超时耗尽后转 `PERSISTENT_BUSY`
t5  EP-RNF_B ONLY AFTER UpgradeAck 才回本地 HN-F_B: SnpResp_I
t6  HN-F_B 完成本地 sharer 清退/upgrade，L2_B 获得本地 unique 视图；此时 home 目录仍保持旧 committed G_S
t7  EPBackend_B → UBCC_home: OuterUpgradeDone{linePa, srcNode=B, homeNode, epoch=reservedEpoch, reqId=r5}
t8  UBCC_home 提交 owner:=B，state:=G_E/G_M，epoch:=reservedEpoch，并回复 OuterUpgradeDoneAck{accepted=true}
t9  B 侧退休本地 upgrade context；home 侧退休 UPGRADE_PENDING；后续冲突请求方可入场
```

补充：

- `t4~t8` 窗口内任何同 PA 冲突请求一律 `BUSY/RETRY`。
- `t5` 之前严禁 `SnpResp_I`；这是修复 “local upgrade early-SnpResp” 的核心约束。

### 5.6 场景 F：writeback / evict

```text
owner/sharer node
  → EP-SNF recv CBWrData/NCBWrData or evict intent
  → EPBackend.handleWriteback()/handleEvict()
  → 若该 PA 存在 RECALL/INVALIDATE/UPGRADE_PENDING/GRANT_HANDSHAKE outstanding：BUSY/RETRY，并在本地 pin 住 line
  → 若来源是“新 requester 但其 Clear 尚未被 home 接受”，同样 BUSY/RETRY + pin
  → 否则 translate local PA -> home PA
  → UBCC_home processWriteback/processEvict(epoch, reqId)
  → requester bookkeeping state update
```

---

## 6. Message Formats

### 6.1 Outer 消息（规范）

```cpp
enum class NeededPerm { Shared = 0, Unique = 1 };
enum class RecallMode { None, ReadRecall, WriteRecall, SharerInvalidate };
enum class ClearReason { GrantHandshake };
enum class UpgradeCause { LocalCleanUnique, LocalStoreUpgrade };

struct OuterReqEnvelope {
    uint64_t linePa;
    NeededPerm neededPerm;
    bool writeIntent;
    RecallMode recallModeHint;
    int srcNode;
    uint64_t epoch;
    uint64_t reqId;
};

struct OuterGrantEnvelope {
    uint64_t linePa;
    int homeNode;
    OuterGrantType grantType;
    uint64_t epoch;
    uint64_t reqId;
    Tick decisionTick;   // reserve/intended result creation tick; not commit point
};

struct OuterRecallMsg {
    uint64_t linePa;
    uint64_t ownerLocalPa;
    int ownerNode;
    int homeNode;
    uint64_t epoch;
    uint64_t reqId;
    RecallMode mode; // ReadRecall / WriteRecall
    bool dataNeeded;
};

struct OuterInvalidateMsg {
    uint64_t linePa;
    uint64_t sharerLocalPa;
    int sharerNode;
    int homeNode;
    uint64_t epoch;
    uint64_t reqId;
};

struct OuterClearMsg {
    uint64_t linePa;
    int srcNode;
    int homeNode;
    uint64_t epoch;
    uint64_t reqId;
    ClearReason reason;
};

struct OuterClearAckMsg {
    uint64_t linePa;
    int homeNode;
    int dstNode;
    uint64_t epoch;
    uint64_t reqId;
    bool accepted;
};

struct OuterUpgradeReq {
    uint64_t linePa;
    int srcNode;
    uint64_t epoch;      // requester observed committed epoch
    uint64_t reqId;      // requester-allocated id, home echoes back if accepted
    NeededPerm desiredPerm;
    UpgradeCause cause;
};

struct OuterUpgradeAck {
    uint64_t linePa;
    int homeNode;
    int dstNode;
    uint64_t epoch;      // reservedEpoch if accepted, request epoch echo otherwise
    uint64_t reqId;
    bool accepted;
};

struct OuterUpgradeDone {
    uint64_t linePa;
    int srcNode;
    int homeNode;
    uint64_t epoch;
    uint64_t reqId;
};

struct OuterUpgradeDoneAck {
    uint64_t linePa;
    int homeNode;
    int dstNode;
    uint64_t epoch;
    uint64_t reqId;
    bool accepted;
};
```

### 6.2 CHI sideband

#### 6.2.1 对外可见 sideband（仅此三项）

```cpp
// CHIRequestMsg
ubcc_needed_perm   // 0/1
ubcc_write_intent  // bool

// CHIDataMsg
m_shared_hint      // bool
```

#### 6.2.2 internal gem5-only sideband

```cpp
enum class EpProxyOp : uint8_t { None = 0, InvalidateOnly, RecallUnique };

// CHIRequestMsg (internal gem5 bookkeeping only)
ep_proxy_op
```

`ep_proxy_op` 只用于区分 EP-RNF proxy flow 的 completion 语义；它不是架构层 CHI 互操作字段，也不得被解释为额外授权信息。

### 6.3 sideband → CHI 翻译表

| neededPerm | writeIntent | EP 翻译结果 |
|---|---|---|
| 0 | false | Shared miss |
| 1 | false | Unique-no-write / `CleanUnique` 语义 |
| 1 | true | Write-intent / `ReadUnique` 语义 |

---

## 7. Data Structures

### 7.1 UBCC `DirEntry`

```cpp
struct DirEntry {
    uint64_t linePa;
    MESIState state;          // G_I / G_S / G_E / G_M (committed only)
    uint64_t sharersMask;     // committed sharers only
    int ownerNode;            // committed owner, -1 if none
    bool dirty;               // valid iff state==G_M
    uint64_t epoch;           // committed global epoch
    uint64_t nextReqId;       // monotonic local allocator
};
```

**禁止保留：** `pendingOp`、`materializedData`、`pendingRequester`、`pendingRecallTarget` 这类把暂态粘在目录上的字段。

### 7.2 UBCC `OutstandingRequest`

```cpp
enum class OpType {
    RECALL,
    INVALIDATE,
    GRANT_HANDSHAKE,
    UPGRADE_PENDING,
};

enum class OpStage {
    CREATED,
    WAITING_TARGET_RESP,
    WAITING_ALL_ACKS,
    WAITING_LOCAL_DONE,
    WAITING_CLEAR,
    DONE,
    CANCELLED,
    TIMED_OUT,
    PERSISTENT_BUSY,
};

struct OutstandingRequest {
    uint64_t linePa;
    uint64_t baseEpoch;       // requester observed committed epoch / validation baseline
    uint64_t reservedEpoch;   // epoch to be committed on Clear or UpgradeDone
    uint64_t reqId;
    OpType opType;
    OpStage stage;
    int requesterNode;
    int homeNode;
    int targetNode;
    uint64_t targetMask;
    MESIState intendedState;
    uint64_t intendedSharersMask;
    int intendedOwnerNode;
    bool intendedDirty;
    NeededPerm neededPerm;
    bool writeIntent;
    RecallMode recallMode;
    UpgradeCause upgradeCause;
    Tick createTick;
    Tick respTick;
    Tick deadlineTick;
    bool accepted;
    bool dataValid;
    bool recallBarrierDone;
    bool invalidateBarrierDone;
    bool clearAckCached;
    uint8_t dataBuf[64];
};
```

补充：`GRANT_HANDSHAKE` 在 `DONE` 后转为 tombstone，而非继续保留 live outstanding。

```cpp
struct GrantHandshakeTombstone {
    uint64_t linePa;
    uint64_t epoch;
    uint64_t reqId;
    OpType opType;            // always GRANT_HANDSHAKE for this table
    OuterClearAckMsg ack;
    Tick expireTick;          // createTick + W
};
```

### 7.3 EPBackend `RequesterLineEntry`

```cpp
struct RequesterLineEntry {
    uint64_t localPa;
    int homeNode;
    RequesterLineState state; // R_I / R_WAIT_GRANT / R_S / R_E / R_M
    uint64_t epoch;
    uint64_t reqId;
    bool writeIntent;
};
```

### 7.4 EP-RNF `PendingChiTxn`

```cpp
enum class PendingChiOp { ReadShared, CleanUnique, ReadUnique };

struct PendingChiTxn {
    uint64_t linePa;
    uint64_t epoch;
    uint64_t reqId;
    PendingChiOp op;
    EpProxyOp proxyOp;
    MachineID hnfDest;
    int beatsExpected;
    int beatsReceived;
    bool needsCompAck;
    bool outerTxnPending;
    bool callbackPayloadStable;
    bool snoopSlotValid;
    SnoopType queuedSnoopType;
    bool queuedRetToSrc;
    std::function<void(bool)> onComplete;
};
```

### 7.5 EP-RNF `RetryEntry`

```cpp
struct RetryEntry {
    uint64_t linePa;
    uint64_t epoch;
    uint64_t reqId;
    PendingChiOp strongestOp; // ReadUnique > CleanUnique > ReadShared
};
```

### 7.6 EP-SNF `DeferredGrantEntry`

```cpp
struct DeferredGrantEntry {
    uint64_t linePa;
    int homeNode;
    uint64_t epoch;
    uint64_t reqId;
    OuterGrantType grantType;
    bool sharedHint;
    DataBlock data;
};
```

---

## 8. Invariants & Assertions

### 8.1 不变量

1. **I1 / 单一全局权威**：对任意 PA，`DirEntry(state, ownerNode, sharersMask, epoch)` 是唯一 committed truth。  
2. **I2 / epoch 单调**：只有 UBCC 形成新意图时允许分配新 epoch；所有新 epoch 都先作为 `reservedEpoch`，仅在 commit 点写入 `DirEntry.epoch`。  
3. **I3 / no-CHI-epoch**：`epoch/reqId` 不得进入 CHI message。  
4. **I4 / no-bypass-hnf**：任何本地 invalidate/recall 都必须经 HN-F。  
5. **I5 / one-PA-one-CHI-flight**：EP-RNF 对同 PA 只允许一个 inflight CHI txn。  
6. **I6 / reserve-not-commit**：`processOuterRequest()` / `processOuterUpgradeReq()` 只能记录 intended 结果，绝不直接改 committed `DirEntry`。  
7. **I7 / clear commits normal miss**：普通 miss 只有在 matching `Clear` 被 home 接受后才允许把 intended 结果写回 committed `DirEntry`。  
8. **I8 / ack-before-snoopresp**：若 `SnpCleanInvalid` 走 local upgrade 路径，则 `OuterUpgradeAck(accepted=true)` 先于 `EP-RNF → HN-F : SnpResp_I`。  
9. **I9 / no-eager-commit-during-upgrade**：`UPGRADE_PENDING` 存活期间，`DirEntry.state/owner/sharers/epoch` 必须保持旧 committed 值；owner commit 只允许发生在 `OuterUpgradeDone` 被接受时。  
10. **I10 / TBE timing invariant**：实现必须满足二选一：**(a)** interconnect 结构性保证 request/response separation ≥ 1 tick；或 **(b)** 只要存在 same-tick 返回可能，EP-SNF deferred `CompData` 就是强制项。  
11. **I11 / irrevocable-after-ack**：一旦 home 发出 `OuterUpgradeAck(true)`，该 `UPGRADE_PENDING` 不得取消，不得解封该 PA，只能等待 matching `OuterUpgradeDone` 或转 `PERSISTENT_BUSY`。  
12. **I12 / grant-after-barriers**：带 `RECALL/INVALIDATE` prerequisite 的普通 grant，必须在所有 prerequisite barriers `DONE` 后才能向 requester 发数据/完成安装。  
13. **I13 / commit-on-Clear**：对普通 miss，`RECALL/INVALIDATE` ack 只释放 barrier；**不**构成 commit point；commit 点唯一是 home 接受 matching `Clear`。  
14. **I14 / tombstone retention**：`GRANT_HANDSHAKE` 完成后必须保留 replay tombstone 至窗口 `W` 结束；`W` 内 duplicate `Clear` 必须返回相同 `ClearAck`。  
15. **I15 / snoop-queue-1-entry**：EP-RNF 对同 PA 最多缓存 1 个待处理 snoop；第二个 snoop 到达即为协议违例并触发 `fatal/panic`。  
16. **I16 / no-self-recall-unreachable**：对 owner-required 路径，`need_dirty_owner && candidates_excluding_requester.empty()` 只能是 debug 断言命中的实现错误，不能是 release-path 分支。  
17. **I17 / proxy-special-path-disjoint**：只有 `(requestor==epRnfMachineID && ep_proxy_op!=None)` 才能进入 EP-RNF proxy special completion；该判定必须与 baseline 路径互斥。  
18. **I18 / proxy-prefix-equals-baseline**：`InvalidateOnly` 与 `RecallUnique` 的 prefix 必须复用 baseline invalidation / dirty-data-collect 逻辑，不得在 prefix 阶段分叉新语义。  
19. **I19 / callback-before-scrub**：`RecallUnique` 路径中，callback payload 必须先稳定，再执行 `scrub_to_I`；`Comp_UC` 只表示完成，不表示授权。  
20. **I20 / scrub-to-I-no-rights**：EP-RNF proxy special completion 后，HN-F 必须为 `I`，`dir_sharers` 不含 EP-RNF，`dir_ownerExists=false`；后续访问必须重新经 UBCC 授权。  
21. **I21 / full-local-scrub**：special completion 必须同时清空目录元数据和 cache/tag/data footprint；不能只改 `dataValid=false`。  
22. **I22 / no-preserving-query-snoop-to-eprnf**：`SnpShared` / `SnpSharedFwd` / `SnpOnceFwd` 在当前设计中不得到达 EP-RNF；到达即为 fatal-grade 协议违例并需审计。  

### 8.2 强制断言

```cpp
assert(!(neededPerm == Shared && writeIntent));
assert(epoch_req == outstanding.reservedEpoch || epoch_req == outstanding.baseEpoch);
assert(reqId_req == outstanding.reqId);
assert(!(resp_is_stale(epoch_req, dir.epoch)));
assert(!chi_msg_contains_epoch_or_reqId);
assert(invalidation_ack_after_cleanunique_complete);
assert(!local_upgrade_snpresp_before_upgrade_ack);
assert(!(outstanding.opType == UPGRADE_PENDING && dir_committed_state_changed_early));
assert(!(in_processOuterRequest && dir_committed_state_modified));
assert(!(grant_emitted_before_all_prereq_barriers_done));
assert(!(upgrade_ack_true_sent && upgrade_state_cancelled));
assert(!(grant_handshake_done_without_clear_commit));
assert(!(duplicate_clear_within_W && clear_ack_differs_from_cached));
assert(!(pending_chi_txn.snoopSlotValid && second_snoop_arrives_same_pa));
assert(timing_separation_ge_1tick || epsnf_deferred_compdata_enabled);
#ifndef NDEBUG
assert(!(need_dirty_owner && candidates_excluding_requester.empty()));
#endif
assert(!(requestor == epRnfMachineID && ep_proxy_op != EpProxyOp::None && final_hnf_state != I));
assert(!(ep_proxy_special_completion && comp_uc_interpreted_as_authoritative_grant));
assert(!(ep_proxy_op == EpProxyOp::RecallUnique && !callback_payload_stabilized_before_scrub));
assert(!(ep_proxy_special_completion && (dir_owner_exists || dir_sharers_contains_ep_rnf)));
assert(!(ep_proxy_special_completion && cache_array_or_tag_retains_line));
assert(!target_ep_rnf_receives_snp_shared);
assert(!target_ep_rnf_receives_snp_shared_fwd);
assert(!target_ep_rnf_receives_snp_once_fwd);
```

### 8.3 epoch 一致性断言

```cpp
// response path
assert(resp.epoch == outstanding.reservedEpoch);
assert(resp.reqId == outstanding.reqId);

// dequeue path
assert(!is_newer(dir.epoch, deferred.epoch));
```

注：EP-SNF deferred CompData **不要求再次校验 epoch 是否等于当前目录 epoch**；其安全性依赖于“失效必须先经 HN-F”和上面的 timing invariant。

---

## 9. Implementation Layers

### 9.1 Layer 3a：Infrastructure

| 文件 | 关键修改 |
|---|---|
| `gem5/configs/ruby/CHI_config.py` | HN-F 默认 `alloc_on_readunique=true`，保留 shared path 可缓存 |
| `gem5/configs/ruby/CHI_ubcc_framework.py` | 修正 L3 DSM policy、注入 `epRnfMachineVersion`、`deadlock_threshold=20000000` |
| `gem5/src/mem/ruby/protocol/chi/CHI-msg.sm` | `m_shared_hint` + internal `EpProxyOp` |
| `gem5/src/mem/ruby/protocol/chi/CHI-cache.sm` | `epRnfMachineVersion` + `tbe.epRnfMachineID` |
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.py` | 增加 `enable_self_test` |
| `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.py` | 如需，暴露 HN-F version/EP backend param |

### 9.2 Layer 3b：SLICC Protocol

| 文件 | 关键修改 |
|---|---|
| `CHI-cache-funcs.sm` | `initializeTBE()` 注入 `epRnfMachineID`；`pickSharerForSnoop()`；sideband helper |
| `CHI-cache-actions.sm` | 4 个 snoop 选择点替换；DCT fallback；`RegisterEPRNF_OnSharedHint`；EP-RNF `CompData_SC` 语义；proxy unique-flow `scrub_to_I` |
| `CHI-cache-transitions.sm` | shared_hint 触发注册；CompAck 保护转移；TC11 upgrade transient 钩子；proxy special completion 收敛到 `I` |
| `CHI-cache-ports.sm` | 保留必要 DPRINTF，其他不动 |

### 9.3 Layer 3c：EP Controllers

| 文件 | 关键修改 |
|---|---|
| `EPRNFController.hh/.cc` | 加 `startReadUnique()`；`EpProxyOp` 透传；完整 snoop matrix；**1-entry per-PA snoop slot**；preserving-query snoop unreachable/fatal；per-PA strongest retry；multi-beat data；`OuterUpgradeAck` gating |
| `EPSNFController.hh/.cc` | shared_hint 注入；home-DDR4 路由；BUSY retry；deferred CompData |

### 9.4 Layer 3d：Backend / UBCC

| 文件 | 关键修改 |
|---|---|
| `EPBackend.hh/.cc` | outer envelopes 带 `epoch+reqId`；read/write recall 路径；**invalidate 经 HN-F 修复**；Clear/ClearAck；UpgradeReq/Ack/Done/DoneAck；writeback/evict 在 `GRANT_HANDSHAKE` 期间 BUSY+pin |
| `UBCCController.hh/.cc` | `DirEntry` 精简；`OutstandingRequest` 扩展到 4 opType + intended result + `reservedEpoch`；统一 **reserve-then-commit**；upgrade ack 后 `PERSISTENT_BUSY`；half-range epoch；`ClearAck` tombstone(`W`)；idempotent ack/clear |

### 9.5 Layer 3e：Integration / Verification

| 文件 | 关键修改 |
|---|---|
| `tests/e2e/test_e2e.py` | TC 覆盖映射、日志检查、`enable_self_test=False`、commit-on-Clear / tombstone(`W`) / `PERSISTENT_BUSY` / TC11 ack-before-snoopresp / snoop-slot 检查；proxy special completion 不得留 owner；unreachable snoop audit |
| `tests/e2e/workloads/e2e_common.h` | 保留 spin barrier，但文档标注非 syscall barrier |
| `tests/ubcc/ep-rnf/*.py` | phase structural checks 更新为 v4 目标（`startReadUnique`、invalidate-via-HNF、Clear/ClearAck、snoop-slot、Upgrade four-message handshake、proxy scrub_to_I、unreachable snoop fatal） |

---

## 10. Test Implications

### 10.1 TC 覆盖映射

| TC | 覆盖路径 | 关键检查 |
|---|---|---|
| TC1 | 本地 DSM 读写 | 不触发 outer path |
| TC2 | 首次远端 shared miss | `shared_hint`、reserve-then-commit、`G_I→G_S` commit on Clear |
| TC3 | pingpong | epoch-tagged ordering、write recall、grant-after-barriers、commit-on-Clear、`RecallUnique` callback data 先于 `scrub_to_I` |
| TC4 | 三节点 owner transfer | 多轮 recall 序列化、tombstone duplicate Clear replay |
| TC5 | single writer | write recall + 多 reader 收敛 |
| TC6 | multi-sharer | `G_S→Unique` invalidation fanout、grant 仅在 invalidate `DONE` 后发出、`InvalidateOnly` 完成后 HN-F=I |
| TC7 | writeback/evict | home-DDR4 routing；RECALL/INVALIDATE/UPGRADE/GRANT_HANDSHAKE 并发 BUSY+pin |
| TC8 | upgrade invalidate | **invalidate 必经 HN-F**、最终读到新值 |
| TC9 | negative | 非 DSM / 非法组合拒绝；snoop slot overflow `fatal/panic`；`SnpShared/SnpSharedFwd/SnpOnceFwd` 到 EP-RNF 必须 `fatal/panic` |
| TC10 | concurrent atomic | 不得返回 0 / 撕裂值；同 PA 冲突继续 BUSY/RETRY |
| TC11 | local upgrade | `SnpCleanInvalid → OuterUpgradeReq/Ack → SnpResp_I → OuterUpgradeDone/DoneAck`；Ack(true) 后不可取消 |

### 10.2 推荐新增/增强检查

1. TC3 / TC8 / TC11 日志中强制检查 `[UBCC-ORDER] epoch reqId` 单调，且普通 grant 的 commit 日志必须出现在 matching `Clear` 接受时。
2. 对 invalidate path 增加断言：`sendInvalidationAck()` 之前必须出现 `CleanUnique complete`。
3. 对 Clear/ClearAck 做 idempotent 重发测试，并验证 tombstone 窗口 `W` 内 duplicate `Clear` 返回 identical `ClearAck`。
4. 对 `W` 过期后的 stale `Clear` 做测试：记录日志、无响应、不得重写目录。
5. 对 UpgradeReq/Ack/Done/DoneAck 做 duplicate + timeout 测试，并验证 Ack(true) 后只能进入 `DONE` 或 `PERSISTENT_BUSY`。
6. 对 writeback/evict 并发测试：覆盖 `RECALL` / `INVALIDATE` / `UPGRADE_PENDING` / `GRANT_HANDSHAKE` 四类 outstanding，检查 BUSY+pin。
7. 对 EP-RNF same-PA snoop serialization 测试：一个 queued snoop 正常执行，第二个 queued snoop 触发 `fatal/panic`。
8. 对 wrap 逻辑做单元测试：`epoch=2^64-2,2^64-1,0,1`。
9. 对 same-tick 请求/返回环境，验证 `epsnf_deferred_compdata_enabled=true`。
10. 对 EP-RNF proxy special completion 加检查：`Comp_UC` 只是 token，HN-F 最终必须为 `I`，再次访问必须重新经 UBCC 授权。
11. 对 `SnpShared` / `SnpSharedFwd` / `SnpOnceFwd` 注入负测：到达 EP-RNF 必须 `fatal/panic` + audit。
12. debug 构建下检查 no-self-recall 断言：`assert(!(need_dirty_owner && candidates_excluding_requester.empty()))`。

### 10.3 关于 Q39

无强制偏好。建议：

- **优先复用 TC3 / TC8 / TC11** 做 order log 检查；
- 若需要稳定复现，再单独加 `TC12_order_audit`。

---

## 11. Known Hazards & Mitigations

| Hazard | 描述 | v4 方案 | 状态 |
|---|---|---|---|
| TBE race | req/data 同 tick 导致 reservation 失衡 | **I10 强制化**：结构性 ≥1 tick 或 EP-SNF deferred CompData 强制开启 | RESOLVED |
| invalidation bypass HN-F | 当前基线直接 ack，失去 grant/invalidate 序列化 | **改为 `startCleanUnique()` 完成后再 ack** | RESOLVED |
| stale recall / ack | 旧响应误写新状态 | 全部 outer response 校验 `(pa, epoch, reqId)` | RESOLVED |
| pendingOwnerUpdate leak | barrier 永不释放 | 统一改为 `OutstandingRequest + Clear/ClearAck/UpgradeDoneAck` | RESOLVED |
| GRANT_HANDSHAKE leak | handshake outstanding 永不退场或 duplicate Clear 不可重放 | `ClearReason::GrantHandshake` 显式 retirement + tombstone(`W`) | RESOLVED |
| normal miss eager-commit | grant 发出即改 committed 目录，导致实现与确认修正冲突 | **全路径统一 reserve-then-commit；普通 miss 仅在 Clear 被接受后提交** | RESOLVED |
| local upgrade early-SnpResp | EP-RNF 过早回 `SnpResp_I`，home 尚未序列化 upgrade | **四消息握手：UpgradeReq → UpgradeAck → SnpResp_I → UpgradeDone → UpgradeDoneAck** | RESOLVED |
| upgrade cancel-after-ack | Ack(true) 后回退旧目录或取消 upgrade | Ack(true) 后进入 **irrevocable-after-ack**；超时只允许重发 Ack / 接受 Done / `PERSISTENT_BUSY` | RESOLVED |
| grant-before-barriers | recall/invalidate 未完成就向 requester 发 grant | `I12`：grant-after-barriers；barrier 只释放资格，不构成 commit | RESOLVED |
| snoop reentrancy on EP-RNF | 同 PA inflight 期间再次来 snoop 无规范队列 | EP-RNF 引入 **1-entry per-PA snoop slot**；overflow 直接 `fatal/panic` | RESOLVED |
| DCT wrong-target | EP-RNF 成为 Fwd target | `pickSharerForSnoop()` + sole-EP-RNF 时强制 non-DCT snoop fallback | RESOLVED |
| zero-fill grant heuristic | 用内容判断数据有效 | 禁止；使用 recall data buffer 绑定 tuple | RESOLVED |
| home-DDR4 routing bug | 写回落到本地 DDR4 | EP-SNF 必须根据 home node 路由 | RESOLVED |
| writeback/evict races with outstanding | writeback/evict 抢跑目录更新或提前释放 line | `RECALL/INVALIDATE/UPGRADE_PENDING/GRANT_HANDSHAKE` 期间统一 BUSY/RETRY + pin | RESOLVED |
| HN-F design drift overreach | 从未列状态推导出新的 EP 特化行为 | `§4.5.3` 边界声明：未列项保持 baseline gem5 CHI | RESOLVED |
| duplicate same-PA requests | 合并/覆盖导致乱序 | 统一 BUSY/RETRY，不 merge | RESOLVED |
| proxy unique-flow completion ambiguity | 文档曾把 `ReadUnique/CleanUnique` 代理完成误写为“EP-RNF 获得本地 owner 语义” | `§4.5.3/§4.5.4` 改为 baseline prefix + local `scrub_to_I`；`Comp_UC` 仅为 token | RESOLVED |
| preserving-query snoop routed to EP-RNF | `SnpShared/SnpSharedFwd/SnpOnceFwd` 若误入 EP-RNF 会破坏 sharer 语义 | 当前设计标记为 unreachable；到达即 `fatal/panic` + audit | RESOLVED |
| no-self-recall empty-candidate over-fatal | 不可达分支被当作 release fatal 路径 | 降级为 debug-only assertion；release build 无需额外分支 | RESOLVED |

---

## 12. Per-file Implementation Map

> 说明：以下“当前基线锚点”均指当前工作树行号附近；实现时按语义定位，允许少量行号漂移。

| 文件 | 当前基线锚点 | 当前问题 / 基线状态 | 需要的 v4 修改 | 依赖 |
|---|---|---|---|---|
| `gem5/configs/ruby/CHI_ubcc_framework.py` | 110-125, 224-261, 287-319 | `configure_l3_dsm_policy()` 当前关闭 DSM L3；deadlock 已固定 | 改为 `alloc_on_readshared=true`, `alloc_on_readunique=true`, `alloc_on_readonce=false`；注入 `epRnfMachineVersion`；保留 node isolation | `CHI-cache.sm` |
| `gem5/configs/ruby/CHI_config.py` | 324-347 | HN-F 默认 `alloc_on_readunique=false` | HN-F 默认值改为 true，避免 framework/默认值冲突 | 无 |
| `gem5/src/mem/ruby/protocol/chi/CHI-cache.sm` | 167-171 | 只有 `epRnfMachineVersion` 参数，无完整 TBE 支撑说明 | 在 TBE 增加 `epRnfMachineID` 字段；初始化链路可达 | `CHI-cache-funcs.sm` |
| `gem5/src/mem/ruby/protocol/chi/CHI-msg.sm` | 232-235 | 已有 `m_shared_hint` 字段，但无 proxy flow completion 区分 | 保留 `m_shared_hint`；新增 internal `EpProxyOp`，仅供 gem5 内部 special completion 判定 | `EPRNFController.cc`, `CHI-cache-actions.sm` |
| `gem5/src/mem/ruby/protocol/chi/CHI-cache-funcs.sm` | 387-418, 657-709 | 有 `initializeTBE()` 与 sideband helper，但无 `pickSharerForSnoop()` | 注入 `epRnfMachineID`；新增 `pickSharerForSnoop()`；维持 sideband 映射 | `CHI-cache-actions.sm` |
| `gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm` | 490-516, 682-704, 1894-2113, 2582-2829 | 仍使用裸 `smallestElement()`；无 EP-RNF 注册 action；DCT fallback 不完整；proxy unique-flow completion 仍沿 baseline owner-transfer 语义 | 4 个单目标 snoop 改用 `pickSharerForSnoop()`；加入 `RegisterEPRNF_OnSharedHint`；sole EP-RNF 时强制 non-DCT snoop；`Send_CompData` 对 EP-RNF 固定 clean-shared 语义；`UpdateDirState_FromReqResp` guard；proxy `InvalidateOnly/RecallUnique` 完成时执行 `scrub_to_I` | `CHI-cache-funcs.sm`, `CHI-cache-transitions.sm`, `CHI-msg.sm` |
| `gem5/src/mem/ruby/protocol/chi/CHI-cache-transitions.sm` | 1547-1562 | 有 CompAck 容错消费 | 增加 shared_hint 注册触发；维持 CompAck 保护；local upgrade transient 需要等待 EP-RNF `SnpResp_I`；proxy special completion 必须落到 `I` | `CHI-cache-actions.sm` |
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.py` | 7-13 | 缺少 `enable_self_test` | 增加 `enable_self_test = Param.Bool(True, ...)` | `tests/e2e/test_e2e.py` |
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh` | 72-152, 154-172, 226-347, 462-482 | outer 消息无 `reqId` / Clear / Upgrade；grant data 注释仍围绕 phys_mem | 所有 outer envelope 加 `reqId`；新增 `OuterClearMsg/OuterClearAckMsg` 与 `OuterUpgradeReq/Ack/Done/DoneAck`；RequesterLineEntry 增加 `reqId`；文档化 “grant data 绑定 epoch+reqId / commit on Clear” | `EPBackend.cc`, `UBCCController.hh` |
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc` | 342-347, 365-428, 430-493, 545-564, 1025-1185, 1421-1472 | recall 仍混有 functionalRead 思路；invalidate 直接 ack（**核心 bug**）；grant data 走 materialized/phys_mem 旧路径；local upgrade 仅旧通知骨架 | read recall → `startReadShared()`；write recall → `startReadUnique(..., EpProxyOp::RecallUnique)`；invalidate → `startCleanUnique(..., EpProxyOp::InvalidateOnly)` callback 后 ack；引入 Clear/ClearAck；实现 UpgradeReq/Ack/Done/DoneAck；outstanding 冲突时 writeback/evict **含 `GRANT_HANDSHAKE`** 均 BUSY+pin | `EPRNFController.cc`, `UBCCController.cc` |
| `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.hh` | 221-345 | 只有 `ReadShared/CleanUnique/ReadOnce`；无 `ReadUnique`；PendingChiTxn 未带 epoch/reqId/beat counters/snoop-slot/proxyOp | 增 `startReadUnique()`；移除 `ReadOnce` 主路径；PendingChiTxn 加 `(epoch, reqId, proxyOp, beatsExpected, beatsReceived, snoopSlot, callbackPayloadStable)`；retry strongest-op；UpgradeAck gating state | `EPRNFController.cc`, `CHI-msg.sm` |
| `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc` | 307-361, 365-499, 568-820 | snoop 只做立即/延迟 `SnpResp_I`；无完整 matrix；无 `ReadUnique`；当前 CompAck/response 生命周期过弱；preserving-query snoop 未显式拒绝 | 完整实现 SnpUnique/SnpOnce/SnpCleanInvalid matrix；新增 `startReadUnique()`；per-PA 单 inflight + strongest retry；**1-entry snoop slot + overflow fatal**；multi-beat 完整收集；local upgrade 时先等 `OuterUpgradeAck` 再 `SnpResp_I`；`SnpShared/SnpSharedFwd/SnpOnceFwd` 到达即 fatal；proxy completion 回调前稳定 payload | `EPBackend.cc`, `CHI-cache-actions.sm` |
| `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.hh` | 41-54 | 有 retry / deferred data 骨架 | 保持；补充 `DeferredGrantEntry` 语义 | `EPSNFController.cc` |
| `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc` | 65-107, 120-291, 325-380 | sideband 已读；CompData 类型固定 UC；NCBWrData 当前写 local phys_mem | shared grant 发送 `m_shared_hint=true`；CompData 类型按 grant 生成；NCBWrData 路由到 home node DDR4；deferred send 不做 epoch 复检；same-tick 时强制 deferred | `EPBackend.cc` |
| `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh` | 44-92, 124-145, 330-399 | 仍混有 `pendingOp` / `materializedData`；Outstanding 仅 3 opType 且无 `reqId` | 精简 DirEntry；Outstanding 扩展为 4 opType + `baseEpoch/reservedEpoch/intendedState/intendedOwner/intendedSharers/targetMask/dataBuf/deadline`；增加 tombstone(`W`) 容器与 `PERSISTENT_BUSY` 支撑 | `UBCCController.cc` |
| `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc` | 114-480, 571-697, 734-854, 878-885, 1082-1098, 1140-1169 | 当前是 deferred-commit；epoch 仅等值检查；一个 PA 一个 outstanding；handshake/clear 语义不完整；local upgrade 旧 barrier 命名与行为不对 | 改为全路径统一 **reserve-then-commit**；普通 miss commit on Clear；half-range epoch；outer response 全部校验 `(epoch, reqId)`；INVALIDATE/RECALL/GRANT_HANDSHAKE/UPGRADE_PENDING 分离；Ack(true) 后不可取消；tombstone(`W`)；日志审计 | `EPBackend.cc` |
| `tests/e2e/test_e2e.py` | 21-33, 82-257, 701-703 | 已有 TC registry 与 verifier；E2E 关闭 self-test | 增加 epoch/reqId order log 检查；增加 commit-on-Clear / tombstone(`W`) / snoop-slot / `PERSISTENT_BUSY` 检查；增加 TC11 `UpgradeAck before SnpResp_I` 检查；增加 outstanding BUSY+pin 断言；增加 proxy special completion 不得留 owner/sharer；保持 `enable_self_test=False` | `EPBackend.py` |
| `tests/e2e/workloads/e2e_common.h` | 39-54 | 当前 `sync_wait` 是 spin barrier，不是 syscall barrier | 保持实现，但在方案中明确其语义仅为“推进 + 可见性等待” | 无 |
| `tests/ubcc/ep-rnf/test_phase1_tc3.py` 等 | 结构检查脚本 | 已面向 v3/v4 语义 | 更新检查点为 `startReadUnique`、invalidate-via-HNF、`Clear/ClearAck`、`tombstone(W)`、1-entry snoop-slot、Upgrade four-message handshake、proxy completion `scrub_to_I`、unreachable snoop fatal | 上述实现文件 |

---

## 13. State Analyzer Issue Closure Map

| Issue | gap 描述 | 已加入的规范文本 | 插入 section |
|---|---|---|---|
| H1 | `OutstandingRequest` 缺完整 opType→stage→event→next_stage | `§4.1.5` 增加完整规范状态机表，覆盖 `RECALL/INVALIDATE/GRANT_HANDSHAKE/UPGRADE_PENDING`、duplicate、timeout、terminal states | `§4.1.5` |
| H2 | HN-F transient states 未形式化可验证 | `§4.5.3` 新增 EP-touching HN-F 状态/事件表，并加边界声明：未列项一律维持 baseline gem5 CHI | `§4.5.3` |
| H3 | sole-EP-RNF fallback 同时提到 non-DCT snoop 与 ReadNoSnp path | `§4.5.5` 选定唯一规范：**force `use_DCT=false` + 走 baseline non-DCT snoop path**，明确禁止切到 `ReadNoSnp` path | `§4.5.5` |
| M1 | TBE race 缓解只是建议，不是强制约束 | `§8.1 I10` 与 `§8.2` 把 “≥1 tick separation OR deferred CompData mandatory” 提升为强制 invariant | `§8.1`, `§8.2`, `§11` |
| M2 | UBCC matrix 漏 self-owner 与 writeback/evict 并发 | `§4.6.2` 增加 edge matrix；`§4.2.3`、`§5.6` 规定 outstanding 存在时 writeback/evict 必须 BUSY 并本地 pin line | `§4.2.3`, `§4.6.2`, `§5.6` |
| F1 | 普通 miss / upgrade 提交模型不统一 | `§2.2`、`§4.1.3` 定义统一 reserve-then-commit；`processOuterRequest()` 不得修改 committed `DirEntry` | `§2.2`, `§4.1.3` |
| F2 | `UPGRADE_PENDING` 在 Ack(true) 后仍可能 timeout-cancel | `§4.1.4`、`§4.1.5` 引入 irrevocable-after-ack；timeout 终态改为 `PERSISTENT_BUSY` | `§4.1.4`, `§4.1.5`, `§8.1 I11` |
| F3 | 普通 grant commit point 误写为 grant emission | `§3.3`、`§3.5`、`§4.6.1` 统一改为 **commit on matching Clear** | `§3.3`, `§3.5`, `§4.6.1` |
| F4 | EP-RNF 同 PA snoop reentry 未明确队列上限 | `§4.3.3`、`§4.3.4` 规定 1-entry per-PA snoop slot；overflow `fatal/panic` | `§4.3.3`, `§4.3.4`, `§8.1 I15` |
| F5 | `GRANT_HANDSHAKE` 完成后 duplicate Clear 无 replay 规范 | `§3.5`、`§7.2` 定义 tombstone 窗口 `W` 与 identical `ClearAck` replay | `§3.5`, `§7.2`, `§8.1 I14` |
| F6 | `Writeback/Evict × GRANT_HANDSHAKE` 缺显式规则 | `§4.2.3`、`§4.6.2`、`§5.6` 规定旧 owner/sharer 与新 requester 均 BUSY/RETRY + pin，待 Clear 退休后再处理 | `§4.2.3`, `§4.6.2`, `§5.6` |
| 2B | no-self-recall generalization 曾保留 release fatal 分支 | `§4.1.3` 改为 unreachable + debug-only assert；`§8.2` 增加断言；release build 不需要额外分支 | `§4.1.3`, `§8.2` |
| 7 | HN-F 缺少最小 EP-touching 设计附录 | 新增 Appendix A，列出 EP-touching baseline subset 与免责声明 | `Appendix A` |
| 8 | EP-SNF 缺少最小状态机设计附录 | 新增 Appendix B，给出最小状态/事件表与不变量边界 | `Appendix B` |
| 9 | EP-RNF proxy unique-flow completion 仍残留“EP-RNF 获得 owner 语义”的错误表述 | `§4.5.3/§4.5.4` 改为 special completion `scrub_to_I`；`§8` 增加 I16-I22 与断言；`§11` 关闭相关 hazard | `§4.5.3`, `§4.5.4`, `§8`, `§11` |

### 13.1 面向实施者的直接增补文本

1. **H1 直接增补文本**：实现不得以“若干散落 if/else”替代表 4.1.5 的规范状态机；若内部状态更多，必须可映射回该表。  
2. **H2 直接增补文本**：凡 EP 触达的 HN-F 事件，必须能在 `§4.5.3` 表中找到唯一落点；找不到即视为实现缺口；未列项一律继承 baseline。  
3. **H3 直接增补文本**：sole-EP-RNF 时 fallback 的“单位”是 **snoop initiator**，不是 miss path。  
4. **M1 直接增补文本**：若仿真配置允许 0-tick 返回而又关闭 deferred CompData，则该配置 **不符合本方案**。  
5. **M2 直接增补文本**：self-owner re-read/re-write 必须幂等；outstanding 期间 writeback/evict 必须 BUSY，不允许偷偷完成目录更新。  
6. **F1/F3 直接增补文本**：普通 miss 的 grant 决策不是 commit；只有 matching `Clear` 被 home 接受，intended 结果才成为 committed `DirEntry`。  
7. **F2 直接增补文本**：`OuterUpgradeAck(true)` 一旦发出，`UPGRADE_PENDING` 只能 `DONE` 或 `PERSISTENT_BUSY`，不得再走 `CANCELLED`。  
8. **F4 直接增补文本**：EP-RNF 对同 PA 只允许 1 个 queued snoop；第二个 snoop 是协议错误，不是性能优化机会。  
9. **F5/F6 直接增补文本**：`GRANT_HANDSHAKE` 完成后需留 tombstone(`W`) 供 duplicate `Clear` replay；在 `GRANT_HANDSHAKE` 活跃期间，任何 writeback/evict 都必须 BUSY+pin。  
10. **2B 直接增补文本**：`need_dirty_owner && candidates_excluding_requester.empty()` 是 debug-only 断言，不是 release 行为分支。  
11. **7 直接增补文本**：Appendix A 只是 HN-F EP-touching baseline subset 设计参考；未列状态/事件一律保持 baseline gem5 CHI。  
12. **8 直接增补文本**：Appendix B 是 EP-SNF 最小状态机设计参考；实现可细化映射，但不得破坏 `§8` 全局不变量。  
13. **9 直接增补文本**：EP-RNF proxy `CleanUnique/ReadUnique` 的 `Comp_UC` 只表示“本地完成”，绝不表示“权限授予”；完成后必须 `scrub_to_I`。  

---

## 14. Final Normative Summary

1. **EP 是外部边界；UBCC 是全局排序点；HN-F 不是全局权威。**
2. **所有 remote miss 与 local upgrade 统一采用 reserve-then-commit；`processOuterRequest()` 只建 outstanding + intended result，绝不直接改 committed `DirEntry`。**
3. **普通 miss 的 commit 点固定为 home 接受 matching `Clear`；local upgrade 的 commit 点固定为 home 接受 matching `OuterUpgradeDone`。**
4. **`epoch` 为每 DirEntry 64-bit committed 值；新 epoch 先作为 `reservedEpoch` 存在 outstanding 中。**
5. **`reqId` 为每 OutstandingRequest 独立编号；`epoch/reqId` 只在 outer 层存在；对外可见 CHI sideband 只允许 `ubcc_needed_perm`、`ubcc_write_intent`、`m_shared_hint`，另有 gem5 internal-only 的 `ep_proxy_op`。**
6. **同 PA 并发冲突统一 BUSY/RETRY；不 merge。**
7. **EP-RNF 对同 PA 只允许一个 inflight CHI 请求；retry 队列同 epoch 保留最强 op；同 PA 最多 1 个 queued snoop，overflow 直接 `fatal/panic`。**
8. **read recall 必须 `ReadShared`；write recall 必须 `ReadUnique`；sharer invalidation 必须 `CleanUnique`。**
9. **禁止用 `ReadOnce` 作为 recall 主路径。**
10. **禁止任何 invalidate/recall 绕过 HN-F。**
11. **`EPBackend::handleInvalidationRequest()` 必须先 `startCleanUnique()`，后 `sendInvalidationAck()`。**
12. **带 `RECALL/INVALIDATE` prerequisite 的普通 grant，必须在 barrier `DONE` 后才能发 grant；barrier ack 只释放资格，不构成 commit。**
13. **Clear/ClearAck 是普通 miss 的 commit/retire 协议；`GRANT_HANDSHAKE` 完成后必须保留 tombstone 窗口 `W`，供 duplicate `Clear` replay identical `ClearAck`。**
14. **local upgrade 必须使用 `UPGRADE_PENDING` + `OuterUpgradeReq/Ack/Done/DoneAck` 四消息握手。**
15. **`SnpResp_I` for local upgrade 必须晚于 `OuterUpgradeAck(true)`。**
16. **一旦 `OuterUpgradeAck(true)` 发出，`UPGRADE_PENDING` 进入 irrevocable-after-ack：不得取消，只能 `DONE` 或 `PERSISTENT_BUSY`。**
17. **sole-EP-RNF 的 DCT fallback 唯一允许形式是 `use_DCT=false` 后继续走 non-DCT snoop path。**
18. **HN-F 未列出的状态/事件/转移全部保持 baseline gem5 CHI；不得从未列项推导新的 EP 语义。**
19. **TBE race 规避必须满足：request/response ≥1 tick，或 deferred CompData 强制开启。**
20. **self-owner re-read/re-write 必须幂等；`RECALL/INVALIDATE/UPGRADE_PENDING/GRANT_HANDSHAKE` 期间 writeback/evict 必须 BUSY 并本地 pin line。**
21. **所有关键路径必须输出带 `epoch+reqId` 的 order log，供 TC3/TC8/TC11 审计。**
22. **EP-RNF proxy invalidation / recall-unique 采用 baseline prefix + special completion `scrub_to_I`；完成后 HN-F 必须收敛到 `I`，不得给 EP-RNF 留下任何本地 owner 语义。**
23. **`Comp_UC` 在 proxy special completion 中只是 non-authoritative token；权限由目录状态决定，不由消息类型决定。**
24. **`SnpShared` / `SnpSharedFwd` / `SnpOnceFwd` 对 EP-RNF 当前均为 unreachable；到达即 `fatal/panic` + audit。**
25. **no-self-recall 空候选分支是 debug-only assertion，不是 release fallback。**
26. **Appendix A/B 是最小设计参考；实现可按 gem5 baseline 展开细节，但不得破坏本文件不变量。**

---

**实施优先级**：先修 `EPBackend invalidation via HN-F` 与 `local upgrade four-message handshake + irrevocable-after-ack`，再落 `UBCC reserve-then-commit + commit-on-Clear + tombstone(W) + epoch/reqId`，随后补 `EPRNF snoop-slot + writeback/evict BUSY+pin`，最后收口 HN-F shared_hint / pickSharer / DCT fallback 与测试审计链。

---

## Appendix A：HN-F EP-touching Baseline Subset（Design Reference）

**免责声明**：**未列出的状态/事件保持 baseline gem5 CHI 行为不变。本附录不替代完整 CHI spec。**

### A.1 状态子集

- 稳态：`I`、`SC`、`UC`、`UD`、`SD`
- 暂态：`RSC`、`SC_RU`、`UC_RSC`、`UD_RSC`、`UC_RU`、`UD_RU`、`SD_RSC`、`SD_RU`

### A.2 EP-touching 事件子集

- `CompData_SC(shared_hint)`
- `EP-RNF.ReadShared`
- `EP-RNF.ReadUnique`
- local upgrade with `EP-RNF in dir_sharers`
- sole-EP-RNF DCT fallback

### A.3 最小参考表

| 事件 | 触达状态 | 参考行为 |
|---|---|---|
| `CompData_SC(shared_hint)` | `I → RSC → SC` | 注册 EP-RNF 为 sharer，shared fill 保持 clean-shared 语义 |
| `EP-RNF.ReadShared` | `SC/UC/UD/SD` | 按 baseline shared handling，落入 `SC_RSC/UC_RSC/UD_RSC/SD_RSC` 等已有路径 |
| `EP-RNF.ReadUnique(RecallUnique)` | `UC/UD` | prefix 保持 baseline `SnpUnique`/dirty collect；completion 改为本地 `scrub_to_I` |
| `EP-RNF.CleanUnique(InvalidateOnly)` | `SC/SD` | prefix 保持 baseline sharer invalidation；completion 改为本地 `scrub_to_I` |
| local upgrade with EP-RNF sharer | `SC → SC_RU → UC/UD` | 必须等待 `OuterUpgradeAck(true)` 后，EP-RNF 才能回 `SnpResp_I` |
| sole-EP-RNF DCT fallback | 任意相关发起点 | 强制 `use_DCT=false`，退回 baseline non-DCT snoop initiator |

## Appendix B：EP-SNF Minimal State Machine（Design Reference）

**免责声明**：**本附录是设计参考。实现者可按 gem5 baseline 细化状态/事件映射，但不得破坏 §8 不变量。**

### B.1 状态集合

- `S_IDLE`
- `S_WAIT_OUTER`
- `S_DEFER_GRANT`
- `S_WAIT_COMPACK`
- `S_WAIT_RETRY`

### B.2 事件表

| 当前状态 | 事件 | 动作 | 下一状态 |
|---|---|---|---|
| `S_IDLE` | 收到 `ReadNoSnp/WriteNoSnp` 且地址属于 remote DSM | 解析 `ubcc_needed_perm/ubcc_write_intent`，调用 `EPBackend.handleRemoteMiss()` | `S_WAIT_OUTER` |
| `S_IDLE` | 收到非 DSM 或非法组合请求 | 拒绝/报错 | `S_IDLE` |
| `S_WAIT_OUTER` | outer grant 到达，且系统要求 deferred send | 缓存 `DeferredGrantEntry` | `S_DEFER_GRANT` |
| `S_WAIT_OUTER` | outer grant 到达，可立即安全发送 | 组装 `CompData_*`，必要时注入 `m_shared_hint` | `S_WAIT_COMPACK` |
| `S_WAIT_OUTER` | home 返回 BUSY/RETRY | 安排 retry/backoff | `S_WAIT_RETRY` |
| `S_DEFER_GRANT` | defer tick 到达 | 发送 `CompData_SC/UC/UD`；shared grant 必须带 `m_shared_hint=true` | `S_WAIT_COMPACK` |
| `S_DEFER_GRANT` | grant 被取消或本地检查失败 | 丢弃 deferred entry 并上报错误 | `S_IDLE` |
| `S_WAIT_COMPACK` | 收到 `CompAck` 或本地完成条件满足 | 退休 grant bookkeeping | `S_IDLE` |
| `S_WAIT_COMPACK` | same-PA 新请求到达 | 返回 BUSY/RETRY 或由上层排队 | `S_WAIT_COMPACK` |
| `S_WAIT_RETRY` | retry timer 到达 | 重发 outer miss | `S_WAIT_OUTER` |
| `S_WAIT_RETRY` | 请求被取消 | 清理本地状态 | `S_IDLE` |

### B.3 最小设计约束

1. shared grant 必须映射为 `CompData_SC + m_shared_hint=true`。
2. deferred `CompData` 只解决 timing / TBE race，不承担 epoch 重判。
3. `NCBWrData` 必须路由到 home node DDR4。
4. 任一状态细化都不得破坏 `§8` 的 commit-on-Clear、ack-before-snoopresp、grant-after-barriers 等不变量。
