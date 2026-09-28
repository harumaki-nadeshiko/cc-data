"""Full TC146 regression for the diagnosed persistence pin race; Docker only."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

C=Path('/campaign')
W=Path('/workspace')
previous=Path('/previous/results/m2/2n1s/tc146/p175')
assert not (C/'started.json').exists()
(C/'started.json').write_text(json.dumps(dict(time=time.time(),network='none')))
active=[]
for arm,cpus in [('spill-noopt',list(range(250,260))),('optimized',list(range(304,314)))]:
    out=C/arm
    out.mkdir()
    request=json.loads((previous/arm/'requested.json').read_text())
    env=request['env']
    rid='hr3-'+hashlib.sha256(arm.encode()).hexdigest()[:12]
    env.update(E2E_RUN_ID=rid,E2E_IPC_DIR='/tmp/'+rid,LOG_BASE=str(out),EP_DOCKER_CPUSET=','.join(map(str,cpus)))
    cmd=['taskset','-c',env['EP_DOCKER_CPUSET'],'bash','tests/e2e/run_multi.sh','--2n1s','146']
    (out/'requested.json').write_text(json.dumps(dict(env=env,command=cmd)))
    log=(out/'controller.log').open('x')
    p=subprocess.Popen(cmd,cwd=W,env=env,stdout=log,stderr=subprocess.STDOUT)
    active.append((arm,p,log,out))
results=[]
while active:
    for arm,p,log,out in list(active):
        rc=p.poll()
        if rc is None: continue
        log.close()
        row=dict(arm=arm,rc=rc,status='FAIL')
        if rc==0 and (out/'verify_tc146.log').read_text().rstrip().endswith('>>> TC146 PASSED <<<'):
            exits=[p.read_text().strip() for p in (out/'child_status_tc146').glob('*.exit')]
            if len(exits)==5 and set(exits)=={'0'}:
                row['status']='PASS'
        results.append(row)
        active.remove((arm,p,log,out))
        (out/'result.json').write_text(json.dumps(row))
    temp=C/'progress.tmp'
    temp.write_text(json.dumps(dict(results=results,active=[r[0] for r in active],updated=time.time())))
    temp.replace(C/'progress.json')
    time.sleep(10)
