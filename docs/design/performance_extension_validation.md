# 性能数据图更新与验证（2026-09-08）

## 范围

仅更新性能图、D3 及其数值源/生成器/测试/清单。D1、D2 正文、DOCX、PDF 和全部流程图
保持原内容；三册统一执行同步、字体和布局检查。原有未跟踪 results/ 和两个指定脚本未动。
历史正式验收与新扩展分区，不改变正式门槛。

## 可复算证据

- performance_extension_data.json：180 个选定 PASS 臂、60 个坐标；6 个指定远端替换臂，
  1 个指定本地替换臂，不重算被替换数据。源文件相对路径与 SHA-256 保留。
- Outer 原始延迟值按频数无损保存，并保留每进程事件数；不是节点均值平均，也不按文本去重。
- E2E 保留每 plane 的操作数、counter_ticks、频率与源行号，采用已有 mean-plane ns/op。
- collector 在远端禁网 Docker 只读 results 解析；libzstd 流式解压，不依赖未安装的 zstd CLI。
- performance_extension_m3_status.json：07:33 UTC 只读状态快照。UBCC 首臂审计通过，
  HA-VI 对臂失败，完整可比较新 pair 为零，gate.json 不存在；不计算新 P100 GM。

## 验证

- ubcc-dev:ubuntu20.04、--network none：tests/scripts 全部 250 tests 通过，包含新增 12 tests。
- ubcc-doc-evolve:ubuntu20.04、--network none：同一 250 tests 通过。
- sync --check：3 册通过；fonts：通过；figures：17 项通过。
- all-three layout：D1 23 页、D2 12 页、D3 27 页，失败页均为 0。
- D3 DOCX/PDF 已重新生成；LibreOffice 的 Java 环境警告未阻止转换。
- 未降低字号、纵横比、布局等校验阈值。旧测试中固定陈旧 -9.2 数值的断言改为负 Delta
  展示要求，并新增实际负值数据测试；新/旧数值源按图分区验证。

## 仍未完成的审查

图表自动几何/字体检查通过，但当前模型不支持图像输入，无法完成截图人工视觉审查。
visual_qa.json 是自动检查结果，不代表人工逐页看图通过。M1 目前采用四纵向分面大图，
每分面 37 柱；在 D3 中缩放后的实际阅读性仍需人工检查，不能据自动 PASS 宣称视觉全通过。
M3 新多拓扑实测未完成，保留历史 2N1S/P100 图并标注新覆盖 N/A；不能称所有实验 PASS。
