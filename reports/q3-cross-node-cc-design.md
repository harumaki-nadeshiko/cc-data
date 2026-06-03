# Q3 跨节点缓存一致性设计文档

> 日期: 2026-06-03  
> 基线: Q2 通过 → Q3 TC1/TC2/TC6/TC10 PASS

---

## 1. 整体架构

### 1.1 拓扑

每个 Node 包含：
- **CPU Cluster** (`ClusterCHI_RNF`): L1I/L1D/L2 per core, 2 clusters × 2 cores = 12 CPUs total (3 nodes)
- **HN-F**: 本节点 Home Node，管理全部 addr_ranges（LocalPrivate + UbccExclusive + all DSM ranges）
- **EP_SNF**: 接收 HN-F 的 ReadNoSnp(DMT)，通过 EPBackend 向 UBCC 请求 grant，返回 CompData
- **EP_RNF**: 外部一致性代理，接收 recall/invalidation → 转化为 CHI Request → HN-F
- **EPBackend**: per-node requester 书签（R_I/R_S/R_E/R_M），地址翻译（NodeAddressMap），grant 数据填充（populateGrantData）
- **UBCCController**: per-node 全局目录，管理 `_directory[homePa]` (state, owner, sharers, epoch, pendingOp)

```
Node0                         Node1                         Node2
┌─ CPU Cluster ─┐            ┌─ CPU Cluster ─┐            ┌─ CPU Cluster ─┐
│ L1D L2        │            │ L1D L2        │            │ L1D L2        │
└──┬────────────┘            └──┬────────────┘            └──┬────────────┘
   │                            │                            │
┌──┴────────────┐            ┌──┴────────────┐            ┌──┴────────────┐
│ HN-F          │            │ HN-F          │            │ HN-F          │
└──┬───┬────────┘            └──┬───┬────────┘            └──┬───┬────────┘
   │   │                        │   │                        │   │
   │   └── EP_SNF               │   └── EP_SNF               │   └── EP_SNF
   │        └── EPBackend       │        └── EPBackend       │        └── EPBackend
   │             └── UBCC       │             └── UBCC       │             └── UBCC
   │                            │                            │
   └── EP_RNF                   └── EP_RNF                   └── EP_RNF
        └── EPBackend                └── EPBackend                └── EPBackend
```

### 1.2 核心路径

#### CPU Store → Remote DSM (Write)

```
CPU → L1D → L2 → HN-F (ReadUnique)
  → HN-F: alloc_on=false → ReadNoSnpDMT → EP_SNF
  → EPBackend.handleRemoteMiss → route to home UBCC(node=home)
  → UBCC.processOuterRequest → grant (G_I→G_M, install owner)
  → EPBackend.populateGrantData → EP_SNF → CompData → HN-F → L2 → L1D
```

#### Cross-Node Read (Recall)

```
CPU(requester) → HN-F → EP_SNF → EPBackend → home UBCC
  → UBCC: existingOwner ≠ requester → GlobalRecallOwner → owner EPBackend
  → EPBackend.handleRecallRequest
    → EP_RNF.reqOut(ReadOnce) → HN-F
    → HN-F: ReadNoSnp → EP_SNF → data
    → EP_RNF.recvDataMsg(CompData) → CompAck → callback
    → sendRecallResponse → home UBCC
  → UBCC: processRecallResponse → grant to requester
```

#### Invalidation (Shared→Unique)

```
CPU(requester) → HN-F → EP_SNF → EPBackend → home UBCC
  → UBCC: G_S + ReadUnique → GlobalInvalidate → sharer EPBackend
  → EPBackend.handleInvalidationRequest
    → EP_RNF.reqOut(CleanUnique) → HN-F
    → HN-F: dir check → SnpCleanInvalid → sharers
    → EP_RNF.recvResponseMsg(Comp_UC) → CompAck → callback
    → sendInvalidationAck → home UBCC
  → UBCC: processInvalidationAck → grant to requester
```

---

## 2. 状态管理

### 2.1 UBCC 全局目录（per-line, home node）

```cpp
struct DirEntry {
    MESIState state;         // G_I / G_S / G_E / G_M
    uint64_t sharersMask;
    int ownerNode;
    bool dirty;
    uint64_t epoch;
    int pendingOp;           // 0=idle, 1=recall, 2=invalidation, 3=grant-handshake
    Tick grantTick;          // when grant was issued (for handshake delay)
    // ... pending recall/invalidation context ...
};
```

**状态转移**：

| 当前 | 新请求 | 条件 | 新状态 | 动作 |
|---|---|---|---|---|
| G_I | ReadShared | — | G_S | GrantShared, 加 sharer |
| G_I | ReadUnique | — | G_E/G_M | GrantExclusive/Modified, 设 owner |
| G_S | ReadShared | — | G_S | 加 sharer |
| G_S | ReadUnique | other sharers | G_E/G_M | Invalidation → sharers |
| G_E | ReadShared | owner≠req | G_S | Recall → owner |
| G_E | ReadUnique | owner≠req | G_E/G_M | Recall → owner |
| G_M | ReadShared | owner≠req | G_S | Recall → owner, 回收 dirty data |
| G_M | ReadUnique | owner≠req | G_M | Recall → owner, 回收 dirty data |

### 2.2 EPBackend Requester 书签（per-line, requester node）

```cpp
enum class RequesterLineState { R_I, R_WAIT_GRANT, R_S, R_E, R_M };
```

跟踪本节点持有的全局权限。Recall 降级: R_M → R_S (Read recall) / R_I (Write recall)。Invalidation: R_S → R_I。

### 2.3 pendingOp 串行化（Q3 新增）

```
pendingOp=0: 空闲，允许新请求
pendingOp=1: recall 进行中（M6）
pendingOp=2: invalidation 进行中（M8）
pendingOp=3: grant CHI 握手进行中（Q3）— 阻塞不同 requester 直到超时
```

timeout: `elapsed > 200000 ticks`（基于实测 handshake ~127k ticks + 安全余量）

---

## 3. 请求类型

### 3.1 内部 CHI 请求（Node 内部）

| 方向 | 消息 | 功能 |
|---|---|---|
| EP_RNF → HN-F | `ReadOnce` (reqOut) | Recall 时获取数据，不跟踪目录 |
| EP_RNF → HN-F | `CleanUnique` (reqOut) | Invalidation 触发 HN-F → sharers |
| EP_RNF → HN-F | `CompAck` (rspOut) | 完成 CompData/Comp_UC 握手 |
| HN-F → EP_SNF | `ReadNoSnpDMT` (reqOut) | DSM miss 请求 grant |
| HN-F → L2 | `SnpUniqueFwd` (snpOut) | 转发 exclusive 给 L2 |

### 3.2 外部协议（UBCC ↔ EPBackend）

| 方向 | 消息 | 功能 |
|---|---|---|
| Req → Home | `GlobalReadShared/Unique` | 请求读/写权限 |
| Home → Req | `GlobalGrantShared/Exclusive/Modified` | 授予权限 |
| Home → Owner | `GlobalRecallOwner` | 回收权限+数据 |
| Owner → Home | `OuterRecallResponse` | 确认回收 |
| Home → Sharer | `GlobalInvalidate` | 失效共享副本 |
| Sharer → Home | `OuterInvalidationAck` | 确认失效 |

---

## 4. Testcase 状态

| TC | 名称 | Q2 (baseline) | Q3 (当前) | 说明 |
|---|---|---|---|---|
| TC1 | 单节点本地 DSM | ✅ PASS | ✅ PASS | |
| TC2 | 跨节点 Remote Read | ❌ XFAIL | ✅ PASS | **Q3 核心修复** |
| TC3 | Ping-Pong Owner Transfer | ❌ XFAIL | ❌ Page fault | pre-existing BTI issue |
| TC4 | 三节点环 | ❌ XFAIL | ❌ FAIL | owner transfer 未完善 |
| TC5 | 单写者正确性 | ❌ XFAIL | ❌ FAIL | 并发写序列化未完善 |
| TC6 | 多 Sharer 读 | ✅ PASS | ✅ PASS | |
| TC7 | Writeback 后读 | ❌ XFAIL | ❌ FAIL | writeback 数据未持久 |
| TC8 | Upgrade Invalidate | ❌ XFAIL | ❌ FAIL | invalidation 后读旧值 |
| TC9 | Non-DSM 负例 | ❌ XFAIL | — | SIGABRT (expected) |
| TC10 | 并发原子性 | ✅ PASS | ✅ PASS | |

---

## 5. 关键架构决策

### 5.1 ReadOnce 代替 ReadShared (Recall)

`ReadShared` 在 HN-F 触发 `SnpUniqueFwd` → L2 → 协议冲突。`ReadOnce` 仅 `SendReadNoSnp → SNF`，不触发目录跟踪。recall 降级 L1/L2 由后续 invalidation 路径负责。

### 5.2 删除 sendLocalSnoop

CHI spec 中 Snoop 由 HN-F 发起。EP_RNF (RN-F) 自行广播 snoop 违反语义。完全删除，recall/invalidation 改为标准 CHI Request 路径。

### 5.3 HN-F MachineID 硬绑定

EP_RNF 通过 `hnf_version` 参数直接获取 HN-F 的 MachineID，不依赖 `mapAddressToDownstreamMachine` 的动态地址映射。

### 5.4 pendingOp=3 串行化

UBCC grant 时设置 `pendingOp=3`, `grantTick=curTick()`。后续不同 requester 的请求被阻塞直到 `elapsed > 200k ticks`。同一 requester 放行（如 write→read 在同一节点）。

---

## 6. 已知局限

1. **pendingOp timeout 为固定值**（200k ticks），非自适应。若 CHI handshake 超过此时长，可能提前放行。
2. **TC3 page fault** 为 ARM `bti` instruction 兼容性问题，非一致性问题。
3. **TC4/TC5/TC7/TC8** 的 owner transfer / writeback / invalidation 数据路径尚未完成。
4. **CleanUnique (invalidation)** 路径未充分验证，预计需进一步调试 L2 SnpCleanInvalid 处理。
5. **UBCC ↔ EPBackend 通信** 仍为进程内函数调用，未模块化。

---

## 7. 修改文件清单

| 文件 | 变更 |
|---|---|
| `UBCCController.hh` | `grantHandshakeComplete()`, `grantTick` 字段 |
| `UBCCController.cc` | `pendingOp=3` busy-check timer, grant tick recording |
| `EPRNFController.hh` | `PendingChiTxn::TXN_READONCE`, `startReadOnce()`, `hnfVersion`, 删除 `sendLocalSnoop` |
| `EPRNFController.cc` | `startReadOnce/ReadShared/CleanUnique`, `sendChiRequest(allowRetry)`, `recvDataMsg/recvResponseMsg(CompAck)`, `retryPendingCompAcks` |
| `EPRNFController.py` | `hnf_version = Param.Int(-1)` |
| `EPBackend.cc` | `handleRecallRequest→startReadOnce`, `handleInvalidationRequest→startCleanUnique` |
| `CHI_ubcc_framework.py` | `hnf_version=hnf_cntrl.version`, `downstream_destinations`, HN-F 创建移到 EP-RNF 前 |
