# 指标 1-3 本机实验补充与验收优先级

本文只规划可在本机完成的汇报与验收证据，不依赖远端 framework 行为。

## P0：正式交付前必须补齐

### 1. 最新版本 provenance 重跑

旧历史结果若缺少 commit、镜像和二进制指纹，应在最新冻结版本上至少重跑一次正式矩阵，记录：

```text
main commit
gem5 commit
Docker image digest
framework/UBIO/networksim/gem5.opt SHA-256
CPU model=o3
sequencer outstanding=16
link latency/sync interval
```

目标不是替换历史重复，而是关闭“结果来自哪个版本”的审计缺口。

### 2. 指标 1/2 coverage 审计

用 `analyze_metric12_manifest.py`建立显式 v2 manifest：

```text
physical runs：一次真实执行只登记一次
logical uses：声明该执行用于哪个指标槽位
requirements：独立声明指标1和指标2的计划重复次数
```

正式报告要求：

```text
Metric 1：TC131 × 3 profiles × 至少3次完整重复
Metric 2：TC135-TC140、TC217 × 3 profiles × 至少3次完整重复
无MISSING/INVALID/DUPLICATE/未授权REUSE
```

同一物理run若确实同时服务多个指标，必须使用相同`reuse_group`并显式`allow_reuse`；报告中的独立证据数只能增加一次。

### 3. 指标 3 权重冻结（已完成）

当前权威 v4 五对确定性 paired 证据已冻结两层聚合并通过：

```text
core：TC228-TC230 等权
representative：TC231-TC235 等权
TC232：2/3 read + 1/3 write
TC233：producer_consumer_service
TC234：queued_token_end_to_end
TC235：catalog_kv_end_to_end
```

正式判定为：

```text
PASS (EXECUTABLE-REFERENCE-MODEL SCOPE)
HA-VI 为可执行参考模型，不是 proxy，不是物理芯片测量
范围固定为 2N1S/O3、one-way completion、100%/150% L3 pressure
```

权威输出见 `results/metric3-l3-only-v4` 与
`results/metric12-final-v1/report/metric123_report.{md,json}`。

### 4. 指标 3 最新版本paired重跑（已完成）

v4 已按以下矩阵完成 160/160 PASS：

```text
TC228-TC235
OurCC lossless-oneway / HA-VI
至少5个pair
每个TC交替AB/BA
每个arm verifier PASS、child exits=0、profile marker匹配
```

确定性重复只作一致性检查，不报告权威CI或p-value。

## P1：强烈建议补充

### 5. 执行顺序敏感性

指标1/2的profile执行顺序应轮换，例如：

```text
round1: naive -> spill-noopt -> optimized
round2: optimized -> naive -> spill-noopt
round3: spill-noopt -> optimized -> naive
```

用于排除宿主热状态、缓存和残留IPC顺序影响。每次必须使用全新run ID和IPC目录。

### 6. 重启与冷启动重复

至少选择以下smoke在容器和IPC完全清理后重复两次：

```text
TC3：基础升级/清理
TC35：双socket混合压力
TC98：8n2s热点串行化
TC160：16 plane能力
```

报告对比verifier、child exits、关键marker和二进制指纹。

### 7. 指标 1 边界压力

在TC131正式点之外增加非合同敏感性点：

```text
目录压力低于、接近和高于spill阈值
ways/metadata容量边界
spill-noopt与optimized的H64 exact-live一致性
```

这些点不进入指标1主均值，只用于证明1.5倍容量结论不是单点偶然。

### 8. 指标 2 负结果保留与稳定性

重点披露并复测已知不利case，例如TC138。报告应同时包含：

```text
每case三profile绝对均值
每次重复的reduction
适用集合是否跨重复稳定
等权平均是否在保留负结果后仍达标
CV和min/max
```

### 9. 指标 3 历史敏感性方法（非当前验收口径）

以下解析式仅保留为冻结前的方法记录，不覆盖当前双 tier 合同：

```text
D = -0.46875*w_remote_read
    +22.625*w_ownership_handoff
    -0.4375*w_shared_to_writer
```

报告OurCC更快的权重区域，但不得从有利权重点选择性推出合同PASS。

## P2：增强可信度但非当前阻塞项

### 10. 链路参数敏感性

保持双方同参数，测试少量固定点：

```text
link/sync = 2.5ns
link/sync = 5ns
link/sync = 10ns
```

用于说明结论是否依赖单一fabric参数。不得把不同参数的OurCC与HA-VI交叉比较。

### 11. 拓扑扩展

核心指标3主合同保持2n1s paired边界；另做4n1s、8n1s或2n2s描述性扩展，检查方向是否保持。扩展拓扑不能与2n1s样本混合平均。

### 12. 故障域能力展示

OurCC的drop/dup/delay/reorder能力可单独作为可靠性附录：

```text
正确性PASS
恢复次数
额外延迟
不与lossless HA baseline主时延混算
```

## 汇报结构建议

最终材料按四层组织：

```text
1. Coverage：计划、已有、缺失、无效、复用
2. Correctness：verifier、child exits、profile/fingerprint
3. Performance：逐run原始值、逐case统计、aggregate
4. Scope：proxy/contract、权重、确定性重复和适用边界
```

当前 coverage、证据和两层聚合已冻结，允许输出三指标总状态
**PASS（EXECUTABLE-REFERENCE-MODEL SCOPE）**。provenance 是记录过 diff/hash 的
dirty-worktree provenance；不得改写成 clean-tree 或物理芯片证据。

## 指标 3 理论分解与仿真解释

统一使用 `T = K_crossnode * tau + P`。`K_crossnode` 由显式路径语义决定，`tau`
是双方共同配置下的跨节点传输贡献，`P` 汇总端点、目录、排队、cache 和完成处理。
现有计时器不能唯一反演精确 `K_crossnode` 或 `P`，因此不填写无证据常数。

- TC228 remote read：请求跨节点到 authority/home，随后 grant/data 返回；比较相同
  请求—完成边界下的传输项与本地处理项。
- TC229 ownership handoff：新请求者触发旧 owner recall，旧 owner 响应后才可向新
  owner 完成；v4 中它是 core 优势的主要来源。
- TC230 shared-to-writer：写者请求后需要 sharer invalidation/ack 收敛再完成；v4
  两个压力点都小幅支持 OurCC。
- TC232 hot key：read 与 write 是不同路径，先分别计量，再按冻结的 2/3 read、1/3
  write 合成；read 子项略偏 HA-VI，write 子项偏 OurCC，representative 总体仍 PASS。
