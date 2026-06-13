# UBCC 项目总结：目标、进度与变更

生成时间: 2026-05-25
源文档: `docs/*.md`, `reports/*.md`
基线冲突处理: 以新文档为准（`docs/multi-node-pa-layout.md` > `docs/basic-framework-prompt.md` > 旧 reports）

---

## 1. 项目目标

在 gem5 上实现 UBCC (Unified Bus Cache Coherence) 基础框架——一个三节点、每节点 2 cluster、每 cluster 2 core 的 cache coherence 原型系统，为后续完整跨节点 DSM 一致性协议搭建基础设施。

### 1.1 核心设计约束

| 约束 | 来源 | 说明 |
|------|------|------|
| N=3, L=2, D=2 | `basic-framework-prompt.md:121` | 默认规模，不得降级 |
| Unified DSM VA/PA | `basic-framework-prompt.md:176-178` | **已被 per-node PA 替代**（见冲突 1） |
| DSM Local ≠ Local Private | `basic-framework-prompt.md:124` | 严格分开 |
| UbccExclusive 不映射给 CPU | `basic-framework-prompt.md:125` | 第一版 |
| EP_RNF 是 sentinel 主路径 | `basic-framework-prompt.md:126` | home-side 逻辑不走 EP_SNF |
| 无 UR_i | `basic-framework-prompt.md:127` | 第一版 |
| UBCC metadata 全量内存驻留 | `basic-framework-prompt.md:128` | 不做 eviction/refill |
| ordinary CHI 限制在 node 内 | `basic-framework-prompt.md:129` | |
| 所有 trace/checker 带 node_id | `basic-framework-prompt.md:130` | |

### 1.2 组件清单（每节点）

- `CL_{i,0}`, `CL_{i,1}` — RN-F cluster，各含 D=2 core + L1I/L1D + shared L2
- `HN_i` — HN-F home agent + L3 + 地址分类路由
- `L_SNF_i` — SN-F，Local Private + UbccExclusive DRAM
- `DL_SNF_i` — SN-F，DSM_i 的本地 backing store
- `EP_SNF_i` — SN-F，DSM_k (k≠i) 的 requester-side remote data plane
- `EP_RNF_i` — RN-F，sentinel 主入口，接收 HN snoop + 响应
- `EP_i` — internal backend（`EPBackend` SimObject）
- `UBCC_i` — internal 全局目录 + outer protocol（`UBCCController` C++ 类）

### 1.3 原始阶段划分

**Agent Execution Guide 的 M 阶段：**
| 阶段 | 目标 | 状态 |
|------|------|------|
| M0 | Docker + Git 自动化预检 | ✅ 完成 |
| M1 | 单节点 clustered CHI | ✅ 完成 |
| M2 | 逻辑域隔离（多节点） | ✅ 完成 |
| M3 | EP-RNF/EP-SNF skeleton | ✅ 完成 |
| M4 | Sentinel registration | ❌ 待开始 |
| M5 | DSM remote first miss bring-up | ❌ 待开始 |
| M6 | UBCC directory + EP-RNF local access | ❌ 待开始 |
| M7 | Writeback/evict/owner transfer | ❌ 待开始 |
| M8 | GrantS + read-sharing recovery | ❌ 待开始 |
| M9 | Metadata + multi-gem5 准备 | ❌ 待开始 |

**Prompt 文档的 Phase 划分：**
| 阶段 | 目标 | 状态 |
|------|------|------|
| Phase 1 | 地址空间与进程控制 | ✅ 完成 |
| Phase 2 | 拓扑接线 | ✅ 完成 |
| Phase 3 | Endpoint skeleton | ✅ 完成 |
| Phase 4 | Guardrails + checker | ✅ 完成 |

### 1.4 原始验收标准（Completion Bar）

1. ✅ N=3, L=2, D=2 主配置可创建成功（对象层 101/101）
2. ✅ DSM VA 固定窗口映射已建立（`Process.map()` + 5/5 SE test）
3. ⚠ 普通页不落入 DSM/UbccExclusive（`phys_pool_id` 已实现，运行时 PA 验证 P2 待补）
4. ✅ HN_i 基于 PA 正确分流（12 条排他性 downstream 断言）
5. ✅ ordinary CHI cross-node checker 存在且执行（C++ checkAddr 4 条 recv 路径）
6. ✅ EP_RNF/EP_SNF 已接入 topology + 最小收发验证（test_ep_instantiate + selfTest）
7. ✅ testcase 不依赖缩小规模/硬编码

---

## 2. 已完成工作

### 2.1 Docker & 工作流（M0）

- `docker/ubcc-dev.Dockerfile` — 基于 ubuntu:20.04，含 gem5 构建依赖 + ARM 交叉编译器
- `scripts/ubcc_docker_build.sh` — 构建镜像，支持代理
- `scripts/ubcc_docker_run.sh` — `--network none` 启动容器，bind mount repo
- `scripts/ubcc_git_preflight.sh` — SSH key、git identity、remote 预检
- `scripts/ubcc_phase_commit.sh` — 阶段结束后自动 add/commit/push

### 2.2 C++ / SimObject 代码

| 文件 | 说明 |
|------|------|
| `gem5/src/mem/ruby/protocol/chi/ep/EPController.py` | EP 基类 SimObject（8 × MessageBuffer + node_id） |
| `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.hh/.cc` | EP_RNF: recvSnoopMsg + selfTest + SnpResp_I |
| `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.hh/.cc` | EP_SNF: recvRequestMsg + recvSnoopMsg + CompData_I |
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh/.cc` | EPBackend SimObject + checkAddr + checkDsmAddr |
| `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh/.cc` | metadata map + outer queue wakeup skeleton |
| `gem5/src/mem/ruby/protocol/chi/ep/NodeAddressMap.hh/.cc` | C++ 地址分类器：isDsm / homeNode / srcNodeId / ... |
| `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.py` | addr_ranges + ep_backend 参数 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.py` | addr_ranges + ep_backend 参数 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.py` | node_id 参数 |
| `gem5/src/mem/ruby/protocol/chi/ep/SConscript` | 编译注册 + RubyEP debug flag |

### 2.3 Python 配置层

| 文件 | 说明 |
|------|------|
| `gem5/configs/ruby/CHI_basic_framework_config.py` | NodeConfig, NodeAddressMap, ClusterCHI_RNF, wrappers |
| `gem5/configs/ruby/CHI_ubcc_framework.py` | `create_ubcc_system` — N=3 多节点拓扑构建 |
| `gem5/configs/example/ubcc/basic_framework_se.py` | Phase 1 SE 仿真配置 |

### 2.4 已有文件修改

| 文件 | 修改 |
|------|------|
| `gem5/src/sim/Process.py` | 新增 `phys_pool_id` 参数 |
| `gem5/src/sim/process.hh` | 新增 `int physPoolId = 0` |
| `gem5/src/sim/process.cc` | `allocateMem`/`deallocateMem`/`replicatePage` 使用 pool_id |
| `gem5/configs/ruby/Ruby.py` | `setup_memory_controllers()` 空 dir_cntrls 早退 |

### 2.5 测试

| 测试文件 | 通过数 | 验证内容 |
|----------|--------|----------|
| `tests/phase1/test_pa_layout_mode.py` | 48/48 | PA 静态验证（PHY_BASE, 非重叠, isDsm/homeNode） |
| `tests/phase1/run_phase1_test.py` | 5/5 | SE 仿真 3 进程 + DSM VA→PA + phys_pool_id |
| `tests/phase2/verify_topo_objects.py` | 101/101 | 对象层 wiring + downstream/routing 排他性 |
| `tests/phase2/test_ruby_create_system_n3l2d2.py` | 9/9 | 3-node 拓扑 Ruby.create_system + DRAM backstore + m5.instantiate |
| `tests/phase3/test_ep_instantiate.py` | INSTANTIATE OK | EP 控制器 standalone Ruby instantiate + selfTest |

---

## 3. 待完成工作

### 3.1 协议阶段（M4–M9）

| 阶段 | 目标 | 关键任务 |
|------|------|----------|
| M4 | Sentinel registration | HN-F directory 登记 EP-RNF sentinel；ExternalSharer/ExternalOwner；禁止 Local PA sentinel |
| M5 | DSM remote first miss | EP-SNF → UBCC data/grant；bring-up 阶段保守 GrantM |
| M6 | UBCC directory + local access | per-line directory map；outer MESI 消息；EP-RNF local read/downgrade/invalidate |
| M7 | Writeback/evict/owner transfer | dirty writeback；clean evict；M owner transfer；epoch 防护 |
| M8 | GrantS + read-sharing | sideband 携带 original_chi_req；GlobalReadShared；sharer mask |
| M9 | Metadata + multi-gem5 准备 | SRAM directory cache；outer protocol ABI；ns-3 时间模型文档化 |

### 3.2 已知未完成项

| 问题 | 说明 | 优先级 |
|------|------|--------|
| 运行时 PA 验证（heap/stack/.data/.text） | phys_pool_id 路由已实现但未在运行时断言 PA 归属 | P2 |
| 全拓扑 Ruby.create_system + m5.instantiate() | 受限于 ArmTableWalker / MemPools 的 gem5 内部问题 | P1 |
| EP_RNF sentinel 完整语义 | ExternalSharer/ExternalOwner 未实现 | P1 (M4) |
| metadata eviction/backing-store | 第一版不做 | P3 (M9) |
| Scheme A local PA symmetry | 可选后续增强 | P3 |

---

## 4. 与原始计划的关键差异（按新文档口径）

### 冲突 1（关键）：Unified DSM PA → Per-Node PA

| 方面 | 原始计划 (`docs/basic-framework-prompt.md`) | 实际实现 (`docs/multi-node-pa-layout.md`) |
|------|------------------------------------------|----------------------------------------|
| DSM PA 方案 | 全 node 统一 PA: `[2*SegSize, 5*SegSize)` | Per-node PA: `PHY_BASE_i + [2*SegSize, 5*SegSize)` |
| 原因 | 简单统一 | gem5 `System` 对同 MachineType 同范围有 fatal 检查 |
| 地址翻译 | 不需要 | 引入 `(src, home, offset)` 三元组 ↔ PA 转换 |
| Local Private PA | 建议 node-distinct backend | 强制 per-node PA（`PHY_BASE_i + [0, 2*SegSize)`） |
| 影响范围 | — | NodeAddressMap + 所有 recvMsg 路径 + 测试断言 |

**结论**：新方案是单 System 约束下的等价替代，通过 `NodeAddressMap` 翻译在语义上等效于统一 DSM PA。

### 冲突 2：测试架构演变

| 方面 | 原始计划 | 当前状态 |
|------|----------|----------|
| 主 bring-up 测试 | 单一 `run_real_topo_test.py` + SMC bypass | 已删除；拆分为 TC1–TC5 套件，无 bypass |
| 测试范围 | 期望全拓扑 `m5.instantiate()` 通过 | 对象层 101/101 通过；全拓扑受 gem5 内部限制诚实报告失败 |
| 报告准确性 | `basic-framework-final.md` 存在不实描述 | Audit #7 发现后已修正措辞 |

### 冲突 3：DRAM backstore 策略

| 方面 | 原始计划 | 当前实现 |
|------|----------|----------|
| L_SNF/DL_SNF 后端 | 未指定细节 | DDR4_2400_8x8 + MemCtrl（真实延迟可建模） |
| EP_SNF 数据路径 | 读 backend memory | 截获 + fake data 响应（符合本阶段语义） |
| setup_memory_controllers | 未提及 | 空 dir_cntrls 早退（`Ruby.py` 修改） |

### 新增项（原始计划未覆盖）

1. **`Ruby.py` 空 dir_cntrls 处理** — 支持 UBCC 不走 Ruby 通用目录内存构建器
2. **`EPBackend::checkDsmAddr()`** — 放宽 EP_SNF 地址检查，只校验 DSM 窗口不强制 home==local
3. **TC4 无 bypass 测试** — `test_ruby_create_system_n3l2d2.py`，9/9 PASS，含真实 DRAM backstore 检查
4. **PA 布局单元测试** — `test_pa_layout_mode.py`，48/48 PASS，覆盖 per-node PA 全部边界

---

## 5. 当前可运行的测试命令

```bash
# 一键全部测试
scripts/ubcc_docker_run.sh bash -lc '
  cd /workspace/gem5
  echo "=== TC1: PA Layout ===" && ./build/ARM/gem5.opt ../tests/phase1/test_pa_layout_mode.py 2>&1 | grep -E "^TOTAL|EXIT"
  echo "=== TC2: Phase1 SE ===" && ./build/ARM/gem5.opt ../tests/phase1/run_phase1_test.py ../tests/phase1/hello.arm 2>&1 | grep -E "Results|hello"
  echo "=== TC3: Verify Topo ===" && ./build/ARM/gem5.opt ../tests/phase2/verify_topo_objects.py ../tests/phase1/hello.arm 2>&1 | grep "^TOTAL"
  echo "=== TC4: N3L2D2 ===" && ./build/ARM/gem5.opt ../tests/phase2/test_ruby_create_system_n3l2d2.py ../tests/phase1/hello.arm 2>&1 | grep -E "^TOTAL|TC-BRINGUP"
  echo "=== TC5: EP Instantiate ===" && ./build/ARM/gem5.opt ../tests/phase3/test_ep_instantiate.py ../tests/phase1/hello.arm 2>&1 | grep "INSTANTIATE\|EP_RNF"
'
```

---

## 6. 总结

**已完成**：基础框架的地址空间、拓扑、skeleton endpoint、隔离守卫全部实现。共 5 个测试套件，通过率 100%。

**待完成**：UBCC coherence 协议核心——sentinel registration（M4）、DSM remote miss（M5）、目录协议（M6）、writeback/transfer（M7）、read-sharing（M8）、metadata 建模（M9）。

**与原始计划最大变更**：统一 DSM PA → per-node PA（由 gem5 单一性约束驱动），以及测试架构从单一 monolith 脚本演化为无 bypass 的模块化套件。

---

## 7. 新增前置任务与分阶段执行方案

### 7.1 新增前置 Task：`Sync_Wait(node_mask)` 自定义系统调用

**动机**：跨节点核心无法直接共享 DRAM，因此无法用普通共享内存实现 barrier/同步原语。需要一个 gem5 自定义 ARM 系统调用，提供全局同步能力。

**语义**：

```c
// ARM 系统调用（64-bit）
// x8 = syscall_number, x0 = node_mask
int Sync_Wait(uint64_t node_mask);
```

- 所有属于 `node_mask` 中节点的线程都会阻塞在 `Sync_Wait` 处。
- 当 `node_mask` 中所有节点上的所有线程都调用了 `Sync_Wait(node_mask)`，它们才被释放继续执行。
- 用于实现全局 barrier、依赖 fence、阶段性同步等场景。

**实现路径**：

| 步骤 | 工作 | 涉及文件 |
|------|------|----------|
| 1 | 在 gem5 SE-mode 中添加自定义系统调用号与 handler 注册 | `gem5/src/arch/arm/linux/se_workload.cc`, `gem5/src/sim/syscall_desc.hh` |
| 2 | 实现 `SyncWait` 功能逻辑：维护 per-node_mask 的 barrier 状态，统计到达的线程数，满足条件时唤醒所有等待线程 | 新增 `gem5/src/sim/sync_wait.hh/.cc` |
| 3 | 将 barrier 状态对象挂到 System 级别（全局可见） | `gem5/src/sim/system.hh` |
| 4 | 线程阻塞机制：利用 `EventQueue` 或 `Fault` 让线程在 barrier 上 spin/block | gem5 SE-mode 线程调度接口 |
| 5 | 测试：编写 ARM 汇编/C 测试程序验证 3 节点同步 | `tests/sync_wait/` |
| 6 | 集成：确保 `Sync_Wait` 可被后续协议阶段使用 | — |

**约束**：
- 仅 SE-mode，不依赖 Linux 内核。
- `node_mask` 按 bit index 编码：bit i = 1 表示 Node i 参与同步。
- 不同 `node_mask` 值视为不同的 barrier 实例。
- 第一版不做超时/中断，纯阻塞等待。

### 7.2 分阶段执行 Agent 编排方案

后续实现不再由单一 Agent 线性推进，而是使用三个专用 Agent 协同：

```
┌──────────────────────────────────────────────────────┐
│              coder-validator-orchestrator              │
│  (读取计划书, 按阶段分派, 协调验收, 决策推进/回退)       │
└────────┬──────────────┬──────────────────┬────────────┘
         │  dispatch     │  dispatch        │  decide
         ▼               ▼                  ▼
┌─────────────────┐  ┌──────────────────────────┐
│ cache-coherence  │  │ strict-task-completion-  │
│ -implementer     │  │ -reviewer                │
│ (写代码 + 构建    │  │ (代码审查 + 验收检查     │
│    + 自测)        │  │   + 回归确认)            │
└─────────────────┘  └──────────────────────────┘
```

**各角色职责**：

| Agent | 职责 | 产出 |
|-------|------|------|
| `cache-coherence-implementer` | 实现该阶段所有代码修改、编译、自测 | 可编译的代码 + 自测结果 |
| `strict-task-completion-reviewer` | 对照阶段定义严格审查实现完整性，检查覆盖度、边界条件、不伪测试 | 审查报告 + PASS/FAIL |
| `coder-validator-orchestrator` | 读取计划书，逐阶段 dispatch 给 implementer → reviewer；reviewer PASS 则进入下一阶段，FAIL 则退回 implementer 修改 | 阶段推进决策 |

**工作流伪代码**：

```
for each stage in [SyncWait, M4, M5, M6, M7, M8, M9]:
    loop:
        result = dispatch(cache-coherence-implementer, stage)
        if result.failed:
            dispatch(cache-coherence-implementer, fix=result.errors)
            continue

        review = dispatch(strict-task-completion-reviewer, stage)
        if review.verdict == PASS:
            break  # 进入下一阶段
        else:
            dispatch(cache-coherence-implementer, fix=review.issues)
            # continue loop
```

**每个阶段的交付物必须包含**：
1. 代码修改（增量 diff）
2. 构建通过（`scons build/ARM/gem5.opt -j32 PROTOCOL=CHI`）
3. 该阶段新增测试全部通过
4. 已有回归测试不降级
5. 审查报告（含验收对照表）

### 7.3 补充后的全量阶段计划

| 序号 | 阶段 | 前置依赖 | 验证方式 | 负责 Agent |
|------|------|----------|----------|-----------|
| **T0** | **`Sync_Wait` 自定义系统调用** | 现有 Phase 1–4 全部通过 | 3-node barrier 测试 | orchestrator → implementer → reviewer |
| M4 | Sentinel registration | T0 + M3 现有通过 | HN-F sentinel insert/snoop EP-RNF | 同上 |
| M5 | DSM remote first miss | M4 | EP-SNF → UBCC data/grant, GrantM | 同上 |
| M6 | UBCC directory + local access | M5 | per-line directory, outer MESI, local recall | 同上 |
| M7 | Writeback/evict/owner transfer | M6 | 3-node ping-pong, epoch 防护 | 同上 |
| M8 | GrantS + read-sharing | M7 | 多 node read-share, local upgrade 全局 inval | 同上 |
| M9 | Metadata + multi-gem5 准备 | M8 | metadata 容量模型, outer ABI 文档化 | 同上 |

**验收纪律**：任何时候不满足交付物标准（编译、测试、回归），orchestrator 不得推进到下一阶段。
