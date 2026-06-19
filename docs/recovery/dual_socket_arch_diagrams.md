# Dual-Socket Architecture & Flow Diagrams

## 1. 节点内架构

```
Node i ─────────────────────────────────────────────────────────────┐
                                                                    │
  Socket 0                              Socket 1                    │
  ┌─────────────┐                        ┌─────────────┐           │
  │ CPU cluster │  (half of node CPUs)   │ CPU cluster │           │
  │ L1D/L2 × N  │                        │ L1D/L2 × N  │           │
  └──────┬──────┘                        └──────┬──────┘           │
         │                                      │                   │
         └──────────────┬───────────────────────┘                   │
                        │                                           │
                   ┌────┴────┐                                      │
                   │  HN-F   │  (single per node, L3 cache)         │
                   │  TBE.   │  requesterSocket in TBE sideband     │
                   │ requester│                                      │
                   │ Socket   │                                      │
                   └────┬────┘                                      │
                        │                                           │
                   ┌────┴────┐                                      │
                   │ EP-SNF  │  (single per node)                   │
                   │  NUMA   │  latency via ingressSocket hint      │
                   │  aware  │  delegates to EPBackend              │
                   └────┬────┘                                      │
                        │                                           │
                   ┌────┴────┐                                      │
                   │EPSNFBack│  (single per node)                   │
                   │ end(i)  │  shared by both sockets              │
                   └───┬─┬───┘                                      │
        ┌──────────────┘ └──────────────┐                            │
        │                               │                            │
  ┌─────┴──────┐                  ┌─────┴──────┐                     │
  │UBAdapter   │                  │UBAdapter   │                     │
  │  (i,0)     │                  │  (i,1)     │                     │
  └─────┬──────┘                  └─────┬──────┘                     │
        │                               │                            │
  ┌─────┴──────┐                  ┌─────┴──────┐                     │
  │UBRouter    │                  │UBRouter    │                     │
  │  (i,0)     │←─lat_numa─→     │  (i,1)     │ (intra-node cross-socket)
  │_pairQueues │                  │_pairQueues │                     │
  └─────┬──────┘                  └─────┬──────┘                     │
        │                               │                            │
  ┌─────┴──────┐                  ┌─────┴──────┐                     │
  │ UBCC       │                  │ UBCC       │                     │
  │  (i,0)     │                  │  (i,1)     │                     │
  │ResidentDir │                  │ResidentDir │                     │
  │(512KB SRAM)│                  │(512KB SRAM)│                     │
  └─────┬──────┘                  └─────┬──────┘                     │
        │                               │                            │
  ┌─────┴──────┐                  ┌─────┴──────┐                     │
  │ MetaRNF    │                  │ MetaRNF    │                     │
  │  (i,0)     │                  │  (i,1)     │                     │
  │ socket 0   │                  │ socket 1   │                     │
  │ local DRAM │                  │ local DRAM │                     │
  └────────────┘                  └────────────┘                     │
                                                                    │
  ┌────────────┐                                                    │
  │ EP-RNF (i) │  (single per node, proxy controller)               │
  └────────────┘                                                    │
                                                                    │
  ┌────────────┐                                                    │
  │ DL-SNF (i) │  (single per node, local private DRAM)             │
  └────────────┘                                                    │
                                                                    │
  ┌────────────┐                                                    │
  │ L-SNF (i)  │  (single per node, UBCC exclusive + metadata)      │
  └────────────┘                                                    │
                                                                    │
  ┌────────────┐                                                    │
  │EPBackend   │  (single per node)                                 │
  │  (i)       │                                                    │
  │_ubAdapters │  = [UBAdapter(i,0), UBAdapter(i,1)]               │
  │_metaRnfs   │  = [MetaRNF(i,0),  MetaRNF(i,1)]                  │
  └────────────┘                                                    │
```

## 2. 跨节点拓扑

```
UBRouter 互联规则: 仅当 (node_id 相同 && socket_id 不同) 或 (socket_id 相同 && node_id 不同)

Node 0                          Node 1                          Node 2
                                   
─── Socket plane 0 ───────────────────────────────────────────────
│ UBRouter  │──lat_inter_node──│ UBRouter  │──lat_inter_node──│ UBRouter  │
│  (0,0)    │                  │  (1,0)    │                  │  (2,0)    │
└───────────┘                  └───────────┘                  └───────────┘
      │ lat_numa                      │ lat_numa                      │
─── Socket plane 1 ───────────────────────────────────────────────
│ UBRouter  │──lat_inter_node──│ UBRouter  │──lat_inter_node──│ UBRouter  │
│  (0,1)    │                  │  (1,1)    │                  │  (2,1)    │
└───────────┘                  └───────────┘                  └───────────┘

Latency matrix:
  UBRouter(i,k) → UBRouter(j,k), i≠j:   lat_inter_node   (同 plane 跨节点)
  UBRouter(i,0) ↔ UBRouter(i,1):         lat_numa         (同节点跨 socket)
  其他组合: 不存在直连，需通过中间节点中转（如 UBRouter(0,0)→UBRouter(1,0)→UBRouter(1,1)）

## 3. Read Miss 流程（跨节点，跨 socket）

```
场景: Node0 Socket0 CPU 读 DSM_1_socket1（home=Node1, homeSocket=1）

   CPU(r,s=0)
      │ ldr [DSM_VA]
      ▼
   L1D → L2 → HN-F(0)  [miss, TBE.requesterSocket = 0]
      │
      │ ReadNoSnp, sideband: ingressSocket=0
      ▼
   EP-SNF(0)  
      │
      │ handleRemoteMiss(linePa, ingressSocket=0, → homeNode=1, homeSocket=1)
      ▼
   EPBackend(0)
      │ PA → homeSocket=1, ingressSocket=0
      │ pick UBAdapter(0,0)  [srcSocket = ingressSocket]
      │ send ReadReq(homePA, homeSocket=1, ingressSocket=0)
      ▼
   UBAdapter(0,0) → UBRouter(0,0)  [enqueue (src=0,s=0 → dst=1,s=1)]
      │
      │ 路径: UBRouter(0,0) → UBRouter(1,0) [lat_inter_node]
      │                          → UBRouter(1,1) [lat_numa, intra-Node1]
      │ 注意: (0,0)→(1,1) 无直连! 必须经 (1,0)→(1,1) 中转
      ▼
   UBRouter(1,1)  [local delivery: dstNode==_nodeId && dstSocket==_socketId]
      ▼
   UBCC(1,1)  
      │ processOuterRequest → residentResult, grant, pendingInv, etc.
      │ response: ReadResp (grantType, dataSource, ...)
      ▼
   UBRouter(1,1) → UBRouter(0,0)  [response, reverse path]
      │
      │ lat_inter_node
      ▼
   UBRouter(0,0) → UBAdapter(0,0) → EPBackend(0)
      │ handleGrant, sendClear (via UBAdapter(0,0) → UBCC(1,1))
      ▼
   CompData back to HN-F(0) → L2 → L1D → CPU


关键点:
- ingressSocket=0 从 TBE 侧带到消息头
- homeSocket=1 从 PA 解码
- 请求走 srcSocket=0 平面，响应原路返回
- Clear 也经同 socket 平面发送
```

## 4. INVALIDATE Fanout 流程

```
场景: UBCC(1,0) 需要对 Node0 的 sharer 发送 Invalidate

   UBCC(1,0)  [homeSocket=0, sharersMask 含 Node0]
      │
      │ fanout 规则: 发往 dstNode 的 UBRouter(dstNode, homeSocket)
      │              → UBRouter(0, 0)
      │ InvalidateReq(src=1,s=0, dst=0,s=0)
      ▼
   UBRouter(1,0) → UBRouter(0,0)  [lat_inter_node]
      │
      │ local delivery (dstNode=0, dstSocket=0)
      ▼
   UBAdapter(0,0) → EPBackend(0)
      │ handleInvalidationRequest, ingressSocket=0
      │ → EP-RNF(0) → startCleanUnique → HN-F(0) → L2 invalidate
      ▼
   InvalidateAck 原路返回: UBAdapter(0,0)→UBRouter(0,0)→UBRouter(1,0)→UBCC(1,0)


关键点:
- homeSocket 决定 fanout 使用的 Router 平面 (socket 0)
- 目标 Node0 的 EPBackend 是共享的，接收 socket 0 入口
- InvalidateAck 不换平面，原路返回
```

## 5. Writeback + HomeWritebackNotify 流程

```
场景: Node0 Socket1 的 CPU 持有 Dirty 行 (home=Node1, homeSocket=0)
      本地 L1/L2 被逐出 → 先刷到本地 HN-F L3 → 再由 EP-SNF 刷回 Home DRAM + 通知 UBCC

   ┌─ Node 0 ──────────────────────────────────────────┐
   │                                                    │
   │   CPU(0,s=1) 持有的 Dirty 行被 capacity evict       │
   │     │                                              │
   │     │ WriteBackFull + CBWrData                      │
   │     ▼                                              │
   │   HN-F(0)  [本地 L3, L1/L2 只能刷到本地 HN-F]       │
   │     │ TBE.requesterSocket = 1 (来自 CPU socket)     │
   │     │ alloc_on_writeback=True → L3 缓存 dirty data  │
   │     │                                              │
   │     │ MaintainCoherence (L3 逐出该行时)              │
   │     │   → SendWriteNoSnp                            │
   │     ▼                                              │
   │   EP-SNF(0)                                        │
   │     │ recvDataMsg: 写数据到 Home DDR4 (Node1 的 DRAM) │
   │     │ 触发 handleHomeWritebackComplete               │
   │     ▼                                              │
   │   EPBackend(0)                                     │
   │     │ PA → homeNode=1, homeSocket=0                  │
   │     │ 发送 HomeWritebackNotify                       │
   │     │ (src=0, srcSocket=1, dst=1, dstSocket=0)      │
   │     ▼                                              │
   │   UBAdapter(0,1) → UBRouter(0,1)                    │
   └────┼───────────────────────────────────────────────┘
        │
        │ lat_inter_node [UBRouter(0,1)→UBRouter(1,1)→UBRouter(1,0) via intra-Node1 lat_numa]
        │ 或者: [UBRouter(0,1)→UBRouter(0,0) via lat_numa] → [UBRouter(0,0)→UBRouter(1,0) via lat_inter_node]
        │
   ┌────┼───────────────────────────────────────────────┐
   │ Node 1 (home)                                      │
   │    ▼                                               │
   │   UBRouter(1,0) → UBCC(1,0)                       │
   │     │ notifyHomeWritebackComplete                   │
   │     │   → validOwner: 检查 epoch                    │
   │     │   → state = G_I, release ownership           │
   └────────────────────────────────────────────────────┘

   EP-SNF(0) 同时将数据写入 Node1 的 DDR4（physMem functionalAccess, writePa = home PA）
```

**关键纠正**: 
- L1/L2 cache 的 WriteBackFull 只能刷到**本地 HN-F**(0) 的 L3，不能跨节点刷到 HN-F(1)
- 本地 HN-F L3 缓存 dirty 数据后，逐出时通过 **本地 EP-SNF** 写到 Home DDR4
- 目录更新通过 **EPBackend→UBRouter→HomeUBCC** 消息路径完成
```

## 6. QueryLineMeta → Writeback 流程（Detached）

```
场景: EPBackend 需要写回，但 _requesterLines 中无此条的 epoch

   EPBackend(node)
      │
      │ 1. 发送 QueryLineMetaReq(homePA, dstSocket=homeSocket)
      ▼
   UBAdapter(node, srcSocket) → UBRouter → UBCC(homeNode, homeSocket)
      │
      │ 2. UBCC 返回 QueryLineMetaResp{epoch, ownerNode}
      ▼
   EPBackend(node)
      │
      │ 3. 构造 WritebackReq(homePA, expectedEpoch=resp.epoch, requesterNode=ownerNode)
      │    srcSocket 取自 ingressSocket
      │    dstSocket = homeSocket
      ▼
   UBCC(homeNode, homeSocket)
      │
      │ 4. 校验 expectedEpoch == directory.epoch
      │    ├─ 匹配 → 执行 writeback, state→G_I, 返回 WritebackResp{accepted=true}
      │    └─ 不匹配 → WritebackResp{accepted=false}, EPBackend 可重试
```

## 7. UBMsg Header 变更

```
结构体 UBMsgHeader (现状 → 新):
  uint32_t type;           // 不变
  uint32_t srcNode;        // 不变
  uint32_t srcSocket;      // NEW — 发送方 socket
  uint32_t dstNode;        // 不变
  uint32_t dstSocket;      // NEW — 目标方 socket (homeSocket 或 fanout 平面)
  uint32_t homeNode;       // 不变
  uint32_t homeSocket;     // NEW — home 目录 socket (仅 ReadReq/ClearReq 等)
  uint32_t ingressSocket;  // NEW — 请求入口 socket (NUMA hint)
  uint32_t requesterNode;  // 不变
  uint32_t targetNode;     // 不变
  uint32_t flags;          // 不变，可扩展 socket 相关 flag
  uint64_t homeLinePa;     // 不变
  uint64_t localLinePa;    // 不变
  uint64_t epoch;          // 不变
  uint64_t reqId;          // 不变
  uint64_t seqNum;         // 不变
  uint64_t enqueueTick;    // 不变
  uint64_t readyTick;      // 不变

新增 UBMsgType:
  QueryLineMetaReq  (EPBackend → UBCC: 请求 {epoch, ownerNode})
  QueryLineMetaResp (UBCC → EPBackend: 返回 {found, epoch, ownerNode})
  HomeWritebackNotify (EPBackend → UBCC: HN-F 已完成 DDR4 写回)

队列键值:
  _pairQueues: key = (srcNode, srcSocket, dstNode, dstSocket)
```
