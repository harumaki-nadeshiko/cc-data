#!/usr/bin/env python3
"""Structural QA for the round-1 release figure inventory."""

import json
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
FIGURES = ROOT / "docs/design/figures"
REPORT = FIGURES / "visual_qa.json"
INVENTORY = FIGURES / "figure_inventory.json"
FONT = "Microsoft YaHei"
MAX_PAGE_HEIGHT = 750
MIN_FONT = 11
OBSOLETE = "ubcc-metric-summary"


def png_dimensions(path):
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"not a PNG: {path}")
    return struct.unpack(">II", data[16:24])


def style_value(style, key):
    match = re.search(rf"(?:^|;){re.escape(key)}=([^;]+)", style)
    return match.group(1) if match else None


def inspect_drawio(path, stem):
    root = ET.parse(path).getroot()
    graph = root.find(".//mxGraphModel")
    cells = root.findall(".//mxCell")
    labels = [cell.get("value", "") for cell in cells if cell.get("value")]
    fonts, sizes = [], []
    for cell in cells:
        style = cell.get("style", "")
        family = style_value(style, "fontFamily")
        size = style_value(style, "fontSize")
        if family: fonts.append(family)
        if size: sizes.append(float(size))
    page_w, page_h = int(graph.get("pageWidth")), int(graph.get("pageHeight"))
    vertex_networksim = any("NetworkSim" in cell.get("value", "") and "not a project component" not in cell.get("value", "")
                            for cell in cells if cell.get("vertex") == "1")
    checks = {
        "editable_content": len(labels) >= 5,
        "wide_aspect_ratio": page_w / page_h >= (2.0 if stem in {"ubcc-verification-stack", "ubcc-two-phase-commit"} else 1.9),
        "max_expected_page_height": page_h <= MAX_PAGE_HEIGHT,
        "approved_drawio_font": bool(fonts) and set(fonts) == {FONT},
        "minimum_font_size": bool(sizes) and min(sizes) >= MIN_FONT,
        "no_core_networksim_component": not vertex_networksim,
    }
    return {"page_width": page_w, "page_height": page_h, "label_count": len(labels),
            "minimum_font_size": min(sizes) if sizes else None, "checks": checks}


def inspect_svg(path):
    text = path.read_text(encoding="utf-8")
    sizes = [float(value) for value in re.findall(r"font-size[:=][\"']?\s*([0-9.]+)", text)]
    sizes.extend(float(value) for value in re.findall(r"font:\s*(?:[0-9]+\s+)?([0-9.]+)px", text))
    sizes = [value for value in sizes if value > 0]
    return {"bytes": path.stat().st_size, "font_declared": FONT in text,
            "minimum_font_size": min(sizes) if sizes else None}


def main():
    errors = []
    if not INVENTORY.is_file():
        raise SystemExit("missing docs/design/figures/figure_inventory.json")
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    diagrams = tuple(inventory.get("diagrams", ()))
    charts = tuple(inventory.get("charts", ()))
    names = diagrams + charts
    if len(names) != len(set(names)) or not diagrams or not charts:
        errors.append("figure inventory is empty or contains duplicate names")
    rows = []
    for stem in names:
        png, svg = FIGURES / f"{stem}.png", FIGURES / f"{stem}.svg"
        checks = {"png_present": png.is_file() and png.stat().st_size > 1000,
                  "svg_present": svg.is_file() and svg.stat().st_size > 1000}
        width = height = 0
        svg_report = {}
        if checks["png_present"]:
            width, height = png_dimensions(png)
            checks.update({"minimum_resolution": width >= 750 and height >= 300,
                           "release_aspect_ratio": width / height >= (1.8 if stem in diagrams else 1.55)})
        if checks["svg_present"]:
            svg_report = inspect_svg(svg)
            checks["approved_svg_font"] = svg_report["font_declared"]
            checks["minimum_svg_font_size"] = (svg_report["minimum_font_size"] is not None and
                                                svg_report["minimum_font_size"] >= 10)
        drawio_report = None
        if stem in diagrams:
            source = FIGURES / f"{stem}.drawio"
            checks["drawio_present"] = source.is_file() and source.stat().st_size > 1000
            if checks["drawio_present"]:
                drawio_report = inspect_drawio(source, stem)
                checks.update(drawio_report["checks"])
        row = {"name": stem, "kind": "diagram" if stem in diagrams else "chart", "width": width, "height": height,
               "svg_report": svg_report, "drawio_report": drawio_report, "checks": checks,
               "status": "PASS" if checks and all(checks.values()) else "FAIL"}
        rows.append(row)
        if row["status"] == "FAIL": errors.append(f"{stem}: {[key for key, ok in checks.items() if not ok]}")

    obsolete_files = [str(path.relative_to(ROOT)) for suffix in ("drawio", "png", "svg", "dot")
                      if (path := FIGURES / f"{OBSOLETE}.{suffix}").exists()]
    if obsolete_files: errors.append(f"obsolete summary graphic remains: {obsolete_files}")
    dot_sources = sorted(str(path.relative_to(ROOT)) for path in FIGURES.glob("*.dot"))
    if dot_sources: errors.append(f"Graphviz release sources remain: {dot_sources}")
    stale_chart_sources = sorted(str(path.relative_to(ROOT)) for stem in charts
                                 if (path := FIGURES / f"{stem}.drawio").exists())
    if stale_chart_sources: errors.append(f"stale chart draw.io sources remain: {stale_chart_sources}")
    payload = {"schema_version": 2, "inventory": str(INVENTORY.relative_to(ROOT)), "figures": rows,
               "obsolete_files": obsolete_files, "graphviz_sources": dot_sources,
               "stale_chart_sources": stale_chart_sources,
               "overall_status": "PASS" if not errors else "FAIL", "errors": errors}
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(payload["overall_status"])
    for row in rows: print(row["name"], row["status"], row["width"], row["height"])
    if errors: print("\n".join(errors))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
