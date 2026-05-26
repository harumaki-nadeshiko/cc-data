# UBCC Detailed Phased Plan v0.1

说明: 本文件已被 `plan/00-plan-index.md` 引导的多文件计划集 supersede；保留作为第一轮单文件草稿。

状态: 第一轮整理稿，供后续多轮打磨

基线来源优先级:
1. `docs/summary-plan-progress.md`
2. `docs/multi-node-pa-layout.md`
3. `docs/basic-framework-prompt.md`
4. `docs/ubcc_agent_execution_guide.md`
5. `reports/basic-framework-no-bypass-fix-8.md`
6. `gem5_chi_ubcc_plan.md`
7. 当前仓库代码与测试结果

本文件目标:
1. 明确目前已经完成的工作。
2. 明确还必须完成的工作。
3. 明确用户已经提出且必须持续满足的要求。
4. 形成后续 Coding Agent 可直接执行的分阶段开发与验证计划。
5. 明确 External Proxy 的内部/外部状态、状态转换和请求转换。

## 1. 当前项目目标

在单个 gem5 `System` 内实现一个 `N=3, L=2, D=2` 的 Ruby CHI + UBCC 原型系统。

每个 node 固定包含:
- 2 个 cluster
- 每个 cluster 2 个 core
- 1 个本地 HN-F/L3
- 1 个 `L_SNF`
- 1 个 `DL_SNF`
- 1 个 `EP_SNF`
- 1 个 `EP_RNF`
- 1 个内部 `EPBackend`
- 1 个内部 `UBCCController`

本阶段不是做完整高性能协议，而是沿着可验证的路径，先把 correctness 主路径一阶段一阶段落地。

## 2. 用户已明确提出的要求

以下要求已经在文档和对话中反复出现，后续计划和实现都必须遵守。

### 2.1 架构与规模要求

- 主配置固定为 `N=3, L=2, D=2`，不得降级成更小规模冒充完成。
- `DSM Local` 与 `Local Private` 必须严格分开。
- `UbccExclusive` 第一版不映射给普通 CPU。
- ordinary CHI traffic 必须限制在 node 内。
- 跨 node 交互只能通过 EP/UBCC outer protocol。
- `EP_RNF` 是 sentinel correctness 主路径，不能把 home-side coherence 主逻辑塞给 `EP_SNF`。
- 第一版不实现独立 `UR_i`。
- metadata 第一版驻留在 UBCC 内部 map，不做 eviction/refill/backing-store protocol。

### 2.2 当前地址方案要求

- 当前代码基线已经采用 `per-node PA`，不再采用最初的全局统一 DSM PA。
- `DSM VA` 仍然要求在各 node 上保持统一窗口语义。
- EP 边界必须通过 `NodeAddressMap` 实现 `PA <-> (src_node, home_node, offset)` 的等价转换。
- 所有 trace/checker/fatal 必须带 `node_id` 或等价 domain 信息。

### 2.3 工作流与验证要求

- 开发、构建、测试在无网络 Docker 容器内完成。
- commit/push 在宿主机完成。
- 编译/测试并行度上限 `-j20`。
- 不允许 bypass、monkey patch、缩规模、print-only、恒真断言等伪验收手段。
- 每阶段完成后必须有真实 testcase 和回归结果支撑。
- 计划文档必须足够细，使其他 Coding Agent 可以照着逐阶段开发和验证。
- 计划需要多轮打磨；每一轮都要把仍不清楚的点转成问题向用户确认。

## 3. 当前已完成工作

以下内容已经有文档和代码事实支撑。

### 3.1 基础框架与拓扑

- 已完成 `N=3, L=2, D=2` 多节点拓扑构建骨架。
- 已实现每 node 的 `ClusterCHI_RNF`、`HNNodeWrapper`、`EPNodeWrapper`。
- 已实现 `create_ubcc_system()`，可组装:
  - 3 个 HN
  - 3 个 L_SNF
  - 3 个 DL_SNF
  - 3 个 EP_SNF
  - 3 个 EP_RNF
  - 6 个 cluster RN-F
  - 12 个 CPU sequencer
- 已验证 cluster 只下游到同 node HN。
- 已验证 HN 只下游到同 node `L_SNF/DL_SNF/EP_SNF`。

### 3.2 地址与隔离

- 已确定 `NODE_ADDR_SHIFT = 40` 的 per-node PA 布局。
- 已实现 Python 与 C++ 两侧 `NodeAddressMap`。
- 已实现 `phys_pool_id` 路由所需 gem5 `Process` 改造。
- 已完成 DSM 窗口的静态地址测试。
- 已完成 per-node PA 设计文档化。

### 3.3 EP skeleton

- `EP_RNF` 已能接收 HN snoop 并返回固定 `SnpResp_I`。
- `EP_SNF` 已能接收请求并返回 `RespSepData + CompData_I` fake data。
- `EPBackend` 已能执行地址守卫。
- `UBCCController` 已有最小 metadata/outer queue skeleton，但还不是协议实现。

### 3.4 无 bypass bring-up 修复

- `tests/phase2/test_ruby_create_system_n3l2d2.py` 已去掉 bypass。
- `L_SNF`/`DL_SNF` 已接入真实 `MemCtrl + DDR4_2400_8x8` backstore。
- `Ruby.py` 已兼容 `dir_cntrls == 0` 的协议侧 backstore 模式。
- `EP_SNF` 地址检查已放宽为 DSM 窗口校验，不再误拒 remote DSM proxy 访问。

### 3.5 当前已通过测试

已知稳定通过的验证基线:

| 测试 | 当前状态 | 覆盖内容 |
|---|---|---|
| `tests/phase1/test_pa_layout_mode.py` | `48/48 PASS` | per-node PA 布局与分类 |
| `tests/phase1/run_phase1_test.py` | `5/5 PASS` | DSM VA 映射与 SE 运行 |
| `tests/phase1/run_phase1_test_enhanced.py` | `12/12 PASS` | per-node PA、pool、DSM VA 增强验证 |
| `tests/phase2/verify_topo_objects.py` | `101/101 PASS` | 对象层 wiring 与 downstream 排他性 |
| `tests/phase2/test_ruby_create_system_n3l2d2.py` | `9/9 PASS` | no-bypass `Ruby.create_system` + DRAM backstore + `m5.instantiate()` |
| `tests/phase3/test_ep_instantiate.py` | `INSTANTIATE OK` | EP 控制器最小 Ruby instantiate |

## 4. 当前未完成工作

当前真正还没做完的，不是基础拓扑，而是 UBCC coherence 主协议。

### 4.1 明确未开始或只做了 skeleton 的部分

- `Sync_Wait(node_mask)` 自定义系统调用尚未实现。
- HN-F sentinel registration 尚未实现。
- `ExternalSharer`/`ExternalOwner`/`ExternalPending` 尚未进入真实 HN-F directory 语义。
- `EP_RNF` 还不能把 HN snoop 转成真实 UBCC 操作。
- `EP_SNF` 还没有真实 remote miss/fill/writeback 协议，只会 fake data。
- `UBCCController` 还没有 per-line global directory、global transaction、owner/sharer 处理。
- remote read/write/upgrade/invalidate/writeback/owner transfer 都还未闭环。
- `GrantS` read-sharing 恢复尚未实现。
- metadata 容量模型与 multi-gem5 外部 ABI 尚未实现。

### 4.2 仍有风险或需重新验证的部分

- `gem5/configs/example/ubcc/basic_framework_se.py` 不是当前主验收链的一部分，仍应视为待重新验证的示例脚本。
- `run_phase1_test.py` 仍偏向 Phase1 旧测试口径；后续是否保留为主测试，需要统一策略。
- DRAM capacity warning 目前不阻塞功能，但后续若做延迟/容量模型应明确处理。

## 5. 当前代码中的真实模块状态

### 5.1 拓扑层

- `CHI_ubcc_framework.py`
  - 已完成 node 级对象组装。
  - 已完成 `L_SNF`/`DL_SNF` backstore 绑定。
  - 已完成 `EP_RNF`/`EP_SNF` 接入。
  - 未完成协议行为注入。

### 5.2 EP backend 层

- `EPBackend`
  - 当前作用: 地址分类守卫 + 承载 `UBCCController` 实例。
  - 当前缺口: 没有 transaction table、没有 request context、没有 inner/outer message 编解码。

- `UBCCController`
  - 当前作用: 空 skeleton。
  - 当前缺口: 没有 global directory、没有 line state 机、没有 outgoing protocol message、没有 timeout/retry/epoch。

### 5.3 请求面当前状态

- `EP_RNF`
  - 当前只会把合法 DSM local 地址 snoop 回复为 `SnpResp_I`。
  - 还不会:
    - 持有 sentinel state
    - 转发到 UBCC
    - 等待 outer invalidate/recall 完成后再答复 HN
    - 作为本地 coherent local access agent 主动对本地 HN 发起访问

- `EP_SNF`
  - 当前只会对 DSM 地址回固定 fake data。
  - 还不会:
    - 解析 needed permission
    - 向 home UBCC 发 `GlobalReadShared/Unique`
    - 处理 writeback/evict
    - 把 global grant 映射回 requester HN 的本地状态

## 6. External Proxy 详细设计基线

本节是后续协议实现的核心约束。这里的 External Proxy 指 `EP_RNF + EP_SNF + EPBackend + UBCCController` 的组合，而不是单一 controller。

### 6.1 External Proxy 的内外边界

对内:
- 面向本 node 的 HN-F / local CHI domain。
- 接收来自 HN 的普通 CHI 请求和 snoop。
- 以本地 CHI 参与者身份存在于 directory 中。

对外:
- 面向其他 node 的 UBCC home/requester。
- 传递 global coherence request、grant、invalidate、writeback、ack、data。
- 外部协议不允许直接绕过 home UBCC 修改本地 CHI cache 状态。

### 6.2 External Proxy 的组件分工

| 组件 | 站位 | 职责 |
|---|---|---|
| `EP_RNF_i` | home-side / local-domain side | 作为 HN directory 中的 external sentinel；响应本地 HN snoop；代表外部世界对本地 CHI domain 执行 coherent local access |
| `EP_SNF_i` | requester-side data plane | 接收本 node HN 对 remote DSM 的 miss / writeback / evict；转成 UBCC outer request |
| `EPBackend_i` | node-local EP glue | 地址翻译、上下文管理、inner/outer request state machine、与 UBCCController 交互 |
| `UBCCController_i` | home for `DSM_i` | 维护 global directory，决定 sharer/owner/grant/recall/invalidation |

### 6.3 External Proxy 对外状态

这是其他 node 从 global directory 视角看到的状态。

| 状态 | 所在侧 | 语义 |
|---|---|---|
| `G_I` | home UBCC | 该 line 没有任何 remote sharer/owner |
| `G_S` | home UBCC | 至少一个 remote node clean shared |
| `G_M` | home UBCC | 存在唯一 global owner，可能 dirty |
| `G_BUSY` | home UBCC | 正在进行 global transaction，不允许并发完成冲突事务 |

补充字段:
- `sharers_mask`
- `owner_node`
- `dirty`
- `epoch`
- `pending_requestor`

### 6.4 External Proxy 对内状态

这是本 node HN-F directory 和本地 EP 视角必须表达的状态。

#### 6.4.1 Home-side sentinel 状态

| 状态 | HN-F 中的表达 | 语义 | 当前是否实现 |
|---|---|---|---|
| `None` | EP_RNF 不在 directory | 外部世界对该 line 无需本地感知 | 否 |
| `ExternalSharer` | EP_RNF 是 sharer | 外部 node 可能有 clean shared copy | 否 |
| `ExternalOwner` | EP_RNF 是 owner/unique holder | 外部 node 可能持有 E/M 或最新数据 | 否 |
| `ExternalPending` | EP_RNF transient/TBE | 正在等 UBCC/outer 完成 | 否 |

#### 6.4.2 Requester-side remote-line 状态

这是 requester node 对一条 remote DSM line 的 EP-side summary，不要求和 HN cache state 一字不差，但必须足够支撑协议。

| 状态 | 语义 |
|---|---|
| `R_I` | 本 node 对该 remote line 没有 global 权限 |
| `R_WAIT_DATA` | requester miss 已发出，等待 home grant/data |
| `R_S` | 本 node 持有 clean shared global 权限 |
| `R_M` | 本 node 持有唯一 owner/global modified 权限 |
| `R_WAIT_WB_ACK` | 已发 writeback/evict，等待 home ack |
| `R_WAIT_RECALL` | 正在响应 home 对 owner/sharer 的 recall/invalidate |

#### 6.4.3 EP 内部事务状态

这是 `EPBackend` 里应该拥有、但当前尚未实现的 transaction 级状态。

| 状态 | 入口 | 出口 |
|---|---|---|
| `TX_IDLE` | 无 in-flight 事务 | 可接受新事务 |
| `TX_WAIT_HN_RESP` | EP_RNF 已向本地 HN 发 local coherent 操作 | 等 HN snoop/data/ack |
| `TX_WAIT_REMOTE_ACK` | 已向 remote node 发送 outer request | 等 remote ack/data |
| `TX_WAIT_HOME_DECISION` | requester 侧 miss 已送达 home UBCC | 等 home grant/data |
| `TX_WAIT_COMPLETION` | 内外侧都已完成大部分动作 | 等最终收尾并清理 state |

### 6.5 External Proxy 的请求与状态转换

#### 6.5.1 Requester 侧: `EP_SNF` 把本地 HN miss 转成 outer request

输入:
- `ReadNoSnp`/后续 sideband permission 信息
- 请求地址是当前 node 视图下的 remote DSM PA

转换步骤:
1. `EP_SNF` 校验地址属于本 node DSM 窗口且 `home != local`。
2. 通过 `NodeAddressMap` 把 PA 转成 `(src_node, home_node, offset)`。
3. `EPBackend` 建立 requester transaction context。
4. 根据 sideband 或 debug 策略，生成:
   - `GlobalReadShared`
   - 或 `GlobalReadUnique`
   - 或 bring-up 阶段保守 `GrantM` 请求
5. 发送到 `home_node` 的 `UBCCController`。
6. home 返回 `grant + data` 后，EP 将其翻译成本地 CHI completion。
7. 若 grant 为 `S`，requester 侧登记 `R_S`，并在本 node 建立 `ExternalSharer` sentinel。
8. 若 grant 为 `M`，requester 侧登记 `R_M`，不能错误地把本 node `EP_RNF` 登成 `ExternalOwner`。

#### 6.5.2 Home 侧: `EP_RNF` 把 HN snoop 转成 global operation

输入:
- 本地 HN 因 sentinel 命中而向 `EP_RNF` 发出的 snoop

转换步骤:
1. `EP_RNF` 校验地址属于本 node `DSM_local`。
2. 读取 sentinel state:
   - `ExternalSharer`
   - `ExternalOwner`
   - `ExternalPending`
3. 把 HN snoop 语义翻译为 outer 请求:
   - 本地要 unique -> `GlobalInvalidateSharers`
   - 本地要读 owner data -> `GlobalRecallOwnerForRead`
   - 本地要写 owner data -> `GlobalRecallOwnerForWrite`
4. `EP_RNF` 不能立即回复 HN，必须等 UBCC 完成外侧事务。
5. UBCC 收到全部 ack/data 后，更新 global directory。
6. `EP_RNF` 再向本地 HN 返回最终 snoop response/data。

#### 6.5.3 Home 侧: UBCC 通过 `EP_RNF` 对本地 CHI domain 发起 coherent local access

适用场景:
- remote read 本 node dirty line
- remote unique 需要使本地 sharer/owner 失效或降级

转换步骤:
1. home UBCC 查询本 line 的 global directory。
2. 若本地 node 可能仍有最新数据或 sharer，需要对本地 HN 发起 coherent 操作。
3. UBCC 把 `(home_node, target_node=self, offset)` 转回本 node PA。
4. 通过 `EP_RNF` 对本地 HN 发起 `ReadShared/ReadUnique/Invalidate-like` 等价本地操作。
5. HN 再按本地 CHI 规则对真实 CPU cache 发 snoop。
6. 本地 HN 的 data/ack 由 `EP_RNF` 回送 UBCC。
7. UBCC 完成 remote transaction 后，决定是否保留或调整 sentinel state。

### 6.6 External Proxy 关键不变量

- `ExternalOwner` 不能与本地真实 CPU dirty owner 共存。
- `ExternalSharer` 可以与本地 clean sharer 共存。
- `ExternalPending` 期间，同 line 的冲突事务必须阻塞、排队或 retry，不能并行提交。
- sentinel registration 必须早于相关 CPU completion。
- sentinel removal 必须晚于 UBCC 确认外部状态清空。
- EP path 只允许 DSM 地址，禁止 LocalPrivate/UbccExclusive 误入。
- ordinary CHI 不能跨 node；所有跨 node 权限变化必须显式走 outer protocol。

## 7. 当前到后续的阶段划分

当前推荐总阶段:
- `T0`: `Sync_Wait(node_mask)` 自定义系统调用
- `M4`: Sentinel registration
- `M5`: DSM remote first miss bring-up
- `M6`: UBCC directory + EP_RNF local coherent access
- `M7`: Writeback / evict / owner transfer
- `M8`: GrantS + read-sharing recovery
- `M9`: Metadata model + multi-gem5 preparation

## 8. 分阶段执行总规则

每个阶段都必须满足:
1. 只改当阶段所需最小文件集合。
2. `scons build/ARM/gem5.opt -j20 PROTOCOL=CHI` 通过。
3. 当前阶段新增 testcase 全部通过。
4. 现有回归不降级。
5. 输出阶段报告，明确已实现与未实现边界。
6. reviewer 未通过前不得推进到下一阶段。

## 9. 详细分阶段计划

### 9.1 T0: `Sync_Wait(node_mask)` 自定义系统调用

#### 目标

提供一个 SE-mode 下的跨 node barrier，同步后续多节点 directed testcase，避免用不可靠的共享 DRAM 假装同步。

#### 必做代码项

- 在 ARM SE workload 注册自定义 syscall 号。
- 实现 `SyncWait` barrier 状态对象。
- barrier 状态挂到 `System` 或等价全局可见位置。
- 支持按 `node_mask` 区分 barrier 实例。
- 支持等待线程阻塞与统一释放。

#### 预期修改文件

- `gem5/src/arch/arm/linux/se_workload.cc`
- `gem5/src/sim/syscall_desc.hh`
- `gem5/src/sim/system.hh`
- 新增 `gem5/src/sim/sync_wait.hh`
- 新增 `gem5/src/sim/sync_wait.cc`
- 新增 `tests/sync_wait/` 下 workload 与测试脚本

#### TestCase 设计

- `TC-T0-1`: 单 barrier，3 个 node 都调用 `Sync_Wait(0b111)` 后统一释放。
- `TC-T0-2`: `node_mask=0b011` 与 `0b100` 两个 barrier 互不干扰。
- `TC-T0-3`: 同一 node 多线程进入 barrier 时计数正确。
- `TC-T0-4`: 未在 `node_mask` 中的 node 不参与 barrier，不被阻塞。
- `TC-T0-5`: barrier 可重复使用两轮，不残留 stale state。

#### 出口标准

- barrier testcase 全部通过。
- 后续 M4-M8 定向测试统一改用此 syscall 做阶段同步。

### 9.2 M4: Sentinel Registration

#### 目标

让 HN-F directory 能真实登记 `EP_RNF` sentinel，并在本地权限变化时把 `EP_RNF` 当作 external participant snoop。

#### 必做代码项

- 定义 `EP_RNF` synthetic MachineID 或等价 directory entry 表达。
- 提供 sentinel insert/update/remove API。
- 支持 `ExternalSharer`。
- 支持 `ExternalOwner`。
- 支持 `ExternalPending` 或等价 transient 阻塞状态。
- 禁止 LocalPrivate/UbccExclusive 地址登记 sentinel。

#### 预期修改文件

- `gem5/src/mem/ruby/protocol/chi/CHI-cache*.sm`
- `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.hh/.cc`
- `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh/.cc`
- 可能新增 `EP sentinel registry` 辅助代码
- 新增 `tests/phase4/`

#### TestCase 设计

- `TC-M4-1`: 手工登记 `ExternalSharer` 后，本地 `ReadUnique` 必须 snoop `EP_RNF`。
- `TC-M4-2`: 本地 `ReadShared` 命中 `ExternalSharer` 时，不应错误触发 owner recall。
- `TC-M4-3`: remote unique 模拟后，本地真实 CPU copy 被 invalidate，HN 记录 `ExternalOwner`。
- `TC-M4-4`: `ExternalOwner` 与本地 dirty owner 不得共存。
- `TC-M4-5`: 非 DSM 地址 sentinel registration 必须 fatal 或 testcase fail。
- `TC-M4-6`: remove sentinel 后，本地再次访问不应再 snoop `EP_RNF`。

#### 出口标准

- HN 可以把 `EP_RNF` 真实当作 sharer/owner 参与者。
- 本地权限变化可稳定触发对 `EP_RNF` 的 snoop。

### 9.3 M5: DSM Remote First Miss Bring-up

#### 目标

实现 remote DSM first miss 从 requester `EP_SNF` 到 home `UBCC` 的首条闭环路径。

#### 必做代码项

- `EP_SNF` 识别 remote DSM miss。
- `EPBackend` 建立 requester transaction context。
- `UBCCController` 支持最小 `GlobalRead*` 处理。
- bring-up 阶段允许保守 `GrantM` debug 模式。
- requester 侧依据 grant 更新最小状态。

#### 预期修改文件

- `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.hh/.cc`
- `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh/.cc`
- `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh/.cc`
- 新增 `tests/phase5/`

#### TestCase 设计

- `TC-M5-1`: Node0 读 Node1 `DSM_1`，requester miss 能收到 data。
- `TC-M5-2`: home UBCC 为首次 remote read 创建 directory entry。
- `TC-M5-3`: 保守 `GrantM` 模式下，requester 被记录为 owner。
- `TC-M5-4`: 不同 `home_node` 的同 offset 请求被正确路由到对应 home UBCC。
- `TC-M5-5`: LocalPrivate 地址不能走 `EP_SNF` outer request。
- `TC-M5-6`: 重复同 line 访问命中 requester 已有状态，不应重复发 full first-miss transaction。

#### 出口标准

- 单条 remote read miss 可稳定返回正确 data。
- requester/home 两侧最小 line state 一致。

### 9.4 M6: UBCC Directory + EP_RNF Local Coherent Access

#### 目标

让 home UBCC 能通过 `EP_RNF` 对本地 CHI domain 做真实 coherent read/downgrade/invalidate/dirty recall。

#### 必做代码项

- 实现 per-line `DirEntry`。
- 实现单 line active transaction 管理。
- 实现 home UBCC 经 `EP_RNF` 向本地 HN 发 local coherent access。
- `EP_RNF` 必须等待 UBCC 完成外侧事务后再回复 HN。
- remote read local dirty line 必须返回最新 data。

#### 预期修改文件

- `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh/.cc`
- `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.hh/.cc`
- `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh/.cc`
- 可能修改 HN SLICC 路径
- 新增 `tests/phase6/`

#### TestCase 设计

- `TC-M6-1`: Node0 先写 Node1 DSM line，Node2 再读，必须看到 Node0 最新值。
- `TC-M6-2`: home 发 recall 给 owner node 后，owner 通过 `EP_RNF` 从本地 HN 拿到最新 data。
- `TC-M6-3`: 本地 HN 对 `ExternalOwner` 发 snoop 时，`EP_RNF` 不能提前答复。
- `TC-M6-4`: home directory 中 `owner/sharers/dirty` 字段与实际事务结果一致。
- `TC-M6-5`: 同一 line 冲突事务必须串行，不能出现双 owner。
- `TC-M6-6`: timeout/retry 路径下不会留下永久 `ExternalPending` 卡死状态。

#### 出口标准

- remote read/write 和 home-side recall 首次形成 correctness 闭环。

### 9.5 M7: Writeback / Evict / Owner Transfer

#### 目标

补齐 remote dirty writeback、clean evict、owner transfer，完成三节点 ping-pong 主路径。

#### 必做代码项

- requester dirty writeback -> home UBCC。
- requester clean evict -> home sharer mask 更新。
- owner transfer 流程。
- stale response 防护，建议引入 `epoch`。

#### 预期修改文件

- `UBCCController.hh/.cc`
- `EPSNFController.hh/.cc`
- `EPBackend.hh/.cc`
- 新增 `tests/phase7/`

#### TestCase 设计

- `TC-M7-1`: owner dirty writeback 后，home 数据更新为最新值。
- `TC-M7-2`: clean evict 后，home sharer mask 清除对应 node。
- `TC-M7-3`: Node0 -> Node1 -> Node2 连续写同一 line，任意时刻最多一个 global owner。
- `TC-M7-4`: stale ack/data 使用旧 epoch 时必须被丢弃。
- `TC-M7-5`: owner transfer 期间本地 HN 不得看到错误的双 owner 状态。
- `TC-M7-6`: owner node 被 remote read 时，数据回传后状态从 `M` 正确降级。

#### 出口标准

- 三节点 ping-pong 正确，dirty data 不丢失。

### 9.6 M8: GrantS + Read-Sharing Recovery

#### 目标

恢复真正的 read-sharing，结束“所有 remote miss 都保守 GrantM”的 debug 形态。

#### 必做代码项

- 增加 HN -> `EP_SNF` 最小 sideband。
- sideband 至少能表达 `needed_perm=S/M`。
- `EP_SNF` 能发 `GlobalReadShared`。
- home UBCC 维护 sharer mask。
- requester 在 completion 前登记 `ExternalSharer`。
- local upgrade 通过 `EP_RNF` 触发 global invalidation。

#### 预期修改文件

- `EPSNFController.hh/.cc`
- HN SLICC 或 config sideband 接口
- `UBCCController.hh/.cc`
- `EPRNFController.hh/.cc`
- 新增 `tests/phase8/`

#### TestCase 设计

- `TC-M8-1`: Node0 与 Node2 同时读 Node1 DSM line，都获得 shared 语义。
- `TC-M8-2`: 之后 Node0 写该 line，Node2 必须被 invalidate。
- `TC-M8-3`: sharer mask 与真实 sharer 集合一致。
- `TC-M8-4`: `GrantS` 默认启用；保守 `GrantM` 仅在 debug flag 下启用。
- `TC-M8-5`: 本地 clean sharer + external sharer 共存时，local upgrade 路径正确。
- `TC-M8-6`: read-only workload 不再错误升级为 owner-only 流量。

#### 出口标准

- read-sharing correctness 成立。
- debug-only `GrantM` fallback 不再是默认主路径。

### 9.7 M9: Metadata Model + Multi-gem5 Preparation

#### 目标

在 correctness 稳定后，整理 metadata 容量建模与外部网络 ABI，为未来多 gem5 或 ns-3 准备。

#### 必做代码项

- 保持 C++ map 基线不破坏 correctness。
- 若引入容量模型，增加独立 metadata cache/model，而不是污染 correctness 主路径。
- 抽象 outer message ABI。
- 记录 fixed-latency / ns-3 / 多进程模式的时间假设。

#### 预期产出

- 代码或文档形式的 outer protocol ABI。
- metadata capacity/model 设计说明。
- 多 gem5 迁移 checklist。

#### TestCase 设计

- `TC-M9-1`: metadata 对普通 CPU 不可见。
- `TC-M9-2`: outer message 编解码字段完整且可自检。
- `TC-M9-3`: fixed latency backend 与抽象 ABI 层解耦。
- `TC-M9-4`: directory capacity pressure 下，correctness 不回退。
- `TC-M9-5`: 文档化的多 gem5 假设与当前单进程实现字段一致。

#### 出口标准

- 不影响 M4-M8 correctness。
- 外部网络迁移前置条件文档化完成。

## 10. 当前已有测试的重新定位

这些测试是后续每阶段都要保留的回归底座。

| 编号 | 测试 | 角色 |
|---|---|---|
| `TC1` | `tests/phase1/test_pa_layout_mode.py` | 地址布局静态守门 |
| `TC2` | `tests/phase1/run_phase1_test.py` | DSM VA/SE 基线 |
| `TC2E` | `tests/phase1/run_phase1_test_enhanced.py` | per-node PA 与 pool 增强基线 |
| `TC3` | `tests/phase2/verify_topo_objects.py` | 对象层 topology guardrail |
| `TC4` | `tests/phase2/test_ruby_create_system_n3l2d2.py` | no-bypass create_system + instantiate guardrail |
| `TC5` | `tests/phase3/test_ep_instantiate.py` | EP 最小 instantiate guardrail |

推荐约束:
- `TC1` 到 `TC5` 必须作为后续所有阶段的回归前置集。
- 新阶段 testcase 通过但 `TC1` 到 `TC5` 失败，视为该阶段未完成。

## 11. 其他 Coding Agent 的执行方式

其他 Coding Agent 接手时，应严格按以下顺序工作:
1. 先读本文件，再读对应阶段的代码与测试。
2. 只处理一个阶段，不抢跑后续阶段的机制。
3. 先写或更新 testcase，再补代码。
4. 构建 `gem5.opt`。
5. 跑本阶段 testcase。
6. 跑 `TC1` 到 `TC5` 回归。
7. 输出阶段报告，列出:
   - 代码修改文件
   - 新增/修改 testcase
   - 实际命令
   - 实际结果
   - 已知未覆盖边界

## 12. 第一轮仍待用户确认的问题

以下问题会直接影响计划书下一轮细化方向。

1. `T0 Sync_Wait` 是否确认为 `M4` 之前的强制前置阶段，还是只作为推荐增强项。
2. `run_phase1_test.py` 在后续计划中是否继续保留为主回归，还是由 `run_phase1_test_enhanced.py` 逐步取代。
3. home-side HN-F 改造你更偏向哪种策略:
   - 最小 SLICC 改造，显式插入 sentinel registration hook
   - 还是允许增加一个更清晰的 helper/sideband 结构，减少后续阶段的补丁复杂度
4. outer protocol 命名你是否希望从现在开始就固定成明确的消息集合，例如:
   - `GlobalReadShared`
   - `GlobalReadUnique`
   - `GlobalInvalidate`
   - `GlobalRecallOwner`
   - `GlobalWriteback`
   - `GlobalEvict`
5. 你是否希望本计划在下一轮就拆成多文件:
   - `plan/01-current-state.md`
   - `plan/02-external-proxy-spec.md`
   - `plan/03-phase-plan.md`
   - `plan/04-test-plan.md`
   还是继续先维护成一个总文件。
