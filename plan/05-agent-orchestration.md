# Agent Orchestration

## 1. 目标

本文件指导主 agent（primary agent，承担 `coder-validator-orchestrator` 角色）如何读取计划书，并把每个阶段的工作分派给:
- `intelligent-agent`（实现，复杂任务档）
- `high-intelligent-agent`（审核，架构级档）

Agent 均为 mode=all 全权限执行 agent，仅在模型/推理档位上区分：
- `futsu-agent` / `medium-agent`：普通任务（低成本 / 常规）
- `intelligent-agent`：复杂任务（默认实现主力）
- `high-intelligent-agent`：高度复杂、架构级任务（默认审核主力）
- `hitomi-agent`：多模态任务（图像/视觉内容）
- `xhigh-intelligent-agent`：极复杂任务，仅用户明确指定时使用

目标不是“尽快推进阶段”，而是“每阶段真实完成后才推进”。

## 2. Agent 职责

### 2.1 主 agent（primary agent，orchestrator 角色）

职责:
- 读取 `plan/00-plan-index.md` 到 `plan/05-agent-orchestration.md`
- 选择当前阶段
- 给实现 agent 派发本阶段实现任务
- 给审核 agent 派发本阶段审核任务
- 根据审核 verdict 决定推进、回退或补修

输出:
- 当前阶段推进状态
- 需要实现 agent 修复的问题列表
- 下一阶段是否允许开始

### 2.2 `intelligent-agent`（实现 agent，对应原 `cache-coherence-implementer`）

职责:
- 按阶段实现最小必要代码
- 增加或修改对应 testcase
- 构建并执行本阶段测试
- 跑 `TC1..TC5` 回归
- 返回实际改动、实际命令、实际结果
- 遵循 `docs/ubcc_docker_git_workflow.md` 的 Docker/宿主机分工

禁止:
- 抢跑后续阶段大机制
- 用 bypass 或 fake pass 伪造完成
- 在未明确说明的情况下大改 HN 状态机
- 静默引入超出 HN 现有表达范围的 `EP_RNF` 特殊状态而不告警

### 2.3 `high-intelligent-agent`（审核 agent，对应原 `strict-task-completion-reviewer`）

职责:
- 按阶段目标、任务、出口标准做严格审核
- 核对代码、测试和实际结果是否匹配
- 查缺少的边界条件、遗漏的 testcase、伪测试风险
- 给出 PASS / FAIL verdict

输出:
- 审核结论
- 必改问题列表
- 可选优化项列表

## 3. orchestrator 固定流程

对每个阶段按如下循环执行:

```text
for stage in [M3.5, T0, M4, M5, M6, M7, M8, M9]:
    loop:
        1. 读取本阶段对应计划章节
        2. 派发 implementer 完成代码 + 测试 + 回归
        3. 收集 implementer 的改动摘要与测试结果
        4. 派发 reviewer 审核该阶段是否真正完成
        5. 如果 reviewer = PASS:
              记录阶段完成；若 stage=M3.5，则暂停等待用户确认，否则进入下一阶段
            否则:
              将问题列表回派给 implementer 修复
              继续循环
```

## 4. orchestrator 读取规则

每阶段最少必须读取:
- `plan/00-terminology.md`
- `plan/01-current-state-and-requirements.md`
- `plan/02-external-proxy-spec.md`
- `plan/03-phase-plan.md`
- `plan/04-test-plan.md`
- `docs/ubcc_docker_git_workflow.md`

若存在最新检查点文档，还必须读取:
- `plan/06-checkpoint-and-resume.md`
- `plan/checkpoints/*.md` 中时间戳最新的文件

建议同时读取:
- `plan/09-stage-execution-playbooks.md`
- `plan/10-validator-checklists.md`
- `plan/11-failure-handling-and-plan-amendment.md`
- `plan/12-stage-report-templates.md`
- `plan/13-orchestrator-stage-prompts.md`

读取重点:
- 术语严格定义，尤其是 `sentinel registration`
- 当前阶段目标
- 当前阶段不做的事
- 当前阶段出口标准
- 当前阶段 testcase
- HN 最小修改原则
- submodule 提交纪律
- `EP_RNF` 是否仍使用 HN 原生 RNF 目录格式表达
- home UBCC 是否严格区分 `E` 与 `M`
- 是否存在中断检查点与 resume point
- 当前阶段 playbook 的任务顺序
- validator checklist
- failure handling 规则
- 阶段报告模板
- 当前阶段派单 prompt 样板

## 5. orchestrator 给 implementer 的任务模板

推荐模板（orchestrator 在调用 `task` 工具的 `prompt` 参数中使用）:

```text
阶段: <M3.5/T0/M4/M5/M6/M7>

请严格按以下计划执行。必读文件:
- plan/00-terminology.md
- plan/01-current-state-and-requirements.md
- plan/02-external-proxy-spec.md
- plan/03-phase-plan.md 中本阶段相关章节
- plan/04-test-plan.md 中本阶段相关章节
- plan/07-stage-state-tables.md
- plan/08-file-modification-matrix.md
- plan/09-stage-execution-playbooks.md
- plan/11-failure-handling-and-plan-amendment.md
- docs/ubcc_docker_git_workflow.md

约束:
1. 只实现本阶段所需最小代码。
2. 优先遵守 HN 最小修改原则；除非证明确实不够，否则不要大改 HN 状态机。
3. 使用 C++ test hook + Python trigger。
4. testcase 规格必须明确写出 inputs、injection method、observables、expected output、negative criteria。
5. build/test 在 Docker 容器内执行，commit 不在容器内进行。
6. 若发现 `EP_RNF` 状态超出 HN-F 可表达范围，先新增根目录 `OhNo_EP_RNF_NotGooOod.md` 再继续 fallback。

完成后返回 implementer 阶段报告，包含:
- 修改文件列表
- 关键实现说明
- 实际命令
- 实际结果
- 仍未覆盖的风险
- 使用了哪些 C++ test hook，以及 Python 如何触发它们
- 当前阶段状态: `COMPLETED | INCOMPLETE | PLAN_DEFECT | STAGE_NOT_COMPLETED`
```

## 6. orchestrator 给 reviewer 的任务模板

推荐模板（orchestrator 在调用 `task` 工具的 `prompt` 参数中使用）:

```text
请严格审查 <M3.5/T0/M4/M5/M6/M7> 是否真正完成。

必读文件:
- plan/00-terminology.md
- plan/01-current-state-and-requirements.md
- plan/02-external-proxy-spec.md
- plan/03-phase-plan.md 中本阶段章节
- plan/04-test-plan.md 中本阶段 testcase
- plan/07-stage-state-tables.md
- plan/08-file-modification-matrix.md
- plan/09-stage-execution-playbooks.md
- plan/10-validator-checklists.md
- plan/11-failure-handling-and-plan-amendment.md

Implementer 已完成的工作:
[在此处描述 implementer 的输出]

请重点检查:
1. 是否只做了表面接线而未完成真实协议语义
2. 是否满足本阶段状态转移表
3. 是否测试输入/注入/观测/预期输出都明确且真实执行
4. 是否违反 HN 最小修改原则
5. 是否维持 home UBCC metadata-only
6. 是否维持 HN 原生 RNF 目录格式承载 `EP_RNF`
7. 是否跑过本阶段测试与 `TC1..TC5`
8. 若 implementer 上报 PLAN_DEFECT / STAGE_NOT_COMPLETED，是否论证成立

请输出 validator 阶段报告，包含:
- Verdict: PASS | FAIL | INCOMPLETE
- continue_to_next_stage: yes/no
- plan_amendment_required: yes/no
- resume_from_step: <text>
```

## 6.1 API 限额中断规则

若 reviewer 因 API 限额或额度问题失败:
1. orchestrator 立即停止当前阶段推进
2. 不得宣布当前阶段 PASS
3. 必须按 `plan/06-checkpoint-and-resume.md` 生成新的检查点文档
4. 必须向用户报告该 checkpoint 路径

若 orchestrator 自身在阶段执行中接近或遭遇 API 限额，也应执行同样规则。

## 6.2 implementer 困难上报规则

若 implementer 返回:
- `INCOMPLETE`
- `PLAN_DEFECT`
- `STAGE_NOT_COMPLETED`

则 orchestrator 必须按 `plan/11-failure-handling-and-plan-amendment.md` 处理，不得把它们等价当成普通 FAIL 一笔带过。

## 7. 阶段推进判定规则

只有满足全部条件才允许进入下一阶段:
- implementer 完成了本阶段目标内的代码和 testcase
- 本阶段 testcase 全部通过
- `TC1..TC5` 回归全部通过
- reviewer 明确给出 PASS

任一条件不满足时:
- orchestrator 不得推进
- 必须先把 reviewer 的 FAIL 问题回派 implementer 修复

## 7.0 M3.5 特殊推进规则

- `M3.5` 是多 Agent 协作冒烟阶段。
- validator PASS 后，orchestrator 不得自动进入 `T0` 或 `M4`。
- orchestrator 必须暂停当前对话，等待用户明确确认，之后才能继续后续阶段。

若失败原因是 API 限额而不是实现缺陷:
- 也不得推进
- 但必须改为先落盘 checkpoint，再等待下一次执行恢复

## 7.1 当前主目标范围

- 当前必须推进到的主目标为 `M3.5`、`T0` 与 `M4 ~ M7`。
- `M8 ~ M9` 当前属于可选后续阶段。
- orchestrator 在 `M7` 完成并 reviewer PASS 后，可以先停止主线推进，等待用户确认是否继续做 `M8 ~ M9`。

## 7.2 Docker 与 submodule 提交纪律

implementer 与 orchestrator 必须遵守:
1. 构建与测试在 Docker 容器内执行。
2. commit/push 在宿主机执行。
3. 若修改了 `gem5/`:
   - 必须先在 `gem5/` 子模块内单独提交
   - 再回到主仓提交 submodule 指针更新与其他文件
4. 主仓阶段提交中必须附带清晰的 gem5 变更摘要，至少说明:
   - 修改了哪些 `gem5` 文件
   - 各文件修改目的
5. reviewer 必须检查是否真的存在 submodule commit，而不是只有主仓指针变化或工作树脏状态。

建议主仓可见性做法:
1. 主仓阶段报告中列出本轮 `gem5` 修改文件清单。
2. 审查时使用 `git diff --submodule=diff` 或等价方式核对主仓引用的 submodule 变更。

## 8. 各阶段派发重点

### `M3.5`

implementer 重点:
- 仅修改根目录 `readme.md`
- 新增一行 `Agent test 666!`

reviewer 重点:
- `readme.md` 是否确实存在该新增行
- orchestrator 是否先调用 implementer，再调用 reviewer
- `M3.5` 通过后是否暂停等待用户确认

### `T0`

implementer 重点:
- syscall 注册
- barrier 状态对象
- 可重复使用 testcase

reviewer 重点:
- barrier 是否真的阻塞/释放
- 是否存在 stale state

### `M4`

implementer 重点:
- sentinel insert/update/remove
- `S_SHARER/S_OWNER/S_PENDING`
- 非 DSM 防护

reviewer 重点:
- HN 是否真的 snoop `EP_RNF`
- 是否错误声明完成但没有真实 directory 语义

### `M5`

implementer 重点:
- `needed_perm` sideband
- `GlobalReadShared/Unique`
- requester first miss closure

reviewer 重点:
- 是否真的把 `Shared/Unique` 从 HN 传到了 `EP_SNF`
- 是否仍偷偷依赖默认 `GrantM`

### `M6`

implementer 重点:
- global directory
- `GlobalRecallOwner`
- `EP_RNF` 延迟响应 HN

reviewer 重点:
- remote read dirty line 是否返回最新数据
- 是否存在 owner/sharer 不一致

### `M7`

implementer 重点:
- writeback/evict
- owner transfer
- epoch/stale 防护

reviewer 重点:
- 任意时刻是否仍最多一个 owner
- stale data 是否可能错误落地

### `M8`

implementer 重点:
- shared 默认路径
- multi-sharer
- upgrade/invalidate closure

reviewer 重点:
- 是否真正恢复 read-sharing
- 是否只是保留 debug `GrantM` 路径冒充完成

### `M9`

implementer 重点:
- outer ABI 文档化
- metadata model 解耦

reviewer 重点:
- 新增模型是否破坏 `M4..M8` 正确性

说明:
- `M8`、`M9` 当前为可选阶段，只有在用户明确继续时才派发。

## 9. 输出要求

每阶段结束时，orchestrator 应产出一份最小摘要:
- 当前阶段
- implementer 结果摘要
- reviewer verdict
- 是否允许推进
- 若不允许推进，下一轮 implementer 必修清单
