# 甲方 HA 技术确认与目标 3 评审 Checklist

> 版本：1.0
> 日期：2026-08-07
> 适用范围：合同目标 3，`OurCC 跨节点 CC 同步平均时延 < 甲方 HA 实现的理论平均时延`。
> 使用对象：甲方 HA 技术接口人、甲方体系结构/协议负责人、甲方合同验收评审小组、我方交付团队。
> 保密原则：本清单只要求抽象协议语义、时序边界、参数区间或 black-box 证据，不要求披露 RTL、
> 私有状态编码、内部模块名称或其他商业秘密。

---

## 0. 使用说明

### 0.1 回答状态

每一项请选择一种状态：

| 状态 | 含义 |
|---|---|
| `[已确认]` | 已给出明确答案和证据来源 |
| `[部分确认]` | 仅部分操作、配置或场景适用 |
| `[未知]` | 当前无法确认，保留全部合法分支和上下界 |
| `[不适用]` | 该机制在甲方 HA 中不存在，需说明替代机制 |
| `[不披露]` | 不披露实现，但同意采用指定的保守比较分支 |

### 0.2 推荐答复格式

```text
问题 ID：
状态：[已确认/部分确认/未知/不适用/不披露]
选择项：[A/B/C/D/自定义]
适用操作：[R_h/R_o/W_s/W_o/M/C/全部]
适用配置或地址域：
完成点：[T_visible/T_commit/T_next/ISA root]
参数值或区间：
例外与 fallback：
证据形式：[接口定义/设计说明/脱敏时序图/black-box counter/共同试验]
证据版本与日期：
甲方责任人：
甲方确认日期：
```

### 0.3 未回答项处理规则

1. 未回答项一律保持 `[未知]`，不得由任何一方代填为有利值。
2. `[未知]` 项按全部合法分支计算上下界。
3. 缺少安全完成证明的“更快路径”不进入合法理论下界。
4. 未冻结 `authority、commit、root completion、placement、service` 的 case 不进入 `STRICT PASS`。
5. 甲方可选择“不披露但同意采用保守分支”，从而在不泄露实现的情况下关闭问题。

### 0.4 最短决策集

以下五项未关闭时，目标 3 总体保持 `UNPROVEN`：

- [ ] `HA-T01`：远端响应是否能够独立完成 requester 权限授予。
- [ ] `HA-T02`：HA metadata 的原子 commit 事件。
- [ ] `HA-T03`：合同 root completion 的保证边界。
- [ ] `HA-T04`：HA/Home 的物理 placement 和地址映射。
- [ ] `HA-T05`：HA 本地 service/queue 的参数或共同 black-box counter。

---

## 1. 已知条件书面复核

以下内容来自当前沟通记录。请甲方技术接口人复核，避免后续对“已知条件”产生歧义。

| ID | 当前记录 | 甲方复核 | 备注/修订 |
|---|---|---|---|
| HA-K01 | 系统工作域为 2 节点 | [ ] 正确 [ ] 修订 [ ] 未知 |  |
| HA-K02 | 全局缓存地址空间不超过 128 MiB | [ ] 正确 [ ] 修订 [ ] 未知 |  |
| HA-K03 | 使用节点级 VI 协议 | [ ] 正确 [ ] 修订 [ ] 未知 |  |
| HA-K04 | 每 Cacheline 有 2-bit 节点级 metadata | [ ] 正确 [ ] 修订 [ ] 未知 |  |
| HA-K05 | 当前理解：1 bit 表示 VI state | [ ] 正确 [ ] 修订 [ ] 未知 | 请给抽象含义 |
| HA-K06 | 当前理解：1 bit 表示唯一远端节点是否存在副本 | [ ] 正确 [ ] 修订 [ ] 未知 | 请确认是 presence 还是其他含义 |
| HA-K07 | 没有额外的节点级 dirty-owner metadata | [ ] 正确 [ ] 修订 [ ] 未知 | 节点内 HN 状态不计入此项 |
| HA-K08 | 节点内详细状态可由现有 HN/CHI 维护 | [ ] 正确 [ ] 修订 [ ] 未知 |  |
| HA-K09 | metadata 位于 HA 或 IODie，当前比较不区分二者 lookup 时延 | [ ] 正确 [ ] 修订 [ ] 未知 | 物理 placement 仍需单独确认 |
| HA-K10 | metadata lookup 可近似视为零附加时延 | [ ] 正确 [ ] 修订 [ ] 未知 | 不等于完整 operation service 为零 |
| HA-K11 | Requester 到 HN 的公共前缀与 OurCC 相同 | [ ] 正确 [ ] 修订 [ ] 未知 |  |
| HA-K12 | Remote Read 从 valid/latest data 所在位置取数 | [ ] 正确 [ ] 修订 [ ] 未知 | 数据位置和返回路由仍需确认 |
| HA-K13 | 合同比较按 lossless transport，不加入消息丢失重试 | [ ] 正确 [ ] 修订 [ ] 未知 | 请确认 duplicate/replay 是否可能 |
| HA-K14 | 网络不保证全局 FIFO | [ ] 正确 [ ] 修订 [ ] 未知 | 需确认同址因果保护 |
| HA-K15 | 不同地址事务可按 ARM/RISC-V 弱内存序乱序完成 | [ ] 正确 [ ] 修订 [ ] 未知 | 需确认 ISA 和 barrier scope |
| HA-K16 | HA 与 OurCC 使用相同时钟尺度 | [ ] 正确 [ ] 修订 [ ] 未知 | 请给频率或归一化方式 |

**本节关闭条件：**HA-K01 至 HA-K16 均有书面复核结果；修订项进入后续参数账本。

---

## 2. HA 技术接口人确认清单

### 2.1 远端数据与权限返回路径

本节不预设甲方存在“Peer Direct”。需要确认的是实际数据和权限依赖。

#### HA-T01：远端响应是否能够独立完成 requester 权限授予

- [ ] `[未知]`：尚未确认远端节点是否直返，以及直返内容。
- [ ] `[A: Central Return]`：远端数据/完成先返回 HA，HA 再向 requester 返回数据和权限。
- [ ] `[B: Direct Data Only]`：远端可直接向 requester 返回数据，但 requester 仍等待 HA 的 Grant/permission。
- [ ] `[C: Direct Data + HA Token]`：HA 预授权远端节点，远端 response 可同时携带数据和可验证权限。
- [ ] `[D: Peer Is Authority]`：远端节点本身可作为该事务的授权权威。
- [ ] `[E: No Remote Direct Route]`：远端节点不能直接响应 requester。

需要补充：

- [ ] 该能力分别适用于 `R_h/R_o/W_s/W_o` 中哪些操作。
- [ ] Direct response 是否携带 PA、requester identity、transaction ID、version/epoch。
- [ ] Requester 如何验证 response 来源和权限有效性。
- [ ] Data 先到、permission 后到时，哪个事件构成 root completion。
- [ ] Direct 路径失败时的 fallback 是 central return、retry 还是终止。

对判定的影响：`B` 只缩短 data path，不自动缩短 permission critical path；`C/D` 可能形成更短的
`R->H->Peer->R` 合法路径，但必须同时关闭 HA-T06、HA-T08、HA-T09。

可接受证据：抽象 sequence diagram、接口语义、脱敏 event trace、共同 black-box 试验。

#### HA-T01A：各操作的 route profile

| 操作 | `[未知]` | Central | Direct data only | Direct data+authority | Fallback/备注 |
|---|:---:|:---:|:---:|:---:|---|
| `R_h` Home memory latest read | [ ] | [ ] | [ ] | [ ] |  |
| `R_o` Remote owner latest read | [ ] | [ ] | [ ] | [ ] |  |
| `W_s` Shared-to-writer | [ ] | [ ] | [ ] | [ ] |  |
| `W_o` Ownership handoff | [ ] | [ ] | [ ] | [ ] |  |

### 2.2 Metadata commit 和对外完成点

#### HA-T02：每条 Cacheline 的 metadata 原子 commit 事件

- [ ] `[未知]`：commit 点尚未确认。
- [ ] `[A]`：发出 Grant 前或与 Grant 注入原子完成。
- [ ] `[B]`：收到远端 owner/sharer completion 后 commit，再发 Grant。
- [ ] `[C]`：requester 数据/权限安装完成后 commit。
- [ ] `[D]`：先清旧 owner/sharer，后安装新 requester，属于 split commit。
- [ ] `[E]`：其他事件，请给抽象定义。

需要补充：

- [ ] commit 前后的 stable/transient state。
- [ ] commit 是否同时更新 owner、sharer、dirty/latest 或 version 信息。
- [ ] commit 后是否立即允许下一同址冲突进入。
- [ ] 若 commit 早于 requester install，如何处理 Recall-before-Grant/Install。
- [ ] transaction 失败、retry 或 timeout 时是否 rollback。

对判定的影响：定义 `T_commit` 和 `T_next`。Eager commit 只有在 HA-T08/HA-T09 提供安全 guard 时
才可进入合法理论下界。

#### HA-T03：合同 root completion 对外保证到哪一点

- [ ] `[未知]`：root completion 未定义。
- [ ] `[A]`：requester 已取得 latest data 和安全 authority，即 `T_visible`。
- [ ] `[B]`：HA authoritative metadata 已 commit，即 `T_commit`。
- [ ] `[C]`：下一同址冲突可安全继续，即 `T_next`。
- [ ] `[D]`：ISA ordinary load/store retire。
- [ ] `[E]`：ISA completed store、release/acquire、DMB/DSB/FENCE 完成。
- [ ] `[F]`：不同 API 使用不同 stop point，请逐项填写 HA-T11。

需要补充：

- [ ] Data arrival 是否可能早于 authority。
- [ ] Requester install 是否有可观测 completion。
- [ ] Root completion 是否等待 HA commit。
- [ ] Root completion 是否等待同址 lock/token release。

### 2.3 物理拓扑和地址映射

#### HA-T04：HA/Home placement

- [ ] `[未知]`：物理位置尚未确认。
- [ ] `[A]`：HA 与 Node0/HN0 共址。
- [ ] `[B]`：HA 与 Node1/HN1 共址。
- [ ] `[C]`：按 PA hash/interleave 分布到每节点 HA slice。
- [ ] `[D]`：HA 位于独立 IODie/central die。
- [ ] `[E]`：其他拓扑。

需要补充：

- [ ] 地址到 Home/HA 的映射函数或抽象规则。
- [ ] Node0、Node1、HA/IODie 之间哪些 edge 跨物理 fabric。
- [ ] HA 与本地 HN 之间是 local service 还是一次 fabric traversal。
- [ ] 不同 placement 是否使用相同单向链路时延。
- [ ] Requester/Home/owner 共址时本地 edge 的 cycle 区间。

对判定的影响：逻辑 `K_logical` 不能直接作为物理 `K_crossnode`。2 节点中
`R->H->Peer->H->R` 常只有两次真实跨节点 traversal。

### 2.4 HA 本地 service 和 queue

#### HA-T05：HA operation service 参数

请提供 cycle、ns 或共同 black-box counter；若不披露绝对值，可提供上下界。

| 参数 | 空载/无争用 | 合同冻结负载 | 状态/证据 |
|---|---:|---:|---|
| `P_dir` metadata lookup |  |  | 当前记录为近似 0 附加时延 |
| `P_owner_select` latest/owner 解析 |  |  |  |
| `P_commit` metadata commit |  |  |  |
| `P_install` requester install |  |  | 若可观测 |
| `P_peer` remote HN/cache service |  |  |  |
| `P_queue` HA/HN/fabric queue |  |  |  |
| `P_retry` NACK/retry 平均成本 |  |  |  |

- [ ] `[未知]`：只能采用 `P_min >= 0`，无法关闭同 K 下的严格大小关系。
- [ ] `[A]`：提供固定 cycle 区间。
- [ ] `[B]`：提供 black-box event counter。
- [ ] `[C]`：提供双方共同 workload 的 target-visible 数据。
- [ ] `[D]`：不披露，接受按指定保守上下界比较。

### 2.5 Write policy 和 latest data 定位

#### HA-T06：写策略

- [ ] `[未知]`。
- [ ] `[A: Write-through]`：写入同步或异步更新 Home memory。
- [ ] `[B: Write-back]`：dirty/latest data 可只存在于节点 cache/HN。
- [ ] `[C: Hybrid]`：按地址域、状态或操作选择 WT/WB。
- [ ] `[D: Update-on-transfer]`：ownership transfer 时更新，普通写不立即写 Home memory。

需要补充：

- [ ] Root completion 时 Home memory 是否保证 latest。
- [ ] Write-through 更新是否位于 store critical path。
- [ ] Hybrid 的选择规则和操作权重。
- [ ] Dirty data 写回或 ownership transfer 的触发事件。

#### HA-T07：2-bit 之外如何确定 dirty/latest data 所在位置

- [ ] `[未知]`。
- [ ] `[A]`：存在额外 exact owner/dirty 信息，请说明位于何处但无需给编码。
- [ ] `[B]`：由 unique-owner invariant 和节点内 HN 状态查询确定。
- [ ] `[C]`：向唯一远端节点发 probe，由远端判断是否持有 latest data。
- [ ] `[D]`：采用 write-through，Home memory 始终 latest。
- [ ] `[E]`：memory 与 peer 并行请求，并通过 version/状态选择 latest。
- [ ] `[F]`：其他机制。

需要补充：

- [ ] Remote presence 是否只能表示“可能存在副本”。
- [ ] Shared clean copy 与 dirty owner 如何区分。
- [ ] HN query/probe 是否进入 demand critical path。
- [ ] Probe 无数据、stale 或冲突时的 fallback。

### 2.6 旧权限撤销和完成确认

#### HA-T08：Invalidate/Recall 的可验证完成事件

- [ ] `[未知]`。
- [ ] `[A: Explicit Ack]`：远端 HN/cache 完成失效或降级后向 HA 返回显式 Ack。
- [ ] `[B: Direct Completion]`：远端完成后直接向 requester 返回可验证 completion。
- [ ] `[C: Implicit Fabric Completion]`：fabric/snoop completion 对 HA 或 requester 提供等价保证。
- [ ] `[D: Lease/Timeout]`：通过 lease 到期或 timeout 证明旧权限不可继续使用。
- [ ] `[E: No Completion Proof]`：没有可验证完成事件。

需要补充：

- [ ] Remote Read 时旧 owner `M/E -> S` 的完成点。
- [ ] Writer Acquire 时 remote sharer `S -> I` 的完成点。
- [ ] Ownership Handoff 时 old owner `M/E -> I` 的完成点。
- [ ] Dirty data、old release 和 new authority 是否绑定同一 transaction/version。
- [ ] 无显式 Ack 时，隐式 completion 的 ordering guarantee。

判定规则：`E` 不满足共同安全域，不进入理论下界；消息名称不同不影响，只需提供等价语义。

### 2.7 同址序列化、transient 和非 FIFO

#### HA-T09：同一 Cacheline 的 serialization authority

- [ ] `[未知]`。
- [ ] `[A]`：per-line transaction lock。
- [ ] `[B]`：version/epoch + commit validation。
- [ ] `[C]`：pending-install/ownership-transfer token。
- [ ] `[D]`：每行 serial queue。
- [ ] `[E]`：上述机制组合。
- [ ] `[F]`：无 guard。

需要补充：

- [ ] stable transaction identity 的字段和有效范围。
- [ ] stale/duplicate response 如何拒绝。
- [ ] Grant 在途时是否允许下一同址请求进入。
- [ ] 新 owner 尚未 install 时是否可能收到下一 Recall。
- [ ] Recall-before-Grant/Install 时 requester 是 buffer、NACK 还是 retry。
- [ ] Timeout/replay 后如何避免 ABA 和 double commit。
- [ ] `T_next` 对应 lock/token/queue 的哪个释放事件。

判定规则：`F` 与 eager commit/direct authority 组合时不构成合法安全下界。

#### HA-T10：2-bit stable code 和 transient state

- [ ] `[未知]`：2-bit 抽象码字尚未确认。
- [ ] `[A]`：两节点 presence vector，例如 `[P0,P1]`。
- [ ] `[B]`：四个稳定码字，例如 `I/N0-exclusive/N1-exclusive/Shared`。
- [ ] `[C]`：1-bit VI + 1-bit remote presence。
- [ ] `[D]`：其他抽象编码。

需要补充：

- [ ] 四个 stable code 的抽象含义。
- [ ] Dirty、latest owner 是否由 2-bit 之外的状态表达。
- [ ] Pending probe、invalidate、ownership transfer、install 状态存放位置。
- [ ] Transient entry 数量和资源不足行为。

### 2.8 ISA 内存序和 API 完成语义

#### HA-T11：各 API 的 counter stop 和 architected guarantee

| API/操作 | Counter stop 事件 | 是否等 `T_visible` | 是否等 `T_commit` | 是否等 `T_next` | Barrier scope/备注 |
|---|---|:---:|:---:|:---:|---|
| Ordinary load |  | [ ] | [ ] | [ ] |  |
| Ordinary store |  | [ ] | [ ] | [ ] | posted/retired/completed? |
| Store-release |  | [ ] | [ ] | [ ] |  |
| Load-acquire |  | [ ] | [ ] | [ ] |  |
| Arm DMB |  | [ ] | [ ] | [ ] | ISH/OSH/SY? |
| Arm DSB |  | [ ] | [ ] | [ ] | ISH/OSH/SY? |
| RISC-V FENCE |  | [ ] | [ ] | [ ] | predecessor/successor sets |
| Atomic RMW/aq/rl |  | [ ] | [ ] | [ ] | 若适用 |

- [ ] 确认目标 ISA 和 memory type/shareability domain。
- [ ] 确认 ordinary store accepted 不会被误作 completed store。
- [ ] 确认 Acquire/Release、DMB/DSB/FENCE 与 HA outstanding transaction 的映射。
- [ ] 同意用 Message Passing、same-line writers 和 independent-line reorder litmus 作为 correctness gate。
- [ ] 同意 architectural register/memory outcome 为 oracle，内部 trace 只作解释。

### 2.9 争用、资源压力和 retry

#### HA-T12：资源与 contention policy

- [ ] `[未知]`。
- [ ] `[A]`：credit/backpressure stall。
- [ ] `[B]`：NACK/retry。
- [ ] `[C]`：bounded queue。
- [ ] `[D]`：version conflict retry。
- [ ] `[E]`：组合策略。

需要补充：

- [ ] 每行和全局 queue/transient limit。
- [ ] Retry 是否复用 stable transaction ID。
- [ ] Retry latency 是否进入原 root operation。
- [ ] Idle/no-contention 与合同 contention profile 是否分开报告。
- [ ] Queue full、timeout 和 terminal failure 的处理。

### 2.10 Transport、重复和可靠性边界

#### HA-T13：Lossless transport 的具体边界

- [ ] 不考虑 packet/message drop。
- [ ] 允许 transport replay，但 no-fault baseline 中 replay 次数为 0。
- [ ] 允许 duplicate delivery，coherence 层必须去重。
- [ ] 网络不保证 FIFO。
- [ ] Link CRC/ECC/poison 不进入目标 3 no-fault baseline。
- [ ] 节点 crash/partition/reconfiguration 不在当前目标 3 范围。

需要补充：

- [ ] Duplicate response 是否可能到达 coherence endpoint。
- [ ] Timeout/replay 属于 link、transport 还是 coherence transaction 层。
- [ ] Non-FIFO 的 ordering domain 是全网、virtual channel 还是同 transaction。

### 2.11 共同 workload、权重和理论平均值

#### HA-T14：共同 operation taxonomy

| 类别 | 定义 | 纳入目标 3 | 权重来源 | 权重 |
|---|---|:---:|---|---:|
| `R_h` | Home memory latest Remote Read | [ ] |  |  |
| `R_o` | Remote owner latest Remote Read | [ ] |  |  |
| `W_s` | Shared-to-writer / remote invalidate | [ ] |  |  |
| `W_o` | Ownership handoff / dirty owner transfer | [ ] |  |  |
| `M` | Metadata query/probe/refill | [ ] |  |  |
| `C` | Contention/queue/retry | [ ] |  |  |

- [ ] 权重总和为 1。
- [ ] 只统计 root issue 时实际触发跨节点 coherence dependency 的操作。
- [ ] Local hit、Silent Upgrade 等不只在一侧混入分母。
- [ ] Retry 按原 root operation 计一次，不按消息数重复计样本。
- [ ] 地址、Home placement、并发度、warm-up、measurement window 和 seed 已冻结。

#### HA-T15：共同 trace/counter 字段

- [ ] `root_id`。
- [ ] `event_id/parent_event_id`。
- [ ] 脱敏 `transaction_id/version`。
- [ ] `root_issue/root_complete`。
- [ ] `peer_request_issue/peer_complete`。
- [ ] `old_permission_revoked`。
- [ ] `latest_data_selected/data_return`。
- [ ] `permission_grant/requester_install_complete`。
- [ ] `metadata_commit/next_conflict_release`。
- [ ] 单调 target counter 和 frequency。
- [ ] data source、abstract state before/after、placement。

如无法输出内部事件，至少提供 target-visible root counter 和能关闭 HA-T01 至 HA-T05 的 black-box
对照试验。

---

## 3. 向甲方评审小组确认的列表

本节不要求评审小组回答 HA 微架构问题，而是请其冻结合同解释、比较规则、证据门槛和最终判定方式。

### 3.1 合同范围与原始门槛

| ID | 待确认事项 | 评审确认 |
|---|---|---|
| RV-01 | HA 只参与目标 3，不参与目标 1 和目标 2 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-02 | 目标 3 原始门槛保持严格 `<` | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-03 | `<= + 结构性优势` 只有在双方书面变更合同时才可替代 `<` | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-04 | 比较对象为甲方实际 HA，不使用未经确认的泛化 HA-A/B/C 替代 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-05 | 未实现的 OurCC profile 不能作为当前实现结果 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |

### 3.2 共同安全域

| ID | 待确认事项 | 评审确认 |
|---|---|---|
| RV-06 | Requester 完成时必须拥有 latest data 和合法 authority | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-07 | Writer 完成前冲突旧权限必须已失效或被可验证机制禁止继续使用 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-08 | 允许无显式 Ack，但必须有等价、可验证的 completion/ordering | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-09 | Data arrival 不自动等同 permission/authority completion | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-10 | 缺少同址 serialization/late response 防护的 eager fast path 不作为合法下界 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-11 | Correctness、memory ordering 和 dirty-data integrity 是性能判定前置 gate | [ ] 同意 [ ] 不同意 [ ] 待讨论 |

### 3.3 计时起点和完成点

| ID | 待确认事项 | 评审确认 |
|---|---|---|
| RV-12 | 主起点为 Requester HN 发出首个跨节点 coherence request | [ ] 同意 [ ] 改为 CPU issue [ ] 待讨论 |
| RV-13 | Requester→HN 公共前缀双方抵消；若保留则双方同时计入 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-14 | 主指标优先采用 `T_visible` | [ ] 同意 [ ] 改用 `T_commit` [ ] 改用 `T_next` [ ] 待讨论 |
| RV-15 | 无论主指标为何，都附报 `T_visible/T_commit/T_next` | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-16 | Completed store 的 timer 覆盖目标平台约定的 DSB/FENCE 或等价完成语义 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-17 | Ordinary posted store 不得冒充 completed store | [ ] 同意 [ ] 不同意 [ ] 待讨论 |

### 3.4 理论模型和未知项处理

| ID | 待确认事项 | 评审确认 |
|---|---|---|
| RV-18 | 使用 `T=K*tau+P` 和 DAG 最长串行依赖链，不以消息总数直接代替 K | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-19 | `K_logical` 与 `K_crossnode` 分开报告 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-20 | 并行 data/permission 路径使用 `max()`，不把所有消息时延直接相加 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-21 | `[未知]` 参数保留全部合法分支和上下界 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-22 | 未知项不默认取对甲方或我方更有利的值 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-23 | 同 K 只能证明同阶；严格 `<` 仍需证明 `P_OurCC<P_HA` 或均值差严格为正 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-24 | 缺少 HA-T01 至 HA-T05 时，整体结论保持 `UNPROVEN` | [ ] 同意 [ ] 不同意 [ ] 待讨论 |

### 3.5 平均值、workload 和分母

| ID | 待确认事项 | 评审确认 |
|---|---|---|
| RV-25 | 共同 operation taxonomy 为 `R_h/R_o/W_s/W_o/M/C` 或双方书面修订版本 | [ ] 同意 [ ] 修订 [ ] 待讨论 |
| RV-26 | Operation weights 来自共同 workload、共同 trace 或书面理论分布 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-27 | 只统计实际触发跨节点 dependency 的 root operation | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-28 | Local hit/Silent Upgrade 不得只在一侧混入跨节点平均值分母 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-29 | Retry 按 root operation 统计，且其等待计入该 root latency | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-30 | Idle/no-contention 和合同 contention profile 分开报告 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |

### 3.6 测量、统计和证据等级

| ID | 待确认事项 | 评审确认 |
|---|---|---|
| RV-31 | 正式比较只使用相同 target/guest-visible root counter | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-32 | OurCC Outer trace、ReadResp latency 和 gem5 wall-clock 只作诊断 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-33 | 双方采用同输入、同 seed、同 placement 分类的 paired runs | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-34 | 至少 3 个 run 只作 smoke minimum，正式样本按预注册 CI 规则增加 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-35 | 报告 mean、P50、P95、P99、max、CV 和 paired delta | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-36 | 主判据为 `delta=T_mean_HA-T_mean_OurCC` 的 95% 单侧置信下界严格大于 0 | [ ] 同意 [ ] 修订统计规则 [ ] 待讨论 |
| RV-37 | 预注册最大轮数、停止规则、异常值处理和 `INCONCLUSIVE` 条件 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-38 | 不删除负结果、退化 case 或未通过场景 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |

### 3.7 Profile 和证据资格

| ID | 待确认事项 | 评审确认 |
|---|---|---|
| RV-39 | `OurCC-current-clear-ack` 是当前可进入正式比较的已实现 profile | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-40 | `OurCC-lossless-oneway` 在完成实现、形式验证和 E2E 前只作理论方案 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-41 | 当前两节点目标 3 不使用现有 C4 三角色 Direct-Forward 作为胜因 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-42 | TLA+ 小模型证明不自动等同完整 ISA memory-model 或任意规模证明 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-43 | 证据等级采用 E0-E5，理论假设不得冒充实测 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |

证据等级：

| 等级 | 含义 |
|---|---|
| E0 | 假设、口头输入、未确认参数 |
| E1 | 静态代码、接口或设计文档 |
| E2 | 形式模型及其明确边界 |
| E3 | 指定配置下的 E2E simulation |
| E4 | 单方 target/实机结果 |
| E5 | 双方可复现、共同口径结果 |

### 3.8 Memory-order correctness gate

| ID | 待确认事项 | 评审确认 |
|---|---|---|
| RV-44 | 单地址 coherence order 与多地址 ISA memory ordering 分开验证 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-45 | Message Passing release/acquire forbidden outcome 必须为 0 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-46 | Same-line competing writers 不得出现双 owner 或反向观察 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-47 | Independent-line 无 fence 的 allowed reorder 不作为 failure | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-48 | DMB/DSB/FENCE scope 和 completion mapping 必须书面冻结 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |
| RV-49 | Correctness gate 未通过时不进行目标 3 性能 PASS 判定 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |

### 3.9 最终结论等级和签字规则

| ID | 待确认事项 | 评审确认 |
|---|---|---|
| RV-50 | `STRICT PASS`：共同条件下严格满足 `<`，且 correctness gate 全部通过 | [ ] 同意 [ ] 修订 [ ] 待讨论 |
| RV-51 | `CONDITIONAL PASS`：仅指定分支或参数成立时满足 `<` | [ ] 同意 [ ] 修订 [ ] 待讨论 |
| RV-52 | `TIE`：理论或统计上相等，不满足原始严格 `<` | [ ] 同意 [ ] 修订 [ ] 待讨论 |
| RV-53 | `UNPROVEN`：未知参数或证据不足 | [ ] 同意 [ ] 修订 [ ] 待讨论 |
| RV-54 | `RISK/FAIL`：合法分支下 OurCC 不快于 HA，或 correctness gate 失败 | [ ] 同意 [ ] 修订 [ ] 待讨论 |
| RV-55 | 仅达 `CONDITIONAL PASS` 时，是否可验收必须由合同方书面决定 | [ ] 同意 [ ] 不同意 [ ] 待讨论 |

---

## 4. 双方需共同签字冻结的产物

| ID | 产物 | 甲方技术签字 | 甲方评审签字 | 我方签字 | 状态 |
|---|---|---|---|---|---|
| SG-01 | HA-K01 至 HA-K16 已知条件复核表 |  |  |  | [ ] |
| SG-02 | HA-T01 至 HA-T15 技术参数答复 |  |  |  | [ ] |
| SG-03 | Remote Read 合法 DAG 和 placement |  |  |  | [ ] |
| SG-04 | Shared-to-writer 合法 DAG 和 placement |  |  |  | [ ] |
| SG-05 | Ownership Handoff 合法 DAG 和 placement |  |  |  | [ ] |
| SG-06 | `T_visible/T_commit/T_next/ISA root` 定义 |  |  |  | [ ] |
| SG-07 | `K_logical/K_crossnode/P` 参数账本 |  |  |  | [ ] |
| SG-08 | Operation taxonomy、weights 和分母 |  |  |  | [ ] |
| SG-09 | Workload、seed、placement、并发度和计时窗 |  |  |  | [ ] |
| SG-10 | Memory-order litmus、scope 和 allowed/forbidden outcome |  |  |  | [ ] |
| SG-11 | Paired-run 统计计划和 CI 判据 |  |  |  | [ ] |
| SG-12 | 原始数据、manifest、版本和证据等级索引 |  |  |  | [ ] |
| SG-13 | 最终结论矩阵和限制清单 |  |  |  | [ ] |

---

## 5. 会议建议顺序

1. 先复核 HA-K01 至 HA-K16，只纠正事实，不讨论输赢。
2. 再关闭最短决策集 HA-T01 至 HA-T05。
3. 关闭 HA-T06 至 HA-T13，确定安全分支和 service 上下界。
4. 由评审组冻结 RV-01 至 RV-30，确定合同、完成点和平均值分母。
5. 冻结 RV-31 至 RV-49，确定测量、统计和 correctness gate。
6. 双方签字 SG-01 至 SG-11 后再运行正式 paired trials。
7. 数据冻结后签字 SG-12、SG-13，并按 RV-50 至 RV-55 给出最终结论。

---

## 6. 名词解释表

| 名词 | 英文全称/原词 | 解释 | 与本 Checklist 的关系 |
|---|---|---|---|
| OurCC / CC-EP | Our Cache Coherence / Cache-Coherence Endpoint | 我方跨节点缓存一致性方案 | 目标 3 的我方比较对象 |
| HA | Hardware Agent / Home Agent | 甲方负责跨节点目录、路由或一致性协调的硬件代理 | 目标 3 的甲方比较对象 |
| CC | Cache Coherence | 保证多个缓存副本对同一地址保持合法一致性的机制 | 合同“跨节点 CC 同步”的核心 |
| Cacheline | Cache Line | 缓存一致性追踪的基本数据粒度，当前通常为 64B | 所有 owner、sharer、commit 和 serialization 按 line 定义 |
| VI | Valid/Invalid | 只区分有效和无效的两态协议抽象 | 甲方节点级目录已知条件 |
| MESI | Modified/Exclusive/Shared/Invalid | 能表达脏独占、干净独占、共享和无效的四态协议 | OurCC 全局状态的主要抽象 |
| Metadata | Directory Metadata | 每条 Cacheline 的状态、presence、owner、sharer 或 transient 信息 | HA-T02、HA-T07、HA-T10 |
| Presence bit | Remote Presence Bit | 表示某节点是否可能持有该 Cacheline 副本的位 | 不自动等于 dirty/latest owner |
| Stable state | Stable Coherence State | 当前没有进行中转移时的稳定目录状态 | HA-T10 要求确认四个码字 |
| Transient state | Transient Coherence State | Probe、invalidate、transfer、install 等进行中的临时状态 | 决定同址并发和 eager commit 是否安全 |
| Requester / R | Requester | 发起 load、store 或权限请求的节点/代理 | 主计时起点和 `T_visible` 的主体 |
| Home / H | Home / Serialization Point | 某 Cacheline 的目录归属和全局序列化点 | 决定 commit、next 和消息 DAG |
| Peer | Remote Owner or Sharer | 远端持有数据或副本的节点 | 本文不预设其可直接授权 requester |
| Owner / O | Owner | 持有 Exclusive/Modified 等独占权限的节点 | Remote owner read 和 ownership handoff 的旧持有者 |
| Sharer / S | Sharer | 持有 Shared 等共享权限的节点 | Writer Acquire 前需要失效的节点 |
| Dirty owner | Dirty Data Owner | 持有唯一最新、尚未写回数据的 owner | HA-T07 需要确认如何定位 |
| Latest data | Latest Coherent Data | 按 coherence order 最新且可合法返回的数据版本 | Requester 完成前必须获得 |
| Authority | Grant Authority / Serialization Authority | 有权授予 Shared/Exclusive/Modified 权限并建立全局顺序的权威 | 区分 direct data 与 direct completion |
| Grant | Permission Grant | 向 requester 授予所需数据和/或权限的协议事件 | Data 到达不一定等于 Grant authority 到达 |
| Central Return | Central-return Response | Peer 数据或完成先回 HA，再由 HA 返回 requester | HA-T01 的一种路由分支 |
| Direct Data Only | Direct Data Response Only | Peer 直接向 requester 发数据，但权限仍由 HA 提供 | 不自动减少 permission critical path |
| Direct Data + Authority | Direct Response with Delegated Authority | Peer 基于 HA token 同时返回数据和可验证权限 | 可能形成更短路径，但需强安全条件 |
| Peer Direct | Peer-to-Peer Direct Response | 远端 Peer 绕过 HA 直接响应 requester 的统称 | 不是甲方已知能力，只是 HA-T01 的候选分支 |
| Token | Transaction/Grant Token | 绑定 PA、requester、transaction 和 version 的授权凭证 | 支持 delegated authority 和 stale rejection |
| Recall | Recall / Downgrade / Revoke | 回收或降级旧 owner 权限，并可取得 latest data | Remote Read 和 Ownership Handoff 的前置动作 |
| Invalidate | Invalidation | 使旧 sharer/owner 副本失效 | Writer Acquire 的前置动作 |
| Ack | Acknowledgement | 对 completion 的显式确认消息 | 无 Ack 消息名不等于无 completion dependency |
| Implicit completion | Implicit Fabric/Snoop Completion | 无显式 Ack，但 fabric 或 snoop 语义提供等价完成保证 | 必须可验证才可进入合法下界 |
| Completion | Protocol Completion | 某个动作已满足协议规定后置条件的事件 | HA-T03、HA-T08、HA-T11 |
| Install | Requester Install Completion | Requester HN/cache 已实际安装数据和权限 | 不应与协议代理收到 Grant 自动等同 |
| Commit | Metadata Commit | Home/HA authoritative metadata 原子提交新状态 | 定义 `T_commit` |
| Split commit | Split Metadata Commit | 旧状态清除和新状态安装在不同阶段完成 | 需证明中间态安全 |
| Eager commit | Eager Metadata Commit | Requester install 前提前提交新 owner/状态 | 必须有 pending-install guard |
| Serialization | Per-line Serialization | 同一 Cacheline 的冲突事务被排序 | 防止双 owner、ABA 和 late response 覆盖 |
| Per-line lock | Per-cacheline Transaction Lock | 用锁阻止同址冲突事务并发推进 | HA-T09 候选机制 |
| Epoch / Version | Transaction Epoch / Version | 区分同一 Cacheline 不同代事务的单调标识 | 非 FIFO、stale、duplicate 处理 |
| Transaction ID | Transaction Identifier | 标识一笔协议事务的稳定 ID | Trace 关联和去重所需 |
| ABA | ABA Problem | 状态 A→B→A 后旧 response 被误认作当前事务 | 需要 version/epoch 防护 |
| Stale response | Stale/Late Response | 属于旧事务但延迟到达的 response | 不得改变新 metadata 或释放新 waiter |
| Duplicate | Duplicate Delivery | 同一消息或 response 被重复投递 | 需要幂等或去重机制 |
| FIFO | First-In-First-Out | 按发送顺序到达 | 甲方网络已知不保证全局 FIFO |
| Non-FIFO | Non-First-In-First-Out | 消息可能乱序到达 | 需要同址因果保护和 stable identity |
| Lossless transport | Lossless Transport | 比较域内不考虑消息丢失 | 不等于 FIFO，也不等于没有 retry/duplicate |
| Write-through / WT | Write-through | 写入时更新 Home memory | 提高 memory-latest 路径比例，但写成本可能更高 |
| Write-back / WB | Write-back | Dirty data 可保留在 cache，延后写回 | 需要定位 dirty/latest owner |
| Hybrid write policy | Hybrid Write Policy | 不同地址/状态/操作采用不同写策略 | 需要分类权重 |
| Probe | Coherence Probe | 向节点查询是否持有副本、状态或 latest data | 可能进入 HA critical path |
| HN / HN-F | Home Node / Fully Coherent Home Node | 节点内 CHI 的 Home/目录和事务管理组件 | 可提供节点内 owner/dirty 状态 |
| CHI | Arm Coherent Hub Interface | Arm 缓存一致性互连协议 | 节点内完成和 snoop 语义来源 |
| IODie | I/O Die | 可能承载集中 HA 的独立 die | HA-T04 placement 分支 |
| Fabric | Coherent Interconnect Fabric | 节点、HA、IODie 之间的互连 | `tau` 和 `K_crossnode` 的物理载体 |
| DAG | Directed Acyclic Graph | 描述消息和完成事件因果依赖的有向无环图 | 用最长依赖链计算理论时延 |
| Critical path | Critical Path | DAG 中决定完成时间的最长串行依赖链 | 不能用消息总数替代 |
| Leg | One-way Dependency Leg | 关键路径上的一个单向依赖段 | `K` 的计数单位 |
| `K_logical` | Logical Critical-path Legs | 逻辑角色间的串行依赖段数 | 不一定等于物理跨节点次数 |
| `K_crossnode` | Physical Cross-node Traversals | 真正跨节点/fabric 边界的串行传输次数 | Placement 冻结后计算 |
| `tau` / `τ` | Normalized One-way Fabric Latency | 一次归一化单向 fabric leg 时延 | 理论模型 `T=K*tau+P` 的网络项 |
| `P` | Local Processing Term | Directory、peer、data、install、commit、queue 等本地处理项 | 同 K 时决定严格快慢 |
| `P_queue` | Queueing Delay | 资源争用、backpressure、NACK/retry 引起的等待 | 合同负载下需单列 |
| `T_visible` | Requester-visible Completion Time | Requester 同时拥有 latest data 和安全 authority 的时间 | 推荐的目标 3 主完成点候选 |
| `T_commit` | Metadata Commit Time | Home/HA authoritative metadata 完成提交的时间 | 附报完成点 |
| `T_next` | Next-conflict Release Time | 下一同址冲突事务可安全继续的时间 | 揭示后台 commit/lock 成本 |
| ISA root | Architected Root Completion | CPU 架构可见的 load/store/barrier 完成点 | 不能自动与内部 Grant/Clear/commit 等同 |
| Root operation | Root Operation | Workload 中一次 load、completed store 或明确 batch | 正式统计样本单位 |
| Root counter | Target-visible Root Counter | 从 root issue 到 root complete 的目标侧单调计时器 | 跨实现正式比较数据 |
| Posted store | Posted/Accepted Store | Store 被 buffer/endpoint 接受但尚未全局完成 | 不得冒充 completed store |
| Completed store | Completed Store | 按平台合同已达到指定完成保证的 store | 通常需 DSB/FENCE 或等价 API |
| Acquire/Release | Acquire/Release Ordering | 建立跨地址 publication 顺序的 ISA 内存序原语 | 与单地址 coherence order 不同层次 |
| DMB | Data Memory Barrier | Arm 数据内存屏障，主要约束内存访问顺序 | Scope 和 completion mapping 需冻结 |
| DSB | Data Synchronization Barrier | Arm 数据同步屏障，具有更强完成要求 | Completed-store timer 常以其结束 |
| FENCE | RISC-V FENCE | 按 predecessor/successor sets 约束内存访问顺序 | RISC-V 完成语义需单列 |
| Weak ordering | Weak Memory Ordering | 允许无依赖、无 barrier 的不同地址访问乱序 | 不允许破坏单地址 coherence |
| Memory-order litmus | Memory Ordering Litmus Test | 检查 allowed/forbidden outcome 的小型并发测试 | 性能判定前 correctness gate |
| Message Passing | MP Litmus | 验证 release/acquire publication 的经典测试 | Forbidden outcome 必须为 0 |
| Operation taxonomy | Operation Classification | 将跨节点事务分为 `R_h/R_o/W_s/W_o/M/C` | 定义理论平均值分母 |
| `R_h` | Remote Read, Home Memory Latest | Home memory 已持有 latest data 的跨节点读 | 可能具有最短 Home path |
| `R_o` | Remote Read, Owner Latest | Latest data 位于远端 owner 的跨节点读 | 需要 Recall/probe/data transfer |
| `W_s` | Shared-to-Writer | 从 shared 状态获取写权限 | 需要 remote invalidation |
| `W_o` | Ownership Handoff | 写 ownership 从旧 owner 转给新 owner | 需要 old release 和 latest dirty data |
| `M` | Metadata Service Class | Metadata query、probe 或 refill 类别 | 影响理论平均值和 P |
| `C` | Contention/Retry Class | Queue、NACK、retry 和资源压力类别 | 影响平均值与尾延迟 |
| Operation weight | Workload Weight | 每类 root operation 在平均值中的比例 | 权重未冻结就没有唯一理论均值 |
| Paired runs | Paired Experimental Runs | HA 与 OurCC 使用相同输入/seed 的成对试验 | 降低 workload 差异影响 |
| Mean/P50/P95/P99/Max | Statistical Summaries | 均值、中位数、高分位和最大值 | 正式结果必须共同报告 |
| CV | Coefficient of Variation | 标准差与均值之比 | 判断多轮稳定性 |
| CI | Confidence Interval | 对均值差的不确定性区间 | 建议用 95% 单侧 CI 判严格 `<` |
| Paired delta | Paired Latency Difference | 每对样本的 `T_HA-T_OurCC` | 主判据的基础数据 |
| E0-E5 | Evidence Levels | 从假设到双方可复现实测的证据分级 | 防止理论假设冒充实测 |
| `STRICT PASS` | Strict Pass | 所有共同条件下严格满足合同 `<` | 目标 3 的完整通过等级 |
| `CONDITIONAL PASS` | Conditional Pass | 仅特定 HA 分支或参数范围下满足 `<` | 是否验收需合同方书面决定 |
| `TIE` | Tie | 理论或统计上相等 | 不满足原始严格 `<` |
| `UNPROVEN` | Unproven | 参数或证据不足，无法判定 | 当前目标 3 总体状态 |
| `RISK/FAIL` | Risk or Fail | 合法分支下不领先，或 correctness gate 失败 | 必须披露并处理 |
| `INCONCLUSIVE` | Inconclusive | 测试、模型、样本或 scope 不足以判定 | 不得被记为 PASS 或 FAIL |

---

## 7. 参考材料

- 系统化对标方案：`docs/delivery/ourcc_vs_customer_ha_target3_benchmark_and_delivery_20260804_zh.md`
- 甲方 15 题研究版确认单：`docs/research/customer_ha_questions_20260806_zh.md`
- 三类操作 DAG：`docs/research/ha_ourcc_operation_dags_20260806.md`
- 目标 3 一页结论：`docs/research/target3_onepage_summary_20260806_zh.md`
- 外部研究主报告：`docs/research/ourcc_vs_customer_ha_external_research_report_20260806_zh.md`
- ARM/RISC-V Litmus 计划：`docs/research/arm_riscv_coherence_litmus_plan_20260806_zh.md`
- 验收总 TODO：`docs/delivery/acceptance_metrics_deliverables_todo_20260807_zh.md`
