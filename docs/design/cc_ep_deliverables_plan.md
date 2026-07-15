# CC-EP 阶段验收交付方案

版本 0.3（分阶段执行方案）— 2026-07-16

---

## 0. 分阶段执行总览（Phase Execution Plan）

> 本节为全文导航。各阶段按依赖关系排列，每个子任务标注难度（★~★★★）、负责人、
> 预估行数。详细技术方案见相应 §5 / §7 / §8。

### Phase 0 — 基础设施（参数迁移 + 开关接线）前置于所有实验

| # | 任务 | 难度 | 行数 | 来源 | 验收 |
|---|------|------|------|------|------|
| **0.1** | **env var → argv 迁移（ubio 侧）**：`ubio_main.cc` 扩展 arg 解析，支持 `--bloom-bytes` / `--sram-bytes` / `--sharers-bits` / `--epoch-bits` / `--ways` / `--set-bits` / `--dram-delay-ps` / `--batch-rs` | ★★ | ~30 | §7.3 | `ubio --help` 输出所有新 arg；env var 仍作 fallback |
| **0.2** | **env var → SimObject Param（gem5 侧）**：`EPRNFController.py` / `EPSNFController.py` / `UBAdapter.py` / `EPBackend.py` 加 Param 声明；对应 `.cc` 读 `_params` 替代 `getenv`；`CHI_ubcc_framework.py` 透传 | ★★ | ~60 | §7.3 | 编译通过；gem5.opt 启动无 crash；env fallback 仍可用 |
| **0.3** | **`test_e2e.py` `--` 分隔 + 新 script args**：`test_e2e.py` 加 `--` 分隔（gem5 params -- workload params）；新增 `--silent-upgrade` / `--ep-retry-cycles` 等 script args | ★★ | ~30 | §7.3 | `test_e2e.py --help` 输出新参数；`--tc 29 --silent-upgrade=1 -- --node 0` 正确拆分 |
| **0.4** | **C4 Direct-Forward 开关接线**：`EPBackend.cc` 加 `UBCC_DIRECT_FWD` env/arg 门控 | ★ | 5 | §5.2.6 | `UBCC_DIRECT_FWD=0` 跑 TC101，C4-FORWARD 日志消失 |

**Phase 0 验收总则**：所有 env var 参数均可通过 argv/SimObject 传入，原 env var 仍作为 fallback（向后兼容）。`test_e2e.py` 能正确分离 script params 和 workload params。

---

### Phase 1 — 协议实现 + 测量工具 + 对照实验（产指标 1/2 核心数据）

| # | 任务 | 难度 | 行数 | 来源 | 验收 |
|---|------|------|------|------|------|
| **1.1** | **静默升级开关实施**：`EPRNFController.cc` `handleSnpCleanInvalid` R_E 分支加 `EP_SILENT_UPGRADE` 门控（开关打开 = 本地静默升级 + 立即 SnpResp_I） | ★★ | ~20 | §5.2.5 | TC29 跑开关对照：on 模式无跨节点 OuterUpgradeReq；off 模式有；均收敛 |
| **1.2** | **ResidentDir 空间 microbench**：`tools/resident_dir_bench.cc`，给定 config → 打印 capacity / dir_bytes / FPR@n | ★★ | ~60 | §7.3 | 输出 CSV："SRAM 预算,capacity,FPR@1K,FPR@10K"；指标 1 的"提升 X%"由 CSV 计算 |
| **1.3** | **`latency_compare.py`**：输入 baseline/optimized 两 run 的 chain JSON → 对照表（场景 | P50/P99 | 降幅%） | ★★ | ~80 | §7.4 | 输出对照 CSV；指标 2 的降幅%在此产出一句结论 |
| **1.4** | **事务类型分组（trace2chain）**：chain 分类器（ReadShared / ReadUnique / Upgrade / Recall） | ★★ | ~40 | §7.4 | 汇总表按场景分组（跨节点读缺失 / 独占写升级 / 多 sharer 读） |
| **1.5** | **静默升级 TC29 对照实验**：跑两次 + `latency_compare.py` | ★ | 0（复用） | §7.5 | **指标 2 实测数据**：降幅 ≥90% vs baseline |
| **1.6** | **复用 workload 延迟采集**：TC80/100/101 各跑一次 + 延迟采集 | ★ | 0（复用） | §7.5 | **指标 1/3 性能数据**：三种场景的实测延迟基线 |

**Phase 1 验收总则**：指标 2 的核心数据——静默升级 on/off 对照有实测延迟降幅 ≥90%（≥10% 即达标）。

---

### Phase 2 — 文档撰写 + 形式化刷新（产三份交付件）

| # | 任务 | 难度 | 行数 | 来源 | 验收 |
|---|------|------|------|------|------|
| **2.1** | **交付件 1：协议总纲** `docs/design/cc_ep_protocol_overview.md` | ★★ | ~200 | §3.1 | 覆盖体系结构 / EP-RNF-UBCC-CHI 接口 / 方案对比矩阵 |
| **2.2** | **交付件 2：形式化+可靠性+HA 对比** 汇整 §5.1/§5.3/§5.4/§5.5 + §7.1-7.2 | ★★★ | ~300 | §3.2 | 含 TLA+ 刷新证据 / 故障 drop 实验结果 / HA 三论据 |
| **2.3** | **交付件 3：性能报告 + 接口说明书** | ★★ | ~250 | §3.3 | 指标 1/2/3 数据 + UBCC/EP-RNF/EP-SNF/UBAdapter 接口说明 |
| **2.4** | **形式化 fanout 刷新**：`ubcc_protocol_core.tla` 对 fix1/fix2 更新 + 重跑 safety | ★★ | ~30 | §7.1 | TLC 16/16 action 覆盖不退化；depth≥23 states 不超时 |
| **2.5** | **fidelity 映射表更新**：`fv_coverage_fidelity.md` A3.1 对齐行号 + A3.3 追加 snoop 仲裁未覆盖 | ★ | ~15 | §7.1 | A3.1 所有 C++ anchor 可 `rg -n` 验证到准确行号 |

**Phase 2 验收总则**：三份交付件完整可提交评审；TLA+ 模型与当前 C++ 代码语义一致（fanout effectiveMask / upgrade-barrier）。

---

### Phase 3 — P1 提升项（强化交付质量）

| # | 任务 | 难度 | 行数 | 来源 | 验收 |
|---|------|------|------|------|------|
| **3.1** | **drop 类 E2E TC**：基于 TC5/TC3 加一条 `drop::ClearReq` 规则，验证重试自愈 | ★ | ~5 | §7.2 | TC 通过 + `[UBFAULT]` marker 出现 + 值收敛 |
| **3.2** | **静默升级故障免疫 TC**：on/off 双态对 OuterUpgradeReq 注入 drop，对比 `[UBFAULT]` | ★ | ~5 | §7.2 | on 态：零 `[UBFAULT]`；off 态：有 `[UBFAULT]` + 重试自愈 |
| **3.3** | **reorder 运行时实现**：`ubio_main.cc` 加延迟重排队列（~50-80 行） | ★★ | ~60 | §7.2 | TC 注入 reorder 规则 → `[UBFAULT]` 命中 + 值收敛 |
| **3.4** | **ResidentDir 性能计数器 + JSON 导出**：补 hit/miss/evict/bloom-FP 计数器 | ★★ | ~40 | §7.3 | UBCCController JSON 输出含 `dir_hits/dir_misses/evictions/fp_count` |
| **3.5** | **关键路径 vs 总时延分离展示**：`chain2html` + `latency_compare` 加关键路径列 | ★★ | ~30 | §7.4 | 对照表有 `critical_path_ns` 独立列 |
| **3.6** | **TBE 干扰 workload**：基于 TC35 改造，混合本地密集 + 跨节点目录并发 | ★★ | ~80 | §7.5 | P99 延迟在 UBCC vs HA-C（模拟）下有可量化差距 |
| **3.7** | **文档标注 RAS 限制**：交付件 2 标 `ras_fault_injection_plan.md` 未落地 | ★ | ~5 | §7.2 | 标注段落出现在最终交付件中 |

**Phase 3 验收总则**：drop/reorder 故障注入有 E2E TC 覆盖且通过；关键路径分离展示可产出一句"CC-EP 关键路径时延 ≤ HA-C 理论最小"的证据。

---

### Phase 4 — P2 可选 / 下阶段

| # | 任务 | 难度 | 行数 | 来源 | 验收 |
|---|------|------|------|------|------|
| **4.1** | CompactCodec 核查 | ★ | ~5 | §7.3 | 16 节点前 `assert(sharers_bits>=10)` |
| **4.2** | 清理 UBLAT 死链 | ★ | ~5 | §7.4 | 文件标记 deprecated / 删除 |
| **4.3** | 日志工具文档 | ★ | ~50 | §7.4 | `trace2chain→latency_compare` 使用手册 |
| **4.4** | snoop 仲裁形式化 | ★★★ | — | §7.1 | TLA+ 模型新增 STALE/IMMED action（或标注下阶段） |
| **4.5** | 静默升级微基准 | ★ | ~30 | §7.5 | 紧循环放大升级路径占比 |
| **4.6** | delay 真实化 | ★ | ~20 | §7.2 | 伪 delay 改为 deferred enqueue |

**Phase 4 不需要 Block 前三阶段**——可在 Phase 1-3 推进期间根据资源灵活穿插。

---

### 依赖关系图

```
Phase 0 (基础设施: argv迁移 + 开关接线)
  ├─→ Phase 1.1 (静默升级)         需 0.2, 0.3
  ├─→ Phase 1.2 (ResidentDir bench) 需 0.1
  ├─→ Phase 1.3 (latency_compare)   无依赖
  ├─→ Phase 1.4 (事务分组)          无依赖
  ├─→ Phase 1.5 (TC29 对照)         需 0.3, 1.1, 1.3
  └─→ Phase 1.6 (workload 延迟)     需 0.3, 1.3

Phase 2 (文档 + 形式化)
  ├─→ 2.1-2.3 (三份交付件)         需 Phase 1 实验数据
  └─→ 2.4-2.5 (形式化刷新)         无依赖, 可并行

Phase 3 (P1 提升)                   无 Block 依赖 Phase 2
Phase 4 (P2 可选)                   无 Block 依赖
```

**建议并行策略**：
- 0.1/0.2/0.3 一人做完后，Phase 1 的 1.2+1.3+1.4 可三人并行。
- 1.1 在 0.2+0.3 完成后一人做。
- Phase 2 文档可边实验边起草，不等 Phase 1 完成。
- Phase 3 的 3.1/3.2/3.6（workload 类）可与 Phase 1 并行用 futsu-guider。

---



---

## 1. 协议定位与核心方案

### 1.1 体系结构

CC-EP 是基于 ARM CHI 的跨节点 Cache Coherence 方案。核心思想：

- **Inner 域（节点内）**：标准 gem5 CHI 协议，由每节点 HN-F 做本地一致性管理。每个 CPU socket 有独立的 HN-F。
- **Outer 域（跨节点）**：每个节点运行一个 UBCC（`UBCCController`，在 ubio 进程中），作为该节点 PA 范围
  的**全局 Home Agent / 目录**。UBCC 维护全局 sharer 状态，协调跨节点 recall/invalidation。
- **EP-RNF**（`EPRNFController`）：本地 HN-F 的一个特殊 RN-F。它是"外部世界在 CHI 域内的代理"——
  本地 HN-F 把外部世界当作一个 sharer，通过 snoop EP-RNF 来向外传播/查询一致性。
- **EP-SNF**（`EPSNFController`）：响应本地 CPU 的 ReadNoSnp 请求，把对外层目录的访问路由到 UBCC。

### 1.2 关键技术决策

| 决策 | 简述 |
|------|------|
| **UBCC 做全局目录** | 每节点一个 UBCC，维护 ResidentDir（SRAM）+ Bloom Filter + MetaRNF DRAM 卸载。Bloom Filter 过滤"不在远程"的行，Resident Dir 只追踪真正跨节点的行，提升 SRAM 等效追踪容量 |
| **EP-RNF proxy 模式** | EP-RNF 作为本地 HN-F 的 sharer，被 snoop 时代表外部世界响应。收到 SnpCleanInvalid 时代外部世界走 OuterUpgradeReq 失效远程副本（hold snoop 等外层），或通过 STALE-retry 让本地写 abort-retry 经全局序重排 |
| **Push-grant** | home 侧 grant 就绪时主动推送 ReadResp 给 requester，消除 pull 轮询延迟 |
| **snoop 冲突仲裁** | EP-RNF 有 in-flight CHI 事务时收到同址冲突 snoop，按 3×3 矩阵（三类 in-flight × 三类 snoop）仲裁——良性 self-snoop 即时响应 clean SnpResp_I，冲突写意图 snoop 回 stale SnpResp_I 让本地写 abort-retry |
| **指数退避** | held-upgrade snoop 的 retry 间隔不再固定 500µs，改用独立指数退避（5→10→20→…→200µs），消除 TEMP-REJECT 不必要等待 |

### 1.3 已修复的 protocol bugs（死锁#1/#2）

| 死锁 | 根因 | 修复 |
|------|------|------|
| **死锁#1**（UBCC 层） | ReadReq 去重守卫 `_inflightReadReqs` 只删不增导致 10ns 重发风暴；home 逆 inval 目标含 stale sharer | 补 insert；home 统一 fanout + 无副本立即 ack |
| **死锁#2**（EP-RNF snoop 排队） | EP-RNF recvSnoopMsg 无差别排队，跨节点写-写竞争时形成环形等待 | 冲突分类仲裁（STALE/IMMED 矩阵）+ stale-retry |
| node7 Finish_CleanUnique assert | CPU requestor 的 is_stale 只对 EP-RNF 设，CPU 路径不设 | 补 `if (!is_stale) is_stale := true` |
| barrier floorLocalExpected 回归 | `floorLocalExpected=_numSockets` 误伤 2s workload（每节点 1 primary） | 删 floor，改为 workload 显式双参 `sync_wait(mask, NUM_SOCKETS)` |

全部验证：71/71 testcase PASS（0 crash），含 TC98 ROUNDS=16 全量通过。

---

## 2. 方案对比（UBCC vs HA）

### 2.1 HA 方案分类

| 方案 | 目录位置 | 核心差异 |
|------|------|------|
| **HA-A** | 每节点 HN-F 自管，无全局目录 | 跨节点 snoop 需 fanout 到所有远程节点（无过滤），8 节点 = 每次写 7 个 fanout |
| **HA-B** | 集中 home node 的 HN-F | 多一跳（请求→集中 home→fanout），有全局目录但集中瓶颈 |
| **HA-C** | 分布式目录（PA hash → home HN-F），目录存在 CPU 的 HN-F 里 | 每个节点的 HN-F 管理部分 PA 范围的全局目录，UBIO 做纯通道 |

当前 CC-EP 方案类似于 HA-C，但**将全局目录从 HN-F 移出到 UBCC（ubio 进程中）**，消除 HN-F 内目录与计算竞争的干扰，并通过 Bloom Filter 降低目录 SRAM 占用。

### 2.2 相对 HA 的优势（讨论中）

- **时延**：UBCC 与节点内 gem5 同进程，home 侧查找免 IPC。HA-C 的 home 在 HN-F 内也不用 IPC——其实两者时延相同（跨节点部分都是 1 跳网络 RTT ≈1.1µs，节点内 CHI 操作 ≈78ns 可以忽略）。**时延上 UBCC 和 HA-C 无明显差异。**
- **SRAM 效率**：UBCC 使用 Bloom Filter + ResidentDir 减少不必要的目录条目，HA-C 的目录直接占用 HN-F 的 TBE/目录 SRAM——容量上 UBCC 有 Bloom 带来的等效追踪范围提升。
- **职责分离**：UBCC 把全局 CC 逻辑从 gem5 CHI 状态机分离到独立进程，方便独立调试/升级/故障隔离。

**当前状态**：HA 时延对比已定案——重定义为"等时延 + 结构性优势"（见 §5.1）。

---

## 3. 交付件与任务分解

### 3.1 交付件 1：协议理论分析 + 方案对比

- 顶层协议总纲文档（串联 EP-RNF/UBCC/CHI 接口设计 + 个方案对比矩阵）
- 待写：`docs/design/cc_ep_protocol_overview.md`

### 3.2 交付件 2：形式化验证 + 可靠性模型 + HA 时延对比

| 子项 | 状态 | 待做 |
|------|------|------|
| **形式化验证**（UBCC core） | 已有完整 TLA+ 套件（`verification/tla/`，9 模型，safety+liveness PASS），但模型止于 07-11，落后于代码 07-14/07-15 更新 | 刷新 core fanout 语义 + fidelity 映射表（见 §7.1） |
| **形式化验证**（EP-RNF 3×3 snoop 矩阵） | 已有初版（`docs/design/eprnf_snoop_conflict_arbitration_plan.md` §9） | 新增 snoop 冲突仲裁 action，或标注 E2E 覆盖、形式化留待下阶段（§7.1） |
| **可靠性模型** | 丢包/乱序/重传——TLA+ `ubcc_transport_faults.tla` 已枚举；运行时 drop/dup 已实现，reorder 未实现 | 写文档 + 补 drop 类 E2E TC（§7.2） |
| **节点故障** | 无实现，无计划在本阶段补设计 | 文档中标注"已知限制" |
| **HA 时延对比** | **已定案（见 §5.1，重定义为等时延+结构优势）** | 按 §5.1 论据 + §7.5 workload 出对比数据 |

### 3.3 交付件 3：仿真代码 + 性能验证

- 仿真代码：已完成（71/71 PASS）
- 接口调用说明书：待写
- 验证报告：
  - **指标 1**（SRAM 容量）：Bloom Filter 等效追踪容量 vs 纯 SRAM 基线（含 MetaRNF DRAM 卸载）→ 待计算（测定方法见 §7.3）
  - **指标 2**（CC 同步时延降低 10%）：**已定案 baseline = 静默升级（见 §5.2）**，用 TC29 开关对照（§7.5）
  - **指标 3**（CC 同步 ≤ HA 理论时延 + 结构优势）：**已定案（见 §5.1）**

### 3.4 16 节点

标为"拓扑设计已完成、仿真留待下阶段"。

---

## 4. 验收指标映射

| 指标 | 内容 | 当前状态 | Baseline | 优化后 | 达标 |
|------|------|------|------|------|------|
| **指标 1** | 512KB SRAM 下 Cacheline 追踪数提升 ≥50% | 需算 Bloom 等效覆盖（测定法 §7.3） | 纯 SRAM（无 DRAM 卸载），满则 evict 全局副本 ≈69K 行 | Bloom + ResidentDir + DRAM 卸载 → 等效追踪 >> 69K | 待计算 |
| **指标 2** | CC 同步时延降低 ≥10%（CC 时延 ≥500ns 场景） | **已定案** | 静默升级关闭：R_E 写升级发跨节点 OuterUpgradeReq ≈810ns | 静默升级开启：R_E 本地升级 ≈78ns（零跨节点） | ≈90% ✅（待实测复核） |
| **指标 3** | CC 同步 ≤ HA 理论时延 + 结构优势 | **已定案** | HA-C（目录在 HN-F，占 TBE） | UBCC（目录外置）等跳数 + 本地干扰更低 + C4/Batch-RS | 待实测（§5.1 三论据） |

---

## 5. 开放问题的解决方案（已定案）

> 本节原为"待专家确认的两个开放问题"。经代码调研后，已收敛为可通过方案评审的定案，
> 并补充了三个支撑性讨论（§5.3 Clear/ClearAck 设计动机、§5.4 关键路径优化分类、
> §5.5 目录压缩机制对比）。工程落地方案见 §7。

### 5.1 指标 3（HA 时延对比）：重定义为"等时延 + 结构性优势"

**核心判断（诚实定位）**：不试图论证"UBCC 跨节点比 HA-C 更快"——因为跨节点跳数结构相同，
硬凑 winner 会被评审识破。改为一个可辩护的复合命题。

#### 5.1.1 为什么跨节点时延无法拉开差距（定量依据）

关键路径分解（`docs/measure/tc98_optimization_analysis.md:143-179`，`scripts/gen_topo.py:39`）：

- 跨节点读/写缺失关键路径 = **4 个跨节点单跳**（req→home、home→owner recall、owner→home resp、
  home→req grant）× 405ns ≈ **1620ns，占总时延 52%**，是绝对主导项。
- UBCC 目录在 ubio 进程、HA-C 目录在 HN-F 内——**两者都本地进程内、免 IPC**，量级 ~78ns，
  相对 405ns/跳可忽略。
- CC-EP（HA-C 类）与标准 HA-C 都是"分布式目录 + PA hash → home"，**跨节点跳数逐跳相等**。

#### 5.1.2 指标 3（修订版定义）

> **指标 3（修订）**：在相同跨节点网络参数下，CC-EP 的跨节点 CC 同步时延 **≤ HA-C 理论最小时延**
> （即等跳数、无协议退化），且在两个 HA-C 结构性劣势维度上严格更优：
> **(i) 本地 TBE/SRAM 干扰**、**(ii) 已实现的通信削减优化（C4 Direct-Forward / Batch-RS）**。
> 指标 3 的定量支撑由这两个结构性差异承担，而非跨节点跳数。

#### 5.1.3 三个可定量的论据

| 论据 | 内容 | 可测性 |
|------|------|------|
| **论据 1（等时延）** | 逐跳关键路径对照表：CC-EP 与 HA-C 跨节点 4 单跳逐跳相等；本地部分 CC-EP ≤ HA-C（UBCC 目录查找不与 HN-F 计算管道争流水级） | 用 §7.4 log 工具分段测定 |
| **论据 2（TBE/SRAM 干扰，CC-EP 严格更优）** | HA-C 全局目录寄生 HN-F 内，占用 HN-F 的 TBE 表项，与本地 CPU 请求争用同一 TBE 池 → 高负载下本地请求排队/重试；UBCC 独立进程，零占用 HN-F TBE | 混合 workload（本地密集 + 跨节点目录并发）对比本地请求 P99（§7.5） |
| **论据 3（通信削减，CC-EP 已具备）** | C4 Direct-Forward（`EPBackend.cc:1109`）、Batch-RS（`UBCCController.cc:2839`）在标准 HA-C（目录在 HN-F）中需侵入 CHI 状态机才能实现；对应 workload 下 CC-EP 有可测降幅（Batch-RS 对 read contention 10-18x，`tc98_optimization_analysis.md:302`） | TC100/TC101 复用（§7.5） |

**达标论证**：指标 3 = "CC-EP 时延 ≤ HA-C 理论最小（论据1）∧ 本地干扰更低（论据2 可测）∧
通信更省（论据3 可测）"，配合指标 1 的 SRAM 效率数据，构成"不牺牲时延、还省 SRAM、还减干扰"
的完整命题。**不再声称"跨节点更快"这个无法成立的强命题。**

### 5.2 指标 2（Latency Baseline）：主方案 = 静默升级（Silent Upgrade）

**核心判断**：采纳原 §5.2 的自我批评——**retry 参数级调整（CompData backoff）降级为"工程调优"，
不作为指标 2 正式 baseline**（"故意劣化 baseline"缺乏说服力）。改用一个真正的协议级 trade-off：
**独占持有者（G_E / 本地 R_E）的写升级从"无条件跨节点 OuterUpgradeReq"优化为"本地静默升级"。**

#### 5.2.1 为什么这是"novel and sound"的协议级优化

这是 **MESI→MOESI 家族最经典、最无争议的协议优化**在跨节点场景的对应物：单节点 MESI 中
E→M 是静默升级（Exclusive 态本地写直接转 Modified，无需上总线）。CC-EP 当前**没有做这个静默升级**——
反而无条件发跨节点 OuterUpgradeReq 问 home。这是真实的协议设计 trade-off（保守 vs 优化），
不是实现 bug。

#### 5.2.2 代码依据（本地可判定性已具备）

- requester 侧 EPBackend **已维护本地权限书签** `RequesterLineState`，其中
  **`R_E` = Clean exclusive owner permission**（`EPBackend.hh:268`）。本地是 R_E holder 时，
  本地就知道自己是全局唯一 owner——**无需问 home**。R_E 的语义由目录 G_E one-hot 不变量
  （`ResidentDir.cc:20-26`：`isExclusive() && popcount==1`）保证。
- 但当前实现：本地 store 命中 → HN-F 发 `SnpCleanInvalid` → EP-RNF `handleSnpCleanInvalid`
  **无条件**发 `OuterUpgradeReq` 到 home（`EPRNFController.cc:820-849`），hold snoop 等
  `OuterUpgradeAck`。
- home 侧 `processOuterUpgradeReq`：即使 `effectiveMask==0`（无其他 sharer）也只走 fast-path
  Ack(true)（`UBCCController.cc:2052-2068`）——**但请求已经跨节点往返到 home 了**，白白花 1 个 RTT。

#### 5.2.3 Baseline vs Optimized 对照

| | Baseline（当前实现） | Optimized（静默升级） |
|---|---|---|
| R_E holder 本地写升级 | 无条件发 `OuterUpgradeReq` → home → `OuterUpgradeAck` = **1 跨节点 RTT** | requester 侧检测 `R_E` 书签 → 本地静默升级 R_E→R_M，EP-RNF 直接 `SnpResp_I`，**0 跨节点消息** |
| 跨节点消息数 | 2（Req + Ack） | 0 |
| 时延 | ≈ 1 RTT ≈ 810ns（2×405）+ PDES sync，**≥500ns 成立** | ≈ 本地 CHI 管道 ~78ns |

**达标性**：(810 − 78) / 810 ≈ **90% 降幅** >> 10% ✅。即便只算最保守单跳 405ns，
(405−78)/405 ≈ 80% 也远超 10%。发生场景（独占持有者转写）普遍且高频（read-modify-write、
锁获取后写、私有数据写）。

#### 5.2.4 正确性（评审必问）

静默升级**只在 requester 本地确知 R_E 时触发**：
- 本地 E→M 升级不改变任何其他节点可见状态（无副本需失效，R_E 已保证全局唯一 owner 且无 sharer）；
- home 目录已是 G_E owner=本节点，M 与 E 对"其他节点"完全等价（都是"独占，别人不能碰"）——
  脏/净差异是本地事务，home 只需在后续 writeback/recall 时知道（write recall 路径 `EPBackend.cc:1145`
  已处理）；
- **零跨节点消息 → 天然免疫丢包/乱序/重传**——不产生 outstanding、无 Clear、无 epoch 递增需求。
  这与 Eager Commit 正相反（§5.3 说明为何 Eager Commit 危险），也正好呼应可靠性模型的关切。

#### 5.2.5 实现方式（编译期/env 开关做对照实验）

- 在 `EPRNFController::handleSnpCleanInvalid`（`:820`）的 DSM 升级分支入口加门控：
  查询 EPBackend 本地书签，若为 `R_E` 且开关 `EP_SILENT_UPGRADE` 开启 → 本地升级 R_E→R_M +
  立即 `SnpResp_I`，**不发** OuterUpgradeReq。
- 开关关闭（baseline）= 当前无条件跨节点行为；开关打开（optimized）= 静默升级。
- **一个开关即可构造 baseline/optimized 对照，无需劣化任何东西**——baseline 就是现有的、
  正确的、保守的实现。改动量小（EP-RNF 一处分支 + 读 EPBackend 现成书签），风险低。

#### 5.2.6 备选方案（若专家认为静默升级"节点内味道太重"）

**MESIF-Forward / MOESI-Owned**：让 G_S/G_M holder 直接向后续 reader 转发，省 home recall 往返。
CC-EP 已有 C4 Direct-Forward 基础设施（`EPBackend.cc:1109`），可作 optimized，baseline 关闭 C4
（走 home 中转）。**注意**：调研发现 `UBCC_DIRECT_FWD` 开关文档声称可关、但**代码未接线**
（无 `getenv`，C4 始终启用，见 §7.2/§7.4 待办），若走这条路需先补开关接线。

#### 5.2.7 明确降级项

- **retry 参数级调整（CompData 10µs backoff / EP-SNF retry cycles）降级为"工程调优"**，
  仅在附录中作为背景说明，不作为指标 2 正式对照。
- 504µs slow-path、pull-grant 轮询 —— 维持已否决（前者是 bug，后者过于 weak）。

### 5.3 支撑讨论 A：为什么需要 Clear / ClearAck（提交屏障的设计动机）

> 评审很可能追问"Clear 是不是冗余往返、能不能省"。此处主动说清，并解释为何 Eager Commit 危险。

#### 5.3.1 Clear 要解决的根本问题：grant "在途"期间目录该反映什么状态？

UBCC 用 **reserved-epoch 两阶段提交**（`UBCCController.cc:2441 commitIntendedResult`）：
- 阶段 1（`processOuterRequest`）：只创建 outstanding，记 `intendedState` + `reservedEpoch=committed+1`，
  **不动已提交 DirEntry**。
- 阶段 2（`processClear` → `commitIntendedResult:2458-2469`）：才写 `entry.state`、递增 `entry.epoch`、
  然后 `replayPendingRequesters`。

grant 需跨节点传回 requester（405ns + PDES sync），此间目录若"发出即提交"（Eager），
则 grant 在途/丢失时目录已说"owner=A"，但 A 从未拿到数据 → B 请求同址 → home 向 A 发 recall →
**A 无数据 → 静默数据损坏**。两阶段提交让在途窗口内目录始终处于**可自愈的安全旧态**。

#### 5.3.2 Clear 承担的三个不可替代职责

| 职责 | 代码证据 | 去掉后果 |
|------|---------|------|
| **① grant 交付确认** | commit 前目录仍是 pre-grant 态；requester 重试命中 `WAITING_CLEAR` 幂等取 grant（`:476-488`） | 丢包场景：目录与实际持有者永久不一致，无法靠重传自愈 |
| **② 同址串行化** | pending 请求排队 `_pendingRequesters`（`:501-529`），**只在** `processClear` 里 `replayPendingRequesters`（`:2350`）放行 | 需另找串行化点，否则同址并发 grant |
| **③ epoch stale rejection** | Clear 携带 baseEpoch，commit 时 epoch 递增；乱序/重传旧消息用半程比较拒绝（`isNewerEpoch:2408`） | 乱序/重传消息的代次判定窗口被破坏 |

#### 5.3.3 ClearAck 的作用：提交成/败/未决的回执

Clear/ClearAck 是**同步请求-返回**（`EPBackend::sendClear:1907-1982`，返回值
`1=accepted / 0=rejected / -1=error / -2=pending`）。requester 必须知道 home **到底有没有接受提交**：
- `accepted`：目录已提交，事务落定，进入稳定态；
- `pending`（首次通常如此）：ClearResp 未回，**重试**（复用同一 reqId 命中缓存，`:1926-1930`）；
- `rejected`：epoch/reqId/stage 不匹配（stale Clear 或 GRANT_HANDSHAKE 已被回收），回退重走 grant。

没有回执，requester 无法安全判断事务是否真正落定，两阶段提交的第二阶段就没有闭环。
Clear 丢了 → 一直 pending → 重试直到 accepted，因此对丢包鲁棒。

#### 5.3.4 结论：Eager Commit 不作为指标 2/关键路径优化的正式方案

Eager Commit（消除 Clear leg，省 1 RTT 约 26-39%，`tc98_optimization_analysis.md:186`）虽诱人，
但**破坏上述①②③**，且 `_outstandingReqs` 每 PA 仅 1 条（`createOutstanding:3049`），
一条 `WAITING_CLEAR` 阻塞该 PA 全部操作并钉住 ResidentDir 条目（`refreshPinnedBit:2346`），
**无超时回收**（`deadlineTick` 设了从不读，是死代码）。`push_grant_design.md §8.4` 明确背书
"commit 仍只在 Clear 发生，已被故障建模"；`tc98_optimization_analysis.md:116` 也标注 Eager Commit
只在 reliable-transport 假设下安全。**因此 Eager Commit 与交付件 2 的可靠性模型（丢包/乱序/重传）
直接冲突，最多作为"reliable-transport 编译期开关的未来探索"一笔带过，不作为正式对照。**

### 5.4 支撑讨论 B：关键路径时延压缩（区别于单请求总时延）

**区分**：单请求总时延含排队/retry（受串行化、资源竞争影响），压它对吞吐帮助有限；
**关键路径**（一次跨节点事务不可避免的串行依赖链）才决定同步原语延迟。压缩关键路径 = 减少链上串行跳数。

关键路径 = 4 个跨节点单跳串行依赖（`tc98_optimization_analysis.md:143-168`）。减跳方向：

| 方向 | 减掉哪一跳 | 关键路径影响 | 正确性代价 |
|------|------|------|------|
| **C4 Direct-Forward（已实现）** | owner 数据不经 home 中转，直发 requester | 数据路径并行化，requester 拿数据早 ~1 跳 | 无（元数据仍经 home，`EPBackend.cc:1109`） |
| **静默升级（§5.2 主方案）** | R_E holder 写升级整条链归零 | 独占写升级从 2 跳 → 0 跳 | 无（本地可判定、零跨节点） |
| **Forward/Owned 态（MESIF/MOESI）** | 后续 reader 由持有者直供，跳过 home recall | 读命中远程共享/独占省 home→owner→home 两跳 | 中：需目录引入 Forward/Owner 指定语义 |
| **推测授权（speculative grant）** | home 在 recall 完成前先发 grant，失效并行 | 省 leg3 等待 | 高：需回滚，正确性复杂 |
| ~~Eager Commit~~ | ~~Clear leg~~ | ~~省关键路径末端 1 跳~~ | **高（见 §5.3，不采纳）** |

**结论**：指标 2 应衡量"关键路径跳数削减"。**静默升级把关键路径从 1 RTT 削到 0，是最纯粹、
最安全的关键路径压缩**，比"单请求总时延里砍 retry"说服力强得多。

### 5.5 支撑讨论 C：目录压缩机制对比（含 ISCA26 limited-pointer 思路）

#### 5.5.1 UBCC 目录当前结构

set-associative + bit-packed（`ResidentDir.hh:76-78`, `ResidentDir.cc:54`）：
每条 entry = valid(1) + mesi(2) + dirty(1) + ctrl(3) + **sharers_bits(默认8=full bitmap)** +
epoch(24) + tag。持久化压缩条目（`BackstoreTypes.hh:186`）sharers 压到 10-bit bitmap（`kMask10`）。

#### 5.5.2 ISCA26 limited-pointer（1-2 pointer + per-set pool + broadcast bit）在 UBCC 的适用性

论文 insight（"多数行少 sharer / 少数行极多 reader"）对 1024 核有效（1024-bit bitmap → 2 pointer 省 98%）。
**关键辨析：论文管的是核（1024 个），UBCC 管的是节点（8/16 个）。**

| 论文机制 | UBCC 上是否值得 | 判断 |
|------|------|------|
| **limited-pointer 替代 bitmap** | **收益微乎其微** | UBCC sharer 域只有 8/16 bit，2 pointer×3bit=6bit 几乎不省；照搬 1024 核方案是场景错配 |
| **pointer pool as fallback（溢出降级）** | **UBCC 已用更强机制实现同源思想** | UBCC 的分层是 `Bloom(过滤不跨节点行) → ResidentDir SRAM(精确) → MetaRNF DRAM(卸载)`，对应论文"精确追踪热点 + 溢出降级"，这才是指标 1 的真正支撑 |
| **broadcast bit（超多 sharer 放弃精确）** | **值得借鉴，但定位为关键路径优化** | 16 节点/更多节点时，全节点读的热点行维护满 bitmap + 逐个 fanout 代价高；broadcast bit 省的是 fanout 复杂度（关键路径，非 SRAM），与已实现的 Batch-RS 同源，可合并为"热点行特殊处理" |

#### 5.5.3 给专家的诚实结论

- **不**把 limited-pointer 直接包装成指标 1 优化（SRAM 收益与 8/16 节点规模不匹配，会被识破场景错配）。
- UBCC 的目录压缩走 **Bloom + 分层卸载**路线（针对"多数行不跨节点"），与论文 **limited-pointer + pool**
  （针对"多数行少 sharer"）是同源分层思想的两种实现；这才是指标 1 的真正支撑。
- 论文的 **broadcast bit** 值得借鉴，但价值定位应是**关键路径/fanout 复杂度优化（§5.4 范畴）**，
  可与 Batch-RS 合并为热点行特殊处理，**而非 SRAM 容量优化（指标 1）**。

---

## 6. 已确认事项

- **Q2**：SRAM baseline 逻辑——纯 SRAM（无 DRAM 卸载），满则 evict 全全局副本 ✓
- **Q3**：节点故障只做文档分析，不补设计 ✓
- **附加 Q2**：504µs 不算 baseline ✓
- **Q4**：16 节点本次不考虑 ✓
- **指标 3（§5.1）**：重定义为"等时延 + 结构性优势"，不声称跨节点更快 ✓
- **指标 2（§5.2）**：主方案 = 静默升级（Silent Upgrade）；retry 参数级调整降级为工程调优 ✓
- **Eager Commit（§5.3）**：与可靠性模型冲突，不作为正式方案 ✓

---

## 7. 工程落地审核（交付前必做项）

> 本节回答"方案确定后，工程上还需完善什么"，覆盖形式化验证刷新、故障注入、
> layout/schema 测定、log 工具、workload 五个维度。每项给出现状、差距、落地步骤。

### 7.1 形式化验证：面对最近代码更新需刷新哪些？

**时间线（git log）**：TLA+ 模型最后更新 **07-11**（`68c0725`）；此后代码更新未反映到模型：

| commit | 日期 | 改动 | 影响的 TLA+ action |
|--------|------|------|------|
| `ae2b1c0` | 07-14 | UBCC fix1/fix2：**invalidation fanout 所有权归 home + send-time effective mask** | `InvalidationBarrier` / `UpgradeBarrier` / `BarrierAck`（语义相关，**必须刷新**） |
| `40c8915`/`addc08b` | 07-15 | EP-RNF **held-upgrade snoop 指数退避**（5→200µs）+ Finish_CleanUnique stale assert | EP intra-node 模型的 upgrade-retry 路径（时序参数，**建议刷新**） |
| `3aa7a78` | 07-15 | EP-RNF **snoop 冲突 3×3 仲裁矩阵** | 目前 EP 模型未建模 snoop 冲突仲裁（**新增覆盖项**） |
| `7c6fbfe` | — | barrier `sync_wait(mask, NUM_SOCKETS)` 双参 | workload 层，非协议模型 |

**刷新工作量评估**（对照 `verification/fv_coverage_fidelity.md` A3.1 映射表）：

1. **`ubcc_protocol_core.tla` 的 fanout 语义（fix1/fix2）** — 中等改动。当前模型 `InvalidationBarrier`
   假设 fanout 由创建 outstanding 的 home 发起（正是 fix1 的结论），需确认模型的 `targetMask` 是否
   已是"send-time effective mask"（fix2）；若模型用的是 acceptance-time snapshot，需改为 send-time。
   预计 ~20-30 行 + 重跑 safety（~21M states，历史 depth 23）。
2. **EP snoop 冲突仲裁（3×3 矩阵）** — 新增覆盖。`ep_intra_node*.tla` 目前不含 snoop-vs-inflight
   冲突分类。需按 `docs/design/eprnf_snoop_conflict_arbitration_plan.md §9` 的矩阵新增 action
   （STALE/IMMED 分类），或在文档中显式标注"3×3 矩阵由 E2E（TC 全量）覆盖，形式化留待下阶段"。
3. **指数退避** — 纯时序参数，**不影响 safety，仅影响 liveness 的 fairness 假设**。模型用抽象
   `RecallTimeout=2`（`fv_coverage_fidelity.md:147` 已披露 model 值≠code 值），退避改动落在这个
   已披露的抽象里，无需改 safety 模型；liveness 只需确认 WF 假设仍成立。
4. **fidelity 映射表刷新** — 更新 `fv_coverage_fidelity.md` A3.1 表，把 fix1/fix2 的 C++ anchor
   对齐到最新行号，A3.3 追加"snoop 仲裁未建模"作为已披露 fidelity risk。

**结论**：**必做 = 刷新 core 模型的 fanout 语义（1）+ 更新 fidelity 映射表（4）**；
**可选/可标注为下阶段 = snoop 仲裁形式化（2）**；退避（3）无需改 safety。
建议由 protocol-analyzer/state-analyzer agent 跑一次 `tlc2.TLC -coverage` 回归，确认 15/15 action
覆盖不退化。

### 7.2 故障注入测试：如何完善？

**现状**（`modules/ubiomodule/ubio_main.cc:69-208`）：真实运行时注入只有 ubio ZMQ 层一处，
支持 **drop / duplicate 完整实现**；**delay 是伪实现**（只打证据不真延迟）；**无 reorder、无概率丢包**。
E2E 覆盖仅 TC47/48/49，**全部是 duplicate**。`framework/Port.hh` 与 UBAdapter 无任何故障 hook。

**差距 vs 交付件 2 可靠性模型（丢包/乱序/重传）**：

| 故障类型 | TLA+ 模型层 | 真实运行时（ubio） | E2E 覆盖 | 差距 |
|---------|:---:|:---:|:---:|------|
| Drop（丢包） | ✅ 枚举 | ✅ 实现 | ❌ 无 TC | **需补 drop 类 E2E TC** |
| Duplicate（重传） | ✅ 枚举 | ✅ 实现 | ✅ TC47/48/49 | 充分 |
| Reorder（乱序） | ✅ 枚举 | ❌ 未实现 | ❌ 无 | **运行时+E2E 皆缺** |
| Delay（延迟） | — | ⚠️ 伪实现 | ❌ 无 | delay 真实化（可选） |

**落地步骤（按优先级）**：

1. **P0 — 补 drop 类 E2E TC**：drop 类注入运行时已支持（copies=0），只需在
   `tests/e2e/run_multi.sh:56-63` 的 `fault_rules_for_tc()` 新增规则（如
   `tcNN_drop_clear:ClearReq:1:0:0:drop::1`），并写 verifier 校验"值仍收敛 + 出现 `[UBFAULT]` 证据 +
   requester 靠 WAITING_CLEAR 重试自愈"。这直接验证 §5.3 论证的 Clear 丢包自愈性。**改动小、价值高。**
2. **P0 — 静默升级的故障免疫验证**：给静默升级路径（§5.2）设计一个 TC，对 `OuterUpgradeReq` 注入 drop，
   验证"开关开启时该路径零跨节点消息，故 drop 规则不命中（无 `[UBFAULT]`）；开关关闭时 drop 命中且靠重试自愈"。
   佐证静默升级的抗故障优势。
3. **P1 — reorder 运行时实现**：ubio 主循环增加一个"延迟重排队列"（把命中 reorder 规则的消息
   缓存 N 个 tick 后再投递），使 `UbioFaultAction` 支持真正的 reorder。工作量中等（~50-80 行
   `ubio_main.cc`），补齐与 TLA+ 模型的对应。
4. **P2 — delay 真实化**：把 `ubio_main.cc:196-204` 的伪 delay 改为真实 deferred enqueue（同 reorder 队列复用）。
5. **文档标注**：`docs/recovery/ras_fault_injection_plan.md` 的 FaultPipeline/hop-scheduler/JSON 架构
   全部未实现，应在交付件 2 中标注为"已知限制 / 下阶段"，避免评审误以为已落地。

### 7.3 ResidentDir layout / Backstore schema 的性能与空间测定

**现状**：`ResidentDirConfig` 参数（`ResidentDir.hh:45-54`）目前**基本硬编码**——只有 `bloom_bytes`
经 UBCCController→ubio_main 传入（60KB），其余（sram_bytes=512KB, sharers_bits=8, epoch_bits=24,
ways/set_bits 自动搜索）用默认值，**无 config/命令行注入路径**（`ubio_main.cc:845`）。
`searchOptimalLayout`（`ResidentDir.cc:45-135`）优化容量最大化，`init()` 已 stderr 打印实际 SRAM 占用
（`ResidentDir.cc:171-189`）。有 `estimateFPR`（解析式，`ResidentDir.cc:820-841`）+ UBCCController 级
evict/writeback 计数器（`UBCCController.hh:713-727`），但**无 ResidentDir 级 hit/miss/FPR 运行计数器**。
**无脱离 gem5 的 ResidentDir unit test**。

**测定方案（分空间 + 性能两轴）**：

1. **参数化打通（前置，必做）**：扩展 UBCCController 构造签名 + `ubio_main.cc:800-809` 参数解析，
   让 `sram_bytes / bloom_bytes / sharers_bits / epoch_bits / ways / set_bits` 可由命令行/env 注入。
   这样才能做 layout 对照实验。改动小（构造函数透传 + argv 解析）。
2. **空间占用测定（静态，无需跑 gem5）**：`searchOptimalLayout` + `init()` 已算出
   `dir_bytes / bloom_bytes / capacity`。写一个**纯 ubio 侧 microbench driver**（复用
   `scripts/build_modules.sh:11-13` 的编译方式，脱离 gem5）：给定 config → 打印 layout 表
   （capacity / dir_bytes / bloom_bytes / entry_bits / FPR@n）。这直接支撑**指标 1**（512KB SRAM
   追踪数）——扫描不同 layout，画"SRAM 预算 vs 等效追踪容量"曲线。
3. **性能测定（动态，需跑 E2E）**：给 ResidentDir 补 hit/miss/evict/bloom-FP 运行计数器
   （complement 现有 `estimateFPR`），经 UBCCController JSON 导出（`UBCCController.cc:1074-1082` 已有
   导出通道）。跑同一 workload × 不同 layout，对比：目录命中率、evict 频率、因 evict 触发的额外远程 recall
   次数（→ 时延）。这把"layout → 性能"落成数字。
4. **CompactCodec 一致性核查**：注意 backstore 压缩 sharers=10bit（`kMask10`）vs SRAM sharers_bits=8
   是两套编码（`BackstoreTypes.hh:159`）。测定 16 节点时需先统一位宽，否则 backstore 溢出。

### 7.4 延迟日志工具：如何高效读出可汇报结果？

**现状**：活跃链路 = `[TRACE-PERF]`（6 埋点，格式 `tick|node|comp|reqId|pa|event|extra`）→
`scripts/trace2chain.py`（按 reqId 组装事务链）→ `scripts/chain2html.py`（分段归类 + P50/P99 聚合 +
HTML 时间轴 + CSV 导出）。`chain2html.py:44-64` 的 `classify_segment` **已能区分真实链路延迟
（nsim_link）与 PDES 同步伪影（nsim_sync）**——这是关键能力。`solve_latency_params.py` 做预算反解。
**孤立遗留**：`tools/latency_trace_to_html.py` 消费的 `[UBLAT]` 埋点在源码中不存在，不产出数据。

**差距（面向"直接读出大家关心的延迟 + 可汇报"）**：

1. 现有工具输出单次 run 的 HTML/CSV，**缺"baseline vs optimized 对照汇总"**——评审要的是
   "指标 2 降低 X%"这种一句话结论，而非两张分开的时间轴。
2. **缺按语义场景分组的聚合**：现有按 segment-type 聚合，但评审关心的是"跨节点读缺失延迟"、
   "独占写升级延迟"这类**按事务类型**的分组。
3. `[UBLAT]` 死链需清理，避免误导。

**落地步骤**：

1. **P0 — 对照汇总脚本**：新增 `scripts/latency_compare.py`，输入两个 run 的 chain JSON
   （baseline / optimized），输出一张对照表：`场景 | baseline P50/P99 | optimized P50/P99 | 降幅%`。
   直接产出指标 2 的可汇报数字。复用 `trace2chain.py` 的链数据，无需改埋点。
2. **P0 — 事务类型分组**：`[TRACE-PERF]` 的 `extra` 字段已含 msgType，`trace2chain.py` 增加一个
   "chain 分类器"（按链首请求类型：ReadShared / ReadUnique / Upgrade / Recall），使汇总能按
   "跨节点读缺失 / 独占写升级 / 多 sharer 读"分组——正好对应 §5.1 论据与 §5.2 场景。
3. **P1 — 关键路径 vs 总时延分离展示**：`chain2html.py` 已有 nsim_link/nsim_sync 分离，
   在汇总里显式给出"关键路径时延（真实链路，去 PDES sync）"列——这是 §5.4 关键路径命题的直接证据，
   也回答"我们真正关心的延迟"。
4. **P2 — 清理死链**：删除或标注 `tools/latency_trace_to_html.py` 为 deprecated（`[UBLAT]` 不存在）。

### 7.5 性能对比 workload：复用还是新建？

**现状**：所有 e2e testcase **仅做正确性（MATCH）判定，无延迟/性能断言**。TC80/81/82 采集
`[LATENCY]` marker 但只数数量不断言。已有场景高度契合待测指标：

| 待测场景 | 现有可复用 workload | 说明 |
|---------|------|------|
| 跨节点读缺失延迟 | **TC80**（`cntvct_el0` 计时跨节点读，最贴合）、TC2、TC96 | 直接复用 |
| **独占写升级（§5.2 主战场）** | **TC29**（`local_upgrade_from_exclusive`，最贴合静默升级）、TC8、TC16、TC97 | 直接复用，加开关对照 |
| 多 sharer 读（Batch-RS） | **TC100**（16 readers 同行） | 直接复用（§5.1 论据3） |
| owner-forward（C4） | **TC101**（direct_fwd 链） | 直接复用（§5.1 论据3） |
| 本地 TBE 干扰（§5.1 论据2） | 无直接对应 | **需新建** |

**结论：以复用为主，仅新建 1-2 个 workload。**

1. **复用（改判定，不改 workload）**：TC29/TC80/TC100/TC101 的 workload 本身足够，
   **只需给 verifier 加延迟采集**——从 §7.4 的对照汇总脚本读延迟，不在 workload 里硬编码断言
   （保持 workload 纯粹）。指标 2（静默升级）用 **TC29** 跑 `EP_SILENT_UPGRADE` on/off 两次即可。
2. **新建 1 个 —— TBE 干扰对比 workload（§5.1 论据2 专用）**：混合负载：每节点本地 CPU 密集访问
   私有段（打满本地 HN-F TBE）+ 并发少量跨节点目录事务，测本地请求 P99。用于对比 HA-C（目录占 TBE）
   vs UBCC（目录外置）。可基于 TC35（NUMA 混合压力）改造。
3. **可选新建 —— 静默升级微基准**：一个纯"R_E holder 反复写同一私有独占行"的紧循环（放大升级路径占比），
   使 §5.2 的降幅在端到端更显著、更易汇报。可基于 TC29 加 ROUNDS 放大。
4. **参数化已就绪**：`NUM_NODES/NUM_SOCKETS` 编译期注入（`test_e2e.py:1332-1338`）+ 运行期
   `--num-nodes/--num-sockets`（`:1451-1452`），`ROUNDS` 等宏改源码重编。跑 3/8 节点、2 socket 无障碍。

---

## 8. 任务归属建议

> 详细任务清单、难度、行数、验收标准见 **§0 分阶段执行总览**。此处仅标注负责人拆分。

| 类型 | 建议负责人 | 理由 |
|------|------|------|
| 协议实现（0.4, 1.1） | 我 / futsu-guider | EPRNFController / EPBackend 小改动 |
| 参数迁移（0.1-0.3） | 我 | 跨多层 plumbing，一人做更连贯 |
| 测量工具（1.2, 1.3, 1.4, 3.4, 3.5） | 我 + futsu-guider | Python 脚本 + ubio 配置 |
| 形式化（2.4, 2.5, 4.4） | protocol-analyzer agent | TLA+ 模型 |
| workload + 实验（1.5, 1.6, 3.1, 3.2, 3.3, 3.6, 4.5） | futsu-guider | E2E 测试编排 |
| 文档撰写（2.1, 2.2, 2.3, 3.7, 4.2, 4.3） | 我 | 顶层汇总 + 专家 in-place update |
| 杂项核查（4.1, 4.6） | futsu-guider | 代码小改动 |
