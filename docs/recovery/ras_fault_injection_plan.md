# CC-EP RAS 故障注入参考计划 v1.0

> 目标：为 CC-EP 分布式一致性协议提供一份**前瞻性、可执行但不要求立即落地**的 RAS（Reliability / Availability / Serviceability）故障注入计划，统一故障模型、测试覆盖、可观测性与分阶段实施路线。
>
> 本文聚焦 **FaultPipeline + NetworkSim hop-scheduler** 的双层注入架构，遵循已冻结决策 Q1-Q10。

---

## 0. 固定前提与边界

### 0.1 已冻结决策摘要

| 决策 | 结论 |
|---|---|
| Q1 | **混合架构**：endpoint `FaultPipeline` + `NetworkSim` hop-scheduler |
| Q2 | **双栈配置**：canonical JSON；兼容 legacy string 导入 |
| Q3 | **可选 inspector**：transport 保持纯净，pipeline 可注册 decoder |
| Q4 | **逐 hop 注入**：每一跳都可命中故障规则 |
| Q5 | **单一实现共享**：仿真与测试使用同一套故障规则实现，避免漂移 |
| Q6 | **中粒度匹配**：`addr range` / `txn class` / `opcode subgroup`；预留更细扩展槽位 |
| Q7 | **混合触发**：Phase 1 以 count-based 为主，预留概率/时间窗 |
| Q8 | **中等可观测**：记录 `rule hit + txn/addr + protection reaction` |
| Q9 | **双标准判定**：Safety 必须满足；Liveness 按故障类别分级 |
| Q10 | **双视图组织**：执行按故障类型；报告与 coverage matrix 按保护机制 |

### 0.2 本文范围

**Phase 1 优先 in-scope**
- message-layer：`drop` / `duplicate`
- transport/hop 级命中与统计
- JSON 规则配置与日志可观测

**Phase 2 扩展 in-scope**
- `delay` / processing jitter / bandwidth throttling
- 独立 scheduler / queue 模块化

**Phase 3 扩展 in-scope**
- `reorder`（带窗口控制）
- richer causality tracing

**明确 out-of-scope（当前版本）**
- payload bit-flip/corrupt 的完整端到端纠错链
- link flapping / reconnect 协议
- 多进程分布式时钟同步误差建模

---

## 1. RAS 框架概述

## 1.1 Reliability

在 CC-EP 中，Reliability 指：

- 故障条件下仍保持一致性核心不变量
- 不出现双 owner、脏数据丢失、stale completion 提交、重复 ack 破坏状态机
- 对重复、乱序、延迟、丢包消息具备协议级防护

对 CC-EP 的直接映射：

- `ackMask` 保证 `InvalidateAck/RecallAck` 幂等
- `epoch gate` 拒绝陈旧完成消息
- `tombstone replay` 处理重复 `Clear` / 重放提交
- `timeout + retry` 处理可恢复丢包或长延迟

## 1.2 Availability

Availability 指：

- 故障发生后事务仍可在可接受时间内恢复、重试或收敛
- 可降级，但不能永久卡死在 `WAITING_*` 或 orphan 状态

对 CC-EP 的直接映射：

- drop 后靠 `timeout/retry` 恢复
- delay/jitter 下仍能完成 `grant -> clear`、`recall -> resp`、`invalidate -> ack` 链路
- combined faults 下保持 forward progress，允许局部吞吐退化

## 1.3 Serviceability

Serviceability 指：

- 能明确知道**故障是否命中**、**命中到哪条事务**、**触发了哪个保护机制**、**是否最终恢复**

对 CC-EP 的直接映射：

- 记录 `fault_rule_hit`、`fault_action_taken`
- 记录 `(addr, txnClass, opcode, epoch, reqId)`
- 记录 `protection_reaction`，如 `ackmask_dedup`、`stale_epoch_drop`、`timeout_retry`
- 记录 `recovery_outcome`，如 `completed` / `retried` / `timed_out_but_safe`

---

## 2. 故障分类体系

## 2.1 Message-layer（最高优先级）

这是 CC-EP 最直接、最相关的故障面。

| 子类型 | 说明 | 典型目标消息 | Phase |
|---|---|---|---|
| Drop | 静默丢弃消息 | `ClearReq` / `InvalidateAck` / `RecallResp` / `ReadReq` / `WritebackReq` | P1 |
| Duplicate | 同一消息重复发送 | `RecallResp` / `InvalidateAck` / `ClearReq` | P1 |
| Delay | 命中后延后 N ticks 再投递 | `Grant` / `RecallResp` / `UpgradeDone` | P2 |
| Reorder | 改变两条或多条消息的可见顺序 | `Resp vs Ack` / `Grant vs stale resp` | P3 |
| Corrupt | payload 字段损坏 | `epoch` / `reqId` / `flags` | 预留 |

**优先级结论**：Phase 1/2/3 的主体都应围绕 message-layer 展开。

## 2.2 Link-layer（中优先级，部分 deferred）

| 子类型 | 说明 | 价值 | Phase |
|---|---|---|---|
| Bandwidth throttling | 链路吞吐限速 | 验证拥塞与 timeout 容忍 | P2 |
| Queue saturation | 人工限制 hop buffer 深度 | 放大延迟/乱序窗口 | P2/P3 |
| Link flapping | 连接断开/恢复 | 高价值但侵入性大 | Deferred |

## 2.3 Timing-layer（中优先级）

| 子类型 | 说明 | 价值 | Phase |
|---|---|---|---|
| Processing delay | endpoint 处理延时抖动 | 验证 timeout 边界 | P2 |
| Clock jitter | 节点间 tick 视角漂移 | 多进程迁移有用 | Deferred |
| Scheduler jitter | 每 hop 调度抖动 | 提高真实感 | P2 |

---

## 3. 故障类型到协议保护机制的映射

## 3.1 核心保护机制

| 机制 | 目标问题 | 典型适用故障 |
|---|---|---|
| `ackMask` 幂等 | 重复 ack / 重放 ack 导致多次完成 | duplicate / reorder |
| `epoch gate` | 陈旧 resp/ack/clear 误提交 | reorder / delay / duplicate |
| `tombstone replay` | 重复 `Clear` / grant 提交重放 | duplicate / delay |
| `timeout` | 对端消息缺失或长期未达 | drop / delay |
| `retry/reissue` | 可恢复的 transient failure | drop / throttle / timing jitter |
| one-home authority | 防止多方同时提交 committed metadata | combined faults |
| outstanding stage guard | 防止在错误阶段消费完成消息 | reorder / duplicate |

## 3.2 故障→保护→预期行为

| 故障 | 主要保护 | 预期结果 |
|---|---|---|
| `InvalidateAck` duplicate | `ackMask` | 重复 ack 被去重；不会提前或二次完成 |
| `RecallResp` duplicate | `epoch gate` + stage guard | 仅首个合法响应生效；后续 stale/dup 被丢弃 |
| `ClearReq` duplicate | tombstone replay | 返回相同 accept/deny 语义，不重复提交目录 |
| `RecallResp` drop | timeout + retry | 事务可重试或失败返回，但 committed metadata 不损坏 |
| `Grant/Clear` delay | tombstone + timeout budget | 最终完成或超时安全退出，不留 grant orphan |
| `Ack/Resp` reorder | `epoch gate` + stage guard | 错序消息不越权推进阶段 |
| combined drop+dup | 多机制叠加 | Safety 恒成立；Liveness 允许降级但不可永久卡死 |

---

## 4. 架构设计：FaultPipeline + NetworkSim 集成

## 4.1 设计原则

1. **transport 纯净**：不把协议语义硬编码进网络层
2. **可选 inspector**：需要时才解析 opcode/txnClass/addr 等中粒度字段
3. **逐 hop 注入**：每次 enqueue/dequeue/schedule 都可命中规则
4. **同一套规则实现**：测试与仿真共用，避免 drift
5. **count-based 优先**：保证回归可重复

## 4.2 逻辑位置图

```text
   +-------------------+        +-------------------+
   |   EP/UBCC Sender  |        | EP/UBCC Receiver  |
   +---------+---------+        +---------+---------+
             |                            ^
             v                            |
      [FaultPipeline: endpoint hooks]     |
             |                            |
             v                            |
      +-----------------------------+     |
      | NetworkSim hop-scheduler    |-----+
      | - per-hop rule evaluation   |
      | - delay queue               |
      | - reorder window            |
      | - bandwidth shaping         |
      +-----------------------------+
             |
             v
      [FaultPipeline: rx hooks]
             |
             v
        Protocol state machine
```

## 4.3 组件分工

### A. FaultPipeline（endpoint 侧）
- 注册 message decoder / inspector
- 暴露结构化字段：`opcode`, `txnClass`, `addr`, `srcNode`, `dstNode`, `epoch`, `reqId`
- 负责产生 observability 事件

### B. NetworkSim hop-scheduler
- 逐 hop 评估 fault rule
- 执行 `drop/dup/delay/reorder`
- 维护每条规则命中计数、窗口队列、延迟队列

### C. RuleMatcher
- 按中粒度字段匹配：
  - `msgType`
  - `opcodeSubgroup`
  - `txnClass`
  - `src/dst`
  - `linkId`
  - `addrRange`
- 预留扩展位：`epoch`, `reqId`, `statePredicate`

### D. Inspector（可选）
- 默认关闭
- 打开后把 transport 消息投影为统一结构化事件
- 不改变传输语义，只提高 rule matching 与日志解释能力

## 4.4 规则执行语义

建议固定顺序：

1. decode / inspect
2. matcher 选出候选规则
3. 按优先级排序
4. 逐条评估 trigger（count / probability / time-window）
5. 首个命中规则执行 action
6. 发出 `fault_rule_hit` 事件
7. 若消息仍然存活，再进入 hop scheduler

**建议默认单命中策略**：同一 hop、同一消息仅允许一条规则生效；combined faults 通过多 hop 或多规则分阶段实现。

---

## 5. JSON 配置模型与完整示例

## 5.1 配置原则

- canonical JSON 为唯一标准格式
- legacy string 仅做导入层，导入后立即归一化成 JSON
- schema 既能表达 deterministic TC，也能表达 stress campaign

## 5.2 顶层 schema 建议

```json
{
  "version": "1.0",
  "mode": "count_first",
  "enableInspector": true,
  "defaults": {
    "maxReorderWindow": 4,
    "defaultDelayTicks": 0,
    "eventTrace": "fault_trace.jsonl"
  },
  "rules": []
}
```

## 5.3 规则字段定义

| 字段 | 含义 |
|---|---|
| `id` | 规则唯一标识 |
| `enabled` | 是否启用 |
| `layer` | `message` / `link` / `timing` |
| `action` | `drop` / `duplicate` / `delay` / `reorder` |
| `priority` | 数值越大优先级越高 |
| `match` | 匹配条件 |
| `trigger` | 触发条件 |
| `params` | 动作参数 |
| `observe` | 命中时的日志与计数策略 |

### `match` 子字段

```json
{
  "msgType": ["RecallResp"],
  "opcodeSubgroup": ["recall_response"],
  "txnClass": ["recall"],
  "srcNodes": [1],
  "dstNodes": [0],
  "linkIds": ["ubcc_1_to_0"],
  "addrRanges": [
    { "base": "0x80000000", "size": "0x1000" }
  ]
}
```

### `trigger` 子字段

```json
{
  "type": "count",
  "hit": 2
}
```

可扩展：
- `type: probability`, `p: 0.1`
- `type: time_window`, `startTick`, `endTick`

## 5.4 完整 JSON 示例（drop / dup / reorder / delay）

```json
{
  "version": "1.0",
  "mode": "count_first",
  "enableInspector": true,
  "defaults": {
    "maxReorderWindow": 4,
    "defaultDelayTicks": 0,
    "eventTrace": "m5out/fault_trace.jsonl"
  },
  "rules": [
    {
      "id": "drop_inval_ack_once",
      "enabled": true,
      "layer": "message",
      "action": "drop",
      "priority": 100,
      "match": {
        "msgType": ["InvalidateAck"],
        "opcodeSubgroup": ["invalidate_ack"],
        "txnClass": ["invalidate"],
        "srcNodes": [2],
        "dstNodes": [0],
        "addrRanges": [
          { "base": "0x90000000", "size": "0x40" }
        ]
      },
      "trigger": {
        "type": "count",
        "hit": 1
      },
      "params": {},
      "observe": {
        "emitTrace": true,
        "emitCounter": true,
        "tag": "tc_ras_drop_ack"
      }
    },
    {
      "id": "dup_clear_req_once",
      "enabled": true,
      "layer": "message",
      "action": "duplicate",
      "priority": 90,
      "match": {
        "msgType": ["ClearReq"],
        "opcodeSubgroup": ["grant_clear"],
        "txnClass": ["grant_handshake"],
        "srcNodes": [1],
        "dstNodes": [0]
      },
      "trigger": {
        "type": "count",
        "hit": 2
      },
      "params": {
        "copies": 1,
        "spacingTicks": 1
      },
      "observe": {
        "emitTrace": true,
        "emitCounter": true,
        "tag": "tc_ras_dup_clear"
      }
    },
    {
      "id": "delay_recall_resp",
      "enabled": true,
      "layer": "timing",
      "action": "delay",
      "priority": 80,
      "match": {
        "msgType": ["RecallResp"],
        "opcodeSubgroup": ["recall_response"],
        "txnClass": ["recall"],
        "srcNodes": [2],
        "dstNodes": [0],
        "linkIds": ["ubcc_2_to_0"]
      },
      "trigger": {
        "type": "count",
        "hit": 1
      },
      "params": {
        "delayTicks": 200
      },
      "observe": {
        "emitTrace": true,
        "emitCounter": true,
        "tag": "tc_ras_delay_recall"
      }
    },
    {
      "id": "reorder_resp_before_ack",
      "enabled": true,
      "layer": "message",
      "action": "reorder",
      "priority": 70,
      "match": {
        "msgType": ["RecallResp", "InvalidateAck"],
        "txnClass": ["recall", "invalidate"],
        "addrRanges": [
          { "base": "0xA0000000", "size": "0x1000" }
        ]
      },
      "trigger": {
        "type": "count",
        "hit": 1
      },
      "params": {
        "window": 2,
        "policy": "swap_first_two"
      },
      "observe": {
        "emitTrace": true,
        "emitCounter": true,
        "tag": "tc_ras_reorder_window"
      }
    }
  ]
}
```

---

## 6. Coverage Matrix

> 组织原则：报告按“保护机制”看覆盖；执行按“故障类型”投放规则。

| 故障类型 | 目标路径 | 保护机制 | 预期 Safety | 预期 Liveness | 预期恢复结果 |
|---|---|---|---|---|---|
| Drop `InvalidateAck` | `INVALIDATE -> WAITING_ALL_ACKS` | timeout + retry | 不得错误提交 unique | 可降级；允许重试 | 最终完成或超时返回但状态安全 |
| Drop `RecallResp` | `RECALL -> WAITING_TARGET_RESP` | timeout + retry + epoch gate | 不得提交 stale data | 可降级 | orphan 回收，不得卡死 |
| Duplicate `InvalidateAck` | ack 汇聚 | `ackMask` | 不得重复记账/提前完成 | 不影响 | 去重后正常完成 |
| Duplicate `RecallResp` | recall 完成 | stage guard + epoch gate | 只允许首个合法 resp 生效 | 不影响 | 重复响应被忽略 |
| Duplicate `ClearReq` | grant commit | tombstone replay | 不得二次提交目录 | 不影响 | 返回一致 replay 结果 |
| Delay `Grant/Clear` | grant handshake | timeout budget + tombstone | 不得遗留 grant leak | 可退化 | 最终 clear 或安全超时 |
| Delay `RecallResp` | recall 回收 | timeout + stage guard | 不得越阶段推进 | 可退化 | 完成时间拉长但可收敛 |
| Reorder `Ack/Resp` | 多消息竞态 | epoch gate + stage guard | 错序消息不可污染 committed | 视窗口可退化 | 旧消息被拒绝 |
| Combined drop+dup | invalidate/clear 混合 | `ackMask` + timeout + tombstone | Safety 恒成立 | 局部退化允许 | 通过重试收敛 |
| Stress throttling + delay | 高频多线竞争 | retry/backoff + timeout | 不得死锁/活锁 | 必须最终前进 | 吞吐下降但全部完成 |

---

## 7. TC 提案（RAS 新增用例）

下面给出 8 个建议 TC；其中前 6 个可作为最小交付集。

## TC-RAS1：单次丢失 InvalidateAck

**目标**
- 验证 `WAITING_ALL_ACKS` 下的 `timeout + retry`
- 验证不会因 ack 缺失而错误授予 unique

**故障配置**
- `action=drop`
- `msgType=InvalidateAck`
- `trigger=count(hit=1)`

**覆盖机制**
- timeout
- retry/reissue
- one-home authority

**通过标准**
- Safety：无双 owner、无旧 sharer 残留提交
- Liveness：允许重试后成功，或按设计返回 busy/timeout
- 日志必须出现 `fault_rule_hit=drop_inval_ack_once` 与 `protection_reaction=timeout_retry`

## TC-RAS2：重复 InvalidateAck 幂等

**目标**
- 验证 `ackMask` 去重

**故障配置**
- `action=duplicate`
- `msgType=InvalidateAck`
- `trigger=count(hit=1)`

**覆盖机制**
- `ackMask`

**通过标准**
- ack 计数只增长一次
- 不得提前完成或二次完成 outstanding
- 出现 `protection_reaction=ackmask_dedup`

## TC-RAS3：重复 ClearReq 的 tombstone replay

**目标**
- 验证 grant/clear 提交链路的 replay 语义

**故障配置**
- `action=duplicate`
- `msgType=ClearReq`
- `trigger=count(hit=2)`

**覆盖机制**
- tombstone replay

**通过标准**
- 目录 committed epoch/state 只推进一次
- 两次 `ClearReq` 返回结果一致
- 出现 `protection_reaction=tombstone_replay`

## TC-RAS4：RecallResp 延迟

**目标**
- 验证长延迟下 recall 路径的安全性和可恢复性

**故障配置**
- `action=delay`
- `msgType=RecallResp`
- `params.delayTicks=200~1000`

**覆盖机制**
- timeout budget
- stage guard

**通过标准**
- 不得因晚到 resp 提前/错误提交
- 若超时后又收到晚到 resp，必须由 `epoch gate` 或 stage guard 拒绝

## TC-RAS5：Resp/Ack 重排窗口

**目标**
- 验证错序消息不会越权推进状态机

**故障配置**
- `action=reorder`
- `window=2`
- 目标：`RecallResp` 与 `InvalidateAck` 或 `Grant/Clear` 相关消息

**覆盖机制**
- `epoch gate`
- stage guard

**通过标准**
- committed metadata 不受 stale/错序消息污染
- 日志出现 `protection_reaction=stale_epoch_drop` 或 `stage_mismatch_ignore`

## TC-RAS6：Drop + Dup 组合故障

**目标**
- 验证多保护机制叠加时系统仍安全收敛

**故障配置**
- hop1：drop 首个 `InvalidateAck`
- hop2：duplicate 后续 `ClearReq`

**覆盖机制**
- timeout/retry
- `ackMask`
- tombstone replay

**通过标准**
- Safety 全满足
- Liveness 可退化但最终完成或有界失败
- 不得留下 orphan outstanding

## TC-RAS7：压力场景下 delay + throttle

**目标**
- 验证高并发下 forward progress

**故障配置**
- `delay` 多类消息
- link `bandwidth throttling`
- 多地址、多节点、概率或时间窗触发

**覆盖机制**
- retry/backoff
- timeout

**通过标准**
- 全部工作负载最终结束
- 不得 deadlock / livelock
- 吞吐下降可接受，但数据收敛一致

## TC-RAS8：同线双端竞争 + 重排

**目标**
- 验证升级/召回竞态在 reorder 下仍可串行化

**故障配置**
- 两节点并发对同一行发 `ReadUnique/CleanUnique`
- 对 ack/resp 注入 reorder + 少量 delay

**覆盖机制**
- one-home authority
- `epoch gate`
- outstanding stage guard

**通过标准**
- 最终值只能是两种竞争写之一
- 不得出现 stale resurrection
- 不得出现多 owner

---

## 8. 可观测性与监控设计

## 8.1 事件设计目标

最小可交付要求不是“打印日志”，而是：

1. 能证明规则真的命中
2. 能关联到具体事务与地址
3. 能看到协议保护机制是否接住故障
4. 能判断最终恢复是否完成

## 8.2 建议事件类型

| 事件类型 | 含义 |
|---|---|
| `fault_rule_hit` | 某条 rule 命中某消息 |
| `fault_action_taken` | drop/dup/delay/reorder 已执行 |
| `protocol_reaction` | 协议侧保护机制反应 |
| `recovery_outcome` | 事务最终恢复结果 |
| `fault_summary` | 测试结束后的命中统计 |

## 8.3 通用字段定义

| 字段 | 说明 |
|---|---|
| `tick` | 当前仿真 tick |
| `node` | 本地节点 |
| `srcNode` / `dstNode` | 消息源/目的节点 |
| `linkId` | 命中的 hop/link |
| `ruleId` | 规则 ID |
| `action` | `drop/duplicate/delay/reorder` |
| `msgType` | 外层消息类型 |
| `opcodeSubgroup` | 协议子类，如 `invalidate_ack` |
| `txnClass` | `read/recall/invalidate/grant_handshake/upgrade` |
| `addr` | line address |
| `epoch` | 协议 epoch |
| `reqId` | 事务请求 ID |
| `outstandingStage` | 命中时所处阶段 |
| `protectionReaction` | `ackmask_dedup` / `stale_epoch_drop` / `timeout_retry` 等 |
| `result` | `completed/retried/ignored/timed_out_safe` |

## 8.4 JSONL trace 示例

```json
{"event":"fault_rule_hit","tick":102300,"ruleId":"dup_clear_req_once","action":"duplicate","msgType":"ClearReq","txnClass":"grant_handshake","srcNode":1,"dstNode":0,"addr":"0x90000040","epoch":19,"reqId":77}
{"event":"protocol_reaction","tick":102305,"addr":"0x90000040","epoch":19,"reqId":77,"protectionReaction":"tombstone_replay","outstandingStage":"WAITING_CLEAR"}
{"event":"recovery_outcome","tick":102330,"addr":"0x90000040","epoch":19,"reqId":77,"result":"completed"}
```

## 8.5 验证恢复的观察点

建议至少提供以下计数器或 trace 聚合：

- `fault_hits_by_rule`
- `duplicate_dedup_count`
- `stale_epoch_reject_count`
- `timeout_retry_count`
- `tombstone_replay_count`
- `outstanding_timeout_count`
- `recovery_completed_count`
- `recovery_safe_abort_count`

---

## 9. 分阶段实施路线

## Phase 1：Drop / Dup + 兼容既有 gem5 fault 规则

**目标**
- 建立最小 RAS 骨架
- 优先覆盖最关键的 message-layer 故障

**范围**
- `drop`
- `duplicate`
- count-based trigger
- endpoint inspector
- 命中 trace + 基本 counter

**交付物**
- `FaultInjector/FaultPipeline` 基础框架
- canonical JSON loader + legacy string importer
- TC-RAS1/2/3/6 最小集

**成功标准**
- 故障可稳定复现
- 至少能验证 `ackMask`、`tombstone replay`、`timeout/retry`

## Phase 2：Delay / Jitter / 独立 scheduler 模块化

**目标**
- 支持时序故障与更真实的网络退化

**范围**
- `delay`
- processing jitter
- bandwidth throttling
- delay queue / tick scheduler 模块化

**交付物**
- TC-RAS4/7
- 延迟统计与恢复时间指标

**成功标准**
- 延迟场景下 Safety 恒成立
- Availability 指标可量化（例如平均恢复 ticks、超时率）

## Phase 3：Reorder Window + 深度可观测

**目标**
- 覆盖最难的错序竞态

**范围**
- `reorder`
- window control
- richer causality trace
- 可选协议阶段前置条件匹配

**交付物**
- TC-RAS5/8
- `stale_epoch_drop`、`stage_mismatch_ignore` 等机制的系统性覆盖报告

**成功标准**
- 错序场景下 committed state 不受污染
- 能清晰解释每次错序为何被协议挡住

---

## 10. 工具与基础设施需求

| 组件 | 作用 | Phase |
|---|---|---|
| `FaultPipeline` | endpoint 注入与事件发射 | P1 |
| `NetworkSim hop-scheduler` | per-hop 故障执行、delay/reorder 窗口 | P1-P3 |
| `JSON rule loader` | canonical config 加载 | P1 |
| `legacy importer` | 旧字符串规则导入 | P1 |
| `Inspector/Decoder` | 提供 opcode/txnClass/addr 粒度 | P1 |
| `fault_trace.jsonl` | 结构化故障 trace | P1 |
| counter/stats bridge | 汇总机制命中与恢复结果 | P1 |
| TC harness helpers | 为每个 TC 注入独立 fault profile | P1-P3 |

---

## 11. TLOC 估算

> 仅为前瞻性估算；实际 TLOC 会受现有 gem5 fault rule 复用程度影响。

| 模块 | 主要内容 | 估算 TLOC |
|---|---|---:|
| JSON schema / loader / importer | canonical JSON + legacy import | 250-400 |
| FaultPipeline core | matcher / trigger / dispatcher | 350-550 |
| Inspector / decoder | 提取 msgType/opcode/txnClass/addr | 180-320 |
| NetworkSim hooks | hop-scheduler 接线 | 200-350 |
| Drop / Dup actions | 基础动作实现 | 120-180 |
| Delay / jitter queue | Phase 2 | 180-260 |
| Reorder window manager | Phase 3 | 220-340 |
| Trace / counters / stats | JSONL + stats integration | 150-250 |
| TC harness + 6~8 个新 TC | 配置、校验器、断言 | 350-600 |
| 文档/样例/回归 glue | profiles、例子、说明 | 100-180 |
| **合计** |  | **2100-3430** |

### 分阶段 TLOC 粗估

| Phase | 范围 | TLOC |
|---|---|---:|
| Phase 1 | drop/dup + JSON + trace + 4 个 TC | 1000-1550 |
| Phase 2 | delay/jitter + 2 个 TC | 450-750 |
| Phase 3 | reorder window + 深度 trace + 2 个 TC | 650-1130 |

---

## 12. 建议验收口径

本计划建议采用以下统一验收口径：

1. **Safety 必须全部满足**：任何故障注入都不得破坏 coherence 基本不变量。
2. **Liveness 分级评估**：
   - duplicate/reorder：原则上应本地收敛
   - drop/delay：允许 timeout + retry 或安全 abort
3. **Serviceability 必须可证明**：
   - 故障命中可见
   - 保护机制反应可见
   - 恢复结论可见

若后续进入实现阶段，建议以 **Phase 1 → TC-RAS1/2/3/6** 作为首个最小闭环里程碑。
