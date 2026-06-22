# Final Bloom Filter redesign

## 1. 背景与冻结决策

本设计替换当前 `ResidentDir` 中的 **counting bloom filter**，改为 **16 路分组 plain bloom filter + 单组 shadow 重建**。

已冻结决策如下：

- `k = 4` 个 hash function
- `bloomRemove()` 不清 bit；仅执行 `staleDeleteCount[group]++`
- 重建触发采用 **双触发**：周期触发 + stale 阈值触发
- 分组方案采用 **16 groups**，每组 **4KB**，总 active BF **64KB**
- group 归属采用 `hash(line_pa) % 16`
- 重建期间采用 **dual-write**：active[group] 与 shadow[group] 同时接收更新
- `activeCount[group]` 统计“逻辑应存在于 BF 中的活跃项”
- 重建必须同时扫描：
  1. ResidentDir resident entries
  2. `UBCCController::_backstore`

额外约束：由于扫描 resident + backstore 的冻结成本不可接受，因此 **不冻结查询路径**；查询始终读取 active BF，重建只对单 group 做 shadow 构建与 swap。

---

## 2. 当前基线（需替换的实现）

当前代码仍是 **3-hash / 4-bit counting bloom**：

- `ResidentDir.hh:50-52`：`DefaultBloomBytes=64KB`、`BloomHashes=3`、`CounterBits=4`
- `ResidentDir.hh:97-99`：`bloomCounterIndex/Read/Write`
- `ResidentDir.hh:115`：`std::vector<uint8_t> _bloomCounters`
- `ResidentDir.cc:71-72`：`_bloomCounterCount = _bloomBytes * 2`，按 4-bit counter 初始化
- `ResidentDir.cc:119-148`：counter 索引/读写实现
- `ResidentDir.cc:346-393`：`bloomMayContain/bloomInsert/bloomRemove/bloomClear`
- `ResidentDir.cc:521-531`：`clear()` 通过 `bloomClear()` 清空整个 BF

当前 UBCC 与 backstore 的 BF 交互点：

- `UBCCController.cc:179`：resident miss 前做 `bloomMayContain()`
- `UBCCController.cc:2251-2252`：commit 非 `G_I` 时执行 `bloomInsert()`
- `UBCCController.cc:2407-2408`：backstore write-ack 后写 `_backstore` 并 `bloomInsert()`
- `UBCCController.cc:2424-2425`：backstore delete-ack 后 `erase` 并 `bloomRemove()`
- `UBCCController.cc:2482-2483`：测试 seed backstore 时 `bloomInsert()`
- `UBCCController.cc:2506-2507`：测试 seed resident 时 `bloomInsert()`
- `UBCCController.hh:609`：`_backstore` 当前为 UBCC 私有 map

配置基线：

- `EPBackend.py:18`：`ubcc_bf_bytes = 64 * 1024`
- `CHI_ubcc_framework.py:168`：默认环境变量 `UBCC_BF_BYTES=65536`

---

## 3. 新设计总览

### 3.1 参数表

| 参数 | 值 |
|---|---:|
| Total BF size | 64KB = 524,288 bits |
| Groups (N) | 16 |
| Per-group size | 4KB = 32,768 bits |
| Hash functions (k) | 4 |
| Shadow buffer | 4KB，仅重建单 group 时动态分配 |
| Group function | `group = hash(line_pa) % 16` |
| Periodic trigger | 每组每 1000 次 `bloomInsert()` |
| Threshold trigger | `staleDeleteCount[group] / activeCount[group] > 0.25` |
| 65K entries FPR | 约 2.4% |
| 16K/group FPR | 约 0.15% |

### 3.2 结构

- active BF：`16 × 4KB`
- shadow BF：仅在某一组重建时为该组分配 `4KB`
- 每组元数据：
  - `activeCount[group]`
  - `staleDeleteCount[group]`
  - `insertCount[group]`
  - `reconstructing[group]` 或单一 `_reconstructingGroup`

### 3.3 语义

- `bloomInsert(line_pa)`：对所属 group 的 4 个 bit 置 1
- `bloomMayContain(line_pa)`：检查所属 group 的 4 个 bit 是否全为 1
- `bloomRemove(line_pa)`：**不改 bit**，只累加 `staleDeleteCount[group]`
- `triggerReconstruct(group)`：重建该组 shadow BF，结束后与 active BF 原子 swap

---

## 4. 数据结构设计

### 4.1 `ResidentDir.hh` 改造

基线位置：

- 常量：`ResidentDir.hh:48-52`
- BF API：`ResidentDir.hh:65-68`
- BF helper：`ResidentDir.hh:96-99`
- BF 字段：`ResidentDir.hh:103-115`

目标修改：

1. 删除 counting-BF 专用定义：
   - 删除 `BloomHashes = 3` 旧语义，改为 `BloomHashes = 4`
   - 删除 `CounterBits`
   - 删除 `_bloomCounterCount`
   - 删除 `_bloomCounters`
   - 删除 `bloomCounterIndex/Read/Write`

2. 新增分组 plain-BF 字段：

   ```cpp
   static constexpr int BloomGroups = 16;
   static constexpr size_t BloomGroupBytes = 4 * 1024;
   static constexpr size_t BloomGroupBits = BloomGroupBytes * 8;
   static constexpr int BloomHashes = 4;
   static constexpr uint64_t ReconstructInsertPeriod = 1000;
   static constexpr double ReconstructStaleRatio = 0.25;

   std::array<std::array<uint8_t, BloomGroupBytes>, BloomGroups> _bloomBits;
   std::array<uint32_t, BloomGroups> _groupActiveCount;
   std::array<uint32_t, BloomGroups> _groupStaleDeleteCount;
   std::array<uint32_t, BloomGroups> _groupInsertCount;

   std::unique_ptr<std::array<uint8_t, BloomGroupBytes>> _shadowBF;
   int _reconstructingGroup;
   ```

3. 新增 helper / API：

   ```cpp
   int bloomGroup(uint64_t line_pa) const;
   size_t bloomBitIndex(uint64_t line_pa, int hash_idx) const;
   bool shouldReconstructGroup(int group) const;
   void reconstructGroup(int group, const std::vector<uint64_t> &backstorePas);
   double estimateFPR(int group) const;
   ```

4. 保留外部调用接口名：
   - `bloomInsert()`
   - `bloomMayContain()`
   - `bloomRemove()`

这样 UBCC 侧原有调用点 `UBCCController.cc:179, 2252, 2408, 2425, 2483, 2507` 不需要全面改签名。

### 4.2 `_backstore` 访问边界

由于 `_backstore` 位于 `UBCCController.hh:609`，`ResidentDir` 不能直接访问它。因此实现上应采用：

- `UBCCController::wakeup()` 或其私有 helper 负责遍历 `_backstore`
- 将匹配 group 的 `linePa` 收集成 `std::vector<uint64_t> backstorePas`
- 调用 `ResidentDir::reconstructGroup(group, backstorePas)`

这比把 `_backstore` 直接暴露给 `ResidentDir` 更干净，也避免 header 相互耦合。

---

## 5. 哈希与分组规则

### 5.1 group 选择

采用 line 粒度：

```cpp
group = splitmix64(line_pa >> 6) % 16;
```

理由：

- 当前所有 BF 调用点传入的本来就是 line 地址语义
- 比 `line_pa % 16` 分布更均匀
- 可直接复用 `ResidentDir.cc:109-116` 已有 `splitmix64()`

### 5.2 4 个 hash bit

每组内部 bit 索引：

```cpp
bit = splitmix64((line_pa >> 6) ^ seed[i]) % 32768;
```

建议使用 4 个固定 seed；当前 `ResidentDir.cc:121-125` 只有 3 个 seed，需要扩成 4 个。

---

## 6. 核心操作语义

### 6.1 `bloomInsert(line_pa)`

1. 计算 `group`
2. 对该 group 的 4 个 bit 置 1
3. 若该组正在重建，shadow BF 对同样 4 个 bit 也置 1
4. `groupInsertCount[group]++`
5. 若插入代表逻辑活跃项，则更新 `groupActiveCount[group]`
6. 若满足周期或 stale 阈值，则请求重建

### 6.2 `bloomMayContain(line_pa)`

1. 只读取 active BF
2. 仅检查所属 group 的 4 个 bit
3. 任一 bit 为 0 则返回 false；否则 true

### 6.3 `bloomRemove(line_pa)`

1. 不修改 active BF bit
2. 若该组正在重建，shadow BF 同样不清 bit
3. `groupStaleDeleteCount[group]++`
4. `groupActiveCount[group]` 仅对“逻辑活跃项删除”做减计数；不能下溢

这里的关键语义是：**删除只制造 staleness，不试图在线精确恢复 bit**。

---

## 7. 重建触发策略

### 7.1 周期触发

每组独立：

```cpp
groupInsertCount[group] % 1000 == 0
```

则允许发起该组重建。

### 7.2 stale 比例触发

当：

```cpp
groupActiveCount[group] > 0 &&
double(groupStaleDeleteCount[group]) / double(groupActiveCount[group]) > 0.25
```

则发起重建。

### 7.3 调度原则

- 一次只重建一个 group
- 若已有 `_reconstructingGroup != -1`，其余组仅记待重建意图，等待后续 `wakeup()` 再尝试
- `UBCCController.cc:117-121` 的 `wakeup()` 是当前最自然的周期检查入口

---

## 8. 重建算法

## 8.1 关键实现修正

用户伪码要求扫描 resident + backstore，但当前实现允许 **resident 与 backstore 同时持有同一 PA**：

- `onBackstoreFillComplete()` 后 resident 可能在、backstore 也在：`UBCCController.cc:2361-2396`
- `onBackstoreWriteAck()` 明确把 resident 元数据写入 `_backstore`：`UBCCController.cc:2400-2418`

因此重建时若直接“resident 扫一遍 + `_backstore` 全扫一遍”，会 **重复插入同一 PA**，导致：

- `activeCount` 高估
- bit set 过密
- FPR 偏高

所以重建必须对 resident/backstore 做去重。

### 8.2 正确的重建准则

1. resident 扫描纳入条件：`entry.state != G_I`
2. backstore 扫描纳入条件：
   - `entry.state != G_I`
   - 该 `linePa` 当前 **不在 resident 中**
3. `G_I + residentDirty` tombstone **不作为逻辑活跃项重插**；它们只通过 stale 机制等待后续重建清除

这与已冻结的“`activeCount` 统计逻辑活跃项”一致，也避免把 tombstone 重新灌回 BF。

### 8.3 重建伪码

```cpp
reconstructGroup(i, backstorePas):
  1. allocate shadowBF[4KB], clear to 0
  2. residentSeen = {}
  3. rebuiltActiveCount = 0

  4. // Scan ResidentDir
     for each used resident slot:
       pa = _keys[slot]
       if bloomGroup(pa) != i: continue
       entry = decodeEntry(pa)
       if entry.state == G_I: continue
       bloomInsertShadowOnly(i, pa)
       residentSeen.insert(pa)
       rebuiltActiveCount++

  5. // Scan Backstore (UBCC 传入的 vector)
     for pa in backstorePas:
       if residentSeen.contains(pa): continue
       bloomInsertShadowOnly(i, pa)
       rebuiltActiveCount++

  6. // Reconstruction window 中的并发写入由常规 bloomInsert/bloomRemove dual-write 处理

  7. activeBF[i] <-> shadowBF[i]
  8. groupActiveCount[i] = rebuiltActiveCount
  9. groupStaleDeleteCount[i] = 0
 10. groupInsertCount[i] = 0
 11. free shadowBF; _reconstructingGroup = -1
```

### 8.4 dual-write 语义

重建期间：

- `bloomInsert(pa)`：
  - 正常写 active[group]
  - 若 `group == _reconstructingGroup`，同时写 shadow[group]
- `bloomRemove(pa)`：
  - 正常只做 stale 计数
  - 若 `group == _reconstructingGroup`，shadow 也不清 bit，仅共享同一 stale 计数语义

因此 swap 后不会丢失重建窗口中的新插入项。

### 8.5 并发保证

查询路径始终读取 active BF：

- 无 group freeze
- 无 query stall
- 无切换期 false negative

在 gem5 当前单线程事件模型下，不需要额外锁；只需保证 swap 是一次性状态切换。

---

## 9. `activeCount` / `staleDeleteCount` 语义

### 9.1 `activeCount[group]`

统计“逻辑应存在于 BF 的活跃 PA 数量”，即：

- resident 中 `state != G_I` 的条目
- 或仅存在于 `_backstore` 中的非 `G_I` 条目

不计入：

- resident empty
- `G_I` tombstone
- 纯 stale bit 残留

### 9.2 `staleDeleteCount[group]`

在以下路径增长：

- `UBCCController.cc:2422-2425` 删除 backstore entry 时
- 其他未来显式 remove 路径

该计数不试图精确等于“实际多余 bit 数”，而是作为 **早期重建触发器**。

---

## 10. 估算 FPR

`ResidentDir` 可增加 `estimateFPR(group)`，基于该组 set-bit 密度做近似：

```cpp
p = bits_set / 32768.0;
fpr ~= pow(p, 4);
```

用途：

- 调试日志
- test/instrumentation
- 重建效果观测

不参与正确性判定。

---

## 11. 逐文件修改目录（含当前代码行号）

### 11.1 `gem5/src/mem/ruby/protocol/chi/ep/ResidentDir.hh`

**当前基线**

- `48-52`：BF 常量仍为 counting-BF 形式
- `65-68`：对外 BF API
- `97-99`：counter helper 声明
- `103-115`：`_bloomCounterCount` / `_bloomCounters`

**需要修改**

- 把 `BloomHashes` 从 3 改为 4
- 删除 `CounterBits`
- 删除 counter helper
- 删除 `_bloomCounterCount`、`_bloomCounters`
- 新增 16-group bit-array、per-group 元数据、shadow rebuild 状态
- 新增 `bloomGroup()`、`bloomBitIndex()`、`shouldReconstructGroup()`、`reconstructGroup()`、`estimateFPR()`

**语义意图**

- 让 `ResidentDir` 成为 BF 存储与重建执行者
- 不让它直接拥有 `_backstore` 的所有权

### 11.2 `gem5/src/mem/ruby/protocol/chi/ep/ResidentDir.cc`

**当前基线**

- `53-74`：构造函数按 counting-BF 初始化
- `109-116`：`splitmix64()` 可复用
- `119-148`：counter index/read/write
- `346-393`：当前 bloom 核心实现
- `521-531`：`clear()`

**需要修改**

- 构造函数改为初始化 16 个 group bit-array 和计数器
- 保留 `splitmix64()`，但新增 group/hash 索引逻辑
- 删除 counter read/write 实现
- 重写 `bloomMayContain()` 为 4-bit plain BF 检查
- 重写 `bloomInsert()` 为 set-bit + dual-write + trigger bookkeeping
- 重写 `bloomRemove()` 为 stale-count bookkeeping
- 重写 `bloomClear()` 为清空全部 group bit-array 与元数据
- 新增 `reconstructGroup()` 与 `estimateFPR()`
- `clear()` 需要同时把 `_reconstructingGroup` 置回 `-1`

**语义意图**

- 用“便宜写入 + 偶发重建”替代“每次删除都维护 counter”

### 11.3 `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh`

**当前基线**

- `205`：构造参数默认 BF bytes
- `609`：`_backstore` 私有存储

**需要修改**

- 新增一个私有 helper，例如：

  ```cpp
  void maybeReconstructBloomGroup();
  void collectBackstorePasForGroup(int group, std::vector<uint64_t>& out) const;
  ```

- 不需要改变 `_backstore` 所有权

**语义意图**

- 让 UBCC 负责 backstore 扫描与调度，ResidentDir 负责本地 BF 重建

### 11.4 `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc`

**当前基线**

- `117-121`：`wakeup()` 仅清 tombstone
- `179`：miss 路径先查 BF
- `2251-2252`：commit 非 `G_I` 时 BF insert
- `2336-2343`：`lookupBackstore()`
- `2361-2396`：backstore fill 完成后 resident/backstore 可并存
- `2400-2418`：backstore write ack 维护 `_backstore` + BF insert
- `2422-2441`：backstore delete ack 维护 `_backstore` + BF remove
- `2475-2484`、`2487-2510`：test seed helper 也会更新 BF

**需要修改**

- `wakeup()` 在 `cleanupTombstones()` 后增加 group rebuild 检查
- 新增遍历 `_backstore` 的 helper，只收集目标 group 的 PA
- 调用 `_directory.shouldReconstructGroup(group)`
- 调用 `_directory.reconstructGroup(group, backstorePas)`
- 保持现有 `bloomInsert/bloomRemove/bloomMayContain` 调用点不变

**语义意图**

- 不入侵主协议路径，只在 wakeup/周期点做维护

### 11.5 `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.py`

**当前基线**

- `18`：说明文字仍写 `counting bloom bytes`

**需要修改**

- 改注释为 `resident dir bloom filter bytes`

**语义意图**

- 避免配置语义与实现漂移

### 11.6 `gem5/configs/ruby/CHI_ubcc_framework.py`

**当前基线**

- `168`：默认 `UBCC_BF_BYTES=65536`

**需要修改**

- 无功能变更
- 可补充注释说明该 64KB 对应 16×4KB 分组 plain BF

---

## 12. SRAM 预算

| 项目 | 大小 |
|---|---:|
| Active BF | 16 × 4KB = 64KB |
| Shadow BF | 4KB，单组重建时动态分配 |
| ResidentDir packed entries | 448KB |
| Group metadata | 约 128B |
| 总计 | 512KB + 4KB dynamic |

注：这里与当前 `ResidentDir` 的逻辑容量划分保持一致：`64KB BF + 448KB resident entries = 512KB`。

---

## 13. 测试计划

### 13.1 定向功能测试

1. **FPR 测量**
   - 每组插入 `N` 个条目
   - 对随机未插入 PA 做 probe
   - 记录 `estimateFPR()` 与实测 FPR

2. **重建恢复能力**
   - 人为污染某组 bit
   - 触发重建
   - 观察 FPR 回落到基线

3. **dual-write 正确性**
   - 在 group 重建窗口内并发执行 insert/remove
   - swap 后验证新插入项仍 `bloomMayContain()==true`

4. **无 false negative**
   - 对所有逻辑活跃项检查 `bloomMayContain()==true`

5. **backstore-only 覆盖**
   - 构造仅在 `_backstore` 中、不在 resident SRAM 中的条目
   - 触发重建
   - 验证重建后这些 PA 仍命中 BF

6. **resident/backstore 去重**
   - 构造同时 resident+backstore 存在的 PA
   - 重建后检查 `activeCount` 未双计数

### 13.2 现有 E2E 关联

- `test_e2e.py:526-537`：TC23 已覆盖 BF 假阳性容忍
- `test_e2e.py:875-889`：TC45 已覆盖 bloom/fill 压力
- 全量要求：`TC1-54` 回归通过

---

## 14. 风险与缓解

### 风险 1：resident/backstore 双计数

**原因**：当前代码允许 resident 与 `_backstore` 同时持有相同 PA。

**缓解**：重建时 resident 先扫，backstore 只补“当前不 resident”的 PA。

### 风险 2：把 tombstone 重新灌回 BF

**原因**：若按 `residentDirty` 误把 `G_I` tombstone 当活跃项插回 shadow，会增加长期假阳性。

**缓解**：`activeCount` 与重建纳入条件统一使用 `state != G_I`。

### 风险 3：重建期间丢插入

**原因**：shadow 只扫描快照而不接收窗口期更新。

**缓解**：对 `_reconstructingGroup` 执行 dual-write。

### 风险 4：过于频繁重建

**原因**：阈值过低或热点 group 插入率过高。

**缓解**：

- 单次只重建一个 group
- 周期阈值固定 1000
- stale 阈值 25%
- 后续可在日志中观测每组 rebuild 频率再微调

### 风险 5：查询冻结代价过大

**原因**：重建需要扫描 resident + backstore。

**缓解**：查询永远读 active BF，不冻结 miss path。

---

## 15. 建议的落地顺序

1. 先改 `ResidentDir.hh/.cc`，完成 grouped plain BF 本体
2. 再改 `UBCCController.hh/.cc`，接上 wakeup 重建调度与 backstore 扫描
3. 最后补 `EPBackend.py` 注释与测试/日志

该顺序风险最低，因为 BF 核心逻辑与 UBCC orchestration 能分步验证。
