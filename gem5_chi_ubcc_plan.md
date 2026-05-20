# gem5 Ruby CHI Single Node 与 UBCC 计划

本文档只制定实施计划，不执行 Subtask 1 或 Subtask 2 的代码实现。

本版按以下新决策重写：

- 将 `git@github.com:GCC314/gem5.git` 作为当前 repo 的 `gem5/` submodule 使用。
- 多节点原型中，不同逻辑 CPU/node 的 CHI domain 必须独立，或至少通过配置、路由和断言达到等价独立，普通 CHI message 不允许跨 domain。
- Subtask 2 固定为 `2Core/Node`。链路打通阶段可以只在每个 node 的任意一个 core 上运行有效 payload，另一个 core 空闲或运行最小干扰程序。
- Subtask 2 主路线改为 EP-RNF Sentinel 主导，EP-SNF 负责 DSM Remote miss/fill/writeback 等剩余 data-plane 场景，HN-F 只做最小必要修改。
- UR 不再作为 DSM Local 的独立 RN-F 讨论；DSM Local 上由 UBCC 触发的 coherent local access 合并进 EP-RNF/EP local agent。UBCC-Exclusive PA 在 SE mode 下不被 CPU 访问，第一版用 UBCC 内部 metadata map 即可。

## 0. 当前 gem5 结构观察

已在 `gem5/` submodule 中确认 Ruby CHI 相关入口：

- `configs/ruby/CHI.py` 是 Ruby CHI 系统创建入口，支持 `--chi-config` 注入自定义 CHI 配置模块。
- `configs/ruby/CHI.py` 默认创建 RN-F、MN、HN-F、SN-F、RNI，并把 controller 放入 Ruby topology。
- `configs/ruby/CHI_config.py` 定义默认 CHI node wrapper 和 controller 配置，包括 `CHI_RNF`、`CHI_HNF`、`CHI_SNF_MainMem`、`CHI_RNI_DMA`。
- 默认 `CHI_RNF.generate()` 是每 CPU 一个 RN-F，并通过 `addPrivL2Cache()` 给每 CPU 创建私有 L2；这不满足 cluster-shared L2，需要自定义 RN-F generator。
- `CHI_Node.connectController()` 为 controller 连接 `REQ/SNP/RSP/DAT` 四类 Ruby network message buffer。
- `CHI_RNF.setDownstream()` 只给 RN-F 的 last-level controller 设置 HN-F destination，因此可以通过自定义 RN-F/HN-F destination set 隔离 logical CHI island。
- `CHI_HNF.createAddrRanges()` 根据 `system.mem_ranges` 生成 HN-F address ranges。多 logical domain 共用一个 RubySystem 时，需要避免默认全局 `system.mem_ranges` 让所有 HN-F 覆盖同一组范围。
- `src/mem/ruby/protocol/chi/CHI-cache.sm`、`CHI-cache-actions.sm`、`CHI-cache-transitions.sm` 是 CHI cache/HN-F/RN-F 行为的主要 SLICC 实现。
- `src/mem/ruby/protocol/chi/CHI-mem.sm` 是 CHI SN-F/memory-side controller。
- `src/mem/ruby/protocol/chi/CHI-msg.sm` 定义 CHI request/response/data message type，包括 `ReadShared`、`ReadUnique`、`MakeReadUnique`、`SnpUnique`、`SnpCleanInvalid`、`ReadNoSnp`、`CompData_*`。
- `src/mem/ruby/protocol/chi/generic/CHIGenericController.*` 提供 C++ 级 CHI generic controller，可以直接 send/recv `REQ/SNP/RSP/DAT` message，适合实现 EP-RNF、EP-SNF 和 UBCC 旁路 controller。
- 当前 CHI HN-F directory 结构中已有 sharer/owner/exclusive owner 概念，适合把 EP-RNF 表达为特殊 external sentinel；但仍需要验证具体 directory update action 是否允许安全插入 synthetic RN-F MachineID。

## 1. 已确认约束与当前决策

本版计划按以下约束推进：

- Subtask 1 的 single-node hierarchy 仍保留 `M=2` cluster、`C=2` cores/cluster、cluster-shared L2、node-shared HN-F/L3、SN-F/DRAM。
- Subtask 2 固定为每 logical node 两个 CPU/core。每个 logical node 有自己的 RN-F cluster、HN-F/L3、SN-F/DRAM、EP-RNF、EP-SNF、UBCC。
- Subtask 2 中的独立 CHI domain 粒度是 node，不是单个 core。同一 node 内两个 core 共享本 node CHI coherence；不同 node 之间必须独立或等价独立。
- 链路打通和早期一致性验证阶段可以只在每个 node 的一个 core 上运行有效 payload，另一个 core 不参与共享数据访问。后续再加入同 node 双 core 并发访问测试。
- 第一版优先在单个 gem5 进程中构造多个 logical CHI island；但必须通过 gating spike 证明它与独立 CHI domain 等价。若无法证明，切换到多个 RubySystem 或多个 gem5 进程。
- 普通 CHI message 的 source/destination 必须属于同一 logical domain。跨 domain 交互只能通过 EP/UBCC outer protocol。
- 第一版不支持 DVM、atomic、exclusive monitor、IO coherent DMA。workload 限制为 SE mode 下普通 cacheable load/store 和 cache line 粒度一致性。
- 所有 logical node 使用相同 global PA 语义。Node `i` 的 DSM PA Local 为 `[dsm_base + dsm_size * i, dsm_base + dsm_size * (i + 1))`。整个 DSM PA 为 `[dsm_base, dsm_base + dsm_size * n)`。
- UBCC-Exclusive metadata 在 SE mode 下不映射给普通 CPU。第一版 directory 使用 UBCC 内部 C++ map，不需要通过 CHI 访问 metadata backing store。
- DSM Local 上的 UBCC 数据访问不再由独立 UR 完成，而由 EP-RNF/EP local agent 承担。原因是这类访问本质上对应一次 global coherence request，需要观察或改变本地 CHI cache 状态。
- Global protocol 从数据结构和 message 上按 MESI 设计，但第一版可以关闭 `GrantE`，以 MSI-compatible mode debug。

## 2. 总体可行性与难度

Subtask 1 可行性高，难度中等。gem5 已有 CHI Ruby 协议和 `--chi-config` 注入机制。主要工作是自定义 node generator，使 C=2、M=2 的 cluster 结构明确表达为每 cluster 一个 RN-F wrapper、多个 core L1、一个 cluster-shared L2、node-shared HN-F/L3 和 SN-F。

Subtask 2 可行性中等，难度高到很高。新路线避免在 HN-F 每条权限路径上插入全局 permission hook，但不是零 HN-F 修改。正确性依赖 EP-RNF sentinel 在 HN-F directory 中的同步登记、持久维护和 snoop 响应。

推荐路线改为：

- EP-RNF Sentinel 是 correctness 主路径，代表“外部世界”出现在本地 HN-F directory 中。
- EP-RNF 同时承担 DSM Local coherent local access agent，用于 UBCC inbound request 对本地 cache/domain 的 read、downgrade、invalidate、dirty recall。
- EP-SNF 负责 DSM Remote miss/fill data plane、remote writeback data plane，以及首 miss 时向 UBCC 请求初始 global grant/data。
- HN-F 最小修改聚焦在 sentinel registration、sentinel state update、必要的 synthetic RN-F directory entry 支持，而不是给所有 HN-F request path 增加 global permission hook。
- `ExternalOwner` 是必须支持的 sentinel 状态，不作为可选优化。它对应“外部 node 已获得独占写权限，本 node 真 CPU 已被 invalidate，而本 node HN-F 需要把外部世界看作当前 owner”的场景。

难度变化：

| 部分 | 可行性 | 难度 | 说明 |
| --- | --- | --- | --- |
| Single-node CHI cluster-shared L2 | 高 | 中 | 配置层为主。 |
| Logical CHI domain 隔离 | 中 | 中到高 | 需要验证 RubySystem、MachineID、NetDest、addr_ranges 是否能做到等价独立。 |
| EP-RNF Sentinel | 中 | 高 | 对 HN-F 侵入较少，但 sentinel 登记和状态一致性是核心风险。 |
| EP-SNF remote fill | 中 | 中到高 | 可以先保守获取 M grant，避免首版依赖 HN-F 原始请求语义；后续必须恢复 GrantS/read-sharing。 |
| Global MESI directory | 中 | 高 | 需要处理 owner/sharer、dirty recall、remote invalidation。 |
| 多 gem5 + 外部 UBCC/ns-3 | 中 | 很高 | 后续迁移，需要同步、序列化和死锁调试。 |

## 3. Subtask 1：Single Node Cache Coherence Architecture

### 3.1 CHI 抽象对应关系

| 目标组件 | gem5/Ruby CHI 对应 | 说明 |
| --- | --- | --- |
| Core L1I/L1D | `CHI_L1Controller` + `RubySequencer` | RN-F 内部的私有 L1；L1I/L1D 分别绑定 CPU instruction/data port。 |
| Cluster L2 | `CHI_L2Controller` | 每 cluster 一个共享 L2。需要新增 shared L2 generator。 |
| CPU Cluster | `CHI_RNF` 的扩展实例 | 一个 RN-F wrapper 包含 C 个 CPU 的 L1 和一个共享 L2。 |
| Node 共享 L3 | `CHI_HNFController` 的 `cache` | HN-F 兼任 home agent、目录和 L3 cache。 |
| Home Agent | `CHI_HNFController` | 处理 RN-F 请求、维护本 CHI domain directory、向 RN-F 发 snoop。 |
| DRAM Memory Controller | `CHI_SNF_MainMem` + gem5 `MemCtrl` | SN-F 是 CHI 内 memory-side 节点，背后连接 DRAM/SimpleMemory。 |
| CHI interconnect | Ruby network，4 个 vnet | `REQ/SNP/RSP/DAT` 对应 vnet 0/1/2/3。 |

### 3.2 单节点目标拓扑

默认参数：`M=2` cluster，`C=2` cores/cluster，总 CPU 数 `4`。

```text
CPU0 L1I/L1D --\
CPU1 L1I/L1D ---- Cluster0 shared L2 --\
                                             HN-F + shared L3 -- SN-F -- DRAM
CPU2 L1I/L1D ---- Cluster1 shared L2 --/
CPU3 L1I/L1D --/
```

Subtask 1 只验证单 node 内 coherence，不代表 Subtask 2 中不同 logical node 的 CPU 会共享同一 CHI domain。

### 3.3 脚本和文件规划

第一阶段尽量不改 SLICC 协议，只增加配置层：

- 新增 `configs/ruby/CHI_single_node_config.py`：继承 `CHI_config.py` 的 class，覆盖 `CHI_RNF.generate()`，按 cluster 分组 CPU。
- 新增 `ClusterCHI_RNF`：构造 C 个 CPU 的 L1I/L1D，然后创建一个共享 `CHI_L2Controller`。
- 将每个 core 的 L1I/L1D `downstream_destinations` 指向 shared L2。
- 将 shared L2 作为该 RN-F 的 network-side last-level controller，并使 shared L2 downstream 指向本 node HN-F。
- 新增 `configs/example/ubcc/chi_single_node.py` 或复用现有 example config：设置 `--ruby --network=garnet|simple --ruby-protocol=CHI --chi-config=... --num-cpus=4 --num-l3caches=1 --num-dirs=1`。

### 3.4 验证计划

验证用例：

- 单 core load/store，确认能从 DRAM 取数并命中 L1/L2/L3。
- 两 core 同 cluster true sharing/false sharing，确认 shared L2 行为正常。
- 跨 cluster ping-pong store/load，确认 HN-F 发送 snoop 并维护一致性。
- Ruby random tester 或 directed tester，覆盖 clean shared、unique dirty、evict/writeback。

观测指标：

- Ruby stats 中各 controller request、snoop、response、data message 数量。
- `RubyProtocol` debug log 中 `ReadShared`、`ReadUnique`、`SnpUnique`、`WriteBackFull` 路径。
- 最终程序输出正确，且无 deadlock。

## 4. Subtask 2：独立 Logical CHI Domain 设计

### 4.1 Domain 独立性目标

Subtask 2 的关键目标不是“在一个 Ruby network 中多放几个 controller”，而是让不同 logical node 的 CHI domain 行为等价于独立 gem5 实例。每个 logical node 固定包含 2 个 core；早期 payload 可以只使用其中一个 core。

每个 logical domain 包含：

- 本 node 的 CPU/RN-F cluster。
- 本 node 的 HN-F/L3。
- 本 node 的 SN-F/DRAM。
- 本 node 的 EP-RNF Sentinel。
- 本 node 的 EP-SNF。
- 本 node 的 UBCC。

隔离规则：

- RN-F downstream 只包含同 node 的 HN-F。
- HN-F downstream 对 Local Normal 和 DSM Local 指向同 node SN-F。
- HN-F downstream 对 DSM Remote 指向同 node EP-SNF。
- HN-F snoop destination 只允许同 node RN-F 和同 node EP-RNF Sentinel。
- 普通 CHI `REQ/SNP/RSP/DAT` 不允许跨 node。
- EP/UBCC outer message 是唯一跨 node 通路。
- 每个 controller 增加 `node_id/domain_id` debug 信息，所有普通 CHI send path 增加 assert 或 debug checker。

### 4.2 单 RubySystem 与多 RubySystem 选择

优先做单 RubySystem、多 logical island，因为它更容易复用当前 gem5 配置入口和 CPU port binding。但该方案必须先通过 gating spike：

- 验证每个 logical HN-F 的 `addr_ranges` 可以被配置成只服务本 node 视图，不被全局 `system.mem_ranges` 误合并。
- 验证 `NetDest` 和 `downstream_destinations` 足以约束普通 CHI message 不跨 node。
- 验证 HN-F snoop 只发给本 node RN-F/EP-RNF。
- 验证相同 DSM global PA 在不同 logical node 中不会被 Ruby 全局路由到错误 HN-F。
- 运行 Local Normal PA traffic，确认 Node0 不产生任何发往 Node1 controller 的普通 CHI message。

如果上述任一项无法可靠保证，则切换方案：

- 中期切到多个 RubySystem，每个 logical node 一个 `system.ruby_node[i]`。
- 或提前切到多个 gem5 进程 + 外部 UBCC/ns-3。

### 4.3 地址空间规划

| Range | Serve path | 说明 |
| --- | --- | --- |
| Local Normal PA | 本 node HN-F -> 本 node SN-F/DRAM | 节点私有普通内存，不进 UBCC。 |
| DSM PA Local | 本 node HN-F -> 本 node SN-F/DRAM；本 node UBCC 是 global home | 其他节点访问时经过本 node UBCC。 |
| DSM PA Remote | 本 node HN-F -> 本 node EP-SNF -> remote home UBCC | 本 node cache 可缓存，外部状态由 home UBCC 管理。 |
| UBCC-Exclusive metadata | UBCC 内部 map，第一版不走 CHI | SE mode 下不映射给 CPU，不进入 global protocol。 |

DSM global PA 固定切片：

```text
DSM_GLOBAL = [dsm_base, dsm_base + dsm_size * n)
DSM_LOCAL(i) = [dsm_base + dsm_size * i,
                dsm_base + dsm_size * (i + 1))
DSM_REMOTE(i) = DSM_GLOBAL - DSM_LOCAL(i)
homeNode(addr) = floor((addr - dsm_base) / dsm_size)
```

建议实现 `UbccAddressMap`：

- `isLocalNormal(addr, node_id)`
- `isDsm(addr)`
- `homeNode(addr)`
- `isDsmLocal(addr, node_id)`
- `isDsmRemote(addr, node_id)`
- `isUbccExclusive(addr, node_id)`
- `lineAddr(addr)`

## 5. EP-RNF Sentinel 主路线

### 5.1 核心思想

EP-RNF 是本 node HN-F directory 中的特殊 RN-F，代表“外部世界”。当外部 node 可能持有某条 DSM line 的 clean copy、exclusive copy 或 dirty owner 权限时，本地 HN-F directory 中必须同步登记 EP-RNF sentinel。

本地 CPU 后续对该 line 的 read/upgrade/write 会走 HN-F 既有 snoop 机制。HN-F snoop EP-RNF 时，EP-RNF 把 snoop 翻译成 UBCC global operation。EP-RNF 只有在 UBCC 完成 remote invalidation、downgrade 或 owner transfer 后，才向 HN-F 返回 snoop response。这样可以避免在 HN-F 每条权限路径显式插入 global permission hook。

### 5.2 组件职责

| 组件 | 推荐实现 | 职责 |
| --- | --- | --- |
| EP-RNF Sentinel | 基于 `CHIGenericController` | 作为特殊 RN-F 进入本地 HN-F directory；响应 HN-F snoop；处理 UBCC inbound local-domain operation；承担 DSM Local coherent access agent。 |
| EP-SNF | 基于 `CHIGenericController` | 响应本 node HN-F 对 DSM Remote 的 `ReadNoSnp`/writeback；向 home UBCC 获取 data/grant；触发或携带 sentinel registration。 |
| UBCC | C++ SimObject 或 EP wrapper 内部模块 | 维护 home directory；处理 outer MESI request；调度 EP-RNF/EP-SNF。 |
| Outer Network | 第一版 fixed-latency queues | 连接 N 个 UBCC，后续替换为 ns-3 或外部网络。 |
| Metadata Store | 第一版 C++ map | SE mode 下不走 CHI，不被 CPU 访问。 |

UR 调整：

- 不再为 DSM Local 单独实现 UR。
- UBCC 需要读取、降级、失效本地 DSM Local cache line 时，通过 EP-RNF 进入本地 HN-F。
- EP-RNF 既是 sentinel，又是 UBCC 对本地 CHI domain 的 coherent local access agent。
- UBCC-Exclusive metadata 第一版为内部 map；如果后续必须评估 metadata DRAM backing，再新增 `MetadataAgent`，但它不参与 DSM coherence 正确性主路径。

### 5.3 Sentinel 状态

| Sentinel 状态 | HN-F directory 表达 | 语义 |
| --- | --- | --- |
| None | EP-RNF 不在 directory | 外部世界对该 line 没有需要本 HN-F 感知的状态。 |
| ExternalSharer | EP-RNF 是 sharer | 外部 node 可能有 clean shared copy；本地获取 unique 前必须 snoop EP-RNF。 |
| ExternalOwner | EP-RNF 是 owner/unique holder | 外部 node 可能有 E/M 或最新数据；本地读写必须通过 EP-RNF/UBCC 召回或转移权限。 |
| ExternalPending | EP-RNF 有 transient TBE | 正在等待 UBCC/outer network，相关 HN-F transaction 必须阻塞或 retry。 |

约束：

- `ExternalOwner` 不允许与本地 CPU dirty owner 同时存在。
- `ExternalSharer` 可以与本地 clean sharers 共存。
- `ExternalOwner` 的典型进入场景是 remote `GlobalReadUnique` 成功后，本 node EP-RNF 对本地发起 `ReadUnique` 或等价 local operation，使本 node 真 CPU cache 被 invalidate，并让 HN-F directory 中 EP-RNF 成为 owner。
- `ExternalOwner` 表示“外部世界 owner”，不是 EP-RNF 本地 cache 真的持有普通 CPU 可访问的数据。EP-RNF 必须能在 HN-F 要求 data/response 时向 UBCC/remote owner 取回或转发。
- sentinel registration 必须早于原始 CPU request completion。
- sentinel removal 必须晚于 UBCC directory 确认外部 sharer/owner 已清空。
- 如果 HN-F directory entry 被清除，必须同步通知 EP/UBCC 或禁止清除仍有外部状态的 sentinel entry。

### 5.4 HN-F 最小修改范围

该路线仍需要修改 HN-F，但修改面应限制在以下几类：

1. Sentinel registration API。允许 EP-SNF 或 EP-RNF 请求 HN-F 在某条 line 的 directory 中插入、更新或删除 EP-RNF MachineID。
2. DSM Remote fill side effect。HN-F 从 EP-SNF 收到 DSM Remote data 后，必须在给原始 RN-F completion 前完成对应 sentinel registration。
3. Synthetic RN-F directory state。HN-F directory 需要能表达 EP-RNF 作为 `ExternalSharer` 或 `ExternalOwner`，并在后续 local upgrade/read path 中按普通 sharer/owner 被 snoop。
4. Sentinel-preserving directory maintenance。HN-F 不得在 UBCC 仍认为外部状态存在时静默删除 sentinel。
5. Debug/assert。非 DSM PA 不允许登记 sentinel；Local Normal PA 不允许触发 EP/UBCC；普通 CHI message 不允许跨 domain。

明确不做的修改：

- 不在 HN-F 每个 `ReadShared`、`ReadUnique`、`MakeReadUnique`、hit、upgrade、writeback path 上插入全局 permission hook。
- 不把 EP-SNF 的普通 `ReadNoSnp` 伪装成完整 permission protocol。
- 不让 UBCC 直接绕过本地 HN-F 修改 CPU cache 状态。

### 5.5 EP-SNF 的保守首版策略

EP-SNF 只看普通 `ReadNoSnp` 时无法知道原始请求是 `ReadShared` 还是 `ReadUnique`。为降低 HN-F 首轮改动，第一版采用保守策略：

- 对 DSM Remote first miss，EP-SNF 默认向 home UBCC 请求 global M-like ownership，或请求 `GrantM`。
- HN-F 仍可按本地 CHI 语义给 CPU `SC/UC/UD` 等状态，但 global directory 认为本 node 是唯一 owner。
- 后续其他 node 请求该 line 时，home UBCC 联系 owner node 的 EP-RNF，由 EP-RNF 通过本地 HN-F 召回或降级。
- 该策略正确但牺牲读共享性能，适合作为 M4/M5 debug 模式。

该策略只能作为临时 bring-up 模式。计划中必须在阶段 2G 恢复 read-sharing 能力：增加最小 HN-F->EP-SNF sideband，携带 `original_chi_req` 或 `needed_perm=S/M`，使 read-only miss 能获得 `GrantS` 并登记 `ExternalSharer`。M8 之后的正确性和性能评估不得继续依赖“所有 DSM Remote miss 保守 GrantM”。

## 6. 关键协议流程

### 6.1 Remote node 读取本 node DSM Local

1. Node1 CPU 读 Node0 DSM Local，Node1 HN-F miss 到 Node1 EP-SNF。
2. Node1 EP-SNF 向 Node0 UBCC 发送 `GlobalReadShared` 或首版保守 `GlobalReadUnique`。
3. Node0 UBCC 查询 home directory。
4. 如果 Node0 本地 cache 可能有 dirty/unique copy，Node0 UBCC 通过 Node0 EP-RNF 对 Node0 HN-F 发起 local read/downgrade/recall。
5. Node0 HN-F 用普通 CHI snoop 本地 RN-F，取得最新 data 或 clean 状态。
6. Node0 EP-RNF 返回 data/ack 给 Node0 UBCC。
7. Node0 UBCC 更新 global directory，把 Node1 加为 sharer 或 owner。
8. Node0 HN-F directory 中同步登记 Node0 EP-RNF sentinel，表示外部世界已持有该 line。
9. Node0 UBCC 返回 grant/data 给 Node1 EP-SNF。
10. Node1 EP-SNF 返回 data 给 Node1 HN-F，Node1 HN-F 按 grant 结果更新 requester 侧状态。
11. 如果 Node1 获得 `GrantS`，Node1 HN-F 应登记 Node1 EP-RNF `ExternalSharer`，使 Node1 后续本地 upgrade 会先通知外部 sharers。
12. 如果 Node1 获得保守 `GrantM`，Node1 是 global owner，不应把 Node1 EP-RNF 登记为 `ExternalOwner`；该 ownership 应记录在 Node1 EP/UBCC state 中，后续 remote request 由 home UBCC 联系 Node1 EP-RNF 召回。
13. Node1 HN-F 完成本地 CPU request。

### 6.2 本 node 写入已有 remote sharer 的 DSM Local

1. Node0 CPU 对 DSM Local line 发 `ReadUnique` 或 `MakeReadUnique`。
2. Node0 HN-F directory 中已有 Node0 EP-RNF `ExternalSharer` sentinel。
3. Node0 HN-F 按普通 CHI 规则向 EP-RNF 发 `SnpUnique` 或 `SnpCleanInvalid`。
4. Node0 EP-RNF 收到 snoop 后向 Node0 UBCC 发起 global invalidation。
5. Node0 UBCC 向所有 remote sharer 发 `GlobalInvalidate`。
6. remote node 通过各自 EP-RNF/HN-F 失效本地 copy，并返回 ack。
7. Node0 UBCC 确认所有 ack 后，更新 directory 为 Node0 owner。
8. Node0 EP-RNF 返回 snoop response 给 Node0 HN-F。
9. Node0 HN-F 完成本地 unique grant。

### 6.3 Remote owner 存在时本 node 读取 DSM Local

1. Node0 CPU 读 DSM Local line。
2. Node0 HN-F directory 中 EP-RNF 是 `ExternalOwner`。
3. Node0 HN-F snoop EP-RNF。
4. EP-RNF 请求 Node0 UBCC 从 remote owner 召回或 downgrade。
5. remote owner 通过其 EP-RNF/HN-F 返回 data，并降级为 S 或 I。
6. Node0 UBCC 更新 global directory。
7. EP-RNF 把 data/ack 返回 Node0 HN-F。
8. Node0 HN-F 按普通 CHI 规则给本地 CPU 返回 data。

### 6.3.1 Remote exclusive write 使本 node 进入 ExternalOwner

1. Node2 对 Node0 DSM Local 发起 `GlobalReadUnique`。
2. Node0 UBCC 判断 Node0 本地可能有 CPU sharer/owner，需要让本地 copy 失效。
3. Node0 UBCC 指示 Node0 EP-RNF 对 Node0 HN-F 发起 `ReadUnique` 或等价 local unique operation。
4. Node0 HN-F 通过普通 CHI snoop 使 Node0 真 CPU cache invalidate，并把 EP-RNF 记录为 owner。
5. Node0 EP-RNF 向 Node0 UBCC 返回 data/ack。
6. Node0 UBCC 授予 Node2 global M。
7. Node0 HN-F 中 Node0 EP-RNF 处于 `ExternalOwner`，表示本 node 的本地 CHI domain 看到“外部世界”是 owner。
8. 后续 Node0 CPU 再读该 line 时，HN-F snoop EP-RNF，EP-RNF 通过 UBCC 从 Node2 召回或 downgrade。

### 6.4 DSM Remote first miss

1. Node1 CPU 访问 Node0 DSM Local，也就是 Node1 DSM Remote。
2. Node1 HN-F miss，downstream 目标为 Node1 EP-SNF。
3. Node1 EP-SNF 向 Node0 UBCC 请求 grant/data。
4. 首版保守策略下，Node1 EP-SNF 请求 `GrantM`；优化版可根据 sideband 请求 `GrantS`。
5. Node0 UBCC 完成 global directory transaction 后返回 data/grant。
6. Node1 EP-SNF 返回 CHI data/response 给 Node1 HN-F。
7. Node1 HN-F 在 completion 前按 grant 结果更新 requester 侧状态：`GrantS` 登记 Node1 EP-RNF `ExternalSharer`，保守 `GrantM` 只记录 Node1 已是 global owner，不登记冲突的 `ExternalOwner`。
8. Node1 HN-F 完成原始 CPU request。

### 6.5 DSM Remote writeback/evict

1. Node1 HN-F 对 DSM Remote dirty line 发生 writeback/evict。
2. Node1 HN-F downstream 到 Node1 EP-SNF。
3. Node1 EP-SNF 转成 `GlobalWriteback` 或 `GlobalEvict` 给 Node0 UBCC。
4. Node0 UBCC 更新 home directory/data。
5. Node1 EP-SNF 在 UBCC ack 后回复 HN-F。
6. 若该 line 不再有外部状态，相关 sentinel 可删除。

## 7. Global Directory 与 MESI

Home UBCC per-line directory entry：

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

第一版策略：

- 数据结构保留 `E`，但默认关闭 `GrantE`。
- `ReadShared` 可以授予 `S`，但若 EP-SNF 无法获得原始请求语义，则 DSM Remote first miss 可保守授予 `M`。
- 保守 `GrantM` 仅用于 M5/M6 bring-up。阶段 2G 必须恢复 `GrantS` read-sharing，不允许把该保守模式作为最终方案。
- 每 line 一个 active global transaction，其他请求排队。
- 不实现 dynamic home migration。
- 不做 directory backing store，直接使用 UBCC C++ map。

状态映射：

| 节点内 CHI 可能状态 | 对外 global summary | 说明 |
| --- | --- | --- |
| 无本地 cache | I | 本节点不是 sharer/owner。 |
| clean shared | S | 可与其他 node 共享。 |
| unique clean | E 或 M-like owner | 第一版可按 M-like owner 处理，后续开启 E。 |
| unique dirty | M | 全局唯一 dirty owner。 |
| shared dirty | M | 节点内可 SD，但对外必须表现为唯一 dirty owner。 |
| HN-F/L3 clean copy only | S 或 I | 第一版按 S 保守处理，避免丢失可服务 data 的 clean copy。 |

## 8. Revised Subtask 2 阶段划分

### 阶段 2A：Domain 隔离 spike

目的：证明单 RubySystem 多 logical CHI island 可以等价于独立 CHI domain。

操作步骤：

1. 定义 `NodeConfig`：`node_id`、2 个 CPU/core、local normal range、DSM local range、DSM remote view、DRAM range。
2. 每 node 生成独立 RN-F、HN-F、SN-F controller set。早期测试只让每 node 的一个 core 跑有效 payload。
3. 每 node RN-F downstream 只指向本 node HN-F。
4. 每 node HN-F downstream 只指向本 node SN-F 或本 node EP-SNF。
5. 增加 node_id debug assert，普通 CHI message 不允许跨 node。
6. 运行 Local Normal PA only workload，验证各 node 互不产生普通 CHI message。
7. 若无法保证隔离，切换多 RubySystem 或多 gem5 方案。

难度：中到高。

### 阶段 2B：EP-RNF/EP-SNF skeleton

目的：实现 EP controller 基础收发能力，不追求完整一致性。

操作步骤：

1. 基于 `CHIGenericController` 实现 `EP_RNF_Controller` skeleton。
2. 基于 `CHIGenericController` 实现 `EP_SNF_Controller` skeleton。
3. EP-RNF 能接收 HN-F snoop 并返回固定 response，用于验证 HN-F 可把 EP-RNF 当 RN-F 目标。
4. EP-SNF 能响应 HN-F `ReadNoSnp`，先返回 fake data。
5. 增加 EP/UBCC outer fixed-latency queue skeleton。

难度：中。

### 阶段 2C：Sentinel registration 最小 HN-F 修改

目的：让 HN-F directory 能同步登记 EP-RNF sentinel，并在本地 upgrade/read 时 snoop EP-RNF。

操作步骤：

1. 定义 EP-RNF synthetic MachineID 和 `node_id`。
2. 给 HN-F 增加 sentinel registration action/API：insert/update/remove。
3. 支持 `ExternalSharer` 和 `ExternalOwner` 两种稳定表达。
4. 确保 registration 发生在相关 CPU request completion 前。
5. 添加断言：Local Normal PA 不允许 sentinel；ExternalOwner 不允许与本地 dirty owner 共存。
6. 构造测试：手工登记 sentinel 后，本地 CPU `ReadUnique` 必须触发 HN-F snoop EP-RNF。

难度：高。

### 阶段 2D：EP-SNF DSM Remote first miss

目的：让 DSM Remote miss 通过 EP-SNF 从 home UBCC 获取 data，并触发必要 sentinel registration。

操作步骤：

1. EP-SNF 处理 DSM Remote `ReadNoSnp`。
2. EP-SNF 向 home UBCC 请求 data/grant。
3. 首版保守请求 `GrantM`，避免依赖 HN-F original request sideband。
4. EP-SNF 返回 CHI data/response 给本地 HN-F。
5. HN-F 在 completion 前完成本 node EP-RNF sentinel 或 owner-state 记录。
6. 只运行 read-only 或 single-owner DSM test。

难度：中到高。

### 阶段 2E：UBCC directory 与 EP-RNF coherent local access

目的：用 EP-RNF 替代独立 UR，处理所有 DSM Local 上由 global request 引发的本地 coherent access。

操作步骤：

1. 实现 `UBCCController` directory map。
2. 实现 outer message：`GlobalReadShared`、`GlobalReadUnique`、`GlobalInvalidate`、`GlobalDowngrade`、`GlobalWriteback`、`GlobalEvict`、`GlobalDataResp`、`GlobalAck`、`GlobalRetry`、`GrantS`、`GrantE`、`GrantM`。
3. EP-RNF 能对本地 HN-F 发起 local read/downgrade/invalidate/recall 操作。
4. Home UBCC grant remote S/M 后，同步在 home HN-F 登记 EP-RNF sentinel。
5. HN-F snoop EP-RNF 时，EP-RNF 能阻塞等待 UBCC 完成 global invalidation/recall，再返回 snoop response。
6. 验证 remote read local dirty line 可获得最新 data。
7. 验证 local write 已有 remote sharer 的 line 会先 invalidate remote sharer。

难度：高到很高。

### 阶段 2F：Writeback、evict 与 dirty owner transfer

目的：补齐多节点 read/write ping-pong 正确性。

操作步骤：

1. 实现 DSM Remote dirty writeback -> EP-SNF -> home UBCC。
2. 实现 DSM Remote clean evict -> home UBCC sharer removal。
3. 实现 dirty owner recall：owner EP-RNF/HN-F 返回 data 并降级/失效。
4. 实现 owner transfer：M owner 从 node A 转到 node B。
5. 增加 per-line epoch，丢弃 stale outer response。
6. 增加 deadlock counter 和 per-line trace。

难度：高。

### 阶段 2G：恢复 GrantS/read-sharing

目的：移除“DSM Remote first miss 一律保守 GrantM”的 bring-up 限制，让 read-only DSM 可以跨 node 共享。

操作步骤：

1. 在 HN-F 到 EP-SNF 的 DSM Remote fill path 增加 minimal sideband，至少携带 `original_chi_req` 或 `needed_perm=S/M`。
2. EP-SNF 根据 sideband 向 home UBCC 请求 `GlobalReadShared` 或 `GlobalReadUnique`。
3. Home UBCC 对 read-only miss 授予 `GrantS`，维护 sharer mask。
4. Requester HN-F 在 `GrantS` completion 前登记本 node EP-RNF `ExternalSharer`。
5. Requester node 后续本地 upgrade 必须 snoop EP-RNF，并由 EP-RNF 触发 global invalidation。
6. 增加多 reader single writer 测试，证明多个 node 可同时持有 S，写者能失效所有 sharer。
7. M8 之后所有 correctness/performance 测试默认启用 GrantS；保守 GrantM 只能作为 debug flag。

难度：中到高。

### 阶段 2H：Metadata 与多 gem5 迁移准备

目的：在 correctness 主路径稳定后，再处理 metadata backing 和外部网络迁移。

操作步骤：

1. 保持 SE mode 下 UBCC metadata 不映射给 CPU。
2. 第一版 directory metadata 使用 C++ map。
3. 如需容量模型，再增加 UBCC SRAM directory cache。
4. 如需 backing store，再设计独立 `MetadataAgent`，但不与 DSM Local EP-RNF 状态混合。
5. 抽象 outer message ABI，为多 gem5 + ns-3 准备序列化格式。
6. 设计跨 gem5 时间推进和阻塞请求调试机制。

难度：中到高。

## 9. 验证计划

核心验证用例：

- Domain isolation：Node0 Local Normal PA traffic 不产生任何 Node1 普通 CHI message。
- Sentinel registration：手工登记 `ExternalSharer` 后，本地 `ReadUnique` 必须 snoop EP-RNF。
- ExternalOwner：remote exclusive write 后，本 node 真 CPU cache 被 invalidate，EP-RNF 成为 HN-F owner；本 node 后续 read 会 snoop EP-RNF 并从 remote owner 召回。
- Remote read：Node0 read Node1 DSM Local，Node1 HN-F/UBCC 登记 external sentinel。
- Local write after remote read：Node1 write 同一 DSM Local，Node0 必须先被 invalidate。
- GrantS/read-sharing recovery：Node0 和 Node2 同时 read Node1 DSM Local 后都持有 S，Node0 write 时 Node2 必须被 invalidate。
- Remote dirty owner recall：Node0 write Node1 DSM Local 后，Node2 read 必须看到 Node0 写入，并触发 Node0 downgrade/writeback。
- 三节点 ping-pong：每 line 任意时刻最多一个 global M owner。
- Local Normal PA：不触发 UBCC/EP message。
- UBCC metadata：SE mode 下不被 CPU 访问，不触发 CHI/global coherence。
- Dirty shared inside one node：对外表现为 M，remote read 必须使该 node 降级并提供最新 data。

工具：

- Ruby debug flags：`RubyProtocol`、新增 `UBCC`、新增 `ExternalProxy`、新增 `Sentinel`。
- per-line trace：addr、node、old state、event、new state、epoch。
- assert：global directory 不允许两个 node 同时 M；不允许 E owner 与 sharers 共存；ExternalOwner 不允许与本地 dirty owner 共存；普通 CHI message 不允许跨 domain。

## 10. 主要风险

- 单 RubySystem 多 logical island 可能无法完全等价于独立 CHI domain，特别是相同 global PA、多 HN-F addr_ranges 和 NetDest 路由之间可能有隐藏耦合。
- Sentinel registration 必须与 HN-F completion 保持顺序；若先完成 CPU request 再登记 sentinel，会出现本地 CPU 立即 upgrade 而外部状态不可见的窗口。
- EP-SNF 只看 `ReadNoSnp` 无法判断原始请求语义。首版保守 `GrantM` 正确但性能差；优化版需要增加 minimal sideband。
- EP-RNF 被 HN-F snoop 后可能等待 outer UBCC round trip，必须有独立 TBE、retry/timeout 和 deadlock debug counter。
- HN-F directory 中 synthetic EP-RNF owner/sharer 的插入、删除和持久性是 correctness 核心，必须重点测试。
- `ExternalOwner` 与 CHI owner/data forwarding 语义贴近，但实现风险高；必须验证 HN-F 对 owner snoop 时 EP-RNF 可以返回符合预期的 response/data。
- Global directory 与本地 HN-F directory 是双层目录，短暂不一致必须由 per-line Busy/epoch/TBE 序列化吸收。
- CHI 内部允许 `SD`，global MESI 不允许多个 dirty shared node；节点边界必须把 `SD` 折叠成唯一 M owner。
- 如果后续启用 `GrantE`，silent E->M 可能不通知 UBCC。建议 E owner 被 remote 请求时总是联系 owner，不依赖及时 E->M 通知。
- Atomic、DVM、DMA、I/O coherent 不是第一版目标，若 workload 依赖这些语义，需要单独阶段支持。

## 11. 建议的最小里程碑

1. M0：`gem5/` submodule 完成，确认 CHI.py、CHI_config.py、CHIGenericController 接口。
2. M1：单节点 CHI C=2/M=2，cluster-shared L2，L3 HN-F，DRAM SN-F 可跑通。
3. M2：N=3 logical node domain isolation spike 通过；普通 CHI message 不跨 domain。
4. M3：EP-RNF/EP-SNF skeleton 可作为 CHI controller 收发消息。
5. M4：HN-F sentinel registration 最小修改完成；本地 upgrade 可 snoop EP-RNF。
6. M5：DSM Remote first miss 通过 EP-SNF + UBCC fake/real data 工作；首版可保守 GrantM。
7. M6：UBCC directory + EP-RNF coherent local access 完成，remote read/write ping-pong 正确。
8. M7：dirty owner recall、writeback、evict、三节点压力测试正确。
9. M8：GrantS/read-sharing 恢复完成，保守 GrantM 仅保留为 debug flag。
10. M9：metadata 容量模型、多 gem5/外部 UBCC/ns-3 迁移接口文档完善。

## 12. 仍需确认或决策的选项

1. 单 RubySystem 多 island 的 gating spike 是否通过。若不通过，应尽早切换多 RubySystem 或多 gem5。
2. EP-SNF first miss 首版固定保守 `GrantM`，但该模式只能用于 M5/M6 bring-up；M8 必须恢复 GrantS/read-sharing。
3. HN-F->EP-SNF minimal sideband 的具体字段：至少需要 `original_chi_req` 或 `needed_perm`，是否还需要 `requestor`、`epoch`、`txn_id` 需要实现前确认。
4. EP-RNF 对本地 HN-F 发起 invalidate/downgrade 的具体 CHI 操作序列，需要在实际 SLICC transition 中确认。
5. `ExternalOwner` 的 response/data forwarding 规则需要在 HN-F owner snoop path 中确认。
6. HN-F directory 中 sentinel entry 的删除策略：由 UBCC 显式删除，还是随 global directory 状态变化同步删除。
7. UBCC metadata 是否长期保持 C++ map，还是后续必须建模 SRAM capacity/backing store。
8. 多 gem5 迁移时 outer protocol 的时间模型：fixed latency、Garnet、ns-3，或外部进程 queue。
