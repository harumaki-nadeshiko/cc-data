#!/usr/bin/env python3

import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/analyze_metric3_paired.py"
SPEC = importlib.util.spec_from_file_location("analyze_metric3_paired", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Metric3PairedAnalyzerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def add_arm(self, pair, tc, arm, order, offset=0.0):
        pair_id = f"r{pair:02d}_tc{tc}"
        log_dir = self.root / "cases" / pair_id / arm
        metrics = {}
        for index, name in enumerate(MODULE.METRIC_REGISTRY[tc]):
            base = float(tc - 200 + index)
            value = base + (2.0 + offset if arm == "ha-vi" else 0.0)
            metrics[name] = {
                "ticks_per_operation": value,
                "counter_frequency_hz": 1000000000,
                "ns_per_operation": value,
            }
        result = {
            "pair": pair, "pair_id": pair_id, "tc": tc, "order": order,
            "arm": arm, "return_code": 0, "status": "PASS", "metrics": metrics,
        }
        self.write(log_dir / "result.json", json.dumps(result))
        self.write(log_dir / f"verify_tc{tc}.log", f"details\n>>> TC{tc} PASSED <<<\n")
        child = log_dir / f"child_status_tc{tc}"
        for name in MODULE.expected_exit_names(tc):
            self.write(child / name, "0\n")
        profile = ("ha_endpoint_profile=ubcc clear_profile=lossless-oneway reliability=eventual-delivery"
                   if arm == "ourcc" else
                   "ha_endpoint_profile=ha-vi clear_profile=ack reliability=clear-ack")
        for node in (0, 1):
            self.write(log_dir / f"gem5_tc{tc}_node{node}" / "stderr.log",
                       f"[EPBACKEND-PROFILE] node={node} {profile}\n")
            if arm == "ha-vi":
                self.write(log_dir / f"ubio_tc{tc}_n{node}_s0" / "stdout.log",
                           f"[UBIO-HA-MANIFEST] controller=ha-vi node={node} socket=0\n")
        return log_dir

    def build_historical(self, pairs=1):
        manifest = {
            "schema_version": 1, "benchmark": "fixture", "pairs": pairs,
            "testcases": list(MODULE.ALL_TCS), "fingerprint": {"main_commit": "abc"},
        }
        self.write(self.root / "manifest.json", json.dumps(manifest))
        for pair in range(1, pairs + 1):
            for index, tc in enumerate(MODULE.ALL_TCS):
                order = "AB" if (pair - 1 + index) % 2 == 0 else "BA"
                for arm in MODULE.ARMS:
                    self.add_arm(pair, tc, arm, order)
        return self.root / "manifest.json"

    def analyze(self, weights=None):
        return MODULE.analyze(self.root / "manifest.json", self.root, weights)

    def write_weights(self, status="FROZEN", threshold=0.0, aggregates=None):
        if aggregates is None:
            aggregates = [
                {"name": "core_equal_weight", "scope": "core",
                 "threshold_ticks": threshold, "comparison": "GT",
                 "weights": MODULE.FROZEN_AGGREGATE_WEIGHTS["core_equal_weight"]},
                {"name": "representative_equal_weight", "scope": "representative",
                 "threshold_ticks": threshold, "comparison": "GT",
                 "weights": MODULE.FROZEN_AGGREGATE_WEIGHTS["representative_equal_weight"]},
            ]
        path = self.root / f"weights-{status}-{len(list(self.root.glob('weights-*')))}.json"
        self.write(path, json.dumps({
            "schema_version": 2, "status": status,
            "contract_authoritative": status == "FROZEN",
            "reference_model_scope": "fixture executable reference model",
            "aggregates": aggregates,
        }))
        return path

    def test_complete_historical_fixture_is_descriptive_and_manifest_only(self):
        self.build_historical(pairs=2)
        report, samples, _, _, code = self.analyze()
        self.assertEqual(code, 0)
        self.assertEqual(report["overall_status"], "DESCRIPTIVE/NON-AUTHORITATIVE")
        self.assertTrue(report["coverage"]["complete"])
        self.assertEqual(len(samples), 24)
        self.assertEqual(report["correctness"]["fingerprint_status_counts"]["MANIFEST_ONLY"], 32)
        self.assertIsNone(report["inference"]["authoritative_ci"])
        self.assertIsNone(report["inference"]["authoritative_pvalue"])

    def test_missing_arm_is_incomplete_without_cartesian_pairing(self):
        self.build_historical()
        target = self.root / "cases/r01_tc228/ha-vi/result.json"
        target.unlink()
        report, samples, _, _, code = self.analyze()
        self.assertEqual(code, 3)
        self.assertEqual(report["overall_status"], "INCOMPLETE")
        self.assertEqual(report["coverage"]["complete_pair_slots"], 7)
        self.assertEqual(len([row for row in samples if row["tc"] == 228]), 0)

    def test_unequal_pair_counts_are_incomplete(self):
        self.build_historical(pairs=2)
        for arm in MODULE.ARMS:
            path = self.root / "cases/r02_tc230" / arm / "result.json"
            path.unlink()
        report, _, _, _, code = self.analyze()
        self.assertEqual(code, 3)
        self.assertFalse(report["coverage"]["balanced_repeat_counts"])
        self.assertEqual(report["coverage"]["complete_pairs_by_tc"]["TC230"], 1)

    def test_duplicate_arm_is_invalid(self):
        self.build_historical()
        manifest = json.loads((self.root / "manifest.json").read_text())
        manifest["schema_version"] = 2
        result = self.root / "cases/r01_tc228/ourcc/result.json"
        records = []
        for path in sorted((self.root / "cases").glob("*/**/result.json")):
            records.append({"result": str(path), "log_dir": str(path.parent)})
        records.append({"result": str(result), "log_dir": str(result.parent)})
        manifest["records"] = records
        explicit = self.root / "explicit.json"
        self.write(explicit, json.dumps(manifest))
        report, _, _, _, code = MODULE.analyze(explicit, None, None)
        self.assertEqual(code, 2)
        self.assertTrue(any("duplicate arm" in error for error in report["errors"]))

    def test_order_imbalance_is_diagnostic_not_invalid(self):
        self.build_historical(pairs=2)
        for result_path in (self.root / "cases").glob("*/**/result.json"):
            result = json.loads(result_path.read_text())
            result["order"] = "AB"
            result_path.write_text(json.dumps(result))
        report, _, _, _, code = self.analyze()
        self.assertEqual(code, 0)
        self.assertFalse(report["order_diagnostics"]["balanced_overall"])
        self.assertTrue(report["order_diagnostics"]["diagnostic_only"])

    def test_unfrozen_weights_never_create_contract_pass(self):
        self.build_historical()
        weights = self.write_weights(status="UNFROZEN", threshold=1000.0)
        report, _, gates, _, code = self.analyze(weights)
        self.assertEqual(code, 0)
        self.assertEqual(report["overall_status"], "DESCRIPTIVE/NON-AUTHORITATIVE")
        self.assertEqual(gates[0]["status"], "DESCRIPTIVE/NON-AUTHORITATIVE")
        self.assertFalse(gates[0]["contract_authoritative"])

    def test_frozen_weights_pass_and_fail_exit_codes(self):
        self.build_historical()
        passed, _, _, _, pass_code = self.analyze(self.write_weights(threshold=0.0))
        aggregates = [
            {"name": "core_equal_weight", "scope": "core",
             "threshold_ticks": 0.0, "comparison": "GT",
             "weights": MODULE.FROZEN_AGGREGATE_WEIGHTS["core_equal_weight"]},
            {"name": "representative_equal_weight", "scope": "representative",
             "threshold_ticks": 3.0, "comparison": "GT",
             "weights": MODULE.FROZEN_AGGREGATE_WEIGHTS["representative_equal_weight"]},
        ]
        failed, _, _, _, fail_code = self.analyze(
            self.write_weights(aggregates=aggregates))
        self.assertEqual((passed["overall_status"], pass_code),
                         (MODULE.REFERENCE_MODEL_PASS, 0))
        self.assertEqual((failed["overall_status"], fail_code),
                         ("INVALID", 2))

    def test_authoritative_schema_rejects_noncontract_aggregates(self):
        self.build_historical()
        common = {"TC228_remote_read": 0.5, "TC229_ownership_handoff": 0.5}
        aggregates = [
            {"name": "business-a", "scope": "core", "threshold_ticks": 0, "weights": common},
            {"name": "business-b", "scope": "core", "threshold_ticks": 0, "weights": common},
        ]
        report, _, _, _, code = self.analyze(self.write_weights(aggregates=aggregates))
        self.assertEqual(code, 2)
        self.assertTrue(any("exactly core_equal_weight" in item
                            for item in report["errors"]))

    def test_equal_weight_descriptive_reports_arm_means_and_reduction(self):
        self.build_historical()
        report, _, _, _, code = self.analyze()
        self.assertEqual(code, 0)
        aggregate = report["aggregates"][0]
        self.assertGreater(aggregate["ha_vi_ticks_per_operation"],
                           aggregate["ourcc_ticks_per_operation"])
        self.assertGreater(aggregate["ourcc_reduction_pct"], 0)
        self.assertEqual(report["core_weight_sensitivity"]["contract_verdict"],
                         "DESCRIPTIVE/NON-AUTHORITATIVE")

    def test_weight_validation_and_representative_scope(self):
        self.build_historical()
        bad = [{"name": "bad", "scope": "core", "threshold_ticks": 0,
                "weights": {"TC232_hot_key_read": 1.0}}]
        report, _, _, _, code = self.analyze(self.write_weights(aggregates=bad))
        self.assertEqual(code, 2)
        self.assertTrue(any("representative metrics require scope=representative" in item
                            for item in report["errors"]))

    def test_frozen_primary_values_and_tc232_composite(self):
        self.build_historical()
        report, _, gates, _, code = self.analyze(self.write_weights())
        self.assertEqual(code, 0)
        self.assertEqual(report["overall_status"], MODULE.REFERENCE_MODEL_PASS)
        self.assertTrue(report["contract_authoritative"])
        self.assertEqual(report["reference_model_scope"],
                         "fixture executable reference model")
        representative = next(row for row in report["aggregates"]
                              if row["name"] == "representative_equal_weight")
        self.assertEqual(representative["verdict"], MODULE.REFERENCE_MODEL_PASS)
        weights = report["weights"]["aggregates"][1]["weights"]
        self.assertAlmostEqual(weights["TC232_hot_key_read"], 2 / 15)
        self.assertAlmostEqual(weights["TC232_hot_key_write"], 1 / 15)
        self.assertIn("TC233_producer_consumer_service", weights)
        self.assertNotIn("TC233_producer_consumer_load", weights)
        self.assertEqual(report["primary_value_definitions"]["TC232"]["formula"],
                         "2/3 * hot_key_read + 1/3 * hot_key_write")
        self.assertTrue(all(row["contract_authoritative"] for row in gates))

    def test_strict_comparison_rejects_tie(self):
        self.build_historical()
        for path in (self.root / "cases").glob("*/ha-vi/result.json"):
            result = json.loads(path.read_text())
            for metric in result["metrics"].values():
                metric["ticks_per_operation"] -= 2.0
            path.write_text(json.dumps(result))
        report, _, _, _, code = self.analyze(self.write_weights())
        self.assertEqual(code, 1)
        self.assertEqual(report["overall_status"],
                         "FAIL (EXECUTABLE-REFERENCE-MODEL SCOPE)")


if __name__ == "__main__":
    unittest.main()
