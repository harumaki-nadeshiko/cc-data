# TC134 v5-o3 Grant Resident Pin Loss 临时修复

## 基线

- 远端分支：`origin/v5-o3`
- 基线提交：`3dcaaa39478c35c3a3dc4cf99c40e617d0bcb3fb`
- 基线标题：`fix: defer saturated H64 probe retries`
- 补丁文件：`docs/issues/tc134_v5_o3_grant_resident_pin_loss_fix.patch`

## 现场现象

TC134 spill 在 node3 `window_pressure` 开始阶段停滞：

- `PA=0x11180000`
- node3/socket0 发出 write-intended `ReadUnique`
- Home0 从 committed `G_I` 生成 intended `G_M`
- `[UBCC-GRANT-READY]` 显示 `baseEpoch=4097`、`reservedEpoch=1`
- Home0 已发送 `ReadResp`
- gem5 node3 随后持续重试 matching `Clear(epoch=4097)`
- Home0 报告 `stale Clear for unknown PA=0x11180000 - dropped`

这不是本次路径上的 epoch mismatch。`processClear()` 在校验 outstanding epoch 前，已经因为 ResidentDir lookup 失败而提前返回。

## 根因

`processOuterRequest()` 创建 `GRANT_HANDSHAKE` 后没有调用 `refreshPinnedBit(line_pa)`。Clear 提交前，ResidentDir 中仍是 committed `G_I`；在 set-local pressure 下，该条目可能被当作未 pinned 的 clean invalid victim 直接删除，但对应 outstanding 仍处于 `WAITING_CLEAR`。

结果是：

```text
create GRANT_HANDSHAKE, intended G_M
-> send ReadResp
-> resident entry remains unpinned committed G_I
-> pressure evicts resident entry
-> outstanding remains WAITING_CLEAR
-> matching Clear sees unknown PA
-> Clear is dropped and retried forever
```

## 与昨晚补丁的关系

昨晚提供的 `tc134_outstanding_allocation_waiter_loss_hotfix.patch` 没有解决这次问题。

昨晚补丁处理的是另一条失败路径：outstanding table allocation 失败后仍返回 grant，以及 resident waiter replay 将 BUSY 错判为同步完成。它没有在成功创建 `GRANT_HANDSHAKE` 后同步 ResidentDir pinned bit，也没有阻止 active outstanding 对应的 resident entry 被选作 victim。因此，即使昨晚补丁已应用，本次 `ReadResp -> Clear` 之间的 resident entry 丢失仍然会发生。

## 修复内容

1. immediate grant 成功创建 outstanding 后调用 `refreshPinnedBit(line_pa)`。
2. `evictOneVictim()` 选出 victim 后复核 `_outstandingReqs`，禁止删除 active transaction 对应条目。
3. `processClear()` 在 unknown-line 路径检查 matching `GRANT_HANDSHAKE`；若 matching grant 的 resident entry 已丢失则立即 fatal，避免伪装成普通 stale Clear 并无限重试。

## 应用

```bash
git fetch origin v5-o3 doc
git switch v5-o3
git pull --ff-only origin v5-o3
git show origin/doc:docs/issues/tc134_v5_o3_grant_resident_pin_loss_fix.patch > /tmp/tc134-pin-loss.patch
git apply --check /tmp/tc134-pin-loss.patch
git apply /tmp/tc134-pin-loss.patch
```

应用后重新构建 UBIO，并重新运行 TC134 spill。

## 预期

- `GRANT_HANDSHAKE/WAITING_CLEAR` 存活期间，该 PA 的 resident entry 不再被逐出。
- matching Clear 能通过 resident lookup，并继续执行 reqId、epoch 和 requester tuple 校验。
- 不再出现该 tuple 对应的 `stale Clear for unknown PA` 无限重试。
- 若其他路径再次造成 outstanding 与 ResidentDir 分离，会立即触发 `matching Clear lost resident entry` fatal，保留明确根因。
