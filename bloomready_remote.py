"""Focused full-application readiness gates; Docker --network none only."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

C = Path('/campaign')
W = Path('/workspace')


def write(p, value):
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(p)


def main():
    assert not (C / 'started.json').exists(), 'No blind restart'
    old = Path('/old/tests/e2e/workloads/e2e_tc142_db_oltp_buffer_pool.c').read_bytes()
    new = (W / 'tests/e2e/workloads/e2e_tc142_db_oltp_buffer_pool.c').read_bytes()
    assert new.replace(b'    portable_wait_ready();\n', b'') == old, 'TC142 seed/ops drift'
    write(C / 'started.json', dict(time=time.time(), workload_baseline_sha256=hashlib.sha256(old).hexdigest(),
          baseline='new gem5/UBIO; frozen TC142 body plus startup call; same build ON/OFF', network='none'))
    jobs = [(2, 1, role, gate) for role in ('naive', 'spill-noopt', 'IdealDir') for gate in (0, 1)]
    jobs += [(4, 2, role, 1) for role in ('spill-noopt', 'IdealDir')]
    active = []
    cpu = int(os.environ.get('GATE_CPU_START', '16'))
    pool = list(map(int, os.environ['GATE_CPU_LIST'].split(','))) if os.environ.get('GATE_CPU_LIST') else None
    cursor = 0
    for n, s, role, gate in jobs:
        key = f'{n}n{s}s-{role}-on{gate}'
        out = C / key
        out.mkdir(exist_ok=False)
        rid = 'br' + hashlib.sha256(key.encode()).hexdigest()[:16]
        budget = 2 * (1+n+n*s)
        selected = pool[cursor:cursor+budget] if pool is not None else list(range(cpu,cpu+budget))
        assert len(selected) == budget
        cpus = ','.join(map(str, selected))
        cursor += budget
        cpu += 2 * (1+n+n*s)
        policy = 'naive' if role == 'naive' else 'spill'
        opts = f'--dir-overflow-policy={policy}'
        if role == 'IdealDir':
            opts += ' --bloom-bytes=61440 --sram-bytes=2097152 --ways=32 --set-bits=0 --allow-oversized-resident-dir-for-test --batch-rs=0'
        target = 65536 * 175 // 100
        env = dict(PATH=os.environ['PATH'], HOME='/tmp', LANG='C.UTF-8',
            LD_LIBRARY_PATH='/workspace/thirdparty/zeromq/lib', PYTHONDONTWRITEBYTECODE='1',
            E2E_RUN_ID=rid, E2E_IPC_DIR='/tmp/'+rid, LOG_BASE=str(out), TIMEOUT_SEC='21600',
            EP_CPU_MODEL='o3', EP_SEQUENCER_MAX_OUTSTANDING='16', EP_TRACE_PERF='full',
            EP_TRACE_PERF_MAX='1000000000', EP_TRACE_CHAIN_OUTPUT='0',
            EP_PERF_PROFILE='spill-noopt' if role == 'IdealDir' else role,
            UBCC_POLICY=policy, UBCC_OPTS=opts, EP_GEM5_OPTS='--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0',
            EP_HA_PROFILE='ubcc', OURCC_CLEAR_PROFILE='ack', EP_L3_SIZE='256kB', EP_L3_ASSOC='16',
            L3_PRESSURE_LEVEL='0', EP_L3_PRESSURE_TARGET_LINES='0', EP_TRACK_L3_OCCUPANCY='0',
            EP_LINK_LATENCY_PS='2500', EP_SYNC_INTERVAL_PS='2500', EP_DSM_DATA_DELAY_PS='68000',
            UBCC_METADATA_SIZE='134217728', EP_PORT_HWM='8192', EP_NSIM_MAX_PENDING='65536',
            EP_DOCKER_CPUSET=cpus, CCACHE_DISABLE='1', EP_SUPERVISOR='1', EP_SUPERVISOR_INTERVAL='30',
            EP_SUPERVISOR_LOG_CEIL_GB='12', EP_SUPERVISOR_DISK_FREE_GB='100',
            EP_SUPERVISOR_PROGRESS_STALL_SEC='1800', EP_SUPERVISOR_STARTUP_STALL_SEC='600',
            EP_SUPERVISOR_ETA_CALIBRATION_SEC='21600', PORTABLE_512K_DIR='1',
            EP_WAIT_BLOOM_READY=str(gate), EP_BLOOM_READY_TIMEOUT_MS='120000',
            WORKLOAD_CFLAGS=f'-DPORTABLE_PRESSURE_LINES={target-n*s*32} -DPORTABLE_TARGET_FOOTPRINT_LINES={target} -DPORTABLE_NAIVE_CAPACITY_LINES=65536 -DPORTABLE_PRESSURE_LEVEL_PCT=175 -DPORTABLE_BATCHES=32')
        cmd = ['taskset', '-c', cpus, 'bash', 'tests/e2e/run_multi.sh', f'--{n}n{s}s', '142']
        write(out / 'requested.json', dict(env=env, command=cmd, role=role, gate=gate))
        log = (out / 'controller.log').open('x')
        proc = subprocess.Popen(cmd, cwd=W, env=env, stdout=log, stderr=subprocess.STDOUT)
        active.append(dict(key=key, n=n, s=s, gate=gate, role=role, rid=rid, out=out, proc=proc, log=log))
    results = []
    while active:
        for run in list(active):
            rc = run['proc'].poll()
            if rc is None:
                continue
            run['log'].close()
            out = run['out']
            row = dict(key=run['key'], rc=rc, status='FAIL')
            try:
                assert rc == 0, f'runner rc={rc}'
                assert (out / 'verify_tc142.log').read_text().rstrip().endswith('>>> TC142 PASSED <<<')
                exits = [p.read_text().strip() for p in (out / 'child_status_tc142').glob('*.exit')]
                assert len(exits) == 1+run['n']+run['n']*run['s'] and set(exits) == {'0'}
                ready = []
                for p in out.rglob('*.log'):
                    with p.open(errors='replace') as f:
                        for line in f:
                            assert not re.search(r'panic:|fatal:|assert failure', line, re.I), line[:500]
                            if '[BLOOM-READY] ' in line:
                                ready.append(line.strip())
                assert len(ready) == run['n']*run['s'], ready
                if run['gate'] and run['role'] != 'naive':
                    assert all('valid=0xffff' in line for line in ready), ready
                row.update(status='PASS', ready=ready,
                    workload_sha256=hashlib.sha256((W/'build/runs'/run['rid']/'workload.elf').read_bytes()).hexdigest())
            except Exception as error:
                row['reason'] = str(error)
            write(out / 'result.json', row)
            results.append(row)
            active.remove(run)
        write(C / 'progress.json', dict(total=len(jobs), completed=results,
              active=[r['key'] for r in active], updated=time.time()))
        time.sleep(10)


if __name__ == '__main__':
    main()
