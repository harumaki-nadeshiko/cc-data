# 目标1/2远端日志性能对比工具

## 1. 用途

`scripts/compare_target12_from_logs.py`用于在目标平台手工启动testcase后，从显式指定的日志文件生成目标1和目标2性能指标。

工具不依赖：

- `run_multi.sh`。
- 固定日志目录结构。
- `verify_tc*.log`。
- Docker、gem5输出目录名称或case目录名称。
- 项目中的其他Python模块。

工具只依赖Python 3标准库。所有输入日志必须在JSON manifest中逐个列出，不使用自动目录扫描或glob。

## 2. 输入日志要求

### 2.1 目标1

每个round的每个profile需要：

| 字段 | 内容 |
|---|---|
| `simout_logs` | 包含`post_pressure_catalog_reuse`的`[GUEST-TIMER]`日志。TC131通常列出node1和node2的simout。 |
| `ubio_logs` | 包含`[UBCC-STATE]`、resident capacity以及最终`h64ExactLiveKnown/h64ExactLiveCount`的UBIO日志。可以是stdout、stderr或手工合并日志。 |

目标1profile固定为：

```text
naive
spill-noopt
optimized
```

工具计算：

```text
capacity_ratio = spill-noopt effective_unique / naive effective_unique

guest_delta_ns/op = spill-noopt guest mean - naive guest mean

guest_delta_cycles = guest_delta_ns/op * contract_clock_hz / 1e9
```

其中spill的`effective_unique`使用：

```text
max(resident_capacity, h64_exact_live)
```

不会把ResidentDir与H64重复项直接相加。

### 2.2 目标2

每个round、每个case、每个profile需要：

| 字段 | 内容 |
|---|---|
| `phase` | 要比较的`[PERF-LATENCY] phase=...`。 |
| `simout_logs` | 包含该phase性能记录的一个或多个simout文件。 |

对于每条目标2记录，工具要求所有列出的文件合计恰好出现一个匹配的`PERF-LATENCY`记录。这样可以避免误把其他phase或其他CPU的计时混入结果。

当前推荐case/phase：

| Case | Phase |
|---|---|
| TC135 | `preserved_sharer_first_load` |
| TC136 | `preserved_owner_store_complete` |
| TC137 | `new_requester_first_load` |
| TC138 | `dirty_owner_handoff_store` |
| TC139 | `mixed_batch_16ops` |
| TC140 | `cross_l2_owner_store` |
| TC217 | `ha10_catalog_batch_16ops` |

工具按以下规则冻结适用集合：

```text
naive guest-visible mean >= 500 ns
```

目标2总指标是适用case的优化百分比等权平均：

```text
case_reduction = (naive_mean - optimized_mean) / naive_mean * 100%

target2_reduction = mean(case_reduction for applicable cases)
```

负结果会正常保留并进入平均，不会被过滤。

## 3. Manifest格式

字段格式示例位于：

```text
scripts/target12_manual_logs_manifest.example.json
```

示例为了保持可读性，只展示round 1的TC131三profile和TC135三profile。正式运行时必须按同一格式补齐至少3轮，以及计划纳入的全部目标2 case。脚本不会根据目录自动补全缺失记录。

顶层字段：

| 字段 | 必需 | 说明 |
|---|---|---|
| `schema_version` | 是 | 固定为`1`。 |
| `base_dir` | 否 | 输入日志相对路径的根目录。相对manifest所在目录解析，默认`.`。 |
| `thresholds` | 否 | 验收阈值；缺省值见模板。 |
| `target1_runs` | 是 | 目标1扁平记录数组。 |
| `target2_runs` | 是 | 目标2扁平记录数组。 |

每个round必须完整包含三个profile。profile也接受以下别名：

```text
spill_opt   -> optimized
spill-opt   -> optimized
spill_noopt -> spill-noopt
```

建议保留标准名称，避免人工歧义。

路径可使用：

```text
相对路径
/绝对路径
普通文本日志
gzip压缩的.log.gz日志
```

`required_markers`是可选字段。提供后，工具会在该条记录列出的日志中检查所有marker。例如：

```json
"required_markers": [
  "phase=catalog_reuse",
  "phase=exclusive_upgrade"
]
```

## 4. 远端使用步骤

### 4.1 部署

目标机只需以下文件：

```text
compare_target12_from_logs.py
target12_manual_logs_manifest.example.json
```

确认Python版本：

```bash
python3 --version
```

建议Python 3.8或更高版本。

### 4.2 编辑manifest

复制模板并逐条替换路径：

```bash
cp target12_manual_logs_manifest.example.json target12_logs.json
```

手工填写所有round/profile/case的日志。正式验收至少需要3个完整round。

检查JSON语法：

```bash
python3 -m json.tool target12_logs.json >/dev/null
```

### 4.3 生成结果

```bash
python3 compare_target12_from_logs.py \
  --manifest target12_logs.json \
  --out-dir target12_report \
  --min-rounds 3 \
  --hash-inputs
```

`--hash-inputs`会为每个输入日志计算SHA-256。日志很大且只需要快速检查时可以省略。

退出码：

| 退出码 | 含义 |
|---:|---|
| `0` | 目标1和目标2均PASS。 |
| `1` | 输入完整且成功计算，但至少一个性能门槛FAIL。 |
| `2` | manifest或日志输入错误，例如缺文件、缺marker、轮数不足或计时记录数量不符。 |

## 5. 输出文件

输出目录包含：

| 文件 | 内容 |
|---|---|
| `performance_comparison.json` | 完整机器可读结果、逐round原始值、统计值、输入文件元数据和门槛。 |
| `performance_comparison.md` | 适合评审的摘要和逐case对比表。 |
| `target2_samples.csv` | 每个round/case/profile的mean、P50、P95、P99、max。 |

命令行也会输出简短状态：

```json
{
  "overall_pass": true,
  "target1_pass": true,
  "target2_pass": true,
  "out_dir": "/path/to/target12_report"
}
```

## 6. 输入校验

工具会主动拒绝以下情况：

- 文件不存在或路径指向目录。
- 同一条记录重复列出同一文件。
- 同一round/case/profile出现重复记录。
- round ID不连续。
- 目标1或目标2少于`--min-rounds`。
- 任一round缺少naive、spill-noopt或optimized。
- UBIO日志中没有resident capacity。
- spill日志中没有validated H64 exact-live计数。
- manifest profile与UBIO实际policy冲突。
- 同一目标1计时phase的counter frequency不一致。
- 目标2指定phase出现0条或多于1条`PERF-LATENCY`。
- 三轮计算出的目标2适用集合不一致。

## 7. 手工启动时的日志保存建议

每次运行建议至少保存：

```text
TC131：node1/node2 simout + home node UBIO完整输出
TC135-TC140：包含PERF-LATENCY的simout
TC217：包含ha10_catalog_batch_16ops的simout
```

不要只保存终端摘要。目标1需要最终H64 exact-live统计，目标2需要完整`PERF-LATENCY`字段。

建议每次手工运行同时记录：

```text
round ID
profile
testcase ID
完整启动参数
ubio/gem5/networksim二进制SHA-256
workload SHA-256
目标平台CPU频率或合同换算频率
```

这些额外信息可以放在manifest的自定义`metadata`字段中。工具会忽略未知字段，但原始manifest应与报告一起归档。

## 8. 目标3

该工具不计算目标3，也不要求客户HA实测输入。输出中目标3固定标记为：

```text
THEORETICAL_ANALYSIS_ONLY
```

目标3的理论分析应单独归档，不与目标1/2实测gate混合。
