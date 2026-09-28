"""Frozen-revision M2 continuation; Docker --network none only.

The original controller MUST remain blocked. Never restart this controller:
claims and unique run directories intentionally fail closed after interruption.
"""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import traceback

O = Path('/campaign')
C = Path('/continuation')
spec = importlib.util.spec_from_file_location('science', O/'finalfix_science.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
a = s.a
a.C = C


def original():
    state = json.loads((O/'state/progress.json').read_text())
    assert state['phase'] == 'BLOCKED' and state['blocked'], 'Original admission must remain stopped'
    return state


def main():
    assert not (C/'state/started.json').exists(), 'No blind restart'
    binary = a.sha(s.W/'gem5/build/ARM/gem5.opt')
    assert binary == 'f35172877853a184be149b796fbfe107502a67d1ae2bb2662a380cf03bb94d4f'
    assert a.sha(s.W/'build/bin/ubio') == 'ac5d956ffac60da8079c2bde88203767bb43c5ba95b6a55f8f784a6d734be7a4'
    state = original()
    # Requested records cover active AND completed original launches; revalidated
    # records cover inherited evidence. Exclude all, irrespective of status.
    excluded = {r['key'] for r in state['active']}
    for pattern in ('results/**/requested.json', 'results/**/result.json',
                    'revalidated/**/result.json'):
        for p in O.glob(pattern):
            r = json.loads(p.read_text())
            excluded.add(r.get('job', r)['key'])
    jobs = [j for j in s.m2_jobs() if j['key'] not in excluded]
    assert all(k.startswith('m3/') for k in state['failed']), state['failed']
    a.write(C/'queue.json', dict(total=len(jobs), metric=2, jobs=jobs,
                                excluded_original=sorted(excluded), original=str(O)))
    a.write(C/'state/started.json', dict(time=time.time(), binary=binary,
             network='none', revision='HNrelease-v3-frozen-M2', original=str(O)))
    active, results, blocked = [], [], None
    while jobs or active:
        old = original()
        if any(k.startswith('m2/') for k in old['failed']):
            blocked = 'Original M2 failure: '+str(old['failed'])
        for run in list(active):
            rc = run['p'].poll()
            if rc is None:
                bad = [p.name for p in (run['out']/f'child_status_tc{run["j"]["tc"]}').glob('*.exit')
                       if p.read_text().strip() not in ('', '0')]
                if bad:
                    blocked = str(bad)
                continue
            run['log'].close()
            row = dict(run['j'], status='FAIL', return_code=rc,
                       elapsed_sec=time.time()-run['start'], binary=binary)
            try:
                row.update(s.audit(run['j'], run['out'], run['rid'], rc))
                row['status'] = 'PASS'
            except Exception:
                row['reason'] = traceback.format_exc()
                blocked = row['reason']
            a.write(run['out']/'result.json', row)
            results.append(row)
            active.remove(run)
            a.event('result', key=row['key'], status=row['status'])
        reserved_path = C/'reserved-cpus.json'
        reserved = set(json.loads(reserved_path.read_text())) if reserved_path.exists() else set()
        used = {c for r in old['active'] for c in r['cpus']}
        used.update(c for r in active for c in r['cpus'])
        pool = set(range(16, 512))-used-reserved
        if not blocked:
            for j in list(jobs):
                if len(active) >= 48 or len(pool) < j['budget']:
                    continue
                if shutil.disk_usage(C).free < (100+12*(len(active)+len(old['active'])+1))*2**30:
                    break
                original()  # Never admit if the legacy domain was unblocked.
                with (C/'claims.lock').open('a') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    claim = C/'claims'/j['key']
                    claim.parent.mkdir(parents=True, exist_ok=True)
                    with claim.open('x') as f:
                        f.write(str(time.time()))
                cpus = sorted(pool)[:j['budget']]
                pool.difference_update(cpus)
                out = C/'results'/j['key']
                out.mkdir(parents=True, exist_ok=False)
                rid = hashlib.sha256(('hnrelease-v3-m2-continuation-20260911/'+j['key']).encode()).hexdigest()[:16]
                assert not (s.W/'build/runs'/rid).exists()
                env = s.env_for(j, out, rid, cpus)
                cmd = ['taskset', '-c', ','.join(map(str, cpus)), 'bash',
                       'tests/e2e/run_multi.sh', '--'+j['topology'], str(j['tc'])]
                a.write(out/'requested.json', dict(job=j, env=env, command=cmd, binary=binary))
                log = (out/'controller.log').open('x')
                p = subprocess.Popen(cmd, cwd=s.W, env=env, stdout=log,
                                     stderr=subprocess.STDOUT, start_new_session=True)
                active.append(dict(j=j, out=out, rid=rid, cpus=cpus, log=log, p=p, start=time.time()))
                jobs.remove(j)
                a.event('launch', key=j['key'], pid=p.pid, cpus=cpus)
        a.write(C/'state/progress.json', dict(total=len(results)+len(jobs)+len(active),
                phase='BLOCKED' if blocked else 'RUNNING' if jobs or active else 'COMPLETE',
                completed=sum(r['status']=='PASS' for r in results),
                failed=[r['key'] for r in results if r['status']!='PASS'], pending=len(jobs),
                active=[dict(key=r['j']['key'], pid=r['p'].pid, cpus=r['cpus'],
                             elapsed_sec=time.time()-r['start']) for r in active],
                blocked=blocked, updated=time.time(), binary=binary,
                legacy_active=old['active'], reserved_cpus=sorted(reserved)))
        if blocked and not active:
            return
        time.sleep(10)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        a.write(C/'state/controller-error.json', dict(reason=traceback.format_exc(), time=time.time()))
        raise
