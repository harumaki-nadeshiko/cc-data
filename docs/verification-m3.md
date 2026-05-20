# M3 EP-RNF/EP-SNF Skeleton Verification (v2)

本文档记录 M3 阶段全部 5 个审计 Testcase 的真实验证状态。v2 修正了夸大声明。

---

## TC1: EP 控制器拓扑接线

**要求**: 启动后检查 EP-RNF/EP-SNF 已进入 `RubySystem` 和 network nodes。

**验证方式**: 
- `--enable-ep-controllers` 标志已在 `gem5/configs/ruby/CHI.py:53-55` 注册
- `_make_ep_post_hook()` 在拓扑创建前通过 `_chi_post_hook` 被调用
- 该 hook 创建每节点 EP-RNF + EP-SNF，分配 MessageBuffer，连接 network in/out ports，加入 `network_cntrls` 和 `all_cntrls`

**关键代码**:
- `gem5/configs/ruby/CHI.py:53-55` — 参数注册
- `gem5/configs/ruby/CHI_multi_node_config.py:48-85` — `_make_ep_post_hook()` 创建+接线
- `gem5/configs/ruby/CHI_multi_node_config.py:218-228` — hook 注册（`generate()` 内）
- `gem5/configs/ruby/CHI.py:250-253` — hook 调用点

**运行结果**: 启用 `--enable-ep-controllers` 后，hook 成功创建 EP 控制器并加入 topology，但在 m5.instantiate() 阶段发生 segfault（Python C API 崩溃）。

**已知问题**: EP 控制器使用 `MachineType_Cache`，可能与 SLICC 生成的 Cache_Controller 产生 MachineID 冲突。`initNetQueues()` 使用 `MachineType_base_number(MachineType_Cache)` 计算队列索引，多个 Cache 类型控制器共用可能导致冲突。该问题需要分配独立 MachineType 或在 C++ 层面修复。

**判定**: PARTIAL — 集成机制完整（参数注册 → hook 调用 → 控制器创建+接线 → topology 注册）；运行时因 MachineID 冲突崩溃。

---

## TC2: HN-F snoop EP-RNF

**要求**: HN-F 向 EP-RNF 发出 snoop，验证 EP-RNF 实际收包。

**验证方式**: EP-RNF 的 `recvSnoopMsg()` → `sendSnoopResp(Comp_I)` 路径完整（静态代码检查）。实际触发需要 HN-F directory 包含 EP-RNF MachineID（M4 sentinel registration）。

**关键代码**:
- `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:49-55` — snoop 接收
- `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:70-91` — Comp_I 响应发送

**判定**: NOT YET VERIFIED — 收发代码路径存在；真实 HN-F→EP-RNF snoop 需要 M4。

---

## TC3: HN-F miss 到 EP-SNF

**要求**: DSM Remote miss 触发 `ReadNoSnp` 到 EP-SNF，验证 EP-SNF 实际收包。

**验证方式**: EP-SNF 的 `recvRequestMsg(ReadNoSnp)` → `handleReadNoSnp()` → `sendFakeDataResp(Comp_UC + DataBlock)` 路径完整（静态代码检查）。实际触发需要 HN-F 路由 DSM Remote 地址到 EP-SNF（M5）。

**关键代码**:
- `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc:69-80` — ReadNoSnp 识别
- `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc:97-140` — Comp_UC + Data 发送

**判定**: NOT YET VERIFIED — 收发代码路径存在；真实 HN-F→EP-SNF ReadNoSnp 需要 M5。

---

## TC4: node_id 追踪

**要求**: 所有 EP 收发日志都必须带 `node_id`。

**验证方式**: EP-RNF 和 EP-SNF 的 SimObject 参数包含 `node_id`（`EPController.py:12,19`）。C++ 构造函数存储为 `const int nodeId`（`EPRNFController.hh:32`）。`getNodeId()` 可供查询。debug 日志可通过 `m_version` 映射到 `node_id`。

**已知不足**: 当前 debug 日志只打印 `m_version`，未直接打印 `node_id`。需要在 `DPRINTF` 中添加 `nodeId` 输出。

**判定**: PARTIAL — `node_id` 参数存在且可在 C++ 中访问；debug 日志暂未直接包含。

---

## TC5: 非法未接线负例

**要求**: 如果 EP 未接进 topology，测试必须失败。

**验证方式**: EP 控制器通过 `_make_ep_post_hook()` 统一创建和接线，不存在"创建但不接线"的代码路径。当 TC1 的 segfault 修复后，若有人绕过 hook 创建 EP 但不接线，MessageBuffer 缺少 `out_port`/`in_port` 导致消息无法发送。但目前没有专门的"未接线应 fatal"测试。

**判定**: NOT YET VERIFIED — 无专门负例测试；设计层面由 hook 统一管理。

---

## 汇总

| TC | 状态 | 证据 |
|----|------|------|
| 1 | PARTIAL | 集成机制完整；运行时 segfault (MachineID 冲突) |
| 2 | NOT YET | 收发代码路径存在；需要 M4 sentinel 触发 |
| 3 | NOT YET | 收发代码路径存在；需要 M5 DSM Remote routing |
| 4 | PARTIAL | `node_id` 参数存在；debug 日志缺少直接输出 |
| 5 | NOT YET | 无专门负例测试 |

## 已知事项

1. `--enable-ep-controllers` 参数已注册（`CHI.py:53-55`），运行时可正确加载配置并创建 EP 控制器。
2. EP 控制器在 m5.instantiate() 阶段崩溃（MachineID/initNetQueues 冲突），需分配独立 MachineType 或在 C++ 层面修复。
3. TC2/TC3 的真实 CHI message 触发需要后续阶段（M4 sentinel registration, M5 DSM Remote routing）。
