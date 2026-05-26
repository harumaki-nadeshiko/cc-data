# Test Plan

## 0. TestSpec 编写规则

从本轮开始，后续所有新 testcase 计划与实现都应遵循统一规格，不能只写“动作大概是什么”。

每个 testcase 至少必须明确:
1. `ID`
2. `Purpose`
3. `Harness Type`
4. `Preconditions`
5. `Inputs`
6. `State Injection / Stimulus Method`
7. `Execution Steps`
8. `Observables`
9. `Expected Output / Pass Criteria`
10. `Negative Criteria / Forbidden Outcome`

### 0.1 Harness Type

当前允许两种主测试形态:

| Harness | 用途 | 特点 |
|---|---|---|
| `PY_INJECT` | Python/gem5 侧定向注入 | 适合目录状态安装、控制器消息注入、负例测试 |
| `ARM_SYNC` | ARM workload + `Sync_Wait` | 适合多节点端到端协议闭环 |

### 0.2 观测来源

每个 testcase 至少要明确依赖哪些观测来源:
- Python 断言
- gem5 fatal/assert
- 指定 debug log 关键字
- 明确的 stdout 标记行
- 指定控制器/目录状态读取接口

当前优先级:
1. 指定控制器/目录状态读取接口
2. Python 断言
3. 指定 debug log 关键字
4. stdout 标记行
5. fatal/assert

### 0.3 测试辅助接口规划

为了让 M4/M5 的 testcase 可执行，计划允许引入最小 test-only helper，但必须限定用途。

允许的 test-only helper 方向:
- `installSentinelForTest(line_pa, mode)`
- `removeSentinelForTest(line_pa)`
- `inspectDirEntryForTest(line_pa)`
- `injectEpSnoopForTest(...)`
- `inspectUbccDirForTest(line_pa)`

约束:
- 这些 helper 只能服务测试和调试，不得成为正式协议主路径依赖。
- 若 helper 会污染正式代码路径，reviewer 必须明确指出。

### 0.3.1 Helper 暴露方式

当前选定方案:
- `C++ debug/test hook + Python trigger`

具体要求:
- helper 的状态读取和注入逻辑优先实现为 C++ controller/backend/directory 的 test-only 接口。
- Python testcase 负责触发这些接口并收集返回值。
- 不优先采用“纯 Python 侧维护一套影子状态”的方式。

原因:
- 协议关键状态真实存在于 C++ 实现内部。
- 直接从 C++ 暴露最小接口，能减少 Python 与真实协议状态不一致的风险。
- 也更利于你和后续 Coding Agent 在调试时直接读取中途内部状态。

### 0.3.2 强注入与路径驱动的折中规则

当前选定折中方案:

| 阶段 | 允许的 helper 风格 | 约束 |
|---|---|---|
| `M4` | 可少量使用强注入 | 可直接安装 sentinel/directory 前置状态，但不能直接伪造“测试已通过”的最终结果 |
| `M5` | 以路径驱动为主 | helper 只能补齐难以自然建立的前置条件，不得直接伪造 grant/data/result state |
| `M6` | 以路径驱动为主 | helper 允许建立 owner/sharer/epoch 初始场景，但 recall/data 返回必须走真实协议路径 |
| `M7` | 以路径驱动为主 | helper 允许制造 stale/双事务前置条件，但 writeback/evict/owner transfer 结果必须由真实路径产生 |

统一禁止:
- 直接把 testcase 末态一次性写成 PASS 所需状态
- 直接伪造最终 owner transfer 结果
- 直接伪造最终 read data 返回值

## 1. 回归底座

这些 testcase 是所有后续阶段的必跑回归底座。

| ID | 文件 | 作用 |
|---|---|---|
| `TC1` | `tests/phase1/test_pa_layout_mode.py` | per-node PA 布局守门 |
| `TC2` | `tests/phase1/run_phase1_test.py` | Phase1 兼容基线 |
| `TC2E` | `tests/phase1/run_phase1_test_enhanced.py` | Phase1 增强基线 |
| `TC3` | `tests/phase2/verify_topo_objects.py` | topology 对象级守门 |
| `TC4` | `tests/phase2/test_ruby_create_system_n3l2d2.py` | no-bypass create_system / instantiate |
| `TC5` | `tests/phase3/test_ep_instantiate.py` | EP 最小 instantiate |

规则:
- 任一阶段新增测试通过但 `TC1..TC5` 回退，视为阶段失败。
- `TC2` 与 `TC2E` 暂时都保留；后续可逐步转移主权重到 `TC2E`。

## 1.5 M3.5 TestCase

### `TC-M3.5-1` Multi-Agent Readme Smoke Check

Purpose:
- 验证 orchestrator 会先派发 implementer 修改 `readme.md`，再派发 validator 检查该修改。

Harness Type:
- `ORCH_FLOW`

Preconditions:
- 仓库根目录存在 `readme.md`

Inputs:
- target file = `readme.md`
- target line = `Agent test 666!`

State Injection / Stimulus Method:
- 无协议状态注入
- 本 testcase 只验证多 Agent 协作流程

Execution Steps:
1. orchestrator 调用 implementer
2. implementer 在 `readme.md` 新增一行 `Agent test 666!`
3. orchestrator 调用 validator
4. validator 检查 `readme.md` 是否存在该新增行

Observables:
- `readme.md` 文件内容
- implementer 阶段报告
- validator 阶段报告
- orchestrator 阶段摘要

Expected Output / Pass Criteria:
- `readme.md` 中存在新增行 `Agent test 666!`
- validator verdict = PASS
- orchestrator 记录本阶段通过并暂停等待用户确认

Negative Criteria / Forbidden Outcome:
- orchestrator 跳过 implementer 直接宣称通过
- orchestrator 未调用 validator 就推进
- `M3.5` 通过后自动直接进入 `T0` 或 `M4`

## 2. T0 TestCase

### `TC-T0-1` Barrier Basic Release

Purpose:
- 验证 `Sync_Wait(0b111)` 仅在 3 个显式参与线程都到达后统一释放。

Harness Type:
- `ARM_SYNC`

Preconditions:
- 每个 node 启动 1 个参与线程，总计 3 个参与线程。

Inputs:
- `node_mask = 0b111`
- 每线程调用次数 = 1

State Injection / Stimulus Method:
- ARM workload 在每个参与线程中打印 `BEFORE_BARRIER node=<id>`
- 然后执行 `Sync_Wait(0b111)`
- 返回后打印 `AFTER_BARRIER node=<id>`

Execution Steps:
1. 启动 Node0/1/2 三个参与线程。
2. 三者先后执行到 barrier 前。
3. 最后一个线程进入 barrier 后统一释放。

Observables:
- stdout 标记行顺序
- syscall 返回值

Expected Output / Pass Criteria:
- 必须出现 3 行 `BEFORE_BARRIER`
- 在第三个 `BEFORE_BARRIER` 出现前，不得出现任何 `AFTER_BARRIER`
- 最终出现 3 行 `AFTER_BARRIER`
- 进程退出码为 0

Negative Criteria / Forbidden Outcome:
- 任意线程在 barrier 条件满足前输出 `AFTER_BARRIER`
- 少于 3 个参与线程却完成 barrier

### `TC-T0-2` Barrier Isolation By Node Mask

Purpose:
- 验证不同 `node_mask` 的 barrier 实例互不干扰。

Harness Type:
- `ARM_SYNC`

Inputs:
- barrier A: `0b011`
- barrier B: `0b100`

State Injection / Stimulus Method:
- Node0/1 调用 `Sync_Wait(0b011)`
- Node2 单独调用 `Sync_Wait(0b100)`

Observables:
- stdout 标记顺序

Expected Output / Pass Criteria:
- Node2 的 barrier 完成不依赖 Node0/1
- Node0/1 的 barrier 只依赖彼此

Negative Criteria / Forbidden Outcome:
- barrier A 与 barrier B 共享状态

### `TC-T0-3` Multi-Thread Same Node Count

Purpose:
- 验证同 node 多线程计数正确，且未调用 syscall 的线程不计入。

Harness Type:
- `ARM_SYNC`

Inputs:
- Node0 启动 2 个线程，其中只有 1 个调用 barrier
- Node1/2 各 1 个调用 barrier

Expected Output / Pass Criteria:
- barrier 只等待 3 个实际调用线程
- Node0 未调用线程不会阻塞整个 barrier 收敛

Negative Criteria / Forbidden Outcome:
- 把未调用线程计入 barrier 完成条件

### `TC-T0-4` Reusable Barrier

目标:
- 同一个 barrier 连续使用两轮。

验证点:
- 第二轮不受第一轮 stale 状态影响。

## 3. M4 TestCase

说明:
- 本节中的 `sentinel registration` 一律采用 `plan/00-terminology.md` 的严格定义，即 home-side HN directory 中对 `EP_RNF` 的 insert/update/remove。

### `TC-M4-1` ExternalSharer Triggers Snoop

Purpose:
- 验证 home-side `S_SHARER` 会让本地 unique 请求真实 snoop `EP_RNF`。

Harness Type:
- `PY_INJECT`

Preconditions:
- Node1 为该 line 的 home node。
- 选定 1 条 `DSM_1` line PA，记为 `line_pa_home_view`。

Inputs:
- `line_pa_home_view`
- sentinel mode = `S_SHARER`

State Injection / Stimulus Method:
- 通过 `installSentinelForTest(line_pa_home_view, S_SHARER)` 在 Node1 HN directory 中安装 `EP_RNF` 项。
- 之后向 Node1 本地 requester 注入 1 个 unique/upgrade 类请求。

Execution Steps:
1. 安装 `S_SHARER` sentinel。
2. 读取目录确认 `EP_RNF` 已在 sharer 集合内。
3. 注入本地 unique 请求。
4. 捕获 HN -> `EP_RNF` snoop。

Observables:
- `inspectDirEntryForTest(line_pa_home_view)`
- HN/EP debug log
- `EP_RNF` 接收计数器或 hook

Expected Output / Pass Criteria:
- 注入 unique 请求后，必须观测到 1 次发往 `EP_RNF` 的 snoop
- snoop 目标 machine id 必须是 Node1 的 `EP_RNF`
- 该行为发生在本地 unique 完成之前

Negative Criteria / Forbidden Outcome:
- HN 直接完成本地 unique 而未 snoop `EP_RNF`
- `EP_RNF` 不在 HN 原生 sharer 表达中，却仍声称 `S_SHARER` 已成立

### `TC-M4-2` ExternalOwner Recorded

Purpose:
- 验证 remote unique/owner 建立后，home-side HN 用原生 owner/unique 目录格式记录 `EP_RNF`。

Harness Type:
- `PY_INJECT`

Preconditions:
- Node1 为 home node。
- 选定 `line_pa_home_view`。

State Injection / Stimulus Method:
- 通过最小测试 helper 触发“remote owner granted”收尾路径，禁止直接伪造最终断言而绕过收尾逻辑。

Observables:
- `inspectDirEntryForTest(line_pa_home_view)`

Expected Output / Pass Criteria:
- owner 字段或等价原生 unique 表达指向 Node1 的 `EP_RNF`
- 不存在单独平行 sentinel 专用 owner 容器

Negative Criteria / Forbidden Outcome:
- 需要一套 HN 原生 owner 之外的平行结构才能表达 `S_OWNER`

### `TC-M4-3` ExternalOwner No Local Dirty Owner Coexist

Purpose:
- 验证同一 line 不能同时存在本地 dirty owner 与 `EP_RNF` owner。

Harness Type:
- `PY_INJECT`

State Injection / Stimulus Method:
- 先让本地 cluster 成为 dirty owner
- 再尝试走 remote owner registration 路径

Expected Output / Pass Criteria:
- 系统必须先完成本地 owner 回收/失效，再允许 `EP_RNF` 成为 owner

Negative Criteria / Forbidden Outcome:
- 同一时刻目录同时显示本地 dirty owner 与 `EP_RNF` owner

### `TC-M4-4` Non-DSM Sentinel Rejected

动作:
- 对 LocalPrivate 或 UbccExclusive 地址尝试登记 sentinel。

预期:
- assert/fatal/测试失败。

### `TC-M4-5` Sentinel Remove Works

Purpose:
- 验证 remove 后 HN 不再把 external world 视为目录参与者。

Harness Type:
- `PY_INJECT`

State Injection / Stimulus Method:
- 先安装 `S_SHARER`
- 再执行 `removeSentinelForTest(line_pa_home_view)`
- 然后重放本地 unique 请求

Expected Output / Pass Criteria:
- remove 后目录中不再存在 `EP_RNF`
- 后续本地 unique 不再发往 `EP_RNF` snoop

## 4. M5 TestCase

说明:
- requester 侧若需要本地状态记账，统一视为 `requester-side external-state bookkeeping`，不与 `sentinel registration` 混用。

### `TC-M5-1` ReadShared Sideband Plumbing

Purpose:
- 验证只读 remote miss 通过消息扩展字段携带 `needed_perm=Shared` 和 `write_intent=false`。

Harness Type:
- `ARM_SYNC`

Preconditions:
- T0 `Sync_Wait` 已可用
- Node0 作为 requester，Node1 作为 home
- 选定一条 `DSM_1` line

Inputs:
- Node0: 对该 line 执行纯 load
- Node1: 不先写入 dirty 数据

State Injection / Stimulus Method:
- 通过 ARM workload 在 barrier 后由 Node0 发起 1 次远程只读访问

Observables:
- HN -> `EP_SNF` 请求消息扩展字段
- `EP_SNF` outer request 类型
- stdout 标记

Expected Output / Pass Criteria:
- `ubcc_needed_perm == Shared`
- `ubcc_write_intent == false`
- `EP_SNF` 发出 `GlobalReadShared`

Negative Criteria / Forbidden Outcome:
- 通过 side table 传递 permission
- 只读 miss 却被标记为 `write_intent=true`

### `TC-M5-2` ReadUnique Sideband Plumbing

Purpose:
- 验证带写意图的 remote miss 会携带 `needed_perm=Unique` 与 `write_intent=true`。

Harness Type:
- `ARM_SYNC`

Inputs:
- Node0: 对 remote DSM line 执行 store

Observables:
- HN -> `EP_SNF` 扩展字段
- `EP_SNF` outer request

Expected Output / Pass Criteria:
- `ubcc_needed_perm == Unique`
- `ubcc_write_intent == true`
- `EP_SNF` 发出 `GlobalReadUnique`

Negative Criteria / Forbidden Outcome:
- store 路径仍只携带 `needed_perm=Unique` 但缺少 `write_intent=true`

### `TC-M5-3` Remote First Miss Shared Grant

Purpose:
- 验证 `Shared + write_intent=false` 返回 `GlobalGrantShared + GlobalData`。

Harness Type:
- `ARM_SYNC`

Expected Output / Pass Criteria:
- requester 收到 shared completion
- home UBCC directory 进入 `G_S`
- requester-side bookkeeping 进入 `R_S`

### `TC-M5-4` Remote First Miss Exclusive Or Modified Grant

Purpose:
- 验证 `Unique` 路径在 MESI 下严格区分 `E` 与 `M`。

Harness Type:
- `ARM_SYNC`

Inputs:
- 子场景 A: `Unique + write_intent=false`
- 子场景 B: `Unique + write_intent=true`

Expected Output / Pass Criteria:
- 子场景 A -> `GlobalGrantExclusive`
- 子场景 B -> `GlobalGrantModified`

Negative Criteria / Forbidden Outcome:
- 两个子场景都落到同一种模糊 owner grant

### `TC-M5-5` ForceGrantM Debug Fallback

预期:
- 打开 debug flag 时，shared miss 也可保守走 owner 模式。
- 默认配置下不应依赖该模式。

### `TC-M5-6` Non-DSM Rejection On EP Path

预期:
- non-DSM 地址不能进入 remote miss outer path。

### `TC-M5-7` Minimal Sideband Only

预期:
- sideband 最小必需字段至少能表达 `needed_perm`。
- 不应冗余塞入可由 PA 直接解析得到的 `src_node/home_node` 字段，除非实现报告明确证明确有必要。

### `TC-M5-8` MESI Sideband Sufficiency

Purpose:
- 验证 sideband 最小化策略已经收敛到 `needed_perm + write_intent`。

Harness Type:
- `PY_INJECT`

Expected Output / Pass Criteria:
- 除 `needed_perm` 与 `write_intent` 外，不存在额外为 `E/M` 判定引入的字段
- `src_node/home_node` 仍由 PA 解析，不在 sideband 中冗余出现

## 5. M6 TestCase

### `TC-M6-1` Remote Read Gets Latest Dirty Data

Purpose:
- 验证 remote read 命中 dirty owner 时，最新值通过 recall 路径返回，而不是来自 home UBCC 常驻 data。

Harness Type:
- `ARM_SYNC`

Preconditions:
- `T0` 已完成
- `M5` 已支持 requester/home 的 first miss 闭环
- Node1 为 home
- 选定 `line_pa_node0_view` 和对应 `line_pa_node2_view`

Inputs:
- Phase A: Node0 对 Node1 DSM line 执行 1 次 store，写入值 `0x11223344`
- Phase B: Node2 对同一 logical line 执行 1 次 load

State Injection / Stimulus Method:
- ARM workload 使用 `Sync_Wait` 串行化两个 phase
- 在 Phase A 结束后，使用 `inspectUbccDirForTest(line_pa_home_view)` 读取 home directory

Execution Steps:
1. Node0 获取该 line 的 owner 权限并写入 `0x11223344`
2. 读取 home directory，确认 owner 为 Node0，state 为 `G_M`
3. Node2 发起对同一 line 的 read
4. home 发起 `GlobalRecallOwner`
5. owner Node0 经 `EP_RNF`/HN 路径返回 data
6. Node2 收到 data 并完成 load

Observables:
- `inspectUbccDirForTest(line_pa_home_view)`
- `inspectDirEntryForTest(line_pa_home_view)`
- `inspectRequesterStateForTest(node_id, line_pa)`
- 关键 debug log: `GlobalRecallOwner`, owner reply, grant/result state
- ARM workload stdout 中打印读取值

Expected Output / Pass Criteria:
- Phase A 后，home directory 显示 Node0 为 owner，state=`G_M`
- Phase B 中出现 `GlobalRecallOwner`
- Node2 最终读到 `0x11223344`
- recall 完成后目录状态与设计一致，不依赖 home UBCC 常驻 data copy

Negative Criteria / Forbidden Outcome:
- Node2 读到旧值或默认值
- home UBCC 直接从本地常驻 data 返回结果而未触发 recall
- 目录状态在 recall 前后不自洽

### `TC-M6-2` GlobalRecallOwner Path

Purpose:
- 验证 `GlobalRecallOwner` 的完整链路节点与方向正确。

Harness Type:
- `PY_INJECT`

Preconditions:
- 构造一条 home 在 Node1、owner 在 Node0 的 line

Inputs:
- `line_pa_home_view`
- owner node = 0

State Injection / Stimulus Method:
- 使用 test hook 安装一个合法的 owner 场景
- 之后由 home 注入 1 个 read-triggered recall

Execution Steps:
1. 通过 helper 安装 Node0 owner / Node1 home 目录状态
2. 触发 home 侧 read or recall path
3. 捕获 home -> owner 的 `GlobalRecallOwner`
4. 捕获 owner `EP_RNF` 向 HN 发 local recall
5. 捕获 owner 返回 data 给 home

Observables:
- `inspectUbccDirForTest`
- owner 节点 `EP_RNF` 收发计数
- HN/EP/UBCC debug log

Expected Output / Pass Criteria:
- 观测到 `GlobalRecallOwner`
- owner 节点 `EP_RNF` 通过本地 HN 路径取回数据
- home 收到 data 并推进事务完成

Negative Criteria / Forbidden Outcome:
- home 直接完成 recall 而不联系 owner
- owner 不经本地 HN 路径就返回 data

### `TC-M6-3` EP_RNF Delayed HN Response

Purpose:
- 验证 `EP_RNF` 对 HN 的响应被正确延迟到 outer transaction 完成之后。

Harness Type:
- `PY_INJECT`

State Injection / Stimulus Method:
- 触发一个必须等待 remote ack/data 的 HN snoop -> `EP_RNF` 路径
- 在 test hook 中人为延后 remote completion 一个阶段

Observables:
- `EP_RNF` 待响应状态
- HN completion 时序
- outer transaction 完成时刻

Expected Output / Pass Criteria:
- 在 outer transaction 完成前，`EP_RNF` 不向 HN 发最终响应
- outer transaction 完成后，HN 才收到最终响应

Negative Criteria / Forbidden Outcome:
- `EP_RNF` 提前答复 HN

### `TC-M6-4` Directory Consistency

Purpose:
- 验证 M6 后 directory 核心字段与事务结果一致，且 `E/M` 严格可区分。

Harness Type:
- `PY_INJECT`

Inputs:
- 3 个子场景:
  - shared-only line
  - exclusive clean owner line
  - modified dirty owner line

Observables:
- `inspectUbccDirForTest(line_pa)`

Expected Output / Pass Criteria:
- shared-only -> `state=G_S`, `owner_node` 无效, `dirty=false`
- exclusive clean owner -> `state=G_E`, `owner_node` 有效, `dirty=false`
- modified dirty owner -> `state=G_M`, `owner_node` 有效, `dirty=true`

Negative Criteria / Forbidden Outcome:
- `G_E` 和 `G_M` 只能通过一个模糊 owner 字段区分而无状态区别

### `TC-M6-5` Home UBCC Metadata-Only

Purpose:
- 验证 home UBCC 只维护 metadata，而不是 data store。

Harness Type:
- `PY_INJECT`

Observables:
- `UBCCController` test inspection API
- 相关字段/结构数量与内容

Expected Output / Pass Criteria:
- UBCC directory inspection API 中不存在“长期缓存完整 line data”的必需字段
- 读路径设计说明与实现均显示 data 来自 owner reply / writeback

Negative Criteria / Forbidden Outcome:
- 为了让测试过关，在 UBCC 中加入常驻 line data copy 作为主数据来源

## 6. M7 TestCase

### `TC-M7-1` Dirty Writeback Updates Home

Purpose:
- 验证 dirty owner writeback 后，home metadata 和后续读路径能观察到最新值。

Harness Type:
- `ARM_SYNC`

Inputs:
- Node0 先持有 `G_M` line 并写入 `0x55667788`
- 随后触发 writeback
- Node2 再次读取该 line

Execution Steps:
1. Node0 成为 modified owner 并写入值
2. Node0 对该 line 触发 writeback
3. home 处理 `GlobalWriteback`
4. Node2 读取同一 line

Observables:
- `inspectUbccDirForTest`
- writeback ack log
- Node2 最终读值

Expected Output / Pass Criteria:
- writeback 后目录状态更新正确
- Node2 最终读到 `0x55667788`

Negative Criteria / Forbidden Outcome:
- writeback 后读取仍得到旧值

### `TC-M7-2` Clean Evict Updates Sharer Mask

Purpose:
- 验证 clean evict 会正确更新 sharer mask。

Harness Type:
- `PY_INJECT`

Inputs:
- 先构造 Node0/Node2 共享某条 Node1 home line
- 再让 Node2 clean evict

Observables:
- `inspectUbccDirForTest(line_pa_home_view)`

Expected Output / Pass Criteria:
- evict 前 sharer mask 包含 Node0 和 Node2
- evict 后 sharer mask 只保留 Node0

Negative Criteria / Forbidden Outcome:
- Node2 evict 后 sharer mask 未清理

### `TC-M7-3` Single Global Owner In Ping-Pong

Purpose:
- 验证连续 owner transfer 中任意时刻最多一个 global owner。

Harness Type:
- `ARM_SYNC`

Inputs:
- Node0 -> Node1 -> Node2 依次对同一 logical line 执行 store

Execution Steps:
1. Node0 写并成为 owner
2. Node1 写并抢占 owner
3. Node2 写并抢占 owner
4. 每步后读取 directory

Observables:
- `inspectUbccDirForTest` 每阶段快照

Expected Output / Pass Criteria:
- 任意快照中 `owner_node` 唯一
- 不存在双 owner

Negative Criteria / Forbidden Outcome:
- 任一中间状态同时出现两个 owner

### `TC-M7-4` Stale Epoch Rejected

Purpose:
- 验证 stale ack/data 不会污染当前事务。

Harness Type:
- `PY_INJECT`

State Injection / Stimulus Method:
- 对同一 line 制造两次连续事务
- 在第二次事务期间注入第一次事务遗留的旧 epoch ack/data

Observables:
- `inspectUbccDirForTest`
- 事务完成状态
- stale-drop debug log

Expected Output / Pass Criteria:
- 旧 epoch 响应被识别并丢弃
- 当前事务按新 epoch 正常完成

Negative Criteria / Forbidden Outcome:
- 旧响应影响当前 owner/sharer/data 结果

### `TC-M7-5` Metadata-Only Home Still Correct

Purpose:
- 验证 metadata-only home 设计在 owner transfer / writeback / evict 下仍保持正确。

Harness Type:
- `PY_INJECT`

Expected Output / Pass Criteria:
- 三类操作都不要求 home UBCC 保留常驻 line data copy
- 目录状态与后续读回结果保持一致

### `TC-M7-6` Recall Result State Split

Purpose:
- 验证 recall 结果按访问类型分裂为两种不同后状态。

Harness Type:
- `ARM_SYNC`

Inputs:
- 子场景 A: remote read 触发 recall
- 子场景 B: remote unique/write 触发 recall

Observables:
- owner 节点 requester-side bookkeeping
- home directory

Expected Output / Pass Criteria:
- 子场景 A: 原 owner 降级为 shared
- 子场景 B: 原 owner 失效为 invalid

Negative Criteria / Forbidden Outcome:
- 两个子场景落到同一种后状态

## 6.1 M6/M7 调试观测建议

对于 `M6 ~ M7`，优先提供以下内部状态读取接口:
- `inspectUbccDirForTest(line_pa)`
- `inspectRequesterStateForTest(node_id, line_pa)`
- `inspectSentinelStateForTest(home_node, line_pa)`
- `inspectEpochForTest(line_pa)`

这些接口的目标不是替代功能验证，而是让 testcase 与人工调试都能观察中途状态是否符合协议预期。

## 7. M8 TestCase

### `TC-M8-1` Two Requesters Hold Shared

场景:
- Node0 和 Node2 同时读 Node1 DSM line。

预期:
- 两者都获得 shared。

### `TC-M8-2` Local Upgrade Invalidates Other Sharers

场景:
- Node0 shared 后再写。

预期:
- Node2 被 global invalidate。

### `TC-M8-3` Shared Default Path

预期:
- 默认配置下 shared miss 走 `GlobalReadShared`，而不是强行 `GrantM`。

### `TC-M8-4` SharerMask Correctness

预期:
- sharer mask 与实际 shared nodes 一致。

## 8. M9 TestCase

### `TC-M9-1` Metadata Not CPU Visible

预期:
- 普通 CPU 无法访问 metadata。

### `TC-M9-2` Outer ABI Completeness

预期:
- outer protocol 字段足以表达 `M4..M8` 所需事务。

### `TC-M9-3` Model Layer Does Not Break Correctness

预期:
- 加入 metadata model 后，`M4..M8` 回归仍通过。

## 9. 建议的测试文件布局

推荐新增:
- `tests/sync_wait/`
- `tests/phase4/`
- `tests/phase5/`
- `tests/phase6/`
- `tests/phase7/`
- `tests/phase8/`
- `tests/phase9/`

每个阶段至少包含:
- 1 个 directed integration test
- 1 个 state/metadata checker test
- 必要时 1 个 debug/assert negative test

说明:
- 当前主计划交付聚焦 `T0` 与 `M4 ~ M7`。
- `M8`、`M9` testcase 保留为可选后续规划。
