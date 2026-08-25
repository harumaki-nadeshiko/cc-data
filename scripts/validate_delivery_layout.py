#!/usr/bin/env python3
"""Validate visual density and geometry of rendered delivery PDFs.

The checker intentionally consumes PDF renderer/extractor output rather than
Markdown or DOCX structure.  It requires Poppler's ``pdftohtml`` and
``pdftoppm`` commands, both supplied by the ubcc-doc-evolve image.
"""

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import statistics
import subprocess
import tempfile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PDFS = tuple(
    ROOT / "docs/design" / name for name in (
        "cc_ep_protocol_overview.pdf",
        "cc_ep_deliverable2_verification_reliability_ha.pdf",
        "cc_ep_deliverable3_performance_api.pdf",
    )
)
DEFAULT_OUTPUT = ROOT / "docs/design/layout_qa.json"
THRESHOLDS = {
    "white_pixel_cutoff": 250,
    "minimum_non_white_ratio": 0.025,
    "minimum_text_characters": 80,
    "orphan_heading_top_fraction": 0.80,
    "heading_body_size_multiplier": 1.20,
    "maximum_single_figure_page_fraction": 0.62,
    "maximum_all_figures_page_fraction": 0.72,
    "maximum_stretched_cjk_width_em": 1.45,
    "minimum_stretched_cjk_characters": 4,
    "geometry_tolerance": 2.0,
    "page_size_relative_tolerance": 0.002,
    "minimum_text_size_points": 7.5,
    "minimum_tiny_text_characters": 4,
}


@dataclass
class TextBox:
    left: float
    top: float
    width: float
    height: float
    font_size: float
    text: str
    bold: bool = False


@dataclass
class ImageBox:
    left: float
    top: float
    width: float
    height: float


@dataclass
class PageInput:
    number: int
    width: float
    height: float
    text_boxes: list = field(default_factory=list)
    images: list = field(default_factory=list)
    non_white_ratio: float = 0.0


def compact_text(value):
    return re.sub(r"\s+", "", value or "")


def display_path(path):
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def is_exempt_front_matter(page):
    text = compact_text("".join(box.text for box in page.text_boxes)).lower()
    return page.number == 1 or (page.number <= 3 and
                                ("目录" in text or "tableofcontents" in text))


def rectangle_fraction(box, page):
    return max(0.0, box.width) * max(0.0, box.height) / (page.width * page.height)


def analyze_page(page, thresholds=THRESHOLDS):
    """Apply deterministic threshold logic to one already-extracted page."""
    issues = []
    text = compact_text("".join(box.text for box in page.text_boxes))
    char_count = len(text)
    exempt = is_exempt_front_matter(page)
    if not exempt and page.non_white_ratio < thresholds["minimum_non_white_ratio"]:
        issues.append({"code": "LOW_NON_WHITE_RATIO", "value": page.non_white_ratio,
                       "threshold": thresholds["minimum_non_white_ratio"]})
    if not exempt and char_count < thresholds["minimum_text_characters"]:
        issues.append({"code": "VERY_FEW_TEXT_CHARACTERS", "value": char_count,
                       "threshold": thresholds["minimum_text_characters"]})

    useful = [box for box in page.text_boxes if len(compact_text(box.text)) >= 2]
    sizes = [box.font_size for box in useful if box.font_size > 0]
    body_size = statistics.median(sizes) if sizes else 0.0
    heading_limit = body_size * thresholds["heading_body_size_multiplier"]
    for box in useful:
        near_bottom = box.top / page.height >= thresholds["orphan_heading_top_fraction"]
        heading_like = box.font_size >= max(12.0, heading_limit) and (
            box.bold or box.font_size > heading_limit)
        following = [other for other in useful
                     if other.top > box.top + box.height and other.top < page.height * 0.95]
        if near_bottom and heading_like and not following:
            issues.append({"code": "ORPHAN_HEADING_NEAR_PAGE_BOTTOM",
                           "text": compact_text(box.text)[:80],
                           "top_fraction": round(box.top / page.height, 4)})

    image_fractions = [rectangle_fraction(image, page) for image in page.images]
    if image_fractions and max(image_fractions) > thresholds["maximum_single_figure_page_fraction"]:
        issues.append({"code": "FIGURE_EXCESSIVE_PAGE_SHARE",
                       "value": round(max(image_fractions), 4),
                       "threshold": thresholds["maximum_single_figure_page_fraction"]})
    if sum(image_fractions) > thresholds["maximum_all_figures_page_fraction"]:
        issues.append({"code": "FIGURES_EXCESSIVE_COMBINED_PAGE_SHARE",
                       "value": round(sum(image_fractions), 4),
                       "threshold": thresholds["maximum_all_figures_page_fraction"]})

    for box in useful:
        cjk = re.findall(r"[\u3400-\u9fff]", box.text)
        visible = compact_text(box.text)
        if (len(cjk) >= thresholds["minimum_stretched_cjk_characters"] and
                box.font_size > 0 and box.width / (len(visible) * box.font_size) >
                thresholds["maximum_stretched_cjk_width_em"]):
            issues.append({"code": "LIKELY_STRETCHED_OR_JUSTIFIED_TABLE_TEXT",
                           "text": compact_text(box.text)[:80],
                           "width_em": round(box.width / (len(visible) * box.font_size), 3)})

    tolerance = thresholds["geometry_tolerance"]
    for kind, boxes in (("text", page.text_boxes), ("image", page.images)):
        for box in boxes:
            if (box.left < -tolerance or box.top < -tolerance or
                    box.left + box.width > page.width + tolerance or
                    box.top + box.height > page.height + tolerance):
                issues.append({"code": "CONTENT_CLIPPED_OUTSIDE_PAGE", "kind": kind,
                               "bounds": [box.left, box.top, box.width, box.height]})

    tiny = [box for box in useful
            if len(compact_text(box.text)) >= thresholds["minimum_tiny_text_characters"]
            and 0 < box.font_size < thresholds["minimum_text_size_points"]]
    if tiny:
        smallest = min(tiny, key=lambda box: box.font_size)
        issues.append({"code": "UNEXPECTEDLY_TINY_TEXT", "value": round(smallest.font_size, 2),
                       "threshold": thresholds["minimum_text_size_points"],
                       "text": compact_text(smallest.text)[:80]})

    return {
        "page": page.number,
        "width_points": round(page.width, 3),
        "height_points": round(page.height, 3),
        "non_white_ratio": round(page.non_white_ratio, 6),
        "text_characters": char_count,
        "front_matter_density_exempt": exempt,
        "image_page_fractions": [round(value, 4) for value in image_fractions],
        "minimum_text_size_points": round(min(sizes), 2) if sizes else None,
        "issues": issues,
        "status": "FAIL" if issues else "PASS",
    }


def read_pgm_non_white_ratio(path, cutoff):
    data = path.read_bytes()
    tokens = []
    cursor = 0
    while len(tokens) < 4:
        while cursor < len(data) and chr(data[cursor]).isspace():
            cursor += 1
        if cursor < len(data) and data[cursor] == ord("#"):
            cursor = data.find(b"\n", cursor) + 1
            continue
        end = cursor
        while end < len(data) and not chr(data[end]).isspace():
            end += 1
        tokens.append(data[cursor:end])
        cursor = end
    if tokens[0] != b"P5":
        raise ValueError(f"unsupported PGM encoding in {path}")
    width, height, maximum = map(int, tokens[1:])
    while cursor < len(data) and chr(data[cursor]).isspace():
        cursor += 1
    pixels = data[cursor:]
    if maximum > 255 or len(pixels) != width * height:
        raise ValueError(f"unsupported or truncated PGM in {path}")
    return sum(pixel < cutoff for pixel in pixels) / len(pixels)


def extract_pages(xml_path, pgm_paths, thresholds=THRESHOLDS):
    root = ET.parse(xml_path).getroot()
    fonts = {item.get("id"): float(item.get("size", "0"))
             for item in root.findall(".//fontspec")}
    pages = []
    for index, element in enumerate(root.findall("page")):
        render_width = float(element.get("width"))
        # pdftohtml's default geometry is rendered at 108 dpi (1.5 PDF points).
        scale = render_width / 595.303937 if render_width else 1.5
        width = render_width / scale
        height = float(element.get("height")) / scale
        texts = []
        for item in element.findall("text"):
            value = "".join(item.itertext())
            texts.append(TextBox(float(item.get("left")) / scale,
                                 float(item.get("top")) / scale,
                                 float(item.get("width")) / scale,
                                 float(item.get("height")) / scale,
                                 fonts.get(item.get("font"), 0.0) / scale, value,
                                 item.find("b") is not None))
        images = [ImageBox(float(item.get("left")) / scale,
                           float(item.get("top")) / scale,
                           float(item.get("width")) / scale,
                           float(item.get("height")) / scale)
                  for item in element.findall("image")]
        ratio = read_pgm_non_white_ratio(pgm_paths[index], thresholds["white_pixel_cutoff"])
        pages.append(PageInput(index + 1, width, height, texts, images, ratio))
    return pages


def run_tool(command):
    try:
        subprocess.run(command, check=True, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True)
    except FileNotFoundError as error:
        raise RuntimeError(f"required PDF tool not found: {command[0]}") from error
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"{' '.join(command)} failed: {error.stderr.strip()}") from error


def validate_pdf(pdf_path, thresholds=THRESHOLDS):
    with tempfile.TemporaryDirectory(prefix="layout-qa-") as temporary:
        temporary = Path(temporary)
        xml_prefix = temporary / "layout"
        pgm_prefix = temporary / "page"
        run_tool(["pdftohtml", "-xml", "-hidden", "-nodrm", str(pdf_path), str(xml_prefix)])
        run_tool(["pdftoppm", "-gray", "-r", "72", str(pdf_path), str(pgm_prefix)])
        xml_path = xml_prefix.with_suffix(".xml")
        pgms = sorted(temporary.glob("page-*.pgm"))
        pages = extract_pages(xml_path, pgms, thresholds)

    reports = [analyze_page(page, thresholds) for page in pages]
    size_issues = apply_page_size_checks(pages, reports, thresholds)
    failed = sum(report["status"] == "FAIL" for report in reports)
    return {"document": str(pdf_path), "page_count": len(reports), "pages": reports,
            "inconsistent_page_sizes": size_issues,
            "failed_page_count": failed, "status": "FAIL" if failed else "PASS"}


def apply_page_size_checks(pages, reports, thresholds=THRESHOLDS):
    """Attach inconsistent-size failures to page reports and return the issues."""
    size_issues = []
    if pages:
        reference = (pages[0].width, pages[0].height)
        tolerance = thresholds["page_size_relative_tolerance"]
        for page in pages[1:]:
            if (abs(page.width - reference[0]) / reference[0] > tolerance or
                    abs(page.height - reference[1]) / reference[1] > tolerance):
                issue = {"code": "INCONSISTENT_PAGE_SIZE", "page": page.number,
                         "size": [page.width, page.height], "reference": list(reference)}
                reports[page.number - 1]["issues"].append(issue)
                reports[page.number - 1]["status"] = "FAIL"
                size_issues.append(issue)
    return size_issues


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", nargs="*", type=Path, default=list(DEFAULT_PDFS))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    documents = []
    errors = []
    for path in args.pdf:
        path = path if path.is_absolute() else ROOT / path
        if not path.is_file():
            errors.append(f"missing PDF: {path}")
            continue
        try:
            report = validate_pdf(path)
            report["document"] = display_path(path)
            documents.append(report)
        except (RuntimeError, ValueError, ET.ParseError, IndexError) as error:
            errors.append(f"{path}: {error}")
    payload = {"schema_version": 1, "thresholds": THRESHOLDS,
               "documents": documents, "errors": errors,
               "overall_status": "PASS" if not errors and all(
                   item["status"] == "PASS" for item in documents) else "FAIL"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(payload["overall_status"])
    for document in documents:
        print(document["document"], document["status"],
              f"{document['failed_page_count']}/{document['page_count']} failed pages")
    for error in errors:
        print(error)
    return 0 if payload["overall_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
