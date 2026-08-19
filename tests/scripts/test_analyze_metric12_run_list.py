#!/usr/bin/env python3

import importlib.util
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/analyze_metric12_run_list.py"
SPEC = importlib.util.spec_from_file_location("analyze_metric12_run_list", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SyntheticAnalyzer(MODULE.Metric12RunListAnalyzer):
    def parse_feature(self, feature):
        target, round_id, case, topology, profile = feature.split("|")
        return MODULE.RunFeature(
            target=target,
            round_id=int(round_id),
            case=case,
            topology=topology,
            profile=profile,
        )


class Metric12RunListAnalyzerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def add_child_exits(self, sim_dir, tc, topology):
        nodes, sockets = (int(value) for value in topology[:-1].split("n"))
        child = sim_dir / f"child_status_tc{tc}"
        for node in range(nodes):
            self.write(child / f"gem5_node{node}.exit", "0\n")
        for node in range(nodes):
            for socket in range(sockets):
                self.write(child / f"ubio_n{node}_s{socket}.exit", "0\n")
        self.write(child / "networksim.exit", "0\n")

    def add_run(self, target, round_id, case, topology, profile,
                timer_ticks=None, latency_mean=None, capacity=None,
                policy=None, exact_live=None):
        key = f"{target}_r{round_id}_{case}_{profile}"
        sim_dir = self.root / key / "logs"
        workload_dir = self.root / key / "m5out"
        nodes = int(topology.split("n", 1)[0])
        for node in range(nodes):
            self.write(workload_dir / f"node{node}" / f"simout_n{node}", "")
        if target == "target1":
            for node, ticks in zip((1, 2), timer_ticks):
                self.write(
                    workload_dir / f"node{node}" / f"simout_n{node}",
                    f"[GUEST-TIMER] node={node} phase=post_pressure_catalog_reuse "
                    f"operations=1 counter_ticks={ticks} "
                    "counter_frequency_hz=1000000000 source=arm_cntvct_el0 "
                    "unit=counter_ticks\n")
            ubio = sim_dir / f"ubio_tc{case[2:]}_n0_s0" / "stdout.log"
            lines = [f"[UBCC-STATE] tick=1 capacity={capacity} policy={policy}\n",
                     f"[UBCC-STATS] {{\"residentCapacity\":{capacity}}}\n"]
            if exact_live is not None:
                lines.append(
                    "[UBCC-STATS] {\"h64ExactLiveKnown\":1,"
                    f"\"h64ExactLiveCount\":{exact_live}}}\n")
            self.write(ubio, "".join(lines))
        else:
            phase = MODULE.TARGET2_PHASES[case]
            contract = MODULE.TARGET2_MARKER_CONTRACT[case]
            self.write(
                workload_dir / f"node{contract['node']}" /
                f"simout_n{contract['node']}",
                f"[PERF-LATENCY] node={contract['node']} phase={phase} "
                f"samples={contract['samples']} "
                f"min={latency_mean} p50={latency_mean} p95={latency_mean} "
                f"p99={latency_mean} max={latency_mean} mean={latency_mean} "
                "counter_frequency_hz=1000000000 source=arm_cntvct_el0 "
                "unit=counter_ticks\n")
        self.write(sim_dir / f"verify_tc{case[2:]}.log",
                   f">>> TC{case[2:]} PASSED <<<\n")
        self.add_child_exits(sim_dir, case[2:], topology)
        return {
            "simulator_log_dir": str(sim_dir),
            "workload_output_dir": str(workload_dir),
            "feature": f"{target}|{round_id}|{case}|{topology}|{profile}",
        }

    def build_full_matrix(self):
        runs = []
        target2_values = {
            "TC135": {"naive": 1000, "spill-noopt": 700, "optimized": 500},
            "TC136": {"naive": 1200, "spill-noopt": 800, "optimized": 600},
            "TC137": {"naive": 800, "spill-noopt": 700, "optimized": 600},
            # Preserve a real negative result in the equal-weight average.
            "TC138": {"naive": 1000, "spill-noopt": 1100, "optimized": 1200},
            "TC139": {"naive": 2000, "spill-noopt": 700, "optimized": 500},
            "TC140": {"naive": 400, "spill-noopt": 350, "optimized": 300},
            # Exactly 500 ns is applicable under the official >= rule.
            "TC217": {"naive": 500, "spill-noopt": 350, "optimized": 250},
        }
        for round_id in (1, 2, 3):
            for profile in MODULE.PROFILES:
                if profile == "naive":
                    capacity, policy, exact, ticks = 65536, "naive", None, (1000, 1000)
                elif profile == "spill-noopt":
                    capacity, policy, exact, ticks = 57344, "spill", 102656, (900, 900)
                else:
                    capacity, policy, exact, ticks = 57344, "spill", 102656, (800, 800)
                runs.append(self.add_run(
                    "target1", round_id, "TC131", "8n1s", profile,
                    timer_ticks=ticks, capacity=capacity, policy=policy,
                    exact_live=exact))
            for case, values in target2_values.items():
                topology = "2n1s" if case == "TC217" else "3n1s"
                for profile in MODULE.PROFILES:
                    runs.append(self.add_run(
                        "target2", round_id, case, topology, profile,
                        latency_mean=values[profile]))
        return runs

    def test_full_matrix_and_custom_feature_parser(self):
        analyzer = SyntheticAnalyzer(self.build_full_matrix(), min_rounds=3)
        report = analyzer.analyze()
        self.assertTrue(report["overall_pass"])
        self.assertAlmostEqual(
            report["target1"]["statistics"]["capacity_ratio"]["mean"],
            102656 / 65536)
        self.assertAlmostEqual(
            report["target1"]["statistics"]["guest_delta_ns_per_operation"]["mean"],
            -100.0)
        self.assertEqual(
            report["target2"]["statistics"]["applicable_cases"],
            ["TC135", "TC136", "TC137", "TC138", "TC139", "TC217"])
        self.assertLess(
            report["target2"]["case_statistics"]["TC138"]
                  ["optimized_reduction_pct"]["mean"], 0)
        self.assertTrue(report["target2"]["statistics"]["applicable_set_stable"])
        self.assertEqual(len(report["resolved_runs"]), 72)

    def test_bad_node_simout_mapping_is_rejected(self):
        run = self.add_run(
            "target1", 1, "TC131", "8n1s", "naive",
            timer_ticks=(1000, 1000), capacity=65536, policy="naive")
        workload = pathlib.Path(run["workload_output_dir"])
        bad = workload / "node7" / "simout_n7"
        bad.rename(workload / "node7" / "simout_n6")
        analyzer = SyntheticAnalyzer([run], min_rounds=1)
        with self.assertRaises(MODULE.EvidenceError):
            analyzer.resolve_runs()

    def test_wrong_official_topology_is_rejected(self):
        analyzer = MODULE.Metric12RunListAnalyzer([], min_rounds=1)
        with self.assertRaises(MODULE.FeatureParseError):
            analyzer._normalize_feature(MODULE.RunFeature(
                target="target2", round_id=1, case="TC217",
                topology="3n1s", profile="naive"), 0, "bad")

    def test_verifier_must_end_in_pass(self):
        run = self.add_run(
            "target1", 1, "TC131", "8n1s", "naive",
            timer_ticks=(1000, 1000), capacity=65536, policy="naive")
        verifier = pathlib.Path(run["simulator_log_dir"]) / "verify_tc131.log"
        verifier.write_text(
            ">>> TC131 PASSED <<<\n>>> TC131 FAILED <<<\n")
        with self.assertRaises(MODULE.EvidenceError):
            SyntheticAnalyzer([run], min_rounds=1).resolve_runs()

    def test_child_status_identity_is_exact(self):
        run = self.add_run(
            "target1", 1, "TC131", "8n1s", "naive",
            timer_ticks=(1000, 1000), capacity=65536, policy="naive")
        child = pathlib.Path(run["simulator_log_dir"]) / "child_status_tc131"
        (child / "unrelated.exit").write_text("0\n")
        with self.assertRaises(MODULE.EvidenceError):
            SyntheticAnalyzer([run], min_rounds=1).resolve_runs()

    def test_target2_marker_contract_is_enforced(self):
        runs = self.build_full_matrix()
        run = next(item for item in runs if
                   item["feature"] == "target2|1|TC135|3n1s|naive")
        workload = pathlib.Path(run["workload_output_dir"])
        correct = workload / "node1" / "simout_n1"
        correct.write_text(correct.read_text().replace(
            "node=1 phase=preserved_sharer_first_load samples=24",
            "node=0 phase=preserved_sharer_first_load samples=8"))
        with self.assertRaises(MODULE.EvidenceError):
            SyntheticAnalyzer(runs, min_rounds=3).analyze()

    def test_ubio_stdout_and_stderr_are_both_parsed(self):
        run = self.add_run(
            "target1", 1, "TC131", "8n1s", "spill-noopt",
            timer_ticks=(900, 900), capacity=57344, policy="spill",
            exact_live=102656)
        ubio_dir = pathlib.Path(run["simulator_log_dir"]) / "ubio_tc131_n0_s0"
        stdout = ubio_dir / "stdout.log"
        lines = stdout.read_text().splitlines()
        exact = [line for line in lines if "h64ExactLiveKnown" in line]
        stdout.write_text("\n".join(
            line for line in lines if "h64ExactLiveKnown" not in line) + "\n")
        (ubio_dir / "stderr.log").write_text("\n".join(exact) + "\n")
        resolved = SyntheticAnalyzer([run], min_rounds=1).resolve_runs()[0]
        coverage = SyntheticAnalyzer._parse_capacity(resolved.ubio_logs)
        self.assertEqual(coverage["effective_unique"], 102656)


if __name__ == "__main__":
    unittest.main()
