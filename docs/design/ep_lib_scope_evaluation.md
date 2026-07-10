# 独立库范围评估 — 逐单元分析（体量 / 对专有方需求 / 自由度 / 重要性）

> 日期: 2026-07-10 | 目的: 重新评估"独立出来的范围取多少"
> 配套: `ep_static_lib_review.md`（交付形态与总工作量）

---

## 0. 先厘清两个平面

我们的代码分布在**两个进程平面**，性质完全不同，独立化诉求也不同：

- **平面 A — gem5 内 EP 侧**（`gem5/src/mem/ruby/protocol/chi/ep/`，8099 行）：编进 gem5
  binary。这是**受 gem5 固定影响**的部分，是"独立库"讨论的主战场。
- **平面 B — ubio 侧 UBCC home 目录**（`modules/ubiomodule/`，6939 行）：**已经是独立进程**，
  自己编、自己跑，**完全不受 gem5 固定影响**。它天然就是"库外"的。

> **重要认知**：你说"需要独立出来的是 EP+UBAdapter"——EP+UBAdapter 都在平面 A（gem5 内）。
> 而 UBCC（真正的一致性目录逻辑）在平面 B，**本来就独立**。所以真正要决策的，是**平面 A 里
> 抽多少进独立库**。

---

## 1. 平面 A 各候选单元：体量与耦合画像

| 单元 | 行数(.cc+.hh) | gem5/SLICC 耦合密度 | framework/zmq 耦合 | 基类 | 角色 |
|------|--------------|--------------------|-------------------|------|------|
| **EPBackend** | 2890 | 中(20) | 低(4) | SimObject | 地址分类、路由、grant/clear/writeback 编排、跨节点 recall 编排 |
| **UBAdapter** | 1711 | 高(44) | **极高(52)** | SimObject | ZMQ 收发、响应轮询、CHI↔CoherenceMessage 翻译 |
| **EPRNFController** | 2217 | **极高(100)** | 低(2) | AbstractController | 本地 snoop/升级/recall 的 CHI 状态机交互 |
| **EPSNFController** | 665 | 高(38) | 无(0) | AbstractController | ReadNoSnp 服务、CompData 生成、retry 队列 |
| **MetaRNFController** | 576 | 中(19) | 无(0) | AbstractController | metadata 异步读写（backstore 访问路径） |
| NodeAddressMap | 8 | 极低 | 无 | 纯函数 | DSM PA 编解码（纯逻辑） |
| CoherenceMessage.hh | 7 | 极低 | 无 | POD | 跨进程消息结构 |
| M4~M8SelfTest | 25 | — | — | — | 自检，**生产排除** |

**"耦合密度"读法**：数字越高，越依赖 gem5/SLICC 生成类型（`CHI*Msg`、`DataBlock`、
`AbstractController`、`curTick/Cycles/scheduleEvent`、`m_ruby_system`），也就越难和 gem5 解耦、
越吃 proprietary binary 的 ABI 一致性。

关键观察：
- **UBAdapter** 是**双向重耦合**：一头 44 次 gem5/SLICC，一头 52 次 framework/zmq。它是
  gem5 世界和我们 ZMQ 世界的**翻译边界**。⚠️ 注意：framework/zmq 那一头**不是对专有方的负担**
  ——framework 已是不被专有化的外部库（libframework.a 我方自持），所以 UBAdapter 独立的真正
  约束只有 gem5 侧 ABI 这一头。
- **EPRNFController** 耦合 gem5 最深(100)——它直接和 CHI 状态机跳消息。**最难独立、独立价值也
  最低**（它几乎就是 gem5 协议栈的一部分）。
- **EPSNFController / MetaRNFController** 对 framework/zmq **零依赖**，纯 gem5 侧逻辑。
- **NodeAddressMap / CoherenceMessage** 是纯逻辑/POD，几乎零耦合。

---

## 2. 逐单元三维分析（对专有方需求 / 我方自由度 / 重要性）

维度定义：
- **对专有方需求**：把这个单元独立出来，需要 proprietary 方额外配合什么（越少越好）。
- **我方自由度**：独立后，拿到 binary 我们能多大程度自主改这个单元（越高越好）。
- **重要性**：这个单元是不是"我们最想保留修改权"的核心 IP（越高越该独立）。

### 2.1 EPBackend（2890 行）
- **对专有方需求（中）**：需要它的 `params/EPBackend.hh`（生成头）ABI 对齐；不依赖 SLICC 控制器
  类，但依赖 `CHI*Msg` 布局。需对方给生成头 + 保证 CHI 消息布局不变。
- **我方自由度（高）**：grant/clear/writeback/recall 编排逻辑几乎全是我们的业务逻辑（push-grant
  就是改这里附近）。独立后改动空间大。
- **重要性（高）**：这是一致性**编排核心**，未来最可能改（新协议特性、新 grant 策略）。
- → **强烈建议独立**。

### 2.2 UBAdapter（1711 行）
- **对专有方需求（低-中）**：只需 gem5 侧 ABI（params/CHI-msg）对齐。它对 framework/zmq 的依赖
  **不构成对专有方的需求**——因为 **framework 相对 gem5 已经是外部库、不会被专有化**（libframework.a
  由我们自己维护并预编译，见 EP SConscript 里链接 `build/framework/lib/libframework.a`）。所以
  UBAdapter 独立后，framework/zmq 这一头始终在我们控制下，专有方无需关心。
- **我方自由度（高）**：ZMQ 收发、响应轮询、push-grant 接收路径都在这——是我们和外部世界的
  接缝，改动频繁；且外部这一头(framework)完全自主。
- **重要性（高）**：它是**跨进程一致性的传输接缝**，我们所有跨节点行为都过它。
- → **强烈建议独立**。framework/zmq 依赖是"库对外部自有库的链接"，不增加专有方负担
  （原先把这列为"边界最复杂"是过虑了——Q4 澄清后，唯一的对专有方需求就是 gem5 侧 ABI）。

### 2.3 EPSNFController（665 行）
- **对专有方需求（低）**：只依赖 gem5 侧 ABI，**零 framework/zmq**。边界干净。
- **我方自由度（高）**：retry 队列、CompData 生成、deferred grant 都在这，是我们调延迟/时序的地方
  （之前改的 :353、20000cy 定时器都在这）。
- **重要性（中高）**：性能/时序调优的主要着力点。
- → **建议独立**，成本低收益高。

### 2.4 MetaRNFController（576 行）
- **对专有方需求（低）**：gem5 侧 ABI，零 zmq。
- **我方自由度（中）**：metadata/backstore 访问路径，改动频率中等。
- **重要性（中）**：是 backstore 卸载路径的一环，但不是一致性核心。
- → **建议独立**（既然 EP 整块都抽，它一起带走成本极低）。

### 2.5 EPRNFController（2217 行）
- **对专有方需求（高）**：gem5/SLICC 耦合密度 100，最吃 CHI 状态机 ABI。若 proprietary 改了 CHI
  `.sm`，这个单元最先崩。
- **我方自由度（中低）**：它大量是"配合 CHI 状态机跳消息"的胶水，业务自由度不如 EPBackend。
- **重要性（中）**：功能上必需（本地 snoop/升级/recall），但**不是我们最想改的 IP**——它更像
  gem5 协议栈的延伸。
- → **必须独立（否则 EP 不完整无法运行），但它是耦合风险最高的单元**。可考虑用最保守的
  "紧贴 gem5、少改"策略对待。

### 2.6 NodeAddressMap + CoherenceMessage（15 行）
- **对专有方需求（极低）**：纯逻辑/POD，无生成头依赖。
- **我方自由度（极高）**：随便改。
- **重要性（高，但小）**：地址布局和消息格式是**跨进程契约**，改它要两边同步——但代码量极小。
- → **必须随 EP 一起带**（EP 和 ubio 都依赖同一份契约），且应该**把它抽成两平面共享的独立头**
  （现在 EP 和 ubio 各有一份副本，是隐患）。

---

## 3. 三种范围方案对比

### 方案甲：只独立 EPBackend + UBAdapter（你最初的直觉）
- 体量：~4600 行。
- 问题：**跑不起来**。EPBackend/UBAdapter 依赖 EPSNFController/EPRNFController/MetaRNFController
  作为同一批 SimObject 协同工作（它们互相注册、EPBackend 持有 epSnf/epRnf 指针）。只抽两个会留下
  悬空依赖。
- 结论：**技术上不成立**——EP 这几个控制器是一个耦合整体。

### 方案乙：独立整个 EP 目录（5 controllers + AddressMap + Msg，排除 SelfTest）
- 体量：~8060 行（去掉 25 行 SelfTest）。
- 优点：一个自洽的单元；EP 内部互相依赖都在库内解决；边界清晰（库对外只暴露 SimObject 接口 +
  链 framework/zmq）。
- 缺点：把耦合最深的 EPRNFController 也纳入，库整体吃 CHI-msg ABI；库同时依赖 zmq/framework。
- 结论：**推荐**。这是唯一"能独立运行 + 边界自洽"的粒度。

### 方案丙：EP 库 + 把 UBCC(平面 B) 也纳入统一 IP 包
- 体量：EP 8060 + ubio 6939 ≈ 15000 行。
- 说明：UBCC 本就是独立进程，不需要"从 gem5 独立"，但可以和 EP 一起作为"我们的一致性 IP"统一
  版本管理、统一对外文档。
- 结论：**打包/版本管理层面推荐**，但 UBCC 不涉及 gem5 固定问题，技术上不与 EP 库同构（一个是
  gem5 内 .a/.so，一个是独立 binary）。**当作两个交付物、一套 IP 文档**即可。

---

## 4. 建议的独立范围

**技术边界：方案乙（整个 EP 目录作为一个库单元）。** 理由：
1. EP 五个控制器是耦合整体，无法只抽 EPBackend+UBAdapter（方案甲不成立）。
2. 方案乙边界自洽：库对 proprietary 只暴露"SimObject 接口 + 生成头 ABI 对齐 + 链 framework/zmq"。
3. NodeAddressMap/CoherenceMessage 必须随行（跨进程契约），且建议抽成**两平面共享的单一头**，
   消除现有"EP 一份、ubio 一份"的副本隐患。

**IP 管理边界：方案丙。** EP 库 + UBCC 进程作为一套"分布式一致性 IP"统一文档/版本，但物理上是
两个交付物。

**对每个单元的差异化对待**（决定拿到 binary 后各自的改动策略）：

| 单元 | 独立后策略 | 拿到 binary 后自由度 |
|------|-----------|---------------------|
| EPBackend | 核心 IP，主要改这里 | 高（若为 .so 插件形态） |
| UBAdapter | 传输接缝，改 ZMQ/push 行为 | 高 |
| EPSNFController | 时序/性能调优 | 高，且已有 env var 旋钮 |
| MetaRNFController | 随行，少动 | 中 |
| EPRNFController | **紧贴 gem5、尽量少改**（耦合风险最高） | 低——把它当准 gem5 组件 |
| AddressMap/Msg | 跨进程契约，改需两平面同步 | 中（改动要谨慎） |

---

## 5. 交付方向纠正（推翻 ep_static_lib_review.md §3 的假设）

**`ep_static_lib_review.md §3` 假设"对方给我们黑盒 binary，能否 binary 后改取决于对方给不给
插件点"——这个假设是错的。** 真实交付流是反过来的：

> **我们先交付 gem5（含 EP+UBAdapter 源码）→ 他们把它合进 proprietary-sim → 产出 binary。**
> 且他们对 memory / cache-coherence 子系统基本不改动。

在这个方向下，一个关键结论：

- **所有 dlopen 插入点、C ABI、`libep_strategy.so` 加载逻辑，都在我们交付的 gem5 源码里，是我们
  现在就写死的。** 他们编译时原样带进 binary。
- 技术前提**已满足**：`gem5.opt` 已链接 `libdl.so.2`（实测 `ldd`），dlopen/dlsym 运行时支持天生
  存在，无需 gem5 插件框架，也**无需对方做任何配合**。
- 因此"binary 定型后能否热插策略 `.so`" = **我们现在有没有在 EP 里埋好 dlopen 桩** —— 完全我方
  可控，不再是"两天内要问对方"的阻塞项。
- memory：确认使用 gem5 标准 memory（SimpleMemory），**不需要**为对接对方 memory 做注入回调。
  `populateGrantData` 里对 `RubySystem::getPhysMem()` 的调用保持现状即可。

---

## 6. 双层独立性架构（本方案核心）

把独立性拆成两层，各自解决不同诉求：

```
┌──────────── 第一层：编译期模块化（源码交付，他们照编）─────────────┐
│ gem5 (我们交付) → 他们合进 proprietary-sim → binary                │
│                                                                    │
│  EPSNF / EPRNF / MetaRNF ─ CHI 胶水，留 gem5 (SimObject/事件循环)  │
│         │ 调用决策                                                 │
│  EPBackend ──────────────── 策略密集，POD 进出                     │
│         │                                                          │
│  ┌───── 第二层：运行期插件（我方预埋 dlopen 桩）──────┐             │
│  │  EPBackend 内的"策略调用点"先 dlsym 找 libep_strategy.so，      │
│  │  找到就用 .so 的实现，找不到就用【编译进 binary 的内置默认】     │
│  │       │ dlopen/dlsym (C ABI, POD 进出)                          │
│  │  libep_strategy.so（可选覆盖层，binary 后可单独替换）           │
│  │    · 地址映射 · grant 决策 · 数据源策略                         │
│  │    · recall/inval/upgrade 决策 · retry-delay 策略               │
│  └────────────────────────────────────────────────────┘           │
│                                                                    │
│  UBAdapter (传输接缝，留 gem5，走 framework/zmq — framework 不专有化)│
└────────────────────────────────────────────────────────────────────┘
```

**两层的自由度对照：**

| 层 | 改什么 | 何时能改 | 需要对方参与？ |
|----|--------|---------|--------------|
| 第一层：源码 | 任意逻辑（含 CHI 胶水、事件时序） | 合并**前**随便改；合并**后**要重新交付源码 + 他们重编 | 合并后需要（重新集成） |
| 第二层：`.so` 热插 | 纯函数决策层（grant/地址/recall/upgrade/retry-delay 策略） | **binary 定型后，只换 `libep_strategy.so`** | **不需要** |

第二层就是"拿到 binary 后仍有修改空间"的兑现，**钥匙在我方，现在就能埋桩**。

---

## 7. dlopen 桩的形态（关键设计约束）

### 7.1 "可选覆盖层 + 内置默认" 模型
桩不能依赖被热插的逻辑本身，否则 `.so` 缺失时无法启动。正确形态：

```cpp
// EPBackend 内（伪代码）。桩本身编译进 binary，不依赖 .so 是否存在。
// 内置默认实现始终编译进 binary → 即使没有 .so，binary 也能独立跑。
struct EpStrategyVTable {
    void (*decide_grant)(const GrantRequest*, GrantDecision*);
    int  (*home_node)(int node_id, uint64_t pa);
    void (*decide_upgrade)(const UpgradeInfo*, UpgradeDecision*);
    uint64_t (*retry_delay_cycles)(int retryCount, uint64_t lastTick);
    // ... 其它纯函数决策
};

static EpStrategyVTable g_strategy = { /* 内置默认实现的函数指针 */ };

void ep_strategy_init() {
    const char* path = std::getenv("EP_STRATEGY_SO");     // 可选
    if (!path) return;                                    // 无 .so → 用内置默认
    void* h = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!h) { warn("EP_STRATEGY_SO load failed, using built-in"); return; }
    // 逐个 dlsym；任何一个缺失就保留该项的内置默认（部分覆盖也允许）
    if (auto f = dlsym(h, "ep_decide_grant"))   g_strategy.decide_grant = (...)f;
    if (auto f = dlsym(h, "ep_home_node"))       g_strategy.home_node    = (...)f;
    // ...
}
```

要点：
1. **内置默认始终在 binary 里** → 没有 `.so` 也能跑（和 push-grant 的 pull-fallback 同一哲学）。
2. **`.so` 是可选覆盖** → 有则覆盖对应决策，可部分覆盖。
3. **通过 env var `EP_STRATEGY_SO` 指定路径** → 与现有 `EP_RETRY_CYCLES` 等 env 旋钮一致。
4. **`.so` 必须无状态（纯函数）**：所有状态（`_requesterLines`、`_pendingGrantTxns` 等）由
   EPBackend 持有并通过 POD struct 传入传出。这样 `.so` 可安全替换，不破坏 gem5 checkpoint。

### 7.2 C ABI 接口草图（只覆盖"纯函数决策层"）

```c
// libep_strategy.so 导出的 C ABI（POD 进出，零 gem5/SLICC 类型）
extern "C" {
  // 地址映射
  int      ep_home_node(int node_id, uint64_t pa);
  int      ep_home_socket(int node_id, uint64_t pa);
  uint64_t ep_build_dsm_pa(int tgt, int home, uint64_t offset, int socket);
  uint64_t ep_dsm_offset(uint64_t pa);
  int      ep_is_dsm(int node_id, uint64_t pa);

  // grant 决策
  typedef struct { uint64_t linePa; int neededPerm, writeIntent, ingressSocket, homeNode; } GrantRequest;
  typedef struct { int action; int reqType; uint64_t baseEpoch, reqId;
                   int recallOwner, dataSource; } GrantDecision;
  void ep_decide_grant(const GrantRequest*, GrantDecision*);

  // 数据源填充策略（数据读取通过回调，避免依赖 gem5 physMem 类型）
  typedef int (*read_phys_mem_fn)(uint64_t pa, uint8_t* buf, int sz, void* ctx);
  int  ep_populate_grant_data(uint64_t homePa, int dataSource,
                              uint8_t* outBuf, int bufSize,
                              read_phys_mem_fn readFn, void* ctx);

  // recall / invalidation / upgrade 决策
  void ep_decide_recall (const void* in, void* out);
  void ep_decide_inval  (const void* in, void* out);
  void ep_decide_upgrade(const void* in, void* out);

  // retry/backoff 策略（返回下次重试 delay cycles；调度执行留 gem5）
  uint64_t ep_retry_delay_cycles(int retryCount, uint64_t lastAttemptTick);
}
```

---

## 8. 埋桩范围 = "高修改概率 ∩ 纯函数决策"（前几节的交集）

| 逻辑 | 可 dlopen 热插？ | binary 后自由度 |
|------|----------------|----------------|
| 地址映射 / DSM 布局 | ✅（纯函数，零 gem5 类型） | 高 |
| grant 决策（EPBackend） | ✅（POD 进出 + 回调） | 高 |
| 数据源填充策略 | ✅（回调读 physMem） | 高 |
| recall / upgrade / inval 决策 | ✅（POD 进出） | 高 |
| retry-delay 策略 | ✅（返回 delay，调度留 gem5） | 高 |
| **CHI snoop 状态机（EPRNF）** | ❌ 只能外化"决策部分"，发消息/时序留 gem5 | **低（binary 后不可改执行）** |
| **CHI 消息构造 / scheduleEvent 时序** | ❌（有状态、绑事件循环） | 低 |
| **传输 / ZMQ 收发（UBAdapter）** | ❌（绑事件循环；但 framework 我方自持，可编译期改） | 低（但我方可控） |

**结论**：EPBackend 的决策层几乎全部可热插；EPRNF 的 CHI snoop 逻辑虽也高修改概率，但只能外化
"决策"、不能外化"执行"——它的 binary 后自由度天然受限，属于第一层（改源码+重新交付）。

---

## 9. 落地清单与工作量（双层方案）

| 任务 | 工时 | 说明 |
|------|------|------|
| 定义 `ep_strategy.h`（C ABI + POD struct + VTable） | 0.5 天 | 只含纯函数决策接口 |
| EPBackend 埋 dlopen 桩 + 内置默认实现 + `ep_strategy_init()` | 1.5 天 | 把现有决策抽成"内置默认"，走 VTable 间接调用 |
| 抽 NodeAddressMap 决策进 `.so`（最易，先做验证机制） | 0.5 天 | 纯函数，PoC 首选 |
| 抽 grant / recall / upgrade / retry-delay 决策 | 2 天 | 逐个从 EPBackend 迁到 VTable |
| 建 `libep_strategy/` 独立 CMake 项目（编 `.so`，可选覆盖） | 0.5 天 | `-fvisibility=hidden` + 只导出 `ep_*` |
| E2E 回归验证（有 `.so` / 无 `.so` 两种路径都跑 TC 全集） | 1 天 | 确认内置默认 == `.so` 行为 |
| 文档（本文件 + `.so` 开发者指南） | 0.5 天 | |
| **合计** | **~6.5 天** | 不含第一层的 gem5 源码模块化（那部分本就在交付源码里） |

**风险**：主要是把 EPBackend 现有决策"提纯"成无状态纯函数——目前决策和状态更新（`_requesterLines`
等）交织在 `handleRemoteMiss` 里，需要小心把"决定"和"改状态/发消息"分开（决定进 `.so`，改状态/
发消息留 EPBackend）。这也是唯一需要动现有代码结构的地方。

---

## 10. 最终建议

1. **技术边界**：方案乙（整个 EP 目录作为 gem5 内一批 SimObject 源码交付）——这是第一层。
2. **可修改性边界**：在 EPBackend 埋 dlopen 桩，把"高修改概率 ∩ 纯函数决策"做成 `libep_strategy.so`
   可选覆盖层（内置默认兜底）——这是第二层，兑现"binary 后可改"，**我方现在就能埋，不依赖对方**。
3. **不再是阻塞项**：`ep_static_lib_review.md §3/§7` 里"必须问对方交付形态/插件 ABI"的结论，在
   "我方交付 gem5"的正确方向下**作废**。唯一还值得跟对方对齐的是：gem5 版本号一致、他们确实不改
   memory/coherence（已口头确认）、以及 SimObject 参数生成流程保留。
4. **PoC 起步**：先拿 NodeAddressMap（纯函数、零耦合）做 dlopen 桩的最小验证，跑通"换 `.so` 改地址
   映射行为"，机制成立后再铺开到 grant/upgrade 等决策。
