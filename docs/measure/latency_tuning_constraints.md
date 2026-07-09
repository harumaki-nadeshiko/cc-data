# 延迟参数微调方案 — 约束方程组与求解

> 状态：方案设计期 | 最后更新：2026-07-09
> 依据：P1 Q1/Q2 链路分析 + Ground Truth 目标 + 2026-07-09 讨论决策

---

## 1. 实际访存路径（经 trace 验证）

### 1.1 私有 DRAM 访存（非 DSM）

```
CPU → L1(miss) → L2(miss) → L3/HN-F(miss) → NoC → L_SNF → MemCtrl → DDR4 → 原路返回
```

- **same socket**: RNF → HN-F(本socket) → L_SNF(本node共享) → DDR4
- **cross socket (NUMA)**: RNF → NoC跨socket → HN-F(异socket) → L_SNF(本node共享) → DDR4
- 差异体现在 **RNF→HNF→SNF 的 NoC 路径**上,不在 nsim 上

### 1.2 DSM DRAM 访存（经 ubio）

```
CPU → L1(miss) → L2(miss) → L3/HN-F(miss) → EP_SNF(零延迟) → EPBackend → UBAdapter → ZMQ(X) → ubio(home) → backstore读(T_ubio_dram) → ZMQ(X) → 原路返回
```

- **same socket home**: UBAdapter(本socket) → ZMQ → ubio(本socket) → 本地处理 → 返回
- **cross socket home (NUMA)**: RNF → NoC跨socket → HN-F(异socket) → EP_SNF(异socket) → UBAdapter(异socket) → ZMQ → ubio(异socket) → 返回
- 差异同样在 **RNF→HNF 的 NoC 跨 socket 路径**上

### 1.3 UBIO-UBIO 间消息（跨 socket / 跨 node）

```
ubio(A) → ZMQ(X) → nsim(L) → ZMQ(X) → ubio(B)
```

- **跨 Node**: `2*X + L_node` = 一跳端到端
- **同 Node 跨 Socket**: `2*X + L_sock` = 一跳端到端
- **跨 Node + 跨 Socket（D4 临时合并）**: `2*X + L_node_sock`，其中 `L_node_sock = L_node + L_sock`

---

## 2. 自由参数清单

| 符号 | 含义 | 单位 | 当前值 | 源代码位置 |
|------|------|------|--------|-----------|
| `X` | ZMQ linkLatency = syncInterval | ps | 100000 | `framework/Port.hh:25-26` `kDefaultLinkLatency` / `kDefaultSyncInterval` |
| `L_node` | nsim cross-node link latency | ps | 405000 | `scripts/gen_topo.py` `--cross-node-latency` default |
| `L_sock` | nsim cross-socket link latency | ps | 25000 | `scripts/gen_topo.py` `--cross-socket-latency` default |
| `L1d_tag` | L1D tag access latency | cycles | 1 | `gem5/configs/ruby/CHI_config.py:69` `L1DCache.tagAccessLatency` |
| `L1d_data` | L1D data access latency | cycles | 2 | `gem5/configs/ruby/CHI_config.py:68` `L1DCache.dataAccessLatency` |
| `L2_tag` | L2 tag access latency | cycles | 2 | `gem5/configs/ruby/CHI_config.py:74` `L2Cache.tagAccessLatency` |
| `L2_data` | L2 data access latency | cycles | 6 | `gem5/configs/ruby/CHI_config.py:73` `L2Cache.dataAccessLatency` |
| `L3_tag` | HN-F L3 tag access latency | cycles | 4 | `gem5/configs/ruby/CHI_ubcc_framework.py:204` `HNFCache.tagAccessLatency` |
| `L3_data` | HN-F L3 data access latency | cycles | 10 | `gem5/configs/ruby/CHI_ubcc_framework.py:203` `HNFCache.dataAccessLatency` |
| `R_lat` | NoC router latency | cycles | 1 | `gem5/configs/ruby/CHI_config.py:111` `NoC_Params.router_latency` |
| `RL_lat` | NoC router_link latency | cycles | 1 | `gem5/configs/ruby/CHI_config.py:109` `NoC_Params.router_link_latency` |
| `NL_lat` | NoC node_link latency | cycles | 1 | `gem5/configs/ruby/CHI_config.py:110` `NoC_Params.node_link_latency` |
| `T_mem` | SN-F→MemCtrl 出向延迟 | cycles | 20 | `gem5/configs/ruby/CHI_config.py:751` `to_memory_controller_latency` |
| `R_resp` | SN-F response enqueue latency | cycles | 2 | `gem5/src/mem/ruby/protocol/chi/CHI-mem.sm:43` `response_latency` |
| `D_resp` | SN-F data enqueue latency | cycles | 1 | `gem5/src/mem/ruby/protocol/chi/CHI-mem.sm:44` `data_latency` |
| `tCL` | DDR4 CAS latency | ns | 13.75 | `gem5/src/mem/DRAMInterface.py:317` (DDR4_2400, 固定) |
| `tRCD` | DDR4 RAS→CAS delay | ns | 13.75 | `gem5/src/mem/DRAMInterface.py:316` (DDR4_2400, 固定) |
| `Δ_noc` | cross-socket NoC 额外延迟 | ns | 不存在 | **需新增**: HN-F 路由到异侧 socket 的 SNF 时额外延迟 |
| `T_ubio_dram` | ubio backstore 读延迟 | ns | 0 | **需新增**: `modules/ubiomodule/ubio_main.cc` `hostIssueBackstoreRead` 中注入 |

### 固定常量

| 常量 | 值 | 说明 |
|------|-----|------|
| `CYCLE_NS` | 0.5 | 1 cycle @2GHz = 0.5ns |
| `N_noc_hops` | 2 | CPU→HN-F 的典型 NoC 路由跳数（可调整） |

---

## 3. 约束方程组与不等式组

### 3.1 不变约束（安全/物理限制）

```
(C1)  syncInterval >= linkLatency              即 X >= X (恒成立,二者取相同值)
(C2)  X >= 0
(C3)  L_node >= 0
(C4)  L_sock >= 0
(C5)  L1d_tag >= 1, L1d_data >= 1
(C6)  L2_tag >= 1, L2_data >= 1
(C7)  L3_tag >= 1, L3_data >= 1
(C8)  R_lat >= 1, RL_lat >= 0, NL_lat >= 0
(C9)  T_mem >= 0
(C10) R_resp >= 1, D_resp >= 0
(C11) Δ_noc >= 0
(C12) T_ubio_dram >= 0
(C13) tCL = 13.75ns, tRCD = 13.75ns           (DDR4_2400_8x8 固定)
```

### 3.2 目标方程

**目标 1: core → Local Soc L3 hit = 15ns**

路径: CPU → L1(miss) → L2(miss) → L3(hit) → 返回

```
(E1)  (L1d_tag + L1d_data + L2_tag + L2_data + L3_tag + L3_data) * CYCLE_NS
        + N_noc_hops * (R_lat + NL_lat) * CYCLE_NS
      = 15ns
```

**目标 2: core → Local DRAM (same socket) = 100ns**

路径: CPU → L1(miss) → L2(miss) → L3(miss) → L_SNF → MemCtrl → DDR4 → 返回

```
(E2)  2 * (L1d_tag + L1d_data + L2_tag + L2_data + L3_tag + L3_data) * CYCLE_NS
        + T_mem * CYCLE_NS
        + (tRCD + tCL)
        + (R_resp + D_resp) * CYCLE_NS
        + N_noc_hops * (R_lat + NL_lat) * CYCLE_NS
      = 100ns
```

**目标 3: core → NUMA DRAM (cross-socket same node) = 110ns**

```
(E3)  E2_result + Δ_noc = 110ns
      → Δ_noc = 10ns
```

**目标 4: core → DSM DRAM (same socket home) = 100ns**

路径: CPU → cache miss chain → EP_SNF → UBAdapter → ZMQ(X) → ubio → backstore(T_ubio_dram) → ZMQ(X) → 返回

```
(E4)  2 * (L1d_tag + L1d_data + L2_tag + L2_data + L3_tag + L3_data) * CYCLE_NS
        + N_noc_hops * (R_lat + NL_lat) * CYCLE_NS
        + 2 * X * 1e-3
        + T_ubio_dram
      = 100ns
```

（`X` 单位为 ps,`X * 1e-3` 转为 ns）

**目标 5: core → DSM DRAM (cross-socket same node) = 110ns**

```
(E5)  E4_result + Δ_noc = 110ns
      → Δ_noc = 10ns  (与 E3 一致)
```

**目标 6: Inter-Node UBIO-UBIO = 415ns**

```
(E6)  2 * X * 1e-3 + L_node * 1e-3 = 415ns
```

**目标 7: Intra-Node UBIO-UBIO = 210~240ns**

```
(E7)  210ns <= 2 * X * 1e-3 + L_sock * 1e-3 <= 240ns
```

**目标 8: D4 临时合并 — cross-node + cross-socket UBIO-UBIO**

跨 node 且跨 socket 的消息走 nsim 一跳,延迟 = L_node + L_sock:

```
(E8)  2 * X * 1e-3 + (L_node + L_sock) * 1e-3
      = (E6结果) + (E7结果) - 2 * X * 1e-3
      = 415 + 225 - 2*X*1e-3
      = 640 - 2*X*1e-3  ns
```

即 D4 合并后的一跳延迟 = `2*X + L_node + L_sock`,等于两条独立路径之和减去重复的 `2*X`。

---

## 4. 求解逻辑

### 4.1 由 E6 和 E7 解出 L_node 和 L_sock

```
L_node = (415 - 2*X_ns) * 1000   ps    其中 X_ns = X * 1e-3 (ns)
L_sock = (225 - 2*X_ns) * 1000   ps    (取 210~240 中值 225ns)
```

合法性约束:
```
L_node >= 0  →  X_ns <= 207.5
L_sock >= 0  →  X_ns <= 112.5
```

### 4.2 由 E1 解 cache 参数

当前: `(1+2+2+6+4+10) * 0.5 + 2*(1+1)*0.5 = 12.5 + 2 = 14.5ns ≈ 15ns`

**cache 参数已满足,不需调整。**

### 4.3 由 E2 解 T_mem

```
T_mem = (100 - 2*cache_chain_ns - (tRCD+tCL) - (R_resp+D_resp)*0.5 - N_noc*(R_lat+NL_lat)*0.5) / CYCLE_NS

cache_chain_ns = (1+2+2+6+4+10) * 0.5 = 12.5
→ T_mem = (100 - 25 - 27.5 - 1.5 - 2) / 0.5 = 44 / 0.5 = 88 cy
```

### 4.4 由 E3/E5 解 Δ_noc

```
Δ_noc = 110 - 100 = 10ns = 20 cy
```

### 4.5 由 E4 解 T_ubio_dram

```
T_ubio_dram = 100 - 2*cache_chain_ns - N_noc*(R_lat+NL_lat)*0.5 - 2*X_ns
            = 100 - 25 - 2 - 2*X_ns
            = 73 - 2*X_ns
```

合法性: `T_ubio_dram >= 0 → X_ns <= 36.5`

### 4.6 X 的可行域

综合所有约束:
```
X_ns <= 36.5    (from T_ubio_dram >= 0)
X_ns <= 112.5   (from L_sock >= 0)
X_ns <= 207.5   (from L_node >= 0)
→ X_ns <= 36.5  (最紧约束)
```

---

## 5. 示例解

| X (ns) | L_node (ns) | L_sock (ns) | L_node_sock (ns) | T_mem (cy) | T_ubio_dram (ns) | Δ_noc (ns) |
|--------|-------------|------------|-------------------|-----------|-------------------|-----------|
| 10 | 395 | 205 | 600 | 88 | 53 | 10 |
| 20 | 375 | 185 | 560 | 88 | 33 | 10 |
| 30 | 355 | 165 | 520 | 88 | 13 | 10 |
| 36.5 | 342 | 152 | 494 | 88 | 0 | 10 |

**推荐 X=20ns**: T_ubio_dram=33ns 接近 DDR4 器件延迟 (tRCD+tCL=27.5ns),物理上合理。

---

## 6. 需新增的参数与实现方案

### 6.1 Δ_noc — cross-socket NoC 额外延迟

**含义**: 当 RNF/HNF 路由到异侧 socket 的 SNF（L_SNF 或 EP_SNF）时,在 NoC 路径上额外加的延迟,用于体现 same-socket 与 cross-socket（NUMA）的 10ns 差异。

**值**: `Δ_noc = 10ns = 20 cy`（固定,由 E3/E5 方程解出）

**实现方案 A — NoC topology 层（推荐,改动最小）**:

在 `gem5/configs/ruby/CHI_config.py` 的 `NoC_Params` 中新增一个参数:

```python
class NoC_Params:
    router_link_latency = 1
    node_link_latency = 1
    router_latency = 1
    cross_socket_link_latency = 20   # NEW: cross-socket NoC extra delay (cycles)
    ...
```

然后在 `gem5/configs/ruby/CHI_ubcc_framework.py` 的 HN-F downstream 设置处（约 line 462-473）,对跨 socket 的路由设置不同的 link latency。具体做法: 在 `create_topology()` 时,对连接异侧 socket HN-F 和 SNF 的 NoC link 使用 `cross_socket_link_latency` 而非默认的 `node_link_latency`。

这需要在 topology 创建函数（`configs/topologies/CustomMesh.py` 或 `create_topology`）中识别哪些 link 是跨 socket 的,并对它们设较高的 latency。

**实现方案 B — HN-F/EP_SNF 层（备选,更直接）**:

在 `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc` 中,当 `ingressSocket != homeSocket` 时,在 `handleRemoteMiss` 调用前 schedule 一个 20 cycle 的延迟事件。即:

```cpp
// EPSNFController.cc,在调用 handleRemoteMiss 之前:
if (_socketId != homeSocket) {
    // cross-socket: 延迟 20 cycles 后再处理
    scheduleEvent(Cycles(20));
    // 把请求存入 pending queue,等 wakeup 时再调用 handleRemoteMiss
    return;
}
```

对于 L_SNF（私有 DRAM 的 cross-socket 访存）,需要在 HN-F 的 SLICC 状态机中对跨 socket 路由的 ReadNoSnp 加额外延迟。这需要改 SLICC 协议文件,工作量较大。

**推荐方案 A**: 如果 topology 层能区分 cross-socket link,方案 A 最干净——一处改动覆盖所有跨 socket 路径（L_SNF 和 EP_SNF 都经过 NoC）。

### 6.2 T_ubio_dram — ubio backstore 读延迟

**含义**: ubio 在 `hostIssueBackstoreRead` 中从软件 map 取到数据后,不立即回调 `onBackstoreFillComplete`,而是延迟 `T_ubio_dram` 后再回调,模拟真实 DRAM 读取延迟。当前 `hostIssueBackstoreRead` 是同步的（`std::map::find` + 立即回调）,延迟为 0。

**值**: `T_ubio_dram = 73 - 2*X_ns`（ns,由 E4 方程解出,X 的函数）。例如 X=20ns 时 T_ubio_dram=33ns。

**实现方案**:

ubio 不是 gem5 的 SimObject,没有 gem5 事件队列。它用自己的 `uint64_t tick` 变量驱动主循环。需要添加一个**延迟回调队列**:

**步骤 1: 在 `UbioBackstoreHost` 中新增 pending 队列**

```cpp
// modules/ubiomodule/ubio_main.cc, struct UbioBackstoreHost 中新增:

struct PendingBackstoreFill {
    uint64_t fireTick;   // tick at which to call onBackstoreFillComplete
    uint64_t pa;
    bool found;
    UBCCController::BackstoreEntry entry;
};

std::vector<PendingBackstoreFill> _pendingFills;
uint64_t _ubioDramDelayPs = 0;  // T_ubio_dram in ps, configurable via env
```

**步骤 2: 修改 `hostIssueBackstoreRead`**

```cpp
void hostIssueBackstoreRead(uint64_t pa) override {
    UBCCController::BackstoreEntry e{};
    auto it = store.find(pa);
    const bool found = it != store.end();
    if (found) e = it->second;

    if (_ubioDramDelayPs == 0) {
        // 原行为: 立即回调
        ubcc.onBackstoreFillComplete(pa, found, e);
    } else {
        // 延迟回调: 存入 pending 队列,等 tick 推进到 fireTick 后执行
        _pendingFills.push_back({tickRef + _ubioDramDelayPs, pa, found, e});
    }
}
```

**步骤 3: 在 ubio 主循环中检查 pending 队列**

在 `ubio_main.cc` 的主 `while` 循环中（约 line 873-905）,每次 `tick` 推进后检查:

```cpp
// 在 tick 推进之后, pollAndProcess 之前:
for (auto it = _pendingFills.begin(); it != _pendingFills.end(); ) {
    if (tick >= it->fireTick) {
        ubcc.onBackstoreFillComplete(it->pa, it->found, it->entry);
        it = _pendingFills.erase(it);
    } else {
        ++it;
    }
}
```

**步骤 4: 配置入口**

通过环境变量 `UBIO_DRAM_DELAY_PS` 设置 `_ubioDramDelayPs`,在 `main()` 开头读取:

```cpp
const char* envDramDelay = std::getenv("UBIO_DRAM_DELAY_PS");
if (envDramDelay) host._ubioDramDelayPs = std::strtoull(envDramDelay, nullptr, 10);
```

**影响分析**:
- `onBackstoreFillComplete` 延迟后,UBCC 的 `processOuterRequest` 返回 `Queued` 而非 `Ready`。requester 会被 enqueue 到 `_residentWaiters`。当 `onBackstoreFillComplete` 最终被调用时,UBCC 会 `replayPendingRequesters` 解除排队阻塞。
- 这与 RECALL orphan 的 timer cleanup 机制类似,都是"延迟后回调"模式。
- 需要确认: ubio 的 `tick` 在等待 `_pendingFills` 期间能正常推进（不卡在 `safeTs` 上）。因为 ubio 的 `tick` 由 `safeTs` 驱动,而 `safeTs` 依赖 peer（gem5/nsim）的心跳。只要 peer 在正常推进,`tick` 就会推进,pending fill 最终会 fire。

**TLOC 估算**: ~25 行（struct + 修改 hostIssueBackstoreRead + 主循环检查 + env 读取）。

---

## 7. 已修复的 bug（本次 P1 阶段）

| Bug | 位置 | 修复 |
|-----|------|------|
| EPSNFController 不传 _socketId | `EPSNFController.cc:87,212` | 加 `_socketId` 参数 |
| EPBackend 响应回调只覆盖 socket-0 | `EPBackend.cc:165-172` | 泛化到所有 socket |
| UBCC 响应不设 dstSocket | `ubio_main.cc:411,443,458,477,501,520,564` | 加 `response.h.dstSocket = msg.h.srcSocket` |

验证结果: TC32/34/35/39 全部 PASS (100ns ZMQ, 2s topology)。
