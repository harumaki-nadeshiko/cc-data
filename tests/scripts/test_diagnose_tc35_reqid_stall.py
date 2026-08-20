#!/usr/bin/env python3

import argparse
import importlib.util
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/diagnose_tc35_reqid_stall.py"
SPEC = importlib.util.spec_from_file_location("diagnose_tc35_reqid_stall", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DiagnoseTc35Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative, lines):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")
        return path

    @staticmethod
    def args():
        return argparse.Namespace(
            max_files=2000,
            max_total_bytes=64 * 1024 * 1024,
            max_line_bytes=64 * 1024,
            max_mismatches=100,
            max_events=100000,
            sample_limit=3,
            excerpt_bytes=320,
            best_effort_io=False,
        )

    def build_full_chain(self, repeats=1000):
        old_req = 41
        new_req = 42
        pa = "0x100"
        self.write("ubio_tc35_n0_s0/stdout.log", [
            f"[UBCC-OUTER-REQ] home=0 pa={pa} requester=1 reqId={old_req}",
            f"[UBCC-GRANT-READY] home=0 pa={pa} requester=1 reqId={old_req}",
            f"[TRACE-PERF] 100|1|ubio|{old_req}|{pa}|SEND_NET|ReadResp",
            f"[TRACE-PERF] 130|0|ubio|{old_req}|{pa}|RECV_NET|ClearReq",
            f"[HOME-CLEAR-RESULT] home=0 pa={pa} reqId={old_req} accepted=1",
            f"[TRACE-PERF] 140|1|ubio|{old_req}|{pa}|SEND_NET|ClearResp",
            f"[UBCC-OUTER-REQ] home=0 pa={pa} requester=1 reqId={new_req}",
        ] + [
            "[UBCC-GRANT-RETRY-TUPLE-MISMATCH] "
            f"home=0 pa={pa} requester=1 incomingSocket=0 "
            f"incomingReqId={new_req} outstandingSocket=0 "
            f"outstandingReqId={old_req}"
            for _ in range(repeats)
        ])
        self.write("nsim_tc35.log", [
            f"[TRACE-PERF] 100|0|nsim|{old_req}|0x0|RECV|src=0 dst=1",
            f"[TRACE-PERF] 105|1|nsim|{old_req}|0x0|FWD|dst=1",
            f"[TRACE-PERF] 120|0|nsim|{old_req}|0x0|RECV|src=1 dst=0",
            f"[TRACE-PERF] 125|0|nsim|{old_req}|0x0|FWD|dst=0",
            f"[TRACE-PERF] 140|1|nsim|{old_req}|0x0|RECV|src=0 dst=1",
            f"[TRACE-PERF] 145|1|nsim|{old_req}|0x0|FWD|dst=1",
        ])
        self.write("ubio_tc35_n1_s0/stdout.log", [
            f"[TRACE-PERF] 110|1|ubio|{old_req}|{pa}|RECV_NET|ReadResp",
            f"[TRACE-PERF] 111|1|ubio|{old_req}|{pa}|SEND_GEM5|ReadResp",
            f"[TRACE-PERF] 119|1|ubio|{old_req}|{pa}|RECV_GEM5|ClearReq",
            f"[TRACE-PERF] 120|0|ubio|{old_req}|{pa}|SEND_NET|ClearReq",
            f"[TRACE-PERF] 150|1|ubio|{old_req}|{pa}|RECV_NET|ClearResp",
            f"[TRACE-PERF] 151|1|ubio|{old_req}|{pa}|SEND_GEM5|ClearResp",
        ])
        self.write("gem5_tc35_node1/stderr.log", [
            f"[ADAPTER-GOT-RESP] node=1 type=ReadResp pa={pa} reqId={old_req}",
            f"savePendingGrantTxn node=1 keyPA={pa} homePA={pa} reqId={old_req}",
            f"[CLEAR-SEND] node=1 pa={pa} reqId={old_req}",
            f"[CLEAR-RESP] node=1 pa={pa} reqId={old_req}",
            f"[CLR-CACHE-HIT] node=1 reqId={old_req} accepted=1",
        ])

    def test_repeated_mismatch_is_deduplicated_and_chain_is_complete(self):
        self.build_full_chain()
        report = MODULE.scan(self.root, self.args())
        self.assertEqual(len(report["mismatches"]), 1)
        item = report["mismatches"][0]
        self.assertEqual(item["mismatch"]["occurrences"], 1000)
        self.assertEqual(item["relation"], "consecutive_new_reqid")
        counts = item["old_chain"]["counts"]
        for stage in (
                "HRR", "HG", "HUSN.RR", "NR.RR", "NF.RR", "RURN.RR",
                "RUSG.RR", "AR", "PG", "CS", "RURG.CQ", "RUSN.CQ",
                "NR.CQ", "NF.CQ", "HURN.CQ", "HC", "HUSN.CR", "NR.CR",
                "NF.CR", "RURN.CR", "RUSG.CR", "CR", "CH"):
            self.assertGreater(counts[stage], 0, stage)
        self.assertEqual(item["new_chain"]["counts"]["HRR"], 1)

    def test_no_mismatch_scan_is_valid(self):
        self.write("plain.log", ["[TC35_PROGRESS] node=0 iter=64"])
        report = MODULE.scan(self.root, self.args())
        self.assertEqual(report["mismatches"], [])
        self.assertEqual(report["files_scanned"], 1)

    def test_reqid_plus_two_identifies_intervening_transaction(self):
        self.write("ubio_tc98_n0_s0/stdout.log", [
            "[UBCC-GRANT-RETRY-TUPLE-MISMATCH] home=0 pa=0x100 requester=1 "
            "incomingSocket=0 incomingReqId=43 outstandingSocket=0 "
            "outstandingReqId=41",
        ])
        report = MODULE.scan(self.root, self.args())
        self.assertEqual(
            report["mismatches"][0]["relation"],
            "new_reqid_after_one_intervening_txn")

    def test_compact_counts_follow_stage_order(self):
        summaries = {stage: MODULE.StageSummary() for stage in MODULE.STAGE_ORDER}
        summaries["HRR"].count = 1
        summaries["HC"].count = 2
        values = MODULE.compact_counts(summaries).split(",")
        self.assertEqual(len(values), len(MODULE.STAGE_ORDER))
        self.assertEqual(values[0], "1")
        self.assertEqual(values[MODULE.STAGE_ORDER.index("HC")], "2")

    def test_pa_filter_does_not_cross_contaminate(self):
        self.build_full_chain(repeats=1)
        with (self.root / "ubio_tc35_n0_s0/stdout.log").open("a") as stream:
            stream.write(
                "[UBCC-GRANT-READY] home=0 pa=0x200 requester=1 reqId=41\n")
        report = MODULE.scan(self.root, self.args())
        self.assertEqual(report["mismatches"][0]["old_chain"]["counts"]["HG"], 1)

    def test_same_reqid_pa_other_socket_does_not_cross_contaminate(self):
        self.build_full_chain(repeats=1)
        self.write("ubio_tc35_n0_s1/other.log", [
            "[UBCC-GRANT-READY] home=0 pa=0x100 requester=1 reqId=41",
        ])
        report = MODULE.scan(self.root, self.args())
        self.assertEqual(report["mismatches"][0]["old_chain"]["counts"]["HG"], 1)

    def test_unknown_pa_is_not_assigned_when_process_has_multiple_pas(self):
        self.build_full_chain(repeats=1)
        with (self.root / "gem5_tc35_node1/stderr.log").open("a") as stream:
            stream.write("[ADAPTER-GOT-RESP] node=1 type=ReadResp pa=0x200 reqId=41\n")
            stream.write("[CLR-CACHE-HIT] node=1 reqId=41 accepted=1\n")
        report = MODULE.scan(self.root, self.args())
        self.assertEqual(report["mismatches"][0]["old_chain"]["counts"]["CH"], 0)

    def test_missing_clear_response_is_reported_after_home_commit(self):
        self.build_full_chain(repeats=1)
        for relative in (
                "ubio_tc35_n0_s0/stdout.log",
                "ubio_tc35_n1_s0/stdout.log",
                "gem5_tc35_node1/stderr.log",
                "nsim_tc35.log"):
            path = self.root / relative
            lines = [line for line in path.read_text().splitlines()
                     if "ClearResp" not in line and "CLR-CACHE-HIT" not in line]
            path.write_text("\n".join(lines) + "\n")
        report = MODULE.scan(self.root, self.args())
        self.assertEqual(
            report["mismatches"][0]["likely_break"],
            "after_HC_before_HUSN.CR")

    def test_rejected_clear_cache_hit_is_not_completion(self):
        self.build_full_chain(repeats=1)
        path = self.root / "gem5_tc35_node1/stderr.log"
        lines = [line.replace("accepted=1", "accepted=0")
                 for line in path.read_text().splitlines()]
        path.write_text("\n".join(lines) + "\n")
        report = MODULE.scan(self.root, self.args())
        item = report["mismatches"][0]
        self.assertEqual(item["old_chain"]["counts"]["CH"], 0)
        self.assertEqual(item["old_chain"]["counts"]["CJ"], 1)
        self.assertEqual(item["likely_break"], "clear_response_rejected")
        self.assertEqual(item["clear_resolution"], "CLEAR_REJECTED")

    def test_legacy_ingress_after_mismatch_marks_transient_resolution(self):
        self.write("ubio_tc98_n0_s0/stdout.log", [
            "[UBCC-GRANT-RETRY-TUPLE-MISMATCH] home=0 pa=0x100 requester=1 "
            "incomingSocket=0 incomingReqId=43 outstandingSocket=0 "
            "outstandingReqId=41",
            "[HOME-CLEAR-COMMIT] home=0 pa=0x100 reqId=41",
        ])
        self.write("gem5_tc98_node1/stderr.log", [
            "[CLEAR-SEND] node=1 pa=0x100 reqId=41",
            "[CLR-CACHE-HIT] node=1 reqId=41 accepted=1",
        ])
        report = MODULE.scan(self.root, self.args())
        self.assertEqual(
            report["mismatches"][0]["clear_resolution"],
            "TRANSIENT_RESOLVED_AFTER_MISMATCH")

    def test_mismatch_after_legacy_ingress_is_post_clear_suspect(self):
        self.write("ubio_tc98_n0_s0/stdout.log", [
            "[HOME-CLEAR-COMMIT] home=0 pa=0x100 reqId=41",
            "[UBCC-GRANT-RETRY-TUPLE-MISMATCH] home=0 pa=0x100 requester=1 "
            "incomingSocket=0 incomingReqId=43 outstandingSocket=0 "
            "outstandingReqId=41",
        ])
        self.write("gem5_tc98_node1/stderr.log", [
            "[CLEAR-SEND] node=1 pa=0x100 reqId=41",
            "[CLR-CACHE-HIT] node=1 reqId=41 accepted=1",
        ])
        report = MODULE.scan(self.root, self.args())
        self.assertEqual(
            report["mismatches"][0]["clear_resolution"],
            "POST_CLEAR_INGRESS_MISMATCH_OR_REPLAY")

    def test_true_accept_after_mismatch_marks_transient(self):
        self.write("ubio_tc98_n0_s0/stdout.log", [
            "[UBCC-GRANT-RETRY-TUPLE-MISMATCH] home=0 pa=0x100 requester=1 "
            "incomingSocket=0 incomingReqId=43 outstandingSocket=0 "
            "outstandingReqId=41",
            "[HOME-CLEAR-RESULT] home=0 pa=0x100 reqId=41 accepted=1",
        ])
        self.write("gem5_tc98_node1/stderr.log", [
            "[CLEAR-SEND] node=1 pa=0x100 reqId=41",
            "[CLR-CACHE-HIT] node=1 reqId=41 accepted=1",
        ])
        report = MODULE.scan(self.root, self.args())
        self.assertEqual(
            report["mismatches"][0]["clear_resolution"],
            "TRANSIENT_RESOLVED_AFTER_MISMATCH")

    def test_mismatch_after_true_accept_is_recreated_tuple(self):
        self.write("ubio_tc98_n0_s0/stdout.log", [
            "[HOME-CLEAR-RESULT] home=0 pa=0x100 reqId=41 accepted=1",
            "[UBCC-GRANT-RETRY-TUPLE-MISMATCH] home=0 pa=0x100 requester=1 "
            "incomingSocket=0 incomingReqId=43 outstandingSocket=0 "
            "outstandingReqId=41",
        ])
        self.write("gem5_tc98_node1/stderr.log", [
            "[CLEAR-SEND] node=1 pa=0x100 reqId=41",
            "[CLR-CACHE-HIT] node=1 reqId=41 accepted=1",
        ])
        report = MODULE.scan(self.root, self.args())
        self.assertEqual(
            report["mismatches"][0]["clear_resolution"],
            "POST_ACCEPT_MISMATCH_RECREATED_OLD_TUPLE")

    def test_long_line_preserves_prefix_marker(self):
        marker = (
            "[UBCC-GRANT-RETRY-TUPLE-MISMATCH] home=0 pa=0x100 requester=1 "
            "incomingSocket=0 incomingReqId=42 outstandingSocket=0 "
            "outstandingReqId=41 " + "x" * 10000)
        self.write("ubio_tc35_n0_s0/long.log", [marker])
        args = self.args()
        args.max_line_bytes = 512
        report = MODULE.scan(self.root, args)
        self.assertEqual(len(report["mismatches"]), 1)
        self.assertEqual(report["truncated_lines"], 1)


if __name__ == "__main__":
    unittest.main()
