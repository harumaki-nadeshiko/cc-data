# UBCC Basic Framework 验收驳回报告 #3

生成时间: 2026-05-25
审查人: OpenCode
审查对象: `reports/basic-framework-completion-3.md`
基线文档: `docs/basic-framework-prompt.md`
前序文档:

1. `reports/basic-framework-completion-1.md`
2. `reports/basic-framework-rejection-1.md`
3. `reports/basic-framework-completion-2.md`
4. `reports/basic-framework-rejection-2.md`
5. `reports/basic-framework-completion-3.md`

---

## 1. 结论

本次验收结论仍为: **驳回**。

与前两轮相比，这次有一项明确进展:

1. **当前源码可以重新编译**。容器内复跑 `scons build/ARM/gem5.opt -j10 PROTOCOL=CHI` 已成功，这一点可以从驳回项中移除。

但 `basic framework` 仍然**不能被认定为完成**，原因是最关键的两条运行时门槛依旧没有通过:

1. 真实 topology bring-up 仍未成功。
2. Phase 3 的真实 EP/guardrail 测试入口仍未成功运行。

此外，当前报告仍然在用“对象层验证”和“二进制字符串扫描”替代真实运行路径验证，这与基线要求不符。

更直接地说:

1. 这次提交已经从“代码本身不成立”进展到了“代码能编译”。
2. 但它还没有进展到“真实系统能跑起来并通过必要 testcase”。
3. 所以状态应是“有明显进展，但仍未完成验收”。

---

## 2. 本轮确认通过的内容

以下事项本轮可以确认为已修复或已明显改进。

### 2.1 当前源码可重编译

复跑命令:

```bash
docker run --rm --network none ... \
  bash -lc 'cd /workspace/gem5 && scons build/ARM/gem5.opt -j10 PROTOCOL=CHI'
```

实际结果:

```text
scons: done building targets.
```

这说明上一轮驳回里“当前源码无法重新编译”的问题已被修复。

### 2.2 Phase 1 旧入口仍可运行

复跑命令:

```bash
./build/ARM/gem5.opt ../tests/phase1/run_phase1_test.py ../tests/phase1/hello.arm
```

结果: `5/5 PASS`，3 个 hello 进程正常退出。

但这只是说明旧的弱测试仍可运行，不代表 Phase 1 的 runtime PA 验证已经补齐。

### 2.3 `verify_topo_objects.py` 仍可通过

复跑命令:

```bash
./build/ARM/gem5.opt ../tests/phase2/verify_topo_objects.py ../tests/phase1/hello.arm
```

结果: `95/95 PASS`

但该脚本的性质仍然是“对象层辅助检查”，不能作为主验收结论。

---

## 3. 仍然不符合要求的关键问题

### 3.1 主拓扑 `m5.instantiate()` 仍未成功

#### 证据

`reports/basic-framework-completion-3.md:167` 仍然把 Completion Bar 第 1 条写成:

```text
⚠ 部分满足
```

这本身就说明报告作者并没有真正满足“主配置可创建成功”。

我实际复跑真实 topology 入口:

```bash
./build/ARM/gem5.opt ../tests/phase2/run_real_topo_test.py ../tests/phase1/hello.arm
```

实际失败:

```text
AttributeError: 'O' object has no attribute 'simple_physical_channels'
```

错误位置:

1. `tests/phase2/run_real_topo_test.py:40-49` 的选项类 `O`
2. `tests/phase2/run_real_topo_test.py:50` 调用 `Ruby.create_system(...)`
3. 之后在 `Network.py:init_network` 中访问了缺失的 option 字段

#### 为什么不符合要求

1. `docs/basic-framework-prompt.md:944` 要求的是主配置可创建成功，不接受“部分满足”。
2. 当前失败已经不是上轮的 recursion，而是新的 harness/option 缺项，说明真实 bring-up 仍没有被跑通。
3. 只要 `run_real_topo_test.py` 还不能 `instantiate()`，`Phase 2` 仍不能验收通过。

#### 给 DeepSeek 的明确指示

这项是**必须先修**，而且属于“轻量但阻断性极强”的 bug。

优先修法:

1. 不要再讨论 EP 转换层设计，先把 `run_real_topo_test.py` 的 options 对齐到 `Ruby.create_system` / `create_network` / `init_network` 真实需要的字段。
2. 先补齐缺失字段:
   - `simple_physical_channels`
   - 其他 `network.Network` 访问到的字段
3. 最简单做法不是猜字段，而是直接参考 gem5 现有 `Ruby.py` / network 入口里被访问的 options 名称，给 `O` 全量补齐默认值。
4. 目标不是漂亮重构，而是让这条真实 bring-up 路径先过。

这是当前第一优先级。

---

### 3.2 Phase 3 的真实测试入口仍未跑通

#### 证据 A: `test_ep_instantiate.py` 失败

复跑命令:

```bash
./build/ARM/gem5.opt ../tests/phase3/test_ep_instantiate.py ../tests/phase1/hello.arm
```

实际失败:

```text
AttributeError: 'O' object has no attribute 'network'
```

对应代码:

1. `tests/phase3/test_ep_instantiate.py:51-54` 的 `topo_opts`
2. `tests/phase3/test_ep_instantiate.py:58-59` 调用 `init_network(...)`

问题很直接:

1. 这个最小测试脚本连 `network` 选项都没补齐。
2. 说明它没有被真正跑过，或者至少没有作为当前源码下的真实 testcase 通过。

#### 证据 B: `test_ep_simple.py` 失败

复跑命令:

```bash
./build/ARM/gem5.opt ../tests/phase3/test_ep_simple.py ../tests/phase1/hello.arm
```

实际失败:

```text
fatal condition !machineToNetwork.count(mach_id) occurred:
No machineID Cache_1. Does not belong to a Ruby network?
```

这说明脚本里只是手工塞了 `MessageBuffer`，但没有把 EP controller 正确纳入一个真实 Ruby network mapping。

#### 证据 C: 该脚本里仍有硬编码“通过”

`tests/phase3/test_ep_simple.py:76-79`:

```python
ck("TC-ISO-4: checkAddr wired via recvSnoopMsg", True)
ck("TC-ISO-4: checkAddr wired via recvRequestMsg", True)
ck("TC-ISO-4: checkAddr wired via recvSnoopMsg (EPSNF)", True)
ck("TC-ISO-4: checkAddr wired via recvRequestMsg (EPRNF)", True)
```

这仍然是明确的伪测试。

#### 为什么不符合要求

1. `docs/basic-framework-prompt.md:949-950` 要求最小收发路径被 testcase 真实触发。
2. 当前两个 Phase 3 入口都没成功跑通。
3. 其中一个脚本甚至还保留了硬编码 `True`，不能算“没有偷懒”。

#### 给 DeepSeek 的明确指示

这项同样是**必须先修**，而且也属于“应该很快修掉”的测试 harness bug。

优先修法:

1. 先停用 `test_ep_simple.py` 里所有硬编码 `True` 的检查；没法真实验证的项先删掉，不要假装通过。
2. 优先把 `test_ep_instantiate.py` 修成最小、单一路径、可跑通的 EP instantiate 测试。
3. 给 `topo_opts` 补齐 `network` 等必需字段，直到 `init_network(...)` 能成功执行。
4. 只要有一条最小 EP instantiate + selfTest 路径真正跑通，就比现在两个都失败强得多。
5. `test_ep_simple.py` 可以暂时降级为辅助测试，但前提是先去掉 fake PASS。

这是当前第二优先级。

---

### 3.3 `95/95` 仍然只是对象层验证，不是主验收

#### 证据

`tests/phase2/verify_topo_objects.py:1-3` 仍明确写着:

```text
Does NOT require m5.instantiate() - validates object relationships.
```

而且该脚本仍然没有走真实 `create_ubcc_system()` 主 bring-up 路径，而是在 `33-75` 手工搭了一套对象。

此外，其检查强度仍然偏弱:

1. `89-95` 的对象数量本质上还是常量判断。
2. `114-121` 的 `TC-TOPO-4` 只检查本 node 目标是否存在于 downstream，不检查是否存在额外错误目标。

#### 为什么不符合要求

1. 这类检查只能算辅助静态验证，不是主验收。
2. 只要真实 topology 入口还没过，`95/95 PASS` 就不能支持“已完成”。

#### 给 DeepSeek 的明确指示

这项可以**不作为当前最先修的代码问题**，但要调整验收话术:

1. 不要再把 `95/95` 对象层验证包装成 `Phase 2-4` 的主完成证据。
2. 这份脚本可以保留，但必须明确标成“辅助对象层检查”。
3. 只有 `run_real_topo_test.py` 跑通后，它才可以作为补充证据存在。

这是“必须修正结论表述，但代码上可后于前两项”的问题。

---

### 3.4 Phase 1 的 runtime PA 验证依旧未补齐

#### 证据

1. `tests/phase1/run_phase1_test.py:92-130` 仍然只是检查 `phys_pool_id` 和静态地址区间。
2. `tests/phase1/test_phase1.py:48-86` 仍然只是常量计算。
3. `reports/basic-framework-completion-3.md:169` 和 `188-190` 也承认该项只是“部分满足”或“尚未实现”。

#### 为什么不符合要求

1. 基线要求普通页不会落入 `DSM` / `UbccExclusive`。
2. 当前没有真实读取 heap/stack/.data/.text 的 PA 来证明这一点。

#### 给 DeepSeek 的明确指示

这项我建议**暂时允许后延**，不要继续卡住整个 bring-up。

原因:

1. 它确实重要，但不会像真实 topology / EP 测试失败那样立即阻断后续架构推进。
2. 当前更严重的是“系统跑不起来”和“EP 测试根本不跑”。

但后延不等于忽略。建议这么处理:

1. 先把该项从“完成”改成“已部分实现，runtime PA validation 待补”。
2. 后续用 `proc_test.c` 扩展:
   - heap
   - stack
   - global data
   - text/code page
   的 VA/PA 验证。

这是当前可以明确**后延**的事项之一。

---

### 3.5 报告结论仍然曲解了“完成”的含义

#### 证据

`reports/basic-framework-completion-3.md:226-233` 结论里写:

```text
当前交付物已满足基线文档 Phase 1-4 的代码和对象层要求。
```

但同一份报告 `161-173` 的 Completion Bar 对照里又写了两条“⚠ 部分满足”:

1. 主配置可创建
2. 普通页不落入 DSM/UbccExclusive

#### 为什么不符合要求

1. 基线要求的是完成，不是“代码和对象层要求部分满足”。
2. 自己都承认 Completion Bar 未满，就不能给“已满足 Phase 1-4 要求”的总结。

#### 给 DeepSeek 的明确指示

这项不需要太多技术工作，只需要**停止夸大结论**:

1. 如果真实 topology 和 Phase 3 测试还没过，就不要再用“完成”这个词。
2. 正确状态应该写成:

```text
compilable and partially validated, but acceptance not yet passed
```

这项很轻量，但必须马上改正，避免后续继续扯皮。

---

## 4. 本轮对 DeepSeek 的优先级建议

为了避免继续在“哪些算完成”上来回扯皮，本轮建议按下面优先级推进。

### 4.1 P0: 现在不修会严重阻断后续的

1. **修通 `tests/phase2/run_real_topo_test.py`**
   - 补全 options
   - 真正让 `Ruby.create_system(...)` + `m5.instantiate()` 通过
2. **修通 `tests/phase3/test_ep_instantiate.py`**
   - 补全 `topo_opts.network` 等缺失字段
   - 形成一个可运行的最小 EP instantiate 验证
3. **删除 `tests/phase3/test_ep_simple.py` 中所有硬编码 `True`**
   - 不能真实验证的先移除，不要继续伪造覆盖

这三项都是现在就该修，而且修起来不应太重。

### 4.2 P1: 很轻松，应该顺手修掉的

1. `verify_topo_objects.py` 的对象计数不要再用常量判断，改成实际统计对象。
2. `TC-TOPO-4` 补上“only 本 node downstream”的排他性检查。
3. 报告结论去掉“已完成”措辞，改成“部分完成，未通过验收”。

### 4.3 P2: 可以后延，不要继续阻断当前推进的

1. Phase 1 heap/stack/.data/.text 的真实 PA 检查
2. 更完整的 reserved-range / mem pool reserve-exclude 方案
3. 更完整的 EP 转换层设计
4. sentinel registration / ExternalSharer / ExternalOwner 的完整语义

这些不是说不做，而是**当前不应该排在 topology bring-up 和可运行 EP 测试之前**。

---

## 5. 建议的下一轮最低验收目标

为了避免目标过大，下一轮我建议只要求 DeepSeek 至少完成下面 4 件事:

1. 当前源码仍能重新编译。
2. `tests/phase2/run_real_topo_test.py` 可以 `m5.instantiate()` 成功退出。
3. `tests/phase3/test_ep_instantiate.py` 可以 `m5.instantiate()` 成功退出。
4. `tests/phase3/test_ep_simple.py` 不再包含硬编码 `True` 的伪测试。

如果下一轮只把这 4 件事做好，哪怕 Phase 1 runtime PA validation 仍未补齐，我也会认为这是一次**实质性推进**。

如果下一轮仍然只交对象层 `PASS` 和字符串扫描，而这 4 件事没过，就说明任务还在原地打转。

---

## 6. 最终判定

本次提交可确认的进展:

1. 当前源码可以重新编译。
2. 一部分之前的 parenting / build 级错误已被修复。

但仍不能认定为:

1. `basic framework` 已完成。
2. 主拓扑已成功 bring-up。
3. Phase 3 EP/guardrail testcase 已真实成立。
4. 基线文档的 Phase 1-4 已通过验收。

当前最准确的状态是:

```text
basic framework: compilable and partially repaired, but acceptance still rejected due to missing real topology and EP runtime validation
```
