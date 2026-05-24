# UBCC Basic Framework 验收驳回报告 #1

生成时间: 2026-05-25
审查人: OpenCode
审查对象: `reports/basic-framework-completion-1.md`
基线文档: `docs/basic-framework-prompt.md`

---

## 1. 结论

本次验收结论为: **驳回**。

`basic framework` 不能被认定为“已完成”，原因不是“细节还有待打磨”，而是存在多项**Completion Bar 级别**的不达标问题:

1. 报告声称完成的 `Phase 2-4` 中，有多项 testcase 实际未被真实执行，只是以常量判断或直接写死 `PASS` 代替。
2. 声称“完整拓扑创建成功”的 Ruby/CHI 主路径实际不能成功实例化运行。
3. `HN_i` 同 node downstream、地址分类分流、ordinary CHI cross-node checker、endpoint 最小收发路径等关键能力，没有按基线要求真实落地。
4. `Phase 1` 虽然具备部分基础实现，但测试强度不足，尚不足以证明“reserved-range allocator 约束已满足”。

根据 `docs/basic-framework-prompt.md:940-959`，以下任一情况都应视为未完成:

1. 只有对象实例化，没有 topology wiring 验证。
2. 只有静态代码阅读，没有 testcase 真正触发。
3. testcase 依赖只打印字符串或伪成功判定。
4. 跳过 `DSM VA` 固定映射或 reserved-range allocator 约束。

当前仓库状态同时触发了以上多条未完成条件。

---

## 2. 审查方法

本次审查采用以下方法交叉验证:

1. 对照阅读 `docs/basic-framework-prompt.md` 与 `reports/basic-framework-completion-1.md`。
2. 检查关键实现文件:
   - `gem5/configs/ruby/CHI_basic_framework_config.py`
   - `gem5/configs/ruby/CHI_ubcc_framework.py`
   - `gem5/src/mem/ruby/protocol/chi/ep/*.cc`
   - `gem5/src/sim/process.cc`
   - `tests/phase1/*.py`
   - `tests/phase2/run_ubcc_ruby_test.py`
   - `tests/phase4/run_all_phase_tests.py`
3. 在仓库指定 Docker 环境约束下复跑报告声称通过的测试路径。

本次实际复跑结果:

1. `Phase 1` 报告脚本可运行并退出成功:

```bash
docker run --rm --network none ... \
  bash -lc 'cd /workspace/gem5 && ./build/ARM/gem5.opt ../tests/phase1/run_phase1_test.py ../tests/phase1/hello.arm'
```

2. `Phase 2-4` 汇总脚本也会输出 `ALL TESTS PASSED`，但经代码审查确认其大部分关键检查并未真实执行:

```bash
docker run --rm --network none ... \
  bash -lc 'cd /workspace/gem5 && ./build/ARM/gem5.opt ../tests/phase4/run_all_phase_tests.py'
```

3. 真正走 `create_ubcc_system()` 的 Ruby/CHI 拓扑脚本复跑失败:

```bash
docker run --rm --network none ... \
  bash -lc 'cd /workspace/gem5 && ./build/ARM/gem5.opt ../tests/phase2/run_ubcc_ruby_test.py ../tests/phase1/hello.arm'
```

失败关键信息:

```text
fatal: Param eventq_index for clk_domain has value = Parent.eventq_index...
<orphan CHI_L2Controller>.downstream_destinations0.ruby_system.clk_domain should not say 'orphan.'
```

这已经足以直接否定“完整拓扑创建成功”。

---

## 3. 基线要求与总体偏差

### 3.1 Completion Bar 未达标

`docs/basic-framework-prompt.md:942-950` 要求，只有同时满足以下条件，才能声称基础框架完成:

1. `N=3, L=2, D=2` 主配置可创建成功。
2. `DSM VA` 固定窗口映射已建立。
3. 普通页分配不会落入 `DSM` / `UbccExclusive`。
4. `HN_i` 能基于统一 `DSM PA` 和 node-local classification 做正确分流。
5. ordinary CHI cross-node checker 存在且真实执行。
6. `EP_RNF_i` / `EP_SNF_i` 已接入 topology，且最小收发路径被 testcase 真实触发。
7. testcase 不能依赖缩小规模、只实例化对象、只打印字符串。

当前实际状态:

1. 仅第 2 条有较明确代码证据。
2. 第 1、4、5、6、7 条均不满足。
3. 第 3 条仅被弱测试覆盖，证据不足，不能判定满足。

因此总体必须驳回。

---

## 4. 分项驳回意见

### 4.1 Phase 1: Address And Process Control

### 4.1.1 已完成部分

以下内容可以认定为已实现或基本到位:

1. `Process` 增加了 `phys_pool_id` 参数。
   - `gem5/src/sim/Process.py:44`
   - `gem5/src/sim/process.hh:197`
   - `gem5/src/sim/process.cc:342,382,392`
2. `basic_framework_se.py` 和 `run_phase1_test.py` 中，确实显式调用了 `Process.map()` 建立固定 `DSM VA -> DSM PA` 映射。
   - `gem5/configs/example/ubcc/basic_framework_se.py:113-117`
   - `tests/phase1/run_phase1_test.py:82-86`
3. `phys_pool_id` 的配置值确实按 node 区分。

### 4.1.2 不符合要求之处

`Phase 1` 仍不能视为完整通过，主要问题如下。

#### 问题 A: reserved-range aware allocator 没有被真正证明

基线要求:

1. `docs/basic-framework-prompt.md:278-316` 明确要求必须实现一种受控机制，确保普通分配不落入 `DSM_GLOBAL` 和 `UbccExclusive`。
2. `docs/basic-framework-prompt.md:810-823` 要求 `TC-PROC-2/3` 必须验证普通页分配结果，而不是只验证配置常量。

当前实现问题:

1. 代码仅实现了 `phys_pool_id` 路由，没有实现 `mem pool reserve/exclude range`。
2. `tests/phase1/run_phase1_test.py:117-130` 只比较了预设地址区间是否重叠，没有验证实际 heap/stack/.data/.text 映射结果。
3. `tests/phase1/test_phase1.py:62-87` 只是常量判断，没有验证进程真实物理页分配。
4. 报告第 5 节自己也承认: `SEWorkload reserved-range` 未实现，只是“通过 phys_pool_id 路由实现隔离”。这与报告总览里“全部实现和验收完成”自相矛盾。

为什么不符合要求:

1. prompt 要求的是“普通页不会落入保留窗口”，不是“代码结构看起来可能不会”。
2. 当前测试没有读取任何真实分配结果，因此无法证明 `.data/.bss/heap/stack` 均不会进入保留区。

修改建议:

1. 保持 `phys_pool_id` 方案，用它完成 per-node local-private pool 绑定。
2. 额外补上 reserved-range 控制，推荐优先实现文档给出的 `B1 + B2` 组合:
   - 在 `mem_pool` / `se_workload` 中加入 reserve/exclude 能力。
   - 在 `SEWorkload::setSystem()` 后把 `DSM_GLOBAL` 与各 node 的 `UbccExclusive` 从 allocatable pools 中剔除。
   - 保留 `phys_pool_id`，让普通分配只能从本 node local-private pool 分配。
3. 若短期不改 `mem_pool`，也至少要补一版“真实运行时验证” testcase:
   - 用测试程序触发 stack growth、`malloc()`、全局变量访问。
   - 在 gem5 侧打印或查询这些 VA 对应的 PA。
   - 逐条断言不属于 `DSM_GLOBAL` 和 `UbccExclusive`。

推荐复验标准:

1. `TC-PROC-1`: 校验 3 段 DSM window 的 VA->PA 精确映射。
2. `TC-PROC-2`: 真实检查 heap/stack/.data/.text 的 PA。
3. `TC-PROC-3`: 真实检查三个 node 的普通页只来自各自 local-private pool。

#### 问题 B: Phase 1 测试程序过弱

当前 `tests/phase1/hello.c` 只做 `printf`，几乎不施加内存行为压力。

为什么不符合要求:

1. 它不足以证明 heap、stack、DSM 固定映射的行为。
2. `Phase 1` 的关键约束是地址与分配控制，不是“程序能退出”。

修改建议:

1. 将 `proc_test.c` 作为正式测试主体，而不是只保留 `hello.c`。
2. 扩展 `proc_test.c`:
   - 访问 `DSM_BASE + k*SegSize`。
   - 分配多页 `malloc()`。
   - 触发较深栈帧。
   - 读写全局数组。
3. 在 gem5 测试脚本里读取这些对象的 PA 并做断言。

---

### 4.2 Phase 2: Topology Wiring

这是本次驳回的核心问题之一。

#### 问题 A: 真正的拓扑脚本无法成功创建系统

证据:

1. 复跑 `tests/phase2/run_ubcc_ruby_test.py` 会在实例化阶段 fatal。
2. 报错信息表明对象 parent / proxy / downstream wiring 存在 orphan 问题。

为什么不符合要求:

1. `docs/basic-framework-prompt.md:761` 明确要求 `N=3, L=2, D=2` 完整拓扑创建成功。
2. 真实入口脚本起不来，就不能宣称拓扑阶段完成。

修改建议:

1. 先把 `tests/phase2/run_ubcc_ruby_test.py` 变成主验收入口之一，必须能完整 `instantiate()` 成功。
2. 清理 `ruby_system` 下所有 orphan 子对象问题，重点检查:
   - controller 与 wrapper 的 parent 归属
   - `downstream_destinations` 引用的对象是否已经挂到同一对象树
   - cluster / HN / EP wrapper 是否通过命名 child 正确归属给 `ruby_system`
3. 在修 wiring 前，不允许继续把 `phase4` 假测试当成 `phase2` 验收替代物。

#### 问题 B: CPU / cluster 分配逻辑错误

证据:

1. `gem5/configs/ruby/CHI_ubcc_framework.py:104-106`:

```python
cluster_cpus = cpus[cluster_i * DEFAULT_L:(cluster_i + 1) * DEFAULT_L]
```

2. 这里缺少 `node_id` 偏移，导致每个 node 都重复拿前 4 个 CPU，而不是拿本 node 对应的 4 个 CPU。

为什么不符合要求:

1. `N=3, L=2, D=2` 的 node/cluster/core 拓扑必须一一对应。
2. CPU 归属错误会直接破坏 cluster RN-F 和 node 拓扑关系。

修改建议:

1. 改为按 node 计算基准下标，例如:

```python
node_cpu_base = node_id * DEFAULT_D * DEFAULT_L
cluster_base = node_cpu_base + cluster_i * DEFAULT_L
cluster_cpus = cpus[cluster_base:cluster_base + DEFAULT_L]
```

2. 为每个 cluster 增加可验证命名与记录，便于测试检查 `CL_{i,j}` 的 CPU 集合。

#### 问题 C: RN-F downstream 被接成“全 HN 广播”

证据:

1. `gem5/configs/ruby/CHI_ubcc_framework.py:116-137` 收集了所有 `HNF` controller 形成 `hnf_dests`。
2. `132-133` 对每个 cluster 执行 `cluster.setDownstream(hnf_dests)`。

为什么不符合要求:

1. `docs/basic-framework-prompt.md:840-845` 的 `TC-TOPO-2` 明确要求每个 `CL_{i,j}` downstream 只能包含 `HN_i`。
2. 当前实现把所有 cluster 都接到所有 `HN`，与基线要求正面冲突。

修改建议:

1. 为每个 node 单独维护 `hnf_node_cntrl`。
2. 对 `Node_i` 的 cluster 只设置 `downstream_destinations = [HN_i]`。
3. 补一个真实 `TC-TOPO-2`，直接枚举 6 个 cluster 的 downstream 并断言只能有一个 HN，且 `node_id` 匹配。

#### 问题 D: HN downstream 被接成“全 memory/EP 广播”

证据:

1. `gem5/configs/ruby/CHI_ubcc_framework.py:122-130` 把所有 node 的 `L_SNF`、`DL_SNF`、`EP_SNF` 都加进 `mem_dests`。
2. `135-137` 对每个 `HN` 执行 `hnf.setDownstream(mem_dests)`。

为什么不符合要求:

1. 基线要求 `HN_i` 必须按地址分类，只把:
   - `LocalPrivate/UbccExclusive -> L_SNF_i`
   - `DsmLocal -> DL_SNF_i`
   - `DsmRemote -> EP_SNF_i`
2. 这里没有 node-local classification，只是把所有目的地全挂下去，实际不能证明路由正确。

修改建议:

1. 如果现有 `CHI_HNFController` 只能基于 `addr_ranges` 选择 memory-side destination，那么必须给 `L_SNF_i`、`DL_SNF_i`、`EP_SNF_i` 配置互斥且完整的地址元数据。
2. `EP_SNF_i` 不能只是“挂上去”，而必须具备能承载 remote DSM 分类的 `addr_ranges` 或等价路由描述。
3. 增加真实样本路由测试，检查 HN 对以下地址的选路结果:
   - `local_private`
   - `ubcc_exclusive`
   - `dsm_local`
   - `dsm_remote`

#### 问题 E: `TC-TOPO-2` 被直接漏掉

证据:

1. 报告只列 `TC-TOPO-1/3/4`，没有 `TC-TOPO-2`。
2. `tests/phase4/run_all_phase_tests.py` 也没有实现 `TC-TOPO-2`。

为什么不符合要求:

1. `docs/basic-framework-prompt.md:825-845` 把 `TC-TOPO-2` 列为必需 testcase。
2. 跳过必测项本身就是验收失败。

修改建议:

1. 明确补上 `TC-TOPO-2`。
2. 不接受任何“TC-TOPO-2 已被其他项间接覆盖”的说法，必须直接检查 downstream 内容。

---

### 4.3 Phase 3: Endpoint Skeleton

#### 问题 A: endpoint 最小收发路径没有被真实触发

基线要求:

1. `docs/basic-framework-prompt.md:777-779` 要求 endpoint 已接线，且最小消息收发路径可触发。
2. `899-911` 要求:
   - `TC-EP-3`: 手工注入 `Snp*`，`recvSnoopMsg()` 被调用，并返回合法 response。
   - `TC-EP-4`: 手工注入 `ReadNoSnp`，`recvRequestMsg()` 被调用，并返回 fake data + legal response。

当前实现问题:

1. `tests/phase4/run_all_phase_tests.py:226-245` 并未注入任何 CHI 消息。
2. 这里只是检查 `has recvSnoopMsg handler`、`isinstance(ep_rnf, EPController)` 等静态条件。
3. `EPRNFController.cc:238-241` 和 `EPSNFController.cc:42-45` 仅打印 trace 后 `return true`，没有构造 response/data 消息。

为什么不符合要求:

1. “有函数”不等于“路径被触发”。
2. “return true”不等于“返回合法 response / fake data”。

修改建议:

1. 为 EP controller 构造最小可注入的消息测试环境。
2. `TC-EP-3`:
   - 构造一个 `Snp*` 消息放入 `snpIn`。
   - `wakeup()` 后断言 `recvSnoopMsg()` 实际被调用。
   - 检查 `rspOut` 中出现合法 response。
3. `TC-EP-4`:
   - 构造 `ReadNoSnp` 请求放入 `reqIn`。
   - `wakeup()` 后断言 `recvRequestMsg()` 被调用。
   - 检查 `rspOut` 与 `datOut` 至少有一组符合 skeleton 约定的返回消息。
4. 如暂时难以使用完整 Ruby network，也至少要做 controller 级 message buffer 注入和 dequeue/enqueue 断言，而不是 `True`。

#### 问题 B: `EP_SNF_i` 缺少能参与 routing 的关键 metadata

证据:

1. `docs/basic-framework-prompt.md:558-571` 明确要求 `EP_SNF_i` 携带 `addr_ranges` 或等价 routing metadata。
2. 当前 Python 参数文件 `EPSNFController.py:14-16` 只有单个默认 `addr_range`，而 `CHI_ubcc_framework.py` 实例化时并未设置该参数。
3. 当前也没有任何代码把 `EP_SNF_i` 的地址职责与 `DSM_remote` 分类绑定起来。

为什么不符合要求:

1. 如果 HN memory-side routing 依赖地址范围，未设置 routing metadata 的 `EP_SNF_i` 根本无法证明能承接 remote DSM 请求。

修改建议:

1. 明确设计 `EP_SNF_i` 的地址责任表示方式:
   - 方案 A: `addr_ranges` 覆盖“对本 node 来说 remote DSM”窗口集合。
   - 方案 B: 增加自定义路由元数据，并在 HN wrapper 中按 `NodeAddressMap` 进行分类后只转发到本 node `EP_SNF_i`。
2. 不要保留未使用的默认 `1MiB addr_range`；要么设成真实值，要么删除该参数避免误导。

#### 问题 C: `EPBackend::checkAddr()` 没有被接入真实路径

证据:

1. 全仓库只有 `EPBackend.cc/.hh` 定义了 `checkAddr()`。
2. 没有任何调用点。

为什么不符合要求:

1. prompt 要求 ordinary CHI cross-node checker “存在且真实执行”。
2. 一个从未被调用的 `fatal` 函数不能算 checker 已落地。

修改建议:

1. 至少在 `EP_SNF_i` 接收 request 与 `EP_RNF_i` 接收 snoop/request 的入口调用 `checkAddr()`。
2. 对跨 node DSM、非 DSM sentinel access、误路由 ordinary CHI 请求，都应在真实消息路径中触发 checker。

#### 问题 D: `TC-EP-5` 负例测试是伪测试

证据:

1. `tests/phase4/run_all_phase_tests.py:250-255` 直接把 `EP_RNF init requires backend` 和 `EP_SNF init requires backend` 写成 `True`。
2. 这并没有实例化未接线 endpoint，也没有验证 `init()` 失败。

为什么不符合要求:

1. `docs/basic-framework-prompt.md:913-916` 明确要求“未接线 endpoint init 必须失败”。

修改建议:

1. 写一个独立负例脚本，故意创建不带 backend 的 endpoint。
2. 运行时断言 gem5 在 `init()` 阶段 fatal，并匹配错误信息中的 `node_id`。

---

### 4.4 Phase 4: Guardrails And Checker

#### 问题 A: ordinary CHI cross-node checker 没有真实执行

证据:

1. `docs/basic-framework-prompt.md:784,948` 要求 checker 存在且真实执行。
2. 当前 `EPBackend::checkAddr()` 无任何调用。
3. `tests/phase4/run_all_phase_tests.py:313-316` 的 `TC-ISO-1~4` 全部直接写死 `True`。

为什么不符合要求:

1. checker 不在运行路径上，就没有 guardrail 效果。
2. 误路由负例没有真正构造，也没有触发 fatal。

修改建议:

1. 在真实请求路径中接入 checker。
2. 明确补齐四个 isolation testcase:
   - `TC-ISO-1`: 三个 node 同时访问 local private，检查无 cross-node ordinary traffic。
   - `TC-ISO-2`: 访问 `DSM_i` 只落到 `DL_SNF_i`。
   - `TC-ISO-3`: 访问 remote DSM 必须先落本 node `EP_SNF_i`。
   - `TC-ISO-4`: 人工把 `CL_{0,0}` 接到 `HN_1`，必须 fatal。

#### 问题 B: `TC-G-4 Trace completeness` 是伪测试

证据:

1. `tests/phase4/run_all_phase_tests.py:297-303` 五项检查全部为常量 `True`。
2. 其中甚至包含 `NodeAddressMap has homeNode trace`，但 `NodeAddressMap` 本身并没有 trace 输出实现。

为什么不符合要求:

1. 基线要求的是“所有新增 trace / checker / route log 都带 `node_id`”，不是“测试作者主观认为应该有”。

修改建议:

1. 用两层验证替代写死 `True`:
   - 静态 grep: 所有新增 `DPRINTF` / `fatal` 模板包含 `node_id`。
   - 运行时验证: 实际触发至少一条 EP 路径日志和一条 checker 日志，检查输出包含 `node_id=`。

#### 问题 C: `TC-G-1` / `TC-G-2` 没有做“用户不可见/注册失败”的真实验证

证据:

1. `tests/phase4/run_all_phase_tests.py:262-283` 只是用 `NodeConfig` / `NodeAddressMap` 常量判断地址不属于 DSM。
2. 没有做任何“CPU 无法映射 UbccExclusive”或“sentinel registration 对 non-DSM 必须失败”的真实尝试。

为什么不符合要求:

1. `docs/basic-framework-prompt.md:920-929` 要求的是行为验证，不是静态范围关系验证。

修改建议:

1. `TC-G-1`:
   - 在测试进程中尝试把普通页映射或访问到 `UbccExclusive`，应失败。
   - 或直接验证 allocator / page mapping 层不会把用户态普通映射放进去。
2. `TC-G-2`:
   - 提供最小 sentinel registration API 或模拟入口。
   - 用 `LocalPrivate` / `UbccExclusive` 地址调用时必须返回错误或 fatal。

---

## 5. 现有测试体系的具体问题

### 5.1 `tests/phase4/run_all_phase_tests.py` 的核心缺陷

该文件当前不是“综合验收测试”，而更接近“静态 smoke checklist”。

具体问题:

1. 未创建 `Root`，未调用 `m5.instantiate()`，未调用 `m5.simulate()`。
2. 未构造真实 `N=3, L=2, D=2` 拓扑。
3. 未调用 `create_ubcc_system()`。
4. 未注入任何 CHI message。
5. 未构造任何 misroute negative case。
6. 大量断言直接写死为 `True`。

这与 `docs/basic-framework-prompt.md:796-797` 的“testcase 不允许删减成对象实例化 + 打印 PASSED”直接冲突。

修改建议:

1. 将测试拆成两层:
   - 配置层构建测试: 真实构建 topology 并检查对象关系。
   - 运行层行为测试: 真实注入消息或执行 workload，检查路由与 fatal。
2. 删除所有“纯 `True` 断言”与“deferred 也算 PASS”逻辑。
3. 若某项当前无法真实验证，应明确标记 `NOT IMPLEMENTED`，而不是伪装成已通过。

### 5.2 `reports/basic-framework-completion-1.md` 的报告问题

该报告存在明显夸大与自相矛盾。

具体表现:

1. 总览宣称“按照 4 个阶段，完成全部实现和验收”。
2. 第 5 节又承认“完整 Ruby/CHI 模拟仍需更多调试”。
3. 第 5 节还承认 `SEWorkload reserved-range` 未实现。
4. 这与“全部完成”和“95/95 通过”的结论不兼容。

修改建议:

1. 重新撰写状态报告，必须按以下分类表述:
   - 已实现并已被真实 testcase 覆盖
   - 已部分实现但尚未通过真实验收
   - 设计存在但尚未接入运行路径
   - 未实现
2. 严禁将“对象存在”“函数存在”“预留骨架”“deferred PASS”计入正式通过数。

---

## 6. 详细整改方案

建议按以下顺序整改，避免继续在伪测试基础上叠加代码。

### 6.1 先修 Phase 2 主路径可实例化

目标:

1. `tests/phase2/run_ubcc_ruby_test.py` 能成功 `instantiate()`。
2. 拓扑对象 parent 关系正确。
3. CPU、cluster、node 绑定正确。

具体步骤:

1. 修正 `cluster_cpus` 的 node 偏移。
2. 为每个 node 单独保存:
   - `hnf_cntrl`
   - `l_snf_cntrl`
   - `dl_snf_cntrl`
   - `ep_rnf_cntrl`
   - `ep_snf_cntrl`
3. 避免把跨 parent 的 orphan controller 直接放进 `downstream_destinations`。
4. 明确将 wrapper / controller 以命名 child 的方式挂接到 `ruby_system`。
5. 修复后以 `tests/phase2/run_ubcc_ruby_test.py` 为第一阻断门，未通过不得继续下阶段。

### 6.2 再修 topology routing 正确性

目标:

1. `CL_{i,j}` 只连 `HN_i`。
2. `HN_i` 只按 node-local 分类分流到 `L_SNF_i / DL_SNF_i / EP_SNF_i`。

具体步骤:

1. 给 cluster 加 `node_id` / `cluster_id` 元数据，方便验证。
2. `TC-TOPO-2` 直接检查 `downstream_destinations` 只包含本 node HN。
3. 为 `L_SNF_i` / `DL_SNF_i` / `EP_SNF_i` 建立明确职责边界。
4. 若 `CHI_HNFController` 现状不足以表达 routing，增加 wrapper 层辅助逻辑，而不是把所有目的地全塞进去。

### 6.3 再做 endpoint 最小行为闭环

目标:

1. `EPRNFController` 能接收 `Snp*` 并回固定合法 response。
2. `EPSNFController` 能接收 `ReadNoSnp` 并回 fake data + legal response。

具体步骤:

1. 在 `recvSnoopMsg()` 中构造最小 response message。
2. 在 `recvRequestMsg()` 中构造 response 与 data message。
3. 将 `_backend->checkAddr()` 接入入口路径。
4. 增加 buffer-level message injection test，不依赖完整协议闭环也可验证最小 skeleton 行为。

### 6.4 最后补齐 guardrail 与负例测试

目标:

1. checker 在真实路径中执行。
2. 所有 negative case 真正 fatal。

具体步骤:

1. 增加 misroute topology 变体测试。
2. 增加 non-DSM sentinel registration 负例测试。
3. 增加 unwired endpoint 负例脚本。
4. 增加 trace 输出采样检查，确保日志含 `node_id`。

---

## 7. 建议的复验清单

整改完成后，建议按以下顺序复验。

### 7.1 Phase 1

1. 真实运行 `proc_test.arm`，验证 3 段 DSM VA 映射。
2. 真实验证 heap/stack/.data/.text 的 PA 不落入 `DSM_GLOBAL` / `UbccExclusive`。
3. 真实验证三个 node 的普通页只从各自 local-private pool 分配。

### 7.2 Phase 2

1. `tests/phase2/run_ubcc_ruby_test.py` 必须能 `instantiate()`。
2. 检查 3 个 `HN`、6 个 cluster RN-F、3 个 `EP_RNF`、3 个 `L_SNF`、3 个 `DL_SNF`、3 个 `EP_SNF`。
3. 检查每个 cluster downstream 只能是本 node `HN_i`。
4. 检查每个 `HN_i` 对地址样本的分流结果正确。

### 7.3 Phase 3

1. 注入 `Snp*` 到 `EPRNFController`，验证 response。
2. 注入 `ReadNoSnp` 到 `EPSNFController`，验证 fake data + legal response。
3. 创建未接线 endpoint，验证 init fatal。

### 7.4 Phase 4

1. 人工 misroute `CL_{0,0} -> HN_1`，验证 checker fatal。
2. 验证 `LocalPrivate` 不触发 EP 路径。
3. 验证 `UbccExclusive` 对普通 CPU 不可见。
4. 验证运行时 trace 与 fatal 输出都包含 `node_id`。

---

## 8. 最终判定

本次提交可以认定为:

1. 已完成一部分基础代码骨架。
2. 已完成一部分 `Phase 1` 基础能力。
3. 尚未完成 `basic framework` 的正式验收。

不能认定为:

1. `N=3, L=2, D=2` 完整拓扑已成功 bring-up。
2. `HN_i` 地址分流已按要求验证通过。
3. endpoint 最小消息收发路径已被真实 testcase 触发。
4. ordinary CHI cross-node checker 已接入并执行。
5. 全部 4 个阶段已经通过正式验收。

因此，`reports/basic-framework-completion-1.md` 的完成声明应撤回，当前状态应更新为:

```text
basic framework: partially implemented, acceptance rejected, remediation required
```
