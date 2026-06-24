# Port 层时间同步 + 异步事件驱动改造正式方案

## 1. 方案摘要

本方案将 CC-EP v4 的多进程传输层从“阻塞式/半同步轮询”升级为“Port 层时间同步 + 单槽 future message 缓存 + 事件驱动唤醒”的统一模型。核心思想是：以 `Port` 为最小时间同步单元，在每条 IPC 通道上维护独立的接收可见时间边界、sync 节拍和 future message 单槽缓存；以上层 `pollAllPorts() -> min(safeTs)` 形成模块级虚拟时间推进；以 `UBAdapter` transport mode 固定化方式在过渡期内隔离新旧路径（旧路径最终完全删除）；以 reqId 头字段、逐跳重分配和回程反向映射支撑 gem5 / UBIOModule / NetworkSim 三进程跨跳异步请求-响应闭环。该设计的哲学是：**不依赖外部 fd 唤醒，不引入全局事务 ID，不追求无限制乱序容忍，而是在“单调时间戳 + heartbeat 常驻 + 每跳重戳 + 稳定出队”前提下，用最小状态获得可证明的保守推进语义。**

## 2. 数据结构定义

### 2.1 `Port` 新增成员字段及语义

#### 已有字段延伸语义
- `_lastRxT: u64`
  - 最近一次被 Port 观测到的入站消息时间戳。
  - **sync 包也必须更新该值**。
  - 初值默认 `UINT64_MAX`；注释保留 fallback=`0` 作为诊断切换方案。

#### 新增字段
- `_pending: bool`
  - 当前 Port 是否缓存了一条来自未来时间的消息。
- `_pendingT: u64`
  - `_pendingMsg` 的可见时间。
- `_pendingMsg: MemMessage`
  - 单槽 future message 本体。
- `_lastSyncTs: u64`
  - 最近一次通过本 Port 发出的 sync/heartbeat 时间。
- `_syncWindow: u64`
  - 滑动时间窗口上界，用于约束本地时间不可无界前推。
- `_syncInterval: u64`
  - sync throttle 周期；在间隔内重复 `emitSync()` 直接返回成功，不重复发包。

#### `Port` 关键不变量
1. 同一 Port 上入站消息 `timestamp` 单调不减。
2. heartbeat/sync 常驻，静默端口也必须周期性发 sync。
3. 一旦 `_pending=true`，在 `_pendingMsg` 到期前 **禁止继续从 socket 拉取新消息**。
4. `receiveTimestamp()` 语义为“当前 Port 对上层暴露的最早接收边界”：
   - `_pending=true` 时返回 `_pendingT`
   - 否则返回 `_lastRxT`

### 2.2 `ReceiveStatus` 枚举更新

从：
- `kMessage`
- `kEmpty`
- `kSync`

更新为：
- `kMessage`：当前调用得到一条 `timestamp <= curT` 的可消费消息
- `kEmpty`：底层 socket 当前无消息
- `kSync`：收到 sync 控制消息（业务层通常跳过，但 Port 必须更新 `_lastRxT`）
- `kPendingFuture`：当前已知存在 future message，但其 `timestamp > curT`，当前不可消费

> 若实现上 `kSync` 仍在 `recv()` 内部被折叠处理，则必须保证上层行为与上述语义等价。

### 2.3 `UBAdapter` transport mode 枚举

- `LegacyInline`
  - 条件：`_port == nullptr`
  - 路径：`transportSend/transportRecv + _lastResponse`
  - 特征：存在 inline 同步回调，response 可在 `send` 调用链内到达

- `PortAsync`
  - 条件：`_port != nullptr`
  - 路径：`Port + processSyncAndReceive + handleResponse + _pendingRequests`
  - 特征：异步事件驱动，依赖 `safeTs` 调度

#### mode 约束
- mode 为 **实例级固定**
- 初始化后不得切换
- 两条路径互斥，并通过断言约束：
  - `LegacyInline` 不得走 `_pendingRequests` 完成路径
  - `PortAsync` 不得再消费 `_lastResponse`

### 2.4 `MemMessage header` 新增 `reqId`

- 字段位置：`MemMessage` 统一消息头（header）
- 字段类型：`u64`（推荐）
- 语义：
  - 每个模块内局部递增分配
  - 转发时可由中间节点重写
  - response 回程时通过反向映射恢复上一跳 reqId
- 设计要求：
  - 不放在 Coherence payload 中
  - NetworkSim / UBIOModule 可在不解析 payload 的情况下安全读写

### 2.5 `_pendingRequests` 键值结构

- key: `u64 reqId`
- value: `PendingTxn`
  - `PacketPtr pkt`
  - 可选补充字段：`Addr lineAddr`、`epoch/debugTag`、请求类型、发起 tick、统计字段

#### 生命周期
- `PortAsync` 路径：
  - 发送前插入
  - 若 `send()` 返回 false，表示**绝对未发送**，立即回滚删除
  - 正常 response 到达后删除
- `LegacyInline` 路径：
  - 不依赖 `_pendingRequests` 完成 response
  - 继续使用 `_lastResponse`

## 3. 每文件修改清单

### 3.1 `framework/Port.{hh,cc}`

#### 修改点摘要
- 将 `recv()` 升级为三态/四态可见性模型，支持 future message 单槽缓存
- 新增 `receiveTimestamp()` 与 `safeTs(curT)`
- 将 `emitSync()` 改为 heartbeat + throttle 语义
- 保留旧 `recvLowerBound()`/`synced_receive_lower_bound` 兼容壳，但内部转调 `safeTs()`

#### 新增/删除/修改接口
- 修改：`MemMessage* recv(u64 curT, ReceiveStatus* st)`
- 新增：`u64 receiveTimestamp() const`
- 新增：`u64 safeTs(u64 curT) const`
- 修改：`bool emitSync(u64 timestamp)`
- 修改：`u64 recvLowerBound()`/旧 lower-bound 接口，转为新语义包装

#### 关键不变量
1. `pending` 存在时禁止继续读 socket
2. sync 包更新 `_lastRxT`
3. `safeTs(curT) = min(receiveTimestamp(), lastSyncBound)`，其中 `lastSyncBound = (hasLastSync ? _lastSyncTs : curT) + _syncWindow`
4. 冷启动默认 `receiveTimestamp = UINT64_MAX`

### 3.2 `framework/MemMessage.hh`

#### 修改点摘要
- 在统一消息头中加入 `reqId`
- 明确消息头可被中间节点重写的字段集合

#### 新增/删除/修改接口
- 修改：`MemMessage::Header`
- 新增：`reqId` 字段访问/初始化规范

#### 关键不变量
1. reqId 必须在 header 中
2. 跨模块转发允许重分配
3. response 必须携带当前跳对应的 reqId 回程

### 3.3 `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.{hh,cc}`

#### 修改点摘要
- 引入 `transport_mode`，将旧路径与新路径显式分叉
- 新增 `_pendingRequests`
- 新增 `processSyncAndReceive()` 事件驱动推进逻辑
- 新路径 response 通过 `handleResponse(reqId)` 直达 pending map；旧路径继续用 `_lastResponse`

#### 新增/删除/修改接口
- 新增：`enum class TransportMode { LegacyInline, PortAsync }`
- 新增：`void processSyncAndReceive()`
- 新增：`template/成员 pollAbs(...)`
- 新增：`void handleResponse(MemMessage* msg)`
- 修改：`sendReadReq()` / `transportSend()` / `transportRecv()`
- 保留：`_lastResponse`、`_lastResponseValid`，但仅 `LegacyInline` 使用

#### 关键不变量
1. mode 实例级固定，运行中不得切换
2. `PortAsync` 路径：发送前插入 `_pendingRequests`
3. 若 `send()` 返回 false，立即回滚 `_pendingRequests`
4. `LegacyInline` 路径不得依赖 `_pendingRequests` 收包
5. `PortAsync` 路径不得再消费 `_lastResponse`

### 3.4 `gem5/src/mem/ruby/protocol/chi/ep/UBIOModule.{hh,cc}`

#### 修改点摘要
- **过渡期**保留旧路径同步调用链能力，作为 `LegacyInline` 的临时兼容基础
- 对旧路径影响点进行注释/断言整理，避免新旧语义混淆
- 明确 latency=0 时 response 可 inline 返回
- **目标：新异步路径全部验证通过后，删除旧同步调用链**

#### 新增/删除/修改接口
- 不强制新增主接口，但需补充/调整：
  - mode 相关判断或注释
  - 旧路径同步调用链文档化
  - 如有必要，暴露 minimal router/adapter mode 查询接口

#### 关键不变量
1. 旧路径同步执行语义不破坏新路径正确性（仅在过渡期有效）
2. `LegacyInline` 下 response 可在同一调用链内写入 `_lastResponse`
3. 旧路径与新路径在同一实例上不可混用

### 3.5 `tools/ubio/ubio_main.cc`

#### 修改点摘要
- 实现 `pollAllPorts()`，统一推进多个 Port
- 每轮对所有 Port 先 heartbeat/sync、再消费可见消息、最后以 `min(safeTs)` 推进本地时间

#### 新增/删除/修改接口
- 新增：`pollAllPorts()`
- 修改：主循环从不完整轮询改为 `pollAllPorts` 驱动

#### 关键不变量
1. 所有端口都执行 heartbeat，即使无业务流量
2. `lastTs` 只按 `min(safeTs)` 前推
3. 单端口 pending future 会自然成为该端口的时间下界

### 3.6 `tools/networksim/networksim_main.cc`

#### 修改点摘要
- NetworkSim 采用与 ubio 相同的 `pollAllPorts() -> min(safeTs)` 推进模型
- 出队时对消息**每跳重戳**为 NetworkSim 当前虚拟时间
- 参与 reqId 重分配与反向恢复

#### 新增/删除/修改接口
- 新增：`pollAllPorts()`
- 修改：延迟队列出队流程，加入重戳和 reqId 重分配
- 修改：response 回程路径，执行反向 reqId 映射

#### 关键不变量
1. 延迟队列按 `(deliver_time, enqueue_order)` 稳定出队
2. 出队重戳使用 NetworkSim 当前虚拟时间
3. NetworkSim 自身也受沉默端口 heartbeat 和 `min(safeTs)` 约束

## 4. 状态转移/时序图（文字版）

### 4.1 新路径 ReadReq 异步完整流程（gem5 → ubio → gem5）

1. gem5 `UBAdapter(PortAsync)` 在 `curTick=t0` 构造 `MemMessage`
2. 本地分配新 `reqId = rid_g0`
3. **发送前**在 `_pendingRequests[rid_g0]` 插入 `PendingTxn{pkt,...}`
4. `transportSend()` 调用 Port 发送：
   - 若 `send()==false`：表示消息绝对未发送，立即删除 `_pendingRequests[rid_g0]`，返回失败
   - 若成功：消息进入 IPC
5. gem5 `processSyncAndReceive()` 周期性：
   - `emitSync(curTick)`
   - `pollAbs(curTick)` 消费所有 `timestamp <= curTick` 的 response
   - 以 `safeTs(curTick)` 安排下一次唤醒
6. NetworkSim 收到请求：
   - 记录 `{rid_g0 -> rid_n0}` 映射
   - 将消息 reqId 重写为 `rid_n0`
   - 按延迟队列入队，`deliver_time = now + latency`
7. 到达 `deliver_time` 后，NetworkSim 出队：
   - 将消息 timestamp 重戳为 NetworkSim 当前虚拟时间 `t1`
   - 发往 ubio
8. ubio 收到请求：
   - 如有转发层，再次重分配 reqId，例如 `rid_u0`
   - 本地 UBCC 处理请求，生成 response
9. response 回程：
   - ubio 依据本地反向映射恢复到上一跳 reqId
   - NetworkSim 收到 response，依据 `{rid_g0 <- rid_n0}` 反向映射恢复为 `rid_g0`
   - 出队时再重戳为 NetworkSim 当前虚拟时间 `t2`
10. gem5 Port 收到 response：
    - 若 `timestamp <= curTick`，`pollAbs()` 直接消费
    - `handleResponse(msg)` 读取 `rid_g0`
    - 命中 `_pendingRequests[rid_g0]`
    - 删除表项并将数据回填到原始 `PacketPtr`

### 4.2 多端口 `pollAllPorts` 推进时间线

设模块有 Port A / Port B，当前 `lastTs=100`：

1. 对 A、B 分别执行 `emitSync(100)`
2. 对 A 执行 `pollAbs(100)`：
   - 若收到 `timestamp=120` 的 future message，则缓存为 `pending(A)=120`
   - A 不再继续读 socket
3. 对 B 执行 `pollAbs(100)`：
   - 若无消息，保持空
4. 计算：
   - `safeTs(A)=min(receiveTimestamp(A)=120, syncBound(A))`
   - `safeTs(B)=min(receiveTimestamp(B)=lastRxT/UINT64_MAX, syncBound(B))`
5. 全局取 `minTs = min(safeTs(A), safeTs(B))`
6. 若 `minTs > lastTs`，则模块时间推进到 `minTs`
7. 当时间推进到 120 后，A 上的 pending 变为可见消息，被下一轮 `pollAbs(120)` 消费

### 4.3 NetworkSim 转发 + reqId 重分配流程

1. 上一跳消息头携带 `reqId = rid_in`
2. NetworkSim 收包后分配本地 `rid_ns`
3. 记录映射：`forwardMap[rid_ns] = rid_in` 或等价双向映射
4. 转发出队时：
   - 将 header.reqId 改写为 `rid_ns`
   - 将 header.timestamp 重戳为当前 NetworkSim 虚拟时间
5. response 回程时携带 `rid_ns`
6. NetworkSim 查映射恢复 `rid_in`
7. 将 response 发回上一跳

## 5. 实施分阶段计划

### Phase 1：传输头与 Port 语义冻结（必须串行）

#### 涉及文件
- `framework/MemMessage.hh`
- `framework/Port.{hh,cc}`

#### 工作内容
- 在消息头中固定 `reqId`
- 实现 `ReceiveStatus` 扩展
- 实现单槽 future cache、`receiveTimestamp()`、`safeTs()`、heartbeat throttle
- 旧 lower-bound 接口转调 `safeTs()`

#### 完成标准
- 单端口场景下 future message 不丢失、不越时消费
- sync 包可推进 `_lastRxT`
- `send()==false` 语义与上层约定一致

#### 预估工作量
- 相对权重：**25%**

### Phase 2：多端口统一推进骨架（必须串行）

#### 涉及文件
- `tools/ubio/ubio_main.cc`
- `tools/networksim/networksim_main.cc`

#### 工作内容
- 实现 `pollAllPorts()`
- 接入 heartbeat + `min(safeTs)` 推进
- NetworkSim 出队重戳

#### 完成标准
- 多端口场景下，静默端口不拖死系统
- 单端口 pending future 能成为全局推进下界
- NetworkSim 时间推进与 ubio 语义一致

#### 预估工作量
- 相对权重：**20%**

### Phase 3：UBAdapter 新异步路径（可与 Phase 2 后半并行）

#### 涉及文件
- `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.{hh,cc}`

#### 工作内容
- 引入 `TransportMode`
- 新增 `_pendingRequests`
- 实现 `processSyncAndReceive()` / `handleResponse()`
- 明确新路径发送前插表、send-fail 回滚

#### 完成标准
- `PortAsync` 模式下 response 全部通过 reqId 命中 `_pendingRequests`
- 不再依赖 retry queue 兜底完成同类功能
- `schedule(syncEvent, safeTs)` 能稳定推进

#### 预估工作量
- 相对权重：**25%**

### Phase 4：旧路径兼容过渡（可并行，但需共享 mode 契约）

#### 涉及文件
- `gem5/src/mem/ruby/protocol/chi/ep/UBIOModule.{hh,cc}`
- `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.{hh,cc}`

#### 工作内容
- 维持 `LegacyInline` 调用链（**仅过渡期内**）
- 增加 mode 断言与注释
- 防止 `_lastResponse` 与 `_pendingRequests` 混用
- 回退路径测试：确保旧路径行为不变

#### 完成标准
- `_port == nullptr` 旧路径行为保持不变
- `_port != nullptr` 新路径不再误读 `_lastResponse`

#### 预估工作量
- 相对权重：**10%**

### Phase 5：联调与回归（最后串行）

#### 涉及文件
- 上述全部

#### 工作内容
- gem5 / ubio / NetworkSim 三进程联调
- future message、heartbeat、reqId 转发、旧路径兼容回归

#### 完成标准
- 新旧路径都能完成 ReadReq/response 闭环
- 多端口 `pollAllPorts` 不死锁、不忙等
- safeTs 推进与每跳重戳符合预期

#### 预估工作量
- 相对权重：**10%**

### Phase 6：完全删除旧同步路径（最终清理）

#### 涉及文件
- `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.{hh,cc}`
- `gem5/src/mem/ruby/protocol/chi/ep/UBIOModule.{hh,cc}`

#### 工作内容
- 删除 `TransportMode::LegacyInline` 枚举值
- 删除 `_lastResponse`、`_lastResponseValid`
- 删除 `transportRecv()` 中所有 legacy 轮询分支
- 删除 UBIOModule 中旧同步直接调用链
- 确保 `_port` 在所有实例中永不 null

#### 完成标准
- 代码中不存在 `LegacyInline` / `_lastResponse` / 旧同步调用链任何残留
- `_port == nullptr` 路径被彻底消除
- 全部 56 E2E TC 通过新异步路径

#### 预估工作量
- 相对权重：**5%**

## 6. 迁移兼容策略

### 6.1 `synced_receive_lower_bound` → `safeTs` 过渡

- 第一阶段不删除旧接口
- 旧接口内部直接调用 `safeTs()` 或 `receiveTimestamp()` 派生逻辑
- 所有新代码只允许调用 `safeTs()`
- 待调用点全部收敛后，再删除旧接口名与旧注释

### 6.2 旧路径保留策略

- 旧路径以 `LegacyInline` mode 在**过渡期内**保留
- **Phase 5 联调通过后，立即执行 Phase 6 完全删除**——不允许长期共存
- Phase 6 删除内容：
  - `TransportMode::LegacyInline` 枚举值
  - `_lastResponse`、`_lastResponseValid` 成员
  - `transportRecv()` 中所有 legacy 轮询分支
  - UBIOModule 中的同步调用链
  - 所有 `_port == nullptr` 的兼容分支

### 6.3 回归测试覆盖要求

至少覆盖以下路径：
1. 单端口，正常请求/响应闭环
2. 单端口，future message 延后可见
3. 多端口，单一端口 pending future 拖住全局 `min(safeTs)`
4. 静默端口仅 heartbeat 场景
5. NetworkSim 转发、每跳重戳、稳定出队
6. reqId 重分配与回程反向恢复
7. `send()==false` 回滚 `_pendingRequests`
8. `LegacyInline` 旧路径 inline response 回归
9. `PortAsync` 新路径不读取 `_lastResponse`

## 7. 未闭合风险与缓解

### 风险 1：旧路径/新路径双轨长期共存导致维护分叉
- 缓解：引入 `TransportMode`；实例级固定；关键路径加断言；文档明确两条路径的完成机制不同。

### 风险 2：源端 `_pendingRequests` 持有 `PacketPtr`，若 response 永不返回会泄漏
- 缓解：文档化前提——IPC 可靠、进程不 crash/restart、response 最终必回；增加 debug 计数器和水位日志，监控异常增长。

### 风险 3：`send()==false` 语义不清会导致误删或悬挂事务
- 缓解：已固定为“绝对未发送”；上层在 `send()==false` 时立即回滚 `_pendingRequests`。

### 风险 4：sync 包若不更新 `_lastRxT`，safeTs 将失效
- 缓解：将“sync 更新接收时间边界”写成 Port 层硬不变量；回归测试覆盖纯 heartbeat 推进场景。

### 风险 5：单槽 future cache 被后续维护误改为“pending 期间继续 drain socket”
- 缓解：在 `Port` 注释和断言中明确：`pending` 存在时禁止继续读 socket；加入调试统计检测违例。

### 风险 6：NetworkSim 若未同步采用 `pollAllPorts -> min(safeTs)`，则端到端 safeTs 保守性不成立
- 缓解：NetworkSim 与 ubio 使用同一推进模型；将其列为非 busy-wait 正确性的前置条件之一。

## 8. 方案结论

在以下前提成立时：
- 同一 Port timestamp 单调不减
- 沉默端口持续 heartbeat
- sync 包更新 `_lastRxT`
- 所有中间节点统一执行 `pollAllPorts -> min(safeTs)`
- `send()==false` 表示绝对未发送
- 旧路径与新路径按 mode 互斥

则本方案可以以较小状态成本完成 Port 层时间同步和异步事件驱动改造，并为 gem5 / UBIOModule / NetworkSim 多进程运行提供一致的时间推进、逐跳重戳和 reqId 转发语义。

**注意：旧同步路径（`LegacyInline` mode、`_lastResponse`、`transportRecv` 轮询）仅在过渡期内作为脚手架保留。Phase 5 联调通过后，Phase 6 将彻底删除全部旧代码——不允许长期共存。最终状态：`_port` 永不 null，所有通信走 PortAsync 新异步路径。**

该方案已达到可实施状态，推荐按 Phase 1 → Phase 6 顺序落地。
