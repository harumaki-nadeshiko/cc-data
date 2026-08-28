#!/usr/bin/env python3
"""Verify that delivery documents and figures use only the approved fonts."""

import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parents[1]
DOCX_FILES = (
    "docs/design/cc_ep_protocol_overview.docx",
    "docs/design/cc_ep_deliverable2_verification_reliability_ha.docx",
    "docs/design/cc_ep_deliverable3_performance_api.docx",
)
PDF_FILES = tuple(path.replace(".docx", ".pdf") for path in DOCX_FILES)
FIGURE_STEMS = (
    "ubcc-system-architecture",
    "gem5-ruby-controller-relationships",
    "ubcc-protocol-paths",
    "ubcc-verification-stack",
    "ubcc-two-phase-commit",
    "ubcc-protocol-authority-comparison",
    "ubcc-path-central-vs-direct",
    "ubcc-metadata-fanout-scaling",
    "ubcc-inner-chi-outer-boundary",
    "ubcc-metric1-capacity-latency",
    "ubcc-metric2-reductions",
    "ubcc-ha-vi-comparison",
    "ubcc-q1-q5-qualification",
    "ubcc-tc120-124-scenarios",
    "ubcc-tc130-134-pressure",
    "ubcc-tc142-147-applications",
    "ubcc-metric3-per-tc-reductions",
)
ALLOWED = {"Calibri", "Calibri-Bold", "Consolas", "MicrosoftYaHei", "SimHei",
           "STIXTwoMath-Regular", "STIXTwoMath"}
DOCX_ALLOWED = {"Calibri", "Consolas", "Microsoft YaHei", "SimHei", "STIX Two Math"}
MATH_FONT = ROOT / "docs/fonts/stix-math/STIXTwoMath-Regular.ttf"
MATH_LICENSE = ROOT / "docs/fonts/stix-math/OFL.txt"
MATH_FONT_SHA256 = "562551b15b836e6e01d1b7350909baf3c8c8d83260c1190fbf4544333e6936de"
MATH_FIGURES = {"ubcc-path-central-vs-direct", "ubcc-metadata-fanout-scaling"}


def docx_fonts(path):
    with zipfile.ZipFile(path) as archive:
        xml = "\n".join(
            archive.read(name).decode(errors="replace")
            for name in archive.namelist() if name.endswith(".xml"))
    return set(re.findall(r'w:(?:ascii|hAnsi|eastAsia|cs)="([^"]+)"', xml))


def pdf_fonts(path):
    output = subprocess.run(["pdffonts", str(path)], check=True, text=True,
                            stdout=subprocess.PIPE).stdout.splitlines()[2:]
    fonts = set()
    not_embedded = []
    for line in output:
        fields = line.split()
        if len(fields) < 8:
            continue
        name = fields[0].split("+", 1)[-1]
        fonts.add(name)
        if fields[-5] != "yes":
            not_embedded.append(fields[0])
    return fonts, not_embedded


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-pdf", action="store_true")
    args = parser.parse_args()
    errors = []
    if not MATH_FONT.is_file() or hashlib.sha256(MATH_FONT.read_bytes()).hexdigest() != MATH_FONT_SHA256:
        errors.append("docs/fonts/stix-math/STIXTwoMath-Regular.ttf: missing or unexpected SHA-256")
    if not MATH_LICENSE.is_file() or "SIL OPEN FONT LICENSE" not in MATH_LICENSE.read_text(errors="replace"):
        errors.append("docs/fonts/stix-math/OFL.txt: missing OFL text")
    for relative in DOCX_FILES:
        path = ROOT / relative
        extra = docx_fonts(path) - DOCX_ALLOWED
        if extra:
            errors.append(f"{relative}: unapproved DOCX fonts {sorted(extra)}")
    for stem in FIGURE_STEMS:
        svg = ROOT / "docs/design/figures" / f"{stem}.svg"
        if not svg.exists():
            errors.append(f"{svg.relative_to(ROOT)}: missing figure")
            continue
        text = svg.read_text(encoding="utf-8")
        if "Microsoft YaHei" not in text:
            errors.append(f"{svg.relative_to(ROOT)}: missing approved SVG font")
        if stem in MATH_FIGURES and "STIX Two Math" not in text:
            errors.append(f"{svg.relative_to(ROOT)}: missing approved math font")
        source = ROOT / "docs/design/figures" / f"{stem}.drawio"
        if source.exists() and "fontFamily=Microsoft YaHei" not in source.read_text(
                encoding="utf-8"):
            errors.append(f"{source.relative_to(ROOT)}: missing approved draw.io font")
    if not args.skip_pdf:
        for relative in PDF_FILES:
            path = ROOT / relative
            fonts, not_embedded = pdf_fonts(path)
            extra = fonts - ALLOWED
            if extra:
                errors.append(f"{relative}: unapproved PDF fonts {sorted(extra)}")
            if not_embedded:
                errors.append(f"{relative}: fonts not embedded {not_embedded}")
    if errors:
        print("\n".join(errors))
        return 1
    print("delivery font policy PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
