# FV Overview: Intra/Inter-Node 验证综合报告

**状态**: 全部 11 个 FV 任务完成  
**成本**: $3.63 / $13.00 (27.9%)  
**产出**: 11 份交付物在 `docs/recovery/fv*.md`

---

## 1. 概述

本次验证以 UBAdapter 为边界，将系统拆为跨节点（M1）和节点内（M2）两部分，按三层证明模型（L1 目录安全 + L2 生命周期 + L3 故障/活性）分三波次执行。

---

## 2. Wave 0: 状态空间冻结（$1.19）

| FV | 目标 | 关键结论 |
|----|------|---------|
| **FV-9** 消息字段表 | 冻结 20 种 UBMsg 的必填/可选字段 | 发现 1 严重问题: `UpgradeAckNotify` 在 `union UBMsgBody` 中**无条目**，未来访问会读到垃圾。字段按语义/路由/运行时三级分类完成。 |
| **FV-1** 状态枚举 | 穷举 MESI × OpType × OpStage 可达组合 | 输出 **56 条合法状态边**，分 10 组（A-J），含非法边检测（G_S+sharers=0 panic, G_E/G_M+popcount≠1 panic）。每条边标注"仅改 live state"还是"真正提交目录"。 |
| **FV-2** 不变量证明 | epoch 单调性 + sharersMask 约束 | epoch **仅 2 个写点**（`commitIntendedResult`、初始化），均单调。所有 12 个 `_directory.update()` 调用经 `validateCanonical` 检查。**结论: 不变量成立。** |
| **FV-3** 生命周期审计 | OutstandingRequest create→remove | GRANT_HANDSHAKE(9 create, 2 remove)、INVALIDATE(1 create, 原地转换)、RECALL(1 create, retry 依赖 remove)、UPGRADE_PENDING(1 create, 2 remove)。**仅 RECALL.DONE 无 GC**。`replayArmed` set-only 安全。`ackMask` OR-only 单调。 |
| **FV-11** 覆盖率矩阵 | 56 条边 → TC 映射 | 38 条直接覆盖 (68%), 8 条间接覆盖 (14%), 8 条未覆盖 (14%) — 主要是 **Dual-Socket 全部边缘** (num_sockets=1 无法触达) 和 G_E/G_M → upgrade-owner 语义缺口。 |

---

## 3. Wave 1: 边界+M2 可观测性（$0.02）

| FV | 目标 | 关键结论 |
|----|------|---------|
| **FV-10** 序列化 | 语义+路由字段 round-trip schema | 定义 13 条收发路径的字段保留表，输出 JSON Schema（20 种 oneOf），13 个发送捕捉点 + 10 个接收捕捉点 + 3 个路由器交叉检查点的插桩建议。`seqNum/enqueueTick/readyTick` 排除。 |
| **FV-6** snoop 矩阵 | Snoop 分类延迟正确性 | 三种 snoop 响应均符合 Q3=B 分类: SnpCleanInvalid 延迟、recall snoop 排队、其余即时。发现 **4 个问题**: SnpUnique `retToSrc` 注释/代码不一致；SnpShared 诊断路径；CHI txn 进行中 upgrade-path 上下文缺失；`receiveUpgradeAck` 无上下文时丢 snoperesp。14 个插桩点推荐。 |
| **FV-8** invalidate barrier | "无提前 ack" 不变量 | **不变量成立**: 三条生产路径（正常完成、重复 pending、send 失败）均在 CleanUnique → Comp_UC → callback → `sendInvalidationAck` 之后。唯一缺口是原型 fallback（无 EP-RNF 时直接 ack），产线上是死代码。 |

---

## 4. Wave 2: 故障/活性/召回闭环（$2.42）

| FV | 目标 | 关键结论 |
|----|------|---------|
| **FV-4** 丢包/重排/重复 | tombstone 幂等 + stale 拒绝 | **Clear 窗口 W 内**: duplicate 可通过 tombstone replay 返回幂等 ClearAck。**RecallResp/InvalidateAck**: 旧 epoch 被正确拒绝。**InvalidateAck**: dup/stale 防护最完整。**RecallResp**: 缺显式 duplicate/stage 去重。loss 恢复依赖外层 timeout/retry。故障注入锚点设计到 UBRouter.cc: M1=全故障, M2=仅 reorder。 |
| **FV-7** recall 数据路径 | 端到端数据一致性 | **主链路通**: OuterRecallMsg→startReadShared/Unique→CompData→recallDataBlk→RecallResponse(data)→UBCC.dataBuf→grant。发现 **4 个风险**: `getdataBlk()` 多 beat 未显式 merge 可能覆盖丢数；`callbackPayloadStable` 是死字段；ReadUnique 未等 Comp_UC；`hasDataPayload`/`dataReturned` 语义可短暂不一致。 |
| **FV-5** 活性/死锁 | wait-for graph 无环证明 | 全面枚举 UBCC 等待依赖: OutstandingRequest 阶段 → 阻塞新请求 → 公平重试 → 无环。**TC2 replayArmed、TC7 barrier、TC10 upgrade barrier 均复核通过**。剩余风险: RECALL.DONE 无限期待原请求者重试可能形成活锁。建议 6 个活性插桩点。 |

---

## 5. 风险汇总（按严重度）

| 严重度 | 来源 | 问题 |
|--------|------|------|
| 🔴 | FV-9 | **UpgradeAckNotify 在 union UBMsgBody 中无条目** — 未来任何代码访问 `msg.b` 将读到未定义内存 |
| 🟠 | FV-7 | **多 beat recall 数据可能覆盖** — `getdataBlk()` 未显式按 beat mask 合并，多拍可能丢数据 |
| 🟠 | FV-7 | **`callbackPayloadStable` 是死字段** — ReadUnique 路径未真正用它保护数据稳定性 |
| 🟠 | FV-6 | **SnpUnique retToSrc 注释与代码不一致** — 注释说 SnpRespData_I，代码是 SnpResp_I，HN-F 可能等数据挂起 |
| 🟡 | FV-3 | **RECALL.DONE 无 GC** — 若原请求者永不重试，条目永远占用 `_outstandingReqs` |
| 🟡 | FV-7 | **ReadUnique 未等 Comp_UC** — 在最后一个 CompData beat 就回调，可能早于 HN-F 确认 |
| 🟡 | FV-4 | **RecallResp 缺 duplicate 去重** — 不如 InvalidateAck/Clear 的 dup 防护完整 |
| 🟢 | FV-11 | **Dual-Socket 零覆盖** — 所有双 socket 边无 TC（num_sockets=1 无法触达），需 NUMA=2 回归 |

---

## 6. 交付物清单

| 文件 | FV | 行数 | 内容 |
|------|-----|------|------|
| `fv1_ubcc_state_enumeration.md` | FV-1 | 状态机枚举 | 56条边，10组分类，提交/非提交标记 |
| `fv2_epoch_sharers_invariants.md` | FV-2 | 不变量证明 | 2个epoch写点，12次canonical检查 |
| `fv3_outstanding_lifecycle.md` | FV-3 | 生命周期审计 | 4类opType，create/remove平衡表 |
| `fv4_fault_recovery_report.md` | FV-4 | 故障恢复 | reorder/dup/loss防护 + 插桩点 |
| `fv5_liveness_deadlock_report.md` | FV-5 | 活性证明 | wait-for graph无环 + 活锁风险 |
| `fv6_snoop_matrix_report.md` | FV-6 | snoop矩阵 | 3种snoop × 分类延迟 = 符合/漂移 |
| `fv7_recall_path_report.md` | FV-7 | recall路径 | 端到端数据链 + 4风险点 |
| `fv8_invalidate_barrier_report.md` | FV-8 | barrier证明 | "无提前ack"成立 |
| `fv9_ubmsg_validation_table.md` | FV-9 | 字段表 | 20种消息 × 20字段 required/optional |
| `fv10_roundtrip_schema.md` | FV-10 | round-trip | JSON Schema + 13收发点 |
| `fv11_state_edge_tc_matrix.md` | FV-11 | 覆盖率 | 56边 → TC映射 + 8未覆盖边 |
