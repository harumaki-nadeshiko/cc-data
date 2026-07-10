# EP+UBAdapter 库化 & 参数外置方案 — 重新审视与工作量分析

> 日期: 2026-07-10 | 审视对象: `ep_static_lib_plan.md`、`p2_8node_externalize_plan.md`
> 场景: 两天后 gem5/ submodule 版本固定，与公司私有仿真器框架合并。
>
> ⚠️ **本文 §3 关于"交付形态取决于对方"的分析已被推翻。** 真实交付流是
> **我们交付 gem5(含 EP) → 他们合入 proprietary-sim**，dlopen 桩由我方预埋、不依赖对方。
> 最新且正确的方案见 **`ep_lib_scope_evaluation.md §5–§10（双层架构）`**。本文的依赖面分析
> (§1)、build-generated 头 ABI 风险(§2)、工作量拆解(§5) 仍然有效，但结论以 scope_evaluation 为准。

---

## 0. TL;DR（结论先行）

1. **参数外置：已基本完成，方向正确。** `chi_params.json`（CHI_config.py 加载，改 JSON 不重编）
   + C++ retry 参数用 **env var**（`EP_RETRY_CYCLES`/`EPRN_*`/`UB_WAIT_CAP`）。env var 方案
   **比原计划的 SimObject Param 更好**——env var 在 proprietary binary 里天然生效、零重编。
   剩一个 `_recallTimeout` 硬编码（UBCCController.hh:667），但它在 ubio（外部模块），改 CLI 即可，
   **不受 gem5 固定影响**。→ 参数外置这块基本收尾，工作量 ~0.3 天扫尾。

2. **静态库方案：方向可行，但原方案低估了关键耦合，且"静态库"未必是拿到 binary 后能融合的形态。**
   原方案的核心论断"只要头文件、符号 link 时解析"**对普通 gem5 API 成立**，但**漏了三类
   build-generated 头文件**（`params/*.hh`、SLICC 的 `CHI/*Msg.hh`、`debug/*.hh`），这三类
   必须与 proprietary binary 的生成结果 **ABI 完全一致**，才是真正的风险点。

3. **"拿到 binary 后还能融合"这个诉求，静态库(.a) 其实满足不了**——见 §3。若真要在**已有
   binary** 之后注入/替换 EP，需要 binary 本身把 EP 做成**动态库(.so) + dlopen 插件点**，
   或 proprietary 方在链接期把我们的 .a/.o 一起链进去（即"合并时"融合，不是"binary 之后"）。
   这点必须先跟对方确认交付形态，否则方案选错。

4. **修正后的工作量：静态库路线 5–7 天（原估 3.6 天偏乐观）**；若要真正的 binary 后可插拔
   （.so 插件）则 8–12 天且**依赖 proprietary 方提供插件 ABI**，不完全由我们决定。

---

## 1. EP+UBAdapter 的真实依赖面（原方案未充分披露）

从 `gem5/src/mem/ruby/protocol/chi/ep/` 实测：

### 1.1 组成（6 个 SimObject + 地址映射）
- `EPController`（基类，`: public AbstractController`，Ruby 控制器）
- `EPRNFController` / `EPSNFController`（继承 EPController）
- `EPBackend` / `UBAdapter` / `MetaRNFController`（`: public SimObject`）
- `NodeAddressMap`（地址编解码，`.cc` 被 `#include "protocol/NodeAddressMap.cc"` — 构建怪癖）
- `CoherenceMessage.hh`（跨进程消息结构，POD）
- `M4~M8SelfTest.cc`（自检，生产可排除 ✓ 与原方案一致）

### 1.2 include 依赖分三类（关键区分）

| 类别 | 例子 | 来源 | 与 proprietary 融合的风险 |
|------|------|------|--------------------------|
| **A. 稳定核心 API** | `sim/cur_tick.hh`, `sim/eventq.hh`(Cycles/Event), `sim/sim_object.hh`, `sim/system.hh`, `base/logging.hh`, `mem/packet.hh`, `mem/ruby/common/DataBlock.hh`, `mem/ruby/slicc_interface/AbstractController.hh`, `mem/ruby/system/RubySystem.hh` | 开源 gem5 手写头 | **低**（原方案覆盖到，判断正确） |
| **B. build-generated 头（原方案严重低估）** | `params/EPBackend.hh` 等 6 个、`mem/ruby/protocol/CHI/CHIRequestMsg.hh` `CHIDataMsg.hh` `CHIResponseMsg.hh` `CHIRequestType.hh` `EpProxyOp.hh`、`debug/RubyEP.hh` `RubyCHIGeneric.hh` | **scons 生成**（只存在于 `build/ARM/`，不在源码树） | **高** ← 见 §2 |
| **C. 外部自有** | `framework/MemMessage.hh`, `framework/Port.hh`, zmq | 我们自己维护的 `libframework.a` | **无**（已独立） |

### 1.3 一个正面结论：EP 没有硬耦合到 SLICC 生成的 *控制器* 类
grep 确认 EP 代码**不引用** `Cache_Controller` / `CHI_*_Controller` 等 SLICC 生成的控制器类。
EP 是"与 CHI 并列的独立控制器"，只依赖 CHI 的**消息类型**（B 类里的 `CHI*Msg.hh`），不依赖
CHI 状态机生成代码。这降低了耦合面，是方案可行的基础。

---

## 2. 原方案最大的盲点：build-generated 头的 ABI 耦合

原方案 §1 说"只要头文件即可"，这对 A 类成立，但 **B 类头是构建产物，不是稳定头**：

- **`params/EPBackend.hh` 等**：由 gem5 SimObject 系统从 `EPBackend.py` **生成**，字段顺序/类型
  由 `.py` 的 Param 声明决定。EP 的 `.cc` 直接读 `params.xxx`。→ proprietary binary 必须用
  **同一份 `.py`** 生成**同样布局**的 params 结构，否则 ABI 错位（读到错字段）。
- **`CHI/CHIRequestMsg.hh` 等**：由 SLICC 从 CHI `.sm` 协议文件生成。消息结构体布局是生成代码。
  → 若 proprietary 方改了 CHI 协议 `.sm`（哪怕加个字段），消息布局变，EP 收发的 CHI 消息 ABI
  就对不上。这是**比 SimObject 基类重构更现实的风险**（原方案 §7.1 把最高风险标成 SimObject
  基类，其实 CHI 消息布局漂移概率更高）。
- **`debug/RubyEP.hh` 等**：由 `DebugFlag('RubyEP')` 生成。相对好办（可自建同名 flag 或用
  `TRACING_ON=0` 去掉 DPRINTF）。

**含义**：静态库能否 link 进 proprietary binary，不取决于"有没有头"，而取决于
**proprietary binary 的 params/CHI-msg 生成结果是否与我们编 .a 时用的完全一致**。因此
`integrate.sh` 里"用 proprietary headers 重编 .a"这一步是**必须的、也是有效的**——只要
proprietary 方肯给我们**它生成后的 headers**（build/ 下的 params/CHI/debug），我们重编一次
.a 即可对齐。这点原方案的 integrate.sh 做对了，但没讲清楚"为什么必须重编"。

---

## 3. 交付形态：静态库满足不了"拿到 binary 之后再融合"

这是**最需要先澄清的战略问题**。用户诉求是"获得私有 binary 之后还有修改空间"。但：

- **静态库(.a)** 的符号在**链接期**解析。一旦 proprietary binary 已经链接成型，.a 就无法
  "事后"塞进去。.a 路线的真实含义是：**在 merge/link 阶段，proprietary 方把我们的 .a（或
  .o）一起链进它的 binary**。即"融合发生在他们构建时"，不是"我们拿到 binary 后"。
- 若要**真正在拿到 binary 后仍能替换 EP**，只有两条路：
  1. **EP 编成动态库 .so + binary 预留 dlopen 插件点**：binary 启动时 `dlopen("libep.so")`，
     通过约定的 C ABI 工厂函数创建 EP SimObject。**这要求 proprietary binary 侧提供插件加载
     机制和稳定的 C ABI 接口**——不是我们单方面能决定的，必须对方配合。
  2. **`LD_PRELOAD` 覆盖符号**：仅当 EP 符号在 binary 里是**未内联的弱/动态符号**才可能，
     gem5 默认静态链接 + 大量内联，基本不可行。原方案 integrate.sh §4 提了 LD_PRELOAD 但
     标注"如果支持"——现实中基本不支持。

**建议**：先向 proprietary 方确认三选一：
- (a) 他们在**链接阶段**接收我们的 `.a`/`.o`（最省事，静态库方案适用，融合在他们构建时）；
- (b) 他们提供 **.so 插件 ABI**（我们拿到 binary 后能换 EP，但需对方支持）；
- (c) 只给纯 binary、无任何注入点（那 EP 修改空间 = 0，只能靠 §4 的运行时参数）。

不同答案对应完全不同的工作量与可行性。**不澄清这点，库化方案可能白做。**

---

## 4. 无论交付形态如何，都应最大化"运行时可调"面（降风险的兜底）

即使最坏情况 (3c)，只要参数是**运行时读取**（env var / 配置文件 / CLI），拿到纯 binary 后
仍能通过外部输入调节行为。当前状态已经不错：

| 可调项 | 机制 | binary 后可改? |
|--------|------|---------------|
| EP retry/backoff（EP_RETRY_CYCLES 等） | **env var** | ✅ 直接 |
| cache/NoC 延迟（chi_params.json） | **JSON**，CHI_config.py 加载 | ✅ 改 JSON（前提 binary 用同一 config.py 流程） |
| 拓扑/节点数/socket | `_opt()` + CLI | ✅ |
| ubio 侧（_recallTimeout 等） | ubio 是外部进程 | ✅ 改 ubio，与 gem5 固定无关 |

**建议扩项**：把 push-grant 新引入的行为开关（如"push 开/关"回退纯 pull）也做成 env var，
这样即使拿到纯 binary，也能在出问题时一键回退到旧路径。**成本 ~0.2 天，收益是拿到 binary
后多一个安全阀。**

---

## 5. 修正后的工作量估计

### 5.1 参数外置（收尾）
| 任务 | 工时 |
|------|------|
| `_recallTimeout` 硬编码 → ubio CLI `--recall-timeout` | 0.2 天 |
| push-grant 开关做成 env var（回退纯 pull 的安全阀） | 0.2 天 |
| 校验 chi_params.json 覆盖所有想调的延迟项 | 0.1 天 |
| **小计** | **0.5 天** |

### 5.2 静态库路线（假设交付形态 = 3a：他们链接期收我们的 .a）
| 任务 | 原估 | 修正 | 说明 |
|------|------|------|------|
| 提取 A 类稳定头 | 0.5 | 0.5 | 一致 |
| **提取 B 类 build-generated 头 + 建立"用 proprietary 生成头重编"流程** | (未列) | **1.5** | 原方案盲点：params/CHI/debug 生成头，需脚本从 proprietary build/ 抽取并对齐 |
| libep_module/ 目录 + CMake | 0.5 | 0.5 | 一致 |
| 用开源 gem5 headers 编译验证 | 0.5 | 0.5 | 一致 |
| **CHI 消息 ABI 一致性校验工具**（对比 CHI*Msg 布局） | (未列) | **1.0** | 防协议 .sm 漂移导致的静默 ABI 错位 |
| integrate.sh（重编 + nm 符号检查 + link 测试） | 0.5 | 0.5 | 基本可用 |
| NodeAddressMap.cc 的 `#include .cc` 怪癖清理 | (未列) | 0.2 | 改成正常编译单元 |
| 排除 M*SelfTest | 0.1 | 0.1 | 一致 |
| README + API surface doc | 0.5 | 0.5 | 一致 |
| SimObject 基类/ RubySystem API 漂移的 thunk 预留 | 1.0 | 1.0 | 一致 |
| **小计** | 3.6 | **~6.3 天** | |

### 5.3 若交付形态 = 3b（.so 插件，binary 后可换 EP）
在 5.2 基础上追加：
| 任务 | 工时 |
|------|------|
| 与 proprietary 方对齐插件 C ABI（工厂函数签名、SimObject 注册协议） | 依赖对方，1–3 天 |
| EP 改为 .so + 导出 C ABI 工厂 + 隐藏内部符号（-fvisibility=hidden） | 2 天 |
| dlopen 加载 + 生命周期/单例（EPBackend::getBackendInstance 等全局状态要处理） | 1.5 天 |
| **追加小计** | **4.5–6.5 天**（且强依赖对方配合） |

---

## 6. 给决策者的建议

1. **立刻要做（不依赖任何外部确认）**：完成 §5.1 参数外置收尾（0.5 天），把 push-grant
   开关也 env var 化。这是无论如何都不亏的兜底。
2. **两天固定 gem5 之前必须澄清（阻塞项）**：向 proprietary 方确认 §3 的交付形态 (a/b/c)。
   这决定库化方案是否成立、工作量是 6 天还是 12 天。**这是最高优先级的沟通事项。**
3. **静态库方案本身可做，但要按 §2 补齐 build-generated 头的对齐流程和 CHI 消息 ABI 校验**——
   原方案 3.6 天的估计偏乐观，实际 ~6.3 天。
4. **EP 无 SLICC 控制器硬耦合**（§1.3）是利好，说明抽 EP 出来是干净的；主要风险集中在
   params/CHI-msg 的 ABI 对齐，而这**可以靠"用 proprietary 生成头重编 .a"化解**——只要对方
   愿意给我们它的 `build/` 下生成头。这是除交付形态外的第二个必问事项。

---

## 7. 必问 proprietary 方的清单（两天内）

1. 交付形态：链接期收 .a/.o（3a）/ .so 插件 ABI（3b）/ 纯 binary（3c）？
2. 是否提供**生成后的 headers**（build/ 下的 `params/*.hh`、`CHI/*Msg.hh`、`debug/*.hh`）？
3. 是否改动过 CHI 协议 `.sm`（影响 CHI 消息 ABI）？gem5 版本号是否确为 v25.1？
4. `SimObject` / `AbstractController` / `RubySystem` 基类接口是否有改动？
5. 是否保留 SimObject Python 参数生成流程（我们的 `EPBackend.py` 等能否被其 config 加载）？
