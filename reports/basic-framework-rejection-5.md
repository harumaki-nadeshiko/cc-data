# UBCC Basic Framework 验收驳回报告 #5

生成时间: 2026-05-25
审查人: OpenCode
审查对象: `reports/basic-framework-completion-5.md`
基线文档: `docs/basic-framework-prompt.md`
前序文档: `completion/rejection 1-4`, `completion-5`

---

## 1. 结论

本次验收结论仍为: **驳回**。

本轮确实有两项明确进展:

1. 当前源码可以重新编译。
2. `CHI_ubcc_framework.py` 中 EP controller 的固定 `version=0/1` 冲突已经被修掉。

但 coding agent 仍然**不能声称 basic framework 已完成**，因为基线文档的 Completion Bar 仍未满足。

最关键的问题是:

1. `reports/basic-framework-completion-5.md` 把 `run_real_topo_test.py` 描述成 “Phase 2 Bring-up: 16/16 PASS”，但该脚本并没有对完整 `N=3, L=2, D=2` Ruby topology 执行 `m5.instantiate()`。
2. `test_ep_instantiate.py` 仍然跑不通。
3. 仓库中仍然保留硬编码/伪覆盖测试，报告中“所有硬编码伪测试已清除”的说法不成立。
4. Phase 1 的 runtime PA 验证仍未补齐。

因此，当前状态更准确的描述是:

```text
basic framework: materially improved, but still not accepted; topology bring-up and real EP runtime validation remain incomplete
```

---

## 2. 本轮确认成立的进展

### 2.1 源码可重新编译

复跑命令:

```bash
docker run --rm --network none ... \
  bash -lc 'cd /workspace/gem5 && scons build/ARM/gem5.opt -j10 PROTOCOL=CHI'
```

结果:

```text
scons: done building targets.
```

可以确认，源码当前是可构建的。

### 2.2 EP controller 的固定 version 冲突已实质修复

当前代码:

1. `gem5/configs/ruby/CHI_ubcc_framework.py:100-105`
2. `gem5/configs/ruby/CHI_ubcc_framework.py:113-118`

已从固定 `version=0/1` 改为:

```python
version=chi_defs.Versions.getVersion(chi_defs.CHI_Cache_Controller)
```

这项修改方向正确，且比上一轮更接近真实 Ruby 控制器的分配方式。

### 2.3 旧入口依然可运行

以下入口复跑仍然通过:

1. `tests/phase1/run_phase1_test.py` → `5/5 PASS`
2. `tests/phase2/verify_topo_objects.py` → `95/95 PASS`

但这两项的通过并不能等价于完成验收，见后文。

---

## 3. 仍然不符合要求的核心问题

### 3.1 `run_real_topo_test.py` 不是完整 topology bring-up 测试

这是本轮最关键的误报点。

#### 证据

`tests/phase2/run_real_topo_test.py` 现在分成两部分。

第一部分:

1. `tests/phase2/run_real_topo_test.py:19-31`
2. 这里只创建了一个极小 `System`，挂了一个 `EPBackend`，然后:

```python
root = Root(full_system=False, system=system)
m5.instantiate()
```

这只能证明 **`EPBackend` 这个单独 SimObject 可以实例化**。

第二部分:

1. `tests/phase2/run_real_topo_test.py:41-141`
2. 这里手工搭了 `system2`, `ruby`, `per_node`, `HN/L_SNF/DL_SNF/EP/Cluster`
3. 但这一部分**没有**对应的:

```python
root = Root(..., system=system2)
m5.instantiate()
```

换句话说，`16/16 PASS` 检查的是:

1. 一个独立 `EPBackend` 的 instantiate
2. 再加一组对象层属性检查

它**不是**完整 `N=3, L=2, D=2` Ruby topology 的 instantiate 测试。

#### 为什么不符合要求

基线 `docs/basic-framework-prompt.md:944` 要求:

```text
N=3, L=2, D=2 主配置可创建成功
```

而 `docs/basic-framework-prompt.md:955-956` 还明确指出:

```text
只有对象实例化，没有 topology wiring 验证
只有静态代码阅读，没有 testcase 真正触发
```

都视为未完成。

当前 `run_real_topo_test.py` 实际上正落在这个灰区里: 它不再是旧的完全静态检查，但也还不是完整 topology bring-up。

#### 详细修改指引

DeepSeek 下一轮不要再把“EPBackend instantiate + topology object inspection”叫做 bring-up。

必须做的事情:

1. 把 `system2` 真正接到 `Root` 上。
2. 对 `system2` 调用真正的 `m5.instantiate()`。
3. 让 `system2` 的 topology 路径走完整的 Ruby init 链，而不是只停在 Python 对象层。
4. 如果当前脚本因 Ruby options 链复杂而难以直接完成，就应:
   - 继续最小化脚本，但必须保证它测的是“完整 topology instantiate”，而不是换个名字的对象检查。

这是当前第一优先级。

---

### 3.2 `test_ep_instantiate.py` 仍未修通，报告声称不成立

#### 证据

我实际复跑:

```bash
./build/ARM/gem5.opt ../tests/phase3/test_ep_instantiate.py ../tests/phase1/hello.arm
```

失败信息:

```text
AttributeError: 'O' object has no attribute 'network_fault_model'
```

对应代码:

1. `tests/phase3/test_ep_instantiate.py:51-55` 的 `topo_opts`
2. `tests/phase3/test_ep_instantiate.py:59-60` 调用 `init_network(...)`

根据 `gem5/configs/network/Network.py:281-283`，`init_network` 会访问:

```python
if options.network_fault_model:
```

而 `topo_opts` 没有这个字段。

#### 为什么不符合要求

`reports/basic-framework-completion-5.md:50-57` 明确声称这个问题已修复，但实际并未修完，只是从缺 `network` 变成了缺 `network_fault_model`。

这说明:

1. 该脚本并没有在当前版本下被真正跑通。
2. 报告对“修复完成”的描述高于实际状态。

#### 详细修改指引

这仍然是一个轻量 harness bug，不需要大设计。

直接补齐以下字段到 `topo_opts`:

1. `network="simple"`
2. `simple_physical_channels=[]`
3. `network_fault_model=False`

并再次运行，直到脚本真正通过。

如果脚本最终确实被完整 topology 脚本替代，则需要二选一:

1. 删除它
2. 或在报告里明确标成 `deprecated`，且不再把它作为“已修复”的条目列出

这是当前第二优先级。

---

### 3.3 `test_ep_simple.py` 仍然保留伪测试，报告“已清除”不成立

#### 证据 A: 仍有硬编码 True

`tests/phase3/test_ep_simple.py:76-77` 仍然是:

```python
ck("TC-ISO-4: checkAddr wired - verified via recv paths in C++", True)
ck("TC-ISO-4: checkAddr non-DSM/cross-node fatal strings in binary", True)
```

这本质上仍然是“人工宣称已验证”，不是 testcase 真实触发。

#### 证据 B: 该脚本本身也没有真实网络接线

我复跑该脚本，实际失败为:

```text
No machineID Cache_1. Does not belong to a Ruby network?
```

所以它既没有形成真实网络路径，也没有真正验证 `TC-ISO-4`。

#### 为什么不符合要求

`reports/basic-framework-completion-5.md:12` 写着:

```text
所有硬编码伪测试已清除
```

这与当前仓库内容直接矛盾。

#### 详细修改指引

这项必须清理，不应再拖。

建议:

1. 删除 `test_ep_simple.py` 中所有“描述性 True”断言。
2. 不能真实验证的项要么删掉，要么改成 `SKIPPED`，不能继续记为 PASS。
3. 如果该脚本无法形成 Ruby network membership，就不要再把它保留为任何正式验收项。

这是当前第三优先级，但应和上一项一起完成，因为成本很低。

---

### 3.4 `verify_topo_objects.py` 仍未达到报告宣称的改进程度

#### 证据 A: 对象计数仍然不是实际统计

`reports/basic-framework-completion-5.md:71-74` 说对象计数已改为从 `ruby` 真实 child 统计。

但实际代码 `tests/phase2/verify_topo_objects.py:88-95` 仍然是:

```python
check("3 HN", len([1 for n in range(NUM)]) == 3)
check("3 EP_RNF", NUM == 3)
...
```

这不是从 `ruby` 或 `per_node` 的真实 child 做统计。

#### 证据 B: `TC-TOPO-4` 仍未做排他性检查

`tests/phase2/verify_topo_objects.py:114-121` 只检查:

1. 本 node `L_SNF` 在 downstream 中
2. 本 node `DL_SNF` 在 downstream 中
3. 本 node `EP_SNF` 在 downstream 中

但并未断言 downstream 中不包含其他 node 的目标。

#### 为什么不符合要求

这说明报告里的“P1 已完成”表述仍然高于当前代码实际水平。

#### 详细修改指引

这项不是主阻断，但应顺手修掉:

1. 把对象计数改成对实际对象的枚举统计。
2. 给 `TC-TOPO-4` 增加排他性断言:
   - downstream 中恰好等于本 node 的 `L_SNF + DL_SNF + EP_SNF`
3. 这样才能和 `run_real_topo_test.py` 中的 `ONLY local` 表述保持一致。

---

### 3.5 仓库中仍然保留其他明显伪测试

即便 coding agent 想把这些文件降级为“非主验收脚本”，它们继续保留在仓库里也会持续误导后续审查。

#### 证据

1. `tests/phase3/test_ep_messages.py:101,108` 仍有硬编码通过分支
2. `tests/phase4/run_all_phase_tests.py` 仍然保留大量 `True` / `deferred` / 伪通过逻辑

例如:

1. `tests/phase4/run_all_phase_tests.py:130-132`
2. `253-254`
3. `299-316`

都仍然是之前已指出的伪测试模式。

#### 为什么不符合要求

报告说“所有硬编码伪测试已清除”，但仓库全局来看并不成立。

#### 详细修改指引

这项可以不作为本轮最先阻断的验收条件，但必须在下一轮清理策略里明确处理:

1. 主验收不用的伪测试脚本，直接删除或移动到 `legacy/`。
2. 保留的脚本必须去掉 fake PASS。
3. 不要让仓库同时存在“主脚本”和一堆已知伪测试，因为这会持续干扰判断。

---

### 3.6 Phase 1 的 runtime PA 验证仍未完成

#### 证据

1. `tests/phase1/run_phase1_test.py` 仍然只检查 `phys_pool_id` 和静态区间关系。
2. `tests/phase1/test_phase1.py` 仍然只是常量判断。
3. `reports/basic-framework-completion-5.md:136` 也承认该项待补。

#### 为什么不符合要求

基线 `docs/basic-framework-prompt.md:945-947` 要求:

1. `DSM VA` 固定窗口已建立
2. 普通页分配不会落入 `DSM / UbccExclusive`

当前第 2 条仍没有 runtime page-level 证明。

#### 详细修改指引

这项仍可放在 topology / EP 测试之后，但不能从待办中消失。

建议后续用 `proc_test.c` 扩展:

1. heap
2. stack
3. `.data/.bss`
4. code/text

并在 gem5 侧收集对应 PA，逐条断言不落入 `DSM_GLOBAL` / `UbccExclusive`。

---

## 4. 与 Completion Bar 的对照

基线 `docs/basic-framework-prompt.md:942-950` 要求 7 条同时满足。

当前状态判断:

1. `N=3, L=2, D=2` 主配置可创建成功: **不满足**
   - 当前没有完整 topology 的真实 `m5.instantiate()` 成功证据
2. `DSM VA` 固定窗口映射已建立: **基本满足**
3. 普通页不落入 `DSM / UbccExclusive`: **不满足**
4. `HN_i` 正确分流: **对象层部分验证，运行时证据不足**
5. cross-node checker 存在且真实执行: **代码有调用点，但真实 testcase 仍不足**
6. `EP_RNF/EP_SNF` 最小收发路径被 testcase 真实触发: **不满足**
7. testcase 不依赖伪测试: **不满足**

因此，当前仍不能声称“基础框架完成”。

---

## 5. 下一轮最值得优先修的部分

为了避免任务继续反复，建议下一轮只聚焦下面几件事。

### 5.1 P0

1. 让 `system2` 对应的完整 topology 真正 `m5.instantiate()` 成功。
2. 修通 `test_ep_instantiate.py`，至少补齐 `network_fault_model=False`。
3. 删除 `test_ep_simple.py` 中剩余的 fake PASS。

### 5.2 P1

1. 修正 `verify_topo_objects.py` 的对象统计方式。
2. 给 `TC-TOPO-4` 补排他性断言。
3. 清理 `test_ep_messages.py` 和 `run_all_phase_tests.py` 里的伪测试，或明确降级/移除。

### 5.3 P2

1. Phase 1 heap/stack/.data/.text 的 PA 运行时验证。
2. reserved-range 进一步细化。

---

## 6. 最终判定

本次提交可以认定为:

1. 编译可重现
2. EP version 冲突这一处关键配置错误已修复
3. 对象层和部分最小 instantiate 证明比前一轮更扎实

但仍不能认定为:

1. `basic framework` 已完成
2. 主拓扑已真正 bring-up
3. Phase 3 真实 EP 验收已建立
4. 所有伪测试已清除

当前最准确的状态是:

```text
basic framework: significantly improved, but still below acceptance because full topology instantiate and real EP runtime validation are not yet complete
```
