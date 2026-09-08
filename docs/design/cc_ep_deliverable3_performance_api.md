# UBCC 性能验收与集成接口说明

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
8. 集成流程与验证模板
9. 总结
附录 A 指标口径
附录 B TC120-TC147 与 TC217 场景明细
附录 C TC228-TC235 场景明细
附录 D 四拓扑扩展性数据
附录 E 接口速查表
附录 F 术语表

<!-- PAGEBREAK -->

---

## 1. 执行摘要

### 1.1 三项指标结论

UBCC 最终性能验收包括容量效率、适用场景端到端时延和 HA-VI 配对比较三项指标。三项
指标均达到冻结验收口径。

| 指标 | 验收门槛 | 最终结果 | 结论 |
|---|---:|---:|---|
| 指标 1：等效追踪容量 | ≥ 1.500× | 1.515× | 通过 |
| 指标 1：附加时延 | < 50.000 cycles | 21.069 cycles | 通过 |
| 指标 2：适用场景等权平均降幅 | ≥ 10.000% | 64.759% | 通过 |
| 指标 3：UBCC 相对 HA-VI | 两场景组均满足 UBCC 平均时延更低 | 固定 L3 配置下两场景组全部满足 | 在可执行参考模型范围内通过 |

### 1.2 结论与范围

| 项目 | 结论 | 适用范围 |
|---|---|---|
| 指标 1 | 容量 1.515×；附加时延 10.535 ns / 21.069 cycles | 当前两个合格证据集按各自冻结职责组合计分 |
| 指标 2 | 适用场景等权平均降幅 64.759% | 冻结计分 testcase 与适用门槛 |
| 指标 3 | 固定 L3 配置下两个场景组均满足 UBCC 聚合均值更低 | 冻结 HA-VI 可执行参考模型，不代表物理芯片 |
| 范围外 | 不作本次交付能力或性能主张 | 未建模目标硬件和端口级 Switch 微体系结构 |

### 1.3 正确性门禁

性能结论以正确性验证为前提：

| 验证矩阵 | 规模 | 结果 |
|---|---:|---:|
| 指标 1/2 profile 矩阵 | 72 项 | 72/72 通过 |
| 指标 1 修正时延独立矩阵 | 6 次仿真运行 | 6/6 通过 |
| 指标 3 配对矩阵 | 80 次仿真运行 | 80/80 通过 |
| 重型回归 | 6 项 | 6/6 通过 |
| Q1-Q5 故障资格 | 52 项 | 52/52 通过 |

### 1.4 完整结果覆盖

正式性能结果分为合同计分集和支撑结果集：合同计分集给出最终 PASS 数值，支撑结果集验证
容量机制、Backstore 生命周期、真实容量压力和代表应用的可迁移性。

| 结果层级 | Testcase | 论证职责 |
|---|---|---|
| 合同计分集 | TC131、TC135-TC140、TC217 | 计算指标 1/2 最终合同数值 |
| 容量与机制集 | TC120-TC129、TC141 | 验证访问模式、offload/onload、writeback、replay 和 shared-writer recovery |
| 真实容量压力集 | TC130-TC134 | 验证热点复用、catalog、checkpoint、frontier 和 sliding window |
| 代表应用集 | TC142-TC147 | 验证数据库、FaaS、图计算和 feature store 在 16N1S Level-A 下的应用价值 |

支撑 testcase 不重复加入合同总分；它们用于说明 UBCC 的收益来源和适用场景，并排除合同
结果由单一 workload 产生的替代解释。

### 1.5 指标 3 适用范围

指标 3 以 HA-VI 可执行参考模型为对照，冻结条件为 2N1S、O3、单向完成语义、256 KiB
L3 和 100% L3 压力。该比较用于评估 UBCC 与冻结参考模型在相同 workload 和完成边界下的
协议时延，不等同于目标物理芯片实测；其他 L3 配置不进入正式结果。

---

## 2. 验收方法

### 2.1 Profile 定义与差异

指标 1/2 使用以下三个基础 profile。三者采用相同处理器、缓存层级、工作负载和完成边界，
差异集中在目录容量组织和协议时延优化。

| 配置维度 | naive | spill-noopt（noopt） | optimized（opt） |
|---|---|---|---|
| 片上目录预算 | 512 KiB | 512 KiB | 512 KiB |
| ResidentDir 物理容量 | 65,536 条 | 57,344 条 | 57,344 条 |
| Bloom 与 GroupIndex | 不启用 Bloom；4 KiB GroupIndex | 60 KiB 分组 Bloom；4 KiB GroupIndex | 与 spill-noopt 相同 |
| 容量溢出策略 | 在 ResidentDir 内选择替换条目 | 冷目录元数据迁移至 H64 Backstore | 与 spill-noopt 相同 |
| H64 Backstore | 不用于容量扩展 | 启用 lookup、upsert、erase 和按需换入 | 与 spill-noopt 相同 |
| 本地 silent upgrade | 关闭 | 关闭 | 开启 |
| batch read-shared | 关闭 | 关闭 | 开启 |
| direct forwarding | 关闭 | 关闭 | 关闭 |
| 指标 1 职责 | 容量分母 | 容量分子和 spill-512K 时延角色 | 支撑结果 |
| 指标 2 职责 | 时延基线 | 容量机制观察 | 与 naive 形成应用时延比较 |

spill-IdealDir 是指标 1 附加时延使用的实验角色。它保持 spill-noopt 的协议优化设置，采用
2 MiB 实验性片上目录预算和 131,072 条 ResidentDir 容量，为 TC131 提供无目录溢出的参考路径。

### 2.2 仿真拓扑与共同配置

本文使用 `NnSs` 描述逻辑仿真拓扑，其中 `N` 表示参与跨节点一致性的协议节点数，`S` 表示
每个节点包含的 Socket 数。例如，`8N1S` 表示 8 个协议节点、每节点 1 个 Socket；`8N2S`
表示 8 个协议节点、每节点 2 个 Socket。

每个协议节点包含处理器与缓存层级、节点内 HN-F，以及连接 Inner CHI 域和 Outer 一致性层
的 EP。地址映射为每条缓存行选择 Home 节点和 Home Socket；Home UBCC 控制器负责全局目录
查询、owner/sharer 仲裁和目录提交。requester、Home 与数据 owner 可以位于不同节点，跨节点
请求、数据、失效和确认均按 node/socket 身份路由。

各对照实验使用相同的处理器模型、缓存配置、workload 输入、逻辑拓扑和根操作完成边界。
拓扑规模变化时，各 testcase 保持其参与者角色、地址映射和发布事件定义。

| 结果范围 | Testcase | 拓扑 | 主要作用 |
|---|---|---|---|
| 指标 1 | TC131 | 8N1S | Home 目录服务、catalog 扫描与写权限升级 |
| 指标 2 核心计分 | TC135-TC140 | 3N1S | owner、sharer、新 requester 与验证者分工 |
| 指标 2 catalog | TC217 | 2N1S | catalog 提供者与 read-mostly 执行者 |
| 机制与容量支撑 | TC120-TC132、TC135-TC141 | 主要为 3N1S | 共享、所有权迁移、换入换出与恢复 |
| 多节点支撑 | TC133 | 8N1S | 多 reader 共享与压力后复用 |
| 多 Socket 支撑 | TC134 | 8N2S | 16 个执行 plane 和跨 Socket 窗口复用 |
| 代表应用 | TC142-TC147 | 16N1S Level-A | 数据库、FaaS、图计算和 feature store |
| 指标 3 | TC228-TC235 | 2N1S | UBCC 与 HA-VI 配对比较 |

指标 3 的配对配置采用 O3、256 KiB L3、100% L3 压力和单向完成语义。16N1S Level-A 表示
协议节点规模和端点能力覆盖。

### 2.3 指标 1 口径

指标 1 从等效追踪容量和目录溢出附加时延两个维度评价分层目录。两个子项分别采用与测量
目标相对应的实验角色和完成事件，共同形成指标 1 的验收结果。

#### 2.3.1 等效追踪容量

等效追踪容量使用 TC131 的三次重复结果，比较固定片上目录预算下的 naive 与 spill-noopt：

```text
等效追踪容量比
= spill-noopt 等效追踪容量 / naive 等效追踪容量
```

naive 采用 ResidentDir 容量作为基线。spill-noopt 采用相同的 512 KiB 片上目录预算，并通过
ResidentDir 与 H64 Backstore 的分层管理扩展可追踪工作集。等效追踪容量按缓存行地址去重，
统计 ResidentDir 有效条目与 Backstore 已持久化有效元数据的并集：

```text
C_effective = union(ResidentDir_live, Backstore_persisted_live)
```

三次重复按照冻结规则汇总，得到 `99,293 / 65,536 = 1.515×`。验收门槛为等效追踪容量比
不低于 `1.500×`。

#### 2.3.2 目录溢出附加时延

附加时延使用同一 spill-noopt 协议策略下的两个实验角色：

- spill-512K 采用 512 KiB 片上目录预算，目录压力触发 ResidentDir 与 H64 Backstore 之间的
  换出和换入；
- spill-IdealDir 采用可容纳 TC131 工作集的扩展 ResidentDir，形成同一协议策略下的无溢出
  参考路径。

附加时延按已经满足完成条件并发布的 Outer 根事件计算。每轮分别求两个角色全部已完成
Outer 事件的平均时延，再计算差值：

```text
delta_outer(r)
= mean(T_outer, spill-512K, r)
- mean(T_outer, spill-IdealDir, r)
```

三轮差值按轮次等权平均：

```text
delta_outer = sum(delta_outer(r), r=1..3) / 3
```

三轮配对共包含六次仿真运行，最终结果为 `10.535 ns`，在 2 GHz 下换算为
`21.069 cycles`。验收门槛为附加时延低于 `50 cycles`，对应低于 `25 ns`。

#### 2.3.3 指标结果

| 子项 | 比较关系 | 最终结果 | 验收门槛 |
|---|---|---:|---:|
| 等效追踪容量 | spill-noopt / naive | 1.515× | ≥ 1.500× |
| Outer 附加时延 | spill-512K - spill-IdealDir | 10.535 ns | < 25.000 ns |
| 2 GHz 周期换算 | `delta_outer × 2` | 21.069 cycles | < 50.000 cycles |

容量实验中的 naive 和 spill-noopt 分别提供固定片上目录基线与分层目录覆盖量；时延实验中的
spill-512K 和 spill-IdealDir 分别提供有目录溢出的路径与无溢出参考路径。每个角色按对应的
测量职责形成指标结果。

### 2.4 指标 2 口径

指标 2 比较 optimized 与 naive。冻结规则为：

- naive 平均时延不低于 500 ns 的场景进入聚合；
- 每个适用场景贡献一个百分比降幅；
- 适用场景按 case 等权平均；
- 所有计划场景均执行，低时延中性场景保留为控制项。

### 2.5 指标 3 口径

指标 3 采用固定 256 KiB L3、100% 压力下的五对 UBCC/HA-VI 配对运行：

- 核心场景组：TC228、TC229、TC230 各占 1/3；
- 代表场景组：TC231-TC235 各占 1/5。

代表场景组每个 testcase 只贡献一个主值：

- TC231：clean shared read；
- TC232：`2/3 × hot-key read + 1/3 × hot-key write`；
- TC233：producer-consumer service；
- TC234：queued-token end-to-end；
- TC235：catalog-KV end-to-end。

判据为严格的 `UBCC 平均时延 < HA-VI 平均时延`。

确定性重复仿真用于证明结果可复现，不等同于对总体分布建立统计置信度。

### 2.6 完成边界

所有比较遵循发布事件原则：只统计满足冻结完成条件并已经发布完成事件的根操作。开始点为
workload 发起目标操作，结束点为数据和权限满足该 workload 的可观察完成条件。未完成事件、
内部服务计时和诊断子阶段不进入聚合。

### 2.7 计分集与支撑集

合同计分集的 testcase、权重和门槛在评审前冻结。支撑集按各自明确职责报告，不将未冻结
权重的场景混合成额外总分：

- TC120-TC124 比较多类访问模式下的完整场景行为；
- TC125-TC129 验证 Backstore 机制路径的完成性；
- TC130-TC134 展示固定容量压力下的真实数据复用场景；
- TC141 验证 spill 后 shared-to-writer 恢复；
- TC142-TC147 展示 16N1S Level-A 代表应用结果。

---

## 3. 指标 1：等效追踪容量与附加时延

### 3.1 结果

指标 1 的三个实验角色采用以下目录容量：

| 角色 | 片上目录预算 | ResidentDir 物理容量 | Backstore | 指标用途 |
|---|---:|---:|---|---|
| naive | 512 KiB | 65,536 条 | 基线策略 | 容量分母 |
| spill-512K | 512 KiB | 57,344 条 | H64 | 容量分子和附加时延被测侧 |
| spill-IdealDir | 2 MiB 实验角色 | 131,072 条 | 无溢出参考 | 附加时延参考侧 |

spill-512K 的 ResidentDir 物理容量为 57,344 条；TC131 结束时，ResidentDir 与 H64 Backstore
按地址去重后的有效覆盖达到 99,293 条，因此容量比为 `99,293 / 65,536 = 1.515×`。

| 子项 | 比较角色 | 最终结果 | 门槛 |
|---|---|---:|---:|
| 等效追踪容量 | spill / naive | 99,293 / 65,536 = 1.515× | ≥ 1.500× |
| Outer 附加时延 | spill-512K mean - spill-IdealDir mean | 10.535 ns | < 25.000 ns |
| 2 GHz 周期换算 | 按未舍入增量换算 | 21.069 cycles | < 50.000 cycles |

容量按 spill/naive 计算，其中 spill 为 512K 容量约束角色；时延按已完成 Outer 事件的
spill-512K 均值减去 spill-IdealDir 均值计算。表中数值均由未舍入结果独立计算后显示至
最多 3 位小数。

![图 3-1 指标 1 容量与 Outer 附加时延](figures/ubcc-metric1-capacity-latency.png =10cm)

图 3-1　指标 1 容量与附加时延

### 3.2 结果解释

spill 的等效追踪容量达到 naive 的 1.515 倍，超过 1.5 倍门槛；该角色采用 512K 容量
约束。容量扩展由
ResidentDir 与 Backstore 的分层管理实现，热点元数据保留在 SRAM 路径，冷元数据进入
后备存储。

附加时延采用同为 spill-noopt 策略的两个角色隔离容量溢出成本：spill-512K 使用 512K
容量约束，spill-IdealDir 使用实验性超大 ResidentDir 作为无溢出的反事实基线。三次重复中，
每次均对全部已完成 Outer 事件求均值后作差；跨轮等权均值为 10.535 ns，即
21.069 cycles。

TC131 两个时延角色的全部可用发布事件统计如下；每个角色均有三次相同配置重复，表中为
每轮已完成 Outer 事件的统计值：

| 角色 | samples/轮 | mean ns | P50 ns | P95 ns | P99 ns | max ns | ResidentDir |
|---|---:|---:|---:|---:|---:|---:|---:|
| spill-512K | 111,182 | 169.769 | 11.000 | 1720.000 | 1732.500 | 2617.000 | 57,344 |
| spill-IdealDir | 111,184 | 159.235 | 11.000 | 1720.000 | 1722.500 | 2601.500 | 131,072 |

spill-512K 每轮观察到 186 次 Backstore found fill；该附加时延矩阵中的最大精确 live 覆盖为
99,291。容量计分矩阵使用的冻结容量分子为 99,293，两组观测分别服务于容量和附加时延子项。
spill-IdealDir 不发生 Backstore fill。两角色的均值差为 10.535 ns。

### 3.3 结论

指标 1 的容量和时延两个子项均通过：

- 等效追踪容量提升 51.509%；
- 附加时延为 21.069 cycles，低于 50 cycles 合同上限。

### 3.4 重复与汇总

容量子项对 naive 和 spill-noopt 分别执行三次重复，每轮形成容量观测值，并按冻结规则汇总
容量分母和分子。附加时延子项执行三对 spill-512K 与 spill-IdealDir；每对先计算两个角色的
已完成 Outer 事件均值之差，再对三轮差值等权平均。optimized profile 作为应用性能支撑，
不参与指标 1 的容量或附加时延计算。

### 3.5 容量机制支撑结果

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

完整场景与当前保留的路径级绝对计时如下。路径级单臂值只说明对应发布事件的绝对成本，
不与不同配置或不同完成边界的数值混合计算降幅。

| TC | 三 profile scenario total（naive / spill-noopt / optimized ticks） | 保留路径计时 |
|---|---|---|
| TC120 | N/A；相对降幅 5.37% / 5.34% | shared-hot 34.958 ticks/op，1389.119 ns/op |
| TC121 | 29,147.33 / 28,897.67 / 28,898.67 | cold-stream 348.625 ticks/op，13,853.113 ns/op |
| TC122 | 25,265.33 / 25,270.33 / 25,266.67 | hot-reuse 73.125 ticks/op，2905.726 ns/op；hot-share 74.583 ticks/op，2963.675 ns/op |
| TC123 | 29,124.33 / 29,125.00 / 29,126.67 | shared-read node1/node2 75.813 / 77.125 ticks/op，均值 76.469 |
| TC124 | 15,031.67 / 15,038.33 / 15,032.33 | requester read 95.156 ticks/op，3781.170 ns/op |
| TC125 | naive N/A；适用 spill profiles 通过 | read-onload 8 ticks/op，317.891 ns/op |
| TC126 | naive N/A；适用 spill profiles 通过 | upgrade-store 51 ticks/op，2026.558 ns/op |
| TC127 | naive N/A；适用 spill profiles 通过 | writeback-flush 61,996 ticks，2,463,499.705 ns |
| TC128 | naive N/A；适用 spill profiles 通过 | verify-read 64 ticks/op，2543.132 ns/op |
| TC129 | naive N/A；适用 spill profiles 通过 | V0 onload 61 ticks/op，2423.922 ns/op；V1 onload 79 ticks/op，3139.178 ns/op |
| TC141 | naive N/A；spill-noopt/optimized 通过 | workload total node0/node1/node2 = 36,814 / 36,825 / 36,825 ticks |

![图 3-2 TC120-TC124 完整场景降幅](figures/ubcc-tc120-124-scenarios.png =10cm)

图 3-2　TC120-TC124 完整场景降幅

### 3.6 多压力与多拓扑扩展观测

新一轮 TC142-TC147 三角色矩阵进一步覆盖 175% 和 200% 目标压力，以及 3N1S、3N2S、
8N1S、8N2S 和 16N1S 拓扑。图 3-3 对每个压力/拓扑坐标先计算六个代表应用的均值；容量
使用 spill 相对 naive 的等效追踪容量增幅，时延使用 spill 相对超大 ResidentDir 参考角色的
已完成 Outer 均值差。该扩展矩阵用于展示目录压力和节点规模下的变化趋势，不替代 TC131
正式计分结果。

175% 压力下五个拓扑的平均容量增幅为 58.7%-86.7%；200% 压力下五个拓扑的
平均容量增幅为 86.9%-120.5%。
3N1S、3N2S 和 8N1S 的已完成平均 Outer 增量保持在约
24-28 cycles；16N1S 中的 B-tree、FaaS 和 feature-store 路径表现出更高的 spill/fill
敏感性，说明高节点数和高元数据 churn 会将后备目录访问放大为排队成本。

| 目标压力 | 拓扑 | 六应用平均容量增幅 | 六应用平均 Outer 增量（cycles @ 2 GHz） |
|---:|---:|---:|---:|
| 175% | 3N1S | 65.5% | 24.2 |
| 175% | 3N2S | 61.2% | 28.3 |
| 175% | 8N1S | 60.8% | 27.1 |
| 175% | 8N2S | 58.7% | -9.2 |
| 175% | 16N1S | 86.7% | 50.7 |
| 200% | 3N1S | 93.1% | 25.0 |
| 200% | 3N2S | 90.9% | 27.4 |
| 200% | 8N1S | 88.0% | 26.1 |
| 200% | 8N2S | 86.9% | 1.9 |
| 200% | 16N1S | 120.5% | 57.4 |

![图 3-3 指标 1 多压力与多拓扑扩展观测](figures/ubcc-metric1-extension-matrix.png =12cm)

图 3-3　指标 1 多压力与多拓扑扩展观测

---

## 4. 指标 2：适用场景端到端时延

### 4.1 场景结果

| 场景 | naive ns/op | spill-noopt ns/op | optimized ns/op | optimized 降幅 |
|---|---:|---:|---:|---:|
| TC135 preserved sharer revisit | 2344.449 | 39.736 | 39.736 | 98.305% |
| TC136 preserved owner store | 2384.186 | 79.473 | 79.473 | 96.667% |
| TC137 new requester load | 2384.186 | 1788.139 | 1788.139 | 25.000% |
| TC138 dirty owner handoff | 2384.186 | 2702.077 | 2702.077 | -13.333% |
| TC139 mixed batch | 23563.703 | 635.783 | 635.783 | 97.302% |
| TC217 catalog batch | 4132.589 | 635.783 | 635.783 | 84.615% |

TC140 的 naive、spill-noopt 和 optimized 均值均为 119.209 ns，低于 500 ns 适用门槛，
因此作为低时延中性控制项，不进入指标 2 聚合。

六个适用场景按 case 等权聚合，TC140 保留为中性控制项。

![图 4-1 指标 2 适用场景端到端时延](figures/ubcc-metric2-reductions.png =11cm)

图 4-1　指标 2 场景时延

### 4.2 聚合结果

六个适用场景的 case-level 等权平均降幅为 **64.759%**。

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

| TC/发布事件 | naive ticks/op | spill-noopt ticks/op | optimized ticks/op | 结果 |
|---|---:|---:|---:|---|
| TC130 scenario total | 38,616.67 | 32,331.67 | 32,329.67 | optimized 降低 16.28% |
| TC130 hot reuse（96 ops） | 110.64 | 46.82 | 46.82 | optimized 降低 57.68% |
| TC131 scenario total | 2,401,101.00 | 2,419,188.67 | 2,419,319.00 | 完整场景成本披露 |
| TC131 catalog reuse（8,192 ops） | 6.54 | 6.40 | 6.42 | optimized 降低 1.84% |
| TC131 exclusive upgrade（256 ops） | 9,358.80 | 9,429.41 | 9,429.93 | optimized 增加 0.76% |
| TC132 scenario total | 1,393,393.67 | 1,430,794.33 | 1,430,796.00 | 完整场景成本披露 |
| TC132 checkpoint recover（8,192 ops） | 48.73 | 67.97 | 67.98 | dirty recovery 成本披露 |
| TC133 scenario total | 725,467.38 | 736,140.75 | 736,044.00 | 完整场景成本披露 |
| TC133 frontier reuse（4,096 ops） | 7.09 | 6.60 | 6.58 | optimized 降低 7.17% |
| TC134 scenario total | 932,512.13 | 742,830.69 | 742,591.44 | optimized 降低 20.37% |
| TC134 window reuse（4,096 ops） | 41.67 | 9.78 | 9.83 | optimized 降低 76.42% |

TC130、TC133 和 TC134 表明 UBCC 在目录压力后仍能保留有价值的热点元数据，收益在滑动
窗口和高复用场景中最为突出。TC132 负责验证脏数据恢复路径，不进入指标 2 合同聚合。

![图 4-2 TC130-TC134 压力后主路径变化](figures/ubcc-tc130-134-pressure.png =10cm)

图 4-2　TC130-TC134 压力后主路径变化

### 4.6 16N1S Level-A 代表应用结果

TC142-TC147 覆盖数据库、FaaS、图计算和 feature store。每个 testcase 均完成 naive、
spill-noopt 和 optimized 三个 profile 的正确性运行，共 18/18 通过。下表使用同一完成边界下
可直接比较的 naive 与 optimized 端到端值；spill-noopt 已通过正确性门禁，但该组未保留用于
此表横向比较的同口径主值，因此标记为 N/A。

| Testcase | 应用场景 | naive ns/op | spill-noopt ns/op | optimized ns/op | optimized 降幅 |
|---|---|---:|---:|---:|---:|
| TC142 | OLTP buffer pool | 5200.314 | N/A | 4437.245 | 14.674% |
| TC143 | B-tree traversal | 3051.768 | N/A | 2267.770 | 25.690% |
| TC144 | WAL/checkpoint | 5294.067 | N/A | 4400.848 | 16.872% |
| TC145 | FaaS warm invocation | 2886.169 | N/A | 2291.965 | 20.588% |
| TC146 | Graph frontier | 3184.057 | N/A | 2266.781 | 28.808% |
| TC147 | Feature store | 2892.318 | N/A | 2321.671 | 19.730% |

六个代表应用均显示 optimized 相对 naive 的端到端收益，降幅范围为 14.67%–28.81%。
该矩阵证明 UBCC 的容量和协议机制可以用于代表性应用模式与 16N1S Level-A 协议节点规模，
而不局限于协议微场景；该结论不包含端口级 Switch 微体系结构。

![图 4-3 TC142-TC147 代表应用降幅](figures/ubcc-tc142-147-applications.png =11cm)

图 4-3　TC142-TC147 代表应用降幅

---

## 5. 指标 3：UBCC 与 HA-VI 配对比较

### 5.1 场景定义与关键路径

指标 3 以共同 workload 和共同根操作完成边界比较 UBCC 与 HA-VI。UBCC 通过 Home UBCC
控制器上的精确 owner/sharer 目录确定数据源、权限目标和提交顺序；HA-VI 按冻结的 VI
有效性状态与参考完成链执行相同根操作。

每个 testcase 在两个对照角色中使用相同的参与者角色、操作序列、输入配比和计量事件。核心
场景组直接覆盖三条一致性关键路径，代表场景组将相同机制置于应用化组合 workload 中。

#### 5.1.1 核心场景组

| TC | 主操作与完成边界 | UBCC 关键路径 | HA-VI 参考路径 | 比较重点 |
|---:|---|---|---|---|
| 228 | requester 获得数据与共享授权 | Home UBCC 控制器按 owner 状态选择权威数据源，并在数据返回后建立共享关系 | 按 VI 有效性关系完成数据定位和有效副本建立 | 数据源定位与共享授权 |
| 229 | 新 owner 获得最新数据与独占权限 | Home UBCC 控制器定位旧 owner，组织释放、数据返回和新 owner 授权 | 按冻结 VI 状态转移和完成链迁移写权限 | 最新数据与写权限迁移 |
| 230 | Ack 收敛并建立单写者 | Home UBCC 控制器冻结精确 sharer 集合，并行失效，Ack 收敛后授权 | 执行冻结的共享副本失效与写者建立路径 | 目标选择、失效扇出和 Ack 收敛 |

三个 testcase 各贡献一个主值，并按 `1/3` 等权形成核心场景组均值。

#### 5.1.2 代表场景组

| TC | 应用场景与操作序列 | 主值及完成边界 | UBCC 关键路径 | HA-VI 参考路径 | 展示内容 |
|---:|---|---|---|---|---|
| 231 | 压力条件下复用 clean shared line | `clean_shared_read_service` | 复用已提交共享关系，完成干净共享数据与权限服务 | 按 VI 有效副本关系完成共享读服务 | 干净共享控制成本 |
| 232 | hot key 上执行 32 次 read 和 16 次 write | `2/3 × read + 1/3 × write` | 共享读取与精确写权限迁移 | read/write 分别按冻结 VI 参考路径执行 | 热点读写综合成本 |
| 233 | producer 写入并发布，consumer 读取并完成服务 | `producer_consumer_service` | 数据发布、consumer 获取和服务完成 | 按有效性发布和读取链完成相同服务 | 发布—消费服务链 |
| 234 | ordered token 在参与者之间排队交接 | `queued_token_end_to_end` | token 写入、权限交接和有序观察 | 按冻结 VI 状态转换完成 token 交接 | 连续所有权交接 |
| 235 | catalog lookup 与 sparse update 组成 KV 批处理 | `catalog_kv_end_to_end` | lookup、稀疏更新和批次同步 | 按相同 lookup/update 序列和同步边界执行 | read-mostly 完整批处理 |

五个 testcase 各贡献一个主值，并按 `1/5` 等权形成代表场景组均值。

#### 5.1.3 主值与辅助事件

| Testcase | 进入聚合的主值 | 辅助事件 |
|---|---|---|
| TC231 | clean shared read service | — |
| TC232 | `2/3 read + 1/3 write` | hot-key read、hot-key write |
| TC233 | producer-consumer service | consumer load |
| TC234 | queued-token end-to-end | token store |
| TC235 | catalog-KV end-to-end | catalog-KV service |

辅助事件用于解释主值构成和关键路径，每个 testcase 在代表场景组中保持一次权重。

### 5.2 假设与公平性边界

| 维度 | 冻结参考条件 | 未建模硬件或禁止外推项 |
|---|---|---|
| 系统与处理器 | 2N1S、O3；两方案使用相同处理器与缓存条件 | 目标物理核、缓存和 Home 微体系结构 |
| workload | 相同 testcase、输入、操作配比及 100% L3 压力 | 未冻结流量、其他规模和其他 L3 配置 |
| 完成与计量 | 相同根操作完成边界和单向完成语义 | 未纳入主值的内部阶段与物理链路握手 |
| HA 对照 | 冻结的 HA-VI 可执行参考参数与行为 | 未建模的 HA 优化、缓冲、队列、预测及扩展状态 |
| 通信 | 相同可执行比较环境 | 端口级 Switch 排队、仲裁、拥塞和链路误码 |
| 聚合 | 核心、代表场景组按冻结主值等权聚合 | 将组均值外推为每个 testcase 或子操作均占优 |

该比较在共同输入、共同完成边界和冻结参考假设下保持公平。聚合优势不表示每个内部子操作
都占优；各场景只按冻结主值贡献一次权重。

### 5.3 聚合结果

| 固定 L3 配置 | 场景组 | UBCC ticks/op | HA-VI ticks/op | UBCC 降幅 |
|---|---|---:|---:|---:|
| 256 KiB / 100% 压力 | 核心场景组 | 31.440 | 39.344 | 20.090% |
| 256 KiB / 100% 压力 | 代表场景组 | 76.178 | 79.060 | 3.645% |

图中比较冻结的 2N1S、O3、单向完成语义和固定 256 KiB L3 配置。

![图 5-1 指标 3 UBCC 与 HA-VI 配对比较](figures/ubcc-ha-vi-comparison.png =11cm)

图 5-1　指标 3 配对结果

### 5.4 理论路径解释

使用统一表达：

```text
T = K_crossnode × τ + P
```

其中 `K_crossnode` 表示跨节点串行消息段，`τ` 表示单段传输时延，`P` 表示目录查询、
节点内一致性和完成处理。

#### 5.4.1 TC228 Remote Read

数据和权限路径为 requester → Home → 数据源 → Home → requester。UBCC 依据 owner 状态
选择权威数据源，并在数据返回后更新共享关系。该场景中 UBCC 与 HA-VI 均保持紧凑路径，
UBCC 的优势较小但稳定。

#### 5.4.2 TC229 Ownership Handoff

UBCC 首先定位 latest-data owner，再组织旧 owner 释放数据和权限，最后向新 owner 授权。
该场景是核心场景组的主要优势来源，说明独立全局目录能够联合判断数据位置与写权限归属，
缩短两个问题的决策路径。

#### 5.4.3 TC230 Shared-to-Writer

UBCC 冻结当前 sharer 目标集合，发出 Invalidate，等待 Ack 收敛后向 requester 授予单写者
权限。核心收益来自精确目标选择和统一 completion/grant 链。

#### 5.4.4 TC232 Hot-Key 组合

TC232 workload 每轮包含 32 次 read 和 16 次 write，因此冻结主值采用 `2/3 read + 1/3
write`。read 和 write 先分别计量，再合成为一个 testcase 主值，避免同一 workload 重复计权。

### 5.5 每 testcase 主值

下表给出固定 L3 配置下的全部 testcase 主值；delta 定义为 `HA-VI - UBCC`，正值表示
UBCC 时延更低。

| TC/主值 | UBCC ticks/op | HA-VI ticks/op | delta |
|---|---:|---:|---:|
| TC228 remote read | 23.938 | 24.013 | 0.075 |
| TC229 ownership handoff | 24.669 | 47.031 | 22.363 |
| TC230 shared-to-writer | 45.713 | 46.988 | 1.275 |
| TC231 clean shared control | 2.494 | 2.562 | 0.068 |
| TC232 weighted composite | 15.238 | 16.979 | 1.742 |
| TC233 producer-consumer service | 13.853 | 14.419 | 0.566 |
| TC234 queued-token end-to-end | 342.250 | 352.850 | 10.600 |
| TC235 catalog-KV end-to-end | 7.053 | 8.488 | 1.436 |

![图 5-2 指标 3 每 testcase 降幅](figures/ubcc-metric3-per-tc-reductions.png =11cm)

图 5-2　指标 3 每 testcase 降幅

### 5.6 复合项与辅助发布事件

| TC/事件 | UBCC ticks/op | HA-VI ticks/op | delta |
|---|---:|---:|---:|
| TC232 hot-key read | 13.013 | 12.744 | -0.269 |
| TC232 hot-key write | 19.688 | 25.450 | 5.763 |
| TC233 consumer load（辅助） | 24.000 | 24.000 | 0.000 |
| TC234 token store（辅助） | 23.500 | 23.013 | -0.488 |
| TC235 catalog-KV service（辅助） | 1.646 | 2.382 | 0.736 |

### 5.7 结论

在冻结的 HA-VI 可执行参考模型和固定 L3 配置下，UBCC 的核心场景组和代表场景组均满足
平均时延更低，指标 3 通过。

---

## 6. 正确性与回归结果

### 6.1 性能矩阵正确性

| 矩阵 | 计划项 | 通过 | 失败 |
|---|---:|---:|---:|
| 指标 1/2 profile 矩阵 | 72 | 72 | 0 |
| 指标 1 修正时延独立矩阵 | 6 次仿真运行 | 6 | 0 |
| 指标 3 | 80 次仿真运行 | 80 | 0 |

每项性能运行同时检查数据读回、目标阶段、受管模块退出和 profile 身份，确保性能值来自完整
且正确的协议执行。

支撑结果还包括 TC120-TC129 的机制与性能路径、TC130-TC134 的真实容量压力场景、
TC141 shared-writer recovery，以及 TC142-TC147 的 16N1S Level-A 三 profile 应用矩阵。

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

EP-SNF 接收节点内服务请求，将地址、操作类型和事务身份交给 UBAdapter，并在 Home UBCC
控制器返回后
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

## 8. 集成流程与验证模板

### 8.1 集成流程

1. 集成方确定节点数、Socket 数、地址映射和链路参数；
2. ubsim 装载 gem5 节点模型、UBIO 和所选参考模型；
3. 调度层选择 testcase 或矩阵并分配并行资源；
4. 各模块按共同事务身份交换请求、响应和完成事件；
5. 验证层检查数据结果、协议完成条件和受管模块状态；
6. 汇总层按冻结口径生成矩阵结论。

验证环境曾使用 NetworkSim 作为模拟传输承载跨进程消息；它不属于项目架构、交付组件
或公开接口。

### 8.2 parallel_test_v2 填写模板

以下模板尚未验证，仅用于集成阶段填写必要信息，不构成功能承诺。

| 必要字段 | 填写值 |
|---|---|
| 测试集合选择方式 | 〔待填写〕 |
| 并行任务数 | 〔待填写〕 |
| 单项超时 | 〔待填写〕 |
| 结果判定与汇总位置 | 〔待填写〕 |

### 8.3 集成配置

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
降幅；在固定 256 KiB L3 配置下，UBCC 的核心场景组和代表场景组均优于 HA-VI 可执行
参考模型。

正确性矩阵、重型回归和 Q1-Q5 故障资格全部通过，为性能结果和远端集成提供了完整基础。

---

## 附录 A 指标口径

| 指标 | 计算方式 |
|---|---|
| 指标 1 容量比 | spill 等效追踪容量 / naive 等效追踪容量（spill 为 512K 容量约束角色） |
| 指标 1 时延差 | 已完成 Outer 事件的 spill-512K 均值 - spill-IdealDir 均值 |
| 指标 2 case 降幅 | `(naive - optimized) / naive × 100%` |
| 指标 2 聚合 | 适用 case 降幅的等权平均 |
| 指标 3 delta | `HA-VI 平均时延 - UBCC 平均时延` |
| 指标 3 通过 | 核心场景组和代表场景组的 delta 均严格大于 0 |

---

## 附录 B TC120-TC147 与 TC217 场景明细

以下说明统一按发布事件组织：拓扑/角色定义参与者，阶段描述根操作序列，压力/工作集说明
容量条件，主测量/完成边界定义可计量事件，能力列只陈述该 testcase 实际展示的性质。

### B.1 TC120-TC129

| TC | 拓扑/角色 | 阶段与操作序列 | 压力/工作集 | 主测量/完成边界 | 展示能力 |
|---|---|---|---|---|---|
| TC120 | 3N1S；node0 初始化，node1 读热点，node2 更新 | 12-line populate → 24 shared reads → owner migration → reread | 12 lines，6 hot | 24-op shared-hot timer及场景完成 | mixed read/write、共享读和所有权迁移 |
| TC121 | 3N1S；node0 写流，node1 抽样读 | 64-line cold stream → 每 4 条抽样 → 完成 | 64 条低复用线 | workload_total | 冷流溢出下三 profile 完成 |
| TC122 | 3N1S；node0 施压，node1 首读，node2 重用 | 24 hot share → 128 cold pressure → 24 hot reread | 24 hot + 128 cold | hot reuse 完成及 workload_total | 压力后共享热点保持 |
| TC123 | 3N1S；node1/node2 共享，node1 升级，node2 验证 | init → share → 96-line pressure → periodic upgrades → verify | 16 hot + 96 cold | verify_upgrade 后发布 | shared-to-writer 升级收敛 |
| TC124 | 3N1S；owner=node2、Home=node1、requester=node0 | owner 写 32 lines → requester 读 32 lines | 32 lines，三方分离 | 32 次读取完成 | 数据源、Home、请求者分离时收敛 |
| TC125 | 3N1S；node0 seed，node1/node2 share，node1 onload | V0 → share → spill → read onload → V1 write → final read | 目标行 + 2 conflict lines | fill 完成且 node0 读回 V1 | shared metadata offload/onload |
| TC126 | 3N1S；node1 在 fill 后升级，node2 终读 | share → spill → waiter/fill → Upgrade replay → verify | 目标行 + 2 conflict lines | Upgrade 单次提交且终值匹配 | waiter 保持 Upgrade 语义 |
| TC127 | 3N1S；node0 dirty owner，node1/node2 读取 | dirty seed → pressure → flush/writeback → onload → reads | dirty target + conflict lines | WB 持久化、fill、两次读完成 | dirty writeback 与元数据恢复 |
| TC128 | 3N1S；三节点共享，node1 clean evict 后重访 | share → drain → pressure → clean evict → onload/revisit | shared target + conflict lines | fill 后 node1 重读正确 | clean/shared 元数据恢复 |
| TC129 | 3N1S；node1 更新，node2 二次 onload，node0 终读 | V0 → spill/fill 1 → V1 ownership → spill/fill 2 → read | 同一行两轮生命周期 | 两轮 fill 且 node2/node0 读回 V1 | 重复 spill/fill 与所有权迁移 |

TC120-TC124 的可比较完整场景结果为：TC120 仅保留相对值，spill-noopt/optimized 相对
naive 分别降低 5.37%/5.34%；TC121 为 29,147.33/28,897.67/28,898.67 ticks；
TC122 为 25,265.33/25,270.33/25,266.67 ticks；TC123 为
29,124.33/29,125.00/29,126.67 ticks；TC124 为
15,031.67/15,038.33/15,032.33 ticks，顺序均为 naive/spill-noopt/optimized。
TC125-TC129 没有语义相同的 naive 对照，正式保留 N/A，不据此计算横向百分比。

### B.2 TC130-TC141

| TC | 拓扑/角色 | 阶段与操作序列 | 压力/工作集 | 主测量/完成边界 | 展示能力 |
|---|---|---|---|---|---|
| TC130 | 3N1S；node0 seed/pressure，其他节点复用 | 24 hot → 192 pressure → 4×24 reuse | 24 hot + 192 pressure | 96-op hot reuse；另报 scenario total | 溢出后保留热点副本 |
| TC131 | 8N1S；node0 Home，node1/2 scan/reuse，node1 upgrade | 4096 hot → 98304 pressure → 8192 reuse → 256 upgrades | 102,400 目标线；512K/IdealDir 角色 | 去重容量、已完成 Outer、guest phases | 1.515× 容量及 spill 成本隔离 |
| TC132 | 3N1S；node1 dirty seed，node0 pressure，node2 recover | 8192 seed → 65536 writes → 8192 reads | 8192 active + 65536 pressure | checkpoint-recover 8192 ops；scenario total | dirty metadata/data 恢复 |
| TC133 | 8N1S；node0 seed/pressure，7 个 reader | 4096 share → 65536 pressure → 4096 reuse | 4096 hot + 65536 pressure | frontier-reuse 4096 ops；scenario total | 8 节点共享 frontier 复用 |
| TC134 | 8N2S；16 planes，socket0 pressure、socket1 reuse | seed/share → 每 socket0 8192 writes → window reuse | 16-plane sliding window | window-reuse 4096 ops；scenario total | 8N2S 容量与跨 socket 复用 |
| TC135 | 3N1S；node1 preserved sharer | seed/share → pressure → 24 first loads | 24 hot + 192 pressure | 24 first-revisit samples | preserved sharer 快速重访 |
| TC136 | 3N1S；node1 dirty owner，node2 验证 | dirty seed → pressure → 24 owner stores → reads | 24 hot + 192 pressure | 24 store-complete samples | preserved owner 重复写入 |
| TC137 | 3N1S；node1 先 share，node2 新 requester | seed/share → pressure → 24 new loads | 24 hot + 192 pressure | 24 first-load samples | spilled shared metadata 服务新请求者 |
| TC138 | 3N1S；node1 dirty owner，node2 新 writer | dirty seed → pressure → 24 handoff stores → verify | 24 hot + 192 pressure | 24 handoff store samples | dirty owner handoff 及其成本 |
| TC139 | 3N1S；node1 mixed executor，node2 验证 | seed/share/owner → pressure → 16×16 mixed ops | 16 hot + 192 pressure | 16 个 16-op batch samples | shared/owner 状态批量复用 |
| TC140 | 3N1S；node0 两个 L2 cluster，node2 verifier | setup → 24 cross-L2 stores → verify | 24 lines | 24 store samples | 低时延 cross-L2 控制场景 |
| TC141 | 3N1S；node1 share/write，node2 verify | seed 16 → share → pressure 192 → fill/release → writes | 16 hot + 192 pressure | release/fill/原 reqId 响应及 32 reads | spill 后 shared-writer recovery |

### B.3 TC142-TC147 与 TC217

| TC | 拓扑/角色 | 阶段与操作序列 | 压力/工作集 | 主测量/完成边界 | 展示能力 |
|---|---|---|---|---|---|
| TC142 | 16N1S；每节点 OLTP plane，Home0 | seed → pressure → 32×(28 reads+4 updates) | 每 plane 32 hot；总目标 98,304 lines | 每 plane 1,024-op end-to-end | OLTP buffer-pool 读写混合扩展 |
| TC143 | 16N1S；每节点 B-tree shard | root/internal/leaf/record traversal + sparse update | 每 plane 137 hot；总目标 98,304 lines | 每 plane 2,048-op end-to-end | 层次共享读取与写升级 |
| TC144 | 16N1S；每节点 WAL/data plane | WAL store → data store → checkpoint verify | 每 plane 192 hot；总目标 98,304 lines | 每 plane 1,024 stores，WAL/data 成对完成 | 有序脏写与 checkpoint 语义 |
| TC145 | 16N1S；每节点 FaaS plane | warm runtime/tenant → package pressure → invocations | 每 plane 136 hot；总目标 98,304 lines | 每 plane 2,048-op end-to-end | warm-container 热状态复用 |
| TC146 | 16N1S；每节点 graph plane | frontier/adjacency/property expansion + sparse writes | 每 plane 192 hot；总目标 98,304 lines | 每 plane 2,048-op end-to-end | 图共享前沿与属性更新 |
| TC147 | 16N1S；每节点 feature-store plane | embedding lookup → pressure → accumulator updates | 每 plane 136 hot；总目标 98,304 lines | 每 plane 2,048-op end-to-end | 高偏斜 lookup 与稀疏更新 |
| TC217 | 2N1S；node0 catalog，node1 batch executor | seed → 每批 80 pressure + 14 reads + 2 updates，共 8 批 | 16 keys + 640 pressure | 16-op catalog batch | read-mostly catalog 容量收益 |

---

## 附录 C TC228-TC235 场景明细

| TC | 拓扑/角色 | 阶段与操作序列 | 压力/工作集 | 主测量/完成边界 | 展示能力 |
|---|---|---|---|---|---|
| TC228 | 2N1S；requester、Home、权威数据源 | request → lookup → source data → Home update → completion | 256 KiB L3；100% 压力；5 pairs | remote-read 数据与共享授权可见 | 权威数据定位和共享授权 |
| TC229 | 2N1S；旧 owner、Home、新 owner | request → old-owner recall/release → latest data → grant | 256 KiB L3；100% 压力；5 pairs | 新 owner 获得数据与独占权限 | ownership handoff |
| TC230 | 2N1S；sharers、Home、新 writer | writer request → invalidates → Ack convergence → grant | 256 KiB L3；100% 压力；5 pairs | 全部 Ack 收敛和单写者授权 | shared-to-writer 精确收敛 |
| TC231 | 2N1S；clean sharers | seed/share → pressure → clean shared read | 256 KiB L3；100% 压力；5 pairs | clean_shared_read_service | 干净共享控制路径复用 |
| TC232 | 2N1S；hot-key readers/writer | 32 reads + 16 writes，分别计时后 2/3+1/3 合成 | 256 KiB L3；100% 压力；5 pairs | read/write 完成；复合值计一次权重 | 热点读写综合成本 |
| TC233 | 2N1S；producer/consumer | produce → publish → load → service completion | 256 KiB L3；100% 压力；5 pairs | producer_consumer_service；load 为辅助 | producer-consumer 服务链 |
| TC234 | 2N1S；ordered token participants | enqueue/wait → token store → ordered observe → finish | 256 KiB L3；100% 压力；5 pairs | queued_token_end_to_end；store 为辅助 | 排队 token 有序交接 |
| TC235 | 2N1S；catalog/KV participants | lookup/update service → batch sync → finish | 256 KiB L3；100% 压力；5 pairs | max catalog_kv_end_to_end；service 为辅助 | catalog/KV 批处理 |

---

## 附录 D 四拓扑扩展性数据

TC142-TC147 的独立 spill-noopt 扩展矩阵覆盖 3N1S、3N2S、8N1S、8N2S，24/24
场景完成。下表为全部可用绝对值，单元格格式为 `service / end-to-end ns/op`：

| TC | 3N1S | 3N2S | 8N1S | 8N2S |
|---|---:|---:|---:|---:|
| TC142 | 269.27 / 13,476.17 | 271.75 / 13,240.20 | 271.70 / 13,164.08 | 271.61 / 13,112.36 |
| TC143 | 185.98 / 6,795.73 | 188.43 / 6,675.43 | 188.45 / 6,635.93 | 188.42 / 6,609.07 |
| TC144 | 248.07 / 13,471.54 | 249.31 / 13,224.06 | 249.58 / 13,144.61 | 248.88 / 13,091.06 |
| TC145 | 259.93 / 6,869.90 | 262.31 / 6,749.21 | 262.35 / 6,709.87 | 262.29 / 6,682.98 |
| TC146 | 198.25 / 6,809.79 | 200.73 / 6,688.11 | 200.30 / 6,647.99 | 200.50 / 6,621.61 |
| TC147 | 243.70 / 6,853.80 | 246.14 / 6,733.15 | 246.18 / 6,693.94 | 246.13 / 6,666.93 |

从 3 个 active planes 扩展到 16 个 active planes 时，各 testcase 的 mean plane service
时延保持在相近范围；该表报告绝对扩展性，不与 16N1S profile 对照表混合计算百分比。

---

## 附录 E 接口速查表

| 模块 | 主要入口 | 主要输出 |
|---|---|---|
| UBCCController | read、upgrade、clear、writeback、evict | grant、target set、commit result |
| ResidentDir | lookup、insert、erase、waiter | directory entry、capacity status |
| H64 Backstore | lookup、upsert、erase | persisted directory metadata |
| EP-RNF | read shared/unique、clean unique、snoop | data、snoop response、completion |
| EP-SNF | request service | CHI data/response |
| UBAdapter | send、receive、retry、callback | Outer message、local completion |

---

## 附录 F 术语表

| 术语 | 说明 |
|---|---|
| UBCC 方案 | 由 Outer 一致性层、Home UBCC 控制器、分层目录和 EP 边界组成的跨节点一致性体系结构 |
| UBCC 控制器 | 维护全局目录、串行化同址事务并执行权限仲裁的控制器组件 |
| Home UBCC 控制器 | 由地址映射选定、负责该地址全局目录和事务提交的 UBCC 控制器实例 |
| HA-VI | 指标 3 使用的 VI 协议可执行参考模型 |
| 可执行参考模型 | 使用相同 workload、完成边界和冻结参数运行的协议对照模型 |
| Profile | 一组冻结的目录策略与协议优化配置 |
| ResidentDir | SRAM 驻留目录 |
| H64 Backstore | 位于 metadata DRAM、保存冷目录元数据的 64 B bucket 哈希表 |
| 等效追踪容量 | ResidentDir 与持久化 Backstore 元数据的去重覆盖量 |
| spill-noopt | 使用 H64 Backstore 且关闭时延优化 |
| optimized | 使用 Backstore 并启用时延优化的 profile |
| 核心场景组 | TC228-TC230 等权聚合 |
| 代表场景组 | TC231-TC235 按冻结主值等权聚合 |
| 单向完成语义 | 根操作不以同步 ClearResp 作为完成条件的语义 |
| 配对运行 | UBCC 与 HA-VI 使用相同输入和冻结条件的成对运行 |
| 完成边界 | 根操作开始和结束时刻的共同定义 |
| ubsim |  |
| ub |  |
| naive | 不使用 Backstore 容量扩展和时延优化的基线 profile |
