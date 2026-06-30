# CC-EP 三部分重构总方案

> 会话来源：`ses_14018bae1ffe4c37R3vMRKOg3Y`（2026-06-28）  
> 原始归档：`docs/design/{port_refactoring,build_system,decoupling}.md`（保留不改）  
> 本文档整合所有多轮迭代的讨论、所有敲定的决策点，以及完整的 A/B 版本方案

---

## 0. 任务背景与范围

用户在 2026-06-28 05:26（UTC+8）发起了一组庞大的并行设计任务（`/tmp/opencode/session_june28.txt:6612`）：

```
3. 【方案】Port 接口重构方案
4. 【方案】编译系统重构方案
5. 【方案】Gem5 侧进一步解耦方案
5.5 【方案】将 3~5 的方案结果刷回到文件供审阅
```

核心要求：
- 每个方案分别输出 **A（推荐/自动最佳）** 和 **B（完整/保留所有权衡）** 两个版本
- 所有方案由 `plan-designer` subagent 执行，通过多轮 Q&A 收敛
- 最终归档到 `docs/design/`，不直接修改源码

以下按 Task 3 → Task 4 → Task 5 逐条梳理完整设计。

---

## 1. Port 接口重构（Task 3）

### 1.1 目标架构

```
Port 对外只暴露：
  void init(const PortParams& params)
  bool send(MemMessage* msg)
  MemMessage* recv(uint64_t curT, ReceiveStatus* status)
  MemMessage* allocateSendBuffer(uint64_t timestamp)
  void terminate()
  void emitSync(uint64_t curTick)
  uint64_t safeTimestamp(uint64_t curT)

不暴露：zmq_context、ZMQ 内部结构、废弃构造
```

配置链：`EnvLoader（环境变量） → PortParams → PortRuntime → PortConfig`

### 1.2 第 1 轮决策（5 个设计口径）

| Q | 问题 | 用户选择 | 含义 |
|---|------|----------|------|
| 1 | `Port::init()` 一次性 or 可复用？ | **A：一次性** | 重复 init 直接 assert / 全局 fail，断了就全局 fail |
| 2 | ZMQ context 谁持有？ | **A：per-port 独立** | 每个 Port 持有自己的 `zmq::context_t`，接口完全不暴露 |
| 3 | `EnvLoader` 定位是"纯加载器"还是"端口参数生成器"？ | **B：参数生成器** | 环境变量负责 endpoint 模板，各程序传 node/portId/role |
| 4 | endpoint 命名统一范围？ | **A：全部纳入**（含 barrier） | 统一 `EnvLoader` 管理，不区分数据面/控制面 |
| 5 | 旧接口兼容策略？ | **A：一步到位删除** | 旧构造/旧方法直接删除，不保留兼容 wrapper |

### 1.3 第 2 轮决策（4 个设计口径）

| Q | 问题 | 用户选择 | 含义 |
|---|------|----------|------|
| 1 | `syncWindow` 是否入 `PortParams`？ | **A：不入** | 独立配置（`PortRuntime`），与传输层职责解耦 |
| 2 | `terminate()` 仅本地收尾 or 协议通知对端？ | **B：发 TERMINATE 通知** | 先发 TERMINATE 控制消息，再本地清理 |
| 3 | `networksim` 批量建 port 如何调 `EnvLoader`？ | **A：每 port 单独调** | 任一失败则全局 fail-fast，已成功的前 N 个全部回滚 |
| 4 | `_sendBufInUse` 去留？ | **B：删除，改用 RAII** | 以 `TxHandle` RAII 句柄替代，析构自动 cancel |

### 1.4 第 3 轮（最终 4 个收敛问题 → 方案合成）

| Q | 问题 | 最终裁定 |
|---|------|----------|
| 1 | TERMINATE 时是否先 flush 发送队列？ | **immediate：不 flush，允丢尾包**。TERMINATE 只做 best-effort 发送，随后本地进入 CLOSED。仅在 abort/fatal/debug 路径使用 |
| 2 | RAII `TxHandle` 析构时未 send 怎么处理？ | **默认 cancel，不 assert**。Debug 版加 `warn/assert_if_armed`，Release 不因漏发而崩 |
| 3 | fail-fast 回滚策略？ | **已创建 Port 全部析构**。前 N 个成功、第 N+1 个失败 → 前 N 个全部析构，不保留半初始化 Port |
| 4 | `localRxEndpoint` / `peerRxEndpoint` 格式？ | **必须完整 `ipc://` URL**。`Port` 不补前缀；规范化由 launcher / `EnvLoader` 负责 |

### 1.5 方案 A（推荐）

#### 架构总览

- **三层配置**：`PortParams`（静态身份+endpoint） → `PortRuntime`（syncWindow/syncInterval/entrySize） → `PortConfig`（合二为一）
- **发送面**：RAII `TxHandle`，替代 `_sendBufInUse`
- **生命周期**：`INIT → READY → TERMINATING → CLOSED`（异常时到 `PEER_LOST`）
- **TxHandle 生命周期**：`ACQUIRED → SENT | CANCELED`（析构时 ACQUIRED → CANCELED）
- **终端语义**：停止新业务 send → 尝试发 1 个 TERMINATE → 立即本地清理 socket/状态
- **调用方统一**：`ubio` / `networksim` 统一改成 `pollAllPorts() → min(safeTs)`；`UBAdapter` 不再硬编码 endpoint、不再 busy-poll

#### 每文件修改摘要

| 文件 | A 方案修改 |
|------|-----------|
| `framework/Port.hh` | 引入 `PortParams`/`PortRuntime`/`PortConfig` 结构体；新增 `TxHandle`（RAII）、`terminate()`、`closeLocal()`；增加 `TERMINATING`/`CLOSED` 状态枚举；旧 `sendAllocateBuffer()` 保留为内联 wrapper，后续版本再删除 |
| `framework/Port.cc` | `_sendBufInUse` 替代为 `TxHandle` RAII（alloc→return TxHandle，析构自动释放）；`recv()` 中的 future COH_MSG 必须入 `_pending` 队列而非丢弃；`terminate()` 实现为 immediate（不 flush 业务队列），内部状态机 `INIT→READY→TERMINATING→CLOSED` |
| `framework/PortEnvLoader.hh/.cc`（新建） | `PortEnvLoader` 类：从环境变量加载 `PortConfig`（name/moduleId/portId/localRxEndpoint/peerRxEndpoint）；校验 endpoint 必须为 `ipc://` 完整 URL；任一失败立即终止（前 N 个已创建 Port 的回滚由调用方控制） |
| `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.hh/.cc` | 移除构造函数中直接使用 `Port(name,id,port,syncWindow)` 的旧代码；改用 `PortEnvLoader` 生成 `PortConfig` → `Port::init()`；`TransportMode` 固定为异步（不再区分）；删除 busy-poll 等待响应的代码块 |
| `tools/ubio/ubio_main.cc` | 用 `PortEnvLoader::makePortConfig()` 创建所有 Port；引入 `pollAllPorts()` 统一轮询，同步策略改为 `min(safeTs)`；异常退出时调用 `terminate()` |
| `modules/networksim/networksim_main.cc` | 从 topology 推导 identity 参数，每 port 单独调 `EnvLoader`；`pollAllPorts()` + `min(safeTs)`；出队时 `re-timestamp` |
| `tools/barrier/barrier_main.cc` | 改用 `EnvLoader` 加载 config，endpoint 统一纳入 `EnvLoader` 管理 |

### 1.6 方案 B（完整版）

在 A 基础上增加：

- **Bounded drain mode**：`terminate()` 增加可选参数 `drainTimeout`——在超时窗口内尝试排空发送队列（但 `TERMINATE` 消息本身优先于业务消息发送）
- **TxHandle 调试增强**：`TxHandle` 增加 `armMustSend()` 方法；若 armed 但在析构时未 send，触发 release 版 `assert`
- **PortFactory 两阶段创建**：`PortFactory::createAllOrRollback(vector<PortConfig>)`——第一阶段验证所有 config，第二阶段批量创建；任一失败自动回滚已创建 Port
- **Launcher 终止协议**：各 launcher 在 `kill()` / `SIGTERM` 处理中先发 TERMINATE 到所有 peer，避免对端阻塞在 recv 上

---

## 2. 编译系统重构（Task 4）

### 2.1 目标架构

```
framework/      → build_framework.sh → build/lib/libframework.a
              ┌→ build_gem5.sh     (scons)
各模块编译:   ├→ build_ubio.sh     → build/bin/ubio
              ├→ build_networksim.sh → build/bin/networksim
              ├→ build_barrier.sh  → build/bin/barrier_manager
              └→ build_all.sh      (聚合)
```

产物目录：
```
build/
├── framework/
│   ├── include/framework/   # Port.hh, MemMessage.hh (公共头)
│   ├── lib/libframework.a   # Port.o + ZMQChannel.o
│   └── obj/{Port.o, ZMQChannel.o}
└── bin/
    ├── ubio
    ├── networksim
    └── barrier_manager
```

### 2.2 第 1 轮决策（5 个设计口径）

| Q | 问题 | 用户选择 | 含义 |
|---|------|----------|------|
| 1 | `libframework.a` 职责边界 | **A：仅 framework/**（Port+MemMessage） | UBCCController/NodeAddressMap 仍归各模块编译 |
| 2 | .a 产物位置 | **A：`build/lib/`** | 集中管理，不与源码树混合 |
| 3 | gem5 如何链接 .a | **A：`ep/SConscript` 局部修改** | 不改 gem5 顶层构建逻辑 |
| 4 | 脚本依赖组织 | **B：独立 `build_framework.sh`** | 各模块脚本消费产物，不内建 framework 构建逻辑 |
| 5 | `run_multi.sh` 改动范围 | **B：统一到 `build/bin/`** | 固定路径引用 |

### 2.3 第 2 轮决策（4 个设计口径）

| Q | 问题 | 用户选择 | 含义 |
|---|------|----------|------|
| Q6 | `libframework.a` 导出边界 | **A：最小导出** | 仅 `framework/include/` 公共头，模块只能通过公共 API 访问 |
| Q7 | 模块脚本对 framework 缺失的行为 | **A：强依赖失败退出** | 若 `build/lib/libframework.a` 不存在则报错退出，不自动触补建 |
| Q8 | `run_multi.sh` 产物发现 | **A：固定路径** | `build/bin/ubio`、`build/bin/networksim`、`build/bin/barrier_manager`——缺失则 error exit |
| Q9 | gem5 `ep/SConscript` 策略 | **A：只加 include/lib/link** | 最小侵入，不改现有源文件组织 |

### 2.4 方案 A（推荐）

#### `scripts/build_framework.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

FRAMEWORK_DIR="$ROOT_DIR/framework"
ZMQ_INC="$ROOT_DIR/thirdparty/zeromq/include"
ZMQ_LIB="$ROOT_DIR/thirdparty/zeromq/lib"

OUT_DIR="$ROOT_DIR/build/framework"
INC_DIR="$OUT_DIR/include/framework"
LIB_DIR="$OUT_DIR/lib"
OBJ_DIR="$OUT_DIR/obj"

die() { printf 'FATAL: %s\n' "$*" >&2; exit 1; }

[ -d "$FRAMEWORK_DIR" ] || die "missing framework/: $FRAMEWORK_DIR"
[ -d "$ZMQ_INC" ]       || die "missing zeromq include: $ZMQ_INC"
[ -d "$ZMQ_LIB" ]       || die "missing zeromq lib: $ZMQ_LIB"
[ -f "$FRAMEWORK_DIR/Port.cc" ]          || die "missing framework/Port.cc"
[ -f "$FRAMEWORK_DIR/Port.hh" ]          || die "missing framework/Port.hh"
[ -f "$FRAMEWORK_DIR/MemMessage.hh" ]    || die "missing framework/MemMessage.hh"
[ -f "$FRAMEWORK_DIR/ZMQChannel.cc" ]    || die "missing framework/ZMQChannel.cc"

mkdir -p "$INC_DIR" "$LIB_DIR" "$OBJ_DIR"

install -m 0644 "$FRAMEWORK_DIR/Port.hh"       "$INC_DIR/Port.hh"
install -m 0644 "$FRAMEWORK_DIR/MemMessage.hh" "$INC_DIR/MemMessage.hh"

CXX="${CXX:-g++}"
CXXFLAGS=(
  -std=c++17 -O2 -Wall -Wextra -pthread
  "-I$ROOT_DIR"
  "-I$ZMQ_INC"
)

"$CXX" "${CXXFLAGS[@]}" -c "$FRAMEWORK_DIR/Port.cc"       -o "$OBJ_DIR/Port.o"
"$CXX" "${CXXFLAGS[@]}" -c "$FRAMEWORK_DIR/ZMQChannel.cc" -o "$OBJ_DIR/ZMQChannel.o"

ar rcs "$LIB_DIR/libframework.a" \
  "$OBJ_DIR/Port.o" \
  "$OBJ_DIR/ZMQChannel.o"

printf '[build] framework ready: %s\n' "$OUT_DIR"
```

#### 四个模块脚本

**`scripts/build_ubio.sh`**：
1. 检查 `build/lib/libframework.a` 存在，否则报错退出
2. 编译 `tools/ubio/ubio_main.cc` + `modules/ubiomodule/*.cc`（UBCCController/ResidentDir/Backstore 等）
3. 链接 `-Ibuild/framework/include -Lbuild/lib -lframework -lzmq -lpthread`
4. 产物 → `build/bin/ubio`

**`scripts/build_networksim.sh`**：
1. 同框架检查
2. 编译 `modules/networksim/networksim_main.cc`
3. 链接方式同上
4. 产物 → `build/bin/networksim`

**`scripts/build_barrier.sh`**：
1. 同框架检查
2. 编译 `tools/barrier/barrier_main.cc`
3. 链接方式同上
4. 产物 → `build/bin/barrier_manager`

**`scripts/build_all.sh`**：调用 `build_framework.sh` + 上述三个脚本

#### gem5 SConscript 修改

在 `gem5/src/mem/ruby/protocol/chi/ep/SConscript` 中：
- 移除 `Source(File('.../framework/Port.cc'))`
- 增加：`env.Append(CPPPATH=['#../../build/framework/include'])`
- 增加：`env.Append(LIBPATH=['#../../build/lib'])`
- 增加：`env.Append(LIBS=['framework'])`

#### run_multi.sh 修改

固定路径引用：
```bash
UBIO_BIN="$PROJECT_ROOT/build/bin/ubio"
NSIM_BIN="$PROJECT_ROOT/build/bin/networksim"
BARRIER_BIN="$PROJECT_ROOT/build/bin/barrier_manager"
[ -x "$UBIO_BIN" ]    || { echo "FATAL: missing $UBIO_BIN"; exit 1; }
[ -x "$NSIM_BIN" ]    || { echo "FATAL: missing $NSIM_BIN"; exit 1; }
[ -x "$BARRIER_BIN" ] || { echo "FATAL: missing $BARRIER_BIN"; exit 1; }
```

### 2.5 方案 B（完整版）

在 A 基础上增加：
- **`common.sh`**：共享 helper 函数（`check_framework()`、`get_zmq_flags()`、标准 include/lib 路径变量）
- **`env.sh` / pkgconfig**：`framework/lib/pkgconfig/ccframework.pc` 文件，支持 `pkg-config --cflags --libs ccframework`
- **`AUTO_BUILD=1`**：`run_multi.sh` 支持 `AUTO_BUILD=1` 选项，在运行前自动检查并执行缺失的构建步骤
- **环境变量覆盖**：`UBIO_BIN`、`NSIM_BIN`、`BARRIER_BIN` 环境变量可覆盖默认 `build/bin/` 路径

---

## 3. Gem5 解耦（Task 5）

### 3.1 目标

分为两个子目标：

| 子目标 | 描述 |
|--------|------|
| **5.1 节点间拆分** | 每个节点（single/dual-socket）划入单独 Gem5 进程，由一个 UBAdapter 与外界模块通过 Port（消息队列）相连 |
| **5.2 UBIO-Gem5 间拆分** | Gem5 目录下完全消除 UBIOModule/UBCCController 的所有定义；外部 modules/ubiomodule/ 去除对 gem5 的依赖；UBIOModule 是普通 C++ 类（非 SimObject 子类）；外部模块只通过 Port 的消息语义与远端通信 |

用户验收标准：
> 在 Gem5 目录下再也搜不到 UBIOModule/UBCCController 的文字（除注释和纯文档）

### 3.2 第 1 轮决策（5 个设计口径）

| Q | 问题 | 用户选择 | 含义 |
|---|------|----------|------|
| Q1 | Gem5 搜索口径 | **A：严格口径** | 源码/脚本/SConscript/Python 配置全消，仅保留注释/文档 |
| Q2 | 外部模块主名 | **两者保留** | `UBIOModule` 是主类，内部持有 `UBCCController*` 指针，分别负责路由和目录 |
| Q3 | `ResidentDir` 归属 | **A：完全外移** | 到 `modules/ubiomodule/`，Gem5 只看 Port 消息 |
| Q4 | 多 socket 进程粒度 | **A：1 节点 = 1 进程** | Dual-socket 也在同一进程内，内部多个 UBAdapter |
| Q5 | 是否保留同进程直达兜底 | **A：不允许** | 纯 Port 通信，跨节点必须走 Port |

### 3.3 第 2 轮决策（5 个设计口径）

| Q | 问题 | 用户选择 | 含义 |
|---|------|----------|------|
| Q1 | UBIOModule/UBCCController 职责切分 | **B：对半切** | `UBIOModule` 负责端口和跨节点路由；`UBCCController` 负责目录状态、请求仲裁、回收/召回策略 |
| Q2 | Port 最小消息单元 | **A：事务级消息** | 含 txnId/srcNode/dstNode/addr/reqType/data；所有协议行为建模成显式 message |
| Q3 | `ResidentDir` 粒度 | **已有实现无需改** | `ResidentDir _directory` 已是 UBCCController 内嵌成员（`UBCCController.hh:619`），始终 cache-line 粒度 |
| Q4 | 请求阻塞模型 | **保持原本** | Per-address 单 outstanding——`processOuterRequest` 遇已有 `_outstandingReqs` 返回 `BUSY(-1)` |
| Q5 | 双写冲突仲裁 | **A：目录仲裁** | 先在目标目录落表者胜（first-writer-wins），另一方收到 retry/nack/backoff |

### 3.4 方案 A（推荐）：分层收敛版

#### 3.4.1 Gem5 侧变动清单

**删除**（策略：先"停编译/停引用/停实例化"，稳定后再物理删除）：
- `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh`
- 历史残留如存在：`UBIOModule.{hh,cc,py}`

**修改**：

| 文件 | 修改内容 |
|------|----------|
| `gem5/configs/ruby/CHI_ubcc_framework.py` | 固化 HN-F DSM downstream = EP-SNF 单入口；**不在 gem5 内实例化/绑定本地 UBIOModule** |
| `gem5/.../CHI-cache-funcs.sm` | `pickSharerForSnoop()`：**排除 EP-RNF 优先级**（不再将 EP-RNF 作为 snoop 目标） |
| `gem5/.../CHI-cache-actions.sm` | sole-EP-RNF 时统一走 **non-Fwd / non-DCT fallback**（不产生 Forward 请求） |
| `gem5/.../ep/EPRNFController.cc` | 恢复 `SnpShared/SnpSharedFwd/SnpOnceFwd` 的 **fatal-grade unreachable**；recall 改为真实 `ReadShared/ReadUnique` 闭环 |
| `gem5/.../ep/EPBackend.hh/.cc/.py` | 去掉对本地 UBCC 的直接依赖（删除 `#include "UBCCController.hh"`）；仅保留 **txn 级消息桥**、MetaRNF、Clear tuple 校验；self-test 改为 `enable_self_test` 显式开关 |
| `gem5/.../ep/UBAdapter.hh/.cc/.py` | 职责收缩为：**消息编解码 + pending completion + Port 轮询**；删除 router/本地 UBCC 语义；`sendXxxReq()` 全部改为 Port async（-2 pending） |
| `gem5/.../ep/MetaRNFController.hh/.cc/.py` | 作为 backstore metadata I/O 的**唯一 gem5 侧入口**，处理 meta data 的 get/set |
| `gem5/.../ep/SConscript` | 不再编译 gem5 侧 UBCC/UBIOModule 残留文件；移除 `Source(UBCCController.cc)` 等行 |

**需要删除的 forward-include 列表**：
```
gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh   ← 从 EPBackend.cc 移除
gem5/src/mem/ruby/protocol/chi/ep/UBIOModule.hh        ← 从 UBAdapter.cc 移除
gem5/src/mem/ruby/protocol/chi/ep/ResidentDir.hh       ← 从 gem5 端移除（若存在）
```

#### 3.4.2 `modules/ubiomodule/` 变动清单

| 文件 | 修改内容 |
|------|----------|
| `UBIOModule.hh/.cc` | 职责冻结为：**端口、路由、定时队列、transit forwarding**；固定 2 port：`gem5_port` / `network_port`；不再是 SimObject 子类 |
| `UBCCController.hh/.cc` | 职责冻结为：**目录状态、仲裁、single-flight、fanout**；`ResidentDir` 继续作为内嵌成员；保持 per-address 单 outstanding；仲裁采用 **先落表者胜**（first-writer-wins） |
| `CoherenceMessage.hh` | 收敛为事务级 envelope：`txnId` / `srcNode+srcSocket` / `dstNode+dstSocket` / `homeNode+homeSocket` / `addr(homeLinePa/localLinePa)` / `reqType` / `data[64]`；sideband: `flags/dataSource/authEpoch/pendingInvMask` |
| `ResidentDir.hh/.cc` | **不改语义模型**；仅补充与 UBCCController 的 slot/control API 对接 |
| `NodeAddressMap.hh/.cc` | 去掉 gem5 依赖（移除 `#include "base/addr_range.hh"` 等）；改为纯 C++ 实现 |

#### 3.4.3 消息流变化

**解耦前**：
```
EPSNF → EPBackend → UBAdapter → (本地 UBCC 函数调用)
                              → (Router 本地路由表)
```

**解耦后**：
```
EPSNF → EPBackend → UBAdapter → Port.send() → ZMQ → ubio(UBIOModule) → UBCCController
         ↑                                                                    |
         └──────── Port.recv() ← ZMQ ← ubio(UBIOModule) ←─────────────────┘
```

所有 gem5↔ubio 通信完全经过 Port 消息。UBCC 内部保持：per-address 单 outstanding、`BUSY(-1)` 在冲突时返回、先落表者胜仲裁。

#### 3.4.4 UBAdapter 职责变更

| 解耦前 | 解耦后 |
|--------|--------|
| 消息编码 + 路由决策 + UBCC 调用 + 同步等待 + Port 管理 | 消息编码/解码 + pending completion 管理 + Port 轮询（不关心目标节点） |
| `sendReadReq()` 内计算 dstNode、进行 `ubccController->processRequest()` | `sendReadReq()` 仅构造 CoherenceMessage → `transportSend()` → scheduleResponseCheck() |
| router 逻辑内嵌 | router 逻辑全部迁移到 UBIOModule |

### 3.5 方案 B（完整版）

在 A 基础上增加：

- **物理删除**：A 稳定后，从 gem5 文件系统删除所有 UBCC/UBIOModule/ResidentDir 文件（含 `.hh`/`.cc`/`.py`）
- **Backstore 消息化**：Backstore 的读/写也改为 Message-based（读缓冲 → Port 请求 → ubio 处理 → 返回），EPBackend/MetaRNF 不再直接访问 backstore
- **Standalone ubio**：`ubio` 进程具有完整协议栈，不依赖 gem5 启动顺序或共享文件描述符
- **Cross-socket transit rewriting**：Dual-socket 场景下，UBIOModule 负责跨 socket 的 transit 消息改写（重新计算 dstNode/dstSocket）

---

## 4. 全部决策汇总

### 4.1 Port 重构

| 决策 | 选择 |
|------|------|
| `init()` 生命周期 | 一次性，重复调用 assert/fail |
| ZMQ context 所有权 | Per-port 独立 context |
| EnvLoader 角色 | 参数生成器（env 模板 + 调用方身份参数） |
| Endpoint 统一 | 全部纳入，含 barrier |
| 旧接口清理 | 一步到位删除 |
| syncWindow 归属 | 不入 PortParams，独立 PortRuntime |
| terminate() 语义 | 发 TERMINATE + 本地清理（immediate，不 flush） |
| 发送面 RAII | TxHandle，析构 auto-cancel |
| Fail-fast 回滚 | 已创建 Port 全部析构 |
| Endpoint 格式 | 完整 `ipc://` URL |

### 4.2 编译系统

| 决策 | 选择 |
|------|------|
| 静态库边界 | 仅 framework/（Port + MemMessage） |
| 产物位置 | `build/lib/libframework.a` |
| Gem5 链接方式 | `ep/SConscript` 局部修改 |
| 脚本依赖 | 独立 `build_framework.sh`，模块脚本消费产物 |
| 产物统一 | `build/bin/ubio` / `networksim` / `barrier_manager` |
| 导出边界 | 仅 `framework/include/` 公共头 |
| Framework 缺失 | 报错退出，不自动补建 |
| run_multi 产物发现 | 固定路径 |
| SConscript 策略 | 只加 include/lib/link |

### 4.3 Gem5 解耦

| 决策 | 选择 |
|------|------|
| 搜索口径 | 严格：源码/脚本/配置全消 |
| 外部模块主名 | UBIOModule 主类 + UBCCController* 指针 |
| ResidentDir 归属 | 完全外移到 modules/ubuntu_module/ |
| 进程粒度 | 1 节点 = 1 进程（dual-socket 在同一进程内） |
| 同进程兜底 | 不允许，纯 Port |
| 职责切分 | UBIOModule 路由/端口，UBCCController 目录/仲裁 |
| Port 消息单元 | 事务级消息（txnId/addr/reqType/data） |
| ResidentDir 粒度 | 保持现有 cache-line 粒度（内嵌于 UBCCController） |
| 阻塞模型 | Per-address 单 outstanding，BUSY(-1) |
| 双写仲裁 | 目录仲裁（先落表者胜） |

---

## 5. 实施建议

三个方案的推荐实施顺序：

```
1. Task 3: Port 重构（基础设施层，所有模块依赖）
   ↓
2. Task 4: 编译系统重构（随后调整构建）
   ↓
3. Task 5: Gem5 解耦（依赖上述两者完成后进行）
```

每个方案均建议先执行 A（推荐版），稳定后再做 B（完整版）扩展。A/B 版本的文件变更不会冲突——B 是 A 的超集。
