# Multi-Node Physical Address Layout for UBCC Basic Framework

生成时间: 2026-05-25
版本: 1.0
基线: `docs/basic-framework-prompt.md`

---

## 1. Design Rationale

gem5 的 `System` 对 PA → 设备的映射有单一性约束：同一个 PA 不能在同一 MachineType 内映射到两个不同设备。直接让 Node 0 和 Node 1 的 LocalPrivate 都使用 PA=[0, 2*SEG) 会触发 `AbstractController::downstream_destinations` 中的同类型同范围冲突 fatal。

**解决方案**：每个 node 分配独立的物理地址基址，使得不同 node 的 LocalPrivate / DSM / UbccExclusive 自然落在不重叠的 PA 区间。

---

## 2. Address Layout

### 2.1 Constants

```
NODE_ADDR_SHIFT = 40          # 每节点 1TB 独立 PA 空间
PHY_BASE_i      = i << 40     # Node i 的物理地址基址
SEG_SIZE        = 128 MB      # 段大小 (0x800_0000)
```

### 2.2 Per-Node PA Mapping

Node i 的本地物理地址空间: `[PHY_BASE_i, PHY_BASE_i + 5 * SEG_SIZE)`

| 偏移 (相对 PHY_BASE_i) | 范围 | 用途 | 管理对象 |
|---|---|---|---|
| `[0*SEG, 1*SEG)` | LocalPrivate | 本节点私有 DRAM，普通页分配 | `L_SNF_i` |
| `[1*SEG, 2*SEG)` | UbccExclusive | UBCC 元数据，不对 CPU 可见 | `L_SNF_i` |
| `[2*SEG, 3*SEG)` | DSM_0 窗口 | DSM node 0 在本节点的 PA 视图 | 见 2.3 |
| `[3*SEG, 4*SEG)` | DSM_1 窗口 | DSM node 1 在本节点的 PA 视图 | 见 2.3 |
| `[4*SEG, 5*SEG)` | DSM_2 窗口 | DSM node 2 在本节点的 PA 视图 | 见 2.3 |

DSM 全局 PA 在当前方案中**不再全局统一**——每个 node 看到相同 DSM 逻辑索引对应**不同的 PA**。

### 2.3 DSM Window Management

记 `PHY_i_DSM_k = PHY_BASE_i + 2*SEG + k*SEG`。

| 条件 | 对象 | 后端 |
|---|---|---|
| `i == k` | `DL_SNF_i` | 本地 DRAM (DSM_i 的 home node) |
| `i != k` | `EP_SNF_i` | External Proxy (远端 DSM，走 UBCC 协议) |

**示例 (Node 0)**:

| PA 范围 | 对象 | 后端 |
|---|---|---|
| `PHY_0_DSM_0` = 0x0_1000_0000 ~ 0x0_1800_0000 | `DL_SNF_0` | 本地 DRAM |
| `PHY_0_DSM_1` = 0x0_1800_0000 ~ 0x0_2000_0000 | `EP_SNF_0` | External Proxy (→ Node 1 DSM) |
| `PHY_0_DSM_2` = 0x0_2000_0000 ~ 0x0_2800_0000 | `EP_SNF_0` | External Proxy (→ Node 2 DSM) |

`EP_SNF_0` 的 `addr_ranges` 包含两段: `[PHY_0_DSM_1, PHY_0_DSM_2)` 和 `[PHY_0_DSM_2, PHY_0_DSM_3)`。`VectorParam.AddrRange` 天然支持多段。

---

## 3. Address Translation at EP Boundary

当 DSM 访问需要跨越 node 边界时（通过 EP_RNF / EP_SNF → UBCC），PA 与三元组互相转换。

### 3.1 Tuple Structure

```
(src_node_id, dsm_home_node_id, dsm_offset)
```

- `src_node_id`: 请求发起方的节点 ID。对于本节点内的 CHI 请求，从 `PHY_BASE_i` 反推: `src = (pa >> 40) & 0x3`
- `dsm_home_node_id`: DSM line 所属的 home node。从段内偏移计算: `home = (pa - PHY_BASE_i - 2*SEG) / SEG_SIZE`
- `dsm_offset`: 段内缓存行偏移: `offset = pa & (SEG_SIZE - 1)`，按 64B 对齐

### 3.2 PA → Tuple (发包方向)

在 `EP_RNF::recvSnoopMsg()` 或 `EP_SNF::recvRequestMsg()` 入口调用:

```cpp
// pa 是请求方的本地 PA (PHY_BASE_src + ...)
int src_node   = (pa >> 40) & 0x3;
int home_node  = ((pa - (src_node << 40)) - 2*SEG) / SEG_SIZE;
Addr offset    = pa & (SEG_SIZE - 1);
```

然后将 `(home_node, offset)` 传递给 `UBCCController` 执行全局目录查找。

### 3.3 Tuple → PA (收包方向)

`UBCCController` 需要向目标 node 发起一致性操作时:

```cpp
// 目标节点 target_node，home_node 是 DSM 的 owner
Addr pa = (target_node << 40) + 2*SEG + home_node*SEG + offset;
```

然后将此 PA 包装为 CHI 请求，通过 `EP_RNF` 或 `EP_SNF` 注入目标节点的 CHI domain。

### 3.4 实现位置

翻译逻辑集中在 `NodeAddressMap` (C++ 类，`gem5/src/mem/ruby/protocol/chi/ep/NodeAddressMap.hh`)，新增三个方法:

```cpp
class NodeAddressMap {
    Addr nodeBase(int node_id) const;          // node_id << 40
    int srcNodeId(Addr pa) const;              // pa >> 40
    int dsmHomeNode(int src_node, Addr pa) const;  // 见 3.2
    Addr dsmOffset(Addr pa) const;             // pa & (SEG-1)
    Addr buildDsmPA(int tgt_node, int home_node, Addr offset) const;  // 见 3.3
};
```

---

## 4. DSM VA Mapping (per-node)

每个 node 上的进程使用统一的 DSM VA 窗口，但映射到不同的 PA:

```
DSM_VA_BASE = 0x7f80000000   (远高于常规 VA 区域)

Node i 的 Process.map():
    DSM_VA_BASE + 0*SEG  →  PHY_BASE_i + 2*SEG + 0*SEG   (DSM_0)
    DSM_VA_BASE + 1*SEG  →  PHY_BASE_i + 2*SEG + 1*SEG   (DSM_1)
    DSM_VA_BASE + 2*SEG  →  PHY_BASE_i + 2*SEG + 2*SEG   (DSM_2)
```

---

## 5. Per-Node Local Memory

普通页分配仍然通过 `phys_pool_id` 路由到各 node 的 `LocalPrivate` 池:

```
Process on Node i: phys_pool_id = i * 3
    → MemPool i*3 = [PHY_BASE_i + 0*SEG, PHY_BASE_i + 1*SEG)
```

`SEWorkload.memPools` 从 `system.getPhysMem()` 获取地址范围，每个 SimpleMemory 对应一个 pool。

---

## 6. Network Topology Impact

地址方案变更**不改变 topology wiring**。`HN_i` 的 `addr_ranges` 和 `downstream_destinations` 设置仅需更新地址数值:

```
HN_i addr_ranges:
    [PHY_BASE_i + 0*SEG, PHY_BASE_i + 1*SEG)  # LocalPrivate
    [PHY_BASE_i + 1*SEG, PHY_BASE_i + 2*SEG)  # UbccExclusive

L_SNF_i addr_ranges:
    [PHY_BASE_i + 0*SEG, PHY_BASE_i + 2*SEG)  # 两个连续段

DL_SNF_i addr_ranges:
    [PHY_BASE_i + 2*SEG + i*SEG, PHY_BASE_i + 2*SEG + (i+1)*SEG)  # DSM_i

EP_RNF_i addr_ranges:
    [PHY_BASE_i + 2*SEG + i*SEG, PHY_BASE_i + 2*SEG + (i+1)*SEG)  # DSM_i

EP_SNF_i addr_ranges:
    [PHY_BASE_i + 2*SEG + k*SEG, PHY_BASE_i + 2*SEG + (k+1)*SEG) for k in [0,1,2], k ≠ i
```

---

## 7. Compatibility

此方案与 `docs/basic-framework-prompt.md` 的关系:
- ✅ 统一 DSM PA 的要求**在单 System 约束下不可行**，以 per-node PA + 三元组转换等效替代
- ✅ `SegSize = 128MB`, `N = 3` 保持不变
- ✅ `DSM Local` 与 `Local Private` 分开
- ✅ `UbccExclusive` 不映射给普通 CPU
- ✅ 所有 trace / checker 仍带 `node_id`
