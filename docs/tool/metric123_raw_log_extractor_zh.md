# Metric1/2/3 远端原始日志统一提取工具

## 用途

`scripts/extract_metric123_from_logs.py`直接读取远端保存的原始 simulator 日志和每节点
simout，不要求预先存在`result.json`。输入必须是显式 JSON manifest：每个物理 run 单独列出
`simulator_log_dir`与`simout_dir`，路径相对于 manifest 所在目录解析。

模板与结构约束：

- `scripts/metric123_raw_manifest.example.json`
- `scripts/metric123_raw_manifest.schema.json`

## 目录与压缩格式

工具递归发现普通文件和`.gz`，支持：

```text
node0/simout_n0
node1/simout_n1.gz
simout_tc228_node0.log
simout_tc228_node1.log.gz
simout_n0
```

同一 run、同一 node 发现两个候选会报`duplicate simout`，不会猜测优先级。

## correctness policy

```text
strict   verifier 和 child exits 必须存在；PASS；exit 全 0；文件名精确符合 topology
required 远端正式证据必须存在；当前执行与 strict 使用相同的精确身份门禁
optional 两类证据都不存在时允许；任一证据存在后仍执行精确身份、PASS、全 0 门禁
```

远端日志完整时建议`strict`；旧归档确实没有 correctness 文件时才使用`optional`。策略只放宽
证据是否必须存在，不会把已有 FAIL 或非零 exit 当成成功。

## 三项提取口径

- Metric1：TC131/8N1S 是冻结正式合同；simulator 日志中的`UBCC-STATE/UBCC-STATS`容量、policy、exact-live；
  simout 中 node1/node2 各唯一一条`post_pressure_catalog_reuse` GUEST-TIMER。
  容量只从Home UBIO目录提取，默认`n0/s0`，兼容`ubio_tc131_n0_s0`与`ubio_n0_s0`；
  非默认Home可在run中填写`home_node/home_socket`。Home 发现依次使用标准目录名、任意布局中的
  `[PROCESS-MANIFEST]`身份、容量 marker 回退；单一回退或多个相同来源产生 WARNING，来源值冲突才拒绝。
  非 TC131/8N1S 数据也可通过`phase/timer_nodes/home_node/home_socket`解析为 extension。
- Metric2：TC135-140/217 的正式 phase、node、samples 合同；目标 PERF-LATENCY 必须全 run 唯一。
  未注册 TC 需提供`phase`，可选`expected_node/expected_samples`（省略时由唯一 marker 确定）；正式 TC
  的错误 topology 或覆盖合同字段仍可解析，但只进入 extension 描述视图。
  每个 repetition 的 applicable case 等权均值都必须达到 10%，且 applicable case 集合跨 repetition 稳定。
  少于完整 TC135-140/217 集合时仍输出已提取值，但 Metric2 总状态为 INCOMPLETE。
- Metric3：直接解析 TC228-235 的 GUEST-TIMER/PERF-LATENCY；这些 TC 即使 topology 非 2n1s 也可
  解析为 extension。未知 TC 必须提供`metric_specs`，每项含`kind=timer|latency`、`phase`和
  `reduction=aggregate|max`，否则报`PARSER_SPEC_REQUIRED`。按 manifest 的`pair/tc/order`
  严格配`ourcc`和`ha-vi`，绝不笛卡尔配对。工具在`output/evidence/metric3`生成标准 arm
  `result.json`证据树，并使用与`analyze_metric3_paired.py`一致的冻结 primary/aggregate 权重。
  PERF-LATENCY 多节点聚合按 samples 对 mean 加权；同一 pair/TC 的 order 冲突直接判 INVALID。

Metric3 定义`delta = HA-VI - OurCC`，严格`delta > 0`才是可执行参考模型范围 PASS。该状态
不是物理甲方硅片测量声明。重复只作描述性汇总；工具不计算 t-test、置信区间或 p-value。

## 执行

日志提取本身仅需 Python 标准库：

```bash
python3 scripts/extract_metric123_from_logs.py \
  --manifest /path/to/metric123-manifest.json \
  --output-dir /path/to/output
```

项目中的测试必须按 Docker-only 规则运行：

```bash
docker run --rm --network none --cpuset-cpus=0-31 \
  -v "$PWD:/workspace" -w /workspace ubcc-dev:ubuntu20.04 \
  python3 -m unittest tests.scripts.test_extract_metric123_from_logs
```

## Python 增量 API

需要边获得 run、边释放远端日志空间时，可使用`Metric123RawLogMatrix`。`add()`接受一个
run 字典或与 manifest run schema 同名的关键字参数，并在返回前完成 simout、simulator
日志及 correctness 证据的全部解析：

```python
from scripts.extract_metric123_from_logs import Metric123RawLogMatrix

matrix = Metric123RawLogMatrix(
    requirements=None,              # 从所有 add 尝试推断覆盖需求
    correctness_policy="strict",
    base_dir="/archive/runs",
)
status = matrix.add(
    id="tc135-r1-naive", metric=2, tc=135, repetition="r1",
    topology="3n1s", profile="naive",
    simulator_log_dir="tc135/r1/naive/simulator",
    simout_dir="tc135/r1/naive/simout",
)
assert status["status"] in ("ADDED", "REJECTED")

# add 返回后即可删除或迁移上述输入目录；finalize 不会重新 open/stat 输入文件。
result = matrix.finalize("/tmp/metric123-report")
report = result["report"]
```

省略`requirements`时，只要 metric/repetition/TC/profile 或 Metric3 pair/order/arm 等身份字段
可解析，即使该次 add 因日志或 correctness 无效而`REJECTED`，其身份仍进入预期覆盖，避免
坏日志从缺失矩阵中消失。显式传入 requirements 时继续使用 manifest 的原有 schema。
`id`可省略，工具按 add 顺序稳定生成`run-000001`。重复请求 ID 自动改为`id-2/id-3`并产生
`DUPLICATE_RUN_ID_RENAMED` WARNING，不影响 ADDED；第二个及后续重复逻辑 slot 仍会在`add()`时立即返回
`REJECTED/DUPLICATE_SLOT`，所有有效声明者都从最终计算中排除，并使报告为`INVALID`。
一次失败的 add 不阻止后续 add。`finalize()`可重复调用，结果确定；也允许在
finalize 后继续 add，下一次 finalize 自动包含新尝试。未提供 output_dir 时不写文件，返回
含`report`、`resolved_runs`、`matrix`、`matrices`、`per_run_metrics`、`issues`和`exit_code`的字典。
`matrix`兼容别名始终指正式 standard matrix；`matrices`同时提供`standard/all/extension`，报告中的
`views.all`和`views.extension`含按 metric/TC/topology/repetition/profile(或 arm)的描述统计与可形成的比较。
extension 不改变正式状态。每个成功 run 含`contract_class`、`standard_contract`和结构化
`contract_warnings`。

Metric1 的 Home UBIO 不再要求固定目录名。发现顺序为：标准`ubio_tcN_nN_sS`目录、日志内
`PROCESS-MANIFEST`身份、最后是唯一或数值一致的容量 marker 来源。回退会产生
`HOME_UBIO_FALLBACK`或`HOME_UBIO_IDENTICAL_MULTIPLE` WARNING；只有多个来源的容量、policy
或 effective unique 互相矛盾时才拒绝。平铺保存的 manifest 与容量日志可以分开存在。
如果远端归档已丢失目录名和`PROCESS-MANIFEST`，可显式传入`home_ubio_log_dir`或
`home_ubio_logs=[...]`；该选择优先于自动发现并产生`HOME_UBIO_EXPLICIT` WARNING。

标准和扩展实验的逻辑 slot 包含 topology 与解析定义，因此同一 TC/profile 的 8N1S 标准点和
4N1S 扩展点可以同时存在。只有完全相同实验坐标的第二个数据点才是`DUPLICATE_SLOT`。
扩展点解析失败会保留`contract_class=extension`错误供审计，但不会把已完整通过的 standard
正式视图降为 INVALID；标准数据自身错误仍按原规则判 INVALID。

两个矩阵可用`merged = left + right`纯内存合并。requirements 做确定性并集，correctness policy 取
`strict > required > optional`中更严格者；ID 冲突自动改名，逻辑 slot 冲突仍无效。合并不修改操作数，
也不会访问源路径，因此原始目录已删除后仍可`merged.finalize()`；`m + m`会去重完全相同的 run snapshot。

## 正式结果还原验证

增量 API 已在 Docker 中直接重放当前保留的正式 raw logs，而不是读取既有分析结果作为输入：

- Metric 1/2：`results/metric12-final-v1/cases`中的 72 个 raw-log run；
- Metric 3 p100：`results/metric3-l3-only-v4/cases`中的 80 个 raw-log arm；
- Metric 3 p150：同目录另外 80 个 raw-log arm。

重放结果与`docs/design/cc_ep_deliverable3_performance_api.md`及其机器可读来源逐字段一致：

```text
Metric 1 capacity ratio       1.5150909423828125
Metric 1 delta cycles       -1635.994218910734
Metric 2 equal-weight          64.75927627819759%

Metric 3 p100 core              7.904166666666667 ticks
Metric 3 p100 representative    2.8820833333333336 ticks
Metric 3 p150 core              7.939583333333333 ticks
Metric 3 p150 representative    2.8785156250000004 ticks
```

此外还逐轮核对 Metric 1，逐轮逐 TC 核对 Metric 2 的三个 profile 均值与降幅，并逐 TC
核对 Metric 3 的 TC228-TC235 主值；浮点比较容差为`1e-9`。

## 输出与退出码

```text
output/report.json           完整机器可读报告
output/report.md             中文摘要和人读矩阵
output/metric_matrix.tsv     run/pair/TC/aggregate 多层矩阵
output/metric_matrix_standard.tsv  正式冻结合同矩阵；与 metric_matrix.tsv 相同
output/metric_matrix_all.tsv       全部成功解析的数据点与描述结果
output/metric_matrix_extension.tsv 非标准 TC/topology/phase 扩展数据点
output/issues.tsv            明确问题清单
output/resolved_runs.json    解析后的绝对路径、来源 marker 与 correctness
output/per-run_metrics.tsv   每个 run 的扁平摘要
output/evidence/metric3/     合成的标准 arm evidence tree
```

退出码：`0`完整且通过，`1`完整但指标失败，`2`manifest/日志/重复证据无效，`3`需求矩阵或
Metric3 pair 不完整。缺失 arm 不会与其他 pair 的 arm 组合。
