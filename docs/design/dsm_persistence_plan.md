# DSM Data Persistence & MetaRNF Backstore 架构修复方案

> 状态：待实施
> 依赖：本方案基于 TC98/TC101 分析（`docs/tc98_tc101_analysis.md`）和架构审计
> 前置 commit：deadlock_threshold 提升、TC101 第 3 barrier、RECALL-PROXY guard

---

## 1. 修正后的目标架构

```
═════════════════════════════════════════════════════════════════════════════
                    目录元数据 (Metadata) vs DSM 数据 (Data)
═════════════════════════════════════════════════════════════════════════════

  On-chip 缓存:   ResidentDir (512KB SRAM)        ost.dataBuf / grant-buffer
                  56-bit packed per entry          64B 请求级临时 buffer
                  Bloom Filter (60KB)              (请求结束即释放)

  Offload/Onload: BackstoreSchema                  DSM Data Store
                  → MetaRNF(ZMQ → gem5)            → 延迟队列(T_dsm_dram)
                  → ReadShared → HN-F L3 cache     → 行为等价于 MemCtrl+DRAM
                  → L_SNF → l_memctrl(DDR4_2400)   → read(PA,buf) / write(PA,buf)
                    256B page 粒度                    64B cache line 粒度

  一致性模型:       UBCC 私有，不参与全局           参与全局一致性
  独立查询:         是                              是
═════════════════════════════════════════════════════════════════════════════


═══ 关键修正 vs 当前实现 ═══

1. MetaRNF:  ReadOnce → ReadShared（L3 自动缓存 metadata page）
2. _lineDataCache: 废弃长期驻留语义 → DSM Data Store 为唯一权威数据源
3. Bloom Filter: 定期 reconstructGroup 修复假阳性累积
4. G_S+RS read coalescing: _pendingDataWaiters 合并同 PA 的多次 read
5. BackstoreSchema: 实例化接入 UbioBackstoreHost
6. MetaRNFClient: ZMQ 连接 gem5 MetaRNF
```

---

## 2. 可执行修改方案

### Phase 1: 基础设施 (P0 — 无新测试，仅保证现有测试不退化)

#### 1a. MetaRNF: ReadOnce → ReadShared

文件: `gem5/src/mem/ruby/protocol/chi/ep/MetaRNFController.cc`

```diff
-    req->m_type = CHIRequestType_ReadOnce;
+    req->m_type = CHIRequestType_ReadShared;
```

方法名 `sendReadOnce` → `sendReadShared`（同步更新 .hh 声明和所有调用点）。

验证：编译 gem5，运行 TC2（最简单的 8n2s 测试）确认无退化。

#### 1b. DSM Data Store（ubio 侧 64B 数据持久化）

文件: `modules/ubiomodule/ubio_main.cc`

新增 `DsmDataStore` 类（UbioBackstoreHost 内嵌或独立）：

```cpp
struct DsmDataStore {
    std::map<uint64_t, std::array<uint8_t,64>> data;
    std::vector<PendingDataFill> pending;

    // 延迟读：入队 fireTick = tick + T_dsm_dram, 到期回调填充 buf
    void read(uint64_t pa, uint8_t *buf, uint64_t tick, uint64_t delay,
              std::function<void()> onComplete);

    // 延迟写：入队 fireTick = tick + T_dsm_dram, 到期写 data map + 回调
    void write(uint64_t pa, const uint8_t *buf, uint64_t tick, uint64_t delay,
               std::function<void()> onComplete);

    // 主循环每 tick 调用
    void drain(uint64_t tick);
};
```

环境变量 `UBCC_DSM_DRAM_DELAY_PS` 控制 `T_dsm_dram`，默认 50000（50ns）。

#### 1c. _pendingDataWaiters（read coalescing, optional）

文件: `modules/ubiomodule/UBCCController.{h,cc}`

```cpp
// 新增成员
std::map<uint64_t, std::vector<PendingRequester>> _pendingDataWaiters;
std::set<uint64_t> _pendingDataReads;  // 正在 flight 的 data read 集合

// G_S+RS fast path 逻辑修改:
//   1. 若 _lineDataCache HIT → 原有逻辑（直接 grant）
//   2. 若 _lineDataCache MISS → 
//        a. 检查 _pendingDataReads.count(PA) > 0 
//             → 将 requester 追加到 _pendingDataWaiters[PA]
//             → 不发起新 read，返回特殊标记（不送 BUSY）
//        b. 否则 → 发起 DsmDataStore.read(PA)
//             → 将 requester 挂入 _pendingDataWaiters[PA]
//             → _pendingDataReads.insert(PA)
//             → 返回特殊标记（让 caller 等待，不送 BUSY）
//
//   DSM Data Store 回调:
//     _pendingDataReads.erase(PA)
//     for each waiter in _pendingDataWaiters[PA]:
//       buildGrantResponse → push-grant 到 requester
//     _pendingDataWaiters.erase(PA)
```

环境变量 `UBCC_COALESCE_RS_READS=1` 控制（默认 OFF，启用后才合并）。

#### 1d. Bloom Filter 定期重构

文件: `modules/ubiomodule/UBCCController.cc`

在 `UBCCController::wakeup` 或类似周期性 tick 路径中：

```cpp
// 每 N 个 tick 检查一次
if (_tickCounter % kBloomReconstructInterval == 0) {
    for (int g = 0; g < ResidentDir::BloomGroups; ++g) {
        if (_directory.shouldReconstructGroup(g)) {
            _directory.reconstructGroup(g);
        }
    }
}
```

验证：运行 TC2/TC3 确认 Bloom 重构不引入退化。

#### 1e. _lineDataCache 语义修正

不再作为长期缓存。改为：

```cpp
// UBCCController::commitIntendedResult:
//   删除 _lineDataCache[pa] = cached 这一行
//   改为: DsmDataStore.write(pa, dataBuf, tick, T_dsm_dram, callback)

// G_S+RS fast path:
//   不再检查 _lineDataCache
//   改为: DsmDataStore.read(pa, buf, tick, T_dsm_dram, callback)
//   回调中构建 grant

// 保留 _lineDataCache 仅用于: 同一请求周期内的临时缓冲
// (例如 RECALL → GRANT_HANDSHAKE 转换期间，数据暂存)
```

---

### Phase 2: BackstoreSchema 接入 (P1)

#### 2a. BackstoreSchema 实例化

文件: `modules/ubiomodule/ubio_main.cc`

```cpp
struct UbioBackstoreHost {
    // ...
    BackstoreSchemaA schema;  // 或 SchemaC，由环境变量选择
};
```

#### 2b. MetaRNFClient

文件: `modules/ubiomodule/ubio_main.cc`

新增:
```cpp
class MetaRNFClient {
    Port *gem5Port;
    // META_RNF_READ(pagePa, 256B) → ZMQ → gem5 MetaRNF → ReadShared → DDR4 → 响应
    // META_RNF_WRITE(pagePa, 256B) → ZMQ → gem5 MetaRNF → WriteUniqueFull → DDR4 → 响应

    void readPage(uint64_t pagePa, uint8_t *buf, uint64_t tick,
                  std::function<void()> onComplete);
    void writePage(uint64_t pagePa, const uint8_t *buf, uint64_t tick,
                   std::function<void()> onComplete);
};
```

新增 CoherenceMessageType: `MetaRNFReadReq`, `MetaRNFReadResp`, `MetaRNFWriteReq`, `MetaRNFWriteResp`。

gem5 侧 MetaRNFController 已有 `issueRead/issueWrite` 接口，只需在 UBAdapter 中新增对应消息处理和路由。

#### 2c. UbioBackstoreHost 重构

```cpp
void hostIssueBackstoreRead(uint64_t pa) override {
    // 1. BackstoreSchema.candidatePagesForLookup(pa, idx) → 候选 page PA 列表
    // 2. for each page: MetaRNFClient.readPage(pagePa, buf, tick, ...)
    // 3. BackstoreSchema.lookupInPage(page, pa) → BackstoreEntry
    // 4. onBackstoreFillComplete(pa, found, entry)
}

void hostIssueBackstoreWrite(uint64_t pa) override {
    BackstoreEntry e{};
    ubcc.snapshotResidentForBackstore(pa, e);
    // 1. BackstoreSchema.planUpsert(pa, entry, idx) → UpdatePlan
    // 2. if needs_read_before: MetaRNFClient.readPage(pagePa)
    // 3. BackstoreSchema.applyUpsert(page, pa, entry, plan)
    // 4. MetaRNFClient.writePage(pagePa, pageBuf)
    // 5. BackstoreSchema.updateIndexAfterWrite(idx, plan)
    // 6. onBackstoreWriteAck(pa)
}
```

---

## 3. 实施顺序

| Phase | 内容 | 依赖 | 验收 |
|-------|------|------|------|
| **Phase 0** | `deadlock_threshold 200M→2G` | 无 | TC2 PASS |
| **Phase 0** | TC101 第 3 barrier + RECALL-PROXY | 无 | TC101 timeout 解除 |
| **Phase 1a** | MetaRNF ReadOnce→ReadShared | 无 | TC2 PASS（无退化） |
| **Phase 1b** | DSM Data Store | 无 | TC2 PASS + 数据可跨请求恢复 |
| **Phase 1c** | _pendingDataWaiters | 1b | TC100 PASS + merge 生效 |
| **Phase 1d** | Bloom reconstruction | 无 | TC2/TC3 PASS |
| **Phase 1e** | _lineDataCache 废弃 | 1b | TC2/TC100 PASS |
| **Phase 2a** | BackstoreSchema 实例化 | 无 | TC2 PASS（元数据走 schema） |
| **Phase 2b** | MetaRNFClient | 2a | TC2 PASS（元数据走 DDR4） |
| **Phase 2c** | UbioBackstoreHost 重构 | 2b | TC2 PASS |
| **TC98** | 最终验收 | Phase 1 全部 | TC98 PASS |
| **TC101** | 最终验收 | Phase 1 全部 | TC101 PASS |

---

## 4. 回归测试列表

### 正确拓扑下的回归：

```bash
# 8n2s (8 node × 2 socket)
--8n2s 2    # basic remote read
--8n2s 95   # barrier stress
--8n2s 96   # cross-socket read
--8n2s 97   # ping-pong
--8n2s 98   # hotspot contention  ← 核心目标
--8n2s 99   # per-plane slots
--8n2s 100  # batch RS grant
--8n2s 101  # C4 direct-forward    ← 核心目标

# 3n1s (3 node × 1 socket)
--1s 2
--1s 3
--1s 8

# 3n2s (3 node × 2 socket)
--2s 39
```

### 每 phase 完成后必跑: TC2, TC100

---

## 5. 验收标准

1. **TC101 PASS**：16/16 READ_VAL MATCH，无 OOM，无 deadlock panic
2. **TC98 PASS**：16 primary × 16 rounds 全部完成，无 Sequencer 超时
3. **所有回归 TC 维持 PASS**
4. MetaRNF 读写路径有 trace log 可验证 DDR4 延迟生效
5. Bloom reconstruction 日志可确认定期触发
