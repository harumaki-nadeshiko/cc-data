"""Run inside Docker, after scripts/build_framework.sh and build_ubio.sh.

UBIO startup checks use no simulator/peer; the runner test executes only its
argument-resolution block and actual forwarding statement, not a full E2E run.
"""
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
KEYS = ('EP_WAIT_BLOOM_READY', 'EP_BLOOM_READY_TIMEOUT_MS')


class BloomReadyCLI(unittest.TestCase):
    def env(self, values=()):
        env = os.environ.copy()
        for key in KEYS:
            env.pop(key, None)
        env.update(zip(KEYS, values))
        return env

    def ubio(self, args=(), values=()):
        command = [str(ROOT / 'build/bin/ubio'), *args]
        try:
            result = subprocess.run(command, env=self.env(values), cwd=ROOT,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    timeout=1)
            return result.returncode, result.stdout.decode()
        except subprocess.TimeoutExpired as exc:
            return 124, (exc.stdout or b'').decode()

    def test_effective_config(self):
        cases = [([], (), (0, 120000)),
                 ([], ('1', '42'), (1, 42)),
                 (['--wait-bloom-ready=0', '--bloom-ready-timeout-ms=120000'],
                  ('invalid', 'invalid'), (0, 120000)),
                 (['--wait-bloom-ready', '1', '--bloom-ready-timeout-ms', '7'],
                  (), (1, 7)),
                 (['--bloom-ready-timeout-ms=18446744073709551615'],
                  (), (0, 18446744073709551615))]
        for args, env, expected in cases:
            with self.subTest(args=args, env=env):
                _, output = self.ubio(args, env)
                manifests = [json.loads(line.split('] ', 1)[1])
                             for line in output.splitlines()
                             if line.startswith('[PROCESS-MANIFEST] ')]
                self.assertTrue(manifests, output)
                manifest = manifests[0]
                self.assertEqual((manifest['wait_bloom_ready'],
                                  manifest['bloom_ready_timeout_ms']), expected)
                self.assertEqual(manifest['argv'][1:], args)

    def test_invalid_values(self):
        for option, values in [('--wait-bloom-ready', ['', '2', '-1', 'true']),
                               ('--bloom-ready-timeout-ms',
                                ['', '0', '-1', '1x', '+1', '1.5',
                                 '18446744073709551616'])]:
            for value in values:
                with self.subTest(option=option, value=value):
                    code, output = self.ubio([f'{option}={value}'])
                    self.assertEqual(code, 1, output)
                    self.assertIn('UBIO-FATAL', output)
            code, output = self.ubio([option])
            self.assertEqual(code, 1, output)
            self.assertIn('requires a value', output)

    def runner(self, args, values=()):
        text = (ROOT / 'tests/e2e/run_multi.sh').read_text()
        block = text.split('export EP_WAIT_BLOOM_READY=', 1)[1]
        block = 'export EP_WAIT_BLOOM_READY=' + block.split('export OURCC_CLEAR_PROFILE=', 1)[0]
        forward = next(line for line in text.splitlines()
                       if 'uextra="$uextra --wait-bloom-ready=' in line)
        return subprocess.run(['bash', '-c', 'set -euo pipefail\n' + block +
                               '\nuextra=""\n' + forward +
                               '\nprintf "%s\\n" "$uextra" "$@"',
                               'runner-test', *args], env=self.env(values),
                              text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_runner_forwarding_and_positionals(self):
        for args in [ ['--wait-bloom-ready=1', '--bloom-ready-timeout-ms=42', '--2n1s', '142'],
                      ['--2n1s', '142', '--wait-bloom-ready', '1', '--bloom-ready-timeout-ms', '42'] ]:
            result = self.runner(args, ('invalid', 'invalid'))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(),
                             [' --wait-bloom-ready=1 --bloom-ready-timeout-ms=42', '--2n1s', '142'])
        result = self.runner(['--2n1s', '142'])
        self.assertIn('--wait-bloom-ready=0 --bloom-ready-timeout-ms=120000', result.stdout)

    def test_runner_rejects_invalid(self):
        for args in [['--wait-bloom-ready=2'], ['--wait-bloom-ready'],
                     ['--bloom-ready-timeout-ms=0'], ['--bloom-ready-timeout-ms=-1'],
                     ['--bloom-ready-timeout-ms=18446744073709551616'],
                     ['--bloom-ready-timeout-ms', '--2n1s', '142']]:
            result = self.runner(args)
            self.assertEqual(result.returncode, 2, result.stderr)

    def test_invalid_environment_is_rejected(self):
        for values in [('invalid', '42'), ('1', '0'), ('1', '18446744073709551616')]:
            code, output = self.ubio(values=values)
            self.assertEqual(code, 1, output)
            self.assertIn('UBIO-FATAL', output)

    def test_last_cli_value_wins(self):
        result = self.runner(['--wait-bloom-ready=0', '--wait-bloom-ready', '1',
                              '--bloom-ready-timeout-ms=7', '--bloom-ready-timeout-ms', '9'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(),
                         '--wait-bloom-ready=1 --bloom-ready-timeout-ms=9')

    def test_runner_environment_fallback(self):
        result = self.runner(['--2n1s', '142'], ('1', '73'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(),
                         [' --wait-bloom-ready=1 --bloom-ready-timeout-ms=73', '--2n1s', '142'])

    def test_python_driver_does_not_fake_readiness(self):
        source = (ROOT / 'tests/e2e/test_e2e.py').read_text()
        self.assertNotIn('EP_WAIT_BLOOM_READY', source)
        self.assertNotIn('EP_BLOOM_READY_TIMEOUT_MS', source)


if __name__ == '__main__':
    unittest.main()
