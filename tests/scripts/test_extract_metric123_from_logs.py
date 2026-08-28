#!/usr/bin/env python3

import gzip
import importlib.util
import json
import pathlib
import pickle
import shutil
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/extract_metric123_from_logs.py"
SPEC = importlib.util.spec_from_file_location("extract_metric123_from_logs", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MOD
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

    @staticmethod
    def outer(latency_ps, req=1):
        return (f"[EP-PERF] kind=outer node=0 pa=0x1000 reqId={req} "
                f"latency_ps={latency_ps}\n")

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
            manifest = {"component": "ubio", "tc": 131, "node": 0, "socket": 0,
                        "experimental_oversized_resident_dir": 0,
                        "overflow_policy": policy}
            self.write(sim / "nested/ubio_tc131_n0_s0/stdout.log.gz",
                       "[PROCESS-MANIFEST] " + json.dumps(manifest) + "\n" +
                       f"[UBCC-STATE] capacity={capacity} policy={policy}\n{stats}", gz=True)
            self.write(sim / "nested/ubio_tc131_n1_s0/stdout.log",
                       "[UBCC-STATE] capacity=999999 policy=naive\n")
            for node in (1, 2): self.write(out / f"simout_tc131_node{node}.log.gz", self.timer(node, "post_pressure_catalog_reuse", ticks), gz=True)
            runs.append({"id": profile, "metric": 1, "tc": 131, "repetition": "r1", "topology": "8n1s",
                         "profile": profile, "simulator_log_dir": str(sim.relative_to(self.root)), "simout_dir": str(out.relative_to(self.root))})
        report, resolved, _, _, issues, code = MOD.analyze(self.manifest(runs, {"metric1": {"repetitions": ["r1"]}, "metric2": {"repetitions": [], "testcases": []}, "metric3": {"pairs": [], "testcases": []}}), self.root / "output")
        self.assertEqual(report["metric1"]["status"], "INCOMPLETE")
        self.assertIn(("r1", "ideal"), report["metric1"]["missing_slots"])
        self.assertTrue(all(x["severity"] == "WARNING" for x in issues))
        self.assertTrue(all(item["metrics"]["capacity"]["resident_capacity"] == 100
                            for item in resolved))

    def test_process_testcase_hint_is_optional_but_conflicts_reject(self):
        run = self.make_m1_run("tc-hint", layout="recognized")
        sim = pathlib.Path(run["simulator_log_dir"])
        log = sim / "ubio_tc131_n0_s0/stdout.log"
        text = log.read_text()
        log.write_text(text.replace('"tc": 131,', '"tc": 0,'))
        matrix = MOD.Metric123RawLogMatrix(base_dir=self.root)
        self.assertEqual(matrix.add(run)["status"], "ADDED")

        conflict = self.make_m1_run("tc-conflict", layout="recognized")
        conflict_log = (pathlib.Path(conflict["simulator_log_dir"]) /
                        "ubio_tc131_n0_s0/stdout.log")
        conflict_log.write_text(conflict_log.read_text().replace('"tc": 131,',
                                                                 '"tc": 999,'))
        rejected = MOD.Metric123RawLogMatrix(base_dir=self.root).add(conflict)
        self.assertEqual(rejected["status"], "REJECTED")
        self.assertIn("conflicts with manifest tc=131", rejected["issue"]["message"])

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

    def make_m1_run(self, run_id, layout="recognized", tc=131, topology="8n1s",
                    profile="naive", repetition="r1", capacity=100, policy="naive"):
        sim, out = self.root / run_id / "sim", self.root / run_id / "out"
        self.correctness(sim, tc, topology)
        for node in (1, 2):
            self.write(out / f"simout_tc{tc}_node{node}.log",
                       self.timer(node, "post_pressure_catalog_reuse"))
        text = ("[PROCESS-MANIFEST] " + json.dumps(
            {"component": "ubio", "tc": tc, "node": 0, "socket": 0,
             "experimental_oversized_resident_dir": 0,
             "overflow_policy": policy}) + "\n" +
                f"[UBCC-STATE] capacity={capacity} policy={policy}\n")
        if policy != "naive":
            text += f'[UBCC-STATS] {{"residentCapacity":{capacity},"h64ExactLiveKnown":1,"h64ExactLiveCount":{capacity + 50}}}\n'
        if layout == "recognized":
            target = sim / f"ubio_tc{tc}_n0_s0/stdout.log"
        elif layout == "manifest":
            target = sim / "arbitrary/process-A/console.txt"
        else:
            target = sim / "capacity.log"
        self.write(target, text)
        return {"id": run_id, "metric": 1, "tc": tc, "repetition": repetition,
                "topology": topology, "profile": profile,
                "simulator_log_dir": str(sim), "simout_dir": str(out)}

    def make_formal_m1(self, run_id, role, capacity, exact, outer_values=(),
                       timer=True, oversized=None, found=0, profile=None,
                       explicit_role=True):
        profile = profile or ("naive" if role == "naive" else "spill-noopt")
        policy = "naive" if role == "naive" else "spill"
        oversized = (1 if role == "ideal" else 0) if oversized is None else oversized
        run = self.make_m1_run(run_id, profile=profile, capacity=capacity, policy=policy)
        sim, out = pathlib.Path(run["simulator_log_dir"]), pathlib.Path(run["simout_dir"])
        manifest = {"component": "ubio", "tc": 131, "node": 0, "socket": 0,
                    "overflow_policy": policy,
                    "experimental_oversized_resident_dir": oversized}
        text = ("[PROCESS-MANIFEST] " + json.dumps(manifest) + "\n" +
                f"[UBCC-STATE] capacity={capacity} policy={policy}\n")
        if policy == "spill":
            text += (f'[UBCC-STATS] {{"residentCapacity":{capacity},'
                     f'"h64ExactLiveKnown":1,"h64ExactLiveCount":{exact}}}\n')
        text += "".join(f"[RESIDENT-FILL-DONE] found=1 pa=0x{i:x}\n"
                        for i in range(found))
        self.write(sim / "ubio_tc131_n0_s0/stdout.log", text)
        for index, value in enumerate(outer_values):
            self.write(sim / f"gem5_tc131_node{index % 2}/stderr.log",
                       self.outer(value, index + 1) +
                       ((sim / f"gem5_tc131_node{index % 2}/stderr.log").read_text()
                        if (sim / f"gem5_tc131_node{index % 2}/stderr.log").exists() else ""))
        if not timer:
            shutil.rmtree(out)
            out.mkdir()
        if explicit_role:
            run["metric1_role"] = role
        return run

    def test_metric1_correct_roles_outer_delta_and_timer_optional(self):
        req = {"metric1": {"repetitions": ["r1"],
                            "roles": ["naive", "spill", "ideal"],
                            "ideal_min_capacity": 1000}}
        matrix = MOD.Metric123RawLogMatrix(req, base_dir=self.root)
        naive = self.make_formal_m1("n", "naive", 100, None, timer=False)
        spill = self.make_formal_m1("s", "spill", 100, 160, (12000, 14000), timer=False)
        ideal = self.make_formal_m1("i", "ideal", 1000, 0, (10000, 12000), timer=False)
        for run in (naive, spill, ideal):
            self.assertEqual(matrix.add(run)["status"], "ADDED")
        result = matrix.finalize()
        row = result["report"]["metric1"]["comparisons"][0]
        self.assertEqual(result["report"]["metric1"]["status"], "PASS")
        self.assertAlmostEqual(row["capacity_ratio"], 1.6)
        self.assertEqual(row["spill_outer_samples"], 2)
        self.assertEqual(row["ideal_outer_samples"], 2)
        self.assertEqual(row["outer_delta_ns"], 2.0)
        self.assertEqual(row["outer_delta_cycles"], 4.0)
        self.assertIsNone(row["legacy_guest_descriptive"]["spill_minus_naive_ns"])
        self.assertEqual(sum(x["code"] == "METRIC1_GUEST_TIMER_MISSING"
                             for x in result["issues"]), 3)

    def test_metric1_role_alias_auto_detection_and_three_roles_coexist(self):
        matrix = MOD.Metric123RawLogMatrix(
            {"metric1": {"repetitions": ["r1"], "ideal_min_capacity": 1000}},
            base_dir=self.root)
        naive = self.make_formal_m1("n-role", "naive", 100, None,
                                    explicit_role=False)
        spill = self.make_formal_m1("s-role", "spill", 100, 160, (12000,),
                                    explicit_role=False)
        ideal = self.make_formal_m1("i-role", "ideal", 1000, 0, (10000,),
                                    explicit_role=False)
        spill["metric1_role"] = "actual"
        for run in (naive, spill, ideal):
            self.assertEqual(matrix.add(run)["status"], "ADDED")
        result = matrix.finalize()
        self.assertFalse(any(x["code"] == "DUPLICATE_SLOT" for x in result["issues"]))
        roles = {x["metric1_role"]: x for x in result["resolved_runs"]}
        self.assertEqual(set(roles), {"naive", "spill", "ideal"})
        self.assertEqual(roles["spill"]["role_source"], "explicit")
        self.assertEqual(roles["ideal"]["role_source"], "auto")
        self.assertTrue(any(x["code"] == "METRIC1_ROLE_AUTO_DETECTED"
                            for x in result["issues"]))

    def test_metric1_legacy_ideal_profile_normalizes_to_standard_role(self):
        matrix = MOD.Metric123RawLogMatrix(
            {"metric1": {"repetitions": ["r1"], "ideal_min_capacity": 1000}},
            base_dir=self.root)
        run = self.make_formal_m1("legacy-ideal", "ideal", 1000, 0,
                                  (10000,), explicit_role=False)
        run["profile"] = "ideal"
        added = matrix.add(run)
        self.assertEqual(added["status"], "ADDED")
        result = matrix.finalize()
        resolved = result["resolved_runs"][0]
        self.assertEqual(resolved["profile"], "spill-noopt")
        self.assertEqual(resolved["metric1_role"], "ideal")
        self.assertTrue(resolved["standard_contract"])
        self.assertTrue(any(
            issue["code"] == "LEGACY_METRIC1_PROFILE_NORMALIZED"
            for issue in result["issues"]))

    def test_metric1_legacy_ideal_conflicting_role_is_rejected_as_standard(self):
        run = self.make_formal_m1("legacy-conflict", "ideal", 1000, 0,
                                  (10000,), explicit_role=False)
        run.update(profile="ideal", metric1_role="spill")
        rejected = MOD.Metric123RawLogMatrix(base_dir=self.root).add(run)
        self.assertEqual(rejected["status"], "REJECTED")
        self.assertEqual(rejected["issue"]["contract_class"], "standard")
        self.assertIn("conflicts with metric1_role", rejected["issue"]["message"])

    def test_guest_timer_field_order_is_irrelevant_and_missing_is_diagnostic(self):
        reordered = self.make_formal_m1("reordered-timer", "naive", 100, None)
        out = pathlib.Path(reordered["simout_dir"])
        for node in (1, 2):
            self.write(out / f"simout_tc131_node{node}.log",
                       "[GUEST-TIMER] phase=post_pressure_catalog_reuse "
                       f"counter_frequency_hz=1000000000 node={node} "
                       "source=arm_cntvct_el0 counter_ticks=1000 "
                       "unit=counter_ticks operations=10\n")
        matrix = MOD.Metric123RawLogMatrix(base_dir=self.root)
        self.assertEqual(matrix.add(reordered)["status"], "ADDED")
        self.assertTrue(matrix.finalize()["resolved_runs"][0]["metrics"]
                        ["guest_timer_complete"])

        diagnostic = self.make_formal_m1("timer-diagnostic", "naive", 100, None)
        diagnostic_out = pathlib.Path(diagnostic["simout_dir"])
        self.write(diagnostic_out / "simout_tc131_node1.log",
                   self.timer(1, "different_phase"))
        self.write(diagnostic_out / "simout_tc131_node2.log",
                   "[GUEST-TIMER] node=2 phase=post_pressure_catalog_reuse\n")
        diagnostic_matrix = MOD.Metric123RawLogMatrix(base_dir=self.root)
        self.assertEqual(diagnostic_matrix.add(diagnostic)["status"], "ADDED")
        issue = next(item for item in diagnostic_matrix.finalize()["issues"]
                     if item["code"] == "METRIC1_GUEST_TIMER_MISSING")
        self.assertIn("simout_files=2", issue["message"])
        self.assertIn("different_phase", issue["message"])
        self.assertIn("missing_fields", issue["message"])

    def test_metric1_optimized_is_support_not_formal_spill(self):
        matrix = MOD.Metric123RawLogMatrix(
            correctness_policy="optional", base_dir=self.root)
        run = self.make_formal_m1(
            "optimized-support", "spill", 100, 160, (12000,),
            profile="optimized", explicit_role=False)
        self.assertEqual(matrix.add(run)["status"], "ADDED")
        resolved = matrix.finalize()["resolved_runs"][0]
        self.assertEqual(resolved["metric1_role"], "support")
        self.assertEqual(resolved["contract_class"], "extension")

    def test_metric1_ideal_gates_and_missing_ideal_are_incomplete(self):
        bad_cases = ((999, 0, 0), (1000, 1, 0), (1000, 0, 1))
        for index, (capacity, found, exact) in enumerate(bad_cases):
            req = {"metric1": {"repetitions": ["r1"], "ideal_min_capacity": 1000}}
            matrix = MOD.Metric123RawLogMatrix(req, base_dir=self.root)
            matrix.add(self.make_formal_m1(f"bn{index}", "naive", 100, None))
            matrix.add(self.make_formal_m1(f"bs{index}", "spill", 100, 160, (12000,)))
            added = matrix.add(self.make_formal_m1(
                f"bi{index}", "ideal", capacity, exact, (10000,), found=found))
            self.assertEqual(added["status"], "ADDED")
            result = matrix.finalize()
            self.assertEqual(result["report"]["metric1"]["status"], "INCOMPLETE")
            self.assertIn(("r1", "ideal"), result["report"]["metric1"]["missing_slots"])
        missing = MOD.Metric123RawLogMatrix(
            {"metric1": {"repetitions": ["r1"]}}, base_dir=self.root)
        missing.add(self.make_formal_m1("mn", "naive", 100, None))
        missing.add(self.make_formal_m1("ms", "spill", 100, 160, (12000,)))
        self.assertEqual(missing.finalize()["report"]["metric1"]["status"], "INCOMPLETE")

    def test_metric1_manifest_and_capacity_fallback_discovery(self):
        for layout, code in (("manifest", "HOME_UBIO_FALLBACK"),
                             ("fallback", "HOME_UBIO_FALLBACK")):
            matrix = MOD.Metric123RawLogMatrix(base_dir=self.root)
            result = matrix.add(self.make_m1_run(layout, layout=layout))
            self.assertEqual(result["status"], "ADDED")
            final = matrix.finalize()
            self.assertTrue(any(x["code"] == code for x in final["issues"]))
            self.assertEqual(final["resolved_runs"][0]["contract_class"], "standard")

    def test_metric1_identical_fallback_sources_warn_but_conflicting_rejects(self):
        identical = self.make_m1_run("identical", layout="fallback")
        sim = pathlib.Path(identical["simulator_log_dir"])
        self.write(sim / "second.log", text := (
            '[PROCESS-MANIFEST] {"component":"ubio","tc":131,"node":0,"socket":0,'
            '"experimental_oversized_resident_dir":0,"overflow_policy":"naive"}\n'
            '[UBCC-STATE] capacity=100 policy=naive\n'))
        matrix = MOD.Metric123RawLogMatrix(base_dir=self.root)
        self.assertEqual(matrix.add(identical)["status"], "ADDED")
        self.assertTrue(any(x["code"] == "HOME_UBIO_IDENTICAL_MULTIPLE"
                            for x in matrix.finalize()["issues"]))

        conflict = self.make_m1_run("conflict", layout="fallback")
        self.write(pathlib.Path(conflict["simulator_log_dir"]) / "second.log",
                   text.replace("capacity=100", "capacity=200"))
        rejected = MOD.Metric123RawLogMatrix(base_dir=self.root).add(conflict)
        self.assertEqual(rejected["status"], "REJECTED")
        self.assertIn("disagree", rejected["issue"]["message"])

    def test_missing_and_duplicate_ids_are_stably_resolved(self):
        matrix = MOD.Metric123RawLogMatrix(base_dir=self.root)
        missing = self.make_m2_run("source-a", profile="naive")
        del missing["id"]
        first = matrix.add(missing)
        second = matrix.add(self.make_m2_run("same", profile="optimized"))
        duplicate = self.make_m2_run("same-other", profile="spill-noopt")
        duplicate["id"] = "same"
        third = matrix.add(duplicate)
        self.assertEqual(first["run_id"], "run-000001")
        self.assertEqual(second["run_id"], "same")
        self.assertEqual(third["status"], "ADDED")
        self.assertEqual(third["requested_id"], "same")
        self.assertEqual(third["run_id"], "same-2")
        self.assertEqual(matrix.finalize()["report"]["ingestion"]["added"], 3)

    def test_nonstandard_metric1_is_extension_with_descriptive_view(self):
        run = self.make_m1_run("m1-ext", tc=999, topology="4n1s")
        matrix = MOD.Metric123RawLogMatrix(base_dir=self.root)
        self.assertEqual(matrix.add(run)["status"], "ADDED")
        result = matrix.finalize()
        self.assertEqual(result["report"]["metric1"]["status"], "NOT_REQUESTED")
        self.assertEqual(len(result["matrices"]["standard"]), 0)
        self.assertEqual(len(result["matrices"]["all"]), 1)
        self.assertEqual(len(result["matrices"]["extension"]), 1)
        self.assertEqual(result["resolved_runs"][0]["contract_class"], "extension")

    def test_standard_formal_result_unchanged_when_extension_is_added(self):
        requirements = {"metric1": {"repetitions": []},
                        "metric2": {"repetitions": ["r1"], "testcases": [135]},
                        "metric3": {"pairs": [], "testcases": []}}
        matrix = MOD.Metric123RawLogMatrix(requirements, base_dir=self.root)
        for profile, mean in (("naive", 1000), ("spill-noopt", 900), ("optimized", 800)):
            matrix.add(self.make_m2_run("official-" + profile, profile=profile, mean=mean))
        before = matrix.finalize()
        sim, out = self.root / "extra/sim", self.root / "extra/out"
        self.correctness(sim, 999, "1n1s")
        self.write(out / "simout_n0", self.latency(0, "extension_phase", 3, 700))
        matrix.add(metric=2, tc=999, repetition="rx", topology="1n1s", profile="naive",
                   phase="extension_phase", simulator_log_dir=str(sim), simout_dir=str(out))
        after = matrix.finalize()
        self.assertEqual(after["report"]["metric2"], before["report"]["metric2"])
        self.assertGreater(after["report"]["views"]["all"]["runs"],
                           before["report"]["views"]["all"]["runs"])
        self.assertEqual(after["report"]["views"]["extension"]["runs"], 1)

    def test_standard_and_extension_same_tc_profile_do_not_conflict(self):
        matrix = MOD.Metric123RawLogMatrix(base_dir=self.root)
        standard = self.make_m1_run("standard", tc=131, topology="8n1s")
        extension = self.make_m1_run("extension", tc=131, topology="4n1s")
        self.assertEqual(matrix.add(standard)["status"], "ADDED")
        self.assertEqual(matrix.add(extension)["status"], "ADDED")
        result = matrix.finalize()
        self.assertEqual(result["report"]["views"]["standard"]["runs"], 1)
        self.assertEqual(result["report"]["views"]["extension"]["runs"], 1)
        self.assertFalse(any(x["code"] == "DUPLICATE_SLOT" for x in result["issues"]))

    def test_flat_manifest_without_capacity_falls_back_to_capacity_file(self):
        sim, out = self.root / "flat/sim", self.root / "flat/out"
        sim.mkdir(parents=True)
        self.write(sim / "identity.log",
                   '[PROCESS-MANIFEST] {"component":"ubio","tc":131,'
                   '"node":0,"socket":0,"overflow_policy":"naive"}\n')
        self.write(sim / "capacity.log",
                   "[UBCC-STATE] capacity=100 policy=naive\n")
        for node in (1, 2):
            self.write(out / f"simout_n{node}",
                       self.timer(node, "post_pressure_catalog_reuse"))
        matrix = MOD.Metric123RawLogMatrix(correctness_policy="optional", base_dir=self.root)
        added = matrix.add(metric=1, tc=131, repetition="r1", topology="8n1s",
                           profile="naive", simulator_log_dir=str(sim), simout_dir=str(out))
        self.assertEqual(added["status"], "ADDED")
        self.assertTrue(any(x["code"] == "HOME_UBIO_FALLBACK"
                            for x in matrix.finalize()["issues"]))

    def test_explicit_home_ubio_path_resolves_ambiguous_sources(self):
        sim, out = self.root / "ambiguous/sim", self.root / "ambiguous/out"
        sim.mkdir(parents=True)
        home = sim / "home-node"
        peer = sim / "peer-node"
        self.write(home / "stdout.log",
                   "[UBCC-STATE] capacity=57344 policy=spill\n"
                   '[UBCC-STATS] {"h64ExactLiveKnown":1,'
                   '"h64ExactLiveCount":99424}\n')
        self.write(peer / "stdout.log",
                   "[UBCC-STATE] capacity=57344 policy=spill\n"
                   '[UBCC-STATS] {"h64ExactLiveKnown":1,'
                   '"h64ExactLiveCount":57344}\n')
        for node in (1, 2):
            self.write(out / f"simout_n{node}",
                       self.timer(node, "post_pressure_catalog_reuse"))
        matrix = MOD.Metric123RawLogMatrix(
            correctness_policy="optional", base_dir=self.root)
        added = matrix.add(
            metric=1, tc=131, repetition="r1", topology="8n1s",
            profile="spill-noopt", simulator_log_dir=str(sim),
            simout_dir=str(out), home_ubio_log_dir=str(home))
        self.assertEqual(added["status"], "ADDED")
        result = matrix.finalize()
        self.assertEqual(result["resolved_runs"][0]["metrics"]["capacity"]
                         ["effective_unique"], 99424)
        self.assertTrue(any(x["code"] == "HOME_UBIO_EXPLICIT"
                            for x in result["issues"]))

    def test_invalid_extension_does_not_invalidate_standard_view(self):
        requirements = {"metric1": {"repetitions": []},
                        "metric2": {"repetitions": ["r1"], "testcases": [135]},
                        "metric3": {"pairs": [], "testcases": []}}
        matrix = MOD.Metric123RawLogMatrix(requirements, base_dir=self.root)
        for profile, mean in (("naive", 1000), ("spill-noopt", 900), ("optimized", 800)):
            matrix.add(self.make_m2_run("official-valid-" + profile,
                                        profile=profile, mean=mean))
        bad = self.make_m2_run("bad-extension", profile="naive")
        bad.update(tc=999, topology="1n1s", phase="missing-phase")
        self.assertEqual(matrix.add(bad)["status"], "REJECTED")
        report = matrix.finalize()["report"]
        self.assertNotEqual(report["overall_status"], "INVALID")
        self.assertTrue(any(x.get("contract_class") == "extension" and
                            x["severity"] == "ERROR" for x in report["issues"]))

    def test_nonstandard_metric2_phase_and_metric3_topology_parse_as_extensions(self):
        sim2, out2 = self.root / "m2ext/sim", self.root / "m2ext/out"
        sim2.mkdir(parents=True)
        self.write(out2 / "simout_n4", self.latency(4, "custom_phase", 7))
        m2 = MOD.Metric123RawLogMatrix(correctness_policy="optional", base_dir=self.root)
        added = m2.add(metric=2, tc=999, repetition="r1", topology="5n1s", profile="naive",
                       phase="custom_phase", expected_node=4, expected_samples=7,
                       simulator_log_dir=str(sim2), simout_dir=str(out2))
        self.assertEqual(added["status"], "ADDED")
        self.assertEqual(m2.finalize()["resolved_runs"][0]["contract_class"], "extension")

        sim3, out3 = self.root / "m3ext/sim", self.root / "m3ext/out"
        self.correctness(sim3, 228, "3n1s")
        self.write(out3 / "simout_n0", self.timer(0, "topology_remote_read"))
        m3 = MOD.Metric123RawLogMatrix(base_dir=self.root)
        self.assertEqual(m3.add(metric=3, tc=228, repetition="r1", topology="3n1s",
                                arm="ourcc", pair="p", order="AB",
                                simulator_log_dir=str(sim3), simout_dir=str(out3))["status"], "ADDED")
        final_m3 = m3.finalize()
        self.assertEqual(final_m3["resolved_runs"][0]["contract_class"], "extension")
        warning_text = next(item["message"] for item in final_m3["issues"]
                            if item["code"] == "NONSTANDARD_CONTRACT")
        self.assertIn("topology=3n1s expected=2n1s", warning_text)

    def test_unknown_metric3_requires_specs_and_can_parse_with_specs(self):
        sim, out = self.root / "m3spec/sim", self.root / "m3spec/out"
        sim.mkdir(parents=True)
        self.write(out / "simout_n0", self.timer(0, "custom_timer"))
        base = {"metric": 3, "tc": 999, "repetition": "r1", "topology": "1n1s",
                "arm": "ourcc", "pair": "p", "order": "AB",
                "simulator_log_dir": str(sim), "simout_dir": str(out)}
        missing = MOD.Metric123RawLogMatrix(correctness_policy="optional", base_dir=self.root)
        rejected = missing.add(base)
        self.assertEqual(rejected["status"], "REJECTED")
        self.assertIn("PARSER_SPEC_REQUIRED", rejected["issue"]["message"])
        supplied = dict(base, metric_specs={"custom": {
            "kind": "timer", "phase": "custom_timer", "reduction": "aggregate"}})
        accepted = MOD.Metric123RawLogMatrix(correctness_policy="optional", base_dir=self.root)
        self.assertEqual(accepted.add(supplied)["status"], "ADDED")
        self.assertEqual(accepted.finalize()["resolved_runs"][0]["metrics"]["custom"]
                         ["ticks_per_operation"], 100)

    def test_matrix_addition_is_snapshot_only_and_unions_requirements(self):
        left = MOD.Metric123RawLogMatrix(
            {"metric1": {"repetitions": []}, "metric2": {"repetitions": ["r1"], "testcases": [135]},
             "metric3": {"pairs": [], "testcases": []}},
            correctness_policy="optional", base_dir=self.root)
        right = MOD.Metric123RawLogMatrix(
            {"metric1": {"repetitions": []}, "metric2": {"repetitions": ["r2"], "testcases": [135]},
             "metric3": {"pairs": [], "testcases": []}},
            correctness_policy="strict", base_dir=self.root)
        run_left = self.make_m2_run("merge-left", profile="naive", repetition="r1")
        run_right = self.make_m2_run("merge-right", profile="naive", repetition="r2")
        run_left["id"] = run_right["id"] = "collision"
        left.add(run_left); right.add(run_right)
        left_before, right_before = left.finalize(), right.finalize()
        merged = left + right
        shutil.rmtree(self.root / "merge-left"); shutil.rmtree(self.root / "merge-right")
        with mock.patch.object(MOD, "open_text", side_effect=AssertionError("reopened input")):
            result = merged.finalize()
        self.assertEqual(left.finalize(), left_before)
        self.assertEqual(right.finalize(), right_before)
        self.assertEqual(merged.correctness_policy, "strict")
        self.assertEqual(merged._data()["requirements"]["metric2"]["repetitions"], ["r1", "r2"])
        self.assertEqual({r["id"] for r in result["resolved_runs"]}, {"collision", "collision-2"})
        self.assertTrue(any(x["code"] == "DUPLICATE_RUN_ID_RENAMED" for x in result["issues"]))

    def test_matrix_merge_accepts_iterables_and_clones_single_input(self):
        matrix = MOD.Metric123RawLogMatrix(base_dir=self.root)
        matrix.add(self.make_m2_run("merge-clone"))
        clone = MOD.merge(item for item in [matrix])
        self.assertIsNot(clone, matrix)
        self.assertEqual(clone.finalize(), matrix.finalize())
        clone._resolved[0]["id"] = "mutated"
        self.assertEqual(matrix._resolved[0]["id"], "merge-clone")
        with self.assertRaisesRegex(ValueError, "at least one"):
            MOD.merge([])
        with self.assertRaisesRegex(TypeError, "item 1"):
            MOD.merge([matrix, object()])

    def test_matrix_merge_accepts_legacy_mixed_inferred_types(self):
        left = MOD.Metric123RawLogMatrix(base_dir=self.root)
        right = MOD.Metric123RawLogMatrix(base_dir=self.root)
        left._inferred["metric2"]["repetitions"].update({1, "r2"})
        right._inferred["metric2"]["repetitions"].add("r3")
        merged = MOD.merge([left, right])
        self.assertEqual(merged._data()["requirements"]["metric2"]["repetitions"],
                         [1, "r2", "r3"])

    def test_matrix_merge_reports_invalid_testcase_requirement_path(self):
        explicit = MOD.Metric123RawLogMatrix(
            {"metric3": {"testcases": [228, "r0"]}}, base_dir=self.root)
        with self.assertRaisesRegex(
                MOD.ExtractError,
                r"merge\(\) item 0 requirements\.metric3\.testcases\[1\].*'r0'"):
            MOD.merge([explicit, MOD.Metric123RawLogMatrix(base_dir=self.root)])

        inferred = MOD.Metric123RawLogMatrix(base_dir=self.root)
        inferred._inferred["metric3"]["testcases"].update({228, "r0"})
        merged = MOD.merge([inferred, MOD.Metric123RawLogMatrix(base_dir=self.root)])
        self.assertEqual(merged._data()["requirements"]["metric3"]["testcases"], [228])
        self.assertTrue(any(issue["code"] == "LEGACY_METRIC3_INFERENCE_REPAIRED"
                            for issue in merged.finalize()["issues"]))

    def test_legacy_metric3_inference_is_repaired_on_unpickle_and_finalize(self):
        matrix = MOD.Metric123RawLogMatrix(base_dir=self.root)
        matrix._inferred["metric3"]["testcases"].update(
            {228, 229, "r0", "r1", "r2"})
        state = matrix.__getstate__()
        restored = object.__new__(MOD.Metric123RawLogMatrix)
        restored.__setstate__(state)
        requirements = restored._data()["requirements"]["metric3"]
        self.assertEqual(requirements["testcases"], [228, 229])
        self.assertEqual(requirements["repetitions"], [])
        result = restored.finalize()
        repaired = [issue for issue in result["issues"]
                    if issue["code"] == "LEGACY_METRIC3_INFERENCE_REPAIRED"]
        self.assertEqual(len(repaired), 1)
        self.assertEqual(repaired[0]["removed_values"], ["r0", "r1", "r2"])

        polluted = MOD.Metric123RawLogMatrix(base_dir=self.root)
        polluted._inferred["metric3"]["testcases"].update({228, "r0"})
        self.assertEqual(
            polluted.finalize()["report"]["metric3"]["missing_slots"][0][0], 228)
        clone = MOD.merge([polluted])
        self.assertEqual(clone._data()["requirements"]["metric3"]["testcases"],
                         [228])

    def test_finalize_reports_invalid_testcase_requirement_path(self):
        matrix = MOD.Metric123RawLogMatrix(
            {"metric3": {"testcases": ["r0"]}}, base_dir=self.root)
        with self.assertRaisesRegex(
                MOD.ExtractError,
                r"requirements\.metric3\.testcases\[0\].*'r0'"):
            matrix.finalize()

    def test_matrix_merge_scans_each_snapshot_once(self):
        matrices = []
        for index in range(8):
            matrix = MOD.Metric123RawLogMatrix(base_dir=self.root)
            matrix.add(self.make_m2_run(
                f"merge-linear-{index}", repetition=f"r{index}"))
            matrices.append(matrix)
        original = MOD._merge_snapshot_fingerprint
        calls = []

        def counted(record):
            calls.append(record["id"])
            return original(record)

        with mock.patch.object(MOD, "_merge_snapshot_fingerprint", side_effect=counted):
            merged = MOD.merge(matrices)
        self.assertEqual(len(calls), 8)
        self.assertEqual(merged.finalize()["report"]["ingestion"]["attempted"], 8)

    def test_matrix_merge_deduplicates_same_evidence_across_policies(self):
        matrix = MOD.Metric123RawLogMatrix(base_dir=self.root)
        matrix.add(self.make_m2_run("merge-policy-dedupe"))
        optional = pickle.loads(pickle.dumps(matrix))
        optional.correctness_policy = "optional"
        optional._resolved[0]["correctness"]["policy"] = "optional"
        optional._resolved[0]["correctness"]["required"] = False
        merged = MOD.merge([matrix, optional])
        self.assertEqual(len(merged._resolved), 1)
        self.assertEqual(merged._resolved[0]["correctness"]["policy"], "strict")

    def test_matrix_merge_requalifies_snapshot_from_final_registry(self):
        run_matrix = MOD.Metric123RawLogMatrix(
            correctness_policy="optional", base_dir=self.root)
        run = self.make_m3_run("merge-qualified", 228, "r1", "ourcc",
                               topology="3n1s")
        self.assertEqual(run_matrix.add(run)["status"], "ADDED")
        qualification = {"id": "m3-merge-3n1s", "metric": 3,
                         "mode": "independent", "topologies": ["3n1s"],
                         "testcases": [228], "arms": ["ourcc", "ha-vi"],
                         "repetitions": ["r1"]}
        registry = MOD.Metric123RawLogMatrix(
            {"qualification_sets": [qualification]},
            correctness_policy="optional", base_dir=self.root)
        result = MOD.merge([run_matrix, registry]).finalize()
        self.assertEqual(result["resolved_runs"][0]["qualified_contracts"],
                         ["m3-merge-3n1s"])
        self.assertTrue(result["resolved_runs"][0]["formal_contract"])
        self.assertEqual(result["report"]["views"]["formal"]["runs"], 1)
        self.assertEqual(result["report"]["views"]["extension"]["runs"], 0)

    def test_matrix_merge_preserves_rejected_id_reservations(self):
        matrix = MOD.Metric123RawLogMatrix(base_dir=self.root)
        bad = self.make_m2_run("merge-rejected")
        (pathlib.Path(bad["simout_dir"]) / "simout_n1").write_text("")
        self.assertEqual(matrix.add(bad)["status"], "REJECTED")
        merged = MOD.merge([matrix, MOD.Metric123RawLogMatrix(base_dir=self.root)])
        replacement = self.make_m2_run("merge-replacement", repetition="r2")
        replacement["id"] = "merge-rejected"
        added = merged.add(replacement)
        self.assertEqual(added["run_id"], "merge-rejected-2")
        self.assertEqual(added["warning"]["code"], "DUPLICATE_RUN_ID_RENAMED")

    def test_matrix_merge_applies_stricter_correctness_policy_from_snapshot(self):
        optional = MOD.Metric123RawLogMatrix(
            correctness_policy="optional", base_dir=self.root)
        run = self.make_m2_run("merge-optional")
        shutil.rmtree(pathlib.Path(run["simulator_log_dir"]))
        pathlib.Path(run["simulator_log_dir"]).mkdir(parents=True)
        self.assertEqual(optional.add(run)["status"], "ADDED")
        strict = MOD.Metric123RawLogMatrix(
            correctness_policy="strict", base_dir=self.root)
        result = MOD.merge([optional, strict]).finalize()
        self.assertEqual(result["resolved_runs"], [])
        self.assertTrue(any(issue["code"] == "EVIDENCE_INVALID"
                            for issue in result["issues"]))

    def test_matrix_merge_result_pickles_without_source_logs(self):
        left = MOD.Metric123RawLogMatrix(base_dir=self.root)
        right = MOD.Metric123RawLogMatrix(base_dir=self.root)
        left.add(self.make_m2_run("merge-pickle-left", repetition="r1"))
        right.add(self.make_m2_run("merge-pickle-right", repetition="r2"))
        merged = MOD.merge([left, right])
        expected = merged.finalize()
        shutil.rmtree(self.root / "merge-pickle-left")
        shutil.rmtree(self.root / "merge-pickle-right")
        restored = pickle.loads(pickle.dumps(merged))
        with mock.patch.object(MOD, "open_text",
                               side_effect=AssertionError("reopened input")):
            self.assertEqual(restored.finalize(), expected)

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
        self.assertTrue((self.root / "report/metric_matrix_standard.tsv").is_file())
        self.assertTrue((self.root / "report/metric_matrix_all.tsv").is_file())
        self.assertTrue((self.root / "report/metric_matrix_extension.tsv").is_file())
        inventory = result["report"]["source_inventory"]
        self.assertEqual(inventory["logical_runs"], 1)
        self.assertGreater(inventory["unique_files"], 0)
        self.assertGreater(inventory["source_references"], 0)
        self.assertIn("not logical runs", inventory["note"])

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
        matrix = MOD.Metric123RawLogMatrix(
            {"metric3": {"mode": "paired", "pairs": ["p1"], "testcases": [228]}},
            base_dir=self.root)
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

    def test_matrix_pickle_round_trip_is_snapshot_only_for_all_protocols(self):
        matrix = MOD.Metric123RawLogMatrix(base_dir=self.root)
        accepted = self.make_m2_run("pickle-accepted", profile="naive")
        rejected = self.make_m2_run("pickle-rejected", profile="optimized")
        (pathlib.Path(rejected["simout_dir"]) / "simout_n1").write_text("")
        self.assertEqual(matrix.add(accepted)["status"], "ADDED")
        self.assertEqual(matrix.add(rejected)["status"], "REJECTED")
        before = matrix.finalize()
        shutil.rmtree(self.root / "pickle-accepted")
        shutil.rmtree(self.root / "pickle-rejected")
        for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
            restored = pickle.loads(pickle.dumps(matrix, protocol=protocol))
            with mock.patch.object(MOD, "open_text",
                                   side_effect=AssertionError("reopened input")):
                self.assertEqual(restored.finalize(), before)
            duplicate = self.make_m2_run(
                f"pickle-duplicate-{protocol}", profile="naive")
            duplicate["repetition"] = "r1"
            result = restored.add(duplicate)
            self.assertEqual(result["status"], "REJECTED")
            self.assertEqual(result["issue"]["code"], "DUPLICATE_SLOT")

    def test_matrix_pickle_rejects_unknown_state_version(self):
        matrix = object.__new__(MOD.Metric123RawLogMatrix)
        with self.assertRaisesRegex(ValueError, "unsupported"):
            matrix.__setstate__({"version": 999})

    def make_m3_run(self, run_id, tc, repetition, arm=None, tick_base=100,
                     identity=None, pair=None, order=None, topology="2n1s"):
        sim, out = self.root / run_id / "sim", self.root / run_id / "out"
        self.correctness(sim, tc, topology)
        if identity:
            self.write(sim / "identity.log", identity)
        text = ""
        for index, (_, (kind, phase, _)) in enumerate(MOD.M3[tc].items()):
            ticks = (tick_base + index * 10) * 10
            text += (self.timer(0, phase, ticks) if kind == "timer" else
                     self.latency(0, phase, 10, tick_base + index * 10))
        self.write(out / "simout_n0", text)
        run = {"id": run_id, "metric": 3, "tc": tc, "repetition": repetition,
                "topology": topology, "simulator_log_dir": str(sim),
               "simout_dir": str(out)}
        if arm is not None: run["arm"] = arm
        if pair is not None: run["pair"] = pair
        if order is not None: run["order"] = order
        return run

    def test_metric3_independent_aliases_repeats_and_arm_means(self):
        requirements = {"metric1": {"repetitions": []},
                        "metric2": {"repetitions": [], "testcases": []},
                        "metric3": {"mode": "independent", "repetitions": ["r1", "r2"],
                                    "testcases": list(MOD.M3), "arms": ["UBCC", "HA_VI"]}}
        matrix = MOD.Metric123RawLogMatrix(requirements, base_dir=self.root)
        for tc in MOD.M3:
            for rep, offset in (("r1", 0), ("r2", 20)):
                matrix.add(self.make_m3_run(f"o-{tc}-{rep}", tc, rep, "lossless-oneway", 100 + offset))
                matrix.add(self.make_m3_run(f"h-{tc}-{rep}", tc, rep, "HaVi", 130 + offset))
        report = matrix.finalize()["report"]
        self.assertEqual(report["metric3"]["status"], "PASS (EXECUTABLE-REFERENCE-MODEL SCOPE)")
        self.assertEqual(report["metric3"]["comparison_mode"], "independent")
        summary = next(x for x in report["metric3"]["metric_summaries"]
                       if x["tc"] == 228)
        self.assertEqual(summary["ourcc_mean_ticks"], 110)
        self.assertEqual(summary["ha_vi_mean_ticks"], 140)
        self.assertEqual(summary["delta_mean_ticks"], 30)
        self.assertEqual(summary["ourcc_count"], 2)

    def test_metric3_repetition_is_optional_and_auto_assigned(self):
        matrix = MOD.Metric123RawLogMatrix(
            correctness_policy="optional", base_dir=self.root)
        run = self.make_m3_run("auto-repetition", 228, "unused", "ourcc")
        run.pop("repetition")
        result = matrix.add(run)
        self.assertEqual(result["status"], "ADDED")
        resolved = matrix.finalize()["resolved_runs"][0]
        self.assertEqual(resolved["repetition"], "auto-repetition")
        self.assertEqual(resolved["repetition_source"], "auto-run-id")
        self.assertTrue(any(item["code"] == "METRIC3_REPETITION_AUTO_ASSIGNED"
                            for item in resolved["contract_warnings"]))
        requirements = matrix._data()["requirements"]["metric3"]
        self.assertEqual(requirements["repetitions"], [])
        self.assertEqual(requirements["min_repetitions"], 1)

    def test_metric3_workers_need_no_shared_id_or_repetition_allocator(self):
        matrices = []
        for index, arm in enumerate(("ourcc", "ha-vi")):
            matrix = MOD.Metric123RawLogMatrix(
                correctness_policy="optional", base_dir=self.root)
            run = self.make_m3_run(f"auto-worker-{index}", 228, "unused", arm)
            run.pop("id")
            run.pop("repetition")
            self.assertEqual(matrix.add(run)["run_id"], "run-000001")
            matrices.append(matrix)
        merged = MOD.merge(matrices)
        resolved = merged.finalize()["resolved_runs"]
        self.assertEqual({row["id"] for row in resolved},
                         {"run-000001", "run-000001-2"})
        self.assertEqual({row["repetition"] for row in resolved},
                         {"run-000001", "run-000001-2"})
        self.assertFalse(any(issue["code"] == "DUPLICATE_SLOT"
                             for issue in merged.finalize()["issues"]))

    def test_metric3_arm_auto_detection_conflict_and_missing(self):
        for label, marker, expected in (
                ("our", "[EPBACKEND-PROFILE] node=0 ha_endpoint_profile=ubcc\n", "ourcc"),
                ("havi", "[UBIO-HA-MANIFEST] controller=ha-vi node=0 socket=0\n", "ha-vi")):
            matrix = MOD.Metric123RawLogMatrix(base_dir=self.root)
            added = matrix.add(self.make_m3_run(label, 228, "r1", identity=marker))
            self.assertEqual(added["status"], "ADDED")
            resolved = matrix.finalize()["resolved_runs"][0]
            self.assertEqual(resolved["arm"], expected)
            self.assertEqual(resolved["arm_source"], "auto")
            self.assertTrue(any(x["code"] == "ARM_AUTO_DETECTED" for x in resolved["contract_warnings"]))
        conflict = ("[EPBACKEND-PROFILE] node=0 ha_endpoint_profile=ubcc\n"
                    "[PROCESS-MANIFEST] {\"component\":\"ubio\",\"home_controller\":\"ha-vi\"}\n")
        rejected = MOD.Metric123RawLogMatrix(base_dir=self.root).add(
            self.make_m3_run("conflicting-arm", 228, "r1", identity=conflict))
        self.assertEqual(rejected["issue"]["code"], "ARM_IDENTITY_CONFLICT")
        missing = MOD.Metric123RawLogMatrix(base_dir=self.root).add(
            self.make_m3_run("missing-arm", 228, "r1"))
        self.assertEqual(missing["issue"]["code"], "ARM_IDENTITY_MISSING")

    def test_metric3_independent_imbalance_is_incomplete_but_descriptive(self):
        req = {"metric3": {"mode": "independent", "min_repetitions": 1,
                           "testcases": [228], "arms": ["ourcc", "ha-vi"]}}
        matrix = MOD.Metric123RawLogMatrix(req, base_dir=self.root)
        matrix.add(self.make_m3_run("o1", 228, "r1", "ourcc", 100))
        matrix.add(self.make_m3_run("o2", 228, "r2", "ourcc", 110))
        matrix.add(self.make_m3_run("h1", 228, "r1", "ha-vi", 130))
        report = matrix.finalize()["report"]
        self.assertEqual(report["metric3"]["status"], "INCOMPLETE")
        self.assertEqual(len(report["views"]["all"]["metric3_arm_comparisons"]), 1)

    def test_metric2_unknown_phase_discovery_and_weighted_multinode(self):
        sim, out = self.root / "m2-auto/sim", self.root / "m2-auto/out"
        sim.mkdir(parents=True)
        self.write(out / "simout_n0", self.latency(0, "only", 1, 100))
        self.write(out / "simout_n1", self.latency(1, "only", 3, 300))
        matrix = MOD.Metric123RawLogMatrix(correctness_policy="optional", base_dir=self.root)
        result = matrix.add(metric=2, tc=999, repetition="r1", topology="2n1s",
                            profile="naive", simulator_log_dir=str(sim), simout_dir=str(out))
        self.assertEqual(result["status"], "ADDED")
        run = matrix.finalize()["resolved_runs"][0]
        self.assertEqual(run["metrics"]["mean_ticks"], 250)
        self.assertEqual(run["metrics"]["nodes"], [0, 1])
        self.assertTrue(any(x["code"] == "METRIC2_PHASE_AUTO_DETECTED"
                            for x in run["contract_warnings"]))

    def test_metric2_unknown_multiple_phases_and_official_extra_phase(self):
        sim, out = self.root / "m2-multi/sim", self.root / "m2-multi/out"
        sim.mkdir(parents=True)
        self.write(out / "simout_n0", self.latency(0, "a", 2, 100) +
                   self.latency(0, "b", 3, 200))
        matrix = MOD.Metric123RawLogMatrix(correctness_policy="optional", base_dir=self.root)
        self.assertEqual(matrix.add(metric=2, tc=999, repetition="r1", topology="1n1s",
                                    profile="naive", simulator_log_dir=str(sim),
                                    simout_dir=str(out))["status"], "ADDED")
        result = matrix.finalize()
        self.assertEqual(set(result["resolved_runs"][0]["metrics"]["latency_phases"]), {"a", "b"})
        self.assertEqual(len(result["matrices"]["all"]), 2)

        official = self.make_m2_run("official-extra", mean=1000)
        phase, _, node, _ = MOD.M2[135]
        path = pathlib.Path(official["simout_dir"]) / f"simout_n{node}"
        path.write_text(path.read_text() + self.latency(node, "extra", 5, 700))
        official_matrix = MOD.Metric123RawLogMatrix(base_dir=self.root)
        self.assertEqual(official_matrix.add(official)["status"], "ADDED")
        official_run = official_matrix.finalize()["resolved_runs"][0]
        self.assertTrue(official_run["standard_contract"])
        self.assertEqual(official_run["metrics"]["mean_ticks"], 1000)
        self.assertIn("extra", official_run["metrics"]["latency_phases"])

    def test_metric1_missing_guest_timers_are_null_in_descriptive_views(self):
        matrix = MOD.Metric123RawLogMatrix(base_dir=self.root)
        runs = (
            self.make_formal_m1("missing-naive", "naive", 100, None, timer=False),
            self.make_formal_m1("missing-spill", "spill", 100, 160, (12000,),
                                timer=False),
            self.make_formal_m1("missing-optimized", "spill", 100, 160,
                                profile="optimized", explicit_role=False,
                                timer=False),
        )
        for run in runs:
            self.assertEqual(matrix.add(run)["status"], "ADDED")
        result = matrix.finalize(self.root / "missing-m1-report")
        comparison = result["report"]["views"]["all"]["comparisons"][0]
        self.assertIsNone(comparison["optimized_delta_ns"])
        self.assertEqual(result["report"]["metric1"]["status"], "INCOMPLETE")
        self.assertTrue(all(row["value"] is None for row in
                            result["report"]["views"]["all"]["matrix"]))
        self.assertIn("N/A", (self.root / "missing-m1-report" /
                              "metric_matrix_all.tsv").read_text())

    def test_metric2_missing_mean_and_zero_denominator_are_incomplete(self):
        requirements = {"metric1": {"repetitions": []},
                        "metric2": {"repetitions": ["r1"], "testcases": [135]},
                        "metric3": {"testcases": []}}
        for label, unavailable in (("missing", None), ("zero", 0)):
            matrix = MOD.Metric123RawLogMatrix(requirements, base_dir=self.root)
            for profile, mean in (("naive", 1000), ("spill-noopt", 900),
                                  ("optimized", 800)):
                self.assertEqual(matrix.add(self.make_m2_run(
                    f"{label}-{profile}", profile=profile, mean=mean))["status"],
                    "ADDED")
            naive = next(run for run in matrix._resolved if run["profile"] == "naive")
            naive["metrics"]["mean_ns"] = unavailable
            result = matrix.finalize(self.root / f"{label}-m2-report")
            self.assertEqual(result["report"]["metric2"]["status"], "INCOMPLETE")
            case = result["report"]["metric2"]["cases"][0]
            self.assertIsNone(case["optimized_reduction_pct"])
            comparison = result["report"]["views"]["all"]["comparisons"][0]
            self.assertIsNone(comparison["optimized_reduction_pct"])
            self.assertIn("N/A", (self.root / f"{label}-m2-report" /
                                  "metric_matrix.tsv").read_text())

    def test_metric3_missing_arm_value_is_incomplete_without_view_crash(self):
        requirements = {"metric1": {"repetitions": []},
                        "metric2": {"repetitions": [], "testcases": []},
                        "metric3": {"mode": "independent", "repetitions": ["r1"],
                                    "testcases": list(MOD.M3),
                                    "arms": ["ourcc", "ha-vi"]}}
        matrix = MOD.Metric123RawLogMatrix(requirements, base_dir=self.root)
        for tc in MOD.M3:
            for arm, tick_base in (("ourcc", 100), ("ha-vi", 130)):
                run = self.make_m3_run(f"missing-arm-{tc}-{arm}", tc, "r1",
                                       arm, tick_base, pair=f"p{tc}", order="AB")
                self.assertEqual(matrix.add(run)["status"],
                    "ADDED")
        target = next(run for run in matrix._resolved
                      if run["tc"] == 228 and run["arm"] == "ha-vi")
        target["metrics"]["remote_read"]["ticks_per_operation"] = None
        target["metrics"]["remote_read"]["ns_per_operation"] = None
        result = matrix.finalize(self.root / "missing-m3-report")
        self.assertEqual(result["report"]["metric3"]["status"], "INCOMPLETE")
        self.assertTrue(result["report"]["metric3"]["missing_slots"])
        run_row = next(row for row in result["report"]["views"]["all"]["matrix"]
                       if row["identity"] == target["id"])
        self.assertIsNone(run_row["value"])
        pair = next(row for row in result["report"]["views"]["all"]["metric3_pairs"]
                    if row["tc"] == 228)
        self.assertIsNone(pair["metrics"]["remote_read"]["ha_vi_ticks_per_operation"])
        self.assertIsNone(pair["metrics"]["remote_read"]["delta_ticks"])
        self.assertIn("N/A", (self.root / "missing-m3-report" /
                              "metric_matrix_all.tsv").read_text())

    def test_descriptive_nonfinite_and_non_arithmetic_values_render_null(self):
        run = self.make_m2_run("nonfinite-extension", profile="naive")
        run.update(tc=999, topology="1n1s", phase=MOD.M2[135][0],
                   expected_node=MOD.M2[135][2], expected_samples=MOD.M2[135][3])
        matrix = MOD.Metric123RawLogMatrix(correctness_policy="optional",
                                           base_dir=self.root)
        self.assertEqual(matrix.add(run)["status"], "ADDED")
        parsed = matrix._resolved[0]
        parsed["metrics"]["mean_ns"] = float("inf")
        parsed["metrics"]["latency_phases"][MOD.M2[135][0]]["mean_ns"] = "N/A"
        result = matrix.finalize(self.root / "nonfinite-report")
        self.assertIsNone(result["report"]["views"]["all"]["matrix"][0]["value"])
        self.assertIsNone(result["report"]["views"]["extension"]["matrix"][0]["value"])
        self.assertIn("N/A", (self.root / "nonfinite-report" /
                              "metric_matrix_extension.tsv").read_text())
        resolved_json = json.loads((self.root / "nonfinite-report" /
                                    "resolved_runs.json").read_text())
        self.assertIsNone(resolved_json[0]["metrics"]["mean_ns"])

    def test_opt_in_metric1_additional_coordinate_is_formal_and_isolated(self):
        qualification = {"id": "m1-tc132", "metric": 1,
                         "coordinates": [{"tc": 132, "topology": "3n1s",
                                          "home_node": 0, "home_socket": 0}],
                         "repetitions": ["r1"], "ideal_min_capacity": 1000}
        matrix = MOD.Metric123RawLogMatrix(
            {"metric1": {"repetitions": []}, "qualification_sets": [qualification]},
            base_dir=self.root)
        specs = (("naive", 100, None, ()), ("spill", 100, 160, (12000,)),
                 ("ideal", 1000, 0, (10000,)))
        for role, capacity, exact, outer in specs:
            run = self.make_formal_m1("q1-" + role, role, capacity, exact, outer)
            old_sim, old_out = pathlib.Path(run["simulator_log_dir"]), pathlib.Path(run["simout_dir"])
            run.update(tc=132, topology="3n1s")
            for path in old_sim.rglob("*"):
                if path.is_file():
                    path.write_text(path.read_text().replace("tc131", "tc132").replace('"tc": 131', '"tc": 132'))
            verifier = old_sim / "verify_tc131.log"
            verifier.rename(old_sim / "verify_tc132.log")
            exits = old_sim / "child_status_tc131"
            shutil.rmtree(exits)
            self.correctness(old_sim, 132, "3n1s")
            for path in list(old_out.rglob("*tc131*")):
                path.rename(path.with_name(path.name.replace("tc131", "tc132")))
            self.assertEqual(matrix.add(run)["status"], "ADDED")
        result = matrix.finalize(require_qualifications=["m1-tc132"])
        self.assertEqual(result["report"]["qualifications"][0]["status"], "PASS")
        self.assertEqual(result["report"]["metric1"]["status"], "NOT_REQUESTED")
        self.assertEqual(result["report"]["views"]["formal"]["runs"], 3)
        self.assertEqual(result["report"]["views"]["extension"]["runs"], 0)
        self.assertTrue(all(run["contract_class"] == "extension" and run["formal_contract"]
                            for run in result["resolved_runs"]))

    def test_opt_in_metric2_exact_topology_phase_and_unregistered_extension(self):
        qualification = {"id": "m2-5n", "metric": 2,
                         "coordinates": [{"tc": 135, "topology": "5n1s",
                                          "phase": "formal_phase", "expected_node": 4,
                                          "expected_samples": 7}], "repetitions": ["r1"]}
        matrix = MOD.Metric123RawLogMatrix(
            {"metric2": {"repetitions": [], "testcases": []},
             "qualification_sets": [qualification]}, correctness_policy="optional",
            base_dir=self.root)
        for profile, mean in (("naive", 1000), ("spill-noopt", 900), ("optimized", 800)):
            sim, out = self.root / ("q2-" + profile) / "sim", self.root / ("q2-" + profile) / "out"
            sim.mkdir(parents=True)
            self.write(out / "simout_n4", self.latency(4, "formal_phase", 7, mean))
            matrix.add(metric=2, tc=135, repetition="r1", topology="5n1s", profile=profile,
                       phase="formal_phase", expected_node=4, expected_samples=7,
                       simulator_log_dir=str(sim), simout_dir=str(out))
        unknown = self.make_m2_run("still-extension")
        unknown.update(tc=999, topology="3n1s", phase=MOD.M2[135][0])
        matrix.add(unknown)
        result = matrix.finalize()
        self.assertEqual(result["report"]["qualifications"][0]["status"], "PASS")
        self.assertEqual(sum(bool(run["qualified_contracts"]) for run in result["resolved_runs"]), 3)
        self.assertEqual(result["report"]["views"]["extension"]["runs"], 1)

    def test_opt_in_metric2_multiplane_timer_contract(self):
        qualification = {"id": "m2-portable", "metric": 2,
                         "coordinates": [{"tc": 142, "topology": "3n1s",
                                          "phase": "db_oltp_end_to_end",
                                          "kind": "timer", "reduction": "aggregate",
                                          "expected_nodes": [0, 1, 2],
                                          "expected_count": 30}],
                         "repetitions": ["r1"],
                         "thresholds": {"baseline_applicable_min_ns": 0,
                                        "reduction_pct_min": 10}}
        matrix = MOD.Metric123RawLogMatrix(
            {"metric2": {"repetitions": [], "testcases": []},
             "qualification_sets": [qualification]}, correctness_policy="optional",
            base_dir=self.root)
        for profile, ticks in (("naive", 3000), ("spill-noopt", 2700),
                               ("optimized", 2400)):
            sim = self.root / f"multi-{profile}" / "sim"
            out = self.root / f"multi-{profile}" / "out"
            sim.mkdir(parents=True)
            for node in range(3):
                self.write(out / f"simout_n{node}", self.timer(
                    node, "db_oltp_end_to_end", ticks=ticks, count=10))
            added = matrix.add(
                metric=2, tc=142, repetition="r1", topology="3n1s",
                profile=profile, phase="db_oltp_end_to_end",
                simulator_log_dir=str(sim), simout_dir=str(out))
            self.assertEqual(added["status"], "ADDED")
        result = matrix.finalize(require_qualifications=["m2-portable"])
        qualification_result = result["report"]["qualifications"][0]
        self.assertEqual(qualification_result["status"], "PASS")
        case = qualification_result["results"][0]["cases"][0]
        self.assertEqual(case["baseline_mean_ns"], 300)
        self.assertEqual(case["result_mean_ns"], 240)
        self.assertAlmostEqual(case["reduction_pct"], 20)

    def test_metric2_standard_can_also_match_qualification_without_reparse(self):
        phase, topology, node, samples = MOD.M2[135]
        qualification = {"id": "m2-standard-shadow", "metric": 2,
                         "coordinates": [{"tc": 135, "topology": topology,
                                          "phase": phase, "expected_node": node,
                                          "expected_samples": samples}],
                         "repetitions": ["r1"]}
        matrix = MOD.Metric123RawLogMatrix(
            {"metric2": {"repetitions": ["r1"], "testcases": [135]},
             "qualification_sets": [qualification]}, base_dir=self.root)
        for profile, mean in (("naive", 1000), ("spill-noopt", 900),
                              ("optimized", 800)):
            matrix.add(self.make_m2_run("shadow-" + profile, profile=profile,
                                        mean=mean))
        result = matrix.finalize()
        self.assertEqual(result["report"]["views"]["standard"]["runs"], 3)
        self.assertEqual(result["report"]["qualifications"][0]["status"], "PASS")
        self.assertTrue(all(run["standard_contract"] and run["formal_contract"]
                            for run in result["resolved_runs"]))

    def test_qualification_schema_is_under_requirements(self):
        schema = json.loads((ROOT / "scripts/metric123_raw_manifest.schema.json")
                            .read_text())
        requirements = schema["properties"]["requirements"]["properties"]
        run_properties = schema["properties"]["runs"]["items"]["properties"]
        self.assertIn("qualification_sets", requirements)
        self.assertNotIn("qualification_sets", run_properties)

    def test_opt_in_metric3_topologies_never_mix_and_missing_is_per_topology(self):
        paired = {"id": "m3-3n-paired", "metric": 3, "mode": "paired",
                  "topologies": ["3n1s"], "testcases": [228],
                  "arms": ["ourcc", "ha-vi"], "pairs": ["p1"]}
        independent = {"id": "m3-8n-independent", "metric": 3,
                       "mode": "independent", "topologies": ["8n1s"],
                       "testcases": [228], "arms": ["ourcc", "ha-vi"],
                       "repetitions": ["r1", "r2"]}
        matrix = MOD.Metric123RawLogMatrix(
            {"metric3": {"testcases": []},
             "qualification_sets": [paired, independent]}, base_dir=self.root)
        matrix.add(self.make_m3_run("p-o", 228, "r1", "ourcc", 100,
                                    pair="p1", order="AB", topology="3n1s"))
        matrix.add(self.make_m3_run("p-h", 228, "r1", "ha-vi", 130,
                                    pair="p1", order="AB", topology="3n1s"))
        for rep, base in (("r1", 200), ("r2", 220)):
            matrix.add(self.make_m3_run("i-o-" + rep, 228, rep, "ourcc", base,
                                        topology="8n1s"))
        result = matrix.finalize()
        by_id = {item["id"]: item for item in result["report"]["qualifications"]}
        self.assertEqual(by_id["m3-3n-paired"]["status"], "PASS")
        self.assertEqual(by_id["m3-3n-paired"]["results"][0]["primary_values"][0]
                         ["delta_mean_ticks"], 30)
        self.assertEqual(by_id["m3-8n-independent"]["status"], "INCOMPLETE")
        self.assertTrue(all(item["topology"] == "8n1s"
                            for item in by_id["m3-8n-independent"]["missing_slots"]))

    def test_metric3_tc232_qualification_uses_topology_operation_mix(self):
        qualifications = [
            {"id": "m3-2n", "metric": 3, "mode": "paired",
             "topologies": ["2n1s"], "testcases": [232],
             "arms": ["ourcc", "ha-vi"], "pairs": ["p1"]},
            {"id": "m3-3n", "metric": 3, "mode": "paired",
             "topologies": ["3n1s"], "testcases": [232],
             "arms": ["ourcc", "ha-vi"], "pairs": ["p1"]},
        ]
        matrix = MOD.Metric123RawLogMatrix(
            {"metric3": {"testcases": []}, "qualification_sets": qualifications},
            base_dir=self.root)
        for topology in ("2n1s", "3n1s"):
            matrix.add(self.make_m3_run(
                f"{topology}-o", 232, "r1", "ourcc", 100,
                pair="p1", order="AB", topology=topology))
            matrix.add(self.make_m3_run(
                f"{topology}-h", 232, "r1", "ha-vi", 130,
                pair="p1", order="AB", topology=topology))
        for run in matrix._resolved:
            read = run["metrics"]["hot_key_read"]
            write = run["metrics"]["hot_key_write"]
            if run["arm"] == "ourcc":
                read["ticks_per_operation"] = 10
                write["ticks_per_operation"] = 20
            else:
                read["ticks_per_operation"] = 20
                write["ticks_per_operation"] = 60
        report = matrix.finalize()["report"]
        by_id = {item["id"]: item for item in report["qualifications"]}
        delta_2n = by_id["m3-2n"]["results"][0]["primary_values"][0]["delta_mean_ticks"]
        delta_3n = by_id["m3-3n"]["results"][0]["primary_values"][0]["delta_mean_ticks"]
        self.assertAlmostEqual(delta_2n, 20)
        self.assertAlmostEqual(delta_3n, 17.5)
        self.assertEqual(MOD.metric3_primary_weights(232, "2n1s"),
                         {"hot_key_read": 2 / 3, "hot_key_write": 1 / 3})
        self.assertEqual(MOD.metric3_primary_weights(232, "3n1s"),
                         {"hot_key_read": 3 / 4, "hot_key_write": 1 / 4})

    def test_metric3_paired_qualification_averages_all_pairs(self):
        qualification = {"id": "m3-two-pairs", "metric": 3, "mode": "paired",
                         "topologies": ["3n1s"], "testcases": [228],
                         "arms": ["ourcc", "ha-vi"], "pairs": ["p1", "p2"]}
        matrix = MOD.Metric123RawLogMatrix(
            {"metric3": {"testcases": []}, "qualification_sets": [qualification]},
            base_dir=self.root)
        for pair, ourcc, havi in (("p1", 100, 110), ("p2", 100, 130)):
            matrix.add(self.make_m3_run(
                pair + "-o", 228, pair, "ourcc", ourcc,
                pair=pair, order="AB", topology="3n1s"))
            matrix.add(self.make_m3_run(
                pair + "-h", 228, pair, "ha-vi", havi,
                pair=pair, order="AB", topology="3n1s"))
        item = matrix.finalize()["report"]["qualifications"][0]
        self.assertEqual(item["status"], "PASS")
        self.assertEqual(item["results"][0]["primary_values"][0]
                         ["delta_mean_ticks"], 20)

    def test_paired_qualification_does_not_change_standard_independent_slots(self):
        qualification = {"id": "m3-paired-shadow", "metric": 3, "mode": "paired",
                         "topologies": ["2n1s"], "testcases": [228],
                         "arms": ["ourcc", "ha-vi"], "pairs": ["p1"]}
        matrix = MOD.Metric123RawLogMatrix(
            {"metric3": {"mode": "independent", "repetitions": ["r1", "r2"],
                         "testcases": [228], "arms": ["ourcc", "ha-vi"]},
             "qualification_sets": [qualification]}, base_dir=self.root)
        for repetition in ("r1", "r2"):
            for arm, base in (("ourcc", 100), ("ha-vi", 130)):
                matrix.add(self.make_m3_run(
                    f"{repetition}-{arm}", 228, repetition, arm, base))
        result = matrix.finalize()
        self.assertFalse(any(issue["code"] == "DUPLICATE_SLOT"
                             for issue in result["issues"]))
        self.assertEqual(result["report"]["views"]["standard"]["runs"], 4)
        self.assertEqual(result["report"]["metric3"]["comparison_mode"],
                         "independent")

    def test_metric3_qualification_requires_exact_two_arms(self):
        bad = {"qualification_sets": [{"id": "bad-arms", "metric": 3,
               "mode": "independent", "topologies": ["3n1s"],
               "testcases": [228], "arms": ["ourcc"],
               "repetitions": ["r1"]}]}
        with self.assertRaisesRegex(MOD.ExtractError, "exactly ourcc and ha-vi"):
            MOD.Metric123RawLogMatrix(bad)

    def test_qualification_registry_merge_conflict_and_pickle_v1_v2(self):
        left_req = {"qualification_sets": [{"id": "same", "metric": 2,
                    "coordinates": [{"tc": 135, "topology": "3n1s", "phase": "a",
                                     "expected_node": 1, "expected_samples": 2}],
                    "repetitions": ["r1"]}]}
        right_req = json.loads(json.dumps(left_req))
        right_req["qualification_sets"][0]["coordinates"][0]["phase"] = "b"
        with self.assertRaisesRegex(MOD.ExtractError, "conflicting qualification"):
            MOD.Metric123RawLogMatrix(left_req) + MOD.Metric123RawLogMatrix(right_req)
        matrix = MOD.Metric123RawLogMatrix(left_req, base_dir=self.root)
        state2 = matrix.__getstate__()
        self.assertEqual(state2["version"], 2)
        restored2 = pickle.loads(pickle.dumps(matrix))
        self.assertEqual(restored2._qualification_sets, matrix._qualification_sets)
        state1 = dict(state2, version=1)
        state1.pop("qualification_sets")
        restored1 = object.__new__(MOD.Metric123RawLogMatrix)
        with mock.patch.object(MOD, "open_text", side_effect=AssertionError("source read")):
            restored1.__setstate__(state1)
        self.assertEqual(restored1._qualification_sets, matrix._qualification_sets)

    def test_qualification_is_nonintrusive_until_explicitly_required(self):
        qualification = {"id": "missing-q", "metric": 3, "mode": "independent",
                         "topologies": ["8n1s"], "testcases": [228],
                         "repetitions": ["r1"]}
        matrix = MOD.Metric123RawLogMatrix(
            {"metric1": {"repetitions": []},
             "metric2": {"repetitions": [], "testcases": []},
             "metric3": {"testcases": []},
             "qualification_sets": [qualification]}, base_dir=self.root)
        ordinary = matrix.finalize()
        required = matrix.finalize(require_qualifications=["missing-q"])
        unknown = matrix.finalize(require_qualifications=["not-registered"])
        self.assertEqual(ordinary["exit_code"], 0)
        self.assertEqual(ordinary["report"]["overall_status"], "PASS")
        self.assertEqual(ordinary["report"]["qualifications"][0]["status"],
                         "INCOMPLETE")
        self.assertEqual(required["exit_code"], 3)
        self.assertEqual(unknown["exit_code"], 2)
        self.assertEqual(required["report"]["overall_status"], "PASS")

    def test_markdown_reports_formal_view_and_qualification_status(self):
        qualification = {"id": "missing-q", "metric": 3, "mode": "independent",
                         "topologies": ["8n1s"], "testcases": [228],
                         "repetitions": ["r1"]}
        matrix = MOD.Metric123RawLogMatrix(
            {"metric1": {"repetitions": []},
             "metric2": {"repetitions": [], "testcases": []},
             "metric3": {"testcases": []},
             "qualification_sets": [qualification]}, base_dir=self.root)
        output = self.root / "formal-report"
        matrix.finalize(output)
        markdown = (output / "report.md").read_text()
        self.assertIn("Formal runs (standard + configured qualifications): 0", markdown)
        self.assertIn("| missing-q | 3 | INCOMPLETE | 0 |", markdown)
        self.assertIn("## 逐测试诊断", markdown)
        self.assertIn("## 未接纳的测试", markdown)
        self.assertIn("## 未满足的矩阵要求", markdown)
        self.assertIn("资格合同 `missing-q`", markdown)

    def test_markdown_explains_extension_and_rejected_run_reasons(self):
        matrix = MOD.Metric123RawLogMatrix(
            correctness_policy="optional", base_dir=self.root)
        extension = self.make_m3_run("diagnostic-extension", 228, "r1",
                                     "ourcc", topology="3n1s")
        self.assertEqual(matrix.add(extension)["status"], "ADDED")
        rejected = self.make_m2_run("diagnostic-rejected")
        (pathlib.Path(rejected["simout_dir"]) / "simout_n1").write_text("")
        self.assertEqual(matrix.add(rejected)["status"], "REJECTED")
        output = self.root / "diagnostic-report"
        matrix.finalize(output)
        markdown = (output / "report.md").read_text()
        self.assertIn("`diagnostic-extension`", markdown)
        self.assertIn("Extension", markdown)
        self.assertIn("当前未配置 qualification_sets", markdown)
        self.assertIn("topology=3n1s expected=2n1s", markdown)
        self.assertIn("`diagnostic-rejected`", markdown)
        self.assertIn("EVIDENCE_INVALID", markdown)
        self.assertIn("Metric2 expected exactly one phase=preserved_sharer_first_load, got 0",
                      markdown)


if __name__ == "__main__":
    unittest.main()
