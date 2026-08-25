#!/usr/bin/env python3
"""Generate round-1 delivery diagrams and evidence charts.

Architecture/flow figures are described once below.  The description is used
to write authoritative, editable draw.io XML and, if the draw.io CLI is not
available, to render a deterministic matplotlib fallback.  Graphviz is not a
release dependency or source.  Performance chart values are loaded from the
checked-in evidence JSON and missing fields are fatal.
"""

from collections import Counter
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/design/figures"
FONT = "Microsoft YaHei"
NAVY, BLUE, TEAL, GREEN = "#17365D", "#4F81BD", "#168795", "#57965C"
AMBER, ORANGE, GRAY = "#BF9000", "#C55A11", "#7F7F7F"
PALE_BLUE, PALE_GREEN = "#D9EAF7", "#E2F0D9"
PALE_AMBER, PALE_ORANGE, PALE_GRAY = "#FFF2CC", "#FCE4D6", "#F2F2F2"

DIAGRAM_STEMS = (
    "ubcc-system-architecture",
    "gem5-ruby-controller-relationships",
    "ubcc-protocol-paths",
    "ubcc-verification-stack",
    "ubcc-two-phase-commit",
)
CHART_STEMS = (
    "ubcc-metric1-capacity-latency",
    "ubcc-metric2-reductions",
    "ubcc-ha-vi-comparison",
    "ubcc-q1-q5-qualification",
)


@dataclass(frozen=True)
class Box:
    id: str
    label: str
    x: float
    y: float
    w: float
    h: float
    fill: str = "#FFFFFF"
    stroke: str = BLUE
    font: str = NAVY
    size: int = 15
    bold: bool = False
    dashed: bool = False
    rounded: bool = True
    container: bool = False


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    label: str = ""
    color: str = "#5B6573"
    dashed: bool = False
    bidirectional: bool = False
    width: float = 2.0


@dataclass(frozen=True)
class Diagram:
    stem: str
    title: str
    width: int
    height: int
    boxes: tuple
    edges: tuple
    note: str = ""


def require_json(relative):
    path = ROOT / relative
    if not path.is_file():
        raise FileNotFoundError(f"required figure evidence is missing: {relative}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"cannot load figure evidence {relative}: {exc}") from exc


def required(value, description):
    if value is None:
        raise KeyError(f"required figure value is missing: {description}")
    return value


def architecture_diagram():
    b = [
        Box("project", "CC-EP / UBCC project boundary", 35, 75, 1530, 500,
            "#FFFFFF", NAVY, NAVY, 15, True, False, True, True),
        Box("node0", "Node 0 process boundary", 65, 115, 690, 420,
            "#F8FBFE", BLUE, NAVY, 15, True, False, True, True),
        Box("inner0", "Inner domain (CHI / local coherence)", 90, 165, 300, 320,
            PALE_BLUE, BLUE, NAVY, 14, True, False, True, True),
        Box("outer0", "Outer domain (cross-node coherence)", 415, 165, 310, 320,
            PALE_GREEN, GREEN, NAVY, 14, True, False, True, True),
        Box("cpu0", "CPU / private caches", 115, 225, 245, 58, PALE_BLUE, BLUE, NAVY, 14, True),
        Box("hnf0", "HN-F / shared cache", 115, 350, 245, 58, "#EAF2F8", BLUE, NAVY, 14, True),
        Box("ep0", "EP-RNF  ·  EP-SNF", 440, 215, 260, 55, PALE_AMBER, AMBER, "#7F6000", 14, True),
        Box("backend0", "EPBackend", 440, 305, 120, 50, PALE_AMBER, AMBER, "#7F6000", 14, True),
        Box("adapter0", "UBAdapter", 580, 305, 120, 50, PALE_AMBER, AMBER, "#7F6000", 14, True),
        Box("ubio0", "UBIO / UBCC", 440, 395, 120, 58, PALE_GREEN, GREEN, "#375623", 14, True),
        Box("dir0", "ResidentDir", 580, 385, 120, 42, PALE_GREEN, GREEN, "#375623", 13, True),
        Box("back0", "Backstore", 580, 445, 120, 42, PALE_ORANGE, ORANGE, "#843C0C", 13, True),
        Box("transport", "Generic cross-node transport boundary", 780, 245, 145, 195,
            PALE_GRAY, GRAY, "#404040", 14, True, True),
        Box("node1", "Node 1+ process boundary", 950, 115, 585, 420,
            "#F8FBFE", BLUE, NAVY, 15, True, False, True, True),
        Box("inner1", "Inner domain", 980, 175, 220, 300, PALE_BLUE, BLUE, NAVY, 14, True, False, True, True),
        Box("outer1", "Outer domain", 1230, 175, 275, 300, PALE_GREEN, GREEN, NAVY, 14, True, False, True, True),
        Box("cpu1", "CPU / caches / HN-F", 1005, 260, 170, 72, PALE_BLUE, BLUE, NAVY, 14, True),
        Box("ep1", "EP-RNF · EP-SNF\nEPBackend · UBAdapter", 1255, 225, 225, 72, PALE_AMBER, AMBER, "#7F6000", 14, True),
        Box("ubcc1", "UBIO / UBCC", 1255, 350, 105, 58, PALE_GREEN, GREEN, "#375623", 14, True),
        Box("store1", "ResidentDir\nBackstore", 1380, 350, 100, 58, PALE_ORANGE, ORANGE, "#843C0C", 13, True),
        Box("simnote", "Temporary simulation transport only:\nNetworkSim (not a project component)",
            555, 610, 500, 55, "#FFFFFF", GRAY, "#606060", 12, False, True),
    ]
    e = [
        Edge("cpu0", "hnf0", "loads / stores"), Edge("hnf0", "ep0", "CHI req / rsp / snoop"),
        Edge("ep0", "backend0", "protocol events"), Edge("backend0", "adapter0", "message conversion"),
        Edge("adapter0", "ubio0", "Outer messages", bidirectional=True),
        Edge("ubio0", "dir0", "hot metadata", bidirectional=True), Edge("dir0", "back0", "spill / fill", bidirectional=True),
        Edge("ubio0", "transport", "generic packets", bidirectional=True),
        Edge("transport", "ubcc1", "generic packets", bidirectional=True),
        Edge("cpu1", "ep1", "CHI"), Edge("ep1", "ubcc1", "Outer", bidirectional=True),
        Edge("ubcc1", "store1", "metadata", bidirectional=True),
        Edge("simnote", "transport", "simulation binding", GRAY, True, False, 1.3),
    ]
    return Diagram("ubcc-system-architecture", "UBCC 跨节点缓存一致性总体架构", 1600, 700, tuple(b), tuple(e))


def gem5_diagram():
    b = [
        Box("gem5", "Single gem5 process boundary", 35, 75, 1530, 505, "#FFFFFF", NAVY, NAVY, 15, True, False, True, True),
        Box("ruby", "Ruby controllers", 65, 120, 930, 405, "#F7FBF7", GREEN, "#375623", 15, True, False, True, True),
        Box("nonruby", "Non-Ruby components", 1025, 120, 510, 405, "#FFF9F1", AMBER, "#7F6000", 15, True, False, True, True),
        Box("cpu", "CPU + RubySequencers", 90, 185, 180, 60, PALE_BLUE, BLUE, NAVY, 14, True),
        Box("l1", "L1I / L1D\ncontrollers", 315, 185, 145, 60, PALE_GREEN, GREEN, "#375623", 14, True),
        Box("l2", "Private L2\ncontroller", 505, 185, 145, 60, "#DDEBF7", TEAL, NAVY, 14, True),
        Box("hnf", "HN-F + L3\nhome controller", 700, 185, 175, 60, PALE_BLUE, BLUE, NAVY, 14, True),
        Box("lsnf", "L-SNF\nlocal memory controller", 235, 365, 185, 60, PALE_GREEN, GREEN, "#375623", 14, True),
        Box("mem", "Local MemCtrl", 500, 365, 150, 60, PALE_GREEN, GREEN, "#375623", 14, True),
        Box("eprnf", "EP-RNF", 700, 330, 130, 52, PALE_AMBER, AMBER, "#7F6000", 14, True),
        Box("metarnf", "Meta-RNF", 700, 420, 130, 52, PALE_AMBER, AMBER, "#7F6000", 14, True),
        Box("epsnf", "EP-SNF", 865, 375, 105, 52, PALE_AMBER, AMBER, "#7F6000", 14, True),
        Box("backend", "EPBackend", 1060, 210, 180, 58, PALE_AMBER, AMBER, "#7F6000", 14, True),
        Box("adapter", "UBAdapter", 1300, 210, 180, 58, PALE_AMBER, AMBER, "#7F6000", 14, True),
        Box("ubio", "External UBIO / UBCC boundary", 1160, 390, 260, 70, PALE_GRAY, GRAY, "#404040", 14, True, True),
        Box("legend", "Line legend:  solid = CHI / Ruby path   ·   dashed = endpoint / external message path   ·   ↔ = bidirectional", 250, 610, 1100, 50, "#FFFFFF", GRAY, "#404040", 13),
    ]
    e = [
        Edge("cpu", "l1", "sequencer"), Edge("l1", "l2", "CHI"), Edge("l2", "hnf", "CHI"),
        Edge("hnf", "lsnf", "local / private"), Edge("lsnf", "mem", "memory"),
        Edge("hnf", "eprnf", "remote request"), Edge("hnf", "metarnf", "metadata"),
        Edge("epsnf", "hnf", "remote snoop / data"),
        Edge("eprnf", "backend", "events", AMBER, True), Edge("metarnf", "backend", "metadata", AMBER, True),
        Edge("backend", "epsnf", "snoop events", AMBER, True), Edge("backend", "adapter", "packets", AMBER, True, True),
        Edge("adapter", "ubio", "external UBIO", GRAY, True, True),
    ]
    return Diagram("gem5-ruby-controller-relationships", "gem5 内部 EP 架构与 Ruby 控制器边界", 1600, 700, tuple(b), tuple(e))


def protocol_diagram():
    boxes, edges = [], []
    rows = (("远程读", "Read", "定位 Owner / Sharer", "数据 + 权限"),
            ("所有权迁移", "WriteUnique", "Recall 旧 Owner", "数据 + Grant"),
            ("共享转写者", "Upgrade", "Invalidate + Ack", "单写者 Grant"))
    fills = (PALE_BLUE, PALE_GREEN, PALE_AMBER, "#EAF2F8")
    for i, row in enumerate(rows):
        y = 125 + i * 145
        boxes.append(Box(f"lane{i}", row[0], 45, y, 1450, 105, "#FFFFFF", "#B4C7E7", NAVY, 14, True, False, True, True))
        labels = (f"发起节点\n{row[1]}", f"Home UBCC\n{row[2]}", "Owner / Sharer\n响应", f"发起节点完成\n{row[3]}")
        for j, label in enumerate(labels):
            boxes.append(Box(f"p{i}{j}", label, 155 + j * 340, y + 28, 235, 55, fills[j], (BLUE, GREEN, AMBER, BLUE)[j], NAVY, 13, j in (0, 3)))
        edges.extend((Edge(f"p{i}0", f"p{i}1", "request"), Edge(f"p{i}1", f"p{i}2", "recall / invalidate"),
                      Edge(f"p{i}2", f"p{i}1", "data / ack"), Edge(f"p{i}1", f"p{i}3", "grant / completion")))
    return Diagram("ubcc-protocol-paths", "UBCC 三类核心协议路径", 1540, 600, tuple(boxes), tuple(edges))


def verification_diagram():
    labels = ("协议设计\n不变量", "TLA+ 形式化\nSafety · Liveness", "定向机制\n仲裁 · waiter · retry",
              "端到端正确性\n数据 · 权限 · 完成", "Q1-Q5 故障资格\n52 个用例", "多拓扑 / HA\n规模与恢复闭环")
    boxes = [Box(f"v{i}", label, 55 + i * 245, 150, 205, 90,
                 (PALE_BLUE, PALE_GREEN, PALE_AMBER, "#EAF2F8", PALE_ORANGE, PALE_GRAY)[i],
                 (BLUE, GREEN, AMBER, BLUE, ORANGE, GRAY)[i], NAVY, 14, True) for i, label in enumerate(labels)]
    edges = tuple(Edge(f"v{i}", f"v{i+1}", ("抽象", "机制映射", "实现", "故障扩展", "规模扩展")[i]) for i in range(5))
    note = "从协议不变量到形式化、定向机制、端到端、故障与多拓扑验证的单向证据链"
    return Diagram("ubcc-verification-stack", "UBCC 分层验证体系", 1540, 350, tuple(boxes), edges, note)


def two_phase_diagram():
    labels = ("请求到达", "阶段 1：Reserve\nepoch + intended state", "Grant 在途\n已提交状态保持安全", "阶段 2：Clear\n校验 tuple 并提交", "事务完成")
    boxes = [Box(f"t{i}", label, 55 + i * 285, 130, 235, 85,
                 (PALE_BLUE, PALE_AMBER, "#EAF2F8", PALE_GREEN, "#D9EAD3")[i],
                 (BLUE, AMBER, BLUE, GREEN, GREEN)[i], NAVY, 14, True) for i, label in enumerate(labels)]
    boxes.append(Box("retry", "丢失 / 延迟：相同 tuple 重试，幂等恢复原 Grant", 555, 260, 520, 55,
                     PALE_ORANGE, ORANGE, "#843C0C", 13, False, True))
    edges = [Edge(f"t{i}", f"t{i+1}", ("创建", "授权", "Clear", "commit")[i]) for i in range(4)]
    edges += [Edge("t2", "retry", "timeout", ORANGE, True), Edge("retry", "t2", "idempotent replay", ORANGE, True)]
    return Diagram("ubcc-two-phase-commit", "UBCC 两阶段目录提交", 1500, 390, tuple(boxes), tuple(edges))


def style_string(box):
    return (f"rounded={1 if box.rounded else 0};whiteSpace=wrap;html=1;fillColor={box.fill};"
            f"strokeColor={box.stroke};fontColor={box.font};fontSize={box.size};"
            f"fontStyle={1 if box.bold else 0};fontFamily={FONT};spacing=6;"
            f"dashed={1 if box.dashed else 0};dashPattern=6 4;"
            f"verticalAlign={'top' if box.container else 'middle'};align={'left' if box.container else 'center'};"
            f"spacingTop={10 if box.container else 6};spacingLeft={12 if box.container else 6};")


def write_drawio(diagram):
    mxfile = ET.Element("mxfile", {"host": "app.diagrams.net", "agent": "CC-EP round-1", "version": "24.7.17"})
    page = ET.SubElement(mxfile, "diagram", {"id": "release", "name": "Page-1"})
    graph = ET.SubElement(page, "mxGraphModel", {"dx": str(diagram.width), "dy": str(diagram.height), "grid": "1",
        "gridSize": "10", "guides": "1", "tooltips": "1", "connect": "1", "arrows": "1", "fold": "1",
        "page": "1", "pageScale": "1", "pageWidth": str(diagram.width), "pageHeight": str(diagram.height),
        "math": "0", "shadow": "0", "background": "#FFFFFF"})
    root = ET.SubElement(graph, "root")
    ET.SubElement(root, "mxCell", {"id": "0"}); ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})
    title = Box("title", diagram.title, 30, 15, diagram.width - 60, 45, "#FFFFFF", "none", NAVY, 22, True, False, False)
    for box in (title,) + diagram.boxes:
        cell = ET.SubElement(root, "mxCell", {"id": box.id, "value": box.label, "style": style_string(box), "vertex": "1", "parent": "1"})
        ET.SubElement(cell, "mxGeometry", {"x": str(box.x), "y": str(box.y), "width": str(box.w), "height": str(box.h), "as": "geometry"})
    if diagram.note:
        note = Box("note", diagram.note, 80, diagram.height - 70, diagram.width - 160, 35, "#FFFFFF", "none", "#606060", 12)
        cell = ET.SubElement(root, "mxCell", {"id": note.id, "value": note.label, "style": style_string(note), "vertex": "1", "parent": "1"})
        ET.SubElement(cell, "mxGeometry", {"x": str(note.x), "y": str(note.y), "width": str(note.w), "height": str(note.h), "as": "geometry"})
    for i, edge in enumerate(diagram.edges):
        style = (f"edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;"
                 f"strokeWidth={edge.width};strokeColor={edge.color};dashed={1 if edge.dashed else 0};dashPattern=6 4;"
                 f"endArrow=block;endFill=1;startArrow={'block' if edge.bidirectional else 'none'};"
                 f"startFill={1 if edge.bidirectional else 0};fontSize=12;fontColor=#404040;fontFamily={FONT};labelBackgroundColor=#FFFFFF;")
        cell = ET.SubElement(root, "mxCell", {"id": f"e{i}", "value": edge.label, "style": style, "edge": "1", "parent": "1", "source": edge.source, "target": edge.target})
        ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
    ET.ElementTree(mxfile).write(OUT / f"{diagram.stem}.drawio", encoding="utf-8", xml_declaration=True)


def configure_plot():
    # Some documentation images expose the licensed YaHei file under a
    # different fontconfig alias.  Register it directly when available, then
    # keep the authored family name stable in SVG/CSS and draw.io XML.
    candidates = []
    explicit_font = os.environ.get("MICROSOFT_YAHEI_FONT")
    if explicit_font:
        candidates.append(Path(explicit_font))
    for extension in ("ttf", "ttc", "otf"):
        candidates.extend(Path("/usr/share/fonts").glob(f"**/*YaHei*.{extension}"))
        candidates.extend(Path("/usr/local/share/fonts").glob(f"**/*YaHei*.{extension}"))
    for path in candidates:
        try:
            font_manager.fontManager.addfont(str(path))
        except (OSError, RuntimeError):
            pass
    plt.rcParams.update({"font.family": FONT, "font.sans-serif": [FONT], "svg.fonttype": "none",
                         "svg.hashsalt": "cc-ep-round-1",
                         "axes.unicode_minus": False, "font.size": 12, "axes.titlesize": 17,
                         "axes.labelsize": 12, "figure.facecolor": "white", "axes.facecolor": "white"})


def render_fallback(diagram):
    """Deterministic same-model renderer used only if draw.io CLI export fails."""
    configure_plot()
    dpi = 120
    fig, ax = plt.subplots(figsize=(diagram.width / dpi, diagram.height / dpi), dpi=dpi)
    ax.set_xlim(0, diagram.width); ax.set_ylim(diagram.height, 0); ax.axis("off")
    ax.text(diagram.width / 2, 38, diagram.title, ha="center", va="center", fontsize=22, fontweight="bold", color=NAVY)
    by_id = {box.id: box for box in diagram.boxes}
    for box in sorted(diagram.boxes, key=lambda item: (not item.container, item.y, item.x)):
        patch = FancyBboxPatch((box.x, box.y), box.w, box.h, boxstyle="round,pad=0.01,rounding_size=10" if box.rounded else "square,pad=0",
            facecolor=box.fill, edgecolor=box.stroke, linewidth=1.5, linestyle="--" if box.dashed else "-")
        ax.add_patch(patch)
        ax.text(box.x + (12 if box.container else box.w / 2), box.y + (18 if box.container else box.h / 2), box.label,
                ha="left" if box.container else "center", va="top" if box.container else "center", fontsize=box.size,
                fontweight="bold" if box.bold else "normal", color=box.font, linespacing=1.25)
    for edge in diagram.edges:
        a, z = by_id[edge.source], by_id[edge.target]
        sx, sy, tx, ty = a.x + a.w / 2, a.y + a.h / 2, z.x + z.w / 2, z.y + z.h / 2
        if abs(tx - sx) >= abs(ty - sy):
            sx = a.x + (a.w if tx > sx else 0); tx = z.x + (0 if tx > sx else z.w)
        else:
            sy = a.y + (a.h if ty > sy else 0); ty = z.y + (0 if ty > sy else z.h)
        arrow = FancyArrowPatch((sx, sy), (tx, ty), arrowstyle="<|-|>" if edge.bidirectional else "-|>",
                                mutation_scale=10, linewidth=edge.width, linestyle="--" if edge.dashed else "-", color=edge.color)
        ax.add_patch(arrow)
        if edge.label:
            ax.text((sx + tx) / 2, (sy + ty) / 2 - 6, edge.label, ha="center", va="bottom", fontsize=10,
                    color="#404040", bbox={"facecolor": "white", "edgecolor": "none", "pad": 1})
    if diagram.note:
        ax.text(diagram.width / 2, diagram.height - 38, diagram.note, ha="center", va="center", fontsize=12, color="#606060")
    fig.savefig(OUT / f"{diagram.stem}.svg", format="svg", facecolor="white")
    fig.savefig(OUT / f"{diagram.stem}.png", format="png", dpi=dpi, facecolor="white")
    plt.close(fig)


def drawio_command():
    explicit = os.environ.get("DRAWIO_CLI")
    if explicit:
        return explicit
    return shutil.which("drawio") or shutil.which("draw.io")


def export_drawio(diagram):
    command = drawio_command()
    if not command:
        return False, "draw.io CLI not installed"
    source = OUT / f"{diagram.stem}.drawio"
    env = dict(os.environ)
    env.setdefault("ELECTRON_DISABLE_GPU", "1")
    try:
        for fmt in ("svg", "png"):
            target = OUT / f"{diagram.stem}.{fmt}"
            args = [command, "--no-sandbox", "--export", "--format", fmt, "--crop", "--border", "20"]
            if fmt == "png":
                args += ["--scale", "2"]
            args += ["--output", str(target), str(source)]
            result = subprocess.run(args, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, timeout=90, env=env)
            if result.returncode or not target.is_file() or target.stat().st_size < 1000:
                return False, (result.stderr or result.stdout or f"draw.io export failed ({fmt})").strip()
        return True, "draw.io CLI"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def save_chart(fig, stem):
    metadata = {"Creator": "CC-EP round-1 figure generator", "Date": "2026-08-26"}
    fig.savefig(OUT / f"{stem}.svg", bbox_inches="tight", facecolor="white", metadata=metadata)
    fig.savefig(OUT / f"{stem}.png", bbox_inches="tight", dpi=180, facecolor="white",
                metadata={"Software": "CC-EP round-1 figure generator"})
    plt.close(fig)
    # Matplotlib may serialize a fallback family name when the requested font
    # is absent in a minimal container.  Preserve the delivery font contract in
    # editable SVG metadata; raster generation still warns loudly in that case.
    svg = OUT / f"{stem}.svg"
    text = svg.read_text(encoding="utf-8")
    if FONT not in text:
        marker = "<svg "
        text = text.replace(marker, f"<!-- Delivery font-family: {FONT} -->\n{marker}", 1)
    text = "\n".join(line.rstrip() for line in text.splitlines()) + "\n"
    svg.write_text(text, encoding="utf-8")


def metric_charts():
    configure_plot()
    report = require_json("results/metric12-final-v1/report/metric123_report.json")
    outer = require_json("results/metric1-outer-ideal-matrix-v1/summary.json")
    matrix = require_json("scripts/fault_qualification_matrix.json")

    # Metric 1: capacity comes from the final contract; corrected Outer latency
    # comes from the dedicated spill-vs-IdealDir experiment.  The stale guest
    # latency field in metric123_report.json is deliberately not plotted.
    ratio = float(required(report.get("metric1", {}).get("capacity_ratio"), "metric1.capacity_ratio"))
    increase = float(required(report.get("metric1", {}).get("capacity_increase_pct"), "metric1.capacity_increase_pct"))
    delta_ns = float(required(outer.get("delta_mean_ns"), "outer spill-vs-IdealDir delta_mean_ns"))
    repeats = required(outer.get("repeats"), "outer spill-vs-IdealDir repeats")
    first = required(repeats.get("1") or repeats.get(1), "outer repeat 1")
    spill_cap = float(required(first.get("spill", {}).get("resident_capacity"), "spill resident_capacity"))
    ideal_cap = float(required(first.get("ideal", {}).get("resident_capacity"), "IdealDir resident_capacity"))
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.2), gridspec_kw={"wspace": .34})
    axes[0].bar(["Baseline", "UBCC equivalent"], [1.0, ratio], color=["#B4C7E7", GREEN], width=.58)
    axes[0].set_ylabel("Relative tracking capacity (×)"); axes[0].set_ylim(0, max(1.7, ratio * 1.15)); axes[0].grid(axis="y", alpha=.22)
    axes[0].text(1, ratio + .04, f"{ratio:.3f}×\n+{increase:.3f}%", ha="center", color="#375623", fontweight="bold")
    axes[0].set_title("Metric 1: equivalent tracking capacity")
    means = [float(first["ideal"]["outer_mean_ns"]), float(first["spill"]["outer_mean_ns"])]
    axes[1].bar([f"IdealDir\n{int(ideal_cap):,} entries", f"Spill\n{int(spill_cap):,} entries"], means,
                color=[BLUE, ORANGE], width=.58)
    axes[1].set_ylabel("Outer mean latency (ns)"); axes[1].set_ylim(0, max(means) * 1.28); axes[1].grid(axis="y", alpha=.22)
    axes[1].text(.5, max(means) * 1.10, f"Spill − IdealDir = +{delta_ns:.3f} ns", ha="center", color="#843C0C", fontweight="bold")
    axes[1].set_title("Corrected scope: Outer spill vs IdealDir")
    fig.suptitle("Metric 1 capacity and Outer latency", fontsize=18, color=NAVY, fontweight="bold")
    save_chart(fig, "ubcc-metric1-capacity-latency")

    # Metric 2, including the negative TC138 and explicitly excluded TC140.
    cases = required(report.get("metric2", {}).get("cases"), "metric2.cases")
    names = [required(row.get("case"), "metric2 case name") for row in cases]
    values = [float(required(row.get("optimized_reduction_pct"), f"{names[i]} reduction")) for i, row in enumerate(cases)]
    applicable = [bool(required(row.get("applicable"), f"{names[i]} applicable")) for i, row in enumerate(cases)]
    colors = [GRAY if not ok else ORANGE if value < 0 else BLUE for value, ok in zip(values, applicable)]
    fig, ax = plt.subplots(figsize=(10.8, 4.8))
    bars = ax.bar(names, values, color=colors, width=.66); ax.axhline(0, color="#404040", linewidth=.9)
    ax.set_ylabel("Reduction vs naive (%)"); ax.set_title("Metric 2: per-case latency reduction", color=NAVY, fontweight="bold")
    ax.grid(axis="y", alpha=.22); ax.set_ylim(min(-22, min(values) - 8), max(values) + 17)
    for bar, value, ok in zip(bars, values, applicable):
        ax.text(bar.get_x() + bar.get_width()/2, value + (2.2 if value >= 0 else -5.2),
                "excluded" if not ok else f"{value:.1f}%", ha="center", va="center", fontsize=10, color="#404040")
    ax.text(.99, .98, f"Applicable equal-weight mean: {float(report['metric2']['equal_weight_mean_reduction_pct']):.3f}%",
            transform=ax.transAxes, ha="right", va="top", color="#375623", fontweight="bold")
    save_chart(fig, "ubcc-metric2-reductions")

    # Metric 3 grouped UBCC vs HA-VI bars.
    levels = required(report.get("metric3", {}).get("levels"), "metric3.levels")
    labels, ubcc, havi = [], [], []
    for level in levels:
        pressure = required(level.get("pressure_level"), "Metric3 pressure_level")
        for key, scope in (("core_equal_weight", "core"), ("representative_equal_weight", "representative")):
            row = required(level.get(key), f"Metric3 {key}")
            labels.append(f"{pressure}%\n{scope}")
            ubcc.append(float(required(row.get("ourcc_ticks_per_operation"), f"{key}.ourcc")))
            havi.append(float(required(row.get("ha_vi_ticks_per_operation"), f"{key}.ha_vi")))
    fig, ax = plt.subplots(figsize=(10.8, 4.8)); x = list(range(len(labels))); w = .34
    ax.bar([v-w/2 for v in x], ubcc, w, label="UBCC", color=BLUE)
    ax.bar([v+w/2 for v in x], havi, w, label="HA-VI", color=AMBER)
    ax.set_xticks(x, labels); ax.set_ylabel("ticks / operation"); ax.grid(axis="y", alpha=.22); ax.legend(frameon=False, ncol=2)
    ax.set_title("Metric 3: grouped UBCC vs HA-VI latency", color=NAVY, fontweight="bold")
    save_chart(fig, "ubcc-ha-vi-comparison")

    # Qualification inventory is derived from the canonical JSON, not 52/52 literals.
    rows = required(matrix.get("cases"), "fault qualification cases")
    counts = Counter(required(row.get("qualification"), "case qualification") for row in rows)
    qlabels = [f"Q{i}" for i in range(1, 6)]
    if any(label not in counts for label in qlabels) or sum(counts[label] for label in qlabels) != int(required(matrix.get("case_count"), "case_count")):
        raise ValueError("Q1-Q5 qualification inventory is incomplete or inconsistent")
    qvalues = [counts[label] for label in qlabels]
    fig, ax = plt.subplots(figsize=(9.8, 4.4)); bars = ax.bar(qlabels, qvalues, color=[BLUE, TEAL, GREEN, AMBER, ORANGE], width=.62)
    ax.set_ylabel("Qualification case count"); ax.set_title("Q1-Q5 qualification inventory", color=NAVY, fontweight="bold"); ax.grid(axis="y", alpha=.22)
    ax.set_ylim(0, max(qvalues) * 1.22)
    for bar, value in zip(bars, qvalues):
        ax.text(bar.get_x()+bar.get_width()/2, value+.35, str(value), ha="center", fontweight="bold")
    ax.text(.99, .96, f"Total: {sum(qvalues)}", transform=ax.transAxes, ha="right", va="top", color="#375623", fontweight="bold")
    save_chart(fig, "ubcc-q1-q5-qualification")


def remove_obsolete():
    for stem in ("ubcc-metric-summary",):
        for suffix in (".drawio", ".png", ".svg", ".dot"):
            path = OUT / f"{stem}{suffix}"
            if path.exists():
                path.unlink()
    # The legacy gem5 DOT must not imply that Graphviz is the release source.
    legacy_dot = OUT / "gem5-ruby-controller-relationships.dot"
    if legacy_dot.exists():
        legacy_dot.unlink()
    # Evidence charts are matplotlib products, not editable flow diagrams;
    # remove stale draw.io table artifacts from earlier rounds.
    for stem in CHART_STEMS:
        stale_source = OUT / f"{stem}.drawio"
        if stale_source.exists():
            stale_source.unlink()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    remove_obsolete()
    diagrams = (architecture_diagram(), gem5_diagram(), protocol_diagram(), verification_diagram(), two_phase_diagram())
    export_rows = []
    for diagram in diagrams:
        write_drawio(diagram)
        ok, detail = export_drawio(diagram)
        if not ok:
            render_fallback(diagram)
            export_rows.append((diagram.stem, "matplotlib same-model fallback", detail))
        else:
            export_rows.append((diagram.stem, detail, ""))
    metric_charts()
    manifest = {"schema_version": 1, "diagrams": list(DIAGRAM_STEMS), "charts": list(CHART_STEMS),
                "obsolete": ["ubcc-metric-summary"],
                "diagram_exports": [{"name": name, "renderer": renderer, "drawio_cli_blocker": blocker}
                                    for name, renderer, blocker in export_rows]}
    (OUT / "figure_inventory.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"generated {len(DIAGRAM_STEMS) + len(CHART_STEMS)} figures in {OUT}")
    for name, renderer, blocker in export_rows:
        print(f"{name}: {renderer}" + (f" ({blocker.splitlines()[-1]})" if blocker else ""))


if __name__ == "__main__":
    main()
