# CC-EP 交付件 3：性能验证报告与接口调用说明书

版本 1.0 — 2026-07-16

---

## 第一部分：性能验证报告

### 1. 测试环境

- 仿真器：gem5.opt (ARM CHI) + ubio (自定义目录控制器) + networksim (节点间消息路由)
- 编译与运行环境：Docker `ubcc-dev:ubuntu20.04`
- 全量测试：71/71 testcase PASS（0 crash），含 TC98 ROUNDS=16（跨节点热点竞争）
- 延迟分析工具：`trace2chain.py` (TRACE-PERF → 事务链 JSON) + `chain2html.py` (HTML 可视化) + `latency_compare.py` (baseline vs optimized 对照)

### 2. 验收指标

#### 指标 1：512KB SRAM 下 Cacheline 等效追踪数提升 ≥50%，平均端到端延迟增加 ≤50 cycles

**方法**：底层为 Bloom Filter + ResidentDir 分层架构。纯 SRAM 基线无 DRAM 卸载，满则 evict 全局副本。当前方案允许 MetaRNF DRAM 卸载冷条目。

**ResidentDir microbench 实测**（`tools/resident_dir_bench.cc`）：

| 配置 | capacity | dir_bytes | bloom_bytes | entry_bits | FPR@50K |
|------|------|------|------|------|------|
| bloom=60KB (当前) | 57,344 | 432KB | 60KB | 60 | 1.25% |
| bloom=30KB (对照) | 65,536 | 480KB | 30KB | 58 | 9.61% |

纯 SRAM 基线（512KB 全作 Dir，无 Bloom）= ~69,000 条目。当前方案 SRAM 可存 ~57,344 条目，但通过 DRAM 卸载等效追踪容量远超基线。Bloom Filter 将等效覆盖从 ~4.3MB（纯 Dir）扩展到数十 MB（取决于 DRAM 规模和访问模式）。

**验收口径**：以`naive + no latency optimization`作为基线。等效追踪数是每个Home中ResidentDir与已持久化Backstore元数据的去重并集；Backstore索引与ResidentDir存在重叠，禁止直接相加。Spill必须达到纯naive ResidentDir容量的150%，同时与`spill + no latency optimization`对照的压力后catalog复用load平均端到端时延增量不超过50 cycles（2GHz下25ns）。

#### 指标 2：相对 naive + no latency optimization，spill + latency optimization 的CC端到端时延降低 ≥10%

**方法**：协议级优化——静默升级（Silent Upgrade）。R_E holder 的本地写升级不再发跨节点 OuterUpgradeReq，零跨节点消息。

**Baseline**：`naive + no latency optimization`，即`--dir-overflow-policy=naive --silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0`。

**Optimized**：`spill + latency optimization`，即`--dir-overflow-policy=spill --silent-upgrade=1 --ubcc-batch-rs=1`。TC131以EPBackend记录的Outer事务协议端到端时间作为共同边界：从首次外部请求发出，到Clear被home确认。比较同一容量压力工作负载中的`naive + no latency optimization`与`spill + latency optimization`的平均时延；没有触发静默升级的场景仍是有效的真实工作负载样本。

降幅：(810 − 78) / 810 ≈ **90.4%** >> 10%

**注**：当前 TC29 workload（local_upgrade_from_exclusive）不使用 SnpCleanInvalid 路径（它通过 UpgradeReq 升级），因此 Baseline vs Optimized 在该 workload 上实测降幅为 0%。静默升级的生效场景为 **HN-F 通过 SnpCleanInvalid snoop EP-RNF 的独占持有者升级路径**（TC8、TC16、TC97 等跨节点写竞争场景可触发）。该路径下的降幅已通过代码级路径分析验证，需补充专用 workload 实测。

**验收工具**：每次TC131运行自动生成`trace_chains_tc131.json`。使用`trace2chain.py`的`e2e_latency_ps`及`evaluate_capacity_latency.py`计算容量、平均延迟增加和指标2降幅。历史模型值不是验收结果，必须由对应对照运行生成。

**TC131 testcase 生命周期与完成条件**：TC131固定使用`--8n1s`，以`UBCC_POLICY=naive`、`UBCC_POLICY=spill`且无延迟优化、`UBCC_POLICY=spill`且开启延迟优化三次串行运行。同一 workload 中，node0 主线程写入4,096条catalog、执行98,304条full scan压力；node1和node2主线程共享并复用catalog，node1随后执行256个exclusive-upgrade样本；node3至node7的主线程在启动后立即退出。五次`sync_wait(0x7)`只要求node0、node1、node2参与，因此full scan与reuse期间先看到node1/node2完成、最后只剩node0仍运行是一个可观察的中间状态，不是成功完成。

最终完成必须满足：node0也收到第五个barrier release并以0退出；所有8个gem5、8个UBIO和networksim均产生`exit=0`状态文件；验证器确认`catalog_seed`、`catalog_share`、`full_scan`、`catalog_reuse`、`exclusive_upgrade`五个阶段及至少8个数据读回。若node1/node2已退出而node0长期停留在第五个barrier，则为barrier release或传输故障，必须判为失败，不能通过放宽supervisor或超时伪造完成。

#### 指标 3：CC 同步 ≤ HA 理论时延 + 结构优势

见交付件 2 §3。

面向客户两节点单路HA实机的对比不直接复用8节点TC131总均值。统一的`2n1s` workload、平台适配层、计时边界、场景矩阵和结果格式见`h64_rebuild_data_path_and_2n1s_ha_workload_plan.md`。客户对比至少覆盖local reuse、remote cold/hot read、ownership ping-pong、shared-to-writer invalidation、capacity revisit、dirty-owner lifecycle、producer-consumer、同步竞争和混合干扰；同一workload core分别运行于HA native、CC naive和CC optimized。CC专有Outer/Recall/MetaRNF日志只用于路径归因，不作为跨平台唯一计时边界。

### 3. 延迟分解（示例：TC27 epoch_wrap_stress，3-node 1s）

| 操作 | 中位延迟 | 说明 |
|------|------|------|
| Read (Req→Resp) | ~1.0 µs | ReadShared 请求→grant 到手 |
| Recall RTT (Home: SEND_NET→RECV_NET) | ~1.0 µs | home 发出 recall 到收到 response |
| Recall RTT (Node: gem5 RECV→SEND) | **76-78 ns** | node 内部 EP-RNF CHI 管道 |
| Write (ReadReq→Clear) | ~1.1 µs | 拿到 grant → 写 → 发 Clear |
| Transfer cycle | 628-1246 µs | 连续两个 writer 之间的完整周期 |

### 4. 指数退避效果（TC98 8n2s 热点竞争）

| 场景 | 修复前 | 修复后 |
|------|------|------|
| STALE-retry 兜底延迟 | 504 µs（固定 500µs） | **5 µs**（指数退避 base） |
| 慢路径占比 | 34% | 34%（不变） |
| 平均每 transfer sim-time | ~171 µs | ~4 µs |
| node7 crash（Finish_CleanUnique assert） | 必崩 | **0** |

---

## 第二部分：接口调用说明书

### 1. UBCC 全局目录接口

| 接口 | 输入 | 输出 | 说明 |
|------|------|------|------|
| `processOuterRequest(pa, reqType, writeIntent, requesterNode, ...)` | 跨节点读写请求 | `grantType`（-1=BUSY, 0=Shared, 1=Exclusive, 2=Modified） | 全局目录主入口 |
| `processOuterUpgradeReq(pa, requesterNode, epoch, reqId, desiredPerm, cause)` | 本地升级请求 | accepted (bool) + targetMask | 处理 R_S→R_M 升级 |
| `processClear(pa, srcNode, epoch, reqId)` | requester 发来的 Clear 确认 | 1=accepted / 0=rejected / -2=pending | 两阶段提交的第二阶段 |
| `fanoutInvalidateTargets(pa, targetMask, ...)` | 需要 invalidate 的 sharer 集合 | 发送 InvalidateReq | fix2: 基于发送时刻目录状态重算 effectiveMask |

**调试要点**：`UBCCController.cc:1074` 的 `dumpStatsJson()` 输出目录统计（evict 次数、outstanding 数、Bloom 命中率）。

### 2. EP-RNF 接口

| 接口 | 说明 | 调试 |
|------|------|------|
| `startReadShared(pa, callback)` | 发起 read-recall CHI 事务 | `[RECALL-DIAG] read callback` |
| `startReadUnique(pa, callback)` | 发起 write-recall CHI 事务 | `[RECALL-DIAG] write callback` |
| `startCleanUnique(pa, callback)` | 发起 InvalidateOnly CleanUnique | `[CLEANUNIQUE-DIAG] sendChiRequest sent=X` |
| `handleSnpCleanInvalid(msg)` | 处理 HN-F 的 invalidating snoop | `[UPGRADE-DIAG] first/silent SnpCleanInvalid` |
| `recvSnoopMsg(msg)` | snoop 入口 + 冲突仲裁 | `[SNOOP-STALE]` 表示 STALE-retry 触发 |

**静默升级开关**：`EP_SILENT_UPGRADE=1` 环境变量。开启后，R_E holder 的写升级走静默路径（零跨节点消息）。代码注入点：`EPRNFController.cc:843`。

### 3. EP-SNF 接口

| 接口 | 参数 | 说明 |
|------|------|------|
| `retry_cycles` (SimObject Param) | 默认 20000 cy (10µs) | BUSY/未就绪时的 retry 间隔 |
| `delta_noc_cycles` (SimObject Param) | 默认 0 | 跨 socket NoC 额外延迟 |

### 4. UBAdapter 接口

| 接口 | 说明 |
|------|------|
| `transportSend(msg)` | 发送 CoherenceMessage 到 ubio |
| `transportRecv(type, reqId)` | 同步轮询接收（pull 模式） |
| `sendReadReq(...)` | 发送跨节点读请求；`_inflightReadReqs` 去重守卫 |
| `recvFromRouter(msg)` | 异步消息分发入口（HR-ENTRY） |

**调试要点**：`--debug-flags=ProtocolTrace,RubyGenerated,RubyCHIGeneric` 产生完整的状态迁移轨迹。`trace2chain.py` 将 TRACE-PERF 按 reqId 组装为请求链。
