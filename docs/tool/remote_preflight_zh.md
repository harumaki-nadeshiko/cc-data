# 远端上机前置检查

正式测试前运行：

```bash
bash scripts/run_remote_preflight.sh
```

它依次完成：

1. 收集远端架构、内核、libc、编译器、libzmq、Git/submodule、三类二进制 SHA-256 与 `ldd`；
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

若使用远端 backend archive：

```bash
FRAMEWORK_BACKEND_LIB=/workspace/path/libframework_remote.a \
FRAMEWORK_INCLUDE_DIR=/workspace/path/include \
FRAMEWORK_LINK_LIBZMQ=1 \
bash scripts/run_remote_preflight.sh
```

已有 TC98 日志时同时审计启动参数：

```bash
bash scripts/run_remote_preflight.sh --tc 98 --log-root /path/to/LOG_BASE
```

TC134：

```bash
bash scripts/run_remote_preflight.sh \
  --tc 134 --profile optimized --log-root /path/to/LOG_BASE
```

正式启动命令：

```bash
env E2E_RUN_ID=tc98_formal LOG_BASE=/path/to/logs \
  EP_TRACE_PERF=off TIMEOUT_SEC=21600 \
  bash tests/e2e/run_multi.sh --8n2s --formal 98

env E2E_RUN_ID=tc134_opt LOG_BASE=/path/to/logs \
  EP_TRACE_PERF=off TIMEOUT_SEC=10800 \
  bash tests/e2e/run_multi.sh --8n2s --formal --profile optimized 134
```

注意参数顺序固定为：拓扑、`--formal`/`--profile`、TC 编号。
