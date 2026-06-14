# Local DSM Routing v4：架构分析与推荐

## 1. 结论先行

**推荐采用 Approach D（C 的工程化版本）**：

- **HN-F 对所有 DSM PA 一律只路由到 EP_SNF**，彻底消除 `same-PA -> DL_SNF/EP_SNF` 二义性。
- **UBCC 只做全局目录与排序**，**不直接碰 DDR4**。
- **本地 DSM 的 clean data 由 home 节点的 EPBackend/home-memory service 提供**；dirty data 由 recall 经 `EP-RNF -> HN-F -> L2` 回收。
- **DL_SNF 不再作为 HN-F 的 DSM downstream**；若保留，只能退化成 EPBackend 私有的本地内存访问后端，而不是地址解码目标。

这本质上是：

> **单路由入口（EP_SNF） + 控制/数据分离（UBCC 只控序，EPBackend/home-memory 供数）**

它同时满足 5 个约束，并且最接近未来“UBCC 在 gem5 外、经消息链路互联”的硬件形态。

---

## 2. 评估准则

必须同时满足：

1. **全局一致性**：所有 DSM 访问都进入 UBCC 目录
2. **UBCC 外置兼容**：UBCC 不直接访问 DDR4
3. **无路由歧义**：HN-F 对每个 PA 只有一个 downstream
4. **TC1 不死锁**：本地-only DSM 不再出现 `SnpShared -> EP-RNF` 卡死
5. **跨节点可见性**：remote write / local read、local write / remote read 都正确

---

## 3. Approach A：单 EP_SNF 路径，grant 后再取本地 DDR4

### 3.1 本地 DSM Read

1. `CPU/L2_i -> HN-F_i -> EP_SNF_i`
2. `EP_SNF_i -> EPBackend_i -> UBCC_i(home)`
3. UBCC 决定权限、必要时建 barrier
4. 若无 dirty owner，grant 返回后再由 EPBackend_i 取本地 DDR4 数据
5. `EP_SNF_i -> HN-F_i: CompData_*`
6. requester 侧发 `Clear`，home UBCC commit

### 3.2 本地 DSM Write

- `I -> Write miss`：同上，但 UBCC 授予 `G_M`
- `S -> local upgrade`：仍需 `EP-RNF -> OuterUpgradeReq/Ack/Done` 四消息握手

### 3.3 TC1

- **理论上可过**，前提是：
  - `pickSharerForSnoop()` 永久排除 EP-RNF 作为 preserving-query/Fwd 目标
  - sole-EP-RNF 时 DCT 必须回退到 non-DCT snoop initiator
  - `SnpShared/SnpSharedFwd/SnpOnceFwd` 到 EP-RNF 继续视为 fatal-grade bug，而不是常规路径
- **当前代码下未必能过**，因为现有死锁正是这组缺陷导致

### 3.4 TC2（Node0 写 DSM_1，Node1 读）

1. Node0 通过 `EP_SNF_0 -> UBCC_1` 获得 `G_M`
2. Node1 读 DSM_1 时也走 `EP_SNF_1 -> UBCC_1`
3. UBCC_1 对 owner=0 发 recall
4. owner 侧必须经 `EP-RNF_0 -> HN-F_0 -> L2_0` 回收数据/权限
5. recall 完成后，UBCC_1 给 Node1 grant

### 3.5 代码/配置变化

- `CHI_ubcc_framework.py`：所有 DSM range 仅归 `EP_SNF`
- 去掉 D-10 workaround
- 修 `CHI-cache-actions.sm` / `CHI-cache-funcs.sm` 的 DCT fallback 与 sharer 选择
- `EPBackend` 需显式区分“grant 后 home-memory 取数”与“recall 数据返回”

### 3.6 优缺点

**优点**：

- 单一路径，路由最干净
- UBCC 一定看到所有 DSM 事务
- 不依赖 DDR4 writeback 才能跨节点可见

**缺点**：

- A 把“UBCC 决策”和“数据如何 materialize”混在一起，接口不干净
- 若实现成“UBCC 直接带数据”或 requester 侧乱用 `functionalRead`，会再次污染架构边界
- 对未来外置 UBCC，不够自然

**判断**：**方向基本正确，但定义还不够收敛**。

---

## 4. Approach B：本地走 DL_SNF，另发轻量通知给 UBCC

### 4.1 本地 DSM Read

1. `HN-F_i -> DL_SNF_i -> DDR4`
2. 同时或随后 `EP_RNF/EPBackend_i -> UBCC_i` 发 metadata 通知

### 4.2 本地 DSM Write

1. 本地先拿到 DL_SNF 数据/权限
2. 再通知 UBCC “本节点已持有/升级为写者”

### 4.3 TC1

- **大概率能过**，因为本地流量仍主要停留在 `DL_SNF`
- 但这是用“绕开 EP 路径”换来的，不是根治

### 4.4 TC2

最大问题是**时间窗**：

- 若 Node0 已通过 DL_SNF 获得本地写权限，但 UBCC 尚未来得及记录 owner
- 此时 Node1 读到达 UBCC，就会按旧目录做错判

为了补这个洞，必须让**本地 DL_SNF grant 在 UBCC ack 前不可提交**。一旦这么做，本地快路径就不再“快”，而且协议变成双提交点。

### 4.5 代码/配置变化

- DL_SNF 需要新增“元数据先行/后确认”协议
- HN-F 需要允许一次本地访问同时驱动本地数据路径 + 外部目录通知路径
- 新增 rollback / BUSY / replay 语义，复杂度极高

### 4.6 优缺点

**优点**：

- 表面上保留了本地 DDR4 快路径
- TC1 风险最小

**缺点**：

- 违反“单线性化点”思路
- 极易出现 notify-before/after data race
- HN-F 角度仍是双路径心智模型
- 与未来外置 UBCC 不自然：本地 cache miss 仍要做 shadow directory 更新

**判断**：**不推荐**。这是把当前冲突复制成更隐蔽的竞态。

---

## 5. Approach C：EP_SNF 统一入口，UBCC 只做目录，EPBackend 在 grant 后访问本地 DDR4

### 5.1 本地 DSM Read

1. `CPU/L2_i -> HN-F_i -> EP_SNF_i`
2. `EP_SNF_i -> EPBackend_i -> UBCC_i(home)` 请求 `Shared`
3. UBCC_i 只做目录判定：
   - `G_I/G_S` 且无 dirty owner：返回 `GrantShared + dataSource=HomeMemory`
   - `G_E/G_M` by other：先发 recall
4. 若 `dataSource=HomeMemory`，由 home-side EPBackend 访问本地 DDR4 / home-memory service 取数
5. `EP_SNF_i -> HN-F_i: CompData_SC(shared_hint=true)`
6. requester 发 `Clear`，UBCC 在 `Clear` 处 commit

### 5.2 本地 DSM Write

#### 情况 1：I 态写 miss

1. `HN-F_i -> EP_SNF_i -> UBCC_i` 请求 `Unique+writeIntent`
2. UBCC_i 若无 owner，回 `GrantModified + dataSource=HomeMemory`
3. EPBackend_i 取 home-memory 数据并回 `CompData_UD/UC`
4. requester 发 `Clear`
5. UBCC_i commit 为 `G_M(owner=i)`

#### 情况 2：本地已 Shared，随后升级写

1. HN-F 对 EP-RNF 发 `SnpCleanInvalid`
2. `EP-RNF_i -> EPBackend_i -> UBCC_i: OuterUpgradeReq`
3. UBCC_i `Ack(true)` 后，EP-RNF_i 才能回 `SnpResp_I`
4. HN-F 本地 upgrade 完成
5. requester 侧发 `OuterUpgradeDone`
6. UBCC_i commit 为 `G_E/G_M`

### 5.3 TC1

- **可以通过，但前提是修正 HN-F/EP-RNF 交互**：
  - `pickSharerForSnoop()` 不能把 EP-RNF 当成常规 `SnpShared` 目标
  - sole-EP-RNF 时必须 DCT fallback
  - local-upgrade 只允许 `SnpCleanInvalid -> OuterUpgradeReq/Ack/Done`
- 也就是说：**TC1 的根因不是“本地 DSM 不能走 EP_SNF”，而是“走 EP_SNF 后 HN-F 对 EP-RNF 的 snoop 选择错了”**

### 5.4 TC2

1. Node0 写 DSM_1：`EP_SNF_0 -> UBCC_1`，UBCC_1 commit `G_M(owner=0)`
2. Node1 读 DSM_1：`EP_SNF_1 -> UBCC_1`
3. UBCC_1 对 owner 0 发 read-recall
4. owner 0 侧必须经 `EP-RNF_0.startReadShared()` 走本地 HN-F 回收数据/降级
5. recall response 到 UBCC_1 后，UBCC_1 发 `GrantShared`
6. Node1 `Clear` 后 commit 为 `G_S(sharers={0,1})`

### 5.5 代码/配置变化

#### 配置

- `gem5/configs/ruby/CHI_ubcc_framework.py`
  - **EP_SNF 覆盖全部 DSM 窗口**
  - **DL_SNF 不再暴露本地 DSM range 给 HN-F**
  - HN-F downstream 对 DSM 只看到 EP_SNF

#### EP/UBCC

- `EPBackend.hh/.cc`
  - 新增显式 `GrantDataSource`：`HomeMemory | RecallBuffer | NoData`
  - 删除/禁止 `functionalRead + phys_mem broadcast` 作为正式 recall 主路径
  - read recall 走 `EP-RNF.startReadShared()`
  - write recall 走 `EP-RNF.startReadUnique(... RecallUnique)`
- `UBCCController.hh/.cc`
  - grant decision 只返回权限与数据来源，不自己 materialize 数据
  - 继续保持 reserve-then-commit / commit-on-Clear
- `EPSNFController.cc`
  - 把本地 DSM 与远端 DSM 都视为 EP 路径
  - 允许 deferred CompData，等待 grant-data source 就绪

#### HN-F / EP-RNF

- `CHI-cache-funcs.sm` / `CHI-cache-actions.sm`
  - 修好 `pickSharerForSnoop()` 与 sole-EP-RNF DCT fallback
- `EPRNFController.cc`
  - 保持 `SnpShared/SnpSharedFwd/SnpOnceFwd -> fatal`
  - 完整落实 local upgrade 四消息握手

### 5.6 优缺点

**优点**：

- 满足全部 5 个约束
- UBCC 只做目录/排序，适配未来外置部署
- 不再依赖“dirty data 必须先刷回 DDR4”
- 数据路径与控制路径边界清晰

**缺点**：

- 需要重写当前 `populateGrantData()` / `handleRecallRequest()` 这类临时逻辑
- 必须真正修掉 TC1 的 HN-F snoop 选路 bug，不能再靠 D-10 绕开

**判断**：**最合理的基线方案**。

---

## 6. Approach D：C 的工程化收敛版（推荐）

### 6.1 核心定义

在 C 的基础上，进一步明确一个实现边界：

> **home 节点的 DSM backing store 属于 `EPBackend + HomeMemoryService`，而不属于 HN-F 可见的 DL_SNF 路由空间。**

也就是：

- **HN-F 路由层**：DSM 只有一个目的地 `EP_SNF`
- **EP 内部实现层**：EPBackend 可以调用本地 home-memory service 读/写 backing store
- **UBCC**：只做 order + directory，不承载数据平面

### 6.2 本地 DSM Read

1. `HN-F_i -> EP_SNF_i`
2. `EPBackend_i -> UBCC_i`
3. UBCC_i 返回：`GrantShared, source=HomeMemory`
4. `EPBackend_i -> HomeMemoryService_i.read(homePa)`
5. `EP_SNF_i -> HN-F_i: CompData_SC`
6. `EPBackend_i -> UBCC_i: Clear`

### 6.3 本地 DSM Write

- 写 miss：`GrantModified, source=HomeMemory`
- 升级写：仍走 `OuterUpgradeReq/Ack/Done`
- dirty 数据平时可停留在 cache；只有 recall / writeback / evict 时才回 home-memory

### 6.4 TC1

会走 EP 路径，但**不会再依赖 DL_SNF**。能否通过取决于两件事：

1. `SnpShared -> EP-RNF` 是否被彻底消灭为不可达
2. local-upgrade 握手是否满足 `Ack(true) before SnpResp_I`

若这两点修好，TC1 应通过，而且比 D-10 更符合最终架构。

### 6.5 TC2

与 C 相同，但实现上更干净：

- clean data：由 home-memory service 提供
- dirty/owned data：由 recall 提供
- 任何 grant 都不再从“猜测哪个 phys_mem/哪个 cache 里有新值”出发

### 6.6 代码/配置变化

除 C 的修改外，再增加：

- 在 `EPBackend` 引入明确的 **home-memory accessor API**
- 把现有 `DL_SNF`：
  - 要么移出 HN-F DSM downstream 集合
  - 要么直接重命名/重构为 EP 私有内存后端
- `populateGrantData()` 退化为：
  - `RecallBuffer` 取 recall payload
  - `HomeMemory` 取 home-memory service
  - **不再**做全系统 functional probe

### 6.7 优缺点

**优点**：

- 是 C 的可实施版本
- 最适配未来“UBCC 外置、链路类 CXL”的形态
- 最容易把当前 prototype 中的临时 `functionalRead/phys_mem broadcast` 清理掉

**缺点**：

- 需要接受一个结论：**TC1 的正确修法不是保留 DL_SNF 本地快路径，而是修掉 EP 路径中的 HN-F 选路 bug**

**判断**：**推荐采用**。

---

## 7. 方案对比总结

| 方案 | 全局一致性 | 无路由歧义 | TC1 前景 | 外置 UBCC 兼容 | 结论 |
|---|---|---:|---:|---:|---|
| A | 高 | 高 | 中 | 中 | 可行，但边界不够清楚 |
| B | 低 | 中 | 高 | 低 | 不推荐 |
| C | 高 | 高 | 高（修好 snoop 后） | 高 | 强可行 |
| D | **最高** | **最高** | **高** | **最高** | **推荐** |

---

## 8. 最终推荐

### 8.1 推荐架构

采用 **Approach D = “All-DSM-through-EP_SNF + UBCC directory-only + EPBackend HomeMemoryService”**。

### 8.2 推荐理由

1. **协议正确性最好**
   - 所有 DSM 请求都先到 UBCC
   - dirty data 不要求预先刷回 DDR4
   - recall 重新回到 `EP-RNF -> HN-F` 正规路径

2. **消除了 HN-F 路由冲突**
   - DSM PA 对 HN-F 只有一个 downstream：`EP_SNF`
   - DL_SNF 不再与 EP_SNF 争夺同一 PA

3. **最符合未来硬件部署**
   - UBCC 外置时，只保留 control-plane 消息最合理
   - 数据来自 home memory 或 owner recall，而不是让 UBCC 承担 data-plane

4. **对当前问题闭环最直接**
   - 解决 Issue 5：不再依赖 stale DDR4 作为跨节点读的真值源
   - 解决 Issue 6：recall 必须经 HN-F，不再允许 `functionalRead` 假完成
   - 暴露并迫使修复 TC1 真根因：`pickSharerForSnoop + DCT fallback + upgrade handshake`

### 8.3 落地顺序

1. **撤销 D-10**：恢复 all-DSM-through-EP_SNF
2. **先修 TC1 根因**：
   - `pickSharerForSnoop()`
   - sole-EP-RNF DCT fallback
   - `SnpShared/SnpSharedFwd/SnpOnceFwd` 对 EP-RNF 保持 fatal
3. **重写 recall 路径**：删除 `functionalRead + phys_mem broadcast`
4. **引入 `GrantDataSource + HomeMemoryService`**
5. **把 DL_SNF 从 HN-F DSM downstream 中移除**
6. **再跑 TC1/TC2/TC3/TC6/TC8/TC11 验证**

---

## 9. 一句话版本

**不要再让同一个 DSM PA 在 HN-F 处同时可去 DL_SNF 和 EP_SNF。正确收敛点是：DSM 统一走 EP_SNF；UBCC 只管全局目录；本地 DDR4 由 EPBackend 的 home-memory service 在 grant 后供数。**
