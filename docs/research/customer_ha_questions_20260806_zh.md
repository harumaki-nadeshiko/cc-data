# 甲方 HA 最小确认问题清单

**日期：** 2026-08-06  
**目的：** 用不暴露 RTL/私有微架构的抽象回答，冻结合同目标 3 的合法比较分支。  
**答法：** 可选择选项、给区间，或标 `不披露但同意采用保守分支`。

| # | 问题与可选回答 | 为什么必须问 | 对判定的影响 |
|---:|---|---|---|
| 1 | **peer direct response 能否独立授予 requester 所需权限？** A 仅 data；B data+由 HA 预授权 token/version；C peer 自身即 authority；D 不 direct。 | 决定 remote owner 路径能否从 central `K约4` 缩为 direct-authority `K约3`。 | B/C 对 OurCC 最不利；A 仍需 Home Grant；D 属 central-return。 |
| 2 | **每条线的 metadata 原子 commit 事件是什么？** A 发 Grant 前/同时；B 收 peer completion；C 收 requester install/completion；D 其他。 | 定义 `T_commit` 和同址 serialization。 | A/B 可使 HA commit 与数据/Grant 重叠；C 增加关键链。 |
| 3 | **对外 root completion 保证到哪一点？** A requester 数据/权限 visible；B HA metadata committed；C 下一冲突事务可安全开始；D ISA store/release/fence 完成。 | 防止双方在不同停止点计时。 | 只回答 A 时仍必须另报 B/C；共同主点才能合同比较。 |
| 4 | **HA/Home 的物理位置和地址映射？** A 与 N0；B 与 N1；C 按地址分布/交错；D IODie。 | 把逻辑 edge 映射为真实 `K_crossnode`。 | H@requester 可让某些 edge 本地；H@peer 可让 Clear/response 增加跨节点往返。 |
| 5 | **请给 HA service 的抽象界。** 无争用与合同负载下分别给 `P_dir+P_commit` 和 `P_queue` 的 cycle 区间，或提供共同 black-box counter。 | 同 K 时严格 `<` 完全由 P 决定。 | 有界可做 break-even；仅“lookup约0”不足，缺失则 `UNPROVEN`。 |
| 6 | **write policy 是？** A write-through；B write-back；C hybrid。root completion 时 memory 是否保证 latest？ | 决定 home-memory fast path、dirty handoff 与流量权重。 | WT 通常提高 HA `R_h` 比例；WB 需 owner 定位/transfer。 |
| 7 | **约 2-bit 之外，dirty/latest owner 如何定位？** A 额外 exact owner/dirty；B unique-owner invariant+HN query；C probe 两节点；D memory 总是 latest；E 其他。 | 2-bit presence 本身不能普遍区分 clean/dirty/latest。 | A/D 支持更快下界；B/C 增加 service/依赖。 |
| 8 | **旧 sharer/owner 失效的可验证 completion 是？** A explicit Ack 回 HA；B peer completion 直达 requester；C fabric implicit completion；D timeout/lease。 | “没有 Ack 包”不等于没有 completion dependency。 | 可证明的 B/C 可能缩短路径；无保证的 implicit 分支不合法。 |
| 9 | **同址并发用什么 serialization identity？** A per-line lock；B version/epoch；C pending-install token；D serial queue；E 组合。stale/duplicate completion 如何拒绝？ | 防止双 owner、ABA、late response 覆盖新事务。 | 有可验证 token 才能纳入 direct-authority fast path。 |
| 10 | **data/permission 路由按什么规则切换？** 对 `R_h/R_o/W_s/W_o` 分别选 central、direct-data-only、direct-data+authority；说明 fallback。 | 平均值需要合法 branch weights。 | 可形成可审计 DAG；不回答则无法计算理论均值。 |
| 11 | **2-bit 的四个稳定码字是什么？transient state 存哪里？** 可只给抽象状态。 | 区分 presence、exclusive/shared、dirty 和 pending。 | 收紧状态能力上下界；避免把 VI 当 dirty-owner。 |
| 12 | **合同 counter stop 对以下 API 各对应什么事件？** ordinary load/store、release/acquire、Arm DMB/DSB、RISC-V FENCE。 | posted accept/fabric response 可能早于 architected completion。 | 语义一致才可比较；否则调整 stop point并运行 litmus。 |
| 13 | **争用/资源压力策略？** A stall/credit backpressure；B NACK/retry；C bounded queue；D 混合。请给 queue limit、retry ID 和统计口径。 | 定义 `C` 类别和尾延迟，避免只测空载。 | no-fault idle 单列；contention profile 共同计权。 |
| 14 | **non-FIFO 下如何关联和去重？** 给出 transaction identity 的稳定范围、duplicate response 是否可能、timeout/replay 层次。 | 不能依赖发送顺序推断 Recall/Grant 到达顺序。 | 支持安全下界与 fault 分域。 |
| 15 | **请确认共同工作负载。** 给 `R_h/R_o/W_s/W_o/M/C` 权重、地址/Home placement、并发度、warm-up、测量窗和 seed。 | `T_mean=sum(w_i*T_i)` 没有权重就无唯一值。 | 冻结后才能计算 overall；双方不得单边混入 local hit 稀释。 |

## 建议答复格式

```text
Q#: 选项 / 区间 / 不披露但同意采用的保守分支
适用操作：R_h, R_o, W_s, W_o, M, C
完成点：T_visible / T_commit / T_next / ISA root
例外或 fallback：...
证据形式：接口定义 / black-box counter / 设计说明 / 共同试验
```

如 Q1-Q5 未关闭，整体维持 `UNPROVEN`。任何未回答项都不默认采用对 OurCC 或甲方
更有利的值，而是在结论矩阵中同时保留合法上下界。
