#!/usr/bin/env python3
"""Generate editable draw.io sources and release PNG figures."""

from pathlib import Path
import subprocess
from xml.sax.saxutils import escape


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/design/figures"


def drawio(name, title, boxes, edges):
    cells = [
        '<mxCell id="0"/>',
        '<mxCell id="1" parent="0"/>',
        ('<mxCell id="title" value="%s" style="text;html=1;align=center;'
         'verticalAlign=middle;fontSize=22;fontStyle=1;fontColor=#17365D;" '
         'vertex="1" parent="1"><mxGeometry x="30" y="20" width="1140" '
         'height="50" as="geometry"/></mxCell>') % escape(title),
    ]
    for box in boxes:
        cells.append(
            '<mxCell id="{id}" value="{label}" style="rounded=1;whiteSpace=wrap;'
            'html=1;fillColor={fill};strokeColor={stroke};fontColor={font};fontSize=16;'
            'fontStyle={bold};spacing=8;" vertex="1" parent="1"><mxGeometry x="{x}" '
            'y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'.format(
                id=box[0], label=escape(box[1]), x=box[2], y=box[3], w=box[4],
                h=box[5], fill=box[6], stroke=box[7], font=box[8],
                bold=1 if box[9] else 0))
    for index, edge in enumerate(edges):
        label = escape(edge[2]) if len(edge) > 2 else ""
        cells.append(
            f'<mxCell id="e{index}" value="{label}" style="edgeStyle=orthogonalEdgeStyle;'
            'rounded=0;orthogonalLoop=1;jettySize=auto;html=1;strokeWidth=2;'
            'endArrow=block;endFill=1;fontSize=13;fontColor=#404040;" edge="1" '
            f'parent="1" source="{edge[0]}" target="{edge[1]}"><mxGeometry relative="1" '
            'as="geometry"/></mxCell>')
    xml = ('<mxfile host="app.diagrams.net" modified="2026-08-24T00:00:00.000Z" '
           'agent="CC-EP" version="24.7.17"><diagram id="release" name="Page-1">'
           '<mxGraphModel dx="1200" dy="800" grid="1" gridSize="10" guides="1" '
           'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
           'pageWidth="1200" pageHeight="800" math="0" shadow="0"><root>'
           + "".join(cells) + '</root></mxGraphModel></diagram></mxfile>')
    (OUT / f"{name}.drawio").write_text(xml, encoding="utf-8")


def render_dot(name, dot):
    dot_path = OUT / f"{name}.dot"
    dot_path.write_text(dot, encoding="utf-8")
    subprocess.run(["dot", "-Tpng", "-Gdpi=150", str(dot_path), "-o",
                    str(OUT / f"{name}.png")], check=True)
    subprocess.run(["dot", "-Tsvg", str(dot_path), "-o",
                    str(OUT / f"{name}.svg")], check=True)


def architecture():
    boxes = [
        ("cpu", "CPU / Cache", 70, 130, 190, 80, "#D9EAF7", "#4F81BD", "#17365D", True),
        ("hnf", "节点内 CHI / HN-F", 70, 270, 190, 90, "#EAF2F8", "#4F81BD", "#17365D", True),
        ("ep", "EP-RNF · EP-SNF\nUBAdapter", 350, 200, 230, 110, "#FFF2CC", "#BF9000", "#7F6000", True),
        ("ubcc", "UBCC\n全局目录与仲裁", 680, 180, 230, 130, "#E2F0D9", "#70AD47", "#375623", True),
        ("dir", "ResidentDir\nSRAM 驻留目录", 980, 110, 170, 90, "#E2F0D9", "#70AD47", "#375623", False),
        ("back", "Backstore\n冷元数据后备存储", 980, 260, 170, 90, "#FCE4D6", "#C55A11", "#843C0C", False),
        ("net", "NetworkSim\n跨节点路由", 680, 410, 230, 90, "#EDEDED", "#7F7F7F", "#404040", True),
        ("peer", "其他节点的 EP / UBCC", 980, 410, 170, 90, "#D9EAF7", "#4F81BD", "#17365D", False),
    ]
    edges = [("cpu", "hnf", "本地访问"), ("hnf", "ep", "CHI 请求 / Snoop"),
             ("ep", "ubcc", "Outer 消息"), ("ubcc", "dir", "热元数据"),
             ("ubcc", "back", "换入 / 换出"), ("ubcc", "net", "跨节点消息"),
             ("net", "peer", "路由与时延")]
    drawio("ubcc-system-architecture", "UBCC 跨节点缓存一致性总体架构", boxes, edges)
    render_dot("ubcc-system-architecture", r'''digraph G {
graph [bgcolor="white", pad="0.35", nodesep="0.55", ranksep="0.75", fontname="Noto Sans CJK SC", label="UBCC 跨节点缓存一致性总体架构", labelloc=t, fontsize=24, fontcolor="#17365D"];
node [shape=box, style="rounded,filled", fontname="Noto Sans CJK SC", fontsize=15, margin="0.16,0.10", penwidth=1.5]; edge [fontname="Noto Sans CJK SC", fontsize=12, color="#5B6573", fontcolor="#404040", penwidth=1.5, arrowsize=0.8];
cpu [label="CPU / Cache", fillcolor="#D9EAF7", color="#4F81BD", fontcolor="#17365D"];
hnf [label="节点内 CHI / HN-F", fillcolor="#EAF2F8", color="#4F81BD", fontcolor="#17365D"];
ep [label="EP-RNF · EP-SNF\nUBAdapter", fillcolor="#FFF2CC", color="#BF9000", fontcolor="#7F6000"];
ubcc [label="UBCC\n全局目录与仲裁", fillcolor="#E2F0D9", color="#70AD47", fontcolor="#375623", width=2.1, height=0.9];
dir [label="ResidentDir\nSRAM 驻留目录", fillcolor="#E2F0D9", color="#70AD47", fontcolor="#375623"];
back [label="Backstore\n冷元数据后备存储", fillcolor="#FCE4D6", color="#C55A11", fontcolor="#843C0C"];
net [label="NetworkSim\n跨节点路由", fillcolor="#EDEDED", color="#7F7F7F", fontcolor="#404040"];
peer [label="其他节点的\nEP / UBCC", fillcolor="#D9EAF7", color="#4F81BD", fontcolor="#17365D"];
cpu -> hnf [label="本地访问"]; hnf -> ep [label="CHI 请求 / Snoop"]; ep -> ubcc [label="Outer 消息"];
ubcc -> dir [label="热元数据"]; ubcc -> back [label="换入 / 换出"]; ubcc -> net [label="跨节点消息"]; net -> peer [label="路由与时延"];
{rank=same; dir; back} }
''')


def protocol_paths():
    boxes = []
    y = 110
    for row, title in enumerate(("远程读", "所有权迁移", "共享转写者")):
        boxes += [
            (f"r{row}a", f"发起节点\n{title}", 50, y, 180, 80, "#D9EAF7", "#4F81BD", "#17365D", True),
            (f"r{row}b", "Home UBCC\n目录定位与仲裁", 330, y, 220, 80, "#E2F0D9", "#70AD47", "#375623", True),
            (f"r{row}c", "当前 Owner / Sharer\n数据或确认", 660, y, 220, 80, "#FFF2CC", "#BF9000", "#7F6000", False),
            (f"r{row}d", "完成\n数据与权限可用", 990, y, 170, 80, "#EAF2F8", "#4F81BD", "#17365D", True),
        ]
        y += 190
    edges = []
    labels = (("请求", "Recall / 数据", "授权"), ("写权限请求", "旧 Owner 释放", "新 Owner 获权"),
              ("写权限请求", "失效 / Ack", "单写者完成"))
    for row, row_labels in enumerate(labels):
        edges += [(f"r{row}a", f"r{row}b", row_labels[0]),
                  (f"r{row}b", f"r{row}c", row_labels[1]),
                  (f"r{row}c", f"r{row}b", "响应"),
                  (f"r{row}b", f"r{row}d", row_labels[2])]
    drawio("ubcc-protocol-paths", "UBCC 三类核心协议路径", boxes, edges)
    render_dot("ubcc-protocol-paths", r'''digraph G {
graph [bgcolor="white", pad="0.35", nodesep="0.55", ranksep="0.8", fontname="Noto Sans CJK SC", label="UBCC 三类核心协议路径", labelloc=t, fontsize=24, fontcolor="#17365D"];
node [shape=box, style="rounded,filled", fontname="Noto Sans CJK SC", fontsize=14, margin="0.14,0.09", penwidth=1.4]; edge [fontname="Noto Sans CJK SC", fontsize=11, color="#5B6573", fontcolor="#404040", penwidth=1.4];
subgraph cluster_read {label="远程读"; color="#B4C7E7"; style="rounded"; r1 [label="发起节点\n读请求", fillcolor="#D9EAF7", color="#4F81BD"]; h1 [label="Home UBCC\n目录定位与仲裁", fillcolor="#E2F0D9", color="#70AD47"]; o1 [label="当前 Owner\n返回最新数据", fillcolor="#FFF2CC", color="#BF9000"]; c1 [label="完成\n数据可用", fillcolor="#EAF2F8", color="#4F81BD"]; r1->h1 [label="请求"]; h1->o1 [label="Recall"]; o1->h1 [label="数据"]; h1->c1 [label="授权"] }
subgraph cluster_owner {label="所有权迁移"; color="#B4C7E7"; style="rounded"; r2 [label="新写者\n写权限请求", fillcolor="#D9EAF7", color="#4F81BD"]; h2 [label="Home UBCC\n定位最新数据", fillcolor="#E2F0D9", color="#70AD47"]; o2 [label="旧 Owner\n释放数据与权限", fillcolor="#FFF2CC", color="#BF9000"]; c2 [label="完成\n新 Owner 获权", fillcolor="#EAF2F8", color="#4F81BD"]; r2->h2; h2->o2 [label="Recall"]; o2->h2 [label="数据 / 释放"]; h2->c2 [label="Grant"] }
subgraph cluster_writer {label="共享转写者"; color="#B4C7E7"; style="rounded"; r3 [label="写者\n写权限请求", fillcolor="#D9EAF7", color="#4F81BD"]; h3 [label="Home UBCC\n冻结目标集合", fillcolor="#E2F0D9", color="#70AD47"]; o3 [label="Sharer 集合\n失效并确认", fillcolor="#FFF2CC", color="#BF9000"]; c3 [label="完成\n单写者权限", fillcolor="#EAF2F8", color="#4F81BD"]; r3->h3; h3->o3 [label="Invalidate"]; o3->h3 [label="Ack"]; h3->c3 [label="Grant"] }
}
''')


def verification():
    boxes = [
        ("spec", "协议设计与不变量", 90, 130, 220, 90, "#D9EAF7", "#4F81BD", "#17365D", True),
        ("tla", "TLA+ 形式化模型\nSafety · Liveness", 390, 130, 220, 90, "#E2F0D9", "#70AD47", "#375623", True),
        ("focus", "定向机制验证\n仲裁 · waiter · retry", 690, 130, 220, 90, "#FFF2CC", "#BF9000", "#7F6000", True),
        ("e2e", "端到端正确性验证\n数据 · 权限 · 完成", 990, 130, 170, 90, "#EAF2F8", "#4F81BD", "#17365D", True),
        ("fault", "Q1-Q5 故障资格\n52 / 52 通过", 390, 360, 220, 100, "#FCE4D6", "#C55A11", "#843C0C", True),
        ("topo", "多拓扑验证\n3N1S · 3N2S · 8N2S · 16N1S", 690, 360, 260, 100, "#EDEDED", "#7F7F7F", "#404040", True),
    ]
    edges = [("spec", "tla", "抽象"), ("tla", "focus", "机制映射"),
             ("focus", "e2e", "实现闭环"), ("focus", "fault", "故障扩展"),
             ("fault", "topo", "规模扩展"), ("topo", "e2e", "结果汇总")]
    drawio("ubcc-verification-stack", "UBCC 分层验证体系", boxes, edges)
    render_dot("ubcc-verification-stack", r'''digraph G {
graph [bgcolor="white", pad="0.35", nodesep="0.55", ranksep="0.75", fontname="Noto Sans CJK SC", label="UBCC 分层验证体系", labelloc=t, fontsize=24, fontcolor="#17365D"];
node [shape=box, style="rounded,filled", fontname="Noto Sans CJK SC", fontsize=14, margin="0.16,0.10", penwidth=1.5]; edge [fontname="Noto Sans CJK SC", fontsize=11, color="#5B6573", penwidth=1.5];
spec [label="协议设计与不变量", fillcolor="#D9EAF7", color="#4F81BD"];
tla [label="TLA+ 形式化模型\nSafety · Liveness", fillcolor="#E2F0D9", color="#70AD47"];
focus [label="定向机制验证\n仲裁 · waiter · retry", fillcolor="#FFF2CC", color="#BF9000"];
e2e [label="端到端正确性验证\n数据 · 权限 · 完成", fillcolor="#EAF2F8", color="#4F81BD"];
fault [label="Q1-Q5 故障资格\n52 / 52 通过", fillcolor="#FCE4D6", color="#C55A11"];
topo [label="多拓扑验证\n3N1S · 3N2S · 8N2S · 16N1S", fillcolor="#EDEDED", color="#7F7F7F"];
spec->tla [label="抽象"]; tla->focus [label="机制映射"]; focus->e2e [label="实现闭环"]; focus->fault [label="故障扩展"]; fault->topo [label="规模扩展"]; topo->e2e [label="结果汇总"];
}
''')


def two_phase():
    boxes = [
        ("req", "请求到达", 70, 210, 170, 80, "#D9EAF7", "#4F81BD", "#17365D", True),
        ("reserve", "阶段 1\n保留 epoch 与 intended state", 330, 180, 240, 140, "#FFF2CC", "#BF9000", "#7F6000", True),
        ("grant", "Grant 在途\n已提交目录保持安全状态", 670, 180, 240, 140, "#EAF2F8", "#4F81BD", "#17365D", True),
        ("clear", "阶段 2\nClear 校验并提交", 1010, 180, 170, 140, "#E2F0D9", "#70AD47", "#375623", True),
        ("retry", "相同 tuple 重试\n幂等返回", 670, 410, 240, 90, "#FCE4D6", "#C55A11", "#843C0C", False),
    ]
    edges = [("req", "reserve", "创建事务"), ("reserve", "grant", "发出授权"),
             ("grant", "clear", "确认完成"), ("grant", "retry", "消息丢失 / 延迟"),
             ("retry", "grant", "恢复原授权")]
    drawio("ubcc-two-phase-commit", "UBCC 两阶段目录提交", boxes, edges)
    render_dot("ubcc-two-phase-commit", r'''digraph G {
graph [bgcolor="white", pad="0.35", nodesep="0.6", ranksep="0.8", fontname="Noto Sans CJK SC", label="UBCC 两阶段目录提交", labelloc=t, fontsize=24, fontcolor="#17365D"];
node [shape=box, style="rounded,filled", fontname="Noto Sans CJK SC", fontsize=14, margin="0.16,0.10", penwidth=1.5]; edge [fontname="Noto Sans CJK SC", fontsize=11, color="#5B6573", penwidth=1.5];
req [label="请求到达", fillcolor="#D9EAF7", color="#4F81BD"];
reserve [label="阶段 1\n保留 epoch 与 intended state", fillcolor="#FFF2CC", color="#BF9000"];
grant [label="Grant 在途\n已提交目录保持安全状态", fillcolor="#EAF2F8", color="#4F81BD"];
clear [label="阶段 2\nClear 校验并提交", fillcolor="#E2F0D9", color="#70AD47"];
retry [label="相同 tuple 重试\n幂等返回", fillcolor="#FCE4D6", color="#C55A11"];
req->reserve [label="创建事务"]; reserve->grant [label="发出授权"]; grant->clear [label="确认完成"]; grant->retry [label="丢失 / 延迟"]; retry->grant [label="恢复原授权"];
}
''')


def charts():
    # SVG keeps typography crisp; Graphviz converts the same data to PNG.
    render_dot("ubcc-metric-summary", r'''digraph G {
graph [bgcolor="white", pad="0.4", nodesep="0.45", ranksep="0.5", fontname="Noto Sans CJK SC", label="UBCC 三项性能指标验收结果", labelloc=t, fontsize=24, fontcolor="#17365D"];
node [shape=box, style="rounded,filled", fontname="Noto Sans CJK SC", fontsize=16, margin="0.18,0.12", penwidth=1.6];
m1 [label="指标 1\n等效追踪容量 1.515×\n容量提升 51.509%\n通过", fillcolor="#E2F0D9", color="#70AD47", fontcolor="#375623"];
m2 [label="指标 2\n适用场景等权平均降幅\n64.759%\n通过", fillcolor="#D9EAF7", color="#4F81BD", fontcolor="#17365D"];
m3 [label="指标 3\n核心场景组约 20.09%\n代表场景组约 3.64%\n参考模型范围内通过", fillcolor="#FFF2CC", color="#BF9000", fontcolor="#7F6000"];
{rank=same; m1; m2; m3}
}
''')
    drawio("ubcc-metric-summary", "UBCC 三项性能指标验收结果", [
        ("m1", "指标 1\n等效追踪容量 1.515×\n容量提升 51.509%\n通过",
         70, 180, 300, 190, "#E2F0D9", "#70AD47", "#375623", True),
        ("m2", "指标 2\n适用场景等权平均降幅\n64.759%\n通过",
         450, 180, 300, 190, "#D9EAF7", "#4F81BD", "#17365D", True),
        ("m3", "指标 3\n核心场景组约 20.09%\n代表场景组约 3.64%\n参考模型范围内通过",
         830, 180, 300, 190, "#FFF2CC", "#BF9000", "#7F6000", True),
    ], [])
    render_dot("ubcc-ha-vi-comparison", r'''digraph G {
graph [bgcolor="white", pad="0.4", nodesep="0.35", ranksep="0.45", fontname="Noto Sans CJK SC", label="UBCC 与 HA-VI 聚合时延对比（ticks/op）", labelloc=t, fontsize=24, fontcolor="#17365D"];
node [shape=plain, fontname="Noto Sans CJK SC"];
chart [label=<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="9" COLOR="#B4C7E7">
<TR><TD BGCOLOR="#D9EAF7"><B>L3 压力</B></TD><TD BGCOLOR="#D9EAF7"><B>场景组</B></TD><TD BGCOLOR="#E2F0D9"><B>UBCC</B></TD><TD BGCOLOR="#FFF2CC"><B>HA-VI</B></TD><TD BGCOLOR="#D9EAF7"><B>UBCC 降幅</B></TD></TR>
<TR><TD>100%</TD><TD>核心场景组</TD><TD>31.440</TD><TD>39.344</TD><TD><B>20.090%</B></TD></TR>
<TR><TD>100%</TD><TD>代表场景组</TD><TD>76.178</TD><TD>79.060</TD><TD><B>3.645%</B></TD></TR>
<TR><TD>150%</TD><TD>核心场景组</TD><TD>31.406</TD><TD>39.346</TD><TD><B>20.179%</B></TD></TR>
<TR><TD>150%</TD><TD>代表场景组</TD><TD>76.195</TD><TD>79.073</TD><TD><B>3.640%</B></TD></TR>
</TABLE>>]; }
''')
    boxes = [
        ("hdr", "L3 压力    场景组              UBCC       HA-VI       UBCC 降幅",
         90, 110, 1020, 55, "#D9EAF7", "#4F81BD", "#17365D", True),
    ]
    rows = (
        ("r1", "100%        核心场景组       31.440   39.344   20.090%"),
        ("r2", "100%        代表场景组       76.178   79.060    3.645%"),
        ("r3", "150%        核心场景组       31.406   39.346   20.179%"),
        ("r4", "150%        代表场景组       76.195   79.073    3.640%"),
    )
    for index, row in enumerate(rows):
        boxes.append((row[0], row[1], 90, 185 + index * 82, 1020, 62,
                      "#FFFFFF" if index % 2 == 0 else "#F4F7FA",
                      "#B4C7E7", "#404040", False))
    drawio("ubcc-ha-vi-comparison", "UBCC 与 HA-VI 聚合时延对比（ticks/op）",
           boxes, [])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    architecture()
    protocol_paths()
    verification()
    two_phase()
    charts()
    print(f"generated figures in {OUT}")


if __name__ == "__main__":
    main()
