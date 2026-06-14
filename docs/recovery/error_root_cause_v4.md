# CC-EP gem5 v4 协议错误根因分析报告

## 1. Executive Summary

当前 v4 实现呈现出非常明确的分层失效特征：**TC1 本地路径可通过，但所有跨节点测试（TC2-TC11）失败**。这说明基础的单节点 CHI/Ruby 路径已经基本可运行，而**跨节点协议闭环**仍存在系统性断裂。

截至本报告撰写时，已有三项修复落地：

- **Fix A**：`CHI-cache-actions.sm:1602`，`Send_ReadNoSnp` 不再只接受 `CompData_UC`，而是同时接受 `CompData_SC` 与 `CompData_UD_PD`
- **Fix B**：`ep/UBCCController.cc:1191`，`processClear()` 改为比较 `baseEpoch`，不再错误比较 `reservedEpoch`
- **Fix C**：`ep/UBCCController.cc:1195`，`Clear` 被拒绝时会将陈旧 `GRANT_HANDSHAKE` 退休到 tombstone，避免永久堵塞该 PA

这三项修复解决的是**协议 tuple 匹配与握手残留**问题，但并未消除跨节点失败的主因。当前仍有三项未解根因：

1. **Issue 4：DONE RECALL 生命周期错误，Recall→Grant→Clear 闭环仍不稳**
2. **Issue 5：跨节点数据可见性错误，数据没有可靠落到 home DDR4**
3. **Issue 6：`handleRecallRequest()` 绕过 HN-F，破坏 scheme_v4 的 no-bypass-HNF 不变量**

结论上，v4 目前不是“某一个 bug 未修”，而是**控制面闭环、数据面落盘点、以及回收路径架构约束**三者同时失配。TC1 能过，只能证明本地路由被 D-10 workaround 暂时稳定；它不能证明跨节点协议已正确。

---

## 2. Resolved Issues

### 2.1 Fix A：`CHI-cache-actions.sm:1602`

**问题**：`Send_ReadNoSnp` 原先只接受 `CompData_UC`，但 EP/DSM 路径实际可能返回 `CompData_SC`（共享干净）或 `CompData_UD_PD`（脏数据/部分脏）。

**修复**：

```sm
tbe.expected_req_resp.addExpectedDataType(CHIDataType:CompData_UC);
tbe.expected_req_resp.addExpectedDataType(CHIDataType:CompData_SC);
tbe.expected_req_resp.addExpectedDataType(CHIDataType:CompData_UD_PD);
```

**根因性质**：这是 **HN-F 输入假设过窄**。v4 方案允许 EP-SNF/EP-RNF 路径按实际权限与脏位返回不同 `CompData_*`，而不是强制统一成 UC。

**影响**：该修复避免了合法返回数据被 HN-F 当作“非预期响应”而卡死或报错，但它只解决**消息类型兼容性**，不解决跨节点一致性本身。

### 2.2 Fix B：`ep/UBCCController.cc:1191`

**问题**：`processClear()` 原先把 `Clear.epoch` 与 `GRANT_HANDSHAKE.reservedEpoch` 比较。根据 `scheme_v4.md` §3.1/§3.5，`Clear` 携带的是 requester 观察到的 **baseEpoch**，不是 home 侧预留但尚未 commit 的 `reservedEpoch`。

**修复**：

- 从“比较 `reservedEpoch`”改为“比较 `baseEpoch`”

**根因性质**：这是 **reserve-then-commit 语义落地错误**。v4 把 committed epoch 与 reserved epoch 明确拆开，但实现曾把两者混同。

**影响**：该修复消除了大量“Clear 明明是正确事务却被 home 当作 epoch mismatch 丢弃”的假阴性。

### 2.3 Fix C：`ep/UBCCController.cc:1195`

**问题**：当 `Clear` 因 epoch 不匹配或其他原因被拒绝时，陈旧 `GRANT_HANDSHAKE` 仍残留在 outstanding 表里，后续同 PA 请求持续命中 BUSY。

**修复**：

- 对陈旧 `GRANT_HANDSHAKE` 执行 `retireToTombstone(*ost, false)`
- 再 `removeOutstanding(line_pa)`

**根因性质**：这是 **失败路径缺少垃圾回收**。握手失败后没有进入 tombstone/replay 语义，而是留下“半死不活”的活跃屏障。

**影响**：该修复能降低永久堵塞概率，但它仍只是**后果清理**。如果上游 Recall/Grant/Clear tuple 链继续错配，新的陈旧握手还会继续产生。

---

## 3. Unresolved Issues

### 3.1 Issue 4：DONE RECALL 生命周期错误 —— `processOuterRequest()` 过早消费终态 outstanding

#### 现象

- `drift_in_progress.md` 的 D-11 已明确记录：Recall 完成后，requester retry 本应进入 `GRANT_HANDSHAKE`，但实际再次触发 recall 或直接 BUSY
- 用户已指出关键窗口在 `ep/UBCCController.cc:144-159`
- 当前尝试修复为“**不删除 terminal outstanding**”，让 `G_E/G_M` 重试路径能在 `312+` 行通过 `recallAlreadyDone` 识别它
- 但即便做了这一步，**TC2 仍在 all-DSM-through-EP_SNF 模式下死锁**

#### 直接根因

`processOuterRequest()` 的入口逻辑曾把 terminal outstanding（包括 `RECALL.DONE`）提前移除，导致后续 `G_E/G_M` 分支根本看不到“此 recall 已完成”的证据。结果是：

1. `processRecallResponse()` 把 RECALL 设为 `DONE`
2. committed `DirEntry` 仍保持旧 owner（这是 reserve-then-commit 的设计要求）
3. requester 重试时，`processOuterRequest()` 又看到 `G_E/G_M + existingOwner!=requester`
4. 因为 DONE RECALL 已被提前删掉，`recallAlreadyDone` 无法命中
5. home 重新走 recall 或返回 BUSY

这等价于把本应是：

```text
RECALL.DONE -> GRANT_HANDSHAKE -> WAITING_CLEAR -> commit
```

实现成了：

```text
RECALL.DONE -> 删除证据 -> 再次按旧目录判定 -> 重新 RECALL / BUSY
```

#### 为什么“保留 DONE RECALL”仍不足以让 TC2 通过

因为当前问题不只是“看不见 DONE RECALL”，而是**Recall→Grant 的就地转换语义仍不完整**：

- `ep/UBCCController.cc:313-350` 采用“把 `RECALL` outstanding 原地改成 `GRANT_HANDSHAKE`”的方式
- 这种做法虽然绕过了“同 PA 不允许双 outstanding”的限制，但也把两个阶段的语义硬塞进了同一个对象
- 一旦旧 RECALL 上残留字段与新 GRANT_HANDSHAKE 所需 tuple 不完全一致，就会出现：
  - grant 已逻辑上放行，但 requester 侧没有形成正确 `Clear(pa, baseEpoch, reqId)` 上下文
  - 或者 `Clear` 到达 home 时与原地转换后的 outstanding tuple 不匹配
  - 或者 prerequisite 已 DONE，但 grant/data 可见性并未真的建立

换言之，**“保留 terminal outstanding”只修复了可见性证据，不保证闭环语义一致**。

#### 更深层根因

Issue 4 本质上是 **UBCC outstanding 生命周期模型与 reserve-then-commit 提交流程没有完全对齐**：

- committed 目录不能在 recall 完成时修改，这是对的
- 但如果 committed 目录不改，就必须由 outstanding 完整承载“下一步该 grant 什么、等待谁的 Clear、使用哪个 tuple commit”
- 当前实现对 `RECALL -> GRANT_HANDSHAKE` 的承载不够强，导致“旧目录仍旧、新目录未 commit、握手上下文又不完整”的悬空态

#### 当前状态判断

Issue 4 已从“完全未闭环”进展到“部分识别 RECALL.DONE”，但仍未形成稳定可提交路径。它仍是 **P0 级控制面阻塞问题**。

---

### 3.2 Issue 5：跨节点数据可见性错误（TC2/TC7/TC11）

#### 现象

这类失败的统一表现是：

- 远端写完成后，另一个节点的读拿到旧值或 0
- 当采用 D-10 workaround 后，本地 home 读会走 `DL_SNF -> DDR4`
- 但 DDR4 中仍是旧数据，因为真实新数据留在某级 cache / Ruby hierarchy 中

用户给出的观察与 drift log D-12 一致：

- `sync_wait(syscall 436)` 只提供线程同步，不做 cache maintenance
- ARM `dc cvac/civac/cvau` 在 gem5 当前 Ruby CHI/SE 组合下会崩溃
- 通过 1MB buffer 写入诱导 eviction 也不可靠

#### 直接根因

在 D-10 之后，TC1 避开了“本地 DSM 走 EP_SNF 导致 EP-RNF 被错误注册”的死锁，但同时也把**本地可见性来源**切回了 `DL_SNF -> DDR4`。这立刻暴露出一个更基础的问题：

> **cross-node 写入后的最新数据没有被可靠写回到 home DDR4，因此任何依赖 DDR4 作为 home truth 的读都会看到旧值。**

这说明当前系统缺的不是一个 barrier，而是**从 Ruby cache hierarchy 到 home backing store 的受控可见性机制**。

#### 为什么现有尝试都无效

1. **`sync_wait` 无效**
   - `decisions.md` D-18 把它定义为 `dmb osh + spin on DSM load`
   - 它能约束 CPU 指令顺序，甚至验证“读最终能否看到值”
   - 但它**不能强制 Ruby CHI cache line writeback**
   - 所以它是同步手段，不是 cache maintenance 手段

2. **ARM `dc cvac/civac/cvau` 崩溃**
   - 说明 gem5 SE mode 下，当前 Ruby CHI 模型没有为这些 cache maintenance 指令提供可用实现路径
   - 这不是 workload 写法问题，而是**模拟器能力缺口**

3. **大 buffer 驱逐无效**
   - eviction 是概率性、间接性的
   - 不能证明目标行被逐出，更不能证明被正确写回到 home-node DDR4

#### 更深层根因

Issue 5 是一个 **架构层真值源不一致** 问题：

- D-10 让本地路径把 **DDR4** 当作最终数据源
- 但跨节点写回路径又没有保证 dirty line 会按测试需要及时落到 DDR4
- 于是 control plane 可能已经正确授权，data plane 却仍停留在旧的 cache residency 中

这直接破坏了以下测试：

- **TC2/TC7/TC11**：读到旧数据
- **TC5**：跨节点串行化依赖“上一轮写入已对下一节点可见”，但该可见性并未建立

#### 与 D-10 的关系

D-10 不是 Issue 5 的根因，但它把 Issue 5 从隐蔽状态变成了主导故障：

- **不做 D-10**：TC1 死在本地 DSM 误入 EP_SNF 的控制面错误上
- **做了 D-10**：TC1 活了，但 cross-node 测试开始大面积暴露“DDR4 不是最新值”的数据面错误

因此 D-10 的性质是：**有效的本地死锁规避，但带出了数据可见性赤字**。

#### 当前状态判断

Issue 5 是 **P0/P1 之间的体系结构缺口**。如果不补上 writeback/flush 语义，即使 Issue 4 与 Issue 6 修好，很多 cross-node 读写测试依然会因 stale DDR4 而失败。

---

### 3.3 Issue 6：`handleRecallRequest()` 绕过 HN-F

#### 现象

`scheme_v4.md` §4.2.3 明确规定：

- read recall：`EP-RNF.startReadShared()`
- write recall：`EP-RNF.startReadUnique(..., EpProxyOp::RecallUnique)`
- **禁止以 `functionalRead` 代替 write recall invalidate**

但 `ep/EPBackend.cc:1024-1177` 当前实现仍然：

- 用 `functionalRead()` 从 Ruby hierarchy 抽数据
- 把数据广播写入所有节点的 `phys_mem`
- 直接构造 `OuterRecallResponse`
- 未经 `EP-RNF -> HN-F -> CHI ReadUnique` 的 owner invalidation 闭环

#### 直接根因

当前 `handleRecallRequest()` 只解决了“**能不能把数据抄出来**”，没有解决“**旧 owner 的权限是否真的被 CHI 撤销**”。

对于 write recall，scheme_v4 要求的不是读数据，而是：

1. 通过 `EP-RNF.startReadUnique()` 向本地 HN-F 发起 CHI 事务
2. HN-F 对旧 owner/L2 发 `SnpUnique` 或等价 invalidating 流程
3. owner L2 被真正打到 `I`
4. 脏数据按 CHI 语义回流
5. 之后 home UBCC 才能把 recall barrier 视为完成

而当前实现把这条路径替换成了：

```text
functionalRead cache -> 复制数据 -> 直接响应 RecallDone
```

这满足了“取到数据”，却**没有满足权限回收**。

#### 为什么这是严重架构违例

因为它破坏了 v4 的核心不变量：

- **外部协议不能绕过 HN-F 改写本地缓存权限**
- **Recall 的完成条件不是“拿到一份数据”，而是“旧 owner 已按 CHI 失权”**

一旦绕过 HN-F，就会产生以下后果：

1. **UBCC 认为 recall 已完成**，但旧 owner 的 L2 可能仍保有可写副本
2. 新 requester 可能收到 grant，而旧 owner 还没被 invalidate
3. 后续本地 writeback/evict/snoop 会与 UBCC 目录状态不一致
4. 某些路径看起来像“recall rejected”或“grant 卡住”，本质上是**home 在等待一个从未真正发生的本地 CHI 失权事件**

#### 与 Issue 4 的耦合

Issue 6 会显著放大 Issue 4：

- Issue 4 让 Recall→Grant 生命周期容易悬空
- Issue 6 则让“Recall 已完成”这个前提本身就不可信

于是 home 侧即使看到了 `RECALL.DONE`，也不意味着可以安全进入 `GRANT_HANDSHAKE`。这就是 TC6/TC8/TC4 这类 recall-heavy 测试容易表现为“recall rejected / handshake 不收敛”的原因。

#### 当前状态判断

Issue 6 是 **P1 级架构一致性错误**。它不一定在所有路径上立刻表现为死锁，但它会持续制造“权限已交接”的假象，从而污染整个跨节点协议闭环。

---

## 4. Failure Pattern Map

### Pattern A：TC2 / TC7 / TC11 / TC3

**主导根因：Issue 5（数据没有可靠到达 DDR4）**

这组测试都依赖“一个节点写、另一个节点或 home 节点随后读到新值”。当前失败不是单纯排序错误，而是**最终读路径回到了 stale DDR4**。

### Pattern B：TC6 / TC8 / TC4

**主导根因：Issue 4 + Issue 6（recall 生命周期不闭环 + write recall 绕过 HN-F）**

这组测试 recall、upgrade、invalidate 交织更重，对“旧 owner 真正失权”要求更高。当前实现里：

- home 侧 recall 阶段定义不稳
- owner 侧又未通过 HN-F 真正 invalidate

因此最容易表现为 recall 被拒绝、grant 不落地、或后续 HN-F panic。

### Pattern C：TC5

**主导根因：Issue 5（跨节点串行化失败）**

TC5 需要前一节点的写入成为后一节点读/写的稳定前置条件。当前 barrier 只保证线程推进，不保证 cache line 已对 home backing store 或其他节点可见，所以序列化语义失真。

---

## 5. Architectural Assessment

### 5.1 对 D-10 的判断

D-10（EP_SNF 排除本地 DSM）本质上是一个**workaround**，不是最终架构收敛点。

它解决的问题是：

- 本地 DSM 不再误走 `EP_SNF -> EPBackend -> UBCC`
- TC1 不再因 EP-RNF 被错误注册而死锁

但它带来的副作用是：

- home-node 本地读重新依赖 `DL_SNF -> DDR4`
- 跨节点写入若未 writeback，则 DDR4 一定陈旧
- 因而 cross-node 测试被迫暴露出数据可见性短板

所以 D-10 的准确评估应为：

> **它是“避免 TC1 立即死锁”的必要止血，不是“满足 v4 架构”的充分修复。**

### 5.2 两条可行路线

#### Path A：修回 all-DSM-through-EP_SNF

目标是让本地与远端 DSM 都经 EP 边界，从而避免 home-node 读直接落到 DDR4 的 stale 问题。

前提是必须修好：

- `SnpShared -> EP-RNF` 不应成为常规路径
- DCT fallback 必须在 sole-EP-RNF sharer 情况下稳定生效
- `pickSharerForSnoop()` 必须避免把 EP-RNF 作为 `SnpShared` 正常目标

**优点**：

- 更接近 EP 作为统一可见性边界的原始方案
- 不依赖 gem5 SE/Ruby 新增 cache maintenance 指令支持

**风险**：

- 需要重新打开此前导致 TC1 死锁的那条路径
- 一旦 DCT fallback / snoop target 选择仍有残缺，会再次退回 D-1/D-17 类问题

#### Path B：保留 D-10，补 cache maintenance / writeback 语义

目标是接受“本地 home 读走 DDR4”这一现实，然后确保跨节点写在 barrier 之前可靠刷回。

所需能力：

- 在 gem5 Ruby CHI cache model 中实现 ARM `dc cvac` / 等价 writeback 支持
- 或提供一条专门的 Ruby-aware flush/writeback API 供测试或 runtime 调用

**优点**：

- 不必重新引入 TC1 的本地路由死锁风险
- 问题边界清晰：把数据可见性补到 backing store

**风险**：

- 需要修改 gem5 模拟器能力，不只是协议代码
- 即使可见性修好，Issue 4/6 仍必须单独修复

### 5.3 综合判断

从工程可控性看，**Issue 4 与 Issue 6 无论选 Path A 还是 Path B 都必须修**；Issue 5 则决定是回到“EP 统一边界”还是“DDR4 作为 home 真值源”。

如果当前目标是尽快恢复 cross-node 可调试性，较稳妥的顺序是：

1. 先修 Issue 4，确保 UBCC 控制面不再永久悬空
2. 再修 Issue 6，确保 recall 完成条件重新可信
3. 最后在 Path A / Path B 之间做数据可见性收敛

---

## 6. Recommended Path Forward

### P0：继续修复 Issue 4（RECALL 生命周期）

当前“保留 DONE RECALL”只是第一步。下一步应聚焦：

- 不再把 `RECALL -> GRANT_HANDSHAKE` 仅作为字段原地篡改处理
- 明确保证转换后 outstanding 的 `(baseEpoch, reqId, intendedState, intendedOwner, intendedSharers)` 完整一致
- 验证 requester 侧确实按该 tuple 发送 `Clear`
- 对 `RECALL.DONE -> grant issued -> Clear accepted` 全链路加审计日志

### P1：修复 Issue 6（write recall 必须走 HN-F）

把 `ep/EPBackend.cc:1024-1177` 的 write recall 改成：

- `EP-RNF.startReadUnique(ownerLocalPa, EpProxyOp::RecallUnique)`
- 等本地 HN-F/CHI 事务完成后再发送 `OuterRecallResponse`
- 禁止使用 `functionalRead + phys_mem broadcast` 作为 write recall 正式路径

### P2：修复 Issue 5（数据可见性）

在架构选择上尽快二选一：

- **Path A**：修通 `SnpShared -> EP-RNF` 问题，使 all-DSM-through-EP_SNF 成立
- **Path B**：在 gem5 Ruby CHI 中补出可用 cache writeback / cache maintenance 支持，使 `sync_wait` 前后的数据可落到 DDR4

若没有这一步，TC2/TC5/TC7/TC11 类测试即使控制面修好，也仍会因 stale data 失败。

---

## 7. Final Assessment

CC-EP gem5 v4 当前的失败图谱并不随机，反而高度一致：

- **Fix A/B/C** 已经清除了三类“协议 tuple 与残留状态”问题
- **Issue 4** 说明 UBCC 的生命周期闭环仍未真正成立
- **Issue 5** 说明数据面还没有可依赖的跨节点可见性落点
- **Issue 6** 说明 recall 路径仍在违反 v4 的核心架构约束

因此，现阶段最准确的判断是：

> **v4 已具备单节点/局部路径可运行性，但跨节点协议仍处于“控制面半闭环 + 数据面未收敛 + recall 架构违规”的中间态。**

只有在 **Issue 4、Issue 6、Issue 5** 按上述顺序收敛后，TC2-TC11 才有可能从“全面失败”转为“按子路径逐项恢复”。
