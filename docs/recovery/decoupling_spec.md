# CC-EP Gem5 解耦规范 v2

## 目标

删除 gem5 进程对 UBIOModule / UBCCController / ResidentDir / Backstore 的一切直接/间接引用。
gem5 侧所有 UBCC 交互只用 `UBAdapter → framework::Port → ZMQ → ubio`。

## 原则

1. **不回退**：只删/改，不 `git checkout`
2. **编译不过就追加修改**：不删完不罢休
3. **3 处协议路径走消息替代**，其余全部 stub 化或本地化

---

## 一、删除文件（11 个）

```
gem5/src/mem/ruby/protocol/chi/ep/UBIOModule.hh
gem5/src/mem/ruby/protocol/chi/ep/UBIOModule.cc
gem5/src/mem/ruby/protocol/chi/ep/UBIOModule.py
gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh
gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc
gem5/src/mem/ruby/protocol/chi/ep/ResidentDir.cc
gem5/src/mem/ruby/protocol/chi/ep/BackstoreSchemaA.cc
gem5/src/mem/ruby/protocol/chi/ep/BackstoreSchemaC.cc
gem5/src/mem/ruby/protocol/chi/ep/NodeAddressMap.cc
gem5/src/mem/ruby/protocol/chi/ep/UBCCProtocolIF.hh
gem5/src/mem/ruby/protocol/chi/ep/CoherenceMessageQueue.hh
```

---

## 二、SConscript

```diff
-SimObject('UBIOModule.py', sim_objects=['UBIOModule'])
 SimObject('EPBackend.py', sim_objects=['EPBackend'])
 SimObject('EPRNFController.py', sim_objects=['EPRNFController'])
 SimObject('EPSNFController.py', sim_objects=['EPSNFController'])
 SimObject('MetaRNFController.py', sim_objects=['MetaRNFController'])
 SimObject('UBAdapter.py', sim_objects=['UBAdapter'])

 Source('EPRNFController.cc')
 Source('EPSNFController.cc')
 Source('EPBackend.cc')
 Source('MetaRNFController.cc')
 Source('UBAdapter.cc')
-Source('UBCCController.cc')
-Source('ResidentDir.cc')
-Source('UBIOModule.cc')
-Source('BackstoreSchemaA.cc')
-Source('BackstoreSchemaC.cc')
-Source('NodeAddressMap.cc')
 Source('M4SelfTest.cc')
 Source('M5SelfTest.cc')
 Source('M6SelfTest.cc')
 Source('M7SelfTest.cc')
 Source('M8SelfTest.cc')
 Source(File(os.path.join(repo_root, 'framework/Port.cc')))
```

---

## 三、EPBackend.hh

### 删除 #include
```diff
-#include "mem/ruby/protocol/chi/ep/UBCCProtocolIF.hh"
```

### 删除 forward decl
```diff
-class UBCCController;
```

### 删除 getUBCC() 方法
```diff
-    UBCCController* getUBCC() const { return _ubcc; }
```

### 删除 _ubcc 成员
```diff
-    // v4-dual-socket: _ubcc retained for backward compatibility...
-    UBCCController *_ubcc = nullptr;
```

### 保留并重写 backstore 函数声明（移除 _ubcc 依赖）

backstore 函数保留在 EPBackend 中，但改为纯 MetaRNF I/O——不再调用 `_ubcc->directory()`/`_ubcc->onBackstore*`，
而是通过 UBAdapter 向 ubio 发 BackstoreResp 消息。

声明的签名不变，实现见 4.13 #11~#13（重写后）。

### 删除 test/debug 函数声明（共 7 个）
```diff
-    std::string inspectOffloadLineForTest(uint64_t homePa) const;
-    bool debugSeedBackstoreForTest(uint64_t homePa, int mesi, uint64_t sharersMask, uint64_t epoch);
-    bool debugSeedResidentForTest(uint64_t homePa, int mesi, uint64_t sharersMask, uint64_t epoch, bool residentDirty);
-    bool debugForceResidentEvictForTest(uint64_t homePa);
```

### 删除计数器函数声明（共 4 个）
```diff
-    uint64_t getStaleRejectedCount() const;
-    void resetStaleRejectedCount();
-    uint64_t getOwnerMismatchRejectedCount() const;
-    void resetOwnerMismatchRejectedCount();
```

### 新增本地计数器
```diff
     NodeAddressMap _addrMap;
+    uint64_t _epRnfSnoopCount = 0;
```

---

## 四、EPBackend.cc

### 4.1 全局替换（整个文件）

```
UBCC_OuterReqType  → OuterReqType
UBCC_OuterGrantType → OuterGrantType
ubccGrant          → grantTypeVar
```

### 4.2 删除 #include
```diff
-#include "mem/ruby/protocol/chi/ep/UBCCController.hh"
```

### 4.3 构造函数 — 删除 _ubcc 创建
```diff
-    _ubcc = new UBCCController(_nodeId, 0, ruby_system,
-                               p.ubcc_epoch_bits,
-                               p.ubcc_bf_bytes,
-                               p.ubcc_force_resident_entries);
-    _ubcc->setBackend(this);
```

### 4.4 析构函数 — 删除 delete
```diff
-    delete _ubcc;
```

### 4.5 init() — 删除 bindUbccToRouter
```diff
-            if (_ubcc) {
-                adapter->bindUbccToRouter(_ubcc);
-            }
```

### 4.6 init() — 删除 self-test 声明和调用
```diff
-void m4SelfTest_run(EPBackend*);
-void m5SelfTest_run(EPBackend*);
-void m6SelfTest_run(EPBackend*);
-void m7SelfTest_run(EPBackend*);
-void m8SelfTest_run(EPBackend*);
```
```diff
-    // ---- M4..M8 Self-Test ----
-    if (_nodeId == 0 && _ubcc) {
-        m4SelfTest_run(this); m5SelfTest_run(this); m6SelfTest_run(this);
-        m7SelfTest_run(this); m8SelfTest_run(this);
-    }
```

### 4.7 wakeup()
```diff
-void EPBackend::wakeup() {
-    if (_ubcc) _ubcc->wakeup();
-}
+void EPBackend::wakeup() {}
```

### 4.8 isDsmAddr — 改用 _addrMap
```diff
-bool EPBackend::isDsmAddr(uint64_t pa) const {
-    if (!_ubcc) return false;
-    return _ubcc->isDsmAddr(pa);
-}
+bool EPBackend::isDsmAddr(uint64_t pa) const {
+    return _addrMap.isDsm(_nodeId, pa);
+}
```

### 4.9 EP_RNF 计数器 — 本地化
```diff
-uint64_t EPBackend::getEpRnfSnoopCount() const {
-    if (!_ubcc) return 0;
-    return _ubcc->getEpRnfSnoopCount();
-}
+uint64_t EPBackend::getEpRnfSnoopCount() const { return _epRnfSnoopCount; }

-void EPBackend::resetEpRnfSnoopCount() {
-    if (_ubcc) _ubcc->resetEpRnfSnoopCount();
-}
+void EPBackend::resetEpRnfSnoopCount() { _epRnfSnoopCount = 0; }

-void EPBackend::incrementEpRnfSnoopCount() {
-    if (_ubcc) _ubcc->incrementEpRnfSnoopCount();
-}
+void EPBackend::incrementEpRnfSnoopCount() { _epRnfSnoopCount++; }
```

### 4.10 backstore 函数 — 空stub
```cpp
void EPBackend::issueBackstoreRead(uint64_t homePa) { /* UBCC moved to ubio */ }
void EPBackend::issueBackstoreWrite(uint64_t homePa) { /* UBCC moved to ubio */ }
void EPBackend::issueBackstoreDelete(uint64_t homePa) { /* UBCC moved to ubio */ }
```

### 4.11 ★★★ handleWriteback — sendQueryLineMetaReq 替代 ★★★
```diff
-    } else if (_ubcc) {
-        epochVal = _ubcc->getEpochForLine(line_pa);
-        if (_epRnfCtrl) {
-            dirtyState = _epRnfCtrl->getDirtyBit(line_pa);
-        }
-        if (epochVal == 0) {
-            _epochCounter++;
-            epochVal = _epochCounter;
-        }
-        int ownerNode = _ubcc->getOwnerForLine(line_pa);
-        if (ownerNode >= 0) {
-            requesterNode = ownerNode;
-        }
-        homeNode = _nodeId;
-    } else {
+    } else {
+        // Query home UBCC via message for epoch + owner
+        uint64_t qEpoch = 0;
+        int qOwnerNode = -1;
+        bool qFound = false;
+        getUBAdapter(0)->sendQueryLineMetaReq(
+            line_pa, homeNode, homeSocket, qEpoch, qOwnerNode, qFound);
+        if (qFound) {
+            epochVal = qEpoch;
+            if (qOwnerNode >= 0) requesterNode = qOwnerNode;
+        }
+        if (epochVal == 0) { _epochCounter++; epochVal = _epochCounter; }
-        fatal("EPBackend node_id=%d: no UBCC/UBAdapter available for writeback\n", _nodeId);
     }
```

### 4.12 ★★★ sendHomeWritebackNotify — sendQueryLineMetaReq 替代 ★★★
```diff
-    uint64_t epochVal = 0;
-    if (_ubcc) {
-        epochVal = _ubcc->getEpochForLine(homePa);
-    }
+    uint64_t epochVal = 0;
+    {
+        uint64_t qEpoch = 0;
+        int qOwnerNode = -1;
+        bool qFound = false;
+        UBAdapter *qAdapter = getUBAdapter(homeSocket);
+        if (!qAdapter) qAdapter = getUBAdapter(0);
+        if (qAdapter) {
+            qAdapter->sendQueryLineMetaReq(
+                homePa, homeNode, homeSocket, qEpoch, qOwnerNode, qFound);
+            if (qFound) epochVal = qEpoch;
+        }
+    }
```

### 4.13 逐行修改明细

以下按出现顺序列出 EPBackend.cc 中每一处含 `_ubcc` / `UBCCController::` 的代码块，给出精确的行号、原始代码、替换后代码、修改理由。

---

#### #1 构造：`_ubcc` 创建

**行号**: 129-134

**原始代码**:
```cpp
    // v4-dual-socket: _ubcc retained for inspection/tests; main paths use message-passing.
    _ubcc = new UBCCController(_nodeId, 0, ruby_system,
                               p.ubcc_epoch_bits,
                               p.ubcc_bf_bytes,
                               p.ubcc_force_resident_entries);
    _ubcc->setBackend(this);
```

**替换为**: 删除全部 6 行。

**理由**: UBCCController 实例现在只在 ubio 进程中存在。gem5 中不再创建本地 UBCC。

---

#### #2 析构：`delete _ubcc`

**行号**: 264

**原始代码**:
```cpp
    delete _ubcc;
```

**替换为**: 删除此行。

**理由**: `_ubcc` 成员已删除，无需析构。

---

#### #3 init(): bindUbccToRouter

**行号**: 308-310

**原始代码**:
```cpp
            if (_ubcc) {
                adapter->bindUbccToRouter(_ubcc);
            }
```

**替换为**: 删除此 3 行。

**理由**: `bindUbccToRouter` 在 UBAdapter 中已删除。UBCC 不再由 gem5 端路由。

---

#### #4 init(): self-test 声明

**行号**: ~262-266（`void m4SelfTest_run...` 等 6 行声明）

**原始代码**:
```cpp
void m4SelfTest_run(EPBackend*);
void m5SelfTest_run(EPBackend*);
void m6SelfTest_run(EPBackend*);
void m7SelfTest_run(EPBackend*);
void m8SelfTest_run(EPBackend*);
```

**替换为**: 删除全部。

**理由**: Self-test 依赖 `_ubcc`，不再可用。

---

#### #5 init(): self-test 调用

**行号**: 326-332

**原始代码**:
```cpp
    // ---- M4 Sentinel Registration Self-Test ----
    // ---- M5 Sideband Self-Test ----
    // ---- M6 UBCC Directory + EP_RNF Self-Test ----
    // Runs during instantiation; results printed to stdout.
    // Python test harness parses the output.
    // Only one node (node 0) runs the self-tests to avoid duplicate output.
    if (_nodeId == 0 && _ubcc) {
        m4SelfTest_run(this);
        m5SelfTest_run(this);
        m6SelfTest_run(this);
        m7SelfTest_run(this);
        m8SelfTest_run(this);
    }
```

**替换为**: 删除全部 11 行。

**理由**: 同上。

---

#### #6 wakeup()

**行号**: 338-339

**原始代码**:
```cpp
    if (_ubcc)
        _ubcc->wakeup();
```

**替换为**: 删除空的函数体内容，保留空实现：
```cpp
{
}
```

**理由**: UBCC 的时钟在 ubio 进程中驱动，gem5 侧无需转发。

---

#### #7 isDsmAddr

**行号**: 425-427

**原始代码**:
```cpp
bool
EPBackend::isDsmAddr(uint64_t pa) const
{
    if (!_ubcc)
        return false;
    return _ubcc->isDsmAddr(pa);
}
```

**替换为**:
```cpp
bool
EPBackend::isDsmAddr(uint64_t pa) const
{
    return _addrMap.isDsm(_nodeId, pa);
}
```

**理由**: `NodeAddressMap` 已有 `isDsm(node_id, pa)` 方法，语义等价。纯本地计算，无需消息。

---

#### #8 getEpRnfSnoopCount

**行号**: 433-436

**原始代码**:
```cpp
uint64_t
EPBackend::getEpRnfSnoopCount() const
{
    if (!_ubcc)
        return 0;
    return _ubcc->getEpRnfSnoopCount();
}
```

**替换为**:
```cpp
uint64_t
EPBackend::getEpRnfSnoopCount() const
{
    return _epRnfSnoopCount;
}
```

**理由**: 测试计数器本地化。EPBackend 自己维护该计数，不再委托 UBCCController。需在 EPBackend.hh private 段添加 `uint64_t _epRnfSnoopCount = 0;`。

---

#### #9 resetEpRnfSnoopCount

**行号**: 441-443

**原始代码**:
```cpp
void
EPBackend::resetEpRnfSnoopCount()
{
    if (_ubcc)
        _ubcc->resetEpRnfSnoopCount();
}
```

**替换为**:
```cpp
void
EPBackend::resetEpRnfSnoopCount()
{
    _epRnfSnoopCount = 0;
}
```

**理由**: 同上。

---

#### #10 incrementEpRnfSnoopCount

**行号**: 448-450

**原始代码**:
```cpp
void
EPBackend::incrementEpRnfSnoopCount()
{
    if (_ubcc)
        _ubcc->incrementEpRnfSnoopCount();
}
```

**替换为**:
```cpp
void
EPBackend::incrementEpRnfSnoopCount()
{
    _epRnfSnoopCount++;
}
```

**理由**: 同上。

---

#### #11~#13 Backstore 函数 — 保留并重写为纯 MetaRNF I/O

**不删除**。EPBackend 的 backstore 方法是 gem5 侧元数据读写的唯一实现。
删除 `_ubcc->directory()` / `_ubcc->onBackstore*()` 调用，改为纯 MetaRNF 操作 + 通过 UBAdapter 向 ubio 发回响应。

**完整消息路径**:
```
ubio UBCC 冷缺失 → UbioBackstoreHost::hostIssueBackstoreRead(pa)
  → 构造 BackstoreReadReq CoherenceMessage
  → ubio gem5Port.send()
  → ZMQ → gem5 Port.recv()
  → UBAdapter::wakeup drain → recvFromRouter / handleResponse
  → EPBackend::issueBackstoreRead(homePa)
  → MetaRNF::issueRead(metaPa, callback)
  → DDR4 物理读取完成 → callback
  → EPBackend 构造 BackstoreReadResp CoherenceMessage
  → UBAdapter::transportSend(resp)
  → ZMQ → ubio gem5Port.recv()
  → UbioBackstoreHost 接收 → ubcc.onBackstoreFillComplete()
写入/删除同理。
```

**新增消息类型**（`framework/MemMessage.hh` 和 `CoherenceMessage.hh`）:
```diff
+    BACKSTORE_READ_REQ  = 7,
+    BACKSTORE_READ_RESP = 8,
+    BACKSTORE_WRITE_REQ = 9,
+    BACKSTORE_WRITE_ACK = 10,
+    BACKSTORE_DELETE_REQ = 11,
+    BACKSTORE_DELETE_ACK = 12,
```

**新增 CoherenceMessage 类型**:
```diff
+    BackstoreReadReq,    // ubio→gem5: 请求读取元数据
+    BackstoreReadResp,   // gem5→ubio: 元数据读取完成
+    BackstoreWriteReq,   // ubio→gem5: 请求写入元数据
+    BackstoreWriteAck,   // gem5→ubio: 写入确认
+    BackstoreDeleteReq,  // ubio→gem5: 请求删除元数据
+    BackstoreDeleteAck,  // gem5→ubio: 删除确认
```

**UBAdapter 处理**（`recvFromRouter` 新增 case）:
```cpp
case CoherenceMessageType::BackstoreReadReq:
case CoherenceMessageType::BackstoreWriteReq:
case CoherenceMessageType::BackstoreDeleteReq:
    if (_backend) {
        _backend->handleBackstoreMessage(msg);
    }
    break;
```

**EPBackend 新增 `handleBackstoreMessage()`**（分派到对应方法）:
```cpp
void EPBackend::handleBackstoreMessage(const CoherenceMessage &msg) {
    switch (msg.h.type) {
    case CoherenceMessageType::BackstoreReadReq:
        issueBackstoreRead(msg.h.homeLinePa);
        break;
    case CoherenceMessageType::BackstoreWriteReq:
        issueBackstoreWrite(msg.h.homeLinePa);
        break;
    case CoherenceMessageType::BackstoreDeleteReq:
        issueBackstoreDelete(msg.h.homeLinePa);
        break;
    }
}
```

**EPBackend::issueBackstoreRead 重写**（删除 `_ubcc` 依赖）:
```cpp
void EPBackend::issueBackstoreRead(uint64_t homePa)
{
    // 通过 MetaRNF 读取 DDR4 中的元数据，不再调用 _ubcc->directory()
    if (!_metaRnf) {
        // 无 MetaRNF 时直接返回 not-found（TC1 等简单测试不需要 backstore）
        CoherenceMessage resp;
        resp.h.type = CoherenceMessageType::BackstoreReadResp;
        resp.h.homeLinePa = homePa;
        resp.h.reqId = _lastBackstoreReqId;
        resp.b.backstoreReadResp.found = false;
        getUBAdapter(0)->transportSend(resp);
        return;
    }
    const uint64_t metaPa = metadataBackstorePa(homePa);
    _metaRnf->issueRead(metaPa,
        [this, homePa](bool ok, const MetaLine &line) {
            CoherenceMessage resp;
            resp.h.type = CoherenceMessageType::BackstoreReadResp;
            resp.h.homeLinePa = homePa;
            bool found = false;
            if (ok) {
                MetaStoreDecoded decoded;
                found = decodeMetaLine(homePa, line, decoded);
                if (found) {
                    resp.b.backstoreReadResp.state = decoded.state;
                    resp.b.backstoreReadResp.sharersMask = decoded.sharersMask;
                    resp.b.backstoreReadResp.epoch = decoded.epoch;
                }
            }
            resp.b.backstoreReadResp.found = found;
            getUBAdapter(0)->transportSend(resp);
        });
}
```

**EPBackend::issueBackstoreWrite 重写**:
```cpp
void EPBackend::issueBackstoreWrite(uint64_t homePa)
{
    if (!_metaRnf) return;
    const uint64_t metaPa = metadataBackstorePa(homePa);
    // 数据应已在调用前由 Caller 填入 _pendingWriteData
    MetaLine line = encodeMetaLine(homePa, _pendingWriteState,
                                    _pendingWriteSharers, _pendingWriteEpoch);
    _metaRnf->issueWrite(metaPa, line, [this, homePa](bool) {
        CoherenceMessage ack;
        ack.h.type = CoherenceMessageType::BackstoreWriteAck;
        ack.h.homeLinePa = homePa;
        getUBAdapter(0)->transportSend(ack);
    });
}
```

**EPBackend::issueBackstoreDelete 重写**:
```cpp
void EPBackend::issueBackstoreDelete(uint64_t homePa)
{
    if (!_metaRnf) return;
    const uint64_t metaPa = metadataBackstorePa(homePa);
    _metaRnf->issueDelete(metaPa, [this, homePa](bool existed) {
        CoherenceMessage ack;
        ack.h.type = CoherenceMessageType::BackstoreDeleteAck;
        ack.h.homeLinePa = homePa;
        ack.b.backstoreDeleteAck.existed = existed;
        getUBAdapter(0)->transportSend(ack);
    });
}
```

**ubio 侧 UbioBackstoreHost 重写**（不再用本地 `std::map`，改为发送消息）:
```cpp
void hostIssueBackstoreRead(uint64_t pa) override {
    // 不再直接操作 ubcc.directory()，改为通过 gem5Port 发消息
    CoherenceMessage req;
    req.h.type = CoherenceMessageType::BackstoreReadReq;
    req.h.homeLinePa = pa;
    req.h.reqId = _nextReqId++;
    _pendingBackstore[req.h.reqId] = pa;
    sendCoh(gem5Port, tick, 0, 0, req);  // 发到 gem5
}
// 响应在 pollAndProcess(netPort) 或 gem5Port 的 recv 中分派
```

**注意**: Backstore 消息路径是 Phase B 的新增功能。当前 TC1 测试不触发 backstore（所有 DSM 行从 G_I 起步），可以先保持 backstore 函数为空实现（返回 false/空响应），测试通过后再补齐 MetaRNF I/O。

#### #14~#17 删除（原 stub 化改为彻底删除声明+实现+调用者）

| 函数名 | 操作 |
|--------|------|
| `inspectOffloadLineForTest()` | 删除 EPBackend.hh 声明、EPBackend.cc 实现 |
| `debugSeedBackstoreForTest()` | 同上 |
| `debugSeedResidentForTest()` | 同上 |
| `debugForceResidentEvictForTest()` | 同上 |

**外部调用者检查**: 仅 UBCCController 和 EPBackend 内部引用，无外部调用者。全部删除无影响。

#### #20~#23 删除（原 stub 化改为彻底删除声明+实现）

| 函数名 | 操作 |
|--------|------|
| `getStaleRejectedCount()` | 删除 EPBackend.hh 声明、EPBackend.cc 实现 |
| `resetStaleRejectedCount()` | 同上 |
| `getOwnerMismatchRejectedCount()` | 同上 |
| `resetOwnerMismatchRejectedCount()` | 同上 |

**外部调用者检查**: 仅 UBCCController 定义和 EPBackend 转发，无外部调用者。

#### #8~#10 EP-RNF Snoop 计数器 — 保留

| 函数名 | 操作 |
|--------|------|
| `getEpRnfSnoopCount()` | 保留声明，实现本地化 |
| `resetEpRnfSnoopCount()` | 保留声明，实现本地化 |
| `incrementEpRnfSnoopCount()` | 保留声明，实现本地化 |

**外部调用者检查**: 被 EPRNFController/EPSNFController 调用，用于测试验证。不能删除。

---

### #include 删除

| 行号 | 内容 | 理由 |
|------|------|------|
| 21 | `#include "mem/ruby/protocol/chi/ep/UBCCController.hh"` | 文件已删除，EPBackend 不再需要完整类型 |

### 枚举类型替换（全局）

| 原始名称 | 替换名称 | 理由 |
|---------|---------|------|
| `UBCC_OuterReqType` | `OuterReqType` | EPBackend.hh 中已有同名枚举（行 33-36），语义相同 |
| `UBCC_OuterGrantType` | `OuterGrantType` | EPBackend.hh 中已有同名枚举（行 39-45），语义相同 |
| `ubccGrant` | `grantTypeVar` | 避免与已删除的 `_ubcc` 成员混淆 |

---

## 五、UBAdapter.hh

```diff
-class UBIOModule;
-    void setRouter(UBIOModule *router) { _router = router; }
-    void bindUbccToRouter(UBCCController *ubcc);
-    UBIOModule * router() const { return _router; }
-    UBIOModule *_router = nullptr;
-    friend class UBIOModule;
```

---

## 六、UBAdapter.cc

```diff
-#include "mem/ruby/protocol/chi/ep/UBIOModule.hh"
-#include "mem/ruby/protocol/chi/ep/UBCCController.hh"
-      _router(p.router),
```

### 删除 bindUbccToRouter 整个函数

### transportSend — 删 _router fallback
```diff
-    if (!_router) { fatal(...); }
-    _router->sendMessage(msg);
```

### 全局替换
```
if (!_router && !_port) → if (!_port)
```

### init() 保留现有代码（自建 Port + _router->setAdapter 作兼容回退）

---

## 七、UBAdapter.py

```diff
-    router = Param.UBIOModule(NULL, "Local UBIOModule for message dispatch")
```

---

## 八、CHI_ubcc_framework.py

```diff
 for socket_id in range(num_sockets):
-    ubiomodule = UBIOModule(node_id=node_id, socket_id=socket_id,
-                            ub_msg_latency="500ns")
-    ub_adapter = UBAdapter(node_id=node_id, socket_id=socket_id,
-                            router=ubiomodule)
+    ub_adapter = UBAdapter(node_id=node_id, socket_id=socket_id)

-    nd['ubiomodules'].append(ubiomodule)
     nd['ub_adapters'].append(ub_adapter)
-    setattr(ruby_system, f"ubiomodule_n{node_id}_s{socket_id}", ubiomodule)

-nd['ubiomodule'] = nd['ubiomodules'][0] if nd['ubiomodules'] else None
 nd['ub_adapter'] = nd['ub_adapters'][0] if nd['ub_adapters'] else None
```

---

## 九、编译后验证

```
grep -rn "UBIOModule" gem5/src/ --include="*.cc" --include="*.hh" | grep -v "comment\|// \*"
grep -rn "_ubcc\b" gem5/src/ --include="*.cc" --include="*.hh"
grep -rn "_router\b" gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.*
```
