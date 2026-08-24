#!/usr/bin/env python3
"""Contract tests for the independent Clear-semantics matrix runner."""

import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "clear_perf", ROOT / "scripts/run_lossless_clear_perf_matrix.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ClearPerfContractTest(unittest.TestCase):
    def test_fixed_matrix_and_alternation(self):
        with tempfile.TemporaryDirectory(dir=str(ROOT / "results")) as temporary:
            root = pathlib.Path(temporary)
            manifest = MODULE.make_manifest(root, {"runtime": {}})
        self.assertEqual(manifest["physical_runs"], 48)
        self.assertEqual(len(manifest["samples"]), 24)
        self.assertEqual(len({arm["run_id"] for sample in manifest["samples"]
                              for arm in sample["arms"].values()}), 48)
        self.assertEqual(MODULE.planned_order(1, 131), "AB")
        self.assertEqual(MODULE.planned_order(1, 135), "BA")
        self.assertEqual(MODULE.planned_order(2, 131), "BA")
        self.assertEqual(manifest["cpu_policy"]["tc131_exclusive_cpuset"], "0-31")
        self.assertEqual(manifest["cpu_policy"]["smaller_case_disjoint_lanes"],
                         ["0-7", "8-15", "16-23", "24-31"])

    def test_primary_timer_and_latency_metrics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            (root / "simout_tc131_node0.log").write_text(
                "[GUEST-TIMER] node=0 phase=post_pressure_catalog_reuse "
                "operations=2 counter_ticks=20 counter_frequency_hz=1000000000\n")
            (root / "simout_tc131_node1.log").write_text(
                "[GUEST-TIMER] node=1 phase=post_pressure_catalog_reuse "
                "operations=3 counter_ticks=45 counter_frequency_hz=1000000000\n")
            metric = MODULE.primary_metric(root, 131)
            self.assertEqual(metric["ticks_per_operation"], 13)
            self.assertEqual(metric["ns_per_operation"], 13)
            (root / "simout_tc135_node1.log").write_text(
                "[PERF-LATENCY] node=1 phase=preserved_sharer_first_load "
                "samples=24 min=1 p50=2 p95=3 p99=4 max=5 mean=12 "
                "counter_frequency_hz=1000000000 source=cntvct_el0 unit=ticks\n")
            metric = MODULE.primary_metric(root, 135)
            self.assertEqual(metric["ticks_per_operation"], 12)
            self.assertEqual(metric["ns_per_operation"], 12)

    def test_descriptive_paired_analysis(self):
        with tempfile.TemporaryDirectory(dir=str(ROOT / "results")) as temporary:
            root = pathlib.Path(temporary)
            manifest = MODULE.make_manifest(root, {"runtime": {}})
            MODULE.atomic_json(root / "manifest.json", manifest)
            for sample in manifest["samples"]:
                for arm, value in (("ack", 110.0), ("lossless-oneway", 100.0)):
                    result = root / sample["arms"][arm]["result"]
                    MODULE.atomic_json(result, {
                        "status": "PASS", "workload_elf_sha256": "same",
                        "primary_metric": {
                            "phase": sample["metric_phase"],
                            "ticks_per_operation": value,
                            "counter_frequency_hz": 1000000000,
                            "ns_per_operation": value,
                        },
                    })
            analysis = MODULE.analyze(root, manifest)
            self.assertTrue(analysis["analysis_complete"])
            self.assertEqual(len(analysis["per_tc"]), 8)
            self.assertEqual(
                analysis["overall_equal_weight_summary"]
                ["equal_weight_mean_of_tc_delta_ns_per_operation"], 10.0)
            markdown = (root / "analysis.md").read_text()
            self.assertIn("no contract verdict", markdown)
            self.assertNotIn("t_test", analysis)
            self.assertNotIn("p_value", analysis)

    def test_launch_identity_uses_fields_recorded_by_runner_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            (root / "launch_manifest.txt").write_text("\n".join((
                "HA_PROFILE=ubcc",
                "OURCC_CLEAR_PROFILE=ack",
                "EP_PERF_PROFILE=optimized",
                "UBCC_POLICY=spill",
                "EP_CPU_MODEL=o3",
                "EP_SEQUENCER_MAX_OUTSTANDING=16",
                "EP_LINK_LATENCY_PS=2500",
                "EP_SYNC_INTERVAL_PS=2500",
                "EP_GEM5_OPTS=--silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1",
            )) + "\n")
            result = MODULE.verify_launch_identity(root, "ack")
            self.assertTrue(result["valid"])


if __name__ == "__main__":
    unittest.main()
