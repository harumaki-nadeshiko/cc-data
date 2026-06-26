# 北京时间 2026/06/27 03:29 未提交修改完整记录

> 基于 opencode.db 中 edit/write 工具调用记录编译。
> 参考 commit: `856e544` (2026-06-26 23:52:52 +0800) — 此后无新提交

## 背景

最后提交 `856e544` (gem5 submodule `903c7f1955`) 之后，opencode.db 中共有 3 个 session:

| Session | 时间 (北京) | 类型 | 编辑 |
|---------|-------------|------|------|
| `ses_0faee79` | 01:54-02:31 | plan-designer | 无文件编辑 |
| `ses_0fa97aae` | 03:29-03:31 | explore | 无文件编辑 |
| `ses_0fa8735` | 03:47-03:48 | code-implementer | **6 次 edit 调用** |

plan-designer (01:54-02:31) 输出了 TC2 修复方案，用户随后手动将方案应用到工作树。
在 03:29 前，所有非 gem5 和 gem5 修改均已存在。

约 03:45，用户意外执行 `git checkout --` 回退了 gem5 中 EPBackend.cc 和 UBAdapter.cc。
ses_0fa8735 于 03:47 启动，通过 6 次 edit 工具调用恢复上述修改。

**以下记录完全来自 opencode.db 中 ses_0fa8735 的 6 次 edit 调用。**

---

## 修改清单

### Edit #1 — EPBackend.cc: M8 Invalidation Routing 改为 Home UBCC 直发

```json
{
  "filePath": "/mnt/data2/cgc/cc-ep/gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc",
  "status": "completed"
}
```

**删除** (~1888 bytes) — 原 requester-side fanout 代码块 `// ---- M8: Global Invalidation Routing ----`:

- 从 `if (pendingInvCount > 0)` 开始
- 遍历 `pendingInvMask` 每个 bit
- 构造 `OuterInvalidateMsg`，通过 `sendInvalidateReqToSharer()` 代发
- 共 ~40 行 C++

**替换为** (~478 bytes) — DPRINTF 诊断日志:

```cpp
if (pendingInvCount > 0) {
    DPRINTF(RubyEP,
            "EPBackend node_id=%d: home UBCC owns invalidation fanout "
            "PA=0x%lx pendingInvCount=%d mask=0x%lx\n",
            _nodeId, line_pa, pendingInvCount, pendingInvMask);
}
```

**理由**: Invalidation 路由权从 requester 侧 EPBackend 转移到 home UBCC。

---

### Edit #2 — UBAdapter.cc: handleResponse() 添加异步控制消息分类

```json
{
  "filePath": "/mnt/data2/cgc/cc-ep/gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc",
  "status": "completed"
}
```

**在原 `handleResponse()` 开头插入 switch 分流逻辑** (~615 bytes new vs 266 bytes old):

`handleResponse()` 原内容 — 直接进入 `_pendingByReqId` 匹配:
```cpp
void UBAdapter::handleResponse(framework::MemMessage *m) {
    if (!_port) return;
    const CoherenceMessage *coh = m->getPayload<CoherenceMessage>();
    if (!coh) return;
    // Dispatch via _pendingByReqId map
    auto it = _pendingByReqId.find(m->hdr.req_id);
    ...
```

`handleResponse()` 新内容 — 先判异步控制消息，再走 pending map:
```cpp
void UBAdapter::handleResponse(framework::MemMessage *m) {
    if (!_port) return;
    const CoherenceMessage *coh = m->getPayload<CoherenceMessage>();
    if (!coh) return;
    // Async control messages: enqueue FIFO, process later via drainDeferredControls
    switch (coh->h.type) {
      case CoherenceMessageType::InvalidateReq:
      case CoherenceMessageType::RecallReq:
      case CoherenceMessageType::UpgradeAckNotify:
        _deferredControls.push_back(*coh);
        return;
      default:
        break;
    }
    // Dispatch via _pendingByReqId map
    auto it = _pendingByReqId.find(m->hdr.req_id);
    ...
```

**关键**: 控制消息在 reqId 匹配前先分流入 FIFO，防止被当成同步响应吞掉。

---

### Edit #3 — UBAdapter.cc: 添加 drainDeferredControls()（含 bug，见下文分析）

```json
{
  "filePath": "/mnt/data2/cgc/cc-ep/gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc",
  "status": "completed"
}
```

**意图**: 在文件末尾 `_lastResponseValid = false;` 之后追加 `drainDeferredControls()`:

```cpp
void UBAdapter::drainDeferredControls() {
    if (_drainingDeferredControls) return;
    _drainingDeferredControls = true;
    while (!_deferredControls.empty()) {
        CoherenceMessage msg = _deferredControls.front();
        _deferredControls.pop_front();
        recvFromRouter(msg);
    }
    _drainingDeferredControls = false;
}
```

**BUG**: oldString `_lastResponseValid = false;\n}\n\n} // namespace ruby\n} // namespace gem5` 在文件中出现**两次**:
1. 在 `checkResponseCallbacks()` 结束处 (正确位置)
2. 在 `handleResponse()` 结束处 (刚被 Edit #2 修改)

匹配到了**第 1 处出现** (handleResponse 之后)，导致:
- `handleResponse()` 被删除
- `scheduleResponseCheck()` 被删除
- 它们被替换为 `drainDeferredControls()`

**这是 03:29 状态与当前状态的唯一差异** — 03:29 时 `handleResponse()` 和 `scheduleResponseCheck()` 均存在且正确。

---

### Edit #4 — UBAdapter.cc: wakeup() recv 条件扩展

```json
{
  "filePath": "/mnt/data2/cgc/cc-ep/gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc",
  "status": "completed"
}
```

```diff
- while (m && st == framework::ReceiveStatus::kMessage) {
+ while (m && (st == framework::ReceiveStatus::kMessage || st == framework::ReceiveStatus::kSync)) {
```

**理由**: 在解耦后的 Port 模式下，kSync 也是有效消息状态，不应跳过。

---

### Edit #5 — UBAdapter.cc: wakeup() 增加 drainDeferredControls 调用

```json
{
  "filePath": "/mnt/data2/cgc/cc-ep/gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc",
  "status": "completed"
}
```

添加 `drainDeferredControls()` 调用并重新编号注释:

```diff
-    // 3. Check for matched responses (for retry-based callers)
+    // 3. Drain deferred async control messages before checking responses
+    drainDeferredControls();
+
+    // 4. Check for matched responses (for retry-based callers)
     checkResponseCallbacks();

-    // 4. Schedule next wakeup using safeTs for conservative advancement
+    // 5. Schedule next wakeup using safeTs for conservative advancement
```

---

### Edit #6 — UBAdapter.cc: wakeup() 超过检查上限后输出警告

```json
{
  "filePath": "/mnt/data2/cgc/cc-ep/gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc",
  "status": "completed"
}
```

在 `if (++_responseCheckCount < 100000)` 的分支后添加 `else` 块:

```cpp
else {
    static int wstop = 0;
    if (++wstop <= 1)
        warn("UBAdapter node=%d: wakeup STOPPED after 100000 checks\n", _nodeId);
}
```

---

## 补遗：非 gem5 文件的修改

以下文件在最后提交后有修改，但未通过 opencode 的 edit/write 工具调用应用 (用户手动编辑):

1. **`framework/Port.cc`** (+27/-8): 双 PAIR socket 全双工 IPC (TX bind + RX connect 分离)
2. **`modules/networksim/networksim_main.cc`** (+21/-1): networksim 调试日志 (NSIM-RECV/FWD/NOBUF/MISS)
3. **`modules/ubiomodule/UBCCController.cc`** (+57/-0): 新增 `fanoutInvalidateTargets()` 实现
4. **`modules/ubiomodule/UBCCController.hh`** (+25/-0): 新增 `UBCCOutboundIf` 接口 + fanout 声明
5. **`tools/ubio/ubio_main.cc`** (+59/-7): `UbioBackstoreHost` 扩为 `UBCCOutboundIf` 实现等
6. **`tests/e2e/run_multi.sh`** (+5/-3): Shell 脚本健壮性修复 + NodeAddressMap.cc 编译
7. **`modules/networksim/networksim`** (binary): 重新编译产物

## 当前状态 vs 03:29 状态差异

| 方面 | 03:29 (与当前 HEAD 相比) | 当前 (当前 HEAD 相比) |
|------|-------------------------|----------------------|
| 非 gem5 文件 | 全部已修改 | 相同 |
| EPBackend.cc M8 块 | 已替换为 DPRINTF | 相同 |
| UBAdapter.hh | 有 `_deferredControls` | 有 `_deferredControls` |
| **UBAdapter.cc handleResponse()** | **存在, 含 async control switch** | **被 Edit #3 误删除** |
| **UBAdapter.cc scheduleResponseCheck()** | **存在** | **被 Edit #3 误删除** |
| UBAdapter.cc drainDeferredControls() | 存在 | 存在 |
| UBAdapter.cc wakeup() edits | 全部已应用 | 全部已应用 |

## 时间线

```
23:52 6/26  ← 最后提交 856e544
  (gap: 用户手动应用 plan-designer 方案)
01:54-02:31 ← ses_0faee79 plan-designer (分析, 无编辑)
  (gap: 用户手动应用修改到工作树)
03:29-03:31 ← ses_0fa97aae explore (读日志, 此时所有修改在)
  ~03:45    ← 用户误执行 git checkout -- 回退 gem5 文件
03:47-03:48 ← ses_0fa8735 code-implementer (6 次 edit, 恢复 gem5 文件)
  * Edit #3 中的 bug 导致 handleResponse/scheduleResponseCheck 被意外删除
03:53       ← 前次文档整理尝试
```

## 从 plan-designer 输出中记录的额外缺失修改

plan-designer (ses_0faee79) 的输出中还包含以下本应实现、但当前工作树中不存在的内容:

1. **`UBCCController::emitUpgradeAckNotify()`** — 未实现，processInvalidationAck 中仍依赖 `_router`
2. **`EPBackend::notifyLocalWriteUpgrade()`** — 未删除 requester-side fanout
3. **UBAdapter 分类函数** — `isAsyncControlType()` / `isSyncResponseType()` 静态辅助函数未添加
