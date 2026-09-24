# D1 附录 D：EP 资源审计边界（移出交付件）

> 本文件原为《UBCC 跨节点缓存一致性协议体系结构》（D1）附录 D。按交付件原则移出，仅作内部审计记录，不随 D1 交付。

本册 3.7 节对应 boundary-v4 运行使用的 BoundaryTransactions 实现：固定 64 项、8 项控制预留，Entry 的当前 ABI 大小为 72 B，节点级表体 4.5 KiB。它不包含原生数据缓冲、请求者稳定状态映射及其他队列；尤其 `_requesterLines` 当前仍为动态映射，不能由此表的容量推出整个 EP 已有界或总预算为 12 KiB。Read／Grant／Upgrade 的统一准入、各读写队列配额、持久化结构上限与整体硬件预算仍需独立审计。

实现核对以远端 boundary-v4 source 快照为准，gem5 二进制 SHA-256 前缀为 7e7d07dd，UBIO 为 3af4cc12。72 B 是实现描述符大小，不是综合后的 SRAM 面积。原生 CHI 数据缓冲不在该条目内重复计费；节点数与 Socket 数不得混用为实例数量。
