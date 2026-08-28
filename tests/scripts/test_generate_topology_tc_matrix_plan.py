#!/usr/bin/env python3

import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/generate_topology_tc_matrix_plan.py"
SPEC = importlib.util.spec_from_file_location("generate_topology_tc_matrix_plan", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class TopologyTcMatrixPlanTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.first = self.root / "first"
        MODULE.write_plan(self.first, 3, 5)
        self.execution = json.loads((self.first / "execution_plan.json").read_text())
        self.qualification = json.loads((self.first / "qualification_manifest.json").read_text())
        self.formal = json.loads((self.first / "formal_manifest.json").read_text())

    def tearDown(self):
        self.temp.cleanup()

    def test_exact_topology_tc_cross_products_and_counts(self):
        expected_topologies = {"2n1s", "3n1s", "3n2s", "8n1s", "8n2s", "16n1s"}
        self.assertEqual({row["id"] for row in self.execution["topologies"]}, expected_topologies)
        self.assertEqual(self.execution["testcases"]["portable"], list(range(142, 148)))
        self.assertEqual(self.execution["testcases"]["metric3"], list(range(228, 236)))
        qjobs = self.qualification["jobs"]
        portable_slots = {(row["topology"], row["tc"]) for row in qjobs
                          if row["family"] == "portable"}
        metric_slots = {(row["topology"], row["tc"]) for row in qjobs
                        if row["family"] == "metric3"}
        self.assertEqual(len(portable_slots), 6 * 6)
        self.assertEqual(len(metric_slots), 6 * 8)
        self.assertEqual(len(qjobs), 6 * 6 + 6 * 8 * 2)
        self.assertEqual(self.formal["job_count"], 6 * 6 * 4 * 3 + 6 * 8 * 5 * 2)

    def test_no_duplicate_job_ids(self):
        jobs = self.execution["jobs"]
        self.assertEqual(len(jobs), len({row["job_id"] for row in jobs}))

    def test_every_metric3_pair_has_exactly_two_distinct_arms(self):
        for manifest in (self.qualification, self.formal):
            grouped = {}
            for row in manifest["jobs"]:
                if row["family"] == "metric3":
                    grouped.setdefault(row["pair_id"], []).append(row)
            self.assertTrue(grouped)
            for rows in grouped.values():
                self.assertEqual(len(rows), 2)
                self.assertEqual({row["arm"] for row in rows}, {"ourcc", "ha-vi"})
                self.assertEqual({row["sequence_index"] for row in rows}, {1, 2})
                self.assertEqual(len({row["pair_order"] for row in rows}), 1)
                if manifest["tier"] == "formal":
                    self.assertEqual({row["repetition"] for row in rows},
                                     {f"p{rows[0]['pair'][1:]}"})

    def test_portable_family_never_plans_ha_vi(self):
        portable = [row for row in self.execution["jobs"] if 142 <= row["tc"] <= 147]
        self.assertTrue(portable)
        self.assertTrue(all(row.get("arm") != "ha-vi" for row in portable))
        self.assertTrue(all("HA-VI" in row["ha_vi_support"] or "central-home" in row["ha_vi_support"]
                            for row in portable))
        ideal = [row for row in self.formal["jobs"]
                 if row["family"] == "portable" and row.get("metric1_role") == "ideal"]
        self.assertEqual(len(ideal), 6 * 6 * 3)
        self.assertTrue(all("--allow-oversized-resident-dir-for-test" in
                            row["command"] for row in ideal))

    def test_tier_paths_are_isolated_and_formal_has_qualification_gate(self):
        for row in self.execution["jobs"]:
            self.assertTrue(row["result_path"].startswith(f"/results/{row['tier']}/cases/"))
            if row["tier"] == "formal":
                self.assertEqual(row["source"]["result_tier"], "qualification")
                self.assertEqual(row["depends_on"], [row["qualification_id"]])
                self.assertNotIn("/smoke/", row["result_path"])
                self.assertNotIn("/qualification/", row["result_path"])

    def test_commands_are_docker_only_offline_and_use_exact_topology_flags(self):
        flags = {row["id"]: row["runner_flag"] for row in self.execution["topologies"]}
        for row in self.execution["jobs"]:
            command = row["command"]
            self.assertTrue(command.startswith("docker run "))
            self.assertIn("--network none", command)
            self.assertIn("ubcc-dev:ubuntu20.04", command)
            self.assertIn(f" {flags[row['topology']]} {row['tc']}", command)
            self.assertIn("E2E_RUN_ID=", command)
            self.assertIn("LOG_BASE=/results/", command)
            self.assertIn("RESULT_TIER=", command)
            self.assertIn("QUALIFICATION_ID=", command)
            self.assertIn("EP_SUPERVISOR_PROGRESS_STALL_SEC=", command)
            self.assertNotIn("E2E_STALL_TIMEOUT_SEC=", command)
            if row["family"] == "metric3":
                self.assertNotIn("HA_EXACT_BYTES=", command)
            self.assertNotIn("docker exec", command)
        self.assertNotIn("subprocess", SCRIPT.read_text().replace("does not import or call\nsubprocess", ""))

    def test_topology_resources_expected_children_and_metric_annotations(self):
        expected = {"2n1s": 5, "3n1s": 7, "3n2s": 10,
                    "8n1s": 17, "8n2s": 25, "16n1s": 33}
        self.assertEqual({row["id"]: row["expected_child_exit_count"]
                          for row in self.execution["topologies"]}, expected)
        for row in self.execution["jobs"]:
            self.assertGreater(row["timeout_sec"], 0)
            self.assertIn(row["resource_class"], {"small", "medium", "large"})
            self.assertEqual(row["expected_child_exit_count"], expected[row["topology"]])
            if row["tc"] == 232:
                formula = row["primary_value_formula"]
                planes = next(item["active_planes"] for item in self.execution["topologies"]
                              if item["id"] == row["topology"])
                self.assertEqual(formula["read_operations"], 16 * planes)
                self.assertEqual(formula["write_operations"], 16)
                self.assertAlmostEqual(formula["read_weight"], planes / (planes + 1))
                self.assertAlmostEqual(formula["write_weight"], 1 / (planes + 1))
            if row["tc"] == 234:
                self.assertIn("serialized-token", row["limitation"])

    def test_dual_socket_ring_annotation_matches_current_workload(self):
        rows = [row for row in self.execution["jobs"]
                if row["topology"] in {"3n2s", "8n2s"} and
                row["tc"] in {228, 229, 233}]
        self.assertTrue(rows)
        self.assertTrue(all("one node ring per socket" in
                            row["dual_socket_ring_semantics"] for row in rows))

    def test_generation_is_byte_identical_across_selected_directories(self):
        second = self.root / "second"
        MODULE.write_plan(second, 3, 5)
        names = {"execution_plan.json", "smoke_manifest.json",
                 "qualification_manifest.json", "formal_manifest.json",
                 "qualification_sets.json", "commands.jsonl"}
        self.assertEqual({path.name for path in self.first.iterdir()}, names)
        self.assertEqual({path.name for path in second.iterdir()}, names)
        for name in names:
            self.assertEqual((self.first / name).read_bytes(), (second / name).read_bytes())

    def test_extractor_qualification_templates_match_formal_jobs(self):
        payload = json.loads((self.first / "qualification_sets.json").read_text())
        requirements = payload["extractor_requirements"]["qualification_sets"]
        self.assertEqual(len(requirements), 6 * 3)
        for topology in self.execution["topologies"]:
            topo = topology["id"]
            metric1 = next(item for item in requirements
                           if item["id"] == f"m1-portable-{topo}")
            metric2 = next(item for item in requirements
                           if item["id"] == f"m2-portable-{topo}")
            metric3 = next(item for item in requirements
                           if item["id"] == f"m3-paired-{topo}")
            self.assertEqual(metric1["repetitions"], ["r1", "r2", "r3"])
            self.assertEqual(metric2["repetitions"], ["r1", "r2", "r3"])
            self.assertEqual(metric3["pairs"], ["p1", "p2", "p3", "p4", "p5"])
            self.assertTrue(all(coordinate["expected_nodes"] ==
                                list(range(topology["active_planes"]))
                                for coordinate in metric2["coordinates"]))


if __name__ == "__main__":
    unittest.main()
