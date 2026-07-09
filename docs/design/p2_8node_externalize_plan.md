# P2: 8-节点扩展 + 参数外部化 + 独立静态库 — 方案文档 v2

> 日期: 2026-07-09 | 状态: 方案设计 | 替换 `p2_8node_externalize_plan.md`

---

## 1. UBCCController 参数管理（外部模块,不进 gem5 params）

UBCCController 在 `modules/ubiomodule/`，不在 `gem5/` 内。其参数通过启动时的 CLI (`argc/argv`) 注入:

**已有 CLI 参数** (`ubio_main.cc:587-591`):
```
--node=N, --socket=S, --num-sockets=K, --num-nodes=M
```

**需新增的 CLI 参数**:
| 参数 | 当前 | 改为 |
|------|------|------|
| `_recallTimeout` | 硬编码 `1000000` (UBCCController.hh:653) | `--recall-timeout=T` (ps) |
| `UBIO_DRAM_DELAY_PS` | 无 | `--dram-delay=PS` (P1 Q3 设计) |

修改量: ~10 行 (ubio_main.cc 解析 argv + 传给 UbioBackstoreHost)

---

## 2. EP+UBAdapter 独立静态库 — 不需要回调

**核心结论: 静态库 (.a) 只是目标文件归档,符号在 link proprietary binary 时解析。EP 模块可以继续使用 `curTick()`/`scheduleEvent()`/`fatal()` 等 gem5 API,只需要头文件。**

详细方案见 `docs/design/ep_static_lib_plan.md`。总工作量 **3.6 天**。

关键前提: proprietary gem5 保留 gem5 公共 API (curTick, scheduleEvent, Cycles, SimObject 基类)。如果它改了 SimObject API,需要预留 1 天做 thunk 适配层 (ifdef 条件编译)。

---

## 3. gem5/ 参数动态指定（编译后可通过配置文件/脚本修改）

### 3.1 不需要重编的参数（Python config 层）

以下参数在 `CHI_config.py` / `CHI_ubcc_framework.py` 中通过 Python 变量或 `_opt()` 读取,**改 .py 文件或传 CLI 参数即可,不需要重编 gem5 C++**:

| 参数 | 当前读取方式 | 动态化方案 |
|------|------------|-----------|
| L1/L2/L3 cache latencies | Python 类变量 | 改成从 `chi_params.json` 读取 |
| NoC router/link latencies | Python 类变量 | 同上 |
| `to_memory_controller_latency` | Python 赋值 `=20` | 移到 `chi_params.json` |
| `number_of_TBEs` / `repl_TBEs` / `snoop_TBEs` | Python 类变量 | 移到 `chi_params.json` |
| `ubcc_epoch_bits/bf_bytes/force_resident/meta_*` | `_opt()` 已外部化 ✅ | 保持 |
| `num_nodes/num_sockets/seg_size` | `_opt()` 已外部化 ✅ | 保持 |

### 3.2 需要重编 C++ 的参数（gem5 C++ 硬编码）

| 参数 | 文件:行 | 当前值 | 动态化方案 |
|------|--------|--------|-----------|
| EPSNFController retry 间隔 | `EPSNFController.cc:131,228,284,341,552` | `Cycles(1600000)` | 新增 SimObject Param `EP_RETRY_CYCLES` 或 env var |
| EPRNFController CompAck retry | `EPRNFController.cc:1360` | `Cycles(100000)` | 新增 Param `EPRN_COMPACK_RETRY_CYCLES` |
| EPRNFController wakeup retry | `EPRNFController.cc:1385` | `Cycles(1000000)` | 新增 Param `EPRN_WAKEUP_RETRY_CYCLES` |
| UBAdapter kWaitCap | `UBAdapter.cc:1237` | `2000000` | 新增 Param `UB_WAIT_CAP` |
| EPBackend 128MB seg size | `EPBackend.cc:102` | `128ULL*1024*1024` | 用 EPBackend.py 的 Param 替代(已有 `metadata_private_size`) |

### 3.3 实现: `chi_params.json`

```json
{
  "cache": {"l1i_data":1,"l1i_tag":1,"l1d_data":2,"l1d_tag":1,
            "l2_data":6,"l2_tag":2,"l3_data":10,"l3_tag":4},
  "noc": {"router_latency":1,"router_link_latency":1,"node_link_latency":1,
          "cross_link_latency":0,"cross_socket_latency":20},
  "snf": {"to_memory_controller_latency":88,"response_latency":2,"data_latency":1},
  "controller": {"hnf_tbes":4096,"snf_transitions_per_cycle":1024},
  "ep": {"retry_cycles":1600000,"eprn_compack_retry":100000,"eprn_wakeup_retry":1000000,
         "ub_wait_cap":2000000}
}
```

加载方式: `CHI_config.py` 开头 `with open('configs/ruby/chi_params.json') as f: params = json.load(f)`，然后用 `params['cache']['l1d_data']` 等替代硬编码值。

**对于 C++ 硬编码的 retry 参数**: 在取得 proprietary binary 之前先在开源 gem5 中把它们从硬编码改为 SimObject Param（从 `.py` 读取）。prop binary 如果是同一版本，会带着这些 Param。

---

## 4. 8 节点 Workload 设计（新增）

### 4.1 模仿 3 节点设计的 8 节点 workload

| 新 TC | 名称 | 模仿的 3 节点 TC | 功能 |
|-------|------|-----------------|------|
| **TC90** | `e2e_tc90_8node_all_to_all` | TC4 (three_node_ring) | 8 节点环形 owner 转移（N 次循环） |
| **TC91** | `e2e_tc91_8node_hotspot` | TC5 (single_writer) | 8 节点争用同一个 PA |
| **TC92** | `e2e_tc92_8node_butterfly` | TC6 (multi_sharer) | 8 节点 butterfly 数据迁移，多 sharer→单一 owner |
| **TC93** | `e2e_tc93_8node_pairwise_pingpong` | TC3 (pingpong) | 4 对节点同时 pingpong（8 节点饱和） |
| **TC94** | `e2e_tc94_8node_barrier_stress` | TC12 (sync_barrier) | 8 节点 barrier，多轮，验证 scale |

### 4.2 延迟测量型 workload

| 新 TC | 名称 | 功能 | 测量方式 |
|-------|------|------|---------|
| **TC80** | `e2e_tc80_cross_node_latency` | 单 PA 跨节点反复读(256 次)，记录往返延迟 | `cntvct_el0` delta + `[LATENCY]` tag |
| **TC81** | `e2e_tc81_cross_socket_latency` | 同 node 跨 socket 反复读，区分 same vs cross | 同上 |
| **TC82** | `e2e_tc82_8node_ring_latency` | 8 节点环形 owner 转移,每次转移延迟 | 同上 |

### 4.3 容量对比型 workload（验收目标 1）

| 新 TC | 名称 | 功能 |
|-------|------|------|
| **TC84** | `e2e_tc84_cacheline_capacity_vanilla` | baseline: 无 BF/backstore, SRAM=512KB, 统计最大 cacheline 数 |
| **TC85** | `e2e_tc85_cacheline_capacity_optimized` | BF+backstore 卸载, 同等 SRAM，与 TC84 对比 |

### 4.4 延迟测量精度分析

ARM `cntvct_el0` 是系统计数器，在 gem5 中映射为 `MISCREG_CNTVCT_EL0`。其速率由 `cntfrq_el0` 配置。

**精度**: gem5 中 cntvct_el0 每 tick 递增 1 次。gem5 `curTick()` 以 ps 为单位。cntvct_el0 的增量速率取决于 `cntfrq_el0` 寄存器。

ARM ARM 规范: `cntfrq_el0` 典型值 = 1MHz ~ 50MHz。在 gem5 默认配置下，cntvct 的增量频率可能与 `curTick()` 的 1ps/tick 不同步。

**准确性评估**:
- `cntvct_el0` 在 gem5 中**只反映 simulated time**（不是 wall-clock）
- 读一次 cntvct = 读一次 MISCREG → 约 1 个 gem5 系统调用
- 连续两次 cntvct 读的差 = 两次读之间经过的 simulated ticks（按 cntfrq 换算）
- **如果 gem5 的 cntfrq 配置为 1GHz,则 1 cntvct tick = 1ns。精度约 ±单个指令执行时间**

**限制**: 对于 <100ns 的延迟测量，cntvct_el0 在 gem5 中的精度受限于模拟器的事件粒度（每次 CPU 指令执行只在 event 边界更新）。单次 `ldr` 延迟可能被量化为最近的 event 边界。**建议用多次采样取平均值来降噪。** 对于 ≥100ns 的延迟（如跨节点 415ns），精度足够。

**更好的方案**: 用 trace 分析做延迟测量（如你之前说的），比 workload 内 cntvct 更精确——trace 可以精确到 ps。cntvct workload 只在没有 trace 时做近似测量。

---

## 5. 工作量汇总

| 阶段 | 内容 | 工时 |
|------|------|------|
| P2-1 | UBCCController num_nodes 硬编码修复 → `--num-nodes` CLI | 0.2 天 |
| P2-2 | chi_params.json + CHI_config.py 加载 + C++ retry params 外部化 | 1 天 |
| P2-3 | 新增 8n workload (TC90-94 + TC80-82 + TC84-85) | 1.5 天 |
| P2-4 | gen_topo 移除 3-node 默认 | 0.3 天 |
| P2-5 | EP 独立静态库 (见 `docs/design/ep_static_lib_plan.md`) | 3.6 天 |
| P2-6 | 验证: 8n1s/8n2s 全 topology TC32+ 通过 | 1 天 |
| **合计** | | **7.6 天** |

---

## 6. 要新增的文件清单

| 文件 | 用途 |
|------|------|
| `configs/topo_8n1s.json` | 8 nodes × 1 socket topology ✅ 已生成 |
| `configs/topo_8n2s.json` | 8 nodes × 2 sockets topology ✅ 已生成 |
| `gem5/configs/ruby/chi_params.json` | gem5 延迟/容量参数集中配置 |
| `libep_module/` 目录 | EP 独立静态库项目 |
| `tests/e2e/workloads/e2e_tc80_*.c` ~ `tc82_*.c` | 延迟测量 workload |
| `tests/e2e/workloads/e2e_tc84_*.c` ~ `tc85_*.c` | 容量对比 workload |
| `tests/e2e/workloads/e2e_tc90_*.c` ~ `tc94_*.c` | 8 节点 workload |

---

## 7. 待确认事项

1. 延迟测量型 TC80-82 是否加到 P2（虽然 cntvct 精度有限，但比没有好）?
2. 容量对比型 TC84-85 需要 vanilla(无 BF/backstore) 配置——这个配置如何切换?（方案: 环境变量 `UBCC_BF_BYTES=0 UBCC_FORCE_RESIDENT=MAX`）
3. EP 独立静态库 — 是否接受"假设 proprietary gem5 保留公共 API"的前提?
4. `chi_params.json` 的方案是否接受? 还是继续用 Python `_opt()` 逐个读取?
