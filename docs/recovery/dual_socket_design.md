# Dual-Socket Design Plan v1.0

**状态**：已合成，可直接作为实施基线  
**适用范围**：gem5 Ruby CHI / CC-EP / 单节点单 L3、双 socket UBCC 扩展  
**约束前提**：

- `sharersMask` 保持 **16-bit、node 粒度**，不扩为 `(node,socket)` 粒度。
- `ResidentDir` 改为 **per-socket**；每节点目录总容量翻倍。
- `L3/HN-F` 仍然 **每节点单实例**，本次不拆分为 per-socket L3。
- `EPBackend` 保持 **per-node 单实例**。
- `UBRouter / UBAdapter / UBCC / MetaRNF` 改为 **per-(node,socket)**。
- `homeSocket` 由 **PA 编码**直接决定；`ingressSocket` 由 **CHI sideband**携带，仅用于 NUMA latency hint / 路由源 socket。
- `WritebackReq` 采用 **optimistic epoch validation**：只校验 `expectedEpoch`，不携带 `expectedOwnerNode`。
- `HomeWritebackNotify` 为新 `UBMsgType`。
- `QueryLineMetaReq/Resp` 为新 `UBMsgType`，用于写回前查询 `{epoch, ownerNode}`。
- `num_sockets=1` 时必须走同一套数组化代码，`socketId=0`，现有 E2E 全通过。

---

## 1. Architecture Diagrams（Before / After）

### 1.1 当前单 socket 基线

```text
Node i

CPU/L1/L2 clusters
      │
    HN-F_i (single L3)
      │
  ┌───┴───────────────┐
  │ DL_SNF_i          │ EP_SNF_i
  │ local/home DRAM   │ DSM proxy
  └───────────────────┘
             │
        EPBackend_i
         │      │
      EP-RNF_i  UBAdapter_i ─ UBRouter_i ─ UBCC_i
```

问题：

1. DSM 只编码 `homeNode`，不编码 `homeSocket`。  
2. `UBRouter`/`UBAdapter`/`UBCC` 只有 node 维度实例。  
3. `EPBackend` 仍持有直接 `_ubcc` 访问路径。  
4. 写回 fallback 依赖 direct `getEpochForLine()/getOwnerForLine()`。  
5. `MetaRNFController` 仍是 per-node singleton。  
6. HN-F / EP-SNF 缺少 `requesterSocket` / `ingressSocket` sideband。

### 1.2 目标双 socket 结构

```text
Node i

CPU/L1/L2 clusters on socket 0/1
            │
        HN-F_i (single per node)
            │
         EP_SNF_i
            │
        EPBackend_i (per-node)
        ┌───────────────┬───────────────┐
        │               │               │
  UBAdapter(i,0)   UBAdapter(i,1)   EP-RNF_i
        │               │
  UBRouter(i,0)    UBRouter(i,1)
        │               │
   UBCC(i,0)         UBCC(i,1)
        │               │
 MetaRNF(i,0)      MetaRNF(i,1)
```

核心原则：

1. **目录归属由 `homeSocket` 决定。**  
2. **请求从 `ingressSocket` 出发，抵达 `homeSocket`。**  
3. **home UBCC fanout recall/invalidate 时，固定走“与自己同 socket 的 router plane”。**  
4. **HN-F 仅保存 `requesterSocket` 于 TBE，用于 sideband / latency hint；不进入持久目录。**

### 1.3 关键数据流

```text
Read miss:
CPU(socket=r) → HN-F(TBE.requesterSocket=r)
             → EP_SNF(msg.ubcc_requester_socket=r)
             → EPBackend
             → UBAdapter(node=req, socket=r)
             → UBRouter(req,r)
             → UBRouter(home,homeSocket)
             → UBCC(home,homeSocket)

Recall / Invalidate:
UBCC(home,homeSocket)
   → UBRouter(home,homeSocket)
   → UBRouter(dst,homeSocket)
   → UBAdapter(dst,homeSocket)
   → EPBackend(dst)
   → EP-RNF(dst)
```

---

## 2. PA Encoding Scheme

### 2.1 现状基线

- C++ `NodeAddressMap.hh:24-56` 与 Python `CHI_basic_framework_config.py:46-80` 当前只支持：
  - `DSM_(request_node, home_node)`
  - 每个 node 视图只有 `N` 个 DSM 段。

### 2.2 新编码目标

采用已确认方案：**显式 `DSM_(home_node, home_socket)` 子段编码**。

每个 requester node 的 PA 视图改为：

```text
PHY_BASE_i + [0, 1*SEG)      : LocalPrivate
PHY_BASE_i + [1, 2*SEG)      : UbccExclusive
PHY_BASE_i + [2, 2+N*S) * SEG: DSM_(homeNode, homeSocket)
PHY_BASE_i + metadata_base   : Metadata private
```

其中：

- `N = num_nodes`
- `S = num_sockets`
- 当前目标 `S = 2`

### 2.3 地址公式

建议统一为：

```cpp
dsm_index   = homeNode * numSockets + homeSocket;
pa          = nodeBase(requestNode)
            + 2 * segSize
            + dsm_index * segSize
            + offset;

homeNode    = ((pa - nodeBase(requestNode) - 2 * segSize) / segSize) / numSockets;
homeSocket  = ((pa - nodeBase(requestNode) - 2 * segSize) / segSize) % numSockets;
```

### 2.4 对现有布局的直接影响

对于 `N=3, S=2`：

- DSM 窗口从原先 `3 * SEG` 扩为 `6 * SEG`
- `metadata_private_base` 必须从原 `phy_base + 5*SEG` 后移到 `phy_base + 8*SEG`
- `NodeConfig.dsm_global_range()` / `dsm_local_range()` / `dsm_range_for()` 都要改为 socket-aware 版本

### 2.5 单 socket 退化行为

当 `num_sockets=1` 时：

- `dsm_index = homeNode`
- 新公式退化为当前实现
- `metadata_private_base` 自动退化回当前位置
- 不需要单独维护 legacy 分支

---

## 3. UBMsg Header Changes

### 3.1 现状基线

- `UBMsg.hh:17-35` 只有现有消息类型。  
- `UBMsg.hh:49-73` 的 `UBMsgHeader` 只有 node 维度字段，无 socket。  
- `ubMsgToString()` (`UBMsg.hh:216-229`) 也未打印 socket。

### 3.2 Header 扩展

在 `UBMsgHeader` 中新增：

```cpp
uint16_t srcSocket;
uint16_t dstSocket;
```

规则：

- `srcSocket`：消息发起的本地 socket 平面
- `dstSocket`：目标 UBRouter/UBCC/UBAdapter 所在 socket 平面
- `homeSocket` **不新增独立 header 字段**，直接从 `homeLinePa` 解码

### 3.3 新消息类型

在 `UBMsgType` 增加：

```cpp
QueryLineMetaReq,
QueryLineMetaResp,
HomeWritebackNotify,
```

推荐顺序：放在 `WritebackReq/Resp` 附近，便于归类。

### 3.4 新 body 定义

#### QueryLineMetaReq

- 无额外 body 字段；用 `homeLinePa` 作为 key。

#### QueryLineMetaResp

建议 body：

```cpp
struct UBQueryLineMetaRespBody {
    bool found;
    int16_t ownerNode;   // -1 if none
    uint8_t mesi;        // G_I/G_S/G_E/G_M snapshot
    uint64_t epoch;
};
```

用途：

- requester-side `_requesterLines` 无条目时，写回前查询 home committed metadata
- 只读查询，不创建 outstanding，不改变 epoch，不触发目录填充副作用

#### HomeWritebackNotify

建议 body 为空，复用 header：

- `homeLinePa`：home-view PA
- `epoch`：写回对应的 `expectedEpoch`
- `srcNode/srcSocket = home node/home socket`
- `dstNode/dstSocket = home node/home socket`

语义：

- HN-F → EP-SNF 完成对 home DRAM 的写入后，通知同 socket 的 home UBCC 进行最终目录释放
- UBCC 必须将其视为 **可丢弃的 stale 通知**：若当前 committed epoch 已变化，则直接忽略

### 3.5 所有消息统一携带 socket 字段

根据已确认决策，以下全部要填 `srcSocket/dstSocket`：

- `ReadReq/Resp`
- `WritebackReq/Resp`
- `EvictReq/Resp`
- `UpgradeReq/Resp`
- `UpgradeDoneReq/Resp`
- `ClearReq/Resp`
- `RecallReq/Resp`
- `InvalidateReq/Ack`
- `UpgradeAckNotify`
- `QueryLineMetaReq/Resp`
- `HomeWritebackNotify`

---

## 4. UBRouter Per-Socket Registry + Latency Tables

### 4.1 现状基线

- `UBRouter.hh:23-28` 注释仍假设 per-node only。  
- `UBRouter.hh:70-72` / `UBRouter.cc:17-29` 静态 registry 为 `std::map<int, UBRouter*>`。  
- `UBRouter.hh:81-82` 队列 key 为 `(srcNode,dstNode)`。  
- `UBRouter.cc:125-180` 本地/远端投递只判断 `dstNode`。

### 4.2 新 registry key

改为：

```cpp
using RouterKey = std::pair<int,int>; // (nodeId, socketId)
static std::map<RouterKey, UBRouter*> _routers;
```

接口改为：

```cpp
static UBRouter* getRouter(int nodeId, int socketId);
static void registerRouter(int nodeId, int socketId, UBRouter* router);
```

### 4.3 Router 实例属性

`UBRouter` 新增：

```cpp
int _socketId;
int socketId() const;
```

并在 Python `UBRouter.py` 增加参数：

```python
socket_id = Param.Int(0, "Socket ID for this router")
```

### 4.4 Pair queue key 扩展

队列 key 需至少扩为四元组：

```cpp
(srcNode, srcSocket, dstNode, dstSocket)
```

否则 `(node0,socket0→node1,socket1)` 与 `(node0,socket1→node1,socket1)` 会错误复用同一 FIFO。

### 4.5 本地投递条件

`drainReadyQueues()` 中：

- 当前：`msg.h.dstNode == _nodeId`
- 新逻辑：`msg.h.dstNode == _nodeId && msg.h.dstSocket == _socketId`

否则一律 remote-forward 到 `getRouter(msg.h.dstNode, msg.h.dstSocket)`。

### 4.6 home-side sharer routing 规则

已确认规则：

> `UBCC(node=H, socket=s)` fanout recall/invalidate 时，统一发往 `UBRouter(dstNode, socket=s)`。

因此：

- directory plane 由 `homeSocket` 锁定
- sharer 不需要扩展成 `(node,socket)` 粒度
- 远端 `EPBackend` 收到消息后，若需进一步面向本地 CHI 域处理，再用 `ingressSocket`/本地规则完成 node 内延迟建模

### 4.7 Latency 表语义

`ub_msg_latency` 继续作为配置入口，但语义扩展为：

- 默认可仍是单值 “per-hop latency”
- 若已有 per-pair 配置解析逻辑，则索引维度扩展为 `(srcNode,srcSocket,dstNode,dstSocket)`
- 单 socket 时，所有索引退化到 `socket=0`

---

## 5. EPBackend Multi-Adapter Routing

### 5.1 现状基线

- `EPBackend.hh:662-665` 当前持有 `_ubcc` 与单个 `_ubAdapter`。  
- `EPBackend.cc:119-123` 构造函数内直接 new `UBCCController`。  
- `EPBackend.cc:1243-1249` 直接 `_ubcc->notifyHomeWritebackComplete()`。  
- `EPBackend.cc:1284-1293` 直接 `_ubcc->getEpochForLine()/getOwnerForLine()`。

这与已确认的 **DETACHED message-passing only** 设计冲突。

### 5.2 目标对象关系

`EPBackend` 改为：

```cpp
std::vector<UBAdapter*> _ubAdapters;      // size = num_sockets
std::vector<MetaRNFController*> _metaRnfs; // size = num_sockets
int _numSockets;
```

删除：

- `UBCCController *_ubcc`
- `getUBCC()` 单实例接口
- `setUBAdapter(UBAdapter*)` 单指针接口

保留：

- `UBCC -> EPBackend` 的 backstore callback 绑定可继续存在
- 但 **EPBackend -> UBCC** 一律通过 `UBAdapter/UBRouter/UBMsg`

### 5.3 路由选择规则

#### Remote miss

- `homeSocket = addrMap.homeSocket(...)`（新接口）
- `srcSocket = ingressSocket`（来自 HN-F sideband）
- 发送适配器选 `_ubAdapters[srcSocket]`
- `ReadReq.dstSocket = homeSocket`

理由：

- 请求“从哪个本地 socket 进入系统”应保留在源 socket 平面
- 目录由 `homeSocket` 决定

#### Recall / Invalidate fanout

- 发起者是 home UBCC
- `srcSocket = homeSocket`
- `dstSocket = homeSocket`
- 远端 `EPBackend` 收到后，按该 socket 平面进入本地适配层

#### Clear / UpgradeDone / Writeback / Evict

- 目录提交类消息都以 `dstSocket = homeSocket`
- `srcSocket` 对于 requester 发起路径使用 `ingressSocket`

### 5.4 `handleRemoteMiss()` 签名变更

建议从：

```cpp
int handleRemoteMiss(uint64_t line_pa, int neededPerm, bool writeIntent,
                     int& outHomeNode);
```

改为：

```cpp
int handleRemoteMiss(uint64_t line_pa, int neededPerm, bool writeIntent,
                     int ingressSocket,
                     int& outHomeNode, int& outHomeSocket);
```

`RetryEntry` 也要存：

```cpp
int ingressSocket;
```

### 5.5 写回前 metadata 查询

当前 direct fallback：

- `EPBackend.cc:1284-1293`

改为：

1. 若 `_requesterLines` 有条目：继续用本地 requester epoch。  
2. 若没有条目：发送 `QueryLineMetaReq(homePa)` 至 `UBCC(homeNode, homeSocket)`。  
3. `QueryLineMetaResp` 返回 `{found, epoch, ownerNode}`。  
4. `WritebackReq` 使用 `expectedEpoch = resp.epoch`。  
5. 若 `ownerNode >= 0`，则 `requesterNode = ownerNode`；否则退化为 `_nodeId`。

### 5.6 home writeback notify 消息化

当前：

- `EPBackend::handleHomeWritebackComplete()` 直接调用 UBCC

新方案：

1. `EPSNF.recvDataMsg()` 在 home DRAM 写入完成后调用 `EPBackend.handleHomeWritebackComplete(homePa, ingressSocket)`  
2. `EPBackend` 解析 `homeSocket = PA`  
3. 通过 `_ubAdapters[homeSocket]` 发送 `HomeWritebackNotify` 到 `UBCC(homeNode, homeSocket)`  
4. UBCC 以 `epoch` 做 optimistic stale drop

### 5.7 inspection/test hook 调整

现有：

- `EPBackend.hh:580-585` 暴露 `getUBCC()/getUBAdapter()`

改为：

- `getUBAdapter(int socket)`
- 若确需测试查看 UBCC，则通过 router registry / topology 暴露 `getUbccForSocket(socket)`，但不允许协议主路径调用

---

## 6. UBCC Per-Socket + QueryLineMeta

### 6.1 现状基线

- `UBCCController` 当前构造为 per-node：`UBCCController.hh:202-205`  
- `_instances` registry 也是 `node_id -> UBCC*`：`UBCCController.hh:225-226`, `UBCCController.cc:45-59`  
- `isDsmAddr()` 的 `_dsmLocalBase/_dsmSegSize` 仍按单 socket 计算：`UBCCController.cc:86-97`

### 6.2 新实例粒度

`UBCCController` 改为 `UBCCController(node_id, socket_id, ...)`。

新增成员：

```cpp
int _socketId;
int _numSockets;
```

静态 registry 改为：

```cpp
static std::map<std::pair<int,int>, UBCCController*> _instances;
```

### 6.3 ResidentDir 语义

`ResidentDir` 本身位宽不变：

- `ResidentDir.hh:21-27` 的 `UBCCDirEntry` 不加 socket 字段
- `ResidentDir.cc:170-183` 的 56-bit 打包格式不变

原因：

- 目录已经由 “哪个 UBCC 实例持有” 隐含 socket 归属
- 每 socket 一个 `ResidentDir`，天然隔离
- 保持条目编码稳定，最小化状态迁移风险

### 6.4 `isDsmAddr()` 与本地 home 范围

当前单 socket：

- `_dsmLocalBase = nodeBase + 2*seg + node*seg`

新逻辑必须改成：

```cpp
_dsmLocalBase = nodeBase(node_id)
              + 2 * segSize
              + (node_id * numSockets + socket_id) * segSize;
_dsmSegSize   = segSize;
```

即每个 UBCC 只接受：

- “本 node 为 home”
- “本 socket 为 homeSocket”

### 6.5 `QueryLineMetaReq/Resp`

新增接口：

```cpp
bool queryLineMeta(uint64_t line_pa,
                   uint64_t &epoch,
                   int &ownerNode,
                   MESIState &state,
                   bool &found) const;
```

处理规则：

1. 先查 resident dir  
2. 若 resident miss 且 metadata offload/backstore 可用，则查 backstore  
3. 不创建 resident placeholder  
4. 不触发 fillPending / wbPending  
5. 只返回 committed 快照

这是写回 fallback 的只读查询面，不参与排序。

### 6.6 `processWriteback()` 语义保持，但入口变更

当前实现（`UBCCController.cc:1490-1572`）仍有效，需做两点调整：

1. 参数名语义统一为 `expectedEpoch`  
2. 只允许由 `dstSocket == _socketId` 的 router 本地投递进入

### 6.7 `HomeWritebackNotify` 处理

替代 `notifyHomeWritebackComplete()` 的 direct 调用。

推荐处理规则：

1. lookup `DirEntry`；不存在则忽略  
2. 若 `isLineBusy(homePa)`，忽略（或保守记录 deferred stat，但不排队）  
3. 若 `msg.h.epoch != 0 && checkEpochForLine(homePa, msg.h.epoch)==false`，忽略  
4. 否则执行与当前 `notifyHomeWritebackComplete()` 等价的 `G_* -> G_I` 释放

这样可与 optimistic epoch validation 保持一致。

### 6.8 sharer routing 不扩 socket 粒度

已确认：

- `sharersMask` 仍只表示 node
- recall/invalidate 从 home UBCC 的 socket plane 发出
- 因此 `getPendingInvalidationMask()`、`getUpgradePendingTargetMask()` 等接口都无需扩大 bit-width

---

## 7. MetaRNF Per-Socket

### 7.1 现状基线

- `MetaRNFController.hh:25` / `.cc:15-38` 的 singleton key 仅为 `node_id`
- `EPBackend.cc:237-239` 也是 `MetaRNFController::getInstance(_nodeId)`

### 7.2 目标

改为 `MetaRNF(node, socket)`：

```cpp
static MetaRNFController* getInstance(int node_id, int socket_id);
static std::map<std::pair<int,int>, MetaRNFController*> _instances;
```

### 7.3 metadata 地址空间

需要将 `metadata_private_range` 也做 per-socket 切分。推荐：

- 每 node 保留一个大的 metadata private 区间
- 按 `socket_id` 划分成 `num_sockets` 个子区间
- `metadataBackstorePa()` 的哈希槽仅在该 socket 子区间内分配

这样：

- `UBCC(node,0)` 与 `UBCC(node,1)` 的 metadata 不会 alias
- backstore 查询天然与 per-socket ResidentDir 对齐

### 7.4 EPBackend 绑定

`EPBackend` 改为：

- `_metaRnfs[socket]`
- `issueBackstoreRead/Write/Delete(homePa)` 先解析 `homeSocket`，再路由到对应 `MetaRNF`

### 7.5 单 socket 兼容

`num_sockets=1` 时：

- `_instances[(node,0)]`
- metadata range 不切分或切分后仍为整段

---

## 8. HN-F TBE Changes（requesterSocket）

### 8.1 现状基线

- `CHI-cache.sm:646-739` 的 `TBE` 无 socket 字段
- `CHI-msg.sm:138-143` 只有 `ubcc_needed_perm` / `ubcc_write_intent`

### 8.2 新字段

在 `CHI-cache.sm` 的 `TBE` 中新增：

```text
int requesterSocket, default="0";
```

用途：

- 仅记录发起 miss / writeback / upgrade 的本地 socket
- 作为 EP-SNF sideband 的来源
- 绝不进入 `DirEntry` / 持久目录

### 8.3 sideband 扩展

在 `CHI-msg.sm` 的 `CHIRequestMsg` 中新增：

```text
int ubcc_requester_socket, default="0";
```

语义：

- HN-F → EP-SNF 的 NUMA latency hint
- EP-SNF → EPBackend 原样转发为 `ingressSocket`

### 8.4 HN-F 填充点

在 `CHI-cache-funcs.sm`：

1. TBE 分配时，从 requestor machine / cluster topology 推导 `requesterSocket`  
2. `prepareRequest()`/`prepareRequestRetry()` 对发往 EP-SNF 的 `ReadNoSnp/WriteNoSnp` 写入 `ubcc_requester_socket`

### 8.5 不进入持久状态

已确认：

- `requesterSocket` 不写入 HN-F 目录
- 不写入 UBCC directory
- 只存在于 TBE 与消息 sideband

---

## 9. EPSNF NUMA Latency Integration

### 9.1 现状基线

- `EPSNFController.cc:181-182` 只读 `neededPerm/writeIntent`  
- `EPSNFController.hh:44-51` 的 `RetryEntry` 无 socket  
- `recvDataMsg()` (`EPSNFController.cc:429-519`) 对 writeback 路径无 socket hint

### 9.2 `recvRequestMsg()` 变更

新增读取：

```cpp
int ingressSocket = msg->m_ubcc_requester_socket;
```

并把它传给：

- `EPBackend::handleRemoteMiss(..., ingressSocket, ...)`

### 9.3 Retry / deferred queue 扩展

以下结构体都要持久化 `ingressSocket`：

- `RetryEntry`
- 任何 deferred grant / deferred writeback helper

否则 BUSY 后重试会丢失原始 socket hint。

### 9.4 `recvDataMsg()` 写回链路

当前 `recvDataMsg()`：

- 把 `NCBWrData` 写入 home DRAM 后，若 `_backend->isDsmAddr(writePa)`，调用 `_backend->handleWriteback(writePa, false)`

新方案：

1. `recvDataMsg()` 从原始 request/TBE 带来的 socket 上下文拿到 `ingressSocket`  
2. 调 `handleWriteback(writePa, false, ingressSocket)`  
3. DRAM 写入成功后，再调 `handleHomeWritebackComplete(writePa, ingressSocket)`  
4. 后者内部转为 `HomeWritebackNotify`

### 9.5 latency 使用点

`ingressSocket` 只影响：

- 选哪个本地 `UBAdapter(srcSocket)` 出发
- 本地 node 内 same-socket / cross-socket latency bucket（若配置存在）

它**不影响**：

- home directory 归属
- sharersMask
- committed owner

---

## 10. Configuration Parameters

### 10.1 新增参数

建议新增：

#### Python SimObject params

- `UBRouter.py`
  - `socket_id`
- `UBAdapter.py`
  - `socket_id`
- `MetaRNFController.py`
  - `socket_id`
- `EPBackend.py`
  - `num_sockets`
  - `ub_adapters = VectorParam.UBAdapter(...)`
  - `meta_rnfs = VectorParam.MetaRNFController(...)`

#### C++ helper params / config

- `NodeAddressMap(num_nodes, num_sockets, seg_size)`
- `NodeConfig(num_sockets=1)`

### 10.2 `CHI_ubcc_framework.py` 拓扑构造

当前：

- `UBRouter/UBAdapter/MetaRNF` 均为 per-node：`CHI_ubcc_framework.py:246-305`

新方案：

```text
for node in num_nodes:
  create EPBackend(node)                      # one per node
  for socket in num_sockets:
    create UBRouter(node,socket)
    create UBAdapter(node,socket)
    create UBCC(node,socket)
    create MetaRNF(node,socket)
  wire arrays into EPBackend
```

### 10.3 地址范围构造

`NodeConfig` 必须：

- 暴露 `dsm_range_for(homeNode, homeSocket, phy_base)`
- 暴露 `dsm_socket_global_range()` / `all_dsm_ranges()` 辅助函数
- 更新 `metadata_private_base`

### 10.4 环境变量 / 默认值

建议新增：

- `UBCC_NUM_SOCKETS=1`（默认）
- `UBCC_SOCKET_LOCAL_LATENCY` / `UBCC_SOCKET_REMOTE_LATENCY`（可选）

若不配置，继续使用现有 `ub_msg_latency` 默认路径。

---

## 11. Single-Socket Backward Compatibility

### 11.1 统一数组化

已确认采用方案 A：

- 所有 per-socket 组件统一数组化
- `num_sockets=1` 时数组大小为 1
- 所有 `socketId` 默认 0

### 11.2 兼容性规则

1. `NodeAddressMap` 新公式在 `num_sockets=1` 下退化为旧布局  
2. `UBMsg.srcSocket/dstSocket` 都为 0  
3. router/ubcc/meta registry key 统一为 `(node,0)`  
4. sideband `ubcc_requester_socket` 默认 0  
5. `tests/e2e/test_e2e.py` 默认不改 case 内容，只默认 `num_sockets=1`

### 11.3 不允许的兼容实现

不得：

- 维护单独的 single-socket fast path
- 在单 socket 模式下继续使用 direct `_ubcc`
- 通过 `if num_sockets == 1` 绕过 QueryLineMeta / HomeWritebackNotify 协议路径

---

## 12. File-by-File Implementation Checklist

下面按实施优先级给出逐文件清单。

### 12.1 地址 / 配置层

| 文件 | 当前基线 | 必要修改 |
|---|---|---|
| `gem5/configs/ruby/CHI_basic_framework_config.py` | `NodeAddressMap` 只支持 `homeNode`，`NodeConfig.metadata_private_base = phy_base + 5*SEG`（行 29-128） | 增加 `num_sockets`、`homeSocket()`、`buildDsmPA(..., homeSocket, ...)`；DSM range 扩展为 `N*S` 段；metadata 基址后移 |
| `gem5/src/mem/ruby/protocol/chi/ep/NodeAddressMap.hh` | `isDsm/homeNode/buildDsmPA` 仅 node 粒度（行 24-56） | 新增 `numSockets` 成员、`homeSocket()`、socket-aware `buildDsmPA()`；保留单 socket 退化 |
| `gem5/configs/ruby/CHI_ubcc_framework.py` | 当前 per-node 创建 `UBRouter/UBAdapter/MetaRNF`（行 246-305） | 改为 per-socket 创建；`EPBackend` 接数组；为每 socket 建 `UBCC/MetaRNF`；更新 addr_ranges |
| `gem5/src/mem/ruby/protocol/chi/ep/UBRouter.py` | 仅 `node_id` + `ub_msg_latency`（行 7-13） | 增加 `socket_id` |
| `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.py` | 仅 `node_id` + `router`（行 7-13） | 增加 `socket_id` |
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.py` | 单 `ub_adapter`、单 `meta_rnf`（行 12-22） | 改为 `num_sockets` + adapter/meta_rnf 数组 |
| `gem5/src/mem/ruby/protocol/chi/ep/MetaRNFController.py` | 无 `socket_id`（行 5-12） | 增加 `socket_id`；metadata range 切分 |

### 12.2 UB message / transport 层

| 文件 | 当前基线 | 必要修改 |
|---|---|---|
| `gem5/src/mem/ruby/protocol/chi/ep/UBMsg.hh` | header 无 socket；无 `QueryLineMeta*` / `HomeWritebackNotify`（行 17-73） | 增加 `srcSocket/dstSocket`；新增 3 个消息类型与 body；更新 debug string |
| `gem5/src/mem/ruby/protocol/chi/ep/UBRouter.hh` | registry key 为 node；queue key 为 `(src,dst)`（行 67-92） | 改为 `(node,socket)` registry；queue key 扩为四元组；新增 `socketId()` |
| `gem5/src/mem/ruby/protocol/chi/ep/UBRouter.cc` | `getRouter/registerRouter/sendMessage/drain` 均只按 node 路由（行 17-203） | 全面 socket-aware；本地投递检查 `dstSocket`；remote delivery 查 `(dstNode,dstSocket)`；新增新消息类型分发 |
| `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.hh` | 单 node 粒度接口；无 QueryLineMeta/HomeWritebackNotify API（行 36-160） | 增加 `socketId()`、`sendQueryLineMetaReq()`、`sendHomeWritebackNotify()`；所有 send/recv API 增 socket 语义 |
| `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc` | 所有消息只填 node 字段（行 61-669） | 所有消息填 `srcSocket/dstSocket`；新增 meta query/notify 构造与响应解析；`recvFromRouter()` 向 backend 传 `ingressSocket` |

### 12.3 EPBackend / UBCC 主协议层

| 文件 | 当前基线 | 必要修改 |
|---|---|---|
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh` | 仍有 `_ubcc` + 单 `_ubAdapter` + 单 `_metaRnf`（行 660-666） | 删除 direct `_ubcc` 主路径；改为 `_ubAdapters[]/_metaRnfs[]/_numSockets`；所有主入口增加 `ingressSocket` |
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc` | 构造时直接 new UBCC（行 99-131）；`handleRemoteMiss`/`handleWriteback`/`handleHomeWritebackComplete` 直接或间接依赖 `_ubcc`（行 381-739, 1243-1343） | 取消 direct `_ubcc` 访问；remote miss 按 `srcSocket=ingressSocket,dstSocket=homeSocket` 发送；writeback fallback 改为 `QueryLineMetaReq/Resp`；home writeback 改为 `HomeWritebackNotify` |
| `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh` | per-node ctor / registry；direct `notifyHomeWritebackComplete/getEpochForLine/getOwnerForLine` 接口（行 202-226, 347-386） | 增 `socket_id` / `(node,socket)` registry；新增 `queryLineMeta()`；保留 `processWriteback/processEvict`；把 home writeback 改为消息处理入口 |
| `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc` | `_instances`、DSM local base、writeback/notify 逻辑均单 socket（行 45-98, 1461-1610） | 按 socket 更新 registry 与 `isDsmAddr()`；实现 `QueryLineMetaResp`；实现 `HomeWritebackNotify` 的 stale-safe 应用 |
| `gem5/src/mem/ruby/protocol/chi/ep/ResidentDir.hh` | 条目编码不含 socket（行 21-27, 48-50） | **不改格式**；仅更新注释说明“per-socket resident dir instance” |
| `gem5/src/mem/ruby/protocol/chi/ep/ResidentDir.cc` | 56-bit packed entry 稳定（行 170-195） | **不改位宽**；仅必要断言/注释 |
| `gem5/src/mem/ruby/protocol/chi/ep/MetaRNFController.hh/.cc` | singleton key 仅 node（行 25-26；`.cc` 15-38） | 改为 `(node,socket)` key；metadata range per-socket；实例注册与查询 socket-aware |

### 12.4 CHI sideband / HN-F / EP-SNF 层

| 文件 | 当前基线 | 必要修改 |
|---|---|---|
| `gem5/src/mem/ruby/protocol/chi/CHI-msg.sm` | 只有 `ubcc_needed_perm` / `ubcc_write_intent`（行 138-143） | 新增 `ubcc_requester_socket` sideband，默认 0 |
| `gem5/src/mem/ruby/protocol/chi/CHI-cache.sm` | `TBE` 无 socket 字段（行 646-739） | 增 `requesterSocket`，默认 0 |
| `gem5/src/mem/ruby/protocol/chi/CHI-cache-funcs.sm` | `prepareRequest()`/`prepareRequestRetry()` 只设置现有 sideband（行 668-769） | 新增 `out_msg.ubcc_requester_socket := tbe.requesterSocket`；在 TBE 初始化时推导 requesterSocket |
| `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.hh` | `RetryEntry` 无 socket（行 44-51） | 新增 `ingressSocket` 字段 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc` | `recvRequestMsg()`/`recvDataMsg()` 不读 socket sideband（行 181-203, 429-519） | 读取 `ubcc_requester_socket`；传给 backend；retry 队列保留该字段；writeback/home-writeback 链路使用该字段 |

### 12.5 测试 / harness 层

| 文件 | 当前基线 | 必要修改 |
|---|---|---|
| `tests/e2e/test_e2e.py` | 当前用例注册到 TC28（行 20-50） | 默认 `num_sockets=1` 回归不变；新增 dual-socket 配置入口与 smoke/regression 子集；现有用例不改判定语义 |

---

## 13. Message Flow Diagrams for Key Operations

### 13.1 Remote Read Miss

```text
CPU(socket=r)
  → HN-F: allocate TBE.requesterSocket=r
  → EP_SNF: ReadNoSnp + {neededPerm, writeIntent, requesterSocket=r}
  → EPBackend.handleRemoteMiss(..., ingressSocket=r)
  → decode PA → {homeNode, homeSocket}
  → UBAdapter(reqNode, r): ReadReq{srcSocket=r, dstSocket=homeSocket}
  → UBRouter(req,r)
  → UBRouter(home,homeSocket)
  → UBCC(home,homeSocket)
  → ReadResp back to UBAdapter(req,r)
  → EPBackend populateGrantData + sendClear
  → EP_SNF returns CompData to HN-F
```

关键点：

- `homeSocket` 只由 PA 决定
- `ingressSocket` 只由 sideband 决定
- Clear 提交仍落在 `homeSocket` 对应 UBCC

### 13.2 Remote Read Miss with Recall

```text
Requester EPBackend(req, ingress=r)
  → ReadReq(dstSocket=homeSocket)
  → UBCC(home,homeSocket): decide recallNeeded, recallOwnerNode
  → UBAdapter(homeSocket plane) sends RecallReq to dstNode=owner, dstSocket=homeSocket
  → UBRouter(owner,homeSocket)
  → UBAdapter(owner,homeSocket)
  → EPBackend(owner) → EP-RNF(owner) → HN-F(owner) → local L2 snoop/CHI req
  → RecallResp(srcSocket=homeSocket, dstSocket=homeSocket)
  → UBCC(home,homeSocket)
  → grant / Clear commit
```

关键点：

- sharer routing 固定走 `homeSocket` plane
- owner 本地实际 CPU 位于哪个 socket 不进入 sharersMask；仅影响 node 内 NUMA latency 模型

### 13.3 Writeback with Requester Metadata Hit

```text
HN-F/EP_SNF(req socket=r)
  → EPBackend.handleWriteback(line_pa, keepAsClean, ingressSocket=r)
  → _requesterLines[line_pa] hit → obtain epoch
  → decode homeSocket from PA
  → UBAdapter(req,r): WritebackReq{srcSocket=r, dstSocket=homeSocket, epoch=expectedEpoch}
  → UBCC(home,homeSocket): processWriteback(expectedEpoch)
```

### 13.4 Writeback with Requester Metadata Miss

```text
EPBackend.handleWriteback(...)
  → _requesterLines miss
  → QueryLineMetaReq{srcSocket=r, dstSocket=homeSocket}
  → UBCC(home,homeSocket) returns {epoch, ownerNode}
  → WritebackReq(expectedEpoch=resp.epoch, requesterNode=resp.ownerNode or self)
  → UBCC optimistic epoch validation
```

### 13.5 Home Writeback Notify

```text
HN-F(home node) writes data to DRAM
  → EP_SNF.recvDataMsg()
  → EPBackend.handleHomeWritebackComplete(homePa, ingressSocket)
  → decode homeSocket from PA
  → UBAdapter(homeSocket): HomeWritebackNotify{epoch=expectedEpoch}
  → UBCC(home,homeSocket): if epoch still current, release dir entry
```

### 13.6 Local Upgrade

```text
Local L2 write upgrade on requester socket=r
  → HN-F snoops EP-RNF
  → EPBackend.notifyLocalWriteUpgrade(line_pa, homeNode, ingressSocket=r)
  → decode homeSocket from PA
  → UBAdapter(req,r): UpgradeReq{dstSocket=homeSocket}
  → UBCC(home,homeSocket): freeze targetMask, maybe fanout invalidations on same socket plane
  → all InvalidateAck return to UBCC(home,homeSocket)
  → UpgradeAckNotify back to requester socket plane
  → EP-RNF replies HN-F
  → UpgradeDone(dstSocket=homeSocket)
```

---

## 14. Impact on Existing Test Suite

### 14.1 单 socket 回归要求

必须保证 `num_sockets=1` 时，现有 `TC1-TC28` 语义不变。

重点回归路径：

- **TC2/TC3/TC4**：remote read 基础路由
- **TC5/TC6**：多 requester / Clear replay / pending requester
- **TC7**：writeback / evict / epoch / owner 校验
- **TC8/TC11/TC16**：upgrade + invalidate + delayed ack
- **TC18-TC24**：ResidentDir / offload / capacity / bloom / stress
- **TC28**：metadata backstore consistency

### 14.2 双 socket 新增覆盖建议

建议至少新增以下 smoke/regression：

1. **DS-TC1：跨 socket remote read**  
   - requester socket1 访问 `(homeNode=0, homeSocket=0)`
   - 验证 `srcSocket=1,dstSocket=0`

2. **DS-TC2：跨 socket writeback fallback**  
   - 清空 requester bookkeeping，强制走 `QueryLineMetaReq/Resp`

3. **DS-TC3：home writeback notify stale drop**  
   - 写回后在 notify 到达前插入新 epoch，验证 notify 被丢弃

4. **DS-TC4：dual-socket local upgrade**  
   - requesterSocket!=homeSocket，验证 Ack/Done 仍正确绑定 `homeSocket`

5. **DS-TC5：single-socket compat**  
   - `num_sockets=1` 下复跑 TC2/TC7/TC8/TC18/TC28

### 14.3 harness 修改原则

`tests/e2e/test_e2e.py` 只做两类扩展：

- 增加 dual-socket 运行参数 / 环境变量
- 增加新的 dual-socket case registry

不修改现有 TC 的 verdict 逻辑。

---

## 15. Recommended Build / Landing Order

### Layer A：地址与配置骨架

1. `CHI_basic_framework_config.py`
2. `NodeAddressMap.hh`
3. `UBRouter.py / UBAdapter.py / EPBackend.py / MetaRNFController.py`
4. `CHI_ubcc_framework.py`

### Layer B：UB transport socket 化

1. `UBMsg.hh`
2. `UBRouter.hh/.cc`
3. `UBAdapter.hh/.cc`

### Layer C：EPBackend / UBCC detached 化

1. `UBCCController.hh/.cc`
2. `EPBackend.hh/.cc`
3. `MetaRNFController.hh/.cc`

### Layer D：CHI sideband / EP-SNF / HN-F

1. `CHI-msg.sm`
2. `CHI-cache.sm`
3. `CHI-cache-funcs.sm`
4. `EPSNFController.hh/.cc`

### Layer E：验证

1. `num_sockets=1` 全量 E2E
2. dual-socket smoke
3. dual-socket race / stale notify / fallback query

---

## 16. Final Normative Decisions Summary

1. **PA 直接编码 `(homeNode, homeSocket)`；每 node 视图有 `N*2` DSM 子段。**  
2. **UBRouter / UBAdapter / UBCC / MetaRNF 全部改为 per-(node,socket)。**  
3. **EPBackend 保持 per-node，但不再持有 direct `_ubcc` 主路径。**  
4. **所有 EPBackend→UBCC 交互统一消息化，经 `UBMsg/UBRouter`。**  
5. **写回前 metadata fallback 统一走 `QueryLineMetaReq/Resp`。**  
6. **HN-F→DRAM 写回完成后的目录释放统一走 `HomeWritebackNotify`。**  
7. **所有 UBMsg 都携带 `srcSocket/dstSocket`。**  
8. **sharer routing 固定为“home UBCC 所在 socket plane”。**  
9. **`homeSocket` 来自 PA；`ingressSocket` 来自 sideband，仅用于源 socket/NUMA hint。**  
10. **写回采用 optimistic epoch validation，仅校验 `expectedEpoch`。**  
11. **`requesterSocket` 只进入 HN-F TBE 与 EP-SNF sideband，不进入持久目录。**  
12. **`num_sockets=1` 走完全相同代码路径，`socketId=0`。**
