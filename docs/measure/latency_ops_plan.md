# 延迟校准与可视化 — 可执行操作方案（交付给实现方）

> 状态：待实施 | 依据：`docs/measure/latency_design.md` + 2026-07-08 讨论决策
> 范围：本文档只覆盖**延迟调参**与**可视化增强**两块，不含形式化验证。
> 执行约束：**所有编译/运行必须在 Docker 内进行**（见 `docs/dev_manual/split_mode_ops.md`）。
> 通则：每个任务先读引用的文件与行号再改；改完按"验收标准"逐条自检；不确定语义时保留原行为并加 `TODO`。

---

## 0. 背景决策速览（实现前必读）

| 编号 | 决策 | 影响 |
|------|------|------|
| D1 | ZMQ `linkLatency` 与 `syncInterval` **解耦**为两个独立可调值 | 但目标框架二者都是 **10ns**（10000 ps），恰好相等 |
| D2 | 约束方向 **`syncInterval >= linkLatency`** 必须始终成立 | 否则时钟前瞻窗口小于对端心跳步长，进程钟卡死/空转 |
| D3 | TRACE-PERF 埋点改用消息自带的 `hdr.timestamp`，不再用进程本地 `_tick` | 消除同步量化误差，nsim 段延迟直接=真实 link latency |
| D4 | 跨 Node+跨 Socket 的边**暂按单跳、用异构时延（两段相加）**实现；真实应为两跳多跳转发，必须留 `TODO` | 赶汇报的临时方案 |
| D5 | ZMQ 延迟**内化进端到端预算**：目标端到端 = ZMQ跳数×linkLatency + nsim段 + gem5内部 | 反解各段参数，trace 直接读端到端=目标 |
| D6 | DRAM 目标是 **core→local/NUMA DRAM 完整往返**，不是 memctrl 单段 | 打点/预算须覆盖 L1→L2→L3→SNF→MemCtrl→DDR4 全往返 |

**关键单位换算**：1 cycle @2GHz = 500 ps；1 ns = 1000 ps。全系统 tick = ps。

---

## 任务 A：ZMQ link latency / syncInterval 解耦并调至 10ns

### A.1 目标
把默认 `linkLatency` 从 100000 ps(100ns) 改为 10000 ps(10ns)，并让 `syncInterval` 成为**独立可配**项（默认也 10000，满足 `syncInterval >= linkLatency`）。

### A.2 涉及文件
- `framework/Port.hh:20-21`（`kDefaultSyncInterval`、`kDefaultLinkLatency`）
- `framework/Port.hh:40-43`（`PortRuntime` 结构）
- `framework/Port.cc:112-114`（`safeTs` 用 `_syncInterval`）、`:126`（`+_linkLatency` 打时间戳）、`:226`（`emitSync` 用 `_linkLatency` 节流）

### A.3 步骤
1. 将 `kDefaultLinkLatency` 与 `kDefaultSyncInterval` 均改为 `10000`。
2. 在两个常量上方加注释，写明：
   - 单位为 ps（10000 ps = 10ns）；
   - 二者语义不同：`linkLatency` = 每个 ZMQ 跳的物理时延 + 心跳发送间隔；`syncInterval` = 时钟前瞻窗口；
   - **不变约束：`syncInterval >= linkLatency`**（违反会导致进程钟无法推进）。
3. 确认没有任何进程用非默认 `PortRuntime` 覆盖（当前全仓库无覆盖，保持默认即可）。
4. 若希望可配：读环境变量 `EP_LINK_LATENCY_PS` / `EP_SYNC_INTERVAL_PS`（可选，非必需；若做则在 `Port::init` 里 fallback 到默认值，并在读到 `syncInterval < linkLatency` 时打印 `[PORT-CFG-WARN]` 并强制 `syncInterval = linkLatency`）。

### A.4 验收标准
- [ ] 编译通过（Docker 内 `build/bin/ubio`、`build/bin/networksim`、`gem5.opt` 全部重编）。
- [ ] 跑一次 1s 拓扑 E2E（TC1+TC2+TC5），进程**不死锁、正常跑完**（对照 `docs/dev_manual/split_mode_ops.md` 的启动流程）。
- [ ] 收集 TRACE-PERF 后，**单个 ZMQ 跳 Tq 中位数 ≈ 10ns**（原为 100ns）。
- [ ] 若实现了 env 覆盖：故意设 `EP_SYNC_INTERVAL_PS=5000 EP_LINK_LATENCY_PS=10000` 时，日志出现 `[PORT-CFG-WARN]` 且被纠正为相等，进程仍能跑完。

---

## 任务 B：TRACE-PERF 埋点改用 hdr.timestamp（消除量化误差）

### B.1 目标
让 nsim（及其它组件）打点的 tick 字段反映**消息的语义到达时间** `hdr.timestamp`，而不是进程本地 `_tick`，从而让相邻同向事件差 = 精确 link latency，不含同步量化抖动。

### B.2 涉及文件与当前行为
- `modules/networksim/networksim_main.cc:117-118`（RECV trace，当前打 `_tick`）
- `modules/networksim/networksim_main.cc:143-144`（FWD trace，当前打 `_tick`）
- 参考：`framework/Port.cc:126` 消息 `hdr.timestamp = sendTick + linkLatency`；`Port.cc:196` recv 时 `_lastRxT = tmp.hdr.timestamp`。

### B.3 步骤
1. nsim RECV 打点：把第一个字段 `_tick` 改为收到的消息 `m->hdr.timestamp`（`m` 在该作用域可得）。
2. nsim FWD 打点：把第一个字段 `_tick` 改为被转发消息 `pf.msg.hdr.timestamp`（即入队时记录的 readyTick/timestamp；注意区分：应打**该消息应到达/转发的语义时间戳**，不是出队时的 `_tick`）。
3. 同步核对 ubio 侧打点（`modules/ubiomodule/ubio_main.cc:279-282` 的 SEND_NET/SEND_GEM5，以及 RECV 打点）与 gem5 侧 UBAdapter 打点（`gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc` 的 SEND/RECV）：**统一改为使用消息 `hdr.timestamp`**，保证六个打点全用同一语义时间轴，才能对齐比较。
4. 若某些打点点位拿不到消息时间戳（如 SEND 发生在 timestamp 赋值之前），则打 `sendTick`（=赋值前的当前 tick），并在注释里注明该点是"发送发起时刻"而非"到达时刻"，供下游脚本正确计算段延迟。

### B.4 验收标准
- [ ] 编译通过并跑完 1s E2E。
- [ ] 收集后重新统计 nsim 段延迟：**cross-node link 的相邻 RECV→FWD 差应稳定 ≈ 配置的 link latency 值**，抖动（max−min）显著小于改前的 ~88ns（目标：抖动 < 2ns，即接近确定值）。
- [ ] 对比改前后同一 reqId 的链：端到端总时长不应因埋点改动而变化（埋点只改"读数口径"，不改物理行为）——若变化说明改错了字段。
- [ ] 文档 `docs/measure/latency_design.md` §5.3 的表格用新口径重测一版数据。

---

## 任务 C：gen_topo.py 支持异构 link latency（按 node/socket 分类）

### C.1 目标
`gen_topo.py` 能按 link 两端所属 (node, socket) 生成**不同的 latency**：
- 同 Node 同 Socket：不产生 nsim link（本地，走不到 nsim）。
- 同 Node 跨 Socket：`CROSS_SOCKET_LATENCY`。
- 跨 Node（含跨 Socket）：**`CROSS_NODE_LATENCY + CROSS_SOCKET_LATENCY`**（D4 的临时"两段相加"方案）。
- 跨 Node 同 Socket：`CROSS_NODE_LATENCY`。

### C.2 涉及文件
- `scripts/gen_topo.py`（当前 42 行，`--type 1s/2s`，统一 `latency=100000`，行 24-33）

### C.3 步骤
1. 新增参数：`--nodes N`（默认从 `--type` 推：1s/2s 都是 3 nodes）、`--sockets K`（1s→1，2s→2）。保留 `--type` 向后兼容，但内部换算成 `nmod = nodes * sockets`。
2. 新增 latency 常量参数（带默认值，单位 ps）：
   - `--cross-node-latency`（默认按 D5 预算反解，先占位如 405000，实测后迭代）
   - `--cross-socket-latency`（默认占位如 25000，实测后迭代）
3. 模块编号约定：`mod_id = node * K + socket`（与 `ubio_main.cc:214 gidOf` 一致，务必核对一致）。
4. 生成 link 时按两端计算 latency：
   ```
   node_a = a // K; sock_a = a % K
   node_b = b // K; sock_b = b % K
   if node_a != node_b and sock_a != sock_b: lat = cross_node + cross_socket   # D4 两段相加
   elif node_a != node_b:                     lat = cross_node
   else:                                      lat = cross_socket   # 同node跨socket
   ```
5. 在文件顶部与生成逻辑处加 **`TODO(2-hop)`** 注释：说明"跨node+跨socket 目前按单跳异构时延=两段之和，物理上应为两跳（inter-node + inter-socket）多跳转发，待 nsim 支持多跳后改回"。
6. 打印摘要时输出每类 link 的数量与 latency，便于核对。

### C.4 验收标准
- [ ] `python3 scripts/gen_topo.py --type 1s --out /tmp/t1.json` 生成 3 模块全 mesh，全部 link 为 `cross_node` latency（1s 无跨socket）。
- [ ] `python3 scripts/gen_topo.py --type 2s --out /tmp/t2.json` 生成 6 模块，link latency 出现 3 种值（cross-node、cross-socket、cross-node+cross-socket），且数量与手算一致。
- [ ] 生成的 JSON 能被 `networksim` 正确加载（配合任务 D）。
- [ ] `--nodes/--sockets` 显式传参与 `--type` 推导结果一致。

---

## 任务 D：networksim 路由改为 per-(src,dst) 异构时延

### D.1 目标
nsim 转发时按 **(源模块, 目标模块) 对**查 link latency，替换当前"按源模块取所有 link 最小值"的错误逻辑（否则任务 C 的异构 latency 会被 min 抹平失效）。

### D.2 涉及文件与当前问题
- `modules/networksim/networksim_main.cc:34`（`_linkLatency` 是 `map<int,uint64_t>` 按源）
- `:93-104`（`buildRoutes` 对每源取 min）
- `:120-122`（转发时按 `sourceId` 查 min）
- 已有但**未接入**的 `modules/networksim/ForwardTable.{hh,cc}`：已支持 `linkLatency(src,dst)` 邻接表，但其 `loadJson` 有"只找第一个 `]`"的解析 bug（`ForwardTable.cc:71` 用 `find(']')` 而非 `rfind`，与主文件 `:74` 注释描述的历史 bug 相同）。

### D.3 步骤（二选一，推荐方案 1）
**方案 1（推荐，改动小）**：在 `networksim_main.cc` 内把 `_linkLatency` 从 `map<int,uint64_t>` 改为 `map<pair<int,int>,uint64_t>`（键=(src,dst)）：
1. `loadTopology`/`buildRoutes`：对每条 link `[a,pa,b,pb,lat]` 同时记录 `(a,b)->lat` 与 `(b,a)->lat`（双向）。
2. 转发处（`:120-122`）：用 `(m->hdr.sourceId, m->hdr.targetId)` 查表；查不到时 fallback 到 `kDefaultLinkLatency` 并打一次告警。
3. 删除"取 min"逻辑。

**方案 2（用 ForwardTable，改动大）**：接入 `ForwardTable`，但**必须先修** `ForwardTable.cc:71` 的 `find(']')` → `rfind(']')`（否则只加载第一条 link），并统一端口/模块 id 语义。除非计划后续做真正多跳路由，否则不建议现在上方案 2。

### D.4 验收标准
- [ ] 用任务 C 的 2s topo 跑 nsim，日志确认**不同 (src,dst) 对使用了不同的 latency**（可临时加调试打印 src/dst/lat）。
- [ ] 加载 6 模块 topo 时**所有 15 条 link 都被加载**（回归 `ForwardTable`/`loadTopology` 的"只加载第一条"历史 bug）。
- [ ] 用任务 B 的新口径测量：跨 node 段延迟 ≈ `cross_node`，跨 socket 段 ≈ `cross_socket`，跨node+socket 段 ≈ 两者之和，各自误差在 link latency 的 ±2% 内。
- [ ] 代码中保留 `TODO(2-hop)` 注释与方案 D4 一致。

---

## 任务 E：端到端延迟预算标定（把 ZMQ 内化）

### E.1 目标
按 D5，把"ZMQ 跳数×linkLatency"当作物理预算的一部分，反解 nsim link latency 与 gem5 内部参数，使 trace 读出的**端到端**正好命中甲方目标。

### E.2 预算公式（跨 Node 同 Socket IO hop 为例，目标 415ns）
```
端到端 = (ZMQ跳数 × linkLatency) + nsim_link_latency + gem5内部(可忽略/另计)
```
- 若 ZMQ=10ns、跨节点 IO hop 走 4 个 ZMQ 跳 → ZMQ 贡献 40ns。
- 则 `nsim_link_latency ≈ 415 - 40 = 375ns`（具体跳数以任务 B 新口径实测为准）。
- 同理反解 cross-socket（目标 210~240ns）与 core→DRAM（100/110ns）各段。

### E.3 步骤
1. 用任务 B 新口径，先实测"纯 ZMQ 跳数×10ns"在各路径上的实际贡献（数清每条路径的 ZMQ 跳数）。
2. 反解出 `gen_topo.py` 的 `--cross-node-latency` / `--cross-socket-latency` 应设的值，回填任务 C 的默认值。
3. 迭代：跑 → 用新口径量端到端 → 与目标比 → 调 latency → 重跑，直到端到端落在目标 ±5% 内。
4. 把最终标定值与"预算分解表"写回 `docs/measure/latency_design.md` §5/§6。

### E.4 验收标准
- [ ] 跨 Node 同 Socket IO hop 端到端 ∈ [394, 436] ns（415±5%）。
- [ ] 同 Node 跨 Socket IO hop 端到端 ∈ 目标区间（210~240ns，含预算内化）。
- [ ] 预算分解表在文档中给出每段的贡献 ns 值，且加和=实测端到端（误差<5%）。
- [ ] core→DRAM 部分若本轮暂不做（需 gem5 内部打点），在文档标注"待 gem5 内部打点后标定"。

---

## 任务 F：可视化工具增强（chain2html.py / trace2chain.py）

> 前提：保持**单文件自包含 HTML**（无需构建、可直接分享），保持现有正确性。
> 涉及文件：`scripts/chain2html.py`（426行）、`scripts/trace2chain.py`（171行）。

### F.1 优先级 P0（汇报刚需，先做）

**F.1.1 时间轴刻度尺**
- 在泳道区顶部加**固定时间标尺**：ns 刻度 + 竖向网格线（对齐到色块坐标系）。
- 验收：任意泳道的任意色块，肉眼可从标尺读出其起止绝对时间（ns），刻度随缩放更新。

**F.1.2 超目标高亮**
- 端到端 `dur_ns > target_ns` 的泳道整行加红色左边框/角标"OVER"。
- 验收：给定 `--target-ns`，所有超标行被标红，未超标行不标；无 target 时不标。

**F.1.3 段内直接标注**
- 色块宽度足够（如 >40px）时，块内直接印 `+Xns`；不够宽时保持仅悬停显示。
- 验收：宽块显示数值且不溢出，窄块回退悬停，无文字重叠。

**F.1.4 分段聚合统计**
- 页面底部加聚合表：按 segment type（gem5→ubio / nsim_fifo / ...）统计 count / avg / p50 / p99（ns）。
- 验收：统计值与 `trace2chain` 原始数据手工抽样核对一致；筛选（PA/rid/hops）时统计随可见集合更新。

**F.1.5 CSV 导出**
- 加"导出 CSV"按钮，导出当前**可见**的 chains（rid, pa, type, dur_ns, tq_hops, ev_count）与/或每段明细。
- 验收：导出文件可被 Excel/pandas 正确解析，行数=当前可见 chain 数。

### F.2 优先级 P1（调参验证刚需）

**F.2.1 X 轴缩放/平移**
- 滚轮缩放、拖拽平移时间轴（替代当前固定 fit-to-width）。
- 验收：能放大到看清 10ns 级短段；平移不丢失色块；标尺同步缩放。

**F.2.2 对比模式**
- 支持加载两个 chains.json（改参前 vs 改参后），同一 rid 上下配对对齐展示；缺失方标"—"。
- 验收：同一 rid 两条链对齐同一时间原点，肉眼可比较各段延迟变化。

### F.3 优先级 P2（体验）
- filter 状态写入 URL hash（可分享视图）；PNG 截图导出；空数据占位提示；泳道按 duration/type/pa 排序与分组折叠。
- 验收：刷新页面后 filter 保持；chains 为空时显示"无数据"而非空白。

### F.4 正确性护栏（贯穿所有改动）
- 单位换算集中到**单一函数**（当前 `TICK2NS=1e-6` 散落多处），避免任务 B 改 timestamp 口径后换算出错。
- 数据自检：某 chain 事件 tick 非单调、或缺 SEND/RECV 配对时，泳道标黄并在悬停提示原因。
- 验收：构造一条乱序/缺配对的测试 chain，页面能标黄提示且不崩溃；正常 chain 不误报。

### F.5 总体验收
- [ ] 用真实 `logs/.../` 目录跑通 `trace2chain.py | chain2html.py` 全链路，产出 HTML 在浏览器正常渲染。
- [ ] P0 全部完成且验收通过；P1/P2 按时间推进。
- [ ] 改动后原有功能（悬停、展开、filter、target 线）不回归。

---

## 附录：任务依赖与建议顺序

```
A(ZMQ 10ns) ──┐
              ├─> E(端到端预算标定)  ← 依赖 B 的新口径 + C/D 的异构 latency
B(timestamp 口径) ──┤
C(gen_topo 异构) ──> D(nsim per-(src,dst) 路由) ──┘

F(可视化) 独立，可并行；但 F 验证效果依赖 A/B 产出的新 trace 数据
```

建议顺序：**B → A → (C, D) → E**，F 全程可并行。B 最先做因为它让所有后续测量可信。
