"""Run isolated exact-profile diagnostics concurrently, never publish results."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import time
from park_validation_driver import api, save

assert Path('/.dockerenv').exists()
root = Path(os.environ.get('JOINT_ROOT', '/mnt/data-xfs/cgc/protocol-retirement-fix-20260915-f'))
old = Path('/mnt/data-xfs/cgc/park-validation-resume-20260915/history-after-publication')
os.umask(0)

def run(case, cpus):
    prior = json.loads((old / case / 'result.json').read_text())
    out = root / case
    out.mkdir()
    for name in ('run', 'logs', 'ipc'): (out / name).mkdir()
    (out / 'run/tmp').mkdir()
    env = prior['profile_env'].copy()
    env.update(EP_DOCKER_CPUSET=cpus, TIMEOUT_SEC='3600',
               EP_SUPERVISOR_ETA_CALIBRATION_SEC='14400')
    tc = int(prior['key'].split('/tc')[1].split('/')[0])
    config = dict(Image='ubcc-dev:ubuntu20.04', User='0', WorkingDir='/candidate',
        Cmd=['bash', 'tests/e2e/run_multi.sh', '--'+prior['key'].split('/')[1], str(tc)],
        Env=[k+'='+str(v) for k,v in env.items()],
        HostConfig=dict(NetworkMode='none', CpusetCpus=cpus, Memory=32*1024**3,
            Binds=[str(root/'source')+':/candidate:ro', str(out/'run')+':/candidate/build/runs',
                   str(out/'logs')+':/evidence', str(out/'ipc')+':/ipc']))
    name = root.name + '-' + case
    cid = api('POST', '/containers/create?name='+name, config)['Id']
    record = dict(state='RUNNING', key=prior['key'], container_id=cid,
                  purpose='diagnostic, not full regression', env=env)
    save(out/'result.json', record)
    api('POST', '/containers/'+cid+'/start')
    while api('GET', '/containers/'+cid+'/json')['State']['Running']:
        time.sleep(15)
    state = api('GET', '/containers/'+cid+'/json')['State']
    record.update(state='EXITED_NOT_AUDITED', docker_state=state)
    save(out/'result.json', record)
    print(json.dumps(record), flush=True)

cases = ['hist78fe7a475f5d', 'hista0f3c1562c0d', 'hist3f273c0370dd',
         'hist079c009f9dde', 'hist6978707dd27a', 'histc5b3eda661f5',
         'histccf612263ab4', 'histce79c9ec517e']
pools = ['0-48', '64-112', '128-176', '192-228', '256-292',
         '320-356', '384-420', '448-484']
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    selected = os.environ.get('JOINT_CASE', '')
    futures = [pool.submit(run, case, cpus) for case, cpus in zip(cases, pools)
               if not selected or case == selected]
    for future in futures: future.result()
