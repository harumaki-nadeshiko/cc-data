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


class GenerateDeliveryFiguresPublicationTest(unittest.TestCase):
    def publication(self):
        observation = {
            "spill_outer_mean_ns": 12.0,
            "ideal_outer_mean_ns": 10.0,
            "spill_resident_capacity": 90,
            "ideal_resident_capacity": 1000,
        }
        return {
            "schema_version": 1,
            "metric_definitions_version": "metric123-publication-v1",
            "metric1": {"capacity_ratio": 1.6, "capacity_increase_pct": 60.0,
                        "outer_delta_mean_ns": 2.0, "observations": [observation]},
            "metric2": {"applicable_equal_weight_mean_reduction_pct": 20.0,
                        "cases": [{"case": "TC135", "optimized_reduction_pct": 20.0,
                                   "applicable": True}]},
            "metric3": {"groups": [{"scope": "core",
                        "ourcc_ticks_per_operation": 8.0,
                        "ha_vi_ticks_per_operation": 10.0}],
                        "per_testcase": [{"case": "TC228",
                        "ourcc_reduction_pct": 20.0}]},
        }

    def test_publication_schema_is_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.json"
            path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "metric123-publication-v1"):
                MOD.publication_data(path)

    def test_publication_charts_generate_expected_files(self):
        try:
            import matplotlib  # noqa: F401
        except (ImportError, OSError):
            self.skipTest("Matplotlib not installed")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "publication.json", root / "figures"
            source.write_text(json.dumps(self.publication()), encoding="utf-8")
            output.mkdir()
            previous = MOD.OUT
            try:
                MOD.OUT = output
                MOD.publication_charts(source)
            finally:
                MOD.OUT = previous
            for stem in ("ubcc-metric1-capacity-latency",
                         "ubcc-metric2-reductions",
                         "ubcc-ha-vi-comparison",
                         "ubcc-metric3-per-tc-reductions"):
                self.assertTrue((output / f"{stem}.png").is_file())
                self.assertTrue((output / f"{stem}.svg").is_file())


if __name__ == "__main__":
    unittest.main()
