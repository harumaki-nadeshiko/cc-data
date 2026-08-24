#!/usr/bin/env python3
"""Summarize fixed-workset L3-size sensitivity results."""

import argparse
import json
import pathlib
import re
import sys


OCC = re.compile(
    r"\[L3-OCC-SAMPLE\].*node=(\d+).*capacity=(\d+).*current=(\d+).*"
    r"peak=(\d+).*dsm=(\d+).*metadata=(\d+).*other=(\d+).*"
    r"peak_dsm=(\d+).*peak_metadata=(\d+).*peak_other=(\d+).*"
    r"alloc=(\d+).*dealloc=(\d+).*replacements=(\d+)")


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def occupancy(log_dir):
    rows = []
    for path in log_dir.glob("gem5_tc*_node*/stderr.log"):
        for line in path.read_text(errors="replace").splitlines():
            match = OCC.search(line)
            if match:
                rows.append(tuple(int(match.group(index)) for index in range(1, 14)))
    by_node = {}
    for row in rows:
        by_node.setdefault(row[0], []).append(row)
    return {
        str(node): {
            "capacity_lines": values[0][1],
            "peak_lines": max(value[3] for value in values),
            "peak_pct": max(value[3] for value in values) * 100.0 / values[0][1],
            "metadata_peak_lines": max(value[8] for value in values),
            "deallocations": max(value[11] for value in values),
            "replacements": max(value[12] for value in values),
        }
        for node, values in sorted(by_node.items())
    }


def write_svg(path, width, height, body):
    text = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}">\n'
            '<style>text{font-family:DejaVu Sans,Arial,sans-serif;fill:#172033}'
            '.title{font-size:22px;font-weight:700}.axis{font-size:12px}'
            '.label{font-size:11px}.grid{stroke:#d8deea;stroke-width:1}'
            '.ourcc{stroke:#126b61;fill:none;stroke-width:2.5}'
            '.havi{stroke:#b34d2e;fill:none;stroke-width:2.5}'
            '.point-o{fill:#126b61}.point-h{fill:#b34d2e}</style>\n' +
            body + '\n</svg>\n')
    path.write_text(text)


def performance_figure(points, sizes, targets, testcases, output):
    width, panel_w, panel_h = 1440, 345, 235
    metric_keys = sorted({(row["tc"], row["metric"]) for row in points})
    panel_rows = (len(metric_keys) + 3) // 4
    height = 80 + panel_rows * panel_h + 80
    panels = []
    colors = {0: "#8793a8", 4096: "#2f73b8", 6144: "#7c4da1"}
    size_index = {size: index for index, size in enumerate(sizes)}
    for panel_index, (tc, metric) in enumerate(metric_keys):
        x0 = 45 + (panel_index % 4) * panel_w
        y0 = 60 + (panel_index // 4) * panel_h
        rows = [row for row in points if row["tc"] == tc and
                row["metric"] == metric]
        maximum = max(max(row["ourcc_ns_per_operation"],
                          row["ha_vi_ns_per_operation"]) for row in rows) * 1.08
        plot_x0, plot_y0 = x0 + 48, y0 + 28
        plot_w, plot_h = panel_w - 70, panel_h - 65
        panels.append(f'<text x="{x0}" y="{y0 + 16}" class="title" font-size="15">TC{tc} {metric}</text>')
        for step in range(5):
            y = plot_y0 + plot_h * step / 4
            value = maximum * (1 - step / 4)
            panels.append(f'<line x1="{plot_x0}" y1="{y:.1f}" x2="{plot_x0 + plot_w}" y2="{y:.1f}" class="grid"/>')
            panels.append(f'<text x="{plot_x0 - 6}" y="{y + 4:.1f}" text-anchor="end" class="label">{value:.0f}</text>')
        for size, index in size_index.items():
            x = plot_x0 + plot_w * index / max(1, len(sizes) - 1)
            panels.append(f'<text x="{x:.1f}" y="{plot_y0 + plot_h + 18}" text-anchor="middle" class="label">{size}</text>')
        for target in targets:
            target_rows = sorted(
                (row for row in rows if row["target_lines"] == target),
                key=lambda row: size_index[row["l3_size"]])
            for arm, field, dash in (("O", "ourcc_ns_per_operation", ""),
                                     ("H", "ha_vi_ns_per_operation", "5,3")):
                coordinates = []
                for row in target_rows:
                    x = plot_x0 + plot_w * size_index[row["l3_size"]] / max(1, len(sizes) - 1)
                    y = plot_y0 + plot_h * (1 - row[field] / maximum)
                    coordinates.append(f"{x:.1f},{y:.1f}")
                panels.append(
                    f'<polyline points="{" ".join(coordinates)}" stroke="{colors[target]}" '
                    f'fill="none" stroke-width="{2.5 if arm == "O" else 1.6}" '
                    f'stroke-dasharray="{dash}" opacity="{1 if arm == "O" else 0.8}"/>')
        panels.append(f'<text x="{plot_x0 + plot_w/2:.1f}" y="{plot_y0 + plot_h + 35}" text-anchor="middle" class="axis">L3 size; solid OurCC, dashed HA-VI</text>')
    legend = ['<text x="45" y="24" class="title">Fixed-workset L3 sensitivity: ns/op</text>']
    for index, target in enumerate(targets):
        x = 780 + index * 180
        legend.append(f'<line x1="{x}" y1="20" x2="{x+35}" y2="20" stroke="{colors[target]}" stroke-width="3"/>')
        legend.append(f'<text x="{x+42}" y="24" class="axis">target {target}</text>')
    write_svg(output, width, height, "\n".join(legend + panels))


def heatmap_figure(points, sizes, targets, testcases, output):
    cell_w, cell_h = 75, 34
    left, top = 190, 70
    columns = [(size, target) for size in sizes for target in targets]
    width = left + len(columns) * cell_w + 30
    metric_keys = sorted({(row["tc"], row["metric"]) for row in points})
    height = top + len(metric_keys) * cell_h + 80
    body = ['<text x="20" y="28" class="title">OurCC reduction versus HA-VI (%)</text>']
    for column, (size, target) in enumerate(columns):
        x = left + column * cell_w
        body.append(f'<text x="{x + cell_w/2:.1f}" y="48" text-anchor="middle" class="label">{size}</text>')
        body.append(f'<text x="{x + cell_w/2:.1f}" y="63" text-anchor="middle" class="label">t={target}</text>')
    lookup = {(row["tc"], row["metric"], row["l3_size"],
               row["target_lines"]): row
              for row in points}
    for row_index, (tc, metric) in enumerate(metric_keys):
        y = top + row_index * cell_h
        body.append(f'<text x="{left - 8}" y="{y + 22}" text-anchor="end" class="axis">TC{tc} {metric}</text>')
        for column, (size, target) in enumerate(columns):
            point = lookup.get((tc, metric, size, target))
            x = left + column * cell_w
            if point is None:
                body.append(f'<rect x="{x}" y="{y}" width="{cell_w-2}" height="{cell_h-2}" fill="#e5e8ef"/>')
                body.append(f'<text x="{x + (cell_w-2)/2:.1f}" y="{y + 21}" text-anchor="middle" class="label">N/A</text>')
                continue
            value = point["ourcc_reduction_pct"]
            magnitude = min(abs(value) / 50.0, 1.0)
            if value >= 0:
                red = int(235 - 125 * magnitude); green = int(248 - 70 * magnitude); blue = int(242 - 95 * magnitude)
            else:
                red = int(250 - 30 * magnitude); green = int(235 - 105 * magnitude); blue = int(230 - 100 * magnitude)
            body.append(f'<rect x="{x}" y="{y}" width="{cell_w-2}" height="{cell_h-2}" fill="rgb({red},{green},{blue})"/>')
            body.append(f'<text x="{x + (cell_w-2)/2:.1f}" y="{y + 21}" text-anchor="middle" class="label">{value:.1f}</text>')
    body.append(f'<text x="{left}" y="{height-25}" class="axis">Positive: OurCC faster. Negative: HA-VI faster. One paired screening sample per cell.</text>')
    write_svg(output, width, height, "\n".join(body))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-root", type=pathlib.Path, required=True)
    args = parser.parse_args()
    root = args.result_root.resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    errors, points = [], []
    runtime_identities = set()
    for size_row in manifest["l3_sizes"]:
        size = size_row["size"]
        capacity = size_row["capacity_lines"]
        normalized = size.replace("KiB", "k").replace("kB", "k")
        normalized = normalized.replace("MiB", "m").replace("MB", "m")
        for target in manifest["target_lines"]:
            for tc in manifest["testcases"]:
                identity = f"l3_{normalized}_t{target:05d}_tc{tc}"
                arms = {}
                hashes = set()
                for arm in ("ourcc", "ha-vi"):
                    result_path = root / "cases" / identity / arm / "result.json"
                    try:
                        result = json.loads(result_path.read_text())
                    except Exception as error:
                        errors.append(f"{identity} {arm}: {error}")
                        continue
                    if result.get("status") != "PASS":
                        errors.append(f"{identity} {arm}: result failure")
                    if result.get("l3_size") != size or \
                            result.get("target_lines") != target or \
                            result.get("tc") != tc:
                        errors.append(f"{identity} {arm}: identity mismatch")
                    hashes.add(result.get("workload_elf_sha256"))
                    runtime_identities.add(json.dumps(
                        result.get("runtime_sha256"), sort_keys=True))
                    arms[arm] = {
                        "metrics": result.get("metrics", {}),
                        "occupancy": occupancy(result_path.parent),
                        "log_dir": result.get("log_dir"),
                    }
                if len(hashes) != 1 or None in hashes:
                    errors.append(f"{identity}: ELF identity mismatch")
                if set(arms) != {"ourcc", "ha-vi"}:
                    continue
                metric_names = sorted(set(arms["ourcc"]["metrics"]) &
                                      set(arms["ha-vi"]["metrics"]))
                if not metric_names:
                    errors.append(f"{identity}: paired metric missing")
                    continue
                if set(arms["ourcc"]["metrics"]) != set(arms["ha-vi"]["metrics"]):
                    errors.append(f"{identity}: paired metric identity mismatch")
                for metric_name in metric_names:
                    ourcc = arms["ourcc"]["metrics"][metric_name]["ns_per_operation"]
                    havi = arms["ha-vi"]["metrics"][metric_name]["ns_per_operation"]
                    points.append({
                        "point_id": identity + "_" + metric_name,
                        "sample_id": identity,
                        "l3_size": size,
                        "capacity_lines": capacity,
                        "target_lines": target,
                        "effective_pressure_pct": target * 100.0 / capacity,
                        "tc": tc,
                        "metric": metric_name,
                        "ourcc_ns_per_operation": ourcc,
                        "ha_vi_ns_per_operation": havi,
                        "delta_ns": havi - ourcc,
                        "ourcc_reduction_pct": ((havi - ourcc) * 100.0 / havi
                                                 if havi else 0.0),
                        "arms": arms,
                    })

    baselines = {(row["l3_size"], row["tc"], row["metric"]): row
                 for row in points if row["target_lines"] == 0}
    for row in points:
        baseline = baselines.get(
            (row["l3_size"], row["tc"], row["metric"]))
        if baseline:
            row["ourcc_change_vs_zero_pct"] = (
                (row["ourcc_ns_per_operation"] -
                 baseline["ourcc_ns_per_operation"]) * 100.0 /
                baseline["ourcc_ns_per_operation"])
            row["ha_vi_change_vs_zero_pct"] = (
                (row["ha_vi_ns_per_operation"] -
                 baseline["ha_vi_ns_per_operation"]) * 100.0 /
                baseline["ha_vi_ns_per_operation"])

    aggregates = []
    for size_row in manifest["l3_sizes"]:
        for target in manifest["target_lines"]:
            rows = [row for row in points if row["l3_size"] == size_row["size"]
                    and row["target_lines"] == target and
                    row["tc"] in (228, 229, 230)]
            if len(rows) != 3:
                continue
            ourcc = sum(row["ourcc_ns_per_operation"] for row in rows) / len(rows)
            havi = sum(row["ha_vi_ns_per_operation"] for row in rows) / len(rows)
            aggregates.append({
                "l3_size": size_row["size"],
                "capacity_lines": size_row["capacity_lines"],
                "target_lines": target,
                "effective_pressure_pct": target * 100.0 /
                    size_row["capacity_lines"],
                "ourcc_equal_core_ns_per_operation": ourcc,
                "ha_vi_equal_core_ns_per_operation": havi,
                "delta_ns": havi - ourcc,
                "ourcc_reduction_pct": (havi - ourcc) * 100.0 / havi,
            })

    payload = {
        "schema_version": 1,
        "status": "VALID/SCREENING" if not errors else "INVALID",
        "pair_count": 1,
        "runtime_identity_count": len(runtime_identities),
        "errors": errors,
        "aggregates": aggregates,
        "points": points,
    }
    atomic_json(root / "analysis.json", payload)
    lines = ["# Metric3 fixed-workset L3-size sensitivity", "",
             f"Status: **{payload['status']}**", "",
             "| L3 | Target lines | Pressure | TC | Metric | OurCC ns/op | HA-VI ns/op | OurCC reduction |",
             "|---:|---:|---:|---:|---|---:|---:|---:|"]
    for row in points:
        lines.append(
            f"| {row['l3_size']} | {row['target_lines']} | "
            f"{row['effective_pressure_pct']:.1f}% | {row['tc']} | "
            f"{row['metric']} | {row['ourcc_ns_per_operation']:.3f} | "
            f"{row['ha_vi_ns_per_operation']:.3f} | "
            f"{row['ourcc_reduction_pct']:.2f}% |")
    lines += ["", "## Equal-core aggregate", "",
              "| L3 | Target lines | Pressure | OurCC ns/op | HA-VI ns/op | OurCC reduction |",
              "|---:|---:|---:|---:|---:|---:|"]
    for row in aggregates:
        lines.append(
            f"| {row['l3_size']} | {row['target_lines']} | "
            f"{row['effective_pressure_pct']:.1f}% | "
            f"{row['ourcc_equal_core_ns_per_operation']:.3f} | "
            f"{row['ha_vi_equal_core_ns_per_operation']:.3f} | "
            f"{row['ourcc_reduction_pct']:.2f}% |")
    if errors:
        lines += ["", "## Errors", ""] + [f"- {error}" for error in errors]
    (root / "analysis.md").write_text("\n".join(lines) + "\n")
    sizes = [row["size"] for row in manifest["l3_sizes"]]
    targets = manifest["target_lines"]
    testcases = manifest["testcases"]
    performance_figure(points, sizes, targets, testcases,
                       root / "figure_ns_per_operation.svg")
    heatmap_figure(points, sizes, targets, testcases,
                   root / "figure_ourcc_reduction_heatmap.svg")
    print(payload["status"])
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
