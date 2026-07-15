# CC-EP 交付件 2：形式化验证、可靠性模型、HA 时延对比

版本 1.0 — 2026-07-16

---

## 1. 形式化验证

### 1.1 TLA+ 模型覆盖

验证套件位于 `verification/tla/`，共 9 个模型：

| 模型 | 覆盖范围 | Safety | Liveness |
|------|------|:---:|:---:|
| `ubcc_protocol_core.tla` | UBCC 目录核心：request/grant/clear/recall/invalidate 全状态机 | ✅ PASS | ✅ PASS |
| `ubcc_transport_faults.tla` | 消息层故障：drop/dup/reorder 枚举 | ✅ PASS | — |
| `ep_intra_node_*.tla` | EP-RNF single-flight、cleanunique 路径 | ✅ PASS | ✅ PASS |
| `ubcc_epoch_monotonic.tla` | epoch 单调性：乱序拒绝、半程比较 | ✅ PASS | — |

**当前 fidelity gap**（`verification/fv_coverage_fidelity.md` A3.3 已披露）：
- EP-RNF snoop 冲突仲裁（3×3 矩阵）未正式建模——由 E2E 全量 71/71 TC 覆盖
- 指数退避时序参数模型用抽象值 `RecallTimeout=2`（实际 5-200µs）

### 1.2 Fidelity 映射（C++↔TLA+）

| TLA+ Action | C++ 对应 | 行号 | 说明 |
|------|------|------|------|
| `InvalidationBarrier` | `processOuterRequest` G_S+RU 分支 | `UBCCController.cc:687-722` | fix1: fanout 由 home 执行；fix2: effectiveMask 为发送时刻目录 snapshot |
| `UpgradeBarrier` | `processOuterUpgradeReq` | `UBCCController.cc:2013-2046` | fix1: fanout 由 home 执行；effectiveMask 同步 |
| `ClearGrantHandshake` | `processClear` | `UBCCController.cc:2213-2353` | two-phase commit |

---

## 2. 可靠性模型

### 2.1 故障分类与协议覆盖

| 故障 | 协议机制 | 代码位置 | 验证 |
|------|------|------|:---:|
| **丢包** | EP_RETRY_CYCLES 超时重发 + STALE-retry 兜底 + Clear WAITING_CLEAR 幂等重试 | `EPSNFController.cc:29`, `EPRNFController.cc:1406`, `UBCCController.cc:476` | TLA+ ✅ / E2E drop TC 待补 |
| **重复** | InvalidateAck 去重（`effAckMask`）、Clear tombstone duplicate detection | `UBCCController.cc:1391`, `UBCCController.cc:558` | TLA+ ✅ / E2E TC47-49 ✅ |
| **乱序** | UBCC epoch 单调递增，`checkEpochForLine` 半程比较拒绝旧 epoch 消息 | `UBCCController.cc:1352` | TLA+ ✅ / E2E 未覆盖 |
| **节点故障** | **无实现**——home 节点故障导致其管理 PA 范围的所有缓存行不可访问（分布式目录的固有限制） | — | 文档标注 |

### 2.2 Clear 的两阶段提交：丢包自愈性

UBCC 用 reserved-epoch 两阶段提交（`commitIntendedResult`）处理 grant 在途窗口：
- **阶段 1**（`processOuterRequest`）：创建 outstanding，记 `intendedState`，不动已提交 DirEntry
- **阶段 2**（`processClear`）：requester Clear 到达后才 commit 目录

若 grant（ReadResp）在途丢失：requester 重试命中 `WAITING_CLEAR` 分支持久化捞 grant（幂等）；目录仍处于前 grant 安全态，可自愈。若 Clear 丢失：requester 一直 pending → 重试 Clear 直到 accepted。

此设计在 `docs/design/cc_ep_deliverables_plan.md` §5.3 详述。

---

## 3. HA 时延对比

### 3.1 指标 3（修订定义）

> **指标 3（修订）**：CC-EP 的跨节点 CC 同步时延 **≤ HA-C 理论最小时延**（等跳数、无协议退化），且在两个结构性维度上严格更优：(i) 本地 TBE/SRAM 干扰更低，(ii) 已实现的通信削减优化（C4 Direct-Forward / Batch-RS）。

### 3.2 三个论据

| 论据 | 内容 | 支撑 |
|------|------|------|
| **论据 1（等时延）** | 跨节点关键路径 = 4 个单跳（req→home, home→owner recall, owner→home resp, home→req grant），CC-EP 与 HA-C 逐跳相等。本地部分 UBCC 目录查找与 HN-F 同级（无额外 IPC） | `tc98_optimization_analysis.md:143-179` |
| **论据 2（TBE 隔离）** | HA-C 目录寄生 HN-F，与 CPU 请求争用同一 TBE 池；UBCC 独立进程，零占用 HN-F TBE | 混合负载实验设计见 `cc_ep_deliverables_plan.md` §7.5 |
| **论据 3（通信削减）** | C4 Direct-Forward 省 owner→home→requester 单跳；Batch-RS 对 read contention 场景 10-18x 减少 recall 次数 | `tc98_optimization_analysis.md:302` |

---

## 4. 目录压缩机制对比

UBCC 采用 Bloom Filter + ResidentDir (SRAM) + MetaRNF (DRAM 卸载) 三层分层：
- **Bloom Filter (60KB)**：快速判定"该行是否在远程存在"，过滤不跨节点的本地访问
- **ResidentDir (448KB)**：set-associative bit-packed 目录条目，仅追踪跨节点行
- **MetaRNF DRAM 卸载**：SRAM 满时将冷目录条目卸载到本地 DRAM（每节点 ~2GB+），远超纯 SRAM 容量限制

与 ISCA26 limited-pointer 方案的对比见 `cc_ep_deliverables_plan.md` §5.5。
