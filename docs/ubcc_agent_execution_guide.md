# UBCC Agent Execution Guide

本文档用于指导后续 Agent 分阶段完成 gem5 Ruby CHI + UBCC 原型。总方案见 `gem5_chi_ubcc_plan.md`；容器环境与自动提交流程见 `docs/ubcc_docker_git_workflow.md`；本文只保留执行边界、阶段目标、输入输出和验收条件。

## 0. 固定设计决策

- gem5 fork 已作为 `gem5/` submodule 加入当前 repo。
- Subtask 1 目标是单节点 `M=2` cluster、`C=2` cores/cluster、cluster-shared L2、node-shared HN-F/L3、SN-F/DRAM。
- Subtask 2 固定 `2Core/Node`。早期链路验证只需要在每个 node 的一个 core 上跑有效 payload。
- Subtask 2 的独立 CHI domain 粒度是 node。同一 node 内两个 core 共享本地 CHI coherence；不同 node 的 ordinary CHI traffic 必须隔离。
- UBCC global coherence 主路线是 EP-RNF Sentinel 主导。
- EP-SNF 负责 DSM Remote miss/fill/writeback data plane。
- HN-F 修改必须保持最小化，集中在 sentinel registration、synthetic EP-RNF directory state、sentinel-preserving maintenance。
- DSM Local 上由 UBCC 触发的 coherent local access 合并进 EP-RNF，不单独实现 UR。
- UBCC metadata 第一版用 C++ map；SE mode 下不映射给普通 CPU。
- `ExternalOwner` 是必须支持的 sentinel 状态。
- DSM Remote first miss 可以在 bring-up 阶段保守 `GrantM`，但 M8 必须恢复 `GrantS`/read-sharing。
- 所有源码修改、构建、测试默认在 `scripts/ubcc_docker_run.sh` 启动的无网络容器中完成。
- 所有 commit/push 默认在宿主机通过 `scripts/ubcc_phase_commit.sh` 完成。

## 1. Agent 通用规则

- 每个阶段只修改该阶段需要的最小文件集合。
- 不要提前实现后续阶段的大型机制。
- 每次改动后运行该阶段指定的 smoke test 或至少构建检查。
- 保持 Local Normal PA 行为不经过 UBCC/EP。
- 所有新增 controller、message、debug trace 都必须携带 `node_id` 或可追踪的 domain 标识。
- 对普通 CHI traffic 增加跨 node 断言或 debug checker。
- 保留保守 `GrantM` debug flag，即使 M8 后默认启用 `GrantS`。
- 开始任何实现阶段前，先通过 `scripts/ubcc_git_preflight.sh`。
- 每个实现阶段结束后，若有代码变更，必须在宿主机执行 `scripts/ubcc_phase_commit.sh <phase> <message>`。

## 2. 容器使用

标准入口：

- 构建镜像：`scripts/ubcc_docker_build.sh`
- 启动开发容器：`scripts/ubcc_docker_run.sh`
- 运行 git 自动化预检：`scripts/ubcc_git_preflight.sh`
- 阶段完成后自动提交：`scripts/ubcc_phase_commit.sh <phase> <message>`

约束：

- 容器运行时无网络。
- build/test 在容器内完成。
- commit/push 在宿主机完成。

## 3. Phase M0：Docker And Git Automation Preflight

目标：确认后续阶段可在无人工干预条件下完成容器构建、离线测试和 commit/push。

主要任务：

1. 使用 `scripts/ubcc_docker_build.sh` 成功构建开发镜像。
2. 使用 `scripts/ubcc_docker_run.sh` 成功启动无网络容器，并确认 repo 已挂载到 `/workspace`。
3. 在容器中运行最小命令，例如 `python3 --version`、`scons --version`。
4. 在宿主机运行 `scripts/ubcc_git_preflight.sh`。
5. 若缺少 git identity，则通过环境变量 `UBCC_GIT_NAME`、`UBCC_GIT_EMAIL` 补足，但不要修改全局 git config。

验收：

- Docker image 构建成功。
- 无网络容器可启动并访问 repo。
- `scripts/ubcc_git_preflight.sh` 输出成功。

## 4. Phase M1：Single Node Clustered CHI

目标：跑通单节点 CHI cache hierarchy。

输入：`gem5/configs/ruby/CHI.py`、`gem5/configs/ruby/CHI_config.py`。

主要任务：

1. 新增 `CHI_single_node_config.py` 或等价配置模块。
2. 实现 `ClusterCHI_RNF`，每 cluster 包含 2 个 core L1 和 1 个 shared L2。
3. 确保 shared L2 是 RN-F last-level controller。
4. 使用 1 个 HN-F/L3 和 1 个 SN-F/DRAM 起步。
5. 跑通单 core、同 cluster 双 core、跨 cluster 基本共享测试。

验收：

- `--chi-config` 能加载自定义 config。
- 4 core single-node CHI 能运行 SE mode smoke workload。
- Ruby debug 中能看到 L1 -> shared L2 -> HN-F -> SN-F 路径。

## 5. Phase M2：Logical Domain Isolation

目标：证明单 RubySystem 多 logical CHI island 可以等价于独立 node domain。

主要任务：

1. 定义 `NodeConfig`，每 node 固定 2 个 CPU。
2. 每 node 生成独立 RN-F、HN-F、SN-F controller set。
3. RN-F downstream 只指向同 node HN-F。
4. HN-F downstream 只指向同 node SN-F 或同 node EP-SNF placeholder。
5. HN-F snoop destination 只包含同 node RN-F 和同 node EP-RNF placeholder。
6. 添加 ordinary CHI message 跨 node checker。

验收：

- Node0 Local Normal PA workload 不产生 Node1 ordinary CHI message。
- 相同 DSM global PA 的不同 node 访问不会被 Ruby 全局路由到错误 HN-F。
- 若无法通过，停止单 RubySystem 路线并切换多 RubySystem 或多 gem5。

## 6. Phase M3：EP-RNF/EP-SNF Skeleton

目标：EP controller 能作为 CHI participant 收发消息。

主要任务：

1. 基于 `CHIGenericController` 实现 `EP_RNF_Controller` skeleton。
2. 基于 `CHIGenericController` 实现 `EP_SNF_Controller` skeleton。
3. EP-RNF 能接收 HN-F snoop 并返回固定合法 response。
4. EP-SNF 能响应 DSM Remote `ReadNoSnp` 并返回 fake data。
5. 建立 fixed-latency outer queue skeleton。

验收：

- HN-F 能把 EP-RNF 作为 snoop destination。
- EP-SNF fake data 能完成一个受控 DSM Remote read miss。

## 7. Phase M4：Sentinel Registration

目标：HN-F directory 能登记 EP-RNF sentinel，并在本地权限变化时 snoop EP-RNF。

主要任务：

1. 定义 EP-RNF synthetic MachineID。
2. 实现 sentinel insert/update/remove 入口。
3. 支持 `ExternalSharer`。
4. 支持 `ExternalOwner`。
5. 确保 registration 早于 CPU completion。
6. 禁止 Local Normal PA sentinel。

验收：

- 手工登记 `ExternalSharer` 后，本地 CPU `ReadUnique` 必须 snoop EP-RNF。
- remote exclusive write 模拟后，本 node 真 CPU copy 被 invalidate，EP-RNF 可成为 HN-F owner。
- `ExternalOwner` 不与本地 CPU dirty owner 共存。

## 8. Phase M5：DSM Remote First Miss Bring-up

目标：DSM Remote miss 经 EP-SNF 和 UBCC 获取 data/grant。

主要任务：

1. EP-SNF 处理 DSM Remote `ReadNoSnp`。
2. EP-SNF 向 home UBCC 请求 data/grant。
3. bring-up 阶段允许固定请求 `GrantM`。
4. requester 获得保守 `GrantM` 时，只记录本 node global owner，不登记冲突的 `ExternalOwner`。
5. requester 获得 `GrantS` 时，登记 requester EP-RNF `ExternalSharer`。

验收：

- Node0 可读取 Node1 DSM Local。
- home UBCC directory 状态正确。
- requester 侧状态符合 grant 语义。

## 9. Phase M6：UBCC Directory And EP-RNF Local Access

目标：UBCC 能通过 EP-RNF 操作本地 CHI domain。

主要任务：

1. 实现 per-line UBCC directory map。
2. 实现 outer MESI message 基础集合。
3. EP-RNF 支持 local read、downgrade、invalidate、dirty recall。
4. HN-F snoop EP-RNF 时，EP-RNF 等待 UBCC 完成 outer transaction 后再响应。
5. remote read local dirty line 必须拿到最新 data。

验收：

- Node0 read Node1 DSM Local 后，Node1 write 同一 line 会先 invalidate Node0。
- Node0 write Node1 DSM Local 后，Node2 read 能看到 Node0 写入。

## 10. Phase M7：Writeback, Evict, Owner Transfer

目标：补齐三节点 ping-pong 正确性。

主要任务：

1. DSM Remote dirty writeback 经 EP-SNF 回 home UBCC。
2. DSM Remote clean evict 更新 home sharer mask。
3. 实现 dirty owner recall。
4. 实现 M owner transfer。
5. 增加 per-line epoch 和 stale response 防护。

验收：

- 三节点 ping-pong 中任意时刻最多一个 global M owner。
- dirty owner 被 remote read 时必须返回最新 data 并降级或失效。

## 11. Phase M8：GrantS And Read-Sharing Recovery

目标：恢复 read-sharing，不再依赖所有 DSM Remote miss 保守 `GrantM`。

主要任务：

1. 增加 HN-F -> EP-SNF minimal sideband。
2. sideband 至少携带 `original_chi_req` 或 `needed_perm=S/M`。
3. EP-SNF 对 read-only miss 请求 `GlobalReadShared`。
4. Home UBCC 授予 `GrantS` 并维护 sharer mask。
5. Requester HN-F 在 completion 前登记 requester EP-RNF `ExternalSharer`。
6. 后续 local upgrade 通过 EP-RNF 触发 global invalidation。

验收：

- Node0 和 Node2 可同时 read Node1 DSM Local 并持有 S。
- Node0 后续 write 使 Node2 invalidate。
- 默认测试启用 `GrantS`；保守 `GrantM` 仅作为 debug flag。

## 12. Phase M9：Metadata And Multi-gem5 Preparation

目标：在 correctness 稳定后处理 metadata 容量模型和外部网络迁移。

主要任务：

1. 保持第一版 metadata 为 C++ map。
2. 如需容量建模，增加 UBCC SRAM directory cache。
3. 如需 backing store，再设计独立 `MetadataAgent`。
4. 抽象 outer protocol ABI。
5. 准备 fixed latency、ns-3 或外部进程 queue 的时间模型选项。

验收：

- metadata 不被普通 CPU 访问。
- 多 gem5 迁移所需 outer message 字段和时序假设文档化。

## 13. 必须优先解决的 Spike

1. `CHIGenericController` 派生的 EP-RNF 是否能被 HN-F 作为 snoop destination。
2. EP-RNF 作为 HN-F owner 时是否能满足 owner snoop response/data forwarding 语义。
3. 单 RubySystem 多 logical island 是否能保证 ordinary CHI message 不跨 node。
4. HN-F directory 是否允许安全插入、保留、删除 synthetic EP-RNF entry。
5. EP-RNF 发起 local unique/read 操作是否能可靠 invalidate/downgrade 本 node 真 CPU cache。
