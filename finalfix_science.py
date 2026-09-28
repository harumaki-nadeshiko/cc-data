"""Same-binary M2/M3 campaign. Execute only in network-disabled Docker.

Readiness failures stop admission; completed evidence is never reused implicitly.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import traceback

C = Path('/campaign')
W = Path('/workspace')
spec = importlib.util.spec_from_file_location('auditor', C / 'auditor.py')
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


def m2_jobs():
    jobs = []
    for n, s, budget in [(2,1,10),(3,1,14),(3,2,20),(4,1,18),
                         (4,2,26),(8,1,34),(8,2,50),(16,1,66)]:
        for tc in range(142,148):
            for pct in (175,200):
                for arm in ('naive','spill-noopt','optimized','IdealDir'):
                    topo = f'{n}n{s}s'
                    jobs.append(dict(key=f'm2/{topo}/tc{tc}/p{pct}/{arm}',
                        metric=2, n=n, s=s, budget=budget, topology=topo,
                        tc=tc, pct=pct, arm=arm, order='AB', seed=0, pair=1))
    assert len(jobs) == 384
    return jobs


def env_for(j, out, rid, cpus):
    e = a.env_for(j, out, rid, cpus)
    e['EP_SUPERVISOR_DISK_FREE_GB'] = '100'
    if j['metric'] == 3:
        return e
    target = 65536*j['pct']//100
    hot = {142:32,143:137,144:192,145:136,146:192,147:136}[j['tc']]
    policy = 'naive' if j['arm']=='naive' else 'spill'
    opts = '--dir-overflow-policy='+policy
    if j['arm']=='IdealDir':
        opts += ' --bloom-bytes=61440 --sram-bytes=2097152 --ways=32 --set-bits=0 --allow-oversized-resident-dir-for-test --batch-rs=0'
    e.update(EP_HA_PROFILE='ubcc', OURCC_CLEAR_PROFILE='ack',
        EP_PERF_PROFILE='spill-noopt' if j['arm']=='IdealDir' else j['arm'],
        UBCC_POLICY=policy, UBCC_OPTS=opts,
        EP_GEM5_OPTS='--silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1' if j['arm']=='optimized' else '--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0',
        L3_PRESSURE_LEVEL='0', EP_L3_PRESSURE_TARGET_LINES='0', EP_TRACK_L3_OCCUPANCY='0',
        PORTABLE_512K_DIR='1', EP_WAIT_BLOOM_READY='1', EP_BLOOM_READY_TIMEOUT_MS='120000',
        TIMEOUT_SEC=str((28800 if j['s']==2 else 21600)*(1.5 if j['pct']==200 else 1)),
        WORKLOAD_CFLAGS=f'-DPORTABLE_PRESSURE_LINES={target-j["n"]*j["s"]*hot} -DPORTABLE_TARGET_FOOTPRINT_LINES={target} -DPORTABLE_NAIVE_CAPACITY_LINES=65536 -DPORTABLE_PRESSURE_LEVEL_PCT={j["pct"]} -DPORTABLE_BATCHES=32')
    e['TIMEOUT_SEC'] = str(int(float(e['TIMEOUT_SEC'])))
    return e


def audit(j,out,rid,rc):
    if j['metric']==3:
        return a.audit(j,out,rid,rc)
    tc,n,s=j['tc'],j['n'],j['s']
    assert rc==0, f'runner rc={rc}'
    assert (out/f'verify_tc{tc}.log').read_text().rstrip().endswith(f'>>> TC{tc} PASSED <<<')
    exits={p.name:p.read_text().strip() for p in (out/f'child_status_tc{tc}').glob('*.exit')}
    expected={'networksim.exit'}|{f'gem5_node{i}.exit' for i in range(n)}|{f'ubio_n{i}_s{k}.exit' for i in range(n) for k in range(s)}
    assert set(exits)==expected and set(exits.values())=={'0'}, exits
    ready=[]
    for p in out.rglob('*.log'):
        with p.open(errors='replace') as f:
            for line in f:
                assert not re.search(r'panic:|fatal:|Invalid transition|assert failure|Assertion .*failed',line,re.I), str(p)+line[:400]
                if '[BLOOM-READY] ' in line:
                    ready.append(line.strip())
    assert len(ready)==n*s,ready
    assert all('enabled=1' in line for line in ready),ready
    if j['arm']!='naive':
        assert all('valid=0xffff' in line for line in ready),ready
    ex=a.module('canonical',W/'scripts/extract_metric123_from_logs.py')
    parsed=ex.extract_run(dict(id=rid,metric=2,tc=tc,topology=j['topology'],simulator_log_dir=str(out),simout_dir=str(out),arm=j['arm'],profile='spill-noopt' if j['arm']=='IdealDir' else j['arm'],repetition='1',pair=f'{j["topology"]}-{tc}-p{j["pct"]}',order='AB'),W,'strict')
    assert parsed['status']=='VALID' and parsed['correctness']['status']=='PASS',parsed
    return dict(parsed=parsed,ready=ready,child_exits=exits,
        workload_sha256=a.sha(W/'build/runs'/rid/'workload.elf'))


def main():
    assert not (C/'state/started.json').exists(), 'No blind restart'
    assert a.sha(W/'gem5/build/ARM/gem5.opt')=='f35172877853a184be149b796fbfe107502a67d1ae2bb2662a380cf03bb94d4f'
    assert a.sha(W/'build/bin/ubio')=='ac5d956ffac60da8079c2bde88203767bb43c5ba95b6a55f8f784a6d734be7a4'
    local=json.loads((C/'local-gates.json').read_text())
    assert len(local)==22 and all(r['status']=='PASS' for r in local)
    m3=a.queue()
    for j in m3:
        j.update(metric=3,key='m3/'+j['key'])
    m3.sort(key=lambda j:(0 if j['topology']=='3n1s' and j['pct']==0 else 1, (230,231,232,233,234,235,228,229).index(j['tc'])))
    m2=m2_jobs()
    jobs=[]
    while m2 or m3:
        if m3: jobs.append(m3.pop(0))
        if m2: jobs.append(m2.pop(0))
    # Broad coverage before admitting the remainder; these are full scientific
    # settings, not reduced workloads, and count once in the 624-run matrix.
    def lead(j):
        return (j['metric']==3 and j['topology']=='3n1s' and j['pct']==0) or (j['metric']==2 and j['topology']=='2n1s' and j['tc']==143 and j['pct']==175)
    jobs.sort(key=lambda j:not lead(j))
    lead_keys={j['key'] for j in jobs if lead(j)}
    assert len(lead_keys)==20
    a.write(C/'queue.json',dict(total=624,m2=384,m3=240,jobs=jobs,reuse=0))
    a.write(C/'state/started.json',dict(time=time.time(),network='none',phase='WAIT_READY_GATES',binary=a.sha(W/'gem5/build/ARM/gem5.opt')))
    while True:
        p=Path('/gates/progress.json')
        g=json.loads(p.read_text()) if p.exists() else {}
        if any(r['status']!='PASS' for r in g.get('completed',[])):
            a.write(C/'state/progress.json',dict(phase='BLOCKED_READY_GATES',gates=g,total=624,pending=624,active=[]))
            return
        if len(g.get('completed',[]))==8 and not g.get('active'):
            break
        a.write(C/'state/progress.json',dict(phase='WAIT_READY_GATES',gates=g,total=624,pending=624,active=[]))
        time.sleep(10)
    pool=set(range(16,512))
    active=[]
    results=[]
    if (C/'resume-from.json').exists():
        previous=Path(json.loads((C/'resume-from.json').read_text())['root'])
        state=json.loads((previous/'state/progress.json').read_text())
        assert not state['active'], 'Previous controller must be drained'
        for p in sorted((previous/'results').rglob('result.json')):
            oldrow=json.loads(p.read_text())
            matches=[j for j in jobs if j['key']==oldrow['key']]
            assert len(matches)==1
            j=matches[0]
            request=json.loads((p.parent/'requested.json').read_text())
            rid=request['env']['E2E_RUN_ID']
            assert oldrow['return_code']==0, 'Never reuse a failed simulation'
            row=dict(j,status='PASS',return_code=0,reuse_evidence=str(p.parent),
                     previous_audit_status=oldrow['status'])
            original_write=a.write
            def revalidation_write(path,value):
                try:
                    relative=path.relative_to(previous)
                except ValueError:
                    return original_write(path,value)
                return original_write(C/'revalidation-sidecars'/relative,value)
            a.write=revalidation_write
            try:
                row.update(audit(j,p.parent,rid,0))
            finally:
                a.write=original_write
            a.write(C/'revalidated'/j['key']/'result.json',row)
            results.append(row)
            jobs.remove(j)
    blocked=None
    while jobs or active:
        for run in list(active):
            rc=run['p'].poll()
            if rc is None:
                bad=[p.name for p in (run['out']/f'child_status_tc{run["j"]["tc"]}').glob('*.exit') if p.read_text().strip() not in ('','0')]
                if bad: blocked=f'{run["j"]["key"]}: child failure {bad}'
                continue
            run['log'].close()
            row=dict(run['j'],status='FAIL',return_code=rc,elapsed_sec=time.time()-run['start'])
            try:
                row.update(audit(run['j'],run['out'],run['rid'],rc))
                row['status']='PASS'
            except Exception:
                row['reason']=traceback.format_exc()
                blocked=row['reason']
            a.write(run['out']/'result.json',row)
            results.append(row)
            pool.update(run['cpus'])
            active.remove(run)
            a.event('result',key=row['key'],status=row['status'],reason=row.get('reason'))
        if not blocked:
            for j in list(jobs):
                if j['key'] not in lead_keys and not lead_keys.issubset({r['key'] for r in results if r['status']=='PASS'}):
                    continue
                if len(active)>=48: break
                if len(pool)<j['budget']: continue
                if shutil.disk_usage(C).free<(100+12*(len(active)+1))*2**30: break
                cpus=sorted(pool)[:j['budget']]
                pool.difference_update(cpus)
                out=C/'results'/j['key']
                out.mkdir(parents=True,exist_ok=False)
                rid=hashlib.sha256(('finalfix-v2-science/'+j['key']).encode()).hexdigest()[:16]
                env=env_for(j,out,rid,cpus)
                cmd=['taskset','-c',','.join(map(str,cpus)),'bash','tests/e2e/run_multi.sh','--'+j['topology'],str(j['tc'])]
                a.write(out/'requested.json',dict(job=j,env=env,command=cmd))
                log=(out/'controller.log').open('x')
                p=subprocess.Popen(cmd,cwd=W,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                active.append(dict(j=j,out=out,rid=rid,cpus=cpus,log=log,p=p,start=time.time()))
                jobs.remove(j)
                a.event('launch',key=j['key'],pid=p.pid,cpus=cpus)
        a.write(C/'state/progress.json',dict(total=624,phase='BLOCKED' if blocked else 'RUNNING' if jobs or active else 'COMPLETE',completed=sum(r['status']=='PASS' for r in results),failed=[r['key'] for r in results if r['status']!='PASS'],pending=len(jobs),active=[dict(key=r['j']['key'],pid=r['p'].pid,cpus=r['cpus'],elapsed_sec=time.time()-r['start']) for r in active],blocked=blocked,updated=time.time()))
        if blocked and not active: return
        time.sleep(10)


if __name__=='__main__':
    try:
        main()
    except Exception:
        a.write(C/'state/controller-error.json',dict(reason=traceback.format_exc(),time=time.time()))
        raise
