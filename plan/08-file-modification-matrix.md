# File Modification Matrix

本文件列出 `T0` 与 `M4 ~ M7` 的建议修改文件集合、文件职责、修改边界与 reviewer 重点。

说明:
- 这是“建议最小修改面”，不是绝对完整列表。
- implementer 若偏离该列表，应在阶段报告里解释原因。

## 0. M3.5 - Multi-agent Collaboration Smoke Check

### 0.1 Likely Modify

| File | Role | Expected Change |
|---|---|---|
| `readme.md` | smoke target | 新增一行 `Agent test 666!` |

### 0.2 Reviewer Focus

- 是否只改了 `readme.md`
- 是否确实新增了目标文本
- `M3.5` 通过后 orchestrator 是否暂停等待用户确认

## 1. T0 - Sync_Wait

### 1.1 Likely Modify

| File | Role | Expected Change |
|---|---|---|
| `gem5/src/arch/arm/linux/se_workload.cc` | ARM SE syscall registration | 注册自定义 syscall 号与 handler |
| `gem5/src/sim/syscall_desc.hh` | syscall 描述 | 增加 `Sync_Wait` 描述 |
| `gem5/src/sim/system.hh` | system-visible state | 挂载 barrier manager 或全局 sync state |
| `gem5/src/sim/system.cc` | system state wiring | 初始化/销毁 sync state |
| `gem5/src/sim/sync_wait.hh` | new helper | barrier manager 声明 |
| `gem5/src/sim/sync_wait.cc` | new helper | barrier manager 实现 |

### 1.2 Likely Add Tests

| File | Purpose |
|---|---|
| `tests/sync_wait/` 下 workload 源码 | barrier 行为验证 |
| `tests/sync_wait/test_sync_wait.py` | Python/gem5 驱动脚本 |

## 2. M4 - Sentinel Registration

### 2.1 Must Inspect

| File | Why |
|---|---|
| `gem5/src/mem/ruby/protocol/chi/CHI-cache.sm` | HN directory 数据结构与状态字段 |
| `gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm` | directory 更新、snoop 发起、owner/sharer 维护 |
| `gem5/src/mem/ruby/protocol/chi/CHI-cache-funcs.sm` | TBE/dir helper、断言、不变量 |
| `gem5/src/mem/ruby/protocol/chi/CHI-cache-transitions.sm` | HN 事务路径与最终提交点 |
| `gem5/src/mem/ruby/protocol/chi/CHI-cache-ports.sm` | request/snoop port 路径 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.hh/.cc` | `EP_RNF` snoop 接收与延迟响应框架 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh/.cc` | home-side sentinel bookkeeper glue |
| `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh/.cc` | home metadata 与 install/remove 时机 |

### 2.2 Likely Modify

| File | Expected Change |
|---|---|
| `CHI-cache.sm` | 确认/最小扩展原生 owner/sharer/transient 承载是否足够表示 `EP_RNF` |
| `CHI-cache-actions.sm` | 增加 sentinel insert/update/remove hook 点 |
| `CHI-cache-funcs.sm` | 增加 helper：检查 `EP_RNF` 是否在原生 directory 中；断言 single-owner invariant |
| `EPRNFController.cc` | 支持 pending response context、测试 hook、snoop tracking |
| `EPBackend.cc` | home-side sentinel management API |
| `UBCCController.cc` | remote grant 完成前触发 home-side sentinel registration |

### 2.3 Likely Add Tests

| File | Purpose |
|---|---|
| `tests/phase4/test_sentinel_registration.py` | `S_SHARER/S_OWNER/S_PENDING` 基线验证 |
| `tests/phase4/test_sentinel_negative.py` | non-DSM / coexistence 负例 |

### 2.4 Reviewer Focus

- 是否真的使用 HN 原生目录格式承载 `EP_RNF`
- 是否在 grant 可见前完成 sentinel registration
- 是否没有偷偷引入并行 sentinel shadow 结构

## 3. M5 - Remote Miss With Permission Sideband

### 3.1 Must Inspect

| File | Why |
|---|---|
| `gem5/src/mem/ruby/protocol/chi/CHI-msg.sm` | `CHIRequestMsg` 扩展 sideband 字段 |
| `gem5/src/mem/ruby/protocol/chi/CHI-cache*.sm` | HN 上层请求语义提取点 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.hh/.cc` | 读取 sideband 并产生 outer request |
| `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh/.cc` | requester txn context |
| `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.hh/.cc` | home MESI grant 决策 |
| `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.py` | 若新增消息/参数暴露需要同步 |

### 3.2 Likely Modify

| File | Expected Change |
|---|---|
| `CHI-msg.sm` | 增加 `ubcc_needed_perm`, `ubcc_write_intent` |
| `CHI-cache-actions.sm` or related HN path files | 在发往 `EP_SNF` 的 remote DSM 路径填充 sideband |
| `EPSNFController.cc` | sideband -> `GlobalReadShared/Unique` / expected grant logic |
| `EPBackend.cc` | requester bookkeeping 与 line context |
| `UBCCController.cc` | `G_I/G_S/G_E/G_M` grant decision |

### 3.3 Likely Add Tests

| File | Purpose |
|---|---|
| `tests/phase5/test_sideband_plumbing.py` | 验证 `needed_perm + write_intent` |
| `tests/phase5/test_remote_first_miss.py` | shared/exclusive/modified grant 基线 |
| `tests/phase5/test_sideband_negative.py` | 非法组合与冗余字段负例 |

### 3.4 Reviewer Focus

- 是否只增加了最小字段 `needed_perm + write_intent`
- 是否把 `E/M` 正确区分为 `GrantExclusive/GrantModified`
- 是否仍偷偷依赖 `force_grant_m`

## 4. M6 - UBCC Directory + EP_RNF Local Coherent Access

### 4.1 Must Inspect

| File | Why |
|---|---|
| `UBCCController.hh/.cc` | global directory, active txn, recall path |
| `EPBackend.hh/.cc` | owner/home/requester context glue |
| `EPRNFController.hh/.cc` | delayed HN response, local recall path |
| `CHI-cache-actions.sm` | HN 本地 recall/snoop 行为 |
| `CHI-cache-funcs.sm` | dir/TBE asserts, owner/sharer consistency |

### 4.2 Likely Modify

| File | Expected Change |
|---|---|
| `UBCCController.hh` | `DirEntry`、txn context、epoch 字段初版 |
| `UBCCController.cc` | `GlobalRecallOwner`、MESI directory update |
| `EPRNFController.cc` | local coherent access trigger、延迟响应 HN |
| `EPBackend.cc` | route recall/data/ack between UBCC and EP_RNF |
| `CHI-cache-actions.sm` | 若需要最小 hook：确保 owner recall 经 HN 正确取数 |

### 4.3 Likely Add Tests

| File | Purpose |
|---|---|
| `tests/phase6/test_recall_owner.py` | recall 路径 |
| `tests/phase6/test_directory_mesi.py` | `G_S/G_E/G_M` 检查 |
| `tests/phase6/test_ep_rnf_delay.py` | `EP_RNF` 延迟响应 |

### 4.4 Reviewer Focus

- remote read dirty line 是否通过 recall 拿到最新值
- home UBCC 是否仍 metadata-only
- `EP_RNF` 是否没有提前答复 HN

## 5. M7 - Writeback / Evict / Owner Transfer

### 5.1 Must Inspect

| File | Why |
|---|---|
| `UBCCController.hh/.cc` | writeback/evict/owner transfer/epoch |
| `EPSNFController.hh/.cc` | requester writeback/evict 发起 |
| `EPBackend.hh/.cc` | requester/home state update |
| `EPRNFController.hh/.cc` | owner recall 后状态变化 |

### 5.2 Likely Modify

| File | Expected Change |
|---|---|
| `UBCCController.cc` | `GlobalWriteback`, `GlobalEvict`, owner transfer, stale filtering |
| `EPSNFController.cc` | requester dirty writeback / clean evict -> outer messages |
| `EPBackend.cc` | epoch propagation, ack handling |
| `EPRNFController.cc` | recall result split: downgrade-to-shared vs invalidate |

### 5.3 Likely Add Tests

| File | Purpose |
|---|---|
| `tests/phase7/test_writeback.py` | dirty writeback |
| `tests/phase7/test_clean_evict.py` | sharer mask 清理 |
| `tests/phase7/test_owner_transfer_pingpong.py` | single-owner invariant |
| `tests/phase7/test_epoch_stale.py` | stale ack/data |

### 5.4 Reviewer Focus

- 是否任意时刻最多一个 global owner
- stale epoch 是否真的被过滤
- home 是否仍 metadata-only

## 6. Test Hook / Inspection API Suggestions

### 6.1 Suggested C++ Test Hooks

| Hook | Likely Home |
|---|---|
| `installSentinelForTest` | HN glue or `EPBackend`/`UBCCController` test facade |
| `removeSentinelForTest` | same as above |
| `inspectDirEntryForTest` | HN/dir-facing helper |
| `inspectUbccDirForTest` | `UBCCController` |
| `inspectRequesterStateForTest` | requester-side `EPBackend` |
| `inspectEpochForTest` | `UBCCController` / txn context |

### 6.2 Suggested Python Drivers

| Driver File | Purpose |
|---|---|
| `tests/phase4/*.py` | strong-injection M4 checks |
| `tests/phase5/*.py` | sideband and first-miss checks |
| `tests/phase6/*.py` | recall / MESI state checks |
| `tests/phase7/*.py` | writeback / owner transfer / stale checks |

## 7. Reviewer Red Flags

implementer 若出现以下现象，应重点质疑:

- 在 HN 外再造一套 parallel sentinel state store
- 用 Python shadow state 代替 C++ 真实状态读取
- 用 helper 直接把 testcase 末态写成 PASS
- 在 `UBCCController` 中引入永久 line data store 以绕开 recall/writeback
- 在 `gem5` submodule 改了关键协议代码，却没有独立 submodule commit
