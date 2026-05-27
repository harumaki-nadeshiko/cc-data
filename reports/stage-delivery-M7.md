# M7 阶段交付报告

- **阶段：** M7 — 写回 / 驱逐 / Owner 转移
- **状态：** PASS
- **完成日期：** 2026-05-27
- **审查轮次：** 2（初次 + 修复轮）
- **编排器判定：** PASS

---

## 1. 阶段摘要

### 1.1 阶段目标

完成写回/驱逐/owner 转移循环以支持完整的三节点一致性：dirty 写回更新 home 元数据，干净驱逐更新 sharer mask，owner 在节点间转移被序列化，过期 epoch 被拒绝，召回结果正确拆分（读取→共享降级，写入→失效）。

### 1.2 完成状态

| 标准 | 结果 |
|---|---|
| Dirty 写回 → home ack | PASS |
| 干净驱逐 → sharer mask 更新 | PASS |
| Owner 转移（单一 owner 不变量） | PASS |
| Epoch 过期过滤 | PASS |
| Home UBCC 仍仅元数据 | PASS |
| 召回结果拆分（读取→共享，写入→无效） | PASS |
| 任意时刻单一全局 owner | PASS |

### 1.3 审查轮次

| 轮次 | 日期 | 关键发现 | 解决方案 |
|---|---|---|---|
| R1（初次） | 2026-05-26 | 完整 M7 实现已提交 | 等待 validator 审查 |
| 修复轮 | 2026-05-27 | P0：owner 不匹配拒绝（非 owner 写回被拒绝）、P0：驱逐 dirty 守卫（dirty owner 驱逐阻止直到写回）、P0：驱逐非 owner 拒绝；P1：召回拆分 PA 修复、epoch 0 移除守卫 | 所有 P0/P1 已解决 |

---

## 2. 代码变更

### 2.1 gem5 子模块

| 文件 | 变更 | 描述 |
|---|---|---|
| `src/mem/ruby/protocol/chi/ep/UBCCController.hh` | 扩展 | `processWriteback()`：处理来自 dirty owner 的 `GlobalWriteback`；`processEvict()`：处理来自干净 sharer/owner 的 `GlobalEvict`；`processOwnerTransfer()`：序列化 owner 交接；epoch 管理：每行 `epoch` 计数器、`validateEpoch()` 过期检查 |
| `src/mem/ruby/protocol/chi/ep/UBCCController.cc` | 扩展 | **写回路径**：owner 发送含数据 + dirty 标志的 `GlobalWriteback` → home 更新元数据（dirty=false，可能转换到 `G_I`/`G_S`/`G_E`），发送 `GlobalAck` → owner 然后可以驱逐。**驱逐路径**：sharer 发送 `GlobalEvict` → home 从 mask 中移除 sharer → 如果 mask 变空，行变为 `G_I`。**Owner 转移**：旧 owner 被召回/失效，新 owner 被安装 — 通过 epoch 强制执行单一 owner 不变量。**Epoch 过滤**：过期响应（epoch < 当前）被拒绝并记录；epoch=0 条目被移除。**召回结果拆分**：读取召回 → 旧 owner 降级为共享（保留在 sharers mask 中），写入/唯一召回 → 旧 owner 被失效（从 sharers mask 中移除） |
| `src/mem/ruby/protocol/chi/ep/EPBackend.hh` | 扩展 | `handleWriteback()`：请求者侧写回启动；`handleEvict()`：请求者侧驱逐；用于测试观察的写回计数器 + 驱逐计数器 |
| `src/mem/ruby/protocol/chi/ep/EPBackend.cc` | 扩展 | 写回流：请求者检测 remote dirty 行的 HN 驱逐/写回 → 向 home 发送 `GlobalWriteback` → 等待 `GlobalAck` → 完成。驱逐流：请求者检测干净驱逐 → 发送 `GlobalEvict` → home 确认。Owner 转移：home UBCC 协调旧 owner 失效和新 owner 安装 |
| `src/mem/ruby/protocol/chi/ep/M7SelfTest.cc` | 新增 | 52 个三元检查：TC-M7-1 dirty 写回（6 个检查：写回更新状态、dirty 被清除、后续读取看到正确状态）、TC-M7-2 干净驱逐（6 个检查：驱逐移除 sharer、dirty 未设置、非 owner 驱逐被拒绝、dirty owner 驱逐被阻止）、TC-M7-3 单一全局 owner（6 个检查：节点间 owner 转移、永无双 owner）、TC-M7-4 过期 epoch 被拒绝（8 个检查：过期 ack/数据被拒绝、不污染当前事务、epoch 不匹配检测）、TC-M7-5 仅元数据（4 个检查：写回/驱逐/转移不添加数据存储）、TC-M7-6 召回结果拆分（10 个检查：读取→共享降级、写入→失效）、加 EPBackend 计数器和结构检查 |

**gem5 commit 历史（M7 相关）：**

| Commit | 描述 |
|---|---|
| `b41fe6012c` | M7 修复轮：P0（owner 不匹配拒绝、驱逐 dirty 守卫、驱逐非 owner 拒绝）+ P1（召回拆分 PA、epoch 0 移除） |

### 2.2 超项目

| 文件 | 变更 | 描述 |
|---|---|---|
| `tests/phase7/test_m7.py` | 仅本地验证脚本（未提交到仓库） | PY_INJECT harness：完整 CHI+UBCC 拓扑，在实例化时运行 M4/M5/M6/M7 所有自检，捕获 C++ stdout，解析所有四个阶段的 PASS/FAIL，回归门控（M4/M5/M6 失败阻止 M7），所有 6 个 M7 测试用例的测试用例覆盖报告 |

**超项目 commit 历史：**

| Commit | 描述 |
|---|---|
| `7e5a1d4` | M7 修复轮：更新 gem5 子模块（P0 owner 不匹配拒绝、驱逐守卫、P1 召回 PA、epoch） |

---

## 3. 与原计划差异

### 3.1 与 `plan/03-phase-plan.md` 的对齐

| 计划 | 实际 | 备注 |
|---|---|---|
| 请求者 dirty 写回 → home ack | 已完成 | `GlobalWriteback` 路径：owner 发送数据 → home 更新 → `GlobalAck` → 请求者完成 |
| 请求者干净驱逐 → sharer mask 更新 | 已完成 | `GlobalEvict` 路径：sharer 从 mask 中移除；如 mask 空则自动转换到 `G_I` |
| Owner 转移 | 已完成 | 通过 epoch 序列化；强制执行单一 owner 不变量 |
| Epoch 或等价的过期保护 | 已完成 | 每行 epoch 计数器；过期响应（epoch < 当前）被拒绝 |
| Home UBCC 仍仅元数据 | 已完成 | M7 中未添加行数据存储；所有数据流经 owner 节点 |
| 召回结果拆分（读取→共享，写入→无效） | 已完成 | 读取召回：owner 降级为共享；写入/唯一召回：owner 被失效 |

### 3.2 关键设计决策

| 决策 | 理由 |
|---|---|
| 非 owner 写回拒绝 | 仅当前 owner（由目录中 `ownerNode` 验证）可以写回；不匹配的写回被拒绝 |
| Dirty owner 驱逐阻止 | Dirty owner 必须在驱逐前写回；对 dirty owner 行的驱逐被拒绝 |
| Epoch 0 条目移除 | 将 epoch=0 的条目视为无效/已移除；防止过期零值问题 |
| Sharer 驱逐 → 自动 `G_I` | 当最后一个 sharer 驱逐时，行返回 `G_I` |
| 召回 PA 验证 | 召回响应 PA 必须与被召回的行 PA 匹配；不匹配 → fatal |

### 3.3 写回/驱逐/转移状态表

| 当前 Home 状态 | 事件 | 守卫 | 动作 | 下一状态 |
|---|---|---|---|---|
| `G_M` | 来自 owner 的 `GlobalWriteback` | `epoch` 匹配、`requester == ownerNode` | 元数据更新：dirty=false | `G_I`（如无 sharers）或 `G_S` |
| `G_S` | 来自 sharer 的 `GlobalEvict` | `sharer in mask`、非 dirty | 从 mask 中移除 sharer | `G_S` 或 `G_I` |
| `G_E` | 来自干净 owner 的 `GlobalEvict` | `requester == ownerNode`、`dirty==false` | 清除 owner | `G_I` |
| `G_E/G_M` | Owner 转移请求 | 竞争 unique/write | 召回旧 owner → 安装新 owner | `G_E` 或 `G_M` |

### 3.4 Epoch 过滤

| 当前 Epoch | 传入响应 Epoch | 动作 |
|---|---|---|
| N | N | 接受（如事务上下文匹配） |
| N | < N | 作为过期拒绝（不变更状态） |
| N | > N | 拒绝（除非明确支持前向 epoch 创建 — M7 不支持） |

### 3.5 召回结果拆分

| 触发 | 旧 Owner 结果 |
|---|---|
| 远程读取召回 owner | 旧 owner 降级为共享（`G_S`，保留在 sharers mask 中） |
| 远程 unique/write 召回 owner | 旧 owner 被失效（从所有 mask 中移除，行变为 `G_I` 或新 owner 获得 `G_E`/`G_M`） |

### 3.6 范围边界

| 范围内（已实现） | 尚未实现（M8） |
|---|---|
| 单一全局 owner 不变量 | 多 sharer 管理（M8） |
| Dirty 写回 → home → ack | 共享加固（M8） |
| 干净驱逐 → sharer mask 更新 | 升级的 GlobalInvalidate（M8） |
| 过期 epoch 过滤 | — |
| 召回结果拆分（降级 vs 失效） | — |

### 3.7 与 `plan/02-external-proxy-spec.md` 的一致性

| 规格要求 | 实现 | 状态 |
|---|---|---|
| Owner 写回更新 home（§7.2） | `processWriteback()`：元数据更新、`GlobalAck` | PASS |
| 干净驱逐更新 sharer mask（§7.2） | `processEvict()`：sharer 移除、mask 清理 | PASS |
| 单一 owner 不变量（§8） | Epoch 序列化、owner 不匹配拒绝 | PASS |
| Home 持续仅元数据（§6.1） | 无数据存储；写回数据路由通过 | PASS |
| 召回结果拆分（§8.1-8.2） | 读取 → 降级共享；Unique/write → 失效 | PASS |

---

## 4. 测试用例

### 4.1 TC-M7-1：Dirty 写回更新 Home

| 属性 | 值 |
|---|---|
| **ID** | TC-M7-1（M7-1-1 到 M7-1-6） |
| **名称** | Dirty 写回更新 Home |
| **类型** | PY_INJECT（C++ 自检） |
| **断言数** | 6 |
| **预期** | 来自 dirty owner 的写回更新目录状态；dirty 标志被清除；后续读取看到正确状态；数据未丢失；写回计数器递增 |
| **实际** | PASS |
| **负面测试** | 来自非 owner 的写回被拒绝 |

### 4.2 TC-M7-2：干净驱逐更新 Sharer Mask

| 属性 | 值 |
|---|---|
| **ID** | TC-M7-2（M7-2-1 到 M7-2-5、M7-2-ext） |
| **名称** | 干净驱逐更新 Sharer Mask |
| **类型** | PY_INJECT（C++ 自检） |
| **断言数** | 6 |
| **预期** | 驱逐从 mask 中移除 sharer；驱逐不设置 dirty；非 owner 驱逐被拒绝；dirty owner 驱逐被阻止；sharer mask 正确反映剩余节点 |
| **实际** | PASS |
| **负面测试** | 非 owner 驱逐被拒绝；dirty owner 驱逐被阻止 |

### 4.3 TC-M7-3：乒乓场景中单一全局 Owner

| 属性 | 值 |
|---|---|
| **ID** | TC-M7-3（M7-3-1 到 M7-3-6） |
| **名称** | 乒乓场景中单一全局 Owner |
| **类型** | PY_INJECT（C++ 自检） |
| **断言数** | 6 |
| **预期** | 在 owner 转移期间的每个快照中，`ownerNode` 是唯一的；无双 owner 状态；owner 转移通过 epoch 序列化 |
| **实际** | PASS |
| **负面测试** | 无两个 owner 的中间状态 |

### 4.4 TC-M7-4：过期 Epoch 被拒绝

| 属性 | 值 |
|---|---|
| **ID** | TC-M7-4（M7-4-1 到 M7-4-8） |
| **名称** | 过期 Epoch 被拒绝 |
| **类型** | PY_INJECT（C++ 自检） |
| **断言数** | 8 |
| **预期** | 过期响应（epoch < 当前）被拒绝；无状态变更；当前事务使用正确 epoch；epoch 不匹配检测有效；过期 ack 被丢弃；过期数据被丢弃 |
| **实际** | PASS |
| **负面测试** | 过期响应不污染目录状态 |

### 4.5 TC-M7-5：仅元数据 Home 仍然正确

| 属性 | 值 |
|---|---|
| **ID** | TC-M7-5（M7-5-1 到 M7-5-4） |
| **名称** | 仅元数据 Home 仍然正确 |
| **类型** | PY_INJECT（C++ 自检） |
| **断言数** | 4 |
| **预期** | 写回/驱逐/转移操作不要求 home UBCC 存储行数据；目录状态与后续读取一致 |
| **实际** | PASS |
| **负面测试** | DirEntry 中未添加行数据字段 |

### 4.6 TC-M7-6：召回结果拆分

| 属性 | 值 |
|---|---|
| **ID** | TC-M7-6（M7-6a-1..5、M7-6b-1..5） |
| **名称** | 召回结果拆分 |
| **类型** | PY_INJECT（C++ 自检） |
| **断言数** | 10 |
| **预期** | 子场景 A（读取召回）：旧 owner 降级为共享；子场景 B（写入/唯一召回）：旧 owner 被失效；两者产生不同的可观察状态 |
| **实际** | PASS |
| **负面测试** | 两个场景不产生相同的结果状态 |

### 4.7 汇总

| 测试组 | 检查数 | PASS | FAIL | SKIP |
|---|---|---|---|---|
| TC-M7-1（Dirty 写回） | 6 | 6 | 0 | 0 |
| TC-M7-2（干净驱逐） | 6 | 6 | 0 | 0 |
| TC-M7-3（单一全局 owner） | 6 | 6 | 0 | 0 |
| TC-M7-4（过期 epoch） | 8 | 8 | 0 | 0 |
| TC-M7-5（仅元数据） | 4 | 4 | 0 | 0 |
| TC-M7-6（召回拆分） | 10 | 10 | 0 | 0 |
| M7-INFRA + 计数器 | 12 | 12 | 0 | 0 |
| **合计** | **52** | **52** | **0** | **0** |

---

## 5. 回归结果

| 测试 | 状态 | 备注 |
|---|---|---|
| TC1–TC5 | 预先存在的 PASS | 不受影响 |
| M4 自检（M7 内） | 0 FAIL | 从 M4 无回归 |
| M5 自检（M7 内） | 0 FAIL | 从 M5 无回归 |
| M6 自检（M7 内） | 0 FAIL | 从 M6 无回归 |
| M7 自检 | 0 FAIL | 所有 52 个检查通过 |

> M7 测试 harness（`test_m7.py`）包括 M4/M5/M6 的累积回归检测。所有阶段报告 0 FAIL。

---

## 6. 未完成 / 待办

| 事项 | 状态 | 备注 |
|---|---|---|
| 多 sharer 共享路径加固 | 推迟到 M8 | 并发访问下的 sharer mask 正确性 |
| 升级的 GlobalInvalidate（M8） | 推迟到 M8 | 当本地升级遇到外部 sharer 时 |
| ARM_SYNC 端到端工作负载 | 已推迟 | M7 使用 PY_INJECT（C++ 自检）；ARM 工作负载已推迟 |

### 6.1 已知限制

1. **写回数据流**已通过结构验证；在单 gem5 原型中，`GlobalWriteback` 数据通过进程内方法调用传递。在真正的多 gem5 部署中，数据必须穿越外部网络。
2. **Owner 转移**序列化有效，但不是最优地处理级联多跳转移 — 重点是正确性，而非延迟。
3. **Epoch 管理**是每行且单调的；无全局 epoch 计数器。

### 6.2 后续阶段回填

| 事项 | 目标阶段 | 优先级 |
|---|---|---|
| 多 sharer 共享加固 | M8 | P0 |
| GlobalInvalidate / 升级路径 | M8 | P0 |
| Owner 转移延迟优化 | M8 后 | P2 |
| ARM_SYNC 工作负载端到端 | M8 后 | P1 |

---

## 7. 子模块状态

| 属性 | 值 |
|---|---|
| gem5 子模块已变更 | 是 |
| gem5 修复轮 commit | `b41fe6012c`（P0 + P1 修复） |
| 超项目最终 commit | `7e5a1d4`（M7 修复轮：更新 gem5 子模块） |

---

## 8. 构建与测试命令链

```bash
# 构建 gem5
docker run --rm -v $(pwd):/workspace -w /workspace/gem5 \
    ubcc-dev:ubuntu20.04 bash -c "scons build/ARM/gem5.opt -j20 PROTOCOL=CHI"

# 运行 M7 测试（包括 M4/M5/M6 回归）
docker run --rm -v $(pwd):/workspace -w /workspace \
    ubcc-dev:ubuntu20.04 bash -c \
    "./gem5/build/ARM/gem5.opt tests/phase7/test_m7.py <arm_binary>"

# 预期：EXIT CODE 0, M7_SELF_TEST_PASSED=1,
#           M4:0 FAIL, M5:0 FAIL, M6:0 FAIL, M7:0 FAIL
```
