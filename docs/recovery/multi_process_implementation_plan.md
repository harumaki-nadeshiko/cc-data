# 多进程 gem5 + 独立 UBIOModule 实现方案

## 1. 总体架构

目标是把当前“单 gem5 内部包含 Ruby/CHI + UBIOModule”的结构拆成：

- `gem5_<node>`：每节点一个 gem5 进程，只保留 Ruby/CHI 协议栈与 `UBAdapter`
- `ubio_<node>`：每节点一个独立 UBIOModule 进程，持有 `UBCCController/ResidentDir/Backstore`
- `networksim`：一个独立网络进程，只负责 store-and-forward + FIFO + 配置时延
- `launcher.py`：唯一编排者，生成 endpoint、分发 per-module 配置、启动和监控子进程

### 1.1 逻辑拓扑

```text
                +----------------------------------+
                |            launcher.py            |
                |  - 解析总配置                     |
                |  - 分配 module_id                |
                |  - 生成 ipc endpoint             |
                |  - 生成 per-module 配置片        |
                |  - 启动/监控/收集日志            |
                +----------------+-----------------+
                                 |
              +------------------+------------------+
              |                                     |
      +-------v--------+                    +-------v--------+
      |    gem5_n0     |                    |    gem5_n1     |   ...
      | Ruby/CHI       |                    | Ruby/CHI       |
      | UBAdapter      |                    | UBAdapter      |
      +-------+--------+                    +-------+--------+
              |                                     |
         ipc/PAIR                                 ipc/PAIR
              |                                     |
      +-------v--------+                    +-------v--------+
      |    ubio_n0     |                    |    ubio_n1     |
      | Port(gem5)     |                    | Port(gem5)     |
      | Port(network)  |                    | Port(network)  |
      | UBCCController |                    | UBCCController |
      | ResidentDir    |                    | ResidentDir    |
      +-------+--------+                    +-------+--------+
              |                                     |
              +------------------+------------------+
                                 |
                           ipc/PAIR star
                                 |
                         +-------v--------+
                         |   networksim   |
                         | per-link FIFO  |
                         | latency model  |
                         +----------------+
```

### 1.2 关键边界

- `gem5 ↔ ubio`：只传输 **传输层 `MemMessage`**；当 `type==COH_MSG` 时，payload 是序列化后的 `CoherenceMessage`
- `ubio ↔ networksim`：同样只传输 `MemMessage`
- `networksim` 不解析 `CoherenceMessage` 内部字段，仅根据传输头的 `src_module/dst_module/src_port/dst_port/type/timestamp/req_id` 转发
- `CONTROL_SYNC` 仅用于时间推进，**不是 keepalive**
- 全局同步窗口 `L` 固定，由 launcher 下发；运行时校验 `link_latency < L`，否则直接 fatal

### 1.3 进程职责

| 进程 | 保留内容 | 移出内容 |
|---|---|---|
| gem5 | RubySystem、CHI controller、EPBackend、EPRNF/EPSNF、UBAdapter | UBCCController、ResidentDir、Backstore、跨节点路由 |
| ubio | UBCCController、ResidentDir、Backstore、单飞行、replay、grant-handshake/recall/invalidate 状态机 | Ruby/CHI controller |
| networksim | link FIFO、转发、时延 | coherence 语义 |

---

## 2. MemMessage 格式

### 2.1 传输层枚举

```cpp
enum class MemMessageType : uint16_t {
    CONTROL_SYNC = 0,
    TERMINATE    = 1,
    COH_MSG      = 2,
};
```

约束：

- 只允许这三类
- 收到未知 `type` 直接 fatal
- `COH_MSG` 的 payload 是完整序列化 `CoherenceMessage`
- 传输层 **不解析** `CoherenceMessage` 内部字段

### 2.2 传输头定义

`Q11` 冻结的最小头字段为：`timestamp, size, type, src_module, src_port, dst_module, dst_port, payload[]`。

`Q13` 又冻结了：**传输层已有 `reqId`，并与 `CoherenceMessage.reqId` 保持一致**。因此最终头需要补入 `req_id`。

推荐固定布局：

```cpp
struct MemMessageHeader {
    uint64_t timestamp;     // 消息在接收侧“可见”的逻辑时间
    uint32_t size;          // 整个消息大小，含 header + payload
    uint16_t type;          // MemMessageType
    uint16_t reserved0;

    uint32_t req_id_lo;     // 低 32b；若需要完整 64b，可直接改为 uint64_t req_id
    uint32_t req_id_hi;

    uint32_t src_module;    // launcher 离线分配的整数 module_id
    uint16_t src_port;
    uint16_t reserved1;

    uint32_t dst_module;
    uint16_t dst_port;
    uint16_t reserved2;

    uint8_t payload[];      // 柔性数组
};
```

更直接的实现也可使用：

```cpp
struct MemMessage {
    uint64_t timestamp;
    uint32_t size;
    MemMessageType type;
    uint64_t req_id;
    uint32_t src_module;
    uint16_t src_port;
    uint32_t dst_module;
    uint16_t dst_port;
    uint8_t payload[];
} __attribute__((packed));
```

### 2.3 payload 规则

- `CONTROL_SYNC`：payload 为空
- `TERMINATE`：payload 为小控制体
- `COH_MSG`：payload 为 `serialize(CoherenceMessage)` 结果

```cpp
struct TerminatePayload {
    uint32_t reason;      // NormalStop / Fatal / LauncherAbort / Timeout
    int32_t  exit_code;
    uint32_t sender;      // sender module_id
};
```

### 2.4 序列化约束

- 统一小端
- `size` 必须等于 `sizeof(header)+payload_bytes`
- 默认 `entry_size=1024B`，可配置；若序列化后超限，发送侧 fatal
- `req_id` 对 `COH_MSG` 必须等于 `CoherenceMessage.h.reqId`
- `src_module/dst_module` 使用运行态整数 ID；名字到 ID 的映射只在 launcher 中存在

---

## 3. Port/ZMQChannel 设计

### 3.1 类分层

```text
Port
 ├─ 维护上层可见接口：send_allocate_buffer / send / recv / emitSync
 ├─ 维护 next_visible_tick / last_sync_emit_tick / ingress buffer
 └─ 持有一个 ZMQChannel

ZMQChannel
 ├─ zmq::context_t
 ├─ tx_socket : PAIR
 ├─ rx_socket : PAIR
 ├─ tx_endpoint / rx_endpoint
 └─ bind/connect 生命周期管理
```

### 3.2 ZMQChannel

每个逻辑 `Port` 只连一个对端，不支持 fan-in/fan-out。为满足“单向*2”的冻结约束，每条 duplex 连接分成：

- 本端 `rx_socket`：`PAIR + bind(local_rx_endpoint)`
- 本端 `tx_socket`：`PAIR + connect(peer_rx_endpoint)`

因此 launcher 必须为每个模块端口生成：

- `local_rx_endpoint`
- `peer_rx_endpoint`

推荐 endpoint 形式：

```text
ipc:///tmp/cc-ep/<run_id>/<module_name>/port_<id>_rx.ipc
```

### 3.3 Port 状态

```cpp
class Port {
  private:
    uint32_t _moduleId;
    uint16_t _portId;
    uint32_t _peerModuleId;
    uint16_t _peerPortId;
    uint64_t _syncWindowL;
    uint32_t _entrySize;

    ZMQChannel _chan;

    uint64_t _lastSyncEmitTick = 0;
    uint64_t _advertisedLowerBound = 0; // 最近一次收到的 CONTROL_SYNC.timestamp
    uint64_t _nextVisibleTick = 0;

    std::deque<std::vector<uint8_t>> _rxReady; // 已收到但未被上层消费的 COH_MSG
    std::priority_queue<...> _rxHeap;          // 按 timestamp 排序
};
```

### 3.4 Port 外部接口

```cpp
MemMessage* send_allocate_buffer(uint64_t timestamp);
bool send(MemMessage* msg);
MemMessage* recv(uint64_t visible_tick);
bool emitSync(uint64_t curTick);
void pumpInbound();
uint64_t nextVisibleTick() const;
```

语义：

- `send_allocate_buffer(timestamp)`：申请一块最多 `entry_size` 的发送 buffer，并先填入 `timestamp`
- `send(msg)`：发送整包；API 视角非阻塞，但 ZeroMQ 在 HWM 下可能阻塞；单线程使用
- `recv(visible_tick)`：返回 **timestamp <= visible_tick** 的最早 `COH_MSG`；无则返回空
- `emitSync(curTick)`：若 `curTick - _lastSyncEmitTick >= L`，发送一个空 payload 的 `CONTROL_SYNC`
- `pumpInbound()`：非阻塞地把 socket 上所有当前可读消息吸入本地堆；对 `CONTROL_SYNC` 只更新时间下界，不上送协议层

### 3.5 next_visible_tick 更新规则

Port 的 `next_visible_tick` 表示：**从该端口观察到的“对端未来最早可能可见事件”的保守下界**。

定义：

- `buffered_min_ts`：本端口已缓存但未交给上层的最早 `COH_MSG.timestamp`；若无，则为 `INF`
- `advertised_lb`：最近收到的 `CONTROL_SYNC.timestamp`；初始为 `0`

则：

```text
next_visible_tick = min(buffered_min_ts, advertised_lb)   if buffered_min_ts exists
next_visible_tick = advertised_lb                         otherwise
```

### 3.6 CONTROL_SYNC 行为

- `CONTROL_SYNC` 是传输层控制包
- 不走 coherence 协议处理
- payload 为空
- 其 `timestamp` 也遵循普通发送规则
- 仅用于让对端更新 `advertised_lb`，从而推动 `safeTick`

---

## 4. 同步算法

### 4.1 设计目标

系统无物理全局时钟；采用**逻辑全局时间 + 各进程本地保守推进**。

冻结约束：

- `L` 为全局固定同步窗口
- 对所有链路强制 `link_latency < L`
- `safeTick` 语义是：**已确认安全可推进的时间下界**
- 每个 `Port` 维护 `next_visible_tick`

### 4.2 时间戳语义

发送侧在构包时写：

```text
msg.timestamp = sender_curTick + modeled_link_latency
```

因此接收侧看到该消息时，可解释为：

> 该事件在逻辑全局时间 `timestamp` 时刻对我可见。

### 4.3 lookahead 与 L

- **静态 lookahead**：每条链路自己的 `link_latency`
- **同步窗口 L**：强制 silent sender 周期性发 `CONTROL_SYNC` 的时间窗口

作用分离：

- `link_latency` 决定消息何时可见
- `L` 决定“如果没有真实数据，也要多久发一次同步下界”

`CONTROL_SYNC` 不是保活，只是时间推进通告。

### 4.4 safeTick 计算

对一个拥有多个端口的模块：

```text
safeTick = min(port_i.next_visible_tick)
```

这不是“当前收到的最早事件时间”，而是“现在可以安全推进到的最小下界”。

### 4.5 通用 synced_receive 算法

```cpp
uint64_t synced_receive(std::vector<Port*>& ports, uint64_t& curTick)
{
    for (auto* p : ports) {
        if (!p->emitSync(curTick)) {
            fatal("emitSync failed");
        }
        p->pumpInbound();
    }

    uint64_t safeTick = INF;
    for (auto* p : ports) {
        safeTick = std::min(safeTick, p->nextVisibleTick());
    }

    bool progressed = true;
    while (progressed) {
        progressed = false;
        for (auto* p : ports) {
            while (auto* msg = p->recv(safeTick)) {
                handle(msg);
                progressed = true;
                p->pumpInbound();
            }
        }
        if (progressed) {
            safeTick = INF;
            for (auto* p : ports)
                safeTick = std::min(safeTick, p->nextVisibleTick());
        }
    }

    if (safeTick > curTick)
        curTick = safeTick;

    return safeTick;
}
```

### 4.6 正确性直觉

若某端口长时间没有真实数据，发送侧最迟每 `L` tick 发一次 `CONTROL_SYNC`。由于链路时延满足 `link_latency < L`，接收侧总能在有限时间内获得新的下界，因此：

- 不会因“静默对端”永久卡死
- 不会在未确认安全前越过未来可能到来的更早消息

### 4.7 gem5 特殊要求

gem5 不能 busy-wait 或 yield。故 gem5 侧实现必须是：

1. `wakeup()` 中调用 `synced_receive`
2. 处理 `safeTick` 内所有可交付消息
3. 若无更多可做工作，则 `schedule(next_event, safeTick)`

即：**事件重调度代替自旋等待**。

---

## 5. gem5 Adapter 设计

### 5.1 总体思路

保留 `UBAdapter` 作为 gem5 进程中的唯一外部消息门面，但把当前“直连本地 UBIOModule”的实现改成“直连独立 ubio 进程的 Port”。

### 5.2 对象关系

```text
EPBackend / EPRNF / EPSNF
          |
          v
      UBAdapter (SimObject)
          |
          +-- Port(to ubio)
          |
          +-- RubySystem*   // Q14 冻结：直接持有
          |
          +-- EventFunctionWrapper recvEvent
```

### 5.3 UBAdapter 关键成员

建议扩展为：

```cpp
class UBAdapter : public SimObject
{
  private:
    RubySystem* _ruby = nullptr;
    std::unique_ptr<Port> _port;
    EventFunctionWrapper _recvEvent;
    Tick _logicalTick = 0;
    Tick _lastScheduledTick = 0;

    uint32_t _moduleId;
    uint16_t _portId;
    std::string _moduleConfigPath;
    Tick _syncWindow;
    uint32_t _entrySize;
};
```

### 5.4 wakeup 流程

```cpp
void UBAdapter::wakeup()
{
    Tick safeTick = synced_receive({_port.get()}, _logicalTick);

    // 1) 收包：Port -> RubySystem
    while (auto* mm = _port->recv(safeTick)) {
        CoherenceMessage coh = deserializeCoh(mm->payload);
        _ruby->injectExternalCohMsg(coh);
    }

    // 2) 发包：RubySystem -> Port
    CoherenceMessage out;
    while (_ruby->popExternalCohMsg(out)) {
        auto bytes = serialize(out);
        auto* mm = _port->send_allocate_buffer(_logicalTick);
        fill_transport_header(mm, MemMessageType::COH_MSG, out.h.reqId, ...);
        copy_payload(mm, bytes);
        _port->send(mm);
    }

    // 3) 重调度
    schedule(_recvEvent, std::max(curTick(), safeTick));
}
```

### 5.5 RubySystem 注入接口

在 `RubySystem` 增加最薄外部接口：

```cpp
void registerExternalAdapter(int node_id, int socket_id, UBAdapter* adapter);
void injectExternalCohMsg(const CoherenceMessage& msg);
bool popExternalCohMsg(CoherenceMessage& out);
```

实现原则：

- `RubySystem` 只做队列与派发，不承担协议决策
- 真实处理仍落在 EP/CHI 控制器
- `UBAdapter` 不直接改 SLICC 状态机，只调用新增的外部消息入口

### 5.6 对现有代码的最小侵入做法

当前 `UBAdapter` 大量 `sendReadReq/sendWritebackReq/...` 已以 `CoherenceMessage` 为核心；因此迁移时保留这些上层 API，不改调用者，只把底层实现从：

```text
UBAdapter -> 本地 UBIOModule::transportSend()
```

替换为：

```text
UBAdapter -> Port::send(COH_MSG) -> 独立 ubio 进程
```

---

## 6. gem5 Config

### 6.1 Python SimObject 改造

`UBAdapter.py` 增加参数：

```python
class UBAdapter(SimObject):
    type = "UBAdapter"
    cxx_header = "mem/ruby/protocol/chi/ep/UBAdapter.hh"
    cxx_class = "gem5::ruby::UBAdapter"

    node_id = Param.Int(0, "Node ID")
    socket_id = Param.Int(0, "Socket ID")
    module_id = Param.UInt32(0, "Launcher-assigned runtime module id")
    port_id = Param.UInt32(0, "Port id to local ubio")
    module_config = Param.String("", "Per-module JSON config path")
    sync_window = Param.Tick(0, "Global fixed sync window L")
    entry_size = Param.UInt32(1024, "Transport buffer size")
```

`UBIOModule.py` 不再被 gem5 配置直接实例化；它会保留仅用于兼容旧分支，主线切到独立进程二进制。

### 6.2 CHI_ubcc_framework.py 的重构方向

当前 `CHI_ubcc_framework.py` 是“多节点同进程 builder”。重构为两层：

1. `chi_ubcc_node_builder.py`：只负责**单节点** Ruby/CHI/EP 对象构建
2. `gem5_run.py`：新的单节点入口，读取 launcher 下发的 per-module 配置片，实例化本节点

### 6.3 gem5_run.py 单节点入口

```bash
gem5.opt gem5/configs/ruby/gem5_run.py --module-config run/gem5_n0.json
```

其行为：

- 只创建一个 node 的 CPU/Ruby/CHI/EP 组件
- 从 per-module 配置读取：
  - 本节点 `node_id/module_id`
  - 本地 `UBAdapter` 的 endpoint
  - 全局 `sync_window`
  - 运行参数（workload、cache、mem range）
- 在 `m5.instantiate()` 前完成 `UBAdapter` 参数绑定

### 6.4 兼容策略

过渡期保留 `CHI_ubcc_framework.py` 作为“单进程回归模式”；`gem5_run.py` 为新的多进程主入口。这样可以在迁移期间并行维护：

- 单进程旧路径：用于协议回归
- 多进程新路径：用于传输/编排/同步验证

---

## 7. UBIOModule 独立进程

### 7.1 进程内组件

每个 `ubio_<node>` 进程持有：

- `Port gem5_port`
- `Port network_port`
- `UBCCController`
- `ResidentDir`
- `BackstoreOrganization`
- `replay/single-flight` 调度器
- 可选的本地定时器/延迟队列

### 7.2 关键重构

当前 `UBCCController` 头文件直接引用 `EPBackend.hh`，并带有 gem5 侧回调依赖。独立进程化时必须先抽出一个共享边界：

```cpp
class UbioHostIf {
  public:
    virtual void sendToGem5(const CoherenceMessage&) = 0;
    virtual void sendToNetwork(const CoherenceMessage&) = 0;
    virtual void issueBackstoreRead(uint64_t pa) = 0;
    virtual void issueBackstoreWrite(uint64_t pa) = 0;
    virtual void issueBackstoreDelete(uint64_t pa) = 0;
};
```

实施上：

- 把 `GrantDataSource` 等共享枚举从 `EPBackend.hh` 挪到共享头（如 `CoherenceMessage.hh` 或新增 `UBCCCommon.hh`）
- `UBCCController` 不再直接持 `EPBackend*`
- 独立进程实现 `UbioHostIf`

### 7.3 主循环

```cpp
while (!terminated) {
    Tick safe = synced_receive({&gem5_port, &network_port}, logicalTick);

    drainLocalTimers(logicalTick);
    drainGeneratedMessages();

    if (safe > logicalTick)
        logicalTick = safe;
}
```

### 7.4 收包分流

- 从 `gem5_port` 收到 `COH_MSG`：
  - 若是发往 home UBCC 的请求/ack：交给 `UBCCController`
  - 若是本地 gem5 必须处理的通知：按需原路回发或经 `network_port` 转发
- 从 `network_port` 收到 `COH_MSG`：
  - 若目标是本节点 UBCC：交给 `UBCCController`
  - 若目标是本节点 gem5：转发给 `gem5_port`

### 7.5 2-port 结构

冻结为固定 2 端口：

- `port 0`：本地 gem5
- `port 1`：networksim

这样 per-module 配置最简单，且与“一个 Port 一个对端”约束匹配。

### 7.6 混合通信模式

UBIOModule-UBIOModule 的跨节点语义是**混合的**：

- 双边 request/response：`ReadReq/ReadResp`、`RecallReq/RecallResp`、`InvalidateReq/InvalidateAck`、`ClearReq/ClearResp`、`WritebackReq/Resp`、`EvictReq/Resp`、`UpgradeReq/Resp`、`UpgradeDoneReq/Resp`
- 单边 fire-and-forget：`HomeWritebackNotify`

实现上仍统一走 `COH_MSG`，只是在 completion 表中区分释放时机。

---

## 8. NetworkSim

### 8.1 职责

`networksim` 是一个无协议语义的中心转发进程：

- 每链路 FIFO
- store-and-forward
- 可配置 link latency
- 不做带宽模型
- 不做 keepalive

### 8.2 端口拓扑

`networksim` 为每个连接到它的模块维护一个本地 `Port`。典型连接对象是所有 `ubio_<node>` 模块。

### 8.3 转发规则

收到 `MemMessage` 后：

1. 校验 `type` 合法
2. 根据 `dst_module` 在全局拓扑中找到出端口
3. 把消息放入 `(src_module,dst_module)` 对应 FIFO
4. 做：

```text
forward_msg.timestamp = recv_msg.timestamp + link_latency
```

5. FIFO 头部消息在其 `timestamp <= local_safeTick` 时，经对应 egress `Port` 发出

### 8.4 FIFO 语义

对同一 `(src_module, dst_module)` 逻辑链路：

- 保持发送顺序
- 不允许乱序绕过前包
- `CONTROL_SYNC` 也进入该 FIFO，从而与数据包共享同一因果通道

### 8.5 启动时校验

启动时逐条校验：

```text
0 <= link_latency < sync_window_L
```

任一不满足，立即 fatal 并退出。

---

## 9. launcher.py

### 9.1 输入与输出

输入：总配置 JSON。

输出：

- `run/topology_networksim.json`：仅 networksim 读取的全局拓扑
- `run/<module>.json`：每模块独立配置片
- `run/logs/<module>.stdout/.stderr`
- `run/endpoints/*.ipc`

### 9.2 启动顺序

建议顺序：

1. 创建 `run_dir`
2. 生成 module name → integer id 映射
3. 生成所有 IPC endpoint
4. 生成 networksim 全局拓扑与每模块本地配置片
5. 启动 `networksim`
6. 启动全部 `ubio_*`
7. 轮询各 `bind` 端 IPC 文件已出现
8. 启动全部 `gem5_*`

不引入 `CONTROL_READY`；准备就绪只靠：

- 进程存活
- bind 端 socket 文件出现

### 9.3 监控策略

- 任一 gem5 非零退出：launcher 终止全部剩余进程并返回失败
- **所有 gem5 正常退出**：launcher 直接 kill 其余 `ubio_*` 和 `networksim`
- `CONTROL_SYNC` 不承担 liveness 判断；launcher 只看子进程退出码

### 9.4 命令模板

`modules[]` 显式指定：

```json
{
  "name": "gem5_n0",
  "type": "gem5",
  "executable": "build/ARM/gem5.opt",
  "args": ["gem5/configs/ruby/gem5_run.py", "--module-config", "@MODULE_CONFIG@"]
}
```

launcher 只做占位符展开，不硬编码模块类型到命令映射。

---

## 10. 配置文件格式

### 10.1 launcher 总配置 JSON schema

```json
{
  "run_dir": "run/mp_tc01",
  "sync_window": 10000,
  "entry_size": 1024,
  "modules": [
    {
      "name": "gem5_n0",
      "type": "gem5",
      "node_id": 0,
      "socket_id": 0,
      "executable": "build/ARM/gem5.opt",
      "args": ["gem5/configs/ruby/gem5_run.py", "--module-config", "@MODULE_CONFIG@"]
    },
    {
      "name": "ubio_n0",
      "type": "ubio",
      "node_id": 0,
      "socket_id": 0,
      "executable": "build/host/ubio_main",
      "args": ["--module-config", "@MODULE_CONFIG@"]
    },
    {
      "name": "networksim",
      "type": "networksim",
      "executable": "build/host/networksim",
      "args": ["--module-config", "@MODULE_CONFIG@"]
    }
  ],
  "links": [
    {"src": "gem5_n0", "src_port": 0, "dst": "ubio_n0", "dst_port": 0, "latency": 0},
    {"src": "ubio_n0", "src_port": 1, "dst": "networksim", "dst_port": 0, "latency": 100},
    {"src": "networksim", "src_port": 1, "dst": "ubio_n1", "dst_port": 1, "latency": 100}
  ],
  "workload": {...},
  "debug": {...}
}
```

### 10.2 per-module 配置片 schema

每模块只看到自己的邻接信息：

```json
{
  "module": {
    "name": "ubio_n0",
    "module_id": 2,
    "type": "ubio",
    "node_id": 0,
    "socket_id": 0
  },
  "runtime": {
    "sync_window": 10000,
    "entry_size": 1024,
    "log_dir": "run/mp_tc01/logs"
  },
  "ports": [
    {
      "port_id": 0,
      "peer_module_id": 1,
      "peer_port_id": 0,
      "local_rx_endpoint": "ipc:///.../ubio_n0_p0_rx.ipc",
      "peer_rx_endpoint": "ipc:///.../gem5_n0_p0_rx.ipc",
      "link_latency": 0
    },
    {
      "port_id": 1,
      "peer_module_id": 5,
      "peer_port_id": 0,
      "local_rx_endpoint": "ipc:///.../ubio_n0_p1_rx.ipc",
      "peer_rx_endpoint": "ipc:///.../networksim_p0_rx.ipc",
      "link_latency": 100
    }
  ],
  "module_specific": {
    "resident_bf_bytes": 65536,
    "backstore_org": "schema_a"
  }
}
```

### 10.3 networksim 专用拓扑 JSON schema

```json
{
  "module": {"name": "networksim", "module_id": 5, "type": "networksim"},
  "runtime": {"sync_window": 10000, "entry_size": 1024},
  "ports": [... 仅 networksim 自己的邻接端口 ...],
  "routes": [
    {"src_module": 2, "dst_module": 4, "ingress_port": 0, "egress_port": 1, "latency": 100},
    {"src_module": 4, "dst_module": 2, "ingress_port": 1, "egress_port": 0, "latency": 100}
  ]
}
```

---

## 11. per-PA 单飞行实现

### 11.1 key

冻结：`single-flight key = cache-line-aligned PA`。

### 11.2 数据结构

沿用并强化 `UBCCController` 现有机制：

- `std::map<uint64_t, OutstandingRequest> _outstandingByPa`
- `std::map<uint64_t, std::deque<PendingRequester>> _pendingRequesters`

### 11.3 入队规则

当新请求命中某 PA 且该 PA 已有 live outstanding：

1. 不立即发新事务
2. 转成 `PendingRequester`
3. 挂入 `_pendingRequesters[line_pa]`

### 11.4 replay 规则

当 head outstanding 完成并释放后：

1. 调 `replayPendingRequesters(line_pa)`
2. 从 deque 头取下一个 `PendingRequester`
3. 以其原始 `reqType/writeIntent/epoch/reqId` 重新走完整 home-path
4. 若 replay 又遇到新 busy，再次排回本 PA 队列

### 11.5 队列深度

建议沿用 `MAX_PENDING_PER_PA = 4`，超限直接 fatal；过渡框架中不做 Nack/Retry 分支，以减少上游改造范围。

### 11.6 为什么继续沿用 deque

这是与现有 `UBCCController` 最一致的方案：

- 不要求 gem5/远端新增 Retry 协议
- 不改变 request/response 语义
- 便于保留当前 `replayPendingRequesters` 行为和日志

---

## 12. 完成条件表

冻结规则：**本地状态更新落完后释放**，而不是“仅收到网络应答就释放”。

| 事务 | 网络交互 | 释放条件 |
|---|---|---|
| `ReadReq -> ReadResp` | 双边 | `ReadResp` 到达，grant data/可见时间/状态填写完成后释放；`Clear` 是独立事务，不阻塞本事务释放 |
| `RecallReq -> RecallResp` | 双边 | owner 响应到达且 recall data 已缓存，后续由 `GRANT_HANDSHAKE` 消费后释放 |
| `InvalidateReq -> InvalidateAck` | 双边 | 所有 sharer ack 到达，并已转化为 `GRANT_HANDSHAKE` 可提交状态后释放 |
| `ClearReq -> ClearResp` | 双边 | `ClearAck/ClearResp` 返回，commit 点完成后释放 |
| `WritebackReq -> ack/WritebackResp` | 双边 | ack 返回且 resident dirty 已清除后释放 |
| `EvictReq -> ack/EvictResp` | 双边 | ack 返回且 sharer 已从目录移除后释放 |
| `UpgradeReq -> UpgradeResp -> UpgradeDoneReq -> UpgradeDoneResp` | 双边链 | `UpgradeDoneResp` 返回且 state committed 后释放 |
| `HomeWritebackNotify` | 单边 | 发送成功即释放 |

实现建议：

- 每个 `OutstandingRequest` 显式记录 `stage`
- 释放动作统一走 `completeOutstanding(line_pa, req_id)`
- 释放后立即 `replayPendingRequesters(line_pa)`

---

## 13. 终止协议

### 13.1 正常结束路径

冻结：**所有 gem5 正常退出后，launcher 直接 kill 其余进程**。

因此正常结束不依赖 `TERMINATE` 包。

### 13.2 TERMINATE 的用途

`TERMINATE` 仅用于：

- launcher 提前中止
- 某模块发现不可恢复 fatal，希望显式通知对端快速退出
- 调试/人工中断

### 13.3 TERMINATE 格式

```cpp
struct TerminatePayload {
    uint32_t reason;
    int32_t  exit_code;
    uint32_t sender;
};
```

其中：

- `reason`：`NormalStop/Fatal/Timeout/LauncherAbort`
- `exit_code`：建议传递实际子进程退出码
- `sender`：发送方 `module_id`

### 13.4 模块侧处理

模块收到 `TERMINATE` 后：

1. 记录日志
2. 置 `terminating=true`
3. 停止产生新业务流量
4. 尽快退出主循环

### 13.5 launcher 监控逻辑

- 周期性 `poll()` 全部子进程
- 任一 gem5 非零退出：
  - 尝试对其余模块发送 `TERMINATE(reason=Fatal)`
  - 等待极短超时
  - 未退出者直接 `kill`
- 全部 gem5 正常退出：直接 kill 其余模块，标记整个 run 成功

---

## 14. 分步实施计划

以下 6 步按“每步可独立编译测试”设计。

### Step 1：传输层落地

范围：

- 新增 `MemMessage`
- 新增 `ZMQChannel`
- 新增 `Port`
- 单元测试 `CONTROL_SYNC/COH_MSG/TERMINATE`

验收：

- 2 个独立测试进程可通过 IPC 往返收发
- `recv(visible_tick)` 能正确门控时间
- `safeTick=min(next_visible_tick)` 的 smoke test 通过

### Step 2：NetworkSim + launcher 落地

范围：

- 实现 `networksim`
- 实现 `launcher.py`
- 生成 per-module 配置片

验收：

- 仅用 dummy sender/dummy receiver 进程，经 `networksim` 正确 FIFO 转发
- `link_latency >= L` 会在启动时 fatal
- launcher 能收集日志并在所有 dummy “gem5” 退出后清理进程

### Step 3：gem5 Adapter 接入 Port

范围：

- `UBAdapter` 从直连本地 UBIOModule 改为直连 Port
- `RubySystem` 增加 `injectExternalCohMsg/popExternalCohMsg`
- 新增 `gem5_run.py`

验收：

- 单 gem5 + dummy ubio 可收发 `COH_MSG`
- gem5 不 busy-wait，只通过事件重调度推进
- `m5.instantiate()` 与正常退出路径稳定

### Step 4：UBIOModule 独立进程化

范围：

- 抽出 `UbioHostIf`
- `UBCCController` 去除对 `EPBackend*` 的直接耦合
- 新增 `ubio_main`
- 接入 `ResidentDir/Backstore/single-flight`

验收：

- 单 ubio 进程可独立跑 directed tests
- `_pendingRequesters + replayPendingRequesters` 正常
- 完成条件表逐项通过定向自测

### Step 5：两节点闭环联调

范围：

- `gem5_n0 <-> ubio_n0 <-> networksim <-> ubio_n1 <-> gem5_n1`
- 跑最小跨节点流：
  - `ReadReq/ReadResp`
  - `RecallReq/RecallResp`
  - `InvalidateReq/InvalidateAck`
  - `ClearReq/ClearResp`

验收：

- 日志中 `req_id` 可全链路关联
- 不出现 safeTick 倒退
- 无同一 PA 双飞行

### Step 6：全量集成与测试迁移

范围：

- 接 `Writeback/Evict/Upgrade/UpgradeDone/HomeWritebackNotify`
- 迁移现有 E2E/TC
- 保留单进程回归模式作为对照

验收：

- 至少先打通一条 controller-directed + 一条 system-level TC
- 之后逐步迁移 56 个 E2E 测试
- 单进程与多进程关键协议路径日志一致

---

## 15. TLOC 估算

以下是建议实施文件与预估 TLOC（新增/改动净行数，粗估）。

### 15.1 传输层与通用运行时

| 文件 | 类型 | 预估 TLOC |
|---|---|---:|
| `framework/MemMessage.hh` | 新增 | 140 |
| `framework/ZMQChannel.hh` | 新增 | 110 |
| `framework/ZMQChannel.cc` | 新增 | 170 |
| `framework/Port.hh` | 新增 | 170 |
| `framework/Port.cc` | 新增 | 300 |
| `framework/Makefile` | 修改 | 20 |
| `framework/tests/port_sync_smoke.cc` | 新增 | 220 |

### 15.2 gem5 侧

| 文件 | 类型 | 预估 TLOC |
|---|---|---:|
| `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.hh` | 修改 | 120 |
| `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc` | 修改 | 260 |
| `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.py` | 修改 | 30 |
| `gem5/src/mem/ruby/system/RubySystem.hh` | 修改 | 40 |
| `gem5/src/mem/ruby/system/RubySystem.cc` | 修改 | 120 |
| `gem5/src/mem/ruby/protocol/chi/ep/CoherenceMessage.hh` | 修改 | 40 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh` | 修改 | 60 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc` | 修改 | 120 |
| `gem5/src/mem/ruby/protocol/chi/ep/SConscript` | 修改 | 20 |
| `gem5/configs/ruby/CHI_ubcc_framework.py` | 修改 | 140 |
| `gem5/configs/ruby/gem5_run.py` | 新增 | 220 |

### 15.3 独立 UBIOModule 进程

| 文件 | 类型 | 预估 TLOC |
|---|---|---:|
| `gem5/src/mem/ruby/protocol/chi/ep/UBCCCommon.hh` | 新增 | 80 |
| `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh` | 修改 | 90 |
| `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc` | 修改 | 220 |
| `tools/ubio/UBIOModuleProcess.hh` | 新增 | 130 |
| `tools/ubio/UBIOModuleProcess.cc` | 新增 | 260 |
| `tools/ubio/ubio_main.cc` | 新增 | 120 |

### 15.4 NetworkSim 与编排

| 文件 | 类型 | 预估 TLOC |
|---|---|---:|
| `tools/networksim/NetworkSim.hh` | 新增 | 110 |
| `tools/networksim/NetworkSim.cc` | 新增 | 240 |
| `tools/networksim/networksim_main.cc` | 新增 | 100 |
| `tools/launcher.py` | 新增 | 280 |

### 15.5 文档与样例配置

| 文件 | 类型 | 预估 TLOC |
|---|---|---:|
| `configs/multi_process/example_launcher.json` | 新增 | 90 |
| `configs/multi_process/example_topology.json` | 新增 | 80 |
| `docs/recovery/multi_process_implementation_plan.md` | 新增 | 当前文件 |

### 15.6 总量

粗估总新增/改动规模：**约 3.9K ~ 4.4K TLOC**。

这其中真正高风险的部分只有三块：

1. `Port + synced_receive`
2. `UBCCController` 与 `EPBackend` 的解耦
3. `UBAdapter ↔ RubySystem` 的外部注入接口

其余部分以胶水和编排为主。
