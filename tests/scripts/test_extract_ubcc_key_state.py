#!/usr/bin/env python3

import gzip
import importlib.util
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/extract_ubcc_key_state.py"
SPEC = importlib.util.spec_from_file_location("extract_ubcc_key_state", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ExtractUbccKeyStateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, lines):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")
        return path

    def test_extracts_flat_remote_logs(self):
        self.write("ubio-0-0.stdout.log", [
            "[UBCC-PROTOCOL-BUILD] revision=test home=0:0",
            "[UBCC-TUPLE-STATE] serial=1 home=0:0 pa=0x100",
        ])
        self.write("ubio-0-1.stderr.log", [
            "[UBCC-UNKNOWN-CLEAR-STATE] serial=1 home=0:1 pa=0x200",
            "[UBCC-EVICTION-ACK-STALE] kind=delete home=0 pa=0x200",
        ])
        report = MODULE.extract(self.root, 8)
        self.assertEqual(report["totals"]["build"], 1)
        self.assertEqual(report["totals"]["tuple"], 1)
        self.assertEqual(report["totals"]["unknown_clear"], 1)
        self.assertEqual(report["totals"]["stale_eviction"], 1)
        text = MODULE.format_report(report)
        self.assertIn("BUILD", text)
        self.assertIn("UNKNOWN_CLEAR", text)

    def test_reads_gzip_and_respects_limit(self):
        path = self.root / "ubio-0-0.stderr.log.gz"
        with gzip.open(path, "wt") as stream:
            for index in range(5):
                stream.write(f"[UBCC-TUPLE-STATE] serial={index}\n")
        report = MODULE.extract(self.root, 2)
        self.assertEqual(report["totals"]["tuple"], 5)
        self.assertEqual(len(report["samples"]["tuple"]), 2)

    def test_missing_markers_report_none(self):
        self.write("gem5-0.stdout.log", ["ordinary line"])
        text = MODULE.format_report(MODULE.extract(self.root, 8))
        self.assertIn("BUILD none", text)
        self.assertIn("TUPLE none", text)
        self.assertIn("UNKNOWN_CLEAR none", text)

    def test_compact_is_at_most_four_lines(self):
        self.write("ubio-0-0.stdout.log", [
            "[UBCC-PROTOCOL-BUILD] revision=test home=0:0",
            "[UBCC-TUPLE-STATE] home=0:0 pa=0x100 resident=1 pinned=1 "
            "outstandingReqId=41 incomingReqId=43 evictionPending=0",
            "[UBCC-UNKNOWN-CLEAR-STATE] home=0:0 pa=0x200 srcNode=3 "
            "clearReqId=55 hasOutstanding=1",
            "[UBCC-EVICTION-ACK-STALE] kind=delete home=0 pa=0x200 "
            "completionEpoch=7 currentEpoch=8 liveOutstanding=1 "
            "action=preserve_current_generation",
        ])
        text = MODULE.format_compact(MODULE.extract(self.root, 1))
        lines = text.splitlines()
        self.assertEqual(len(lines), 4)
        self.assertEqual(
            lines[0],
            "SUMMARY build=1 tuple=1 unknown_clear=1 stale_eviction=1 "
            "revision=test home=0:0")
        self.assertIn("outstandingReqId=41", lines[1])
        self.assertIn("clearReqId=55", lines[2])
        self.assertIn("action=preserve_current_generation", lines[3])

    def test_compact_omits_absent_marker_lines(self):
        self.write("ubio-0-0.stdout.log", [
            "[UBCC-PROTOCOL-BUILD] revision=test home=0:0",
        ])
        lines = MODULE.format_compact(MODULE.extract(self.root, 1)).splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("revision=test", lines[0])


if __name__ == "__main__":
    unittest.main()
