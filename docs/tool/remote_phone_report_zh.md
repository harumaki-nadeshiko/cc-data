# 手机友好的远端环境报告

`scripts/remote_phone_report.py`用于远端机器无法方便回传文件、只能通过手机或聊天
手工转录结果的场景。它直接使用远端原生 Python 运行，不需要 Docker，也不会构建或
启动项目程序。

## 使用方法

建议给每个二进制和产物指定短名称，参数可重复：

```bash
python3 scripts/remote_phone_report.py \
  --libzmq '/opt/runtime libs/libzmq.so.5' \
  --binary 'ubio=/opt/ubcc bin/ubio' \
  --binary 'networksim=/opt/ubcc bin/networksim' \
  --artifact 'topo=/opt/ubcc configs/topo_8n2s.json' \
  --baseline configs/runtime_fingerprint_local.json
```

路径含空格时必须像上例一样整体加引号。也可只传路径，例如
`--artifact '/tmp/a file'`，此时名称自动取 basename；但 `NAME=PATH`更适合手工转录
并能和本地 baseline 中 basename 相同的条目配对。

默认成功输出严格为三行 ASCII：

```text
PHONEENV arch=aarch64[base=x86_64] kernel=6.1.0 libc=glibc-2.36 python=3.11.2 compiler=gcc-12.2.0 libzmq=4.3.5[=base]
PHONEHASH ubio=0123456789ab[base=fedcba987654] topo=MISSING
PHONELDD ubio=1:libexample.so networksim=0
```

- `[=base]`表示与 baseline 一致，`[base=...]`表示不一致并给出本地值；baseline 中
  没有可配对字段时不加后缀。
- `PHONEHASH`只显示 SHA-256 的前 12 位；文件不存在显示 `MISSING`。
- `PHONELDD`的 `0`表示无缺失依赖，`数量:名称列表`表示 `ldd`报告的缺失依赖；还
  可能显示 `BINARY-MISSING`、`UNAVAILABLE`或`NOT-DYNAMIC`。
- 不传 `--baseline`即可只报告远端值。baseline 应由
  `collect_runtime_fingerprint.py`生成，仓库参考文件是
  `configs/runtime_fingerprint_local.json`。

需要机器读取的完整详情时可加 `--json`。JSON压缩在单行输出；默认不会生成 JSON
文件，也不会在远端留下报告文件。
