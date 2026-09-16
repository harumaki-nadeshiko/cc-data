"""Re-audit historical runs with their exact verifier environment (not defaults)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
root=Path(sys.argv[1]); out=root/'history-after-publication'; source=root/'source'
while True:
    for p in out.glob('hist*/result.json'):
        record=json.loads(p.read_text())
        if record['state']=='RUNNING' or (p.parent/'profile-audit.json').exists(): continue
        old=json.loads((p.parent/'audit.json').read_text()) if (p.parent/'audit.json').exists() else None
        if old is None: continue
        tc=int(record['key'].split('/tc')[1].split('/')[0])
        logs=p.parent/'logs'; env=dict(os.environ); env.update(record['profile_env'])
        cmd=[sys.executable,str(source/'tests/e2e/verify.py'),'--tc='+str(tc),'--simout']
        cmd += [str(x) for x in sorted(logs.glob('simout_tc*_node*.log'))]
        cmd += ['--fault-log'] + [str(x) for x in sorted(logs.glob('ubio_tc*_n*_s*/stdout.log'))]
        cmd += [str(x) for x in sorted(logs.glob('gem5_tc*_node*/stderr.log'))]
        cmd += [str(logs/'launch_manifest.txt')]
        result=subprocess.run(cmd,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=300)
        (p.parent/'profile-audit-science.log').write_bytes(result.stdout)
        nongolden=all(c['passed'] for c in old['checks'] if c['check'] not in
                      ('science verifier exit','exact science sentinel'))
        passed=nongolden and result.returncode==0 and result.stdout.rstrip().endswith(
            ('>>> TC%d PASSED <<<'%tc).encode())
        audit=dict(key=record['key'],state='PASS' if passed else 'FAIL',
                   source_version=record['source_version'],reason='exact recorded profile environment',
                   original_audit_preserved=True,exit_code=result.returncode,other_checks=nongolden)
        (p.parent/'profile-audit.json').write_text(json.dumps(audit,indent=2)+'\n')
        print(json.dumps(audit),flush=True)
    if (out/'complete.json').exists(): break
    time.sleep(20)
results=[json.loads(p.read_text()) for p in out.glob('hist*/profile-audit.json')]
(out/'profile-complete.json').write_text(json.dumps(dict(total=len(results),
    counts={s:sum(r['state']==s for r in results) for s in sorted({r['state'] for r in results})},
    results=results),indent=2)+'\n')
