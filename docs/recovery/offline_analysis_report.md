# 离线分析报告：在无法接入真实多节点框架时可推进的工作

## 1. 结论

即使现在碰不到真实的多节点运行框架，仍然有一批**可以完全基于现有代码/文档离线推进**的工作，主要分成三类：

1. **形式化验证**：把当前代码中的状态、转移、消息、等待关系抽成模型与不变量。
2. **模块拆分 / API 边界分析**：把 standalone UBCC 所需的最小边界、gem5 污染面、时间/路由/回调依赖切清楚。
3. **代码质量 / 重构分析**：先做职责分层、重复代码识别、dispatcher/table 化候选识别，再决定是否进入重构。

真正被“没有真实多节点框架”卡住的，是**跨进程时钟/传输/故障注入的在线行为验证**；但**语义抽取、模型建立、边界切割、ABI 固化、依赖图分析**都可以先做。

---

## 2. 现状锚点（供后续任务引用）

- `UBCCController.hh:14-15` 直接包含 `EPBackend.hh` 与 `ResidentDir.hh`，`OutstandingRequest` 还直接使用 `DataBlock` 语义与 `GrantDataSource`，说明协议核心仍被 gem5/EP 类型污染。
- `UBCCController.hh:202-215` 暴露了 `setRouter()` / `setBackend()`，`UBCCController.cc` 多处直接使用 `curTick()`，说明时间与发送路径尚未抽象。
- `UBRouter.hh:29-32, 70-72, 85` 表明 Router 仍是 `SimObject`，并依赖静态 registry 与 `EventFunctionWrapper`。
- `UBAdapter.hh:36-39, 69-123` 表明 Adapter 仍是同步 request/response 门面，且承担 EPBackend 与 Router 的胶水层职责。
- `UBMsg.hh:48-63` 和 `UBMsgQueue.hh:17-68` 说明消息格式与队列都直接依赖 `Tick`，目前还不是脱离 gem5 的稳定 wire/runtime ABI。
- `ResidentDir.hh:14-43` 与 `ResidentDir.cc:150-167` 已经给出了很清晰的目录 canonical invariant，可直接作为形式化约束来源。
- `EPBackend.hh:289-301, 394, 498, 538, 609-611` 已暴露出 host-facing 的最小语义面：grant data source、recall/invalidate 回调、upgrade ready 通知、backstore read/write/delete。
- `docs/recovery/verification_plan.md:169-218`、`docs/recovery/formal_verification_plan.md:22-79` 已经给出 TLA+/Rumur/CBMC 三层验证路线；`docs/recovery/migration_plan.md:117-173` 已经给出独立化边界草案。

---

## 3. 子任务清单

> 约定：
> - **Type = out-of-place**：不需要改当前代码；可由只读型子代理完成。
> - **Type = needs code changes**：需要后续改代码/加文件；此处仅输出应改哪些文件、改什么。

---

## A. 形式化验证

### FV-1. UBCC 单行状态机枚举与转移表抽取

- **描述**：从 `UBCCController.hh/.cc` 抽取 `MESIState × OpType × OpStage × 输入消息` 的可达转移表，形成“代码到模型”的黄金枚举。
- **依据**：`UBCCController.hh:61-78, 81-160` 已明确定义 `OpType`/`OpStage`/`OutstandingRequest`；`ResidentDir.hh:14-43` 给出 committed directory 状态集合。
- **Type**：out-of-place
- **建议子代理类型**：`formal-modeling-subagent`
- **具体工作**：
  1. 列出 committed 状态：`G_I/G_S/G_E/G_M`。
  2. 列出 live outstanding 状态：`RECALL/INVALIDATE/GRANT_HANDSHAKE/UPGRADE_PENDING` 各自阶段。
  3. 对 `processOuterRequest/processRecallResponse/processInvalidationAck/processOuterUpgradeReq/processOuterUpgradeDone/processClear` 建立“前置条件→状态变化→提交点”表。
  4. 标出哪些边只是 live-state 更新，哪些边真正写 committed dir。
- **产出**：`state_transition_matrix.md` 或 TLA+ 常量/动作表。

### FV-2. M1（UBCC-only）TLA+ 抽象模型草案

- **描述**：不依赖真实网络，只用代码与文档为 `UBCC + ResidentDir + tombstone + pendingQueue + 非 FIFO 网络` 建立单行 TLA+ 抽象模型。
- **依据**：`verification_plan.md:171-210` 已定义 M1 的状态变量与 safety/liveness；`formal_verification_plan.md:87-108, 191-220` 已给出 invariant 列表。
- **Type**：out-of-place
- **建议子代理类型**：`protocol-model-checker-subagent`
- **具体工作**：
  1. 把 `dir[line] / ost[line] / tombstone[line] / net[msg] / pendingQueue[line]` 映射成 TLA+ 变量。
  2. 先只建单 line、2~3 node 的小模型。
  3. 编码 safety：single-home、shared/exclusive 互斥、epoch 单调、stale completion reject、tombstone 幂等。
  4. 编码 liveness：accepted request 最终 grant 或 retry、`GRANT_HANDSHAKE` 不泄漏。
- **产出**：`ubcc_only.tla` 的离线初稿与 invariants checklist。

### FV-3. EP-RNF / HN-F 边界抽象响应矩阵核对

- **描述**：从 `CHI-cache-actions.sm`、现有文档与 EP 控制器接口中抽取“snoop 类型 × 本地状态 × 预期响应”的黄金矩阵，用于后续 M2 模型和 code review。
- **依据**：`verification_plan.md:136-150` 已给出黄金矩阵；`CHI-cache-actions.sm` 是当前 CHI 行为锚点；`EPBackend.hh:394, 498, 538` 暴露 recall / invalidate / upgrade ready 边界。
- **Type**：out-of-place
- **建议子代理类型**：`boundary-protocol-auditor`
- **具体工作**：
  1. 建立 `SnpCleanInvalid/SnpUnique/SnpOnce/...` 对应的黄金响应表。
  2. 明确哪些情况允许 `SnpRespData_*`，哪些必须 `SnpResp_I`。
  3. 标注“EP-RNF only sharer 时禁止 Fwd”的约束。
  4. 输出与当前代码/文档一致、不一致、未覆盖三类结论。
- **产出**：`eprnf_boundary_matrix.md`。

### FV-4. Wait-for graph / 死锁环离线建模

- **描述**：按每条 line 建立等待图，分析 `UBCC ↔ requester ↔ owner/sharers ↔ EP-RNF/HN-F` 的等待边，找潜在死锁环与“必须依赖公平性”的边。
- **依据**：`formal_verification_plan.md:228-286` 已明确 wait-for graph 目标；`UBCCController.hh` 的 `PendingRequester`、`OutstandingRequest` 与 `EPBackend.hh` 的 clear/upgrade/recall/invalidation 接口足以做静态等待关系抽取。
- **Type**：out-of-place
- **建议子代理类型**：`deadlock-analysis-subagent`
- **具体工作**：
  1. 抽等待边：`requester 等 ClearAck`、`UBCC 等 RecallResp/InvalidateAck/UpgradeDone`、`EP-RNF 等 UpgradeAckNotify`。
  2. 区分“协议必需等待”与“实现引入等待”。
  3. 给出 cycle 候选和解除条件（timer/retry/CompAck/fairness）。
- **产出**：`wait_for_graph.md` + 死锁候选清单。

### FV-5. UBMsg 线协议/字段不变量说明书

- **描述**：仅基于 `UBMsg.hh` 和迁移文档，输出一份稳定的 message schema：字段、方向、合法 flag 组合、每类消息必须/禁止携带的数据。
- **依据**：`UBMsg.hh:17-178` 已给出消息全集；`verification_plan.md:152-165` 与 `migration_plan.md:179-242` 已定义 wire 不变量与外部 envelope 方向。
- **Type**：out-of-place
- **建议子代理类型**：`wire-abi-auditor`
- **具体工作**：
  1. 为每个 `UBMsgType` 列出 header 字段语义。
  2. 列出 `flags` 的合法组合与非法组合。
  3. 指出当前格式中哪些字段仍是 gem5 语义（如 `Tick`），哪些能直接转成 wire ABI。
  4. 给出向 `version/payloadLen/type-specific body` 迁移时不可变的字段集。
- **产出**：`ubmsg_wire_spec.md`。

### FV-6. CBMC/局部证明 harness 设计

- **描述**：为 epoch 比较、tombstone replay、tuple match、`processClear()` 接收门控等局部逻辑设计可证明的 harness。
- **依据**：`formal_verification_plan.md:68-79` 已明确 CBMC 适用于 helper 级别；当前实现里相关逻辑散落在 `UBCCController.cc` 中。
- **Type**：needs code changes
- **需要落地的文件改动**：
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh`
    - 把可证明的纯逻辑 helper 显式抽成 `static`/`free function`，例如 epoch 比较、tuple match、tombstone accept 判定。
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc`
    - 将 `processClear()` / `processOuterUpgradeDone()` 中的门控判断拆成纯函数，减少对对象状态和 `curTick()` 的依赖。
  - `tests/formal/cbmc/ubcc_epoch_harness.cpp`
  - `tests/formal/cbmc/ubcc_tombstone_harness.cpp`
  - `tests/formal/cbmc/ubcc_clear_gate_harness.cpp`
- **改动目的**：让 helper 逻辑能在不拉起整套 gem5 的情况下做 bounded proof。

### FV-7. 运行时不变量断言插桩计划

- **描述**：把形式化不变量映射成实现侧断言/审计点，形成“模型→代码”闭环。
- **依据**：`ResidentDir.cc:150-167` 已经有 canonical invariant 的先例；`formal_verification_plan.md:451-473` 也要求实现侧 assertion mirror。
- **Type**：needs code changes
- **需要落地的文件改动**：
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc`
    - 在 committed state write 点、Clear commit、UpgradeDone commit、Recall/Invalidate terminal path 加 invariant checks。
  - `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc`
    - 在 grant data source、clear replay、upgrade ready 接口处增加 tuple/epoch 一致性审计。
  - `gem5/src/mem/ruby/protocol/chi/ep/UBRouter.cc`
    - 在消息投递前后增加 message-type/flags/body 一致性检查。
  - `tests/unit/test_ubmsg_invariants.py`
    - 增加字段组合和拒绝路径的单元测试。
- **改动目的**：把离线证明出来的 invariant 变成回归可守护的实现约束。

---

## B. 模块拆分 / API 边界分析

### MS-1. Public API 边界与 gem5 污染面清册

- **描述**：列出每个模块的 public surface，尤其是哪些 API/类型无法离开 gem5：`SimObject`、`Params`、`Tick`、`DataBlock`、`SimpleMemory`、`RubySystem`、`EPBackend`。
- **依据**：
  - `UBRouter.hh:29-32, 67-72, 85`
  - `UBAdapter.hh:36-39`
  - `UBMsg.hh:48-63`
  - `UBCCController.hh:14, 121, 202-215`
  - `EPBackend.hh:289-301, 633-638`
- **Type**：out-of-place
- **建议子代理类型**：`api-boundary-cartographer`
- **具体工作**：
  1. 枚举 header 级 public API。
  2. 标出每个 API 参数/返回值是否含 gem5 专有类型。
  3. 把类型分为：可直接迁移、需 typedef/adapter、必须留在 gem5。
  4. 输出“最小 standalone 核心”与“必须保留在 gem5”边界表。
- **产出**：`api_contamination_matrix.md`。

### MS-2. 头文件依赖图 / include graph / 层级违规报告

- **描述**：从当前头文件 include 关系抽图，检查是否存在不必要的上层反向依赖、类型泄漏和可前置声明而未前置声明的热点。
- **依据**：当前 include 关系已经表明：`UBCCController.hh` 直接 include `EPBackend.hh`，`UBRouter.hh`/`UBAdapter.hh` 直接继承 `SimObject`。
- **Type**：out-of-place
- **建议子代理类型**：`dependency-graph-subagent`
- **具体工作**：
  1. 生成 `*.hh/*.cc` include graph。
  2. 标记“协议核心依赖宿主”的逆向边。
  3. 给出可以改成前置声明的点。
  4. 给出未来拆库时的编译防火墙建议。
- **产出**：`include_dependency_report.md` + 可视化图。

### MS-3. Host callback / transport / clock 三类边界切面分析

- **描述**：基于现有代码，提前定义 standalone UBCC 最少需要向外索取的三类服务：host callback、消息发送、逻辑时钟。
- **依据**：
  - `UBCCController.hh:213-214` 已有 `setRouter/setBackend`
  - `UBRouter.hh:58-68` 暴露 send/queue 语义
  - `UBCCController.cc`、`UBAdapter.cc`、`UBRouter.cc` 多处直接使用 `curTick()`
  - `EPBackend.hh:609-611` 已暴露 backstore 服务面
- **Type**：out-of-place
- **建议子代理类型**：`standalone-cut-planner`
- **具体工作**：
  1. 归并出 `IUbccHost`、`IRouterEgress/ITransport`、`ILogicalClock` 的候选方法集。
  2. 标明每个现有调用点属于哪类服务。
  3. 输出接口最小集合与非目标集合（暂不抽象的 API）。
- **产出**：`standalone_cut_contract.md`。

### MS-4. UBMsg 语义层 / wire 层拆分设计

- **描述**：把当前 `UBMsg` 中“协议语义 struct”和“稳定 wire ABI”拆成两层，这是后续多进程化的关键 compile firewall。
- **依据**：`UBMsg.hh:48-63` 仍直接携带 `Tick`；`migration_plan.md:191-242` 已明确要转成 `version/payloadLen` envelope。
- **Type**：needs code changes
- **需要落地的文件改动**：
  - `gem5/src/mem/ruby/protocol/chi/ep/UBMsg.hh`
    - 保留语义层 `UBMsgSemantic` 或缩减为只含协议字段，不直接暴露 wire 序列化细节。
  - `gem5/src/mem/ruby/protocol/chi/ep/ubmsg_wire.hh`
    - 新增稳定 wire header/body 定义，禁止直接依赖 `Tick`/gem5 object。
  - `gem5/src/mem/ruby/protocol/chi/ep/ubmsg_wire.cc`
    - 新增 serialize/deserialize/validate。
  - `tests/unit/test_ubmsg_wire.py` 或 `test_ubmsg_wire.cc`
    - roundtrip、非法 flags、body 长度校验测试。
- **改动目的**：为后续 IPC/Ns3UB 通路准备稳定 ABI，而不必先接真实框架。

### MS-5. 时间与队列语义抽象（`Tick` → `logical_tick`）

- **描述**：把 `UBMsg`/`UBMsgQueue`/`UBRouter` 中跟 gem5 `curTick()` 绑定的语义切出来，为 standalone runtime 做准备。
- **依据**：`UBMsg.hh:62-63`、`UBMsgQueue.hh:17-68`、`UBRouter.cc:94,104,202`、`UBAdapter.cc` 多处 `enqueueTick/readyTick = curTick()`。
- **Type**：needs code changes
- **需要落地的文件改动**：
  - `gem5/src/mem/ruby/protocol/chi/ep/UBMsg.hh`
    - 把时间字段改成协议层无关的 `uint64_t logicalTick` 或包装类型。
  - `gem5/src/mem/ruby/protocol/chi/ep/UBMsgQueue.hh`
    - 去除对 `base/types.hh` 的硬依赖，改用 runtime-neutral tick type。
  - `gem5/src/mem/ruby/protocol/chi/ep/UBRouter.hh/.cc`
    - 将 `EventFunctionWrapper` / `schedule(curTick()+1)` 胶水压缩到 adapter/runtime 层。
  - `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc`
    - 把 `curTick()` 采样集中在单一注入点，而不是每种消息都手写一遍。
- **改动目的**：先完成时钟语义解耦，再谈真实多节点调度器接入。

### MS-6. 调用图级别的“谁该留在 gem5 / 谁可独立”证明报告

- **描述**：对 UBCC/ResidentDir/Router/Adapter/Backend/EPRNF/EPSNF 做职责归属判定，并给出“为什么它该留/可走”的证据。
- **依据**：`migration_plan.md:17-38, 117-139` 已有方向；当前代码可补齐更细的函数级证据。
- **Type**：out-of-place
- **建议子代理类型**：`architecture-review-subagent`
- **具体工作**：
  1. 以类/函数为粒度划分“协议核心 / gem5 glue / CHI-specific / test-only”。
  2. 给出“保留在 gem5”与“可独立”的函数清单。
  3. 标出高风险切口（如 `GrantDataSource`、`RubySystem*`、`SimpleMemory*`）。
- **产出**：`module_ownership_report.md`。

---

## C. 代码质量 / 重构分析

### CQ-1. God object / 职责热区分析

- **描述**：对 `UBCCController` 和 `EPBackend` 做职责拆分体检，按“协议状态机 / backstore / resident eviction / routing glue / test inspection / logging”分块统计。
- **依据**：
  - `UBCCController.{hh,cc}` 规模大且同时管理 protocol、resident/backstore、router callback、debug/test API。
  - `EPBackend.hh` 既管理 requester state，又处理 recall/invalidate/upgrade/backstore/metaRNF。
- **Type**：out-of-place
- **建议子代理类型**：`refactor-architecture-subagent`
- **具体工作**：
  1. 以 public/private method 分簇。
  2. 统计每簇的状态字段依赖。
  3. 找出“可抽纯 helper”“可抽 service object”“必须保留在 controller”的边界。
- **产出**：`responsibility_heatmap.md`。

### CQ-2. 消息构造重复代码盘点

- **描述**：审计 `UBAdapter.cc` 中大量重复的 `UBMsg req; req.h...` 构造模板，提炼成 builder/table-driven 方案。
- **依据**：`UBAdapter.cc` 多个 `send*Req/send*Resp` 函数重复设置 `type/src/dst/homeNode/requesterNode/epoch/reqId/seqNum/enqueueTick/readyTick`。
- **Type**：out-of-place
- **建议子代理类型**：`duplication-audit-subagent`
- **具体工作**：
  1. 统计 header 字段赋值重复模式。
  2. 提炼公共字段模板与每类消息的差异字段。
  3. 给出 builder / helper / table-dispatch 三种改造方案。
- **产出**：`message_builder_refactor_note.md`。

### CQ-3. Router dispatcher 表驱动重构评估

- **描述**：分析 `UBRouter::drainReadyQueues()` / `deliverToUbcc()` 的 `switch` 分发是否已到需要表驱动或 visitor 化的程度。
- **依据**：`UBRouter.cc:125-174, 221-417` 对消息类型的分发已明显扩张，且本地投递目标判断和具体处理混在一起。
- **Type**：out-of-place
- **建议子代理类型**：`dispatcher-design-subagent`
- **具体工作**：
  1. 分开分析“路由决策”和“消息处理”。
  2. 标记新增消息类型时必须同步修改的所有位置。
  3. 评估表驱动 dispatch 的收益/风险。
- **产出**：`router_dispatch_review.md`。

### CQ-4. 日志/断言/失败策略一致性审计

- **描述**：检查 `fatal/warn/printf/DPRINTF` 的使用是否一致，哪些路径属于协议错误、环境错误、可恢复错误、测试诊断输出。
- **依据**：`UBAdapter.cc`、`UBRouter.cc`、`UBCCController.cc` 当前混合使用 `fatal/warn/printf/DPRINTF`；这会影响离线验证、回归与未来 standalone 服务化。
- **Type**：out-of-place
- **建议子代理类型**：`runtime-policy-auditor`
- **具体工作**：
  1. 分类所有失败路径。
  2. 给出 fatal→panic_if / warn→stats / printf→debug macro 的整理建议。
  3. 标出必须保留“硬失败”的协议破坏点。
- **产出**：`logging_and_failure_policy.md`。

### CQ-5. 纯函数抽取与单元测试切入点识别

- **描述**：识别能从大控制器里抽出的纯逻辑：epoch compare、state canonicalization、target mask 计算、ack mask 更新、wire field validation。
- **依据**：`ResidentDir.cc:150-167` 已经证明 canonical validation 可以做成局部纯逻辑；`UBCCController` 和 `UBMsg` 仍有不少可提纯的 helper。
- **Type**：out-of-place
- **建议子代理类型**：`testability-review-subagent`
- **具体工作**：
  1. 列出当前依赖 `this`/`curTick()` 但其实可提纯的逻辑片段。
  2. 标出抽取后可直接单元测试/CBMC 的函数。
  3. 给出优先抽取顺序。
- **产出**：`pure_helper_candidates.md`。

### CQ-6. Builder / dispatcher / helper 落地重构

- **描述**：把 CQ-2/CQ-3/CQ-5 的离线结论真正落地到代码中，降低未来协议演进成本。
- **依据**：当前 `UBAdapter.cc` 和 `UBRouter.cc` 已出现重复构造与集中式大 switch。
- **Type**：needs code changes
- **需要落地的文件改动**：
  - `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.hh/.cc`
    - 新增 `buildBaseHeader()` / `fillCommonReqFields()` / 每类消息 builder。
  - `gem5/src/mem/ruby/protocol/chi/ep/UBRouter.hh/.cc`
    - 把 message type → handler 的分发表或小型 handler class 引入。
  - `gem5/src/mem/ruby/protocol/chi/ep/UBMsg.hh`
    - 新增字段校验 helper。
  - `tests/unit/test_ubrouter_dispatch.py`
    - 增加 dispatcher 行为测试。
- **改动目的**：减少复制粘贴 bug，方便后续独立化与协议扩展。

---

## 4. 优先级建议（只看“现在就能做”的）

### 第一优先级：完全不改代码、但价值最高

1. **FV-1 UBCC 状态机枚举**
2. **FV-2 M1 TLA+ 草案**
3. **MS-1 API 污染面清册**
4. **MS-3 host/transport/clock 边界切面分析**
5. **FV-5 UBMsg 线协议说明书**

这 5 项做完后，后续无论是接真实多节点框架、还是先做 standalone stub，都不会返工太多。

### 第二优先级：适合为后续改代码做铺垫

6. **FV-4 wait-for graph / 死锁环分析**
7. **MS-2 include/dependency graph**
8. **CQ-1 God object 热区分析**
9. **CQ-2 消息构造重复代码盘点**
10. **CQ-3 Router dispatcher 评估**

### 第三优先级：需要开始落地代码时再做

11. **FV-6 CBMC harness**
12. **FV-7 运行时 invariant 插桩**
13. **MS-4 UBMsg semantic/wire 拆分**
14. **MS-5 Tick 解耦**
15. **CQ-6 builder/dispatcher/helper 落地重构**

---

## 5. 直接回答用户问题

如果我现在碰不到真实多节点框架，我仍然能基于现有代码立即推进的，主要是：

1. **把 UBCC/EP 的协议语义抽成形式化模型**，尤其是单 line 的状态机、提交点、不变量、wait-for graph。
2. **把 UBMsg / Router / UBCC / EPBackend 的边界切干净**，提前知道哪些类型和 API 以后一定要从 gem5 中剥离。
3. **做高价值的只读重构分析**，例如职责热区、重复消息构造、dispatcher 膨胀、纯函数抽取点。

换句话说：

- **证明“协议是什么”**：现在就能做。
- **证明“模块怎么切”**：现在就能做。
- **证明“实现哪里值得先重构”**：现在就能做。
- **验证“真实多节点 runtime 行为是否正确”**：这部分才需要真实框架。
