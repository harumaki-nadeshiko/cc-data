# Framework 稳定接口使用与后端移植权威指南

> **权威状态日期：2026-08-05**
> **状态：当前仓库中 Framework 使用、链接、语义合同与后端移植的唯一权威说明。**
> 若 `docs/framework_manual.md`、`docs/migration_guide.md`、旧设计稿、遗留源码或历史命令与本文冲突，以本文、`framework/iface/*.hh` 和 `framework/tests/iface_contract_test.cc` 为准。

## 1. 当前架构与冻结边界

Framework 已拆成三个明确层次，不再允许消费者依赖某个具体传输实现：

| 层次 | 当前产物/目标产物 | 责任 | 稳定性 |
|---|---|---|---|
| 公共接口 | `framework/iface/Message.hh`、`framework/iface/Port.hh`、`framework/iface/Log.hh` | 声明不透明消息、端口、PDES 和日志 API | **稳定公共源码接口**；消费者只能包含这些头 |
| 本地后端 | `build/framework/lib/libframework_local.a` | 当前 ZeroMQ IPC 实现；实现文件为 `framework/Port.cc`、`framework/Log.cc` | 可替换实现，不向消费者暴露 ZMQ 类型或对象布局 |
| 真实后端 | 计划产物 `build/framework/lib/libframework_real.a` | 用目标框架 `libxxx.a + xxx.hh` 包装并实现同一套 iface | 尚需由目标平台实现；只有该后端实现文件可包含 `xxx.hh` |

消费者（native 模块和 gem5 EP）编译时只看到安装到
`build/framework/include/framework/iface/` 的三个公共头；链接时在
`libframework_local.a` 与将来的 `libframework_real.a` 之间二选一。**不得把两个后端同时链接，也不得把后端 archive 合并进模块 archive。**

本文所称“稳定”首先是**源码/API 合同稳定**，不是 C ABI 承诺：

- 接口使用 C++17，包括 `std::string`、`std::string_view` 和模板日志函数；
- 后端 archive 与消费者应使用兼容的编译器、C++17 模式、标准库和 ABI 选项构建；
- 编译器主版本、libstdc++/libc++、`_GLIBCXX_USE_CXX11_ABI` 或异常/RTTI 等 ABI 设置不兼容时，不能假定可链接或可运行；
- 本接口**不是 C API，也不保证 C ABI**。若目标系统要求跨工具链的稳定二进制边界，应在真实后端内部再封装目标 C ABI，不得把该边界冒充为 Framework 自身保证。

## 2. Message 合同

### 2.1 不透明对象与访问器

`framework::Message` 只有前置声明。上层不得 `sizeof(Message)`、访问字段、继承、栈上构造、序列化其内存，或依赖本地后端的 40 字节 wire header。必须通过以下访问器操作：

- 时间戳：`GetMessageTimestamp` / `SetMessageTimestamp`；
- 稳定消息类型：`GetMessageType` / `SetMessageType`；
- 路由字段：source id、target id；
- 事务字段：request id；
- payload：`SetMessagePayload`、`GetMessagePayloadData`、`GetMessagePayloadSize`、`GetMaxPayloadSize`；
- 转发复制：`CopyMessage`；
- 放弃未发送消息：`ReleaseMessage`。

payload 是**无类型字节序列**。应用协议负责定义 payload 中结构体的布局、字节序和版本；Framework 不解释 payload。写入前必须确认 `size <= GetMaxPayloadSize()`；`size != 0` 时 data 不得为空。读取时按 `GetMessagePayloadSize()` 校验后再转换或拷贝，不能仅凭消息类型盲目解引用。

### 2.2 稳定类型与终止信息

`MessageType : uint32_t` 的冻结值为：

| 名称 | 值 | 含义 |
|---|---:|---|
| `ControlSync` | 0 | 普通可见的 PDES 同步消息 |
| `Terminate` | 1 | 普通可见的终止通知 |
| `Payload` | 2 | 应用 payload |

不得重排、复用或私自扩展这些值。显式终止通知可携带稳定的
`TerminateInfo`：`reason`、`exitCode`、`sender` 均为 `uint32_t`，顺序固定，
总长固定为 12 字节。header-only 的 `Terminate`（payload 长度 0）同样合法；接收方必须兼容两种形式。

### 2.3 精确所有权表

| 操作/来源 | 返回或传入对象的所有权 | 精确生命周期和后续动作 |
|---|---|---|
| `AllocateSendMessage` | **owned（调用方拥有）** | 分配成功后由调用方持有，必须且只能交给一次 `SendMessage`，或在未发送时交给一次 `ReleaseMessage` |
| `ReceiveMessage` 返回非空 | **borrowed（借用）** | 仅保证有效到**同一个 Port** 的下一次 `ReceiveMessage` 调用；在其他 Port 上调用 receive 不会使它失效；不得 `ReleaseMessage`，不得发送 borrowed 指针 |
| `CopyMessage(dst, src)` | **所有权不改变** | source 仍保持原 owned/borrowed 状态，destination 仍保持原 owned 状态；复制应用可见字段和 payload，但**保留 destination 的时间戳**及其后端分配/容量/所有权 |
| `SendMessage(port, owned)` | **总是消费** | 无论返回 `true` 还是 `false`，message 都已消费，调用方不得重试同一指针、读取或释放它 |
| `ReleaseMessage(owned)` | **消费未发送 owned 对象** | 仅用于尚未传给 `SendMessage` 的 allocated message；调用后指针失效 |

因此，接收后要排队、跨回调保存或转发时，应立即从目标发送 Port 分配新 owned message，再 `CopyMessage`。转发时间戳由 destination 的分配时间决定，不能依赖 source 时间戳被复制。

### 2.4 合同错误

空必需指针、非法类型、超长 payload、非零长度配空 data、无效内部长度、时间戳加链路时延溢出、同步时间回退等均是编程合同错误。实现应使用
`LogAssertIf(condition, module, ...)` 检查：**condition 为 true 表示通过；`LogAssertIf(false, ...)` 才记录错误并 abort。** 不得把它误读为“条件为 true 时断言”。可恢复的端口创建或传输故障则通过 `nullptr`/`false` 返回并记录日志。

## 3. Port 配置、拓扑与生命周期

### 3.1 `PortConfig` 的完整含义

`PortConfig` 不是简单的地址字符串，而是后端解析连接的完整逻辑身份：

| 字段 | 含义 |
|---|---|
| `selfRole` | 本端进程/组件角色 |
| `peerRole` | 对端角色 |
| `channelName` | 逻辑通道，不可只靠 role 猜测 |
| `nodeId` | 本端节点号，范围 `[0, numNodes)` |
| `socketId` | 本端 socket 号，范围 `[0, numSockets)` |
| `numNodes` | 完整运行拓扑的节点数，不是局部可见数量 |
| `numSockets` | 每节点完整 socket 数，不是当前进程打开的 Port 数 |

后端负责校验完整拓扑并计算：

```text
gid = nodeId * numSockets + socketId
```

上层不得自行拼接本地 ZMQ IPC 文件名，也不得把 gid 算法复制成另一套协议。当前 local 后端的 canonical 组合是：

1. `gem5 <-> ubio`，`channelName="coherence"`，双向角色组合均支持；
2. `ubio <-> networksim`，`channelName="network"`，双向角色组合均支持；
3. 遗留兼容组合 `selfRole="barrier"`、`peerRole="gem5"`、`channelName="barrier"`，当前为 barrier bind-only 特例，不代表通用的反向 Port 规则。

角色匹配不区分大小写；local 后端还把 `nsim`、`network`、`network_sim` 规范化为 `networksim`，但新代码应直接使用 canonical 名称。非法范围、gid 溢出、不支持的 role/channel、分配失败或后端连接创建失败时，`CreatePort` 返回 `nullptr`；调用方必须停止启动或执行明确降级，不能继续解引用。

### 3.2 关闭与销毁

- `TerminatePort(port)`：尝试非阻塞发送 header-only `Terminate`，是 **best-effort** 通知；随后关闭本端资源。它不保证对端收到，不是可靠的分布式 shutdown barrier。
- `DestroyPort(port)`：释放对象及底层资源；它不是“保证送达终止消息”的替代品。
- 正常关闭顺序是 `TerminatePort(port); DestroyPort(port);`。失败清理可直接 `DestroyPort`；二者都接受空指针。
- 如协议要求可靠退出，应由应用先显式发送带 request/ack 的终止协议并等待确认，再调用上述资源清理 API。

## 4. Receive 与 PDES 时间合同

### 4.1 三态 Receive

`ReceiveMessage(port, currentTimestamp, &status)` 只有以下三种语义：

| status | 返回值 | 含义 |
|---|---|---|
| `ReceiveStatus::Message` | 非空 borrowed message | 有一条当前可见消息 |
| `ReceiveStatus::Empty` | `nullptr` | 当前没有已接收或 pending 的消息 |
| `ReceiveStatus::PendingFuture` | `nullptr` | 已收到下一条消息，但其时间戳大于 `currentTimestamp`，必须等虚拟时间到达后再调同一 Port |

`ControlSync` 与 `Terminate` 都通过普通 `ReceiveMessage` 可见，不存在另一个隐藏 control callback。消费者必须先看 `GetMessageType()` 再分派。当前合同中 `Terminate` 即使时间戳在未来也立即可见；`ControlSync` 与 payload 一样受虚拟时间门控。

### 4.2 PDES API

- `EmitSync(port, now)`：按后端 link latency 形成未来同步时间戳；距离上次实际同步不足 link latency 时可成功但不发送重复消息；时间回退或加法溢出是合同错误。
- `ReceiveTimestamp(port)`：若有 pending future，返回该 pending 时间戳，否则返回最近接收时间戳。
- `SafeTimestamp(port, now)`：结合接收时间和同步区间给出本端可安全推进的虚拟时间上界；调用方应取所有相关 Port 的最小安全界。
- `SyncInterval(port)`：返回后端最终采用的同步区间。local 后端允许 `EP_SYNC_INTERVAL_PS` 覆盖配置，并确保不小于 link latency。

Framework **不会用 wall clock 推进仿真时间**。睡眠、poll 次数、主机调度时间和消息等待耗时都不能增加 `currentTimestamp`；只有模拟器/模块自己的离散事件和 PDES 安全界推进虚拟时间。`PendingFuture` 不是“等待几毫秒后就可以忽略时间戳”的提示。

## 5. 日志合同

公共日志入口为 `LogDebug/LogInfo/LogWarn/LogError/LogAssertIf`。格式只冻结以下子集：

- `{}`：十进制整数、字符串或指针的普通格式；
- `{:x}`：整数小写十六进制，不自动加 `0x`；
- `{{` 与 `}}`：分别输出字面量 `{` 与 `}`；
- 每次调用最终**恰好输出一个换行**：输入末尾已有 `\n`/`\r` 会先剥离；
- Debug、Info 写 stdout；Warn、Error、Assert 写 stderr；每条立即 flush；
- 不自动添加 module、level、时间戳等前缀，调用者若需要必须显式写入格式串；
- Debug 默认关闭，`FRAMEWORK_LOG_DEBUG` 或 `EP_DEBUG_FRAMEWORK` 为非空且不是 `0/false/FALSE/off/OFF` 时启用；该结果在进程首次查询后缓存。

native 模块及后端使用上述 Framework 日志；gem5 模拟器自身的 debug/fatal/panic/统计日志仍遵循 gem5 日志体系。真实后端包装 `libxxx` 时，不应把目标库日志重定向成 gem5 DPRINTF，也不应让 gem5 日志 API 泄漏到 native 后端。需要特别注意：E2E runner/verifier 会解析 stdout/stderr 和固定 sentinel，`>>> TC<N> PASSED <<<` 必须保持 verifier 日志最后一行；增加前缀、额外换行、把 Info 改到 stderr，或在 PASS 后输出内容，都可能破坏自动解析。

## 6. 最小 native 用例

以下代码展示创建、发送、三态接收、转发和关闭。错误路径省略了业务级重试，但没有省略所有权处理：

```cpp
#include "framework/iface/Log.hh"
#include "framework/iface/Port.hh"

#include <cstdint>
#include <cstring>

using namespace framework;

struct AppPayload { std::uint32_t opcode; std::uint32_t value; };

int main()
{
    PortConfig aCfg{"gem5", "ubio", "coherence", 0, 0, 1, 1};
    PortConfig bCfg{"ubio", "gem5", "coherence", 0, 0, 1, 1};
    PortRuntime rt;
    rt.syncInterval = 2500;
    rt.linkLatency = 2500;

    Port* a = CreatePort(aCfg, rt);
    Port* b = CreatePort(bCfg, rt);
    if (!a || !b) {
        DestroyPort(a);
        DestroyPort(b);
        return 1;
    }

    Message* tx = AllocateSendMessage(a, 100); // owned；后端加入链路时延
    if (!tx) {
        DestroyPort(a);
        DestroyPort(b);
        return 2;
    }
    const AppPayload payload{7, 0x1234};
    SetMessageType(tx, MessageType::Payload);
    SetMessageTargetId(tx, 0);
    SetMessageRequestId(tx, 42);
    SetMessagePayload(tx, &payload, sizeof(payload));
    if (!SendMessage(a, tx)) { // 无论成功失败，tx 均已消费
        DestroyPort(a);
        DestroyPort(b);
        return 3;
    }

    std::uint64_t now = 0;
    ReceiveStatus status = ReceiveStatus::Empty;
    const Message* rx = nullptr;
    while (!(rx = ReceiveMessage(b, now, &status))) {
        if (status == ReceiveStatus::PendingFuture)
            now = ReceiveTimestamp(b); // 虚拟时间推进，不是 wall-clock sleep
        else
            EmitSync(b, now);          // 实际程序还应轮询其他 Port/事件
    }

    if (GetMessageType(rx) == MessageType::Payload &&
        GetMessagePayloadSize(rx) == sizeof(AppPayload)) {
        AppPayload decoded{};
        std::memcpy(&decoded, GetMessagePayloadData(rx), sizeof(decoded));

        // 转发：新建 owned destination；Copy 保留 destination timestamp。
        Message* fwd = AllocateSendMessage(a, now);
        if (fwd) {
            CopyMessage(fwd, rx);
            SetMessageTargetId(fwd, 1);
            (void)SendMessage(a, fwd); // fwd 总是被消费
        }
    }

    // rx 是 borrowed，绝不能 ReleaseMessage(rx)。
    TerminatePort(a); // best-effort notice + close
    DestroyPort(a);
    TerminatePort(b);
    DestroyPort(b);
    return 0;
}
```

若 `AllocateSendMessage` 后在发送前发生业务错误，应调用 `ReleaseMessage(tx)`；不能让 owned message 泄漏。

## 7. 本地构建、运行与后端选择

### 7.1 local 后端

```bash
# 构建/安装 local Framework 与公共头
bash scripts/build_framework.sh

# 运行接口合同测试
make -C framework clean test

# 构建全部 native 模块（默认 local）
FRAMEWORK_BACKEND=local \
FRAMEWORK_BACKEND_LIB=build/framework/lib/libframework_local.a \
  bash scripts/build_all.sh

# 构建 gem5；先确保 build/framework 下的头和 archive 已生成
FRAMEWORK_BACKEND=local \
FRAMEWORK_BACKEND_LIB=build/framework/lib/libframework_local.a \
  scons -C gem5 build/ARM/gem5.opt -j8 PROTOCOL=CHI

# 运行代表性 E2E；Linux CPU id 24-31 即按 1 起始口径的人类 CPU25-32
LOG_BASE=logs/framework_iface_final_1s_20260805 \
  taskset -c 24-31 bash tests/e2e/run_multi.sh --1s 1 3
LOG_BASE=logs/framework_iface_final_2s_20260805 \
  taskset -c 24-31 bash tests/e2e/run_multi.sh --2s 32
```

local 运行期还使用 `UBCC_IPC_DIR`（IPC 根目录）、`EP_LINK_LATENCY_PS`、
`EP_SYNC_INTERVAL_PS` 和 `EP_PORT_HWM`；它们不是后端选择变量。

### 7.2 real 后端

先独立产出 `libframework_real.a`，再让消费者选择它：

```bash
export FRAMEWORK_BACKEND=real
export FRAMEWORK_BACKEND_LIB=/opt/cc-framework/lib/libframework_real.a
export FRAMEWORK_BACKEND_CPPFLAGS='-I/opt/xxx/include -DXXX_TARGET=1'
export FRAMEWORK_BACKEND_LDFLAGS='-L/opt/xxx/lib -lxxx -ldl -pthread'

bash scripts/build_all.sh
scons -C gem5 build/ARM/gem5.opt -j8 PROTOCOL=CHI
```

仓库脚本实际读取的附加编译/链接变量是
`FRAMEWORK_BACKEND_CPPFLAGS` 和 `FRAMEWORK_BACKEND_LDFLAGS`。它们分别承担通常所说的 backend `CPPFLAGS`、`LDFLAGS`；不要只设置未加前缀的 shell `CPPFLAGS/LDFLAGS` 并假定这些脚本会自动读取。四个后端选择入口总结如下：

| 变量 | 作用 |
|---|---|
| `FRAMEWORK_BACKEND` | 后端名，默认 `local`；`real` 会形成默认名 `libframework_real.a` |
| `FRAMEWORK_BACKEND_LIB` | backend archive 的绝对路径或 workspace-relative 路径 |
| `FRAMEWORK_BACKEND_CPPFLAGS` | 目标头目录和必要宏，即 backend 专用 CPPFLAGS |
| `FRAMEWORK_BACKEND_LDFLAGS` | `libxxx.a`/系统依赖的查找与链接参数，即 backend 专用 LDFLAGS |

### 7.3 静态链接顺序

静态链接按从左到右解析。正确顺序是：

```text
模块/gem5 对象 ...  libframework_real.a  libxxx.a  xxx 的传递依赖 ... 系统库
```

local 对应：

```text
模块对象 ... libframework_local.a -lzmq -lpthread
```

即先出现引用者/包装层，再出现提供符号的 archive。循环依赖应首先从设计上消除；确有第三方循环 archive 时只对目标依赖使用链接器 group，不要把模块对象、Framework archive 和 `libxxx.a` 用 `ar` 合并成一个“大库”。**不做 archive merging**，否则会隐藏依赖、污染符号边界，并使后端替换和合同测试失真。

## 8. `libframework_real.a` 实现清单与映射

以下每项均为移植必做项；真实后端通过的标准不是“能链接”，而是与 iface 合同一致。

### 8.1 文件和包含边界

- 新后端实现 `.cc` 只包含 `framework/iface/{Message,Port,Log}.hh` 与目标 `xxx.hh`；
- **只有 real backend 实现可包含 `xxx.hh`**，native 模块、gem5 EP、protocol 头和公共 iface 均不得包含；
- archive 只导出 iface 所声明的 `framework::*` 定义，目标对象布局、句柄和辅助函数保持私有。

### 8.2 flexible-array 目标消息

若目标消息类似：

```cpp
struct xxx_message {
    xxx_header h;
    unsigned char payload[];
};
```

不要把它直接 typedef 成公共 `Message`。推荐私有包装：

```cpp
// 仅在 real backend .cc 中
struct Message {
    xxx_message* native = nullptr;
    std::size_t capacity = 0;
    bool owned = false;
};

struct Port {
    xxx_port* native = nullptr;
    Message receiveView;       // 每 Port 独立 borrowed wrapper
    xxx_message* receiveNative = nullptr;
    // pending future、最近 receive/sync 时间、拓扑映射等私有状态
};
```

具体清单：

1. **分配**：`AllocateSendMessage` 调目标 allocator，或按目标要求分配
   `sizeof(xxx_message)+GetMaxPayloadSize()`；设置 owned、capacity、source gid 和加入 link latency 后的 timestamp。失败返回 `nullptr`。
2. **借用生命周期**：每个 `Port` 持有独立 receive wrapper/target receive buffer；同 Port 下一次 receive 才可复用或归还。绝不能使用全局 singleton receive wrapper，否则另一个 Port 的 receive 会错误地使借用失效。
3. **类型映射**：建立显式双向表，把稳定值 `ControlSync=0`、`Terminate=1`、`Payload=2` 映射到目标 enum；未知目标类型应记录并丢弃/报错，不能 `static_cast` 后穿透。
4. **字段 flattening**：source、target、request id、timestamp、payload size 逐字段映射。若 `xxx.hh` 使用嵌套 union/bitfield/不同宽度，必须显式范围检查和展开；不得 memcpy 公共 Message 对象。
5. **payload 最大值**：`GetMaxPayloadSize()` 返回真实可保证值。它必须覆盖当前消费者所需消息；若目标 MTU 更小，需在后端内部可靠分片重组，不能静默截断。设置 payload 时检查容量与 null/size 合同。
6. **Copy 语义**：复制 type/source/target/request id/payload，保留 destination timestamp、target allocation、capacity 和 owned 状态；source 的 owned/borrowed 状态不变。
7. **发送消费**：`SendMessage` 在所有返回路径上释放/转交 owned target message 和 wrapper。目标 send 失败时也必须消费；若目标 API 失败后仍由调用方拥有，real backend 应在返回前自行 free。
8. **Release**：只释放尚未发送的 owned wrapper/native allocation；合同违规按 `LogAssertIf(false, ...)` 处理。
9. **终止**：显式 `Terminate` 和可选 12-byte `TerminateInfo` 正常收发；`TerminatePort` 只做 non-blocking best-effort notice 后关闭，`DestroyPort` 负责最终资源释放。不能宣称 best-effort 等于可靠通知。
10. **PDES**：实现三态 receive、future pending、每 Port receive timestamp、同步节流、溢出/回退检查、`SafeTimestamp` 和 `SyncInterval`；不得以 wall clock 超时推进 virtual timestamp。若目标库内建 PDES，仍需适配为完全相同的可观察语义。
11. **日志**：实现 `{}`、`{:x}`、`{{`、`}}`、stdout/stderr 分流、恰好一个换行和 debug env；目标 `xxx` 日志若无法满足合同，应由适配层格式化，而不是修改消费者。
12. **创建与 gid**：消费完整 `PortConfig`，验证范围，按冻结公式计算 gid，再映射到目标 endpoint/channel；无法表示的 role/channel/topology 必须让 `CreatePort` 返回 `nullptr`，不可悄悄连到默认端点。

## 9. 合同测试与 2026-08-05 验证基线

### 9.1 后端合同测试预期

任何后端都必须用同一份 `framework/tests/iface_contract_test.cc` 验证，至少覆盖：

- `TerminateInfo` 12 字节及三个字段 offset；
- 日志格式结果 `-7 ff {} str 0x1234\n`、恰好一个换行和 assert 行为；
- payload 超限、非零 null payload、同步溢出和时间回退均 SIGABRT；
- Port pair 创建、opaque allocate、metadata/payload accessors；
- `CopyMessage` 保留 destination timestamp；
- send consumption、per-Port borrowed receive storage 和 future pending；
- `ReceiveTimestamp`、`SafeTimestamp`、`SyncInterval`、同步节流及 uint64 边界；
- `ControlSync` 和 `Terminate` 通过普通 receive 可见，Terminate 立即可见；
- 最终输出精确包含 `PASS: iface contract`，进程退出码为 0。

对 real 后端执行时，应把该测试对象链接到 `libframework_real.a`，其后放
`libxxx.a` 及依赖；不得保留 local `Port.o`/`Log.o` 造成“测到错误后端”。

### 9.2 已验证命令与结果

2026-08-05 冻结基线已验证：

| 项目 | 命令/门禁 | 结果 |
|---|---|---|
| local Framework build | `bash scripts/build_framework.sh` | PASS，生成 `build/framework/lib/libframework_local.a` 和安装 iface 头 |
| Framework contract | `make -C framework clean test` | PASS，终局 `PASS: iface contract` |
| native build | `FRAMEWORK_BACKEND=local FRAMEWORK_BACKEND_LIB=build/framework/lib/libframework_local.a bash scripts/build_all.sh` | PASS，生成 `build/bin/{ubio,networksim,barrier_manager}` |
| gem5 build | `FRAMEWORK_BACKEND=local FRAMEWORK_BACKEND_LIB=build/framework/lib/libframework_local.a scons -C gem5 build/ARM/gem5.opt -j8 PROTOCOL=CHI` | PASS |
| TC1、TC3 | 在 `ubcc-dev:ubuntu20.04` 容器中以 `--cpuset-cpus 24-31` 运行 `LOG_BASE=logs/framework_iface_final_1s_20260805 bash tests/e2e/run_multi.sh --1s 1 3` | PASS |
| TC32 | 在同一容器/cpuset 中运行 `LOG_BASE=logs/framework_iface_final_2s_20260805 bash tests/e2e/run_multi.sh --2s 32` | PASS |

这里的 `24-31` 是 Linux 从 0 开始的 cpuset/CPU id，对应人工从 1 开始称呼的
**CPU25-32**。最终 E2E 证据路径：

- `logs/framework_iface_final_1s_20260805/verify_tc1.log`：`>>> TC1 PASSED <<<`；
- `logs/framework_iface_final_1s_20260805/verify_tc3.log`：`>>> TC3 PASSED <<<`；
- `logs/framework_iface_final_2s_20260805/verify_tc32.log`：`>>> TC32 PASSED <<<`。

上述是 local 后端验收基线，不代表尚未交付的 real 后端已经 PASS；real 后端必须复跑相同 build/contract/E2E 门禁。

## 10. 明确排除的遗留接口和文件

以下内容为历史、测试草案或 production-excluded，不属于当前 Framework 生产路径：

- `modules/ubiomodule/test_peer.cc`；
- pseudo `NetworkSim` 路径/文件（包括基于 `PseudoMemPort`、`PseudoManager`、`PseudoMemPacket` 的旧伪网络方案）；
- `scripts/build_modules.sh`；
- `framework/Port.hh`、`framework/MemMessage.hh` 等旧 concrete Port/MemMessage 头，以及 `Pseudo*.hh`、旧 `ZMQChannel/ZMQTransport` 头。

不得从这些文件复制 API、对象布局、线格式或构建命令到新生产代码。尤其不要包含旧
`framework/Port.hh`/`MemMessage.hh`；唯一允许的生产 include 是
`framework/iface/{Message,Port,Log}.hh`。

## 11. real 后端移植验收清单

- [ ] 消费者只包含三个 `framework/iface` 公共头；只有 real backend 包含 `xxx.hh`。
- [ ] C++17、编译器、标准库及 ABI 选项与所有消费者兼容，并明确未宣称 C ABI。
- [ ] `libframework_real.a` 独立产出，未与模块对象、local backend 或 `libxxx.a` 合并。
- [ ] static link 顺序为消费者对象 → real backend → `libxxx.a` → 传递/系统依赖。
- [ ] Message owned/borrowed、每 Port 借用期限、Copy 保留 dst timestamp、Send/Release 消费语义全部满足。
- [ ] 稳定 MessageType、12-byte `TerminateInfo`、payload 上限和字段 flattening 有显式映射/检查。
- [ ] 所有 canonical PortConfig 和完整拓扑正确映射，gid 由后端计算，失败返回 `nullptr`。
- [ ] Receive 三态、普通可见 control、Terminate 立即可见和 PDES 四个 API 无 wall-clock 推进。
- [ ] TerminatePort 为 best-effort，Destroy 资源语义清楚；可靠关闭另有应用层 ack。
- [ ] 日志格式、stream、debug env、单换行及 parser 约束满足。
- [ ] 同一份 iface contract test 在 real backend 上输出 `PASS: iface contract`。
- [ ] native、gem5 均使用 real backend 构建成功；TC1、TC3、TC32 在 CPU25-32 对应 cpuset 上复跑 PASS，并归档完整日志。
