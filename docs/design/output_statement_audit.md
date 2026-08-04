# CC-EP 输出语句审阅与日志分级

> 静态审阅日期：2026-08-04

## 1. 文档目的

本文档审阅当前 CC-EP 实现中的程序输出，包括：

- C/C++ 的 `printf`、`fprintf`、`warn`、`fatal`、`panic`、`LogInfo`。
- gem5 的 `DPRINTF`、`DPRINTFR`、`warn`、`fatal`、`panic`。
- Python 的 `print`。
- Shell 的 `echo`、`printf`。
- workload 通过 `_raw_write()`、`emit_*()` 产生的 guest 输出。
- 写入 stdout、stderr、单独日志文件、TSV、JSON、JSONL、HTML 的输出。

审阅目标是回答：

1. 哪些输出是性能测试的正式数据或可复现性证据。
2. 哪些输出是正确性测试的 oracle 或 verifier 硬依赖。
3. 哪些输出是 fault smoke/qualification 的证据。
4. 哪些输出只用于调试，应默认关闭。
5. 哪些无条件高频输出会扰动性能或放大日志。

本文档是静态代码审阅结果，不表示所有输出都已完成代码整改。登记粒度是
“输出族”而不是每个重复调用点：同一 emitter、同一 marker schema 或同一测试
PASS/FAIL 模板只登记一次，并列出代表位置和消费者。这样既覆盖所有输出情况，
也避免把 200 多个 `emit_read_val()` 调用和数百个同构测试断言机械展开。

## 2. 审阅边界

### 2.1 完整审阅范围

本次完整审阅以下项目自有或项目直接修改、调用的路径：

- `modules/`
- `framework/`
- `protocol/`
- `tools/`
- `tests/e2e/`
- `tests/e2e/workloads/`
- `tests/phase1/` 到 `tests/phase8/`
- `tests/sync_wait/`
- `tests/ubcc/`
- `tests/logging/`
- `scripts/`
- `gem5/src/mem/ruby/protocol/chi/ep/`
- 本项目修改的 CHI generic 路径：
  - `gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm`
  - `gem5/src/mem/ruby/protocol/chi/CHI-cache-funcs.sm`
  - `gem5/src/mem/ruby/protocol/chi/CHI-cache-ports.sm`
  - `gem5/src/mem/ruby/slicc_interface/AbstractController.cc`
- 本项目运行配置：
  - `gem5/configs/ruby/CHI_ubcc_framework.py`
  - `gem5/configs/example/ubcc/`

审阅同时反向搜索了所有文本消费者，包括 Python regex、shell `grep`、最后一行
判定、日志白名单、JSON/TSV reader 和 watchdog 对文件大小增长的依赖。

### 2.2 不逐条登记的范围

`gem5/` 是完整上游模拟器源码，包含其他 ISA、GPU、SystemC、网络设备、第三方库和上游测试。本文不逐条登记与 CC-EP 运行路径无关的上游输出，例如 AMD GPU、RISC-V、SystemC compliance tests 和 googletest 自身输出。

这些输出仍可能在启用对应 gem5 组件或 debug flag 时出现，但不属于当前 CC-EP 协议、runner 或 workload 的输出策略。若未来启用新的 gem5 子系统，应为该子系统补充专项审阅。

### 2.3 搜索方法与覆盖量

本次按以下原语交叉搜索并人工归并：

- C/C++：`printf/fprintf/vfprintf/puts/fputs/perror`、`LogInfo/LogError`、
  `DPRINTF/DPRINTFR`、`warn/fatal/panic`、标准和 gem5 assert。
- Python：`print`、`sys.stdout/stderr.write`、`json.dump`、文件 `write/write_text`、
  subprocess stdout/stderr 配置。
- Shell：`echo/printf/tee`、stdout/stderr 合并、`/dev/null`、artifact 追加和命令替换。
- Guest：`_raw_write()` 及所有 `emit_*()` 调用。
- Consumer：regex、substring、`grep`、最后一行匹配、计数、顺序、JSON/TSV reader。

静态搜索在测试/workload 范围发现约 730 个显式输出或底层 write 位置；公共
emitter 被复用时按 schema 归并，例如 `emit_read_val` 约 206 个调用、
`emit_phase_done` 约 210 个调用。核心 native/EP 路径另有数百个直接输出点和
大量 SLICC `DPRINTF/assert`。精确数量会随代码变化，本文以输出族和
producer-consumer 映射作为稳定审阅结果。

## 3. 分类定义

本文使用以下分类。

| 分类 | 定义 | 默认策略 |
|---|---|---|
| `BOOT` | 启动、拓扑、资源预算、二进制或配置 manifest | 保留，低频 |
| `PERF_RESULT` | 正式性能结果、延迟、计数和终局统计 | 保留，结构化 |
| `PERF_TRACE` | 逐消息或逐事务性能 trace | 默认关闭或有界采样 |
| `CORRECTNESS_ORACLE` | verifier 直接解析的正确性结果 | 必须保留且格式稳定 |
| `FAULT_EVIDENCE` | fault rule 加载、命中、释放和恢复证据 | fault 模式必须保留 |
| `LIVENESS` | supervisor 的进展、heartbeat 和 stall 判断输入 | 保留，但应结构化低频 |
| `ERROR` | 可恢复异常、配置错误、传输错误 | 保留，必要时限流 |
| `FATAL` | 不变量破坏或不可恢复错误 | 始终保留 |
| `DEBUG` | 定点调试、逐状态或逐消息诊断 | 默认关闭 |
| `ARTIFACT` | TSV、JSON、JSONL、HTML 等机器可读产品 | 保留，不与普通日志混写 |

同一输出可以服务多个用途。例如 `[GUEST-TIMER]` 同时属于性能结果和部分 testcase 的正确性 oracle。

消费者依赖再分三级：

| 等级 | 含义 | 修改要求 |
|---|---|---|
| `HARD` | 缺失或改格式会改变 runner 控制流、使测试失败或使检查被错误绕过 | 不得单独修改 producer |
| `STRUCTURED` | parser 依赖字段名、顺序、计数或最后一行 | producer、parser、schema 和测试必须原子更新 |
| `SOFT` | 只影响报告、人工诊断或可选性能分析 | 可 gate，但应保留失败诊断和 provenance |
| `NONE` | 仓库内未发现消费者 | 可归 DEBUG；仍需判断是否是尚未接入 verifier 的必要证据 |

## 4. 总体结论

### 4.1 必须保留的输出

以下输出存在 parser、verifier、runner 或合同证据的硬依赖，不能直接删除或随意改变格式：

- guest `[READ_VAL]`。
- verifier 最后一行 `>>> TC<N> PASSED <<<` 或 `>>> TC<N> FAILED <<<`。
- gem5 端口就绪文本 `STEP5 ... Port enabled`。
- `[GUEST-TIMER]` 和 `[PERF-LATENCY]`。
- portable workload 的 `[E2E_META]`、`[TOPOLOGY]`、`[PORTABLE-PRESSURE]`。
- fault 的实际 `[UBFAULT] ... rule='...' action=...` 命中记录。
- Delay/Reorder 的 `[UBFAULT-DELIVER]` 应作为 qualification 证据保留；当前 verifier
  尚未导入该 marker，这是测试覆盖缺口而不是删除依据。
- `[UBCC-STATS]`、`[ResidentDirStats]` 和 `[TRACE-PERF-SUMMARY]`。
- 各 testcase verifier 明确检查的专用 marker。
- child PID、exit status、matrix TSV、progress JSON 和 supervisor status。
- `fatal`、`panic`、重要 `warn`、传输初始化和配置错误。

### 4.2 当前最严重的问题

以下输出位于热路径且多数无条件执行：

1. `UBCCController.cc` 的 outer request、resident miss/fill/replay、recall、invalidate、upgrade、writeback 和 fanout 逐事件输出。
2. `ubio_main.cc` 的每条 coherence message 接收输出。
3. gem5 EP 的 EPSNF 数据 beat、QLM/writeback retry、EPRNF snoop/upgrade 生命周期输出。
4. 无条件 `[EP-PERF]` 每事务输出。
5. `appendTmpLog()` 每条记录执行 `fopen("a")`、`vfprintf`、`fclose`。
6. workload 每次内存操作前后的 `[PROGRESS]`、`[BEFORE_WR]`、`[AFTER_WR]`。

这些输出会影响宿主机 wall-clock、容器调度、文件系统写入量和长时间性能矩阵的稳定性。它们不能作为“免费观测”。

### 4.3 已有较好实现

- `framework/Port.cc` 的每消息 `[PORT-SEND]`、`[PORT-RECV]` 已由 `EP_DEBUG_PORT=1` 控制，默认关闭。
- `TracePerfPolicy` 支持 `full|sample|off`、first-N、every-K 和最大记录数。
- gem5 `DPRINTF/DPRINTFR` 默认关闭。
- `UBIO_DEBUG_PERF`、`UBCC_DEBUG_CLEAR`、EPBackend `_verboseLog` 和 H64 debug 字段已经覆盖部分定点诊断。

这些机制应成为其余输出整改的基础。

## 5. 输出通道与机制

### 5.1 stdout 和 stderr

当前代码没有严格执行通道职责。普通协议事件、性能数据、错误和调试信息同时分布在 stdout 和 stderr。

建议目标：

- stdout：机器可读测试结果、guest oracle、明确的数据产品。
- stderr：启动信息、warning、error、fatal 和人工诊断。
- 独立 artifact 文件：TSV、JSON、JSONL、HTML、trace chain。
- 不应依赖“stderr 一定表示失败”或“stdout 一定是数据”，因为当前实现尚不满足该约束。

### 5.2 `framework::LogInfo`

定义：`framework/Log.cc:7-15`。

当前行为：

- 无日志级别。
- 无环境开关。
- 无采样或速率限制。
- 直接写 stderr。
- 很多调用格式串已含 `\n`，`LogInfo` 又追加换行，可能产生空行。

结论：当前 `LogInfo` 不能视为低成本 info logger。UBCC 热路径中的 `LogInfo` 应纳入统一日志 gate。

### 5.3 `warn`、`fatal`、`panic`

定义：`modules/ubiomodule/ubio_base.hh:35-49`。

- `warn`：写 stderr，继续运行。
- `fatal`、`panic`：写 `PANIC:` 并 `abort()`。

这些输出原则上必须保留。对 fault storm 下可能重复的 `warn`，可以增加计数和速率限制，但不能静默吞掉首次错误及终局汇总。

### 5.4 gem5 `DPRINTF/DPRINTFR`

EP 和 CHI generic 中的大量详细输出使用 `RubyEP`、`RubyCHIGeneric`、`RubyGenerated` 或 `RubySlicc` debug flag。

这些输出默认关闭，分类为 `DEBUG`。性能运行不得启用宽泛 debug flags；定点诊断应限定 flag、地址、时间窗和最大日志量。

### 5.5 `TracePerfPolicy`

定义：`protocol/TracePerfPolicy.hh`。

支持：

- `EP_TRACE_PERF=off`
- `EP_TRACE_PERF=sample`
- `EP_TRACE_PERF=full`
- `EP_TRACE_PERF_FIRST_N`
- `EP_TRACE_PERF_EVERY`
- `EP_TRACE_PERF_MAX`

注意：

- sample 默认 first 500，最大 2000。
- full 模式只有显式设置 `EP_TRACE_PERF_MAX` 才有上限。
- 每个进程退出时输出 `[TRACE-PERF-SUMMARY]`。

性能矩阵默认应使用 `off`；只有 trace 性能实验使用 bounded `sample`。禁止无上限 `full` 用于长时间容量矩阵。

### 5.6 `appendTmpLog`

定义：`modules/ubiomodule/UBCCController.cc:22-37`。

当前写入：`/workspace/tmp_logs/<name>`。

调用族：

- `ubcc_outer_req.log`
- `ubcc_inv_ack.log`
- `ubcc_clear.log`
- `ubcc_fill_complete.log`

问题：

- 无开关。
- 绕过 runner 对 stdout/stderr 的日志管理。
- 每条记录都打开和关闭文件。
- 可能与多进程并发追加竞争。

结论：纯 `DEBUG`，应默认关闭。若保留，应使用显式环境开关和进程唯一文件名，或改为统一 buffered logger。

### 5.7 标准 `assert`、gem5 assert 与测试断言

项目自有运行时代码、CHI SLICC 和 host test 中存在大量 `assert`、`gem5_assert`
及 `panic_if/fatal_if`：

- 成功时不产生输出。
- 失败时通常向 stderr 输出表达式、文件和行号并终止。
- 标准 `assert` 在 `NDEBUG` 下会完全移除。
- `panic_if/fatal_if` 和 gem5 `fatal` 不受 `NDEBUG` 影响，应承担生产不变量。

分类：运行时协议不变量属于 `FATAL`，必须保留；测试程序中的标准 `assert` 属于
正确性判定，但不能在可能启用 `NDEBUG` 的构建中作为唯一判据。测试必须同时以
显式非零退出码表达失败，不能在断言被编译掉后仍打印 `passed`。

### 5.8 直接 syscall、pipe 和重定向

- guest `_raw_write()` 直接向 fd 1 写 stdout，绕过 libc buffering，见
  `tests/e2e/workloads/e2e_common.h`。这些记录仍可能与多 CPU 输出交错。
- shell/Python 将子进程 stdout/stderr 合并到 case 日志，输出重定向本身是 artifact
  生产路径，不能只审阅 `print`/`printf`。
- `tools/launcher.py` 把 networksim stdout/stderr 接到无人读取的 `PIPE`。日志超过
  pipe 容量后可能阻塞 networksim 和整套运行，这是输出相关的正确性风险；应写
  独立日志文件或持续 drain。

## 6. 性能测试必须保留的输出

### 6.1 Guest 性能结果

| Marker | 定义位置 | 用途 | 策略 |
|---|---|---|---|
| `[GUEST-TIMER]` | `tests/e2e/workloads/e2e_common.h:185-209` | phase 和 workload total ticks | 保留，格式稳定 |
| `[PERF-LATENCY]` | `tests/e2e/workloads/perf_latency.h:57-92` | min/p50/p95/p99/max/mean | 保留，每 phase 一条 |
| HA `kind=sample` JSONL | `e2e_ha_2n1s_core.c` | HA 样本 | 保留为 artifact |
| HA `kind=manifest` | 同上 | 配置和样本上下文 | 保留 |

解析方包括：

- `tests/e2e/test_e2e.py`
- `scripts/summarize_database_perf_matrix.py`
- `scripts/summarize_tc135_perf_matrix.py`
- `scripts/summarize_p0_512k_round.py`
- `scripts/evaluate_capacity_latency.py`
- `scripts/evaluate_protocol_latency.py`

### 6.2 协议性能结果

| Marker | 位置 | 评价 |
|---|---|---|
| `[EP-PERF] kind=outer` | `EPBackend.cc` | 有价值，但当前无条件逐事务输出 |
| `[EP-PERF] kind=upgrade_silent` | `EPBackend.cc` | 有价值，高频风险更高 |
| `[EP-PERF] kind=upgrade_network` | `EPBackend.cc` | 有价值，但应受性能采样 gate 控制 |
| `[TRACE-PERF]` | gem5/ubio/networksim | 结构统一，已有策略 gate |
| `[TRACE-PERF-SUMMARY]` | `TracePerfPolicy.hh` | 证明采样和 suppression 数量 |

建议：

- 将 `[EP-PERF]` 纳入独立 bounded policy，不能继续无条件输出。
- 常规性能矩阵只保留 guest 聚合结果和终局统计。
- protocol trace 实验单独运行，不与主吞吐/延迟矩阵混合。

### 6.3 终局统计

必须保留：

- `[ResidentDirStats]`：`modules/ubiomodule/ResidentDir.cc`。
- `[UBCC-STATS]`：`modules/ubiomodule/ubio_main.cc` 和 `UBCCController` 统计导出。
- `[TRACE-PERF-SUMMARY]`。

终局统计应成为逐事件日志的替代品。例如 spill/fill/evict 应优先用计数器汇总，而不是每 cache line 输出。

### 6.4 启动和配置 manifest

性能结果必须能证明实际配置。建议保留：

- `[ResidentDir]`
- `[ResidentDir-BUDGET]`
- `[UBIO-MANIFEST]`
- `[UBIO-H64-HOST]`
- `[UBIO-POLICY]`
- `[RUNNER-MANIFEST]`
- `[E2E_META]`
- `[TOPOLOGY]`
- `[PORTABLE-PRESSURE]`
- gem5 `[EPBACKEND-MANIFEST]`
- `CHI_ubcc_framework.py` 的 `[UBCC-CONFIG]`、`[Q1-DSM-MAP]`

这些输出应每实例或每 plane 最多一条，不得放在事务路径中。

## 7. 正确性测试必须保留的输出

### 7.1 通用 guest oracle

#### `[READ_VAL]`

定义：`tests/e2e/workloads/e2e_common.h:261-280`。

该 marker 是大多数 testcase 的核心 oracle。字段和格式均被正则解析：

```text
[READ_VAL] node=<n> home=<h> offset=<...> expected=<hex> actual=<hex> MATCH|MISMATCH
```

不能删除、改字段名或将十六进制改成其他格式。

`test_e2e.py` 还包含并发输出损坏后的 fallback。它会利用附近的 `[BEFORE_RD]` 恢复 home 字段。因此，在彻底保证 guest 输出原子性之前，不能全局删除 `[BEFORE_RD]`。

#### `[PHASE]`

定义：`e2e_common.h:282-292`。

TC116、TC121-TC147 等 verifier 依赖指定 phase 完成 marker。必须保留 testcase 明确要求的 phase。

#### `[SYNC]`

定义：`e2e_common.h:294-309`，并由 TC12 等使用。

TC12 verifier 检查同步阶段，不能删除。

### 7.2 Runner/verifier sentinel

必须保持 verifier 日志最后一行为：

```text
>>> TC<N> PASSED <<<
```

或：

```text
>>> TC<N> FAILED <<<
```

`run_multi.sh` 使用最后一行判断结果。即使 verifier exit code 为 0，在 sentinel 后增加额外输出也会导致 runner 判失败。

### 7.3 gem5 端口就绪 marker

`UBAdapter.cc` 输出：

```text
STEP5 ... Port enabled
```

`run_multi.sh` 在启动 UBIO 前 grep 此 marker。它是控制流协议的一部分，不能删除或随意改名。

### 7.4 Testcase 专用 marker

以下 marker 被 verifier 直接解析或 grep：

| Marker | 代表 testcase |
|---|---|
| `[FLAG_SEEN]` | TC13 |
| `[PHASE_RD]` | TC14 |
| `[READ_PHASE]` | TC17 |
| `[EPOCH_WRAP]` | TC27 |
| `[META_REL]` | TC28 |
| `[TC29_UPG]` | TC29 |
| `[TC30_CLR]` | TC30 |
| `[TC32_LAT]` | TC32 |
| `[TC33_WB]` | TC33 |
| `[TC35_PROGRESS]` | TC35 |
| `[TC36_GE]` | TC36 |
| `[TC37_GM]` | TC37 |
| `[TC38_CLR]` | TC38 |
| `[TC39_ROUTE]` | TC39 |
| `[TC40_RECALL]` | TC40 |
| `[TC41_PHASE]` | TC41 |
| `[TC42_EPOCH]` | TC42 |
| `[TC43_ROUND]` | TC43 |
| `[TC44_PATH]` | TC44 |
| `[TC45_STRESS]` | TC45 |
| `[TC46_BYTE]`、`[TC46_SUMMARY]` | TC46 |
| `[TC63_ORPHAN]` | TC63 |
| `[TC64_ORPHAN]` | TC64 |

这些输出的更改必须同步修改 verifier，并重新验证 testcase。

### 7.5 协议正确性证据

现有 verifier 还依赖部分协议日志：

- `[UBCC-NAIVE-EVICT]`
- `[UBCC-NAIVE-DIRTY-RECALL-PAYLOAD]`
- `[UBCC-NAIVE-EVICT-DONE]`
- `RESIDENT-SPILL-START/DONE`
- `RESIDENT-FILL-ISSUED/DONE`
- `UBCC-SPILL-DIRTY-PERSIST`
- `UBCC-SHARED-RELEASE`
- `RESIDENT-WAITER-UPGRADE-DROP-NOT-SHARER`
- `UBCC-UPGRADE-COMMIT`
- `RESIDENT-WAITER-REPLAY-UPGRADE-QUEUED`
- `UBCC-OUTER-REQ`
- `UBCC-WB-REQ`
- `WB-DATA-PERSIST`

代表依赖：TC125-TC129、TC141、TC200-TC203。

这些 marker 暂时不能直接删除。但长期应将“test oracle”与普通调试日志分离：

- 为 qualification 增加显式、低频、结构化 evidence marker。
- verifier 不再依赖全量逐事务 debug 文本。
- 性能模式可以关闭普通协议 trace，而不破坏 correctness verifier。

## 8. Fault 测试必须保留的输出

### 8.1 `[UBFAULT]` rule load

位置：`modules/ubiomodule/ubio_main.cc`。

用途：证明规则被正确解析和加载。malformed rule 必须输出错误。

注意：仅有 loaded marker 不足以判定 fault 发生。

### 8.2 `[UBFAULT]` fired record

TC148-TC159 的 verifier 使用如下模式计数：

```text
[UBFAULT] ... rule='<rule-id>' ... action=Drop|Duplicate|Delay|Reorder
```

必须保留字段：

- node
- rule name
- action
- message type
- src/dst
- PA
- reqId
- delay ticks，若适用

每条 rule 的实际命中记录不能被普通 debug gate 关闭。

### 8.3 `[UBFAULT-DELIVER]`

Delay/Reorder 需要同时证明：

1. 消息被 hold/delay/reorder。
2. 消息最终实际 release/deliver。

因此 `[UBFAULT-DELIVER]` 是 qualification 必要证据，不能只保留初始 action marker。
但当前 `verify.py` 的 UBIO 日志白名单只导入包含完整子串 `[UBFAULT]` 的行，
`[UBFAULT-DELIVER]` 不满足该条件；仓库内也没有其他自动消费者。当前依赖等级是
`NONE`，目标依赖等级应为 `HARD`。在接入 verifier 前，现有自动测试不能证明
Delay/Reorder 的消息最终被实际投递。

### 8.4 Recovery 证据

当前 fault qualification 还使用以下恢复 marker：

- `[UBCC-INVALIDATE-RETRY]`
- exact UpgradeReq replay 的 UBCC 日志。
- `[UPGRADE-DIAG] UpgradeAck`
- `[UPGRADE-DIAG] UpgradeDone`
- `[EP-UPGRADE-STALE-REJECT]`
- recall retry/timeout 日志。
- `[UBCC-UPGRADE-COMMIT]`

建议将这些 marker 收敛为专用 `FAULT_EVIDENCE` 或 `RECOVERY_EVIDENCE` 输出，并只在 fault qualification 或显式 recovery trace 模式启用。最终正确性仍由数据 oracle 和状态 drain 判定，不能仅靠恢复 marker 存在就 PASS。

## 9. Supervisor 和 liveness 输出

### 9.1 必须保留的状态文件

`run_multi.sh` 写入：

- networksim PID 和 exit code。
- 各 gem5 PID 和 exit code。
- 各 UBIO PID 和 exit code。
- supervisor manifest。
- supervisor status。

这些是进程归属、清理和最终判定的机器状态，不是 console 装饰输出。

### 9.2 Supervisor status

重要记录：

- `FAULT ... gem5_exit`
- `FAULT ... progress_stall`
- `FAULT ... log_size`
- `FAULT ... disk_free`
- `COMPLETE ...`
- 周期性 `OK ... guest_bytes=... protocol_tick=...`

这些输出应保留在独立 status 文件中。

### 9.3 当前对日志增长的隐式依赖

当前 supervisor 会使用：

- guest simout 文件大小增长。
- UBIO stderr 中的 `tick=<N>`。
- 某些普通 watchdog 使用 UBIO stderr 文件大小增长。

因此直接关闭大量协议输出前，必须先保证 liveness 由明确 heartbeat/progress counter 提供，不能继续将“日志文件变大”等同于“协议有进展”。

推荐新增固定低频 heartbeat：

```text
[PROTOCOL-HEARTBEAT] tick=<N> completed=<N> outstanding=<N> retries=<N>
```

性能模式只保留 heartbeat 和终局 stats，不依赖普通 debug 文本维持 watchdog。

## 10. 仅调试、应默认关闭的输出

### 10.1 Native UBCC/UBIO

应默认关闭的输出族：

- `[UBCC-OUTER-REQ]`
- `[UBCC-GSRS-FAST]`
- `[UBCC-SHARER-UPGRADE]`
- `[UBCC-INVALIDATE-CREATE]`
- `[RECALL-CREATE]`
- `[UBCC-GRANT-READY]`
- `[RECALL-TRACE-A]`
- 普通 `[RECALL-DIAG]`
- `[UBCC-INV-ACK]`、`[UBCC-INV-DONE]`
- 正常 `[UBCC-UPGRADE-*]` 生命周期轨迹
- `[UBCC-WB-ENTER]`、普通 `[UBCC-WB-REQ]`
- `[UBCC-HOME-WB*]`
- `[UBCC-QUEUE-REPLAY*]`
- 普通 `[PUSH-GRANT]`
- 普通 `[UBCC-FANOUT]`
- `[RESIDENT-MISS]`
- `[RESIDENT-FILL-ISSUED]`
- `[RESIDENT-WAITER-ENQ]`
- `[RESIDENT-WAITER-REPLAY]`
- `[RESIDENT-CAPACITY-REPLAY]`
- `[RESIDENT-FILL-DONE]`
- `[RESIDENT-SPILL-START/DONE]` 的非 qualification 全量版本
- Legacy backstore 的每 read/write/chain 输出
- `[ubio:<nid>] gem5 recv ...`
- `[ubio:<nid>] net recv ...`
- `[UBIO-PRE-EMIT]`、`[UBIO-POST-EMIT]`
- `appendTmpLog()` 全部输出

### 10.2 gem5 EP

默认关闭或改为受控 evidence 的输出族：

- `[EPSNF-WRITE-DBID]`
- `[EPSNF-DATA-RECV]`
- `[EPSNF-WRITE-DONE]`
- `[EPSNF-WB-PENDING]`
- `[EPSNF-WB-DEDUP]`
- `[EP-QLM-PENDING]`
- `[EPSNF-QLM-READY]`
- `[EPSNF-WB-QLM-ENQ]`
- `[EPSNF-WB-RETRY]`
- `[EPSNF-WB-DONE]`
- `[QLM-CACHED-REQID]`
- `[QLM-WAIT-REQID]`
- `[QLM-SENT]`
- `[QLM-FOUND]`
- 正常 `[UPGRADE-DIAG]` pending/deferred/Ack/Done
- 正常 snoop conflict/stale 逐消息轨迹
- `[METARNF-WRITE-COALESCE]`
- `[METARNF-WRITE-QUEUE]`
- `[METARNF-WRITE-DEQUEUE]`
- 硬编码地址窗口 `[META-TRACE]`
- `[C4-ESNF-WNOSNP]`、`[C4-EPSNF-BEAT]`

以下属于错误或不变量，仍需保留：

- `[EPSNF-DATA-NO-PENDING-WRITE]`
- `[EP-WB-QLM-FAIL]`
- `[EPSNF-WB-QLM-FAIL]`
- `[EPSNF-WB-TERMINAL]`
- recall error 系列
- `warn`、`fatal`、`panic`

注意：`[EPSNF-WB-FATAL]` 当前只是字符串前缀，并未调用 `fatal()`。应改名为 terminal/error，或真正执行确定性 fatal；不能继续用误导性名称。

### 10.3 Workload

优先关闭或限频：

- `[BEFORE_WR]`
- `[AFTER_WR]`
- 普通 `[BEFORE_RD]`，但需先移除 parser fallback 依赖
- 每操作 `[PROGRESS]`
- 旧式逐样本 `[LATENCY]`，若已有 `[PERF-LATENCY]` 聚合
- `[STORM]`
- 仅报告用途的 `[TC112_LOCAL]`
- `[TC113_UPG]`、`[TC113_DONE]`
- `[TC115_CPU0]`、`[TC115_CPU2]`

长 workload 的进度应改为按固定操作数或固定 guest 时间输出低频 heartbeat，不应每次内存访问输出。

### 10.4 Python 配置和诊断

以下属于 DEBUG/operator 信息：

- `[FAULT-DEBUG]`
- 大部分 `[E2E-Q2]`
- memory object 全量 dump
- `[MINIMAL]`
- `[DIAG]`
- `[PORT-TEST]`
- `[DIRECT-BIND]`
- `[TINY]`
- `[NO-UNPROXY]`
- `CHI_ubcc_framework.py` 的 `[Q3 DEBUG]`

它们应只在对应诊断脚本或显式 verbose 模式中出现。

## 11. 高频输出风险清单

### 11.1 极高风险

| 输出族 | 风险原因 |
|---|---|
| UBCC 逐 outer request/ack/clear/writeback/replay | 单事务多通道重复输出 |
| UBIO 每 coherence message recv | `EP_TRACE_PERF=off` 时仍持续输出 |
| EPSNF 每 data beat | 位于数据热路径，且多处 `fflush(stderr)` |
| QLM/writeback pending/retry | fault/no-response 时按重试放大 |
| `appendTmpLog` | 每条同步打开、写入和关闭文件 |
| 无界 `EP_TRACE_PERF=full` | 长运行可生成多 GB 日志 |

### 11.2 高风险

| 输出族 | 风险原因 |
|---|---|
| 无条件 `[EP-PERF]` | 性能采集本身扰动性能 |
| resident spill/fill/evict 每 line 输出 | 512 KiB/150% 压力矩阵中规模巨大 |
| EPRNF snoop/upgrade 生命周期 | contention 下接近逐 snoop |
| MetaRNF queue/coalesce/dequeue | metadata 压力下高频 |
| Legacy backstore 每操作输出 | legacy schema 下按 read/write 放大 |

### 11.3 中风险

| 输出族 | 风险原因 |
|---|---|
| Barrier ARRIVED/RELEASE | 随节点数和 barrier 次数增长 |
| TC121 `[PROGRESS]` | 默认每 store 前后输出 |
| TC120 `[PROGRESS]` | 每操作两条 |
| TC131/TC134 progress | 长 run 持续，但有 liveness 价值 |
| D1/C1/A3/A5 before/after | pressure 扩大后线性增长 |

## 12. 按文件的输出族登记

### 12.1 `modules/ubiomodule/UBCCController.cc`

| 输出族 | 分类 | 性能模式 |
|---|---|---|
| init/config、peer exit | BOOT/ERROR | 保留低频 |
| `[UBCC-STATE]` | LIVENESS/DEBUG | 用低频 heartbeat 替代 |
| `[UBCC-STABLE-*]` | DEBUG | 默认关闭，仅 stall dump |
| `[RESIDENT-*]` | CORRECTNESS/DEBUG | 普通逐事件关闭；必要 evidence 保留 |
| `[UBCC-NAIVE-*]` | CORRECTNESS/DEBUG | 统计替代；qualification 可开 |
| `[UBCC-ASYNC-WB]` | DEBUG | 默认关闭 |
| `[UBCC-OUTER-REQ]` | DEBUG/部分 verifier 依赖 | 迁移 verifier 后关闭 |
| queue/grant/recall/invalidate/upgrade trace | DEBUG/部分 correctness | 分离 evidence 与 debug |
| `[UBCC-latency] [UBST]` | PERF_TRACE | 默认关闭或采样 |
| writeback/evict trace | DEBUG/部分 correctness | 默认关闭，保留 error/evidence |
| Clear mismatch/warn | ERROR | 保留 |
| tombstone/double commit/invariant | CORRECTNESS/FATAL | 保留 |
| retry/exhaustion | FAULT_EVIDENCE/ERROR | fault 模式保留 |
| `[UBCC-STATS]` | PERF_RESULT | 保留终局 |

### 12.2 `modules/ubiomodule/ubio_main.cc`

| 输出族 | 分类 | 性能模式 |
|---|---|---|
| `[UBFAULT]`、`[UBFAULT-DELIVER]` | FAULT_EVIDENCE | 无 fault rule 时自然无输出 |
| `[TRACE-PERF]` | PERF_TRACE | off 或 bounded sample |
| H64 PDES debug | DEBUG | 默认关闭 |
| legacy backstore per-op | DEBUG | 默认关闭 |
| startup/config/manifest | BOOT | 保留低频 |
| per-message recv | DEBUG | 默认关闭 |
| terminate/stats | PERF_RESULT | 保留 |
| bad payload/unhandled/error | ERROR | 保留、限流 |
| PRE/POST emit | DEBUG | 删除或默认关闭 |

### 12.3 `framework/Port.cc`

| 输出族 | 分类 | 策略 |
|---|---|---|
| `[PORT-CFG-WARN]` | ERROR | 保留 |
| bind/connect/send error | ERROR | 保留 |
| endpoint manifest | BOOT | 保留一次 |
| `[PORT-SEND]`、`[PORT-RECV]` | DEBUG | 已由 `EP_DEBUG_PORT` 默认关闭 |

### 12.4 `modules/networksim/`

| 输出族 | 分类 | 策略 |
|---|---|---|
| topology/startup/done | BOOT | 保留 |
| `[TRACE-PERF]` | PERF_TRACE | 使用 policy |
| `[NSIM-STAT]` | LIVENESS/PERF | 低频保留 |
| no route/no buffer/miss | ERROR | 保留首次并计数汇总 |

### 12.5 `gem5/.../ep/EPBackend.cc`

| 输出族 | 分类 | 策略 |
|---|---|---|
| `[EPBACKEND-MANIFEST]` | BOOT | 保留 |
| `[EP-PERF]` | PERF_TRACE/RESULT | 纳入 bounded policy |
| recall error | ERROR | 保留 |
| QLM pending/retry | DEBUG | 默认关闭 |
| upgrade pending/stale reject | FAULT/CORRECTNESS | stale/error 保留；正常 pending gate |
| `_verboseLog` 输出 | DEBUG | 默认关闭 |
| `DPRINTF` | DEBUG | 默认关闭 |
| fatal/warn | ERROR/FATAL | 保留 |

### 12.6 `EPRNFController.cc`

| 输出族 | 分类 | 策略 |
|---|---|---|
| snoop immediate/stale | CORRECTNESS/DEBUG | qualification gate |
| retry/drop recovery | FAULT_EVIDENCE | fault/recovery gate |
| normal UpgradeAck/Done | DEBUG/qualification | 默认关闭，qualification 开 |
| recall proxy | CORRECTNESS/DEBUG | 定向 gate |
| `DPRINTF` | DEBUG | 默认关闭 |
| fatal/warn | ERROR/FATAL | 保留 |

### 12.7 `EPSNFController.cc`

| 输出族 | 分类 | 策略 |
|---|---|---|
| write DBID/data beat/done | DEBUG | 默认关闭 |
| C4 data beat | CORRECTNESS qualification | 仅 C4 模式 |
| QLM/writeback pending/retry | DEBUG | 默认关闭 |
| QLM terminal/no pending write | ERROR | 保留 |
| misleading `EPSNF-WB-FATAL` | ERROR | 改名或真正 fatal |

### 12.8 `MetaRNFController.cc`

| 输出族 | 分类 | 策略 |
|---|---|---|
| queue/coalesce/dequeue | DEBUG | 默认关闭，改计数器 |
| hard-coded `[META-TRACE]` | DEBUG | 改显式地址/模式 gate |
| fatal/warn | ERROR/FATAL | 保留 |

### 12.9 `UBAdapter.cc`

| 输出族 | 分类 | 策略 |
|---|---|---|
| STEP5 Port enabled | BOOT/control dependency | 必须保留 |
| startup/exit/barrier | BOOT/LIVENESS | 低频保留 |
| `[TRACE-PERF]` | PERF_TRACE | 使用 policy |
| first-N wakeup/send diagnostics | DEBUG | 可删除或 gate |
| response/control queue trace | DEBUG | 默认关闭 |
| QLM trace | DEBUG | 默认关闭 |
| transport warn/fatal | ERROR/FATAL | 保留 |

### 12.10 `tests/e2e/test_e2e.py`、`verify.py`

| 输出族 | 分类 | 策略 |
|---|---|---|
| compile/config error | ERROR | 保留 |
| setup memory/object dump | DEBUG | verbose gate |
| per-case verify summary | CORRECTNESS_ORACLE | 保留 |
| mismatch detail | ERROR | 保留 |
| final sentinel | CORRECTNESS_ORACLE/control | 必须最后输出 |
| suite summary | ARTIFACT/operator | 保留 |

### 12.11 `tests/e2e/run_multi.sh`

| 输出族 | 分类 | 策略 |
|---|---|---|
| launch/bind/operator status | BOOT/operator | 保留 |
| PID/exit files | ARTIFACT/control | 必须保留 |
| supervisor manifest/status | LIVENESS/control | 必须保留 |
| runner manifest | BOOT/provenance | 保留 |
| fault rule print | FAULT/provenance | 保留 |
| PASS/FAIL summary | CORRECTNESS | 保留 |

### 12.12 `tests/e2e/workloads/`

| 输出族 | 分类 | 策略 |
|---|---|---|
| `[READ_VAL]` | CORRECTNESS_ORACLE | 必须保留 |
| `[PHASE]`、`[SYNC]` | CORRECTNESS/LIVENESS | 指定 testcase 保留 |
| `[GUEST-TIMER]` | PERF_RESULT | 保留聚合 |
| `[PERF-LATENCY]` | PERF_RESULT | 保留聚合 |
| `[E2E_META]`、`[TOPOLOGY]` | BOOT/provenance | 保留 |
| `[PORTABLE-PRESSURE]` | BOOT/correctness | 必须保留 |
| testcase 专用 marker | CORRECTNESS_ORACLE | 按 verifier 保留 |
| before/after/progress | DEBUG/LIVENESS | 默认关闭或限频 |
| HA JSONL | ARTIFACT | 格式稳定、独立保存 |

### 12.13 `scripts/`

| 输出族 | 分类 | 策略 |
|---|---|---|
| build/usage/error | operator/ERROR | 保留 |
| matrix TSV | ARTIFACT | 格式稳定 |
| summary JSON | ARTIFACT | stdout 不混入 debug |
| HTML stdout | ARTIFACT | 必须重定向为文件 |
| completion message | operator | 保留 |

### 12.14 `tests/sync_wait/`

sync_wait workload 使用裸文本作为严格的全局时序 oracle：

| 输出族 | 生产者 | 消费者 | 分类与策略 |
|---|---|---|---|
| `BEFORE_BARRIER`、`AFTER_BARRIER` | `tc_t0_1.c`、`tc_t0_2.c` | `test_sync_wait.py` 计数并按 syscall tick 排序 | CORRECTNESS，必须保留 |
| `CALLER`、`NON_CALLER`、`NON_CALLER_DONE` | `tc_t0_3.c` | non-caller 隔离和顺序检查 | CORRECTNESS，必须保留 |
| `BEFORE/AFTER_BARRIER_R1/R2` | `tc_t0_4.c` | 两轮 barrier 顺序检查 | CORRECTNESS，必须保留 |
| `SYNC_WAIT_RET=-22`、`PASS_ERROR_RETURNED`、`FAIL_NO_ERROR` | `tc_t0_5.c` 到 `tc_t0_7.c` | 负参数测试 | CORRECTNESS，必须保留 |
| 完整 timeline 重印、每项 PASS | `test_sync_wait.py` | 人工/CI | DEBUG/operator，成功时可 quiet，失败时保留 |

这里的 workload 每行输出还会与 gem5 `SyscallBase` write trace 对齐。改变行数、合并
marker 或在中间插入额外 stdout 都可能破坏全局 tick timeline，依赖等级为 `HARD`。

### 12.15 `tests/phase1/` 到 `tests/phase8/`

| 输出族 | 生产/消费 | 分类与策略 |
|---|---|---|
| `TEST ... OK`、`FAIL: file:line`、Passed/Failed summary | phase1 H64 reference test | FAIL 和终局 summary 为 CORRECTNESS；逐项 OK 可由 verbose 控制 |
| Python `PASS/FAIL`、`TOTAL` | phase1-phase3 topology/instantiate/symbol tests | 终局 summary 与失败明细保留；逐项 PASS 可 quiet |
| `M4` 到 `M8 ...: PASS|FAIL|SKIP` | EP self-test 输出，由 phase4-phase8 Python regex 捕获 | CORRECTNESS/STRUCTURED，不可改自由文本格式 |
| `M*_SELF_TEST_PASSED=1`、`M*_SELF_TEST_FAILED=1` | EP self-test 总 sentinel | CORRECTNESS/HARD |
| `M*_GATE: PASS|FAIL`、`REGRESSION:` | Python harness | CORRECTNESS summary，保留 |
| captured C++ output 全量重印 | Python harness | DEBUG；正常成功可只留 gate，失败时全量打印 |

部分 M5 verifier 还匹配 `Shared+false dispatch succeeded`、`GrantShared` 等英文自由
文本。它们看似调试输出，实际属于 `HARD` oracle。应迁移为稳定 `[M5-GRANT]`
结构化 marker 后才能关闭旧文本。

### 12.16 `tests/ubcc/`、host tools 与 smoke binaries

`tests/ubcc/` 的结构检查脚本、`tools/*_test.cc`、`framework/tests/`、
`modules/networksim/main_test.cc` 和 `modules/ubiomodule/test_peer.cc` 统一使用：

- stdout：逐项 `PASS`、阶段名、最终 `passed`。
- stderr：`FAIL`、timeout、输入文件或 transport 错误。
- 进程退出码：最终正确性状态。

这些不在生产热路径，属于测试 UI。策略是保留失败明细和最终 summary；成功逐项
输出可置于 `--verbose`。使用标准 `assert` 的工具必须确认构建未定义 `NDEBUG`，
或改为显式检查后返回非零。

### 12.17 Python/shell 分析与 artifact 工具

| 输出类型 | 代表生产者 | 分类 | 规则 |
|---|---|---|---|
| stdout 纯 JSON | `summarize_*_matrix.py`、`evaluate_capacity_latency.py` | ARTIFACT/PERF | stdout 不得加入进度文本；错误发 stderr |
| JSONL 文件 | `analyze_2n1s_cc.py`、`summarize_2n1s_*.py` | ARTIFACT/PERF/CORRECTNESS | 保持一行一记录并增加 schema version |
| stdout 纯 HTML | `chain2html.py`、`trace_visualizer.py` | ARTIFACT | 不得混入状态行 |
| TSV matrix/progress JSON/result JSON | matrix runners | ARTIFACT/CORRECTNESS | schema、列数和 header 必须一致 |
| heartbeat/control log | P0/HA runners | LIVENESS | 推荐 JSONL，保留 timestamp、case、状态和 rc |
| 人类表格/求解报告 | `latency_compare.py`、`solve_latency_params.py` | PERF/operator | 增加 `--json`；约束失败发 stderr 并返回非零 |
| build/usage/completion | build scripts | OP/ERROR | 低频保留；不得静默吞掉非 optional 失败 |

### 12.18 CHI SLICC 与 generic gem5 输出

`CHI-cache-actions.sm`、`CHI-cache-funcs.sm`、`CHI-cache-ports.sm` 和
`AbstractController.cc` 的输出分两类：

- `DPRINTF(RubyGenerated|RubyCHIGeneric|RubySlicc, ...)`：逐 HNF request、snoop、
  TBE、queue stall 和 state dump，统一为 DEBUG，性能运行不得开启宽泛 flag。
- `assert`、`panic`、`fatal`：TxnId、mask、owner/sharer、TBE、data-valid、路由映射
  等协议不变量，统一为 FATAL，必须保留但不应依赖自由文本作为稳定 parser API。

### 12.19 未发现的输出机制

在项目自有 C/C++ 范围未发现 `std::cout`、`std::cerr`、`std::clog`、`perror`、
`warn_once`、`inform` 或 `DBPRINTF` 的实际使用。`snprintf/sprintf` 主要只构造内存
字符串，不直接形成输出，除非该字符串随后传给上述输出设施。

## 13. 输出相关缺陷与风险

以下不是“日志太多”的风格问题，而是审阅中发现的行为或证据风险。

| 严重度 | 位置 | 问题 | 后果/建议 |
|---|---|---|---|
| 高 | `tools/launcher.py` | networksim stdout/stderr 指向无人读取的 `PIPE` | pipe 满后死锁；改写文件或持续 drain |
| 高 | `run_tc135_perf_matrix.sh`、`run_tc90_*`、`run_tc90_default_sweep.sh` | TSV header 与数据列数/语义不一致 | parser 错位；固定 `event tc profile topology status ...` schema |
| 高 | `run_ha_formal_150_matrix.py` | deadline 后 `SKIP` 未计入失败，仍可退出 0 | 不完整矩阵被报告完成；SKIP/MISSING 必须非成功 |
| 高 | `q2_regression.sh` | `|| true` 后读取 pipeline status | 可能把失败记录为 `EXIT=0`；在覆盖前保存 rc |
| 高 | `run_multi.sh` 及多个 matrix runner | 以 verifier 日志最后一行精确匹配 PASS | 后续多一行即误判；同时检查 rc 和结构化 result artifact |
| 高 | fault verifier ingestion | `[UBFAULT-DELIVER]` 未导入 | Delay/Reorder delivery 无自动证据；加入白名单和精确检查 |
| 中 | `run_multi.sh` watchdog | 以 guest/protocol 日志字节增长当 progress | 关闭 debug 后可能误杀；改用固定低频 heartbeat |
| 中 | `test_marker_compliance.py` | consumed marker 清单少于实际 `verify.py` 消费集合；`--strict` 未把 warning 纳入 exit | governance 可漏报；从消费者生成 source of truth 并修 strict |
| 中 | `build_framework.sh`、`unpin_p0_containers.sh` | stderr 丢弃并 `|| true` | 失败静默；至少记录首次失败和最终状态 |
| 中 | `TracePerfPolicy.hh` | 默认 sample 而非 off；full 默认无界 | 正式性能矩阵显式 `off`，诊断 sample 必须 cap |
| 中 | `TracePerfPolicy.hh` | atomic counter 配合未加锁 `std::map` component 插入 | 多线程首次记录可能有数据竞争；预注册或加锁 |
| 中 | `framework::LogInfo` | 无 gate/rate limit，且调用方常自带换行 | 热路径高开销和双空行；统一 logger contract |
| 中 | `appendTmpLog()` | 每条 open/write/close，失败静默 | 性能扰动和证据丢失；默认关闭并集中管理 |
| 中 | TC47-49、TC117-119 早期 fault helper | 任意 `[UBFAULT]`，包括 loaded/malformed，均可算 evidence | fault 未 fired 也可能通过；统一使用 action record regex |
| 中 | TC9 | 依赖精确 gem5 page-fault 英文文本 | 上游文案变化即失败；增加结构化 expected-fault marker |
| 低 | `EPSNF-WB-FATAL` 文本 | 名称含 FATAL 但未必真正终止 | 改名 ERROR/TERMINAL 或执行确定性 fatal |

## 14. 机器接口清单

下列输出应视为 schema/API，而不是普通日志：

| 接口 | 通道 | 依赖 |
|---|---|---|
| `[READ_VAL]` | guest stdout | HARD/STRUCTURED |
| `[PHASE]`、`[SYNC]`、testcase 专用 oracle | guest stdout | HARD/STRUCTURED |
| `[GUEST-TIMER]`、`[PERF-LATENCY]` | guest stdout | HARD/STRUCTURED，仅相应性能 TC |
| HA JSONL | guest stdout/artifact | HARD/STRUCTURED |
| `STEP5 Port enabled` | gem5 stdout | HARD，进程握手 |
| `>>> TC<N> PASSED|FAILED <<<` | verifier stdout/log | HARD，且当前必须为最后一行 |
| fired `[UBFAULT]` | UBIO stderr | HARD/STRUCTURED，TC148-TC159 精确计数 |
| spill/fill/upgrade/writeback evidence | UBCC stdout/stderr | HARD，仅对应 qualification TC |
| `[RUNNER-MANIFEST]` | runner stderr | HARD；缺失会静默跳过 naive 检查 |
| `[UBCC-STATS]`、`[ResidentDirStats]` | UBIO stderr | STRUCTURED/STAT |
| `[EP-PERF]` | gem5 stderr | STRUCTURED/PERF，可按实验 gate |
| `[TRACE-PERF]` | component stderr | STRUCTURED/PERF，可关闭或采样 |
| matrix TSV/summary JSON/progress JSON | artifact 文件 | HARD/STRUCTURED |

所有这些接口都应增加 schema version，并逐步从自由文本 regex 迁移到 JSONL 或独立
result 文件。迁移期间不能只修改 producer。

## 15. 推荐运行模式

### 15.1 `performance`

保留：

- boot/config manifest。
- guest `[GUEST-TIMER]`、`[PERF-LATENCY]`。
- `[UBCC-STATS]`、`[ResidentDirStats]`。
- 低频 heartbeat。
- error/fatal。
- testcase 必需的最少 oracle。

关闭：

- `EP_TRACE_PERF`，默认 `off`。
- Port debug。
- UBCC/UBIO/gem5 EP 普通逐消息和逐事务输出。
- `appendTmpLog`。
- workload before/after 和高频 progress。

### 15.2 `correctness`

保留：

- 所有通用和 testcase oracle。
- 必要协议 evidence marker。
- error/fatal/invariant。
- 稀疏 phase/progress。

可选：

- bounded `TRACE-PERF sample`，仅用于失败定位。
- 定向协议 debug，但必须有最大记录数。

### 15.3 `fault-smoke`

保留：

- correctness oracle。
- `[UBFAULT]` loaded/fired。
- `[UBFAULT-DELIVER]`。
- 每 rule 精确 hit count 所需字段。
- error/fatal。
- 最少 recovery evidence。

关闭：

- 与 fault 判定无关的全量协议 debug。

### 15.4 `fault-qualification`

在 smoke 基础上保留：

- stable reqId/epoch 证据。
- retry deadline、attempt 和 pending mask 的结构化 evidence。
- commit exactly once 证据。
- final protocol drain summary。
- binary hash、rules manifest 和 per-rule result artifact。

仍不应开启所有逐消息 debug；qualification 需要精确证据，不需要无限日志。

### 15.5 `debug`

允许启用：

- Port per-message trace。
- gem5 DPRINTF。
- UBIO/UBCC 定点 trace。
- address filter。
- full protocol trace。

但必须同时设置：

- 最大记录数。
- 地址或 reqId filter。
- 时间窗或 testcase 范围。
- 日志目录上限。

## 16. 推荐统一开关

建议引入统一环境变量：

```text
CC_EP_LOG_MODE=performance|correctness|fault-smoke|fault-qualification|debug
```

可配细项：

```text
CC_EP_PROTOCOL_TRACE=off|sample|full
CC_EP_PROTOCOL_TRACE_MAX=<N>
CC_EP_RECOVERY_EVIDENCE=0|1
CC_EP_CORRECTNESS_EVIDENCE=0|1
CC_EP_VERBOSE=0|1
CC_EP_DEBUG_PA=<hex>
CC_EP_DEBUG_REQID=<id>
CC_EP_DEBUG_MAX=<N>
```

现有变量继续映射：

- `EP_TRACE_PERF`
- `EP_TRACE_PERF_MAX`
- `EP_DEBUG_PORT`
- `UBIO_DEBUG_PERF`
- `UBCC_DEBUG_CLEAR`

## 17. 推荐整改顺序

### P0：立即减少性能扰动

1. 将 UBIO 每消息 recv 输出默认关闭。
2. 将 `appendTmpLog` 默认关闭。
3. 将 EPSNF data beat 和 QLM retry 输出默认关闭。
4. 将无条件 `[EP-PERF]` 纳入 bounded policy。
5. 性能 runner 明确设置 `EP_TRACE_PERF=off`。
6. 新增独立低频 protocol heartbeat，解除 supervisor 对普通日志增长的依赖。

### P1：分离 oracle 与 debug

1. 为 spill/fill/upgrade/writeback qualification 增加结构化 evidence。
2. 修改 verifier，不再依赖全量 `UBCC-OUTER-REQ` 等 debug 文本。
3. 对 `[RESIDENT-*]`、recall、invalidate、upgrade 生命周期增加统一 gate。
4. 用终局 counter 替代逐 cache-line 输出。

### P2：统一日志框架

1. 给 `LogInfo` 增加模式和级别。
2. 统一 stdout/stderr 职责。
3. 所有高频 logger 支持 first-N、every-K、max-N。
4. 所有 artifact 输出写独立文件，禁止与普通日志混合。
5. 增加自动检查，防止性能模式出现未允许的高频前缀。

## 18. 变更输出时的验证规则

任何删除、改名或改格式前，必须执行：

1. 搜索 `tests/e2e/test_e2e.py`、`verify.py` 和 scripts 中对 marker 的依赖。
2. 搜索 runner 对最后一行、grep 文本、PID/exit 文件的依赖。
3. 确认 supervisor 不依赖该日志维持 progress。
4. 更新 verifier 和文档。
5. 运行相关 correctness/fault testcase。
6. 性能模式比较修改前后的日志量和 wall-clock，确认输出扰动下降。

特别禁止：

- 在 verifier sentinel 后追加输出。
- 删除 `STEP5 Port enabled` 而不修改 runner。
- 将 `[UBFAULT]` fired record 只改成总计。
- 删除 `[READ_VAL]` 或改变其字段格式。
- 关闭所有 `tick=` 输出而不提供新的 heartbeat。
- 在性能矩阵启用无界 full trace。

## 19. 审阅结论

当前代码具备完整的正确性、fault 和性能可观测性，但输出层次混杂：正式结果、test oracle、恢复证据和临时调试经常在同一热路径无条件输出。

性能测试真正需要的是：

- 配置 manifest。
- guest 聚合计时和延迟。
- 终局统计。
- 低频 liveness heartbeat。
- error/fatal。

正确性测试真正需要的是：

- `[READ_VAL]` 等 oracle。
- 指定 phase/testcase marker。
- verifier sentinel。
- invariant 和错误证据。

fault 测试真正需要的是：

- 每条 rule 的 loaded、fired 和 deliver 证据。
- stable tuple、retry 和 exactly-once 的结构化恢复证据。
- 数据收敛与最终状态 drain。

其余逐消息、逐 beat、逐 cache-line、逐 retry 的普通文本轨迹应归入 `DEBUG`，默认关闭，并通过有界采样、地址过滤和独立诊断运行启用。
