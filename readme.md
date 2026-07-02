# cc-ep

多节点分布式共享内存 (DSM) 缓存一致性仿真工程：把 gem5 全系统仿真按节点拆分为
多个进程，经 ZeroMQ IPC + 保守式 PDES 时钟同步互联，外挂 ubio（home 目录/路由）、
networksim（跨节点交叉开关）、barrier_manager（跨节点栅栏）等 native 模块。

## 文档

- **[框架手册 docs/framework_manual.md](docs/framework_manual.md)** —— 整体架构、
  各模块、进程拓扑、`framework` 传输层接口、MemMessage 报文格式、时钟同步设计、
  编译/运行方法，以及**移植（替换 Port 实现）指南**。
- 设计细节见 `docs/design/`。

## 快速开始

```bash
# 1. 构建 framework 静态库 + 三个 native 二进制
bash scripts/build_framework.sh
bash scripts/build_all.sh          # -> build/bin/{ubio,networksim,barrier_manager}

# 2. 构建 gem5（Docker 内 scons build/ARM/gem5.opt）

# 3. 端到端多进程测试
bash tests/e2e/run_multi.sh 1 3 16                              # 冒烟
bash tests/e2e/run_multi.sh 1 2 3 4 5 6 7 8 10 11 12 13 16 53   # 核心 14 项回归
```
