# TC49: duplicate InvalidateAck times out in multi-process split mode

## 现象

- `tests/e2e/run_multi.sh --1s 49` → **TIMEOUT** (600s)
- `tests/e2e/run_multi.sh --1s 48` → PASSED（同结构 workload，仅 duplicated ack 来自 Node2 而非 Node1）
- 单进程模式（所有节点同在一个 gem5 进程中）下 TC49 大概率可通过

## 协议背景

UBCC 一致性协议。TC49 workload (`tests/e2e/workloads/e2e_tc49_reorder_acks.c`)：

1. Node0 写入初始值到 DSM home=0
2. Node1 和 Node2 读取 → 成为 sharers (G_S)
3. Node0 做 exclusive upgrade → UBCC 发送 UPGRADE → EPBackend 向 Node1/Node2 fanout INVALIDATE
4. Node1/Node2 各回 InvalidateAck
5. UBCC 收齐 ack → 发 UpgradeAckNotify → gem5 的 store 完成
6. 所有节点读回最终值验证

fault 规则：`InvalidateAck:1:0:0:dup::1` — 在 ubio nid=0 上将 Node1 的 InvalidateAck 复制一份（`copies=2`），同一 tick 内连续投递两次给 `UBCCController::processInvalidationAck()`。

## 关键代码路径

### Ack 处理（`modules/ubiomodule/UBCCController.cc:1285-1513`）

```cpp
// 去重: 对同一节点重复的 ack 直接 return
uint64_t &effAckMask = isUpgradePath ? ost->upgradeAckMask : ost->ackMask;
if (effAckMask & nodeBit) { return true; /* duplicate, idempotent */ }
effAckMask |= nodeBit;
pendingAckCount--;

// 全部到齐 → 状态转移
if (pendingAckCount == 0) {
    if (isUpgradePath) {
        ost->stage = OpStage::WAITING_LOCAL_DONE;
        // 通过 _outbound 发送 UpgradeAckNotify
    }
}
```

这个处理是**对称的**——对 Node1 (bit 1) 和 Node2 (bit 2) 无区别，重复 ack 被正确过滤。

### 故障注入（`modules/ubiomodule/ubio_main.cc:725-735`）

```cpp
int faultCopies = applyUbioFault(*coh, nid);  // dup → 返回 2
// ...
for (int rep = 0; rep < faultCopies; ++rep) {
    handleUbccMessage(ubcc, nid, *coh, response, hasResponse);
    if (hasResponse) sendCoh(out, tick, ..., response);  // 第二次 no-op
}
```

同一 tick 内，ubio 收到一条 InvalidateAck，注入后变成两份，UBCCController 连续处理两次。第一次正常消耗 ack，第二次命中去重逻辑直接返回。**此处是确定性行为，不依赖时序。**

### 多跳 IPC 链路

Upgrade 的 InvalidateReq 由 gem5 侧 **EPBackend** 发出，不在 UBCC 侧 fanout。拆分模式下 ack 的回路：

```
gem5_1 → (IPC) → ubio_1 → (nsim) → ubio_0  (Node1 InvalidateAck 到达 ubio_0 的 netPort)
gem5_2 → (IPC) → ubio_2 → (nsim) → ubio_0  (Node2 InvalidateAck 到达 ubio_0 的 netPort)
```

TC49 的 Node1 ack 被 dup，所以 ubio_0 的 netPort 需要处理 **3 条** InvalidateAck。TC48 是 Node2 被 dup，也是 3 条。两者到达顺序由 networksim 的 `_fifo`（按 `readyTick` 排序）决定。

### uBio 主循环顺序

```cpp
// ubio_main.cc 主循环
gem5Port->emitSync(tick);
netPort->emitSync(tick);
pollAndProcess(gem5Port, ...);   // 先 poll gem5 port
pollAndProcess(netPort, ...);    // 再 poll net port
minTs = min(gem5Port->safeTs, netPort->safeTs);
```

`pollAndProcess` 有 `drain_cnt <= 200` 保护，防止单个 port 独占消息处理。但 ack 收到后、`UpgradeAckNotify` 通过 **gem5Port** 发出——如果 gem5Port 在该 tick 已经被 pollAndProcess 过（ack 还未到达 netPort 时），`UpgradeAckNotify` 得等到**下一个 tick** 才能被 gem5 收到。这一 tick 的延迟在大多数场景下无害，但可能与其他 IPC 消息交互形成死等。

## TC48 vs TC49 的差异（推测）

TC48 (PASS): Node2 的 ack 被 dup。Node2 的 IPC 路径比 Node1 长（多一个 node 跳转），ack 到达 ubio_0 的时序**更靠后**。所有 ack（Node1×1 + Node2×2）到达后，`UpgradeAckNotify` 在下个 tick 发出→gem5 收到→流程继续。

TC49 (TIMEOUT): Node1 的 ack 被 dup。Node1 的 ack 更快到达——Node1 的两份 ack 可能在同一批 `pollAndProcess(netPort)` 中处理完，而 Node2 的 ack 稍后到。如果时序导致 **先到达的 acks 处理完后、最后的 ack 到达前** gem5Port 已经过了 poll 窗口 → `UpgradeAckNotify` 延后 → 如果 gem5 侧（EPBackend/EP_RNF）同时有其他消息积压，形成 circular wait → 死锁/超时。

**但这不能 100% 解释**——TC48 和 TC49 的 ack 总数相同、逻辑对称，理论上都应通过或都应失败。更可能的解释：这是一个**概率性时序问题**（flaky），在不同运行中可能有时通过有时挂。

## 验证建议

1. 在单进程模式下跑 TC49（`gem5.opt test_e2e.py --tc=49 --all` 或等价），确认是否通过。若通过则确认是拆分模式引入的。
2. 在拆分模式下重复跑 TC49 五次（`run_multi.sh --1s 49 49 49 49 49`），统计通过率。若偶发通过则是 flaky。
3. 在 UBCCController::processInvalidationAck 中加 `std::fprintf` 打印每个到达的 ack（src、时间戳、pendingAckCount 变化），与 TC48 的对应日志对比，观察 ack 到达时序的差异。
4. 检查 EPBackend/EP_RNF 侧：`completeHeldUpgrade` → `UpgradeAckNotify` 回调路径是否有 polling 盲区，导致 ack 收到→notify 之间跨越 tick。

## 已知不相关

- `applyUbioFault` 的 dup 逻辑正确，copies=2，去重逻辑正确
- `fault_rules_args` JSON 空格 bug 已修（TC48 PASS 验证）
- UBCCController ack bitmask 去重逻辑正确
- ResidentDir bloom filter 非计数型，不受 duplicate 影响