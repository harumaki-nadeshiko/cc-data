# EP 模块独立静态库 — 详细方案文档

> 状态: 方案设计 | 日期: 2026-07-09
> 前提: gem5 proprietary binary 是同一 gem5 版本 (v25.1),保留 SimObject 接口和所有公共 API 符号

---

## 1. 为什么不需要回调函数

**静态库 (.a) 只是目标文件的归档**。符号解析发生在最终链接时（link proprietary binary 时），不是在编译 .a 时。所以 EP 模块代码**可以继续使用 gem5 的 API**（`curTick()`, `scheduleEvent()`, `Cycles()`, `fatal()`, `DPRINTF` 等），只要 proprietary gem5 binary 提供这些符号。

编译 .a 时只需要**头文件**（声明），不需要已经 link 好的符号。链接 proprietary binary 时，linker 把 .a 中的符号引用解析到 gem5 提供的实际符号。

**结论: 只要 proprietary gem5 保持与开源 gem5 相同的 SimObject/Event/Proto API 头文件，EP 模块代码不需要任何修改。**

## 2. 假设条件

| 假设 | 是否成立 |
|------|---------|
| proprietary gem5 保留 `gem5::ruby::SimObject` 基类 | ✅ 极可能,gem5 协议栈依赖 |
| proprietary gem5 保留 `curTick()` (定义在 `sim/cur_tick.hh`) | ✅ gem5 核心 API |
| proprietary gem5 保留 `scheduleEvent()` / `Cycles()` | ✅ gem5 事件模型 |
| proprietary gem5 保留 `fatal()` / `warn()` / `DPRINTF` | ✅ gem5 日志 |
| proprietary gem5 保留 `System::syncWait` / `System::systemList` | ✅ gem5 同步机制 |
| proprietary gem5 提供公开头文件 | ⚠️ 需要确认,但通常 proprietary binary 附带开发 headers |

**如果 proprietary gem5 修改了上述 API 的签名或语义，则需要适配。** 但当前方案假设 API 相同。

## 3. 需要纳入 libep_module.a 的源文件

### 3.1 核心协议模块（ep/）

| 文件 | 大小 | 功能 |
|------|------|------|
| `EPSNFController.cc` + `.hh` + `.py` | 556+150 行 | EP SNF 控制器（Ruby state machine 回调） |
| `EPBackend.cc` + `.hh` + `.py` | 2050+838 行 | EP 后端（核心: 地址分类、路由、grant/clear/writeback) |
| `UBAdapter.cc` + `.hh` + `.py` | 1431+249 行 | UBAdapter（ZMQ 发送/接收, response polling） |
| `EPRNFController.cc` + `.hh` + `.py` | ~1400+200 行 | EP RNF 控制器（snoop/receive 本地处理） |
| `MetaRNFController.cc` + `.hh` + `.py` | ~600+100 行 | MetaRNF（metadata 异步读写服务） |

### 3.2 协议定义与地址映射

| 文件 | 位置 | 功能 |
|------|------|------|
| `CoherenceMessage.hh` | `ep/` (副本) 和 `protocol/` | 跨进程一致性消息数据结构 |
| `NodeAddressMap.cc` + `.hh` | `ep/` | DSM PA 地址解码/编码（`homeNode()`, `homeSocket()`, `buildDsmPA()` 等） |

### 3.3 自检模块（可选,可排除）

| 文件 | 功能 |
|------|------|
| `M4SelfTest.cc` ~ `M8SelfTest.cc` (6 个文件) | 内置自检（构造测试请求注入 Ruby）,生产可排除 |

### 3.4 依赖的外部模块（不在 gem5/ 内,但被 UBAdapter 依赖）

| 文件 | 位置 | 用途 |
|------|------|------|
| `framework/MemMessage.hh` | `framework/` | ZMQ 消息容器 |
| `framework/Port.hh` | `framework/` | ZMQ port 抽象 |

**这两个由我们独立维护（framework 在外面），不需要纳入 gem5 的 .a。** 它们已经是独立的 `libframework.a`。

### 3.5 配套 Python SimObject 描述

| 文件 | 用途 |
|------|------|
| `EPSNFController.py` | 告诉 gem5 Python config 如何实例化 `EPSNFController` |
| `EPBackend.py` | 同上 |
| `UBAdapter.py` | 同上 |
| `EPRNFController.py` | 同上 |
| `MetaRNFController.py` | 同上 |

**这些 .py 文件不参与编译**。它们是 gem5 构建系统用来生成 `params/*.hh` 和 C++ SimObject 参数的。proprietary gem5 的 Python config 在构建拓扑时需要 import 它们。

## 4. 目录结构

```
libep_module/
├── CMakeLists.txt              # 构建脚本
├── include/                    # 从开源 gem5 复制的头文件
│   ├── gem5_pub/
│   │   ├── base/logging.hh
│   │   ├── sim/cur_tick.hh
│   │   ├── sim/eventq.hh       # Cycles, Event, scheduleEvent
│   │   ├── sim/system.hh       # System::syncWait
│   │   ├── sim/sim_object.hh   # SimObject 基类
│   │   ├── mem/ruby/common/DataBlock.hh
│   │   ├── mem/ruby/system/RubySystem.hh
│   │   ├── mem/ruby/protocol/CHI/
│   │   ├── params/             # 从 .py 生成的参数头文件
│   │   ├── debug/              # DPRINTF 宏
│   │   └── ...
│   └── framework/
│       ├── MemMessage.hh
│       └── Port.hh
├── src/                        # EP 模块源文件
│   ├── EPSNFController.cc
│   ├── EPBackend.cc
│   ├── UBAdapter.cc
│   ├── EPRNFController.cc
│   ├── MetaRNFController.cc
│   ├── NodeAddressMap.cc
│   └── CoherenceMessage.hh
├── py/                         # SimObject Python 描述
│   ├── EPSNFController.py
│   ├── EPBackend.py
│   ├── UBAdapter.py
│   ├── EPRNFController.py
│   └── MetaRNFController.py
└── integrate.sh                # 集成脚本
```

## 5. CMakeLists.txt 设计

```cmake
cmake_minimum_required(VERSION 3.16)
project(libep_module VERSION 1.0 LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)

# 如果集成了 proprietary gem5,设置 GEM5_HEADERS 指向 proprietary headers
if(NOT DEFINED GEM5_HEADERS)
    set(GEM5_HEADERS "${CMAKE_SOURCE_DIR}/include/gem5_pub")
endif()

add_library(ep_module STATIC
    src/EPSNFController.cc
    src/EPBackend.cc
    src/UBAdapter.cc
    src/EPRNFController.cc
    src/MetaRNFController.cc
    src/NodeAddressMap.cc
    # 排除 M*SelfTest.cc (生产不需要)
)

target_include_directories(ep_module PUBLIC
    ${GEM5_HEADERS}
    ${CMAKE_SOURCE_DIR}/include/framework
    ${CMAKE_SOURCE_DIR}/src
)

# 编译定义(与 gem5 一致)
target_compile_definitions(ep_module PRIVATE
    TRACING_ON=1
)
```

## 6. 集成脚本 `integrate.sh`

```bash
#!/bin/bash
# integrate.sh — 用 proprietary gem5 headers 重编 libep_module.a
# 用法: ./integrate.sh <proprietary_gem5_headers_dir> <proprietary_gem5_binary>

set -euo pipefail

GEM5_HDR="${1:?need proprietary gem5 headers dir}"
GEM5_BIN="${2:?need proprietary gem5 binary path}"

# 1. 用 proprietary headers 重编
cmake -B build -S . -DGEM5_HEADERS="$GEM5_HDR"
cmake --build build -j$(nproc)

# 2. ABI 兼容性检查
echo "=== ABI check ==="
nm build/libep_module.a | grep "U curTick\|U fatal\|U scheduleEvent\|U Cycles" | head

# 3. 确认 binary 能 link
echo "=== Link test ==="
echo "int main(){}" | g++ -x c++ - -Lbuild -lep_module -o /tmp/ep_test 2>&1 || true
echo "Link test complete (ignore unresolved references from gem5 symbols)"

# 4. 替换 binary 中的 ep 模块(如果 proprietary binary 支持 LD_PRELOAD)
# 或给出连接指令
echo "Done. libep_module.a ready."
echo "To link into proprietary gem5:"
echo "  g++ ... -Lbuild -lep_module ... -o proprietary_gem5"
```

## 7. 需要额外考虑的 gem5 API 表面

### 7.1 API 变更风险矩阵

| gem5 API | 使用位置 | proprietary 可能变更? | 风险 |
|----------|---------|---------------------|------|
| `curTick()` | EPBackend, UBAdapter, EPSNFController | 低 (核心 API) | 低 |
| `scheduleEvent(Cycles(N))` | EPSNFController, EPRNFController | 低 (核心 API) | 低 |
| `fatal()` / `warn()` | EPBackend, EPSNFController | 低 (日志) | 低 |
| `DPRINTF(Ruby*)` | EPBackend | 中 (debug 类) | 中 |
| `SimObject` 基类 | 所有 SimObject | 中 (可能重构) | **高** |
| `System::syncWait` | UBAdapter | 中 | 中 |
| `RubySystem` | EPSNFController | 中 | 中 |
| `cyclesToTicks()` | EPSNFController | 低 | 低 |

**最高风险**: proprietary gem5 可能重构 `SimObject` 基类或 `RubySystem` 接口。如果发生这种变更,EP 模块的 `.hh` 文件需要适配。

### 7.2 对抗 API 变更的 fallback

如果 proprietary gem5 修改了 SimObject 基类:
1. 保留一份开源 gem5 的 SimObject 头文件
2. 写一个适配层(thunk),将 proprietary 的 SimObject 接口映射到开源接口
3. 或用 `#ifdef GEM5_PROPRIETARY` 条件编译两套代码

## 8. 工作量估计

| 任务 | 难度 | 工时 |
|------|------|------|
| 提取开源 gem5 公共头文件集合 | 低 | 0.5 天 |
| 建立 `libep_module/` 目录结构 + CMakeLists.txt | 低 | 0.5 天 |
| 编译验证(用开源 gem5 headers) | 低 | 0.5 天 |
| 编写 `integrate.sh` | 低 | 0.5 天 |
| 排除 M*SelfTest.cc (生产不需要) | 低 | 0.1 天 |
| 编写 README + API surface doc | 低 | 0.5 天 |
| 预留: 适配 proprietary API 变更的 thunk 层 | 中 | 1.0 天 |
| **合计** | | **3.6 天** |

## 9. 决策清单

| 决策 | 建议 |
|------|------|
| 是否包含 M*SelfTest.cc? | **排除** — 生产不需要,减少 proprietary gem5 的依赖面 |
| 是否包含 MetaRNFController? | **包含** — 是 backstore 访问路径的一部分 |
| 头文件从开源 gem5 提取还是等 proprietary? | **先用开源提取**,integrate.sh 支持用 proprietary 覆盖 |
| 如果 proprietary 改了 SimObject API 怎么办? | **预留 1 天做 thunk 适配层**,ifdef 条件编译 |
