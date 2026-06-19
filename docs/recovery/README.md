# docs/recovery 文档索引（2026-06-19 清理版）

本目录已按“当前执行基线 / 主题设计 / 历史决策与修复日志”重组，目标是：

1. 减少重复文档；
2. 只保留仍有决策价值的内容；
3. 让实现与验证入口清晰可检索。

---

## 1) 当前执行基线（优先阅读）

- `scheme_v4.md`：协议总基线（权威实现语义）
- `drift_in_progress.md`：与基线不一致的最新偏离记录（唯一追加日志）
- `ubcc_implementation_plan.md`：当前可执行实施计划
- `verification_plan.md`：统一后的验证与 RAS 计划（含形式化部分）
- `migration_plan.md`：独立化迁移总方案（含 C2C 演进定位）

## 2) 主题设计（按需阅读）

- `system_topology.md`
- `message_passing_refactor_plan.md`
- `ubcc_directory_offload_design.md`
- `new_testcase_design.md`
- `tc45_protocol_primitives.md`
- `offline_analysis_report.md`

## 3) 历史决策与上下文（回溯时阅读）

- `decisions.md`

## 4) 历史协议专题（保留用于根因追溯）

以下文档存在一定历史性，但仍包含可用于回放问题链路的细粒度分析，暂保留：

- `error_root_cause_v4.md`
- `gap_analysis_and_fix_plan.md`
- `local_dsm_routing_v4.md`
- `recall_spec_v4.md`
- `recall_done_fix.md`
- `upgrade_invalidate_fix.md`
- `snp_shared_fix_plan.md`
- `workflow_guide.md`

---

## 维护规则

- 若实现偏离 `scheme_v4.md`，必须先更新 `drift_in_progress.md`。
- 新文档优先并入现有主文档（verification/migration/implementation），避免再新增平行“vN 规范”。
- 若文档仅用于一次性流程或提示词，应放临时目录，不进入 `docs/recovery/` 常驻。
