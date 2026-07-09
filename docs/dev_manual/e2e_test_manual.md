# E2E 测试使用手册

> 最后更新: 2026-07-09

## 1. 拓扑类型与 Workload 对应关系

| 拓扑 | 命令 | 节点数 | sockets/node | NMOD | 适用 TC |
|------|------|--------|-------------|------|---------|
| 3n1s | `--1s` | 3 | 1 | 3 | TC1-31, 36-38, 40-54, 63-64 |
| 3n2s | `--2s` | 3 | 2 | 6 | TC32-35, 39 (dual-socket) |
| 8n1s | `--8n1s` | 8 | 1 | 8 | TC90 |
| 8n2s | `--8n2s` | 8 | 2 | 16 | TC91+ (待实现) |

**规则**: 不要将 dual-socket TC(32-35,39) 跑在 1s 拓扑上,反之亦然。
同样的,8-node TC 只在对应的 8n 拓扑上运行。

## 2. 运行单个测试

```bash
# Docker 环境下:
cd /workspace

# 3 节点 1 socket
TIMEOUT_SEC=600 bash tests/e2e/run_multi.sh --1s 3    # TC3 pingpong

# 3 节点 2 sockets  
TIMEOUT_SEC=600 bash tests/e2e/run_multi.sh --2s 32   # TC32 cross-socket

# 8 节点 1 socket
TIMEOUT_SEC=600 bash tests/e2e/run_multi.sh --8n1s 90  # TC90 all-to-all
```

## 3. 运行多个测试

```bash
TIMEOUT_SEC=600 bash tests/e2e/run_multi.sh --1s 1 2 3 4 5
```

## 4. 参数外部化

### 4.1 Gem5 延迟/容量配置 (`chi_params.json`)

文件位置: `gem5/configs/ruby/chi_params.json`

修改 JSON 中的值后重启 gem5 即可生效,不需要重编。

```json
{
  "cache": {"l1d_data": 2, "l2_data": 6, "l3_data": 10, ...},
  "noc": {"router_latency": 1, ...},
  "snf": {"to_memory_controller_latency": 20, ...},
  "controller": {"hnf_tbes": 4096, ...}
}
```

### 4.2 ZMQ 延迟 (`framework/Port.hh` 或环境变量)

```bash
export EP_LINK_LATENCY_PS=100000    # 100ns
export EP_SYNC_INTERVAL_PS=100000   # 100ns
```

改 `framework/Port.hh:25-26` 的 `kDefault*` 常量永久生效。

### 4.3 C++ 重试周期 (环境变量)

```bash
export EP_RETRY_CYCLES=1600000           # EPSNFController retry (default 800µs)
export EPRN_COMPACK_RETRY_CYCLES=100000  # EPRNF CompAck retry
export EPRN_WAKEUP_RETRY_CYCLES=1000000  # EPRNF wakeup retry
export UB_WAIT_CAP=2000000               # UBAdapter spin-wait cap
```

### 4.4 UBIO 参数 (命令行)

```bash
./build/bin/ubio --node=0 --socket=0 --num-nodes=8 --num-sockets=1
```

UBCCController 的 `T_ubio_dram` (backstore 延迟, P1 Q3 设计) 和 `_recallTimeout` 待 CLI 化。

### 4.5 拓扑延迟 (gen_topo CLI)

```bash
python3 scripts/gen_topo.py --nodes 8 --sockets 1 \
  --cross-node-latency 375000 --cross-socket-latency 185000 --out topo.json
```

### 4.6 延迟参数求解

```bash
# 输入 X(ZMQ 延迟 ns),解出所有其他参数
python3 scripts/solve_latency_params.py --x-ns 20
```

## 5. 构建

### 5.1 Gem5

```bash
# Docker 内:
cd /workspace/gem5 && scons -j$(nproc) build/ARM/gem5.opt
```

### 5.2 Framework/Ubio/Networksim

```bash
# 主机 (非 Docker):
bash scripts/build_framework.sh
bash scripts/build_ubio.sh
bash scripts/build_networksim.sh
```

## 6. 验证与日志

日志输出到 `logs/<date>_<time>_<topo>/`:

```
gem5_tc<N>_node<M>/    # gem5 per-node stderr/stdout
ubio_n<N>_s<S>/        # ubio per-(node,socket) stderr
nsim_tc<N>.log         # networksim log
verify_tc<N>.log       # 验证结果
```

## 7. 延迟 trace 分析

```bash
# 收集 TRACE-PERF
grep -h 'TRACE-PERF' logs/*/gem5_tc*_node*/stderr.log logs/*/ubio_n*/stderr.log logs/*/nsim_tc*.log \
  | sort -t'|' -k1 -n | python3 scripts/trace2chain.py > chains.json

# 可视化
python3 scripts/chain2html.py --target-ns 415 chains.json > tc.html
```

## 8. 已修复的已知问题 (P1)

| 问题 | 修复 | 状态 |
|------|------|------|
| EPSNFController socket 路由 bug | `EPSNFController.cc` `_socketId` 参数 | ✅ |
| EPBackend 响应回调仅 socket-0 | `EPBackend.cc` 泛化 | ✅ |
| UBCC 响应 dstSocket 未设 | `ubio_main.cc` 7 处 | ✅ |
| gem5 SE MemPool 3 节点限制 | `se_workload.cc` → 16 | ✅ |
