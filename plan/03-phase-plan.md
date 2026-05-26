# Phase Plan

## 1. 总体阶段顺序

推荐顺序固定为:
1. `M3.5` - Multi-agent collaboration smoke check
2. `T0` - `Sync_Wait(node_mask)`
3. `M4` - Sentinel registration
4. `M5` - Remote miss with permission sideband
5. `M6` - UBCC directory + EP_RNF local coherent access
6. `M7` - Writeback / evict / owner transfer
7. `M8` - Shared-read hardening and upgrade/invalidate closure
8. `M9` - Metadata model + multi-gem5 preparation

说明:
- `M3.5` 是 `T0` 之前的协作框架冒烟验证阶段，用于确认 orchestrator -> implementer -> validator 链路行为符合预期。
- 任何需要多节点同步的 testcase，不得绕过 `T0`。
- 因此 `T0` 是后续协议阶段测试的硬前置任务。
- 当前主目标阶段是 `M3.5` 与 `M4 ~ M7`。
- `M8 ~ M9` 保留为可选后续阶段，不作为当前主交付承诺。

## 2. M3.5 - Multi-agent Collaboration Smoke Check

### 2.5.1 阶段目标

验证 orchestrator -> implementer -> validator 的协作链路是否按预期工作。

### 2.5.2 唯一任务

在仓库根目录 `readme.md` 新增一行:

```text
Agent test 666!
```

### 2.5.3 强制执行顺序

1. orchestrator 必须先调用 implementer 做该修改。
2. implementer 完成后，orchestrator 必须再调用 validator。
3. validator 只检查 `readme.md` 是否确实存在新增行 `Agent test 666!`。

### 2.5.4 验收标准

- `readme.md` 中存在新增行 `Agent test 666!`
- validator 明确给出 PASS

### 2.5.5 特殊暂停规则

`M3.5` 验收成功后:
- orchestrator 不得直接进入 `T0` 或 `M4`
- 必须暂停当前对话
- 等待用户明确确认之后，才允许开始后续阶段分发

### 2.5.6 失败处理

- 若 implementer 未完成修改，则按 `INCOMPLETE` 处理
- 若 validator 未能确认该行存在，则 `M3.5` 不通过
- 若遇到 API 限额，则按 checkpoint 规则停止并落盘

## 3. 阶段通用门槛

每阶段开始前必须满足:
- 当前代码可编译。
- `TC1` 到 `TC5` 回归通过。
- 上一阶段 reviewer verdict 为 PASS。

每阶段结束时必须满足:
- `scons build/ARM/gem5.opt -j20 PROTOCOL=CHI` 通过。
- 本阶段新增 testcase 全部通过。
- `TC1` 到 `TC5` 不回退。
- 阶段报告写明已实现边界与未实现边界。

## 4. T0 - Sync_Wait(node_mask)

### 4.1 阶段目标

实现 SE-mode 下的跨 node barrier syscall，为后续多节点 directed testcase 提供可重复、可验证的同步原语。

### 4.2 输入

- 当前 Phase1-3 基线代码
- `plan/04-test-plan.md` 中 `T0` testcase 规范

### 4.3 主要任务

1. 注册 ARM 自定义 syscall。
2. 实现 `SyncWait` barrier 状态对象。
3. 让 barrier 状态全局可见。
4. 支持按 `node_mask` 区分 barrier 实例。
5. 支持重复使用，不残留 stale 状态。
6. 增加最小测试 workload 和脚本。
7. barrier 统计对象只包含显式调用该 syscall 的线程。

### 4.4 不做的事

- 不做 timeout。
- 不做 signal/interruption。
- 不做 full-system Linux 支持。

### 4.5 出口标准

- `T0` testcase 全部通过。
- 后续多节点协议 testcase 改为使用该 barrier。

## 5. M4 - Sentinel Registration

### 5.1 阶段目标

在不大改 HN 状态机的前提下，实现严格定义的 home-side `sentinel registration`:
- 在 home node 的 HN directory 中对 `EP_RNF` 做 insert/update/remove
- 并在本地权限变化时真实 snoop `EP_RNF`
- 且优先使用与普通 CPU cluster RNF 相同的 HN 原生目录格式表达 `EP_RNF`

### 5.2 输入

- `T0` 已完成
- `EP_RNF`/`EPBackend` skeleton

### 5.3 主要任务

1. 定义 `EP_RNF` synthetic identity。
2. 实现 home-side sentinel insert/update/remove 接口。
3. 支持 `S_SHARER`。
4. 支持 `S_OWNER`。
5. 支持 `S_PENDING` 或等价 transient 表达。
6. 增加非 DSM 保护。
7. 增加 HN 最小 hook，不重写 HN 状态机。

### 5.4 推荐实现策略

- 优先在 directory 维护层和 controller helper 层做扩展。
- 避免修改 HN 主状态机的状态定义和主转移表。
- 如必须修改，也应限制在 sentinel hook 点。

### 5.5 出口标准

- 本地 unique/read 流在 sentinel 存在时会真实 snoop `EP_RNF`。
- `S_OWNER` 与本地真实 dirty owner 不共存。
- home-side sentinel insert/update 在 remote grant 对 requester 可见之前已经完成。
- `EP_RNF` 在 HN 内优先以原生 RNF 目录格式表达，而不是并行 sentinel 专用格式。

## 6. M5 - Remote Miss With Permission Sideband

### 6.1 阶段目标

实现 requester 侧 remote DSM miss 闭环，并优先采用"HN 发 `ReadNoSnp` 时携带 `Shared/Unique` 意图"的最小修改路线。

### 6.2 输入

- `T0`、`M4` 已完成
- `NodeAddressMap` 已存在

### 6.3 主要任务

1. 定义 HN -> `EP_SNF` 的 UBCC sideband 字段。
2. sideband 固定携带:
   - `needed_perm = Shared | Unique`
   - `write_intent = false | true`
3. `EP_SNF` 根据 sideband 发 `GlobalReadShared` 或 `GlobalReadUnique`。
4. `EPBackend` 建立 requester transaction context。
5. home UBCC 支持最小读 miss 决策与 data 返回。
6. 保留 debug fallback: `force_grant_m`，但默认不作为唯一主路线。
7. sideband 主路线采用直接扩展消息字段，而不是 side table。
8. requester 侧若需记账，统一作为 `requester-side external-state bookkeeping` 处理，不把它混称为 sentinel registration。
9. 为满足 home UBCC 的 MESI 要求，设计中必须显式区分 `GrantExclusive` 与 `GrantModified` 的条件，不得继续把 `E/M` 合并成单一 owner 态。
10. `write_intent` 的来源固定为 HN-F 上层原始请求语义 sideband，而不是 PA 解析或后验猜测。

### 6.4 阶段边界

本阶段只要求 first miss 闭环，不要求完整 owner recall / writeback。

### 6.5 出口标准

- requester 远程读可按 `Shared/Unique + write_intent` 取得 `GrantShared/Exclusive/Modified`。
- 不需要通过大改 HN 状态机来区分 `Shared/Unique/write_intent`。

## 7. M6 - UBCC Directory + EP_RNF Local Coherent Access

### 7.1 阶段目标

让 home UBCC 能通过 `EP_RNF` 对本地 CHI domain 发起真实 coherent 操作，并完成 dirty recall/read closure。

### 7.2 主要任务

1. 实现 per-line global directory。
2. 实现 active transaction 管理。
3. 实现 `GlobalRecallOwner`。
4. 实现 UBCC -> `EP_RNF` -> HN -> local cache 的 recall 通路。
5. `EP_RNF` 必须延迟答复 HN，直到 outer transaction 完成。
6. home UBCC 只管理目录/元数据，不持有长期缓存的数据本体。
7. home UBCC 的 per-line state 必须使用 MESI，显式区分 `E` 和 `M`。

### 7.3 出口标准

- remote read local dirty line 拿到最新值。
- home/reader/owner 三侧状态一致。

## 8. M7 - Writeback / Evict / Owner Transfer

### 8.1 阶段目标

补齐 dirty writeback、clean evict、owner transfer，支撑三节点 ping-pong。

### 8.2 主要任务

1. requester dirty writeback -> home ack。
2. requester clean evict -> sharer mask 更新。
3. owner transfer。
4. 引入 `epoch` 或等价 stale 防护。
5. 继续坚持"home UBCC 不缓存实际 data"的设计边界。
6. 对 owner recall 结果维持当前约定:
   - remote read -> owner 降级为 shared
   - remote unique/write -> owner 失效为 invalid

### 8.3 出口标准

- 任意时刻最多一个 global owner。
- dirty data 不丢失。

## 9. M8 - Shared-Read Hardening And Upgrade/Invalidate Closure

### 9.1 阶段目标

把 `Shared` 路径从"能跑"提升到"可稳定验收"，并补齐多 sharer、upgrade、global invalidate 的闭环。

说明:
- 本阶段当前是可选阶段，不属于当前主承诺范围。

### 9.2 主要任务

1. 多 sharer mask 正确维护。
2. local upgrade 命中 external sharer 时触发 `GlobalInvalidate`。
3. remote sharer ack 收齐后，本地 unique 才完成。
4. 默认启用 `Shared` 路径。
5. `force_grant_m` 保留为 debug 开关，但不再是默认。

### 9.3 出口标准

- 两个 requester 可同时持有 shared。
- 后续 unique write 会正确失效其他 sharer。

## 10. M9 - Metadata Model + Multi-gem5 Preparation

### 10.1 阶段目标

在不影响正确性的前提下，为 metadata 容量模型和外部网络迁移做准备。

说明:
- 本阶段当前是可选阶段，不属于当前主承诺范围。

### 10.2 主要任务

1. 抽象 outer protocol ABI。
2. 如需要，加入 metadata capacity/model。
3. 记录多 gem5 / ns-3 时间假设。

### 10.3 出口标准

- `M4..M8` correctness 不回退。
- 外部迁移假设文档化。
