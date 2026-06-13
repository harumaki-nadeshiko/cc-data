# Protocol Development Workflow — Coordination Document

> **Audience**: 主 Agent (Sisyphus, 默认模型 DeepSeek v4-Pro)  
> **使用方式**: 在每一步完成后，读本文档的对应章节，严格按指令分派 subagent。  
> **核心约束**: 你是协调者，不要自己做实现/分析/调试——全部通过 `task()` 分派。

---

## 设计偏离记录规则（强制）

**任何与 `docs/recovery/scheme_v4.md` 不符的自行修改与决定，必须在执行后立即追加记录到 `docs/recovery/drift_in_progress.md`。**

包括但不限于：
- 修改 scheme_v4.md 中标记为 `fatal/unreachable` 的路径（如改为 defensive/warning）
- 禁用/跳过/绕过任何测试或 self-test
- 改变数据结构的位置或可见性（如将类内 enum 移到类外）
- 修改 API 签名而未更新 scheme_v4.md
- 因编译/运行限制而采用的 workaround
- 任何未经过用户明确确认的设计决策

每条记录包含：**时间、位置、偏离内容、偏离原因、状态（✅/⚠️）**。

> 违反此规则会导致用户无法追踪代码变更与设计的对应关系。

---

---

## 架构规则

**主 Agent 拥有 `task` 工具**，可以分派 subagent。Subagent **没有** `task` 工具，不能再分派。

```
你 (主 Agent, DeepSeek v4-Pro, 拥有 task 工具)
  │
  ├─→ task(subagent_type="plan-designer", ...)     GPT-5.4   方案设计
  ├─→ task(subagent_type="state-analyzer", ...)    GPT-5.4   状态分析
  ├─→ task(subagent_type="code-implementer", ...)  DS v4-Pro 代码实现
  ├─→ task(subagent_type="build-runner", ...)      DS v4-Pro 编译测试
  ├─→ task(subagent_type="flash-scanner", ...)     DS Flash  错误分类
  ├─→ task(subagent_type="failure-analyst", ...)   GPT-5.3   深度分析
  └─→ task(subagent_type="strict-task-completion-reviewer", ...)  GPT-5.3  最终审核
```

---

## 可用 Subagent 速查

| subagent_type | 模型 | 用途 | 输入 |
|---------------|------|------|------|
| `plan-designer` | GPT-5.4 | 引导提问 + 方案合成 | `"Phase A: ..."` 或 `"Phase C: ..."` |
| `state-analyzer` | GPT-5.4 | 状态空间穷举 | `"Analyze scheme_v4.md"` |
| `code-implementer` | DS v4-Pro | 按 scheme 逐层实现 | `"Implement Layer X: files [...]"` |
| `build-runner` | DS v4-Pro | 编译 / 运行测试 | `"Build gem5.opt"` 或 `"Test TC N"` |
| `flash-scanner` | DS Flash | 错误 triage | `"Triage these errors: [...]"` |
| `failure-analyst` | GPT-5.3 | 深度故障分析 | `"Analyze TC N failure"` |
| `strict-task-completion-reviewer` | GPT-5.3 | 阶段完成审核 | `"Audit Phase X"` |

---

## Phase 1: 方案拟定

### Step 1.1: 启动 Phase A (Gap Discovery)

**用户输入**: `用 plan-designer 开始 Phase A: Gap Discovery`

**你的操作**:
```
task(subagent_type="plan-designer",
     description="Phase A: Gap Discovery round 1",
     prompt="读取 docs/recovery/entry_document.md。开始 Phase A Gap Discovery。
             问 3-5 个引导式问题，聚焦在协议设计中未明确的方面。
             每个问题提供候选选项和优劣势分析。不要输出方案——只问问题。")
```

**后续**: 将 plan-designer 的提问展示给用户，等待用户回答。将用户回答传回 plan-designer 继续下一轮提问。

**循环**:
```
用户回答 → task(subagent_type="plan-designer",
                 prompt="基于用户回答继续下一轮提问。用户回答: [用户回答内容]。问 3-5 个新问题。")
```

**终止条件**: 用户说 `开始方案合成` 或 `Phase C`

### Step 1.2: Phase C — 方案合成

**用户输入**: `开始方案合成`

**你的操作**:
```
task(subagent_type="plan-designer",
     description="Phase C: Schema Synthesis",
     prompt="Phase C: Schema Synthesis。
             基于 Phase A 所有问答结果 + docs/recovery/entry_document.md，
             输出完整的 scheme_v4.md。
             格式要求见 entry_document.md §8。
             将结果写入 docs/recovery/scheme_v4.md。")
```

**后续**: 将方案展示给用户审核。用户可能要求修改或确认。

### Step 1.3: 状态空间分析

**用户输入**: `开始 Phase 2` 或 `分析状态空间`

**你的操作**:
```
task(subagent_type="state-analyzer",
     description="Phase 2: State space analysis",
     prompt="Analyze docs/recovery/scheme_v4.md for state space hazards.
             如果 scheme 未就绪，返回 SCHEME_NOT_FOUND。")
```

**后续**:
- 如果发现 critical hazard → 展示给用户，等待决策
- 如果 clean → 告知用户可以进入 Phase 3

---

## Phase 3: 分层实现

### 准备

确认 gem5 在 `c665e76a58`，working tree clean。

### 逐层执行 (3a → 3b → 3c → 3d → 3e)

**对每个 layer**，执行相同的三步循环：

#### Step 3.X.1: 实现

```
task(subagent_type="code-implementer",
     description="Implement Layer 3X",
     prompt="Implement Layer 3X per docs/recovery/scheme_v4.md。
             Files: [列出该层文件]。
             不要编译，只修改代码。")
```

#### Step 3.X.2: 编译

```
task(subagent_type="build-runner",
     description="Build after Layer 3X",
     prompt="Build gem5.opt。")
```

#### Step 3.X.3: 编译结果处理

- **PASS** → 提交 + 进入下一层
- **FAIL (第 1 次)** → 用 flash-scanner triage:
  ```
  task(subagent_type="flash-scanner",
       description="Triage compile errors",
       prompt="Triage these compile errors: [粘贴 build-runner 输出的错误]")
  ```
  - 如果 recommendation=simple_fix → 回到 Step 3.X.1（重新实现）
  - 如果 recommendation=needs_scheme_fix → 展示给用户

- **FAIL (第 2 次)** → 展示错误给用户，询问方向

### 各层文件清单

| Layer | 文件 |
|-------|------|
| 3a: Infrastructure | `CHI_config.py`, `CHI_ubcc_framework.py`, `CHI-msg.sm`, `CHI-cache.sm`, `EPRNFController.py` |
| 3b: SLICC Protocol | `CHI-cache-actions.sm`, `CHI-cache-funcs.sm`, `CHI-cache-transitions.sm` |
| 3c: EP Controllers | `EPRNFController.cc`, `EPRNFController.hh`, `EPSNFController.cc`, `EPSNFController.hh` |
| 3d: Backend Logic | `EPBackend.cc`, `EPBackend.hh`, `UBCCController.cc`, `UBCCController.hh` |
| 3e: Integration | 全量编译 + TC1 验证 |

### 提交 (每层编译通过后)

```bash
cd gem5 && git add -A && git commit -m "phase3X: <描述>" && cd ..
git add gem5 && git commit -m "phase3X: submodule @ <hash>"
```

---

## Phase 4: 增量测试

按优先级逐 TC 测试。**每个 TC 最多 2 次修复尝试**。

### 测试命令

```
task(subagent_type="build-runner",
     description="Test TC N",
     prompt="Test TC N。")
```

### 测试结果处理

| 结果 | 操作 |
|------|------|
| PASS | 记录通过，进入下一个 TC |
| FAIL (第 1 次) | flash-scanner triage → 如果 simple_fix，让 code-implementer 修复后重测 |
| FAIL (第 2 次, P0) | 标注 OPEN_QUESTION, 继续下一个 |
| FAIL (第 1 次, P1/P2) | 标注 OPEN_QUESTION, 继续下一个 |

### 测试优先级

```
P0: TC1 → TC2 → TC5
P1: TC6 → TC7 → TC11
P2: TC3 → TC8 → TC4
```

---

## 失败升级协议

| 场景 | 处理 |
|------|------|
| 编译失败 1 次 | flash-scanner triage → code-implementer 修复 |
| 编译失败 2 次 | **展示给用户**，等待方向 |
| P0 测试失败 2 次 | 标注 OPEN_QUESTION，继续 |
| P1/P2 测试失败 1 次 | 标注 OPEN_QUESTION，继续 |
| 3+ OPEN_QUESTION | **展示给用户**，建议调用 failure-analyst 深调 |

**深度分析调用**（仅用户明确要求时）:
```
task(subagent_type="failure-analyst",
     description="Deep analysis of TC N",
     prompt="Analyze TC N failure. Read simout from m5out/e2e/tcN/.
              Root cause + fix. Cross-reference docs/recovery/decisions.md.")
```

---

## 当前状态追踪

维护一个简单的状态记录。在每步完成后更新用户"当前进度"：

```
📋 Phase X | Layer Y | TC: [通过/总数] | Open Questions: N
```

不需要写文件，口头汇报即可。
