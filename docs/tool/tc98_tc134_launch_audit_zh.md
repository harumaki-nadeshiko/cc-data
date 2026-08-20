# TC98 / TC134 启动参数与进程生效值审计

`run_multi.sh` 会在每个进程启动前把**最终命令**写入：

```text
$LOG_BASE/launch_commands_tc<TC>.jsonl
```

每条记录保存 `component/node/socket/tc/topology/argv/env`。`argv` 使用 Python
`shlex.split` 从最终 shell 命令解析，保留参数顺序与重复 flag。UBIO、gem5 配置脚本和
networksim 还会各自在自己的 stdout 输出一行 `[PROCESS-MANIFEST]`，记录实际 argv
及最终生效参数。

## TC98：8 节点 × 2 socket，正式参数

```bash
docker run --rm --network none \
  -v "$PWD:/workspace" -w /workspace \
  ubcc-dev:ubuntu20.04 \
  env LOG_BASE=/workspace/logs/tc98_formal \
      LD_LIBRARY_PATH=/workspace/thirdparty/zeromq/lib \
  bash tests/e2e/run_multi.sh --8n2s --formal 98

docker run --rm --network none \
  -v "$PWD:/workspace" -w /workspace \
  ubcc-dev:ubuntu20.04 \
  python3 scripts/audit_tc_launch.py /workspace/logs/tc98_formal \
    --tc 98 --formal
```

审计项包括：16 个唯一 UBIO plane、8 个唯一 gem5 node、1 个 networksim；无
fault override；UBIO `ways=1`、spill、H64；O3、sequencer=16、link/sync=2500ps、
端口 HWM=8192、networksim pending=65536，以及两端 metadata bytes 一致。

## TC134：三种策略/profile

下面三组命令分别执行；`--profile` 会同时固定 policy 与 gem5 优化设置。

```bash
for profile in naive spill-noopt optimized; do
  docker run --rm --network none \
    -v "$PWD:/workspace" -w /workspace \
    ubcc-dev:ubuntu20.04 \
    env LOG_BASE="/workspace/logs/tc134_${profile}" \
        LD_LIBRARY_PATH=/workspace/thirdparty/zeromq/lib \
    bash tests/e2e/run_multi.sh --8n2s --profile "$profile" 134

  docker run --rm --network none \
    -v "$PWD:/workspace" -w /workspace \
    ubcc-dev:ubuntu20.04 \
    python3 scripts/audit_tc_launch.py "/workspace/logs/tc134_${profile}" \
      --tc 134 --profile "$profile"
done
```

期望值：

| profile | UBIO bloom | policy | UBIO batch | gem5 silent/direct/batch |
|---|---:|---|---:|---|
| `naive` | 0 | naive | 0 | 0 / 0 / 0 |
| `spill-noopt` | 61440 | spill | 0 | 0 / 0 / 0 |
| `optimized` | 61440 | spill | 0 | 1 / 0 / 1 |

三种 profile 的 UBIO 均要求 `sram=524288, ways=0, set_bits=0`。

## 远端日志平铺

工具递归读取日志，也支持把远端文件全部平铺到同一目录；只要文件内容中仍有
`[PROCESS-MANIFEST]` 行即可。若 launch JSONL 位于别处，显式指定：

```bash
python3 scripts/audit_tc_launch.py /path/to/flat_logs \
  --launch-jsonl /path/to/launch_commands_tc134.jsonl \
  --tc 134 --profile optimized
```

成功只输出一行紧凑 `PASS`；失败输出 `FAIL` 和逐项 mismatch。
