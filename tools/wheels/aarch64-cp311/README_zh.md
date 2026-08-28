# Matplotlib aarch64 / Python 3.11 离线 Wheel

该目录提供统一 Metric 报告 PNG 柱状图所需的完整离线依赖闭包。

目标环境：

```text
OS: Linux aarch64，glibc >= 2.17
Python: CPython 3.11
ABI: cp311
Platform: manylinux2014_aarch64 / manylinux_2_17_aarch64
Matplotlib: 3.10.3
```

以下命令均在仓库根目录执行。安装前校验：

```bash
cd tools/wheels/aarch64-cp311 && sha256sum -c SHA256SUMS && cd ../../..
```

离线安装：

```bash
python3.11 -m pip install \
  --no-index \
  --find-links=tools/wheels/aarch64-cp311 \
  -r tools/wheels/aarch64-cp311/requirements.txt
```

仅安装 Matplotlib 及由 pip 解析的依赖：

```bash
python3.11 -m pip install \
  --no-index \
  --find-links=tools/wheels/aarch64-cp311 \
  matplotlib==3.10.3
```

验证：

```bash
python3.11 -c 'import matplotlib, numpy, PIL; print(matplotlib.__version__, numpy.__version__, PIL.__version__)'
```

Extractor 无论是否安装 Matplotlib 都会生成零依赖的：

```text
metric_summary_bar_chart.svg
```

安装成功后还会生成：

```text
metric_summary_bar_chart.png
```

Wheel 内已携带各上游项目的 METADATA/LICENSE 文件。版本和文件哈希固定在本目录的
`requirements.txt`与`SHA256SUMS`中。
