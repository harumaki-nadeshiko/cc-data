# CC-EP 跨节点 Cache Coherence 协议：体系结构与方案对比

版本 1.1 — 2026-08-07（同步当前实现与 HA 外部研究）

---

## 1. 概述

CC-EP 是基于 ARM CHI 协议探索的跨节点 Cache Coherence（CC）方案。核心思想：在每个节点独立的标准 CHI 一致性域（inner domain）之上，构建一个 EP（External Proxy）扩展层，将全局 CC 的目录与仲裁逻辑从 CPU 内的 Home Agent（HN-F）中分离到独立的 UBCC（Universal Backstore Cache Controller）进程中。

本文档涵盖：
- **§2** 体系结构——Inner/Outer 域划分，核心组件（EP-RNF, EP-SNF, UBCC）的职责
- **§3** 关键协议路径——跨节点读、写升级、recall 的完整消息流
- **§4** 方案对比——UBCC（当前）vs HA-A/B/C（替代方案）的定量与定性分析
- **§5** 已修复的协议死锁及其方法论意义

---

## 2. 体系结构

### 2.1 Inner 域与 Outer 域

```
┌───────────── Node N ─────────────────────────────────────────────┐
│  ┌─ Inner CHI Domain ───────────────────────────────────────┐   │
│  │  cpu0.l1* │ cpu0.l2 │                                     │   │
│  │  cpu1.l1* │ cpu1.l2 │                                     │   │
│  │  HN-F(socket0) ───── HN-F(socket1)                        │   │
│  │      │                      │                              │   │
│  │  EP-RNF ──────────────────── EP-SNF                       │   │
│  └──────┬──────────────────────┬─────────────────────────────┘   │
│         │ (CHI req/snoop)      │ (ReadNoSnp/Writeback)            │
│  ┌──────▼──────────────────────▼─────────────────────────────┐   │
│  │  UBIO Process: UBCC (Home Agent/Directory)                │   │
│  │  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐ │   │
│  │  │ Bloom Filter │  │ ResidentDir  │  │ MetaRNF (DRAM)   │ │   │
│  │  │ (60KB SRAM)  │  │ (448KB SRAM) │  │ (目录卸载)       │ │   │
│  │  └─────────────┘  └──────────────┘  └──────────────────┘ │   │
│  └───────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

- **Inner 域**：标准 gem5 CHI 协议。每个 CPU socket 有自己的 HN-F（本地 Point of Coherence）。
  EP-RNF 和 EP-SNF 被注册为 HN-F 的特殊 RN-F/SN-F。
- **Outer 域**：跨节点一致性由 UBCC 管理。UBCC 维护全局目录（Bloom Filter + ResidentDir + MetaRNF DRAM 卸载），
  协调跨节点 recall、invalidation、grant。

### 2.2 核心组件

| 组件 | 位置 | 职责 |
|------|------|------|
| **EP-RNF** (`EPRNFController`) | gem5 进程 | HN-F 的 "外部世界代理"。HN-F 将外部世界视为一个 sharer，对同址写升级时 snoop EP-RNF，EP-RNF 代外部世界响应——发起 OuterUpgradeReq 去失效远程副本，或在冲突时执行 STALE-retry 让本地写 abort-retry 经全局序重排 |
| **EP-SNF** (`EPSNFController`) | gem5 进程 | 响应本地 CPU 的 ReadNoSnp/WriteNoSnp，将请求路由到 UBCC |
| **UBCC** (`UBCCController`) | ubio 进程 | 全局 Home Agent。管理 ResidentDir（SRAM 目录）+ Bloom Filter（预过滤）+ MetaRNF DRAM 卸载。对每个 PA 维护全局 sharer 状态、epoch 单调序列号、协调跨节点 recall/invalidation 扇出 |
| **UBAdapter** | gem5 进程 | gem5↔ubio 的 IPC Portal。消息序列化/反序列化、PDES 时钟同步（`safeTs`）、push-grant 响应缓存 |
| **Networksim** | 独立进程 | 节点间消息路由（仅多进程 `--8n*` 拓扑） |

### 2.3 EP-RNF 的 Snoop 仲裁（3×3 矩阵）

EP-RNF 在持有 in-flight CHI 事务期间收到同址 snoop 时，不盲目排队（原死锁#2 根因），而按以下矩阵仲裁：

| in-flight ↓ \ snoop → | SnpCleanInvalid | SnpUnique | SnpOnce | SnpShared/Fwd |
|------|:---:|:---:|:---:|:---:|
| CleanUnique (InvalidateOnly) | STALE | STALE | STALE | fatal |
| ReadUnique (RecallUnique) | STALE | STALE | STALE | fatal |
| ReadShared (NoProxyOp) | STALE | STALE | IMMED | fatal |
| （良性 self-snoop） | IMMED clean SnpResp_I ||||

- **STALE**：回 stale SnpResp_I，让发起方 CleanUnique 完成为 Comp_UC(stale)，L2 检测后 re-fetch 经全局序重排
- **IMMED**：即时正常响应（clean SnpResp_I 或 SnpRespData_SC）
- **fatal**：保序 snoop 不得指向 EP-RNF（路由错误）

详见 `docs/design/eprnf_snoop_conflict_arbitration_plan.md` §9。

---

## 3. 关键协议路径

### 3.1 跨节点读（ReadShared）

```
Node-A CPU read miss
  → A.HN-F → A.EP-SNF → A.UBIO → A.UBCC (lookup directory)
  → 若远程有脏副本: A.UBCC → RemoteNode.EPBackend (recall)
    → RemoteNode.EP-RNF → RemoteNode.HN-F → snoop local caches → read data
    → RemoteNode.EPBackend → A.UBCC (RecallResp + data)
  → A.UBCC grant → A.UBIO → A.EPBackend 接受 Grant
  → A.EPBackend → A.UBCC ClearReq
  → A.UBCC commit/retire/release → A.EPBackend ClearResp accepted
  → A.EP-SNF → A.HN-F → A.CPU (final CompData/ReadResp)
```

当前 Clear 不是 HN/L2 明确 install Ack；它确认 requester 协议代理已接受 Grant，并触发
Home commit。当前 root path 在 ClearResp accepted 后才向 HN/L2 返回最终数据。

### 3.2 跨节点写升级（CleanUnique → OuterUpgradeReq）

```
Node-A CPU store to shared line
  → A.HN-F → snoop EP-RNF (SnpCleanInvalid)
  → A.EP-RNF.handleSnpCleanInvalid:
      if (本地 R_E 持有者 && EP_SILENT_UPGRADE=1):
        → 静默升级: 立即 SnpResp_I (0 跨节点消息)  ← 指标2 优化
      else:
        → 发 OuterUpgradeReq → A.UBCC (home 仲裁)
        → home: processOuterUpgradeReq → fanout invalidate 到所有远程 sharer
        → 等 InvalidateAcks → OuterUpgradeAck(true) → SnpResp_I
```

### 3.3 Recall（远程节点需要本节点的脏数据）

```
RemoteNode write → UBCC RECALL to Node-A (当前 owner)
  → Node-A.EPBackend.handleRecallRequest
  → Node-A.EP-RNF.startReadUnique (CHI 事务)
  → Node-A.HN-F → snoop local caches → capture dirty data
  → callback → Node-A.EPBackend.sendRecallResponse → UBCC
  → UBCC grant to RemoteNode
```

---

## 4. 方案对比（UBCC vs HA）

> 本节只描述架构设计空间。`HA-A/B/C` 是内部分类，不是甲方实现事实；公开厂商协议也仅
> 用于说明合法机制，不得直接映射为甲方 HA。

### 4.1 HA 方案分类

| 方案 | 目录位置 | 跨节点 snoop | 与 UBCC 的核心差异 |
|------|------|------|------|
| **HA-A** | 每节点 HN-F 自管，无全局目录 | fanout 到所有远程节点（8 节点=7 个 fanout） | UBCC 有全局目录精确 fanout，减少不必要消息 |
| **HA-B** | 集中 home node 的 HN-F | 多一跳（请求→集中 home→fanout） | UBCC 免集中跳 |
| **HA-C** | 分布式目录（PA hash→home HN-F） | 逻辑角色可与 UBCC 类似，物理 traversal 依 placement | UBCC 目录外置，不占 HN-F TBE；时延仍需比较 P/queue |

### 4.2 时延对比

Remote-owner central-return 的逻辑关键链可写为
`requester->home->owner->home->requester`，即 `K_logical=4`。但合同只有 2 节点，三个
逻辑角色至少两个共址，因此通常不是四次物理跨节点 traversal。还必须分别计算
`K_crossnode` 和 `P_dir/P_peer/P_data/P_install/P_commit/P_queue`。

合法 HA 还可能采用：

- Home-memory latest 的 K=2 fast path；
- direct-data-only，数据旁路但 authority 仍经 Home；
- direct-data+authority，满足 token/version/completion 条件时 visible K 可为 3。

因此不能无条件声称 UBCC 与 HA-C “跳数完全相同”或“时延无显著差异”。当前目标 3 为
`UNPROVEN（存在实质性 RISK）`。

UBCC 的优势在于：
1. **SRAM 效率潜力**：Bloom Filter + ResidentDir + backstore 分层追踪；历史结果达到目标 1，
   但当前冻结代码复跑和 E5 provenance 尚未闭环。
2. **TBE 隔离**：目录不在 HN-F 内，不占用 CPU 请求的 TBE 资源（指标 3 论据2）
3. **已有通信优化**：C4 Direct-Forward 可优化 3+ 节点 data route，但不携带完整 Grant
   authority，且两节点三角色路径不可达；Batch-RS 只用于适用 workload 的通信削减。

这些优势必须映射到共同 `T_visible/T_commit/T_next/T_root_current` 和 `K/P/w` 后，才能进入
合同严格 `<` 的比较。详细外部研究见
`docs/research/ourcc_vs_customer_ha_external_research_report_20260806_zh.md`。

---

## 5. 已修复的协议死锁

本方案在开发过程中发现并修复了两类跨节点写-写竞争引起的死锁，它们的方法论意义是：
**不能假定 EP-RNF 是一个"快"的 RN-F——它是外部世界的代理，其事务需要外层往返；**
**当它与本地 CHI 域的正常请求并发时，必须通过仲裁（而非排队）来保持全局活性。**

| 死锁 | 根因 | 修复 | 方法论 |
|------|------|------|------|
| **死锁#1**（UBCC stale sharer + ReadReq 风暴） | `_inflightReadReqs` 只删不增→10ns 重发风暴；invalidation 目标含已被 recall 的非 sharer | 补 insert 去重；home 统一 fanout；无副本立即 ack | 目录的 sharer 跟踪不能依赖陈旧排队请求重放时的状态 |
| **死锁#2**（EP-RNF snoop 排队） | `recvSnoopMsg` 无差别排队，跨节点双写者互等形成环 | 冲突分类仲裁（STALE 矩阵）| EP-RNF 作为慢 RN-F 必须在 snoop 时响应，不能排队——即使响应是"你输了，重试" |
