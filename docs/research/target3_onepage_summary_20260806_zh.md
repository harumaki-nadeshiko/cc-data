# 合同目标 3 一页结论表

**日期：** 2026-08-06  
**原始门槛：** OurCC 跨节点 CC 同步平均时延 `<` 甲方 HA 理论平均时延  
**当前结论：** **`UNPROVEN（存在实质性 RISK）`**

## 为什么还不能判 PASS

- 当前 OurCC `OurCC-current-clear-ack` 的安全语义清晰，但现有代码只确认 requester 协议
  代理接受 Grant，并未取得 HN/L2 明确 install Ack；随后还需 `ClearReq`，Home
  commit/retire/release 并返回 `ClearResp accepted`，EP-SNF 才完成当前 root path。相对于
  在 Grant/peer completion 即完成 commit 的合法 HA 分支，`T_commit/T_next/T_root_current`
  可能更晚。
- 两节点中 `requester/home/owner` 至少两个逻辑角色共址。四个逻辑箭头通常不是四次
  跨节点 traversal；Home placement 可让 Clear 往返全本地，也可再增加两次跨节点
  traversal。
- 没有冻结甲方分支、本地服务项 `P`、operation weights 和共同 completion API，就没有
  唯一“理论平均值”。同 `K` 只能证明同阶，不能证明严格 `<`。

## 最关键 unknown

1. **HU-03 peer authority：** direct response 是仅 data，还是独立足以授予权限？
2. **HU-05/HU-06 commit boundary：** metadata 何时原子 commit，root completion 是否等待
   global commit/next-safe？
3. **HU-09/HU-10 placement/service：** HA/Home 位于哪一节点/die，无争用与冻结负载下的
   `P_dir+P_commit+P_queue` 是多少？

HU-01 write policy 与 HU-07 dirty/latest-owner tracking 还会显著改变分支权重。

## 结论矩阵

| HA 合法分支 | Remote Read | Shared-to-Writer | Owner Handoff | 当前判定 |
|---|---|---|---|---|
| Home-memory latest，Grant 前/同时 commit | HA 可 `K=2`；OurCC visible 可同 K，但 commit/next 多 Clear | 需另看 invalidate | 不适用 | `UNPROVEN`，OurCC commit 风险 |
| central-return + explicit Ack + root 等 commit | 常见 `K=4` vs `K=4` | `K=4` vs `K=4` | `K=4` vs `K=4` | `UNPROVEN`；只有 `P_OurCC<P_HA` 且均值证据成立时才 `CONDITIONAL PASS` |
| direct-data-only + Home Grant | data 可早到，但 authority 路径仍关键 | 同理 | 同理 | `UNPROVEN`；不能把 data arrival 当 completion |
| direct-data+authority + commit 并行 | HA 可 `K=3` | 若 completion 同时授权也可更短 | HA 最有利 | 条件分支为 `RISK/FAIL`，除非 OurCC 的 P 优势超过至少 `1 tau` |
| implicit completion 但无可验证 ordering | 不合法 | 不合法 | 不合法 | `NOT APPLICABLE`，排除该假下界 |
| Proposed `OurCC-lossless-oneway` | requester 可早退，Home commit 仍需 Clear | 同左 | 同左 | 当前 `NOT APPLICABLE`：未实现 |

`T(o,x)=K(o,x)*tau+P(o,x)`；OurCC 严格更快当且仅当：

```text
(K_HA - K_OurCC) * tau > P_OurCC - P_HA
```

## 关闭路径

1. 甲方回答 15 个抽象问题，先关闭 peer authority、commit/root、placement/service 五项。
2. 双方为每类操作签字冻结合法 DAG、`T_visible/T_commit/T_next` 和 counter stop。
3. 冻结跨节点 `R_h/R_o/W_s/W_o/M/C` 权重、placement、同频和
   no-fault/contention profile。
4. 采用同输入/seed 的 paired 多轮试验；预注册最大轮数、95% 单侧 CI 和 inconclusive 规则。
5. 只有主指标 `delta=T_mean_HA-T_mean_OurCC` 的置信下界严格大于 0，且
   correctness/memory-order gate 全过，才判 `STRICT PASS`。

## 当前不可声称

- 不可声称 OurCC 已严格快于甲方 HA，或把 `TIE/同阶` 写成 PASS。
- 不可把 direct data 当 direct permission/authority，把无显式 Ack 当无 completion 成本。
- 不可把 metadata lookup 近似零写成完整 HA service 为零，也不可把 2-bit VI 自动解释为
  dirty owner。
- 不可把三角色 C4 当作两节点胜因，或把未实现 one-way Clear 当当前性能。
- 不可把 OurCC fault robustness 的 retry 成本混入 lossless HA baseline。
- 不可删除退化 case、用单轮均值或内部 trace 替代共同 guest/root completion。
