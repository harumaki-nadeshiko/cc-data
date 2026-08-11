# OurCC 与甲方 HA 跨节点缓存一致性理论时延对标及目标 3 交付方案

> 版本：1.1
> 初版日期：2026-08-04
> 外部研究整合：2026-08-07，公开资料检索截止 2026-08-06
> 范围：仅覆盖合同目标 3，不改变目标 1、目标 2 的比较对象与验收口径。
> 目标 3 原文：`跨节点CC同步平均时延 < HA实现跨节点CC的理论平均时延`。
> 文档属性：理论对标、假设管理、验收关闭与可选协议优化方案。除明确标注为“已实现”的能力外，
> 本文中的 proposed profile、参数分支和推导不得表述为已有实测结果。

---

## 0. 执行摘要

### 0.1 当前结论

在甲方 HA 的 write policy、peer-direct 授权能力、invalidate completion、metadata commit、
requester completion 和物理拓扑尚未全部关闭前，不能无条件证明目标 3 的严格小于关系。
当前最稳健的判定是：

> **目标 3 当前为 `UNPROVEN（存在实质性 RISK）`，但已具备明确、可执行、可审计的条件关闭路径。**

关键原因如下：

1. 当前已交付 OurCC `clear-ack` profile 中，requester 外层事务实际等待 `ClearResp` accepted，
   随后 EP-SNF 才向本地 HN/L2 返回 `CompData`。因此当前 requester-visible 路径不能排除
   `ClearReq/ClearResp`。
2. 合同是 2 节点场景。现有 C4 Direct-Forward 要求 requester、owner、home 为三个不同节点，
   在 2 节点下不可达，不能作为目标 3 的主胜因。
3. HA 的 2-bit VI + remote-presence metadata 能说明远端是否可能存在副本，但在 write-back 分支下
   不能单独表达 dirty/latest owner。HA 是否需要探测远端，取决于其写策略和节点内 HN/CHI 机制。
4. 若 HA 使用 central-return、completed invalidate Ack，拟议 `lossless-oneway` 可把 OurCC 的
   requester-visible 串行网络深度恢复到与 HA 同阶；但同阶不等于严格更快，仍需证明本地项
   `P_OurCC < P_HA`。
5. 若 HA 支持携带完整授权的 true peer-direct response，HA 可能比 OurCC 少一个串行 leg；此时
   结论可能为 tie、unproven，甚至 HA 更优。

### 0.2 推荐的验收路线

推荐采用以下组合策略，在不伪造数据、不偷换安全边界的前提下最大化通过概率：

1. 冻结共同功能域和计时边界，不比较实现内部消息名称，而比较相同 root operation 的安全完成。
2. 将 HA 所有未知属性列为正式参数，强制按 `[未知/A/B/扩展]` 分支给出结论，不默认采用其最佳分支。
3. 同时报出 `T_visible`、`T_commit`、`T_next`，主指标优先采用合同最自然的 requester-visible completion。
4. 用协议 DAG 的最长串行依赖链比较，不用消息总数或逻辑箭头数冒充物理跨节点跳数。
5. 将当前 `clear-ack` 与拟议 `lossless-oneway` 分开：前者是已交付可靠 profile，后者是目标 3
   对齐 HA lossless 假设的可选 profile。
6. 只向甲方询问抽象完成语义，不要求其披露私有微架构。未答复项保持 `[未知]`，由结论区间承担。
7. 最终平均值只使用双方一致的 target/guest-visible root counter；OurCC Outer trace 只用于拆链诊断。

### 0.3 外部研究整合结论

2026-08-06 外部研究使用 Arm CHI/Arm ARM、CCIX、CXL 公开资料、经典目录论文、
RISC-V RVWMO、TLA+ coherence verification 和 NIST/SciPy 统计资料，对本文的内部条件模型
进行了核验。结论没有把甲方私有 HA 映射到任何公开厂商实现，关键结果如下：

1. 公开协议共同要求 per-line serialization authority；direct data 不自动携带 authority。
2. 无显式 Ack 只在存在可验证 ordering/completion 时构成合法 fast path，dependency 不能删除。
3. 两节点 2-bit presence 可表达副本存在性，但不能单独表达 dirty/latest owner 和 transient。
4. Arm/RISC-V ISA completion 不能由内部 fabric response 自动替代，必须有 endpoint mapping。
5. 合法 direct-data+authority HA 分支可使 `T_visible` 达到 K=3，是对 OurCC 不利的实质风险。
6. central-return 分支常见同 K=4；同 K 必须证明 `P_OurCC<P_HA`，不能自动 PASS。
7. strict `<` 的最短关闭路径是甲方回答 15 个抽象问题并冻结 paired workload/CI，而不是
   继续扩展假想 HA 数值。

外部研究权威附件：

- `docs/research/ourcc_vs_customer_ha_external_research_report_20260806_zh.md`
- `docs/research/target3_onepage_summary_20260806_zh.md`
- `docs/research/customer_ha_questions_20260806_zh.md`
- `docs/research/ha_coherence_source_matrix_20260806.tsv`
- `docs/research/ha_ourcc_operation_dags_20260806.md`
- `docs/research/arm_riscv_coherence_litmus_plan_20260806_zh.md`

### 0.4 可以和不可以声称的内容

| 主张 | 当前是否可用 | 说明 |
|---|:---:|---|
| 当前 OurCC 已严格快于甲方 HA | 否 | HA 参数未关闭，当前 Clear 还可能增加前台路径 |
| 当前 OurCC 有明确、可审计的安全完成点 | 是 | Recall/Invalidate barrier、Clear commit、epoch/reqId/tombstone 均有代码证据 |
| `lossless-oneway` 可删除 requester 对 ClearResp 的等待 | 条件可用 | 必须标注 proposed/unimplemented，并在真实 local install 后发 one-way Clear |
| `lossless-oneway` 一定严格快于 HA | 否 | 对 HA central-return 通常先达到同 K；严格小于还需 P 项优势 |
| HA 若无显式 Ack 就一定更快 | 否 | 无消息名不等于无完成语义，必须证明隐式 completion |
| HA 2-bit 目录一定无法处理 dirty data | 否 | 可能采用 write-through，或通过节点内 HN 状态/探测处理 |
| 当前 C4 在 2 节点下带来目标 3 优势 | 否 | 当前触发条件在 2 节点不可达，且 direct data 不携带正式 Grant authority |
| OurCC 额外的 drop/dup/reorder 鲁棒性优于 HA | 可作能力差异 | 不得把该能力的时延成本强加给 HA lossless 理论模型 |

---

## 1. 合同范围与比较对象

### 1.1 目标隔离

| 合同目标 | 比较对象 | HA 是否参与 |
|---|---|:---:|
| 目标 1 | OurCC capacity baseline vs OurCC capacity optimization | 否 |
| 目标 2 | OurCC naive latency vs OurCC optimized latency | 否 |
| 目标 3 | OurCC theoretical latency vs 甲方 HA theoretical latency | 是 |

目标 3 不应反向改写目标 1、目标 2，也不应以 HA 的 2-bit metadata 容量重新计算目标 1。

### 1.2 OurCC 比较 profile

| Profile | 状态 | 核心语义 | 目标 3 用途 |
|---|---|---|---|
| `OurCC-current-clear-ack` | 已实现、当前交付基线 | Grant 后发 ClearReq，Home commit，requester 等 ClearResp accepted | 保守、真实的当前实现结果 |
| `OurCC-lossless-oneway` | 拟议、尚未实现 | requester local install 后单向 Clear，不等待 ClearResp；Home 收 Clear 后 commit | 与 HA lossless 假设公平对齐的推荐 profile |
| `OurCC-eager-no-clear` | 理论探索、不推荐 | Home 在 Grant 发出前后直接 commit | 用于说明理论下界和非 FIFO 风险，不作为近期交付 |

禁止把 `lossless-oneway` 或 `eager-no-clear` 的理论 K 值标为当前代码实测能力。

### 1.3 甲方 HA 已知条件

| ID | 已知条件 | 状态 | 对比较的含义 |
|---|---|---|---|
| HK-01 | 2 节点 | 已知 | 三个逻辑角色中至少两个物理共址，必须区分 logical leg 与 cross-node traversal |
| HK-02 | 全局缓存地址空间不超过 128 MiB | 已知 | 约束 HA metadata 覆盖域，不直接决定单事务时延 |
| HK-03 | VI 协议 | 已知 | 节点级状态至少为 Valid/Invalid，不等同于 MESI dirty-owner metadata |
| HK-04 | 每 Cacheline 2-bit 节点级目录 | 已知 | bit1=VI state，bit2=唯一远端节点 presence |
| HK-05 | 无额外节点级 dirty-owner metadata | 已知 | write-back 下 latest data 定位依赖写策略、节点内 HN 或 probe |
| HK-06 | 节点内详细状态可由现有 HN/CHI 维护 | 已知 | 不应假设 HA 完全没有 dirty 信息，但需明确查询与完成路径 |
| HK-07 | metadata 位于 HA 或 IODie，二者不作明显时延区分 | 已知 | 本文将二者统一抽象为 HA/Home |
| HK-08 | metadata lookup 近似零附加时延 | 已知 | OurCC 不能依赖目录 lookup 成本证明领先 |
| HK-09 | Requester 到 HN 的路径与 OurCC 一致 | 已知 | 公共前缀可抵消 |
| HK-10 | Remote Read 从 valid/latest data 所在位置取数 | 已知 | 必须讨论 latest 所在 Home memory 或 remote owner 的分支 |
| HK-11 | 网络不考虑丢包 | 已知 | HA 模型不加入虚构的重传成本；可定义 OurCC lossless profile 对齐 |
| HK-12 | 网络不保证 FIFO | 已知 | 同址 Grant/Recall 因果必须由协议保证，不能依赖全局 FIFO |
| HK-13 | 独立事务可按 ARM/RISC-V 弱序乱序完成 | 已知 | 不同地址可乱序，但单地址 coherence 和 barrier 语义不能破坏 |
| HK-14 | HA 与 OurCC 同频 | 已知 | 可使用同一时钟尺度比较本地处理项 |

---

## 2. 公平性与统一完成定义

### 2.1 共同功能域

一次被计入目标 3 的跨节点 root operation，在比较终点至少应满足：

1. requester 获得正确且最新的数据；
2. requester 获得操作所需的 Shared、Exclusive 或 Modified 等价权限；
3. 所有冲突旧权限已完成失效、降级，或被协议机制保证不能继续形成冲突访问；
4. 不存在两个节点同时合法使用冲突写权限；
5. dirty/latest data 不丢失；
6. 同一 Cacheline 的后续冲突事务存在明确序列化点；
7. 不同地址的弱序优化不能替代单地址 coherence ordering；
8. 若 workload 使用 completed store、fence、DSB 或等价屏障，posted write 不能冒充完成。

任何不满足上述安全条件的“更短路径”都不属于合法 HA 理论下界。

### 2.2 推荐主计时边界

推荐目标 3 主边界为：

> **起点**：Requester HN 发出首个跨节点 coherence request。
> **终点**：Requester HN/cache 的 root operation 已安全取得正确数据或权限，且冲突远端旧权限
> 已不可继续使用。

Requester→HN 是双方相同公共前缀，可在理论比较中抵消。若合同方坚持从 CPU 指令 issue 开始，
双方必须同时加入相同层级的 CPU/L1/L2/HN 前缀，不能只对一方加入。

### 2.3 三个必须同时报告的完成点

#### 2.3.1 Requester-visible completion

记为：

\[
T_{visible}
\]

定义：requester 的 HN/cache/architectural operation 已安全获得正确数据或权限，并满足旧冲突权限
不可继续使用。本文推荐将其作为目标 3 主指标。

#### 2.3.2 Home/global commit

记为：

\[
T_{commit}
\]

定义：Home/HA metadata 已正式提交新目录状态和事务代际，不再处于 tentative/reserved 状态。

#### 2.3.3 Next-conflict release

记为：

\[
T_{next}
\]

定义：同一 Cacheline 的下一冲突事务可继续执行，且不会绕过上一事务的安全闭环。

一般有 `T_next >= T_commit`，但具体实现也可能在 metadata commit 后继续保留 install guard。

#### 2.3.4 当前 OurCC root completion

记为 `T_root_current`：当前 requester 等待 `ClearResp accepted`，随后 EP-SNF 才向本地 HN/L2
完成 root path。它是当前实现可测的正式 root completion，但不应与更早、按共同安全语义定义的
`T_visible` 混为同一点。

外部研究 DAG 中：

- `T_visible` 只在 latest data + authority + agreed requester install 条件满足时成立；
- `T_commit/T_next` 仍由 Home commit/release 决定；
- `T_root_current` 是 current clear-ack 的额外前台等待点。

### 2.4 不得混用的测量口径

| 口径 | 可否作为正式目标 3 结果 | 用途 |
|---|:---:|---|
| Target/guest-visible root issue→root complete | 是 | 双方正式比较 |
| OurCC Outer transaction latency | 否 | 拆分协议内部路径 |
| ReadResp 到 EP 的时间 | 否 | response-visible 诊断，不等于 HN/L2/cache install |
| ClearReq/ClearResp 独立链 | 否 | 提交阶段诊断 |
| gem5 wall-clock | 否 | 受 PDES、主机调度和进程布局影响 |

正式计量边界见 `docs/delivery/ha_comparison_request_chains_20260731_zh.md:19-56`。

---

## 3. 理论模型

### 3.1 基础模型

对操作类别 `o` 和完成点 `x`：

\[
T_{o,x}=K_{o,x}\tau+P_{o,x}
\]

其中：

- `K`：该完成点最长串行依赖链上的归一化 fabric legs；
- `tau`：一个归一化单向 fabric leg 的时延；
- `P`：目录处理、HN/CHI service、数据选择、cache install、commit、排队等非 fabric 项。

可进一步分解：

\[
P=P_{dir}+P_{peer}+P_{data}+P_{install}+P_{commit}+P_{queue}
\]

Requester→HN 公共项不进入差值，或在双方公式中同时保留。

### 3.2 `K` 的正确解释

必须区分：

1. `K_logical`：requester、home、owner 等逻辑角色之间的串行协议段；
2. `K_crossnode`：真正跨节点/跨 die 的物理传输段；
3. message count：总消息数量；
4. fanout count：并行发送到多个 sharer 的消息数量。

合同是 2 节点。若 requester 和 home 分处两节点，则 remote owner 必与其中一方共址；因此
`requester→home→owner→home→requester` 的四个逻辑箭头不必然等于四次物理跨节点传输。
正式计算表必须同时填写 `K_logical` 与 `K_crossnode`。

### 3.3 并行路径

若 peer direct 只发送数据，而权限仍由 Home 返回，完成时间应取两条依赖路径的最大值：

\[
T_{visible}=T_{prefix}+\max(T_{peer\rightarrow requester,data},
T_{peer\rightarrow home,ack}+T_{home\rightarrow requester,grant})+P
\]

因此 direct data 不自动意味着 permission critical path 减少一个 leg。

### 3.4 加权平均

建议操作集合至少包括：

- `R_h`：Home memory 已是 latest 的 Remote Read；
- `R_o`：latest data 位于 remote owner 的 Remote Read；
- `W_s`：shared→writer，需要远端 invalidate；
- `W_o`：ownership handoff，需要旧 owner 释放并转移 latest data；
- `M`：metadata refill/probe；
- `C`：contention/retry。

平均值：

\[
\bar T=\sum_i w_iT_i,\quad \sum_iw_i=1
\]

权重 `w_i` 必须来自双方同意的合同 workload、双方可复现 trace，或书面冻结的理论分布。
只统计 root issue 时确实触发跨节点 coherence dependency 的操作。Silent Upgrade、retained local hit 等
没有跨节点依赖的操作，不应只在 OurCC 一侧混入“跨节点同步平均值”以稀释均值。

### 3.5 Break-even

OurCC 严格快于 HA 的条件为：

\[
(K_{HA}-K_{OurCC})\tau>P_{OurCC}-P_{HA}
\]

结论解释：

| K 关系 | 严格小于所需条件 |
|---|---|
| `K_OurCC < K_HA` | OurCC 多出的本地处理小于节省的 fabric legs |
| `K_OurCC = K_HA` | 必须证明 `P_OurCC < P_HA`；只能说同阶不足以满足严格 `<` |
| `K_OurCC > K_HA` | 必须证明 HA 的本地处理劣势大于至少一个 `tau`，通常较难 |

### 3.6 结论等级

| 等级 | 定义 |
|---|---|
| `STRICT PASS` | 在已冻结参数和共同安全边界下，数学上严格满足 `<` |
| `CONDITIONAL PASS` | 指定 HA 分支或参数范围成立时满足 `<` |
| `TIE` | 理论值相同；不满足合同严格小于 |
| `UNPROVEN` | 未知项或 P 项证据不足，不能判定 |
| `RISK/FAIL` | 已知分支下 OurCC 不快于 HA，或比较边界不公平 |

---

## 4. HA 未知项台账与 `[未知/A/B]` 分类

### 4.1 HA 参数总表

| ID | 属性 | 当前状态 | 关闭方式 | 结论敏感度 |
|---|---|---|---|---|
| HU-01 | write-through / write-back | `[未知]` | 甲方抽象协议说明 | 高 |
| HU-02 | peer response routing | `[未知]` | 消息依赖图或脱敏 trace | 高 |
| HU-03 | peer direct 是否携带 Grant authority | `[未知]` | 回答 data-only 或 data+permission | 极高 |
| HU-04 | invalidate completion | `[未知]` | 明确 completed Ack/implicit completion | 极高 |
| HU-05 | metadata commit 点 | `[未知]` | 明确 Grant 前、Ack 后或 requester install 后 | 极高 |
| HU-06 | requester completion 是否等待 commit | `[未知]` | root_complete 定义 | 极高 |
| HU-07 | dirty/latest data 定位 | `[未知]` | HN 查询、presence 推断或 probe | 高 |
| HU-08 | same-line serialization | `[未知]` | per-line lock/version/install guard | 高 |
| HU-09 | HA/IODie 与两个节点的物理拓扑 | `[未知]` | 逻辑拓扑图 | 高 |
| HU-10 | local service/queue `P_HA` | `[未知]` | 参数表、target trace 或上下界 | 中高 |
| HU-11 | store completion 与 barrier 语义 | `[未知]` | workload API 定义 | 高 |
| HU-12 | contention/retry policy | `[未知]` | NACK、queue、version retry 说明 | 中高 |

### 4.2 Peer response 与授权

| 分支 | 定义 | 理论影响 | 我方对标策略 |
|---|---|---|---|
| `[未知]` | 不知道 peer 是否直返，也不知道是否携带权限 | 只能给 central/direct 区间 | 结论保持 UNPROVEN，要求关闭 HU-02/HU-03 |
| `[A: central-return]` | peer data/Ack 先回 HA，再由 HA Grant requester | 常见冲突路径约 4 logical legs | `lossless-oneway` 可争取同 K，并比较 P |
| `[B: direct-data-only]` | peer 直发数据，但 Home 仍返回权限或 commit token | 关键路径取 data 与 permission 两路最大值，未必减 K | 强调 OurCC 当前 C4 也属于 data-only 类，公平按 DAG 比较 |
| `[C: direct-data+authority]` | peer response 同时携带完整、可验证的 Grant authority | 可形成真正 3-leg candidate | OurCC 可能不占优；需转为条件结论或引入真正 authority-forward 设计 |
| `[D: speculative-data]` | 数据提前，权限随后到达 | 可降低数据等待，不一定降低 root completion | 按 permission completion 计时，不能以数据先到代替安全完成 |

### 4.3 Invalidate completion

| 分支 | 定义 | 是否是合法公平基线 | 时延影响 |
|---|---|:---:|---|
| `[未知]` | 不知道 HA 是否等待远端真正失效 | 待定 | Writer Acquire 无法给出下界 |
| `[A: explicit-completed-Ack]` | 远端 HN/cache 完成 invalidation 后返回 Ack | 是 | central path 常为 requester→HA→peer→HA→requester |
| `[B: peer-direct-completion]` | peer 完成失效后直接向 requester 返回 delegated completion | 条件是 | 可少一 leg，但 HA 必须保留同址序列化并授予 authority |
| `[C: implicit-fabric-completion]` | 无显式 Ack 消息，但 fabric/snoop completion 保证旧权限已不可用 | 条件是 | 需提供等价硬件语义；不能因没有消息名就视为零成本 |
| `[D: eager-Grant-without-proof]` | 未确认旧 sharer 失效即授予 writer | 否 | 不满足共同安全边界，不纳入理论下界 |

### 4.4 Metadata commit

| 分支 | 定义 | 关键风险 | 对完成点的影响 |
|---|---|---|---|
| `[未知]` | commit 点未披露 | 无法确定 `T_commit/T_next` | 给上下界，不判 PASS |
| `[A: peer-completion-then-commit]` | Ack/data 回 HA 后 commit，再 Grant requester | 路径清晰 | visible 常等于或晚于 commit |
| `[B: requester-install-then-commit]` | 类似两阶段提交，requester install 确认后 commit | 多一个确认方向 | 与 OurCC Clear 类似，可公平比较 Ack/oneway |
| `[C: eager-commit-with-guard]` | HA 在 Grant 前 commit，但用 per-line lock/version/pending-install guard | guard 的释放点需明确 | visible 可短，next 仍可能等待 install |
| `[D: split-commit]` | 先清旧 owner/sharer，再安装新 owner | 中间态必须安全且可恢复 | 需要逐阶段 DAG |
| `[E: eager-without-guard]` | metadata 指向尚未安装 Grant 的新 owner，并立即处理下一冲突 | Recall-before-Grant 风险 | 不属于合法公平基线 |

### 4.5 Write policy

| 分支 | latest data | Remote Read 影响 | 我方策略 |
|---|---|---|---|
| `[未知]` | 未知 | 无法判断 Home 是否可直接返回 | 同时计算 WT/WB 分支 |
| `[A: write-through]` | Home memory 通常保持最新 | Remote Read 可短至 requester↔HA；写路径可能承担额外更新成本 | 不主张 HA 需 remote dirty recall；按其最有利读路径计算 |
| `[B: write-back]` | latest data 可能只在 remote cache | 需要定位并取得 dirty owner 数据 | 检查 HA 是否查询 HN 或保守 probe |
| `[C: hybrid]` | region/operation 分别采用 WT/WB | 平均值依赖各类权重 | 分类型加权，不用单一 K |
| `[D: update-on-transfer]` | owner handoff 时直传，Home memory 延后更新 | read 与 write/handoff 路径不同 | 分别建立 R_o 与 W_o DAG |

### 4.6 Dirty/latest owner 定位

| 分支 | 定义 | 结果 |
|---|---|---|
| `[未知]` | 2-bit 之外如何找到 latest 未知 | 不得断言 HA 一定快或一定慢 |
| `[A: WT-no-dirty-owner]` | memory 始终 latest，presence 只服务失效 | 读路径最短，写路径需计 WT 成本 |
| `[B: WB-query-HN]` | HA 查询本地/远端 HN 的详细状态 | `P_HA` 增加查询，必要时增加 peer leg |
| `[C: WB-presence-implies-owner]` | 远端 presence 被协议限制为唯一 latest owner | 需证明不允许双方同时 valid shared，否则 presence 不能表达 dirty |
| `[D: WB-conservative-probe]` | HA 不知道 dirty 位，向远端探测 | Remote Read/Write Acquire 加入 probe dependency |
| `[E: memory-peer-race]` | memory read 与 peer probe 并行，选择已证明 latest 的结果 | 时延取并行最大/最小组合，需版本验证 |

### 4.7 Requester completion

| 分支 | 定义 | 目标 3 处理 |
|---|---|---|
| `[未知]` | root_complete 未定义 | 不给单值，保留 visible/commit 两种结果 |
| `[A: blocking-to-global-commit]` | requester 等 metadata commit | 与 `T_commit` 对齐 |
| `[B: blocking-to-install]` | requester install 后完成，Home commit 后台化 | 主比较 `T_visible`，另报 `T_commit/T_next` |
| `[C: data-early-permission-late]` | 数据先到，权限后到 | root completion 取权限到达 |
| `[D: store-buffer-accepted]` | store 进入 buffer 即对 CPU 返回 | 若合同需要 completed store，不能作为终点 |
| `[E: posted-write-with-fence]` | posted write 后由 fence/DSB 收敛 | 计时必须覆盖 fence/DSB 完成 |

### 4.8 Same-line serialization

| 分支 | 机制 | 合法性与影响 |
|---|---|---|
| `[未知]` | 同址事务如何排序未知 | 无法证明 `T_next` 和 eager commit 安全 |
| `[A: per-line-lock]` | 目录项锁到事务完成 | 安全清晰，但可能产生 queue 项 |
| `[B: version/epoch]` | 多事务可并发，commit 时校验版本 | 需计算 retry/conflict `P_queue` |
| `[C: pending-install-token]` | 新 owner 未安装前不向其发同址 Recall | 可支持 eager commit，但 token 释放点需明确 |
| `[D: no-guard]` | metadata eager 指向新 owner后立即处理下一请求 | 非 FIFO 下不安全，不纳入下界 |

### 4.9 物理拓扑

| 分支 | 定义 | 处理方式 |
|---|---|---|
| `[未知]` | HA 是独立 IODie、附着某节点，还是每节点分布式 | 同时报 logical DAG 与 placement cases |
| `[A: independent-central-HA]` | 两节点均通过 fabric 到独立 HA/IODie | logical legs 较接近物理 traversals |
| `[B: HA-colocated-with-home-node]` | HA 与某节点 HN 共址 | HA↔该节点为 local service |
| `[C: distributed-HA]` | 每节点有 HA slice，PA hash 到 home slice | requester/home placement 决定物理跨节点次数 |

---

## 5. OurCC 当前代码事实

### 5.1 Current Clear 是 commit 协议，不是普通内存屏障

当前 Clear 至少承担：

1. 以 `epoch + reqId + requester` 匹配 Grant handshake；
2. 将 intended state 提交为 committed directory state；
3. 退役相应 waiter；
4. 安装 tombstone，支持 duplicate Clear 幂等重放；
5. 删除 outstanding；
6. 释放同址 pending request replay；
7. 拒绝 stale/mismatched completion。

代码证据：`modules/ubiomodule/UBCCController.cc:3573-3788`。

### 5.2 Current requester-visible completion 包含 ClearResp 等待

`EPBackend::handleRemoteMiss()` 在收到 Grant 并处理数据后调用 `sendClear()`；若返回 `-2`，事务继续
pending。只有 Clear accepted 后才清除 `outerTxnPending` 并返回成功：

- `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc:483-515`
- `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc:782-851`
- `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc:2231-2307`

EP-SNF 只有在 `handleRemoteMiss()` 成功返回后才发送 `CompData`：

- `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc:105-155`
- `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc:275-300`

因此现有 trace 工具把 ReadResp 与 Clear 分开，只能说明工具口径，不证明 HN/L2 已在 Clear 前完成。

### 5.3 Current Clear 也不是严格的 local cache install Ack

当前 EPBackend 在对本地 HN/L2 发送最终 `CompData` 之前发 Clear。因此更准确的表述是：

> Current Clear 确认 requester-side protocol agent 已接受 Grant，并触发 Home commit；它不是由
> HN/L2 明确返回的“缓存行已安装”确认。

这意味着 `lossless-oneway` 若要把 ClearResp 后台化，不能只是让 `sendClear()` fire-and-forget；必须
补足 local install completion 或等价 pending-grant guard。

### 5.4 Recall/Invalidate 在 Grant 前完成

Home 收到 RecallResp 并校验 owner、epoch、reqId、dirty data 后，才创建 `GRANT_HANDSHAKE` 并 Push Grant：

- `modules/ubiomodule/UBCCController.cc:2248-2367`
- `modules/ubiomodule/UBCCController.cc:2402-2465`

`processClear()` 只接受 `WAITING_CLEAR`，且代码约束该阶段只在 Recall/Invalidate barrier 完成后建立：

- `modules/ubiomodule/UBCCController.cc:3721-3746`

因此旧 owner/sharer 的冲突权限在正常路径上先于新 Grant 被安全回收，这是 OurCC 可审计的优势。

### 5.5 C4 Direct-Forward 的目标 3 限制

当前 C4 触发条件要求：

```cpp
requesterNode != ownerNode && requesterNode != homeNode
```

代码：`gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc:1242-1279,1325-1361`。

合同仅有 2 节点，无法满足 requester、owner、home 三个不同 node ID。此外 direct data 使用
`reqId=0`，不会被 requester 的同步 ReadReq 作为正式 Grant 消费；Home 仍需在 RecallResp 后发送
携带完整 metadata 的 Push Grant。故 C4 当前只优化部分数据流，不等于 authority critical path 缩短。

配置默认值也为关闭：`gem5/src/mem/ruby/protocol/chi/ep/EPBackend.py:28-29`。

### 5.6 TLA+ 能证明和不能证明的内容

核心模型 `verification/tla/ubcc_protocol_core.tla` 覆盖 reserve-then-commit、Recall、Invalidate、
ClearCommit、UpgradeCommit 等抽象状态机；`ClearCommit` 位于约 `:393-424`。

模型边界：

1. 默认 `Nodes={0,1,2}`，不是合同专用 2 节点模型；
2. 不建模真实物理时间、链路排队和 HN/L2 cache install；
3. 不证明某个 `K` 或 target-visible latency；
4. 不包含完整 ARM/RISC-V memory model；
5. transport fault model 的 safety/liveness 不能替代性能证据。

---

## 6. 三类核心操作 DAG 与完成点

以下 `K` 为通用 logical critical-path 近似。正式计算必须根据 HU-09 再映射到物理跨节点 traversals。

> **口径更新：** 本节原有 `K_visible` 表是在初版中按“当前 requester root 等待 ClearResp”记账，
> 实质更接近 `K_root_current`。外部研究已经把早期 data+authority 可见点记为 `T_visible`，把
> current ClearResp 等待单列为 `T_root_current`。新评审应优先使用
> `docs/research/ha_ourcc_operation_dags_20260806.md`；本节旧 K 表保留用于说明当前 root 路径，
> 不得与新 `T_visible` 表直接混算。

### 6.1 Remote Read

#### 6.1.1 `R_h`：Home/memory 已有 latest data

HA central：

```text
Requester --Read--> HA/Home --Data+Grant--> Requester
```

- `K_visible ~= 2`
- 若远端 presence 仍需 downgrade，转入 owner/sharer 分支。

OurCC current：

```text
Requester EP --ReadReq--> Home
Requester EP <--Grant/Data-- Home
Requester EP --ClearReq--> Home [commit, next-conflict release]
Requester EP <--ClearResp-- Home
Requester HN/L2 <--local CompData-- EP
```

- `K_root_current ~= 4`；Grant/data 到 EP 的早期 `T_visible` 候选需另行冻结 install/authority 边界
- `K_commit ~= 3`
- `K_next ~= 3`

OurCC lossless-oneway：

```text
Requester --ReadReq--> Home
Requester <--Grant/Data-- Home
Requester local install
Requester --Clear(oneway)--> Home [commit, next-conflict release]
Requester root complete without waiting for ClearResp
```

- `K_visible ~= 2`，但必须包含 local install `P_install`
- `K_commit ~= 3`
- `K_next ~= 3`

#### 6.1.2 `R_o`：latest data 位于 remote owner

HA central-return：

```text
Requester --Read--> HA --Recall/Probe--> Owner
Requester <--Grant+Data-- HA <--Data+Release-- Owner
```

- `K_visible ~= 4`

HA true peer-direct authority：

```text
Requester --Read--> HA --DelegatedRecall--> Owner
Requester <--Data+Authority+CompletedRelease-- Owner
```

- 条件成立时 `K_visible ~= 3`
- 必须证明 Home 已授权、旧权限释放和 next-conflict serialization。

OurCC current：

```text
Requester --ReadReq--> Home
Home --RecallReq--> Owner
Owner --RecallResp(data+release)--> Home
Home --PushGrant(data+permission)--> Requester
Requester --ClearReq--> Home [commit]
Home --ClearResp--> Requester
EP --local CompData--> HN/L2
```

- `K_root_current ~= 6`；Grant/data 到 EP 的 `T_visible` 候选约 K=4，但不是当前 HN/L2 root completion
- `K_commit ~= 5`
- `K_next ~= 5`

OurCC lossless-oneway：

- `K_visible ~= 4`
- `K_commit ~= 5`
- `K_next ~= 5`

这里的 current C4 direct data 与 Home Grant 是并行双路径，正式权限仍等待 Home Grant，不能把
current 路径直接改写为 3 legs。

### 6.2 Writer Acquire / Remote Invalidate

HA explicit completed Ack：

```text
Requester --WriteIntent--> HA
HA --Invalidate--> Remote Sharer
Remote HN/cache completes invalidation
Remote Sharer --Ack--> HA [commit]
HA --Grant--> Requester
```

- `K_visible ~= 4`
- commit 通常在第 3 leg 后；具体是否阻塞 requester 由 HU-05/HU-06 决定。

HA peer-direct completion：

```text
Requester --> HA --> Remote Sharer
Remote Sharer --> Requester [completed invalidation + delegated Grant]
Remote Sharer --> HA [metadata update, possibly parallel]
```

- 可成为 `K_visible ~= 3` 的候选；必须证明 delegated authority 与同址锁。

OurCC current：

```text
Requester --ReadUnique--> Home
Home --InvalidateReq--> Remote Sharer
Remote Sharer completes local CHI invalidation
Remote Sharer --InvalidateAck--> Home
Home --PushGrant--> Requester
Requester --ClearReq--> Home [commit]
Home --ClearResp--> Requester
EP --local CompData/permission--> HN/L2
```

- `K_root_current ~= 6`；Grant 到 EP 的 `T_visible` 候选约 K=4，current root 仍等待 ClearResp
- `K_commit ~= 5`
- `K_next ~= 5`

OurCC lossless-oneway：

- Grant 前仍等待 completed InvalidateAck；
- requester local install 后单向 Clear；
- `K_visible ~= 4`；
- `K_commit/K_next ~= 5`。

注意 fanout 消息数不等于 K。合同 2 节点只有一个远端节点级 target。

### 6.3 Ownership Handoff

HA central-return：

```text
New Writer --> HA --> Old Owner
Old Owner completes release and returns latest data --> HA
HA commits new owner --> New Writer
```

- `K_visible ~= 4`

HA direct-data-only：

```text
Old Owner --data--> New Writer
Old Owner --release Ack--> HA --Grant authority--> New Writer
```

- root completion 取两条路径最大值，通常不能直接按 3 legs。

HA true direct ownership transfer：

```text
New Writer --> HA --> Old Owner
Old Owner --latest data + release + delegated ownership--> New Writer
```

- 条件成立时 `K_visible ~= 3`。

OurCC current：

```text
New Writer --> Home
Home --> Old Owner [RecallUnique]
Old Owner local CHI completes and R_E/R_M -> R_I
Old Owner --> Home [RecallResp(latest data)]
Home --> New Writer [PushGrant]
New Writer --> Home [ClearReq, commit]
Home --> New Writer [ClearResp]
EP --> local HN/L2 [CompData/permission]
```

- 旧 owner 不可继续使用：RecallResp 前的 local CHI completion；
- tentative Grant：第 4 logical leg；
- commit/next：第 5 leg；
- current `T_root_current`：约第 6 leg；不得标为统一 `T_visible`。

OurCC lossless-oneway：

- `K_visible ~= 4`
- `K_commit/K_next ~= 5`
- 不等待第 6 leg ClearResp。

### 6.4 Eager no-Clear 的非 FIFO 反例

```text
1. Home 向 A 发 Grant(X)
2. Home 立即 commit owner=A，并放行下一同址请求
3. B 请求 X
4. Home 向 A 发 Recall(X)
5. 网络非 FIFO，Recall(X) 先于 Grant(X) 到 A
6. A 尚无数据/权限，无法正常完成 Recall
```

解决该反例至少需要以下之一：

- requester 按 epoch/reqId 缓冲 early Recall；
- requester NACK，Home retry；
- Home pending-install guard；
- transport delivery/install completion；
- 同址消息专用 ordering guarantee。

因此“网络不丢包”不等于“可以无条件删除 Clear”。

---

## 7. K 值和结论矩阵

### 7.1 Logical K 到各 profile 的前台完成点

| 操作 | HA central `K_visible` | HA direct `K_visible` | OurCC current `K_root_current` | OurCC one-way `K_visible` | OurCC eager `K_visible` |
|---|---:|---:|---:|---:|---:|
| Home-latest Remote Read | 2 | 2 | 4 | 2 | 2 |
| Dirty-owner Remote Read | 4 | 3（条件） | 6 | 4 | 4 |
| Remote Invalidate Writer Acquire | 4 | 3（条件） | 6 | 4 | 4 |
| Ownership Handoff | 4 | 3（条件） | 6 | 4 | 4 |

注：OurCC current 列是等待 ClearResp 的 `K_root_current`，不能与其他列的 `K_visible`
直接当成同完成点比较。该表是 logical DAG 归一化示意，也不代表 2 节点下每个 leg 都跨物理节点。

### 7.2 Profile 三完成点

| Profile | `T_visible` | `T_commit` | `T_next` | requester root |
|---|---|---|---|---|
| HA central blocking | 通常等于或晚于 commit | peer completion 后 | commit/lock release 后 | 依 HU-06/HU-11 |
| HA install-visible/background commit | requester install 时 | 稍晚 | 通常 commit 后 | 可与 visible 同点，须甲方确认 |
| OurCC current | Grant/data 到 EP 后的候选点；HN/L2 install 未确认 | ClearReq 到 Home | Clear commit 后 | ClearResp accepted 后返回 local CompData |
| OurCC lossless-oneway | 真实 local install 后，不等 ClearResp | one-way Clear 到 Home | commit 后 | 未实现 |
| OurCC eager no-clear | Grant install 后 | Grant 前/同时 | 需额外 guard，否则不安全 | 理论探索 |

### 7.3 目标 3 结论矩阵

| HA 分支 | OurCC current | OurCC lossless-oneway | 推荐结论 |
|---|---|---|---|
| WT + Home latest + central response | 通常多 Clear RTT | 可达到同 K | current 不利；oneway 仍需 P 优势 |
| WB + central-return + completed Ack | 通常多 Clear RTT | 常见冲突路径同 K | `CONDITIONAL PASS` 取决于 P 和操作权重 |
| WB + direct-data-only | current 不利 | permission DAG 可能同 K | 需按 max(data, permission) 计算 |
| WB + true peer-direct authority | 多 2-3 legs | 常多 1 leg | `UNPROVEN/RISK`，不能承诺领先 |
| HA requester-install Ack commit | 可能接近双方两阶段 | oneway 可能少 Ack response leg | 有利分支，可重点关闭 |
| HA eager commit + safe install guard | current 不利 | 可能同 K | 比较 guard/commit 的 P 项 |
| HA eager commit 无 guard | 不比较 | 不比较 | 非合法安全下界 |
| HA WB 且必须 conservative probe | 取决于 probe | OurCC 显式 owner 可能占优 | 可形成条件严格优势 |

### 7.4 外部研究后的合法分支矩阵

| HA 分支 | 对 OurCC 的影响 | 当前等级 |
|---|---|---|
| Home-memory latest，Grant 前/同时 commit | HA 可大量使用 K=2；OurCC commit/root 仍有 Clear | `UNPROVEN/RISK` |
| central-return + explicit completed Ack + root 等 commit | 常见 visible K=4 vs K=4；P 和权重决胜 | `UNPROVEN`；可达 `CONDITIONAL PASS` |
| direct-data-only + Home Grant | data 可早到，authority path 通常仍 K=4 | `UNPROVEN` |
| direct-data+authority + commit 并行 | HA visible 可 K=3 | `RISK/FAIL` 条件分支 |
| implicit completion 无 ordering guarantee | 不满足共同安全域 | `NOT APPLICABLE` |
| write-through、memory 常 latest | 提高 HA home fast-path 权重 | 对 OurCC `RISK` |
| write-back、无 exact owner、必须 probe | 增加 HA service/dependency | OurCC 可 `CONDITIONAL PASS` |

整体仍为 `UNPROVEN（存在实质性 RISK）`，不是双方实测 FAIL，也不是条件 PASS。

### 7.5 平均时延关闭公式

对双方同一操作权重：

\[
\Delta \bar T=\bar T_{OurCC}-\bar T_{HA}
=\sum_i w_i[(K_{OurCC,i}-K_{HA,i})\tau+(P_{OurCC,i}-P_{HA,i})]
\]

验收条件为：

\[
\Delta \bar T<0
\]

每一个 `[未知]` 参数都应映射到 `K/P/w` 的一个区间。若整个允许区间均 `<0`，可判 `STRICT PASS`；
若只有特定分支 `<0`，判 `CONDITIONAL PASS`；若区间跨过 0，则判 `UNPROVEN`。

---

## 8. “尽量圆过去”且保持合同诚信的评审策略

### 8.1 主叙事

推荐评审主叙事如下：

> 目标 3 比较的是相同安全语义下的跨节点同步理论时延，而不是私有模块数量、消息名称或仿真
> wall-clock。OurCC 已提供明确可审计的 Recall/Invalidate barrier、owner data 选择、epoch/reqId、
> Home commit 和 same-line release 语义。甲方 HA 的若干关键完成语义尚未披露，因此本文不猜测其
> 私有实现，而以 `[未知/A/B]` 分支给出上下界。对双方共同的 lossless 假设，我方提出
> `lossless-oneway` profile，在保留旧权限回收和 Home 序列化的同时，删除 requester 对 ClearResp
> 的前台等待。最终结论由甲方抽象参数确认单和统一 root counter 关闭。

该叙事的优势是：

1. 把争论从“谁消息少”转成“谁在相同安全终点下串行依赖更短”；
2. 不替 HA 虚构实现，也不主动把其所有未知项设为最佳；
3. 将 OurCC 复杂的 Clear 解释为可验证性和可靠性能力，而非无意义开销；
4. 用 lossless profile 对齐可靠性假设，避免 `fault-tolerant vs lossless` 的能力不对称；
5. 将 strict `<` 的剩余问题压缩为少量可回答参数。

### 8.2 最有利且合理的主指标

优先争取 `T_visible` 作为合同“同步时延”的主指标，理由：

1. 它最接近 load/completed store 对 requester 的实际可见完成；
2. 不要求所有实现采用相同 metadata commit 微架构；
3. 可以公平容纳 background commit，但要求旧冲突权限已不可使用；
4. 同时附报 `T_commit/T_next`，避免被质疑后台化成本被隐藏。

不能把终点前移到“第一份数据到达”，因为数据可能尚无合法权限；也不能只对 HA 使用
requester-visible、对 OurCC 使用 commit-complete。

### 8.3 对我们不利事实的处理

| 不利事实 | 推荐处理 | 禁止处理 |
|---|---|---|
| Current 等 ClearResp | 主动披露为可靠 profile 成本，并给 lossless-oneway 关闭方案 | 声称当前已后台化 Clear |
| 2N 下 C4 不可达 | 从目标 3 主论据移除，保留为多节点扩展能力 | 继续宣称 2N direct-forward 胜出 |
| HA lookup 近零 | 明确接受，重点比较网络 dependency 和 completion semantics | 暗示 HA lookup 很慢 |
| HA 可能 WT | 建立最有利 HA 读路径，避免 strawman | 默认 HA 必须 dirty recall |
| HA 可能 true direct | 列为风险分支并请求确认 authority | 把 direct data 偷换成不完整响应 |
| 同 K 不满足 `<` | 明确需要 P 项或 operation mix 证据 | 把“同阶”写成“达标” |

### 8.4 可作为 OurCC 优势但不能直接替代时延证明的点

- reserve-then-commit 的明确安全窗口；
- Recall/Invalidate barrier 的显式完成；
- epoch/reqId stale rejection；
- tombstone duplicate replay；
- same-PA serialization；
- drop/dup/reorder 故障模型；
- 显式 owner/sharer metadata；
- 可拆分的 visible/commit/next trace points。

这些是可审计性、鲁棒性和工程确定性优势。只有映射到 `K/P/w` 后，才能进入目标 3 时延公式。

---

## 9. 推荐的 `lossless-oneway` 方案

### 9.1 推荐结论

推荐把 `lossless-oneway` 实现为目标 3 专用、显式配置的可选 profile；默认仍保留 current
`clear-ack`。不推荐近期采用 pure eager no-Clear。

### 9.2 安全时序

```text
Grant/Data received by requester protocol agent
-> deliver to local HN/cache
-> local install/permission completion
-> send Clear(epoch, reqId, installed=true) one-way
-> requester root complete without waiting for ClearResp
-> Home receives Clear
-> commit intended directory state
-> retire waiter/tombstone/outstanding
-> release next same-line conflict
```

### 9.3 必须满足的条件

1. Clear 在真实 local install completion 后发出，不能只依据 EP bookkeeping；
2. Home 在收到 Clear 前保持 `WAITING_CLEAR`，不放行同址 next conflict；
3. Grant 前的 Recall/Invalidate barrier 保持不变；
4. transport 为 lossless 且 eventual-delivery；
5. duplicate Clear 继续由 tombstone 幂等处理；
6. requester 不等待 ClearResp；
7. Home 可不发送 ClearResp，或仅发送异步诊断 Ack；
8. lossless profile 禁止启用 Clear drop fault injection；
9. 若无法得到 local install callback，必须增加 pending-grant Recall buffer/NACK，而不能提前 commit。

### 9.4 最小工程变更

| 组件 | 最小变更 |
|---|---|
| 配置 | 新增 `clear_profile=ack|lossless_oneway`，默认 `ack` |
| EPBackend | 拆分 issue Clear 与 wait ClearAck；增加 Grant 生命周期状态 |
| EPSNF/HN 接口 | 增加 local install/transaction retirement callback |
| UBAdapter | 增加 one-way Clear 发送接口；oneway 不进入 `_inflightClearReqs` 等待 |
| Home UBCC | 保留 `processClear()` 的 commit、retirement、tombstone、replay；可不发同步 ClearResp |
| Trace | 增加 `grant_received/local_install_complete/clear_sent/home_commit/next_release` |
| TLA+ | 增加 2-node、Grant-in-flight、install、one-way Clear、early Recall 模型 |

建议 requester 状态至少拆为：

```text
GRANT_RECEIVED
DATA_SENT_TO_HN
LOCAL_INSTALLED
CLEAR_SENT
```

### 9.5 风险与缓解

| 风险 | 严重度 | 缓解 |
|---|:---:|---|
| Clear 在 install 前发出 | 高 | 使用真实 local completion callback；形式化检查 Recall-before-install |
| lossless 假设外 Clear 永不到达 | 高 | 仅在显式 lossless profile 启用；默认回退 ack |
| requester/节点故障 | 高 | 本阶段声明不覆盖节点故障；不能声称 fail-stop recovery |
| duplicate Clear | 低 | 复用 tombstone |
| non-FIFO reorder | 中 | epoch/reqId + Home WAITING_CLEAR + same-line queue |
| profile 混用 | 中 | fault injection 启用时强制 ack profile |
| 实际收益被 local CHI/queue 掩盖 | 中 | 同时报 DAG 理论值和 target-visible 实测 |

### 9.6 验证计划

形式验证至少覆盖：

- `NoTwoWriters`
- `NoReadBeforeLatest`
- `CommitOnlyAfterInstallClear`
- `RecallOnlyAfterInstallOrBuffered`
- `NextConflictOnlyAfterCommit`
- `OneWayEventuallyCommits`
- `NoDoubleCommit`
- `EpochMonotonic`

E2E 至少覆盖：

- Home-latest Remote Read；
- dirty-owner Remote Read；
- shared→writer；
- dirty ownership handoff；
- same-PA back-to-back conflict；
- ping-pong；
- Grant/Clear/Recall 人工 reorder；
- duplicate Clear；
- completed store + barrier + readback；
- current ack 与 oneway A/B。

性能结果按 root type 报告：samples、mean、P50、P95、P99、max、run-level CV，并同时给
issue→Grant、issue→local install、issue→Home commit、issue→root complete、next-conflict stall。

### 9.7 回退

- 默认 `clear_profile=ack`；
- oneway 仅在显式 lossless transport 下开放；
- fault injection、节点恢复或未知 transport 自动拒绝 oneway；
- 不删除 ClearResp 代码；
- 保留相同 epoch/reqId/tombstone schema；
- 出现 install ordering 风险时单参数回退 current path。

---

## 10. 完整交付包

### 10.1 必交材料

| ID | 交付件 | 内容 | 关闭标准 |
|---|---|---|---|
| D3-01 | 本正文 | 公平边界、分支模型、结论矩阵 | 评审冻结版本 |
| D3-02 | HA 假设台账 | HK/HU 全量参数、来源、状态、影响 | 所有关键未知有 owner 和关闭方式 |
| D3-03 | HA 抽象参数确认单 | 不要求私有实现，只确认完成语义 | HU-01 至 HU-09 有书面答复或保持 unknown |
| D3-04 | 操作 DAG 附件 | 三类操作 × HA/OurCC profiles | 标出 revoke/data/authority/install/commit/next |
| D3-05 | 参数化计算表 | `K_logical/K_crossnode/tau/P/w` | 可自动计算区间和 break-even |
| D3-06 | OurCC current 证据索引 | 代码、TLA+、E2E、trace | 每个主张有证据等级 |
| D3-07 | Lossless profile 设计 | 状态机、接口、风险、回退 | 设计评审通过 |
| D3-08 | Lossless profile 验证报告 | TLA+、E2E、reorder、A/B | safety/liveness 和性能门槛通过 |
| D3-09 | 双方 target-visible 结果 | 相同 workload 和 root counter | 多轮可复现 manifest |
| D3-10 | 评审问答与限制清单 | 常见质疑、标准回答、禁止表述 | 交付前内部演练 |

### 10.2 参数化计算表字段

建议 CSV/XLSX 至少包含：

```text
operation_class
operation_weight
implementation_profile
ha_branch
requester_home_owner_placement
logical_legs
physical_crossnode_traversals
tau
directory_lookup
peer_service
data_service
local_install
metadata_commit
queue_retry
t_visible
t_commit
t_next
source
confidence
evidence_level
ourcc_minus_ha
break_even_p
```

### 10.3 证据等级

| 等级 | 含义 | 可支持的主张 |
|---|---|---|
| E0 | 假设、口头输入或未确认参数 | 只能形成分支 |
| E1 | 静态代码/文档证据 | 证明实现路径存在 |
| E2 | 形式模型 | 证明模型边界内 safety/liveness |
| E3 | E2E simulation | 证明指定配置下功能和仿真时延 |
| E4 | 单方 target 实机 | 支持该实现 target-visible 数据 |
| E5 | 双方可复现实测 | 支持最终跨实现验收 |

理论 profile 必须标 `proposed/unimplemented`，不得因有公式就提升为 E3/E4。

### 10.4 HA 最小问题集

外部研究已将最小问题扩展并按敏感度排序为 15 题，完整可填表版本见
`docs/research/customer_ha_questions_20260806_zh.md`。Q1-Q5 是最短决策集：

1. HA 是 write-through、write-back 还是 hybrid？
2. peer 是否能直接向 requester 返回数据？
3. direct response 是 data-only，还是同时携带完整 permission/authority？
4. 远端 invalidate/recall 在什么事件后视为 completed？
5. metadata 在 peer completion 前、后，还是 requester install 后 commit？
6. requester root_complete 是否等待 metadata commit？
7. write-back 下 HA 如何确定 latest/dirty data 所在位置？
8. Grant 在途时，HA 如何阻止下一同址 Recall 越过 Grant？
9. HA/IODie 与两个节点的物理位置和 fabric 边界是什么？
10. completed store 是否包含 fence/DSB 或等价完成语义？

其余问题覆盖 route profile、2-bit 码字/transient、contention/retry、non-FIFO identity 和
共同 workload weights。Q1-Q5 未关闭时整体保持 `UNPROVEN`。

### 10.5 目标关闭门槛

目标 3 只能在以下条件满足后关闭：

1. 共同功能域和主完成点书面冻结；
2. HA 关键未知参数被回答，或甲方接受按 unknown 区间判定；
3. operation mix 和平均值分母冻结；
4. logical/physical K 分开计算；
5. 双方使用相同 target-visible root counter；
6. baseline/HA 与 OurCC 使用同输入/seed 的 paired runs；
7. 至少 3 个独立 run 仅作 smoke minimum，按预注册 CI half-width/pass margin 增加样本；
8. 给出 mean、P50、P95、P99、max、CV 和 paired delta；
9. 主判据为 `delta=T_mean_HA-T_mean_OurCC` 的预注册 95% 单侧置信下界严格大于 0；
10. strict `<` 有公式和数据闭环，而非“同阶”替代；
11. proposed profile 若进入正式结果，必须先完成实现和验证；
12. 所有限制和不适用分支进入最终报告。

---

## 11. 评审问答

### Q1：为什么不能直接用 OurCC Outer latency 对比 HA？

Outer latency 是内部协议诊断。仓库现有汇总明确标注 `guest_visible=false` 和
`cross_platform_comparable=false`。正式对比必须使用双方 target-visible root issue→root complete。

### Q2：Clear 是不是 requester cache install Ack？

当前不是严格意义的 HN/L2 install Ack。它表示 EP 协议代理已接受 Grant，并触发 Home commit；当前
代码在 Clear accepted 后才让 EP-SNF 返回最终 CompData。拟议 oneway 必须新增真实 install completion。

### Q3：既然网络不丢包，为什么不直接删除 Clear？

无丢包不等于 FIFO，也不等于新 owner 已安装 Grant。Home eager commit 并放行下一同址请求时，Recall
可能先于 Grant 到达新 owner。必须有 install guard、buffer/NACK 或 ordering guarantee。

### Q4：为什么 2 节点下不使用 C4 Direct-Forward 证明领先？

当前 C4 条件要求 requester、owner、home 三者 node ID 互异，2 节点不可满足；且 C4 direct data 的
`reqId=0` 不携带正式 Grant metadata，permission path 仍经 Home。

### Q5：HA 只有 2-bit，是否一定不知道 dirty owner？

不能这样断言。write-through 下 memory 可始终 latest；write-back 下可能查询节点内 HN 状态或探测远端。
2-bit VI/presence 本身不等于 dirty-owner metadata，但完整 HA 仍可能通过其他机制解决。

### Q6：HA 没有显式 InvalidateAck 是否就更快？

不一定。必须存在等价的 completed snoop/fabric completion，证明旧权限已不可使用。没有消息名称不能
等同于没有串行完成依赖。

### Q7：弱内存序是否允许 Recall 越过 Grant？

网络可以非 FIFO，但协议必须正确处理同一 Cacheline 的因果关系。ARM/RISC-V 弱序允许独立事务乱序，
不允许双 writer、错误 read-from 或破坏 acquire/release/barrier。

### Q8：为什么 lossless-oneway 仍然不是自动 PASS？

它主要删除 current 的 ClearResp 等待，使常见 central-return 路径达到与 HA 同 K。合同要求严格 `<`，
同 K 还需证明 `P_OurCC < P_HA` 或通过 operation mix 得到严格负的平均差值。

### Q9：如果 HA 支持 true peer-direct authority 怎么办？

该分支下 HA 可能少一个 leg，OurCC 不应强行宣称领先。可选择：接受条件结论；开发真正 delegated
authority forward；或从甲方确认其 direct response 实际只是 data-only。

### Q10：现有 TLA+ 是否证明目标 3？

否。它证明抽象协议状态机的部分 safety/liveness，不建模真实 CHI install、物理时延、完整 memory model，
也不是目标 3 的性能证明。

### Q11：retained local hit 和 Silent Upgrade 能否降低跨节点平均值？

若 root issue 时没有跨节点 dependency，它们不应只在 OurCC 一侧计入“跨节点同步平均值”。它们可作为
整体 workload 性能或目标 2 的优化，但目标 3 的跨节点分母必须双方一致。

### Q12：怎样在不披露 HA 私有实现的情况下完成验收？

甲方只需确认 data/permission routing、completion、commit、same-line guard、write policy 和拓扑等抽象
语义，并提供脱敏 root/event timestamps。无需提供 RTL、内部状态编码或私有模块名称。

---

## 12. 风险登记

| ID | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R-01 | 把 current Clear 后台化 | 结论被代码反例推翻 | 明确 current 等 ClearResp；oneway 单列 |
| R-02 | 把 logical arrows 当物理 hops | 2 节点 K 严重失真 | 双列 logical/physical，冻结 placement |
| R-03 | 把 direct data 当完整权限 | 错判 3-leg | DAG 中分离 data path 与 authority path |
| R-04 | 默认 HA write-back | 对 HA 构成 strawman | 同时计算 WT/WB/hybrid |
| R-05 | 默认 HA 无 direct | 错误乐观 | 保留 unknown/direct 分支 |
| R-06 | 把同 K 写成严格快 | 不满足合同 `<` | 必须关闭 P 和权重 |
| R-07 | 用 Outer trace 代替 root counter | 跨平台不可比 | 只作 E1/E3 拆链诊断 |
| R-08 | oneway 在 install 前发 Clear | correctness failure | local install callback + TLA+ |
| R-09 | eager commit 无 guard | Recall-before-Grant | 不纳入合法下界 |
| R-10 | Invalidate callback 异常路径过早 Ack | 旧权限可能未完成 | 复核当前 EPRNF/FV8 异常路径并补 E2E |
| R-11 | TLA+ 覆盖表述过大 | 评审质疑 fidelity | 明确模型不含真实时延和完整 memory model |
| R-12 | operation mix 分母不一致 | 平均值可被人为操纵 | 冻结 root 分类和权重 |

---

## 13. 证据索引

| 主题 | 文件与位置 | 证据等级 |
|---|---|:---:|
| 正式 root 计时边界 | `docs/delivery/ha_comparison_request_chains_20260731_zh.md:19-56` | E1 |
| ReadResp/Clear 工具分链 | `docs/delivery/ha_comparison_request_chains_20260731_zh.md:381-406` | E1 |
| 协议总体与 Clear 生命周期 | `docs/protocol_design.md:128-198,203-269,324-442` | E1 |
| Requester 等 ClearResp | `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc:483-515,782-851,2231-2307` | E1 |
| EP-SNF 成功后返回 CompData | `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc:105-155,275-300` | E1 |
| RecallResp 后创建 Grant | `modules/ubiomodule/UBCCController.cc:2248-2475` | E1 |
| Clear commit 和 replay | `modules/ubiomodule/UBCCController.cc:3573-3788` | E1 |
| C4 条件与 data-only reqId | `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc:1242-1279,1325-1361` | E1 |
| C4 默认关闭 | `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.py:28-29` | E1 |
| Clear/Eager 历史分析 | `docs/measure/tc98_optimization_analysis.md:63-127,143-190` | E0/E1 |
| 当前可靠性与 HA 边界 | `docs/design/cc_ep_deliverable2_verification_reliability_ha.md:40-105` | E1/E2 |
| 核心 reserve-then-commit 模型 | `verification/tla/ubcc_protocol_core.tla:123-424` | E2 |
| transport fault 模型 | `verification/tla/ubcc_transport_faults.tla` | E2 |
| Invalidate barrier 静态核查 | `verification/wave1/fv8_invalidate_barrier_report.md` | E1/E2 |
| 外部目标 3 主报告 | `docs/research/ourcc_vs_customer_ha_external_research_report_20260806_zh.md` | external research |
| 外部来源矩阵 | `docs/research/ha_coherence_source_matrix_20260806.tsv` | external source ledger |
| HA/OurCC DAG 账本 | `docs/research/ha_ourcc_operation_dags_20260806.md` | E0/E1 conditional model |
| ARM/RISC-V litmus 规格 | `docs/research/arm_riscv_coherence_litmus_plan_20260806_zh.md` | specification, unrun |

行号随代码演进可能漂移，最终交付前应由脚本重新生成 symbol-based evidence manifest。

---

## 14. 名词解释表

### 14.1 系统、协议和角色

| 名词 | 中文解释与关键概念 | 与目标 3 的关系 | 代码/文档位置 |
|---|---|---|---|
| OurCC / CC-EP | 我方跨节点缓存一致性端点方案 | 目标 3 的我方比较对象 | `docs/protocol_design.md` |
| HA | 甲方 Hardware/Home Agent 方案 | 目标 3 的理论基线 | 本文 HK/HU 台账 |
| CC / Cache Coherence | 缓存一致性，保证多缓存副本对同一地址的合法可见性 | 合同“跨节点 CC 同步”的核心 | 全仓库协议文档 |
| Cacheline | 缓存行，协议追踪和传输的基本粒度，当前通常为 64B | 所有完成和序列化均按 line/PA 定义 | `protocol/CoherenceMessage.hh` |
| Global Address Space | 跨节点统一访问的地址空间 | HA 已知覆盖不超过 128 MiB | 合同输入 |
| VI | Valid/Invalid 两态协议 | HA 节点级 metadata 状态，不直接表达 dirty owner | 本文 HK-03/HK-04 |
| MESI | Modified/Exclusive/Shared/Invalid 四态协议 | OurCC 全局状态能显式表达 owner/dirty/shared | `verification/tla/ubcc_protocol_core.tla:41` |
| G_I/G_S/G_E/G_M | OurCC Home committed global states | 决定 Recall、Invalidate 和 Grant 路径 | `docs/protocol_design.md` |
| R_I/R_WAIT_GRANT/R_S/R_E/R_M | requester 侧协议书签状态 | 当前 Grant/Clear retry 和 silent upgrade 的依据 | `gem5/.../EPBackend.hh` |
| Directory | 按 Cacheline 记录 sharer/owner/state 的目录 | lookup、commit 和序列化属于 P 项 | `modules/ubiomodule/ResidentDir.*` |
| Presence Bit | 指示唯一远端节点是否可能缓存该行的位 | HA 能否省 probe 的关键，但不等于 dirty 位 | 本文 HK-04 |
| Sharer | 持有共享只读副本的节点 | Writer Acquire 前必须失效冲突 sharer | `DirEntry.sharersMask` |
| Owner | 持有 Exclusive/Modified 权限的节点 | Remote Read/Handoff 的数据和权限来源 | `UBCCController.cc` |
| Dirty Owner | 持有唯一最新、尚未写回数据的 owner | 决定是否必须 remote Recall | `UBCCController.cc:2297-2359` |
| Latest Data | 按 coherence order 最新且合法的数据版本 | 目标完成前必须选中正确来源 | Recall/data path |
| Requester | 发起 load/store/permission 请求的节点或代理 | 主计时起点和终点角色 | `EPBackend.cc` |
| Home | 某 Cacheline 的全局目录和序列化归属点 | 决定 K、commit 和 next-conflict | `UBCCController.cc` |
| Peer | 远端 owner 或 sharer 节点 | 可能提供 data、release 或 Ack | 本文 HA DAG |
| HN / HN-F | Arm CHI Home Node / Fully Coherent Home Node | requester 公共前缀和节点内一致性管理 | `docs/protocol_design.md` |
| CHI | Arm Coherent Hub Interface | OurCC 节点内缓存一致性协议 | `gem5/src/mem/ruby/protocol/chi/` |
| RN-F / EP-RNF | Fully Coherent Request Node；OurCC 中代理外部世界的 RN | 执行 local Recall/Invalidate 相关 CHI 事务 | `EPRNFController.cc` |
| SN-F / EP-SNF | Slave Node；OurCC 中承接 HN ReadNoSnp 的外部端点 | 在 outer 成功后向 HN/L2 返回 CompData | `EPSNFController.cc` |
| UBCCController | OurCC Home 全局目录控制器 | 负责 barrier、Grant、Clear commit | `modules/ubiomodule/UBCCController.cc` |
| EPBackend | requester/owner 侧 outer protocol backend | 处理 ReadReq、Grant、Recall、Clear | `gem5/.../EPBackend.cc` |
| UBAdapter | gem5 与 ubio/network message path 适配器 | 承载跨节点请求和 response | `gem5/.../UBAdapter.cc` |
| IODie | I/O Die，可能承载集中 HA/metadata | 决定 HA 物理 placement 和 K_crossnode | 本文 HU-09 |

### 14.2 消息、权限和状态机

| 名词 | 中文解释与关键概念 | 与目标 3 的关系 | 代码/文档位置 |
|---|---|---|---|
| Remote Read | requester 跨节点获取数据/Shared 权限 | 三类主操作之一 | 本文 6.1 |
| Writer Acquire | requester 获取 Exclusive/Modified 写权限 | 需要完成远端 invalidate | 本文 6.2 |
| Ownership Handoff | 旧 owner 向新 owner 转移数据和写权限 | 常见 dirty-owner 关键路径 | 本文 6.3 |
| Recall | 回收或降级旧 owner 权限，并可取得数据 | Grant 前的安全 barrier | `EPBackend.cc:1114-1409` |
| RecallReq/RecallResp | Recall 请求/完成响应 | owner release 和 latest data 返回路径 | `CoherenceMessage.hh`, `UBCCController.cc` |
| Invalidate | 使其他 sharer 副本失效 | Writer Acquire 的前置条件 | `docs/protocol_design.md:484-522` |
| InvalidateAck | 远端完成失效后的确认 | 决定合法 Grant 的最早时刻 | `UBCCController.cc` |
| Grant | Home 或被授权 peer 授予数据/权限 | data 到达不等于完整 Grant authority | `EPBackend::handleGrant` |
| Grant Authority | 有权建立新 Shared/Exclusive/Modified 状态的授权凭证 | 区分 data-only direct 与 true direct | 本文 4.2 |
| Push Grant | Home 主动向 requester 推送 Grant | 消除 requester pull retry 等待 | `UBCCController.cc:2442-2465` |
| Pull Fallback | Push 失败后 requester 通过重试获得 Grant | 影响 P_queue，不是正常最短路径 | 同上 |
| Direct-Forward | owner 绕过 Home 向 requester 直发数据 | 当前 C4 只优化 data path | `EPBackend.cc:1242-1361` |
| Central-return | peer response 先回 Home，再由 Home 返回 requester | HA 常见 4-leg 分支 | 本文 4.2 |
| Peer-direct | peer 直接向 requester 返回数据或完成 | 是否携带 authority 决定 3-leg 是否成立 | 本文 4.2 |
| ReadShared | CHI 获取共享权限 | read Recall 的本地 CHI 操作 | `EPRNFController.cc` |
| ReadUnique | CHI 获取唯一权限 | write Recall/Writer Acquire 的本地操作 | `EPRNFController.cc` |
| ReadNoSnp | CHI 无 snoop read 请求 | EP-SNF 的外层入口 | `EPSNFController.cc:226-284` |
| CompData_SC/UC | CHI Shared/Unique completion data | HN/L2 requester-visible 数据返回 | `EPSNFController.cc:122-145` |
| SnpCleanInvalid | CHI clean+invalidate snoop | 远端旧权限撤销的节点内动作 | `EPRNFController.cc` |
| CleanUnique | 获取唯一 clean 权限的 CHI 操作 | Invalidate completion 语义的一部分 | `EPRNFController.cc` |
| Comp_UC | Unique completion | 证明本地 unique/invalidate 动作完成的响应之一 | `verification/wave1/fv8_invalidate_barrier_report.md` |
| CompAck | Completion acknowledgement | CHI 内部完成确认，不能与 outer Clear 混同 | 同上 |
| ClearReq | requester 请求 Home 提交 intended Grant | current commit critical path | `EPBackend::sendClear`, `UBCCController::processClear` |
| ClearResp / ClearAck | Home 返回 Clear accepted/rejected/pending | current requester-visible 等待项 | `UBAdapter.cc`, `EPBackend.cc:2231-2307` |
| GRANT_HANDSHAKE | Home 已准备 Grant、等待 Clear commit 的 outstanding 类型 | current 两阶段状态 | `UBCCController.hh` |
| WAITING_CLEAR | 等待匹配 Clear 的 stage | 同址请求继续被阻塞 | `UBCCController.hh:178` |
| Outstanding Request | 每 PA 的进行中协议事务记录 | same-line serialization 和 retry 的基础 | `UBCCController.hh` |
| Tombstone | 已完成事务的有限期幂等记录 | duplicate Clear 不重复 commit | `UBCCController.cc:3593-3611` |
| Epoch | 每行事务代际/版本号 | 拒绝 stale、证明 commit 顺序 | `ubcc_protocol_core.tla` |
| reqId | requester 分配的事务唯一标识 | 区分同 epoch 请求和 response | `CoherenceMessage.hh` |
| Stale Message | 属于旧 epoch/reqId 的延迟或重复消息 | 非 FIFO 下必须安全拒绝 | `processClear`, `processRecallResponse` |
| Idempotence | 重复执行不改变最终结果 | duplicate 消息安全性的核心 | tombstone/ack bitmask |
| Reserve-then-Commit | 先记录 intended state，后在完成事件提交 | OurCC current 的安全窗口 | `ubcc_protocol_core.tla:123-188,400-411` |
| Eager Commit | Grant 发出前后立即提交 metadata | 可缩短 commit，但需 install guard | 本文 6.4 |
| Per-line Serialization | 同一 Cacheline 的冲突事务按序处理 | 决定 T_next 和安全性 | `_outstandingReqs`, pending queues |

### 14.3 计时、模型和统计

| 名词 | 中文解释与关键概念 | 与目标 3 的关系 | 代码/文档位置 |
|---|---|---|---|
| Root Operation | workload 中一次 load、completed store 或约定 batch | 正式统计样本单位 | `docs/delivery/ha_comparison_request_chains_20260731_zh.md` |
| Root Issue/Complete | root operation 的开始/完成事件 | 正式计时边界 | 同上 `:21-31` |
| Requester-visible Completion | requester 安全获得数据/权限的时刻 | 推荐目标 3 主终点 | 本文 2.3.1 |
| Home/Global Commit | metadata 正式提交的时刻 | 附报完成点 | 本文 2.3.2 |
| Next-conflict Release | 下一同址冲突可继续的时刻 | 揭示后台 commit 对连续竞争的影响 | 本文 2.3.3 |
| Critical Path | DAG 中最长串行因果链 | 理论时延的一阶决定项 | 本文 3 |
| DAG | Directed Acyclic Graph，有向无环依赖图 | 正确处理并行 data/permission 路径 | 本文 6 |
| Leg | 关键路径中的单向依赖段 | `K` 的计数单位，不等于消息总数 | 本文 3.2 |
| Logical Leg | 逻辑角色间依赖段 | 可能是本地或跨节点 | 本文 3.2 |
| Physical Cross-node Traversal | 真正跨物理节点/fabric 边界的传输 | 决定网络主导项 | 本文 3.2 |
| Fanout | Home 并行向多个 sharer 发消息 | 消息数可增加但 K 不一定增加 | Invalidate path |
| `tau` / `τ` | 归一化单向 fabric leg latency | 网络主导模型参数 | 本文 3.1 |
| `K` | 串行 critical-path legs 数 | 一阶比较项 | 本文 3.1 |
| `P` | 本地处理、数据服务、install、commit、queue 项 | 同 K 时决定严格大小 | 本文 3.1 |
| Break-even | 双方时延相等的参数条件 | 判断条件 PASS | 本文 3.5 |
| Weighted Average | 按 operation mix 加权的平均时延 | 合同“平均时延”的数学定义 | 本文 3.4 |
| Operation Mix | 各类 root operation 的权重分布 | 未冻结时无法证明平均 `<` | 本文 3.4 |
| Mean/P50/P95/P99/Max | 均值和延迟分位数/最大值 | 正式结果的统计字段 | 本文 10.5 |
| CV | Coefficient of Variation，变异系数 | 判断多轮结果稳定性 | 本文 9.6 |
| Guest-visible / Target-visible | 目标系统可见计时器测得的完成时间 | 跨实现正式比较数据 | delivery 性能文档 |
| Outer Diagnostic | OurCC outer protocol 内部计时 | 只能拆链，不能替代 root latency | `summarize_2n1s_protocol.py` |
| PDES | Parallel Discrete Event Simulation，并行离散事件仿真 | 影响 wall-clock/仿真对齐，不是硬件协议时延 | `docs/measure/tc98_optimization_analysis.md` |

### 14.4 写策略、可靠性和内存序

| 名词 | 中文解释与关键概念 | 与目标 3 的关系 | 代码/文档位置 |
|---|---|---|---|
| Write-through / WT | 每次写同时更新 Home memory | 可让 Home memory 始终 latest，读路径更短 | 本文 4.5 |
| Write-back / WB | dirty data 可只保留在 cache，延后写回 | 需要定位 remote dirty owner | 本文 4.5 |
| Hybrid Write Policy | 不同 region/操作采用不同写策略 | 平均值需分类型加权 | 本文 4.5 |
| Lossless Transport | 假设消息不丢失 | HA 已知条件和 oneway 前提 | 本文 HK-11 |
| Eventual Delivery | 已发送消息最终到达 | oneway Home 最终 commit 的活性前提 | 本文 9.3 |
| FIFO | First-In-First-Out，按发送顺序到达 | HA 明确不保证全局 FIFO | 本文 HK-12 |
| Non-FIFO | 消息可能乱序到达 | 触发 Recall-before-Grant 风险 | 本文 6.4 |
| Drop/Duplicate/Reorder | 丢包、重复、乱序 | OurCC fault profile 的验证对象 | `ubcc_transport_faults.tla` |
| Weak Ordering | 弱内存序，允许部分独立访问乱序 | 不能削弱单地址 coherence | 本文 2.1 |
| OoO | Out-of-Order，处理器乱序执行 | 与网络 non-FIFO 是不同概念 | reliability 文档 |
| Acquire/Release | 获取/释放内存序语义 | 决定跨地址可见顺序，不替代 Clear commit | memory-model 语义 |
| Barrier/Fence/DSB | 内存屏障/完成屏障 | completed store 的终点可能依赖它 | 本文 4.7 |
| Posted Write | 先接受请求、稍后全局完成的写 | 不能冒充 completed store | 本文 4.7 |
| Safety | 不出现双 writer、错误数据、非法共享 | 所有理论下界的前提 | TLA+ models |
| Liveness | 已接受事务最终完成 | lossless eventual delivery 的目标 | TLA+ models |
| NoDoubleCommit | 同一事务不能重复提交 | tombstone/reqId 的验证性质 | `ubcc_protocol_core.tla` |
| EpochMonotonic | committed epoch 单调前进 | 防 stale 覆盖新状态 | `ubcc_protocol_core.tla` |
| TBE | Transaction Buffer Entry，HN/CHI 事务资源 | 资源竞争进入 P_queue | CHI implementation |
| Backpressure | 资源或队列不足导致请求暂停 | 影响尾延迟和 contention | EPSNF/UBCC retry |
| BUSY/Retry | 暂不能服务并要求重试 | 进入 P_queue，不应误作固定 K | `EPBackend.cc`, `UBCCController.cc` |

### 14.5 Profile、风险机制和结论术语

| 名词 | 中文解释与关键概念 | 与目标 3 的关系 | 代码/文档位置 |
|---|---|---|---|
| Clear-ack Profile | 当前同步 ClearReq/ClearResp profile | 已交付、保守比较对象 | Current code |
| Lossless-oneway Profile | requester install 后单向 Clear、不等 Ack | 推荐的目标 3 对齐 profile，尚未实现 | 本文 9 |
| Eager no-Clear Profile | 不等待 requester completion 即 commit | 理论探索，非 FIFO 下有高风险 | 本文 6.4 |
| Theoretical Profile | 由协议假设和公式定义、未必已有代码 | 不得冒充 Delivered Implementation | 本文 1.2 |
| Delivered Implementation | 已落地并验证的代码 profile | 当前仅 clear-ack 可这样表述 | Current code |
| Recall-before-Grant | 后一 Recall 先于前一 Grant 到达 | eager commit 的核心反例 | 本文 6.4 |
| Pending-install Guard | Grant 未安装前阻止下一同址冲突 | 支持安全 eager/oneway 的候选机制 | Proposed |
| Install Acknowledgement | requester HN/cache 已安装数据/权限的确认 | oneway Clear 的正确触发点 | Proposed |
| Data Critical Path | 最新数据到 requester 的最长依赖链 | 可能短于权限路径 | 本文 3.3 |
| Permission Critical Path | requester 获得合法权限的最长依赖链 | root completion 通常不能早于它 | 本文 3.3 |
| STRICT PASS | 无附加未关闭条件的严格通过 | 合同最终目标 | 本文 3.6 |
| CONDITIONAL PASS | 指定参数分支下通过 | 当前最现实结论形式 | 本文 3.6 |
| TIE | 理论相等 | 不满足严格 `<` | 本文 3.6 |
| UNPROVEN | 证据或参数不足 | 当前总体状态 | 本文 0.1 |
| RISK/FAIL | 已知分支下不满足或安全边界非法 | 必须披露 | 本文 3.6 |

---

## 15. 最终建议与行动顺序

1. 立即冻结本文的共同功能域和三完成点定义。
2. 使用 D3-03 向甲方关闭 HU-01 至 HU-09，不要求其披露私有实现。
3. 从所有 2 节点目标 3 主材料中删除“当前 C4 使 OurCC 少一跳”的论据。
4. 明确 current requester-visible 路径等待 ClearResp，不再使用 ReadResp-only trace 作为正式结果。
5. 建立 HA `[未知/A/B/扩展]` 参数化计算表，先给区间和 break-even，不急于给单值。
6. 设计并实现安全的 `lossless-oneway`，重点先解决真实 local install completion。
7. 新增 2-node、non-FIFO、Grant/Install/Clear/Recall 专用 TLA+ 模型。
8. 用 Remote Read、Writer Acquire、Ownership Handoff 三类 root 覆盖主要协议路径。
9. 只以 target-visible counter 关闭最终平均时延，Outer trace 作为 DAG 证据附件。
10. 若 HA true peer-direct authority 被确认，及时将结论调整为条件通过/未证明，并评估 delegated authority
    forward，而不是继续使用无法成立的严格领先表述。

在完成上述关闭前，正式对外结论建议保持：

> **OurCC 已提供可审计的跨节点一致性安全完成语义；当前 clear-ack profile 的目标 3 严格领先尚未
> 证明。对甲方给定的 lossless transport，可通过 lossless-oneway profile 消除 requester 对
> ClearResp 的前台等待，使常见 central-return 分支达到与 HA 同阶的网络关键路径。最终严格小于关系
> 由 HA 抽象完成语义、物理 placement、本地 P 项和统一 operation mix 共同关闭。**
