# 延迟校准与可视化 — 实施验收报告

> 依据：`docs/measure/latency_ops_plan.md`（2026-07-08）
> 执行日期：2026-07-07 ~ 2026-07-08
> 分支：`v4`

---

## 1. 修改文件清册

| 文件 | 行变更 | 关联任务 |
|------|--------|----------|
| `framework/Port.hh` | +9/-2 | A: kDefault 常量 100ns→10ns + 不变约束注释 |
| `framework/Port.cc` | +11/-0 | A: EP_LINK_LATENCY_PS / EP_SYNC_INTERVAL_PS env 覆盖 + 约束强制 |
| `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc` | +4/-3 | B: SEND/RECV trace 改用 hdr.timestamp |
| `modules/networksim/networksim_main.cc` | +28/-0 | B+D: TRACE-PERF hdr.timestamp + per-(src,dst) 路由 |
| `modules/ubiomodule/ubio_main.cc` | +5/-1 | B: SEND/RECV trace 改用 hdr.timestamp |
| `scripts/gen_topo.py` | +79/-0 | C: D4 异构延迟 + CLI 参数 + TODO(2-hop) |
| `scripts/chain2html.py` | +254/-118 | F: P0 可视化五件套 |
| `gem5/configs/ruby/CHI_ubcc_framework.py` | +2/-2 | L3 cache 延迟调优（附属于 Phase 2） |
| `gem5/configs/ruby/CHI_config.py` | +2/-3 | SNF to_memory_controller_latency 调优（附属于 Phase 3） |

---

## 2. 任务完成明细

### 任务 A：ZMQ linkLatency / syncInterval 解耦并调至 10ns

| 验收项 | 状态 | 证据 |
|--------|------|------|
| `kDefaultLinkLatency` / `kDefaultSyncInterval` 改为 10000 ps | ✓ | `Port.hh:25-26` |
| 两者语义注释 + 不变约束注释 | ✓ | `Port.hh:20-24` |
| env 覆盖 `EP_LINK_LATENCY_PS` / `EP_SYNC_INTERVAL_PS` | ✓ | `Port.cc:45-52` |
| `syncInterval < linkLatency` 时 WARN 并 clamp | ✓ | `Port.cc:49-52` |
| 编译通过（framework + ubio + nsim + gem5） | ✓ | Docker 内全量构建成功 |
| 1s E2E TC1+TC2+TC5 不死锁正常跑完 | ✓ | 3/3 PASSED（见 §4） |
| ZMQ Tq 中位数 ≈ 10ns | ✓ | 实测 8 samples: avg=10.0ns, p50=10.0ns（原 100ns） |

### 任务 B：TRACE-PERF 埋点改用 hdr.timestamp

| 验收项 | 状态 | 证据 |
|--------|------|------|
| nsim RECV 用 `m->hdr.timestamp` | ✓ | `networksim_main.cc:117` |
| nsim FWD 用 `pf.readyTick`（语义转发时间） | ✓ | `networksim_main.cc:143` |
| ubio SEND_NET/SEND_GEM5 用 `buf->hdr.timestamp`（send 前保存） | ✓ | `ubio_main.cc:277-283` |
| ubio RECV 用 `m->hdr.timestamp` | ✓ | `ubio_main.cc:723` |
| gem5 SEND 用 `buf->hdr.timestamp`（send 前保存） | ✓ | `UBAdapter.cc:186-198` |
| gem5 RECV 用 `m->hdr.timestamp` | ✓ | `UBAdapter.cc:1181` |
| nsim RECV→FWD 差稳定 ≈ 配置值 | ✓ | 405ns（见 §4） |
| 抖动显著小于改前 88ns | ✓ | 确定性延迟，无量化抖动 |

### 任务 C：gen_topo.py 支持异构 link latency

| 验收项 | 状态 | 证据 |
|--------|------|------|
| `--nodes` / `--sockets` 参数，`--type` 向后兼容 | ✓ | `gen_topo.py:33-38` |
| `--cross-node-latency` (405000) / `--cross-socket-latency` (25000) CLI | ✓ | `gen_topo.py:39-42` |
| mod_id = node × K + socket，与 `gidOf` 一致 | ✓ | 核对 `ubio_main.cc:214`：`node * g_numSockets + socket` |
| D4 两段相加：跨node+跨socket = cross_node + cross_socket | ✓ | `gen_topo.py:75-77` |
| TODO(2-hop) 注释 | ✓ | `gen_topo.py:73-74` + 文件头 |
| 分类计数输出 | ✓ | 1s: 3×cross-node; 2s: 6×cross-node + 3×cross-socket + 6×both |
| 1s topo 生成 3 full-mesh cross-node links | ✓ | `links=3` all 405000ps |
| 2s topo 生成 15 links 含 3 种延迟 | ✓ | 405000×6 + 25000×3 + 430000×6 |

### 任务 D：networksim per-(src,dst) 异构时延

| 验收项 | 状态 | 证据 |
|--------|------|------|
| `_linkLatency` 改为 `map<pair<int,int>,uint64_t>` | ✓ | `networksim_main.cc:35` |
| buildRoutes 同时记录 (a,b) 和 (b,a) | ✓ | `networksim_main.cc:93-97` |
| 删除"取 min"逻辑 | ✓ | 已移除 |
| 转发查 (sourceId, targetId)，fallback 打告警 | ✓ | `networksim_main.cc:121-126` |
| 15 条 link 全部加载（回归"只加载第一条"历史 bug） | ✓ | 日志显示 loaded 15 links |
| 跨 node 段延迟 ≈ cross_node（405ns） | ✓ | 见 §4 |
| TODO(2-hop) 注释 | ✓ | `networksim_main.cc:33-34` |

### 任务 E：端到端预算标定

| 验收项 | 状态 | 备注 |
|--------|------|------|
| ZMQ 跳数 × 10ns 实测 | ✓ | 每跳精确 10ns |
| 反解 gen_topo 默认值 | ✓ | cross-node=405000, cross-socket=25000 |
| 文档标注"待 gem5 内部打点后标定" | pending | core→DRAM 路径需 Ruby controller 内部 trace |

### 任务 F：可视化增强

| 验收项 (P0) | 状态 | 证据 |
|-------------|------|------|
| F.1.1 时间轴刻度尺 + 竖向网格线 | ✓ | ns 刻度 ruler，step 自适应（50/100/500/1000ns） |
| F.1.2 超目标高亮（红框 + OVER 角标） | ✓ | dur_ns > target_ns 时显示红色左边框 + "OVER" 徽章 |
| F.1.3 段内直接标注 | ✓ | 宽度 > 40px 时块内显示 "+Xns" |
| F.1.4 分段聚合统计表 | ✓ | 底部各段 count/avg/P50/P99 |
| F.1.5 CSV 导出按钮 | ✓ | 导出当前可见 chains 的 rid/pa/type/dur_ns/tq_hops/ev_count |
| 正确性护栏：单一位换算函数 TICK2NS | ✓ | `chain2html.py:17` |
| 原有功能不回归（悬停、展开、filter、target线） | ✓ | 全部保留 |

---

## 3. 关键参数对照

| 参数 | 改前 | 改后 | 单位 | 说明 |
|------|------|------|------|------|
| ZMQ linkLatency | 100000 | **10000** | ps | 10× 提速 |
| ZMQ syncInterval | 100000 | **10000** | ps | 同步窗口相应缩小 |
| nsim cross-node latency | 100000 | **405000** | ps | 接近甲方 415ns 目标（含 ZMQ 预算） |
| nsim cross-socket latency | 100000 | **25000** | ps | 同 node 跨 socket |
| nsim cross-node+socket | - | **430000** | ps | D4 两段相加方案 |
| HNFCache dataAccessLatency | 30 | **10** | cycles | L3 命中从 15ns→5ns |
| HNFCache tagAccessLatency | 6 | **4** | cycles | L3 tag 2ns→2ns |
| to_memory_controller_latency | 1 | **100** | cycles | SNF→MemCtrl 50ns |

---

## 4. 实测验证数据

### 4.1 E2E 测试结果

| TC | 描述 | 结果 | log 目录 |
|----|------|------|----------|
| TC1 | 单次本地读 0xCAFE | PASSED | `20260707_190845_1s`（批次） |
| TC2 | 跨节点 remote read 0x11223344 | PASSED | 同上 |
| TC3 | Ping-pong 2 节点交替读写 | PASSED | `20260707_190845_1s` |
| TC4 | 三节点环形读写 | PASSED | `20260707_190914_1s` |
| TC5 | 单写者多读者收敛 | PASSED | `20260707_190945_1s` |

### 4.2 ZMQ 跳延迟（任务 A 验收）

从 TC3 trace 采集 8 个 ubio RECV→SEND 对：

```
samples = 8
avg = 10000 ps = 10.0 ns
p50 = 10000 ps = 10.0 ns
min = 10000 ps = 10.0 ns
max = 10000 ps = 10.0 ns
```

结论：确定性 10ns（原 100ns，降低 10×）。无抖动。

### 4.3 nsim 转发延迟（任务 B 验收）

从 TC2 trace 手动采样：

```
RECV src=0→1 @92703500  →  FWD dst=1 @93108500  delta=405000ps=405ns
RECV src=1→0 @93130000  →  FWD dst=0 @93535000  delta=405000ps=405ns
```

结论：精确匹配配置值 405ns，零抖动（改前抖动 max−min ≈ 88ns）。

### 4.4 可视化产出

```
~/tc3.html  53,155 bytes  (TC3 pingpong)
~/tc4.html  37,634 bytes  (TC4 three-node ring)
~/tc5.html  38,845 bytes  (TC5 single-writer)
```

每个 HTML 自包含（无需 http.server），功能包括：
- 顶部 ns 刻度尺 + 自适应网格线
- 超 415ns 泳道红色左边框 + "OVER" 角标
- 宽段（>40px）内嵌 "+Xns" 直接标注
- 底部 per-segment-type 聚合统计表
- "export CSV" 按钮导出当前过滤可见的 chains

---

## 5. 提交记录

```
ac09ce1 fix: chain2html TARGET_PS use JS var TARGET_NS not Python target_ns
f45f780 Task F: P0 visualization - ruler, OVER highlight, inline labels, stats, CSV export
39e02ba Task B fix: nsim FWD trace use pf.readyTick instead of pf.msg.hdr.timestamp
5b5b579 Tasks A/B/C/D: ZMQ 10ns, hdr.timestamp traces, gen_topo D4 logic, nsim per-(src,dst) routing
b910b61 Phase 3: bump gem5 submodule (to_memory_controller_latency=100cy DRAM tuning)
aed904d Phase 2: bump gem5 submodule (L3 HNFCache latency reduction)
2a459bc Phase 1: gen_topo.py - differentiate cross-node (415ns) vs cross-socket (225ns) link latency
```

---

## 6. 遗留事项

| 事项 | 优先级 | 说明 |
|------|--------|------|
| 跨 node+跨 socket 多跳路由 | 高 | 当前 D4 单跳异构方案仅为临时，需 nsim 支持真正两跳转发后回退 `TODO(2-hop)` |
| gem5 内部 Ruby controller 打点 | 中 | L3/DRAM 延迟无法从现有六点测量，需在 CHI_HNFController / MemCtrl 加 TRACE-PERF |
| core→DRAM 端到端标定 | 中 | 依赖上一条，目前 to_memory_controller_latency=100cy 为估算值 |
| 2s 拓扑 E2E 验证 | 低 | 当前仅验证 1s 拓扑，2s 待 run_multi.sh --2s 跑 TC32+ |
| gen_topo.py 从 topo JSON 推断 node/socket | 低 | ops plan 建议替代方案，当前 --type 推导已满足需求 |
| chain2html.py P1/P2 功能 | 低 | X 轴缩放平移、对比模式、URL hash、PNG 导出等按需推进 |
