# BloomReady 启动参数

不需要设置环境变量，完整 runner 示例（在项目标准 Docker 环境内运行）：

```bash
bash tests/e2e/run_multi.sh --wait-bloom-ready=1 --bloom-ready-timeout-ms=120000 --2n1s 142
```

IDE 分别启动组件时，把以下参数加入**每个 UBIO 进程**的 argv：

```text
--wait-bloom-ready=1 --bloom-ready-timeout-ms=120000
```

两层都支持 `--参数 值` 与 `--参数=值`。优先级为 CLI > 原环境变量
`EP_WAIT_BLOOM_READY` / `EP_BLOOM_READY_TIMEOUT_MS` > 默认值 `0` / `120000`。
开关只接受 0、1；超时为正整数毫秒，最大为 uint64 上限
18446744073709551615。runner 保持原有不接受前导零的校验规则。

runner 会把有效值显式传给所有 UBIO，原有运行配置记录与适用性检查也使用
这些值；UBIO 的 `[PROCESS-MANIFEST]` 同时记录原始 argv 和有效字段
`wait_bloom_ready`、`bloom_ready_timeout_ms`。
启动门控沿用现有适用性限制（TC142–147、多节点、完整参与掩码）。

`tests/e2e/test_e2e.py` 是 gem5 配置，不启动独立 UBIO，也不能用它的 argparse
控制兄弟进程，因此没有添加无效的同名 Python 参数。分组件启动时必须设置
UBIO argv；通常直接使用上述 runner 即可完成端到端转发。

CLI 回归测试（先在 Docker 中构建 framework 和 UBIO）：

```bash
python3 tests/scripts/test_bloomready_cli.py -v
```

该测试覆盖真实 UBIO 启动 manifest、校验与优先级，以及 runner 的解析/转发
代码块；不代替完整多进程 BloomReady 仿真。
