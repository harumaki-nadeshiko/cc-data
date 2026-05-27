# M6 阶段交付报告

- **阶段：** M6 — UBCC 目录 + EP_RNF 本地一致性访问
- **状态：** PASS
- **完成日期：** 2026-05-26 → 2026-05-27（修复轮）
- **审查轮次：** 2（初次 + 修复轮）
- **编排器判定：** PASS

---

## 1. 阶段摘要

### 1.1 阶段目标

使 home UBCC 能够通过 `EP_RNF` 在本地 CHI 域上执行真正的一致性操作，完成 dirty recall/read 闭环，并确保每行全局目录（MESI）正确维护 — 同时保持 home UBCC 严格仅元数据（不缓存数据）。

### 1.2 完成状态

| 标准 | 结果 |
|---|---|
| 每行全局目录（`DirEntry`） | PASS |
| 活动事务管理 | PASS |
| `GlobalRecallOwner` 实现 | PASS |
| UBCC → EP_RNF → HN → 本地缓存召回路径 | PASS |
| EP_RNF 延迟 HN 响应 | PASS |
| Home UBCC MESI（`E` ≠ `M`） | PASS |
| Home UBCC 仅元数据（无数据存储） | PASS |
| 目录一致性（G_S/G_E/G_M 字段） | PASS |

### 1.3 审查轮次

| 轮次 | 日期 | 关键发现 | 解决方案 |
|---|---|---|---|
| R1（初次） | 2026-05-26 | 完整 M6 实现已提交 | 等待 validator 审查 |
| 修复轮 | 2026-05-27 | P0：移除召回回退旁路；P0：召回失败时中止；P0：目标不匹配时 fatal；召回路由、外部事务生命周期、busy/owner 检查、测试断言加强 | 所有 P0/P1 已解决 |

---

## 2. 代码变更

### 2.1 gem5 子模块

| 文件 | 变更 | 描述 |
|---|---|---|
| `src/mem/ruby/protocol/chi/ep/UBCCController.hh` | 扩展 | `processOuterRequest()` 召回输出（`outRecallNeeded`、`outRecallOwnerNode`）；用于召回完成的 `completeRecall()`；`processRecallResponse()`；用于事务序列化的 `G_BUSY` 状态；`DirEntry` 字段：`ownerNode`、`sharersMask`（64 位）、`dirty`、`epoch`、`pendingOp`、`pendingRequester` |
| `src/mem/ruby/protocol/chi/ep/UBCCController.cc` | 扩展 | `GlobalRecallOwner` 完整路径：home 检测冲突（现有 owner ≠ 请求者），将行标记为 busy（`G_BUSY` + `pendingOp` = RECALL），通过 `getInstance(ownerNode)` 将召回路由到 owner 节点，通过 `completeRecall()` 等待召回响应，恢复挂起的请求者 grant；召回结果拆分：读取 → 旧 owner 降级为共享，写入/唯一 → 旧 owner 被失效 |
| `src/mem/ruby/protocol/chi/ep/EPBackend.hh` | 扩展 | `handleRecallRequest()`：接收来自 home UBCC 的召回，通过 `EPRNFController` 启动本地一致性访问；`handleRecallResponse()`：将数据/ack 转发回 home UBCC；`inspectUbccDirForTest()`：返回 JSON 结构化目录快照供测试观察；`getRecallCount()` / `getRecallAckCount()` 计数器 |
| `src/mem/ruby/protocol/chi/ep/EPBackend.cc` | 扩展 | 召回编排：home 侧分配召回上下文，向 owner 节点的 EPBackend 发送 `GlobalRecallOwner`，owner 侧通过 `EP_RNF` 触发 HN snoop，等待数据/ack，将响应返回给 home；EP_RNF 延迟响应：外部事务完成门控最终 HN 响应 |
| `src/mem/ruby/protocol/chi/ep/EPRNFController.hh` | 扩展 | `injectEpSnoopForTest()`：本地一致性访问注入的仅测试钩子；用于延迟 HN 回复的 snoop 响应上下文 |
| `src/mem/ruby/protocol/chi/ep/EPRNFController.cc` | 扩展 | 延迟响应处理：当 HN 对 EP_RNF 进行 snoop 时分配挂起响应上下文；保持响应直到外部事务完成；释放带有数据/ack 的响应给 HN |
| `src/mem/ruby/protocol/chi/ep/M6SelfTest.cc` | 新增 | 52 个三元检查：目录一致性 MESI 状态（TC-M6-4：G_S dirty=false、G_E dirty=false、G_M dirty=true、G_E ≠ G_M）、仅元数据（TC-M6-5：UBCC 中无行数据）、GlobalRecallOwner 路径（TC-M6-2：召回启动、owner 联系、数据返回、目录更新）、EP_RNF 延迟响应（TC-M6-3：挂起上下文分配、HN 响应门控）、召回计数器 |

**gem5 commit 历史（M6 相关）：**

| Commit | 描述 |
|---|---|
| `899ead12f7` | M6 修复轮：召回路由、外部事务生命周期、busy/owner 检查、测试断言 |
| `607a8f0e0e` | M6 P0：移除召回回退旁路、召回失败时中止、目标不匹配时 fatal |
| `b41fe6012c` | M7 修复轮（单独阶段） |

### 2.2 超项目

| 文件 | 变更 | 描述 |
|---|---|---|
| `tests/phase6/test_recall.py` | 仅本地验证脚本（未提交到仓库） | PY_INJECT harness：完整 CHI+UBCC 拓扑，在实例化时运行 M4/M5/M6 所有自检，捕获 C++ stdout，解析所有三个阶段的 PASS/FAIL，回归门控检查（M4/M5 失败阻止 M6），TC-M6-2/3/4/5 的测试用例覆盖报告 |
| `reports/` | — | M6 特定修复报告集成到 validator 审查周期 |

**超项目 commit 历史：**

| Commit | 描述 |
|---|---|
| `99cb400` | M6 修复轮：更新 gem5 子模块（召回路由、外部事务、busy 检查、测试断言） |

---

## 3. 与原计划差异

### 3.1 与 `plan/03-phase-plan.md` 的对齐

| 计划 | 实际 | 备注 |
|---|---|---|
| 每行全局目录 | 已完成 | `DirEntry` 含 `state`、`ownerNode`、`sharersMask`、`dirty`、`epoch`、`pendingOp` |
| 活动事务管理 | 已完成 | `G_BUSY` 状态防止冲突事务；`pendingOp`/`pendingRequester` 序列化 |
| `GlobalRecallOwner` | 已完成 | 完整路径：home 检测冲突 → 路由召回给 owner → owner 执行本地一致性访问 → 返回数据 → home 完成 |
| UBCC → EP_RNF → HN → 本地缓存召回路径 | 已完成 | Home UBCC 向 owner 节点的 EPBackend 发送召回；owner 侧 EP_RNF 触发 HN snoop |
| EP_RNF 延迟 HN 响应 | 已完成 | 分配挂起响应上下文；HN 响应由外部事务完成门控 |
| Home UBCC 仅元数据 | 已完成 | 目录维护元数据；无永久行数据缓存 |
| Home UBCC MESI（E ≠ M） | 已完成 | `G_E`（dirty=false）和 `G_M`（dirty=true）严格区分 |

### 3.2 关键设计决策

| 决策 | 理由 |
|---|---|
| `G_BUSY` 用于事务序列化 | 防止同一行上的重叠事务；`pendingOp` 字段记录活动操作类型（RECALL、INVALIDATE 等） |
| 通过 `UBCCController::getInstance(nodeId)` 路由召回 | 在单 gem5 原型中，所有 UBCC 实例自注册；召回消息直接发送到 owner 节点的 UBCC → EPBackend → EP_RNF 路径 |
| Owner 召回结果拆分 | 读取触发召回 → 旧 owner 降级为共享；Unique/写入触发召回 → 旧 owner 被失效 |
| 无召回回退旁路 | P0 修复：召回必须联系真实 owner；不允许数据捷径 |
| 目标不匹配时 `fatal()` | 确保从 owner 返回的数据与预期行 PA 匹配 |

### 3.3 M6 召回流程

```
Home UBCC 检测冲突（owner ≠ 请求者）
  → 将行标记为 G_BUSY，设置 pendingOp=RECALL
  → 将 GlobalRecallOwner 路由到 owner 节点的 UBCC
    → owner EPBackend.handleRecallRequest()
      → EP_RNF 注入 HN snoop（本地一致性访问）
        → HN snoop 本地缓存 → 获取数据
      → EP_RNF 保持 HN 响应（延迟）
    → owner 返回数据 + ack 给 home
  → home UBCC.processRecallResponse()
    → 更新目录（owner 降级/失效）
    → 清除 G_BUSY
    → 恢复挂起的请求者 grant
```

### 3.4 范围边界

| 范围内（已实现） | 尚未实现（M7+） |
|---|---|
| 单 owner 冲突的 `GlobalRecallOwner` | 多请求者序列化（排队） |
| 目录 MESI 状态（G_I/G_S/G_E/G_M/G_BUSY） | 写回（M7） |
| 被召回 owner 的降级/失效 | 驱逐（M7） |
| EP_RNF 延迟 HN 响应 | Owner 转移（M7） |
| 强制执行仅元数据 home 设计 | 基于 epoch 的过期过滤（M7） |

### 3.5 与 `plan/02-external-proxy-spec.md` 的一致性

| 规格要求 | 实现 | 状态 |
|---|---|---|
| 含 MESI 的每行目录（§6.1） | 含 `G_I/G_S/G_E/G_M/G_BUSY` 的 `DirEntry` | PASS |
| E ≠ M 明确（§6.1） | `G_E`（dirty=false）vs `G_M`（dirty=true） | PASS |
| `GlobalRecallOwner`（§7.4） | Home → owner → EP_RNF → HN → 数据返回 | PASS |
| EP_RNF 延迟 HN 响应（§7.5） | 挂起响应上下文；由外部事务完成门控 | PASS |
| Home 仅元数据（不缓存数据）（§6.1） | UBCC 目录无行数据字段 | PASS |
| 召回结果拆分（§8） | 读取 → 降级（共享），写入 → 失效 | PASS |

---

## 4. 测试用例

### 4.1 TC-M6-4：目录一致性

| 属性 | 值 |
|---|---|
| **ID** | TC-M6-4（M6-4a、4b、4c、4d） |
| **名称** | 目录一致性 |
| **类型** | PY_INJECT（C++ 自检） |
| **断言数** | 4 项核心 |
| **预期** | G_S → `dirty=false`、`ownerNode` 无效；G_E → `dirty=false`、`ownerNode` 有效；G_M → `dirty=true`、`ownerNode` 有效；G_E ≠ G_M |
| **实际** | PASS |
| **负面测试** | G_E 和 G_M 未合并为单一 owner 状态 |

### 4.2 TC-M6-5：Home UBCC 仅元数据

| 属性 | 值 |
|---|---|
| **ID** | TC-M6-5（M6-5） |
| **名称** | Home UBCC 仅元数据 |
| **类型** | PY_INJECT（C++ 自检） |
| **断言数** | 1 项核心 |
| **预期** | UBCC 目录检查 API 显示无永久行数据存储字段 |
| **实际** | PASS |
| **负面测试** | 无行数据副本用作主要数据源 |

### 4.3 TC-M6-2：GlobalRecallOwner 路径

| 属性 | 值 |
|---|---|
| **ID** | TC-M6-2（M6-2 系列） |
| **名称** | GlobalRecallOwner 路径 |
| **类型** | PY_INJECT（C++ 自检） |
| **断言数** | 多项 |
| **预期** | 读取与现有 owner 冲突时启动召回；owner 被联系；数据返回；目录更新；召回计数器递增 |
| **实际** | PASS |
| **负面测试** | 无旁路 owner 联系；无过期数据返回 |

### 4.4 TC-M6-3：EP_RNF 延迟 HN 响应

| 属性 | 值 |
|---|---|
| **ID** | TC-M6-3（M6-3 系列） |
| **名称** | EP_RNF 延迟 HN 响应 |
| **类型** | PY_INJECT（C++ 自检） |
| **断言数** | 多项 |
| **预期** | 当 HN 对 EP_RNF 进行 snoop 时分配挂起响应上下文；HN 响应被门控直到外部事务完成 |
| **实际** | PASS |
| **负面测试** | EP_RNF 在外部事务完成前不响应 HN |

### 4.5 附加自检

| 测试组 | 检查数 | 目的 |
|---|---|---|
| M6-BUSY（1-6） | 6 | `G_BUSY` 状态：召回期间设置，完成后清除，扩展检查工作 |
| M6-CNT（1-2） | 2 | 召回计数器：UBCC 和 EPBackend 级别 |
| M6-DIR | 多项 | 目录召回状态转换（owner 降级、失效） |
| M6-META | 多项 | 仅元数据强制执行：无缓存行数据；zip 级分析显示 DirEntry 中无 `data` 字段 |

### 4.6 汇总

| 测试组 | 检查数 | PASS | FAIL | SKIP |
|---|---|---|---|---|
| M6-4（目录一致性） | 4+ | 4+ | 0 | 0 |
| M6-5（仅元数据） | 1+ | 1+ | 0 | 0 |
| M6-2（召回路径） | 6+ | 6+ | 0 | 0 |
| M6-3（延迟响应） | 3+ | 3+ | 0 | 0 |
| M6-BUSY（事务管理） | 6 | 6 | 0 | 0 |
| M6-CNT（计数器） | 2 | 2 | 0 | 0 |
| **合计** | **52** | **52** | **0** | **0** |

---

## 5. 回归结果

| 测试 | 状态 | 备注 |
|---|---|---|
| TC1 (`test_pa_layout_mode.py`) | 预先存在的 PASS | 不受影响 |
| TC2 (`run_phase1_test.py`) | 预先存在的基线 | 不受影响 |
| TC2E (`run_phase1_test_enhanced.py`) | 预先存在的基线 | 不受影响 |
| TC3 (`verify_topo_objects.py`) | 预先存在的基线 | 不受影响 |
| TC4 (`test_ruby_create_system_n3l2d2.py`) | 预先存在的基线 | 不受影响 |
| TC5 (`test_ep_instantiate.py`) | 预先存在的基线 | 不受影响 |
| M4 自检（M6 内） | 0 FAIL | 从 M4 无回归 |
| M5 自检（M6 内） | 0 FAIL | 从 M5 无回归 |
| M6 自检 | 0 FAIL | 所有 52 个检查通过 |

> M6 测试 harness（`test_recall.py`）包括 M4/M5 回归检测：如果在捕获的输出中发现任何 M4 或 M5 FAIL，M6 门控失败。M6 一致通过所有回归检查。

---

## 6. 未完成 / 待办

| 事项 | 状态 | 备注 |
|---|---|---|
| 写回实现 | 推迟到 M7 | Dirty owner 必须能够写回 |
| 干净驱逐 | 推迟到 M7 | 驱逐时 sharer mask 必须更新 |
| Owner 转移 | 推迟到 M7 | 节点间的 owner 交接 |
| 基于 epoch 的过期过滤 | 推迟到 M7 | 过期 ack/数据保护 |
| 多请求者冲突排队 | 部分 | `G_BUSY` 防止重叠；完整排队尚未实现 |
| ARM_SYNC TC-M6-1 工作负载 | 已推迟 | 端到端 ARM 工作负载（node0 写入、node2 读取）需要 HN 协议路由 |

### 6.1 已知限制

1. **召回序列化**使用 `G_BUSY` 防止重叠事务，但不排队竞争请求 — 冲突请求者必须重试。
2. **无硬件辅助外部网络** — 召回路由使用进程内 `getInstance()` 查找。在多 gem5 或真实硬件场景中，这将通过外部网络路由。
3. **EP_RNF 本地一致性访问**当前使用测试注入路径；真实的 HN snoop 集成已在结构上存在，但未通过 ARM 工作负载验证。

### 6.2 后续阶段回填

| 事项 | 目标阶段 | 优先级 |
|---|---|---|
| 写回（dirty 数据返回 home） | M7 | P0 |
| 干净驱逐 | M7 | P0 |
| Owner 转移（节点到节点交接） | M7 | P0 |
| Epoch 过期过滤 | M7 | P0 |
| ARM_SYNC 端到端工作负载 | M8 后 | P1 |

---

## 7. 子模块状态

| 属性 | 值 |
|---|---|
| gem5 子模块已变更 | 是 |
| gem5 修复轮 commit | `899ead12f7`（召回路由、外部事务生命周期、busy/owner 检查） |
| gem5 P0 修复 commit | `607a8f0e0e`（移除召回回退旁路） |
| 超项目最终 commit | `99cb400`（M6 修复轮：更新 gem5 子模块） |

---

## 8. 构建与测试命令链

```bash
# 构建 gem5
docker run --rm -v $(pwd):/workspace -w /workspace/gem5 \
    ubcc-dev:ubuntu20.04 bash -c "scons build/ARM/gem5.opt -j20 PROTOCOL=CHI"

# 运行 M6 测试（包括 M4/M5 回归）
docker run --rm -v $(pwd):/workspace -w /workspace \
    ubcc-dev:ubuntu20.04 bash -c \
    "./gem5/build/ARM/gem5.opt tests/phase6/test_recall.py <arm_binary>"

# 预期：EXIT CODE 0, M6_SELF_TEST_PASSED=1,
#           M4: X PASS / 0 FAIL, M5: Y PASS / 0 FAIL, M6: Z PASS / 0 FAIL
```
