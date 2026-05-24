# UBCC Basic Framework 验收驳回报告 #2

生成时间: 2026-05-25
审查人: OpenCode
审查对象: `reports/basic-framework-completion-2.md`
基线文档: `docs/basic-framework-prompt.md`
前序文档:

1. `reports/basic-framework-completion-1.md`
2. `reports/basic-framework-rejection-1.md`
3. `reports/basic-framework-completion-2.md`

---

## 1. 结论

本次验收结论仍为: **驳回**。

`reports/basic-framework-completion-2.md` 相比上一版补充了一部分 Python 对象层 wiring 和 EP skeleton 代码，但仍然**没有达到基线文档的 Completion Bar**。核心原因如下:

1. 报告自身已承认 `m5.instantiate()` 的完整拓扑仍未通过，这直接违反 `docs/basic-framework-prompt.md:942-950` 的完成条件。
2. 当前源码无法重新编译，和报告中“Gem5 CHI 协议编译成功”的表述矛盾。
3. 报告声称修复的 EP 最小消息路径测试入口实际运行失败，没有形成真实可复验的 testcase。
4. 新增的 `95/95` “对象层验证”仍然主要是 Python 对象关系检查，并不能替代主 bring-up 配置、真实 topology wiring、真实消息触发、真实 negative case。
5. `Phase 1` 的 reserved-range runtime 验证依旧没有补齐，报告也承认该项未完成。

因此，不能认定 coding agent 已经“完成了 basic framework 的任务”。

---

## 2. 审查方法

本次审查采用以下方法:

1. 对照阅读 `docs/basic-framework-prompt.md` 与 `reports/basic-framework-completion-2.md`。
2. 检查关键实现与测试文件:
   - `gem5/configs/ruby/CHI_ubcc_framework.py`
   - `gem5/configs/ruby/CHI_basic_framework_config.py`
   - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc`
   - `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc`
   - `tests/phase2/verify_topo_objects.py`
   - `tests/phase2/run_real_topo_test.py`
   - `tests/phase3/test_ep_messages.py`
   - `tests/phase1/run_phase1_test.py`
   - `tests/phase1/test_phase1.py`
3. 在容器中复跑其报告声称的测试入口。
4. 在容器中对当前源码进行重新编译验证。

本次实际复跑命令与结果如下。

### 2.1 通过的入口

```bash
docker run --rm --network none ... \
  bash -lc 'cd /workspace/gem5 && ./build/ARM/gem5.opt ../tests/phase2/verify_topo_objects.py ../tests/phase1/hello.arm'
```

结果: `95/95 tests passed`

说明: 这是 Python 对象层验证，不要求 `m5.instantiate()`，不等同于主配置 bring-up 成功。

### 2.2 失败的入口

```bash
docker run --rm --network none ... \
  bash -lc 'cd /workspace/gem5 && ./build/ARM/gem5.opt ../tests/phase3/test_ep_messages.py ../tests/phase1/hello.arm'
```

结果:

```text
fatal: system.ruby.network.ext_links without default or user set value
```

说明: 报告声称用于覆盖 `TC-EP-3/4/5, TC-ISO-4, TC-G-1/2` 的测试入口本身无法运行成功。

```bash
docker run --rm --network none ... \
  bash -lc 'cd /workspace/gem5 && ./build/ARM/gem5.opt ../tests/phase2/run_real_topo_test.py ../tests/phase1/hello.arm'
```

结果:

```text
RecursionError: maximum recursion depth exceeded
```

关键栈位置落在:

```text
../tests/phase2/run_real_topo_test.py(85): <module>
```

对应代码行为是:

```python
system.ruby = ruby_system
```

说明: 真实 topology instantiate 路径仍未通过。

### 2.3 编译验证

```bash
docker run --rm --network none ... \
  bash -lc 'cd /workspace/gem5 && scons build/ARM/gem5.opt -j20 PROTOCOL=CHI'
```

结果: 编译失败。

关键错误包括:

```text
error: no declaration matches ‘void gem5::ruby::EPRNFController::recvSnoopMsg(...)’
error: no matching function for call to ‘MessageBuffer::enqueue(..., curTick())’
```

说明: 当前源码与已有 `gem5.opt` 二进制不一致，不能证明当前提交可重新构建。

---

## 3. 与基线 Completion Bar 的对照

`docs/basic-framework-prompt.md:942-950` 要求只有同时满足以下条件，才可声称完成:

1. `N=3, L=2, D=2` 主配置可创建成功。
2. `DSM VA` 固定窗口映射已建立。
3. 普通页分配不会落入 `DSM` / `UbccExclusive`。
4. `HN_i` 能基于统一 `DSM PA` 和 node-local classification 做正确分流。
5. ordinary CHI cross-node checker 存在且真实执行。
6. `EP_RNF_i` / `EP_SNF_i` 已接入 topology，且最小收发路径被 testcase 真实触发。
7. testcase 不能依赖缩小规模、只实例化对象、只打印字符串。

当前状态判断:

1. 第 1 条: **不满足**。`run_real_topo_test.py` 实际失败，报告也承认 `m5.instantiate()` 尚未通过。
2. 第 2 条: **部分满足**。代码中仍有固定 DSM 映射。
3. 第 3 条: **不满足**。没有真实 runtime allocation 验证。
4. 第 4 条: **证据不足，不能判定满足**。对象层 wiring 有改进，但真实 bring-up 未通过。
5. 第 5 条: **不满足**。checker 虽然有调用点，但缺乏真实可运行 testcase 支撑。
6. 第 6 条: **不满足**。Phase 3 测试入口实际失败。
7. 第 7 条: **不满足**。当前仍大量依赖对象层验证、常量判断和二进制字符串扫描。

结论: Completion Bar 仍明显未达标。

---

## 4. 分项驳回意见

### 4.1 主拓扑仍未 bring-up

这是本次最关键的驳回点。

#### 证据

1. `reports/basic-framework-completion-2.md:268-269`:

```text
m5.instantiate() 完整拓扑: Python 对象层 95/95 测试通过，但 m5.instantiate() 因 RubySystem proxy 链深度问题尚未通过。
```

2. 实际复跑 `tests/phase2/run_real_topo_test.py` 失败，报 `RecursionError`。
3. 报错栈包含 `tests/phase2/run_real_topo_test.py:85` 的 `system.ruby = ruby_system`。

#### 为什么不符合要求

1. 基线要求的是主配置真实创建成功，不是“对象层可以先验证”。
2. 上一轮驳回已经明确指出，不能用对象实例化验证替代真正的 topology bring-up。
3. 报告将“未通过 instantiate”写入已知限制，实际上就是承认 `Phase 2` 仍未完成。

#### 修改建议

1. 停止把 `verify_topo_objects.py` 作为 `Phase 2-4` 主验收入口。
2. 以 `run_real_topo_test.py` 或等价的真实 topology instantiate 脚本为第一阻断门。
3. 排查 `ruby_system` 的 parent / child 关系、循环引用和 `system.ruby = ruby_system` 时的对象树冲突。
4. 真正修通后，再做对象计数、downstream、route table 的运行时验证。

---

### 4.2 当前源码无法重新编译

#### 证据

容器内重新构建 `gem5.opt` 时，出现以下编译错误。

1. `EPRNFController.cc` 中函数签名和声明不一致:

```text
error: no declaration matches ‘void gem5::ruby::EPRNFController::recvSnoopMsg(...)’
```

对应源码:

1. `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.hh:168`
   - 声明为 `bool recvSnoopMsg(...)`
2. `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:101-118`
   - 却定义成 `void EPRNFController::recvSnoopMsg(...)`
   - 并且函数体内还 `return true;`

2. `MessageBuffer::enqueue()` 调用参数错误:

```text
error: no matching function for call to ‘MessageBuffer::enqueue(..., curTick())’
```

对应源码:

1. `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:131-133`
2. `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc:42-45`

#### 为什么不符合要求

1. 报告 `reports/basic-framework-completion-2.md:291` 声称“Gem5 CHI 协议编译成功”。
2. 但对当前源码重新构建失败，说明报告结论依赖旧二进制，而非当前工作树。
3. 不能重新构建的代码不能作为通过验收的交付物。

#### 修改建议

1. 先修复当前源码到可重新编译状态，再谈验收。
2. 编译成功后，必须重新运行所有 claimed tests，而不是继续沿用旧 `gem5.opt`。
3. 验收报告中必须明确给出“本次源码重新编译成功”的对应日志或命令结果。

---

### 4.3 Phase 3 测试入口并未真正可运行

#### 证据

1. `reports/basic-framework-completion-2.md:207-229` 把 Phase 2-4 的主要验证落在对象层和二进制字符串扫描上。
2. 新增的 `tests/phase3/test_ep_messages.py` 被描述为覆盖:

```text
TC-EP-3/4/5, TC-ISO-4, TC-G-1/2
```

3. 但实际复跑时，在 `m5.instantiate()` 前后直接失败:

```text
fatal: system.ruby.network.ext_links without default or user set value
```

#### 为什么不符合要求

1. 一个无法运行的 testcase 不能证明任何行为已被真实触发。
2. 报告用 `selfTest()` 和 `strings` 扫描来补位，但真实脚本并未形成可复验路径。

#### 修改建议

1. 先把 `tests/phase3/test_ep_messages.py` 修到可完整运行。
2. 若测试依赖 Ruby network，必须提供最小可用 topology/link 配置。
3. 若想做 controller 级 standalone 测试，则不要走不完整的 `SimpleNetwork` 初始化路径，应使用不依赖未配置 network links 的测试结构。
4. 测试必须直接验证:
   - 注入请求成功
   - `recv*Msg()` 被真实调用
   - 输出 buffer 中出现期望 response/data
   - negative case 真正 fatal

---

### 4.4 `95/95` 对象层验证仍不能替代真实验收

#### 证据

1. `tests/phase2/verify_topo_objects.py:1-3` 明确声明:

```text
Does NOT require m5.instantiate() - validates object relationships.
```

2. 该脚本并未调用 `create_ubcc_system()`，而是在 `33-75` 手工构造了一套平行对象。
3. 该脚本的多项检查仍然是弱化版:
   - `89-95` 的对象计数实际上是常量判断，例如 `NUM == 3`
   - `114-121` 的 `TC-TOPO-4` 只检查本 node 目标“存在于” downstream，不检查“only 本 node”
   - `126-127` 只检查 `has_parent()`
   - `129-134` 只检查 message buffer 属性存在

#### 为什么不符合要求

1. 基线要求的是主配置、真实 wiring、真实 testcase 触发。
2. 手工搭出的对象关系不能代替 `create_ubcc_system()` 真正产生的对象树。
3. “目标存在于 downstream” 不等于 “地址分类正确” 或 “only 同 node 路由”。
4. `has_parent()`、`hasattr()` 和 buffer 存在也不能证明运行路径成立。

#### 修改建议

1. `verify_topo_objects.py` 可以保留为辅助静态检查，但不能作为主验收。
2. 所有 Phase 2 结论必须以真实 `create_ubcc_system()` 路径为准。
3. 将 `TC-TOPO-1/2/3/4` 迁移或镜像到真实 instantiate 测试中。

---

### 4.5 报告对 `TC-TOPO-4` 的表述仍然夸大

#### 证据

1. 报告 `reports/basic-framework-completion-2.md:77` 写的是:

```text
每个 HN_i 的 downstream 包含且仅包含本 node 的 L_SNF_i、DL_SNF_i、EP_SNF_i。
```

2. 但 `tests/phase2/verify_topo_objects.py:114-121` 实际只检查:
   - downstream 中存在本 node `L_SNF`
   - downstream 中存在本 node `DL_SNF`
   - downstream 中存在本 node `EP_SNF`

并没有检查 downstream 中是否还含有其他 node 的目标。

#### 为什么不符合要求

1. 报告结论和测试内容不一致。
2. “包含” 与 “包含且仅包含” 是不同强度的断言。

#### 修改建议

1. 为每个 `HN_i` 明确断言 downstream 的完整集合恰好为本 node 的三类目标。
2. 在真实 topology instantiate 路径中做同样验证，而不是只在对象层做。

---

### 4.6 Phase 1 的关键验收缺口仍未补齐

#### 证据

1. `tests/phase1/run_phase1_test.py:92-130` 仍然只做:
   - `phys_pool_id` 值打印
   - 预设地址区间的几何关系检查
2. `tests/phase1/test_phase1.py:48-86` 仍是常量运算，不检查真实进程分配结果。
3. `reports/basic-framework-completion-2.md:270` 继续承认:

```text
Phase 1 reserved-range 运行时验证 ... 更完整的 reserved-range 实现可在后续单独迭代。
```

#### 为什么不符合要求

1. 基线要求的是“普通页分配不会落入 `DSM` / `UbccExclusive`”。
2. 当前依旧没有真实检查 heap/stack/.data/.text 的 PA。
3. 该问题在上一份驳回报告中已明确指出，本次没有被真正关闭。

#### 修改建议

1. 增加真实 runtime allocation 验证。
2. 对测试程序中的:
   - heap
   - stack
   - globals / `.data`
   - code / text page
   建立实际 VA->PA 检查。
3. 若声称 `phys_pool_id` 足以满足第一版要求，则必须给出实际 page allocation 结果，而不是只给 pool id 配置值。

---

### 4.7 checker 仍缺少真实可运行验收链路

#### 证据

1. 代码里确实新增了 `_backend->checkAddr(...)` 调用点:
   - `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:106-107,273-274,284-285`
   - `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc:72-73,101-102`
2. 但用于验证这些调用点的 `tests/phase3/test_ep_messages.py` 不能成功运行。
3. 报告对 `TC-G-4` 的主要佐证仍是 `strings` 扫描，而非运行时日志。

#### 为什么不符合要求

1. 代码里“有调用点”不等于 testcase 已经真实覆盖。
2. checker 的核心要求是“存在且真实执行”。
3. 当前仍缺少可运行 negative testcase 证明非 DSM 和跨 node DSM 在真实路径上 fatal。

#### 修改建议

1. 修通 Phase 3 测试入口。
2. 在可运行的 controller/message 测试里直接触发:
   - DSM local pass
   - non-DSM fatal
   - cross-node DSM fatal
3. 记录运行时输出而不是仅做 `strings` 扫描。

---

## 5. 对 `reports/basic-framework-completion-2.md` 的具体评价

这份报告相比上一版更谨慎，至少承认了一部分未完成项，但仍存在以下问题:

1. 一方面第 6 节承认完整拓扑 `instantiate` 未通过；另一方面第 7 节仍给出接近“全部整改完成”的总结，这会误导验收判断。
2. 把对象层 `95/95 PASS` 作为 Phase 2-4 的主体证据，仍然是在绕开上轮驳回强调的“真实路径验证”。
3. 将二进制字符串扫描当作 Phase 3/4 的主要佐证强度过低。
4. 对某些 testcase 的验证强度表述仍高于实际测试内容，例如 `TC-TOPO-4`。

更准确的状态表述应当是:

```text
部分 Python wiring 已修复；部分 EP skeleton 代码已补充；
但完整 topology instantiate、当前源码重编译、可运行 EP testcase、
以及 Phase 1 runtime allocation validation 仍未通过。
```

---

## 6. 下一步整改要求

建议按以下顺序整改。

### 6.1 第一优先级: 修到当前源码可编译

1. 修复 `EPRNFController.cc` 中 `recvSnoopMsg` 的签名错误。
2. 修复 `MessageBuffer::enqueue()` 调用参数。
3. 重新完整构建 `gem5.opt`。
4. 重新运行全部 testcase，禁止继续依赖旧二进制。

### 6.2 第二优先级: 修通真实 topology instantiate

1. 以 `tests/phase2/run_real_topo_test.py` 为主阻断门。
2. 消除 `system.ruby = ruby_system` 相关的递归 parent/path 问题。
3. 修通后再在真实路径中检查 `TC-TOPO-1/2/3/4`。

### 6.3 第三优先级: 修通可运行的 EP 行为测试

1. 让 `tests/phase3/test_ep_messages.py` 能完整执行。
2. 覆盖:
   - `TC-EP-3`
   - `TC-EP-4`
   - `TC-EP-5`
   - 非 DSM fatal
   - cross-node DSM fatal
3. 用运行时行为和输出 buffer 断言替代 `strings` 扫描。

### 6.4 第四优先级: 补齐 Phase 1 runtime validation

1. 真实检查 heap/stack/.data/.text 的 PA。
2. 真实检查普通页不会进入 `DSM_GLOBAL` / `UbccExclusive`。
3. 真实检查 per-node pool binding 的实际分配效果。

---

## 7. 最终判定

本次提交可以认定为:

1. 修复了一部分 Python 对象层 wiring。
2. 补充了一部分 EP skeleton 代码与 `checkAddr()` 调用点。
3. 比第一版更接近目标。

但仍不能认定为:

1. `basic framework` 已完成。
2. `N=3, L=2, D=2` 主配置已成功 bring-up。
3. 当前源码可重新构建并通过完整验收。
4. Phase 3/4 的真实行为测试已成立。

因此，`reports/basic-framework-completion-2.md` 的完成声明不应被接受。当前状态应更新为:

```text
basic framework: partially repaired, acceptance rejected again, core bring-up still failing
```
