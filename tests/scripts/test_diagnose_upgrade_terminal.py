#!/usr/bin/env python3

import importlib.util
import pathlib
import tempfile
import unittest


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


if __name__ == "__main__":
    unittest.main()
