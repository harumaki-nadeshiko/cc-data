# gem5 ↔ ubio 完全解耦重构计划

## 执行纲领

### 迭代纪律

本计划按阶段（Phase）依次执行。每个阶段被细化为若干个 iteration，每个 iteration 只针对**当前首个未完成的阶段**，循环执行以下 5 步：

1. **代码读取分析与修改** — 阅读相关源码，实施当前 iteration 的改动
2. **编译** — 确保 gem5 + ubio 构建成功
3. **测试执行** — 运行全量回归测试（14+ TC）
4. **结果分析** — 检查测试结果，识别回归并修复
5. **提交** — 若当前阶段的所有 iteration 均通过，将阶段内所有 commit 整合提交

### 提交规则

- **每个阶段完成之后必须 commit**，不得跨阶段混入未提交改动
- 一个阶段包含的多次 iteration 中的中间 commit 允许 amend/squash，但阶段终态 commit 不可丢失
- 如需暂时回到之前的某个 commit 做对比验证，必须使用 `git stash` + checkout 或新建临时分支，**保证之后能复原到当前工作状态**（通过 `git stash pop` 或 merge 回主分支）
- 严禁 `git reset --hard` 丢弃已验证通过的改动

### 迭代上限

所有阶段总计最多执行 **TEST_ALL** 个 iteration（常量后续指定）。超过上限时暂停并报告进展，由用户决定是否追加配额。

### 阶段执行顺序

阶段按 0 → 1 → 2 → 3 → 4 严格串行。前一阶段全部 iteration 通过并提交后，方可开始下一阶段。

---

## 现状总览

扫描发现 **127 处硬依赖**、**39 处注释/命名依赖**、**6 处构建/IPC 交叉依赖**。核心耦合模式：

| 方向 | 模式 | 数量 |
|------|------|------|
| gem5 → ubio | `#include` 文本包含 ubio 源码、CoherenceMessage 协议定义、UBCC 环境变量、libframework 链接 | 58 |
| ubio → gem5 | `namespace gem5::ruby`、`Tick/curTick/DPRINTF` API、`gem5_shim.hh` 双向适配器、`mem/ruby/` 路径镜像 | 52 |
| 构建/IPC | SConscript 跨界引用、IPC endpoint 硬编码 `gem5_ubio`、环境变量共享 | 17 |

## 你的附加要求汇总

| # | 要求 | 调查结论 |
|---|------|---------|
| 1 | 外部模块 log 用 `framework::LogInfo/LogError` | 当前 ubio 用 `DPRINTF(RubyEP)`,直接依赖 gem5 debug flag，需改为 framework 日志宏 |
| 2 | 非 Port 初始化参数走 argc/argv | 发现 1 个 Fault(`UBIO_FAULT_RULES`) + 大量 `UBCC_*` 运行时配置读环境变量，需迁移 |
| 3 | BARRIER/GEM5_HELLO 放入 CohMessage 头，传输层只保留 Payload/Sync/Terminate | 详见 §3.2 分析 |
| 4 | 分析 Port wall-clock 接口并决定取舍 | 纯活性(liveness)机制：检测 gem5 异常退出。正常退出 TERMINATE 已处理。可安全移除。 |
| 5 | MemMessage `src/dst module/port` vs `sourceId/targetId` | 详见 §3.4 分析 |
| 6 | 严格限制共用文件范围 | 已整理完毕，见 §4 |
| 7 | Backstore 不应在 gem5 中 | 已确认 `_org` 零调用，可安全删除；仅保留类型定义共享 |
| 8 | `UBCC_NUM_NODES` 等通过 gem5 argc/argv 传入 | 见 §5 |

---

## 阶段 0：共享协议层（protocol/）

### 目标
提取两侧公用的消息协议定义到独立目录，消除 `../../../../../../../modules/` 相对路径和文本包含模式。

### 新建目录结构
```
protocol/
├── CoherenceMessage.hh     # 权威源。从 modules/ubiomodule/ 移至此（原位置删除）
├── BackstoreTypes.hh       # 权威源。消除 gem5/modules 两份副本
├── BackstoreOrganization.hh
├── BackstoreSchemaA.hh
├── BackstoreSchemaA.cc
├── BackstoreSchemaC.hh
├── BackstoreSchemaC.cc
├── NodeAddressMap.hh       # 纯类定义版本（不含 getenv helper）
└── CoherenceConstants.hh   # 新建：MESIState, OuterGrantType, OuterReqType 等共用枚举
```

### 改动
1. **gem5 侧**：`SConscript` 添加 `-I$(ROOT)/protocol`；所有 `#include "../../../../../../../modules/..."` 改为 `#include "protocol/..."`；删除 `gem5/.../BackstoreSchemaA.cc` 等 1 行 wrapper（不再需要文本包含）；`CoherenceMessage.hh` wrapper 删除。
2. **ubio 侧**：`build_ubio.sh` 添加 `-I$(ROOT)/protocol`；删除 `modules/ubiomodule/mem/ruby/protocol/chi/ep/` 下所有 forwarder `.hh`（不再需要）；`modules/ubiomodule/` 下的 `CoherenceMessage.hh` 权威源**移入 protocol/**。
3. **Backstore 在 gem5 中的残留**：删除 `EPBackend::_org` 成员变量和 `setBackstoreOrganization()`/`backstoreOrganization()` 方法（已确认零调用）；删除 `EPBackend.py` 中 Backstore 相关参数。gem5 不再实例化任何 Backstore Organization。

### 共用文件清单（严格限制）

以下是必须共享的文件（协议级数据结构，序列化兼容性要求强制一致）：

| 文件 | 行数 | 共享理由 | 方式 |
|------|------|---------|------|
| `CoherenceMessage.hh` | 282 | 所有 inter-process 消息的 type/flags/body 定义 | 构建时复制到 gem5 include 路径 |
| `BackstoreTypes.hh` | 238 | `BackstoreEntry`、`GroupIndex`、`pack/unpack` 格式 | 同上 |
| `BackstoreOrganization.hh` | 123 | 页组织抽象接口 | 同上 |
| `BackstoreSchemaA.hh/.cc` | 75 | Schema A 实现 | 同上 |
| `BackstoreSchemaC.hh/.cc` | ~80 | Schema C 实现 | 同上 |
| `NodeAddressMap.hh` | ~100 | PA→node/socket 地址映射逻辑 | 同上（需先清理 getenv helper） |
| `CoherenceConstants.hh` | 新建 | MESIState, OuterGrantType 等共用枚举 | 同上 |

**总计 8 个文件**，全部为协议级数据结构定义，必须保持严格一致。

### 非共用文件（不需要共享的）

- `UBCCController.cc/hh` — 仅 ubio 使用
- `UBIOModule.cc/hh` — 仅 ubio 使用（旧单体架构残留，多进程分离后可能简化）
- `ResidentDir.cc/hh` — 仅 ubio 使用
- `UBAdapter.cc/hh` — 仅 gem5 使用
- `EPBackend.cc/hh` — 仅 gem5 使用
- `EP*NFController.cc/hh` — 仅 gem5 使用
- `MetaRNFController.cc/hh` — 仅 gem5 使用

---

## 阶段 1：ubio 去 gem5 化

### 1.1 日志系统迁移

**当前**：ubio 使用 `DPRINTF(RubyEP, ...)`、`DPRINTF(RubyCHIGeneric, ...)` 等 gem5 调试宏。

**目标**：改为 `framework::LogInfo` / `framework::LogError`。

**接口**（你的指定）：
```cpp
namespace framework {
    void LogInfo(const char *module_name, const char *format_str, ...);
    void LogError(const char *module_name, const char *format_str, ...);
}
```

**LogCategory 建议**（替代 gem5 debug flags）：
| 旧的 gem5 flag | 新的 module_name |
|----------------|-----------------|
| `RubyEP` | `"UBCC"` |
| `RubyCHIGeneric` | `"UBCC"` |
| `UBLatency` | `"UBCC-latency"` |
| `UBInvariant` | `"UBCC-invariant"` |

**改动量**：约 50 处 `DPRINTF` → `framework::LogInfo/LogError`。

### 1.2 命名空间解耦

**当前**：所有 modules/ 代码在 `namespace gem5::ruby` 下。

**目标**：
- UBCC 相关：`namespace ubiocc`
- framework 共享：保持 `namespace framework`
- 协议层（protocol/）：`namespace protocol`（无 gem5 依赖）

**改动**：全局 `s/namespace gem5/namespace ubiocc/g`（在 modules/ 范围内），`s/gem5::ruby::/ubiocc::/g`。注意 `CFLAG_ACCEPTED` 等枚举从 `gem5::ruby::` 移到 `protocol::`。

### 1.3 删除 gem5_shim.hh 及所有存根

**删除文件列表**：
| 文件 | 说明 |
|------|------|
| `modules/ubiomodule/gem5_shim.hh` | `Tick`/`curTick`/`SimObject`/`DataBlock` 存根 |
| `modules/ubiomodule/base/types.hh` | gem5 类型转发 |
| `modules/ubiomodule/base/logging.hh` | gem5 日志转发 |
| `modules/ubiomodule/sim/cur_tick.hh` | gem5 tick 存根 |
| `modules/ubiomodule/sim/sim_object.hh` | gem5 SimObject 存根 |
| `modules/ubiomodule/debug/RubyEP.hh` | gem5 debug flag 存根（共 4+ 文件） |
| `modules/ubiomodule/mem/ruby/common/DataBlock.hh` | DataBlock 存根 |
| `modules/ubiomodule/mem/ruby/system/RubySystem.hh` | RubySystem 存根 |
| `modules/ubiomodule/mem/ruby/protocol/chi/ep/*.hh` | 前向转发层（约 6 个文件） |

**替代方案**：
- `Tick` → `uint64_t`（已在此处和其他标准 C++ 类型中可用）
- `curTick()` → `framework::currentTick()`（由 Port 的 safeTs/minTs 提供）
- `SimObject` → ubio 自己定义轻量基类（或不需要，取决于 UBIOModule 最终是否保留）
- `DPRINTF` → `framework::LogInfo/LogError`（见 §1.1）
- `DataBlock` → `uint8_t data[64]`（已足够）

### 1.4 SimObject 解耦

**当前**：`UBIOModule` 继承 `gem5::SimObject`（在 gem5 内编译时）或 `gem5_shim.hh` 中的存根（独立编译时）。

**目标**：`UBIOModule` 不再继承任何 gem5 类。重新设计为独立的事件循环驱动对象：
```cpp
class UBIOModule {
public:
    void init();
    void tick();  // 替代 SimObject::startup() + gem5 event scheduling
    void drain();
};
```

**注意**：`UBIOModule` 在 gem5 in-process 模式中可能存在 gem5 SimObject 版本。需要在 `gem5/src/` 下保留一个轻量的 wrapper 类将 gem5 `SimObject::startup()` 委托给 ubio `UBIOModule::tick()` 循环。但这不涉及 ubio 侧依赖 gem5 头文件。

---

## 阶段 2：gem5 去 ubio 化

### 2.1 Backstore 完全移除

**当前状态**（调查确认）：
- `EPBackend::_org` (BackstoreOrganization*) — 创建后**零调用**，纯残留代码
- `BackstoreOrganization` 及其子类 — 类型定义需保留在 protocol/ 共享，但 gem5 不再实例化
- `BackstoreSchemaA.cc/BackstoreSchemaC.cc` 1 行 wrapper — gem5 不再需要编译这些文件

**改动**：
1. 删除 `gem5/.../ep/BackstoreSchemaA.cc`、`BackstoreSchemaC.cc`、`NodeAddressMap.cc`（1 行 wrapper）
2. 从 `gem5/.../ep/SConscript` 移除 `Source('BackstoreSchemaA.cc')` 等行
3. 删除 `EPBackend::_org`、`setBackstoreOrganization()`、`backstoreOrganization()`
4. 删除 `EPBackend.py` 中 Backstore 相关参数

### 2.2 NodeAddressMap 环境变量清理

**当前**：`gem5/.../NodeAddressMap.hh` 有两个 inline 函数 `epNumNodesFromEnv()`、`epNumSocketsFromEnv()` 含 `std::getenv()`。

**改动**：
1. 删除这两个 inline 函数
2. `UBAdapter` 构造时从参数获取 `numNodes`/`numSockets`（见 §5 arg 传递）
3. 统一使用 `NodeAddressMap(int numNodes, int numSockets, int localNode)` 构造函数（当前已存在）

### 2.3 删除 #include 路径指向 modules/

**当前**：
```
gem5/.../ep/CoherenceMessage.hh        → #include "../../../../../../../modules/..."
gem5/.../ep/BackstoreTypes.hh          → gem5 自有副本
gem5/.../ep/BackstoreOrganization.hh   → gem5 自有副本
```

**改为**：
```
gem5/.../ep/ 下不再保留任何 "modules/" 路径的 #include
所有共享文件从 protocol/ 目录引入
```

### 2.4 libframework 链接解耦

**当前**：gem5 SConscript 链接 `libframework.a` 和 `libzmq`。

**改动**：
- gem5 不再链接 libframework（Port/IPC 层移至 ubio/nsim 进程内）
- gem5 UBAdapter 只通过 ZMQ 与外部通信（ZMQ 是 gem5 自身可接受的依赖）
- 删除 SConscript 中 `repo_root = ../../..` 跨界导航和 `libframework.a` 链接

---

## 阶段 3：传输层重构

### 3.1 MemMessage 精简

**当前 MemMessageType**：
```cpp
enum class MemMessageType : uint32_t {
    CONTROL_SYNC    = 0,
    TERMINATE       = 1,
    COH_MSG         = 2,
    BARRIER_REACHED = 3,
    BARRIER_RELEASE = 4,
    PORT_HELLO      = 5,   // 死代码，全代码库零使用
    PORT_HELLO_ACK  = 6,   // 死代码，全代码库零使用
};
```

**你的要求**：传输层只保留 Payload/Sync/Terminate 三种类型。

**改动**：
```cpp
enum class MemMessageType : uint32_t {
    CONTROL_SYNC = 0,
    TERMINATE    = 1,
    PAYLOAD      = 2,   // 原 COH_MSG，payload 承载 CoherenceMessage
};
```

- `BARRIER_REACHED` / `BARRIER_RELEASE` → 移入 `CoherenceMessageType` 枚举（协议层），通过 `PAYLOAD` + CoherenceMessage 承载
- `PORT_HELLO` / `PORT_HELLO_ACK` → **删除**（死代码，零使用，确认: `grep -rn "PORT_HELLO" tools/ modules/ --include="*.cc"` 无调用点）
- `CONTROL_SYNC` 保留为传输层控制消息（客户端不感知，Port 内部自动维护）

### 3.2 CoherenceMessageType 扩展

在 `protocol/CoherenceMessage.hh` 的 `CoherenceMessageType` 枚举中新增：
```cpp
BarrierReached,      // 替代 MemMessageType::BARRIER_REACHED
BarrierRelease,      // 替代 MemMessageType::BARRIER_RELEASE
```

barrier 进程收包逻辑从读 `MemMessage::hdr.type` 改为读 `CoherenceMessage::h.type`。

### 3.3 MemMessage 字段精简

**当前 MemMessageHeader**：
```cpp
struct MemMessageHeader {
    uint64_t timestamp;
    uint32_t size;
    uint32_t type;          // MemMessageType
    uint32_t src_module;    // ←
    uint32_t src_port;      // ←
    uint32_t dst_module;    // ←
    uint32_t dst_port;      // ←
    uint64_t req_id;
};
```

**调查结论**（你的问题 5）：
- `src/dst_module/port` **仅在单跳（Port→Port）范围内使用**，不沿 coherence 链路传播
- 每个 hop（gem5→ubio→net→ubio→gem5）中转节点会用**下一跳地址**重写 `dst_module/dst_port`
- `src_module` 用于接收端区分消息来源（barrier 进程直接从 `hdr.src_module` 获知节点号）
- CoherenceMessage header 已经携带 `srcNode/srcSocket/dstNode/dstSocket` 提供全程不变的逻辑路由信息

**建议**：简化为 `sourceId` + `targetId`（Node 级，不含 socket/port 粒度），需要 socket 路由时编码进 sourceId（如 `sourceId = node * numSockets + socket`）。Port ID 不再作为协议字段（Port 层通过 socket 绑定自动确定）。

**必须保留的字段**：`req_id`（用于请求-响应匹配）。

### 3.4 Port wall-clock 接口清理

**调查结论**（你的问题 4）：

这两个接口仅用于活性(liveness)，不影响协议正确性：
- `peerStaleMs(thresholdMs)`：检测对端 wall-clock silence
- `markPeerDone(reason)`：标记对端死亡

**删除方案**：
- 正常退出路径：gem5 → ubio TERMINATE → Port 自动 `failClosed()` —— 已完善
- 异常退出路径：删除后，gem5 crash 时 ubio 会永久卡住。但 e2e 测试 launcher 已负责监测 gem5 退出并 kill 其余进程，所以 wall-clock 接口作为兜底可以安全移除
- 删除 `Port::peerStaleMs()`、`Port::markPeerDone()`（及其在 `Port.cc` 中的实现）、`ubio_main.cc:901-906` 的调用点
- 删除 `_lastRxWallMs`、`_initWallMs` 成员变量和 `nowWallMs()` helper

### 3.5 IPC endpoint 命名去 gem5 化

**当前**：
```cpp
// framework/Port.cc:296
static const std::string IPC_BASE = "/workspace/gem5/shared_ipc/ipc";
// endpoint URI: ipc:///workspace/gem5/shared_ipc/ipc_gem5_0_to_ubio_0
```

**改为**：IPC_BASE 从环境变量或配置读取（这是 Port 初始化参数，保留环境变量合理）；endpoint 名从 `gem5_ubio` → `node_ep`：
```
ipc:///workspace/gem5/shared_ipc/ipc_node_0_to_ep_0
```

---

## 阶段 4：参数传递机制重构

### 4.1 Fault Injection 参数从环境变量迁移到 argc/argv

**当前**：`UBIO_FAULT_RULES` 通过 `getenv()` 读取（`tools/ubio/ubio_main.cc:121`）。

**目标**：通过命令行参数传递：
```
./ubio --node=0 --fault-rules="drop:ReadReq:1:0:0x1000:drop"
```

**改动**：
- `ubio_main.cc`：添加 `--fault-rules` 参数解析
- `run_multi.sh`：在 ubio 启动行通过 `--fault-rules` 传入（之前在环境变量中）

### 4.2 UBCC 运行时配置从环境变量迁移到 argc/argv

**需要迁移的变量**：
| 环境变量 | 读取位置 | 改为参数 |
|----------|---------|---------|
| `UBCC_NUM_NODES` | NodeAddressMap.hh, ubio_main.cc, CHI_ubcc_framework.py | `--num-nodes` |
| `UBCC_NUM_SOCKETS` | NodeAddressMap.hh, UBAdapter.cc, UBCCController.cc, ubio_main.cc, CHI_ubcc_framework.py | `--num-sockets` |
| `UBCC_LOCAL_NODE` | UBAdapter.cc, CHI_ubcc_framework.py | `--local-node`（或 `--node-id`） |
| `UBCC_EPOCH_BITS` 等 Python 参数 | CHI_ubcc_framework.py | gem5 `--param` |
| `UBCC_BF_BYTES` | CHI_ubcc_framework.py | gem5 `--param` |
| ... | (完整清单见调查 §1) | |

### 4.3 gem5 启动脚本改造

**`UBCC_NUM_NODES` / `UBCC_NUM_SOCKETS` 的传递链**：

当前路径（环境变量）：
```
run_multi.sh 设置 UBCC_NUM_NODES=3 → gem5 读 getenv → ubio 读 getenv → barrier 不需要
```

目标路径（命令行参数）：
```
run_multi.sh:
  gem5 --param=num_nodes=3 --param=num_sockets=1
  ubio --num-nodes=3 --num-sockets=1 --node-id=0
```

在 gem5 侧通过 `test_e2e.py` 已有的 `--num-nodes`/`--node-id`/`--num-sockets` 参数传入，无需新增。

### 4.4 保留环境变量的参数（仅 Port 初始化）

以下参数保留通过环境变量（因为它们在 Port 初始化阶段就需要，早于参数解析）：
| 环境变量 | 用途 |
|----------|------|
| `IPC_BASE_PATH` | 替代硬编码 `/workspace/gem5/shared_ipc`，Port 绑定前需要 |
| `UBIO_PORT_ENABLE` | 控制 gem5 侧 Port 模式（单节点单独运行 vs 全部绑定） |

---

## 实施顺序与里程碑

| 阶段 | 主要工作 | 改动量 | 风险 | 预估 iteration 数 |
|------|---------|--------|------|------------------|
| 0 | 新建 `protocol/`，迁移 8 个共享文件，消除 `#include "../../.."` | 新建目录 + 50 处路径重写 | 极低 | 2 |
| 1.1 | 日志迁移：`DPRINTF` → `framework::LogInfo/LogError` | 50 处替换 | 低 | 1 |
| 1.2-1.3 | 命名空间解耦 + 删除 gem5_shim 及所有存根 | 30 处重写 + 15 个文件删除 | 低 | 2 |
| 1.4 | SimObject 解耦 | 轻量重构 | 中 | 1 |
| 2.1-2.3 | gem5 侧清理（Backstore、NodeAddressMap、#include） | 10 处删除 + 5 个文件改动 | 低 | 1 |
| 2.4 | libframework 链接解耦 | SConscript 重构 | 中 | 1 |
| 3 | 传输层重构（MemMessage 精简、BARRIER 迁移、wall-clock 删除、endpoint 改名） | 多文件 | 中高 | 3 |
| 4 | 参数传递重构（env→argc/argv） | 启动脚本 + 参数解析 | 低 | 2 |
| **合计** | | | | **13** |

### 阶段执行流程

每个阶段按以下模式执行：

```
Phase N 开始
  │
  ├─ Iteration 1: 读取→修改→编译→测试→分析
  │   ├─ 若测试通过 → 进入 Iteration 2（或提交阶段）
  │   └─ 若失败 → 分析根因，在 Iteration 1 内修复并重测（不消耗新 iteration）
  │
  ├─ Iteration 2: ...（同上）
  │
  └─ 全部 iteration 通过后 → git commit（阶段提交）
       └─ 标注 "Phase N complete: <简述>"
```

- 若一个 iteration 内的编译/测试失败，允许在同一次 iteration 内反复修复和重测，不消耗额外 iteration 配额。
- 只有当改动**整体通过**才标记 iteration 完成，进入下一个 iteration；**若多次修复失败或发现需要不相关的额外改动，才计入新的 iteration**。
- 跨阶段之间必须 commit，不允许未提交改动进入下一阶段。

---

## 附录：Backstore 在 gem5 中的现状（已确认可安全删除）

gem5 中 `EPBackend::_org` (BackstoreOrganization*) 创建后**零调用**。实际 backstore 操作路径：

```
ubio_main → UbioBackstoreHost::store (std::map<uint64_t, BackstoreEntry>)
         → UBCCController::ResidentDir (热路径 512KB SRAM 缓存)
```

gem5 侧仅保留 `BackstoreTypes.hh` / `BackstoreOrganization.hh` 等类型定义供**编译共享**（协议层序列化兼容性要求），无需实例化。

## 附录：Port wall-clock 接口详细分析

`peerStaleMs(thresholdMs)` + `markPeerDone(reason)` 的唯一调用者：
```cpp
// tools/ubio/ubio_main.cc:901-906
if (gem5Port->peerStaleMs(5000)) {
    gem5Port->markPeerDone("gem5 node finished (stale sync)");
}
```

**作用**：检测 gem5 异常退出（未发 TERMINATE）。正常退出路径（gem5 发 TERMINATE → `Port::failClosed()`）已完善。**可安全删除**，因为 launcher 脚本已负责进程生命周期管理。
