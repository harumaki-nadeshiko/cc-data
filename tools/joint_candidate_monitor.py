"""Incremental protocol-progress evidence; only owned case containers may stop."""
import collections
import hashlib
import json
from pathlib import Path
import time
import os
from park_validation_driver import api, save

assert Path('/.dockerenv').exists()
root = Path(os.environ.get('JOINT_ROOT', '/mnt/data-xfs/cgc/protocol-retirement-fix-20260915-f'))
offsets = {}
observed = {}
while True:
    running = 0
    for p in root.glob('hist*/result.json'):
        r = json.loads(p.read_text())
        cid = r['container_id']
        state = api('GET', '/containers/'+cid+'/json')['State']
        if not state['Running']: continue
        running += 1
        now = time.monotonic()
        item = observed.setdefault(cid, dict(last_success=now, successes=0, rejects=0))
        new_rejects = 0
        for log in (p.parent/'logs').rglob('*.log'):
            if log.name not in ('stderr.log', 'stdout.log'): continue
            key = str(log)
            offset = offsets.get(key, 0)
            with log.open('rb') as f:
                f.seek(offset)
                data = f.read()
                offsets[key] = f.tell()
            for line in data.decode(errors='replace').splitlines():
                # Only completed outer operations or completed release count;
                # bytes/ticks/negative retries never refresh the success horizon.
                if ('[EP-PERF] kind=outer' in line or
                    '[EP-AUTHORITY-RELEASE] phase=retired' in line or
                    '[UBADAPTER-BARRIER-RELEASE]' in line):
                    item['last_success'] = now
                    item['successes'] += 1
                if 'neither owner' in line or '[EP-UPGRADE-LOCAL-BLOCKED]' in line:
                    new_rejects += 1
        item['rejects'] += new_rejects
        horizon = now-item['last_success']
        save(p.parent/'progress-observation.json', dict(item, no_success_seconds=horizon,
             offsets=offsets, classification='diagnostic, not PASS'))
        if new_rejects >= 1000 and horizon >= 180:
            evidence = p.parent/'scientific-stop'
            evidence.mkdir(exist_ok=True)
            save(evidence/'inspect.json', api('GET', '/containers/'+cid+'/json'))
            save(evidence/'processes.json', api('GET', '/containers/'+cid+'/top?ps_args=aux'))
            save(evidence/'decision.json', dict(state='EARLY_STOP_NO_PROGRESS',
                reason='At least 1000 protocol rejections in one observation window and no successful outer/release completion for 180 seconds',
                no_success_seconds=horizon, offsets=offsets, raw_logs_preserved=True))
            api('POST', '/containers/'+cid+'/stop?t=30')
    if not running and len(list(root.glob('hist*/result.json'))) == int(os.environ.get('JOINT_COUNT', '8')): break
    time.sleep(15)
