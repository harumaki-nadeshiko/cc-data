# TC98 BUSY Retry 风暴修复方案

> 状态：待实施
> 前置依赖：当前 HEAD (a0446f6) 的所有修改已合入
> 预计影响文件：4 个 gem5 文件 + 1 个 ubio 文件

---

## 1. 问题描述

TC98（8n2s 16 路热点竞争）在 1500s 内只完成 15/256 笔事务。
根因是 BUSY retry 风暴淹没了 PDES 带宽，导致真正有用的消息
（InvalidateAck、RecallResp）被延迟投递。

### 1.1 根因链

```
HN-F (任意节点 X) 发 ReadNoSnp 给本地 EP-SNF
    ↓
EP-SNF 调 handleRemoteMiss → sendReadReq
    requesterNode = _nodeId（始终是 home node 0）  ← BUG
    ↓
ubio 转发到 home UBCC
    ↓
UBCC: existing outstanding (requester=0) + 新请求(requester=0)
    → "same requester" → BUSY（不入队 _pendingRequesters）
    ↓
EP-SNF 收到 BUSY → 20,000 cycle 后 retry → 又发一遍 ReadReq
    ↓
16 路并发 × 每 20,000 cycle = 海量跨进程 ReadReq 穿过 PDES
    ↓
InvalidateAck / RecallResp 被挤在 networksim fifo 里
    → outstanding 永远不 clear → BUSY 永远持续
```

### 1.2 证据

- `ubio_n0_s0/stderr.log`：`BUSY (n=2000)` — 2000+ 次同一 requester BUSY
- `ubio_n0_s0/stderr.log`：0 条 `UBCC-QUEUE enqueue` — push-grant 路径完全失效
- `ubio_n0_s0/stderr.log`：4 条 `invalidation ack` — 6 个目标只收到 2 个 ack
- 完整事务理论时延 ~2.7μs（6 跳 × 415ns + 处理），256 笔 = 691μs 模拟时间
- 实际 1500s 只完成 15 笔 → 有效吞吐接近零

---

## 2. 修复方案

### Fix A: 修正 EP-SNF requesterNode（根因修复）

**问题**：`EPBackend::handleRemoteMiss` 中 `requesterNode` 始终设为
`_nodeId`（本地 node ID），不区分原始请求来自哪个远端节点。

**修复**：从 CHI 消息的 `m_requestor` (MachineID) 推导出原始请求节点 ID，
传给 `sendReadReq` 的 `requesterNode` 参数。

#### 修改文件

**`gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc`**

`recvRequestMsg` 中，从 `msg->m_requestor` 提取 node ID：

```cpp
// 当前代码（EPSNFController.cc ~line 237）：
int homeNode = -1;
int grantResult = _backend->handleRemoteMiss(
    msg->m_addr, neededPerm, writeIntent, _socketId, homeNode);

// 修改为：
int originNode = cycleOriginNode(msg->m_requestor);  // 新增：从 MachineID 推导
int homeNode = -1;
int grantResult = _backend->handleRemoteMiss(
    msg->m_addr, neededPerm, writeIntent, _socketId, homeNode,
    originNode);  // 新增参数
```

**`gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh`**

`handleRemoteMiss` 签名增加 `originNode` 参数：

```cpp
// 当前：
int handleRemoteMiss(uint64_t line_pa, int neededPerm, bool writeIntent,
                     int socketId, int &outHomeNode);

// 修改为：
int handleRemoteMiss(uint64_t line_pa, int neededPerm, bool writeIntent,
                     int socketId, int &outHomeNode,
                     int originNode = -1);  // -1 表示未知，fallback 到 _nodeId
```

**`gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc`**

`handleRemoteMiss` 内部，`requesterNode` 使用 `originNode`：

```cpp
// 当前（~line 530）：
// 无 originNode 参数，直接用 _nodeId

// 修改：在构造 reqEnv 时
reqEnv.srcNode = (originNode >= 0) ? originNode : _nodeId;
```

以及 `_requesterLines[line_pa]` 的 entry 也需要记录 originNode，
以便 retry 时保留正确的请求者信息。

**`gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.hh`**

`RetryEntry` 增加 `originNode` 字段：

```cpp
struct RetryEntry {
    uint64_t linePa;
    int neededPerm;
    bool writeIntent;
    MachineID hnReq;
    MachineID fwdReq;
    bool dataToFwdReq;
    int originNode = -1;  // 新增
};
```

retry 路径中把 `originNode` 传给 `handleRemoteMiss`。

#### originNode 推导方法

从 `MachineID` 提取 node ID 的方式取决于 gem5 的 CHI 拓扑配置。
在 `CHI_ubcc_framework.py` 中，每个 node 的 HN-F controller 有
一个 `version` 和 `node_id`。`MachineID` 包含 `MachineType` 和
`num`（version）。需要一个映射函数：

```cpp
// EPSNFController.cc 或 EPBackend.cc 中新增：
int EPSNFController::extractOriginNode(const MachineID &mid) const {
    // 方案 1：查 m_ruby_system 的 MachineID→nodeId 映射表
    // 方案 2：如果 version 编码中包含 node_id 信息，直接提取
    // 方案 3：从 msg->m_addr 的 PA 编码推导 home node
    //         （但这是 home 不是 requester）
    // 推荐方案 1：在 EPBackend::init 时构建映射表
}
```

**注意**：如果 MachineID → nodeId 的映射不可直接获取，
可以退而求其次：**在 CHI 消息中增加一个 sideband 字段传递
原始请求者 node ID**。这需要修改 CHI message format（更侵入），
但最可靠。

#### 验收标准

1. TC98 的 UBCC 日志中出现 `UBCC-QUEUE enqueue` 条目（不同请求者被入队）
2. TC98 的 UBCC 日志中出现 `PUSH-GRANT` 条目（push-grant 路径被触发）
3. TC98 在 600s 内完成（理论 <10s，留充足裕量）
4. 回归测试全部通过：TC2/3/8/10(1s) TC32-35/39(2s) TC90(8n1s)
   TC2/100/101(8n2s) TC102/22/23/28(1s)

---

### Fix B: EP-SNF retry 指数退避（防御性修复）

**问题**：EP-SNF `_retryQueue` 固定 20,000 cycle 间隔 retry，在
push-grant 失效或网络拥塞时产生 retry 风暴。

**修复**：引入指数退避 + 可配置上下限 + push-grant 到达时重置。

#### 修改文件

**`gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.hh`**

新增环境变量读取函数和 RetryEntry 字段：

```cpp
// 新增可配置参数函数（与 epsnf_retry_cycles 同位置）：
static uint64_t epsnf_retry_min_cycles();   // EP_RETRY_MIN_CYCLES, default 20000 (10μs)
static uint64_t epsnf_retry_max_cycles();   // EP_RETRY_MAX_CYCLES, default 2000000 (1ms)

// RetryEntry 增加退避计数：
struct RetryEntry {
    uint64_t linePa;
    int neededPerm;
    bool writeIntent;
    MachineID hnReq;
    MachineID fwdReq;
    bool dataToFwdReq;
    int originNode = -1;      // Fix A
    int retryCount = 0;       // Fix B: 退避计数器
};
```

**`gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc`**

```cpp
// 新增参数读取：
static uint64_t epsnf_retry_min_cycles() {
    static uint64_t v = 0;
    if (v == 0) {
        const char *e = std::getenv("EP_RETRY_MIN_CYCLES");
        v = e ? std::strtoull(e, nullptr, 10) : 20000;
    }
    return v;
}

static uint64_t epsnf_retry_max_cycles() {
    static uint64_t v = 0;
    if (v == 0) {
        const char *e = std::getenv("EP_RETRY_MAX_CYCLES");
        v = e ? std::strtoull(e, nullptr, 10) : 2000000;
    }
    return v;
}

// 计算当前退避间隔：
static uint64_t retryInterval(int retryCount) {
    uint64_t base = epsnf_retry_min_cycles();
    uint64_t cap  = epsnf_retry_max_cycles();
    // 指数退避：base << retryCount，但不超过 cap
    // 安全限制 shift 量防止溢出
    int shift = std::min(retryCount, 20);
    uint64_t interval = base << shift;
    return std::min(interval, cap);
}
```

修改 retry 调度（3 处）：

```cpp
// 原代码（~line 252-253，初次 BUSY 入队）：
_retryQueue.push_back(entry);
scheduleEvent(Cycles(epsnf_retry_cycles()));

// 改为：
entry.retryCount = 0;
_retryQueue.push_back(entry);
scheduleEvent(Cycles(retryInterval(0)));

// 原代码（~line 155-156，retry 失败后重新调度）：
if (needWakeup)
    scheduleEvent(Cycles(epsnf_retry_cycles()));

// 改为：
if (needWakeup) {
    // 找最小退避间隔
    uint64_t minInterval = epsnf_retry_max_cycles();
    for (auto &e : _retryQueue)
        minInterval = std::min(minInterval, retryInterval(e.retryCount));
    scheduleEvent(Cycles(minInterval));
}

// retry 循环内，失败时递增 retryCount：
} else {
    it->retryCount++;
    needWakeup = true;
    ++it;
}
```

**Push-grant 到达时重置退避**：

在 `_onResponseWired` callback 触发时（UBAdapter.cc ~line 1461），
EP-SNF 的 wakeup 会处理 retry queue。`handleRemoteMiss` 成功后
entry 被 erase，自然不需要重置。但如果 push-grant 到达后
第一次 retry 就成功了，退避计数自然终止。无需额外代码。

#### 可配置参数汇总

| 环境变量 | 默认值 | 含义 |
|---------|--------|------|
| `EP_RETRY_MIN_CYCLES` | 20,000 (10μs @2GHz) | 首次 retry 间隔 |
| `EP_RETRY_MAX_CYCLES` | 2,000,000 (1ms @2GHz) | 退避上限 |

退避序列示例（默认值）：10μs → 20μs → 40μs → 80μs → 160μs →
320μs → 640μs → 1ms → 1ms → 1ms ...

#### Writeback retry 也适用

`_pendingWritebacks` 的 retry 同样使用 `epsnf_retry_cycles()`，
同样适用指数退避。在 `PendingWriteback` 中增加 `retryCount` 字段，
`processPendingWritebacks` 中递增并使用 `retryInterval()`。

#### 验收标准

1. 环境变量 `EP_RETRY_MIN_CYCLES` 和 `EP_RETRY_MAX_CYCLES` 可配置
   且生效（修改后 retry 间隔变化）
2. TC98 日志中 retry 间隔随重试次数增大（可通过 DPRINTF 或日志验证）
3. 即使 Fix A 未实施，Fix B 单独也能减少 retry 风暴（BUSY 日志行数
   从 ~2000 降到 <100）
4. 回归测试不受影响（退避初始间隔 = 原值 20,000 cycle）

---

## 3. 实施顺序

1. **先实施 Fix A**（requesterNode 修正）——这是根因修复，消除
   retry 风暴的源头
2. **再实施 Fix B**（指数退避）——作为防御层，防止未来类似场景
3. 两个 Fix 合成一个 commit

## 4. 测试计划

```bash
# 构建
docker run --rm -v /mnt/data2/cgc/cc-ep:/workspace -w /workspace \
  ubcc-dev:ubuntu20.04 bash -c '
    cd /workspace/gem5 && scons build/ARM/gem5.opt -j32
    bash /workspace/scripts/build_ubio.sh
  '

# 回归测试
TIMEOUT_SEC=120 bash tests/e2e/run_multi.sh --1s 2 3 8 10 102 22 23 28
TIMEOUT_SEC=120 bash tests/e2e/run_multi.sh --2s 32 33 34 35 39
TIMEOUT_SEC=300 bash tests/e2e/run_multi.sh --8n1s 90
TIMEOUT_SEC=300 bash tests/e2e/run_multi.sh --8n2s 2 100 101

# TC98 验收
TIMEOUT_SEC=600 bash tests/e2e/run_multi.sh --8n2s 98
# 预期：PASS，耗时 <600s

# 验证 push-grant 生效
grep "PUSH-GRANT" logs/*/ubio_n0_s0/stderr.log  # 应有条目
grep "UBCC-QUEUE.*enqueue" logs/*/ubio_n0_s0/stderr.log  # 应有条目
grep "BUSY" logs/*/ubio_n0_s0/stderr.log | wc -l  # 应 <100
```

## 5. 风险评估

- **Fix A 风险**：MachineID → nodeId 映射如果不准确，可能导致
  错误的 requester 入队。需要在 TC3/TC39（2-3 节点简单场景）
  验证 RECALL → push-grant 路径正确。
- **Fix B 风险**：退避过于激进（上限过大）可能导致正常延迟增大。
  默认上限 1ms 远大于一笔事务的 2.7μs，不影响正常场景。
- **回归风险**：Fix A 的 originNode 参数有 default=-1 fallback，
  旧路径行为不变。Fix B 的初始间隔 = 原值，首次 retry 行为不变。
