# TC98 / TC134 远端进程参数日志审计

## 适用边界

远端不执行本仓库的 `tests/e2e/run_multi.sh`，也没有本仓库的 supervisor。
远端继续使用其既有启动、超时和进程回收机制。本工具只做一件事：从远端各模拟器
stdout 中读取进程实际 argv 与 effective 配置，并与本机测定后固化的合同比较。

本机合同位于：

```text
configs/tc98_tc134_process_contracts.json
```

远端需要使用包含本次 `[PROCESS-MANIFEST]` 改动的 UBIO、gem5 和 networksim：

```text
[PROCESS-MANIFEST] {...}
```

每个模拟器进程只输出一条。当前Port由每个进程内的单一owner线程使用，因此启动argv
是进程级证据，不需要远端启动器生成额外manifest。

gem5同时记录：

```text
process_argv：从/proc/self/cmdline读取的完整gem5进程命令
config_argv：test_e2e.py收到的配置参数
```

## 所需日志

TC98/TC134 8n2s各需要：

```text
16份 UBIO stdout
8份 gem5 stdout
1份 networksim stdout
```

日志可以保持远端目录结构，也可以全部平铺到一个目录。审计器递归搜索
`.log/.txt/.out/.err`及无扩展名文件。

远端不需要提供：

```text
run_multi.sh输出
launch_commands_tc*.jsonl
launch_manifest.txt
supervisor日志或配置
```

## TC98

远端运行结束或卡死并完成日志收集后，在可以访问日志的仓库环境执行：

```bash
python3 scripts/audit_tc_launch.py /path/to/remote_tc98_logs \
  --tc 98 --formal
```

审计项包括：

- 16个唯一UBIO plane、8个唯一gem5 node、1个networksim；
- 实际拓扑为8 nodes × 2 sockets；
- UBIO实际argv中不存在fault rules；
- UBIO `ways=1`、spill、H64、batch-RS=1；
- gem5 O3、sequencer=16、silent/direct/batch=`0/0/1`；
- UBIO进程实际看到link/sync=`2500/2500ps`、port HWM=8192；
- networksim实际max pending=65536；
- UBIO和gem5 metadata bytes均为134217728；
- gem5没有未知参数，HA/Clear profile为`ubcc/ack`。

`sequencer_max_outstanding=0`在manifest中表示“未显式覆盖”，审计器按当前本机
RubySequencer模型默认值16计算effective值，不把0误当成实际容量。

## TC134

按远端实际运行的profile指定：

```bash
python3 scripts/audit_tc_launch.py /path/to/remote_tc134_logs \
  --tc 134 --formal --profile naive

python3 scripts/audit_tc_launch.py /path/to/remote_tc134_logs \
  --tc 134 --formal --profile spill-noopt

python3 scripts/audit_tc_launch.py /path/to/remote_tc134_logs \
  --tc 134 --formal --profile optimized
```

| profile | UBIO bloom | policy/schema | UBIO batch | gem5 silent/direct/batch |
|---|---:|---|---:|---|
| `naive` | 0 | naive/disabled | 0 | 0 / 0 / 0 |
| `spill-noopt` | 61440 | spill/h64 | 0 | 0 / 0 / 0 |
| `optimized` | 61440 | spill/h64 | 0 | 1 / 0 / 1 |

三种profile均要求UBIO `sram=524288, ways=0, set_bits=0`。

## 可选本机证据

本机使用 `run_multi.sh` 时会生成 `launch_commands_tc<TC>.jsonl`。它只用于本机检查
“launcher准备的argv”和“进程实际argv”是否一致，不是远端输入要求。需要检查时显式传入：

```bash
python3 scripts/audit_tc_launch.py /path/to/local_logs \
  --launch-jsonl /path/to/launch_commands_tc98.jsonl \
  --tc 98 --formal
```

成功输出一行 `PASS`；失败逐项列出远端实际值与本机合同期望值。该工具不判断远端
timeout、supervisor或进程回收策略，也不代替TC98/TC134协议进度分析器。

只有手机文字信道时增加：

```bash
python3 scripts/audit_tc_launch.py /path/to/remote_logs \
  --tc 98 --formal --compact-phone
```

成功严格一行；失败最多四行，只需把这些行手打到对话中。
