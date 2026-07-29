# HA Workload 交付件中文使用说明

## 1. 交付目标

本交付件用于在 CC reference 和客户 HA target 上运行相同的 2N1S workload。两端必须保持相同操作序列、地址 offset、数据值、seed、barrier 位置、计时边界和 JSONL schema，从而比较 guest-visible latency、分布和 useful throughput。

交付的是 portable workload 源码和固定场景契约，不要求客户提供 FPGA SDK、地址布局、affinity、cache-control 或私有运行时实现。

## 2. 交付目录

| 文件 | 用途 |
|---|---|
| `tests/e2e/workloads/e2e_ha_2n1s_core.c` | HA01-HA10 共用 portable workload core |
| `tests/e2e/workloads/perf_latency.h` | CC 侧固定缓冲 CNTVCT 分布辅助 |
| `tests/ha_2n1s/README.md` | 场景索引和英文 portable contract |
| `scripts/summarize_2n1s_guest.py` | 汇总两节点 JSONL guest sample |
| `scripts/analyze_2n1s_cc.py` | CC-only Outer protocol diagnostic |
| `docs/measure/tc217_ha10_2n1s_perf_20260728.md` | HA10 CC reference 结果 |

建议向目标方交付源码、本文档和固定 JSONL schema；不要交付本地 logs、`.elf`、HTML 或 `tmp_libs/`。

## 3. 固定运行契约

以下内容是跨平台可比性的组成部分，不得修改：

- 拓扑：2 nodes x 1 socket，两个 primary participant。
- seed：`131`。
- 每个 scenario 的操作顺序、offset、读写值和 iteration 数。
- `sync_wait(0x3)` 对应的 barrier 位置和参与者集合。
- timed region 的开始/结束位置和 `operations` 计数。
- JSONL 的 `kind`、`scenario`、`phase`、`node`、`iteration`、`latency_ticks`、`operations`、`measurement_source` 和 validation 语义。
- timed region 内不得执行 console 输出、文件写入、JSON serialization 或进度 syscall。
- 每个 node 只允许一个 primary workload thread 生成正式 sample；辅助线程不能重复执行场景。

目标侧可以修改：

- HA-visible shared range 的基地址和映射方式。
- node/thread identity 获取方式。
- target timer 读取实现和频率获取方式。
- barrier、console/UART/file transport 的 SDK 调用。
- 编译器、链接脚本、启动方式和必要的 target affinity 设置。

## 4. 场景清单

| TC | Scenario | 功能 |
|---:|---|---|
| 210 | HA01 | node0 local reuse |
| 211 | HA02 | node1 remote read node0 data |
| 212 | HA03 | ownership handoff |
| 213 | HA04 | shared read then writer |
| 214 | HA07 | 16-record producer-consumer stream |
| 215 | HA05 | capacity shared-victim revisit |
| 216 | HA06 | dirty-owner capacity lifecycle |
| 217 | HA10 | read-mostly skewed catalog performance |
| 218 | HA08 | barrier and sequence-lock handoff contention |
| 219 | HA09 | local compute and remote pressure |

HA10 是推荐的性能验收入口：512-entry 容量模型、16 条 catalog line、640 条 streamed pressure line、8 个 batch，每 batch 14 次 skewed read 和 2 次 update，另有独立 128-op useful throughput sample。

## 5. 目标侧适配接口

目标适配层只需替换四类 primitive：

| CC reference primitive | HA target 实现 |
|---|---|
| `dsm_load(home, offset)` | 从目标侧 HA-visible shared range 读取 64-bit value |
| `dsm_store(home, offset, value)` | 向对应 shared range 写入 64-bit value，并满足场景要求的完成语义 |
| `sync_wait(0x3)` | 两 participant barrier；返回前保证本轮双方都到达 |
| `read_timer()` / JSONL output | 单调硬件计数器；timed region 结束后通过 UART、文件或结果通道输出 |

适配伪代码：

```c
uint64_t dsm_load(unsigned home, uint64_t offset)
{
    volatile uint64_t *p = map_ha_address(home, offset);
    return *p;
}

void dsm_store(unsigned home, uint64_t offset, uint64_t value)
{
    volatile uint64_t *p = map_ha_address(home, offset);
    *p = value;
    target_store_completion_barrier();
}

void sync_wait(unsigned participant_mask)
{
    target_barrier_wait(participant_mask);
}
```

若 target timer 不是 CNTVCT，保留原始 tick 输出，并在 manifest 增加实际 `timer_frequency_hz`。不要在 workload 内用浮点换算纳秒；后处理统一换算。

## 6. CC Reference 运行

所有正式运行使用隔离容器和独立 run ID：

```bash
docker run --rm --network none \
  -v "$PWD:/workspace" -w /workspace ubcc-dev:ubuntu20.04 \
  env E2E_RUN_ID=ha10_naive_001 \
      LOG_BASE=logs/ha10_naive_001 \
      EP_PERF_PROFILE=naive \
      UBCC_POLICY=naive \
  bash tests/e2e/run_multi.sh --2n1s 217
```

```bash
docker run --rm --network none \
  -v "$PWD:/workspace" -w /workspace ubcc-dev:ubuntu20.04 \
  env E2E_RUN_ID=ha10_optimized_001 \
      LOG_BASE=logs/ha10_optimized_001 \
      EP_PERF_PROFILE=optimized \
      UBCC_POLICY=spill \
  bash tests/e2e/run_multi.sh --2n1s 217
```

不得通过增大 `EP_SUPERVISOR_PROGRESS_STALL_SEC` 掩盖无进展；默认 600 秒 stall 门限保持不变。

## 7. JSONL 输出规范

每个 node 至少输出一条 manifest 和一条 validation：

```json
{"kind":"manifest","scenario":"HA10","mode":"ha","node":0,"seed":131,"nodes":2,"sockets_per_node":1,"threads_per_node":1,"working_set_bytes":41984,"iterations":8,"measurement_source":"target_counter","guest_visible":true,"timer_frequency_hz":100000000}
```

普通 phase sample：

```json
{"kind":"sample","scenario":"HA10","phase":"catalog_batch","node":1,"iteration":0,"latency_ticks":421,"operations":16,"measurement_source":"target_counter"}
```

最终 validation：

```json
{"kind":"validation","scenario":"HA10","mode":"ha","node":1,"seed":131,"errors":0}
```

硬性通过条件：

- node0 和 node1 都有 manifest。
- 两节点 `scenario`、seed、working set 和 iteration contract 一致。
- 两节点都有 validation，且 `errors=0`。
- sample 的 phase、iteration 和 operations 数量满足场景定义。
- 所有正确性 readback 均匹配；不能以 timeout、zero-data fallback 或缺失 sample 判为通过。

## 8. HA10 样本要求

HA10 必须输出：

- 8 条 `phase=catalog_batch` sample，iteration 为 0 到 7，每条 `operations=16`。
- 1 条 `phase=catalog_useful_throughput` sample，`operations=128`。
- node0 和 node1 各一条 `errors=0` validation。
- 两个 update key 的最终值校验成功。

timed batches 只把 elapsed tick 写入固定数组；全部 8 个 batch 结束后再生成 JSONL。

## 9. 汇总与比较

CC reference 可直接汇总：

```bash
python3 scripts/summarize_2n1s_guest.py \
  --input build/runs/<run-id>/tc217/m5out/node0/simout_n0 \
          build/runs/<run-id>/tc217/m5out/node1/simout_n1 \
  --output results/ha10_guest.jsonl
```

目标侧若使用 `measurement_source=target_counter`，可先将同 schema JSONL 汇入一个文件，再按以下公式计算：

```text
ticks_per_op = latency_ticks / operations
ns_per_op = ticks_per_op * 1e9 / timer_frequency_hz
useful_ops_per_second = operations * timer_frequency_hz / latency_ticks
```

分布至少报告 samples、min、mean、P50、P95、P99 和 max。多次独立运行时，还应报告 run-level mean、标准差和变异系数 CV。

CC 与 HA 对比只能使用 guest/target-visible sample。`EP-PERF kind=outer` 标记为 `guest_visible=false`、`cross_platform_comparable=false`，只能用于 CC 内部协议诊断。

## 10. 交付验收清单

- portable core 未改变 seed、地址 offset、值、barrier 和 operation count。
- target adapter 只替换 load/store、barrier、identity、timer/output primitive。
- timed region 内无输出或动态分配。
- 两节点 validation 均为零错误。
- HA10 的 8+1 个 sample 完整。
- 原始 JSONL、构建版本、target timer frequency 和运行命令一并归档。
- 每个 profile/target 至少使用 3 个独立 run ID；不复用 IPC endpoint 或结果目录。
- 报告明确区分 latency、throughput、scenario total 和 protocol diagnostic。

## 11. 常见错误

| 错误 | 影响 | 修正 |
|---|---|---|
| 在 timed loop 中打印 JSON | syscall 污染延迟 | 固定数组缓存，循环结束后输出 |
| 改动 barrier 位置 | 两端执行窗口不可比 | 恢复 portable core 定义的位置 |
| 用完整 scenario 除操作数 | 混入 setup/validation | 使用对应 phase sample |
| 缺失 node validation 仍汇总 | 可能把半程运行当性能结果 | validation 不全立即失败 |
| 用 Outer timing 对比 HA | 测量边界不同 | 仅比较 guest/target counter |
| timeout 后返回零数据 | 隐藏协议或平台错误 | 返回失败并保留错误证据 |
