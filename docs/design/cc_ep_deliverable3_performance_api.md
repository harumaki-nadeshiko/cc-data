# UBCC 性能验收与集成接口说明

**文档版本：V1.0**

**交付阶段：正式文档第一版**

**项目名称：<XXX>**

**甲方单位：<XXX>**

<!-- PAGEBREAK -->

---

## 目录

1. 执行摘要
2. 验收方法
3. 指标 1：等效追踪容量与附加时延
4. 指标 2：适用场景端到端时延
5. 指标 3：UBCC 与 HA-VI 配对比较
6. 正确性与回归结果
7. 集成接口
8. 集成流程与代码结构
9. 总结
附录 A 指标口径
附录 B 指标 2 场景说明
附录 C 指标 3 场景说明
附录 D 接口速查表
附录 E 术语表

<!-- PAGEBREAK -->

---

## 1. 执行摘要

### 1.1 三项指标结论

UBCC 最终性能验收包括容量效率、适用场景端到端时延和 HA-VI 配对比较三项指标。三项
指标均达到冻结验收口径。

![图 1-1 UBCC 三项性能指标验收结果](figures/ubcc-metric-summary.png)

| 指标 | 验收门槛 | 最终结果 | 结论 |
|---|---:|---:|---|
| 指标 1：等效追踪容量 | ≥ 1.500× | 1.515× | 通过 |
| 指标 1：附加时延 | < 50.000 cycles | -1635.994 cycles | 通过 |
| 指标 2：适用场景等权平均降幅 | ≥ 10.000% | 64.759% | 通过 |
| 指标 3：UBCC 相对 HA-VI | 两场景组均满足 UBCC 平均时延更低 | 两压力点、两场景组全部满足 | 在可执行参考模型范围内通过 |

### 1.2 正确性门禁

性能结论以正确性验证为前提：

| 验证矩阵 | 规模 | 结果 |
|---|---:|---:|
| 指标 1/2 正式矩阵 | 72 项 | 72/72 通过 |
| 指标 3 配对矩阵 | 160 arms | 160/160 通过 |
| 重型回归 | 6 项 | 6/6 通过 |
| Q1-Q5 故障资格 | 52 项 | 52/52 通过 |

### 1.3 完整结果覆盖

正式性能结果分为合同计分集和支撑结果集：合同计分集给出最终 PASS 数值，支撑结果集验证
容量机制、Backstore 生命周期、真实容量压力和代表应用的可迁移性。

| 结果层级 | Testcase | 论证职责 |
|---|---|---|
| 合同计分集 | TC131、TC135-TC140、TC217 | 计算指标 1/2 最终合同数值 |
| 容量与机制集 | TC120-TC129、TC141 | 验证访问模式、offload/onload、writeback、replay 和 shared-writer recovery |
| 真实容量压力集 | TC130-TC134 | 验证热点复用、catalog、checkpoint、frontier 和 sliding window |
| 代表应用集 | TC142-TC147 | 验证数据库、FaaS、图计算和 feature store 在 16N1S 下的应用价值 |

支撑 testcase 不重复加入合同总分；它们用于说明 UBCC 的收益来源和适用场景，并排除合同
结果由单一 workload 产生的替代解释。

### 1.4 指标 3 适用范围

指标 3 以 HA-VI 可执行参考模型为对照，冻结条件为 2N1S、O3、单向完成语义以及 100%
和 150% L3 压力。该比较用于评估 UBCC 与冻结参考模型在相同 workload 和完成边界下的
协议时延，不等同于客户物理芯片实测。

---

## 2. 验收方法

### 2.1 Profile 定义

指标 1/2 使用三个 profile：

| Profile | 目录策略 | 时延优化 | 论证职责 |
|---|---|---|---|
| naive | ResidentDir 满时直接替换 | 关闭 | 固定 SRAM 基线 |
| spill-noopt | ResidentDir + Backstore | 关闭 | 证明容量扩展本身的收益与成本 |
| optimized | ResidentDir + Backstore | 开启 | 证明协议优化后的应用场景价值 |

### 2.2 指标 1 口径

指标 1 包含两个子项：

1. `spill-noopt / naive` 等效追踪容量比不低于 1.5；
2. `spill-noopt - naive` 的平均 guest 时延增量低于 50 cycles。

等效追踪容量按 ResidentDir 与已持久化 Backstore 元数据的去重覆盖量计算。

### 2.3 指标 2 口径

指标 2 比较 optimized 与 naive。冻结规则为：

- naive 平均时延不低于 500 ns 的场景进入聚合；
- 每个适用场景贡献一个百分比降幅；
- 适用场景按 case 等权平均；
- 所有计划场景均执行，低时延中性场景保留为控制项。

### 2.4 指标 3 口径

指标 3 采用五对 UBCC/HA-VI 配对运行，并在两个 L3 压力点分别判定：

- **核心场景组**：TC228、TC229、TC230 各占 1/3；
- **代表场景组**：TC231-TC235 各占 1/5。

代表场景组每个 testcase 只贡献一个主值：

- TC231：clean shared read；
- TC232：`2/3 × hot-key read + 1/3 × hot-key write`；
- TC233：producer-consumer service；
- TC234：queued-token end-to-end；
- TC235：catalog-KV end-to-end。

判据为严格的 `UBCC 平均时延 < HA-VI 平均时延`。

### 2.5 完成边界

所有比较采用共同的根操作边界。开始点为 workload 发起目标操作，结束点为数据和权限满足
该 workload 的可观察完成条件。内部服务计时和诊断子阶段不重复计入聚合。

### 2.6 计分集与支撑集

合同计分集的 testcase、权重和门槛在评审前冻结。支撑集按各自明确职责报告，不将未冻结
权重的场景混合成额外总分：

- TC120-TC124 比较多类访问模式下的完整场景行为；
- TC125-TC129 验证 Backstore 机制路径的完成性；
- TC130-TC134 展示固定容量压力下的真实数据复用场景；
- TC141 验证 spill 后 shared-to-writer 恢复；
- TC142-TC147 展示 16N1S 代表应用结果。

---

## 3. 指标 1：等效追踪容量与附加时延

### 3.1 结果

| 项目 | naive | spill-noopt | 比较结果 |
|---|---:|---:|---:|
| 等效追踪条目 | 65,536 | 99,293 | 1.515× |
| guest 平均时延 | 899.032 ns/op | 81.035 ns/op | -817.997 ns/op |
| 2 GHz 周期差 | — | — | -1635.994 cycles |

### 3.2 结果解释

spill-noopt 的等效追踪容量达到 naive 的 1.515 倍，超过 1.5 倍门槛。容量扩展由
ResidentDir 与 Backstore 的分层管理实现，热点元数据保留在 SRAM 路径，冷元数据进入
后备存储。

压力后的 guest 平均时延没有增加，spill-noopt 比 naive 低 817.997 ns/op。该结果说明
分层目录不仅扩展了等效追踪容量，也减少了 naive 容量替换带来的重复目录操作。

### 3.3 结论

指标 1 的容量和时延两个子项均通过：

- 等效追踪容量提升 51.509%；
- 附加时延低于合同上限，并表现为负增量。

### 3.4 容量机制支撑结果

指标 1 的合同数值由 TC131 给出，以下 testcase 对容量机制和 Backstore 生命周期提供支撑：

| Testcase | 场景 | 结果职责 |
|---|---|---|
| TC120 | baseline performance mix | 验证 mixed read/write 下三 profile 正确执行 |
| TC121 | cold streaming overflow | 验证低复用连续压力下的目录行为 |
| TC122 | hot reuse after pressure | 验证目录压力后热点共享信息保持 |
| TC123 | shared hotset periodic upgrade | 验证共享热点与周期性写升级 |
| TC124 | owner/home/requester split | 验证三方分离的数据与权限收敛 |
| TC125 | read offload/onload | 验证冷元数据换出和读换入 |
| TC126 | resident upgrade replay | 验证 waiter 与升级重放 |
| TC127 | writeback offload/onload | 验证脏写回持久化和重新装入 |
| TC128 | clean evict offload/onload | 验证干净逐出和元数据恢复 |
| TC129 | long mixed integration | 验证多轮 spill/fill 与所有权迁移 |
| TC141 | shared-writer recovery | 验证 spill 后共享转写者恢复 |

TC120-TC124 的三 profile 运行均通过；TC125-TC129 的适用 spill 路径均通过。该组结果说明
正式容量收益建立在完整的元数据生命周期之上，而不是单一容量计数。

---

## 4. 指标 2：适用场景端到端时延

### 4.1 场景结果

| 场景 | naive ns/op | optimized ns/op | optimized 降幅 |
|---|---:|---:|---:|
| TC135 preserved sharer revisit | 2344.449 | 39.736 | 98.305% |
| TC136 preserved owner store | 2384.186 | 79.473 | 96.667% |
| TC137 new requester load | 2384.186 | 1788.139 | 25.000% |
| TC138 dirty owner handoff | 2384.186 | 2702.077 | -13.333% |
| TC139 mixed batch | 23563.703 | 635.783 | 97.302% |
| TC217 catalog batch | 4132.589 | 635.783 | 84.615% |

TC140 的 naive 均值为 119.209 ns，低于 500 ns 适用门槛，因此作为低时延中性控制项，
不进入指标 2 聚合。

### 4.2 聚合结果

六个适用场景的 case-level 等权平均降幅为：

```text
64.759%
```

TC138 展示了 dirty owner handoff 场景下数据回收与权限迁移的机制权衡，其结果完整纳入
等权平均。聚合结果仍显著超过 10% 门槛，说明 optimized profile 在适用场景集合中形成
稳定的总体收益。

### 4.3 优势来源

指标 2 的主要收益来自：

- 已有目录关系的直接复用；
- 热点 owner 和 sharer 状态的保留；
- 批量场景下跨节点控制消息的摊薄；
- 分层目录减少容量替换与后续重建；
- 权限路径与数据路径按实际状态选择，避免不必要的全流程重建。

### 4.4 结论

指标 2 的适用场景等权平均降幅为 64.759%，超过 10% 合同门槛。

### 4.5 真实容量压力支撑结果

TC130-TC134 使用更大工作集和多节点拓扑，展示目录压力后状态复用的场景价值：

| Testcase | 场景 | 主要结果 |
|---|---|---:|
| TC130 | directory overflow 后热点复用 | spill 相对 naive 降低 57.68% |
| TC131 | catalog full scan 后复用 | 正式指标 1 容量与时延计分场景 |
| TC132 | dirty checkpoint recovery | 验证脏数据 checkpoint 与 Backstore 恢复 |
| TC133 | 8N1S shared frontier | optimized 相对 naive 降低 7.17% |
| TC134 | 8N2S sliding window | optimized 相对 naive 降低 76.42% |

TC130、TC133 和 TC134 表明 UBCC 在目录压力后仍能保留有价值的热点元数据，收益在滑动
窗口和高复用场景中最为突出。TC132 负责验证脏数据恢复路径，不进入指标 2 合同聚合。

### 4.6 16N1S 代表应用结果

TC142-TC147 覆盖数据库、FaaS、图计算和 feature store。每个 testcase 均运行 naive、
spill-noopt 和 optimized 三个 profile，共 18/18 通过。

| Testcase | 应用场景 | naive ns/op | optimized ns/op | optimized 降幅 |
|---|---|---:|---:|---:|
| TC142 | OLTP buffer pool | 5200.314 | 4437.245 | 14.674% |
| TC143 | B-tree traversal | 3051.768 | 2267.770 | 25.690% |
| TC144 | WAL/checkpoint | 5294.067 | 4400.848 | 16.872% |
| TC145 | FaaS warm invocation | 2886.169 | 2291.965 | 20.588% |
| TC146 | Graph frontier | 3184.057 | 2266.781 | 28.808% |
| TC147 | Feature store | 2892.318 | 2321.671 | 19.730% |

六个代表应用均显示 optimized 相对 naive 的端到端收益，降幅范围为 14.67%–28.81%。
该矩阵证明 UBCC 的容量和协议机制可以迁移到真实应用形态与 16 节点拓扑，而不局限于
协议微场景。

---

## 5. 指标 3：UBCC 与 HA-VI 配对比较

### 5.1 场景定义

核心场景组承担协议关键路径比较：

| 场景 | 主操作 | 主要协议责任 |
|---|---|---|
| TC228 | Remote Read | 权威数据定位与共享授权 |
| TC229 | Ownership Handoff | 旧 owner 释放、最新数据返回、新 owner 获权 |
| TC230 | Shared-to-Writer | sharer 失效、Ack 收敛和单写者授权 |

代表场景组承担应用组合价值比较，包括 clean shared read、hot-key read/write、
producer-consumer、queued token 和 catalog-KV。

### 5.2 聚合结果

| L3 压力 | 场景组 | UBCC ticks/op | HA-VI ticks/op | UBCC 降幅 |
|---:|---|---:|---:|---:|
| 100% | 核心场景组 | 31.440 | 39.344 | 20.090% |
| 100% | 代表场景组 | 76.178 | 79.060 | 3.645% |
| 150% | 核心场景组 | 31.406 | 39.346 | 20.179% |
| 150% | 代表场景组 | 76.195 | 79.073 | 3.640% |

### 5.3 理论路径解释

使用统一表达：

```text
T = K_crossnode × τ + P
```

其中 `K_crossnode` 表示跨节点串行消息段，`τ` 表示单段传输时延，`P` 表示目录查询、
节点内一致性和完成处理。

#### 5.3.1 TC228 Remote Read

数据和权限路径为 requester → Home → 数据源 → Home → requester。UBCC 依据 owner 状态
选择权威数据源，并在数据返回后更新共享关系。该场景中 UBCC 与 HA-VI 均保持紧凑路径，
UBCC 的优势较小但稳定。

#### 5.3.2 TC229 Ownership Handoff

UBCC 首先定位 latest-data owner，再组织旧 owner 释放数据和权限，最后向新 owner 授权。
该场景是核心场景组的主要优势来源，说明独立全局目录能够有效缩短“数据在哪里”和“谁可写”
两个问题的联合决策路径。

#### 5.3.3 TC230 Shared-to-Writer

UBCC 冻结当前 sharer 目标集合，发出 Invalidate，等待 Ack 收敛后向 requester 授予单写者
权限。核心收益来自精确目标选择和统一 completion/grant 链。

#### 5.3.4 TC232 Hot-Key 组合

TC232 workload 每轮包含 32 次 read 和 16 次 write，因此冻结主值采用 `2/3 read + 1/3
write`。read 和 write 先分别计量，再合成为一个 testcase 主值，避免同一 workload 重复计权。

### 5.4 压力稳定性

从 100% 提升至 150% L3 压力后，两组结果保持稳定：

- 核心场景组降幅维持约 20.1%；
- 代表场景组降幅维持约 3.64%。

这表明 UBCC 的比较优势不是由单一压力点产生，而是来自目录定位、权限仲裁和完成路径的
结构性差异。

### 5.5 结论

在冻结的 HA-VI 可执行参考模型范围内，UBCC 在两个 L3 压力点的核心场景组和代表场景组
均满足平均时延更低，指标 3 通过。

---

## 6. 正确性与回归结果

### 6.1 性能矩阵正确性

| 矩阵 | 计划项 | 通过 | 失败 |
|---|---:|---:|---:|
| 指标 1/2 | 72 | 72 | 0 |
| 指标 3 | 160 arms | 160 | 0 |

每项性能运行同时检查数据读回、目标阶段、受管模块退出和 profile 身份，确保性能值来自完整
且正确的协议执行。

支撑结果还包括 TC120-TC129 的机制与性能路径、TC130-TC134 的真实容量压力场景、
TC141 shared-writer recovery，以及 TC142-TC147 的 16N1S 三 profile 应用矩阵。

### 6.2 重型回归

重型回归覆盖热点竞争、大拓扑、目录容量和长路径事务，共 6 项，全部通过。

### 6.3 故障资格

Q1-Q5 故障资格共 52 项，覆盖消息丢失、重复、延迟、乱序、连续丢失、组合故障、burst、
partial Ack 和多拓扑，52/52 通过。该结果为性能结论提供可靠性前提。

---

## 7. 集成接口

### 7.1 UBCC 目录服务接口

| 接口类别 | 输入 | 输出 | 完成条件 | 重试语义 |
|---|---|---|---|---|
| 读请求 | 地址、requester、权限类型、事务身份 | 数据、共享/独占授权 | 数据和授权可用 | BUSY 或相同 tuple 重试 |
| 写权限请求 | 地址、requester、目标权限 | target set、接受状态 | 失效目标收敛 | 保持 epoch/reqId |
| Clear | 地址、requester、epoch、reqId | 接受/等待/拒绝 | intended state 提交 | tombstone 幂等返回 |
| Writeback | 地址、来源、epoch、数据状态 | 接受状态 | 数据和目录关系更新 | 重复写回不重复提交 |
| Evict | 地址、来源、目录身份 | 接受状态 | sharer/owner 关系释放 | 过期请求被拒绝 |

### 7.2 EP-RNF 接口

| 接口类别 | 作用 | 完成条件 |
|---|---|---|
| ReadShared | 从节点内缓存层级获取共享数据 | 数据和共享状态返回 |
| ReadUnique | 回收独占数据并使本地副本降级 | 数据返回且本地权限释放 |
| CleanUnique | 执行节点内失效 | 目标缓存副本失效 |
| Snoop 处理 | 响应 HN-F 的 invalidating 或 data snoop | immediate 或 stale 响应完成 |

### 7.3 EP-SNF 接口

EP-SNF 接收节点内服务请求，将地址、操作类型和事务身份交给 UBAdapter，并在 UBCC 返回后
生成节点内数据或完成响应。

### 7.4 UBAdapter 接口

| 能力 | 说明 |
|---|---|
| 消息发送 | 将节点内请求转换为 Outer 协议消息 |
| 消息接收 | 按类型和事务身份分发响应 |
| 回调完成 | 将数据和权限结果返回 EPBackend |
| 稳定重试 | 对可恢复请求保持原 epoch/reqId |
| 生命周期管理 | 退役已完成 pending、ready 和 deferred 状态 |

### 7.5 公共消息字段

跨模块消息至少包含：

- 消息类型；
- 源节点与目标节点；
- 源 Socket 与目标 Socket；
- 物理地址；
- epoch 与 reqId；
- 请求权限或响应状态；
- 数据有效性和数据负载；
- target mask 与 Ack 信息。

---

## 8. 集成流程与代码结构

### 8.1 构建流程

远端工程采用 CMake 组织，`compile.sh` 作为统一构建入口，依次完成 framework、hamodule、
ubiomodule、networksim 和 gem5 的构建。

### 8.2 运行流程

1. `gen_topo.py` 根据节点数、Socket 数和链路参数生成拓扑；
2. `parallel_test_v2.py` 选择 testcase 或矩阵，生成运行配置并安排并行资源；
3. `parallel_test_v2.py` 调用 `simulate.py` 启动单组仿真；
4. `simulate.py` 根据 JSON 配置启动 gem5、ubiomodule 和 networksim；
5. 验证逻辑汇总数据结果和模块退出状态；
6. `parallel_test_v2.py` 汇总矩阵结果。`[TODO-R01]`

### 8.3 工程结构

```text
<XXX>sim/
├── src/
├── sims/
│   ├── gem5/
│   ├── ubiomodule/
│   ├── hamodule/
│   ├── networksim/
│   ├── framework/
│   │   ├── iface/
│   │   └── <XXX>sim_shim/
│   └── protocol/
├── compile.sh
├── gen_topo.py
├── parallel_test_v2.py
├── simulate.py
└── CMakeLists.txt
```

### 8.4 集成配置

主要配置项包括：

- 节点数与每节点 Socket 数；
- 地址段和 Home 映射；
- ResidentDir 与 Backstore 容量；
- CPU 模型和缓存层级；
- 链路时延与拓扑；
- 测试集合、超时和并行任务数；
- UBCC 或 HA-VI 运行模式。

---

## 9. 总结

UBCC 在容量效率、适用场景时延和 HA-VI 配对比较三个维度均达到冻结验收口径。分层目录
实现 1.515 倍等效追踪容量，optimized profile 在适用场景中取得 64.759% 等权平均
降幅；在两个 L3 压力点下，UBCC 的核心场景组和代表场景组均优于 HA-VI 可执行参考模型。

正确性矩阵、重型回归和 Q1-Q5 故障资格全部通过，为性能结果和远端集成提供了完整基础。

---

## 附录 A 指标口径

| 指标 | 计算方式 |
|---|---|
| 指标 1 容量比 | spill-noopt 等效追踪容量 / naive 等效追踪容量 |
| 指标 1 时延差 | spill-noopt guest 平均时延 - naive guest 平均时延 |
| 指标 2 case 降幅 | `(naive - optimized) / naive × 100%` |
| 指标 2 聚合 | 适用 case 降幅的等权平均 |
| 指标 3 delta | `HA-VI 平均时延 - UBCC 平均时延` |
| 指标 3 通过 | 核心场景组和代表场景组的 delta 均严格大于 0 |

---

## 附录 B 指标 2 场景说明

| 场景 | 主值 |
|---|---|
| TC135 | preserved sharer first load |
| TC136 | preserved owner store complete |
| TC137 | new requester first load |
| TC138 | dirty owner handoff store |
| TC139 | mixed batch 16 operations |
| TC140 | cross-L2 owner store，中性控制项 |
| TC217 | catalog batch 16 operations |

### B.1 指标 1/2 支撑结果图谱

| Testcase 范围 | 结果层级 | 主要职责 |
|---|---|---|
| TC120-TC124 | 场景行为 | mixed、cold stream、hot reuse、shared upgrade、三方分离 |
| TC125-TC129 | Backstore 机制 | offload/onload、replay、writeback、mixed lifecycle |
| TC130-TC134 | 真实容量压力 | overflow、catalog、checkpoint、frontier、sliding window |
| TC135-TC140 | 指标 2 计分与控制 | preserved state、new requester、handoff、batch、neutral control |
| TC141 | 正确性资格 | spill 后 shared-to-writer recovery |
| TC142-TC147 | 代表应用 | OLTP、B-tree、WAL、FaaS、graph、feature store |
| TC217 | 指标 2 计分 | catalog batch |

---

## 附录 C 指标 3 场景说明

| 场景 | 场景组 | 聚合主值 |
|---|---|---|
| TC228 | 核心 | remote read |
| TC229 | 核心 | ownership handoff |
| TC230 | 核心 | shared-to-writer |
| TC231 | 代表 | clean shared read |
| TC232 | 代表 | 2/3 hot-key read + 1/3 hot-key write |
| TC233 | 代表 | producer-consumer service |
| TC234 | 代表 | queued-token end-to-end |
| TC235 | 代表 | catalog-KV end-to-end |

---

## 附录 D 接口速查表

| 模块 | 主要入口 | 主要输出 |
|---|---|---|
| UBCCController | read、upgrade、clear、writeback、evict | grant、target set、commit result |
| ResidentDir | lookup、insert、erase、waiter | directory entry、capacity status |
| Backstore | read、write、erase | persisted directory metadata |
| EP-RNF | read shared/unique、clean unique、snoop | data、snoop response、completion |
| EP-SNF | request service | CHI data/response |
| UBAdapter | send、receive、retry、callback | Outer message、local completion |
| NetworkSim | route message | destination delivery |

---

## 附录 E 术语表

| 术语 | 说明 |
|---|---|
| UBCC | 跨节点缓存一致性方案及全局目录控制器 |
| HA-VI | 指标 3 使用的 VI 协议可执行参考模型 |
| 可执行参考模型 | 使用相同 workload、完成边界和冻结参数运行的协议对照模型 |
| Profile | 一组冻结的目录策略与协议优化配置 |
| ResidentDir | SRAM 驻留目录 |
| Backstore | 冷目录元数据的后备存储 |
| 等效追踪容量 | ResidentDir 与持久化 Backstore 元数据的去重覆盖量 |
| naive | 不使用 Backstore 容量扩展和时延优化的基线 profile |
| spill-noopt | 使用 Backstore、关闭时延优化的 profile |
| optimized | 使用 Backstore 并启用时延优化的 profile |
| 核心场景组 | TC228-TC230 等权聚合 |
| 代表场景组 | TC231-TC235 按冻结主值等权聚合 |
| 单向完成语义 | 根操作不以同步 ClearResp 作为完成条件的语义 |
| 配对运行 | UBCC 与 HA-VI 使用相同输入和冻结条件的成对运行 |
| 完成边界 | 根操作开始和结束时刻的共同定义 |
