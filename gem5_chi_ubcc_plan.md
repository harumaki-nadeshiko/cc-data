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

## 1. 已确认约束与当前假设

本版计划按以下约束推进：

- Cluster 内 L2 是 cluster shared。L1I/L1D 每 core 私有，L2 每 cluster 一个共享实例。
- 最终目标是每个节点运行独立 gem5 实例，UBCC 在 gem5 外部，通过 ns-3 或其他外部网络/模块连接多个 gem5。当前阶段先用单个 gem5 仿真 N 个逻辑节点。
- 即使当前用单 gem5，也要尽量隔离不同节点的 Ruby ARM CHI domain。具体做法是隔离每个 node 的 RN-F/HN-F/SN-F destination set、地址范围和逻辑网络边界，只允许 UBCC/EP 路径跨节点交互。
- 第一版不支持扩展 CHI feature，例如 DVM、atomic、exclusive monitor、IO coherent DMA。目标 workload 限制为普通 cacheable load/store 和 cache line 粒度一致性。
- 所有 Node 使用相同 global PA。Node `i` 的 DSM PA Local 为 `[dsm_base + dsm_size * i, dsm_base + dsm_size * (i + 1))`。整个 DSM PA 为 `[dsm_base, dsm_base + dsm_size * n)`。
- 第一版 global directory 可以先不真实落到 DRAM；但 UR 必须从早期开始具备向本地 CHI/内存发起读写的能力。
- UR 后续访问的空间由设计保证是 UBCC-Exclusive PA。实现仍需在地址译码上显式排除 DSM global coherence，并限制 UR 使用独立资源，不能与 EP-RNF/EP-SNF 共用 TBE、credit、request queue 或关键 response queue。

## 2. 总体可行性与难度

Subtask 1 可行性高，难度中等。gem5 已有 CHI Ruby 协议，默认已经包含 L1/L2 RN-F、L3 HN-F、DRAM SN-F 的基本结构。主要工作是自定义 node generator 和拓扑脚本，使 C=2、M=2 的 cluster 结构明确表达为每 cluster 一个 RN-F、共享 L2、全节点共享 HN-F/L3 和 SN-F。

Subtask 2 可行性中等，难度高。难点不是多几个 controller，而是 EP 如何在 Ruby CHI HN-F 决策期间介入全局权限。仅把 DSM Remote 的 HN-F 下游接到 EP-SNF，可以处理远端 miss 取数，但不能保证本节点获得 `ReadUnique`/写权限前已经完成全局失效。现有 HN-F 向 SN-F 取数时会使用 `ReadNoSnp`，EP-SNF 看不到最初来自 RN-F 的 `ReadShared` 或 `ReadUnique` 意图。因此完整 UBCC 需要在 HN-F 权限路径或等价 sentinel 机制中介入。

- 推荐路径：实现 DSM-aware HN-F permission hook，并把 EP-SNF 作为 DSM Remote data plane，把 EP-RNF 作为 inbound local-domain operation agent。
- 备选路径：让 EP-RNF 作为每条 DSM line 的 sentinel sharer/owner 出现在本地 HN-F directory 中，强制 HN-F 在本地升级时 snoop EP-RNF，由 EP-RNF 再触发全局权限变化。该方案更少改 HN-F，但状态更绕，容易出现漏登记、重入和死锁，不建议作为主要正确性机制。

建议采用“先功能、后精确”的路线：先完成 Subtask 1，再搭出单 gem5 内的多逻辑节点隔离骨架和 UR read/write 能力，再实现只读/远端取数版 EP-SNF，最后实现带 DSM-aware HN-F 权限钩子的完整 UBCC。最终拆成多 gem5 + 外部 UBCC 时，应尽量复用同一套 EP/UR/outer message 语义。

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

DSM global PA 采用固定切片：

```text
DSM_GLOBAL = [dsm_base, dsm_base + dsm_size * n)
DSM_LOCAL(i) = [dsm_base + dsm_size * i,
                dsm_base + dsm_size * (i + 1))
DSM_REMOTE(i) = DSM_GLOBAL - DSM_LOCAL(i)
homeNode(addr) = floor((addr - dsm_base) / dsm_size)
```

如果一个 workload 或 OS 视角中每个 node 都能看到同一段 DSM PA，则每个逻辑 node 的 HN-F 都需要覆盖完整 `DSM_GLOBAL`，但 HN-F downstream target 按 `homeNode(addr)` 决定：本 node 的 DSM Local 走 local SN-F/DRAM，其他 node 的 DSM Remote 走本 node EP-SNF。Local Normal PA 和 UBCC-Exclusive PA 不应与 `DSM_GLOBAL` 重叠。

建议定义一个 `UbccAddressMap`：

- `isLocalNormal(addr, node_id)`
- `isDsm(addr)`
- `homeNode(addr)`
- `isDsmLocal(addr, node_id)`
- `isDsmRemote(addr, node_id)`
- `isUbccExclusive(addr, node_id)`
- `lineAddr(addr)`

第一版 N=3 时直接使用上述固定连续切片，不做 page migration、不做动态 home 迁移。

### 4.1.1 单 gem5 内的 CHI domain 隔离策略

最终多 gem5 版本天然每个进程一个 Ruby CHI domain；当前单 gem5 版本需要人为隔离。推荐先采用“一个 RubySystem，多个逻辑 CHI island”的实现，而不是一开始尝试多个 RubySystem。

单 RubySystem 方案：

- 每个 node 拥有自己的 RN-F cluster、HN-F/L3、SN-F/DRAM、EP、UR、UBCC wrapper。
- `rnf.setDownstream()` 只指向同 node 的 HN-F，不包含其他 node 的 HN-F。
- `hnf.setDownstream()` 对 Local Normal 和 DSM Local 指向同 node local SN-F；对 DSM Remote 指向同 node EP-SNF；不直接指向其他 node SN-F。
- Ruby network 物理上可以共用一个 network 对象，但 destination set 和 address map 必须让普通 CHI message 不跨 node。跨 node 只能通过 UBCC outer message。
- Debug/stats 中给每个 controller 增加 `node_id`，每次普通 CHI message 发送时 assert source/destination 属于同 node，除非 destination 是本 node EP/UR。

多 RubySystem 方案：

- 语义更接近最终多 gem5，但 gem5 配置和 CPU port/memory port binding 更复杂，且很多 Ruby helper 假设单个 `system.ruby`。
- 建议作为后续迁移验证目标，不作为第一版功能原型。

### 4.2 逻辑模块

| 模块 | 位置 | 推荐实现 | 作用 |
| --- | --- | --- | --- |
| UBCC | CHI domain 外 | 新 C++ SimObject 或 `CHIGenericController` 旁路 controller | 管理本 node 作为 home 的 DSM directory，处理 outer protocol。 |
| EP-RNF | CHI domain 内侧 RN-F 抽象 | 推荐基于 `CHIGenericController` 实现 | 把外部请求转成对本地 HN-F 的 CHI RN-F 请求，接收本地 HN-F snoop/response。 |
| EP-SNF | CHI domain 内侧 SN-F 抽象 | 推荐基于 `CHIGenericController` 实现 memory responder | 作为 DSM Remote 的数据来源，对 HN-F 的 `ReadNoSnp/WriteNoSnp` 做远端转换。 |
| UR | CHI domain 内侧 RN-F/RNI | 第一版即实现 `CHIGenericController` RN-F 的最小 read/write 能力；directory 可暂放 UBCC 内部 map | 让 UBCC 访问 DSM Local data 和 UBCC-Exclusive metadata。 |
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
| unique clean | E | 全局只有该 node 有 clean exclusive 权限，可本地 silent upgrade 为 M，但需要通知 home UBCC 记录 E->M。 |
| unique dirty | M | 全局唯一 dirty owner。 |
| shared dirty | M | 节点内可 SD，但对外必须表现为唯一 dirty owner；其他 node 要读时先 downgrade/writeback。 |
| HN-F/L3 clean copy but no RN-F sharer | 可选 S 或 I | 第一版建议记录为 S if line may serve data；精确版区分 LLC-only clean。 |

结论：建议从数据结构和 message enum 一开始就使用 MESI，而不是写死 MSI。实现时可以先关闭 E grant，将所有独占 clean grant 按 M-like owner 处理；但接口、directory entry、EP summary、HN-F hook response 都要保留 `E`。这样后续打开 E 只需要补状态转换和优化路径，不需要重构所有权限 API。

#### 修正 3：UR 访问 metadata 时必须避免协议递归

UBCC 处理 DSM 请求时若通过 UR 访问 UBCC-Exclusive PA，可能产生新的 CHI 请求。如果这些请求又触发 UBCC/EP，会形成递归和死锁。计划中必须保证：

- UBCC-Exclusive PA 不属于 DSM PA。
- HN-F 对 UBCC-Exclusive PA 不调用 global permission hook。
- UR 有独立 TBE/credit 预算，不与 EP-RNF/EP-SNF 的外部请求等待形成循环。
- 第一版 directory entry 可以先放 UBCC 内部 SRAM/map，不落 DRAM；但 UR 的 read/write transaction path 仍需早期实现并可被单独测试。

## 5. MSI/MESI 与 EP/HN-F 路线评估

### 5.1 MSI 写完后再改 MESI 的工作量

如果第一版代码硬编码 MSI，后续改成 MESI 的工作量偏大。原因是 E 不只是 directory 多一个状态，还会影响 HN-F hook、EP summary、本地升级路径、outer message 的 permission 语义。

需要改动的部分：

- Global directory：`state` 从 `I/S/M/Busy` 扩展为 `I/S/E/M/Busy`，并增加 `exclusive_owner` 或复用 `owner` 表示 E owner。
- Outer protocol：grant/response 要能表达 `GrantE`，不能只表达 `GrantS` 和 `GrantM`。
- EP summary：`grant_state` 要区分 `S`、`E`、`M`，并记录 E owner 的 clean exclusive 权限。
- HN-F hook：本 node 在持有 global E 后，本地 `ReadUnique`/`MakeReadUnique` 不应再发全局 invalidation，只需要通知 home UBCC E->M 或在写回时标记 dirty。
- Local state mapping：节点内 CHI `UC` 应映射到 global E，`UD/SD` 映射到 global M。若此前把 `UC` 当 M，后续要拆分 dirty ownership 和 clean exclusive ownership。
- 测试：需要新增 “single clean owner silent upgrade”、“remote read of E owner”、“E owner eviction without dirty data” 等测试。

工作量判断：

- 如果从第一天就用 `GlobalPerm = I/S/E/M`、`GrantType = S/E/M`、`owner` 字段，并只是暂时不发 `GrantE`，后续打开 MESI 是中等工作量，主要补状态转换和测试。
- 如果先实现纯 MSI 并在代码各处假设 `owner` 一定 dirty、`S` 才是 clean、`M` 才能独占，后续改 MESI 是中到高工作量，会涉及 UBCC、EP、HN-F hook 和测试的系统性修改。

建议：从一开始直接按 MESI 设计数据结构和消息；第一阶段可以运行在 “MSI-compatible mode”，即 home UBCC 暂时不主动授予 E，或者只在明确安全的 `ReadUnique` clean path 授予 E。这样保留演进空间，同时不增加第一阶段核心正确性风险。

### 5.2 路线 A：DSM-aware HN-F 权限钩子

路线 A 在 HN-F 处理 DSM line 的关键权限路径上插入 UBCC/EP permission request。HN-F 在完成本地 CHI `ReadShared`、`ReadUnique`、`MakeReadUnique`、writeback/evict 前，先获得 global MESI permission。

主要实现任务：

- 修改 `CHI-cache.sm` TBE 字段，增加 `global_perm_needed`、`global_perm_granted`、`global_grant_state`、`global_req_type`、`global_epoch`。
- 修改 HN-F path 的 SLICC actions/transitions：`ReadShared`、`ReadUnique`、`MakeReadUnique`、`CleanUnique`、evict、writeback、snoop-induced downgrade。
- 新增 HN-F 到 EP/UBCC 的 message buffer 或专用 Ruby message type，例如 `GlobalPermReq/GlobalPermResp`。
- HN-F 对非 DSM PA 走原路径，确保 Local Normal PA 不受影响。
- HN-F 对 UBCC-Exclusive PA 明确禁止 global permission hook。
- EP/UBCC 返回 `GrantS/GrantE/GrantM/Retry/Nack`，HN-F 根据 grant 决定继续、阻塞或 retry。

优点：

- HN-F 能看到原始请求语义，知道请求是 `ReadShared` 还是 `ReadUnique`。
- 能覆盖 HN-F hit、upgrade、LLC 有 clean data 等不访问 SN-F 的路径。
- 全局 invalidation 可以严格发生在 HN-F 给 RN-F 返回 unique completion 之前。
- 状态机语义清晰，适合最终多 gem5 外部 UBCC 模式。

缺点：

- 要改 SLICC HN-F 状态机，任务量高，debug 难度高。
- 需要设计 HN-F 与 EP/UBCC 的专用 flow control，避免阻塞 Ruby vnet 造成 deadlock。
- 需要更完整的回归测试，防止破坏非 DSM CHI 行为。

任务量：高，但这是最直接、最可控的正确性路线。

是否还需要 EP-RNF：需要，但角色不同。路线 A 中 EP-RNF 不再负责“拦截本地 CPU 发起的 DSM 权限请求”；这个由 HN-F hook 完成。EP-RNF 仍建议保留，用于外部 global request 进入本地 CHI domain，例如 home UBCC 需要从本地 dirty cache 取回 DSM Local data，或需要让本地 cache 对某条 line downgrade/invalidate。也可以用更强的 HN-F sideband command 替代 EP-RNF，但那会让 HN-F hook 继续膨胀。工程上更清晰的划分是：HN-F hook 处理本地 CPU outbound permission，EP-RNF/UR 处理 UBCC inbound local-domain operation。

### 5.3 路线 B：sentinel EP-RNF

路线 B 让 EP-RNF 作为本地 HN-F directory 中代表“外部世界”的特殊 RN-F。理论上，当本地 CPU 对 DSM line 请求 unique 时，HN-F 会 snoop 这个 sentinel；EP-RNF 收到 snoop 后再触发 global permission/invalidation。

主要实现任务：

- EP-RNF 必须能作为普通 RN-F 被 HN-F directory 记录为 sharer/owner。
- 每条可能被外部节点持有或需要外部仲裁的 DSM line，都要保证 sentinel 已注册到 HN-F directory。
- EP-RNF 需要响应 HN-F snoop，并把 snoop 翻译为 global UBCC 请求。
- EP-SNF 仍需处理 DSM Remote data miss。
- 需要定义 sentinel 在 DSM Local、DSM Remote、local-only line 上何时注册、何时注销。

优点：

- 表面上对 HN-F SLICC 权限路径的直接改动较少。
- 利用 HN-F 既有 snoop 机制，让“本地升级会通知外部世界”的语义看起来比较自然。

缺点：

- 正确性依赖 sentinel 一定已被 HN-F 记录。第一次访问、evict、directory replacement、HNF hit 但 sentinel 未登记等场景很容易漏。
- EP-RNF 被 snoop 时只知道 HN-F 发来的 snoop，不一定知道原始全局目标语义，仍需要额外状态推断。
- 对 DSM Remote line，HNF miss 取数走 EP-SNF；如何同时让 EP-RNF 成为 sentinel sharer/owner 是额外协议。
- 会把“外部世界 summary”和“本地 HN-F directory entry”绑定得很紧，双目录状态更容易不一致。
- 如果为了保险给所有 DSM line 预注册 sentinel，会产生巨大无效状态和额外 snoop。

任务量：中到高。直接 SLICC 改动可能少于路线 A，但状态维护和 corner case 工作量更高，debug 风险更大。

是否可与路线 A 同时选择：可以，但不建议把 sentinel 作为 correctness 的核心机制。两者同时使用时，sentinel 更适合作为 debug/保守 fallback 或统计外部存在性的 HN-F directory placeholder；真正的 global permission 仍由 HN-F hook 决定。否则同一条 line 会同时被 HN-F hook 和 sentinel snoop 触发 global transaction，必须处理重复请求、重入和顺序冲突。

### 5.3.1 Sentinel 为主的可行性评估

如果坚持以 Sentinel EP-RNF 为主，可以采用如下原则：内部到外部、外部到内部的 global coherence 影响都通过 EP-RNF；EP-SNF 只处理 HN-F miss 时的数据填充；当 HN-F 从 EP-SNF 取得 DSM Remote 数据后，同时把 EP-RNF 登记为该 line 的 sentinel sharer/owner。该路线有可行性，但不是“零 HN-F 修改”路线，至少需要 HN-F 支持 sentinel registration 这个 side effect。

核心机制：

- HN-F 第一次 miss 到 DSM Remote 时，向 EP-SNF 取数。EP-SNF 与 home UBCC 交互，拿到 data 和初始 global grant。
- HN-F 在完成原始 RN-F 请求前，必须把 EP-RNF 写入本地 HN-F directory，作为 external-world sentinel。
- 之后 HN-F hit、LLC clean data hit、本地 shared->unique upgrade 不访问 EP-SNF；但由于 EP-RNF 已在 directory 中，本地权限变化会触发 HN-F snoop EP-RNF。
- EP-RNF 收到 snoop 后，把本地 HN-F snoop 翻译成 global request，例如 external invalidate、dirty owner recall、global M acquire。
- 外部 global request 进入本 node 时，也由 EP-RNF 对本地 HN-F 发起 `ReadShared`、`ReadUnique` 或等价操作，让 HN-F 通过正常 CHI snoop 影响本地 CPU cache。

这个方案成立需要满足的条件：

- Sentinel registration 必须和原始 miss completion 保持顺序。不能先把 data 返回给 CPU，再异步登记 EP-RNF，否则存在本地 CPU 立即 upgrade 而 HN-F 还不知道 EP-RNF 的窗口。
- EP-SNF 普通 `ReadNoSnp` response 本身不能自然让 HN-F 把另一个 RN-F 加入 directory。需要修改 HN-F fill action，或新增 EP-SNF 到 HN-F 的 sideband 信息，明确“本次 DSM fill 后请登记 EP-RNF 为 sentinel”。
- EP-SNF 如果看不到原始请求语义，无法区分最初是 `ReadShared` 还是 `ReadUnique`。可选解法有两个：第一，HNF->EP-SNF miss 请求携带 `original_chi_req` 和 `needed_perm`；第二，EP-SNF 对所有 DSM Remote miss 保守申请 global M。第二种正确但会破坏读共享性能，并让 EP 状态中出现“global M 但本地 CPU 只有 clean/shared”的过保守状态。
- EP-RNF 在 HN-F directory 中的状态必须有严格定义。推荐至少区分 `ExternalSharerSentinel` 和 `ExternalOwnerSentinel`，不要简单把 EP-RNF 永远登记为 owner。
- EP-RNF 响应 HN-F snoop 时可能需要等待 outer UBCC/ns-3 round trip。HN-F snoop transaction 会被长时间挂起，需要为 EP-RNF snoop TBE、outer request TBE 和 retry/timeout 设计独立资源，避免死锁。

Sentinel 状态建议：

| Sentinel 状态 | HN-F directory 表达 | 语义 | 典型用途 |
| --- | --- | --- | --- |
| None | EP-RNF 不在 directory | 外部世界对该 line 没有需要本 HN-F 感知的状态 | Local Normal PA，或 DSM line 完全未接触。 |
| ExternalSharer | EP-RNF 是 sharer | 其他节点可能有 clean copy；本地要获得 M 时必须先 snoop EP-RNF | 本地 CPU 持有 S，global 仍有其他 S sharer。 |
| ExternalOwner | EP-RNF 是 owner/unique holder | 外部世界可能持有唯一或最新 copy；本地读写必须先从 EP-RNF/UBCC 获取或转移权限 | 本节点被 remote write/read-unique invalidated 后，EP-RNF 留在本地 HN-F 中代表外部 owner。 |
| ExternalPending | EP-RNF 有 transient TBE | 正在等待 global grant/data/ack，HN-F 对该 line 的相关事务必须阻塞或 retry | Snoop EP-RNF 后等待 UBCC。 |

注意：`ExternalOwner` 不应和本地 CPU dirty owner 同时存在。若 remote node 获取 M，EP-RNF 可以通过向 HN-F 发 `ReadUnique` 让本地 CPU sharer/owner 失效，然后 EP-RNF 成为本地 HN-F 看到的 owner；此时 EP-RNF owner 表示“外部世界 owner”，不是 EP-RNF 本身真的在本地 cache 中持有数据。

首 miss 后如何登记 EP-RNF：

- 对 DSM Remote `ReadShared` miss：EP-SNF 从 home UBCC 获取 data。若 global grant 是 S，HN-F 给本地 requester S，同时把 EP-RNF 登记为 `ExternalSharer`，代表其他节点仍可能共享。若 global grant 是 E 且没有其他 sharer，可以不登记 EP-RNF，或登记为可快速删除的 clean sentinel；为了简单和保守，也可以登记 `ExternalSharer`，代价是本地 future upgrade 会多一次 EP-RNF snoop。
- 对 DSM Remote `ReadUnique` miss：EP-SNF 必须从 home UBCC 获取 M。HN-F 给本地 requester unique/M 后，通常不应再把 EP-RNF 登记为 owner，因为本地 requester 是 owner；可以不登记 sentinel，或登记一个非冲突的 external summary 只用于 future external request debug。外部请求本地 owner 时会由 home UBCC 直接联系本 node EP-RNF。
- 如果 EP-SNF 无法得知原始 miss 类型而保守申请 M，则 HN-F 仍可能只给本地 requester S。这是正确但过保守的 global state：home UBCC 认为本 node 是 M/E owner，后续其他 node 请求都要联系本 node EP-RNF。此时最好让 EP-RNF 在本地 HN-F 中登记为 `ExternalSharer` 或记录 EP state，而不是和本地 requester 冲突地登记为 owner。

EP-RNF 在该路线中负责的场景：

- HN-F snoop EP-RNF：本地 CPU 对 DSM line 的 read/upgrade/writeback/evict 导致 HN-F snoop sentinel，EP-RNF 将 snoop 转成 global permission/data request，并在 UBCC 完成后返回 snoop response。
- 外部 remote read 本节点可能持有的 line：home UBCC 联系本 node EP-RNF；EP-RNF 对本地 HN-F 发 `ReadShared` 或等价请求，让 HN-F 召回本地 dirty owner 或确认 clean data，然后把 data/ack 返回 UBCC。
- 外部 remote write/read-unique 需要失效本节点：home UBCC 联系本 node EP-RNF；EP-RNF 对本地 HN-F 发 `ReadUnique` 或等价请求，迫使 HN-F invalidate 本地 sharers/owner，随后 EP-RNF 可在 HN-F 中保留为 `ExternalOwner` sentinel。
- 本地 HN-F hit/LLC clean data path 的 global 干预：这类路径不会访问 EP-SNF，只依赖 HN-F directory 中的 EP-RNF sentinel 被 snoop。
- 本地 upgrade path 的 global 干预：如果 EP-RNF 是 sharer，HN-F 的 `SnpUnique`/`SnpCleanInvalid` 到 EP-RNF 会触发 global invalidation；UBCC ack 后 EP-RNF 返回 snoop response，HN-F 才能完成本地 unique grant。

EP-SNF 在该路线中负责的场景：

- 只处理 HN-F 对 DSM Remote 的 miss/fill 数据请求，即 HN-F 需要从“远端内存源”取得 line data 时。
- 对 miss 请求向 home UBCC 发起初始 global read/read-unique 或保守 global M acquire，并取得 data。
- 将 data 返回 HN-F，同时携带或触发 sentinel registration 信息，让 HN-F 把 EP-RNF 登记为合适的 sentinel 状态。
- 处理 HN-F 对 DSM Remote 的 writeback/evict，把 dirty data 或 owner release 转发给 home UBCC。
- 不负责 HN-F hit、LLC clean hit、upgrade、外部 invalidation/downgrade；这些都由 EP-RNF sentinel 路径处理。

可行性结论：Sentinel 为主路线可以实现，但必须把“EP-SNF fill 后同步登记 EP-RNF sentinel”作为 HN-F 的明确协议动作，否则会漏掉 HN-F hit/upgrade/LLC clean data path。它比纯 HN-F permission hook 更依赖 directory 状态不丢失，corner case 更多；但它的优点是后续绝大多数内外转换都统一走 EP-RNF，概念上更接近“外部世界是本地 CHI domain 里的一个特殊 RN-F”。如果选择这条路线，建议把 sentinel registration、sentinel state、EP-RNF snoop response 作为第一优先级验证对象。

### 5.4 路线 C：在 HN-F -> EP-SNF 请求中携带 Global Coherence 信息，并保留 EP-RNF

路线 C 是混合方案：HNF 仍把 DSM Remote 的下游目标设为 EP-SNF，但不再发送普通无语义的 `ReadNoSnp`。HNF 对 DSM line 发送增强请求，携带 `needed_perm=S/E/M`、`original_chi_req`、`requestor`、`epoch` 等 global coherence 信息。EP-SNF 同时承担 data plane 和 permission request endpoint；EP-RNF 负责外部请求影响本地 CHI domain。

主要实现任务：

- 修改 HN-F 生成下游请求的动作，让 DSM Remote 的 downstream request 带上 global permission 信息。
- 对 HN-F hit/upgrade 不访问 SN-F 的情况，仍必须强制产生一个 permission request 到 EP-SNF 或 EP control endpoint。
- EP-SNF 收到增强请求后向 home UBCC 发 `GlobalReadShared/GlobalReadUnique`，收到 grant/data 后再回复 HN-F。
- EP-RNF 保留，用于 inbound invalidate/downgrade/data recall。

优点：

- 和现有拓扑直觉接近：DSM Remote 的“内存来源”就是 EP-SNF。
- Data plane 和 permission plane 可以在同一个 EP-SNF TBE 中关联，首版实现容易追踪。
- EP-RNF 保持清晰角色：不拦截本地 CPU outbound request，只处理外部对本地 CHI domain 的影响。

缺点：

- 如果只改 HNF->EP-SNF miss 路径，仍然不正确，因为 HN-F hit、upgrade、LLC clean copy path 可能完全不访问 EP-SNF。
- 因此路线 C 本质上仍需要 DSM-aware HN-F permission hook，只是 hook 的 endpoint 选为 EP-SNF，而不是独立 EP control controller。
- 需要扩展 CHI request message 或新增旁路 message。直接污染标准 `ReadNoSnp` 字段会使协议边界不清晰。

任务量：高，但低于“完整 HN-F hook + 独立复杂 EP control plane”的最重版本。它是推荐的工程折中路线。

### 5.5 推荐路线

推荐采用路线 C 的结构化版本：

- HN-F 增加 DSM-aware permission hook，这是 correctness 必需项。
- Permission hook 的 endpoint 可以放在 EP-SNF 所属的 `ExternalProxy` 内，但不要把语义伪装成普通 `ReadNoSnp`；使用专用 `GlobalPermReq/Resp` 或增强的内部 EP message。
- EP-SNF 负责 DSM Remote data plane，处理 HN-F 对远端数据的读取和写回。
- EP-RNF 保留，负责 inbound local-domain operation，包括 remote read 需要获取本地 dirty data、remote write 需要 invalidate 本地 clean sharers、dirty owner recall 等。
- 不采用 sentinel EP-RNF 作为主要正确性机制。sentinel 可作为后续优化或 debug placeholder，但不应替代 HN-F permission hook。

这条路线与最终多 gem5/外部 UBCC 最兼容：每个 gem5 内部只暴露 EP/UR 接口，跨 gem5 的 global request/response 由外部 UBCC/ns-3 传输；当前单 gem5 版本只是把 outer network 简化成进程内 message queues。

## 6. EP 详细设计

### 6.1 EP 组件划分

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

### 6.2 EP 维护的状态

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
- `grant_state`: `I`, `S`, `E`, `M`, `PendingS`, `PendingE`, `PendingM`, `Revoking`, `WritingBack`。
- `owner_node`: 若 `E` 或 `M`，当前 global owner。
- `sharer_mask`: 若 `S`，clean sharer node bitset。
- `dirty_owner_known`: home 是否已确认 dirty owner。
- `data_version`: optional epoch/version，用于处理 racing invalidation。
- `pending_outer_txns`: 等待 global permission/data 的本地请求列表。

EP-SNF TBE：

- `addr`, `line_addr`, `chi_requestor`, `chi_txn_id`
- `origin_hnf`, `original_chi_type`，若 HN-F hook 能传入则记录 `ReadShared`/`ReadUnique`。
- `needed_perm`: `S`、`E` 或 `M`
- `data_buf`, `received_data_beats`, `expected_data_beats`
- `state`: `WaitGlobalGrant`, `WaitGlobalData`, `SendingCompData`, `WaitWritebackAck`, `Done`

EP-RNF TBE：

- `addr`, `outer_requestor_node`, `outer_txn_id`
- `operation`: `ReadLocalShared`, `ReadLocalUnique`, `InvalidateLocal`, `DowngradeLocal`, `WritebackLocal`
- `issued_chi_req`: `ReadShared`, `ReadUnique`, `CleanUnique`, `MakeReadUnique` 或 snoop response handling。
- `state`: `IssueInnerReq`, `WaitCompData`, `WaitCompAck`, `ReturnOuterData`, `Done`

### 6.3 Global directory 状态机

建议第一版按 MESI 设计 directory 和 message。为了降低首轮 debug 难度，可以通过参数关闭 `GrantE`，让系统以 MSI-compatible mode 运行；但代码中不应硬编码 MSI。

Home UBCC per-line directory entry：

- `state`: `I`, `S`, `E`, `M`, `Busy`
- `owner`: exclusive/dirty owner node id，`E` 或 `M` 有效。
- `sharers`: bitset，只有 `S` 有效。
- `pending`: 当前被序列化的 global transaction。
- `data_location`: `HomeDRAM`, `OwnerNode`, `UBCCBuffer`。
- `data_dirty`: dirty data 是否尚未写回 home DRAM，只有 `M` 需要为 true。

状态转换：

| 当前 | 请求 | 动作 | 下一状态 |
| --- | --- | --- | --- |
| I | `GlobalReadShared(req)` | 从 home DRAM/本地 HN-F 取数据；若启用 E grant，可授予 clean exclusive，否则加入 sharer | E owner=req 或 S |
| I | `GlobalReadUnique(req)` | 从 home DRAM/本地 HN-F 取数据，授予 req owner | M |
| S | `GlobalReadShared(req)` | 发 clean data，加入 sharer | S |
| S | `GlobalReadUnique(req)` | 向所有 sharer 发 invalidate，等待 ack，发 data/permission | M owner=req |
| S | `GlobalEvict(node)` | 移除 sharer；若空则 I | S/I |
| E | `GlobalReadShared(req)` | 通知 E owner downgrade 为 S，给 req 发 clean data，owner 和 req 都成为 sharer | S |
| E | `GlobalReadUnique(owner)` | owner 已独占，无需 invalidate；记录 E->M 或 dirty intent | M owner=owner |
| E | `GlobalReadUnique(req != owner)` | invalidate E owner 或 transfer clean data，授予 req M | M owner=req |
| E | `GlobalEvict(owner)` | owner 丢弃 clean exclusive，无需写回 | I |
| M | `GlobalReadShared(req)` | 向 owner 发 downgrade/recall；owner 返回 data；home 写回或缓存 data；owner 降为 sharer，req 加 sharer | S |
| M | `GlobalReadUnique(req)` | 向 owner 发 transfer/invalidate；owner 返回 data 并失效；req 成 owner | M owner=req |
| M | `GlobalWriteback(owner)` | 接收 data，写回 home local DRAM；owner 清除 | I 或 S |

并发策略：第一版每 line 一个 Busy TBE，其他请求 Nack/Retry 或排队。优先实现排队，便于 deterministic debug。

### 6.4 内侧请求到全局请求的翻译

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
| `ReadShared` miss/hit for CPU | 若 home 为本 node，UBCC 按 global MESI 授予 S 或 E；若其他 remote sharer/owner 存在，按 MESI 处理 | 向 home UBCC 发 `GlobalReadShared`，等待 grant/data 后允许 HN-F 完成 |
| `ReadUnique` / `MakeReadUnique` | 若 home 为本 node，UBCC invalidate 其他 nodes 后允许本地 M；若本 node 已持有 E，只记录 E->M | 向 home UBCC 发 `GlobalReadUnique`，等待所有 remote invalidation 完成；若本 node 已持有 E，可走 E->M 快路径 |
| local clean evict | 更新 home directory sharer bit | 向 home UBCC 发 `GlobalEvict` |
| local dirty writeback | 更新 home data 或通知 owner release | 向 home UBCC 发 `GlobalWriteback(data)` |
| local downgrade due to remote read | EP-RNF/UBCC 触发本地 clean/downgrade，返回 data | 不适用，remote line 的 home 不在本 node |

第一版简化：DSM Local 的本 node CPU 访问也统一经过 UBCC directory，这样本 node 与 remote node 对同一 DSM Local line 的权限序列化一致。

### 6.5 全局请求到本地 CHI 请求的翻译

当 node i 是 line 的 home，其他 node 发来 global request 时，node i UBCC 需要通过 EP-RNF/UR 操作本地 CHI domain。

翻译规则：

| Global request | 本地 CHI 操作 | 说明 |
| --- | --- | --- |
| remote `GlobalReadShared` for DSM Local | EP-RNF 发 `ReadShared` 或 UR 读 local data | 如果本地可能 dirty，需要 HN-F snoop dirty owner 后返回 clean data。 |
| remote `GlobalReadUnique` for DSM Local | EP-RNF 发 `ReadUnique`/`MakeReadUnique` 获得 unique，然后本地降级/失效自身 copy | 需要确保 home node 本地 cache 不再持有冲突权限。 |
| remote invalidates this node as sharer | EP-RNF/DSM-aware HN-F 触发本地 invalidate | 可以通过 HN-F hook 直接让 HN-F snoop本地 RN-F，或 EP-RNF sentinel 被 snoop。 |
| remote downgrade this node as dirty owner | EP-RNF 触发 clean/writeback，取回 data | 返回 data 给 home UBCC，内侧状态降为 S 或 I。 |
| remote read this node as E owner | EP-RNF 或 HN-F sideband 触发 clean downgrade | 本 node 从 global E 变为 S，返回 clean data 或允许 home 从 DRAM/L3 返回。 |

注意：如果 EP-RNF 只是普通 RN-F，它主动发 `ReadShared` 可以获得数据，但“让本地已有 CPU cache 失效/降级”更自然的发起点是 HN-F。计划中优先通过 DSM-aware HN-F hook 实现本地 invalidation/downgrade。

### 6.6 EP-RNF 与 EP-SNF 协同

EP-SNF 的职责：

- 本地 HN-F 对 DSM Remote line 发生 miss 时，提供 data block。
- 对 HN-F 写回 DSM Remote dirty data 时，转成 `GlobalWriteback`。
- 不独自决定 global permission，permission 由 HN-F hook + UBCC 完成。

EP-RNF 的职责：

- 外部 node 请求本 node DSM Local line 时，对本地 HN-F 发起内部操作以获得最新 data。
- 本 node 被 home 要求 invalidation/downgrade 时，协助触发本地 CHI domain 的 snoop/clean。
- 如果采用 sentinel 备选方案，EP-RNF 还需要在 HN-F directory 中代表“外部世界”的 sharer/owner；推荐路线不依赖该职责。

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

### 6.7 Retry、死锁和顺序约束

计划中必须显式处理：

- EP/UBCC 每 line 只允许一个 active transaction，其他请求排队或 retry。
- `REQ/SNP/RSP/DAT` 四个 CHI vnet 不能被 EP 内部等待互相堵住；EP TBE 要有容量上限和 deadlock debug counter。
- Global invalidation 必须在 HN-F 发 unique completion 前完成。
- Dirty owner downgrade 必须携带 data，home directory 才能把 M 转 S。
- EP-SNF 收到 HN-F writeback 后，必须等 home UBCC 接收 data 后再返回完成 ack，或记录 durable pending writeback。
- UBCC-Exclusive metadata access 不能等待一个正在等待该 metadata 的 outer transaction。

## 7. UR 与 Global Directory 设计

### 7.1 UR 角色

UR 不是普通 CPU，也不是外部 node。它是 UBCC 的本地 CHI request agent，用来：

- 读取 DSM PA Local 的实际数据，尤其当 home UBCC 要服务 remote read 时。
- 写回 DSM PA Local 的数据，例如 remote dirty owner 释放后更新 home DRAM/L3。
- 读写 UBCC-Exclusive PA 上的 directory backing store。

实现路线：

- Phase 2.1：实现 `UBCCReaderController`，继承 `CHIGenericController`，支持最小 RN-F read/write transaction。该阶段 directory 仍可使用 UBCC 内部 C++ map，不要求 metadata 真实落 DRAM。
- Phase 2.2：用 UR 读写 UBCC-Exclusive PA 的测试空间，证明 UR 能独立访问本地 CHI/内存，并验证该空间不触发 global permission hook。
- Phase 2.3：给 UR 增加 small SRAM directory cache 和 writeback/evict policy，把 directory backing store 切到 UBCC-Exclusive PA。

UR 资源隔离要求：

- UR 使用独立 controller、独立 TBE 表、独立 trigger queue、独立 retry queue。
- UR 不能复用 EP-RNF 或 EP-SNF 的 transaction table。
- UBCC 发起 UR metadata access 时不得持有会阻塞 EP response 的全局锁。
- UR 只允许访问 DSM Local data 和 UBCC-Exclusive PA；访问 DSM Remote 或 Local Normal PA 应 assert 或 fatal，除非后续阶段显式放开。

### 7.2 Directory 存储

第一版 directory entry：

```text
struct DirEntry {
  Addr line;
  State state;       // I/S/E/M/Busy
  uint32_t sharers;  // N <= 32 first
  int owner;         // valid if E/M
  bool dirty;
  uint64_t epoch;
}
```

SRAM cache 逐出策略：

- 第一版不逐出，容量设大。
- 第二版固定 set-associative，小容量 miss 时从 UBCC-Exclusive PA 读取 entry。
- dirty directory entry 逐出时，UR 对 UBCC-Exclusive PA 执行 writeback。
- directory backing store 不进入 UBCC global protocol，只受本 node CHI normal coherence 保护。

## 8. Subtask 2 阶段划分

### 阶段 2A：多节点地址与配置骨架

目的：在单 gem5 进程中生成 N=3 个逻辑 Node，每个 Node 复用 Subtask 1 的 CHI domain 结构，并在同一 RubySystem 内形成逻辑隔离的 CHI island。

操作步骤：

1. 定义 `NodeConfig`：`node_id`、CPU range、local normal range、DSM local range、UBCC exclusive range、DRAM range。
2. 生成每 node 的 RN-F clusters、HN-F/L3、SN-F/DRAM。
3. 给每 node HN-F 设置 addr ranges：local normal + DSM local + DSM remote + UBCC exclusive，但 DSM remote 的 downstream 指向本 node EP-SNF。
4. 普通 RN-F/HN-F/SN-F 的 `downstream_destinations` 和 snoop destinations 只包含同 node controller。
5. 添加 node_id debug assert：普通 CHI message 不允许跨 node，EP/UR 例外。
6. 注意 gem5 Ruby 通常假设一个 RubySystem；第一版建议在一个 RubySystem 内放所有 logical node controller，用 node_id 参数约束连接和 address map。
7. 添加 outer network skeleton，只连接 UBCC，不参与 Ruby network。

难度：中。

### 阶段 2B：EP-SNF 远端只读数据源原型

目的：证明 DSM Remote miss 可以通过 EP-SNF 从 home node 取数，同时 UR 已具备独立 read/write 能力但 directory 暂不落 DRAM。

操作步骤：

1. 实现 `EP_SNF_Controller` 基于 `CHIGenericController`，能响应 HN-F 的 `ReadNoSnp`。
2. 对 `ReadNoSnp(addr)`，通过 outer message 向 `homeNode(addr).UBCC` 请求 data。
3. home UBCC 第一版可以从内部 fake data map 返回 data；若要真实数据，使用 home EP-RNF/UR 从 local HN-F/DRAM 取数。
4. EP-SNF 用 `CHIDataMsg`/`CHIResponseMsg` 按 `CHI-mem.sm` 期望回复 HN-F。
5. 单独测试 UR 对 UBCC-Exclusive PA 的 read/write，确认不占用 EP 资源，不触发 global coherence。
6. 限制该阶段 workload 为 read-only DSM，避免写权限错误。

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

### 阶段 2D：Home UBCC directory 与 global MESI

目的：实现节点间 MESI 一致性。第一轮可以关闭 E grant，以 MSI-compatible mode 验证；但 directory/message/API 均按 MESI 编写。

操作步骤：

1. 实现 `UBCCController`，维护 per-line directory map。
2. 实现 outer message：`GlobalReadShared`、`GlobalReadUnique`、`GlobalInvalidate`、`GlobalDowngrade`、`GlobalWriteback`、`GlobalEvict`、`GlobalDataResp`、`GlobalAck`、`GlobalRetry`、`GrantS`、`GrantE`、`GrantM`。
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
6. 验证 local E owner 被 remote read 触发时 clean downgrade 为 S。

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
- Node0 read Node1 DSM Local 获得 E 后，Node0 write 只触发 E->M，不向其他 node 发 invalidation。
- Node0 持有 E 时 Node2 read 同一 line，Node0 必须 downgrade 为 S，Node2 获得 S。
- Local Normal PA 不触发任何 UBCC/EP message。
- UBCC-Exclusive PA 只由 UR 访问，不触发 global coherence。
- Dirty shared inside one node 对外表现为 M，remote read 必须使该 node 降级并提供最新 data。

工具：

- Ruby debug flags：`RubyProtocol`、新增 `UBCC`、新增 `ExternalProxy`。
- per-line trace：addr、node、old state、event、new state。
- assert：global directory 不允许 `owner` 与 `sharers` 冲突；不允许两个 node 同时 M；不允许 E owner 与任意 sharer 共存。

难度：高。

## 9. 建议的最小里程碑

1. M1：单节点 CHI C=2/M=2，cluster-shared L2，L3 HN-F，DRAM SN-F 可跑通。
2. M2：N=3 logical node address map 和配置骨架完成，单 RubySystem 内 CHI island 逻辑隔离，Local Normal PA 工作。
3. M3：UR 最小 read/write 能力完成，UBCC-Exclusive PA 不触发 global coherence。
4. M4：DSM Remote read-only 通过 EP-SNF + UBCC fake directory/data 工作。
5. M5：DSM-aware HN-F hook 完成，global S/E/M permission 可阻塞 HN-F completion。
6. M6：global MESI directory 完成，跨节点 read/write ping-pong 正确；可先关闭 E grant 做 MSI-compatible debug。
7. M7：EP-RNF/UR 完整接入，dirty owner recall 和 metadata backing store 正确。
8. M8：压力测试、stats、debug 工具、多 gem5/外部 UBCC 迁移接口文档完善。

## 10. 主要风险

- HN-F hook 改动 SLICC 状态机，容易引入 deadlock 或破坏非 DSM PA 行为。
- EP-SNF 无法从普通 `ReadNoSnp` 推断 original permission，这是架构性风险，必须通过 HN-F hook/增强 EP message 解决；sentinel 不建议作为主要正确性机制。
- 如果选择 Sentinel 为主路线，最大风险是 sentinel registration 与 HN-F completion 的原子性，以及 HN-F directory replacement/evict 后 sentinel 状态丢失。
- 一个 RubySystem 中建多个逻辑 Node 需要小心 MachineID、controller ordering、NetDest、address range 和 node_id 隔离 assert。
- Global directory 与本地 CHI directory 双层目录可能短暂不一致，需要 epoch/TBE 序列化保证 stale response 不生效。
- CHI 内部允许 `SD`，global MESI 不允许多个 dirty shared node；必须在节点边界把 `SD` 折叠成唯一 M owner。
- UR-backed metadata 可能导致协议递归；第一版 directory 可先用内部 map，但 UR read/write path 仍要实现并用独立资源隔离。
- 如果先硬编码 MSI，后续改 MESI 会有中到高重构成本；因此从第一版就要保留 MESI enum/API，即使暂时关闭 E grant。
- Atomic、DVM、DMA、I/O coherent 不是第一版目标，若 workload 依赖这些语义，需要单独阶段支持。

## 11. 推荐决策

建议按以下决策进入后续实现：

1. Subtask 1 固定采用 cluster-shared L2。
2. Subtask 2 当前单 gem5 原型采用一个 RubySystem 内多个逻辑 CHI island；最终迁移到多 gem5 + 外部 UBCC/ns-3。
3. Global protocol 从数据结构和 message 上直接按 MESI 设计，允许首轮关闭 E grant 进行 MSI-compatible debug。
4. EP/HN-F 采用路线 C 的结构化版本：DSM-aware HN-F permission hook + EP-SNF data plane + EP-RNF inbound local-domain operation。
5. 不把 sentinel EP-RNF 作为主要正确性机制，只作为后续可选 debug/placeholder 方案。
6. UR 从早期就实现最小 read/write，但 directory backing store 可以后置。

如果明确选择 Sentinel 为主，则替代决策为：EP-RNF 是所有 hit/upgrade/inbound global operation 的主路径；EP-SNF 只做 DSM Remote miss/fill 和 writeback data plane；HN-F 必须支持 EP-SNF fill 后同步登记 EP-RNF sentinel，且该登记必须早于原始 CPU request completion。
