# Validator Checklists

本文件把 validator/reviewer 的审查动作细化成逐项清单，避免只给笼统 PASS/FAIL。

## 1. 通用审查清单

validator 对任意阶段至少检查:

| Item | Check |
|---|---|
| C1 | 是否修改了本阶段需要的最小文件集合 |
| C2 | 是否同步更新了 testcase |
| C3 | 是否同步更新了必要文档/计划引用 |
| C4 | 是否真实运行本阶段测试 |
| C5 | 是否真实运行 `TC1..TC5` 回归 |
| C6 | 是否存在 bypass/hardcoded PASS/伪测试 |
| C7 | 若改了 `gem5/`，是否存在独立 submodule commit |
| C8 | 若出现 API 中断，是否正确落盘 checkpoint |

## 1.5 M3.5 Checklist

| Item | Check |
|---|---|
| M3.5-1 | implementer 是否确实修改了根目录 `readme.md` |
| M3.5-2 | `readme.md` 是否存在 `Agent test 666!` |
| M3.5-3 | orchestrator 是否先调用 implementer，再调用 validator |
| M3.5-4 | `M3.5` PASS 后 orchestrator 是否暂停等待用户确认 |

## 2. T0 Checklist

| Item | Check |
|---|---|
| T0-1 | `Sync_Wait` 只统计显式调用线程 |
| T0-2 | barrier 可重复使用 |
| T0-3 | 不同 `node_mask` 互不干扰 |
| T0-4 | 未调用线程不被错误计入 |

## 3. M4 Checklist

| Item | Check |
|---|---|
| M4-1 | `EP_RNF` 是否在 HN 原生目录格式中可观测 |
| M4-2 | 是否真的存在 `S_SHARER` install/remove |
| M4-3 | 是否真的存在 `S_OWNER` install |
| M4-4 | local unique/upgrade 是否真实 snoop `EP_RNF` |
| M4-5 | sentinel registration 是否在 grant 可见前完成 |
| M4-6 | `S_OWNER` 与本地 dirty owner 是否互斥 |
| M4-7 | non-DSM sentinel 是否被拒绝 |
| M4-8 | 是否偷偷引入平行 sentinel shadow 结构 |

## 4. M5 Checklist

| Item | Check |
|---|---|
| M5-1 | `CHIRequestMsg` 是否新增 `ubcc_needed_perm` |
| M5-2 | `CHIRequestMsg` 是否新增 `ubcc_write_intent` |
| M5-3 | sideband 是否来自 HN 上层语义 |
| M5-4 | 是否拒绝 `Shared + true` 非法组合 |
| M5-5 | `GlobalGrantShared/Exclusive/Modified` 是否可区分 |
| M5-6 | home UBCC 是否使用 `G_S/G_E/G_M` 而非模糊 owner |
| M5-7 | 是否只增加了最小字段，没有塞 `src/home` 冗余字段 |
| M5-8 | 是否仍偷偷依赖 `force_grant_m` 作为默认主路线 |

## 5. M6 Checklist

| Item | Check |
|---|---|
| M6-1 | `UBCCController` 是否有可观测 `DirEntry` |
| M6-2 | `GlobalRecallOwner` 是否真实发出 |
| M6-3 | owner data 是否经 `EP_RNF` -> HN 路径取回 |
| M6-4 | `EP_RNF` 是否延迟响应 HN |
| M6-5 | remote read dirty line 是否读到最新值 |
| M6-6 | home UBCC 是否仍 metadata-only |
| M6-7 | `G_E` / `G_M` 是否严格可区分 |

## 6. M7 Checklist

| Item | Check |
|---|---|
| M7-1 | dirty writeback 是否正确更新后续可观察结果 |
| M7-2 | clean evict 是否正确更新 sharer mask |
| M7-3 | owner transfer 后是否任意时刻最多一个 owner |
| M7-4 | stale epoch 是否被过滤 |
| M7-5 | recall 结果是否按 read/write 分裂 |
| M7-6 | home UBCC 是否仍 metadata-only |

## 7. 审查输出格式

validator 输出建议固定为:

```md
# <STAGE> Validation Result

- Verdict: PASS | FAIL | INCOMPLETE

## Checked Items
- [PASS/FAIL] <item>

## Must Fix
- ...

## Optional Suggestions
- ...

## Continue Decision
- proceed_to_next_stage: yes/no
```

## 8. 未完成与计划缺陷的审查规则

若 implementer 上报 `未完成`:
- validator 必须区分是实现尚未做完，还是计划本身不充分

若 implementer 上报 `计划缺陷`:
- validator 必须审查论据是否成立
- 若成立，应要求先修订计划，再决定是否重试本阶段

若 implementer 上报 `阶段未完成`:
- validator 必须明确给出:
  - 是否允许继续后续阶段
  - 若不允许，下一次应从哪一步恢复

默认规则:
- 没有 validator 明确 PASS，不允许推进下一阶段
