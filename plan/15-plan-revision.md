# UBCC 计划修订草案 v0.1

> 生成日期: 2026-05-30
>
> 基于 `plan/03-phase-plan.md`、`plan/04-test-plan.md`、`reports/stage-delivery-M3.5-M8.md`
> 以及用户审核意见，修订形成四个新增阶段。

---

## 1. 概述

### 1.1 现状回顾

M3.5 ~ M8 七个阶段已全部 PASS，累计 349 项测试断言。但当前测试存在重大结构缺口：

| 现状 | 缺口 |
|---|---|
| 278 项 C++ Self-Test（`MxSelfTest.cc`） | 无端到端应用层 load/store 验证 |
| 70 项 `T0 Sync_Wait` ARM_SYNC 测试 | 协议路径测试全部走 PY_INJECT 或 C++ 内嵌自检 |
| 1 项 ORCH_FLOW 冒烟 | 无任何跨阶段集成测试 |
| `UbccExclusive` 地址段已预留、已隔离 | 无任何 UBCC 对独占区域的读写测试 |
| HN-F 已实例化（带 `HNFCache`） | 未确认 HN-F 是否维护 L3 数据缓存 |
| `EPBackend`/`UBCCController` 均进程内单例 | 无外部网络/消息传输路径 |

### 1.2 修订目标

在已有 M3.5~M8 坚实基础上，补齐以下四个方向的缺失：

| 方向 | 优先级 | 代号 | 目标 |
|---|---|---|---|
| **A. 端到端应用层测试** | 最高，最先做 | **E2E** | 各节点 ARM workload 直接对 DSM 地址做 load/store 并验证 |
| **B. UBCC 独占区域读写** | 一般，中间做 | **UBE** | UBCC 对 `UbccExclusive` 区域读写，与 EP 流量隔离 |
| **C. HN-F L3 Cache 确认** | 较重要，中间做 | **L3** | 确认 HN-F 是否维护 L3 Cache，给出位置或实现 |
| **D. 外部模块化架构设计** | 非常重要，最后做 | **EXT** | UBCC 未来作为外部独立模块，EP/UR 暴露端口 |

### 1.3 与原计划关系

本修订不改变现有 M3.5~M9 的交付物和路线。四个新阶段作为 **M8 之后、M9 之前** 的插入阶段，或与 M9 部分并行。原 M9 保留，但其"ARM_SYNC 端到端工作负载"部分合并入本修订的阶段 E2E。

---

## 2. 阶段 E2E: 端到端应用层测试

> 优先级: **最高** | 执行顺序: **第一** | 依赖: T0 (Sync_Wait) + M5~M8 协议闭环

### 2.1 现状诊断

当前所有协议测试（M4~M8）的测试路径是：

```
Python harness → gem5.opt 启动
  → C++ SelfTest (MxSelfTest.cc) 内部自检
    → 调用 UBCCController/EPBackend API 直接构造场景 + 断言
  → Python 捕获 stdout 判定 PASS/FAIL
```

这条路径的缺陷：

1. **没有经过真实 CPU load/store 通路**：测试通过 `installSentinelForTest()`、`processOuterRequest()` 等 API 直接构造协议状态，并未让 ARM CPU 通过 `ldr`/`str` 指令真正触发 CHI 事务
2. **没有经过真实 HN-F snoop/data 通路**：`GlobalRecallOwner` 的 data 返回是在 `EPBackend` 层模拟的，未经过 HN-F 的 `SnpOnce` 等真实 snoop 动作
3. **没有跨节点数据一致性验证**：测试只检查控制器/目录内部状态字段，不检查"CPU0 写入的值在 CPU1 上能否读到"
4. **没有负载级正确性验证**：所有断言都是状态机/字段/计数器级别的，不是"写入 0xCAFE → 另一端读出 0xCAFE"

### 2.2 测试架构设计

#### 2.2.1 分层验证模型

```
                    ┌─────────────────────────────────────┐
 Layer 1:           │    ARM workload (C/asm)             │
 应用层验证          │    load/store DSM 地址              │
  (新增)            │    打印 read 值到 stdout             │
                    └─────────────┬───────────────────────┘
                                  │ 通过真实 CHI 协议路径
                    ┌─────────────▼───────────────────────┐
 Layer 2:           │    CHI + EP + UBCC 协议栈           │
 协议路径           │    (已有 M4~M8 闭环)                 │
                    └─────────────┬───────────────────────┘
                                  │ test-only observation hook
                    ┌─────────────▼───────────────────────┐
 Layer 3:           │    只读检测 (read-only assertions)   │
 底层辅助验证       │    - HN-F directory 状态             │
  (新增)            │    - UBCC directory 状态             │
                    │    - protocol trace 关键事件         │
                    └─────────────────────────────────────┘
```

**Layer 1** 是主验证层：应用层的 read 值必须与 write 值一致。

**Layer 3** 是辅助验证层：不改变协议行为，只做只读观测，帮助定位 Layer 1 失败时的根因。

#### 2.2.2 测试基础设施需求

```
tests/e2e/
├── workloads/                   # ARM 汇编/C workload
│   ├── e2e_dsm_write_read.c     # 单节点写->读
│   ├── e2e_two_node_pingpong.c  # 双节点 ping-pong
│   ├── e2e_three_node_ring.c    # 三节点环
│   ├── e2e_single_writer.c      # 单写者正确性
│   ├── e2e_multi_sharer.c       # 多 sharer 读一致性
│   ├── e2e_writeback_evict.c    # writeback/evict 后数据正确
│   ├── e2e_concurrent.c         # 并发冲突
│   └── e2e_negative.c           # 负例: non-DSM 地址
├── helpers/                     # 测试辅助
│   ├── dsm_access.h             # DSM load/store 内联宏
│   ├── barrier.h                # Sync_Wait 封装
│   └── e2e_check.h              # 结果验证 + 输出标记
├── test_e2e.py                  # Python 驱动脚本
└── config/                      # gem5 配置变体
    └── e2e_cfg_base.py
```

#### 2.2.3 DSM 访问方式

从 ARM workload 中直接访问 DSM 地址：

```c
// dsm_access.h 示意
// DSM_VA_BASE 在 gem5 config 中已映射 (见 basic_framework_se.py)
// DSM_VA_BASE = MaxAddr - 4*SEG_SIZE，每个 node 的 DSM_i 映射到对应偏移

static volatile uint32_t *dsm_addr(int home_node, uint32_t offset) {
    // 使用全局统一的 DSM VA 窗口
    uint64_t va = DSM_VA_BASE + home_node * SEG_SIZE + offset;
    return (volatile uint32_t *)va;
}

// 读操作
static inline uint32_t dsm_load(int home_node, uint32_t offset) {
    uint32_t val;
    asm volatile("ldr %w0, [%1]" : "=r"(val) : "r"(dsm_addr(home_node, offset)));
    return val;
}

// 写操作
static inline void dsm_store(int home_node, uint32_t offset, uint32_t val) {
    asm volatile("str %w0, [%1]" : : "r"(val), "r"(dsm_addr(home_node, offset)));
}
```

#### 2.2.4 多节点同步

所有多节点测试统一使用已有的 `Sync_Wait(node_mask)`：

```c
// barrier.h 封装
#define SYNC_WAIT(mask)                         \
    do {                                        \
        register long x0 asm("x0") = (mask);   \
        register long x8 asm("x8") = 436;      \
        asm volatile("svc #0"                   \
            : "+r"(x0)                          \
            : "r"(x8)                           \
            : "memory");                        \
    } while (0)
```

#### 2.2.5 输出标记规范

沿用 T0 的标记约定，便于 Python harness 解析：

```
[BEFORE_WRITE] node=<id> line=<home> val=<hex>
[AFTER_WRITE]  node=<id> line=<home> val=<hex>
[BEFORE_READ]  node=<id> line=<home>
[READ_VALUE]   node=<id> line=<home> expected=<hex> actual=<hex> MATCH|MISMATCH
[PHASE_DONE]   node=<id> phase=<name>
```

#### 2.2.6 Layer 3 只读观测

Python harness 在每个 phase 结束后，通过已有的 test helper 读取协议层内部状态（只读，不注入）：

```python
# test_e2e.py 中调用已有 API
backend = system.node0_ep_backend  # 通过 SimObject 引用
ubcc = backend.getUBCC()

# 读取目录状态 (只读)
dir_json = ubcc.inspectUbccDirForTest(line_pa)
dir_state = json.loads(dir_json)

# 验证协议层状态与 Layer 1 结果的一致性
assert dir_state['state'] == expected_mesi_state
assert dir_state['ownerNode'] == expected_owner
```

### 2.3 Testcase 列表

#### 2.3.1 E2E-TC1: 单节点本地 DSM 读写烟测

| 字段 | 内容 |
|---|---|
| **ID** | `E2E-TC1` |
| **Purpose** | 验证 Node0 能对本地 DSM_0 做 store/load 并获得一致值 |
| **Harness** | `ARM_SYNC` + `LAYER3_OBSERVE` |
| **Preconditions** | T0 可用，M5 协议闭环 |
| **Steps** | 1. Node0 对 DSM_0 line 做 `str 0xCAFE`<br>2. Node0 对同一 line 做 `ldr`<br>3. 比较 read 值 |
| **Layer 3** | 读取 home UBCC directory，验证 state 为 `G_M` 或 `G_E` |
| **Expected** | `actual == 0xCAFE`，MATCH |
| **Negative** | 读到旧值、读到 0、读到随机值 |

#### 2.3.2 E2E-TC2: 单节点跨 DSM 读 (remote read)

| 字段 | 内容 |
|---|---|
| **ID** | `E2E-TC2` |
| **Purpose** | Node0 写 DSM_1 (home=Node1)，Node1 读同一 line |
| **Harness** | `ARM_SYNC` + `LAYER3_OBSERVE` |
| **Preconditions** | 双节点，Sync_Wait |
| **Steps** | Phase 1: Node0 写 DSM_1 地址，写入 0x11223344<br>Phase 2: Node1 读同一 DSM_1 地址<br>使用 barrier 串行化 |
| **Layer 3** | Phase 1 后: home UBCC 目录 owner=Node0, state=G_M<br>Phase 2 后: 观测到 GlobalRecallOwner |
| **Expected** | Node1 读到 0x11223344 |
| **Negative** | Node1 读到旧值或默认值 |

#### 2.3.3 E2E-TC3: 双节点 Ping-Pong (owner transfer)

| 字段 | 内容 |
|---|---|
| **ID** | `E2E-TC3` |
| **Purpose** | Node0→Node1 交替写同一 DSM line，验证每次读到最新值 |
| **Harness** | `ARM_SYNC` + `LAYER3_OBSERVE` |
| **Steps** | R1: Node0 写 0xA → Node1 读 → 验证 0xA<br>R2: Node1 写 0xB → Node0 读 → 验证 0xB<br>R3: Node0 写 0xC → Node1 读 → 验证 0xC |
| **Layer 3** | 每轮后检查目录 owner 唯一性 |
| **Expected** | 三轮全部 MATCH，owner 唯一 |
| **Negative** | 双 owner、读到旧值 |

#### 2.3.4 E2E-TC4: 三节点环 (full 3-node)

| 字段 | 内容 |
|---|---|
| **ID** | `E2E-TC4` |
| **Purpose** | Node0→Node1→Node2→Node0 环形 owner transfer |
| **Harness** | `ARM_SYNC` + `LAYER3_OBSERVE` |
| **Steps** | 1. Node0 写 0x1<br>2. Node1 写 0x2<br>3. Node2 写 0x3<br>4. Node0 读，验证 0x3 |
| **Layer 3** | 每步后检查所有三侧目录/requester 状态一致性 |
| **Expected** | 每次 read 结果与最近 write 一致 |
| **Negative** | 数据丢失、状态不一致 |

#### 2.3.5 E2E-TC5: 单写者正确性 (Single Writer)

| 字段 | 内容 |
|---|---|
| **ID** | `E2E-TC5` |
| **Purpose** | 验证同一时刻只有一个有效 writer，并发写不丢数据 |
| **Harness** | `ARM_SYNC` + `LAYER3_OBSERVE` |
| **Steps** | 1. Barrier 同步 Node0/1/2<br>2. 三者同时对同一 DSM line 写不同值<br>3. 最终所有节点读，验证值相同且为其中之一 |
| **Layer 3** | 检查 W->R 闭环，所有节点最终读到一致值 |
| **Expected** | 所有节点最终读到相同值 |
| **Negative** | 数据撕裂、读到不同于任何写入者的值 |

#### 2.3.6 E2E-TC6: 多 Sharer 读一致性

| 字段 | 内容 |
|---|---|
| **ID** | `E2E-TC6` |
| **Purpose** | Node0 写后，Node1 和 Node2 同时读，验证读到正确值 |
| **Harness** | `ARM_SYNC` + `LAYER3_OBSERVE` |
| **Steps** | Phase 1: Node0 写 0xDEADBEEF<br>Phase 2: Node1 和 Node2 同时读 |
| **Layer 3** | 验证 directory sharer mask 包含 Node1 和 Node2<br>验证 state=G_S |
| **Expected** | Node1 和 Node2 都读到 0xDEADBEEF |
| **Negative** | 任一读到旧值 |

#### 2.3.7 E2E-TC7: Writeback 后读

| 字段 | 内容 |
|---|---|
| **ID** | `E2E-TC7` |
| **Purpose** | Node0 写后 writeback，Node1 再读，验证数据不丢失 |
| **Harness** | `ARM_SYNC` + `LAYER3_OBSERVE` |
| **Steps** | 1. Node0 写 0x5566<br>2. 触发 evict/writeback（可能通过后续 store 另一 cache line 触发）<br>3. Node1 读 |
| **Layer 3** | writeback 后 directory dirty=false<br>Node1 read 不触发 recall |
| **Expected** | Node1 读到 0x5566 |

#### 2.3.8 E2E-TC8: 升级失效其他 Sharer

| 字段 | 内容 |
|---|---|
| **ID** | `E2E-TC8` |
| **Purpose** | Node0 Shared + Node1 Shared → Node0 再写，验证 Node1 被 invalidate |
| **Harness** | `ARM_SYNC` + `LAYER3_OBSERVE` |
| **Steps** | Phase 1: Node0 写 0xAAA, Node1 读, Node2 读（都 shared）<br>Phase 2: Node0 再写 0xBBB<br>Phase 3: Node1 读，应得 0xBBB |
| **Layer 3** | Phase 2 后: Node1/Node2 从 sharer mask 移除 |
| **Expected** | Node1 读到 0xBBB |
| **Negative** | Node1 读到旧的 0xAAA |

#### 2.3.9 E2E-TC9: Non-DSM 地址保护

| 字段 | 内容 |
|---|---|
| **ID** | `E2E-TC9` |
| **Purpose** | 验证 non-DSM 地址进入 EP path 被正确拒绝 |
| **Harness** | `ARM_SYNC` (负例) |
| **Steps** | 尝试对 `LocalPrivate` 或 `UbccExclusive` 区域地址走跨节点路径 |
| **Expected** | fatal/assert 触发，或结果拒绝 |
| **Negative** | DSM 语义被错误应用到 non-DSM 地址 |

#### 2.3.10 E2E-TC10: 并发读写原子性

| 字段 | 内容 |
|---|---|
| **ID** | `E2E-TC10` |
| **Purpose** | 验证并发 read 和 write 不会产生中间态脏读 |
| **Harness** | `ARM_SYNC` |
| **Steps** | Node0 持续写一个 counter，Node1 持续读<br>多次迭代验证每次读到的是单调递增 or 合法值 |
| **Expected** | 每次读取都是完整写入值（无撕裂） |

### 2.4 [TBD] 列表

- **[TBD-E2E-1] 是否需要为 L1/L2 数据缓存开启真实 cache 才能触发 evict/writeback？**
  - 当前测试多使用 `AtomicSimpleCPU`，可能绕过 cache。
  - 如需验证 writeback/evict 路径，可能需切换为 `TimingSimpleCPU` 并配置真实 L1/L2 cache。
  - **建议**: E2E-TC1~TC6 先用 AtomicSimpleCPU 验证基本 load/store 正确性；E2E-TC7~TC8 需切换到真实 cache 模式。

- **[TBD-E2E-2] 主配置是否使用 Ruby CHI 协议？**
  - 当前 `basic_framework_se.py` (Phase 1 基线) 使用 SimpleMemory + membus，非 Ruby/CHI。
  - Ruby/CHI 拓扑走 `run_ubcc_ruby_test.py` → `CHI_ubcc_framework.py` → `create_ubcc_system()`。
  - **需要确认**: E2E 测试要跑在哪个配置上？如果跑在 Ruby 配置上，DSM VA mapping 的配置方式需要调整。
  - **建议**: 基于 `run_ubcc_ruby_test.py` 的 Ruby 配置做 E2E 测试，同时把 DSM VA 映射逻辑加入。

- **[TBD-E2E-3] `EP_SNF -> HN` 的真实 CHI 路由是否已通？**
  - 当前 EP_SNF 接收 `ReadNoSnp` 后的应答路径走的是 EPBackend API 调用，不是真实 CHI response message 回 HN。
  - 端到端测试要求 HN 发 `ReadNoSnp` → EP_SNF 收到 → EPBackend 处理 → 最终 data 通过 CHI response 回到 HN → 进入 L1 cache → CPU load 完成。
  - **需要验证**: `Send_ReadNoSnp` 的响应路径是否已闭环。

- **[TBD-E2E-4] Layer 3 只读观测是否允许使用现有 test-only API？**
  - 现有 `inspectUbccDirForTest()`、`inspectDirEntryForTest()` 等 API 是 test-only。
  - 在 E2E 中允许使用这些 API 做只读辅助验证，但不注入状态。
  - **建议**: 允许，但需在 testcase 中标注 Layer 3 结果为辅助性（不影响主 PASS/FAIL 判定）。

---

## 3. 阶段 UBE: UBCC 独占区域读写

> 优先级: **一般** | 执行顺序: **中间** | 依赖: E2E 基本框架可用

### 3.1 背景

根据 `plan/ubcc-detailed-phased-plan-v0.1.md` §2.1:

> `UbccExclusive` 第一版不映射给普通 CPU。

当前 PA 布局中，每个 node 的 `UbccExclusive` 区域位于：

```
[PHY_BASE + 1*SEG, PHY_BASE + 2*SEG)  # 128 MB per node
```

该区域当前通过 `SimpleMemory` 连接到 node membus，但：
1. 没有 HN-F snoop/directory 覆盖
2. 普通 CPU 不能访问（符合设计意图）
3. EP_RNF / EP_SNF 的 `addr_ranges` 中没有 `UbccExclusive` 范围
4. 没有任何 UBCC 组件实际读写这个区域

### 3.2 目标

让 UBCC（作为外部代理）能够对 `UbccExclusive` 区域做读写，且不与普通 EP（DSM）流量干扰：

1. **写入数据**: UBCC 通过某种方式将数据写入 `UbccExclusive` 地址
2. **读出数据**: UBCC 从同一地址读回，验证一致性
3. **隔离性**: `UbccExclusive` 流量不与跨节点 DSM (EP) 流量混合
4. **非 CPU 可见**: 普通 CPU 不能访问 `UbccExclusive`

### 3.3 设计选项

#### 3.3.1 选项 A: UBCC 内部直连 (推荐先做)

```
UBCCController
  └── 直接通过节点本地 membus 访问 UbccExclusive SimpleMemory
      (绕过 HN-F，绕过 CHI)
```

- **优点**: 最简单，无需修改 HN-F；验证 UBCC 能否存储/检索数据
- **缺点**: 不是真实 cache coherence 路径；不能与其他 observer 共享
- **实现**: 给 UBCCController 增加一个 membus port，通过 `SimpleMemory::port` 读写

#### 3.3.2 选项 B: 独立 UBCC Reader RN-F (远期)

```
UBCCController
  └── 专用 RN-F (类似 CPU cluster RN-F)
      └── 经由 HN-F 对 UbccExclusive 做 coherent 读写
          (受 HN-F snoop/directory 管理)
```

- **优点**: 真实 CHI coherent 路径；与其他 coherent agent 可共享
- **缺点**: 需要新增 RN-F 类型、配套 sequencer、tester
- **实现**: 定义 `UBCCReaderRNF`，类似 `ClusterCHI_RNF` 但只含 1 个 sequencer，地址范围限定为 `UbccExclusive`

#### 3.3.3 选项 C: EP_RNF 复用 (折中)

复用已有的 `EP_RNF`，把 `UbccExclusive` 范围加入其 `addr_ranges`。UBCC 通过 `EPBackend` 发起 local coherent access（类似 M6 的 recall 路径），让 HN-F 执行对 `UbccExclusive` 的 coherent 操作。

- **优点**: 复用已有组件，路径与 DSM 对称
- **缺点**: EP_RNF 语义上属于 "外部代理 sentinel"；混入 `UbccExclusive` 可能混淆协议语义

### 3.4 Testcase 列表

#### 3.4.1 UBE-TC1: UBCC 直连写读

| 字段 | 内容 |
|---|---|
| **ID** | `UBE-TC1` |
| **Purpose** | UBCC 直接对 UbccExclusive 地址写数据并读回 |
| **Harness** | `PY_INJECT` |
| **Steps** | 1. Python 侧通过 UBCCController API 写 0xCAFE 到 UbccExclusive PA<br>2. 通过同一 API 读出<br>3. 比较 |
| **Expected** | 读出 0xCAFE |
| **Negative** | 读出 0、读出随机值 |

#### 3.4.2 UBE-TC2: UBCC 写 + 另一 Node 读隔离

| 字段 | 内容 |
|---|---|
| **ID** | `UBE-TC2` |
| **Purpose** | Node0 的 UbccExclusive 数据不应被 Node1 的 DSM 路径看到 |
| **Steps** | 1. Node0 UBCC 写 UbccExclusive<br>2. Node1 通过 DSM_0 read 尝试读取<br>3. 确认地址绕回或拒绝 |
| **Expected** | UbccExclusive 与 DSM 隔离，跨节点不可见 |

#### 3.4.3 UBE-TC3: 两节点 UbccExclusive 互不干扰

| 字段 | 内容 |
|---|---|
| **ID** | `UBE-TC3` |
| **Purpose** | Node0 和 Node1 各自的 UbccExclusive 区域独立 |
| **Steps** | 1. Node0 UBCC 写 0xAA 到自己 UbccExclusive<br>2. Node1 UBCC 写 0xBB 到自己 UbccExclusive<br>3. 各自读回 |
| **Expected** | Node0 读到 0xAA, Node1 读到 0xBB |

### 3.5 [TBD] 列表

- **[TBD-UBE-1] UBE 阶段采用哪个选项？**
  - 选项 A (直连) 最快落地但局限大。
  - 选项 B (独立 RN-F) 最真实但工程量大。
  - 选项 C (EP_RNF 复用) 折中但有语义混淆风险。
  - **建议**: 先做选项 A (1~2 个 testcase) 验证 UBCC 能否与 `UbccExclusive` 存储交互，再做选项 C 做 coherent 访问验证。

- **[TBD-UBE-2] `UbccExclusive` 是否需要 HN-F directory 管理？**
  - 如果走选项 C，则 HN-F 需要对 `UbccExclusive` 范围建立 directory。
  - 当前 HN-F 的 `addr_ranges` 只包含 `local_private` 和 `ubcc_exclusive`（见 `_make_hnf` 第 82 行），说明 HN-F 理论上已覆盖该范围。
  - 但是否需要 sentinel registration 之类机制来管理 `UbccExclusive` 的 owner/sharer？
  - **建议**: 如果只是 UBCC 独占访问，不需要 multi-agent coherence，则不需要 directory。

- **[TBD-UBE-3] 是否需要独立 UBCC Reader RN-F 实体？**
  - 如果未来 UBCC 需要对 `UbccExclusive` 做 coherent 的 load/store（与其他有 cache 的 agent 交互），则需要真实 RN-F。
  - 如果只是 UBCC 独占、无共享需求，则不需要。
  - **建议**: 本阶段先不实现独立 RN-F，在 EXT 阶段一并考虑。

---

## 4. 阶段 L3: HN-F L3 Cache

> 优先级: **较重要** | 执行顺序: **中间** | 依赖: 无强依赖

### 4.1 现状调查

#### 4.1.1 HN-F 在 UBCC 框架中的实例化

在 `CHI_ubcc_framework.py` 中：

```python
class HNFCache(RubyCache):
    dataAccessLatency = 10
    tagAccessLatency = 2
    size = getattr(options, "l3_size", "256kB")
    assoc = getattr(options, "l3_assoc", 16)

def _make_hnf(ruby_system, addr_ranges, llcache_type, node_id):
    hnf_cache = llcache_type()      # → HNFCache()
    hnf_cntrl = chi_defs.CHI_HNFController(
        ruby_system, hnf_cache, NULL, addr_ranges)
    ...
```

这里 `hnf_cache` 是传入 `CHI_HNFController` 的 `cache` 参数。在 `CHI-cache.sm` 中：

```slang
CacheMemory * cache;           // 第 47 行: "Cache for storing local lines"
bool is_HN;                    // 第 120 行: "Set when this is used as the home node"
bool alloc_on_readshared;      // 第 154 行: 控制对不同请求类型分配缓存行
bool alloc_on_readunique;
bool alloc_on_readonce;
bool alloc_on_writeback;
...
bool dealloc_on_unique;        // 第 162 行: dealloc 控制 (用于 inclusivity)
bool dealloc_on_shared;
bool dealloc_backinv_unique;
bool dealloc_backinv_shared;
```

#### 4.1.2 结论

**HN-F 确实维护了一个 Cache（L3 Cache）。** 这个 cache 通过 `CacheMemory` 实现，有完整的 tag + data 存储，受 `alloc_on_*` 和 `dealloc_on_*` 参数控制 inclusivity 策略。

在 UBCC 框架中：
- `HNFCache` 被实例化为 256kB / 16-way set-associative
- 作为 `CHI_HNFController` 的 `cache` 参数传入
- HN-F 对所有流经它的地址范围（`local_private` + `ubcc_exclusive`）可以做缓存行分配

#### 4.1.3 关键问题

当前的 `alloc_on_*` 默认值，以及 HN-F 目录与 L3 Cache 的关系，需要进一步确认：

1. **L3 是否对 `UbccExclusive` 范围做缓存分配？** HN-F 的 `addr_ranges` 包含 `ubcc_exclusive_range`，所以理论上可以分配。但 `alloc_on_*` 的默认值决定是否真的分配。
2. **L3 是否缓存 DSM 地址的数据？** 当前 HN-F 的 `addr_ranges` 不包含 DSM 范围（DSM 走 `EP_SNF`），所以 LMS 数据不会进 L3。
3. **HN-F directory 与 L3 Cache 的关系**: 在 CHI 协议中，directory（snoop filter）与 L3 Cache 是同一物理结构的不同组成部分，还是分离的？
4. **`dealloc_on_*` 默认值**: 决定缓存是 inclusive 还是 exclusive 还是 NINE（non-inclusive non-exclusive）。

### 4.2 需要确认的事项

| # | 问题 | 建议验证方式 |
|---|---|---|
| L3-1 | HN-F 对 `UbccExclusive` 的 alloc 行为 | 检查 `alloc_on_*` 默认值 + trace log |
| L3-2 | DSM 地址是否会被 HN-F L3 缓存 | 检查 HN-F `addr_ranges` 的 DSM 部分 + `is_HN=true` 时的路由行为 |
| L3-3 | directory 与 L3 Cache 的结构关系 | 阅读 `CHI-cache-actions.sm` 中 directory 操作与 cache 操作的关系 |
| L3-4 | inclusivity 策略 (inclusive/exclusive/NINE) | 检查 `dealloc_on_unique/dealloc_on_shared` 默认值 |
| L3-5 | UBCC 框架中的 `alloc_on_*` 是否被显式设置 | 搜索 `CHI_ubcc_framework.py`、`CHI_config.py` 中的 alloc 参数 |

### 4.3 [TBD] 列表

- **[TBD-L3-1] 需要确认 HN-F L3 Cache 的 inclusivity 策略。** 当前 UBCC 框架代码未见显式设置 `alloc_on_*` / `dealloc_on_*` 参数。需要确认 CHI 协议的默认值是 inclusive 还是 exclusive，以及这对 UBCC 协议正确性的影响。

- **[TBD-L3-2] `UbccExclusive` 区域是否需要 L3 Cache 覆盖？** 如果 `UbccExclusive` 只是 UBCC 的 metadata/data 存储区（不要求 snoop filter），可能不需要 L3 缓存。但需显式确认。

- **[TBD-L3-3] 是否需要单独的 L3 验证 testcase？** 如果 L3 只是确认现状，可能不需要独立测试；如果需要调整 alloc 策略，则需要。

---

## 5. 阶段 EXT: 外部模块化架构设计

> 优先级: **非常重要** | 执行顺序: **最后** | 依赖: E2E 基本框架可用 + UBE 验证完成

### 5.1 架构概览

当前 UBCC 实现是 **单 gem5 进程内** 的原型：

```
┌─────────────── single gem5 process ───────────────┐
│                                                    │
│  ┌───────┐  ┌───────┐  ┌───────┐                 │
│  │ Node0 │  │ Node1 │  │ Node2 │                 │
│  │ EP/UB │  │ EP/UB │  │ EP/UB │                 │
│  └───┬───┘  └───┬───┘  └───┬───┘                 │
│      │           │           │                     │
│      └───────────┼───────────┘                     │
│                  │ 静态注册表 (static map)           │
│           UBCCController::getInstance()            │
│           EPBackend::getBackendInstance()           │
└────────────────────────────────────────────────────┘
```

远期目标是让 UBCC 每个 node 的 EP/UBCC 部分成为 **独立外部模块**，通过真实网络/消息传输通信：

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  gem5 Node0  │    │  gem5 Node1  │    │  gem5 Node2  │
│  ┌──────┐    │    │  ┌──────┐    │    │  ┌──────┐    │
│  │ EP   │◄───┼────┼──┤ UBCC │    │    │  │ EP   │    │
│  │ UBCC │    │    │  │      │    │    │  │      │    │
│  └──────┘    │    │  └──────┘    │    │  └──────┘    │
└──────┬───────┘    └──────┬───────┘    └──────┬───────┘
       │                   │                   │
       └───────────────────┼───────────────────┘
                           │
                    ┌──────┴──────┐
                    │  外部网络    │
                    │ (gRPC/TCP/  │
                    │  shared-mem)│
                    └─────────────┘
```

### 5.2 EP/UR 端口设计

#### 5.2.1 当前内部接口

当前 EPBackend 对外暴露的核心接口：

```cpp
// 请求者侧: HN → EP_SNF → EPBackend
int handleRemoteMiss(uint64_t line_pa, int neededPerm, bool writeIntent,
                      int& outHomeNode);
OuterGrantType handleGrant(uint64_t line_pa, OuterGrantType grant, int homeNode);

// 所有者侧: Home UBCC → EPBackend (recall/invalidate)
bool handleRecallRequest(const OuterRecallMsg &recallMsg);
bool handleInvalidationRequest(const OuterInvalidateMsg &invMsg);

// 写回/驱逐
bool handleWriteback(uint64_t line_pa, bool keepAsClean);
bool handleEvict(uint64_t line_pa);
```

#### 5.2.2 外部端口抽象建议

将这些内部接口替换为 **消息端口**：

```
┌─────────────────────────────────────────────────┐
│            EPBackend 内部                        │
│                                                 │
│  ┌──────────┐   ┌──────────────┐   ┌──────────┐│
│  │ HN 接口  │──►│ 事务管理器    │──►│ 出站端口  ││
│  │ (CHI)    │   │ (TX_WAIT_*)  │   │ (OutPort) ││
│  └──────────┘   └──────────────┘   └─────┬────┘│
│                                          │     │
└──────────────────────────────────────────┼─────┘
                                           │
                                ┌──────────▼──────┐
                                │ 外部消息传输层   │
                                │ (ExternalMsgBus)│
                                └──────────┬──────┘
                                           │
┌──────────────────────────────────────────┼─────┐
│            EPBackend 内部                 │     │
│  ┌──────────┐   ┌──────────────┐   ┌─────▼────┐│
│  │ HN 接口  │◄──┤ 事务管理器    │◄──┤ 入站端口  ││
│  │ (CHI)    │   │ (TX_WAIT_*)  │   │ (InPort)  ││
│  └──────────┘   └──────────────┘   └──────────┘│
└─────────────────────────────────────────────────┘
```

**设计原则**:
1. EPBackend 内部事务状态不变（`TX_IDLE` / `TX_WAIT_HOME` / `TX_WAIT_HN` / `TX_WAIT_REMOTE` / `TX_WAIT_FINISH`）
2. 将 `UBCCController::getInstance(node_id)->someMethod()` 调用替换为出站端口 `send(msg)` → 入站端口 `recv(msg)`
3. 消息序列化格式支持未来跨进程/跨机器传输

#### 5.2.3 端口消息类型

```proto
// 示意: 外部端口消息类型

// 请求类 (requester → home)
message OuterRequest {
  uint64 line_pa = 1;
  OuterReqType req_type = 2;   // GlobalReadShared / GlobalReadUnique
  bool write_intent = 3;
  int src_node = 4;
  uint64 epoch = 5;
}

// Grant 类 (home → requester)
message OuterGrant {
  uint64 line_pa = 1;
  OuterGrantType grant_type = 2;  // Shared / Exclusive / Modified
  int home_node = 3;
  uint64 epoch = 4;
  // 时序断言字段
  Tick grant_visible_tick = 5;
  Tick sentinel_visible_tick = 6;
}

// Recall 类 (home → owner)
message OuterRecall {
  uint64 line_pa = 1;
  uint64 owner_local_pa = 2;
  int owner_node = 3;
  int home_node = 4;
  uint64 epoch = 5;
  bool is_read_request = 6;
  bool data_needed = 7;
}

// Recall Response 类 (owner → home)
message OuterRecallResponse {
  uint64 line_pa = 1;
  int owner_node = 2;
  int home_node = 3;
  uint64 epoch = 4;
  bool data_returned = 5;
  bool ack_received = 6;
}

// Invalidate 类 (home → sharer)
message OuterInvalidate {
  uint64 line_pa = 1;
  uint64 sharer_local_pa = 2;
  int sharer_node = 3;
  int home_node = 4;
  uint64 epoch = 5;
}

// Invalidate Ack 类 (sharer → home)
message OuterInvalidateAck {
  uint64 line_pa = 1;
  int ack_node = 2;
  int home_node = 3;
  uint64 epoch = 4;
  bool success = 5;
}

// Writeback / Evict 类
message OuterWriteback { ... }
message OuterEvict { ... }
message OuterAck { ... }
```

### 5.3 UBCC 间通信协议

#### 5.3.1 通信拓扑

当前是 fully connected (任意 node 可访问任意其他 node 的 UBCC)。外部化后拓扑不变：

```
EP(node_i) → UBCC(node_j)  当 node_j 是 home
UBCC(node_i) → EP(node_j)  当 node_j 是 owner/sharer
```

#### 5.3.2 消息层抽象

```cpp
// 外部消息总线接口 (抽象基类)
class ExternalMsgBus {
public:
    // 发送消息到目标 node
    virtual bool send(int target_node, const OuterRequest &msg) = 0;
    virtual bool send(int target_node, const OuterGrant &msg) = 0;
    virtual bool send(int target_node, const OuterRecall &msg) = 0;
    // ... 其他消息类型

    // 接收消息 (会被 EPBackend/UBCC 轮询或回调)
    virtual bool recv(int &source_node, OuterRequest &msg) = 0;
    // ...
};

// 单 gem5 进程内实现 (当前架构的抽象包装)
class LocalMsgBus : public ExternalMsgBus {
    // 直接通过 UBCCController::getInstance() / EPBackend::getBackendInstance() 转发
};

// 共享内存实现 (单机器多进程)
class SharedMemMsgBus : public ExternalMsgBus { ... };

// TCP/gRPC 实现 (多机器)
class TcpMsgBus : public ExternalMsgBus { ... };
```

### 5.4 与 AbstractMemory / gem5 Port 对比

当前 gem5 的 `AbstractMemory` + `RequestPort`/`ResponsePort` 体系是 gem5 内部数据路径。UBCC 协议层消息与 gem5 port 有本质区别：

| 维度 | gem5 Port | UBCC Outer Message |
|---|---|---|
| 层级 | 物理地址读写 | 一致性协议消息 |
| 内容 | read/write + data | grant/recall/invalidate/ack |
| 粒度 | 字节/缓存行 | 缓存行 + 元数据 |
| 时序 | 实时 | 可引入传输延迟 |
| 目标 | 内存控制器 | 另一节点的 UBCC |

**结论**: 不应复用 gem5 Port 体系做 UBCC 间通信。使用独立的消息总线抽象。

### 5.5 路线图

#### 5.5.1 阶段 EXT-1: 内部抽象层 (gem5 内)

- **目标**: 在当前单 gem5 进程内，将 `UBCCController::getInstance()` 直接调用替换为 `ExternalMsgBus` 接口调用
- **实现**: 实现 `LocalMsgBus`，内部转发到静态注册表
- **验证**: 所有 M4~M8 + E2E 测试回归通过

#### 5.5.2 阶段 EXT-2: 消息序列化 + 传输延迟

- **目标**: 在 `LocalMsgBus` 之上增加消息序列化/反序列化，以及可配置传输延迟
- **验证**: 通过延迟注入测试验证 protocol 的时序正确性（超时/重传暂不实现）
- **注意**: 延迟可能打破当前 C++ Self-Test 中的 `sentinelVisibleTick ≤ grantVisibleTick` 等时序断言；需评估影响

#### 5.5.3 阶段 EXT-3: 共享内存传输

- **目标**: 单机器多 gem5 进程通过共享内存队列通信
- **挑战**: 多 gem5 实例之间的地址空间映射、node ID 发现、同步 (barrier)
- **验证**: 与 E2E testcase 相同的 workload，但分布在不同 gem5 进程

#### 5.5.4 阶段 EXT-4: TCP/gRPC 传输

- **目标**: 跨机器 UBCC 通信
- **验证**: 同上

### 5.6 [TBD] 列表

- **[TBD-EXT-1] 消息总线应支持哪些传输层？**
  - 短期: LocalMsgBus（当前单进程）+ SharedMemMsgBus（单机器多进程）
  - 长期: TcpMsgBus（多机器）
  - **建议**: 分阶段实现，先 LocalMsgBus 验证抽象层正确性。

- **[TBD-EXT-2] 外部端口是否需要支持异步/回调模型？**
  - 当前 EPBackend 的事务管理（`TX_WAIT_*`）已隐含异步语义。
  - `ExternalMsgBus::recv()` 可以设计为阻塞或回调。
  - **建议**: 先使用 gem5 的 tick-based 轮询（`wakeup()`），与现有模型一致。

- **[TBD-EXT-3] 是否需要独立 UR (UBCC Reader) 组件？**
  - 原计划 (§2.1): "第一版不实现独立 `UR_i`"
  - 当前所有 local coherent access 通过 `EP_RNF` 完成。
  - **建议**: 在 EXT 阶段重新评估 `UR_i` 的必要性。如果外部 UBCC 需要向本地 CHI domain 发起 load/store，可能需要独立 RN-F。

- **[TBD-EXT-4] UBCC 间通信是否需要 Flow Control / Credit？**
  - 当前单进程内无流控需求。
  - 外部化后需要防止消息丢失/溢出。
  - **建议**: 在 EXT-2 阶段至少实现 `NackRetry` 机制；完整 credit 系统推迟。

- **[TBD-EXT-5] 与 M9 的 Outer Protocol ABI 关系？**
  - M9 计划做 "抽象 outer protocol ABI"，与 EXT 阶段的消息端口设计高度重叠。
  - **建议**: EXT 阶段直接产出 outer protocol ABI 定义，成为 M9 的前置工作。

---

## 6. 执行顺序与依赖

```
                    M3.5 ──► T0 ──► M4 ──► M5 ──► M6 ──► M7 ──► M8
                                                                 │
                                                                 ▼
                    ┌────────────────────────────────────────────┐
                    │              阶段 E2E (最先做)              │
                    │  ┌───────────────────────────────────────┐ │
                    │  │ E2E-TC1: 单节点本地 DSM 读写          │ │
                    │  │ E2E-TC2: 双节点 remote read           │ │
                    │  │ E2E-TC3: 双节点 ping-pong             │ │
                    │  │ E2E-TC4: 三节点环                    │ │
                    │  │ E2E-TC5: 单写者正确性                 │ │
                    │  │ E2E-TC6: 多 sharer 读一致性           │ │
                    │  │ E2E-TC7: Writeback 后读               │ │
                    │  │ E2E-TC8: 升级失效其他 sharer          │ │
                    │  │ E2E-TC9: Non-DSM 保护                 │ │
                    │  │ E2E-TC10: 并发读写原子性              │ │
                    │  └───────────────────────────────────────┘ │
                    └────────────────────┬───────────────────────┘
                                         │
                    ┌────────────────────┴───────────────────────┐
                    │              阶段 UBE (中间做)              │
                    │  ┌───────────────────────────────────────┐ │
                    │  │ UBE-TC1: UBCC 直连写读               │ │
                    │  │ UBE-TC2: 跨节点隔离验证               │ │
                    │  │ UBE-TC3: 两节点互不干扰               │ │
                    │  └───────────────────────────────────────┘ │
                    └────────────────────┬───────────────────────┘
                                         │
                    ┌────────────────────┴───────────────────────┐
                    │              阶段 L3 (中间做)               │
                    │  ┌───────────────────────────────────────┐ │
                    │  │ L3-1~L3-5: 现状确认 + 文档化          │ │
                    │  └───────────────────────────────────────┘ │
                    └────────────────────┬───────────────────────┘
                                         │
                    ┌────────────────────┴───────────────────────┐
                    │              阶段 EXT (最后做)              │
                    │  ┌───────────────────────────────────────┐ │
                    │  │ EXT-1: 内部抽象层 (LocalMsgBus)       │ │
                    │  │ EXT-2: 序列化 + 传输延迟              │ │
                    │  │ EXT-3: 共享内存传输 (多进程)          │ │
                    │  │ EXT-4: TCP/gRPC 传输 (多机)           │ │
                    │  └───────────────────────────────────────┘ │
                    └────────────────────┬───────────────────────┘
                                         │
                                         ▼
                                   原 M9 保留
                         (metadata model + multi-gem5 cleanup)
```

**执行原则**:

1. **E2E 最优先**：在所有其他阶段之前完成。因为它是唯一验证"协议在生产负载下真实 work"的手段。
2. **UBE 和 L3 可并行**：两者无相互依赖，且都与 E2E 无阻塞依赖。
3. **EXT 在最后**：依赖 E2E 验证协议正确性，依赖 UBE 确认 `UbccExclusive` 接口语义。
4. **与 M9 的关系**：M9 的 outer protocol ABI + multi-gem5 准备与 EXT 高度重叠。EXT 完成后，M9 只需做 cleanup + metadata capacity model。

---

## 7. 附录

### 7.1 当前测试缺口可视化

```
协议路径覆盖 (M4~M8 已做):
  ├── Directory state transitions   ✅ C++ SelfTest (278 assertions)
  ├── Sentinel insert/update/remove ✅
  ├── Sideband plumbing             ✅
  ├── MESI grant decision           ✅
  ├── GlobalRecallOwner             ✅
  ├── Writeback/Evict               ✅
  ├── GlobalInvalidate              ✅
  ├── Epoch stale filtering         ✅
  └── SharerMask maintenance        ✅

端到端覆盖 (缺失):
  ├── CPU load → CHI → EP → UBCC → EP → CHI → CPU store  ❌ 无
  ├── 跨节点读数据一致性                                   ❌ 无
  ├── 真实 HN-F snoop/data 通路                           ❌ 无
  ├── 多节点并发正确性                                    ❌ 无
  └── 写回/驱逐后数据持久性                               ❌ 无

基础设施覆盖:
  ├── Sync_Wait barrier            ✅ T0 (70 assertions)
  ├── 地址分类/隔离                ✅ TC1~TC5
  ├── UbccExclusive 实际读写        ❌ 无
  └── 外部消息传输                 ❌ 无 (全进程内)
```

### 7.2 关键假设

1. **Ruby CHI 配置下的 DSM VA 映射**：假设可以在 `run_ubcc_ruby_test.py` 的配置中加入与 `basic_framework_se.py` 相同的 VA→PA 映射逻辑。
2. **EP_SNF → HN 的 CHI Response 路径**：假设 `Send_ReadNoSnp` 发出去后，data 能通过 CHI response 通道回到 HN，并最终到达 requester 的 L1 cache。
3. **AtomicSimpleCPU 的 L1 行为**：假设 AtomicSimpleCPU 通过 Ruby sequencer 发出的 load/store 会触发完整的 CHI 事务（包括 `ReadNoSnp`、`ReadShared`、`ReadUnique` 等）。
4. **`force_grant_m` debug flag**：假设默认已关闭，`Shared` 路径已走默认 `GlobalReadShared` → `GlobalGrantShared` 路径。

### 7.3 待用户确认的问题汇总

| # | 阶段 | 问题 | 优先级 |
|---|---|---|---|
| 1 | E2E | 端到端测试跑在 Ruby/CHI 配置还是 SimpleMemory 配置？ | **P0** (阻塞) |
| 2 | E2E | EP_SNF → HN 的 CHI response 回环是否已验证通？ | **P0** (阻塞) |
| 3 | E2E | 是否需要切换到 TimingSimpleCPU + 真实 L1/L2 cache？ | P1 |
| 4 | UBE | UBE 采用选项 A (直连) / B (独立 RN-F) / C (EP_RNF 复用)？ | P1 |
| 5 | UBE | UbccExclusive 是否需要 HN-F directory 管理？ | P2 |
| 6 | L3 | 是否需要对 L3 alloc/dealloc 策略做显式配置？ | P1 |
| 7 | EXT | 消息总线先做 LocalMsgBus (进程内) 还是直接做 SharedMemMsgBus？ | P1 |
| 8 | EXT | 是否需要独立 UR (UBCC Reader) 组件？ | P2 |
| 9 | EXT | EXT 阶段是否替代还是子集于 M9？ | P1 |
| 10 | ALL | 四个阶段的优先级排序是否认可（E2E > UBE ≈ L3 > EXT）？ | **P0** (方向确认) |
