# gem5 Ruby CHI + UBCC 设计方案总结

> 基于 `gem5_chi_ubcc_plan.md`、`docs/ubcc_agent_execution_guide.md`、`docs/ubcc_docker_git_workflow.md`。

---

## 一、总体架构

### 1.1 目标

在 gem5 Ruby CHI 协议之上，构造 **N 个独立 CHI Domain（Logical Node）**，通过 **EP (External Proxy) + UBCC (Unified Bus Coherence Controller)** 实现跨节点的全局缓存一致性。

### 1.2 单节点内部结构

每个 Logical Node 包含：

```text
                     ┌─ CPU0 L1I/L1D ─┐
                     └─ CPU1 L1I/L1D ─┘
                            │
                    Cluster L2 (shared)
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
      RN-F               EP-RNF        EP-SNF
   (本地CPU)         (Sentinel)     (Remote Data)
              │             │             │
              └─────────────┼─────────────┘
                            ▼
                   HN-F / L3 Cache
                            │
                        ┌───┴───┐
                        ▼       ▼
                    SN-F/DRAM  EP-SNF
                   (Local)     (DSM Remote)
                            │
                            ▼
                          UBCC
                            │
                      Outer Network
```

| 组件 | 职责 |
|------|------|
| **RN-F** | 标准 CHI Request Node，含 CPU L1 + Cluster-shared L2 |
| **HN-F/L3** | Home Node，维护本地 directory、L3 cache，向 RN-F/EP-RNF 发 snoop |
| **SN-F/DRAM** | Memory Controller，服务 Local Normal PA 和 DSM Local PA |
| **EP-RNF Sentinel** | 特殊 RN-F，代表"外部世界"出现在本地 HN-F directory 中；响应 HN-F snoop；承担 UBCC 对本地的 coherent access |
| **EP-SNF** | 响应 HN-F 对 DSM Remote 的 ReadNoSnp/WriteBack；向 Home UBCC 获取/回写数据 |
| **UBCC** | 全局 Home Directory，MESI 协议，调度 EP-RNF/EP-SNF |
| **Outer Network** | 连接各 Node UBCC，第一版 fixed-latency queue |

### 1.3 地址空间划分

| 地址区间 | 服务路径 | 说明 |
|----------|---------|------|
| **Local Normal PA** | HN-F → SN-F/DRAM | 节点私有内存，不进 UBCC/EP |
| **DSM PA Local** | HN-F → SN-F/DRAM；UBCC 为 global home | 本节点是 home，数据在本地 DRAM |
| **DSM PA Remote** | HN-F → EP-SNF → Home UBCC | 本节点是 requester，数据在远程 home |
| **UBCC-Exclusive metadata** | UBCC 内部 C++ map | SE mode 下不映射给 CPU |

DSM Global PA 切片规则：

```text
DSM_GLOBAL     = [dsm_base, dsm_base + dsm_size * N)
DSM_LOCAL(i)   = [dsm_base + dsm_size * i, dsm_base + dsm_size * (i + 1))
homeNode(addr) = floor((addr - dsm_base) / dsm_size)
```

### 1.4 Domain 隔离规则

- RN-F downstream 只指向同 node HN-F
- HN-F downstream 指向同 node SN-F（Local/DSM Local）或同 node EP-SNF（DSM Remote）
- HN-F snoop destination 只含同 node RN-F + 同 node EP-RNF
- **普通 CHI REQ/SNP/RSP/DAT 不允许跨 node**
- EP/UBCC Outer Message 是唯一跨 node 通路

---

## 二、核心设计路线

### 2.1 主路线：EP-RNF Sentinel

**核心思想**：EP-RNF 作为"外部世界"的代理，出现在本地 HN-F 的 directory 中。

- 当外部 node 持有某条 DSM line 的权限时，本地 HN-F directory 中同步登记 EP-RNF Sentinel
- 本地 CPU 后续对该 line 的 upgrade/read 走 HN-F 既有 snoop 机制
- HN-F snoop EP-RNF → EP-RNF 将 snoop 翻译为 UBCC global operation → 完成后返回 snoop response
- **避免在 HN-F 每条权限路径上显式插入 global permission hook**

### 2.2 EP-RNF Sentinel 状态

| Sentinel 状态 | HN-F directory 表达 | 语义 |
|--------------|-------------------|------|
| **None** | EP-RNF 不在 directory | 外部无状态 |
| **ExternalSharer** | EP-RNF 为 sharer | 外部有 clean shared copy，本地 get unique 前需 snoop |
| **ExternalOwner** | EP-RNF 为 owner/unique holder | 外部有 E/M 或最新数据，本地读写需召回或转移权限 |
| **ExternalPending** | EP-RNF 有 transient TBE | 等待 UBCC，相关 HN-F transaction 阻塞/retry |

关键约束：
- `ExternalOwner` **不允许**与本地 CPU dirty owner 同时存在
- `ExternalSharer` **可以**与本地 clean sharers 共存
- Sentinel registration 必须**早于** CPU request completion
- Sentinel removal 必须**晚于** UBCC 确认外部状态已清空

### 2.3 EP-SNF 保守首版策略

EP-SNF 仅看 ReadNoSnp 时无法区分原始请求是 ReadShared 还是 ReadUnique。

- **M5/M6 bring-up 阶段**：DSM Remote first miss 默认请求 `GrantM`（保守策略）
- **M8 阶段**：增加 HN-F→EP-SNF minimal sideband（携带 `original_chi_req` / `needed_perm=S/M`），恢复 `GrantS`/read-sharing
- 保守 `GrantM` 只能作为 debug flag，不作为最终方案

---

## 三、主要 Case 的完整请求路径

以下逐 Case 列出请求路径，并标注 **EP-RNF 涉及** 和 **EP-SNF 涉及**。

> 约定：所有路径中 `→` 表示同步调用链，`…` 表示 outer network 跨 node 传输。

---

### Case 1：Node0 CPU 访问 Local Normal PA

**场景**：Node0 本地 CPU 读/写节点私有内存，不涉及任何跨节点状态。

**请求路径**：
```
CPU0 → L1 → L2(Cluster) → HN-F(Node0) → SN-F(Node0) → DRAM(Node0)
```

**EP-RNF 涉及**：否  
**EP-SNF 涉及**：否  
**UBCC 涉及**：否

> 纯本地 CHI 路径，不穿越任何 EP/UBCC 组件。

---

### Case 2：Node0 CPU 访问 DSM Local（无外部状态）

**场景**：Node0 本地 CPU 访问以自己的 Node0 为 Home 的 DSM line，且外部没有 node 持有该 line 的权限。

**请求路径**：
```
CPU0 → L1 → L2(Cluster) → HN-F(Node0) → SN-F(Node0) → DRAM(Node0)
```

**EP-RNF 涉及**：否  
**EP-SNF 涉及**：否  
**UBCC 涉及**：否

> 与 Case 1 路径相同。HN-F 按地址范围将 DSM Local 路由到同 node SN-F。

---

### Case 3：Node0 CPU 读 Node1 DSM Local（Remote First Miss，读）

**场景**：Node0 CPU 首次读取以 Node1 为 Home 的 DSM line（即 Node0 的 DSM Remote）。Node0 本地缓存未命中。

**请求路径**：

```
[Requester Node0]
CPU0 → L1/L2 → HN-F(Node0) miss
                │
                ▼
         EP-SNF(Node0)     ← ReadNoSnp
                │
         UBCC(Node0)
                │
    … Outer Network …
                │
[Home Node1]
         UBCC(Node1)        ← 查询 global directory
                │
    ┌───────────┼───────────┐
    │ 本地有 dirty/unique?  │
    │ YES → 走 Case 3a 子路径 │
    │ NO  → 直接返回 data    │
    └───────────┼───────────┘
                │
    Case 3a: 本地 CPU 持有 dirty/unique copy 时：
    UBCC(Node1) → EP-RNF(Node1) → HN-F(Node1) snoop local CPU
                → 召回/降级本地 copy → 获得最新 data
                │
         UBCC(Node1)         ← 更新 global directory (Node0→sharer/owner)
         EP-RNF(Node1)       ← 登记 sentinel (ExternalSharer 或 ExternalOwner 视 grant 而定)
                │
    … Outer Network … (grant + data)
                │
[Requester Node0]
         UBCC(Node0) → EP-SNF(Node0) → HN-F(Node0)
                │
         HN-F(Node0) 完成 sentinel registration
         (GrantS → 登记 EP-RNF(Node0) 为 ExternalSharer)
         (GrantM → 记录本 node 为 global owner，不登记 ExternalOwner)
                │
         CPU0 收到 data
```

**涉及 EP-RNF**：是（Home 侧，用于召回/降级本地 CPU 副本；Requester 侧，用于登记 sentinel）  
**涉及 EP-SNF**：是（Requester 侧，用于 ReadNoSnp → fill data）  
**涉及 UBCC**：是（两侧）

---

### Case 4：Node0 CPU 写 Node1 DSM Local（Remote First Miss，独占写）

**场景**：Node0 CPU 首次独占写入以 Node1 为 Home 的 DSM line。

**请求路径**：

```
[Requester Node0]
CPU0 → L1/L2 → HN-F(Node0) miss
                │
         EP-SNF(Node0)     ← ReadNoSnp（保守 GrantM 模式）
                │
         UBCC(Node0)
                │
    … Outer Network … (GlobalReadUnique / 保守 GrantM)
                │
[Home Node1]
         UBCC(Node1)        ← 查询 global directory
                │
         若本地有 sharer/owner：
         UBCC(Node1) → EP-RNF(Node1) → HN-F(Node1)
                → 失效/召回本地所有 CPU 副本
                │
         UBCC(Node1) 更新 global directory (Node0→M owner)
         EP-RNF(Node1) 登记 ExternalOwner sentinel
                │
    … Outer Network … (GrantM + data)
                │
[Requester Node0]
         UBCC(Node0) → EP-SNF(Node0) → HN-F(Node0)
         HN-F(Node0) 给 CPU0 SC/UC/UD 状态
         Node0 记录为 global M owner
                │
         CPU0 完成写入
```

**涉及 EP-RNF**：是（Home 侧，召回/失效本地 CPU；Home 侧，登记 ExternalOwner sentinel）  
**涉及 EP-SNF**：是（Requester 侧，ReadNoSnp fill）  
**涉及 UBCC**：是（两侧）

---

### Case 5：Node0 CPU 升级本地 S→M（已有 ExternalSharer Sentinel）

**场景**：Node0 本地 CPU 持有某条 DSM line 的 S 状态，但 home 在 Node1，且外部还有其他 sharer（Node0 HN-F directory 中已有 EP-RNF 为 ExternalSharer）。Node0 CPU 现在要 upgrade 到独占（M）。

**请求路径**：

```
[Requester Node0]
CPU0 → L1/L2 → HN-F(Node0)   ← MakeReadUnique
                │
         HN-F directory 发现 EP-RNF(Node0) 为 ExternalSharer
                │
         HN-F(Node0) snoop → EP-RNF(Node0)
                │
         EP-RNF(Node0) → UBCC(Node0)
                │
    … Outer Network … (GlobalInvalidate)
                │
[Home Node1]
         UBCC(Node1) → 向所有 remote sharer 发 GlobalInvalidate
                │
         各 remote node:
         Remote UBCC → Remote EP-RNF → Remote HN-F → 失效本地 CPU copy
                → 各 node 返回 ack
                │
         UBCC(Node1) 更新 directory (Node0 → M owner)
                │
    … Outer Network … (ack)
                │
[Requester Node0]
         UBCC(Node0) → EP-RNF(Node0) → 返回 snoop response
                │
         HN-F(Node0) 完成本地 unique grant
         CPU0 获得 M 状态
```

**涉及 EP-RNF**：是（Requester 侧 sentinel snoop → global invalidation 触发；Home 侧及所有 remote sharer 侧，用于失效本地 CPU）  
**涉及 EP-SNF**：否（不涉及 remote fill data plane）  
**涉及 UBCC**：是（所有侧）

---

### Case 6：Node0 CPU 读 DSM Local，ExternalOwner Sentinel 存在

**场景**：Node0 有某条 DSM Local line（以自己为 Home），但之前被 Node2 以独占方式拿走（Node0 HN-F directory 中 EP-RNF 为 ExternalOwner）。现在 Node0 CPU 要读取该 line。

**请求路径**：

```
[Home Node0]
CPU0 → L1/L2 → HN-F(Node0) miss
                │
         HN-F directory 发现 EP-RNF(Node0) 为 ExternalOwner
                │
         HN-F(Node0) snoop → EP-RNF(Node0)
                │
         EP-RNF(Node0) → UBCC(Node0)
                │
    … Outer Network … (GlobalDowngrade / Recall to remote owner)
                │
[Remote Owner Node2]
         UBCC(Node2) → EP-RNF(Node2) → HN-F(Node2)
                → snoop/recall 本地 CPU (当前 M owner)
                → CPU 返回 dirty data，降级为 I 或 S
                │
         EP-RNF(Node2) → UBCC(Node2)
                │
    … Outer Network … (data + ack)
                │
[Home Node0]
         UBCC(Node0) 更新 directory (Node2 → S 或 I；Node0 → S)
         EP-RNF(Node0) 更新 sentinel (ExternalOwner → ExternalSharer)
                │
         EP-RNF(Node0) 返回 snoop response + data 给 HN-F(Node0)
                │
         HN-F(Node0) 给 CPU0 返回 data (S 状态)
```

**涉及 EP-RNF**：是（Home 侧 sentinel snoop + Remote owner 侧召回）  
**涉及 EP-SNF**：否  
**涉及 UBCC**：是（两侧）

---

### Case 7：Remote 独占写使本 Node 进入 ExternalOwner（Sentinel 创建）

**场景**：Node2 CPU 对以 Node0 为 Home 的 DSM line 发起 GlobalReadUnique。Node0 本地 CPU 之前持有该 line（可能是 S 或 M）。需要让 Node0 本地 copy 失效，并使 Node0 HN-F 中 EP-RNF 成为 ExternalOwner。

**请求路径**：

```
[Remote Requester Node2]
CPU2 → HN-F(Node2) → EP-SNF(Node2) → UBCC(Node2)
                │
    … Outer Network … (GlobalReadUnique)
                │
[Home Node0]
         UBCC(Node0) 查询 global directory
         判断本地可能有 CPU sharer/owner → 需要失效
                │
         UBCC(Node0) → EP-RNF(Node0)
                → 对 HN-F(Node0) 发起 ReadUnique 或等价 local unique operation
                │
         HN-F(Node0) 通过普通 CHI snoop 失效 Node0 本地 CPU
         HN-F directory: EP-RNF(Node0) 记录为 owner (ExternalOwner)
                │
         EP-RNF(Node0) → UBCC(Node0) (data + ack)
         UBCC(Node0) 更新 directory (Node2 → M owner)
                │
    … Outer Network … (GrantM + data)
                │
[Remote Requester Node2]
         UBCC(Node2) → EP-SNF(Node2) → HN-F(Node2) → CPU2 完成
```

**涉及 EP-RNF**：是（Home 侧，对本地发起 ReadUnique → 成为 ExternalOwner）  
**涉及 EP-SNF**：是（Remote requester 侧，ReadNoSnp fill）  
**涉及 UBCC**：是（两侧）

---

### Case 8：DSM Remote Writeback/Evict

**场景**：Node0 HN-F 对以 Node1 为 Home 的 DSM Remote dirty line 发生 writeback 或 clean evict。

**请求路径**：

```
[Requester Node0]
HN-F(Node0) → EP-SNF(Node0)     ← WriteBackFull / Evict
                │
         EP-SNF(Node0) → UBCC(Node0)
                │
    … Outer Network … (GlobalWriteback / GlobalEvict + data)
                │
[Home Node1]
         UBCC(Node1) 更新 home directory
         (writeback: 写入 DRAM, 更新 dirty/state)
         (evict: 从 sharer mask 移除 Node0)
                │
         若该 line 不再有外部状态 → sentinel 可删除
                │
    … Outer Network … (ack)
                │
[Requester Node0]
         UBCC(Node0) → EP-SNF(Node0) → HN-F(Node0) 完成
```

**涉及 EP-RNF**：否（除非需要删除 ExternalSharer sentinel）  
**涉及 EP-SNF**：是（Requester 侧，writeback/evict data plane）  
**涉及 UBCC**：是（两侧）

---

### Case 9：Dirty Shared Node 内部→对外折叠为唯一 M Owner

**场景**：Node0 内部两个 CPU 同时持有某 DSM line 的 dirty shared (SD) 状态。对外（global directory）必须表现为唯一的 M owner。当 remote node 请求该 line 时：

**请求路径**：

```
[Home Node1]
         UBCC(Node1) 收到 remote request 命中 Node0 为 M owner
                │
    … Outer Network … (GlobalDowngrade/Recall → Node0)
                │
[Owner Node0]
         UBCC(Node0) → EP-RNF(Node0) → HN-F(Node0)
                → snoop/recall 本地两个 CPU 的 SD 副本
                → 收集最新 dirty data，将两个 CPU 降级或失效
                │
         EP-RNF(Node0) → UBCC(Node0) (data + ack)
                │
    … Outer Network … (data)
                │
[Home Node1]
         UBCC(Node1) 更新 directory，继续服务 remote request
```

**涉及 EP-RNF**：是（Owner 侧，recall/downgrade 本地 CPU）  
**涉及 EP-SNF**：否  
**涉及 UBCC**：是（两侧）

---

### Case 10：多 Reader + Single Writer（GrantS/read-sharing）

**场景**：Node0 和 Node2 同时读取 Node1 DSM Local（均为 S 状态），后续 Node0 写入。

**Phase 1 — 两个 Reader 同时读**：

```
Node0 read: Node0 HN-F → EP-SNF(Node0) → UBCC(Node0) → UBCC(Node1) → GrantS → EP-SNF → HN-F → 登记 EP-RNF(Node0) ExternalSharer
Node2 read: Node2 HN-F → EP-SNF(Node2) → UBCC(Node2) → UBCC(Node1) → GrantS → EP-SNF → HN-F → 登记 EP-RNF(Node2) ExternalSharer
Home UBCC(Node1) sharer mask: {Node0, Node2}
```

**Phase 2 — Node0 写入**：

```
Node0 CPU → HN-F(Node0) MakeReadUnique
  → HN-F 发现 EP-RNF(Node0) ExternalSharer → snoop EP-RNF(Node0)
  → EP-RNF(Node0) → UBCC(Node0)
  → … Outer Network … → UBCC(Node1) (GlobalInvalidate)
  → UBCC(Node1) 向 Node2 发 GlobalInvalidate
    → UBCC(Node2) → EP-RNF(Node2) → HN-F(Node2) → 失效 CPU2 → ack
  → UBCC(Node1) 更新 directory (Node0→M owner)
  → … ack … → EP-RNF(Node0) → snoop response
  → HN-F(Node0) grant M → CPU0 写入
```

**涉及 EP-RNF**：是（所有参与 node 的 EP-RNF：Requester 侧 sentinel snoop；Home 侧 invalidate sharers；其他 sharer 侧失效）  
**涉及 EP-SNF**：是（Phase 1 的 remote fill）  
**涉及 UBCC**：是（所有侧）

---

## 四、各 Case 的 EP-RNF / EP-SNF 涉及速查表

| Case | 场景 | EP-RNF 涉及 | EP-SNF 涉及 |
|------|------|:----------:|:----------:|
| Case 1 | Local Normal PA 访问 | N | N |
| Case 2 | DSM Local，无外部状态 | N | N |
| Case 3 | Remote 读 DSM（first miss） | Y (Home侧召回; Req侧sentinel) | Y (Req侧 fill) |
| Case 4 | Remote 独占写 DSM（first miss） | Y (Home侧失效; Home侧ExternalOwner) | Y (Req侧 fill) |
| Case 5 | 本地 S→M 升级（有 ExternalSharer） | Y (Req侧 snoop; 全部 remote 侧失效) | N |
| Case 6 | 本地读 DSM（有 ExternalOwner） | Y (Home侧 snoop; Remote owner侧召回) | N |
| Case 7 | Remote 独占→本地进入 ExternalOwner | Y (Home侧→ExternalOwner) | Y (Req侧 fill) |
| Case 8 | DSM Remote writeback/evict | N (除非删除 sentinel) | Y (Req侧 writeback) |
| Case 9 | 节点内 SD→对外 M owner 折叠 | Y (Owner侧 召回) | N |
| Case 10 | 多 Reader + Single Writer | Y (所有侧) | Y (Phase1 fill) |

---

## 五、EP-RNF 与 EP-SNF 职责对比

| | EP-RNF Sentinel | EP-SNF |
|------|-----------------|--------|
| **CHI 角色** | 伪装为 RN-F | 伪装为 SN-F |
| **实现基类** | `CHIGenericController` | `CHIGenericController` |
| **主要负责场景** | Sentinel 登记/删除、响应 HN-F snoop、本地 coherent access、ExternalOwner 管理 | DSM Remote ReadNoSnp fill、writeback/evict data plane |
| **涉及 Case** | Case 3/4/5/6/7/9/10 | Case 3/4/7/8/10 |
| **核心 challenge** | 被 HN-F snoop 后需等待 UBCC outer round trip 才能回复；ExternalOwner data forwarding 语义 | 仅看 ReadNoSnp 无法区分原始请求语义，首版保守 GrantM |
| **是否参与本地 CHI coherence** | 是（sentinel 在 HN-F directory 中像普通 RN-F 一样被 snoop） | 否（仅作为 data provider/consumer） |

---

## 六、Global Directory（UBCC）设计

### 6.1 Per-line Directory Entry

```cpp
struct DirEntry {
    Addr     line;
    State    state;     // I / S / E / M / Busy
    uint32_t sharers;   // bitmask, N ≤ 32 (第一版)
    int      owner;     // node_id, valid if E/M
    bool     dirty;
    uint64_t epoch;     // 防 stale response
};
```

### 6.2 状态映射（Node内 → Global）

| 节点内 CHI 状态 | 对外 Global Summary | 说明 |
|-----------------|---------------------|------|
| 无本地 cache | I | 不是 sharer/owner |
| Clean Shared | S | 可与其他 node 共享 |
| Unique Clean | E 或 M-like | 第一版按 M 处理，后续开启 E |
| Unique Dirty | M | 全局唯一 dirty owner |
| Shared Dirty | M | 节点内 SD，对外折叠为唯一 M owner |
| HN-F/L3 only clean | S (保守) | 避免丢失可服务 data 的 clean copy |

### 6.3 Outer Message 类型

| 方向 | Message |
|------|---------|
| Request → Home | `GlobalReadShared`, `GlobalReadUnique`, `GlobalWriteback`, `GlobalEvict` |
| Home → Sharer/Owner | `GlobalInvalidate`, `GlobalDowngrade` |
| Home → Requester | `GrantS`, `GrantE`, `GrantM`, `GlobalDataResp` |
| 通用 | `GlobalAck`, `GlobalRetry` |

---

## 七、分阶段里程碑

| 里程碑 | 内容 | 关键验证 |
|--------|------|---------|
| **M0** | Docker/Git 自动化预检 | 容器可离线构建/测试，可无人干预 commit/push |
| **M1** | 单节点 CHI C=2/M=2 | cluster-shared L2 + HN-F/L3 + SN-F 跑通 |
| **M2** | N=3 logical domain isolation spike | Local Normal PA 不产生跨 node CHI message |
| **M3** | EP-RNF/EP-SNF skeleton | 能以 CHI participant 收发消息 |
| **M4** | HN-F sentinel registration | 本地 upgrade 可 snoop EP-RNF；ExternalOwner 支持 |
| **M5** | DSM Remote first miss (保守 GrantM) | Node0 可读取 Node1 DSM Local |
| **M6** | UBCC directory + EP-RNF coherent local access | remote read/write ping-pong 正确 |
| **M7** | Writeback, evict, owner transfer | 三节点压力测试正确 |
| **M8** | GrantS/read-sharing 恢复 | 多个 node 可同时 S，写者能失效所有 sharer |
| **M9** | Metadata 容量模型 + 多 gem5 迁移准备 | 接口文档化，外部网络选项确认 |

---

## 八、主要风险

1. **单 RubySystem 多 logical island 隔离**：相同 global PA + 多 HN-F addr_ranges + NetDest 路由可能有隐藏耦合，若 gating spike 失败需切换多 RubySystem/多 gem5。
2. **Sentinel registration 时序**：必须先登记 sentinel 再完成 CPU request，否则出现 local CPU 立即 upgrade 而外部状态不可见的窗口。
3. **EP-SNF 语义盲区**：仅看 ReadNoSnp 无法区分 ReadShared/ReadUnique，首版保守 GrantM 正确但性能差。
4. **EP-RNF snoop→outer round trip 阻塞**：必须独立 TBE + retry/timeout + deadlock debug counter。
5. **ExternalOwner data forwarding**：HN-F 对 owner snoop 时 EP-RNF 必须能返回符合 CHI 规范的 response/data。
6. **双层 directory 一致性**：Global directory 与本地 HN-F directory 之间的短暂不一致由 per-line Busy/epoch/TBE 序列化吸收。
7. **SD→M 折叠**：CHI 内部允许多 CPU shared dirty，但 global MESI 必须折叠成唯一 M owner。
8. **Silent E→M**：若后续启用 GrantE，建议 E owner 被 remote 请求时总是联系 owner，不依赖及时 E→M 通知。

---

## 九、不做的范围（第一版）

- DVM、Atomic、Exclusive Monitor
- IO coherent DMA
- Dynamic home migration
- Directory backing store（使用 UBCC C++ map）
- 多 gem5 进程 / ns-3 外部网络（M9 仅文档化）
- UBCC metadata 的 DRAM backing（SE mode 下使用内部 map）
- 独立 UR（DSM Local coherent access 合并进 EP-RNF）
