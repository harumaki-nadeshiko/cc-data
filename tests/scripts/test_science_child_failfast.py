"""Execute the actual scientific child-exit guard in a bounded shell fixture.

Run only in Docker --network none ubcc-dev:ubuntu20.04.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ScienceChildFailfastTest(unittest.TestCase):
    def run_guard(self, tc, statuses):
        source = (ROOT / 'tests/e2e/run_multi.sh').read_text()
        start = source.index('        # Scientific campaigns must not wait hours')
        end = source.index('        sleep 1; waited=', start)
        guard = source[start:end]
        with tempfile.TemporaryDirectory() as directory:
            for name, status in statuses.items():
                Path(directory, name).write_text(status)
            script = '''_supervisor_stop() { echo STOP_SUPERVISOR; }
_kill_infra() { echo STOP_OWN_CHILDREN; }
run_fixture() {
 local tc="$1" child_status_dir="$2"
''' + guard + '\n}\nrun_fixture "$1" "$2"\n'
            return subprocess.run(['bash', '-c', script, 'fixture', str(tc), directory],
                                  capture_output=True, text=True, timeout=5)

    def test_each_scientific_tc_rejects_failed_peer(self):
        for tc in list(range(142, 148)) + list(range(228, 236)):
            with self.subTest(tc=tc):
                result = self.run_guard(tc, {'ubio_n0_s0.exit': '134\n'})
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn('status=134', result.stdout)
                self.assertIn('STOP_OWN_CHILDREN', result.stdout)

    def test_empty_and_successful_exit_files_do_not_abort(self):
        for statuses in ({}, {'gem5_node0.exit':'0\n'}, {'networksim.exit':''}):
            result = self.run_guard(142, statuses)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('STOP_', result.stdout)

    def test_expected_negative_tc_is_not_changed(self):
        result = self.run_guard(9, {'gem5_node0.exit':'134\n'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('STOP_', result.stdout)


if __name__ == '__main__':
    unittest.main()
