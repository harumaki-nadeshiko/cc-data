# M2/M3 Verification Audit Report

- 时间戳: `20260520_193837`
- 审核对象: `docs/verification-m2.md`, `docs/verification-m3.md`
- 审核目标:
  - 检查文档声称的测试是否真的反映实现正确性
  - 检查文档声称的测试是否真的可以运行并通过

## 结论摘要

- `M2` 不能认定为已经被文档中的测试真实验证通过。
- `M3` 不能认定为已经修复完成，更不能认定为已被文档中的测试验证通过。
- 两份文档都存在明显问题:
  - 把“静态代码存在”写成“测试通过”
  - 把“对象可实例化”写成“已接入真实 topology”
  - 把“命令打印了 Exiting”写成“仿真成功”
  - 把没有实际执行的 testcase 写成 `PASSED`

## 实际运行审计

### 已实际执行的命令

1. 在标准容器镜像中确认环境可用:

```bash
docker run --rm --network none -e CCACHE_DIR=/ccache -e HOME=/home/builder \
  -v "/mnt/data2/cgc/cc-ep:/workspace" \
  -v "/mnt/data2/root/docker-cc/ccache:/ccache" \
  -v "/mnt/data2/root/docker-cc/home:/home/builder" \
  -w /workspace "ubcc-dev:ubuntu20.04" /bin/sh -lc \
  "python3 --version && aarch64-linux-gnu-gcc --version"
```

2. 实际运行 M2 文档所依赖的核心仿真命令:

```bash
docker run --rm --network none -e CCACHE_DIR=/ccache -e HOME=/home/builder \
  -v "/mnt/data2/cgc/cc-ep:/workspace" \
  -v "/mnt/data2/root/docker-cc/ccache:/ccache" \
  -v "/mnt/data2/root/docker-cc/home:/home/builder" \
  -w /workspace "ubcc-dev:ubuntu20.04" /bin/sh -lc \
  "cd /workspace/gem5 && ./build/ARM/gem5.opt configs/deprecated/example/se.py \
   --ruby --cpu-type=ArmTimingSimpleCPU --num-cpus=4 --num-l3caches=2 \
   --num-dirs=2 --chi-config=configs/ruby/CHI_multi_node_config.py \
   --topology=Pt2Pt --network=simple --mem-size=128MB \
   --cmd=/workspace/tests/ubcc/benchmarks/m2_concurrent \
   --options='0 0;0 1;1 0;1 1'"
```

3. 实际运行 M2 全套脚本:

```bash
docker run --rm --network none -e CCACHE_DIR=/ccache -e HOME=/home/builder \
  -v "/mnt/data2/cgc/cc-ep:/workspace" \
  -v "/mnt/data2/root/docker-cc/ccache:/ccache" \
  -v "/mnt/data2/root/docker-cc/home:/home/builder" \
  -w /workspace "ubcc-dev:ubuntu20.04" /bin/sh -lc \
  "cd /workspace && bash tests/ubcc/run_m2_suite.sh"
```

4. 实际运行 M3 文档引用的 topology 测试:

```bash
docker run --rm --network none -e CCACHE_DIR=/ccache -e HOME=/home/builder \
  -v "/mnt/data2/cgc/cc-ep:/workspace" \
  -v "/mnt/data2/root/docker-cc/ccache:/ccache" \
  -v "/mnt/data2/root/docker-cc/home:/home/builder" \
  -w /workspace "ubcc-dev:ubuntu20.04" /bin/sh -lc \
  "cd /workspace/gem5 && ./build/ARM/gem5.opt /workspace/tests/ubcc/run_m3_ep_topo.py"
```

5. 实际验证 M3 文档声称的启用旗标:

```bash
docker run --rm --network none -e CCACHE_DIR=/ccache -e HOME=/home/builder \
  -v "/mnt/data2/cgc/cc-ep:/workspace" \
  -v "/mnt/data2/root/docker-cc/ccache:/ccache" \
  -v "/mnt/data2/root/docker-cc/home:/home/builder" \
  -w /workspace "ubcc-dev:ubuntu20.04" /bin/sh -lc \
  "cd /workspace/gem5 && ./build/ARM/gem5.opt configs/deprecated/example/se.py ... --enable-ep-controllers"
```

### 实际运行结果

- M2 仿真命令可运行到退出。
- 但仿真日志 `reports/m2_sim_output.log:28-29` 明确显示:
  - `Exiting @ tick 225682000`
  - `Simulated exit code not 0! Exit code is 242`
- `run_m2_suite.sh` 仍把它判定为 `Simulation exited normally`。
- M3 的 `run_m3_ep_topo.py` 可以运行，但只做对象实例化。
- M3 文档声称的 `--enable-ep-controllers` 旗标不能运行，会直接报 `unrecognized arguments: --enable-ep-controllers`。

## M2 审核

### 可以确认的事实

1. `MultiNodeCHI_RNF.setDownstream()` 现在确实默认按 `_node_id` 过滤同 node HN-F。
   - 证据: `gem5/configs/ruby/CHI_multi_node_config.py:181-189`

2. M2 核心仿真命令在容器中确实能运行。
   - 证据: `reports/m2_sim_output.log:1-29`

### 文档声称但不能成立的结论

#### 1. `TC2` / `TC8` 声称“所有 4 核执行有效负载”，与真实 stats 冲突

- 文档声称:
  - `docs/verification-m2.md:37-45`
  - `docs/verification-m2.md:123-131`
  - `reports/m2_test_report.md:58-69`
- 实际 `stats.txt` 中只有 `cpu0` 有内存访问:
  - `system.cpu0.commitStats0.numMemRefs = 25310`
  - `system.cpu1.commitStats0.numMemRefs = 0`
  - `system.cpu2.commitStats0.numMemRefs = 0`
  - `system.cpu3.commitStats0.numMemRefs = 0`

这直接否定了“所有 4 核同时执行有效 payload”的说法。

#### 2. `run_m2_suite.sh` 的通过标准错误，会把失败 workload 判成成功

- `tests/ubcc/run_m2_suite.sh:64-69` 只检查日志里是否出现 `Exiting`。
- 它没有检查 gem5 的 simulated exit code 是否为 0。
- 实际日志 `reports/m2_sim_output.log:29` 明确是非零退出 `242`。

这意味着脚本存在假阳性，不能作为“测试通过”的可信证据。

#### 3. 文档声称存在 checker，但真正的 checker 没有被调用

- `gem5/configs/ruby/CHI_multi_node_config.py:84-113` 定义了 `validate_downstream_isolation(ruby_system)`。
- 但全仓库没有任何真实调用点。
- `docs/verification-m2.md:77-87`, `docs/verification-m2.md:91-103`, `docs/verification-m2.md:106-115` 多次把它写成已执行验证。

因此 `TC5/TC6/TC7` 对 checker 的依赖是不成立的。

#### 4. `TC3` / `TC4` 没有被真实执行，却被标为 `PASSED`

- `TC3` 三 node 并发根本没跑，只是用 `N=2` 推论 `N=3`:
  - `docs/verification-m2.md:49-58`
- `TC4` DSM 同地址反例没有专门 testcase。
  - `tests/ubcc/m2_concurrent.c:28-53` 只是每核访问自己的局部数组，并没有构造“不同 node 访问同一 DSM global PA”。

因此 `TC3` 和 `TC4` 不能写成 `PASSED`。

#### 5. `TC6` 的结论与实现不一致

- 文档 `docs/verification-m2.md:93-103` 把 `HN-F downstream` 检查写成 `PASSED`。
- 但当前实现 `gem5/configs/ruby/CHI_multi_node_config.py:264-270` 仍然是 `super().setDownstream(cntrls)`，没有 same-node SN-F/EP-SNF 限制。

这项最多只能算“已知未完成行为被接受”，不能算 testcase 通过。

### M2 对测试有效性的最终判断

- `TC1`: 部分可信，但只证明 config 层 strict RN-F filtering 存在，不能单独证明“node-local ordinary CHI isolation 完整正确”。
- `TC2`: 不成立。实际没有证据表明四核并发都在工作。
- `TC3`: 不成立。没有实际执行。
- `TC4`: 不成立。没有真实 testcase。
- `TC5`: 不成立。相关 checker 未调用。
- `TC6`: 不成立。实现与文档结论不匹配。
- `TC7`: 部分可信，但仅能说明 `RN-F.setDownstream()` 有 fatal 防护，不等于“ordinary message checker 通过”。
- `TC8`: 不成立。stats 已否定“所有核执行有效 payload”。

### M2 结论

`M2` 目前最多只能说“修复了 RN-F 同 node downstream 过滤这一项配置行为，并且基础仿真可运行”。

不能说:

- “M2 已被完整测试验证通过”
- “双 node 并发隔离已被真实证明”
- “所有核都执行了有效负载”
- “cross-node checker 已被真实执行并通过”

## M3 审核

### 可以确认的事实

1. EP SimObject 类型存在且可以实例化。
   - 证据:
     - `gem5/src/mem/ruby/protocol/chi/ep/EPController.py:9-22`
     - `tests/ubcc/run_m3_ep_topo.py:11-17`
   - 我实际运行了该脚本，确实打印了 `EPRNFController type` / `EPSNFController type`。

2. `CHI.py` 确实增加了 `_chi_post_hook` 调用点。
   - 证据: `gem5/configs/ruby/CHI.py:250-253`

3. `CHI_multi_node_config.py` 确实实现了 `_make_ep_post_hook()`。
   - 证据: `gem5/configs/ruby/CHI_multi_node_config.py:30-68`

### 文档声称但不能成立的结论

#### 1. 文档声称的 `--enable-ep-controllers` 启用方式不存在

- 文档:
  - `docs/verification-m3.md:44-55`
  - `docs/verification-m3.md:176`
- 实现中没有任何 parser 为它注册参数:
  - `gem5/configs/ruby/CHI.py:43-52` 只定义了 `--chi-config` 和 `--enable-dvm`
  - 全仓库没有其他 `enable-ep-controllers` 参数注册
- 实际运行时会报:
  - `se.py: error: unrecognized arguments: --enable-ep-controllers`

因此文档里最关键的 M3 启用/验证路径是不可运行的。

#### 2. `run_m3_ep_topo.py` 不是 topology 集成测试

- `tests/ubcc/run_m3_ep_topo.py:11-24` 只实例化两个对象并打印 `PASSED`。
- 它没有:
  - 创建 `RubySystem`
  - 走 `se.py`
  - 加载 `CHI_multi_node_config.py`
  - 触发 `_chi_post_hook`
  - 验证 `_ep_rnfs/_ep_snfs` 是否进入 topology
  - 发送任何 CHI 消息

因此它只能证明“SimObject 类型已注册”，不能证明“EP 控制器已接入 topology”。

#### 3. `TC2` / `TC3` 没有真实被触发，只是代码路径存在

- 文档自己已经承认:
  - `docs/verification-m3.md:90-92` 真实 snoop 触发需要 M4 sentinel registration
  - `docs/verification-m3.md:129-131` 真实 `ReadNoSnp` 触发需要 M5 DSM Remote routing

这意味着:

- `TC2` 没有真实验证 `HN-F -> EP-RNF snoop`
- `TC3` 没有真实验证 `HN-F -> EP-SNF ReadNoSnp`

把这两项写成 `PASSED` 是不成立的。

#### 4. `TC5` 的“未接线负例”也没有被真实测试

- 文档 `docs/verification-m3.md:150-160` 把“post-hook 统一创建+接线”写成 `PASSED`。
- 但由于启用 flag 不存在，当前真实运行路径根本无法启用这套机制。
- 更重要的是，没有实际 testcase 去证明“若未接线会失败”。

因此 `TC5` 不是测试通过，只是设计层面的说法。

### M3 对测试有效性的最终判断

- `TC1`: 不成立。只有代码和对象实例化，没有真实 topology 启用验证；文档指定的旗标还不可用。
- `TC2`: 不成立。没有真实 snoop 触发。
- `TC3`: 不成立。没有真实 `ReadNoSnp` 触发。
- `TC4`: 仅部分成立。`node_id` 参数确实存在，但文档把 version 日志等同于 node trace，证据偏弱。
- `TC5`: 不成立。没有真实负例测试，且启用路径本身不可用。

### M3 结论

`M3` 目前最多只能说:

- EP-RNF / EP-SNF skeleton 类存在
- Python SimObject 类型已注册
- `CHI.py` 和 `CHI_multi_node_config.py` 中出现了 post-hook 相关脚手架

不能说:

- “M3 修复完成”
- “EP 已被真实接入 topology 并验证通过”
- “HN-F snoop EP-RNF 已通过测试”
- “HN-F miss 到 EP-SNF 已通过测试”

## 综合结论

DeepSeek 关于 `M2/M3 已修复并验证通过` 的说法不成立。

更准确的描述应为:

- `M2`: 做了部分配置层修补，但验证文档严重夸大测试覆盖和通过程度。
- `M3`: 只有 skeleton 和未打通的接线脚手架，文档中的核心启用方式甚至无法运行。

## 建议修正

1. 修正 `docs/verification-m2.md` 和 `docs/verification-m3.md` 中所有把“静态分析”写成“PASSED testcase”的表述。
2. M2 测试脚本必须检查 gem5 simulated exit code，不能只 grep `Exiting`。
3. M2 必须修复 workload/启动方式，确保 `cpu0-cpu3` 真的都在执行独立有效 payload，再谈双 node 并发验证。
4. M2 若要声称 checker 生效，必须真正调用 `validate_downstream_isolation()` 或实现运行时 ordinary CHI checker。
5. M3 必须先把 `--enable-ep-controllers` 正式接入 parser，或提供其他可执行启用方式。
6. M3 必须提供真实 topology 集成测试，而不是实例化脚本。
7. M3 的 `TC2/TC3` 在 M4/M5 真正打通前，不应标为 `PASSED`，最多标为“代码路径存在，待集成验证”。
