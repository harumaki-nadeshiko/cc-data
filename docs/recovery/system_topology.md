# CC-EP System Topology

## Per-Node Architecture

```
                        ┌──────────────────────────────────────────────┐
                        │            Node i (i ∈ {0,1,2})              │
                        │                                              │
  ┌──────┐  ┌──────┐   │  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐    │
  │CPU 0 │  │CPU 1 │   │  │CPU 2 │  │CPU 3 │  │      │  │      │    │
  │L1I/L1D│ │L1I/L1D│  │  │L1I/L1D│ │L1I/L1D│ │  ... │  │  ... │    │
  └──┬───┘  └──┬───┘   │  └──┬───┘  └──┬───┘  └──┬───┘  └──┬───┘    │
     │         │       │     │         │         │         │         │
     └────┬────┘       │     └────┬────┘         │         │         │
          │            │          │              │         │         │
     ┌────▼────┐       │     ┌────▼────┐         │         │         │
     │  L2 $   │       │     │  L2 $   │         │  (same for CPUs 4-11)
     └────┬────┘       │     └────┬────┘         │         │         │
          │            │          │              │         │         │
          └─────┬──────┘          └──────┬───────┘         │         │
                │                        │                 │         │
          ┌─────▼────────────────────────▼─────────────────▼─────┐   │
          │                   HN-F (L3 Cache)                    │   │
          │         addr_ranges: [LocalPrivate, DSM_0/1/2]       │   │
          └──┬──────────────────┬──────────────────┬────────────┘   │
             │                  │                  │                 │
        ┌────▼────┐       ┌─────▼─────┐      ┌────▼─────┐           │
        │ L_SNF   │       │ EP_SNF    │      │ EP_RNF   │           │
        │ (DDR4)  │       │ (DSM proxy)│     │ (CHI proxy)│         │
        └─────────┘       └─────┬─────┘      └────┬─────┘           │
                                │                  │                 │
                                │           ┌──────┘                 │
                                │           │                        │
                          ┌─────▼───────────▼─────┐                 │
                          │      EPBackend        │                 │
                          │  handleRemoteMiss     │                 │
                          │  handleRecallRequest  │                 │
                          │  handleGrant          │                 │
                          └──────────┬────────────┘                 │
                                     │                               │
                              ┌──────▼──────┐                       │
                              │  UBAdapter  │  ◄── EP ⇄ outer boundary
                              └──────┬──────┘                       │
                                     │ sendMessage(UBMsg)             │
                                     │ MsgQueue (FIFO, T ticks)      │
                              ┌──────▼──────┐                       │
                              │  UBRouter   │                       │
                              │  ┌───────┐  │                       │
                              │  │ UBCC  │  │                       │
                              │  └───────┘  │                       │
                              └──────┬──────┘                       │
                                     │ MsgQueue                     │
                                     ▼ (to UBRouter_j, j≠i)         │
                        └──────────────────────────────────────────────┘
```

## Inter-Node Topology

```
   Node 0                        Node 1                        Node 2
  ┌────────┐                   ┌────────┐                   ┌────────┐
  │UBRouter│◄────MsgQueue─────►│UBRouter│◄────MsgQueue─────►│UBRouter│
  │ ┌────┐ │                   │ ┌────┐ │                   │ ┌────┐ │
  │ │UBCC│ │                   │ │UBCC│ │                   │ │UBCC│ │
  │ └────┘ │                   │ └────┘ │                   │ └────┘ │
  └───┬────┘                   └───┬────┘                   └───┬────┘
      │                            │                            │
  MsgQ│                        MsgQ│                        MsgQ│
  (A) │                        (A) │                        (A) │
      │                            │                            │
  ┌───▼────┐                   ┌───▼────┐                   ┌───▼────┐
  │UBAdapter│                  │UBAdapter│                  │UBAdapter│
  └───┬────┘                   └───┬────┘                   └───┬────┘
      │                            │                            │
      ▼                            ▼                            ▼
  EPBackend_0                 EPBackend_1                 EPBackend_2
```

Three MsgQueue types:
- **MsgQ+A**: UBAdapter_i ↔ UBRouter_i (intra-node, configurable latency T)
- **MsgQ+B**: UBRouter_i ↔ UBRouter_j (inter-node, configurable latency, future ns3ub)

## DSM Address Layout (per-node view)

```
                  LocalPrivate                  DSM cross-node
  ┌─────────────────┬─────────────────┬─────────────────┬─────────────────┬─────────────────┐
  │  [0*SEG..1*SEG) │  [1*SEG..2*SEG) │  [2*SEG..3*SEG) │  [3*SEG..4*SEG) │  [4*SEG..5*SEG) │
  │   LocalPrivate  │  UbccExclusive  │     DSM_0       │     DSM_1       │     DSM_2       │
  │   → L_SNF/DDR4  │   → L_SNF/DDR4  │  → EP_SNF→UBCC  │  → EP_SNF→UBCC  │  → EP_SNF→UBCC  │
  └─────────────────┴─────────────────┴─────────────────┴─────────────────┴─────────────────┘
  0              128M             256M             384M             512M             640M

  SEG_SIZE = 128 MB
  Node i: base = i << 40  (i.e. Node1 = 0x10000000000)
```

## Message Types (UBMsg)

```
┌─────────────────────────────────────────────────────────────────┐
│                         UBMsg (fixed envelope)                   │
├──────────┬──────────┬────────┬────────┬──────────┬──────────────┤
│ type(4B) │ txnId(8B)│ src(4B)│ dst(4B)│ homePa(8B)│ epoch(8B)    │
├──────────┴──────────┴────────┴────────┴──────────┴──────────────┤
│ reqId(8B) │ authEpoch(8B) │ flags(4B) │ dataLen(2B) │           │
├───────────┴───────────────┴───────────┴─────────────┴───────────┤
│                       union payload                             │
│  ReadReq │ ReadResp │ ClearReq │ ClearResp │ RecallReq │ ...     │
├─────────────────────────────────────────────────────────────────┤
│                      data[64] (optional)                        │
└─────────────────────────────────────────────────────────────────┘
```

| Type | Direction | Purpose |
|------|-----------|---------|
| ReadReq | EP→UBCC | OuterRequest (ReadShared/ReadUnique) |
| ReadResp | UBCC→EP | Grant (G_S/G_E/G_M) or BUSY |
| ClearReq | EP→UBCC | Commit grant handshake |
| ClearResp | UBCC→EP | ClearAck |
| RecallReq | UBCC→EP(owner) | Request dirty data from owner |
| RecallResp | EP(owner)→UBCC | Dirty data response |
| InvalidateReq | UBCC→EP(sharer) | Invalidate shared copy |
| InvalidateAck | EP(sharer)→UBCC | Invalidation complete |
| UpgradeReq | EP→UBCC | Local write upgrade |
| UpgradeResp | UBCC→EP | Upgrade granted |
| UpgradeDone | EP→UBCC | Upgrade finished |
| UpgradeAck | UBCC→EP | Upgrade complete |

## Current Implementation Status

| Message Path | Status |
|-------------|--------|
| ReadReq → ReadResp (OuterRequest→Grant) | ✅ Messaged |
| ClearReq → ClearResp | ❌ Still synchronous |
| RecallReq → RecallResp | ❌ Still synchronous |
| InvalidateReq → InvalidateAck | ❌ Still synchronous |
| UpgradeReq → UpgradeResp → UpgradeDone → UpgradeAck | ❌ Still synchronous |
| Writeback, Evict | ❌ Still synchronous |

## Migration completed: Phase 1+2 (1/11 paths)
