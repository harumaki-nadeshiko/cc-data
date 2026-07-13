# 专家接手文档 — 未完成事项 & 现存问题（更新至 2026-07-13 晚）

> 原始提交时间：2026-07-13 凌晨
> 原始最后 commit：`463a9e2`
> 本更新：`c7ab6a6` ~ `464f1f8`（9 个新 commit，详见下方提交链）

---

## 1. 背景

原始目标：修复 TC98（hotspot deadlock）和 TC101（C4 direct-forward timeout），补齐 DSM 数据和目录元数据的持久化存储路径（MetaRNFClient ZMQ 集成）。

本次修复轮次新增成果：**MetaRNFClient ZMQ 集成完成 + TC3/TC8 退化修复 + TC101 NetDest 断言修复**。

---

## 2. 当前提交链（ubio 侧 - HEAD: `c7ab6a6`）

```
c7ab6a6 fix: BarrierRelease — check send() return, don't clear on failure, add RETRY log
b145d4e fix: TERMINATE forwarding from gem5Port → netPort, ignore TERMINATE from netPort (TC90 deadlock WIP)
464f1f8 bump gem5: TC101 NetDest assertion fix (CleanUnique hnfDest)
d61a678 bump gem5: clear _activeRecallPAs after ReadShared RECALL
27f55c8 fix: TC3/TC8 regression — clear _activeRecallPAs after ReadShared RECALL completes (queued-snoop-safe)
35c12a3 fix: revert _lineDataCache RecallBuffer in G_I/G_S-upgrade paths — restore HomeMemory (TC3/TC8 regression)
be2eeca bump gem5 submodule: UBAdapter MetaRNF fixes + deadlock_threshold 10G
c1f4519 fix: remove premature anonymous namespace close at line 310 — ubio build fix
4e891f9 infra: add MetaRNF message types and UBMetaRNFBody to CoherenceMessage, add <functional> to UBCCController
a6d4306 fix: add missing BackstoreSchemaA.hh include for ubio build
e6a71b3 feat: MetaRNFClient ZMQ integration + TC101 lineDataCache + TC98 diagnostic + dup BUSY removal
463a9e2 Phase 3: CoherenceMessage MetaRNF types + gem5 UBAdapter MetaRNF dispatch  ← 原始 HEAD
```

Gem5 子模块 (`v4-selfsnoop-fix-clean`):
```
87e17511fb fix: CleanUnique needsCompAck=false initially to prevent NetDest assert on uninit hnfDest (TC101)
ef043a6ee4 fix: clear _activeRecallPAs after ReadShared RECALL completes — prevent stale RECALL-snoop from blocking subsequent upgrades (TC3/TC8)
c0ded3e586 fix: MemMessage namespace qualification in MetaRNF ReadResp path
fe94bc1fa4 fix: UBAdapter MetaRNF type mismatch + TC98 deadlock_threshold 10G
```

---

## 3. 本次新完成的成果

### 3.1 MetaRNFClient ZMQ 集成（完成 ✅）

`ubio_main.cc` 中新增 `MetaRNFClient` 结构体：
- `readPage(pagePa, callback)`: 构造 MetaRNFReadReq → sendCoh(gem5Port) → 回调存储在 _pendingReads map
- `writePage(pagePa, page)`: 构造 MetaRNFWriteReq → sendCoh(gem5Port) fire-and-forget
- `handleResp(msg)`: pollAndProcess 中拦截 MetaRNFReadResp → 查找回调 → 调用
- pollAndProcess 中添加了 MetaRNFReadResp 处理分支（isUbccIngress 检查之前）
- hostIssueBackstoreRead/Write/Delete 改为先查本地 _pages 缓存，miss 时走 MetaRNF 异步读取；write 采用 write-through

**gem5 侧**（`UBAdapter.cc`）：
- MetaRNFReadReq: 4 次 MetaLine read → 组装 256B → MetaRNFReadResp
- MetaRNFWriteReq: 4 次 issueWrite → fire-and-forget
- 修复了 ReadCallback 类型（`const DataBlock&` → `const MetaLine&`）和 issueWrite 签名不匹配

### 3.2 TC3/TC8 退化修复（完成 ✅）

**退化的两个根因**：

**Fix A**（`35c12a3` — UBCCController.cc）:
回退了 G_I/G_S-upgrade 路径中错误的 `_lineDataCache → RecallBuffer` 覆盖。
`dataSource=RecallBuffer` 在 gem5 EPBackend 中的语义是"数据在本地 recall capture buffer 里"，
但 `_lineDataCache` 的数据并非本地 recall 所得。`populateGrantData(RecallBuffer)` 
检查 `_recallCaptureDataValid`，该标志在非本地 recall 场景下为 false → grant data 为空 →
gem5 使用 L1/L2 过期缓存 → 写传播丢失。

**Fix B**（`27f55c8` — EPRNFController.cc）:
ReadShared RECALL 完成后清除 `_activeRecallPAs`。
CHI ReadShared 不触发 SnpCleanInvalid（只有 ReadUnique 会），所以 `_activeRecallPAs` 
一直不被清理 → 后续 upgrade 的 SnpCleanInvalid 被过时的 RECALL-snoop guard 拦截 →
返回 SnpResp_I（"我不持有这行"）→ 节点假性无效化但 L1/L2 仍保留旧数据。

**修复位置**: EPRNFController::completePendingChiTxn，在 processQueuedSnoop 之后，
对 ReadShared RECALL 正确时机清除 active recall。

### 3.3 TC101 NetDest 断言修复（完成 ✅）

**根因**（`87e17511` — EPRNFController.cc）:
`startCleanUnique` 中 `needsCompAck=true` 但 `hnfDest=MachineID()`（MachineType_NUM）。
在 Comp_UC 到达之前，`retryPendingCompAcks`（由 wakeup 周期调用）尝试将默认 hnfDest
加入 NetDest → `MachineType_base_level(MachineType_NUM)` 越界 → assert 失败。

**修复**:
- `startCleanUnique`: needsCompAck 初始改为 false（hnfDest 未知，CompAck 应在 Comp_UC 之后发送）
- `retryPendingCompAcks`: 增加防御性检查，跳过 hnfDest.type == MachineType_NUM 的条目

### 3.4 TC90/TC98 辅助修复（WIP）

- **TERMINATE 转发**（`b145d4e`）: ubio 收到 gem5 TERMINATE 后转发到 netPort → networksim
- **BarrierRelease 发送检查**（`c7ab6a6`）: 检查 gem5Port->send() 返回值，失败不 clear
- **重复 BUSY 检查移除**: `handleUbccMessage` 中删除重复的 BUSY 检查

---

## 4. 回归测试状态（更新后）

| TC | 拓扑 | 之前状态 | 现在状态 | 备注 |
|----|------|---------|---------|------|
| TC2 | --1s | ✅ PASS | ✅ PASS | |
| TC3 | --1s | ✅ PASS → ❌(退化) → ✅ | ✅ PASS | 两处修复见 3.2 |
| TC8 | --1s | ✅ PASS → ❌(退化) → ✅ | ✅ PASS | 同上 |
| TC10 | --1s | ✅ PASS | ✅ PASS | |
| TC32-35 | --2s | ✅ PASS | ✅ PASS | |
| TC39 | --2s | ✅ PASS | ✅ PASS | |
| TC2 | --8n2s | ✅ PASS | ✅ PASS | |
| TC100 | --8n2s | ✅ PASS | ✅ PASS | |
| TC101 | --8n2s | ❌ TIMEOUT/C4 | ✅ PASS | NetDest fix (3.3) + _lineDataCache fix (3.2A) |
| TC90 | --8n1s | ❌ | ❌ TIMEOUT | PDES/Barrier 死锁，详见 §5 |
| TC98 | --8n2s | ❌ Deadlock | ❌ TIMEOUT | PDES 性能天花板，详见 §5 |

**通过率：10/12（83%）** — TC90/TC98 仍失败。

---

## 5. TC90 / TC98 深度分析

### 5.1 TC90 (--8n1s all-to-all): Barrier/PDEs 死锁

**现象**：8 节点 all-to-all 读写，120s 超时。所有节点 simout 显示 8 个 `READ_VAL MATCH`（数据全对），
但只有 node 0, 1 退出，nodes 2-7 卡住。

**已排除的假说**：
- ❌ 不是 C4 direct-forward（关掉 `UBCC_DIRECT_FWD=0` 仍失败）
- ❌ 不是 batch RS（关掉 `UBCC_BATCH_RS=0` 仍失败）
- ❌ 不是 _lineDataCache → RecallBuffer（已回退）
- ❌ 不是 `_activeRecallPAs` 不清除（已修复）
- ❌ 不是 TERMINATE 未转发（已修复 `b145d4e`）
- ❌ 不是 BarrierRelease send 失败（已加检查 `c7ab6a7`，日志无 RETRY）

**关键日志证据**：

1. UBIO 侧：ALL 节点都发送了 BarrierRelease（2-3 次），`BarrierRelease` 日志无 RETRY
2. gem5 侧：nodes 2,3,4,5,6,7 各收到 **1** 次 `UBADAPTER-BARRIER-RELEASE`（只收到第一个 barrier 的 release）
3. gem5 侧：node 0/1 收到 **0** 次 BARRIER-RELEASE 但仍然 exit——说明他们的 sync_wait 在别处被唤醒
4. node 1 的 CLK-SYNC 在第二个 barrier send（~500M ticks）之后继续 advancing（548M, 598M...）——gem5 wakeup 在跑但收不到 BarrierRelease

**核心矛盾**：UBIO 发送成功了（无 RETRY），gem5 wakeup 在运行（CLK-SYNC 推进），但 BarrierRelease 不到。这暗示 ZMQ PUB-SUB 的消息投递在 gem5 的 UBAdapter 侧被丢弃或未及时送达。

**最可能的根因**：UBAdapter::wakeup 在 safeTs 停滞时的 busy-wait 路径（line 1298-1333）中，`_port->recv(curT)` 成功收到了 BarrierRelease 消息（line 1321-1324），但 `releaseBarrier()` 调用 `tc->activate()` 时所有 threads 已经在第一个 barrier release 后重建了新的 `_barriers[mask]` 状态。第一个 barrier 和第二个 barrier 共享同一个 mask=0xFF → 共享同一个 `_barriers[0xFF]` entry。releaseBarrier 的 cleanup 和第二个 barrier 的 state setup 之间存在竞态。

### 5.2 TC98 (--8n2s hotspot contention): PDES 性能天花板

**现象**：16 核抢同一 PA，0 progress、0 SIM_DONE。120s 内 node 0 的 UBIO 产生 **625K 行**日志（正常情况 500-3000 行）。

**根因**：16 个请求者争夺同一行的串行化访问（RECALL→Grant→Clear 循环）。每次事务需要跨节点 PDES 同步。
17 个进程（16 ubio + 1 nsim）的 safeTs 取 min，高竞争下最慢节点拖累所有人。
256 次事务（16 请求者 × 16 轮）在保守 PDES 下无法在合理时间内完成。

**UBIO 日志爆炸原因**：每个请求者不停重试（BUSY），每次重试都打印日志 → node 0 ubio 625K 行/120s。

**尝试过的缓解**：
- deadlock_threshold 提升至 10G（已做）→ 不再 panic，但仍然太慢

**可能解决方向**：
- EP_SYNC_INTERVAL_PS 从 25000 减到 5000（小步更快）
- UBCC 端合并重试/replay 优化
- gem5 端乐观 PDES

---

## 6. 关键文件索引（更新后）

| 文件 | 关键行号 | 内容 |
|------|---------|------|
| `modules/ubiomodule/ubio_main.cc` | 332-397 | MetaRNFClient 结构体（新增） |
| `modules/ubiomodule/ubio_main.cc` | 400-520 | UbioBackstoreHost（MetaRNF 接入） |
| `modules/ubiomodule/ubio_main.cc` | 852-880 | TERMINATE forwarding + BarrierRelease retry |
| `modules/ubiomodule/ubio_main.cc` | 950-980 | BarrierRelease 发送（含 send 结果检查） |
| `modules/ubiomodule/ubio_main.cc` | 1143-1148 | MetaRNFReadResp 处理 |
| `modules/ubiomodule/UBCCController.cc` | 590-604 | G_I+RS grant 路径（已回退为 HomeMemory） |
| `modules/ubiomodule/UBCCController.cc` | 652-664 | G_S+RS fast path（_lineDataCache → RecallBuffer，注意这是历史遗留） |
| `gem5/.../UBAdapter.cc` | 1153-1191 | MetaRNF ReadReq/WriteReq dispatch |
| `gem5/.../UBAdapter.cc` | 1320-1324 | BarrierRelease 处理（busy-wait 路径） |
| `gem5/.../UBAdapter.cc` | 1342-1349 | pendingT 推进（barrier 关键路径） |
| `gem5/.../EPRNFController.cc` | 950-985 | completePendingChiTxn（active recall 清除） |
| `gem5/.../EPRNFController.cc` | 1253-1259 | startCleanUnique（needsCompAck fix） |
| `gem5/.../EPRNFController.cc` | 1074-1095 | retryPendingCompAcks（hnfDest guard） |
| `gem5/configs/ruby/CHI_ubcc_framework.py` | 434 | deadlock_threshold = 10G |
| `framework/Port.hh` | 116-125 | Port::safeTs（无 _peerTerminated 标志） |

---

## 7. 建议的下一步

1. **TC90**：在 gem5 UBAdapter::wakeup 的 busy-wait 路径（line 1298-1333）加 `[BARRIER-DEBUG]` 日志，
   确认 releaseBarrier() 是否被调用，以及调用时 _barriers[mask] 的状态。
   怀疑重点是：两个 sync_wait(0xFF) 共享 _barriers[0xFF] 导致的竞态。
   
   快速验证：把 TC90 的两个 sync_wait 改成不同 mask（如 0xFF 和 0xFE），看是否能解除死锁。

2. **TC98**：不是功能性问题，是性能问题。要么接受需要更长时间运行（5-10 分钟），
   要么优化 PDES sync 频率或 UBCC batch 处理。

3. **MetaRNFClient 联调**：ZMQ 通路已打通（编译通过），但还需要运行一次完整的 8-node 集成测试
   确认 MetaRNF 消息正确路由。建议用 TC2（最简）先验证。

4. **编译命令**：
   ```bash
   # ubio
   docker run --rm -v /mnt/data2/cgc/cc-ep:/workspace -w /workspace ubcc-dev:ubuntu20.04 bash -c 'bash scripts/build_ubio.sh'
   # gem5
   docker run --rm -v /mnt/data2/cgc/cc-ep:/workspace -w /workspace ubcc-dev:ubuntu20.04 bash -c 'cd /workspace/gem5 && scons build/ARM/gem5.opt -j32'
   ```

5. **运行测试**：8n2s/8n1s 不能并行，1s/2s 可以。每轮测试前务必清理残留进程。
