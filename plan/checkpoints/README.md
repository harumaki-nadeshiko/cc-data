# Checkpoints Directory

本目录用于保存 `coder-validator-orchestrator` 在阶段执行中断时生成的检查点文档。

使用规则:
- 每次因 API 限额或等价外部中断停止时，新建一个新的 checkpoint 文档
- 不覆盖旧文档
- 下一次恢复时，优先读取时间戳最新的 checkpoint 文档

推荐命名格式:
- `<YYYYMMDD-HHMMSS>-<stage>-checkpoint.md`

示例:
- `20260526-231500-M5-checkpoint.md`
