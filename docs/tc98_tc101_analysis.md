# TC98 & TC101 失败分析

> 状态：TC100 已通过。TC98/TC101 均失败，非一致性 bug，属于 PDES 保守同步下多节点热竞争时序边界问题。
> 本文档供专家咨询使用。

---

## 1. 系统环境

| 项目 | 值 |
|------|-----|
| 规模 | 8 节点 × 2 socket（16 个 primary，每个 4 CPU，socket 0/1） |
| 同步 | PDES 保守同步（CONTROL_SYNC heartbeat + `safeTs` 滑动窗口） |
| 一致 home | 所有 PA 映射到 node 0 socket 0 的 UBCC |
| EPSNF retry cycle | 1,600,000 ticks（16×100ns） |
| Sequencer deadlock threshold | 500,000,000 ticks（500ms） |
| C3 Batch RS | 默认 ON（`UBCC_BATCH_RS=1`），可通过 env 关闭 |
| C4 Direct-Forward | 默认 ON（`UBCC_DIRECT_FWD=1`），可通过 env 关闭 |
| 当前 commit | `31c9cc5`（含 grant-hit fix） |

---

## 1.1 Docker 编译与实验运行

### 编译 ubio（native 模块）

```bash
# ubio（UBCC directory controller）
docker run --rm -v /mnt/data2/cgc/cc-ep:/workspace -w /workspace \
  ubcc-dev:ubuntu20.04 bash -c 'bash scripts/build_ubio.sh'

# 或手动编译
docker run --rm -v /mnt/data2/cgc/cc-ep:/workspace -w /workspace \
  ubcc-dev:ubuntu20.04 bash -c '
ROOT=/workspace MOD=$ROOT/modules/ubiomodule
CXX="-std=c++17 -O2 -I$MOD -I$MOD/mem/ruby -I$ROOT -I$ROOT/thirdparty/zeromq/include"
LD="-L$ROOT/thirdparty/zeromq/lib -lzmq -lpthread"
SRCS="$MOD/UBCCController.cc $MOD/ResidentDir.cc $MOD/BackstoreSchemaA.cc $MOD/BackstoreSchemaC.cc $MOD/NodeAddressMap.cc"
g++ $CXX $ROOT/tools/ubio/ubio_main.cc $ROOT/framework/Port.cc $SRCS $LD -o $ROOT/build/bin/ubio
'
```

### 编译 gem5

```bash
docker run --rm -v /mnt/data2/cgc/cc-ep:/workspace -w /workspace \
  ubcc-dev:ubuntu20.04 bash -c '
cd /workspace/gem5
# 如需强制重编译特定模块（修改了 .cc/.hh 后）：
# rm -f build/ARM/gem5.build/mem/ruby/protocol/chi/ep/EPRNFController.o
# rm -f build/ARM/gem5.build/mem/ruby/protocol/chi/ep/EPBackend.o
scons build/ARM/gem5.opt -j32
'
```

### 编译 workload（TC98 / TC101）

```bash
# 在 Docker 内编译 workload 为 ARM 静态 ELF：
docker run --rm -v /mnt/data2/cgc/cc-ep:/workspace -w /workspace \
  ubcc-dev:ubuntu20.04 bash -c '
NUM_NODES=8 NUM_SOCKETS=2 bash scripts/compile_workload.sh 98   # TC98
NUM_NODES=8 NUM_SOCKETS=2 bash scripts/compile_workload.sh 101  # TC101
NUM_NODES=8 NUM_SOCKETS=2 bash scripts/compile_workload.sh 100  # TC100
'
```

### 运行实验

```bash
# 在 Docker 内运行（所有 8 节点 gem5 + 16 ubio + 1 networksim）
docker run --rm -v /mnt/data2/cgc/cc-ep:/workspace -w /workspace \
  ubcc-dev:ubuntu20.04 bash -c '
# TC98（timeout 1200s）
TIMEOUT_SEC=1200 tests/e2e/run_multi.sh --8n2s 98 2>&1 | tail -5

# TC100（C3 batch RS demo，timeout 600s）
TIMEOUT_SEC=600 tests/e2e/run_multi.sh --8n2s 100 2>&1 | tail -5

# TC101（C4 direct-forward demo，timeout 600s）
TIMEOUT_SEC=600 tests/e2e/run_multi.sh --8n2s 101 2>&1 | tail -5

# 关闭 C3 / C4 功能后运行：
UBCC_BATCH_RS=0 tests/e2e/run_multi.sh --8n2s 101 2>&1 | tail -5
UBCC_DIRECT_FWD=0 tests/e2e/run_multi.sh --8n2s 101 2>&1 | tail -5
'
```

### 查看日志

```bash
# 日志位置（每次运行生成时间戳目录）：
ls -t /mnt/data2/cgc/cc-ep/logs | head -3
# 例：/mnt/data2/cgc/cc-ep/logs/20260712_072710_8n2s/

# 目录结构：
# ├── gem5_tc101_node0/stdout.log  ← gem5 仿真输出（EPSNF-RECV、CLEAR trace 等）
# ├── gem5_tc101_node0/stderr.log  ← gem5 诊断（GEM5-SEND、CLK-SYNC 等）
# ├── ubio_n0_s0/stderr.log        ← node 0 socket 0 的 UBCC 消息日志（关键）
# └── ubio_n0_s0/stdout.log        ← node 0 socket 0 的 ubio 标准输出

# 常用 grep（在 Docker 外直接查）：
LOGDIR=$(ls -td /mnt/data2/cgc/cc-ep/logs/20260712_*8n2s/ | head -1)

# TC98 进度
grep -h "TC98_PROGRESS" $LOGDIR/gem5_tc98_node*/stdout.log | head

# TC101 完成节点
grep -c "SIM DONE" $LOGDIR/gem5_tc101_node*/stdout.log

# UBCC grant-hit 命中次数
grep -c "grant hit" $LOGDIR/ubio_n0_s0/stderr.log

# UBCC BUSY retry 次数（TC101）
grep -c "BUSY" $LOGDIR/ubio_n0_s0/stderr.log

# gem5 死锁检测
grep "Deadlock\|panic\|assert" $LOGDIR/gem5_tc98_node0/stderr.log
```

### 环境变量说明

| 变量 | 默认值 | 作用 |
|------|--------|------|
| `UBCC_BATCH_RS=1` | 1（启用） | C3 Batch RS Grant：合并同一 PA 的多个 ReadShared 请求 |
| `UBCC_DIRECT_FWD=1` | 1（启用） | C4 Direct-Forward：RECALL 时数据直接从 owner 转发给 requester |
| `MAX_PENDING_PER_PA` | 16 | 每个 PA 的 `_pendingRequesters` 队列深度上限 |
| `EP_SYNC_INTERVAL_PS` | 25000 | PDES 同步间隔（ps），脚本中通过 `--C5` 传递 |

---

## 2. TC98 — Hotspot Contention（16 核抢 1 行）

### 2.1 负载设计

16 个 primary（8 节点 × 2 socket）全部针对**同一个 cache line**（home node 0 的 `PA=0x10007800`）做 16 轮 write-then-read-back：

```c
for (r = 0; r < 16; r++) {
    *hot_addr() = unique_val;   // RU, 写唯一值
    (void)*hot_addr();          // RS, 流水线排空读回
}
// 写 done marker → barrier → node 0 校验所有 marker
```

### 2.2 实际表现（当前已应用 grant-hit fix 后）

| 指标 | 值 |
|------|----|
| 节点 progress 日志 | **0 条**（16 轮 × 16 primary 中没有任何一个走到第 1 轮 `(r%4)==0` 的 progress 打印点）|
| RECALL-SNOOP（自我 snoop guard 触发） | 1 次（node 0） |
| UPGRADE-DIAG | 0 次 |
| grant-hit（同 requester WAITING_CLEAR 重试命中） | **9 次** |
| 最终故障 | node 0 Sequencer panic: `Possible Deadlock detected`（~200ms sim time） |

### 2.3 死锁历程

```
Timeline on PA=0x10007800 (home node 0):

T0: node 0 (socket 0) RU → UBCC outstanding {opType=RECALL, stage=WAITING_TARGET_RESP,
    requester=0}, RECALL → owner node 7
T1: node 7 响应 RECALL，数据 forward 给 node 0
    outstanding 变为 {opType=GRANT_HANDSHAKE, stage=WAITING_CLEAR, requester=0}
T2: node 0 写完，立即 (void)*hot_addr() 读回 RS
    → UBCC 看到 outstanding requester=0, stage=WAITING_CLEAR
    → grant-hit fix 命中（9 次之一），返回 grant，不挂起
T3: node 1 (socket 0) 到达 RU → UBCC 看到 outstanding 属于 requester=0
    → 放入 _pendingRequesters 队列
T4: Clear 从 node 0 提交，outstanding 释放 → pending 队列回放 → node 1 拿 grant
T5-T∞: 16 primary 轮转串行化，每轮都需要完整的 RECALL+Grant+Clear+ReadBack
    → 在 PDES 保守同步下，跨节点消息每次至少 100ns delay
    → 16 个请求者 × 16 轮 → 256 次事务, 每次 ~300ns → 预估 ~77μs
    → 但实际表现为 200ms 还未完成 1 轮 (0 条 progress)
```

**关键观察**：TC98 没有吐出任何 progress 日志，意味着 Sequencer 在**远小于 200ms** 时就已经判定死锁。而 16 个节点全部卡在同一 PA 上，只要有一个节点的 Sequencer 因为任何原因 retry 过长时间没进展，就会触发 `Possible Deadlock` panic。

### 2.4 可能的根因

1. **PDES 保守同步下的隐式等待链**。当 node 7 从 owner 回复 RECALL 时，node 7 自己也可能在等待其他 node 的同步（PDES `safeTs` 不前进）。整个 16 节点系统的 `safeTs` 被最慢的节点的 `lastSyncTs + syncInterval` 上限卡住。在 TC98 的 all-to-one 模式下，最慢节点就是 home node 0 等待的所有 requester 中最慢的那个。

2. **grant-hit fix 只覆盖 WAITING_CLEAR**。如果 requester A 的 RU outstanding 还在 `WAITING_TARGET_RESP`（RECALL 未完成），requester B 到达 → _pendingRequesters 入队。但入队后的回放时机取决于 Clear→DONE 的提交时序。如果 PDES 时钟不前进，Clear 可能永远不会提交。

3. **Sequencer 的 `m_deadlock_threshold` 过于激进**。500M ticks（500ms sim time）本身不小，但在 TC98 的全热竞争模式下，PDES 时钟被卡死 → Sequencer 的 retry 请求走不出去 → 500M ticks 在 Sequencer 的本地时钟下流逝 → panic。

### 2.5 可能的解决方案

| 方案 | 风险 | 预期效果 |
|------|------|----------|
| **A. 放宽 deadlock_threshold**（如 5× 到 2.5G ticks） | 低 | 延迟 panic 但不解决根源 |
| **B. WAITING_CLEAR 阶段允许其他 requester 被 queue**（而不是 BUSY） | 中 — 需验证并发安全性 | 减少 retry 风暴，让队列回放代替轮询 |
| **C. WAITING_TARGET_RESP 阶段允许其他 requester 入队**（不是只排队到等待 Clear） | 高 — 并发一致性需要仔细分析 | 更多并发度 |
| **D. 将 TC98 改为分 PA（如 TC99 每 node 独立行）** | 无 | 已验证 work（TC99 通过） |
| **E. RECALL 返回时批量回放 pending queue**（避免一个一个顺序回放） | 低 | 提升吞吐，减少 PDES 等待链效应 |

---

## 3. TC101 — Direct-Forward Chain（C4 stress test）

### 3.1 负载设计

7 个 node（1-7）组成单向链 × 32 轮。node 0 只做 verifier：

```c
// Init: 每个 node 写自己的 slot，建立 initial ownership
*slot_addr(node_id, socket_id) = init_val;
barrier();

// Chain × 32 rounds:
for (r = 0; r < 32; r++) {
    if (node_id != 0) {
        got = *slot_addr(node_id - 1, socket_id);  // 读前驱 slot → 触发 C4-FORWARD
        *slot_addr(node_id, socket_id) = got ^ 0x80000000u;  // 写自己 slot
    }
    // node 0 无操作（保持 PDES 平衡但不参与 chain）
}
barrier();

// Verify: node 0 读所有节点的 slot
for (n = 0; n < 8; n++)
    for (s = 0; s < 2; s++)
        *slot_addr(n, s);  // ← node 0 卡在这里（PA=0x10007bc0, node 7 slot）
```

### 3.2 实际表现

| 指标 | 值 |
|------|-----|
| **Node 1-7 SIM DONE** | **7/7 完成** ✓ |
| C4-FORWARD（direct-forward 事件） | 14 次 |
| RECALL-SNOOP（自我 snoop guard 触发） | 12 次（node 1-6 各 2 次） |
| grant-hit（同 requester WAITING_CLEAR 重试命中） | **54 次** |
| **Node 0 SIM DONE** | **0（未完成）** ✗ |
| Node 0 读 PA=0x10007bc0 的 BUSY retry 次数 | **34,459 次** |
| 最终故障 | ubio 进程被 OOM killer 杀死 |

### 3.3 故障历程

```
Phase 1: Init (barrier 前)
  node 7 写 slot_addr(7,0) = PA=0x10007bc0
  → UBCC 处理 RU, 授 grant, Clear 提交, DONE ✓

Phase 2: Chain × 32 rounds (node 0 不参与)
  node 7 反复: 读 node 6 slot → 写自己 slot PA=0x10007bc0
  → 每轮在 PA=0x10007bc0 上产生完整的 RU+Grant+Clear 事务
  → BARRIER 等待, 7/7 nodes DONE ✓

Phase 3: Verify (node 0 only)
  node 0 循环读所有 slot, 到达 node 7 的 slot_addr(7,0) = PA=0x10007bc0
  → ReadReq (requester=0, epoch=15, reqId=15) 发送到 UBCC home node 0
  → UBCC 已在 PA=0x10007bc0 上有 outstanding:
       {
         opType  = GRANT_HANDSHAKE (0),
         stage   = WAITING_TARGET_RESP (1),  ← RECALL 等待 node 7 回应
         requesterNode = 0                  ← 这是 node 0 自己的请求！
       }
  → node 0 (requester=0) 看到同 requester outstanding, stage=WAITING_TARGET_RESP
  → grant-hit fix 不匹配（只覆盖 WAITING_CLEAR）
  → 返回 BUSY
  → node 0 EPRNF 收到 BUSY → 无限 retry

  34,459 次 retry, ubio stderr 24 万行日志
  → OOM killer 杀 ubio 进程
```

### 3.4 关键疑问

**为什么 outstanding 的 requester 是 node 0？**

Node 7 在 chain 最后一轮写自己的 slot，生成一个 GRANT_HANDSHAKE outstanding (requester=7)。这个 outstanding 会经过：RECALL to node 6 → Grant to node 7 → Clear from node 7 → DONE。

BARRIER 后，node 7 的 outstanding **应该**已经完成（node 7 报告了 SIM DONE）。但 node 0 verify 时发现 PA=0x10007bc0 上仍有 outstanding，且**requesterNode=0**，说明这是 node 0 **自己首次 read** 时创建的：

1. node 0 ReadReq (reqId=15) → UBCC 创建 GRANT_HANDSHAKE (requester=0)
2. UBCC 检测到需要 RECALL owner（node 7）→ stage 变为 WAITING_TARGET_RESP
3. RECALL 发送到 node 7 → **node 7 已经 SIM DONE，不再处理新消息**
4. RECALL 永远收不到回应 → outstanding stuck

**为什么 node 7 的 last write outstanding 没有挡住 node 0 的 read？**

Node 7 的 outstanding 可能已经在 BARRIER 之前完成了 Clear 提交，但 UBCC 的 `_pendingRequesters` 中仍有残留，或者 node 7 完成 Clear 后 directory 状态更新不完整，导致 node 0 请求到达时 UBCC 认为 node 7 仍然是 owner → 需要 RECALL。

### 3.5 可能的解决方案

| 方案 | 风险 | 预期效果 |
|------|------|----------|
| **A. RECALL 超时/重试机制** | 中 — 需定义超时后的 fallback 行为 | 解除 stuck RECALL |
| **B. WAITING_TARGET_RESP 阶段同 requester 返回特别处理**（如立即重新发起 GRANT 而不等待 RECALL） | 高 — 可能引入一致性问题 | 避免 retry storm |
| **C. Verify 前插入 pipeline drain**（如 node 0 先对 PA=0x10007bc0 做一次 nop-write 再读） | 低 — workload 层面的 workaround | 强制清理残留 outstanding |
| **D. BARRIER 后等待 N ticks 再开始 verify** | 低 — 但 PDES 下不一定有效 | 给 PDES 时钟推进留缓冲 |
| **E. Retry 加指数退避上限**（Ubio 侧不打印无限日志） | 低 — 实际价值仅为运维 | 防止 OOM，不解决 correctness |
| **F. 调查 PDES 下 RECALL 是否会在目标节点 DONE 后丢失** | 中 — 需深入 PDES/Port 代码 | 如果确认是 PDES 缺陷 → 修复核心 |
| **G. `_pendingRequesters` 队列状态机完整性检查** | 低 — 代码审查 | 确保队列消费与 outstanding 释放同步 |

---

## 4. 相关代码位置

| 文件 | 行号 | 内容 |
|------|------|------|
| `modules/ubiomodule/UBCCController.cc` | 460-495 | `processOuterRequest` 的 outstanding 检查 & grant-hit fix |
| `modules/ubiomodule/UBCCController.cc` | 625-700 | G_S+RS fast path（batch RS） |
| `modules/ubiomodule/UBCCController.cc` | 2740-2840 | `_pendingRequesters` 队列入队/回放逻辑 |
| `modules/ubiomodule/UBCCController.hh` | 99-109 | `OpStage` 枚举（CREATED/WAITING_TARGET_RESP/WAITING_ALL_ACKS/WAITING_LOCAL_DONE/WAITING_CLEAR/DONE/CANCELLED/TIMED_OUT/PERSISTENT_BUSY） |
| `gem5/src/mem/ruby/system/Sequencer.cc` | 239 | `Possible Deadlock detected` panic 位置 |
| `gem5/src/mem/ruby/system/Sequencer.py` | | `deadlock_threshold` 参数（默认 500M ticks） |
| `gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc` | 704-755 | 自我 snoop guard（TC98/TC101 中正常工作） |
| `tests/e2e/workloads/e2e_tc98_8n2s_hotspot.c` | 52-68 | TC98 热竞争循环 |
| `tests/e2e/workloads/e2e_tc101_8n2s_direct_fwd.c` | 48-75 | TC101 chain + verify |

---

## 5. 已尝试的修改（总结）

1. **grant-hit fix**（`31c9cc5`）：WAITING_CLEAR + 同 requester → 直接返回 grant（不再要求 replayArmed + reqId 匹配）。在 TC98 命中 9 次，TC101 命中 54 次。**有效但不彻底** —— TC98 仍有不同 requester 之间的热竞争，TC101 仍有 RECALL stuck 导致的 retry storm。

2. **自我 snoop guard**（`gem5` 子模组分支 `v4-selfsnoop-fix-clean`）：EPRNFController 在 `handleSnpCleanInvalid` 中检查是否有 live CHI txns 或 active RECALL，如有则走 non-upgrade 路径。**已验证有效**（TC98/TC101 均无 UPGRADE-DIAG 触发，TC2/TC3/TC8/TC39/TC99 全部通过回归）。

3. **G_S+RS fast path**：受 `_batchRsEnabled` 开关控制，默认 ON。Close 回归通过。

---

## 6. 待请教专家的问题

1. **PDES 下 RECALL 可能丢失吗？** node 7 SIM DONE 后，node 0 发起的 RECALL 是否因为 PDES 保守同步而无法递送到 node 7？PDES 是否要求所有节点同时 active 才能完成跨节点消息交换？

2. **WAITING_TARGET_RESP 阶段的同 requester retry** 应该如何设计？当前返回 BUSY 是正确的，但是否应该加钩子让请求者知道 "RECALL 正在进行，请等待" 而不是无限重试？

3. **Sequencer deadlock 检测阈值** 在 PDES 下是否有特殊含义？517M ticks 的阈值是否过大或过小？是否有其他信号可以区分 "PDES stalled" 和 "真正的 coherence deadlock"？

4. **_pendingRequesters 队列** 的消费条件是否充分？在 RECALL → WAITING_CLEAR → DONE 的状态迁移中，pending 队列的入队/出队是否有遗漏的边界情况？
