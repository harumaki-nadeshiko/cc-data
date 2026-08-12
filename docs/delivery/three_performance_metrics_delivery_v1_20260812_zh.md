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
| 目标 3：OurCC 相对甲方 HA | OurCC 理论平均时延严格 `<` HA | Scheme B 场景向量和 N=2/4/8 break-even 已完成 | `CONDITIONAL PASS`（理论） |

对外推荐结论：

> 目标 1/2 的既有量化结果超过合同门槛，但在冻结代码多轮重算和 E5 provenance 闭环前，
> 最终状态保持 PARTIAL。目标 3 的正确比较对象是“MESI + metadata offload + lossless one-way
> Clear”对“VI + limited address space + write-back + write-invalidate HA”。在地址空间对齐的
> Scheme B 下，场景向量和严格 break-even 已完成；合同权重满足对应 N 的不等式时可判理论
> STRICT PASS，否则为 TIE 或 FAIL。当前权重尚未签署，因此第一版状态为 CONDITIONAL PASS。

## 2. 正式 Source

| Source | 提交 | 用途 |
|---|---|---|
| `docs/measure/full_performance_metrics_report_20260729_zh.md` | `27d6dd1` | 目标 1/2 数值和公式 |
| `docs/measure/performance_comparison_brief_20260729_zh.md` | `27d6dd1` | 目标 1/2 汇报口径 |
| `docs/delivery/performance_metrics_summary_20260731_zh.md` | `6f93f19` | 性能状态和负结果边界 |
| `docs/measure/tc_matrix_execution_catalog_20260805_zh.md` | `8876aa0` | TC131 三 profile 运行记录 |
| `docs/research/ourcc_vs_vi_bitmap_ha_theory_and_simulation_contract_20260811_zh.md` | `5c05249` 引入，本次修正 flat-bitmap 容量 | 目标 3 指定方案、场景向量和 break-even 主 source |
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
stable metadata  = 1-bit local VI + (N-1)-bit remote presence
address space    = limited exact range
write policy     = write-back
write protocol   = write-invalidate
write permission = 每次 write 都经过 Home transaction
dirty bit        = 无额外节点级 dirty/latest bit
overflow         = 目标空间内无 overflow；更大范围才使用 ideal parallel broadcast
direct transfer  = 不考虑
```

### 5.3 共同假设

1. 双方 distributed Home、外部拓扑和 fabric `tau` 一致。
2. requester、Home、holder placement 由地址和访问节点决定。
3. 同一 cacheline 在 Home 最多一笔 active transaction。
4. HA requester Ack 对应 OurCC one-way Clear/Ack。
5. 双方 Home 在 Ack/Clear 到达后 commit/release 同址锁。
6. requester install ordering 和 same-line serialization 满足冻结安全接口。
7. 未单独配置的本地处理差近似为 0。
8. 已显式建模的 metadata offload、broadcast 和 fabric latency不得作为未知项清零。

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

## 7. Scheme B 场景向量

地址空间对齐采用 Scheme B。512 KiB 的 HA flat bitmap 按严格 `N bit/line` 分别覆盖 128/64/32 MiB，
与 N=2/4/8 的目标地址空间完全一致，因此 HA 全部走 exact path；OurCC resident miss 走 metadata
offload。以下为修正容量口径后的 `Delta = OurCC - HA`：

| 场景 | N=2 | N=4 | N=8 |
|---|---:|---:|---:|
| Home latest read | +66.2 ns | +63.5 ns | +58.6 ns |
| sole clean/potential holder read | -319.8 ns | -527.5 ns | -634.9 ns |
| dirty/latest holder read | +66.2 ns | +63.5 ns | +58.6 ns |
| cold/no-sharer write | +66.2 ns | +63.5 ns | +58.6 ns |
| single-sharer write | +66.2 ns | +63.5 ns | +58.6 ns |
| multi-sharer write | +66.2 ns | +63.5 ns | +58.6 ns |
| dirty ownership handoff | +66.2 ns | +63.5 ns | +58.6 ns |
| repeated same-writer write | -423.5 ns | -628.5 ns | -731.0 ns |

负值表示 OurCC 更快。该表说明：HA flat bitmap 的容量优势使 OurCC 在多数首次事务上承担约
58.6-66.2 ns 平均 offload penalty；OurCC 的严格优势集中在 sole-clean ambiguity 和 repeated E/M
write permission reuse。

## 8. 严格 Break-Even

定义：

```text
w_s = sole clean/potential holder read
w_r = repeated same-writer write
w_p = 其他承担平均 metadata offload penalty 的首次事务总权重
```

### 8.1 N=2

```text
STRICT PASS iff:
319.7578*w_s + 423.5*w_r > 66.2422*w_p
```

### 8.2 N=4

```text
STRICT PASS iff:
527.4727*w_s + 628.5*w_r > 63.5273*w_p
```

### 8.3 N=8

```text
STRICT PASS iff:
634.8594*w_s + 731.0*w_r > 58.6406*w_p
```

等号成立时为 `TIE`，不满足合同严格 `<`；左侧小于右侧时为 `FAIL`。

这些公式本身不依赖 workload。合同可以直接冻结理论权重或允许的权重区域；workload、trace 和
target counter只是在合同未给权重时用于校准 `w` 的可选来源。

## 9. 目标 3 判定

| 项目 | 当前结论 |
|---|---|
| 指定比较方案 | 已冻结 |
| Scheme B 场景向量 | 已完成 |
| N=2/4/8 break-even | 已完成 |
| 所有权重下无条件严格 `<` | 不成立 |
| 满足 break-even 的权重区域 | `STRICT PASS` |
| 当前总状态 | `CONDITIONAL PASS`（理论） |

当前不能写成无条件 PASS，因为 Scheme B 包含 OurCC 较慢场景，且合同 operation weights 尚未签署。
也不能写成“尚未分析”：场景向量和严格判定公式已经形成。

## 10. 关闭条件

1. 双方签署第 5 节的指定方案和共同安全完成点。
2. 确认 HA 不含超出 VI/presence bitmap 的 dirty/latest 等价持久状态。
3. 确认 HA flat bitmap `N bit/line` 容量和 OurCC metadata offload 参数口径。
4. 合同冻结 operation weights，或冻结允许的 weight 区域。
5. 将权重代入对应 N 的 break-even，得到 `Delta_mean < 0`。
6. 由双方或独立评审者复核公式、量纲和不等式方向。

## 11. 禁止表述

- 不把 current sync `ClearResp` 纳入指定 OurCC 理论 candidate。
- 不把 lossless one-way Clear 写成当前已有性能实测结果。
- 不把 Scheme A 的各自缩小 coherent range写成地址空间对齐比较。
- 不把 HA10 workload 名称写成甲方 HA 实现。
- 不把示例 mix 写成合同权重。
- 不把 `TIE` 或同 K 写成满足严格 `<`。
- 不隐藏 Scheme B 中多数首次事务的 OurCC metadata offload penalty。

## 12. 签署建议

| 项目 | 第一版建议状态 |
|---|---|
| 目标 1 数值达到门槛 | 可确认 |
| 目标 1 最终冻结 PASS | 暂不确认，等待多轮重算和 E5 manifest |
| 目标 2 历史适用集合达到门槛 | 可确认 |
| 目标 2 最终冻结 PASS | 暂不确认，等待统一筛选和多轮重算 |
| 目标 3 指定理论方案和场景向量 | 可确认 |
| 目标 3 N=2/4/8 break-even | 可确认 |
| 目标 3 无条件 STRICT PASS | 暂不确认 |
| 目标 3 条件结论 | `CONDITIONAL PASS`；待合同权重签署 |
