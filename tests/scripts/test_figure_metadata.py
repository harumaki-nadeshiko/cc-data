#!/usr/bin/env python3

import importlib.util
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


if __name__ == "__main__":
    unittest.main()
