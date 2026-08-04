# CC-EP 交付件 2：形式化验证、可靠性模型、HA 时延对比

版本 1.1 — 2026-08-03（同步当前实现）

---

## 1. 形式化验证

### 1.1 TLA+ 模型覆盖

验证套件位于 `verification/tla/`。核心模型和当前实现 focused 模型如下：

| 模型 | 覆盖范围 | Safety | Liveness |
|------|------|:---:|:---:|
| `ubcc_protocol_core.tla` | UBCC 目录核心：request/grant/clear/recall/invalidate 全状态机 | ✅ PASS | ✅ PASS |
| `ubcc_transport_faults.tla` | 消息层故障：drop/dup/reorder 枚举 | ✅ PASS | — |
| `ep_intra_node_*.tla` | EP-RNF single-flight、cleanunique 路径 | ✅ PASS | ✅ PASS |
| `ubcc_multi_pa.tla` / `ubcc_multi_socket.tla` | 跨 PA 隔离和跨 socket 路由 | ✅ PASS | — |
| `ubcc_tc224_waiter_retirement.tla` | TC224 Clear commit 精确退役 stale Read waiter | ✅ PASS：274,593 states | — |
| `ep_rnf_snoop_arbitration.tla` | 当前 EP-RNF STALE/IMMED 3×3 仲裁 | ✅ PASS：328 states | — |

**当前 fidelity 边界**（详见 `verification/fv_coverage_fidelity.md`）：
- TC224 focused 模型覆盖 exact waiter retirement，不包含真实 ResidentDir/H64 容量与时序；后者由 focused host regression 和 full-scale TC224 E2E 覆盖。
- EP-RNF focused 模型覆盖仲裁决策，不包含 CHI payload、TBE 和完整 HN-F 状态机。
- RECALL orphan 模型使用抽象 `RecallTimeout=2`；生产实现使用实际 tick 参数。模型证明机制形状，不证明 timeout 数值调优。

### 1.2 Fidelity 映射（C++↔TLA+）

| TLA+ Action | C++ 对应 | 行号 | 说明 |
|------|------|------|------|
| `InvalidationBarrier` | `processOuterRequest` G_S+RU 分支 | `UBCCController.cc:687-722` | fix1: fanout 由 home 执行；fix2: effectiveMask 为发送时刻目录 snapshot |
| `UpgradeBarrier` | `processOuterUpgradeReq` | `UBCCController.cc:2013-2046` | fix1: fanout 由 home 执行；effectiveMask 同步 |
| `ClearCommit` | `processClear` | `UBCCController.cc:3466-3680` | 校验 epoch/reqId/requester/stage，commit，退役 waiter，安装 tombstone，replay |
| `RetireCommittedWaiter` | `retireCommittedResidentWaiters` | `UBCCController.cc:663-695` | 精确匹配 Read `(PA,node,socket,reqId)`；legacy reqId=0 再匹配 base epoch；保留非匹配和非 Read waiter |
| `ReplayAfterErase` | `replayResidentWaiters` | `UBCCController.cc:1052-1292` | 同步 Clear 可删除 queue；replay 在后续访问前重新查找 iterator |
| `SnoopArbitrate` | `recvSnoopMsg` | `EPRNFController.cc:382-489` | active recall 优先；ReadShared+SnpOnce immediate data；冲突写类 immediate STALE |

---

## 2. 可靠性模型

### 2.1 故障分类与协议覆盖

| 故障 | 协议机制 | 代码位置 | 验证 |
|------|------|------|:---:|
| **丢包** | requester retry、Clear tombstone/idempotence、orphan cleanup | `ubio_main.cc:190-237`; UBCC Clear/outstanding paths | TLA+ fault envelope ✅；E2E TC110、TC118/119 sampled coverage；TC117-119 strict smoke 3/3 PASS |
| **重复** | ack bitmask idempotence、Clear tombstone、相同 waiter tuple 去重 | `UBCCController.cc`: ack handlers, `enqueueResidentWaiterIfNew`, `processClear` | TLA+ ✅；E2E TC47-49、TC119 sampled coverage |
| **乱序/延迟** | ubio deferred queue 按 `fireTick` 延迟真实消息；epoch/reqId/tombstone 拒绝 stale/duplicate commit | `ubio_main.cc:126-237,2667-2668` | TLA+ ✅；TC117-119 strict smoke 3/3 PASS；TC148 32-hit ClearReq qualification PASS，逐规则验收 |
| **节点故障** | **无实现**——home 节点故障导致其管理 PA 范围的所有缓存行不可访问（分布式目录的固有限制） | — | 文档标注<br>FaultPipeline/hop-scheduler/JSON architecture documented in `docs/recovery/ras_fault_injection_plan.md` is not yet implemented — planned for next phase. |

### 2.2 Clear 的两阶段提交：丢包自愈性

UBCC 用 reserved-epoch 两阶段提交（`commitIntendedResult`）处理 grant 在途窗口：
- **阶段 1**（`processOuterRequest`）：创建 outstanding，记 `intendedState`，不动已提交 DirEntry
- **阶段 2**（`processClear`）：requester Clear 到达后才 commit 目录

若 grant（ReadResp）在途丢失：requester 重试命中 `WAITING_CLEAR` 分支持久化捞 grant（幂等）；目录仍处于前 grant 安全态，可自愈。若 Clear 丢失：requester 一直 pending → 重试 Clear 直到 accepted。

此设计在 `docs/design/cc_ep_deliverables_plan.md` §5.3 详述。

### 2.3 TC224 committed waiter 活性闭环

Full-scale TC224 暴露的最终死锁不是 Clear 本身未提交，而是提交后同一
`(PA,node,socket,reqId)` stale Read waiter 仍可在 tombstone 过期后 replay，创建第二个
`WAITING_CLEAR`。requester 已缓存完成结果，不会发送第二次 Clear，导致该 set 永久被 pin。

当前实现按以下顺序处理成功 Clear：

1. `commitIntendedResult` 更新 committed directory；
2. `retireCommittedResidentWaiters` 只删除精确匹配的 stale Read waiter；
3. legacy `reqId=0` 额外要求 waiter epoch 等于 outstanding base epoch；
4. Writeback/Upgrade/Evict 和不同 requester/socket/reqId waiter 保留；
5. 安装 tombstone、删除 outstanding，再 replay 其余请求；
6. replay 在同步 Clear 可能删除 queue 后重新获取 map iterator。

focused TLA+ 模型 `ubcc_tc224_waiter_retirement.tla` 在 2 nodes × 2 sockets、
`reqId={0,1}`、2 epochs、最多 2 waiters 的界限内穷举 274,593 个 distinct states，
验证上述精确退役和保留性质，零反例。完整容量/H64 行为由 host regression 与
TC224 8,192/65,536 full-scale PASS 补充，不能把 focused 模型扩大表述为完整目录证明。

### 2.4 甲方 HA 可靠性假设边界

甲方已知 HA 工作域是 2 节点、VI、网络不考虑丢包，乱序主要来自处理器 OoO。
这与 CC-EP 的 transport drop/dup/reorder 鲁棒性模型不是同一个比较域：

- CC transport fault 模型是我方扩展能力，不用于论证甲方 HA 在合同工作域内更差；
- HA 理论分析默认 lossless transport，不能加入虚构的丢包重试成本；
- CPU OoO 需要按 acquire/release、barrier 和可观察内存序分析，不能等同于网络消息任意乱序；
- 当前 TLA+ 套件尚未建立完整 ARM memory-model/OoO litmus 模型，这是交付件 2 的明确后续项。

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
