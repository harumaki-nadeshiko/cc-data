# FV-4（v3）：Clear / RecallResp / InvalidateAck 故障恢复复核

## 范围与方法

使用 `grep + sed -n` 复核以下片段：

- `gem5/src/mem/ruby/protocol/chi/ep/UBRouter.cc:91-223`
- `gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc:421-629`
- `gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc:1058-1110, 1196-1273, 1961-2240`
- 辅助核对：`UBCCController.cc:1466-1480, 2253-2273`

## 结论

| 检查项 | 结论 | 证据 |
|---|---|---|
| tombstone replay 幂等（`Clear`） | **成立** | `processClear()` 入口先查 tombstone（`1991-1999`）；首次成功后 `retireToTombstone(*ost, true)` 并删除 outstanding（`2105-2107`）；`checkTombstone()` 命中后直接返回历史 `accepted`（`2253-2273`）。 |
| stale-epoch rejection（`RecallResp`） | **成立** | `processRecallResponse()` 先 `normalizeEpoch()`，再调 `checkEpochForLine()`；失败时 `_staleRejectedCount++` 并拒绝（`1075-1096`）。`checkEpochForLine()` 用 half-range 规则：若 committed epoch 更新，则判 stale（`1466-1480`）。 |
| stale-epoch rejection（`InvalidateAck`） | **成立** | `processInvalidationAck()` 同样先 `normalizeEpoch()`，再做 `checkEpochForLine()`，失败直接 reject（`1215-1241`）；之后还有 outstanding/opType/stage/dup ack 过滤（`1243-1273`）。 |
| stale-epoch rejection（`Clear`） | **不成立（非同类实现）** | `processClear()` **没有**调用 `checkEpochForLine()`；它做的是：先 tombstone replay（`1991-1999`），否则要求 live `GRANT_HANDSHAKE` 且 `baseEpoch/reqId/requester/stage` 精确匹配（`2009-2085`）。因此这是 **strict tuple match**，不是 half-range stale-epoch reject。 |

## 代码链路摘要

1. `UBRouter` 将 `ClearReq/RecallResp/InvalidateAck` 本地投递到 UBCC（`154-186`）。
2. `UBAdapter` 发送：
   - `ClearReq`：带 `epoch/reqId`，同步等待 `ClearResp`（`421-456`）。
   - `RecallResp`：带 `epoch/reqId`，fire-and-forget（`479-505`）。
   - `InvalidateAck`：带 `epoch/reqId`，fire-and-forget（`526-545`）。

## 关键说明

- **`Clear` 的幂等性成立**：同一 `(PA, epoch, reqId)` 在 tombstone 窗口 `W` 内会重放历史 `accepted`，不会再次提交目录。
- **`RecallResp` / `InvalidateAck` 的 stale reject 成立**：两者都走 `checkEpochForLine()` 的 half-range 校验。
- **`Clear` 不是 half-range stale reject**：它只接受 tombstone replay 或当前 live grant 的精确 tuple；所以“验证 stale-epoch rejection”这一项对 `Clear` 的准确表述应为：**不存在与 `RecallResp/InvalidateAck` 同类的 stale-epoch reject 机制**。
- **额外风险点**：`Clear` 的 `epoch mismatch` 分支会 `retireToTombstone(*ost, false)` 并 `removeOutstanding(line_pa)`（`2034-2050`）；这表示它不只是拒绝该旧 `Clear`，还会退休当前 live `GRANT_HANDSHAKE`。

## 最终判定

- **tombstone replay idempotent**：**是**。
- **stale-epoch rejection / RecallResp**：**是**。
- **stale-epoch rejection / InvalidateAck**：**是**。
- **stale-epoch rejection / Clear**：**否（仅 strict tuple match，不是 half-range stale reject）**。
