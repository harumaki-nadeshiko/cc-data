# UBCC 互联高速化设计方案

> **更新说明**: 本文档已根据 2026-06-04 的架构澄清进行重写。
> 关键修正：EP↔UBCC 是本地低延迟接口（同一进程/主机），
> UBCC 是独立于 gem5 的外部模块，UBCC↔UBCC 之间通过高速互联通信。
>
> 适用范围: UBCC 框架全栈（UBCC / EPSNFController / EPRNFController / EPBackend）
>
> 关联文档: `plan/07-stage-state-tables.md`, `plan/09-stage-execution-playbooks.md`,
> `plan/01-current-state-and-requirements.md`

---

## 1. 架构澄清

### 1.1 三层架构

```
┌──────────────────────────────────────────────────────────────────┐
│  Layer 1: gem5 Simulator Instance (Node A)                       │
│                                                                  │
│  ┌─────┐   ┌─────┐   ┌──────────────┐   ┌────────────────────┐  │
│  │ L1  │   │ L2  │   │ HN-F (本地)   │   │ EP Controllers     │  │
│  │ L1  │──▶│(CHI)│──▶│ (CHI Domain) │──▶│ (EP-SNF / EP-RNF)  │  │
│  └─────┘   └─────┘   └──────────────┘   └─────────┬──────────┘  │
│                                                     │            │
│                                              ┌──────▼────────┐  │
│                                              │ EP Backend    │  │
│                                              │ (gem5 对外接口) │  │
│                                              └──────┬────────┘  │
└─────────────────────────────────────────────────────┼────────────┘
                                                       │
                                    EP↔UBCC Interface  │ 本地调用/共享内存
                                                       │ 延迟 ≈ 0 （同一主机）
                                                       │
┌──────────────────────────────────────────────────────┼────────────┐
│ Layer 2: External UBCC Module (独立进程/主机)          │            │
│                                                      │            │
│  ┌───────────────────────────────────────────────────▼────────┐   │
│  │            UBCC Module Instance                             │   │
│  │  ┌──────────┐  ┌──────────────┐  ┌────────────────────┐    │   │
│  │  │ EP I/F   │──│ UBCC Logic   │──│ Global Directory   │    │   │
│  │  │(接收gem5)│  │ (状态机/转发)  │  │ (跨节点目录)       │    │   │
│  │  └──────────┘  └──────┬───────┘  └────────────────────┘    │   │
│  └───────────────────────┼─────────────────────────────────────┘   │
└──────────────────────────┼─────────────────────────────────────────┘
                           │
        ╔══════════════════╪═══════════════════════════════════╗
        ║  Layer 3: High-Speed Interconnect                    ║
        ║  (UBCC↔UBCC, ~300ns~3ms, 跨 CHI Domain)             ║
        ║  ┌──────────┐     ┌──────────┐     ┌──────────┐     ║
        ║  │ UBCC A   │────▶│ UBCC B   │────▶│ UBCC C   │     ║
        ║  │ (Node A) │◀────│ (Node B) │◀────│ (Node C) │     ║
        ║  └──────────┘     └──────────┘     └──────────┘     ║
        ╚═══════════════════════════════════════════════════════╝
```

### 1.2 层间特性对比

| 特性 | Layer 1→Layer 2 (EP↔UBCC) | Layer 2↔Layer 2 (UBCC↔UBCC) |
|---|---|---|
| 通信方式 | 本地函数调用 / 共享内存 | 高速互联网络 |
| 延迟 | ≈0（架构分离的人为边界） | ~300ns ~ 3ms |
| 语义 | 同步调用或轻量异步 | 消息传递 |
| 可靠性 | 完全可靠（同一主机） | 可能丢包/乱序（需要协议处理） |
| CHI Domain | 相同 CHI Domain | 不同 CHI Domain |
| 是否跨 gem5 实例 | 不跨（同一 gem5） | 跨（不同 gem5 实例） |

### 1.3 关键推论

1. **UBCC 是全局一致性的大脑**: 承担全局目录维护、转发决策、跨节点一致性逻辑
2. **EP Backend 是 gem5 的门面**: 负责将 gem5 内部请求转换为 UBCC 接口调用，并将 UBCC 的响应转换回 gem5 内部消息
3. **EP↔UBCC 延迟 ≈ 0**: 不需要在 EP 侧做复杂的重试/超时——如果 UBCC 需要等待远端，UBCC 内部管理等待状态，EP 只需轮询或等待通知
4. **UBCC↔UBCC 延迟 300ns~3ms**: 决定了全局目录查询/转发的响应时间
5. **每个 Node 一个独立的 gem5 实例 + 一个独立的 UBCC 模块实例**

---

## 2. 延迟模型

### 2.1 三层延迟分解

| 路径 | 延迟 | 折合 tick (@1GHz) |
|---|---|---|
| EP Backend → UBCC（本地调用） | ~0 | 0~1 |
| UBCC 本地目录查找 | ~10ns | ~10 |
| UBCC 本地缓存命中 | ~30ns | ~30 |
| **UBCC↔UBCC 单跳** | **300ns ~ 3ms** | **300 ~ 3,000,000** |
| UBCC→远端内存（含目录+访问） | ~1μs ~ 10ms | ~1,000 ~ 10,000,000 |
| UBCC 完整 grant 返回 EP | ~0 | 0~1 |

### 2.2 旧假设 vs 新假设

| 特性 | 旧假设 | 新假设 |
|---|---|---|
| EP↔UBCC 延迟 | 毫秒级 | ≈0（架构边界） |
| UBCC↔UBCC 延迟 | 1ms ~ 100ms | 300ns ~ 3ms |
| pendingOp 超时 | 5M ticks (5ms) | **10K ~ 500K ticks**（取决于互联类型） |
| 重试方式 | per-cycle polling | **事件驱动 + 轻量级轮询兜底** |

---

## 3. 核心组件职责重定义

### 3.1 EP Backend（gem5 内部）

**职责**:
- 作为 gem5 Ruby 子系统和外部 UBCC 的桥梁
- 将 gem5 内部请求（ReadNoSnp、CHI snoop 响应等）转换为 UBCC 接口调用
- 将 UBCC 的 grant 结果填充到 CompData 中返回
- **不负责** 一致性协议决策、全局目录、重试策略——这些是 UBCC 的事

**当前实现缺陷**:
- EPBackend 内部包含了一些本应在 UBCC 中的逻辑（如 `handleRecallRequest`、`handleInvalidationRequest`）
- 这些逻辑需要在未来迁移到 UBCC 中，EPBackend 只保留接口转换

### 3.2 EPSNFController（gem5 内部）

**职责**:
- 接收本地 HN-F 发来的 `ReadNoSnp` 请求
- 调用 EPBackend → UBCC 获取 grant
- 将 grant 结果以 `CompData` 返回给 HN-F

**EP↔UBCC 即时**这一事实的影响：
- `recvRequestMsg` 中调用 `handleRemoteMiss` 后，如果 UBCC 同步返回 grant，立即发送 CompData
- 如果 UBCC 返回 BUSY（需要等待远端），将请求入队，等待 UBCC 的通知或定时轮询
- **因为 EP↔UBCC 是即时的，所以不需要复杂的 timeout 逻辑**——BUSY 意味着 UBCC 内部正在等待互联响应，EP 只需等待 UBCC 主动通知

### 3.3 EPRNFController（gem5 内部）

**职责**:
- 接收 UBCC（通过 EPBackend）发出的 recall/invalidation 请求
- 将请求转换为标准 CHI 消息（`ReadShared`/`CleanUnique`）发给本地 HN-F
- 处理 CompAck 等确认消息

**EP↔UBCC 即时**的影响：
- EPRNFController 不再需要处理跨节点延迟
- 它的 CHI 请求是发给本地 HN-F 的，延迟由本地缓存层次决定（~10-100 cycle）
- `m_allowRetry = true` 保留作为标准 CHI 行为

### 3.4 UBCC（gem5 外部模块）

**职责**:
- **全局目录维护**：跟踪每个缓存行所在的 Node
- **转发决策**：收到 EP 的 grant 请求后，决定是本地服务还是转发到远端 UBCC
- **一致性逻辑**：处理跨节点的 MESI 状态转换
- **远端通信**：通过高速互联与其他 UBCC 通信

**实现接口**：

```cpp
// EP Backend 调用的 UBCC 接口（本地，≈0 延迟）
class UBCCInterface {
public:
    // 同步 grant：UBCC 可立即返回 grant 或 BUSY
    // 返回值: >= 0 = grant type, < 0 = BUSY
    virtual int processOuterRequest(
        uint64_t linePa,
        int neededPerm,
        bool writeIntent,
        int& homeNode,
        bool& outRecallNeeded,
        int& outRecallOwnerNode,
        Tick& outGrantVisibleTick,
        Tick& outSentinelVisibleTick) = 0;

    // 异步通知：UBCC 完成 grant 后通知 EP
    // 用于替代轮询
    virtual void registerGrantCallback(
        uint64_t linePa,
        std::function<void(int grantType)> callback) = 0;
};
```

### 3.5 分层后的通信路径

**Remote Read (Node A 读取 Node B 的内存)**:

```
① Node A 的 HN-F 收到 L2 的 ReadShared
   → 发现地址 home 是 Node B
   → 发送 ReadNoSnp 给 Node A 的 EP-SNF

② Node A 的 EP-SNF → EPBackend → UBCC Interface
   → processOuterRequest(addr, ReadShared, ...)

③ Node A 的 UBCC 模块:
   a. 查本地目录 → 确信 home 是 Node B
   b. 通过高速互联发送请求给 Node B 的 UBCC
   c. Node B 的 UBCC 查本地目录 + 读取内存
   d. Node B 的 UBCC 通过互联返回数据 + grant
   e. Node A 的 UBCC 更新本地目录

④ UBCC 返回 grant 给 EPBackend
   → populateGrantData 填充数据
   → EP-SNF 发送 CompData 给 HN-F
```

**Write + Invalidation (Node A 写，Node B/C 有副本)**:

```
① Node A 的 HN-F 收到写请求 → 发 ReadNoSnp → EP-SNF

② EP-SNF → EPBackend → UBCC:
   → processOuterRequest(addr, Unique+write, ...)

③ UBCC 查全局目录 → Node B, C 有副本
   → 通过互联发送 Invalidation 给 Node B, C 的 UBCC
   → Node B, C 的 UBCC 将请求转给各自 EPBackend
   → EPBackend → EPRNFController → 本地 HN-F (CleanUnique)
   → HN-F 发 SnpCleanInvalid 给本地 L2

④ Invalidation 完成 → 通过原路径返回确认
⑤ UBCC 授予 Node A 写权限 → 返回 grant
⑥ EP-SNF 发送 CompData
```

---

## 4. 对当前 EP Backend 中 pendingOp / Retry Queue 的影响

### 4.1 当前设计中 pendingOp 的实际含义

```
UBCC (当前在 gem5 内部) 的处理路径:

processOuterRequest:
  ├─ pendingOp==0: 新请求 → 设置 pendingOp=3, 处理
  ├─ pendingOp==3+相同requester: 重入 → 继续/释放
  ├─ pendingOp==3+不同requester: BUSY → 返回 -1
  └─ pendingOp==2: invalidation 进行中
```

当 UBCC 摘出为外部模块后，**pendingOp 将在外部 UBCC 中管理**，EP 侧不再需要它。

### 4.2 过渡期设计（当前 → 外部 UBCC）

在当前阶段（UBCC 仍在 gem5 内部），pendingOp 和 retry queue 的设计需要适应：

| 场景 | EP↔UBCC 延迟 | 是否需要 retry queue |
|---|---|---|
| UBCC 本地处理（目录命中） | ≈0 | 不需要 |
| UBCC 需要等远端响应 | UBCC 内部管理等待 | **需要**（EP 侧等待通知） |
| UBCC 被摘出为外部模块 | ≈0（本地接口） | 由外部 UBCC 管理 |

**最终设计目标**: 当 UBCC 完全摘出后，EP 侧的 retry queue 可以删除，因为：
- EP 调用 UBCC 是本地接口，延迟 ≈0
- 如果 UBCC 需要等待远端，UBCC 内部管理等待状态
- UBCC 通过 callback 或消息通知 EP 完成
- EP 不需要轮询

### 4.3 临时保留的机制

在 UBCC 完全摘出之前，以下机制需要保留并适配：

| 机制 | 保留理由 | 适配方向 |
|---|---|---|
| `pendingOp` 检查 | 防止同地址重入 | 在外部 UBCC 中实现 |
| `grantTick` + 超时 | 防止 UBCC 无响应 | 外部 UBCC 管理超时 |
| EPSNF `_retryQueue` | 等待 UBCC grant 完成 | 改为 callback 驱动 |
| `scheduleEvent(Cycles(1))` | 轮询 UBCC 状态 | 替换为事件驱动 |

---

## 5. 对当前代码的修改计划

### 5.1 当前问题清单

| # | 问题 | 根因 | 影响 |
|---|---|---|---|
| P1 | `_hnfVersion` 推导 | Python `Param.Int` 的 `allParams` 初始时序问题 | 构建失败 |
| P2 | TBE `decrementReserved` 断言 | CHI SLICC TBE 被双重释放 | TC2 运行时崩溃 |
| P3 | `_retryQueue` per-cycle 轮询 | 低效的轮询策略 | 性能（非 correctness） |
| P4 | pendingOp=3 的 5M tick hardcode | 不合理的超时值 | 参数不可配 |

### 5.2 P1 修复状态

**已修复** ✅: `_hnfVersion` 改为从 `p.downstream_destinations[0]->getVersion()` 推导。
不再依赖 `Param.Int` 参数注册。

### 5.3 P2 分析

TBE `decrementReserved()` 断言在 CHI SLICC 协议中意味着一个 TBE 被释放了两次。

经 debug 输出确认，TC2 流程：
1. Node0 写 `0x11223344`（通过 L1→L2→HN-F 路径）
2. Node1 读 `0x10018000000`（通过 EP-SNF→UBCC 路径）
3. `populateGrantData node=1` 正确读出 `0x11223344`
4. **在测试退出阶段**触发 TBE 断言

潜在原因：
- **EP-RNF 发送的 CHI 请求**: `handleRecallRequest` → `startReadShared` → `sendChiRequest` 可能引发 TBE 释放时序问题
- **CompAck 处理**: `retryPendingCompAcks` 可能在错误时机重发 CompAck
- **PendingChiTxn 管理**: `_pendingChiTxns` 清理时机可能与 TBE 释放不同步

**诊断方法**（在独立分支上执行）:
```bash
# 启用 CHI 协议调试，追踪 TBE 生命周期
gem5.opt --debug-flags=RubyCHI --debug-start=43275000 --debug-end=43282000 ...
```

### 5.4 P3 修复

**将 per-cycle polling 改为事件驱动**:

```cpp
// EPSNFController: wakeup 不再每 cycle 轮询
void EPSNFController::wakeup() {
    EPController::wakeup();

    // 轮询间隔提高到 _retryPollInterval（默认 100 cycle）
    if (!_retryQueue.empty() && _retryTick <= curTick()) {
        processRetryQueue();
        _retryTick = curTick() + _retryPollInterval;
    }
}

// UBCC 通知 EP 的入口
void EPSNFController::onGrantReady(uint64_t linePa) {
    // 立即处理一次（不等待轮询间隔）
    if (hasRetryEntry(linePa)) {
        processRetryQueue();
    }
}
```

### 5.5 P4 修复

pendingOp 超时改为从 Python 配置传入：

```python
# UBCCController.py （Python SimObject 文件）
class UBCCController(AbstractController):
    interconnect_latency = Param.Latency("1us",
        "Estimated UBCC-to-UBCC interconnect latency")
    pending_op_timeout_multiplier = Param.Int(5,
        "pendingOp timeout multiplier relative to interconnect latency")
```

```cpp
// UBCCController 构造时计算超时
Tick _pendingOpTimeout;

UBCCController::UBCCController(const Params &p)
    : ...,
      _pendingOpTimeout(
          std::max(p.interconnect_latency * p.pending_op_timeout_multiplier,
                   Cycles(1000).toTicks()))
{
}
```

---

## 6. UBCC 摘出为外部模块的演进路径

### Phase 1: 当前（UBCC 在 gem5 内）
- UBCCController 是 gem5 的 SimObject
- pendingOp 在 UBCC 内管理
- retry queue 在 EPSNFController 内
- EP↔UBCC 是 C++ 函数调用

### Phase 2: UBCC 接口抽象
- 定义 `UBCCInterface` 纯虚类
- `UBCCController` 实现 `UBCCInterface`
- `EPBackend` 通过 `UBCCInterface` 指针调用 UBCC
- **不改变功能，只改变接口组织**

```cpp
// EPBackend.hh
class EPBackend : public SimObject {
    UBCCInterface *_ubcc;  // 通过接口访问
};
```

### Phase 3: UBCC 摘出为共享库
- `UBCCInterface` 保持不变
- UBCC 编译为独立的 `.so` 或进程
- EP↔UBCC 通信通过共享内存或 Unix socket
- 通信延迟 ≈ 0（同一主机）

### Phase 4: UBCC 独立部署
- 每个 Node 运行自己的 gem5 实例 + UBCC 进程
- UBCC 之间通过高速互联通信
- EP↔UBCC 仍是本地通信
- EP 侧完全不需要 retry queue

---

## 7. 参数配置

### 7.1 推荐的 Python 配置

```python
# configs/ruby/CHI_ubcc_framework.py

# 高速互联配置
def get_ubcc_params(interconnect_type):
    params = {
        'interconnect_latency': '1us',
        'pending_op_timeout_multiplier': 5,
    }

    if interconnect_type == "cxl":
        params['interconnect_latency'] = '200ns'
    elif interconnect_type == "numa":
        params['interconnect_latency'] = '500ns'
    elif interconnect_type == "optical":
        params['interconnect_latency'] = '10us'
    elif interconnect_type == "conservative":
        params['interconnect_latency'] = '1ms'
        params['pending_op_timeout_multiplier'] = 5  # 保持 5M 行为

    return params
```

### 7.2 运行时自适应

UBCC 支持根据实际观测到的 RTT 动态调整超时：

```cpp
void UBCCController::onRemoteResponse(Tick rtt) {
    // EWMA 平滑
    _measuredLatency = _measuredLatency * 0.75 + rtt * 0.25;
    // 更新超时 = 测量值 × 倍数（但不低于最小值）
    _pendingOpTimeout = std::max(
        _measuredLatency * _timeoutMultiplier,
        Cycles(1000).toTicks());
}
```

---

## 8. 对现有阶段计划的影响

### 8.1 需要保留的代码

| 代码 | 理由 | 计划移除阶段 |
|---|---|---|
| EPSNFController `_retryQueue` | UBCC 仍可能在 gem5 内返回 BUSY | Phase 3 |
| UBCC `pendingOp` 状态机 | 防止同地址重入 | Phase 3 |
| UBCC `grantTick` 超时 | UBCC 无响应时的安全网 | Phase 3 |
| EPBackend `handleRemoteMiss` | 必须的接口转换 | 永不清除 |

### 8.2 需要新增的测试

| TestCase | 描述 | 优先级 |
|---|---|---|
| HS-1 | `interconnect_latency` 参数化有效 | 高 |
| HS-2 | pendingOp 超时边界行为正确 | 高 |
| HS-3 | 事件驱动重试响应正确 | 中 |
| HS-4 | 外部 UBCC 接口可用 | 低（Phase 2 后） |

---

## 9. 附录：当前代码与理想架构的差距

| 组件 | 当前状态 | 理想（外部 UBCC）状态 |
|---|---|---|
| `UBCCController` | gem5 SimObject，在 gem5 内 | 外部独立模块 |
| `EPBackend._ubccCtrl` | UBCCController 指针 | `UBCCInterface` 接口指针 |
| `pendingOp` | UBCCController 成员 | 外部 UBCC 管理 |
| `EPSNFController._retryQueue` | 轮询 UBCC | 不存在（UBCC 直接通知） |
| `EPRNFController.sendChiRequest` | 发给本地 HN-F | 发给本地 HN-F（不变） |
| `EPBackend.handleRecallRequest` | EP 内实现 | 迁到外部 UBCC |
| `EPBackend.handleInvalidationRequest` | EP 内实现 | 迁到外部 UBCC |
