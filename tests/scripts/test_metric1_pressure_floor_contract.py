#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/run_metric1_portable_calibrated_matrix.py"


def load_matrix():
    spec = importlib.util.spec_from_file_location("metric1_portable", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Metric1PressureFloorContractTest(unittest.TestCase):
    def test_non_integral_percentages_use_floor(self):
        matrix = load_matrix()
        self.assertEqual(matrix.portable_target_lines(101), 66191)
        self.assertEqual(matrix.portable_target_lines(137), 89784)
        self.assertEqual(matrix.portable_target_lines(199), 130416)

    def test_all_metric1_entrypoints_share_floor_helper(self):
        for relative in (
            "scripts/run_metric1_data68_parallel.py",
            "scripts/run_metric1_delta_matrix.py",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("matrix.portable_target_lines(args.pressure_pct)", source)
            self.assertNotIn("pressure_pct % 100", source)
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("integral target footprint", source)


if __name__ == "__main__":
    unittest.main()
