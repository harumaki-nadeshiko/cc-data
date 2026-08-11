# ArmO3CPU 形式化与可靠性补充结果

> 日期：2026-08-10
> 结论分级：focused model-scope PASS + executable E2E PASS
> 非结论：不构成完整 gem5、完整 CHI 或 ARM ISA 弱内存序形式证明

## 1. 影响判定

ArmO3CPU 不直接改变 `ubcc_protocol_core.tla`、`ubcc_multi_pa.tla`、
`ubcc_multi_socket.tla` 和 `ubcc_transport_faults.tla` 的抽象 transition
relation。这些模型从 requester request/transport envelope 开始，验证 Home/目录侧
per-PA canonical state、epoch、tuple、commit、fault handling 和 waiter retirement；CPU
从 TimingSimple 更换为 O3 不会自动使这些模型内的结论失效。

O3 改变的是 CPU/Ruby/EP refinement boundary 的前提：Ruby 可同时看到多笔 CPU
memory operation，response/data 可按不同顺序到达，输出 channel 可临时反压，且 CPU
callback/retirement 不得早于协议真实完成。因此新增独立 focused model，而不是把 O3
pipeline 内部状态塞入 UBCC Home 核心模型。

## 2. 新增模型

| Artifact | 作用 |
|----------|------|
| `verification/tla/ep_o3_completion_backpressure.tla` | 两条不同 cache line、EP-RNF global CHI proxy serialization、Data/`Comp_UC` 任意顺序、显式 no-data completion、`rspOut`/`datOut` 临时反压和可靠重试 |
| `verification/tla/ep_o3_completion_backpressure.cfg` | safety invariants |
| `verification/tla/ep_o3_completion_backpressure_liveness.cfg` | safety invariants + `AllIssuedComplete` temporal property |

模型的严格 callback 条件是：

1. Data beats 全部接收，或显式 no-data completion 将 data phase 标记完成。
2. `Comp_UC` 已接收。
3. `CompAck` 已实际离开 pending state 并注入 response output。
4. 只有上述条件都满足后 callback 才可发生并释放 active proxy transaction。

模型同时检查每条 line 的 grant beat accounting，避免临时 `datOut` 反压造成丢 beat，
并检查一条 line 的 completion 不会完成另一条 line。

## 3. TLC 结果

执行环境：`ubcc-dev:ubuntu20.04` Docker；容器内只读挂载宿主 OpenJDK
11.0.27；TLC 2026.05.26.235334；`TLC_WORKERS=4`。

| Run | Generated | Distinct | Depth | Result |
|-----|----------:|---------:|------:|--------|
| safety | 17,505 | 4,564 | 23 | PASS，0 states left，0 counterexample |
| liveness | 17,505 | 4,564 | 23 | PASS，0 states left，0 counterexample |

用户随后在机器无其他任务时授权提高 worker 数。使用 `TLC_WORKERS=16` 独立复跑
safety/liveness，结果同样为 17,505 generated / 4,564 distinct / depth 23、零反例，
说明结果不依赖原 4-worker 调度。

复现命令的核心部分：

```bash
TLC_WORKERS=4 bash verification/tla/run_tlc.sh \
  verification/tla/ep_o3_completion_backpressure.tla \
  verification/tla/ep_o3_completion_backpressure.cfg 600
TLC_WORKERS=4 bash verification/tla/run_tlc.sh \
  verification/tla/ep_o3_completion_backpressure.tla \
  verification/tla/ep_o3_completion_backpressure_liveness.cfg 600
```

原始日志：

- `verification/results/o3_20260810/tlc_ep_o3_completion_backpressure__ep_o3_completion_backpressure.log`
- `verification/results/o3_20260810/tlc_ep_o3_completion_backpressure__ep_o3_completion_backpressure_liveness.log`
- `verification/results/o3_20260810_workers16/tlc_ep_o3_completion_backpressure__ep_o3_completion_backpressure.log`
- `verification/results/o3_20260810_workers16/tlc_ep_o3_completion_backpressure__ep_o3_completion_backpressure_liveness.log`

SHA256：

| Artifact | SHA256 |
|----------|--------|
| model | `bd5dcccfcdd4003556ad0b20a41b612726a9c893a05f361b14ba14e63f8ac148` |
| safety cfg | `73bb9ab865adbcd50e999ab2b4a5813db8e2298c42ea4749ae37137b9a8c2312` |
| liveness cfg | `d6f6ca48a006bf919421e302c34f5244539365b313a5cfd481165db86045fde0` |
| safety log | `3c960b2c1d675a75e122054bae3dd2c2ff791c12ccc8421d8c26801fee33cedb` |
| liveness log | `0f14f5fb05999dc285410ca6b72185d045c0df1b7af04cc685e5e67ad463d6e5` |
| safety log, 16 workers | `69046f97a24c03021b581b9706fdb2eff57709f3507b9545405e5b87dba0938d` |
| liveness log, 16 workers | `b34da904639b1729389d7e1828ae69b1dbcc256f4f5a2aab4683bf67a5af38a8` |

相邻 focused regression：`ep_rnf_snoop_arbitration.tla` 在用户授权的 16 workers
下 PASS，2,297 generated / 328 distinct / depth 8，日志 SHA256 为
`33dd124a48f82f65ccfefb11f6a532ad8065002d9cf2360bab934760281fd2e5`。
`ep_intra_node_single_vsmall.cfg` 随后使用 28 workers 继续执行。修复前模型约 35 分钟后达到
1,078,428,572 generated / 720,387,925 distinct / depth 28，queue 仍有
360,193,825 states 且持续增长，最终 disk state pool 报 `No space left on device`。
这是明确的 resource-exhausted state explosion，不是 invariant counterexample，也不能标为
PASS。运行同时报告既有 model warning：action 在设置 `txnCount'` 后又把 `txnCount`
列入 `UNCHANGED`（`ep_intra_node_single.tla:399`）。原始日志位于
`verification/results/ep_single_28workers/tlc_ep_intra_node_single__ep_intra_node_single_vsmall.log`，
SHA256 为 `a9332e53cc2f41d6ec46fc8bd3b30f7d8ca5d801a0475265e6d46b249787cab4`。
runner 退出清理已回收临时 `states/`；新 O3 focused model 本身已独立完整收敛。

该 state explosion 随后完成模型级闭环修复。修复后的 EP single suite 不依赖 TLC
`CONSTRAINT`，而由 fresh transaction budget、动作级 channel capacities、retry
coalescing 和 bounded drop counters 保证有限。最终语义复核继续修复
dirty-to-shared stale backing、普通 load oracle 和 pending-eviction dead end，并加入
terminal-state consistency。
最终结果为最大 safety 203,174 distinct / depth 22、最大 liveness 10,184 distinct /
depth 22、bounded-fault 1,436 distinct / depth 18，全部 PASS。详见
`verification/ep_intra_node_single_closure_20260810_zh.md`。

## 4. Liveness 前提

`FairSpec` 对 `UnblockRsp`、`UnblockDat` 和 forward-progress actions 施加 fairness。
因此 liveness PASS 表示 temporary backpressure 最终解除时，pending output 不丢失且所有已
issued line 最终 callback/data drain。永久 output blockage、永久消息丢失、进程崩溃不在该
property 的承诺范围内。

## 5. 实现级证据

正式 O3 配置使用 `ArmO3CPU` 和 Ruby Sequencer
`sequencer_max_outstanding=16`：

| Evidence | Result |
|----------|--------|
| 原有回归 TC1-159、TC200-203、TC210-227 的适用集合 | 146/146 PASS |
| 3-node/1-socket | 107/107 PASS |
| 3-node/2-socket | 6/6 PASS |
| 8-node/1-socket | 7/7 PASS |
| 8-node/2-socket | 8/8 PASS |
| 2-node/1-socket | 18/18 PASS |
| O3 focused TC300-303 | 4/4 PASS |

原有回归汇总为 `logs/v5o3_original_o3_full_20260810/summary.log`。TC300-303
分别覆盖 release/acquire publication、dirty ownership handoff 与 no-data
RecallUnique、32-line MLP data isolation、shared-line invalidation race。测试使用
`DSB SY`、LDAR/STLR 和架构可观察 outcome；不依赖固定 NOP 或
`coherence_settle()` 作为 correctness primitive。

## 6. 仍未证明的边界

- 未对 ArmO3CPU pipeline、LSQ、speculation 和 squash 的全部内部状态建模。
- 未建立完整 ARMv8/AArch64 axiomatic memory model；TC300-303 是 executable
  architectural evidence，不是 ISA-level proof。
- 未建模完整 Ruby TBE、完整 CHI channel/credit topology、真实 queue depth 和任意节点数。
- EP-RNF 当前仍是 node-global `_chiRequestInFlight` single-flight；模型验证其 correctness，
  不证明 per-PA/per-socket multi-transaction 性能扩展。
- 永久 output backpressure 或节点故障不在 liveness 假设内。
- O3 correctness PASS 不证明 HA 合同目标 3；HA 平均时延比较仍为 `UNPROVEN`。

## 7. 结论

O3 不推翻既有 UBCC Home/目录层 model-scope proof；它新增了必须验证的
CPU/Ruby/EP refinement obligations。新增 focused model 已对两条 line、两 beats 的有限
状态空间完整检查 strict completion、跨 line 隔离、可靠 output accounting 和 temporary
backpressure 下的 progress，safety/liveness 均 PASS。完整 ARM ISA memory ordering 和
完整生产实现仍明确保留为未证明边界。
