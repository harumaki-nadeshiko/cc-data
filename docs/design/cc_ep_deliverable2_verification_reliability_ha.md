# CC-EP 交付件 2：形式化验证、可靠性模型、HA 时延对比

版本 1.3 — 2026-08-10（增加 ArmO3CPU refinement proof 与 executable evidence）

---

## 1. 形式化验证

### 1.1 TLA+ 模型覆盖

验证套件位于 `verification/tla/`。核心模型和当前实现 focused 模型如下：

| 模型 | 覆盖范围 | Safety | Liveness |
|------|------|:---:|:---:|
| `ubcc_protocol_core.tla` | UBCC 目录核心抽象：request/grant/clear/recall/invalidate 关键状态转移 | ✅ PASS | ✅ PASS |
| `ubcc_transport_faults.tla` | 消息层故障：drop/dup/reorder 枚举 | ✅ PASS | — |
| `ep_intra_node_*.tla` | EP-RNF single-flight、cleanunique 路径 | ✅ PASS | ✅ PASS |
| `ubcc_multi_pa.tla` / `ubcc_multi_socket.tla` | 跨 PA 隔离和跨 socket 路由 | ✅ PASS | — |
| `ubcc_tc224_waiter_retirement.tla` | TC224 Clear commit 精确退役 stale Read waiter | ✅ PASS：274,593 states | — |
| `ep_rnf_snoop_arbitration.tla` | 当前 EP-RNF STALE/IMMED 3×3 仲裁 | ✅ PASS：328 states | — |
| `ubcc_tc157_partial_ack_redrive.tla` | partial Ack、pending-only redrive、stable tuple audit | ✅ PASS：171 distinct | ✅ PASS：171 distinct |
| `ubcc_tc159_upgrade_replay.tla` | Notify-drop replay 控制流；标签级 exact/mismatch 抽象 | ✅ PASS：99 distinct | 条件 PASS：41 distinct |
| `ubcc_tc159_tuple_guards.tla` | Notify/Done 逐字段 guard，含 early Done cache/commit | ✅ PASS：896 distinct | — |
| `ubcc_retry_exhaustion.tla` | proposed EXHAUSTED terminal contract 自洽性 | ✅ PASS：recover/permanent | ✅ PASS |
| `ep_o3_completion_backpressure.tla` | O3 两 line pending、ReadUnique strict completion、no-data、可靠 output/backpressure | ✅ PASS：4,564 distinct | ✅ PASS：4,564 distinct |

**当前 fidelity 边界**（详见 `verification/fv_coverage_fidelity.md`）：
- TC224 focused 模型覆盖 exact waiter retirement，不包含真实 ResidentDir/H64 容量与时序；后者由 focused host regression 和 full-scale TC224 E2E 覆盖。
- EP-RNF focused 模型覆盖仲裁决策，不包含 CHI payload、TBE 和完整 HN-F 状态机。
- RECALL orphan 模型使用抽象 `RecallTimeout=2`；生产实现使用实际 tick 参数。模型证明机制形状，不证明 timeout 数值调优。
- TC159 replay liveness 只覆盖 `UpgradeAckNotify` Drop，并依赖 Home ready 后保留 replay budget；不覆盖 UpgradeReq/UpgradeResp Drop。
- tuple-guard strengthened PASS 是拟议严格 guard 的可行性；当前 C++ 的 mismatched Notify/Done 和 early Done 路径已有 expected counterexample。
- retry-exhaustion PASS 证明 proposed terminal state machine 自洽，不表示当前 C++ 已实现 `EXPECTED_RETRY_EXHAUSTION`。
- O3 focused PASS 只覆盖 CPU/Ruby/EP refinement boundary 的两 line、两 beat bounded model；不包含 ArmO3 pipeline、完整 Ruby TBE/CHI 或 ARM ISA memory model。

本轮 focused 结果、原始日志、hash 和模型边界见
`verification/formal_reliability_results_20260807_zh.md` 与
`verification/results/formal_run_manifest_20260807.tsv`；O3 addendum 见
`verification/formal_reliability_o3_addendum_20260810_zh.md`。

### 1.2 Fidelity 映射（C++↔TLA+）

| TLA+ Action | C++ 对应 | 行号 | 说明 |
|------|------|------|------|
| `InvalidationBarrier` | `processOuterRequest` G_S+RU 分支 | `UBCCController.cc:687-722` | fix1: fanout 由 home 执行；fix2: effectiveMask 为发送时刻目录 snapshot |
| `UpgradeBarrier` | `processOuterUpgradeReq` | `UBCCController.cc:2013-2046` | fix1: fanout 由 home 执行；effectiveMask 同步 |
| `ClearCommit` | `processClear` | `UBCCController.cc:3466-3680` | 校验 epoch/reqId/requester/stage，commit，退役 waiter，安装 tombstone，replay |
| `RetireCommittedWaiter` | `retireCommittedResidentWaiters` | `UBCCController.cc:663-695` | 精确匹配 Read `(PA,node,socket,reqId)`；legacy reqId=0 再匹配 base epoch；保留非匹配和非 Read waiter |
| `ReplayAfterErase` | `replayResidentWaiters` | `UBCCController.cc:1052-1292` | 同步 Clear 可删除 queue；replay 在后续访问前重新查找 iterator |
| `SnoopArbitrate` | `recvSnoopMsg` | `EPRNFController.cc:382-489` | active recall 优先；ReadShared+SnpOnce immediate data；冲突写类 immediate STALE |
| `ReceiveData` / `ReceiveCompUC` | EPRNF ReadUnique completion bookkeeping | `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc` | Data 和 `Comp_UC` 可任意顺序；no-data completion 显式结束 data phase |
| `SendCompAck` / `CompleteCallback` | pending response retry 与 callback gate | `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc` | `CompAck` 实际注入 output 后才 callback；反压时保留 pending state |
| `SendGrantBeat` | EP-SNF/MetaRNF reliable data output | `gem5/src/mem/ruby/protocol/chi/ep/` | output 满时保留 beat 并重试，不提前推进 transaction |

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

甲方已知 HA 工作域是 2 节点、VI、网络不考虑丢包，但网络不保证 FIFO；不同地址还可因
处理器 OoO/弱内存序乱序完成。网络 non-FIFO 与 CPU OoO 可分层抽象，但二者在
CPU/Ruby/EP refinement boundary 必须联合验证 completion、并发和 output backpressure。
这与 CC-EP 的 transport drop/dup/reorder 鲁棒性模型不是同一个比较域：

- CC transport fault 模型是我方扩展能力，不用于论证甲方 HA 在合同工作域内更差；
- HA 理论分析默认 lossless transport，不能加入虚构的丢包重试成本；
- 网络 non-FIFO 需要 stable transaction identity、same-line serialization 和 stale/duplicate handling；
- CPU OoO 需要按 acquire/release、barrier 和可观察内存序分析，不能等同于网络消息重排；
- `ep_o3_completion_backpressure.tla` 已对两 line pending、Data/`Comp_UC` 任意顺序、strict callback 和 temporary backpressure 做 bounded proof；
- ArmO3CPU、Sequencer outstanding=16 的原有 146 TC 为 146/146 PASS，TC300-303 为 4/4 PASS；
- 当前仍未建立完整 ARM memory-model/OoO litmus 形式模型，不能把 focused proof 或 E2E outcome 表述为完整 ISA proof。

---

## 3. HA 时延对比

### 3.1 合同指标 3

> **原始门槛：** OurCC 跨节点 CC 同步平均时延 `<` 甲方 HA 实现的理论平均时延。

`<= + 结构性优势` 只能作为双方书面合同变更，不能由本交付件自行替换严格 `<`。

### 3.2 外部研究后的当前判定

**当前结论：`UNPROVEN（存在实质性 RISK）`。**

外部规范和论文支持以下边界：

- directory/Home 必须提供 per-line serialization authority；
- direct data 不自动等于 direct permission/authority；
- 无显式 Ack 仍需要可验证的 completion/ordering；
- 2-bit presence 不能单独表达 dirty/latest owner 和 transient；
- Arm/RISC-V ISA completion 不能由内部 fabric response 自动替代。

这些资料不能确定甲方私有 write policy、authority、commit、placement、service 或 workload
weights。合法 direct-data+authority HA 分支可具有 K=3 的 visible path，对 OurCC 构成
`RISK/FAIL` 条件分支；central-return 分支常同 K=4，同 K 仍必须证明 `P_OurCC<P_HA`。

### 3.3 统一比较模型

必须分别报告：

- `T_visible`：latest data + authority + agreed requester install。
- `T_commit`：HA/Home metadata 原子 commit。
- `T_next`：下一同址冲突可安全开始。
- `T_root_current`：OurCC current 等到 ClearResp accepted 的 root completion。

```text
T_s(o,x) = K_logical_s(o,x) * tau + P_s(o,x)
T_s(o,x) = K_crossnode_s(o,x) * tau + P'_s(o,x)
P = P_dir + P_peer + P_data + P_install + P_commit + P_queue
```

OurCC 严格快于 HA 当且仅当：

```text
(K_HA - K_OurCC) * tau > P_OurCC - P_HA
```

同 K 只能证明同阶，不能满足原始严格 `<`。

### 3.4 结构性优势的正确用途

| 优势 | 可以支持 | 不能替代 |
|------|------|------|
| UBCC 不占 HN-F TBE | 独立资源/干扰 qualification | 严格时延 `<` |
| 显式 epoch/reqId/Clear/tombstone | 可审计 safety/replay | HA 必然更慢 |
| C4 direct data | 3+ 节点 data-route 优化 | 2 节点完整 authority 优势 |
| Batch-RS | 指定 workload 的通信削减 | 所有 HA 分支的理论平均 |
| fault robustness | 独立 reliability qualification | lossless HA baseline 的重试成本 |

### 3.5 外部研究附件和关闭路径

- 主报告：`docs/research/ourcc_vs_customer_ha_external_research_report_20260806_zh.md`
- 一页结论：`docs/research/target3_onepage_summary_20260806_zh.md`
- 甲方 15 题：`docs/research/customer_ha_questions_20260806_zh.md`
- 来源矩阵：`docs/research/ha_coherence_source_matrix_20260806.tsv`
- DAG：`docs/research/ha_ourcc_operation_dags_20260806.md`
- Litmus：`docs/research/arm_riscv_coherence_litmus_plan_20260806_zh.md`

Q1-Q5 未关闭时整体保持 `UNPROVEN`。最终 strict PASS 需要共同 root counter、冻结 weights/
placement/profile、paired multi-run，并要求 `delta=T_mean_HA-T_mean_OurCC` 的预注册 95%
单侧置信下界严格大于 0。

---

## 4. 目录压缩机制对比

UBCC 采用 Bloom Filter + ResidentDir (SRAM) + MetaRNF (DRAM 卸载) 三层分层：
- **Bloom Filter (60KB)**：快速判定"该行是否在远程存在"，过滤不跨节点的本地访问
- **ResidentDir (448KB)**：set-associative bit-packed 目录条目，仅追踪跨节点行
- **MetaRNF DRAM 卸载**：SRAM 满时将冷目录条目卸载到本地 DRAM（每节点 ~2GB+），远超纯 SRAM 容量限制

与 ISCA26 limited-pointer 方案的对比见 `cc_ep_deliverables_plan.md` §5.5。
