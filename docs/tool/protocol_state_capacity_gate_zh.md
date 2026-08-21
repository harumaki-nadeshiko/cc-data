# 协议动态状态容量门禁

## 目的与边界

`configs/protocol_state_capacity_manifest.json` 把 UBCC/UBIO、networksim、framework 以及 CHI EP 中的高优先级运行态动态容器登记为可机器检查的债务清单。`scripts/audit_protocol_state_capacity.py` 检查清单结构、重复项、源文件/符号漂移，并扫描所列目标源文件中的 `std::map/set/unordered_map/deque/vector` 持久声明，防止新增容器绕过登记。

此改动只增加清单、审计脚本、测试和文档，**不改变任何生产协议行为、容量、阻塞方式或消息时序**。

## 使用

```bash
python3 scripts/audit_protocol_state_capacity.py
python3 scripts/audit_protocol_state_capacity.py --json
python3 scripts/audit_protocol_state_capacity.py --fail-on-unbounded
```

- 默认“registry gate”要求清单合法、符号仍存在且没有未知声明。已登记的历史 `unbounded` 债务本身不会让默认门禁失败。
- `scripts/run_remote_preflight.sh` 自动执行 registry gate；正式上机前若源码新增未登记动态容器会直接失败。
- `--fail-on-unbounded` 是严格 FPGA 目标门禁；任一非 `host-only` 的 `unbounded` 条目仍存在即失败。
- `--json` 输出稳定的机器可读结果与分类/容器计数；诊断文本默认写到标准错误。
- 仅允许以 `immutable` 或 `config` 显式排除初始化后冻结的拓扑、模型数据存储和 host registry。排除项同样验证文件与符号，不能用来隐藏一般运行态队列。

## 当前诚实状态

当前清单完成后，**registry gate 应当 PASS**：这说明已知高优先级容器已登记，扫描范围内没有未解释的新声明，且源码符号没有漂移。

当前 **strict FPGA gate 应当 FAIL**：UBAdapter、EPBackend、EP-RNF、EP-SNF、MetaRNF 输出/事务表，UBCC tombstone/复制表，UBIO legacy page/chain/completion，以及 framework 旧接收队列等仍登记为非 host-only `unbounded`。必须按 manifest 的 `target_replacement` 完成固定槽、固定 ring、generation handle 和明确 FULL/credit 语义后，逐项把分类改为有证据的 `hard`。

## 不能推出的结论

本门禁**不是“系统已经有界”的证明**。它是防止审计结果失联和新增动态容器静默进入的工程护栏：

1. 静态扫描不是完整 C++ 解析器，宏、类型别名、第三方内部队列和运行库隐藏缓冲仍需专项审计。
2. `hard` 只表示存在可核查容量表达式，不证明所有 admission、回滚、异常和 teardown 路径都遵守同一个 invariant。
3. `indirect` 仍需证明拓扑/配置和生命周期关系；`host-only` 只表示不进入 FPGA 数据面目标，不表示 host 内存风险消失。
4. ZMQ HWM、阻塞发送、消息对象的二次分配和同一事务跨表复制，不能仅凭容器登记得到解决。

容量重构完成后还需压力测试、高水位观测、FULL 路径验证、晚到 completion/generation 测试及形式化 invariant 检查。
