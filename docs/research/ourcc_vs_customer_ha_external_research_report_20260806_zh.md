# OurCC/CC-EP 与甲方 HA：合同目标 3 外部研究与验收分析

**报告日期：** 2026-08-06  
**公开资料检索截止：** 2026-08-06  
**判定对象：** `OurCC 跨节点 Cache Coherence 同步平均时延 < 甲方 HA 理论平均时延`  
**总体判定：** `UNPROVEN（存在实质性 RISK）`

> 本报告是条件化架构分析，不是双方实测结果。除项目事实外，甲方私有参数均不作
> 猜测；没有可靠公开数值的时延项保留为符号，不编造 ns。
>
> **内部事实校正：** 外部任务书曾简写为“requester 安装后发送 Clear”。当前代码审阅显示，
> Clear 只确认 EP 协议代理接受 Grant，并非 HN/L2 明确 install Ack；EP-SNF 在 Clear accepted
> 后才向本地 HN/L2 返回最终 CompData。本文以该当前代码事实为准。

## 1. 执行摘要

在共同安全功能域和相同完成点下，目前不能证明 OurCC/CC-EP 的跨节点同步平均时延
严格小于甲方 HA。当前 `OurCC-current-clear-ack` 提供了清晰、可审计的两阶段提交语义；
但 requester 协议代理接受 Grant 后还必须发送 `ClearReq`，Home commit/retire 后返回并
等待 `ClearResp accepted`，EP-SNF 才向本地 HN/L2 完成当前 root operation。因此相对于
能在 Home Grant 或已验证 peer completion 时完成全局提交的合法 HA 分支，OurCC 当前
`T_commit`、`T_next` 和 `T_root_current` 均可能多出串行依赖。

最关键未知量：

1. **HU-03：peer response 是否携带完整且可验证的 Grant authority。** 若可以，
   remote-owner 分支可能形成 `requester->HA->owner->requester` 的三段关键链；若只能
   direct data，权限路径仍必须经 HA。
2. **HU-05/HU-06：metadata commit 和 requester root completion 边界。** 数据到达不等于
   可安全使用权限；HA 是否在 Grant 前 commit、是否等待 global commit，决定应比较
   `T_visible` 还是更晚完成点。
3. **HU-09/HU-10：HA/Home placement 与本地 service/queue。** 两节点中三个逻辑角色
   必有共址；相同 `K_logical` 可能对应不同 `K_crossnode`。metadata lookup 近似零不能
   推出完整 HA service 为零。

最可能的条件性优势分支是：甲方 central-return、invalidate explicit completed Ack、root
等待 commit，且 OurCC 的 placement 和本地项满足 break-even。该分支最多支持
`CONDITIONAL PASS`，仍需甲方回答、共同 operation weights 和 paired measurement。

最危险分支是：甲方采用 direct-data+authority，peer/fabric completion 可安全授权，HA
commit 与 requester path 并行。该分支下 OurCC 可能具有更长 `K`，结论为 `RISK/FAIL`，
除非测得足够大的 OurCC 本地项优势。

合同原始严格 `<` 不能以“同阶”“消息更少”或结构性隔离优势代替。若无法获得甲方
抽象参数或可运行 HA，应维持 `UNPROVEN`。

## 2. 研究方法与证据边界

采用“项目事实、外部机制、条件模型、验收证据”四层法：

1. 项目内部实现描述标为 `PROJECT FACT`，不使用外部资料替代代码证据。
2. 规范、标准组织、同行评审论文和厂商官方资料标为 `EXTERNAL FACT`。
3. 使用 `T_visible/T_commit/T_next/T_root_current` 和符号时延比较合法分支。
4. 对甲方私有参数保留 unknown、上下界和敏感度，不选择性采用有利分支。
5. 协议 safety、ISA memory ordering、transport reliability 和 node/RAS 分开验收。

详细来源、章节、URL、authority 和项目影响见：

`docs/research/ha_coherence_source_matrix_20260806.tsv`

公开资料可以证明“合法机制需要哪些 authority/completion/serialization 条件”，不能替
甲方选择其私有实现，也不能给出甲方 `P` 数值。

## 3. 项目事实与外部事实

### 3.1 PROJECT FACT

| ID | 事实 | 对目标 3 的作用 |
|---|---|---|
| PF-01 | 节点内为 ARM CHI domain；UBCC 是独立 native UBIO 全局 HA/目录 | 定义比较对象 |
| PF-02 | Bloom+ResidentDir+DRAM metadata 不占 HN-F TBE | 结构性隔离，不自动等价低时延 |
| PF-03 | UBCC 按 PA 维护 owner/sharer/epoch/reqId/outstanding；duplicate 幂等、不得 double commit | 安全与 replay 边界 |
| PF-04 | current profile 的 Clear 不是 HN/L2 install Ack；等待 ClearResp accepted 后才完成当前 root path | 可能增加 root 串行依赖；`T_visible` 需冻结 install 定义 |
| PF-05 | `OurCC-lossless-oneway` 是 `PROPOSED/UNIMPLEMENTED` | 只能建模，不能计入当前结果 |
| PF-06 | C4 是 direct data，不是完整 authority，且两节点三角色路径不可达 | 目标 3 中 `NOT APPLICABLE` |
| PF-07 | bounded fault qualification 有历史证据 | 能力差异，不污染 lossless baseline |
| PF-08 | crash continuity、永久 partition、完整 ISA model、真正 16N switch 未证明 | 明确范围边界 |

### 3.2 EXTERNAL FACT

| ID | 外部事实 | 来源 | 适用边界 |
|---|---|---|---|
| EF-01 | 目录协议按 block 序列化；stable owner/sharer 与 transient state 分离；invalidate completion 依赖 Ack/completion | Sorin/Hill/Wood [S8] | 教科书协议，不等同甲方实现 |
| EF-02 | CHI Home 协调 transaction；DCT 可分离 direct data 与 Home completion；相关事务可使用 CompAck | Arm CHI [S1][S2] | 不证明甲方采用 CHI |
| EF-03 | CCIX HA 是 Point of Coherency/Serialization；NoCompAck 仍要求 ordering guarantee | CCIX [S5] | 公开协议示例 |
| EF-04 | credit/backpressure、link replay 与 coherence retry 是不同层 | CCIX/CXL [S5][S6] | 具体取决于 transport profile |
| EF-05 | Arm DMB/DSB、Acquire/Release 与内部 fabric ack 不是同一语义层 | Arm ARM/guide [S3][S4] | 需要 endpoint mapping |
| EF-06 | RVWMO/FENCE/aq/rl 定义架构可见顺序 | RISC-V [S11][S12] | 需平台连接到 coherence completion |
| EF-07 | full-map presence 通常每节点一 bit，并另需 dirty/owner；coarse scheme 以额外 traffic 换位数 | [S9][S10] | 解释 2-bit 边界，不规定甲方编码 |
| EF-08 | 有限 TLA+ model 不自动证明参数化任意规模 | [S17][S18] | 限制 formal 外推 |

## 4. 甲方 HA 参数账本

| ID | 当前甲方值 | 极简影响 | 无回答时处理 |
|---|---|---|---|
| HU-01 write policy | `UNKNOWN` | 决定 memory-latest 与 owner path 权重 | 同列 WT/WB/hybrid |
| HU-02 data route | `UNKNOWN` | central/direct | 给分支区间 |
| HU-03 peer authority | `UNKNOWN` | 可把 K=4 降为 K=3 | 保留最有利 HA 合法分支 |
| HU-04 invalidate completion | `UNKNOWN` | writer safety completion | explicit/implicit 均保留，但隐式须可证明 |
| HU-05 metadata commit | `UNKNOWN` | 决定 `T_commit/T_next` | Grant 前至 requester completion 区间 |
| HU-06 root waits commit | `UNKNOWN` | 决定计时终点 | 全报三个完成点和 ISA root |
| HU-07 dirty/latest owner | `UNKNOWN` | 影响 probe/query/data source | exact owner 至 conservative probe 区间 |
| HU-08 same-line serialization | `UNKNOWN` | 防 stale/double owner | 未说明则 direct fast path 不可采用 |
| HU-09 physical placement | `UNKNOWN` | 映射 `K_crossnode` | 枚举 H@R/H@peer/IODie |
| HU-10 HA service/queue | `UNKNOWN` | 同 K 时决定严格快慢 | `P_min>=0`，上界需实测 |
| HU-11 ISA completion | `UNKNOWN` | 防 posted accept 冒充完成 | 取共同 API completion |
| HU-12 contention/retry | `UNKNOWN` | 决定 C 类与尾延迟 | idle 和 contention 分开 |

完整 15 题确认单见：

`docs/research/customer_ha_questions_20260806_zh.md`

## 5. 两节点 VI/2-bit metadata 边界

两节点最自然的 2-bit 编码是 presence vector `[P0,P1]`，可精确表达两个节点是否存在
副本，但不能同时表达 clean/dirty、latest owner 和 in-flight transient。另一种四码字编码
可表达 `I/N0-exclusive/N1-exclusive/Shared`，仍无法区分 clean exclusive 与 dirty modified，
也没有 pending-install/version 状态。

write-back 下仅靠 2 bits 不能普遍确定 dirty/latest owner。合法补充机制包括：额外 dirty/
owner 信息、唯一 owner invariant+HN query、probe、write-through、或把 transient/token 放在
HA 临时表。因此“2-bit lookup 约 0”不等于完整 HA operation service 为 0。

## 6. 统一完成点和理论模型

定义：

- `T_visible`：requester 同时拥有 latest data 和安全 authority。
- `T_commit`：Home/HA authoritative metadata 原子提交。
- `T_next`：下一同址冲突事务可安全开始。
- `T_root_current`：当前 OurCC requester 收到 `ClearResp accepted` 并完成本地 root path。

注意：当前 `T_root_current` 晚于或等于 `T_next`；不能把第一份 data 到达当 `T_visible`，
也不能把 `T_visible` 自动当 ISA completed store/fence。

```text
T_s(o,x) = K_logical_s(o,x) * tau + P_s(o,x)
T_s(o,x) = K_crossnode_s(o,x) * tau + P'_s(o,x)
P = P_dir + P_peer + P_data + P_install + P_commit + P_queue
T_mean_s(x) = sum(w_i * T_s(i,x))
```

严格优势条件：

```text
(K_HA - K_OurCC) * tau > P_OurCC - P_HA
```

- `K_OurCC<K_HA`：有结构性 headroom，但仍需测 P。
- `K_OurCC=K_HA`：只能证明同阶，必须证明 `P_OurCC<P_HA`。
- `K_OurCC>K_HA`：OurCC 必须以 P 优势跨越至少一段 tau；无证据时为 RISK。

## 7. 三类操作结论

详细 Mermaid DAG、placement 和可复制账本见：

`docs/research/ha_ourcc_operation_dags_20260806.md`

| 操作/分支 | 到 `T_visible` 的逻辑链 | 结论影响 |
|---|---|---|
| Home-memory latest | `R->H->R`，K=2 | HA 可很快；OurCC current 后续还有 Clear commit/root |
| remote owner central-return | `R->H->O->H->R`，K=4 | 两方可能同 K，P 决胜 |
| direct-data-only | data 与 `O->H->R Grant` 取 max，通常仍 K=4 | data 早到不等于安全完成 |
| direct-data+authority | `R->H->O->R`，K=3 | 对 OurCC 最危险合法 HA 分支 |
| shared-to-writer explicit Ack | `R->H->S->H->R`，K=4 | OurCC current 后续 Clear 增加 commit/root dependency |
| ownership handoff central | `R->H->O->H->R`，K=4 | dirty data、release、authority 必须同 transaction |

两节点中 central K=4 通常只对应两次真实跨节点 traversal；若 H 与 requester 共址，
OurCC Clear 往返可本地；若 H 与 peer 共址，ClearReq/ClearResp 可再增加两次 traversal。

## 8. 目标 3 结论矩阵

| 合法 HA 分支 | Weighted result | 判定 |
|---|---|---|
| Home-memory latest，Grant 前/同时 commit | 权重未知，HA 可大量 K=2 | `UNPROVEN`，OurCC commit 风险 |
| central-return + explicit Ack + root 等 commit | 常同 K=4；严格结果取决于 P 和权重 | `UNPROVEN`；满足 break-even 时 `CONDITIONAL PASS` |
| direct-data-only + Home Grant | data 早到不缩短 authority path | `UNPROVEN` |
| direct-data+authority + commit 并行 | HA 可 K=3 | `RISK/FAIL` 条件分支 |
| implicit completion 无可验证 ordering | 不满足共同安全域 | `NOT APPLICABLE` |
| HA write-through、memory 常 latest | HA home fast path 增多 | 对 OurCC `RISK` |
| HA write-back、无 exact owner、必须 probe | HA 可能增加 service/leg | 可形成 `CONDITIONAL PASS` |
| Proposed OurCC one-way | 未实现 | 当前 `NOT APPLICABLE` |

**整体结论：`UNPROVEN（存在实质性 RISK）`。** 当前没有冻结参数、共同权重和 P 证据
支持数学上的 `STRICT PASS`。

## 9. Memory-order 与 completion

单地址 coherence ordering 不等于多地址 memory ordering。Arm Acquire/Release、DMB/DSB 和
RISC-V FENCE/aq/rl 都要求平台把 endpoint/coherence completion 映射到 architected event。
内部 `ClearResp accepted`、Grant、cache install、Home commit、store retire 不能在没有平台
合同的情况下互相替代。

最小计划覆盖：Message Passing、release/acquire、DMB/DSB/FENCE、same-line competing
writers、independent-line allowed reordering。完整规格见：

`docs/research/arm_riscv_coherence_litmus_plan_20260806_zh.md`

当前这些 litmus **未运行**，不得写成 PASS。

## 10. Lossless transport 与 fault 分域

| 层 | 机制 | 目标 3 baseline |
|---|---|---|
| flow control | credit/backpressure | 固定 pipeline/冻结负载 queue 可计入 |
| link integrity | CRC/replay/reinit | no-fault 下 replay 次数为 0 |
| coherence transaction | NACK/retry/stable ID/timeout | idle baseline 可为 0；contention 单列 |
| data RAS | ECC/poison/containment | 独立能力 gate |
| node fault | crash/partition/reconfiguration | 当前 out-of-scope |

OurCC bounded fault robustness 是独立 qualification，不能把其 retry 成本加入 lossless HA
理论平均来制造优势。

## 11. 16-node coherent switch 研究结论

公开资料支持两种高层架构 [S7][S13][S14]；有限实例模型不自动提供任意 16-node 证明
[S17][S18]：

1. 集中式/分层 switch+HA：serialization 清晰，但 root/bank 可能成为热点和高影响故障域。
2. 分布式 Home slices+routed fabric：aggregate bandwidth 更好，但路由重配置、Home remap、
   partition 和形式化状态空间更复杂。

公开研究只证明架构可行性，不证明当前项目已实现。仓库静态可行性和当前 blocker 详见：

`docs/analysis/16n_switch_feasibility_20260807_zh.md`

## 12. 目标 1/2 方法学补充

- 512 KiB 应明确为 `512*1024 bytes`；bit ledger 区分 persistent/transient/off-chip。
- capacity 用 `|Resident union Backstore|`，Bloom positive 不是 exact tracked cacheline。
- 目标 2 eligibility 使用 naive guest-visible mean `>=500 ns`，并在看 optimized 前预注册。
- case-level 等权平均保留为合同主指标，同时附 operation-weighted 敏感性分析。
- 至少 3 轮只是 smoke minimum；按预注册 CI half-width/CV/margin 决定是否增加样本。
- paired run 应保留 raw samples、负结果和 TC138 退化，不能按结果停表。

## 13. 推荐合同文字

### A. 保留严格 `<`

双方冻结 topology、同频、operation weights、placement、lossless 负载和共同安全完成语义；
采用 paired runs。当 `delta=T_mean_HA-T_mean_OurCC` 的预注册 95% 单侧置信下界严格大于 0，
且 correctness/memory-order gates 通过时，判目标 3 PASS。

### B. 条件化严格 `<`

按甲方书面冻结的 HA profile 分别比较；仅当指定 profile 满足 break-even 且 paired evidence
确认时，判该 profile `CONDITIONAL PASS`。未知或不满足 profile 保持 `UNPROVEN/RISK`。

### C. `<= + 结构性优势`

这是正式合同变更，不是原始 `<` 的自动解释。时延 non-inferiority 和结构性指标必须分别
判定，结构优势不得抵消 correctness failure。

## 14. 行动项和停止条件

| 优先级 | 行动 | 完成证据 |
|---:|---|---|
| P0 | 获取甲方 Q1-Q5，随后 Q6-Q15 | 双方签字参数表 |
| P0 | 冻结 operation set/weights、placement、主完成点 | versioned acceptance manifest |
| P0 | 生成三完成点、placement、logical/physical traversal、P 分项 trace | schema+tests |
| P0 | paired multi-run，预注册 CI/最大轮数/inconclusive | raw result package |
| P0 | correctness/memory-order gates 先于时延判定 | oracle/litmus report |
| P1 | one-way profile 如推进，先证明 install/fence/next-conflict | design+formal+E2E |
| P2 | repeated/composed/burst/topology/exhaustion | coverage matrix |

**停止条件：** 若甲方不提供合法分支、completion、service/placement 边界，也无可运行 HA，
目标 3 正式记录为 `UNPROVEN`，不得使用假想 HA 数值签收。

## 15. 研究附件

- 一页结论：`docs/research/target3_onepage_summary_20260806_zh.md`
- 甲方问题：`docs/research/customer_ha_questions_20260806_zh.md`
- 来源矩阵：`docs/research/ha_coherence_source_matrix_20260806.tsv`
- 操作 DAG：`docs/research/ha_ourcc_operation_dags_20260806.md`
- Litmus 规格：`docs/research/arm_riscv_coherence_litmus_plan_20260806_zh.md`

本报告不改变合同：`TIE` 不满足 `<`，结构性优势不自动满足 `<`，unknown 也不按任一方
胜出处理。
