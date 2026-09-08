import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/generate_delivery_figures.py"
SPEC = importlib.util.spec_from_file_location("generate_delivery_figures", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class GenerateDeliveryFiguresTest(unittest.TestCase):
    def test_publication_source_reconstructs_article_chart_inputs(self):
        report, outer, preview = MOD.publication_sources(ROOT / MOD.PUBLICATION_DATA)
        self.assertAlmostEqual(report["metric1"]["capacity_ratio"], 1.5150909423828125)
        self.assertAlmostEqual(outer["delta_mean_ns"], 10.53457816755224)
        self.assertEqual(len(report["metric2"]["cases"]), 7)
        self.assertEqual(len(preview["metric1_matrix"]), 10)
        self.assertEqual(len(preview["metric3_per_tc"]), 8)

    def test_equivalent_publication_path_produces_same_lineage_values(self):
        source = json.loads((ROOT / MOD.PUBLICATION_DATA).read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            copy = Path(temporary) / "publication.json"
            copy.write_text(json.dumps(source), encoding="utf-8")
            left = MOD.publication_sources(ROOT / MOD.PUBLICATION_DATA)
            right = MOD.publication_sources(copy)
        self.assertEqual(left, right)

    def test_release_charts_have_one_performance_data_source(self):
        report, outer, preview = MOD.publication_sources(ROOT / MOD.PUBLICATION_DATA)
        matrix = MOD.require_json(MOD.QUALIFICATION_MATRIX)
        charts = MOD.chart_lineage(report, outer, matrix, preview)
        for chart in charts:
            if chart["name"] == "ubcc-q1-q5-qualification":
                continue
            self.assertEqual(chart["source_artifacts"], [MOD.PUBLICATION_DATA])


if __name__ == "__main__":
    unittest.main()
