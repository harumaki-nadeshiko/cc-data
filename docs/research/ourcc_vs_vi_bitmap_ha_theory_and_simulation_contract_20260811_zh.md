# OurCC 与 VI+Sharer-Bitmap HA 理论对比及仿真合同

**日期：** 2026-08-11  
**状态：** 理论模型冻结稿；仿真结果尚未填写  
**目标：** 在不计传输故障、不计瞬态 TxnToken 面积、暂不考虑 direct transfer 的条件下，比较 OurCC 与甲方 HA 的 requester-visible、Home release 和目录扩展性能。

---

## 0. 真正关键的结论

> **K1：HA 的 requester Ack 对应 OurCC 的 `ClearReq`，不是 `ClearResp`。**

> **K2：可以删除 requester 对同步 `ClearResp` 的等待，但不能删除 requester 到 Home 的 Ack/Clear。Home 仍须在收到 Ack 后才能提交并释放同址锁。**

> **K3：纯理论 ordered/lossless 模型下可以不考虑 TxnToken，也可以删除长期 Epoch；但必须冻结为“每行单 outstanding、消息 exactly-once、同址有序、Ack 后旧权限不再产生任何消息、测试期间无节点重启”。**

> **K4：当前实现的 Clear 发在本地 HN/L2 最终 CompData/install 之前。仿真若采用 receipt-Ack 模型，必须增加 per-line pending-install ordering。最后一个 CompData 注入仅在冻结的 HN-F“同地址 TBE 有序且后续同址事务不能越过该 TBE”合同下足够；否则不能把注入点泛化为真实 InstallDone。**

> **K5：甲方没有 dirty bit 时，write-back 最新数据位置不能从一般 bitmap 唯一判定。最小安全约定是：多个 holder 必为 clean 且 Home data latest；unique write 后只剩一个 potential latest holder，其他节点读取时必须联系该 holder，或引入未披露的等价状态。**

> **K6：甲方“每次写都触发 invalidation/HA transaction”使其无法复用本地写权限。OurCC 的 E/M repeated write 是最稳定的理论优势。**

---

## 1. 冻结假设

### 1.1 共同假设

1. 网络 exactly-once、ordered、eventual delivery。
2. 不考虑 drop、duplicate、reorder 和 retry latency。
3. 每个 Home 对同一 Cacheline 最多一笔 active coherence transaction。
4. 不计 TxnToken、line lock、Ack mask 等 transient state 的长期 SRAM 面积。
5. O3 可以并发不同地址请求，但不能消除同一 Cacheline 的因果依赖链。
6. HA 与 OurCC 均采用按地址均匀分片的 distributed Home。
7. requester、Home、holder 的 placement 由地址和访问节点决定，不假设 Home 永远 local 或永远 remote。
8. 不考虑 owner-to-requester direct data/authority transfer；数据和权限走 central Home path。
9. Broadcast fanout 近似并行，但写入必须等待所有 required InvAck。
10. 不考虑 endpoint crash、checkpoint 和 restore 对主路径的影响；仅在风险章节说明。

### 1.2 甲方 HA 模型

持久 metadata 最小口径：

```text
remote sharer bitmap = N - 1 bits
local VI state       = 1 bit
total                = N bits / represented Cacheline
```

行为：

1. write-back，不是 write-through。
2. 每次写都向 HA 申请 unique ownership，并执行 invalidation transaction。
3. HA 收齐所有 required InvAck 后向 requester 返回 permission `Resp`。
4. requester 收到 Resp 后发送 Ack；Ack 仅表示 Resp received，不要求本地 cache install 已完成。
5. HA 收到 requester Ack 前不释放同地址锁。
6. 没有额外节点级 dirty bit。
7. 多 holder 状态必须为 clean，Home data 必须 latest。
8. unique write 后只剩一个 potential latest holder；其他节点 read 时，HA 必须联系该 holder，除非甲方存在尚未披露的等价 dirty/latest 状态。
9. **客户外部 HA ordering 源码/可审计证据当前仍不可获得。本文只能把“同地址请求排队，前一 transaction 的 requester Ack 到达并释放锁前不启动下一笔”作为待客户确认的冻结接口假设，不能写成已由本仓库证实或已独立审计的事实。**

### 1.3 OurCC 理论优化模型

1. MESI directory，支持 E/M permission reuse。
2. requester 收到 Grant/Data 后完成本地有序接收。
3. requester 单向发送 Clear/Ack，Home 收到后 commit/release。
4. requester 不等待 `ClearResp`。
5. 当前 24-bit stable Epoch 分为两个理论 profile：
   - `EPOCH`：保留现有 stable Epoch。
   - `NO_STABLE_EPOCH`：删除长期 Epoch，依赖本节 frozen assumptions。
6. metadata resident miss 可进入 MetaRNF/H64 offload，warm/cold latency 采用当前报告值。

---

## 2. Clear、Ack 与完成点

### 2.1 正确消息映射

```text
HA:
    HA -> Requester: Resp(permission grant)
    Requester -> HA: Ack(resp received)
    HA: commit/release lock

OurCC optimized:
    Home -> Requester: Grant/Data
    Requester -> Home: Clear/Ack
    Home: commit/release lock
```

因此：

| HA | OurCC |
|---|---|
| permission Resp | Grant/Data |
| requester Ack | ClearReq/InstallAck |
| HA Ack receive 后 release | Home Clear receive 后 release |
| 无 Ack-of-Ack | 当前额外 ClearResp |

### 2.2 三种完成点

| 完成点 | 定义 |
|---|---|
| `T_resp` | requester 收到 data/permission Resp |
| `T_release` | requester Ack 到达 Home，Home commit 并释放同址锁 |
| `T_root` | requester 按协议定义结束该请求 |

甲方 receipt-Ack 模型：

```text
T_root_HA ~= T_resp_HA + local Ack-post cost
T_release_HA = T_resp_HA + requester->Home Ack leg
```

OurCC one-way Clear：

```text
T_root_Our ~= T_resp_Our + local ordered-install/Ack-post cost
T_release_Our = T_resp_Our + requester->Home Clear leg
```

OurCC current sync ClearResp：

```text
T_root_current = T_resp_Our
               + requester->Home Clear
               + Home->requester ClearResp
```

### 2.3 Receipt-Ack 是否安全

在纯理论模型中，可以令 Ack 在收到 Resp 时立即发出，但这条件化地依赖尚未取得
外部源码/证据的客户 Home/HA ordering 合同：Ack 到达前阻塞下一同址 transaction，
从而避免 Home 侧 authority 重叠。Requester 侧仍需以下最小抽象不变量：

```text
OrderedInstallGuard:
    同址后续 snoop/request 即使在物理 install 前到达 requester，
    也必须排在该 Resp 对应的 install 之后处理。
```

这是为了处理一个窄窗口：Ack 已发且 HA 随后释放，但 requester 实体 cache install 仍在完成中。
实现方式可以是：

1. requester per-line ingress FIFO；
2. `INSTALL_PENDING` guard，后续 snoop 暂存；
3. 后续 snoop NACK/retry；
4. 更强的真实 `InstallDone` 后才发 Ack；或者在冻结的 HN-F 同地址 TBE
   ordering 合同成立时，以最后一个 CompData 注入作为足够的排序点。

这里必须限制“最后一个 CompData 注入足够”的适用范围：HN-F 必须保证该地址的
后续 snoop/request 不能越过仍持有安装上下文的 TBE。若只有消息已注入而没有该
same-address TBE ordering，注入不等价于 cache install，也不等价于一般意义的
`InstallDone`。

纯理论延迟中，local install/guard latency 未配置，按近似 0；因此**若客户 ordering
合同成立**，Ack 不增加额外理论等待。仿真实现仍需 transient install-pending
context，不能只依赖 Home queue。

### 2.4 当前代码为何不能直接视作 install-Ack

当前一般 Remote Miss 顺序是：

```text
EP receives outer Grant
-> EP sends ClearReq
-> Home commits/releases
-> ClearResp returns
-> EP-SNF constructs local CompData
-> local HN/L2 receives and installs data/permission
```

因此当前 Clear 只证明 EP protocol accept，不证明 local HN/L2 install。相关实现位置：

- `gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc:2239-2312`
- `gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc:289-430`
- `modules/ubiomodule/UBCCController.cc:3560-3768`

### 2.5 去除同步 ClearResp 的理论协议

```text
Home:
    finish old-owner recall / sharer invalidation
    create WAITING_ACK
    send Grant/Data
    retain same-line lock

Requester:
    receive Grant/Data
    establish OrderedInstallGuard
    post one-way Clear/Ack to reliable transport
    complete requester root without waiting response

Home:
    receive exact Clear/Ack
    commit intended directory result
    release same-line lock
    replay queued requests
```

如果采用更强 install-Ack，必须使用真实 completion hook；仅在冻结的 HN-F
same-address TBE ordered contract 下，才可用最后一个 CompData 注入替代该 hook：

```text
receive Grant/Data
-> HN/L2 InstallDone
   OR last CompData injection + ordered HN-F same-address TBE contract
-> post Clear/Ack
```

### 2.6 DMB/DSB/atomic

普通 request 可不等 Home Ack-of-Ack。对于要求 global commit 的 barrier/atomic，可以增加累计 commit barrier：

```text
Requester posts all pending per-line Ack
Requester -> Homes: CommitBarrier(sequence)
Homes ensure prior Ack committed
Homes -> Requester: CommitBarrierResp
```

这样避免每行同步 `ClearResp`，又不弱化强 barrier 的 global completion 语义。

---

## 3. NO_STABLE_EPOCH 理论条件

本理论分析不引入 TxnToken，也不计 TxnToken 面积。删除长期 Epoch 的最小条件为：

1. 同一 Cacheline 单 outstanding。
2. 消息以 Cacheline address、requester 和 active stage 匹配。
3. 网络 exactly-once 且同址有序。
4. Home 在 Ack 前不复用该行 transaction context。
5. InvAck/RecallResp 表示目标节点已经完成旧权限 quiescence。
6. Ack 后旧 copy 不再产生旧 WB、Evict 或 data response。
7. requester 重新获得同一地址前，前一 transaction 已完整 release。
8. 测试期间无 endpoint crash/restart。
9. 不支持 checkpoint/restore，或 restore 前全系统 drain 并清空 coherence state。

在这些强约束下，stable Epoch 不再承担必要的 transport 或 ABA 防护，可从 persistent directory 中删除。

若以下任一项不成立，则至少需要 compact generation/lease：

- Ack 后仍可能延迟产生旧 writeback；
- endpoint 可重启并保留旧消息；
- 不同通道没有同址 ordering；
- cache copy 生命周期可跨越多个 ownership transaction；
- asynchronous metadata writeback 可能覆盖更新后的状态。

### 3.1 Crash 的有限讨论

本理论 profile 不覆盖 requester 在 Grant 后、Ack 前 crash。若未来纳入 crash：

1. Home 需要 lease/failure detector；或
2. requester rejoin 前全域 drain；或
3. 增加 incarnation/generation。

checkpoint/restore 不进入本阶段主模型。

---

## 4. HA 无 dirty bit 的理论状态语义

### 4.1 必需不变量

```text
holder count = 0:
    Home data latest

holder count > 1:
    all holders clean
    Home data latest

unique-write ownership:
    bitmap only retains requester
    requester may hold latest dirty data
```

由于没有 dirty bit，`one holder` 既可能来自 clean singleton read，也可能来自 unique write。若没有其他状态，HA 必须保守地把 sole holder 当作 potential latest owner，并在其他 requester read 时联系它。

若甲方能够避免该 probe，则说明实现中存在下列至少一种等价信息：

- dirty/latest bit；
- owner mode；
- write-acquired state；
- Home data valid bit；
- per-holder response contract。

这些信息即使不称为 dirty bit，也必须纳入后续事实确认。

### 4.2 Write 路径

每次 write：

```text
Requester -> HA: AcquireUnique
HA locks line
HA invalidates all bitmap holders except requester
HA waits all required InvAck
HA -> Requester: permission Resp
Requester -> HA: receipt Ack
HA releases lock
```

即使 requester 已是唯一 holder，题设仍要求每次 write 经过 HA transaction；此时 target mask 可为空，但 requester-Home round trip 仍存在。

---

## 5. 实际延迟参数

| 参数 | 数值 | 来源 |
|---|---:|---|
| CPU cycle | 0.5 ns | `scripts/solve_latency_params.py` |
| L1 tag+data | 1.5 ns | 同上 |
| Local L3 target | 15 ns | 同上 |
| Local DRAM | 100 ns | 同上 |
| NUMA DRAM | 110 ns | 同上 |
| 单向跨节点 `tau` | 约 410 ns | trace/report |
| Remote ReadShared | 896 ns | `reports/metrics_latency_report.md` |
| Upgrade | 890 ns | 同上 |
| ReadUnique current | 1728 ns | 同上 |
| Metadata offload warm | 49 ns | 同上 |
| Metadata offload cold | 90 ns | 同上 |

本地协议 residual：

```text
P_read     = 896 - 2*410 = 76 ns
P_inv      = 890 - 2*410 = 70 ns
P_handoff ~= 88 ns
P_offload_avg = (49 + 90)/2 = 69.5 ns
```

### 5.1 Address-sliced Home

```text
q_N = P(Home != Requester) = 1 - 1/N
```

| N | q_N | requester-Home 平均跨节点 RTT legs |
|---:|---:|---:|
| 2 | 0.5 | 1.0 |
| 4 | 0.75 | 1.5 |
| 8 | 0.875 | 1.75 |

单 remote holder central path：

```text
R -> H -> P -> H -> R
K1(N) = 4 - 4/N
```

| N | K1 |
|---:|---:|
| 2 | 2.0 |
| 4 | 3.0 |
| 8 | 3.5 |

多个 remote sharer 并行 invalidation 的最长 path：

| N | Km |
|---:|---:|
| 2 | 2.0 |
| 4 | 3.5 |
| 8 | 3.75 |

---

## 6. ScaleScheme A：压缩 represented address space

### 6.1 目标地址空间

本阶段每 Home 原始目标空间：

```text
M_target(N) = 128 MiB / (N/2) = 256/N MiB
```

| N | 每 Home 目标空间 |
|---:|---:|
| 2 | 128 MiB |
| 4 | 64 MiB |
| 8 | 32 MiB |

### 6.2 固定面积容量模型

设：

- SRAM `A` bits；
- associativity `W`；
- sets `S=2^s`；
- line size 64 B；
- compressed tag `t ~= ceil(log2 W)`；
- fixed per-entry metadata `f(N)`；
- PLRU `p(W)=2^ceil(log2 W)-1`。

约束：

```text
A_used = S * [ W*(f(N)+t) + p(W) ] <= A
capacity = W*S
represented bytes = capacity*64
```

甲方最小模型：

```text
f_HA(N) = N
```

OurCC current stable Epoch：

```text
f_Our_E(N) = valid1 + MESI2 + dirty1 + ctrl3 + sharersN + epoch24
           = N + 31
```

OurCC no-stable-Epoch：

```text
f_Our_N(N) = N + 7
```

对 HA 使用完整 512 KiB；OurCC 保留 Bloom/group-index 后 exact directory 预算约 448 KiB。这是当前结构口径，不是说 HA 的 transient table 免费，只是本阶段明确不计。

### 6.3 理论 exact capacity

| N | HA N-bit | OurCC EPOCH | OurCC NO_STABLE_EPOCH |
|---:|---:|---:|---:|
| 2 | 1,048,576 lines / 64 MiB | 98,304 / 6 MiB | 262,144 / 16 MiB |
| 4 | 524,288 / 32 MiB | 90,112 / 5.5 MiB | 262,144 / 16 MiB |
| 8 | 327,680 / 20 MiB | 81,920 / 5 MiB | 196,608 / 12 MiB |

相对目标空间：

| N | HA exact coverage | Our EPOCH resident coverage | Our NO_EPOCH resident coverage |
|---:|---:|---:|---:|
| 2 | 50.0% | 4.69% | 12.5% |
| 4 | 50.0% | 8.59% | 25.0% |
| 8 | 62.5% | 15.63% | 37.5% |

Scheme A 严格含义是把 coherent represented range 缩到上述容量；范围内全部走 exact path，范围外不属于该配置的 coherent address space。

### 6.4 Exact requester-visible latency

每格为：

```text
HA / OurCC one-way / Delta(Our-HA)
```

| 场景 | N=2 | N=4 | N=8 |
|---|---:|---:|---:|
| 本地 L1 读命中 | `1.5 / 1.5 / 0` | `1.5 / 1.5 / 0` | `1.5 / 1.5 / 0` |
| Home latest-data read | `510 / 510 / 0` | `715 / 715 / 0` | `817.5 / 817.5 / 0` |
| 多个 clean holder，Home latest | `510 / 510 / 0` | `715 / 715 / 0` | `817.5 / 817.5 / 0` |
| sole potential clean holder read | `896 / 510 / -386` | `1306 / 715 / -591` | `1511 / 817.5 / -693.5` |
| sole dirty/latest holder read | `896 / 896 / 0` | `1306 / 1306 / 0` | `1511 / 1511 / 0` |
| 无 sharer首次写 | `510 / 510 / 0` | `715 / 715 / 0` | `817.5 / 817.5 / 0` |
| single-sharer write | `890 / 890 / 0` | `1300 / 1300 / 0` | `1505 / 1505 / 0` |
| multi-sharer write | `890 / 890 / 0` | `1505 / 1505 / 0` | `1607.5 / 1607.5 / 0` |
| 同一 writer repeated write | `425 / 1.5 / -423.5` | `630 / 1.5 / -628.5` | `732.5 / 1.5 / -731` |
| dirty ownership handoff | `908 / 908 / 0` | `1318 / 1318 / 0` | `1523 / 1523 / 0` |

### 6.5 不冻结最终权重的加权公式

```text
T_mean = sum_i(weight_i * T_i)
sum_i(weight_i) = 1
```

本阶段不冻结合同权重。以下三组仅作 sensitivity，不是最终目标 3 权重：

| Mix | 特征 |
|---|---|
| 基准示例 | local 38%，repeated write 10%，其余分散 |
| 读共享示例 | clean/shared/remote read 权重较高 |
| 写复用示例 | repeated write 25% |

| Mix | N | HA | OurCC one-way | Delta | Our 改善 |
|---|---:|---:|---:|---:|---:|
| 基准示例 | 2 | 412.3 ns | 350.7 ns | -61.6 ns | 14.9% |
| 基准示例 | 4 | 605.0 ns | 512.6 ns | -92.4 ns | 15.3% |
| 基准示例 | 8 | 691.1 ns | 583.3 ns | -107.8 ns | 15.6% |
| 读共享示例 | 2 | 471.3 ns | 424.2 ns | -47.1 ns | 10.0% |
| 读共享示例 | 4 | 686.6 ns | 614.9 ns | -71.7 ns | 10.4% |
| 读共享示例 | 8 | 783.9 ns | 700.0 ns | -84.0 ns | 10.7% |
| 写复用示例 | 2 | 476.8 ns | 359.4 ns | -117.5 ns | 24.6% |
| 写复用示例 | 4 | 712.6 ns | 537.7 ns | -174.9 ns | 24.5% |
| 写复用示例 | 8 | 810.0 ns | 606.4 ns | -203.6 ns | 25.1% |

真正的结论应以场景向量和权重变量给出，而不是把上述任一示例宣称为合同平均。

---

## 7. ScaleScheme B：超出 exact range 后 Broadcast

### 7.1 HA

```text
represented address -> exact bitmap
unrepresented address -> ideal parallel broadcast
```

Broadcast write等待所有 required InvAck。理论模型不计 fanout bandwidth、Ack incast 和 queue，因此是 HA 的乐观下界。

### 7.2 OurCC

```text
ResidentDir hit -> exact path
ResidentDir miss -> metadata offload
```

采用：

```text
warm offload 49 ns
cold offload 90 ns
50/50 average 69.5 ns
```

### 7.3 按目标地址空间的平均场景结果

下表已按第 6.1 节各 N 的 `128/64/32 MiB` 目标空间加权 exact coverage。

每格：

```text
HA bitmap+broadcast / Our EPOCH offload / Delta
```

| 场景 | N=2 | N=4 | N=8 |
|---|---:|---:|---:|
| Home latest read | `700 / 576 / -124` | `1110 / 775 / -335` | `1311 / 876 / -435` |
| sole clean/potential holder read | `896 / 962 / +66` 或按 clean proof优化 | `1408 / 1372 / -36` | `1565 / 1573 / +8` |
| dirty/latest holder read | `896 / 962 / +66` | `1408 / 1372 / -36` | `1565 / 1573 / +8` |
| cold/no-sharer write | `700 / 576 / -124` | `1110 / 775 / -335` | `1311 / 876 / -435` |
| single-sharer write | `890 / 956 / +66` | `1403 / 1366 / -37` | `1559 / 1569 / +10` |
| multi-sharer write | `890 / 956 / +66` | `1505 / 1572 / +67` | `1608 / 1671 / +63` |
| repeated write | `658 / 1.5 / -656` | `1067 / 1.5 / -1066` | `1280 / 1.5 / -1279` |

注：sole-holder read 取决于甲方如何识别 Home data valid。若所有 sole holder 一律视为 potential dirty，则走 holder path；若存在未披露的 clean proof，则可走 Home path。

### 7.4 权重敏感性

Scheme B 不冻结最终权重。示例 mix 的预期趋势：

1. `N=2`：OurCC 优势主要来自 repeated write；metadata offload 在冲突场景可能使 OurCC 慢约 60~70 ns。
2. `N=4`：HA 约一半地址 broadcast，OurCC offload penalty 仅 49~90 ns，OurCC 优势扩大。
3. `N=8`：HA exact coverage 虽为 62.5%，但 unrepresented 地址仍需 broadcast；OurCC repeated write 和 offload 路径仍有优势。
4. pure sharing-heavy、multi-sharer write 且无 repeated write 时，理想 broadcast 可使双方接近，OurCC 甚至可能因 metadata offload慢约 60~70 ns。
5. 一旦加入非零 broadcast bandwidth/queue，HA 延迟会随 N 和活跃节点增长；本理论表没有计入该效应。

---

## 8. Current sync ClearResp 的代价

对于需要 Ack/Clear 的 operation，当前 requester root 相比 one-way 增加：

```text
Delta_ClearResp = 2*q_N*tau
```

| N | 平均额外 Clear RTT |
|---:|---:|
| 2 | 410 ns |
| 4 | 615 ns |
| 8 | 717.5 ns |

固定 remote Home 时为约 820 ns。

甲方 requester Ack 和 OurCC ClearReq 使双方 Home release path 同阶；差异来自 requester 是否等待 Home 的 Ack-of-Ack。

---

## 9. O3 约束

O3 不改变消息 DAG，但要求 Ack 的本地语义可实现：

1. InvAck 前，本地 old permission 必须已撤销。
2. speculative load 如果命中旧副本，必须被 invalidation squash/re-execute。
3. dirty old owner Ack 前，旧数据必须被捕获或安全写回。
4. requester receipt-Ack 后，后续同址 snoop必须受 OrderedInstallGuard 约束。
5. DMB/DSB completion 不应仅等 CPU retire；需要对应 local install 或 global commit contract。

纯理论 latency 可令未配置的 local guard/install latency近似 0；仿真必须实现或显式断言这些约束。

---

## 10. 仿真计划

在 `v5-o3` 基础上建立 `v5-ha`：

### 10.1 OurCC profile

```text
clear_profile = lossless_oneway
directory_profile = compressed_epoch | compressed_no_stable_epoch
```

目标：

1. requester 不同步等待 ClearResp；
2. Home 收 one-way Clear 后 commit/release；
3. pending-install ordering 对 O3 可见；
4. 压缩 tag/entry 配置可输出实际 capacity。

### 10.2 HA baseline profile

```text
protocol = VI_BITMAP_HA
bitmap_bits = N
write_policy = writeback
write_requires_home_invalidation = true
same_address_ordering = customer_external_contract
home_release_after_requester_ack = true
dirty_bit = false
overflow = shrink_range | broadcast
direct_transfer = false
```

### 10.3 统一 scenario

1. Home latest clean read。
2. sole holder read。
3. multi-clean holder read。
4. no-sharer write。
5. single-sharer write。
6. multi-sharer write。
7. repeated same-writer write。
8. dirty ownership handoff。
9. O3 invalidation/speculation qualification。
10. capacity exact-hit、offload 和 broadcast fallback。

### 10.4 统一计时点

```text
request_start
permission_resp_received
requester_ack_posted
home_ack_received
home_lock_released
requester_root_complete
```

不得用 OurCC-only Outer trace 替代两方案共同 completion point。

---

## 11. Brief Summary

- 理论上删除同步 `ClearResp` 是安全和必要的；保留单向 requester Ack。
- HA receipt-Ack 与 OurCC one-way Clear 在 Home release 路径上同构。
- 当前 OurCC 代码尚未把 Clear 绑定到 local install；理论可用 OrderedInstallGuard，仿真必须实现或断言。
- 在强 ordered/lossless、单 outstanding、无 crash 模型中，可删除 stable Epoch，不考虑 TxnToken。
- HA N-bit bitmap 的片上 exact capacity更大；OurCC 的扩展优势来自 metadata offload，而不是单条 resident entry 更小。
- Exact range 内，首次冲突路径大多相等；OurCC 的核心优势是 repeated E/M write，以及 HA 无 dirty/latest 状态时的 sole-holder ambiguity。
- Broadcast fallback 下，OurCC 的 49~90 ns metadata offload通常小于一个 410 ns fabric leg，但理想 multi-sharer broadcast可能与 exact invalidation同阶。
- 最终平均不能在当前阶段冻结；必须输出 scenario vector，并由客户/合同冻结 workload weights。

---

## 12. 已闭合信息与未闭合确认项

> **C1：HA sole-holder clean/dirty 区分。** 没有 dirty bit时，HA 是否总是联系 sole holder，还是存在其他 Home-data-valid/owner-mode 状态？

> **C2（未闭合）：** 客户外部 HA ordering source/可审计证据仍不可获得。当前比较只能条件化地假设其在 requester Ack 前排队后续同地址请求；在获得源码、形式接口或可复现实验证据前，不得把该 ordering 能力标为已验证。Requester 侧仍需确认 Resp received 到实体 install 完成的窄窗口是否有 transient install-pending context。

> **C3：OurCC install hook。** 最后一个 CompData 注入只有在 ordered HN-F same-address TBE contract 下才足够；否则仍需从 requester CompAck/HN-F TBE retire 导出真实 `InstallDone`，或实现 pending-install guard。

> **C4：NO_STABLE_EPOCH 条件。** InvAck/RecallResp 后是否绝不再产生旧 WB/Evict？若不能证明，必须保留 compact lease/generation。

> **C5：DSB/atomic completion。** requester install 即完成，还是必须等待 Home release？建议普通请求 one-way，强 barrier使用累计 commit barrier。

> **C6：Broadcast 物理实现。** 是否真正 single-cycle multicast/parallel Ack，还是存在 per-destination injection 和 Ack aggregation成本？

> **C7：最终 workload weights。** 当前不冻结；正式比较需由合同给出场景比例或 workload trace。
