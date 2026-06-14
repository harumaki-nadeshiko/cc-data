# Gap Analysis and Fix Plan：`error_root_cause_v4.md` × `recall_spec_v4.md`

## 0. 执行摘要（按用户指令重排）

这份计划现在以 **all-DSM-through-EP_SNF** 为第一优先级。原因不是“它能顺手修掉一些 bug”，而是：

> **它才是正确架构基线。**

若继续保留“本地 DSM 例外走 `DL_SNF -> DDR4`”的 workaround，那么：

- recall 语义会建立在错误路由之上；
- `populateGrantData()` / buffer / HomeMemoryService 的真值入口无法收敛；
- `pickSharerForSnoop()` / DCT fallback / `SnpShared -> EP-RNF` 的修复结果会被旧 routing 污染；
- TC1 通过也只能证明 workaround 生效，不能证明协议收敛。

因此新的固定顺序是：

1. **F1：恢复 all-DSM-through-EP_SNF，并在该架构下修通 TC1**
2. **F2：修 recall 控制面闭环**（`handleRecallRequest -> EP-RNF -> HN-F`，`RECALL -> GRANT_HANDSHAKE`）
3. **F3：修数据面**（`GrantDataSource + HomeMemoryService + RecallBuffer`）
4. **F4：清理 workaround / 恢复 fatal / self-test 隔离**

### 0.1 编号说明（避免 D-10 歧义）

当前文档体系里有两个“D-10”：

- `drift_in_progress.md` 的 **D-10**：`EP_SNF addr_ranges` 覆盖本地 DSM 后，曾被当成 TC1 死锁根因，后来引入“排除本地 DSM”的 workaround。
- `decisions.md` 的 **D-10**：`pickSharerForSnoop()` 的优先级选择。

本文中：

- **“D-10 workaround”** 专指“本地 DSM 不走 EP_SNF”的 workaround；
- **“pickSharerForSnoop 修复”** 单独写，不再混叫 D-10。

---

## 1. recall_spec_v4 已覆盖的 bug（结论不变，但执行顺序前移了路由基础）

### 1.1 覆盖情况总表

| 项目 | recall_spec_v4 是否覆盖 | 文档结论 | 但实现优先级 |
|---|---|---|---|
| Issue 4：DONE RECALL 生命周期 | 是 | **覆盖** | 仍排在 **F2**，因为必须先有正确路由基础 |
| Issue 5：数据可见性 | 是 | **覆盖** | 仍排在 **F3**，因为真值入口必须建立在 all-DSM-through-EP_SNF 上 |
| Issue 6：`handleRecallRequest()` 绕过 HN-F | 是 | **覆盖** | 仍排在 **F2**，但必须在 F1 之后落地 |
| Fix A：HN-F 接收合法 `CompData_*` | 部分 | **必须保留** | 不回退 |
| Fix B：`Clear` 比较 `baseEpoch` | 是 | **已被吸收** | 在 F2 中做 tuple hardening |
| Fix C：stale `GRANT_HANDSHAKE` retire | 是 | **已被吸收** | 在 F2 中一起收紧 |

### 1.2 关键更新

旧结论“recall_spec_v4 已覆盖 Issue 4/5/6”依旧成立；**变化的是实施顺序**：

- 以前：先修 recall，再考虑 routing；
- 现在：**先修 routing foundation，再修 recall/data plane。**

理由来自 `scheme_v4.md` 与 `local_dsm_routing_v4.md` 的共同结论：

- DSM 对 HN-F 应只有 **一个 downstream：`EP_SNF`**；
- UBCC 只做目录/排序，不应靠 `DL_SNF -> DDR4` 帮它兜真值；
- 本地 clean data 应由 **`EPBackend + HomeMemoryService`** 供给；
- dirty data 应由 **`EP-RNF -> HN-F -> L2` recall** 回收。

---

## 2. recall_spec_v4 没有覆盖，或代码仍未落实的剩余缺口

### 2.1 F1 之前最大的阻塞：routing foundation 仍是 workaround 状态

当前代码还保留混合路由：

- `gem5/configs/ruby/CHI_ubcc_framework.py:215-235`：`EP_SNF.addr_ranges` 覆盖全部 DSM；
- `gem5/configs/ruby/CHI_ubcc_framework.py:316-325`：HN-F downstream 仍同时挂 `EP_SNF` 和 `DL_SNF`；
- `gem5/configs/ruby/CHI_ubcc_framework.py:296-309`：还保留了为 `functionalRead`/probe 服务的 DSM `addr_ranges` 扩张 workaround。

这意味着同一 DSM PA 仍存在“两套心智模型”：

- 逻辑上说“都应先到 UBCC”；
- 物理上又允许 HN-F 仍把 DSM 打到 `DL_SNF`。

**这必须先消掉。**

### 2.2 TC1 真根因不是“本地 DSM 不能走 EP_SNF”，而是 HN-F/EP-RNF 交互错误

`local_dsm_routing_v4.md` 已经把根因说清楚：

- `pickSharerForSnoop()` 不能在 preserving/Fwd 场景把 EP-RNF 选为目标；
- sole-EP-RNF 时必须走 DCT fallback；
- `SnpShared/SnpSharedFwd/SnpOnceFwd -> EP-RNF` 应重新恢复为 **fatal-grade unreachable**，而不是 defensive success。

对应代码缺口仍在：

- `CHI-cache-funcs.sm:922-943`
- `CHI-cache-actions.sm:490-506, 510-520, 597-612, 694-716, 1919-2139`
- `ep/EPRNFController.cc:631-645, 756-768`

### 2.3 recall 闭环文档已定义，但代码仍是“半实现”

当前代码里的关键缺口：

- `ep/UBCCController.cc:313-350`：`RECALL -> GRANT_HANDSHAKE` 仍是 **in-place mutation**；
- `ep/UBCCController.cc:556-635`：`processRecallResponse()` 只把 barrier 置 DONE，没有形成更强的 handshake identity 约束；
- `ep/EPBackend.cc:520-570`：grant 后立即 `populateGrantData()` + `sendClear()`，但 tuple 绑定仍较松散。

### 2.4 数据面仍建立在旧 workaround 上

当前数据面仍明显不符合 v4：

- `ep/EPBackend.cc:622-931`：`populateGrantData()` 仍依赖 `phys_mem + functionalRead + scavenging + back-fill`；
- `ep/EPBackend.cc:1069-1177`：`handleRecallRequest()` 仍是 `functionalRead + phys_mem broadcast + fake completion`；
- `ep/EPBackend.hh:278-287, 604-624`：还只有 `GrantDataProvenance`，没有正式的 `GrantDataSource + HomeMemoryService`；
- `ep/EPSNFController.cc:249-260`：grant data 仍只是“拿 lastGrantData，拿不到就发零”。

### 2.5 清理项尚未完成

当前还残留这些不应长期存在的工程态：

- `ep/EPBackend.py:7-13` 没有 `enable_self_test` 参数；
- `ep/EPBackend.cc:93-109` 默认执行 M4-M8；
- `ep/EPSNFController.cc:28-55`、`ep/EPRNFController.cc:237-273` 常开 selfTest 注入；
- `tests/e2e/test_e2e.py:696-705` / `tests/e2e/test_self_test.py:174-189` 已在写 `enable_self_test`，但 SimObject 侧未真正支持。

---

## 3. 依赖图（新的执行顺序）

```text
F1 路由基线：all-DSM-through-EP_SNF + TC1 under correct routing
  ├─ F1a 配置：移除 HN-F→DL_SNF 的 DSM 公开路由
  ├─ F1b HN-F：pickSharerForSnoop 永不把 EP-RNF 当 preserving/Fwd 目标
  ├─ F1c HN-F：sole-EP-RNF 时强制 DCT fallback
  └─ F1d EP-RNF：SnpShared/SnpSharedFwd/SnpOnceFwd 恢复 fatal
            ↓ blocks
F2 recall 控制面：真实 recall + RECALL→GRANT_HANDSHAKE
  ├─ F2a EPBackend：handleRecallRequest 改为 startReadShared/startReadUnique
  ├─ F2b UBCC：RECALL terminal record 与 GRANT_HANDSHAKE record 分离
  └─ F2c UBCC/EPBackend：Clear tuple(baseEpoch, reqId) 强校验
            ↓ blocks
F3 数据面：GrantDataSource + HomeMemoryService + RecallBuffer
  ├─ F3a EPBackend.hh/.cc：定义正式数据源接口
  ├─ F3b EPBackend.cc：重写 populateGrantData
  └─ F3c EPSNFController：只消费正式 grant data source
            ↓ blocks
F4 清理与收口
  ├─ F4a 移除 functionalRead/phys_mem/workaround 残留
  ├─ F4b 恢复/收紧 fatal 与断言
  └─ F4c self-test / workload 正式隔离
```

### 3.1 预计工期

| Fix | 预估工作量 | 风险 |
|---|---:|---|
| F1 | 0.5–1.0 天 | 中：会直接暴露 TC1/TC11 真 bug |
| F2 | 1.0–1.5 天 | 高：牵涉 outstanding 生命周期 |
| F3 | 1.5–2.0 天 | 高：是数据真值入口重构 |
| F4 | 0.5–1.0 天 | 低-中：清理后容易暴露隐藏依赖 |

---

## 4. Complete Fix Plan（可执行版）

> 建议每做完一个 fix，都先 `scons build/ARM/gem5.opt -j$(nproc)`，再跑该 fix 指定的最小验证集合；不要跨多个 fix 一次性改完。

### F1（TOP）：恢复 all-DSM-through-EP_SNF，并在正确架构下修通 TC1

**目标**：先把 foundation 改对，再谈 recall / buffer / HomeMemoryService。

**阻塞关系**：`F1 -> F2 -> F3 -> F4`

#### F1.1 需要改的文件与位置

| 文件 | 约行号 | 要改什么 |
|---|---:|---|
| `gem5/configs/ruby/CHI_ubcc_framework.py` | `215-235` | 保持 `EP_SNF.addr_ranges` 覆盖 **全部 DSM**；若仍有“排除本地 DSM”的过滤逻辑，先删掉。 |
| `gem5/configs/ruby/CHI_ubcc_framework.py` | `316-325` | **把 DSM 的 HN-F downstream 收敛为 `EP_SNF` 单入口**；`DL_SNF` 只保留 local-private / ubcc-exclusive 正常内存用途，不再作为 DSM 公开目标。 |
| `gem5/src/mem/ruby/protocol/chi/CHI-cache-funcs.sm` | `922-943` | `pickSharerForSnoop()` 不允许在 candidate 为空时回退到 `sharers.smallestElement()`；应改成“调用前已保证非 EP-RNF，函数内 assert 非空”。 |
| `gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm` | `490-506` | 保持 shared-owner 场景 sole-EP-RNF DCT fallback；同时补日志/断言说明该路径是 fallback，不是 normal Fwd。 |
| `gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm` | `510-520` | **补上 no-owner sharer-only 场景的 sole-EP-RNF fallback**；当前这里仍可能走 `SendSnpSharedFwdToSharer`。 |
| `gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm` | `597-612` | `ReadOnce` 上游路径继续保留 sole-EP-RNF fallback；确认 fallback 后不会再走 `SnpOnceFwd`。 |
| `gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm` | `694-716` | `ReadUnique_HitUpstream` 同样保持 sole-EP-RNF fallback。 |
| `gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm` | `1919-2139` | 所有 `pickSharerForSnoop()` 调用点增加前置断言：preserving/Fwd 目标不可为 EP-RNF；若唯一 sharer 为 EP-RNF，必须改走非 Fwd/non-DCT 路径。 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc` | `631-645` | 把 `SnpShared/SnpSharedFwd` 从 `warn + SnpResp_SC` 改回 **fatal**；`SnpOnceFwd` 保持 fatal。 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc` | `756-768` | `sendSnpRespSC()` 仅保留给真正合法场景；不再作为 defensive mask。 |

#### F1.2 具体修改准则

1. **先改 routing，再改 HN-F snoop 选路，再恢复 EP-RNF fatal。**
2. 目标不是“让 EP-RNF 更能兜底”，而是“让它根本不该收到 preserving/Fwd snoop”。
3. F1 不碰 `populateGrantData()` 主体，不碰 recall buffer；只修 foundation。

#### F1.3 验证检查

- **最小回归**：
  - `python3 tests/e2e/test_e2e.py --tc 1`
  - `python3 tests/e2e/test_e2e.py --tc 11`
- **看什么**：
  - TC1、TC11 通过；
  - 日志中不再出现 `SnpShared/SnpSharedFwd/SnpOnceFwd` 到 EP-RNF；
  - 日志中 DSM 访问不再由 `DL_SNF` 服务；
  - 若触发 fatal，应是暴露真实路径缺口，而不是继续加 defensive response。

#### F1.4 回滚计划

- 若系统在 `m5.instantiate()` 前就挂：先只回滚 `EPRNFController.cc` 的 fatal 恢复，保留 routing 改动与 HN-F 选路改动继续诊断。
- 若 TC1 比原来更早死锁：**不要回滚 all-DSM-through-EP_SNF**；应保留 routing 改动，继续修 `CHI-cache-actions.sm`/`CHI-cache-funcs.sm`。
- 只有当 boot 都失败且无法进入 workload 时，才临时恢复旧 fatal 行为；恢复后必须立即重新排查触发点，不能把它当最终状态。

---

### F2：修 recall 控制面闭环（真实 recall + RECALL→GRANT_HANDSHAKE）

**目标**：让 recall 真正经 `EP-RNF -> HN-F -> L2`，并让 home 端把 recall 完成与 grant/clear commit 正确串起来。

**前置依赖**：**F1 完成**

#### F2.1 需要改的文件与位置

| 文件 | 约行号 | 要改什么 |
|---|---:|---|
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc` | `1047-1177` | 重写 `handleRecallRequest()`：删除 `functionalRead + phys_mem broadcast + fake sendRecallResponse()`；改为根据 recall 类型调用 `EPRNFController::startReadShared()` 或 `startReadUnique(..., RecallUnique)`，等待真实 completion callback 后再回 `OuterRecallResponse`。 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc` | `1021-1105, 1253-1259` | 把 recall path 与现有 deferred/request-complete 路径接上；确保 recall 完成时能把“有无数据、降级/失权完成”准确回传 EPBackend。 |
| `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc` | `313-350` | 去掉 `RECALL -> GRANT_HANDSHAKE` 的 in-place mutation；改为：RECALL 保留 terminal identity，requester retry 命中 DONE recall 时，新建或显式替换为独立 `GRANT_HANDSHAKE` 记录。 |
| `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc` | `556-635` | `processRecallResponse()` 除了置 `DONE`，还要把 terminal tuple 固化：`(linePa, requester, baseEpoch, reservedEpoch, reqId, opType=RECALL)`。 |
| `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh` | `57-142` | 如现有 `OutstandingRequest` 字段不够，补强 recall terminal / handshake matching 所需字段；不要再靠隐式继承。 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc` | `520-570` | `sendClear()` 使用的 epoch/reqId 必须严格对应这次 grant 的 tuple；必要时从 grant envelope / requester entry 显式取值，不要靠“当前 entry.epoch”推断。 |
| `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc` | `1151-1231` | 保持 `processClear()` 对 `baseEpoch` 比较，但补充对 reqId、requesterNode、stage 的强校验与审计日志。 |

#### F2.2 具体修改准则

1. **Recall completion 不是 functionalRead 成功；而是 owner 真正经 HN-F/L2 完成降级/失权。**
2. `RECALL` 与 `GRANT_HANDSHAKE` 是两个不同生命周期对象；不要再就地改 opType 试图复用 identity。
3. `Clear` 只匹配这次 requester-visible transaction 的 `(baseEpoch, reqId)`。

#### F2.3 验证检查

- **最小回归**：
  - `python3 tests/e2e/test_e2e.py --tc 2`
  - `python3 tests/e2e/test_e2e.py --tc 3`
  - `python3 tests/e2e/test_e2e.py --tc 8`
- **看什么**：
  - 同一 `(PA, reqId)` 不再重复发 recall；
  - `[UBCC-ORDER]` 日志中能看到 recall prerequisite 完成后，再由 `Clear` 提交 `GRANT_HANDSHAKE`；
  - 不再出现 `handleRecallRequest()` 中 `functionalRead`/broadcast 调试打印；
  - TC2/TC3 至少进入“有正确 recall 生命周期、而不是旧 bypass”的失败/通过状态。

#### F2.4 回滚计划

- 若 F2 改完后 TC2/TC3 全面退化：优先回滚 **UBCC 的 handshake 对象重构**，保留 `handleRecallRequest()` 改造继续单独验证。
- 若 HN-F 路径已经跑通但 Clear 全部 mismatch：回滚 `sendClear()` 取 tuple 的变更，只保留 RECALL terminal / callback 改动。
- 禁止回滚到 `functionalRead + fake completion` 作为“暂时可跑”方案。

---

### F3：修数据面（`GrantDataSource + HomeMemoryService + RecallBuffer`）

**目标**：把 grant 数据源从 workaround 收敛成正式接口。

**前置依赖**：**F2 完成**

#### F3.1 需要改的文件与位置

| 文件 | 约行号 | 要改什么 |
|---|---:|---|
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh` | `278-287` | 用正式 `GrantDataSource` 替代仅调试用的 `GrantDataProvenance`；至少定义 `HomeMemory / RecallBuffer / NoData`。 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh` | `604-624` | 新增 `HomeMemoryService` 接口声明；`populateGrantData()` 改成按数据源取数，而不是多 PA view 探测。 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc` | `622-931` | 整体重写 `populateGrantData()`：删掉 `functionalRead`、`scavenge`、`back-fill`、`first_word!=0` 风格判断；按 `GrantDataSource` 从 `RecallBuffer` 或 `HomeMemoryService.read()` 取数。 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc` | `520-570` | grant 路径在拿到 UBCC grant 后，同时拿到 dataSource；不再无条件调用旧 `populateGrantData(reqPa, homePa, homeNode)`。 |
| `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh` | `78-142` | OutstandingRequest/Grant decision 中新增 dataSource 字段或等价信息。 |
| `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc` | `106-435` | `processOuterRequest()` 只做权限/目录决策，并返回对应 dataSource；无 dirty owner 时给 `HomeMemory`，recall 完成后给 `RecallBuffer`。 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc` | `249-260` | CompData 不再“拿不到数据就发零”；若 dataSource 需要数据却未就绪，应 defer/retry，而不是 silent zero-fill。 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.hh` | `58-75` | 如需保留 deferred grants，补齐 dataSource / ready 状态字段。 |

#### F3.2 具体修改准则

1. **UBCC 不 materialize 数据，只决定该去哪里拿数据。**
2. `HomeMemoryService.read()` 是 clean/shared grant 的统一真值入口。
3. recall 返回的数据进入 `RecallBuffer`；随后 shared/unique grant 明确消费它。
4. 不能再靠“读遍不同 PA / phys_mem / functionalRead”来猜真值。

#### F3.3 验证检查

- **最小回归**：
  - `python3 tests/e2e/test_e2e.py --tc 2`
  - `python3 tests/e2e/test_e2e.py --tc 5`
  - `python3 tests/e2e/test_e2e.py --tc 7`
  - `python3 tests/e2e/test_e2e.py --tc 11`
- **扩展回归**：
  - `python3 tests/e2e/test_e2e.py --tc 3`
  - `python3 tests/e2e/test_e2e.py --tc 6`
  - `python3 tests/e2e/test_e2e.py --tc 8`
- **看什么**：
  - grant data 不再依赖 `functionalRead`/`phys_mem broadcast` 日志；
  - TC5/TC7 的可见性错误应收敛；
  - 若数据未就绪，应看到 defer/retry，而不是读到全零假成功。

#### F3.4 回滚计划

- 若 F3 改造后 shared 读全面变零：先回滚 `EPSNFController` 的“未就绪即阻塞”逻辑，保留 `GrantDataSource` 架子继续调试供数源。
- 若 HomeMemoryService 引入后本地 DSM 读失败：临时回滚 HomeMemoryService 的读实现，但不要恢复 `populateGrantData()` 的全局 scavenging。
- recall buffer 若有生命周期 bug，可先让 `RecallBuffer` 只覆盖 recall case、`HomeMemory` 覆盖 clean case，分阶段收敛。

---

### F4：清理 workaround、恢复 fatal、落实 self-test/workload 隔离

**目标**：把临时诊断痕迹清掉，让回归结果可解释。

**前置依赖**：**F3 完成**

#### F4.1 需要改的文件与位置

| 文件 | 约行号 | 要改什么 |
|---|---:|---|
| `gem5/configs/ruby/CHI_ubcc_framework.py` | `296-309` | 删除为 `functionalRead()` 服务的 cluster `addr_ranges` 扩张 workaround。 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc` | `119-149` 及相关 DSM 检查代码 | 收紧 Q2 “accept cross-node PA” 型 workaround；恢复真正非法路径 fatal。 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.py` | `7-13` | 增加 `enable_self_test = Param.Bool(True, ...)`。 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc` | `93-109` | `EPBackend::init()` 按 `enable_self_test` 决定是否运行 M4-M8。 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc` | `28-55` | 给 `selfTest()` 加开关，workload 模式默认关闭。 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc` | `237-273` | 同上；workload 模式不注入测试 snoop。 |
| `tests/e2e/test_e2e.py` | `696-705` | 保留 workload 侧 `enable_self_test=False`；改成真正生效的配置。 |
| `tests/e2e/test_self_test.py` | `174-189` | 保留 self-test 侧 `enable_self_test=True`；验证自测与 workload 正式分离。 |

#### F4.2 具体修改准则

1. 所有“为了让旧 workaround 能跑”的 debug/scavenge/fallback 都要逐项删除。
2. impossible path 要恢复 fatal，而不是 silent accept。
3. self-test 只在自测模式运行；workload 回归必须是干净环境。

#### F4.3 验证检查

- **最小回归**：
  - `python3 tests/e2e/test_self_test.py`
  - `python3 tests/e2e/test_e2e.py --tc 1`
  - `python3 tests/e2e/test_e2e.py --tc 2`
  - `python3 tests/e2e/test_e2e.py --tc 9`
- **看什么**：
  - self-test 只在 `test_self_test.py` 中执行；
  - workload 模式无 M4-M8 注入；
  - 非 DSM 负例 TC9 不被新 routing/data plane 误伤；
  - 不再出现旧 `functionalRead`/`phys_mem` workaround 日志。

#### F4.4 回滚计划

- 若去掉 `addr_ranges` workaround 后某些旧 phase test 失败，不要立即恢复全量 workaround；先确认这些测试是否仍应依赖旧 functionalRead 语义。
- 若 self-test gating 影响 CI：先保留 `EPBackend.enable_self_test`，延迟删除 `EPSNF/EPRNF selfTest()` 注入，但 workload 模式必须继续默认关闭。

---

## 5. Fix 间依赖与解锁关系

| Fix | 被谁阻塞 | 它解锁谁 | 主要解锁 TC |
|---|---|---|---|
| F1 | 无 | F2/F3/F4 | **TC1、TC11**；并为 TC2/3/5/6/7/8 打基础 |
| F2 | F1 | F3/F4 | **TC2、TC3、TC8** |
| F3 | F2 | F4 | **TC2、TC5、TC7、TC11**，改善 TC3/6/8 |
| F4 | F3 | 回归可信度 | self-test / TC9 / 全量回归 |

---

## 6. 建议的提交节奏（给 code-implementer subagent）

1. **Commit A / Patchset A：F1 only**
   - 目标：TC1 在 all-DSM-through-EP_SNF 下通过。
2. **Commit B / Patchset B：F2 only**
   - 目标：TC2/TC3 的 recall 生命周期正确。
3. **Commit C / Patchset C：F3 only**
   - 目标：数据真值入口收敛，TC5/TC7 回正。
4. **Commit D / Patchset D：F4 only**
   - 目标：清理 workaround、恢复 fatals、隔离 self-test。

每个 patchset 都应附：

- 改动文件列表
- 对应 TC 结果
- 若失败，失败点处的关键日志片段

---

## 7. 一句话结论（更新版）

**`recall_spec_v4` 的方向没有错，但执行顺序之前错了：现在必须先把 all-DSM-through-EP_SNF 作为 F1 落地，先拆掉 D-10 workaround，再去修 recall 闭环与数据面；否则后续所有 fix 都是在错误路由基础上的补丁。**
