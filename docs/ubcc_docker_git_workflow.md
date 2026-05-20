# UBCC Docker And Git Workflow

本文档定义后续 Agent 的测试环境、容器使用方式，以及每阶段结束后的自动 commit/push 流程。

## 1. 方案结论

采用两层执行模型：

- 开发/构建/测试在一个无网络 Docker 容器中完成。
- git preflight、commit、push 在宿主机侧通过包装脚本完成，不依赖容器联网。

这样做的原因：

- 你要求运行中的 Docker 容器不需要访问网络。
- gem5 的修改、编译、SE mode 运行都可以在离线容器中进行。
- push 需要网络，因此放在宿主机侧统一执行更稳定，也更容易保证使用固定 SSH key 且无人工输入。

## 2. 宿主机前提

已知宿主机环境：

- Ubuntu 20.04 focal
- x86_64 Linux 5.15.0-58-generic
- AMD Ryzen 9 5950X
- 可用额外存储目录：`/mnt/data2/$USER/docker-cc`
- HTTP/SOCKS5 混合代理：宿主机 `127.0.0.1:19973`
- 自动 push 使用 SSH key：`/mnt/data2/$USER/.ssh/id_rsa_np`

补充约束：

- 容器运行时使用 `--network none`。
- repo 目录直接 bind mount 到容器中，用于代码修改、构建和运行。
- Docker image 构建阶段允许走宿主机代理，因为需要安装依赖。

## 3. 固化资产

本 repo 中已添加：

- `docker/ubcc-dev.Dockerfile`
- `scripts/ubcc_docker_build.sh`
- `scripts/ubcc_docker_run.sh`
- `scripts/ubcc_git_preflight.sh`
- `scripts/ubcc_phase_commit.sh`

## 4. Docker 环境设计

### 4.1 目标

容器需要支持：

- 在 x86_64 主机上构建 `build/ARM/gem5.opt`
- 在 gem5 SE mode 下运行 ARM workload
- 在容器内交叉编译简单 ARM 测试程序

### 4.2 镜像内容

镜像基于 `ubuntu:20.04`，包含：

- gem5 常用构建依赖：`build-essential`、`scons`、`python3-dev`、`protobuf`、`boost`、`hdf5`、`libelf`、`swig`、`zlib` 等
- ARM 交叉编译器：`gcc-aarch64-linux-gnu`、`g++-aarch64-linux-gnu`、`libc6-dev-arm64-cross`
- `ccache`，配合宿主机持久目录复用编译缓存

### 4.3 镜像构建

使用：

```bash
scripts/ubcc_docker_build.sh
```

该脚本会：

- 使用宿主机网络构建 Docker image
- 默认把 `http_proxy` 和 `https_proxy` 指向 `http://127.0.0.1:19973`
- 把镜像标记为 `ubcc-dev:ubuntu20.04`
- 预创建 `/mnt/data2/$USER/docker-cc/ccache` 和 `/mnt/data2/$USER/docker-cc/home`

如果代理地址或镜像 tag 需要覆盖，可传环境变量：

```bash
IMAGE_TAG=ubcc-dev:test http_proxy=http://127.0.0.1:19973 scripts/ubcc_docker_build.sh
```

### 4.4 容器运行

使用：

```bash
scripts/ubcc_docker_run.sh
```

或直接执行命令：

```bash
scripts/ubcc_docker_run.sh bash -lc 'scons build/ARM/gem5.opt -j16'
```

该脚本会：

- 用 `--network none` 启动容器
- 将 repo 根目录挂载到容器内 `/workspace`
- 将 `/mnt/data2/$USER/docker-cc/ccache` 挂载到 `/ccache`
- 将 `/mnt/data2/$USER/docker-cc/home` 挂载到 `/home/builder`

推荐约定：

- 所有源码修改、gem5 构建、SE mode 测试都在该容器内执行
- 所有 git preflight、commit、push 都在宿主机执行

## 5. 自动 commit/push 设计

### 5.1 原则

- 不修改全局 git config
- 不要求人工输入密码或 passphrase
- 强制使用 `/mnt/data2/$USER/.ssh/id_rsa_np`
- commit identity 优先从环境变量读取，不存在时再读取 repo-local git config

### 5.2 需要的环境变量

若 repo 内尚未配置 `user.name`/`user.email`，则在宿主机执行前导出：

```bash
export UBCC_GIT_NAME='Your Name'
export UBCC_GIT_EMAIL='you@example.com'
```

可选变量：

```bash
export UBCC_SSH_KEY="/mnt/data2/$USER/.ssh/id_rsa_np"
```

### 5.3 无人工干预预检

宿主机执行：

```bash
scripts/ubcc_git_preflight.sh
```

该脚本检查：

- SSH key 存在
- git identity 可用
- `origin` remote 存在
- 对 GitHub 的 SSH 认证可无交互完成
- 对当前分支的 `git push --dry-run` 可无交互完成

这是后续所有“阶段结束自动 commit/push”的前置门槛。

### 5.4 每阶段结束后的提交流程

阶段代码修改与测试完成后，在宿主机执行：

```bash
scripts/ubcc_phase_commit.sh M3 "m3: add EP-RNF and EP-SNF skeleton"
```

该脚本会：

1. 运行 `scripts/ubcc_git_preflight.sh`
2. `git add -A`
3. 若无变更则直接退出
4. 用无交互身份完成 `git commit`
5. 使用指定 SSH key `git push origin HEAD:refs/heads/<current-branch>`

## 6. Agent 执行要求

后续 Agent 必须按以下模式工作：

1. 先在宿主机运行 `scripts/ubcc_git_preflight.sh`
2. 再用 `scripts/ubcc_docker_run.sh` 进入容器完成该阶段的修改、构建和测试
3. 回到宿主机运行 `scripts/ubcc_phase_commit.sh <phase> <message>`
4. 只有当该阶段无文件改动时，才允许跳过 commit/push

## 7. 推荐工作流

```bash
scripts/ubcc_docker_build.sh
scripts/ubcc_git_preflight.sh
scripts/ubcc_docker_run.sh
# 容器内完成修改、构建、测试
scripts/ubcc_phase_commit.sh M1 "m1: add clustered single-node CHI config"
```

## 8. 注意事项

- 容器内默认无网络，不要在容器内执行需要联网的下载、push、submodule update。
- 若后续必须下载额外依赖，应先更新 Dockerfile，再重建镜像。
- `scripts/ubcc_phase_commit.sh` 不会修改 git config；若缺少身份，只会失败并提示设置 `UBCC_GIT_NAME`、`UBCC_GIT_EMAIL`。
- 若当前处于 detached HEAD，preflight 会失败；需要先切到明确分支。
