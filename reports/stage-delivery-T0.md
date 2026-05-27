# T0 阶段交付报告

- **阶段：** T0 — Sync_Wait(node_mask)
- **状态：** PASS
- **完成日期：** 2026-05-25（初次），2026-05-26（修复轮次）
- **审查轮次：** 3（初次 + 2 轮修复）
- **编排器判定：** PASS

---

## 1. 阶段摘要

### 1.1 阶段目标

实现 SE 模式跨节点屏障系统调用 `Sync_Wait(node_mask)`，为多节点定向协议测试用例提供可重复、可验证的同步。这是所有后续协议阶段（M4–M8）的硬性前提条件，这些阶段需要多节点时序控制。

### 1.2 完成状态

| 标准 | 结果 |
|---|---|
| 在 ARM SE 中注册系统调用 436 | PASS |
| 屏障仅计数显式调用者 | PASS |
| 不同 `node_mask` 实例隔离 | PASS |
| 屏障跨轮次可重用 | PASS |
| 参数验证（无效 masks） | PASS |
| TC-T0-1 到 TC-T0-7 全部通过 | 70/70 检查 PASS |
| 回归（TC1–TC5）不受影响 | PASS |

### 1.3 审查轮次

| 轮次 | 日期 | 关键发现 | 解决方案 |
|---|---|---|---|
| R1（初次） | 2026-05-25 | 基础实现已提交；等待 validator 审查 | — |
| R2（修复） | 2026-05-26 | P0：缺少参数验证；P1：断言薄弱；P2：预编译二进制文件 | 添加 3 个验证检查、加强断言、用仅源码自动编译替换二进制文件 |
| R3（修复） | 2026-05-26 | P1：TC-T0-3 非调用者断言匹配了调用者输出 | 改为 CPU 级别输出文件检查（70/70 总检查） |

---

## 2. 代码变更

### 2.1 gem5 子模块

| 文件 | 变更 | 描述 |
|---|---|---|
| `src/sim/sync_wait.hh` | 新增 | `SyncWaitManager` 类：每个 mask 的屏障映射、`popcount` 线程跟踪、挂起/唤醒、轮次间重置 |
| `src/sim/sync_wait.cc` | 新增 | `barrierWait()`：3 个验证检查（mask==0 → -EINVAL、N=3 之外的位 → -EINVAL、重复调用者 → 返回 0）、线程挂起/恢复、自动重置 |
| `src/sim/sync_wait.hh` | R2 修复 | 添加 `MAX_NODE_COUNT=3`、将 `barrierWait()` 返回值从 `void` 改为 `int` |
| `src/sim/sync_wait.cc` | R2 修复 | 添加 `#include <cerrno>`、3 个返回 `-EINVAL` 的参数验证检查 |
| `src/arch/arm/linux/se_workload.cc` | 修改 | 在 SyscallTable32/64 中注册系统调用 436；`syncWaitFunc<ABI>` 处理器从 ABI arg0 提取 `node_mask`，传递给 `SyncWaitManager` |
| `src/arch/arm/linux/se_workload.cc` | R2 修复 | 添加高 32 位检查；传播 `barrierWait()` 返回值 |
| `src/sim/system.hh` | 修改 | 向 `System` 类添加 `SyncWaitManager syncWait` 成员 |
| `src/sim/SConscript` | 修改 | 将 `Source('sync_wait.cc')` 添加到 sim 构建 |

**gem5 commit 历史（T0 相关）：**

| Commit | 描述 |
|---|---|
| `95e3e2763f` | T0：添加 SyncWaitManager 屏障，用于 SE 模式跨节点同步 |
| `9d714c6ea2` | T0 修复：添加 Sync_Wait 参数验证（mask=0、hi32 位、节点边界） |

### 2.2 超项目

| 文件 | 变更 | 描述 |
|---|---|---|
| `tests/sync_wait/tc_t0_1.c` | 新增 | TC-T0-1：3 个线程、mask=0b111、基本释放 |
| `tests/sync_wait/tc_t0_2.c` | 新增 | TC-T0-2：隔离、masks 0b011 和 0b100 |
| `tests/sync_wait/tc_t0_3.c` | 新增 | TC-T0-3：同节点多线程（调用者 + 非调用者） |
| `tests/sync_wait/tc_t0_4.c` | 新增 | TC-T0-4：可重用屏障（2 轮） |
| `tests/sync_wait/tc_t0_5.c` | 新增（R2） | TC-T0-5：mask=0 → `-EINVAL` 负面测试 |
| `tests/sync_wait/tc_t0_6.c` | 新增（R2） | TC-T0-6：高 32 位 → `-EINVAL` 负面测试 |
| `tests/sync_wait/tc_t0_7.c` | 新增（R2） | TC-T0-7：N=3 之外的位 → `-EINVAL` 负面测试 |
| `tests/sync_wait/test_sync_wait.py` | 新增 → R3 修复 | 测试驱动：每个用例的 gem5 调用、基于 trace 的全局排序、自动编译、70 个检查 |
| `.gitignore` | 新增（R2） | 排除生成的测试二进制文件（`tc_t0_*` 二进制文件） |
| `tests/sync_wait/tc_t0_{1,2,3_caller,3_noncaller,4}` | 已删除（R2） | 移除预编译二进制文件，替换为仅源码自动编译 |
| `reports/stage-t0-implementation-1.md` | 已更新 | R2 和 R3 修复摘要、70/70 结果 |

**超项目 commit 历史：**

| Commit | 描述 |
|---|---|
| `632d25a` | T0：添加 Sync_Wait 屏障测试基础设施 |
| `97dc12e` | T0 修复轮：更新阶段报告，含验证逻辑、测试结果、命令链 |
| `aedd906` | T0 修复轮：添加参数验证 + 负面测试 + 加强断言 |
| `42589ad` | T0 第 2 轮修复：清理二进制文件、基于 trace 的全局排序、精确 errno 检查、artifact-dir 支持 |
| `55dac63` | T0 第 3 轮：将 TC-T0-3 非调用者断言加强为 CPU 级别输出文件检查 |
| `b3dff28` | T0 第 3 轮报告：填写实际 commit hash（55dac63） |

---

## 3. 与原计划差异

### 3.1 与 `plan/03-phase-plan.md` 的对齐

| 计划 | 实际 | 备注 |
|---|---|---|
| 注册 ARM 自定义系统调用 | 已完成 | 系统调用 436 在 32/64 位表中 |
| 实现 `SyncWait` 屏障状态对象 | 已完成 | `SyncWaitManager` 类，带每个 mask 的隔离 |
| 使屏障状态全局可见 | 已完成 | 挂载在 `System` 上 |
| 支持 `node_mask` 隔离实例 | 已完成 | 以 `node_mask` 为键的映射 |
| 支持跨轮次可重用屏障 | 已完成 | 当所有线程唤醒时自动重置 |
| 最小测试工作负载 + 脚本 | 已完成 | 7 个工作负载、Python 测试驱动 |
| 仅计数显式调用者 | 已完成 | 仅调用 `Sync_Wait` 的线程被计数 |
| 无超时、无信号、无 FS 模式 Linux | 已完成 | 未实现 |

### 3.2 计划外的参数添加

| 添加内容 | 理由 |
|---|---|
| 3 个负面测试（TC-T0-5/6/7） | 由 validator 要求：`mask=0`、高 32 位、N=3 之外的位 |
| 返回值 `int` 而非 `void` | 需要传播无效输入时的 `-EINVAL` |
| 从 `.c` 源文件自动编译 | 消除预编译二进制文件依赖 |
| 基于 trace 的全局排序 | 使用 `SyscallBase` 调试 trace 构建跨 CPU 的全局排序时间线 |

### 3.3 与 `plan/02-external-proxy-spec.md` 的一致性

不适用 — T0 是同步基础设施阶段，不触及任何 EP/UBCC 组件。

### 3.4 实现简化（无）

`plan/03-phase-plan.md` §4 中的所有计划功能均已实现。无范围缩减。

---

## 4. 测试用例

### 4.1 TC-T0-1：屏障基本释放

| 属性 | 值 |
|---|---|
| **ID** | TC-T0-1 |
| **名称** | 屏障基本释放 |
| **类型** | ARM_SYNC |
| **断言数** | 11 |
| **预期** | 3 行 `BEFORE_BARRIER` 在任何 `AFTER_BARRIER` 之前；节点内 `BEFORE < AFTER` 顺序 |
| **实际** | PASS — 所有 3 个线程一起释放 |
| **负面测试** | 未观察到提前 `AFTER_BARRIER` |

### 4.2 TC-T0-2：按节点掩码的屏障隔离

| 属性 | 值 |
|---|---|
| **ID** | TC-T0-2 |
| **名称** | 按节点掩码的屏障隔离 |
| **类型** | ARM_SYNC |
| **断言数** | 12 |
| **预期** | Node0+1（mask=0b011）独立于 Node2（mask=0b100）释放 |
| **实际** | PASS — 按 mask 的屏障隔离已确认 |
| **负面测试** | 未检测到跨 mask 干扰 |

### 4.3 TC-T0-3：同节点多线程计数

| 属性 | 值 |
|---|---|
| **ID** | TC-T0-3 |
| **名称** | 同节点多线程计数 |
| **类型** | ARM_SYNC |
| **断言数** | 9 |
| **预期** | 3 个调用者通过屏障；1 个非调用者不产生 `AFTER_BARRIER` |
| **实际** | PASS — 非调用者输出通过 CPU 级别输出文件检查验证干净 |
| **负面测试** | 非调用者不计入屏障总数 |

### 4.4 TC-T0-4：可重用屏障

| 属性 | 值 |
|---|---|
| **ID** | TC-T0-4 |
| **名称** | 可重用屏障 |
| **类型** | ARM_SYNC |
| **断言数** | 20 |
| **预期** | 2 个完整轮次、全局计数正确、R1 排序在 R2 之前 |
| **实际** | PASS — 第 1 轮的过期状态不影响第 2 轮 |
| **负面测试** | 无跨轮次干扰 |

### 4.5 TC-T0-5：Mask=0 拒绝（负面）

| 属性 | 值 |
|---|---|
| **ID** | TC-T0-5 |
| **名称** | Mask=0 被拒绝 |
| **类型** | ARM_SYNC（负面） |
| **断言数** | 6 |
| **预期** | `Sync_Wait(0)` 返回 `-EINVAL`（-22），无阻塞 |
| **实际** | PASS — 系统调用立即返回 -22 |
| **负面测试** | 无阻塞、无成功返回 |

### 4.6 TC-T0-6：高 32 位拒绝（负面）

| 属性 | 值 |
|---|---|
| **ID** | TC-T0-6 |
| **名称** | 高 32 位被拒绝 |
| **类型** | ARM_SYNC（负面） |
| **断言数** | 6 |
| **预期** | `Sync_Wait(0x1_0000_0007)` 返回 `-EINVAL`（-22） |
| **实际** | PASS — hi-32 位守卫在低 32 位评估之前触发 |
| **负面测试** | 无阻塞、无 mask 误解释 |

### 4.7 TC-T0-7：N=3 之外的位拒绝（负面）

| 属性 | 值 |
|---|---|
| **ID** | TC-T0-7 |
| **名称** | N=3 之外的位被拒绝 |
| **类型** | ARM_SYNC（负面） |
| **断言数** | 6 |
| **预期** | `Sync_Wait(0b1000)` 返回 `-EINVAL`（-22） |
| **实际** | PASS — 带超出 MAX_NODE_COUNT-1 的位的 mask 被拒绝 |
| **负面测试** | 无阻塞、无无效节点被目标 |

### 4.8 参数验证逻辑汇总

| 检查 | 位置 | 条件 | 错误 |
|---|---|---|---|
| mask == 0 | `barrierWait()` | `node_mask == 0` | `-EINVAL` |
| N=3 之外的位 | `barrierWait()` | `node_mask & ~((1<<MAX_NODE_COUNT)-1)` | `-EINVAL` |
| 高 32 位非零 | `syncWaitFunc()` | `node_mask >> 32` | `-EINVAL` |

---

## 5. 回归结果

| 测试 | 状态 | 详情 |
|---|---|---|
| TC1 (`test_pa_layout_mode.py`) | 预先存在的 PASS | 不受影响 — T0 不触及 PA layout 或 Ruby |
| TC2 (`run_phase1_test.py`) | 预先存在的 PASS | 不受影响 |
| TC2E (`run_phase1_test_enhanced.py`) | 预先存在的 PASS | 不受影响 |
| TC3 (`verify_topo_objects.py`) | 预先存在的 PASS | 不受影响 |
| TC4 (`test_ruby_create_system_n3l2d2.py`) | 预先存在的 PASS | 不受影响 |
| TC5 (`test_ep_instantiate.py`) | 预先存在的 PASS | 不受影响 |

> T0 变更（`sync_wait.{hh,cc}`、`se_workload.cc`）不触及任何 Ruby 内存系统、拓扑配置或 EP 控制器路径。无回归风险。

---

## 6. 未完成 / 待办

| 事项 | 状态 | 备注 |
|---|---|---|
| 超时机制 | 未实现 | 对定向测试用例可接受；任何卡住的线程都是测试失败 |
| 序列化/检查点支持 | 未实现 | 对 T0 范围可接受 |
| `MAX_NODE_COUNT` 硬编码 | 尚未可配置 | 当前硬编码为 3；应在未来阶段从拓扑派生 |
| 全系统 Linux 支持 | 未实现 | 按计划明确排除 |

### 6.1 已知限制

1. 如果某个线程从未调用屏障，等待中的线程将永远阻塞 — 对确定性测试用例可接受。
2. `MAX_NODE_COUNT = 3` 是硬编码的；如果 N 变化，必须更新。
3. 屏障状态不受检查点管理，因此不支持检查点/恢复场景。

### 6.2 后续阶段回填

| 事项 | 目标阶段 | 优先级 |
|---|---|---|
| 可配置的 `MAX_NODE_COUNT` | M9 或 M8 后清理 | P2 |
| 超时支持 | 无 — 未计划 | — |

---

## 7. 子模块状态

| 属性 | 值 |
|---|---|
| gem5 子模块已变更 | 是（R2 修复） |
| gem5 最终 commit | `9d714c6ea293d2add442b5d6ef86c9c36c659bef` |
| gem5 原始 T0 commit | `95e3e2763f44e76c232cdb55ec1de50dc06fa5d5` |
| 超项目最终 commit | `55dac63910d5ce93a053ac4e1c9b32222f7f784c` |
| 超项目初始 commit | `632d25a` |

---

## 8. 构建与测试命令链

```bash
# 构建 gem5
docker run --rm -v $(pwd):/workspace -w /workspace/gem5 \
    ubcc-dev:ubuntu20.04 bash -c "scons build/ARM/gem5.opt -j20 PROTOCOL=CHI"

# 运行 T0 测试
docker run --rm -v $(pwd):/workspace -w /workspace \
    ubcc-dev:ubuntu20.04 bash -c "python3 tests/sync_wait/test_sync_wait.py"

# 预期：Results: 70/70 tests passed
```
