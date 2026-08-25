#!/usr/bin/env python3

import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/generate_metric123_report.py"
SPEC = importlib.util.spec_from_file_location("generate_metric123_report", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class GenerateMetric123ReportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write_json(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value))
        return path

    @staticmethod
    def target12():
        return {
            "schema_version": 1,
            "target1": {"statistics": {
                "capacity_ratio": {"mean": 1.6, "cv_pct": 0.25},
                "guest_delta_ns_per_operation": {"mean": -800.0, "cv_pct": 0.0},
                "guest_delta_cycles": {"mean": -1600.0, "cv_pct": 0.0},
                "pass": True,
            }},
            "target2": {
                "statistics": {
                    "pass": True,
                    "applicable_cases": ["TC135"],
                    "applicable_set_stable": True,
                    "equal_weight_mean_reduction_pct": {"mean": 20.0, "cv_pct": 1.0},
                },
                "case_statistics": {"TC135": {
                    "profile_mean_ns": {
                        "naive": {"mean": 1000.0},
                        "spill-noopt": {"mean": 900.0},
                        "optimized": {"mean": 800.0},
                    },
                    "optimized_reduction_pct": {"mean": 20.0, "cv_pct": 1.0},
                }},
            },
        }

    @staticmethod
    def corrected_metric1():
        return {
            "schema_version": 1,
            "definition": "mean(all completed spill Outer) - mean(all completed ideal Outer)",
            "status": "PASS",
            "complete_repeats": 3,
            "delta_mean_ns": 10.5,
            "delta_mean_cycles_2ghz": 21.0,
            "delta_stdev_ns": 0.5,
            "repeats": {
                str(index): {"delta_outer_mean_ns": 10.5,
                             "delta_outer_mean_cycles_2ghz": 21.0}
                for index in range(1, 4)
            },
        }

    def test_legacy_guest_delta_is_not_formal_metric1(self):
        target12 = self.write_json("target12.json", self.target12())
        report = MOD.build_report(target12, None, None, "test")
        self.assertEqual(report["metric1"]["status"], "INCOMPLETE")
        self.assertIsNone(report["metric1"]["outer_delta_ns"])
        self.assertNotIn("guest_delta_ns_per_operation", report["metric1"])
        self.assertFalse(report["metric12_overall_pass"])

    def test_corrected_outer_delta_drives_metric1(self):
        target12 = self.write_json("target12.json", self.target12())
        metric1 = self.write_json("metric1.json", self.corrected_metric1())
        report = MOD.build_report(target12, metric1, None, "test")
        self.assertEqual(report["metric1"]["status"], "PASS")
        self.assertEqual(report["metric1"]["outer_delta_ns"], 10.5)
        self.assertEqual(report["metric1"]["outer_delta_cycles"], 21.0)
        self.assertAlmostEqual(report["metric1"]["latency_delta_cv_pct"],
                               0.5 / 10.5 * 100.0)
        self.assertTrue(report["metric12_overall_pass"])
        self.assertIn("spill-512K - spill-IdealDir", MOD.render_markdown(report))
        self.assertNotIn("guest_delta", MOD.compact_text(report))
        self.assertEqual(MOD.tsv_rows(report)[2][1], "outer_delta")

    def test_wrong_metric1_definition_is_rejected(self):
        metric1 = self.corrected_metric1()
        metric1["definition"] = "mean(spill Outer) - mean(naive Outer)"
        path = self.write_json("metric1.json", metric1)
        with self.assertRaises(MOD.InputError):
            MOD.parse_corrected_metric1(metric1, path)


if __name__ == "__main__":
    unittest.main()
