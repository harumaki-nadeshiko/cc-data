# VA-PA Mapping & PA Range 对照表

## 基础参数

```
NODES = 3           SEG_SIZE = 128MB = 0x8000000
MaxAddr = 0xFFFFFFFFFFFF   node_shift = 40 (1<<40 = 0x10000000000)
DSM_VA_BASE = (MaxAddr+1) - (total_segs + 1) * SEG_SIZE
```

---

## 1. num_sockets=1

```
total_segs = 3 × 1 = 3
dsm_va_base = 0x1000000000000 - (3+1) × 0x8000000
            = 0x1000000000000 - 0x20000000
            = 0xFFFFFFE0000000    ← 与 dsm_access.h 一致 ✓
```

### VA→PA (req_node=0 的视图)

| seg_idx | home_node | home_socket | VA | PA (req=0) |
|---------|-----------|-------------|-----|------------|
| 0 | 0 | 0 | `0xFFFFFFE0_000000` | `0x10000000` |
| 1 | 1 | 0 | `0xFFFFFFE0_800000` | `0x18000000` |
| 2 | 2 | 0 | `0xFFFFFFE1_000000` | `0x20000000` |

### req_node=i 的 PA

```
req_base = i << 40 = i × 0x100_0000_0000
DSM PA range: [req_base + 0x10000000, req_base + 0x20000000 + 0x8000000)
            = [req_base + 0x10000000, req_base + 0x28000000)
```

---

## 2. num_sockets=2

```
total_segs = 3 × 2 = 6
dsm_va_base = 0x1000000000000 - (6+1) × 0x8000000
            = 0x1000000000000 - 0x38000000
            = 0xFFFFFFC8000000    ← dsm_access.h 仍是 0xFFFFFFE0000000 ✗ MISMATCH!
```

### VA→PA (req_node=0 的视图)

| seg_idx | home_node | home_socket | VA | PA (req=0) |
|---------|-----------|-------------|-----|------------|
| 0 | 0 | 0 | `0xFFFFFFC8_000000` | `0x10000000` |
| 1 | 0 | 1 | `0xFFFFFFC8_800000` | `0x18000000` |
| 2 | 1 | 0 | `0xFFFFFFC9_000000` | `0x20000000` |
| 3 | 1 | 1 | `0xFFFFFFC9_800000` | `0x28000000` |
| 4 | 2 | 0 | `0xFFFFFFCA_000000` | `0x30000000` |
| 5 | 2 | 1 | `0xFFFFFFCA_800000` | `0x38000000` |

### req_node=i 的 PA

```
req_base = i << 40
DSM PA range: [req_base + 0x10000000, req_base + 0x38000000 + 0x8000000)
            = [req_base + 0x10000000, req_base + 0x40000000)
```

---

## 3. 对照表：VA mismatch 影响

| num_sockets | Python dsm_va_base | Binary DSM_VA_BASE | 差值 |
|-------------|-------------------|-------------------|------|
| 1 | `0xFFFFFFE0_000000` | `0xFFFFFFE0_000000` | 0 ✓ |
| 2 | `0xFFFFFFC8_000000` | `0xFFFFFFE0_000000` | `-0x18000000` ✗ |

所以当 `num_sockets=2` 时，binary 的 `dsm_addr(0, off)` ：
- binary 算 VA = `0xFFFFFFE0_000000 + off`
- framework 映射的 VA 范围从 `0xFFFFFFC8_000000` 开始
- binary 的 VA 比 framework 的 base **高了 0x18000000**
- **此 VA 未被 framework 映射** → MMU fault → gem5 fallback 到非 DSM PA

---

## 4. 修复方案

### 方案 A：编译时传参（推荐）

给 `dsm_access.h` 新增编译时参数：

```c
#ifndef NUM_SOCKETS
#define NUM_SOCKETS 1
#endif
#ifndef NUM_NODES
#define NUM_NODES 3
#endif
#define TOTAL_SEGS  (NUM_NODES * NUM_SOCKETS)
#define DSM_VA_BASE ((0xFFFFFFFFFFFFULL + 1) - (TOTAL_SEGS + 1) * SEG_SIZE)
```

dual-socket ELF 编译：
```bash
aarch64-linux-gnu-gcc -DNUM_SOCKETS=2 -DNUM_NODES=3 ...
```

### 方案 B：框架传参运行时

框架通过 `argv[3]` 传入 `num_sockets`，binary 在运行时计算 VA base。

### 方案 C：固定总段数

`dsm_access.h` 使用足够大的总段数（如 6），使 `DSM_VA_BASE` 兼容 num_sockets=1 和 num_sockets=2：

```c
#define MAX_TOTAL_SEGS 6   // = max(num_nodes * num_sockets)
#define DSM_VA_BASE ((0xFFFFFFFFFFFFULL + 1) - (MAX_TOTAL_SEGS + 1) * SEG_SIZE)
```

num_sockets=1 时，VA 从 `0xFFFFFFC8_000000` 开始，但只有前 3 个段(0x18000000)被映射。binary 访问 home=0/1/2 会落在映射范围内。

**推荐方案 A+B 组合**：测试框架编译 dual-socket ELF 时传 `-DNUM_SOCKETS=2`，保证 VA 精确匹配。
