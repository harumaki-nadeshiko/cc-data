#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import sys
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

    def test_e2e_verifier_uses_the_same_floor_relation(self):
        source = (ROOT / "tests/e2e/test_e2e.py").read_text(
            encoding="utf-8")
        self.assertIn(
            "expected_target = naive_capacity * pressure_pct // 100", source)
        self.assertIn("total_unique != expected_target", source)
        self.assertNotIn(
            "total_unique * 100 != naive_capacity * pressure_pct", source)

    def test_e2e_verifier_accepts_floor_and_rejects_ceil(self):
        sys.path.insert(0, str(ROOT / "tests/e2e"))
        try:
            import test_e2e
        finally:
            sys.path.pop(0)

        def verify(total):
            pressure = total - 32
            lines = [
                "[E2E_META] node=0 test=TC142",
                "[TOPOLOGY] node=0 planes=1",
                ("[PORTABLE-PRESSURE] node=0 planes=1 hot_lines=32 "
                 f"pressure_lines={pressure} total_unique_lines={total} "
                 "naive_capacity_lines=65536 target_footprint_lines=0 "
                 "pressure_level_pct=101 batches=32"),
            ]
            return test_e2e.verify_portable_large_workload(
                142, [], lines, (), 1, "latency", "service", "end", 1, 32)

        accepted = verify(66191)
        self.assertIn("expected 1 READ_VAL", accepted[1])
        rejected = verify(66192)
        self.assertIn("floor(65536*101/100)=66191", rejected[1])


if __name__ == "__main__":
    unittest.main()
