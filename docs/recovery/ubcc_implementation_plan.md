# UBCC 目录 Offload 剩余工作：一次编译周期实施方案

> 目标：在**不再改 SLICC/CHI 协议**的前提下，一次性补齐目录 offload 剩余功能，并能用 **2 个 controller-directed TC + 2 个 system-level TC** 验证。
>
> 已知基线：
> - `ResidentDir` 已替换 `_directory`
> - `UBAdapter/UBRouter/MsgQueue` 已可用
> - `ubcc_epoch_bits=24` 已验证
> - 现有 16 个 TC 全通过
> - 本轮 **不做真实 CHI timed metadata path**；`MetaRNF` 只做 **stub + 延时调度器**

---

## 0. 本轮固定边界

### 做
1. `ResidentDir` 补齐 **Counting BF + control byte + victim/evict API**
2. `UBCCController` 补齐 **resident miss/fill/replay/backstore/tombstone/delete**
3. 新增 `MetaRNFController`，作为 `EPBackend` 的 metadata 异步服务 stub
4. 新增 4 个 TC（2 directed + 2 e2e）

### 不做
1. 不改 `CHI-cache*.sm`
2. 不改 `EPRNFController/EPSNFController` 协议语义
3. 不做真实 HN-F/L3/private-DRAM metadata 路径
4. 不在本轮引入新的 outer message 类型

这保证本轮所有风险集中在 `ep/` C++ 与 Python 配置层，可一次性编译。

---

## 1. 一次编译周期的实施顺序

## Step 1：先改 resident 存储抽象

### 文件
- `gem5/src/mem/ruby/protocol/chi/ep/ResidentDir.hh`
- `gem5/src/mem/ruby/protocol/chi/ep/ResidentDir.cc`

### 目的
把当前“只有 packed entry 的 resident 哈希表”扩成：
- resident entry
- counting BF
- control byte
- victim/evict 能力

### 当前基线问题
- `ResidentDir.hh:21-32` 仍保留 `ownerNode/dirty`
- `ResidentDir.cc:88-115` 仍把 owner/dirty pack 进 56b
- 没有 BF/query/remove
- 没有 `fillPending/wbPending/pinned/LRU`
- 没有 victim 选择接口

### 精确修改

#### 1.1 改 `UBCCDirEntry` 语义
把 `UBCCDirEntry` 改成 committed metadata 视图：
- 保留：`lineAddr/state/sharersMask/epoch`
- 新增：`residentDirty`
- 删除：`ownerNode`
- 删除：`dirty`

新增 helper：
- `bool isEmpty() const` → `state==G_I && !residentDirty`
- `bool isTombstone() const` → `state==G_I && residentDirty`
- `bool isExclusive() const` → `state==G_E || state==G_M`

新增静态推导函数：
- `int ownerFromSharers(const UBCCDirEntry&)`
- `bool protoDirty(const UBCCDirEntry&)` → `state==G_M`
- `bool canonicalOneHotRequired(const UBCCDirEntry&)`

#### 1.2 改 packed56 编码
把当前 owner/dry 打包改成最终 resident 语义：
- `[1:0]` `MESI`
- `[2]` `residentDirty`
- `[18:3]` `sharersMask`
- `[42:19]` `epoch`
- `[55:43]` 保留 13b（本轮不再 pack owner）

本轮 control byte **不塞回 56b**，而是用并行数组：
- `std::vector<uint8_t> _ctrl`

原因：
- 一次编译周期内最小化 pack/unpack 风险
- 仍保留 resident 数据面 7B/entry 的编码逻辑
- BF 已经用 `_buf` 尾部空间，control byte 不再强行塞入 `_buf`

#### 1.3 新增 counting BF
在 `ResidentDir` 中新增：
- `static constexpr size_t DefaultBloomBytes = 64 * 1024;`
- `static constexpr int BloomHashes = 3;`
- `static constexpr int CounterBits = 4;`

新增 API：
- `bool bloomMayContain(uint64_t pa) const;`
- `void bloomInsert(uint64_t pa);`
- `void bloomRemove(uint64_t pa);`
- `void bloomClear();`

实现：
- 64KB → 131072 个 4-bit counter
- 3 个 hash：`splitmix64(pa ^ seed_i)`
- `query`：3 个 counter 均非 0 才返回 true
- `insert/remove`：4-bit 饱和增减

#### 1.4 新增 control byte API
control bit 分配：
- bit0: `fillPending`
- bit1: `wbPending`
- bit2: `pinned`
- bit[7:3]: `lruAge`

新增 API：
- `uint8_t control(size_t slot) const`
- `void setFillPending(uint64_t pa, bool)`
- `void setWbPending(uint64_t pa, bool)`
- `void setPinned(uint64_t pa, bool)`
- `bool fillPending(uint64_t pa) const`
- `bool wbPending(uint64_t pa) const`
- `bool pinned(uint64_t pa) const`
- `void touch(uint64_t pa)`

#### 1.5 新增 victim 选择/逐出接口
新增 API：
- `bool hasFreeSlot() const;`
- `bool pickVictim(uint64_t avoidPa, uint64_t &victimPa, UBCCDirEntry &victim) const;`
- `bool forceRemove(uint64_t pa);`
- `bool lookupWithSlot(uint64_t pa, UBCCDirEntry& out, size_t& slot) const;`

victim 规则：
- 跳过 `pinned=1`
- 其余按最小 `lruAge` 选
- 本轮允许 O(N) 扫描；后续再优化

#### 1.6 insert/update 时加 canonical assert
在 `insert()` / `update()` 前统一调用 `validateCanonical()`：
- `G_E/G_M` 时 `popcount(sharersMask)==1`
- `G_S` 时 `sharersMask!=0`
- `G_I && residentDirty==0` 才是 empty

若非法，直接 `panic_if`。

---

## Step 2：新增 metadata stub 服务

### 文件
- `gem5/src/mem/ruby/protocol/chi/ep/MetaRNFController.hh` **(新增)**
- `gem5/src/mem/ruby/protocol/chi/ep/MetaRNFController.cc` **(新增)**
- `gem5/src/mem/ruby/protocol/chi/ep/MetaRNFController.py` **(新增)**
- `gem5/src/mem/ruby/protocol/chi/ep/SConscript`

### 目的
给 `UBCCController` 一个**异步 backstore 完成回调入口**，但不引入新的 CHI 协议面修改。

### 精确修改

#### 2.1 `MetaRNFController` 类型
做成普通 `SimObject`，不是 `AbstractController`。

公开 API：
- `void issueRead(uint64_t linePa, std::function<void(bool, const BackstoreEntry&)> cb)`
- `void issueWrite(uint64_t linePa, const BackstoreEntry&, std::function<void()> cb)`
- `void issueDelete(uint64_t linePa, std::function<void(bool)> cb)`

内部：
- `EventFunctionWrapper` 调度完成事件
- 参数化延时：
  - `read_latency_ticks`，默认 `8000`
  - `write_latency_ticks`，默认 `7500`
  - `delete_latency_ticks`，默认 `7500`

#### 2.2 `SConscript`
新增：
- `SimObject('MetaRNFController.py', sim_objects=['MetaRNFController'])`
- `Source('MetaRNFController.cc')`

---

## Step 3：EPBackend 加 metadata 桥接层

### 文件
- `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh`
- `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc`
- `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.py`

### 当前基线问题
- 没有 `MetaRNF` 指针
- 没有 metadata read/write/delete 异步接口
- Python 无 resident/BF 参数

### 精确修改

#### 3.1 `EPBackend.hh`
新增前向声明：
- `class MetaRNFController;`

新增成员：
- `MetaRNFController *_metaRnf = nullptr;`

新增接口：
- `void setMetaRnfController(MetaRNFController *ctrl);`
- `void issueBackstoreRead(uint64_t homePa);`
- `void issueBackstoreWrite(uint64_t homePa);`
- `void issueBackstoreDelete(uint64_t homePa);`

新增测试包装接口（通过 `EPBackend` 暴露给 Python，避免直接 SWIG 访问 `UBCCController`）：
- `std::string inspectOffloadLineForTest(uint64_t homePa) const;`
- `bool debugSeedBackstoreForTest(uint64_t homePa, int mesi, uint64_t sharersMask, uint64_t epoch);`
- `bool debugSeedResidentForTest(uint64_t homePa, int mesi, uint64_t sharersMask, uint64_t epoch, bool residentDirty);`
- `bool debugForceResidentEvictForTest(uint64_t homePa);`

#### 3.2 `EPBackend.cc`
新增 3 类桥接函数：

1. `issueBackstoreRead(homePa)`
   - 调 `_metaRnf->issueRead()`
   - 完成后回调 `_ubcc->onBackstoreFillComplete(homePa, found, entry)`

2. `issueBackstoreWrite(homePa)`
   - 从 `_ubcc` 取待写回的 resident entry，转成 `BackstoreEntry`
   - 调 `_metaRnf->issueWrite()`
   - 完成后回调 `_ubcc->onBackstoreWriteAck(homePa)`

3. `issueBackstoreDelete(homePa)`
   - 调 `_metaRnf->issueDelete()`
   - 完成后回调 `_ubcc->onBackstoreDeleteAck(homePa, existed)`

#### 3.3 `EPBackend.py`
新增参数：
- `meta_rnf = Param.MetaRNFController(NULL, ...)`
- `ubcc_bf_bytes = Param.UInt32(64*1024, ...)`
- `ubcc_force_resident_entries = Param.UInt32(0, ...)`  # 仅测试用，0=按 64KB BF 正常算

---

## Step 4：UBCCController 接入 offload 主逻辑

### 文件
- `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh`
- `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc`

### 这是本轮主改动

---

### 4.1 头文件数据结构改造

#### 新增 `BackstoreEntry`
```cpp
struct BackstoreEntry {
    MESIState state;
    uint64_t sharersMask;
    uint64_t epoch;
};
```

#### 新增 resident stall waiter
复用现有 `PendingRequester` 字段，新增一张表：
- `std::map<uint64_t, std::deque<PendingRequester>> _residentWaiters;`

用途：
- resident miss + BF positive 时排队
- fillPending/deletePending/wbPending 期间排队

#### 新增 backstore 容器
本轮直接用：
- `std::unordered_map<uint64_t, BackstoreEntry> _backstore;`

不要在本轮实现开放寻址；那是后续性能优化，不应阻塞一次性可编译落地。

#### 新增 helper 声明
- `bool ensureResidentForAccess(...);`
- `bool handleResidentMiss(...);`
- `void enqueueResidentWaiter(...);`
- `void replayResidentWaiters(uint64_t linePa);`
- `void refreshPinnedBit(uint64_t linePa);`
- `bool evictOneVictim(uint64_t avoidPa);`
- `void scheduleBackstoreWrite(uint64_t linePa);`
- `void scheduleBackstoreDelete(uint64_t linePa);`
- `void onBackstoreFillComplete(uint64_t linePa, bool found, const BackstoreEntry &entry);`
- `void onBackstoreWriteAck(uint64_t linePa);`
- `void onBackstoreDeleteAck(uint64_t linePa, bool existed);`
- `std::string inspectOffloadLineForTest(uint64_t linePa) const;`
- `bool debugSeedBackstoreForTest(...)`
- `bool debugSeedResidentForTest(...)`
- `bool debugForceResidentEvictForTest(uint64_t)`

#### 删除/废弃语义
- `ensureDirEntry()` 从“miss 就造空项”改为**废弃 helper**
- 新入口统一走 `ensureResidentForAccess()`

---

### 4.2 `processOuterRequest()` 改造

### 当前基线
- `UBCCController.cc:172-175`：先 `ensureDirEntry()`，resident miss 直接造空项

### 改法

#### 4.2.1 进入时先做 resident 准备
在真正读 committed state 前，调用：
- `ensureResidentForAccess(line_pa, reqType, writeIntent, requesterNode, baseEpoch, reqId, entry)`

返回语义：
- `Ready`：`entry` 可直接使用
- `Queued`：本请求已入 `_residentWaiters[line_pa]`，立即返回 BUSY
- `Busy`：当前 line 处于 `wbPending/deletePending`，已排队，返回 BUSY

#### 4.2.2 resident miss 规则
1. resident hit → 直接继续现有 grant/recall/invalidate 逻辑
2. resident miss + `BF negative`：
   - 若无空位，先 `evictOneVictim(line_pa)`
   - 插入 empty resident entry：`G_I + residentDirty=0`
   - `touch()`
   - 继续当前请求逻辑
3. resident miss + `BF positive`：
   - 若本 PA 尚无 `fillPending`：
     - 必要时先找 victim
     - 插入 placeholder resident entry（逻辑 empty）
     - `setFillPending(true)`、`setPinned(true)`
     - 把当前请求入 `_residentWaiters[line_pa]`
     - 通过 `EPBackend.issueBackstoreRead(line_pa)` 发起 fill
   - 若已有 `fillPending`：
     - 仅排队到 `_residentWaiters[line_pa]`
   - 返回 BUSY

#### 4.2.3 replay
`onBackstoreFillComplete()` 完成后：
- backstore hit → 写入 resident committed entry，`residentDirty=0`
- backstore miss → 写入 empty resident entry，`residentDirty=0`
- 清 `fillPending`
- `refreshPinnedBit()`
- `replayResidentWaiters(line_pa)`

replay 时调用原 `processOuterRequest()`，保留 `reqId/baseEpoch`。

---

### 4.3 `commitIntendedResult()` 改造

### 当前基线
- `UBCCController.cc:1793-1800` 直接写 `ownerNode/dirty`

### 改法
改成 resident committed update 的唯一入口：
- `entry.state = ost.intendedState`
- `entry.sharersMask = ost.intendedSharersMask`
- `entry.epoch = reservedEpoch`
- `entry.residentDirty = true`

并执行：
- `panic_if(state in {G_E,G_M} && popcount(sharersMask)!=1)`
- 若 `state != G_I`：`_directory.bloomInsert(linePa)`
- 若 `state == G_I`：
  - 保持 `residentDirty=true`
  - 立即 `scheduleBackstoreDelete(linePa)`
  - 不清 BF

注意：
- 普通非空 commit **只脏 resident，不立即写 backstore**
- tombstone commit **必须立刻发 delete**

---

### 4.4 `processClear()` / `processOuterUpgradeDone()` 改造

两处 commit 点都要在 `_directory.update()` 后补：
- `refreshPinnedBit(line_pa)`
- 若 committed 成 `G_I` → delete 已调度，不要立刻 remove resident
- 若 committed 成非空 → 仅 replay 已排队的 requester / resident waiter

顺序固定：
1. `commitIntendedResult()`
2. `_directory.update()`
3. `retireToTombstone()/removeOutstanding()`（若适用）
4. `refreshPinnedBit()`
5. `replayPendingRequesters()`
6. `replayResidentWaiters()`

---

### 4.5 `processWriteback()` / `processEvict()` 改造

#### 4.5.1 先确保 resident 可见
不能再假设 metadata 一定 resident。

两函数入口都改成：
- 若 resident miss + `BF positive` → 发 fill，返回 false 让上层 retry
- 若 resident miss + `BF negative` → 直接 materialize empty，再按现有逻辑处理

#### 4.5.2 去掉 `entry.ownerNode/entry.dirty`
统一改成：
- owner = `ownerFromSharers(entry)`
- dirty = `(entry.state == G_M)`

#### 4.5.3 committed 后 residentDirty 行为
- `processWriteback()`：更新 committed resident 后 `residentDirty=true`
- `processEvict()`：
  - 若结果为 `G_I` → 进入 tombstone + delete
  - 否则 `residentDirty=true`

---

### 4.6 新增 victim eviction 流程

#### `evictOneVictim(avoidPa)`
1. `ResidentDir.pickVictim()` 选出 unpinned victim
2. 若 victim `residentDirty==0`：直接 remove
3. 若 victim `residentDirty==1 && state!=G_I`：
   - `setWbPending(true)`
   - `setPinned(true)`
   - `scheduleBackstoreWrite(victimPa)`
   - 本次插入返回失败/BUSY，等待 ack 后重试
4. 若 victim `residentDirty==1 && state==G_I`：
   - `setWbPending(true)`
   - `setPinned(true)`
   - `scheduleBackstoreDelete(victimPa)`
   - 本次插入返回失败/BUSY，等待 ack 后重试

#### ack 回调
- `onBackstoreWriteAck()`：
  - backstore upsert 完成
  - `residentDirty=false`
  - 清 `wbPending`
  - 若该行是 eviction target → remove resident
- `onBackstoreDeleteAck()`：
  - backstore erase 完成
  - `bloomRemove(linePa)`
  - tombstone resident 直接 remove

---

### 4.7 pinned 规则

`refreshPinnedBit(linePa)` 统一计算：
- outstanding 存在 → pin
- `_pendingRequesters[linePa]` 非空 → pin
- `_residentWaiters[linePa]` 非空 → pin
- `fillPending` → pin
- `wbPending` → pin
- `state==G_I && residentDirty==1` → pin

否则 unpin。

---

### 4.8 Backstore 读写删的 home truth 规则

本轮 backstore 存 committed metadata 真值：
- key: `homePa`
- value: `BackstoreEntry{state, sharersMask, epoch}`

写入时：
- `G_S/G_E/G_M` → upsert
- `G_I` 不写 entry，只走 delete

读取时：
- 命中 → refill resident committed copy
- 未命中 → refill logical empty

删除时：
- 只有 delete ack 后才允许 `bloomRemove()`

---

### 4.9 测试可观测接口

新增 `inspectOffloadLineForTest(homePa)`，返回 JSON 风格字符串，字段固定：
- `resident_present`
- `resident_state`
- `resident_sharers_mask`
- `resident_epoch`
- `resident_dirty`
- `bf_positive`
- `fill_pending`
- `wb_pending`
- `pinned`
- `backstore_present`
- `backstore_state`
- `backstore_sharers_mask`
- `backstore_epoch`
- `resident_waiter_depth`

directed TC 全部通过这个接口断言，避免 SWIG 暴露复杂 C++ 结构。

---

## Step 5：框架配置接线

### 文件
- `gem5/configs/ruby/CHI_ubcc_framework.py`

### 精确修改

#### 5.1 每节点实例化 `MetaRNFController`
在 `EPBackend` 创建前后插入：
- `meta_rnf = MetaRNFController(node_id=node_id, ...)`
- `ep_backend = EPBackend(..., meta_rnf=meta_rnf, ubcc_bf_bytes=..., ubcc_force_resident_entries=...)`

#### 5.2 暴露参数
从环境变量读取：
- `UBCC_BF_BYTES`，默认 `65536`
- `UBCC_FORCE_RESIDENT_ENTRIES`，默认 `0`
- `UBCC_META_READ_TICKS`，默认 `8000`
- `UBCC_META_WRITE_TICKS`，默认 `7500`
- `UBCC_META_DELETE_TICKS`，默认 `7500`

#### 5.3 本轮不改路由
`MetaRNF` stub 不挂 NoC，不参与 `network_nodes`，避免额外 protocol/build 风险。

---

## Step 6：新增 4 个 TC

## TC-D1（controller-directed）

### 文件
- `tests/ubcc/directory_offload/test_direct_fill_replay.py` **(新增)**

### 覆盖路径
- resident miss
- BF positive
- fillPending
- per-PA waiting queue
- backstore hit refill
- replay

### 步骤
1. 构造最小 RubySystem + `EPBackend + MetaRNFController`
2. `debugSeedBackstoreForTest(X, G_S, 1<<1, 7)`
3. 对 `X` 发第一次 `processOuterRequest` 等价操作（通过 `handleRemoteMiss` 或直接 UBCC wrapper）
4. 断言第一次返回 BUSY，且 `fill_pending=1`
5. 再对同一 `X` 发第二次请求，断言进入 waiter queue
6. `m5.simulate()` 到 read callback 完成
7. 断言：
   - resident 已存在
   - `resident_state==G_S`
   - `bf_positive==true`
   - `fill_pending==0`
   - waiter depth 清零

### 通过标准
fill + replay 全链路一次通过，无死锁。

---

## TC-D2（controller-directed）

### 文件
- `tests/ubcc/directory_offload/test_direct_dirty_evict_tombstone.py` **(新增)**

### 覆盖路径
- residentDirty
- dirty victim writeback
- tombstone delete
- BF remove only on delete ack
- `G_E/G_M` one-hot 约束

### 步骤
1. 强制 `UBCC_FORCE_RESIDENT_ENTRIES=4`
2. `debugSeedResidentForTest(A, G_M, 1<<2, 11, true)`
3. `debugForceResidentEvictForTest(A)`
4. 模拟到 writeback ack 完成
5. 断言：
   - resident 不再存在
   - backstore 命中 `A`
   - backstore state=`G_M`
6. 再 seed 一个 tombstone 行 `B: G_I + residentDirty=1`
7. `debugForceResidentEvictForTest(B)` 或走 delete ack 路径
8. 断言：
   - delete ack 前 `bf_positive==true`
   - delete ack 后 `bf_positive==false`
   - resident 被释放

### 额外检查
读源码确认 `insert()/update()` 内存在 `G_E/G_M` one-hot `panic_if`；若运行态不方便触发 fatal，这条做 source assertion 即可。

---

## TC-S1（system-level）

### 文件
- `tests/e2e/workloads/e2e_tc18_directory_fill_replay.c` **(新增)**
- `tests/e2e/test_e2e.py`

### 场景
验证“metadata 已被挤出 resident，但仍在 backstore 中”时，同 PA 并发请求能通过 fillPending + replay 正确完成。

### 前置配置
- `UBCC_FORCE_RESIDENT_ENTRIES=8`

### workload 设计
1. Node1 先访问 Node0-home 的 `X`
2. Node1 再访问足够多的 Node0-home 不同地址 `X1..X9`，把 `X` 的 resident metadata 挤掉
3. Node1 与 Node2 几乎同时再次读 `X`
4. 两边都打印 `[READ_VAL]`

### 期望
- 两个 reader 都读到同一正确值
- 日志里出现一次 fill、一次 replay（可加 `[UBCC-OFFLOAD] fill_start/fill_done/replay`）

### `test_e2e.py` 变更
- 注册 `TC18`
- 新增 `verify_tc18()`：要求 Node1/Node2 最终都读到预期值，且无 `deadlock/panic`

---

## TC-S2（system-level）

### 文件
- `tests/e2e/workloads/e2e_tc19_directory_dirty_persist.c` **(新增)**
- `tests/e2e/test_e2e.py`

### 场景
验证 dirty metadata 逐出到 backstore 后，后续 refill 仍能恢复 owner 信息，并驱动 recall/read 正确。

### 前置配置
- `UBCC_FORCE_RESIDENT_ENTRIES=8`

### workload 设计
1. Node1 对 Node0-home 的 `Y` 执行写，成为唯一 owner（`G_M`）
2. 再访问很多其他 Node0-home 地址，把 `Y` 的 resident metadata 挤出，触发 dirty metadata writeback
3. Node2 读 `Y`
4. 期望 home UBCC 从 backstore refill 出 `G_M(owner=Node1)`，然后走 recall，最终 Node2 读到 Node1 写入值

### 期望
- Node2 最终读到 Node1 的新值
- 无 stale metadata / owner 丢失

### `test_e2e.py` 变更
- 注册 `TC19`
- 新增 `verify_tc19()`：检查最终值正确

---

## 7. 需要改动的文件总表

### 新增文件
1. `gem5/src/mem/ruby/protocol/chi/ep/MetaRNFController.hh`
2. `gem5/src/mem/ruby/protocol/chi/ep/MetaRNFController.cc`
3. `gem5/src/mem/ruby/protocol/chi/ep/MetaRNFController.py`
4. `tests/ubcc/directory_offload/test_direct_fill_replay.py`
5. `tests/ubcc/directory_offload/test_direct_dirty_evict_tombstone.py`
6. `tests/e2e/workloads/e2e_tc18_directory_fill_replay.c`
7. `tests/e2e/workloads/e2e_tc19_directory_dirty_persist.c`

### 修改文件
1. `gem5/src/mem/ruby/protocol/chi/ep/ResidentDir.hh`
2. `gem5/src/mem/ruby/protocol/chi/ep/ResidentDir.cc`
3. `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh`
4. `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc`
5. `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh`
6. `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc`
7. `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.py`
8. `gem5/src/mem/ruby/protocol/chi/ep/SConscript`
9. `gem5/configs/ruby/CHI_ubcc_framework.py`
10. `tests/e2e/test_e2e.py`

**本轮不应修改**：
- `CHI-cache*.sm`
- `CHI-msg.sm`
- `EPRNFController.*`
- `EPSNFController.*`

---

## 8. 一次编译周期的实际执行顺序（给实现者）

1. 先完成 `MetaRNFController` 三文件 + `SConscript`
2. 再改 `ResidentDir.hh/.cc`
3. 再改 `UBCCController.hh`（结构体/声明）
4. 再改 `EPBackend.hh/.py`
5. 再改 `EPBackend.cc`
6. 最后改 `UBCCController.cc`
7. 补 `CHI_ubcc_framework.py`
8. 补 4 个测试文件 + `test_e2e.py`
9. **只编译一次**：`scons build/ARM/gem5.opt -j32`
10. 编译过后按顺序跑：`TC-D1 -> TC-D2 -> TC18 -> TC19 -> 现有16TC回归`

原因：`UBCCController.cc` 依赖前面所有头文件和桥接 API，放最后一次性收口最稳。

---

## 9. 本轮最关键的 5 条实现约束

1. **禁止 resident miss 时直接造空项替代 BF/backstore 判定**
2. **`G_E/G_M` 必须 one-hot；owner 只从 `sharersMask` 推导**
3. **`commitIntendedResult()` 只写 resident；非空项不立即落 backstore**
4. **`G_I + residentDirty=1` tombstone 必须等 delete ack 后才 empty**
5. **BF 只能 advisory；delete ack 前绝不清 BF**

---

## 10. 最小验收标准

### 编译
- 单次 `scons build/ARM/gem5.opt -j32` 通过

### 新增 4 TC
- `test_direct_fill_replay.py` PASS
- `test_direct_dirty_evict_tombstone.py` PASS
- `TC18` PASS
- `TC19` PASS

### 回归
- 现有 16 个 TC 继续全 PASS

满足以上三条，即可把本方案交给 `medium-guider` 直接落代码。
