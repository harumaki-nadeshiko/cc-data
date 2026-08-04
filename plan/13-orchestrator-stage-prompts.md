# Orchestrator Stage Prompts

本文件提供主 agent（primary agent，承担 orchestrator 角色）可直接使用或轻改的阶段派单 prompt 样板。

使用原则:
- 每次只派发一个阶段
- 先 implementer，后 validator
- validator 未 PASS 不推进

## 1. 通用 implementer prompt 样板

```text
阶段: <STAGE>

请阅读以下文件:
- plan/00-terminology.md
- plan/01-current-state-and-requirements.md
- plan/02-external-proxy-spec.md
- plan/03-phase-plan.md
- plan/04-test-plan.md
- plan/07-stage-state-tables.md
- plan/08-file-modification-matrix.md
- plan/09-stage-execution-playbooks.md
- plan/11-failure-handling-and-plan-amendment.md
- docs/ubcc_docker_git_workflow.md

请执行:
1. 只实现本阶段最小必要代码与测试。
2. 遵守 HN 最小修改原则。
3. 若发现 EP_RNF 状态超出 HN 现有表达范围，先创建根目录 `OhNo_EP_RNF_NotGooOod.md` 再继续 fallback。
4. 使用 C++ test hook + Python trigger。
5. testcase 必须写清 inputs、injection method、observables、expected output、negative criteria。
6. build/test 在 Docker，commit 不在容器内进行。
7. 最后返回 implementer 阶段报告，状态只能是:
   - COMPLETED
   - INCOMPLETE
   - PLAN_DEFECT
   - STAGE_NOT_COMPLETED
```

## 2. 通用 validator prompt 样板

```text
请审查 <STAGE> 是否真正完成。

请阅读:
- plan/00-terminology.md
- plan/01-current-state-and-requirements.md
- plan/02-external-proxy-spec.md
- plan/03-phase-plan.md
- plan/04-test-plan.md
- plan/07-stage-state-tables.md
- plan/08-file-modification-matrix.md
- plan/09-stage-execution-playbooks.md
- plan/10-validator-checklists.md
- plan/11-failure-handling-and-plan-amendment.md

请重点检查:
1. 是否只做了表面接线而未完成真实协议语义
2. 是否满足本阶段状态转移表
3. 是否测试输入/注入/观测/预期输出都明确且真实执行
4. 是否违反 HN 最小修改原则
5. 是否维持 home UBCC metadata-only
6. 是否维持 HN 原生 RNF 目录格式承载 `EP_RNF`
7. 是否跑过本阶段测试与 `TC1..TC5`
8. 若 implementer 上报 PLAN_DEFECT / STAGE_NOT_COMPLETED，是否论证成立

请输出 validator 阶段报告，并包含:
- Verdict: PASS | FAIL | INCOMPLETE
- continue_to_next_stage: yes/no
- plan_amendment_required: yes/no
- resume_from_step: <text>
```

## 2.5 M3.5 implementer prompt

```text
当前阶段: M3.5 / Multi-agent Collaboration Smoke Check

唯一任务:
- 在仓库根目录 `readme.md` 新增一行:
  `Agent test 666!`

要求:
- 不做其他代码修改
- 完成后返回 implementer 阶段报告
```

## 2.6 M3.5 validator prompt

```text
当前阶段: M3.5 / Multi-agent Collaboration Smoke Check

唯一审查任务:
- 检查仓库根目录 `readme.md` 是否存在新增行:
  `Agent test 666!`

额外检查:
- orchestrator 是否先调用 implementer，再调用 validator
- `M3.5` PASS 后是否暂停等待用户确认

输出:
- Verdict: PASS | FAIL
- continue_to_next_stage: no
- resume_from_step: 等待用户确认后开始 T0
```

## 3. T0 implementer prompt

```text
当前阶段: T0 / Sync_Wait(node_mask)

目标:
- 实现只统计显式调用线程的 barrier syscall
- 支持按 node_mask 隔离
- 支持重复使用

最小交付:
- syscall 注册
- barrier manager
- `TC-T0-1 ~ TC-T0-4`

不做:
- timeout
- signal
- full-system 支持

重点返回:
- barrier 状态结构
- 为什么它只统计显式调用线程
- ARM workload 与测试结果
```

## 4. M4 implementer prompt

```text
当前阶段: M4 / Sentinel Registration

目标:
- 实现严格定义的 home-side sentinel registration
- 在 HN 原生目录格式中承载 `EP_RNF`
- 本地冲突请求会真实 snoop `EP_RNF`

最小交付:
- HN/dir inspection API
- sentinel install/remove test hook
- `S_SHARER`
- `TC-M4-1` 到 `TC-M4-5`

关键约束:
- sentinel registration 必须在 remote grant 对 requester 可见前完成
- 不允许造平行 sentinel shadow 目录结构

重点返回:
- `EP_RNF` 在 HN 原生目录中的承载方式
- install/remove 时机
- 是否需要 `OhNo_EP_RNF_NotGooOod.md`
```

## 5. M5 implementer prompt

```text
当前阶段: M5 / Remote Miss With Permission Sideband

目标:
- 在 HN -> EP_SNF 路径传递 `needed_perm + write_intent`
- 让 home UBCC 基于 MESI 区分 `GrantShared/Exclusive/Modified`

最小交付:
- `CHIRequestMsg` 扩展字段
- sideband inspection API
- `GlobalGrantShared` 与 `GlobalGrantExclusive` 至少分离
- `TC-M5-1/2/3/4/7/8`

关键约束:
- sideband 来源是 HN 上层请求语义
- 不得增加 `src/home` 冗余字段
- 不得把 `E/M` 混成一个 owner grant

重点返回:
- 字段定义
- sideband 填充点
- grant decision 表达方式
```

## 6. M6 implementer prompt

```text
当前阶段: M6 / UBCC Directory + EP_RNF Local Coherent Access

目标:
- 实现 `G_S/G_E/G_M` 目录状态
- 实现 `GlobalRecallOwner`
- 实现 `EP_RNF` 延迟响应 HN

最小交付:
- `DirEntry` inspection API
- `GlobalRecallOwner` 主路径
- `TC-M6-1` 到 `TC-M6-5`

关键约束:
- remote read dirty line 必须经过 recall 拿到最新值
- home UBCC 仍 metadata-only
- `EP_RNF` 不得提前响应 HN

重点返回:
- `DirEntry` 结构
- recall 数据路径
- 延迟响应实现方式
```

## 7. M7 implementer prompt

```text
当前阶段: M7 / Writeback / Evict / Owner Transfer

目标:
- 实现 dirty writeback
- 实现 clean evict
- 实现 owner transfer
- 引入 epoch/stale 过滤

最小交付:
- writeback/evict/owner transfer 主路径
- epoch inspection API
- `TC-M7-1` 到 `TC-M7-6`

关键约束:
- 任意时刻最多一个 global owner
- stale 响应不得污染当前事务
- recall 结果必须按 read/write 分裂

重点返回:
- writeback/evict 状态更新
- owner transfer 规则
- epoch/stale 过滤逻辑
```

## 8. orchestrator 中断后恢复 prompt

```text
检测到执行曾中断。请先阅读:
- plan/06-checkpoint-and-resume.md
- plan/checkpoints/ 下时间戳最新的 checkpoint 文档

请先不要直接开始新阶段。

先做:
1. 总结 checkpoint 记录的当前阶段与剩余任务
2. 从 `resume_from_step` 指定的位置恢复
3. 完成当前阶段后，再触发 validator
4. validator PASS 前不得推进到下一阶段
```

## 9. PLAN_DEFECT 审查 prompt

```text
implementer 上报了 PLAN_DEFECT。

请重点审查:
1. 这是否真的是计划缺陷，而不是实现未完成
2. implementer 给出的反例、状态冲突或文件落点是否成立
3. 若确属计划缺陷，应修改哪份 `plan/` 文档
4. 修订计划后当前阶段应从哪一步重新开始

输出:
- plan_amendment_required: yes/no
- affected_plan_files:
- resume_from_step:
```
