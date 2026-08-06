# ChatGPT Work 研究任务书：CC-EP 与甲方 HA 验收对标

## 使用方法

请将本文档全文直接提交给 ChatGPT Work。本文已经包含完成任务所需的项目背景、
内部事实、合同指标、研究问题、约束和输出格式。除非为了确认甲方私有参数，否则
不要先向用户提问；先完成公开资料调研和条件化分析，再把无法从公开资料确定的事项
集中列为“待甲方确认问题”。

---

## 给 ChatGPT Work 的任务

你是一名负责计算机体系结构、目录式缓存一致性、ARM CHI、跨节点 coherent fabric、
可靠传输、弱内存序和系统验收的高级研究员。请基于本任务书提供的项目事实，并结合
公开规范、论文、厂商技术资料和可信工程资料，撰写一份可用于合同评审的中文研究报告。

你的任务不是证明我方方案一定优于甲方 HA，也不是为已有结论寻找单方面支持材料。
你的任务是建立公平、可审计、可复现的比较模型，识别未知参数，给出条件化结论，
明确哪些主张可以成立、哪些仍未证明、哪些需要甲方确认或进一步实验。

研究截止日期以你执行任务时能够访问的最新公开资料为准。报告中注明检索日期。

## 1. 最终研究目标

请重点研究和回答合同目标 3：

```text
OurCC/CC-EP 跨节点 Cache Coherence 同步平均时延
是否严格小于甲方 HA 实现的理论平均时延？
```

原始合同使用严格小于 `<`。如果只能证明不劣于 `<=`、条件成立或结构性优势，必须
明确写成 `TIE`、`CONDITIONAL PASS` 或 `UNPROVEN`，不能擅自把原始门槛改写成 PASS。

同时请评估：

1. 当前比较边界是否公平。
2. 甲方 HA 哪些参数会决定结论方向。
3. 两节点 VI/2-bit 目录能够表达哪些状态，不能直接表达哪些状态。
4. direct data、Grant authority、metadata commit 和 requester completion 应如何区分。
5. ARM/RISC-V 弱内存序、acquire/release、DMB/DSB/fence 对完成点有何要求。
6. lossless coherent fabric 是否仍需要 replay、retry、duplicate suppression 或协议级 timeout。
7. 真正 16-node coherent switch 的可行架构、工作量和验收方式。
8. 我方现有目标 1、目标 2 的测量方法是否合理，哪些地方需要加强统计或口径。

## 2. 合同目标

### 2.1 目标 1

在 512 KiB 长期片上状态预算下：

- spill 方案的等效 Cacheline 追踪容量至少达到 naive baseline 的 150%。
- 即容量提升至少 50%。
- spill-noopt 相对 naive 的压力后附加同步成本小于 50 cycles。
- 需要按 Cacheline 对 ResidentDir 和 backstore metadata 去重，禁止直接相加。

历史内部结果：

- naive：65,536 lines。
- spill-noopt：102,656 lines。
- ratio：156.64%。
- 压力后内部 Outer mean 增量：6.03 ns，即 12.06 cycles @ 2 GHz。

这些结果完成于后续协议修复之前，因此只能作为历史证据，不是最终冻结代码 PASS。

### 2.2 目标 2

- baseline：`naive + no latency optimization`。
- optimized：`spill + latency optimization`。
- 适用场景：naive guest-visible mean `>=500 ns`。
- 对适用 case 的时延降低百分比做 case-level 等权平均。
- 平均降低至少 10%。
- 正式计时必须使用 guest/target-visible counter，不能用内部协议 trace 替代。

历史内部结果：

- TC135：降低 90.63%。
- TC136：降低 87.88%。
- TC137：降低 21.54%。
- TC138：退化 12.12%。
- TC139：降低 90.77%。
- TC217：降低 47.24%。
- 历史等权平均：降低 54.32%。

历史报告曾使用严格 `>500 ns`，而合同验收 TODO 要求 `>=500 ns`；最终必须统一。
多数结果为单轮或缺少统一 CV，因此仍需当前代码多轮复验。

### 2.3 目标 3

原始门槛：

```text
OurCC 跨节点 CC 同步平均时延 < 甲方 HA 理论平均时延
```

当前内部结论为 `UNPROVEN`，原因是甲方 HA 的 write policy、peer authority、
invalidate completion、metadata commit、requester completion、dirty owner 定位和
物理拓扑尚未全部明确。

## 3. OurCC/CC-EP 当前实现事实

以下内容是项目内部实现事实。除非你发现内部描述自相矛盾，否则无需通过外部文献证明；
外部资料用于评价其合理性和与业界方案的比较。

### 3.1 架构

- 每个节点内部是 ARM CHI coherence domain。
- EP-RNF、EP-SNF 和 UBAdapter 位于 gem5 CHI 侧。
- UBCC 位于独立 native UBIO 进程，承担全局 Home Agent/目录仲裁。
- 全局目录从 HN-F 中移出，主要由 Bloom Filter、ResidentDir 和 DRAM backstore metadata 组成。
- UBCC 不占 HN-F TBE，这是我方主张的结构性隔离优势之一。
- NetworkSim 负责节点/plane 间消息路由。

### 3.2 核心协议状态

- UBCC 按 PA 维护 owner、sharer、epoch、reqId、outstanding 和 transient state。
- epoch 必须单调。
- 同一逻辑事务不得 double commit。
- duplicate request/response 必须幂等处理。
- retry 应保持 stable reqId，除非启动新的逻辑事务。

### 3.3 当前 Clear commit

当前已实现 profile 为 `OurCC-current-clear-ack`：

1. requester 向 Home 请求并取得 Grant。
2. requester 在本地安装数据/权限。
3. requester 发送 ClearReq。
4. Home 使用 PA、requester、epoch、reqId 精确匹配事务。
5. Home commit 目录状态。
6. Home 退役对应 waiter。
7. Home 安装 tombstone，支持 duplicate Clear 幂等 replay。
8. Home 删除 outstanding 并释放 pending same-line request。
9. requester 当前等待 ClearResp accepted。

因此 Clear 不是普通内存屏障，也不是可随意删除的调试确认。它承担两阶段提交的
commit/retirement 语义。

### 3.4 Proposed profile

`OurCC-lossless-oneway` 只是拟议方案，尚未实现：

- requester 本地安装后发送 one-way Clear。
- requester 不等待 ClearResp。
- Home 收到 Clear 后 commit。

报告必须把该 profile 标为 `PROPOSED/UNIMPLEMENTED`。可以分析理论可行性和风险，
不能把它当成当前代码性能结果。

### 3.5 C4 Direct-Forward

- 当前 C4 主要是 direct data forwarding，不等于正式 Grant authority forwarding。
- permission/commit 关键路径可能仍由 Home 控制。
- 当前触发条件要求 requester、owner、home 是三个不同节点。
- 合同目标 3 是 2 节点场景，因此现有 C4 三角色路径不可达，不能作为 2 节点目标 3 的主胜因。

### 3.6 Fault/reliability

当前 fault injector 支持：

- Drop。
- Duplicate。
- Delay。
- Reorder。

当前 verifier 能精确检查：

- `[UBFAULT-TRIGGER]` 的 rule、action 和命中次数。
- `[UBFAULT-DELIVER]` 的 buffered delivery 次数。
- unexpected trigger/delivery。
- workload 数据 oracle。

已有 bounded qualification 覆盖：

- ClearReq。
- UpgradeReq。
- InvalidateAck。
- RecallResp。
- UpgradeResp。
- UpgradeAckNotify。

历史汇总中 TC148-TC159 合计 184 个真实 fault hits。它证明的是指定消息和指定
bounded single-fault 场景，不证明任意消息、持续丢包、任意组合故障或节点故障。

### 3.7 当前不支持或尚未证明的范围

- 节点 crash 后继续提供 coherence 服务。
- 永久网络 partition。
- Byzantine fault。
- 任意 payload/header corruption。
- 所有消息类型的持续丢包恢复。
- 完整 Q1-Q7 repeated/composed/burst/topology/exhaustion qualification。
- 完整 ARM/RISC-V memory model 证明。
- 真正 16-node Switch 仿真。

## 4. 甲方 HA 已知条件

当前已知条件：

| ID | 条件 |
|---|---|
| HK-01 | 2 节点 |
| HK-02 | 全局缓存地址空间不超过 128 MiB |
| HK-03 | 节点级 VI 协议 |
| HK-04 | 每 Cacheline 约 2-bit 节点级 metadata |
| HK-05 | 未知是否有额外节点级 dirty-owner metadata |
| HK-06 | 节点内详细状态可能由现有 HN/CHI 维护 |
| HK-07 | metadata 位于 HA 或 IODie，当前不区分其明显时延差异 |
| HK-08 | metadata lookup 近似零附加时延 |
| HK-09 | requester 到本地 HN 的路径与 OurCC 一致 |
| HK-10 | Remote Read 从 valid/latest data 所在位置取数 |
| HK-11 | 网络不考虑丢包，按 lossless baseline |
| HK-12 | 网络不保证 FIFO |
| HK-13 | 不同地址允许按 ARM/RISC-V 弱序乱序完成 |
| HK-14 | HA 与 OurCC 同频 |

## 5. 必须研究的甲方 HA 未知参数

请对每项给出：

- 公开架构中常见选择。
- 每种选择的协议和时延影响。
- 需要甲方确认的最小问题。
- 如果甲方不回答，可使用的保守上下界。
- 对目标 3 结论的敏感度。

| ID | 未知参数 |
|---|---|
| HU-01 | write-through、write-back 或 hybrid |
| HU-02 | peer response 是 central-return 还是 direct |
| HU-03 | peer direct 是否携带完整、可验证的 Grant authority |
| HU-04 | invalidate completion 是 explicit Ack、peer direct completion 还是 implicit fabric completion |
| HU-05 | metadata commit 在 Grant 前、peer completion 后、requester install 后或其他阶段 |
| HU-06 | requester root completion 是否等待 metadata/global commit |
| HU-07 | dirty/latest data 如何定位 |
| HU-08 | same-line serialization 使用 lock、version、pending-install token 还是其他机制 |
| HU-09 | HA/IODie 与两个节点的物理 placement |
| HU-10 | HA local directory/service/queue 时延 |
| HU-11 | store completion、fence、DMB/DSB 的 API 语义 |
| HU-12 | contention、NACK、retry、queue 和 backpressure 策略 |

## 6. 统一比较边界

### 6.1 相同安全功能域

任何被纳入目标 3 的 root operation，在完成点至少满足：

1. requester 获得正确且最新的数据。
2. requester 获得所需的 Shared、Exclusive、Modified 或等价权限。
3. 冲突旧权限已真正失效、降级，或被硬件机制保证不能继续使用。
4. 不存在两个节点同时合法持有冲突写权限。
5. dirty/latest data 不丢失。
6. 同一 Cacheline 的下一冲突事务有明确 serialization point。
7. 弱内存序不能破坏单地址 coherence ordering。
8. 如果 workload 使用 completed store、fence、DMB/DSB，posted write 不能冒充完成。

不满足上述条件的“更短路径”不得作为合法 HA 理论下界。

### 6.2 三个完成点

必须分别报告：

- `T_visible`：requester 安全获得数据或权限。
- `T_commit`：Home/HA metadata 正式提交。
- `T_next`：下一同址冲突事务可以安全继续。

不得只选择对某一方最有利的完成点。

### 6.3 时延模型

对操作类型 `o` 和完成点 `x` 使用：

```text
T(o,x) = K(o,x) * tau + P(o,x)
```

其中：

- `K_logical`：最长串行依赖链上的逻辑协议段。
- `K_crossnode`：真实物理跨节点或跨 die traversal。
- `tau`：归一化单向 fabric leg 时延。
- `P`：目录、peer、data、install、commit、queue 等本地项。

进一步分解：

```text
P = P_dir + P_peer + P_data + P_install + P_commit + P_queue
```

必须区分：

- message count。
- fanout count。
- logical dependency legs。
- physical cross-node traversals。

2 节点中 requester、home、owner 三个逻辑角色至少有两个物理共址，因此四个逻辑箭头
不一定等于四次物理跨节点传输。

### 6.4 并行路径

如果 peer 只 direct-forward data，而 permission 仍由 Home 返回，则：

```text
T_visible = prefix + max(peer_to_requester_data,
                         peer_to_home_completion + home_to_requester_grant) + P
```

不能因为数据先到就认为 root operation 已安全完成。

### 6.5 加权平均

建议至少分为：

- `R_h`：Home memory 是 latest 的 Remote Read。
- `R_o`：latest data 在 remote owner 的 Remote Read。
- `W_s`：shared-to-writer，需要 remote invalidate。
- `W_o`：ownership handoff，需要旧 owner 释放和数据转移。
- `M`：metadata refill/probe。
- `C`：contention/retry。

平均值：

```text
T_mean = sum(weight_i * T_i)
```

权重必须来自双方同意的 workload、可复现 trace 或书面冻结的理论分布。不得只在
OurCC 一侧混入 local hit/silent upgrade 来稀释“跨节点同步平均值”。

### 6.6 Break-even

OurCC 严格快于 HA 的条件：

```text
(K_HA - K_OurCC) * tau > P_OurCC - P_HA
```

请对以下情况分别判断：

- `K_OurCC < K_HA`。
- `K_OurCC = K_HA`。
- `K_OurCC > K_HA`。

同 K 只能证明同阶，不能自动满足严格 `<`；必须证明 `P_OurCC < P_HA`。

## 7. 必须完成的研究任务

### 7.1 公开架构综述

调研并比较以下公开资料中与本问题相关的机制：

- ARM AMBA CHI。
- CXL.cache/CXL fabric 的公开一致性和可靠性资料。
- CCIX 的公开资料。
- 公开可引用的 Intel UPI、AMD Infinity Fabric 或类似 coherent interconnect 资料。
- 学术界目录式 coherence、limited-pointer directory、coarse vector directory、
  direct cache-to-cache transfer 和 scalable coherent fabric 论文。

不要因为某协议知名就默认其机制与甲方 HA 相同。每个类比都要写适用条件和局限。

### 7.2 两节点 VI/2-bit metadata 能力分析

回答：

1. 两节点时 2-bit metadata 最合理的编码有哪些。
2. 它能否唯一表达 remote presence、local presence、invalid。
3. 在 write-back 下能否仅靠 2-bit metadata 确定 dirty/latest owner。
4. 如果不能，公开架构通常依赖 HN query、唯一 owner invariant、probe 还是 write-through。
5. shared clean copies 与 dirty owner 如何区分。
6. 哪些状态必须存在于节点内 HN/cache，而不一定存入节点级 metadata。

### 7.3 三类核心操作 DAG

分别为 OurCC 和 HA 绘制：

1. Remote Read。
2. Shared-to-Writer。
3. Ownership Handoff。

每类至少包含：

- Home memory latest。
- remote owner latest。
- central-return。
- direct-data-only。
- direct-data+authority。
- explicit invalidate Ack。
- implicit fabric completion。
- requester 等待 commit与不等待 commit。

对每条 DAG 标明：

- logical actor。
- physical placement。
- dependency edge。
- 可并行 edge。
- serialization point。
- `T_visible/T_commit/T_next`。
- `K_logical/K_crossnode`。
- 尚未知的 `P` 项。

请使用 Mermaid 或清晰的 ASCII 图，并同时提供表格形式，避免只给图片。

### 7.4 Completion 和 authority

重点研究：

- data response 是否可以携带 coherence authority。
- authority 由 peer、Home、version/token 还是 fabric completion 提供。
- requester install 前 metadata 指向新 owner 是否安全。
- pending-install token、per-line lock、version/epoch 在公开协议中的作用。
- non-FIFO fabric 上 Recall-before-Grant 或 next-conflict 风险。
- duplicate completion 如何避免 double commit。

### 7.5 ARM/RISC-V memory-order

研究并给出规范依据：

- 单地址 coherence ordering 与多地址 memory ordering 的区别。
- acquire/release publication 的最低要求。
- ARM DMB 与 DSB 的完成语义差异。
- RISC-V fence 的相关语义。
- store buffer accepted、posted write 和 architecturally completed store 的区别。
- coherent interconnect completion 是否自动满足 CPU barrier completion。

设计最小可执行 litmus 集：

- Message Passing。
- Store-Release/Load-Acquire。
- Store + DMB/DSB + Remote Load。
- Same-line Competing Writers。
- Independent-line Allowed Reordering。

每个 litmus 给出：

- 初始状态。
- 两个或多个线程的伪代码。
- allowed outcome。
- forbidden outcome。
- 需要的 barrier。
- 建议使用的工具，如 herd7、litmus7 或项目 E2E。

### 7.6 Lossless transport 和可靠性

回答：

1. lossless fabric 通常由哪些机制实现：credit、link replay、CRC、retry、poison、
   duplicate suppression、end-to-end timeout。
2. 链路层 replay 与 coherence transaction retry 如何分层。
3. 协议层是否仍需 stable transaction ID 和 idempotence。
4. HA 理论时延是否应包含 retry 成本。
5. OurCC 的 drop/dup/delay/reorder robustness 应如何作为能力差异呈现，而不污染公平的
   lossless HA baseline。
6. 哪些故障应标为 transport qualification，哪些应标为 node/RAS out-of-scope。

### 7.7 16-node coherent switch

提出至少两种可行架构：

- 集中式或分层 coherent switch。
- 分布式 home slice + switch routing。

比较：

- home placement。
- route selection。
- hop count。
- multicast/fanout。
- ordering domain。
- congestion/backpressure。
- switch failure 和 partition。
- metadata scaling。
- correctness 和性能验证工作量。

给出从现有 8N full-mesh/8N2S plane 模型升级到真正 16 nodes Switch 所需的最小工作包：

- 配置。
- NetworkSim changes。
- routing table。
- workload。
- correctness gate。
- liveness gate。
- performance gate。
- fault gate。
- 预计风险。

### 7.8 目标 1、目标 2 方法学审阅

请评价：

- 512 KiB 是否应使用 KiB 而不是 KB，Bloom/ResidentDir/GroupIndex 如何计入。
- 等效追踪容量如何避免 ResidentDir/backstore 重复统计。
- Outer mean 是否适合作为目标 1 的“附加同步时延”，是否还应提供 guest-visible 结果。
- 目标 2 使用 `>=500 ns` 筛选是否可能形成 selection bias。
- case-level 等权平均是否合理，是否应同时报告 operation-weighted 结果。
- 至少 3 轮是否足够；建议的置信区间、bootstrap 或非参数统计方法。
- 主机并发、PDES、日志 I/O 对 guest counter 和 wall-clock 的不同影响。

不要重新计算内部数字，除非任务书给出的数据足以计算。重点评价方法和提出改进。

## 8. 检索与资料要求

### 8.1 来源优先级

优先使用：

1. 正式架构规范和标准组织资料。
2. 同行评审论文。
3. 厂商官方技术白皮书、公开演讲和专利。
4. 大学课程或权威研究机构资料。
5. 工程博客只用于补充，不作为关键结论唯一依据。

### 8.2 推荐检索关键词

```text
ARM CHI home node completion grant data response
ARM CHI direct data transfer permission response
directory cache coherence pending owner install transient state
directory coherence invalidate acknowledgement completion
non FIFO interconnect coherence ordering
two node valid invalid directory presence bit
two bit directory cache coherence dirty owner
limited pointer directory two nodes
CXL.cache host managed device memory coherence completion
CXL link retry replay CRC poison
CCIX coherent interconnect retry ordering
lossless coherent fabric transaction retry
ARMv8 DMB DSB cache coherence completion
ARM acquire release message passing litmus
RISC-V fence coherence ordering
16 node coherent switch directory protocol
scalable cache coherent fabric switch topology
```

可以扩展关键词，但报告中要列出实际检索式。

### 8.3 引用要求

每个关键外部事实必须给出引用。引用至少包含：

- 标题。
- 作者或发布组织。
- 年份/版本。
- URL、DOI 或规范编号。
- 具体章节、页码或表格编号，若可获得。
- 访问日期。

禁止：

- 编造论文、规范编号、页码或 URL。
- 使用无法访问的来源却声称已核验全文。
- 用搜索摘要代替原文。
- 将二手文章中的推测写成规范要求。

如果只能访问摘要，明确标记 `ABSTRACT ONLY`。如果来源相互冲突，列出冲突，不要
自行选择对我方最有利的一项。

### 8.4 来源评价表

报告附录中为每条关键资料填写：

| 字段 | 内容 |
|---|---|
| Citation | 完整引用 |
| URL/DOI | 稳定链接 |
| Type | 规范/论文/白皮书/专利/博客 |
| Authority | 高/中/低 |
| Relevant claim | 支撑的事实 |
| Exact location | 页码/章节 |
| Assumptions | 节点数、write policy、FIFO 等 |
| Maps to | HU-01 至 HU-12 或其他研究问题 |
| Conflict | 与其他来源是否冲突 |
| Impact | 对目标 3 的影响 |

## 9. 禁止的错误推论

报告不得出现以下错误：

1. 把消息总数当成最长串行物理 hop 数。
2. 把 direct data 当成 direct permission/authority。
3. 把没有显式 Ack 消息名当成没有 completion 成本。
4. 把 metadata lookup 近似零时延推导为整个 Home service 为零。
5. 把 VI/presence bit 自动解释成 dirty owner bit。
6. 把单地址 coherence ordering 当成完整 CPU memory ordering。
7. 把 transport reorder 的 TLA+ 结果当成 ARM OoO 证明。
8. 把 proposed `lossless-oneway` 当成当前实现。
9. 把 C4 在 3+ 节点的能力当成 2 节点目标 3 优势。
10. 把 8N2S 的 16 planes 当成 16 nodes。
11. 把我方 fault robustness 的重试成本强行加入 lossless HA baseline。
12. 只选择 HA 的最慢分支，忽略其合法的最优分支。
13. 只报告我方平均收益，删除退化 case。
14. 用内部协议 trace 替代跨平台 guest-visible root counter。
15. 把单轮结果写成有统计置信区间的结果。

## 10. 结论等级

请对每个操作类别和整体目标分别使用：

| 等级 | 定义 |
|---|---|
| `STRICT PASS` | 在已冻结参数和共同安全边界下，数学上严格满足 OurCC `<` HA |
| `CONDITIONAL PASS` | 仅在明确 HA 分支或参数区间内满足 `<` |
| `TIE` | 理论值相同，不满足原始严格小于合同 |
| `UNPROVEN` | unknown 或 `P` 项证据不足 |
| `RISK/FAIL` | 已知合法分支下 OurCC 不快于 HA，或边界不公平 |
| `NOT APPLICABLE` | 该机制不适用于目标拓扑或操作类型 |

如果当前无法达到 `STRICT PASS`，应诚实给出最接近的条件关闭路径，而不是修改定义。

## 11. 必须提交的最终报告

请输出一份中文 Markdown 报告，建议文件名：

```text
ourcc_vs_customer_ha_external_research_report_YYYYMMDD_zh.md
```

报告必须包含以下章节。

### 11.1 执行摘要

- 一页以内。
- 目标 3 当前判定。
- 最关键的三个未知参数。
- 最可能的 PASS 分支和最危险的 FAIL 分支。
- 推荐下一步。

### 11.2 研究范围、方法和检索记录

- 检索日期。
- 数据库、搜索引擎和资料类型。
- 实际检索关键词。
- 纳入/排除标准。
- 无法访问的关键资料。

### 11.3 项目事实与外部事实分离

建立两张表：

- `PROJECT FACT`：来自本任务书的内部实现事实。
- `EXTERNAL FACT`：由公开资料支持的事实。

不得混写。

### 11.4 甲方 HA 参数账本

对 HU-01 至 HU-12 填写：

| ID | 常见公开实现选择 | 当前甲方值 | 置信等级 | 时延影响 | 需要甲方确认的问题 | 默认上下界 |
|---|---|---|---|---|---|---|

甲方值未知时写 `UNKNOWN`，不要猜测。

### 11.5 公开架构和文献综述

按机制而不是按厂商宣传组织：

- serialization authority。
- data/permission routing。
- owner tracking。
- invalidate completion。
- commit/install transient state。
- retry/replay。
- weak memory ordering。
- switch scaling。

### 11.6 三类操作 DAG

提供 OurCC 与 HA 的 Remote Read、Shared-to-Writer、Ownership Handoff DAG。

### 11.7 理论时延模型

对每类操作给出：

| 方案/分支 | T point | K logical | K cross-node | P components | lower bound | upper bound | unknown |
|---|---|---:|---:|---|---|---|---|

如果没有公开数值，不要编造纳秒值。可以保留符号表达式和 break-even。

### 11.8 目标 3 结论矩阵

至少包含：

| HA 分支 | Remote Read | Shared-to-Writer | Owner Handoff | Weighted result | Conclusion |
|---|---|---|---|---|---|

### 11.9 ARM/RISC-V memory-order 分析

- 规范事实。
- 与 coherence completion 的关系。
- litmus 测试设计。
- 当前项目尚未证明的边界。

### 11.10 Lossless transport 和 fault 分域

- link-level 与 transaction-level 分层。
- 公平比较规则。
- OurCC robustness 的正确表述。
- 节点故障和 Byzantine 等 out-of-scope。

### 11.11 16-node Switch 可行性

- 至少两种架构。
- 预计代码修改点。
- correctness/performance/fault/formal 验收矩阵。
- 风险和粗粒度工作量。

不要给虚假精确的人日；使用 `小/中/大` 或范围，并说明估计依据。

### 11.12 目标 1、目标 2 方法学审阅

- 哪些方法合理。
- 哪些会形成 bias。
- 建议的多轮统计和结果表。
- 推荐保留的负结果。

### 11.13 待甲方确认问题

请将问题压缩成甲方容易回答、不会暴露私有微架构的抽象问题。每个问题说明：

- 为什么必须问。
- 可选答案。
- 每个答案如何改变结论。

### 11.14 推荐合同文字

给出三套可选文字：

1. 保留严格 `<` 的验收文字。
2. 条件化 `<` 的验收文字。
3. `<= + 结构性优势` 的范围变更文字。

明确指出第三套属于合同变更，不是对原始文字的自动解释。

### 11.15 最终行动项

按优先级列出：

- 无需外部信息即可完成。
- 需要甲方回答。
- 需要实现。
- 需要仿真/形式化/实机测试。
- 可以明确延期或 out-of-scope。

### 11.16 参考文献和来源评价表

完整列出所有引用。

## 12. 额外交付物

除主报告外，请同时输出以下内容。

### 12.1 一页结论表

```text
target3_onepage_summary_YYYYMMDD_zh.md
```

只包含：当前结论、关键 unknown、结论矩阵、下一步和不可声称内容。

### 12.2 甲方问题清单

```text
customer_ha_questions_YYYYMMDD_zh.md
```

最多 15 个问题，按结论敏感度排序。

### 12.3 文献证据表

```text
ha_coherence_source_matrix_YYYYMMDD.tsv
```

TSV 列：

```text
id	title	organization_or_authors	year	type	url_or_doi	section_or_page	authority	claim	ha_parameter	project_impact	access_date
```

### 12.4 DAG 表

```text
ha_ourcc_operation_dags_YYYYMMDD.md
```

包含 Mermaid 和可复制表格。

### 12.5 Litmus 规格

```text
arm_riscv_coherence_litmus_plan_YYYYMMDD_zh.md
```

只写规格和预期 outcome，不伪造尚未运行的结果。

## 13. 报告写作要求

- 使用中文。
- 技术名词首次出现时给出英文。
- 结论先行，推导随后。
- 所有数字注明来源、单位和口径。
- 所有假设显式编号。
- 明确区分 observed、modeled、estimated、proposed、unknown。
- 对我方不利的合法分支必须保留。
- 不使用“显然”“一定”“普遍认为”等无证据措辞。
- 不将论文中的特定原型直接泛化为所有 HA。
- 每章结尾列“对合同目标 3 的影响”。

## 14. 完成判据

只有满足以下条件才算研究任务完成：

1. HU-01 至 HU-12 全部有公开资料分析或明确写 UNKNOWN。
2. 三类核心操作均有 OurCC/HA DAG。
3. `T_visible/T_commit/T_next` 均被区分。
4. `K_logical/K_crossnode/P` 均被区分。
5. 至少分析 central-return、direct-data-only、direct-data+authority 三个 HA 分支。
6. ARM/RISC-V memory-order 有规范引用和 litmus 计划。
7. lossless transport 与 fault robustness 公平分域。
8. 16-node Switch 有可行性和验收工作包。
9. 目标 1、目标 2 方法学有独立审阅。
10. 每个关键外部事实有可核验引用。
11. 没有编造来源、数字或甲方私有实现。
12. 最终明确给出 `STRICT PASS/CONDITIONAL PASS/TIE/UNPROVEN/RISK`，不能只写模糊总结。

## 15. 当前建议起始结论

在开始调研前，可采用以下中性初始假设：

```text
当前 OurCC 提供了明确且可审计的安全完成语义；在甲方 HA 的 authority、completion、
write policy、dirty-owner 定位、本地服务成本和物理 placement 未冻结前，严格小于关系
尚未证明。公开资料调研的目标是缩小未知区间、识别合法最优/最差分支，并定义能够
通过甲方少量抽象回答和双方共同 workload 关闭目标 3 的路径。
```

请从该中性结论出发，不要预设最终 winner。
