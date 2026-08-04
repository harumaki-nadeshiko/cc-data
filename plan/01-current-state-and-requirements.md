# Current State And Requirements

## 1. 当前项目目标

在单个 gem5 `System` 内实现 `N=3, L=2, D=2` 的 Ruby CHI + UBCC 原型。

每个 node 固定包含:
- 2 个 cluster
- 每个 cluster 2 个 core
- 1 个 HN-F/L3
- 1 个 `L_SNF`
- 1 个 `DL_SNF`
- 1 个 `EP_SNF`
- 1 个 `EP_RNF`
- 1 个 `EPBackend`
- 1 个 `UBCCController`

当前重点是 correctness 主路径，而不是性能优化。

## 2. 用户要求

### 2.1 非协商约束

- 主配置固定为 `N=3, L=2, D=2`。
- 不允许缩规模伪通过。
- `DSM Local` 与 `Local Private` 严格分开。
- `UbccExclusive` 第一版不映射给普通 CPU。
- ordinary CHI traffic 必须限制在 node 内。
- 跨 node 通信只能走 EP/UBCC outer protocol。
- `EP_RNF` 是 sentinel 主路径。
- 第一版不实现独立 `UR_i`。
- metadata 第一版使用内存驻留 map。
- 所有 trace/checker/fatal 必须带 `node_id`。

### 2.2 工作方式要求

- 开发、构建、测试在无网络 Docker 容器内完成。
- commit/push 在宿主机完成。
- 最大并行度 `-j20`。
- 不允许 bypass、monkey patch、硬编码 PASS、print-only testcase。
- 每阶段必须给出真实 testcase 和回归结果。
- 若因 API 限额导致 reviewer/orchestrator 无法继续，必须停止推进并落盘阶段检查点文档，供下次恢复。

### 2.3 计划书要求

- 计划中的术语必须有明确、可复用的定义，避免不同 Agent 理解不一致。
- 计划必须足够详细，使其他 Coding Agent 可直接执行。
- 计划必须显式写出每阶段目标、任务、验收标准、TestCase。
- 计划必须显式写出 External Proxy 的内部状态、外部状态、请求转换、状态转换。
- 计划必须显式指导主 agent（primary agent，承担 orchestrator 角色）如何分派 `intelligent-agent`（实现）和 `high-intelligent-agent`（审核）。

### 2.4 本轮新增用户决策

- 只要 testcase 需要多节点同步，就必须在那之前先实现同步机制。
- `run_phase1_test.py` 可以被逐步替代，不要求一次性废弃。
- 优先选择“不改 HN-F 状态机”的方案。
- 推荐在 HN 向 `EP_SNF` 下发 remote `ReadNoSnp` 时携带 `Shared` 还是 `Unique` 的请求意图。
- `Sync_Wait` 只统计显式调用该 syscall 的线程，不统计节点上未调用的线程。
- `HN -> EP_SNF` 的 permission sideband 首选直接扩展消息字段，而不是 side table/context map。
- 如果后续实现发现 `EP_RNF` 需要表达的状态超出了 HN-F 现有可表达范围，必须在仓库根目录新增 `OhNo_EP_RNF_NotGooOod.md`，醒目说明该问题与后续 HN 扩展计划。
- 在这种异常情况下，fallback 允许做最小 directory helper 扩展。
- 如果 `Sync_Wait` 遇到实质性瓶颈无法落地，再考虑 Python/gem5 侧定向注入方案。
- outer protocol 消息名可以现在固定下来。
- 计划书结构由我选择，以 Agent 更易执行为准。
- 当前主要讨论与实现目标集中在 `M3.5`、`M4 ~ M7`；`M8 ~ M9` 作为可选项保留。

## 3. 当前已完成工作

### 3.1 基础拓扑

- 已完成 `create_ubcc_system()`。
- 已完成 `ClusterCHI_RNF`、`HNNodeWrapper`、`EPNodeWrapper`。
- 已完成 3 node / 6 cluster / 12 CPU 的对象级 topology 组装。
- 已完成 cluster 只连本 node HN 的 downstream 约束。
- 已完成 HN 只连本 node `L_SNF/DL_SNF/EP_SNF` 的 downstream 约束。

### 3.2 地址方案

- 当前基线为 per-node PA，而非最初的统一 DSM PA。
- 已实现 Python/C++ 双侧 `NodeAddressMap`。
- 已实现 `PA <-> (src_node, home_node, offset)` 的基础转换工具。
- 已完成 `phys_pool_id` 的 gem5 `Process` 路由改造。

### 3.3 EP skeleton

- `EP_RNF` 已接入 topology，可回复固定 `SnpResp_I`。
- `EP_SNF` 已接入 topology，可回复 `RespSepData + CompData_I` fake data。
- `EPBackend` 已具备地址守卫。
- `UBCCController` 仅有 skeleton，不具备 coherence 语义。

### 3.4 无 bypass bring-up

- `tests/phase2/test_ruby_create_system_n3l2d2.py` 已去掉 bypass。
- `L_SNF`/`DL_SNF` 已接真实 `MemCtrl + DDR4_2400_8x8`。
- `Ruby.py` 已支持协议自己提供 backstore 时的 `dir_cntrls == 0` 路径。
- 当前 `Ruby.create_system + m5.instantiate()` 的无 bypass 基线已通过。

## 4. 当前已通过测试

| 测试 | 状态 | 用途 |
|---|---|---|
| `tests/phase1/test_pa_layout_mode.py` | `48/48 PASS` | 地址布局静态守门 |
| `tests/phase1/run_phase1_test.py` | `5/5 PASS` | DSM VA / SE 运行基线 |
| `tests/phase1/run_phase1_test_enhanced.py` | `12/12 PASS` | per-node PA / pool 增强基线 |
| `tests/phase2/verify_topo_objects.py` | `101/101 PASS` | 对象级 topology 守门 |
| `tests/phase2/test_ruby_create_system_n3l2d2.py` | `9/9 PASS` | no-bypass create_system / instantiate |
| `tests/phase3/test_ep_instantiate.py` | `INSTANTIATE OK` | EP 最小 Ruby instantiate |

## 5. 当前未完成工作

以下内容都不能在计划或报告中写成“已完成”。

- `Sync_Wait(node_mask)` 还未实现。
- HN-F sentinel registration 未实现。
- `ExternalSharer` / `ExternalOwner` / `ExternalPending` 未进入真实 directory。
- `EP_RNF` 不能把 HN snoop 转成真实 UBCC 操作。
- `EP_SNF` 不能处理真实 remote miss/fill/writeback/evict。
- `UBCCController` 没有 per-line global directory。
- global owner/sharer/dirty/epoch 管理未实现。
- read-sharing / upgrade / invalidation / owner transfer 未闭环。
- metadata 容量模型和 multi-gem5 准备未实现。

## 6. 当前推荐实现策略

### 6.1 HN 修改策略

推荐主路线:
- 尽量不改 HN-F 状态机。
- 只在 HN 向 `EP_SNF` 下发 remote `ReadNoSnp` 时增加最小 sideband 信息。
- sideband 至少表达 `needed_perm = Shared | Unique`。
- 不额外冗余携带可由 PA 直接解析得到的 `src_node/home_node` 信息，除非后续设计证明确有必要。
- home-side `EP_RNF` 目录项优先使用与普通 CPU cluster RNF 相同的 HN 原生目录格式和状态承载方式。

允许的最小 HN 修改:
- 给发往 `EP_SNF` 的消息附带 UBCC sideband 字段。
- 在 sentinel registration 相关点增加 hook 或 helper。
- 增加必要 assert/debug。

不优先选择的路线:
- 大范围重写 HN coherence state machine。
- 在 HN 每条主请求路径里插入大面积 global permission hook。

### 6.2 同步机制策略

- 任何需要可靠跨 node 时序控制的 testcase，都必须以前置 `Sync_Wait` 为门。
- 因此 `T0` 是后续协议阶段 testcase 的硬前置任务，而不是可选增强项。
- `Sync_Wait` 只统计显式调用该 syscall 的线程。
- 若 `T0` 证明确实短期不可落地，才允许为极早期 bring-up testcase 临时评估 Python/gem5 定向注入替代方案；该替代方案不能默认化，也不能直接取代 `T0` 的正式目标。

### 6.3 EP_RNF 表达范围策略

- 目标前提: `EP_RNF` 作为 RNF 抽象，应尽量使用 HN-F 已有 RNF/directory 语义来表达，不应天然要求 HN-F 新增特殊状态。
- 更强的当前主张: sentinel 与普通 CPU cluster RNF 不应走两套平行的 HN 目录格式；优先应共享同一种 HN-F 原生 owner/sharer/transient 表达，只是在语义解释和 EP_RNF 的响应约束上不同。
- 如果实现过程中发现 `EP_RNF` 状态确实超出 HN-F 可表达范围，必须先暂停“默认继续推进”的路径。
- 必须新增根目录文档 `OhNo_EP_RNF_NotGooOod.md`，说明:
  - 哪些 `EP_RNF` 状态超出 HN-F 现有表示能力
  - 为什么现有 RNF 抽象不够
  - 后续准备在 HN-F 侧新增什么最小状态或 helper 扩展
- 在该文档存在后，才允许采用“最小 directory helper 扩展”作为 fallback。

### 6.4 Home UBCC 数据职责策略

- home UBCC 主职责是保存目录与元数据，不缓存缓存行真实数据。
- 最新数据可以暂驻 owner node，直到 recall / writeback / evict 把数据带回需要的路径。
- 后续 `M6 ~ M7` 的设计必须遵循这一点，不能把 home UBCC 设计成缓存大规模实际 data 的中心。
- home UBCC 的状态机要求使用 MESI，而不是把 `E` 和 `M` 混成一个 owner 态。
- 因此后续设计中必须显式区分:
  - clean exclusive owner (`E`)
  - dirty modified owner (`M`)

### 6.5 Phase1 测试策略

- `run_phase1_test.py` 暂时保留，作为兼容基线。
- 后续逐步把主回归重心转向 `run_phase1_test_enhanced.py`。
- 在完全替代前，两者都应保留在回归集合中。

### 6.6 Docker 与提交策略

- 实现阶段必须遵循 `docs/ubcc_docker_git_workflow.md`。
- build/test 在 Docker 容器中完成。
- commit/push 在宿主机完成。
- `gem5` submodule 的改动不能只留在工作树，必须有独立 submodule commit。
- 主仓提交时，必须同时更新 submodule 指针并附带清晰的主仓变更说明，让主仓历史能读出本轮改动涉及哪些 `gem5` 文件/模块。

### 6.7 测试辅助接口与观测策略

- 测试辅助接口优先采用 `C++ debug/test hook + Python trigger` 方案。
- 原因是协议关键状态位于 controller/directory 的 C++ 实现内部，直接从 C++ 暴露最小测试接口更一致，也更利于调试。
- Python 层主要负责:
  - 构造 testcase
  - 调用 test hook
  - 收集观测值
  - 做断言
- 后续 `M4 ~ M7` testcase 的主观测来源优先是内部状态读取接口，而不是仅靠 stdout。
- stdout 仍可保留为辅助观测，但不能替代目录/控制器内部状态断言。
- 测试 hook 采用折中方案:
  - `M4` 允许少量强注入 helper，用于最早期定点验证 sentinel/directory 目标状态
  - `M5 ~ M7` 优先转向路径驱动，只允许 helper 建立难以自然准备的前置条件，不能直接伪造 testcase 的最终通过状态
