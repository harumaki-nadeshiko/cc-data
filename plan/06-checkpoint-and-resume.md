# Checkpoint And Resume

## 1. 目标

本文件定义当 `coder-validator-orchestrator` 在执行阶段任务时，因为 API 限额、reviewer 调用失败或其他外部额度问题而无法继续推进时，如何安全停止、落盘当前进度，并在下一次执行时恢复。

## 2. 何时必须创建检查点文档

出现以下任一情况时，orchestrator 必须停止当前推进，并创建新的检查点文档:
- `strict-task-completion-reviewer` 因 API 限额/额度失败，未能完成审核
- `coder-validator-orchestrator` 自身因 API 限额/额度无法继续执行
- implementer/reviewer 的结果已部分返回，但当前轮无法继续完成完整闭环

说明:
- 此时不得假设阶段通过
- 不得推进到下一阶段
- 必须保留“当前做到哪里、还差什么、下次从哪继续”的书面状态

## 3. 检查点文件位置与命名

检查点文档放在:
- `plan/checkpoints/`

建议命名格式:
- `plan/checkpoints/<YYYYMMDD-HHMMSS>-<stage>-checkpoint.md`

例子:
- `plan/checkpoints/20260526-231500-M5-checkpoint.md`

## 4. 检查点文档必填字段

每个检查点文档至少必须包含以下部分:

### 4.1 Header

- `Stage`
- `Timestamp`
- `Trigger`
- `Verdict`

其中:
- `Trigger` 应明确写为例如 `reviewer_api_limit`, `orchestrator_api_limit`
- `Verdict` 在中断时通常应为 `INCOMPLETE`

### 4.2 Current Summary

必须总结:
- 当前阶段目标
- 当前已经完成的子任务
- 当前尚未完成的子任务

### 4.3 Code And Test State

必须记录:
- 当前已修改文件列表
- 当前已新增/修改 testcase
- 当前构建状态
- 当前已通过的测试
- 当前未执行或失败的测试

若涉及 `gem5` submodule，还必须记录:
- submodule 内是否已有 commit
- submodule commit hash
- 主仓是否已更新 submodule 指针

### 4.4 Reviewer State

必须记录:
- reviewer 是否已开始
- reviewer 做到了哪一步
- reviewer 未完成的原因是否为 API 限额

### 4.5 Resume Point

必须明确写出:
- 下次从哪一步继续
- 首个应执行的具体动作
- 若需先运行哪个测试或读取哪个文件，也必须写明

### 4.6 Remaining Work

必须列出:
- 当前阶段剩余任务
- 后续尚未开始的阶段与任务

## 5. 检查点文档模板

推荐模板:

```md
# <STAGE> Checkpoint

- Stage: <T0/M4/M5/M6/M7>
- Timestamp: <YYYY-MM-DD HH:MM:SS>
- Trigger: <reviewer_api_limit / orchestrator_api_limit / ...>
- Verdict: INCOMPLETE

## Current Summary

- Stage goal:
- Completed in this run:
- Still incomplete:

## Code And Test State

- Modified files:
- New/updated tests:
- Build status:
- Passed tests:
- Not run / failed tests:

### Submodule State

- gem5 submodule changed: <yes/no>
- gem5 committed: <yes/no>
- gem5 commit hash:
- superproject submodule pointer updated: <yes/no>

## Reviewer State

- Reviewer started: <yes/no>
- Reviewer completed: <yes/no>
- Failure reason:
- Pending review scope:

## Resume Point

- Next exact step:
- Next exact command or task to run:
- Files to read first:

## Remaining Work

- Remaining tasks in current stage:
- Next stages not yet started:
```

## 6. orchestrator 的中断处理规则

当 reviewer 因 API 限额失败时，orchestrator 必须按顺序执行:
1. 立即停止当前阶段推进
2. 不得宣布当前阶段 PASS
3. 生成新的检查点文档
4. 在文档中记录当前已完成工作、剩余工作、resume point
5. 向用户返回该检查点文档路径

## 7. 下一次执行如何恢复

下一次执行时，orchestrator 应按如下顺序:
1. 读取 `plan/00-plan-index.md`
2. 读取最新的 `plan/checkpoints/*.md`
3. 优先根据最新检查点文档中的 `Resume Point` 恢复
4. 完成当前阶段剩余任务
5. 重新触发 reviewer
6. 只有 reviewer PASS 后才允许继续到下一阶段

## 8. 与阶段计划的关系

- 检查点文档不是替代阶段计划
- 它只是阶段执行时的临时运行态快照
- 恢复时仍要遵守 `plan/01` 到 `plan/05` 的正式规则
