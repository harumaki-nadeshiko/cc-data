# CC-EP 合同验收 TODO

> 建立日期：2026-08-03
> 状态入口：本文件
> 资源约束：本轮并行任务合计最多使用 32 logical cores；fault矩阵使用互斥lane 0-31

## P0

| ID | 任务 | 状态 | 验收出口 |
|---|---|---|---|
| A01 | 冻结合同验收基线与权威状态入口 | DONE | 三项目标、fault、R07、N16和三份交付件状态已统一 |
| F01 | 审计当前 C++ 与 TLA+ 语义差异 | DONE | TC224、EP-RNF 仲裁、fault injection 漂移已列明 |
| F02 | TC224 Clear commit/waiter retirement focused formal model | DONE | TLC 274,593 distinct states，零反例 |
| F03 | EP-RNF STALE/IMMED 仲裁 focused formal model | DONE | TLC 328 distinct states，零反例 |
| R01 | 同步 drop/dup/delay/reorder 实现与文档 | DONE | 文档对齐 `ubio_main.cc` deferred queue |
| R02 | TC117-119 verifier 强制 `[UBFAULT]` 证据 | DONE | 无实际故障命中不得 PASS |
| R03 | 复跑 TC117-119 单故障 smoke | DONE | `logs/fault_smoke_20260803`：3/3 PASS，correctness 和 `[UBFAULT]` marker 均满足 |
| R04 | 增加高强度 fault qualification profile | DONE | TC148：32 PA、32 条规则、四种 action 各 8 次，逐规则 hit-count 验收 |
| R05 | 运行高强度 fault qualification | DONE | `logs/fault_all_20260803_strict`：32/32 reads MATCH，32/32 rules exactly once |
| R06 | 甲方 HA lossless 网络与 CPU OoO 边界 | DONE | 交付件 2 明确与 CC transport fault 模型分域 |
| R07 | ARM acquire/release/barrier/OoO litmus 或形式化模型 | DONE（EXECUTABLE/O3 SCOPE） | O3 可执行回归已关闭本期边界；完整 ARMv8 axiomatic/herd7 证明明确为已知限制/范围外 |
| V01 | 更新 fidelity anchors、模型清单和验证报告 | DONE | 当前实现 focused models 和限制已记录 |
| P01 | 收口 512 KiB 目标 1 最新代码证据 | DONE | capacity ratio 1.515091；delta -1635.994219 cycles；72-run矩阵PASS |
| P02 | 按合同 `baseline >=500 ns` 重算目标 2 | DONE | 适用case等权降幅64.759276%；三轮稳定；72-run矩阵PASS |
| H01 | 建立甲方 HA 参数账本 | TODO | 2N、128 MiB、VI、2-bit、lossless、未知时延参数分级 |
| H02 | 建立 CC/HA 参数化理论时延模型 | TODO | 请求 DAG、上下界、敏感性、break-even |
| N16 | 实现或书面协商 16 节点 Switch 仿真范围 | Level-A PASS；Level-B PENDING | Level-A 能力/语义验收通过；Level-B 真正 port-level Switch 仅待合同确认或书面 waiver，不以 8N2S/16 planes 冒充 |
| D03 | 刷新交付件 3 的 8N/16N 与性能结论 | DONE | Markdown/DOCX同步，目标1/2/3、N16和fault状态一致 |

## P1

| ID | 任务 | 状态 | 验收出口 |
|---|---|---|---|
| D01 | 刷新交付件 1 的创新点和指定 HA 对比 | DONE | Markdown/DOCX同步，HA-VI reference-model范围明确 |
| E01 | 生成验收 evidence manifest | DONE | `results/final-acceptance/evidence_manifest.json` |
| E02 | 制作最终评审材料 | DONE | 三份交付件双格式、状态入口、限制和证据映射已同步 |

## 当前执行顺序

1. A01：建立合同验收状态总表。
2. H01/H02：甲方 HA 理论比较模型。
3. N16 Level-B：仅关闭 port-level Switch 合同确认或 waiver；R07 已在 EXECUTABLE/O3 范围完成。

## Fault Injection 强度评估

TC117-119仍是快速smoke；当前高强度资格结论由52-case Q1-Q5矩阵提供：

- TC117：1 次 Reorder；
- TC118：1 次 Drop + 1 次 Delay；
- TC119：1 次 Drop + 1 次 Duplicate + 1 次 Delay；
- 合计仅 6 次确定性 fault hit；
- 全部集中在 `ClearReq`、固定方向和固定 PA；
- 不覆盖持续 burst、重复命中、不同 delay 档、跨消息类型或接近 timeout/tombstone 边界。

TC148 Level-1仍保留；跨消息Q1-Q5已扩展到Clear、Upgrade、Invalidate和Recall链，
覆盖repeated、composed、burst和3N1S/3N2S/8N2S/16N1S，52/52 PASS。证据为
`logs/fault_qualification/q1-q5-final-v1`。

## 当前合同状态补记（2026-08-23）

- 指标 1/2/3：见 `results/metric12-final-v1/report/metric123_report.md`，三项均
  PASS；指标 3 必须写作 **PASS（EXECUTABLE-REFERENCE-MODEL SCOPE）**。
- HA-VI 是冻结的可执行参考模型，不是 proxy，也不是甲方物理芯片测量。
- 指标 3 两层冻结聚合：core TC228-TC230 等权；representative TC231-TC235
  等权，其中 TC232 为 2/3 read + 1/3 write，TC233/234/235 分别采用
  `producer_consumer_service`、`queued_token_end_to_end`、
  `catalog_kv_end_to_end`。
- Q6 retry-exhaustion 不属于本期范围；Q7 定义为新进程启动的 no-fault
  regression，不复用 fault qualification 进程状态。
- 权威证据来自 `results/metric3-l3-only-v4` 与
  `results/metric12-final-v1/report`；provenance 明确记录 dirty worktree，故 PASS
  只绑定所记录 diff/hash，不表示 clean checkout。
- Fault Q1-Q5：52/52 PASS，见
  `logs/fault_qualification/q1-q5-final-v1/summary.json`。
- 三份正式交付件均有同名Markdown/DOCX；同步清单和哈希见
  `docs/design/delivery_document_pairs.json`。
