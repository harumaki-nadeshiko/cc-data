# C2C Migration Assessment：从 Home-Centric Recall 到 Cache-to-Cache 迁移评估

**状态**：未来演进评估文档  
**当前基线**：Recall v4 home-centric model  
**目标**：评估未来是否迁移到 direct owner→requester C2C 数据面

---

## 1. 当前 Home-Centric 模型摘要

## 1.1 当前模型做什么

当前 v4 采用 **home-centric recall**：

- **权限控制中心**：home UBCC
- **数据落点**：home EPBackend buffer
- **共享真值入口**：`HomeMemoryService.read()`
- **requester grant 数据来源**：home buffer 或 home DRAM

当出现 `G_E/G_M` 上的 remote miss 时：

1. requester 向 home UBCC 发 outer request；
2. home UBCC 判定需要 recall；
3. recall 发往 owner 节点的 cache hierarchy；
4. owner 通过 `EP-RNF -> HN-F -> L2` 失权并返回数据；
5. 数据先落到 home EPBackend buffer；
6. home 再把 grant 数据提供给 requester；
7. requester 发 `Clear`；
8. home commit committed 目录。

## 1.2 当前数据流

```text
owner cache -> owner EP-RNF/HN-F -> home EPBackend buffer
                                      │
                                      ├-> requester grant payload
                                      └-> future HomeMemoryService.read()
```

### 优点

- 目录与线性化点最清晰；
- 数据总会在 home 留下一份稳定副本；
- 对 future multi-process / external UBCC 比较自然；
- 有利于调试与故障恢复。

### 代价

- dirty 数据迁移至少多经过一次 home 落点；
- owner→requester 无法直接快传；
- 一次 recall + grant 可能跨两段 UBCC 链路。

---

## 2. 未来 C2C 模型会是什么样

## 2.1 核心思想

未来 C2C（cache-to-cache）模型的核心是：

> **home 只负责权限裁决与排序，数据尽量由旧 owner 直接发给新 requester。**

## 2.2 目标流向

以 write recall 为例：

```text
requester -> home UBCC : ReadUnique
home UBCC -> owner     : Recall / revoke permission
owner -> requester     : direct data transfer (C2C)
owner -> home UBCC     : recall complete / permission surrendered
requester -> home UBCC : Clear
home UBCC              : commit new owner
```

read recall 也类似，只是 owner 降级为 shared，请求者获得 shared。

## 2.3 Home 在 C2C 中的角色

home 不再是每次 recall 数据的必经落点，但仍保留以下职责：

- per-PA 总排序点
- 目录真值源
- epoch/reqId 分配与校验
- recall / invalidate / clear / upgrade 生命周期管理
- 异常恢复与 retry 协调

---

## 3. Delta 分析：Home-Centric 与 C2C 的差异

## 3.1 控制面

控制面变化相对小：

- home 仍做 `processOuterRequest()`
- home 仍建 `RECALL` outstanding
- home 仍决定何时允许 grant 提交

但需要新增：

- owner→requester 的 direct route metadata
- requester-side direct data receive path
- home 对“数据已送达 requester”而非“数据已装入 home buffer”的完成判定

## 3.2 数据面

这是最大变化。

### 当前

```text
owner -> home buffer -> requester
```

### C2C

```text
owner -> requester
home 只收 completion / permission status
```

这意味着：

1. home 不再天然拥有一份最新数据副本；
2. shared 读后是否仍要更新 home DRAM，需要额外定义；
3. write recall 后 requester 成为唯一新 owner，home 可只保元数据；
4. read recall 若要求 MESI shared 与 memory 一致，owner 可能仍需异步/同步 flush home。

## 3.3 完成条件变化

### 当前 recall DONE

`RecallResponse received + data installed in home buffer`

### C2C recall DONE 可能变为

`owner permission revoked + requester data receive acknowledged (+ optional home memory update)`

这会把 DONE 的定义从“home 本地可见”改成“跨两端握手可证实”。

## 3.4 故障恢复变化

当前 home-centric 的优势是：

- home 有 buffer，可以重放 grant 或从 buffer 重供数

C2C 后需要额外处理：

- owner 已失权但 requester 没收到完整数据
- requester 收到数据但 Clear 丢失
- home 没看到 requester receive-ack
- duplicate C2C transfer / partial beat arrival

因此故障模型会明显更复杂。

---

## 4. 工作量评估

## 4.1 主要改动文件

预计至少涉及以下文件：

1. `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh`
2. `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc`
3. `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.hh`
4. `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc`
5. `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.hh`
6. `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc`
7. `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh`
8. `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc`
9. 可能新增 direct-transfer message / routing 定义文件
10. 可能调整配置脚本与测试脚本

## 4.2 代码规模估计

保守估计：

- **核心协议改动**：`~900-1500 LOC`
- **测试与 instrumentation**：`~200-400 LOC`
- **总量**：`~1100-1900 LOC`

## 4.3 复杂度评估

复杂度评级：**高**。

原因：

1. 它不是单点性能优化，而是**数据平面重构**；
2. recall DONE 定义要重写；
3. retry / timeout / duplicate 处理面增大；
4. 需要 owner、requester、home 三方一致握手；
5. 测试覆盖必须重做，尤其是 recall-heavy 与 fail/retry 场景。

---

## 5. 迁移前提条件

在迁移到 C2C 之前，必须先满足以下前提：

## 5.1 home-centric 基线稳定

必须先证明以下能力已稳定：

- TC1/TC2/TC3/TC5/TC6/TC8/TC11 可稳定通过
- recall / invalidate / clear / upgrade 生命周期闭环正确
- `functionalRead` workaround 已彻底移除
- home buffer / HomeMemoryService 行为稳定

## 5.2 外部链路抽象稳定

必须已有明确的：

- `EPBackend <-> UBCC <-> EPBackend` 消息边界
- requester / owner / home node addressing
- 可扩展的数据消息封装
- 多 beat 数据传输与重传机制

也就是说，需要先具备接近 **CXL-like / packetized UBCC links** 的链路抽象。

## 5.3 requester-side 接收与确认能力

必须具备：

- requester EPBackend 直接接收 owner 数据
- 数据完整性确认
- requester-to-home receive-ack / clear 协调
- duplicate payload 去重

## 5.4 共享语义补充定义

特别是 read recall 场景，需要提前定清：

- owner→requester direct shared transfer 后，home DRAM 是否同步更新？
- shared 真值源是否仍要求 home memory clean？
- 若 home 不同步更新，HomeMemoryService.read() 如何避免 stale？

如果这些未先定义，C2C 容易破坏当前 v4 对 shared-memory truth source 的收敛。

---

## 6. 迁移步骤建议

建议采用分阶段迁移，而不是一次性替换。

## Phase 0：稳定当前 Home-Centric v4

目标：

- 完成 recall_spec_v4 落地
- 消灭 `functionalRead/broadcast` recall 正式路径
- 引入稳定的 home buffer + HomeMemoryService

产出：

- 正确性基线
- 数据源抽象 `GrantDataSource`
- 稳定测试矩阵

## Phase 1：抽象 direct-transfer 消息类型

新增但默认不启用：

- `OwnerDataToRequester`
- `RequesterDataAck`
- `RecallCompleteNoHomeData` 或等价 completion

此阶段只加抽象，不改变默认行为。

## Phase 2：先做 write recall 的 C2C

优先 write recall，因为：

- 语义较清楚：旧 owner 失效，新 writer 接管
- 不强依赖 shared-memory-clean 的附加约束

策略：

- owner 直接把数据送给 requester
- home 只等待 owner revoke + requester receive-ack
- 可选：home 不立即保数据，仅保元数据

## Phase 3：再做 read recall 的 C2C

这是更难的一步，因为需要决定：

- shared 后 home memory 是否必须同步 clean
- owner direct-to-requester 后 home 是否仍需拿一份副本

一个可行中间态是：

- **读 recall 仍 home-centric**
- **写 recall 先 C2C**

这样能先收获最有价值的 owner migration 优化。

## Phase 4：引入混合模式与策略切换

支持：

- `home-centric only`
- `write-recall C2C only`
- `full C2C`

便于：

- A/B testing
- 故障定位
- 按测试用例逐步放开

## Phase 5：清理 home-buffer 依赖

在 full C2C 稳定前，不应移除 home buffer。  
等 full C2C 稳定后，再评估：

- buffer 是否只保 writeback / local DSM backing
- recall 路径是否可不再强依赖 buffer 落点

---

## 7. 推荐结论

当前阶段的推荐结论是：

> **短期不直接切到 full C2C；先把 home-centric v4 做成稳定、可验证、无绕路的正确性基线，再以 write recall 为突破口做增量 C2C。**

理由：

1. 当前主要问题仍是正确性闭环，而不是数据面极限性能；
2. home-centric 已与 `scheme_v4 + local_dsm_routing_v4` 收敛；
3. C2C 的主要增益在 owner→new-writer 迁移，而这恰好适合先从 write recall 开始；
4. read recall 涉及 shared-memory truth source，风险更高，应后做。

---

## 8. 一句话版本

**home-centric v4 是正确性基线；未来 C2C 应作为其上的数据平面优化，而不是在 recall 闭环尚未稳定前提前替换。**
