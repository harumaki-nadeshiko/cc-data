# HA Workload Bare-metal Arm 使用与移植说明

> 交付版：2026-07-31

## 1. 交付目标

本交付件用于在 CC reference 和客户 HA target 上运行相同的 2N1S workload。两端必须保持相同操作序列、地址 offset、数据值、seed、barrier 位置、计时边界和 JSONL schema，从而比较 guest-visible latency、分布和 useful throughput。

交付的是 portable workload 源码和固定场景契约，不要求客户提供 FPGA SDK、地址布局、affinity、cache-control 或私有运行时实现。

## 2. 交付目录

| 文件 | 用途 |
|---|---|
| `tests/e2e/workloads/e2e_ha_2n1s_core.c` | HA01-HA12 共用 portable workload core |
| `tests/e2e/workloads/e2e_tc142_*.c` - `e2e_tc147_*.c` | topology-portable 业务 workload 源码 |
| `tests/e2e/workloads/portable_large_workload.h` | plane、dynamic barrier、service/end-to-end 公共契约 |
| `tests/e2e/workloads/perf_latency.h` | CC 侧固定缓冲 CNTVCT 分布辅助 |
| `tests/ha_2n1s/README.md` | 场景索引和英文 portable contract |
| `docs/delivery/ha_workload_scenario_catalog_20260731_zh.md` | 每个交付场景的角色、操作、计时、Global/CHI 和验收规范 |
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
| 220 | HA11 | exact-150 clean/shared admission and revisit |
| 221 | HA12 | exact-150 dirty admission, revisit and handoff |
| 142-147 | portable business | OLTP、B-tree、WAL、FaaS、graph、feature store |

HA10 是推荐的性能验收入口：512-entry 容量模型、16 条 catalog line、640 条 streamed pressure line、8 个 batch，每 batch 14 次 skewed read 和 2 次 update，另有独立 128-op useful throughput sample。

HA11/HA12 是精确 150% capacity 入口：64 hot + 704 pressure = 768 unique lines。
TC142-TC147 是推荐的业务 workload 扩展。TC123/130/132/135/138/139 的原源码
仍固定为 3N1S；其 2N1S 角色合并、计时和验收定义见场景规范目录，当前不得标为
directly runnable implementation。

## 5. 目标侧适配接口

目标适配层只需替换四类 primitive：

| CC reference primitive | HA target 实现 |
|---|---|
| `dsm_load(home, offset)` | 从目标侧 HA-visible shared range 读取 32-bit value |
| `dsm_store(home, offset, value)` | 向对应 shared range写入 32-bit value；完成边界见下文 |
| `sync_wait(0x3)` | 两 participant barrier；返回前保证本轮双方都到达 |
| `read_timer()` / JSONL output | 单调硬件计数器；timed region 结束后通过 UART、文件或结果通道输出 |

适配伪代码：

```c
uint32_t dsm_load(unsigned home, uint32_t offset)
{
    volatile uint32_t *p = map_ha_address(home, offset);
    return *p;
}

void dsm_store(unsigned home, uint32_t offset, uint32_t value)
{
    volatile uint32_t *p = map_ha_address(home, offset);
    *p = value;
}

void perf_store_complete(unsigned home, uint32_t offset, uint32_t value)
{
    dsm_store(home, offset, value);
    __asm__ volatile("dsb sy" ::: "memory");
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

## 12. 当前源码状态与两种移植路线

当前 `e2e_ha_2n1s_core.c` 是“场景逻辑可移植、CC reference glue 尚未抽离”的源码，
直接包含：

```c
#include "dsm_access.h"
#include "e2e_common.h"
#include "perf_latency.h"
```

这些 header 包含 gem5 SE 地址映射、Linux/gem5 syscall、CNTVCT marker 和 CC harness
输出逻辑。仓库当前没有可直接交给 target SDK 的 `ha_target_adapter.h`。因此目标方
不能原样将该文件用 `aarch64-none-elf-gcc -nostdlib` 编译后运行。

### 路线 A：最小侵入替换

保持 `e2e_ha_2n1s_core.c` 的 scenario 分支不变，在 target 工程中提供同名兼容头：

- `dsm_access.h`：实现 target 的 32-bit shared load/store。
- `e2e_common.h`：实现 node identity、barrier、timer、validation/output；删除 syscall。
- `perf_latency.h`：实现 serialized counter、`dsb sy` store completion 和固定数组分布。

优点是场景源码 diff 最小；缺点是兼容头容易意外带入不需要的 CC API。

### 路线 B：推荐的正式交付

将场景逻辑只依赖以下 `ha_platform.h`，CC 和 target 各自实现 adapter：

```c
#ifndef HA_PLATFORM_H
#define HA_PLATFORM_H
#include <stdint.h>

uint32_t ha_node_id(void);
uint32_t ha_local_thread_id(void);
uint32_t ha_load32(uint32_t home, uint32_t offset);
void ha_store32(uint32_t home, uint32_t offset, uint32_t value);
void ha_store32_complete(uint32_t home, uint32_t offset, uint32_t value);
void ha_barrier(uint32_t participant_mask, uint32_t generation);
uint64_t ha_counter_read_serialized(void);
uint64_t ha_counter_frequency_hz(void);
void ha_result_write(const char *data, uint32_t size);
void ha_fatal(uint32_t code);
#endif
```

正式给客户时推荐路线 B。平台 adapter 可以闭源，客户只需返回结果 JSONL，不必
公开私有 HA SDK、地址 hash、home selection 或 cache-maintenance 实现。

## 13. Bare-metal Arm 平台前提

### 13.1 拓扑与启动

- 两个物理 node，各启动一个正式 participant。
- 每 node 只运行一个 workload primary；其他 core 停在 WFE 或只做平台服务。
- 两边运行相同 scenario image/逻辑，通过 MPIDR、strap、mailbox 或启动参数获得
  `node_id=0/1`。
- 两个 node 的 image build ID、scenario、seed 和地址表必须一致。

若两个 node 是两个独立 SoC/板卡，启动器必须在运行前完成共享 HA window 配置和
链路训练；不能让 node0 在 node1 尚未进入 startup barrier 时开始 seed。

### 13.2 Shared HA Range

workload 的 `home` 是逻辑 home ID，不是直接物理地址。adapter 负责：

```text
target_address = ha_home_base[home] + offset
```

当前 HA01-HA12 和 TC142-TC147 都只访问 `home=0`，但接口保留 home 参数。
只运行 HA01-HA12 时建议至少为 Home0 预留 8 MiB；交付 TC142-TC147 时应按其
最大 offset 为 Home0 预留至少 80 MiB、64-byte 对齐的 shared window。

内存属性必须让被测 HA coherence 真正参与：

- Normal memory。
- Write-back cacheable。
- 按 target HA 要求设置 Inner/Outer Shareable。
- 两 node 对同一逻辑 line 的映射必须别名到同一 HA coherence address。
- 不要映射成 Device 或 Non-cacheable，否则会绕过 cache/HA 路径，结果不可比。
- 不要在每次 load/store 前后执行 cache clean/invalidate，这会改变 workload。

控制区与数据区应分离。barrier mailbox、UART buffer、result ring 不得占用 workload
offset，也不得与 timed catalog line 共享 cacheline。

### 13.3 数据宽度与对齐

- workload value 是 32-bit。
- 所有业务 line offset 都按 64 bytes 分隔。
- adapter 的 load/store 必须是单次对齐 32-bit architectural access。
- 不得把一个 32-bit access 扩展成 software lock、memcpy 或两个总线 transaction，
  除非这就是目标 HA 的正式编程模型，并在 manifest 中披露。

## 14. 内存序与 Store 完成语义

必须区分两个 primitive：

```text
ha_store32           = 普通 workload store
ha_store32_complete  = store + DSB SY，等待架构要求的完成边界
```

CC reference 中 `perf_store_complete()` 使用 `str; dsb sy`。HA10 seed、pressure 和
两个 update key 使用该完成 primitive。不要给所有普通 store 无条件增加额外 DSB，
也不要删除已有的 DSB，否则两端操作序列不同。

barrier 前至少需要满足：本阶段要求可见的 stores 已按 workload 定义完成。若 target
barrier 不隐含 memory ordering，adapter 应在 barrier entry/exit 使用平台规定的
`DMB ISH`/`DSB`。具体指令可由平台决定，但必须说明其语义。

## 15. 两节点 Barrier 实现

`sync_wait(0x3)` 是可复用、带 generation 的两 participant barrier。要求：

- node0 和 node1 每代各到达一次。
- 不允许上一代 release 释放下一代。
- 返回前双方都已到达，且阶段前的数据可见性成立。
- barrier 失败必须 fatal，不能 timeout 后继续。

若平台没有硬件 barrier，可在独立 control region 使用原子计数/代号：

```c
void ha_barrier(uint32_t mask, uint32_t generation)
{
    dmb_ish();
    atomic_store_release(&slot[node_id].arrived, generation);
    send_event_to_peer();
    while (atomic_load_acquire(&slot[peer].arrived) != generation)
        wait_for_event();
    dmb_ish();
}
```

实际实现需处理 event 丢失、cacheline sharing 和 interconnect atomics 能力。若 HA
平台不支持跨 node 原子，可使用 doorbell/mailbox，由两侧 firmware coordinator
维护 generation。barrier 本身不在 HA10 service timer 内，但其位置不能修改。

## 16. Arm Counter

首选 `CNTVCT_EL0`/`CNTFRQ_EL0`：

```c
static inline uint64_t ha_counter_read_serialized(void)
{
    uint64_t value;
    __asm__ volatile("isb\n\tmrs %0, cntvct_el0"
                     : "=r"(value) : : "memory");
    return value;
}
```

启动 firmware/hypervisor 必须允许当前 EL 读取 counter。常见配置点包括
`CNTKCTL_EL1`、`CNTHCTL_EL2`；具体位由启动 EL 决定。若不能使用 CNTVCT，可使用
SoC global timer，但必须满足：

- 单调、不回退。
- workload 运行期间频率固定或可校准。
- 至少在每个 node 内同一时钟域。
- manifest 输出准确 `timer_frequency_hz`。
- start/stop 使用同一种 serialization 规则。

跨 node 比较不要求 counter phase 对齐，因为每个 sample 是本 node 的 elapsed
delta；请求链全局时序若需要跨 node 对齐，则必须额外做 counter synchronization，
或只比较每个角色的本地 duration。

## 17. 输出与 libc

timed region 内禁止 UART、semihosting、filesystem、dynamic allocation 和 JSON
serialization。HA10 把 8 个 batch ticks 写入固定 `uint64_t samples[8]`，结束后再输出。

bare-metal 有两种输出方式：

1. 使用小型 freestanding formatter，通过 `ha_result_write()` 写 UART/result ring。
2. 链接 newlib/picolibc 的 `printf`，但必须确认 heap、锁和 syscall stub，不在 timer 内调用。

推荐方式 1。每条 JSON 必须单行原子输出；两个 node 使用独立 UART channel、独立
buffer，或在记录中保留 node 字段后离线合并。

## 18. Build 与 Link 示例

### 18.1 Freestanding

```bash
aarch64-none-elf-gcc \
  -mcpu=<target-core> -O2 -ffreestanding -fno-builtin \
  -fno-stack-protector -fno-pic -nostdlib \
  -DHA_SCENARIO=10 \
  -Iha-workload-delivery/include \
  startup.S e2e_ha_2n1s_target.c ha_platform_target.c mini_format.c \
  -T ha_target.ld -Wl,-Map,ha10.map \
  -o ha10.elf

aarch64-none-elf-objcopy -O binary ha10.elf ha10.bin
```

### 18.2 使用目标 SDK

```bash
<target-cc> -O2 -DHA_SCENARIO=10 \
  -Iinclude \
  e2e_ha_2n1s_target.c ha_platform_sdk.c \
  <startup/runtime libraries> -o ha10.elf
```

不能只交付 CC 侧静态 ELF：它依赖 gem5 SE 虚拟地址和 syscall ABI，不能在裸机上
运行。客户必须用目标 toolchain 重新链接。

## 19. Scenario 编译映射

| TC | `HA_SCENARIO` | 建议 target binary |
|---:|---:|---|
| 210 | 1 | `ha01_local_reuse.elf` |
| 211 | 2 | `ha02_remote_read.elf` |
| 212 | 3 | `ha03_ownership_handoff.elf` |
| 213 | 4 | `ha04_shared_writer.elf` |
| 214 | 7 | `ha07_producer_consumer.elf` |
| 215 | 5 | `ha05_shared_capacity.elf` |
| 216 | 6 | `ha06_dirty_capacity.elf` |
| 217 | 10 | `ha10_catalog.elf` |
| 218 | 8 | `ha08_barrier_handoff.elf` |
| 219 | 9 | `ha09_mixed_pressure.elf` |
| 220 | 11 | `ha11_clean_exact150.elf` |
| 221 | 12 | `ha12_dirty_exact150.elf` |

可以构建 12 个独立 binary，也可以构建一个 image，通过只读 boot parameter 选择
scenario。若运行时选择，必须保证分支外的 code/data 不改变 cache footprint；正式
对比推荐独立 binary。

TC142-TC147 使用各自独立源码，不通过 `HA_SCENARIO` 选择。其 target binary 建议为
`tc142_oltp.elf`、`tc143_btree.elf`、`tc144_wal.elf`、`tc145_faas.elf`、
`tc146_graph.elf` 和 `tc147_feature.elf`。

## 20. HA10 Bare-metal 执行步骤

1. 两 node 加载同一 `ha10.elf` build。
2. 配置 Home0 shared window、control barrier region 和 result region。
3. 使能 cache、HA coherence、global counter 和 UART/result channel。
4. node0/node1 各启动一个 primary，进入 startup barrier。
5. 两侧输出 manifest。
6. node0 seed 16 catalog lines；node1 warm read并建立两个 update-key owner。
7. 执行 8 batch：node0 每批 80 pressure stores；barrier；node1 timed 14 reads + 2 updates；barrier。
8. timer 结束后输出 8 条 `catalog_batch` 和一条 128-op throughput sample。
9. node0 readback 两个 update key；两 node 输出 `errors=0` validation。
10. 收集 JSONL、binary hash、firmware/HA config、timer frequency 和运行日志。

## 21. Target Manifest 扩展字段

除固定 schema 外，bare-metal manifest 建议增加：

```json
{"target":"board-x","soc_revision":"A1","cpu":"neoverse-n2","exception_level":"EL1","cache_mode":"WB-shareable","ha_mode":"vendor-ha-v3","shared_base_home0":"0x...","binary_sha256":"...","adapter_git":"...","timer_frequency_hz":100000000}
```

这些字段用于复现，不参与性能值计算。若无法披露 HA mode 名称，可用匿名配置 ID，
但每次运行必须可唯一追溯。

## 22. Bring-up 分阶段验收

不要第一步就跑 HA10：

| 阶段 | 场景 | 通过条件 |
|---|---|---|
| 1 | HA01 local reuse | node0 本地读回正确；timer/JSON 正常 |
| 2 | HA02 remote read | node1 读到 node0 数据 |
| 3 | HA03 ownership handoff | node1 写后 node0 读到新值 |
| 4 | HA04 shared-to-writer | 双方 shared 后 writer 转换正确 |
| 5 | HA07 producer-consumer | 16 records 全部正确，barrier generation 稳定 |
| 6 | HA05/HA06 capacity | 640 pressure lines 后 victim 数据正确 |
| 7 | HA08 | 16 次 barrier/seq-lock 无死锁 |
| 8 | HA10 | 8+1 samples 完整、双 validation=0 |
| 9 | HA11/HA12 | 精确 768-line capacity record；clean/dirty phase 和 final reads 完整 |
| 10 | TC142-TC147 | 每 plane participant、service/end-to-end、32 samples 和 final reads 完整 |

任何阶段失败都先修 correctness，不应收集性能数字。

## 23. 公平比较清单

- 相同 Arm core frequency、cache enable 和编译优化级别。
- 相同两个 primary participant，不用额外 worker 帮 HA 预取。
- 相同 64-byte line spacing、offset、seed、value 和 operation order。
- 相同 barrier 位置。
- 相同 store completion primitive。
- 相同 timer 边界；timer 内无输出。
- cold/warm cache 条件相同，不在 HA 侧额外 flush 或预取。
- 每个平台至少 3 个独立 run；推荐 5 次。
- 报告 run-level mean、stdev、CV；高 CV 先排查频率和后台中断。
- CC/HA 只比较 guest/target counter；CC Outer 只做归因。

## 24. 与请求链交付件配合

业务性能之外，代表场景的请求树、时序和 normalized trace schema 见：

`docs/delivery/ha_comparison_request_chains_20260731_zh.md`

目标 HA 可以只输出抽象角色 requester/home/owner/sharer，不必公开私有模块名。若
不能提供内部 trace，至少返回 root issue/complete、data source 和 child request
计数；否则只能比较总 latency，无法解释差异来源。
