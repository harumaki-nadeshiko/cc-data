# CC-EP 多进程架构重构方案

## 0. 已冻结决策

- UBAdapter 作为极薄边界，只负责 transport，不承载协议判断。
- gem5 ↔ UBIOModule 全部改为纯异步消息驱动。
- MetaRNF 保留在 gem5 内，作为 CHI metadata I/O 代理。
- `CoherenceMessage` 先承载现有语义，单消息最大 512B。
- NetworkSim 分两阶段演进：Phase 1 固定延迟+保序，Phase 2 再引入乱序/故障钩子。
- ResidentDir 为进程级易失态；UBIOModule 崩溃即整次实验 fatal，不做恢复。
- PseudoMemPort 背压隐藏在端口内部缓冲；gem5 不感知 `WOULD_BLOCK`。
- gem5 侧严格事件驱动 `poll()`；外部进程主循环 `poll()`，不使用 gem5 阻塞 `recv()`。
- 配置采用双层：`launcher.py` 负责系统级编排，`gem5/configs/ruby/gem5_run.py` 负责单节点装配。
- transport 层不解析协议语义；`CoherenceMessage` 作为 `PseudoMemPacket.payload` 编解码对象。
- MetaRNF 路径采用严格提交：UBCC 必须等待 callback 才能 committed。
- 同一 `homePa` 采用双层串行化：UBIOModule 协议层排他，MetaRNF scoreboard 兜底。
- metadata pending 期间允许 query/read 旁路读 stable state，write/delete/recall 排队。

---

## 1. 进程拓扑图

```text
                                   +----------------------+
                                   |   PseudoManager      |
                                   |  topology/endpoints  |
                                   +----------+-----------+
                                              |
                                   control/config only
                                              |

  +--------------------------------+          |         +--------------------------------+
  |         gem5 进程 (Node i)     |                    |      UBIOModule 进程 (Node i)  |
  |--------------------------------|                    |---------------------------------|
  | CPU/L1/L2/HN-F/Ruby            |                    | UBIOModule                      |
  | EPRNFController                |                    |  - UBCCController               |
  | EPSNFController                |                    |  - ResidentDir                  |
  | EPBackend                      |<----callback------>|  - GroupIndex                   |
  | MetaRNFController              |                    |  - NodeAddressMap               |
  | UBAdapter                      |                    |                                 |
  |  - PseudoMemPort (gem5 side)   |====PseudoMemPacket=>| PseudoMemPort (gem5 side)      |
  |                                | <=payload=          |                                 |
  | MetaRNF --CHI--> HN-F          |   CoherenceMessage  | PseudoMemPort (network side)   |
  +--------------------------------+                    +==================+==============+
                                                                          ||
                                                                          || PseudoMemPacket
                                                                          || payload=CoherenceMessage
                                                                          \/
                                                        +-----------------------------------------+
                                                        |            NetworkSim 进程              |
                                                        |-----------------------------------------|
                                                        | Forwarder                               |
                                                        | Topology Table / ForwardTable           |
                                                        | Minimal timing model (Phase 1 保序)     |
                                                        | Future ns3 adapter replacement boundary  |
                                                        +-----------------------------------------+
```

### 拓扑说明

1. 每个 gem5 进程只保留本节点 Ruby/CHI 相关组件与 `UBAdapter` 边界。
2. 每个 UBIOModule 进程只承载跨节点一致性逻辑与目录状态，不直接访问 gem5 内部 CHI 网络。
3. MetaRNF 留在 gem5 内，通过内部 CHI 发 `ReadOnce/WriteUniqueFull` 到 HN-F，作为 metadata I/O 代理。
4. NetworkSim 只看 `PseudoMemPacket` 的 transport 头，不解析 `CoherenceMessage` 语义。

---

## 2. 目录结构

```text
父 repo/
  modules/
    ubiomodule/
      UBIOModule.{hh,cc}        ← 原 UBRouter，改名
      UBCCController.{hh,cc}     ← 从 gem5 迁移
      ResidentDir.{hh,cc}        ← 从 gem5 迁移
      BackstoreTypes.hh          ← 从 gem5 迁移
      BackstoreOrganization.hh   ← 从 gem5 迁移
      BackstoreSchemaA/B/C.{hh,cc}
      CoherenceMessage.hh        ← 原 UBMsg.hh，改名
      NodeAddressMap.{hh,cc}
      GroupIndex.{hh,cc}
      main.cc                    ← UBIOModule 进程入口
    networksim/
      NetworkSim.{hh,cc}
      ForwardTable.{hh,cc}
      main.cc
  framework/
    PseudoMemPort.{hh,cc}
    PseudoMemPacket.{hh,cc}
    PseudoManager.{hh,cc}
  thirdparty/
    zeromq/                      ← ZeroMQ 源码
  launcher.py                    ← 顶层启动器
  config/
    topology.json                ← 网络拓扑 + 节点映射

gem5/
  src/mem/ruby/protocol/chi/ep/
    UBAdapter.{hh,cc}            ← 保留，加 PseudoMemPort
    EPBackend.{hh,cc}            ← 保留，改为异步回调
    MetaRNFController.{hh,cc}    ← 保留（gem5 内 CHI 代理）
    EPRNFController.{hh,cc}      ← 保留
    EPSNFController.{hh,cc}      ← 保留
  configs/ruby/
    CHI_ubcc_framework.py        ← 去掉 UBRouter SimObject，加 PseudoMemPort 绑定
    gem5_run.py                  ← 单节点 gem5 入口（launcher 调用）
```

### 目录边界原则

- `modules/ubiomodule/`：协议与目录核心；不依赖 gem5 SimObject 生命周期。
- `framework/`：纯 transport 抽象；不理解一致性语义。
- `modules/networksim/`：最小网络转发器；以后可被 ns3 + adapter 替换。
- `gem5/.../ep/`：保留 CHI/Ruby 接入、回调桥、边界适配。

---

## 3. 改名映射表

| 原名 | 新名 | 位置 |
|------|------|------|
| UBRouter | UBIOModule | `modules/ubiomodule/` |
| UBMsg | CoherenceMessage | `modules/ubiomodule/` |
| UBMsgQueue | 废除（用 PseudoMemPort 替代） | — |
| UB_FLAG_* | 不变（封装在 CoherenceMessage 内） | — |

### 补充约束

- `UBRouter` 的“路由器”语义废除，新的 `UBIOModule` 是节点级 I/O + coherence boundary 容器。
- `CoherenceMessage` 保留现有 body 语义与 flag 语义，避免首轮改 transport 时连带改协议语义。

---

## 4. 关键接口设计

### 4.1 CoherenceMessage

- 最大 512B。
- 用作跨进程协议 payload。
- 需要显式 `serialize()` / `deserialize()`，禁止直接依赖进程内指针或布局偶然一致。

建议头部：

```text
type
src_node
dst_node
epoch
req_id
home_pa
local_pa
flags
seq_num
body_kind
body_len
```

body 采用 union/variant 承载现有 outer message 族：

- outer request
- grant/ack
- recall request/response
- writeback/evict
- metadata request/response
- control/error envelope

### 4.2 PseudoMemPacket

transport-only：

```text
type
src_id
dst_id
payload_len
payload[]
```

约束：

1. 不解析 payload 协议含义。
2. 只负责端点寻址、长度、基础收发。
3. 为未来 ZeroMQ / ns3 adapter 保留替换空间。

### 4.3 PseudoMemPort

接口语义：

- `send(packet)`：对上层看作成功入本地缓冲；背压由端口内部缓冲处理。
- `recv(packet)`：阻塞接口，仅允许外部进程使用，不进入 gem5 主事件线程。
- `poll()`：非阻塞，立即返回是否有待处理数据。

实现约束：

- gem5 侧只能 `poll()` + drain，不允许阻塞 `recv()`。
- UBIOModule/NetworkSim 主循环也可统一用 `poll()`，便于 Phase 1/2 行为一致。
- 端口内部需有有界缓冲与统计计数，便于 Phase 5 观察背压行为。

### 4.4 UBAdapter

职责固定为：

1. 持有 gem5 侧 `PseudoMemPort`。
2. 把 EPBackend/EPRNF/EPSNF 发起的外部操作编码成 `CoherenceMessage`。
3. 封装为 `PseudoMemPacket` 后发送。
4. 轮询收包、解包、按 `txn_id/req_id` 分发到 gem5 内回调。

明确不做：

- 不做协议仲裁。
- 不维护目录真值。
- 不做 MetaRNF 决策。

---

## 5. 核心时序与异步化改造

### 5.1 EPBackend 异步化

迁移前：

```text
EPBackend -> _ubcc->process...() -> 同步返回
```

迁移后：

```text
EPBackend
  -> UBAdapter 构造 CoherenceMessage
  -> PseudoMemPort::send()
  -> 立即返回

UBIOModule
  -> 收包
  -> UBCCController / ResidentDir / GroupIndex 处理
  -> 完成后回发 CoherenceMessage

gem5 / UBAdapter
  -> poll + recv
  -> 匹配 pending txn
  -> 回调 EPBackend 原完成逻辑
```

### 5.2 pending map

EPBackend 内新增 pending map：

- key：`txn_id` 或 `(req_id, home_pa)`
- value：请求类型、回调、期望返回类型、超时/调试信息

适用接口：

- `issueBackstoreRead()`
- `issueBackstoreWrite()`
- `issueBackstoreDelete()`
- `handleRemoteMiss()`
- 所有原本依赖同步 UBCC 返回的路径

### 5.3 MetaRNF 严格提交语义

该路径必须满足：

1. UBIOModule 对 metadata write/delete 发起请求后，只能把目录项置为 transient/pending。
2. 只有 gem5 内 MetaRNF completion callback 返回后，UBCC 才能把该操作转 committed stable state。
3. pending 期间：
   - query/read 可读取旧 stable state；
   - write/delete/recall 必须排队。

---

## 6. 关键 race 场景与缓解方案

### 6.1 Race A：MetaRNF I/O 完成前，UBCC 先对外暴露新 stable state

**风险**：目录已显示新 owner/sharer，但 metadata backstore 尚未落盘；若随后又来冲突请求，会基于假 committed 状态继续推进，导致目录/backstore 分叉。

**冻结决策**：严格提交。

**方案**：

1. UBCC 为对应 `homePa` 建立 pending record。
2. ResidentDir/control byte 增加 `metaWritePending/metaDeletePending` 概念位，或以 outstanding 记录承载同等语义。
3. MetaRNF callback 到达前，禁止把 pending 结果升格为 stable committed。
4. callback 成功后再提交；失败则直接 fatal。

### 6.2 Race B：同一 homePa 上多次 metadata 操作交错

**风险**：`grant -> writeback -> delete` 交错时，MetaRNF 自身 scoreboard 与 UBCC 外层 outstanding 若不一致，会出现先后关系颠倒。

**冻结决策**：双层串行化。

**方案**：

1. UBIOModule 协议层先对同一 `homePa` 做排他，形成逻辑串行顺序。
2. MetaRNF 继续保留每-PA scoreboard，作为执行层兜底。
3. 若两层状态观察到矛盾，直接断言失败，避免 silent corruption。

### 6.3 Race C：metadata pending 时到达新请求

**冻结决策**：读旁路，冲突排队。

**方案**：

- `query/read`：允许基于旧 stable state 响应，只做观察性读取，不推进破坏性状态转换。
- `write/delete/recall/invalidate-like conflicting ops`：进入 per-PA wait queue。
- MetaRNF callback 返回后，由 UBIOModule 统一 drain 队列。

### 6.4 进程崩溃语义

**冻结决策**：UBIOModule 或 NetworkSim 挂掉即整次实验 fatal。

**影响**：

- 不实现断连收口。
- 不引入 incarnation/generation 字段。
- launcher 只负责拉起实验，不负责在线恢复。

这能显著降低第一版协议复杂度，但要求 Phase 3/4 验证先以功能正确性为主，不把中途恢复纳入范围。

---

## 7. 逐步执行计划（Phase）

### Phase 1：建立 transport 骨架

**目标**：先把多进程 transport 边界做出来，但不触碰真实跨进程协议复杂度。

**工作项**：

1. 创建 `framework/` 与 `thirdparty/zeromq/` 目录。
2. 实现 `PseudoMemPacket`、`PseudoMemPort`、`PseudoManager`。
3. 第一版可先用本地队列/pipe/unix domain pipe 模拟，不要求真实网络。
4. 加基础统计：发送数、接收数、内部缓冲深度、丢弃/fatal 计数。

**验证方法**：

- 独立单元测试：packet 序列化/反序列化正确。
- 端口回环测试：send → poll → recv 路径正确。
- manager 拓扑测试：点对点、双工、多端口映射正确。

### Phase 2：先在 gem5 内完成改名与消息封装

**目标**：在单进程内先稳定名字、类型、接口，不立刻拆进程。

**工作项**：

1. `UBRouter -> UBIOModule` 改名。
2. `UBMsg -> CoherenceMessage` 改名。
3. `UBMsgQueue` 逐步废除，改成通过 `PseudoMemPort` 同进程回环/适配。
4. 保持逻辑仍可在单进程内运行，避免一次引入“改名 + 跨进程”双变量。

**验证方法**：

- 跑现有 28 个 E2E TCs，确保纯改名/封装无回归。
- 特别检查日志、断言、测试脚本中旧类型名引用是否已收敛。

### Phase 3：抽出 UBIOModule 进程，先验证单节点

**目标**：把 UBIOModule 真正迁出 gem5，但先不引入 NetworkSim 多跳复杂度。

**工作项**：

1. 把 `UBIOModule/UBCCController/ResidentDir/NodeAddressMap/Backstore*` 移到 `modules/ubiomodule/`。
2. gem5 内保留 `UBAdapter + EPBackend + MetaRNF + EPRNF/EPSNF`。
3. `EPBackend` 全面异步化；建立 pending map。
4. `UBAdapter` 通过本地 `PseudoMemPort` 与单独 UBIOModule 进程通信。
5. `CHI_ubcc_framework.py` 去掉 `UBRouter` SimObject 装配，改为注入端口/节点参数。
6. 引入 `gem5_run.py`，只负责单节点启动与参数消费。

**验证方法**：

- 先跑单节点 TC1。
- 再跑全部单节点/本地回环相关用例。
- 加一组专门检查 pending map 与 callback 配对的 directed tests。

### Phase 4：接入 NetworkSim，验证多节点拓扑

**目标**：替换原 UBIOModule↔UBIOModule 直连，形成最小独立网络进程。

**工作项**：

1. 实现 `modules/networksim/NetworkSim` 与 `ForwardTable`。
2. 支持读取 `config/topology.json`。
3. Phase 4.1：固定延迟 + 保序转发。
4. Phase 4.2：预留乱序/故障注入开关，但默认关闭。
5. `launcher.py` 统一拉起：一个 NetworkSim + N 个 UBIOModule + N 个 gem5。

**验证方法**：

- 多节点 TCs：`TC2-6, TC50-54`。
- 校验跨节点 request/grant/recall/writeback 时序。
- 对照旧单进程模型日志，确保协议顺序一致。

### Phase 5：替换为真实 ZeroMQ transport，多机部署

**目标**：把 Phase 1 的 pseudo transport 替换为 ZeroMQ 实现，同时保持 packet API 不变。

**工作项**：

1. `PseudoMemPort` 后端从本地队列切换到 ZeroMQ。
2. `PseudoManager` 对接真实 endpoint/route 管理。
3. 支持跨主机 endpoint 配置。
4. 保持 `CoherenceMessage` 和 `PseudoMemPacket` 线协议不变。

**验证方法**：

- 吞吐/延迟/队列深度统计。
- 对比 Phase 4 功能正确性结果。
- 压测背压行为与长事务稳定性。

---

## 8. gem5 配置与启动方式调整

### 8.1 `CHI_ubcc_framework.py`

调整方向：

1. 删除 `UBRouter`/`UBIOModule` 作为 gem5 SimObject 的创建逻辑。
2. 保留 `UBAdapter`、`EPBackend`、`MetaRNFController`、`EPRNFController`、`EPSNFController` 的节点内装配。
3. 为 `UBAdapter` 注入：
   - `node_id`
   - `socket_id`
   - `gem5-side endpoint`
   - `launcher` 下发的 transport 参数

### 8.2 `launcher.py`

职责：

1. 读取 `config/topology.json`。
2. 分别生成：
   - 每个 gem5 的节点参数
   - 每个 UBIOModule 的端口参数
   - NetworkSim 的拓扑参数
3. 按顺序启动：`PseudoManager`（若独立）、`NetworkSim`、`UBIOModule[xN]`、`gem5_run.py[xN]`。

---

## 9. 与未来 ns3 + adapter 对接的边界

替换原则：

1. **不改 `CoherenceMessage`**：继续作为 payload codec。
2. **不改 UBIOModule / gem5 协议层接口**：仍然只见 `PseudoMemPort` 抽象。
3. **只替换 NetworkSim/transport 实现**：
   - 当前：`PseudoMemPacket + NetworkSim`
   - 未来：`PseudoMemPacket + ns3 adapter`

因此当前就必须坚持：

- network 层不理解 coherence opcode；
- transport 层只理解 endpoint、route、delay、queue；
- 协议调试日志分为 packet 日志与 message 日志两层。

---

## 10. 各阶段验证矩阵

| Phase | 验证重点 | 最小验证 | 扩展验证 |
|------|----------|----------|----------|
| 1 | transport 抽象正确 | packet/port/manager UT | 多端口回环与拓扑映射 |
| 2 | 改名无回归 | 28 个 E2E TCs | 日志/脚本旧名清理 |
| 3 | gem5↔UBIOModule 异步桥 | 单节点 TC1 | 全单节点回归 + pending/callback 定向测试 |
| 4 | 多节点消息转发 | `TC2-6, TC50-54` | 对照单进程旧日志检查顺序 |
| 5 | 真实 ZeroMQ 与性能 | Phase 4 全回归 | 吞吐/延迟/背压对比 |

---

## 11. 实施优先级建议

建议严格按以下顺序推进，避免双重变量叠加：

1. 先做 `framework/` 骨架。
2. 再做 `UBRouter/UBMsg` 改名与 `CoherenceMessage` 封装。
3. 再做 `EPBackend` 异步化与 pending map。
4. 然后把 UBIOModule 真正迁出进程。
5. 最后再引入 NetworkSim 与真实 ZeroMQ。

这样每一阶段失败时，回归面都可局部化定位。
