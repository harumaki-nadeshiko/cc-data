#!/usr/bin/env python3

import importlib.util
import io
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/diagnose_tc134_timeout.py"
SPEC = importlib.util.spec_from_file_location("diagnose_tc134_timeout", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DiagnoseTc134TimeoutTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, lines):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")

    def guest_lines(self, stalled_writer_plane=6, stalled_progress=0,
                    barrier_stall=False):
        lines = ["[PHASE]      node=0 phase=window_seed status=done"]
        for plane in range(1, 16, 2):
            lines.append(
                f"[PHASE]      node={plane} phase=window_share status=done")
        for plane in range(0, 16, 2):
            progress = stalled_progress if plane == stalled_writer_plane else 8192
            lines.append(
                f"[PROGRESS] node={plane} phase=window_share_barrier_enter iter=0")
            if plane != stalled_writer_plane or not barrier_stall:
                lines.append(
                    f"[PROGRESS] node={plane} phase=window_pressure iter={progress}")
        return lines

    def test_identifies_physical_node_three_writer(self):
        self.write("simout_tc134_node3.log", self.guest_lines(6, 0))
        report = MODULE.analyze(self.root)
        self.assertEqual(report["summary_diagnosis"],
                         "ONE_WRITER_BLOCKS_GLOBAL_POST_SHARE_BARRIER")
        suspect = report["suspects"][0]
        self.assertEqual(suspect["writer_plane"], 6)
        self.assertEqual(suspect["physical_node"], 3)
        self.assertEqual(suspect["paired_sharer_plane"], 7)
        self.assertEqual(suspect["diagnosis"],
                         "EVIDENCE_GAP_ENABLE_TRANSACTION_TRACE_FOR_SUSPECT_RANGE")

    def test_unresolved_fill_in_suspect_range(self):
        self.write("simout_tc134_node3.log", self.guest_lines(6, 2048))
        offset = MODULE.stream_offset(3, 2048)
        self.write("ubio_tc134_n0_s0/stdout.log", [
            f"[RESIDENT-FILL-ISSUED] tick=99 home=0 pa=0x{offset:x} "
            "waiterDepth=1 opKind=0",
        ])
        report = MODULE.analyze(self.root)
        suspect = report["suspects"][0]
        self.assertEqual(suspect["diagnosis"],
                         "CANDIDATE_HOME_RESIDENT_FILL_NOT_COMPLETED")
        self.assertEqual(suspect["suspect_line_begin"], 2048)
        self.assertEqual(len(suspect["unresolved_fills"]), 1)

    def test_separates_barrier_stall_from_first_store_chunk(self):
        self.write("simout_tc134_node3.log", self.guest_lines(6, 0, True))
        report = MODULE.analyze(self.root)
        suspect = report["suspects"][0]
        self.assertEqual(suspect["diagnosis"],
                         "GUEST_STUCK_AT_POST_SHARE_BARRIER")

    def test_custom_progress_step_narrows_offset_range(self):
        self.write("simout_tc134_node3.log", self.guest_lines(6, 64))
        report = MODULE.analyze(self.root, progress_step=64)
        suspect = report["suspects"][0]
        self.assertEqual(suspect["suspect_line_begin"], 64)
        self.assertEqual(suspect["suspect_line_end_exclusive"], 128)

    def test_plane_three_is_node_one_socket_one(self):
        identity = MODULE.plane_identity(3)
        self.assertEqual(identity["physical_node"], 1)
        self.assertEqual(identity["socket"], 1)
        self.assertEqual(identity["role"], "sharer")

    def test_rejects_non_tc134_log_directory(self):
        self.write("unrelated.log", ["tick=99", "[PHASE] node=0 phase=done status=done"])
        report = MODULE.analyze(self.root)
        self.assertEqual(report["summary_diagnosis"], "NO_TC134_EVIDENCE")
        self.assertEqual(report["suspects"], [])

    def test_all_writers_complete_moves_to_next_barrier(self):
        self.write("simout_tc134.log", self.guest_lines(stalled_writer_plane=-1))
        report = MODULE.analyze(self.root)
        self.assertEqual(report["completed_writers"], 8)
        self.assertEqual(
            report["summary_diagnosis"],
            "PRESSURE_COMPLETE_CHECK_POST_PRESSURE_BARRIER_OR_REUSE")
        self.assertEqual(report["suspects"], [])

    def test_guest_progress_is_read_only_from_simout(self):
        self.write("simout_tc134_node3.log", self.guest_lines(6, 1024))
        self.write("ubio_tc134_n3_s0/stdout.log", [
            "[PROGRESS] node=6 phase=window_pressure iter=8192",
            "[PHASE] node=7 phase=window_share status=done",
        ])
        self.write("nsim_tc134.log", [
            "[PROGRESS] node=6 phase=window_pressure iter=8192",
        ])
        report = MODULE.analyze(self.root)
        plane6 = next(row for row in report["planes"] if row["plane"] == 6)
        self.assertEqual(plane6["window_pressure_iter"], 1024)
        self.assertEqual(report["completed_writers"], 7)
        self.assertEqual(report["source_scan"]["files"]["simout"], 1)
        self.assertEqual(report["source_scan"]["files"]["ubio"], 1)
        self.assertEqual(report["source_scan"]["files"]["nsim"], 1)

    def test_resident_markers_are_read_only_from_ubio(self):
        self.write("simout_tc134_node3.log", self.guest_lines(6, 2048) + [
            "[RESIDENT-FILL-ISSUED] tick=99 home=0 pa=0x1180000 "
            "waiterDepth=1 opKind=0",
        ])
        report = MODULE.analyze(self.root)
        self.assertEqual(report["suspects"][0]["unresolved_fills"], [])

    def test_trace_component_must_match_log_source(self):
        self.write("simout_tc134_node3.log", self.guest_lines(6, 0))
        self.write("ubio_tc134_n0_s0/stdout.log", [
            "[TRACE-PERF] 10|0|gem5|55|0x1180000|SEND|ReadReq",
        ])
        report = MODULE.analyze(self.root)
        self.assertFalse(report["trace_available"])
        self.assertEqual(
            report["source_scan"]["rejected_cross_source_traces"], 1)

    def test_flat_remote_log_names_are_fuzzy_classified(self):
        self.write("run_simout_tc134_node3_stdout.log",
                   self.guest_lines(6, 3072))
        offset = MODULE.stream_offset(3, 3072)
        self.write("remote_ubio_tc134_n0_s0_stdout.log", [
            f"[RESIDENT-FILL-ISSUED] tick=99 home=0 pa=0x{offset:x} "
            "waiterDepth=1 opKind=0",
        ])
        self.write("remote_gem5_tc134_node3_stderr.log", [
            f"[TRACE-PERF] 100|3|gem5|55|0x{offset:x}|SEND|ReadReq",
        ])
        self.write("remote_networksim_tc134_stdout.log", [
            "[TRACE-PERF] 101|0|nsim|55|0x0|RECV|src=6 dst=0",
        ])
        report = MODULE.analyze(self.root)
        plane6 = next(row for row in report["planes"] if row["plane"] == 6)
        self.assertEqual(plane6["window_pressure_iter"], 3072)
        self.assertEqual(report["source_scan"]["files"]["simout"], 1)
        self.assertEqual(report["source_scan"]["files"]["ubio"], 1)
        self.assertEqual(report["source_scan"]["files"]["gem5"], 1)
        self.assertEqual(report["source_scan"]["files"]["nsim"], 1)
        self.assertEqual(len(report["suspects"][0]["unresolved_fills"]), 1)

    def test_supervisor_is_optional(self):
        self.write("simout_tc134_node3.log", self.guest_lines(6, 1024))
        report = MODULE.analyze(self.root)
        self.assertNotIn("supervisor", report["source_scan"]["files"])
        self.assertEqual(report["supervisor_tail"], [])

    def test_separate_simout_directory_matches_tc98_input_model(self):
        protocol_root = self.root / "protocol"
        simout_root = self.root / "guest"
        simout = simout_root / "simout_tc134_node3.log"
        simout.parent.mkdir(parents=True, exist_ok=True)
        simout.write_text("\n".join(self.guest_lines(6, 4096)) + "\n")
        offset = MODULE.stream_offset(3, 4096)
        protocol = protocol_root / "remote_ubio_tc134_n0_s0_stdout.log"
        protocol.parent.mkdir(parents=True, exist_ok=True)
        protocol.write_text(
            f"[RESIDENT-FILL-ISSUED] tick=99 home=0 pa=0x{offset:x} "
            "waiterDepth=1 opKind=0\n")
        report = MODULE.analyze(protocol_root, simout_root=simout_root)
        plane6 = next(row for row in report["planes"] if row["plane"] == 6)
        self.assertEqual(plane6["window_pressure_iter"], 4096)
        self.assertEqual(report["log_dir"], str(protocol_root))
        self.assertEqual(report["simout_dir"], str(simout_root))
        self.assertEqual(report["source_scan"]["files"]["simout"], 1)
        self.assertEqual(report["source_scan"]["files"]["ubio"], 1)

    def test_overlapping_roots_do_not_double_count_files(self):
        self.write("simout_tc134_node3.log", self.guest_lines(6, 1024))
        report = MODULE.analyze(self.root, simout_root=self.root)
        self.assertEqual(report["source_scan"]["files"]["simout"], 1)

    def test_user_supplied_remote_path_examples(self):
        examples = {
            "/tmp/tcrandom/wjsj/djskkw/node2/simout_n2": "simout",
            "/home/logs/sjsjsjej/gem5-0.stdout.log": "gem5",
            "/home/logs/38393847478/ubio-0-1.stderr.log": "ubio",
            "/jdeiejejjeh/djdjdj/jsjdjdj/nsim-0.stdout.log": "nsim",
        }
        for path, expected in examples.items():
            with self.subTest(path=path):
                self.assertEqual(MODULE.log_source(pathlib.Path(path)), expected)

    def test_random_parent_names_do_not_define_source(self):
        self.assertEqual(
            MODULE.log_source(pathlib.Path(
                "/tmp/ubio/gem5/nsim/node2/unrelated.stdout.log")),
            "other")

    def test_user_flat_names_drive_separate_root_analysis(self):
        protocol_root = self.root / "protocol" / "38393847478"
        simout_root = self.root / "guest" / "node2"
        simout = simout_root / "simout_n2"
        simout.parent.mkdir(parents=True, exist_ok=True)
        simout.write_text("\n".join(self.guest_lines(6, 5120)) + "\n")
        offset = MODULE.stream_offset(3, 5120)
        protocol_root.mkdir(parents=True, exist_ok=True)
        (protocol_root / "ubio-0-1.stderr.log").write_text(
            f"[RESIDENT-FILL-ISSUED] tick=99 home=0 pa=0x{offset:x} "
            "waiterDepth=1 opKind=0\n")
        (protocol_root / "gem5-0.stdout.log").write_text(
            f"[TRACE-PERF] 100|3|gem5|55|0x{offset:x}|SEND|ReadReq\n")
        (protocol_root / "nsim-0.stdout.log").write_text(
            "[TRACE-PERF] 101|0|nsim|55|0x0|RECV|src=6 dst=0\n")
        report = MODULE.analyze(protocol_root, simout_root=simout_root)
        plane6 = next(row for row in report["planes"] if row["plane"] == 6)
        self.assertEqual(plane6["window_pressure_iter"], 5120)
        self.assertEqual(report["source_scan"]["files"]["simout"], 1)
        self.assertEqual(report["source_scan"]["files"]["ubio"], 1)
        self.assertEqual(report["source_scan"]["files"]["gem5"], 1)
        self.assertEqual(report["source_scan"]["files"]["nsim"], 1)

    def test_compact_output_is_short_and_identifies_writer(self):
        self.write("simout_n3", self.guest_lines(6, 2048))
        report = MODULE.analyze(self.root)
        stream = io.StringIO()
        with redirect_stdout(stream):
            MODULE.print_compact(report)
        lines = stream.getvalue().splitlines()
        self.assertLessEqual(len(lines), 7)
        self.assertTrue(lines[0].startswith("STATUS "))
        self.assertTrue(lines[1].startswith("SOURCES "))
        self.assertIn("p6=2048/8192", lines[2])
        self.assertTrue(any("physicalNode=3" in line and
                            "writerPlane=6" in line for line in lines))

    def test_compact_no_evidence_is_three_lines(self):
        self.write("unrelated.log", ["nothing"])
        report = MODULE.analyze(self.root)
        stream = io.StringIO()
        with redirect_stdout(stream):
            MODULE.print_compact(report)
        lines = stream.getvalue().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("NO_TC134_EVIDENCE", lines[0])

    def test_pa_aliases_and_hex_request_ids_are_parsed(self):
        self.write("simout_n3", self.guest_lines(6, 0))
        offset = MODULE.stream_offset(3, 0)
        aliases = ("PA", "homePa", "homePA", "homeLinePa", "keyPA")
        for index, alias in enumerate(aliases, 1):
            self.write(f"ubio-0-{index}.stderr.log", [
                f"[UBCC-OUTER-REQ] home=0 {alias}=0x{offset + index * 64:x} "
                f"reqId=0x{40 + index:x}",
            ])
        report = MODULE.analyze(self.root)
        suspect = report["suspects"][0]
        reqids = {item["reqid"] for item in suspect["matching_reqids"]}
        self.assertEqual(reqids, {41, 42, 43, 44, 45})
        self.assertTrue(all(item["last_protocol_stage"] == "HOME_OUTER_REQ"
                            for item in suspect["matching_reqids"]))

    def test_compact_reports_last_non_sampled_protocol_stage(self):
        self.write("simout_n3", self.guest_lines(6, 0))
        offset = MODULE.stream_offset(3, 0)
        self.write("gem5-3.stderr.log", [
            f"[PENDING-READ-SAVE] node=3 localPa=0x{offset:x} "
            f"homePA=0x{offset:x} sourceSocket=0 epoch=1 reqId=77",
        ])
        report = MODULE.analyze(self.root)
        stream = io.StringIO()
        with redirect_stdout(stream):
            MODULE.print_compact(report)
        self.assertIn("77:REQUESTER_PENDING_READ_SAVE", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
