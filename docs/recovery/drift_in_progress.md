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
