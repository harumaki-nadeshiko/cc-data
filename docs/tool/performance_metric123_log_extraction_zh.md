# 性能指标 1-3 日志提取与远端人工报告指南

## 1. 结论先行

三个指标不是从同一类日志、同一个实验或同一个脚本得到的。

| 指标 | 正式输入 | 主脚本 | 当前输出含义 |
|---|---|---|---|
| 指标 1 | TC131，8N1S，三 profile，至少三轮 | `compare_target12_from_logs.py` | 容量比与 guest-visible 压力后附加成本 |
| 指标 2 | TC135-TC140、TC217，三 profile，至少三轮 | `compare_target12_from_logs.py` | `naive >=500 ns` case 的百分比等权平均 |
| 指标 3 | 冻结的 HA/OurCC DAG、完成点、`K/tau/P`、权重；paired root samples 为最终 gate | `ha_vi_bitmap_baseline.py` 仅作敏感性计算 | 当前必须保持 `UNPROVEN`，不能由 target12 PASS 外推 |

推荐远端最短流程：

```text
人工填写 metric12_log_inventory.tsv
  -> build_metric12_manifest.py
  -> compare_target12_from_logs.py
  -> ha_vi_bitmap_baseline.py（可选，指标3敏感性）
  -> generate_metric123_report.py
```

上述四个脚本只依赖 Python 3 标准库。日志解析不依赖 gem5、Docker、第三方 Python 包或
固定日志目录名。正式构建和仿真仍必须按项目规则在 Docker 中执行。

当前正在执行的`16N1S TC142-TC147`属于现实数据库/FaaS/图/feature workload和扩展性补充
证据，不是原合同指标 1/2 的主矩阵。它不能替代指标 1 的 TC131，或指标 2 的
TC135-TC140/TC217；可在最终报告附录中单列 service、end-to-end、P99 和吞吐。

当前历史最终归档已经按正式口径复核对齐：

```text
72 matrix rows
72 verifier PASS
576 child exits，全部为0
指标1容量比 = 1.56640625
指标1 guest delta = -825.3134890562957 ns/op
指标2等权平均 = 54.12200730285039%
```

归档冻结了UBIO、networksim、framework、gem5.opt、runner、verifier和workload源码SHA-256。
尚缺的是直接记录的main/gem5 commit和Docker image digest；这是provenance身份缺口，不是性能
marker或公式未对齐。

## 2. 历史上数字是怎么得到的

### 2.1 第一代：2026-07 历史报告

历史报告：

```text
docs/measure/full_performance_metrics_report_20260729_zh.md
docs/delivery/performance_metrics_summary_20260731_zh.md
```

指标 1 使用 TC131 三 profile：

```text
naive
spill-noopt
optimized
```

容量来自 UBIO 日志：

```text
[UBCC-STATE] ... capacity=<N> policy=<naive|spill>
[UBCC-STATS] {"residentCapacity":<N>,...}
[UBCC-STATS] {"h64ExactLiveKnown":1,"h64ExactLiveCount":<N>}
```

旧容量公式：

```text
naive effective_unique = resident_capacity
spill effective_unique = max(resident_capacity, h64_exact_live)
capacity_ratio = spill-noopt / naive
```

旧报告得到：

```text
102656 / 65536 = 1.56640625，即提升 56.640625%
```

旧指标 1 时延来自 gem5 的内部协议日志：

```text
[EP-PERF] kind=outer ... latency_ps=<N>
```

使用 `scripts/evaluate_capacity_latency.py` 递归扫描三个日志目录，得到历史
`+6.03 ns / 12.06 cycles @ 2 GHz`。该值现在只应保留为历史协议诊断，因为脚本输出自身已
标记：

```text
outer_protocol_diagnostic_only=true
guest_visible_latency_status=not_measured
```

指标 2 的第一代矩阵来自：

```text
TC135-TC140 3N1S × naive/spill-noopt/optimized
TC217 HA10 2N1S × naive/spill-noopt/optimized
```

原始入口：

```text
logs/tc135_perf_matrix_20260728_all/summary_merged.json
logs/tc217_ha10_final_{naive,spill-noopt,optimized}/guest_summary.jsonl
scripts/summarize_tc135_perf_matrix.py
scripts/summarize_2n1s_guest.py
```

核心 marker 是 guest/target-visible：

```text
[PERF-LATENCY] ... phase=<phase> ... mean=<ticks> counter_frequency_hz=<Hz>
[GUEST-TIMER] ... operations=<N> counter_ticks=<ticks> counter_frequency_hz=<Hz>
```

每个 case 先计算：

```text
reduction_pct = (naive_mean_ns - optimized_mean_ns) / naive_mean_ns * 100
```

然后只选 naive mean 大于等于 500 ns 的 case 做等权平均。历史六个 case 为 TC135-TC139、
TC217，结果约 54.32%。TC138 的负结果必须保留。

### 2.2 第二代：m12b Timing/O3 配对矩阵

用途是比较同 commit 下 CPU 模型变化，不是最终三轮合同矩阵。

```text
/mnt/data2/cgc/cc-ep-v5-o3-perf-762aeee/logs/m12b/comparison.json
/mnt/data2/cgc/log-archives/2026-history-cleanup/m12b/
```

归档包括：

```text
timing-tc131.tar.zst
timing-tc135_140.tar.zst
o3-tc131.tar.zst
o3-tc135_140.tar.zst
```

该矩阵为 42/42 PASS，但只有单轮，指标 2 不含 TC217，指标 1 时延仍是 Outer diagnostic。
因此它适合回答“Timing 与 O3 是否敏感”，不应覆盖最终三轮结果。

### 2.3 第三代：target12 最终三轮矩阵

当前最强的指标 1/2 历史证据：

```text
/mnt/data2/cgc/log-archives/2026-history-cleanup/main-log-suites/
target12_final_perf_20260807.tar.zst
```

关键文件：

```text
target12_final_perf_20260807/matrix.tsv
target12_final_perf_20260807/summary.json
target12_final_perf_20260807/summary.md
target12_final_perf_20260807/freeze.sha256
target12_final_perf_20260807/runner_heartbeat.log
```

矩阵规模：

```text
指标1：TC131 × 3 profiles × 3 rounds = 9 runs
指标2：TC135-TC140、TC217 × 3 profiles × 3 rounds = 63 runs
总计：72/72 PASS
```

该版已将指标 1 成本更新为 guest-visible `post_pressure_catalog_reuse`，不再使用 Outer 作为
正式端到端成本。历史冻结结果约为：

```text
指标1容量比：1.56640625
指标1 guest delta：-825.313 ns/op
指标2等权平均：54.122007%
```

`scripts/summarize_target12_final_perf.py`依赖该固定目录结构；远端手工实验应优先使用不依赖
目录结构的`compare_target12_from_logs.py`。

## 3. 指标 1 的正式提取

### 3.1 实验矩阵

```text
TC131
topology = 8N1S
rounds >= 3
profiles = naive, spill-noopt, optimized
```

指标 1 正式比较只使用：

```text
naive vs spill-noopt
```

`optimized`仍必须运行，用于同矩阵 correctness 和配置完整性，但不用于隔离 spill 容量机制本身
的附加成本。

### 3.2 每个 run 最少保存

```text
node1 simout：包含 post_pressure_catalog_reuse GUEST-TIMER
node2 simout：包含 post_pressure_catalog_reuse GUEST-TIMER
Home UBIO stdout/stderr：包含 policy、resident capacity、H64 exact-live
verify_tc131.log
所有 child exit 状态文件
最终 argv、commit、二进制 SHA-256、timer frequency
```

旧的显式manifest解析脚本只需要前三类日志；后四类必须由人工或运行协调器做
correctness/provenance gate。新的`analyze_metric12_run_list.py`已经强制嵌入verifier和
child-exit门禁；commit、最终argv、二进制SHA和镜像digest仍需由运行协调器或外部metadata保存。

### 3.3 公式

```text
spill_effective_unique = max(resident_capacity, h64_exact_live)
capacity_ratio = spill_effective_unique / naive_resident_capacity
capacity PASS = capacity_ratio >= 1.5

profile_guest_ns_per_op = mean(node1_ns_per_op, node2_ns_per_op)
delta_ns_per_op = spill_noopt - naive
delta_cycles = delta_ns_per_op * contract_clock_hz / 1e9
latency PASS = delta_cycles < 50
```

禁止把 ResidentDir 数量和 Backstore 数量直接相加；`h64_exact_live`必须已经是去重后的精确
live union，Bloom positive 不计入 exact tracked line。

## 4. 指标 2 的正式提取

### 4.1 实验矩阵与 phase

| Case | Topology | Phase |
|---|---|---|
| TC135 | 3N1S | `preserved_sharer_first_load` |
| TC136 | 3N1S | `preserved_owner_store_complete` |
| TC137 | 3N1S | `new_requester_first_load` |
| TC138 | 3N1S | `dirty_owner_handoff_store` |
| TC139 | 3N1S | `mixed_batch_16ops` |
| TC140 | 3N1S | `cross_l2_owner_store` |
| TC217 | 2N1S | `ha10_catalog_batch_16ops` |

每个 case 必须运行三 profile、至少三轮。每条 manifest 记录列出的 simout 合计必须恰好有一条
目标 phase 的`PERF-LATENCY`，防止误取其他节点或 phase。

### 4.2 公式

```text
mean_ns = mean_ticks * 1e9 / counter_frequency_hz
applicable = naive_mean_ns >= 500
case_reduction_pct = (naive_mean_ns - optimized_mean_ns) / naive_mean_ns * 100
metric2 = mean(case_reduction_pct for applicable cases)
PASS = metric2 >= 10%
```

这是 case-level percentage 的等权平均，不是：

```text
sum(naive ns) 与 sum(optimized ns) 的比值
按 samples/operations 加权
只平均正收益 case
```

TC139 是 16-op batch，TC217 也是 batch/useful-op 场景。先在各自 case 内按共同边界得到百分比，
再等权平均，不能直接把它们的绝对 ns 与单操作 case 相加。

## 5. 指标 3 的正式提取

指标 3 不是从 target12 日志中“扒一个数”。正式命题是：

```text
OurCC 跨节点 CC 同步平均时延 < 甲方 HA 理论平均时延
```

理论模型：

```text
T_s(o,x) = K_s(o,x) * tau + P_s(o,x)
P = P_dir + P_peer + P_data + P_install + P_commit + P_queue
T_mean_s = sum(weight_i * T_s(i))
delta = T_mean_HA - T_mean_OurCC
```

正式比较必须冻结：

```text
HA合法协议分支与write policy
T_visible / T_commit / T_next / T_root_current
logical K与真实cross-node K
placement与双方P项
R_h/R_o/W_s/W_o/M/C权重或允许区间
共同root counter、paired run和correctness gate
```

`scripts/ha_vi_bitmap_baseline.py`可以输出窄化 VI bitmap/OurCC profile 的场景向量，也可以在
调用者提供 scenario counts 后计算敏感性平均。它明确输出：

```text
final_weights_frozen=false
```

所以该脚本输出不是合同 PASS。当前正式状态仍为：

```text
UNPROVEN（存在实质性 RISK）
```

以下数据只能作校准旁证：

```text
summarize_2n1s_guest.py 输出的 guest_summary.jsonl
summarize_2n1s_protocol.py 输出的 protocol_summary.jsonl
TC217/HA10 CC reference
EP-PERF Outer 日志
```

特别注意：HA10 名字中的 HA 不表示它是甲方 VI bitmap HA 实测；历史 TC217 日志是 CC reference。

## 6. 远端人工操作

### 6.0 直接使用三字段 run list

如果远端只能提供一个`list[dict]`，每个dict包含：

```text
simulator_log_dir
workload_output_dir
feature
```

可以直接使用：

```text
scripts/analyze_metric12_run_list.py
```

记录形状示例见：

```text
scripts/metric12_run_list.example.json
```

该文件只有两条记录片段，不可直接执行。正式输入必须包含完整72项矩阵。

每个run的目录职责：

```text
simulator_log_dir:
  Home UBIO stdout/stderr
  verify_tc*.log
  child_status*/**.exit

workload_output_dir:
  node0/simout_n0
  node1/simout_n1
  ...

feature:
  描述target、round、TC、topology和profile的字符串
```

默认feature格式是分号分隔的`key=value`：

```text
target=target1;round=1;case=TC131;topology=8n1s;profile=naive
target=target2;round=1;case=TC135;topology=3n1s;profile=optimized
```

执行：

```bash
python3 scripts/analyze_metric12_run_list.py \
  --input metric12_runs.json \
  --out-dir metric12_report \
  --min-rounds 3 \
  --hash-inputs
```

输出：

```text
metric12_report/performance_comparison.json
metric12_report/performance_comparison.md
metric12_report/resolved_runs.json
metric12_report/target2_samples.csv
```

该工具的主体是`Metric12RunListAnalyzer`类。远端若使用类似：

```text
PERF-B-TC132-3n1s-spill
```

的自定义标签，不要修改分析主体。继承该类并覆盖：

```python
def parse_feature(self, feature: str) -> RunFeature:
    ...
```

占位示例：

```text
scripts/metric12_custom_feature_parser.example.py
```

加载自定义类：

```bash
python3 scripts/analyze_metric12_run_list.py \
  --input metric12_runs.json \
  --analyzer scripts/metric12_custom_feature_parser.example.py:RemoteMetric12Analyzer \
  --out-dir metric12_report \
  --min-rounds 3 \
  --hash-inputs
```

正式门禁：

```text
指标1必须是TC131/8N1S
指标2的TC135-TC140必须是3N1S
指标2的TC217必须是2N1S
TC131必须精确取得node1/node2的目标GUEST-TIMER
指标2每个run的目标PERF-LATENCY必须全目录唯一
指标2 marker node和samples必须符合各TC正式合同
timer source必须是arm_cntvct_el0，unit必须是counter_ticks
必须有唯一verifier，且最后一个非空行是明确PASS sentinel
child status文件名必须精确覆盖当前TC全部gem5/UBIO/networksim，且值全0
三轮和三profile必须完整
指标2适用规则为naive mean >= 500ns
TC138等负结果不得删除
```

退出码：

```text
0: 证据有效且指标1/2均PASS
1: 证据有效但至少一个指标未达到门槛
2: 输入schema、feature、目录、marker、verifier或child-exit无效
```

### 6.1 准备四个脚本和一个模板

```text
scripts/build_metric12_manifest.py
scripts/compare_target12_from_logs.py
scripts/generate_metric123_report.py
scripts/ha_vi_bitmap_baseline.py
scripts/metric12_log_inventory.example.tsv
```

### 6.2 填写日志清单

复制示例：

```bash
cp scripts/metric12_log_inventory.example.tsv metric12_log_inventory.tsv
```

TSV 中多个日志路径使用分号分隔。正式清单应包括：

```text
TC131：3 rounds × 3 profiles = 9 rows
TC135-TC140、TC217：3 rounds × 7 cases × 3 profiles = 63 rows
总计 72 rows
```

建议另建`metadata.json`：

```json
{
  "main_commit": "replace-me",
  "gem5_commit": "replace-me",
  "cpu_model": "replace-me",
  "platform": "replace-me",
  "ubio_sha256": "replace-me",
  "gem5_sha256": "replace-me",
  "networksim_sha256": "replace-me"
}
```

### 6.3 TSV 转严格 manifest

```bash
python3 scripts/build_metric12_manifest.py \
  --inventory metric12_log_inventory.tsv \
  --base-dir /absolute/path/to/log-root \
  --metadata-json metadata.json \
  --output metric12_logs.json
```

### 6.4 从原始日志提取指标 1/2

```bash
python3 scripts/compare_target12_from_logs.py \
  --manifest metric12_logs.json \
  --out-dir metric12_report \
  --min-rounds 3 \
  --hash-inputs
```

输出：

```text
metric12_report/performance_comparison.json
metric12_report/performance_comparison.md
metric12_report/target2_samples.csv
```

退出码：`0`为指标1/2 PASS，`1`为门槛FAIL，`2`为输入或日志错误。

### 6.5 可选：生成指标 3 理论敏感性 JSON

没有正式 scenario counts 时，只生成场景向量：

```bash
python3 scripts/ha_vi_bitmap_baseline.py \
  --nodes 2 \
  --schemes shrink,broadcast \
  --protocols ha,ourcc_epoch,ourcc_no_stable_epoch \
  > metric3_model.json
```

若双方已经书面冻结 counts，可通过`--scenario-counts`传入。未冻结的示例 counts 不能写入
正式报告并判 PASS。

### 6.6 生成一屏摘要和正式 Markdown

```bash
python3 scripts/generate_metric123_report.py \
  --target12-json metric12_report/performance_comparison.json \
  --metric3-model-json metric3_model.json \
  --label remote-rounds-1-3 \
  --out-dir metric123_report
```

输出：

```text
metric123_report/metric123_compact.txt
metric123_report/metric123_key_values.tsv
metric123_report/metric123_report.json
metric123_report/metric123_report.md
```

其中：

```text
compact.txt：终端、邮件、工单的一屏结论
key_values.tsv：人工表格粘贴
report.json：后续自动化输入
report.md：评审报告
```

`generate_metric123_report.py`也可直接读取历史
`summarize_target12_final_perf.py`产生的`summary.json`。它永远不会把指标1/2 PASS合并成三指标
合同 PASS；指标3仍输出`UNPROVEN`。

若输入是`compare_target12_from_logs.py`产生的`performance_comparison.json`，其中没有嵌入
verifier和child-exit总账，最终报告会显示：

```text
correctness_gate=NOT_EMBEDDED_CHECK_VERIFIER_AND_CHILD_EXITS
```

若输入是带`all_cases_pass`的历史最终`summary.json`，报告会显示`PASS`或`FAIL`。无论哪种
输入，人工都应保留原始verifier与child status作为可审计证据。

## 7. 人工最终复核清单

脚本完成后仍需逐项确认：

```text
[ ] matrix中计划运行数全部存在，无MISSING/SKIP/PENDING
[ ] 所有纳入run的verifier rc=0且有明确PASS sentinel
[ ] 所有gem5、UBIO、networksim child exit=0
[ ] 三profile实际flags与名称一致
[ ] 所有round使用同一commit、submodule、二进制和timer配置
[ ] TC131 naive没有Backstore/Spill marker
[ ] TC131 spill有validated H64 exact-live
[ ] 指标2适用集合跨round稳定
[ ] TC138等负结果未删除
[ ] Outer diagnostic未替代guest-visible指标
[ ] 指标3没有被target12 overall_pass误判为PASS
```

## 8. 旧脚本的正确定位

| 脚本 | 继续使用场景 | 不应承担的职责 |
|---|---|---|
| `evaluate_capacity_latency.py` | 历史 TC131 Outer 诊断复算 | 当前正式 guest-visible 指标1/2 |
| `summarize_tc135_perf_matrix.py` | 旧固定目录单轮排查 | 三轮严格门禁和最终等权平均 |
| `summarize_p0_512k_round.py` | 矩阵运行通知摘要 | 正式 evaluator |
| `latency_compare.py` | TRACE 请求链归因 | guest-visible root latency 验收 |
| `summarize_target12_final_perf.py` | 固定 target12 目录历史复算 | 远端任意目录手工日志 |
| `compare_target12_from_logs.py` | 指标1/2正式低依赖提取 | 指标3理论证明、verifier/child-exit执行 |
| `ha_vi_bitmap_baseline.py` | 指标3场景向量与权重敏感性 | 未冻结输入下的合同PASS |
| `generate_metric123_report.py` | 压缩结构化结果并出报告 | 从错误或不完整实验中创造证据 |

## 9. 推荐归档目录

```text
evidence-<date>/
  metadata.json
  metric12_log_inventory.tsv
  metric12_logs.json
  raw-logs/或raw-logs.tar.zst
  verifier/
  child-status/
  metric12_report/
  metric3_model.json
  metric123_report/
  freeze.sha256
```

报告中引用文件时同时记录 SHA-256。不要只保存 Markdown；至少保留原始日志、manifest、
机器可读 JSON、verifier 和 child status。
