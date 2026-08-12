# CC-EP 三项性能指标第一版交付件

> 版本：V1.0
> 日期：2026-08-12
> 分支：`doc`
> 用途：合同目标 1、目标 2、目标 3 的第一版统一评审材料
> 文档属性：目标 1/2 使用既有性能结果；目标 3 使用 2026-08-11 冻结理论模型，不是新一轮实测报告

## 1. 一页结论

| 指标 | 合同门槛 | 当前结果 | 第一版状态 |
|---|---|---|---|
| 目标 1A：512 KiB 等效追踪容量 | spill 不低于 naive 的 150% | `102,656 / 65,536 = 156.64%` | 数值 PASS；最终 `PARTIAL` |
| 目标 1B：压力后附加同步成本 | spill-noopt 相对 naive `<50 cycles` | `+6.03 ns = 12.06 cycles @ 2 GHz` | 数值 PASS；最终 `PARTIAL` |
| 目标 2：CC 端到端时延降低 | 适用集合平均降低 `>=10%` | 历史适用集合 case-level 等权平均降低 `54.32%` | 数值 PASS；最终 `PARTIAL` |
| 目标 3：OurCC 相对甲方 HA | OurCC 理论平均时延严格 `<` HA | Scheme A/B micro-scenario 数值、metadata warm/cold envelope 和加权方法已形成 | `UNPROVEN`；明确 sensitivity 点 PASS |

对外推荐结论：

> 目标 1/2 的既有量化结果超过合同门槛，但在冻结代码多轮重算和 E5 provenance 闭环前，
> 最终状态保持 PARTIAL。目标 3 的正确比较对象是“MESI + metadata offload + lossless one-way
> Clear”对“VI + limited metadata + write-back + write-invalidate HA”。Scheme A 通过缩小共同
> coherent address space换取 N-bit exact metadata；Scheme B 保留 128 MiB、固定 2-bit coarse state，
> 每次 coherence transaction都需要 broadcast。两套方案均已形成 micro-scenario 数值和加权模板；
> 最终权重及 OurCC `q_on/h_L3/Q/Wcrit` 尚未签署，因此合同状态为 UNPROVEN；文档只对明确列出的
> 零排队 sensitivity 点给出 PASS，不把示例外推为合同结论。

## 2. 正式 Source

| Source | 提交 | 用途 |
|---|---|---|
| `docs/measure/full_performance_metrics_report_20260729_zh.md` | `27d6dd1` | 目标 1/2 数值和公式 |
| `docs/measure/performance_comparison_brief_20260729_zh.md` | `27d6dd1` | 目标 1/2 汇报口径 |
| `docs/delivery/performance_metrics_summary_20260731_zh.md` | `6f93f19` | 性能状态和负结果边界 |
| `docs/measure/tc_matrix_execution_catalog_20260805_zh.md` | `8876aa0` | TC131 三 profile 运行记录 |
| `docs/research/ourcc_vs_vi_bitmap_ha_theory_and_simulation_contract_20260811_zh.md` | `5c05249` 引入，本次修正 Scheme A/B | 目标 3 指定方案、Tag/容量、micro-scenario 和加权方法主 source |
| `docs/delivery/ourcc_vs_customer_ha_target3_benchmark_and_delivery_20260804_zh.md` | `a9e8f47` 后由 `5c05249` 修订 | 旧分支分析和公平完成点背景 |
| `docs/delivery/acceptance_metrics_deliverables_todo_20260807_zh.md` | 本次同步更新 | 三指标状态总账 |

若 8 月 4 日旧分支分析与 8 月 11 日冻结稿冲突，以 8 月 11 日冻结稿为准。

## 3. 目标 1

### 3.1 容量

```text
capacity_ratio = 102656 / 65536
               = 1.5664
               = 156.64%

required_lines = 65536 * 1.5
               = 98304

margin = 102656 - 98304
       = 4352 lines
```

目标 1A 数值超过 150% 门槛。

### 3.2 附加成本

```text
naive Outer mean       = 162.72 ns
spill-noopt Outer mean = 168.76 ns
extra                  = 6.03 ns
extra_cycles           = 6.03 ns * 2 GHz
                       = 12.06 cycles
```

目标 1B 数值低于 50 cycles 门槛。

### 3.3 状态

目标 1 数值 PASS，最终状态 `PARTIAL`。尚需在冻结代码上重算 capacity/evaluation JSON、完成至少
三轮独立运行并绑定完整 evidence manifest。

## 4. 目标 2

历史适用集合结果：

| Case | 降幅 |
|---|---:|
| TC135 | 90.63% |
| TC136 | 87.88% |
| TC137 | 21.54% |
| TC138 | -12.12% |
| TC139 | 90.77% |
| TC217/HA10 | 47.24% |
| case-level 等权平均 | 54.32% |

目标 2 数值超过 10% 门槛，并完整保留 TC138 的退化项。最终状态 `PARTIAL`，尚需从冻结基线
原始数据按 `>=500 ns` 统一筛选、多轮重算并完成 E5 provenance。

`HA10` 是 portable workload 名称，不是甲方 HA 实现或甲方 HA 实测。

## 5. 目标 3 比较对象

### 5.1 OurCC 理论方案

```text
coherence       = MESI
metadata        = resident directory + offload
address space   = 与 HA 目标 coherent address space 对齐
transport       = lossless / ordered / exactly-once
clear           = one-way Clear/Ack
requester root  = 不等待同步 ClearResp
write reuse     = E/M repeated write 可本地复用权限
```

这是目标 3 的理论优化 profile。不得将其写成当前 `clear-ack` profile 的已有实测结果。

### 5.2 甲方 HA 理论方案

```text
coherence        = VI
write policy     = write-back
write protocol   = write-invalidate
write permission = 每次 write 都经过 Home transaction
dirty bit        = 无额外节点级 dirty/latest bit
direct transfer  = 不考虑

Scheme A:
    stable metadata = N bit/line
    address space   = 256/N MiB
    routing         = exact bitmap

Scheme B:
    stable metadata = 2 bit/line
    address space   = 128 MiB
    routing         = normal broadcast/probe for coarse state
```

### 5.3 共同假设

1. 双方 distributed Home、外部拓扑和 fabric `tau` 一致。
2. requester、Home、holder placement 由地址和访问节点决定。
3. 同一 cacheline 在 Home 最多一笔 active transaction。
4. HA requester Ack 对应 OurCC one-way Clear/Ack。
5. 双方 Home 在 Ack/Clear 到达后 commit/release 同址锁。
6. requester install ordering 和 same-line serialization 满足冻结安全接口。
7. 未单独配置的本地处理差近似为 0。
8. 已显式建模的 metadata onload、broadcast 和 fabric latency不得作为未知项清零。

## 6. 完成点和模型

```text
T_resp    = requester 收到 data/permission response
T_release = requester Ack/Clear 到达 Home，Home commit/release
T_root    = requester 按共同协议定义完成 root operation
```

单类操作：

```text
T_i = K_i * tau + P_i
```

- `K_i`：最长串行关键路径的 fabric legs，不是消息总数。
- `tau`：一个归一化单向 fabric leg 时延。
- `P_i`：目录、peer、data、install、commit、offload、broadcast aggregation 和 queue 等处理项。
- `w_i`：操作类权重，`w_i >= 0` 且 `sum(w_i)=1`。

平均差值：

```text
Delta_mean = T_mean_OurCC - T_mean_HA
           = sum_i(w_i * Delta_i)

合同通过条件：Delta_mean < 0
```

## 7. Scheme A

Scheme A 的共同地址空间和 OurCC 压缩 Tag：

| N | 共同地址空间 | OurCC line-address bits | EPOCH24 capacity/coverage | NO_EPOCH capacity/coverage |
|---:|---:|---:|---:|---:|
| 2 | 128 MiB | 21 | 81,920 / 3.906% | 262,144 / 12.5% |
| 4 | 64 MiB | 20 | 81,920 / 7.813% | 196,608 / 18.75% |
| 8 | 32 MiB | 19 | 73,728 / 14.063% | 163,840 / 31.25% |

OurCC 必须使用 `tag_bits = line_address_bits - set_bits`。不能继续使用默认 40-bit PA，也不能使用旧的
`tag ~= log2(ways)` 近似。

Scheme A exact/resident-hit 的主要结论：首次 Home-latest、dirty-owner、single/multi-sharer 和 handoff
路径大多同阶；OurCC 在 sole-clean read 和 repeated E/M write上严格占优。若发生 metadata onload，
每个 scenario 另加：

```text
P_meta,A,j = q_on,A,j * [49*h_A,j + 90*(1-h_A,j) + Q_A,j] + Wcrit_A,j
```

代表 micro-scenario 的 `Delta exact = OurCC - HA`：

| Scenario | N=2 | N=4 | N=8 |
|---|---:|---:|---:|
| Home latest clean read | 0 ns | 0 ns | 0 ns |
| sole clean holder read | -386 ns | -591 ns | -693.5 ns |
| dirty/latest holder read | 0 ns | 0 ns | 0 ns |
| cold/no-sharer write | 0 ns | 0 ns | 0 ns |
| single-sharer write | 0 ns | 0 ns | 0 ns |
| multi-sharer write | 0 ns | 0 ns | 0 ns |
| repeated same-writer write | -423.5 ns | -628.5 ns | -731 ns |
| dirty ownership handoff | 0 ns | 0 ns | 0 ns |

## 8. Scheme B

Scheme B 对所有 N 保留 128 MiB 和 2-bit coarse state。N=2/4/8 每次被计入目标 3 的 coherence
transaction都使用 normal broadcast/probe，不是 overflow fallback。HA 乐观并行 broadcast 下界：

| N | read/probe | write-invalidate |
|---:|---:|---:|
| 2 | 896 ns | 890 ns |
| 4 | 1511 ns | 1505 ns |
| 8 | 1613.5 ns | 1607.5 ns |

代表 micro-scenario 的 `Delta exact = OurCC - HA`：

| Scenario | N=2 | N=4 | N=8 |
|---|---:|---:|---:|
| Home latest read，需要 probe | -386 ns | -796 ns | -796 ns |
| dirty/latest holder read | 0 ns | -205 ns | -102.5 ns |
| cold/no-sharer write，需要 broadcast | -380 ns | -790 ns | -790 ns |
| single-sharer write | 0 ns | -205 ns | -102.5 ns |
| multi-sharer write | 0 ns | 0 ns | 0 ns |
| repeated same-writer write | -888.5 ns | -1503.5 ns | -1606 ns |

Scheme B 不设置可跳过 broadcast 的 2-bit fast path。OurCC metadata onload项为：

```text
P_meta,B,j = q_on,B,j * [49*h_B,j + 90*(1-h_B,j) + Q_B,j] + Wcrit_B,j
```

## 9. Offload/L3 口径

- 49 ns：历史报告中的 HN-F L3 warm MetaRNF path value。
- 90 ns：历史报告中的 L3 miss到 metadata DRAM cold path value。
- 69.5 ns：旧 50/50 假设，不是测量平均，已废弃。
- 历史 10% cold miss和 9 ns 加权成本也是模型假设，不是通用 workload counter。
- 地址空间缩小和 Tag 压缩会提高 resident coverage，也可能提高 metadata L3 hit率，但后者必须由
  metadata working set/reuse测量或冻结，不能从容量比直接推出。

HN-F L3 为 256 KiB，H64 每个 64B bucket含 5 个 slots，理想最多容纳约 20,480 个 metadata entries。
若完整地址空间均形成 metadata，L3 静态覆盖比例约为：

| Scheme | N=2 | N=4 | N=8 |
|---|---:|---:|---:|
| A：128/64/32 MiB | 0.98% | 1.95% | 3.91% |
| B：固定 128 MiB | 0.98% | 0.98% | 0.98% |

该比例不是 `h_L3`。repeat-one-bucket、低于 256 KiB metadata working set和 streaming应分别给出
HN-F hit/miss及 warm/cold latency。

每个评审 scenario 至少给出 resident-hit、forced warm onload、forced cold onload三个数。

## 10. Micro-Scenario 加权评审

每行至少包含：

```text
scheme, N, scenario_id,
T_HA,
T_Our_exact,
q_on, h_L3, Q,
Wcrit, P_meta,
T_Our,
Delta,
weight
```

```text
T_mean_HA  = sum_j(weight_j * T_HA,j)
T_mean_Our = sum_j(weight_j * T_Our,j)
Delta_mean = sum_j(weight_j * Delta_j)

STRICT PASS iff Delta_mean < 0
```

冻结 scenario ID 为 `R_HOME/R_SOLE_CLEAN/R_SOLE_DIRTY/W_COLD/W_SINGLE/W_MULTI/W_REPEAT/W_HANDOFF`。
冻结稿已提供八 scenario 等权 sensitivity 示例。该示例取 `Q=0`、`Wcrit=0`，只展示格式和数值
envelope，不是最终合同权重。

| Scheme | N | HA mean | Our resident-hit | Delta | Our forced-warm | Delta | Our forced-cold | Delta |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 2 | 740.6 | 639.4 | -101.2 | 682.3 | -58.3 | 718.2 | -22.4 |
| A | 4 | 1099.4 | 946.9 | -152.4 | 989.8 | -109.6 | 1025.7 | -73.7 |
| A | 8 | 1253.1 | 1075.1 | -178.1 | 1117.9 | -135.2 | 1153.8 | -99.3 |
| B | 2 | 894.5 | 639.4 | -255.1 | 682.3 | -212.2 | 718.2 | -176.3 |
| B | 4 | 1509.5 | 946.9 | -562.6 | 989.8 | -519.7 | 1025.7 | -483.8 |
| B | 8 | 1612.0 | 1075.1 | -536.9 | 1117.9 | -494.1 | 1153.8 | -458.2 |

forced-warm/forced-cold 假设除 repeated write 外，其余七项都发生 onload，仅用于展示路径 envelope。
最终评审必须用实际或冻结的 `q_on/h_L3/Q/Wcrit` 和 scenario weights替换。

Scheme A N=2 forced-cold 等权点的 PASS 余量最小，为 22.44 ns/op。若七个 onload项具有相同额外
`Q+Wcrit=X`，则该 sensitivity 点要求 `X<25.64 ns/onload`；这不是其他权重的通用边界。

## 11. 目标 3 判定

| 项目 | 当前结论 |
|---|---|
| 指定比较方案 | Scheme A/B 已重新冻结 |
| Scheme A Tag/容量模型 | 已完成 |
| Scheme A/B micro-scenario 数值 | 已完成基础值与 warm/cold envelope |
| 具体加权平均 | 已给方法和 sensitivity；合同权重未签署 |
| `q_on/h_L3/Q/Wcrit` | 尚需按 scenario 冻结或测量 |
| 明确等权、Q=0、Wcrit=0 sensitivity | Scheme A/B N=2/4/8 均 PASS |
| 当前合同状态 | `UNPROVEN` |

当前不能写成无条件 PASS，因为最终权重和 metadata locality 参数尚未签署。也不能写成“尚未分析”：
两套 Scheme 的容量、Tag、DAG、micro-scenario 基础数值和
加权方法已经形成。

## 12. 关闭条件

1. 双方签署 Scheme A/B 定义和共同安全完成点。
2. 确认 Scheme B 2-bit coarse metadata和每次 transaction normal broadcast语义。
3. 确认 HA 不含未披露的 dirty/latest 等价持久状态。
4. 为每个 micro-scenario 冻结或测量 `q_on/h_L3/Q/Wcrit`。
5. 合同冻结 scenario weights，逐行计算数值和 Delta。
6. 对 Scheme A/B 分别计算加权平均并证明 `Delta_mean < 0`。
7. 由双方或独立评审者复核公式、量纲和不等式方向。

## 13. 禁止表述

- 不把 current sync `ClearResp` 纳入指定 OurCC 理论 candidate。
- 不把 lossless one-way Clear 写成当前已有性能实测结果。
- 不把 Scheme B broadcast写成 overflow fallback。
- 不让 Scheme A 只缩 HA 地址空间而不同时缩 OurCC Tag。
- 不把 69.5 ns写成测量平均或固定 offload成本。
- 不把 HA10 workload 名称写成甲方 HA 实现。
- 不把示例 mix 写成合同权重。
- 不把 `TIE` 或同 K 写成满足严格 `<`。
- 不隐藏 Scheme B normal broadcast成本或 OurCC metadata onload penalty。

## 14. 签署建议

| 项目 | 第一版建议状态 |
|---|---|
| 目标 1 数值达到门槛 | 可确认 |
| 目标 1 最终冻结 PASS | 暂不确认，等待多轮重算和 E5 manifest |
| 目标 2 历史适用集合达到门槛 | 可确认 |
| 目标 2 最终冻结 PASS | 暂不确认，等待统一筛选和多轮重算 |
| 目标 3 Scheme A/B 和 micro-scenario 基础表 | 可确认 |
| 目标 3 metadata warm/cold envelope | 可确认 |
| 目标 3 无条件 STRICT PASS | 暂不确认 |
| 目标 3 sensitivity 结果 | 明确等权、零排队 envelope 为 PASS |
| 目标 3 合同结论 | `UNPROVEN`；待权重和 `q_on/h_L3/Q/Wcrit` 签署 |
