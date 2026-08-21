# 远端上机前置检查

> `scripts/run_remote_preflight.sh` 是有现成 Docker 镜像环境的辅助入口，不适用于
> 当前无法使用镜像的远端。当前远端请使用裸机
> `scripts/run_framework_stress_bare.sh` 和 `scripts/remote_phone_report.py`。

该脚本只执行环境比较、framework独立压力测试和可选的**既有日志**审计，不启动
TC98/TC134。远端正式测试仍由远端既有启动和进程回收机制组织；远端没有本仓库的
supervisor，也不要求执行`run_multi.sh`。

正式测试前可运行：

```bash
bash scripts/run_remote_preflight.sh
```

它依次完成：

1. 收集远端架构、内核、libc、编译器、libzmq、Git/submodule、三类二进制 SHA-256 与 `ldd`；
   同时单独记录实际 framework backend archive 的 SHA-256；
2. 与 `configs/runtime_fingerprint_local.json` 比较；
3. 使用待测 framework backend 执行 100,000 消息/方向的公共接口压力测试；
4. 即使环境比较失败也继续完成压力测试和日志审计，最后统一输出
   `PREFLIGHT PASS/FAIL`，避免一次上机只得到第一项差异。

远端 libzmq 不在标准搜索路径时：

```bash
LIBZMQ_PATH=/path/libzmq.so.5 \
LIBZMQ_HOST_LIB_DIR=/path/to/lib \
bash scripts/run_remote_preflight.sh
```

`LIBZMQ_HOST_LIB_DIR`是必填项，目录会只读挂载进测试容器；`LIBZMQ_PATH`
只在实际文件名不是`libzmq.so`时需要设置。

若使用远端 backend archive：

```bash
FRAMEWORK_BACKEND_LIB=/workspace/path/libframework_remote.a \
FRAMEWORK_INCLUDE_DIR=/workspace/path/include \
FRAMEWORK_LINK_LIBZMQ=1 \
bash scripts/run_remote_preflight.sh
```

远端运行结束或卡死、日志已收集后，可同时审计TC98进程参数：

```bash
bash scripts/run_remote_preflight.sh --tc 98 --log-root /path/to/LOG_BASE
```

TC134已有日志：

```bash
bash scripts/run_remote_preflight.sh \
  --tc 134 --profile optimized --log-root /path/to/LOG_BASE
```

这里的`--tc/--profile`只告诉审计器选择哪份本机合同，不会启动任何模拟器，也不
假设远端存在supervisor、`LOG_BASE`或本机runner目录结构。
