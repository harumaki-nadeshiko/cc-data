# CC-EP → 目标框架迁移指南

> 当前状态：框架层完成，独立模块可独立编译测试，gem5 内部仍为指针耦合
> 目标框架：基于 ZeroMQ 消息队列的多进程仿真架构
> 前置阅读：`docs/recovery/refactoring_plan.md`

---

## 0. 诚实状态声明

### 已完成（5 个独立测试全部 PASS）

| 组件 | 测试 | 通信方式 |
|------|------|---------|
| `framework/PseudoMemPort` | 本地队列 send/recv/poll 单元测试 | 内存队列 |
| `modules/ubiomodule/` | CoherenceMessage round-trip via PseudoMemPort | PseudoMemPort |
| `modules/networksim/` | 两个 UBIOModule 经 NetworkSim 通信 | PseudoMemPort |
| `thirdparty/zeromq/` | CoherenceMessage via ZMQ DEALER socket | ZeroMQ |
| gem5 E2E (56 TCs) | 内部编译测试，全部 PASS | **直接指针** |

### 未完成（gem5 内部仍用指针耦合）

gem5 进程内部的 UBAdapter↔UBIOModule↔EPBackend↔UBCCController 之间**仍然是直接 C++ 指针调用**，不是通过 PseudoMemPort 异步消息。

- `UBAdapter::_router->sendMessage()` — 仍然直接调用 UBIOModule
- `UBIOModule::_localAdapter->recvFromRouter()` — 仍然直接调用 UBAdapter
- `EPBackend::_ubcc->processOuterRequest()` — 仍然直接调用 UBCCController
- `UBIOModule::deliverToUbcc()` — 仍然直接调用 UBCCController（这个是正确的，UBCC 在 UBIOModule 进程内）

**根因**：gem5 SCons 构建系统能加 `framework/` include path 但需调通编译。且将所有同步调用转为异步 PseudoMemPort 需要重写 EPBackend 的 ~200 行 pending txn 匹配逻辑。这是 Phase 3c 的核心工作，在下述"待完成事项"中列为最高优先级。

---

## 1. 架构对照

### 当前 CC-EP（单 gem5 进程内）

```
┌─────────────────────────────────────────────┐
│  gem5 进程                                    │
│  ┌─────────┐  ┌──────────┐  ┌───────────┐   │
│  │UBAdapter│→│UBIOModule│→│UBIOModule  │   │
│  │         │←│(UBRouter)│←│(other node)│   │
│  └─────────┘  └──────────┘  └───────────┘   │
│       ↓              ↓                       │
│  EPBackend     UBCCController                │
│  EPRNF/EPSNF   ResidentDir                   │
└─────────────────────────────────────────────┘
```

### 目标框架（多进程 ZeroMQ）

```
gem5 进程 (Node i)          UBIOModule 进程 (Node i)       NetworkSim 进程
┌──────────────────┐       ┌──────────────────────┐       ┌────────────────┐
│ UBAdapter        │       │ UBCCController        │       │ ForwardTable   │
│  └PseudoMemPort──┼──ZMQ──┼─PseudoMemPort(gem5)   │       │                │
│                  │       │ PseudoMemPort(net)────┼──ZMQ──┼─PseudoMemPort  │
│ EPBackend        │       │ ResidentDir           │       │      ↕         │
│ MetaRNFController│       │ CoherenceMessage      │       │ (other nodes)  │
└──────────────────┘       └──────────────────────┘       └────────────────┘
```

---

## 2. 文件映射表

### 2.1 从 gem5 迁移到父 repo `modules/ubiomodule/`

| gem5 原位置 | 新位置 | 说明 |
|------------|--------|------|
| `gem5/src/.../ep/UBIOModule.{hh,cc}` | `modules/ubiomodule/UBIOModule.{hh,cc}` | 已拷贝，需解耦 gem5 |
| `gem5/src/.../ep/UBCCController.{hh,cc}` | `modules/ubiomodule/UBCCController.{hh,cc}` | 已拷贝 |
| `gem5/src/.../ep/ResidentDir.{hh,cc}` | `modules/ubiomodule/ResidentDir.{hh,cc}` | 已拷贝 |
| `gem5/src/.../ep/CoherenceMessage.hh` | `modules/ubiomodule/CoherenceMessage.hh` | 已重命名（原 UBMsg.hh） |
| `gem5/src/.../ep/CoherenceMessageQueue.hh` | `modules/ubiomodule/CoherenceMessageQueue.hh` | 已重命名 |
| `gem5/src/.../ep/BackstoreTypes.hh` | `modules/ubiomodule/BackstoreTypes.hh` | 已拷贝 |
| `gem5/src/.../ep/BackstoreOrganization.hh` | `modules/ubiomodule/BackstoreOrganization.hh` | 已拷贝 |
| `gem5/src/.../ep/BackstoreSchema{A,C}.{hh,cc}` | `modules/ubiomodule/BackstoreSchema{A,C}.{hh,cc}` | 已拷贝 |
| `gem5/src/.../ep/NodeAddressMap.{hh,cc}` | `modules/ubiomodule/NodeAddressMap.{hh,cc}` | 已拷贝 |

### 2.2 保留在 gem5 的文件

| 文件 | 原因 |
|------|------|
| `UBAdapter.{hh,cc,py}` | gem5 边界，持有 PseudoMemPort |
| `EPBackend.{hh,cc,py}` | gem5 外部协议处理 |
| `EPRNFController.{hh,cc}` | gem5 CHI 请求侧 |
| `EPSNFController.{hh,cc}` | gem5 CHI 响应侧 |
| `MetaRNFController.{hh,cc,py}` | gem5 内 CHI metadata I/O 代理 |
| `CHI_ubcc_framework.py` | gem5 配置入口 |

### 2.3 新建的模块

| 文件 | 用途 |
|------|------|
| `framework/PseudoMemPort.{hh,cc}` | 消息端口抽象（send/recv/poll） |
| `framework/PseudoManager.{hh,cc}` | 拓扑管理与消息路由 |
| `framework/PseudoMemPacket.hh` | 通用传输包（512B payload） |
| `framework/ZMQTransport.{hh,cc}` | ZeroMQ DEALER socket 封装 |
| `modules/networksim/NetworkSim.{hh,cc}` | 最小网络模拟器 |
| `modules/networksim/ForwardTable.{hh,cc}` | 拓扑转发表 |
| `thirdparty/zeromq/build.sh` | ZeroMQ 可移植构建脚本 |
| `thirdparty/zeromq/include/` | ZeroMQ 头文件 |
| `config/topology.json` | 网络拓扑配置示例 |

---

## 3. 编译指南

### 3.1 构建 UBIOModule 独立模块

```bash
# 1. 构建 ZeroMQ（一次性，产物在 thirdparty/zeromq/lib/）
cd thirdparty/zeromq && bash build.sh
# 输出: include/zmq.h, include/zmq.hpp, lib/libzmq.a

# 2. 构建 UBIOModule 独立进程
cd modules/ubiomodule
g++ -std=c++17 -I../.. -I../../thirdparty/zeromq/include \
    -pthread \
    -o ubiomodule main.cc \
    UBIOModule.cc UBCCController.cc ResidentDir.cc \
    NodeAddressMap.cc BackstoreSchemaA.cc BackstoreSchemaC.cc \
    ../../framework/PseudoMemPort.cc ../../framework/PseudoManager.cc \
    ../../framework/ZMQTransport.cc \
    ../../thirdparty/zeromq/lib/libzmq.a
```

### 3.2 构建 NetworkSim

```bash
cd modules/networksim
g++ -std=c++17 -I../.. -I../../thirdparty/zeromq/include \
    -pthread \
    -o networksim main.cc \
    NetworkSim.cc ForwardTable.cc \
    ../../framework/PseudoMemPort.cc ../../framework/PseudoManager.cc \
    ../../framework/ZMQTransport.cc \
    ../../thirdparty/zeromq/lib/libzmq.a
```

### 3.3 构建 gem5（保持不变）

```bash
docker run -v $(pwd):/workspace/gem5 -w /workspace/gem5/gem5 \
    ubcc-dev:mold bash -c 'scons build/ARM/gem5.opt -j32'
```

---

## 4. 配置指南

### 4.1 拓扑配置（`config/topology.json`）

```json
{
  "ports": [100, 200, 300],
  "links": [
    [100, 200, 5],
    [200, 300, 5],
    [100, 300, 10]
  ]
}
```

每个 link 为 `[src_port, dst_port, latency_ticks]`。

### 4.2 启动器（`launcher.py`，需创建）

```python
# 示例：启动 3 节点系统
import subprocess, os

NUM_NODES = 3

# 1. 启动 NetworkSim
netsim = subprocess.Popen(["./modules/networksim/networksim",
    "--topology", "config/topology.json",
    "--endpoint", "tcp://*:6000"])

# 2. 启动每个 UBIOModule
for n in range(NUM_NODES):
    port = 7000 + n
    ubiomodule = subprocess.Popen(["./modules/ubiomodule/ubiomodule",
        f"--node-id={n}",
        f"--gem5-endpoint=tcp://127.0.0.1:{port}",
        f"--net-endpoint=tcp://127.0.0.1:{port + 10}"])

# 3. 启动 gem5 实例
for n in range(NUM_NODES):
    gem5 = subprocess.Popen([
        "./gem5/build/ARM/gem5.opt",
        "configs/ruby/gem5_run.py",
        f"--node-id={n}",
        f"--ubiomodule-endpoint=tcp://127.0.0.1:{7000 + n}",
        "--tc=1"
    ])

# 4. 等待完成
for p in [netsim] + ubiomodules + gem5s:
    p.wait()
```

### 4.3 故障注入配置（`config/fault_rules.json`）

```json
{
  "rules": [
    {
      "match": {"msg_type": "ClearReq", "src": 0, "dst": 1},
      "action": "drop",
      "count": 1
    },
    {
      "match": {"msg_type": "InvalidateAck", "probability": 0.1},
      "action": "duplicate"
    },
    {
      "match": {"msg_type": "RecallResp"},
      "action": "delay",
      "delay_ticks": 5000
    }
  ]
}
```

---

## 5. 消息格式

### 5.1 PseudoMemPacket（传输层）

```
Byte 0-3:   type (uint32)
Byte 4-7:   src_id (uint32)
Byte 8-11:  dst_id (uint32)
Byte 12-15: payload_len (uint32)
Byte 16+:   payload[] (max 512 bytes)
```

传输层不解析 payload 含义。

### 5.2 CoherenceMessage（协议层，封装在 payload 中）

当前承载 20 种消息类型，最大 512B。包括：
- ReadReq / ReadResp
- RecallReq / RecallResp
- InvalidateReq / InvalidateAck
- ClearReq / ClearResp
- WritebackReq / WritebackResp
- EvictReq / EvictResp
- UpgradeReq / UpgradeResp / UpgradeDoneReq / UpgradeDoneResp
- QueryLineMetaReq / QueryLineMetaResp
- HomeWritebackNotify
- UpgradeAckNotify

详细字段定义见 `modules/ubiomodule/CoherenceMessage.hh`。

---

## 6. gem5 侧集成点

### 6.1 UBAdapter 持有 PseudoMemPort

```cpp
// UBAdapter.hh (已添加)
#include "framework/PseudoMemPort.hh"

class UBAdapter {
    pseudo::PseudoMemPort* _pseudoPort = nullptr;  // 可选

    void setPseudoPort(pseudo::PseudoMemPort* port) { _pseudoPort = port; }
    pseudo::PseudoMemPort* pseudoPort() const { return _pseudoPort; }
};
```

### 6.2 发送消息（替代原 sendReadReq → UBIOModule::sendMessage）

```cpp
// 原路径（gem5 内部）:
int result = _router->sendMessage(msg, latency);  // 同步

// 新路径（PseudoMemPort → ZeroMQ）:
PseudoMemPacket pkt;
pkt.type = PacketType::CoherenceMessage;
pkt.src_id = _portId;
pkt.dst_id = _ubiomodulePortId;
pkt.setPayload(coherenceMsg);
_pseudoPort->send(pkt);
```

### 6.3 接收消息（替代原 recvFromRouter）

```cpp
// 新路径:
void wakeup() {
    if (_pseudoPort && _pseudoPort->poll()) {
        PseudoMemPacket pkt;
        _pseudoPort->recv(pkt);
        const CoherenceMessage* msg = pkt.getPayload<CoherenceMessage>();
        if (msg) handleIncomingMessage(*msg);
    }
}
```

---

## 7. API 对照 — 当前 → 目标

| 当前调用 | 目标调用 | 变更 |
|----------|---------|------|
| `_router->sendMessage(msg, lat)` | `_port->send(pkt)` | 同步 → 异步 fire-and-forget |
| `_router->deliverToUbcc(msg, resp)` | `_port->poll() → recv() → dispatch` | 直接调用 → 消息循环 |
| `_ubcc->processOuterRequest(...)` | 封装为 CoherenceMessage → send | 本地调用 → 远程调用 |
| `_ubcc->processClear(...)` | 同上 | 同上 |
| `_backend->handleGrant(...)` | recv CoherenceMessage → 回调 | 同步返回 → 异步回调 |

---

## 8. 待完成事项

| 项目 | 优先级 | TLOC估 | 说明 |
|------|--------|--------|------|
| **gem5 SCons 接入 framework/** | **P0** | 3 | 调通 SConscript include path 使 gem5 能 `#include "framework/PseudoMemPort.hh"` |
| **UBAdapter 异步化** | **P0** | 30 | 14 处 `_router->sendMessage()` → `transportSend()` via PseudoMemPort |
| **UBIOModule 异步化** | **P0** | 20 | `deliverToAdapter` → PseudoMemPort, wakeup() 加 `transportRecv()` |
| **EPBackend pending txn map** | **P0** | 150 | 所有 `_ubcc->process*()` 同步调用 → CoherenceMessage 异步请求-响应 |
| **CHI_ubcc_framework.py 改造** | **P1** | 30 | 创建 PseudoManager, 连接 UBAdapter↔UBIOModule ports |
| **gem5_run.py** | **P1** | 20 | 单节点 gem5 入口（接收节点编号、UBIOModule endpoint） |
| **launcher.py** | **P1** | 40 | 统一启动三类进程的编排脚本 |
| **UBIOModule standalone 完整编译** | **P2** | 50 | gem5_shim.hh 替换真实 gem5 依赖，编译通过 |
| **FaultInjector 移植** | **P2** | 40 | 从 gem5 UBIOModule 移植到 framework/ |
| **ns3 adapter 替换** | **P3** | 200 | 替换 NetworkSim 为真实 ns3 网络模拟 |

---

## 9. 验证方法

### 9.1 各阶段验证

| 阶段 | 验证方法 | 预期 |
|------|---------|------|
| 单进程 PseudoMemPort | `modules/ubiomodule/main_test.cc` | PASS |
| NetworkSim 转发 | `modules/networksim/main_test.cc` | PASS |
| ZeroMQ 跨进程 | `thirdparty/zeromq/test_zmq.cc` | PASS |
| gem5 内改名后 | `test_e2e.py --tc=1-54,63-64` | 56/56 PASS |
| TLA+ 模型 | `verification/tla/run_tlc.sh` | 5/5 PASS |

### 9.2 迁移后验证

```bash
# 1. 单节点冒烟
python3 launcher.py --num-nodes=1 --tc=1

# 2. 多节点基础
python3 launcher.py --num-nodes=3 --tc=4

# 3. 故障注入
python3 launcher.py --num-nodes=3 --tc=47 --fault-config=config/fault_rules.json

# 4. 全量回归
for tc in $(seq 1 54); do
    python3 launcher.py --num-nodes=3 --tc=$tc
done
```
