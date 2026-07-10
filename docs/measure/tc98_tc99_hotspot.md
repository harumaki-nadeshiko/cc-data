# TC98 / TC99 8n2s 热区争用 — 设计文档与问题分析

> 日期: 2026-07-10 | 供专家评审

## 1. 背景

TC98 是 8-node dual-socket 压力测试：16 个 socket-plane primary CPU（8 节点 × 2 socket）
向 home node 0 反复写入。设计目标为验证 UBCC home 目录在高并发下的序列化能力（outstanding /
replayArmed pipeline）。

## 2. TC98（原始激进版）：单 PA 16 路争写

**源码**: `tests/e2e/workloads/e2e_tc98_8n2s_hotspot.c`

**核心设计**：所有 16 个 socket-plane CPU 向 **同一个 cache line**（`home=0, socket=0, offset=0x7800`）
写入 16 轮。每轮写入后立即读回。结束后每个 CPU 向自己的 done slot 写入唯一标记，barrier 后
node0 读取所有 16 个 done marker 验证。

```c
// 同一条 line，16 路并发写
*hot_addr() = v;
(void)*hot_addr();  // pipeline drain
```

**编译与运行**:
```bash
# 编译 (Docker 内)
bash scripts/ubcc_docker_run.sh bash -c \
  "NUM_NODES=8 NUM_SOCKETS=2 bash scripts/compile_workload.sh 98"

# 运行 (Docker 内, 可能需要很长的 timeout)
bash scripts/ubcc_docker_run.sh bash -c \
  "TIMEOUT_SEC=3600 tests/e2e/run_multi.sh --8n2s 98"
```

### 2.1 问题

实测 **TIMEOUT 超过 1800s**（30 分钟），8 个 gem5 节点无一个产生 `SIM DONE` 或
`[TC98_PROGRESS]` 标记。日志特征：

```
[CLK-SYNC] node=0 curT=3659800000 rxt=3659800000 safeT=3659800000 WAIT cnt=38000
```

保守 PDES 同步的 wait 计数极高（38000），说明各 gem5 节点大部分时间在等待 peer 推进时钟，
仿真几乎停滞。

### 2.2 根因分析

单 PA 16 路争写的请求流模式：

```
CPU_0(s0)/CPU_1(s1)/CPU_2(s0)/CPU_3(s1)/...CPU_15(s1)
  │     │     │     │          │
  ▼     ▼     ▼     ▼          ▼
  ┌─────────────────────────────────┐
  │  home UBCC (node 0, socket 0)   │
  │  outstanding slot: 1/line       │
  │  对同一 PA 只能有一个 RECALL    │
  │  或 GRANT_HANDSHAKE 在进行      │
  └─────────────────────────────────┘
```

- UBCC 对同一 PA 只能有一个活跃 outstanding。16 个请求者中只有 1 个能拿到 grant，其余 15 个
  被 BUSY(-1) 返回，排队进入 `_pendingRequesters[linePa]`。
- 拿到 grant 的请求者通过 RECALL/INVALIDATE 获取所有权 → 写数据 → Clear 提交 → **然后**
  UBCC 的 `replayPendingRequesters` 才唤醒下一个排队者。
- 每个请求完成需要一次完整的 RECALL+Clear 跨节点往返（~2-3µs）。16 轮 × 16 请求者 = 256 次
  串行操作。但由于 gem5 PDES 保守同步，**所有 8 个 gem5 实例必须同步推进虚拟时间**。
  当 node 7 在处理自己的事务时，node 0-6 都在等待 node 7 推进 `safeTs`，导致整体进度被
  最慢节点拖住。16 个 gem5 进程间的保守同步形成了 **级联等待**：每个节点的请求都依赖其它
  节点的 UBCC 响应，形成交叉依赖环。

- 同 node 的 cross-socket 请求还要经过内部 NoC（gem5 Ruby NoC MessageBuffer），进一步
  增加延迟。

**本质**：这不是 UBCC 目录协议的问题（串行化是正确的），而是 **8n2s 拓扑下 gem5 PDES
保守同步 + 16 路单 PA 争用的组合**使仿真时间推进极端缓慢。这是仿真性能瓶颈，不是一致性
bug。

## 3. TC99（温和替代版）：Per-Plane Slot

**源码**: `tests/e2e/workloads/e2e_tc99_8n2s_perplane_slots.c`

**核心设计**：每个 socket-plane **各自拥有独立的 cache-line 对齐的 slot**（同一 home node 0，
不同 offset，每 64 字节一条 line），16 路并发写互不冲突。仍然练习 16 路 UBCC outstanding /
`_pendingRequesters` 队列竞争（同一 home 的不同 PA），但不触发单 PA 串行化。

```c
// 各自独立的 slot，16 路并发写互不冲突
*my_slot(node_id, socket_id) = v;
(void)*my_slot(node_id, socket_id);
```

**编译与运行**（同 TC98，只需改 tc id）:
```bash
bash scripts/ubcc_docker_run.sh bash -c \
  "TIMEOUT_SEC=600 tests/e2e/run_multi.sh --8n2s 99"
```

**实测结果**: PASS (16/16 MATCH，<5 分钟)。

## 4. 两版本对比

| 维度 | TC98 (激进) | TC99 (温和) |
|------|-----------|-----------|
| 写目标 | **同一条 cache line**（offset=0x7800） | 各自独立 line（0x7800 + plane×64） |
| 并发写冲突 | 16→1 串行化（UBCC outstanding 互斥） | 无冲突（不同 PA 可并行） |
| 目录压力 | 单 PA 队列深度 15 | 多 PA 分散，队列较短 |
| PDES 交叉等待 | 严重（级联依赖） | 轻微（跨节点消息可并行） |
| 仿真速度 | >1800s 超时 | <5 分钟 |
| 测试目标 | 极限串行化压力 | 16 路目录 pipeline 压力 |
| 适用场景 | 验证 UBCC 单 PA 正确性（需超长 timeout） | 日常回归 / CI |

## 5. 给专家的待确认问题

1. **PDES 保守同步在 8n2s+单 PA 争用下的可扩展性**：当前 gem5 syncInterval=100ns 是否偏大？
   调小（如 10ns）是否能缓解级联等待？还是根本瓶颈在 PDES 模型本身？

2. **TC98 是否应该保留**：如果调整 syncInterval 后能在合理时间内完成（如 <30 分钟），
   可作为极限压力测试保留。否则建议仅保留 TC99 作为 8n2s 回归项，TC98 降级为"手动触发
   的压力分析工具"。

3. **UBCC 的 `_pendingRequesters` 队列深度**：当前设计是否有反压机制防止队列无限增长？
   16 路 16 轮意味着最多 15 个排队者 × 16 轮 = 240 个 pending entry 峰值。

## 6. 测试注册清单

| TC | 拓扑 | 状态 | 说明 |
|----|------|------|------|
| 98 | 8n2s | ⚠️ 编译通过，运行超时(>1800s) | 单 PA 16 路争写，需超长 timeout |
| 99 | 8n2s | ✅ PASS | 独立 slot，16 路目录 pipeline 测试 |
