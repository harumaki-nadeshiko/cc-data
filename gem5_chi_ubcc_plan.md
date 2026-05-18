# gem5 Ruby CHI Single Node 与 UBCC 计划

本文档只制定实施计划，不执行 Subtask 1 或 Subtask 2 的代码实现。

## 0. 当前 gem5 结构观察

已在 `gem5/` 中确认 Ruby CHI 相关入口：

- `configs/ruby/CHI.py` 是 Ruby CHI 系统创建入口，负责生成 RN-F、MN、HN-F、SN-F、RNI，并把 controller 放入拓扑。
- `configs/ruby/CHI_config.py` 定义默认 CHI node wrapper 和 controller 配置，包括 `CHI_RNF`、`CHI_HNF`、`CHI_SNF_MainMem`、`CHI_RNI_DMA`。
- `src/mem/ruby/protocol/chi/CHI-cache.sm`、`CHI-cache-actions.sm`、`CHI-cache-transitions.sm` 是 CHI cache/HN-F/RN-F 行为的主要 SLICC 实现。
- `src/mem/ruby/protocol/chi/CHI-mem.sm` 是 CHI SN-F/memory-side controller。
- `src/mem/ruby/protocol/chi/CHI-msg.sm` 定义 CHI request/response/data message type。
- `src/mem/ruby/protocol/chi/generic/CHIGenericController.*` 提供 C++ 级 CHI generic controller，可以直接收发 Ruby CHI `REQ/SNP/RSP/DAT` message，适合实现 EP/UBCC 这类非标准 Ruby controller。
- `src/mem/ruby/protocol/chi/tlm/*` 是 CHI-TLM 桥，可作为外部模型桥接参考，但 UBCC 的第一版不必依赖 SystemC/TLM。

## 1. 需要先澄清的问题与默认假设

以下问题会影响最终方案。若暂时不回答，计划按默认假设推进。

- Cluster 内 L2 是每 core 私有还是 cluster 共享？默认假设：L1I/L1D 每 core 私有，L2 每 cluster 共享。
- Subtask 2 的 N 个 Node 是要建成一个 gem5 进程内的逻辑多节点，还是多个 gem5 进程/多实例联动？默认假设：第一版在单一 gem5 进程、单一 `System` 中建模 N 个逻辑 Node。
- 是否要求一开始支持完整 ARM CHI feature，例如 DVM、atomic、exclusive monitor、IO coherent DMA？默认假设：第一版只支持普通 cacheable load/store 和 cache line 粒度一致性；DVM/atomic/DMA 后续补齐。
- DSM PA 在所有 Node 中是否使用相同 global physical address？默认假设：所有 Node 使用同一 canonical DSM PA，地址译码通过 PA range 判断 home node。
- UBCC directory 是否必须第一版就真实存入 Ruby/L3/DRAM？默认假设：第一版先用 UBCC 内部 C++ map/SRAM 建模目录；第二版再通过 UR 读写 UBCC-Exclusive PA 作为 backing store。

## 2. 总体可行性与难度

Subtask 1 可行性高，难度中等。gem5 已有 CHI Ruby 协议，默认已经包含 L1/L2 RN-F、L3 HN-F、DRAM SN-F 的基本结构。主要工作是自定义 node generator 和拓扑脚本，使 C=2、M=2 的 cluster 结构明确表达为每 cluster 一个 RN-F、共享 L2、全节点共享 HN-F/L3 和 SN-F。

Subtask 2 可行性中等，难度高。难点不是多几个 controller，而是 EP 如何在 Ruby CHI HN-F 决策期间介入全局权限。仅把 DSM Remote 的 HN-F 下游接到 EP-SNF，可以处理远端 miss 取数，但不能保证本节点获得 `ReadUnique`/写权限前已经完成全局失效。现有 HN-F 向 SN-F 取数时会使用 `ReadNoSnp`，EP-SNF 看不到最初来自 RN-F 的 `ReadShared` 或 `ReadUnique` 意图。因此完整 UBCC 需要以下二选一：

- 推荐路径：实现 DSM-aware HN-F，在 HN-F 处理 DSM line 的 `ReadShared`、`ReadUnique`、`MakeReadUnique`、`CleanUnique`、evict/writeback 关键路径插入 UBCC/EP 权限请求。
- 备选路径：让 EP-RNF 作为每条 DSM line 的 sentinel sharer/owner 出现在本地 HN-F directory 中，强制 HN-F 在本地升级时 snoop EP-RNF，由 EP-RNF 再触发全局权限变化。该方案更少改 HN-F，但状态更绕，容易出现漏登记、重入和死锁。

建议采用“先功能、后精确”的路线：先完成 Subtask 1，再实现只读/远端取数版 EP-SNF，再实现带 DSM-aware HN-F 权限钩子的完整 UBCC。

## 3. Subtask 1：Single Node Cache Coherence Architecture

### 3.1 CHI 抽象对应关系

| 目标组件 | gem5/Ruby CHI 对应 | 说明 |
| --- | --- | --- |
| Core L1I/L1D | `CHI_L1Controller` + `RubySequencer` | RN-F 内部的私有 L1；L1I/L1D 分别绑定 CPU instruction/data port。 |
| Cluster L2 | `CHI_L2Controller` | 推荐每 cluster 一个共享 L2。需要新增 `addSharedL2Cache()` 或新的 `ClusterCHI_RNF` generator。 |
| CPU Cluster | `CHI_RNF` 的一个扩展实例 | 一个 RN-F wrapper 包含 C 个 CPU 的 L1 和一个共享 L2；最后一级 RN controller 指向 HN-F。 |
| Node 共享 L3 | `CHI_HNFController` 的 `cache` | HN-F 兼任 home agent、目录和 L3 cache。 |
| Home Agent | `CHI_HNFController` | 处理 RN-F 请求、维护本 CHI domain directory、向 RN-F 发 snoop。 |
| DRAM Memory Controller | `CHI_SNF_MainMem` + gem5 `MemCtrl` | SN-F 是 CHI 内 memory-side 节点，背后连接 DRAM/SimpleMemory。 |
| CHI interconnect | Ruby network，4 个 vnet | `REQ/SNP/RSP/DAT` 对应 vnet 0/1/2/3。 |
| Misc/DVM node | `CHI_MN` | 可保留默认 MN；第一版不重点验证 DVM。 |

### 3.2 单节点目标拓扑

默认参数：`M=2` cluster，`C=2` cores/cluster，总 CPU 数 `4`。

逻辑连接：

```text
CPU0 L1I/L1D --\
CPU1 L1I/L1D ---- Cluster0 shared L2 --\
                                             HN-F + shared L3 -- SN-F -- DRAM
CPU2 L1I/L1D ---- Cluster1 shared L2 --/
CPU3 L1I/L1D --/
```

CHI 方向：

- 每个 cluster 是一个 RN-F node wrapper。
- 每个 core 的 L1 controller 下游为该 cluster 的共享 L2 controller。
- 每个 cluster L2 下游为所有 HN-F controller。
- 每个 HN-F 下游为所有 SN-F controller。
- HN-F/L3 的 `addr_ranges` 覆盖本节点 normal PA，按 cache line interleave 分配到 `num_l3caches` 个 HN-F。第一版可取 `num_l3caches=1` 简化，之后扩到多个 slice。

### 3.3 脚本和文件规划

第一阶段应尽量不改 SLICC 协议，只增加配置层：

- 新增 `configs/ruby/CHI_single_node_config.py`：继承 `CHI_config.py` 的 class，覆盖 `CHI_RNF.generate()`，按 cluster 分组 CPU。
- 在该 config 中新增 `ClusterCHI_RNF`：构造 C 个 CPU 的 L1I/L1D，然后创建一个共享 `CHI_L2Controller`，把所有 L1 的 `downstream_destinations` 指向共享 L2，并把共享 L2 作为该 RN-F 的 network-side last-level controller。
- 新增 `configs/example/ubcc/chi_single_node.py` 或复用现有 `configs/example/se.py` 加参数：设置 `--ruby --network=garnet|simple --ruby-protocol=CHI --chi-config=... --num-cpus=4 --num-l3caches=1 --num-dirs=1`。
- 若只需要计划期原型，也可以先用默认 `CHI_RNF.generate()` 得到每 core 私有 L2；但这不满足“Cluster 内共享 L2”的严格解释。

### 3.4 详细阶段划分

#### 阶段 1A：基线 CHI 可运行性确认

目的：确认 clone 的 gem5 能用 `PROTOCOL=CHI` 构建并运行最小 Ruby CHI 系统。

操作步骤：

1. 查看 gem5 当前分支、构建环境和可用 ISA。
2. 构建 `build/ARM/gem5.opt` 或目标 ISA 的 `PROTOCOL=CHI` 版本。
3. 运行 gem5 自带 CHI smoke test 或最小 SE 程序。
4. 记录默认 CHI 配置下 `configs/ruby/CHI.py` 生成的 RN-F/HN-F/SN-F 数量。

产物：构建和 baseline run 命令记录，不修改协议。

难度：低。

#### 阶段 1B：Clustered RN-F 设计

目的：实现每 cluster 多 core、共享 L2 的 RN-F generator。

操作步骤：

1. 在自定义 CHI config 中定义 `ClusterCHI_RNF(CHI_config.CHI_Node)` 或继承 `CHI_config.CHI_RNF`。
2. 构造 C 个 CPU 的 `RubySequencer`、L1I/L1D cache 和 `CHI_L1Controller`。
3. 创建单个 shared `CHI_L2Controller`，cache size 用新的参数如 `--cluster-l2-size`，或复用 `--l2_size`。
4. 将每个 core 的 L1I/L1D `downstream_destinations = [shared_l2]`。
5. `getNetworkSideControllers()` 返回 `[shared_l2]`，或返回所有 controller 但 only shared L2 作为 last-level；推荐所有参与 Ruby network 的 controller 都通过 `connectController()` 连接，但只有 shared L2 下游指向 HN-F。
6. `getSequencers()` 返回 C 个 CPU wrapper，保证 `connectCpuPorts()` 能正常绑定。
7. `generate(options, ruby_system, cpus)` 按 `[cluster*C:(cluster+1)*C]` 分组生成 M 个 RN-F。

风险点：默认 `CHI_RNF` 的 `addPrivL2Cache()` 是每 CPU 私有 L2，不能直接满足 cluster-shared L2。需要小心 `_ll_cntrls` 和 controller 列表，否则 HN-F 可能把 snoop 发到不该收的 L1/L2。

难度：中。

#### 阶段 1C：HN-F/L3 与 SN-F/DRAM 连接脚本

目的：用现有 `CHI.py` 连接 shared L3/HN-F 与 DRAM SN-F。

操作步骤：

1. 使用 `--chi-config=configs/ruby/CHI_single_node_config.py` 注入自定义 generator。
2. 设置 `options.num_l3caches=1` 起步，对应一个 `CHI_HNF` 和一个 L3 RubyCache。
3. 设置 `options.num_dirs=1` 起步，对应一个 `CHI_SNF_MainMem`。
4. 保持 `CHI_HNF.createAddrRanges(sysranges, cache_line_size, hnf_list)` 默认 interleave 逻辑。
5. `rnf.setDownstream(hnf_dests)` 后确认实际是 shared L2 指向 HN-F。
6. `hnf.setDownstream(mem_dests)` 后确认 HN-F 指向 SN-F。
7. 拓扑第一版用 `Crossbar` 或 `Pt2Pt`；后续性能实验再引入 `CustomMesh`。

难度：低到中。

#### 阶段 1D：验证计划

目的：证明单节点 CHI cache hierarchy 正确。

验证用例：

- 单 core load/store，确认能从 DRAM 取数并命中 L1/L2/L3。
- 两 core 同 cluster false sharing/true sharing，确认 shared L2 和 HN-F 行为正常。
- 跨 cluster ping-pong store/load，确认 HN-F 发送 snoop 并维护一致性。
- Ruby random tester 或 directed tester，覆盖 clean shared、unique dirty、evict/writeback。

观测指标：

- Ruby stats 中各 controller request、snoop、response、data message 数量。
- `RubyProtocol` debug log 中 `ReadShared`、`ReadUnique`、`SnpUnique`、`WriteBackFull` 路径。
- 最终程序输出正确，且无 deadlock。

难度：中。

## 4. Subtask 2：多节点 UBCC 总体设计

### 4.1 地址空间规划

每个 Node 的 PA 分区建议显式配置：

| Range | 示例 | Serve path | 说明 |
| --- | --- | --- | --- |
| Local Normal PA | `0x0000_0000 - 0x3fff_ffff` | HN-F -> local SN-F -> local DRAM | 本节点私有普通内存，不进 UBCC。 |
| DSM PA Local | node i 的 DSM slice | HN-F -> local SN-F/DRAM，并由 node i UBCC 作为 global home | 其他节点访问时要经过 node i UBCC。 |
| DSM PA Remote | 其他 node 的 DSM slice | HN-F -> EP-SNF -> remote UBCC/home node | 本节点 cache 可缓存，但全局权限由 home UBCC 授权。 |
| UBCC-Exclusive PA | 每 node 私有 metadata range | UR -> HN-F -> local SN-F/DRAM | 只给 UBCC/UR 使用，不允许普通 CPU 访问，不进入 DSM 全局协议。 |

建议定义一个 `UbccAddressMap`：

- `isLocalNormal(addr, node_id)`
- `isDsm(addr)`
- `homeNode(addr)`
- `isDsmLocal(addr, node_id)`
- `isDsmRemote(addr, node_id)`
- `isUbccExclusive(addr, node_id)`
- `lineAddr(addr)`

第一版可用固定连续切片，例如 N=3 时 DSM window 均分为 3 个 home slice。

### 4.2 逻辑模块

| 模块 | 位置 | 推荐实现 | 作用 |
| --- | --- | --- | --- |
| UBCC | CHI domain 外 | 新 C++ SimObject 或 `CHIGenericController` 旁路 controller | 管理本 node 作为 home 的 DSM directory，处理 outer protocol。 |
| EP-RNF | CHI domain 内侧 RN-F 抽象 | 推荐基于 `CHIGenericController` 实现 | 把外部请求转成对本地 HN-F 的 CHI RN-F 请求，接收本地 HN-F snoop/response。 |
| EP-SNF | CHI domain 内侧 SN-F 抽象 | 推荐基于 `CHIGenericController` 实现 memory responder | 作为 DSM Remote 的数据来源，对 HN-F 的 `ReadNoSnp/WriteNoSnp` 做远端转换。 |
| UR | CHI domain 内侧 RN-F/RNI | 第一版可为 UBCC 内部 map；第二版为 `CHIGenericController` RN-F | 让 UBCC 访问 DSM Local data 和 UBCC-Exclusive metadata。 |
| Outer Network | CHI domain 外 | 第一版 fixed latency message queues；第二版可用 Garnet 或独立 network | 连接 N 个 UBCC/EP，传输 global MESI 请求。 |

### 4.3 对原方案的主要修正

#### 修正 1：EP-SNF 不能单独保证全局写权限

HN-F 在处理本地 RN-F 的 `ReadUnique` miss 时，会向下游 SN-F 发 `ReadNoSnp` 获取数据。EP-SNF 如果只看到 `ReadNoSnp`，无法判断这是 shared read 还是 unique read，更无法在 HN-F 给本地 RN-F 返回 unique 前完成全局 invalidation。

计划中的解决方案：

- 在 HN-F 的 DSM address path 增加权限钩子：当 HN-F 收到 DSM line 的 `ReadShared`、`ReadUnique`、`MakeReadUnique`、`CleanUnique`、writeback/evict 时，先向本 node EP/UBCC 申请 global permission。
- EP-SNF 仍保留，用于 HN-F miss 时取 DSM Remote 的 data block。
- EP-RNF 用于外部请求进入本地 CHI domain，例如 remote node 要读取 node0 的 DSM Local line，需要 EP-RNF 对 node0 HN-F 发起内部 read/snoop/clean 操作。

#### 修正 2：全局 MESI 与节点内 CHI/MOESI 状态映射要显式定义

建议映射：

| 节点内 CHI 可能状态 | 对外 global summary | 说明 |
| --- | --- | --- |
| 无本地 cache | I | 本节点不是 sharer/owner。 |
| 只有 clean shared | S | 可与其他 node 共享。 |
| unique clean | E 或 M-like owner | 第一版可当 M owner 处理，避免 E 优化复杂度。 |
| unique dirty | M | 全局唯一 dirty owner。 |
| shared dirty | M | 节点内可 SD，但对外必须表现为唯一 dirty owner；其他 node 要读时先 downgrade/writeback。 |
| HN-F/L3 clean copy but no RN-F sharer | 可选 S 或 I | 第一版建议记录为 S if line may serve data；精确版区分 LLC-only clean。 |

第一版全局 directory 可只实现 MSI，后续再加 E 优化。这样可以降低状态转换复杂度。

#### 修正 3：UR 访问 metadata 时必须避免协议递归

UBCC 处理 DSM 请求时若通过 UR 访问 UBCC-Exclusive PA，可能产生新的 CHI 请求。如果这些请求又触发 UBCC/EP，会形成递归和死锁。计划中必须保证：

- UBCC-Exclusive PA 不属于 DSM PA。
- HN-F 对 UBCC-Exclusive PA 不调用 global permission hook。
- UR 有独立 TBE/credit 预算，不与 EP-RNF/EP-SNF 的外部请求等待形成循环。
- 第一版目录先放 UBCC 内部 SRAM map，避免一开始引入 UR 递归风险。

## 5. EP 详细设计

### 5.1 EP 组件划分

每个 Node 一个 `ExternalProxy` wrapper，内部包含：

- `EP_RNF_Controller`：对内表现为 RN-F，请求本地 HN-F，并处理本地 HN-F 发来的 snoop/response/data。
- `EP_SNF_Controller`：对内表现为 SN-F，为本地 HN-F 的 DSM Remote miss 提供 data/ack。
- `EP_StateTable`：每 line 维护本 node 内侧 summary 和外侧 global grant summary。
- `EP_UBCC_Link`：与本 node UBCC 交换 global request/response。
- `EP_TransactionTable`：跨 CHI 内侧 transaction 与 outer transaction 的 TBE 表，保存 txn id、addr、requestor、data beat、expected ack 数。

实现选择：

- EP-RNF/EP-SNF 推荐继承 `CHIGenericController`，直接收发 `CHIRequestMsg`、`CHIResponseMsg`、`CHIDataMsg`。
- 不建议第一版用标准 `CHI_RNF` + fake CPU sequencer，因为 EP 需要接收 outer protocol 驱动，而不是 CPU port 驱动。
- EP-SNF 不应继承 `CHI_Memory_Controller`，因为它不是固定 memory port，而是远端权限和数据的桥。

### 5.2 EP 维护的状态

每个 cache line 的 EP 状态建议分成两侧摘要。

内侧摘要 `InnerSummary`：

- `inner_state`: `I`, `S_clean`, `M_dirty_or_unique`, `Pending`
- `hnf_has_line`: 本地 HN-F/L3 是否可能有该 line。
- `local_has_dirty`: 本 node 内是否可能有 dirty data，包括 CHI `UD` 或 `SD`。
- `local_has_unique`: 本 node 内是否可能有 unique permission。
- `local_sharer_count_hint`: 可选，只作为统计/优化，不作为 correctness 依据。
- `sentinel_registered`: 若采用 EP-RNF sentinel 备选方案，表示 EP-RNF 是否已被 HN-F 记录为 sharer/owner。
- `inner_epoch`: 每次本地权限变化递增，用于丢弃 stale outer response。

外侧摘要 `OuterGrantSummary`：

- `home_node`: 该 line 的 global home node。
- `grant_state`: `I`, `S`, `M`, `PendingS`, `PendingM`, `Revoking`, `WritingBack`。
- `owner_node`: 若 `M`，当前 global owner。
- `sharer_mask`: 若 `S`，clean sharer node bitset。
- `dirty_owner_known`: home 是否已确认 dirty owner。
- `data_version`: optional epoch/version，用于处理 racing invalidation。
- `pending_outer_txns`: 等待 global permission/data 的本地请求列表。

EP-SNF TBE：

- `addr`, `line_addr`, `chi_requestor`, `chi_txn_id`
- `origin_hnf`, `original_chi_type`，若 HN-F hook 能传入则记录 `ReadShared`/`ReadUnique`。
- `needed_perm`: `S` 或 `M`
- `data_buf`, `received_data_beats`, `expected_data_beats`
- `state`: `WaitGlobalGrant`, `WaitGlobalData`, `SendingCompData`, `WaitWritebackAck`, `Done`

EP-RNF TBE：

- `addr`, `outer_requestor_node`, `outer_txn_id`
- `operation`: `ReadLocalShared`, `ReadLocalUnique`, `InvalidateLocal`, `DowngradeLocal`, `WritebackLocal`
- `issued_chi_req`: `ReadShared`, `ReadUnique`, `CleanUnique`, `MakeReadUnique` 或 snoop response handling。
- `state`: `IssueInnerReq`, `WaitCompData`, `WaitCompAck`, `ReturnOuterData`, `Done`

### 5.3 Global directory 状态机

建议第一版实现 MSI，E 作为优化后续加入。

Home UBCC per-line directory entry：

- `state`: `I`, `S`, `M`, `Busy`
- `owner`: dirty/unique owner node id，只有 `M` 有效。
- `sharers`: bitset，只有 `S` 有效。
- `pending`: 当前被序列化的 global transaction。
- `data_location`: `HomeDRAM`, `OwnerNode`, `UBCCBuffer`。
- `data_dirty`: dirty data 是否尚未写回 home DRAM。

状态转换：

| 当前 | 请求 | 动作 | 下一状态 |
| --- | --- | --- | --- |
| I | `GlobalReadShared(req)` | 从 home DRAM/本地 HN-F 取数据，发 data，加入 sharer | S |
| I | `GlobalReadUnique(req)` | 从 home DRAM/本地 HN-F 取数据，授予 req owner | M |
| S | `GlobalReadShared(req)` | 发 clean data，加入 sharer | S |
| S | `GlobalReadUnique(req)` | 向所有 sharer 发 invalidate，等待 ack，发 data/permission | M owner=req |
| S | `GlobalEvict(node)` | 移除 sharer；若空则 I | S/I |
| M | `GlobalReadShared(req)` | 向 owner 发 downgrade/recall；owner 返回 data；home 写回或缓存 data；owner 降为 sharer，req 加 sharer | S |
| M | `GlobalReadUnique(req)` | 向 owner 发 transfer/invalidate；owner 返回 data 并失效；req 成 owner | M owner=req |
| M | `GlobalWriteback(owner)` | 接收 data，写回 home local DRAM；owner 清除 | I 或 S |

并发策略：第一版每 line 一个 Busy TBE，其他请求 Nack/Retry 或排队。优先实现排队，便于 deterministic debug。

### 5.4 内侧请求到全局请求的翻译

需要 DSM-aware HN-F hook 将原始 HN-F 事件通知 EP。建议 hook API 语义：

```text
HNF_DSM_PERMISSION_REQ(node, addr, original_chi_req, requestor, wants_data)
HNF_DSM_PERMISSION_RESP(addr, granted_state, data_optional, retry_or_fail)
HNF_DSM_WRITEBACK_NOTIFY(addr, dirty, data_optional)
HNF_DSM_EVICT_NOTIFY(addr)
```

翻译规则：

| 内侧 HN-F 事件 | DSM Local | DSM Remote |
| --- | --- | --- |
| `ReadShared` miss/hit for CPU | 若 home 为本 node，只需本地 directory 标记本 node sharer；若其他 remote sharer/owner 存在，按 global MSI 处理 | 向 home UBCC 发 `GlobalReadShared`，等待 grant/data 后允许 HN-F 完成 |
| `ReadUnique` / `MakeReadUnique` | 若 home 为本 node，UBCC invalidate 其他 nodes 后允许本地 M | 向 home UBCC 发 `GlobalReadUnique`，等待所有 remote invalidation 完成 |
| local clean evict | 更新 home directory sharer bit | 向 home UBCC 发 `GlobalEvict` |
| local dirty writeback | 更新 home data 或通知 owner release | 向 home UBCC 发 `GlobalWriteback(data)` |
| local downgrade due to remote read | EP-RNF/UBCC 触发本地 clean/downgrade，返回 data | 不适用，remote line 的 home 不在本 node |

第一版简化：DSM Local 的本 node CPU 访问也统一经过 UBCC directory，这样本 node 与 remote node 对同一 DSM Local line 的权限序列化一致。

### 5.5 全局请求到本地 CHI 请求的翻译

当 node i 是 line 的 home，其他 node 发来 global request 时，node i UBCC 需要通过 EP-RNF/UR 操作本地 CHI domain。

翻译规则：

| Global request | 本地 CHI 操作 | 说明 |
| --- | --- | --- |
| remote `GlobalReadShared` for DSM Local | EP-RNF 发 `ReadShared` 或 UR 读 local data | 如果本地可能 dirty，需要 HN-F snoop dirty owner 后返回 clean data。 |
| remote `GlobalReadUnique` for DSM Local | EP-RNF 发 `ReadUnique`/`MakeReadUnique` 获得 unique，然后本地降级/失效自身 copy | 需要确保 home node 本地 cache 不再持有冲突权限。 |
| remote invalidates this node as sharer | EP-RNF/DSM-aware HN-F 触发本地 invalidate | 可以通过 HN-F hook 直接让 HN-F snoop本地 RN-F，或 EP-RNF sentinel 被 snoop。 |
| remote downgrade this node as dirty owner | EP-RNF 触发 clean/writeback，取回 data | 返回 data 给 home UBCC，内侧状态降为 S 或 I。 |

注意：如果 EP-RNF 只是普通 RN-F，它主动发 `ReadShared` 可以获得数据，但“让本地已有 CPU cache 失效/降级”更自然的发起点是 HN-F。计划中优先通过 DSM-aware HN-F hook 实现本地 invalidation/downgrade。

### 5.6 EP-RNF 与 EP-SNF 协同

EP-SNF 的职责：

- 本地 HN-F 对 DSM Remote line 发生 miss 时，提供 data block。
- 对 HN-F 写回 DSM Remote dirty data 时，转成 `GlobalWriteback`。
- 不独自决定 global permission，permission 由 HN-F hook + UBCC 完成。

EP-RNF 的职责：

- 外部 node 请求本 node DSM Local line 时，对本地 HN-F 发起内部操作以获得最新 data。
- 本 node 被 home 要求 invalidation/downgrade 时，协助触发本地 CHI domain 的 snoop/clean。
- 如果采用 sentinel 备选方案，EP-RNF 还需要在 HN-F directory 中代表“外部世界”的 sharer/owner。

协同流程示例：本 node 首次读取 remote DSM line：

1. CPU RN-F 向本地 HN-F 发 `ReadShared`。
2. HN-F 判断 `addr` 是 DSM Remote，触发 EP permission hook。
3. EP 向 home UBCC 发 `GlobalReadShared`。
4. home UBCC 如需从 home node 本地 cache/DRAM 取数，通过 home EP-RNF/UR 获取 data。
5. home UBCC 返回 grant S + data。
6. 本 node EP permission hook 放行 HN-F。
7. HN-F 若缺 data，则向 EP-SNF 发 `ReadNoSnp`；EP-SNF 用已缓存的 outer data 回复 `CompData`。
8. HN-F 向 CPU RN-F 返回 `CompData_SC`。
9. EP state 记录本 node 对该 line 有 global S grant。

流程示例：本 node 对 remote DSM line 写升级：

1. CPU RN-F 向 HN-F 发 `ReadUnique` 或 `MakeReadUnique`。
2. HN-F DSM hook 暂停该 transaction，向 EP 请求 global M。
3. EP 向 home UBCC 发 `GlobalReadUnique`。
4. home UBCC invalidates 当前 sharers 或 recalls dirty owner。
5. 所有 ack/data 到齐后，home UBCC 授予本 node M。
6. EP 通知 HN-F hook 放行。
7. HN-F 继续节点内 CHI unique grant，本地 CPU 获得写权限。
8. EP state 记录本 node 是 global M owner。

### 5.7 Retry、死锁和顺序约束

计划中必须显式处理：

- EP/UBCC 每 line 只允许一个 active transaction，其他请求排队或 retry。
- `REQ/SNP/RSP/DAT` 四个 CHI vnet 不能被 EP 内部等待互相堵住；EP TBE 要有容量上限和 deadlock debug counter。
- Global invalidation 必须在 HN-F 发 unique completion 前完成。
- Dirty owner downgrade 必须携带 data，home directory 才能把 M 转 S。
- EP-SNF 收到 HN-F writeback 后，必须等 home UBCC 接收 data 后再返回完成 ack，或记录 durable pending writeback。
- UBCC-Exclusive metadata access 不能等待一个正在等待该 metadata 的 outer transaction。

## 6. UR 与 Global Directory 设计

### 6.1 UR 角色

UR 不是普通 CPU，也不是外部 node。它是 UBCC 的本地 CHI request agent，用来：

- 读取 DSM PA Local 的实际数据，尤其当 home UBCC 要服务 remote read 时。
- 写回 DSM PA Local 的数据，例如 remote dirty owner 释放后更新 home DRAM/L3。
- 读写 UBCC-Exclusive PA 上的 directory backing store。

实现路线：

- Phase 2.1：不实现 UR，UBCC directory 使用内部 C++ map，DSM Local data 通过 EP-RNF 从 HN-F 获取。
- Phase 2.2：实现 `UBCCReaderController`，继承 `CHIGenericController`，支持最小 RN-F read/write transaction。
- Phase 2.3：给 UR 增加 small SRAM directory cache 和 writeback/evict policy。

### 6.2 Directory 存储

第一版 directory entry：

```text
struct DirEntry {
  Addr line;
  State state;       // I/S/M/Busy
  uint32_t sharers;  // N <= 32 first
  int owner;         // valid if M
  bool dirty;
  uint64_t epoch;
}
```

SRAM cache 逐出策略：

- 第一版不逐出，容量设大。
- 第二版固定 set-associative，小容量 miss 时从 UBCC-Exclusive PA 读取 entry。
- dirty directory entry 逐出时，UR 对 UBCC-Exclusive PA 执行 writeback。
- directory backing store 不进入 UBCC global protocol，只受本 node CHI normal coherence 保护。

## 7. Subtask 2 阶段划分

### 阶段 2A：多节点地址与配置骨架

目的：在单 gem5 进程中生成 N=3 个逻辑 Node，每个 Node 复用 Subtask 1 的 CHI domain 结构。

操作步骤：

1. 定义 `NodeConfig`：`node_id`、CPU range、local normal range、DSM local range、UBCC exclusive range、DRAM range。
2. 生成每 node 的 RN-F clusters、HN-F/L3、SN-F/DRAM。
3. 给每 node HN-F 设置 addr ranges：local normal + DSM local + DSM remote + UBCC exclusive，但 DSM remote 的 downstream 指向本 node EP-SNF。
4. 注意 gem5 Ruby 通常假设一个 RubySystem；第一版建议在一个 RubySystem 内放所有 logical node controller，用 node_id 参数约束连接和 address map。
5. 添加 outer network skeleton，只连接 UBCC，不参与 Ruby network。

难度：中。

### 阶段 2B：EP-SNF 远端只读数据源原型

目的：证明 DSM Remote miss 可以通过 EP-SNF 从 home node 取数。

操作步骤：

1. 实现 `EP_SNF_Controller` 基于 `CHIGenericController`，能响应 HN-F 的 `ReadNoSnp`。
2. 对 `ReadNoSnp(addr)`，通过 outer message 向 `homeNode(addr).UBCC` 请求 data。
3. home UBCC 第一版直接从 backing memory 或内部 fake memory map 返回 data；若要真实，使用 home EP-RNF/UR 从 local HN-F 取数。
4. EP-SNF 用 `CHIDataMsg`/`CHIResponseMsg` 按 `CHI-mem.sm` 期望回复 HN-F。
5. 限制该阶段 workload 为 read-only DSM，避免写权限错误。

难度：中到高。

### 阶段 2C：DSM-aware HN-F 权限钩子

目的：让 HN-F 在授予 shared/unique 前获得 UBCC 全局许可。

操作步骤：

1. 在 SLICC `CHI-cache-actions.sm` HN-F path 中识别 DSM address。
2. 为 HN-F TBE 增加 `needs_global_perm`、`global_perm_state`、`global_perm_done` 等字段。
3. 在 `Initiate_ReadShared_Miss/HitUpstream`、`Initiate_ReadUnique_Miss/Upgrade/HitUpstream` 等 path 中插入 `SendGlobalPermReq` 和 `WaitGlobalPermResp` action。
4. 新增 HN-F 与 EP/UBCC 的 message buffer，或把 EP 作为 generic controller 接收专用 `CHIRequestMsg` extension。推荐新增专用 Ruby message type，避免污染标准 CHI request type。
5. EP 收到 permission request 后执行 global directory transaction，返回 permission response。
6. HN-F 只有收到 permission response 后才能继续 `SendCompData`/`SendComp`。
7. 对 non-DSM PA 保持原始 CHI 行为。

难度：高。

### 阶段 2D：Home UBCC directory 与 global MSI

目的：实现节点间 MSI 一致性。

操作步骤：

1. 实现 `UBCCController`，维护 per-line directory map。
2. 实现 outer message：`GlobalReadShared`、`GlobalReadUnique`、`GlobalInvalidate`、`GlobalDowngrade`、`GlobalWriteback`、`GlobalEvict`、`GlobalDataResp`、`GlobalAck`、`GlobalRetry`。
3. 对每 line 进行序列化，其他请求排队。
4. 实现 sharer invalidation 和 dirty owner recall。
5. 实现 data movement：dirty owner -> home UBCC -> requester；clean home data -> requester。
6. 实现 stats 和 debug print：state transition、sharer mask、owner、pending event。

难度：高。

### 阶段 2E：EP-RNF 本地 cache 干预

目的：让 global invalidation/downgrade 能真正影响本 node 内 CHI cache。

操作步骤：

1. 实现 EP-RNF 发起内部 CHI transaction 的能力。
2. 对 home UBCC 要求的 local read/downgrade，EP-RNF 向本地 HN-F 发 `ReadShared`/`ReadUnique`/`CleanUnique` 或触发 HN-F hook。
3. 若采用 sentinel 机制，确保 EP-RNF 被 HN-F 记录为 DSM line 的 external sharer，并在本地 upgrade 时收到 snoop。
4. 验证 local dirty line 被 remote read 触发时能 downgrade 并返回最新 data。
5. 验证 local clean sharers 被 remote write 触发时全部 invalidated。

难度：高。

### 阶段 2F：UR-backed directory

目的：把 directory 从无限 C++ map 过渡到 UBCC SRAM cache + UBCC-Exclusive PA backing store。

操作步骤：

1. 实现 UR 最小 read/write。
2. 定义 directory entry 在 UBCC-Exclusive PA 上的 layout。
3. 实现 UBCC SRAM cache lookup/miss/fill/writeback。
4. 对 metadata range 加 address guard，确保 CPU 不能访问或访问会 fatal。
5. 增加 deadlock test：metadata miss during global transaction。

难度：中到高。

### 阶段 2G：验证与收敛

目的：证明多节点 DSM 一致性。

验证用例：

- Node0 read Node1 DSM Local，Node1 后续 write，Node0 必须被 invalidate。
- Node0 write Node1 DSM Local 后，Node2 read 必须看到 Node0 写入，并触发 Node0 downgrade/writeback。
- 三节点 false sharing ping-pong，验证每 line 只有一个 M owner。
- Local Normal PA 不触发任何 UBCC/EP message。
- UBCC-Exclusive PA 只由 UR 访问，不触发 global coherence。
- Dirty shared inside one node 对外表现为 M，remote read 必须使该 node 降级并提供最新 data。

工具：

- Ruby debug flags：`RubyProtocol`、新增 `UBCC`、新增 `ExternalProxy`。
- per-line trace：addr、node、old state、event、new state。
- assert：global directory 不允许 `owner` 与 `sharers` 冲突；不允许两个 node 同时 M。

难度：高。

## 8. 建议的最小里程碑

1. M1：单节点 CHI C=2/M=2，cluster-shared L2，L3 HN-F，DRAM SN-F 可跑通。
2. M2：N=3 logical node address map 和配置骨架完成，Local Normal PA 工作。
3. M3：DSM Remote read-only 通过 EP-SNF + UBCC fake directory/data 工作。
4. M4：DSM-aware HN-F hook 完成，global S/M permission 可阻塞 HN-F completion。
5. M5：global MSI directory 完成，跨节点 read/write ping-pong 正确。
6. M6：EP-RNF/UR 完整接入，dirty owner recall 和 metadata backing store 正确。
7. M7：压力测试、stats、debug 工具、文档完善。

## 9. 主要风险

- HN-F hook 改动 SLICC 状态机，容易引入 deadlock 或破坏非 DSM PA 行为。
- EP-SNF 无法从 `ReadNoSnp` 推断 original permission，这是架构性风险，必须通过 HN-F hook 或 sentinel 机制解决。
- 一个 RubySystem 中建多个逻辑 Node 需要小心 MachineID、controller ordering、NetDest 和 address range。
- Global directory 与本地 CHI directory 双层目录可能短暂不一致，需要 epoch/TBE 序列化保证 stale response 不生效。
- CHI 内部允许 `SD`，global MESI 不允许多个 dirty shared node；必须在节点边界把 `SD` 折叠成唯一 M owner。
- UR-backed metadata 可能导致协议递归；第一版应避免 UR，先用内部 map。
- Atomic、DVM、DMA、I/O coherent 不是第一版目标，若 workload 依赖这些语义，需要单独阶段支持。

## 10. 推荐决策

建议先确认两个设计选择后再进入实现：

1. Cluster L2 是否确认为每 cluster 共享。若不是，可直接复用默认 CHI per-core private L2，大幅降低 Subtask 1 工作量。
2. Subtask 2 是否接受第一版修改 HN-F SLICC。若不接受，EP 只能做 sentinel sharer 方案，但复杂度和正确性风险更高。
