# Failure Handling And Plan Amendment

本文件定义 implementer 在执行过程中遇到困难时的上报格式，以及 validator/orchestrator 如何处理。

## 1. 目标

确保实现困难被诚实记录，而不是被伪装成完成。

## 2. implementer 可上报的结果状态

implementer 对某阶段只能上报以下 4 种状态之一:

### 2.1 `COMPLETED`

含义:
- 认为本阶段已完成
- 已有代码、测试和回归结果支撑

### 2.2 `INCOMPLETE`

含义:
- 已有部分结果
- 但尚不足以宣称本阶段完成
- 需要 validator 审查并给出修改建议

### 2.3 `PLAN_DEFECT`

含义:
- implementer 认为无法完成的根本原因在于原计划存在缺陷、遗漏或错误前提
- 必须给出论据与建议修订点

### 2.4 `STAGE_NOT_COMPLETED`

含义:
- implementer 认为当前阶段在本轮内实在无法完成
- 需要 validator 判断是否允许继续后续阶段，或者必须停在当前阶段

## 3. implementer 上报模板

```md
# <STAGE> Implementer Result

- Status: COMPLETED | INCOMPLETE | PLAN_DEFECT | STAGE_NOT_COMPLETED

## Summary
- What was attempted:
- What was finished:
- What is still missing:

## Evidence
- Modified files:
- Tests run:
- Results:

## Blockers
- ...

## If PLAN_DEFECT
- Claimed plan defect:
- Why current plan is insufficient:
- Proposed amendment:

## Suggested Next Step
- ...
```

## 4. validator 对这四种状态的处理

### 4.1 `COMPLETED`

- 按正常 checklist 严审
- 只有 validator PASS，才算阶段真正完成

### 4.2 `INCOMPLETE`

- validator 必须审查当前部分结果
- 输出明确修改建议
- orchestrator 应将修改建议回派 implementer

### 4.3 `PLAN_DEFECT`

- validator 必须审查“是否真的是计划缺陷，而不是实现没做完”
- 若论据成立:
  - 先修改计划
  - 再重新派发本阶段
- 若论据不成立:
  - 按 `INCOMPLETE` 处理

### 4.4 `STAGE_NOT_COMPLETED`

- validator 必须决定:
  - `continue_later_stages = yes/no`
  - 默认 `no`
- 若允许继续后续阶段，必须明确写出为什么当前未完成不会污染后续阶段正确性
- 若不允许继续，必须给出恢复点

## 5. orchestrator 的决策规则

### 5.1 默认规则

- 没有 validator PASS，不推进下一阶段

### 5.2 遇到 `INCOMPLETE`

- 回派 implementer 修复
- 不推进下一阶段

### 5.3 遇到 `PLAN_DEFECT`

- 先让 validator 审查是否确属计划缺陷
- 若 validator 同意:
  - 先修订计划文档
  - 生成 checkpoint
  - 再重新开始当前阶段

### 5.4 遇到 `STAGE_NOT_COMPLETED`

- 由 validator 决定是否允许继续后续阶段
- 默认不允许
- 若 validator 允许继续，orchestrator 必须在阶段摘要和 checkpoint 中明确记录该例外决定

## 6. checkpoint 与失败状态的关系

当 implementer 上报以下任一状态时，建议同时写 checkpoint:
- `INCOMPLETE`
- `PLAN_DEFECT`
- `STAGE_NOT_COMPLETED`

若同时又遭遇 API 限额，则 checkpoint 为强制要求。

## 7. reviewer/validator 输出中的继续决策字段

validator 输出必须包含:
- `verdict: PASS | FAIL | INCOMPLETE`
- `plan_amendment_required: yes/no`
- `continue_to_next_stage: yes/no`
- `resume_from_step: <text>`

## 8. 计划修订纪律

若发生 `PLAN_DEFECT` 并被 validator 认可:
- 必须先更新 `plan/` 正式文档
- 再继续实施
- 不允许“先按新理解改代码，之后再补计划”
