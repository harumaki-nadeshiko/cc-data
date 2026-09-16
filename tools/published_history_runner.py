"""Post-push exact-profile replay using frozen binaries and the C real audit."""
import concurrent.futures
import hashlib
import json
import math
import sys
import time
from pathlib import Path
from park_validation_driver import api, cpu_pools, save, now
from park_validation_audit import audit

assert Path('/.dockerenv').exists()
root = Path(sys.argv[1])
source = root / 'source'
old = Path('/history-old/results')
manifest = json.loads((root / 'manifest.json').read_text())
assert json.loads((root / 'complete.json').read_text())['counts'] == {'PASS': 72}
release = json.loads((root / 'release.json').read_text())
assert release['main'] == 'f82230ab4911765b2f0b896ce3f2bb27c28bdbf5'
assert release['gem5'] == 'fee5ff4cca2dc79368d83fcf91b017fc406bc643'
output = root / 'history-after-publication'
output.mkdir(exist_ok=False)
paths = [old / 'm2/16n1s/tc147/p175/naive/requested.json']
paths += [old / ('m2/8n2s/tc143/p%d/%s/requested.json' % (pct, arm))
          for pct, arm in [(175, 'spill-noopt'), (175, 'optimized'),
                           (200, 'naive'), (200, 'spill-noopt'), (200, 'optimized')]]
paths += sorted(p for p in (old / 'm3').rglob('requested.json')
                if any('/tc%d/' % tc in str(p) for tc in (228,229,230,232,234))
                and ('/3n2s/' in str(p) or '/16n1s/' in str(p)))
requests = []
for p in paths:
    request = json.loads(p.read_text())
    request['historical_origin'] = str(p)
    request['historical_sha256'] = hashlib.sha256(p.read_bytes()).hexdigest()
    requests.append(request)
save(output / 'requested-profiles.json', requests)

def run(request, node, cpus):
    job = dict(request['job'])
    job['role'] = 'historical-after-publication'
    short = 'hist' + hashlib.sha256(job['key'].encode()).hexdigest()[:12]
    out = output / short
    out.mkdir()
    for name in ('run', 'logs', 'ipc'): (out / name).mkdir()
    (out / 'run/tmp').mkdir()
    env = dict(request['env'])
    env.update(E2E_RUN_ID=short, E2E_IPC_DIR='/ipc', LOG_BASE='/evidence',
               HOME='/candidate/build/runs/tmp', TMPDIR='/candidate/build/runs/tmp',
               LD_LIBRARY_PATH='/candidate/build/lib:/candidate/thirdparty/zeromq/lib',
               EP_DOCKER_CPUSET=','.join(map(str, cpus)), CCACHE_DISABLE='1')
    # Trace sampling is the only intentional non-path profile delta. It does
    # not change workload size, timeout, cache geometry, or protocol flags.
    env.update(EP_TRACE_PERF='sample', EP_TRACE_PERF_MAX='2000', EP_TRACE_PERF_FIRST_N='500')
    record = dict(key=job['key'], role=job['role'], source_version=manifest['source_version'],
                  state='RUNNING', started_at=now(), published=release,
                  historical_origin=request['historical_origin'],
                  historical_sha256=request['historical_sha256'], cpus=cpus,
                  profile_env=env, trace_delta='full->sample max2000')
    save(out / 'result.json', record)
    config = dict(Image='ubcc-dev:ubuntu20.04', User='1031:1032', WorkingDir='/candidate',
        Cmd=['bash','tests/e2e/run_multi.sh','--'+job['topology'],str(job['tc'])],
        Env=[k+'='+str(v) for k,v in env.items()],
        HostConfig=dict(NetworkMode='none', Binds=[str(source)+':/candidate:ro',
            str(out/'run')+':/candidate/build/runs',str(out/'logs')+':/evidence',str(out/'ipc')+':/ipc'],
            CpusetCpus=','.join(map(str,cpus)), CpusetMems=node,
            Memory=24*1024**3, MemorySwap=24*1024**3))
    start = time.monotonic()
    try:
        cid = api('POST','/containers/create?name='+short+'-published',config)['Id']
        record['container_id']=cid
        save(out/'result.json',record)
        api('POST','/containers/'+cid+'/start')
        # Long historical cases may exceed the HTTP wait timeout; polling is
        # scoped to this owned container and never restarts a failed job.
        while True:
            state=api('GET','/containers/'+cid+'/json')['State']
            if not state['Running']: break
            time.sleep(15)
        record['exit_code']=state['ExitCode']
        raw=api('GET','/containers/'+cid+'/logs?stdout=1&stderr=1',raw=True)
        with (out/'stdout.raw').open('wb') as f:
            offset=0
            while offset<len(raw):
                size=int.from_bytes(raw[offset+4:offset+8],'big')
                f.write(raw[offset+8:offset+8+size]); offset+=8+size
        checked=audit(source,out,job,record,manifest)
        save(out/'audit.json',checked)
        record['state']=checked['state']
    except Exception as error:
        record.update(state='INFRA_ERROR',error=repr(error))
    record.update(completed_at=now(),elapsed_seconds=time.monotonic()-start)
    save(out/'result.json',record)
    return record

pools=cpu_pools(); pending=list(requests); running={}; results=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
    while pending or running:
        for request in list(pending):
            if len(running)>=10: break
            j=request['job']; need=math.floor(1.5*(1+j['n']+j['n']*j['s']))
            node=next((n for n,c in pools.items() if len(c)>=need),None)
            if node is None: continue
            cpus,pools[node]=pools[node][:need],pools[node][need:]
            running[executor.submit(run,request,node,cpus)]=(node,cpus)
            pending.remove(request)
        done,_=concurrent.futures.wait(running,return_when=concurrent.futures.FIRST_COMPLETED)
        for f in done:
            node,cpus=running.pop(f); pools[node].extend(cpus)
            result=f.result(); results.append(result)
            save(output/'results.json',results); print(json.dumps(result),flush=True)
save(output/'complete.json',dict(total=len(results),counts={s:sum(r['state']==s for r in results)
     for s in sorted({r['state'] for r in results})},source_version=manifest['source_version']))
