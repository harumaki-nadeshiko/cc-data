# Trace Visualizer Manual — TRACE-PERF 请求链路可视化工具

> Produced: 2026-07-20 | Enhanced visualization tool

## 概述

`trace_visualizer.py` 是 TRACE-PERF 日志的增强可视化工具，用于分析和可视化请求链路延迟。相比原有的 `trace2chain.py + chain2html.py` 管道，本工具提供更准确的延迟测量、更丰富的视图和更好的交互性。

### 核心改进

| 改进项 | 说明 |
|--------|------|
| **准确的延迟测量** | 正确检测响应到达源节点的时刻，不再包含 ClearReq/ClearResp 的额外时间 |
| **生命周期隔离** | ReadReq 和 ClearReq 分离为独立链路，避免延迟膨胀 |
| **多视图支持** | 时间轴、流图、延迟分解、统计分析四种视图 |
| **因果关系可视化** | 显示请求在组件间的传播路径 |
| **事件去重** | 自动去除日志中的重复事件 |

## 1. 使用方法

### 1.1 基本用法

```bash
# 分析单个日志目录
python3 scripts/trace_visualizer.py logs/20260719_061438_1s > tc121.html

# 分析并设置目标延迟线
python3 scripts/trace_visualizer.py --target-ns 415 logs/20260719_061438_1s > tc121.html

# 按 PA 前缀过滤
python3 scripts/trace_visualizer.py --filter-pa 0x10000000 logs/20260719_061438_1s > filtered.html

# 分析多个日志目录
python3 scripts/trace_visualizer.py logs/20260719_061438_1s logs/20260718_143440_1s > combined.html
```

### 1.2 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `input` | 日志目录或文件路径（必填，可多个） | - |
| `--target-ns` | 目标延迟（ns），用于绘制目标线 | None |
| `--filter-pa` | PA 前缀过滤 | None |
| `--min-req-id` | 最小 reqId | 2 |
| `--exclude-req-ids` | 排除的 reqId（逗号分隔） | 1,7 |

### 1.3 输出文件

输出为单个自包含的 HTML 文件，可直接在浏览器中打开，无需外部依赖。

## 2. 视图说明

### 2.1 Timeline 视图

时间轴视图以泳道图形式展示每个请求的时间线：

- **横轴**: 绝对时间（ns），所有链路共享同一时间轴
- **纵轴**: 每个请求一行，按开始时间排序
- **色块**: 表示不同类型的延迟段

**色块颜色含义**:

| 颜色 | 类型 | 说明 |
|------|------|------|
| 🔵 蓝色 | gem5→ubio | gem5 发送到 ubio |
| 🟢 绿色 | ubio→gem5 | ubio 发送到 gem5 |
| 🟠 橙色 | ubio→nsim | ubio 发送到网络 |
| 🟣 紫色 | nsim_link | **网络链路延迟**（配置的延迟） |
| ⚪ 灰色 | nsim_sync | PDES 同步对齐（非真实延迟） |
| 🟡 黄色 | ubio_proc | ubio 处理时间 |
| 🟢 深绿 | gem5_proc | gem5 内部处理 |

### 2.2 Flow Diagram 视图

流图视图显示请求在组件间的传播路径：

```
gem5_1 → ubio_0 → nsim_1 → nsim_0 → ubio_0 → gem5_0 → ...
```

每个节点显示组件名和节点 ID，箭头表示消息流向。

### 2.3 Latency Breakdown 视图

延迟分解视图以堆叠条形图形式展示每个请求的时间组成：

- 每个色块代表一段延迟
- 悬停显示具体时间和占比
- 下方显示图例和各段累计时间

### 2.4 Statistics 视图

统计视图显示整体延迟分布：

- P50/P99/Mean 延迟
- 按请求类型的延迟统计
- 最小/最大延迟

## 3. 交互功能

### 3.1 过滤器

| 过滤器 | 说明 |
|--------|------|
| PA | 按 PA 前缀过滤 |
| reqId | 按 reqId 前缀过滤 |
| Type | 按请求类型过滤（ReadReq/UpgradeReq/WriteReq/ClearReq） |
| Min hops | 最小跳数过滤 |
| Zoom | 时间轴缩放（1x-50x） |

### 3.2 链路选择

- 点击左侧链路列表中的条目，高亮显示该链路
- 在 Flow Diagram 和 Latency Breakdown 视图中，选中的链路会单独显示
- 点击 "Expand/Collapse" 按钮展开/收起所有链路的详细事件表

### 3.3 事件详情

展开链路后，显示每个事件的详细信息：

| 列 | 说明 |
|----|------|
| tick | 时间戳（ps） |
| comp:event | 组件:事件类型 |
| node | 节点 ID |
| msg_type | 消息类型 |
| extra | 附加信息 |
| dt | 距上一事件的时间差 |

### 3.4 CSV 导出

点击 "Export CSV" 按钮，导出当前过滤条件下的链路数据为 CSV 文件。

## 4. 链路字段说明

### 4.1 基本字段

| 字段 | 说明 |
|------|------|
| key | 链路唯一标识（reqId:pa#instance） |
| reqId | 请求 ID |
| pa | 物理地址 |
| primary_type | 主要消息类型 |
| category | 消息类别（含 write intent 检测） |
| is_clear | 是否为 ClearReq/ClearResp 链路 |

### 4.2 时间字段

| 字段 | 说明 |
|------|------|
| first_tick | 第一个事件的时间戳（ps） |
| response_tick | 响应到达源节点的时间戳（ps） |
| dur_ps / dur_ns | 从请求发送到响应到达的持续时间 |
| crit_ps / crit_ns | 关键路径延迟（nsim_link 段之和） |

### 4.3 关键路径计算

**关键路径 (critical_path)** = 所有 `nsim_link` 段（nsim RECV → nsim FWD）的时间之和

这代表了真实的网络链路延迟，不包括：
- PDES 同步对齐时间（nsim_sync）
- ubio 处理时间（ubio_proc）
- gem5 处理时间（gem5_proc）

## 5. 准确性验证

### 5.1 验证结果

| 测试用例 | 链路数 | 跨节点 ReadReq | P50 延迟 | 关键路径 |
|----------|--------|----------------|----------|----------|
| TC121 | 139 | 4 | 875ns | 820ns |
| TC120 | 1405 | 64 | 1713ns | 820ns |
| TC124 | 1595 | 62 | 832ns | 820ns |

### 5.2 延迟组成分析

以 TC121 的跨节点 ReadReq 为例：

```
总延迟: 859.0ns
├── gem5→ubio: 2.5ns (Tq)
├── nsim_link: 410.0ns (第一跳)
├── nsim_sync: 4.0ns (PDES 同步)
├── ubio_proc: 2.5ns (处理)
├── ubio→gem5: 24.0ns (RecallReq)
├── ... (RecallResp 处理)
├── nsim_link: 410.0ns (第二跳)
└── nsim_sync: 3.5ns (PDES 同步)

关键路径: 820ns = 2 × 410ns
```

### 5.3 与目标对比

| 指标 | 目标 | 实测 | 状态 |
|------|------|------|------|
| 跨 Node IO hop | 415ns | 410ns | ✅ 达标 |
| Tq (ZMQ IPC) | ~100ns | ~100ns | ✅ 达标 |
| 总 IO hop (往返) | ~830ns | ~820ns | ✅ 达标 |

## 6. 与原有工具的对比

| 特性 | trace2chain + chain2html | trace_visualizer |
|------|--------------------------|------------------|
| 延迟测量 | 包含 ClearReq 时间 | 仅测量到响应到达 |
| ClearReq 处理 | 混入 ReadReq 链路 | 分离为独立链路 |
| 视图类型 | 仅时间轴 | 时间轴 + 流图 + 延迟分解 + 统计 |
| 因果关系 | 不显示 | 流图显示组件间传播 |
| 事件去重 | 无 | 自动去重 |
| 响应检测 | 未实现 | 检测响应到达源节点 |

## 7. 高级用法

### 7.1 分析特定请求

```bash
# 只看特定 PA 的请求
python3 scripts/trace_visualizer.py --filter-pa 0x10000000 logs/20260719_061438_1s > pa_filtered.html

# 只看特定 reqId 范围
python3 scripts/trace_visualizer.py --min-req-id 72057594037927937 logs/20260719_061438_1s > rid_filtered.html
```

### 7.2 比较不同配置

```bash
# 生成两个配置的可视化
python3 scripts/trace_visualizer.py --target-ns 415 logs/20260719_061417_1s > baseline.html
python3 scripts/trace_visualizer.py --target-ns 415 logs/20260719_061438_1s > optimized.html

# 在浏览器中打开两个文件对比
```

### 7.3 批量生成

```bash
# 为所有 TC121 运行生成可视化
for dir in logs/*_1s; do
    if [ -f "$dir/verify_tc121.log" ]; then
        name=$(basename "$dir")
        python3 scripts/trace_visualizer.py "$dir" > "viz_${name}_tc121.html"
    fi
done
```

## 8. 故障排除

### 8.1 无链路显示

- 检查日志目录是否包含 TRACE-PERF 行
- 尝试降低 Min hops 过滤值
- 检查 PA/reqId 过滤条件

### 8.2 延迟异常大

- 检查是否混入了多个测试用例的日志
- 使用 PA 过滤隔离特定请求
- 查看展开的事件表，确认是否有异常事件

### 8.3 跨节点链路缺失

- 确认日志中包含 nsim 事件
- 检查 Min hops 是否设置过高（跨节点至少需要 2 跳）

## 9. 技术实现

### 9.1 链路构建算法

1. **事件去重**: 基于 (tick, node, comp, reqId, pa, event, extra) 去重
2. **生命周期检测**: gem5 SEND ReadReq/UpgradeReq/... 标记链路开始
3. **ClearReq 隔离**: ClearReq/ClearResp 单独建链
4. **响应检测**: 在源节点查找 ubio RECV_NET / SEND_GEM5 / gem5 RECV
5. **nsim 关联**: nsim 事件通过 `last_pa_key_for_rid` 关联到正确的链路

### 9.2 关键路径计算

```python
crit_ps = 0
for i in range(response_event_idx):
    if events[i].comp == "nsim" and events[i+1].comp == "nsim":
        if events[i].event == "RECV" and events[i+1].event == "FWD":
            crit_ps += events[i+1].tick - events[i].tick
```

### 9.3 响应检测逻辑

对于跨节点请求（已见 nsim 事件）：
1. 在源节点查找 `ubio RECV_NET` + 匹配响应类型
2. 在源节点查找 `ubio SEND_GEM5` + 匹配响应类型
3. 在源节点查找 `gem5 RECV` + 匹配响应类型

## 10. 相关文档

- [Log Tracing Manual](../dev_manual/log_tracing_manual.md) — TRACE-PERF 管道使用手册
- [Perf Trace Guide](../dev_manual/perf_trace_guide.md) — 性能 tracing 与延迟校准指南
- [Latency Design](../measure/latency_design.md) — 延迟测量方案
