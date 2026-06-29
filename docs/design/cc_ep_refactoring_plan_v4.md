# CC-EP V4 一体化重构方案

## 背景

TC2 E2E 测试调试过程中暴露出的架构债务：

- UBAdapter 空轮询自停，ReadResp 到达无人接收，消息被丢弃
- emitSync 每 ~1000 tick 一次，产生 CONTROL_SYNC 风暴拥塞 nsim
- retry 无上限导致 ReadReq 风暴（382K/6935），远超正常（1994）
- handleRemoteMiss 不检查 sendClear 返回值，Clear 走异步路径但 EPSNF 错误标记事务完成，GRANT_HANDSHAKE 永不解锁
- Gem5 侧源码级编译 UBCCController/UBIOModule，Port 暴露 ZMQ 内部实现

修复 retry=20000、wakeup 自停移除、Clear pending 闭环后 TC2 死锁解决，但架构耦合问题仍然存在。本次重构一次性解决以下三方面。

---

## 修订说明（v4.1，2026-06-29）

> 本文档初稿基于旧代码状态撰写。此后时钟同步与协议层若干 bug 已被修复（实现方式与背景描述不同，例如 Clear 闭环采用 pending-Clear fast-path + reqId 复用，而非 retry=20000；recall 脏数据交付修复等），TC2 已 PASS。

**重构总原则（确认）**：
- **正确性优先、保持当前已验证行为**：以当前代码实际状态为基线，重构不得回退任何已验证的修复；不回头对齐背景里描述的旧修法。
- **重构目标聚焦解耦**：在保证正确性的前提下，消除 UBIOModule/UBCCController 与 gem5 的耦合，使 gem5 与外部模块仅通过 Port 通信。
- **决策修订**：见下表「确认/修订」标注（决策 2 保留每 Port 独立 context；决策 7 terminate 不 flush 成立；决策 9 TxHandle 改为非 RAII 显式释放）。

**实施节奏（确认）**：
- Phase 1（Port 重构）+ Phase 2（编译系统）**合并为一次**提交。
- Phase 3（解耦）单独一次。
- Phase 4（物理删除）单独一次。

**前置准备（待执行）**：
- gem5 内 UBCC/UBIOModule **运行态引用审计**（区分真·运行态引用 vs forward-include 死代码），交由专门 subagent 完成，用以评估 Phase 3 实际工作量。
- 建立尽量宽的回归基线（TC1/TC3~11 当前 PASS/FAIL）。

---

## 一、最终架构

```
┌────────────────────────────────────────────────────────────────────┐
│  build/                                                            │
│  ├── bin/              ← 三个进程的最终二进制                        │
│  │   ├── ubio                 (UBIOModule + UBCCController)        │
│  │   ├── networksim           (网络延迟模拟)                         │
│  │   └── barrier_manager      (同步屏障)                            │
│  └── framework/        ← 共享静态库                                 │
│      ├── include/framework/{Port.hh, MemMessage.hh}                 │
│      └── lib/libframework.a                                         │
│                                                                     │
│  scripts/                                                           │
│  ├── build_framework.sh       ← 编译 libframework.a + 安装公共头     │
│  ├── build_ubio.sh            ← 编译 ubio，消费 framework           │
│  ├── build_networksim.sh      ← 编译 networksim                    │
│  ├── build_barrier.sh         ← 编译 barrier_manager               │
│  └── build_all.sh             ← 顺序调上述三个脚本                    │
│                                                                     │
│  tests/e2e/run_multi.sh       ← 固定路径 build/bin/{ubio,networksim,barrier_manager} │
└────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────┐      Port (ipc://)       ┌──────────────────────────────┐
│        Gem5 (1 node = 1 进程) │◄─────────────────────────►│        ubio process           │
│                              │                          │                              │
│  EPSNF ─► EPBackend          │                          │  UBIOModule                  │
│              │               │                          │  ├── 端口收发 (2 port:        │
│              ▼               │                          │  │      gem5_port +           │
│           UBAdapter          │                          │  │      network_port)         │
│  (编解码 + pending          │                          │  ├── 跨节点路由               │
│   completion + Port 轮询)    │                          │  ├── 定时队列/超时            │
│                              │                          │  └── transit forwarding       │
│  EPBackend: 事务级消息桥     │                          │       │                      │
│  MetaRNF: backstore metadata │                          │       ▼                      │
│  Gem5 内无 UBCC/UBIOModule  │                          │  UBCCController              │
│  定义，无 forward-include    │                          │  ├── ResidentDir (内嵌)       │
│  不允许同进程直达兜底        │                          │  │   └── cache-line 粒度       │
└──────────────────────────────┘                          │  ├── 请求仲裁 (先落表者胜)    │
                                                           │  ├── single-flight            │
                                                           │  │   (per-address 单 outstanding)│
                                                           │  └── invalidation fanout      │
                                                           │                              │
                                                           │  CoherenceMessage:            │
                                                           │  {txnId, srcNode/srcSocket,   │
                                                           │   dstNode/dstSocket,           │
                                                           │   homeNode/homeSocket, addr,   │
                                                           │   reqType, data[64], sideband} │
                                                            └──────────────────────────────┘

### 架构决策

| # | 决策 | 内容 |
|---|------|------|
| 1 | Port 生命周期 | 一次性 init，不可复用；断了全局 fail |
| 2 | ZMQ context 所有权 | **每 Port 独立持有自己的 `zmq::context_t`（与原框架一致，确认保留）** |
| 3 | EnvLoader 定位 | 环境变量提供 endpoint 模板，node/portId/role 由各程序原有逻辑提供 |
| 4 | endpoint 命名统一 | 全部纳入 EnvLoader 统一管理（含 barrier） |
| 5 | 旧接口兼容 | 一步到位删除旧接口 |
| 6 | syncWindow 归属 | 不入 PortParams，保留为独立 runtime 配置 |
| 7 | terminate 语义 | 发一次 best-effort TERMINATE 通知对端，随后立即本地清理，不做业务队列 flush。**前提：terminate 只发生在「全部正常结束」或「非正常异常退出」两种情况，此时丢弃在途消息无所谓（确认）** |
| 8 | nsim 批量建 port | 每 port 单独调 EnvLoader，任一失败则全局 fail-fast 并回滚已建 port |
| 9 | 发送缓冲管理 | 删除 `_sendBufInUse`，改为显式句柄 `TxHandle`。**修订：TxHandle 不采用 RAII 自动 cancel；由发送方在适当时机手动 `send()` 或 `cancel()` 释放该发送槽。未释放即视为编程错误（debug 断言/泄漏告警），不依赖析构兜底** |
| 10 | endpoint 格式 | localRxEndpoint / peerRxEndpoint 必须是完整 `ipc://` URL |
| 11 | .a 职责边界 | 仅 `framework/Port.cc + ZMQChannel.cc` 入库 |
| 12 | .a 产物位置 | 统一 `build/framework/lib/libframework.a` |
| 13 | gem5 链接方式 | ep/SConscript 局部修改（CPPPATH/LIBPATH/LIBS） |
| 14 | 脚本依赖 | 独立 `build_framework.sh`，四个模块脚本消费产物；framework 缺失时脚本报错退出 |
| 15 | 产物发现 | 固定路径 `build/bin/`，不设环境变量覆盖 |
| 16 | 导出边界 | 仅 `build/framework/include/framework/{Port.hh, MemMessage.hh}` 作为公共头 |
| 17 | Gem5 搜索口径 | 严格：源码/脚本/SConscript/Python 全消 UBCC/UBIOModule（仅允许注释提及） |
| 18 | 外部模块主名 | 两者保留：UBIOModule 是主类，持有 `UBCCController*` 指针，分别负责路由和目录 |
| 19 | ResidentDir 归属 | 完全外移到 modules/ubiomodule/，作为 UBCCController 内嵌成员 |
| 20 | 进程粒度 | 1 节点 = 1 Gem5 进程（dual-socket 同进程内多 UBAdapter） |
| 21 | 同进程直达兜底 | 不允许，必须纯 Port 通信 |
| 22 | UBIOModule/UBCCController 职责切分 | UBIOModule 管路由/端口/transit；UBCCController 管目录状态/仲裁/fanout |
| 23 | Port 最小消息单元 | 事务级 envelope：txnId、srcNode/srcSocket、dstNode/dstSocket、homeNode/homeSocket、addr、reqType、data[64]、sideband |
| 24 | 目录粒度 | 保持 cache-line 粒度（现有 `UBCCController.hh:619` 已内嵌 `ResidentDir _directory`） |
| 25 | 请求阻塞模型 | per-address 单 outstanding，`processOuterRequest` 遇已有 `_outstandingReqs` 返回 BUSY(-1) |
| 26 | 双写冲突仲裁 | 目录仲裁：先落 `_outstandingReqs[linePa]` 表者胜，后者收到 BUSY(-1) 后重试 |
| 27 | EP-RNF 角色 | 不作为 CHI Fwd 数据源，`pickSharerForSnoop()` 排除 EP-RNF 优先级；recall 改为真实 ReadShared/ReadUnique 闭环 |

---

## 二、Port 接口重构

### 接口定义

```cpp
class Port {
public:
    void init(const PortParams& params);
    bool send(MemMessage* msg);
    MemMessage* recv(uint64_t curT, ReceiveStatus* status);
    MemMessage* allocateSendBuffer(uint64_t timestamp);
    void terminate();
    void emitSync();
    uint64_t safeTimestamp();
};
```

2.1 不暴露 `zmq::context_t`、`zmq::socket_t` 等内部类型。

### 配置体系

```
PortParams (EnvLoader 加载，静态身份)
├── name             — 端口名称（用于日志）
├── moduleId         — 模块标识（gem5/ubio/networksim/barrier）
├── portId           — 端口编号
├── localRxEndpoint  — 本地接收端点（完整 ipc:// URL）
└── peerRxEndpoint   — 对端接收端点（完整 ipc:// URL）

PortRuntime (独立配置，不进 PortParams)
├── syncWindow       — 同步窗口（tick）
├── syncInterval     — 同步间隔（tick）
└── entrySize        — 缓冲条目大小（字节）

PortConfig = PortParams + PortRuntime
```

2.2 `syncWindow/syncInterval` 不进 `PortParams`，保留为独立 runtime 配置，由各模块上层按需设置。

### 生命周期

```
INIT ─► READY ─► TERMINATING ─► CLOSED
              (收 PEER_LOST → CLOSED)
```

- `init(PortParams)` 后进入 READY
- `terminate()` 被调用：发一次 best-effort TERMINATE 消息到对端，随后立即本地清理（不对业务队列 flush）→ CLOSED
- 收到 TERMINATE 消息：停止新发送，清空本地资源 → CLOSED
- terminate 后禁止继续 send

### TxHandle（显式发送句柄，非 RAII）

```cpp
class Port::TxHandle {
public:
    MemMessage* buffer();       // 访问待填充的发送缓冲
    bool send();                // 提交发送，仅可调用一次；调用后句柄失效
    void cancel();              // 显式放弃，释放发送槽；调用后句柄失效
    // 无析构自动 cancel：释放由发送方在 send()/cancel() 中显式完成
};
```

2.3 `sendAllocateBuffer` 改名为 `allocateSendBuffer`，返回 `TxHandle`。删除 `_sendBufInUse`。
2.4 **修订（不采用 RAII）**：发送槽的释放由发送方在适当时机**手动**调用 `send()` 或 `cancel()` 完成；不依赖析构自动 cancel。
  - 约束：`TxHandle` **不得跨 event/wakeup 边界持有**——在 gem5 事件驱动 + inline yield-wait 模型下，必须在同一次 wakeup 内完成 `allocate→fill→send/cancel`。
  - 若一个发送槽在再次 `allocate` 时仍未释放：视为编程错误，触发 debug 断言或泄漏告警（release 下记录并强制回收），而非静默析构兜底。

### port 创建与回滚

2.5 每个 port 单独调 `PortEnvLoader::load(params)` 加载配置。任一 port 加载/初始化失败时，回滚已创建的全部 port（调用每个已建 port 的 `closeLocal()`）。

### 文件变更

| 文件 | 变更摘要 |
|------|----------|
| `framework/MemMessage.hh` | 新增 `TerminatePayload{reason, exit_code, sender}` |
| `framework/Port.hh` | 新增 `PortParams`, `PortRuntime`, `PortConfig`；新增 `TxHandle`, `terminate()`, `closeLocal()`；增加 TERMINATING/CLOSED 状态 |
| `framework/Port.cc` | RAII 发送槽；future COH_MSG 进 `_pending`；`_lastRxT` 哨兵；`terminate(immediate)`；禁止 terminate 后 send |
| `framework/PortEnvLoader.hh` (新) | 加载 PortConfig，校验 endpoint 为完整 ipc:// URL |
| `framework/PortEnvLoader.cc` (新) | per-port load；任一失败返回 error string |
| `gem5/UBAdapter.hh` | 固定 TransportMode；基于 PortConfig 的 init；区分 response 与 async ingress |
| `gem5/UBAdapter.cc` | PortEnvLoader 建 port；发送失败回滚；删除 busy-poll；收到 TERMINATE 置 fail-fast |
| `tools/ubio/ubio_main.cc` | PortEnvLoader 建 port；失败回滚全部 port；收到 TERMINATE 停止新流量并退出 |
| `modules/networksim/networksim_main.cc` | 配置化建 port；`pollAllPorts()`；出队重戳虚拟时间；terminate 收敛退出 |
| `tools/launcher.py` | 生成完整 ipc:// endpoint URL |

---

## 三、编译系统

### 目录结构

```
build/
├── bin/
│   ├── ubio
│   ├── networksim
│   └── barrier_manager
└── framework/
    ├── include/framework/
    │   ├── Port.hh
    │   └── MemMessage.hh
    ├── lib/libframework.a
    ├── obj/
    │   ├── Port.o
    │   └── ZMQChannel.o
    └── manifest.txt

scripts/
├── build_framework.sh
├── build_ubio.sh
├── build_networksim.sh
├── build_barrier.sh
└── build_all.sh
```

### 各脚本规范

**`scripts/build_framework.sh`**

```bash
# 编译 framework/Port.cc + framework/ZMQChannel.cc
# 产出:
#   build/framework/lib/libframework.a
#   build/framework/include/framework/{Port.hh, MemMessage.hh}
#   build/framework/manifest.txt
```

**`scripts/build_ubio.sh`**

```bash
# 前置: build/framework/lib/libframework.a（缺失→exit 1）
# 编译: ubio_main.cc, UBCCController.cc, ResidentDir.cc,
#        BackstoreSchemaA.cc, BackstoreSchemaC.cc, NodeAddressMap.cc
# 链接: -lframework -lzmq -lpthread
# 产出: build/bin/ubio
```

**`scripts/build_networksim.sh`**

```bash
# 前置: build/framework/lib/libframework.a
# 编译: networksim_main.cc
# 链接: -lframework -lzmq
# 产出: build/bin/networksim
```

**`scripts/build_barrier.sh`**

```bash
# 前置: build/framework/lib/libframework.a
# 编译: barrier_main.cc
# 链接: -lframework -lzmq
# 产出: build/bin/barrier_manager
```

**`scripts/build_all.sh`**

按顺序调用 `build_ubio.sh`、`build_networksim.sh`、`build_barrier.sh`。

### gem5 侧

`gem5/src/mem/ruby/protocol/chi/ep/SConscript`:

```python
framework_prefix = "#build/framework"
env.Append(CPPPATH=[framework_prefix + "/include"])
env.Append(LIBPATH=[framework_prefix + "/lib"])
env.Append(LIBS=["framework"])
# 移除 Source(File("...framework/Port.cc"))
# framework 缺失 → raise SCons.Errors.StopError
```

### run_multi.sh

- 二进制路径固定为 `build/bin/ubio`、`build/bin/networksim`、`build/bin/barrier_manager`
- 移除内嵌 g++ 编译，改为 `ensure_tools()` 检查二进制是否存在
- 缺失时输出：`请先运行 scripts/build_framework.sh && scripts/build_all.sh`

---

## 四、解耦

### 原则

4.1 Gem5 目录源码/脚本/SConscript/Python 中 **不得出现 UBCCController、UBIOModule** 运行态引用（注释除外）。
4.2 所有目录/仲裁/路由逻辑外移到 `modules/ubiomodule/`。
4.3 Gem5 与外部模块**仅通过 Port 消息通信**，无直接函数调用，无同进程直达兜底。

### 消息流

#### Read Miss

```
EPSNF
  → EPBackend (recvRequest)
  → UBAdapter (编解码, insert pending completion)
  → Port::send(ReadReq)
  → ubio UBIOModule::recv → route to UBCCController
  → UBCCController::processOuterRequest(linePa)
      若 _outstandingReqs[linePa] 已有 → BUSY(-1)
      否则分配 outstanding → 查 ResidentDir → 决定 local/remote
  → ReadResp 原路返回: UBCCController → UBIOModule::send → Port → gem5 UBAdapter
  → UBAdapter::handleResponse → EPBackend → EPSNF
```

#### Recall

```
Home UBCC → UBIOModule → RecallReq → requester gem5 UBAdapter
  → EPBackend → EPRNF → HN-F → L2 eviction
  → RecallResp(data, dirty, dataReturned) 原路返回 Home UBCC
```

#### Clear

```
RECALL.DONE 与 GRANT_HANDSHAKE 分离
Clear 匹配 (linePa, requesterNode, baseEpoch, reqId)
sendClear 返回 false → handleRemoteMiss 返回 -2 → EPSNF 重试 Clear
```

#### 双写冲突

```
N0, N1 同时对同一 linePa 请求 UpgradeReq / WBUnique
→ 先后到达 Home UBCC → processOuterRequest:
    首个到达者 → 分配 _outstandingReqs[linePa] → 受理
    后者 → _outstandingReqs[linePa] 已有 → BUSY(-1) → 重试
→ 先落表者胜
```

### Gem5 侧变更

| 文件 | 变更 |
|------|------|
| `EPBackend.hh/.cc` | 删除本地 UBCC 依赖；仅保留 txn 级消息桥、MetaRNF、Clear tuple 校验 |
| `UBAdapter.hh/.cc` | 仅做消息编解码 + pending completion + Port 轮询；删除 router/本地 UBCC 语义 |
| `EPRNFController.cc` | SnpShared/SnpSharedFwd/SnpOnceFwd → fatal-grade unreachable；recall → ReadShared/ReadUnique 闭环 |
| `CHI-cache-funcs.sm` | `pickSharerForSnoop()` 排除 EP-RNF 优先级 |
| `CHI-cache-actions.sm` | sole-EP-RNF 时走 non-Fwd/non-DCT fallback |
| `CHI_ubcc_framework.py` | 固化 HN-F DSM downstream = EP-SNF 单入口；不实例化 UBIOModule |
| `MetaRNFController.hh/.cc` | 作为 backstore metadata I/O 唯一 gem5 侧入口 |
| `SConscript` | 不再编译 gem5 侧 UBCC/UBIOModule 残留文件 |

#### forward-include 删除清单

| 文件 | 删除 |
|------|------|
| `EPBackend.cc` | `#include "mem/ruby/protocol/chi/ep/UBCCController.hh"` |
| `UBAdapter.cc` | `#include "mem/ruby/protocol/chi/ep/UBCCController.hh"` |
| `UBAdapter.hh` | `class UBCCController;`、`class UBIOModule;`、`setRouter()`、`router()`、`_router` |
| `UBAdapter.py` | `router = Param.UBIOModule(...)` |
| `CHI_ubcc_framework.py` | `UBIOModule(...)` 实例化、`nd['ubiomodules']` |

### modules/ubiomodule/ 组件规格

| 组件 | 冻结职责 |
|------|----------|
| **UBIOModule** | 端口收发、跨节点路由、定时队列/超时重传、transit forwarding。2 port（gem5_port + network_port） |
| **UBCCController** | 目录状态（含内嵌 ResidentDir）、请求仲裁、per-address single-flight、invalidation fanout |
| **ResidentDir** | cache-line 粒度，不改变语义，补 slot/control API 对接 UBCCController |
| **CoherenceMessage** | 事务级：`txnId`, `srcNode/srcSocket`, `dstNode/dstSocket`, `homeNode/homeSocket`, `addr`, `reqType`, `data[64]`, `sideband{flags, dataSource, authEpoch, pendingInvMask}` |
| **NodeAddressMap** | homeNode/homeSocket 计算、跨 socket transit 映射 |

---

## 五、物理删除（一次性收尾）

首轮"停编译/停引用/停实例化"稳定验证后，从 gem5 目录下物理删除以下文件：

- `gem5/.../ep/UBIOModule.hh`、`UBIOModule.cc`、`UBIOModule.py`
- `gem5/.../ep/UBCCController.hh`、`UBCCController.cc`
- `gem5/.../ep/ResidentDir.cc`
- `gem5/.../ep/BackstoreSchemaA.cc`、`BackstoreSchemaC.cc`
- `gem5/.../ep/NodeAddressMap.cc`
- `gem5/.../ep/UBCCProtocolIF.hh`
- `gem5/.../ep/CoherenceMessageQueue.hh`
- `SConscript` 只保留 EPBackend/EPRNF/EPSNF/MetaRNF/UBAdapter 目标

同步完成：
- EPBackend 完全移除 `_ubcc` 观念，backstore 改为 `ubio UBCC → BackstoreReadReq → gem5 UBAdapter → EPBackend → MetaRNF` 消息往返
- 跨 socket 时 `dst_module/dst_port` 表示下一跳，`dstNode/dstSocket` 表示最终协议目标

---

## 六、实施顺序

```
1. Port 重构 → 2. 编译系统 → 3. 解耦 → 4. 物理删除
```

- **Phase 1 (Port)**: infrastructure 层，所有模块依赖；完成后各模块都通过 PortEnvLoader + TxHandle 使用 Port
- **Phase 2 (Build)**: 建立 `build/` 统一产物体系，所有模块通过脚本构建而非 scons 内嵌编译
- **Phase 3 (Decouple)**: 基于干净的 Port 接口和统一编译体系，把 UBCC/UBIOModule 完全迁出 gem5
- **Phase 4 (Delete)**: 前三步稳定后，物理删除 gem5 目录下的残留文件，完成物理层面的彻底解耦
