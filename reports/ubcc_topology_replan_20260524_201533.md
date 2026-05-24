# UBCC Topology Replan

- 时间戳: `20260524_201533`
- 目标: 在当前 `M0` 状态下，重新制定基础框架、拓扑连接、地址映射与基础测试方案
- 文档定位:
  - 前半部分: 设计评价、关键答疑、方案取舍
  - 后半部分: 可直接作为 Coding Agent prompt 基础的详细设计与测试要求

## 1. Executive Summary

你的新方案方向是对的，尤其是下面四点：

1. `DSM Local` 与 `Local Private DRAM` 分开处理。
2. 保留 `EP_RNF` 作为 sentinel 主路径，不把 home-side coherence 主逻辑塞进 `EP_SNF`。
3. 允许 `DL_SNF` 独立服务 `DSM Local` 的普通 memory-side 访问，为后续调整留接口。
4. 强制保持 `N/L/D = 3/2/2`，避免在 bring-up 规模上做 shortcut 或 special-case。

我建议采用如下基础结构：

| 组件 | CHI 角色 | 建议职责 |
| --- | --- | --- |
| `CL_{i,j}` | RN-F | cluster wrapper: `D=2` cores + L1/L2 |
| `HN_i` | HN-F | node-local home agent + L3 + address-class routing |
| `EP_RNF_i` | RN-F | external sentinel + UBCC 对本地 CHI domain 的 coherent local access agent |
| `L_SNF_i` | SN-F | Local Private DRAM + UBCC Exclusive DRAM |
| `DL_SNF_i` | SN-F | `DSM_i` backing store |
| `EP_SNF_i` | SN-F | `DSM_k, k != i` requester-side remote data plane |
| `EP_i` | internal | `EP_RNF_i` / `EP_SNF_i` / optional `DL_SNF_i` shared backend |
| `UBCC_i` | internal | 以 node 为粒度管理 `DSM_i` 的全局目录与 outer protocol |

核心边界：

1. `DSM Local` 的普通 memory-side path 走 `DL_SNF_i`。
2. `DSM Local` 上由 UBCC 触发的 coherent local access 仍必须走 `EP_RNF_i`。
3. `EP_SNF_i` 只负责 `DSM Remote` 的 requester-side miss/fill/writeback data plane。
4. `UR_i` 暂不进入第一版。
5. `UBCC` 元数据第一版就在模块内部全量维护，不做逐出/载回。

## 2. 关键答疑与评价

### 2.1 关于 `N/L/D = 3/2/2` 是否必须保持

你的要求是对的，应该保持。

原因：

1. `N=1` 很容易让实现者在地址分类、routing、homeNode 计算、checker 上偷懒。
2. `L=1` 很容易让 cluster-level RN-F wrapper 退化成 per-core RN-F。
3. `D=1` 很容易绕过同 node 内真实 CHI coherence。

因此本轮所有基础设计、配置和测试都应以：

```text
N = 3
L = 2
D = 2
```

为默认目标规模。

允许的降规模场景只有两种：

1. 单独的 unit-test / pure helper test
2. 明确标注为 debug-only 的最小 reproducer

但主配置、主 smoke、主验收一律维持 `3/2/2`。

### 2.2 关于 `L_SNF / DL_SNF / EP_SNF` 三分法

这部分值得保留。

优点：

1. `Local Private` 和 `DSM Local` 的 backing store 分离，后续协议调整不会污染普通本地内存路径。
2. `DL_SNF_i` 让 home DSM memory-side endpoint 独立存在，后续如果要加统计、fault injection、debug hook，很容易插入。
3. `EP_SNF_i` 的职责可以保持狭窄，只做 requester-side remote data plane。

推荐 routing：

| 地址类别 | `HN_i` downstream |
| --- | --- |
| `LocalPrivate` | `L_SNF_i` |
| `UbccExclusive` | `L_SNF_i` |
| `DsmLocal` | `DL_SNF_i` |
| `DsmRemote` | `EP_SNF_i` |

### 2.3 为什么 `DSM Local` 的 coherent local access 仍必须走 `EP_RNF_i`

因为 `DL_SNF_i` 是 memory-side endpoint，不是外部世界在 `HN_i` directory 中的表示。

`DSM Local` 的难点不是“从哪块 DRAM 读数据”，而是：

1. 本地 CPU cache 可能有 sharer / owner / dirty copy。
2. 远端 node 可能已有 `S` 或 `M` 状态。
3. home node 需要把“外部世界”表达为 directory 中的 sentinel。

这些都要求：

1. `HN_i` 能 snoop 一个 synthetic external endpoint。
2. 这个 endpoint 能把 local-domain coherence 操作翻译给 `UBCC_i`。

因此 home-side coherent local access agent 仍必须是 `EP_RNF_i`，而不是 `DL_SNF_i`。

### 2.4 关于 `UR_i`

你的意见成立，先省略掉没有问题。

建议结论：

1. 第一版没有 `UR_i`。
2. `UBCC Exclusive` 不映射给普通 CPU。
3. `UBCC_i` 的 metadata 直接在模块内部全量维护，不做 eviction / refill / backing-store protocol。

这能显著减少第一版不必要的协议面。

### 2.5 关于 `EP_i` / `UBCC_i`

这个抽象是好的，但要限制边界：

1. `EP_i` 只是后端聚合模块，不是 CHI endpoint。
2. 进入 CHI domain 的只有：
   - `CL_{i,j}`
   - `EP_RNF_i`
   - `HN_i`
   - `L_SNF_i`
   - `DL_SNF_i`
   - `EP_SNF_i`
3. `UBCC_i` 只管理 `DSM_i` 的目录和 outer protocol。
4. `EP_i` 负责前端 endpoint 与 `UBCC_i` 的翻译和编排。

## 3. 地址空间与 VA->PA 方案评价

## 3.1 你的目标

你提出的要求可以归纳成两条：

1. 每个 core/process 通过统一的 `VA` 窗口访问 `DSM`。
2. 每个 `HN-F/EP/UBCC` 所看到的 `DSM` 物理地址必须统一且稳定。

这是正确目标。

## 3.2 推荐的两级映射模型

建议显式区分两层：

1. **进程视角**: `VA -> PA`
2. **协议/拓扑视角**: `PA -> region/homeNode/backend`

也就是：

```text
VA --(OS/SE-mode page mapping)--> PA
PA --(NodeAddressMap / HN routing)--> L_SNF / DL_SNF / EP_SNF / homeNode / UBCC
```

这两层不要混写。

## 3.3 对你提出的 `VA -> PA` 方案的评价

你建议：

1. 普通堆/栈/.data/.text 的 `VA` 映射到本地 `Local PA`
2. 每个进程在初始化时，将固定窗口：

```text
[DSM_BASE, DSM_BASE + N * SegSize)
```

映射到：

```text
[2 * SegSize, (N + 2) * SegSize)
```

对应 `DSM_*`

这个方案是合理的，我建议采用。

优点：

1. 所有 node 的应用程序都通过相同 `VA` 访问 `DSM`。
2. `DSM` 的 `PA` 对所有 node 一致，便于 `homeNode(addr)`、directory key、debug trace 统一。
3. `HN-F/EP/UBCC` 只需要面向统一 `PA` 处理 `DSM`，不需要感知各 node 不同的 `VA`。

## 3.4 推荐的第一版 `PA` 分类

我建议优先采用你给出的这个统一 `DSM` 物理窗口：

```text
[0 * SegSize, 1 * SegSize) = LocalPrivate
[1 * SegSize, 2 * SegSize) = UbccExclusive
[2 * SegSize, (N + 2) * SegSize) = DSM window

DSM_k = [(2 + k) * SegSize, (3 + k) * SegSize)
homeNode(pa) = floor((pa - 2 * SegSize) / SegSize)
```

原因：

1. `DSM` 的 `PA` 全局统一，最符合你“每个 HN-F/EP/UBCC 处理的 DSM 物理地址统一”的要求。
2. 对后续实现 `homeNode()` 最简单。
3. 对 directed testcase 最好写。

## 3.5 关于 “不同 node 的 Local Memory PA 是否需要区分”

我的评价是：

1. **DSM 的 PA 必须统一。**
2. **Local Private / UbccExclusive 的 PA 不必跨 node 统一，但不能与 DSM 冲突。**

因此第一版推荐：

1. 对 `DSM` 使用全 node 统一 `PA` 窗口。
2. 对 `LocalPrivate/UbccExclusive` 使用 node-local backend binding 或内部不重叠 backend range。

实现上有两个可选方案：

### 方案 A: 对外统一、对内虚拟化 backend

- 对 CPU / HN / EP / UBCC 暴露的分类 `PA` 仍是：
  - `LocalPrivate = [0, SegSize)`
  - `UbccExclusive = [SegSize, 2*SegSize)`
  - `DSM = [2*SegSize, (N+2)*SegSize)`
- 真正后端 DRAM 地址由 `(node_id, regionOffset)` 再映射到不重叠 backend range。

优点：

1. 逻辑视图最干净。
2. 程序和协议看到的 `DSM PA` 完全统一。

缺点：

1. backend 侧需要额外翻译。

### 方案 B: 只统一 DSM，Local memory 用不重叠全局 PA

- `DSM` 保持统一全局窗口。
- `LocalPrivate` / `UbccExclusive` 给每个 node 分配不重叠全局 backend PA。

优点：

1. 更符合 gem5 单系统全局物理地址习惯。

缺点：

1. 文档视图不如方案 A 对称。

**推荐顺序**：

1. 先按方案 A 设计接口。
2. 如果 gem5 单 RubySystem 对重叠 local PA 过于敏感，则退到方案 B 的实现，但对外仍保留统一逻辑视图。

## 3.6 对 HN-F 分流机制的建议

你的判断正确：每个 `HN_i` 必须根据 `PA` 分类做分流。

推荐实现 helper：

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

`HN_i` 的转发表必须等价于：

```text
if isLocalPrivate(i, pa) or isUbccExclusive(i, pa):
    -> L_SNF_i
elif isDsmLocal(i, pa):
    -> DL_SNF_i
elif isDsmRemote(i, pa):
    -> EP_SNF_i
else:
    fatal
```

## 3.7 对进程初始化与页分配的建议

你提出“在初始化时固定映射 DSM 窗口，后续分配物理页时跳过之前部分”，这是合理的。

第一版建议要求：

1. 每个 process 在启动时预留固定 `DSM VA` 窗口。
2. 该窗口必须一一映射到统一的 `DSM PA` 窗口。
3. 普通堆/栈/代码/数据页分配不得落入 `DSM PA` 和 `UbccExclusive PA`。
4. `UbccExclusive` 不映射给普通用户进程。

这部分需要在 SE mode workload/config 脚本中显式控制，而不是依赖默认随机分配。

## 4. Revised Design

本节开始按“可直接指导 Coding Agent 编程”的粒度写。

## 4.1 固定设计约束

Coding Agent 必须遵守：

1. 默认规模固定为 `N=3, L=2, D=2`。
2. 不允许为了 bring-up 把主配置降成 `N=1` 或 `L=1` 或 `D=1`。
3. `DSM` 的 `PA` 在所有 node 上必须统一。
4. `LocalPrivate` 与 `DSM` 必须分开。
5. `UbccExclusive` 第一版不映射给普通 CPU。
6. `EP_RNF_i` 是 sentinel 主路径，不允许把 home-side coherence 主逻辑塞给 `EP_SNF_i`。
7. 第一版不实现 `UR_i`。
8. 第一版 `UBCC_i` metadata 全量内存驻留，不做 eviction/refill。
9. 所有 ordinary CHI traffic 必须限制在 node 内。
10. 所有新 trace / debug / checker 都必须携带 `node_id`。

## 4.2 组件清单

每个 node `i` 固定创建：

```text
RN-F:
  CL_{i,0}
  CL_{i,1}
  EP_RNF_i

HN-F:
  HN_i

SN-F:
  L_SNF_i
  DL_SNF_i
  EP_SNF_i

Internal:
  EP_i
  UBCC_i
```

## 4.3 地址空间规范

### 4.3.1 统一 DSM PA 窗口

默认：`SegSize = 128MB`

```text
PA:
  LocalPrivate   = [0 * SegSize, 1 * SegSize)
  UbccExclusive  = [1 * SegSize, 2 * SegSize)
  DSM_0          = [2 * SegSize, 3 * SegSize)
  DSM_1          = [3 * SegSize, 4 * SegSize)
  DSM_2          = [4 * SegSize, 5 * SegSize)
```

总 DSM 窗口：

```text
DSM_GLOBAL = [2 * SegSize, (N + 2) * SegSize)
```

### 4.3.2 统一 DSM VA 窗口

每个 process 固定：

```text
VA:
  DSM_VA = [DSM_BASE, DSM_BASE + N * SegSize)
```

映射关系：

```text
VA [DSM_BASE + k*SegSize, DSM_BASE + (k+1)*SegSize)
  -> PA [ (2+k)*SegSize, (3+k)*SegSize )
```

### 4.3.3 普通进程页分配约束

Coding Agent 必须保证：

1. 普通 heap/stack/.data/.text 页不落入 `DSM_GLOBAL`。
2. 普通页不落入 `UbccExclusive`。
3. `DSM` 映射是启动时固定建立，而不是运行期碰巧分配到。

## 4.4 Routing 规范

### 4.4.1 RN-F routing

每个 `CL_{i,j}` downstream 只能指向 `HN_i`。

### 4.4.2 HN-F routing

`HN_i` 必须按 `PA` 分类：

```text
LocalPrivate / UbccExclusive -> L_SNF_i
DsmLocal                     -> DL_SNF_i
DsmRemote                    -> EP_SNF_i
```

### 4.4.3 HN-F snoop destination

`HN_i` 的 ordinary CHI snoop destination 只允许：

```text
CL_{i,0}, CL_{i,1}, EP_RNF_i
```

不允许跨 node ordinary CHI snoop。

## 4.5 第一阶段实现范围

Coding Agent 第一轮只允许实现这些：

1. `NodeConfig`
2. `NodeAddressMap`
3. `ClusterCHI_RNF` with `L=2, D=2`
4. `HN_i / L_SNF_i / DL_SNF_i / EP_SNF_i / EP_RNF_i` topology wiring
5. `EP_i` shell
6. `UBCC_i` shell
7. `EP_RNF_i` skeleton receive-snoop/respond path
8. `EP_SNF_i` skeleton receive-ReadNoSnp/respond path
9. ordinary CHI cross-node checker
10. `DSM VA` 固定窗口映射

第一轮禁止实现：

1. 真正的 sentinel registration
2. 真正的 UBCC directory coherence protocol
3. remote invalidation / owner transfer / recall
4. read-sharing recovery
5. metadata eviction/refill/backing-store

## 5. Testcases And Acceptance

本节用于防止 Coding Agent 做 shortcut。

### 5.1 Address Mapping Tests

#### TC-ADDR-1 DSM VA fixed mapping

- 配置: `N=3, SegSize=128MB`
- 检查:
  - `DSM_BASE + 0*SegSize -> PA DSM_0`
  - `DSM_BASE + 1*SegSize -> PA DSM_1`
  - `DSM_BASE + 2*SegSize -> PA DSM_2`
- 验收:
  - 映射精确一致，不允许偏移错误。

#### TC-ADDR-2 Normal pages skip reserved windows

- 检查:
  - heap/stack/.data/.text 不落入 `UbccExclusive`
  - 不落入 `DSM_GLOBAL`
- 验收:
  - 任一普通页落入保留窗口即失败。

#### TC-ADDR-3 Region boundary correctness

- 检查每个 region 的首地址、末地址、前后边界。
- 验收:
  - 无 off-by-one。

#### TC-ADDR-4 homeNode correctness

- 对 `DSM_0/1/2` 内多个地址检查 `homeNode(pa)`。
- 验收:
  - 必须分别返回 `0/1/2`。

### 5.2 Topology Creation Tests

#### TC-TOPO-1 Full scale creation

- 配置: `N=3, L=2, D=2`
- 检查:
  - 3 个 `HN`
  - 6 个 cluster RN-F
  - 3 个 `EP_RNF`
  - 3 个 `L_SNF`
  - 3 个 `DL_SNF`
  - 3 个 `EP_SNF`
- 验收:
  - 数量必须完全匹配。

#### TC-TOPO-2 RN-F same-node routing

- 检查每个 `CL_{i,j}` downstream 仅为 `HN_i`。
- 验收:
  - 出现任何非本 node `HN` 即失败。

#### TC-TOPO-3 HN region routing table

- 检查 `HN_i` 对四类地址的 route 结果。
- 验收:
  - `LocalPrivate/UbccExclusive -> L_SNF_i`
  - `DsmLocal -> DL_SNF_i`
  - `DsmRemote -> EP_SNF_i`

#### TC-TOPO-4 HN snoop destination restriction

- 检查 `HN_i` 的 snoop destination 集合。
- 验收:
  - 只能包含本 node cluster RN-F + `EP_RNF_i`。

### 5.3 Ordinary CHI Isolation Tests

#### TC-ISO-1 LocalPrivate no cross-node CHI

- 场景: 三个 node 同时访问各自 `LocalPrivate`。
- 验收:
  - 无任何跨 node ordinary `REQ/SNP/RSP/DAT`。

#### TC-ISO-2 DsmLocal no wrong-home routing

- 场景: 每个 node 访问自己的 `DSM_i`。
- 验收:
  - 只进入本 node `DL_SNF_i`，不进入 `EP_SNF_i`。

#### TC-ISO-3 DsmRemote hits local EP_SNF only

- 场景: `Node_i` 访问 `DSM_k, k!=i`。
- 验收:
  - 请求必须先到 `EP_SNF_i`。
  - 不允许被错误送往本 node `DL_SNF_i` 或其他 node 普通 CHI endpoint。

#### TC-ISO-4 Misrouting negative test

- 人工构造 `CL_{0,0} -> HN_1`。
- 验收:
  - checker 必须 fatal。

### 5.4 Endpoint Skeleton Tests

#### TC-EP-1 EP object creation

- 检查: `EP_RNF_i` / `EP_SNF_i` 均可创建。
- 验收:
  - `node_id` 正确可见。

#### TC-EP-2 Endpoint wiring

- 检查:
  - message buffers 完整
  - network port 已接线
- 验收:
  - 任何 endpoint 未接线即失败。

#### TC-EP-3 EP_RNF snoop receive path

- 场景: 手工注入 `Snp*`。
- 验收:
  - `recvSnoopMsg()` 被调用
  - 返回合法固定 response

#### TC-EP-4 EP_SNF ReadNoSnp receive path

- 场景: 手工注入 `ReadNoSnp`。
- 验收:
  - `recvRequestMsg()` 被调用
  - 返回 fake data + legal response

#### TC-EP-5 Unwired endpoint negative test

- 场景: 故意创建未接线 endpoint。
- 验收:
  - init 必须失败，不能静默通过。

### 5.5 Guardrail Tests

#### TC-G-1 UbccExclusive not CPU visible

- 场景: 用户 workload 访问 `UbccExclusive`。
- 验收:
  - 第一版直接失败或拒绝映射。

#### TC-G-2 Non-DSM sentinel forbidden

- 场景: 对 `LocalPrivate` / `UbccExclusive` 尝试 sentinel registration。
- 验收:
  - 必须断言失败。

#### TC-G-3 Full scale only

- 检查主配置脚本。
- 验收:
  - 默认主配置必须是 `N=3, L=2, D=2`。
  - 不允许主 smoke 改成缩小规模偷跑。

#### TC-G-4 Trace completeness

- 验收:
  - 所有新增 controller / route / message / checker log 都带 `node_id`。

## 6. Acceptance Bar

Coding Agent 只有同时满足下面条件，才可声称“基础框架完成”：

1. `N=3, L=2, D=2` 主配置可创建成功。
2. `DSM VA` 固定窗口映射已建立，并且普通页分配避开保留窗口。
3. `HN_i` 能基于统一 `PA` 正确分流到 `L_SNF_i / DL_SNF_i / EP_SNF_i`。
4. ordinary CHI cross-node checker 存在且真实执行。
5. `EP_RNF_i` 和 `EP_SNF_i` 已接入 topology，且最小收发路径可验证。
6. 所有本报告 testcase 至少有对应脚本或自动化验证方式。

下列情况一律视为未完成：

1. 只在 `N=1` 或 `L=1` 或 `D=1` 上通过。
2. 只有对象实例化，没有真实 topology wiring 验证。
3. 只有日志打印，没有自动化断言。
4. 只有“代码路径存在”，没有 testcase 真正触发。
5. 测试绕开 `DSM VA` 固定窗口或绕开 `3/2/2` 主规模。

## 7. Final Recommendation

建议将本轮 Coding Agent 的 prompt 基线固定为：

```text
实现目标不是全局一致性协议，而是完成 3-node / 2-cluster-per-node / 2-core-per-cluster
的基础框架：统一 DSM VA/PA 窗口、NodeAddressMap、clustered RN-F、HN address routing、
L_SNF/DL_SNF/EP_SNF 三分法、EP_RNF/EP_SNF skeleton、ordinary CHI cross-node checker，
并提供不能通过 shortcut 糊弄过去的自动化 testcase。
```

这是当前阶段最稳妥、也最能防止后续返工的方案。
