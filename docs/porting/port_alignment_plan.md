# Port 对齐方案文档（2.1.1 / 2.1.3 / 2.1.5）

> 目的：把 `framework::Port` 的接口与生命周期模型对齐真实目标框架（参考实现见
> `docs/all.cpp`），以便移植。本文档供**审核**与**执行 agent 照做**。执行 agent
> 请严格按"逐文件改法"操作，不要自行发挥；每完成一项须按"验证"小节回归。
>
> 本轮涵盖三项，按**推荐执行顺序**：
> 1. **2.1.5** 去 wall-clock（最小，几乎只是清理）
> 2. **2.1.1** 去发送句柄 TxHandle（缓冲区不由句柄管理）
> 3. **2.1.3** 去 PortState（端口可用状态由应用层维护）
>
> 每项一个独立 commit。**禁止**把三项混在一个 commit 里。

---

## 0. 背景与全局约束

### 0.1 参考实现（真实框架）的 Port 形态

来自 `docs/all.cpp`，权威接口只有：

```cpp
bool init(params);
bool terminate();
MemHeader* alloc_buf(u64 ts);      // 我们当前叫 allocateSendBuffer
bool send(msg);                    // 直接传 msg 指针，无句柄
u64  receiveTimestamp();
u64  safeTs(u64 curT);
bool emitSync(u64 curTick);
MessageHeader* receive(u64 curT, ReceiveStatus* status);   // 我们叫 recv
```

参考实现的关键事实（务必对照）：

- **无 TxHandle**：`alloc_buf(t)` 返回缓冲区指针，调用方直接填字段，再 `send(msg)`。
  发送后缓冲区由 Port（或其下的 channel）自行回收，调用方不持有句柄、不显式
  release/cancel。见 `all.cpp:41-45`（`sendToSim`）、`all.cpp:102-104`（`emitSync`）。
- **无 PortState 枚举**：参考实现的 Port 内部**不维护** INIT/READY/PEER_LOST/CLOSED
  这类状态机。端口是否可用由**应用层**判断（应用层为每个 port 维护必要状态）。
- **时钟同步不依赖墙钟时间戳**：卡住时用 `std::this_thread::yield()` 让出 CPU
  （`all.cpp:62`），但**不**用 `std::chrono::` 取墙钟时间做跨节点同步。yield 允许，
  chrono 时间戳不允许。

### 0.1b 当前进程退出机制现状（理解 2.1.3 的关键背景）

经代码核验（`tests/e2e/run_multi.sh` + 各模块主循环），**当前架构下**：
- launcher (`run_multi.sh`) **只等待 gem5 进程结束**，之后**主动 kill** ubio/nsim/barrier。
- 各 native 模块（ubio/nsim/barrier）**不靠 TERMINATE 优雅退出**，而是靠被 kill。
- 当前 `recv` 对 TERMINATE 返回 `kEmpty/nullptr`（内部 `failClosed()`），导致 ubio 的
  `done=true`（`ubio_main.cc:644`）与 nsim 的 `_done=true`（`networksim_main.cc:114`）
  **都是从不执行的死代码**。
- 各模块在"等待被 kill"期间不冻结全局时钟，**唯一**靠 `safeTs()` 对 terminate 后 port
  返回 `UINT64_MAX`（即本文档 2.1.3 要删除的那段特判）。

> 这意味着：2.1.3 删掉 `safeTs` 的 `UINT64_MAX` 特判后，**必须**由应用层显式实现
> "terminate 后的 port 不计入 `min(safeTs)`"，否则死锁（详见 R5）。2.1.3 的应用层改法
> 就是把上述**死代码分支激活**为正确的 per-port done 机制。

### 0.2 三条硬约束（用户明确）

- **C1（去 wall-clock）**：允许 `std::this_thread::yield()`；**禁止**用 `std::chrono::`
  取墙钟时间戳做跨节点同步。
- **C2（去句柄）**：核心是**缓冲区不由句柄（TxHandle）管理**。`allocateSendBuffer`
  返回什么类型不重要（可返回 `MemMessage*`）。
- **C3（去 PortState）**：Port 内部**不**维护端口可用状态。"是否可用/是否已 terminate"
  由**应用层**为每个 Port 维护。真实实现中，收到 TERMINATE 后应用层给对应标志置位，
  之后**不再 poll 该 port**（既不 recv 也不把它的 safeTs 计入 min）。

### 0.3 不变式（三项都必须保持，改完回归验证）

1. **发送打戳**：`allocateSendBuffer(t)` 必须把 `hdr.timestamp = t + linkLatency`、
   历史方案曾要求预置`hdr.sourceId = moduleId`；当前合同已变更为只预置size/timestamp，sourceId/targetId由应用显式设置。
2. **接收乱序缓冲**：`recv` 遇 `timestamp > curT` 的消息必须缓存为 pending 并返回
   `kPendingFuture`；直到 curT 追上才交付。
3. **safeTs 语义**：`min(receiveTimestamp(), (lastSyncTs? lastSyncTs : curT) + syncInterval)`；
   `_lastRxT` 初值 0（吸收元，启动前不推进）。
4. **TERMINATE 后不阻塞全局时钟**：某 port 的对端 terminate 后，该 port 的 safeTs
   不得再把全局 `min(safeTs)` 钳在旧值（否则空闲节点冻结全局）。**2.1.3 改动后，
   此不变式由应用层保证**（不再 poll 该 port、不计入 min）。

### 0.4 涉及文件总览（全仓库）

传输层核心：
- `framework/Port.hh`、`framework/Port.cc`

调用方（8 个发送点 + 若干 recv/状态查询点）：
- `modules/ubiomodule/ubio_main.cc`
- `modules/networksim/networksim_main.cc`
- `modules/barrier/barrier_main.cc`
- `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc`、`UBAdapter.hh`

测试：
- `framework/tests/port_sync_smoke.cc`

> **注意**：`gem5/` 是独立 git 子模块，其改动单独 commit + push；根仓库改动单独
> commit + push。参见 §5 提交规范。

---

## 2.1.5 去 wall-clock（先做，最小）

### 现状考察

经全仓库审计（`rg -n "chrono|::now\(\)|steady_clock|system_clock|WallMs|nowWall"`），
**跨节点时钟同步当前已不依赖墙钟时间戳**：
- `modules/ubiomodule/ubio_main.cc` 主循环卡住时用 `std::this_thread::yield()`
  （行 ~896），无 chrono 调用；但文件顶部 `#include <chrono>`（行 14）**未被使用**。
- `modules/networksim/networksim_main.cc` 卡住时 `yield()`（行 ~180），无墙钟。
- `gem5/.../UBAdapter.cc` stall 忙等用 `waitIters < kWaitCap`（迭代计数，行 ~1218-1246）
  + `yield()`（行 ~1224），**不看墙钟时间**。
- 历史上的 `nowWallMs()` / `_lastRxWallMs` / `peerStaleMs()` 已在早前 Phase 3a 删除。

> 结论：C1（禁止 chrono 墙钟时间戳同步）**已满足**。本项唯一遗留是删除未使用的
> `<chrono>` include。`kWaitCap` 迭代上限是纯迭代计数、非墙钟，**保留不动**（它只是
> 兜底安全网，不参与时钟推进决策）。

### 目标态

- `ubio_main.cc` 不再 `#include <chrono>`（若确认无任何 chrono 用法）。
- 其它 chrono include 仅存在于**测试文件**（`port_sync_smoke.cc`、`main_test.cc`）与
  **非同步用途**（如 `PseudoMemPort.cc` 的 `wait_for` 是伪内存端口、运行时未用），
  这些**不在本项范围**，保留。

### 逐文件改法

**文件：`modules/ubiomodule/ubio_main.cc`**

1. 执行前先确认该文件确实无 chrono 用法：
   ```
   rg -n "chrono|::now\(|duration|milliseconds|microseconds|seconds|steady_clock|system_clock" modules/ubiomodule/ubio_main.cc
   ```
   预期只匹配到第 14 行的 `#include <chrono>` 本身。**若出现任何实际使用**（如
   `std::chrono::...::now()`），**停止并上报**——那属于范围外，需重新评估。
2. 删除第 14 行 `#include <chrono>`。

### 不要动

- `gem5/.../UBAdapter.cc` 的 `kWaitCap` / `waitIters` / `yield()`：保留。
- `modules/networksim/networksim_main.cc` 的 `yield()`：保留。
- `framework/tests/port_sync_smoke.cc`、`modules/networksim/main_test.cc`、
  `modules/networksim/NetworkSim.cc`、`framework/PseudoMemPort.cc` 的 chrono：保留
  （测试/非运行时/非同步用途）。

### 验证

- 编译 native：`bash scripts/build_framework.sh && bash scripts/build_all.sh`。
- 冒烟：`bash tests/e2e/run_multi.sh 1 3 16`（须全 PASSED）。
- 本项不涉及 gem5，**无需重编 gem5**。

### commit

根仓库单独 commit：
```
2.1.5: remove unused <chrono> include from ubio_main

Cross-node clock sync already uses yield()+iteration counting, not wall-clock
timestamps (nowWallMs/peerStaleMs were removed in Phase 3a). The only remnant
was an unused <chrono> include in ubio_main.cc. kWaitCap in UBAdapter is a pure
iteration-count safety net (no wall clock) and is left unchanged.
```

---

## 2.1.1 去发送句柄 TxHandle（模型 B：alloc 每次 new，send(msg) 内部 delete）

### 参考实现（对齐目标，唯一接口）

来自 `docs/all.cpp`（`sendToSim` 40-48、`emitSync` 98-107）：

```cpp
MemMessage* alloc_buf(u64 ts);       // 分配一块待填充的传输层包，失败返回 0/nullptr
bool        send(MemMessage* msg);   // 以 msg 为参数提交；成败都拿走 msg 所有权
```

参考的调用范式（**没有** abort、**没有** 占用标志、**没有** 二次 buffer()）：

```cpp
auto* msg = port.alloc_buf(curTick());
if (!msg) return false;              // 分配失败：直接返回，无需释放
/* 填 msg 字段 */
if (!port.send(msg)) return false;   // 发送失败：直接返回；msg 所有权已交给 send
```

### 所有权模型（本项采用：模型 B）

- **`alloc_buf(ts)` 每次 `new` 一块新的 `MemMessage`**，打戳后返回其指针。这块 buffer 是
  一个"待填充的传输层包"，与 zmq 队列 entry 是两回事（zmq entry 是发送时的载体）。
- **`send(MemMessage* msg)` 拿走 `msg` 的所有权**：把 `*msg` 整包 `memcpy` 进一个 zmq
  message 发送，**然后 `delete msg`**（无论 zmq 发送成功或失败都 delete —— msg 已交给
  send，调用方不得再用）。
- **send 前放弃**（分配了但决定不发，如 `setPayload` 失败）：**由应用层自己 `delete msg`**
  后 return。Port 不提供 abort/free 接口（参考没有）。
- **无 `_sendBufInUse` 单发送槽标志**（参考没有）：每次 alloc 都是独立的新 buffer，天然
  不存在"上一次没 send"的占用冲突。
- **`alloc_buf` 返回 nullptr**：仅在极端分配失败时（如 `new(std::nothrow)` 返回空）；正常
  运行几乎不发生。调用方仍须按参考写 `if(!msg) return`（分配失败无 buffer 可 delete）。

> 内存管理小结（执行 agent 必须严格遵守）：
> - `alloc_buf` **new**，`send` **delete**（成功/失败都 delete）。
> - alloc 成功但走到"不 send 的 return 分支" → **调用方 delete**。
> - alloc 失败（nullptr）→ **无 buffer，不 delete，直接 return**。
> - 每条 alloc 出来的 msg 有且仅有一次 delete（在 send 内 或 在放弃分支）。禁止双重 delete。

### 现状（要改掉的句柄模型）

当前发送采用**显式句柄** `TxHandle`（`framework/Port.hh:47-63`、`framework/Port.cc:21-37`）：
`allocateSendBuffer` 返回 `TxHandle*`（内部单个 `_sendBuf` + `_sendBufInUse` 占用标志 +
复用句柄对象 `_txHandle`）；`TxHandle::buffer()` 取缓冲、`send()` 提交、`cancel()` 放弃。
**要全部删除**：`TxHandle` 类、`_sendBufInUse`、`_txHandle`、`releaseSendSlot`。

**8 个业务调用点**（`X=allocateSendBuffer(t); buf=X->buffer(); 填; X->send()/cancel()`）：

| # | 文件:行 | 当前用法要点 |
|---|---------|----------|
| 1 | `modules/networksim/networksim_main.cc:135` | `fh=alloc; if(fh){buf=fh->buffer(); *buf=pf.msg; buf->hdr.timestamp=ts; fh->send();}` |
| 2 | `modules/barrier/barrier_main.cc:87` | `rh=alloc; if(rh){rel=rh->buffer(); 填 barrier PAYLOAD; rh->send();}` |
| 3 | `modules/ubiomodule/ubio_main.cc:245`（sendCoh） | `h=alloc; buf=h?h->buffer():nullptr; if(!buf) return false; 填; if(!setPayload){return false}; h->send();` |
| 4 | `modules/ubiomodule/ubio_main.cc:683`（barrier 转发） | `fh=alloc; if(fh){fwd=fh->buffer(); *fwd=*m; 改 ts/targetId; fh->send();}` |
| 5 | `modules/ubiomodule/ubio_main.cc:699`（barrier release） | `rh=alloc; if(rh){rel=rh->buffer(); 填; rh->send();}` |
| 6 | `gem5/.../UBAdapter.cc:122`（sendBarrierReached） | `h=alloc; if(!h) return; buf=h->buffer(); 填 barrier; h->send();` |
| 7 | `gem5/.../UBAdapter.cc:165`（transportSend） | `h=alloc; if(!h) return false; buf=h->buffer(); 填; if(!setPayload){h->cancel(); return false;} if(!h->send()) return false;` |
| 8 | `framework/Port.cc:263`（emitSync 内部） | `h=alloc; if(!h) return false; buf=h->buffer(); 填 SYNC; h->send();` |

> 调用点 #3（setPayload 失败直接 return，未 cancel——旧句柄模型下靠下次 allocate 覆盖）与
> #7（setPayload 失败 `h->cancel()`）是"send 前放弃"场景。模型 B 下**它们必须改成
> `delete buf; return`**（见下逐点）。

### 目标态（对齐参考的最终接口）

Port 的发送接口收敛为**恰好两个**，与参考签名一致：

```cpp
MemMessage* Port::allocateSendBuffer(uint64_t ts);  // new + 打戳，返回指针（失败 nullptr）
bool        Port::send(MemMessage* msg);             // memcpy 进 zmq 发送 + delete msg
```

**不得**新增 `abortSend`/`send()`(无参)/`_sendBufInUse`/`buffer()`。send 前放弃由应用层
`delete`。

### 逐文件改法

#### 文件：`framework/Port.hh`

1. **删除整个 `TxHandle` 类**（约 47-63 行，含其上方注释块）。
2. public 区把
   ```cpp
   TxHandle* allocateSendBuffer(uint64_t timestamp);
   ```
   改为
   ```cpp
   // Allocate a NEW transport packet stamped at `timestamp` (hdr.timestamp =
   // Historical sourceId preset removed; current iface presets timestamp/size only.
   // caller fills. Ownership passes to send(); if the caller decides NOT to
   // send, it must delete the returned pointer itself. Returns nullptr only on
   // allocation failure.
   MemMessage* allocateSendBuffer(uint64_t timestamp);
   // Send `msg` (memcpy into a zmq message) and DELETE msg (takes ownership,
   // deletes on both success and failure). Returns false on transport failure.
   bool send(MemMessage* msg);
   ```
3. private 区：
   - 删除 `friend class TxHandle;`
   - 删除 `void releaseSendSlot();`、`bool doSend();`（doSend 内容并入新的 `send(msg)`；
     或把 doSend 保留为 `bool doSend(const MemMessage* msg)` 私有 helper——**推荐直接并入
     `send(msg)`**，减少一层）。
   - 删除成员 `MemMessage _sendBuf;`、`bool _sendBufInUse = false;`、`TxHandle _txHandle{this};`。
     > 模型 B 不再有 Port 内部单缓冲；每次 alloc 都是独立堆对象。

#### 文件：`framework/Port.cc`

1. **删除 `TxHandle` 三个方法定义**（`buffer()`/`send()`/`cancel()`，约 21-37 行）。
2. 重写 `Port::allocateSendBuffer`（约 159-170 行）：
   ```cpp
   MemMessage*
   Port::allocateSendBuffer(uint64_t timestamp)
   {
       if (_state != PortState::READY) return nullptr;   // 2.1.3 会把 _state 判断改掉
       MemMessage* msg = new (std::nothrow) MemMessage();
       if (!msg) return nullptr;
       msg->clear();
       msg->hdr.timestamp = timestamp + _linkLatency;
       // sourceId/targetId are application-owned; do not infer them from Port.
       msg->hdr.size      = sizeof(MemMessageHeader);
       return msg;
   }
   ```
   > 当前不变式仅保留timestamp+linkLatency与size；sourceId/targetId不得由传输层隐式填写。
   > `new (std::nothrow)` 使分配失败返回 nullptr 而非抛异常，匹配参考 `if(!msg)` 契约。
   > 需要 `#include <new>`（若未包含）。
3. 用 `send(MemMessage* msg)` 替换原 `doSend()`（把 zmq 发送逻辑迁进来，操作 `*msg` 而非
   `_sendBuf`，末尾 delete）：
   ```cpp
   bool
   Port::send(MemMessage* msg)
   {
       if (!msg) return false;
       // 2.1.3 会把下面的 _state 判断改成 !_open
       if (_state == PortState::PEER_LOST || _state != PortState::READY) {
           delete msg;                 // 拿走所有权：失败也要 delete
           return false;
       }
       bool ok = false;
       auto& sock = _txSock ? *_txSock : *_rxSock;
       try {
           zmq::message_t z(msg->hdr.size);
           std::memcpy(z.data(), msg, msg->hdr.size);
           if (portDebugEnabled())
               std::fprintf(stderr, "[PORT-SEND] %s type=%u ts=%lu dst=%u\n",
                            _name.c_str(), msg->hdr.type, msg->hdr.timestamp,
                            msg->hdr.targetId);
           sock.send(z, zmq::send_flags::none);
           ok = true;
       } catch (const zmq::error_t& e) {
           std::fprintf(stderr, "[PORT-SEND-ERR] %s: %s\n", _name.c_str(), e.what());
           ok = false;
       }
       delete msg;                     // 成败都 delete（所有权已转移）
       return ok;
   }
   ```
4. `terminate()`（约 108-128 行）：删除对 `_sendBufInUse`/`releaseSendSlot` 的引用
   （原 `if (_txSock && _sendBufInUse) { releaseSendSlot(); }` 整段删除——模型 B 没有单缓冲
   占用概念）。terminate 内部**自建**一个 TERMINATE 消息直接 zmq 发送的逻辑**保留不变**
   （它不走 allocateSendBuffer，是独立的一次性 zmq send）。
5. `emitSync()`（约 258-272 行）改为：
   ```cpp
   bool Port::emitSync(uint64_t curTick) {
       if (_lastSyncTs > 0 && curTick - _lastSyncTs < _linkLatency) return true;
       MemMessage* msg = allocateSendBuffer(curTick);
       if (!msg) return false;
       msg->hdr.type = static_cast<uint32_t>(MemMessageType::CONTROL_SYNC);
       msg->hdr.size = sizeof(MemMessageHeader);
       if (send(msg)) { _lastSyncTs = curTick; return true; }  // send 内部已 delete
       return false;
   }
   ```

#### 调用方统一改法（模式）

- 旧：
  ```cpp
  framework::TxHandle* h = port->allocateSendBuffer(t);
  if (!h) { /* fail */ }
  MemMessage* buf = h->buffer();
  /* 填字段 */
  h->send();          // 或 h->cancel();
  ```
- 新（send 成功路径）：
  ```cpp
  framework::MemMessage* buf = port->allocateSendBuffer(t);
  if (!buf) { /* fail: 无 buffer，直接处理 */ }
  /* 填字段 */
  port->send(buf);    // send 内部 delete buf
  ```
- 新（send 前放弃路径，如 setPayload 失败）：
  ```cpp
  if (!buf->setPayload(msg)) { delete buf; return false; }   // 应用层自己 delete
  ```

逐点：

- **#1 `networksim_main.cc:135`**：
  `MemMessage* buf = it->second->allocateSendBuffer(_tick);`；删 `buf=fh->buffer()`；
  `*buf=pf.msg; buf->hdr.timestamp=ts;` 保留；`fh->send();` → `it->second->send(buf);`。
- **#2 `barrier_main.cc:87`**：`MemMessage* buf = ports[ni]->allocateSendBuffer(tick);`；
  删 `rel=rh->buffer()`（用 buf）；`rh->send();` → `ports[ni]->send(buf);`。
- **#3 `ubio_main.cc:245`（sendCoh）**：`MemMessage* buf = port->allocateSendBuffer(tick);`；
  `if(!buf) return false;` 保留；**`setPayload` 失败分支改为 `delete buf; return false;`**；
  `h->send();` → `port->send(buf);`。
- **#4 `ubio_main.cc:683`（barrier 转发）**：`MemMessage* fwd = netPort->allocateSendBuffer(m->hdr.timestamp);`
  `if(!fwd) {...}`; `*fwd=*m; 改 ts/targetId;`；`fh->send();` → `netPort->send(fwd);`。
- **#5 `ubio_main.cc:699`（barrier release）**：`MemMessage* rel = gem5Port->allocateSendBuffer(tick);`
  `if(!rel){...}`; 填；`rh->send();` → `gem5Port->send(rel);`。
- **#6 `UBAdapter.cc:122`（sendBarrierReached）**：`framework::MemMessage* buf = _port->allocateSendBuffer(curTick());`
  `if(!buf) return;` 填 barrier PAYLOAD + setPayload；`h->send();` → `_port->send(buf);`。
  > 若此处也有 setPayload，失败分支同样 `delete buf; return;`。
- **#7 `UBAdapter.cc:165`（transportSend）**：`framework::MemMessage* buf = _port->allocateSendBuffer(curTick());`
  `if(!buf) return false;` 填；**`setPayload` 失败：`h->cancel()` → `delete buf; return false;`**；
  `if(!h->send())` → `if(!_port->send(buf))`。
  > 注意 `_port->send(buf)` 失败后**不要再 delete buf**（send 已 delete），直接 `return false`。

#### 文件：`framework/tests/port_sync_smoke.cc`

执行前 `rg -n "TxHandle|allocateSendBuffer|->send\(|->buffer\(|->cancel\(" framework/tests/port_sync_smoke.cc`。
若使用旧句柄接口，按上述模式改（alloc 返回 MemMessage*，send(buf)，放弃则 delete）。

### 风险与注意

- **R1（内存泄漏/双重释放）★本项最高风险**：模型 B 用裸 `new/delete` 管理每个发送包。
  规则：**每个 alloc 出来的 msg 恰好 delete 一次**——在 `send(msg)` 内（成败都 delete），
  或在"放弃分支"由调用方 delete。执行时逐点核对：
  - 每个 alloc 成功后，所有可能的 return 路径要么调用了 `send(buf)`（含 delete），要么
    `delete buf`。**不得两者都做**（send 后再 delete = 双重释放）。
  - #3、#7 的 setPayload 失败分支必须 `delete buf`（原来是 return / cancel）。
  - #7 的 `send(buf)` 失败后**不得**再 delete（send 已 delete）。
- **R2（勿再调用 buffer()）**：所有 `x->buffer()` 删除，用 alloc 返回值。TxHandle 删掉后
  漏改会编译报错，易检测。
- **R3（send 是成员方法，传对的 msg 与 port）**：`port->send(buf)` 里 buf 必须是**该 port**
  的 allocateSendBuffer 返回的那个指针（#1 是 `it->second`，#2 是 `ports[ni]`）。
- **R4（性能）**：每次发送一次 new/delete。当前发送频率下可接受；参考实现同样每包一 msg。
  若后续有性能顾虑，可另行引入池化（**本项不做**，保持与参考一致的简单模型）。

### 验证

- 编译 native + 重编 gem5（UBAdapter 改了）。
- **建议用 ASan 跑一次冒烟**（模型 B 涉及裸 new/delete，ASan 能抓泄漏/双重释放）：
  native 二进制加 `-fsanitize=address` 重编后跑 `run_multi.sh 1 3 16`（若环境支持）。
- 冒烟：`run_multi.sh 1 3 12 13 16`（12/13 覆盖 barrier 发送路径）。
- 全量回归：`run_multi.sh 1 2 3 4 5 6 7 8 10 11 12 13 16 53`（须 14/14 PASSED）。

### commit（分两个：gem5 子模块 + 根仓库）

gem5 子模块：
```
2.1.1 (gem5): drop TxHandle; use alloc/send(msg) with delete-on-send

UBAdapter transportSend/sendBarrierReached now use MemMessage* buf =
_port->allocateSendBuffer(...) and _port->send(buf) (send takes ownership and
deletes buf). The setPayload-failure path deletes buf itself before return.
Matches the reference alloc_buf/send(msg) model (no handle, no abort).
```
根仓库：
```
2.1.1: remove TxHandle; alloc_buf/send(MemMessage*) with delete-on-send

Align Port send API with the reference framework:
- allocateSendBuffer(ts) news a stamped MemMessage and returns it (nullptr on
  alloc failure); the caller fills it.
- send(MemMessage* msg) memcpys it into a zmq message, sends, and deletes msg
  (ownership transfer; deletes on both success and failure).
- send-before-commit abandonment (e.g. setPayload failure) deletes the buffer
  in the caller.
Delete TxHandle, _sendBuf, _sendBufInUse, _txHandle, releaseSendSlot, doSend.
Updated all senders (ubio/networksim/barrier) and emitSync.
```

---

## 2.1.3 去 PortState

### 现状考察

`framework/Port.hh:30`：
```cpp
enum class PortState { INIT, READY, TERMINATING, CLOSED, PEER_LOST };
```
`Port` 内部成员 `PortState _state = PortState::INIT;`（约 109 行）。

`_state` 的**全部读写点**（`framework/Port.cc`）：

| 位置 | 用途 |
|------|------|
| `init():47` | `if (_state != INIT) return false;`（防重复 init） |
| `init():88` | `_state = READY;`（成功） |
| `init()` 失败路径 | 经 `closeLocal()` → `_state = CLOSED` |
| `failClosed():96` | `_state = PEER_LOST;`（收到 TERMINATE 时调用） |
| `closeLocal():101-102` | `if(_state==CLOSED) return; _state=CLOSED;` |
| `terminate():109-110` | `if(_state!=READY){closeLocal();return;} _state=TERMINATING;` |
| `safeTs():142-144` | `if(PEER_LOST||CLOSED||TERMINATING) return UINT64_MAX;`（★关键正确性） |
| `allocateSendBuffer():162` | `if(_state != READY || _sendBufInUse) return nullptr;` |
| `doSend():175-176` | `if(_state==PEER_LOST) return false; if(_state!=READY) return false;` |
| `recv():199-202` | `if(PEER_LOST||CLOSED) return kEmpty; if(_state!=READY) return kEmpty;` |
| `recv():243-247` | 收到 TERMINATE → `failClosed()` → kEmpty |

对外**没有** `state()`/`isReady()`/`failClosed()` 被业务代码调用（除注释）。审计：
```
rg -n "PortState|isReady\(|failClosed\(|\.state\(\)|->state\(\)" modules gem5/src/mem/ruby/protocol/chi/ep
```
预期业务代码零调用（仅 UBAdapter.cc 注释提到 PEER_LOST）。**执行前必须重跑此审计确认。**

### ★ 关键正确性机制（务必理解后再改）

`safeTs()` 里 `if(PEER_LOST||CLOSED||TERMINATING) return UINT64_MAX;` 是一个**必须保留
其效果**的机制：对端 terminate 后，该 port 的对端虚拟时钟视为"已完成 = +∞"，从而
它不再把全局 `min(safeTs)` 钳住 → 空闲/早退节点不会冻结仍在运行的节点（不变式 §0.3.4）。

去掉 PortState 后，**这个"+∞"语义必须转由应用层实现**（用户确认："真实实现中
terminate 确实会给类似的标志置位，并在之后不去 poll 它"）。

### 目标态（对齐参考）

- **删除 `PortState` 枚举**及 `Port::_state` 成员。
- Port 内部不再自维护可用状态。`init` 的防重复用一个**局部/最小**手段替代（见下）。
- "对端是否已 terminate / 该 port 是否可用"由**应用层**为每个 port 维护一个标志。
- **应用层主循环规则**（每个使用 Port 的进程都要遵守）：
  1. 维护 `bool portDone[p]`（或等价结构），初值 false。
  2. `recv` 若返回一条 `TERMINATE`（见下"recv 语义调整"），把该 port 的 `portDone` 置真。
  3. 之后**不再 poll 该 port**（不 recv、不 emitSync）。
  4. 计算 `min(safeTs)` 时**跳过** `portDone` 为真的 port（等价于旧的返回 UINT64_MAX）。

### recv 语义调整（配合去 state）

旧 `recv` 收到 TERMINATE 时内部 `failClosed()`（置 PEER_LOST）并返回 `kEmpty`——应用层
**看不到** TERMINATE。**这正是当前 ubio `done=true`、nsim `_done=true` 分支成为死代码的
根因**（`while(m && st==kMessage)` / `while(m=recv())` 都因 recv 返回 kEmpty/nullptr 而
不进入）。去 state 后，应用层**必须**能看到 TERMINATE 才能置 done。两种对齐方案：

- **方案 T-A（推荐，改动小且贴参考）**：`recv` 收到 TERMINATE 时，**返回该消息**
  （status = `kMessage`），让应用层用 `hdr.type == TERMINATE` 识别并置 `portDone`。
  与 2.1.2 里"SYNC 作为 kMessage 返回、由应用层按 type 处理"完全同构。
- **方案 T-B**：新增 `ReceiveStatus::kTerminated`。**不推荐**（刚在 2.1.2 精简掉 kSync，
  再加状态与方向相悖）。

> **采用 T-A。** 各应用层 recv 循环已有 `if(type==TERMINATE)` 分支（见下调用点），
> 只需把"置 done 标志 + 停止 poll 该 port"补进去。

### Port 内部改法

> **同时删除 `closeLocal`**（用户审核："没有 closeLocal"）。参考只有 init/terminate 两个
> 生命周期方法。`closeLocal` 现有职责是"reset sockets/ctx（释放 zmq 资源）"，这本就是
> unique_ptr 的 RAII 能自动完成的事。改法：把资源释放**内联到析构函数与 terminate**，
> 删除 `closeLocal` 方法与其对外声明；应用层对 `closeLocal` 的调用一并去除（见下）。

#### 文件：`framework/Port.hh`

1. 删除 `enum class PortState {...};`（行 30）。
2. 删除成员 `PortState _state = PortState::INIT;`（约 109 行）。
3. 删除对外声明 `bool isReady() const;`、`PortState state() const;`、
   `void failClosed(const char*);`、`void closeLocal();`（凡存在者）。
4. 保留 `void terminate();`（生命周期需要；内部不再依赖 _state）。
5. 新增一个**最小私有布尔**替代 `init` 防重复与 socket 有效性判断：
   ```cpp
   bool _open = false;   // sockets bound/connected & usable; set by init, cleared on close
   ```
   > 说明：C3 要求 Port 不维护"对端是否 terminate/可用"这类**协议级**状态；但"socket
   > 是否已成功 init"是**资源生命周期**事实，不是协议状态。保留一个 `_open` 布尔用于
   > 避免对未 init / 已释放的 socket 收发（防崩溃），符合参考精神。**这是资源事实，
   > 不是 PEER_LOST 那种协议状态。** 若审核认为连 `_open` 也应外置，见 §2.1.3 附录 B。

#### 文件：`framework/Port.cc`

> 先引入一个私有 helper（**不对外**，仅供析构/terminate 内联复用；命名随意，如
> `_releaseSockets()`），把原 `closeLocal` 的资源释放体搬进去：
> ```cpp
> void Port::_releaseSockets() {          // private, not in the public API
>     if (!_open) return;
>     _open = false;
>     if (_rxSock) _rxSock.reset();
>     if (_txSock) _txSock.reset();
>     if (_ctx)    _ctx.reset();
> }
> ```
> （若审核连私有 helper 都不想要，可把这段直接内联进析构与 terminate 两处；helper 只是
> 避免重复。它不是公开接口，不违反"没有 closeLocal"。）

1. `init()`：
   - `if(_state != INIT) return false;` → `if(_open) return false;`（防重复 init）。
   - 结尾 `_state = READY;` → `_open = true;`。
   - 失败路径原 `closeLocal()` → `_releaseSockets()`。
2. **析构 `~Port()`**：原 `closeLocal();` → `_releaseSockets();`。
3. `terminate()`：
   - 开头 `if(_state!=READY){closeLocal();return;}` → `if(!_open){_releaseSockets();return;}`。
   - 删除 `_state = TERMINATING;`（无此状态）。
   - 中段原 `if(_txSock && _sendBufInUse){ releaseSendSlot(); }` **整段删除**（2.1.1 已移除
     `_sendBufInUse`/单缓冲概念）。
   - terminate 内部自建 TERMINATE 消息直接 zmq send 的逻辑**保留**（不走 allocateSendBuffer）。
   - 结尾原 `closeLocal();` → `_releaseSockets();`。
4. `failClosed()`：**删除整个方法**（唯一职责是置 PEER_LOST，现由应用层接管）。
5. `safeTs()`：
   - **删除** `if(PEER_LOST||CLOSED||TERMINATING) return UINT64_MAX;` 整段。
   - 剩下的 `min(receiveTimestamp(), lookahead)` 逻辑**保留不变**。
   - > 删除后，"对端 terminate 后不阻塞全局"的责任**完全**转移到应用层（不再 poll 该
     > port + 不计入 min）。见下"应用层改法"。
6. `allocateSendBuffer()`：`if(_state != READY) return nullptr;` → `if(!_open) return nullptr;`
   （2.1.1 已把此处改为只判 `_state`，本项再改成 `!_open`）。
7. `send(MemMessage* msg)`（2.1.1 引入）：把其中的
   `if(_state==PEER_LOST || _state != READY){ delete msg; return false; }` →
   `if(!_open){ delete msg; return false; }`。
8. `recv()`：
   - 开头 `if(PEER_LOST||CLOSED) return kEmpty; if(_state!=READY) return kEmpty;` →
     `if(!_open){ st=kEmpty; return nullptr; }`。
   - TERMINATE 分支（约 243-247 行）：**改为返回该消息**（方案 T-A）：
     ```cpp
     if (tmp.hdr.type == static_cast<uint32_t>(MemMessageType::TERMINATE)) {
         // Deliver to caller; the application marks this port done and stops
         // polling it (Port no longer tracks peer-terminated state).
         _lastRxT = (uint64_t)tmp.hdr.timestamp;   // 已在上方统一更新，可省
         st = ReceiveStatus::kMessage;
         static thread_local MemMessage result; result = tmp; return &result;
     }
     ```
     > 删除原 `failClosed(...)` 调用。TERMINATE 也应遵守时间戳可见性吗？参考对所有消息
     > 一视同仁按 timestamp 排序。**保守做法**：TERMINATE 通常 timestamp 较小可立即可见；
     > 为简单起见，让 TERMINATE 走与普通消息相同的"timestamp>curT 则 pending"路径即可
     > （即：不在此提前 return，而是 fall-through 到统一的可见性判断）。**推荐**：TERMINATE
     > 提前返回（如上），因为 terminate 是控制信号、宜尽快让应用层停止 poll。二者都可，
     > 执行时**采用提前返回**并在 commit message 注明。

### 应用层改法（每个使用 Port 的进程）

统一模式：为每个 port 维护 `done` 标志；recv 到 TERMINATE 置 done；之后跳过该 port
的 poll 与 safeTs。

> **另需清理 `closeLocal` 调用点**：`closeLocal` 已删除（见上）。全仓库审计
> `rg -n "closeLocal" framework modules gem5/src/mem/ruby/protocol/chi/ep`，把应用层调用
> 去掉。已知点：`modules/ubiomodule/ubio_main.cc:615`（init 失败时
> `gem5Port->closeLocal(); netPort->closeLocal();`）——这是 init 失败的清理，删掉这两句
> 即可（Port 析构会自动 `_releaseSockets()`；且 init 失败路径内部已释放自身资源）。
> 若发现其它 `closeLocal` 调用点，一并去除（改为依赖析构 RAII）。

> ★★★ **关键前提更正（执行 agent 必读）** ★★★
>
> 经代码核验，**当前 native 模块（ubio/nsim/barrier）并不靠 TERMINATE 优雅退出，而是
> 靠 launcher（`run_multi.sh`）在所有 gem5 结束后主动 `kill` 它们**（见 `run_multi.sh`
> 约 231-232 行 `kill $UBIO_PIDS` / `kill $NSIM_PID`）。具体地：
> - `run_multi.sh` 只 `wait` gem5 进程（约 208-211），gem5 全退后 kill ubio/nsim/barrier。
> - **当前 `recv` 收到 TERMINATE 时返回 `kEmpty + nullptr`**（Port.cc failClosed 路径），
>   因此 ubio 的 `while(m && st==kMessage)` **根本不进入**，`ubio_main.cc:644` 的
>   `done=true` 是**死代码**；nsim 的 `while(m=p->recv())` 同理不进入，`networksim_main.cc:114`
>   的 `_done=true` 也是**死代码**。
> - ubio/nsim 在"等待被 kill"期间**不冻结全局时钟**，唯一靠的就是 `safeTs()` 对
>   terminate 后的 port 返回 `UINT64_MAX`（本项要删的那段）。
>
> 因此，删掉 `safeTs` 的 UINT64_MAX 特判后，**必须**由应用层显式实现"terminate 后的
> port 不计入 min(safeTs)"，否则该 port 的 safeTs 会永久钳住全局 → 死锁/超时（R5）。
> 这正是用户描述的真实做法："terminate 给标志置位，之后不去 poll 它"。
> 下面各应用层改法据此重写，**目标是把现有死代码分支激活为正确的 per-port done 机制**。

#### 文件：`modules/ubiomodule/ubio_main.cc`

两个 port：`gem5Port`、`netPort`。**需要**引入 per-port done 标志并改主循环。

1. 在主循环前声明：
   ```cpp
   bool gem5Done = false, netDone = false;
   ```
2. `pollAndProcess` 需要知道当前处理的是哪个 port 以便置对应 done。最简做法：给
   `pollAndProcess` 增加一个 `bool* doneFlag` 参数（或用 `fromNetwork` 区分），在其
   TERMINATE 分支里：
   ```cpp
   if (m->hdr.type == static_cast<uint32_t>(MemMessageType::TERMINATE)) {
       std::fprintf(stderr, "[ubio:%d] recv TERMINATE ts=%lu\n", nid, m->hdr.timestamp);
       *doneFlag = true;   // 标记该 port 的对端已 terminate
       break;              // 停止排空该 port（本轮）
   }
   ```
   > 注意：**不要**再用单一的进程级 `done` 置真退出——那是错误的（当前它是死代码，
   > 且语义应是"该 port 不再参与时钟"，而非"整个进程立刻退出"）。
3. 主循环里，**已 done 的 port 不再 emitSync、不再 poll、不计入 min**：
   ```cpp
   while (!(gem5Done && netDone)) {
       if (!gem5Done) gem5Port->emitSync(tick);
       if (netPort && !netDone) netPort->emitSync(tick);

       if (!gem5Done) pollAndProcess(gem5Port, gem5Port, false, &gem5Done);
       if (netPort && !netDone) pollAndProcess(netPort, netPort, true, &netDone);

       uint64_t minTs = UINT64_MAX;
       if (!gem5Done) minTs = std::min(minTs, gem5Port->safeTs(tick));
       if (netPort && !netDone) minTs = std::min(minTs, netPort->safeTs(tick));

       if (minTs > tick) tick = minTs; else std::this_thread::yield();
   }
   ```
   > 循环条件 `!(gem5Done && netDone)`：两个对端都 terminate 后 ubio 优雅退出主循环
   > （不再依赖被 kill；被 kill 仍是最终兜底，但优雅退出更干净且验证性更强）。
   > 若某 TC 里 netPort 为 nullptr（单节点无网络），把 `netDone` 视为一直 true 处理
   > （或用 `(netPort==nullptr || netDone)` 作为"net 侧已完成"的判据）。**执行时注意
   > netPort 可能为空的分支**。
4. 由于 recv 现在把 TERMINATE 作为 kMessage 返回（方案 T-A），`while(m && st==kMessage)`
   **能**进入 TERMINATE 分支（改前不能）。这是使上述机制生效的前提。

#### 文件：`modules/networksim/networksim_main.cc`

nsim 有 N*K 个 port（`_ports`，按 module 索引）。**需要**引入 per-port done 集合。

1. 在 `NetworkSim` 增加成员：`std::set<int> _donePorts;`（或 `std::map<int,bool>`）。
2. `step()` 的 recv 循环里，TERMINATE 分支改为**标记该 port done、停止排空它**，而不是
   `_done=true; return`（后者会让整个 nsim 提前整体退出，可能丢弃仍需转发的在途消息）：
   ```cpp
   for (auto& kv : _ports) {
       int mod = kv.first;
       if (_donePorts.count(mod)) continue;      // 已 done 的 port 不再 poll
       Port* p = kv.second.get();
       p->emitSync(_tick);
       while (MemMessage* m = p->recv(_tick)) {
           if (m->hdr.type == (uint32_t)MemMessageType::TERMINATE) {
               _donePorts.insert(mod);
               break;                              // 停止排空该 port
           }
           if (m->hdr.type == (uint32_t)MemMessageType::CONTROL_SYNC) continue;
           ...
       }
   }
   ```
   > 注意 recv 现在（方案 T-A）把 TERMINATE 作为可见消息返回，`while(m=p->recv())` 能
   > 取到它。原 `_done=true; return` 分支删除。
3. `run()` 的 `min(safeTs)` 计算里**跳过** `_donePorts`：
   ```cpp
   uint64_t minTs = UINT64_MAX;
   for (auto& kv : _ports) {
       if (_donePorts.count(kv.first)) continue;   // done 的 port 不计入 min
       minTs = std::min(minTs, kv.second->safeTs(_tick));
   }
   ```
4. `run()` 的循环终止：当所有 port 都 done（`_donePorts.size() == _ports.size()`）时退出：
   ```cpp
   while (_donePorts.size() < _ports.size() && (maxSteps<0 || s<maxSteps)) { ... }
   ```
   删除旧的 `_done` 成员及其使用（或保留 `_done` 仅作 maxSteps 之外的强制停止，二选一；
   **推荐**删除 `_done`，统一用 `_donePorts` 判终止）。
   > 这样 nsim 在所有对端 terminate 后优雅退出，且退出前 `_fifo` 里的在途消息该转发的
   > 已转发（不像旧 `return` 那样可能丢弃）。被 launcher kill 仍是兜底。

#### 文件：`modules/barrier/barrier_main.cc`

barrier 是集中式服务，靠被 launcher kill 退出。其时钟推进（约 107-111 行）：
```cpp
if (!any) tick++;                       // 无 barrier 活动时自由 +1
else { minTs = min over ports safeTs; tick = (minTs>tick)?minTs:tick+1; }
```
即**无消息时用 `tick++` 自由推进**，有消息时才用 `min(safeTs)`（且卡住时也 `tick+1`
兜底）。因此 barrier **不会**因某 port 的 safeTs 冻结而卡死（它有 `tick++` 逃逸）。

> **执行要点**：barrier **需要**引入 per-port done 标志，语义与 ubio/nsim 一致，原因有二：
> (1) 正确性：done 的 port 不应再计入 `else` 分支的 `min(safeTs)`（否则 minTs 被钳低，
>     虽有 `tick+1` 兜底但会让 barrier 时钟推进变慢）；
> (2) 一致性：与 §0.3.4 不变式对齐（terminate 的 port 不参与 min）。
> 具体：
> 1. 增加 `std::set<size_t> donePorts;`（按 ports 下标）。
> 2. recv 循环里 TERMINATE 分支：`donePorts.insert(i); ` 并停止排空该 port（TERMINATE 现在
>    作为 kMessage 可见）。注意 barrier 循环体当前对非-barrier 类型是 fall-through 忽略；
>    需**新增** TERMINATE 判断以置 done。
> 3. `else` 分支的 `min(safeTs)` 跳过 `donePorts` 里的下标。
> 4. barrier **不需要**因所有 port done 而退出（它靠被 kill）；`tick++` 逃逸保证不卡死。
> 若审核认为 barrier 的 `tick++` 逃逸已足够规避 R5、可接受 minTs 被轻微钳低，则 barrier
> 可**不改**（仅确认 TERMINATE/SYNC 不被误当 barrier 处理：当前有 `CONTROL_SYNC continue`
> 和 `bc=(type==PAYLOAD)?...:nullptr; if(bc && type==BarrierReached)`，TERMINATE 自然被忽略）。
> **默认采用"引入 done 标志"以严格对齐；barrier 不改是可接受的次选**，执行时二选一并在
> commit message 注明。

#### 文件：`gem5/.../UBAdapter.cc`

UBAdapter 只有一个 `_port`。它**不主动**处理对端（ubio）的 TERMINATE——gem5 侧是被
launcher 管理生命周期，且 gem5 进程结束时**自己发** TERMINATE（`init()` 里
`registerExitCallback([...]{ portToClose->terminate(); })`）。

审计 UBAdapter 的 recv 循环（wakeup 主循环 ~1140、stall 循环 ~1230）对 TERMINATE 的处理：
当前它们不显式处理 TERMINATE（因为旧 recv 对 TERMINATE 返回 kEmpty，循环自然结束）。
改后 TERMINATE 作为 kMessage 返回：
- wakeup 主循环 `while(m && st==kMessage)`：会取到 TERMINATE，但循环体分支
  （CONTROL_SYNC / PAYLOAD-barrier / PAYLOAD-coh）都不匹配 TERMINATE →
  落到 `if(type != PAYLOAD){ ...log NONCOH...; continue; }`（约 1161 行）→ 被当"非
  coherence 消息"记录并跳过。**功能上无害**（gem5 不需要对 ubio 的 terminate 做动作），
  但会打印 NONCOH 日志。
  > **执行要点**：在 UBAdapter wakeup 主循环里，PAYLOAD 检查**之前**加一个显式跳过：
  > ```cpp
  > if (m->hdr.type == static_cast<uint32_t>(framework::MemMessageType::TERMINATE)) {
  >     m = _port->recv(curTick(), &st); continue;   // peer ubio done; nothing to do
  > }
  > ```
  > stall 循环同理（若其循环体不含 TERMINATE 处理，加同样跳过，或依赖它落入无匹配分支
  > 被忽略——但为干净，建议显式跳过）。

### 风险与注意

- **R4（TERMINATE 可见性行为翻转）**：这是本项**最高风险点**。改前 recv 对 TERMINATE
  返回 kEmpty（应用层看不到）；改后返回 kMessage（应用层能看到）。所有 recv 循环的
  条件从"kEmpty 退出"变为"能取到 TERMINATE"。必须逐个确认：
  - ubio：TERMINATE 现在**能**触发 `done=true; break;`（改前反而不能！）——需回归确认
    ubio 正常退出，不挂起。
  - nsim：TERMINATE 现在**能**触发 `_done=true; return;`——同上。
  - barrier / UBAdapter：TERMINATE 被忽略（无退出语义）——需确认不误处理、不刷屏。
- **R5（safeTs 去掉 +∞ 后的全局冻结）★本项最高风险**：删掉 `safeTs` 的 UINT64_MAX
  分支后，terminate 后的 port 的 safeTs 会退化为一个**有限且不再增长**的值（对端不再
  发 sync，`receiveTimestamp()` 停在最后一个时间戳），从而**永久钳住** `min(safeTs)` →
  全局时钟卡死 → 该进程主循环 `minTs<=tick` 恒真 → 无限 yield → **TC 超时挂起**。
  **删 safeTs 特判的配套义务**是：应用层必须"terminate 后不再 poll 该 port + 不把它计入
  min"（即 §应用层改法里的 per-port done 机制）。**当前 ubio/nsim 靠 safeTs 的 UINT64_MAX
  苟活到被 kill，并无优雅退出；本项必须把 done 机制真正实现出来，否则 100% 死锁。**
  务必逐进程核对 done 机制已落地。
- **R6（_open 与 2.1.1 顺序）**：本项依赖 2.1.1 已完成（`allocateSendBuffer`/`send(msg)`
  已就位，`_state` 判断已收敛到 allocateSendBuffer 与 send 两处）。**必须先做 2.1.1 再做
  2.1.3。** 2.1.3 把这两处及 recv/init/terminate/析构 里的 `_state` 全部改成 `_open`，
  并删除 `PortState`、`failClosed`、`closeLocal`。

### 验证

- 编译 native + 重编 gem5。
- **重点回归**：`run_multi.sh 1 3 16` 冒烟后，**必须**跑全量
  `1 2 3 4 5 6 7 8 10 11 12 13 16 53`，尤其关注是否有 TC **超时挂起**（R4/R5 的症状是
  某进程不退出 → launcher 超时）。任何超时都指向"某 port terminate 后仍被 poll/计入 min"。
- 若出现挂起：优先检查对应进程的 recv 循环是否正确识别 TERMINATE 并停止参与时钟。

### 附录 B（可选，若审核要求连 `_open` 也外置）

把 `_open` 也删除，`init` 防重复改为"依赖调用方不重复 init"（参考也不防），
收发前的 socket 有效性改为"依赖调用方不在 close 后收发"。**不推荐**：会把防崩溃责任
全甩给调用方，回归风险高。默认保留 `_open`（资源事实，非协议状态）。

### commit（分两个）

gem5 子模块：
```
2.1.3 (gem5): adapt to Port without PortState

recv() now returns TERMINATE as a kMessage; UBAdapter drain loops skip it
explicitly. No functional change to gem5 lifecycle (gem5 sends its own
TERMINATE on exit via registerExitCallback).
```
根仓库：
```
2.1.3: remove PortState; peer-terminated state owned by the application

Delete the PortState enum and Port::_state. Port keeps only a private _open
resource flag (bound/usable), not protocol state. safeTs() drops its
PEER_LOST/CLOSED/TERMINATING UINT64_MAX special-case; recv() now delivers
TERMINATE to the caller (kMessage) instead of failClosed(). The "peer done =>
+inf, stop polling" invariant is now enforced by each application loop
(ubio/nsim exit the whole process on TERMINATE, so the port stops participating
in min(safeTs) naturally). Removed failClosed(). Matches the reference Port,
which tracks no peer state internally.
```

---

## 3. 执行顺序与总验证

1. **2.1.5**（删 chrono include）→ 编译 native + 冒烟 `1 3 16` → commit（根仓库）。
2. **2.1.1**（去 TxHandle）→ 编译 native + 重编 gem5 + 全量回归 → commit（gem5 + 根）。
3. **2.1.3**（去 PortState）→ 编译 native + 重编 gem5 + **全量回归（重点查超时挂起）**
   → commit（gem5 + 根）。

**每项都必须 14/14 PASSED 且无超时**才能进入下一项。任一项回归失败，先修复再提交，
不要叠加改动。

### 全量回归命令
```bash
docker run --rm -v <repo>:/workspace/gem5 -w /workspace/gem5 ubcc-dev:ubuntu20.04 bash -c \
  'mkdir -p shared_ipc && rm -rf shared_ipc/ipc_*; \
   bash tests/e2e/run_multi.sh 1 2 3 4 5 6 7 8 10 11 12 13 16 53'
```
预期尾行：`=== Results: 14 pass, 0 fail ===`。

### gem5 重编命令
```bash
docker run --rm -e CCACHE_DIR=/ccache -v <repo>:/workspace/gem5 \
  -v <ccache>:/ccache -w /workspace/gem5/gem5 ubcc-dev:ubuntu20.04 \
  bash -c 'scons build/ARM/gem5.opt -j$(nproc)'
```

## 4. 执行 agent 注意事项（务必遵守）

- **不要自行扩展 Port 接口**。三项全部完成后，Port 公开接口应严格收敛为参考的这几个：
  `init / terminate / allocateSendBuffer / send(MemMessage*) / receiveTimestamp /
  safeTs / emitSync / recv` + 只读 getter（moduleId/portId/syncInterval/name）。
  **不得**新增 `abortSend`、无参 `send()`、`isReady`、`state`、`failClosed`、`closeLocal`、
  `buffer()`；**不得**保留 `_sendBufInUse`、`TxHandle`、`PortState`。
  > 说明：`closeLocal` 在 2.1.3 里被删除，其资源释放逻辑内联到析构函数（RAII）与
  > `terminate()`。参考实现只有 init/terminate 两个生命周期方法。
- **不要改 MemMessage 线格式**（40 字节布局固定）。
- **不要改时钟不变式**（§0.3）。
- 每改一处，先按文档给的 `rg` 审计命令确认改点集合，**不要凭记忆**。
- 遇到与文档描述不符的现状（行号漂移属正常，用符号搜索定位；但**语义**不符如"某调用点
  多了一个 send 失败分支未在文档列出"），**停止并上报**，不要擅自处理。

## 5. 提交规范

- `gem5/` 是独立 git 子模块（remote `github.com:GCC314/gem5.git`，分支 `v4`）：其内改动
  在 `gem5/` 目录内 `git add/commit`，然后 `git push origin v4`。
- 根仓库（remote `github-work:harumaki-nadeshiko/cc-data.git`，分支 `v4`）：改动含子模块
  指针更新，`git add/commit`，`git push origin v4`。
- 先 push gem5 子模块，再 push 根仓库。
- commit message 用文档各项给出的模板。
```
