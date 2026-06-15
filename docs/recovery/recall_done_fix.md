# RECALL.DONE requester-private 修复规范

**状态**：定稿（针对 `RECALL.DONE` 被错误跨 requester 消费的问题）  
**适用基线**：`docs/recovery/scheme_v4.md`  
**本文件目标**：把用户已确认的 Q1/Q2/Q3/Q4 决策落成可实现的 UBCC 级规范。

---

## 1. 结论摘要

本修复采用以下四条规范性结论：

1. **`RECALL.DONE` 是 requester-private。** 只能被创建它的原 requester 消费；其他 requester 不得借用该 DONE recall 直接拿 grant。
2. **冲突 requester 不丢弃，改为排队。** Node1 先完成自己的 `Clear` 提交；Node2 仅排队，不能插队，也不能消费 Node1 的 DONE recall。
3. **只允许 `RS` 合并 `RS`。** 任意 `RU`/write-intent 必须等待当前 grant commit 后再重新仲裁。
4. **每个 PA 仅允许 1 个 live commit object。** live object 仍是 `RECALL / INVALIDATE / GRANT_HANDSHAKE / UPGRADE_PENDING` 之一；排队请求放到独立 `pendingRequesters` sidecar 中。

---

## 2. 修复目标

修复以下错误路径：

```text
Node1: RU -> RECALL(owner) -> RECALL.DONE -> waiting Node1 grant/clear
Node2: RS/RU arrives before Node1 Clear
```

错误行为（禁止）：

- Node2 看到 `RECALL.DONE` 后直接转 `GRANT_HANDSHAKE`
- Node2 借用 Node1 的 recall 结果
- Node2 把 Node1 挤掉，导致 Node1 的 `Clear` tuple 无法 commit

正确行为（本规范）：

- Node2 只能进入 `pendingRequesters` 队列
- Node1 仍是 head requester
- 只有 Node1 的 `Clear` 提交后，Node2 才能被 replay

---

## 3. `pendingRequesters` 数据结构

## 3.1 存储位置

**必须存放在 `UBCCController`，按 PA 建 sidecar queue；不得放进 committed `DirEntry`。**

原因：

- `DirEntry` 只表示 committed truth；
- `pendingRequesters` 是纯瞬态调度状态；
- queue 生命周期应跟随 live outstanding，而不是污染目录真值。

推荐形态：

```cpp
struct PendingRequesterAtom {
    int requesterNode;
    UBCC_OuterReqType reqType;   // RS / RU
    bool writeIntent;            // 仅对 RU 有意义
    uint64_t observedEpoch;      // 初次到达时 requester 携带的 epoch
    uint64_t reqId;              // requester 分配，重试必须复用
    Tick enqueueTick;            // 用于公平性/超时审计
};

struct PendingRequesterBucket {
    // 仅 RS merge 使用；RU bucket 永远只有 1 个 atom
    std::deque<PendingRequesterAtom> atoms;
    bool rsMergeBucket;          // true => bucket 内全是 RS
};

std::map<uint64_t, std::deque<PendingRequesterBucket>> _pendingRequestersByPa;
unsigned _maxPendingRequestersPerPa = 4;   // 默认 4，建议做成参数
```

## 3.2 为什么不是“每 bucket 一个 requester”

因为 Q3=C 要求 **RS 可合并 RS**。因此队列元素不能只是一条单 requester 记录；至少要允许一个 RS bucket 挂多个 `PendingRequesterAtom`。

## 3.3 深度限制

- **默认深度：4 个 logical requester / PA**；
- 应做成可配置参数，如 `ubccPendingRequestersPerPa`；
- bucket 合并后，**深度按 atom 数量计数，不按 bucket 数量计数**；
- 满了以后，新的冲突请求返回 `BUSY/RETRY`，但**不入队**。

在当前 3 节点 DSM 下，4 已足够；把它做成参数是为了避免未来扩展时再次改 ABI。

## 3.4 队列不变量

对任一 PA，必须满足：

1. `_outstandingReqs[linePa]` 最多 1 个 live object；
2. `pendingRequestersByPa[linePa]` 可以有 0..N 个 queued atom；
3. queued atom **不是** live grant/commit object；
4. live object 属于某 requester 时，其他 requester 只能排队，不能消费其 barrier；
5. `RECALL.DONE` 若存在，其 owner 是 `OutstandingRequest.requesterNode`，不是“全体后来者共享”。

---

## 4. `processOuterRequest()` 修正规范

## 4.1 总原则

当同一 PA 已存在 live outstanding，且该 outstanding 对应的 requester 不是当前 requester 时：

- **不允许**直接从 `RECALL.DONE` 或 `GRANT_HANDSHAKE` 派生当前 requester 的 grant；
- **允许**把当前 requester 放入 `pendingRequesters`；
- 返回值仍为 `BUSY/RETRY`（当前接口即 `-1`），但这是“已排队 BUSY”，不是“直接丢弃 BUSY”。

## 4.2 必须区分三种情况

### 情况 A：`RECALL.DONE` 且 `requester == originalRequester`

保持 v4 语义：

- 允许 `RECALL.DONE -> GRANT_HANDSHAKE`
- 使用该 requester 自己的 tuple 创建新的 `GRANT_HANDSHAKE`
- 这是唯一合法的 DONE recall 消费者

### 情况 B：`RECALL.DONE` 且 `requester != originalRequester`

这是本次修复的核心：

1. 不得把 DONE recall 原地转给新 requester；
2. 调用 `enqueueOrMergePendingRequester()`；
3. 若队列有空间：入队并返回 `BUSY/RETRY`；
4. 若队列已满：直接返回 `BUSY/RETRY`，但不入队；
5. 原 `RECALL.DONE` 保持不变，等待原 requester 自己消费。

### 情况 C：`GRANT_HANDSHAKE/INVALIDATE/UPGRADE_PENDING/WAITING_RECALL` 活跃，且 `requester != activeRequester`

处理规则与情况 B 相同：

- 若可排队则入队；
- 否则 BUSY；
- 绝不创建第二个 live commit object。

## 4.3 入队/合并规则

### 4.3.1 普通入队

若队尾不是可合并 RS bucket，则追加新 bucket：

```cpp
bucket.atoms = [{requesterNode, reqType, writeIntent, observedEpoch, reqId, now}]
bucket.rsMergeBucket = (reqType == RS)
```

### 4.3.2 `RS merge RS`

若满足以下条件，则**合并到最后一个 RS bucket**：

1. 新请求是 `RS`
2. 队尾 bucket 是 `rsMergeBucket=true`
3. 当前 live head 尚未 commit（即“before the first Clear”）

合并后：

- 只增加一个 queued atom；
- **不创建新的 live `GRANT_HANDSHAKE`**；
- `RU` 永不合并到 RS bucket。

## 4.4 duplicate retry 规则

同一 requester 对同一 PA 的 retry 很容易造成重复排队，因此必须加入去重：

- 若队列中已存在同 `(requesterNode, reqId)` atom：直接返回 `BUSY/RETRY`，**不得重复入队**；
- 若同 requester 已排队但换了新 `reqId`：也不得允许第二条逻辑请求并存；返回 `BUSY/RETRY` 并打审计日志；
- 即：**每 requester/PA 同时最多 1 个 queued logical request**。

## 4.5 审计日志

必须新增：

```text
[UBCC-QUEUE] pa=<pa> action=<enqueue|merge|drop_full|dup_retry>
requester=<n> reqType=<RS|RU> writeIntent=<0|1> reqId=<id> depth=<d>
```

---

## 5. `processClear()` 修正规范

## 5.1 基本顺序

`processClear()` 在接受 head requester 的匹配 `Clear` 后，必须按以下顺序执行：

1. 校验 `(srcNode, epoch, reqId)` 与 live `GRANT_HANDSHAKE` 匹配；
2. 提交 `commitIntendedResult()`；
3. 把该 handshake 退休为 tombstone；
4. 删除 live outstanding；
5. 检查 `pendingRequestersByPa[linePa]`；
6. 若非空，**按 FIFO 取 head bucket 进行 replay**；
7. replay 必须基于 **刚刚 commit 后的新 committed state**。

## 5.2 replay 语义

replay 不是“复用旧 outstanding”，而是：

> 把 queued requester 当成一条全新的 `processOuterRequest()` 输入，再走一次完整仲裁；唯一差别是它的公平位置已被锁定，不能再被后来者插队。

推荐 helper：

```cpp
void replayNextPendingRequester(uint64_t linePa);
```

内部步骤：

1. 查看 committed `DirEntry`（此时已是 Clear 后的新状态）；
2. 从 head bucket 取第一个 atom；
3. **rebase 该 atom 的 `baseEpoch = entry.epoch`**；
4. 使用原 `reqId`、原 `requesterNode`、原 `reqType/writeIntent` 调 `processOuterRequest()`；
5. 若 replay 成功创建 live object，则该 atom 从 queue 中移除；
6. 若 bucket 清空，则弹出 bucket。

### 关键说明：为什么要 rebase epoch

排队请求最初到达时看到的是旧 committed epoch；但它真正被服务时，head grant 已经 commit，目录 epoch 已前进。  
因此 replay 时必须把 queued request 的 `baseEpoch` 重新绑定到**当前 committed epoch**，否则后续 `Clear` 会与新的 live `GRANT_HANDSHAKE` tuple 不匹配。

原始 `observedEpoch` 仍可保留作审计，但**不能**再用作 replay 后 live grant 的 Clear tuple。

## 5.3 replay 的状态结果

### 5.3.1 `RS after RS`

若 Node1 Clear 提交后 committed state 是 `G_S`，而下一条 queued 请求是 `RS`：

- 直接创建新的 `GRANT_HANDSHAKE`
- `intendedState = G_S`
- `intendedSharersMask = committedSharers | (1 << requester)`
- **不需要 recall / invalidation**

### 5.3.2 `RU after RS`

若 Node1 Clear 提交后 committed state 是 `G_S`，而下一条 queued 请求是 `RU`：

- 创建 `INVALIDATE`（目标为当前 committed sharers，排除 requester 自身）
- 同时记录 intended unique result
- invalidate `DONE` 后再进入该 requester 自己的 `GRANT_HANDSHAKE`

### 5.3.3 `RU/RS after E/M`

若 replay 时 committed state 仍是 `G_E/G_M` 且 owner 不是 replay requester：

- 正常重新发起新的 requester-private `RECALL`
- 这次 RECALL 的 beneficiary 是 queued head requester 自己
- 不能重用前一位 requester 的 DONE recall

---

## 6. RS merge 优化的精确定义

Q3=C 中的 “Only RS can merge RS” 在本实现中定义为：

1. **合并的是 queue bucket，不是 live commit object。**
2. 新来的 RS 若命中队尾 RS bucket，则只追加一个 atom，不创建新的 live outstanding。  
3. 当该 bucket 被激活后，bucket 内 atom 仍按 FIFO 一个一个 replay。  
4. 每个 atom 仍有**自己独立的 `GRANT_HANDSHAKE/Clear`**；本修复**不**引入“多 requester 共用一个 Clear”的新协议。

这样做的原因：

- 满足“RS arrival 不新建 live handshake”的要求；
- 保持“每 PA 仅 1 个 live commit object”；
- 不破坏现有 `Clear(pa, epoch, reqId)` 一对一提交模型。

换言之，**merge = 队列级合并 / 仲裁级合并，不是 commit 级合并。**

---

## 7. 边界情况与失效策略

## 7.1 原 requester 永远不发 `Clear`

结论：**后继 queued requester 不得绕过它。**

处理：

- 继续沿用 v4 的 `GRANT_HANDSHAKE -> WAITING_CLEAR -> TIMED_OUT` 规则；
- 若 head requester Clear 超时但预算未耗尽：继续等待/重试；
- 若预算耗尽：PA 进入 fenced `TIMED_OUT`，整个 queue 冻结；
- queued requester 继续收到 `BUSY/RETRY`；
- 打 fatal-grade 审计日志：

```text
[UBCC-QUEUE-STALL] pa=<pa> headRequester=<n> reason=<clear_timeout>
queuedDepth=<d>
```

**本修复不引入“超时后跳过 head requester”的旁路。** 否则会破坏 Q1 的 requester-private 提交语义。

## 7.2 原 requester 停在 `RECALL.DONE` 不继续取 grant

同样不允许后继者绕过。可增加 watchdog 审计：

- `RECALL.DONE` 长时间未被原 requester 消费时，仅记录告警；
- 该 PA 仍保持 head ownership，不切换给后继 requester。

## 7.3 队列满

- 返回 `BUSY/RETRY`
- 不入队
- 不替换已排队 requester
- 不丢掉原 head

## 7.4 duplicate Clear replay 与 tombstone

本修复建议同步把 tombstone 从“每 PA 单条”提升为“每 PA 多条窗口内 tombstone 列表”，即：

```cpp
std::map<uint64_t, std::deque<GrantHandshakeTombstone>> _tombstonesByPa;
```

原因：有了 queued replay 后，同一 PA 可能在窗口 `W` 内连续完成多个 grant；若仍只有单 tombstone，较早 requester 的 duplicate `Clear` 可能被后续 requester 覆盖。

这是本修复的**强烈建议伴随修改**。

---

## 8. 对现有代码的最小修改点

## 8.1 `UBCCController.hh`

新增：

- `PendingRequesterAtom`
- `PendingRequesterBucket`
- `_pendingRequestersByPa`
- `_maxPendingRequestersPerPa`
- helper 声明：
  - `bool enqueueOrMergePendingRequester(...)`
  - `bool isDuplicateQueuedRequester(...)`
  - `void replayNextPendingRequester(uint64_t linePa)`
  - `size_t pendingRequesterDepth(uint64_t linePa) const`

## 8.2 `UBCCController.cc`

修改：

1. `processOuterRequest()`：
   - 区分 “same requester consume DONE recall” 与 “foreign requester queue behind it”；
   - 对活跃 `GRANT_HANDSHAKE/INVALIDATE/RECALL/UPGRADE_PENDING` 的 foreign requester 走 enqueue 路径；
   - 加 duplicate queued retry 去重；
   - 增加 queue 审计日志。

2. `processClear()`：
   - commit + tombstone + remove outstanding 后，调用 `replayNextPendingRequester(linePa)`；
   - replay 用新 committed state；
   - replay 时对 queued atom 做 `baseEpoch <- entry.epoch` rebase。

3. tombstone 逻辑（建议）：
   - 改为每 PA 可保留多个窗口内 tombstone；
   - `checkTombstone()` 按 `(epoch, reqId)` 扫描匹配。

## 8.3 `EPBackend.cc/.hh`（伴随修正，建议纳入）

若 replay 后要让 requester 后续 `Clear` 正确匹配，则 grant tuple 必须来自 live outstanding，而不是本地旧 `entry.epoch`。  
因此建议把 `OuterGrantEnvelope.epoch` 明确改为“**本次 grant 绑定的 baseEpoch**”，而不是 requester 侧缓存的旧 epoch。

否则 queued replay 即使在 UBCC 端 rebase 成功，requester 侧仍可能发送旧 epoch 的 `Clear`。

---

## 9. 规范性伪代码

## 9.1 冲突请求到达

```text
processOuterRequest(pa, req, requester):
  if no live outstanding:
      normal v4 path

  if live outstanding belongs to requester itself:
      if live == RECALL.DONE:
          requester-private consume -> create GRANT_HANDSHAKE
      else:
          BUSY/RETRY

  if live outstanding belongs to other requester:
      if queue depth < limit:
          enqueue or merge-RS
      else:
          drop enqueue
      return BUSY/RETRY
```

## 9.2 Clear 提交后自动 replay

```text
processClear(pa, src, epoch, reqId):
  validate against live GRANT_HANDSHAKE
  commit intended result
  retire handshake to tombstone
  remove live outstanding

  if pending queue non-empty:
      atom = dequeue head logical requester
      atom.baseEpoch = directory[pa].epoch   // rebase to newly committed epoch
      replay atom as fresh processOuterRequest(pa, ...)
```

---

## 10. 最终语义

这次修复后，同一 PA 的正确时序应变为：

```text
Node1 request -> RECALL(Node1-private) -> RECALL.DONE(Node1-private)
             -> GRANT_HANDSHAKE(Node1) -> Clear(Node1) -> commit
             -> replay pending Node2 using new committed state
             -> Node2 own RECALL/INVALIDATE/GRANT_HANDSHAKE if needed
```

其中：

- `RECALL.DONE` **不是共享通行证**；
- queue 只保证公平与不丢请求，**不改变 head requester 的提交优先级**；
- `RS merge RS` 只发生在排队层，不突破“一次只允许一个 live commit object”这一硬约束。
