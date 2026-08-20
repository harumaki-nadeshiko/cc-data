# 协议运行态容量审计

> 审计基线：Git `6b2a14cfd8e8b12eba7c94b2c933d25a62713b30`，2026-08-21。
> 范围：UBCC、UBIO、networksim、framework、UBAdapter、EPBackend、EP-RNF、EP-SNF、MetaRNF；仅审计运行态协议/传输状态，不把静态配置表、测试局部变量或容量本身即为功能数据面的 DRAM 镜像误判为“事务队列”。
> 方法：新鲜源代码静态审计；未编译、未运行测试，因此内存数字是基于字段与容器常见 64 位实现开销的量级估算，不是 ABI `sizeof` 结果。

## 1. 结论摘要

当前实现不是“全面无界”，而是三种状态混合：

1. **已经良好定界的固定表**：H64 host 的 128 个事务槽、8 个 RMW flight、每 bucket 8 waiter；UBIO DSM 256 个异步槽；MetaRNF 的 8 flight、128 line-op、64 legacy write；networksim 的 65,536 转发项。
2. **逻辑上有限但用动态容器表达的状态**：UBCC `_outstandingReqs` 最多 128、waiter 总数分别 256；UBAdapter read 最多 64；MetaRNF scoreboard 最多 8。它们虽有 admission 检查，仍存在多表复制、漏删、动态分配、迭代时重入等风险。
3. **真正无硬上限或只受外部流量/拓扑/生命周期间接限制的状态**：UBAdapter 的 pending/ready/reliable/deferred 表队列，EPBackend 的多个 PA/reqId map，EP-RNF/EP-SNF 输出队列，UBIO legacy MetaRNF page 请求、fault-delay、backstore completion、barrier generation，framework 旧 PseudoMemPort 队列以及未显式设置 HWM 的 ZMQChannel/ZMQTransport。

最大结构性问题不是某一个 `std::map`，而是**同一事务身份被拆成多个容器**。例如 requester read/grant/clear 在 UBAdapter、EPBackend、UBCC 分别拥有 pending、ready、grant data、grant txn、outstanding、tombstone、waiter 副本。某一步漏删或异步旧完成晚到，就会产生“孤儿状态”“旧完成命中新事务”“一个 PA 两个 reqId”或容量永久占用。EPBackend 注释已经记录了曾因 fresh reqId 造成死循环，以及为防同一 Home PA 分配第二个 reqId而新增独立 map 的历史【gem5/gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh:1004-1013】。

建议统一采用：**每组件固定事务槽 + generation-tagged handle、固定 waiter pool、固定输出 ring/credit、明确 FULL 返回语义**。建议默认容量见第 6 节。

## 2. 容量分类与内存估算口径

- **硬界（H）**：数组长度、常量检查或 transport HWM 能阻止继续增长。
- **间接界（I）**：受拓扑、地址空间、另一个表或配置限制，但容器自身无一致的 admission invariant。
- **无界（U）**：生产者可持续追加，代码中无硬上限。
- `std::map/set` 每节点常见额外开销约 40–64 B，再加 key/value；`deque` 有块表与分块开销；`std::function`、`shared_ptr` 和 CHI 智能指针可能继续堆分配。
- framework wire payload 上限为 1024 B【framework/MemMessage.hh:9-10】【framework/MemMessage.hh:39-42】；旧 Pseudo packet 含 512 B payload【framework/PseudoMemPacket.hh:10-18】。因此“消息队列 × 深度”可按约 0.6–1.2 KiB/项估算，具体取决于队列保存完整消息还是智能指针。

## 3. 分组件清单

### 3.1 UBCC

| 状态 | 当前容器/界 | 风险与近似内存 | 建议容量/形态 |
|---|---|---|---|
| 活跃 home 事务 | `_outstandingReqs: map<PA, OutstandingRequest>`【modules/ubiomodule/UBCCController.hh:800-803】；硬界 128，创建时检查【modules/ubiomodule/UBCCController.hh:340-342】【modules/ubiomodule/UBCCController.cc:5296】 | H；map 动态分配。若每项含 64 B data 与控制字段，约 0.2–0.4 KiB/项，即 25–50 KiB。PA 同时充当身份与查找索引，无法天然拒绝旧 generation 完成。 | `TxnSlot[128]`；PA→slot 用固定 2-way hash/CAM；handle=`{slot, generation}`。 |
| immediate grant data | `_immediateGrantData: map<PA, OutstandingRequest>`【modules/ubiomodule/UBCCController.hh:507-509】 | U/生命周期间接界；它复制 `OutstandingRequest`，可能与正式 outstanding 同时保存同一 data/tuple。 | 不设独立 map；事务槽内一个 `grant_data_state`。128。 |
| pending requester | `map<PA, deque<PendingRequester>>`【modules/ubiomodule/UBCCController.hh:815-819】；每 PA 32、全局 256【modules/ubiomodule/UBCCController.hh:330-342】 | H，但两级动态分配；同一 tuple 还可能在 resident/H64 waiter 中出现。约 256×(约 96–160 B)=24–40 KiB，加 map/deque 开销。 | 全局 `Waiter[256]` + 每槽链头；每事务最多 32。FULL=`Busy/RetryAfterCredit`，不得静默 drop。 |
| resident waiter | 同型 map/deque；全局 256【modules/ubiomodule/UBCCController.hh:818-820】 | H；回放可同步进入其他路径，代码专设 `_capacityReplayActive` 抑制嵌套【modules/ubiomodule/UBCCController.hh:823-826】，说明生命周期/重入复杂。 | 与 pending requester 共用或分区 waiter pool：建议总 256，其中 resident 保留 128，按原因字段区分。 |
| H64 persistence waiter | set 32 个 in-flight PA；每 PA 8、总 64 waiter【modules/ubiomodule/UBCCController.hh:878-886】 | H；又一份 PA→deque 与 requester tuple；完成若只按 PA 匹配会有 ABA 风险。 | 32 persistence slot + 64 waiter；完成携带 generation。 |
| H64 lookup retry | 固定 256 项数组，每 wake 最多 8【modules/ubiomodule/UBCCController.hh:828-840】 | H；这是正确方向，但只有 PA，没有 owning txn generation；槽复用后旧 retry 可能语义模糊。 | 改为 `{txn_handle, reason, next_tick}`，容量 128 足够（不应超过事务槽）。 |
| tombstone | `map<PA, deque<Tombstone>>`，窗口 W=10,000,000 ticks【modules/ubiomodule/UBCCController.hh:804-813】【modules/ubiomodule/UBCCController.hh:904-905】 | **U/时间间接界**；每 PA 多 entry，只有过期清理，没有全局最大值。高请求率×长 W 可显著增长。 | 固定 tombstone ring 1024；每项完整 tuple+expiry+generation。满时优先清过期，否则对新事务显式 backpressure，不能覆盖尚在 W 内项。 |
| completed read identity | set + order deque，硬界 65,536【modules/ubiomodule/UBCCController.hh:810-813】；超限逐出【modules/ubiomodule/UBCCController.cc:4415】 | H，但身份被双存一份；估计 tuple 32 B，set node 72–96 B + deque 32 B，合计约 6–9 MiB。对 FPGA 完全不合适。 | 固定 4096-entry set-associative replay filter，或按协议最大重传窗口推导；若必须 65,536，使用定长 SRAM hash（约 2–3 MiB raw），不是树+deque。 |
| eviction/async WB | `_evictionPendingRemoval: map<PA,epoch>` 无自身硬界【modules/ubiomodule/UBCCController.hh:821-823】；`_asyncWbSnapshots` 硬界 128【modules/ubiomodule/UBCCController.hh:926-931】 | 前者 U/间接界，后者 H；两个 map 都表示持久化 generation，可能重复或乱序清理。 | 合并为 128 persistence slot，字段含 op/snapshot epoch/generation。 |
| replay active / commit count | `_pendingReplayActive: set<PA>`【modules/ubiomodule/UBCCController.hh:818-820】；`_commitCount: map<PA,int>`【modules/ubiomodule/UBCCController.hh:1100】 | 前者间接受 waiter 数限制；后者看似测试观测但常驻且 U。 | replay bit 放 txn slot；测试计数改固定采样/全局 counter，生产态不保留逐 PA map。 |
| ResidentDir/Bloom scratch | `_dirBits/_bloomBits` 为配置决定的固定向量【modules/ubiomodule/ResidentDir.hh:294-295】；实现检查总 on-chip ≤512 KiB【modules/ubiomodule/ResidentDir.cc:260-285】 | H；属于功能容量，不是泄漏。`_h64BloomScratch` 长度由 Bloom 配置间接限定【modules/ubiomodule/UBCCController.hh:891-900】。 | 保持，但在 elaboration 时固定长度；明确 512 KiB 是综合预算而非 host-only assert。 |
| registry | 静态 `map<(node,socket),ptr>`【modules/ubiomodule/UBCCController.hh:944-946】 | I：最多 `numNodes*numSockets`，只适合软件绑定。 | 仿真保留；RTL 用 elaboration 端口数组，不进入协议 SRAM。 |

### 3.2 UBIO（含 host、故障注入、HA adapter）

| 状态 | 当前界 | 风险/内存 | 建议 |
|---|---|---|---|
| DSM 数据面 | 128 MiB byte vector + 2 MiB valid vector【modules/ubiomodule/ubio_main.cc:673-682】 | H，约 130 MiB；这是模拟 DRAM，不应综合为片上 SRAM。 | RTL 外接 DRAM；host 模拟可保留。 |
| DSM async | `PendingDataOp[256]`【modules/ubiomodule/ubio_main.cc:683-706】 | H；每项含 64 B + 两个 `std::function`，host 约 30–60 KiB。缺 generation；回调捕获对象销毁是风险。 | `DsmSlot[256]`，completion `{slot,generation,status}`，回调在边界适配层。 |
| legacy MetaRNF page read/write | `_pendingReads`、`_pendingWrites` 两个 reqId map【modules/ubiomodule/ubio_main.cc:773-800】【modules/ubiomodule/ubio_main.cc:836-865】 | **U**；read 在发送前插入，write 发送成功后插入；无总 credit、timeout 或取消。每项含 `std::function`。 | page slot 64（read 32/write 32 或共享 64）；FULL 立即 callback `RetryableBusy`。 |
| H64 line read/write | map 各 32，deferred 固定 64；combined read≤32、write≤32【modules/ubiomodule/ubio_main.cc:868-895】【modules/ubiomodule/ubio_main.cc:980-1006】【modules/ubiomodule/ubio_main.cc:1072-1107】 | H；但同一操作先在 deferred array、再搬到 map，仍有身份迁移窗口。FULL 已返回 `RetryableBusy`，语义较好。 | 统一 `MetaLineSlot[64]`，状态 `Deferred/ReadyToSend/Waiting/Completing`；不搬运 callback。 |
| backstore fill/ack | `_pendingFills`、`_pendingBackstoreAcks` 为 vector，无界 push【modules/ubiomodule/ubio_main.cc:1186-1188】【modules/ubiomodule/ubio_main.cc:1691-1704】【modules/ubiomodule/ubio_main.cc:1782-1805】 | **U**；完成只携带 PA/snapshot epoch，旧 completion 与 PA 再用存在混淆。vector erase 还会移动元素。 | fill ring 128、ack ring 128；条目携带 UBCC txn/persistence handle。满时不发起底层 I/O。 |
| legacy backstore page/cache/chain | `_pages`、`_pagesDirty`、`_deferredReadsByPage`【modules/ubiomodule/ubio_main.cc:1177-1188】及 `_chainCtx/_chainPages/_chainGroup`【modules/ubiomodule/ubio_main.cc:1635-1639】 | U，且一个 chain 被三张 map 表示；page 数按写入量增长，deferred vector 每 page 也无界。 | 生产 H64 路径删除 legacy runtime；过渡期 chain slot 32、每 chain page list 32、每 page waiter 8；FULL=Busy。 |
| H64 host | 128 txn slots、1 scan、每 active bucket 8 waiter、最多 128 bucket states、8 active RMW【modules/ubiomodule/BackstoreHostH64.hh:108-121】【modules/ubiomodule/BackstoreHostH64.hh:159-165】【modules/ubiomodule/BackstoreHostH64.hh:235-264】 | H；当前最佳参考实现。配置声明 max pending 128/max waiter 8【modules/ubiomodule/BackstoreHostH64.hh:74-91】，但类内常量才是真正执行界。 | 保持 128/8/8；补 generation 到 MetaRNF completion；配置与实现常量统一并静态校验。 |
| authoritative grant read | 固定 32【modules/ubiomodule/ubio_main.cc:1203-1215】 | H；保存完整 `CoherenceMessage`，约 32 KiB 量级。 | 保持 32；使用 txn handle，FULL 返回 requester retry。 |
| fault delay/reorder | `g_delayedQueue: deque`，按 fireTick 插入，无界【modules/ubiomodule/ubio_main.cc:310-321】【modules/ubiomodule/ubio_main.cc:441-450】 | **U**；规则 `matchCount=0` 表示无限【modules/ubiomodule/ubio_main.cc:172】。故障模式可无限吞消息，掩盖真实 transport backpressure。 | 仅验证构建启用；固定 4096。FULL 时记录 `FaultInjectorOverflow` 并终止测试，不能悄悄 drop。 |
| reliable network responses | deque，硬界 8192，满则 panic【modules/ubiomodule/ubio_main.cc:3424-3447】 | H，按约 1 KiB 消息估算约 8–10 MiB；panic 是明确但过晚的 full 行为。 | 输出 ring 256，credit 来自 Port；FULL 对上游停止接收/不完成事务，关闭阶段保留 32 个控制 credit。 |
| barrier arrivals | `map<BarrierKey, fixed arrivals>`；每 key 32 planes×4 generations【modules/ubiomodule/ubio_main.cc:3476-3485】 | **key 数 U**；每 map value 约 544 B，恶意 mask/seq 组合可增长。 | 固定 8 个 barrier-generation slot；key 冲突 FULL，旧 generation 必须显式 retire。 |
| PeerExit sets/maps | peer 集合由拓扑间接限定【modules/ubiomodule/PeerExitCoordinator.hh:87-90】；send-attempt map key 含 exitId【modules/ubiomodule/ubio_main.cc:3349-3361】 | I；正常生命周期约 planes 数，但 nonce 变化/异常消息可能留下历史 key。 | 固定 peer table `MAX_PLANES=32/64`；每 peer 一个当前 exit generation。 |
| HomeVI adapter | `requests/wireRequests/expectedResponses/writebacks` 四 map【modules/ubiomodule/ubio_main.cc:1937-1981】；只对 `requests.size() >= maxActive` 检查【modules/ubiomodule/ubio_main.cc:2073】 | requests H（配置），其余 I/U；一个事务被三表复制，writeback 不计入同一总容量。 | `HAWireTxn[maxActive]` 单槽持有 wire/expected/writeback 状态；建议 128；固定地址索引。 |

### 3.3 networksim

生产 `networksim_main` 的 `_fifo` 硬界默认 65,536，环境可调 1..1,048,576【modules/networksim/networksim_main.cc:65-83】；当满时停止从 Port 接收，让 Port HWM 反压源【modules/networksim/networksim_main.cc:226-239】。队列只保存 `Message*`，但每条消息是动态分配的完整 framework message；按约 1.1 KiB/项，默认约 70–80 MiB，最大配置可超过 1 GiB。`_links/_ports/_linkLatency/_donePorts/exit maps` 受 `numNodes*numSockets` 及 full-mesh 路由数 `P(P-1)` 间接限定【modules/networksim/networksim_main.cc:56-71】【modules/networksim/networksim_main.cc:207-223】。

旧单进程 `NetworkSim` 自身没有 transit FIFO，但通过目标 `PseudoMemPort::enqueue` 转发【modules/networksim/NetworkSim.cc:41-59】，实际容量落入 framework 的无界 `_rx_queue`。

建议：生产转发 ring 默认 **4096**，按 ingress/egress 或 virtual channel 分区；每端口 credit 128，总和不得超过 ring；控制消息保留 64 credit。拓扑表在 elaboration 后固定。FULL 时停止 ingress，绝不 `ReleaseMessage` 丢包。若实验必须 65,536，应明确它是 host stress profile，而非 FPGA 目标。

### 3.4 framework 与隐藏 transport 队列

1. `framework::Port` 每端口仅有一个 future `pendingMessage`【framework/Port.cc:128-144】【framework/Port.cc:339-395】，但底层两个 ZMQ socket 的 `sndhwm/rcvhwm` 默认各 8192、环境最大 1,048,576【framework/Port.cc:241-259】。这是当前最重要的**隐藏队列**。满时 payload send 使用阻塞 `send_flags::none`【framework/Port.cc:314-327】，`sndtimeo=-1`【framework/Port.cc:247-248】，所以 full 行为是无限阻塞，而不是可组合的协议 backpressure。
2. `ZMQChannel` 没有设置 HWM，发送 `dontwait` 并以 false 暴露 EAGAIN【framework/ZMQChannel.cc:18-23】【framework/ZMQChannel.cc:49-61】；容量依赖 libzmq 默认值，属 I，且调用者若不可靠重试就丢消息。
3. `ZMQTransport` 同样没设置 HWM，发送/接收均阻塞【framework/ZMQTransport.cc:10-20】【framework/ZMQTransport.cc:27-40】【framework/ZMQTransport.cc:43-57】；隐藏容量与阻塞时长不可审计。
4. `PseudoMemPort::_rx_queue` 是无界 deque【framework/PseudoMemPort.hh:24-28】【framework/PseudoMemPort.hh:59-65】；文档甚至声明“backpressure is hidden internally”，意味着生产者无法知道容量。每项约 528 B，100k 项约 50 MiB。
5. `PseudoManager` 的 port/connection map/vector 受拓扑间接界定【framework/PseudoManager.hh:64-67】，不是主要运行态风险。

建议统一：每 Port 明确 **TX ring 256、RX ring 256、future slot 1、control reserve 16**；ZMQ HWM 只作为外层安全网并设置为与 ring 相等，而非再形成 8192 深隐藏缓冲。所有发送 API 返回 `Accepted/Full/Closed/Invalid`；协议层仅在 `Accepted` 后改变“已发送”状态。禁止无限阻塞发送。

### 3.5 UBAdapter

UBAdapter 同时保存：read inflight map（仅此项硬界 64）、clear/HA 两类 inflight set、clear retry map、`_pendingByReqId`、`_readyResponses`、`_deferredControls`、`_reliableOutputs`【gem5/gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.hh:291-323】。除 read 外均无硬界。PendingTxn 还包含 `std::function`【gem5/gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.hh:275-288】。因此一个请求可同时出现在 inflight set、pending map、ready map、可靠输出 deque 中。

`MaxInflightReadReqs=64` 仅在 read 发送处检查【gem5/gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc:592】；它不能约束 clear、permission probe、异步 control 或输出积压。建议用 **128 个 AdapterTxn slot**（read 保留 64、clear 32、HA/control 32，可借用），**RX completion ring 128**，**TX reliable ring 256**，**control ring 64**。reqId 编码 `{adapter-id,slot,generation}`；ready response 直接写回所属槽，不再建第二张 map。FULL：同步接口返回 busy；异步入口保持 source credit，不接收后再排无界 deque。

### 3.6 EPBackend

主要动态状态集中在 `_pendingGrantData`、requester line maps、active recall、pending grant/read/upgrade、deferred invalidation、HA probe pending/completed、HA remote misses【gem5/gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh:878-915】【gem5/gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh:936-940】【gem5/gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh:967-1064】。

- `_haCompletedProbeResponses` 只有事后 `size()>1024` 的清理迹象【gem5/gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc:481】，不是 admission hard bound，且“completed cache”与 pending map 双存身份。
- requester line maps 是缓存权限元数据，容量受实际 cache/DSM footprint 间接限制，但无 eviction/硬界；不应和瞬态事务表混在同一种动态容器策略中。
- pending read 与 pending grant 分离是为修复同 PA 二次 reqId【gem5/gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh:986-1007】，但也导致一次事务在阶段转换时跨 map 搬迁。

建议：**256 个 node-level protocol slots**，每 PA 至多一个主事务；状态覆盖 Read→Grant→Clear、Upgrade、Recall/Invalidate、HA probe/miss。grant data 内嵌槽（约 64 B×256=16 KiB raw）。HA completed replay cache 固定 256。requester-line 元数据单独做固定 cache：建议 4096 entries、4-way；满时走协议 evict/writeback，不能无限增长。active recall/deferred invalidation 成为事务槽 bit/payload。

### 3.7 EP-RNF

EP-RNF 有 7 类动态协议状态：`_pendingChiTxns`、response/data 输出 deque、retry map、upgrade map+set、deferred CHI deque、pending HN response map、outer-pending map【gem5/gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.hh:430-443】【gem5/gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.hh:481-541】【gem5/gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.hh:556-580】。源码没有总容量检查；不同 PA 可无限增长。输出 backpressure 时直接 push deque【gem5/gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:1115-1149】。

建议 **128 个 RNF line txn slot**，每槽包含 pending CHI、queued snoop（最多 1）、retry/upgrade/HN/outer 标志；不再用 5 张 PA map。response ring 64、data ring 64、request ring 64，使用 MessageBuffer credit。FULL：新 CPU/outer 请求返回 retry；已接收 snoop必须使用预留 16 槽，避免一致性死锁。retry set 改固定 ready bitmap/时间轮。

### 3.8 EP-SNF

`_pendingWrites` map、grant retry deque、deferred CompData vector、response/data deque、deferred grant vector、writeback deque 均无硬容量【gem5/gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.hh:45-93】【gem5/gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.hh:100-147】。writeback 虽有最大 retry 次数 128 与 backoff cap【gem5/gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc:1010-1119】，这限制重试次数，不限制同时 pending 数。

建议 **128 个 SNF transaction/write slot**，response ring 64、data ring 64、writeback ring 64、grant retry ring 64；数据 payload 内嵌 slot。对 HN-F 已接受的请求必须预留 completion credit，禁止“先接请求、后发现输出队列满”。超过 retry 上限应生成明确 terminal error/abort completion，而不是遗留 pending。

### 3.9 MetaRNF

MetaRNF 已有 8 个固定 flight slot【gem5/gem5/src/mem/ruby/protocol/chi/ep/MetaRNFController.hh:59-62】【gem5/gem5/src/mem/ruby/protocol/chi/ep/MetaRNFController.hh:188-194】、line pending 128、每地址 8、legacy write 64【gem5/gem5/src/mem/ruby/protocol/chi/ep/MetaRNFController.hh:116-140】。但 request/response/data 输出 deque、`_waitQueues`、deferred completion deques 没有独立硬界【gem5/gem5/src/mem/ruby/protocol/chi/ep/MetaRNFController.hh:153-167】【gem5/gem5/src/mem/ruby/protocol/chi/ep/MetaRNFController.hh:192-201】。`_scoreboard` 理论最多 8，仍用 map；`_perAddressPendingCount` 与 `_waitQueues`、全局 pending deque重复表达等待关系。

建议沿用 8 flight，但每槽加 generation；统一 pending pool **128**，每 PA 最多 8；legacy write 合并进同一 pool或保留 64 分区。输出 ring req/rsp/data 各 32；deferred completion ring 16（不会超过 active+刚完成）。scoreboard 用 8-entry CAM；wait queue以 pending pool next-index 表达。FULL 已有 reject 计数和检查【gem5/gem5/src/mem/ruby/protocol/chi/ep/MetaRNFController.cc:301-330】，应把相同语义扩展到所有输出与 legacy 路径。

### 3.10 HA/UBIO VI 路径补充

HAController 每地址 waiter 深度由配置限定，但 `work_` 的活跃地址数、`writebacks_` 和 `actions_` 总数无界【modules/hamodule/HAController.hh:115-143】；只检查单地址 queueDepth【modules/hamodule/HAController.cc:67-96】。因此地址洪泛可绕过 per-address bound。建议 work slot 128、waiter pool 256、writeback slot 64、action ring 256。FULL 必须产生 Reject action；action ring 应预留 reject/completion credit，否则“想报告 full，但报告本身也 full”。

### 3.11 其余持久容器与低风险/静态界项目

为避免只列高风险项而漏掉 `map/set/deque/vector`，下表补齐审计范围内其余持久容器。函数局部临时 vector 不跨事件存活，另列在表后。

| 组件 | 容器 | 现有边界与判断 | 处理建议 |
|---|---|---|---|
| UBIO CoherenceMessageQueue | `_fifo: deque<Entry>`【modules/ubiomodule/CoherenceMessageQueue.hh:27-36】【modules/ubiomodule/CoherenceMessageQueue.hh:70-73】；`enqueue` 无检查直接 push【modules/ubiomodule/CoherenceMessageQueue.hh:78-85】 | U；若该 legacy/测试路径接入生产流量，会形成另一个隐藏消息队列。 | 删除旁路或固定 128；FULL 返回 false/credit stall。 |
| UBIO fault rules | `g_faultRules: vector`【modules/ubiomodule/ubio_main.cc:310-321】 | 启动配置字符串决定，运行后不增长；I（输入长度），不属协议事务泄漏。 | 配置解析限制 256 rules，超限启动失败。 |
| UBIO barrier/exit | `peerExitMarked: set`、send-attempt map、`barrierArrivals: map`【modules/ubiomodule/ubio_main.cc:3317】【modules/ubiomodule/ubio_main.cc:3349-3361】【modules/ubiomodule/ubio_main.cc:3476-3485】 | peer set 拓扑界；attempt/barrier key 无硬界。 | 固定 plane table与8 generation slots。 |
| UBCC peer exit | `PeerExitCoordinator` 三个 set + retry map【modules/ubiomodule/PeerExitCoordinator.hh:87-90】 | I：正常最多 `numNodes*numSockets-1`。 | 固定 64-plane bitmap/array。 |
| UBCC schema storage | Schema H64 `_buckets: vector`【modules/ubiomodule/BackstoreSchemaH64.hh:466】；ResidentDir vectors【modules/ubiomodule/ResidentDir.hh:294-295】 | H/I：构造配置决定，运行时不增长；H64软件schema容量为 groups×buckets。 | 数据面/模型存储；RTL映射DRAM/SRAM，不作为txn槽。 |
| networksim topology | `_links: vector`、`_ports/_linkLatency: map`、done/exit set/maps【modules/networksim/networksim_main.cc:56-71】 | I：配置要求完整 full mesh，路由精确为 `P(P-1)`【modules/networksim/networksim_main.cc:207-223】；exit map最多P个正常peer。 | elaboration后转定长数组/矩阵；非法新key拒绝。 |
| legacy networksim | `_port_ids: vector`【modules/networksim/NetworkSim.hh:61-67】、ForwardTable map→vector【modules/networksim/ForwardTable.hh:52-56】 | I：addPort/config调用次数；无重复/最大检查。数据队列在PseudoMemPort。 | 非生产则标记test-only；否则固定MAX_PORTS=64。 |
| framework manager | `_ports: map`、`_connections: map<int,vector<int>>`【framework/PseudoManager.hh:64-67】 | I：拓扑构建调用决定；运行数据不应改变。 | 初始化冻结；最大64 ports、每port63 peers。 |
| UBAdapter registry | `_clockAdapters: vector`【gem5/gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.hh:247-251】 | I：SimObject数量；注册/注销生命周期，非协议事务。 | 仿真保留并断言≤planes；RTL为静态端口。 |
| EPBackend topology/registry | `_ubAdapters/_epSnfs` 与二维cache vector【gem5/gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh:865-915】；静态 backend map【gem5/gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh:1063-1064】 | I：socket/node/cache配置；初始化后固定。 | 冻结为参数化数组；不占协议txn预算。 |
| EP-RNF topology | `sequencers`、`_hnfVersions`、`_downstreamBySocket` vectors【gem5/gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.hh:553-554】 | I：cache/socket数；初始化后固定。 | 参数化数组；构造时验证长度。 |
| MetaRNF registry | 静态 instance map【gem5/gem5/src/mem/ruby/protocol/chi/ep/MetaRNFController.hh:203】 | I：node×socket实例数。 | 仿真绑定保留；RTL静态。 |
| HA directory | `FlatBitmapDirectory::bytes_` vector【modules/hamodule/FlatBitmapDirectory.hh:54】与 `unavailable_` vector【modules/hamodule/HAController.hh:138-143】 | H/I：directory line count构造时确定，不在运行中增长。 | 功能SRAM；容量manifest中单列。 |

函数局部的 candidate-page、scan shadow、reconstructed/reject、retry-lines 等 vector，其峰值由当前目录/组/事务快照间接决定，生命周期止于一次调用；它们仍不适合综合，但不构成跨事件状态泄漏。迁移到RTL时应改为定长 scratch 或逐项流水，而不是堆分配。静态消息名 `map<string,type>`【modules/ubiomodule/ubio_main.cc:192】只读且元素数由源码固定，不计入运行态容量。

## 4. 重复与生命周期风险矩阵

| 风险 | 当前证据 | 后果 |
|---|---|---|
| 同事务多表复制 | UBAdapter pending/ready/inflight；EPBackend read/grant/data；UBCC outstanding/waiter/tombstone | 任一路径漏删即内存/credit 泄漏；不同表的字段可漂移。 |
| PA 作为唯一异步身份 | 多数 completion API 只传 PA/reqId/epoch；UBCC backstore ack 按 PA+snapshot epoch【modules/ubiomodule/UBCCController.hh:745-752】 | PA 释放后重用，旧完成可能命中新事务（ABA）。epoch 是协议数据版本，不等价于本地槽 generation。 |
| 回调重入与容器搬迁 | UBIO 专设 `_reentrantDepth` 和 deferred array【modules/ubiomodule/ubio_main.cc:880-901】；UBCC 抑制 nested replay【modules/ubiomodule/UBCCController.hh:823-826】 | iterator/引用失效、同一事务重复 admission、回调递归耗尽栈。 |
| 输出队列无 credit | UBAdapter、EP-RNF、EP-SNF、MetaRNF 均以 deque 保存“可靠输出” | 下游永久阻塞时 host 内存无限增长；FPGA 无法实现；退出/控制消息被数据堵死。 |
| timeout 当作容量回收 | UBCC recall timeout=10M ticks【modules/ubiomodule/UBCCController.hh:907-911】 | timeout 能处理丢包孤儿，但不能证明正常慢完成不会在槽复用后晚到。 |
| 动态容器地址不稳定 | vector erase/move、map node、`std::function` 捕获 | callback 保留悬空 `this`/引用；综合工具通常不支持。 |

## 5. FPGA 综合关注

1. `std::map/set/unordered_map/deque`、动态 `vector`、`shared_ptr`、`std::function`、`new/delete` 不能直接映射为确定端口数、确定时延的 SRAM/寄存器结构。
2. 多张 PA map 意味着多次关联查找；若朴素 CAM 化，面积按“表数×容量×PA 位宽”增长。统一槽后只需一个 admission 索引，槽内字段按 index 访问。
3. 65,536-entry replay identity 若保留，应设计为 SRAM hash/set-associative table，而不是红黑树；8192/65536 条完整 1 KiB 消息队列对应 8–64 MiB，远超常见片上 RAM，应降深并依靠端到端 credit。
4. 大数据 payload 应存在 data RAM，控制槽只存 data-buffer index；否则 256 槽×多个 64/256 B 副本迅速消耗 BRAM。
5. 所有容量必须是 elaboration parameter，有编译期关系：`waiters <= slots * max_waiters_per_slot`、`accepted_requests <= completion_credits + free_slots`、控制 credit 不可被数据借尽。

## 6. 建议默认容量总表

| 组件 | txn slots | waiter/pending | 输出 ring | replay/tombstone |
|---|---:|---:|---:|---:|
| UBCC | 128 | requester 256；persistence 64 | control/data 各 64 | tombstone 1024；read replay 4096 |
| UBIO Meta/page/H64 adapter | line 64；page 64；grant-read 32；DSM 256 | chain 32×32 pages | net response 256 | barrier generation 8 |
| H64 host | 128 | 8/bucket，最多 128 buckets | 由 MetaRNF credit | — |
| networksim | 4096 transit | 每 ingress 128 credit | 每 egress 128 | control reserve 64 |
| framework Port | — | RX 256 + future 1 | TX 256 | control reserve 16 |
| UBAdapter | 128 | ready completion 128 | TX 256；control 64 | — |
| EPBackend | 256 | requester-line cache 4096 | 由 controller rings | HA replay 256 |
| EP-RNF | 128 | queued snoop 1/slot | req/rsp/data 各 64 | snoop reserve 16 |
| EP-SNF | 128 | retry/writeback 各 64 | rsp/data 各 64 | — |
| MetaRNF | flight 8 | pending 128，8/address | req/rsp/data 各 32 | — |
| HAController | 128 | waiter 256；WB 64 | action 256 | — |

这些是保守起点，不是未经测量的最终性能结论。迁移后应由高水位、FULL 次数、等待周期直方图驱动调整；容量调整不能改变正确性，只改变 admission/吞吐。

## 7. 审计判定

- **可作为模板**：BackstoreHostH64、UBIO DSM fixed slots、MetaRNF flight table、networksim“FIFO 满则停止 ingress”。
- **必须优先整改**：framework 隐藏 ZMQ 队列/无限阻塞；UBAdapter/EP-RNF/EP-SNF/MetaRNF 输出 deque；EPBackend 与 UBCC 的事务多表复制；UBIO legacy page/chain/fill/ack 无界容器。
- **不能仅靠加 `size()` 检查解决**：如果同一事务仍跨多个 map，容量检查会产生新的部分分配回滚路径；应先确立唯一槽所有权，再把所有辅助状态改为 slot index/handle。
