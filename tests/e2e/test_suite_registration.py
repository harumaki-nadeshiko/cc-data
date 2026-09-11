"""Registration/selection contracts; no simulator or compiler is launched."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

import test_e2e


ROOT = Path(__file__).resolve().parents[2]


class SuiteRegistrationTest(unittest.TestCase):
    def test_registered_cases_preserve_numbering_and_sources(self):
        self.assertNotIn(141, test_e2e.TESTCASES)
        self.assertNotIn(141, test_e2e.VERIFIERS)
        self.assertFalse(hasattr(test_e2e, "verify_tc141"))
        for tc in (140, 142):
            self.assertIn(tc, test_e2e.TESTCASES)
            self.assertIn(tc, test_e2e.VERIFIERS)
        for name in test_e2e.TESTCASES.values():
            self.assertTrue((ROOT / "tests/e2e/workloads" / (name + ".c")).is_file())

    def test_unknown_verifier_uses_existing_failure_contract(self):
        self.assertEqual(test_e2e.verify_testcase(141, [], []),
                         (False, "FAILED: unknown test case TC141", []))

    def test_explicit_selection_fails_before_execution(self):
        commands = (
            (["bash", "tests/e2e/run_multi.sh", "--1s", "141"], 2,
             "unsupported test case TC141"),
            (["bash", "tests/e2e/run_multi.sh", "--3n2s", "140", "141"], 2,
             "unsupported test case TC141"),
            (["bash", "scripts/compile_workload.sh", "141"], 2,
             "tc_id=141 not found in TESTCASES"),
            ([sys.executable, "tests/e2e/test_e2e.py", "--tc", "141"], 2,
             "unsupported test case TC141"),
            ([sys.executable, "tests/e2e/verify.py", "--tc", "141"], 1,
             "FAILED: unknown test case TC141"),
        )
        for command, code, message in commands:
            with self.subTest(command=command):
                result = subprocess.run(command, cwd=ROOT, text=True,
                                        capture_output=True, timeout=20,
                                        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                self.assertIn(message, result.stdout + result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_active_queue_and_runner_do_not_select_removed_case(self):
        for relative in ("tests/e2e/run_multi.sh",
                         "scripts/run_low_frequency_correctness_queue.sh"):
            self.assertNotRegex((ROOT / relative).read_text(), r"\b(?:TC)?141\b")
        queue = (ROOT / "scripts/run_low_frequency_correctness_queue.sh").read_text()
        self.assertIn("expected_targets\\t62\\n", queue)


if __name__ == "__main__":
    unittest.main()
