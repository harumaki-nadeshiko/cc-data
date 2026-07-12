# 专家接手文档 — 未完成事项 & 现存问题

> 提交时间：2026-07-13 凌晨
> 最后 commit：`463a9e2 Phase 3: CoherenceMessage MetaRNF types + gem5 UBAdapter MetaRNF dispatch`

---

## 1. 背景

目标：修复 TC98（hotspot deadlock）和 TC101（C4 direct-forward timeout），同时补齐 DSM 数据和目录元数据的持久化存储路径。

已完成的工作分了三个 Phase，但 Phase 3 的核心部分（MetaRNFClient 的 ZMQ 连接）未完成。

---

## 2. 当前提交链

```
463a9e2 Phase 3: CoherenceMessage MetaRNF types + gem5 UBAdapter MetaRNF dispatch  ← HEAD
21dae66 Phase 3: MetaRNFClient — async DDR4 page R/W via delay-simulated backstore
bb6b3f4 fix: G_S+RS → createOutstanding + re-enable writeDsmData in commitIntendedResult
e86b736 Phase 2: BackstoreSchemaA integration — page-based directory metadata store
cf81d26 bump gem5 submodule: deadlock_threshold 2G + self-snoop guard fix
e9029cc Phase 1: DsmDataStore persist + bloom reconstruct + PDES TERMINATE forward
92c9848 fix: EPRNF self-snoop guard — block ReadShared only, not ReadUnique/CleanUnique
48d1690 fix: TC101 3rd barrier + RECALL-PROXY guard, TC98/T101 analysis docs + backstore-architecture plan
```

Gem5 子模块在独立分支 `v4-selfsnoop-fix-clean` 上：
```
d15d36a Phase 3: UBAdapter — MetaRNFReadReq/WriteReq dispatch to MetaRNFController
d9c8c5b fix: increase sequencer deadlock_threshold 200M→2G
0468dc0 fix: minimal CHI-cache-actions — preserve dir state for non-self-snoop
c35661f fix: self-snoop guards + C4 direct-forward
```

---

## 3. 归档已完成的成果

| 成果 | 文件 | 说明 |
|------|------|------|
| Self-snoop guard 修复 | `gem5/.../EPRNFController.cc:704` | 只对 ReadShared 触发，不再误杀 CleanUnique 升级路径。修复了 TC3/TC8/TC39 的 split-brain 回归 |
| G_S+RS 回退 outstanding | `UBCCController.cc:635` | 从 ae57a55 的 tempOst+immediate commit 回退到 createOutstanding。tempOst 路径不创建 outstanding 导致并发读竞态 |
| DsmDataStore | `ubio_main.cc:DsmDataStore` | 64B DSM 数据持久化队列，T_dsm_dram=50000ps 默认延迟。commitIntendedResult 中 writeDsmData 通路已打通 |
| Bloom 重构 | `UBCCController.cc:wakeup` | ResidentDir 16 组 Bloom Filter，每 10000 次 wakeup 触发 shouldReconstructGroup→reconstructGroup。此前从未被调用 |
| PDES TERMINATE 转发 | `ubio_main.cc:1086` | gem5 退出时 ubio 向 networksim 转发 TERMINATE，排除冻结节点对 safeTs 的贡献。修复了 idle node 导致的时钟冻结 |
| BackstoreSchemaA | `ubio_main.cc:hostIssueBackstore*` | 平铺 std::map 替换为页式 SchemaA 存储（256B pages, 12B compact entries, 16 组 GroupIndex）。实现 planUpsert/applyUpsert/lookupInPage |
| MetaRNF CoherenceMessage 类型 | `protocol/CoherenceMessage.hh` | 新增 MetaRNFReadReq/Resp、MetaRNFWriteReq 三种消息类型 + UBMetaRNFBody 结构（256B 数据 + page PA），已添加到 union 和 typeToString |
| gem5 UBAdapter MetaRNF 路由 | `gem5/.../UBAdapter.cc:1150` | recvFromRouter 新增 MetaRNFReadReq→MetaRNFController::issueRead(4×64B→256B) 和 WriteReq 分发。编译通过但未联调 |

---

## 4. 未完成事项

### 4.1 MetaRNFClient 的 ZMQ 集成（核心缺失）

**问题**：`ubio_main.cc` 中的 MetaRNFClient 仍然是模拟实现（`std::map<uint64_t, BackstorePage> _pages` + 延迟队列），不是真正的 ZMQ→gem5→DDR4 通路。

**已具备的基础设施**（在其他文件中已完成）：
- `protocol/CoherenceMessage.hh`：MetaRNFReadReq/Resp/WriteReq 消息类型和 body
- `gem5/.../UBAdapter.cc`：UBAdapter 接收 MetaRNFReadReq → MetaRNFController::issueRead(4×64B→256B) → 回调组装 256B → 发送 MetaRNFReadResp 回 ubio

**需要做的事**（在 `ubio_main.cc` 中）：
1. 把 MetaRNFClient 的内部实现从延迟队列改为 `sendCoh(gem5Port, ...)` 发送 MetaRNFReadReq/WriteReq
2. 在 main loop 的消息处理中（pollAndProcess lambda, 约 line 1085），在 `isUbccIngress` 检查之前，添加对 `MetaRNFReadResp` 的处理，调用 `host._metaRNF.handleResp(*coh)`
3. 三个 host 方法中把 `_getPage(pagePa)` 替换为 `_metaRNF.readPage(pagePa, gem5Port, callback)`，callback 中做 schema.lookupInPage/applyUpsert
4. 注意：上次尝试时遇到了一个多余的 `}` 导致编译失败（"expected declaration before '}'" at line 734）。建议从当前干净的 e86b736 版本开始，只做 MetaRNFClient 的增量修改

**MetaRNFClient 的接口设计**（已在 `protocol/CoherenceMessage.hh` 和 UBAdapter 中对接好）：
```
ubio 侧调用：
  _metaRNF.readPage(pagePa, gem5Port, callback)  // 发 MetaRNFReadReq, 回调收 MetaRNFReadResp
  _metaRNF.writePage(pagePa, data, gem5Port)      // 发 MetaRNFWriteReq, fire-and-forget

gem5 侧（已实现）：
  UBAdapter::recvFromRouter MetaRNFReadReq → MetaRNFController::issueRead(4次64B) → 组装256B → MetaRNFReadResp 回 ubio
  UBAdapter::recvFromRouter MetaRNFWriteReq → MetaRNFController::issueWrite(4次64B)
```

### 4.2 TC98 和 TC101 仍未通过
- **TC98**：16 核抢同一 PA，Sequencer deadlock panic。deadlock_threshold 从 200M 提到了 2G 仍无效。根因是热点竞争下 PDES 时钟走不动
- **TC101**：C4 direct-forward chain，node 0 verify 时一些 PA 返回零数据。根因是 `_lineDataCache` 未命中时 `dataSource=HomeMemory` → gem5 physMem 对 DSM 地址全零填充

### 4.3 TC90（--8n1s）未通过
根因未查。可能也是 MetaRNF/BackstoreSchema 或 G_S+RS 相关。

---

## 5. 回归测试状态

| TC | 拓扑 | 状态 | 备注 |
|----|------|------|------|
| TC2 | --1s | ✅ PASS | |
| TC3 | --1s | ✅ PASS | self-snoop fix 修复 |
| TC8 | --1s | ✅ PASS | self-snoop fix 修复 |
| TC10 | --1s | ✅ PASS | |
| TC32-35 | --2s | ✅ PASS | |
| TC39 | --2s | ✅ PASS | G_S+RS outstanding 修复 |
| TC2 | --8n2s | ✅ PASS | |
| TC100 | --8n2s | ✅ PASS | |
| TC90 | --8n1s | ❌ | 未查根因 |
| TC98 | --8n2s | ❌ | Deadlock, 预存 |
| TC101 | --8n2s | ❌ | TIMEOUT/C4, 预存 |

---

## 6. 关键文件索引

| 文件 | 关键行号 | 内容 |
|------|---------|------|
| `modules/ubiomodule/UBCCController.cc` | 635 | G_S+RS 的 createOutstanding（必须保持 outstanding，不能 immediate commit） |
| `modules/ubiomodule/UBCCController.cc` | 2422 | commitIntendedResult 中的 writeDsmData 调用 |
| `modules/ubiomodule/UBCCController.cc` | 130 | wakeup 中的 bloom reconstruction |
| `modules/ubiomodule/UBCCController.hh` | 30-42 | UBCCHostIf: readDsmData/writeDsmData 接口 |
| `modules/ubiomodule/ubio_main.cc` | 310-340 | MetaRNFClient（待修复） |
| `modules/ubiomodule/ubio_main.cc` | 396-480 | hostIssueBackstoreRead/Write/Delete（需接入 MetaRNFClient） |
| `modules/ubiomodule/ubio_main.cc` | 1085 | MetaRNFReadResp 处理（待添加） |
| `modules/ubiomodule/ubio_main.cc` | 1092 | TERMINATE forwarding to networksim |
| `protocol/CoherenceMessage.hh` | 47-49 | MetaRNF message types |
| `protocol/CoherenceMessage.hh` | 214-218 | UBMetaRNFBody struct |
| `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc` | 1150-1190 | MetaRNFReadReq/WriteReq dispatch |
| `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc` | 704-720 | Self-snoop guard (ReadShared only) |
| `gem5/configs/ruby/CHI_ubcc_framework.py` | 434 | deadlock_threshold |
| `docs/design/dsm_persistence_plan.md` | - | 完整架构方案和 Phase 规划 |
| `docs/tc98_tc101_analysis.md` | - | TC98/101 的失败分析 |

---

## 7. 建议的下一步

1. **立即**：完成 MetaRNFClient 的 ZMQ 集成（4.1），这是 Phase 3 的核心未完成项，也是打通 DDR4 持久化路径的最后一步
2. **然后**：分析 TC90/TC98/TC101 失败根因，利用已打通的 DsmDataStore 和 MetaRNF 路径修复数据源问题
3. **编译命令**：
   ```bash
   # ubio
   docker run --rm -v /mnt/data2/cgc/cc-ep:/workspace -w /workspace ubcc-dev:ubuntu20.04 bash -c 'bash scripts/build_ubio.sh'
   # gem5
   docker run --rm -v /mnt/data2/cgc/cc-ep:/workspace -w /workspace ubcc-dev:ubuntu20.04 bash -c 'cd /workspace/gem5 && scons build/ARM/gem5.opt -j32'
   ```
4. **运行测试**：8n2s 不能并行（单机限制），3n1s/3n2s 可以。建议每轮测试前 kill 残留进程
