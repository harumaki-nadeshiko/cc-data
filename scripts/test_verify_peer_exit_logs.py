#!/usr/bin/env python3
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "scripts" / "verify_peer_exit_logs.py"


class VerifyPeerExitLogsTest(unittest.TestCase):
    def make_logs(self, root, missing_nsim_mod=None):
        tc = 2
        planes = [(0, 0), (1, 0), (2, 0)]
        for node, socket in planes:
            exit_id = 100 + node
            path = root / f"ubio_tc{tc}_n{node}_s{socket}" / "stdout.log"
            path.parent.mkdir(parents=True)
            peers = [plane for plane in planes if plane != (node, socket)]
            lines = [
                f"[PEER-EXIT-START] local={node}:{socket} exitId={exit_id} "
                "version=1 required=2 seenNotify=0",
            ]
            for peer_node, peer_socket in peers:
                lines.append(
                    f"[PEER-EXIT-ACK-RECV] local={node}:{socket} "
                    f"peer={peer_node}:{peer_socket} exitId={exit_id}")
            lines.extend([
                f"[PEER-EXIT-QUIESCE] local={node}:{socket} "
                f"exitId={exit_id} acked=2/2",
                f"[PEER-EXIT-CLOSE] local={node}:{socket} exitId={exit_id}",
                f"[NETWORK-EXIT-REQUEST-SEND] local={node}:{socket} "
                f"exitId={exit_id} attempt=1 sent=1",
                f"[NETWORK-EXIT-ACK-RECV] local={node}:{socket} "
                f"exitId={exit_id} attempts=1",
            ])
            path.write_text("\n".join(lines) + "\n")

        nsim_lines = []
        for mod in range(len(planes)):
            if mod == missing_nsim_mod:
                continue
            exit_id = 100 + mod
            nsim_lines.append(
                f"[NSIM-NETWORK-EXIT-REQUEST-RECV] mod={mod} exitId={exit_id} "
                f"requests={mod + 1}/3 tick={mod + 1}")
        for mod in range(len(planes)):
            if mod == missing_nsim_mod:
                continue
            exit_id = 100 + mod
            nsim_lines.append(
                f"[NSIM-NETWORK-EXIT-ACK-SEND] mod={mod} exitId={exit_id} "
                f"sent=1 fifo=0 tick=4")
        (root / f"nsim_tc{tc}.log").write_text("\n".join(nsim_lines) + "\n")

    def run_verifier(self, root):
        return subprocess.run(
            [sys.executable, str(VERIFIER), str(root), "--tc", "2",
             "--num-nodes", "3",
             "--num-sockets", "1"],
            text=True, capture_output=True, check=False)

    def test_complete_network_exit_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.make_logs(root)
            result = self.run_verifier(root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("NetworkExit ACK completed on 3/3", result.stdout)

    def test_missing_node2_network_exit_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self.make_logs(root, missing_nsim_mod=2)
            result = self.run_verifier(root)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("NetworkExit Request mod set mismatch", result.stdout)
            self.assertIn("actual=['0', '1']", result.stdout)


if __name__ == "__main__":
    unittest.main()
