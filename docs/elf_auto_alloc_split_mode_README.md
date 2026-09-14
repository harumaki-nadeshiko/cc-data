# Split Mode ELF Auto-Allocation Patch

这组 patch 将 split mode 的 ELF、stack、heap 和匿名 mmap 恢复为
`Process::allocateMem()` 自动分配，并把固定 DSM VA/PA mapping 从 Ruby protocol
builder 移到 `m5.instantiate()` 之后、`m5.simulate()` 之前。

## 基线

- 父仓库：`98760b727c745b725d502da4193a9a32db8dc652`
- gem5：`e604f261585bf02df8e1e714a67c03e72352d87a`

## 文件

- `docs/elf_auto_alloc_gem5.patch`：在 gem5 仓库根应用。
- `docs/elf_auto_alloc_parent.patch`：在父仓库根应用。

## 应用

```bash
git -C gem5 apply --check ../docs/elf_auto_alloc_gem5.patch
git -C gem5 apply ../docs/elf_auto_alloc_gem5.patch

git apply --check docs/elf_auto_alloc_parent.patch
git apply docs/elf_auto_alloc_parent.patch
```

应用 gem5 patch 后需要在项目 Docker 镜像中重新构建 gem5。项目规则要求所有编译、
测试和仿真均在 `ubcc-dev:ubuntu20.04` 中执行。

## 行为变化

- `SEWorkload.allocatable_mem_ranges` 明确提供 Process 自动分配池。
- split worker 只提供本地 node 的 local-private 128 MiB 作为 pool 0。
- 当前 split 配置中的 `Process.phys_pool_id=0` 与单个本地 pool 对齐。
- 删除 Python ELF `PT_LOAD` 解析和 `defer_map()` 预映射。
- 删除固定 64 MiB local shared test window。
- `Process::initState()` 自动通过 `allocateMem()` 分配并加载 ELF。
- DSM mapping 仍保留，但在 `m5.instantiate()` 后使用 `proc.map()` 建立。

## 不适用测试

该简化 patch 不建立 `LOCAL_DRAM_VA_BASE=0x01000000` 的共享映射，因此不要运行：

- TC112
- TC131
- TC140

TC228-TC235 仅在 `L3_PRESSURE_LEVEL=0` 且没有设置正数
`L3_PRESSURE_TARGET_LINES` 时可用。启用 local-private L3 pressure 会调用
`local_dram_store()`，需要恢复独立的 local shared mapping 方案。

## 前置条件

- 保留 `ruby.phys_mem` 于最终 `system.memories`，使其获得 backing store。
- DL-SNF 若保留，其重叠 DRAM 必须保持 `in_addr_map=False`。该基线已设置。
- `m5.instantiate()` 必须完整成功后再调用 `setup_dsm_va_mapping()`。
- `setup_dsm_va_mapping()` 必须发生在 `m5.simulate()` 前。

## 最低验证

1. instantiate 前不再有 ELF/local-window `defer_map()`。
2. `Process::initState()` 进入 `allocateMem()`。
3. split node R 的 ELF/stack/heap PA 位于 `[R<<40, R<<40+128MiB)`。
4. 同一 worker 不同 Process 的自动分配 PA 不重叠。
5. DSM mapping 在 instantiate 后成功建立。
6. TC1 等不依赖 local shared window 的基础 workload 正常执行。
