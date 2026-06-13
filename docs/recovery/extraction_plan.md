# 会话历史提取与文档化方案

## 0. 背景

gem5 submodule 在 commit `c665e76a58` 处的代码**裸状态无法通过任何 E2E 测试**。
此前 Phase 0-4 的完整协议实现存在于 uncommitted working tree 中，因 `git checkout -- .` 丢失。

**目标**：从 116 个 session（11 天对话，7,635 条消息，31,699 个工具调用，53MB 原始数据）中，
提取出完整的代码修改意图、演进历史、和最终正确状态，生成可执行的恢复方案文档。

**关键挑战**：
- 53MB 原始数据远超任何单模型上下文窗口
- 大量元数据、中间思考过程和 abortive experiments 需要过滤
- 设计决策在会话过程中发生过流变（协议语义修正、优化回退等）

## 1. 数据预处理（已完成）

### 1.1 原始数据
| 文件 | 格式 | 大小 |
|------|------|------|
| `tmp_logs/full_conversation.jsonl` | JSONL, 每行一条 message | 57.5 MB |
| `tmp_logs/conversation_index.json` | 116 个 session 摘要 | ~50 KB |

### 1.2 预处理脚本
`tools/preprocess_conversation.py` 执行：
1. 按 session 和 3 小时窗口分片
2. 过滤中间思考过程和重复调试输出
3. 仅保留：用户消息、文件修改操作（edit/write）、测试/编译输出、task 调度
4. 输出为 `tmp_logs/chunks/chunk_NNNN.json`

**执行**：
```bash
python3 tools/preprocess_conversation.py
```

## 2. Agent 配置

### 2.1 已有 Agent
| Agent 名 | 模型 | 用途 |
|----------|------|------|
| `cache-coherence-implementer` | deepseek-v4-pro | 协议实现与修复 |
| `strict-task-completion-reviewer` | gpt-5.3-codex | 严格完成度审查 |
| `coder-validator-orchestrator` | deepseek-v4-flash | 计划编排调度 |

### 2.2 新增 Agent
| Agent 名 | 模型 | mode | 定位 |
|----------|------|------|------|
| `agent-flash` | deepseek-v4-flash | all | **高吞吐扫描**：批量处理分片、提取结构化信息 |
| `agent-pro` | deepseek-v4-pro | all | **深度推理合成**：分析流变决策、生成最终文档 |

## 3. 三阶段处理方案

### Phase 1: 并行分片扫描（Agent-Flash 主导）

```
For each chunk file in tmp_logs/chunks/:
  调用 agent-flash subagent:
    prompt: "分析该会话分片。提取：
    1. 修改了哪些文件（路径 + 修改意图）
    2. 用户的决策性消息（时间 + 内容摘要）
    3. 测试/编译结果（PASS/FAIL + 错误信息）
    4. 本分片内设计决策的流变（如果同一文件被多次修改）
    返回结构化 JSON。"
```

**并行度**: 5-10 个 chunk 同时处理（agent-flash 的 deepseek-v4-flash 模型低成本高并发）

**输出**: `tmp_logs/phase1/chunk_NNNN_analysis.json` (每个 chunk 一个分析文件)

### Phase 2: 文件维度聚合（Agent-Pro 主导）

```
调用 agent-pro subagent:
  prompt: "读取 Phase 1 所有分片分析结果，按文件维度聚合：
  对于每个被修改的文件：
    1. 按时间序列列出所有修改操作（edit/write）
    2. 识别哪些修改是 experiment（后来被回退）
    3. 识别哪些修改是 final（最终保留）
    4. 提取修改之间的依赖关系
    5. 输出每个文件的'修改时间线'文档
  
  对于全局决策：
    1. 列出所有用户决策性消息（带时间戳）
    2. 追踪设计流变（如 shared_hint → shared_hint via CompData_SC → alloc_on_readunique)
    3. 构建'决策依赖图'
  
  文件输出：
  - tmp_logs/phase2/per_file_timeline.json
  - tmp_logs/phase2/decision_graph.json
  - tmp_logs/phase2/experiment_vs_final.json"
```

**输出**: 3 个结构化 JSON 文件

### Phase 3: 恢复方案生成（Agent-Pro 主导）

```
调用 agent-pro subagent:
  prompt: "基于 Phase 2 的输出，生成以下文档：
  
  ## 文档 A: 完整修改目录
  - 列出所有被修改的文件
  - 每个文件的最终正确状态描述
  - 关键修改的 diff 形式呈现
  
  ## 文档 B: 按阶段的实施计划
  - Phase 0: MachineID 注入 + 测试基础设施
  - Phase 1: shared_hint 注册 + DCT fallback
  - Phase 2: UBCC snoop 操作 + 目录更新
  - Phase 3: globalInvalidate + 跨节点数据路径
  - Phase 4: 写回 + 召回 + 数据一致性
  每个阶段列出：
    - 需要修改的文件和具体修改内容
    - 验证方法（哪个 TC）
    - 前置依赖
    - 已知陷阱
  
  ## 文档 C: 决策与关键修复记录
  - alloc_on_readunique True/False 的决策过程
  - NCBWrData 跨节点路由的设计演变
  - EP-RNF retToSrc/multicast 的决策过程
  - GRANT_HANDSHAKE BUSY 机制的演变
  - deadlock_threshold 从 "10ms" 到 20000000 的修复
  
  每个文档应为独立、完整的 markdown，可直接作为 agent prompt 使用。
  文件输出：
  - docs/recovery/catalog.md
  - docs/recovery/phase_plan.md  
  - docs/recovery/decisions.md"
```

## 4. 执行命令

```bash
# Step 0: 预处理（如果尚未执行）
python3 tools/preprocess_conversation.py

# Step 1: 并行分片扫描
# 使用 Agent-Flash 批量处理所有 chunk
# 调度方式：每个 chunk 一个 subagent 调用

# Step 2: 文件维度聚合
# 使用 Agent-Pro 单次调用深度分析

# Step 3: 恢复方案生成
# 使用 Agent-Pro 生成最终文档
```

## 5. 预期产出

| 文档 | 用途 |
|------|------|
| `docs/recovery/catalog.md` | 完整文件修改目录，可执行的重放参考 |
| `docs/recovery/phase_plan.md` | 按阶段的实施计划，agent 可直接执行 |
| `docs/recovery/decisions.md` | 决策记录，避免重蹈覆辙 |

## 6. 约束与注意

- Agent-Flash 的 deepseek-v4-flash 模型：**速度优先，可接受一定漏检率**，后续由 Agent-Pro 弥补
- Agent-Pro 的 deepseek-v4-pro 模型：**质量优先，用于合成和验证**
- 每个 subagent 调用的 prompt 必须包含明确的输入文件路径和输出格式要求
- 如果 API 配额不足，Phase 1 可分多轮执行
