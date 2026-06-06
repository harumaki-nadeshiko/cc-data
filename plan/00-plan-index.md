# UBCC Plan Index

状态: 当前主计划入口

推荐精简入口:
- `plan/14-orchestrator-quickstart.md`

外部必读工作流文档:
- `docs/ubcc_docker_git_workflow.md`

高速互联设计方案:
- `docs/high-speed-interconnect-design.md`（UBCC 节点间互联从慢速升级为高速后的全栈调整方案）

建议 Agent 阅读顺序:
1. `plan/14-orchestrator-quickstart.md`
2. `plan/00-terminology.md`
3. `plan/01-current-state-and-requirements.md`
4. `plan/02-external-proxy-spec.md`
5. `plan/03-phase-plan.md`
6. `plan/04-test-plan.md`
7. `plan/05-agent-orchestration.md`
8. `plan/06-checkpoint-and-resume.md`
9. `plan/07-stage-state-tables.md`
10. `plan/08-file-modification-matrix.md`
11. `plan/09-stage-execution-playbooks.md`
12. `plan/10-validator-checklists.md`
13. `plan/11-failure-handling-and-plan-amendment.md`
14. `plan/12-stage-report-templates.md`
15. `plan/13-orchestrator-stage-prompts.md`

文件职责:

| 文件 | 用途 |
|---|---|---|
| `00-terminology.md` | 统一术语，特别是 sentinel registration 的严格定义 |
| `01-current-state-and-requirements.md` | 当前完成度、未完成项、硬约束、用户要求 |
| `02-external-proxy-spec.md` | External Proxy 架构、状态、请求转换、关键不变量 |
| `03-phase-plan.md` | `M3.5 + T0 + M4..M9` 的开发阶段计划 |
| `04-test-plan.md` | 回归基线与各阶段 TestCase 细化 |
| `05-agent-orchestration.md` | orchestrator / implementer / reviewer 的协作规则 |
| `06-checkpoint-and-resume.md` | API 限额/中断时的检查点落盘与恢复规则 |
| `07-stage-state-tables.md` | `M4 ~ M7` 的状态转移表与非法状态定义 |
| `08-file-modification-matrix.md` | `M3.5 + T0 ~ M7` 的建议修改文件清单与 reviewer 焦点 |
| `09-stage-execution-playbooks.md` | `M3.5 + T0 ~ M7` 的阶段内执行顺序与最小可交付 diff |
| `10-validator-checklists.md` | validator 的逐项审查清单与输出格式 |
| `11-failure-handling-and-plan-amendment.md` | 未完成/计划缺陷/阶段未完成时的处理机制 |
| `12-stage-report-templates.md` | implementer/validator/orchestrator 的阶段报告模板 |
| `13-orchestrator-stage-prompts.md` | `M3.5 + T0 ~ M7` 的标准派单 prompt 样板 |
| `14-orchestrator-quickstart.md` | orchestrator 的精简执行入口与当前主线摘要 |
| `docs/high-speed-interconnect-design.md` | UBCC 互联高速化后的全栈调整方案 |
| `15-fix-to-pass-tests.md` | TC2~TC6 通过的修复计划（TBE 断言 + pendingOp 参数化 + retry queue 优化） |

## 文档分层

### A. 权威基线
- `14-orchestrator-quickstart.md`
- `00-terminology.md`
- `01-current-state-and-requirements.md`
- `02-external-proxy-spec.md`
- `03-phase-plan.md`
- `04-test-plan.md`
- `05-agent-orchestration.md`

### B. 执行细化材料
- `07-stage-state-tables.md`
- `08-file-modification-matrix.md`
- `09-stage-execution-playbooks.md`
- `10-validator-checklists.md`
- `11-failure-handling-and-plan-amendment.md`
- `12-stage-report-templates.md`
- `13-orchestrator-stage-prompts.md`

### C. 中断恢复材料
- `06-checkpoint-and-resume.md`
- `plan/checkpoints/`

### D. 历史草稿
- `ubcc-detailed-phased-plan-v0.1.md`

说明:
- `plan/ubcc-detailed-phased-plan-v0.1.md` 保留为第一轮单文件草稿。
- 当前主计划已成熟到可供 orchestrator 直接驱动 `M3.5 ~ M7`。
- 当前主目标阶段是 `M3.5`、`M4 ~ M7`；`M8 ~ M9` 为可选后续阶段。
- `plan/checkpoints/` 用于保存中断时的阶段检查点文档。
