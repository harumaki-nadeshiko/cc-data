# 指标 1/2 manifest 审计工具

`scripts/analyze_metric12_manifest.py` 在正式指标计算前审计证据矩阵。v2 将物理实验
目录 `runs` 与逻辑计权视图 `uses` 分开；旧的顶层 run list 仍可直接输入。

## v2 要点

- `requirements.metric1.repetitions` 与 `metric2.repetitions` 独立，可以是不连续且不同的
  标识或数量；`profiles`、指标 2 的 `cases` 省略时采用官方全集。
- 每个 use 通过 `physical_run_id` 引用 run，并显式声明 `metric`、`repetition`、
  `profile`，指标 2 还声明 `case`。`view` 对象也可承载这些字段。
- 默认禁止一个物理 run 占据多个 required slot。确需把同一结果作为不同视图使用时，
  所有相关 use 必须给出相同非空 `reuse_group`，并设置 `allow_reuse: true`（或由 policy
  打开复用）。即使允许，ledger 的 `independent_evidence_count` 也只增加一次。
- 同一物理 run 绝不能在同一个比较 aggregate（指标 1 的一次 repetition，或指标 2 的
  repetition+case）内重复计权。

slot 状态为 `VALID/MISSING/INVALID/DUPLICATE/REUSE`；要求之外的 use 为
`UNEXPECTED`。缺 slot 或次数不足产生 `INCOMPLETE`，不会被当成工具异常。部分矩阵会
输出 coverage 和能安全计算的 provisional comparison，但绝不会标为 PASS。

旧 list 格式没有显式计划次数，因此默认要求每个指标至少 3 次；若只观察到 1 或 2 次，
工具会生成缺失 repetition 占位并输出 `INCOMPLETE`。可用
`--legacy-min-repetitions N`调整，但正式验收建议改用 v2 显式 requirements。

## 输出及退出码

输出目录包含：

- `metric12_manifest_audit.json`
- `metric12_manifest_audit.md`
- `metric12_manifest_coverage.csv`

退出码：`0 PASS`、`1 FAIL`、`2 INVALID`、`3 INCOMPLETE`。

```bash
python3 scripts/analyze_metric12_manifest.py \
  --input scripts/metric12_manifest.example.json --out-dir /tmp/metric12-audit
```
