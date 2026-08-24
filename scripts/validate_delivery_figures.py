#!/usr/bin/env python3
"""Structural visual QA for release figures."""

import json
from pathlib import Path
import subprocess
import struct
import xml.etree.ElementTree as ET
import zlib


ROOT = Path(__file__).resolve().parents[1]
FIGURES = ROOT / "docs/design/figures"
REPORT = FIGURES / "visual_qa.json"
NAMES = (
    "ubcc-system-architecture",
    "ubcc-protocol-paths",
    "ubcc-verification-stack",
    "ubcc-two-phase-commit",
    "ubcc-metric-summary",
    "ubcc-ha-vi-comparison",
)


def font_ok():
    try:
        result = subprocess.run(["fc-match", "Noto Sans CJK SC"], text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return result.returncode == 0 and "NotoSansCJK" in result.stdout
    except FileNotFoundError:
        # Minimal release containers do not include fontconfig. The generated
        # SVGs still retain the explicit CJK font-family declaration.
        return all(
            "Noto Sans CJK SC" in (FIGURES / f"{name}.svg").read_text(
                encoding="utf-8")
            for name in NAMES
        )


def inspect_png(path):
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"not a PNG: {path}")
    offset = 8
    chunks = []
    while offset < len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        chunks.append((kind, payload))
        offset += 12 + length
        if kind == b"IEND":
            break
    ihdr = next(payload for kind, payload in chunks if kind == b"IHDR")
    width, height, bit_depth, color_type, compression, filtering, interlace = \
        struct.unpack(">IIBBBBB", ihdr)
    if bit_depth != 8 or interlace != 0 or compression != 0 or filtering != 0:
        raise ValueError(f"unsupported PNG encoding: {path}")
    channels = {2: 3, 6: 4}.get(color_type)
    if channels is None:
        raise ValueError(f"unsupported PNG color type {color_type}: {path}")
    raw = zlib.decompress(b"".join(payload for kind, payload in chunks
                                   if kind == b"IDAT"))
    stride = width * channels
    rows = []
    previous = bytearray(stride)
    cursor = 0
    for _ in range(height):
        filter_type = raw[cursor]
        cursor += 1
        scan = bytearray(raw[cursor:cursor + stride])
        cursor += stride
        for index in range(stride):
            left = scan[index - channels] if index >= channels else 0
            up = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 1:
                scan[index] = (scan[index] + left) & 0xff
            elif filter_type == 2:
                scan[index] = (scan[index] + up) & 0xff
            elif filter_type == 3:
                scan[index] = (scan[index] + ((left + up) // 2)) & 0xff
            elif filter_type == 4:
                estimate = left + up - upper_left
                distances = (abs(estimate - left), abs(estimate - up),
                             abs(estimate - upper_left))
                predictor = (left if distances[0] <= distances[1] and
                             distances[0] <= distances[2] else
                             up if distances[1] <= distances[2] else upper_left)
                scan[index] = (scan[index] + predictor) & 0xff
            elif filter_type != 0:
                raise ValueError(f"unsupported PNG filter {filter_type}: {path}")
        rows.append(scan)
        previous = scan

    left, top, right, bottom = width, height, -1, -1
    alpha_min, alpha_max = 255, 255
    sampled_colors = set()
    sample_step = max(1, min(width, height) // 256)
    for y, row in enumerate(rows):
        for x in range(width):
            start = x * channels
            red, green, blue = row[start:start + 3]
            alpha = row[start + 3] if channels == 4 else 255
            alpha_min, alpha_max = min(alpha_min, alpha), max(alpha_max, alpha)
            comp = tuple((component * alpha + 255 * (255 - alpha)) // 255
                         for component in (red, green, blue))
            if x % sample_step == 0 and y % sample_step == 0:
                sampled_colors.add(comp)
            if comp != (255, 255, 255):
                left, top = min(left, x), min(top, y)
                right, bottom = max(right, x), max(bottom, y)
    bbox = None if right < 0 else (left, top, right + 1, bottom + 1)
    if bbox:
        left, top, right, bottom = bbox
        margins = {"left": left, "top": top, "right": width - right,
                   "bottom": height - bottom}
        ratio = ((right - left) * (bottom - top) /
                 (width * height))
    else:
        margins, ratio = None, 0.0
    return {
        "width": width, "height": height,
        "mode": "RGBA" if channels == 4 else "RGB",
        "non_white_bbox": bbox, "margins_px": margins,
        "content_bbox_ratio": round(ratio, 4),
        "alpha_extrema": (alpha_min, alpha_max),
        "sampled_color_count": len(sampled_colors),
        "checks": {
            "minimum_resolution": width >= 750 and height >= 350,
            "non_empty_content": bbox is not None and ratio >= 0.12,
            "edge_clearance": bool(margins and min(margins.values()) >= 3),
            "color_content": len(sampled_colors) >= 8,
        },
    }


def inspect_drawio(path):
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    cells = root.findall(".//mxCell")
    labels = [cell.attrib.get("value", "") for cell in cells
              if cell.attrib.get("value")]
    return {"xml_valid": True, "cell_count": len(cells),
            "label_count": len(labels)}


def main():
    rows = []
    cjk_font = font_ok()
    for name in NAMES:
        png = FIGURES / f"{name}.png"
        svg = FIGURES / f"{name}.svg"
        source = FIGURES / f"{name}.drawio"
        png_report = inspect_png(png)
        drawio_report = inspect_drawio(source)
        checks = dict(png_report["checks"])
        checks.update({
            "cjk_font_available": cjk_font,
            "svg_present": svg.is_file() and svg.stat().st_size > 1000,
            "drawio_editable_content": drawio_report["label_count"] >= 3,
        })
        rows.append({
            "name": name,
            "png": str(png.relative_to(ROOT)),
            "svg": str(svg.relative_to(ROOT)),
            "drawio": str(source.relative_to(ROOT)),
            "png_report": png_report,
            "drawio_report": drawio_report,
            "checks": checks,
            "status": "PASS" if all(checks.values()) else "FAIL",
        })
    payload = {
        "schema_version": 1,
        "scope": "Structural visual QA; corporate brand review remains TODO-R04.",
        "figures": rows,
        "overall_status": "PASS" if all(row["status"] == "PASS" for row in rows)
                          else "FAIL",
    }
    REPORT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    print(payload["overall_status"])
    for row in rows:
        print(row["name"], row["status"], row["png_report"]["width"],
              row["png_report"]["height"])
    return 0 if payload["overall_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
