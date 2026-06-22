# Backstore + BF + MetaRNF DRAM-native 完整设计文档

## 1. 背景、冻结决策与目标

本文冻结 Backstore / Bloom Filter / MetaRNF 的 DRAM-native 方案，不再保留软件 `unordered_map` backstore 作为功能路径。

已冻结决策：

- **Q6**：两级组织：**page directory（片上）+ compact entry pages（DRAM）**
- **Q7**：可插拔 `BackstoreOrganization` 接口，至少支持 **Schema A（append-friendly）** 与 **Schema C（bucketized）**
- **Q8**：片上预算固定为 **64KB**：**60KB BF + 4KB index**；16 groups，每组 **256B GroupIndex + 3.75KB BF**
- **Q9**：**per-line single-flight + global window N=8**；允许乱序完成；dual-key：**PA scoreboard + slot/txnId 内部跟踪**
- **Page size**：可配置，默认 **256B**；测试模式支持 **64B**
- **Entry format**：C++ API 用 **24B expanded entry**；DRAM 页内存储 **12B compact entry**
- **Resident/DRAM coexistence**：允许；**resident authoritative**，DRAM 为 shadow copy
- **MetaRNF slot key**：PA 做串行化键，slot/txnId 做 CHI 事务跟踪键

核心目标：

1. 删除当前软件 `_backstore` 真值源
2. 让 BF 只承担“负过滤器”职责，不承担真值存储
3. 让 backstore 元数据真实落到 metadata-private DRAM
4. 保持现有 UBCC resident/offload 生命周期与 CHI 时序兼容
5. 为 Schema A/C 对比实验提供稳定抽象边界

---

## 2. 设计不变式与非目标

### 2.1 设计不变式

- **I1. resident authoritative**：若 resident 与 DRAM 同时存在同一 PA，以 resident 为准
- **I2. DRAM shadow only**：DRAM backstore 只保存可恢复元数据，不是并发提交源
- **I3. BF never proves presence**：BF positive 只表示“可能存在”，必须继续查 resident/index/DRAM page
- **I4. per-line single-flight**：同一 metadata PA 的 page I/O 同时最多 1 个在飞请求
- **I5. bounded global concurrency**：MetaRNF 同时最多 8 个 metadata page request
- **I6. weakly-consistent reconstruct**：重建期间查询不冻结；依赖 dual-write + atomic swap 避免 false negative
- **I7. delete is tombstone-first**：删除先写 tombstone，不做在线 page compaction

### 2.2 非目标

- 不在本轮引入 page compaction GC 线程
- 不在本轮做跨 group rebalance
- 不在本轮把 BF 变成 counting BF
- 不在本轮让 EPBackend 保留任意软件 fallback 真值路径

---

## 3. 当前基线与必须删除的旧路径

当前代码基线仍是“resident + counting BF + software `_backstore` + MetaRNF 单飞行 stub”。

### 3.1 ResidentDir / BF 基线

- `ResidentDir.hh:48-52`：`DefaultBloomBytes=64KB`、`BloomHashes=3`、`CounterBits=4`
- `ResidentDir.hh:97-99`：counter helper 声明
- `ResidentDir.hh:115`：`std::vector<uint8_t> _bloomCounters`
- `ResidentDir.cc:71-72`：`_bloomCounterCount = _bloomBytes * 2`
- `ResidentDir.cc:346-393`：当前 counting BF 的 mayContain/insert/remove/clear

### 3.2 UBCC software backstore 基线

- `UBCCController.hh:248`：现有 `BackstoreEntry` 为轻量软件镜像项
- `UBCCController.hh:609`：`std::unordered_map<uint64_t, BackstoreEntry> _backstore`
- `UBCCController.cc:2335-2344`：`lookupBackstore()`
- `UBCCController.cc:2400-2418`：`onBackstoreWriteAck()` 直接写 `_backstore`
- `UBCCController.cc:2422-2441`：`onBackstoreDeleteAck()` 直接 `erase`
- `UBCCController.cc:2475-2484`：`debugSeedBackstoreForTest()` 直接 seed `_backstore`

### 3.3 EPBackend fallback 基线

- `EPBackend.cc:173-181`：当前 `metadataBackstorePa()` 是“line→64B slot”固定映射
- `EPBackend.cc:184-229`：当前编码是单条 64B metadata line，不支持页式组织
- `EPBackend.cc:1026-1030`：`issueBackstoreRead()` 在无 MetaRNF 时走 `_ubcc->lookupBackstore()` fallback
- `EPBackend.cc:1047-1049`：MetaRNF decode 失败后再次退回 `_ubcc->lookupBackstore()`
- `EPBackend.cc:1061-1064`、`1086-1088`：write/delete 在无 MetaRNF 时直接本地 ack

### 3.4 MetaRNF 基线

- `MetaRNFController.hh:72-74`：`_requestInFlight + _pending`，本质单飞行
- `MetaRNFController.cc:146-197`：`sendReadOnce()/sendWriteUnique()` 被 `_requestInFlight` 限死
- `MetaRNFController.cc:238-273`：complete 后才释放全局 in-flight

### 3.5 本文要求删除 / 修改

**DELETE**

- `UBCCController::_backstore`
- `UBCCController::lookupBackstore()`
- EPBackend `issueBackstoreRead()` 中的软件 fallback 路径

**MODIFY**

- `UBCCController::onBackstoreWriteAck()`：改为处理 DRAM page 写完成后的 resident 收尾
- `UBCCController::onBackstoreDeleteAck()`：改为 tombstone delete 完成后的 resident 收尾
- `ResidentDir`：新增 `reconstructGroup()`
- `MetaRNFController`：新增 multi-flight + scoreboard
- 新增 `BackstoreOrganization` 抽象与 Schema A / Schema C

---

## 4. 总体架构概览

### 4.1 组件分工

1. **ResidentDir**
   - 继续保存 resident 元数据真值
   - 持有 60KB grouped plain BF
   - 持有 4KB on-chip `GroupIndex`
   - 负责单 group reconstruct

2. **BackstoreOrganization**
   - 负责“PA ↔ group/page/slot”的组织策略
   - 负责 compact entry 编解码
   - 负责 Schema A/C 差异

3. **EPBackend**
   - 负责 UBCC 与 BackstoreOrganization / MetaRNF 的桥接
   - 负责 page 级 read-modify-write
   - 不再提供软件 `_backstore` fallback

4. **MetaRNFController**
   - 负责真实 CHI metadata page I/O
   - 提供 8-flight、per-PA single-flight、OOO completion

### 4.2 查询路径总图

```text
UBCC resident miss
  -> ResidentDir.bloomMayContain(pa)
     -> negative: 直接按“不存在 backstore”处理
     -> positive: 读取 GroupIndex + BackstoreOrganization 定位 page
          -> MetaRNF 读 1~N 个 metadata pages
          -> decode compact entries
          -> 若 resident 已持有同 PA，resident 覆盖 DRAM
          -> onBackstoreFillComplete(pa, found, entry)
```

### 4.3 写回路径总图

```text
resident victim / writeback-needed
  -> snapshot resident expanded entry
  -> BackstoreOrganization::upsert()
  -> EPBackend 发起 page read-modify-write
  -> MetaRNF write page(s)
  -> UBCC.onBackstoreWriteAck(pa)
```

### 4.4 删除路径总图

```text
resident tombstone eviction / delete
  -> BackstoreOrganization::tombstone(pa)
  -> EPBackend 读目标 page 并置 deleted flag
  -> MetaRNF 写回 page
  -> ResidentDir.bloomRemove(pa) 仅记 stale
  -> UBCC.onBackstoreDeleteAck(pa, existed)
```

---

## 5. `BackstoreOrganization` 抽象与 Schema A / Schema C

### 5.1 抽象目标

接口层必须把以下逻辑从 UBCC/EPBackend 中剥离：

- group 归属
- page directory 选择
- page 链表布局
- entry 插入/更新/删除策略
- compact/expanded 编码转换
- 组扫描顺序

### 5.2 建议接口

```cpp
class BackstoreOrganization
{
  public:
    virtual ~BackstoreOrganization() = default;

    virtual uint32_t groupForPa(uint64_t pa) const = 0;
    virtual uint64_t pageIdForPa(uint64_t pa) const = 0;
    virtual std::vector<uint64_t> candidatePagesForLookup(
        uint64_t pa, const GroupIndex &idx) const = 0;
    virtual bool lookupInPage(uint64_t pa, const BackstorePage &page,
                              BackstoreEntry &out) const = 0;
    virtual UpdatePlan planUpsert(uint64_t pa, const BackstoreEntry &entry,
                                  const GroupIndex &idx) const = 0;
    virtual UpdatePlan planDelete(uint64_t pa, const GroupIndex &idx) const = 0;
    virtual std::vector<uint64_t> scanGroupPages(const GroupIndex &idx) const = 0;
    virtual CompactBackstoreEntry compress(const BackstoreEntry &entry) const = 0;
    virtual BackstoreEntry expand(const CompactBackstoreEntry &entry) const = 0;
};
```

### 5.3 Schema A：append-friendly

特征：

- group 内维护 append 顺序 page 链
- 新 entry 优先写 head/tail 可用页
- delete 只打 tombstone
- reconstruct/GC 再清理碎片

适用：

- 写多、删多、研究 append 行为
- 更容易 bring-up

代价：

- lookup 可能沿链走更久
- tombstone 累积更快

### 5.4 Schema C：bucketized

特征：

- group 内先按 bucket 定位，再在 bucket page 链中查找
- page_directory[4] 保存 hottest / first bucket pages
- 链更短，lookup 更稳定

适用：

- lookup 稳定性优先
- 适合高碰撞实验

代价：

- 插入与 split/overflow 决策更复杂
- 实现复杂度高于 Schema A

### 5.5 默认策略

- **默认 bring-up：Schema A**
- **ablation：Schema A vs Schema C**
- 对 UBCC/ResidentDir/MetaRNF 暴露同一 API，不允许上层逻辑分叉

---

## 6. DRAM-native Backstore 与片上索引设计

### 6.1 容量切分

- 总片上预算：**64KB**
- BF：**60KB**
- Index：**4KB**
- Group 数：**16**
- 每组：**3.75KB BF + 256B GroupIndex**

### 6.2 Page 组织

- 默认 page size：**256B**
- 测试 page size：**64B**
- 256B page：24B 头 + 232B entry payload → **19 个 12B compact entries**
- 64B page：24B 头 + 40B payload → **3 个 12B entries + 4B slack**

### 6.3 两级索引

1. **一级：GroupIndex（片上）**
   - 保存前 4 个 page pointer
   - 保存计数器与 existence summary
   - 保存 reconstruct 所需统计量

2. **二级：DRAM page chain**
   - 真正存 entry
   - 超过前 4 个页面后经 `next_page_ptr` 扩展

### 6.4 resident/DRAM 共存语义

- resident 中 `G_S/G_E/G_M` 或 `G_I + residentDirty` 表示本地 authoritative 状态
- DRAM 中相同 PA 仅作为 shadow
- reconstruct 扫 DRAM 时若 `ResidentDir.has(pa)`，**跳过 DRAM 条目**
- delete 不要求立刻清 DRAM page；tombstone 可与 resident 消失不同步

### 6.5 编码语义

- C++ API 一律使用 `BackstoreEntry`（24B expanded）
- DRAM page 一律使用 `CompactBackstoreEntry`（12B compact）
- 压缩/解压完全由 `BackstoreOrganization` 负责

---

## 7. BF、GroupIndex 与重建设计

### 7.1 BF 角色

新 BF 是 **grouped plain BF**，不再支持精确 remove。

- `bloomMayContain(pa)`：负过滤
- `bloomInsert(pa)`：置位 + 统计插入
- `bloomRemove(pa)`：**不清 bit**，只增加 `stale_delete_count`

### 7.2 group 归属

继续按 line 粒度 hash：

```cpp
group = splitmix64(line_pa >> 6) % 16;
```

### 7.3 插入规则

以下情形执行 `bloomInsert(pa)`：

- resident 中形成有效非 `G_I` 条目
- DRAM backstore upsert 成功
- debug seed backstore / resident
- reconstruct dual-write 窗口中的新写入

### 7.4 删除规则

以下情形执行 `bloomRemove(pa)`：

- DRAM tombstone delete 完成
- resident 与 DRAM 逻辑上都不再需要该 PA

注意：

- `bloomRemove()` 不会清 bit
- 所有 stale 清理由 `reconstructGroup()` 统一完成

### 7.5 reconstruct 触发

双触发：

- **periodic**：`insert_count % Period == 0`
- **stale**：`stale_delete_count / live_count > Threshold`

建议默认：

- `Period = 1024`
- `Threshold = 0.25`

### 7.6 reconstruct 一致性模型

- 只重建单 group
- 查询始终读 active BF
- reconstruct 期间该 group 新 insert dual-write 到 shadowBF
- swap 后更新 `stale_delete_count=0`
- `insert_count` 用 `startEpoch` 做弱一致性回填

---

## 8. 端到端消息流与软件接口语义

### 8.1 Backstore Read（resident miss + BF positive）

```text
UBCCController::handleResidentMiss
  -> ResidentDir::bloomMayContain(pa)
  -> EPBackend::issueBackstoreRead(pa)
  -> BackstoreOrganization::candidatePagesForLookup(pa, groupIndex)
  -> MetaRNFController::issueRead(page_pa, cb)
  -> HN-F -> local metadata DRAM
  -> page callback decode compact entries
  -> 找到 entry 或链尾失败
  -> UBCCController::onBackstoreFillComplete(pa, found, entry)
```

关键变化：

- 不再允许 `_ubcc->lookupBackstore()` fallback
- MetaRNF decode 失败即按 page miss / invalid page 处理，而不是回退软件 map

### 8.2 Backstore Write（resident writeback / eviction）

```text
UBCCController::scheduleBackstoreWrite(pa)
  -> EPBackend snapshot resident expanded entry
  -> BackstoreOrganization::planUpsert(pa, entry, groupIndex)
  -> 若目标 page 不在片上索引，先读 page / 可能分配新 page
  -> 改写 compact entry / page header
  -> MetaRNF issueWrite(page_pa)
  -> UBCCController::onBackstoreWriteAck(pa)
```

语义：

- 写 ack 不再表示“软件 map 已写入”
- 仅表示“DRAM page 更新完成，可清 residentDirty/wbPending”

### 8.3 Backstore Delete（tombstone）

```text
UBCCController::scheduleBackstoreDelete(pa)
  -> BackstoreOrganization::planDelete(pa, groupIndex)
  -> 读目标 page
  -> 找到 entry 后打 deleted=1 tombstone
  -> MetaRNF issueWrite(page_pa)
  -> ResidentDir::bloomRemove(pa)
  -> UBCCController::onBackstoreDeleteAck(pa, existed)
```

语义：

- delete ack 只表示 tombstone 已落 DRAM
- page 不做在线压缩
- `existed=false` 合法，表示 BF 假阳性或旧 tombstone 已不存在

### 8.4 reconstructGroup 扫描流

```text
ResidentDir decides reconstruct group_i
  -> scan resident slots for group_i
  -> BackstoreOrganization::scanGroupPages(groupIndex[i])
  -> MetaRNF 并发读取 page 链（最多 8 flights）
  -> 跳过 deleted 与 resident-overridden entries
  -> build shadowBF
  -> atomic swap
```

---

## 9. 并发控制、排序语义与竞态缓解

### 9.1 两层并发控制

1. **UBCC line-level**
   - 同一 home line 仍受 resident/outstanding 生命周期约束
   - 不允许同一 line 的 fill/write/delete 在 UBCC 层无序重叠提交

2. **MetaRNF page-level**
   - scoreboard key = `metadataPa`
   - 同一 page 同时只允许一个 active slot
   - 不同 page 最多 8 个并发

### 9.2 dual-key 语义

- **外部串行化键**：`metadataPa`
- **内部事务键**：`slot_id + txnId/dbid`

意义：

- 逻辑正确性按 page address 串行化
- CHI 返回匹配按 slot/txnId 识别
- 支持 OOO completion，但不破坏 per-page single-flight

### 9.3 主要 race windows

#### R1. resident fill 与 DRAM delete 交错

- 风险：读 miss 读到 page，同时 delete 正在 tombstone 同一 entry
- 规则：若 resident 在 fill-complete 前已安装 authoritative entry，则 delete callback 只清 shadow，不回滚 resident

#### R2. reconstruct 与 live insert/delete 交错

- 风险：swap 后丢新插入 bit
- 缓解：对 `_reconstructingGroup` 强制 dual-write

#### R3. page split/allocate 与并发 lookup

- 风险：lookup 只看旧 directory[0..3]，漏掉新页
- 缓解：更新 page_directory 属于 page write 提交的一部分；directory pointer 先写新页、后发布 pointer

#### R4. 同页多个不同 PA 同时 upsert

- 风险：page RMW 冲突
- 缓解：scoreboard 以 `metadataPa` 串行化；第二个请求入等待队列

#### R5. callback 重入 / slot 泄漏

- 风险：同一 read/write 完成两次释放 slot
- 缓解：slot 状态机只允许 `Allocated -> Sent -> Waiting -> Done -> Free` 单向转换

### 9.4 完成顺序

- 不要求不同 metadata pages 按 issue 顺序完成
- 要求同一 metadata page 的 queued 请求按 FIFO drain
- reconstruct 扫描页允许乱序回调，但最终以 page vector 全收齐为准再 swap

---

## 10. 逐文件修改目录、构建顺序与测试矩阵

### 10.1 `gem5/src/mem/ruby/protocol/chi/ep/ResidentDir.hh`

当前基线：

- `48-52`：counting BF 常量
- `97-99`：counter helper
- `103-115`：`_bloomCounterCount/_bloomCounters`

修改：

- 改为 `BloomGroups=16`
- 总 BF 从 64KB 逻辑切成 `16 × 3.75KB`
- 新增 `GroupIndex _groupIndex[16]`
- 新增 `reconstructGroup()`、`shouldReconstructGroup()`、`estimateFPR()`
- 删除 counting-BF helper 与 counters

### 10.2 `gem5/src/mem/ruby/protocol/chi/ep/ResidentDir.cc`

当前基线：

- `53-74`：counting BF 初始化
- `119-148`：counter 读写
- `346-393`：counting BF 操作

修改：

- 重新实现 grouped plain BF
- 新增 group hash / bit index helper
- 新增 reconstruct 扫描 resident 逻辑
- `clear()` 同时清 BF、GroupIndex、reconstruct 状态

### 10.3 `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh`

当前基线：

- `565`：`lookupBackstore()` 声明
- `609`：`_backstore`

修改：

- 删除 `_backstore`
- 删除 `lookupBackstore()`
- 保留 `snapshotResidentForBackstore()`，但输出 upgraded `BackstoreEntry`
- 新增 backstore organization / reconstruct 调度 helper

### 10.4 `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc`

当前基线：

- `214-220`：resident miss fallback 到 `lookupBackstore()`
- `2407-2408`：write ack 写软件 `_backstore`
- `2424-2425`：delete ack 擦软件 `_backstore`
- `2450`：inspect 路径查询 `lookupBackstore()`
- `2481-2483`：debug seed `_backstore`

修改：

- resident miss 只能通过 EPBackend/MetaRNF page 读
- `onBackstoreWriteAck()` 只做 resident 收尾与 BF insert
- `onBackstoreDeleteAck()` 只做 resident 收尾与 BF stale-delete
- inspect/debug 改成走 organization + DRAM page snapshot，或明确仅检查 resident/BF 状态
- 新增 wakeup/reconstruct 调度

### 10.5 `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh`

当前基线：

- `680-690`：单条 64B metadata line codec

修改：

- 删除 `metadataBackstorePa()` 的单-line 槽位语义
- 删除旧 `encodeMetaLine()/decodeMetaLine()`
- 改为 page codec、compact entry codec、organization-owned helper
- 新增 organization 指针与 page RMW helper

### 10.6 `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc`

当前基线：

- `173-181`：line→64B metadata slot
- `1020-1095`：read/write/delete 含 fallback

修改：

- read：按 groupIndex + organization 定位 page 链
- write：执行 page RMW upsert
- delete：执行 page RMW tombstone
- 移除 `_ubcc->lookupBackstore()` fallback 与 decode-failure fallback

### 10.7 `gem5/src/mem/ruby/protocol/chi/ep/MetaRNFController.hh/.cc`

当前基线：

- `MetaRNFController.hh:48-59`：`PendingTxn`
- `MetaRNFController.hh:72-74`：单 `_requestInFlight`
- `MetaRNFController.cc:146-197`：单飞行限制

修改：

- 新增 `_flightSlots[8]`
- 新增 `_scoreboard: metadataPa -> slot_id`
- 新增 per-PA wait queue
- send/recv 路径改为按 slot 跟踪 txn/dbid/回调

### 10.8 新增文件

建议新增：

- `ep/BackstoreOrganization.hh`
- `ep/BackstoreOrganization.cc`
- `ep/BackstoreSchemaA.hh`
- `ep/BackstoreSchemaA.cc`
- `ep/BackstoreSchemaC.hh`
- `ep/BackstoreSchemaC.cc`

并在 `ep/SConscript` 注册新源文件。

### 10.9 配置文件修改

- `EPBackend.py:17-18`：说明文字从 “counting bloom” 改成 “resident BF bytes”；新增 page size / organization / flights 参数
- `MetaRNFController.py`：新增 `flight_slots=8` 等参数
- `configs/ruby/CHI_ubcc_framework.py:167-173`：新增环境变量
  - `UBCC_BF_BYTES`
  - `UBCC_BACKSTORE_PAGE_BYTES`
  - `UBCC_BACKSTORE_ORG`
  - `UBCC_META_MAX_FLIGHTS`

### 10.10 测试：E2E 端到端 backstore 写回、读取、BF 重构与内部状态校验（TC60）

#### 10.10.1 测试目标

本 TC 是 DRAM backstore 方案的**自检型集成测试**，核心验证四条链路：

1. **写回链路**：resident 条目耗尽后通过 MetaRNF 写入 DRAM page，`_backstore` 软件 map 不再参与
2. **读取链路**：BF positive → MetaRNF 读 DRAM page → 解压 compact entry → 恢复 expanded entry
3. **BF 重构**：人工注入足够多的 tombstone/stale 条目，使 `stale_delete_count / live_count > Threshold`，触发 `reconstructGroup()`
4. **内部状态校验**：重构完成后遍历 ResidentDir、BF bit-vector、GroupIndex、指定 DRAM page snapshot，断言一致性

#### 10.10.2 TC 场景概览

```text
单 socket，4 CPU，256KB L2 per CPU，UBCC 32KB（Tiny）

阶段 1：注入已知 PA 集的 resident 条目并写回 DRAM
   - 写入 N_entries = 256 条不同的 cache line（PA 按 64B stride，共覆盖 16KB）
   - 全部经过 allocate → fill → dirty → writeback → resident evict
   - 预期每条产生一次 MetaRNF page write（upsert）

阶段 2：构造部分读丢失 / tombstone 以制造 false positive 和 stale 计数
   - 从 N_entries 中挑 M = 64 条做 explicit delete/tombstone
   - 对另 P = 32 条做 overwrite 写回（产生新旧两版 shadow entry）
   - 再对 Q = 20 条发起读 miss（验证 BF positive → DRAM read → entry decode 路径）
   - 对 R = 10 条未写回过任何 entry 的 PA 做读 miss（验证 BF negative → fast skip）

阶段 3：人工推进重构触发条件
   - 读取 GroupIndex[i] 的 stale_delete_count / live_count
   - 若比率未达 Threshold=0.25，追加 tombstone 直到满足
   - 调用 ResidentDir::tryReconstructGroup()，或通过 advanceTick 让 periodic epoch 触发

阶段 4：静态一致性验证（重构后）
   - 验证规则集见 10.10.4
```

#### 10.10.3 测试参数

| 参数 | 值 | 说明 |
|------|-----|------|
| `num_sockets` | 1 | 单 socket 简化 |
| `num_CPUs` | 4 | 足够并发写回 |
| `UBCC_BF_BYTES` | 64000 | 60KB BF |
| `UBCC_BACKSTORE_PAGE_BYTES` | 256 | 256B page |
| `UBCC_BACKSTORE_ORG` | `schema_a` | Schema A 先测 |
| `UBCC_META_MAX_FLIGHTS` | 4 | 小并发便于调试 |
| `ReconstructPeriod` | 64 | 降低 period 阈值加速触发 |
| `ReconstructStaleThreshold` | 0.20 | 降低 stale 阈值 |
| `N_entries` | 256 | 总写回条目 |
| `M_delete` | 64 | 删除/tombstone 条目 |
| `P_overwrite` | 32 | 覆盖写回条目 |
| `Q_miss_hit` | 20 | BF positive 命中条数 |
| `R_miss_none` | 10 | BF negative 条数（未写回过 backstore） |

#### 10.10.4 内部状态校验规则集

重构完成后，逐一执行以下断言（通过 `RubySystem` debug/drain 接口在仿真内完成）：

```
V1. BF 无假阴性（核心正确性）
    for each PA in {所有真正存在于 DRAM backstore 且非 tombstone 的条目}：
        assert ResidentDir::bloomMayContain(pa) == true
    注：迭代 source of truth = BackstoreOrganization::scanGroupPages 的 entry 清单

V2. BF 假阳性可控
    fp_pas = {已知从未写入 backstore 的 PA}
    fp_count = count(bloomMayContain(pa) for pa in fp_pas)
    fp_rate = fp_count / len(fp_pas)
    assert fp_rate < 0.05   // 60KB BF, k=4, ~256 entries → <2.7% expected

V3. stale_delete_count 归零
    for each group in 0..15：
        assert groupIndex[group].stale_delete_count == 0

V4. live_count 一致性
    for each group in 0..15：
        expected_live = count(非 tombstone 条目 in group 的 DRAM pages + resident 条目)
        // 允许小误差（reconstruct 期间可能有 insert 产生偏离）
        assert |groupIndex[group].live_count - expected_live| <= 16

V5. GroupIndex page_directory 不为空（若 group 有活跃条目）
    for each group in 0..15：
        if expected_live > 0：
            assert groupIndex[group].page_directory[0] != 0
        else：
            assert groupIndex[group].page_directory[0] == 0

V6. 单 PA 不存在臃肿条目（每 PA 在 DRAM 最多一份有效 entry）
    for each PA in all_pa_set：
        dr_entries = BackstoreOrganization::findInAllPages(pa)
        rn_entries = count(resident 中存在 pa 且非空洞)
        assert rn_entries + count(dr_entries 中非 tombstone) <= 1
        注：若 resident 存在，DRAM 条目不计入

V7. reconstruct 期间未丢失 live insert
    // 若在 reconstruct 窗口期间对 group_i 插入了额外条目
    // 这些条目应该在 shadowBF 或 activeBF（swap 后）中存在
    for pa in group_i_pas_inserted_during_reconstruct:
        assert ResidentDir::bloomMayContain(pa) == true

V8. MetaRNF flight 状态无泄漏
    // 所有测试结束后
    for each slot in 0..7:
        assert metaRNF._flightSlots[slot].state == Free
    assert metaRNF._scoreboard is empty
    assert metaRNF._waitQueues all empty
```

#### 10.10.5 与现有 TC 的关系

| 现有 TC | 本 TC 的增强 |
|----------|-------------|
| TC23（假阳性容忍） | 改为 DRAM-native 查询路径，不再走软件 `_backstore` |
| TC28（驱逐元数据一致性） | 增加 BF 重构后的全量内部校验 |
| TC45（bloom/fill 压力） | 增加可控 tombstone 注入与 stale 触发 |
| TC50-54（复杂场景） | 保留为 ablation 参考（Schema A vs C） |

#### 10.10.6 实现接口

测试脚本 `tests/e2e/test_e2e.py` 中新增函数：

```python
def test_tc60_backstore_writeback_read_reconstruct(helper,env):
    """TC60: DRAM backstore 写回/读取/BF重构 + 内部状态校验"""
    # 阶段 1：注入 256 PA → writeback → DRAM
    # 阶段 2：64 tombstone + 32 overwrite + 20 miss-hit + 10 miss-none
    # 阶段 3：触发 reconstruct
    # 阶段 4：调用 gem5 debug/python 验证接口做全量断言
```

gem5 侧需暴露 Python-facing debug 接口（新增于 `ResidentDir` 或通过 `RubySystem` 桥接）：

```cpp
// 暴露给 Python 测试用的内部状态校验 API
void ResidentDir::verifyBFConsistency();
uint64_t ResidentDir::countFalsePositives(const std::vector<uint64_t>& check_pas);
GroupIndex ResidentDir::getGroupIndex(int group);
std::vector<BackstoreEntry> BackstoreOrganization::scanAllEntries(int group);
```

#### 10.10.7 预期结果与失败语义

| 失败断言 | 根因方向 |
|----------|---------|
| V1 失败 | BF 位未正确设置；dual-write 遗漏；page scan 遗漏 entry |
| V2 失败 | BF FPR 异常升高（hash 碰撞、bit 分配错误） |
| V3 失败 | `stale_delete_count` 未在 swap 后清零 |
| V4/V5 失败 | DRAM page 计数与实际条目数严重偏离 |
| V6 失败 | overwrite 未生成 tombstone、旧 entry 残留 |
| V7 失败 | reconstruct 竞态：live insert 丢失 |
| V8 失败 | MetaRNF slot 泄漏：未正确释放或 drain |

### 10.11 测试矩阵

已有 E2E 回归可直接复用：

- **TC23** `tests/e2e/test_e2e.py:526-537`：BF 假阳性容忍
- **TC28** `tests/e2e/test_e2e.py:606-617`：resident 驱逐后 backstore 元数据一致性
- **TC45** `tests/e2e/test_e2e.py:875-889`：bloom/fill 压力

新增建议测试：

1. **UT-BS1**：Schema A insert/update/delete/tombstone
2. **UT-BS2**：Schema C bucket collision + overflow chain
3. **UT-BS3**：resident+DRAM coexistence，resident override DRAM
4. **UT-BS4**：reconstructGroup 与 live insert dual-write
5. **UT-BS5**：MetaRNF 8-flight OOO completion
6. **UT-BS6**：64B page mode

构建顺序：

1. `BackstoreOrganization` 抽象层
2. `ResidentDir` BF/index/reconstruct
3. `MetaRNFController` multi-flight
4. `EPBackend` page RMW
5. `UBCCController` 删除软件 `_backstore`
6. 配置 + 测试接线

---

## 11. Concrete data structure definitions

```cpp
// DRAM page layout (256B)
struct BackstorePage {
    uint64_t page_id;           // 8B
    uint16_t entry_count;       // 2B
    uint16_t free_offset;       // 2B  (next free byte in entries[] array)
    uint64_t next_page_ptr;     // 8B  (0 = end of chain)
    // 20B header + 4B padding = 24B
    // 232B for entries (19 × 12B compact entries per page)
    uint8_t  entries[232];
};
static_assert(sizeof(BackstorePage) == 256);

// Compact entry (12B, DRAM format)
struct CompactBackstoreEntry {
    uint64_t pa : 44;           // PA bits [19:0] and node/socket bits
    uint16_t state : 2;         // MESI
    uint16_t sharers : 10;      // compressed sharers bitmap
    uint32_t epoch : 24;        // epoch
    uint16_t flags : 4;         // deleted, dirty, reserved
    // Total: 44+2+10+24+4 = 84 bits → 11 bytes → padded to 12B
};

// Expanded entry (24B, C++ API format to keep alignment simple)
struct BackstoreEntry {
    uint64_t pa;
    UBCCMESIState state;
    uint64_t sharersMask;
    uint64_t epoch;
    bool deleted;
};
```

补充语义：

- `flags.deleted=1` 表示 tombstone
- `flags.dirty` 只表示 shadow page 中记录的脏来源属性，不改变“resident authoritative”原则
- `epoch` 默认按 24b 存储；expanded 侧允许更宽，但写回 compact 时截断/规范化

---

## 12. On-chip index layout (256B per group)

```cpp
struct GroupIndex {
    uint64_t page_directory[4];  // 32B: first 4 page pointers (64B for entries, rest via DRAM chain)
    uint32_t live_count;         // 4B
    uint32_t dirty_count;        // 4B
    uint32_t stale_delete_count; // 4B
    uint32_t insert_count;       // 4B
    uint8_t  existence_bf[32];   // 32B: small counting BF (8 counters × 4-bit, ~32 bits)
    uint8_t  padding[176];       // 176B: reserved for future use
};
static_assert(sizeof(GroupIndex) == 256);
```

语义约束：

- `live_count`：该 group 逻辑活跃项数（resident + backstore shadow 去重后的全集估计）
- `dirty_count`：需要写回或 recently shadowed 的脏项数
- `stale_delete_count`：自上次 reconstruct 以来的 tombstone 删除次数
- `insert_count`：用于 periodic reconstruct epoch
- `existence_bf`：小摘要；只能做“快速值得继续查页吗”的 hint，不能代替主 BF

---

## 13. BF reconstruction with DRAM backstore (full pseudocode)

```text
reconstructGroup(group_i):
  // 1. Allocate shadow BF (3.75KB for group_i)
  shadowBF = alloc(3.75KB); clear(shadowBF)
  
  // 2. Record start epoch for weak consistency
  startEpoch = groupIndex[group_i].insert_count
  
  // 3. Scan ResidentDir for group_i entries
  for slot in ResidentDir:
    if hash(slot.pa) % 16 != group_i: continue
    entry = decode(slot)
    if entry.state == G_I && !entry.residentDirty: continue
    shadowBF.insert(entry.pa)  // dual-write to activeBF too if concurrent insert
  
  // 4. Scan DRAM backstore for group_i entries
  pagePtr = groupIndex[group_i].page_directory[0]
  while pagePtr != 0:
    page = metaRNF.readPage(pagePtr)  // 256B CHI read
    for entry in page.entries:
      if entry.deleted: continue
      if ResidentDir.has(entry.pa): continue  // resident is authoritative
      shadowBF.insert(entry.pa)
    pagePtr = page.next_page_ptr
  
  // 5. Atomic swap
  swap(activeBF[group_i], shadowBF)
  free(shadowBF)
  
  // 6. Update counters
  groupIndex[group_i].stale_delete_count = 0
  groupIndex[group_i].insert_count = startEpoch
```

实现补充：

- `ResidentDir.has(entry.pa)` 应查 resident slot 是否存在且非空/非纯空洞
- 若 reconstruct 窗口内 page delete/insert 发生，live path 必须 dual-write 到 shadowBF
- 若 page 链超过 `page_directory[4]`，依然按 `next_page_ptr` 全链扫描

---

## 14. MetaRNF multi-flight design

- `_flightSlots[8]`: each slot tracks (state, metadataPa, callback)
- `_scoreboard`: maps metadataPa → slot_id (per-line serialization)
- `issueRead(pa, cb)`: check scoreboard, if busy → queue; else allocate slot, send CHI read
- `onReadComplete(pa, data)`: invoke callback, free slot, drain queued requests for same pa

进一步约束：

- write/delete 也复用同一 scoreboard，确保同 page RMW 串行化
- slot 内需记录 `txnId/dbid/responder/waitingCompAfterDbid`
- 支持 read/read 合并排队，但不做 write 与 read 的危险重排
- drain 顺序按同 PA FIFO；不同 PA 允许乱序完成

---

## 15. 结论

最终方案是：

- **resident 真值 + DRAM shadow backstore**
- **60KB grouped BF + 4KB GroupIndex**
- **page-based compact metadata in DRAM**
- **BackstoreOrganization 可插拔组织**
- **MetaRNF 8-flight、per-page single-flight、OOO completion**

这套方案完整替换当前 software `_backstore`，同时保留 BF 假阳性容忍、resident authoritative、以及后续 Schema A/C 对照实验所需的抽象边界。
