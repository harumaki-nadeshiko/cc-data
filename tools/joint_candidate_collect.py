"""Preserve owned container output and report verifier/exit closure explicitly."""
import json
import os
from pathlib import Path
import time
from park_validation_driver import api, save
assert Path('/.dockerenv').exists()
root = Path(os.environ.get('JOINT_ROOT', '/mnt/data-xfs/cgc/protocol-retirement-fix-20260915-f'))
while True:
    results = []
    for p in sorted(root.glob('hist*/result.json')):
        r = json.loads(p.read_text())
        state = api('GET', '/containers/'+r['container_id']+'/json')['State']
        if state['Running']:
            results.append(dict(key=r['key'], state='RUNNING'))
            continue
        raw = api('GET', '/containers/'+r['container_id']+'/logs?stdout=1&stderr=1', raw=True)
        chunks = []; i = 0
        while i+8 <= len(raw):
            size = int.from_bytes(raw[i+4:i+8], 'big')
            chunks.append(raw[i+8:i+8+size]); i += 8+size
        body = b''.join(chunks)
        (p.parent/'runner-output.log').write_bytes(body)
        tc = int(r['key'].split('/tc')[1].split('/')[0])
        text = body.decode(errors='replace')
        stopped = p.parent/'scientific-stop/decision.json'
        passed = (state['ExitCode'] == 0 and '>>> TC%d PASSED <<<' % tc in text
                  and 'PeerExit closed and NetworkExit ACK completed' in text)
        results.append(dict(key=r['key'], state='EARLY_STOP_NO_PROGRESS' if stopped.exists()
            else ('PASS_RUNNER_VERIFIER' if passed else 'FAIL'), exit_code=state['ExitCode']))
    counts = {s:sum(r['state']==s for r in results) for s in {r['state'] for r in results}}
    save(root/'observed-results.json', dict(results=results, counts=counts,
         publication_eligible=False, all72_complete=False))
    print(json.dumps(counts), flush=True)
    if len(results)==8 and not counts.get('RUNNING'): break
    time.sleep(30)
