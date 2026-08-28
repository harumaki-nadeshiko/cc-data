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

- Metric1：TC131/8N1S 正式合同改为每轮三个显式角色：`naive`（512KiB naive，只提供容量分母）、
  `spill`（512KiB spill-noopt，提供容量分子和真实 Outer 延迟）、`ideal`（spill-noopt、实验性超大
  ResidentDir，无容量 eviction/offload，提供 Outer 反事实基线）。`metric1_role`别名包括
  `baseline -> naive`、`spill-512k/actual -> spill`、`ideal-dir/infinite -> ideal`。省略时 profile=naive
  自动为 naive；Home UBIO `PROCESS-MANIFEST experimental_oversized_resident_dir=1`自动为 ideal；其余
  其余spill-noopt自动为spill；optimized自动为support extension，并产生`METRIC1_ROLE_AUTO_DETECTED`。
  为兼容旧 run-list/远端调用，Metric1 的`profile=ideal|ideal-dir|infinite`会被显式归一为
  `profile=spill-noopt + metric1_role=ideal`，并产生`LEGACY_METRIC1_PROFILE_NORMALIZED`。该兼容只修正
  字段词汇，不绕过 oversized ResidentDir、capacity、fill、exact-live 或 Outer sample 标准门禁。
  正式容量比为`spill effective_unique / naive effective_unique`；正式延迟附加为
  `mean(all completed EP-PERF kind=outer in spill) - mean(all completed EP-PERF kind=outer in ideal)`，
  `cycles = ns * 2GHz`。每轮同时满足 ratio>=1.5 且 delta cycles<50 才 PASS，全部轮次都须 PASS。
  跨轮报告 ratio/delta 的等权 mean、stdev、CV，不按 Outer sample 数给轮次加权。
  simulator 日志递归读取`.log/.gz`；优先仅用`gem5_tc*_node*/stderr.log(.gz)`，不存在时才确定性回退
  全部日志，并去除 stdout/stderr 中完全相同的复制行；保留 source files、samples、mean、p50/p95/p99/max。
  spill/ideal 标准角色至少须有一条 completed Outer。
  旧 node1/node2 `post_pressure_catalog_reuse` GUEST-TIMER 已弃用为描述字段，不参与完整性或 PASS。
  完整时继续输出旧 guest 值；缺失或部分存在仍 ADDED，产生`METRIC1_GUEST_TIMER_MISSING`，描述字段为 null。
  GUEST-TIMER 字段顺序不影响解析；warning 会列出已发现 simout 数、marker 总数、可见 phase 和最多三条
  malformed marker，便于区分“文件未发现”“phase 不匹配”和“字段不完整”。
  容量只从Home UBIO目录提取，默认`n0/s0`，兼容`ubio_tc131_n0_s0`与`ubio_n0_s0`；
  非默认Home可在run中填写`home_node/home_socket`。Home 发现依次使用标准目录名、任意布局中的
  `[PROCESS-MANIFEST]`身份、容量 marker 回退；单一回退或多个相同来源产生 WARNING，来源值冲突才拒绝。
  `parse_capacity`还保留 manifest oversized flag、resident capacity、Home 日志中
  `RESIDENT-FILL-DONE found=1`计数和最大 H64 exact-live。ideal 标准门禁为：TC131、8N1S、
  spill-noopt、policy=spill、oversized=1、resident capacity>=`requirements.metric1.ideal_min_capacity`
  （默认102656）、found fills=0、H64 exact-live=0。spill 要求 spill-noopt/policy=spill/oversized=0；
  naive 要求 naive/policy=naive/oversized=0。门禁失败只进入 extension，正式角色槽保持缺失，绝不静默使用。
  `requirements.metric1`支持`repetitions`、默认`roles=[naive,spill,ideal]`及`ideal_min_capacity`。
  旧 manifest 没有 ideal 时可正常解析（timer 缺失也不会拒绝），但标准 Metric1 为 INCOMPLETE。
  下游`scripts/generate_metric123_report.py`必须通过`--metric1-json`读取本提取器的`report.json`或
  `run_metric1_outer_ideal_matrix.py`的`summary.json`。旧Metric1/2 JSON中的`guest_delta_*`只保留历史
  描述用途；未提供corrected Outer/IdealDir结果时，统一报告中的Metric1固定为`INCOMPLETE`，不得再据此PASS。
  裸进程启动不强制传testcase：gem5的`test_e2e.py --tc=N`与UBIO的`ubio --tc=N`都是可选身份提示。
  提取时以raw manifest中每个run的`tc`为权威身份；进程`PROCESS-MANIFEST.tc`缺失或为0时允许，非零时
  只做一致性交叉校验，冲突才拒绝。UBIO的`--tc`不选择overflow policy、ResidentDir容量、协议优化或
  fault规则，这些功能配置仍必须由各自argv给出。

### Metric1 裸进程 argv 合同

正式矩阵固定为`TC131 / 8N1S / O3 / 3 repetitions x 3 roles`。外部launcher负责把每个物理运行
登记到raw manifest的`tc=131`和对应`metric1_role`；gem5与UBIO进程argv中的`--tc=131`均可省略。
Port/IPC/PDES参数不属于Metric1提取合同。

每个gem5节点的共同配置脚本参数为：

```text
test_e2e.py
--node-id=<0..7> --num-nodes=8 --num-sockets=1
--workload=<TC131 workload.elf>
--cpu-model=o3 --sequencer-max-outstanding=16
--l3-size=256kB --l3-assoc=16
--ha-profile=ubcc --clear-profile=ack
--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0
--ubcc-metadata-size=134217728
```

每个UBIO使用`--node=<0..7> --socket=0 --num-nodes=8 --num-sockets=1`，角色差异为：

```text
naive:
  --bloom-bytes=0 --sram-bytes=524288 --ways=0 --set-bits=0
  --dir-overflow-policy=naive --batch-rs=0
  --metadata-dram-bytes=134217728

spill:
  --bloom-bytes=61440 --sram-bytes=524288 --ways=0 --set-bits=0
  --dir-overflow-policy=spill --batch-rs=0
  --metadata-dram-bytes=134217728

ideal:
  --bloom-bytes=61440 --sram-bytes=2097152 --ways=32 --set-bits=0
  --dir-overflow-policy=spill --batch-rs=0
  --allow-oversized-resident-dir-for-test
  --metadata-dram-bytes=134217728
```

推荐的非正式extension最小矩阵同样使用`naive/spill/ideal`三角色，每点先做1轮资格运行：

```text
TC132 / 3N1S / dirty checkpoint recovery / 73728 unique lines
TC133 / 8N1S / shared frontier reuse      / 69632 unique lines
TC142 / 3N1S / portable OLTP p150         / 98304 unique lines
```

TC132/TC133直接复用现有workload。TC142必须在编译workload时使用p150定义，不把这些定义传给
gem5或UBIO：`PORTABLE_PRESSURE_LINES=98208`、`PORTABLE_TARGET_FOOTPRINT_LINES=98304`、
`PORTABLE_NAIVE_CAPACITY_LINES=65536`、`PORTABLE_PRESSURE_LEVEL_PCT=150`、`PORTABLE_BATCHES=32`。
三类extension沿用与正式矩阵相同的gem5 O3/L3/优化关闭参数和三角色UBIO目录参数，只替换
`--workload`、`--num-nodes`及每节点进程数。extension必须单独报告Outer samples和mean，不与TC131
正式三轮聚合。TC125-130、TC200-203等要求实际spill/fill或工作集远小于512KiB容量的机制测试不纳入。
- Metric2：TC135-140/217 的冻结正式 phase、node、samples 合同仍要求正式 phase 唯一 marker，并只用它
  计算 standard 结果。工具同时扫描全部 PERF-LATENCY。未注册 TC 省略`phase`时，只有一个 phase 会自动
  选择并产生`METRIC2_PHASE_AUTO_DETECTED`；存在多个 phase 时不混合均值，而是全部保存在
  `metrics.latency_phases`，产生`METRIC2_MULTIPLE_PHASES`并按 phase 输出描述矩阵。显式`phase`聚合所有
  同名记录；频率必须一致，mean 按 samples 加权，保留 nodes、records、total samples。正式 run 的额外
  phase 只作 extension 描述和 WARNING，不改变冻结正式值。
  每个 repetition 的 applicable case 等权均值都必须达到 10%，且 applicable case 集合跨 repetition 稳定。
  少于完整 TC135-140/217 集合时仍输出已提取值，但 Metric2 总状态为 INCOMPLETE。
- Metric3：直接解析 TC228-235 的 GUEST-TIMER/PERF-LATENCY；这些 TC 即使 topology 非 2n1s 也可
  解析为 extension。未知 TC 必须提供`metric_specs`，每项含`kind=timer|latency`、`phase`和
  `reduction=aggregate|max`，否则报`PARSER_SPEC_REQUIRED`。默认是 independent：每个 run 以
  `repetition/tc/topology/arm/metric spec names`为独立身份，不要求 pair/order，不构造配对或 ABBA；
  每个 TC/metric/arm 对全部 independent run 计算 mean、stdev、count，delta 为两个 arm mean 之差。
  `requirements.metric3`支持`mode=independent`（默认）、`repetitions`或正整数`min_repetitions`、
  `testcases`和默认双 arm 的`arms`。显式 repetitions 要求每个 TC/arm/repetition 恰有一个有效 run；
  arm 数量不平衡时仍输出描述比较，但正式状态为 INCOMPLETE。完整正式 PASS 仍要求 TC228-235 和双 arm。

  `arm`可省略；工具递归读取 simulator 日志中的`EPBACKEND-PROFILE ha_endpoint_profile=ubcc|ha-vi`、
  UBIO `PROCESS-MANIFEST home_controller=ubcc|ha-vi`或`UBIO-HA-MANIFEST controller=ha-vi`。证据一致时
  自动归一为`ourcc`/`ha-vi`并产生`ARM_AUTO_DETECTED`；无证据或冲突分别报
  `ARM_IDENTITY_MISSING`/`ARM_IDENTITY_CONFLICT`。显式 arm 大小写不敏感，支持
  `ourcc/ubcc/ubcc-lossless/lossless-oneway`及`ha-vi/havi/ha_vi`别名。

  兼容旧 manifest：显式`mode=paired`，或未写 mode 但 requirements 中有非空`pairs`时，继续按
  `pair/tc/order`严格配双 arm、绝不跨 pair 或笛卡尔配对，并保持旧正式 replay 数值与证据树不变。
  无论模式如何，描述视图都提供`metric3_arm_comparisons`；只有实际带 pair/order 的 run 才进入
  `metric3_pairs`。PERF-LATENCY 多节点聚合按 samples 对 mean 加权。

Metric3 定义`delta = HA-VI - OurCC`，正值报告`OURCC_FASTER`，负值报告`HA_VI_FASTER`，零值报告
`TIE`。所有方向都是真实统计结果，不做筛选，也不以`delta > 0`作为状态或退出码门槛。`COMPLETE`
只表示证据、arm 和矩阵完整，不表示所有场景均强于 HA-VI。旧 qualification 中的
`thresholds.delta_ticks_strict_min`仅为输入兼容字段，不再执行。重复只作描述性汇总；工具不计算
t-test、置信区间或 p-value。

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

# Metric1 每轮三个角色；spill 与 ideal 虽同为 spill-noopt，role 使二者 slot 可共存。
status = matrix.add(
    id="tc131-r1-ideal", metric=1, tc=131, repetition="r1",
    topology="8n1s", profile="spill-noopt", metric1_role="ideal",
    simulator_log_dir="tc131/r1/ideal/simulator",
    simout_dir="tc131/r1/ideal/simout",
)

# Metric3 independent run：无需预分配 repetition；省略时使用最终 run ID
# 作为内部样本身份。arm 也可由 simulator 日志自动识别。
status = matrix.add(
    metric=3, tc=228,
    topology="2n1s",
    simulator_log_dir="tc228/r1/simulator",
    simout_dir="tc228/r1/simout",
)

# add 返回后即可删除或迁移上述输入目录；finalize 不会重新 open/stat 输入文件。
result = matrix.finalize("/tmp/metric123-report")
report = result["report"]
```

省略`requirements`时，只要 metric/repetition/TC/profile 或 Metric3 TC/arm 等身份字段
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

Metric3 independent 模式不要求调用方预分配 repetition 序号。run 同时省略`id`和`repetition`时，
Matrix 先分配`run-000001`，再将其作为内部样本身份，并标记
`repetition_source=auto-run-id`。不同 worker 都可从`run-000001`开始；批量`merge()`发生 ID 冲突改名时
会同步更新自动样本身份，因此不需要跨 worker 的共享计数器。自动推断 requirements 使用
`min_repetitions=1`按每个 TC/arm 检查样本数量，不把自动 ID 建成 repetition 笛卡尔积。若需要至少 N 个
样本，只配置`metric3.min_repetitions=N`即可。Metric1/Metric2 仍要求 repetition，因为它们需要按轮对齐
naive/spill/ideal 或三个 profile 后再计算比较值。

## 显式资格合同（opt-in）

冻结 standard 合同、`report.metric1/2/3`、`views.standard`和未配置时的退出码/数值完全不变。
额外正式坐标只能由顶层`requirements.qualification_sets`注册；run 自身填写 TC、phase、topology
等字段不能把自己提升为正式资格点。每个 set 有全局唯一`id`和`metric`：

- Metric1：`coordinates=[{tc,topology,home_node,home_socket}]`、`repetitions`、可选
  `ideal_min_capacity`和`thresholds`。仍使用`naive/spill/ideal`、现有容量/Outer公式与角色门禁；
  每个 coordinate 独立聚合，重复名相同也不会碰撞。threshold 默认仍为 ratio `>=1.5`、
  Outer delta `*2GHz <50 cycles`。
- Metric2：`coordinates=[{tc,topology,phase,expected_node,expected_samples}]`、`repetitions`；
  默认 profiles 为三 profile，baseline/result 默认`naive/optimized`，适用门槛500ns、降幅门槛10%。
  注册的 phase/node/samples 是精确合同；未注册自动发现 phase 仍只是 extension。
  多 plane workload 可改用`kind=timer|latency`、`reduction=aggregate|max`、`expected_nodes=[...]`
  和`expected_count`。timer aggregate 使用`sum(counter_ticks)/sum(operations)`；latency aggregate 按
  samples 加权；节点集合、总 count 和频率都必须精确匹配。若 qualification 与冻结 TC135-140/217
  合同相同，run 仍先按冻结定义解析并同时命中 qualification，不会从 standard 视图消失。
- Metric3：只复用 builtin TC228-235 parser、primary和aggregate权重。set 声明
  `mode=paired|independent`、`topologies`、`testcases`、`arms`，并按模式给出`pairs`或
  `repetitions/min_repetitions`。完整性、均值和配对始终按 topology 分开，绝不跨 topology 补槽或混合。
  TC232 的冻结 2N1S 主值仍为`2/3 read + 1/3 write`；额外 topology qualification 按工作负载
  实际操作数计算，P 个 active plane 时使用`read=P/(P+1)`、`write=1/(P+1)`，代表场景组中的
  TC232 分量也使用同一拓扑相关权重。paired set 对同一 TC/metric 的多个 pair 先求 delta 均值，
  再计算 testcase primary 和 tier aggregate；qualification mode 不改写 run 的 standard logical slot。
  `arms`固定要求恰好包含`ourcc`和`ha-vi`。

解析后的 run 新增`formal_contract`和`qualified_contracts`；为兼容既有消费者，
`contract_class`仍只有`standard|extension`。成功资格化的非 standard run 保留
`contract_class=extension`，但进入`views.formal`并从`views.extension`排除；standard run 也可同时
命中资格 set。报告新增`report.qualifications`，输出新增`metric_matrix_formal.tsv`。

资格缺失/失败默认不改变 standard 的`overall_status/exit_code`。只有显式使用可重复 CLI 参数才组合退出状态：

```bash
python3 scripts/extract_metric123_from_logs.py --manifest manifest.json \
  --require-qualification m1-tc132 --require-qualification m3-3n-paired
```

被要求的 ID 未注册或资格证据无效返回2，缺槽返回3，完整但门槛失败返回1。矩阵合并时，同 ID
定义必须完全一致，否则拒绝合并。pickle state 已升级为v2；仍接受旧v1，并只从 snapshot 中的
requirements重建资格 registry，不读取任何原始日志。

报告中的`source_inventory`明确分离三种计数：`logical_runs`是成功解析且未发生 slot 冲突的逻辑
运行数，`unique_files`是证据文件去重数，`source_references`是 timer、latency、Outer、capacity 等
marker/source 行引用数。一个 run 可产生数万条 Outer source reference，因此不得把 sources 数当成
run 数。`NONSTANDARD_CONTRACT`现在包含`failed_gates`，会直接列出 topology、phase、node、samples
或 Metric1 角色门禁中的不匹配项。

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

大量矩阵应使用批量`merge()`纯内存合并：

```python
from scripts.extract_metric123_from_logs import merge

merged = merge(matrix_list)       # 也接受 generator/iterator
result = merged.finalize()
```

`merge()`按输入顺序单次扫描所有 retained snapshot，每条 snapshot 只做一次 fingerprint 和深拷贝；避免
`m1 + m2 + ... + mN`反复扫描累计结果造成的二次复杂度。空输入抛`ValueError`，单元素输入返回完全隔离的
snapshot clone。合并后的 Matrix 仍可继续`add()`、`finalize()`和 pickle。

二元`left + right`保留并内部复用`merge((left, right))`。requirements 做确定性并集，scalar 字段按输入
顺序由后项覆盖，correctness policy 取`strict > required > optional`中更严格者；ID 冲突自动改名，逻辑
slot 冲突仍无效。最终 qualification registry 会在内存 snapshot 上重新匹配`qualified_contracts`，因此
registry 与 run 分布在不同 Matrix 时也能正确进入`views.formal`。被拒绝 attempt 的 ID reservation 会保留；
较宽松 policy 下接受但 correctness 非 PASS 的 snapshot 不能进入较严格合并结果。合并不修改操作数，也不
访问源路径，因此原始目录已删除后仍可`merged.finalize()`；重复的相同 run snapshot 会去重。

矩阵可直接使用 Python `pickle` 序列化。pickle 保存的是版本化内存 snapshot，不保存打开的文件、迭代器
或派生 slot 索引；反序列化会重建 slot 索引，不重新读取任何 raw log。协议0至当前
`pickle.HIGHEST_PROTOCOL`均支持 round-trip；跨 Python 版本长期保存时应由调用方选择兼容的 pickle protocol。
pickle 只应用于可信输入，不应加载来源不明的 pickle 文件。

## 正式结果还原验证

增量 API 已在 Docker 中直接重放当前保留的正式 raw logs，而不是读取既有分析结果作为输入：

- Metric 1/2：`results/metric12-final-v1/cases`中的 72 个 raw-log run；
- Metric 3 p100：`results/metric3-l3-only-v4/cases`中的 80 个 raw-log arm；
- Metric 3 p150：同目录另外 80 个 raw-log arm。

旧 raw replay 中 Metric2/Metric3 数值继续逐字段一致；旧 Metric1 只有 naive/spill/optimized、没有
counterfactual ideal，因此按修正定义为 INCOMPLETE。以下旧 guest delta 仅为弃用的描述兼容值，不能作为 PASS：

```text
Metric 1 capacity ratio       1.5150909423828125
Metric 1 legacy guest delta cycles -1635.994218910734 (deprecated descriptive)
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

`report.md`是首选的人读诊断入口，包含三个中文章节：

- `逐测试诊断`：逐 run 列出 Standard、Formal qualification 或 Extension，并展开冻结合同未通过的
  `failed_gates`、命中的 qualification ID 和其他告警。看到`contract_class=extension`时，应先看这里判断
  它是已资格化的 formal extension，还是 topology/TC/phase/node/samples/profile/role 等门禁不匹配。
- `未接纳的测试`：逐条列出`add()`返回 REJECTED 的 run、slot、错误码和具体证据错误；包括日志缺失、
  correctness 失败、schema 无效及 duplicate slot。
- `未满足的矩阵要求`：展开 Metric1/2/3 的`missing_slots`、Metric3 不完整 pair，以及每个未 PASS
  qualification 的缺失槽位、失败结果和 registry 错误。

机器程序仍应读取`report.json`、`resolved_runs.json`和`issues.tsv`。其中 extension 原因也以
`resolved_runs[*].contract_warnings[*].failed_gates`结构化保存，不需要从 message 文本反向解析。

冻结 standard 与 qualification/extension 的完整性域严格分离。例如 TC142 不在冻结 Metric2 registry，
所以 run 会提示`TC142 is not in the standard Metric2 registry`；但它不会因此进入标准 Metric2
`missing_slots`。只有显式注册 TC142 qualification 后，它的缺槽才出现在对应
`report.qualifications[*].missing_slots`中。旧 pickle 若曾把 TC142 等 extension TC 污染到 inferred standard
requirements，会自动清理并产生`LEGACY_METRIC2_INFERENCE_REPAIRED`。

Metric3 最低样本数缺槽在 JSON 中使用具名对象，不再输出难以理解的位置数组：

```json
{"kind":"minimum_samples","tc":228,"arm":"ha-vi","observed_samples":0,"required_min_samples":1}
```

其含义是 TC228 的 HA-VI arm 当前有0个有效样本，合同最低要求1个；中文 Markdown 显示为
`TC228 / arm=ha-vi：实际样本 0，最低要求 1`。精确 repetition 模式则使用
`kind=exact_repetition`及具名的`repetition/tc/arm`字段。

退出码：`0`完整且通过，`1`完整但指标失败，`2`manifest/日志/重复证据无效，`3`需求矩阵、
independent repetition/arm 覆盖或 paired pair 不完整。independent 不配对；paired 缺失 arm 也不会
与其他 pair 的 arm 组合。
