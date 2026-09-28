"""Docker --network none only; fresh paired revision, no result reuse."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import traceback

C = Path('/campaign')
W = Path('/workspace')
spec = importlib.util.spec_from_file_location('previous_auditor', C / 'auditor.py')
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)
write, event = old.write, old.event


def main():
    assert not (C / 'state/started.json').exists(), 'No blind restart'
    gates = json.loads((C / 'remote-gates.json').read_text())
    assert len(gates) == 4 and all(g['status'] == 'PASS' for g in gates)
    local = json.loads((C / 'local-gates.json').read_text())
    assert len(local) == 16 and all(g['status'] == 'PASS' for g in local)
    assert (C / 'readiness.json').exists() and (C / 'trace-evidence.json').exists()
    jobs = old.queue()
    write(C / 'queue.json', dict(total=240, jobs=jobs, reuse=0))
    write(C / 'state/started.json', dict(time=time.time(), network='none',
          revision=json.loads((C / 'provenance.json').read_text()),
          numa_pools=['0-127,256-383', '128-255,384-511'],
          memory_policy='first touch, cpuset-mems=0,1; no strict membind'))
    pools = [set(range(128)) | set(range(256,384)),
             set(range(128,256)) | set(range(384,512))]
    active, results, blocked = [], [], None
    while jobs or active:
        for run in list(active):
            rc = run['process'].poll()
            if rc is None:
                exits = (run['out'] / f'child_status_tc{run["job"]["tc"]}').glob('*.exit')
                bad = {p.name: p.read_text().strip() for p in exits
                       if p.read_text().strip() not in ('', '0')}
                if bad and not blocked:
                    blocked = str(bad)
                    event('admission_stopped', reason=blocked)
                continue
            run['log'].close()
            row = dict(run['job'], status='FAIL', return_code=rc,
                       run_id=run['rid'], cpus=run['cpus'], numa=run['numa'],
                       elapsed_sec=time.time()-run['started'])
            try:
                row.update(old.audit(run['job'], run['out'], run['rid'], rc))
                row['status'] = 'PASS'
            except Exception:
                row['reason'] = traceback.format_exc()
                blocked = row['reason']
            write(run['out'] / 'result.json', row)
            results.append(row)
            active.remove(run)
            pools[run['numa']].update(run['cpus'])
            event('result', key=row['key'], status=row['status'], reason=row.get('reason'))
            old.summary(results)
            # Lossless compression only after the independent audit hashes logs.
            for p in run['out'].rglob('*.log'):
                if p.stat().st_size > 1024*1024:
                    subprocess.run(['gzip', '-n', str(p)], check=True)
        if not blocked:
            for job in list(jobs):
                if len(active) >= 16:
                    break
                if sum(len(r['cpus']) for r in active) + job['budget'] > 504:
                    continue
                if shutil.disk_usage(C).free < (100 + 12*(len(active)+1))*2**30:
                    break
                candidates = [i for i in range(2) if len(pools[i]) >= job['budget']]
                if not candidates:
                    continue
                numa = max(candidates, key=lambda i: len(pools[i]))
                cpus = sorted(pools[numa])[:job['budget']]
                pools[numa].difference_update(cpus)
                out = C / 'results' / job['key']
                out.mkdir(parents=True, exist_ok=False)
                rid = hashlib.sha256(('invalidatefix/' + job['key']).encode()).hexdigest()[:16]
                env = old.env_for(job, out, rid, cpus)
                env['EP_SUPERVISOR_DISK_FREE_GB'] = '100'
                cmd = ['taskset', '-c', ','.join(map(str,cpus)), 'bash',
                       'tests/e2e/run_multi.sh', '--'+job['topology'], str(job['tc'])]
                write(out/'requested_launch.json', dict(job=job, env=env,
                      command=cmd, run_id=rid, numa=numa))
                log = (out/'controller.log').open('w')
                process = subprocess.Popen(cmd, cwd=W, env=env, stdout=log,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                active.append(dict(job=job, out=out, rid=rid, cpus=cpus,
                              numa=numa, log=log, process=process, started=time.time()))
                jobs.remove(job)
                event('launch', key=job['key'], cpus=cpus, numa=numa, pid=process.pid)
        write(C/'state/progress.json', dict(total=240,
              phase='BLOCKED' if blocked else 'RUNNING' if jobs or active else 'COMPLETE',
              completed=sum(r['status']=='PASS' for r in results),
              failed=[r['key'] for r in results if r['status']!='PASS'],
              pending=len(jobs), active=[dict(job=r['job'], cpus=r['cpus'],
              pid=r['process'].pid, elapsed_sec=time.time()-r['started']) for r in active],
              allocated_logical_cpus=sum(len(r['cpus']) for r in active),
              blocked=blocked, updated=time.time()))
        if blocked and not active:
            return
        time.sleep(10)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        write(C/'state/controller-error.json', dict(reason=traceback.format_exc()))
        raise
