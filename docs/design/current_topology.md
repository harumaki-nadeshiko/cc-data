# Current System Topology (dual-socket v2, num_sockets=1 for backward compat)

## Single Node Detail

```
╔══════════════════════════════════════════════════════════════════════╗
║                          Node i                                      ║
║                                                                      ║
║  ┌───────────────────┐          ┌───────────────────┐               ║
║  │   Socket 0        │          │   Socket 1        │               ║
║  │  (num_sockets=1   │          │  (disabled for    │               ║
║  │   covers all DSM) │          │   num_sockets=1)  │               ║
║  │                   │          │                   │               ║
║  │ CPU[0..N/2-1]     │          │ CPU[N/2..N-1]     │               ║
║  │  │ L1D/L2         │          │  │ L1D/L2         │               ║
║  │  └────────┬───────┘          │  └────────┬───────┘               ║
║  └───────────┼──────────────────┴───────────┼───────────────────────║
║              │          addr_range routes     │                       ║
║              │    DSM(*,0) ──→ HN-F(0)       │ DSM(*,1) ──→ HN-F(1) ║
║              │    local(0) ──→ HN-F(0)       │ local(1) ──→ HN-F(1) ║
║              │    meta(0)  ──→ HN-F(0)       │ meta(1)  ──→ HN-F(1) ║
║              │                               │                       ║
║     ┌────────┴───────┐               ┌───────┴─────────┐            ║
║     │  HN-F(i,0)     │               │  HN-F(i,1)      │            ║
║     │  L3 cache      │               │  L3 cache       │            ║
║     │  dir_sharers:  │               │  dir_sharers:   │            ║
║     │   {EP-RNF(i)}  │               │   {EP-RNF(i)}   │            ║
║     └────────┬───────┘               └───────┬─────────┘            ║
║              │                               │                       ║
║     ┌────────┴───────┐               ┌───────┴─────────┐            ║
║     │ EP-SNF(i,0)    │               │ EP-SNF(i,1)     │            ║
║     │ DSM(*,0) load  │               │ DSM(*,1) load   │            ║
║     │ /writeback     │               │ /writeback      │            ║
║     └────────┬───────┘               └───────┬─────────┘            ║
║              └───────────┬───────────────────┘                      ║
║                          │                                          ║
║                   ┌──────┴──────┐                                    ║
║                   │ EPBackend(i)│  (single per-node)                ║
║                   │ _epSnfs[2]  │                                    ║
║                   │ _ubAdapters │                                    ║
║                   └──────┬──────┘                                    ║
║                          │                                           ║
║          ┌───────────────┼───────────────┐                           ║
║          │               │               │                           ║
║   ┌──────┴──────┐ ┌──────┴──────┐ ┌──────┴──────┐                   ║
║   │EP-RNF(i)    │ │UBAdapter   │ │UBAdapter   │                     ║
║   │(single)     │ │  (i,0)     │ │  (i,1)     │                     ║
║   │_hnfVers[2]  │ └──────┬─────┘ └──────┬─────┘                     ║
║   │downstream→  │        │              │                           ║
║   │ HN-F(0/1)   │  ┌─────┴──────┐ ┌─────┴──────┐                    ║
║   └─────────────┘  │UBRouter   │ │UBRouter   │                      ║
║                    │  (i,0)    │ │  (i,1)    │                      ║
║                    └─────┬─────┘ └─────┬─────┘                      ║
║                          │             │                            ║
║                    ┌─────┴──────┐ ┌────┴──────┐                     ║
║                    │ UBCC(i,0)  │ │ UBCC(i,1) │                     ║
║                    │ResidentDir │ │ResidentDir│                     ║
║                    └─────┬──────┘ └────┬──────┘                     ║
║                          │             │                            ║
║                    ┌─────┴──────┐ ┌────┴──────┐                     ║
║                    │MetaRNF(i,0)│ │MetaRNF(i,1)│                    ║
║                    │socket DRAM │ │socket DRAM │                     ║
║                    └────────────┘ └────────────┘                     ║
╚══════════════════════════════════════════════════════════════════════╝
```

## Cross-Node Topology

```
           Node 0                              Node 1
  ╔════════════════════╗              ╔════════════════════╗
  ║                    ║              ║                    ║
  ║  UBRouter(0,0) ───lat_inter_node──→ UBRouter(1,0)    ║
  ║       │                            │                  ║
  ║       │ lat_numa                   │ lat_numa         ║
  ║       │ (intra-node)               │ (intra-node)     ║
  ║       │                            │                  ║
  ║  UBRouter(0,1) ───lat_inter_node──→ UBRouter(1,1)    ║
  ║       │                            │                  ║
  ║  UBCC(0,k) ←── directory for DSM(*,k) on Node 0      ║
  ║  UBCC(1,k) ←── directory for DSM(*,k) on Node 1      ║
  ╚════════════════════╝              ╚════════════════════╝

  UBRouter connectivity rule:
    (i,k) ↔ (j,k)  when i≠j  (same socket plane, cross-node)
    (i,0) ↔ (i,1)            (same node, cross-socket)
    No other direct connections
```

## Read Miss Request Chain

```
CPU(socket=r) ──ldr [DSM_VA]──→ L1D → L2
  │ PA.homeSocket determines target HN-F plane
  │
  ├── DSM(*,socket=r): lat_local ──→ HN-F(node_i, socket=r)
  └── DSM(*,socket≠r): lat_numa  ──→ HN-F(node_i, socket=¬r)

HN-F(node_i, homeSocket=k):
  │ L3 miss → delegating to downstream SN-F
  ▼
EP-SNF(node_i, socket=k)          [handles DSM(*,k) only]
  │ recvRequestMsg(ReadNoSnp)
  │ → handleRemoteMiss(linePa, ingressSocket=r, → homeNode, homeSocket)
  ▼
EPBackend(node_i)                 [single per-node, shared]
  │ PA → homeSocket=k
  │ pick UBAdapter(srcSocket=r)
  │ send ReadReq(homePA, dstSocket=k, ingressSocket=r)
  ▼
UBAdapter(node_i, srcSocket=r) → UBRouter(node_i, srcSocket=r)
  │
  │ Latency chain (cross-node, cross-socket):
  │   UBRouter(i,r) → UBRouter(j,r) [lat_inter_node]
  │     → UBRouter(j,r) → UBRouter(j,k) [lat_numa]    (if r≠k)
  │     → UBRouter(j,k) → UBCC(j,k)   [local delivery]
  ▼
UBCC(homeNode=j, socket=k):
  │ processOuterRequest → G_I/G_S/G_E/G_M → grant + pendingInv
  │ response: ReadResp(grantType, dataSource, ...)
  ▼
[Reverse path back to requester, same socket plane routing]

Key:
  homeSocket = k (from PA encoding)
  homeNode   = j (from PA encoding)
  ingressSocket = r (from CPU socket, carried in sideband)
  srcSocket = ingressSocket (request leaves via ingress plane)
  dstSocket = homeSocket (request destined for home directory plane)
```

## INVALIDATE / RECALL Fanout Chain

```
UBCC(homeNode=H, socket=homeSocket):
  │ sharersMask indicates Node i has sharer
  │ fanout rule: send to UBRouter(i, homeSocket)  [same socket plane]
  ▼
UBRouter(H, homeSocket) → UBRouter(i, homeSocket)
  │
  ├── INVALIDATE: local delivery to UBAdapter(i, homeSocket)
  │                  → EPBackend(i)
  │                    → handleInvalidationRequest(invMsg, ingressSocket=homeSocket)
  │                      → EP-RNF(i) → startCleanUnique(PA)
  │                        → decodeHomeSocket(PA) → select HN-F(i, k)
  │                          → Send CleanUnique(proxyOp=InvalidateOnly) → HN-F(i,k)
  │                            → HN-F invalidates local L1/L2
  │
  └── RECALL: local delivery to UBAdapter(i, homeSocket)
                → EPBackend(i)
                  → handleRecallRequest(recallMsg, ingressSocket=homeSocket)
                    → EP-RNF(i) → startReadShared(PA)/startReadUnique(PA)
                      → decodeHomeSocket(PA) → select HN-F(i, k)
                        → Send ReadShared/ReadUnique → HN-F(i,k)
                          → HN-F queries local cache hierarchy
```

## Writeback Chain

```
CPU(socket=r) evicts dirty line (home=Node_j, homeSocket=k):
  │
  ▼
HN-F(node_i, socket=r)            [L1/L2 only write back to LOCAL HN-F]
  │ WriteBackFull + CBWrData
  │ alloc_on_writeback=True → L3 caches dirty data
  │
  │ L3 eviction (MaintainCoherence):
  │   → SendWriteNoSnp
  ▼
EP-SNF(node_i, socket=r)          [handles ALL DSM(*,*) loads/writebacks]
  │ recvDataMsg → functionalAccess → write to HOME DDR4
  │   (writePa = home PA on Node_j, translated by NodeAddressMap)
  │ → handleHomeWritebackComplete(homePa)
  ▼
EPBackend(node_i)
  │ PA → homeSocket=k
  │ send HomeWritebackNotify(srcSocket=r, dstSocket=k)
  ▼
UBAdapter(i,r) → UBRouter(i,r) → UBRouter(j,k) → UBCC(j,k):
  │ processHomeWritebackNotify → state→G_I, release ownership
```

## Latency Table

| Hop | Latency (num_sockets=1) | Latency (num_sockets=2) |
|-----|--------------------------|--------------------------|
| CPU RNF → HN-F (same socket) | 0 (single socket) | lat_local |
| CPU RNF → HN-F (cross socket) | N/A | lat_numa |
| HN-F → EP-SNF | 0 (1:1 pair) | 0 (1:1 pair) |
| EP-SNF → EPBackend | 0 (direct call) | 0 (direct call) |
| UBAdapter → UBRouter (same node) | 0 | 0 |
| UBRouter(i,k) → UBRouter(j,k) (i≠j) | lat_inter_node (e.g. 500ns) | lat_inter_node |
| UBRouter(i,0) → UBRouter(i,1) | N/A | lat_numa |
| UBRouter → UBCC (local) | 0 | 0 |
