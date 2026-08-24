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

- Metric1：TC131/8N1S；simulator 日志中的`UBCC-STATE/UBCC-STATS`容量、policy、exact-live；
  simout 中 node1/node2 各唯一一条`post_pressure_catalog_reuse` GUEST-TIMER。
  容量只从Home UBIO目录提取，默认`n0/s0`，兼容`ubio_tc131_n0_s0`与`ubio_n0_s0`；
  非默认Home可在run中填写`home_node/home_socket`。
- Metric2：TC135-140/217 的正式 phase、node、samples 合同；目标 PERF-LATENCY 必须全 run 唯一。
  每个 repetition 的 applicable case 等权均值都必须达到 10%，且 applicable case 集合跨 repetition 稳定。
  少于完整 TC135-140/217 集合时仍输出已提取值，但 Metric2 总状态为 INCOMPLETE。
- Metric3：直接解析 TC228-235 的 GUEST-TIMER/PERF-LATENCY；按 manifest 的`pair/tc/order`
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

## 输出与退出码

```text
output/report.json           完整机器可读报告
output/report.md             中文摘要和人读矩阵
output/metric_matrix.tsv     run/pair/TC/aggregate 多层矩阵
output/issues.tsv            明确问题清单
output/resolved_runs.json    解析后的绝对路径、来源 marker 与 correctness
output/per-run_metrics.tsv   每个 run 的扁平摘要
output/evidence/metric3/     合成的标准 arm evidence tree
```

退出码：`0`完整且通过，`1`完整但指标失败，`2`manifest/日志/重复证据无效，`3`需求矩阵或
Metric3 pair 不完整。缺失 arm 不会与其他 pair 的 arm 组合。
