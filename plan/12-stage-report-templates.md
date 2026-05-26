# Stage Report Templates

本文件提供 `T0` 与 `M4 ~ M7` 的阶段报告模板，供 implementer、validator、orchestrator 统一产出风格。

## 1. 通用要求

每个阶段报告必须做到:
- 诚实区分“已完成”和“未完成”
- 明确列出实际改动文件
- 明确列出实际执行命令与结果
- 明确列出测试覆盖与未覆盖边界
- 若涉及 `gem5`，明确记录 submodule commit 状态

## 2. implementer 阶段报告模板

```md
# <STAGE> Implementation Report

- Stage: <T0/M4/M5/M6/M7>
- Status: COMPLETED | INCOMPLETE | PLAN_DEFECT | STAGE_NOT_COMPLETED

## Goal
- ...

## Completed Work
- ...

## Incomplete Work
- ...

## Modified Files
- `...`

## Test Hooks / Inspection APIs
- `...`

## Tests

### Stage Tests
- command:
- result:

### Regression Tests
- command:
- result:

## Key Results
- ...

## Known Gaps
- ...

## If PLAN_DEFECT
- Claimed defect:
- Evidence:
- Proposed plan change:

## Submodule State
- gem5 submodule changed: yes/no
- gem5 submodule committed: yes/no
- gem5 commit hash:
- superproject pointer updated: yes/no
```

## 3. validator 阶段报告模板

```md
# <STAGE> Validation Report

- Stage: <T0/M4/M5/M6/M7>
- Verdict: PASS | FAIL | INCOMPLETE
- continue_to_next_stage: yes/no
- plan_amendment_required: yes/no
- resume_from_step: <text>

## Scope Reviewed
- ...

## Checklist Results
- [PASS/FAIL] ...

## Findings
- ...

## Must Fix
- ...

## Optional Suggestions
- ...

## Decision Rationale
- ...
```

## 4. orchestrator 阶段摘要模板

```md
# <STAGE> Orchestrator Summary

- Stage: <T0/M4/M5/M6/M7>
- implementer_status: <...>
- validator_verdict: <...>
- proceed_to_next_stage: yes/no

## Implementation Summary
- ...

## Validation Summary
- ...

## Current Decision
- ...

## Next Step
- ...

## If Stopped
- checkpoint path:
```

## 5. Stage-Specific Required Sections

### 5.0 M3.5

必须额外写:
- `readme.md` 修改结果
- validator 对该行的确认结果
- 是否已暂停等待用户确认

### 5.1 T0

必须额外写:
- barrier 计数语义
- `node_mask` 处理语义
- 可重复使用验证结果

### 5.2 M4

必须额外写:
- `EP_RNF` 在 HN 原生目录中的承载方式
- sentinel install 时机
- 是否出现 `OhNo_EP_RNF_NotGooOod.md`

### 5.3 M5

必须额外写:
- `ubcc_needed_perm`
- `ubcc_write_intent`
- `GrantShared/Exclusive/Modified` 决策规则

### 5.4 M6

必须额外写:
- `GlobalRecallOwner` 路径
- `EP_RNF` 延迟响应行为
- home UBCC metadata-only 保持情况

### 5.5 M7

必须额外写:
- writeback/evict/owner transfer 行为
- epoch/stale 过滤结果
- single-owner invariant 验证结果

## 6. 报告文件建议位置

推荐放在:
- `reports/stage-<stage>-implementation-<n>.md`
- `reports/stage-<stage>-validation-<n>.md`
- `reports/stage-<stage>-orchestrator-<n>.md`

若是中断检查点，则仍放到:
- `plan/checkpoints/`
