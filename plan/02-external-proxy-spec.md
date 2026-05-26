# External Proxy Spec

## 1. 定义

本文中的 External Proxy 不是单一对象，而是以下 4 个组件的组合:
- `EP_RNF_i`
- `EP_SNF_i`
- `EPBackend_i`
- `UBCCController_i`

它负责把本 node 的 local CHI domain 与跨 node 的 UBCC outer protocol 连接起来。

术语约束:
- `sentinel registration` 的严格定义见 `plan/00-terminology.md`
- 本文中的 `sentinel` 默认指 home-side HN directory 中的 `EP_RNF` synthetic entry

## 2. 内外边界

### 2.1 对内

对内面向本 node CHI domain:
- 接收来自本 node HN 的 request/snoop/downstream traffic
- 作为 HN directory 中的 synthetic participant 存在
- 作为 UBCC 对本地 cache/domain 发起 coherent local access 的入口

### 2.2 对外

对外面向其他 node 的 UBCC / EP:
- 传递 global read / write / invalidate / recall / writeback / evict
- 传递 grant / ack / data
- 维护 home directory 和 requester summary

## 3. 组件职责

| 组件 | 角色 | 主要职责 |
|---|---|---|
| `EP_RNF_i` | home-side sentinel + local access agent | 响应 HN snoop；承接 UBCC 对本地 CHI domain 的 coherent 操作 |
| `EP_SNF_i` | requester-side remote data plane | 把 HN 对 remote DSM 的访问转换成 outer request；回送 completion/data |
| `EPBackend_i` | glue / transaction manager | 地址翻译、上下文管理、inner/outer 事务编排 |
| `UBCCController_i` | home directory controller | 对 `DSM_i` 维护全局目录、owner/sharer、grant 和 recall |

## 4. 固定 outer protocol 消息集合

当前先固定以下逻辑消息名，后续代码实现与测试都按这个命名体系组织。

请求类:
- `GlobalReadShared`
- `GlobalReadUnique`
- `GlobalInvalidate`
- `GlobalRecallOwner`
- `GlobalWriteback`
- `GlobalEvict`

响应类:
- `GlobalGrantShared`
- `GlobalGrantExclusive`
- `GlobalGrantModified`
- `GlobalAck`
- `GlobalData`
- `GlobalNackRetry`

内部辅助类:
- `LocalRecallShared`
- `LocalRecallUnique`
- `LocalInvalidate`

说明:
- 这些名字是计划层协议名，不要求第一轮代码里立刻有完全同名类。
- 但阶段实现、报告、测试描述都应使用这套名字，避免术语漂移。

## 4.1 HN -> EP_SNF sideband 基线

主路线:
- 直接给 `CHIRequestMsg` 或等价消息载体增加 UBCC 扩展字段。
- 最低要求字段:
  - `ubcc_needed_perm`
  - 取值至少支持 `Shared` 与 `Unique`
  - `ubcc_write_intent`
  - 来源: HN-F 上层原始请求语义 sideband，而不是由 PA 推导

受控扩展:
- 当前已经选定 `ubcc_write_intent` 作为满足 MESI 区分所需的最小附加字段。
- 除 `ubcc_needed_perm` 与 `ubcc_write_intent` 外，不应继续扩展更多字段，除非后续阶段报告明确证明仍然不够。
- 不允许因此顺手加入 `src_node/home_node` 这类可由 PA 直接解析得到的冗余字段。

约束:
- 优先通过消息扩展字段传递 permission 意图，不优先使用 side table/context map。
- 该扩展字段应只影响发往 `EP_SNF` 的 remote DSM 路径，不应污染普通 node-local CHI 路径语义。

### 4.1.1 Sideband 语义表

当前固定语义:

| `ubcc_needed_perm` | `ubcc_write_intent` | 语义 | 预期 home grant |
|---|---:|---|---|
| `Shared` | `false` | requester 只需共享读权限 | `GlobalGrantShared` |
| `Unique` | `false` | requester 需要独占但当前请求本身不带写意图 | `GlobalGrantExclusive` |
| `Unique` | `true` | requester 当前请求具有写意图，需要 dirty owner | `GlobalGrantModified` |

非法组合:
- `Shared + true` 应视为非法组合，测试中必须拒绝或报错。

## 5. 地址转换规则

### 5.1 Requester PA 到 global tuple

对任意 requester node `src` 的 remote DSM PA:

```text
src_node  = src
home_node = dsmHomeNode(src, pa)
offset    = dsmOffset(pa)
```

输出 tuple:

```text
(src_node, home_node, offset)
```

### 5.2 Global tuple 到 target PA

当 home UBCC 需要在目标节点 `tgt` 注入 CHI 侧操作时:

```text
pa = buildDsmPA(tgt_node=tgt, home_node=home, offset=offset)
```

## 6. External Proxy 状态

### 6.1 Home UBCC 外部状态

| 状态 | 语义 |
|---|---|
| `G_I` | 无 sharer / owner |
| `G_S` | 存在一个或多个 sharer |
| `G_E` | 存在唯一 clean exclusive owner |
| `G_M` | 存在唯一 dirty modified owner |
| `G_BUSY` | 当前 line 有 in-flight global transaction |

每个 line 的 directory 最低字段要求:
- `line_addr`
- `state`
- `sharers_mask`
- `owner_node`
- `dirty`
- `epoch`
- `pending_op`

约束:
- home UBCC 维护的是目录/元数据，而不是缓存行真实数据本体。
- 若需要最新数据，应通过 owner recall、writeback 或当前响应路径获取，而不是假设 home UBCC 自己长期缓存 line data。
- home UBCC 必须使用 MESI 语义，`E` 与 `M` 不得合并成一个模糊 owner 态。

### 6.2 Home-side sentinel 状态

| 状态 | 位置 | 语义 |
|---|---|---|
| `S_NONE` | HN directory 中不存在 EP_RNF | 外部世界对该 line 无需本地感知 |
| `S_SHARER` | HN 原生 sharer 表达里的 `EP_RNF` 项 | 外部可能持有 clean shared copy |
| `S_OWNER` | HN 原生 owner/unique 表达里的 `EP_RNF` 项 | 外部可能持有唯一最新副本 |
| `S_PENDING` | HN 原生 transient/TBE 或最小 helper 表达里的 `EP_RNF` 项 | 正在等待 UBCC / remote 完成 |

重要说明:
- 这里的 `S_*` 首先是语义别名。
- 当前主路线不是在 HN 里额外发明一套 sentinel 专用目录格式。
- 更推荐的做法是: `EP_RNF` 在 HN directory 里尽量与普通 CPU cluster RNF 使用相同的原生 owner/sharer/transient 表达，只在 EP_RNF 控制器行为上施加额外约束。

### 6.3 Requester-side remote-line 状态

| 状态 | 语义 |
|---|---|
| `R_I` | requester 对该 remote line 无全局权限 |
| `R_WAIT_GRANT` | remote miss 已发出，等待 home decision |
| `R_S` | requester 拥有 shared 权限 |
| `R_M` | requester 拥有 unique/owner 权限 |
| `R_WAIT_ACK` | writeback / evict 已发出，等待 home ack |
| `R_WAIT_RECALL` | 正在响应 home recall / invalidate |

### 6.4 EPBackend 事务状态

| 状态 | 语义 |
|---|---|
| `TX_IDLE` | 无 in-flight 事务 |
| `TX_WAIT_HOME` | requester 请求已发给 home UBCC |
| `TX_WAIT_HN` | 正等待本地 HN 的 snoop/data/ack |
| `TX_WAIT_REMOTE` | 正等待 remote node ack/data |
| `TX_WAIT_FINISH` | 收尾阶段，等待最终 response 与清理 |

## 7. 请求转换

### 7.1 requester 读 remote DSM

路径:
1. HN 判定该 PA 属于 remote DSM。
2. HN 向 `EP_SNF` 下发 `ReadNoSnp`。
3. 在消息 sideband 中携带 `needed_perm = Shared | Unique`。
4. 在消息 sideband 中同时携带 `write_intent`，该值来自 HN-F 上层原始请求语义。
5. `EP_SNF` 读取 sideband:
   - `Shared + write_intent=false` -> `GlobalReadShared`
   - `Unique + write_intent=false` -> `GlobalReadUnique` with `GrantExclusive` expectation
   - `Unique + write_intent=true` -> `GlobalReadUnique` with `GrantModified` expectation
6. home UBCC 查目录并返回:
   - `GlobalGrantShared + GlobalData`
   - 或 `GlobalGrantExclusive + GlobalData`
   - 或 `GlobalGrantModified + GlobalData`
7. `EP_SNF` 把结果翻译回本地 CHI completion/data。

注:
- requester 侧若需要后续升级/回写等记账，该行为统一称为 `requester-side external-state bookkeeping`，不与 `sentinel registration` 混用。
- 当前最小 sideband 固定为 `needed_perm + write_intent`；`src_node/home_node` 由 PA 解析得到，不作为默认扩展字段。

### 7.2 requester 写回 remote DSM

路径:
1. HN 对 remote DSM dirty line 发生 writeback 或 evict。
2. HN 下发到 `EP_SNF`。
3. `EP_SNF` 转成 `GlobalWriteback` 或 `GlobalEvict`。
4. home UBCC 更新目录和数据。
5. home 返回 `GlobalAck`。
6. `EP_SNF` 再回复 HN。

### 7.3 home 侧 local upgrade 命中 external sharer

路径:
1. HN 发现本 line 的 sentinel 为 `S_SHARER`。
2. HN snoop `EP_RNF`。
3. `EP_RNF` 把此 snoop 转成 `GlobalInvalidate`。
4. home UBCC 等待所有 remote ack。
5. `EP_RNF` 最终答复 HN。
6. HN 才能给本地 requester unique completion。

### 7.4 home 侧 local read 命中 external owner

路径:
1. HN 发现 sentinel 为 `S_OWNER`。
2. HN snoop `EP_RNF`。
3. `EP_RNF` 转成 `GlobalRecallOwner`。
4. remote owner 返回 data，并降级或失效。
5. home UBCC 更新 directory。
6. `EP_RNF` 带 data 返回 HN。

### 7.5 home UBCC 主动对本地 domain 发 local coherent access

适用场景:
- remote read 需要本地 dirty data
- remote unique 需要使本地 sharer/owner 失效

路径:
1. UBCC 决定需要访问本地 CHI domain。
2. UBCC 用 `(target=self, home, offset)` 构造本地 PA。
3. UBCC 通过 `EP_RNF` 发起 `LocalRecallShared` / `LocalRecallUnique` / `LocalInvalidate`。
4. HN 再对本地 CPU cache 发普通 CHI snoop。
5. `EP_RNF` 收集 data/ack 回给 UBCC。

## 8. 关键不变量

- `S_OWNER` 不能与本地真实 dirty owner 共存。
- `S_SHARER` 可以与本地 clean sharer 共存。
- `S_PENDING` 期间不得提交冲突事务。
- sentinel insert 必须早于相关 CPU completion。
- sentinel remove 必须晚于 UBCC 确认外部状态已清空。
- EP path 只允许 DSM 地址。
- LocalPrivate / UbccExclusive 地址进入 EP path 必须报错。
- ordinary CHI 不得跨 node。
- 任何需要 `Shared` / `Unique` 决策的 remote miss，优先通过 HN->EP_SNF sideband 表达，而不是修改 HN 状态机。

## 9. HN 最小修改策略

推荐优先级:
1. 不改 HN 状态机。
2. 仅给发往 `EP_SNF` 的消息增加 UBCC sideband 字段。
3. 仅在 sentinel registration 点增加 hook/helper。
4. 仅在必要处增加 assert/debug。

如果第 1-3 项无法达成阶段正确性，再单独升级到更深 HN 修改，但应在阶段报告里明确记录“为什么最小修改路线不够”。

## 10. EP_RNF 表达范围异常升级规则

设计预期:
- `EP_RNF` 作为 RNF 抽象，应尽量落在 HN-F 已有 owner/sharer/transient 语义范围内。

异常条件:
- 若实现者发现 `EP_RNF` 所需状态无法被 HN-F 现有 owner/sharer/transient 组合表达。

强制动作:
1. 在仓库根目录新增 `OhNo_EP_RNF_NotGooOod.md`。
2. 该文档必须明确写出:
   - 不可表达的具体状态
   - 哪条协议路径需要该状态
   - 为什么现有 HN-F 表达不够
   - 计划引入的最小 helper 或状态扩展
3. 在文档写出前，不得把这类问题静默地“顺手扩展掉”。

允许 fallback:
- 在完成上述告警文档后，允许做最小 directory helper 扩展。
