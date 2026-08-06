# 甲方 HA 与跨节点一致性调研交接初稿

> 日期：2026-08-07
> 用途：交给 ChatGPT Work 或其他具备网络检索能力的研究工具继续补充外部文献
> 当前内容来源：仅 CC-EP 仓库内实现、测试和交付文档；外部事实尚未检索核验

## 1. 调研目标

本调研服务于合同目标 3：

```text
OurCC 跨节点 CC 同步平均时延 < 甲方 HA 实现的理论平均时延
```

调研不应预设 OurCC 一定更快。目标是建立公平、可审计的比较模型，识别甲方 HA
参数对结论的敏感性，并确认哪些完成语义和理论下界在业界实现中合理。

需要外部资料回答的核心问题：

1. 两节点、节点级 VI/presence 目录的典型 HA 架构如何处理 dirty/latest owner。
2. peer-direct data 是否通常携带 coherence authority，还是仍需 Home grant/commit。
3. invalidate completion、metadata commit 和 requester completion 的常见定义。
4. write-through、write-back、hybrid 对 Remote Read、Writer Acquire 和 Owner Handoff 的理论路径影响。
5. 非 FIFO fabric 上 same-line serialization、pending install 和 eager commit 的安全要求。
6. ARM/RISC-V acquire/release、DMB/DSB/fence 与跨节点 coherence completion 的关系。
7. 16-node switch/fabric 下目录式一致性的拓扑、路由和性能评估方法。
8. 丢包、重复、延迟、乱序等 transport fault 是否属于 HA 理论基线，如何公平分域。

## 2. 当前仓库已经确定的事实

### 2.1 OurCC 当前实现

- 全局目录在 UBCC/native UBIO 中，不占 HN-F TBE。
- requester 从 Home 获得 grant 后，本地安装，再发送 ClearReq。
- 当前 `clear-ack` profile 等待 ClearResp accepted。
- Home 的 Clear commit 包含 epoch/reqId/requester 精确匹配、目录状态提交、waiter
  retirement、tombstone、outstanding 删除和 pending replay。
- Recall/Invalidate/Upgrade 等路径使用显式 completion/ack。
- C4 Direct-Forward 当前是 data-only 类能力，不等同于完整 authority forwarding。
- C4 要求 requester、owner、home 为三个不同节点，在目标 3 的 2 节点范围不可作为主路径。
- `lossless-oneway` 只是 proposed profile，当前未实现。

### 2.2 已知甲方 HA 条件

- 2 节点。
- 全局缓存地址空间不超过 128 MiB。
- 节点级 VI 协议。
- 每 cacheline 2-bit metadata：本地状态/presence 的具体编码需确认。
- metadata 位于 HA 或 IODie，当前比较暂不区分二者。
- metadata lookup 可近似为零附加时延。
- requester 到 HN 的公共前缀与 OurCC 一致。
- 网络按 lossless 处理。
- 网络不保证 FIFO。
- 不同地址允许按弱内存序乱序完成。

### 2.3 当前未知且高度敏感的参数

| ID | 问题 | 对结论的影响 |
|---|---|---|
| HU-01 | write-through、write-back 或 hybrid | 决定 memory 是否保持 latest |
| HU-02 | peer response 是 central-return 还是 direct | 决定数据关键路径 |
| HU-03 | peer direct 是否携带 grant authority | 可能改变串行跨节点 leg 数 |
| HU-04 | invalidate completion | 决定 writer 何时安全完成 |
| HU-05 | metadata commit 点 | 决定 `T_commit/T_next` |
| HU-06 | requester 是否等待 global commit | 决定 `T_visible` |
| HU-07 | dirty/latest owner 定位 | 可能引入 HN query 或 remote probe |
| HU-08 | same-line serialization | 决定 eager commit 是否安全 |
| HU-09 | HA/IODie placement | 逻辑 leg 不等于物理 traversal |
| HU-10 | HA local service/queue | 同 K 时决定严格快慢 |
| HU-11 | store/fence/barrier 完成语义 | 决定 root operation 终点 |
| HU-12 | contention/retry | 决定平均值和尾延迟 |

## 3. 推荐文献调研主题

### 3.1 目录式 cache coherence 和 Home Agent

检索关键词：

```text
directory based cache coherence home agent ownership transfer
distributed directory coherence dirty owner tracking
home agent peer to peer data response grant authority
directory coherence pending owner install transient state
```

优先资料类型：

- ARM CHI architecture/specification 的公开版本或技术介绍。
- CXL.cache、CCIX、UPI、Infinity Fabric、TileLink 等公开协议资料。
- ISCA、MICRO、HPCA、ASPLOS 目录式一致性论文。
- 厂商公开专利只作补充，不能替代规范或论文。

需要提取：

- 谁是 serialization authority。
- data response 和 permission response 是否可以分离。
- direct forwarding 是否携带 authority。
- owner installation 前后的 transient/pending state。
- 下一同址冲突事务何时允许进入。

### 3.2 两节点 VI/presence metadata 的能力边界

检索关键词：

```text
two node valid invalid directory coherence presence bit
two bit directory cache coherence remote presence
limited pointer directory two node coherence
coarse vector directory dirty owner
```

研究问题：

- 2-bit metadata 是否只表达 local/remote valid presence。
- write-back 时 dirty/latest owner 需要额外信息还是可由 HN 查询获得。
- 远端唯一 presence 是否可以安全推断 owner。
- shared 状态下 presence bit 如何区分 clean sharer 与 dirty owner。

### 3.3 Completion、Ack 和 eager commit

检索关键词：

```text
cache coherence invalidate acknowledgement completion semantics
coherence eager grant pending install token
directory coherence transient state requester install acknowledgement
non FIFO interconnect coherence ordering
```

需要区分：

- 显式 Ack。
- 隐式 fabric/snoop completion。
- requester install completion。
- metadata commit。
- next-conflict release。
- posted write 与 completed store/fence。

调研不得以“没有 Ack 消息名”推断“没有 completion 成本”。

### 3.4 ARM/RISC-V 弱内存序

检索关键词：

```text
ARMv8 acquire release cache coherence completion point
ARM DMB DSB completion coherence point of coherency
RISC-V memory model fence coherence ordering
litmus message passing cache coherent interconnect
```

需要输出：

- 单地址 coherence ordering 与不同地址 memory ordering 的区别。
- store-release/load-acquire 的 observable guarantee。
- DMB 与 DSB 的完成语义差异。
- posted store 何时不能作为合同 root completion。
- 推荐的 herd7/litmus 测试集及 allowed/forbidden outcome。

### 3.5 Lossless transport 与可靠性分域

检索关键词：

```text
lossless coherent interconnect retry replay protocol
cache coherence link level retry duplicate suppression
coherence protocol transport fault model
CXL link retry poison reliability coherence
```

研究目标：

- 区分链路层 lossless/replay 与协议层 timeout/retry。
- 确定 HA 理论模型是否应包含 retry 成本。
- 说明 OurCC 额外 drop/dup/reorder robustness 是能力差异，但不能强加到 lossless HA baseline。
- 识别 exactly-once commit、stable transaction ID 和 duplicate response 的业界处理方式。

### 3.6 16-node Switch 和多跳拓扑

检索关键词：

```text
16 node cache coherent switch topology directory coherence
cache coherent fabric switch multi hop latency
CXL fabric manager coherent switch scale out
multi socket directory coherence topology evaluation
```

需要提取：

- Switch 与 full-mesh 的区别。
- home placement 和 route selection。
- hop count、serialization、queueing 和 multicast/fanout 成本。
- 16-node correctness 与性能 qualification 的常用 workload。
- switch failure、link flapping、partition 是否应属于本期验收。

## 4. 推荐输出结构

ChatGPT Work 最终可按以下结构扩写：

1. 摘要和结论等级。
2. 调研范围与方法。
3. 术语和统一完成点。
4. 甲方 HA 参数表及每项置信等级。
5. OurCC 当前实现事实。
6. 业界目录式 HA 架构综述。
7. 两节点 VI/2-bit metadata 能力分析。
8. Remote Read DAG。
9. Shared-to-Writer DAG。
10. Ownership Handoff DAG。
11. `T_visible/T_commit/T_next` 对比。
12. `K_logical/K_crossnode/P` 上下界。
13. 弱内存序和 barrier 边界。
14. lossless transport 和 fault 分域。
15. 16-node Switch 可行性。
16. break-even 和敏感性分析。
17. 风险、unknown 和不能声称的内容。
18. 推荐合同文字和验收步骤。
19. 文献表。

## 5. 文献记录模板

每条资料建议记录：

| 字段 | 内容 |
|---|---|
| Citation | 作者、标题、会议/规范、年份 |
| URL/DOI | 稳定链接 |
| Source type | 规范、论文、专利、厂商白皮书、博客 |
| Authority | 高/中/低 |
| Architecture | 协议和拓扑 |
| Relevant claim | 与本项目直接相关的结论 |
| Exact quote/page | 可审计引用 |
| Assumptions | 节点数、write policy、FIFO、completion 等 |
| Mapping | 对 HU-01 至 HU-12 哪些项有帮助 |
| Conflict | 是否与其他资料冲突 |
| Project impact | 对 OurCC 目标 3 的影响 |

## 6. 需要避免的研究错误

- 不把消息总数当串行物理 hop 数。
- 不把 data direct 当 permission/authority direct。
- 不把无显式 Ack 当无 completion。
- 不把 cache coherence ordering 当完整 CPU memory ordering。
- 不把 8N2S/16 planes 当 16 nodes。
- 不把 proposed `lossless-oneway` 当当前实现。
- 不选择性忽略对 OurCC 不利的 HA 分支。
- 不引用无法核验的二手性能数字作为合同基线。
- 不把 fault robustness 的成本强加给 lossless HA 理论模型。

## 7. 当前可先行完成的仓库内分析

无需外部文献即可继续完成：

1. 从当前 C++ 提取 OurCC Remote Read、Writer Acquire、Owner Handoff 的精确 DAG。
2. 明确 ClearReq/ClearResp 对 `T_visible/T_commit/T_next` 的实际影响。
3. 对当前 topology 参数计算 `K_logical/K_crossnode`。
4. 将 HA unknown 作为符号参数，生成条件结论和 break-even 表。
5. 为 ARM litmus 编写测试规格，不先声称结果。
6. 为 16N Switch 编写配置和 qualification requirements，不先声称实现。

## 8. 交接完成条件

外部调研完成后，应至少得到：

- HU-01 至 HU-12 的 confirmed/unknown 状态及资料依据。
- 三类 root operation 的 HA/OurCC DAG。
- 每类操作的理论上下界和敏感参数。
- 对严格 `<`、条件 `<`、tie 和 risk 分支的明确判断。
- ARM/RISC-V memory-order 可执行验证建议。
- 16N Switch 的可行架构和验收工作量估计。
- 所有结论的引用和证据等级。

调研结果应合并回：

- `docs/delivery/ourcc_vs_customer_ha_target3_benchmark_and_delivery_20260804_zh.md`
- `docs/design/cc_ep_deliverable2_verification_reliability_ha.md`
- `docs/design/cc_ep_protocol_overview.md`
- `docs/delivery/acceptance_metrics_deliverables_todo_20260807_zh.md`
