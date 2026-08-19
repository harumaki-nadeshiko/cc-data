# CC-EP 多节点全局/局部一致性协议设计

> 版本: v4 (dual-socket, reserve-then-commit, 双向 epoch, tombstone, upgrade_invalidate_fix)
> 最后更新: 2026-07-19

---

## 1. 整体架构总览

### 1.1 系统定位

cc-ep 是一个**多节点分布式共享内存 (DSM) 缓存一致性仿真器**。将一个 gem5 全系统仿真拆分为多个独立 OS 进程, 进程间通过 ZeroMQ IPC 交换一致性消息, 并用保守式并行离散事件仿真 (PDES) 时钟同步保证因果性。

### 1.2 分层架构

```
┌──────────────────────────────────────────────────────────────────────┐
│                        gem5 仿真进程 (每节点 1 个)                     │
│  ┌─────────┐  ┌──────┐  ┌──────┐  ┌───────────────────────────────┐ │
│  │  ARM    │  │ L1 D │  │  L2  │  │          HN-F (L3)             │ │
│  │  CPU    │─▶│ 32KB │─▶│256KB │─▶│  ┌────────┬────────┬────────┐  │ │
│  └─────────┘  └──────┘  └──────┘  │  │DL_SNF  │EP_SNF  │ L_SNF  │  │ │
│                                    │  │ ┌───┐  │        │ ┌───┐  │  │ │
│                                    │  │ │DDR│  │        │ │DDR│  │  │ │
│                                    │  │ │MC │  │        │ │MC │  │  │ │
│                                    │  │ └───┘  │        │ └───┘  │  │ │
│                                    │  └────────┴────────┴────────┘  │ │
│                                    │         ▲                      │ │
│                                    │  ┌──────┴────────┐            │ │
│                                    │  │   EP-RNF      │            │ │
│                                    │  │ (owner-side)  │            │ │
│                                    │  └───────────────┘            │ │
│                                    └───────────────────────────────┘ │
│         UBAdapter (传输适配器, ZMQ IPC)                               │
└───────────────────────┬──────────────────────────────────────────────┘
                        │ ZMQ PAIR (IPC)
┌───────────────────────┴──────────────────────────────────────────────┐
│              ubio 进程 (每 (node, socket) 平面 1 个)                  │
│  ┌──────────────────────────────────────────────────────┐           │
│  │           UBCCController (Home 目录 + 一致性控制器)   │           │
│  │  ┌─────────────────────┐  ┌────────────────────────┐ │           │
│  │  │    ResidentDir      │  │  Backstore Schema A/C  │ │           │
│  │  │  (57,344 目录条目)   │  │  (256B 页, 紧凑编码)   │ │           │
│  │  │  Bloom Filter       │  │                        │ │           │
│  │  └─────────────────────┘  └────────────────────────┘ │           │
│  └──────────────────────────────────────────────────────┘           │
│         Port(gem5) ◄─────► Port(networksim)                         │
└───────────────────────┬──────────────────────────────────────────────┘
                        │ ZMQ PAIR (IPC)
┌───────────────────────┴──────────────────────────────────────────────┐
│           networksim (跨节点交叉开关, 1 个)                           │
│         FIFO 延迟管道 │ 全连接 mesh 拓扑 │ Per-module 路由             │
└──────────────────────────────────────────────────────────────────────┘
```

### 1.3 核心组件对照

| 组件 | 位置 | 角色 |
|---|---|---|
| **UBCCController** | `modules/ubiomodule/UBCCController.hh/.cc` (3604行) | Home 目录状态机: grant 决策, recall 回收, invalidation 广播, upgrade 握手, writeback/evict 处理, Clear/Tombstone |
| **ResidentDir** | `modules/ubiomodule/ResidentDir.hh/.cc` | 集关联常驻目录 (8192 sets × 7 ways = 57,344 条目), 计数 bloom filter (4 哈希), 伪 LRU 替换, spill-to-backstore |
| **EPBackend** | `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh/.cc` | 请求端后端: 远程 miss 分发, grant 处理, recall/invalidate 处理, upgrade 管理, Clear/ClearAck, writeback/evict |
| **EPSNFController** | `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.hh/.cc` | EP-SNF 状态机: 系于 HN-F, 处理 ReadNoSnp/WriteNoSnp, 重试队列, 延迟 CompData/grants |
| **EPRNFController** | `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.hh/.cc` | EP-RNF 状态机: owner 端, 处理 CHI 请求至 HN-F (ReadShared/CleanUnique/ReadUnique), 处理 snoops, upgrade pending, snoop 冲突仲裁 |
| **UBAdapter** | `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.hh/.cc` | 传输适配器 SimObject: wrapper framework::Port, 序列化 CoherenceMessage, PDES 时钟同步集成 |
| **MetaRNFController** | `gem5/src/mem/ruby/protocol/chi/ep/MetaRNFController.hh/.cc` | Phase 3: 256B 元数据页通过 CHI 访问 |
| **networksim** | `modules/networksim/` | 跨节点交叉开关: per-module FIFO 队列, 拓扑驱动路由 |
| **barrier_manager** | `modules/barrier/` | 栅栏协调: 聚合 BarrierReached, 广播 BarrierRelease |

---

## 2. 地址空间布局

### 2.1 节点级地址映射

| 参数 | 值 | 含义 |
|---|---|---|
| `NODE_ADDR_SHIFT` | 40 | 每节点地址空间 1TB |
| 段大小 | 128MB | DSM 段 per (node, socket) 平面 |
| 本地段偏移 | `nodeBase + 2×seg + (node×K + socket)×seg` | DSM plane 物理地址 |

### 2.2 地址判定

- `UBCCController::isDsmAddr(pa)`: 检查 PA 是否属于本 plane 的 home 地址
- `EPBackend::isDsmAddr(pa)`: 检查 PA 是否为远程 DSM 线
- `EPBackend::homeNodeCrossNode(pa)`: 从 PA 推导 home node/socket

---

## 3. 运行时进程拓扑

以 N 个节点、K 个 socket/节点为例, 共 N×K + N + 2 个进程:

| 进程 | 数量 | 职责 | 端口 |
|---|---|---|---|
| `barrier_manager` | 1 | 跨节点栅栏 | bind-only, 每节点 1 端点 |
| `networksim` | 1 | 交叉开关网络 | N×K + N 个 module 端点 |
| `gem5.opt` | N | ARM CPU + Ruby/CHI | 1 个 gem5UbioPort(gid) |
| `ubio` | N×K | Home 目录 | 2 个 Port: gem5Port + netPort |

**全局 ID**: `gid = node × K + socket`。它是应用层编码源/目标端点的规则；
Framework/传输层不会从Port隐式填入Message。每个Payload发送方必须显式设置
`sourceId/targetId`，内建Sync/Terminate保持`0/0`。

**连接拓�结构**:
- gem5 ↔ 本地 ubio: 双向 IPC pair
- ubio ↔ networksim: 双向 IPC pair
- gem5(socket-0) ↔ barrier_manager: 栅栏专用
- 所有跨 node 消息经 networksim 转发

---

## 4. 消息层设计

### 4.1 传输层 (MemMessage)

`framework/MemMessage.hh`: 40 字节固定头 + 最多 1024 字节载荷.

```
Off 0: timestamp (8B)   - 消息可见时刻 (发送tick + linkLatency)
Off 8: size      (4B)   - 头+载荷总字节
Off 12: type     (4B)   - MemMessageType: CONTROL_SYNC=0, TERMINATE=1, PAYLOAD=2
Off 16: sourceId (4B)   - 源端点 gid
Off 20: reserved (4B)
Off 24: targetId (4B)   - 目标端点 gid
Off 28: reserved (4B)
Off 32: req_id   (8B)   - 事务匹配 ID
```

### 4.2 一致性消息 (CoherenceMessage)

`protocol/CoherenceMessage.hh`: 作为 `PAYLOAD` 载荷.

**消息类型枚举 (CoherenceMessageType)**:

| 类型 | 方向 | 用途 |
|---|---|---|
| **ReadReq** | EP-SNF → ubio | 请求 Shared 或 Unique 权限 |
| **ReadResp** | ubio → EP-SNF | Grant 响应 (Shared/Exclusive/Modified/BUSY), 带数据 |
| **RecallReq** | ubio → EP-RNF | 请求 owner 返回/交换数据 |
| **RecallResp** | EP-RNF → ubio | Owner 召回响应, 带数据 |
| **InvalidateReq** | ubio → EP-SNF | 使一个 sharer 失效 |
| **InvalidateAck** | EP-SNF → ubio | Sharer 失效完成确认 |
| **WritebackReq** | EP-SNF → ubio | 写回脏数据 |
| **WritebackResp** | ubio → EP-SNF | 写回确认 |
| **EvictReq** | EP-SNF → ubio | 驱逐干净行 |
| **EvictResp** | ubio → EP-SNF | 驱逐确认 |
| **UpgradeReq** | EP-RNF → ubio | 请求 Shared→Unique 升级 |
| **UpgradeResp** | ubio → EP-RNF | 升级确认 (带 invalidation 目标掩码) |
| **UpgradeDoneReq** | EP-RNF → ubio | 本地升级完成 |
| **UpgradeDoneResp** | ubio → EP-RNF | 升级完成确认 |
| **ClearReq** | EP-SNF → ubio | 提交 GRANT_HANDSHAKE |
| **ClearResp** | ubio → EP-SNF | Clear 确认 |
| **UpgradeAckNotify** | ubio → EP-RNF | 所有 invalidation ack 已收到 (Ack(true) 就绪) |
| **QueryLineMetaReq/Resp** | EP-SNF ↔ ubio | 查询目录元数据快照 |
| **HomeWritebackNotify** | EP-SNF → ubio | HN-F 完成 DDR4 写回 |
| **BarrierReached** | gem5 → barrier | 节点已到达栅栏 |
| **BarrierRelease** | barrier → gem5 | 栅栏释放 |
| **MetaRNFReadReq/Resp/WriteReq** | ubio ↔ MetaRNF | 256B 元数据页访问 |

**消息头字段 (CoherenceMessageHeader)**:
```
type, srcNode/srcSocket, dstNode/dstSocket,
homeNode/homeSocket, ingressSocket,
requesterNode, targetNode,
flags (uint32_t),
homeLinePa, localLinePa,
epoch, reqId, seqNum,
enqueueTick, readyTick
```

**消息标志 (CoherenceMessageFlags)**:

| 标志 | 位 | 含义 |
|---|---|---|
| `CFLAG_WRITE_INTENT` | 0 | 请求有写意图 |
| `CFLAG_KEEP_AS_CLEAN` | 1 | Owner 写回后保留干净副本 |
| `CFLAG_ACCEPTED` | 2 | 响应被接受 |
| `CFLAG_DATA_RETURNED` | 3 | 数据已返回 |
| `CFLAG_HAS_DATA` | 4 | 消息携带数据 |
| `CFLAG_IS_READ_RECALL` | 5 | Recall 由读触发 (降级到 Shared) |
| `CFLAG_BUSY` | 6 | 资源忙 (协议层 BUSY) |
| `CFLAG_DATA_FORWARDED` | 7 | 数据从 owner 直接转发到 requester |

### 4.3 ReadResp Body 字段

```cpp
struct UBReadRespBody {
    int8_t grantType;           // -1=BUSY, 0=Shared, 1=Exclusive, 2=Modified
    int8_t dataSource;          // 0=HomeMemory, 1=RecallBuffer, 2=NoData
    int16_t pendingInvCount;    // 待处理 invalidation 计数 (-1 表示无)
    Tick grantVisibleTick;
    Tick sentinelVisibleTick;
    bool recallNeeded;
    int recallOwnerNode;
    uint64_t authEpoch;         // 授权 epoch
    uint64_t committedEpoch;    // 当前已提交 home epoch
    uint64_t pendingInvMask;    // 等待失效的 sharers 掩码
    uint8_t grantData[64];      // 可选 recall-buffer 载荷
};
```

---

## 5. 全局目录状态 — 目录侧 (Home UBCC Side)

### 5.1 MESI 提交状态 (UBCCMESIState / DirEntry)

```cpp
enum class UBCCMESIState : uint8_t {
    G_I = 0,  // Invalid — 无数缓存副本
    G_S = 1,  // Shared — 一个或多个 sharer, 无 owner
    G_E = 2,  // Exclusive — 干净独占 owner
    G_M = 3,  // Modified — 脏独占 owner
};
```

**DirEntry 字段**: `lineAddr`, `state`, `sharersMask` (64 位), `epoch`, `residentDirty`.

**不变式**:
- `G_E` 和 `G_M`: `popcount(sharersMask) == 1` (lockstep 不变式); 该唯一置位 = owner.
- `G_S`: `sharersMask != 0` (空 `G_S` 须规范化为 `G_I`).
- epoch 严格单调递增 (每次提交 `newEpoch >= oldEpoch`).

### 5.2 未完成操作状态机 (OutstandingRequest / OpType/OpStage)

Home 端对所有进行中的操作使用 `OutstandingRequest` 跟踪. 每 PA 最多一个活跃 outstanding.

```
OpType:
├── RECALL            - 回收 owner 数据后再授权
├── INVALIDATE        - 升级到 Unique 前先行失效 sharers
├── GRANT_HANDSHAKE   - 授权待提交, 等 requester 发送 Clear
└── UPGRADE_PENDING   - 本地升级四消息握手中

OpStage:
├── CREATED                - 刚创建, 无响应
├── WAITING_TARGET_RESP    - RECALL: 等待 owner recall 响应
├── WAITING_ALL_ACKS       - INVALIDATE / UPGRADE_PENDING: 等待所有 sharer ack
├── WAITING_LOCAL_DONE     - UPGRADE_PENDING: 等待 OuterUpgradeDone
├── WAITING_CLEAR           - GRANT_HANDSHAKE: 等待匹配 Clear
├── DONE                   - 终态: 操作成功完成 (RECALL.DONE = 仅 requester 可消费)
├── CANCELLED              - 终态: 被拒绝或验证失败
├── TIMED_OUT              - 终态: 重试预算耗尽
└── PERSISTENT_BUSY        - 终态: 不可撤销 post-ack, 仅接受 Done
```

### 5.3 OutstandingRequest 字段

```
linePa                - 关联缓存行 (home PA 视图)
baseEpoch              - requester 观察到的已提交 epoch (校验基线)
reservedEpoch          - 待提交的 epoch (committed_epoch + 1)
reqId                  - requester 分配的 ID (home 回显)
opType / stage         - 类型 / 状态
requesterNode / requesterSocket  - 等待完成者
homeNode / targetNode  - home 节点 / recall 目标
targetMask             - 失效目标掩码 (待失效 sharers)
intendedState          - 保留但未提交的目录结果 (MESIState)
intendedSharersMask    - 目标 sharers 集
intendedOwnerNode      - 目标 owner
intendedDirty          - 目标脏标志
reqType / writeIntent  - 原始请求参数
recallBarrierDone      - recall 屏障已通过
invalidateBarrierDone  - invalidation 屏障已通过
replayArmed            - 此 grant 由 replay 创建 (重试命中允许)
createTick / respTick / deadlineTick
dataBuf[64] / dataValid - recall 数据缓冲区
dataSource             - 数据来源 (HomeMemory/RecallBuffer/NoData)
upgradeTargetMask / upgradePendingAckCount / upgradeAckMask  - UPGRADE 专用
upgradeDoneArrived / upgradeDoneEpoch   - TENTATIVE 缓存 (Done 早到于 acks)
```

---

## 6. 本地请求端状态 — 请求端 (Requester Side)

### 6.1 RequesterLineState (EPBackend)

```cpp
enum class RequesterLineState {
    R_I,            // 无全局权限
    R_WAIT_GRANT,   // 远程 miss 发出, 等待 home grant
    R_S,            // 持有共享读权限
    R_E,            // 干净独占 owner (GrantExclusive)
    R_M             // 脏 modified owner (GrantModified)
};
```

**RequesterLineEntry 字段**: `lineAddr`, `state`, `pendingReq`, `epoch`, `reqId`, `writeIntent`, `homeNode`.

### 6.2 EPBackend 关键接口

| 方法 | 用途 |
|---|---|
| `handleRemoteMiss(linePa, neededPerm, writeIntent, ingressSocket, outHomeNode)` | 向 home UBCC 发起远程 miss |
| `handleGrant(linePa, grant, homeNode)` | home UBCC 授权后更新本地记账 |
| `sendClear(linePa, homeNode, epoch, reqId)` | 提交 GRANT_HANDSHAKE |
| `notifyLocalWriteUpgrade(...)` | 发起本地写升级 (Shared→Unique) |
| `sendUpgradeDone(...)` | 发送本地升级完成确认 |
| `handleRecallRequest(recallMsg)` | 接收并处理 recall 请求 |
| `sendRecallResponse(response)` | 发送 recall 响应 |
| `handleInvalidationRequest(invMsg)` | 接收失效请求 |
| `sendInvalidationAck(ack)` | 发送失效确认 |
| `handleWriteback(linePa, keepAsClean, data)` | 处理写回 |
| `handleEvict(linePa)` | 处理驱逐 |

### 6.3 EPSNFController — Requester 端 SNF

**重试队列 (RetryEntry)**: 当 home BUSY 但 EP-SNF 收到 ReadNoSnp 时, 重试条目入队. 包含 `linePa`, `neededPerm`, `writeIntent`, `hnReq/fwdReq`.

**延迟 Grant (DeferredGrantEntry)**: Grant ready 但 CompData 必须延迟 1 个 tick 以满足 TBE 时序不变量 `I10`. 延迟时不重新检查 epoch.

**Pending Writeback**: HN-F WriteNoSnp → EP-SNF → 在数据拍到达前跟踪 DBID → 全部到达后通知 home.

### 6.4 EPRNFController — Owner/Sharer 端 RNF

**PendingChiTxn**: 对 HN-F 发起的 CHI 请求 (ReadShared/CleanUnique/ReadUnique). 在每个 PA 的单入口 snoop 槽排队后续 snoops.

**UpgradePending**: 本地上级 (SnpCleanInvalid → OuterUpgradeReq) 的 pending 上下文. `rejected` 标志表示 home 拒绝.

**重试队列**: 跨重试保留最强操作 (ReadUnique > CleanUnique > ReadShared). 过期 epoch 被丢弃.

---

## 7. 全局请求状态变化与协议流

### 7.1 关键设计原理

1. **目录式 (Directory-Based)**: home UBCC 维护每行 sharer 掩码、owner 跟踪和 epoch 计数.
2. **Epoch 校验**: 所有消息携带 epoch; 过期 epoch 被拒绝以防止竞态.
3. **预留再提交 (Reserve-then-Commit)**: `GRANT_HANDSHAKE` 预留目标状态但等待 requester 发送 `Clear` 后才提交.
4. **墓碑窗口 (Tombstone Window)**: 完成的 grants 产生墓碑 W 时间内支持幂等 Clear 重放.
5. **请求者私有的 RECALL.DONE**: 只有引起 recall 的请求者能消费 DONE 状态; 其他请求者排队.
6. **UpgradeInvalidateFix**: UPGRADE_PENDING 的失效广播由 home 发起 (与 INVALIDATE 路径统一), 避免孤儿 pending.

### 7.2 远程读 Miss (ReadReq → ReadResp → Clear 流程)

```
                    Requester (EP-SNF)              Home (UBCC)
                    ==================              ============
CPU load ──────────▶ HN-F ReadNoSnp
                       │
                       ▼
                    EP-SNF recvRequestMsg
                    → isDsmAddr? yes
                    → handleRemoteMiss(PA, perm, writeIntent)
                        │
                        ▼  ReadReq(src=reqNode, epoch=committed, reqId=X)
                    ┌────────────────────────────────────┐
                    │                                    │
                    │   processOuterRequest(PA, reqType, │
                    │     writeIntent, reqNode, epoch,   │
                    │     reqId)                         │
                    │                                    │
                    │   ┌─ ensureResidentForAccess       │
                    │   │  (backstore fill if miss)      │
                    │   ├─ checkTombstone(dup Clear?)    │
                    │   ├─ check existing outstanding    │
                    │   │  → same req in WAITING_CLEAR?  │
                    │   │    grant idempotently          │
                    │   │  → other active outstanding?   │
                    │   │    enqueue to _pendingRequesters│
                    │   │    return BUSY                 │
                    │   ├─ allocateReservedEpoch(entry)   │
                    │   │  = committed_epoch + 1         │
                    │   └─ switch(committed_state):      │
                    │                                    │
                    │   G_I:                             │
                    │     RS → GRANT_HANDSHAKE(G_S)      │
                    │     RU+!write → GRANT_HANDSHAKE(G_E)│
                    │     RU+write → GRANT_HANDSHAKE(G_M) │
                    │                                    │
                    │   G_S:                             │
                    │     RS → GRANT_HANDSHAKE(G_S)      │
                    │     RU (other sharers exist):       │
                    │       create INVALIDATE            │
                    │       fanoutInvalidateTargets       │
                    │       return BUSY (wait all acks)   │
                    │     RU (no other sharers):          │
                    │       GRANT_HANDSHAKE(G_E/G_M)      │
                    │     RU (existing sharer):           │
                    │       return BUSY → use upgrade path│
                    │                                    │
                    │   G_E / G_M:                       │
                    │     RECALL.DONE for this req:       │
                    │       create GRANT_HANDSHAKE        │
                    │       grant immediately            │
                    │     RECALL.DONE for other req:     │
                    │       enqueue, return BUSY         │
                    │     owner exists ≠ req:             │
                    │       create RECALL                 │
                    │       initiateRecall(ownerNode)     │
                    │       return BUSY (wait recall)     │
                    │     same owner / no recall needed:  │
                    │       GRANT_HANDSHAKE               │
                    │                                    │
                    │   SHALL NOT modify committed       │
                    │   DirEntry (only on Clear)          │
                    │                                    │
                    │◄── ReadResp(grant, data, epoch) ──│
                    │                                    │
                    ▼                                    │
               EP-SNF receives grant                      │
               → CompData to L2                           │
               → _requesterLines[PA] = R_WAIT_GRANT      │
                                                          │
               ── (later, when HN-F retires client) ──   │
                                                          │
               EP-SNF sendClear(PA, home, epoch, reqId)  │
                    │                                    │
                    │  ClearReq(src=reqNode, epoch,      │
                    │          reqId=X)                   │
                    │────────────────────────────────────▶│
                    │                                    │
                    │   processClear(PA, srcNode, epoch, │
                    │                reqId):              │
                    │   ├─ check tombstone (dup Clear)   │
                    │   ├─ find outstanding               │
                    │   ├─ assert(opType==GRANT_HANDSHAKE)│
                    │   ├─ assert(stage==WAITING_CLEAR)   │
                    │   ├─ assert(barriers: RECALL/INV    │
                    │   │  already DONE)                  │
                    │   ├─ commitIntendedResult(entry,ost)│
                    │   │  entry.state = intendedState    │
                    │   │  entry.sharersMask = intended...│
                    │   │  entry.epoch = reservedEpoch    │
                    │   ├─ _directory.update(entry)       │
                    │   ├─ retireToTombstone(ost, true)   │
                    │   │  → GrantHandshakeTombstone      │
                    │   │  → expireTick = now + W         │
                    │   ├─ removeOutstanding(PA)          │
                    │   ├─ replayPendingRequesters(PA)    │
                    │   │  dequeue head requester         │
                    │   │  re-call processOuterRequest()  │
                    │   │  with rebased epoch on NEW      │
                    │   │  committed state                │
                    │   └─ replayResidentWaiters(PA)      │
                    │                                    │
                    │◄── ClearResp(accepted=true) ───────│
                    ▼                                    │
               _requesterLines[PA] = R_S/R_E/R_M          │
               DONE                                       │
```

### 7.3 Recall 生命周期

```
触发: 在 G_E 或 G_M 中, 有 owner ≠ requester.

Home UBCC:
  [CREATED] → initiateRecall → RecallReq → 发送到 owner node
  [WAITING_TARGET_RESP] ← 等待 owner 响应

Owner Node (EPRNFController):
  接收 RecallReq → EPBackend.handleRecallRequest()
  → _activeRecallPAs[PA] = true
  → EPRNFController 发起 ReadUnique 到 HN-F (写 recall)
    或 ReadShared 到 HN-F (读 recall)
  → HN-F 发送 SnpUnique/SnpShared 给本地缓存
  → CHI 数据拍到达, Comp_UC/CompData 到达
  → 构造 RecallResp {dataReturned, dataPayload, epoch}
  → EPBackend.sendRecallResponse()

Home UBCC 收到 RecallResp:
  processRecallResponse(PA, ownerNode, dataReceived, epoch, reqId, dataBlk)
  ├─ epoch 校验 (stale → reject)
  ├─ 匹配 RECALL outstanding
  ├─ ost->recallBarrierDone = true
  ├─ ost->dataValid = dataReceived
  ├─ 捕获 recall 数据到 ost->dataBuf
  ├─ removeOutstanding(PA)
  ├─ 创建新的 GRANT_HANDSHAKE
  │  - stage = WAITING_CLEAR
  │  - recallBarrierDone = true
  │  - replayArmed = true
  │  - copy data + dataSource=RecallBuffer
  │  - 根据 reqType/writeIntent 设置 intendedState
  └─ sendGrantPush (主动推送 ReadResp 到 requester)

RECALL orphan 清理:
  isExpiredRecall(): age > _recallTimeout(1,000,000 ticks)
  cleanupExpiredRecallIfNeeded(): remove + replay waiters
```

### 7.4 Invalidate 生命周期

```
触发: G_S 中非 requester sharers 存在且请求 Unique.

Home UBCC:
  [CREATED] → fanoutInvalidateTargets → InvalidateReq × N → 发送到各 sharer
  [WAITING_ALL_ACKS] ← 等待所有 sharer ack

Sharer Node (EPSNFController):
  接收 InvalidateReq → EPBackend.handleInvalidationRequest()
  → EPRNFController.startCleanUnique() → HN-F 发送 SnpCleanInvalid
  → 本地缓存失效 → SnpResp_I
  → Comp_UC 到达
  → EPBackend.sendInvalidationAck() → InvalidateAck 发回 home

Home UBCC 收到 InvalidateAck:
  processInvalidationAck(PA, ackNode, epoch, reqId)
  ├─ epoch 校验
  ├─ 匹配 INVALIDATE outstanding
  ├─ 检查重复 ack
  ├─ ost->ackMask |= nodeBit
  ├─ ost->pendingAckCount--
  ├─ 提交 sharer 移除 (entry.sharersMask &= ~nodeBit)
  │  - 若 G_S 且 sharersMask==0 → 规范化为 G_I
  ├─ _directory.update(entry)
  └─ 若 pendingAckCount==0:
     - ost->invalidateBarrierDone = true
     - ost->stage = DONE
     - 将 INVALIDATE 原地转换为 GRANT_HANDSHAKE:
       * ost->opType = GRANT_HANDSHAKE
       * ost->stage = WAITING_CLEAR
       * ost->replayArmed = true
       * 保留 intendedState/owner/sharers
     - sendGrantPush (主动推送 grant)

边界情况 (effectiveMask == 0 即初始没有活跃 sharers):
  立即将 INVALIDATE → GRANT_HANDSHAKE, 跳过 WAITING_ALL_ACKS 阶段.
```

### 7.5 Upgrade 生命周期 (本地 Shared→Unique 升级)

```
触发: EP-RNF 收到 SnpCleanInvalid (HN-F 发起 CleanUnique).
      非召回情形 → 这是真正的本地升级.

流程 (四消息握�):

1. OuterUpgradeReq (EP-RNF → ubio):
   EPRNFController.handleSnpCleanInvalid()
   → _upgradePending[PA] = {valid, homeNode, epoch, reqId, hnfDest}
   → EPBackend.notifyLocalWriteUpgrade(PA, homeNode, desiredPerm, cause)
   → UpgradeReq(src=reqNode, epoch, reqId, desiredPerm, cause)

2. Home UBCC: processOuterUpgradeReq()
   ├─ 验证 requester 是已提交的 sharer
   │  → 不是? PERMANENT reject (被淘汰, 需 ReadUnique)
   ├─ 验证无其他 outstanding (否则 TEMPORARY reject)
   ├─ allocateReservedEpoch
   ├─ freeze targetMask = entry.sharersMask & ~requesterBit
   ├─ 创建 UPGRADE_PENDING outstanding
   ├─ 若有其他 sharers (targetMask≠0):
   │  - fanoutInvalidateTargets (home 发起失效广播!)
   │  - stage = WAITING_ALL_ACKS (等待所有 ack 后 Ack(true))
   └─ 若无其他 sharers (targetMask==0):
      - stage = WAITING_LOCAL_DONE (立即 Ack(true))
   ── UpgradeResp(accepted=true/false, targetMask, committedEpoch) ──▶

3. 升级就绪信号:
   当 UPGRADE_PENDING 的 WAITING_ALL_ACKS 完成时:
   → ost->invalidateBarrierDone = true
   → ost->accepted = true
   → ost->stage = WAITING_LOCAL_DONE
   → emit UpgradeAckNotify 到 requester node
   → EPBackend.notifyUpgradeAckReady(PA)
   → EPRNFController.receiveUpgradeAck(PA)
   → 发送 SnpResp_I 到 HN-F (snoop 被释放)

4. OuterUpgradeDone (EP-RNF → ubio):
   本地升级完成后:
   → EPBackend.sendUpgradeDone(PA, homeNode, epoch, reqId)
   → UpgradeDoneReq

   Home UBCC: processOuterUpgradeDone()
   ├─ 匹配 UPGRADE_PENDING outstanding
   ├─ 若 stage==WAITING_ALL_ACKS (Done 早于 acks):
   │  - TENTATIVE: 缓存 Done 参数 (upgradeDoneArrived=true)
   │  - 等待 acks 完成后自动提交
   ├─ 若 stage==WAITING_LOCAL_DONE:
   │  - commitIntendedResult(entry, *ost)
   │  - _directory.update(entry)
   │  - ost->stage = DONE
   │  - removeOutstanding(PA)
   │  - replayPendingRequesters(PA)
   └─ 返回 UpgradeDoneResp(accepted=true)

拒绝处理:
  - PERMANENT reject (not-sharer): requester 必须放弃升级,
    发送 SnpResp_I(stale=true) 给 HN-F, 然后 ReadUnique(I→M).
  - TEMPORARY reject (other outstanding): requester 重试升级.
  - 拒绝后的升级尝试: 从 pending 队列重新发起, 新 reqId.
```

### 7.6 Writeback 生命周期

```
触发: HN-F WriteNoSnp → EP-SNF.

EP-SNF:
  → handleWriteback(PA, keepAsClean, data)
  → WritebackReq(src, epoch, keepAsClean, data)

Home UBCC: processWriteback()
  ├─ ensureResidentForAccess (可能触发 backstore fill)
  ├─ 检查 line busy (outstanding → retry)
  ├─ epoch 校验 (stale → reject, _staleRejectedCount++)
  ├─ owner 匹配校验 (不匹配 → reject, _ownerMismatchRejectedCount++)
  └─ 处理:
     - keepAsClean=yes: entry.state = G_E, sharers = {reqNode}
     - keepAsClean=no:  entry.state = G_I, sharers = {}
     - 持久化数据到 DSM: _host->writeDsmData(PA, data)
     - _directory.update(entry)
     - scheduleBackstoreWrite(PA)
  → WritebackResp(success=true/false)
```

### 7.7 Evict 生命周期

```
触发: 本地替换策略驱逐干净缓存行.

Home UBCC: processEvict()
  ├─ epoch 校验
  ├─ 检查被驱逐者是否是 sharer 或 owner
  │  - dirty owner evict → REJECTED (必须先 writeback)
  │  - 不在 sharers 中 → REJECTED
  ├─ 从 entry.sharersMask 中移除
  ├─ 如果是 clean owner: 清除所有权 (sharersMask=0)
  ├─ 重新确定新状态:
  │  - 无 sharers 无 owner → G_I
  │  - 有 owner → 保持 G_E/G_M
  │  - 仅有 sharers → G_S
  └─ _directory.update(entry)
  → EvictResp(success=true/false)
```

### 7.8 HomeWriteback 通知

```
触发: HN-F 完成 DDR4 写回后通过 EPBackend → UBCC.

Home UBCC: notifyHomeWritebackComplete(homePa)
  ├─ 确认条目存在且非 G_I (否则 NOP)
  ├─ 检查 line busy (若有新请求 → defer)
  ├─ entry.state = G_I, entry.sharersMask = 0
  └─ _directory.update(entry)
```

---

## 8. Epoch 时序机制

### 8.1 半范围 Epoch 比较 (§3.1.2)

```cpp
bool isNewerEpoch(uint64_t a, uint64_t b) const {
    uint64_t delta = (normalize(a) - normalize(b)) & mask;
    uint64_t half_range = (epochBits == 64) ? (1ULL << 63) : (1ULL << (bits-1));
    return delta != 0 && delta < half_range;
}
```

- `checkEpochForLine(PA, responseEpoch)`: 若 `isNewerEpoch(committedEpoch, responseEpoch)` 则迟消息已过期.
- 默认 `epochBits=64`; 可配置以支持回绕实验.

### 8.2 Epoch 分配与提交流程

```
committedEpoch = entry.epoch          (当前已提交, 目录中可见)
reservedEpoch  = committedEpoch + 1   (分配, 待提交)

processOuterRequest / processOuterUpgradeReq:
  → allocateReservedEpoch(entry)   # reservedEpoch = committed_epoch + 1
  → oreq->reservedEpoch = reservedEpoch
  → oreq->baseEpoch = reqNode observed committed epoch

processClear / processOuterUpgradeDone:
  → commitIntendedResult():
    validateEpochMonotonic(oldEpoch, ost->reservedEpoch)
    entry.epoch = reservedEpoch   # 提升已提交 epoch
```

### 8.3 墓碑窗口与幂等性

```
processClear 接受后:
  retireToTombstone(ost, accepted=true)
  → GrantHandshakeTombstone{PA, baseEpoch, reqId, expireTick=now+W}

后续 ReadReq 到达 (丢失 grant, 重试):
  checkTombstone(PA, epoch, reqId, outAccepted)
  → 扫描 _tombstones[PA] deque 查找 (epoch, reqId) 匹配
  → 若命中且未过期 → 幂等返回 grant (无需重新执行)
  → 若未命中 → 正常 alloc + handshake 流程

cleanupTombstones(): 在每次 tombstone 查询时删除已过期的 front 条目.
```

---

## 9. 紧急情况与异常处理

### 9.1 Recall Orphan 清理

```
isExpiredRecall(): createTick + _recallTimeout < curTick
cleanupExpiredRecallIfNeeded(): remove outstanding + replay waiters
cleanupExpiredRecalls(): 遍历所有 outstanding 批量清理
```

### 9.2 请求排队 (_pendingRequesters)

每个 PA 最多 `MAX_PENDING_PER_PA = 32` 个排队请求者.

```
排队时机:
  - 有活跃 outstanding (RECALL/INVALIDATE/UPGRADE_PENDING 进行中)
  - RECALL.DONE 但属于不同请求者

队列中已有相同 (requester, reqId) → BUSY (重复重试)

队列满 → drop_full (语义上 BUSY — 调用者将重试)

重放时机:
  processClear 提交后 → replayPendingRequesters(PA)
  → 弹出队首请求者
  → 使用新 committed state 重新调用 processOuterRequest()
```

### 9.3 ResidentDir 容量溢出

```
策略: Spill (默认) 或 NaiveEvict

Spill:
  - 请求者加入 _residentWaiters
  - 触发 backstore 驱逐 → 写入 → 释放常驻槽
  - 随后的请求触发 backstore fill (MetaRNF)

NaiveEvict:
  - evictOneVictimNaive → 强制失效/写回 + 移除目录条目
```

### 9.4 双边故障保护

所有写操作 (Writeback/Evict/WritebackNotify) 在修改目录状态/清除所有权前等待 backstore 写入确认.

---

## 10. 传输层: PDES 时钟同步 (保守式)

### 10.1 核心不变式

> 一个进程绝不把自己的虚拟时钟推进到超过"任一对端可能给它发消息的最早时刻".

### 10.2 关键量

| 参数 | 默认值 | 含义 |
|---|---|---|
| `linkLatency` | 100,000 ps | 发送消息的 `timestamp = 发送时刻 + linkLatency` |
| `syncInterval` | 100,000 ps | 心跳/前瞻窗口 |

### 10.3 Per-Port Done 机制

当 port 收到 `TERMINATE` 消息:
- 标记该 port `done`
- 不再 `emitSync`、`recv`、不把其 `safeTs` 计入 `min`

这防止已终止的对端冻结全局时钟.

### 10.4 各进程主循环

```
while (!allPortsDone) {
    for each non-done port: emitSync(tick)
    for each non-done port: pollAndProcess (排空可见消息, 处理 TERMINATE)
    minTs = min(non-done ports safeTs)
    if (minTs > tick) tick = minTs   // 安全跳进
    else std::this_thread::yield()   // 被卡住 → yield 忙等
}
```

---

## 11. 拓扑与配置

### 11.1 缓�参数 (`gem5/configs/ruby/chi_params.json`)

| 组件 | 大小/延迟 | 类型 |
|---|---|---|
| L1i Cache | 32KB | 指令 |
| L1d Cache | 32KB | 数据 |
| L2 Cache | 256KB | 统一 |
| L3 (HN-F) | ~TBE 4096 entries × 3 | 每 socket |

### 11.2 拓扑配置 (`configs/`)

| 文件 | 布局 |
|---|---|
| `topo_1s.json` | 3 nodes × 1 socket |
| `topo_2s.json` | 3 nodes × 2 sockets (dual-socket) |
| `topo_8n1s.json` | 8 nodes × 1 socket |
| `topo_8n2s.json` | 8 nodes × 2 sockets |

### 11.3 关键超时与限制

| 参数 | 默认值 | 含义 |
|---|---|---|
| `MAX_PENDING_PER_PA` | 32 | 每 PA 最大排队请求者 |
| `_tombstoneWindowW` | 100,000 ticks | 墓碑窗口 |
| `_recallTimeout` | 1,000,000 ticks | Recall orphan 清理超时 |
| `_epochBits` | 64 | Epoch 宽度 |
| ResidentDir capacity | 57,344 条目 | 8192 sets × 7 ways |
| ResidentDir SRAM | 512KB | 总计 SRAM 预算 |

---

## 12. 形式化验证

核心协议已用 TLA+/TLC 模型检测进行形式化验证:

| 类别 | 状态数 | 结果 |
|---|---|---|
| 安全性 (4 invariants) | 20,980,755 | 零反例 |
| 活性 (4 temporal) | 128,577 | 零反例 |
| 故障容错 (9 invariants) | 23,242,903 | 零反例 |
| 动作覆盖率 | 15/15 (100%) | 全部触发 |

**发现的真实 Bug**: RECALL orphan 死锁 (FV3-LEAK-001) — 形式化证明修复消除该问题.

---

## 13. 测试覆盖

~102 个 E2E 测试用例, 按拓扑/特性分类:

| 类别 | TC 范围 | 描述 |
|---|---|---|
| 基础协议 | TC1-10 | 本地/远程读写, ping-pong, writeback/evict, upgrade |
| 并发与竞态 | TC11-19 | 本地升级, barrier, release/acquire, 双升级竞态 |
| 目录/Backstore | TC20-31 | 容量压力, bloom false positive, epoch 回绕 |
| Dual-Socket | TC32-39 | 跨 socket 读写, writeback, pingpong, NUMA 压力 |
| 延迟/容量/8节点 | TC80-94 | 跨节点延迟, 8节点 ring/hotspot/butterfly |
| 故障注入 | TC47-49 | Drop/duplicate/reorder 故障测试 |

**运行命令**: `bash tests/e2e/run_multi.sh <TC编号...>`
