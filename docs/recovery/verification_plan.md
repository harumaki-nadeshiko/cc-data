# CC-EP 验证与 RAS 方案 v4.0

## 1. 目标与边界

本文定义 `CC-EP`/`UBCC` 协议在**当前单进程 gem5 实现**与**未来 Ns3UB 多进程迁移架构**上的完整验证与 RAS 方案，覆盖：

- 形式化验证
- 仿真测试
- 故障注入
- 恢复/持久化策略
- 迁移正确性
- 工具链与覆盖率度量

适用代码范围：

- `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.{hh,cc}`
- `gem5/src/mem/ruby/protocol/chi/ep/ResidentDir.{hh,cc}`
- `gem5/src/mem/ruby/protocol/chi/ep/UBRouter.{hh,cc}`
- `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.{hh,cc}`
- `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.{hh,cc}`
- `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.{hh,cc}`
- `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.{hh,cc}`
- `gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm`
- `gem5/src/mem/ruby/protocol/chi/CHI-cache-funcs.sm`
- `gem5/src/mem/ruby/protocol/chi/CHI-cache-transitions.sm`
- `tests/e2e/test_e2e.py`
- `gem5/configs/ruby/CHI_ubcc_framework.py`

---

## 2. 顶层验证决策（按已选 Q1-Q5 固化）

| 决策 | 选项 | 结论 |
|---|---|---|
| Q1 | C | **双层模型**：M1=UBCC-only，验证目录不变量/epoch/活性；M2=EP-RNF 边界模型，验证 snoop/recall/upgrade 竞态 |
| Q2 | B | **网络允许 reorder/dup/loss**，协议依赖 eventual retry + timer recovery，而非 FIFO 假设 |
| Q3 | C | **分级恢复**：已提交 metadata 持久化；tombstone 软持久化；outstanding 不持久化，但通过 `epoch+reqId` 拒绝陈旧完成消息 |
| Q4 | A | **单 home-node 权威**：每条 line 只有一个 home UBCC 拥有最终裁决权 |
| Q5 | B | **TC→状态机边覆盖映射**：回归计划以“状态边”而不是“功能点”组织 |

这 5 项不是建议，而是本方案后续所有验证、恢复、迁移检查的固定前提。

---

## 3. 协议状态枚举表（黄金语义）

### 3.1 UBCC ResidentDir 持久状态

来源：`ResidentDir.hh`

| 状态 | 语义 | 约束 | 是否持久化 |
|---|---|---|---|
| `G_I` | 全局无副本 | `sharersMask==0` | 是 |
| `G_S` | 全局共享干净 | `popcount(sharersMask)>=1`，无唯一 owner | 是 |
| `G_E` | 全局唯一干净 owner | `popcount(sharersMask)==1` | 是 |
| `G_M` | 全局唯一脏 owner | `popcount(sharersMask)==1` | 是 |
| `Tombstone` (`G_I + residentDirty`) | resident 槽位占位/删除后痕迹，不代表协议上仍有效 | 不可对外授权 | 仅 resident 层局部存在 |

**UBCC 持久不变量**：

1. `G_S` 不允许 dirty owner。
2. `G_E/G_M` 必须 one-hot sharer。
3. `epoch` 仅在 commit 时前进。
4. ResidentDir 与 backstore 的冲突合并以“较新 committed epoch”为准。

### 3.2 Requester 侧状态

来源：`EPBackend.hh`

| 状态 | 语义 | 允许事件 |
|---|---|---|
| `R_I` | 无远端权限 | RemoteMiss, Replay |
| `R_WAIT_GRANT` | 已发起 miss，等待 home grant | Grant, Busy/Retry, Timeout |
| `R_S` | 持有共享权限 | Read hit, UpgradeReq, Invalidate |
| `R_E` | 持有干净独占 | Local read/write, Writeback, Evict, Recall |
| `R_M` | 持有脏独占 | Local read/write, Writeback, Recall |

### 3.3 OutstandingRequest 状态机

来源：`UBCCController.hh`

#### 3.3.1 操作类型

| `OpType` | 作用 |
|---|---|
| `RECALL` | 从 owner 拉回/降级数据 |
| `INVALIDATE` | 对 sharers 发无效化以支持 unique/upgrade |
| `GRANT_HANDSHAKE` | grant 已可见，等待 `Clear` 才提交目录结果 |
| `UPGRADE_PENDING` | 本地 sharer 升级为 unique 的四消息握手 |

#### 3.3.2 阶段

| `OpStage` | 语义 | 恢复语义 |
|---|---|---|
| `CREATED` | 已分配，尚未收到任何目标反馈 | 崩溃后丢弃 |
| `WAITING_TARGET_RESP` | 等 recall 目标响应 | 崩溃后丢弃，等待 requester 重试 |
| `WAITING_ALL_ACKS` | 等所有 invalidation ack | 崩溃后丢弃，等待 requester 重试 |
| `WAITING_LOCAL_DONE` | 等 `OuterUpgradeDone` | 崩溃后丢弃，等待 requester 重试 |
| `WAITING_CLEAR` | grant 已发出，等待 `Clear` 提交 | 崩溃后丢弃，但用 `epoch+reqId` 拒绝陈旧 Clear |
| `DONE` | 完成 | `GRANT_HANDSHAKE` 转 tombstone |
| `CANCELLED` | 被拒绝/失效 | 不恢复 |
| `TIMED_OUT` | 超时失败 | 不恢复，要求重试 |
| `PERSISTENT_BUSY` | 已进入不可回滚阶段，只接受匹配完成消息 | 仅作保护态，不对外新授权 |

#### 3.3.3 每类操作的规范流

| 操作 | 规范边 |
|---|---|
| `RECALL` | `CREATED → WAITING_TARGET_RESP → DONE/CANCELLED/TIMED_OUT` |
| `INVALIDATE` | `CREATED → WAITING_ALL_ACKS → DONE/CANCELLED/TIMED_OUT` |
| `GRANT_HANDSHAKE` | `CREATED → WAITING_CLEAR → DONE → tombstone(W)` |
| `UPGRADE_PENDING` | `CREATED → WAITING_ALL_ACKS/WAITING_LOCAL_DONE → DONE/PERSISTENT_BUSY` |

### 3.4 HN-F 相关 CHI 状态（交叉节点验证子集）

来源：`CHI-cache.sm`

| 类别 | 状态 |
|---|---|
| 稳态 | `I`, `SC`, `UC`, `SD`, `UD`, `UD_T` |
| 仅上游持有 | `RU`, `RSC`, `RSD`, `RUSC`, `RUSD` |
| 复合态 | `SC_RSC`, `SD_RSC`, `SD_RSD`, `UC_RSC`, `UC_RU`, `UD_RU`, `UD_RSD`, `UD_RSC` |
| 泛化暂态 | `BUSY_INTR`, `BUSY_BLKD` |

#### 交叉节点必须覆盖的关键边

| 事件 | 关键边 |
|---|---|
| First miss + `shared_hint` | `I → SC` 且 `dir_sharers += EP-RNF` |
| 本地升级 `CleanUnique` | `SC → UC/UD`，同时对 EP-RNF 发 `SnpCleanInvalid` |
| 远端共享 recall (`ReadShared`) | `UD/UC/SD → *_RSC/RSC`，`CompAck` 后收敛到 `SC/SD/UC/UD` 正确子集 |
| 远端 unique recall (`ReadUnique`) | `SC/UC/UD → UC_RU/UD_RU/BUSY_*` |
| DCT fallback | `EP-RNF only sharer` 时禁止进入 Fwd 数据源路径 |
| CompAck 容忍 | `SC_RSC/UD_RU/UC_RU/RSC/RUSC/SD_RSC/UD_RSC/UD_RSD/SD_RSD` 上 `CompAck` 必须可消费 |

### 3.5 EP-RNF snoop 响应矩阵（黄金矩阵）

> 该矩阵是**验收基准**。当前实现若与矩阵不符，应视为待修正或需兼容性说明。

| Snoop | 条件 | 黄金响应 |
|---|---|---|
| `SnpCleanInvalid` | 普通 invalidate | 立即 `SnpResp_I` |
| `SnpCleanInvalid` | 本地升级链路 | 延迟到 `OuterUpgradeAck(true)` 后再 `SnpResp_I` |
| `SnpUnique` | `retToSrc=false` 且未收集到数据 | `SnpResp_I` |
| `SnpUnique` | `retToSrc=false` 且收集到 clean data | `SnpResp_I` + `SnpRespData_I` |
| `SnpUnique` | `retToSrc=false` 且收集到 dirty data | `SnpResp_I` + `SnpRespData_I_PD` |
| `SnpUnique_RetToSrc` | `retToSrc=true` 且 clean data | `SnpRespData_I` |
| `SnpUnique_RetToSrc` | `retToSrc=true` 且 dirty data | `SnpRespData_I_PD` |
| `SnpOnce` | EP-RNF-only sharer | `SnpRespData_SC` |
| `SnpSharedFwd/SnpOnceFwd/SnpUniqueFwd` | 目标为 EP-RNF | **非法**，必须在 HN-F 侧先 DCT-off fallback |

### 3.6 UBMsg 线协议状态/类型表（迁移必须保持）

来源：`UBMsg.hh`

| 类别 | 类型 |
|---|---|
| Read 路径 | `ReadReq`, `ReadResp` |
| Recall 路径 | `RecallReq`, `RecallResp` |
| Invalidate 路径 | `InvalidateReq`, `InvalidateAck` |
| Writeback/Evict | `WritebackReq`, `WritebackResp`, `EvictReq`, `EvictResp` |
| Upgrade | `UpgradeReq`, `UpgradeResp`, `UpgradeDoneReq`, `UpgradeDoneResp`, `UpgradeAckNotify` |
| Commit 握手 | `ClearReq`, `ClearResp` |

**迁移不变量**：字段、位宽、字节序、flag 语义、`epoch/reqId/seqNum` 语义在 Ns3UB 边界前后必须严格一致。

---

## 4. 形式化验证计划

## 4.1 模型 M1：UBCC-only（目录/epoch/活性）

### 4.1.1 建模对象

- Home UBCC（单权威）
- `ResidentDir` + backstore
- `OutstandingRequest`
- `GrantHandshakeTombstone`
- 请求者抽象节点 N
- 非 FIFO 网络：可 reorder / dup / loss
- 定时器：retry、deadline、tombstone 窗口

### 4.1.2 状态变量

- `dir[line] = {mesi, sharersMask, epoch}`
- `ost[line] = {opType, stage, baseEpoch, reservedEpoch, reqId, targetMask, ackMask, dataValid}`
- `tombstone[line] = deque[(epoch, reqId, accepted, expire)]`
- `backstore[line] = committed metadata only`
- `pendingQueue[line]`
- `net[msg]`：允许丢包、乱序、重复

### 4.1.3 Safety 性质

1. **单 home 权威**：任一时刻只有 `home(line)` 能提交 `epoch+state`。
2. **共享/独占互斥**：`G_S` 不可同时存在独占 owner；`G_E/G_M` 不可多 owner。
3. **epoch 单调**：已提交 `epoch` 只前进，不回退；比较采用 half-range 规则。
4. **授权前不提交**：`reservedEpoch` 在 `Clear/UpgradeDone` 前不得写回 committed metadata。
5. **tombstone 幂等**：相同 `(line, epoch, reqId)` 的重复 `Clear` 返回同一 `accepted`。
6. **ack 单调**：`ackMask` 只能增长，不能清零回退。
7. **backstore 只镜像 committed**：未完成 outstanding 绝不落盘为 committed state。
8. **stale 完成拒绝**：陈旧 `RecallResp/InvalidateAck/Clear/UpgradeDone` 不得改变 committed state。

### 4.1.4 Liveness 性质

在公平重试假设下：

1. 被接受的 `ReadReq` 最终要么 grant，要么返回可重试 busy。
2. `INVALIDATE` 的每个目标要么 ack，要么超时触发失败/重试，不可永久悬挂。
3. `GRANT_HANDSHAKE` 不得永久泄漏；最终进入 `DONE` 或 `TIMED_OUT`，并清理 live outstanding。
4. `pendingQueue[line]` 中的请求在前序事务完成后最终被调度。

### 4.1.5 恢复语义验证

按 Q3 固化：

- 持久化：`dir committed metadata`、`backstore`
- 软持久化：`tombstone`（可丢，但保留更好）
- 不持久化：`OutstandingRequest`

验证目标：

1. 崩溃后若 outstanding 丢失，陈旧完成消息因 `epoch+reqId` 被拒绝。
2. 重新发起的请求不会把旧的 `reservedEpoch` 错误提交。
3. 丢失 tombstone 仅影响幂等体验，不影响安全性。

## 4.2 模型 M2：EP-RNF 边界（snoop/recall/upgrade 竞态）

### 4.2.1 建模对象

- HN-F（只保留交叉节点相关状态）
- EP-RNF
- EPBackend requester bookkeeping
- Home UBCC
- Remote owner/sharer 抽象 cache
- CHI Req/Rsp/Data 三通道

### 4.2.2 重点竞态

1. 两个节点同时 `ReadUnique` 同一 line。
2. recall 目标 L2 在 recall 过程中 evict/writeback。
3. `outerTxnPending=true` 时 EP-RNF 又收到新 snoop。
4. `CompAck` 到达 compound states（`SC_RSC`, `UD_RU` 等）。
5. multi-beat `CompData` 最后一拍前 callback 提前触发。
6. `pendingOwnerUpdate=true` 时又有 cross-node unique。
7. EP-RNF 被错误选为 Fwd 目标。
8. DCT 打开但 EP-RNF-only sharer 导致期望消息类型不匹配。

### 4.2.3 Safety 性质

1. EP-RNF 不直接绕过 HN-F 发本地 snoop。
2. `shared_hint` 注册后，local upgrade 必须能触发对 EP-RNF 的 `SnpCleanInvalid`。
3. `SnpUnique` 响应类型必须是 HN-F expected set 的子集。
4. `SnpOnce` 若经 EP-RNF，返回数据必须来自远端/回调缓冲，而非零填充伪成功。
5. `CompAck` 目标必须是 HN-F，而非任意 responder。

### 4.2.4 Liveness 性质

1. `OuterUpgradeReq` 被接受后，`SnpResp_I` 最终释放 HN-F。
2. recall 数据最终稳定后才允许 grant 使用。
3. `sendOrRetry`/retry queue 保证无永久背压丢失。

## 4.3 工具与产物

### 主工具

- **TLA+**：主规范
- **Apalache**：有界模型检查
- **TLC**：长轨迹/死锁搜索

### 产物

- `tools/formal/ubcc_core.tla`
- `tools/formal/eprnf_boundary.tla`
- `tools/formal/ns3ub_channel.tla`
- `tools/formal/trace_refinement.py`

### 退出标准

- M1 safety 全通过
- M1/M2 在给定节点数/line 数/消息乱序边界下无死锁
- 所有已知竞态均有 counterexample-free 结果或明确例外说明

---

## 5. 仿真测试计划

## 5.1 测试层级

### L0：静态/结构检查

- `test_phase0_machineid.py`：EP-RNF MachineID 注入
- `test_phase1_tc3.py`：`shared_hint`/注册链路
- `M7SelfTest`：`DirEntry` 尺寸、无 data buffer 回归
- SLICC 结构检查：`pickSharerForSnoop`、`RegisterEPRNF_OnSharedHint`、`CompAck` 转移存在

### L1：单组件单元测试

新增建议：

- `tests/unit/test_resident_dir.py`
  - one-hot 约束
  - tombstone/remove/forceRemove
  - bloom insert/remove 饱和计数
- `tests/unit/test_ubcc_epoch_reqid.py`
  - stale reject
  - duplicate retry
  - tombstone replay
- `tests/unit/test_ubmsg_wire.py`
  - `UBMsgHeader` 位宽/flag 编码
  - serializer/deserializer 一致性
- `tests/unit/test_eprnf_snoop_matrix.py`
  - `SnpCleanInvalid` defer/fast-path
  - `SnpUnique` 矩阵
  - `SnpOnce` 数据来源

### L2：系统级定向回归

顺序：

1. `TC1`
2. `TC2`
3. `TC8`
4. `TC11`
5. `TC3/TC4/TC5/TC6/TC7/TC10/TC12/TC13/TC14/TC16`
6. `TC15/TC17/TC18-TC28`

### L3：故障注入回归

- 延迟抖动
- 乱序
- 重复包
- 丢包
- home UBCC 重启
- router 重启
- backstore 不可用/慢响应

### L4：迁移 A/B 对拍

- In-proc `UBRouter` vs out-of-proc Ns3UB
- 单节点、双节点、三节点
- 无故障/有故障两组
- trace 等价 + 最终内存等价 + committed metadata 等价

## 5.2 执行命令基线

### 构建

```bash
docker run --rm -v /mnt/data2/cgc/cc-ep:/workspace ubcc-dev:ubuntu20.04 \
  bash -c 'cd /workspace/gem5 && scons build/ARM/gem5.opt -j32'
```

### 单个 TC

```bash
docker run --rm -v /mnt/data2/cgc/cc-ep:/workspace ubcc-dev:ubuntu20.04 \
  bash -c 'cd /workspace && ./gem5/build/ARM/gem5.opt tests/e2e/test_e2e.py --tc=<N>'
```

### 全量 E2E

```bash
python3 tests/e2e/test_e2e.py --all
```

## 5.3 TC → 状态机边覆盖映射

| TC | 主要覆盖边 | 关键控制器 | 优先级 |
|---|---|---|---|
| TC1 `dsm_local` | `G_I→GRANT_HANDSHAKE→Clear→G_M/G_E` | UBCC, EPBackend | P0 |
| TC2 `remote_read` | `G_M→RECALL→GRANT_SHARED→G_S` | UBCC, EP-RNF, HN-F | P0 |
| TC3 `pingpong` | `G_M(A)↔G_M(B)` 反复迁移；`UD_RU/SC_RSC` | UBCC, HN-F | P1 |
| TC4 `three_node_ring` | 三节点 ownership 环迁移 | UBCC, Router | P1 |
| TC5 `single_writer` | 单写多读；重复 grant/clear | UBCC, tombstone | P1 |
| TC6 `multi_sharer` | `G_I→G_S(sharers>1)`；shared fanout | UBCC, EP-RNF | P1 |
| TC7 `writeback_evict` | `G_M→Writeback→G_E/G_I→ReadReq` | UBCC, EPBackend | P1 |
| TC8 `upgrade_invalidate` | `G_S→UPGRADE_PENDING→G_M`；`SnpCleanInvalid` defer | UBCC, EP-RNF, HN-F | P0 |
| TC9 `non_dsm_negative` | 地址拒绝路径 | EPBackend | P0(XFAIL/negative) |
| TC10 `concurrent_atomic` | 并发 unique/atomic 活性；无零值伪成功 | UBCC, HN-F | P1 |
| TC11 `local_upgrade` | `shared_hint` 注册 → local upgrade snoop chain | HN-F, EP-RNF, UBCC | P0 |
| TC12 `sync_barrier` | 多轮可见性/屏障单调性 | EPBackend, memory ordering | P1 |
| TC13 `release_acquire` | 双 line 顺序约束（flag/data） | UBCC, EPBackend | P1 |
| TC14 `multi_sharer_wave` | 读写波次串行化 | UBCC pending queue | P1 |
| TC15 `credit_storm` | `RetryAck/PCrdGrant` 前进性 | HN-F, NoC | P2 |
| TC16 `dual_upgrade_race` | 两 sharer 同时升级；`UPGRADE_PENDING` 仲裁 | UBCC, EP-RNF | P0 |
| TC17 `writeback_dma` | writeback 与 DMA/remote-read 重叠 | EP-SNF, UBCC | P2 |
| TC18 `directory_fill_replay` | ResidentDir fill/backstore replay | ResidentDir, UBCC | P0 |
| TC19 `directory_dirty_persist` | dirty committed metadata 持久化 | UBCC, backstore | P0 |
| TC20 `offload_smoke_a` | 迁移前 smoke A | UBAdapter, UBRouter | P2 |
| TC21 `offload_smoke_b` | 迁移前 smoke B | UBAdapter, UBRouter | P2 |
| TC22 `resident_capacity_pressure` | victim 选择、resident→backstore | ResidentDir | P0 |
| TC23 `bloom_false_positive_fallback` | BF 假阳性→回退 miss/refill | ResidentDir, backstore | P0 |
| TC24 `multinode_pressure_stress` | 多节点并发压力收敛 | UBCC, Router | P2 |
| TC25 `invalidate_clear_cycle` | 高频 `INVALIDATE→Clear→tombstone replay` | UBCC | P0 |
| TC26 `l3_eviction_writeback_chain` | L3 驱逐后 writeback 链 | HN-F, EP-SNF, UBCC | P2 |
| TC27 `epoch_wrap_stress` | epoch wrap + stale reject | UBCC epoch logic | P0 |
| TC28 `backstore_metadata_consistency` | resident/backstore 镜像一致性 | UBCC, EPBackend | P0 |

## 5.4 边覆盖验收标准

对每个 TC，至少记录：

- 命中的 `controller/state/event→next_state`
- `epoch`, `reqId`, `homeNode`, `targetMask`, `ackMask`
- 是否经过 `GRANT_HANDSHAKE`
- 是否命中 `tombstone replay`
- 是否触发 `pendingOwnerUpdate`
- 是否使用 `RecallBuffer` / `HomeMemory` / `NoData`

要求：

1. 每个 P0 TC 至少命中 1 条“提交边”。
2. `TC8/TC11/TC16/TC25/TC27` 必须命中竞态/恢复相关边。
3. 全部 TC 合并后，四类 `OpType` 都必须被命中。

---

## 6. RAS 方案

## 6.1 故障模型

| 故障 | 是否纳入 | 处理原则 |
|---|---|---|
| UBMsg 乱序 | 是 | 依赖 `epoch+reqId`、幂等 ack、最终重试 |
| UBMsg 重复 | 是 | tombstone / duplicate retry / ack 去重 |
| UBMsg 丢失 | 是 | timer + retry |
| UBCC 进程崩溃 | 是 | committed metadata 恢复，outstanding 丢弃 |
| UBRouter 进程崩溃 | 是 | 链路重建 + seq gap 观测 + 上层重试 |
| gem5/EPBackend 崩溃 | 是 | requester 侧重新发起，home 以 committed state 为准 |
| backstore 不可用 | 是 | 降级为 busy/retry，不得伪造 grant |
| 双 home 脑裂 | 否（架构禁止） | 由单 home 权威设计排除 |

## 6.2 检测项（必须打点）

### 每 line

- `committed_epoch`
- `reserved_epoch`
- `reqId`
- `opType/opStage`
- `targetMask/ackMask`
- `grantVisibleTick/sentinelVisibleTick`
- `pendingOwnerUpdate age`
- `tombstone hits/misses`

### 全局计数器

- `staleRejectedCount`
- `duplicateMsgCount`
- `retryCount`
- `timeoutCount`
- `persistentBusyCount`
- `backstoreRead/Write/Delete count`
- `orphanOutstandingCount`
- `upgradeDeferredCount`

## 6.3 分级恢复策略

### Level A：Committed metadata

- 持久化对象：`mesi`, `sharersMask`, `epoch`
- 语义：唯一可信恢复源
- 恢复后：直接作为 home authority 装载

### Level B：Tombstone（软持久化）

- 对象：`(linePa, epoch, reqId, accepted, expireTick)`
- 目的：重复 `Clear` 的幂等重放
- 允许丢失：是
- 丢失后影响：可能把重复 `Clear` 当 stale 拒绝，但不破坏安全性

### Level C：Outstanding（不持久化）

- 对象：全部 `OutstandingRequest`
- 崩溃后处理：全部丢弃
- 安全兜底：
  - 任何迟到 `RecallResp/InvalidateAck/Clear/UpgradeDone` 先过 `epoch+reqId` 校验
  - 校验不通过则拒绝
  - requester 超时后必须重试

## 6.4 故障处置矩阵

| 场景 | 风险 | 检测 | 恢复动作 | 验收 TC |
|---|---|---|---|---|
| GRANT 发出后 home 崩溃，`Clear` 未到 | grant 已可见但未 commit | outstanding 缺失、late Clear | 拒绝旧 Clear；requester 重试；重新授权 | TC25, TC27 |
| invalidation 已发出一半时崩溃 | sharer 集合不一致 | ackMask 不完整 | 用 committed state 重启；重试请求重新发 invalidate | TC8, TC16 |
| tombstone 丢失 | duplicate Clear 误判 | tombstone miss | stale reject + requester 重试，不可错误提交 | TC25 |
| backstore 写失败 | metadata 镜像落后 | I/O 错误计数 | 标记 busy/retry；禁止宣称持久成功 | TC19, TC28 |
| router 丢包/乱序 | ack/grant 次序颠倒 | seq gap + timeout | 上层 retry；不得依赖 FIFO | TC15, TC24 |

## 6.5 服务化观测与告警

未来多进程部署必须提供：

- `/metrics`：上述计数器
- `/dump/line/<pa>`：打印 resident/backstore/outstanding/tombstone
- `/dump/queue`：router per-pair FIFO 深度
- `/trace/recent`：最近 N 条 UBMsg JSON

告警阈值：

- `pendingOwnerUpdate age > 5x interconnectLatency`
- `orphanOutstandingCount > 0`
- `staleRejectedCount` 短时间暴涨
- `tombstone miss after duplicate clear` 非零

---

## 7. 迁移正确性（UBCC/UBRouter → Ns3UB 独立进程）

## 7.1 必须保持不变的语义

1. `UBMsg` wire format 完全保留。
2. `epoch/reqId` 比较规则不变。
3. `homeLinePa` 始终是权威地址视角。
4. `localLinePa` 仅作本地 sideband，不能替代 `homeLinePa`。
5. `seqNum` 仅用于观测/诊断，不参与协议判定。
6. 协议不得依赖单进程同步调用语义。

## 7.2 迁移阶段

| 阶段 | 架构 | 目标 |
|---|---|---|
| M0 | 当前 in-proc `UBRouter` | 建立黄金 trace |
| M1 | `UBRouter` 独立进程，仍本地 loopback | 验证 wire/序列化 |
| M2 | `UBCC+UBRouter` 独立进程，经 Ns3UB | 验证功能等价 |
| M3 | Ns3UB 开启 reorder/dup/loss 注入 | 验证恢复能力 |

## 7.3 A/B 对拍要求

同一 workload、同一 seed、同一 fault profile 下，对比：

- 最终内存值
- 最终 committed metadata
- 每条 line 的最终 epoch
- 每类消息计数
- 每个 TC 的 state-edge 覆盖集合

允许差异：

- 具体 readyTick
- retry 次数
- 中间 trace 次序

不允许差异：

- 最终 committed state
- 最终数据值
- 是否接受/拒绝某次 `Clear/UpgradeDone`

## 7.4 迁移专项测试

新增建议：

- `tests/migration/test_ubmsg_roundtrip.py`
- `tests/migration/test_ns3ub_fault_profile.py`
- `tests/migration/test_ab_trace_refinement.py`
- `tests/migration/test_process_restart_recovery.py`

---

## 8. 工具链与自动化

## 8.1 追踪格式

统一 JSON trace：

```json
{
  "tick": 0,
  "node": 0,
  "controller": "UBCC",
  "linePa": "0x...",
  "event": "InvalidateAck",
  "state_before": "WAITING_ALL_ACKS",
  "state_after": "DONE",
  "epoch": 12,
  "reqId": 99,
  "homeNode": 2,
  "targetMask": "0x6",
  "ackMask": "0x6"
}
```

## 8.2 必备脚本

- `tools/trace/collect_protocol_trace.py`
- `tools/trace/extract_state_edges.py`
- `tools/trace/compare_ab_runs.py`
- `tools/fault/ubmsg_fault_injector.py`
- `tools/recovery/replay_after_crash.py`
- `tools/formal/gen_tla_constants.py`

## 8.3 CI 分层

| CI 层 | 内容 |
|---|---|
| CI-0 | build + L0 结构检查 |
| CI-1 | P0 TC: 1,2,8,11,16,18,19,22,23,25,27,28 |
| CI-2 | P1 TC: 3,4,5,6,7,10,12,13,14 |
| CI-3 | P2 TC: 15,17,20,21,24,26 |
| CI-4 | fault injection nightly |
| CI-5 | migration A/B weekly |

---

## 9. 已知高风险点与专项验收

| 风险 | 必须检查 |
|---|---|
| `TBEStorage` 相关竞态 | `CompAck`/`CompData` 不得在未分配完成时消费错误路径 |
| `pendingOwnerUpdate` 生命周期 | 只允许 home UBCC clear；超龄必须报警 |
| `GRANT_HANDSHAKE` 泄漏 | live outstanding 最终必须转 `DONE/tombstone` 或 `TIMED_OUT` |
| `SnpUnique` 响应类型漂移 | 必须与黄金矩阵对齐 |
| `SnpOnce` 零填充伪成功 | 必须证明数据源权威 |
| DCT fallback 不完整 | EP-RNF 绝不成为 Fwd target |
| backstore 镜像错误 | 只持久化 committed metadata |
| epoch wrap | half-range 比较必须通过 TC27 + M1 formal |

---

## 10. 最终验收标准

系统视为验证完成，需同时满足：

1. M1/M2 形式化模型通过。
2. `TC1-TC28` 全通过（TC9 为负测，按预期拒绝即算通过）。
3. P0 全部进入每次 PR 的必跑回归。
4. 故障注入在 reorder/dup/loss 下保持 safety，不出现 silent corruption。
5. `UBCC/UBRouter` 独立进程迁移的 A/B 对拍无最终状态差异。
6. 崩溃恢复验证满足“committed 持久、tombstone 软持久、outstanding 丢弃但安全”。

---

## 11. 建议的实施顺序

1. 先补 `trace + state-edge extractor`
2. 再建 `M1 UBCC-only` TLA+
3. 再补 P0 单元测试与 P0 E2E 门禁
4. 再建 `M2 EP-RNF boundary` TLA+
5. 最后做 Ns3UB A/B 与故障注入

这样可以先锁住**目录安全性**，再锁住**边界竞态**，最后再验证**网络迁移**。
