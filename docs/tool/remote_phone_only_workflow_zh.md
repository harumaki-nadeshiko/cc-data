# 远端仅手机文字信道操作

远端不需要Docker，不运行本机`run_multi.sh`，也不假设存在supervisor。正式模拟仍由
远端原有启动器负责。所有需要回传的输出压缩为少量ASCII行。

## 环境三行

```bash
python3 scripts/remote_phone_report.py \
  --libzmq "$LIBZMQ_FILE" \
  --baseline configs/runtime_fingerprint_local.json \
  --binary ubio="$UBIO_BIN" \
  --binary nsim="$NSIM_BIN" \
  --binary gem5="$GEM5_BIN" \
  --artifact backend="$BACKEND_LIB" \
  --artifact teste2e="$REMOTE_TEST_E2E" \
  --artifact topo="$REMOTE_TOPO"
```

只输出`PHONEENV/PHONEHASH/PHONELDD`三行，直接手打回传。

## Framework裸机压力一行

```bash
CXX=g++ \
FRAMEWORK_BACKEND_LIB="$BACKEND_LIB" \
FRAMEWORK_INCLUDE_DIR="$FRAMEWORK_INCLUDE_DIR" \
LIBZMQ_INCLUDE_DIR="$LIBZMQ_INCLUDE_DIR" \
LIBZMQ_LIB_DIR="$LIBZMQ_DIR" \
FRAMEWORK_LINK_LIBZMQ=1 \
  bash scripts/run_framework_stress_bare.sh
```

成功一行`FWSTRESS PASS ...`；失败最多三行。若backend还有额外依赖，通过
`FRAMEWORK_BACKEND_LDFLAGS`和`FRAMEWORK_RUNTIME_LIBRARY_PATH`提供。

## 参数审计最多四行

远端日志在远端仓库可直接读取时：

```bash
python3 scripts/audit_tc_launch.py "$REMOTE_LOG_DIR" \
  --tc 98 --formal --compact-phone
```

TC134增加`--profile naive|spill-noopt|optimized`。成功一行，失败最多四行。

## 协议故障最多四行

```bash
python3 scripts/extract_ubcc_key_state.py "$REMOTE_LOG_DIR" --compact
```

只需手打上述命令输出，不要求传送文件。
