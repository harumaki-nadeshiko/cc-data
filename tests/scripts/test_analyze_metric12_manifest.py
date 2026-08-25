#!/usr/bin/env python3

import importlib.util
import pathlib
import shutil
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MANIFEST = load("analyze_metric12_manifest",
                ROOT / "scripts/analyze_metric12_manifest.py")
RUN_TESTS = load("metric12_run_list_test_helpers",
                 ROOT / "tests/scripts/test_analyze_metric12_run_list.py")


class Metric12ManifestAuditorTest(unittest.TestCase):
    def setUp(self):
        self.helper = RUN_TESTS.Metric12RunListAnalyzerTest(methodName="runTest")
        self.helper.setUp()

    def tearDown(self):
        self.helper.tearDown()

    def legacy_runs(self):
        return self.helper.build_full_matrix()

    @staticmethod
    def v2(runs, m1_repetitions=(1, 2, 3), m2_repetitions=(1, 2, 3)):
        physical, uses = [], []
        for index, run in enumerate(runs):
            target, repetition, case, topology, profile = run["feature"].split("|")
            run_id = f"r{index}"
            physical.append({
                "id": run_id,
                "simulator_log_dir": run["simulator_log_dir"],
                "workload_output_dir": run["workload_output_dir"],
            })
            uses.append({
                "id": f"u{index}", "physical_run_id": run_id,
                "metric": target, "repetition": repetition, "case": case,
                "topology": topology, "profile": profile,
            })
        return {
            "schema_version": 2,
            "requirements": {
                "metric1": {"repetitions": list(m1_repetitions)},
                "metric2": {"repetitions": list(m2_repetitions)},
            },
            "runs": physical, "uses": uses,
        }

    def audit(self, manifest):
        return MANIFEST.Metric12ManifestAuditor(
            manifest, analyzer_class=RUN_TESTS.SyntheticAnalyzer).audit()

    def test_complete_72_legacy_compatibility(self):
        report = self.audit(self.legacy_runs())
        self.assertEqual(report["source_schema_version"], 1)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["coverage"]["expected_slot_count"], 72)
        self.assertEqual(report["coverage"]["counts"]["VALID"], 72)
        self.assertEqual(report["evidence_ledger"]["independent_evidence_count"], 72)
        self.assertIsNotNone(report["formal_analysis"])
        self.assertEqual(
            report["provisional"]["metric1"]["comparisons"][0]["outer_delta_ns"], 2.0)

    def test_legacy_guest_timer_profile_matrix_is_incomplete_not_pass(self):
        runs = self.legacy_runs()
        for run in runs:
            if run["feature"].startswith("target1|") and run["feature"].endswith("|ideal"):
                run["feature"] = run["feature"][:-len("ideal")] + "optimized"
        report = self.audit(runs)
        self.assertEqual(report["status"], "INCOMPLETE")
        self.assertEqual(report["coverage"]["counts"]["MISSING"], 3)
        self.assertEqual(report["coverage"]["counts"]["SUPPORT"], 3)
        self.assertEqual(report["provisional"]["metric1"]["comparisons"], [])
        self.assertIsNone(report["formal_analysis"])

    def test_independent_noncontiguous_repetitions(self):
        runs = self.legacy_runs()
        # Keep 3 metric1 repetitions and only two metric2 repetitions, then
        # rename their identities independently and non-contiguously.
        runs = [run for run in runs if not run["feature"].startswith("target2|3|")]
        manifest = self.v2(runs, ("m1-a", "m1-c", "m1-z"), (10, 30))
        for use in manifest["uses"]:
            old = use["repetition"]
            if use["metric"] == "target1":
                use["repetition"] = {"1": "m1-a", "2": "m1-c", "3": "m1-z"}[old]
            else:
                use["repetition"] = {"1": 10, "2": 30}[old]
        report = self.audit(manifest)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["formal_analysis"]["adapter"],
                         "independent-repetition-reduction")

    def test_missing_slot_is_incomplete(self):
        manifest = self.v2(self.legacy_runs())
        manifest["uses"].pop()
        report = self.audit(manifest)
        self.assertEqual(report["status"], "INCOMPLETE")
        self.assertEqual(report["exit_code"], 3)
        self.assertEqual(report["coverage"]["counts"]["MISSING"], 1)
        self.assertIsNone(report["formal_analysis"])

    def test_missing_repetition_is_incomplete_not_schema_error(self):
        runs = [run for run in self.legacy_runs()
                if not run["feature"].startswith("target1|3|")]
        manifest = self.v2(runs)
        report = self.audit(manifest)
        self.assertEqual(report["status"], "INCOMPLETE")
        self.assertEqual(report["coverage"]["counts"]["MISSING"], 3)

    def test_legacy_missing_repetition_is_not_inferred_complete(self):
        runs = [run for run in self.legacy_runs()
                if "|3|" not in run["feature"]]
        report = self.audit(runs)
        self.assertEqual(report["status"], "INCOMPLETE")
        self.assertEqual(report["coverage"]["counts"]["MISSING"], 24)
        self.assertIsNone(report["formal_analysis"])

    def test_duplicate_slot_is_invalid(self):
        manifest = self.v2(self.legacy_runs())
        duplicate = dict(manifest["uses"][0], id="duplicate-use")
        manifest["uses"].append(duplicate)
        report = self.audit(manifest)
        self.assertEqual(report["status"], "INVALID")
        self.assertEqual(report["coverage"]["counts"]["DUPLICATE"], 1)

    def test_reuse_is_forbidden_by_default(self):
        manifest = self.v2(self.legacy_runs())
        target2 = next(use for use in manifest["uses"] if use["metric"] == "target2")
        target2["physical_run_id"] = manifest["uses"][0]["physical_run_id"]
        report = self.audit(manifest)
        self.assertEqual(report["status"], "INVALID")
        self.assertTrue(any(issue["code"] == "REUSE_FORBIDDEN"
                            for slot in report["coverage"]["slots"]
                            for issue in slot["issues"]))

    def make_cross_metric_reuse(self):
        runs = self.legacy_runs()
        manifest = self.v2(runs)
        m1_use = next(use for use in manifest["uses"] if
                      use["metric"] == "target1" and use["repetition"] == "1" and
                      use["profile"] == "naive")
        m2_use = next(use for use in manifest["uses"] if
                      use["metric"] == "target2" and use["repetition"] == "1" and
                      use["case"] == "TC135" and use["profile"] == "naive")
        by_id = {run["id"]: run for run in manifest["runs"]}
        destination = by_id[m1_use["physical_run_id"]]
        source = by_id[m2_use["physical_run_id"]]
        # Enrich the metric1 physical directories with TC135 evidence, so both
        # views are independently valid while sharing physical identity.
        dst_sim = pathlib.Path(destination["simulator_log_dir"])
        src_sim = pathlib.Path(source["simulator_log_dir"])
        shutil.copytree(src_sim / "child_status_tc135", dst_sim / "child_status_tc135")
        shutil.copy2(src_sim / "verify_tc135.log", dst_sim / "verify_tc135.log")
        dst_work = pathlib.Path(destination["workload_output_dir"])
        src_work = pathlib.Path(source["workload_output_dir"])
        latency = (src_work / "node1/simout_n1").read_text()
        with (dst_work / "node1/simout_n1").open("a") as stream:
            stream.write(latency)
        m2_use["physical_run_id"] = m1_use["physical_run_id"]
        for use in (m1_use, m2_use):
            use["allow_reuse"] = True
            use["reuse_group"] = "same-experiment-two-metrics"
        return manifest, m1_use["physical_run_id"]

    def test_explicit_cross_metric_reuse_is_allowed_and_not_independent(self):
        manifest, shared_id = self.make_cross_metric_reuse()
        report = self.audit(manifest)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["coverage"]["counts"]["REUSE"], 2)
        self.assertEqual(report["evidence_ledger"]["weighted_contribution_count"], 72)
        self.assertEqual(report["evidence_ledger"]["independent_evidence_count"], 71)
        entry = next(row for row in report["evidence_ledger"]["physical_runs"]
                     if row["physical_run_id"] == shared_id)
        self.assertEqual(entry["contribution_count"], 2)

    def test_same_run_cannot_be_weighted_twice_in_one_aggregate(self):
        manifest = self.v2(self.legacy_runs())
        first, second = manifest["uses"][0], manifest["uses"][1]
        second["physical_run_id"] = first["physical_run_id"]
        for use in (first, second):
            use["allow_reuse"] = True
            use["reuse_group"] = "forbidden-same-aggregate"
        report = self.audit(manifest)
        self.assertEqual(report["status"], "INVALID")
        self.assertTrue(any(issue["code"] == "REUSE_WITHIN_AGGREGATE"
                            for slot in report["coverage"]["slots"]
                            for issue in slot["issues"]))

    def test_bad_evidence_does_not_block_other_ledger_entries(self):
        manifest = self.v2(self.legacy_runs())
        bad = manifest["runs"][0]
        pathlib.Path(bad["simulator_log_dir"]).joinpath("verify_tc131.log").write_text(
            ">>> TC131 FAILED <<<\n")
        report = self.audit(manifest)
        self.assertEqual(report["status"], "INVALID")
        self.assertEqual(report["coverage"]["counts"]["INVALID"], 1)
        self.assertEqual(report["coverage"]["counts"]["VALID"], 71)
        self.assertEqual(report["evidence_ledger"]["independent_evidence_count"], 71)


if __name__ == "__main__":
    unittest.main()
