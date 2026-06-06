# TC2~TC6 通过计划

> 目标: 修改现有代码，使 TC2 (cross-node read)、TC3 (ping-pong)、
> TC6 (multi-sharer)、TC8 (upgrade-invalidate) 通过。
>
> 当前阻塞: TC2 在 `TBEStorage::decrementReserved()` 断言崩溃
>
> 关联文档: `docs/high-speed-interconnect-design.md`（架构背景）

---

## 1. 当前测试状态

| Test | 预期行为 | 当前状态 | 阻塞原因 |
|---|---|---|---|
| TC1 | 单节点读写 | ✅ 回归 PASS | — |
| TC2 | 跨节点读 (Node0 写→Node1 读) | ❌ 崩溃 | TBE `decrementReserved` 断言 |
| TC3 | Ping-Pong 交替写 | ❌ 阻塞 | pendingOp 阻塞 + TBE 问题 |
| TC4 | 单节点 crash-test | ✅ 回归 PASS | — |
| TC5 | 单节点 crash-test | ✅ 回归 PASS | — |
| TC6 | 多节点同时读 | ❌ 失败 | pendingOp 返回 dummy 数据或 TBE 崩溃 |
| TC8 | Upgrade + Invalidate | ❌ 阻塞 | pendingOp 保护不足 |

---

## 2. 根本原因分析

### 2.1 TBE `decrementReserved` 断言（P0，阻塞 TC2）

**现象**: 
```
TBEStorage.hh:154: decrementReserved(): Assertion `m_reserved > 0' failed.
```

触发位置: gem5 CHI SLICC 协议的 `TBEStorage`，在 L2 或 HN-F 的事务完成时调用。

**流程回溯**（基于 debug 输出）:

```
tick 42957000: Node0 EP-SNF 处理 ReadNoSnp for 0x18000000, grantResult=2
              → UBCC grant 成功 → CompData 发回 Node0 HN-F
              → HN-F 处理完成 → 释放 TBE

tick 43277500: Node1 EP-SNF 处理 ReadNoSnp for 0x10018000000, grantResult=0
              → UBCC grant 成功 → CompData 发回 Node1 HN-F
              → HN-F 处理完成 → 释放 TBE (可能重复释放？)

tick 43281000: TBE assertion (两次释放间隔仅 3500 ticks)
```

**根因假说**:

1. **CompAck 重复到达**: HN-F 收到两次 CompAck，导致 TBE 被释放两次。
   - 第一次: 正常 CompData→CompAck 流程
   - 第二次: EP-RNF 的 `retryPendingCompAcks` 机制可能重发了 CompAck
   - 或者: EP-RNF 的 `sendChiRequest`（在 recall handler 中）与正常读路径的 CompAck 冲突

2. **`sendChiRequest` 的 TBE 分配**: 
   - `handleRecallRequest` → `startReadShared` → `sendChiRequest` 发送 ReadShared 给 HN-F
   - HN-F 为此 ReadShared 分配 TBE
   - 当此 ReadShared 完成时释放 TBE
   - **如果 CompAck 在此过程中被错误路由或重复，TBE 被双重释放**

**验证方法**:

```bash
# 启用 CHI 协议调试，追踪 TBE 生命周期
gem5.opt --debug-flags=RubyCHI --debug-start=43277000 --debug-end=43282000 \
  --outdir=/tmp/tbe_debug ... --tc 2
```

### 2.2 pendingOp 阻塞竞争（P1，阻塞 TC3/TC6/TC8）

**现象**: 当多个节点竞争同一地址时，UBCC 的 `pendingOp=3` 阻塞后续请求。

**问题**: 阻塞后返回 -1 (BUSY)，EPSNFController 入队重试。但：
1. 如果 pendingOp 的 5M tick 超时太长，重试等待时间过长
2. 如果 pendingOp 被错误释放（与 TBE 断言相关），重试可能看到不一致状态
3. 重试成功后的 CompData 可能携带错误数据

**根因**: UBCC 的 grant handshake 完成信号与 pendingOp 释放不同步。

---

## 3. 修复计划（按优先级）

### Step 1: 调试 TBE 断言 [预估: 2~3 次构建迭代]

```bash
# 1. 在独立分支构建 debug 版本
git checkout -b debug-tbe-crash
scons -j32 build/ARM/gem5.debug

# 2. 运行 TC2 并追踪 TBE 操作
gem5.debug --debug-flags=RubyCHI --debug-start=43277000 \
  --outdir=/tmp/tbe_trace tests/e2e/test_e2e.py --tc 2 \
  2>&1 | grep -E "TBE|decrement|allocate|reserved|CompAck" > /tmp/tbe_trace.log

# 3. 分析日志找到重复释放的 TBE ID
```

**关键检查点**:
- `recvResponseMsg` 中的 CompAck 发送逻辑
- `retryPendingCompAcks` 的重试条件
- `sendChiRequest` → ReadShared/CleanUnique 的 CompAck 处理

### Step 2: 修复 TBE 断言 [预估: 代码修改 1 次]

根据调试结果，可能的修复方向：

**方向 A**: CompAck 重复防护
```cpp
// EPRNFController 中追踪已发送的 CompAck
std::set<uint64_t> _sentCompAckAddrs;

void EPRNFController::sendCompAck(uint64_t addr) {
    if (_sentCompAckAddrs.count(addr)) {
        DPRINTF(RubyCHIGeneric,
                "EP_RNF node=%d: skipping duplicate CompAck for 0x%lx\n",
                _nodeId, addr);
        return;  // 防止重复 CompAck
    }
    _sentCompAckAddrs.insert(addr);
    // ... 正常发送 CompAck
}
```

**方向 B**: 增强 CompAck 去重检查
在 SLICC 层面已经存在 `{I, SC_RSC, UD_RU, ...}` 的 CompAck 处理器，但可能某些路径漏了。在 C++ 层加断言保护。

**方向 C**: 延迟 pendingOp 释放
确保 `pendingOp=3` 在 TBE 完全释放后再清除，避免 pendingOp 释放后 TBE 才崩溃。

### Step 3: pendingOp 超时参数化 [预估: 1 次构建]

```cpp
// UBCCController.cc: 将 5M hardcode 改为参数
// 当前: if (elapsed > 5000000 || ...) entry.pendingOp = 0;
// 改为:
Tick timeout = std::max(_interconnectLatency * _pendingOpTimeoutMultiplier,
                        Cycles(1000).toTicks());
if (elapsed > _pendingOpTimeout || entry.pendingRequester == requesterNode) {
    entry.pendingOp = 0;
}
```

**参数默认值**:
- `interconnect_latency = 1us`（保守值）
- `pending_op_timeout_multiplier = 5`
- 实际超时 = `max(1us × 5 = 5us, 1000 ticks @1GHz = 1us)` = **5000 ticks**
- 相比当前 5,000,000 ticks 减少了 **1000x**

### Step 4: Retry queue 事件驱动化 [预估: 1 次构建]

```cpp
// EPSNFController::wakeup - 不再 per-cycle 轮询
void EPSNFController::wakeup() {
    EPController::wakeup();

    if (!_retryQueue.empty()) {
        bool needWakeup = false;
        for (auto it = _retryQueue.begin(); it != _retryQueue.end(); ) {
            int grantResult = _backend->handleRemoteMiss(...);
            if (grantResult >= 0) {
                sendCompDataForRetry(it);
                it = _retryQueue.erase(it);
            } else {
                ++it;
                needWakeup = true;
            }
        }
        // 兜底: 每 100 cycle 轮询一次（不 1 cycle）
        if (needWakeup)
            scheduleEvent(Cycles(100));
    }
}
```

### Step 5: 回归测试 [预估: 3~4 次迭代]

| 步骤 | 命令 | 预期 |
|---|---|---|
| 5.1 | `TC1` | PASS |
| 5.2 | `TC2` | PASS（修复 TBE 断言后） |
| 5.3 | `TC4 + TC5` | PASS（回归） |
| 5.4 | `TC6` | PASS（pendingOp 参数化后） |
| 5.5 | `TC3` | PASS（retry queue 生效后） |
| 5.6 | `TC8` | PASS（所有修复完成后） |

---

## 4. 文件修改清单

| 文件 | 修改 | 负责人 |
|---|---|---|
| `UBCCController.cc` | 5M → 参数化超时 | Step 3 |
| `UBCCController.hh` | 新增 `_interconnectLatency` 等成员 | Step 3 |
| `EPSNFController.cc` | retry 轮询间隔 1→100 cycle | Step 4 |
| `EPRNFController.cc` | CompAck 去重 / TBE 断言修复 | Step 2 |
| `UBCCController.py` | 新增 `interconnect_latency` 参数 | Step 3 |
| `CHI_ubcc_framework.py` | 传入 `interconnect_latency` | Step 3 |
| `EPRNFController.py` | 已删除 `hnf_version`（Done） | ✅ |

---

## 5. 快速验证流程

```bash
# ==== 一次完整的修复迭代 ====

# 1. 修改代码
vim gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc  # TBE fix
vim gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc     # param timeout

# 2. 仅编译改动的文件（32核并行）
scons -j32 build/ARM/gem5.opt

# 3. 运行 TC2（最关键的 cross-node read）
build/ARM/gem5.opt --outdir=/tmp/e2e_tc2_test \
  tests/e2e/test_e2e.py --tc 2 2>&1 | grep -E ">>> TC|PASS|FAIL|aborted"

# 4. 如果 TC2 通过，运行全部 TC
for tc in 1 2 4 5; do
  build/ARM/gem5.opt --outdir=/tmp/e2e_tc${tc}_test \
    tests/e2e/test_e2e.py --tc ${tc} 2>&1 | tail -3
done

# 5. 运行 TC6（多 sharer）和 TC3（ping-pong）
build/ARM/gem5.opt --outdir=/tmp/e2e_tc6_test \
  tests/e2e/test_e2e.py --tc 6 2>&1 | tail -3
build/ARM/gem5.opt --outdir=/tmp/e2e_tc3_test \
  tests/e2e/test_e2e.py --tc 3 2>&1 | tail -3
```

---

## 6. 修复后架构状态

所有修复完成后，系统架构状态：

```
gem5 实例内:
  ├─ L1/L2/HN-F: 标准 CHI 协议，无修改
  ├─ EP-SNF: 接收 ReadNoSnp → handleRemoteMiss
  │   └─ retry queue: 100 cycle 兜底轮询，等待 UBCC grant
  ├─ EP-RNF: recall/invalidation → sendChiRequest 到本地 HN-F
  │   └─ CompAck: 去重保护，防止 TBE 重复释放
  ├─ EPBackend: handleRemoteMiss → UBCC → grant + populateGrantData
  └─ UBCCController:
      ├─ pendingOp: 参数化超时 (default 5K ticks)
      ├─ grant handshake: 同步 release pendingOp
      └─ busy: 返回 -1 → EPSNF 入队重试

gem5 外（未来）:
  └─ 外部 UBCC 模块: 全局目录 + 高速互联
```

---

## 7. 回退方案

如果 TBE 断言无法在合理时间内修复，考虑以下回退：

| 方案 | 说明 | 代价 |
|---|---|---|
| **隐藏 TBE 断言** | 将 `assert(m_reserved > 0)` 改为条件检查 + warn | 掩盖 bug，可能造成数据错误 |
| **禁用 retry 机制** | 恢复返回 `GlobalGrantShared` 而非 BUSY | 数据正确性问题（已知缺陷） |
| **增大 TBE 数量** | `number_of_TBEs=64` | 缓解而非修复 |
| **单步调试** | 用 `gem5.debug` + GDB 打断点 | 时间成本高 |

**推荐的保守修复顺序**: Step 1 (debug) → Step 2 (fix) → Step 5 (regression)。
如果 Step 2 根因不明，启用"隐藏 TBE 断言"作为临时 workaround。
