#!/usr/bin/env python3

import importlib.util
import io
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/diagnose_upgrade_terminal.py"
SPEC = importlib.util.spec_from_file_location("diagnose_upgrade_terminal", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DiagnoseUpgradeTerminalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, lines):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")

    @staticmethod
    def marker(stage, reqid, message_type=None):
        type_field = f" type={message_type}" if message_type else ""
        return (f"[UPGRADE-FORENSIC] stage={stage} pa=0x100 reqId={reqid} "
                f"epoch=5{type_field}")

    def test_response_break_at_requester_port(self):
        reqid = 5
        stages = [
            ("GEM5_REQ_SEND", None),
            ("UBIO_GEM5_RECV", "UpgradeReq"),
            ("UBIO_NET_SEND", "UpgradeReq"),
            ("NSIM_RECV", "UpgradeReq"),
            ("NSIM_FWD", "UpgradeReq"),
            ("UBIO_NET_RECV", "UpgradeReq"),
            ("HOME_REQ_RESULT", None),
            ("UBIO_NET_SEND", "UpgradeResp"),
            ("NSIM_RECV", "UpgradeResp"),
            ("NSIM_FWD", "UpgradeResp"),
            ("UBIO_NET_RECV", "UpgradeResp"),
            ("UBIO_GEM5_SEND", "UpgradeResp"),
        ]
        lines = [self.marker(stage, reqid, kind) for stage, kind in stages]
        lines.append(
            "[EPRNF-UPGRADE-TERMINAL] node=2 pa=0x100 sourceSocket=0 "
            "homeNode=0 epoch=5 reqId=5 reason=EXHAUSTED_NO_RESPONSE "
            "resends=8 homeAccepted=0")
        self.write("combined.log", lines)
        report = MODULE.diagnose(self.root)
        self.assertEqual(report["terminal_count"], 1)
        self.assertEqual(report["transactions"][0]["diagnosis"],
                         "REQUESTER_PORT_PENDING_OR_DELIVERY_BREAK")

    def test_complete_chain_points_to_eprnf_state(self):
        reqid = 9
        lines = []
        for stage, _ in MODULE.STAGES:
            if ":" in stage:
                base, kind = stage.split(":", 1)
            else:
                base, kind = stage, None
            lines.append(self.marker(base, reqid, kind))
        self.write("complete.log", lines)
        report = MODULE.diagnose(self.root, [str(reqid)])
        self.assertEqual(report["transactions"][0]["diagnosis"],
                         "RESPONSE_CONSUMED_CHECK_EPRNF_STATE_UPDATE")

    def test_compact_report_omits_raw_samples(self):
        reqid = 11
        self.write("terminal.log", [
            self.marker("GEM5_REQ_SEND", reqid),
            "[EPRNF-UPGRADE-TERMINAL] node=4 pa=0x100 sourceSocket=0 "
            "homeNode=0 epoch=5 reqId=11 reason=EXHAUSTED_NO_RESPONSE "
            "resends=8 homeAccepted=0",
        ])
        compact = MODULE.compact_report(MODULE.diagnose(self.root))
        self.assertNotIn("samples", compact["transactions"][0])
        self.assertEqual(
            compact["diagnosis_counts"],
            {"REQUESTER_GEM5_TO_UBIO_BREAK": 1})
        self.assertEqual(compact["transactions"][0]["last_present"],
                         "GEM5_REQ_SEND")

    def test_compact_human_is_two_lines_per_transaction(self):
        reqid = 12
        self.write("terminal.log", [
            self.marker("GEM5_REQ_SEND", reqid),
            "[EPRNF-UPGRADE-TERMINAL] node=4 pa=0x100 sourceSocket=0 "
            "homeNode=0 epoch=5 reqId=12 reason=EXHAUSTED_NO_RESPONSE "
            "resends=8 homeAccepted=0",
        ])
        stream = io.StringIO()
        with redirect_stdout(stream):
            MODULE.print_compact(MODULE.diagnose(self.root))
        lines = stream.getvalue().splitlines()
        self.assertEqual(len(lines), 5)
        self.assertIn("diagnosis=REQUESTER_GEM5_TO_UBIO_BREAK", lines[3])
        self.assertTrue(lines[4].startswith("CNT "))


if __name__ == "__main__":
    unittest.main()
