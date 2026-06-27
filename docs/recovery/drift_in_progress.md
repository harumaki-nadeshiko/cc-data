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

## 2026-06-28

- TC2 链路挂起诊断新增：定位 `modules/networksim/networksim_main.cc` 的 `NetworkSim::step()` 在高压输入下存在“无限 drain”风险。
  - 现象：`nsim` 日志仅见 `[NSIM-RECV]`，几乎不出现 `[NSIM-FWD]`；`ubio_n1` 无 `net recv`。
  - 根因：`while (p->recv())` 无上限，单端口持续有包时会长期停留在接收阶段，导致同一 tick 的 FIFO 出队/转发阶段得不到执行。
  - 修复：为每个端口每 tick 增加接收预算 `kRecvBudgetPerPortPerTick=256`，超预算后进入下一阶段并打点 `[NSIM-BUDGET]`。

- UBIO ReadReq 转发链路诊断增强（仅加日志，无逻辑改动）：
  - 文件：`tools/ubio/ubio_main.cc`
  - 在 `pollAndProcess` 增加 `[UBIO-RR-PATH]` 日志，明确区分：
    1) 是否进入 `dstNode != nid`；
    2) `isDsmAddr` 判定结果及 `!isDsmAddr` 检查是否通过；
    3) 调用 `sendCoh` 后返回 true/false；
    4) `netPort` 为空时的分支。
  - 在 `sendCoh` 增加 `[UBIO-RR-SEND]` 日志，明确失败原因：
    - `sendAllocateBuffer` 返回 null；
    - `port->send` 失败；
    - 以及 `no_port`/`setPayload_fail` 辅助信息。

- TC2 Clear 全链路诊断增强（仅加 `fprintf(stderr, ...)`，无逻辑改动）：
  - `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc`
    - 在 `EPBackend::sendClear()` 增加 `[CLEAR-SEND]`，打印 `pa/homeNode/epoch/reqId`。
  - `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc`
    - 在 `sendClearReq()` 增加 `[CLEAR-ADAPTER]`，打印 `transportSend` 返回值。
    - 在 `recvFromRouter(ReadResp)` 增加 stderr 版 `[ADAPTER-GOT-RESP]`（用于判定 grant 抵达 N0）。
    - 在 `recvFromRouter(ClearResp)` 与 `handleResponse(ClearResp)` 增加 `[CLEAR-RESP]`。
  - `tools/ubio/ubio_main.cc`
    - 在 `sendCoh()` 对 `ClearReq/ClearResp` 增加 `[UBIO-CLEAR] send`。
    - 在 `pollAndProcess()` 对 `ClearReq/ClearResp` 增加 `[UBIO-CLEAR] recv`。
    - 在 `handleUbccMessage(ClearReq)` 增加 `[UBIO-CLEAR] ubcc-enter/ubcc-exit`。
  - `modules/ubiomodule/UBCCController.cc`
    - 在 `processClear()` 增加 `[UBCC-CLEAR]` 入口、各类 drop 原因、accept 结果日志。

- UBAdapter request/response 接口异步化（本次仅限定文件改动）：
  - 文件：`gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.hh`
    - 将 `sendWritebackReq/sendEvictReq/sendUpgradeReq/sendUpgradeDoneReq` 返回类型从 `bool` 改为 `int`。
  - 文件：`gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc`
    - 将 `sendWritebackReq/sendEvictReq/sendUpgradeReq/sendUpgradeDoneReq/sendQueryLineMetaReq` 按 `sendReadReq/sendClearReq` 模式改为：
      1) 先查 `_lastResponse` 缓存；
      2) 发送后在 `_port` 路径返回 `-2`（pending）并 `scheduleResponseCheck()`；
      3) 非 `_port` 路径保留 `transportRecv` 同步兜底；
      4) 统一 `-1` 表示错误，`0/1` 表示拒绝/接受（或查询 found/not-found）。
  - 文件：`gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc`
    - 适配以上接口，新增 `-2` 分支处理与诊断日志：
      - Writeback/Evict：区分 `pending` 与 `accepted`，避免把 pending 直接当 hard-fail；
      - Upgrade：`-2` 时打印 `[EP-UPGRADE-PENDING]` 并返回 `false` 等待上层重试；
      - UpgradeDone：`-2` 时打印 `[EP-UPGDONE-PENDING]`，返回 pending-success 语义；
      - QueryLineMeta：在 `handleWriteback/sendHomeWritebackNotify` 中识别 `-2` 并打印 pending 诊断。
