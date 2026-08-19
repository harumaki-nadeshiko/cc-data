# CC-EP 框架手册 (Framework Manual)

> [!WARNING]
> **历史/已弃用文档，不再描述当前 Framework 公共接口与后端架构。** 本文保留用于追溯旧 ZeroMQ concrete Port、MemMessage 和早期构建/运行设计，其中大量路径、接口及“当前状态”陈述已经过时，不得作为新代码或移植依据。
> **截至 2026-08-05 的唯一权威指南：[`docs/design/framework_iface_backend_guide_zh.md`](design/framework_iface_backend_guide_zh.md)。** 当前生产代码只能使用 `framework/iface/{Message,Port,Log}.hh`，并通过 `libframework_local.a` 或未来 `libframework_real.a` 选择后端。

> 面向"移植到目标框架"的读者。本手册详尽介绍当前 cc-ep 工程的整体架构、
> 各模块职责、进程拓扑、IPC/时钟同步设计、`framework` 传输层接口、消息格式、
> 编译与运行方法，以及移植时最可能需要替换/微调的边界点。
>
> **以下内容仅对应当时的历史仓库状态，不再宣称是当前最终态。**

---

## 目录

1. [整体架构总览](#1-整体架构总览)
2. [仓库目录结构](#2-仓库目录结构)
3. [运行时进程拓扑](#3-运行时进程拓扑)
4. [framework 传输层（移植重点）](#4-framework-传输层移植重点)
   - 4.1 [Port 接口](#41-port-接口)
   - 4.2 [MemMessage 报文格式](#42-memmessage-报文格式)
   - 4.3 [PortEnvLoader 端点命名](#43-portenvloader-端点命名)
   - 4.4 [Log 日志接口](#44-log-日志接口)
5. [时钟同步设计（PDES）](#5-时钟同步设计pdes)
6. [各模块详解](#6-各模块详解)
   - 6.1 [ubio](#61-ubio-modulesubiomodule)
   - 6.2 [networksim](#62-networksim-modulesnetworksim)
   - 6.3 [barrier_manager](#63-barrier_manager-modulesbarrier)
   - 6.4 [gem5 侧 UBAdapter](#64-gem5-侧-ubadapter)
7. [protocol 共享协议层](#7-protocol-共享协议层)
8. [编译方法](#8-编译方法)
9. [运行方法](#9-运行方法)
10. [移植指南（替换 Port 实现）](#10-移植指南替换-port-实现)

---

## 1. 整体架构总览

本工程模拟一个 **多节点分布式共享内存 (DSM) 缓存一致性系统**。它把一个
gem5 全系统仿真拆分为 **多个独立操作系统进程**，进程之间通过 **ZeroMQ IPC**
交换消息，并用一套 **保守式并行离散事件仿真 (PDES) 时钟同步** 协议保证各
进程虚拟时钟不会互相超前而破坏因果性。

系统由两类代码构成：

| 层                     | 语言/形态         | 作用 |
|------------------------|-------------------|------|
| **gem5 仿真进程**       | C++ (gem5 SimObject) | 每个 DSM 节点跑一个 gem5 进程，内含该节点的 CPU/Ruby/CHI 缓存层次；通过 `UBAdapter` 与外部进程通信 |
| **外部 native 模块**    | 独立 C++ 可执行文件 | `ubio`（目录/后备存储 + 路由）、`networksim`（跨节点交叉开关网络）、`barrier_manager`（跨节点栅栏） |

三类外部模块 + N 个 gem5 进程，全部通过 **`framework::Port`** 这一层统一的
传输/时钟抽象互联。**移植的核心就是替换 `framework::Port` 的具体实现**（把
ZeroMQ 换成目标框架的传输原语），只要保持接口函数签名一致，上层模块无需改动。

```
   ┌──────────────┐   IPC    ┌──────────────┐   IPC    ┌──────────────┐
   │ gem5 node 0  │◄────────►│   ubio n0    │◄────────►│  networksim  │
   │ (UBAdapter)  │  Port    │ (home dir +  │  Port    │  (crossbar)  │
   └──────────────┘          │   router)    │          └──────┬───────┘
                             └──────────────┘                 │
   ┌──────────────┐   IPC    ┌──────────────┐   IPC           │
   │ gem5 node 1  │◄────────►│   ubio n1    │◄────────────────┤
   └──────────────┘          └──────────────┘                 │
                                                               │
   ┌──────────────┐          ┌──────────────┐                 │
   │barrier_manager│◄────────►│  (all ubio/  │                 │
   └──────────────┘   Port    │   gem5)      │─────────────────┘
                              └──────────────┘
```

---

## 2. 仓库目录结构

```
cc-ep/
├── framework/              # ★ 传输层 + 日志（移植核心）
│   ├── Port.hh / Port.cc          # Port 类 + PortEnvLoader（ZeroMQ 实现）
│   ├── MemMessage.hh              # 线格式报文头 + 载荷容器
│   ├── Log.hh / Log.cc           # LogInfo / LogError
│   ├── Makefile                  # 独立构建 libframework.a
│   ├── tests/port_sync_smoke.cc  # Port 冒烟测试（基于旧接口，见下注）
│   ├── ZMQChannel.* ZMQTransport.*  # 遗留（运行时未用，归档保留）
│   └── Pseudo*.{hh,cc}           # 遗留伪内存端口（运行时未用）
│
├── protocol/               # ★ gem5 与外部模块共享的协议头（权威副本）
│   ├── CoherenceMessage.hh        # 一致性消息 + 栅栏控制（Port 载荷内容）
│   └── NodeAddressMap.{hh,cc}     # 节点/socket ↔ 物理地址映射
│
├── modules/                # ★ 外部 native 模块（main + 实现同目录）
│   ├── ubiomodule/                # ubio：main + UBCCController + ResidentDir + 后备存储
│   │   ├── ubio_main.cc               # ← 入口
│   │   ├── test_peer.cc               # 独立 Port 对端测试
│   │   ├── UBCCController.{hh,cc}      # 目录/一致性控制器
│   │   ├── ResidentDir.{hh,cc}        # 常驻目录（bloom filter）
│   │   ├── ubio_base.hh              # cc:: 命名空间下的最小类型垫片（原 gem5_shim.hh）
│   │   ├── ubio_types.hh             # ubiocc::Tick 等本地类型别名
│   │   ├── CoherenceMessage.hh        # protocol/ 的转发头
│   │   ├── NodeAddressMap.{hh,cc}     # protocol/ 的转发头
│   │   ├── BackstoreTypes.hh          # UBCCMESIState 等（ubio 私有）
│   │   ├── BackstoreSchemaA.{hh,cc}   # 后备存储 schema A
│   │   ├── BackstoreSchemaC.{hh,cc}   # 后备存储 schema C
│   │   ├── BackstoreOrganization.hh   # 后备存储布局
│   │   ├── CoherenceMessageQueue.hh   # 消息队列工具
│   │   ├── DataBlock.hh               # 64 字节缓存行容器
│   │   └── ...                        # 其它辅助
│   ├── networksim/                # networksim：main + 实现
│   │   ├── networksim_main.cc         # ← 入口（含交叉开关路由）
│   │   ├── NetworkSim.{hh,cc}        # 网络仿真器实现
│   │   ├── ForwardTable.{hh,cc}      # 转发表
│   │   ├── main_test.cc              # 独立测试
│   │   └── networksim               # 拓扑目录（含 topo3.json 示例）
│   └── barrier/                   # barrier_manager
│       └── barrier_main.cc            # ← 入口
│
├── scripts/                # 构建脚本
│   ├── build_framework.sh         # 产出 libframework.a + 安装头文件
│   ├── build_all.sh              # 构建三个 native 二进制
│   ├── build_ubio.sh / build_networksim.sh / build_barrier.sh
│   └── build_modules.sh          # 备用一体化构建脚本
│
├── tools/                  # Python 工具 + 数据文件（非模块源码）
│   ├── launcher.py / latency_trace_to_html.py
│   ├── networksim/topo3.json      # 示例拓扑
│   ├── ubio/ barrier/             # 遗留目录（原 main 已迁入 modules/）
│   └── ...
│
├── tests/e2e/              # 端到端测试
│   ├── run_multi.sh              # ★ 多进程测试驱动
│   ├── test_e2e.py               # gem5 配置 + 各 TC 的 workload 编译/校验
│   └── workloads/                # TC 的 C 源码 + 编译产物 .elf
│
├── gem5/                   # gem5 子模块（独立 git repo）
│   └── src/mem/ruby/protocol/chi/ep/   # UBAdapter / EPBackend / EP*Controller
│
├── thirdparty/zeromq/      # ZeroMQ 头 + 静态库（移植时可替换）
├── docs/                   # 设计文档（本手册 + porting/ 目录）
└── ...
```

> **约定**：外部模块的 main 与实现源码统一放在 `modules/<模块>/` 下；
> `tools/` 只保留 Python 脚本与数据文件，不再存放 C++ main。
>
> **协议层精简**：`protocol/` 只保留 `CoherenceMessage.hh` 与
> `NodeAddressMap.{hh,cc}`（gem5 仍需引用的协议头）。后备存储相关头
> （`BackstoreTypes.hh`、`BackstoreSchemaA/C` 等）已迁回 `modules/ubiomodule/`，
> 成为 ubio 私有实现（gem5 对后备存储元数据格式零感知）。
>
> **gem5_shim 重命名**：`modules/ubiomodule/gem5_shim.hh` 已重命名为
> `ubio_base.hh`（诚实命名，不含任何 gem5 依赖，也不含 SimObject/RubySystem 等分支）。

---

## 3. 运行时进程拓扑

以 `N` 个节点、每节点 `K` 个 socket 为例，共启动 **`N*K + N + 2`** 个进程：

| 进程                   | 数量   | 职责 |
|------------------------|--------|------|
| `barrier_manager`      | 1      | 跨节点栅栏协调（先绑定） |
| `networksim`           | 1      | 跨节点交叉开关网络（先绑定） |
| `gem5.opt`（每节点一个）| N      | 该节点的 CPU + Ruby/CHI 缓存层次，含 `UBAdapter` |
| `ubio`（每 plane 一个） | N*K    | `(node, socket)` 平面的 home 目录 + 路由器 |

**全局 id**：每个 `(node, socket)` 平面有唯一 `gid = node*K + socket`。
`K=1` 时 `gid == node`，退化为单 socket 的传统布局。

**连接关系**（都经 `framework::Port`）：
- 每个 gem5 节点 ↔ 对应 ubio（点对点，双向 IPC 对）
- 每个 ubio ↔ networksim（点对点）
- gem5 节点（socket 0 的 UBAdapter）↔ barrier_manager（栅栏专用）
- ubio 之间的跨节点消息一律经 networksim 转发（不直连）

启动顺序（见 `run_multi.sh:start_all`）：
1. barrier_manager 绑定端点
2. networksim 绑定端点 + 生成 `topo.json`（全连接 mesh，port=1，延迟 100000）
3. 拉起 N 个 gem5，等待每个打印 `STEP5 Port enabled`（表示 Port 绑定完成）
4. 拉起 N*K 个 ubio

---

## 4. framework 传输层（移植重点）

`framework` 是唯一需要为目标框架重写实现的层。它对上层暴露三个东西：
**`Port` 类**、**`MemMessage` 报文格式**、**`PortEnvLoader` 端点命名**。
当前实现基于 ZeroMQ `PAIR` socket + IPC (`ipc://`) 传输。

### 4.1 Port 接口

定义于 `framework/Port.hh`。移植时**必须保持以下公开签名不变**，只替换
`Port.cc` 内部实现（`_ctx/_txSock/_rxSock` 等 ZMQ 成员可替换为目标框架句柄）。

```cpp
namespace framework {

static constexpr uint64_t kDefaultSyncInterval = 100000;
static constexpr uint64_t kDefaultLinkLatency  = 100000;

// —— 接收状态 ——
enum class ReceiveStatus {
    kMessage,        // 取到一条可见（timestamp <= curT）的消息；可能是
                     // CONTROL_SYNC — 调用方按 hdr.type 过滤跳过。
    kEmpty,          // 无消息
    kPendingFuture,  // 队首消息 timestamp > curT，尚不可见（被缓存）
};

// —— 端点静态身份（由 PortEnvLoader 填充）——
struct PortParams {
    std::string name;
    uint32_t moduleId = 0;
    uint32_t portId   = 0;
    std::string localRxEndpoint;   // 完整 URL（当前为 ipc://...）
    std::string peerRxEndpoint;    // 完整 URL
};

// —— 运行期可调参数 ——
struct PortRuntime {
    uint64_t syncInterval = kDefaultSyncInterval;  // = 100000
    uint64_t linkLatency  = kDefaultLinkLatency;   // = 100000
};

class Port {
  public:
    Port();
    ~Port();

    Port(const Port&) = delete;
    Port& operator=(const Port&) = delete;

    // 一次性初始化。失败返回 false。不可复用。
    bool init(const PortParams& params,
              const PortRuntime& runtime = PortRuntime());

    // 尽力给对端发 TERMINATE，然后释放 socket 资源。
    void terminate();

    // —— 数据面（发送）——
    // 分配一个以 timestamp 打戳的新堆 MemMessage（hdr.timestamp =
    // 历史说明已过时：当前opaque iface只预置timestamp/type/size；
    // sourceId/targetId必须由应用显式设置。
    // 所有权交给 send()；若调用方决定不发，必须自行 delete 返回的指针。
    // 仅在分配失败时返回 nullptr。
    MemMessage* allocateSendBuffer(uint64_t timestamp);

    // 将 msg 整包 memcpy 进 zmq 消息发送，然后 delete msg（拿走所有权，
    // 成败都 delete）。传输失败返回 false。
    bool send(MemMessage* msg);

    // —— 数据面（接收）——
    // 尝试接收一条消息。返回可见消息指针或 nullptr；status 回填 ReceiveStatus。
    // 返回指针指向 thread_local 静态缓冲，下一次 recv 前有效。
    MemMessage* recv(uint64_t curT, ReceiveStatus* status = nullptr);

    // —— 时钟同步接口（见第 5 节）——
    uint64_t receiveTimestamp() const;    // 对端最新时间戳
    uint64_t safeTs(uint64_t curT) const; // 本端可安全推进到的时刻上界
    bool emitSync(uint64_t curTick);      // 发心跳（>= linkLatency 才真正发）

    uint32_t moduleId() const;
    uint32_t portId() const;
    uint64_t syncInterval() const;
    const std::string& name() const;
};

} // namespace framework
```

> **注意**：Port 公开接口**没有** `TxHandle`、`PortState`、`closeLocal`、
> `failClosed`、`isReady`、`state`、`abortSend`、无参 `send()`、`buffer()`。
> 这些在 Port 对齐三项（2.1.1/2.1.3）中已被删除，移植时**不得重新引入**。

**关键语义约束**（移植实现必须满足）：

1. **发送所有权模型（模型 B）**：
   - `allocateSendBuffer(ts)` 每次 `new` 一块新 `MemMessage`，打戳后返回。
   - `send(msg)` 把 `*msg` 整包拷进传输层发出，**然后 `delete msg`**（成败都 delete）。
   - 调用方在"分配了但决定不发"（如 `setPayload` 失败）的路径上须**自行 `delete msg`**。
   - 每个 alloc 出来的 msg **有且仅有一次 delete**：在 `send` 内，或在放弃分支。
     禁止双重 delete，禁止泄漏。
2. **发送打戳**：`allocateSendBuffer(timestamp)` 把 `hdr.timestamp` 预置为
   `timestamp + linkLatency`（即"消息在对端最早可见时刻"），并把
   `hdr.size = kMemMessageHeaderSize`。历史 concrete Port 曾预置sourceId；当前opaque iface不再这样做，上层必须显式设置sourceId/targetId。
   `type`/`req_id`/`targetId`/payload。
3. **接收乱序缓冲**：`recv` 若取到 `timestamp > curT` 的消息，**不能**返回它，
   而要缓存为 `_pending` 并返回 `kPendingFuture`；直到 `curT` 追上才交付。
   这是保证因果性的关键——上层依赖它做保守推进。
4. **同步心跳与终止通知的交付方式**：
   - 收到 `CONTROL_SYNC`：更新对端时间戳 `_lastRxT`，作为**普通 `kMessage`** 返回
     （不再有 `kSync` 状态）；调用方按 `hdr.type == CONTROL_SYNC` 识别并跳过。
   - 收到 `TERMINATE`：作为**普通 `kMessage`** 返回（不再是 `kEmpty`），让应用层
     识别 `hdr.type == TERMINATE` 并标记该 port 完成、停止轮询它。Port 内部**不**维护
     "对端已终止"状态（无 `PortState::PEER_LOST`）。
5. **Port 不维护协议级状态**：Port 仅有一个私有 `_open` 布尔表示 socket 是否已
   绑定/可用（资源生命周期事实），**不**维护对端是否 terminate/可用（那是应用层职责）。
   `safeTs()` **不**对终止的 port 返回 `UINT64_MAX`——"对端终止后不阻塞全局时钟"
   的不变式由**应用层**保证（停止 poll 该 port、不计入 `min(safeTs)`）。
6. **bind-only 模式**：当 `peerRxEndpoint == localRxEndpoint`（barrier 用），
   收发共用同一个绑定 socket，不建立单独的 tx 连接。

### 4.2 MemMessage 报文格式

定义于 `framework/MemMessage.hh`。这是 **线格式 (wire format)**，是移植时
另一个需要与目标框架对齐的点（若目标框架有自己的报文头，需在此适配）。

```cpp
namespace framework {

static constexpr uint32_t kMaxPayloadSize      = 1024;
static constexpr uint32_t kMemMessageHeaderSize = 40;   // 固定 40 字节

enum class MemMessageType : uint32_t {
    CONTROL_SYNC    = 0,   // 时钟心跳（无载荷）
    TERMINATE       = 1,   // 关停通知
    PAYLOAD         = 2,   // 承载 CoherenceMessage（含一致性事务 + 栅栏控制）
};

// 40 字节，字段偏移固定
struct MemMessageHeader {
    uint64_t timestamp;   // off 0  : 消息可见时刻（虚拟时钟）
    uint32_t size;        // off 8  : 含头+载荷的总字节数
    uint32_t type;        // off 12 : MemMessageType
    uint32_t sourceId;    // off 16 : 源端点 gid (= node*K + socket)
    uint32_t _reserved0;  // off 20 : 保留（曾为 src_port）
    uint32_t targetId;    // off 24 : 目标端点 gid
    uint32_t _reserved1;  // off 28 : 保留（曾为 dst_port）
    uint64_t req_id;      // off 32 : 事务匹配 id
};
static_assert(sizeof(MemMessageHeader) == kMemMessageHeaderSize);

struct MemMessage {
    MemMessageHeader hdr;
    uint8_t payload[kMaxPayloadSize];

    void clear();
    bool isValid() const;                    // size >= 头长

    template<typename T> bool setPayload(const T& obj); // 拷入 + 更新 size
    template<typename T> const T* getPayload() const;   // 校验 size 后取出
    template<typename T> T* getPayload();
    void setRawPayload(const uint8_t* data, uint32_t len);
    const uint8_t* rawPayload() const;
    uint32_t payloadLen() const;             // size - 头长
};

} // namespace framework
```

> **消息类型精简**：`MemMessageType` 只有三个值（`CONTROL_SYNC / TERMINATE /
> PAYLOAD`）。曾经的 `BARRIER_REACHED` / `BARRIER_RELEASE` **已删除**——栅栏控制
> 现在作为 `PAYLOAD` 消息承载的 `CoherenceMessage`（其 `CoherenceMessageType`
> 含 `BarrierReached` / `BarrierRelease`，body 为 `UBBarrierBody{mask}`）。

**载荷**：数据消息（`PAYLOAD`）通过 `setPayload<CoherenceMessage>()` 装入一整个
`cc::glob::CoherenceMessage`（见第 7 节），接收方用 `getPayload<CoherenceMessage>()`
取出。`CoherenceMessage` 大小必须 `<= kMaxPayloadSize`（UBAdapter 构造时有
`fatal_if` 断言）。

### 4.3 PortEnvLoader 端点命名

`framework/Port.cc` 末尾的 `PortEnvLoader` 集中管理 IPC URL 拼装。**移植时若
改用其他寻址方式（如 TCP、共享内存 key），只改这里即可**，各模块通过工厂方法
拿 `PortParams` 而不自己拼 URL。

当前 IPC 基址：`static const std::string IPC_BASE = "/workspace/gem5/shared_ipc/ipc";`

| 工厂方法 | 用途 | localRx / peerRx |
|----------|------|------------------|
| `gem5UbioPort(gid)`        | gem5 侧的 ubio 端口 | rx=`_ubio_g_to_gem5_g`, tx=`_gem5_g_to_ubio_g` |
| `ubioGem5Port(gid, true)`  | ubio 侧的 gem5 端口 | rx=`_gem5_g_to_ubio_g`, tx=`_ubio_g_to_gem5_g` |
| `ubioNetPort(gid)`         | ubio 侧的 network 端口 | rx=`_networksim_mg_to_ubio_g`, tx=`_ubio_g_to_networksim_mg` |
| `nsimUbioPort(mod)`        | networksim 侧的 ubio 端口 | rx=`_ubio_m_to_networksim_mm`, tx=`_networksim_mm_to_ubio_m` |
| `barrierPort(n)`           | barrier（bind-only） | rx=tx=`/tmp/barrier_mn_p1` |

> 命名对称性是正确性关键：一端的 `localRx` 必须等于另一端的 `peerRx`。

### 4.4 Log 日志接口

`framework/Log.hh`：轻量 printf 风格日志，输出到 `stderr`，带模块名前缀。
外部模块统一用它，不依赖 gem5 的 `DPRINTF`。

```cpp
namespace framework {
void LogInfo (const char* module_name, const char* fmt, ...);  // "[mod] ..."
void LogError(const char* module_name, const char* fmt, ...);  // "[mod:ERROR] ..."
}
```

---

## 5. 时钟同步设计（PDES）

系统用 **保守式并行离散事件仿真** 保证跨进程因果性。每个进程维护一个 64 位
虚拟时钟 `tick`。核心不变式：

> **一个进程绝不把自己的虚拟时钟推进到超过"任一对端可能给它发消息的最早时刻"。**

三个关键量：

- **`linkLatency`（默认 100000）**：链路延迟。发送的消息 `timestamp = 发送时刻 + linkLatency`，
  即消息在对端最早于 `发送时刻 + linkLatency` 可见。
- **`syncInterval`（默认 100000）**：心跳/前瞻窗口。即使没有数据消息，也周期性
  发 `CONTROL_SYNC` 让对端知道"我至少推进到了这里"，从而对端可安全前瞻这么多。
- **`safeTs(curT)`**：本端某 Port 允许推进到的时刻上界。

### `safeTs` 语义

```cpp
uint64_t Port::safeTs(uint64_t curT) const {
    // 不再有 PEER_LOST/CLOSED/TERMINATING 特判（PortState 已删除）。
    // safeTs = min(对端最新时间戳, 自身前瞻窗口)。
    // _lastRxT 初值 0：启动前 receiveTimestamp()==0，safeTs()==0（min 的吸收元），
    // 本地时钟停在 0 直到对端首次 sync 抬升 _lastRxT。无需特判分支。
    uint64_t rxt = receiveTimestamp();
    uint64_t base      = (_lastSyncTs > 0) ? _lastSyncTs : curT;
    uint64_t syncBound = base + _syncInterval;
    return (rxt < syncBound) ? rxt : syncBound;
}
```

### per-port done 机制（对端终止后不冻结全局）

Port 对齐 2.1.3 删除了 `safeTs` 对终止 port 返回 `UINT64_MAX` 的特判。此前，终止
的 port 的 `safeTs` 返回 `+∞` 使其不钳住 `min(safeTs)`；删除后，"对端终止后不阻塞
全局时钟"的**责任转移到应用层**：

- 每个**使用 Port 的进程**为每个 port 维护 `bool done` 标志（或等价结构）。
- `recv` 收到 `TERMINATE`（现在作为 `kMessage` 返回）→ 置该 port 的 `done`。
- 之后**不再** poll 该 port（不 `emitSync`、不 `recv`、不把它的 `safeTs` 计入 `min`）。
- 等价于旧的 `UINT64_MAX`：done 的 port 不再约束全局时钟。

### 各进程主循环推进模式

所有 native 模块的主循环都是同一模板（以 ubio 为例，`ubio_main.cc`）：

```
bool gem5Done = false, netDone = false;
while (!(gem5Done && (netPort == nullptr || netDone))) {
    if (!gem5Done) gem5Port->emitSync(tick);      // 1. 心跳（done 的端口不发）
    if (netPort && !netDone) netPort->emitSync(tick);

    if (!gem5Done) pollAndProcess(gem5Port, ..., &gem5Done);  // 2. 排空可见消息
    if (netPort && !netDone) pollAndProcess(netPort, ..., &netDone);

    uint64_t minTs = UINT64_MAX;                   // 3. 求全局安全上界（跳过 done）
    if (!gem5Done) minTs = gem5Port->safeTs(tick);
    if (netPort && !netDone) minTs = std::min(minTs, netPort->safeTs(tick));

    if (minTs > tick) tick = minTs;                 // 4a. 可安全跳进
    else std::this_thread::yield();                 // 4b. 被卡 → 让出 CPU，绝不 ++tick
}
```

> `pollAndProcess` 收到 `TERMINATE`（`hdr.type == TERMINATE`）时置对应 `doneFlag`
> 并 `break` 停止排空该端口。

**为什么不 `++tick` 空转**：若本端超前对端太多，发出的消息 `timestamp` 会落在
对端"遥远的未来"，被对端缓存为 `_pending` 迟迟不交付，导致协议停滞甚至看似死锁。
因此被卡住时**只在墙钟时间上 yield 忙等**，不推进虚拟时钟。
（`std::this_thread::yield()` 允许；**禁止**用 `std::chrono` 墙钟时间戳做跨节点
同步——见移植约束 §10。）

### gem5 侧的对接（`UBAdapter::wakeup()`）

gem5 是事件驱动而非忙循环。`UBAdapter` 每次 `wakeup()`：
1. `_port->emitSync(curTick())` 发心跳；
2. 排空并处理收到的消息（收到 `TERMINATE` 显式跳过——gem5 不需要对 ubio 的
   terminate 做动作）；
3. 用 `safeT = _port->safeTs(curTick())` 决定下一次 `wakeup` 的调度时刻：
   - `safeT > curTick`：`schedule(event, safeT)` 推进；
   - `safeT <= curTick`（被卡）：在墙钟上 `yield` 忙等并持续排空对端消息，
     直到 `safeT` 抬升；有 2,000,000 次迭代的安全上限兜底再退回同 tick 重排。

---

## 6. 各模块详解

### 6.1 ubio (`modules/ubiomodule/`)

**角色**：每个 `(node, socket)` 平面一个 ubio 进程，是该 DSM 平面的
**home 目录 + 一致性控制器 + 跨节点路由器**。

**入口**：`ubio_main.cc`，参数：
```
ubio --node=<n> --socket=<s> --num-sockets=<K> --num-nodes=<N> [--fault-rules=<rules>]
```
- `--node/--socket`：本进程负责哪个平面（`gid = node*K + socket`）。
- `--num-sockets/--num-nodes`：全局布局（用于 gid 计算与栅栏广播范围）。
- `--fault-rules`：调试用故障注入规则（drop/duplicate 指定消息）。

**两个 Port**：`gem5Port`（连本节点 gem5）+ `netPort`（连 networksim）。

**per-port done 标志**：`gem5Done`、`netDone`，初值 false。收到 TERMINATE 置对应
标志，之后跳过该 port 的 emitSync/recv/safeTs。两个 port 都 done 后主循环退出
（优雅退出，不再依赖被 launcher kill；kill 仍是最终兜底）。

**主循环**：`emitSync`（done 的 port 跳过）→ `pollAndProcess`（done 的 port 跳过）→
`safeTs` 推进（done 的 port 不计入 min）。

**消息处理** (`pollAndProcess`)：
- `TERMINATE` → 置对应 `doneFlag`，`break`；
- `CONTROL_SYNC` → 跳过（作为普通 `kMessage`，按 `hdr.type` 识别）；
- `PAYLOAD` → 取出 `CoherenceMessage`：
  - `BarrierReached` → 记录、必要时向其他节点 plane-0 转发、集齐后回
    `BarrierRelease` 给本地 gem5；
  - 其它一致性事务 → 目标是本平面 (`dstNode==nid && dstSocket==sid`) 或属本地
    DSM 地址的 UBCC-ingress 请求 → 交 `UBCCController` 处理，产生响应回发；
  - 否则 → 经 `netPort` 转发到 `gid=dstNode*K+dstSocket`（跨节点走 networksim）。

**核心组件**：
- `UBCCController`（`UBCCController.{hh,cc}`）：目录状态机，处理
  Read/Recall/Invalidate/Upgrade/Writeback/Evict/Clear 等一致性事务。
- `ResidentDir`（`ResidentDir.{hh,cc}`）：常驻目录，用计数型 bloom filter 追踪
  sharer 集合。
- `NodeAddressMap`：`(node, socket)` ↔ 物理地址段映射，`isDsmAddr()` 判定某 PA
  是否属本平面。
- `ubio_base.hh`：在 `namespace cc` 下提供 `Tick/Addr/DataBlock/SimObject` 等最小
  类型垫片，使 UBCC 代码可脱离 gem5 头独立编译（原 `gem5_shim.hh`，已重命名以消除
  "依赖 gem5"的误导性命名）。
- 后备存储 schema（`BackstoreSchemaA/C`、`BackstoreTypes.hh` 等）：ubio 私有，
  gem5 对其后备存储元数据格式零感知。

### 6.2 networksim (`modules/networksim/`)

**角色**：跨节点 **交叉开关 (crossbar) 网络**。所有 ubio 之间的跨平面消息都发给
networksim，由它按目标 module 转发。

**入口**：`networksim_main.cc`，参数：`networksim <topology.json>`。

**拓扑文件**：`{"links": [[src_mod, src_port, dst_mod, dst_port, latency], ...]}`。
`run_multi.sh` 自动生成全连接 mesh（所有 module 两两相连，port=1，latency=100000）。

**内部结构**（Phase 3b 修复后）：
- `_ports`：**按 module id 索引**（每 module 一条到 ubio 的 IPC 通道）。
- `_linkLatency`：`map<module, latency>`，每个源 module 的转发延迟。
- `_fifo`：延迟队列，消息按 `readyTick` 排序，到点转发。
- `_donePorts`：`set<int>`，per-port done 集合。

**step()**：
1. 对每个**未 done**的 Port `emitSync` + 排空接收；收到 `TERMINATE` → 把该 module
   加入 `_donePorts` 并 `break` 停止排空它（不再 `_done=true; return` 整体退出）；
2. `_fifo` 队首 `readyTick <= tick` 的消息 → 按 `targetId`（=dst module）查 `_ports`
   转发。

**run()**：循环终止条件为 `_donePorts.size() == _ports.size()`（所有对端都
terminate）。`min(safeTs)` 跳过 `_donePorts` 中的 module。

> **移植注意**：路由**只按 module id**（`targetId`），拓扑里的 port 号只用于查
> 延迟，不参与端口选择。这是 Phase 3b 死锁修复的要点。

### 6.3 barrier_manager (`modules/barrier/`)

**角色**：跨节点栅栏协调器。

**入口**：`barrier_main.cc`，参数：`barrier_manager <num_nodes>`。

**逻辑**：每节点一个 bind-only Port（`barrierPort(n)`）。收到某 `mask` 的
`BarrierReached`（作为 `PAYLOAD` `CoherenceMessage`，来自各节点 socket-0 的
UBAdapter），记录到达集合；当到达数 `>= popcount(mask)` 时向 mask 内所有节点广播
`BarrierRelease`（同样是 `PAYLOAD` `CoherenceMessage`）。

**per-port done**：barrier 也维护 `donePorts` 集合。收到 `TERMINATE` 置 done，
`min(safeTs)` 跳过 done 的 port。barrier **不**因所有 port done 而退出（它靠被
launcher kill），`tick++` 逃逸保证不卡死。

> 注：多进程拆分模式下，栅栏也可经 ubio 之间转发（`ubio_main.cc` 内有
> BARRIER 转发逻辑），barrier_manager 是集中式备选路径。

### 6.4 gem5 侧 UBAdapter

**位置**：`gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.{hh,cc,py}`。

**角色**：gem5 进程内的传输适配器 SimObject，把 gem5 CHI 协议的一致性请求
序列化为 `CoherenceMessage` 经 `framework::Port` 发出，并接收响应。

**配置参数**（Phase 4 后全部走 SimObject param，不再读环境变量）：
- `node_id` / `socket_id`：本 adapter 所属平面；
- `num_nodes` / `num_sockets`：全局布局；
- `local_node`：本 gem5 进程拥有的节点（-1 = 单进程建所有节点）。

**关键方法**：
- `init()`：当 `local_node<0 || node_id==local_node` 时创建并绑定 Port
  （`gem5UbioPort(gid)`），注册退出回调（发 TERMINATE）与栅栏回调。
- `transportSend(msg)`：`allocateSendBuffer` → 填 `PAYLOAD` + `setPayload(msg)` →
  `send(buf)`（send 拿走所有权并 delete buf；`setPayload` 失败则 `delete buf` 后 return）。
- `transportRecv(type, reqId)`：轮询匹配指定类型/reqId 的响应。
- `wakeup()`：见第 5 节的时钟对接。drain 循环显式跳过 `TERMINATE`。

---

## 7. protocol 共享协议层

`protocol/` 存放 gem5 与外部模块 **共享的权威协议定义**。gem5 侧和
`modules/` 侧通过转发头引用同一份定义，保证线格式一致。

- **`CoherenceMessage.hh`**（命名空间 `cc::glob`）：一致性消息，是
  `MemMessage` 的 `PAYLOAD` 载荷内容。含：
  - `CoherenceMessageType`：ReadReq/ReadResp/Recall/Invalidate/Upgrade/...
    以及 `BarrierReached` / `BarrierRelease`（栅栏控制已并入此枚举，不再作为
    独立 `MemMessageType`）。
  - `CoherenceMessageHeader`：`type, srcNode/srcSocket, dstNode/dstSocket,
    homeNode/homeSocket, ingressSocket, requesterNode, targetNode, flags,
    homeLinePa, localLinePa, epoch, reqId, seqNum, enqueueTick, readyTick`。
  - `CoherenceMessageBody`：按类型的 tagged union（Read/Recall/Invalidate/...
    各自 body，以及 `UBBarrierBody{mask}`）。
  - `CoherenceMessage = { header h; body b; }`。
- **`NodeAddressMap.{hh,cc}`**：`(node, socket)` ↔ 物理地址段映射，
  `isDsmAddr()` 判定地址归属。

> `CoherenceMessage.hh` 的 `Tick/Addr` 已自包含（`using Tick = uint64_t;`），
> 不依赖 gem5 类型，可独立编译。移植时若需微调该结构，两侧转发头会同步生效。
>
> 后备存储相关头（`BackstoreTypes.hh`、`BackstoreSchemaA/C` 等）**不在 `protocol/`**，
> 已迁回 `modules/ubiomodule/`，是 ubio 私有实现（gem5 对后备存储元数据格式零感知）。

---

## 8. 编译方法

### 8.1 前置：ZeroMQ

第三方库在 `thirdparty/zeromq/{include,lib}`。移植目标框架时，把
`framework/Port.cc` 里的 `#include <zmq.hpp>` 与链接的 `-lzmq` 替换为目标框架的
头/库即可（接口层不变）。

### 8.2 framework 静态库

```bash
bash scripts/build_framework.sh
# 产出：
#   build/framework/lib/libframework.a
#   build/framework/include/framework/{Port.hh, MemMessage.hh, Log.hh}
```
`build_framework.sh` 用 `g++ -std=c++17 -O2 -I<root> -I<framework>
-I<zmq_include>` 编译 `Port.cc`、`Log.cc`，打包成 `libframework.a`，并把公共头
**安装到 `build/framework/include/framework/`**（gem5 SConscript 会把该路径加入
CPPPATH）。

或用独立 Makefile（本地开发）：`cd framework && make`。

### 8.3 三个 native 二进制

```bash
bash scripts/build_all.sh        # = build_ubio + build_networksim + build_barrier
# 产出：build/bin/{ubio, networksim, barrier_manager}
```
各脚本编译模式（以 ubio 为例）：
```
g++ -std=c++17 -O2 -Wall -pthread \
    -I modules/ubiomodule -I modules/ubiomodule/mem/ruby \
    -I build/framework/include -I <root> -I thirdparty/zeromq/include \
    modules/ubiomodule/ubio_main.cc <UBCC 实现 .cc...> \
    build/framework/lib/libframework.a \
    -L thirdparty/zeromq/lib -lzmq -lpthread -o build/bin/ubio
```
- `networksim`：只需 `networksim_main.cc` + libframework。
- `barrier_manager`：只需 `barrier_main.cc` + libframework。

### 8.4 gem5

在 Docker 内 scons 构建：
```bash
docker run --rm -v <repo>:/workspace -w /workspace/gem5 ubcc-dev:ubuntu20.04 \
  bash -c 'scons build/ARM/gem5.opt -j$(nproc)'
# 产出：gem5/build/ARM/gem5.opt
```
> 改了 SimObject 的 `.py` 参数或 `.cc` 后必须重编 gem5。framework 头变更后需
> 先 `build_framework.sh`（重装头）再重编 gem5。scons 缓存偶发陈旧时删
> `gem5/build/ARM/gem5.opt` 重编。
>
> **注意**：gem5 的 SConscript（`src/mem/ruby/protocol/chi/ep/SConscript`）把
> `repo_root` 算为 gem5 目录的上一级（即 `/workspace`），并从
> `build/framework/lib/libframework.a` 链接预编译的 framework。因此 Docker 挂载时
> **必须把整个根仓库挂为 `/workspace`**（而非仅挂 `gem5/` 子目录），以便 scons
> 找到 `libframework.a`。若挂载里包含 ccache 卷，需确保权限可写（否则 scons 报
> `ccache: Failed to create directory` 之类错误；去掉 ccache 挂载即可）。

---

## 9. 运行方法

### 9.1 端到端多进程测试（推荐）

```bash
# 在 Docker 内运行（挂载整个根仓库到 /workspace/gem5）
docker run --rm -v <repo>:/workspace/gem5 -w /workspace/gem5 ubcc-dev:ubuntu20.04 \
  bash -c 'mkdir -p shared_ipc && rm -rf shared_ipc/ipc_*; \
           bash tests/e2e/run_multi.sh <TC 编号...>'
```
例：
```bash
bash tests/e2e/run_multi.sh 3            # 只跑 TC3
bash tests/e2e/run_multi.sh 1 3 16       # 跑多个
bash tests/e2e/run_multi.sh 1 2 3 4 5 6 7 8 10 11 12 13 16 53   # 核心回归 14 项
```
`run_multi.sh` 会：生成拓扑 → 启动 barrier/nsim/gem5×N/ubio×(N*K) → 等所有 gem5
绑定 Port → 等 gem5 结束 → 聚合各节点 simout 校验 → 打印 `TCx PASSED/FAILED`。

**默认维度**：`NUM_NODES=3`，`NUM_SOCKETS=1`（部分 TC 如 32-35/39 自动升 2 socket）。
可用环境变量 `NUM_NODES` 覆盖。

### 9.2 日志位置

每次运行在 `logs/<时间戳>/` 下：
- `barrier.log` / `nsim.log` / `topo.json`
- `gem5_tc<N>_node<i>/{stdout,stderr}.log`
- `ubio_n<i>_s<j>/{stdout,stderr}.log`

调试端口收发：设 `EP_DEBUG_PORT=1` 打开 `[PORT-SEND]/[PORT-RECV]` 逐条 trace
（默认关闭，因为量极大）。

### 9.3 Port 冒烟测试

`framework/tests/port_sync_smoke.cc` 存在但**基于已删除的旧 Port 接口**
（`sendAllocateBuffer`、`pollHandshake`、5 参数构造器等），当前不可编译。
移植时如需该测试，须先按新接口重写。不列为正式回归项。

---

## 10. 移植指南（替换 Port 实现）

移植到目标框架的**最小改动面**：

### 必改
1. **`framework/Port.cc` 实现体**：把 ZeroMQ 的 `context_t/socket_t/message_t`
   替换为目标框架的连接/收发原语。**保持 `Port.hh` 中所有公开签名不变**：
   `init / terminate / allocateSendBuffer / send(MemMessage*) / recv /
   receiveTimestamp / safeTs / emitSync` + 只读 getter
   （`moduleId / portId / syncInterval / name`）。
   - 保持第 4.1 节列出的**语义约束**：发送所有权模型 B（alloc new、send delete、
     放弃分支调用方 delete）、发送打戳 `+linkLatency`、接收乱序缓冲 `kPendingFuture`、
     CONTROL_SYNC 与 TERMINATE 作为普通 `kMessage` 返回、bind-only 模式。
   - **不得**重新引入 `TxHandle`、`PortState`、`closeLocal`、`failClosed`、
     `isReady`、`state`、`abortSend`、无参 `send()`、`buffer()`、`_sendBufInUse`。
2. **`Port.hh` 私有成员**：`_ctx/_txSock/_rxSock`（`zmq::` 类型）替换为目标框架
   句柄类型；这是私有实现细节，不影响上层。移植时把 `Port.hh:13-16` 的 zmq
   前置声明改掉。保留 `_open` 布尔（资源生命周期标记）。
3. **`framework/Makefile` 与 `scripts/build_*.sh`**：替换 `-I.../zeromq/include`
   与 `-lzmq` 为目标框架的头/库路径。

### 可能微调
4. **`MemMessage.hh`**：若目标框架有自带报文头，需让 `MemMessageHeader` 与之
   兼容（当前 40 字节固定布局，`timestamp@0 / size@8 / type@12 / sourceId@16 /
   targetId@24 / req_id@32`）。改动后**所有二进制 + gem5 必须用同一份头重编**。
5. **`PortEnvLoader`（`Port.cc` 末尾）**：若寻址方式变化（TCP/共享内存/框架内
   句柄），改这里的 URL 拼装；保持"一端 localRx == 另一端 peerRx"的对称性。
6. **`IPC_BASE` 路径**（`Port.cc`）：当前硬编码
   `/workspace/gem5/shared_ipc/ipc`，按目标环境调整。

### 文件移动
7. 若目标框架要求特定目录布局，外部模块源码在 `modules/<模块>/`（main +
   实现同目录），可整体迁移；include 均为 ROOT 相对路径，移动位置不影响，只需
   更新 `scripts/build_*.sh` 中的路径与编译 `-I`。

### 移植后验证
- `bash scripts/build_framework.sh && bash scripts/build_all.sh`（native 编译通过）
- 重编 gem5
- `bash tests/e2e/run_multi.sh 1 3 16`（冒烟：本地 + 跨节点 + dual-upgrade）
- 全量 14 项回归：`run_multi.sh 1 2 3 4 5 6 7 8 10 11 12 13 16 53`

### 时钟同步不变式（移植时务必保持）
- 发送消息 `timestamp = curTick + linkLatency`；
- `safeTs` = `min(对端最新时间戳, 自身 lastSync + syncInterval)`，`_lastRxT` 初值 0
  （min 的吸收元，启动前停在 0）；
- **不再有**对终止 port 返回 `UINT64_MAX` 的特判——per-port done 的"不计入 min"责任
  由应用层保证（见 §5）；
- 收到 `timestamp > curT` 的消息必须缓存（`kPendingFuture`），不得提前交付；
- 收到 `CONTROL_SYNC` 与 `TERMINATE` 都作为 `kMessage` 返回，调用方按 `hdr.type`
  识别（无 `kSync` 状态、无 Port 内部 `failClosed`）；
- 被卡住时墙钟 `yield` 忙等，**绝不空转推进虚拟时钟**；
- **禁止**用 `std::chrono` 墙钟时间戳做跨节点同步（`yield` 允许，chrono 时间戳不允许）。

---

*本手册对应仓库状态：Port 对齐三项（2.1.5 / 2.1.1 / 2.1.3）全部完成、
后备存储解耦、外部模块 main 归位 modules/、gem5_shim 重命名 ubio_base 之后。*
