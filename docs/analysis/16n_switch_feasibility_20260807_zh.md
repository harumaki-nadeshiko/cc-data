# 16N 单级 Abstract Fabric 可行性分析

- 日期：2026-08-07
- 性质：**静态可行性分析（只读）**，不实现 16N 代码，不运行仿真
- 证据口径：本文件全部为仓库内静态事实（源码 / 配置 / runner / 测试 / 文档），证据等级 **E0/E1**（`docs/delivery/acceptance_metrics_deliverables_todo_20260807_zh.md:41-42`：E1=静态代码审阅、接口或路径分析；E0=设计主张无运行证据）。所有结论不得被当作 E2+ 运行证据。
- 前置状态引用：`docs/delivery/acceptance_metrics_deliverables_todo_20260807_zh.md:61` 已把 16N Switch 标为 `TODO`，`:844` 标为 `TODO / E0`，并写明"8N2S/16 planes 不能替代"。

---

## 1. 结论摘要

| # | 结论 | 证据等级 |
|---|---|---|
| C1 | 16N single-stage **abstract fabric**（沿用当前 PDES + networksim 直接转发的行为级互连）**工程可行**，但当前状态为 **TODO/E1**，不是已支持能力 | E1 |
| C2 | 当前 `networksim` 是 **target-ID 直接转发 + full-mesh latency table**，不是真实多跳 / 端口级 switch | E1 |
| C3 | 16N 主要 **P0 blocker** 是 ResidentDir 默认 `sharers_bits=8` 且当前断言禁止 `>10`；默认 Spill/H64 slot 可保存 16-bit sharer，但 legacy schema 的 CompactCodec 固定 10-bit，仍无法表达 node10-15；`ResidentDir.cc:418` 注释中的 `--sharers-bits=10` 也不足 | E1 |
| C4 | `NSIM-NOROUTE` 当前无路由时**静默回落 1ps**（`networksim_main.cc:252`），16N 下必须 **fail closed** | E1 |
| C5 | runner（`tests/e2e/run_multi.sh`）缺 `--16n1s` 拓扑种类 / 16N TC / 16N workload / 16N verifier | E1 |
| C6 | gem5 地址映射断言支持到 16（`CHI_basic_framework_config.py:37-38`）与 `NodeAddressMap::MAX_NODES=16`（`protocol/NodeAddressMap.hh:16`）是**静态能力**，不是 16N E2E 证明 | E1 |

**总判断**：16N 若以 **Level A（抽象单级 switch）** 交付，属可落地的工程项，主要成本是目录 sharers 宽度改造 + 一套 16N 运行/验证链路；若以 **Level B（真实多跳 / 端口级 switch）** 交付，需要重写 networksim 路由/排队模型，工作量和风险显著放大。两条路线见第 4 节，工作包见第 5 节，验收矩阵见第 6 节。

---

## 2. 静态事实核查

### 2.1 networksim 现状：target-ID 直接转发 + full-mesh latency table

关键事实（全部为当前实现，非设计文档）：

1. **入口为 `networksim_main.cc`**（`scripts/build_networksim.sh:45` 只编译该文件），`modules/networksim/NetworkSim.{hh,cc}` / `ForwardTable.{hh,cc}` 是未被该入口使用的旧版（仅 `modules/networksim/main_test.cc` 使用）。
2. **路由表是 (src,dst) 全对延迟表**：`modules/networksim/networksim_main.cc:209-215` `buildRoutes()` 对每条 link 写 `_linkLatency[{src_mod,dst_mod}]` 与反向 `{dst_mod,src_mod}`；注释 `:55-56` 明确 `TODO(2-hop)`："cross-node+cross-socket currently single-hop heterogeneous delay. Revert to multi-hop when nsim supports it"。
3. **转发是按消息的 target-ID 直达目标模块**：`networksim_main.cc:217-277` `step()` 读取 `GetMessageSourceId/GetMessageTargetId`（`MemMessage.hh:22-25`，均为 uint32），按 `(sourceId,targetId)` 查 `_linkLatency`（`:247-250`），随后 `_ports.find(targetId)`（`:256`）**一次性投递到最终目的模块**，无逐跳、无端口选择。
4. **全 mesh 由 `scripts/gen_topo.py` 生成**：`:68-88` 对所有模块对 `a<b` 生成 link（NMOD=16 时为 120 条），`:21-24`、`:75-78` 同样标注 `TODO(2-hop)`；`ForwardTable.hh:16-18` 也写明 "Phase 1: static table, all-to-all connectivity assumed"、`ForwardTable.hh:41-44` `buildFullMesh`。
5. `ForwardTable::nextHop()`（`ForwardTable.cc:17-28`）存在但**未被 active 入口使用**——即当前没有任何下一跳 / 端口级路由逻辑。

**含义**：当前"互连"等价于带中央有界 readyTick FIFO 的完全图直连；它有基础
backpressure，但不建模 per-port 仲裁、serialization/bandwidth、显式扇出/多播或逐跳延迟，
不能声称为真实端口级 switch。

### 2.2 P0 blocker：目录 sharers 位宽（8-bit 默认 / CompactCodec 10-bit / `--sharers-bits=10` 不足）

sharer 位图语义：`UBCCController.cc:1670` `entry.sharersMask | (1ULL << requesterNode)`、`:3030` `entry.sharersMask = (1ULL << requesterNode)`，即 **sharer bit 下标 = node ID**。因此：

- **16N 需要 16-bit 位图**（node0-15 → bit0-15）。
- **SRAM ResidentDir 默认 8-bit**：`modules/ubiomodule/ResidentDir.hh:81` `int sharers_bits = 8;`，只能表达 node0-7；runner 从不传 `--sharers-bits`（`tests/e2e/run_multi.sh:202-311` `ubio_extra_args_for_tc` 中无该参数），E2E 全程用默认 8。位宽进入布局计算：`ResidentDir.cc:73` `entry_fixed = 1+2+1+3+sharers_bits+epoch_bits`、`:117-119` 偏移、`:398-401` 读写。
- **CompactCodec（legacy_schema_a 的 on-wire 格式）固定 10-bit**：`modules/ubiomodule/BackstoreTypes.hh:227` `kMask10=(1ULL<<10)-1`，`:254` `pack` 用 `sharersMask & kMask10`（高位被静默截断），`:289` `unpack` 用 `& 0x3FF`。**bit10-15 无法表达**。
- **守卫断言封死 >10**：`modules/ubiomodule/ResidentDir.cc:416-422`：
  - 注释（`:418`）"For 16-node expansion, --sharers-bits=10 MUST be passed" —— **该建议本身不足**：10-bit 只覆盖 node0-9，仍无法表达 node10-15；
  - `LogAssertIf(_layout.sharers_bits <= 10, ...)`（`:420-422`）—— 即使想传 `--sharers-bits=16` 也会在此被断言拦下。
- **默认 backstore schema 实际是 H64**（`ubio_main.cc:1971` 默认 Auto；`:382` 默认 policy=Spill；`:2072-2078` Auto 在 Spill 下解析为 H64），而 **H64 slot 已用 uint16_t sharers**（`BackstoreSchemaH64.hh:45`，codec `kSharersMask=(1U<<16)-1` `:122`；`BackstoreHostH64.cc:358` `sharersMask & 0xFFFF`）。因此**真正 P0 阻塞点是 SRAM 侧默认 8-bit + `ResidentDir.cc:420-422` 断言 + legacy_schema_a 的 CompactCodec 10-bit 截断**；H64 侧本身可容纳 16 节点。
- 规划文档 `docs/design/ubcc_directory_offload_design.md:32-65` 已定义 16-node canonical entry（`sharersMaskOrWbMask: 16b`），属 **E0 设计主张，未实现**。

**P0 修复方向（见第 5 节）**：`--sharers-bits=16` + 放宽/按 schema 限定断言 + CompactCodec 加 16-bit 变体或强制 H64；并复核 SRAM 容量预算（位宽每 +1，`ResidentDir.cc:94` `entry_bits` 增 1，容量下降，影响目标 1 capacity ratio）。

### 2.3 `NSIM-NOROUTE` 1ps 回落，需 fail closed

`modules/networksim/networksim_main.cc:247-253`：

```cpp
uint64_t lat = 1;
auto lit = _linkLatency.find({sourceId, targetId});
if (lit != _linkLatency.end()) lat = lit->second;
else LogWarn("NetworkSim", "[NSIM-NOROUTE] src={} dst={} falling back to 1ps", ...);
```

当前行为：缺失路由只告警并回落到 1ps。在 16N 下若拓扑/链路表不完整，会**静默把"无路由"当成"1ps 直连"**，掩盖配置错误并污染时延数据。必须改为 **fail closed**（启动时校验全对连通性，或运行时把缺失路由按 fatal/error 处理，保留 `NSIM-MISS`（`:257-263`）的 drop 语义只作为显式配置）。

### 2.4 runner / TC / workload / verifier 缺口

| 缺口 | 静态事实 | 位置 |
|---|---|---|
| 无 `--16n1s` | topo 种类只有 `--1s/--1s-tinydir/--2s/--8n1s/--8n2s/--2n1s` | `tests/e2e/run_multi.sh:84-91` |
| 无 16N topo 配置 | `configs/` 只有 `topo_1s/1s_tinydir/2n1s/2s/8n1s/8n2s.json`，无 `topo_16n1s.json` | `configs/` |
| 无 16N TC | `TESTCASES` 注册表（`tests/e2e/test_e2e.py:21-160`）无 16N 项；`run_multi.sh:1075-1083` `required_topology_for_tc` 只映射 1s/2s/8n1s/8n2s/2n1s | `test_e2e.py:21` 起、`run_multi.sh:1075-1083` |
| 无 16N workload | 8N workload 硬编码规模与 mask：`e2e_tc94_8node_barrier_stress.c:6` `#define NUM_NODES 8`、`:26` `sync_wait(0xFF)`；编译注入 `-DNUM_NODES=${NUM_NODES:-3}`（`scripts/compile_workload.sh:57`） | `tests/e2e/workloads/e2e_tc94_8node_barrier_stress.c:6,26` |
| 无 16N verifier | `tests/e2e/verify.py:30-37` 要求 `--tc`/`--simout`，`:134` 调 `verify_testcase()`，判据在 `test_e2e.py` 内按 TC 硬编码；无 16N 项 | `tests/e2e/verify.py:30-37,134` |

补充（已具备、16N 不需要改的静态能力）：
- 消息头 sourceId/targetId 为 uint32（`framework/MemMessage.hh:22-25`），16 模块 id 无溢出风险；gem5 侧 UBAdapter 以 `gid=node*numSockets+socket`（`gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc:123`）与 networksim 模块 id 对齐。
- gem5 sync_wait barrier 用 32-bit node_mask（`gem5/src/sim/sync_wait.cc:42`），支持 16 节点 mask（0xFFFF）。
- `gen_topo.py` 的 `--nodes/--sockets` 参数已支持任意规模（16N 全 mesh=120 条 link）。

### 2.5 gem5 地址映射"支持到 16"只是静态能力

- `gem5/configs/ruby/CHI_basic_framework_config.py:37-38`：`assert 1 <= num_nodes <= 16`；`:23` `NODE_ADDR_SHIFT=40`；`NodeConfig`（`:97-172`）与 `NodeAddressMap`（`:29-94`）按 `num_nodes` 参数化。
- C++ 侧 `protocol/NodeAddressMap.hh:16` `MAX_NODES=16`、`:15` `NODE_ADDR_SHIFT=40`。
- `tests/e2e/test_e2e.py` split 模式按 `--num-nodes` 构建 per-node 地址/映射（`test_e2e.py:3124-3131, 3160-3164, 3364-3372`）。

这些都是**静态参数化能力**。没有 `topo_16n1s.json`、没有 runner 模式、没有 workload、没有 verifier、没有历史运行 → **不能作为 16N E2E 证明**（结论 C6）。

---

## 3. 现状：与 8N 的差距

已具规模证据的是 8N：`configs/topo_8n1s.json`（8 gem5 + 8 ubio + networksim）、TC82/90-94/133（`run_multi.sh:1079`）、8N workload（`test_e2e.py:83-94`）。16N 相对 8N 的全部新增工作量 = **目录位宽改造（P0）+ 一套 16N 拓扑/TC/workload/verifier**，协议/目录本身无拓扑特判（home 由 PA `node<<40` 决定，见 `protocol/NodeAddressMap.hh:15,22-24`）。

---

## 4. 两条路线

### Level A：抽象单级 switch（abstract fabric，推荐作为默认交付线）

- 定义：**行为级 single-stage fabric**。保留当前 PDES + networksim 进程架构，但把互连语义显式定义为"单级交叉开关"：任意 (src,tgt) 一次投递、延迟由链路/开关矩阵给定；不建模端口级排队与多跳。
- 本质：把当前 full-mesh latency table **显式固化为"单级 switch 延迟模型"**，而不是继续装作通用拓扑。
- 满足：真正 `num_nodes=16` 的功能 / 正确性 / 基础时延 qualification（E3），并给后续 Level B 留接口。

### Level B：真实 switch（real multi-hop / port-level switch）

- 定义：networksim 升级为**端口级多跳 switch 模型**：每模块多端口、路由表下一跳（复用/启用 `ForwardTable::nextHop`）、逐跳延迟累加、入/出端口队列、扇出/多播、背压与拥塞、开关级故障注入。
- 需要：`gen_topo.py` 输出端口级拓扑（非全对直连）、networksim 路由/排队引擎、拥塞与乱序语义、开关故障与 partition 处理。
- 满足：switch 性能语义（hop/serialization/queueing/fanout）可评估，多跳拓扑对比，Level B 验收矩阵。

---

## 5. 最小工作包（WBS）

### Level A（含 P0 修复）

| WP | 内容 | 涉及文件（现状行号） |
|---|---|---|
| A-1 | `--sharers-bits=16` 路径打通：runner 传参 + 断言按 schema 放宽 + 布局/容量复核 | `ResidentDir.hh:81`、`ResidentDir.cc:73,416-422`、`ubio_main.cc:1998-1999`、`run_multi.sh:202-311` |
| A-2 | CompactCodec 16-bit 处理：legacy_schema_a 加 16-bit 变体（或强制 H64）并做 wire 兼容决策 | `BackstoreTypes.hh:227,254,289`、`BackstoreSchemaA.cc:57,129,146,166-168` |
| A-3 | `topo_16n1s.json`（16 gem5 + 16 ubio + networksim）+ `--16n1s` + TC→topo 映射 | `configs/topo_8n1s.json` 模板、`run_multi.sh:84-91,1075-1083` |
| A-4 | 16N workload（barrier mask 0xFFFF + DSM 读写，克隆 TC94 参数化） | `tests/e2e/workloads/e2e_tc94_8node_barrier_stress.c:6,26` |
| A-5 | 16N TC 注册 + verifier 函数 | `test_e2e.py:21-160`、`verify.py:30-37,134` |
| A-6 | networksim 缺失路由 **fail closed**（启动全对连通性校验 / 运行时 fatal） | `networksim_main.cc:247-253` |
| A-7 | 全 mesh 生成校验（16N=120 条）与运行规模验证（16 gem5 并行 init、bind 等待） | `gen_topo.py:68-88`、`run_multi.sh:754-774` |

### Level B（在 A 之上）

| WP | 内容 | 涉及文件 |
|---|---|---|
| B-1 | networksim 端口级模型：路由表下一跳、逐跳延迟、端口队列 | `networksim_main.cc:209-215,217-277`、`ForwardTable.cc:17-28` |
| B-2 | 拓扑格式升级：输出端口/链路而非全对直连；多跳路径计算 | `gen_topo.py:10-12,21-24,68-88` |
| B-3 | 开关级拥塞/背压（复用 `EP_PORT_HWM` 语义）与扇出/多播 | `run_multi.sh:39` 注释、`networksim_main.cc:61,226` |
| B-4 | 开关级故障注入（drop/dup/delay/reorder per port） | `run_multi.sh:104-199` `fault_rules_for_tc` |
| B-5 | 多跳时延链校验（对齐现有 nsim RECV/FWD 记账） | `docs/measure/latency_design.md:224-225` |

---

## 6. 验收矩阵

| Gate | Level A 验收 | Level B 验收 |
|---|---|---|
| 功能正确性 | 16N TC（barrier + DSM rw）在 `topo_16n1s` 下 `verify.py` `>>> PASSED <<<`，全节点 READ_VAL MATCH | 同 A + 多跳路径下结果一致 |
| 目录/backstore 正确性 | 16 节点 sharers 全位图无截断；G_E/G_M one-hot 不变量在 node10-15 成立；spill/recall 跨 node10-15 | 同 A |
| 安全（fail closed） | 缺失路由导致显式失败而非 1ps 回落；`NSIM-NOROUTE` 不再静默 | 同 A + 端口路由完整性校验 |
| 时延 | 16N 全 mesh 单跳延迟语义正确（非性能 claim） | 逐跳/串行化/排队语义可评估 |
| 容量（目标 1 复核） | sharers_bits=16 后 capacity ratio 复核不破目标 1 | 同 A |
| 故障 | 16N 下注入用例回归（drop/dup/delay/reorder） | 开关端口级注入回归 |
| 证据 | E3（TC PASS + 配置/oracle 明确）；冻结 manifest（E5）后续 | E3+ |

---

## 7. 风险

1. **目录位宽改造的容量涟漪**：`ResidentDir.cc:94` entry_bits 随 sharers_bits 增长 → SRAM 容量下降，可能冲击目标 1 capacity ratio（默认 60KiB bloom + 512KiB SRAM 预算，`ResidentDir.hh:50-55`）。需重新跑容量/时延矩阵。
2. **backstore wire 兼容**：CompactCodec 改 16-bit 破坏 legacy_schema_a 既有 on-DRAM 数据；H64 已是 16-bit 但需确认 E2E 默认链路全程 H64（`ubio_main.cc:2072-2078` 依赖 policy=Spill）。
3. **`--sharers-bits=10` 建议性注释误导**（`ResidentDir.cc:418`）：若照做，node10-15 静默丢位（pack 截断 `& kMask10` 或 SRAM 写溢出），属**静默数据损坏类**风险，P0 修完前禁止 16N 运行。
4. **16N 运行规模**：16 gem5 + 16 ubio + networksim 进程，init/bind 等待上限 300s/节点（`run_multi.sh:758`），PDES 串行化与日志量放大（可参考 8N 治理手段，`run_multi.sh:31-39`）。
5. **Level B 语义成本**：真实 switch 引入拥塞/乱序，可能与现有协议层"全局有序假设"交互，需要新的 ordering domain 验证。
6. **8N2S/16 planes ≠ 16N**：16 个 socket-plane 的 8 节点不等于 16 节点目录/地址空间（`acceptance_metrics_deliverables_todo_20260807_zh.md:61`），不能以 8N2S 结果顶替。

---

## 8. 复杂度区间（人日，区间而非伪精确）

| 路线 | 区间 | 依据 |
|---|---|---|
| Level A（含 P0） | **约 2–4 人周** | A-1/A-2（目录位宽+codec）为主，A-3~A-5 为既有模式的复制/参数化，A-6/A-7 为加固与规模验证 |
| Level B | **约 2–4 人月** | 路由/排队/拥塞引擎重写（B-1~B-4）+ 多跳语义与故障验证（B-5） |

注：估值为粗粒度范围（对齐 `chatgpt_work_research_prompt_20260807_zh.md:710` 的要求），取决于 sharers 位宽是否复用 H64 通道（可显著减少 A-2）。

---

## 9. 不得声称的内容

1. 不得声称"当前已支持 16N"或"16N 可行"之外的任何运行结论——当前仅 E1 静态证据。
2. 不得声称当前 networksim 是"switch"或具备端口级/多跳语义。
3. 不得声称 `--sharers-bits=10` 足够支持 16N（10-bit 只覆盖 node0-9，node10-15 不可表达）。
4. 不得声称 gem5 地址映射断言 `<=16`（`CHI_basic_framework_config.py:37-38`）即 16N E2E 通过。
5. 不得声称 16N 的任何性能/时延/容量结论（目标 1/2/3 的 16N 口径均未运行）。
6. 不得声称 8N2S/16 planes 或 8N 结果可替代 16N qualification。
7. 不得把 `docs/design/ubcc_directory_offload_design.md:32-65` 的 16-node entry 当作已实现。

---

## 10. 建议决策门（Decision Gates）

- **Gate 0（范围决策，本周）**：确定 Level A 还是 Level B；确定 sharers 策略（H64-only 复用 vs CompactCodec 16-bit 扩展）；给出甲方 16N 是否属本期验收的书面结论。
- **Gate 1（Level A 功能，P0 修复后）**：A-1~A-6 全部落地，16N TC PASS（E3），`NSIM-NOROUTE` 不再静默。
- **Gate 2（Level A 性能/容量复核）**：sharers_bits=16 下目标 1/2 矩阵复测，容量 ratio 不破线。
- **Gate 3（Level B 立项）**：仅当 Level A 证据不足以支撑 switch 语义评估时启动；以 B-1 路由引擎原型 + 多跳对比为出口。
- **Gate 4（冻结）**：E5 manifest（配置/二进制/原始证据/多轮统计），对齐 `acceptance_metrics_deliverables_todo_20260807_zh.md:848` 的 Reproducibility 门槛。

---

## 11. 对当前项目状态的影响

- 在 Gate 1 完成前，16N 保持 `TODO/E0-E1`（与 `acceptance_metrics_deliverables_todo_20260807_zh.md:61,844` 一致）。
- P0 修复（sharers 位宽）是**独立于拓扑的协议数据面改造**，即使不启动 16N，也建议在本期做位宽守卫修正（防止任何 `num_nodes>=11` 或未来扩展时的静默截断）。
- 本文件不修改任何代码、配置或运行环境；如需进入实施，按第 5 节 WBS 另行立项。
