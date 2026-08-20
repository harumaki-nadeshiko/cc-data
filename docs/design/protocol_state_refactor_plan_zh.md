# 协议状态固定容量重构计划

> 目标：把 UBCC—UBIO—framework/networksim—gem5 EP 的分布式、动态、PA-keyed 运行态，迁移为可审计、可综合、端到端有反压的固定容量协议机器。本文只给设计与迁移计划，不修改代码。

## 1. 设计目标与非目标

### 1.1 必须满足

1. 每个逻辑事务在每一组件中只有一个 owning slot；其他索引只保存 slot handle，不复制事务正文。
2. 所有内存与队列有硬上限；FULL 是协议可见、可测试的正常状态，不是 OOM、无限阻塞、静默 drop 或事后 panic。
3. 所有异步完成携带 generation；晚到、重复、乱序 completion 不得命中新一代槽。
4. 已接受请求在接受时就拥有最终 completion 所需的 slot、waiter 和输出 credit，或有协议证明安全的预留池。
5. 容量只影响性能/admission，不改变 coherence safety；缩小容量的 formal small model 应与生产设计同构。
6. 软件实现不再依赖 `std::map/set/deque/vector` 的无界增长；RTL 可直接映射为 SRAM、ring、bitmap、有限 CAM。

### 1.2 非目标

- 不在本重构中改变 MESI/VI 状态语义、home 地址映射或 H64 持久化布局。
- 静态拓扑表、配置解析临时 vector、host DRAM 数据面不要求全部消除；要求消除的是**运行时协议容量的不确定性**。
- 不以“把 map 换 unordered_map”作为完成标准；那只改变平均查找复杂度，不解决容量和生命周期。

## 2. 统一抽象

### 2.1 Transaction Handle

建议 wire/local completion handle：

```text
TxnHandle = { endpoint_id, slot_index, generation }
```

- `slot_index` 宽度由容量决定，如 128 槽为 7 bit、256 槽为 8 bit。
- `generation` 建议至少 16 bit；每次 Free→Allocated 自增，0 保留无效。生产可用 32 bit；formal 可缩为 2–3 bit并显式检查 wrap 条件。
- `reqId` 可以编码 handle，也可保留外部 reqId 并在槽内记录；但所有本地 callback/completion 必须携带 handle。
- 协议 epoch 表示数据/权限世代；slot generation 表示本地资源世代。两者不可互相替代。当前 UBCC 的 snapshot epoch 已能防部分旧持久化完成【modules/ubiomodule/UBCCController.hh:745-752】，但不能证明槽释放/重分配安全。

completion 接收规则：

```text
if slot_index out of range: InvalidCompletion
else if !slot.valid: LateCompletionDrop
else if slot.generation != completion.generation: StaleCompletionDrop
else if expected_kind/state mismatch: ProtocolError
else consume exactly once
```

重复 completion 可由 `completion_seen` bit 幂等处理；generation mismatch 只计数，不改变任何当前状态。

### 2.2 Unique Transaction Slot

每组件槽都采用共同生命周期：

```text
FREE -> ALLOCATED -> WAIT_INPUT/WAIT_OUTPUT/WAIT_COMPLETION
     -> COMPLETING -> RETIRING -> FREE(next generation)
```

一个槽至少含：valid、generation、kind、PA、外部 reqId/epoch、阶段、owner/requester、deadline、waiter head/count、output reservation、data-buffer index、expected completion mask。原先散落在 pending/read/grant/upgrade/active-recall/deferred map 的字段移入同一槽。

PA 索引只负责“是否已有该 PA 主事务”：固定 set-associative hash 或小 CAM，value 为 handle。索引插入和槽分配必须原子化；失败时不留下半个事务。

### 2.3 Fixed Waiter Pool

不用 `map<PA,deque<Waiter>>`，采用：

```text
WaiterPool[N]
free_head
TxnSlot.wait_head / wait_tail / wait_count
Waiter.next
```

- waiter 保存完整请求语义和 source completion route，不只保存 PA。
- admission 同时检查 per-slot limit 与 global free count。
- 可按类别分区或设最低保留：coherence response、capacity replay、persistence、control；防止普通 read 耗尽 invalidate/recall 所需资源。
- retire 必须由 handle 精确删除，不扫描/猜测“同 PA 看起来相同”的请求。当前 TC224 focused model只证明了特定 waiter 匹配语义【verification/fv_coverage_fidelity.md:135-140】，统一 pool 后应证明通用 alloc-link-unlink-free invariant。

### 2.4 Fixed Output Rings 与 Credits

每条输出通道采用固定 ring：`entries[N], head, tail, count`。发送者只有在以下两种条件之一成立时才能把协议阶段推进为“sent”：

1. 下游立即接受；或
2. 本地 ring 已成功占用一个 entry，且该 entry 的最终发送责任明确。

绝不能像当前多处实现那样先建立事务，再无界 `push_back` 可靠输出。EP-RNF 当前在 MessageBuffer 拒绝后追加 deque【gem5/gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:1115-1149】，UBIO reliable response 则到 8192 才 panic【modules/ubiomodule/ubio_main.cc:3424-3447】；两者都应改为 credit-before-accept。

每 ring 应包含：

- 数据 credit；
- 不能被数据使用的控制 reserve（invalidate/recall ack、terminate、sync、credit return）；
- high/low watermark，仅用于性能调度，不代替硬界；
- 每类 FULL/blocked-cycle/highwater counter。

framework Port 不再使用无限阻塞 send。当前 `sndtimeo=-1` 且 payload 用 blocking send【framework/Port.cc:247-259】【framework/Port.cc:314-327】，重构后 API 统一为：

```text
SendResult = Accepted | Full | Closed | Invalid
```

ZMQ HWM 设置成外显 ring 深度，只作为防御；不能让 libzmq 再隐藏 8192 条消息。

### 2.5 显式 FULL 行为

| 入口类别 | FULL 行为 |
|---|---|
| 可重试 requester 新请求 | 返回 `RetryableBusy`/CHI Retry；reqId 是否复用由规范明确，默认同事务复用。 |
| 已接收且协议要求必答的 snoop/control | 使用预留槽/credit；预留也满属于设计 invariant 违反，fail-fast 并保存最小诊断，不得 drop。 |
| waiter pool 满 | 不接受新请求；不能先 ACK ingress 再丢 waiter。 |
| output ring 满 | 保持 owning slot 在 `WAIT_OUTPUT`；停止相应 ingress credit，不复制消息到第二个队列。 |
| tombstone/replay cache 满 | 先清理已过期项；仍满则阻止会生成新 tombstone 的 transaction commit/admission，不能覆盖窗口内 identity。 |
| fault injection queue 满 | 测试失败 `FaultInjectorOverflow`，因为继续运行会改变被测故障语义。 |
| shutdown control 满 | 使用独立 reserve；若 reserve 满，重复同 generation 的 shutdown 合并，而非追加。 |

## 3. 组件目标结构

### 3.1 UBCC：HomeTxnTable

以 `HomeTxn[128]` 取代 `_outstandingReqs`、`_immediateGrantData`、`_pendingReplayActive`、主要 persistence maps。当前 outstanding 已有 128 总界【modules/ubiomodule/UBCCController.hh:338-342】，因此首阶段可保持吞吐不变。

槽字段覆盖：grant/recall/invalidate/upgrade 阶段、intended result、64 B data-buffer index、requester、epoch/reqId、tombstone intent、resident/H64 persistence generation、waiter list。PA→slot 固定索引保证每 PA 一个主事务。

- requester waiter pool 256，per PA 32，延续当前显式界【modules/ubiomodule/UBCCController.hh:330-342】。
- persistence waiter 64，per PA 8，延续当前界【modules/ubiomodule/UBCCController.hh:878-886】。
- tombstone ring 1024；read replay cache 4096，后续根据最大端到端重传窗口证明是否增加。
- async backstore API 一律传 `TxnHandle/PersistHandle`；`onBackstore*Complete` 不再只靠 PA/snapshot epoch。

关键 invariant：

1. 每个有效 PA index 恰好指向一个 valid 且 PA 相同的槽。
2. 每个 waiter 恰好处于 free list 或一条 slot list，不可两者皆是/皆非。
3. committed directory 与 intended result 分离，直到匹配 Clear/Done；保留现有 reserve-then-commit 语义【modules/ubiomodule/UBCCController.hh:409-414】。
4. Free 槽没有 output reservation、data buffer 或 waiter。

### 3.2 UBIO：I/O Slot 层

- `MetaLineSlot[64]` 合并 read/write/deferred map+array；当前 read/write各32的总并发不变【modules/ubiomodule/ubio_main.cc:868-895】【modules/ubiomodule/ubio_main.cc:1072-1081】。
- `MetaPageSlot[64]` 替换无界 `_pendingReads/_pendingWrites`。
- `BackstoreCompletionRing[128]` 分别承载 fill/ack 或共用 tagged union。
- `DsmSlot[256]` 保持当前容量【modules/ubiomodule/ubio_main.cc:683-706】，增加 generation。
- legacy chain 若迁移期仍存在：`ChainSlot[32]`、每槽固定 32 page offsets；最终 H64-only 后删除三张 chain map。当前 chain 被三 map 拆分【modules/ubiomodule/ubio_main.cc:1635-1639】，是优先清理对象。
- `NetworkResponseRing[256]` 取代 8192 deque，并让 Port credit 阻止继续接收。
- HomeVI 的四 map 合并为 `HAWireTxn[128]`；当前仅 `requests` 受 `maxActive` 约束【modules/ubiomodule/ubio_main.cc:1935-1988】，重构后所有 wire/expected/writeback 子状态共享同一总界。

### 3.3 networksim/framework：端到端 credit 边界

framework 每端口 RX/TX 256、control reserve 16；networksim transit 4096、每 ingress 最多128 outstanding。当前 networksim 在 65,536 满时停止 receive 的行为是正确原型【modules/networksim/networksim_main.cc:226-239】，但容量过大且 Port 下面仍有 8192 隐藏 HWM。

迁移后 credit 流：

```text
UBAdapter/UBIO TX ring free
  -> framework TX credit
  -> networksim ingress/transit/egress credit
  -> peer framework RX credit
  -> endpoint input slot
```

软件 ZMQ 不能原生提供完整跨进程 credit 时，先采用显式 credit control message + 本地 ring；HWM 与 ring 对齐。sync/terminate/credit-return 走 control reserve，避免数据面拥塞造成全局停机死锁。

### 3.4 UBAdapter：AdapterTxnTable

`AdapterTxn[128]` 合并 `_inflight*`、`_pendingByReqId`、`_readyResponses` 和 clear retry；当前只有 read 有64界【gem5/gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.hh:291-300】，其他类别按保留配额纳入总界。response 到达时用 reqId/handle直接定位槽并置 `response_ready`。callback 存 callback-id/owner slot，而不是通用 `std::function`（host 适配层可在表外维护定长 callback array）。

`ReliableTxRing[256]`、`ControlRxRing[64]` 替代两个无界 deque【gem5/gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.hh:317-323】。同步 polling 只观察槽状态，不把响应复制进 ready map。

### 3.5 EPBackend：NodeTxnTable

`NodeTxn[256]` 合并 pending read/grant/upgrade、grant data、active recall、deferred invalidate、HA probe/miss。状态机显式允许 Read→Grant→Clear 在同槽内推进，解决当前为防二次 reqId而增加多个 map的根因【gem5/gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh:986-1013】。

requester line metadata 与瞬态事务分开：`RequesterLineCache[4096]`，4-way；eviction 必须触发现有 writeback/evict 协议。HA completed response 固定 replay cache 256，不使用“超过1024再清”的软界。

跨 UBAdapter/EPBackend 的所有回调携带双方 handle；EPBackend slot retire 前取消/断开 adapter child slot，晚到 response 只能命中 generation mismatch。

### 3.6 EP-RNF / EP-SNF

- EP-RNF：`RnfTxn[128]` 把 pending CHI、queued snoop、retry、upgrade、HN response、outer flag收拢。每槽最多一个 queued snoop，符合当前按 PA 串行的设计意图【gem5/gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.hh:473-479】。req/rsp/data ring各64，snoop reserve 16。
- EP-SNF：`SnfTxn[128]` 合并 pending write/grant/writeback；retry/writeback ring各64，rsp/data各64。只有在 completion output credit 已保留时才接受 HN 请求。

控制器之间不再通过“容器为空/是否有 key”隐式推导阶段，而是读取 owning slot 的枚举状态。所有 terminal retry exhaustion 必须 retire slot并向上游生成可观察结果。

### 3.7 MetaRNF

保留当前 `FlightSlot[8]` 的结构基础【gem5/gem5/src/mem/ruby/protocol/chi/ep/MetaRNFController.hh:78-106】【gem5/gem5/src/mem/ruby/protocol/chi/ep/MetaRNFController.hh:188-194】，增加 generation 和 output reservation。`PendingOp[128]` 统一 legacy/line wait；每 PA 8 的界保持不变【gem5/gem5/src/mem/ruby/protocol/chi/ep/MetaRNFController.hh:59-62】。8-entry CAM 取代 scoreboard map；pending pool链取代 `_waitQueues` 与 `_perAddressPendingCount` 的重复表达。req/rsp/data ring各32。

## 4. 为什么现有形式化模型没有阻止已观察到的 bug

现有 formal 成果仍有价值，但其证明对象与本次问题不同：

1. **手写抽象，不是代码提取。** 项目明确声明 TLA+ 与 C++ 对应由人工维护，不主张“model=code”【verification/fv_coverage_fidelity.md:104-114】。因此 C++ 新增一张 map、漏删一条 ready response、回调重入或 ZMQ 队列行为，不会自动出现在模型中。
2. **许多模型把事务存储抽象成理想的每 PA 单槽。** multi-PA 模型直接定义 `slot[pa]`，并假定只有 free 时才能 start【verification/tla/ubcc_multi_pa.tla:5-19】【verification/tla/ubcc_multi_pa.tla:53-68】。这证明“若实现真是唯一槽”时的隔离性，却没有证明 C++ 中 `_pendingReadTxns/_pendingGrantTxns/_pendingByReqId/_readyResponses` 的分布式表示满足该抽象。
3. **容量、backpressure、allocator failure 被排除。** coverage 文档曾把 Bloom/backstore/MetaRNF、真实延迟/ZMQ列为抽象或非目标【verification/fv_coverage_fidelity.md:78-89】【verification/fv_coverage_fidelity.md:142-150】。观察到的 full、旧 completion、输出堵塞、重入 bug恰好发生在这些 refinement 层。
4. **focused model只闭合特定语义。** TC224 模型证明特定 committed waiter 的精确 retirement，明确抽象掉 victim selection/H64 timing；EP snoop模型抽象 CHI payload/TBE【verification/fv_coverage_fidelity.md:135-167】。它们不证明全局 waiter pool容量、输出 credit或跨组件reqId生命周期。
5. **公平性假设隐藏调度实现。** 文档指出 cleanup liveness依赖 `wakeup()` 周期调度，而该假设本身未被代码验证【verification/fv_coverage_fidelity.md:154-163】。类似地，“队列最终发送”“credit最终返回”若直接设 WF，可能掩盖实际事件未重排程或控制消息被数据堵死。
6. **协议 epoch 抽象不足以捕获资源 ABA。** 旧 completion 命中新复用槽是 allocator/generation refinement bug；即使 MESI epoch单调，仍可能调用错误 callback或释放错误 credit。

因此现有 PASS 应解释为：核心协议状态机在其抽象与公平性假设下满足性质；它不是动态容器实现、传输容量和异步资源生命周期的 refinement proof。

## 5. 需要新增的 refinement models

### 5.1 Slot Allocator / Generation 模型

参数缩小为 2–3 slots、2-bit generation，允许 alloc/free/realloc、completion duplicate/delay/reorder。性质：

- stale generation 永不改变新槽；
- completion最多消费一次；
- 无 valid slot 被 free list再次分配；
- generation wrap 前不存在仍可能到达的旧 completion；若无法证明，规定 drain/quiesce 或增大 generation。

### 5.2 Waiter Pool Refinement

模型包含 free list、per-slot linked list、取消、精确 retirement、replay重入。证明：

- conservation：`free + linked = N`；
- unique ownership；
- FIFO/优先级规则；
- FULL 不改变已接受 waiter；
- owning txn retire 后没有悬挂 waiter。

它应取代只针对某个 PA/请求类型的 focused waiter抽象，同时保留 TC224 的 exact-match性质。

### 5.3 Output Ring / Credit 网络模型

至少建两 endpoint + networksim，容量 1–3，包含 data/control 两类、credit return、下游暂停、重试。证明：

- no drop/no duplicate/order（按通道规范）；
- `produced = queued + delivered` conservation；
- count永不越界；
- control reserve不被data耗尽；
- 在明确公平性“下游最终消费、链路最终送达”下进展；不对永久断链虚假证明 liveness。

### 5.4 Distributed-to-Unique-Slot Refinement

迁移期同时建“旧 ghost maps”和“新 slot machine”：每次 C++ trace event更新抽象状态，检查 refinement relation：

```text
Old pendingRead + pendingGrant + grantData + adapterPending
  refines exactly one NewTxnSlot state
```

若旧表示同时出现两个 live reqId、缺字段或孤儿项，refinement assertion立即失败。这是现有核心模型与实现之间缺失的一层。

### 5.5 Backstore/MetaRNF Completion 模型

包含 UBCC txn、UBIO I/O slot、MetaRNF flight、乱序/重复/晚到 completion、RetryableBusy。证明：

- snapshot epoch + generation 双匹配；
- FULL 不产生 callback丢失；
- 每个 accepted op最终恰有一个 terminal completion（在I/O公平假设下）；
- 同 PA serialization 与不同 PA并行；
- callback不可同步递归重新分配正在retire的槽。

### 5.6 Trace Refinement

现有文档已把 trace validation列为改进方向【verification/fv_coverage_fidelity.md:106-111】。新实现应输出稳定事件：`ALLOC, ENQUEUE_WAITER, RESERVE_OUTPUT, SEND, COMPLETE(gen), RETIRE, CREDIT_RETURN, FULL`。离线 checker 将真实 E2E trace投影到 TLA+ action并校验每个 handle的合法状态序列。formal证明抽象机；trace refinement检查代码确实实现该抽象机。

## 6. 分阶段迁移

### Phase 0：规格冻结与观测（无语义变化）

- 为审计表中的每个容器定义 owner、容量、插入/删除点、FULL语义、highwater。
- 增加统一 transaction lifecycle文档和事件字典。
- 建立容量 dashboard：current/highwater/full/retry/stale-completion/oldest-age。
- 冻结新的无界容器进入协议路径；评审模板要求capacity proof。

退出条件：每个运行态容器都可归入“删除、固定化、静态配置/数据面例外”。

### Phase 1：先固定 transport 输出

- framework Port引入显式非阻塞SendResult、TX/RX ring和control reserve；ZMQ HWM与ring对齐。
- networksim将默认 transit降至4096，并实现per-port credit；保持满时停止 ingress。
- UBIO、UBAdapter、EP-RNF、EP-SNF、MetaRNF 的可靠输出先换ring，不动内部事务map。

理由：若输出仍无界，内部槽固定后会把死锁/内存增长转移到边界，无法验证端到端容量。

### Phase 2：generation-tagged async completion

- 从 DSM、MetaRNF、backstore completion 开始，因为它们边界清楚。
- 增加 slot generation；旧 PA-only API在兼容期包装，但必须查到handle后才能调用新核心。
- 注入 delayed completion 跨越 slot retire/reallocate，确认只增加 stale counter。

### Phase 3：UBAdapter + MetaRNF 槽化

- 它们已有较清楚的 reqId/flight 边界与部分硬限：read64、MetaRNF flight8/pending128。
- 双轨 shadow mode：旧 map仍驱动行为，新 slot同步更新并assert等价；稳定后切换driver，最后删除旧map。

### Phase 4：EP-RNF / EP-SNF / EPBackend 槽化

- 先 controller本地状态，后跨controller handle。
- EPBackend把 Read→Grant→Clear 合为单槽；RequesterLine cache独立容量化。
- 为 snoop/control预留槽与输出credit，执行capacity=1/2极限测试。

### Phase 5：UBCC + UBIO home 槽化

- HomeTxn[128] shadow对照现有 outstanding；逐步吸收 immediate data、waiter、replay、persistence。
- 固定 waiter pool；保留现有per-PA32/总256等外部行为，降低迁移风险。
- tombstone/replay filter固定化；legacy Schema A chain退出生产路径。

### Phase 6：删除兼容状态并综合化

- 删除 ghost maps、PA-only completion、通用协议路径 `std::function/shared_ptr`。
- 把参数导出为单一 capacity manifest，生成C++常量、RTL参数、formal cfg与测试矩阵。
- 完成FPGA SRAM banking/port conflict分析和时序预算。

## 7. 测试计划

### 7.1 单元/属性测试

- ring wrap、满/空同时操作、control reserve、credit重复返回。
- slot generation wrap、晚到/重复/错误kind completion。
- waiter alloc/link/unlink/cancel/owner retire；随机序列后pool conservation。
- FULL路径不得改变目录、epoch、accepted count或丢 callback。

### 7.2 容量阶梯

所有组件用容量 `1,2,3,默认值` 运行同一协议场景。小容量更容易暴露“接受前未预留completion资源”、环形索引错误和公平性依赖。测试必须验证结果相同，仅延迟/retry次数不同。

### 7.3 故障与生命周期

- completion在retire前、retire同tick、reallocate后到达；duplicate×2、reorder、drop+timeout。
- transport满时注入recall/invalidate/terminate，验证control reserve。
- 退出时所有slot/ring/waiter必须drain或以明确cancel终结；不得依赖进程销毁回收“正确性状态”。

### 7.4 Differential / Shadow

迁移期旧实现与新shadow机并行：比较每个协议动作后的 committed directory、slot abstract state、输出消息tuple。容器内部布局可不同，但抽象投影必须相同。发现差异时记录最短事件前缀。

### 7.5 性能与内存

- 记录highwater、FULL率、平均/99.9% wait、输出blocked cycles。
- host使用分配统计确认稳态协议运行无动态分配。
- 默认容量接受标准：正常workload FULL近零；stress workload可FULL但必须进展且内存常数。

## 8. Formal 计划与门禁

1. **F0 allocator**：2 slots/2-bit generation exhaustive；安全性质全过。
2. **F1 waiter**：2 txn/3 waiters，含重入/cancel；conservation与精确retire全过。
3. **F2 ring-credit**：2 endpoints、容量1–2、data/control；安全+liveness（明确公平假设）。
4. **F3 UBCC refinement**：核心协议action不变，资源层加入slot/waiter/output FULL；证明FULL stutter不改变目录安全。
5. **F4 EP refinement**：RNF/SNF/Backend容量与snoop reserve；证明无协议环形等待。
6. **F5 Meta/backstore**：generation completion与RetryableBusy；证明exactly-once terminal outcome。
7. **F6 trace conformance**：真实trace无非法transition、无handle ABA、最终free-list守恒。

每个模型报告必须列出：容量、节点/PA范围、公平假设、被抽象的payload/transport行为、代码event映射。禁止再用“核心模型PASS”泛化声称实现的queue/allocator已获证明。

## 9. 迁移不变量与评审清单

每个PR必须回答：

1. 新请求在哪一步原子取得txn slot、waiter与completion output credit？
2. FULL返回给谁，是否保留原reqId/handle，是否可能静默drop？
3. 每个异步回调携带哪个generation？晚到时做什么？
4. 一个事务是否还能同时存在于两个owning容器？若是，refinement relation是什么、何时删除？
5. cancel/timeout/peer-exit是否释放所有child slots、data buffer、waiter和credit？
6. control reserve是否可能被普通data占用？
7. capacity=1时是否仍正确？下游永久停止时内存是否保持常数？
8. formal模型的公平性是否由真实调度/credit机制支持？

## 10. 完成定义

重构完成不是“所有map都消失”，而是同时满足：

- 审计范围内所有运行态协议状态有硬界与明确FULL语义；
- 同组件每事务唯一owning slot；
- 所有跨异步边界completion有generation；
- 所有可靠输出使用固定ring/credit，无无限阻塞和隐藏大HWM；
- 稳态不做协议动态分配；
- capacity=1/2、故障、退出、长时stress下内存恒定且无孤儿slot；
- 新resource refinement models通过，真实trace通过conformance checker；
- 核心MESI/VI formal invariants继续通过，并明确区分“协议抽象证明”和“实现refinement证明”。
