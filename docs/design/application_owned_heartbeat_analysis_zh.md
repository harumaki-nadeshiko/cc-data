# 应用侧接管 CONTROL_SYNC Heartbeat 分析

> 日期：2026-07-29
> 状态：仅分析，不修改当前实现

## 当前实现

`framework/Port.cc::emitSync(curTick)` 当前同时承担三项职责：

1. 根据 `_lastSyncTs` 和 `_linkLatency` 判断是否需要发送。
2. 构造带 `timestamp=curTick+linkLatency` 的 `CONTROL_SYNC`。
3. 使用 ZMQ `dontwait` best-effort 发送；成功后更新 private `_lastSyncTs`。

普通 `Port::send()` 使用 blocking send 和有限 HWM，保证 coherence/data message
不静默丢失。`emitSync()` 绕过 `send()`，只让 heartbeat non-blocking，目的是
避免 peer 尚未 bind 时阻塞 gem5 event queue。

应用调用点：

| 组件 | 调用位置 | 调度方式 |
|---|---|---|
| gem5 UBAdapter | `UBAdapter::wakeup()` | gem5 event queue wakeup |
| ubio | main loop | 每次 outer loop 对 gem5/net port 调用 |
| networksim | `NetworkSim::step()` | 每个 step 对 active port 调用 |
| barrier | main loop | 每轮对所有 port 调用 |

接收侧 `Port::recv()` 将 heartbeat 当普通 timestamped message：先更新 `_lastRxT`，
再由应用按 `CONTROL_SYNC` 类型丢弃 payload。`safeTs()` 使用 peer `_lastRxT` 与
本地 `_lastSyncTs + syncInterval` 共同形成 conservative PDES bound。

## 当前 non-blocking 行为评价

优点：

- peer 尚未 bind 时不会卡住 gem5 event loop。
- heartbeat 丢失不会破坏 coherence data correctness。
- 失败时不更新 `_lastSyncTs`，下一次调用会立即重试。
- coherence/data send 仍保留 blocking backpressure。

限制：

- `dontwait` 失败没有区分 `EAGAIN`、peer 未连接和 socket error。
- 没有应用可见的连续失败次数、最后成功 wall time 或 peer-health 状态。
- ubio/networksim 每次 loop 都调用；真正节流仍隐藏在 Port private state。
- peer 死亡后 heartbeat 会持续失败，但这本身不能解除 `safeTs()` 停滞。
- heartbeat best-effort 与 peer failure detection 混在一起，但两者不是同一问题。

peer 正常运行时不应长期停滞；peer 死亡应由 supervisor、TERMINATE 或显式
membership/failure protocol 处理，不能期待 heartbeat 丢弃自动恢复 PDES。

## 为什么现有 API 不能完全由应用接管

应用可调用的相关接口只有：

```cpp
allocateSendBuffer(timestamp)
send(message)              // blocking
emitSync(curTick)           // 内部 dontwait
safeTs(curTick)
receiveTimestamp()
syncInterval()
```

如果应用自行构造 `CONTROL_SYNC` 后调用 `send()`：

- 又会回到 blocking send，重现启动时 peer 未 bind 的风险。
- 应用无法更新 Port private `_lastSyncTs`。
- `safeTs()` 仍看不到应用已成功发送 heartbeat 的时间。

因此，只把 `emitSync()` 调用从 Port 移到应用 helper，而底层仍调用
`Port::emitSync()`，只能接管调度策略，不能接管发送语义。要完全接管，目标
framework 至少需要一个不包含 policy 的低层接口。

## 推荐的目标接口边界

推荐 framework 仅提供 mechanism，并将 non-blocking send 与成功后的本地
timestamp bookkeeping 原子化：

```cpp
enum class TrySendResult {
    Sent,
    WouldBlock,
    Disconnected,
    Error,
};

TrySendResult trySendSync(uint64_t curTick);
uint64_t lastLocalSyncTimestamp() const;
uint64_t linkLatency() const;
```

`trySendSync()` 只负责构造/序列化 CONTROL_SYNC、`dontwait` 发送和在成功时更新
`_lastSyncTs`；它不决定何时调用、重试间隔或 peer failure policy。不要拆成
`trySendControl()` 与 `noteLocalSyncSent()` 两步，否则应用可能漏记或错误排序。

如果目标设计希望应用连 packet 都构造，可提供单个无策略接口：

```cpp
TrySendResult trySend(MemMessage *msg);
```

并让应用维护自己的 `lastSuccessfulSyncTick`，同时把该值显式传给：

```cpp
safeTs(curTick, lastSuccessfulSyncTick)
```

关键原则是 framework 不决定 heartbeat cadence、retry/backoff 或 failure policy，
但仍负责 ZMQ ownership、packet serialization 和错误分类。

## 应用侧 HeartbeatPump

每个应用可拥有一个固定状态的小对象，不按 PA/request 动态增长：

```text
HeartbeatPump
  last_success_tick
  next_retry_tick
  consecutive_failures
  last_success_wall_time
  peer_state = Starting | Alive | Suspect | Terminated
```

统一算法：

1. 若 peer 已收到 `TERMINATE`，停止 heartbeat 并从 safeTs membership 中移除。
2. 若 `curTick < next_retry_tick`，跳过。
3. 构造 `CONTROL_SYNC(timestamp=curTick+linkLatency)`。
4. non-blocking try-send。
5. 成功：更新 local sync timestamp，清零失败计数，按 link latency 安排下一次。
6. `WouldBlock/Starting`：保持原 local sync timestamp，在同一 virtual tick 的
   后续 event-loop iteration 重试；不能只安排未来 virtual tick，因为 safeTs
   停滞时该 tick 可能永远到不了。
7. 连续失败只转为 `Suspect` 并报告 supervisor，不擅自假定 peer 已退出。
8. 只有显式 TERMINATE、进程退出通知或 membership decision 才删除 peer。

不要使用纯 wall-clock timeout 修改协议 membership；wall clock 只用于运维告警和
终止整个测试。PDES 时间推进仍必须由 timestamp/membership protocol 决定。

## 各组件调度建议

### gem5

- 使用独立 heartbeat Event，而不是依赖收到请求后的 wakeup。
- event 在 `next_retry_tick` 调度，发送失败后仍重新 schedule。
- 收包 wakeup 可顺便 pump，但不是唯一发送来源。
- 绝不能在 gem5 event callback 中调用可能无限阻塞的 send。

### ubio

- 在 outer loop 开头 pump 两条独立 heartbeat：gem5 port、network port。
- 每条 port 有独立失败计数和 next retry，避免一个 peer 阻塞另一条链路。
- heartbeat 不应饥饿 deferred H64、barrier release 或 coherence response。

### networksim

- 每个 active port 一个 HeartbeatPump。
- 在 bounded receive/fwd work 后按 round-robin pump，避免大量 port 的 heartbeat
  抢占数据转发。
- 收到 peer-exit notification 后显式停止对应 pump，并更新 safe-time membership。

## Failure 与 Backpressure

必须区分：

| 情况 | heartbeat | data/coherence | membership |
|---|---|---|---|
| peer 尚未 bind | best-effort 重试 | 启动门禁前不发送或受控等待 | Starting |
| HWM 临时满 | 可丢本次 heartbeat | blocking/backpressure，不丢 | Alive/Suspect |
| peer 正常但慢 | cadence 降低，继续重试 | 保持 backpressure | Alive |
| peer 进程死亡 | 上报 supervisor | send fail/fatal | 需显式退出协议 |
| 已收到 TERMINATE | 停止 | 停止新业务发送 | 从 active membership 移除 |

## 迁移步骤建议

1. 先增加 Port mechanism API 和精确错误分类，不改变现有 `emitSync()`。
2. 实现可单测的应用侧 `HeartbeatPump`。
3. gem5、ubio、networksim 逐个切换，保留旧路径作为编译期 fallback。
4. 添加 late-bind、HWM-full、peer-terminate、peer-crash fault tests。
5. 比较 safeTs、协议 tick、heartbeat 发送量和 wall-clock simulation speed。
6. 全部组件迁移后再删除 Port 内 cadence policy。

## 必须验证的性质

- peer late bind 时 event loop 不阻塞。
- heartbeat 失败不更新 last-success timestamp。
- heartbeat 丢失后可重试并恢复 safeTs 推进。
- data/coherence 不因 heartbeat policy 变成 fire-and-forget。
- 一个 port 失败不阻塞其他 port 的 pump。
- TERMINATE 后不再等待退出 peer 的 heartbeat。
- 没有 wall-clock timeout 伪造协议时间或 membership。
- 所有 heartbeat state 固定容量，按 port 数有界。

## 结论

应用侧负责 cadence、retry 和 failure observation 是更合理的所有权；framework
应只提供 non-blocking control send、timestamp bookkeeping 和错误分类。但在当前
API 下，应用无法同时获得 non-blocking send 和 `_lastSyncTs` 正确更新，所以不能
仅通过改 ubio/nsim/gem5 调用代码完成安全迁移。应先扩展目标 framework 的低层
mechanism API，再逐组件接管 policy。
