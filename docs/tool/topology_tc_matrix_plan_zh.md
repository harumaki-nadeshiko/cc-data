# 拓扑 × TC 矩阵计划生成器

`scripts/generate_topology_tc_matrix_plan.py` 是确定性的**纯计划生成器**。它只写 JSON
和 JSONL，不调用 Docker、不构建 workload、不启动 gem5/networksim，也不执行任何生成的
命令。实际执行必须由外部调度器或操作者另行完成。

## 1. 生成方法

生成计划本身不属于构建或仿真，可直接运行 Python：

```bash
python3 scripts/generate_topology_tc_matrix_plan.py \
  --output-dir /path/to/plan \
  --formal-repetitions 3 \
  --metric3-pairs 5
```

输出目录中恰好生成：

- `execution_plan.json`：完整计划、约束、计数和全部 job；
- `smoke_manifest.json`：代表性子集，每个拓扑各一个 portable 和一个 Metric3 pair；
- `qualification_manifest.json`：完整拓扑 × TC 资格矩阵；
- `formal_manifest.json`：通过资格门禁后才能执行的正式矩阵；
- `qualification_sets.json`：资格 ID、成员和 all-pass 门禁；
- `commands.jsonl`：每行一个 job ID、tier 和 Docker 命令。

文件中没有时间戳、随机数和输出目录绝对路径。相同参数向不同目录生成的六个文件逐字节
相同。job ID、pair 顺序、seed、run-private `E2E_RUN_ID` 和结果路径均由固定输入推导。

## 2. 固定范围与计数

拓扑固定为：

```text
2n1s, 3n1s, 3n2s, 8n1s, 8n2s, 16n1s
```

命令使用与名称完全对应的 `run_multi.sh` 参数：`--2n1s`、`--3n1s`、
`--3n2s`、`--8n1s`、`--8n2s`、`--16n1s`，不使用旧的 `--1s/--2s`
别名。

TC 固定为两组：

- portable：TC142-TC147；
- Metric3：TC228-TC235。

默认计数如下：

| Tier | 组成 | executable jobs |
|---|---|---:|
| smoke | 6 个 portable 代表项 + 6 个双臂 Metric3 pair | 18 |
| qualification | `6 topology × 6 portable × 1 arm` + `6 × 8 Metric3 × 2 arms` | 132 |
| formal portable | `6 × 6 × (3 profiles + 1 IdealDir role) × 3 repetitions` | 432 |
| formal Metric3 | `6 × 8 × 5 pairs × 2 arms` | 480 |
| formal 合计 | 432 + 480 | 912 |

qualification 的“完整 cross product”有 `6 × (6+8)=84` 个资格 slot；Metric3
slot 是 paired gate，因此每个 slot 恰好有 `ourcc`、`ha-vi` 两个 executable job。

## 3. Tier 和资格门禁

- smoke 只做代表性探路，一次重复，不能作为正式数据源；
- qualification 对全部拓扑和全部 TC 做一次覆盖；portable 使用 UBCC
  `spill-noopt`，Metric3 使用一对确定性 AB/BA 双臂；
- formal job 的 `depends_on` 指向对应 `qualification_id`，且 `source` 明确记录
  `result_tier=qualification`；
- 每条命令同时注入 `RESULT_TIER`、`QUALIFICATION_ID`、
  `SOURCE_RESULT_TIER`、`SOURCE_QUALIFICATION_ID`，便于外部 runner 拒绝越级执行；
- 三个 tier 的 `LOG_BASE` 分别位于 `/results/smoke/cases`、
  `/results/qualification/cases`、`/results/formal/cases`。formal 不复用 smoke 或
  qualification 的结果路径。

生成器只描述依赖，不读取结果、更不会自行判定或绕过 gate。外部调度器必须在
`qualification_sets.json` 的全部成员 PASS 后才调度依赖它的 formal jobs。

同一文件还包含 `extractor_requirements.qualification_sets`，可直接放入
`Metric123RawLogMatrix` 的 requirements：

- `m1-portable-<topology>`：TC142-TC147 的 naive/spill/ideal 三角色容量/Outer 合同；
- `m2-portable-<topology>`：各 plane 的 end-to-end GUEST-TIMER，按总 operations 聚合；
- `m3-paired-<topology>`：TC228-TC235 的 5-pair 双 arm 合同。

Metric1 资格仍要求 IdealDir 日志满足 oversized ResidentDir、无 fill、exact-live=0 和 completed
Outer 门禁；计划生成了 role 不表示运行结果自动通过。

## 4. TC142-TC147 支持边界

这六个 workload 默认是 central-home。计划因此默认 **UBCC-only**：

- qualification 使用 `spill-noopt`；
- formal 使用 `naive`、`spill-noopt`、`optimized` 三 profile，并额外运行 `IdealDir`
  counterfactual role；因此同一坐标可同时形成 Metric1 容量/Outer 资格和 Metric2 profile 对比；
- 默认正式重复数为 3，可通过 `--formal-repetitions` 修改；
- 不生成 TC142-TC147 的 HA-VI arm。

审计边界是：未修改 workload 的 striped-home 行为前，HA-VI paired 方案在 3n1s
以上拓扑不受支持。不能因为命令能够启动，就把 central-home 结果宣称为大拓扑 HA-VI
可扩展性证据。

portable p150 固定总 footprint 为 98,304 lines；每个 TC 的 pressure lines 按
`98304 - hot_lines_per_plane × active_socket_planes` 计算，避免双 socket 拓扑仍误用
node 数。

## 5. TC228-TC235 Metric3 口径

Metric3 在全部六种拓扑上规划 `ourcc/ha-vi` paired jobs，固定为 L3-only：

```text
METRIC3_L3_EXPERIMENT_MODE=l3-only
L3_DIRECTORY_PRESSURE_LINES=0
```

计划不写死 `HA_EXACT_BYTES`。`run_multi.sh` 按 `active_planes` 和 512 KiB exact-bitmap
预算计算安全窗口，避免把 2N1S 的 128 MiB 配置错误复用到 3N2S、8N2S 或 16N1S。

默认 5 pairs，可通过 `--metric3-pairs` 修改。每个 `(topology, TC, pair)` 根据固定
奇偶式产生 AB 或 BA，两个 arm 共用 pair ID、order 和 seed，且 `sequence_index` 为
1/2。这里的 A/B 分别对应 `ourcc`、`ha-vi`。

需要保留三项解释限制：

1. **双 socket ring**：当前 TC228、TC229、TC233 保持 socket 不变，只让 node 编号前驱/后继，
   因此 3N2S/8N2S 实际形成“每个 socket 一条 node ring”，不是一条覆盖全部 plane 的 ring。
   计划会如实记录该语义；若后续改成 all-plane ring，必须以新的 workload/qualification ID 区分。
2. **TC232**：每个 active plane 执行 16 次 read，全局总共只有 16 次 write。令 active plane
   数为 P，则实际操作权重为 `read=P/(P+1)`、`write=1/(P+1)`。只有 P=2 的 2N1S
   使用冻结的 `2/3 × read + 1/3 × write`；其他拓扑不得沿用该固定权重。
3. **TC234**：queued-token 有串行依赖链。其 end-to-end latency 有意义，但它是并行
   throughput scaling 的弱证据，不能单独支持扩展性结论。

## 6. Docker 命令契约

`commands.jsonl` 中每条命令都以 `docker run` 开头，并固定包含：

```text
--rm
--network none
ubcc-dev:ubuntu20.04
```

执行前由操作者设置两个宿主环境变量：

```bash
export WORKSPACE=/mnt/data2/cgc/cc-ep
export RESULT_ROOT=/path/to/private/results
```

命令把它们分别挂载到 `/workspace` 和 `/results`。每个 job 有独立 container name、
`E2E_RUN_ID` 和 `/results/<tier>/cases/<job-id>`，从而隔离 workload build/run-private
路径及结果路径。不要把 `RESULT_ROOT` 指向计划输出目录，也不要让多个计划共享同一结果根。

计划按拓扑携带 timeout、small/medium/large resource class、Docker CPU/内存上限和
预期 child exit 数。预期值为 `gem5 nodes + UBIO planes + 1 networksim`：

| topology | expected child exits |
|---|---:|
| 2n1s | 5 |
| 3n1s | 7 |
| 3n2s | 10 |
| 8n1s | 17 |
| 8n2s | 25 |
| 16n1s | 33 |

这些字段是外部 runner 的验收输入；生成器本身不会检查 child，也不会执行命令。
