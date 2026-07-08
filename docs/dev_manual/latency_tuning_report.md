# 延迟调参分析报告

基于 TC1/TC2/TC5 的 TRACE-PERF 数据（2026-07-07 21:17 运行），所有时间单位：gem5/ubio/nsim tick = 1ps。

## 1. 当前实测值

### 1.1 Tq（ZMQ IPC 单跳）

| 统计量 | 值 |
|--------|-----|
| 样本数 | 54 |
| **平均值** | **98.7ns**（98472ps） |
| P50 中位数 | 100.0ns |
| 最小值 | 32.0ns |
| 最大值 | 100.0ns |

**结论**：Tq ≈ 100ns，与甲方目标 100ns 几乎完全吻合。不需调整。

### 1.2 nsim FIFO 转发延迟（按 link pair 分）

| Link Pair | 方向 | 样本数 | avg | p50 | min | max |
|-----------|------|--------|-----|-----|-----|-----|
| mod0→mod0 | same-mod | 1 | 100ns | 100ns | 100ns | 100ns |
| mod1→mod1 | same-mod | 4 | 100ns | 100ns | 100ns | 100ns |
| mod0→mod1 | cross-node | 4 | 134ns | 148ns | 100ns | 188ns |
| mod1→mod0 | cross-node | 4 | 115ns | 100ns | 100ns | 162ns |
| mod1→mod2 | cross-node | 4 | 100ns | 100ns | 100ns | 100ns |
| mod2→mod1 | cross-node | 7 | **173ns** | 162ns | 162ns | 188ns |
| **cross-node 汇总** | | 19 | **137ns** | 148ns | 100ns | 188ns |
| **所有汇总** | | 24 | 129ns | 100ns | 100ns | 188ns |

nsim 延迟 = `link_latency`（gen_topo.py 当前统一为 100000ps=100ns）+ FIFO 排队等待。跨节点路由因 safeTs 对齐引入额外等待，mod2→mod1 路径平均排队 +73ns。

### 1.3 跨节点 IO hop 端到端

```
总延迟 = 2×Tq + nsim_FIFO(avg cross-node)
       = 2×100ns + 137ns
       = 337ns
目标    = 415ns
差值    = -78ns  （比目标快 78ns）
```

## 2. 目标 vs 实际

| 指标 | 目标 | 当前实测 | 差 | 需调 |
|------|------|---------|----|----|
| Tq（ZMQ IPC） | ~100ns | 98.7ns | ✓ 合格 | 不需调 |
| 跨Node IO hop | 415ns | 337ns | **-78ns** | ↑ 增加 nsim link |
| 同Node跨Socket IO hop | 210~240ns | ~300ns (2×Tq+100ns same-mod) | **+60~90ns** | ↓ 减少 same-mod link |
| core→Local L3 | 15ns | 未测 | 待测 | 待测 |
| core→Local DRAM | 100ns | 未测 | 待测 | 待测 |
| core→Remote DRAM | 110ns | 未测 | 待测 | 待测 |

**注**：L3/DRAM 延迟无法从边界 TRACE-PERF 测量（TC1 纯 gem5 内部，无 trace 事件）。需要给 gem5 Ruby 控制器内部加 trace 点或从 gem5 stats 中提取。

## 3. 调参方案

### 3.1 networksim link latency（核心改动，`scripts/gen_topo.py`）

目前 `latency = 100000` 统一值。需要按 link 类型区分：

```python
# gen_topo.py 改动
CROSS_NODE_LATENCY = 178000   # 跨节点: 178ns (原100ns, 增加78ns)
CROSS_SOCKET_LATENCY = 25000  # 跨Socket: 25ns (原100ns, 减少75ns)
```

| Link 类型 | 目标 IO hop | 公式 | 需配 nsim latency |
|----------|-----------|------|------------------|
| 跨Node同Socket | 415ns | 2×100ns + L_nsim | L_nsim = 415−200 = **215ns** → link=178ns（余37ns给FIFO排队） |
| 同Node跨Socket | 210~240ns | 2×100ns + L_nsim | L_nsim = 10~40ns → link=**25ns** |

注意：当前 `gen_topo.py` 只接受 `--type 1s/2s`，需增加 `--nodes N` `--sockets K` 参数，或从 topo JSON 间接推断 node_id（`mod // num_sockets`）。

### 3.2 L3 cache 延迟（`gem5/configs/ruby/CHI_ubcc_framework.py`）

```python
class HNFCache(RubyCache):
    dataAccessLatency = 30 → 10   # 15ns → 5ns
    tagAccessLatency  = 6  → 4    # 3ns → 2ns
```

目标 15ns（=30cy @2GHz）。当前 L1(2cy)+L2(3cy)+L3(36cy)=41cy=20.5ns，超了。调整后 L1+L2+L3=19cy≈9.5ns + ruby network 内部排队 5ns → 接近 15ns。具体值需加 gem5 内部 trace 点后实测迭代。

### 3.3 DRAM 延迟

DDR4_2400 内部 tCAS+tRCD 等由 gem5 模拟，当前未测。如果实测本地 DRAM 偏离 100ns，调 `SNF.to_memory_controller_latency`（控制 HN-F→SN-F→MemCtrl 的额外 cycle 数）。

远端 DRAM(110ns) = 本地 DRAM(100ns) + 跨 socket nsim hop(25ns) + 额外 ZMQ 开销。

## 4. 后续步骤

1. **立即执行**：改 `gen_topo.py` 区分 link 类型，降 same-node、升 cross-node
2. **重新跑 TC2/TC5**：验证跨节点 IO hop 是否接近 415ns
3. **加 gem5 内部 trace**：在 EPBackend/CHI_HNFController 的请求开始/完成处加 `[TRACE-PERF]`，才能测量 L3/DRAM
4. **迭代 L3 延迟**：跑 TC1 测 core→L3 往返，对照 15ns 微调 `HNFCache` 参数
