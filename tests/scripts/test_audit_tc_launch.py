#!/usr/bin/env python3

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "audit_tc_launch.py"


def write_fixture(root, tc, profile="optimized"):
    launch = root / f"launch_commands_tc{tc}.jsonl"
    rows = []
    env = {"EP_LINK_LATENCY_PS": "2500", "EP_SYNC_INTERVAL_PS": "2500",
           "EP_PORT_HWM": "8192", "EP_NSIM_MAX_PENDING": "65536",
           "EP_TRACE_PERF": "off"}
    manifests = []
    for node in range(8):
        if tc == 98:
            silent, batch = 0, 1
        else:
            silent, batch = ((1, 1) if profile == "optimized" else (0, 0))
        gem5_config_argv = ["test_e2e.py", "--x=1", "--x=2"]
        if tc == 134:
            gem5_config_argv += [f"--silent-upgrade={silent}", "--direct-fwd=0",
                                 f"--ubcc-batch-rs={batch}"]
        rows.append({"component": "gem5", "node": node, "socket": None,
                     "tc": tc, "topology": "8n2s",
                     "argv": ["gem5", "--outdir=x"] + gem5_config_argv,
                     "env": env})
        manifests.append({"component": "gem5-config", "tc": tc,
                          "argv": gem5_config_argv,
                          "config_argv": gem5_config_argv,
                          "process_argv": ["gem5", "--outdir=x"] + gem5_config_argv,
                          "unknown_args": [], "node": node, "num_nodes": 8,
                           "num_sockets": 2, "cpu_model": "o3",
                           "ha_profile": "ubcc", "clear_profile": "ack",
                          "build_nodes": [node], "cpus_per_node": 4,
                          "process_cpu_count": 4,
                          "sequencer_max_outstanding": 16, "metadata_bytes": 134217728,
                          "silent_upgrade": {"requested": silent, "effective": silent},
                          "direct_fwd": {"requested": 0, "effective": 0},
                          "batch_rs": {"requested": batch, "effective": batch}})
        for socket in range(2):
            ubio_argv = ["ubio"]
            bloom, policy = 61440, "spill"
            if tc == 134 and profile == "naive":
                bloom, policy = 0, "naive"
            if tc == 134:
                ubio_argv += [f"--bloom-bytes={bloom}", "--sram-bytes=524288",
                              "--ways=0", "--set-bits=0",
                              f"--dir-overflow-policy={policy}", "--batch-rs=0",
                              "--metadata-dram-bytes=134217728"]
            rows.append({"component": "ubio", "node": node, "socket": socket,
                         "tc": tc, "topology": "8n2s", "argv": ubio_argv, "env": env})
            manifests.append({"component": "ubio", "argv": ubio_argv, "node": node,
                               "tc": tc,
                              "socket": socket, "num_nodes": 8, "num_sockets": 2,
                               "resident_dir": {"bloom_bytes": bloom, "sram_bytes": 524288,
                                                "ways": 1 if tc == 98 else 0, "set_bits": 0,
                                                "pa_bits": 43, "sharers_bits": 8,
                                                "epoch_bits": 24},
                               "overflow_policy": policy, "batch_rs": 1 if tc == 98 else 0,
                               "schema": "disabled" if policy == "naive" else "h64",
                               "home_controller": "ubcc", "dram_delay_ps": 0,
                               "fault_rule_args": 0, "blc_bytes": 0,
                               "desc_scratch_bytes": 0,
                               "metadata_dram_bytes": 134217728,
                               "env": env})
    rows.append({"component": "networksim", "node": None, "socket": None,
                 "tc": tc, "topology": "8n2s", "argv": ["networksim"], "env": env})
    manifests.append({"component": "networksim", "argv": ["networksim"],
                      "tc": tc,
                      "num_nodes": 8, "num_sockets": 2, "max_pending": 65536,
                      "trace_all_forwarded": 0})
    launch.write_text("".join(json.dumps(row) + "\n" for row in rows))
    (root / f"remote_tc{tc}_all_stdout.log").write_text(
        "".join("[PROCESS-MANIFEST] " + json.dumps(row) + "\n" for row in manifests))


def run_audit(root, *extra):
    return subprocess.run([sys.executable, str(SCRIPT), str(root), *extra],
                          text=True, capture_output=True)


class AuditTcLaunchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_tc98_remote_logs_pass_without_launcher_jsonl(self):
        write_fixture(self.root, 98)
        (self.root / "launch_commands_tc98.jsonl").unlink()
        result = run_audit(self.root, "--tc", "98", "--formal")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stdout.startswith("PASS TC98 formal"))
        self.assertIn("remote process logs", result.stdout)

    def test_optional_launcher_jsonl_is_cross_checked(self):
        write_fixture(self.root, 98)
        result = run_audit(
            self.root, "--launch-jsonl", str(self.root / "launch_commands_tc98.jsonl"),
            "--tc", "98", "--formal")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_tc134_profiles_pass(self):
        for profile in ("naive", "spill-noopt", "optimized"):
            with self.subTest(profile=profile):
                case = self.root / profile
                case.mkdir()
                write_fixture(case, 134, profile)
                result = run_audit(case, "--tc", "134", "--profile", profile)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_reports_effective_mismatch(self):
        write_fixture(self.root, 134, "optimized")
        log = self.root / "remote_tc134_all_stdout.log"
        text = log.read_text().replace(
            '"batch_rs": {"requested": 1, "effective": 1}',
            '"batch_rs": {"requested": 1, "effective": 0}', 1)
        log.write_text(text)
        result = run_audit(self.root, "--tc", "134", "--profile", "optimized")
        self.assertEqual(result.returncode, 1)
        self.assertIn("TC134 gem5 batch_rs", result.stdout)

    def test_rejects_fault_override(self):
        write_fixture(self.root, 98)
        log = self.root / "remote_tc98_all_stdout.log"
        rows = []
        for line in log.read_text().splitlines():
            row = json.loads(line.split("[PROCESS-MANIFEST] ", 1)[1])
            if row["component"] == "ubio" and row["node"] == 0 and row["socket"] == 0:
                row["argv"].append("--fault-rules=x")
            rows.append(row)
        log.write_text("".join("[PROCESS-MANIFEST] " + json.dumps(row) + "\n"
                               for row in rows))
        (self.root / "launch_commands_tc98.jsonl").unlink()
        result = run_audit(self.root, "--tc", "98")
        self.assertEqual(result.returncode, 1)
        self.assertIn("fault rules present", result.stdout)


if __name__ == "__main__":
    unittest.main()
