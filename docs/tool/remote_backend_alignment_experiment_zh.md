# remote-backend 对齐实验记录

## 实验定位

本记录对应本地 worktree 分支 `experiment/remote-backend-equivalent`，用于把当前 framework endpoint 行为与 remote-backend 等价条件对齐。这里记录的是已经得到的实验事实，不把 transport 形态相关性扩大解释成协议根因。

## 精确结果

### `b490b47`

- public stress：双方向各 **100,000** 条，**PASS**。
- 精确 blocking `Terminate` TC98 workload：功能结果 **16/16 match**。
- 同一 blocking teardown 条件下，shutdown timeout：**8/25 exits**。

因此，数据/协议工作负载可以全部匹配，但阻塞式 teardown 仍可独立卡在退出阶段。`16/16 match` 与 `8/25 exits` 必须同时报告，不能把功能匹配写成完整进程生命周期通过。

### `dba773c`

- 唯一实验改动是 teardown 的 `dontwait` 行为；不借机修改协议、拓扑或数据路径。
- TC98：**PASS 25/25**。

这个 A/B 结果把 shutdown timeout 稳定地定位到可单独复现的 blocking teardown 条件；它不证明所有退出竞态已经被形式化消除，但证明该 workload 下仅改变 `dontwait` 足以消除观察到的失败。

## 结论边界

1. **单个双向 PAIR socket 且不设置额外 socket options，不足以构成协议 stall 的充分原因。** public stress 双方向各 100k 已通过；blocking 版本的 TC98 也达到 16/16 功能匹配。不能仅凭“bidirectional PAIR/no options”推断协议数据面必然停滞。
2. **阻塞 teardown 是另一条可独立复现的问题。** 功能请求已完成并不保证 `Terminate`、关闭握手和 socket 销毁能在 timeout 内结束；`8/25 exits` 正是该分离现象。
3. `dba773c` 的 25/25 结果支持把 `dontwait` 作为退出路径对齐点，而不是把它描述成协议正确性修复。
4. 本实验不改变生产协议容量结论，也不证明 transport 无隐藏队列、无丢包或在任意调度下都能安全退出。后续仍应分别验证数据面 backpressure、控制消息保留 credit、关闭状态机幂等性和 peer 异常退出。
