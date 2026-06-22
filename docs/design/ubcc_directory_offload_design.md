# UBCC 目录 Onload/Offload 最终设计（已收敛版）

## 1. 目标与范围

本文固定 UBCC 目录分层实现方案，只覆盖：

- resident directory cache（片上 SRAM 抽象）
- Bloom Filter / Counting Bloom Filter
- private DRAM backstore（full directory）
- 经 MetaRNF + HN-F(L3) 的 timed metadata access path

不变项：

- **UBCC 仍是同 PA 的唯一排序点**
- **Clear / OuterUpgradeDone 仍是 committed 提交点**
- outstanding / tombstone / pending requester / recall data buffer **继续常驻 UBCC，不 offload**

---

## 2. 已确认的固定决策

### 2.1 Epoch

- **统一 24b epoch**，片上与 backstore 同宽
- 不做截断、展开、异宽比较
- `DirEntry.epoch` 语义上是唯一权威 committed epoch
- 除目录本体外，其他模块**不持有永久 epoch 副本**
- per-PA epoch 永久单调递增
- 24b 对单 PA 提供约 8M commits 窗口，满足回环安全要求
- `ubcc_epoch_bits` 已支持配置，24b 已通过全部 TC

### 2.2 16-node canonical resident entry

已选定格式：

- `MESI`: 2b
- `residentDirty`: 1b
- `sharersMaskOrWbMask`: 16b
- `epoch`: 24b
- control byte: 8b
- reserved: 13b

明确约束：

- **无显式 ownerCode**：由 `sharersMask + MESI` 推导
- **无显式 Valid**：`G_I && residentDirty==0` 即 empty
- **两个 dirty 语义分离**：
  - `protoDirty := (MESI == G_M)`，不单独存位
  - `residentDirty` 单独存位，表示相对 backstore 是否脏
- **无 `nextReqId`**：所有请求自带 node-namespaced `reqId`
- **`G_E/G_M` 下 `sharersMask` 必须 one-hot**

### 2.3 Resident entry 位布局（16-node, 24b epoch）

| bit range | field |
|---|---|
| `[1:0]` | `MESI` |
| `[2]` | `residentDirty` |
| `[18:3]` | `sharersMaskOrWbMask[15:0]` |
| `[42:19]` | `epoch[23:0]` |
| `[50:43]` | `control byte` |
| `[55:51]` | `reserved` |
| `[63:56]` | `reserved/alignment` |

总计：**56b = 7B/entry**。

语义判定：

- `MESI==G_I && residentDirty==0`：empty
- `MESI==G_I && residentDirty==1`：delete-pending tombstone
- 其他组合：正常 resident committed entry

其中：

- 正常态下 `[18:3]` 表示 `sharersMask`
- `G_I + residentDirty=1` tombstone 态下 `[18:3]` 复用为 `writebackTrackingMask`

### 2.4 Control byte

control byte 固定存在于同一 SRAM buffer 中，但**不计入 content budget**。其内容固定为：

- `fillPending`
- `wbPending`
- `pinned`
- replacement/LRU bits

---

## 3. SRAM 布局与 512KB 预算

### 3.1 物理组织

- 预分配单块 `uint8_t ubcc_sram_buffer[512*1024]`
- Resident Directory Cache 与 Bloom Filter **共享这一块 buffer**
- 字段按位紧密排列，访问时做 bit-pack/unpack
- 使用单一 bump allocator：初始化时先算各结构所需 bit 数，再从 buffer 顺序切分
- control byte 也在同一 buffer 中

### 3.2 总预算

- 总 SRAM = `512 KiB = 524,288 B = 4,194,304 bits`
- 单 resident entry = `56 bits = 7 B`

若 Bloom Filter 固定切片为 `BF_bytes`，则：

- `resident_entries = floor((524,288 - BF_bytes) / 7)`
- `resident_dir_bytes = resident_entries * 7`
- `spare_bytes = 524,288 - BF_bytes - resident_dir_bytes`

由于 entry 恰好为 7B，预算计算可直接按字节做。

### 3.3 常见切片对应容量

| BF 切片 | Resident entries | Resident dir 占用 | 备注 |
|---:|---:|---:|---|
| `0 KiB` | 74,898 | 524,286 B | 仅公式上成立，不建议无 BF |
| `32 KiB` | 70,217 | 491,519 B | 余 1B spare |
| `64 KiB` | 65,536 | 458,752 B | **整齐切分，刚好 2^16 项** |
| `128 KiB` | 56,173 | 393,211 B | 余 5B spare |
| `256 KiB` | 37,449 | 262,143 B | 余 1B spare |

### 3.4 Counting BF 的计数容量

若采用 **4-bit counter** 的 Counting BF，则：

- `BF_counters = BF_bytes * 2`

对应常见切片：

| BF 切片 | 4-bit counters |
|---:|---:|
| `32 KiB` | 65,536 |
| `64 KiB` | 131,072 |
| `128 KiB` | 262,144 |
| `256 KiB` | 524,288 |

> 目前**已确认的是“Counting BF 推荐、支持删除”**，但 **BF 固定切片大小** 尚未最终拍板，因此本文保留公式与常见刻度，待实现前最后确认。

---

## 4. Backstore（Full Directory in DRAM）

### 4.1 存储格式

backstore 只保存稳定 committed directory：

- `state`: 2b
- `sharersMask`: 16b
- `epoch`: 24b

总计：**42b/entry**。

明确不存：

- `residentDirty`
- `ownerCode`
- `Valid`
- `nextReqId`
- outstanding / tombstone / pending requester / recall data

推导规则：

- `owner` 由 `MESI + sharersMask` 推导
- `G_E/G_M` 时 `sharersMask` 必须 one-hot
- backstore empty 由“**哈希表中无此记录**”表示，不额外存 invalid 记录

### 4.2 组织方式

- backstore 采用**开放寻址哈希表**
- 删除采用 **Robin Hood delete**
- 删除后**不保留空洞 tombstone slot**，而是回填/压缩 probe cluster

### 4.3 删除语义

删除 tombstone **只存在于 resident 层**：

1. 协议提交到 `G_I` 时，resident entry 进入 `G_I + residentDirty=1`
2. 后台向 backstore 发送 delete
3. backstore ack 后，resident entry 才能从 tombstone 转为 empty / victimizable

因此：

- resident tombstone 用于保证删除未落盘前不会把旧 committed 状态重新读回
- backstore 本体不存 `residentDirty`，也不存逻辑 tombstone record

---

## 5. 元数据访问路径（固定版）

固定访问路径如下：

```text
UBCC → UBRouter → UBAdapter → EPBackend → MetaRNF → HN-F(L3) → L_SNF → private DRAM
```

### 5.1 MetaRNF 的角色

- MetaRNF 是**节点内新增 RNF**
- 挂在 `EPBackend` 上
- 与 EP-RNF **同级**，但只服务目录 metadata
- 它不承载普通 DSM 数据访问，只承载 backstore metadata timed 访问

### 5.2 各段职责

- `UBCC`：目录排序点；决定 resident hit/miss、replay、commit、evict、delete
- `UBRouter / UBAdapter`：本地接口桥接，不改变协议排序语义
- `EPBackend`：提供目录存储服务入口，完成 `line_pa -> metadata_private_range` 映射
- `MetaRNF`：把目录读/写/删转成真实 CHI timed memory transaction
- `HN-F(L3)`：作为 backstore 的 inclusive writeback cache，提供 hit/miss/evict/writeback timing
- `L_SNF -> private DRAM`：承载 full directory 真值存储

### 5.3 关键要求

- backstore 访问**必须经过 HN-F/L3 timing path**
- 禁止继续使用 `functionalAccess()` 作为目录主路径
- 目录 onload/offload 必须具备真实 `L3 hit / L3 miss / DRAM / eviction / writeback` 时延特征

---

## 6. Bloom Filter 语义

- Bloom Filter 位于 `ubcc_sram_buffer` 中
- 只做 **advisory negative filter**
- `BF negative`：允许直接判定“无需查 backstore”
- `BF positive`：必须继续查 resident/backstore，**不能当成命中真值**
- `G_I` commit 后 **不立即清 BF**
- 只有在 **backstore delete ack 完成后** 才允许清除 BF
- 推荐使用 **Counting BF** 支持删除

更新规则：

1. empty → non-empty committed transition：置 BF
2. refill 命中 backstore：保持 BF 已置位
3. 提交为 `G_I`：先保留 BF
4. delete ack：再清 BF

---

## 7. Resident hit / miss / refill / eviction

### 7.1 Resident hit

resident hit 时：

- 直接按现有协议逻辑处理
- `commitIntendedResult()` 只更新 resident copy
- 提交后置 `residentDirty=1`
- 不同步写 backstore

### 7.2 Resident miss

resident miss 时禁止再走“直接造 `G_I` 空项”的旧语义。

固定流程：

1. 先查 resident directory
2. miss 后查 BF
3. 若 `BF negative`：
   - 直接 materialize 一个 resident empty entry
   - 不访问 backstore
4. 若 `BF positive`：
   - 建立 `fillPending`
   - 同 PA 后续请求进入 waiting queue
   - 经 `EPBackend + MetaRNF` 发 timed backstore read
5. refill 返回后重放等待队列

### 7.3 Refill 返回

- backstore hit：装入 resident，然后重放请求
- backstore miss：装入逻辑 empty resident entry，然后重放请求

### 7.4 Commit 与删除

- 普通 grant：仍在 `processClear()` 提交
- local upgrade：仍在 `processOuterUpgradeDone()` 提交
- 提交到非空项：resident entry 更新并置 `residentDirty=1`
- 提交到 `G_I`：resident entry 保留为 `G_I + residentDirty=1` tombstone，直到 delete ack

### 7.5 Victim eviction

victim 选择规则：

- `pinned=1`：不可驱逐
- `residentDirty=0`：可直接丢弃 resident copy
- `residentDirty=1`：必须先写回/删除 backstore，ack 后才可释放

以下情况必须 pin：

- 同 PA 存在 outstanding request
- 同 PA 存在 pending requester
- 同 PA 存在 fillPending
- 同 PA 存在 wbPending
- 同 PA 处于 delete-pending tombstone

---

## 8. 时延模型（2GHz）

固定设计值如下：

| 路径 | 时延 |
|---|---:|
| TD$ hit read | 500 ticks |
| TD$ hit modify | 1000 ticks |
| TD$ miss + L3 hit refill | 8000 ticks |
| TD$ miss + DRAM row-hit refill | 25492 ticks |
| TD$ miss + DRAM closed-row refill | 39652 ticks |
| Dirty writeback on L3 hit | 7500 ticks |

这些值适用于：

- resident directory SRAM probe / modify
- MetaRNF 经 HN-F/L3 的 timed backstore access
- private DRAM row-hit / closed-row 一阶模型

---

## 9. 协议不变量

实现中必须始终满足：

1. **UBCC 是唯一排序点**
2. **同 PA 在 fill/writeback 期间仍保持单飞行**
3. **`G_E/G_M` 下 `sharersMask` 必须 one-hot**
4. **`owner` 只能由 `MESI + sharersMask` 推导，不再独立存储**
5. **`G_I + residentDirty=0` 是唯一 empty 编码**
6. **`G_I + residentDirty=1` 是唯一 delete-pending tombstone 编码**
7. **BF 只能剪枝，不能提供协议真值**
8. **删除未 ack 前不得清 BF**

---

## 10. 代码落点

### 10.1 `UBCCController`

需要完成：

- `_directory` 从“全量真值”改为 resident cache
- 删除 `ownerNode`/`nextReqId` 依赖
- `ensureDirEntry()` 语义拆分为 resident lookup + miss handling
- resident miss 时引入 `fillPending + waiting queue + replay`
- `commitIntendedResult()` 只写 resident，并维护 `residentDirty/BF`
- eviction / delete 走异步 backstore write/delete

### 10.2 `EPBackend`

需要完成：

- 提供目录存储服务入口
- 负责 `line_pa -> metadata_private_range` 映射
- 通过 MetaRNF 发 timed metadata access
- 回调 UBCC 完成 fill/writeback/delete ack

### 10.3 `MetaRNF`

需要新增：

- 节点内 metadata 专用 RNF
- 与 EP-RNF 同级
- 只面向 backstore metadata 事务

### 10.4 `CHI_ubcc_framework.py`

需要完成：

- 为每节点新增 `metadata_private_range`
- 将该地址窗口纳入本节点 HN-F / private DRAM 路由
- 保证 CPU/RNF 普通负载不可访问该范围
- 暴露 resident SRAM 与 BF 切片参数

---

## 11. 最终结论

最终设计已经固定为：

- **24b 统一 epoch**
- **56b resident canonical entry（7B/entry）**
- **42b backstore entry**
- **无 ownerCode / 无 Valid / 无 nextReqId**
- **residentDirty 与 protoDirty 分离**
- **resident dir + BF 共享 512KB 单 buffer**
- **MetaRNF 经 HN-F(L3) 访问 private DRAM backstore**
- **Counting BF 推荐，delete ack 后才清 BF**
- **backstore 使用开放寻址 + Robin Hood delete**

这就是后续实现与文档同步的唯一基线。

---

## 12. 待用户确认的唯一未定参数

1. **请确认 512KB 中给 BF 固定切多少容量**：当前设计已固定公式，但 BF 固定切片值尚未被最终拍板。若你希望我直接固化到文档实现基线，我建议你在 `64 KiB` 与 `128 KiB` 中二选一，或给出其他精确字节数。
