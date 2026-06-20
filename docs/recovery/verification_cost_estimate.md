# FV 任务成本估算 (v3 — 新增 agent 后)

## Agent 一览

| Agent | 模型 | Input $/MTok | Output $/MTok | 用途 |
|-------|------|-------------|---------------|------|
| **quick-analyzer** **[NEW]** | DeepSeek V4 Flash | $0.14 | $0.28 | 简单表/枚举/矩阵 |
| **protocol-analyzer** **[NEW]** | DeepSeek V4 Pro | $1.74 | $3.48 | 中度协议分析/追踪 |
| code-implementer | DeepSeek V4 Pro | $1.74 | $3.48 | 代码修改 |
| medium-guider | GPT-5.3 Codex | $1.75 | $14.00 | 中等推理+代码 |
| plan-designer | GPT-5.3 | $1.75 | $14.00 | 方案设计 |
| state-analyzer | GPT-5.4 | $2.50 | $15.00 | 状态空间/不变量 |
| intelligent-guider | GPT-5.4 | $2.50 | $15.00 | 深度分析/活性/竞态 |

## 任务重新分配

## 假设

- 每次 subagent 调用：~30K input tokens (读源码) + ~5K output tokens (产出)
- 复杂任务 (intelligent-guider, state-analyzer)：~50K input + ~8K output
- 简单任务 (medium-guider 轻量)：~20K input + ~3K output
- 包括多轮收紧的调用按 1.5× 估算

## 按任务重新分配

### Wave 0 (可立即启动，无 instrument)

| FV | 任务 | Agent | 模型 | 调用 | 成本 |
|----|------|-------|------|------|------|
| FV-1 | MESI 状态枚举 | state-analyzer | GPT-5.4 | 1 | $0.25 |
| FV-2 | epoch+sharers 不变量 | state-analyzer | GPT-5.4 | 2 | $0.75 |
| FV-3 | OutstandingRequest 生命周期 | **protocol-analyzer** | V4 Pro | 2 | $0.21 |
| FV-9 | UBMsg 字段验证表 | **quick-analyzer** | V4 Flash | 1 | $0.004 |
| FV-11 | 覆盖率矩阵 | **quick-analyzer** | V4 Flash | 1 | $0.006 |
| **Wave 0** | | | | **7** | **$1.22** |

### Wave 1 (需 instrument 设计)

| FV | 任务 | Agent | 模型 | 调用 | 成本 |
|----|------|-------|------|------|------|
| FV-6 | Snoop 处理 | **protocol-analyzer** | V4 Pro | 2 | $0.21 |
| FV-7 | Recall 数据路径 | intelligent-guider | GPT-5.4 | 2 | $0.75 |
| FV-8 | Invalidate barrier | medium-guider | GPT-5.3C | 2 | $0.37 |
| FV-10 | 序列化 round-trip | **protocol-analyzer** | V4 Pro | 2 | $0.21 |
| **Wave 1** | | | | **8** | **$1.54** |

### Wave 2 (需故障注入)

| FV | 任务 | Agent | 模型 | 调用 | 成本 |
|----|------|-------|------|------|------|
| FV-4 | 丢包/重排/重复 | intelligent-guider | GPT-5.4 | 2 | $0.90 |
| FV-5 | 无死锁活性 | intelligent-guider | GPT-5.4 | 2 | $0.90 |
| **Wave 2** | | | | **4** | **$1.80** |

## 总成本

| Wave | 调用数 | GPT-5.4 | V4 Pro | V4 Flash | GPT-5.3C | 合计 |
|------|--------|---------|--------|----------|----------|------|
| Wave 0 | 7 | 3 | 2 | 2 | 0 | $1.22 |
| Wave 1 | 8 | 2 | 4 | 0 | 2 | $1.54 |
| Wave 2 | 4 | 4 | 0 | 0 | 0 | $1.80 |
| **∑** | **19** | **9** | **6** | **2** | **2** | **$4.56**

## 与原始分配的对比

| 任务 | 原 agent | → 新 agent | 原因 |
|------|---------|-----------|------|
| FV-3 | medium-guider (GPT-5.3C, $14 output) | protocol-analyzer (V4 Pro, $3.48 output) | output 便宜 4× |
| FV-6 | state-analyzer (GPT-5.4) | protocol-analyzer (V4 Pro) | snoop 链路不需要深度证明 |
| FV-9,11 | medium-guider | quick-analyzer (V4 Flash) | 纯表/矩阵，Flash 够用 |
| FV-10 | medium-guider | protocol-analyzer (V4 Pro) | 序列化分析，V4 Pro 推理更好

## 建议

- **Wave 0 全并行启动**：FV-1/2/3/9/11 无依赖，$1.22 先跑通
- Wave 0 产出 → Wave 1 逐步推进 → Wave 2 最后
- 若预算有限，可砍 Wave 2（$1.80）—— FV-4/5 对当前单进程 gem5 不是阻塞项，standalone 迁移后再做
