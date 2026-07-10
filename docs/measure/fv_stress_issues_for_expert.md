# 形式化验证与压力测试问题汇总 — 供专家评审

> 日期: 2026-07-11 | 合并 docs/measure/tc98_tc99_hotspot.md + 本次 TLA+ 分析

---

## Part A: TLA+ 建模缺陷 — `ep_intra_node_single`

### A.1 模型简介

`verification/tla/ep_intra_node_single.tla` — EP intra-node 单 socket 一致性协议。
建模范围：2 CPUs, HN-F 目录, EP-RNF, SNF/Backend，4 个消息队列（reqQ/snpQ/rspQ/datQ）。

### A.2 问题 1: 状态空间爆炸（无界消息队列 + 非确定性数据选择）

**症状**: 原始 `Spec == Init /\ [][Next]_vars`（无公平性约束），TLC 运行 9 小时产生
4.5B+ distinct states（depth 122），未完成。即使加入 `FairSpec`（13 条 WF 公平性），
仍然 348M distinct states，15 分钟超时。

**根因**:
1. `BackendGrant`（line 308）、`CpuStore`（line 124）等 action 使用了 `\E gd/data \in DataV`
   非确定性数据选择。每次调用产生 `|DataV|=2` 个分支状态。这些数据分支在各消息队列中不断累积，
   且消息队列可以无界增长（无公平性约束时 CPU 可无限发请求而 HN-F 不消费）。
2. 与 `ubcc_protocol_core.tla`（秒级完成）对比：UBCC 模型有 `MaxEpoch`/`TombstoneWindow` 自然
   上界，且有 5 条 WF 公平性约束。而本模型缺少事务计数上限。

**缓解方案**: 加入 `CONSTRAINT QueueBounded` 限制总飞行消息数 ≤N。
实测：≤4 时 3 秒完成（1.8M distinct states）。≤6/8/10 时触发问题 2。

### A.3 问题 2: 伪死锁（模型未建模 Retry）

**症状**: `QueueBounded ≤ 6` 及以上时，TLC 报告 "Deadlock reached"（真死锁，非 CONSTRAINT 伪影）。

**死锁 trace**:
```
State 18: <HnfDropStaleReq>
  cpuState = (0 :> "P_RS" @@ 1 :> "P_EVICT")
  hnfState = "UD"
  reqQ = <<>>
  hnfTbeValid = FALSE
```

**根因**: `HnfDropStaleReq`（line 217–234）丢弃了一个 RS 请求（因为 CPU 状态与请求不匹配的
竞争条件），但模型没有 `CpuRetry` 动作让 CPU 从 "P_RS" 重新发请求。CPU 被永久卡在
pending 状态，形成有穷状态空间中的真死锁。

**判断**: 这不是 EP 实现的问题——EP-SNF/EPRNF 有 retry 队列（`_retryQueue`）和定时器重新
发起请求。TLA+ 模型是抽象层，没有建模这套 retry 机制，因此在高并发宽度（≥6 飞行消息）下
触碰到了未被建模的 race recovery 场景。

**关系**: `QueueBounded ≤ 4` 恰好剪掉了通往此死锁的路径（到达死锁需要 ≥5 步中间状态），
避开了模型抽象缺陷，验证了安全不变式。

### A.4 建议的模型改进

| 优先级 | 改动 | 说明 |
|--------|------|------|
| P0 | 添加 `CpuRetry(cpu)` 动作 | `cpuState[cpu] \in {"P_RS","P_RU","P_EVICT"} /\ reqQ' = Append(reqQ, ...)` 模拟 retry 队列重新驱动 |
| P1 | 用 `MaxTxn` 替代无界探索 | 参考 `ep_intra_node.tla` 的 `MaxTxn=3` 模式，限制总事务数 |
| P2 | `BackendGrant` 数据值改为固定值 | `gd` 的取值不影响 permission/coherence 不变式，去掉 `\E gd` 可将状态空间减半 |

### A.5 运行方式

```bash
cd verification/tla
# 快速: CONSTRAINT 限制飞行消息
./run_tlc.sh ep_intra_node_single.tla ep_intra_node_single.cfg

# 完整公平性（需要更长时间）
# 修改 cfg: SPECIFICATION FairSpec + 去掉 CONSTRAINT
./run_tlc.sh ep_intra_node_single.tla ep_intra_node_single.cfg
```

---

## Part B: TLA+ 建模缺陷 — `ubcc_transport_faults`

### B.1 问题: push-grant 故障路径未建模

`ubcc_transport_faults.tla` 仅对 **Clear**（显式队列）和 RecallResp/InvAck（duplicate）注入
传输故障。push-grant 新增的 home→requester grant 推送消息**没有被故障模型覆盖**。

但安全性仍然成立，因为：
- push-grant 丢失 → `replayArmed` 保留 → EP-SNF retry 定时器 fallback 拉取
- 这形成隐式的"自愈"机制，在传输故障模型下等价于原 pull 路径

**建议**: 在 transport_faults 模型中显式添加 push-grant 消息的 Drop/Duplicate 操作，并验证
fallback 路径不引入死锁。

---

## Part C: 8n2s 压力测试 — TC98 单 PA 争用超时

详见独立文档 `docs/measure/tc98_tc99_hotspot.md` 的 §2。核心问题：

- TC98（激进版）：16 socket-plane CPU 向**同一条 cache line**（home=0,socket=0,offset=0x7800）
  写入 16 轮。UBCC 目录对同一 PA 只能有一个 outstanding → 16→1 串行化 → PDES 保守同步下 16 个
  gem5 实例形成级联等待 → 仿真 >1800s 超时。
- TC99（温和版）：各自独立 cache line，16 路并发无冲突 → <5min PASS。
- 不是一致性 bug，是 8n2s + 单 PA 争用 + PDES 同步的**仿真性能瓶颈**。

### 待问专家（合并）

1. PDES `syncInterval=100ns` 在 8n2s+单 PA 争用下是否为性能瓶颈？调小能否缓解级联等待？
2. `UBCCController::_pendingRequesters` 队列深度是否有上限？16 路 × 16 轮最坏情况下排队者峰值
  是多少？
3. `ep_intra_node_single` TLA+ 模型是否值得加入 retry 机制？还是维持当前 `CONSTRAINT` 做法，
  仅验证正向协议路径？

---

## Part D: PDES 同步量化延迟

在之前 latency 分析中发现的 nsim 网络跳存在 ~177.5ns 的 PDES 同步对齐尾延迟（`syncInterval=100ns`
量化），已写入 `framework/Port.hh`（行 20–27 注释）。可通过环境变量 `EP_SYNC_INTERVAL_PS` 在生产
环境调小以降低量化误差（代价：心跳频率增加，IPC 开销增大）。

---

## 文件索引

| 文件 | 内容 |
|------|------|
| `docs/measure/tc98_tc99_hotspot.md` | TC98 单 PA 争用根因分析 + TC98/99 编译运行指南 |
| `verification/tla/ep_intra_node_single.tla` | EP intra-node TLA+ 模型（含 FairSpec + QueueBounded） |
| `verification/tla/ep_intra_node_single.cfg` | TLC 配置（CONSTRAINT QueueBounded ≤4） |
| `framework/Port.hh:20-35` | syncInterval 保真度权衡注释 |
