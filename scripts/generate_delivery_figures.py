#!/usr/bin/env python3
"""Generate round-1 delivery diagrams and evidence charts.

Architecture/flow figures are described once below.  The description is used
to write authoritative, editable draw.io XML and, if the draw.io CLI is not
available, to render a deterministic matplotlib fallback.  Graphviz is not a
release dependency or source.  Performance chart values are loaded from the
checked-in evidence JSON and missing fields are fatal.
"""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import argparse
import shutil
import subprocess
import tempfile
from typing import Optional
import xml.etree.ElementTree as ET

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle
except ModuleNotFoundError:  # Metadata-only validation does not require rendering dependencies.
    matplotlib = plt = font_manager = None
    FancyArrowPatch = FancyBboxPatch = Rectangle = None


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/design/figures"
FONT = "Microsoft YaHei"
MATH_FONT = "STIX Two Math"
MATH_FONT_PATH = ROOT / "docs/fonts/stix-math/STIXTwoMath-Regular.ttf"
FONTCONFIG_FILE = ROOT / "docs/fonts/fonts.conf"
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
    "ubcc-protocol-authority-comparison",
    "ubcc-path-central-vs-direct",
    "ubcc-metadata-fanout-scaling",
    "ubcc-inner-chi-outer-boundary",
)
CHART_STEMS = (
    "ubcc-metric1-capacity-latency",
    "ubcc-metric2-reductions",
    "ubcc-ha-vi-comparison",
    "ubcc-tc120-124-scenarios",
    "ubcc-tc130-134-pressure",
    "ubcc-tc142-147-applications",
    "ubcc-metric3-per-tc-reductions",
    "ubcc-metric1-extension-matrix",
)

GENERATOR = "scripts/generate_delivery_figures.py::metric_charts"
PUBLICATION_DATA = "docs/design/performance_publication_data.json"


def publication_sources(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("metric_definitions_version") != "metric123-publication-v1":
        raise ValueError("publication JSON requires metric123-publication-v1")
    metric1, metric2, metric3 = data["metric1"], data["metric2"], data["metric3"]
    report = {
        "metric1": {
            "capacity_ratio": metric1["capacity_ratio"],
            "capacity_increase_pct": metric1["capacity_increase_pct"],
        },
        "metric2": {
            "cases": metric2["cases"],
            "equal_weight_mean_reduction_pct": metric2["applicable_equal_weight_mean_reduction_pct"],
        },
        "metric3": {"levels": [{
            "pressure_level": metric3.get("pressure_level", 100),
            "core_equal_weight": next(row for row in metric3["groups"] if row["scope"] == "core"),
            "representative_equal_weight": next(row for row in metric3["groups"] if row["scope"] == "representative"),
        }]},
    }
    outer = {"delta_mean_ns": metric1["outer_delta_mean_ns"], "repeats": {}}
    for index, row in enumerate(metric1["repetitions"], 1):
        outer["repeats"][str(index)] = {
            "spill": {"resident_capacity": row["spill_resident_capacity"],
                      "outer_mean_ns": row["spill_outer_mean_ns"]},
            "ideal": {"resident_capacity": row["ideal_resident_capacity"],
                      "outer_mean_ns": row["ideal_outer_mean_ns"]},
        }
    charts = data.get("charts", {})
    preview = {
        "tc120_124": charts.get("tc120_124", []),
        "tc130_134": charts.get("tc130_134", []),
        "tc142_147": charts.get("tc142_147", []),
        "metric1_matrix": charts.get("metric1_matrix", []),
        "metric3_per_tc": data["metric3"].get("per_testcase", []),
    }
    preview["metric3_per_tc"] = [
        {"case": row["case"][2:], "reduction_pct": row["ourcc_reduction_pct"]}
        for row in preview["metric3_per_tc"]
    ]
    return report, outer, preview

DIAGRAM_DOCUMENT_REFERENCES = {
    "ubcc-system-architecture": [{"document": "docs/design/cc_ep_protocol_overview.md", "figure": "图 2-1"}, {"document": "docs/design/cc_ep_protocol_overview.docx", "figure": "图 2-1"}],
    "gem5-ruby-controller-relationships": [{"document": "docs/design/cc_ep_protocol_overview.md", "figure": "图 3-1"}, {"document": "docs/design/cc_ep_protocol_overview.docx", "figure": "图 3-1"}],
    "ubcc-protocol-paths": [{"document": "docs/design/cc_ep_protocol_overview.md", "figure": "图 5-1"}, {"document": "docs/design/cc_ep_protocol_overview.docx", "figure": "图 5-1"}],
    "ubcc-verification-stack": [{"document": "docs/design/cc_ep_deliverable2_verification_reliability_ha.md", "figure": "图 1-1"}, {"document": "docs/design/cc_ep_deliverable2_verification_reliability_ha.docx", "figure": "图 1-1"}],
    "ubcc-two-phase-commit": [{"document": "docs/design/cc_ep_deliverable2_verification_reliability_ha.md", "figure": "图 4-1"}, {"document": "docs/design/cc_ep_deliverable2_verification_reliability_ha.docx", "figure": "图 4-1"}],
    "ubcc-protocol-authority-comparison": [{"document": "docs/design/cc_ep_protocol_overview.md", "figure": "图 7-1"}],
    "ubcc-path-central-vs-direct": [{"document": "docs/design/cc_ep_protocol_overview.md", "figure": "图 7-2"}],
    "ubcc-metadata-fanout-scaling": [{"document": "docs/design/cc_ep_protocol_overview.md", "figure": "图 7-3"}],
    "ubcc-inner-chi-outer-boundary": [{"document": "docs/design/cc_ep_protocol_overview.md", "figure": "图 7-4"}],
}
CHART_DOCUMENT_REFERENCES = {
    "ubcc-metric1-capacity-latency": [{"document": "docs/design/cc_ep_deliverable3_performance_api.md", "figure": "图 3-1"}, {"document": "docs/design/cc_ep_deliverable3_performance_api.docx", "figure": "图 3-1"}],
    "ubcc-metric2-reductions": [{"document": "docs/design/cc_ep_deliverable3_performance_api.md", "figure": "图 4-1"}, {"document": "docs/design/cc_ep_deliverable3_performance_api.docx", "figure": "图 4-1"}],
    "ubcc-ha-vi-comparison": [{"document": "docs/design/cc_ep_deliverable3_performance_api.md", "figure": "图 5-1"}, {"document": "docs/design/cc_ep_deliverable3_performance_api.docx", "figure": "图 5-1"}],
    "ubcc-metric1-extension-matrix": [{"document": "docs/design/cc_ep_deliverable3_performance_api.md", "figure": "图 3-3"}, {"document": "docs/design/cc_ep_deliverable3_performance_api.docx", "figure": "图 3-3"}],
}


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
    font_family: str = FONT


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    label: str = ""
    color: str = "#5B6573"
    dashed: bool = False
    bidirectional: bool = False
    width: float = 2.0
    label_x: float = 0.0
    label_y: float = 0.0
    source_x: Optional[float] = None
    source_y: Optional[float] = None
    target_x: Optional[float] = None
    target_y: Optional[float] = None
    waypoints: tuple = field(default_factory=tuple)


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
        Box("node0", "Node 0", 35, 95, 520, 445, "#F8FBFE", BLUE, NAVY, 16, True, False, False, True),
        Box("inner0", "Inner domain", 60, 145, 215, 345, PALE_BLUE, "#B4C7E7", NAVY, 13, True, False, False, True),
        Box("outer0", "Outer domain", 300, 145, 225, 345, PALE_GREEN, "#A9D18E", NAVY, 13, True, False, False, True),
        Box("cpu0", "CPU / private caches", 82, 205, 170, 56, PALE_BLUE, BLUE, NAVY, 13, True, False, False),
        Box("hnf0", "HN-F / shared cache", 82, 330, 170, 56, "#EAF2F8", BLUE, NAVY, 13, True, False, False),
        Box("ep0", "EP-RNF / EP-SNF", 325, 205, 175, 56, PALE_AMBER, AMBER, "#7F6000", 13, True, False, False),
        Box("backend0", "EPBackend", 325, 305, 82, 50, PALE_AMBER, AMBER, "#7F6000", 12, True, False, False),
        Box("adapter0", "UBAdapter", 418, 305, 82, 50, PALE_AMBER, AMBER, "#7F6000", 12, True, False, False),
        Box("ubcc0", "UBIO / UBCC", 325, 405, 82, 52, PALE_GREEN, GREEN, "#375623", 12, True, False, False),
        Box("store0", "ResidentDir\nBackstore", 418, 400, 82, 62, PALE_ORANGE, ORANGE, "#843C0C", 11, True, False, False),
        Box("transport", "Generic packet transport", 615, 285, 250, 72, PALE_GRAY, GRAY, "#404040", 14, True, True, False),
        Box("node1", "Node 1 … Node N", 925, 95, 520, 445, "#F8FBFE", BLUE, NAVY, 16, True, False, False, True),
        Box("inner1", "Inner domain", 950, 145, 215, 345, PALE_BLUE, "#B4C7E7", NAVY, 13, True, False, False, True),
        Box("outer1", "Outer domain", 1190, 145, 225, 345, PALE_GREEN, "#A9D18E", NAVY, 13, True, False, False, True),
        Box("cpu1", "CPU / private caches", 972, 205, 170, 56, PALE_BLUE, BLUE, NAVY, 13, True, False, False),
        Box("hnf1", "HN-F / shared cache", 972, 330, 170, 56, "#EAF2F8", BLUE, NAVY, 13, True, False, False),
        Box("ep1", "EP-RNF / EP-SNF", 1215, 205, 175, 56, PALE_AMBER, AMBER, "#7F6000", 13, True, False, False),
        Box("backend1", "EPBackend", 1215, 305, 82, 50, PALE_AMBER, AMBER, "#7F6000", 12, True, False, False),
        Box("adapter1", "UBAdapter", 1308, 305, 82, 50, PALE_AMBER, AMBER, "#7F6000", 12, True, False, False),
        Box("ubcc1", "UBIO / UBCC", 1215, 405, 82, 52, PALE_GREEN, GREEN, "#375623", 12, True, False, False),
        Box("store1", "ResidentDir\nBackstore", 1308, 400, 82, 62, PALE_ORANGE, ORANGE, "#843C0C", 11, True, False, False),
        Box("simnote", "Temporary simulation binding only: NetworkSim (not a project component)", 540, 585, 400, 38, "#FFFFFF", GRAY, "#606060", 11, False, True, False),
    ]
    e = [
        Edge("cpu0", "hnf0", source_x=.5, source_y=1, target_x=.5, target_y=0),
        Edge("hnf0", "ep0", source_x=1, source_y=.5, target_x=0, target_y=.5,
             waypoints=((285, 358), (285, 233))),
        Edge("ep0", "backend0", source_x=.47, source_y=1, target_x=.5, target_y=0),
        Edge("backend0", "adapter0", source_x=1, source_y=.5, target_x=0, target_y=.5),
        Edge("backend0", "ubcc0", source_x=.5, source_y=1, target_x=.5, target_y=0),
        Edge("ubcc0", "store0", bidirectional=True, source_x=1, source_y=.5, target_x=0, target_y=.5),
        Edge("adapter0", "transport", color=TEAL, source_x=1, source_y=.5, target_x=0, target_y=.35,
             waypoints=((550, 330), (550, 310))),
        Edge("transport", "adapter1", color=TEAL, source_x=1, source_y=.35, target_x=0, target_y=.5,
             waypoints=((900, 310), (1180, 310), (1180, 330))),
        Edge("cpu1", "hnf1", source_x=.5, source_y=1, target_x=.5, target_y=0),
        Edge("hnf1", "ep1", source_x=1, source_y=.5, target_x=0, target_y=.5,
             waypoints=((1175, 358), (1175, 233))),
        Edge("ep1", "backend1", source_x=.47, source_y=1, target_x=.5, target_y=0),
        Edge("backend1", "adapter1", source_x=1, source_y=.5, target_x=0, target_y=.5),
        Edge("backend1", "ubcc1", source_x=.5, source_y=1, target_x=.5, target_y=0),
        Edge("ubcc1", "store1", bidirectional=True, source_x=1, source_y=.5, target_x=0, target_y=.5),
        Edge("simnote", "transport", color=GRAY, dashed=True, width=1.3,
             source_x=.5, source_y=0, target_x=.5, target_y=1),
    ]
    return Diagram("ubcc-system-architecture", "UBCC 跨节点缓存一致性总体架构", 1480, 660, tuple(b), tuple(e))


def gem5_diagram():
    b = [
        Box("gem5", "Single gem5 process boundary", 35, 75, 1530, 505, "#FFFFFF", NAVY, NAVY, 15, True, False, True, True),
        Box("ruby", "Ruby controllers", 65, 120, 930, 405, "#F7FBF7", GREEN, "#375623", 15, True, False, True, True),
        Box("nonruby", "Non-Ruby components", 1025, 120, 510, 405, "#FFF9F1", AMBER, "#7F6000", 15, True, False, True, True),
        Box("cpu", "CPU + RubySequencers", 90, 185, 180, 60, PALE_BLUE, BLUE, NAVY, 14, True),
        Box("l1", "L1I / L1D\ncontrollers", 315, 185, 175, 70, PALE_GREEN, GREEN, "#375623", 14, True),
        Box("l2", "Private L2\ncontroller", 540, 185, 165, 70, "#DDEBF7", TEAL, NAVY, 14, True),
        Box("hnf", "HN-F + L3\nhome controller", 755, 185, 190, 70, PALE_BLUE, BLUE, NAVY, 14, True),
        Box("lsnf", "L-SNF\nlocal memory controller", 235, 365, 185, 60, PALE_GREEN, GREEN, "#375623", 14, True),
        Box("mem", "Local MemCtrl", 500, 365, 150, 60, PALE_GREEN, GREEN, "#375623", 14, True),
        Box("eprnf", "EP-RNF", 745, 335, 145, 58, PALE_AMBER, AMBER, "#7F6000", 14, True),
        Box("metarnf", "Meta-RNF", 745, 430, 145, 58, PALE_AMBER, AMBER, "#7F6000", 14, True),
        Box("epsnf", "EP-SNF", 900, 380, 110, 58, PALE_AMBER, AMBER, "#7F6000", 14, True),
        Box("backend", "EPBackend", 1060, 210, 180, 58, PALE_AMBER, AMBER, "#7F6000", 14, True),
        Box("adapter", "UBAdapter", 1300, 210, 180, 58, PALE_AMBER, AMBER, "#7F6000", 14, True),
        Box("ubio", "External UBIO / UBCC boundary", 1160, 390, 260, 70, PALE_GRAY, GRAY, "#404040", 14, True, True),
        Box("legend", "Line legend:  solid = CHI / Ruby path   ·   dashed = endpoint / external message path   ·   ↔ = bidirectional", 250, 610, 1100, 50, "#FFFFFF", GRAY, "#404040", 13),
    ]
    e = [
        Edge("cpu", "l1", "sequencer", label_y=-18), Edge("l1", "l2", "CHI", label_y=-18), Edge("l2", "hnf", "CHI", label_y=-18),
        Edge("hnf", "lsnf"), Edge("lsnf", "mem", "memory", label_y=-18),
        Edge("hnf", "eprnf", "remote request", label_x=0.35), Edge("hnf", "metarnf", "metadata", label_x=-0.35),
        Edge("epsnf", "hnf", "remote snoop / data", label_x=0.35),
        Edge("eprnf", "backend", "events", AMBER, True, label_y=-18), Edge("metarnf", "backend", "metadata", AMBER, True, label_y=18),
        Edge("backend", "epsnf", "snoop events", AMBER, True, label_y=-18), Edge("backend", "adapter", "packets", AMBER, True, True, label_y=-18),
        Edge("adapter", "ubio", "external UBIO", GRAY, True, True, label_y=18),
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
        boxes.append(Box(f"lane{i}", row[0], 35, y, 1450, 108, "#FFFFFF", "#B4C7E7", NAVY, 14, True, False, False, True))
        labels = (f"Requester\n{row[1]}", f"Home UBCC\n{row[2]}", "Owner / Sharer\nresponse", f"Requester\n{row[3]}")
        for j, label in enumerate(labels):
            boxes.append(Box(f"p{i}{j}", label, 175 + j * 330, y + 30, 220, 56, fills[j], (BLUE, GREEN, AMBER, BLUE)[j], NAVY, 13, j in (0, 3), False, False))
        # Four independent lanes: request, recall/invalidate, data/ack, grant.
        edges.extend((
            Edge(f"p{i}0", f"p{i}1", color=BLUE, source_x=1, source_y=.35, target_x=0, target_y=.35),
            Edge(f"p{i}1", f"p{i}2", color=AMBER, source_x=1, source_y=.25, target_x=0, target_y=.25),
            Edge(f"p{i}2", f"p{i}1", color=TEAL, source_x=0, source_y=.75, target_x=1, target_y=.75),
            Edge(f"p{i}1", f"p{i}3", color=GREEN, source_x=1, source_y=.52, target_x=0, target_y=.52,
                 waypoints=((805, y + 93), (1135, y + 93))),
        ))
    return Diagram("ubcc-protocol-paths", "UBCC 三类核心协议路径", 1540, 620, tuple(boxes), tuple(edges))


def verification_diagram():
    labels = ("协议设计\n不变量", "TLA+ 形式化\nSafety · Liveness", "定向机制\n仲裁 · waiter · retry",
              "端到端正确性\n数据 · 权限 · 完成", "Q1-Q5 故障资格\n52 个用例", "多拓扑 / HA\n规模与恢复闭环")
    boxes = [Box(f"v{i}", label, 55 + i * 245, 150, 205, 90,
                 (PALE_BLUE, PALE_GREEN, PALE_AMBER, "#EAF2F8", PALE_ORANGE, PALE_GRAY)[i],
                 (BLUE, GREEN, AMBER, BLUE, ORANGE, GRAY)[i], NAVY, 14, True) for i, label in enumerate(labels)]
    edges = tuple(Edge(f"v{i}", f"v{i+1}", ("抽象", "机制映射", "实现", "故障扩展", "规模扩展")[i], label_y=-18 if i % 2 == 0 else 18) for i in range(5))
    note = "从协议不变量到形式化、定向机制、端到端、故障与多拓扑验证的单向证据链"
    return Diagram("ubcc-verification-stack", "UBCC 分层验证体系", 1540, 350, tuple(boxes), edges, note)


def two_phase_diagram():
    labels = ("请求到达", "阶段 1：Reserve\nepoch + intended state", "Grant 在途\n已提交状态保持安全", "阶段 2：Clear\n校验 tuple 并提交", "事务完成")
    boxes = [Box(f"t{i}", label, 55 + i * 285, 130, 235, 85,
                 (PALE_BLUE, PALE_AMBER, "#EAF2F8", PALE_GREEN, "#D9EAD3")[i],
                 (BLUE, AMBER, BLUE, GREEN, GREEN)[i], NAVY, 14, True) for i, label in enumerate(labels)]
    boxes.append(Box("retry", "丢失 / 延迟：相同 tuple 重试，幂等恢复原 Grant", 555, 260, 520, 55,
                     PALE_ORANGE, ORANGE, "#843C0C", 13, False, True))
    edges = [Edge(f"t{i}", f"t{i+1}", ("创建", "授权", "Clear", "commit")[i], label_y=-18 if i % 2 == 0 else 18) for i in range(4)]
    edges += [Edge("t2", "retry", "timeout", ORANGE, True, label_y=-18), Edge("retry", "t2", "idempotent replay", ORANGE, True, label_y=18)]
    return Diagram("ubcc-two-phase-commit", "UBCC 两阶段目录提交", 1500, 390, tuple(boxes), tuple(edges))


def authority_comparison_diagram():
    boxes = (
        Box("states", "稳定状态族\nVI / MSI / MESI / MOESI / MESIF", 45, 105, 310, 92, PALE_BLUE, BLUE, NAVY, 14, True, False, False),
        Box("statejob", "副本可读/可写吗？\n是否脏？是否有 owner/forwarder？", 45, 255, 310, 100, "#FFFFFF", BLUE, NAVY, 13, False, False, False),
        Box("chi", "CHI 端到端事务/承载协议", 455, 105, 310, 92, PALE_AMBER, AMBER, "#7F6000", 14, True, False, False),
        Box("chijob", "谁发 Req/Snp/Rsp/Dat？\n如何路由、排序、重试与流控？", 455, 255, 310, 100, "#FFFFFF", AMBER, NAVY, 13, False, False, False),
        Box("ubcc", "UBCC：本地 CHI + Outer 目录", 865, 105, 330, 92, PALE_GREEN, GREEN, "#375623", 14, True, False, False),
        Box("ubccjob", "authority：Home UBCC\ndata：memory / owner / requester\ncommit：Home outstanding + Clear", 865, 255, 330, 118, "#FFFFFF", GREEN, NAVY, 13, False, False, False),
        Box("rule", "状态族定义稳定权限；CHI 定义事务语言和承载；UBCC 定义跨节点权威、目录和提交边界。", 190, 445, 860, 68, PALE_GRAY, GRAY, "#404040", 13, True, False, False),
    )
    edges = (
        Edge("states", "statejob", source_x=.5, source_y=1, target_x=.5, target_y=0),
        Edge("chi", "chijob", source_x=.5, source_y=1, target_x=.5, target_y=0),
        Edge("ubcc", "ubccjob", source_x=.5, source_y=1, target_x=.5, target_y=0),
        Edge("statejob", "rule", color=BLUE, source_x=.5, source_y=1, target_x=.18, target_y=0, waypoints=((200, 405), (345, 405))),
        Edge("chijob", "rule", color=AMBER, source_x=.5, source_y=1, target_x=.5, target_y=0),
        Edge("ubccjob", "rule", color=GREEN, source_x=.5, source_y=1, target_x=.82, target_y=0, waypoints=((1030, 405), (895, 405))),
    )
    return Diagram("ubcc-protocol-authority-comparison", "状态、事务承载与全局权威的职责分离", 1240, 570, boxes, edges)


def central_direct_diagram():
    boxes = (
        Box("central", "A  当前 UBCC：Home 中心转送", 35, 80, 720, 245, "#F8FBFE", BLUE, NAVY, 14, True, False, False, True),
        Box("cr", "Requester", 75, 175, 145, 58, PALE_BLUE, BLUE, NAVY, 13, True, False, False),
        Box("ch", "Home UBCC\nauthority + commit", 315, 155, 180, 98, PALE_GREEN, GREEN, "#375623", 13, True, False, False),
        Box("co", "Old owner", 595, 175, 125, 58, PALE_AMBER, AMBER, "#7F6000", 13, True, False, False),
        Box("direct", "B  候选优化：Home 授权，owner 直接送数", 785, 80, 720, 245, "#F8FBFE", TEAL, NAVY, 14, True, False, False, True),
        Box("dr", "Requester", 825, 175, 145, 58, PALE_BLUE, BLUE, NAVY, 13, True, False, False),
        Box("dh", "Home UBCC\nauthority + commit", 1065, 155, 180, 98, PALE_GREEN, GREEN, "#375623", 13, True, False, False),
        Box("do", "Old owner", 1345, 175, 125, 58, PALE_AMBER, AMBER, "#7F6000", 13, True, False, False),
        Box("cost1", "K_crossnode ≈ 4; D = 2\nReq → Home → Owner → Home → Requester", 95, 385, 570, 72, PALE_ORANGE, ORANGE, "#843C0C", 13, True, False, False, False, MATH_FONT),
        Box("cost2", "candidate data path ≈ 3; D = 1\nT_visible = max(T_data, T_authority)", 845, 385, 570, 72, PALE_GREEN, GREEN, "#375623", 13, True, False, False, False, MATH_FONT),
        Box("boundary", "直接送数只改变 data location/path，不转移 authority；B 为可演进候选，不是当前能力声明。", 300, 510, 940, 62, PALE_GRAY, GRAY, "#404040", 13, True, False, False),
    )
    edges = (
        Edge("cr", "ch", "1 Req", BLUE, source_x=1, source_y=.35, target_x=0, target_y=.35), Edge("ch", "co", "2 Recall", AMBER, source_x=1, source_y=.28, target_x=0, target_y=.28),
        Edge("co", "ch", "3 Data", TEAL, source_x=0, source_y=.75, target_x=1, target_y=.75), Edge("ch", "cr", "4 Data + Grant", GREEN, source_x=0, source_y=.82, target_x=1, target_y=.82),
        Edge("dr", "dh", "1 Req", BLUE, source_x=1, source_y=.35, target_x=0, target_y=.35), Edge("dh", "do", "2 Recall + route", AMBER, source_x=1, source_y=.28, target_x=0, target_y=.28),
        Edge("do", "dr", "3 Data", TEAL, source_x=.5, source_y=1, target_x=.5, target_y=1, waypoints=((1408, 285), (898, 285))),
        Edge("dh", "dr", "Grant", GREEN, source_x=0, source_y=.78, target_x=1, target_y=.78),
        Edge("ch", "cost1", source_x=.5, source_y=1, target_x=.5, target_y=0), Edge("dh", "cost2", source_x=.5, source_y=1, target_x=.5, target_y=0),
    )
    return Diagram("ubcc-path-central-vs-direct", "跨节点数据路径：中心转送与直接转发", 1540, 620, boxes, edges)


def metadata_scaling_diagram():
    boxes = (
        Box("formula", "B_dir = N + b_owner + b_state\n+ b_epoch + b_ctrl + b_tag\nb_owner/tag may be zero by organization", 35, 90, 450, 105, PALE_BLUE, BLUE, NAVY, 13, True, False, False, False, MATH_FONT),
        Box("fanout", "M ≈ 2S + 2; K_crossnode ≈ 4\nfanout width = S", 545, 90, 450, 95, PALE_AMBER, AMBER, "#7F6000", 14, True, False, False, False, MATH_FONT),
        Box("h64", "当前 H64 codec：12 B / 96 bit\n44 PA + 2 MESI + 2 slot + 16 sharers\n+24 epoch + 8 integrity", 1055, 90, 450, 95, PALE_GREEN, GREEN, "#375623", 14, True, False, False),
        Box("scale", "同一概念模型按 N 扩展", 570, 215, 400, 48, PALE_GRAY, GRAY, "#404040", 12, True, False, False),
        Box("n2", "N=2\nbitmap: 2 bit/line\n2^20 entries: 0.25 MiB\nworst M≈4", 90, 300, 330, 112, "#FFFFFF", BLUE, NAVY, 13, True, False, False),
        Box("n8", "N=8\nbitmap: 8 bit/line\n2^20 entries: 1 MiB\nworst M≈16", 605, 300, 330, 112, "#FFFFFF", AMBER, NAVY, 13, True, False, False),
        Box("n16", "N=16\nbitmap: 16 bit/line\n2^20 entries: 2 MiB\nworst M≈32", 1120, 300, 330, 112, "#FFFFFF", GREEN, NAVY, 13, True, False, False),
        Box("tbe", "瞬态/TBE：target mask N + ack mask N，至少 2N bit/事务；64 B 数据缓冲为 512 bit。", 250, 465, 1040, 70, PALE_GRAY, GRAY, "#404040", 13, True, False, False),
    )
    edges = (
        Edge("formula", "scale", color=BLUE, source_x=.5, source_y=1, target_x=.15, target_y=0),
        Edge("fanout", "scale", color=AMBER, source_x=.5, source_y=1, target_x=.5, target_y=0),
        Edge("h64", "scale", color=GREEN, source_x=.5, source_y=1, target_x=.85, target_y=0),
        Edge("scale", "n2", color=BLUE, source_x=.2, source_y=1, target_x=.5, target_y=0, waypoints=((650, 280), (255, 280))),
        Edge("scale", "n8", color=AMBER, source_x=.5, source_y=1, target_x=.5, target_y=0),
        Edge("scale", "n16", color=GREEN, source_x=.8, source_y=1, target_x=.5, target_y=0, waypoints=((890, 280), (1285, 280))),
        Edge("n2", "tbe", color=BLUE, source_x=.5, source_y=1, target_x=.2, target_y=0, waypoints=((255, 425), (458, 425))), Edge("n8", "tbe", color=AMBER, source_x=.5, source_y=1, target_x=.5, target_y=0), Edge("n16", "tbe", color=GREEN, source_x=.5, source_y=1, target_x=.8, target_y=0, waypoints=((1285, 425), (1082, 425))),
    )
    return Diagram("ubcc-metadata-fanout-scaling", "目录元数据、失效扇出与瞬态成本随 N 扩展", 1540, 590, boxes, edges)


def inner_outer_boundary_diagram():
    boxes = (
        Box("current", "当前选择", 30, 75, 720, 420, "#F8FBFE", BLUE, NAVY, 15, True, False, False, True),
        Box("inner", "每节点本地 CHI 域\nRN-F / HN-F / SN-F", 75, 150, 250, 85, PALE_BLUE, BLUE, NAVY, 14, True, False, False),
        Box("ep", "EP / UBAdapter\n语义与身份边界\n双向 Outer request / recall", 405, 150, 250, 95, PALE_AMBER, AMBER, "#7F6000", 13, True, False, False),
        Box("outer", "Outer UBCC\nowner/sharer directory\nepoch + reqId + Clear commit", 240, 320, 310, 105, PALE_GREEN, GREEN, "#375623", 14, True, False, False),
        Box("future", "假设的 Outer CHI（候选）", 790, 75, 720, 420, "#FFF9F1", AMBER, NAVY, 15, True, True, False, True),
        Box("agents", "全局 CHI agents\nRN / HN / SN / bridges", 835, 150, 250, 85, PALE_BLUE, BLUE, NAVY, 14, True, False, False),
        Box("fabric", "CHI fabric\nReq/Rsp/Snp/Dat + credits", 1165, 150, 250, 85, PALE_AMBER, AMBER, "#7F6000", 14, True, False, False),
        Box("cost", "新增全局 ID、通道排序、credit、\n重试代理、bridge 与验证组合", 1000, 320, 310, 105, PALE_ORANGE, ORANGE, "#843C0C", 13, True, False, False),
        Box("decision", "Outer CHI 适合已有全局 CHI fabric/IP 和标准 agent 互操作；当前 UBCC 优先选择更窄的目录/消息边界。", 235, 540, 1070, 66, PALE_GRAY, GRAY, "#404040", 13, True, False, False),
    )
    edges = (
        Edge("inner", "ep", "local CHI", BLUE, source_x=1, source_y=.5, target_x=0, target_y=.5),
        Edge("ep", "outer", color=GREEN, source_x=.68, source_y=1, target_x=.72, target_y=0),
        Edge("outer", "ep", color=AMBER, source_x=.28, source_y=0, target_x=.32, target_y=1),
        Edge("agents", "fabric", "CHI channels", BLUE, bidirectional=True, source_x=1, source_y=.5, target_x=0, target_y=.5), Edge("fabric", "cost", source_x=.5, source_y=1, target_x=.75, target_y=0), Edge("agents", "cost", source_x=.5, source_y=1, target_x=.25, target_y=0),
    )
    return Diagram("ubcc-inner-chi-outer-boundary", "本地 CHI 与假设 Outer CHI 的边界及成本", 1540, 650, boxes, edges)


def style_string(box):
    return (f"rounded={1 if box.rounded else 0};whiteSpace=wrap;html=1;fillColor={box.fill};"
            f"strokeColor={box.stroke};fontColor={box.font};fontSize={box.size};"
            f"fontStyle={1 if box.bold else 0};fontFamily={box.font_family};spacing=6;"
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
    title = Box("title", diagram.title, 30, 15, diagram.width - 60, 45, "#FFFFFF", "none", NAVY, 24, True, False, False)
    for box in (title,) + diagram.boxes:
        cell = ET.SubElement(root, "mxCell", {"id": box.id, "value": box.label, "style": style_string(box), "vertex": "1", "parent": "1"})
        ET.SubElement(cell, "mxGeometry", {"x": str(box.x), "y": str(box.y), "width": str(box.w), "height": str(box.h), "as": "geometry"})
    if diagram.note:
        note = Box("note", diagram.note, 80, diagram.height - 70, diagram.width - 160, 35, "#FFFFFF", "none", "#606060", 12)
        cell = ET.SubElement(root, "mxCell", {"id": note.id, "value": note.label, "style": style_string(note), "vertex": "1", "parent": "1"})
        ET.SubElement(cell, "mxGeometry", {"x": str(note.x), "y": str(note.y), "width": str(note.w), "height": str(note.h), "as": "geometry"})
    for i, edge in enumerate(diagram.edges):
        style = (f"edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=16;html=1;"
                  f"strokeWidth={edge.width};strokeColor={edge.color};dashed={1 if edge.dashed else 0};dashPattern=6 4;"
                  f"endArrow=block;endFill=1;startArrow={'block' if edge.bidirectional else 'none'};"
                  f"startFill={1 if edge.bidirectional else 0};fontSize=14;fontColor=#404040;fontFamily={FONT};labelBackgroundColor=#FFFFFF;"
                  + (f"exitX={edge.source_x};exitY={edge.source_y};exitDx=0;exitDy=0;" if edge.source_x is not None else "")
                  + (f"entryX={edge.target_x};entryY={edge.target_y};entryDx=0;entryDy=0;" if edge.target_x is not None else ""))
        cell = ET.SubElement(root, "mxCell", {"id": f"e{i}", "value": edge.label, "style": style, "edge": "1", "parent": "1", "source": edge.source, "target": edge.target})
        geometry = ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
        if edge.waypoints:
            points = ET.SubElement(geometry, "Array", {"as": "points"})
            for x, y in edge.waypoints:
                ET.SubElement(points, "mxPoint", {"x": str(x), "y": str(y)})
        if edge.label_x or edge.label_y:
            ET.SubElement(geometry, "mxPoint", {"x": str(edge.label_x), "y": str(edge.label_y), "as": "offset"})
    ET.ElementTree(mxfile).write(OUT / f"{diagram.stem}.drawio", encoding="utf-8", xml_declaration=True)


def configure_plot():
    if plt is None:
        raise RuntimeError("matplotlib is required to render delivery figures")
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
    if MATH_FONT_PATH.is_file():
        candidates.append(MATH_FONT_PATH)
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
                fontweight="bold" if box.bold else "normal", color=box.font,
                fontfamily=box.font_family, linespacing=1.25)
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
            ax.text((sx + tx) / 2 + edge.label_x * 80, (sy + ty) / 2 - 6 + edge.label_y,
                    edge.label, ha="center", va="bottom", fontsize=10,
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
    if FONTCONFIG_FILE.is_file():
        env["FONTCONFIG_FILE"] = str(FONTCONFIG_FILE)
    try:
        for fmt in ("svg", "png"):
            target = OUT / f"{diagram.stem}.{fmt}"
            args = ["xvfb-run", "-a", command, "--no-sandbox", "--export", "--format", fmt, "--crop", "--border", "20"]
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


def metric_charts(publication_path=None):
    configure_plot()
    if publication_path is None:
        publication_path = ROOT / PUBLICATION_DATA
    report, outer, preview = publication_sources(publication_path)

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
    axes[1].set_title("Outer spill vs IdealDir")
    save_chart(fig, "ubcc-metric1-capacity-latency")

    # Metric 2, including the negative TC138 and explicitly excluded TC140.
    cases = required(report.get("metric2", {}).get("cases"), "metric2.cases")
    names = [required(row.get("case"), "metric2 case name") for row in cases]
    values = [float(required(row.get("optimized_reduction_pct"), f"{names[i]} reduction")) for i, row in enumerate(cases)]
    applicable = [bool(required(row.get("applicable"), f"{names[i]} applicable")) for i, row in enumerate(cases)]
    colors = [GRAY if not ok else ORANGE if value < 0 else BLUE for value, ok in zip(values, applicable)]
    fig, ax = plt.subplots(figsize=(9.4, 3.8))
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
    levels = [level for level in required(report.get("metric3", {}).get("levels"), "metric3.levels")
              if int(level.get("pressure_level", -1)) == 100]
    if len(levels) != 1:
        raise ValueError("Metric3 formal chart requires exactly the 100% L3 pressure result")
    labels, ubcc, havi = [], [], []
    for level in levels:
        pressure = required(level.get("pressure_level"), "Metric3 pressure_level")
        for key, scope in (("core_equal_weight", "core"), ("representative_equal_weight", "representative")):
            row = required(level.get(key), f"Metric3 {key}")
            labels.append(scope)
            ubcc.append(float(required(row.get("ourcc_ticks_per_operation"), f"{key}.ourcc")))
            havi.append(float(required(row.get("ha_vi_ticks_per_operation"), f"{key}.ha_vi")))
    fig, ax = plt.subplots(figsize=(9.4, 3.8)); x = list(range(len(labels))); w = .34
    ax.bar([v-w/2 for v in x], ubcc, w, label="UBCC", color=BLUE)
    ax.bar([v+w/2 for v in x], havi, w, label="HA-VI", color=AMBER)
    ax.set_xticks(x, labels); ax.set_ylabel("ticks / operation"); ax.grid(axis="y", alpha=.22); ax.legend(frameon=False, ncol=2)
    ax.set_title("Metric 3: grouped UBCC vs HA-VI latency", color=NAVY, fontweight="bold")
    save_chart(fig, "ubcc-ha-vi-comparison")

    def compact_bar(stem, title, rows, ylabel, width=8.8):
        fig, ax = plt.subplots(figsize=(width, 3.7))
        labels = [row[0][2:] if row[0].startswith("TC") else row[0] for row in rows]
        values = [row[1] for row in rows]
        bars = ax.bar(labels, values, color=[BLUE if value >= 0 else ORANGE for value in values], width=.62)
        for bar, row in zip(bars, rows):
            if len(row) > 2 and row[2]:
                bar.set_hatch("////")
                bar.set_edgecolor("#404040")
        ax.axhline(0, color="#404040", linewidth=.8); ax.grid(axis="y", alpha=.2)
        ax.set_title(title, color=NAVY, fontweight="bold"); ax.set_ylabel(ylabel)
        span = max(max(values) - min(values), 1.0)
        lower = min(0, min(values) - span * .14)
        upper = max(0, max(values) + span * .18)
        ax.set_ylim(lower, upper)
        for bar, value in zip(bars, values):
            near_zero = abs(value) < span * .055
            if near_zero:
                y, va, color = span * .045, "bottom", "#404040"
            elif value < 0:
                y, va, color = value + span * .045, "bottom", "white"
            else:
                y, va, color = value + span * .025, "bottom", "#404040"
            ax.text(bar.get_x() + bar.get_width() / 2, y, f"{value:.2f}%",
                    ha="center", va=va, fontsize=10, color=color, fontweight="bold")
        save_chart(fig, stem)

    compact_bar("ubcc-tc120-124-scenarios", "TC120–TC124 scenario changes",
                [(row["case"], row["reduction_pct"], row.get("estimated", False)) for row in preview["tc120_124"]], "optimized reduction (%)", 8.4)
    compact_bar("ubcc-tc130-134-pressure", "TC130–TC134 pressure-path comparison",
                [(row["case"], row["reduction_pct"], row.get("estimated", False)) for row in preview["tc130_134"]], "optimized reduction (%)", 8.4)
    compact_bar("ubcc-tc142-147-applications", "TC142–TC147 application reductions",
                [(row["case"], row["reduction_pct"], row.get("estimated", False)) for row in preview["tc142_147"]], "optimized reduction (%)", 9.2)

    if preview.get("metric1_matrix"):
        rows = preview["metric1_matrix"]
        labels = [f"P{row['pressure_pct']}\n{row['topology'].upper()}" for row in rows]
        fig, axes = plt.subplots(2, 1, figsize=(12.0, 6.1), sharex=True,
                                gridspec_kw={"hspace": .16})
        for ax, field, estimated_field, ylabel, title in (
                (axes[0], "capacity_gain_pct", "capacity_estimated", "Capacity gain (%)", "Metric 1 extension: effective capacity gain"),
                (axes[1], "outer_delta_cycles_2ghz", "latency_estimated", "Outer delta (cycles @ 2 GHz)", "Metric 1 extension: spill minus oversized-resident reference")):
            values = [row[field] for row in rows]
            bars = ax.bar(range(len(rows)), values, color=[GREEN if value >= 0 else TEAL for value in values], width=.68)
            ax.axhline(0, color="#404040", linewidth=.8)
            ax.grid(axis="y", alpha=.2)
            ax.set_ylabel(ylabel)
            ax.set_title(title, color=NAVY, fontweight="bold")
            for bar, row, value in zip(bars, rows, values):
                if row.get(estimated_field, False):
                    bar.set_hatch("////")
                    bar.set_edgecolor("#404040")
                ax.text(bar.get_x() + bar.get_width()/2, value,
                        f"{value:.1f}", ha="center",
                        va="bottom" if value >= 0 else "top", fontsize=10)
        axes[1].set_xticks(range(len(rows)), labels, rotation=0)
        save_chart(fig, "ubcc-metric1-extension-matrix")
    metric3_rows = preview["metric3_per_tc"]
    labels = [str(tc) for tc in range(228, 236)]
    values = [next(row["reduction_pct"] for row in metric3_rows if row["case"] == str(tc)) for tc in range(228, 236)]
    fig, ax = plt.subplots(figsize=(8.4, 4.5)); y = list(range(len(labels)))
    bars = ax.barh(y, values, .56, color=BLUE)
    ax.set_yticks(y, labels); ax.set_xlabel("UBCC reduction (%)"); ax.grid(axis="x", alpha=.2)
    ax.set_title("Metric3 per-testcase reductions", color=NAVY, fontweight="bold")
    ax.set_xlim(0, max(values) * 1.23); ax.invert_yaxis()
    for bar, value in zip(bars, values):
        ax.text(value + max(values) * .012, bar.get_y() + bar.get_height() / 2,
                f"{value:.2f}%", va="center", ha="left", fontsize=10, color="#404040")
    save_chart(fig, "ubcc-metric3-per-tc-reductions")
    return chart_lineage(report, outer, preview,
                         PUBLICATION_DATA if publication_path == ROOT / PUBLICATION_DATA
                         else str(publication_path))


def chart_lineage(report, outer, preview=None, publication_source=PUBLICATION_DATA):
    first = outer["repeats"]["1"]
    charts = [
        {"name": "ubcc-metric1-capacity-latency", "source_artifacts": [publication_source],
         "generator": GENERATOR, "metric_definition": "Capacity ratio and increase use the final Metric1 capacity contract; latency is independently defined as mean(all completed spill Outer) - mean(all completed ideal Outer).",
         "evidence_sets": [
             {"name": "capacity", "physical_runs": 6, "roles": ["naive", "spill-noopt"],
              "repetitions": 3, "aggregation": "per-role capacity, then spill / naive; optimized support runs are excluded"},
             {"name": "outer_latency", "physical_arms": 6, "roles": ["spill-512K", "spill-IdealDir"],
              "repetitions": 3, "aggregation": "per-repeat completed-Outer mean difference, then equal-weight mean"}],
         "cross_set_weighting": "none; the two evidence sets serve independent Metric1 subcontracts",
         "expected_values": {"capacity_ratio": float(report["metric1"]["capacity_ratio"]), "capacity_increase_pct": float(report["metric1"]["capacity_increase_pct"]), "ideal_outer_mean_ns": float(first["ideal"]["outer_mean_ns"]), "spill_outer_mean_ns": float(first["spill"]["outer_mean_ns"]), "outer_delta_mean_ns": float(outer["delta_mean_ns"]), "ideal_resident_capacity": int(first["ideal"]["resident_capacity"]), "spill_resident_capacity": int(first["spill"]["resident_capacity"])},
         "document_references": CHART_DOCUMENT_REFERENCES["ubcc-metric1-capacity-latency"]},
        {"name": "ubcc-metric2-reductions", "source_artifacts": [publication_source], "generator": GENERATOR,
         "metric_definition": "Per-case optimized reduction versus naive; non-applicable cases remain visible as excluded.",
         "expected_values": {"cases": [{"case": row["case"], "optimized_reduction_pct": float(row["optimized_reduction_pct"]), "applicable": bool(row["applicable"])} for row in report["metric2"]["cases"]], "applicable_equal_weight_mean_reduction_pct": float(report["metric2"]["equal_weight_mean_reduction_pct"])},
         "document_references": CHART_DOCUMENT_REFERENCES["ubcc-metric2-reductions"]},
        {"name": "ubcc-ha-vi-comparison", "source_artifacts": [publication_source], "generator": GENERATOR,
         "metric_definition": "Grouped UBCC and HA-VI ticks per operation at the fixed 256 KiB L3, 100% pressure configuration.",
         "expected_values": {"groups": [{"pressure_level": level["pressure_level"], "scope": scope, "ubcc_ticks_per_operation": float(level[key]["ourcc_ticks_per_operation"]), "ha_vi_ticks_per_operation": float(level[key]["ha_vi_ticks_per_operation"])} for level in report["metric3"]["levels"] if int(level["pressure_level"]) == 100 for key, scope in (("core_equal_weight", "core"), ("representative_equal_weight", "representative"))]},
         "document_references": CHART_DOCUMENT_REFERENCES["ubcc-ha-vi-comparison"]},
    ]
    if preview is not None:
        refs = {
            "ubcc-tc120-124-scenarios": [{"document": "docs/design/cc_ep_deliverable3_performance_api.md", "figure": "图 3-2"}, {"document": "docs/design/cc_ep_deliverable3_performance_api.docx", "figure": "图 3-2"}],
            "ubcc-tc130-134-pressure": [{"document": "docs/design/cc_ep_deliverable3_performance_api.md", "figure": "图 4-2"}, {"document": "docs/design/cc_ep_deliverable3_performance_api.docx", "figure": "图 4-2"}],
            "ubcc-tc142-147-applications": [{"document": "docs/design/cc_ep_deliverable3_performance_api.md", "figure": "图 4-3"}, {"document": "docs/design/cc_ep_deliverable3_performance_api.docx", "figure": "图 4-3"}],
            "ubcc-metric3-per-tc-reductions": [{"document": "docs/design/cc_ep_deliverable3_performance_api.md", "figure": "图 5-2"}, {"document": "docs/design/cc_ep_deliverable3_performance_api.docx", "figure": "图 5-2"}],
        }
        for stem, title, key in (("ubcc-tc120-124-scenarios", "Scenario change reductions", "tc120_124"),
                                 ("ubcc-tc130-134-pressure", "Pressure path reductions", "tc130_134"),
                                 ("ubcc-tc142-147-applications", "Application reductions", "tc142_147"),
                                 ("ubcc-metric3-per-tc-reductions", "Metric3 per-TC reductions", "metric3_per_tc")):
            charts.append({"name": stem, "source_artifacts": [publication_source], "generator": GENERATOR,
                           "metric_definition": title + "; visual-only preview derived from the checked-in preview data.",
                           "derived_values": {"rows": preview[key]}, "document_references": refs[stem]})
        charts.append({
            "name": "ubcc-metric1-extension-matrix",
            "source_artifacts": [publication_source],
            "generator": GENERATOR,
            "metric_definition": "Mean TC142-TC147 capacity gain and completed-Outer delta for each pressure/topology coordinate.",
            "derived_values": {"rows": preview.get("metric1_matrix", [])},
            "document_references": CHART_DOCUMENT_REFERENCES["ubcc-metric1-extension-matrix"],
        })
    return charts


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


def main(argv=None):
    global OUT, PUBLICATION_DATA
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-only", action="store_true")
    parser.add_argument("--charts-only", action="store_true")
    parser.add_argument("--publication-json", type=Path)
    parser.add_argument("--out-dir", type=Path)
    args = parser.parse_args(argv)
    if args.out_dir is not None:
        OUT = args.out_dir.expanduser().resolve()
    if args.publication_json is not None:
        PUBLICATION_DATA = str(args.publication_json.expanduser().resolve())
    OUT.mkdir(parents=True, exist_ok=True)
    if args.metadata_only:
        publication_path = (Path(PUBLICATION_DATA) if Path(PUBLICATION_DATA).is_absolute()
                            else ROOT / PUBLICATION_DATA)
        try:
            publication_source = str(publication_path.relative_to(ROOT))
        except ValueError:
            publication_source = str(publication_path)
        report, outer, preview = publication_sources(publication_path)
        charts = chart_lineage(report, outer, preview, publication_source)
        diagrams = [{"name": stem, "document_references": DIAGRAM_DOCUMENT_REFERENCES[stem]}
                    for stem in DIAGRAM_STEMS]
        existing = require_json("docs/design/figures/figure_inventory.json") if (OUT / "figure_inventory.json").is_file() else {}
        manifest = {"schema_version": 2, "diagrams": diagrams, "charts": charts,
                    "obsolete": ["ubcc-metric-summary"],
                    "diagram_exports": existing.get("diagram_exports", [])}
        (OUT / "figure_inventory.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"updated metadata for {len(diagrams) + len(charts)} figures in {OUT}")
        return
    remove_obsolete()
    if args.charts_only:
        publication_path = (Path(PUBLICATION_DATA) if Path(PUBLICATION_DATA).is_absolute()
                            else ROOT / PUBLICATION_DATA)
        metric_charts(publication_path)
        print(f"generated charts in {OUT}")
        return
    diagrams = (architecture_diagram(), gem5_diagram(), protocol_diagram(), verification_diagram(), two_phase_diagram(),
                authority_comparison_diagram(), central_direct_diagram(), metadata_scaling_diagram(),
                inner_outer_boundary_diagram())
    export_rows = []
    for diagram in diagrams:
        write_drawio(diagram)
        ok, detail = export_drawio(diagram)
        if not ok:
            render_fallback(diagram)
            export_rows.append((diagram.stem, "matplotlib same-model fallback", detail))
        else:
            export_rows.append((diagram.stem, detail, ""))
    publication_path = (Path(PUBLICATION_DATA) if Path(PUBLICATION_DATA).is_absolute()
                        else ROOT / PUBLICATION_DATA)
    charts = metric_charts(publication_path)
    diagrams = [{"name": stem, "document_references": DIAGRAM_DOCUMENT_REFERENCES[stem]}
                for stem in DIAGRAM_STEMS]
    manifest = {"schema_version": 2, "diagrams": diagrams, "charts": charts,
                 "obsolete": ["ubcc-metric-summary"],
                "diagram_exports": [{"name": name, "renderer": renderer, "drawio_cli_blocker": blocker}
                                    for name, renderer, blocker in export_rows]}
    (OUT / "figure_inventory.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"generated {len(DIAGRAM_STEMS) + len(CHART_STEMS)} figures in {OUT}")
    for name, renderer, blocker in export_rows:
        print(f"{name}: {renderer}" + (f" ({blocker.splitlines()[-1]})" if blocker else ""))


if __name__ == "__main__":
    main()
