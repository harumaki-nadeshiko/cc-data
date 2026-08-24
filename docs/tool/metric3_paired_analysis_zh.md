# 指标 3：HA-VI 与 OurCC+Lossless 配对分析

`scripts/analyze_metric3_paired.py`分析HA-VI可执行参考模型与OurCC
`lossless-oneway`仿真的配对结果。HA-VI arm可保留`clear_profile=ack`占位，因为HA-VI
不执行OurCC Clear；其完成边界由`ha-install-ack-oneway`profile marker单独校验。差值固定定义为：

```text
delta = HA-VI - OurCC
delta > 0 表示 OurCC 更快
```

## 历史证据目录

```bash
python3 scripts/analyze_metric3_paired.py \
  --evidence-root /path/to/ourcc_havi_paired_evidence \
  --output-dir metric3_report
```

## 显式 manifest

```bash
python3 scripts/analyze_metric3_paired.py \
  --manifest scripts/metric3_paired_manifest.example.json \
  --output-dir metric3_report
```

## 冻结权重

```bash
python3 scripts/analyze_metric3_paired.py \
  --evidence-root /path/to/evidence \
  --weights scripts/metric3_weights.example.json \
  --output-dir metric3_report
```

当前合同已冻结两个等权tier并输出合同 PASS：core 为 TC228-TC230；representative
为 TC231-TC235，其中 TC232 使用 2/3 read + 1/3 write，TC233/234/235 分别使用
`producer_consumer_service`、`queued_token_end_to_end`、
`catalog_kv_end_to_end`。HA-VI 是 executable reference model，不是 proxy，也不是
physical-silicon measurement。

## 鲁棒性规则

- OurCC或HA-VI任一arm缺失时，不做非配对替代。
- 次数不齐、重复arm、orphan arm均进入coverage ledger。
- AB/BA只作顺序敏感性诊断。
- 确定性重复不是独立随机样本，不输出权威CI或p-value。
- TC228-TC230 是 core 等权 tier；TC231-TC235 是 representative 等权 tier。
- 同一paired sample可进入多个命名aggregate，但贡献ledger逐sample记录，独立证据数不增加。
- 同一aggregate内同一paired sample只能贡献一次。

## 输出

```text
metric3_paired_report.json
metric3_paired_report.md
samples.csv
gate_ledger.csv
contribution_ledger.csv
compact.txt
```

当前 v4 五对证据输出：

```text
overall_status=PASS (EXECUTABLE-REFERENCE-MODEL SCOPE)
p100 core reduction=20.090019%
p100 representative reduction=3.645457%
p150 core reduction=20.178969%
p150 representative reduction=3.640320%
```

权威目录为 `results/metric3-l3-only-v4`，统一报告为
`results/metric12-final-v1/report/metric123_report.{md,json}`。报告必须保留
EXECUTABLE-REFERENCE-MODEL、2N1S/O3 和 dirty-worktree provenance 限定。

## 理论解释边界

分析采用 `T = K_crossnode * tau + P`：路径事件决定 `K_crossnode`，共同 fabric
配置决定 `tau`，端点/目录/排队/cache/完成处理进入 `P`。TC228 是 remote-read
请求/返回路径；TC229 是 request→old-owner recall→response→new-owner completion；
TC230 是 writer request→sharer invalidation/ack→completion；TC232 分别测 hot read
和 hot write 后按 2/3、1/3 合成。工具输出支持比较总时延与方向，但不支持凭总量
臆造精确 `K_crossnode` 或 `P` 数值。
