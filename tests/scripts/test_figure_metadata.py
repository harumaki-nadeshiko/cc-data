#!/usr/bin/env python3

import importlib.util
import hashlib
import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]


def load_script(name):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GENERATOR = load_script("generate_delivery_figures.py")
VALIDATOR = load_script("validate_delivery_figures.py")


class FigureMetadataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inventory = json.loads((ROOT / "docs/design/figures/figure_inventory.json").read_text())

    def test_every_chart_has_complete_valid_lineage(self):
        self.assertEqual({entry["name"] for entry in self.inventory["charts"]}, set(GENERATOR.CHART_STEMS))
        for chart in self.inventory["charts"]:
            checks = VALIDATOR.validate_chart_metadata(chart)
            self.assertTrue(all(checks.values()), (chart["name"], checks))

    def test_metric1_separates_capacity_and_outer_latency_sources(self):
        chart = next(row for row in self.inventory["charts"]
                     if row["name"] == "ubcc-metric1-capacity-latency")
        self.assertEqual(chart["source_artifacts"], [GENERATOR.METRIC_REPORT,
                                                     GENERATOR.METRIC1_OUTER_SUMMARY])
        self.assertNotIn("guest_delta", json.dumps(chart))
        self.assertEqual(chart["evidence_sets"][0]["physical_runs"], 6)
        self.assertEqual(chart["evidence_sets"][1]["physical_arms"], 6)
        self.assertEqual(
            chart["cross_set_weighting"],
            "none; the two evidence sets serve independent Metric1 subcontracts")
        report = GENERATOR.require_json(GENERATOR.METRIC_REPORT)
        outer = GENERATOR.require_json(GENERATOR.METRIC1_OUTER_SUMMARY)
        lineage = next(row for row in GENERATOR.chart_lineage(
            report, outer, GENERATOR.require_json(GENERATOR.QUALIFICATION_MATRIX))
                       if row["name"] == chart["name"])
        self.assertEqual(lineage, chart)

    def test_diagrams_have_document_references(self):
        self.assertEqual({entry["name"] for entry in self.inventory["diagrams"]}, set(GENERATOR.DIAGRAM_STEMS))
        for diagram in self.inventory["diagrams"]:
            self.assertTrue(all(VALIDATOR.validate_diagram_metadata(diagram).values()))

    def test_protocol_selection_figures_use_square_boxes_and_explicit_anchors(self):
        stems = {
            "ubcc-protocol-authority-comparison", "ubcc-path-central-vs-direct",
            "ubcc-metadata-fanout-scaling", "ubcc-inner-chi-outer-boundary",
        }
        self.assertTrue(stems.issubset(set(GENERATOR.DIAGRAM_STEMS)))
        for stem in stems:
            report = VALIDATOR.inspect_drawio(
                ROOT / "docs/design/figures" / f"{stem}.drawio", stem)
            self.assertTrue(report["checks"]["square_or_minimal_radius_boxes"], stem)
            self.assertTrue(report["checks"]["reviewed_connector_anchors"], stem)

    def test_math_figures_declare_stix_math(self):
        for stem in ("ubcc-path-central-vs-direct", "ubcc-metadata-fanout-scaling"):
            text = (ROOT / "docs/design/figures" / f"{stem}.drawio").read_text()
            self.assertIn("fontFamily=STIX Two Math", text)

    def test_math_font_asset_and_license_are_pinned(self):
        font = ROOT / "docs/fonts/stix-math/STIXTwoMath-Regular.ttf"
        license_path = ROOT / "docs/fonts/stix-math/OFL.txt"
        self.assertEqual(hashlib.sha256(font.read_bytes()).hexdigest(),
                         "562551b15b836e6e01d1b7350909baf3c8c8d83260c1190fbf4544333e6936de")
        self.assertIn("SIL OPEN FONT LICENSE",
                      license_path.read_text(errors="replace"))


if __name__ == "__main__":
    unittest.main()
