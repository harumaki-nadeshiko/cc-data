# Framework 与程序运行输出契约

> 更新日期：2026-07-29
> 范围：framework、gem5 EP、ubio/UBCC、networksim、guest workload、E2E runner 与分析脚本

## 目的

运行输出不是同一种性质。部分 marker 是 correctness verifier、性能分析或
runner 活性管理的稳定接口；另一些只是开发诊断。目标库集成和日志裁剪时，
必须先区分两者，不能因“看起来很吵”而关闭 verifier 正在消费的证据，也不能
把项目侧 instrumentation 放进不可侵入修改的 framework。

分类如下：

| 类别 | 默认策略 | 修改要求 |
|---|---|---|
| `CORR` correctness | 保持开启 | 改名、改字段或关闭前同步修改 verifier |
| `PERF` performance | 测量运行保持开启 | timed region 内不得输出；字段必须稳定可解析 |
| `OP` operational/liveness | 保持开启 | runner 启停和 watchdog 依赖，不可随意移动 stream |
| `STAT` statistics | 正式矩阵保持开启 | 容量、活动量和归因分析依赖 |
| `DEBUG` diagnostic | 默认关闭 | 新 marker 必须使用 `[DEBUG-...]` 且受 gate 控制 |

## 所有权边界

`framework/` 只提供 transport/runtime contract，例如 `Port`、`MemMessage` 和
基础日志接口。项目侧性能采样策略位于：

```text
protocol/TracePerfPolicy.hh
```

gem5、ubio 和 networksim 可消费该项目 header，但目标 framework 不需要安装、
导出或实现 TracePerf。`CONTROL_SYNC` 等 transport 行为与 TracePerf 输出策略是
两个独立问题。

## Guest 稳定契约

| Marker | 分类 | 生产者 | 消费者 | 关闭后果 |
|---|---|---|---|---|
| `[READ_VAL]` | CORR | `tests/e2e/workloads/e2e_common.h` | `test_e2e.py::parse_read_vals` 和多数 TC verifier | correctness 无法验收 |
| `[BEFORE_RD]` | CORR 辅助 | `e2e_common.h` | interleaved `READ_VAL` fallback | 并发输出破碎时可能误解析 home |
| `[PHASE]` | CORR | `e2e_common.h` | TC116、TC120-TC144 等 | 无法证明场景阶段全部完成 |
| `[SYNC]` | CORR | barrier workloads | TC12 verifier | barrier 覆盖无法验收 |
| `[E2E_META]` | OP/审计 | `e2e_common.h` | 日志审计和 workload total 初始化 | 丢失 testcase/node 归属和总计时起点 |
| `[GUEST-TIMER]` | PERF | `e2e_common.h` | TC verifier、matrix summarizer | guest-visible 性能数据无效 |
| `[PERF-LATENCY]` | PERF | `perf_latency.h` | TC135-TC140、TC142-TC144、TC217 verifier | latency distribution 无效 |
| HA JSONL manifest/sample/validation | CORR+PERF | `e2e_ha_2n1s_core.c` | HA verifier、`summarize_2n1s_guest.py` | portable HA 结果无效 |

`[GUEST-TIMER]` 和 `[PERF-LATENCY]` 的字段顺序、`source=arm_cntvct_el0`
及 `unit=counter_ticks` 均属于 parser contract。计时区间内不得调用这些 emitter、
`printf`、JSON 输出或 progress marker；样本必须先写入固定容量数组，区间结束后
统一输出。

TC142-TC144 新增稳定 phase：

```text
db_oltp_service / db_oltp_end_to_end / db_oltp_batch_32ops
db_btree_service / db_btree_end_to_end / db_btree_batch_64ops
db_wal_service / db_wal_end_to_end / db_wal_batch_32ops
```

## Runner 与活性契约

| 输出 | 分类 | 生产者 | 消费者 | 注意事项 |
|---|---|---|---|---|
| `STEP5 Port enabled node=... socket=... gid=...` | OP | `UBAdapter.cc` | `run_multi.sh` 启动门禁 | 必须保留在 runner 当前搜索的 stdout；改名会导致 300 秒 bind timeout |
| `>>> TC<N> PASSED <<<` | CORR+OP | `verify.py` | `run_multi.sh`、矩阵和队列脚本 | 必须是 verifier log 最后一行 |
| exact TC9 page-fault text | CORR+OP | gem5 fault path | runner、TC9 verifier | 当前依赖文字较脆弱，修改 gem5 fault 文案时同步更新 |
| supervisor manifest/progress | OP | `run_multi.sh` | stall watchdog、审计 | 长任务不得关闭；不用于 guest latency |
| child exit status | OP | runner wrapper | `run_multi.sh` | verifier PASS 不能覆盖子进程失败 |

`[UBADAPTER-STARTUP]` 当前用于确认每个 socket-plane 的 adapter 已启动，是故障
复测的重要 operational evidence；runner 的硬门禁仍是 `STEP5 Port enabled`。

## UBCC 正确性与测试证据

下列 marker 当前被 `verify.py` 导入或被 testcase 精确检查，属于稳定测试接口：

| Marker/prefix | 主要用途 |
|---|---|
| `[UBFAULT-LOAD]` | fault rule 配置加载和 malformed 诊断 |
| `[UBFAULT-TRIGGER]` | fault injection 已实际命中；逐 rule/action 精确计数 |
| `[UBFAULT-DELIVER]` | Delay/Reorder buffered message 实际 release/delivery |
| `[UBCC-NAIVE-EVICT]`、`[UBCC-NAIVE-EVICT-DONE]` | naive overflow/recall 证据 |
| `[UBCC-NAIVE-DIRTY-RECALL-PAYLOAD]` | dirty recall authoritative payload |
| `[RESIDENT-SPILL-START]`、`[RESIDENT-SPILL-DONE]` | spill 生命周期 |
| `[RESIDENT-FILL-ISSUED]`、`[RESIDENT-FILL-DONE]` | backstore onload 生命周期 |
| `[RESIDENT-WAITER-ENQ]`、`[RESIDENT-WAITER-REPLAY-UPGRADE-QUEUED]` | fill/capacity waiter 语义 |
| `[RESIDENT-WAITER-UPGRADE-DROP-NOT-SHARER]` | TC141 negative regression evidence |
| `[UBCC-UPGRADE-COMMIT]`、`[UBCC-OUTER-REQ]` | Upgrade replay 次数与类型 |
| `[UBCC-WB-REQ]`、`[WB-DATA-PERSIST]` | writeback data persistence |
| `[UBCC-SHARED-RELEASE]` | shared release 只移除释放者 sharer bit |
| `[BACKSTORE-WRITE...]`、`[BACKSTORE-READ...]` | spill 活动证据；naive profile 中作为禁止项 |

这些 marker 中有些看起来像诊断日志，但因为 verifier 已消费，它们实际属于
`TEST/CORR`。如果希望减少输出，应先把 verifier 改为消费结构化 counter 或
summary，再删除逐事件 marker，不能先 gate 后保留原 verifier。

## 性能与统计契约

| Marker | 分类 | 消费者 | 说明 |
|---|---|---|---|
| `[EP-PERF] kind=outer` | PERF | `analyze_2n1s_cc.py`、`evaluate_*latency.py`、matrix summarizer | 协议诊断，不是 HA/应用正式计时边界 |
| `[EP-PERF] kind=upgrade_silent/upgrade_network` | PERF | 按 kind 的协议分析 | 仅在对应路径命中时归因 |
| `[TRACE-PERF]` | PERF/诊断 | `trace2chain.py`、`trace_visualizer.py` | 跨组件事务链；普通 correctness PASS 不依赖 |
| `[TRACE-PERF-SUMMARY]` | STAT | 人工审计 | 判断采样/抑制量；当前无硬 parser |
| `[UBCC-STATE]` | STAT+OP | capacity evaluator、watchdog tick | 容量和长任务进展 |
| `[UBCC-STATS]` | STAT+CORR | capacity evaluator、naive-policy verifier | JSON 字段为分析接口 |
| `[ResidentDirStats]` | STAT | TC116、报告脚本 | dir hit/miss/eviction 活动量 |

`TRACE-PERF` 不是必须常开。当前策略支持：

```text
EP_TRACE_PERF=off|sample|full
```

普通 correctness 和正式 guest-visible latency 测试可设为 `off`，减少 host I/O；
只有需要 transaction chain/visualizer 时才设为 `sample` 或 `full`。它不属于
framework public API，也不得把 trace 输出放进 guest timed region。

## Debug-only 输出

满足以下条件的输出才可视为 `DEBUG`：

1. 没有 verifier、runner、watchdog 或分析脚本消费。
2. 不代表 fatal invariant、数据错误或 fault evidence。
3. 默认关闭。
4. 新增时使用 `[DEBUG-<module>-<detail>]`。
5. gem5 可用 `DPRINTF` gate；native component 使用 `_verboseLog`、`_debugLog`
   或更窄的专用 gate。

典型 debug-only family：

```text
[DEBUG-H64-DSM-*]
[DEBUG-TC5-CLEAR-TRACE]
[DEBUG-UBCC-CLEAR]
[DEBUG-UBCC-ORDER]
[DEBUG-UBADAPTER-STARTUP]
gem5 DPRINTF(RubyEP/RubyEPVerbose/...)
```

`[GEM5-SEND]`、`[CLR-CACHE-*]`、`[WAKEUP-*]`、`[RSP-WIRED]`、大量
`[RECALL-DIAG]` 等若无 parser 依赖，应逐步迁为 `[DEBUG-...]` 并默认关闭。
在迁移前需再次搜索消费者，避免 broad substring verifier 造成隐式依赖。

## Networksim 与 Port 输出

framework `Port` 的 bind/connect error、`[PORT-SEND-ERR]` 和非法消息错误属于
`OP/CORR`，不可静默。逐包 `[PORT-SEND]`、`[PORT-RECV]` 受 debug gate 控制，
属于 `DEBUG`。

networksim 的逐包 `[TRACE-PERF]` 只有 trace-chain 运行需要；普通 correctness
可关闭。拓扑加载失败、bind 失败、目标不存在、进程退出异常属于 `OP`，必须
保留并使进程返回非零，而不是只打印后继续。

## 修改检查清单

修改任何输出前执行：

1. 搜索 exact marker 和关键 substring 的所有消费者。
2. 判断它属于 correctness、performance、operational、stats 还是 debug。
3. 若改 schema，同时修改 parser、verifier、summarizer 和文档。
4. 确认 timed region 内无输出 syscall。
5. correctness evidence 不得仅因日志降噪而 gate。
6. debug marker 必须 `[DEBUG-...]` 且默认关闭。
7. 跑 `python3 tests/logging/test_marker_compliance.py`。
8. 跑相关 TC verifier 和至少一个完整 E2E。

## 已知治理缺口

- `tests/logging/test_marker_compliance.py` 的 consumed-marker 清单不是完整 parser
  source of truth；新增或删除 marker 仍需全仓搜索。
- 旧 inventory 的 file line number 会随代码变化，本文件以 producer/consumer
  路径和 marker schema 为准，不把行号当稳定接口。
- 一些 verifier 使用 `SILENT`、`C4`、`DIRECT-FWD` 等宽泛 substring，可能把
  无关诊断计入活动量。后续应替换为 exact marker/counter。
- TC9 依赖 gem5 exact fault text，建议未来增加结构化 expected-fault marker。
