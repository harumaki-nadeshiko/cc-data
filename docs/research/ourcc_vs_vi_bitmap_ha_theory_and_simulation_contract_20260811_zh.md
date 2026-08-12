# OurCC 与 VI+Sharer-Bitmap HA 理论对比及仿真合同

**日期：** 2026-08-11  
**状态：** 理论模型冻结稿；2026-08-13 修正 Scheme A/B、Tag 和 metadata locality；仿真结果尚未填写
**目标 3 当前合同状态：** `UNPROVEN`；仅明确标注的 sensitivity 参数点为 PASS
**目标：** 在不计传输故障、不计瞬态 TxnToken 面积、暂不考虑 direct transfer 的条件下，比较 OurCC 与甲方 HA 的 requester-visible、Home release 和目录扩展性能。

---

## 0. 真正关键的结论

> **K1：HA 的 requester Ack 对应 OurCC 的 `ClearReq`，不是 `ClearResp`。**

> **K2：可以删除 requester 对同步 `ClearResp` 的等待，但不能删除 requester 到 Home 的 Ack/Clear。Home 仍须在收到 Ack 后才能提交并释放同址锁。**

> **K3：纯理论 ordered/lossless 模型下可以不考虑 TxnToken，也可以删除长期 Epoch；但必须冻结为“每行单 outstanding、消息 exactly-once、同址有序、Ack 后旧权限不再产生任何消息、测试期间无节点重启”。**

> **K4：当前实现的 Clear 发在本地 HN/L2 最终 CompData/install 之前。仿真若采用 receipt-Ack 模型，必须增加 per-line pending-install ordering。最后一个 CompData 注入仅在冻结的 HN-F“同地址 TBE 有序且后续同址事务不能越过该 TBE”合同下足够；否则不能把注入点泛化为真实 InstallDone。**

> **K5：甲方没有 dirty bit 时，write-back 最新数据位置不能从一般 bitmap 唯一判定。最小安全约定是：多个 holder 必为 clean 且 Home data latest；unique write 后只剩一个 potential latest holder，其他节点读取时必须联系该 holder，或引入未披露的等价状态。**

> **K6：甲方“每次写都触发 invalidation/HA transaction”使其无法复用本地写权限。OurCC 的 E/M repeated write 是最稳定的理论优势。**

> **K7：目标 3 必须分别评审 Scheme A 和 Scheme B。Scheme A 缩小双方共同地址空间并同步压缩
> OurCC Tag；Scheme B 保留 128 MiB、每行固定 2-bit coarse state，broadcast 是正常协议路径。**

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

持久 metadata 分为两个 ScaleScheme：

```text
Scheme A:
    remote sharer bitmap = N - 1 bits
    local VI state       = 1 bit
    total                = N bits / represented Cacheline
    coherent range       = 256/N MiB

Scheme B:
    coarse metadata      = 2 bits / represented Cacheline
    coherent range       = 128 MiB for N=2/4/8
    every in-scope coherence transaction = broadcast/probe for N=2/4/8
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
Scheme A exact N-bit metadata:
    holder count = 0:
        Home data latest
    holder count > 1:
        all holders clean
        Home data latest
    unique-write ownership:
        exact bitmap only retains requester
        requester may hold latest dirty data

Scheme B coarse 2-bit metadata:
    persistent state does not identify an exact holder set for N>2
    read uses broadcast probe to locate any potential latest data
    write uses broadcast invalidation and waits required completion
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

Scheme A 每次 write：

```text
Requester -> HA: AcquireUnique
HA locks line
HA invalidates all exact bitmap holders except requester
HA waits all required InvAck
HA -> Requester: permission Resp
Requester -> HA: receipt Ack
HA releases lock
```

Scheme B 每次 write：

```text
Requester -> HA: AcquireUnique
HA locks line
HA broadcasts invalidation/probe to all possible remote nodes
HA waits all required completion
HA -> Requester: permission Resp
Requester -> HA: receipt Ack
HA releases lock
```

即使 requester 已是唯一实际 holder，题设仍要求每次 write 经过 HA transaction。Scheme A 的 exact
bitmap允许 target mask为空，但 requester-Home round trip仍存在；Scheme B 始终执行 normal
broadcast/invalidation transaction。

---

## 5. 实际延迟参数

| 参数 | 数值 | 来源 |
|---|---:|---|
| CPU cycle | 0.5 ns | `scripts/solve_latency_params.py` |
| L1 tag+data | 1.5 ns | 同上 |
| Core to local L3 hit | 15 ns | 目标 CPU 配置 |
| Local DRAM | 100 ns | 同上 |
| NUMA DRAM | 110 ns | 同上 |
| 单向跨节点 `tau` | 约 410 ns | trace/report |
| Remote ReadShared | 896 ns | `reports/metrics_latency_report.md` |
| Upgrade | 890 ns | 同上 |
| ReadUnique current | 1728 ns | 同上 |
| Historical MetaRNF warm path | 49 ns | 旧报告端到端路径值，不作为新模型参数 |
| Historical MetaRNF cold path | 90 ns | 旧报告端到端路径值，不作为新模型参数 |

本地协议 residual：

```text
P_read     = 896 - 2*410 = 76 ns
P_inv      = 890 - 2*410 = 70 ns
P_handoff ~= 88 ns
```

`49 ns` 和 `90 ns` 是旧版 MetaRNF 端到端报告值，不是 L3 array hit和 DRAM本体延迟；仓库也未
归档对应 raw samples。当前代码使用 `ReadOnce` 路径，而旧报告描述的是可分配 `ReadShared` 路径，
因此两值存在版本漂移，不能继续作为冻结参数。

按本次硬件假设：CPU 有 64 个 1 MiB L3 slices，总计 64 MiB；忽略 slice内部接线，Core访问任意
L3 slice为 15 ns。理论 metadata onload使用：

```text
L_meta_L3  = 15 ns
L_meta_DRAM = 100 ns
```

这里的 100 ns定义为从 metadata access发起到 DRAM数据返回的完整 Core-visible cold endpoint，已包含
L3 lookup/miss detection，不再额外叠加 15 ns。若后续平台把 100 ns定义成 miss后的纯 DRAM service，
则 cold endpoint必须改为 115 ns并重算全部 sensitivity。

对 micro-scenario `j`，使用：

```text
q_on,j = 实际触发 MetaRNF demand onload 的概率
h_j    = 已触发 onload 时的 HN-F L3 hit probability
Q_j    = MetaRNF/L3/DRAM queueing 项

L_on,j = 15*h_j + 100*(1-h_j) + Q_j
P_onload,j = q_on,j * L_on,j + Wcrit_j
```

`Wcrit_j` 是 demand-visible metadata victim writeback项。历史报告把 writeback描述为 fire-and-forget，
所以 sensitivity 表取 `Wcrit_j=0`；正式评审必须验证该写回确实不阻塞 requester-visible path，并把其
queue interference计入 `Q_j`，否则需填入非零 `Wcrit_j`。

三个可直接展示的数值边界为：

| metadata path | `q_on` | `h` | demand penalty |
|---|---:|---:|---:|
| ResidentDir hit / Bloom authoritative negative | 0 | N/A | 0 ns |
| forced L3-warm onload | 1 | 1 | 15 ns + `Q` |
| forced DRAM-cold onload | 1 | 0 | 100 ns + `Q` |

地址空间缩小和 Tag 缩短会提高 ResidentDir coverage，并通常缩小 metadata working set、提高 `h`；
但 `q_on` 和 `h` 仍取决于具体 working set、reuse distance、set conflict 和 Bloom 结果，不能只由容量比
直接推出。

### 5.1 64 MiB L3 的 Metadata 分配变量

定义：

```text
C_L3_total = 64 MiB
C_meta     = metadata允许使用的 L3 容量
C_data     = 64 MiB - C_meta
0 <= C_meta <= 64 MiB
```

物理变量允许到 64 MiB。评审主扫描只需到 32 MiB，因为 H64 每个 64B bucket含 5 个 12B slots；
按 80% hash load factor，当前最大完整 metadata footprint为：

```text
F_meta = cachelines / (5 * 0.8) * 64 B
```

| Scheme | N=2 | N=4 | N=8 |
|---|---:|---:|---:|
| A metadata footprint `F_A(N)` | 32 MiB | 16 MiB | 8 MiB |
| B metadata footprint `F_B(N)` | 32 MiB | 32 MiB | 32 MiB |

在无 conflict额外需求的容量参考模型中，`C_meta>F_meta` 不再提高 metadata capacity hit率，只会继续
减少普通数据可使用的 L3；若实测 replacement/conflict显示 32 MiB后仍有收益，再扩展扫描到 64 MiB。

正式 `h_j(C_meta)` 仍应由 metadata reuse distance或 counter得到。为提供可复算的容量敏感性，定义
一个均匀工作集参考模型：

```text
h_uniform(C_meta, F_meta) = min(1, C_meta/F_meta)
L_on_uniform(C_meta) = 100 - 85*h_uniform(C_meta,F_meta) + Q
```

该线性模型不是 cache replacement定律，只用于参数扫描。真实场景边界仍为：

- repeat-one-bucket：warm-up 后 `h` 接近 1，即使 `C_meta` 很小；
- metadata working set `<=C_meta`：warm-up 后 `h` 接近 1；
- streaming 或 reuse distance `>C_meta`：`h` 接近 0。

### 5.2 普通数据 L3 机会成本

metadata占用 L3 不能视为免费。令 `A_data,j` 为每个 root operation处于关键路径的普通 data access数，
`m_data,j(C_data)` 为普通数据可用 L3为 `C_data` 时的 miss probability。单关键访问、无 MLP/O3 overlap、
新增 miss只落到本地 DRAM的参考估算为：

```text
P_steal_est,j(C_meta)
  = A_data,j * [m_data,j(64-C_meta) - m_data,j(64)] * (100-15)
  = A_data,j * Delta_m_data,j(C_meta) * 85 ns
```

该式不是 coherence workload的一般等式。正式判定使用 paired requester-visible latency：

```text
P_steal_measured,j(C_meta)
  = T_data_path,j(C_data=64-C_meta | metadata replay fixed)
    - T_data_path,j(C_data=64 MiB | same metadata replay)
```

shadow-cache miss curve只用于 `P_steal_est`。正式 `P_steal_measured` 必须保持 metadata请求流和
metadata latency固定并做 paired replay；operation mix、seed和 placement必须一致。若 paired差已经
包含 metadata/data queue interference，不得再在 `Q_j` 中重复计数。

完整 OurCC 附加项：

```text
P_add,j(C_meta)
  = q_on,j * [100 - 85*h_j(C_meta) + Q_j]
    + Wcrit_j
    + P_steal,j(C_meta)
```

参考/sensitivity中，`P_steal` 必须明确写成 `0` 或 `P_steal_est`；正式合同判定中，
`P_steal := P_steal_measured`。

选择 `C_meta` 的原则不是越大越好，而是最小化：

```text
metadata onload penalty + normal-data opportunity cost
```

在 `C_meta<F_meta` 的均匀参考模型中，每增加 1 MiB metadata L3 的边际收益为：

```text
85 * q_on_mean / F_meta   ns/op/MiB
```

只有当普通数据机会成本的边际增长小于该值时，继续增加 `C_meta` 才有净收益。

在第 7.6 节八场景等权、七项 onload参考中，`q_on_mean=7/8`，边际 metadata收益为：

| Scheme | N=2 | N=4 | N=8 |
|---|---:|---:|---:|
| A | 2.324 ns/op/MiB，至 32 MiB | 4.648 ns/op/MiB，至 16 MiB | 9.297 ns/op/MiB，至 8 MiB |
| B | 2.324 ns/op/MiB，至 32 MiB | 2.324 ns/op/MiB，至 32 MiB | 2.324 ns/op/MiB，至 32 MiB |

因此优化问题为：

```text
C_meta* = argmin_C Delta_mean(C)
subject to 0 <= C <= 64 MiB
           normal_data_slowdown(C) <= agreed_limit
```

离散评审中选择满足严格负 Delta和普通数据退化限制的最小扫描点；不预设 8 MiB或 32 MiB。

### 5.3 Address-sliced Home

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

## 6. ScaleScheme A：缩小共同地址空间，增加每行位宽

### 6.1 定义

Scheme A 在固定 512 KiB HA SRAM 下，把共同 coherent address space 缩小，使每个 cacheline 可以保留
完整 `N bit` 节点状态：

```text
HA bits/line = local VI 1 bit + remote presence (N-1) bits = N bits
M_A(N) = (512 KiB * 8 / N) * 64 B = 256/N MiB
```

| N | 共同地址空间 | cachelines | HA bits/line |
|---:|---:|---:|---:|
| 2 | 128 MiB | 2,097,152 | 2 |
| 4 | 64 MiB | 1,048,576 | 4 |
| 8 | 32 MiB | 524,288 | 8 |

OurCC 必须暴露同一个缩小后的地址空间，并同步减少 tag width，不能继续按默认 40-bit PA 计算。

### 6.2 OurCC Tag 和容量模型

共同地址空间的 cacheline-address bits：

```text
L_A(N) = log2(M_A(N)/64 B) = 22 - log2(N)
tag_bits = L_A(N) - set_bits
```

这与实现一致：`ResidentDir.cc` 使用 `tag_bits = cl_addr_bits - set_bits`。旧版
`tag ~= ceil(log2 ways)` 不适用于“ResidentDir + metadata offload”模型，已废弃。

OurCC 目录预算采用当前结构的 448 KiB：512 KiB 减 60 KiB Bloom 和 4 KiB GroupIndex。对 `W` ways、
`S=2^s` sets：

```text
entry_bits = 7 + N + epoch_bits + (L_A(N)-s)
plru_bits  = 2^ceil(log2(W)) - 1
A_used     = 2^s * (W*entry_bits + plru_bits) <= 448 KiB * 8
capacity   = W * 2^s
```

按实现搜索范围 `W=2..32` 重算：

| N | Profile | layout | tag bits | entry bits | capacity | resident bytes | coverage |
|---:|---|---|---:|---:|---:|---:|---:|
| 2 | EPOCH24 | `5 x 2^14` | 7 | 40 | 81,920 | 5.0 MiB | 3.906% |
| 4 | EPOCH24 | `5 x 2^14` | 6 | 41 | 81,920 | 5.0 MiB | 7.813% |
| 8 | EPOCH24 | `9 x 2^13` | 6 | 45 | 73,728 | 4.5 MiB | 14.063% |
| 2 | NO_STABLE_EPOCH | `2 x 2^17` | 4 | 13 | 262,144 | 16 MiB | 12.5% |
| 4 | NO_STABLE_EPOCH | `3 x 2^16` | 4 | 15 | 196,608 | 12 MiB | 18.75% |
| 8 | NO_STABLE_EPOCH | `5 x 2^15` | 4 | 19 | 163,840 | 10 MiB | 31.25% |

这些是理论压缩配置，不是当前默认 `pa_bits=40` binary 的输出。

### 6.3 Scheme A micro-scenario 数值

下表先展示双方 exact/resident-hit 的 requester-visible 基础值。OurCC 若触发 metadata onload，再按
该场景加 `P_onload,j`，而不是统一加 69.5 ns。

每格为 `HA / OurCC exact / Delta exact(Our-HA)`：

| Scenario | N=2 | N=4 | N=8 |
|---|---:|---:|---:|
| Home latest clean read | `510 / 510 / 0` | `715 / 715 / 0` | `817.5 / 817.5 / 0` |
| sole clean holder read | `896 / 510 / -386` | `1306 / 715 / -591` | `1511 / 817.5 / -693.5` |
| sole dirty/latest holder read | `896 / 896 / 0` | `1306 / 1306 / 0` | `1511 / 1511 / 0` |
| cold/no-sharer write | `510 / 510 / 0` | `715 / 715 / 0` | `817.5 / 817.5 / 0` |
| single-sharer write | `890 / 890 / 0` | `1300 / 1300 / 0` | `1505 / 1505 / 0` |
| multi-sharer write | `890 / 890 / 0` | `1505 / 1505 / 0` | `1607.5 / 1607.5 / 0` |
| repeated same-writer write | `425 / 1.5 / -423.5` | `630 / 1.5 / -628.5` | `732.5 / 1.5 / -731` |
| dirty ownership handoff | `908 / 908 / 0` | `1318 / 1318 / 0` | `1523 / 1523 / 0` |

任一 OurCC scenario 的最终值为：

```text
T_OurCC,A,j  = T_exact,j + P_add,A,j
P_onload,A,j = q_on,A,j * [15*h_A,j + 100*(1-h_A,j) + Q_A,j] + Wcrit_A,j
P_add,A,j    = P_onload,A,j + P_steal,A,j(C_meta)
```

Scheme A 的缩小地址空间和 Tag 压缩会提高 ResidentDir coverage，并可提高 metadata L3 locality；正式
评审必须为每个 scenario 报告 `q_on,A,j`、`h_A,j`、warm/cold 样本，而不是把 coverage 当作请求概率。

---

## 7. ScaleScheme B：固定 128 MiB，2-bit coarse state 正常 Broadcast

### 7.1 定义

Scheme B 对所有 N 保留相同 128 MiB coherent address space，每行仍只有 2 bit：

```text
address space = 128 MiB = 2,097,152 cachelines
HA bits/line  = 2
HA SRAM       = 2,097,152 * 2 bits = 512 KiB
```

对 N>2，这 2 bit 只能表达 local-valid 与 coarse remote-present/summary，不能定位具体远端 holder。
因此 broadcast/probe 是正常 coherence 路径，不是地址 overflow fallback。

按本次修正后的 Scheme B 合同，2-bit coarse state不提供可跳过 broadcast 的精确 negative；所有被计入
目标 3 的 coherence transaction都执行 normal broadcast/probe。

### 7.2 OurCC 固定 128 MiB Tag 和容量

Scheme B 对所有 N 使用：

```text
L_B = log2(128 MiB / 64 B) = 21
tag_bits = 21 - set_bits
```

| N | Profile | layout | tag bits | entry bits | capacity | coverage of 128 MiB |
|---:|---|---|---:|---:|---:|---:|
| 2 | EPOCH24 | `5 x 2^14` | 7 | 40 | 81,920 | 3.906% |
| 4 | EPOCH24 | `5 x 2^14` | 7 | 42 | 81,920 | 3.906% |
| 8 | EPOCH24 | `9 x 2^13` | 8 | 47 | 73,728 | 3.516% |
| 2 | NO_STABLE_EPOCH | `2 x 2^17` | 4 | 13 | 262,144 | 12.5% |
| 4 | NO_STABLE_EPOCH | `3 x 2^16` | 5 | 16 | 196,608 | 9.375% |
| 8 | NO_STABLE_EPOCH | `5 x 2^15` | 6 | 21 | 163,840 | 7.813% |

### 7.3 HA Broadcast DAG

Read/probe：

```text
Requester -> Home
Home -> all possible remote nodes: Broadcast Probe
all nodes -> Home: ProbeResp / optional latest Data
Home -> Requester: Data/Resp
Requester -> Home: Ack
```

Write-invalidate：

```text
Requester -> Home
Home -> all possible remote nodes: Broadcast Invalidate
all nodes -> Home: required InvAck
Home -> Requester: permission Resp
Requester -> Home: Ack
```

在 fanout 全并行、不计 injection/aggregation/queue 的 HA 乐观下界中：

```text
T_BcastRead(N)  ~= K_m(N)*tau + 76 ns
T_BcastWrite(N) ~= K_m(N)*tau + 70 ns
K_m(2/4/8)      = 2.0 / 3.5 / 3.75
```

| N | Broadcast read/probe | Broadcast write-invalidate |
|---:|---:|---:|
| 2 | 896 ns | 890 ns |
| 4 | 1511 ns | 1505 ns |
| 8 | 1613.5 ns | 1607.5 ns |

正式理论比较保留这一对 HA有利的乐观下界。实测 broadcast injection、Ack aggregation和 queueing
只作为补充披露，不用更慢实测替换该下界来制造 OurCC PASS。

### 7.4 Scheme B micro-scenario 数值

下表使用 EPOCH24 OurCC exact/resident-hit 值。OurCC metadata miss仍需另加 `P_onload,B,j`，并计入
普通数据机会成本 `P_steal,B,j`。

每格为 `HA coarse-broadcast / OurCC exact / Delta exact(Our-HA)`：

| Scenario | N=2 | N=4 | N=8 |
|---|---:|---:|---:|
| Home latest clean read，需要 probe | `896 / 510 / -386` | `1511 / 715 / -796` | `1613.5 / 817.5 / -796` |
| sole clean holder read | `896 / 510 / -386` | `1511 / 715 / -796` | `1613.5 / 817.5 / -796` |
| dirty/latest holder read | `896 / 896 / 0` | `1511 / 1306 / -205` | `1613.5 / 1511 / -102.5` |
| cold/no-sharer write，需要 broadcast | `890 / 510 / -380` | `1505 / 715 / -790` | `1607.5 / 817.5 / -790` |
| single-sharer write | `890 / 890 / 0` | `1505 / 1300 / -205` | `1607.5 / 1505 / -102.5` |
| multi-sharer write | `890 / 890 / 0` | `1505 / 1505 / 0` | `1607.5 / 1607.5 / 0` |
| repeated same-writer write | `890 / 1.5 / -888.5` | `1505 / 1.5 / -1503.5` | `1607.5 / 1.5 / -1606` |
| dirty ownership handoff | `908 / 908 / 0` | `1523 / 1318 / -205` | `1625.5 / 1523 / -102.5` |

OurCC 最终值：

```text
T_OurCC,B,j  = T_exact,j + P_add,B,j
P_onload,B,j = q_on,B,j * [15*h_B,j + 100*(1-h_B,j) + Q_B,j] + Wcrit_B,j
P_add,B,j    = P_onload,B,j + P_steal,B,j(C_meta)
```

Scheme B 固定 128 MiB，因此 Tag 不随 N 缩短；metadata working set 通常也大于 Scheme A，`q_on,B,j`
和 cold fraction预期更高，但仍必须由 micro-scenario counter 或冻结假设给出。

### 7.5 Micro-scenario 加权评审方法

最终评审不应先把所有路径压成一个抽象 `w_p`。应逐项提供：

| 字段 | 含义 |
|---|---|
| `scenario` | 第 7.5.1 节冻结的唯一 scenario ID |
| `scheme` | A 或 B |
| `N` | 2、4、8 |
| `T_HA` | 对应 HA exact/broadcast requester-visible latency |
| `T_Our_exact` | OurCC resident-hit latency |
| `q_on` | 该 scenario 的 metadata demand onload率 |
| `h_L3` | onload 条件下 HN-F L3 hit率 |
| `C_meta` | metadata允许使用的 L3 容量；`C_data=64-C_meta` |
| `P_onload` | `q_on*[15*h+100*(1-h)+Q] + Wcrit` |
| `Wcrit` | demand-visible metadata victim writeback；若为 0 必须给出异步证明 |
| `P_steal` | metadata挤占普通数据 L3造成的 paired latency增加 |
| `P_add` | `P_onload + P_steal(C_meta)` |
| `T_Our` | `T_Our_exact + P_add` |
| `Delta` | `T_Our - T_HA` |
| `weight` | 评审冻结的 scenario 权重 |

平均值：

```text
T_mean_HA  = sum_j(weight_j * T_HA,j)
T_mean_Our = sum_j(weight_j * T_Our,j)
Delta_mean = sum_j(weight_j * Delta_j)

STRICT PASS iff Delta_mean < 0
```

每个 scenario 至少展示 resident-hit、15 ns L3-warm onload 和 100 ns DRAM-cold onload 三个边界；
正式加权单值必须同时提供 `C_meta/q_on/h_L3/Q/Wcrit/P_steal` 和权重来源。

#### 7.5.1 唯一 Micro-Scenario 清单

| ID | 场景 | 说明 |
|---|---|---|
| `R_HOME` | Home latest clean read | 包括 no-holder 和 multi-clean holder；Home data latest |
| `R_SOLE_CLEAN` | sole clean holder read | Scheme A exact holder但无 dirty bit；OurCC MESI知道 Home latest |
| `R_SOLE_DIRTY` | sole dirty/latest holder read | 需要取得 potential latest data |
| `W_COLD` | cold/no-sharer first write | HA 仍执行题设要求的 write transaction |
| `W_SINGLE` | single-sharer writer acquire | 一个实际远端 sharer |
| `W_MULTI` | multi-sharer writer acquire | 多个远端 sharer，fanout并行下界 |
| `W_REPEAT` | repeated same-writer write | OurCC E/M local reuse；HA 每次写仍事务化 |
| `W_HANDOFF` | dirty ownership handoff | 旧 owner释放 latest data和权限 |

#### 7.5.2 待定参数的关闭方法

| 参数 | 定义/测量方法 |
|---|---|
| `C_meta` | 扫描 0/4/8/16/32 MiB；选择加入 `P_steal` 后 `Delta_mean` 最小且普通数据退化满足限制的点 |
| `q_on,j` | `MetaRNF demand read count / root operation count`，按 scenario分别统计 |
| `h_L3,j` | `MetaRNF HN-F L3 hit count / MetaRNF demand access count` |
| `Q_j` | `observed onload latency - [15*h_j + 100*(1-h_j)]`，报告 mean/P50/P95/P99 |
| `Wcrit_j` | 有/无 metadata victim writeback 的 paired requester-visible latency差值 |
| `P_steal,j` | 同 workload在 64 MiB data-only 与 `64-C_meta` 普通数据容量下的 paired requester-visible latency差值 |
| `w_j` | 同时报八 scenario等权和合同/trace权重；最终签署使用后者 |

metadata locality诊断至少覆盖 repeat-one-bucket、working set `<=C_meta`、working set `>C_meta`
streaming。每个点记录 L3 aggregate容量、metadata reservation、普通 data reservation、warmup、seed、
hit/miss和 latency分布。

### 7.6 `C_meta` 等权 sensitivity 扫描

仅用于展示评审格式，取第 7.5.1 节八个 scenario 各 `1/8`。除 `W_REPEAT` 外，其余七项全部发生
onload；采用均匀 footprint参考 `h=min(1,C_meta/F_meta)`，并取 `Q=0`、`Wcrit=0`、`P_steal=0`。
因此该表是无普通数据机会成本的 metadata-only sensitivity，不是合同平均。

表中为 `Delta_mean = OurCC - HA`，单位 ns/op：

| Scheme | N | `C_meta=0` | 4 MiB | 8 MiB | 16 MiB | 32 MiB |
|---|---:|---:|---:|---:|---:|---:|
| A | 2 | -13.7 | -23.0 | -32.3 | -50.9 | -88.1 |
| A | 4 | -64.9 | -83.5 | -102.1 | -139.3 | -139.3 |
| A | 8 | -90.6 | -127.8 | -164.9 | -164.9 | -164.9 |
| B | 2 | -167.6 | -176.9 | -186.2 | -204.8 | -241.9 |
| B | 4 | -475.1 | -484.4 | -493.7 | -512.3 | -549.4 |
| B | 8 | -449.4 | -458.7 | -468.0 | -486.6 | -523.8 |

`C_meta=8 MiB` 可作为低占用参考点：只占总 L3 的 12.5%，保留 56 MiB 给普通数据；在均匀参考
模型下，Scheme A metadata footprint覆盖为 N=2/4/8 的 25%/50%/100%，Scheme B 为 25%。但正式
结论不固定 8 MiB，必须加入 `P_steal(C_meta)` 后从扫描点中选择净收益最优值。

在该明确等权参考中，即使 `C_meta=0` 且七个 onload全部走 100 ns DRAM，Scheme A/B N=2/4/8
仍为负 Delta。L3 metadata allocation用于扩大余量，不是为了把结果从 FAIL 调成 PASS。

若评审将 100 ns解释为 L3 miss后的纯 DRAM service，则保守 cold endpoint为 115 ns。最脆弱的
Scheme A N=2等权点变为：

| `C_meta` | 0 MiB | 4 MiB | 8 MiB | 16 MiB | 32 MiB |
|---|---:|---:|---:|---:|---:|
| `Delta_mean`, 115 ns cold | -0.6 | -11.5 | -22.4 | -44.3 | -88.1 |

该压力测试仍为负 Delta，但 `C_meta=0` 只剩 0.6 ns/op余量，说明少量 metadata L3可提高鲁棒性。

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
write_policy = writeback
write_requires_home_invalidation = true
same_address_ordering = customer_external_contract
home_release_after_requester_ack = true
dirty_bit = false
direct_transfer = false

scale_scheme = a
stable_bits_per_line = N
coherent_range_mib = 256/N
routing = exact_bitmap

scale_scheme = b
stable_bits_per_line = 2
coherent_range_mib = 128
routing = normal_broadcast_for_coarse_state
```

### 10.3 统一 scenario

1. `R_HOME`：Home latest clean read，包括 multi-clean Home-latest。
2. `R_SOLE_CLEAN`：sole clean holder read。
3. `R_SOLE_DIRTY`：sole dirty/latest holder read。
4. `W_COLD`：cold/no-sharer first write。
5. `W_SINGLE`：single-sharer writer acquire。
6. `W_MULTI`：multi-sharer writer acquire。
7. `W_REPEAT`：repeated same-writer write。
8. `W_HANDOFF`：dirty ownership handoff。
9. O3 invalidation/speculation qualification。
10. Scheme A compressed-range exact path、Scheme B normal broadcast、OurCC offload/onload。

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
- Scheme A 通过把共同地址空间压缩为 `128/64/32 MiB`，使 HA 使用 N-bit exact metadata；OurCC
  同时按共同范围缩短 Tag，并保留 metadata offload。
- Scheme B 对所有 N 保留 128 MiB，但每行只有 2-bit coarse metadata；N=2/4/8 每次纳入目标 3 的
  transaction都走 normal broadcast/probe，不是 overflow fallback。
- OurCC 的核心优势是 exact sharer/owner semantics、sole-clean 判定和 repeated E/M write；代价是
  ResidentDir miss后的 metadata onload。
- 新模型使用 64 MiB aggregate L3、15 ns L3 hit、100 ns metadata DRAM；旧 49/90/69.5 ns不作为参数。
- `C_meta` 是 0..64 MiB物理变量；当前容量主扫描为 0..32 MiB，并与普通数据 L3机会成本共同优化。
- 最终评审必须逐 micro-scenario 报告双方数值、Delta、`C_meta/q_on/h_L3/Q/Wcrit/P_steal`
  和权重，再进行加权平均。

---

## 12. 已闭合信息与未闭合确认项

> **C1：Scheme A sole-holder clean/dirty 区分。** 没有 dirty bit时，HA 是否总是联系 exact sole
> holder，还是存在其他 Home-data-valid/owner-mode 状态？Scheme B 已冻结为 coarse broadcast，不依赖
> sole-holder 精确识别。

> **C2（未闭合）：** 客户外部 HA ordering source/可审计证据仍不可获得。当前比较只能条件化地假设其在 requester Ack 前排队后续同地址请求；在获得源码、形式接口或可复现实验证据前，不得把该 ordering 能力标为已验证。Requester 侧仍需确认 Resp received 到实体 install 完成的窄窗口是否有 transient install-pending context。

> **C3：OurCC install hook。** 最后一个 CompData 注入只有在 ordered HN-F same-address TBE contract 下才足够；否则仍需从 requester CompAck/HN-F TBE retire 导出真实 `InstallDone`，或实现 pending-install guard。

> **C4：NO_STABLE_EPOCH 条件。** InvAck/RecallResp 后是否绝不再产生旧 WB/Evict？若不能证明，必须保留 compact lease/generation。

> **C5：DSB/atomic completion。** requester install 即完成，还是必须等待 Home release？建议普通请求 one-way，强 barrier使用累计 commit barrier。

> **C6：Broadcast 物理实现。** 是否真正 single-cycle multicast/parallel Ack，还是存在 per-destination injection 和 Ack aggregation成本？

> **C7：最终 operation weights。** 当前尚未签署；正式比较可由合同直接冻结场景比例或允许区域，
> 也可选择 workload/trace 作为校准来源。
