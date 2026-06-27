# Drift In Progress

## 2026-06-23

- 集成 `framework::Port` 到 `UBAdapter` 的可选传输路径：
  - 新增 `framework::Port* _port` 成员，默认 `nullptr`。
  - 新增 `setPort()/port()` 注入接口（UBAdapter 不自行创建 Port）。
  - 新增 `transportSend()/transportRecv()`，当 `_port` 可用时走 `MemMessage(COH_MSG)`，否则保留 `_router->sendMessage()` 原路径。
- 更新 `gem5/src/mem/ruby/protocol/chi/ep/SConscript`：
  - 添加 `framework/` 与 `thirdparty/zeromq/include` 头文件搜索路径。
  - 编译 `framework/Port.cc`。
  - 链接 `libzmq`、`-pthread`，并设置 ZMQ 库目录的 rpath。

## 2026-06-24

- 修复 `tools/ubio/ubio_main.cc` 的 networksim 连接约束：
  - `--net-ep` 继续强制走 connect 模式（`bind=false`）。
  - 新增 endpoint 规范校验，要求格式为 `ipc:///tmp/networksim_m{node}_p1`。
- 开始拆解 `EPBackend` ↔ `UBCCController` 头文件耦合：
  - 新增 `UBCCProtocolIF.hh` 抽象接口与 `UBCCRegistry.{hh,cc}` 本地实例注册层。
  - `EPBackend.hh` 移除对 `UBCCController.hh` 的直接包含，改依赖抽象接口。
  - `UBAdapter` / `UBIOModule` 改为依赖 `UBCCProtocolIF`，不再需要 `UBCCController.hh`。
- 在 `gem5/src/mem/ruby/protocol/chi/ep/SConscript` 中加入 standalone `tools/ubio/ubio` 构建目标，链接 `modules/ubiomodule` 的 standalone UBCC 源。

## 2026-06-27

- TC2 TIMEOUT 诊断（仅 workload 允许改动）进行了 3 轮迭代验证：
  1. 非 primary CPU 从 `sync_wait` 改为直接 `_exit_program(0)`；TC2 仍 TIMEOUT（300s）。
  2. 将“写者/读者”角色临时互换（Node1 写、Node0 读）以绕开 Node0→Node1 写路径；TC2 仍 TIMEOUT（300s）。
  3. 结合 `ubio_n1/stderr.log` 与 `nsim.log` 复核：`processOuterRequest` 进入 `existing outstanding ... stage=4 — BUSY` 后持续重试，未见完成闭环。
- 以上 workload 试改已回退，`tests/e2e/workloads/e2e_tc2_remote_read.c` 当前恢复到仓库基线版本（无持久代码漂移）。
