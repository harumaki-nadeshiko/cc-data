"""Fresh 240-case M3 revision; independent failure domain, Docker network none."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import time
import traceback

C = Path('/campaign')
W = Path('/workspace')
spec = importlib.util.spec_from_file_location('audit', C/'auditor.py')
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
POOL = set(range(128,256)) | set(range(384,512))


def m2_occupied():
    used = set()
    for root in (Path('/legacy'), Path('/m2')):
        state = json.loads((root/'state/progress.json').read_text())
        used.update(c for r in state['active'] for c in r['cpus'])
    assert POOL.issubset(set(json.loads(Path('/m2/reserved-cpus.json').read_text())))
    return used


def main():
    assert not (C/'state/started.json').exists(), 'No blind restart'
    inventory = json.loads((C/'inventory.json').read_text())
    binary = a.sha(W/'gem5/build/ARM/gem5.opt')
    for rel in ('gem5/build/ARM/gem5.opt','build/bin/ubio'):
        assert a.sha(W/rel) == inventory[rel]
    jobs = a.queue()
    for j in jobs:
        j.update(metric=3, key='m3/'+j['key'])
    def lead(j):
        return (j['tc']==232 and j['topology']=='8n1s' and j['pct']==100) or (
            j['tc'] in (228,230,232) and j['topology']=='3n1s' and j['pct']==0)
    jobs.sort(key=lambda j:(not lead(j), j['tc']!=232, j['topology']!='8n1s', j['pct']!=100))
    leads = {j['key'] for j in jobs if lead(j)}
    assert len(leads)==8
    a.write(C/'queue.json', dict(total=240,jobs=jobs,reuse=0,revision='tc232-shared-v1'))
    a.write(C/'state/started.json', dict(binary=binary,time=time.time(),network='none',
            revision='tc232-shared-v1',cpu_pool=sorted(POOL),reuse=0))
    active, results, blocked = [], [], None
    while jobs or active:
        for run in list(active):
            rc = run['p'].poll()
            if rc is None:
                bad = [p.name for p in (run['out']/f'child_status_tc{run["j"]["tc"]}').glob('*.exit')
                       if p.read_text().strip() not in ('','0')]
                if bad:
                    blocked = str(bad)
                continue
            run['log'].close()
            row = dict(run['j'],status='FAIL',return_code=rc,binary=binary,
                       elapsed_sec=time.time()-run['start'])
            try:
                row.update(a.audit(run['j'],run['out'],run['rid'],rc))
                row['status']='PASS'
            except Exception:
                row['reason']=traceback.format_exc()
                blocked=row['reason']
            a.write(run['out']/'result.json',row)
            results.append(row)
            active.remove(run)
            a.event('result',key=row['key'],status=row['status'],reason=row.get('reason'))
            a.summary(results)
        gatepath = C/'local-gates.json'
        gates = json.loads(gatepath.read_text()) if gatepath.exists() else []
        gates_ok = len(gates)==36 and all(r['status']=='PASS' for r in gates)
        if gates and not gates_ok:
            blocked='Local gate failure or incomplete gate publication'
        occupied = m2_occupied()
        pool = POOL-occupied-{c for r in active for c in r['cpus']}
        if not blocked and gates_ok:
            for j in list(jobs):
                if j['key'] not in leads and not leads.issubset({r['key'] for r in results if r['status']=='PASS'}):
                    continue
                if len(pool)<j['budget'] or len(active)>=48:
                    continue
                if shutil.disk_usage(C).free < (100+12*(len(active)+1))*2**30:
                    break
                cpus=sorted(pool)[:j['budget']]
                assert not (set(cpus)&m2_occupied())
                pool.difference_update(cpus)
                out=C/'results'/j['key']
                out.mkdir(parents=True,exist_ok=False)
                rid=hashlib.sha256(('tc232-shared-v1-science/'+j['key']).encode()).hexdigest()[:16]
                assert not (W/'build/runs'/rid).exists()
                env=a.env_for(j,out,rid,cpus)
                cmd=['taskset','-c',','.join(map(str,cpus)),'bash','tests/e2e/run_multi.sh','--'+j['topology'],str(j['tc'])]
                a.write(out/'requested.json',dict(job=j,env=env,command=cmd,binary=binary))
                log=(out/'controller.log').open('x')
                p=subprocess.Popen(cmd,cwd=W,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                active.append(dict(j=j,out=out,rid=rid,cpus=cpus,log=log,p=p,start=time.time()))
                jobs.remove(j)
                a.event('launch',key=j['key'],pid=p.pid,cpus=cpus)
        phase='BLOCKED' if blocked else 'WAIT_LOCAL_GATES' if not gates_ok else 'RUNNING' if jobs or active else 'COMPLETE'
        a.write(C/'state/progress.json',dict(total=240,phase=phase,
                completed=sum(r['status']=='PASS' for r in results),
                failed=[r['key'] for r in results if r['status']!='PASS'],pending=len(jobs),
                active=[dict(key=r['j']['key'],pid=r['p'].pid,cpus=r['cpus'],
                             elapsed_sec=time.time()-r['start']) for r in active],
                blocked=blocked,updated=time.time(),binary=binary,
                waiting_m2_cpus=sorted(POOL&occupied)))
        if blocked and not active:
            return
        time.sleep(10)


if __name__=='__main__':
    try:
        main()
    except Exception:
        a.write(C/'state/controller-error.json',dict(reason=traceback.format_exc(),time=time.time()))
        raise
