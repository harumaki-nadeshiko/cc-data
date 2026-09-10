# Stage2 执行台账

## 约束与待办

- [x] 确认两个工作区已有改动、文档基线 6e3e679；保留 TC141 删除与原始历史。
- [x] 确认本地开发、文档 Docker 镜像可用；所有执行采用 network none。
- [x] 重算 60 对逐 plane ns/op、两种拓扑子集的 reduction AM，记录 4N 替换敏感性模型（stage2_recomputed_data.json）。
- [ ] 核实计时边界与历史源码差异，不以当前 TC143 源码替代实测来源。
- [ ] 检查远端 M3 现状、资源占用和失败原因；确认五拓扑与三轮清单。
- [ ] 安排新增 M1/M2：六 TC × 三拓扑 × 四配置 × 两压力 = 144 次；IdealDir 独立。
- [ ] M3：八 TC × 五拓扑 × 两配置 × 三轮 = 240 次；首轮 80 次，后续两轮 160 次。
- [ ] 原 TC135–140、217 补两轮共 42 次，仅在指纹相容且前序任务结束后执行。
- [ ] 修改 D1/D2/D3 正文、图表与方法口径；禁止触碰 split_mode_va_pa_mapping.md。
- [ ] 生成 DOCX，再从 DOCX 生成 PDF；完成文本检查与实际图片视觉检查。
- [ ] 汇报实际产物、实际启动/完成数量、尚存阻塞；不提交或推送。

## 远端初查

存在持续运行的容器 `m3-multitopo-p100-20260908-v2`（初查已运行约 15 小时）。在核实其清单与资源前不并发启动新矩阵。

## 本次交付与验证

- D3 第 4.6 节已改为完整 episode 方法、三种单 Socket 拓扑 reduction AM 表和配对图；删除该节混合历史 optimized 的表格。原始数据 JSON 未改动。
- 应用图保留负值，拓扑用颜色标识，x 轴仅 TC；机制 M2 图删除 TC140 与 GM 汇总柱，阈值标签为 `10% reduction`。M1 扩展图 Outer delta 改为 ns，容量继续 GM、Delta 继续 AM。
- D2 图 1-1 改为分层梯形金字塔，保留 draw.io 源与 PNG/SVG；第一次实际读页发现文字密集，调小层内文字后再次读页确认改善。
- 三份 DOCX 已生成，三份 PDF 均由 DOCX 经 LibreOffice 转换。第一次缺字体的产物已由加载现有 fonts.zip 后的产物覆盖。
- `sync_delivery_documents.py --check`：3/3 同步；`test_publication_extension.py`：13/13 通过；PDF 文本关键数字、频率、240 次新计划断言通过。
- 实际读取了 D2 第 3 页（修正前后）和 D3 第 14 页渲染 PNG。属于抽样视觉检查，不是全册视觉/8pt 验收。图片位于 `/tmp/opencode/stage2-d2-page3-final.png`、`/tmp/opencode/stage2-d3-page14.png`。
- 原始压缩包已在 Docker 内列目录并提取至 `/tmp/opencode/stage2-original`，未覆盖现有原图；尚未完成逐图布局迁移。

## 远端明确阻塞与实际启动数

只读检查确认旧 supervisor 为 BLOCKED，旧清单仍是六拓扑五轮 480 次。结果仅两条：2N1S/r01/TC228/ourcc 为审计修正后 PASS，HA-VI 为 FAIL。HA-VI `verify_tc228.log` 中 HAT01 两个 plane 各 errors=16，runner return_code=1。CPU 预留 0–63，network none。未修改旧任务、源码、清单或结果。

本次新启动运行数 **0**。新 M3 240 次（首轮 80）、新增 M1/M2 144 次及后续 42 次重复均未启动。M3 必须先定位并修复上述数据验证失败；M1/M2 的独立四配置 runner、IdealDir 定量事件/误差资格及新增拓扑源码指纹尚未完成审计，不能用 optimized 冒充 IdealDir 来启动。原场景补两轮仍需指纹相容检查。

## 尚未完成的 Stage2 范围

D1 全面架构/三子图/目录结构交互重设计、D2 圆状态图、三册全面正文清理与附录搬迁、估计值斜体或斜线图统一展示、IdealDir 非零 H64 定量容差实现、全册字号及布局验收均未完成。当前产物是部分实施版本，不是最终验收版。

## Q1 与 Q2 的复现边界

计时参考：主工作区 TC142 workload 第 38–66 行覆盖压力写入、批内服务与屏障；第 68–70 行以业务操作数归一化。统计来源为记录的 selected_arms/timers，publication_extension_charts.e2e_ns 每 plane 换算后等权。TC143 工作区存在源码变体，当前源码不充当历史实测源码指纹证明。

4N 敏感性：对 S=1,2，r(4NS)=r(3NS)+α[r(8NS)−r(3NS)]，中心 α=1/5；替换五拓扑聚合中的两个 3N 坐标。α∈[0,1] 仅为敏感性端点，不是预测置信区间或真实上下界。压力步长覆盖集合大小 256/gcd(P,256)，这里 P 指 plane 步长而不是压力百分比；3/6 plane 对应 256/128 个 set，4/8 plane 对应 64/32 个 set。该离散变化使线性模型尤其不可靠。
