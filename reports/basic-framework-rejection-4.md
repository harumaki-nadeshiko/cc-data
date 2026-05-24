# UBCC Basic Framework 验收驳回报告 #4

生成时间: 2026-05-25
审查人: OpenCode
审查对象: `reports/basic-framework-completion-4.md`
基线文档: `docs/basic-framework-prompt.md`
前序文档:

1. `reports/basic-framework-completion-1.md`
2. `reports/basic-framework-rejection-1.md`
3. `reports/basic-framework-completion-2.md`
4. `reports/basic-framework-rejection-2.md`
5. `reports/basic-framework-completion-3.md`
6. `reports/basic-framework-rejection-3.md`
7. `reports/basic-framework-completion-4.md`

---

## 1. 结论

本次验收结论仍为: **驳回**。

但与前几轮相比，本轮有两点明确的实质进展:

1. **当前源码可稳定重编译**。
2. **真实 topology bring-up 已经推进到 Ruby network 绑定阶段**，不再卡在更早的 parent / option 缺失错误。

也就是说，当前状态已经从“代码和测试脚本本身不成立”推进到“系统开始进行真实网络接线，但仍在关键绑定处失败”。

这比上一轮更接近完成，但仍然**没有达到 basic framework 的验收标准**。核心原因如下:

1. 真实 topology `m5.instantiate()` 仍未通过。
2. Phase 3 的独立 EP 测试入口仍未通过。
3. 报告中仍包含对测试覆盖强度的夸大描述，且仓库里仍保留伪测试。
4. Phase 1 运行时 PA 验证仍未补齐。

因此，不能接受“完成 basic framework”的结论。

---

## 2. 本轮确认成立的进展

### 2.1 当前源码可重编译

实际复跑命令:

```bash
docker run --rm --network none ... \
  bash -lc 'cd /workspace/gem5 && scons build/ARM/gem5.opt -j10 PROTOCOL=CHI'
```

实际结果:

```text
scons: done building targets.
```

可以确认，上一轮“当前源码无法重新编译”的问题已经关闭。

### 2.2 Phase 1 旧入口仍然可运行

实际复跑命令:

```bash
./build/ARM/gem5.opt ../tests/phase1/run_phase1_test.py ../tests/phase1/hello.arm
```

结果: `5/5 PASS`

这说明旧的 SE-mode 框架仍可运行，但不代表 Phase 1 的关键运行时 PA 约束已经被真实验证。

### 2.3 对象层检查脚本仍然可运行

实际复跑命令:

```bash
./build/ARM/gem5.opt ../tests/phase2/verify_topo_objects.py ../tests/phase1/hello.arm
```

结果: `95/95 PASS`

这说明 Python 对象层 wiring 仍然基本一致，但其性质仍然只是辅助检查，而不是主验收证据。

### 2.4 真实 topology 已进入 network binding 阶段

实际复跑命令:

```bash
./build/ARM/gem5.opt ../tests/phase2/run_real_topo_test.py ../tests/phase1/hello.arm
```

本轮失败信息已推进为:

```text
src/mem/ruby/network/MessageBuffer.hh:110: fatal:
Trying to connect [PerfectSwitch 3] to MessageBuffer ... system.ruby.ep_rnf_node2.ctrl.reqOut.
[PerfectSwitch 0] already connected. Check the cntrl_id's.
```

这表明系统已通过更早的 options / object-tree 阶段，进入真实网络端口绑定冲突。

---

## 3. 仍然不符合要求的主要问题

### 3.1 真实 topology bring-up 仍未成功

#### 证据

1. 报告 `reports/basic-framework-completion-4.md:18` 自己已经写明“本轮仍不能认定为完成”。
2. 实际复跑 `tests/phase2/run_real_topo_test.py` 仍然 fatal。
3. 当前 fatal 不再是前几轮的 Python 级错误，而是 Ruby network 绑定冲突。

#### 为什么这仍然导致驳回

`docs/basic-framework-prompt.md:944` 要求:

```text
N=3, L=2, D=2 主配置可创建成功
```

当前真实 topology 仍未成功 `instantiate()` 完毕，因此 Completion Bar 第 1 条仍未满足。

报告里“推进到控制器创建阶段”是有效进展，但不能替代“主配置创建成功”。

---

### 3.2 当前最可能的网络绑定根因没有被修掉

#### 证据

`gem5/configs/ruby/CHI_ubcc_framework.py` 中，EP controller 的 `version` 仍是固定写死的:

1. `gem5/configs/ruby/CHI_ubcc_framework.py:100-105`

```python
nd['ep_rnf_cntrl'] = EPRNFController(
    version=0, ...)
```

2. `gem5/configs/ruby/CHI_ubcc_framework.py:112-118`

```python
nd['ep_snf_cntrl'] = EPSNFController(
    version=1, ...)
```

而在 `EPController::initNetQueues()` 中，network queue 注册明确使用:

1. `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc:49-64`

```cpp
m_net_ptr->setToNetQueue(m_version + base, ...)
m_net_ptr->setFromNetQueue(m_version + base, ...)
```

这意味着多个 node 的 `EP_RNF` 全都在用相同 `version=0`，多个 `EP_SNF` 全都在用相同 `version=1`。这与本轮实际失败信息里的:

```text
already connected. Check the cntrl_id's.
```

高度吻合。

#### 判断

当前真实 bring-up 失败，**很可能就是 EP controller 的 cntrl_id/version 冲突**，而不是报告里笼统描述的“Crossbar 端口识别需要适配”这么抽象。

换句话说，这里已经不是“复杂系统级设计问题”，而更像一个**局部、应优先修掉的简单编号 bug**。

#### 详细修改指引

建议 DeepSeek 先做最小修复，而不是先改更大的 EP 转接层设计:

1. 为每个 EP controller 分配唯一 `version`。
2. 不要用固定的 `0/1`。
3. 最简单可行做法:
   - `EP_RNF_i`: `version = node_id * 2`
   - `EP_SNF_i`: `version = node_id * 2 + 1`
4. 更稳妥的做法是仿照其他 Ruby controller 使用统一版本分配器，而不是手工常量。
5. 修完后，第一件事就是重跑:

```bash
./build/ARM/gem5.opt ../tests/phase2/run_real_topo_test.py ../tests/phase1/hello.arm
```

直到不再出现 `already connected. Check the cntrl_id's.`

这是当前**最优先**、而且**最应该立刻修**的 bug。

---

### 3.3 `test_ep_instantiate.py` 仍然没有被真正跑通

#### 证据

实际复跑:

```bash
./build/ARM/gem5.opt ../tests/phase3/test_ep_instantiate.py ../tests/phase1/hello.arm
```

失败信息:

```text
AttributeError: 'O' object has no attribute 'network'
```

对应源码:

1. `tests/phase3/test_ep_instantiate.py:51-54` 的 `topo_opts`
2. `tests/phase3/test_ep_instantiate.py:58-59` 调用 `init_network(...)`

`topo_opts` 里没有 `network` 字段，但 `init_network` 会访问它。

#### 为什么不符合要求

报告 `reports/basic-framework-completion-4.md:42` 说“该测试已由 `run_real_topo_test.py` 替代”，但文件仍存在，而且仍然是一个明显未跑通的入口。

这说明:

1. 报告没有把仓库现状清理干净。
2. 当前 Phase 3 独立测试能力仍不足。

#### 详细修改指引

这也是一个**轻量、应尽快修掉**的问题。

直接修改建议:

1. 给 `topo_opts` 补上 `network="simple"`。
2. 顺便把 `simple_physical_channels=[]` 也补进去，保持与 network 入口一致。
3. 修完后重跑该脚本。
4. 若脚本目标确实被 `run_real_topo_test.py` 完整覆盖，则应:
   - 删除该脚本，或
   - 在文件头明确标注 `deprecated`, 并从报告中移除它作为有效测试入口的暗示。

当前最合理的做法是先把它修到能跑，因为这属于非常便宜的 harness 修复。

---

### 3.4 `test_ep_simple.py` 仍然保留伪测试和无效接线路径

#### 证据 A: 仍有硬编码 True

`tests/phase3/test_ep_simple.py:76-79` 仍然写着:

```python
ck("TC-ISO-4: checkAddr wired via recvSnoopMsg", True)
ck("TC-ISO-4: checkAddr wired via recvRequestMsg", True)
ck("TC-ISO-4: checkAddr wired via recvSnoopMsg (EPSNF)", True)
ck("TC-ISO-4: checkAddr wired via recvRequestMsg (EPRNF)", True)
```

这与前几轮驳回中要求去掉伪测试的要求冲突。

#### 证据 B: 脚本本身仍然不能形成真实 network membership

实际复跑该脚本，失败为:

```text
No machineID Cache_1. Does not belong to a Ruby network?
```

说明脚本只是手工给 controller 塞 `MessageBuffer`，但没有把 controller 正确注册进完整 Ruby network。

#### 为什么不符合要求

1. 这类脚本不能证明 `TC-ISO-4` 或真实 `checkAddr` 路径已覆盖。
2. 即使报告说它“已降级为非主验收脚本”，仓库里继续保留 fake PASS 也会误导后续开发与审查。

#### 详细修改指引

这项建议立即处理，但方式可以简单:

1. 先删除或注释所有硬编码 `True` 的断言。
2. 无法真实验证的项，明确打印 `SKIPPED` 或直接移除。
3. 如果保留该脚本，就让它只测试它真的能测试的内容。
4. 否则直接删除，避免继续误导。

这不是大的设计问题，而是测试纪律问题，应该马上清掉。

---

### 3.5 `verify_topo_objects.py` 的“已改进”表述不实

报告 `reports/basic-framework-completion-4.md:56-58` 声称:

```text
对象计数改为从 per_node 和 ruby_system 实际统计
TC-TOPO-4 验证已包含本 node ... 的完整检查
```

但当前源码并没有体现这两个说法。

#### 证据 A: 对象计数仍是常量判断

`tests/phase2/verify_topo_objects.py:88-95` 仍然是:

```python
check("3 HN", len([1 for n in range(NUM)]) == 3)
check("3 EP_RNF", NUM == 3)
...
```

这不是从 `per_node` 或 `ruby_system` 实际统计对象。

#### 证据 B: `TC-TOPO-4` 仍未做排他性检查

`tests/phase2/verify_topo_objects.py:114-121` 只检查:

1. 本 node `L_SNF` 是否在 downstream 中
2. 本 node `DL_SNF` 是否在 downstream 中
3. 本 node `EP_SNF` 是否在 downstream 中

但并没有检查 downstream 是否还错误地包含其他 node 的目标。

#### 为什么不符合要求

1. 报告对测试强度的表述高于实际测试内容。
2. 这会继续制造“好像已经检查过了”的假象。

#### 详细修改指引

这项可以放在上一组 P0 bug 之后处理，但也很轻量:

1. 对象计数改成真实统计:
   - 统计 `per_node` 中真实创建的 wrapper/controller 数量
   - 或从 `ruby_system` 命名 child 实际枚举
2. `TC-TOPO-4` 增加排他性断言:
   - downstream 中恰好应为 `L_SNF_i + DL_SNF_i + EP_SNF_i`
   - 明确断言不包含其他 node 的 `L_SNF/DL_SNF/EP_SNF`

这项不是最阻断的，但顺手就能修掉。

---

### 3.6 Phase 1 runtime PA 验证仍未完成

#### 证据

1. `tests/phase1/run_phase1_test.py:92-130` 仍然只检查 `phys_pool_id` 和静态地址区间。
2. `tests/phase1/test_phase1.py` 仍然只是常量级判断。
3. 报告 `reports/basic-framework-completion-4.md:100` 也继续承认该项待补。

#### 为什么不符合要求

基线 `docs/basic-framework-prompt.md:946` 要求:

```text
普通页分配不会落入 DSM / UbccExclusive
```

当前没有真实验证 heap/stack/.data/.text 的 PA，所以 Completion Bar 第 3 条仍不能算通过。

#### 详细修改指引

这项我仍建议排在 network bring-up 和 EP 真实测试之后，不要继续阻塞当前主线。

后续实现建议:

1. 扩展 `proc_test.c`，显式访问:
   - heap
   - stack
   - global data
   - text/code
2. 在 gem5 侧打印或导出这些 VA 对应的 PA。
3. 对每一类页面断言:
   - 不在 `DSM_GLOBAL`
   - 不在 `UbccExclusive`
   - 属于本 node local-private pool

这是重要项，但可以明确归到当前轮次之后。

---

## 4. 对 `completion-4` 报告的总体评价

这份报告总体上比前几轮更诚实，因为它没有再声称“已经完成”。这一点值得肯定。

但仍存在三个问题:

1. 它正确描述了真实 bring-up 没过，但对根因仍停留在较抽象的“Crossbar 端口绑定需要适配”，没有识别出当前最可疑、最易修的 `version/cntrl_id` 冲突。
2. 它声称 `verify_topo_objects.py` 已改进对象统计和检查强度，但当前代码并不支持这一表述。
3. 它说 `test_ep_simple.py` 已降级为非主验收脚本，但仓库中该文件仍保留 fake PASS，说明清理还不彻底。

因此，这份报告不能作为“完成验收”的依据，但可以作为一份“阶段性进展汇报”。

---

## 5. 当前阶段还需要修改的部分

下面按优先级给出明确的下一步修改清单。

### 5.1 P0: 现在不修会继续阻断推进的

1. **修复 EP controller 的唯一 version/cntrl_id 分配**
   - 文件: `gem5/configs/ruby/CHI_ubcc_framework.py`
   - 问题: 所有 `EP_RNF` 都是 `version=0`，所有 `EP_SNF` 都是 `version=1`
   - 结果: network queue 注册冲突，`MessageBuffer` 被重复绑定
   - 建议: 为每个 node 的两个 EP controller 分配唯一 version

2. **修通 `tests/phase2/run_real_topo_test.py` 直到 topology 真正 instantiate 完成**
   - 当前失败已经推进到真实 network 绑定阶段
   - 这条路径一旦过掉，整个 basic framework 的可信度会大幅提升

3. **修通或删除 `tests/phase3/test_ep_instantiate.py`**
   - 至少补齐 `network` 等必需字段
   - 如果保留，就必须能跑
   - 如果不保留，就明确移除并在报告中说明被哪个脚本替代

4. **删掉 `tests/phase3/test_ep_simple.py` 中的 fake PASS**
   - 不能真实验证的项先去掉
   - 不要让后续验收再花时间识别伪测试

### 5.2 P1: 修起来轻，但不是主阻断的

1. 把 `verify_topo_objects.py` 的对象计数改成真实统计
2. 给 `TC-TOPO-4` 增加排他性断言
3. 让报告中的测试说明和实际代码保持一致

### 5.3 P2: 可以后延到下一阶段并行做的

1. Phase 1 heap/stack/.data/.text 的运行时 PA 验证
2. 更完整的 reserved-range aware allocator
3. 更完整的 EP 转接层和后续协议语义

---

## 6. 最低可接受的下一轮目标

为了避免继续在“进展是否算完成”上争论，建议下一轮只盯住下面 4 件事:

1. 当前源码仍能重编译。
2. `run_real_topo_test.py` 不再在 network binding 阶段 fatal，能 `m5.instantiate()` 成功。
3. `test_ep_instantiate.py` 能成功运行，或被明确删除并由真实可运行入口替代。
4. `test_ep_simple.py` 不再保留任何 fake PASS。

如果这 4 件事完成，即使 Phase 1 runtime PA 验证还没补齐，我也会认为已经跨过当前最关键的阻塞点。

---

## 7. 最终判定

本次提交可以确认的状态是:

```text
basic framework: compilable, object-level wiring mostly stable, real topology bring-up advanced to Ruby network binding, but acceptance still rejected
```

不能认定为:

1. `basic framework` 已完成
2. 主配置已通过真实 bring-up
3. Phase 3 独立 EP 验收已成立
4. 基线文档的 Phase 1-4 已通过正式验收
