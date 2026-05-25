# UBCC Basic Framework 修复报告 #8（3-node / No Bypass）

生成时间: 2026-05-25  
执行人: OpenCode  
目标: 按用户要求完成 `N=3`、无 bypass、`L_SNF/DL_SNF` 使用可建模延迟的 DRAM backstore，`EP_SNF` 截获请求返回假数据，`EP_RNF` 仅保证接入。

---

## 1. 实施结论

本轮已完成并验证：

1. **去除 testcase 层 bypass 依赖**：`tests/phase2/test_ruby_create_system_n3l2d2.py` 不再 monkey-patch `setup_memory_controllers`。  
2. **3-node 拓扑可真实创建并 instantiate**：TC4 实测 `9/9 PASS`。  
3. **L_SNF / DL_SNF 绑定 DRAM backstore**：每个 node 各 1 个 `L_SNF` backstore（256MB）+ 1 个 `DL_SNF` backstore（128MB），均为 `MemCtrl + DDR4_2400_8x8`。  
4. **EP_SNF 按阶段语义处理请求**：允许 DSM 地址进入 EP proxy 路径并返回 fake data；不要求本地 backing memory。  
5. **EP_RNF 仅接入拓扑**：在 TC4 中验证对象存在与拓扑连接，不强行测行为细节。

---

## 2. 关键代码修改

### 2.1 DRAM backstore 接入（L_SNF / DL_SNF）

文件: `gem5/configs/ruby/CHI_ubcc_framework.py`

改动:

1. 新增 `_make_dram_memctrl(addr_range)`，使用 `DDR4_2400_8x8 + MemCtrl`。  
2. 每个 node:
   - `L_SNF_i` 绑定 `AddrRange(local_private + ubcc_exclusive)` 的 DRAM backstore。
   - `DL_SNF_i` 绑定 `DSM_i` 的 DRAM backstore。
3. 对应 `MemCtrl` 显式挂到 `system`（`l_snf_memctrl_node{i}` / `dl_snf_memctrl_node{i}`），用于可追踪和测试验证。


### 2.2 EP_SNF 地址检查语义调整

文件:

- `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh`
- `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc`
- `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc`

改动:

1. `EPBackend` 新增 `checkDsmAddr(pa)`：只校验地址属于本 node 视图 DSM 窗口，不再强制 home==local node。  
2. `EPSNFController::recvRequestMsg/recvSnoopMsg` 改用 `checkDsmAddr()`。  

效果:

- `EP_SNF` 可作为 remote DSM proxy 截获请求并回 fake data，不会被“local-only”检查误杀。


### 2.3 无目录控制器时的 Ruby 通用兼容处理

文件: `gem5/configs/ruby/Ruby.py`

改动:

1. `setup_memory_controllers()` 增加早退：`if len(dir_cntrls) == 0: return`。

原因:

- UBCC 路径已由协议侧直接绑定 backstore，不走 Ruby 通用目录内存构建器。
- 避免 `system.mem_ctrls = []` 导致的 `AttributeError: Invalid assignment for Class System with parameter mem_ctrls`。

---

## 3. TC4（无 bypass）改造

文件: `tests/phase2/test_ruby_create_system_n3l2d2.py`

改动要点:

1. 删除所有 monkey patch / bypass 逻辑。  
2. 保持 `N=3, L=2, D=2`，真实调用 `Ruby.create_system`。  
3. 补上 Ruby CPU 端口连接：`ruby._cpu_ports[i].connectCpuPorts(cpu)`，避免 `Unconnected port` fatal。  
4. 新增 DRAM backstore 检查项（`L_SNF/DL_SNF` 每 node 均存在 `MemCtrl.dram` 且类型为 `DDR4_2400_8x8`）。  
5. 最终执行 `m5.instantiate()` 并纳入 PASS/FAIL。

---

## 4. 测试结果

### 4.1 本轮核心目标测试（全部通过）

1. **TC4 no-bypass bring-up**

```bash
docker run --rm --network none -v /mnt/data2/cgc/cc-ep:/workspace -w /workspace/gem5 \
  ubcc-dev:ubuntu20.04 \
  bash -lc './build/ARM/gem5.opt ../tests/phase2/test_ruby_create_system_n3l2d2.py ../tests/phase1/hello.arm'
```

结果:

- `TOTAL: 9/9 tests passed`
- `TC-BRINGUP: m5.instantiate(): PASS`
- 退出码 `0`


2. **TC2 增强版（已提前落地）**

文件: `tests/phase1/run_phase1_test_enhanced.py`  
结果: `12/12 PASS`, 退出码 `0`


### 4.2 回归验证（通过）

1. `tests/phase1/test_pa_layout_mode.py` -> `48/48 PASS`  
2. `tests/phase2/verify_topo_objects.py` -> `101/101 PASS`  
3. `tests/phase3/test_ep_instantiate.py` -> `INSTANTIATE OK`

---

## 5. 当前语义状态对照

1. `L_SNF_i` / `DL_SNF_i`: **真实 DRAM backstore**（满足本阶段要求，并可用于后续延迟建模）。  
2. `EP_SNF_i`: **proxy 截获 + fake data 响应**（符合“本阶段不要求 remote backing memory”的约束）。  
3. `EP_RNF_i`: **已接入拓扑**（本阶段不测试其完整行为语义）。

---

## 6. 已知事项

运行时会看到 DRAM 容量告警（`DDR4_2400_8x8` 设备默认容量大于当前分配 range），属于可预期 warning，不影响本轮功能验证。后续可通过自定义 DRAM 参数或专门的 smaller interface 消除。
