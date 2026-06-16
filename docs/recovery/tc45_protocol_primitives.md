# CC-EP TC4/TC5 协议原语参考手册

> 生成: 2026-06-16
> 源码: gem5 子模块 @ c665e76a58 (Phase 1-5 最终版)
> 日志: `tmp_logs/tc4_debug.log`, `tmp_logs/tc5_debug.log`, `tmp_logs/tc4_snd.log`

本文档逐个解释 TC4（三节点环）和 TC5（并发写）测试中涉及的每个协议原语、请求、子请求、状态和消息。

---

## 1. Ruby/SLICC 层（HN-F, L1D, L2, EP_SNF）

### 1.1 CHI 请求类型

#### ReadNoSnp / ReadNoSnpSep
- **位置**: `EPSNFController::recvRequestMsg()` in `EPSNFController.cc`
- **语义**: CHI "非 Snoop 读"——不可缓存读，不在请求节点的本地层次结构中触发 snoop。HN-F 对属于 DSM 段的物理地址发送此请求到 EP_SNF。
- **作用**: 从 CHI 域到外部（UBCC）协议的网关。EP_SNF 从 CHIRequestMsg 中提取 `ubcc_needed_perm`（0=Shared, 1=Unique）和 `ubcc_write_intent` 侧带字段，映射为 `OuterReqType`，并调用 `EPBackend::handleRemoteMiss()`。
- **触发时机**: HN-F 检测到对 DSM PA 范围的缺失时，发送 ReadNoSnp 到 EP_SNF。这是 UBCC 第一次介入的时刻。

#### WriteNoSnp / WriteNoSnpPtl
- **位置**: `EPSNFController::recvRequestMsg()`, `EPSNFController.cc:144`
- **语义**: CHI "非 Snoop 写"——不可缓存写。EP_SNF 缓冲 `PendingWrite`（地址 → 请求者）并等待 NCBWrData。
- **作用**: 将写数据路由到 home 节点的 DDR4（非本地 DDR4）。NCBWrData 到达时，通过 `functionalAccess` 写入 home PA，然后向 HN-F 返回 `CompDBIDResp`。
- **触发时机**: HN-F 先发 WriteNoSnp；NCBWrData 随后到达（可能来自不同节点的 L2 写回）。`CompDBIDResp` 发回 HN-F。

#### ReadUnique
- **位置**: EP-RNF 发往 HN-F，用于写召回。`EPRNFController::startReadUnique()` 发送 `CHIRequestType_ReadUnique` 带 `EpProxyOp::RecallUnique`。
- **语义**: CHI "独占读"——获取线的独占访问权。HN-F 发送 `SnpUnique` 使现 owner 失效，收集脏数据，返回 `Comp_UC` + 数据。
- **作用**: 写召回路径（scheme_v4 §4.3.2）。owner 节点的 EP-RNF 取回脏数据并使本地副本失效，实现所有权转移。
- **触发时机**: `EPBackend::handleRecallRequest()` 触发，当 home UBCC 标记 `recallNeeded=true` 且召回类型为 `WriteRecall` 时。

#### ReadShared
- **位置**: EP-RNF 发往 HN-F，用于读召回。`EPRNFController::startReadShared()` 发送 `CHIRequestType_ReadShared`。
- **语义**: CHI "共享读"——以共享状态获取线。HN-F 发送 `SnpShared` 将 owner 从 UD 降级为 SC 并收集数据。
- **作用**: 读召回路径。owner 降级为 R_S，干净数据返回 home。
- **触发时机**: `EPBackend::handleRecallRequest()` 触发，当召回类型为 `ReadRecall` 时。

#### CleanUnique
- **位置**: EP-RNF 发往 HN-F，用于 sharer 失效。`EPRNFController::startCleanUnique()` 发送 `CHIRequestType_CleanUnique` 带 `EpProxyOp::InvalidateOnly`。
- **语义**: CHI "干净独占"——使 sharer 失效，获取干净的独占权。对 EP-RNF 代理流：`Comp_UC` 仅作为完成令牌返回（非所有权授予）。HN-F 特殊完成将 EP-RNF 擦除为 `I`。
- **作用**: Sharer 失效路径（§4.2.4）。当 home UBCC 需要使远程 sharer 失效时，通过 EP-RNF→HN-F→snoop L2 sharers→失效完成。
- **触发时机**: `EPBackend::handleInvalidationRequest()` 触发，当 `OuterInvalidateMsg` 到达时。

### 1.2 CHI 响应类型

#### CompAck
- **位置**: HN-F SLICC 转移。由 EP-RNF 通过 `EPRNFController::sendCompAck()` 生成。
- **语义**: 确认 CompData 已收到。标准 CHI RN-F 行为。
- **作用**: 关闭数据传输阶段；HN-F 退出 TBE。
- **触发时机**: EP-RNF 收到 CompData 响应的所有数据 beat 后。

#### CompData_SC（共享干净）
- **位置**: EP-SNF 或 HN-F 生成。EP-SNF 中 `neededPerm==0` → `CHIDataType_CompData_SC`。
- **语义**: 数据响应，指示共享干净状态。CHIDataMsg 上的 `m_m_shared_hint=true` 侧带触发 HN-F 中的 `RegisterEPRNF_OnSharedHint`。
- **作用**: 首次缺失共享授权 → HN-F 安装 SC 行并注册 EP-RNF 到 `dir_sharers`。

#### CompData_UC（独占干净）
- **位置**: EP-SNF 当 `neededPerm==1` 时生成。HN-F 为 CleanUnique 完成生成。
- **语义**: 独占干净状态的数据响应。对 EP-RNF 代理失效：仅完成令牌，非所有权授予。
- **作用**: 独占授权数据传递；HN-F 进入 UC/UD。

#### Comp_UC（无数据响应）
- **位置**: HN-F 向 EP-RNF 发送，在 `CleanUnique(InvalidateOnly)` 或 `ReadUnique(RecallUnique)` 完成后。
- **语义**: CHI 响应，指示事务完成（所有权变更或失效）。
- **作用**: 触发 EP-RNF 的 `finishChiTxn()`，调用回调（发送召回响应或失效确认）。

### 1.3 CHI Snoop 类型

#### SnpCleanInvalid
- **位置**: HN-F SLICC，用于 sharer 失效。`EPRNFController::handleSnpCleanInvalid()` 处理。
- **语义**: "Snoop, 干净失效"——请求使共享副本失效。非升级：立即 `SnpResp_I`。升级路径：必须等 `OuterUpgradeAck(true)` 才能回复。
- **作用**: 失效扇出，从 HN-F 到 sharer。对本地升级：触发 `OuterUpgradeReq` 序列（§5.5）。

#### SnpShared / SnpSharedFwd
- **位置**: HN-F 生成，用于共享数据恢复。EP-RNF 绝不能收到这些。
- **语义**: 转发 snoop：与请求者共享数据。`SnpSharedFwd` 由 `pickSharerForSnoop()` 选择，从 Fwd 上下文中排除 EP-RNF。
- **作用**: 对 EP-RNF 不适用；收到则 `fatal/panic`。

#### SnpUnique
- **位置**: HN-F 生成，用于所有权转移。`EPRNFController::handleSnpUnique()` 处理。
- **语义**: "Snoop, 独占"——使当前 owner 失效并收集脏数据。
- **作用**: 写召回路径：HN-F 使 L2 owner 失效，收集脏数据，返回 EP-RNF。

#### SnpOnce
- **位置**: HN-F 生成，用于非 DCT 数据取回。`EPRNFController::handleSnpOnce()` 处理。
- **语义**: "Snoop 单次"——读行并共享数据。阻塞式远程取回。
- **作用**: EP-RNF 为唯一 sharer 时的共享读召回路径。

### 1.4 SLICC 动作（状态机）

#### Initiate_Request
- **位置**: `CHI-cache-actions.sm`
- **语义**: 分配 TBE，将 HN-F 从稳态转为瞬态，分发 CHI 请求到下游。
- **作用**: 标准 CHI 请求发起。对 EP-RNF 请求，检查 `ep_proxy_op` 进行特殊完成路由。

#### Finalize_DeallocateRequest
- **位置**: `CHI-cache-actions.sm`
- **语义**: 退出 TBE，更新目录状态（`dir_sharers`, `dir_ownerExists`, `dataMaybeDirtyUpstream`），清除瞬态。
- **作用**: 完成 HN-F 事务。对 EP-RNF 代理操作：执行 `scrub_to_I`（从 sharers 清除 EP-RNF，设置 `state=I`）。

#### Receive_ReqResp
- **位置**: `CHI-cache-actions.sm`
- **语义**: 处理来自 SN-F 的传入 CompData/DBIDResp。触发 `UpdateDirState_FromReqResp`。如果 TBE 状态不一致则断言行。
- **作用**: 首次缺失填充路径。`shared_hint` 触发 `RegisterEPRNF_OnSharedHint`。

#### UpdateDirState_FromReqResp
- **位置**: `CHI-cache-actions.sm`
- **语义**: 根据响应类型更新 `dir_sharers`、`dir_ownerExists`、`dataMaybeDirtyUpstream`。**关键断言**（scheme_v4 §4.5.2 第 6 项）：responder==EP-RNF 绝不能提升为 owner。
- **作用**: 保持 HN-F 目录在填充/snoop 后的一致性。

### 1.5 Ruby 端口

#### RSPIN（响应输入端口）
- **位置**: `CHI-cache-ports.sm`
- **语义**: 传入 CHI 响应的消息缓冲区。HN-F 按顺序出队。
- **作用**: 从 RN-F/SN-F 接收 CompData、DBIDResp、SnpResp、ReadReceipt。

#### REQIN（请求输入端口）
- **位置**: EP-SNF 和 HN-F 消息缓冲区。
- **语义**: 传入 CHI 请求的消息缓冲区。EP-SNF 出队 ReadNoSnp/WriteNoSnp。
- **作用**: HN-F→EP-SNF 请求路由的入口点。

#### reqRdyPort
- **位置**: Ruby 消息缓冲区就绪信号。
- **语义**: 指示消息可以从 REQIN 出队。
- **作用**: 驱动 EP-SNF/HN-F 中的 `recvRequestMsg()`。

### 1.6 HN-F 状态值

| 状态 | 语义 | 涉及 EP |
|-------|-----------|-------------|
| `I` | 无效。无本地副本，无目录数据。 | ✅ 首次缺失入口点 |
| `SC` | 共享干净。多个共享副本，全部干净。 | ✅ EP-RNF 注册为 sharer |
| `UC` | 独占干净。单个干净 owner。 | ✅ RecallUnique 路径: `SnpUnique` 失效 |
| `UD` | 独占脏。单个脏 owner。 | ✅ WriteRecall 路径: 脏数据收集 |
| `SD` | 共享脏。有脏副本的共享（MESI 扩展）。 | ✅ ReadShared 召回: 降级 SD→SC |
| `RSC` | 瞬态: I→SC（等 CompData）。 | ✅ 首次缺失共享授权填充 |
| `RSD` | 瞬态: SC→SD（写升级挂起）。 | ⚪ |
| `UC_RU` | 瞬态: UC→...（ReadUnique 进行中）。 | ✅ 已添加 CompAck 转移 |
| `UD_RU` | 瞬态: UD→...（ReadUnique 进行中）。 | ✅ 已添加 CompAck 转移 |
| `SC_RSC` | 复合: SC + 等待 RSC。 | ✅ DCT fix: CompAck 转移 |

**目录元数据**:
- `dir_sharers`: Sharer 位掩码。EP-RNF 在首次缺失共享填充后初始化。
- `dir_ownerExists`: 当 state∈{UC,UD} 时为真。
- `dataMaybeDirtyUpstream`: 当任何副本可能为脏时为真。

---

## 2. EPBackend 层

### 2.1 handleRemoteMiss

- **位置**: `EPBackend.cc:261`
- **参数**:
  - `uint64_t line_pa` — 请求者本地视图中的 PA
  - `int neededPerm` — 0=Shared, 1=Unique
  - `bool writeIntent` — 对写意图独占为 true
  - `int& outHomeNode` — 输出：此 PA 的 home 节点
- **返回**: `int` — 授权结果代码（-1=BUSY, 否则为 OuterGrantType）
- **语义**: 完整的远程缺失分发管道。将 PA 翻译为 home PA，构建 `OuterReqEnvelope(epoch, reqId)`，通过 `processOuterRequest()` 路由到 home UBCC。处理召回路由（构建 `OuterRecallMsg`，路由到 owner 的 EPBackend）。处理失效扇出（对挂起掩码中的每个 sharer，发送 `OuterInvalidateMsg`）。通过 `populateGrantData()` 填充授权数据，发送 `Clear` 提交。
- **为何存在**: 所有远程缺失处理的单一入口点。将 CHI 侧带（neededPerm, writeIntent）桥接到外部协议。
- **触发时机**: 当 DSM PA 到达 ReadNoSnp 时，由 EP_SNF 调用。

### 2.2 外部授权类型与信封

#### OuterGrantEnvelope
- **位置**: `EPBackend.hh:153`
- **字段**: `linePa`, `grantType`, `homeNode`, `epoch`, `reqId`, `grantVisibleTick`, `sentinelVisibleTick`
- **语义**: 从 home UBCC 到请求者的线格式授权消息。
- **作用**: 日志和未来网络迁移。

#### GrantDataSource（F3）
- **位置**: `EPBackend.hh:286`
- **取值**: `HomeMemory`（DDR4 的干净数据）, `RecallBuffer`（召回的脏数据）, `NoData`（未初始化内存的零填充）
- **语义**: 授权数据填充的形式化数据源。替代旧的 `functionalAccess` + `first_word != 0` 启发式。
- **作用**: `populateGrantData()` 用于选择正确的数据路径。

#### PendingGrantTxn（upgrade_invalidate_fix D5）
- **位置**: `EPBackend.hh:704`
- **字段**: `valid`, `linePa`, `homeNode`, `baseEpoch`, `reqId`, `grantType`
- **语义**: 独立的授权元组上下文，用于 Clear 重放正确性。防止 `sendClear` 在重试覆盖 `entry.epoch` 后使用过期 epoch。
- **作用**: `sendClear()` 优先使用 `_pendingGrantTxns[linePa].baseEpoch` 而非调用者提供的 epoch。

### 2.3 sendClear

- **位置**: `EPBackend.cc:1548`
- **参数**: `linePa`, `homeNode`, `epoch`, `reqId`
- **语义**: 向 home UBCC 发送 `OuterClearMsg` 以提交 GRANT_HANDSHAKE。首先检查 `PendingGrantTxn` 获取正确的 `baseEpoch` 以避免 epoch 重定基问题。如果 outstanding 已被消费则软跳过（无 baseEpoch → 已被其他请求者的 Clear 提交）。
- **为何存在**: 普通缺失授权的提交点（§3.3）。直到 Clear 被接受，意图的 DirEntry 才被提交。
- **触发时机**: 每次成功授权的 `handleRemoteMiss()` 末尾调用。与授权在同一 tick 同步调用。

### 2.4 processOuterRequest（EPBackend → UBCC）

- **位置**: `EPBackend::handleRemoteMiss():431` 调用，`homeUbcc->processOuterRequest()`
- **参数**: `homePa`, `ubccReq`, `writeIntent`, `_nodeId`, `entry.epoch`, `reqIdVal` 及召回/失效状态的输出指针
- **语义**: 委托给 UBCC 控制器。UBCC 返回：
  - 成功时返回授权类型
  - BUSY 时返回 `-1` enum（线忙、召回挂起、队列满）
  - 需要召回时设置 `recallNeeded=true` 和 `recallOwnerNode`
- **作用**: 核心仲裁和授权决策。UBCC 创建 OutstandingRequest 但**不修改**已提交的 DirEntry。

### 2.5 召回路径

#### handleRecallRequest
- **位置**: `EPBackend.cc:890`
- **语义**: 在 owner 节点的 EPBackend 上调用。验证本节点是召回目标。通过 EP-RNF 发起 CHI 召回：读召回用 `ReadShared`，写召回用 `ReadUnique`。异步回调返回 `OuterRecallResponse` 到 home UBCC。
- **作用**: 通过 HN-F 的正确召回路径（无 functionalRead 旁路）。

#### sendRecallResponse
- **位置**: `EPBackend.cc:1001`
- **语义**: 通过 `HomeMemoryService::write()` 将数据写入 home 节点 DDR4，然后路由 `processRecallResponse()` 到 home UBCC。
- **作用**: 完成召回→授权数据路径。

### 2.6 失效路径

#### handleInvalidationRequest
- **位置**: `EPBackend.cc:1247`
- **语义**: 在 sharer 节点的 EPBackend 上调用。通过 `EP-RNF.startCleanUnique(InvalidateOnly)` 经 HN-F 失效。异步回调返回 `OuterInvalidationAck` 到 home UBCC。
- **为何修复**: 先前的实现直接发送失效确认，绕过了 HN-F（§4.2.4）。

### 2.7 请求者簿记

- **位置**: `EPBackend.hh:252-269`（RequesterLineEntry）, `std::map<uint64_t, RequesterLineEntry> _requesterLines`
- **状态**: `R_I`（无权限）, `R_WAIT_GRANT`（缺失进行中）, `R_S`（共享）, `R_E`（干净独占）, `R_M`（脏修改）
- **作用**: 追踪请求者对远程 DSM 线持有的全局权限。由 `handleGrant()`、`handleRecallRequest()`、`handleInvalidationRequest()`、`handleWriteback()`、`handleEvict()` 更新。

---

## 3. UBCC 层

### 3.1 processOuterRequest（完整流程）

- **位置**: `UBCCController.cc:118`
- **完整流程**:
  1. **入口校验**: 检查 DSM 地址、验证 requestNode、拒绝 Shared+writeIntent。
  2. **Outstanding 检查**: 如果此 PA 已有 outstanding：
     - 同请求者有活跃 outstanding → BUSY (-1)
     - 同请求者有 replayArmed 授权 → 返回缓存授权（重试命中）
     - 不同请求者 → 入队 `_pendingRequesters` 或 dup_retry
  3. **Tombstone 检查**: 如果匹配 tombstone 存在 → 幂等授权。
  4. **分配 reservedEpoch**: `reservedEpoch = entry.epoch + 1`（尚未提交）。
  5. **状态依赖决策**（按 `entry.state` 分支）:
     - `G_I`: 直接 GRANT_HANDSHAKE（无需召回/失效）。
       - Shared: 意图 G_S, sharers+=req
       - Unique(无写): 意图 G_E, owner=req
       - Unique(写): 意图 G_M, owner=req
     - `G_S`:
       - Shared: 意图 G_S, sharers+=req（直接 GRANT_HANDSHAKE）
       - 已有 sharer 发 Unique: 推迟到 UPGRADE_PENDING，返回 BUSY
       - 非 sharer 发 Unique: 创建 INVALIDATE + GRANT_HANDSHAKE，返回 BUSY
     - `G_E` / `G_M`:
       - 如果 RECALL.DONE 已完成且同请求者: 转移到 GRANT_HANDSHAKE（F2）
       - 如果 RECALL.DONE 已完成但不同请求者: 新请求者入队
       - Owner 存在且非请求者: 发起 RECALL，返回 BUSY
       - 同 owner 或无 owner: 直接 GRANT_HANDSHAKE
  6. **返回**: 授权类型及 OutstandingRequest 中记录的意图状态。DirEntry **未修改**。
- **为何先预留再提交**: 已提交的 DirEntry 仅在 Clear 到达（或本地升级的 UpgradeDone）时变更。防止并发操作期间目录损坏。

### 3.2 OutstandingRequest 与生命周期

- **位置**: `UBCCController.hh:82-162`
- **关键字段**:
  - `linePa`, `baseEpoch`（校验基线）, `reservedEpoch`（待提交）, `reqId`
  - `opType`（RECALL/INVALIDATE/GRANT_HANDSHAKE/UPGRADE_PENDING）
  - `stage`（CREATED→WAITING_TARGET_RESP/WAITING_ALL_ACKS/WAITING_CLEAR/WAITING_LOCAL_DONE→DONE）
  - `intendedState`, `intendedOwnerNode`, `intendedSharersMask`, `intendedDirty`
  - `dataBuf[64]`, `dataValid` — 召回数据缓冲区
  - `dataSource` — 授权的 GrantDataSource
  - `recallBarrierDone`, `invalidateBarrierDone` — 屏障标记
  - `replayArmed` — 由 replay 创建时为 true（F24: 允许重试命中）
- **为何存在**: 将瞬态请求状态与持久目录状态分离（D-12）。

#### opType 取值
| opType | 含义 | 创建者 | 提交点 |
|--------|---------|------------|--------------|
| `RECALL` | Owner 召回进行中 | `processOuterRequest`（G_E/G_M 分支） | 匹配召回响应 → DONE |
| `INVALIDATE` | Sharer 失效扇出 | `processOuterRequest`（G_S + 非 sharer 发 unique） | 所有确认收到 → DONE |
| `GRANT_HANDSHAKE` | 授权等待 Clear | RECALL.DONE→转移, INVALIDATE.DONE→转移, 或 processOuterRequest 直接 | 匹配 Clear → DONE + tombstone |
| `UPGRADE_PENDING` | 本地升级四消息握手 | `processOuterUpgradeReq` | OuterUpgradeDone → DONE |

#### stage 取值（OpStage）
| Stage | 适用于 | 含义 |
|-------|-----------|---------|
| `CREATED` | 全部 | 刚刚创建，尚无响应 |
| `WAITING_TARGET_RESP` | RECALL | 等待 owner 召回响应 |
| `WAITING_ALL_ACKS` | INVALIDATE, UPGRADE_PENDING | 等待所有 sharer 确认 |
| `WAITING_LOCAL_DONE` | UPGRADE_PENDING | 等待 OuterUpgradeDone |
| `WAITING_CLEAR` | GRANT_HANDSHAKE | 等待匹配 Clear |
| `DONE` | 全部 | 终态：操作完成 |
| `CANCELLED` | UPGRADE_PENDING | 拒绝/校验失败 |
| `TIMED_OUT` | RECALL, INVALIDATE, GRANT_HANDSHAKE | 重试预算耗尽 |
| `PERSISTENT_BUSY` | UPGRADE_PENDING | 确认后不可撤销超时 |

#### OutstandingRequest API
- `findOutstanding(linePa)`: 返回此 PA 的 outstanding 指针，或 nullptr。
- `createOutstanding(linePa, opType, requesterNode, targetNode)`: 创建新条目（如已存在则失败）。
- `removeOutstanding(linePa)`: 擦除条目。

### 3.3 processClear

- **位置**: `UBCCController.cc:1548`
- **参数**: `line_pa`, `srcNode`, `epoch`, `reqId`
- **校验链**（全部必须通过）:
  1. **Tombstone 检查**: 如果 `(pa, epoch, reqId)` 匹配 tombstone → 返回缓存接受值。
  2. **GRANT_HANDSHAKE 存在**: 必须有 `opType==GRANT_HANDSHAKE` 的 outstanding。
  3. **Epoch 匹配**: `ost->baseEpoch == epoch`（过期失配 → retireToTombstone(false) + remove）。
  4. **ReqId 匹配**: `ost->reqId == reqId`。
  5. **请求者匹配**: `ost->requesterNode == srcNode`。
  6. **Stage 匹配**: `ost->stage == WAITING_CLEAR`。
- **成功时**: `commitIntendedResult()` 将意图状态/owner/sharers/epoch 写入 DirEntry。retireToTombstone(W)。调用 `replayPendingRequesters()`。
- **为何存在**: 所有普通缺失授权的提交点（§3.3, §3.5）。没有 Clear，意图目录结果永远不会被提交。

### 3.4 DirEntry（已提交目录状态）

- **位置**: `UBCCController.hh:501-518`
- **字段**:
  - `lineAddr`, `state`（G_I/G_S/G_E/G_M）, `sharersMask`, `ownerNode`, `dirty`
  - `epoch` — 已提交全局 epoch（单调递增）
  - `nextReqId` — 本地单调 reqId 分配器
- **禁止的字段**: `pendingOp`, `materializedData`, `pendingRequester`, `pendingRecallTarget` — 全部移至 OutstandingRequest。
- **作用**: PA 所有权的静止时单一真源。

#### MESIState 取值
| 状态 | 含义 | ownerNode | sharersMask | dirty |
|-------|---------|-----------|-------------|-------|
| `G_I` | 无效 | -1 | 0 | false |
| `G_S` | 共享 | -1 | sharer 位掩码 | false |
| `G_E` | 干净独占 owner | owner 节点 | 0 | false |
| `G_M` | 脏修改 owner | owner 节点 | 0 | truer |

### 3.5 PendingRequester 队列与 replay

- **位置**: `UBCCController.hh:188-198`, `std::map<uint64_t, std::deque<PendingRequester>> _pendingRequesters`
- **语义**: 在活跃 outstanding 存在时到达的外部请求者在此排队。在 Clear 提交时 replay。
- **队列行为**:
  - `enqueue`: 添加挂起请求者（每 PA 最多 4 个）。
  - `dup_retry`: 同一 (requester, reqId) 已排队 → BUSY。
  - `merge`: RS + 已有 RS → 跳过（重复共享）。
  - `drop_full`: 队列满 → 静默丢弃。
- **replayPendingRequesters**: 从 `processClear()` 和 `processOuterUpgradeDone()` 调用。出队队首，按新提交状态重定基 epoch，用 rebaseEpoch 调用 `processOuterRequest()`。如创建新活跃 outstanding → break（链式 replay）。标记授权 `replayArmed=true`。

### 3.6 Tombstone

- **位置**: `UBCCController.hh:168-180`, `std::map<uint64_t, std::deque<GrantHandshakeTombstone>> _tombstones`
- **字段**: `linePa`, `epoch`, `reqId`, `accepted`, `expireTick`（createTick + W）
- **语义**: GRANT_HANDSHAKE 到达 DONE 时转为 tombstone 而非保留为活跃 outstanding。窗口 W 内的重复 Clear 返回相同缓存的 ClearAck。
- **窗口 W**: 100000 ticks（可通过 `_tombstoneWindowW` 配置）。
- **清理**: `cleanupTombstones()` 在每次 `wakeup()` 调用——删除过期条目。

### 3.7 本地升级（UPGRADE_PENDING）路径

- **processOuterUpgradeReq**: `UBCCController.cc:1336`
  - 验证请求者是否已提交 sharer，无已有 outstanding。
  - 分配 reservedEpoch，冻结 targetMask（sharers 减去请求者）。
  - 若 targetMask != 0: stage=WAITING_ALL_ACKS（必须先失效）。
  - 若 targetMask == 0: stage=WAITING_LOCAL_DONE，立即 accepted=true。
  - DirEntry 未修改。

- **processOuterUpgradeDone**: `UBCCController.cc:1447`
  - 若 stage==WAITING_ALL_ACKS: 暂态——缓存 Done，返回 true（暂不提交）。
  - 若 stage==WAITING_LOCAL_DONE 且 accepted: 提交意图结果，retire。
  - 调用 `replayPendingRequesters()`。

### 3.8 Epoch 管理

- `allocateReservedEpoch(entry)`: 返回 `entry.epoch + 1`。此处不修改已提交 epoch。
- `isNewerEpoch(a, b)`: 半幅比较: `(a-b) & 0xFFFFFFFFFFFFFFFF < 1<<63`。
- `commitIntendedResult(entry, ost)`: 将 `ost.intendedState/intendedSharersMask/intendedOwnerNode/intendedDirty` 写入 entry，设置 `entry.epoch = ost.reservedEpoch`。

---

## 4. EP-RNF 层

### 4.1 CHI 操作

| PendingChiOp | EpProxyOp | CHI 请求 | 用途 |
|-------------|-----------|------------|----------|
| `ReadShared` | `NoProxyOp` | `ReadShared` | 读召回（降级 owner→共享） |
| `CleanUnique` | `InvalidateOnly` | `CleanUnique` | Sharer 失效 |
| `ReadUnique` | `RecallUnique` | `ReadUnique` | 写召回（失效 owner，收集脏数据） |

### 4.2 PendingChiTxn（每 PA 在途 CHI 事务）

- **位置**: `EPRNFController.hh:252-285`
- **关键字段**:
  - `linePa`, `epoch`, `reqId`
  - `op` — PendingChiOp::ReadShared/CleanUnique/ReadUnique
  - `proxyOp` — EpProxyOp::NoProxyOp/InvalidateOnly/RecallUnique
  - `hnfDest` — CompAck 路由的 HN-F MachineID
  - `beatsExpected` / `beatsReceived` — 多 beat CompData 追踪
  - `needsCompAck` — CompAck 是否仍需发送
  - `snoopSlotValid` — 1-entry 槽中是否有 HN-F snoop 排队
  - `onComplete` — CHI 事务完成时调用的回调

### 4.3 Snoop 处理

| Snoop | Handler | 响应 | 阻塞? |
|-------|---------|----------|-----------|
| `SnpCleanInvalid` | `handleSnpCleanInvalid()` | `SnpResp_I`（或升级路径推迟） | 仅升级路径 |
| `SnpUnique` | `handleSnpUnique()` | `SnpResp_I` / `SnpRespData_I_PD` | 是 |
| `SnpOnce` | `handleSnpOnce()` | `SnpRespData_SC` | 是 |
| `SnpShared` / `SnpSharedFwd` | 不可达 | `fatal/panic` | 是（终止） |
| `SnpOnceFwd` | 不可达（DCT fallback） | `fatal/panic` | 是（终止） |

### 4.4 UpgradePending（本地升级上下文）

- **位置**: `EPRNFController.hh:419-431`
- **字段**: `valid`, `linePa`, `homeNode`, `epoch`, `reqId`, `hnfDest`, `ackReceived`
- **语义**: 追踪通过 OuterUpgradeReq/Ack 握手进行本地升级的 PA。SnpResp_I 推迟到 `receiveUpgradeAck()` 调用后。

### 4.5 启动方法

- `startReadShared(linePa, onComplete)`: 向 HN-F 发送 ReadShared。
- `startCleanUnique(linePa, onComplete)`: 向 HN-F 发送 CleanUnique（InvalidateOnly）。
- `startReadUnique(linePa, onComplete)`: 向 HN-F 发送 ReadUnique（RecallUnique）。
- `receiveUpgradeAck(linePa)`: EPBackend 在 OuterUpgradeAck(true) 到达时调用。触发推迟的 SnpResp_I。

---

## 5. EP-SNF 层

### 5.1 recvRequestMsg 流程

1. **WriteNoSnp / WriteNoSnpPtl**: 缓冲 `PendingWrite`（地址→请求者），等待 NCBWrData。
2. **ReadNoSnp / ReadNoSnpSep**: 提取侧带（`ubcc_needed_perm`, `ubcc_write_intent`），分发 `handleRemoteMiss()`。
3. **若 BUSY**（grantResult < 0）: 入队 `_retryQueue`，下次 wakeup 重试。
4. **若数据未就绪**: 推迟到重试队列（除非 NoData 源）。
5. **构建 CompData**: 切块为数据通道大小消息，推迟 1 tick。

### 5.2 NCBWrData 路由

- 用 `NodeAddressMap::buildDsmPA()` 翻译本地 PA → home PA。
- 通过 functionalAccess 从 home DDR4 读当前行。
- 用传入数据覆盖掩码字节。
- 写回 home DDR4。
- 向挂起写请求者发送 `CompDBIDResp`。

### 5.3 推迟的 Grants / CompData

- `_deferredCompData`: 1-tick 推迟的 CompData 消息。
- `_deferredGrants`: 带 epoch/reqId 的推迟授权数据。

---

## 6. 屏障层（sync_wait）

### 6.1 sync_wait

- **位置**: `e2e_common.h`（workload 侧）
- **语义**: 基于 syscall 436 的跨节点同步屏障。gem5 SE 模式的 `SyncWaitManager` 按节点计数（非线程）。每个节点的多个 CPU 共享一个到达槽位。
- **作用**: 在跨节点操作之前同步，确保先前的操作全局可见。
- **触发时机**: 每次跨节点读写前后的测试代码中调用。

### 6.2 SyncWaitManager

- **位置**: `gem5/src/sim/sync_wait.cc`
- **关键字段**: `BarrierState::generation`, `BarrierState::arrivedNodes`, `BarrierState::suspendedThreads`, `BarrierState::threadGen`
- **语义**: 按 node_id 计数的屏障。popcount(node_mask) 个节点到达时释放所有线程。Generation 追踪防止跨代竞争。

---

## 7. TC4 请求链分析（三节点环）

TC4 测试: Node0 向 DSM_2(home=2) 写 0x1，Node1 向 DSM_2 写 0x2，Node2 读并验证。

### 7.1 预期行为（scheme_v4）

```
步骤 1: Node0 向 DSM_2(home=2) 写 0x1
  CPU_st → L1D → L2_N0 → HN-F_N0 → ReadUnique(ubcc_needed_perm=1, writeIntent=true)
    → EP_SNF_N0 → ReadNoSnp → EPBackend_N0.handleRemoteMiss(neededPerm=1, writeIntent=true)
    → UBCC_N2.processOuterRequest(G_I, GlobalReadUnique, writeIntent=true, requesterNode=0)
    → GRANT_HANDSHAKE: intendedState=G_M, ownerNode=0, reservedEpoch=1
    → grant G_M → handleGrant → R_M → populateGrantData(HomeMemory) → CompData_UC
    → sendClear(pa, homeNode=2, epoch=1, reqId=1)
    → UBCC_N2.processClear(pa, srcNode=0, epoch=1, reqId=1)
    → commitIntendedResult: G_M owner=0 epoch=1
    → retireToTombstone(W)
    日志: [UBCC-ORDER] pa=... epoch=1 op=ClearGrantHandshake requester=0 state=G_M  ✓

步骤 2: Node1 向 DSM_2(home=2) 写 0x2
  CPU_st → HN-F_N1 → EPBackend_N1.handleRemoteMiss(neededPerm=1, writeIntent=true)
    → UBCC_N2.processOuterRequest(G_M, unique, writeIntent=true, requesterNode=1)
    → 需要 RECALL（owner=0，与 requester=1 不同）
    → 发起召回: 创建 RECALL(WAITING_TARGET_RESP) + GRANT_HANDSHAKE
    → EPBackend_N0.handleRecallRequest() → EP-RNF_N0.startReadUnique() → HN-F_N0
    → HN-F 向 L2 owner 发送 SnpUnique，收集脏数据 (0x1)
    → EP-RNF_N0 finishChiTxn → sendRecallResponse(data=0x1)
    → UBCC_N2.processRecallResponse: RECALL→DONE, recallBarrierDone=true
    → Node1 重试时: 为 requester=1 创建 GRANT_HANDSHAKE
    → handleGrant → CompData_UC 带 data=0x1（来自 recall buffer）
    → sendClear(epoch=2, reqId=2)
    → UBCC_N2.processClear → commitIntendedResult: G_M owner=1 epoch=2
    日志: [UBCC-ORDER] pa=... epoch=2 op=ClearGrantHandshake requester=1 state=G_M  ✓

步骤 3: Node2 从 DSM_2(home=2) 读
  → 类似召回链: 召回 owner=1，获取数据=0x2，授权 R_S 给 Node2。
```

### 7.2 实际行为（来自 tc4_debug.log）

```
F3-DEBUG: populateGrantData node=0 homePA=0x20020000000 dataSource=0  (HomeMemory)
UBCC-RMOST: PA=0x20020000000 found=1 opType=2 stage=4 reqr=0 reqId=1 epoch=1 replayArmed=144
UBCC-ORDER: pa=0x20020000000 epoch=1 reqId=1 op=ClearGrantHandshake requester=0 state=G_M
  → 步骤 1 成功: Node0 已提交为 G_M owner。

F3-DEBUG: populateGrantData node=1 homePA=0x20020000000 dataSource=0  (HomeMemory)
UBCC-PCLEAR-DROP: PA=0x20020000000 ost=0x5651e2415368 opType=0
  → Node1 的 Clear 被丢弃: opType=0 表示 outstanding 是 RECALL，非 GRANT_HANDSHAKE。
     这意味着 Node1 的 Clear 在召回响应完成前到达。
     此时 GRANT_HANDSHAKE 尚未创建。

UBCC-QUEUE: pa=0x20020000000 action=enqueue requester=2 reqType=RU writeIntent=1 reqId=1 depth=1
  → Node2 的请求在 Node1 outstanding 后面排队（RECALL 活跃中）。

UBCC-QUEUE: pa=0x20020000000 action=dup_retry requester=2 reqType=RU ...（重复约 80 次）
  → Node2 重试但始终 dup_retry 因为它已经在队列中。
```

### 7.3 错误分析（TC4）

1. **应该发生**: Node1 的召回完成 → Node1 的 GRANT_HANDSHAKE 创建 → Node1 发送 Clear → 提交。然后 Node2 的排队请求被 replay。
2. **实际发生**: 日志显示 Node1 的 `UBCC-PCLEAR-DROP`——其 Clear 因 outstanding 为 RECALL（非 GRANT_HANDSHAKE）被丢弃。这意味着 `sendClear()` 在召回响应返回前被调用。Outstanding 为 RECALL(`WAITING_TARGET_RESP`)，非 GRANT_HANDSHAKE(`WAITING_CLEAR`)。
3. **偏离位置**: 日志中缺少 `[RECALL-DIAG]` 行——`handleRecallRequest()` 在 Node0 上未被调用。没有所有者发来的召回响应，RECALL 永远不转移到 GRANT_HANDSHAKE。
4. **根因**: 从 owner 来的召回响应从未被发送或处理。EPBackend Node0 上的召回请求触发了 ReadUnique，但 `SendRecallResponse`（将数据写回 home 的 DDR4 并通知 UBCC RecallDone）的完成回调从未执行。

---

## 8. TC5 请求链分析（并发写）

TC5 测试: 三个节点同时向 DSM_1(home=1) 写入不同值（0xAA000001, 0xBB000002, 0xCC000003），然后所有节点读取他们的最终值。

### 8.1 预期行为

```
步骤 1: Node1（本地 home）向 DSM_1(home=1) 写 0xBB000002
  → UBCC_N1.processOuterRequest(G_I, GlobalReadUnique, writeIntent=true, requesterNode=1)
  → 直接 GRANT_HANDSHAKE（home==requester）→ 意图 G_M, owner=1
  → 无需召回（G_I → 首次缺失）
  → Clear 接受 → G_M 已提交

步骤 2: Node0 向 DSM_1(home=1) 写 0xAA000001
  → UBCC_N1.processOuterRequest(G_M, GlobalReadUnique, writeIntent=true, requesterNode=0)
  → 需要 RECALL：owner=1（与 requester=0 不同）
  → 召回 Node1：通过 HN-F 取回脏数据 (0xBB000002)
  → 重试授予 Node0 G_M
  → Clear → 已提交 G_M owner=0

步骤 3: Node2 向 DSM_1(home=1) 写 0xCC000003
  → UBCC_N1.processOuterRequest(G_M, GlobalReadUnique, writeIntent=true, requesterNode=2)
  → 需要 RECALL：owner=0 → 召回 → 重试 → G_M owner=2
```

最终所有节点应该读回同一值（最后的写者，0xCC000003）。

### 8.2 实际行为（来自日志）

```
[UBCC-ORDER] pa=0x10018000000 epoch=1 reqId=1 op=ClearGrantHandshake requester=1 state=G_M
  → 步骤 1 成功: Node1（本地 home）提交为 G_M owner。

仅有一个 UBCC-ORDER 条目——Node0 和 Node2 从未向 home UBCC 发送 Clear。
后续日志显示：
  - Node0 向 0x18000000 写了它的值 → 数据存储在 Node1 的 DDR4 中
  - Node2 向 0x10018000000 写了它的值 → 数据存储在 Node2 的 DDR4 中
  - 所有三个节点读回自己的值
```

但是，**DSM store** 走的是本地缓存层次结构到 EP_SNF，写的是 **节点的本地视图**：
- Node0 的 DSM_1 本地 PA: `0x18000000`
- Node1 的 DSM_1 本地 PA: `0x10018000000`
- Node2 的 DSM_1 本地 PA: `0x20018000000`

只有 **ReadNoSnp**（来自 EP_SNF）通过 EPBackend 进入 UBCC。而 **WriteNoSnp**（写回）**不进入** UBCC——它直接在 EP_SNF 的 `recvDataMsg()` 中处理 NCBWrData，绕过整个 UBCC 层。

### 8.3 错误分析（TC5）

1. **应该发生**: 所有三个并发写通过在 home UBCC 的一次串行仲裁被排序。最终值由最终成功的写操作确定。
2. **实际发生**: Node1 提交了 G_M（本地 home）。Node0 的写经历了 ReadNoSnp → handleRemoteMiss → processOuterRequest，但从未完成（`populateGrantData` 调用后无 Clear）。Node2 类似。所有节点读回自己的本地缓存值。
3. **偏离位置**: 缺少第二个/第三个 UBCC ClearGrantHandshake 条目。写未完成外部授权周期——读从**本地缓存**返回，而非 home。
4. **根因**: WriteNoSnp 旁路 UBCC。每个节点本地写自己的 DSM 副本，不通过 home UBCC 串行化。仅 ReadNoSnp 触发 UBCC 请求，这意味着第一个读 miss 进入 UBCC，但写绕过它。

---

## 9. 关键协议原语速查表

| 原语 | 层 | 作用 |
|-----------|-------|---------|
| `ReadNoSnp` | CHI→EP-SNF | 从 CHI 到外部协议的网关 |
| `handleRemoteMiss` | EPBackend | 完整远程缺失管线（请求→授权→clear） |
| `processOuterRequest` | UBCC | 预留意图结果，创建 outstanding，触发召回/失效 |
| `OutstandingRequest` | UBCC | 瞬态请求状态（**未提交**） |
| `DirEntry` | UBCC | 已提交目录真值 |
| `processClear` | UBCC | 提交点：将意图结果写入 DirEntry |
| `sendClear` | EPBackend | 请求者侧 Clear 分发 |
| `replayPendingRequesters` | UBCC | 提交后 replay 排队请求者 |
| `handleRecallRequest` | EPBackend | Owner 侧召回，经 EP-RNF→HN-F |
| `handleInvalidationRequest` | EPBackend | Sharer 侧失效，经 EP-RNF→HN-F |
| `startReadShared` | EP-RNF | CHI ReadShared 到 HN-F |
| `startCleanUnique` | EP-RNF | CHI CleanUnique（InvalidateOnly）到 HN-F |
| `startReadUnique` | EP-RNF | CHI ReadUnique（RecallUnique）到 HN-F |
| `PendingChiTxn` | EP-RNF | 在途 CHI 事务上下文 |
| `PendingRequester` | UBCC | 排队的外部请求者 |
| `GrantHandshakeTombstone` | UBCC | 窗口 W 内的幂等 Clear 重放 |
| `sync_wait` | Workload | 跨节点屏障（syscall 436） |
