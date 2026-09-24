# Split Mode VA/PA Mapping

本文描述当前 E2E 配置中“一台 gem5 进程只运行一个全局 node”的 VA/PA
映射。本文只讨论 split mode，不讨论单个 gem5 同时创建所有 node 的 legacy
模式。

对应实现主要位于：

- `tests/e2e/test_e2e.py`
- `gem5/configs/ruby/CHI_ubcc_framework.py`
- `gem5/configs/ruby/CHI_basic_framework_config.py`
- `gem5/src/sim/process.cc`
- `gem5/src/sim/se_workload.cc`

## 1. Split Mode 进程模型

命令行使用：

```text
--num-nodes N --node-id R --num-sockets S
```

其中：

- `N` 是全局 node 数量。
- `R` 是当前 gem5 worker 负责的全局 node ID。
- `S` 是每个 node 的 socket 数量。
- 当前 worker 只创建 node `R` 的 CPU 和 Ruby controller。
- 每个 node 默认有 `DEFAULT_D * DEFAULT_L = 2 * 2 = 4` 个 CPU。

配置代码构造：

```python
BUILD_NODES = [R]
TOTAL_CPUS = DEFAULT_D * DEFAULT_L
```

当前 worker 中第 `i` 个本地 CPU 对应的全局 CPU ID 是：

```text
global_cpu_index = R * CPUS_PER_NODE + i
```

每个 CPU 创建独立 `Process`：

```python
Process(
    pid=100 + global_cpu_index,
    phys_pool_id=R,
)
```

因此同一 worker 中所有 Process 使用同一个 `phys_pool_id=R`。该 ID 选择
node `R` 的普通物理页池，不是 Process 唯一 ID。每个 Process 仍有独立页表。

## 2. 全局 PA 编码

常量：

```text
NODE_ADDR_SHIFT = 40
SEG_SIZE = 128 MiB = 0x08000000
```

node `R` 的 PA 基址：

```text
B_R = R << 40
```

不同 node 的 PA view 以 1 TiB 为间隔：

```text
node 0 base = 0x00000000000
node 1 base = 0x10000000000
node 2 base = 0x20000000000
```

对于当前 worker，CPU 发出的 PA 使用 node `R` 的本地 PA view。DSM PA 同时编码：

- requester node `R`
- home node
- home socket
- segment 内 offset

## 3. Node 内 PA 布局

设：

```text
B = R << 40
j = home_node * S + home_socket
```

则 node `R` 的 PA 布局为：

| 区域 | PA 范围 |
|---|---|
| Local private | `[B, B + SEG)` |
| Routing/metadata-private window | `[B + SEG, B + 2*SEG)` |
| DSM `(home_node, home_socket)` | `[B + (2+j)*SEG, B + (3+j)*SEG)` |
| Metadata backstore | 从 `B + (2 + N*S)*SEG` 开始 |

在多 socket 配置中，local-private、routing window 和 metadata backstore 会按
socket 切片。DSM 始终按 `(home_node, home_socket)` 分段。

### 3.1 3n1s 示例

当：

```text
N = 3
S = 1
SEG = 0x08000000
```

node `R` 的 PA offset 为：

| 区域 | 相对 `B_R` 的范围 |
|---|---|
| Local private | `[0x00000000, 0x08000000)` |
| Routing window | `[0x08000000, 0x10000000)` |
| DSM home node 0 | `[0x10000000, 0x18000000)` |
| DSM home node 1 | `[0x18000000, 0x20000000)` |
| DSM home node 2 | `[0x20000000, 0x28000000)` |
| Metadata backstore | `[0x28000000, 0x30000000)`，默认 128 MiB |

例如 node 2 worker 中，DSM home node 0 的 requester-local PA 范围是：

```text
[0x20010000000, 0x20018000000)
```

它不是 home node 0 的最终 home PA。EP/UBCC 路径会在跨 node 传输时将其转换为
home node 0 view：

```text
home_pa = buildDsmPA(home_node, home_node, offset, home_socket)
```

## 4. Process 中的三类显式映射

当前配置显式建立三类映射：

1. DSM 固定 VA window。
2. ELF `PT_LOAD` 页面。
3. 供测试直接访问的 local-private VA window。

三类映射都通过：

```python
proc.map(va, pa, size, cacheable=True)
```

最终进入 C++：

```cpp
Process::map(...)
{
    pTable->map(vaddr, paddr, size, flags);
}
```

每个 Process 有独立 `EmulationPageTable`，所以多个 Process 可以使用相同 VA，
但映射到各自 node view 中的 PA。

## 5. DSM VA Window

DSM VA window 由 `setup_dsm_va_mapping()` 建立。

总 DSM segment 数：

```text
T = N * S
```

DSM VA 基址：

```text
DSM_VA_BASE = 2^48 - (T + 1) * SEG
```

保留最高的一个 `SEG`，实际映射 `T` 个 DSM segment。

对于当前 worker 的每个 Process：

```text
seg_idx = home_node * S + home_socket

VA = DSM_VA_BASE + seg_idx * SEG
PA = B_R + (2 + seg_idx) * SEG
```

映射大小均为一个完整 `SEG`，当前为 128 MiB。

### 5.1 3n1s DSM VA 示例

当 `N=3, S=1`：

```text
DSM_VA_BASE = 0xFFFFE0000000
```

| VA 范围 | 映射语义 | node `R` 中的 PA |
|---|---|---|
| `[0xFFFFE0000000, 0xFFFFE8000000)` | DSM home 0 | `[B_R+0x10000000, B_R+0x18000000)` |
| `[0xFFFFE8000000, 0xFFFFF0000000)` | DSM home 1 | `[B_R+0x18000000, B_R+0x20000000)` |
| `[0xFFFFF0000000, 0xFFFFF8000000)` | DSM home 2 | `[B_R+0x20000000, B_R+0x28000000)` |

所有 worker 使用相同 DSM VA。不同 worker 的 PA 高 40 位不同：

```text
node 0 process: DSM VA -> node 0 PA view
node 1 process: DSM VA -> node 1 PA view
node 2 process: DSM VA -> node 2 PA view
```

这样 workload 可以用统一指针访问 DSM，EP 路径再根据 PA 中的 home segment
完成 home-node 路由。

## 6. ELF `PT_LOAD` 映射

`test_e2e.py` 直接解析 ELF64 program header，并枚举所有 `PT_LOAD` segment 的
`p_vaddr` 和 `p_memsz`。

每个 worker 为所有全局 node 初始化物理页计数器，但只使用本地 node `R` 的一项：

```text
next_pa[R] = B_R + 0x00100000
```

对当前 worker 中每个 Process、每个 `PT_LOAD` 页面：

```text
VA = ELF segment 中的原始页面 VA
PA = next_pa[R]
next_pa[R] += 4096
size = 4096
cacheable = true
```

不同 Process 的 ELF 页面获得不同 PA，因为 `next_pa[R]` 在 worker 内共享并单调
递增。各 Process 的 VA 可以相同，但不会映射到同一物理页。

## 7. Local-Private 测试窗口

配置还为每个 Process 显式建立 64 MiB local-private 映射：

```text
LOCAL_VA_BASE = 0x01000000
LOCAL_SIZE = 64 MiB
```

映射公式：

```text
VA = LOCAL_VA_BASE + offset
PA = B_R + offset
0 <= offset < 64 MiB
```

因此：

```text
[0x01000000, 0x05000000)
    ->
[B_R, B_R + 0x04000000)
```

该窗口用于 TC112、L3 pressure 等需要直接访问 node-local PA 的测试。

## 8. 普通缺页分配

未被显式 `proc.map()` 覆盖的 stack 扩展、heap、mmap 或其他缺页，仍可能进入：

```cpp
Process::allocateMem()
    -> SEWorkload::allocPhysPages(npages, physPoolId)
    -> MemPools::allocPhysPages(npages, pool_id)
```

当前 split worker 中所有 Process 使用：

```text
phys_pool_id = R
```

本机 `SEWorkload::setSystem()` 在没有 conf-reported memory 时创建 fallback pools：

```text
pool[i] = [i<<40, i<<40 + 256 MiB)
```

在该 fallback 条件成立时，`phys_pool_id=R` 会让普通页从 node `R` 的 PA view
分配。

必须注意：`phys_pool_id` 是 `MemPools` vector 下标，不是强类型 node ID。如果
远端使用真实 `AbstractMemory` ranges populate pools，pool 顺序必须另行确认，不能
自动假设 `pool[R]` 对应 node `R`。

## 9. `system.mem_ranges` 与 Process 页表的区别

`system.mem_ranges` 不会直接创建 Process VA/PA 映射。它用于描述系统物理地址范围、
创建 Ruby `phys_mem` 并参与其他系统配置。

在调用 `Ruby.create_system()` 前，当前代码临时使用一个从 0 开始、覆盖最高 node
末端的连续范围：

```text
[0, max_pa)
```

目的是让 Ruby `phys_mem` 覆盖所有 node view。

Ruby 创建后，代码又把 `system.mem_ranges` 改成所有全局 node 的：

- local-private ranges
- routing ranges
- DSM ranges

这与单个 worker 只创建 node `R` 的 controller 不同：逻辑地址声明仍覆盖全局
`N` 个 node。

Process 页表的真实映射只由：

- ELF loader/缺页路径
- `proc.map()`
- syscall/mmap 等运行时路径

决定。

## 10. 当前实际执行顺序

当前本机代码的顺序是：

```text
1. 创建 Root。
2. 创建 node R 的 ArmSystem、4 个 CPU 和 4 个 Process。
3. 每个 Process 设置 phys_pool_id=R。
4. Ruby.create_system()：
   a. 创建 RubySystem/network/phys_mem。
   b. create_ubcc_system() 创建 node R 的 Ruby controller。
   c. create_ubcc_system() 末尾调用 setup_dsm_va_mapping()。
5. Ruby.create_system() 返回。
6. test_e2e.py 显式映射 ELF PT_LOAD 页面。
7. test_e2e.py 显式映射 64 MiB local-private VA window。
8. 重建 system.memories。
9. 调用 m5.instantiate()。
10. 调用 m5.simulate()。
```

因此当前 workspace 中，DSM、ELF 和 local-private 三类显式映射都发生在
`m5.instantiate()` 之前。

`Process.map` 是 `@cxxMethod`，调用时会隐式请求 Process C++ 对象。配置必须保证
Root、System、workload 和 Process proxy 已足够完整，否则可能提前物化 C++ 参数或
触发 `Parent.eventq_index` 等 proxy 错误。

如果远端已将 DSM mapping 移到 `m5.instantiate()` 之后，则 DSM 的 VA/PA 公式不变，
只有映射时机改变。ELF/local 映射是否也移动，必须单独检查远端代码。

## 11. 当前实现的注意事项

### 11.1 注释与实际 stack 映射不一致

代码注释写的是“Pre-map binary + stack pages”，但当前显式代码只映射：

- ELF `PT_LOAD`
- local-private VA window

没有单独枚举并映射初始 stack VA。未被其他初始化路径覆盖的 stack 页面仍可能通过
`Process::allocateMem()` 和 `phys_pool_id` 分配。

### 11.2 ELF PA 与 local-private 测试 PA 可能重叠

ELF PA 从：

```text
B_R + 1 MiB
```

开始分配；local-private 测试窗口映射到：

```text
[B_R, B_R + 64 MiB)
```

因此 local-private PA window 包含 ELF 预分配 PA 区域。两个不同 VA 可能别名到同一
PA。这是当前实现的实际行为，应视为待清理风险，不能默认认为两类映射物理隔离。

### 11.3 多个 Process 的 DSM PA 有意相同

同一 worker 中不同 Process 的同一 DSM VA 映射到同一 DSM PA。这是共享 DSM
语义，而不是冲突：

```text
Process A DSM VA -> node R DSM PA
Process B DSM VA -> 同一个 node R DSM PA
```

不同 Process 的普通 ELF/heap/stack 页则原则上应使用不同物理页。

### 11.4 `cacheable=True`

当前三类显式 `proc.map()` 都使用 `cacheable=True`。DSM 访问会经过 L1/L2/HNF，
依赖 UBCC recall/invalidate 保持一致性。

## 12. 3n1s 单 Worker 汇总

对负责全局 node `R` 的一个 gem5 worker：

```text
B_R = R << 40
```

主要映射为：

| 用途 | VA | PA |
|---|---|---|
| ELF PT_LOAD | ELF 自带 VA | 从 `B_R + 1 MiB` 起逐页分配 |
| Local test window | `[0x01000000, 0x05000000)` | `[B_R, B_R + 64 MiB)` |
| DSM home 0 | `[0xFFFFE0000000, 0xFFFFE8000000)` | `[B_R+0x10000000, B_R+0x18000000)` |
| DSM home 1 | `[0xFFFFE8000000, 0xFFFFF0000000)` | `[B_R+0x18000000, B_R+0x20000000)` |
| DSM home 2 | `[0xFFFFF0000000, 0xFFFFF8000000)` | `[B_R+0x20000000, B_R+0x28000000)` |
| 普通缺页 | 运行时 VA | `MemPool[phys_pool_id=R]` 分配 |

CPU 发出的 DSM PA 始终是 requester node `R` 的本地 view。跨 node EP/UBCC 路径再将
它转换为 home node view，并通过 ubio/IPC 访问对应 home plane。
