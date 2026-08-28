import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/run_topology_tc_matrix_plan.py"
SPEC = importlib.util.spec_from_file_location("run_topology_tc_matrix_plan", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class TopologyPlanExecutorTest(unittest.TestCase):
    def test_replaces_mounts_and_writes_resumable_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            workspace, results = root / "workspace", root / "results"
            workspace.mkdir(); results.mkdir()
            job = {"job_id": "smoke-x", "tier": "smoke", "topology": "2n1s",
                   "tc": 228, "result_path": "/results/smoke/cases/smoke-x",
                   "expected_child_exit_count": 1,
                   "command_argv": ["docker", "run", "-v",
                                    "${WORKSPACE:?set WORKSPACE}:/workspace",
                                    "-v", "${RESULT_ROOT:?set RESULT_ROOT}:/results",
                                    "LOG_BASE=/results/old/path"]}
            manifest = root / "smoke.json"
            manifest.write_text(json.dumps({"tier": "smoke", "jobs": [job]}))
            result_dir = results / "smoke/cases/smoke-x"
            result_dir.mkdir(parents=True)
            (result_dir / "verify_tc228.log").write_text(">>> TC228 PASSED <<<\n")
            child = result_dir / "child_status_tc228"; child.mkdir()
            (child / "one.exit").write_text("0\n")
            with mock.patch.object(MODULE.subprocess, "run") as run:
                run.return_value.returncode = 0
                self.assertEqual(MODULE.run_manifest(manifest, workspace, results), 0)
                argv = run.call_args.args[0]
                self.assertIn(f"{workspace}:/workspace", argv)
                self.assertIn(f"{results}:/results", argv)
                self.assertIn("LOG_BASE=/results/smoke/cases/smoke-x", argv)
                self.assertNotIn("LOG_BASE=/results/old/path", argv)
            self.assertEqual(json.loads((result_dir / "_plan_result.json").read_text())
                             ["status"], "PASS")
            with mock.patch.object(MODULE.subprocess, "run") as run:
                self.assertEqual(MODULE.run_manifest(manifest, workspace, results), 0)
                run.assert_not_called()

    def test_rejects_formal_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "formal.json"
            path.write_text(json.dumps({"tier": "formal", "jobs": []}))
            with self.assertRaisesRegex(ValueError, "only permits"):
                MODULE.run_manifest(path, pathlib.Path(temporary),
                                    pathlib.Path(temporary))


if __name__ == "__main__":
    unittest.main()
