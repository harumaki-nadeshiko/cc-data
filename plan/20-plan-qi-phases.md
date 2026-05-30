# UBCC 阶段计划 — Qi 阶段

> 生成日期: 2026-05-30
>
> 基准基线: M3.5 ~ M8 全部 PASS（349 项测试断言，46 个 commit）
>
> 基于: `plan/15-plan-revision.md` 用户审核意见与讨论结论

---

## 1. 概述

### 1.1 阶段路线图

| 阶段 | 代号 | 名称 | 目标 | 依赖 | 优先级 |
|---|---|---|---|---|---|
| Q1 | Q1 | 基础设施修复与验证 | 修复 EP_SNF→HN response 回环、DSM VA Mapping for Ruby/CHI、DSM range 进 HN-F | 无（但从 M8 基线出发） | 🔴 P0（前提条件） |
| Q2 | Q2 | 端到端应用层测试 | 10 个 ARM workload testcase，验证协议在真实 CPU load/store 路径下正确 | Q1 完成 | 🔴 P0（最高优先级） |
| Q3 | Q3 | UR RN-F + UbccExclusive 区域 | 实现独立 UR RN-F controller，UbccExclusive 走 HN-F L3 一致性路径 | Q2 必达集（TC1~TC6）完成 | 🟡 P1 |
| Q4 | Q4 | 外部模块化架构设计 | 定义 ExternalMsgBus 抽象层、消息类型、实现路线图，与 M9 合并 | Q2 必达集（TC1~TC6）完成（可部分并行） | 🔴 P0（最后做） |

### 1.2 与 M3.5~M9 的关系

原阶段 M3.5~M8 全部 PASS，不变。Qi 阶段在 **M8 之后** 执行：

```
M3.5 → T0 → M4 → M5 → M6 → M7 → M8  (全部 PASS)
                                        │
                    ┌───────────────────┴───────────────────┐
                    ▼                                       ▼
              Q1 (基础设施修复)                      Q4 (外部架构设计)
                    │                               最后合并后一起做
                    ▼
              Q2 (端到端测试)
                    │
                    ▼
              Q3 (UR RN-F + UbccExclusive)
                    │
                    ▼
              Q4 (与 Q2 后残留合并) ≈ 原 M9
```

Q4 与原 M9 的关系：**EXT ≈ M9，两者合并**。Q4 以用户新要求为准：
- 外部模块化架构设计替代原 M9 中的 outer protocol ABI 抽象
- 原 M9 中的 multi-gem5 准备、metadata 容量模型被 Q4 吸收
- Q4 产出 direct outer protocol ABI 定义

### 1.3 设计原则

以下原则贯穿全部 4 个 Qi 阶段，源自用户讨论结论：

1. **EP_SNF 职责净化**：
   - EP_SNF 仅处理 eviction/flushback（→ UBCC）+ HN-F miss 拉数据
   - **不**处理 recall/invalidate 等主动干涉（那是 EP_RNF 的职责）

2. **EP_RNF 职责：sentinel + 外部干涉透传**：
   - 响应 HN snoop（作为 directory 参与者）
   - 透传 home UBCC 的 recall/invalidate 给本地 CHI domain
   - **不**承担本地 coherent 访问职责（那是 UR RN-F 的职责）

3. **UR RN-F：UBCC 本地 coherent load/store 入口**：
   - 独立 controller，类似 `ClusterCHI_RNF` 但专用于 UBCC
   - 地址范围限定为 `UbccExclusive`：`[PhyBase + 1*SEG, PhyBase + 2*SEG)`
   - 对 UbccExclusive 区域执行真实 CHI coherent 操作

4. **DSM 进 L3**：
   - HN-F 的 `addr_ranges` 需包含 DSM range
   - L3 对 DSM 行做缓存
   - 一致性通过 EP_RNF recall/invalidate 维护

5. **"普通 RN-F 能读到 L3 某行 → 该行未过期"**：
   - 核心断言：当普通 CPU cluster 的 RNF 从 HN-F L3 读到一个 DSM 行时，该行数据必须是有效的（未被 home UBCC 标记为 stale）
   - 如果 home UBCC 已将该行 owner 转移，必须通过 EP_RNF 先 invalidate L3 中的副本

6. **UbccExclusive 区域采用方案 B — 独立 UR RN-F**：
   - 不使用 EP_RNF 复用（方案 C）
   - 不使用 UBCC 直连 membus（方案 A）
   - 目的：职责分离清晰，真实 CHI coherent 路径验证

---

## 2. 阶段 Q1: 基础设施修复与验证

> 优先级: 🔴 P0 | 执行顺序: 第一 | 依赖: M8 基线

### 2.1 目标

在进入 Q2 端到端测试之前，修复并验证三个关键基础设施问题，确保 Ruby/CHI 配置下有完整的端到端数据通路。

### 2.2 检查清单

#### 2.2.1 EP_SNF → HN CHI response 回环验证

##### 当前状态分析

**已有 CHI response 通路**：EP_SNF 在 `ReadNoSnp` 事务完成后，通过 CHI SLICC 协议模板的 `RespSepData + CompData` 路径向 HN-F 发送 CHI response。这是 CHI SNF controller 的标准响应机制。

**关键遗留问题：payload 未接入真实数据**：
```
HN-F 发送 ReadNoSnp → EP_SNF 收到 (via CHI message routing)
  → EP_SNF 调用 EPBackend::handleRemoteMiss()
    → EPBackend → UBCCController (单进程内 API 调用)
      → grant 决策 → EPBackend 更新 internal state ✅
        → EP_SNF 发 CHI CompData response 回 HN-F ✅ (通路存在)
          → ⚠️ payload 是 dummy 0 数据，未接入 EPBackend/UBCC 的真实 grant data
```

M4~M8 的 C++ Self-Test 通过 `installSentinelForTest()`、`processOuterRequest()` 等 API 直接构造协议状态，绕过了 CHI response 数据路径，因此未捕获 payload 为空的问题。Q2 端到端测试要求 **payload 为真实非零数据**：

```
HN-F 发 ReadNoSnp → EP_SNF 收 → EPBackend 处理
  → EP_SNF 发 CHI CompData response 回 HN-F (带真实数据 payload)
    → HN-F 收 data → forward to CPU L1/L2 → CPU load 完成 → [READ_VAL] ... MATCH
```

##### 验证方法

1. **静态审查**: 检查 `EPSNFController.cc` 中的 `recvRequestMsg()` 逻辑，确认是否存在从 EPBackend 返回 grant 后发 CHI response 回 HN-F 的路径。
2. **追踪验证**: 在 CHI SLICC 协议中搜索 `Send_ReadNoSnp` 的 response 完成路径，确认 EP_SNF 作为 SNF 类型的 controller 是否有发送 `CompData` 回 requester HN-F 的 action 定义。
3. **最小烟测**: 构造一个单节点场景，让 CPU 对 DSM 地址做一次 load，触发 HN-F `ReadNoSnp` → EP_SNF → 应答回 HN-F → CPU 完成 load。记为 `Q1-TC0`。

##### 故障诊断与修复

| 症状 | 可能原因 | 修复方案 |
|---|---|---|
| EP_SNF 收到 ReadNoSnp 后无动作 | EPSNFController 未正确注册为 CHI SNF 角色 | 检查 `EPSNFController` 的 SLICC 基类与 CHI port 连接 |
| response 发出但 payload 为 0 | CompData 的 data 字段未接入 EPBackend grant 结果 | 在 SLICC action 中将 `CompData` 的 data field 绑定到 EPBackend 的 `lastGrantData()` 接口 |
| response 发出但 HN-F 不收 | HN-F 的 transaction buffer 未匹配 response | 检查 CHI transaction ID 匹配与 TxnId 分配 |
| HN-F 收到 response 但 data 不进 cache | alloc_on_* 参数未对 DSM 地址生效 | 见 §2.2.3 |

##### 修复优先级

- **P0 必做**: EP_SNF 发出的 `CompData` 必须携带真实非零 payload（来自 EPBackend/UBCC 的 grant 数据），否则 Q2 的 `[READ_VAL]` 验证无法通过。
- **允许的最小实现**: 在现有 `RespSepData + CompData` 通路基础上，修改 SLICC action 中 `CompData` 的 data 绑定，使其来源从 dummy 0 切换到 EPBackend 的 grant data buffer。

#### 2.2.2 DSM VA Mapping 在 Ruby/CHI 配置中的状态

##### 当前 `create_ubcc_system()` 的地址映射

在 `CHI_ubcc_framework.py` 的 `create_ubcc_system()` 中：
- HN-F 的 `addr_ranges` = `[local_private_range, ubcc_exclusive_range]`（第 82-83 行）
- **DSM range 不在 HN-F addr_ranges 内**
- CPU 的 `phys_pool_id` 设置？需检查。

**Node-local PA 布局（与 `create_ubcc_system()` / `NodeAddressMap` 一致）**：

关键常量（定义于 `CHI_basic_framework_config.py`）：
- `DEFAULT_SEG_SIZE = 128MB = 0x0800_0000`
- `NODE_ADDR_SHIFT = 40`
- `PhyBase = node_id << 40`（即 node 间间距为 1TB = `0x0100_0000_0000`）
- 每 node 有效区域 = 5 × SEG_SIZE = 640MB

每个 node 的 PA 空间由 5 个连续的区域组成：`LocalPrivate (1*SEG)` + `UbccExclusive (1*SEG)` + `DSM_0 (1*SEG)` + `DSM_1 (1*SEG)` + `DSM_2 (1*SEG)`。

具体数值（所有地址为 hex，SEG = `0x0800_0000`）：

```
Node0 PA 布局 (PhyBase = 0 << 40 = 0x0000_0000_0000):
  [0x0000_0000_0000, 0x0000_0800_0000)  LocalPrivate   (128MB)
  [0x0000_0800_0000, 0x0000_1000_0000)  UbccExclusive  (128MB)
  [0x0000_1000_0000, 0x0000_1800_0000)  DSM_0          (128MB)
  [0x0000_1800_0000, 0x0000_2000_0000)  DSM_1          (128MB)
  [0x0000_2000_0000, 0x0000_2800_0000)  DSM_2          (128MB)
  ─────────────────────────────────────────
             每节点有效范围: 640MB

Node1 PA 布局 (PhyBase = 1 << 40 = 0x0100_0000_0000):
  [0x0100_0000_0000, 0x0100_0800_0000)  LocalPrivate   (128MB)
  [0x0100_0800_0000, 0x0100_1000_0000)  UbccExclusive  (128MB)
  [0x0100_1000_0000, 0x0100_1800_0000)  DSM_0          (128MB)
  [0x0100_1800_0000, 0x0100_2000_0000)  DSM_1          (128MB)
  [0x0100_2000_0000, 0x0100_2800_0000)  DSM_2          (128MB)

Node2 PA 布局 (PhyBase = 2 << 40 = 0x0200_0000_0000):
  [0x0200_0000_0000, 0x0200_0800_0000)  LocalPrivate   (128MB)
  [0x0200_0800_0000, 0x0200_1000_0000)  UbccExclusive  (128MB)
  [0x0200_1000_0000, 0x0200_1800_0000)  DSM_0          (128MB)
  [0x0200_1800_0000, 0x0200_2000_0000)  DSM_1          (128MB)
  [0x0200_2000_0000, 0x0200_2800_0000)  DSM_2          (128MB)
```

每个 node 的独立内存后端（由 `create_ubcc_system()` 创建）：
- `l_snf`（L_SNF）：覆盖 `[PhyBase + 0*SEG, PhyBase + 2*SEG)` — 即 LocalPrivate + UbccExclusive（256MB）
- `dl_snf`（DL_SNF）：覆盖 `[PhyBase + (2+node_id)*SEG, PhyBase + (3+node_id)*SEG)` — 即本 node 作为 home 的 DSM 行（128MB）
- `ep_snf`（EP_SNF）：覆盖所有 remote DSM 行 — 即 `{DSM_k | k ≠ node_id}` 的 PA window

跨节点访问通过全局 PA 路由：`NodeAddressMap::buildDsmPA(tgt_node, home_node, offset)` 将 home node 的 DSM offset 转换为目标 node 可路由的 PA（目标 node PA 空间下 `DSM_<home>` window 内的地址）。

**DSM VA Mapping 公式（与 `basic_framework_se.py` 对齐）**：
```
DSM_VA_BASE = MaxAddr - 4 * SEG_SIZE
# 注意：无 +1 偏移，VA base 为自然对齐的 4*SEG 倍数边界
# 每个 node k 的 DSM_k 窗口映射：
#   DSM_VA_BASE + k * SEG_SIZE  →  PhyBase_local + 2*SEG + k*SEG
```

##### 检查 `basic_framework_se.py` 和 `CHI_ubcc_framework.py`

| 配置项 | `basic_framework_se.py` (Phase1 基线) | `CHI_ubcc_framework.py` (Ruby 基线) | 差异 |
|---|---|---|---|
| DSM VA→PA 映射 | ✅ 有（第 112-115 行 `proc.map()`） | ❌ 无（Ruby 框架不创建 Process） | **需移植** |
| CPU 类型 | AtomicSimpleCPU 或 TimingSimpleCPU | 走 `ClusterCHI_RNF` 创建的 sequencer | **默认 Atomic-like** |
| L1/L2 Cache | 无（走 membus） | 有（`CHI_L1Controller` + `CHI_L2Controller`） | Ruby 已有 |
| 地址空间 | per-node PA | per-node PA（`NodeAddressMap`） | 一致 |

##### 修复方案

**需要在 Ruby/CHI 配置中加入 DSM VA mapping**：

在调用 `create_ubcc_system()` 的启动脚本中（如 `run_ubcc_ruby_test.py`），加入与 `basic_framework_se.py` 相同的 VA→PA 映射逻辑：

```python
# 在 gem5 config 脚本中加入
dsm_va_base = MaxAddr - 4 * seg_size
for node_id in range(num_nodes):
    for cpu in cluster_cpus:
        proc = cpu.workload[0]  # SEWorkload 已创建
        for nid in range(num_nodes):
            dsm_pa_base = addr_map.dsmLocalBase(nid)
            dsm_va = dsm_va_base + nid * seg_size
            proc.map(dsm_va, dsm_pa_base, seg_size, cacheable=True)
```

**需要确认的问题**：
- Ruby sequencer 路径下，CPU `phys_pool_id` 是否已正确设置？当前 `ClusterCHI_RNF` 未显示设置 `phys_pool_id`。
- **[TBD-Q1-1]**: 是否需要在 `ClusterCHI_RNF.__init__()` 中为 CPU 设置 `phys_pool_id`？当前可能继承 SE workload 的默认值。

##### 地址边界测试用例（融入 Q2 验证）

| 测试项 | 目的 | 方法 |
|---|---|---|
| DSM_VA_BASE 开始地址 | 确认 DSM_VA_BASE 可正确 ldr/str | 在 TC1 中额外加载 `dsm_addr(0, 0)` 和 `dsm_addr(0, 0xFF_FFFF)` 验证 |
| DSM_VA_BASE + 3*SEG 结束地址 | 确认最高 DSM 窗口末尾可访问 | `dsm_addr(2, SEG_SIZE - 4)` 读/写 |
| 超出 DSM 区域的负例 | 确认 `dsm_addr(3, 0)` 触发 mapping error | TC9 中覆盖 |
| 跨 node DSM PA 视图一致性 | 确认 Node_i 上 DSM_k 的 PA 与 home Node_k 的 DSMPA 一致 | 通过 Layer 3 observation 交叉验证 `buildGlobalPA` 结果 |

#### 2.2.3 DSM range 进入 HN-F addr_ranges (L3)

##### 当前 HN-F addr_ranges

```python
# CHI_ubcc_framework.py 第 81-84 行
nd['hnf_wrapper'], nd['hnf_cntrl'] = _make_hnf(
    ruby_system,
    [cfg.local_private_range, cfg.ubcc_exclusive_range],  # ← 无 DSM
    HNFCache, node_id)
```

**问题**: DSM range 不在 HN-F `addr_ranges` 中，意味 HN-F **不负责 DSM 地址的 L3 缓存与 snoop 管理**。DSM 请求直接路由到 `EP_SNF`。

##### 修改 `_make_hnf()` 中的 addr_ranges

需要将 DSM range 加入 HN-F 的 `addr_ranges`：

```python
# 修改后
nd['hnf_wrapper'], nd['hnf_cntrl'] = _make_hnf(
    ruby_system,
    [cfg.local_private_range,
     cfg.ubcc_exclusive_range,
     NodeConfig.dsm_global_range(DEFAULT_SEG_SIZE)],  # ← 新增 DSM
    HNFCache, node_id)
```

**但需注意**：如果 DSM range 进 HN-F addr_ranges，那么：
1. HN-F 会对 DSM 地址尝试 allocate L3 cache line
2. local CPU 对 DSM 地址的访问会先查 HN-F L3
3. HN-F 需要知道 DSM 行的 owner/sharer 信息（通过 sentinel registration）
4. HN-F snoop/directory 需要与 home UBCC 的 global directory 协同

**这是 M4 sentinel registration 已经打好的基础。** 但需要验证：

##### 分析 alloc_on_* / dealloc_on_* 配置

当前 UBCC 框架代码未见显式设置 `alloc_on_*` 参数。需要：
1. 检查 CHI 协议中 `HNFCache` controller 的 `alloc_on_readshared`、`alloc_on_readunique` 等默认值
2. 确认对 DSM 地址的 alloc 行为是否符合预期
3. 评估是否需要为 DSM 地址单独配置 alloc/dealloc 策略

##### 评估 inclusivity 策略

| 策略 | 含义 | 对 DSM 的影响 |
|---|---|---|
| Inclusive | L3 包含所有 L1/L2 行 | DSM 行的 L3 副本必须与 home UBCC global state 一致 |
| Exclusive | L3 与 L1/L2 互斥 | DSM 行可能不在 L3，降低了一致性维护复杂度 |
| NINE (Non-Inclusive Non-Exclusive) | L3 独立缓存 | 灵活的中间方案 |

**推荐**: 对 DSM 地址采用 NINE 策略。原因：
- DSM 行的真实 owner 可能在 remote node，L3 inclusive 意味着每次 remote owner 变更都要 invalidate 本 node L3
- NINE 允许 L3 缓存 DSM 行的本地 hot 副本，同时由 sentinel registration 管理外部一致性
- 当 sentinel 被 recall/invalidate 时，L3 中对应 DSM 行一并失效（通过正常的 CHI snoop 机制）

##### **[TBD-Q1-2]**: 如果 DSM range 进 HN-F addr_ranges 后导致 L3 alloc 行为异常（例如 DSM 行与 DSM 行本地缓存打架），是否需要对 DSM 地址单独设置 `alloc_on_*=false` 策略？建议先不改 alloc 策略，在 Q2 TC1 中观察行为。

#### 2.2.4 回归验证

- **全部 TC1~TC5 回归通过**（Phase1/2/3 基线）
- **M4~M8 全部 `MxSelfTest` 回归通过**（278 项 C++ 自检）
- **T0 全部 7 个 `Sync_Wait` testcase 通过**（70 项断言）

### 2.3 验收标准

| # | 验收项 | PASS 条件 |
|---|---|---|
| Q1-1 | EP_SNF→HN response 数据通路 | `Q1-TC0` PASS：单节点 CPU load DSM 地址 → data 返回非零真实值 → CPU 读到正确值（`[READ_VAL] ... MATCH`），确认 `CompData` payload 已接入 EPBackend/UBCC 真实数据 |
| Q1-2 | DSM VA Mapping | gem5 config 中 DSM VA→PA 映射可用，ARM workload 可通过 `ldr/str` 访问 DSM 地址 |
| Q1-3 | DSM range 进 HN-F addr_ranges | HN-F `addr_ranges` 包含 DSM range，系统 `m5.instantiate()` 不报错 |
| Q1-4 | M4~M8 自检回归 | 所有 `MxSelfTest` 0 FAIL（EP_SNF 行为变更可能影响 M5/M6 自检） |
| Q1-5 | TC1~TC5 回归 | 全部 PASS |
| Q1-6 | T0 Sync_Wait 回归 | 全部 PASS |

---

## 3. 阶段 Q2: 端到端应用层测试

> 优先级: 🔴 P0 | 执行顺序: 第二 | 依赖: Q1 完成

### 3.1 测试架构

#### 3.1.1 分层验证模型

```
                  ┌─────────────────────────────────────┐
Layer 1:          │    ARM workload (C/内联汇编)          │
应用层验证          │    ldr/str DSM 地址                  │
(主验证层)          │    打印 read 值到 stdout              │
                  └─────────────┬───────────────────────┘
                                │ 通过真实 CHI 协议路径
                  ┌─────────────▼───────────────────────┐
Layer 2:          │    CHI + EP + UBCC 协议栈            │
协议路径            │    (M4~M8 已闭环)                    │
                  └─────────────┬───────────────────────┘
                                │ test-only observation hook
                  ┌─────────────▼───────────────────────┐
Layer 3:          │    只读检测 (read-only assertions)     │
底层辅助验证        │    - HN-F directory 状态             │
(辅助验证层)        │    - UBCC directory 状态             │
                  │    - protocol trace 关键事件          │
                  └─────────────────────────────────────┘
```

- **Layer 1 是主验证层**: 应用层的 read 值必须与 write 值一致。PASS/FAIL 由 Layer 1 结果决定。
- **Layer 3 是辅助验证层**: 不改变协议行为，只做只读观测。当 Layer 1 PASS 时，Layer 3 用于 confirm 协议状态符合预期。当 Layer 1 FAIL 时，Layer 3 用于定位根因。

#### 3.1.2 测试目录结构

```
tests/e2e/
├── workloads/                       # ARM workload C 源码
│   ├── e2e_tc1_dsm_local.c         # 单节点本地 DSM 读写 (TC1)
│   ├── e2e_tc2_remote_read.c       # 双节点 remote read (TC2)
│   ├── e2e_tc3_pingpong.c          # 双节点 Ping-Pong (TC3)
│   ├── e2e_tc4_three_node_ring.c   # 三节点环 (TC4)
│   ├── e2e_tc5_single_writer.c     # 单写者正确性 (TC5)
│   ├── e2e_tc6_multi_sharer.c      # 多 Sharer 读一致性 (TC6)
│   ├── e2e_tc7_writeback_evict.c   # Writeback 后读 (TC7)
│   ├── e2e_tc8_upgrade_invalidate.c # Shared→Upgrade 失效 (TC8)
│   ├── e2e_tc9_non_dsm_negative.c  # Non-DSM 地址负例 (TC9)
│   └── e2e_tc10_concurrent_atomic.c # 并发读写原子性 (TC10)
├── helpers/                         # 测试辅助头文件
│   ├── dsm_access.h                # DSM load/store 内联汇编
│   ├── barrier.h                   # Sync_Wait 封装
│   └── e2e_check.h                 # 结果验证 + 输出标记
├── test_e2e.py                      # Python 测试驱动
├── test_e2e_runner.sh               # 编译 + 运行脚本
└── config/                          # gem5 配置变体
    └── e2e_cfg_base.py             # 基于 CHI_ubcc_framework 的 E2E 配置
```

#### 3.1.3 DSM 地址访问方式

从 ARM workload 中使用内联汇编直接访问 DSM 地址：

```c
// dsm_access.h
#ifndef E2E_DSM_ACCESS_H
#define E2E_DSM_ACCESS_H

#include <stdint.h>

// DSM VA base: 由 gem5 config 设置 (MaxAddr - 4*SEG_SIZE)
// 每个 node 的 DSM_k 映射到 DSM_VA_BASE + k * SEG_SIZE
// SEG_SIZE = 128MB = 0x8000000

#define SEG_SIZE  0x8000000ULL   // 128 MB
#define DSM_VA_BASE  (0xFFFFFFFFFFFFULL - 4 * SEG_SIZE)

static inline volatile uint32_t* dsm_addr(int home_node, uint32_t offset)
{
    uint64_t va = DSM_VA_BASE + (uint64_t)home_node * SEG_SIZE + offset;
    return (volatile uint32_t*)va;
}

// DSM load (32-bit)
static inline uint32_t dsm_load(int home_node, uint32_t offset)
{
    uint32_t val;
    asm volatile("ldr %w0, [%1]" : "=r"(val) : "r"(dsm_addr(home_node, offset)));
    return val;
}

// DSM store (32-bit)
static inline void dsm_store(int home_node, uint32_t offset, uint32_t val)
{
    asm volatile("str %w0, [%1]" : : "r"(val), "r"(dsm_addr(home_node, offset)));
}

// DSM load (64-bit)
static inline uint64_t dsm_load64(int home_node, uint32_t offset)
{
    uint64_t val;
    asm volatile("ldr %0, [%1]" : "=r"(val) : "r"(dsm_addr(home_node, offset)));
    return val;
}

// DSM store (64-bit)
static inline void dsm_store64(int home_node, uint32_t offset, uint64_t val)
{
    asm volatile("str %0, [%1]" : : "r"(val), "r"(dsm_addr(home_node, offset)));
}

#endif // E2E_DSM_ACCESS_H
```

#### 3.1.4 Sync_Wait 多节点同步

```c
// barrier.h
#ifndef E2E_BARRIER_H
#define E2E_BARRIER_H

// Sync_Wait ARM syscall 436
// 参数 x0 = node_mask (bitmask, e.g. 0b111 for nodes 0,1,2)
static inline void sync_wait(unsigned int node_mask)
{
    register long x0 asm("x0") = (long)node_mask;
    register long x8 asm("x8") = 436;
    asm volatile("svc #0"
                 : "+r"(x0)
                 : "r"(x8)
                 : "memory");
}

#endif // E2E_BARRIER_H
```

#### 3.1.5 输出标记规范

所有 workload 必须使用统一标记格式，便于 Python harness 解析：

```
[E2E_META]   node=<id> test=<test_name>
[BEFORE_WR]  node=<id> home=<home_node> offset=<hex> val=<hex>
[AFTER_WR]   node=<id> home=<home_node> offset=<hex> val=<hex>
[BEFORE_RD]  node=<id> home=<home_node> offset=<hex>
[READ_VAL]   node=<id> home=<home_node> offset=<hex> expected=<hex> actual=<hex> MATCH|MISMATCH
[BARRIER]    node=<id> action=enter|exit mask=<hex>
[PHASE]      node=<id> phase=<name> status=done
[FATAL]      node=<id> reason=<msg>
```

### 3.2 测试用例

#### E2E-TC1: 单节点本地 DSM 读写烟测

| 字段 | 内容 |
|---|---|
| **ID** | `E2E-TC1` |
| **Purpose** | 验证 Node0 能对本地 DSM_0 做 store/load 并获得一致值。这是整个 E2E 测试的最小烟测，验证 CHI→EP→UBCC 的最短路径。 |
| **配置** | `TimingSimpleCPU` + L1/L2 (32kB/256kB) + Ruby/CHI + HNFCache 256kB |
| **Preconditions** | Q1 全部通过（EP_SNF→HN response 回环通、DSM VA 映射可用、DSM range 进 HN-F） |
| **Workload 源码** | `tests/e2e/workloads/e2e_tc1_dsm_local.c` |
| **Execution Steps** | 1. Node0 CPU0 对 DSM_0 offset=0 做 `str 0xCAFE`<br>2. Node0 CPU0 对同一地址做 `ldr`<br>3. 比较 read 值与 0xCAFE |
| **Layer 1（应用层验证）** | `[READ_VAL]` 输出: `actual=0xCAFE MATCH` |
| **Layer 3（只读观测）** | 通过 `inspectUbccDirForTest(line_pa)` 检查 home UBCC directory state 为 `G_M` 或 `G_E`，owner=Node0 |
| **Expected Output** | `[READ_VAL] node=0 home=0 offset=0 expected=cafe actual=cafe MATCH` |
| **Negative Criteria** | 读到旧值 0、读到随机值、MATCH 但 MISMATCH |

伪代码：
```c
// e2e_tc1_dsm_local.c
#include "dsm_access.h"
#include "e2e_check.h"

void main() {
    int node_id = GET_NODE_ID();
    printf("[E2E_META] node=%d test=TC1\n", node_id);

    uint32_t val = 0xCAFE;
    printf("[BEFORE_WR] node=%d home=0 offset=0 val=%x\n", node_id, val);
    dsm_store(0, 0, val);

    printf("[BEFORE_RD] node=%d home=0 offset=0\n", node_id);
    uint32_t got = dsm_load(0, 0);

    int match = (got == val);
    printf("[READ_VAL] node=%d home=0 offset=0 expected=%x actual=%x %s\n",
           node_id, val, got, match ? "MATCH" : "MISMATCH");

    printf("[PHASE] node=%d phase=done status=done\n", node_id);
    _exit(match ? 0 : 1);
}
```

#### E2E-TC2: 双节点 Remote Read（写者→读者）

| 字段 | 内容 |
|---|---|
| **ID** | `E2E-TC2` |
| **Purpose** | Node0 写 DSM_1 (home=Node1)，Node1 读同一 line。验证 remote write→recall→remote read 的完整路径。 |
| **配置** | 同上 |
| **Preconditions** | Sync_Wait 可用；Q1 通过 |
| **Workload 源码** | `tests/e2e/workloads/e2e_tc2_remote_read.c` |
| **Execution Steps** | Phase 1: Node0 写 DSM_1 offset=0，值为 0x11223344<br>Phase 2: Node1 读同一 DSM_1 offset=0<br>使用 barrier 串行化 Phase 1→Phase 2 |
| **Layer 1 验证** | `[READ_VAL] node=1 home=1 ... actual=0x11223344 MATCH` |
| **Layer 3 观测** | Phase 1 后: home UBCC directory owner=Node0, state=G_M<br>Phase 2 中: 观测到 `GlobalRecallOwner` (recall from owner Node0)<br>Phase 2 后: Node1 成为 owner 或 sharer |
| **Expected Output** | Node1 读到 0x11223344 MATCH |
| **Negative Criteria** | Node1 读到旧值 0 或默认值 |

伪代码：
```c
// e2e_tc2_remote_read.c
#include "dsm_access.h"
#include "barrier.h"
#include "e2e_check.h"

void main() {
    int node_id = GET_NODE_ID();
    printf("[E2E_META] node=%d test=TC2\n", node_id);

    if (node_id == 0) {
        // Phase 1: Node0 writes DSM_1 (home=Node1)
        uint32_t val = 0x11223344;
        printf("[BEFORE_WR] node=%d home=1 offset=0 val=%x\n", node_id, val);
        dsm_store(1, 0, val);
        printf("[AFTER_WR] node=%d home=1 offset=0 val=%x\n", node_id, val);
    }

    sync_wait(0b011);  // Node0 and Node1 sync

    if (node_id == 1) {
        // Phase 2: Node1 reads DSM_1
        printf("[BEFORE_RD] node=%d home=1 offset=0\n", node_id);
        uint32_t got = dsm_load(1, 0);
        int match = (got == 0x11223344);
        printf("[READ_VAL] node=%d home=1 offset=0 expected=11223344 actual=%x %s\n",
               node_id, got, match ? "MATCH" : "MISMATCH");
        _exit(match ? 0 : 1);
    }

    sync_wait(0b011);  // Node0 waits for Node1 to finish
    printf("[PHASE] node=%d phase=done status=done\n", node_id);
    _exit(0);
}
```

#### E2E-TC3: 双节点 Ping-Pong Owner Transfer（3 轮）

| 字段 | 内容 |
|---|---|
| **ID** | `E2E-TC3` |
| **Purpose** | Node0→Node1 交替写同一 DSM_1 line，验证每轮 owner transfer 后读到最新值 |
| **配置** | 同上 |
| **Preconditions** | E2E-TC2 通过 |
| **Workload 源码** | `tests/e2e/workloads/e2e_tc3_pingpong.c` |
| **Execution Steps** | Round 1: Node0 写 0xA → barrier → Node1 读 → 验证 0xA<br>Round 2: Node1 写 0xB → barrier → Node0 读 → 验证 0xB<br>Round 3: Node0 写 0xC → barrier → Node1 读 → 验证 0xC<br>共 6 个 barrier phase |
| **Layer 1 验证** | 三轮全部 MATCH |
| **Layer 3 观测** | 每轮后目录 owner 唯一（不应出现双 owner） |
| **Expected Output** | 6 行 `[READ_VAL] ... MATCH` |
| **Negative Criteria** | 任一轮 MISMATCH、目录出现双 owner |

伪代码：
```c
// e2e_tc3_pingpong.c
void main() {
    int node_id = GET_NODE_ID();
    printf("[E2E_META] node=%d test=TC3\n", node_id);

    int fail = 0;

    // Round 1: Node0 writes 0xA, Node1 reads
    if (node_id == 0) dsm_store(1, 0, 0xA);
    sync_wait(0b011);
    if (node_id == 1) { if (dsm_load(1, 0) != 0xA) fail++; }
    sync_wait(0b011);

    // Round 2: Node1 writes 0xB, Node0 reads
    if (node_id == 1) dsm_store(1, 0, 0xB);
    sync_wait(0b011);
    if (node_id == 0) { if (dsm_load(1, 0) != 0xB) fail++; }
    sync_wait(0b011);

    // Round 3: Node0 writes 0xC, Node1 reads
    if (node_id == 0) dsm_store(1, 0, 0xC);
    sync_wait(0b011);
    if (node_id == 1) { if (dsm_load(1, 0) != 0xC) fail++; }
    sync_wait(0b011);

    printf("[PHASE] node=%d phase=done status=%s\n", node_id, fail ? "FAIL" : "done");
    _exit(fail ? 1 : 0);
}
```

#### E2E-TC4: 三节点环 Owner Transfer

| 字段 | 内容 |
|---|---|
| **ID** | `E2E-TC4` |
| **Purpose** | Node0→Node1→Node2→Node0 环形 owner transfer，验证完整三节点一致性 |
| **配置** | 同上 |
| **Preconditions** | E2E-TC3 通过 |
| **Workload 源码** | `tests/e2e/workloads/e2e_tc4_three_node_ring.c` |
| **Execution Steps** | 1. Node0 写 0x1 → barrier<br>2. Node1 写 0x2 → barrier<br>3. Node2 写 0x3 → barrier<br>4. Node0 读，验证 0x3 |
| **Layer 1 验证** | Node0 最终读到 0x3 MATCH |
| **Layer 3 观测** | 每步后所有三侧 directory/requester 状态一致，owner 唯一 |
| **Expected Output** | `[READ_VAL] node=0 ... actual=3 MATCH` |
| **Negative Criteria** | 数据丢失、状态不一致、读到 0x1 或 0x2 |

伪代码：
```c
// e2e_tc4_three_node_ring.c
void main() {
    int node_id = GET_NODE_ID();
    printf("[E2E_META] node=%d test=TC4\n", node_id);

    // Node0 writes 0x1 to DSM_2 (home=Node2) for this example
    if (node_id == 0) dsm_store(2, 0, 0x1);
    sync_wait(0b111);

    if (node_id == 1) dsm_store(2, 0, 0x2);
    sync_wait(0b111);

    if (node_id == 2) dsm_store(2, 0, 0x3);
    sync_wait(0b111);

    int fail = 0;
    if (node_id == 0) {
        uint32_t got = dsm_load(2, 0);
        if (got != 0x3) fail++;
        printf("[READ_VAL] node=%d home=2 offset=0 expected=3 actual=%x %s\n",
               node_id, got, got == 0x3 ? "MATCH" : "MISMATCH");
    }
    sync_wait(0b111);

    printf("[PHASE] node=%d phase=done status=%s\n", node_id, fail ? "FAIL" : "done");
    _exit(fail ? 1 : 0);
}
```

#### E2E-TC5: 单写者正确性（并发写，最终一致）

| 字段 | 内容 |
|---|---|
| **ID** | `E2E-TC5` |
| **Purpose** | 验证同一时刻并发写不丢数据，最终一致。三节点同时对同一 DSM line 写不同值，最终所有节点读到相同值 |
| **配置** | 同上 |
| **Preconditions** | E2E-TC4 通过 |
| **Workload 源码** | `tests/e2e/workloads/e2e_tc5_single_writer.c` |
| **Execution Steps** | 1. Barrier 同步 Node0/1/2<br>2. 三者同时对同一 DSM line 写不同值（无 barrier 间串行）<br>3. Barrier 等待所有写完成<br>4. 所有节点读，验证值相同且为 {0xAA, 0xBB, 0xCC} 之一 |
| **Layer 1 验证** | 所有节点最终读到相同值（值必须为写入的 3 个值之一） |
| **Layer 3 观测** | 检查 W→R 闭环，home directory 中 owner 唯一 |
| **Expected Output** | Node0/1/2 的 `[READ_VAL] actual=` 全部相同 |
| **Negative Criteria** | 数据撕裂（部分字节来自不同写入）、读到不同于任何写入者的值 |

伪代码：
```c
// e2e_tc5_single_writer.c
void main() {
    int node_id = GET_NODE_ID();
    printf("[E2E_META] node=%d test=TC5\n", node_id);

    // Sync before concurrent writes
    sync_wait(0b111);

    // Concurrent writes: each node writes its own value
    uint32_t vals[] = {0xAA000001, 0xBB000002, 0xCC000003};
    dsm_store(1, 0, vals[node_id]);

    // Sync after all writes
    sync_wait(0b111);

    // All nodes read
    uint32_t got = dsm_load(1, 0);
    printf("[READ_VAL] node=%d home=1 offset=0 expected=%x actual=%x %s\n",
           node_id, vals[node_id], got,
           (got == 0xAA000001 || got == 0xBB000002 || got == 0xCC000003) ? "MATCH" : "MISMATCH");

    // Cross-check: all nodes must read the same value
    sync_wait(0b111);
    printf("[PHASE] node=%d phase=done status=done\n", node_id);
    _exit(0);
}
```

#### E2E-TC6: 多 Sharer 读一致性（1 写 2 同时读）

| 字段 | 内容 |
|---|---|
| **ID** | `E2E-TC6` |
| **Purpose** | Node0 写 DSM_2 后，Node1 和 Node2 同时 Shared read，验证读到正确值 |
| **配置** | 同上 |
| **Preconditions** | E2E-TC5 通过 |
| **Workload 源码** | `tests/e2e/workloads/e2e_tc6_multi_sharer.c` |
| **Execution Steps** | Phase 1: Node0 写 DSM_2 offset=0 值为 0xDEADBEEF<br>Phase 2: Node1 和 Node2 同时读（同一 barrier 后） |
| **Layer 1 验证** | Node1 和 Node2 都读到 0xDEADBEEF |
| **Layer 3 观测** | directory sharer mask 包含 Node1 和 Node2，state=G_S |
| **Expected Output** | 两行 `[READ_VAL] ... actual=DEADBEEF MATCH` |
| **Negative Criteria** | 任一读到旧值、data 未正确传播到 sharers |

伪代码：
```c
// e2e_tc6_multi_sharer.c
void main() {
    int node_id = GET_NODE_ID();
    printf("[E2E_META] node=%d test=TC6\n", node_id);

    // Phase 1: Node0 writes
    if (node_id == 0) {
        dsm_store(2, 0, 0xDEADBEEF);
    }
    sync_wait(0b111);

    // Phase 2: Node1 and Node2 read concurrently
    int fail = 0;
    if (node_id == 1 || node_id == 2) {
        uint32_t got = dsm_load(2, 0);
        fail = (got != 0xDEADBEEF);
        printf("[READ_VAL] node=%d home=2 offset=0 expected=DEADBEEF actual=%x %s\n",
               node_id, got, fail ? "MISMATCH" : "MATCH");
    }
    sync_wait(0b111);

    printf("[PHASE] node=%d phase=done status=%s\n", node_id, fail ? "FAIL" : "done");
    _exit(fail ? 1 : 0);
}
```

#### E2E-TC7: Writeback 后读（数据不丢失）

| 字段 | 内容 |
|---|---|
| **ID** | `E2E-TC7` |
| **Purpose** | Node0 写后触发 evict/writeback，Node1 再读，验证数据不丢失 |
| **配置** | 同上，需触发容量 eviction（通过写满 L1/L2 cache set 来驱逐目标行） |
| **Preconditions** | E2E-TC6 通过 |
| **Workload 源码** | `tests/e2e/workloads/e2e_tc7_writeback_evict.c` |
| **Execution Steps** | 1. Node0 写 DSM_1 offset=0 值为 0x55667788<br>2. Node0 写大量其他地址以触发 cache eviction（驱逐 DSM_1 行）<br>3. barrier<br>4. Node1 读 DSM_1 offset=0 |
| **Layer 1 验证** | Node1 读到 0x55667788 |
| **Layer 3 观测** | writeback 后 home directory dirty=false；Node1 read 不触发 GlobalRecallOwner（因为 dirty data 已在 home） |
| **Expected Output** | `[READ_VAL] node=1 ... actual=55667788 MATCH` |
| **Negative Criteria** | writeback 丢失数据导致 Node1 读到旧值/默认值 |

**实现注意事项**：
- 需要 TimingSimpleCPU + 真实 L1/L2 才能触发 capacity eviction（AtomicSimpleCPU 不维护 cache state）
- 需要知道 L1 数据 cache 的 set/way 结构来确定 eviction 地址模式
- **[TBD-Q2-1]**: 如果 eviction 路径触发条件复杂，允许阶段交付时先以直接 EPBackend `handleWriteback()` API 触发 writeback（作为 Layer 3 辅助注入，但 Layer 1 验证仍走真实 ldr/str）。

伪代码：
```c
// e2e_tc7_writeback_evict.c
#include "dsm_access.h"
#include "barrier.h"

#define EVICT_SET_SIZE  (32 * 1024)  // 32kB L1D cache, flood to evict
#define EVICT_STRIDE    64           // cache line size

void main() {
    int node_id = GET_NODE_ID();
    printf("[E2E_META] node=%d test=TC7\n", node_id);

    if (node_id == 0) {
        // Phase 1: Write targeted value
        dsm_store(1, 0, 0x55667788);

        // Phase 2: Flood cache to trigger eviction
        volatile uint32_t *evict = (volatile uint32_t*)0x10000000; // local private addr
        for (int i = 0; i < EVICT_SET_SIZE; i += EVICT_STRIDE) {
            evict[i/4] = (uint32_t)i;
        }
    }

    sync_wait(0b011);

    if (node_id == 1) {
        uint32_t got = dsm_load(1, 0);
        int match = (got == 0x55667788);
        printf("[READ_VAL] node=%d home=1 offset=0 expected=55667788 actual=%x %s\n",
               node_id, got, match ? "MATCH" : "MISMATCH");
        _exit(match ? 0 : 1);
    }

    sync_wait(0b011);
    _exit(0);
}
```

#### E2E-TC8: Shared→Upgrade 失效其他 Sharer

| 字段 | 内容 |
|---|---|
| **ID** | `E2E-TC8` |
| **Purpose** | Node0 写 DSM_2 → Node1+Node2 都 Shared read → Node0 再写，验证 Node1/Node2 被 GlobalInvalidate |
| **配置** | 同上 |
| **Preconditions** | E2E-TC7 通过 |
| **Workload 源码** | `tests/e2e/workloads/e2e_tc8_upgrade_invalidate.c` |
| **Execution Steps** | Phase 1: Node0 写 0xAAA<br>Phase 2: Node1 读 + Node2 读（都 shared）<br>Phase 3: Node0 再写 0xBBB<br>Phase 4: Node1 读，应得 0xBBB |
| **Layer 1 验证** | Phase 4 中 Node1 读到 0xBBB（不是旧的 0xAAA） |
| **Layer 3 观测** | Phase 3 后: Node1/Node2 从 sharer mask 移除；观测到 GlobalInvalidate 发送 |
| **Expected Output** | `[READ_VAL] node=1 ... actual=BBB MATCH` |
| **Negative Criteria** | Node1 读到旧的 0xAAA、GlobalInvalidate 未触发 |

伪代码：
```c
// e2e_tc8_upgrade_invalidate.c
void main() {
    int node_id = GET_NODE_ID();
    printf("[E2E_META] node=%d test=TC8\n", node_id);

    // Phase 1: Node0 writes 0xAAA
    if (node_id == 0) dsm_store(2, 0, 0xAAA);
    sync_wait(0b111);

    // Phase 2: Node1 and Node2 shared read
    if (node_id == 1) dsm_load(2, 0);
    if (node_id == 2) dsm_load(2, 0);
    sync_wait(0b111);

    // Phase 3: Node0 writes 0xBBB (should invalidate sharers)
    if (node_id == 0) dsm_store(2, 0, 0xBBB);
    sync_wait(0b111);

    // Phase 4: Node1 reads (should get 0xBBB, not 0xAAA)
    int fail = 0;
    if (node_id == 1) {
        uint32_t got = dsm_load(2, 0);
        fail = (got != 0xBBB);
        printf("[READ_VAL] node=%d home=2 offset=0 expected=BBB actual=%x %s\n",
               node_id, got, fail ? "MISMATCH" : "MATCH");
    }
    sync_wait(0b111);

    printf("[PHASE] node=%d phase=done status=%s\n", node_id, fail ? "FAIL" : "done");
    _exit(fail ? 1 : 0);
}
```

#### E2E-TC9: Non-DSM 地址负例

| 字段 | 内容 |
|---|---|
| **ID** | `E2E-TC9` |
| **Purpose** | 验证对 `LocalPrivate` 或 `UbccExclusive` 区域地址尝试走跨节点 DSM 路径时被正确拒绝 |
| **配置** | 同上 |
| **Preconditions** | 无 |
| **Workload 源码** | `tests/e2e/workloads/e2e_tc9_non_dsm_negative.c` |
| **Execution Steps** | 1. Node0 尝试通过 DSM VA 窗口访问超出 DSM range 的地址（偏移到 LocalPrivate 或 UbccExclusive range）<br>2. 或直接构造一个 non-DSM PA 走 EP path（通过 Layer 3 helper 注入） |
| **Layer 1 验证** | 系统的地址守卫（`isDsm()` check）触发 fatal/assert，或结果被拒绝 |
| **Layer 3 观测** | EPBackend 的 `checkDsmAddr()` 返回 false；fatal 消息出现在 log |
| **Expected Output** | 系统在 fatal/assert 处停止（预期行为），或 `[FATAL] ... non-DSM address rejected` |
| **Negative Criteria** | Non-DSM 地址静默通过 EP path 并被当作 DSM 处理 |

**实现注意事项**：
- 这是一个负例测试，预期系统会 fatal/assert。Python harness 需要捕获预期的 fatal exit 并判定为 PASS。
- **[TBD-Q2-2]**: 如果当前系统对 non-DSM 地址通过 EP path 时没有 fatal 守卫（而是静默处理），需在 Q1 中加入地址守卫。

#### E2E-TC10: 并发读写原子性

| 字段 | 内容 |
|---|---|
| **ID** | `E2E-TC10` |
| **Purpose** | 验证并发 read 和 write 不会产生中间态脏读（无撕裂） |
| **配置** | 同上 |
| **Preconditions** | E2E-TC5 通过 |
| **Workload 源码** | `tests/e2e/workloads/e2e_tc10_concurrent_atomic.c` |
| **Execution Steps** | 1. Node0 循环写递增 counter 到 DSM_1<br>2. Node1 循环读同一 DSM_1 counter<br>3. 执行 N=100 轮<br>4. Python harness 收集 Node1 的每次读取值<br>5. 验证每次读取都是完整写入值（无部分撕裂） |
| **Layer 1 验证** | 每次读取值为某次完整写入的 counter 值（单调递增或无规律的合法值，但不应出现撕裂的中间值） |
| **Layer 3 观测** | home directory 始终只有 1 个 owner，无同时多 writer |
| **Expected Output** | Node1 所有读取值均合法（Python harness 判定） |
| **Negative Criteria** | 出现不属于任何写入的中间值（撕裂） |

伪代码：
```c
// e2e_tc10_concurrent_atomic.c
#include "dsm_access.h"
#include "barrier.h"

#define ROUNDS 100

void main() {
    int node_id = GET_NODE_ID();
    printf("[E2E_META] node=%d test=TC10\n", node_id);

    if (node_id == 0) {
        for (int i = 0; i < ROUNDS; i++) {
            dsm_store(1, 0, (uint32_t)(0xA0000000 + i));
        }
    } else if (node_id == 1) {
        for (int i = 0; i < ROUNDS; i++) {
            uint32_t got = dsm_load(1, 0);
            printf("[READ_VAL] node=%d home=1 offset=0 expected=* actual=%x MATCH\n",
                   node_id, got);
        }
    }

    printf("[PHASE] node=%d phase=done status=done\n", node_id);
    _exit(0);
}
```

### 3.3 Python 测试驱动

#### 3.3.1 `tests/e2e/test_e2e.py` 架构

```python
#!/usr/bin/env python3
"""E2E test driver for Qi Phase 2.
Launches gem5.opt with Ruby/CHI configuration, runs each test workload
on N=3 nodes, parses stdout for [READ_VAL] markers, and reports PASS/FAIL.
"""
import os
import sys
import re
import subprocess
import json
from pathlib import Path

GEM5_BIN = "build/ARM/gem5.opt"
E2E_CONFIG = "tests/e2e/config/e2e_cfg_base.py"
WORKLOAD_DIR = "tests/e2e/workloads"
ROUNDS = 100  # For TC10 concurrent atomic validation

TESTCASES = [
    "e2e_tc1_dsm_local",
    "e2e_tc2_remote_read",
    "e2e_tc3_pingpong",
    "e2e_tc4_three_node_ring",
    "e2e_tc5_single_writer",
    "e2e_tc6_multi_sharer",
    "e2e_tc7_writeback_evict",
    "e2e_tc8_upgrade_invalidate",
    "e2e_tc9_non_dsm_negative",
    "e2e_tc10_concurrent_atomic",
]

def compile_workload(name):
    """Cross-compile ARM workload for gem5 SE mode."""
    src = f"{WORKLOAD_DIR}/{name}.c"
    bin = f"{WORKLOAD_DIR}/{name}.elf"
    helpers = f"{WORKLOAD_DIR}/../helpers"
    cmd = (
        f"aarch64-linux-gnu-gcc -static -O0 -g "
        f"-I{helpers} "
        f"-o {bin} {src}"
    )
    subprocess.run(cmd, shell=True, check=True)
    return bin

def run_test(binary, test_id):
    """Run gem5 with the given binary and return (exit_code, stdout_lines)."""
    cmd = [
        GEM5_BIN,
        f"--outdir=m5out/{test_id}",
        E2E_CONFIG,
        f"--cmd={binary}",
        "--cpu-type=timing",
        "--ruby",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout.split("\n")

def parse_e2e_output(stdout_lines):
    """Parse [READ_VAL] markers from workload stdout.
    Returns list of dicts with keys: node, home, offset, expected, actual, verdict, raw_line.
    """
    reads = []
    pattern = re.compile(
        r"\[READ_VAL\]\s+node=(\d+)\s+home=(\d+)\s+offset=(\w+)\s+"
        r"expected=(\w+)\s+actual=(\w+)\s+(MATCH|MISMATCH)"
    )
    for line in stdout_lines:
        m = pattern.search(line)
        if m:
            reads.append({
                "node": int(m.group(1)),
                "home": int(m.group(2)),
                "offset": m.group(3),
                "expected": m.group(4),
                "actual": m.group(5),
                "verdict": m.group(6),
                "raw_line": line.strip(),
            })
    return reads

def verify_testcase(test_id, reads, raw_lines):
    """Per-testcase explicit verification rules.

    Each TC has a dedicated pass/fail rule. No generic "MATCH → PASS" fallback.
    Returns (passed: bool, details: str, mismatches: list).
    """

    # ── TC1: 恰好 1 条 READ_VALUE，且为 0xCAFE ──
    if test_id == "e2e_tc1_dsm_local":
        if len(reads) != 1:
            return False, f"TC1 FAILED: expected exactly 1 READ_VALUE, got {len(reads)}", reads
        actual = int(reads[0]["actual"], 16)
        if actual != 0xCAFE:
            return False, f"TC1 FAILED: expected 0xCAFE, got 0x{actual:X}", [reads[0]]
        return True, "TC1 PASSED: single read 0xCAFE", []

    # ── TC2: 恰好 1 条 READ_VALUE，Node1 读，值为 0x11223344 ──
    if test_id == "e2e_tc2_remote_read":
        if len(reads) != 1:
            return False, f"TC2 FAILED: expected exactly 1 READ_VALUE, got {len(reads)}", reads
        if reads[0]["node"] != 1:
            return False, f"TC2 FAILED: expected Node1 read, got Node{reads[0]['node']}", [reads[0]]
        actual = int(reads[0]["actual"], 16)
        if actual != 0x11223344:
            return False, f"TC2 FAILED: expected 0x11223344, got 0x{actual:X}", [reads[0]]
        return True, "TC2 PASSED: Node1 read 0x11223344", []

    # ── TC3: 恰好 6 条 READ_VALUE (3 轮 × 2 节点)，全部 MATCH ──
    if test_id == "e2e_tc3_pingpong":
        if len(reads) != 6:
            return False, f"TC3 FAILED: expected exactly 6 READ_VALUE, got {len(reads)}", reads
        mismatches = [r for r in reads if r["verdict"] != "MATCH"]
        if mismatches:
            return False, f"TC3 FAILED: {len(mismatches)} MISMATCH(es) in ping-pong rounds", mismatches
        return True, "TC3 PASSED: 6 reads all MATCH (3 rounds)", []

    # ── TC4: 恰好 4 条 READ_VALUE (4 步)，最后一步 Node0 读值为 0x3 ──
    if test_id == "e2e_tc4_three_node_ring":
        if len(reads) != 4:
            return False, f"TC4 FAILED: expected exactly 4 READ_VALUE, got {len(reads)}", reads
        # 最后一步：Node0 读取，验证值为 0x3
        last_read = reads[-1]
        if last_read["node"] != 0:
            return False, f"TC4 FAILED: final read expected Node0, got Node{last_read['node']}", [last_read]
        actual = int(last_read["actual"], 16)
        if actual != 0x3:
            return False, f"TC4 FAILED: final read expected 0x3, got 0x{actual:X}", [last_read]
        return True, "TC4 PASSED: 4-step ring, final Node0 read 0x3", []

    # ── TC5: 最终三节点读值相同且都属于 {0xAA000001, 0xBB000002, 0xCC000003} ──
    if test_id == "e2e_tc5_single_writer":
        if len(reads) < 3:
            return False, f"TC5 FAILED: expected ≥3 READ_VALUE (one per node), got {len(reads)}", reads
        legal = {0xAA000001, 0xBB000002, 0xCC000003}
        # 收集每个 node 的最后一次读取值
        node_last = {}
        for r in reads:
            node_last[r["node"]] = int(r["actual"], 16)
        if len(node_last) < 3:
            return False, f"TC5 FAILED: only {len(node_last)} nodes produced READ_VALUE", reads
        values = set(node_last.values())
        if len(values) != 1:
            return False, f"TC5 FAILED: nodes disagree on final value: {node_last}", reads
        final_val = list(values)[0]
        if final_val not in legal:
            return False, f"TC5 FAILED: final value 0x{final_val:X} not in legal set", reads
        return True, f"TC5 PASSED: all 3 nodes converged to 0x{final_val:X}", []

    # ── TC6: Node1 和 Node2 的 READ_VALUE 都应为 0xDEADBEEF ──
    if test_id == "e2e_tc6_multi_sharer":
        node1_reads = [r for r in reads if r["node"] == 1]
        node2_reads = [r for r in reads if r["node"] == 2]
        if len(node1_reads) == 0:
            return False, "TC6 FAILED: no READ_VALUE from Node1", reads
        if len(node2_reads) == 0:
            return False, "TC6 FAILED: no READ_VALUE from Node2", reads
        for r in node1_reads:
            if int(r["actual"], 16) != 0xDEADBEEF:
                return False, f"TC6 FAILED: Node1 read 0x{r['actual']}, expected 0xDEADBEEF", [r]
        for r in node2_reads:
            if int(r["actual"], 16) != 0xDEADBEEF:
                return False, f"TC6 FAILED: Node2 read 0x{r['actual']}, expected 0xDEADBEEF", [r]
        return True, "TC6 PASSED: Node1 and Node2 both read 0xDEADBEEF", []

    # ── TC7: 恰好 1 条 READ_VALUE，值为 0x55667788 ──
    if test_id == "e2e_tc7_writeback_evict":
        if len(reads) != 1:
            return False, f"TC7 FAILED: expected exactly 1 READ_VALUE, got {len(reads)}", reads
        actual = int(reads[0]["actual"], 16)
        if actual != 0x55667788:
            return False, f"TC7 FAILED: expected 0x55667788, got 0x{actual:X}", [reads[0]]
        return True, "TC7 PASSED: writeback+read preserved 0x55667788", []

    # ── TC8: Phase 3 Node1 读值为 0xBBB ──
    if test_id == "e2e_tc8_upgrade_invalidate":
        node1_reads = [r for r in reads if r["node"] == 1]
        if len(node1_reads) == 0:
            return False, "TC8 FAILED: no READ_VALUE from Node1 after upgrade", reads
        # 最后一轮（Phase 4）Node1 的读必须为 0xBBB
        last_n1 = node1_reads[-1]
        actual = int(last_n1["actual"], 16)
        if actual != 0xBBB:
            return False, f"TC8 FAILED: Node1 final read 0x{actual:X}, expected 0xBBB", [last_n1]
        return True, "TC8 PASSED: Node1 read 0xBBB after invalidate+upgrade", []

    # ── TC9: 必须检测到 [FATAL] 或明确拒绝信号；无输出 = FAIL ──
    if test_id == "e2e_tc9_non_dsm_negative":
        # 负例：不期望任何 [READ_VAL]
        if len(reads) > 0:
            return False, "TC9 FAILED: unexpected [READ_VAL] in negative test", reads
        # 必须检测到 fatal/rejection 信号
        has_fatal = any("[FATAL]" in line for line in raw_lines)
        if has_fatal:
            return True, "TC9 PASSED: expected [FATAL] detected", []
        # 无 fatal 且无输出 = FAIL（与旧逻辑不同：不再接受静默无输出）
        return False, "TC9 FAILED: no [FATAL] or rejection signal detected", []

    # ── TC10: 所有 read 值在合法集合内，无效 0 = FAIL ──
    if test_id == "e2e_tc10_concurrent_atomic":
        if len(reads) == 0:
            return False, "TC10 FAILED: no READ_VALUE from concurrent phase", []
        legal_values = set(0xA0000000 + i for i in range(ROUNDS))
        violations = []
        for r in reads:
            actual_val = int(r["actual"], 16)
            if actual_val == 0:
                violations.append(r)
            elif actual_val not in legal_values:
                violations.append(r)
        if violations:
            return False, f"TC10 FAILED: {len(violations)} illegal values (including 0)", violations
        return True, f"TC10 PASSED: {len(reads)} reads, all in legal range, no 0s", []

    # ── Unknown test: FAIL ──
    return False, f"FAILED: unknown test_id '{test_id}' — no verification rule defined", []
```

#### 3.3.2 Layer 3 只读 API 调用

在 `test_e2e.py` 中，通过 gem5 Python config 暴露的 SimObject 引用读取协议层内部状态：

```python
# 在 e2e_cfg_base.py 的 after_instantiate hook 中
def get_layer3_snapshot(system, line_pa):
    """Read-only observation of protocol state.
    Returns dict with keys: state, ownerNode, sharersMask, dirty, epoch.
    """
    backend = system.node0_ep_backend  # via SimObject reference
    ubcc = backend.getUBCC()
    dir_json = ubcc.inspectUbccDirForTest(line_pa)
    return json.loads(dir_json)
```

### 3.4 验收标准

#### 3.4.1 必达集（TC1~TC6 — Q2 必须 PASS 方可交付）

| # | 验收项 | PASS 条件 |
|---|---|---|
| Q2-1 | E2E-TC1 | 单节点本地 DSM 读写：`[READ_VAL] ... MATCH` |
| Q2-2 | E2E-TC2 | 双节点 remote read：Node1 读到 Node0 写入的值，MATCH |
| Q2-3 | E2E-TC3 | 双节点 Ping-Pong 3 轮：全部 6 读 MATCH |
| Q2-4 | E2E-TC4 | 三节点环：Node0 最终读到 0x3，MATCH |
| Q2-5 | E2E-TC5 | 单写者正确性：三节点并发写后读到相同值 |
| Q2-6 | E2E-TC6 | 多 Sharer 读：Node1+Node2 都读到 0xDEADBEEF |

#### 3.4.2 增强集（TC7~TC10 — Q2 交付时尽力完成，可作为 Q4 并行风险项）

| # | 验收项 | PASS 条件 | 风险说明 |
|---|---|---|---|
| Q2-7 | E2E-TC7 | Writeback 后读：数据不丢失，Node1 读到 0x55667788 | eviction 触发依赖 L1/L2 capacity pressure，可能需要 test-only API 辅助 |
| Q2-8 | E2E-TC8 | Shared→Upgrade：Node0 升级写后 Node1 读到新值 0xBBB | 依赖 GlobalInvalidate 完整通路（M8 已建，但端到端路径较深） |
| Q2-9 | E2E-TC9 | Non-DSM 负例：系统正确拒绝或 fatal | 负例测试，如当前无 fatal 守卫需 Q1 中加入 |
| Q2-10 | E2E-TC10 | 并发读写原子性：无撕裂值 | 依赖 timing + 真实 CHI concurrent 语义（当前 N=3 单 gem5 并发有限） |

#### 3.4.3 通用验收

| # | 验收项 | PASS 条件 |
|---|---|---|
| Q2-11 | 回归 | M4~M8 自检 0 FAIL、TC1~TC5 全部 PASS、T0 70 项 PASS |
| Q2-12 | 编译 | `scons build/ARM/gem5.opt -j20 PROTOCOL=CHI` 通过 |

**注意**: Q4（外部模块化架构设计）**仅依赖必达集（TC1~TC6）**。增强集（TC7~TC10）作为 Q4 并行风险项，可在 Q4 执行期间继续攻坚。

---

## 4. 阶段 Q3: UR RN-F + UbccExclusive 区域

> 优先级: 🟡 P1 | 执行顺序: 第三 | 依赖: Q2 必达集（TC1~TC6）完成

### 4.1 设计目标

1. 实现独立 **UR RN-F** (UBCC Reader RN-F) controller，专门负责 UBCC 对 `UbccExclusive` 区域的 coherent load/store
2. `UbccExclusive` 地址范围通过 HN-F 做 coherent 访问（走 L3 cache）
3. EP_RNF 不再承担任何本地 coherent 访问职责（职责净化）
4. EP_SNF 继续保持职责净化（仅 eviction/flushback + miss 拉数据）

### 4.2 组件设计

#### 4.2.1 UR RN-F Controller

##### 设计选择：基于 `ClusterCHI_RNF` 简化模板

当前 `EP_RNF` 和 `EP_SNF` 都基于 `Base_CHI_Cache_Controller`。UR RN-F 应采用相同基类，但**直接复用 `ClusterCHI_RNF` 模板的简化版**：

| 复用项 | 来源 | 说明 |
|---|---|---|
| CPU type | `TimingSimpleCPU` | 借用 gem5 现有 CPU 模型，不走真实 OoO pipeline |
| L1I + L1D caches | `_make_private_l1` from `CHI_ubcc_framework.py` | 64kB, assoc=4, 与 UR 规模匹配 |
| L2 controller | `CHI_L2Controller` 轻量简化 | 也可跳过 L2，UR 直接接 HN-F |
| Sequencer | `RubyPortProxy` 类 sequencer | 单一事务流，不需要多核心 sequencer |
| CHI-RNF 协议栈 | `ClusterCHI_RNF` 现有 SLICC 模板 | 复用 `ReadOnce`/`WriteUnique`/`WriteClean` 等 transaction handler |

**最小实现方案**：从 `ClusterCHI_RNF` 派生一个 `UbccRNF` 类，仅覆盖以下差异：
1. **地址范围限定**为 `ubcc_exclusive_range`（`[PhyBase + 1*SEG, PhyBase + 2*SEG)`）
2. **cache 大小**缩小为 64kB（UR 场景不需要大 cache）
3. **不需要 L2**：直接 downstream → HN-F
4. **不需要多 CPU**：仅 1 个 sequencer

##### SLICC State/Event 映射（I/V/D 三状态简化）

UR RN-F 的 SLICC state machine 仅需 3 个稳定状态，事件集为 CHI RNF 子集：

```
状态:
  I (Invalid)  -- 本行不在 UR cache 中
  V (Valid)    -- 本行在 UR cache 中，clean（exclusive 或 shared）
  D (Dirty)    -- 本行在 UR cache 中，dirty modified

事件（从 CHI SLICC RNF 模板裁剪）:
  ┌───────────────────┬──────────────────────────────────────────────┐
  │ 事件              │ CHI CHI 对应请求/响应                          │
  ├───────────────────┼──────────────────────────────────────────────┤
  │ ReadOnce         │ 读请求（无写意图）→ HN-F 返回 CompData         │
  │ WriteUnique      │ 独占写请求 → HN-F 返回 CompDBIDResp            │
  │ WriteClean       │ Clean writeback → HN-F 返回 RespSepData        │
  │ Evict            │ 驱逐通知 → HN-F 确认                          │
  │ Snoop (from HN-F)│ 收到 HN 发出的 snoop（如 invalidate/downgrade）│
  └───────────────────┴──────────────────────────────────────────────┘

转换规则:
  I --[ReadOnce]--> V        (HN-F 返回 data)
  I --[WriteUnique]--> D     (HN-F 返回 data + unique permission)
  V --[WriteUnique]--> D     (升级写)
  V --[Evict]--> I           (clean eviction)
  D --[WriteClean]--> V      (dirty writeback, 保留 clean copy)
  D --[WriteClean+Evict]--> I (dirty eviction)
  V/D --[SnoopInvalidate]--> I  (HN 发来的 invalidate)
```

**注意**：上述状态/事件映射是 CHI 协议层行为，不与 UBCC MESI 目录直接耦合。UR RN-F 对 HN-F 而言就是一个普通 CHI RN-F requester。

##### Python 实例化步骤

在 `create_ubcc_system()` 中为每个 node 加入 UR RN-F：

```python
# 在 CHI_ubcc_framework.py 的 create_ubcc_system() 中
def _make_ur_rnf(ruby_system, cfg, node_id):
    """Create a lightweight UR RN-F for UbccExclusive region access."""
    from m5.objects import DMASequencer, RubyPortProxy
    # 1. 创建简化 CPU（通过 TimingSimpleCPU + sequencer 代理）
    sequencer = DMASequencer(version=node_id, ruby_system=ruby_system)
    sequencer.dcache = None   # UR 不维护分层 cache，直接走 L1-like buffer
    # 2. 创建 UR cache（64kB, assoc=4）
    l1_cache = L1Cache(size="64kB", assoc=4,
                       is_read_only=False,
                       data_latency=2, tag_latency=2,
                       clusivity="mostly_excl",
                       tgts_per_mshr=8)
    # 3. 连接：sequencer → L1 → HN-F
    l1_cache.cpu_side = sequencer.mem_side_port
    l1_cache.mem_side = nd['hnf_cntrl'].cpu_side_port
    nd['ur_rnf_seq'] = sequencer
    nd['ur_rnf_cache'] = l1_cache
    return nd
```

**注意**: 上述代码为概念性示例，具体 gem5 SimObject 类名需与 Python config 实际可用类对齐。

##### Q3-0 Smoke Test: UR RN-F 实例化 + ReadOnce/WriteUnique trace

| 字段 | 内容 |
|---|---|
| **ID** | `Q3-0-smoke` |
| **Purpose** | 验证 UR RN-F 可被 `m5.instantiate()` 成功创建，CHI trace 中出现 ReadOnce/WriteUnique 事件 |
| **Harness** | `PY_INJECT`（通过 UR test hook 发起） |
| **Steps** | 1. Python config 中调用 `_make_ur_rnf()` 为 Node0 创建 UR RN-F<br>2. `m5.instantiate()` 不报错，确认 UR controller 在拓扑中就位<br>3. 通过 Python hook 发起 `ReadOnce(ubcc_exclusive_line_pa)` → 检查 CHI trace 有 ReadOnce 事件<br>4. 通过 Python hook 发起 `WriteUnique(ubcc_exclusive_line_pa, 0xDEAD)` → 检查 CHI trace 有 WriteUnique 事件 |
| **Expected** | gem5 正常启动并执行到 trace 输出，无 crash/assert |
| **Negative** | `m5.instantiate()` 报错、trace 中无 ReadOnce/WriteUnique 事件 |

##### UR cache 大小

| 参数 | 值 | 原因 |
|---|---|---|
| Size | 64kB | UBCC metadata 存储需求不大 |
| Assoc | 4 | 低延迟优先 |
| Line size | 64B (与系统一致) | |

##### [TBD-Q3-1] UR RN-F 的 sequencer 如何被 Python/UBCC API 驱动？

当前设计：UR RN-F 不是直接被 CPU workload 驱动的（普通 CPU 不映射 UbccExclusive）。需要一种方式来触发 UR 的 load/store。

选项：
- **A. Python test hook 驱动**: 通过 `pybind` 暴露的 Python API 发起 `readOnce(line_pa)` / `writeUnique(line_pa, data)` 调用
- **B. UBCCController 内部触发**: UBCC 内部逻辑需要读写 UbccExclusive 数据时，通过内部通道发起 UR RN-F 请求
- **C. 专用 sequencer workload**: 给 UR RN-F 分配一个"虚拟 CPU" sequencer，通过 gem5 SE workload 驱动

**推荐先做选项 A**（Python test hook），后续在 EXT 阶段（Q4）迁移到选项 B（UBCC 内部触发）。

#### 4.2.2 EP_RNF 职责净化

当前 EP_RNF 承担了 M6 中的 "local coherent access" 路径（recall 时通过 HN-F 取数据）。Q3 后：

| 职责 | 归属 |
|---|---|
| 响应 HN snoop（作为 sentinel/directory 参与者） | EP_RNF ✅ |
| 透传 home UBCC 的 recall/invalidate 给本地 CHI domain | EP_RNF ✅ |
| 对 UbccExclusive 区域做 coherent load/store | **UR RN-F** ✅（从 EP_RNF 移除） |
| 对 DSM 地址做本地 coherent 访问（recall path） | EP_RNF ✅（保持不变） |

**EP_RNF 的 recall 路径保留原因**:
- 当 home UBCC 发 GlobalRecallOwner 到 Node0 时，Node0 的 EP_RNF 需要通过本地 HN-F 路径取回 dirty data
- 这是 sentinel 的透传职责，不是主动的 load/store
- EP_RNF 不做"我主动想读 UbccExclusive"的行为

**代码修改范围**:
- 不需要从 EP_RNF 移除 recall 路径代码（那是 M6 的核心功能）
- 需要移除的是：如果当前 EP_RNF 有任何针对 UbccExclusive 的主动读写能力（目前代码中可能不存在，需要审查确认）

#### 4.2.3 EP_SNF 职责确认

当前 EP_SNF 职责：
- 接收 HN-F `ReadNoSnp`（带 sideband），调用 EPBackend handleRemoteMiss
- 接收 HN-F writeback/evict 请求，调用 EPBackend handleWriteback/handleEvict

Q3 后职责不变，因为当前 EP_SNF 已经只做 eviction/flushback + miss 拉数据。但需要**审查确认** EP_SNF 没有偷偷做了 recall/invalidate 的主动干涉。

### 4.3 Testcase

#### UBE-TC1: UR RN-F 写 UbccExclusive 并读回

| 字段 | 内容 |
|---|---|
| **ID** | `UBE-TC1` |
| **Purpose** | UR RN-F 通过 HN-F coherent 路径写 UbccExclusive 地址并读回 |
| **Harness** | `PY_INJECT`（通过 UR test hook 发起） |
| **Preconditions** | UR RN-F controller 已实例化，连接到本 node HN-F |
| **Steps** | 1. Python 侧通过 UR test hook 发起对 UbccExclusive offset=0 的 `WriteUnique`，写入 0xCAFE<br>2. 通过 UR test hook 发起对同一地址的 `ReadOnce`<br>3. 比较读取值 |
| **Expected** | 读出 0xCAFE |
| **Negative** | 读出 0、读出随机值、CHI 路由失败 |

#### UBE-TC2: UR 写 UbccExclusive + 另一 Node DSM 隔离

| 字段 | 内容 |
|---|---|
| **ID** | `UBE-TC2` |
| **Purpose** | Node0 的 UbccExclusive 数据不应被 Node1 的 DSM 路径看到（地址隔离） |
| **Harness** | `ARM_SYNC` |
| **Steps** | 1. Python 侧通过 Node0 UR test hook 写 Node0 UbccExclusive 为 0xAA<br>2. Node1 CPU 通过 DSM VA 读 DSM_0 地址（地址空间不同，应不可达）<br>3. 确认 Node1 读到的是 DSM 数据而非 UbccExclusive 数据 |
| **Expected** | UbccExclusive 与 DSM 互不干扰（地址空间隔离） |
| **Negative** | UbccExclusive 数据从 DSM 路径泄露 |

#### UBE-TC3: 两节点 UbccExclusive 互不干扰

| 字段 | 内容 |
|---|---|
| **ID** | `UBE-TC3` |
| **Purpose** | Node0 和 Node1 各自的 UbccExclusive 区域独立，互不影响 |
| **Harness** | `PY_INJECT` |
| **Steps** | 1. Node0 UR 写 0xAA 到 Node0 UbccExclusive offset=0<br>2. Node1 UR 写 0xBB 到 Node1 UbccExclusive offset=0<br>3. 各自读回 |
| **Expected** | Node0 读到 0xAA，Node1 读到 0xBB |
| **Negative** | 跨节点 UbccExclusive 数据污染 |

#### UBE-TC4: UR 路径走 L3 缓存（L3 hit）

| 字段 | 内容 |
|---|---|
| **ID** | `UBE-TC4` |
| **Purpose** | 验证 UR RN-F 对 UbccExclusive 的访问走 HN-F L3 cache 路径（非 bypass） |
| **Harness** | `PY_INJECT` + `LAYER3_OBSERVE` |
| **Steps** | 1. UR 写 UbccExclusive offset=0<br>2. UR 再次读同一 offset=0<br>3. Layer 3 检查 HN-F L3 cache 中该行的状态（应为 Valid/Exclusive） |
| **Expected** | 第二次读走 L3 hit 路径（不触发 CHI miss on interconnect） |
| **Negative** | 每次 UR 访问都走 memory backend |

#### UBE-TC5: UR 路径绕过 EP_RNF（确认职责分离）

| 字段 | 内容 |
|---|---|
| **ID** | `UBE-TC5` |
| **Purpose** | 验证 UR RN-F 的 load/store 不经过 EP_RNF 路径 |
| **Harness** | `PY_INJECT` + `LAYER3_OBSERVE` |
| **Steps** | 1. 清空 EP_RNF snoop counter<br>2. UR RN-F 写 UbccExclusive offset=0<br>3. UR RN-F 读 UbccExclusive offset=0<br>4. 检查 EP_RNF snoop counter 是否为零 |
| **Expected** | EP_RNF snoop counter == 0（UR 操作不触发 EP_RNF） |
| **Negative** | UR 读写触发 EP_RNF 参与 |

### 4.4 验收标准

| # | 验收项 | PASS 条件 |
|---|---|---|
| Q3-1 | UR RN-F controller 实例化 | `m5.instantiate()` 不报错，UR controller 在拓扑中就位 |
| Q3-2 | UBE-TC1 | UR 写读 UbccExclusive：读回写入值 |
| Q3-3 | UBE-TC2 | UbccExclusive 与 DSM 隔离 |
| Q3-4 | UBE-TC3 | 两节点 UbccExclusive 互不干扰 |
| Q3-5 | UBE-TC4 | UR 路径走 L3 cache hit |
| Q3-6 | UBE-TC5 | UR 路径不经过 EP_RNF（职责分离确认） |
| Q3-7 | 职责审查 | 代码审查确认 EP_RNF 无本地 coherent 访问逻辑；EP_SNF 无 recall/invalidate 逻辑 |
| Q3-8 | 回归 | Q2 必达集（TC1~TC6）全部 PASS、M4~M8 自检 0 FAIL、TC1~TC5/T0 全部 PASS |

---

## 5. 阶段 Q4: 外部模块化架构设计

> 优先级: 🔴 P0 | 执行顺序: 最后 | 依赖: Q2 必达集（TC1~TC6）完成（可部分与 Q3 并行）

### 5.1 架构概览

当前 UBCC 是 **单 gem5 进程内** 原型。Q4 的目标是定义外部模块化架构，使 UBCC 各组件可迁移到独立进程/机器。

#### 5.1.1 当前架构 vs 目标架构

```
当前（单 gem5 进程）:
┌──────────── single gem5 process ────────────┐
│  ┌───────┐  ┌───────┐  ┌───────┐            │
│  │ Node0 │  │ Node1 │  │ Node2 │            │
│  │EP/UBCC│  │EP/UBCC│  │EP/UBCC│            │
│  └───┬───┘  └───┬───┘  └───┬───┘            │
│      │           │           │                │
│      └─────── API calls ─────┘                │
│         UBCCController::getInstance()        │
└───────────────────────────────────────────────┘

目标（多进程，可跨机器）:
┌────────┐  网络  ┌────────┐  网络  ┌────────┐
│gem5 N0 │◄──────►│gem5 N1 │◄──────►│gem5 N2 │
│┌──────┐│        │┌──────┐│        │┌──────┐│
││EP/UR ││        ││EP/UR ││        ││EP/UR ││
││UBCC  ││        ││UBCC  ││        ││UBCC  ││
│└──────┘│        │└──────┘│        │└──────┘│
└────────┘        └────────┘        └────────┘
     │                  │                  │
     └──────────────────┼──────────────────┘
                        │
              ┌─────────┴─────────┐
              │  ExternalMsgBus   │
              │  (Local/Shm/TCP)  │
               └───────────────────┘
```

#### 5.1.2 Q4 并行风险项：增强集 TC7~TC10

Q4 仅依赖 Q2 必达集（TC1~TC6）。Q2 增强集（TC7~TC10）作为 Q4 并行风险项：

| 风险 ID | 描述 | 缓解措施 |
|---|---|---|
| R-Q4-1 | TC7（Writeback 后读）在单 gem5 下 eviction 触发困难 | Q4 不受 TC7 阻塞；TC7 可在 Q4 进行期间独立攻坚 |
| R-Q4-2 | TC8（Shared→Upgrade）深度端到端路径未闭环 | Q4 关注的是消息总线抽象，非一致性协议行为细节 |
| R-Q4-3 | TC9（Non-DSM 负例）依赖 Q1 加的 fatal 守卫 | 在 Q1 中提前加入地址守卫，降低 TC9 风险 |
| R-Q4-4 | TC10（并发原子性）依赖真实 CHI timing 并发 | 当前单 gem5 并发有限，Q4 传输延迟引入后可验证真正的跨 gem5 并发 |

**策略**: Q4 可随着 Q2 增强集逐个 PASS 而进行增量式回归验证（每多一个 TC PASS 即并入 Q4 回归集），但不以增强集 PASS 为 Q4 启动的前置条件。

### 5.2 ExternalMsgBus 抽象

#### 5.2.1 接口定义

```cpp
// ExternalMsgBus: 抽象基类
class ExternalMsgBus {
public:
    virtual ~ExternalMsgBus() = default;

    // === 请求类 (requester → home) ===
    virtual bool sendRequest(int targetNode, const OuterRequest &msg) = 0;

    // === Grant 类 (home → requester) ===
    virtual bool sendGrant(int targetNode, const OuterGrant &msg) = 0;

    // === Recall 类 (home → owner) ===
    virtual bool sendRecall(int targetNode, const OuterRecallMsg &msg) = 0;
    virtual bool sendRecallResponse(int targetNode, const OuterRecallResponse &msg) = 0;

    // === Invalidate 类 (home → sharer) ===
    virtual bool sendInvalidate(int targetNode, const OuterInvalidateMsg &msg) = 0;
    virtual bool sendInvalidateAck(int targetNode, const OuterInvalidationAck &msg) = 0;

    // === Writeback/Evict 类 ===
    virtual bool sendWriteback(int targetNode, const OuterWritebackMsg &msg) = 0;
    virtual bool sendEvict(int targetNode, const OuterEvictMsg &msg) = 0;
    virtual bool sendAck(int targetNode, const OuterAckMsg &msg) = 0;

    // === 接收接口 ===
    // 由 EPBackend/UBCCController 轮询或回调
    virtual bool hasPendingMessage() = 0;
    virtual MessageType peekMessageType() = 0;
    virtual bool recvRequest(int &sourceNode, OuterRequest &msg) = 0;
    virtual bool recvGrant(int &sourceNode, OuterGrant &msg) = 0;
    // ... 其他 recv 方法
};
```

#### 5.2.2 消息类型详定义

外层消息类型（protobuf 风格）：

```proto
// 请求: requester → home
message OuterRequest {
  uint64 line_pa = 1;             // 缓存行地址 (home node's PA view)
  OuterReqType req_type = 2;      // GlobalReadShared | GlobalReadUnique
  bool write_intent = 3;          // write intent flag
  int src_node = 4;               // requester node ID
  uint64 epoch = 5;               // per-transaction epoch
  uint64 txn_id = 6;              // transaction ID for matching
}

// Grant: home → requester
message OuterGrant {
  uint64 line_pa = 1;
  OuterGrantType grant_type = 2;  // Shared | Exclusive | Modified
  int home_node = 3;
  uint64 epoch = 4;
  uint64 txn_id = 5;
  // data payload: 64B cache line (optional, only for GrantExclusive/Modified)
  bytes data = 6;
}

// Recall: home → owner
message OuterRecall {
  uint64 line_pa = 1;
  uint64 owner_local_pa = 2;      // PA in owner node's local view
  int owner_node = 3;
  int home_node = 4;
  uint64 epoch = 5;
  bool is_read_request = 6;       // true = recall for read (downgrade)
  bool data_needed = 7;           // true = dirty data must be returned
}

// RecallResponse: owner → home
message OuterRecallResponse {
  uint64 line_pa = 1;
  int owner_node = 2;
  int home_node = 3;
  uint64 epoch = 4;
  bool data_returned = 5;
  // data payload: 64B cache line (if data_returned=true)
  bytes data = 6;
}

// Invalidate: home → sharer (C++ type: OuterInvalidateMsg)
message OuterInvalidateMsg {
  uint64 line_pa = 1;
  uint64 sharer_local_pa = 2;
  int sharer_node = 3;
  int home_node = 4;
  uint64 epoch = 5;
}

// InvalidateAck: sharer → home (C++ type: OuterInvalidationAck)
message OuterInvalidationAck {
  uint64 line_pa = 1;
  int ack_node = 2;
  int home_node = 3;
  uint64 epoch = 4;
  bool success = 5;
}

// Writeback: requester → home (C++ type: OuterWritebackMsg)
message OuterWritebackMsg {
  uint64 line_pa = 1;
  int requester_node = 2;
  int home_node = 3;
  uint64 epoch = 4;
  bool keep_as_clean = 5;
  bytes data = 6;                 // dirty cache line data
}

// Evict: requester → home (C++ type: OuterEvictMsg)
message OuterEvictMsg {
  uint64 line_pa = 1;
  int evicting_node = 2;
  int home_node = 3;
  uint64 epoch = 4;
}

// Ack: home → requester (writeback/evict response) (C++ type: OuterAckMsg)
message OuterAckMsg {
  uint64 line_pa = 1;
  int home_node = 2;
  uint64 epoch = 3;
  bool success = 4;
}
```

##### 消息类型名映射表（文档旧名 → EPBackend.hh 实际名）

为确保 Q4 外部化实现与现有代码对齐，以下为 protobuf 风格的文档旧名到 `EPBackend.hh` 中实际 C++ 结构体名的映射：

| 旧名（文档） | 新名（`EPBackend.hh`） | 说明 |
|---|---|---|
| `OuterInvalidate` | `OuterInvalidateMsg` | 对端无效化请求（M8 已定义） |
| `OuterInvalidateAck` | `OuterInvalidationAck` | 无效化确认（M8 已定义） |
| `OuterWriteback` | `OuterWritebackMsg` | 写回请求（M7 已定义） |
| `OuterEvict` | `OuterEvictMsg` | 驱逐请求（M7 已定义） |
| `OuterAck` | `OuterAckMsg` | 确认响应（M7 已定义） |
| `OuterRecall` | `OuterRecallMsg` | Recall 请求（M6 已定义） |
| `OuterRecallResponse` | —（已一致） | Recall 响应 |
| `OuterRequest` / `OuterGrant` | —（待定义） | Q4 新增类型 |

**设计原则**：Q4 中新增的 `OuterRequest` 和 `OuterGrant` 类型（当前 `EPBackend.hh` 中无直接对应物）也应遵循 `OuterXxxMsg` 命名约定，即最终应为 `OuterRequestMsg` 和 `OuterGrantMsg`。

### 5.3 传输层实现路线图

#### EXT-1: 内部抽象层 (LocalMsgBus)

- **目标**: 在当前单 gem5 进程内，将 `UBCCController::getInstance(node_id)->someMethod()` 直接调用替换为 `ExternalMsgBus` 接口调用
- **实现**: `LocalMsgBus` 实现 `ExternalMsgBus`，内部仍然走静态注册表转发
- **验证**: 所有 M4~M8 自检 + Q2 E2E testcase 回归通过
- **产出**: 
  - `src/mem/ruby/protocol/chi/ep/ExternalMsgBus.hh`（抽象基类）
  - `src/mem/ruby/protocol/chi/ep/LocalMsgBus.hh/.cc`（进程内实现）
  - EPBackend/UBCCController 中所有 `getInstance()` 调用替换为 `_msgBus->send*(...)`

#### EXT-2: 消息序列化 + 传输延迟

- **目标**: 在 `LocalMsgBus` 之上增加消息序列化/反序列化，以及可配置传输延迟
- **实现**: 
  - 引入简单的二进制序列化（或 flatbuffers/protobuf-lite）
  - `ExternalMsgBus` 增加 `setTransportLatency(Tick)` 接口
  - 延迟注入通过 `schedule()` 事件实现（与 gem5 tick 模型一致）
- **验证**: 通过延迟注入测试验证 protocol 时序正确性
  - 评估 `sentinelVisibleTick ≤ grantVisibleTick` 等时序断言在引入延迟后的影响
- **[TBD-Q4-1]**: 引入传输延迟后，M4 的 `sentinelVisibleTick ≤ grantVisibleTick` 时序断言可能需要调整为：`sentinelVisibleTick + transportLatency ≤ grantVisibleTick + transportLatency`。需在 EXT-2 中重新评估该断言。

#### EXT-3: 共享内存传输 (SharedMemMsgBus)

- **目标**: 单机器多 gem5 进程通过共享内存队列通信
- **挑战**: 
  - 多 gem5 实例之间的同步（barrier 如何跨进程？）
  - Node ID 发现与服务注册
  - 共享内存管理（避免 gem5 的物理地址映射与共享内存冲突）
- **验证**: 与 E2E TC1~TC6 相同的 workload，但分布在两个独立的 gem5 进程

#### EXT-4: TCP/gRPC 传输 (TcpMsgBus)

- **目标**: 跨机器 UBCC 通信
- **实现**: 基于 gRPC 或原始 TCP socket
- **额外需求**: 
  - Node 发现/注册服务
  - 连接管理（建连/断连/重连）
  - Flow control / Credit 机制（防止消息丢失/溢出）
- **验证**: 同上

### 5.4 UBCC 间通信协议

#### 5.4.1 通信拓扑

当前（fully connected）：任意 node 的 EP/UBCC 可直接访问任意其他 node 的 UBCC/EP。外部化后拓扑不变：

```
EP(node_i) → UBCC(node_j)  当 node_j 是 DSM line 的 home
UBCC(node_i) → EP(node_j)  当 node_j 是 owner/sharer（recall/invalidate）
```

#### 5.4.2 流控/重试

- **当前单进程**: 无流控需求（同步 API 调用）
- **外部化后**: 需要防止消息丢失/溢出
- **EXT-2 阶段**: 实现 `NackRetry` 基础机制（消息丢失 → Nack → 重发）
- **EXT-4 阶段**: 完整 credit-based 流控

#### 5.4.3 与 gem5 Port 体系的关系

| 维度 | gem5 Port | UBCC Outer Message |
|---|---|---|
| 层级 | 物理地址读写 | 一致性协议消息 |
| 内容 | read/write + data | grant/recall/invalidate/ack + metadata |
| 粒度 | 任意字节 | 缓存行（64B）+ epoch + 节点信息 |
| 时序 | gem5 tick（无引入延迟） | 可引入传输延迟 |
| 目标 | 内存控制器 | 另一节点的 UBCC |

**结论**: 不复用 gem5 Port 体系。使用独立的 ExternalMsgBus 抽象。

### 5.5 与 M9 的合并

| 原 M9 任务 | Q4 对应 | 说明 |
|---|---|---|
| 抽象 outer protocol ABI | EXT-1～EXT-4 | ExternalMsgBus 接口 + 消息类型定义即为 outer protocol ABI |
| metadata capacity model | EXT-2 | 序列化格式即 metadata capacity 的实际表达 |
| 记录多 gem5 / ns-3 时间假设 | EXT-3 | 共享内存/跨进程即多 gem5 准备 |
| ARM_SYNC 端到端工作负载 | → Q2（优先级更高） | 原 M9 中的 E2E 负载已在 Q2 中完成 |

### 5.6 验收标准

| # | 验收项 | PASS 条件 |
|---|---|---|
| Q4-1 | ExternalMsgBus 抽象层定义 | 头文件 `ExternalMsgBus.hh` 完成，所有消息类型有明确定义 |
| Q4-2 | EXT-1 LocalMsgBus 实现 | 所有 getInstance() 调用被替换，Q2 必达集（TC1~TC6）全部 PASS |
| Q4-3 | EXT-2 序列化 + 延迟 | 消息序列化/反序列化正确，加入延迟后 Q2 回归 PASS 或时序断言已更新 |
| Q4-4 | EXT-3 共享内存 | Q2 TC1~TC6 在两独立 gem5 进程间 PASS |
| Q4-5 | EXT-4 TCP/gRPC | 同上（可推迟到 Q4 后独立交付） |
| Q4-6 | 文档化 | outer protocol ABI 定义文档、消息类型规范、与 M9 关联说明 |
| Q4-7 | 回归 | M4~M8 自检 0 FAIL、TC1~TC5 全部 PASS、T0 70 项 PASS、Q2 必达集（TC1~TC6）全部 PASS |

---

## 6. 术语表

| 术语 | 全称 / 说明 |
|---|---|
| UBCC | Unified Bus Cache Coherence |
| EP | External Proxy（外部代理） |
| EP_RNF | External Proxy RNF（外部代理请求节点功能） |
| EP_SNF | External Proxy SNF（外部代理从属节点功能） |
| UR | UBCC Reader（UBCC 本地 coherent 访问节点） |
| UR RN-F | UBCC Reader RNF（UR 的完整 CHI 请求节点控制器） |
| HN-F | Home Node - Fully coherent（CHI 主节点/目录节点） |
| CHI | Coherent Hub Interface（ARM 一致性互连接口） |
| SLICC | Specifying and Implementing Coherence Controllers |
| MESI | Modified / Exclusive / Shared / Invalid（缓存一致性状态） |
| DSM | Distributed Shared Memory（分布式共享内存） |
| Sentinel | 在 HN-F directory 中代表 EP_RNF 的合成目录条目 |
| Sync_Wait | ARM syscall 436 跨节点同步 barrier |
| ExternalMsgBus | UBCC 外部消息总线抽象层 |
| Layer 1 | 应用层验证（ARM workload ldr/str） |
| Layer 3 | 底层只读辅助验证（C++ test hook） |
| UbccExclusive | UBCC 独占地址区域 `[PhyBase+1*SEG, PhyBase+2*SEG)` |
| LocalPrivate | 节点本地私有地址区域 `[PhyBase, PhyBase+1*SEG)` |

---

## 7. TBD 汇总

| ID | 阶段 | 描述 | 优先级 |
|---|---|---|---|
| TBD-Q1-1 | Q1 | ClusterCHI_RNF 中是否需要为 CPU 设置 `phys_pool_id`？ | P0 |
| TBD-Q1-2 | Q1 | DSM range 进 HN-F 后 L3 alloc 策略是否需要单独配置？ | P1 |
| TBD-Q2-1 | Q2 | E2E-TC7 eviction 触发若困难，是否允许以 test-only API 辅助？ | P1 |
| TBD-Q2-2 | Q2 | Non-DSM 地址进入 EP path 时是否有 fatal 守卫？若无，需在 Q1 加入（否则 TC9 无法作为负例 PASS） | P1 |
| TBD-Q3-1 | Q3 | UR RN-F sequencer 如何被驱动？Python hook vs UBCC 内部触发 vs 虚拟 CPU？ | P0 |
| TBD-Q4-1 | Q4 | 引入传输延迟后，`sentinelVisibleTick ≤ grantVisibleTick` 时序断言如何调整？ | P1 |

---

## 8. 附录

### 8.1 相关文件索引

| 文件 | 说明 |
|---|---|
| `plan/03-phase-plan.md` | M3.5~M9 原阶段计划 |
| `plan/15-plan-revision.md` | 修订讨论草案（Qi 阶段前身） |
| `reports/stage-delivery-M3.5-M8.md` | M3.5~M8 总交付报告 |
| `plan/00-terminology.md` | 统一术语定义 |
| `plan/04-test-plan.md` | 测试计划与 TestCase 规范 |
| `gem5/configs/ruby/CHI_ubcc_framework.py` | Ruby/CHI 拓扑构建 |
| `gem5/configs/example/ubcc/basic_framework_se.py` | Phase1 SE 基线（SimpleMemory 模式） |
| `gem5/configs/ruby/CHI_basic_framework_config.py` | NodeAddressMap / NodeConfig 定义 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh` | EPBackend 完整接口 |
| `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh` | UBCC MESI 目录管理 |

### 8.2 当前测试缺口可视化

```
协议路径覆盖 (M4~M8 C++ Self-Test):
  ├── Directory state transitions   ✅ 278 assertions
  ├── Sentinel insert/update/remove ✅
  ├── Sideband plumbing             ✅
  ├── MESI grant decision           ✅
  ├── GlobalRecallOwner             ✅
  ├── Writeback/Evict               ✅
  ├── GlobalInvalidate              ✅
  ├── Epoch stale filtering         ✅
  └── SharerMask maintenance        ✅

端到端覆盖 (Q2 要补齐):
  ├── CPU load → CHI → EP → UBCC → EP → CHI → CPU store  ❌ 待 Q2
  ├── 跨节点读数据一致性                                   ❌ 待 Q2
  ├── 真实 HN-F snoop/data 通路                           ❌ 待 Q1+Q2
  ├── 多节点并发正确性                                    ❌ 待 Q2
  └── 写回/驱逐后数据持久性                               ❌ 待 Q2

基础设施覆盖:
  ├── Sync_Wait barrier            ✅ T0 (70 assertions)
  ├── EP_SNF→HN response 回环       ❌ 待 Q1
  ├── DSM VA Mapping for Ruby/CHI  ❌ 待 Q1
  ├── DSM range in HN-F L3         ❌ 待 Q1
  ├── UbccExclusive 相干访问        ❌ 待 Q3
  └── 外部消息传输                 ❌ 待 Q4
```
