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
        self.assertEqual(m3.finalize()["resolved_runs"][0]["contract_class"], "extension")

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

    def make_m3_run(self, run_id, tc, repetition, arm=None, tick_base=100,
                    identity=None, pair=None, order=None):
        sim, out = self.root / run_id / "sim", self.root / run_id / "out"
        self.correctness(sim, tc)
        if identity:
            self.write(sim / "identity.log", identity)
        text = ""
        for index, (_, (kind, phase, _)) in enumerate(MOD.M3[tc].items()):
            ticks = (tick_base + index * 10) * 10
            text += (self.timer(0, phase, ticks) if kind == "timer" else
                     self.latency(0, phase, 10, tick_base + index * 10))
        self.write(out / "simout_n0", text)
        run = {"id": run_id, "metric": 3, "tc": tc, "repetition": repetition,
               "topology": "2n1s", "simulator_log_dir": str(sim),
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


if __name__ == "__main__":
    unittest.main()
