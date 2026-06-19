# Dual-Socket HN-F / EP-SNF Implementation Checklist v4.0

**状态**：最终版（Q1-Q5 已确认）  
**目标**：将当前“每节点单 HN-F / 单 EP-SNF”实现，收敛为“每节点双 HN-F / 双 EP-SNF / 单 EP-RNF / 单 EPBackend”的可执行改造清单。  
**约束**：`num_sockets=1` 必须完全退化为当前行为，28 个单 socket TC 全通过。

---

## 0. 最终确认结论（冻结）

1. `local_private_range`、`metadata_private_range` 均按 socket 切分。  
2. `ubcc_exclusive_range` 删除；其所有“路由占位/CPU 可见私有元数据窗口”的使用点改名并迁移到 `metadata_private_range(socket)`。  
3. EP-RNF 保持 **全局单个** `_chiRequestInFlight` 串行化，不做 per-socket/per-plane 并发。  
4. `num_sockets > 1` 时，EP-RNF 必须拿到 **完整的每-socket HN-F 版本槽位**；缺失即 `fatal`。  
5. cluster/topology 构造时显式传入 `socket_id`。  
6. EPBackend 提供 `registerEpSnf(socket)` / `getEpSnf(socket)`，并在 `init()` 做完整性检查。  

---

## 1. 目标拓扑（冻结）

```text
Node i:
  CPU L1D/L2 clusters (each cluster has explicit socket_id=0/1)
    │
    ├── PA.homeSocket = 0 ──> HN-F(i,0) ──> EP-SNF(i,0)
    └── PA.homeSocket = 1 ──> HN-F(i,1) ──> EP-SNF(i,1)

  EP-SNF(i,0) ─┐
  EP-SNF(i,1) ─┴─> EPBackend(i)
                    │
                    └─> EP-RNF(i)   // single controller, PA.homeSocket-selecting
                          │
                 UBAdapter(i,0) / UBAdapter(i,1)
                 UBRouter(i,0)  / UBRouter(i,1)
                 UBCC(i,0)      / UBCC(i,1)
                 MetaRNF(i,0)   / MetaRNF(i,1)
```

### 1.1 地址归属原则

- `HN-F(i,k)` 覆盖：`local_private(k)` + `metadata_private(k)` + `DSM(*,k)`。
- `EP-SNF(i,k)` 覆盖：`DSM(*,k)`。
- `EP-RNF(i)` 根据 `PA.homeSocket` 选择下游 `HN-F(i,k)`。
- `EPBackend(i)` 仍为单实例，但持有 per-socket `EP-SNF` 句柄表。

### 1.2 命名澄清

当前代码里有两个“metadata”概念，必须区分：

1. **routing window**：原 `ubcc_exclusive_range` 的那一段，改名为 `metadata_private_range(socket)`；这是 HN-F/CPU-visible 的 per-socket 私有窗口。  
2. **metadata backstore**：当前 `metadata_private_base/size` 对应的 16MB 元数据后备存储；该块继续保留，供 `EPBackend/MetaRNF` 使用。为避免额外文件改动，**EPBackend 参数名保持不变**，但文档上统一称其为“metadata backstore”。

---

## 2. 不需要改的前提件

以下文件已具备 dual-socket 基础能力，本轮只消费，不再扩展：

- `gem5/src/mem/ruby/protocol/chi/ep/NodeAddressMap.hh:19-74`  
  已支持 `num_sockets`、`homeSocket()`、`buildDsmPA(..., home_socket)`。
- `gem5/src/mem/ruby/protocol/chi/ep/MetaRNFController.hh:25` / `.cc:15-28`  
  已支持 `(node_id, socket_id)` 键注册；Python 侧只需真正实例化 per-socket MetaRNF。

---

## 3. 文件级实施清单

---

## 3.1 `gem5/configs/ruby/CHI_basic_framework_config.py`

### A. 当前基线

- `NodeConfig`：`96-128`
  - `local_private_range` 为单段。
  - `ubcc_exclusive_range` 为单段。
  - `metadata_private_range` 是 DSM 之后的 16MB 整段。
- `get_all_system_ranges()`：`149-156`
  - 仍把 `ubcc_exclusive_range` 作为系统地址空间一部分。
- `ClusterCHI_RNF`：`159-233`
  - 无 `socket_id`。
  - 无 per-cluster socket 元信息可供 topology/latency 使用。

### B. 必改范围

1. **`NodeConfig` 重构（修改 `96-128`）**

   将当前单值属性改成 per-socket helper：

   ```python
   def local_private_range(self, socket_id): ...
   def metadata_private_range(self, socket_id): ...   # 原 ubcc_exclusive slot
   def metadata_backstore_range(self, socket_id): ... # 16MB 后备区切片
   def all_local_private_ranges(self): ...
   def all_metadata_private_ranges(self): ...
   def all_metadata_backstore_ranges(self): ...
   ```

   明确布局：

   ```text
   [0*SEG, 1*SEG) : local_private total window, evenly split by socket
   [1*SEG, 2*SEG) : metadata_private total window, evenly split by socket
   [2*SEG, 2+N*S) : DSM(*, socket)
   [metadata_backstore_base, metadata_backstore_end) : 16MB backstore, split by socket
   ```

   删除：

   ```python
   ubcc_exclusive_base
   ubcc_exclusive_end
   ubcc_exclusive_range
   ```

2. **保留 EPBackend 参数兼容名**

   `metadata_private_base/size/end` 当前在 `105-116` 定义。这里建议：

   - 代码字段继续保留现名，避免连带修改 SimObject param。  
   - 文档/注释补充说明：这些字段表示 **metadata backstore**，不是新的 `metadata_private_range(socket)`。

3. **`get_all_system_ranges()` 更新（修改 `149-156`）**

   用：

   - `all_local_private_ranges()`
   - `all_metadata_private_ranges()`
   - `NodeConfig.dsm_global_range(...)`

   替换原 `ubcc_exclusive_range` 收集逻辑。

4. **`ClusterCHI_RNF` 增加显式 socket 参数（修改 `159-233`）**

   `__init__` 新增：

   ```python
   def __init__(..., socket_id=0):
       self.socket_id = socket_id
   ```

   这是 topology latency 的唯一来源；禁止再用 cluster 下标隐式推断。

### C. 本文件新增/保留接口清单

- 新增：`local_private_range(socket_id)`
- 新增：`metadata_private_range(socket_id)`
- 新增：`metadata_backstore_range(socket_id)`
- 新增：`all_*_ranges()` 辅助函数
- 删除：`ubcc_exclusive_range`
- 新增：`ClusterCHI_RNF.socket_id`

---

## 3.2 `gem5/configs/ruby/CHI_ubcc_framework.py`

### A. 当前基线

- 环境与 `num_sockets` 读取：`155-176`
- 本地 SNF / EP-SNF / HN-F / MetaRNF / EP-RNF 创建：`200-329`
  - 每节点仅 1 个 `ep_snf_cntrl`
  - 每节点仅 1 个 `hnf_cntrl`
  - 每节点仅 1 个 `meta_rnf_cntrl`
  - EP-RNF 只绑定 `downstream_destinations=[nd['hnf_cntrl']]`
- cluster 创建：`334-372`
  - 无 `socket_id`
  - cluster 下游只接单个 HN-F
- HN-F downstream 回填：`373-392`
  - 只回填单个 `ep_snf_cntrl`

### B. 必改范围

1. **本地内存窗口与 L_SNF 地址覆盖（修改 `204-223`）**

   当前：

   ```python
   addr_ranges=[
       cfg.local_private_range,
       cfg.ubcc_exclusive_range,
       cfg.metadata_private_range,
   ]
   ```

   改为：

   ```python
   addr_ranges = (
       cfg.all_local_private_ranges()
       + cfg.all_metadata_private_ranges()
       + cfg.all_metadata_backstore_ranges()
   )
   ```

   `l_backstore_range` 继续覆盖整块 node-local DRAM；只更新注释，说明第二段已经不是 `ubcc_exclusive`。

2. **每节点创建 2 个 EP-SNF（修改 `257-281`）**

   当前单实例：

   - `nd['ep_snf_cntrl']`
   - `addr_ranges=[DSM(nid,sid) for nid for sid]`

   改为列表：

   ```python
   nd['ep_snf_cntrls'] = []
   nd['ep_snf_wrappers'] = []
   for sid in range(num_sockets):
       ep_snf = EPSNFController(
           ...,
           ep_backend=ep_backend,
           addr_ranges=[NodeConfig.dsm_range_for(nid, seg_size, cfg.phy_base,
                                                 num_sockets, sid)
                        for nid in range(num_nodes)])
   ```

   命名要求：

   - `ep_snf_node{node_id}_s{sid}`：真实 dual-socket 名称
   - `num_sockets == 1` 时额外保留 legacy alias：`ep_snf_node{node_id}`

3. **每节点创建 2 个 HN-F（修改 `283-299`）**

   当前单实例 `hnf_ranges` 直接覆盖所有 DSM。  
   改为：

   ```python
   nd['hnf_cntrls'] = []
   nd['hnf_wrappers'] = []
   for sid in range(num_sockets):
       hnf_ranges = [
           cfg.local_private_range(sid),
           cfg.metadata_private_range(sid),
           cfg.metadata_backstore_range(sid),
       ] + [NodeConfig.dsm_range_for(nid, seg_size, cfg.phy_base,
                                     num_sockets, sid)
            for nid in range(num_nodes)]
   ```

   命名要求同上：

   - `hnf_node{node_id}_s{sid}`
   - `num_sockets == 1` 时保留 `hnf_node{node_id}` alias

4. **每节点创建 2 个 MetaRNF（修改 `301-315`）**

   当前只创建 `socket_id=0` 的单实例。  
   改为：

   ```python
   nd['meta_rnf_cntrls'] = []
   for sid in range(num_sockets):
       MetaRNFController(
           socket_id=sid,
           addr_ranges=[cfg.metadata_backstore_range(sid)],
           metadata_private_range=cfg.metadata_backstore_range(sid),
           downstream_destinations=[nd['hnf_cntrls'][sid]])
   ```

   说明：这里必须用 **backstore slice**，不能错误复用新的 routing `metadata_private_range(socket)`。

5. **EP-RNF 绑定所有本地 HN-F（修改 `316-329`）**

   当前：

   ```python
   addr_ranges=[DSM(node_id, socket0)]
   downstream_destinations=[nd['hnf_cntrl']]
   ```

   改为：

   ```python
   addr_ranges=[NodeConfig.dsm_range_for(node_id, seg_size, cfg.phy_base,
                                         num_sockets, sid)
                for sid in range(num_sockets)]
   downstream_destinations=[nd['hnf_cntrls'][sid] for sid in range(num_sockets)]
   ```

   **顺序必须稳定**：list index 就是 `socket_id`。

6. **EPBackend 绑定 per-socket EP-SNF（修改 `257-266` 与 EP-SNF 创建后）**

   checklist 目标：

   ```python
   ep_backend.registerEpSnf(sid, nd['ep_snf_cntrls'][sid])
   ```

   如果 Python 侧 SimObject 代理不能直接调用该方法，则保持设计不变，但必须在实现时确保等价注册时序发生在 `m5.instantiate()` 前后之一，且 `EPBackend::init()` 的完整性检查能看见全部槽位。

7. **cluster 显式带 `socket_id`（修改 `334-350`）**

   当前：

   ```python
   cluster = ClusterCHI_RNF(...)
   ```

   改为：

   ```python
   cluster_socket = <from CPU objects, explicit socket_id>
   cluster = ClusterCHI_RNF(..., socket_id=cluster_socket)
   ```

   同时加两条断言：

   - 一个 cluster 内所有 CPU 的 `socket_id` 必须一致。
   - `socket_id` 必须在 `[0, num_sockets)`。

8. **cluster 下游改为所有本地 HN-F（修改 `373-378`）**

   当前：

   ```python
   hnf_c_list = [nd['hnf_cntrl']]
   ```

   改为：

   ```python
   hnf_c_list = [nd['hnf_cntrls'][sid] for sid in range(num_sockets)]
   ```

   由地址路由把请求送到正确 `homeSocket` 的 HN-F。

9. **每个 HN-F 只下挂对应 socket 的 EP-SNF（修改 `379-392`）**

   当前：单个 HN-F 下挂单个 `ep_snf_cntrl`。  
   改为：对每个 `sid`：

   ```python
   snf_dests = []
   snf_dests.extend(nd['l_snf'].getAllControllers())
   snf_dests.append(nd['ep_snf_cntrls'][sid])
   nd['hnf_wrappers'][sid].setDownstream(snf_dests)
   nd['hnf_cntrls'][sid].unproxyParams()
   ```

10. **CPU/HN-F NUMA latency 标记（修改 `334-392` 周边）**

   本文件必须为 topology 提供以下信息：

   - `cluster.socket_id`
   - `hnf.socket_id`（可通过容器下标或显式属性）
   - `lat_local` / `lat_numa`

   要求：

   - same-socket `cluster -> HN-F` 使用 `lat_local`
   - cross-socket `cluster -> HN-F` 使用 `lat_numa`

   若当前 `create_topology()` 分支无法直接区分 pairwise latency，则至少先把 `socket_id` 元信息挂到 controller/wrapper 上，作为 topology builder 的输入；不要把 NUMA 推断写死在 cluster 序号里。

11. **L1/L2 addr_ranges 同步修改（修改 `356-371`）**

   当前含：

   - `cfg.local_private_range`
   - `cfg.ubcc_exclusive_range`
   - `cfg.metadata_private_range`

   改为：

   ```python
   cntrl.addr_ranges = (
       cfg.all_local_private_ranges()
       + cfg.all_metadata_private_ranges()
       + cfg.all_metadata_backstore_ranges()
       + dsm_ranges
   )
   ```

### C. 本文件输出结构要求

- `nd['hnf_cntrls'][sid]`
- `nd['ep_snf_cntrls'][sid]`
- `nd['meta_rnf_cntrls'][sid]`
- `nd['clusters'][*].socket_id`
- `nd['ep_rnf_cntrl']` 单实例，`downstream_destinations` 长度=`num_sockets`

---

## 3.3 `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.hh`

### A. 当前基线

- `sendChiRequest()` 声明：`351-355`
- `_hnfVersion` / `_chiRequestInFlight`：`433-451`
- 当前只有单个 HN-F 版本号，且无 socket-aware downstream 表。

### B. 必改范围

1. **新增 socket-aware 成员（修改 `348-355` 与 `433-451`）**

   将：

   ```cpp
   int _hnfVersion;
   ```

   改为：

   ```cpp
   int _numSockets;
   NodeAddressMap _addrMap;
   std::vector<int> _hnfVersions;               // index == socket_id
   std::vector<MachineID> _downstreamBySocket;  // index == socket_id
   ```

   保留：

   ```cpp
   bool _chiRequestInFlight;
   std::deque<DeferredChiRequest> _deferredChiReqs;
   ```

   即：**序列化仍是全局单份**。

2. **头文件 include 补充（文件顶部 `11-20` 附近）**

   增加：

   ```cpp
   #include "mem/ruby/protocol/chi/ep/NodeAddressMap.hh"
   ```

3. **新增辅助函数（建议放在 `350-409` 私有区）**

   ```cpp
   int decodeHomeSocket(uint64_t linePa) const;
   MachineID selectHnfDestination(uint64_t linePa) const;
   ```

   目的：把 `PA -> homeSocket -> MachineID` 选择逻辑从 `sendChiRequest()` 抽出来，避免后续 recall/upgrade 路径再复制一遍。

### C. 必须满足的结构约束

- `_hnfVersions.size() == _numSockets`
- `_downstreamBySocket.size() == _numSockets`
- `num_sockets==1` 时，`_hnfVersions[0]` 等价旧 `_hnfVersion`

---

## 3.4 `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc`

### A. 当前基线

- 构造函数：`217-234`
  - 只取 `p.downstream_destinations[0]`
- `init()`：`259-275`
  - 只打印单个 `hnfVersion`
- `sendChiRequest()`：`925-985`
  - 固定发往 `_hnfVersion`

### B. 必改范围

1. **构造函数数组化（修改 `217-234`）**

   目标行为：

   - `_numSockets = p.num_sockets`（若无直接 param，则从 `_backend->numSockets()` 或 Ruby config 中取；但最终对象内必须持有该值）
   - `_addrMap = NodeAddressMap(3, _numSockets, 128MB)`
   - 依 `p.downstream_destinations` 顺序填：

   ```cpp
   _hnfVersions.resize(_numSockets, -1);
   _downstreamBySocket.resize(_numSockets);
   for (int s = 0; s < _numSockets; ++s) {
       _hnfVersions[s] = p.downstream_destinations[s]->getVersion();
       _downstreamBySocket[s] = MachineID{MachineType_Cache, _hnfVersions[s]};
   }
   ```

2. **严格完整性检查（修改 `259-275`）**

   `init()` 中新增：

   - `num_sockets > 1` 且 `downstream_destinations.size() != num_sockets` -> `fatal`
   - 任一 `_hnfVersions[s] < 0` -> `fatal`
   - `decodeHomeSocket(linePa)` 结果越界 -> `fatal`

   打印信息改为完整数组，而不是单个 `hnfVersion=%d`。

3. **`sendChiRequest()` 改按 `PA.homeSocket` 选 HN-F（修改 `925-985`）**

   当前：

   ```cpp
   hnfId.num = _hnfVersion;
   ```

   改为：

   ```cpp
   int homeSocket = decodeHomeSocket(linePa);
   MachineID hnfId = _downstreamBySocket[homeSocket];
   ```

   同时 DPRINTF/printf 必须打印：

   - `homeSocket`
   - `dest.num`
   - `inFlight`

4. **保持 Q2 约束：不拆 `_chiRequestInFlight`**

   不允许引入：

   - `_chiRequestInFlight[num_sockets]`
   - `per-socket deferred queue`

   本轮只做目标 HN-F 选择，不做并发模型升级。

### C. 必测行为

- `ReadShared/ReadUnique/CleanUnique` 对同一 EP-RNF：
  - socket0 地址发到 `HN-F(i,0)`
  - socket1 地址发到 `HN-F(i,1)`
- 同 tick 若 socket0 请求在飞，socket1 请求也必须被 defer，而不是绕过全局串行化。

---

## 3.5 `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh`

### A. 当前基线

- UBAdapter 接口：`588-607`
- MetaRNF 单指针：`693`
- `_ubAdapters` / `_numSockets`：`694-695`

### B. 必改范围

1. **新增 EP-SNF 注册接口（修改 `588-607` 附近）**

   新增：

   ```cpp
   void registerEpSnf(int socketId, EPSNFController *ctrl);
   EPSNFController* getEpSnf(int socketId) const;
   ```

2. **新增字段（修改 `688-699`）**

   在私有成员区加入：

   ```cpp
   std::vector<EPSNFController*> _epSnfs;
   ```

   保持：

   ```cpp
   std::vector<UBAdapter*> _ubAdapters;
   int _numSockets;
   ```

3. **前置声明（文件顶部 `22-28`）**

   增加：

   ```cpp
   class EPSNFController;
   ```

### C. 兼容性要求

- `getEpSnf()` 默认可支持 `socket=0` 调用。
- `num_sockets==1` 时 `_epSnfs.size()==1` 即通过。

---

## 3.6 `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc`

### A. 当前基线

- 构造函数：`99-140`
  - 只初始化 `_ubAdapters`
- `init()`：`242-275`
  - 只校验/绑定 UBAdapter
- backstore MetaRNF 路径：`978-1059`
  - 仍走单 `_metaRnf`

### B. 必改范围

1. **构造函数初始化 `_epSnfs`（修改 `99-140`）**

   与 `_ubAdapters` 同步：

   ```cpp
   _epSnfs.resize(_numSockets, nullptr);
   ```

2. **实现 `registerEpSnf()` / `getEpSnf()`（新增实现块）**

   规则：

   - `socketId >= size` 时允许 resize 到 `socketId+1`
   - 允许覆盖前 `fatal_if(existing && existing != ctrl)`，避免 silent alias

3. **`init()` 完整性检查（修改 `242-275`）**

   在现有 UBAdapter 绑定循环后追加：

   ```cpp
   for (int s = 0; s < _numSockets; ++s) {
       fatal_if(_epSnfs[s] == nullptr,
                "EPBackend node_id=%d: missing EP-SNF for socket %d", ...);
   }
   ```

   这是 Q5 的强制要求。

4. **说明：本轮 EPBackend 主协议路径不改为 per-socket 实例**

   仍保持：

   - 单个 `EPBackend`
   - 单个 `EP-RNF`
   - `_ubAdapters[s]` 按 socket 使用

   新增 `_epSnfs[s]` 主要用于：

   - 拓扑完整性校验
   - 后续如果需要从 backend 回指 EP-SNF 时已有稳定索引

5. **metadata backstore 备注（关联 `978-1059`）**

   若 `metadataBackstorePa()` 已按 `homePa` 哈希落到 node-wide backstore，则本轮至少要保证：

   - MetaRNF 实例化为 per-socket
   - `EPBackend::init()`/wiring 能拿到对应 socket 的 MetaRNF

   若暂不扩 `_metaRnf` 为数组，必须在本文件注释里明确：当前 backstore 仍通过单指针访问，仅作为单 socket 兼容路径；dual-socket 正式接线时不可遗漏该点。

---

## 4. 精确修改顺序（建议执行顺序）

1. **先改地址帮助类**：`CHI_basic_framework_config.py`
2. **再改 topology 构造**：`CHI_ubcc_framework.py`
3. **再改 EP-RNF 目标选择**：`EPRNFController.hh/cc`
4. **最后改 backend 完整性与 EP-SNF 注册**：`EPBackend.hh/cc`

原因：Python topology 改完后，C++ 侧的 `downstream_destinations` / `registerEpSnf()` 才有真实对象可接。

---

## 5. `num_sockets=1` 退化要求（必须逐条满足）

1. 仍只创建 1 个 `HN-F(i,0)` / `EP-SNF(i,0)` / `MetaRNF(i,0)`。  
2. 仍只创建 1 个 `downstream_destinations[0]`。  
3. `EP-RNF::_hnfVersions.size()==1`。  
4. `_chiRequestInFlight` 行为与当前完全一致。  
5. legacy alias 名称仍可访问：
   - `hnf_node{node_id}`
   - `ep_snf_node{node_id}`
   - `meta_rnf_node{node_id}`（若当前测试依赖）
6. 28 个既有 TC 不允许因对象命名或 addr_ranges 变化而失效。

---

## 6. 必做代码审查点

### 6.1 地址空间

- 是否还残留 `cfg.ubcc_exclusive_range`
- 是否所有 HN-F/L1/L2/L_SNF 覆盖都切换到了 `metadata_private_range(socket)`
- 是否 `MetaRNF` 使用的是 `metadata_backstore_range(socket)`，不是 routing window

### 6.2 HN-F / EP-SNF 一一对应

- `HN-F(i,0)` 是否只下挂 `EP-SNF(i,0)`
- `HN-F(i,1)` 是否只下挂 `EP-SNF(i,1)`
- EP-RNF 的 `downstream_destinations` 顺序是否与 `socket_id` 完全一致

### 6.3 严格模式

- `num_sockets=2` 且只给 1 个 HN-F downstream 是否 `fatal`
- `EPBackend` 少注册一个 `EP-SNF` 是否 `fatal`

### 6.4 全局串行化不变

- 不能出现 `socket0` 与 `socket1` CHI 请求并行发往两个 HN-F 的新行为

### 6.5 NUMA latency 元信息

- `cluster.socket_id` 是否真实来自 CPU/cluster 输入，而不是循环下标猜测
- topology builder 是否能看到 `cluster.socket_id` 与目标 `hnf socket`

---

## 7. 建议验收矩阵

### P0：单 socket 回归

- `UBCC_NUM_SOCKETS=1`
- 全 28 TC 通过
- 对比对象数、消息流、fatal/warn 数量无异常新增

### P1：双 socket 构造与路由

- `UBCC_NUM_SOCKETS=2` 能成功 instantiate
- 每节点出现：2×HN-F、2×EP-SNF、2×MetaRNF、1×EP-RNF、1×EPBackend
- `EP-RNF sendChiRequest()` 日志能打印正确 `homeSocket`

### P2：双 socket 定向路径

- socket0 地址 miss -> `HN-F(i,0)`
- socket1 地址 miss -> `HN-F(i,1)`
- `HN-F(i,0)` 的 downstream 只走 `EP-SNF(i,0)`
- `HN-F(i,1)` 的 downstream 只走 `EP-SNF(i,1)`

### P3：错误注入

- 缺少 `downstream_destinations[1]` -> `fatal`
- 缺少 `registerEpSnf(1, ...)` -> `fatal`
- cluster mixed-socket CPUs -> `assert/fatal`

---

## 8. 一页式执行摘要

本次真正要做的只有四件事：

1. **把 node 内单 HN-F / 单 EP-SNF 改成 per-socket 双实例。**
2. **把地址覆盖从“单段 private + 全 DSM”改成“per-socket private + per-socket DSM”。**
3. **让单个 EP-RNF 按 `PA.homeSocket` 选对应 HN-F，但仍保持全局串行化。**
4. **让单个 EPBackend 显式保存 per-socket EP-SNF 槽位，并在 `init()` 做 completeness check。**

只要这四点做到，且 `num_sockets=1` 严格退化，本文设计即闭环。
