#!/usr/bin/env python3
"""Structural and data-lineage QA for the delivery figure inventory."""

from collections import Counter
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
REQUIRED_CHART_FIELDS = ("name", "source_artifacts", "generator", "metric_definition", "document_references")
STALE_GUEST_LATENCY_FIELDS = {"guest_delta_cycles", "guest_delta_ns_per_operation"}


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


def load_json(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def contains_stale_guest_latency(value):
    if isinstance(value, dict):
        return (bool(STALE_GUEST_LATENCY_FIELDS.intersection(value)) or
                any(contains_stale_guest_latency(item) for item in value.values()))
    if isinstance(value, list):
        return any(contains_stale_guest_latency(item) for item in value)
    return isinstance(value, str) and any(field in value for field in STALE_GUEST_LATENCY_FIELDS)


def expected_chart_values(stem, sources):
    source = {path: load_json(path) for path in sources}
    if stem == "ubcc-metric1-capacity-latency":
        report, outer = source[sources[0]], source[sources[1]]
        first = outer["repeats"]["1"]
        return "expected_values", {
            "capacity_ratio": float(report["metric1"]["capacity_ratio"]),
            "capacity_increase_pct": float(report["metric1"]["capacity_increase_pct"]),
            "ideal_outer_mean_ns": float(first["ideal"]["outer_mean_ns"]),
            "spill_outer_mean_ns": float(first["spill"]["outer_mean_ns"]),
            "outer_delta_mean_ns": float(outer["delta_mean_ns"]),
            "ideal_resident_capacity": int(first["ideal"]["resident_capacity"]),
            "spill_resident_capacity": int(first["spill"]["resident_capacity"]),
        }
    if stem == "ubcc-metric2-reductions":
        metric2 = source[sources[0]]["metric2"]
        return "expected_values", {
            "cases": [{"case": row["case"], "optimized_reduction_pct": float(row["optimized_reduction_pct"]),
                       "applicable": bool(row["applicable"])} for row in metric2["cases"]],
            "applicable_equal_weight_mean_reduction_pct": float(metric2["equal_weight_mean_reduction_pct"]),
        }
    if stem == "ubcc-ha-vi-comparison":
        levels = [level for level in source[sources[0]]["metric3"]["levels"]
                  if int(level["pressure_level"]) == 100]
        return "expected_values", {
            "groups": [{"pressure_level": level["pressure_level"], "scope": scope,
                        "ubcc_ticks_per_operation": float(level[key]["ourcc_ticks_per_operation"]),
                        "ha_vi_ticks_per_operation": float(level[key]["ha_vi_ticks_per_operation"])}
                       for level in levels for key, scope in
                       (("core_equal_weight", "core"), ("representative_equal_weight", "representative"))],
        }
    if stem == "ubcc-q1-q5-qualification":
        matrix = source[sources[0]]
        counts = Counter(row["qualification"] for row in matrix["cases"])
        labels = [f"Q{i}" for i in range(1, 6)]
        return "derived_values", {"qualification_counts": {label: counts[label] for label in labels},
                                  "total": sum(counts[label] for label in labels)}
    if stem in {"ubcc-tc120-124-scenarios", "ubcc-tc130-134-pressure", "ubcc-tc142-147-applications", "ubcc-metric3-per-tc-reductions"}:
        key = {"ubcc-tc120-124-scenarios": "tc120_124", "ubcc-tc130-134-pressure": "tc130_134",
               "ubcc-tc142-147-applications": "tc142_147", "ubcc-metric3-per-tc-reductions": "metric3_per_tc"}[stem]
        raw = load_json("docs/design/performance_preview_data.json")
        cases = raw["testcases"]
        if key == "metric3_per_tc":
            def primary(tc, pressure):
                row = cases[tc]["metric3"][f"p{pressure}"]
                if "ubcc" in row:
                    return row
                return row["composite"] if tc == "TC232" else row["primary"]
            rows = [{"case": tc[2:], "reduction_pct":
                     100 * (1 - primary(tc, 100)["ubcc"] / primary(tc, 100)["ha_vi"])}
                    for tc in ("TC228", "TC229", "TC230", "TC231", "TC232", "TC233", "TC234", "TC235")]
            return "derived_values", {"rows": rows}
        tcs = {"tc120_124": ("TC120", "TC121", "TC122", "TC123", "TC124"), "tc130_134": ("TC130", "TC131", "TC132", "TC133", "TC134"), "tc142_147": ("TC142", "TC143", "TC144", "TC145", "TC146", "TC147")}[key]
        field = "primary_reduction_pct" if key == "tc130_134" else "optimized_reduction_pct"
        rows = [{"case": tc, "reduction_pct": float(cases[tc]["measurements"][field])} for tc in tcs]
        return "derived_values", {"rows": rows}
    raise ValueError(f"no chart lineage validator for {stem}")


def validate_chart_metadata(chart):
    checks = {f"metadata_{field}": bool(chart.get(field)) for field in REQUIRED_CHART_FIELDS}
    checks["metadata_values"] = ("expected_values" in chart) ^ ("derived_values" in chart)
    sources = chart.get("source_artifacts", [])
    checks["source_artifacts_are_relative"] = bool(sources) and all(
        isinstance(path, str) and not Path(path).is_absolute() and ".." not in Path(path).parts for path in sources)
    checks["source_artifacts_exist"] = checks["source_artifacts_are_relative"] and all(
        (ROOT / path).is_file() for path in sources)
    generator = str(chart.get("generator", "")).split("::", 1)[0]
    checks["generator_exists"] = bool(generator) and (ROOT / generator).is_file()
    references = chart.get("document_references", [])
    checks["document_references_exist"] = bool(references) and all(
        isinstance(ref, dict) and ref.get("figure") and ref.get("document") and
        not Path(ref["document"]).is_absolute() and (ROOT / ref["document"]).is_file()
        for ref in references)
    checks["no_stale_guest_latency_field"] = not contains_stale_guest_latency(chart)
    if chart.get("name") == "ubcc-metric1-capacity-latency":
        sets = chart.get("evidence_sets", [])
        checks["metric1_evidence_sets"] = (
            len(sets) == 2 and sets[0].get("physical_runs") == 6 and
            sets[0].get("roles") == ["naive", "spill-noopt"] and
            sets[1].get("physical_arms") == 6 and
            sets[1].get("roles") == ["spill-512K", "spill-IdealDir"] and
            chart.get("cross_set_weighting") ==
            "none; the two evidence sets serve independent Metric1 subcontracts")
    if checks["source_artifacts_exist"]:
        try:
            value_field, expected = expected_chart_values(chart.get("name"), sources)
            checks["values_match_source_json"] = chart.get(value_field) == expected
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
            checks["values_match_source_json"] = False
    else:
        checks["values_match_source_json"] = False
    return checks


def validate_diagram_metadata(diagram):
    references = diagram.get("document_references", []) if isinstance(diagram, dict) else []
    return {
        "diagram_document_references": bool(references) and all(
            isinstance(ref, dict) and ref.get("figure") and ref.get("document") and
            not Path(ref["document"]).is_absolute() and (ROOT / ref["document"]).is_file()
            for ref in references)
    }


def main():
    errors = []
    if not INVENTORY.is_file():
        raise SystemExit("missing docs/design/figures/figure_inventory.json")
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    diagram_entries = tuple(inventory.get("diagrams", ()))
    chart_entries = tuple(inventory.get("charts", ()))
    diagrams = tuple(entry.get("name") if isinstance(entry, dict) else entry for entry in diagram_entries)
    charts = tuple(entry.get("name") if isinstance(entry, dict) else entry for entry in chart_entries)
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
            checks.update(validate_diagram_metadata(diagram_entries[diagrams.index(stem)]))
        else:
            checks.update(validate_chart_metadata(chart_entries[charts.index(stem)]))
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
    payload = {"schema_version": 3, "review_method": "automated_structural_and_lineage",
               "human_visual_review": "NOT_RUN",
               "limitations": ["No pixel-level aesthetic, brand, color-perception, or semantic-arrow review was performed."],
               "inventory": str(INVENTORY.relative_to(ROOT)), "figures": rows,
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
