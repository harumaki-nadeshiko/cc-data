# Stage State Tables

本文件为 `M4 ~ M7` 提供更细的状态转移表与事件-动作-结果定义。

使用方式:
- implementer 用于明确每阶段应该实现哪些状态变化
- reviewer 用于核对是否真正落地到状态机层面
- orchestrator 用于向 implementer/reviewer 派发更明确的任务

术语基线:
- `sentinel registration` 只指 home-side HN directory 对 `EP_RNF` 的 insert/update/remove
- `requester-side external-state bookkeeping` 指 requester 侧对 remote line 的本地记账
- home UBCC 状态使用 `MESI`

## 0. M3.5 Flow Table

### 0.1 Multi-Agent Collaboration Flow

| Step | Actor | Event | Required Action | Next |
|---|---|---|---|---|
| 1 | orchestrator | start `M3.5` | call implementer for single-file modification | wait-implementer |
| 2 | implementer | receives `M3.5` task | append `Agent test 666!` to root `readme.md` and report result | wait-validator |
| 3 | orchestrator | implementer returned | call validator to inspect `readme.md` | wait-validator |
| 4 | validator | checks `readme.md` | PASS iff line exists | wait-user-confirm |
| 5 | orchestrator | validator PASS | stop automatic progression and wait for user confirmation | paused |

Forbidden behavior:
- orchestrator skips implementer
- orchestrator skips validator
- orchestrator auto-continues to `T0`/`M4` after `M3.5` PASS

## 1. M4 State Tables

### 1.1 Home-Side Sentinel Semantic States

| Semantic State | HN 原生目录承载方式 | 含义 | 必须保持的不变量 |
|---|---|---|---|
| `S_NONE` | `EP_RNF` 不在 sharers/owner 中 | external world 对该 line 无需 home HN 感知 | 本地 HN 不应因 external world 额外 snoop `EP_RNF` |
| `S_SHARER` | `EP_RNF` 在 sharers 集合中，owner 不为 `EP_RNF` | external world 存在 clean shared copy | 本地 unique/upgrade 必须 snoop `EP_RNF` |
| `S_OWNER` | `EP_RNF` 进入 HN 原生 owner/unique 承载位 | external world 为唯一最新 owner | 不得与本地 dirty owner 共存 |
| `S_PENDING` | HN 原生 transient/TBE 或最小 helper 表达中含 `EP_RNF` pending | 正在等待 UBCC/remote 完成 | 冲突事务不得越过 pending 状态提交 |

### 1.2 Home-Side Sentinel Registration Transition Table

| Current | Event | Guard | Actions | Next |
|---|---|---|---|---|
| `S_NONE` | remote `GrantShared` 完成 | home line 属于本 node DSM | 在 HN directory 插入 `EP_RNF` 为 sharer | `S_SHARER` |
| `S_NONE` | remote `GrantExclusive/Modified` 完成 | home line 属于本 node DSM，且本地 owner 已回收 | 在 HN directory 以原生 owner/unique 方式安装 `EP_RNF` | `S_OWNER` |
| `S_SHARER` | 新 remote sharer 加入 | `EP_RNF` 已存在 | 无需重复安装；只更新 home UBCC sharer metadata | `S_SHARER` |
| `S_SHARER` | external world 升级成唯一 owner | 所有其他 external sharer 已清空，且本地 owner 不存在 | 将 `EP_RNF` 从原生 sharer 承载切换到原生 owner 承载 | `S_OWNER` |
| `S_OWNER` | owner 降级为 shared | remote read/recall 流程完成 | 将 `EP_RNF` 从 owner 承载降级到 sharer 承载 | `S_SHARER` |
| `S_SHARER` | external world 完全清空 | home UBCC 确认无外部 sharer/owner | 从 HN directory 删除 `EP_RNF` | `S_NONE` |
| `S_OWNER` | external world 完全清空 | home UBCC 确认无外部 sharer/owner | 从 HN directory 删除 `EP_RNF` | `S_NONE` |
| `S_SHARER` or `S_OWNER` | 需等待 remote 完成的本地冲突事务开始 | 事务未完成 | 进入 pending 承载 | `S_PENDING` |
| `S_PENDING` | remote/UBCC 事务完成 | 成功提交 | 根据事务结果恢复为 `S_SHARER`/`S_OWNER`/`S_NONE` | result-dependent |

### 1.3 HN Local Request vs Sentinel Behavior Table

| Local HN Event | Sentinel State | Required Behavior |
|---|---|---|
| local read hit/miss | `S_NONE` | 不因 external world 额外 snoop `EP_RNF` |
| local read while external owner exists | `S_OWNER` | HN 必须 snoop `EP_RNF`，并等待 `EP_RNF` 经 UBCC/remote 完成 recall |
| local unique/upgrade | `S_SHARER` | HN 必须 snoop `EP_RNF`，`EP_RNF` 触发 global invalidate |
| local unique/upgrade | `S_OWNER` | HN 必须 snoop `EP_RNF`，`EP_RNF` 触发 owner recall / invalidation |
| 任意本地冲突事务 | `S_PENDING` | 不得越过 pending，必须等待或 retry |

### 1.4 M4 Reviewer Checkpoints

- `EP_RNF` 是否用 HN 原生 sharer/owner 表达，而不是平行 shadow 结构
- sentinel install 是否发生在 remote grant 对 requester 可见之前
- `S_OWNER` 是否与本地 dirty owner 互斥
- `S_PENDING` 是否真的阻止冲突事务越过

## 2. M5 State Tables

### 2.1 Requester-Side Sideband Decision Table

| Upper Request Semantics | `ubcc_needed_perm` | `ubcc_write_intent` | Expected Outer Request | Expected Grant |
|---|---|---:|---|---|
| pure load on remote DSM | `Shared` | `false` | `GlobalReadShared` | `GlobalGrantShared` |
| read-for-ownership / unique but not immediate write | `Unique` | `false` | `GlobalReadUnique` | `GlobalGrantExclusive` |
| store / write-intent miss | `Unique` | `true` | `GlobalReadUnique` | `GlobalGrantModified` |

非法组合:
- `Shared + true`

### 2.2 Requester-Side Bookkeeping State Table

| Current | Event | Guard | Actions | Next |
|---|---|---|---|---|
| `R_I` | remote miss issued | line is remote DSM | allocate requester transaction context | `R_WAIT_GRANT` |
| `R_WAIT_GRANT` | receive `GlobalGrantShared + Data` | sideband=`Shared,false` | install local shared bookkeeping | `R_S` |
| `R_WAIT_GRANT` | receive `GlobalGrantExclusive + Data` | sideband=`Unique,false` | install local exclusive bookkeeping | `R_M` equivalent owner-capable local bookkeeping |
| `R_WAIT_GRANT` | receive `GlobalGrantModified + Data` | sideband=`Unique,true` | install local modified owner bookkeeping | `R_M` |
| `R_S` | local upgrade/store | local path requires write | trigger new outer txn via `EP_SNF` | `R_WAIT_GRANT` or transition-specific pending |
| `R_M` | local writeback/evict begins | dirty or clean | emit `GlobalWriteback` or `GlobalEvict` | `R_WAIT_ACK` |
| `R_WAIT_ACK` | receive home ack | ack valid and epoch matches | clear or downgrade local bookkeeping | `R_I` or result-dependent |

说明:
- `R_M` 是 requester 侧“拥有 unique/owner 类权限”的统一记账态。
- 是否细分本地 `R_E` / `R_Md` 取决于实现是否需要在 requester 侧显式区分 clean exclusive 与 dirty modified。
- 但 home UBCC 必须严格区分 `G_E` 和 `G_M`。

### 2.3 Home UBCC MESI Grant Decision Table

| Current Home State | Incoming Request | Guard | Response | Next Home State |
|---|---|---|---|---|
| `G_I` | `GlobalReadShared` | no owner/no sharer | `GlobalGrantShared + Data` | `G_S` with requester in sharers |
| `G_I` | `GlobalReadUnique` with `write_intent=false` | no owner/no sharer | `GlobalGrantExclusive + Data` | `G_E` with requester as owner |
| `G_I` | `GlobalReadUnique` with `write_intent=true` | no owner/no sharer | `GlobalGrantModified + Data` | `G_M` with requester as owner |
| `G_S` | `GlobalReadShared` | sharers exist, no owner | `GlobalGrantShared + Data` | `G_S` add requester to sharers |
| `G_S` | `GlobalReadUnique` with `write_intent=false/true` | invalidate sharers required | delay response until invalidations complete | `G_E` or `G_M` |
| `G_E` | `GlobalReadShared` | existing exclusive owner clean | recall/downgrade owner to shared | `G_S` |
| `G_E` | `GlobalReadUnique` | existing owner may transfer | owner transfer / invalidate as needed | `G_E` or `G_M` |
| `G_M` | `GlobalReadShared` | existing dirty owner | `GlobalRecallOwner` then data response | `G_S` |
| `G_M` | `GlobalReadUnique` | existing dirty owner | `GlobalRecallOwner` / invalidation / owner transfer | `G_E` or `G_M` |

### 2.4 M5 Reviewer Checkpoints

- `needed_perm + write_intent` 是否真的从 HN 上层语义传到 `EP_SNF`
- 是否仍偷偷依赖 `force_grant_m`
- 是否存在 `Shared + true` 的非法组合处理
- home MESI 是否严格区分 `E` 与 `M`

## 3. M6 State Tables

### 3.1 `GlobalRecallOwner` Main Path Table

| Step | Actor | Event | Required Action | Observable |
|---|---|---|---|---|
| 1 | home UBCC | incoming read/unique conflicts with existing owner | allocate active txn, mark line busy | `G_BUSY` or txn pending state |
| 2 | home UBCC | owner is remote | send `GlobalRecallOwner` to owner node | outer message log |
| 3 | owner node `EP_RNF`/backend | receives recall | trigger local coherent access via HN | `EP_RNF` local recall hook/log |
| 4 | owner HN | snoops local cache/domain | gather latest data and permission downgrade/invalidations | local HN debug/log |
| 5 | owner `EP_RNF` | receives local result | return data/ack to home UBCC | `EP_RNF` response log |
| 6 | home UBCC | receives data/ack | update directory and complete pending requester txn | dir state snapshot |
| 7 | home/requester | final response | requester receives data/grant | requester completion observable |

### 3.2 EP_RNF Response Ordering Table

| Current | Event | Required Behavior | Next |
|---|---|---|---|
| idle | HN snoops `EP_RNF` and outer txn required | do not immediately answer HN; allocate pending response context | wait-for-outer |
| wait-for-outer | outer txn not done | retain pending | wait-for-outer |
| wait-for-outer | outer txn done and result ready | send final response/data to HN | idle |

Forbidden behavior:
- `EP_RNF` sends final HN response before outer txn completion

### 3.3 Home UBCC Directory Invariants After M6

| State | Required Fields |
|---|---|
| `G_S` | `owner_node` invalid, `dirty=false`, `sharers_mask.count>=1` |
| `G_E` | `owner_node` valid, `dirty=false`, exclusive owner unique |
| `G_M` | `owner_node` valid, `dirty=true`, modified owner unique |

## 4. M7 State Tables

### 4.1 Writeback / Evict Transition Table

| Current Home State | Incoming Event | Guard | Actions | Next |
|---|---|---|---|---|
| `G_M` | `GlobalWriteback` from owner | epoch matches | update metadata, clear/adjust owner | `G_I`/`G_S`/`G_E` result-dependent |
| `G_S` | `GlobalEvict` from sharer | sharer in mask | remove sharer | `G_S` or `G_I` |
| `G_E` | clean owner evict | owner matches and clean | clear owner | `G_I` |
| `G_M` | dirty owner transfer begins | competing unique/write request | recall old owner, install new owner | `G_E` or `G_M` |

### 4.2 Owner Transfer Table

| Old State | Request | Intermediate Actions | New State |
|---|---|---|---|
| `G_E` owner=A | `GlobalReadUnique` from B, `write_intent=false` | recall/transfer ownership from A to B cleanly | `G_E` owner=B |
| `G_E` owner=A | `GlobalReadUnique` from B, `write_intent=true` | recall/transfer ownership from A to B with write intent | `G_M` owner=B |
| `G_M` owner=A | `GlobalReadShared` from B | recall owner A, downgrade A to sharer, add B sharer | `G_S` |
| `G_M` owner=A | `GlobalReadUnique` from B | recall/invalidate A, transfer ownership to B | `G_E` or `G_M` owner=B |

### 4.3 Epoch / Stale Response Table

| Current Epoch | Incoming Response Epoch | Required Action |
|---|---|---|
| `N` | `N` | accept if transaction context matches |
| `N` | `< N` | reject as stale, do not mutate state |
| `N` | `> N` | reject unless implementation explicitly supports forward epoch creation |

### 4.4 Recall Result Split Table

| Trigger | Old Owner Result | New Semantics |
|---|---|---|
| remote read recalls owner | old owner downgraded to shared | `G_S` or requester/home state consistent with shared readers |
| remote unique/write recalls owner | old owner invalidated | requester becomes new owner with `G_E` or `G_M` |

## 5. Cross-Stage Forbidden States

以下状态在 `M4 ~ M7` 任何阶段都应被视为非法:

| Illegal State | Why Illegal |
|---|---|
| local dirty owner and `EP_RNF` owner coexist on same line | violates single-owner invariant |
| home UBCC keeps only one generic owner state without `E/M` split | violates MESI requirement |
| requester receives grant before home-side sentinel registration becomes visible | local home HN may miss external world |
| home UBCC returns latest data from a permanent local copy instead of recall/writeback path | violates metadata-only home design |
| `EP_RNF` requires a fully separate parallel directory structure in HN without `OhNo_EP_RNF_NotGooOod.md` | violates current representation policy |
