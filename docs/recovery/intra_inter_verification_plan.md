# Intra/Inter-Node 验证计划

> 文档定位：本文是 `docs/recovery/verification_plan.md` 的执行化补充，专门回答 **M1 跨节点** / **M2 节点内** / **UBAdapter 边界契约** 三部分如何验证、按什么波次执行、每个 FV 任务由谁做、依赖什么、需要观测什么。  
> 不重复定义总体验证不变量；全局 safety/liveness 基线仍以 `docs/recovery/verification_plan.md` 为准，离线前置分析仍以 `docs/recovery/offline_analysis_report.md` 为准。

---

## Part 1. Strategy Overview

## 1.1 验证对象与边界

### 1.1.1 架构切分

```text
M1: Inter-node（允许 reorder + dup + loss）

EPBackend
  -> UBAdapter
  -> UBRouter
  -> UBCCController + ResidentDir + MetaRNF
  -> UBRouter
  -> UBAdapter
  -> Remote EPBackend

核心验证点：目录提交、epoch/reqId 门控、tombstone 幂等、故障恢复、跨节点活性


M2: Intra-node（只允许 reorder，不允许 dup/loss）

EPBackend <-> EPRNFController <-> HN-F / CHI-cache-actions.sm

核心验证点：snoop 分类延迟、recall/invalidate 链路、CompAck/CompData 完成、upgrade 串行化


Boundary: UBAdapter contract

保留：语义字段 + 路由字段
局部化：seqNum / enqueueTick / readyTick 等 runtime-local 字段
门控：epoch + reqId tuple 必须跨边界不失真
```

### 1.1.2 边界契约（Q5=B 固化）

| 类别 | 必须跨边界保持 | 允许本地化/重编码 | 验证重点 |
|---|---|---|---|
| 语义字段 | `type`、`homeLinePa/linePa`、`localLinePa`（若消息类型使用）、`flags` 中语义位、`epoch`、`reqId` | 无 | 不可丢失、不可静默改写 |
| 路由字段 | `homeNode/homeSocket`、`ingressSocket`、`requesterNode`、`targetNode`、`src/dst` 拓扑意图 | 具体队列实例、局部 MachineID 编码 | 路由必须与 home/ingress 语义一致 |
| runtime-local | `seqNum`、`enqueueTick`、`readyTick` | 可在本地重新赋值 | 不得参与协议仲裁 |

结论：**凡是参与授权/提交/去重/陈旧拒绝的字段，都必须在 UBAdapter 两侧逐项可对账。**

## 1.2 总体验证方法（Q1=C + Q2=C 固化）

采用三层闭环：

1. **L1：目录/epoch safety**  
   证明 `ResidentDir`、`OutstandingRequest`、`tombstone`、`epoch+reqId` 门控不会产生非法提交。
2. **L2：请求生命周期 safety**  
   证明 recall / invalidate / upgrade / clear 的创建、等待、完成、回放、退休无泄漏、无双重提交。
3. **L3：故障/liveness**  
   在 M1 的 reorder+dup+loss 与 M2 的 reorder 下，证明非 stale 请求最终完成或显式失败/重试，不产生死锁。

覆盖策略采用“双层覆盖”：

- **规范序列层**：以 TC/协议路径为主，便于直接映射回归测试。
- **可组合原语层**：以状态边/消息原语为主，便于形式化与缺口发现。

## 1.3 Wave-based 执行计划（Q4=C 固化）

### Wave 0：立即执行，无需新插桩

目标：先冻结边界表、状态空间、目录不变量、生命周期基线。

| 优先级 | 任务 | 产出 |
|---|---|---|
| P0 | FV-9 | UBMsg 字段/合法值表，冻结边界契约 |
| P1 | FV-1 | `MESI × OpType × OpStage` 枚举与非法边表 |
| P2 | FV-2 | epoch/sharersMask 不变量证明笔记 |
| P3 | FV-3 | OutstandingRequest 生命周期审计 |
| P4 | FV-11 | 状态边 -> TC 覆盖矩阵与 uncovered 清单 |

### Wave 1：插桩设计收敛 + 推荐插桩任务执行

目标：把 M2 可观察性补齐，并建立 UBAdapter 边界 round-trip 证据。

| 优先级 | 任务 | 产出 |
|---|---|---|
| P0 | FV-10 | 语义+路由字段 round-trip 对账基线 |
| P1 | FV-6 | snoop 分类延迟正确性报告 |
| P2 | FV-8 | invalidate barrier 链路验证报告 |

### Wave 2：故障模型/活性/召回链路收敛

目标：在稳定插桩上完成 M1 故障恢复与端到端活性闭环。

| 优先级 | 任务 | 产出 |
|---|---|---|
| P0 | FV-4 | reorder+dup+loss 故障恢复证据 |
| P1 | FV-7 | recall 数据路径端到端证据 |
| P2 | FV-5 | 活性/无死锁结论 |

### 波次退出准则

- **Wave 0 退出**：`FV-1/2/3/9` 完成，且 `FV-11` 能列出明确 uncovered edges。  
- **Wave 1 退出**：能稳定采集 boundary trace、upgrade/snoop trace、invalidate barrier trace。  
- **Wave 2 退出**：故障恢复、recall 路径、活性三者结果互相一致，且所有 uncovered 边有 TC、分析豁免或后续测试计划。

## 1.4 插桩策略

### 1.4.1 允许范围

- 仅允许 **debug-time-only** 插桩；通过 `--debug-flags` 打开。
- 允许零语义变化的辅助产物：脚本、表格、schema、trace parser、coverage extractor。
- 禁止把 debug 字段写入生产协议状态；不得改变授权/提交/仲裁结果。

### 1.4.2 建议调试面

- `RubyEP`
- `RubyCHIGeneric`
- `RubySlicc`
- 若新增 debug 事件，必须只输出观测数据，不改变时序/状态机分支。

### 1.4.3 插桩分级

| 分级 | 任务 | 含义 |
|---|---|---|
| 无插桩 | FV-1/2/3/9/11 | 只靠代码阅读、现有日志、静态枚举 |
| 推荐插桩 | FV-6/8/10 | 无插桩可做，但效率和结论可信度明显下降 |
| 必需插桩 | FV-4/5/7 | 没有观测点或故障注入点就无法形成结论 |

## 1.5 Agent 选型原则

| 子代理 | 适用任务 | 使用原则 |
|---|---|---|
| `state-analyzer (GPT-5.4)` | 状态枚举、不变量证明、死锁图 | 仅用于高状态空间任务 |
| `intelligent-guider (GPT-5.4)` | 故障模型、活性、竞态、复杂链路 | 用于跨边界/跨层闭环任务 |
| `medium-guider (GPT-5.3-Codex)` | 路径跟踪、接口契约、覆盖映射 | 代码级追踪 |
| `protocol-analyzer (DeepSeek V4-Pro)` ★ | 消息流分析、UBMsg验证、覆盖率 | 中度协议分析，output成本低 |
| `quick-analyzer (DeepSeek V4-Flash)` ★ | 简单表生成、字段枚举、矩阵 | 超低成本批量分析 |
| `flash-scanner (DeepSeek v4-Flash)` | 批量日志筛查、异常归类 | 只做廉价 triage，不负责最终证明 |

选型规则：

1. **先用中等成本模型做结构化抽取**；
2. **只有遇到状态爆炸、竞态归纳、活性证明时才升级到重模型**；
3. **日志海量筛查一律交给 flash-scanner 做预清洗**。

### 成本上限

累计超过 **$13.00** 立即暂停并汇报当前进度。每波次任务执行完后必须查询本轮已消耗成本。

定价速查（$/MTok input/output）：

| 模型 | Input | Output | Agent |
|------|-------|--------|-------|
| DeepSeek V4 Flash | $0.14 | $0.28 | quick-analyzer, flash-scanner |
| DeepSeek V4 Pro | $1.74 | $3.48 | protocol-analyzer, code-implementer |
| GPT-5.3 Codex | $1.75 | $14.00 | medium-guider |
| GPT-5.4 | $2.50 | $15.00 | state-analyzer, intelligent-guider, failure-analyst |

### 成本查询 SOP

累计成本已持久化在 `/tmp/cost.txt`（仅跟踪本次验证任务，不含项目历史）。

每次完成一组 subagent 调用后，更新成本：

```bash
# 1. 查询最近 N 次 subagent 调用的累计成本
sqlite3 -column -header ~/.local/share/opencode/opencode.db \
  "SELECT agent, printf('$%.6f', cost) AS cost, tokens_input, tokens_output, title
   FROM session WHERE parent_id IS NOT NULL
   ORDER BY time_created DESC LIMIT 10;"

# 2. 将增量成本加到 /tmp/cost.txt 的 fv_total 上
# 3. 检查 fv_total 是否超过 budget=13.00
cat /tmp/cost.txt
```

如累计超过 $13.00，停止提交新任务，输出当前进度报告。

## 1.6 关键代码锚点

- `UBCCController.hh:59-179`：`OpType` / `OpStage` / `OutstandingRequest` 定义  
- `UBCCController.cc:371-1030, 1058-1307, 1739-2263, 2467-2585`：外部请求、ack、upgrade、clear、tombstone、replay  
- `ResidentDir.hh:14-43`, `ResidentDir.cc:151-167`：目录 canonical invariant  
- `EPBackend.hh:108-200, 255-295`：Outer 消息与 requester 状态定义  
- `EPBackend.cc:517-816`：remote miss / outerTxnPending / Clear  
- `EPBackend.cc:1154-1315`：recall 端到端路径  
- `EPBackend.cc:1530-1892`：invalidate / local upgrade / UpgradeDone  
- `EPRNFController.hh:241-476`：PendingChiTxn、retry、upgradePending、outerTxnPending  
- `EPRNFController.cc:331-395, 470-545, 625-943, 947-1086, 1088-1399`：snoop、CompData、CHI 请求、finish、upgrade 延迟  
- `UBMsg.hh:17-209`：UBMsg schema  
- `UBAdapter.cc:63-170, 175-681, 722-809`：边界构造/重构  
- `UBRouter.cc:91-223, 228-500`：M1 传输与本地交付  
- `CHI-cache-actions.sm:1973-2523`：SnpUnique/SnpShared/SnpOnce/Fwd 约束与 EP-RNF sharer 注册  
- `tests/e2e/test_e2e.py`：TC1-TC22 现有回归入口

---

## Part 2. Per-FV Task Cards

## FV-1：MESI × OpType × OpStage 状态枚举与非法转移检测

- **所属波次**：Wave 0
- **目标**：给出 UBCC 单行状态机的可达组合、合法边、非法边，以及每条边的提交点/非提交点。
- **范围**：
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh:59-179`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:371-1030`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:1058-1307`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:1739-2100`
  - `gem5/src/mem/ruby/protocol/chi/ep/ResidentDir.hh:14-43`
- **方法**：静态分析 + 表格化枚举；按 `CommittedState × LiveOutstanding × InputEvent -> {next, actions, commit?}` 输出黄金表。
- **插桩需要**：无。
- **主代理**: `state-analyzer (GPT-5.4)`
- **咨询**: `state-analyzer (GPT-5.4)`
- **预期交付物**：`fv1_ubcc_state_enumeration.md`，包含非法边列表与“只改 live state / 真正提交目录”二分标记。
- **依赖**：无。
- **波次内优先级**：P1。

## FV-2：Epoch 单调性 + sharersMask 不变量证明

- **所属波次**：Wave 0
- **目标**：证明 committed epoch 只前进不回退，且 `G_S/G_E/G_M/G_I` 的 sharers 约束不被 `Clear/UpgradeDone/InvalidateAck` 破坏。
- **范围**：
  - `gem5/src/mem/ruby/protocol/chi/ep/ResidentDir.hh:14-43`
  - `gem5/src/mem/ruby/protocol/chi/ep/ResidentDir.cc:151-167`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:1218-1294`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:2019-2078`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:2127-2176`
- **方法**：静态证明，逐点核对 epoch 比较、reservedEpoch 分配、commit 语义与 ResidentDir canonical check。
- **插桩需要**：无。
- **主代理**: `state-analyzer (GPT-5.4)`
- **咨询**: `state-analyzer (GPT-5.4)`
- **预期交付物**：`fv2_epoch_sharers_invariants.md`，包含“哪几个写点会改 committed epoch / sharersMask”的证明表。
- **依赖**：建议先完成 FV-1。
- **波次内优先级**：P2。

## FV-3：OutstandingRequest 生命周期——无泄漏、replayArmed、ackMask

- **所属波次**：Wave 0
- **目标**：确认 `OutstandingRequest` 从创建到退休的每条路径都能收敛，且 `replayArmed`、`ackMask`、`pendingAckCount` 不会悬挂或回退。
- **范围**：
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh:80-161`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:427-539`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:1196-1385`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:1936-2100`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:2197-2240`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:2467-2585`
- **方法**：静态路径追踪；按 `create -> wait -> done/tombstone/remove -> replay` 链路做生命周期表。
- **插桩需要**：无。
- **主代理**: `quick-analyzer (DeepSeek V4-Flash)`
- **咨询**: `state-analyzer (GPT-5.4)`
- **预期交付物**：`fv3_outstanding_lifecycle.md`，包含 leak checklist、ackMask 单调性表、replayArmed 触发/清除条件。
- **依赖**：建议先完成 FV-1。
- **波次内优先级**：P3。

## FV-4：Fault model（reorder + dup + loss）——tombstone replay / Clear dedup / stale-epoch rejection

- **所属波次**：Wave 2
- **目标**：在 M1 里验证重复、乱序、丢包下的去重、陈旧拒绝、tombstone 幂等与 replay 恢复是否成立。
- **范围**：
  - `gem5/src/mem/ruby/protocol/chi/ep/UBRouter.cc:91-223`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc:421-629, 722-809`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:527-539`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:1058-1110`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:1196-1273`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:1961-2240`
  - `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc:599-604, 746-751, 810-814`
- **方法**：必需插桩 + 故障注入脚本；仅在 M1 上注入 reorder/dup/loss，不在 M2 上注入 dup/loss。
- **插桩需要**：**必需**。
- **具体插桩点**：
  - `UBRouter.cc:91-110`：记录发送前快照；增加 debug-only fault decision（正常/重排/复制/丢弃）。
  - `UBRouter.cc:115-223`：记录实际出队顺序、readyTick、交付目的端；用于证明“观察到的乱序”而非“消息构造顺序”。
  - `UBAdapter.cc:421-505, 526-545, 551-629`：记录 `ClearReq`、`RecallResp`、`InvalidateAck` 的边界字段快照。
  - `UBCCController.cc:1058-1110`：记录 recall stale epoch / owner mismatch / reqId mismatch 拒绝原因。
  - `UBCCController.cc:1196-1273`：记录 invalidate ack 的 duplicate / not-in-target / stale 拒绝原因。
  - `UBCCController.cc:1961-2240`：记录 tombstone hit、Clear epoch mismatch、reqId mismatch、accepted replay。
  - `EPBackend.cc:599-604, 746-751, 810-814`：记录 `outerTxnPending` 的 set/clear，避免把“协议不活”与“busy 窗口没释放”混淆。
- **主代理**: `intelligent-guider (GPT-5.4)`
- **咨询**: `flash-scanner (DeepSeek v4-Flash)`
- **预期交付物**：`fv4_fault_recovery_report.md` + `fault_trace_schema.md` + 一组故障脚本配置。
- **依赖**：FV-3、FV-9、FV-10。
- **波次内优先级**：P0。

## FV-5：Liveness——每个 non-stale 请求最终完成，且无死锁

- **所属波次**：Wave 2
- **目标**：证明在公平重试假设下，请求不会永久卡在 outstanding、retry、queued snoop、pending requester 或 pending HN response 上。
- **范围**：
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:427-523`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:1739-1955`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:1961-2100`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:2467-2585`
  - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:331-369`
  - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:866-1086`
  - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:1348-1399`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBRouter.cc:115-223`
- **方法**：必需插桩 + wait-for graph + 活性 trace；从“有无永久未清理实体”倒推可能死锁环。
- **插桩需要**：**必需**。
- **具体插桩点**：
  - `UBCCController.cc:2545-2585`：每次 create/remove outstanding 记账，导出 per-line live count。
  - `UBCCController.cc:2467-2531`：记录 pending requester 出队/重放/再次阻塞。
  - `UBCCController.cc:1930-1955, 2076-2100`：记录真正 commit 的时间点与退休时间点。
  - `EPRNFController.cc:866-943`：记录 queued snoop 是否被消费、finishChiTxn 后是否继续推进。
  - `EPRNFController.cc:947-1086`：记录 deferred CHI req、CompAck retry、_chiRequestInFlight 清零时机。
  - `UBRouter.cc:115-223`：记录队列积压时长，区分“未 ready”与“已 ready 未送达”。
- **主代理**: `intelligent-guider (GPT-5.4)`
- **咨询**: `state-analyzer (GPT-5.4)`
- **预期交付物**：`fv5_liveness_deadlock_report.md`，包含 wait-for graph、活性假设、无死锁结论或最小反例。
- **依赖**：FV-1、FV-3、FV-4。
- **波次内优先级**：P2。

## FV-6：Snoop handling correctness——`SnpCleanInvalid -> OuterUpgradeReq -> Ack -> SnpResp_I` 及分类延迟

- **所属波次**：Wave 1
- **目标**：验证 snoop 延迟分类符合“`SnpCleanInvalid` 延迟、recall snoop 排队、其他 BUSY/即时响应”的既定设计，并识别与黄金矩阵的漂移。
- **范围**：
  - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.hh:241-443`
  - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:625-795`
  - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:1348-1399`
  - `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc:1530-1600`
  - `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc:1634-1759`
  - `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc:1865-1892`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:1739-1955`
  - `gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm:1973-2228`
- **方法**：静态矩阵核对 + 推荐插桩 trace；重点比较规范矩阵与当前实现。
- **插桩需要**：**推荐**。
- **具体插桩点**：
  - `EPRNFController.cc:657-743`：记录 `SnpCleanInvalid` 进入 upgrade path 还是 non-upgrade path，以及是否发生 deferred `SnpResp_I`。
  - `EPRNFController.cc:747-795`：记录 `SnpUnique/SnpOnce` 响应类型；特别标记 `retToSrc=true` 分支，检查是否与黄金矩阵不一致。
  - `EPBackend.cc:1530-1600`：记录 `startCleanUnique` 回调时刻与 `InvalidateAck` 发送时刻。
  - `EPBackend.cc:1865-1892`：记录 `notifyUpgradeAckReady -> receiveUpgradeAck` 的桥接。
  - `UBCCController.cc:1739-1955`：记录 upgrade accepted/deferred/commit 三阶段。
- **主代理**: `quick-analyzer (DeepSeek V4-Flash)`
- **咨询**: `intelligent-guider (GPT-5.4)`
- **预期交付物**：`fv6_snoop_matrix_report.md`，包含“符合 / 漂移 / 未覆盖”三类结论。
- **依赖**：FV-9。
- **波次内优先级**：P1。

## FV-7：Recall data path——`OuterRecallMsg -> startReadShared/Unique -> callback -> RecallResponse -> UBCC`

- **所属波次**：Wave 2
- **目标**：验证 recall 请求、CHI 数据采集、home 安装、UBCC 受理与 grant data source 选择构成闭环，且不会错配 owner/epoch/reqId。
- **范围**：
  - `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh:108-138`
  - `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc:1154-1315`
  - `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc:500-816`
  - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:470-547`
  - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:899-943`
  - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:1088-1180`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc:462-590, 762-783`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBRouter.cc:417-435`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:1058-1154`
  - `gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm:2436-2512`
- **方法**：必需插桩 + 端到端 trace 对账；覆盖 read recall 与 write recall 两支。
- **插桩需要**：**必需**。
- **具体插桩点**：
  - `EPBackend.cc:1154-1267`：记录 recall 入参、选择 `ReadShared` 还是 `ReadUnique`、callback 成功位、`dataReturned/hasDataPayload`。
  - `EPRNFController.cc:470-547`：记录 `CompData` beat 到达、`recallDataBlk` 是否稳定、`CompAck` 时机。
  - `EPRNFController.cc:899-943`：记录 `finishChiTxn` 前后 `recallDataValid` 与 callback 调用顺序。
  - `EPBackend.cc:1271-1315`：记录 home memory install 与 `sendRecallResp` 元组。
  - `UBAdapter.cc:551-590, 462-505`：记录 RecallReq/RecallResp 边界字段快照。
  - `UBRouter.cc:430-433`：记录 RecallResp 到达 home UBCC 的最终元组。
  - `UBCCController.cc:1058-1154`：记录 recall target 校验、reqId 校验、dataBuf 安装、barrier release。
- **主代理**: `intelligent-guider (GPT-5.4)`
- **咨询**: `state-analyzer (GPT-5.4)`
- **预期交付物**：`fv7_recall_path_report.md`，至少包含共享 recall、unique recall、回调失败、陈旧 recall 四类轨迹。
- **依赖**：FV-9、FV-10；建议先完成 FV-6。
- **波次内优先级**：P1。

## FV-8：Invalidate barrier——`startCleanUnique(InvalidateOnly) -> callback -> InvalidateAck`

- **所属波次**：Wave 1
- **目标**：证明无效化屏障是“先经 HN-F 完成、后发 `InvalidateAck`”，而不是本地提前确认。
- **范围**：
  - `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc:1530-1600`
  - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:397-455`
  - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:1183-1235`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc:511-629, 785-801`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:1196-1307`
- **方法**：推荐插桩 + 回调时序核对；对比 callback 前后是否存在提前 ack。
- **插桩需要**：**推荐**。
- **具体插桩点**：
  - `EPBackend.cc:1562-1583`：记录 `startCleanUnique` 发起时刻、callback 时刻、`sendInvalidationAck` 时刻。
  - `EPRNFController.cc:397-455`：记录 `Comp_UC` 到达及 `finishChiTxn` 触发点。
  - `EPRNFController.cc:1183-1235`：记录 `CleanUnique` 重复请求、send 失败、正常完成三类分支。
  - `UBAdapter.cc:511-545, 595-629`：记录 `InvalidateAck` 与 `InvalidateReq` 的边界元组。
  - `UBCCController.cc:1275-1307`：记录 ackMask 递增与 remaining count 递减。
- **主代理**: `quick-analyzer (DeepSeek V4-Flash)`
- **咨询**: `intelligent-guider (GPT-5.4)`
- **预期交付物**：`fv8_invalidate_barrier_report.md`，含“无提前 ack”证明与失败路径说明。
- **依赖**：FV-9。
- **波次内优先级**：P2。

## FV-9：UBMsg field validation table——每类消息的必填/可选/取值范围

- **所属波次**：Wave 0
- **目标**：冻结 UBAdapter 边界上每种 UBMsg 的必填字段、可选字段、非法 flag 组合与取值范围。
- **范围**：
  - `gem5/src/mem/ruby/protocol/chi/ep/UBMsg.hh:17-209`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc:63-717`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBRouter.cc:228-482`
- **方法**：静态 schema 审计；对每种 `UBMsgType` 建立 header/body/flags 规则表。
- **插桩需要**：无。
- **主代理**: `quick-analyzer (DeepSeek V4-Flash)`
- **咨询**: 无。
- **预期交付物**：`fv9_ubmsg_validation_table.md`，至少列出 required/optional/forbidden 字段与合法 flag 组合。
- **依赖**：无。
- **波次内优先级**：P0。

## FV-10：Serialization round-trip——`UBMsg -> binary -> UBMsg` 对语义+路由字段无损

- **所属波次**：Wave 1
- **目标**：证明 UBAdapter 边界可构造一个稳定 round-trip schema，使语义字段和路由字段无损往返，而 runtime-local 字段不被误当协议语义。
- **范围**：
  - `gem5/src/mem/ruby/protocol/chi/ep/UBMsg.hh:51-209`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc:86-107, 190-207, 244-258, 300-318, 367-382, 421-438, 479-501, 526-541, 564-587, 608-625, 648-660, 700-713`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBRouter.cc:91-223`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc:722-809`
- **方法**：推荐插桩 + 外部 helper schema；不要求改协议语义，只要求建立可验证的 pack/unpack 对账表。
- **插桩需要**：**推荐**。
- **具体插桩点**：
  - `UBAdapter.cc` 各 `send*` 构造点：记录发送前 header/body 语义快照。
  - `UBRouter.cc:91-110, 130-206`：记录队列入队前快照与交付前快照。
  - `UBAdapter.cc:722-809`：记录接收后重构的 Outer 消息字段。
  - `UBMsg.hh:51-209`：作为 schema 权威，导出对账字段表。
- **主代理**: `quick-analyzer (DeepSeek V4-Flash)`
- **咨询**: `flash-scanner (DeepSeek v4-Flash)`
- **预期交付物**：`fv10_roundtrip_schema.md` + `ubmsg_roundtrip_cases.json`。
- **依赖**：FV-9。
- **波次内优先级**：P0。

## FV-11：State-edge -> TC coverage matrix——找出未覆盖边

- **所属波次**：Wave 0
- **目标**：把 FV-1 的状态边映射到现有 TC，识别“未被任何 TC 触达”的边和“只有日志证据、没有值证据”的边。
- **范围**：
  - `docs/recovery/verification_plan.md`
  - `docs/recovery/offline_analysis_report.md`
  - `tests/e2e/test_e2e.py`
  - `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc`
  - `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc`
  - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc`
  - `gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm`
- **方法**：从状态边出发做覆盖映射，而不是从功能点出发；给每条关键边分配“已覆盖 / 间接覆盖 / 未覆盖 / 需新 TC”。
- **插桩需要**：无。
- **主代理**: `quick-analyzer (DeepSeek V4-Flash)`
- **咨询**: `flash-scanner (DeepSeek v4-Flash)`
- **预期交付物**：`fv11_state_edge_tc_matrix.md`，并给出优先补测清单。建议至少优先检查：
  - TC5：竞争完成/最终收敛
  - TC6：重复共享 miss / replay
  - TC8：invalidate + upgrade
  - TC10：并发压力
  - TC11：本地升级 snoop 链
  - TC15：RetryAck / PCrdGrant 恢复
  - TC16：双升级竞争
  - TC17：writeback + remote-read overlap
  - TC18/TC19：replay / dirty persist
  - TC22：ResidentDir 容量压力
- **依赖**：FV-1；建议参考 FV-6/FV-7 的关键边列表。
- **波次内优先级**：P4。

---

## 3. 执行顺序建议（精简版）

1. **先冻结边界**：FV-9  
2. **再冻结状态空间**：FV-1、FV-2、FV-3  
3. **把测试映射回来**：FV-11  
4. **补足边界与 M2 观测性**：FV-10、FV-6、FV-8  
5. **最后做 M1 故障与全局闭环**：FV-4、FV-7、FV-5

这样安排的原因：

- 没有 FV-9，后续所有“边界保持”结论都不稳；
- 没有 FV-1/2/3，无法判断 fault/liveness 看到的是协议 bug 还是状态机理解错误；
- 没有 FV-10，跨节点故障注入日志无法证明字段未漂移；
- 没有 FV-6/8，Wave 2 的 recall/liveness 很容易把 M2 分类延迟问题误判为 M1 问题。

---

## 4. 最终验收标准

满足以下条件时，可认为 Intra/Inter-node 验证计划执行完成：

1. `FV-1` 到 `FV-11` 全部有交付物；
2. 所有 required instrumentation 都有明确 trace schema；
3. M1 的 reorder+dup+loss 已验证以下三项：
   - tombstone replay 幂等；
   - Clear / InvalidateAck / RecallResp 的 stale tuple 被拒绝；
   - 非 stale 请求不会永久卡死；
4. M2 的 snoop / invalidate / recall / upgrade 四条关键链路均有至少一条成功轨迹和一条异常轨迹；
5. `FV-11` 中每条关键状态边都被归类为：
   - 已有 TC 覆盖；或
   - 有离线证明覆盖；或
   - 已列入新增 TC 计划。

---

## 5. 备注

- 当前实现中，`EPRNFController.cc:747-785` 的 `SnpUnique` 响应策略与 `verification_plan.md` 中的黄金矩阵存在潜在漂移；FV-6 必须把它作为显式检查项，而不是默认当作已满足。  
- `UBRouter.cc` 是 **唯一** 合法的 M1 故障注入锚点；不得把 dup/loss 注入到 M2。  
- `seqNum/enqueueTick/readyTick` 可用于调试，但不得作为协议正确性判据；正确性只看 `type/PA/epoch/reqId` 与相应 flags/路由字段。  
- 若 Wave 2 结论与 Wave 0/1 的静态结论冲突，应优先回查边界契约与插桩语义，而不是直接修改协议代码。
