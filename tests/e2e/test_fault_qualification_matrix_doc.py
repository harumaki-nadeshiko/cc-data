#!/usr/bin/env python3
from __future__ import annotations

import csv
import importlib.util
import io
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


matrix = load(ROOT / "tests/e2e/fault_qualification_matrix.py", "fq_matrix")
generator = load(ROOT / "scripts/generate_fault_qualification_matrix_doc.py", "fq_generator")


class FaultQualificationMatrixDocTest(unittest.TestCase):
    def setUp(self):
        self.rows = matrix.resolved_matrix()
        self.by_id = {row["case_id"]: row for row in self.rows}

    def test_52_unique_self_contained_cases(self):
        self.assertEqual(52, len(self.rows))
        self.assertEqual(52, len(self.by_id))
        required = {"case_id", "qualification", "tc", "tcid", "topology",
                    "workload_id", "cpu_allocation", "gem5", "ubio", "networksim",
                    "compile", "fault_rules", "verifier"}
        for row in self.rows:
            self.assertTrue(required.issubset(row))
            self.assertTrue(row["fault_rules"])

    def test_generated_files_match_in_memory_resolution(self):
        expected = generator.generated_contents()
        for path, content in expected.items():
            self.assertTrue(path.exists(), path)
            self.assertEqual(content, path.read_text(encoding="utf-8"), path)
        payload = json.loads(expected[generator.JSON_PATH])
        self.assertEqual(self.rows, payload["cases"])
        tsv_rows = list(csv.DictReader(io.StringIO(expected[generator.TSV_PATH]),
                                       delimiter="\t"))
        self.assertEqual(52, len(tsv_rows))
        self.assertEqual({row["case_id"] for row in self.rows},
                         {row["case_id"] for row in tsv_rows})

    def test_representative_q1_to_q5_exact_args(self):
        q1 = self.by_id["q1-tc47"]
        self.assertEqual("--1s", q1["topology"]["runner_flag"])
        self.assertEqual([], q1["ubio"]["directory_args_per_tc"])
        self.assertIn("--cpu-model=timing", q1["gem5"]["args_per_node"])
        self.assertNotIn("--sequencer-max-outstanding=16", q1["gem5"]["args_per_node"])
        self.assertEqual("tc47_drop_clear:ClearReq:1:0:0:drop::1", q1["fault_rules"])
        self.assertFalse(q1["ubio"]["params"]["ha_vi_only_args_passed"])
        self.assertEqual(65536, q1["networksim"]["params"]["max_pending"])
        self.assertEqual("aarch64-linux-gnu-gcc", q1["compile"]["command_argv"][0])

        q2 = self.by_id["q2-clear-drop3"]
        self.assertEqual("tc148_q2_clear_drop_first_3:ClearReq:0:1:0x10018014800:drop::3",
                         q2["fault_rules"])
        self.assertTrue(q2["verifier"]["effective_checks"]["stable_reqid_per_rule"])

        q3 = self.by_id["q3-clear-request-response"]
        self.assertEqual(
            "tc148_q3_clear_req:ClearReq:0:1:0x10018014800:drop::1;"
            "tc148_q3_clear_resp:ClearResp:1:0:0x10018014800:delay:20000:1",
            q3["fault_rules"])

        q4 = self.by_id["q4-near-outstanding-upgrade"]
        self.assertIn("--sequencer-max-outstanding=16", q4["gem5"]["args_per_node"])
        self.assertEqual(16, q4["gem5"]["params"]["sequencer_max_outstanding"])
        self.assertEqual(1, q4["gem5"]["params"]["ubcc_batch_rs"])

        q5 = self.by_id["q5-16n1s-delay_ack"]
        self.assertEqual("--16n1s", q5["topology"]["runner_flag"])
        self.assertEqual(32, q5["cpu_allocation"]["cpus"])
        self.assertTrue(q5["cpu_allocation"]["exclusive"])
        self.assertEqual("0-31", q5["cpu_allocation"]["runner_cpuset"])
        self.assertIn("-DHA_TOPOLOGY_SCENARIO=3", q5["compile"]["args"])
        self.assertIn("tc230_q5_16n1s_delay_ack_n14", q5["fault_rules"])
        self.assertIn("--num-nodes=16", q5["ubio"]["args_per_node_socket"])


if __name__ == "__main__":
    unittest.main()
