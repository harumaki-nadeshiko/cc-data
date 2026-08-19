#!/usr/bin/env python3

import importlib.util
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/analyze_tc98_logs.py"
SPEC = importlib.util.spec_from_file_location("analyze_tc98_logs", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Args:
    num_nodes = 8
    num_sockets = 2
    rounds = 16
    hot_pa = None
    sample_limit = 3
    simout_dir = None


class AnalyzeTc98LogsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def add_common(self, epoch_drop=False):
        for node in range(8):
            lines = []
            for socket in range(2):
                lines.append(
                    f"[TC98_PROGRESS] node={node} sock={socket} r=12\n")
            if node == 0:
                for plane in range(16):
                    lines.append(
                        "[READ_VAL] node=0 home={} offset=0 expected=98dd{:04x} "
                        "actual=98dd{:04x} MATCH\n".format(plane, plane, plane))
            self.write(self.root / f"simout_tc98_node{node}.log", "".join(lines))

        ubio = self.root / "ubio_tc98_n0_s0" / "stdout.log"
        epochs = [1, 2, 3, 4]
        if epoch_drop:
            epochs.append(3)
        commit_lines = [
            "UBCC node_id=0: commitIntendedResult PA=0x10007800 "
            f"path=Clear state=G_M owner=0 epoch={epoch}\n" for epoch in epochs
        ]
        for node in range(8):
            for socket in range(2):
                plane_log = self.root / f"ubio_tc98_n{node}_s{socket}" / "stdout.log"
                text = (
                    f"[PEER-EXIT-CLOSE] local={node}:{socket} exitId=1\n"
                    f"[NETWORK-EXIT-ACK-RECV] local={node}:{socket} exitId=1 attempts=1\n")
                if plane_log == ubio:
                    text = "".join(commit_lines) + text
                self.write(plane_log, text)
        nsim = "".join(
            f"[NSIM-NETWORK-EXIT-ACK-SEND] mod={mod} exitId=1 sent=1 fifo=0 tick=9\n"
            for mod in range(16))
        self.write(self.root / "nsim_tc98.log", nsim)
        self.write(self.root / "verify_tc98.log", ">>> TC98 PASSED <<<\n")
        child = self.root / "child_status_tc98"
        for node in range(8):
            self.write(child / f"gem5_node{node}.exit", "0\n")
        for node in range(8):
            for socket in range(2):
                self.write(child / f"ubio_n{node}_s{socket}.exit", "0\n")
        self.write(child / "networksim.exit", "0\n")

    def analyze(self):
        args = Args()
        args.log_dir = str(self.root)
        return MODULE.analyze(args)

    def test_pass(self):
        self.add_common()
        report = self.analyze()
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(report["hot_line"]["monotonic"])
        self.assertEqual(report["shutdown"]["network_exit_ack_ubio"], 16)

    def test_epoch_decrease_fails(self):
        self.add_common(epoch_drop=True)
        ubio = self.root / "ubio_tc98_n0_s0" / "stdout.log"
        with ubio.open("a") as stream:
            stream.write("Panic: [UBInv] PA=0x10007800 epoch DECREASED 4 -> 3\n")
        report = self.analyze()
        self.assertEqual(report["status"], "FAIL")
        self.assertFalse(report["hot_line"]["monotonic"])
        self.assertEqual(report["issues"]["epoch_decreased"], 1)

    def test_separate_simout_directory(self):
        self.add_common()
        simout_root = self.root.parent / f"{self.root.name}-simout"
        simout_root.mkdir()
        self.addCleanup(lambda: __import__("shutil").rmtree(simout_root))
        for path in list(self.root.glob("simout_tc98_node*.log")):
            path.rename(simout_root / path.name)
        args = Args()
        args.log_dir = str(self.root)
        args.simout_dir = str(simout_root)
        report = MODULE.analyze(args)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["progress"]["complete_planes"], 16)
        self.assertEqual(report["done_markers"]["match"], 16)


if __name__ == "__main__":
    unittest.main()
