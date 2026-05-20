# M2 Domain Isolation Verification (v2)

本文档记录 M2 阶段全部 8 个审计 Testcase 的真实验证状态。v2 修正了之前将静态分析写成测试通过的夸大声明。

---

## TC1: Node-local normal PA

**要求**: Node0 的 core 访问本地 normal PA，Node1 不应收到 ordinary CHI message。

**验证方式**: `setDownstream()` 静态代码检查 + 运行时验证。RN-F 严格按 `_node_id` 过滤下游，过滤后数量 ≠ 1 则 fatal。`_make_downstream_check_hook()` 在拓扑创建前实际执行下游验证。

**关键代码**:
- `gem5/configs/ruby/CHI_multi_node_config.py:168-174` — `setDownstream()` 强制过滤 + 数量断言
- `gem5/configs/ruby/CHI_multi_node_config.py:30-46` — `_make_downstream_check_hook()` 运行时验证
- `gem5/configs/ruby/CHI_multi_node_config.py:218-228` — hook 注册（`generate()` 内）

**运行证据**: `reports/m2_sim_output.log` 显示 `M2: Downstream isolation check passed (2 RN-Fs)`；模拟正常退出 @ tick 235517500，exit code 0。

**判定**: PASSED — 下游过滤在配置和运行时均被验证执行。

---

## TC2: 双 node 并发 local-normal

**要求**: Node0 与 Node1 同时访问各自 local-normal PA，互不串扰。

**验证方式**: 4 核通过 `--cmd="bin;bin;bin;bin"` 创建 4 个独立进程，每核执行 `m2_concurrent.c`。stats 中 `numMemRefs` 验证所有核均产生内存引用。

**关键代码**:
- `tests/ubcc/m2_concurrent.c` — 并发 workload
- `tests/ubcc/run_m2_suite.sh:61-69` — `--cmd` 分号分隔创建独立进程
- `tests/ubcc/run_m2_suite.sh:103-116` — per-core memRefs 检查

**运行证据**: stats.txt 显示 cpu0-3 各 `numMemRefs=25310`。模拟 exit code 0。

**判定**: PASSED — 所有 4 核同时执行有效负载，memRefs > 0 已验证。

---

## TC3: 三 node 并发 local-normal

**要求**: 所有 node 同时压测，checker 零告警。

**验证状态**: N=2 已验证。N=4 可行（同为 2 的幂）。N=3 受限于 `Ruby.py:setup_memory_controllers` 的 `int(math.log(num_dirs, 2))` 要求 2 的幂。多节点逻辑支持任意 N，约束在 gem5 基础设施层。

**判定**: NOT YET VERIFIED — N=2 已通过；N=4 可测试但未执行；N=3 需修改 `setup_memory_controllers`。

---

## TC4: DSM 同地址反例

**要求**: 不同 node 访问同一 DSM global PA，验证不会被 Ruby 路由到错误 HN-F。

**验证状态**: 严格下游过滤保证 RN-F 只能发往同节点 HN-F。每个 HN-F 处理完整内存范围（`createAddrRanges` 全范围）。但当前没有构造专门 testcase 让两个节点访问同一地址。DSM 场景需要 M5+ UBCC 支持。

**关键代码**:
- `gem5/configs/ruby/CHI_multi_node_config.py:237-244` — `createAddrRanges()` 全内存范围
- `gem5/configs/ruby/CHI_multi_node_config.py:168-174` — RN-F 严格下游过滤

**判定**: NOT YET VERIFIED — 代码路径存在；无专门 DSM testcase。需要 M5+。

---

## TC5: RN-F downstream 检查

**要求**: 启动时检查每个 RN-F 的 downstream 目的地仅包含同 node HN-F。

**验证方式**: `_make_downstream_check_hook()` 在拓扑创建前执行，遍历所有 RN-F 的 `_ll_cntrls` 的 `downstream_destinations`，检查每个目标 `_node_id` 与 RN-F 相同。违反则 `fatal()`。此 hook 由 `MultiNodeCHI_RNF.generate()` 自动注册并通过 `CHI.py` 的 `_chi_post_hook` 调用。

**关键代码**:
- `gem5/configs/ruby/CHI_multi_node_config.py:30-46` — 下游检查 hook
- `gem5/configs/ruby/CHI_multi_node_config.py:218-228` — hook 注册
- `gem5/configs/ruby/CHI.py:250-253` — hook 调用点

**运行证据**: `M2: Downstream isolation check passed (2 RN-Fs)` 出现在仿真输出中。

**判定**: PASSED — checker 被实际调用且通过。

---

## TC6: HN-F downstream 检查

**要求**: HN-F downstream 仅包含同 node SN-F/EP-SNF。

**验证状态**: 当前实现 `MultiNodeCHI_HNF.setDownstream()` 保留全部 SN-F（`super().setDownstream(cntrls)`），不做过滤。原因是 gem5 内存交错要求 HN-F 能访问任意 SN-F 来处理不同地址。此行为文档化。

**关键代码**:
- `gem5/configs/ruby/CHI_multi_node_config.py:267-270` — HN-F setDownstream（全 SN-F）

**判定**: NOT YET VERIFIED — HN-F 下游未过滤；RN-F→HN-F 隔离是主防线。当 EP-SNF 引入（M3+）后收紧。

---

## TC7: 跨 node ordinary message 负例

**要求**: 人工构造错误配置，checker 必须触发断言。

**验证方式**: RN-F `setDownstream()` 在过滤后检查数量为 1，否则 fatal。HN-F 下游检查在 `validate_downstream_isolation()` 中（但该函数未被调用，其逻辑已合并到 `_make_downstream_check_hook()` 中仅检查 RN-F 侧）。跨节点 HN-F 下游违反目前不会在启动时 fatal。

**判定**: PARTIAL — RN-F 侧有 fatal 防护；HN-F 侧未覆盖。

---

## TC8: 非空闲 node workload

**要求**: 所有 node 执行最小有效 payload，禁止直接退出。

**验证方式**: `m2_concurrent.c` 所有核执行相同逻辑（4096 字 × 50 迭代 + streaming 阶段）。stats 中 cpu0-3 的 `numMemRefs` 均 > 0。`run_m2_suite.sh` 显式检查每核 memRefs。

**关键代码**:
- `tests/ubcc/m2_concurrent.c:28-53` — 所有核统一执行路径
- `tests/ubcc/run_m2_suite.sh:103-116` — per-core memRefs > 0 检查

**运行证据**: cpu0-3 各 `numMemRefs=25310`。

**判定**: PASSED — 所有核执行有效负载。

---

## 汇总

| TC | 状态 | 证据 |
|----|------|------|
| 1 | PASSED | `setDownstream()` fatal + `_make_downstream_check_hook()` 实际调用 |
| 2 | PASSED | 4 核各 25310 memRefs, exit code 0 |
| 3 | NOT YET | N=2 通过；N=3 受限於 gem5 目录交错 |
| 4 | NOT YET | 代码路径存在；DSM testcase 需要 M5+ |
| 5 | PASSED | `_make_downstream_check_hook()` 实际调用并输出通过 |
| 6 | NOT YET | HN-F downstream 未过滤（已文档化） |
| 7 | PARTIAL | RN-F fatal；HN-F 侧未覆盖 |
| 8 | PASSED | 4 核 memRefs > 0，经脚本检查 |
