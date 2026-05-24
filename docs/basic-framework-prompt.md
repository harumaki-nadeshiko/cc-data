# Basic Framework Prompt

本文件是后续 Coding Agent 的正式工作提示文档。使用方式如下：

1. 先完整阅读本文，再开始编码。
2. 本文是当前阶段的设计与验收基线；若与旧报告、旧草案、仓库历史残留实现冲突，以本文为准。
3. 先做基础框架，不提前实现完整 UBCC coherence 协议。
4. 每做完一个阶段，必须运行本文指定的 testcase，不能只靠“代码路径存在”或“对象能实例化”宣称完成。
5. 不允许通过缩小规模、跳过地址映射、绕开 topology、只做 print-only 测试等 shortcut 混过验收。

## 0. Working Mode

Coding Agent 必须按以下工作方式执行：

1. 开发、构建、测试都在无网络 Docker 容器中完成。
2. git 预检、commit、push 都在宿主机完成，不在容器内完成。
3. 容器与宿主机工作流必须使用仓库内已有脚本，不要自行发明平替流程。
4. 编译和执行阶段允许使用的并行核心数上限为 `20`。不要使用超过 `20` 的 `-j` 参数，也不要在测试脚本中默认使用更高并行度。

### 0.1 Docker Usage

标准入口：

1. 构建镜像：

```bash
scripts/ubcc_docker_build.sh
```

2. 启动开发容器：

```bash
scripts/ubcc_docker_run.sh
```

3. 在容器中直接执行命令：

```bash
scripts/ubcc_docker_run.sh bash -lc 'scons build/ARM/gem5.opt -j20'
```

容器约束：

1. 容器运行时必须 `--network none`。
2. repo 必须挂载到容器内 `/workspace`。
3. 所有源码修改、gem5 构建、SE-mode 测试、ARM workload 编译都在容器内完成。
4. 容器内不要执行联网下载、`git push`、`submodule update` 等需要网络的动作。

### 0.2 Git / Commit / Push Workflow

宿主机标准入口：

1. 预检：

```bash
scripts/ubcc_git_preflight.sh
```

2. 阶段完成后提交：

```bash
scripts/ubcc_phase_commit.sh <phase> <message>
```

例如：

```bash
scripts/ubcc_phase_commit.sh M1 "m1: add basic framework topology skeleton"
```

流程要求：

1. 开始任何实现阶段前，先在宿主机运行 `scripts/ubcc_git_preflight.sh`。
2. 在容器内完成该阶段修改、构建、测试。
3. 回到宿主机运行 `scripts/ubcc_phase_commit.sh <phase> <message>`。
4. 若该阶段没有文件改动，才允许跳过 commit/push。

身份与 SSH 约束：

1. 不修改全局 git config。
2. 若缺少 git identity，则在宿主机导出：

```bash
export UBCC_GIT_NAME='Your Name'
export UBCC_GIT_EMAIL='you@example.com'
```

3. SSH key 默认使用：

```bash
export UBCC_SSH_KEY="/mnt/data2/$USER/.ssh/id_rsa_np"
```

### 0.3 Parallelism Cap

编译和执行的并行度上限固定为 `20`。

具体要求：

1. `scons` 最多使用 `-j20`。
2. 任何编译脚本、测试脚本、benchmark build 脚本都不得默认使用大于 `20` 的并行度。
3. 若脚本支持可配置并行度，默认值应不超过 `20`，并在文档或脚本中明确注明。

## 1. Scope

当前目标不是实现完整跨节点 DSM 一致性协议，而是完成以下基础框架：

1. 固定规模 `N=3, L=2, D=2` 的 node/cluster/core 拓扑。
2. 统一的 `DSM VA -> DSM PA` 固定映射。
3. `HN_i` 基于 `PA` 的地址分类与转发。
4. `L_SNF_i / DL_SNF_i / EP_SNF_i` 三分法。
5. `EP_RNF_i` / `EP_SNF_i` skeleton endpoint。
6. `EP_i` / `UBCC_i` backend shell。
7. ordinary CHI cross-node checker。
8. 自动化 testcase 与明确验收标准。

## 2. Non-Negotiable Constraints

Coding Agent 必须遵守：

1. 默认主配置固定为 `N=3, L=2, D=2`。
2. 不允许把主 bring-up 配置降成 `N=1`、`L=1` 或 `D=1`。
3. `DSM` 的 `PA` 在所有 node 上必须统一。
4. `DSM Local` 与 `Local Private DRAM` 必须分开。
5. `UbccExclusive` 第一版不映射给普通 CPU。
6. `EP_RNF_i` 是 sentinel 主路径，不允许把 home-side coherence 主逻辑塞给 `EP_SNF_i`。
7. 第一版不实现 `UR_i`。
8. 第一版 `UBCC_i` metadata 全量内存驻留，不做 eviction/refill/backing-store protocol。
9. ordinary CHI traffic 必须限制在 node 内。
10. 所有新 trace / checker / debug 输出必须带 `node_id`。

## 3. Design Summary

每个 node `i` 创建如下对象：

```text
RN-F endpoints:
  CL_{i,0}
  CL_{i,1}
  EP_RNF_i

HN-F:
  HN_i

SN-F endpoints:
  L_SNF_i
  DL_SNF_i
  EP_SNF_i

Internal backend modules:
  EP_i
  UBCC_i
```

职责划分：

| 对象 | 角色 | 职责 |
| --- | --- | --- |
| `CL_{i,j}` | RN-F | `D=2` core cluster，包含 L1/L2，对外表现为一个 cluster RN-F |
| `HN_i` | HN-F | node-local home agent + L3 + address-class routing |
| `EP_RNF_i` | RN-F | external sentinel + UBCC 对本地 CHI domain 的 coherent local access agent |
| `L_SNF_i` | SN-F | Local Private DRAM + UbccExclusive DRAM |
| `DL_SNF_i` | SN-F | `DSM_i` 的 backing store |
| `EP_SNF_i` | SN-F | `DSM_k, k != i` 的 requester-side remote data plane |
| `EP_i` | internal backend | 统一后端，连接 `EP_RNF_i` / `EP_SNF_i`，转给 `UBCC_i` |
| `UBCC_i` | internal backend | 管理 `DSM_i` 的全局目录与 outer protocol |

## 4. Addressing And Mapping

本节先给最终建议，再解释可选方案 A 的细节。

### 4.1 Required Property

必须满足：

1. 每个 node 上的应用程序通过统一的 `DSM VA` 窗口访问 `DSM`。
2. `HN-F/EP/UBCC` 处理 `DSM` 时看到的是统一的 `DSM PA`。
3. 普通 heap/stack/.data/.text 的 `PA` 不与 `DSM` 和 `UbccExclusive` 冲突。

### 4.2 Unified DSM PA Window

固定：`SegSize = 128MB`，`N = 3`

统一 `DSM PA`：

```text
DSM_0 = [2*SegSize, 3*SegSize)
DSM_1 = [3*SegSize, 4*SegSize)
DSM_2 = [4*SegSize, 5*SegSize)

DSM_GLOBAL = [2*SegSize, 5*SegSize)
homeNode(pa) = floor((pa - 2*SegSize) / SegSize)
```

统一 `DSM VA`：

```text
DSM_VA = [DSM_BASE, DSM_BASE + N*SegSize)

DSM_BASE + 0*SegSize -> DSM_0
DSM_BASE + 1*SegSize -> DSM_1
DSM_BASE + 2*SegSize -> DSM_2
```

实现要求：

1. 每个 process 在启动时就固定建立 `DSM_BASE .. DSM_BASE + N*SegSize` 的映射。
2. 不允许把 DSM 访问建立在“运行期碰巧分配到对应物理页”的偶然行为上。

### 4.3 Recommended First Implementation For Local Memory

推荐的第一版实现是：

1. **统一 DSM PA**。
2. **不要求不同 node 的 LocalPrivate/UbccExclusive 使用相同 PA 数值**。
3. **LocalPrivate/UbccExclusive 使用 node-distinct backend PA ranges**。

也就是：

```text
visible DSM PA: globally unified
visible/local normal PA: node-specific backend PA allowed
```

原因：

1. 这满足你最关心的约束: `DSM` 的 `PA` 对所有 node 统一。
2. 它避免了在第一版就实现“每个 node 的普通本地页都共享同一逻辑 PA 数值”的复杂 page allocator。
3. 它显著降低 SE-mode 与 backend memory mapping 的复杂度。

### 4.4 SE-Mode VA->PA Implementation

SE 模式下，`VA -> PA` 最直接的控制点是 `Process.map()`：

- `gem5/src/sim/Process.py:40-42`
- `gem5/src/sim/process.cc:444-449`

也就是说，Coding Agent 应显式调用：

```python
process.map(vaddr, paddr, size, cacheable=True)
```

来建立固定 `DSM VA -> DSM PA` 窗口。

已有例子：

- `gem5/configs/example/apu_se.py:1062-1063`

这意味着：

1. `DSM` 固定窗口应通过显式 `Process.map()` 完成。
2. 普通 heap/stack/.data/.text 仍由 `Process::allocateMem()` 的默认缺页分配路径处理，除非我们后续加 node-aware allocator。

### 4.5 Why Default Page Allocation Must Be Controlled

默认普通页分配走：

- `Process::allocateMem()` -> `SEWorkload::allocPhysPages()`
  - `gem5/src/sim/process.cc:318-345`
  - `gem5/src/sim/se_workload.cc:74-78`

而 `SEWorkload::setSystem()` 会把系统可分配的物理内存池从：

- `sys->getPhysMem().getConfAddrRanges()`

灌进 `MemPools`：

- `gem5/src/sim/se_workload.cc:43-54`

因此，如果不额外控制：

1. 普通页分配可能落到 `DSM` 窗口。
2. 普通页分配可能落到 `UbccExclusive` 窗口。

这在第一版是不可接受的。

因此必须实现以下二选一机制：

### 推荐机制 B1: Reserved-Range Aware SEWorkload

新增一个 `UBCCSEWorkload` 或等效机制：

1. 在 `SEWorkload::setSystem()` 完成 `memPools.populate()` 后，显式从 free list 中移除：
   - `DSM_GLOBAL`
   - 每个 node 的 `UbccExclusive` range
2. 普通 `allocateMem()` 只能从剩余 allocatable local-private pools 分配。

这需要在：

- `gem5/src/sim/mem_pool.hh/.cc`
- `gem5/src/sim/se_workload.hh/.cc`

增加“reserve/exclude range”能力。

### 推荐机制 B2: Per-Process Pool ID

由于系统中会有多个 node 的 local memory pool，普通页分配还需要 **按 node 固定到对应 local-private pool**。

建议：

1. 给 `Process` 增加 `phys_pool_id` 或等效字段。
2. 修改 `Process::allocateMem()` 调用：

```cpp
seWorkload->allocPhysPages(npages, physPoolId)
```

而不是默认 `pool_id=0`。

需要修改：

- `gem5/src/sim/process.hh`
- `gem5/src/sim/process.cc`
- `gem5/src/sim/Process.py`

## 5. Scheme A In Detail

你问的重点是：**如果不同 node 的 Local Memory 仍想共用同一数值 PA，具体在哪里做地址翻译？**

答案：**不要在 `AbstractMemory::access()` 里做。**

### 5.1 Why Not In `AbstractMemory::access()`

`AbstractMemory::access()` 的行为是：

- 先断言 `pkt->getAddrRange()` 属于本 memory 的 `range`
- 再执行

```cpp
uint8_t *host_addr = toHostAddr(pkt->getAddr());
```

见：

- `gem5/src/mem/abstract_mem.cc:394-397`
- `gem5/src/mem/abstract_mem.cc:491-493`
- `gem5/src/mem/abstract_mem.hh:300-304`

所以如果 packet 到这里时还是“逻辑 local PA”，那么：

1. 它必须已经落在该 memory object 的 `range` 里。
2. `toHostAddr()` 会直接把这个 `PA` 解释成该 memory 的 host offset。

这太晚了，无法优雅支持“多个 node 共享相同逻辑 local PA，但实际落到不同 backend memory”。

### 5.2 Existing gem5 Mechanism We Can Reuse

gem5 已经有现成地址重映射对象：

- `gem5/src/mem/addr_mapper.hh`
- `gem5/src/mem/addr_mapper.cc`

尤其是：

- `RangeAddrMapper`

它会在 packet 通过 mapper 时：

1. 保存原始地址。
2. 把 `pkt->setAddr(remapAddr(orig_addr))`。
3. 将 packet 发往真正 backend memory。
4. 在响应返回时把地址恢复为原始地址。

关键代码：

- `recvFunctional()` / `recvAtomic()` / `recvTimingReq()` 里重写地址
- `recvTimingResp()` 里恢复地址
- `AddrMapperSenderState` 保存原始地址

见：

- `gem5/src/mem/addr_mapper.cc:69-76`
- `gem5/src/mem/addr_mapper.cc:100-108`
- `gem5/src/mem/addr_mapper.cc:133-159`
- `gem5/src/mem/addr_mapper.cc:162-191`

### 5.3 Exact Translation Placement In Scheme A

如果采用 Scheme A，那么翻译点应放在：

```text
L_SNF_i  --memory_out_port-->  RangeAddrMapper_i  --> backend SimpleMemory/MemCtrl
```

而不是：

```text
CPU/TLB
HN_i
AbstractMemory::access()
```

### 5.4 Packet Flow In Scheme A

以 `Node1` 访问逻辑 `LocalPrivate PA = 0x0010_0000` 为例：

1. CPU 发出 packet，`pkt->getAddr() = 0x0010_0000`
2. `RN-F -> HN_1`
3. `HN_1` 分类为 `LocalPrivate`，发给 `L_SNF_1`
4. `L_SNF_1.memory_out_port` 连接到 `RangeAddrMapper_1.cpu_side_port`
5. `RangeAddrMapper_1` 将：

```text
logical [0, 2*SegSize)
-> backend [Node1BackendBase, Node1BackendBase + 2*SegSize)
```

例如：

```text
0x0010_0000 -> 0x4801_0000
```

6. backend `SimpleMemory/MemCtrl/AbstractMemory` 只看到翻译后的 backend PA
7. `AbstractMemory::access()` 对 backend PA 计算 `hostAddr`
8. 响应返回 mapper 时，mapper 恢复 `pkt->getAddr()` 为原始逻辑 PA
9. `L_SNF_1` / `HN_1` / 上游 Ruby/CPU 继续只看到逻辑 PA

### 5.5 What Scheme A Solves, And What It Does Not

Scheme A 只解决：

1. 多个 node 的 **相同逻辑 local PA** 如何映射到不同 backend memory。
2. backend memory 的 `AbstractMemory::access()` 如何继续正常工作。

Scheme A **不自动解决**：

1. SE-mode 默认 page allocator 仍会分配全局唯一 PA，而不是你想要的 node-symmetric local PA。
2. 普通 heap/stack 页不会自动落入 `[0, SegSize)` 这类逻辑 local PA 窗口。

因此，若真要完整采用 Scheme A，还必须再做一项：

### 5.6 Additional Requirement For Full Scheme A

需要一个 **node-aware process physical allocator**，使得 `Process::allocateMem()` 对于属于 `Node_i` 的进程：

1. 返回逻辑 local PA 窗口内的页
2. 避开 `UbccExclusive` 与 `DSM` 窗口

这意味着需要改：

- `Process::allocateMem()`
- `SEWorkload::allocPhysPages()` 或其上层策略

因为这一步复杂度明显更高，所以：

### 5.7 Recommendation On Scheme A

推荐结论：

1. 把 Scheme A 保留为可选进阶方案。
2. 第一版 Coding Agent 不要以 Scheme A 为主实现路径。
3. 第一版优先实现：
   - 统一 `DSM PA`
   - node-distinct local-private backend PA
   - reserved-range aware allocator

这样风险最低。

## 6. Mapping Abstract Objects To Existing gem5 / Ruby / CHI Objects

本节明确“你的抽象对象对应 gem5 现有哪个对象，需要改什么，哪些要新设计”。

### 6.1 `CL_{i,j}`

对应已有对象：

- `configs/ruby/CHI_config.py` 中的：
  - `CHI_Node`
  - `CHI_L1Controller`
  - `CHI_L2Controller`
  - `RubySequencer`

建议实现：

- 新增 `ClusterCHI_RNF` wrapper
- 每个 cluster 封装 `D=2` 个 core 的 L1I/L1D + 一个 shared L2

需要修改：

- 新增配置文件：`gem5/configs/ruby/CHI_basic_framework_config.py`

不需要修改 SLICC。

### 6.2 `HN_i`

对应已有对象：

- `CHI_HNFController`

建议实现：

- 新增 Python wrapper `MultiNodeCHI_HNF` / `UBCCHNFNode`
- 每个 node 一个 HN-F + shared L3

第一阶段所需修改：

1. 配置层设置 `node_id`
2. 设置本 node 的 downstream destination set
3. 通过地址分类把不同范围交给 `L_SNF_i / DL_SNF_i / EP_SNF_i`
4. 加 ordinary CHI cross-node checker

第一阶段尽量不改 HN SLICC coherence 语义。

### 6.3 `L_SNF_i`

对应已有对象：

- `CHI_SNF_Base`
- `CHI_SNF_MainMem`
- `CHI_Memory_Controller` (`CHI-mem.sm`)

建议实现：

- 复用 `CHI_SNF_MainMem` wrapper
- 每个 node 一个 `L_SNF_i`

第一阶段所需修改：

1. 自定义 `addr_ranges`
2. 绑定到 local backend memory
3. 如果采用 Scheme A，则 `memory_out_port` 不直接连 backend memory，而是连 `RangeAddrMapper`

### 6.4 `DL_SNF_i`

对应已有对象：

- 同 `L_SNF_i`，仍复用 `CHI_SNF_MainMem`

建议实现：

- 每个 node 一个 `DL_SNF_i`
- 它只服务统一 `DSM_i` 范围

第一阶段所需修改：

1. 配置 `addr_ranges = DSM_i`
2. 绑定到 `DSM_i` 的 backing memory

不负责 coherence，第一阶段只负责 memory-side data path。

### 6.5 `EP_SNF_i`

对应已有对象：

- 基础类: `CHIGenericController`

建议实现：

- 新增 SimObject:
  - `gem5/src/mem/ruby/protocol/chi/ep/EPController.py`
  - `EPSNFController.hh`
  - `EPSNFController.cc`

第一阶段所需能力：

1. 携带 `node_id`
2. 携带本 endpoint 的 `addr_ranges` 或等价 routing metadata
3. 收到 `ReadNoSnp` 时返回 fake data + legal response
4. 挂进 Ruby network topology

需要新增或修改：

1. `EPController.py` 增加 `node_id`
2. 建议额外增加 `addr_ranges` 参数，便于 config/debug 和 HN memory-side routing 对齐
3. 新增 `SConscript` 注册源码

说明：

- 若现有 HN memory-side 选择路径必须依赖 memory-controller style `addr_ranges`，则 `EPSNFController` 必须显式支持这一属性；不能只做裸 `CHIGenericController` 后就指望 HN 自动知道何时选它。

### 6.6 `EP_RNF_i`

对应已有对象：

- 基础类: `CHIGenericController`

建议实现：

- 新增 SimObject:
  - `EPRNFController.hh`
  - `EPRNFController.cc`

第一阶段所需能力：

1. 携带 `node_id`
2. 进入 topology
3. 接收 `HN_i` snoop
4. 回固定合法 response

第一阶段不要求：

1. 真正 sentinel registration
2. `ExternalSharer/ExternalOwner` 完整语义
3. 真正 UBCC-driven local recall

### 6.7 `EP_i`

对应已有对象：

- 无直接现成对象，建议新设计

建议实现：

- 新增一个 `EPBackend` SimObject：
  - Python: `EPBackend.py`
  - C++: `EPBackend.hh/.cc`

理由：

1. `EP_RNF_i` 与 `EP_SNF_i` 需要共享同一个 backend 实例
2. 用纯 C++ 普通类很难在 Python config 层把同一实例干净地传给多个 endpoint
3. SimObject 更容易接 stats/debug/param

第一阶段能力：

1. 保存 `node_id`
2. 保存 `NodeAddressMap` 参数副本或其简化版
3. 持有 `UBCC_i` shell
4. 提供最小 API 给 `EP_RNF_i` / `EP_SNF_i`

### 6.8 `UBCC_i`

对应已有对象：

- 无直接现成 CHI object，建议新设计

建议实现：

- 第一阶段先作为 `EPBackend` 内部拥有的 C++ 类：
  - `UBCCController.hh/.cc`

第一阶段能力：

1. 保存 `node_id`
2. 保存 `DSM_i` ownership information shell
3. fixed-latency outer queue skeleton
4. 不做完整目录协议，只做对象壳体与最小调度接口

### 6.9 `NodeAddressMap`

对应已有对象：

- 无直接现成对象，建议新设计

建议实现方式：

1. Python 配置侧一个 helper class
2. C++ 运行时一份镜像 helper 或小型 immutable struct

建议接口：

```text
class NodeAddressMap {
  Region classify(node_id, pa)
  bool isLocalPrivate(node_id, pa)
  bool isUbccExclusive(node_id, pa)
  bool isDsm(pa)
  int homeNode(pa)
  bool isDsmLocal(node_id, pa)
  bool isDsmRemote(node_id, pa)
  Addr regionOffset(pa)
  Addr dsmLineAddr(pa)
}
```

### 6.10 Memory Translation Objects

已有对象可复用：

- `RangeAddrMapper` (`gem5/src/mem/addr_mapper.hh/.cc`)

建议：

1. 第一版优先不依赖 Scheme A。
2. 如果后续需要同数值 local PA alias，再实例化 `RangeAddrMapper_i` 置于 `L_SNF_i` 与 backend memory 之间。
3. 不要修改 `AbstractMemory::access()`。

## 7. Detailed Implementation Plan

## 7.1 New Python Config / Runner Files

建议新增：

1. `gem5/configs/ruby/CHI_basic_framework_config.py`
   - 定义 `NodeConfig`
   - 定义 `NodeAddressMap`
   - 定义 `ClusterCHI_RNF`
   - 定义 `HN/L_SNF/DL_SNF/EP_SNF/EP_RNF` wrapper 构造
2. `gem5/configs/example/ubcc/basic_framework_se.py`
   - 负责创建 process
   - 固定 `DSM VA -> DSM PA` map
   - 设置 node_id / pool_id / workload 分配
   - 组装 `N=3, L=2, D=2` 系统

不建议继续依赖通用 `configs/deprecated/example/se.py` 作为主入口，因为：

1. 它不懂 `DSM` 固定窗口
2. 它不懂 node-aware process placement
3. 它不懂 reserved-range allocator 约束

## 7.2 New C++ / SimObject Files

建议新增：

1. `gem5/src/mem/ruby/protocol/chi/ep/EPController.py`
2. `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.hh`
3. `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc`
4. `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.hh`
5. `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc`
6. `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.py`
7. `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh`
8. `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc`
9. `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh`
10. `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc`
11. `gem5/src/mem/ruby/protocol/chi/ep/SConscript`

若采用 reserved-range allocator 方案，还需修改：

1. `gem5/src/sim/Process.py`
2. `gem5/src/sim/process.hh`
3. `gem5/src/sim/process.cc`
4. `gem5/src/sim/se_workload.hh`
5. `gem5/src/sim/se_workload.cc`
6. `gem5/src/sim/mem_pool.hh`
7. `gem5/src/sim/mem_pool.cc`

## 7.3 Phase Breakdown

### Phase 1: Address And Process Control

实现：

1. `NodeConfig`
2. `NodeAddressMap`
3. `basic_framework_se.py`
4. `Process.map()` 建立固定 `DSM VA` 窗口
5. reserved-range aware normal page allocation
6. `phys_pool_id` or equivalent process-to-node local memory binding

验收重点：

1. `DSM` 固定窗口映射正确
2. 普通页不落入 `DSM` / `UbccExclusive`
3. 三个 node 的进程只从各自 local-private pool 分配普通页

### Phase 2: Topology Wiring

实现：

1. `ClusterCHI_RNF`
2. `HN_i`
3. `L_SNF_i`
4. `DL_SNF_i`
5. `EP_SNF_i` placeholder
6. `EP_RNF_i` placeholder

验收重点：

1. `N=3, L=2, D=2` 完整拓扑创建成功
2. `RN-F -> HN_i` 严格同 node
3. `HN_i` 基于地址分类路由到 `L_SNF_i / DL_SNF_i / EP_SNF_i`

### Phase 3: Endpoint Skeleton

实现：

1. `EP_RNF_i` receive-snoop/respond skeleton
2. `EP_SNF_i` receive-ReadNoSnp/respond skeleton
3. `EP_i` shell
4. `UBCC_i` shell
5. fixed-latency outer queue shell

验收重点：

1. endpoint 已接线
2. 最小消息收发路径可触发

### Phase 4: Guardrails And Checker

实现：

1. ordinary CHI cross-node checker
2. forbidden-region assertions
3. trace completeness

验收重点：

1. 人工 misroute 必须 fatal
2. `LocalPrivate` 不触发 EP
3. `UbccExclusive` 不对 CPU 可见

## 8. Testcases And Acceptance Criteria

以下 testcase 是必需项，不允许删减成“对象实例化 + 打印 PASSED”。

### 8.1 Address / Process Tests

#### TC-PROC-1 DSM fixed mapping

- 配置: `N=3`, `SegSize=128MB`
- 检查:
  - `DSM_BASE + 0*SegSize -> DSM_0`
  - `DSM_BASE + 1*SegSize -> DSM_1`
  - `DSM_BASE + 2*SegSize -> DSM_2`
- 验收:
  - 映射精确成立

#### TC-PROC-2 Normal allocation excludes reserved windows

- 检查:
  - heap/stack/.data/.text 不落入 `DSM_GLOBAL`
  - 不落入 `UbccExclusive`
- 验收:
  - 一条违规映射即失败

#### TC-PROC-3 Per-node pool binding

- 检查:
  - 属于 `Node_i` 的进程普通页只从该 node 的 local-private pool 分配
- 验收:
  - 不允许从其他 node pool 分配

### 8.2 Topology Tests

#### TC-TOPO-1 Full-scale object count

- 配置: `N=3, L=2, D=2`
- 检查:
  - 3 个 `HN`
  - 6 个 cluster RN-F
  - 3 个 `EP_RNF`
  - 3 个 `L_SNF`
  - 3 个 `DL_SNF`
  - 3 个 `EP_SNF`
- 验收:
  - 数量必须完全匹配

#### TC-TOPO-2 RN-F same-node downstream

- 检查每个 `CL_{i,j}` 的 downstream。
- 验收:
  - 只能包含 `HN_i`

#### TC-TOPO-3 HN route table correctness

- 对每个 node 注入地址分类样本。
- 验收:
  - `LocalPrivate/UbccExclusive -> L_SNF_i`
  - `DsmLocal -> DL_SNF_i`
  - `DsmRemote -> EP_SNF_i`

#### TC-TOPO-4 Snoop destination restriction

- 验收:
  - `HN_i` ordinary snoop destination 只能是本 node cluster RN-F + `EP_RNF_i`

### 8.3 Ordinary CHI Isolation Tests

#### TC-ISO-1 Three-node LocalPrivate traffic

- 场景: 三个 node 同时访问各自本地普通页。
- 验收:
  - 无跨 node ordinary `REQ/SNP/RSP/DAT`

#### TC-ISO-2 DsmLocal routing

- 场景: 每个 node 访问自己的 `DSM_i`
- 验收:
  - 只到本 node `DL_SNF_i`

#### TC-ISO-3 DsmRemote routing

- 场景: `Node_i` 访问 `DSM_k, k!=i`
- 验收:
  - 请求必须首先落到本 node `EP_SNF_i`

#### TC-ISO-4 Misroute negative test

- 场景: 人工把 `CL_{0,0}` 接到 `HN_1`
- 验收:
  - checker 必须 fatal

### 8.4 Endpoint Skeleton Tests

#### TC-EP-1 EP creation

- 验收:
  - `EP_RNF_i` / `EP_SNF_i` 可创建
  - `node_id` 正确

#### TC-EP-2 EP wiring

- 验收:
  - message buffer 四类端口齐全
  - network ports 已接线

#### TC-EP-3 EPRNF snoop path

- 场景: 手工注入 `Snp*`
- 验收:
  - `recvSnoopMsg()` 被调用
  - 返回合法 response

#### TC-EP-4 EPSNF ReadNoSnp path

- 场景: 手工注入 `ReadNoSnp`
- 验收:
  - `recvRequestMsg()` 被调用
  - 返回 fake data + legal response

#### TC-EP-5 Unwired endpoint negative test

- 验收:
  - 未接线 endpoint init 必须失败

### 8.5 Guardrail Tests

#### TC-G-1 UbccExclusive not CPU visible

- 验收:
  - 普通用户进程无法把页映射到 `UbccExclusive`

#### TC-G-2 Non-DSM sentinel forbidden

- 验收:
  - 对 `LocalPrivate` / `UbccExclusive` 做 sentinel registration 尝试必须失败

#### TC-G-3 Full scale preserved

- 验收:
  - 主配置不能偷偷降成 `N=1` / `L=1` / `D=1`

#### TC-G-4 Trace completeness

- 验收:
  - 所有新增 trace / checker / route log 都带 `node_id`

## 9. Completion Bar

Coding Agent 只有同时满足下面条件，才可以声称“基础框架完成”：

1. `N=3, L=2, D=2` 主配置可创建成功。
2. `DSM VA` 固定窗口映射已建立。
3. 普通页分配不会落入 `DSM` / `UbccExclusive`。
4. `HN_i` 能基于统一 `DSM PA` 和 node-local classification 做正确分流。
5. ordinary CHI cross-node checker 存在且真实执行。
6. `EP_RNF_i` / `EP_SNF_i` 已接入 topology，且最小收发路径被 testcase 真实触发。
7. testcase 不能依赖缩小规模、只实例化对象、只打印字符串。

以下情况一律视为未完成：

1. 只在缩小规模上通过
2. 只有对象实例化，没有 topology wiring 验证
3. 只有静态代码阅读，没有 testcase 真正触发
4. 只有 `grep Exiting` 这类伪成功判定
5. 跳过 `DSM VA` 固定映射或 reserved-range allocator 约束

## 10. Final Recommendation

第一版 Coding Agent 应采用下面的主实现路线：

1. 统一 `DSM VA -> DSM PA`
2. `N=3, L=2, D=2` 完整拓扑
3. `L_SNF_i / DL_SNF_i / EP_SNF_i` 三分法
4. `EP_RNF_i` 作为 sentinel 主路径预留入口
5. `EP_SNF_i` 仅做 requester-side remote skeleton
6. node-aware ordinary CHI isolation checker
7. reserved-range aware allocator，避免普通页落入 `DSM` / `UbccExclusive`

Scheme A 的 local-PA symmetry 可以保留为后续增强方向，但不是第一版的推荐主路径。
