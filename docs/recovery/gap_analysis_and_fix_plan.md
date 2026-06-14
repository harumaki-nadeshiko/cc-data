# Gap Analysis and Fix Plan：`error_root_cause_v4.md` × `recall_spec_v4.md`

## 1. recall_spec_v4 已覆盖的 bug

结论先说：**`recall_spec_v4` 在架构层面覆盖了 `error_root_cause_v4` 的三大主问题（Issue 4/5/6），但没有覆盖 TC1 本地路由根因、D-10 workaround、自测隔离等外围问题。**

### 1.1 覆盖情况总表

| 项目 | recall_spec_v4 是否覆盖 | 结论 | 说明 |
|---|---|---|---|
| Issue 4：DONE RECALL 生命周期 | 是，且是核心内容 | **覆盖** | spec 明确了 `RECALL.DONE -> GRANT_HANDSHAKE(WAITING_CLEAR)`、retry 复用原 `(epoch, reqId)`、以及 Clear 才 commit 的闭环。 |
| Issue 5：数据可见性 | 是，且是核心内容 | **覆盖** | spec 把 grant 数据源收敛为 `RecallBuffer / HomeMemoryService / NoData`，并明确禁止 `functionalRead + phys_mem broadcast` 作为正式路径。 |
| Issue 6：`handleRecallRequest()` 绕过 HN-F | 是，且是核心内容 | **覆盖** | spec 把 recall 定义为必须经 `EP-RNF -> HN-F -> L2` 的正规回收路径。 |
| Fix A | 部分 | **兼容且仍必须保留** | spec 没有替代它；它仍是 HN-F 接收合法 `CompData_*` 组合的必要修正。 |
| Fix B | 是 | **已被 spec 语义吸收** | spec 的 Clear 语义本来就是“匹配 baseEpoch，不匹配 reservedEpoch”。 |
| Fix C | 是 | **已被 spec 语义吸收** | spec 的 handshake 生命周期要求 stale `GRANT_HANDSHAKE` 可退休，不能永久堵塞 PA。 |

### 1.2 Issue 4：DONE RECALL 生命周期

`error_root_cause_v4` 指出的问题是：home 端虽然能看到 `RECALL.DONE`，但 **retry 后没有稳定进入 `GRANT_HANDSHAKE -> WAITING_CLEAR -> commit`**，导致重复 recall、BUSY 卡死、或 Clear tuple 悬空。

`recall_spec_v4` 对这个问题给出了明确闭环：

- `GRANT_HANDSHAKE` 是显式阶段，而不是“DONE recall 的模糊后继”；
- retry 命中 `DONE` recall 后，应转入 `GRANT_HANDSHAKE`；
- `timeout/retry` 必须复用同一 `(linePa, epoch, reqId)`；
- Clear 才是 commit 点；
- even during `GRANT_HANDSHAKE`，buffer eviction 也有定义，不允许把握手上下文弄丢。

因此：**Issue 4 在文档层面已被覆盖。**

但当前代码里仍能看到残留风险：

- `UBCCController.cc:321-350` 仍采用 **in-place** 的 `RECALL -> GRANT_HANDSHAKE` 转换；
- `processClear()` 虽已按 `baseEpoch` 匹配（`UBCCController.cc:1189-1201`），但 transition 本身仍缺少“新 handshake record 的强校验”。

所以更准确地说：**spec 覆盖了 Issue 4；当前实现只修到了半步。**

### 1.3 Issue 5：数据可见性

`error_root_cause_v4` 的问题定义是：跨节点读写不能再依赖 stale DDR4；dirty data 没有稳定落到 home 真值入口。

`recall_spec_v4` 的对应修法非常直接：

- grant 数据源改为 **`RecallBuffer / HomeMemoryService / NoData`**；
- `HomeMemoryService.read()` 先查 buffer，再查 DRAM；
- 删除 recall 正式路径中的 `functionalRead + phys_mem broadcast`；
- `populateGrantData()` 不再做全系统 probe，而改成按 `GrantDataSource` 取数。

这正好对上 `error_root_cause_v4` 的核心判断：

- 不能把 stale DDR4 当跨节点读真值；
- 不能靠 opportunistic `functionalRead()` 假装“数据已经可见”。

因此：**Issue 5 在架构层面被 recall_spec 明确覆盖。**

但要注意一个关键限制：**如果 D-10 继续保留、HN-F 仍允许本地 DSM 走 `DL_SNF -> DDR4`，那 recall_spec 的数据真值入口会被本地旁路绕开，Issue 5 仍会以另一种形式残留。**

### 1.4 Issue 6：`handleRecallRequest()` 绕过 HN-F

当前实现中最明显的违背点在：

- `EPBackend.cc:1069-1177` 仍用 `functionalRead()` 抓 owner 数据；
- 之后还把数据 broadcast 到各节点 `phys_mem`；
- 然后直接 `sendRecallResponse()`，并没有把 write recall 建立在 `EP-RNF -> HN-F -> L2` 的权限回收事实上。

这正是 `error_root_cause_v4` 所说的“RecallDone 不可信”。

`recall_spec_v4` 对应给出的修法是：

- **write recall 不是 `functionalRead`**；
- read recall / write recall 都必须回到 EP-RNF 发起、HN-F snoop/收数、owner L2 真失权的路径；
- home 只在收到可靠 recall completion 后才进入 grant/clear 闭环。

因此：**Issue 6 被 recall_spec 直接覆盖。**

### 1.5 Fix A / B / C 状态

#### Fix A

当前已落在 `CHI-cache-actions.sm:1601-1604`：`Send_ReadNoSnp` 接受 `CompData_UC/SC/UD_PD`。

判断：

- **不是 recall_spec 的主修复内容**；
- 但 recall_spec 引入的 `GrantDataSource` / home-memory / recall-buffer 路径，仍然需要 HN-F 正确接收这些合法 `CompData_*`；
- 所以 **Fix A 必须保留，不能回退。**

#### Fix B

当前已落在 `UBCCController.cc:1189-1192`：Clear 比较 `baseEpoch` 而不是 `reservedEpoch`。

判断：

- 这与 recall_spec 的 `Clear carries baseEpoch` 完全一致；
- **Fix B 已被 recall_spec 语义吸收。**

#### Fix C

当前已落在 `UBCCController.cc:1193-1201`：Clear mismatch 时 retire stale `GRANT_HANDSHAKE` 到 tombstone。

判断：

- 这与 recall_spec 的 handshake 生命周期要求一致；
- **Fix C 已被 recall_spec 语义吸收。**

---

## 2. recall_spec_v4 **没有覆盖**，或覆盖不完整的剩余问题

### 2.1 D-10 routing workaround 仍是独立 bug

当前配置仍保留了 D-10 风格的混合路由痕迹：

- `CHI_ubcc_framework.py:215-235`：`EP_SNF` 覆盖所有 DSM 窗口；
- `CHI_ubcc_framework.py:320-325`：HN-F downstream 同时包含 `EP_SNF` 和 `DL_SNF`。

这意味着：

- 同一 DSM PA 仍可能存在 **EP_SNF / DL_SNF 双入口心智模型**；
- recall_spec 的 `HomeMemoryService` 真值入口无法真正收敛；
- Issue 5 会被“本地旁路回 DDR4”重新污染。

**结论：D-10 workaround 不属于 recall_spec 覆盖范围，必须单独修。**

### 2.2 `pickSharerForSnoop` / DCT fallback / `SnpShared -> EP-RNF`

这是 recall_spec 之外的另一组 P0 根因。

当前代码仍有明显残缺：

- `CHI-cache-funcs.sm:926-943`：`pickSharerForSnoop()` 虽先排除 EP-RNF，但 **candidate 为空时仍回退到 `sharers.smallestElement()`**，可能再次选回 EP-RNF；
- `EPRNFController.cc:631-639`：`SnpShared/SnpSharedFwd` 仍是 **defensive `SnpResp_SC`**，不是 fatal；
- `drift_in_progress.md` 的 D-1 / D-17 说明这一条路径仍被真实触发。

`recall_spec_v4` 默认假设 recall 路径正确，但 **并不修 HN-F 的 sharer 选路逻辑**。

**结论：这组 bug 不被 recall_spec 覆盖。**

### 2.3 TC1 在 all-DSM-through-EP_SNF 下的死锁根因

`local_dsm_routing_v4.md` 已给出结论：

- TC1 真根因不是“本地 DSM 不能走 EP_SNF”；
- 而是“走 EP_SNF 后，HN-F 错把 EP-RNF 当成常规 preserving-query / fwd snoop 目标”。

也就是说：

- recall_spec 可以把 cross-node recall 做对；
- 但 **不能保证 TC1 在撤销 D-10 后立刻可跑**。

**结论：TC1 deadlock root cause 不被 recall_spec 解决。**

### 2.4 `RECALL -> GRANT_HANDSHAKE` 的 epoch/reqId tuple 匹配仍有实现缺口

文档层面，recall_spec 已要求 retry 复用原 tuple；
但实现层面仍有两个缺口：

1. `UBCCController.cc:321-350` 仍是 in-place mutation；
2. 当前 transition 没有把“旧 RECALL 的 terminal identity”和“新 GRANT_HANDSHAKE 的 committed matching tuple”彻底拆开验证。

所以这个问题应判定为：

- **文档层：已覆盖**；
- **代码层：仍未完全解决**。

若只“照着 recall_spec 方向改一半”，这个 bug 仍可能残留。

### 2.5 Self-test stubs / self-test 隔离

这部分明显不在 recall_spec 范围内，但当前实现确实有问题：

- `M4SelfTest.cc` ~ `M8SelfTest.cc` 全部被 stub 成空函数；
- `EPBackend::init()` 仍无 `_enableSelfTest` 守卫（`EPBackend.cc:103-109` 仍直接调用）；
- `EPBackend.py` 目前只有 `node_id` 和 `ruby_system` 两个参数，**没有** `enable_self_test`；但 `tests/e2e/test_e2e.py:700-704` 仍在写 `be.enable_self_test = False`；
- 这意味着文档里的 D-17“self-test / workload 分离”并未真正落地。

额外地，`EPSNFController.cc:35-55` 还有一个常开的 `selfTest()` 注入路径，也会污染 workload 模式。

**结论：self-test 问题完全不被 recall_spec 覆盖。**

### 2.6 `alloc_on_readshared=True` 的副作用仍未完全收敛

`drift_in_progress.md` 的 D-9 仍把它标为“待诊断确认”。

它不是 recall_spec 的主目标，但如果：

- L3 shared caching 与 `shared_hint` / EP-RNF 注册 / 本地 upgrade 时序耦合仍有残留；
- 那么 TC1 / TC6 / TC11 仍可能出现边界问题。

**结论：这是 recall_spec 之外的中风险残留项。**

### 2.7 workload flush / barrier workaround 不是协议修复

`drift_in_progress.md` 的 D-12（`dc civac` flush）与 D-18（`sync_wait` barrier）更像测试/workload 辅助手段，而不是协议正确性的替代品。

即使 recall_spec 落地，仍需明确：

- 哪些 TC 依赖显式 cache maintenance；
- 哪些 TC 应在纯协议正确性下通过。

否则容易出现“测试通过，但靠的是 workload flush，而不是 coherence 真闭环”。

---

## 3. Complete Fix Plan（按依赖排序）

### 3.1 总体顺序

建议顺序：

1. **先清理测试干扰**，保证后续观察可信；
2. **先修 TC1 根因**，否则无法撤销 D-10；
3. **撤销 D-10，恢复单入口 DSM 路由**；
4. **修 Issue 4：Recall 生命周期闭环**；
5. **修 Issue 6：Recall 必须经 HN-F**；
6. **修 Issue 5：GrantDataSource + HomeMemoryService**；
7. **最后做 workload / self-test / regression 收尾**。

### 3.2 详细计划表

| 优先级 | Fix | 主要文件 | 估计改动 | 前置依赖 | 主要解锁 TC |
|---|---|---|---:|---|---|
| P0 | Self-test 隔离真正落地 | `ep/EPBackend.py`, `ep/EPBackend.hh`, `ep/EPBackend.cc`, `tests/e2e/test_e2e.py`, `tests/e2e/test_self_test.py`, `ep/M4-8SelfTest.cc`, `ep/EPSNFController.cc` | 60-140 LOC | 无 | 让 **TC2-TC11** 的日志/死锁诊断可信 |
| P0 | HN-F snoop 目标选择修正 | `CHI-cache-funcs.sm`, `CHI-cache-actions.sm` | 50-100 LOC | 上一项建议先做 | **TC1**，并为 TC6/TC8/TC11 打基础 |
| P0 | 恢复 EP-RNF snoop fatal 不变量 | `ep/EPRNFController.cc` | 10-20 LOC | HN-F snoop 目标修正 | 防止 `SnpShared -> EP-RNF` 被 workaround 掩盖；保护 **TC1/TC11** |
| P0 | 撤销 D-10，恢复 all-DSM-through-EP_SNF | `configs/ruby/CHI_ubcc_framework.py`, 视实现可能加 `ep/EPSNFController.cc/.hh` | 20-70 LOC | HN-F snoop 目标修正 | **TC1** 架构正确版；也是 **TC2/3/5/6/7/8/11** 前置 |
| P1 | Issue 4：Recall→Grant→Clear 生命周期重写 | `ep/UBCCController.hh`, `ep/UBCCController.cc`, `ep/EPBackend.cc` | 120-220 LOC | 撤销 D-10 后更有验证价值 | **TC2/3/4/6/8/11** |
| P1 | Tuple hardening：`(baseEpoch, reqId)` 在 transition 中强校验 | `ep/UBCCController.cc`, `ep/EPBackend.cc` | 40-80 LOC | 上一项 | 进一步稳定 **TC2/3/4/6/8** |
| P1 | Issue 6：删除 `functionalRead + phys_mem broadcast` recall 正式路径 | `ep/EPBackend.hh`, `ep/EPBackend.cc`, `ep/EPRNFController.hh`, `ep/EPRNFController.cc` | 150-300 LOC | Recall 生命周期闭环 | **TC2/4/6/8**，并为 Issue 5 打前置 |
| P1 | Issue 5：`GrantDataSource + HomeMemoryService` | `ep/EPBackend.hh`, `ep/EPBackend.cc`, `ep/EPSNFController.hh`, `ep/EPSNFController.cc`, `ep/UBCCController.hh`, `ep/UBCCController.cc` | 220-420 LOC | Issue 4 + Issue 6 + all-DSM-through-EP_SNF | **TC2/3/5/7/11**，并改善 **TC4/TC6/TC8** |
| P2 | 清理 `populateGrantData()` 的 probe/scavenge/非零启发式遗留 | `ep/EPBackend.cc` | 80-180 LOC（通常并入上一项） | `GrantDataSource` | 提升 **TC2/3/5/7** 正确性与可解释性 |
| P2 | 验证/收尾：恢复真实 self-tests，限定 workload flush 的使用边界 | `ep/M4-8SelfTest.cc`, `tests/e2e/workloads/dsm_access.h`, `tests/e2e/workloads/e2e_tc2_remote_read.c`, 可能含其他 workload | 30-100 LOC | 核心协议修完后 | 提升回归可信度；避免假阳性 |

### 3.3 每项 fix 的说明

#### Fix 1：Self-test 隔离真正落地

当前文档与代码不一致：

- 文档说有 `enable_self_test`；
- 代码里 `EPBackend.py` 没这个参数；
- M4-M8 被 stub 掉只是临时诊断措施；
- `EPSNFController::selfTest()` 还在常开。

目标：

- workload 模式下完全无自测干扰；
- self-test 模式下恢复真正的 M4-M8 校验；
- E2E 与 self-test 分开跑。

#### Fix 2：HN-F snoop 目标选择修正

要点：

- `pickSharerForSnoop()` 不能在候选集为空时回退到 EP-RNF；
- sole-EP-RNF 时必须走 DCT fallback / non-Fwd 路径；
- `SnpShared/SnpSharedFwd/SnpOnceFwd -> EP-RNF` 必须重新变成不可达。

这是 **撤销 D-10 的前置条件**。

#### Fix 3：撤销 D-10

目标：

- HN-F 对 DSM 只看见 `EP_SNF` 一个 downstream；
- `DL_SNF` 不再作为 HN-F 的 DSM 公开路由目标；
- 本地 DSM 与远程 DSM 一律走 EP 入口。

否则 recall_spec 的数据真值入口永远不能完全生效。

#### Fix 4：Issue 4 生命周期闭环

目标：

- `RECALL.DONE` 不是“旧 outstanding 上改几个字段就算了”；
- 必须有稳定的 `GRANT_HANDSHAKE(WAITING_CLEAR)` 承载；
- Clear 之前 committed DirEntry 不变；
- timeout/retry 复用同一 tuple；
- stale handshake 可退休。

#### Fix 5：Tuple hardening

目标：

- 明确区分 `baseEpoch` / `reservedEpoch`；
- 明确记录 `reqId` 属于哪次 requester-visible transaction；
- `RECALL -> GRANT_HANDSHAKE` 时做一致性校验，而不是隐式继承。

这是对 Fix B/C 的加强版，而不是替代。

#### Fix 6：Issue 6，无 bypass recall

目标：

- read recall 走 `EP-RNF.startReadShared()`；
- write recall 走 `EP-RNF.startReadUnique(... RecallUnique)`；
- owner 真正经 HN-F/L2 丢权；
- home 只接受真实 completion，不接受 `functionalRead()` 假完成。

#### Fix 7：Issue 5，数据真值入口收敛

目标：

- `UBCC` 只决定权限和 `GrantDataSource`；
- `EPBackend` 提供 `HomeMemoryService.read/write`；
- `populateGrantData()` 只按 `GrantDataSource` 取 `RecallBuffer/HomeMemory/NoData`；
- 删除全系统 probe、跨节点 phys_mem broadcast、first-word 非零启发式。

### 3.4 TC 解锁映射

| TC | 主要依赖 fix | 备注 |
|---|---|---|
| TC1 `dsm_local` | Fix 2 + Fix 3 | recall_spec 本身不够，必须先修 HN-F 选路 |
| TC2 `remote_read` | Fix 4 + Fix 6 + Fix 7 | 最典型的 Recall→Grant→Clear + 数据可见性路径 |
| TC3 `pingpong` | Fix 4 + Fix 5 + Fix 6 + Fix 7 | 高强度 owner 迁移，最能暴露 tuple/race 问题 |
| TC4 `three_node_ring` | Fix 4 + Fix 5 + Fix 6 | 时序最敏感，Issue 4/6 先收敛 |
| TC5 `single_writer` | Fix 7 | 主要看跨节点写后可见性/串行化 |
| TC6 `multi_sharer` | Fix 2 + Fix 4 + Fix 6 | 多 sharer + invalidate/recall |
| TC7 `writeback_evict` | Fix 7 | 需要 home 真值入口稳定 |
| TC8 `upgrade_invalidate` | Fix 2 + Fix 4 + Fix 5 + Fix 6 | upgrade + invalidate + race window |
| TC11 `local_upgrade` | Fix 2 + Fix 3 + Fix 4 + Fix 7 | 本地升级与 EP-RNF 注册/握手强相关 |
| TC9 `non_dsm_negative` | 基本无直接依赖 | 主要做回归确认 |
| TC10 `concurrent_atomic` | 本计划未直接覆盖 | 需单独验证 atomic path |

---

## 4. 完成上述 fix plan 后，仍然存在的已知缺口

### 4.1 仍不是 full C2C

即使全部按上面修完，体系仍是 **home-centric recall**，不是 owner→requester direct C2C。这个不是 bug，而是当前阶段明确不做的范围。

### 4.2 TC9/TC10 仍缺少直接证明

本轮 fix plan 聚焦 recall / grant / visibility / routing：

- `TC9` 只是负例回归；
- `TC10` 的 atomic 并发路径并不在 recall_spec 主覆盖面内。

所以即使 TC1/2/3/4/5/6/7/8/11 恢复，**TC10 仍应被视为“未充分验证”。**

### 4.3 极端竞态仍缺专门测试

即使 fix plan 完成，以下 race 仍需要专门 case 才能说“证据充分”：

- 两个节点同时 `ReadUnique` 同一行；
- recall target 的 L2 在 recall 期间发生 evict/writeback；
- `GRANT_HANDSHAKE` 期间 buffer eviction / timeout / duplicate Clear；
- local upgrade 与 remote recall 交叠。

换句话说：**修完后可以从“明显错误”进入“可验证基线”，但还没到“形式上完全无 race 证明”。**

### 4.4 自测恢复后，self-test 本身也要跟着协议一起维护

当前 M4-M8 已被 stub，说明它们已经落后于 v4 API。即使把自测隔离机制修好，**仍需要补做 self-test 内容升级**，否则它们只能证明“能编译”，不能证明“协议行为正确”。

### 4.5 额外提醒：若不移除 workaround，结论会被污染

若最终仍保留以下任一项，则测试结论应降级：

- `functionalRead + phys_mem broadcast` recall 正式路径；
- `SnpShared -> EP-RNF` defensive `SnpResp_SC`；
- D-10 本地路由 workaround；
- workload flush 作为跨节点可见性的主要证明手段。

这些都属于“测试能跑，但不能证明架构收敛”的状态。

---

## 5. 一句话结论

**`recall_spec_v4` 已经覆盖了 `error_root_cause_v4` 的三大主 bug（Issue 4/5/6）；真正剩下的阻塞项，不在 recall 本身，而在 TC1 路由根因、D-10 撤销、HN-F snoop 选路、tuple 实现细节，以及 self-test / workload 隔离没有真正落地。**
