# 远端裸机 Framework 压力测试

`scripts/run_framework_stress_bare.sh` 用于用户明确指定的远端原生环境。它直接调用
远端 C++ 编译器并启动 `gem5`、`ubio` 两个原生进程，**不会调用 Docker**。仓库内的
开发验证仍须遵守项目的 Docker-only 规则；不要把本脚本用于本地开发构建替代品。

## 最小用法

后端库必须显式提供，可以是绝对路径或相对仓库根目录的路径：

```bash
FRAMEWORK_BACKEND_LIB=/opt/ubcc/lib/libframework_remote.so \
FRAMEWORK_INCLUDE_DIR=/opt/ubcc/include \
  bash scripts/run_framework_stress_bare.sh
```

默认每个方向发送 `100000` 条、每条 `256` 字节。成功时紧凑模式只输出一行：

```text
FWSTRESS PASS n=100000 bytes=256 gem5_ms=1234 ubio_ms=1230
```

失败最多输出三行，首行为 `FWSTRESS FAIL stage=compile|run ...`。`--verbose` 会额外
显示完整编译/启动命令和日志，适合排障，不适合手机端紧凑采集。

## 常用配置

```bash
CXX=g++ \
FRAMEWORK_BACKEND_LIB=vendor/lib/libframework_remote.a \
FRAMEWORK_INCLUDE_DIR=vendor/include \
LIBZMQ_INCLUDE_DIR=vendor/zeromq/include \
LIBZMQ_LIB_DIR=vendor/zeromq/lib \
FRAMEWORK_LINK_LIBZMQ=1 \
FRAMEWORK_BACKEND_CPPFLAGS='-DREMOTE_BACKEND=1' \
FRAMEWORK_BACKEND_LDFLAGS='-ldl' \
FRAMEWORK_RUNTIME_LIBRARY_PATH='/opt/vendor/lib:/opt/extra/lib' \
  bash scripts/run_framework_stress_bare.sh \
    --messages 250000 --payload-bytes 1024 --timeout-ms 180000
```

变量说明：

- `CXX`：编译器命令，默认 `g++`。
- `FRAMEWORK_BACKEND_LIB`：必填；静态或共享 backend 库。
- `FRAMEWORK_INCLUDE_DIR`：应包含 `framework/iface/Port.hh`。未设置时依次尝试
  `build/framework/include` 和仓库根目录。
- `LIBZMQ_INCLUDE_DIR`、`LIBZMQ_LIB_DIR`：非系统 ZeroMQ 的头文件和库目录。
- `FRAMEWORK_LINK_LIBZMQ`：`auto`（默认）、`1` 或 `0`。静态 backend 自动检测到
  ZeroMQ 未定义符号时会链接 `-lzmq`；特殊库请显式设为 `1`。
- `FRAMEWORK_BACKEND_CPPFLAGS`、`FRAMEWORK_BACKEND_LDFLAGS`：backend 附加参数。
- `FRAMEWORK_RUNTIME_LIBRARY_PATH`：运行时共享库搜索路径，追加到
  `LD_LIBRARY_PATH`；脚本也会自动加入 backend 和 ZeroMQ 库所在目录。

脚本在 `mktemp` 目录中编译并为每次运行设置独立的 `UBCC_IPC_DIR`。两个角色都
收到内部 `--timeout-ms`，外层优先使用系统 `timeout`，并始终设置清理 watchdog；
退出、信号和超时时会终止并回收两个角色，避免遗留进程。
