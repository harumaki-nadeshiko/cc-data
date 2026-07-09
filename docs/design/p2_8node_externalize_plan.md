# P2: 8-节点扩展 + 参数外部化 + 独立静态库 — 统一方案文档

> 日期: 2026-07-09 | 状态: 方案设计

---

## 0. P1 遗留 — TC 完备性基线

现有 56 个 TC (TC1-54 + TC11 + TC63-64),按 8 个维度分层覆盖:

| 维度 | TC 数量 | 覆盖状况 |
|------|---------|---------|
| A: 基本协议路径 (MESI 转移) | TC1-10 | ✅ 全覆盖 |
| B: 并发与竞争 (UPGRADE/snoop/barrier) | TC11-19 | ✅ |
| C: 目录/Backstore/Bloom 压力 | TC20-31 | ✅ |
| D: Dual-Socket 跨 socket | TC32-39 | ✅ |
| E: RECALL/孤儿/超时 | TC40-46 | ✅ |
| F: 故障注入 (drop/dup/reorder) | TC47-49 | ⚠️ 仅 3 种 × 1 消息类型(形式化已补 B1-B3) |
| G: 复杂应用 | TC50-54 | ⚠️ toy scale (56-90 行) |
| H: RECALL orphan 专项 | TC63-64 | ✅ |

**缺口**:
1. 缺乏延迟测量型 benchmark（用于验收指标 目标 1/2）
2. 缺乏 8 节点规模测试
3. 缺乏竞争性 benchmark（Cacheline 数量对比）

---

## 1. P2/Q1: 新增 Micro-Benchmark 设计

### 1.1 延迟测量型 (Latency)

| # | 名称 | 功能 | 验收指标 |
|---|------|------|---------|
| L1 | `e2e_tc80_cross_node_latency` | 单 PA 跨节点反复读/写，记录每次 ReadReq→ReadResp 往返，统计 avg/p50/p99 | 目标 2: baseline >=500ns 场景的延迟对比 |
| L2 | `e2e_tc81_cross_socket_latency` | 同 node 跨 socket 反复读，统计同 socket vs 跨 socket 延迟差 | P1 Q3 中 NUMA 10ns 差的验证 |
| L3 | `e2e_tc82_8node_ring_latency` | 8 节点环形 owner 转移，统计每次转移延迟 | 8 node 规模下跨节点延迟 |
| L4 | `e2e_tc83_cold_miss_latency` | 全冷 cache miss 场景（G_I→G_E），无 RECALL 干扰的纯延迟 | baseline 测量 |

### 1.2 容量压力型 (Capacity/Cacheline)

| # | 名称 | 功能 | 验收指标 |
|---|------|------|---------|
| C1 | `e2e_tc84_cacheline_capacity_vanilla` | 基线: vanilla UBCC(无 BF/backstore 卸载), SRAM=512KB, 统计 max cachelines | 目标 1 baseline |
| C2 | `e2e_tc85_cacheline_capacity_optimized` | 优化: BF+backstore 卸载, 同等 SRAM 容量, 统计 max cachelines | 目标 1: 与 C1 对比, +50% |
| C3 | `e2e_tc86_directory_eviction_stress` | 超过 SRAM 容量的连续 newline 访问, 统计 eviction 频率和时延 | 目录满后的行为 |

### 1.3 8 节点专项 (8-Node specific)

| # | 名称 | 功能 |
|---|------|------|
| N1 | `e2e_tc90_8node_all_to_all` | 8 节点全对全 DSM 读写, 验证无死锁 |
| N2 | `e2e_tc91_8node_hotspot` | 8 节点争用同一个 PA, 验证 owner churn 正确性 |
| N3 | `e2e_tc92_8node_butterfly` | 8 节点 butterfly 数据迁移模式 |

### 1.4 Workload 设计原则

- 所有新 workload 是自包含的单文件 C（与现有 TC 格式一致）
- 每个 workload 输出 `[READ_VAL]` / `[E2E_META]` 标签供 verify 脚本解析
- 延迟测量型用 ARM `cntvct_el0` 计时器统计 delta
- 不依赖共享宏 `-DNUM_NODES`（每个 workload 自己写死 8）

---

## 2. P2/Q2: 8 节点 2D Full-Mesh 拓扑

### 2.1 拓扑定义

当前 `gen_topo.py` 生成的就是 2D Full-Mesh: 所有 (node,socket) 平面之间一条直连 link,按 `(node_a, socket_a)` vs `(node_b, socket_b)` 分类设置延迟。

**8 节点 × 1 socket**:

```
NMOD = 8, 全交叉 = 28 links
所有 link 是 cross-node (same socket), 延迟 = L_node
```

**8 节点 × 2 sockets**:

```
NMOD = 16, 全交叉 = 120 links
  cross-node (same socket):   56 links, 延迟 = L_node
  cross-socket (same node):    8 links, 延迟 = L_sock  
  cross-node+socket (D4):     56 links, 延迟 = L_node + L_sock
```

### 2.2 需要改的硬编码点

| # | 文件:行 | 当前 | 改为 |
|---|--------|------|------|
| 1 | `CHI_basic_framework_config.py:19` | `DEFAULT_N = 3` | 保持默认 3，8n 通过 CLI 覆盖 |
| 2 | `UBCCController.cc:101` | `NodeAddressMap(3, kNumSockets, kSegSize)` | `NodeAddressMap(num_nodes, ...)` — **必须改**,3 硬编码 |
| 3 | `gen_topo.py:14,35-38` | `--type 1s/2s` → NMOD=3/6 | 已有 `--nodes`/`--sockets`,移除 3 的隐式默认 |
| 4 | `test_e2e.py:1160` | `-DNUM_NODES=3` | `f"-DNUM_NODES={num_nodes}"` |
| 5 | `run_multi.sh` | 无 8n 参数 | 新增 `--8n1s`/`--8n2s` 参数,对应 `configs/topo_8n1s.json`/`topo_8n2s.json` |

### 2.3 已新增的配置文件

- `configs/topo_8n1s.json` — 8 nodes × 1 socket × 8 gem5 + 8 ubio + 1 nsim
- `configs/topo_8n2s.json` — 8 nodes × 2 sockets × 8 gem5 + 16 ubio + 1 nsim

### 2.4 gen_topo 默认值调整

删除 `--type` 对 `--nodes` 的隐式推导。改为: 如果 `--nodes` 未给,从 topo JSON 读取 (run_multi.sh 已从 JSON 读取 NUM_NODES → 传给 gen_topo)。

### 2.5 IPE 端口数量

IPC 端口命名规则: `ipc:///workspace/gem5/shared_ipc/ipc_*`。8 节点 × 2 sockets = 16 ubio + 8 gem5 = 24 个模块,每个需要 2 个 IPC 端点 (rx+tx),总共 48 个端点。当前 PortEnvLoader 支持任意 (nodeId, socketId)。无需修改。

---

## 3. P2/Q3: gem5/ 内所有参数清单 + 外部化方案

### 3.1 完整参数清单（仅 gem5/ 目录内）

#### A. Cache 延迟 (CHI_config.py)

| 参数 | 当前值 | 行号 | 外部化方式 |
|------|--------|------|-----------|
| L1I dataAccessLatency | 1 cy | 63 | `chi_params.json` |
| L1I tagAccessLatency | 1 cy | 64 | `chi_params.json` |
| L1D dataAccessLatency | 2 cy | 68 | `chi_params.json` |
| L1D tagAccessLatency | 1 cy | 69 | `chi_params.json` |
| L2 dataAccessLatency | 6 cy | 73 | `chi_params.json` |
| L2 tagAccessLatency | 2 cy | 74 | `chi_params.json` |
| L3/HN-F dataAccessLatency | 10 cy | CHI_ubcc_framework.py:204 | `chi_params.json` |
| L3/HN-F tagAccessLatency | 4 cy | CHI_ubcc_framework.py:205 | `chi_params.json` |

#### B. NoC 参数 (CHI_config.py:102-116)

| 参数 | 当前值 | 外部化方式 |
|------|--------|-----------|
| router_link_latency | 1 cy | `chi_params.json` |
| node_link_latency | 1 cy | `chi_params.json` |
| router_latency | 1 cy | `chi_params.json` |
| router_buffer_size | 4 | `chi_params.json` |
| cntrl_msg_size | 8 | 固定(协议定义) |
| data_width | 32 | 固定(协议定义) |
| cross_link_latency | 0 cy | `chi_params.json` |

#### C. 内存控制器 (CHI_config.py:751)

| 参数 | 当前值 | 外部化方式 |
|------|--------|-----------|
| `to_memory_controller_latency` | 20 cy | `chi_params.json` (P1 Q3 建议 88cy) |

#### D. CHI 控制器容量 (CHI_config.py:278-281)

| 参数 | 当前值 | 外部化方式 |
|------|--------|-----------|
| `number_of_TBEs` | 16 | `chi_params.json` (可调,影响并发请求数) |
| `number_of_repl_TBEs` | 16 | `chi_params.json` |
| `number_of_snoop_TBEs` | 4 | `chi_params.json` |
| `number_of_DVM_TBEs` | 16 | `chi_params.json` |

#### E. EPBackend SimObject 参数 (EPBackend.py)

| 参数 | 当前默认值 | 外部化方式 |
|------|-----------|-----------|
| `num_nodes` | 3 | 已通过 `_opt("ubcc_num_nodes")` 读取 ✅ |
| `num_sockets` | 1 | 已通过 `_opt("ubcc_num_sockets")` 读取 ✅ |
| `ubcc_epoch_bits` | 64 | 已通过 `_opt` 读取 ✅ |
| `ubcc_bf_bytes` | 65536 (64KB) | 已通过 `_opt` 读取 ✅ |
| `ubcc_force_resident_entries` | 0 | 已通过 `_opt` 读取 ✅ |
| `ubcc_meta_max_flights` | 8 | 已通过 `_opt(ubcc_meta_max_flights, 8)` 读取 ✅ |
| `ubcc_meta_read_ticks` | 8000 | 已通过 `_opt` 读取 ✅ |
| `ubcc_meta_write_ticks` | 7500 | 已通过 `_opt` 读取 ✅ |
| `ubcc_meta_delete_ticks` | 7500 | 已通过 `_opt` 读取 ✅ |
| `metadata_private_size` | 16MB | 已通过 SimObject Param 传递 ✅ |

#### F. UBAdapter SimObject 参数 (UBAdapter.py)

| 参数 | 当前默认值 | 外部化方式 |
|------|-----------|-----------|
| `node_id` | 0 | 构建时传入 ✅ |
| `socket_id` | 0 | 构建时传入 ✅ |
| `num_nodes` | 3 | 需改: 从构造参数读取 `p.num_nodes` |
| `num_sockets` | 1 | 构建时传入 ✅ |

#### G. EPSNFController 硬编码 (C++)

| 参数 | 当前值 | 外部化方式 |
|------|--------|-----------|
| `scheduleEvent(Cycles(1600000))` retry 延迟 | 1600000 cycles (800µs) | `EP_RETRY_CYCLES` env var 或 UBAdapter params JSON |
| `kWaitCap` | 2000000 | `UB_WAIT_CAP` env var |

#### H. 其他硬编码常量

| 参数 | 位置 | 当前值 | 外部化 |
|------|------|--------|--------|
| `DEFAULT_SEG_SIZE` | CHI_basic_framework_config.py:22 | 128MB | 已可 override ✅ |
| `NODE_ADDR_SHIFT` | CHI_basic_framework_config.py:23 | 40 | 固定(由架构决定) |
| `seg_size` 硬编码 128MB | EPBackend.cc:102, UBCCController.cc:88 | 128MB | `EP_SEG_SIZE` env var |
| `deadlock_threshold` | CHI_ubcc_framework.py:434 | 200000000 | 已可 override ✅ |

### 3.2 外部化策略

**不需要重编 gem5 的参数**（已通过 Python `_opt()` / SimObject Param 读取）:
- 所有 EPBackend/UBAdapter SimObject 参数 (`num_nodes`, `num_sockets`, `ubcc_*`)
- CHI 容量参数 (`number_of_TBEs` 等)
- topology (`Crossbar` vs `CustomMesh`)

**需要重编 gem5 C++ 的参数**（硬编码在 .cc/.hh 中）:
- Cache latency class 变量 (`dataAccessLatency` 等) — Python 类变量,改 Python 不需要重编
- `to_memory_controller_latency` — Python 赋值,不需要重编
- `scheduleEvent(Cycles(1600000))` — C++ 硬编码,**需要重编**
- `UBCCController.cc:101` `NodeAddressMap(3, ...)` — C++ 硬编码,**需要重编**
- `EPBackend.cc:102` `128ULL * 1024 * 1024` seg size — C++ 硬编码

### 3.3 建议的外部配置文件

**`gem5/configs/ruby/chi_params.json`**:
```json
{
  "_comment": "CHI latency parameters — loaded by CHI_config.py at startup",
  "cache": {
    "l1i_data": 1, "l1i_tag": 1,
    "l1d_data": 2, "l1d_tag": 1,
    "l2_data": 6, "l2_tag": 2,
    "l3_data": 10, "l3_tag": 4
  },
  "noc": {
    "router_latency": 1, "router_link_latency": 1,
    "node_link_latency": 1, "cross_link_latency": 0,
    "cross_socket_latency": 20
  },
  "snf": {
    "to_memory_controller_latency": 88,
    "response_latency": 2, "data_latency": 1
  },
  "controller": {
    "hnf_tbes": 4096, "snf_transitions_per_cycle": 1024
  }
}
```

在取得 proprietary binary 后，只要它是同一个 gem5 版本且保留 Python config 机制，这个 JSON 仍然可读。

---

## 4. P2/Q4: EP + UBAdapter 独立静态库 — 方案与工作量

### 4.1 需要独立出来的源文件

| 文件 | 依赖 |
|------|------|
| `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.{cc,hh}` | gem5 Ruby/SimObject/Event/Proto |
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.{cc,hh}` | 同上 + UBAdapter + NodeAddressMap |
| `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.{cc,hh}` | framework/Port + gem5 SimObject/SyncWait |
| `gem5/src/mem/ruby/protocol/chi/ep/NodeAddressMap.{cc,hh}` | 自包含(纯计算) |
| `gem5/src/mem/ruby/protocol/chi/ep/CoherenceMessage.hh` | 协议定义 |
| `protocol/CoherenceMessage.hh` | 纯数据结构,无 gem5 依赖 |
| `gem5/src/mem/ruby/protocol/chi/ep/*.py` | SimObject Python 描述 |

### 4.2 对代码的修改（为独立编译）

**问题**: 这些文件 `#include` gem5 内部头文件（`base/logging.hh`, `sim/cur_tick.hh`, `sim/system.hh` 等）。为独立编译，需要:

| 修改项 | 说明 | TLOC |
|--------|------|------|
| 1. 提取 gem5 公共头文件集合 | 从开源 gem5 抓取 EP 模块依赖的所有 gem5 头文件,打包为 `include/gem5_pub/` | 0 (抓取,不改) |
| 2. 移除 `fatal()` / `warn()` 依赖 | 替换为 `fprintf(stderr,...)` + `exit(1)` 或回调 | ~20 行 |
| 3. `curTick()` 替换 | 当前 EP 模块用 `curTick()` 获取 gem5 当前时钟。改为通过函数指针/回调获取: `std::function<uint64_t()> getCurrentTick;` | ~10 行 |
| 4. `scheduleEvent()` 替换 | EPSNFController 调用 `scheduleEvent(Cycles(N))` 进行 retry。改为回调: `std::function<void(uint64_t delay_ps)> scheduleCallback;` | ~15 行 |
| 5. `DPRINTF` 替换 | gem5 debug trace → `fprintf(stderr, ...)` 或可选回调 | ~5 行 |
| 6. SimObject Python 描述保留 | `.py` 文件不编译,只是告诉 proprietary gem5 如何实例化。但 proprietary gem5 的 Python config 必须能 import 它们。可以把它们打包为 `ep_params.py` | 0 |
| 7. `System::syncWait` 依赖 | UBAdapter 在 barrier release 时需要 `System::systemList[0]->syncWait.releaseBarrier()`。改为回调: `std::function<void(uint32_t)> releaseBarrier;` | ~5 行 |

### 4.3 编译与集成流程

**取得 proprietary binary 之前**:
```
1. 从开源 gem5 提取 EP 模块源文件 + 依赖头文件
2. 修改 EP 模块,将所有 gem5 内部 API (fatal/warn/curTick/scheduleEvent/DPRINTF/SyncWait)
   替换为回调/函数指针
3. 编译为 libep_module.a (需要开源 gem5 头文件)
4. 编写 integrate.sh: 接收 proprietary gem5 头文件路径 + binary 路径,
   用 proprietary 头文件重编 libep_module.a,生成最终 gem5
```

**取得 proprietary binary 之后**:
```
1. proprietary binary 附带头文件 package (至少 SimObject/Event/Proto 头文件)
2. 运行 integrate.sh:
   a) 用 proprietary 头文件重编 libep_module.a
   b) 验证 ABI 兼容性 (symbol check)
   c) 将 libep_module.a link 进 proprietary gem5 (或作为 preload .so)
3. chi_params.json 放到 configs/ 目录
4. 运行测试
```

### 4.4 无法独立的部分（必须在 gem5 源码内修改）

| 项 | 原因 |
|----|------|
| `CHI_config.py` 中 `to_memory_controller_latency` 赋值 | Python config,不需要编译,但需要 gem5 源码树中的 `CHI_config.py` |
| `CHI_ubcc_framework.py` topology 构建 | Python config,同理 |
| `CHI-mem.sm` 的 `response_latency`/`data_latency` | SLICC 协议文件,编译后嵌入 binary,无法外部化 |
| SLICC 生成的 Controller 代码 | 编译后嵌入 binary |

### 4.5 工作量估计

| 阶段 | 任务 | 工作量 |
|------|------|--------|
| **提取** | 收集 EP 模块全部源文件 + 依赖头文件列表 | 0.5 天 |
| **解耦** | fatal/DPRINTF → fprintf, curTick/scheduleEvent → 回调 | 1 天 |
| **构建** | 编写 CMakeLists.txt 编译 `libep_module.a` | 0.5 天 |
| **集成脚本** | `integrate.sh` 自动化: 用 proprietary 头文件重编 + ABI 检查 | 0.5 天 |
| **测试** | 与开源 gem5 link 验证功能不变 | 1 天 |
| **合计** | | **3.5 天** |

---

## 5. P2 执行计划

| Phase | 内容 | 预计工期 |
|-------|------|---------|
| P2-1 | 修 UBCCController.cc(3→num_nodes), gen_topo.py(移除3默认) | 0.5 天 |
| P2-2 | 新增 8 节点 workload (N1-N3) + 8n topology 配置 | 1 天 |
| P2-3 | chi_params.json + CHI_config.py 加载逻辑 | 0.5 天 |
| P2-4 | EP 模块解耦 + libep_module.a | 3.5 天 |
| P2-5 | 验证: 8 节点 1s/2s 全 topology 通过 TC32+ | 1 天 |

**总计: 6.5 天**

---

## 6. 待确认事项（P2 QFin）

1. 8 节点 workload 设计(N1-N3)是否充分?需要额外几个?
2. 延迟测量型(L1-L4)+容量压力型(C1-C3)是否加入 P2 还是留到 P3?
3. `chi_params.json` 方案是否替代直接修改 `CHI_config.py` 类变量的方式?
4. EP 模块独立静态库的优先级 — 是否需要等 proprietary binary 前最后做?
5. `UBCCController.cc:101` 的 `NodeAddressMap(3,...)` 硬编码修复是否现在就做?
