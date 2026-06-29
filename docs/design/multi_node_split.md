# Multi-Node Multi-Process Split (Supplement to cc_ep_refactoring_plan_v4.md)

> **背景**：`cc_ep_refactoring_plan_v4.md` 的一体化重构方案完成后，发现当前运行态仍然是**单个 gem5 进程管理多个节点**。本节是在原方案基础上追加的**多节点 → 多进程**拆分计划。

---

## 1. 当前状态

```
launcher.py → gem5.opt (单进程)
  └── RubySystem 内循环创建多个 EPBackend/UBAdapter
      ├── node0: EPBackend ─┬─ UBAdapter ─ Port ─ ubio_n0
      │                      └── shared event queue (gem5 主循环)
      ├── node1: EPBackend ─┬─ UBAdapter ─ Port ─ ubio_n1
      │                      └── shared event queue
      └── nodeN ...
  └── system.physmem (所有节点共享同一地址空间)
```

**关键特征**：
- 所有 node 在同一个 gem5 进程/地址空间内，共享事件队列
- 跨节点通信通过跨进程 Port（ipc://）到 ubio，再回到同一 gem5 进程内另一个 node 的 UBAdapter
- 一个 node 的事件风暴（如 TC2 中的 380K ReadReq）会影响整个仿真
- 单个 gem5 进程的内存占用量随 node 数线性增长

---

## 2. 目标状态

```
launcher.py → for i in 0..N-1:
  └── spawn gem5.opt --node-id=$i
      └── RubySystem 只创建 node=$i 的 EPBackend/UBAdapter
          └── EPBackend ─┬─ UBAdapter ─ Port ─ ubio_n{i}
                          └── 独立 event queue (各自 gem5 主循环)
      └── system.physmem (每进程独立)
```

**完整进程拓扑 (N nodes)**：

```
barrier_manager (1)         ← 分布式时钟同步
networksim      (1)         ← 网络延迟模拟
ubio_n0..N-1    (N)         ← 目录/仲裁/路由
gem5 --nid=0..N-1 (N)       ← 每个节点独立仿真
                              ────────────
                    总计: 2N + 2 个进程
```

**启动/关闭顺序（必须保证）**：

```
启动: barrier_manager → networksim → ubio_n{0..N-1} → gem5.opt --node-id={0..N-1}

关闭: gem5 完成 simulation → auto terminate(通知 ubio) → ubio exit → networksim exit → barrier exit
```

---

## 3. 变更清单

### 3.1 launcher.py: 多进程管理

从"启动单个 gem5 进程"改为"为每个 node 启动独立 gem5 进程"：

```python
def launch_test(tc_id, num_nodes):
    procs = []
    # 1. 启动 barrier_manager
    procs.append(Popen(["barrier_manager", f"--num-nodes={num_nodes}"]))

    # 2. 启动 networksim (可选)
    procs.append(Popen(["networksim", ...]))

    # 3. 启动 N × ubio
    for i in range(num_nodes):
        env = build_env(i)  # 生成 per-node endpoint 环境变量
        procs.append(Popen(["ubio", f"--node-id={i}"], env=env))

    # 4. 启动 N × gem5
    for i in range(num_nodes):
        cmd = [
            "gem5.opt",
            f"--node-id={i}",
            f"--num-nodes={num_nodes}",
            f"--outdir=logs/gem5_tc{tc}_node{i}",
            "configs/example/chii.py",
            "--ruby", f"--num-cpus=2",
        ]
        procs.append(Popen(cmd, env=build_env(i)))

    # 等待全部结束，收集退出码
    results = [p.wait() for p in procs]
```

**endpoint 命名规则**（launcher 为每个 node 生成）：

```
# node=i:
GEM5_{i}_RX=ipc:///tmp/ep_ipc/gem5_{i}_ubio
UBIO_{i}_RX=ipc:///tmp/ep_ipc/ubio_{i}_gem5
BARRIER_{i}_RX=ipc:///tmp/ep_ipc/barrier_{i}
NETWORKSIM_{i}_RX=ipc:///tmp/ep_ipc/nsim_{i}
```

### 3.2 Gem5 启动入口: `--node-id` 参数

新增 `EPNodeParams`（`gem5/src/mem/ruby/protocol/chi/ep/EPNodeParams.hh`）：

```cpp
struct EPNodeParams {
    int nodeId = -1;       // 当前进程负责的 node (必需)
    int numNodes = 1;      // 总节点数
};
```

- `--node-id` **必须提供**，缺失时报错退出
- `--num-nodes` 告知当前进程全局节点数，用于 endpoint 计算、NodeAddressMap 中 homeNode 映射

### 3.3 RubySystem / CHI 拓扑

当前拓扑构建在 `CHI_ubcc_framework.py` / `CHI_ep_framework.py` 中循环创建 N 个 RubySystem：

```python
# 当前: for node in range(num_nodes): create_RubySystem(node)
# 目标: create_RubySystem(node_id)  -- 只创建当前进程的 node
```

| 文件 | 变更 |
|------|------|
| `CHI_ubcc_framework.py` | 移除 `for node in range(num_nodes)` 循环，只创建 `nodeId` |
| `CHI_ep_framework.py` | 同上 |
| `EPBackend.py` | 接收 `nodeId` 参数，只创建对应 node 的 Backend |
| `EPBackend.hh` | 新增 `nodeId()` / `numNodes()` 访问器 |
| `UBAdapter.py` | `nodeId` 从构造参数传入，不复用自动发现 |
| `NodeAddressMap` | 使用 `numNodes` 全局参数正确计算 homeNode/homeSocket |

### 3.4 跨节点通信路径重定向

| 场景 | 当前 | 目标 |
|------|------|------|
| Node0 ↔ Node0 (local) | 同进程内 EPBackend 直接调用 UBAdapter | 同进程内不变 |
| Node0 ↔ Node1 (remote) | Port → ubio_n0 → networksim → ubio_n1 → 同一 gem5 内 Node1 | Port → ubio_n0 → networksim → ubio_n1 → Port → **不同 gem5 进程**的 Node1 |
| Node0 ↔ Socket1 (dual-socket) | 同进程内，同地址空间 | 同进程内，同地址空间（不变） |

原有依赖共享地址空间的跨节点交互（如全局拓扑查找）全部改为 Port 消息 + ubio 路由。

### 3.5 分布式时钟同步

多 gem5 进程各自运行独立事件队列后，时钟同步仍由现有 `emitSync` / `safeTimestamp` / `barrier_manager` 保障：

```
loop:
  tick = event_queue.next_event_time()
  emitSync(tick) → barrier_manager
  barrier_manager 等待所有进程到达 tick
    OK      → 所有进程继续
    timeout → 宣告节点失联 → 测试失败（不 hang）
```

已在之前时钟同步修复中验证有效。确保 barrier_manager 启动在所有 gem5 进程之前。

### 3.6 run_multi.sh 适配

`run_multi.sh` 调用 `launcher.py` 时传入 `--num-nodes` 参数：

```bash
./launcher.py --test-case=$TC --num-nodes=$NUM_NODES
```

launcher 根据 `--num-nodes` 决定启动的 gem5/ubio 进程数。

### 3.7 日志与调试

每个 gem5 进程输出到独立日志目录，便于区分：

```
logs/gem5_tc{tc}_node{node_id}/simout.{seed}.txt
```

进程崩溃时 launcher 应确保：
- 向 barrier_manager 发送失联信号（避免其它进程无限等待）
- 向存活进程发送 TERMINATE（触发 graceful exit）
- 收集各进程退出码用于测试报告

---

## 4. 与 cc_ep_refactoring_plan_v4.md 的关系

| 原方案章节 | 拆分关系 |
|------------|----------|
| Port 重构 | 基础接口不变；launcher 改为生成 per-node endpoint；gem5 启动参数新增 `--node-id` |
| 编译系统 | 不变；gem5 二进制只有一份，不同 node 通过参数区分 |
| 解耦 | 多进程拆分是解耦的前置条件——物理隔离后 port-only 通信才是真正唯一路径 |
| 物理删除 | 不变 |

**实施归属**：多进程拆分整合入 `cc_ep_refactoring_plan_v4.md` 的 Phase 3（解耦）中，作为解耦的第一步。

---

## 5. 回归与风险

| 风险 | 缓解措施 |
|------|----------|
| 进程启动竞态（gem5 启动快于 ubio） | 启动顺序强制约束：ubio 全部就绪后再启动 gem5 |
| 单进程崩溃影响其它进程 | launcher 向 barrier 发送失联信号，向存活进程发 TERMINATE |
| 时钟同步跨进程多路径 | barrier_manager 超时检测 + 日志采集（已有机制） |
| 日志量放大 N 倍 | 按 node 分目录独立存储 |
| 回归基线漂移 | 单节点（N=1）行为应与拆分前完全一致——先用 N=1 回归确认 |
