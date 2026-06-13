# 快速启动提示词

## 刚完成 Phase A（引导提问结束）

> 已完成 Phase A：Gap Discovery。现在需要 Phase C 方案合成。
> 
> 请严格按照 `docs/recovery/workflow_guide.md` 中的 Phase 1 Step 1.2 执行：
> 用 `plan-designer` 进行 Phase C Schema Synthesis，输出 scheme_v4.md。

---

## 其他阶段的启动提示词

### 方案已审核，准备实现
> 方案 `docs/recovery/scheme_v4.md` 已确认。请严格按照 `docs/recovery/workflow_guide.md` 的 Phase 3 流程，
> 从 Layer 3a 开始逐层实现。每个 Layer 的流程：code-implementer 实现 → build-runner 编译 → 处理结果。

### 某层编译失败需要修复
> Layer 3X 编译失败。请按 `docs/recovery/workflow_guide.md` 的 Phase 3 Step 3.X.3 流程处理。
> 先用 flash-scanner triage，根据 triage 结果决定下一步。

### 准备开始测试
> Phase 3 所有层编译通过。请按 `docs/recovery/workflow_guide.md` 的 Phase 4 流程，
> 从 TC1 开始按优先级测试。每个 TC 最多 2 次修复尝试。

### TC 测试反复失败
> TC N 已失败 2 次，标记为 OPEN_QUESTION。请按 `docs/recovery/workflow_guide.md` 的 Phase 4 流程继续下一个 TC。

### OPEN_QUESTION 积累太多
> 已有 3 个 OPEN_QUESTION。请按 `docs/recovery/workflow_guide.md` 的失败升级协议，
> 建议调用 `failure-analyst` 进行深度分析。

---

## 规则速查

- **所有实现/分析/调试都通过 `task()` 分派给 subagent** ——主 agent 只做协调
- **编译阶段**: 每层 1 次实现 → 1 次编译 → PASS 则 commit → 下一层
- **测试阶段**: P0 最多 2 次修复，P1/P2 最多 1 次修复
- **失败 2 次后必须展示给用户**，不要自己无限重试
- **不要修改 source files 自己** ——始终通过 code-implementer
- **不要自己分析错误** ——始终通过 flash-scanner 或 failure-analyst
