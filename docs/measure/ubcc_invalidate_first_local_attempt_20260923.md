# UBCC invalidate-first 本地执行记录（未完成修复）

## 状态

未实现 solution A，没有修改协议源代码。下述成功构建和测试均为已有工作树的基线，不能作为修复验收证据。没有提交，没有访问远端 campaign。

所有构建和测试使用 `ubcc-dev:ubuntu20.04`，`--network none --cpuset-cpus 0-31`，挂载本地仓库到 `/workspace`。

## 构建

```sh
docker run --rm --network none --cpuset-cpus 0-31 \
  -v /mnt/data2/cgc/cc-ep:/workspace -w /workspace/gem5 \
  ubcc-dev:ubuntu20.04 bash -lc 'scons build/ARM/gem5.opt -j24'
```

成功：`[LINK] -> ARM/gem5.opt`，`scons: done building targets.`。警告包括 GCC 9.4 非官方支持版本、缺少 capstone、unused variables 和 narrowing conversion。本次构建更新了已有二进制，复现尝试是在此次构建之前执行。

## 复现尝试

公共 Docker 前缀：

```sh
docker run --rm --network none --cpuset-cpus 0-31 \
  -v /mnt/data2/cgc/cc-ep:/workspace -w /workspace \
  -e EP_HA_PROFILE=ubcc
```

在公共前缀之后添加以下参数及命令：

```sh
-e E2E_RUN_ID=invfirst_before_tc145_r2 -e TIMEOUT_SEC=600 \
ubcc-dev:ubuntu20.04 bash -lc \
'bash tests/e2e/run_multi.sh --3n2s --profile optimized 145'
```

TC145 r2 完整通过：planes=6、operations=12288、batches=32；全部 10 个子进程退出码 0；日志扫描没有 PANIC、panic:、fatal: 或 invalidation timed out。r1 被工具 120 秒超时中断，不计通过或死锁复现。

```sh
-e E2E_RUN_ID=invfirst_before_tc147_p175_r1 -e TIMEOUT_SEC=900 \
-e PORTABLE_512K_DIR=1 \
-e 'WORKLOAD_CFLAGS=-DPORTABLE_PRESSURE_LINES=114176 -DPORTABLE_TARGET_FOOTPRINT_LINES=114688 -DPORTABLE_NAIVE_CAPACITY_LINES=65536 -DPORTABLE_PRESSURE_LEVEL_PCT=175 -DPORTABLE_BATCHES=32' \
ubcc-dev:ubuntu20.04 bash -lc \
'bash tests/e2e/run_multi.sh --16n1s --profile naive 147'
```

TC147 全部 16 节点成功启动，但调用被工具 1020 秒超时中断，没有 verifier 结果；不能据此判定发生目标死锁。没有完成确定性三节点复现，也没有完成每个失败配置三次运行。

## 基线回归

在公共前缀之后：

```sh
-e E2E_RUN_ID=invfirst_baseline_regression -e TIMEOUT_SEC=600 \
ubcc-dev:ubuntu20.04 bash -lc \
'bash tests/e2e/run_multi.sh --1s 5 11 31'
```

重建后 TC5、TC11、TC31 各运行一次，全部 verifier PASS、全部子进程退出码 0、没有 panic/fatal/invalidation-timeout 标记，PeerExit/NetworkExit 正常闭合。

## 本地证据目录

- `logs/20260923_132743_3n2s_invfirst_before_tc145_r1`
- `logs/20260923_133034_3n2s_invfirst_before_tc145_r2`
- `logs/20260923_133938_16n1s_invfirst_before_tc147_p175_r1`
- `logs/20260923_135940_1s_invfirst_baseline_regression`

## 尚需实现及证明

- 仅暂停 EPBackend ReadReq 重试不能释放 HN demand TBE 对 CleanUnique 的阻塞。
- 现有 `Initiate_Snoop_Hazard`/`RestoreFromHazard` 可供设计参考，但不能直接把 CleanUnique 当作 snoop：它从 reqRdy 消费请求资源，而 hazard 使用独立 snoop TBE 和资源记账。
- 必须匹配 UBCC 同 PA、socket、未获外部 grant 的 demand，并阻止旧 fill 越过 barrier；不能只检查 `_pendingReadTxns` 非空。
- 独立 barrier 必须完成真实 snoop 收敛、必要的脏数据发布、旧权限与副本失效，然后可靠发送 ACK，最后恢复 demand。
- 需要验证 ACK 重传、晚到 response、CompAck 与 HN 恢复的顺序，以及 RNF 同地址 deferred transaction、多 socket 和 HA 隔离。
- 当前无修复后测试矩阵；不能宣称死锁已消除。
