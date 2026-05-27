# UBCC M3.5–M8 总交付报告

- **项目名称：** UBCC (Unified Bus Cache Coherence)
- **覆盖阶段：** M3.5 → T0 → M4 → M5 → M6 → M7 → M8
- **总状态：** **全部 PASS**
- **总测试断言数：** **349**
- **总审查轮次：** **19**
- **总 Commit 数：** **46**（gem5 子模块 17 + 超项目 29）
- **报告生成日期：** 2026-05-27

---

## 1. 总体概览

### 1.1 项目背景

UBCC（Unified Bus Cache Coherence）项目在 gem5 多节点 CHI 拓扑之上实现外部代理（External Proxy）一致性协议。自 M3.5 多智能体协作冒烟检查起，到 M8 共享读取加固与升级/失效闭环完成止，本项目共经历 7 个阶段，累计完成 349 项测试断言，经历 19 轮审查迭代，累计交付 46 个 commit，所有阶段均通过验收。

### 1.2 阶段路线

```
M3.5 (协作冒烟) → T0 (同步原语) → M4 (哨兵注册) → M5 (侧带+首次缺失)
  → M6 (目录+召回) → M7 (写回/驱逐/转移) → M8 (共享加固+升级/失效)
```

M3.5 验证 orchestrator → implementer → validator 协作链路；T0 提供多节点同步基础设施（`Sync_Wait`），是所有后续协议阶段的硬前置条件；M4–M8 逐步构建完整的外部代理一致性协议闭环。

### 1.3 汇总数字

| 指标 | 数值 |
|---|---|
| 覆盖阶段 | 7 个（M3.5、T0、M4、M5、M6、M7、M8） |
| 总体状态 | 全部 PASS |
| 总测试断言数 | 349 |
| 总审查轮次 | 19 |
| gem5 子模块 commit 数 | 17 |
| 超项目 commit 数 | 29 |
| 测试类型分布 | ORCH_FLOW / ARM_SYNC / PY_INJECT（C++ 自检） |

---

## 2. 各阶段摘要表

| 阶段 | 目标简述 | 状态 | 测试断言数 | 审查轮次 | 关键交付物 |
|---|---|---|---|---|---|
| **M3.5** | 多智能体协作冒烟检查 | PASS | 1 | 1 | `readme.md` 修改验证 |
| **T0** | Sync_Wait 跨节点屏障系统调用 | PASS | 70 | 3 | `SyncWaitManager`、ARM syscall 436、7 个测试工作负载 |
| **M4** | 哨兵注册（EP_RNF 目录条目） | PASS | 36 | 4 | `SentinelHelper`、`DirEntrySnapshot`、FAIL 注入验证 |
| **M5** | 远程缺失与权限侧带 | PASS | 77 | 2 阶段（4 轮） | CHI 侧带字段、MESI grant 决策引擎、首次缺失闭环 |
| **M6** | UBCC 目录 + EP_RNF 本地一致性访问 | PASS | 52 | 2 | `GlobalRecallOwner`、EP_RNF 延迟 HN 响应、目录一致性 |
| **M7** | 写回 / 驱逐 / Owner 转移 | PASS | 52 | 2 | Dirty 写回、干净驱逐、Owner 转移序列化、Epoch 过期过滤 |
| **M8** | 共享读取加固与升级/失效闭环 | PASS | 61 | 3 | `GlobalInvalidate`、多 sharer mask 维护、共享默认路径启用 |

---

## 3. 各阶段详细说明

### 3.1 M3.5 — 多智能体协作冒烟检查

- **阶段目标：** 验证 orchestrator → implementer → validator 协作链按预期工作。
- **核心实现内容：**
  - Implementer 在仓库根目录 `readme.md` 中新增测试行 `Agent test 666!`
  - Validator 确认该行存在并给出 PASS 判定
  - Orchestrator 在 PASS 后按规则暂停，等待用户确认
- **测试结果：**
  - 断言数：1（validator 行存在性检查）
  - 全部通过，验证后测试行已被清理
- **与原计划差异：** 无。完全按 `plan/03-phase-plan.md` §2.5 执行。
- **未完成/待办：** 无。

### 3.2 T0 — Sync_Wait(node_mask)

- **阶段目标：** 实现 SE 模式跨节点屏障系统调用 `Sync_Wait(node_mask)`，为后续所有协议阶段提供可重复、可验证的多节点同步。
- **核心实现内容：**
  - `SyncWaitManager` 类：以 `node_mask` 为键的屏障映射、`popcount` 线程跟踪、挂起/唤醒、自动重置
  - ARM 系统调用 436 注册（32 位和 64 位 syscall table）
  - 参数验证：`mask=0` → `-EINVAL`、高 32 位 → `-EINVAL`、N=3 之外位 → `-EINVAL`
  - 7 个测试工作负载（4 正面 + 3 负面）+ `test_sync_wait.py` 驱动
  - R2 修复：添加参数验证、加强断言、自动编译替代预编译二进制
  - R3 修复：TC-T0-3 非调用者断言加强为 CPU 级别输出文件检查
- **测试结果：**
  - 断言数：**70**（TC-T0-1: 11、TC-T0-2: 12、TC-T0-3: 9、TC-T0-4: 20、TC-T0-5: 6、TC-T0-6: 6、TC-T0-7: 6）
  - 70/70 检查全部 PASS
  - 回归（TC1–TC5）：PASS，不受影响
- **与原计划差异：**
  - 计划外增加 3 个负面测试（由 validator 要求）
  - 返回值从 `void` 改为 `int`（需传播 `-EINVAL`）
  - 增加基于 trace 的全局排序机制
- **未完成/待办：**
  - 超时机制、序列化/检查点支持（未实现，不在范围内）
  - `MAX_NODE_COUNT` 硬编码为 3（P2，M9 可配置化）
  - 全系统 Linux 支持（明确排除）

### 3.3 M4 — 哨兵注册

- **阶段目标：** 在 HN 目录中使用原生 CHI `Cache_DirEntry` 格式安装、更新和移除 `EP_RNF` 合成条目，确保哨兵存在时本地 unique/read 请求触发 Snoop。
- **核心实现内容：**
  - `SentinelHelper` 类：基于 RTTI 的 `EP_RNF` MachineID 发现
  - Home 侧哨兵 API：`installSentinelForTest()` / `removeSentinelForTest()` / `inspectDirEntryForTest()`
  - `DirEntrySnapshot`：JSON 结构化目录检查，暴露 `sharerCount`、`epRnfInSharers`、`epRnfIsOwner`、`state` 字段
  - MESI 状态枚举：`G_I`、`G_S`、`G_E`、`G_M`、`G_BUSY`
  - Non-DSM 保护：所有哨兵操作上的 `isDsm()` 守卫
  - 三元 PASS/FAIL/SKIP 评分机制
  - FAIL 注入验证：证明 Python harness 门控正确区分真实失败和通过
- **测试结果：**
  - 断言数：**36**（M4SelfTest.cc 三元检查，典型拓扑 ~23–24 有效检查，0 FAIL）
  - 测试分组：地址分类（4）、Non-DSM 拒绝（2）、共享/owner/移除哨兵（7）、Snoop 计数器（2）、目录格式（2）、M4-4/5 就绪（6 SKIP）、Python 门控（2）
  - 回归：TC1–TC5 PASS、M4 自检 0 FAIL
- **与原计划差异：**
  - `OhNo_EP_RNF_NotGooOod.md` 未创建：EP_RNF 哨兵状态已使用 HN 原生格式成功表示
  - 三元 PASS/FAIL/SKIP 评分：将 M5 依赖的功能正确标记为 SKIP 而非 FAIL
  - FAIL 注入验证：证明 Python 门控正确性
- **未完成/待办：**
  - Grant 完成路径哨兵安装（推迟到 M5）
  - 端到端 Snoop 触发（推迟到 M5）
  - `sentinelVisibleTick ≤ grantVisibleTick` 时序断言（推迟到 M5）
  - `S_PENDING` 完整冲突阻塞（推迟到 M6）

### 3.4 M5 — 远程缺失与权限侧带

- **阶段目标：** 实现请求者侧远程 DSM 缺失闭环：HN 侧带权限信息 → EP_SNF 转换为外部请求 → UBCCController MESI grant 决策 → 请求者收到 GlobalGrant。
- **核心实现内容：**
  - **阶段 1 — 侧带管道：**
    - SLICC `CHI-msg.sm`：`CHIRequestMsg` 新增 `ubcc_needed_perm`（int）、`ubcc_write_intent`（bool）
    - `CHI-cache-funcs.sm`：`setUbccSideband()` 在 `Send_ReadNoSnp` 动作中填充侧带
    - EP_SNF 读取侧带，映射到外部请求类型（`GlobalReadShared` / `GlobalReadUnique`）
    - `Shared+true` 非法组合 → `fatal()` 守卫
  - **阶段 2 — MESI Grant 决策：**
    - 完整 MESI 5 状态转换机（`G_I`/`G_S`/`G_E`/`G_M`/`G_BUSY`）
    - `OuterGrantEnvelope` 含时序断言（`sentinelTick ≤ grantTick`）
    - 64 位 `sharersMask`、epoch 递增
    - 请求者侧 `RequesterLineEntry` 簿记（与哨兵分离）
- **测试结果：**
  - 断言数：**77**（69 PASS、0 FAIL、8 SKIP）
  - 测试分组：最小侧带（2）、MESI 收敛（18）、侧带检查（8）、请求者簿记（2）、Home 目录（3）、结构完整性（3）、ARM_SYNC 就绪（6 SKIP）、MESI 5 转换场景（30）、Grant Envelope（5）
  - 阶段 1 自检：0 FAIL；阶段 2 自检：0 FAIL
  - M4 回归（M5 内）：0 FAIL 回归
- **与原计划差异：**
  - `force_grant_m` 调试标志存在但非默认
  - `Shared+true` fatal 守卫需子进程隔离验证
  - ARM 工作负载端到端验证推迟（依赖 HN → EP_SNF 路由）
- **未完成/待办：**
  - ARM_SYNC TC-M5-1/2 工作负载测试（推迟）
  - 完整召回路径（推迟到 M6）
  - 多请求者冲突排队（推迟到 M6）

### 3.5 M6 — UBCC 目录 + EP_RNF 本地一致性访问

- **阶段目标：** 使 home UBCC 通过 `EP_RNF` 在本地 CHI 域上执行真正的一致性操作，完成 dirty recall/read 闭环，确保 home UBCC 严格仅元数据。
- **核心实现内容：**
  - `GlobalRecallOwner` 完整路径：home 检测 owner 冲突 → 路由召回给 owner → owner EP_RNF 触发 HN snoop → 返回数据 → home 完成 grant
  - EP_RNF 延迟 HN 响应：分配挂起响应上下文，HN 响应由外部事务完成门控
  - 目录 MESI 严格区分：`G_E`（dirty=false）≠ `G_M`（dirty=true）
  - `G_BUSY` 用于事务序列化，防止重叠冲突
  - 召回结果拆分：读取召回 → 旧 owner 降级共享；Unique/写入召回 → 旧 owner 失效
  - 修复轮关键改进：移除召回回退旁路、目标不匹配时 `fatal()`、召回路由加固
- **测试结果：**
  - 断言数：**52**（52 PASS、0 FAIL、0 SKIP）
  - 测试分组：目录一致性（4+）、仅元数据（1+）、召回路径（6+）、延迟响应（3+）、事务管理（6）、计数器（2）
  - M4/M5 回归（M6 内）：全部 0 FAIL
- **与原计划差异：**
  - 无召回回退旁路（P0 修复要求）
  - 召回通过 `UBCCController::getInstance()` 进程内路由（单 gem5 原型）
- **未完成/待办：**
  - 写回实现（推迟到 M7）
  - 干净驱逐（推迟到 M7）
  - Owner 转移（推迟到 M7）
  - 基于 Epoch 的过期过滤（推迟到 M7）
  - 多请求者冲突排队（部分，`G_BUSY` 已有，完整排队未实现）

### 3.6 M7 — 写回 / 驱逐 / Owner 转移

- **阶段目标：** 完成写回/驱逐/owner 转移循环以支持完整的三节点一致性。
- **核心实现内容：**
  - **写回路径：** owner 发送 `GlobalWriteback`（含数据+dirty 标志）→ home 更新元数据 → `GlobalAck` → owner 解除 dirty
  - **驱逐路径：** sharer/干净 owner 发送 `GlobalEvict` → home 更新 sharer mask → mask 空则条目移除
  - **Owner 转移：** 通过 epoch 序列化，强制执行单一 owner 不变量
  - **Epoch 过期过滤：** 过期响应（epoch < 当前）被拒绝；epoch=0 条目被移除
  - **召回结果拆分：** 读取召回 → owner 降级共享（保留在 mask）；写入召回 → owner 失效（从 mask 移除）
  - **修复轮关键加固：** 非 owner 写回拒绝、dirty owner 驱逐阻止、非 owner 驱逐拒绝、召回 PA 验证
- **测试结果：**
  - 断言数：**52**（52 PASS、0 FAIL、0 SKIP）
  - 测试分组：Dirty 写回（6）、干净驱逐（6）、单一全局 owner（6）、过期 epoch（8）、仅元数据（4）、召回拆分（10）、基础设施+计数器（12）
  - M4/M5/M6 回归（M7 内）：全部 0 FAIL
- **与原计划差异：** 无。完全与 `plan/03-phase-plan.md` 对齐。
- **未完成/待办：**
  - 多 sharer 共享路径加固（推迟到 M8）
  - GlobalInvalidate / 升级路径（推迟到 M8）
  - ARM_SYNC 端到端工作负载（推迟）

### 3.7 M8 — 共享读取加固与升级/失效闭环

- **阶段目标：** 将共享读取路径从"能工作"加固到"在所有边界情况下可验证正确"：多 sharer mask 维护、升级触发 `GlobalInvalidate`、ack 收集、默认启用共享路径。
- **核心实现内容：**
  - **`GlobalInvalidate` 流程：** home 检测 `G_S` + `GlobalReadUnique` → 向每个 sharer 发送 `GlobalInvalidate` → 收集 ack → 全部收齐后 grant
  - **Ack 收集机制：** `pendingInvalidations` 集合、幂等重新进入（重复 ack 无操作）、home epoch 关联
  - **SharerMask 完整生命周期：** grant 添加、驱逐移除、失效移除、降级转换时同步更新
  - **共享默认路径：** `GlobalReadShared` → `GlobalGrantShared`（默认），无 `force_grant_m` 回退
  - **修复轮关键加固：**
    - P0-1：用于失效的 home epoch（防止过期 ack 污染新事务）
    - P0-2：无操作重新进入返回（幂等失效 ack，防止重放死锁）
    - P1-4：`ackNode` 边界检查（入口处、所有提前返回之前）
    - 最终修复：ackNode 边界检查重新定位到所有提前返回之前的入口处
- **测试结果：**
  - 断言数：**61**（61 PASS、0 FAIL、0 SKIP）
  - 测试分组：两个请求者共享（9）、升级失效（24）、共享默认路径（7）、SharerMask 正确性（10）、Busy 行（3）、Ack 计数器（1）、挂起失效生命周期（4）、幂等 Ack（3）
  - M4/M5/M6/M7 回归（M8 内）：全部 0 FAIL
- **与原计划差异：** 无。完全与 `plan/03-phase-plan.md` 对齐。
- **未完成/待办：**
  - 多 sharer 的 ARM_SYNC 端到端工作负载（尚未）
  - 元数据模型（推迟到 M9）
  - 多 gem5 准备（推迟到 M9）

---

## 4. 代码修改总览

### 4.1 gem5 子模块（17 commits）

#### Sim 基础设施
| 文件 | 阶段 | 描述 |
|---|---|---|
| `src/sim/sync_wait.hh` | T0 | `SyncWaitManager` 类声明 |
| `src/sim/sync_wait.cc` | T0 | 屏障实现 + 参数验证 |
| `src/sim/system.hh` | T0 | `SyncWaitManager` 成员 |
| `src/sim/SConscript` | T0 | 构建集成 |

#### ARM SE
| 文件 | 阶段 | 描述 |
|---|---|---|
| `src/arch/arm/linux/se_workload.cc` | T0 | 系统调用 436 注册 + 高 32 位检查 |

#### CHI SLICC 协议
| 文件 | 阶段 | 描述 |
|---|---|---|
| `src/mem/ruby/protocol/chi/CHI-msg.sm` | M5 | 侧带字段 `ubcc_needed_perm` + `ubcc_write_intent` |
| `src/mem/ruby/protocol/chi/CHI-cache-funcs.sm` | M5 | `setUbccSideband()` 辅助函数 |
| `src/mem/ruby/protocol/chi/CHI-cache-actions.sm` | M5 | `Send_ReadNoSnp` 侧带注入 |

#### EP 控制器
| 文件 | 阶段 | 描述 |
|---|---|---|
| `src/mem/ruby/protocol/chi/ep/UBCCController.hh` | M4–M8 | MESI 目录管理、grant 决策、召回、写回、驱逐、失效、epoch |
| `src/mem/ruby/protocol/chi/ep/UBCCController.cc` | M4–M8 | 状态转换实现、召回/写回/驱逐/失效路径、SharerMask 管理 |
| `src/mem/ruby/protocol/chi/ep/EPBackend.hh` | M4–M8 | 外部事务接口、侧带/召回/写回/驱逐/失效处理、测试 API |
| `src/mem/ruby/protocol/chi/ep/EPBackend.cc` | M4–M8 | 外部事务编排、请求者上下文、召回/写回/驱逐/失效协调 |
| `src/mem/ruby/protocol/chi/ep/EPRNFController.hh` | M4、M6 | Snoop 计数器、本地一致性访问注入 |
| `src/mem/ruby/protocol/chi/ep/EPRNFController.cc` | M6 | 延迟 HN 响应处理 |
| `src/mem/ruby/protocol/chi/ep/EPSNFController.hh` | M5 | 侧带读取 + 外部请求映射 |
| `src/mem/ruby/protocol/chi/ep/EPSNFController.cc` | M5 | 侧带提取 + 请求路由 |
| `src/mem/ruby/protocol/chi/ep/SentinelHelper.hh` | M4 | MachineID 发现 + 目录操作 |
| `src/mem/ruby/protocol/chi/ep/SConscript` | M4 | 构建集成 |

#### 自检模块
| 文件 | 阶段 | 断言数 |
|---|---|---|
| `src/mem/ruby/protocol/chi/ep/M4SelfTest.cc` | M4 | 36 |
| `src/mem/ruby/protocol/chi/ep/M5SelfTest.cc` | M5 | 77 |
| `src/mem/ruby/protocol/chi/ep/M6SelfTest.cc` | M6 | 52 |
| `src/mem/ruby/protocol/chi/ep/M7SelfTest.cc` | M7 | 52 |
| `src/mem/ruby/protocol/chi/ep/M8SelfTest.cc` | M8 | 61 |

### 4.2 超项目（29 commits）

#### 测试基础设施
| 文件 | 阶段 | 类型 |
|---|---|---|
| `tests/sync_wait/tc_t0_1.c` ~ `tc_t0_7.c` | T0 | ARM_SYNC（C 工作负载 × 7） |
| `tests/sync_wait/test_sync_wait.py` | T0 | PY_INJECT（驱动脚本） |
| `tests/phase4/test_sentinel_registration.py` | M4 | PY_INJECT |
| `tests/phase5/test_sideband_plumbing.py` | M5 | PY_INJECT |
| `tests/phase5/test_remote_first_miss.py` | M5 | PY_INJECT |
| `tests/phase6/test_recall.py` | M6 | PY_INJECT |
| `tests/phase7/test_m7.py` | M7 | PY_INJECT |
| `tests/phase8/test_shared_hardening.py` | M8 | PY_INJECT |

#### 报告与文档
| 文件 | 阶段 | 描述 |
|---|---|---|
| `reports/M4_sentinel_registration_fixes_report.md` | M4 | 审查修复报告 |
| `reports/M4_sentinel_registration_fixes_report_R2.md` | M4 | R2 修复报告 |
| `reports/issue-closure-m4.md` | M4 | 问题关闭矩阵 |
| `reports/m4-fail-injection-proof.md` | M4 | FAIL 注入验证证明 |

#### 其他
| 文件 | 阶段 | 描述 |
|---|---|---|
| `readme.md` | M3.5 | 测试行添加/清理 |
| `.gitignore` | T0、M5 | 排除测试二进制 + 临时文件 |

### 4.3 修改文件统计

| 类别 | 文件数 | 涉及阶段 |
|---|---|---|
| gem5 Sim 基础设施 | 3 | T0 |
| gem5 ARM SE | 1 | T0 |
| gem5 CHI SLICC | 3 | M5 |
| gem5 EP 控制器 | 11 | M4–M8 |
| gem5 自检模块 | 5 | M4–M8 |
| 超项目 测试脚本 | 8 | T0、M4–M8 |
| 超项目 C 工作负载 | 7 | T0 |
| 超项目 报告文档 | 4 | M4 |
| 超项目 其他 | 2 | M3.5、T0 |
| **总计** | **44** | — |

---

## 5. 测试总览

### 5.1 各阶段测试类型分布

| 阶段 | C++ Self-Test | PY_INJECT | ARM_SYNC | ORCH_FLOW | 合计 |
|---|---|---|---|---|---|
| M3.5 | 0 | 0 | 0 | 1 | 1 |
| T0 | 0 | 0 | 70 | 0 | 70 |
| M4 | 36 | 0 | 0 | 0 | 36 |
| M5 | 77 | 0 | 0 | 0 | 77 |
| M6 | 52 | 0 | 0 | 0 | 52 |
| M7 | 52 | 0 | 0 | 0 | 52 |
| M8 | 61 | 0 | 0 | 0 | 61 |
| **合计** | **278** | **0** | **70** | **1** | **349** |

> 注：M4–M8 的 PY_INJECT 栏为 0 是因为断言计数来自 C++ 内嵌自检（MxSelfTest.cc），Python harness 负责启动仿真 + 捕获 stdout + 门控决策，但不产生独立的断言计数。M4 的 Python harness 自身含 2 个门控检查（FAIL 注入 + 干净 PASS），已计入 M4SelfTest.cc 的 36 个检查中。

### 5.2 累积回归结果

| 基线测试 | 类型 | M3.5 | T0 | M4 | M5 | M6 | M7 | M8 |
|---|---|---|---|---|---|---|---|---|
| TC1 `test_pa_layout_mode.py` | PY_INJECT | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| TC2 `run_phase1_test.py` | PY_INJECT | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| TC2E `run_phase1_test_enhanced.py` | PY_INJECT | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| TC3 `verify_topo_objects.py` | PY_INJECT | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| TC4 `test_ruby_create_system_n3l2d2.py` | PY_INJECT | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| TC5 `test_ep_instantiate.py` | PY_INJECT | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| M4 自检回归 | — | — | — | — | 0 FAIL | 0 FAIL | 0 FAIL | 0 FAIL |
| M5 自检回归 | — | — | — | — | — | 0 FAIL | 0 FAIL | 0 FAIL |
| M6 自检回归 | — | — | — | — | — | — | 0 FAIL | 0 FAIL |
| M7 自检回归 | — | — | — | — | — | — | — | 0 FAIL |

**结论：零回归。** 全部 6 个基线测试在所有阶段保持 PASS。M4–M8 自检内部累积回归检测全部为 0 FAIL（后续阶段在 `EPBackend::init()` 中串行执行所有先前阶段的 `MxSelfTest`，任何 FAIL 都会阻止当前阶段的门控通过）。

### 5.3 测试目录布局

```
tests/
├── sync_wait/           # T0: ARM_SYNC 工作负载
│   ├── tc_t0_1.c .. 7.c
│   └── test_sync_wait.py
├── phase1/              # 基线测试（M0–M3 遗留）
│   ├── test_pa_layout_mode.py
│   ├── run_phase1_test.py
│   ├── run_phase1_test_enhanced.py
│   └── test_phase1.py
├── phase2/              # 基线测试
│   ├── verify_topo_objects.py
│   └── test_ruby_create_system_n3l2d2.py
├── phase3/              # 基线测试
│   └── test_ep_instantiate.py
├── phase4/              # M4: 哨兵注册
│   └── test_sentinel_registration.py
├── phase5/              # M5: 侧带 + 首次缺失
│   ├── test_sideband_plumbing.py
│   └── test_remote_first_miss.py
├── phase6/              # M6: 召回
│   └── test_recall.py
├── phase7/              # M7: 写回/驱逐/转移
│   └── test_m7.py
└── phase8/              # M8: 共享加固
    └── test_shared_hardening.py
```

---

## 6. 已知限制与待办

### 6.1 跨阶段未完成项

| 事项 | 涉及阶段 | 当前状态 | 优先级 | 备注 |
|---|---|---|---|---|
| ARM_SYNC 端到端工作负载 | T0/M5/M6/M7/M8 | 推迟 | P1 | 结构验证通过 C++ 自检完成；端到端 ARM 工作负载需真实 CHI 协议路径下的 HN → EP_SNF 路由 |
| 多请求者冲突排队 | M6 | 部分实现 | P1 | `G_BUSY` 可防止重叠事务，但不排队竞争请求；冲突请求者需重试 |
| 多 gem5 准备 | M8 | 未实现 | P3 | 当前单 gem5 原型使用进程内方法调用；多 gem5 部署需外部网络路由 |
| `MAX_NODE_COUNT` 可配置化 | T0 | 硬编码为 3 | P2 | 需从拓扑派生 |
| 超时 / 重传机制 | T0/M6/M8 | 未实现 | P3 | 对确定性测试可接受；真实硬件部署需要 |
| SharerMask 64 位扩展 | M5/M8 | N=3 足够 | P3 | 极大节点数需扩展 |

### 6.2 后续阶段（M9）计划

根据 `plan/03-phase-plan.md` §1：

| M9 目标 | 描述 |
|---|---|
| 元数据模型 | 容量模型、外部协议 ABI 抽象 |
| 多 gem5 准备 | 多实例部署假设 |
| ARM_SYNC 端到端工作负载 | 在真实 CHI 协议路径下验证 M5–M8 所有功能 |
| 清理与加固 | `MAX_NODE_COUNT` 可配置化、SharerMask 扩展、失效广播优化 |

---

## 7. 附录

### 7.1 各阶段 Commit Hash 汇总

#### gem5 子模块

| 阶段 | Commit Hash | 描述 |
|---|---|---|
| M3.5 | （无变更） | — |
| T0 | `95e3e2763f` | T0：SyncWaitManager 屏障 |
| T0 | `9d714c6ea2` | T0 修复：参数验证 |
| M4 | `97220b31eb` | M4 R2：三元评分 |
| M4 | `d013f0a3a8` | M4 R3：修复硬编码 PASS |
| M4 | `79f5fa74dd` | M4 R4：最终门控修复 |
| M4 | `eb58a922a1` | M4 Final：fflush(stdout) |
| M5 | `423355ecbd` | M5 阶段 1：SLICC 侧带 |
| M5 | `5b66adc3a9` | M5 阶段 1 P0：assert→fatal |
| M5 | `31ef2e1233` | M5 阶段 1：M5SelfTest + 门控 |
| M5 | `9b94dc22dd` | M5 阶段 2 修复：tick、MESI、sharersMask |
| M5 | `b9d418a5ba` | M5 阶段 2：P0+P1 修复 |
| M6 | `607a8f0e0e` | M6 P0：移除召回回退旁路 |
| M6 | `899ead12f7` | M6 修复轮：召回路由、事务、测试 |
| M7 | `b41fe6012c` | M7 修复轮：P0+P1 |
| M8 | `4a9a672335` | M8 修复轮：P0-1/P0-2/P1-4 |
| M8 | `ad782435d6` | M8：ackNode 边界检查重新定位 |
| M8 | `d1f6ec4947` | M8 最终：边界检查移至目录查找之前 |

#### 超项目

| 阶段 | Commit Hash | 描述 |
|---|---|---|
| M3.5 | `3497b74` | M0：README 测试 |
| T0 | `632d25a` | T0：Sync_Wait 测试基础设施 |
| T0 | `97dc12e` | T0 修复轮：阶段报告 |
| T0 | `aedd906` | T0 修复轮：参数验证 |
| T0 | `42589ad` | T0 R2：二进制清理、基于 trace 排序 |
| T0 | `55dac63` | T0 R3：TC-T0-3 断言加强 |
| T0 | `b3dff28` | T0 R3 报告 |
| M4 | `865fc77` | M4 R2：更新 gem5 子模块 |
| M4 | `e7f9cbe` | M4 R2：测试 harness 更新 |
| M4 | `9040fd9` | M4 R3：更新 gem5 + 单元测试 |
| M4 | `4fc2d53` | M4 R4：最终门控修复 |
| M4 | `284f32f` | M4 Final：证据关闭 |
| M4 | `6da4531` | M4 最终：关闭矩阵 |
| M4 | `60e5614` | Plan/Docs 文档 |
| M4 | `f331a06` | M4 Final：文档对齐 |
| M5 | `1c5488f` | M5 阶段 1 P0：更新 gem5 |
| M5 | `902c4e1` | M5 阶段 1：更新 gem5 |
| M5 | `0f0a892` | M5 阶段 1：添加测试 harness |
| M5 | `805f5fd` | M5 阶段 1：修复 fd 捕获 |
| M5 | `2b034db` | M5 阶段 1：修复 fd 捕获顺序 |
| M5 | `fd4c410` | M5 阶段 1：gitignore + 参数检查 |
| M5 | `4bf0419` | M5 阶段 2 修复：添加测试 |
| M5 | `0a61c2d` | M5：修复 requesterNode=-1 |
| M5 | `934c239` | M5 阶段 2：更新 gem5 |
| M6 | `99cb400` | M6 修复轮：更新 gem5 |
| M7 | `7e5a1d4` | M7 修复轮：更新 gem5 |
| M8 | `16c1780` | M8 修复轮：P0-1/P0-2/P1-4 |
| M8 | `1ae8c4a` | M8：更新 gem5（ackNode） |
| M8 | `6e966e6` | M8 最终：更新 gem5 |

### 7.2 关键术语表

| 术语 | 全称 / 说明 |
|---|---|
| UBCC | Unified Bus Cache Coherence |
| EP | External Proxy（外部代理） |
| EP_RNF | 外部代理的 RNF（请求者节点功能） |
| EP_SNF | 外部代理的 SNF（从属节点功能） |
| HN | Home Node（目录节点） |
| CHI | Coherent Hub Interface（ARM 一致性互连接口） |
| SLICC | Specifying and Implementing Coherence Controllers（gem5 一致性协议 DSL） |
| MESI | Modified / Exclusive / Shared / Invalid（缓存一致性状态） |
| DSM | Distributed Shared Memory（分布式共享内存） |
| Oracle | 编排器（orchestrator agent） |
| Implementer | 实现器 agent |
| Validator | 验证器 agent |
| ARM_SYNC | ARM 汇编同步测试用例（多节点时序控制） |
| PY_INJECT | Python 注入式测试（启动 gem5 仿真 + 捕获 C++ 自检输出） |
| ORCH_FLOW | 编排器流程测试（验证 agent 协作链路） |
| Shall/Should | 强制要求 / 建议要求（来自 External Proxy Spec） |
| G_I / G_S / G_E / G_M / G_BUSY | Home UBCC 全局 MESI 目录状态（Invalid / Shared / Exclusive / Modified / Busy） |
| SharerMask | 64 位 Sharer 位掩码（每 bit 对应一个节点） |
| Epoch | 每行单调计数器，用于过期响应过滤 |
| Sentinel | 在 HN 目录中代表 EP_RNF 的合成条目 |
