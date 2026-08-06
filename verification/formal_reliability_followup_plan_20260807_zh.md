# 形式化验证与可靠性补充计划

> 日期：2026-08-07
> 计算资源上限：任何单次补充计算最多使用 4 logical cores
> 默认执行：`TLC_WORKERS=4` 或更少；禁止主动使用 8/16/28 cores

## 1. 目的

本文列出当前验收仍需补充的形式化、可靠性和可行性分析。它是执行计划，不把尚未
运行的模型或实验标为 PASS。

## 2. 已有形式化基础

当前已有：

- UBCC protocol core safety/liveness。
- no-cleanup liveness contrast。
- transport fault safety/liveness。
- multi-PA。
- multi-socket。
- EP intra-node single/dual。
- TC224 waiter retirement focused model。
- EP-RNF snoop arbitration focused model。

这些结果只在各自有限状态空间中成立，不能外推为完整生产代码证明。

## 3. P0/P1 补充模型

### 3.1 TC157 partial Ack re-drive

#### 要证明的不变量

1. Home 只重发 `targetMask & ~ackMask`。
2. retry 保持原 epoch/reqId。
3. duplicate Ack 不重复减少 pending count。
4. completion 后停止 retry。
5. retry 次数有界。
6. 全部目标 Ack 后 outstanding drain。
7. retry exhaustion 到达明确 terminal state，不是 stutter/timeout。

#### 最小抽象

- 1 个 home。
- 1 个 requester。
- 2 个 invalidate target。
- Ack 可 drop、duplicate、delay/reorder 抽象为任意到达顺序。
- retry budget 取 2 或 3。

#### 建议产物

```text
verification/tla/ubcc_tc157_partial_ack_redrive.tla
verification/tla/ubcc_tc157_partial_ack_redrive.cfg
verification/results/ubcc_tc157_partial_ack_redrive.log
```

### 3.2 TC159 stable tuple 和 exact replay

#### 要证明的不变量

1. accepted pending upgrade 在最终 Ack/commit 前保持同一
   `(PA,node,socket,epoch,reqId)`。
2. exact duplicate UpgradeReq 幂等 replay 当前 stage。
3. `WAITING_LOCAL_DONE` exact replay 可以返回 `targetMask=0`。
4. non-matching tuple 必须拒绝。
5. recovery resend 不产生 reqId churn。
6. requester completion 与 Home commit 至多一次。
7. completion 后 tuple 和 retry state drain。

#### 最小抽象

- 1 PA。
- 1 requester、1 home。
- UpgradeResp 和 UpgradeAckNotify 各允许一次或连续两次 drop。
- Home stage：NEW、WAITING_ACKS、WAITING_LOCAL_DONE、COMMITTED。
- requester stage：IDLE、REQUESTED、ACCEPTED、DONE。

#### 建议产物

```text
verification/tla/ubcc_tc159_upgrade_replay.tla
verification/tla/ubcc_tc159_upgrade_replay.cfg
verification/results/ubcc_tc159_upgrade_replay.log
```

### 3.3 Retry exhaustion 通用模型

#### 要证明的性质

- 每个 retryable wait-point 最终完成或到达 `EXHAUSTED`。
- 永久 Drop 不形成无限 retry/stutter。
- exhaustion 不提交错误目录状态。
- exhaustion 后不再发送新 retry。
- error result 可由 verifier 确定性识别。

#### 建议范围

先抽象 Clear、InvalidateAck、RecallResp、UpgradeResp/AckNotify 四条关键链，不在首版
做所有消息笛卡尔积。

### 3.4 ARM memory-order litmus 模型/测试

形式模型不应直接尝试完整 ARM ISA。建议先建立 executable litmus specification：

- MP：message passing。
- release/acquire publish-consume。
- store + DMB/DSB + remote load。
- same-line competing writer。
- independent-line allowed reordering。

每个场景记录 allowed/forbidden outcome，再决定使用 herd7、TLA+ 抽象或 E2E。

## 4. Q1-Q7 可靠性分析

### 4.1 Q1 single-fault

当前 TC148-TC159 可作为基础。需要生成统一 message/action coverage 表，区分：

- 已 qualification。
- 只有 Duplicate/Delay/Reorder。
- Drop recovery 已实现。
- Drop 只保证安全失败。
- 未覆盖。

### 4.2 Q2 repeated-loss

建议第一批：

- drop first 2。
- drop first 3。
- drop ordinals 1,3。
- request retry 后 replay response 再次 drop。

每个 case 验证 stable reqId、deadline、attempt count 和 drain。

### 4.3 Q3 composed-fault

首批组合：

- UpgradeResp Drop + UpgradeAckNotify Drop。
- InvalidateReq Drop + InvalidateAck Drop。
- RecallReq Drop + RecallResp Drop。
- ClearReq Drop + ClearResp Delay。

### 4.4 Q4 concurrency/burst

- 多 PA 同时 fault。
- 多 home 同时 fault。
- partial Ack 后 fault。
- outstanding 接近容量。
- burst 中相同 rule ordinal 精确命中。

### 4.5 Q5 topology

- 3N1S：完整基础。
- 3N2S：src/dst socket、same-PA cross-socket、tuple routing。
- 8N2S：代表性抽样和高并发，不替代 3N1S 基础矩阵。

### 4.6 Q6 exhaustion

- 测试 retry budget 建议设为 3。
- 结果必须为 `EXPECTED_RETRY_EXHAUSTION`。
- 外层 TIMEOUT 一律失败。
- 验证没有错误 commit、没有 queue 泄漏、没有 completion 后 retry。

### 4.7 Q7 no-fault regression

每次 qualification 后至少执行：

- TC8 upgrade/invalidate。
- TC16 concurrent upgrades。
- spill/fill representative。
- 2S routing representative。

## 5. 计算资源规则

### 5.1 TLC

所有补充模型使用：

```bash
TLC_WORKERS=4 bash verification/tla/run_tlc.sh model.tla model.cfg <timeout>
```

小模型调试可使用 `TLC_WORKERS=1` 或 `2`。当前 runner 默认已经收敛为 4 workers；
正式命令仍应显式记录 `TLC_WORKERS`，避免后续默认值变化影响证据复现。

### 5.2 E2E/fault

单次计算最多使用 4 logical cores，例如：

```bash
FAULT_CPU_SET=6-9 bash scripts/run_fault_tests.sh <mode>
```

不得与其他任务叠加后超过用户指定的整体资源上限。

### 5.3 运行 artifact

每次补充计算必须保存：

- command。
- cpuset/workers。
- timeout。
- source/binary/model hash。
- stdout/stderr。
- return code。
- summary。

## 6. 执行顺序

1. 将 `run_tlc.sh` 默认 worker 从 8 收敛到 4，增加 durable output path。
2. 建立 TC157 focused model，先用 tiny cfg 检查。
3. 建立 TC159 focused model，先用 tiny cfg 检查。
4. 建立 retry exhaustion 抽象。
5. 扩展 fault rule schema 后执行 Q2/Q3。
6. 执行 3N2S/8N2S 代表性 Q5。
7. 最后执行 Q7 无故障回归。

## 7. 完成定义

形式化和可靠性补充完成后应产生：

- 新模型、cfg 和原始结果。
- 每个模型的范围、变量和 invariant/property 表。
- C++ symbol fidelity mapping。
- fault message/action/topology/retry coverage matrix。
- per-case trigger、delivery、retry、drain 和 data oracle artifact。
- 明确的 `QUALIFIED/FAILED/EXPECTED_RETRY_EXHAUSTION/NOT IN SCOPE` 状态。

在上述产物不存在前，不得把计划中的模型或 fault 场景写成已验证。
