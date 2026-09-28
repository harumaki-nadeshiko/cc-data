"""Bounded sequential checks/pairs; run inside network-disabled Docker only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('--tag', required=True)
parser.add_argument('--topology', default='2n1s')
parser.add_argument('--pressure', default='100')
parser.add_argument('--arms', default='ha-vi')
parser.add_argument('--tcs', default='229,230,235')
args = parser.parse_args()
out = ROOT / 'ha-evidence' / args.tag
out.mkdir(exist_ok=True)
manifest = {'topology': args.topology, 'pressure': args.pressure,
            'seed': 0, 'jobs': [], 'binaries': {}}
for name in ['build/bin/ubio', 'build/bin/networksim', 'gem5/build/ARM/gem5.opt']:
    manifest['binaries'][name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
for tc in map(int, args.tcs.split(',')):
    arms = args.arms.split(',')
    if tc % 2:
        arms.reverse()
    for arm in arms:
        job = {'tc': tc, 'arm': arm, 'order': 'AB' if tc % 2 == 0 else 'BA'}
        folder = out / f'tc{tc}-{arm}'
        folder.mkdir(exist_ok=True)
        job['log_dir'] = str(folder)
        env = dict(os.environ, EP_HA_PROFILE=arm,
                   OURCC_CLEAR_PROFILE='lossless-oneway' if arm == 'ubcc' else 'ack',
                   EP_CPU_MODEL='o3', EP_SEQUENCER_MAX_OUTSTANDING='16',
                   EP_L3_SIZE='256KiB', EP_L3_ASSOC='16',
                   L3_PRESSURE_LEVEL=args.pressure, METRIC3_L3_SEED='0',
                   EP_DSM_DATA_DELAY_PS='68000', EP_TRACE_PERF='sample',
                   E2E_RUN_ID=f'{hashlib.sha256(args.tag.encode()).hexdigest()[:6]}{tc}{arm[0]}',
                   E2E_IPC_DIR=f'/tmp/b{tc}{arm[0]}',
                   LOG_BASE=str(folder), TIMEOUT_SEC='1200')
        command = ['bash', 'tests/e2e/run_multi.sh', '--' + args.topology, str(tc)]
        job['command'] = command
        job['env'] = {k: v for k, v in env.items() if k.startswith(('EP_', 'OURCC_', 'E2E_', 'L3_', 'METRIC3_', 'LOG_BASE', 'TIMEOUT_SEC'))}
        start = time.time()
        with (folder / 'runner.log').open('w') as log:
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=log,
                                    stderr=subprocess.STDOUT)
        job['wall_seconds'] = time.time() - start
        job['returncode'] = result.returncode
        text = (folder / 'runner.log').read_text()
        job['passed'] = result.returncode == 0 and f'>>> TC{tc} PASSED <<<' in text
        manifest['jobs'].append(job)
        (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        print(f"TC{tc} {arm}: {'PASS' if job['passed'] else 'FAIL'}", flush=True)
        if not job['passed']:
            sys.exit(1)
