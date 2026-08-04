# CC-EP 合同验收 TODO

> 建立日期：2026-08-03
> 状态入口：本文件
> 资源约束：所有并行任务合计最多使用 16 logical cores

## P0

| ID | 任务 | 状态 | 验收出口 |
|---|---|---|---|
| A01 | 冻结合同验收基线与权威状态入口 | TODO | 三项目标、三份交付件、8N direct、16N switch 逐项标记 PASS/PARTIAL/UNPROVEN/NOT IMPLEMENTED |
| F01 | 审计当前 C++ 与 TLA+ 语义差异 | DONE | TC224、EP-RNF 仲裁、fault injection 漂移已列明 |
| F02 | TC224 Clear commit/waiter retirement focused formal model | DONE | TLC 274,593 distinct states，零反例 |
| F03 | EP-RNF STALE/IMMED 仲裁 focused formal model | DONE | TLC 328 distinct states，零反例 |
| R01 | 同步 drop/dup/delay/reorder 实现与文档 | DONE | 文档对齐 `ubio_main.cc` deferred queue |
| R02 | TC117-119 verifier 强制 `[UBFAULT]` 证据 | DONE | 无实际故障命中不得 PASS |
| R03 | 复跑 TC117-119 单故障 smoke | DONE | `logs/fault_smoke_20260803`：3/3 PASS，correctness 和 `[UBFAULT]` marker 均满足 |
| R04 | 增加高强度 fault qualification profile | DONE | TC148：32 PA、32 条规则、四种 action 各 8 次，逐规则 hit-count 验收 |
| R05 | 运行高强度 fault qualification | DONE | `logs/fault_all_20260803_strict`：32/32 reads MATCH，32/32 rules exactly once |
| R06 | 甲方 HA lossless 网络与 CPU OoO 边界 | DONE | 交付件 2 明确与 CC transport fault 模型分域 |
| R07 | ARM acquire/release/barrier/OoO litmus 或形式化模型 | TODO | 不把 CPU OoO 等同于网络任意乱序 |
| V01 | 更新 fidelity anchors、模型清单和验证报告 | DONE | 当前实现 focused models 和限制已记录 |
| P01 | 收口 512 KiB 目标 1 最新代码证据 | TODO | +50% capacity、附加同步时延 <50 cycles，当前代码复跑 |
| P02 | 按合同 `baseline >=500 ns` 重算目标 2 | TODO | 平均方式冻结，多轮统计，降幅 >=10% |
| H01 | 建立甲方 HA 参数账本 | TODO | 2N、128 MiB、VI、2-bit、lossless、未知时延参数分级 |
| H02 | 建立 CC/HA 参数化理论时延模型 | TODO | 请求 DAG、上下界、敏感性、break-even |
| N16 | 实现或书面协商 16 节点 Switch 仿真范围 | TODO | 真正 num_nodes=16，不以 8N2S/16 planes 替代 |
| D03 | 刷新交付件 3 的 8N/16N 与性能结论 | TODO | 8N direct、16N switch、目标 1/2/3 状态一致 |

## P1

| ID | 任务 | 状态 | 验收出口 |
|---|---|---|---|
| D01 | 刷新交付件 1 的创新点和指定 HA 对比 | TODO | 统一功能/成本表，避免泛化 HA-C 替代甲方 HA |
| E01 | 生成验收 evidence manifest | TODO | commit、submodule、配置、run ID、timer、sample count、hash 可追溯 |
| E02 | 制作最终评审材料 | TODO | 合同逐项映射、负结果披露、未知参数和后续决策清楚 |

## 当前执行顺序

1. A01：建立合同验收状态总表。
2. H01/H02：甲方 HA 理论比较模型。
3. R07：补 ARM acquire/release/barrier/OoO 验证。

## Fault Injection 强度评估

当前 TC117-119 是快速 smoke，不是高强度 qualification：

- TC117：1 次 Reorder；
- TC118：1 次 Drop + 1 次 Delay；
- TC119：1 次 Drop + 1 次 Duplicate + 1 次 Delay；
- 合计仅 6 次确定性 fault hit；
- 全部集中在 `ClearReq`、固定方向和固定 PA；
- 不覆盖持续 burst、重复命中、不同 delay 档、跨消息类型或接近 timeout/tombstone 边界。

TC148 已完成 Level-1 qualification：32 条 PA、32 次可计数 hit、四种 action 各 8 次，
verifier 逐规则检查恰好一次。当前仍只覆盖 ClearReq。跨消息类型 Level-2
qualification 需先确认各消息的 retry/恢复合同，不能把已知不支持的 fire-and-forget
loss 路径伪装成应当 PASS 的测试。
