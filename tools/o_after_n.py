"""One-shot O deployment controller. Reads N only; never stops N or edits code."""
import concurrent.futures
import collections
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import time
from park_validation_driver import api, save, sha, run_job, cpu_pools, now
from park_validation_stage import stage
import park_validation_driver as driver

# Remote image defaults to an unprivileged user; use the same root runtime as N.
_api = api
def api(method, route, data=None, raw=False):
    if method == 'POST' and route.startswith('/containers/create'):
        data = dict(data, User='0')
    return _api(method, route, data, raw)
driver.api = api

N = Path('/mnt/data-xfs/cgc/protocol-retirement-fix-20260915-n')
O = Path('/mnt/data-xfs/cgc/protocol-retirement-fix-20260916-o')
TERMINAL = {'PASS','FAIL','INFRA_ERROR','EARLY_STOP_NO_PROGRESS','PASS_RUNNER_VERIFIER'}

def group_terminal(expected, results, active, controller_running, explicit=None):
    if active: return False
    complete = len(results) == expected and all(r.get('state') in TERMINAL for r in results)
    # A dead controller with unfinished jobs is stopped, not a successful suite.
    return complete or explicit in ('COMPLETE','BLOCKED','FAILED') or not controller_running

def snapshot_n():
    containers = api('GET','/containers/json?all=1')
    owned = []
    controllers = {}
    for c in containers:
        names = c.get('Names',[])
        for group, name in [('history','/joint-retirement-n-controller'),('basic','/joint-retirement-n-basic72')]:
            if name in names: controllers[group] = c['State'] == 'running'
        # Bind source paths identify all native/gem5 children, not collector uptime.
        if c['State'] == 'running':
            info = api('GET','/containers/'+c['Id']+'/json')
            if any(m.get('Source','').startswith(str(N)) and m.get('Destination') == '/candidate'
                   for m in info.get('Mounts',[])):
                owned.append(c['Id'])
    histories = json.loads((N/'observed-results.json').read_text())['results']
    basics = [json.loads(p.read_text()) for p in (N/'runs').glob('*/result.json')]
    groups = {}
    for group, rows, total in [('history',histories,8),('basic',basics,72)]:
        active = []
        paths = N.glob('hist*/result.json') if group == 'history' else (N/'runs').glob('*/result.json')
        for p in paths:
            r = json.loads(p.read_text())
            if r.get('container_id'):
                st = api('GET','/containers/'+r['container_id']+'/json')['State']
                if st['Running'] or st.get('Restarting'): active.append(r['container_id'])
        assert group in controllers, 'missing N controller identity'
        terminal = group_terminal(total,rows,active,controllers[group])
        groups[group] = dict(total=total,counts=dict(collections.Counter(r['state'] for r in rows)),
                             pending=total-len(rows), active=active,controller_running=controllers[group],
                             terminal=terminal, state='COMPLETE' if len(rows)==total and terminal else
                             ('BLOCKED' if terminal else 'RUNNING'))
    return dict(groups=groups,active_children=owned,
                terminal=not owned and all(g['terminal'] for g in groups.values()),observed_at=now())

def verify_o():
    m = json.loads((O/'candidate-manifest.json').read_text())
    assert m['build_verified'] and m['state']=='O_BUILD_PASS_NOT_RELEASE'
    files = m['files']
    assert hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()==m['source_version']
    assert {str(p.relative_to(O/'source')) for p in (O/'source').rglob('*') if p.is_file()}==set(files)
    for name, expected in files.items(): assert sha(O/'source'/name)==expected, name
    n = json.loads((N/'candidate-manifest.json').read_text())
    changed = [k for k in files if k in n['files'] and files[k]!=n['files'][k]]
    save(O/'n-o-manifest-diff.json',dict(changed=changed,added=sorted(set(files)-set(n['files'])),
        removed=sorted(set(n['files'])-set(files)), n_source=n['source_version'],o_source=m['source_version']))
    m['queue'] = stage(O/'source')
    return m

def history_job(m, case, prior, node, cpus):
    out = O/case; out.mkdir()
    for d in ('run/tmp','logs','ipc'): (out/d).mkdir(parents=True)
    env = prior['env'].copy()
    short = 'o'+hashlib.sha256((m['source_version']+case).encode()).hexdigest()[:14]
    env.update(E2E_RUN_ID=short,EP_DOCKER_CPUSET=','.join(map(str,cpus)),
               E2E_IPC_DIR='/ipc',LOG_BASE='/evidence',TMPDIR='/candidate/build/runs/tmp')
    config = dict(Image='ubcc-dev:ubuntu20.04',WorkingDir='/candidate',
        Cmd=['bash','tests/e2e/run_multi.sh','--'+prior['key'].split('/')[1],prior['key'].split('/tc')[1].split('/')[0]],
        Env=[k+'='+str(v) for k,v in env.items()],HostConfig=dict(NetworkMode='none',
        CpusetCpus=env['EP_DOCKER_CPUSET'],CpusetMems=node,Memory=32*1024**3,MemorySwap=32*1024**3,
        Binds=[str(O/'source')+':/candidate:ro',str(out/'run')+':/candidate/build/runs',
               str(out/'logs')+':/evidence',str(out/'ipc')+':/ipc']))
    r = dict(key=prior['key'],source_version=m['source_version'],state='RUNNING',env=env,started_at=now(),cpus=cpus)
    save(out/'requested.json',config)
    cid = api('POST','/containers/create?name=oc'+short,config)['Id']
    r['container_id']=cid;save(out/'result.json',r)
    api('POST','/containers/'+cid+'/start')
    status = api('POST','/containers/'+cid+'/wait?condition=not-running')
    raw = api('GET','/containers/'+cid+'/logs?stdout=1&stderr=1',raw=True)
    body=b''; i=0
    while i+8<=len(raw):
        size=int.from_bytes(raw[i+4:i+8],'big');body+=raw[i+8:i+8+size];i+=8+size
    (out/'runner-output.log').write_bytes(body)
    tc = prior['key'].split('/tc')[1].split('/')[0]
    passed = status['StatusCode']==0 and ('>>> TC'+tc+' PASSED <<<').encode() in body and b'PeerExit closed and NetworkExit ACK completed' in body
    r.update(state='PASS_RUNNER_VERIFIER' if passed else 'FAIL',exit_code=status['StatusCode'],completed_at=now())
    save(out/'result.json',r);return r

def run_all(m, n_snapshot):
    claim=O/'launch-claim.json'
    with claim.open('x') as f: json.dump(dict(source_version=m['source_version'],n=n_snapshot,started_at=now()),f)
    pending=[]
    for p in sorted(N.glob('hist*/result.json')):
        prior=json.loads(p.read_text());top=prior['key'].split('/')[1];n,s=map(int,top.replace('s','').split('n'))
        pending.append(dict(kind='history',case=p.parent.name,prior=prior,need=math.floor(1.5*(1+n+n*s)),memory=32))
    assert len(pending)==8
    pending += [dict(kind='basic',job=j,need=j['cpu_floor'],memory=12) for j in m['queue']['jobs']]
    pools=cpu_pools(); assert sum(map(len,pools.values()))==512, 'expected qualified 512 CPU machine'
    running={};results=[];memory=0
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as executor:
        while pending or running:
            for j in list(pending):
                if memory+j['memory']>512:continue
                node=next((n for n,c in pools.items() if len(c)>=j['need']),None)
                if node is None:continue
                cpus=pools[node][:j['need']];del pools[node][:j['need']]
                if j['kind']=='history': f=executor.submit(history_job,m,j['case'],j['prior'],node,cpus)
                else:f=executor.submit(run_job,O,m,j['job'],node,cpus,j['memory'])
                running[f]=(node,cpus,j);pending.remove(j);memory+=j['memory']
            save(O/'chain-state.json',dict(state='RUNNING_O',n=n_snapshot,o_source=m['source_version'],
                pending=len(pending),active=len(running),counts=dict(collections.Counter(r['state'] for r in results)),
                cpu_leases=[c for _,cpus,_ in running.values() for c in cpus],memory_gib=memory,updated_at=now()))
            done,_=concurrent.futures.wait(running,timeout=30,return_when=concurrent.futures.FIRST_COMPLETED)
            for f in done:
                node,cpus,j=running.pop(f);pools[node].extend(cpus);pools[node].sort();memory-=j['memory']
                try: results.append(f.result())
                except Exception as e:results.append(dict(state='INFRA_ERROR',job=j,error=repr(e)))
    save(O/'chain-state.json',dict(state='COMPLETE' if all(r['state'] in ('PASS','PASS_RUNNER_VERIFIER') for r in results) else 'FAILED',
        n=n_snapshot,o_source=m['source_version'],total=len(results),results=results,updated_at=now(),publication_eligible=False))

def main():
    assert Path('/.dockerenv').exists()
    os.umask(0)
    lock=(O/'chain.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    assert not (O/'launch-claim.json').exists(), 'one-shot: existing launch must not repeat'
    m=None
    while True:
        n=snapshot_n()
        if (O/'BUILD_FAILED.json').exists():raise RuntimeError((O/'BUILD_FAILED.json').read_text())
        if m is None and (O/'BUILD_READY.json').exists():m=verify_o()
        save(O/'chain-state.json',dict(state='WAIT_N' if not n['terminal'] else ('READY_O' if m else 'BUILD_O'),
            n=n,o_build='PASS' if m else 'BUILD_O',o_source=m['source_version'] if m else None,updated_at=now(),
            policy='N both groups terminal and no owned children; N failures do not block O; 80 fresh cases'))
        if m and n['terminal']:
            m=verify_o();n=snapshot_n()
            if n['terminal']:run_all(m,n);return
        time.sleep(30)

if __name__=='__main__':
    try:main()
    except Exception as e:
        save(O/'chain-state.json',dict(state='FAILED',error=repr(e),updated_at=now(),publication_eligible=False))
        raise
