# CC-EP 整体验收指标、交付件与 TODO 总表

> 整理日期：2026-08-07
> 范围：当前工作树中的协议实现、E2E、性能、HA、fault、形式化、构建与文档交付
> 资源约束：所有并行任务合计最多使用 16 logical cores

## 1. 文档定位

本文将整个项目的验收内容统一分为三部分：

1. 验收指标：什么条件满足后才可以判定合同或工程验收 PASS。
2. 交付件：最终必须提供哪些源码、程序、配置、测试、证据和文档。
3. TODO：从当前状态到可冻结交付之间还需要完成的工作。

本文不是新定义合同，也不以测试数量替代合同指标。发生事实冲突时按以下优先级解释：

1. 当前实现源码、配置和 verifier。
2. 与冻结 commit、binary hash 和 manifest 绑定的当前运行 artifact。
3. 当前执行契约和权威 testcase 目录。
4. 历史日志、历史报告和历史 PASS 声明。

历史 PASS 只证明对应历史代码和配置，不能自动外推为当前冻结基线 PASS。

## 2. 状态与证据等级

### 2.1 状态

| 状态 | 含义 |
|---|---|
| `PASS` | 满足明确验收门槛，且有冻结代码、配置和原始证据 |
| `PARTIAL` | 有实现或历史 PASS，但范围、统计、拓扑、可复现性或当前代码复验尚未闭环 |
| `UNPROVEN` | 当前信息不足，不能判定成立或失败 |
| `TODO` | 尚未完成，实现或证据缺失 |
| `NOT IMPLEMENTED` | 当前版本明确未实现 |
| `OUT OF SCOPE` | 经确认不属于本期验收范围；必须有书面范围说明 |

### 2.2 证据等级

| 等级 | 证据 |
|---|---|
| `E0` | 设计主张或推导，无运行证据 |
| `E1` | 静态代码审阅、接口或路径分析 |
| `E2` | 单元测试、focused test 或小规模仿真 |
| `E3` | 完整 E2E testcase PASS，配置和 oracle 明确 |
| `E4` | 多 testcase、多拓扑或 fault/performance matrix PASS |
| `E5` | 冻结 commit、环境、binary hash、原始 artifact、多轮统计和独立复现齐全 |

最终合同 PASS 应以 `E5` 为目标。形式化结论可以在其明确的小模型范围内达到强证据，
但不能替代生产规模 E2E 和性能复验。

## 3. 当前总体判断

| 领域 | 当前状态 | 结论 |
|---|---|---|
| 协议功能正确性 | `PARTIAL` | 核心路径、重点修复和大量 TC 有较强证据，但缺冻结基线下完整 146-TC 回归总表 |
| 可靠性与 fault | `PARTIAL` | TC117-TC159 bounded fault 证据较强；完整 Q1-Q7 release qualification 尚未全部落地 |
| 目标 1：512 KiB 容量与成本 | `PARTIAL` | 历史结果达标，但最新协议代码复跑和 E5 provenance 未闭环 |
| 目标 2：时延降低 | `PARTIAL` | 历史结果达标，但 `>=500 ns` 口径、多轮统计和冻结基线未闭环 |
| 目标 3：相对甲方 HA | `UNPROVEN` | 外部研究、条件 DAG、来源矩阵和 litmus 规格已归档；甲方私有参数、共同完成语义、paired 数据和 ARM/RISC-V 实跑尚未关闭，且存在合法 direct-authority 风险分支 |
| 3N/8N 拓扑 | `PARTIAL` | 1S、2S、8N1S、8N2S 实现存在；当前代码完整矩阵和 8N direct 状态需统一冻结 |
| 16N Switch | `TODO` | 没有真正 `num_nodes=16` 的 Switch qualification，8N2S/16 planes 不能替代 |
| 形式化验证 | `PASS/PARTIAL` | 指定小模型内零反例；代码轨迹反校验、ARM memory model 和生产 timeout 数值未覆盖 |
| 构建可复现性 | `TODO` | 缺统一 evidence manifest、镜像 digest、全体 binary/config/raw artifact hash |
| 输出与运维 | `PARTIAL` | 输出审阅已完成；高频日志、runner 判定和 heartbeat 仍需整改 |
| 最终三份交付件 | `PARTIAL` | 文档主体存在，但状态、HA、8N/16N、性能和最新 fault 证据尚未统一刷新 |

因此当前项目不能判定为“整体合同验收 PASS”。

## 4. 合同硬指标

### 4.1 目标 1：512 KiB 下等效追踪容量与附加成本

#### 验收指标

| 子项 | 硬门槛 |
|---|---|
| SRAM 预算 | 统一冻结为 512 KiB，并明确 Bloom、ResidentDir、GroupIndex 等组成 |
| 容量 baseline | `naive + no latency optimization` |
| 优化对象 | `spill + no latency optimization` |
| 等效追踪容量 | spill 至少达到 naive 的 150%，即提升至少 50% |
| 去重口径 | ResidentDir 与 backstore metadata 按 cacheline 去重，禁止直接相加 |
| 附加同步成本 | spill-noopt 相对 naive 小于 50 cycles |
| 频率换算 | 若按 2 GHz，50 cycles 对应 25 ns；最终应记录实际合同频率 |
| correctness | 三 profile 数据 oracle、phase、child exit 和 verifier 全部 PASS |
| 统计 | 当前冻结代码至少 3 个独立 run；报告 min/median/max、mean、stdev/CV |

#### 当前证据

历史报告给出：

- naive：65,536 lines。
- spill-noopt：102,656 lines。
- capacity ratio：156.64%。
- 压力后 Outer mean 增量：6.03 ns，即 12.06 cycles @ 2 GHz。

历史数字满足门槛，但报告明确说明结果完成于后续协议修复之前，因此当前状态为
`PARTIAL`，不能直接作为冻结交付的最终 PASS。

#### 最终 PASS 条件

1. TC131 canonical 8N1S 的 naive、spill-noopt、optimized 三 profile 全部 PASS。
2. 当前代码重新计算 capacity ratio `>=1.5`。
3. 附加成本 `<50 cycles`。
4. 结果绑定 commit、submodule、binary hash、topology、最终 argv 和 timer 配置。
5. 性能运行关闭非必要高频调试输出。

### 4.2 目标 2：CC 端到端时延降低

#### 验收指标

| 子项 | 硬门槛 |
|---|---|
| baseline | `naive + no latency optimization` |
| optimized | `spill + latency optimization` |
| 适用集合 | naive guest-visible mean `>=500 ns` 的场景；必须统一 `>=`，不得继续混用历史 `>500 ns` |
| 主计时 | guest/target-visible counter；不得以 `EP-PERF kind=outer` 替代 |
| 聚合方式 | case-level percentage 等权平均，公式必须冻结 |
| 降幅 | 适用集合平均降低至少 10% |
| 重复性 | 每个纳入场景至少 3 个独立 run，建议 5 个 |
| 统计 | mean、P50、P95、P99、max、stdev/CV |
| 负结果 | 退化场景必须保留，不得从平均集合中事后删除 |

#### 当前证据

历史适用集合包括 TC135-TC139 和 TC217，报告平均降低 54.32%，其中 TC138
退化 12.12%。历史结果满足 10% 门槛，但存在以下未闭环项：

- 历史筛选使用严格 `>500 ns`，合同 TODO 要求 `>=500 ns`。
- 多数场景是单轮或缺少统一 CV。
- 当前代码和最终二进制尚未统一复跑。

当前状态为 `PARTIAL`。

#### 最终 PASS 条件

1. 冻结适用集合和等权平均公式。
2. TC135-TC140 三 profile correctness 全部 PASS。
3. TC217 至少 3 个独立 run，三 profile均有完整样本。
4. 所有纳入 case 的 guest-visible 数据可由原始 JSONL/marker 重新计算。
5. 平均降幅 `>=10%`，并披露 TC138、TC132、HA06 等负结果。

### 4.3 目标 3：OurCC 与甲方 HA 跨节点同步理论时延

#### 合同原始门槛

```text
OurCC 跨节点 CC 同步平均时延 < 甲方 HA 实现的理论平均时延
```

如果双方书面接受修订口径 `<= + 结构性优势`，必须作为正式合同范围变更记录，
不能由内部文档自行替换原始严格小于门槛。

#### 必须冻结的比较条件

- 甲方 HA write-through/write-back/hybrid。
- peer response 是 central-return、data-only 还是 data+authority。
- invalidate completion 语义。
- metadata commit 点。
- requester root completion 点。
- dirty/latest data 定位方式。
- same-line serialization。
- HA/IODie 物理 placement。
- store、fence、DMB/DSB 完成语义。
- contention/retry 和本地 service/queue 上下界。

#### 统一输出指标

- `T_visible`：requester 安全取得数据或权限。
- `T_commit`：Home/HA metadata 正式提交。
- `T_next`：下一同址冲突事务可以安全继续。
- `K_logical`：逻辑依赖段数。
- `K_crossnode`：真实物理跨节点传输段数。
- `P`：目录、peer、data、install、commit、queue 等本地项。

#### 当前状态

外部研究整合后的正式结论为 `UNPROVEN（存在实质性 RISK）`：

- 当前 `clear-ack` profile 真实存在并等待 ClearResp accepted。
- 拟议 `lossless-oneway` 尚未实现，不能作为当前代码结果。
- 2 节点下现有 C4 Direct-Forward 的三角色路径不可达，不能作为主要胜因。
- 甲方 HA 多个高敏感参数仍为 unknown。
- ARM acquire/release/barrier/OoO 验证未完成。
- 公开资料确认 direct data 不自动等于 authority，无显式 Ack 不等于无 completion。
- 合法 direct-data+authority HA 分支可具有更短 K=3 visible path，对 OurCC 构成风险。
- central-return 分支常见同 K=4；同 K 仍需证明 `P_OurCC<P_HA`。

已完成并归档：

- 外部研究主报告、一页结论、15 题甲方确认单。
- Arm CHI/Arm ARM、CCIX/CXL、目录论文、RISC-V、formal/statistics 来源矩阵。
- Remote Read、Shared-to-Writer、Ownership Handoff 条件 DAG 和 placement 账本。
- ARM/RISC-V 五类 litmus 规格，当前明确为未运行。

#### 最终 PASS 条件

1. HU-01 至 HU-12 高敏感项已确认，或双方接受明确的 unknown 区间。
2. 至少对 Remote Read、Shared-to-Writer、Ownership Handoff 建立完整 DAG。
3. 按共同安全完成点计算上下界和 break-even。
4. 使用相同 target/guest-visible root counter 的可复现 workload。
5. ARM/RISC-V 弱内存序与 transport reorder 的证明域明确分离。
6. 使用 paired samples，并预注册最大轮数、inconclusive 和 95% 单侧 CI 规则。
7. 主指标 `delta=T_mean_HA-T_mean_OurCC` 的置信下界严格大于 0。
8. 最终结论达到 `STRICT PASS`；若仅 `CONDITIONAL PASS`，需合同方书面接受。

## 5. 工程验收指标

### 5.1 功能正确性

| 指标 | PASS 判据 |
|---|---|
| 支持 testcase | 当前 registry/verifier 共同支持的 146 个 TC 有完整清单 |
| 数据 oracle | 所有要求的 `[READ_VAL]`、JSON validation、byte oracle 均匹配 |
| 进程结果 | gem5、UBIO、networksim 全部受管 child exit 为 0；TC9 仅允许定义明确的预期 crash |
| 文件完整性 | 预期 simout、verify、child status 和 result artifact 全部存在 |
| verifier | 不能仅依赖自由文本最后一行；目标状态为 rc + 结构化 result 双重判断 |
| 协议 invariant | 无 double commit、epoch 回退、非法 owner/sharer、重复应用数据和永久 outstanding |
| 无静默跳过 | `SKIP/MISSING/PENDING` 不得被统计为整体成功 |
| 回归范围 | 冻结基线下完整 mandatory TC 100% PASS |

最低 smoke 不等于完整正确性验收。最终应按 3N1S、3N2S、8N1S、8N2S、2N1S
分组执行，并对 permissive verifier 做补强或附加 artifact 检查。

### 5.2 Fault 与可靠性

#### 已有 bounded qualification

| 范围 | 当前证据 |
|---|---|
| TC117-TC119 | 3 个 strict smoke，6 个确定性 fault hit |
| TC148 | ClearReq 32 hits，Drop/Duplicate/Delay/Reorder 各 8 |
| TC149-TC155 | UpgradeReq、InvalidateAck、RecallResp 的 bounded single-fault qualification |
| TC156-TC159 | RecallResp、InvalidateAck、UpgradeResp、UpgradeAckNotify 单次 Drop recovery |

历史汇总记录 TC148-TC159 qualification 共 184 个真实 fault hits，并有 TC8/TC16
无故障回归。最终验收仍需将这些结果统一归档到 manifest；测试数量不能替代恢复合同。

#### 完整 release qualification Q1-Q7

| 层级 | 指标 |
|---|---|
| Q1 single-fault | 关键 message × Drop/Duplicate/Delay/Reorder，规则精确命中 |
| Q2 repeated-loss | drop-first-2、drop-first-3、指定 ordinal、request/response 交叉再次丢失 |
| Q3 composed-fault | 协议依赖链上的双故障组合，不做无意义全笛卡尔积 |
| Q4 concurrency/burst | 多 PA、多 home、partial Ack、接近 outstanding 容量和 burst |
| Q5 topology | 3N1S 完整基础，3N2S socket tuple/routing，8N2S 代表性抽样 |
| Q6 retry-exhaustion | 持续 Drop 必须确定性达到 retry budget 并安全失败，不得以外层 timeout 代替 |
| Q7 no-fault regression | 每次 qualification 后运行必要无故障回归 |

每个 successful fault case 必须同时满足：

1. 期望 rule 100% 实际 action hit。
2. 每条 rule 精确 hit count，无 unexpected hit。
3. Delay/Reorder 有实际 hold/release/delivery 和接收顺序证据。
4. Duplicate 证明 exactly once commit/apply。
5. Retry 保持 stable reqId，transport retry 不无故增加 epoch。
6. partial Ack 只重发 pending mask。
7. 数据 oracle 100% 正确。
8. outstanding、pending txn、held upgrade、waiter、retry、deferred fault queue 全部 drain。
9. completion 后不再 retry。
10. 正常退出或明确的 `EXPECTED_RETRY_EXHAUSTION`，禁止 silent timeout。

当前 bounded single-fault qualification 较强，但完整 Q1-Q7 尚为 `PARTIAL/TODO`。

### 5.3 性能和容量 workload

| 指标 | PASS 判据 |
|---|---|
| TC131-TC134 | canonical topology、三 profile、correctness 与 timer 完整 |
| TC135-TC140 | 每个 case 精确 read/sample/phase，三 profile矩阵完整 |
| TC142-TC147 portable | 3N1S、2N1S、3N2S、8N1S、8N2S 的适用集合明确 |
| 512 KiB p150 | target footprint 98,304 lines，naive capacity 65,536，ratio 1.5 |
| HA formal150 | TC210-TC221 × 三 profile，共 36 case，要求 36 PASS、0 SKIP |
| C-group HA | TC222-TC227 至少完成正式三 profile或明确适用 profile |
| 统计边界 | service、end-to-end、workload_total 和 Outer diagnostic 不得混用 |
| 并发影响 | qualification 可并发；正式 latency 比较必须串行或证明资源隔离 |

### 5.4 拓扑

| 拓扑 | 验收要求 | 当前状态 |
|---|---|---|
| 3N1S | 基础 correctness、fault Q1-Q4、性能基础矩阵 | `PARTIAL` |
| 3N2S | socket routing、same-PA interference、关键 fault qualification | `PARTIAL` |
| 8N1S | 8N direct correctness、TC90、TC131/133 和 portable | `PARTIAL` |
| 8N2S | 16 planes correctness、TC98/134、portable representative | `PARTIAL` |
| 2N1S | HA01-HA12、C-group adaptation、目标 3 workload | `PARTIAL` |
| 16N Switch | 真正 `num_nodes=16` Switch 仿真或书面 waiver | `TODO` |

8N2S 表示 8 nodes × 2 sockets，共 16 planes；不得表述为 16 nodes。

### 5.5 形式化验证

| 模型 | 最低验收 |
|---|---|
| UBCC core safety | canonical、epoch monotonic、no double commit、tombstone 等 invariant 零反例 |
| Core liveness | RECALL、outstanding、invalidate、upgrade 最终进展 |
| No-cleanup contrast | 预期机械复现 wedge，作为修复对比证据 |
| Transport fault safety/liveness | bounded fault envelope 内零反例 |
| Multi-PA | 两 PA 无 cross-contamination |
| Multi-socket | plane/socket route 不破坏 home transition |
| TC224 focused | 精确 waiter retirement/replay invariant |
| EP-RNF focused | STALE/IMMED 仲裁 invariant |
| TC157 focused | partial Ack、pending-mask-only redrive、duplicate Ack 幂等和 bounded recovery；当前 model-scope PASS |
| TC159 focused | bounded Notify-drop recovery 与 strengthened exact tuple model-scope PASS；当前 C++ mismatch/budget gap 有预期反例 |
| Retry exhaustion focused | proposed `EXHAUSTED` terminal contract 的 recover/permanent cfg model-scope PASS；生产代码未实现 |
| Coverage | 正式 protocol actions 100% 被触发，分母定义明确 |

最终形式化交付还必须包含：

- `.tla`、`.cfg`、TLC/JDK 版本和 hash。
- 完整命令、worker 数和 timeout。
- 原始 TLC stdout/stderr，不放在易丢失的 `/tmp`。
- distinct states、depth、invariants/properties 和 PASS/预期 VIOLATED。
- C++ symbol fidelity mapping。
- 明确 small-scope、手工抽象和未覆盖边界。

### 5.6 构建与可复现性

| 指标 | PASS 判据 |
|---|---|
| 源码冻结 | 主仓 commit、gem5 submodule、dirty state 有记录 |
| 干净构建 | framework、ubio、networksim、gem5.opt 从冻结源码重新构建 |
| 实际二进制 | E2E 使用的必须是 `gem5/build/ARM/gem5.opt`，防止构建到错误目录 |
| Hash | binary、archive、workload ELF、topology、config、verifier、TLC jar 均有 SHA-256 |
| Toolchain | OS/kernel、Docker、image digest、gcc/g++、Python、Java、SCons、ZeroMQ 版本 |
| 资源 | CPU model、NUMA、cpuset、RAM、磁盘门限、并发度 |
| 命令 | 最终 argv 和有效环境变量完整记录 |
| Artifact | manifest、result、raw logs、summary 一一可追溯 |

### 5.7 输出、日志与运维

| 指标 | PASS 判据 |
|---|---|
| 性能模式 | 只保留 manifest、聚合 timer/latency、终局 stats、低频 heartbeat、error/fatal |
| 正确性模式 | 保留 oracle 和必要 evidence，不依赖全量 debug trace |
| fault 模式 | 保留 rule loaded/fired/deliver、retry、commit 和 drain evidence |
| debug 模式 | 必须有最大记录数、PA/reqId filter、时间窗和磁盘上限 |
| stdout/stderr | 机器 artifact 不与人类日志混写 |
| heartbeat | watchdog 不依赖普通 debug 日志文件增长 |
| runner 判定 | 结构化 result + exit code，不只依赖最后一行 sentinel |
| schema | JSON/JSONL/TSV 和稳定 marker 有 schema version |

详细输出分类见 `docs/design/output_statement_audit.md`。

## 6. 最终交付件清单

### 6.1 三份合同主交付件

| 交付件 | 主文件 | 必须包含 | 当前状态 |
|---|---|---|---|
| 交付件 1：协议理论分析与方案对比 | `docs/design/cc_ep_protocol_overview.md` | 架构、接口、消息流、创新点、与甲方指定 HA 的统一功能/成本对比、边界 | `PARTIAL`，需刷新指定 HA 和当前实现 |
| 交付件 2：形式化、可靠性、HA | `docs/design/cc_ep_deliverable2_verification_reliability_ha.md` | formal inventory、fidelity、fault qualification、HA 参数/理论模型、限制 | `PARTIAL`，需加入最新 fault、ARM OoO 和 HA关闭结果 |
| 交付件 3：性能报告与接口说明 | `docs/design/cc_ep_deliverable3_performance_api.md` | 目标1/2/3、8N/16N、性能原始证据、接口和操作说明 | `PARTIAL`，多处历史状态需刷新 |

### 6.2 源代码

必须交付：

- `gem5/src/mem/ruby/protocol/chi/ep/` 的 EPBackend、EPRNF、EPSNF、MetaRNF、UBAdapter。
- 项目修改的 CHI SLICC/generic 文件。
- `modules/ubiomodule/` 的 UBCC、ResidentDir、H64 backstore、fault injector。
- `modules/networksim/`。
- `framework/iface/` 和选定 backend 实现。
- `protocol/` 中的消息 schema、TracePerf policy 等共享代码。

源码包应使用白名单，排除历史 concrete framework、临时 debug 源、构建物和缓存。

### 6.3 构建产物

最低运行产物：

- `gem5/build/ARM/gem5.opt`
- `build/bin/ubio`
- `build/bin/networksim`
- `build/framework/lib/libframework_<backend>.a`

`barrier_manager` 可以作为独立工具交付，但当前正式 E2E 不启动它，不能继续写入当前
E2E 进程拓扑。

每个产物必须附 SHA-256、构建命令、toolchain 和来源 commit。

### 6.4 配置与拓扑

- `configs/topo_1s.json`
- `configs/topo_2s.json`
- `configs/topo_8n1s.json`
- `configs/topo_8n2s.json`
- `configs/topo_2n1s.json`
- 最终 8N direct 配置和证据。
- 16N Switch 配置，或书面 waiver。
- `scripts/gen_topo.py` 和生成 topology 示例。

每次正式运行还必须归档 run-private `topo.json`，不能只保留模板。

### 6.5 Workload 与 testcase

- `tests/e2e/workloads/` 中所有正式 testcase 源码。
- 当前 146 个支持 TC 的 registry 和 verifier 映射。
- portable workload 公共头、timer/latency emitter。
- HA01-HA12 和 C-group HA workload。
- ARM memory-order litmus，完成 R07 后纳入。

不以历史 ELF 代替源码交付。正式 ELF 应由冻结源码构建，并记录 hash。

### 6.6 Runner、verifier 与分析工具

- `tests/e2e/run_multi.sh`
- `tests/e2e/test_e2e.py`
- `tests/e2e/verify.py`
- `scripts/compile_workload.sh`
- `scripts/run_fault_tests.sh`
- approved correctness/performance/HA matrix runners
- `scripts/run_p0_512k_matrix.py`
- `scripts/run_ha_formal_150_matrix.py`
- `scripts/summarize_*`
- `scripts/evaluate_capacity_latency.py`
- trace chain/HTML 工具

必须明确哪些 runner 是 authoritative，哪些是 legacy/manual；旧 runner 不能进入默认验收入口。

### 6.7 Fault qualification 交付件

建议目录至少包含：

```text
fault-qualification/
  manifest.json
  binary_hashes.json
  environment.json
  rules.json
  per_rule_results.tsv
  per_case_results.tsv
  protocol_drain.tsv
  retry_summary.tsv
  data_oracle.tsv
  qualification_summary.md
  raw_logs/
```

必须分别标记：

- `QUALIFIED`
- `FAILED`
- `EXPECTED_RETRY_EXHAUSTION`
- `NOT IN SCOPE`

### 6.8 形式化交付件

```text
verification/formal/
  models/
  configs/
  raw-results/
  toolchain.json
  model-code-fidelity.md
  coverage-summary.md
  known-limitations.md
```

现有 `verification/tla/` 模型应保留；正式运行输出需从 `/tmp` 迁移到耐久 artifact。

### 6.9 性能与 HA artifact

必须交付：

- 目标 1 原始 TC131 三 profile数据和 evaluation JSON。
- 目标 2 每个纳入 case 的多轮 guest-visible JSONL。
- TC135-TC140 matrix 和 summary。
- TC142-TC147 portable matrix。
- TC210-TC221 formal150 36-case matrix。
- TC222-TC227 C-group adaptation 结果。
- HA 参数账本、请求 DAG、上下界、break-even 和最终结论。
- 所有负结果和退化项。

### 6.10 HA target adaptation package

最终面向甲方 target 的包应包含：

- portable workload core。
- `ha_platform.h`。
- example platform adapter。
- linker script 示例。
- result JSON schema。
- seed、地址、操作序列、barrier 和计时边界说明。
- target build/run/collect 指南。

当前 workload core 和文档存在，但完整 adapter/schema package 尚未形成。

### 6.11 Evidence manifest

建议最终目录：

```text
manifest/
  evidence-manifest.json
  source-sha256.txt
  binary-sha256.txt
  workload-sha256.txt
  topology-sha256.txt
  environment.json
  final-commands.jsonl
  container-image.json
  testcase-matrix.tsv
  known-limitations.md
```

每个报告表格单元必须可以反查到 run ID、原始日志和 hash。

### 6.12 操作和开发文档

- testcase reference。
- E2E 执行手册。
- 当前执行矩阵目录。
- runtime output contract 和 output audit。
- framework backend 移植指南。
- HA workload delivery guide。
- formal consolidated report 和 one-page summary。
- known issues、known limitations 和 out-of-scope 清单。

## 7. 最终验收 Gate

### 7.1 合同 Gate

必须全部满足：

1. 目标 1 在冻结当前代码上 PASS。
2. 目标 2 按 `>=500 ns` 和冻结统计公式 PASS。
3. 目标 3 达到合同接受的结论等级；未证明时不能标 PASS。
4. 三份主交付件状态一致。
5. 8N direct 状态明确。
6. 16N Switch PASS，或有正式书面 waiver。

### 7.2 功能 Gate

- mandatory correctness case 100% PASS。
- 无 unexpected MISMATCH、crash、timeout、missing artifact。
- 所有受管 child 正常退出，预期负测除外。
- `SKIP/MISSING/PENDING` 视为不完整，不得整体 PASS。

### 7.3 Fault Gate

- 期望 rule 100% 精确命中。
- 无 unexpected hit。
- 数据 oracle 100%。
- 无 reqId churn、无 duplicate commit/apply。
- successful case 所有 protocol/fault queue drain。
- exhaustion case 确定性安全失败。
- qualification 后 no-fault regression 100% PASS。

### 7.4 性能 Gate

- timed region 内无输出 syscall。
- guest-visible 和 Outer diagnostic 分域。
- 正式 latency run 独立且可重复。
- 多轮统计和负结果披露完整。
- 性能结果可由原始数据重新计算。

### 7.5 可复现 Gate

- clean build。
- source/binary/config/workload/toolchain hash 完整。
- environment、container digest、cpuset 和命令完整。
- 所有正式 artifact 有 schema 和 durable path。
- 另一个操作者可按 manifest 重放代表 testcase。

## 8. 整体 TODO List

### 8.1 P0：发布阻塞项

### P0-1 冻结唯一验收基线与状态总表

**工作：**

- 完成 A01。
- 对三项目标、三份交付件、8N direct、16N Switch 逐项标记状态。
- 区分历史代码、当前代码、单轮、多轮、理论和 proposed profile。
- 清理 TC224、fault TC149-TC159、D1/D2 formal 等旧状态漂移。

**完成定义：**每项都有 commit、配置、证据目录、证据等级和当前结论。

### P0-2 修复并冻结验收 runner

**工作：**

- 所有并行任务合计限制在 16 logical cores。
- 修复 supplementary runner 的 declared case set、返回值和终局统计。
- `SKIP/MISSING/PENDING/FAIL` 全部使完整矩阵非成功。
- 同时检查 process rc 和结构化 result。
- expected/planned/executed/pass/fail/skip/missing 数量必须一致。

**完成定义：**小矩阵 dry-run 证明所有计划 case 被调度，任一缺失时 runner 非零退出。

### P0-3 完成目标 1 当前代码复跑

**工作：**

- TC131 8N1S naive、spill-noopt、optimized。
- 运行 `evaluate_capacity_latency.py`。
- 至少 3 个独立 run。
- 输出 capacity ratio、extra ns/cycles 和统计波动。

**完成定义：**ratio `>=1.5`、extra `<50 cycles`、三 profile correctness PASS、E5 manifest 完整。

### P0-4 完成目标 2 多轮重算

**工作：**

- 冻结 `>=500 ns` 适用集合和平均公式。
- TC135-TC140 三 profile 当前代码复跑。
- TC217 至少三轮。
- 保留所有退化项。

**完成定义：**平均降低 `>=10%`，原始 guest-visible 数据和多轮统计完整。

### P0-5 完成目标 3 HA 参数账本和理论模型

**当前状态：`PARTIAL`。** 外部公开研究、HU 账本、三类操作 DAG、来源矩阵、break-even、
15 题确认单和合同文字已经归档；甲方私有答案、共同 workload/placement、P 上下界和 paired
数据仍缺失。

**工作：**关闭 HU-01 至 HU-12 或正式保留 unknown 区间；由双方签字冻结 DAG、完成点、
operation weights、placement 和 break-even 输入。

**完成定义：**获得 `STRICT PASS`、被书面接受的 `CONDITIONAL PASS`，或诚实保留 `UNPROVEN`。

### P0-6 完成 ARM acquire/release/barrier/OoO 验证

**工作：**新增 message passing、release/acquire、DMB/DSB、same-line ordering、independent-line OoO litmus。

**完成定义：**每个 litmus 有 allowed/forbidden outcome、运行次数、seed、配置和零 forbidden result。

### P0-7 关闭 8N direct 和 16N Switch 范围

**工作：**冻结 8N direct 证据；实现真正 16N Switch，或获得合同方书面 waiver。

**完成定义：**不再以 8N2S/16 planes 替代 16 nodes。

### P0-8 生成统一 evidence manifest

**工作：**完成 commit、submodule、dirty state、binary/config/workload/toolchain hash、环境和最终命令归档。

**完成定义：**所有正式结论可从表格反查原始 artifact。

### P0-9 收口输出治理的正确性和性能风险

**工作：**

- 修复无人读取 subprocess pipe。
- 性能模式关闭 UBIO per-message、EPSNF data-beat、`appendTmpLog` 和无界 trace。
- `[EP-PERF]` 有界。
- 独立 heartbeat 取代日志增长 watchdog。
- runner 不再只依赖最后一行 sentinel。
- 修复 TSV schema 和被 `|| true` 吞掉的 rc。

**完成定义：**correctness 不退化，性能日志量显著下降，runner 无误判。

### P0-10 刷新三份主交付件和最终评审材料

**工作：**完成 D01、D03、E02；同步目标 1/2/3、fault、TC224、8N/16N 和负结果。

**完成定义：**三份交付件、状态总表和 manifest 无互相矛盾的状态。

### 8.2 P1：资格闭环

### P1-1 完整 fault qualification Q1-Q7

**工作：**在已有 TC148-TC159 基础上补 repeated loss、composed fault、burst/concurrency、3N2S/8N2S、retry exhaustion 和统一 no-fault regression。

**完成定义：**形成完整 qualification artifact，成功 case drain，exhaustion case 确定性失败。

### P1-2 补齐 TC157/TC159 focused formal models

**当前状态：`PARTIAL`。** 模型、cfg、durable raw logs 和 C++ fidelity mapping 已完成；
TC157 model-scope 完成。TC159 主路径和 strengthened 语义完成，但当前 C++ 仍存在
mismatched Done/Notify 与预算耗尽 re-poll 缺口，因此不能按生产语义关闭。

**工作：**

- TC157 partial Ack pending-mask re-drive model。
- TC159 stable tuple、same-reqId exact replay、WAITING_LOCAL_DONE model。

**完成定义：**目标语义零反例，C++ symbol fidelity mapping 完整；任何 current-code
expected violation 必须修复或进入正式限制表。

### P1-3 冻结 3N2S/8N2S 当前代码矩阵

**工作：**完成 portable 512 KiB、关键 correctness 和代表 fault cases；定位 TC143 8N2S 等失败或超时。

**完成定义：**expected set 无 PENDING/MISSING，已知失败进入正式限制表。

### P1-4 完成 TC98 qualification

**工作：**保留 full stress，并增加日常短 profile；至少取得一次完整 full artifact。

**完成定义：**correctness 修复与长周期性能资格分开记录。

### P1-5 补强 permissive verifier

重点包括：

- TC84/85 必须要求 capacity marker。
- TC95/97、TC114/115 必须要求非零 read/phase。
- TC100/101 必须在机制 qualification 中强制优化实际命中。
- TC120 必须强制必要 phase/read。

**完成定义：**空输出、零 marker 或未命中优化不得误 PASS。

### P1-6 建立完整 146-TC full regression suite

**工作：**按 topology 和语义分组，纳入 2N1S、fault、HA、spill-only 和 naive-only applicability。

**完成定义：**expected set 完整，语义 SKIP 明确，不支持 ID 不被误调度。

### P1-7 完成 HA target adaptation package

**工作：**提供 `ha_platform.h`、adapter 示例、linker 示例、result schema 和 target guide。

**完成定义：**甲方可在未知 target 上编译、运行并采集与 CC 侧同口径结果。

### P1-8 保存 formal 原始结果

**当前状态：`PARTIAL`。** 本轮 focused 模型原始 TLC 输出已迁入
`verification/results/`，runner 默认保存 command、workers、timeout 和 rc；既有其他正式模型
仍需按同一格式迁移和补 hash/manifest。

**工作：**TLC 输出从 `/tmp` 迁移到项目 artifact，记录 Java/TLC/hash/workers/timeout。

**完成定义：**每个正式模型可独立重放并核对状态数和性质。

### 8.3 P2：工程强化

### P2-1 模型与代码轨迹反校验

输出结构化 C++ transition trace，建立 event 到 TLA+ action 映射，自动检测实现和模型漂移。

### P2-2 timeout 与周期 wakeup 校准

验证 idle wakeup、最大正常网络时延、timeout margin、接近边界的 delay/reorder 和误清理风险。

### P2-3 剩余 transport reliability contract

补齐尚未覆盖消息的 request/response recovery、send failure 传播、bounded retry budget 和明确 terminal failure。

### P2-4 统一日志框架

实现 `CC_EP_LOG_MODE=performance|correctness|fault-smoke|fault-qualification|debug`，统一 first-N/every-K/max-N、schema 和 stream 职责。

### P2-5 真正 per-hop fault/RAS 框架

实现 canonical JSON rule、legacy import、per-hop scheduler、rule/reaction/outcome JSONL；节点故障未实现时继续标 `NOT IMPLEMENTED`。

### P2-6 真正多跳路由与时延标定

替换 cross-node+cross-socket 单跳相加近似，记录实际 hop 序列并标定 core-to-DRAM 路径。

### P2-7 Framework real backend

交付 real backend archive、ABI 说明和同一 iface contract test 的 PASS 结果；local backend PASS 不能外推。

### P2-8 metadata 参数 single source of truth

修复 gem5 `--ubcc_metadata_size` parser 和 `topo_1s_tinydir.json` 的 `{ubio_extra_args}`，增加非默认值一致性测试。

### P2-9 Docker/toolchain 固化

冻结 image digest、base digest、包清单/SBOM 和 mold 等外部工具来源/hash。

### P2-10 补充 BF/backstore 后续验证

实现或明确延期 TC60、256B page、Schema A/C 消融和 BF FPR 性能统计。

### 8.4 P3：清理与一致性

### P3-1 关闭已解决 issue

更新 TC49、TC98、TC224 issue 的 current status、resolved scope、remaining scope 和 latest evidence。

### P3-2 清理 testcase 命名漂移

统一 TC47/TC49 registry 名称、实际 rule 和文档描述。

### P3-3 更新陈旧文档

修复以下漂移：

- 旧的“5 models”“71/71”“TC1-TC54”总览。
- D1/D2 仍写 planned。
- TC224 full-scale 仍写未通过。
- Level-2 fault 仍写 future work。
- 8N2S 仍写未实现。
- 当前 E2E 仍包含 barrier_manager。
- gen_topo docstring 的旧延迟值。

### P3-4 退役或标记 legacy runner/tool

明确 `run_all_e2e.sh`、旧 sweep、旧 matrix、旧 framework 手册和旧 trace tool 的状态，避免作为验收入口。

### P3-5 统一 artifact schema

为 JSON/JSONL/TSV 增加 schema version；stdout 纯机器输出，状态和错误进入 stderr 或独立文件。

## 9. 推荐执行顺序

1. 修复 runner 和 16-core 资源约束。
2. 冻结代码、构建产物和 evidence manifest 基础字段。
3. 完成目标 1 当前代码多轮复跑。
4. 完成目标 2 当前代码多轮重算。
5. 完成 H01/H02 和 ARM memory-order 验证。
6. 决策并关闭 16N Switch 范围。
7. 完成完整 fault Q1-Q7 和 focused formal 补充。
8. 完成 3N2S/8N2S、TC98、146-TC regression 和 verifier hardening。
9. 生成最终 E5 evidence manifest。
10. 刷新三份交付件、状态总表和最终评审材料。

### 9.1 文档产出分工

### 可基于当前仓库独立完成

以下文档不依赖外部网络资料，可以直接编写或刷新：

- 唯一验收状态总表。
- 当前 146-TC 执行矩阵和 applicability 表。
- output statement audit 和 runtime output contract。
- fault TC117-TC159 bounded qualification 报告。
- TC157/TC159 实现语义与 focused model specification。
- build/run/evidence manifest schema。
- known limitations、out-of-scope 和 negative-results 清单。
- authoritative/legacy runner 与工具索引。
- 三份交付件的当前实现章节和证据索引。

### 外部研究已完成、仍需甲方信息

公开资料检索和条件化分析已经完成并归档。以下项目的公开机制边界已建立，但甲方私有值
仍只能由甲方确认：

- 甲方 HA write policy、completion、authority 和 dirty-owner 机制。
- 两节点 VI/2-bit directory 的业界能力边界。
- ARM/RISC-V barrier 与 coherence completion 的规范依据。
- lossless coherent fabric 的 replay/retry 分层。
- 16-node coherent switch 的公开架构和评估方法。
- OurCC 与 HA 的理论时延文献支撑和 break-even 比较。

研究原件为：

- `docs/research/ourcc_vs_customer_ha_external_research_report_20260806_zh.md`
- `docs/research/target3_onepage_summary_20260806_zh.md`
- `docs/research/customer_ha_questions_20260806_zh.md`
- `docs/research/ha_coherence_source_matrix_20260806.tsv`
- `docs/research/ha_ourcc_operation_dags_20260806.md`
- `docs/research/arm_riscv_coherence_litmus_plan_20260806_zh.md`

原始任务书和交接初稿继续保留为 provenance：

- `docs/research/chatgpt_work_research_prompt_20260807_zh.md`
- `docs/research/customer_ha_coherence_research_handoff_20260807_zh.md`

### 需要计算验证

以下内容需要模型检查或 E2E/fault 计算：

- TC157 partial Ack re-drive focused model。
- TC159 stable tuple/exact replay focused model。
- retry exhaustion model。
- fault Q2-Q6。
- ARM memory-order litmus 运行。
- 目标 1/2 多轮性能复跑。
- 3N2S/8N2S 和 16N qualification。

资源规则已于 2026-08-10 更新：当前无并行任务时，新增 TLC 默认使用 16 workers、
状态空间较大时最多 28；历史运行仍保留其实际 worker provenance。执行计划见：

`verification/formal_reliability_followup_plan_20260807_zh.md`

## 10. 最终交付判定模板

最终状态表至少应包含：

| 项目 | 门槛 | 当前值 | 状态 | 证据等级 | run/artifact |
|---|---|---|---|---|---|
| 目标 1A | capacity ratio >=1.5 | 待冻结复跑 | PARTIAL | E4 historical | 待补 |
| 目标 1B | extra latency <50 cycles | 待冻结复跑 | PARTIAL | E4 historical | 待补 |
| 目标 2 | applicable mean reduction >=10% | 待 `>=500ns` 多轮重算 | PARTIAL | E4 historical | 待补 |
| 目标 3 | OurCC < HA theoretical mean | 外部条件模型完成；私参/实测未闭环 | UNPROVEN | external research + E0-E1 | 甲方 Q1-Q15、paired E5、litmus 实跑待补 |
| 8N direct | 冻结矩阵 PASS | 待统一 | PARTIAL | E3/E4 | 待补 manifest |
| 16N Switch | 真正 16 nodes PASS或waiver | 未实现 | TODO | E0 | 待决策 |
| Correctness | mandatory 100% PASS | 待冻结全回归 | PARTIAL | E3/E4 | 待补 |
| Fault | Q1-Q7 gate PASS | bounded single-fault 已完成 | PARTIAL | E4 historical | 待扩展/归档 |
| Formal | 指定模型零反例 | 已有 PASS | PASS/PARTIAL | model-scope strong | 待补 raw artifact |
| Reproducibility | E5 manifest 完整 | 未完成 | TODO | E1 | E01 |

只有所有合同硬门槛均为 `PASS`，其余 mandatory 工程 Gate 无 `FAIL/MISSING`，并完成
E5 可复现证据后，才可以把整个项目标记为“验收 PASS”。
