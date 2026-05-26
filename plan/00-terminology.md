# Terminology

本文件用于统一计划中的关键术语，避免不同 Agent 对同一词语理解不一致。

## 1. 基本角色

### 1.1 Home Node

对一条 DSM line 而言，`home node` 指该 line 所属 DSM 分片的归属节点。

例子:
- 如果地址属于 `DSM_1`，则 `Node1` 是该 line 的 home node。

### 1.2 Requester Node

`requester node` 指当前发起访问该 DSM line 的节点。

例子:
- `Node0` 读取 `Node1` 的 DSM line 时，`Node0` 是 requester，`Node1` 是 home。

### 1.3 Local CHI Domain

某个 node 自己内部的 ordinary CHI coherence 域，包括:
- 本 node CPU clusters
- 本 node HN-F
- 本 node node-local memory-side controllers

ordinary CHI traffic 只能在这个域内传播。

## 2. External Proxy 相关术语

### 2.1 External World

对某个 home node 的 HN-F 来说，`external world` 指“本 node local CHI domain 之外，但可能持有该 line 全局权限的其他 node”。

在 home node 的 HN-F 视角里，这个 external world 通过 `EP_RNF` 抽象成一个 synthetic RNF 参与者。

### 2.2 Sentinel

`sentinel` 指 home-side HN-F directory 中为 `EP_RNF` 建立的一条 synthetic RNF 目录表示。

它的意义是:
- HN-F 必须知道“外部世界”对该 line 可能还有权限或最新数据。
- 因此本地 CPU 后续做 read/upgrade/write 时，HN-F 不能只看本地真实 CPU sharer/owner，还必须把 `EP_RNF` 也当成目录参与者处理。

### 2.3 Sentinel States

计划中使用以下 sentinel 状态名:
- `S_NONE`
- `S_SHARER`
- `S_OWNER`
- `S_PENDING`

这些状态描述的是“external world 在 home-side HN-F 眼里是什么角色”。

## 3. Sentinel Registration 的正式定义

### 3.1 本计划中的严格定义

`sentinel registration` 在本计划中有严格限定含义:

> 对一条 **home 在本 node** 的 DSM line，在 **本 node 的 HN-F directory** 中插入、更新或删除 `EP_RNF` 这条 synthetic RNF 目录项，使 HN-F 能把“external world”纳入本地目录与 snoop 决策。

换句话说，sentinel registration 是 **home-side HN directory maintenance 行为**。

### 3.2 它具体包括什么

sentinel registration 只包括以下三类动作:

1. `insert`
   - 原来没有 `EP_RNF` 目录项
   - 现在因为 external world 获得了 shared/owner 权限，需要把 `EP_RNF` 加入该 line 的 HN-F directory

2. `update`
   - 原来已有 `EP_RNF` 目录项
   - 现在 external world 的语义发生变化，例如从 `S_SHARER` 变成 `S_OWNER`

3. `remove`
   - external world 已不再持有该 line 需要 HN-F 感知的权限
   - 可以从 HN-F directory 中移除 `EP_RNF`

### 3.2.1 它何时必须完成

对于 home node 向 remote requester 发放 grant 的事务，当前更正确的时序要求是:

> home-side sentinel registration 必须在该 grant 对 requester 可见之前完成。

原因:
- 一旦 remote requester 已被允许继续执行，本地 home CPU 与 remote requester 就可能并发观察到该 line 的新权限关系。
- 如果这时 home-side HN-F 还没登记 `EP_RNF`，则本地 HN 可能错误地忽略 external world，导致本地 unique/read 路径绕过应有的 global invalidate / recall。

因此:
- sentinel insert/update 可以与 grant 属于同一个事务收尾阶段
- 但从 correctness 角度看，顺序必须是“先提交 home-side directory 可见性，再让 grant 对 requester 可见”

### 3.3 它不是什么

sentinel registration **不是** 下列行为:

1. 不是 requester node 本地 cache/HN 的普通 miss 分配
2. 不是 home UBCC 创建 global directory entry 本身
3. 不是 requester 侧 remote-line summary 的本地记账
4. 不是 generic 的“某条线被协议关注了”这种宽泛说法

## 4. Requester-Side External-State Bookkeeping

为了避免和 sentinel registration 混淆，计划中把 requester 侧的本地记账单独命名为:

`requester-side external-state bookkeeping`

它的含义是:
- requester node 因 remote miss 获得了 `Shared` 或 `Unique` 之类的 global 权限后
- requester 侧 EP/HN/backend 需要记住“这条 line 与外部全局协议有关”
- 这样后续本地 upgrade/writeback/evict 才知道要走 outer protocol

该行为不是本计划中“sentinel registration”一词的默认含义。

## 5. Sentinel Registration 的触发条件

本计划当前把 sentinel registration 的主语义限定为 home-side，因此其典型触发条件是:

### 5.1 insert 触发

- 某个 remote requester 获得该 home line 的 `Shared` 权限
- 或某个 remote requester 获得该 home line 的 `Owner/Unique` 权限
- 此时 home-side HN-F 必须开始把 external world 视为 sharer/owner 参与者

### 5.2 update 触发

- external world 对同一 line 的 home-side语义发生变化
- 例如从 `S_SHARER` 变为 `S_OWNER`

### 5.3 remove 触发

- home UBCC 确认 external world 已没有 sharer/owner
- 并且不再需要 home-side HN-F 在本地权限变化时 snoop `EP_RNF`

## 6. 为什么需要 Sentinel Registration

如果没有 sentinel registration:
- home-side HN-F 只知道本地真实 CPU cluster 的 owner/sharer
- 它不知道 external world 可能也持有权限
- 那么本地 CPU 后续做 unique/read/write 时，HN-F 可能错误地直接完成事务
- 从而绕过 global invalidate / recall / owner transfer

sentinel registration 的作用就是让 HN-F 在“不大改状态机”的前提下，仍然能把 external world 当作一个可被 snoop 的目录参与者。

## 7. 与 EP_RNF RNF 抽象的一致性要求

- `EP_RNF` 应尽量表现得像一个 RNF 抽象，而不是完全独立于 HN 目录模型之外的怪物状态。
- 因此 sentinel registration 设计优先复用 HN 现有 sharer/owner/transient 表达。
- 更明确地说: home-side HN-F 中 `EP_RNF` 的目录项，优先应当与普通 CPU cluster RNF 使用相同的原生目录格式与状态承载方式。
- 本计划中的 `S_SHARER` / `S_OWNER` / `S_PENDING` 首先是语义标签，而不是要求在 HN 内部额外平行维护一套不同格式的 sentinel 专用状态结构。
- 如果后续发现 `EP_RNF` 的所需状态超出 HN-F 现有可表达范围，必须触发根目录告警文档:
  - `OhNo_EP_RNF_NotGooOod.md`

## 8. 对后续阶段的用词约束

从现在起:
- `M4` 中的 `sentinel registration` 一律指 home-side HN directory 对 `EP_RNF` 的 insert/update/remove。
- requester 侧的状态安装或本地记账，不再用这个词，统一叫:
  - `requester-side external-state bookkeeping`
