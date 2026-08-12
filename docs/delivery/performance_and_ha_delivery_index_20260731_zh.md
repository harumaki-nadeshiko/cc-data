# 性能汇报与 HA Workload 交付索引

> 交付日期：2026-07-31

## 明早汇报建议顺序

1. 先讲两个正式性能目标及结论：目标 1、目标 2 均 PASS。
2. 再讲结果边界：spill 主要改善 capacity pressure 后的高复用路径，不是所有路径都更快。
3. 用 HA10 展示可跨方案比较的实际 workload：延迟降低 47.24%，吞吐提升 89.54%。
4. 用四类请求链解释差异来源：remote read、shared-to-writer、dirty capacity、mixed catalog。
5. 最后交付 HA workload 源码、平台 shim 契约、JSONL schema 和验收清单。

## 文档清单

| 文档 | 用途 |
|---|---|
| `docs/delivery/three_performance_metrics_delivery_v1_20260812_zh.md` | 三项合同性能指标第一版；目标 3 以 8 月 11 日 VI bitmap HA 冻结稿和 Scheme B break-even 为准 |
| `docs/delivery/performance_metrics_summary_20260731_zh.md` | 当前全部性能证据、两个目标判定、风险和汇报口径 |
| `docs/delivery/ha_comparison_request_chains_20260731_zh.md` | 代表场景的请求树、时序、dataflow 和 HA trace 契约 |
| `docs/delivery/ha_workload_scenario_catalog_20260731_zh.md` | HA01-HA12、TC142-TC147 和 2N1S adapted TC 的逐场景详细规范 |
| `docs/dev_manual/ha_workload_delivery_guide_zh.md` | HA workload bare-metal Arm 使用和移植手册 |
| `docs/measure/tc217_ha10_2n1s_perf_20260728.md` | HA10 CC reference 结果 |
| `docs/measure/portable_large_workload_matrix_20260730_zh.md` | TC142-TC147 四拓扑 24/24 结果 |

## 推荐交付包

```text
ha-workload-delivery/
  e2e_ha_2n1s_core.c
  e2e_ha_cgroup_2n1s.c
  e2e_tc142_db_oltp_buffer_pool.c
  e2e_tc143_db_btree_traversal.c
  e2e_tc144_db_wal_checkpoint.c
  e2e_tc145_faas_warm_invocation.c
  e2e_tc146_graph_frontier.c
  e2e_tc147_feature_store.c
  portable_large_workload.h
  ha_platform.h                 # 由目标方实现，接口见移植手册
  ha_platform_example.c         # 目标方按 SoC/SDK 替换
  ha_linker_example.ld          # 仅作为段布局示例
  README.md
  ha_workload_delivery_guide_zh.md
  ha_comparison_request_chains_20260731_zh.md
  ha_workload_scenario_catalog_20260731_zh.md
  summarize_2n1s_guest.py
  schema/
    ha_result_schema.json
```

当前仓库已包含 portable core、summarizer 和文档化 schema；真正发送给目标方时，
不要打包本地 logs、ELF、HTML、`build/runs` 或私有 CC protocol trace。

TC123/130/132/135/138/139 的原源码仍固定为 3N1S；独立 2N1S reference
implementation 为 TC222-TC227。TC222/223/225/226/227 默认 optimized smoke PASS；
TC224 的 512/4096 qualification profile PASS，但 8,192/65,536 原始规模因 600 秒
progress stall FAIL，不能列为 full-scale qualified。

## 一页结论

| 项目 | 结果 | 状态 |
|---|---:|---|
| 目标 1：等效追踪容量 | 102,656 / 65,536 = 156.64% | PASS |
| 目标 1：压力后附加成本 | +6.03 ns = 12.06 cycles @ 2 GHz，门限 50 cycles | PASS，需最新代码复跑 TC131 |
| 目标 2：`>500 ns` 适用场景平均时延降幅 | 54.32%，门限 10% | PASS |
| HA10 useful latency | 500.74 → 264.19 ns/op，-47.24% | PASS |
| HA10 useful throughput | 1.997 → 3.785 Mops/s，+89.54% | PASS |
| TC142-TC147 多拓扑 | 4 topology × 6 TC = 24/24 | PASS |
| C 组 2N1S 迁移 | 6/6 有独立源码/verifier；5 default smoke PASS，TC224 compact PASS | PARTIAL：TC224 full-scale FAIL |

汇报时必须同时说明：TC138 dirty-owner handoff 慢 12.12%，TC132 checkpoint
recover 慢 39.49%，HA06 admission 慢约 19.6%。这些是方案成本，不应隐藏。
