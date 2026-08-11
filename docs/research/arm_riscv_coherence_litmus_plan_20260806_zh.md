# ARM/RISC-V 跨节点一致性 Litmus 规格

**日期：** 2026-08-06  
**性质：** 测试计划与预期 outcome；**未运行，不包含任何结果**。

## 1. 目标与非目标

验证：

1. 单地址 coherence order 不被 owner/epoch/duplicate 处理破坏。
2. 多地址 publication 正确实现 acquire/release 和 barrier。
3. store accepted、permission visible、Home commit、next release 与 ISA completion 不被混并。
4. non-FIFO/late completion 不制造双 owner 或逆序观察。
5. 实现不比 Arm/RVWMO 规范更弱。

不自动覆盖 mixed-size、unaligned、atomicity 全集、device/I/O memory、所有 scope、节点 crash、
永久 partition 或 Byzantine。五个测试通过也不是完整 ISA memory-model 证明。

## 2. 工具分层

| 层 | 工具/证据 | 能回答 | 不能回答 |
|---|---|---|---|
| ISA axiomatic | `herd7` + AArch64/RISC-V model | allowed/forbidden outcome | 项目 interconnect 是否正确 |
| hardware/model litmus | `litmus7`/RISC-V harness | endpoint 实际观察 | 未观察 allowed 不等于 forbidden |
| 项目 E2E | guest + root counter + UBCC trace | completion 与 outcome 关联 | 不替代 ISA model |
| 协议形式化 | TLA+ reorder/duplicate | single-writer/no double commit | CPU OoO 全集 |

规范锚点和工具来源见 `ha_coherence_source_matrix_20260806.tsv` 的 S3/S4/S11/S12/S19/S20。

## 3. 通用执行约束

- `x/flag/y` 使用不同 cacheline；L4 例外。
- 每轮恢复初始值并同步起跑；初始化不在被测窗口。
- PE 固定到两个不同节点，记录 PA、Home placement、初始 owner/sharer、数据源。
- 覆盖 Home@requester/Home@peer 和 memory-latest/remote-owner-latest。
- barrier scope 由平台冻结；不得自行猜测 ISH/OSH/SY。
- outcome 只以 architectural register/memory 为准，protocol trace 仅作解释。
- `forbidden_count` 必须为 0；出现一次即 correctness failure。
- allowed outcome 不要求必须观察。

建议 trace 字段：

```text
test_id,iteration,node,PA,home_placement,ISA_instruction,
issue_timestamp,retire_timestamp,reqId,epoch,grant_timestamp,
install_timestamp,clear_timestamp,home_commit,T_next,ClearResp,
observed_registers,seed
```

## 4. L1 Message Passing

```text
x=0; flag=0

P0@N0                       P1@N1
x=1                         r0=ACQUIRE_LOAD(flag)
RELEASE_STORE(flag,1)       if r0==1: r1=x
```

AArch64：`STR x; STLR flag` / `LDAR flag; LDR x`。  
RISC-V：`.rl/.aq` 原子版本，或 `fence rw,w` + `fence r,rw` 版本。

| 条件 | outcome | 判定 |
|---|---|---|
| 正确 release/acquire 且 `r0=1` | `r1=1` | required |
| 正确 release/acquire | `r0=1,r1=0` | **forbidden** |
| 无同步 control | `r0=1,r1=0` | 以固定 herd7 model 为准，通常 allowed |

Oracle：若 flag release 已被 acquire 观察，data line 不能因尚未完成的跨节点 commit 仍读 0。

## 5. L2 Release/Acquire 原语直测

```text
payload=0; seq=0

P0@N0                       P1@N1
payload=42                  r0=load_acquire(seq)
store_release(seq,1)        r1=(r0==1)?payload:-1
```

- Allowed：`r0=0` 或 `r0=1,r1=42`。
- Forbidden：`r0=1,r1` 为旧 payload。
- 必须记录实际汇编，不得把 C/C++ memory_order 标签当汇编证据。

## 6. L3 Store + DMB/DSB/FENCE + Remote Load

Arm：

```text
P0@N0                       P1@N1
STR x,1                     loop LDAR doorbell
DMB/DSB <scope>             LDR x -> r1
STR doorbell,1
```

RISC-V：producer `sw x; fence rw,w; sw doorbell`，consumer 观察 doorbell 后
`fence r,rw; lw x`。

- Forbidden：正确 scope/sets 下 consumer 见 `doorbell=1` 却读 `x=0`。
- `DMB_retire` 证明 ordering，不泛化为所有 external side effect complete。
- `DSB_retire` 与 `T_visible/T_commit/T_next` 的映射必须由平台合同确定并用 trace 检查。

## 7. L4 Same-line Competing Writers

```text
x=0

P0@N0: release_store(x,1)   P1@N1: release_store(x,2)
P2@N0: a=load(x); fence; b=load(x)
P3@N1: c=load(x); fence; d=load(x)
```

Forbidden：

```text
P2: 1 -> 2
P3: 2 -> 1
```

内部 invariant：不得双 Modified owner、不得同 tuple double commit、stale completion 不得覆盖
高 epoch、final data 必须是最后合法写、next ownership 不得越过旧 owner release。

## 8. L5 Independent-line Allowed Reordering

```text
x=0; y=0
P0@N0: x=1; r0=y
P1@N1: y=1; r1=x
```

- 无 fence 时 `r0=0,r1=0` 是否 allowed，以固定 Arm/RVWMO model 输出为准。
- 两侧加入 full DMB 或 `FENCE rw,rw` 后，`0,0` 为 forbidden。
- 无 fence 未观察到 `0,0` 不是 failure。

## 9. Completion-point 断言

| ID | 断言 |
|---|---|
| CP-1 | `T_visible` 时 data 与 authority 属于当前 PA/epoch/transaction |
| CP-2 | `T_commit` epoch 单调且 transaction 最多 commit 一次 |
| CP-3 | `T_next` 前下一冲突不得获得 authority |
| CP-4 | current clear-ack root stop 不早于 ClearResp accepted |
| CP-5 | one-way 实现后，普通 root 与 fence/same-line catch-up 点明确 |
| CP-6 | non-FIFO/duplicate 下 stale completion 不改变目录或释放 waiter |
| CP-7 | barrier retire 映射符合冻结 platform contract |

## 10. 执行矩阵和结果 schema

| 轴 | 值 |
|---|---|
| ISA | AArch64；RISC-V frozen model/extension |
| profile | current clear-ack；one-way 仅实现后 |
| Home | requester-local；peer-local；其他 die class |
| initial state | memory latest；remote shared；remote dirty owner |
| route | central；direct-data-only；authority 仅确认后 |
| load | idle；same-line contention；fault qualification 独立 |
| barrier | none；release/acquire；DMB/DSB scope；FENCE sets |

```text
test_id,isa_model_version,binary_hash,profile,placement,initial_state,
route,barrier_variant,iterations,allowed_outcomes,observed_counts,
forbidden_count,first_failure_seed,invariant_status,
T_visible_summary,T_commit_summary,T_next_summary,root_summary
```

## 11. Pass/Fail

- **PASS：** 所有预定义 forbidden outcome 和 CP invariant 命中数为 0，且官方 model
  allowed set 归档。
- **FAIL：** 任一 forbidden outcome、双 owner、double commit、dirty loss 或 stale epoch commit。
- **INCONCLUSIVE：** model/harness/scope 不匹配、trace 无法关联或样本中断。
- correctness PASS 后才进行性能判定；litmus outcome count 不替代时延测量。
