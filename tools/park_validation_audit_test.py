#!/usr/bin/env python3
"""Audit rejection tests; fixture results are never campaign PASS evidence."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from park_validation_audit import audit


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parents[1] / 'boundary-evidence/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        self.out = self.root / 'out'
        self.logs = self.out / 'logs'
        self.logs.mkdir(parents=True)
        self.put(self.source / 'tests/e2e/verify.py', '# fixture\n')
        self.put(self.source / 'scripts/verify_peer_exit_logs.py', '# fixture\n')
        self.put(self.out / 'stdout.raw', '=== Results: 1 pass, 0 fail ===\n')
        self.manifest = dict(source_version='fixture-not-candidate', queue=dict(
            verifier_sha256=hashlib.sha256(b'# fixture\n').hexdigest()))
        self.result = dict(source_version='fixture-not-candidate', exit_code=0)

    def put(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def run_audit(self, tc):
        return audit(self.source, self.out,
                     dict(tc=tc, n=1, s=1, key='fixture/%d' % tc),
                     self.result, self.manifest)

    def negative(self):
        self.put(self.logs / 'gem5_tc9_node0/stderr.log',
                 'Page table fault when accessing virtual address 0xfffff8000000\n')

    def test_negative_exact_fault_only(self):
        self.negative()
        self.assertEqual(self.run_audit(9)['state'], 'PASS')

    def test_negative_read_value_rejected(self):
        self.negative()
        self.put(self.out / 'run/m5out/node0/simout_n0', 'READ_VAL: 12\n')
        self.assertEqual(self.run_audit(9)['state'], 'FAIL')

    def test_wrong_crash_rejected(self):
        self.put(self.logs / 'gem5_tc9_node0/stderr.log', 'segmentation fault\n')
        self.assertEqual(self.run_audit(9)['state'], 'FAIL')

    def test_runner_failure_rejected(self):
        self.negative()
        self.result['exit_code'] = 1
        self.assertEqual(self.run_audit(9)['state'], 'FAIL')

    def positive(self):
        for name in ('networksim', 'gem5_node0', 'ubio_n0_s0'):
            self.put(self.logs / ('child_status_tc2/%s.exit' % name), '0\n')
        self.put(self.logs / 'simout_tc2_node0.log', 'fixture\n')

    def test_positive_reruns_both_verifiers(self):
        self.positive()
        with patch('park_validation_audit.subprocess.run', return_value=
                   SimpleNamespace(returncode=0, stdout=b'>>> TC2 PASSED <<<\n')) as run:
            self.assertEqual(self.run_audit(2)['state'], 'PASS')
            self.assertEqual(run.call_count, 2)

    def test_child_failure_overrides_science_pass(self):
        self.positive()
        self.put(self.logs / 'child_status_tc2/ubio_n0_s0.exit', '137\n')
        with patch('park_validation_audit.subprocess.run', return_value=
                   SimpleNamespace(returncode=0, stdout=b'>>> TC2 PASSED <<<\n')):
            self.assertEqual(self.run_audit(2)['state'], 'FAIL')

    def test_missing_child_overrides_science_pass(self):
        self.positive()
        (self.logs / 'child_status_tc2/networksim.exit').unlink()
        with patch('park_validation_audit.subprocess.run', return_value=
                   SimpleNamespace(returncode=0, stdout=b'>>> TC2 PASSED <<<\n')):
            self.assertEqual(self.run_audit(2)['state'], 'FAIL')

    def test_zero_exit_without_science_sentinel_rejected(self):
        self.positive()
        with patch('park_validation_audit.subprocess.run', return_value=
                   SimpleNamespace(returncode=0, stdout=b'not a verification result\n')):
            self.assertEqual(self.run_audit(2)['state'], 'FAIL')


if __name__ == '__main__':
    assert Path('/.dockerenv').exists(), 'Docker only'
    unittest.main(verbosity=2)
