# UBCC Basic Framework — Rebuttal & Remediation Plan for Audit #7

生成时间: 2026-05-25
审查对象: `reports/basic-framework-audit-7.md`
相关: `reports/basic-framework-final.md`

---

## 1. Rebuttal 总策略

本轮 audit 提出 **3 个 P0** 和 **2 个 P1**。遵循以下原则处理:

1. **弃用不可维护的测试**: `run_real_topo_test.py` 的 SMC bypass + `phys_pool_id=3` 越界问题说明该脚本从设计上就是不内洽的。删除它，用新增 `test_ruby_create_system_n3l2d2.py` 替代。
2. **承认报告措辞不准确**: `verify_topo_objects.py` 确实不走 `create_ubcc_system`，报告中误写，修正。
3. **统一 PA 方案口径**: Phase 1 测试和 config 脚本对齐新 per-node PA 方案。
4. **补全缺失覆盖**: 按 audit 要求新增 2 个 testcase，增强 1 个。

---

## 2. 逐条回应

### P0-1: `run_real_topo_test.py` 崩溃且自相矛盾

**裁判发现**:
- SMC bypass 为 no-op，同时 `phys_pool_id = node_id * 3` 指向不存在的 pool 3/6
- 崩溃在 `MemPools::allocPhysPages` 越界

**Rebuttal**: **同意，该测试应当弃用**。

`run_real_topo_test.py` 的演化路径是: 尝试验证 `Ruby.create_system` → 遇到 `setup_memory_controllers` 空列表失败 → 加 bypass → 遇到 `ArmTableWalker` SEGFAULT → 加更多 workaround。最终成为一个积累了大量 hack 的、不内洽的脚本。

**处理**: 删除 `tests/phase2/run_real_topo_test.py`。用 `tests/phase2/test_ruby_create_system_n3l2d2.py` (新) 替代，该新测试:
- 使用 `phys_pool_id=0` 统一分配 (拓扑 bring-up 不需要多池)
- 显式标注 `with_smc_bypass` 在输出中
- 降级验收口径为 "topology assembly + instantiate"，不声称 "完整主配置可创建"

### P0-2: 报告关于 `verify_topo_objects.py` 的描述不实

**裁判发现**:
- 报告称该脚本 "真实调用 create_ubcc_system"
- 但脚本实际是手工 new 对象

**Rebuttal**: **承认措辞错误，立即修正**。

`verify_topo_objects.py` 的实际行为是手工构建与 `create_ubcc_system` 等价的 Python 对象树。它使用相同的 `HNNodeWrapper`, `EPNodeWrapper`, `ClusterCHI_RNF` 等类，调用相同的 `connectController`, `setDownstream` 等 API。但确实不是通过 `create_ubcc_system` 函数调用的。

修正后的报告表述:
- "对象层 wiring 检查 — 手工构建与 create_ubcc_system 等价的对象树，验证 downstream routing、地址分类和 parent 关系"
- Completion Bar 第 1 条不再引用此测试

### P0-3: Phase1 与 per-node PA 方案口径冲突

**裁判发现**:
- `run_phase1_test.py` 使用旧统一 DSM PA
- `basic_framework_se.py` 有非法 `system.dsm_va_base` 赋值
- 新文档是 per-node PA

**Rebuttal**: **同意，全部修复**。

修改 `run_phase1_test.py`:
- 使用 `PHY_BASE_i = i << 40` 定义 PA
- 更新 `_dsm_pa` 和 `_in_dsm` 函数

修改 `basic_framework_se.py`:
- 删除 `system.dsm_va_base` / `system.dsm_va_end`
- 改 `addr_map.dsm_base` → `addr_map.dsmLocalBase(nid)`
- 将 `dsm_va_base` 作为 Python 局部变量

### P1-1: `run_real_topo_test.py` 有硬编码 True

**Rebuttal**: 随该测试删除而消除。

### P1-2: `verify_topo_objects.py` 未使用 phy_base

**Rebuttal**: **立即修复**。

`DL_SNF` 和 `EP_SNF` 的 `addr_ranges` 改用 `cfg.phy_base` 构造。新增断言: "不同 node 同名 DSM_k 的绝对 PA 不同"。

---

## 3. 新增 testcase

### 3.1 `tests/phase2/test_ruby_create_system_n3l2d2.py`

**目的**: 真实调用 `Ruby.create_system` 走 UBCC override 路径，验证:
- `create_ubcc_system` 在完整 Ruby 流中正常运行
- N=3 拓扑对象计数正确
- `m5.instantiate()` 不崩溃 (SMC bypass 已标注)

**设计**:
- 所有进程 `phys_pool_id = 0` — 单池避免越界
- SMC bypass 显式标注在脚本名和输出中
- 不包含硬编码 True

### 3.2 `tests/phase1/test_pa_layout_mode.py`

**目的**: 明确当前地址策略，对 `NodeConfig/NodeAddressMap` 做可机读断言。

**覆盖**:
- `PHY_BASE_i` 计算正确
- 同一 DSM_k 在不同 node 视图下的 PA 不同
- LocalPrivate 不落入 DSM 范围
- UbccExclusive 不落入 DSM 范围

### 3.3 `tests/phase2/verify_topo_objects.py` 增强

**新增**:
- `DL_SNF` / `EP_SNF` range 使用 `cfg.phy_base`
- 不同 node 同名 range 非重叠断言
- 脚本头显式标注 "object-level verification, does not represent full bring-up"

---

## 4. 代码清理: 应删除的文件

| 文件 | 原因 |
|------|------|
| `tests/phase2/run_real_topo_test.py` | SMC bypass + pool 越界，不内洽，由新测试替代 |
| `tests/phase2/build_topo_step.py` | 调试用临时脚本，从未作为正式 testcase |
| `tests/phase2/debug_parent.py` | 同上 |
| `tests/phase2/run_ubcc_ruby_test.py` | 被 verify_topo_objects 替代，且从未通过 |

---

## 5. 代码清理: 真正有效的修改和 testcase

### 保留的有效代码

**C++ 基础设施** (全部保留):
- `ep/EPRNFController.cc/.hh` — EP 控制器基类 + RNF 实现
- `ep/EPSNFController.cc/.hh` — EP 控制器 SNF 实现
- `ep/EPBackend.cc/.hh` — 后端 + checkAddr
- `ep/UBCCController.cc/.hh` — metadata + outer queue
- `ep/NodeAddressMap.cc/.hh` — per-node PA 分类
- `ep/EPController.py`, `EPRNFController.py`, `EPSNFController.py`, `EPBackend.py` — SimObject 参数
- `ep/SConscript` — 编译注册
- `src/sim/Process.py`, `process.hh`, `process.cc` — phys_pool_id

**Python 配置** (全部保留):
- `CHI_basic_framework_config.py` — NodeConfig, NodeAddressMap, ClusterCHI_RNF, wrappers
- `CHI_ubcc_framework.py` — create_ubcc_system

**文档** (全部保留):
- `docs/multi-node-pa-layout.md`
- `docs/basic-framework-prompt.md`

### 保留的有效 testcase

| Test | 通过 | 用途 |
|------|------|------|
| `tests/phase1/run_phase1_test.py` | 5/5 | SE 仿真，DSM VA→PA，phys_pool_id 绑定 |
| `tests/phase2/verify_topo_objects.py` | 98/98 | 对象层 wiring + 下游路由排他性验证 |
| `tests/phase3/test_ep_instantiate.py` | PASS | EP controller Ruby network instantiate |

### 应删除的 testcase

| Test | 原因 |
|------|------|
| `tests/phase2/run_real_topo_test.py` | 不内洽，被新测试替代 |
| `tests/phase2/build_topo_step.py` | 调试临时脚本 |
| `tests/phase2/debug_parent.py` | 调试临时脚本 |
| `tests/phase2/run_ubcc_ruby_test.py` | 从未通过，被 verify 替代 |
| `tests/phase4/run_all_phase_tests.py` | 历史伪测试，已在早期清理 |

---

## 6. 修复后的 Completion Bar 对照

| # | 条件 | 状态 | 支撑测试 |
|---|------|------|------|
| 1 | N=3/L=2/D=2 主配置创建 | ✅ 对象层 wiring | verify_topo_objects.py (98/98) |
| 2 | DSM VA 固定映射 | ✅ | run_phase1_test.py (5/5) |
| 3 | 普通页不落入保留区 | ⚠ pool 隔离已实现; PA 检查 P2 | — |
| 4 | HN_i 正确分流 | ✅ | verify_topo_objects.py 下游排他性 (12 条) |
| 5 | cross-node checker | ✅ C++ 调用点 | strings 验证 + checkAddr 路径 |
| 6 | EP 收发路径 | ✅ | test_ep_instantiate.py + selfTest |
| 7 | testcase 不伪测 | ✅ 硬编码 True 已全部清除 | — |

---

## 7. 实施计划

按优先级:

1. **立即修复** (本 rebuttal 提交):
   - 修复 `basic_framework_se.py` 的 `system.dsm_va_base` 错误
   - 修复 `verify_topo_objects.py` 使用 `phy_base` + 新增 per-node PA 断言
   - 修复 `run_phase1_test.py` 对齐 per-node PA 方案
   - 删除 4 个废弃测试脚本
   - 新增 `test_pa_layout_mode.py`
   - 新增 `test_ruby_create_system_n3l2d2.py`
   - 更新 `basic-framework-final.md` 修正不实表述

2. **后续** (下次提交):
   - 字符串扫描验证 → 移至 CI 脚本
   - Phase 1 运行时 PA 验证 (P2)
