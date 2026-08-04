# Orchestrator Quickstart

本文件是主 agent（primary agent，承担 orchestrator 角色）的精简启动入口。

如果你是 orchestrator，本文件优先级最高；它告诉你现在该做什么、先读什么、在哪些条件下必须停下。

## 1. 当前主任务范围

当前主交付范围:
- `M3.5`
- `T0`
- `M4`
- `M5`
- `M6`
- `M7`

当前不是主承诺范围:
- `M8`
- `M9`

默认规则:
- `M3.5` PASS 后必须暂停等待用户确认，再开始 `T0`。
- 完成并通过 `M7` 后即可停下，等待用户确认是否继续可选阶段。

## 2. 你必须先读哪些文件

最小必读集合:
1. `plan/00-terminology.md`
2. `plan/01-current-state-and-requirements.md`
3. `plan/02-external-proxy-spec.md`
4. `plan/03-phase-plan.md`
5. `plan/04-test-plan.md`
6. `plan/05-agent-orchestration.md`
7. `plan/07-stage-state-tables.md`
8. `plan/08-file-modification-matrix.md`
9. `plan/09-stage-execution-playbooks.md`
10. `plan/10-validator-checklists.md`
11. `plan/11-failure-handling-and-plan-amendment.md`
12. `docs/ubcc_docker_git_workflow.md`

若存在中断检查点，还必须读:
- `plan/06-checkpoint-and-resume.md`
- `plan/checkpoints/` 下最新 checkpoint 文档

如果需要生成报告或派发更标准化 prompt，再读:
- `plan/12-stage-report-templates.md`
- `plan/13-orchestrator-stage-prompts.md`

## 3. 你启动时要先做什么

### 3.1 如果没有 checkpoint

按顺序执行:
1. 确认当前目标阶段
2. 给 implementer 派发当前阶段
3. 收 implementer 结果
4. 给 validator 派发审核
5. 只有 validator PASS 才推进下一阶段

### 3.2 如果有 checkpoint

按顺序执行:
1. 读取最新 checkpoint
2. 识别 `resume_from_step`
3. 恢复当前阶段
4. 完成当前阶段后重新触发 validator
5. validator PASS 前不得推进

## 4. 当前不可违反的硬规则

- 不缩规模，固定 `N=3, L=2, D=2`
- ordinary CHI 不跨 node
- `EP_RNF` 优先使用 HN 原生 RNF 目录格式承载
- home UBCC 是 metadata-only，不保存长期 line data
- home UBCC 必须使用 `MESI`，显式区分 `E` 与 `M`
- remote grant 对 requester 可见前，home-side sentinel registration 必须完成
- `HN -> EP_SNF` sideband 最小集合是:
  - `ubcc_needed_perm`
  - `ubcc_write_intent`
- test hook 采用折中方案:
  - `M4` 可少量强注入
  - `M5~M7` 以路径驱动为主
- helper 风格优先 `C++ test hook + Python trigger`
- 主观测来源优先内部状态接口

## 5. 你何时必须停下

出现以下任一情况必须停止推进:

1. validator 未 PASS
2. implementer 上报 `INCOMPLETE`
3. implementer 上报 `PLAN_DEFECT`
4. implementer 上报 `STAGE_NOT_COMPLETED`
5. reviewer/validator 因 API 限额失败
6. orchestrator 自身遭遇 API 限额，无法安全完成当前轮

此时必须:
1. 不推进下一阶段
2. 创建 checkpoint 文档
3. 明确记录当前做到哪里、还差什么、从哪恢复

## 6. 你如何判断计划是否成熟到可执行

对 `M3.5 ~ M7`，当前计划已经具备:
- 术语定义
- 阶段目标
- testcase 规格
- 状态转移表
- 文件修改矩阵
- playbook
- validator checklist
- 中断恢复机制
- failure handling

因此:
- 当前计划已足够支撑 orchestrator 开始按阶段派发 `M3.5 ~ M7`

仍属实现时再确认的细节:
- 某些 test hook 的最终 API 名称
- 某些 SLICC hook 具体插入点
- requester 侧是否需要显式细分 `R_E` 与 `R_Md`

这些不阻塞当前计划执行，但若 implementer 认为已构成计划缺陷，必须按 `PLAN_DEFECT` 流程上报。

## 7. 当前推荐启动顺序

如果现在立刻启动主线开发，推荐顺序是:
1. `M3.5`
2. `T0`
3. `M4`
4. `M5`
5. `M6`
6. `M7`

特殊规则:
- `M3.5` PASS 后必须暂停，等待用户确认，再开始 `T0`

## 8. 对用户的最小回报格式

每阶段结束后，建议向用户最少返回:
- 当前阶段
- implementer 状态
- validator verdict
- 是否推进到下一阶段
- 若未推进，checkpoint 路径或 `resume_from_step`
