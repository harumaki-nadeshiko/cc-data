#!/usr/bin/env python3
"""Re-run frozen scientific and shutdown verifiers against this run's evidence."""
import hashlib
from pathlib import Path
import subprocess
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(source, out, job, result, manifest):
    assert Path('/.dockerenv').exists(), 'Docker only'
    tc, nodes, sockets = job['tc'], job['n'], job['s']
    logs = out / 'logs'
    verifier = source / 'tests/e2e/verify.py'
    record = dict(key=job['key'], source_version=result['source_version'],
                  raw_stdout_sha256=digest(out / 'stdout.raw'),
                  verifier_sha256=digest(verifier), state='FAIL', checks=[])
    assert record['verifier_sha256'] == manifest['queue']['verifier_sha256']
    assert result['source_version'] == manifest['source_version']
    checks = record['checks']

    def require(condition, description):
        checks.append(dict(check=description, passed=bool(condition)))
        return bool(condition)

    require(result['exit_code'] == 0, 'runner exit status')
    raw = (out / 'stdout.raw').read_bytes()
    require(b'=== Results: 1 pass, 0 fail ===' in raw, 'single-job runner summary')
    if tc == 9:
        # This deliberate page fault may terminate before normal PeerExit. Do
        # not impose the positive-test child-zero rule or accept arbitrary crash.
        crash = logs / 'gem5_tc9_node0/stderr.log'
        require(crash.is_file() and
                b'Page table fault when accessing virtual address 0xfffff8000000'
                in crash.read_bytes(), 'exact non-DSM fault')
        outputs = list((out / 'run').rglob('simout_n*'))
        outputs += list(logs.rglob('*.log'))
        require(bool(outputs), 'negative evidence exists')
        require(not any(b'READ_VAL' in p.read_bytes() for p in outputs),
                'negative has no READ_VAL')
    else:
        status_dir = logs / ('child_status_tc%d' % tc)
        expected = {'networksim.exit'}
        expected.update('gem5_node%d.exit' % n for n in range(nodes))
        expected.update('ubio_n%d_s%d.exit' % (n, s)
                        for n in range(nodes) for s in range(sockets))
        statuses = {p.name: p for p in status_dir.glob('*.exit')}
        require(set(statuses) == expected, 'exact managed-child set')
        require(bool(statuses) and all(p.read_text().strip() == '0'
                                      for p in statuses.values()), 'all child exits zero')
        simouts = []
        for n in range(nodes):
            path = logs / ('simout_tc%d_node%d.log' % (tc, n))
            if require(path.is_file(), 'simout node%d' % n):
                simouts.append(str(path))
        faultlogs = [str(p) for p in sorted(logs.glob('ubio_tc*_n*_s*/stdout.log'))]
        faultlogs += [str(p) for p in sorted(logs.glob('gem5_tc*_node*/stderr.log'))]
        faultlogs.append(str(logs / 'launch_manifest.txt'))
        commands = [
            ('science', [sys.executable, str(verifier), '--tc=%d' % tc,
                         '--simout', *simouts, '--fault-log', *faultlogs]),
            ('peer-exit', [sys.executable, str(source / 'scripts/verify_peer_exit_logs.py'),
                           str(logs), '--tc', str(tc), '--num-nodes', str(nodes),
                           '--num-sockets', str(sockets)])]
        for name, command in commands:
            proc = subprocess.run(command, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, timeout=120)
            evidence = out / ('audit-' + name + '.log')
            evidence.write_bytes(proc.stdout)
            require(proc.returncode == 0, name + ' verifier exit')
            if name == 'science':
                require(proc.stdout.rstrip().endswith(
                    ('>>> TC%d PASSED <<<' % tc).encode()), 'exact science sentinel')
        record['peer_verifier_sha256'] = digest(source / 'scripts/verify_peer_exit_logs.py')
    if all(check['passed'] for check in checks):
        record['state'] = 'PASS'
    return record
