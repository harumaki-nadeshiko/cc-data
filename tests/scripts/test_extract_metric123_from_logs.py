#!/usr/bin/env python3

import gzip
import importlib.util
import json
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/extract_metric123_from_logs.py"
SPEC = importlib.util.spec_from_file_location("extract_metric123_from_logs", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class ExtractMetric123Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, path, text, gz=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        if gz:
            with gzip.open(path, "wt") as stream: stream.write(text)
        else:
            path.write_text(text)

    def correctness(self, sim, tc, topology="2n1s"):
        self.write(sim / f"verify_tc{tc}.log", f">>> TC{tc} PASSED <<<\n")
        nodes, sockets = MOD.topology_size(topology)
        names = {f"gem5_node{n}.exit" for n in range(nodes)} | {f"ubio_n{n}_s{s}.exit" for n in range(nodes) for s in range(sockets)} | {"networksim.exit"}
        for name in names: self.write(sim / f"child_status_tc{tc}" / name, "0\n")

    @staticmethod
    def timer(node, phase, ticks=1000, count=10):
        return f"[GUEST-TIMER] node={node} phase={phase} operations={count} counter_ticks={ticks} counter_frequency_hz=1000000000 source=arm_cntvct_el0 unit=counter_ticks\n"

    @staticmethod
    def latency(node, phase, samples, mean=1000):
        return f"[PERF-LATENCY] node={node} phase={phase} samples={samples} min=100 p50=500 p95=1500 p99=1800 max=2000 mean={mean} counter_frequency_hz=1000000000 source=arm_cntvct_el0 unit=counter_ticks\n"

    def manifest(self, runs, requirements, policy="strict"):
        path = self.root / "manifest.json"
        path.write_text(json.dumps({"schema_version": 1, "correctness_policy": policy,
                                    "requirements": requirements, "runs": runs}))
        return path

    def test_metric1_separate_dirs_and_gz_flat_simout(self):
        runs = []
        for profile, policy, capacity, exact, ticks in (("naive", "naive", 100, None, 1000),
                                                        ("spill-noopt", "spill", 100, 160, 1050),
                                                        ("optimized", "spill", 100, 160, 900)):
            sim, out = self.root / profile / "simulator", self.root / profile / "simout"
            self.correctness(sim, 131, "8n1s")
            stats = "" if exact is None else f'[UBCC-STATS] {{"residentCapacity":{capacity},"h64ExactLiveKnown":1,"h64ExactLiveCount":{exact}}}\n'
            self.write(sim / "nested/ubio_tc131_n0_s0/stdout.log.gz",
                       f"[UBCC-STATE] capacity={capacity} policy={policy}\n{stats}", gz=True)
            self.write(sim / "nested/ubio_tc131_n1_s0/stdout.log",
                       "[UBCC-STATE] capacity=999999 policy=naive\n")
            for node in (1, 2): self.write(out / f"simout_tc131_node{node}.log.gz", self.timer(node, "post_pressure_catalog_reuse", ticks), gz=True)
            runs.append({"id": profile, "metric": 1, "tc": 131, "repetition": "r1", "topology": "8n1s",
                         "profile": profile, "simulator_log_dir": str(sim.relative_to(self.root)), "simout_dir": str(out.relative_to(self.root))})
        report, resolved, _, _, issues, code = MOD.analyze(self.manifest(runs, {"metric1": {"repetitions": ["r1"]}, "metric2": {"repetitions": [], "testcases": []}, "metric3": {"pairs": [], "testcases": []}}), self.root / "output")
        self.assertEqual(report["metric1"]["status"], "PASS")
        self.assertAlmostEqual(report["metric1"]["comparisons"][0]["capacity_ratio"], 1.6)
        self.assertFalse(issues)
        self.assertTrue(all(item["metrics"]["capacity"]["resident_capacity"] == 100
                            for item in resolved))

    def test_metric2_duplicate_and_missing_phase_are_clear_errors(self):
        phase, topology, node, samples = MOD.M2[135]
        runs = []
        for profile in MOD.PROFILES:
            sim, out = self.root / profile / "sim", self.root / profile / "out"
            self.correctness(sim, 135, topology)
            text = "" if profile == "naive" else self.latency(node, phase, samples)
            if profile == "optimized": text += self.latency(node, phase, samples)
            self.write(out / f"node{node}/simout_n{node}", text)
            runs.append({"id": profile, "metric": 2, "tc": 135, "repetition": 1, "topology": topology,
                         "profile": profile, "simulator_log_dir": str(sim), "simout_dir": str(out)})
        report, _, _, _, issues, code = MOD.analyze(self.manifest(runs, {"metric1": {"repetitions": []}, "metric2": {"repetitions": [1], "testcases": [135]}, "metric3": {"pairs": [], "testcases": []}}), self.root / "output")
        self.assertEqual(code, 2)
        messages = " ".join(x["message"] for x in issues)
        self.assertIn("got 0", messages); self.assertIn("got 2", messages)

    def test_metric3_incomplete_pair_never_cross_pairs(self):
        runs = []
        for pair, arm in (("p1", "ourcc"), ("p2", "ha-vi")):
            sim, out = self.root / pair / arm / "sim", self.root / pair / arm / "out"
            self.correctness(sim, 228)
            self.write(out / "simout_n0", self.timer(0, "topology_remote_read", 1000 if arm == "ourcc" else 1200))
            runs.append({"id": pair+arm, "metric": 3, "tc": 228, "repetition": 1, "topology": "2n1s",
                         "arm": arm, "pair": pair, "order": "AB", "simulator_log_dir": str(sim), "simout_dir": str(out)})
        report, _, _, _, _, code = MOD.analyze(self.manifest(runs, {"metric1": {"repetitions": []}, "metric2": {"repetitions": [], "testcases": []}, "metric3": {"pairs": ["p1", "p2"], "testcases": [228]}}), self.root / "output")
        self.assertEqual(code, 3)
        self.assertEqual(report["metric3"]["samples"], [])
        self.assertEqual(len(report["metric3"]["incomplete_pairs"]), 2)

    def test_optional_correctness_allows_absent_but_rejects_present_failure(self):
        phase, topology, node, samples = MOD.M2[135]
        out = self.root / "out"; sim = self.root / "sim"; sim.mkdir()
        self.write(out / f"simout_tc135_node{node}.log", self.latency(node, phase, samples))
        run = {"id": "optional", "metric": 2, "tc": 135, "repetition": 1, "topology": topology,
               "profile": "naive", "simulator_log_dir": str(sim), "simout_dir": str(out)}
        extracted = MOD.extract_run(run, self.root, "optional")
        self.assertEqual(extracted["correctness"]["status"], "NOT_PRESENT_OPTIONAL")
        self.write(sim / "verify_tc135.log", ">>> TC135 FAILED <<<\n")
        with self.assertRaisesRegex(MOD.ExtractError, "not PASS"):
            MOD.extract_run(run, self.root, "optional")

    def test_duplicate_simout_layout_is_rejected(self):
        out = self.root / "out"
        self.write(out / "node0/simout_n0", self.timer(0, "x"))
        self.write(out / "simout_n0", self.timer(0, "x"))
        with self.assertRaisesRegex(MOD.ExtractError, "duplicate simout"):
            MOD.discover_simouts(out, 228)

    def test_metric3_latency_uses_sample_weighted_mean(self):
        runs = []
        for arm, means in (("ourcc", (100, 300)), ("ha-vi", (200, 500))):
            sim, out = self.root / arm / "sim", self.root / arm / "out"
            self.correctness(sim, 233)
            self.write(out / "simout_n0",
                       self.latency(0, "producer_consumer_load", 1, means[0]) +
                       self.timer(0, "producer_consumer_service", 1000))
            self.write(out / "simout_n1",
                       self.latency(1, "producer_consumer_load", 3, means[1]) +
                       self.timer(1, "producer_consumer_service", 1000))
            runs.append({"id": arm, "metric": 3, "tc": 233, "repetition": 1,
                         "topology": "2n1s", "arm": arm, "pair": "p1", "order": "AB",
                         "simulator_log_dir": str(sim), "simout_dir": str(out)})
        extracted = [MOD.extract_run(run, self.root, "strict") for run in runs]
        self.assertEqual(extracted[0]["metrics"]["producer_consumer_load"]
                         ["ticks_per_operation"], 250)
        self.assertEqual(extracted[1]["metrics"]["producer_consumer_load"]
                         ["ticks_per_operation"], 425)

    def test_metric3_conflicting_order_is_invalid(self):
        runs = []
        for arm, order in (("ourcc", "AB"), ("ha-vi", "BA")):
            sim, out = self.root / arm / "sim", self.root / arm / "out"
            self.correctness(sim, 228)
            self.write(out / "simout_n0", self.timer(0, "topology_remote_read"))
            runs.append({"id": arm, "metric": 3, "tc": 228, "repetition": 1,
                         "topology": "2n1s", "arm": arm, "pair": "p1", "order": order,
                         "simulator_log_dir": str(sim), "simout_dir": str(out)})
        report, _, _, _, issues, code = MOD.analyze(self.manifest(
            runs, {"metric1": {"repetitions": []},
                   "metric2": {"repetitions": [], "testcases": []},
                   "metric3": {"pairs": ["p1"], "testcases": list(MOD.M3)}}),
            self.root / "output")
        self.assertEqual(code, 2)
        self.assertTrue(any(issue["code"] == "M3_ORDER_CONFLICT" for issue in issues))

    def make_m2_run(self, run_id, profile="naive", repetition="r1", mean=1000):
        phase, topology, node, samples = MOD.M2[135]
        sim, out = self.root / run_id / "sim", self.root / run_id / "out"
        self.correctness(sim, 135, topology)
        self.write(out / f"simout_n{node}", self.latency(node, phase, samples, mean))
        return {"id": run_id, "metric": 2, "tc": 135, "repetition": repetition,
                "topology": topology, "profile": profile,
                "simulator_log_dir": str(sim), "simout_dir": str(out)}

    def test_incremental_finalize_uses_only_memory_after_add(self):
        run = self.make_m2_run("memory")
        matrix = MOD.Metric123RawLogMatrix(base_dir=self.root)
        self.assertEqual(matrix.add(run)["status"], "ADDED")
        shutil.rmtree(self.root / "memory")
        with mock.patch.object(MOD, "open_text", side_effect=AssertionError("reopened input")):
            result = matrix.finalize(self.root / "report")
        self.assertEqual(result["report"]["ingestion"]["added"], 1)
        self.assertEqual(len(result["resolved_runs"]), 1)
        self.assertTrue((self.root / "report/report.json").is_file())
        self.assertTrue((self.root / "report/metric_matrix.tsv").is_file())

    def test_incremental_incomplete_requirements_lists_missing_slots(self):
        requirements = {"metric1": {"repetitions": []},
                        "metric2": {"repetitions": ["r1"], "testcases": [135]},
                        "metric3": {"pairs": [], "testcases": []}}
        matrix = MOD.Metric123RawLogMatrix(requirements, base_dir=self.root)
        matrix.add(self.make_m2_run("only-naive"))
        report = matrix.finalize()["report"]
        self.assertEqual(report["overall_status"], "INCOMPLETE")
        self.assertIn(("r1", 135, "spill-noopt"), report["metric2"]["missing_slots"])

    def test_incremental_rejected_add_does_not_stop_later_ingestion(self):
        bad = self.make_m2_run("bad")
        (pathlib.Path(bad["simout_dir"]) / "simout_n1").write_text("")
        matrix = MOD.Metric123RawLogMatrix(base_dir=self.root)
        self.assertEqual(matrix.add(bad)["status"], "REJECTED")
        self.assertEqual(matrix.add(self.make_m2_run("good"))["status"], "ADDED")
        report = matrix.finalize()["report"]
        self.assertEqual(report["overall_status"], "INVALID")
        self.assertEqual(report["ingestion"]["attempted"], 2)
        self.assertEqual(report["ingestion"]["rejected"], 1)
        self.assertTrue(any(x["code"] == "EVIDENCE_INVALID" for x in report["issues"]))

    def test_incremental_duplicate_slot_excludes_all_claimants(self):
        matrix = MOD.Metric123RawLogMatrix(base_dir=self.root)
        self.assertEqual(matrix.add(self.make_m2_run("first"))["status"], "ADDED")
        duplicate = matrix.add(self.make_m2_run("second"))
        self.assertEqual(duplicate["status"], "REJECTED")
        self.assertEqual(duplicate["issue"]["code"], "DUPLICATE_SLOT")
        result = matrix.finalize()
        self.assertEqual(result["report"]["overall_status"], "INVALID")
        self.assertEqual(result["resolved_runs"], [])
        self.assertEqual(result["report"]["ingestion"]["duplicate_conflicted"], 2)
        self.assertTrue(any(x["code"] == "DUPLICATE_SLOT" for x in result["issues"]))

    def test_incremental_metric3_incomplete_pair(self):
        sim, out = self.root / "m3" / "sim", self.root / "m3" / "out"
        self.correctness(sim, 228)
        self.write(out / "simout_n0", self.timer(0, "topology_remote_read"))
        matrix = MOD.Metric123RawLogMatrix(base_dir=self.root)
        matrix.add(id="m3-ourcc", metric=3, tc=228, repetition="r1",
                   topology="2n1s", arm="ourcc", pair="p1", order="AB",
                   simulator_log_dir=str(sim), simout_dir=str(out))
        report = matrix.finalize()["report"]
        self.assertEqual(report["metric3"]["status"], "INCOMPLETE")
        self.assertEqual(report["metric3"]["incomplete_pairs"][0]["present_arms"], ["ourcc"])

    def test_incremental_repeated_finalize_is_deterministic(self):
        matrix = MOD.Metric123RawLogMatrix(base_dir=self.root)
        matrix.add(self.make_m2_run("repeat"))
        first = matrix.finalize()
        second = matrix.finalize()
        self.assertEqual(first, second)

    def test_incremental_add_after_finalize_updates_next_result(self):
        matrix = MOD.Metric123RawLogMatrix(base_dir=self.root)
        matrix.add(self.make_m2_run("first", profile="naive"))
        self.assertEqual(matrix.finalize()["report"]["ingestion"]["attempted"], 1)
        matrix.add(self.make_m2_run("second", profile="optimized"))
        self.assertEqual(matrix.finalize()["report"]["ingestion"]["attempted"], 2)

    def test_incremental_add_result_does_not_alias_internal_state(self):
        matrix = MOD.Metric123RawLogMatrix(base_dir=self.root)
        result = matrix.add(self.make_m2_run("isolated"))
        result["slot"][0] = 999
        stored = matrix.finalize()["report"]["ingestion"]["add_results"][0]
        self.assertEqual(stored["slot"][0], 2)


if __name__ == "__main__":
    unittest.main()
