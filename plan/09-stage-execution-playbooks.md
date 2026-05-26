# Stage Execution Playbooks

本文件把 `T0` 与 `M4 ~ M7` 细化为 implementer 可直接执行的阶段内顺序、最小可交付 diff 范围与停机点。

## 1. 通用执行规则

### 1.1 阶段内固定顺序

每个阶段都按以下顺序推进:
1. 读取本阶段相关计划章节
2. 明确本阶段状态转移表与非法状态
3. 先实现最小测试 hook / inspection API
4. 先落最小负例/结构性 testcase
5. 再实现最小协议路径
6. 再补端到端 testcase
7. 跑本阶段测试
8. 跑 `TC1..TC5` 回归
9. 输出实现结果或未完成状态

### 1.2 最小可交付 diff 原则

每阶段优先拆成 2 类 diff:
- `A类`: 结构准备 diff
  - test hook
  - inspection API
  - 最小消息字段
  - 最小 helper
- `B类`: 协议行为 diff
  - 真正的状态转移
  - 真正的消息路径
  - 真正的 directory 更新

若阶段中断，至少应保证 `A类` 与 `B类` 边界清晰，便于 validator 判断“做到哪里”。

### 1.3 停机点规则

允许的阶段内停机点:
- 完成 test hook，但尚未完成协议行为
- 完成单条主路径，但尚未完成全部边界路径
- 完成 Python 注入测试，但 ARM_SYNC 闭环尚未完成

不允许的停机点:
- 已修改核心协议路径，但没有任何 inspection/test 可验证
- 改了一半消息字段语义，但未同步测试与文档

## 1.5 M3.5 Playbook

### 1.5.1 阶段内任务顺序

1. orchestrator 派发 implementer
2. implementer 仅修改根目录 `readme.md`
3. implementer 返回结果
4. orchestrator 派发 validator
5. validator 检查新增行是否存在
6. 若 PASS，则 orchestrator 暂停并等待用户确认

### 1.5.2 最小可交付 diff 范围

必须且仅需包含:
- 根目录 `readme.md` 新增一行 `Agent test 666!`

### 1.5.3 不允许的实现捷径

- 不修改 `readme.md` 仅在报告中声称已修改
- 跳过 validator
- `M3.5` PASS 后直接自动进入 `T0` 或 `M4`

### 1.5.4 阶段完成判据

- `readme.md` 中存在 `Agent test 666!`
- validator PASS
- orchestrator 暂停等待用户确认

## 2. T0 Playbook

### 2.1 阶段内任务顺序

1. 注册 syscall 编号与 handler
2. 创建 barrier manager 数据结构
3. 挂到 `System`
4. 实现显式调用线程计数
5. 实现重复使用 reset 逻辑
6. 编写 ARM workload
7. 跑 `TC-T0-1 ~ TC-T0-4`

### 2.2 最小可交付 diff 范围

必须至少包含:
- syscall 注册
- barrier manager
- 1 个通过的 barrier testcase

建议首批不做:
- timeout
- signal
- 复杂调度优化

### 2.3 阶段完成判据

- `TC-T0-1 ~ TC-T0-4` 通过
- barrier 只统计显式调用线程
- 支持重复使用

## 3. M4 Playbook

### 3.1 阶段内任务顺序

1. 添加 HN/dir inspection API
2. 添加最小 sentinel install/remove test hook
3. 写 `TC-M4-1/2/3/4/5`
4. 实现 `EP_RNF` synthetic identity
5. 实现 home-side sentinel insert/remove
6. 实现 `S_SHARER`
7. 实现 `S_OWNER`
8. 实现 `S_PENDING`
9. 验证 install 时序在 grant 可见前完成

### 3.2 最小可交付 diff 范围

必须至少包含:
- HN 原生目录可观测接口
- `S_SHARER` install/remove
- 本地 unique 会 snoop `EP_RNF`

建议第二批再补:
- `S_OWNER`
- `S_PENDING`
- 更复杂冲突路径

### 3.3 不允许的实现捷径

- 直接在 Python 影子结构里记一个 sentinel 状态然后宣称完成
- 在 HN 外另造一套平行 owner/sharer 结构替代原生目录承载
- 让 remote grant 先完成，再异步补 home-side sentinel registration

### 3.4 阶段完成判据

- `EP_RNF` 以 HN 原生 RNF 目录格式被观察到
- `S_SHARER`/`S_OWNER` 均可建立
- 本地请求在需要时真实 snoop `EP_RNF`
- `S_OWNER` 与本地 dirty owner 不共存

## 4. M5 Playbook

### 4.1 阶段内任务顺序

1. 在 `CHIRequestMsg` 增加 `ubcc_needed_perm`
2. 增加 `ubcc_write_intent`
3. 写 sideband inspection API
4. 写 `TC-M5-1/2/7/8`
5. 在 HN -> `EP_SNF` remote DSM 路径填 sideband
6. 在 `EP_SNF` 读取 sideband 并映射 outer request
7. 在 home UBCC 实现 `G_I/G_S/G_E/G_M` 的最小 grant decision
8. 写 `TC-M5-3/4/5/6`
9. 验证 `Shared + true` 非法组合

### 4.2 最小可交付 diff 范围

必须至少包含:
- sideband 字段
- `EP_SNF` 正确读取 sideband
- `GlobalGrantShared` 与 `GlobalGrantExclusive` 分离

建议第二批再补:
- `GlobalGrantModified`
- requester bookkeeping 更完整状态
- debug fallback `force_grant_m`

### 4.3 不允许的实现捷径

- 只改日志，不改消息结构
- 仍把 `E/M` 合并成一个 owner grant
- 通过 PA 猜写意图而不是从 HN 上层 sideband 传下来

### 4.4 阶段完成判据

- `needed_perm + write_intent` 可观测
- `GlobalGrantShared/Exclusive/Modified` 可区分
- 非法 sideband 组合被拒绝

## 5. M6 Playbook

### 5.1 阶段内任务顺序

1. 扩展 `UBCCController` 的 `DirEntry`
2. 增加 `inspectUbccDirForTest`
3. 增加 `inspectRequesterStateForTest`
4. 写 `TC-M6-4/5`
5. 实现 `GlobalRecallOwner`
6. 实现 `EP_RNF` 延迟响应上下文
7. 实现 owner node 经本地 HN 取回数据
8. 写 `TC-M6-1/2/3`

### 5.2 最小可交付 diff 范围

必须至少包含:
- `DirEntry` 的 `G_S/G_E/G_M`
- `GlobalRecallOwner` 主路径
- `EP_RNF` 延迟响应 HN

建议第二批再补:
- 更完整 epoch 管理
- 多 requester 冲突排队

### 5.3 不允许的实现捷径

- 在 home UBCC 常驻保存 line data 以绕开 recall
- 让 `EP_RNF` 在 outer txn 完成前就回复 HN

### 5.4 阶段完成判据

- remote read dirty line 读到最新值
- `EP_RNF` 延迟响应成立
- home UBCC 仍 metadata-only

## 6. M7 Playbook

### 6.1 阶段内任务顺序

1. 为 `UBCCController` 增加 epoch/stale 检查接口
2. 写 `TC-M7-4`
3. 实现 `GlobalWriteback`
4. 实现 `GlobalEvict`
5. 实现 owner transfer
6. 写 `TC-M7-1/2/3/5/6`
7. 回归 single-owner invariant

### 6.2 最小可交付 diff 范围

必须至少包含:
- dirty writeback
- clean evict
- stale epoch 过滤

建议第二批再补:
- owner transfer 全边界情况
- 更复杂 ping-pong 序列

### 6.3 不允许的实现捷径

- stale 响应不做过滤
- owner transfer 完成后目录短时间允许双 owner

### 6.4 阶段完成判据

- dirty writeback / clean evict / owner transfer 均可验证
- 任意时刻最多一个 global owner
- recall 结果分裂规则成立:
  - remote read -> old owner shared
  - remote unique/write -> old owner invalid
