# EP Intra-Node Single 模型闭合修复结果

> 日期：2026-08-10，最终语义复核与复跑：2026-08-11
> TLC：2026.05.26.235334，OpenJDK 11.0.27，Docker `ubcc-dev:ubuntu20.04`
> 正式复跑：`TLC_WORKERS=28`

## 1. 修复前根因

修复前 `ep_intra_node_single.tla` 的可达状态集合是无限的，而不是尚未跑完的巨大有限图：

- `CpuRetry` 可对同一 pending CPU 无限 `Append(reqQ, ...)`。
- `MaxTxn` 在 issue 时不递增，不能限制 fresh operation 或 outstanding request。
- `TailSeq` 对长度 `<=999` 的队列清空全部元素，而不是删除队首。
- `HnfInstallGrant` 同时设置 `txnCount' = txnCount + 1` 并把 `txnCount` 列入
  `UNCHANGED`，使 action 恒为 false。
- watchdog 可无限追加 `COMP_UC`；channel 无动作级容量。
- `TypeOK` 未覆盖 TBE、queue、counter 和多数 boolean fields。
- RNF completion path 从 `Init` 不可达，且 `hnfTbeValid` guard 互相矛盾。

28-worker 修复前运行达到 1,078,428,572 generated / 720,387,925 distinct /
depth 28，仍有 360,193,825 states queued，随后 disk state pool 耗尽。这是
resource-exhausted state explosion，不是 safety counterexample。

## 2. 分阶段修复

### 2.1 确定性模型错误

- `TailSeq` 改为只删除队首。
- `txnCount` 改为 fresh CPU operation issue budget；load/store/store-hit/evict 在 issue
  时递增，retry 不递增。
- 删除 completion 阶段的重复计数和矛盾 `UNCHANGED`。
- 用显式括号固定 `IF/ELSE` action grouping。

### 2.2 有限状态闭合

- 增加 `ReqQCap`、`SnpQCap`、`RspQCap`、`DatQCap`。
- 所有 `Append` 在动作内检查对应 channel capacity；满队列状态仍可达，sender 不推进。
- `CpuRetry` 在同一 tuple 已 queued 或 active 时 coalesce。
- 增加有界 `MaxReqDrops`、`MaxCompUCDrops` 和有限 drop counters。
- watchdog 仅在尚未观察 `Comp_UC` 且 queue 中无 `Comp_UC` 时重发。
- 扩充 `TypeOK`，覆盖全部函数、TBE fields、queues、capacities、fault counters、timer
  和 backend grant validity。
- safety finiteness 不依赖 fairness；fairness 仅用于已 admitted transaction 的内部调度。

### 2.3 RNF completion path

- 增加 `EpRnfAcquireShared`，抽象一个已完成的远程 ReadShared，使 EPRNF 合法进入
  `HAVE_SC` 并成为 HNF sharer。
- CleanUnique receive 要求 issuing TBE 处于 `WAIT_COMP_UC`，不再要求 TBE 无效。
- `SelfSnoopGuard` 改为允许 pending CU 与 issuing TBE 共存，但 phase 必须属于 completion
  phases。
- 使用 `hnfPendingOwnerUpdate` 区分 direct-hit grant 与 post-snoop grant。

TLC 在此阶段发现原模型存在 action overlap：`WAIT_GRANT` 时 `HnfHitServe` 和
`HnfGrantAfterSnoop` 同时 enabled，前者可把 RU requester 错置为 `I`。当前已由
`hnfPendingOwnerUpdate` 精确区分。

### 2.4 Payload 和 grant validity

- RU 成功时提交 `hnfTbeGrantData`/`cpuPendingData`，不再提交 backend 旧值。
- owner handoff 使用 HNF 已保存的最新 owner data，不回退到旧 DRAM。
- backend grant 返回 `dramData`，允许 singleton payload control model。
- 增加 `backendGrantValid`，不再使用 `backendGrantData > 0` 判断 grant 是否存在。

TLC coverage 确认 singleton payload 0 下 `BackendSendClear` 和
`BackendRecvClearAck` 各执行 8 次，零值不再被错误解释为 invalid。

### 2.5 Dirty-to-shared 数据责任与 load oracle

独立复审发现，原 owner snoop 抽象会把 `UD` 无条件降为 `SC`，却不更新 DRAM；后续
`SC` read 又错误回源到旧 DRAM。与此同时，旧 `DataIntegrity` 只约束 `UC/UD`，普通
`SC` load 即使获得旧值也不会触发 invariant。

最终修复为：

- 有效 `SC` read 由 HNF 本地 `hnfData` 服务，不再访问 backend。
- dirty owner 被 snoop 时，在该原子 handoff 抽象中把 `hnfData` 同步到 `dramData`，再
  进入 clean shared。
- `DataIntegrity` 覆盖所有稳定 CPU holder：`SC/UC/UD`。
- 新增 `SharedBackingDataConsistent`：`SC` 时 HNF 和 DRAM 均等于
  `latestGlobalWrite`；`I` 时 DRAM 等于最新全局写。

targeted reachability 检查命中了原缺陷对应的三事务序列，最终状态为两个 `SC` CPU、
HNF 和 DRAM 全部持有最新值 1，而不是从旧 DRAM 返回 0。

### 2.6 Pending request 与 terminal-state 完整性

复审还发现，CPU sharer invalidation 会把另一个 pending read/write 请求直接改为 `I`，
使 `AllCpuRequestsTerminate` 可通过静默取消满足。修复后：

- `P_RS/P_RU` 不被另一事务的 sharer invalidation 取消，仍由已排队 request 或 retry
  完成。
- 已发起 eviction 的 `P_EVICT` 若其副本被另一事务 invalidated，则转为 `I`，其旧 queue
  entry 由 stale-request retirement 清理。
- 新增 `TerminalStateConsistent == (~ENABLED Next) =>
  (txnCount = MaxTxn /\ SystemQuiescent)`。因此虽然 bounded model 使用
  `CHECK_DEADLOCK FALSE` 允许合法终止，但任何无 non-stuttering action 的状态都必须是
  transaction budget 已用尽后的完整 quiescence。

该 invariant 首次运行实际发现了一个 `P_EVICT` stale dead end；上述 eviction 处理修复后，
全部配置重新完整 PASS。

## 3. 最终 TLC 结果

| Config | Scope | Generated | Distinct | Depth | Result |
|--------|-------|----------:|---------:|------:|--------|
| `ep_intra_node_single_nano.cfg` | 2 CPU、1 txn、singleton payload、queue cap 1 | 34 | 34 | 9 | PASS |
| `ep_intra_node_single_vsmall.cfg` | 2 CPU、1 txn、2 payload values | 50 | 50 | 9 | PASS |
| `ep_intra_node_single_small.cfg` | 2 CPU、2 txn、payload + queue 2 | 954 | 542 | 15 | PASS |
| `ep_intra_node_single_rnf.cfg` | reachable RNF CompUC/CompAck/callback | 918 | 524 | 15 | PASS |
| `ep_intra_node_single_watchdog.cfg` | one request drop + one CompUC drop safety | 2,748 | 1,436 | 18 | PASS |
| `ep_intra_node_single.cfg` | 8 CPU、3 txn、ReqQ=3、other caps=2 | 457,504 | 203,174 | 22 | PASS |
| `ep_intra_node_single_liveness.cfg` | 2 CPU normal safety + 5 progress properties | 954 | 542 | 15 | PASS |
| `ep_intra_node_single_fault_liveness.cfg` | bounded-drop safety + 5 progress properties | 2,748 | 1,436 | 18 | PASS |
| `ep_intra_node_single_max_liveness.cfg` | 3 CPU、3 txn mixed-sharer safety + 5 progress properties | 22,509 | 10,184 | 22 | PASS |

所有运行均为完整 state graph search，`0 states left on queue`。最终原始日志位于
`verification/results/ep_single_repair_final_semantics/`；最大配置约 4 秒完成。

修复前后按 distinct states 比较：

```text
720,387,925 observed before disk exhaustion
203,174 complete largest bounded graph
ratio ~= 3,545x
```

该比例不是性能优化比；修复前空间无限，720M 只是耗尽前缀。它只说明无界 retry history
是原 explosion 的主因。

## 4. Coverage 证据

最终 coverage logs 为
`verification/results/ep_single_repair_datafix_coverage/coverage_final_max.log` 和
`coverage_final_fault.log`。它们确认：

- `DropReq` 非零：111 source states / 391 transitions。
- `DropCompUC` 非零：16 source states / 32 transitions。
- `CpuRetry` 非零：233 successful transitions。
- watchdog fire 非零：8 successful transitions；watchdog tick、`HnfRecvCompUC`、
  `EpRnfSendCompAck`、`HnfRecvCompAck`、`EpRnfCallback` 均非零。
- `SC` local hit、dirty owner handoff、RNF snoop、CPU sharer invalidation、backend Clear
  send/ack 均非零。
- targeted reachability 证明 EPRNF `CompUC+CompAck` 后仍有 CPU sharer时，会进入
  `WAIT_SNP_CU` 继续 invalidation，而不是直接 grant unique。

## 5. 当前 proof claim

当前可声明：

- 在配置的 CPU 数、fresh transaction budget、payload values、per-channel capacities 和
  bounded drop budget 内，状态空间由模型语义天然有限并完整枚举。
- safety 覆盖 Type、queue capacity、stable-holder/load data、shared backing data、dirty
  consistency、single dirty owner、callback ordering、writeback、grant lifetime、watchdog
  consistency、RNF completion phases 和 terminal-state consistency。
- liveness 在内部 forward-progress actions 的 weak fairness 下验证 admitted CPU request
  最终退出 pending、TBE/grant/watchdog 最终解除；在 `txnCount = MaxTxn` 已成立的行为上，
  系统最终完整 quiescent。

不能声明：

- 任意 queue depth、任意 CPU 数或无限 transaction stream 已证明。
- 永久 request/CompUC loss 会成功恢复；当前 fault envelope 每类最多 drop 一次。
- 完整 gem5、完整 CHI credit/channel 或 ARM ISA memory model 已证明。
- `EpRnfAcquireShared` 是完整 UBCC/Ruby request flow；它是 focused boundary abstraction。
- `BoundedRunTerminates` 证明运行必然发满 `MaxTxn`；fresh transaction generation 没有
  fairness，该 property 是达到 budget 后的条件性 drain 证明。
- `WatchdogTimeout=2` 是真实时间或全局 scheduler steps 上界；模型证明的是 bounded timer
  domain 下的 eventual recovery。
- EP-RNF data payload 正确性；RNF 在本模型中是无 data field 的目录/completion 抽象。

## 6. SHA256

| Artifact | SHA256 |
|----------|--------|
| model | `aad9b87c09f8234b59f24fb5b6eac76ca7cec46ef2097bdd91a3ae1aff7c9bf8` |
| nano | `22b550f9aa557eef900ea5cd4d15e996dc70b17cca8877469b59b3f85b15e837` |
| vsmall | `c7f94b8c70b86156b5dce5f9fc1f9bc1a1269a0baa7d24511f536631a329f9ed` |
| small | `a11fcd589140eba57a2aa965283ded4d908be76cf05ee7d744c31c0aeb9fb782` |
| RNF | `0ffd5721164e9bc9ca1dc119fe81158b6da9e90d37cc3c8d38cbd518b28cde40` |
| bounded-drop safety | `d711222459f1bb7d14314bd69a1c5c07c541a455e2c663583e482abf5b3d2029` |
| liveness | `93fbe211d0347bbfc0cf7d910cc78a5669fda825b117b5476b6dd58759c91af8` |
| bounded-drop liveness | `9333e04c0093e23fe72981ce908c76efb792d1b28b834d831e31314614c9dec6` |
| max liveness | `d09d885838399e0514411dbe93c8d29146355af2386bfd1dc549b6cd2cabbb25` |
| max safety | `81b9e5ff87f18e3cf92dec9ddd19bd1f7c90db566d4291f046f560067d2ecb92` |

以上 hash 对应最终完整 invariant suite。若后续编辑模型或 cfg，应重新生成并更新。
