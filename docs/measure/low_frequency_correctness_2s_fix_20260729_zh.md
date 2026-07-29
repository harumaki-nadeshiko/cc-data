# 低频正确性队列与 2S 修复复测报告

> 日期：2026-07-29
> 环境：`ubcc-dev:ubuntu20.04`，Docker `--network none`
> Stall 门限：`EP_SUPERVISOR_PROGRESS_STALL_SEC=600`，未放宽

## 1. 原始队列结果

低频队列共 64 个 `TC/profile/topology` 目标。原始追加式记录位于：

- `logs/low_frequency_correctness_20260729/targets.tsv`
- `logs/low_frequency_correctness_20260729/matrix.tsv`
- `logs/low_frequency_correctness_20260729_runner.log`

去重后的原始最终结果为 59 PASS、5 FAIL。失败均为 2S：

| TC | 原始结果 | 现象 |
|---:|---|---|
| 32 | FAIL | 初次运行 tick=0 stall；bootstrap 修复后暴露 recall outstanding 过早清理 |
| 34 | FAIL | tick=0 stall |
| 35 | FAIL | 协议持续推进，但 recall outstanding 过早清理后 requester 无法完成 |
| 39 | FAIL | tick=0 stall |
| 81 | FAIL | tick=0 stall |

## 2. 根因

### 2.1 Tick=0 启动竞态

`framework::Port::emitSync()` 原来通过普通阻塞 send 发送 `CONTROL_SYNC`。ZMQ peer 尚未 bind 时，首次 heartbeat 可无限阻塞 gem5 event loop。2S 需要启动 6 个 UBIO，竞态窗口显著大于 1S。

证据特征：

```text
alive=3/3
guest_bytes=0
protocol_tick=0
progress_stall=600s
```

TC33 在相同 `topo_2s.json` 下能够通过，说明拓扑连接定义本身正确，只是启动时序具有偶然性。

### 2.2 Recall 虚拟超时过短

原 `_recallTimeout=1,000,000` protocol ticks。TC32 的 socket1 recall 日志显示：

1. Home node0/socket1 为 `PA=0x18006040` 建立 reqId=6 recall。
2. 在约 1,001,000 ticks 时执行 `expired RECALL cleanup`，删除 outstanding。
3. owner node1 随后返回成功、带数据的 RecallResp。
4. RecallResp 因 outstanding 已删除而成为 orphan，不能生成最终 ReadResp。

TC35 reqId=73 是相同模式。正常 Outer 长尾已有接近 10M ticks 的样本，因此 1M 门限低于合法 2S RTT。

### 2.3 Socket-plane 响应字段不完整

部分 response 没有显式设置 `srcSocket/homeSocket`，本地 response `targetId` 使用 bare node id 而不是：

```text
gid = node * numSockets + socket
```

点对点 endpoint 当前不依赖该 header 完成物理投递，但它会破坏 2S 路由一致性和后续 target 过滤语义。

## 3. 修复

- `framework/Port.cc`
  - 仅将 `CONTROL_SYNC` heartbeat 改为 non-blocking `dontwait`。
  - coherence/data message 仍使用阻塞发送和 HWM backpressure。
  - heartbeat 发送失败时不更新 `_lastSyncTs`，后续 event loop 会继续重试。
- `modules/ubiomodule/UBCCController.hh/.cc`
  - recall protocol timeout 调整为 10M ticks。
  - 普通 requester recall 和 capacity recall 都保留同一 reqId/epoch tuple，最多精确重试 3 次。
  - 不再删除活跃 recall 后把延迟 RecallResp 变成 orphan。
  - push grant 发送结果稳定记录；deferred UpgradeResp 发送失败时 fail-closed。
- `modules/ubiomodule/ubio_main.cc`
  - Read/Writeback/Evict/Upgrade/Clear/Query response 补齐 socket 字段。
  - 本地 gem5 response 和 control push 使用 socket-plane gid。
  - 无数据 push 使用固定 32 槽 `PendingGrantRead` 表，不新增无界 PA map。
  - 仅 `NotWritten` 可发送 no-data；`RetryableBusy` 有界重试；`IoError` 不伪造 payload。
  - gem5 已退出且无 authoritative recall data 时 fatal，不再发送 `HAS_DATA+zero`。
- `gem5/.../UBAdapter.cc`
  - 增加每个 socket-plane 的 startup port 诊断。
  - MetaRNF line response 返回原始 requester node/socket。

Wall-clock supervisor stall 门限保持 600 秒，没有通过延长 stall 掩盖问题。

## 4. 2S 修复后复测

| TC | 场景 | 修复后结果 | 证据目录 |
|---:|---|---|---|
| 32 | cross-socket read miss | PASS | `logs/tc2s_actual_gem5_tc32/` |
| 33 | cross-socket dirty writeback sanity | PASS | `logs/tc2s_fix_tc33_sanity/` |
| 34 | dual-socket pingpong | PASS | `logs/tc2s_fix_final_34_39_81/` |
| 35 | NUMA mixed stress | PASS | `logs/tc2s_fix_final_tc35/` |
| 39 | dual-socket same-PA interference | PASS | `logs/tc2s_fix_final_34_39_81/` |
| 81 | cross-socket latency correctness | PASS | `logs/tc2s_fix_final_34_39_81/` |

TC32 final read 和 latency marker 正确：

```text
TC32 PASSED: cross-socket read valid (same=1351, cross=1603)
```

TC81 correctness 通过，但本轮 same/cross timer 均为 0，属于当前 counter 分辨率限制，不作为有效性能样本。

## 5. 后续队列重测

| TC/profile | 结果 | 证据目录 |
|---|---|---|
| TC82 optimized | PASS | `logs/post2s_fix_tc82/` |
| TC84 optimized | PASS | `logs/post2s_fix_tc84_85/` |
| TC85 optimized | PASS | `logs/post2s_fix_tc84_85/` |
| TC128 spill-noopt | PASS | `logs/post2s_fix_tc128/` |
| TC141 spill-noopt | PASS | `logs/post2s_fix_tc141_noopt/` |
| TC141 optimized | PASS | `logs/actual_gem5_tc141/` |

额外 recall timeout/retry 定向回归：

| TC | 结果 | 证据目录 |
|---:|---|---|
| 40 | PASS | `logs/post2s_fix_tc40/` |

## 6. 最终结论

原始低频队列的 64 个目标，在原运行或修复后独立 rerun 中全部取得 PASS：

```text
64 PASS
0 unresolved FAIL
```

原始 `matrix.tsv` 作为执行历史保留，不覆盖其中的旧 FAIL。正式结论应同时引用本文列出的修复后独立 rerun 目录。
