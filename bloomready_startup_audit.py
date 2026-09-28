"""Read live logs in Docker; startup proof is separate from E2E PASS."""
import hashlib
import json
from pathlib import Path
import re

C = Path('/campaign')
rows = []
for requested in sorted(C.glob('*/requested.json')):
    out = requested.parent
    req = json.loads(requested.read_text())
    n, s = map(int, re.match(r'(\d+)n(\d+)s', out.name).groups())
    ready, released, first = [], [], []
    for f in out.glob('ubio*/stdout.log'):
        with f.open(errors='replace') as stream:
            for line in stream:
                if '[BLOOM-READY] ' in line:
                    ready.append(dict(re.findall(r'(\w+)=([^\s]+)', line)))
                if '[STARTUP-READY-RELEASE]' in line:
                    released.append(dict(re.findall(r'(\w+)=([^\s]+)', line)))
    for f in out.glob('gem5*/stderr.log'):
        with f.open(errors='replace') as stream:
            for line in stream:
                match = re.search(r'\[TRACE-PERF\] (\d+)\|.*\|SEND\|(ReadReq|WriteReq|UpgradeReq)\|', line)
                if match:
                    first.append(int(match[1]))
                    break
    row = dict(key=out.name, home_count=len(ready), release_count=len(released),
               ready=ready, first_dsm_ticks=first, status='PENDING')
    if len(ready) == n*s and len(released) == n*s and first:
        assert len({r['home'] for r in ready}) == n*s
        max_ready = max(int(r['tick']) for r in ready)
        min_release = min(int(r['tick']) for r in released)
        assert min_release >= max_ready
        assert min(first) > max(int(r['tick']) for r in released)
        if req['gate'] and req['role'] != 'naive':
            assert all(r['valid'] == '0xffff' for r in ready)
        if req['role'] == 'naive':
            assert all(r['required'] == '0' for r in ready)
        row.update(status='STARTUP_PASS', max_ready_tick=max_ready, first_release_tick=min_release)
    elf = C / 'source/build/runs' / req['env']['E2E_RUN_ID'] / 'workload.elf'
    row['elf_sha256'] = hashlib.sha256(elf.read_bytes()).hexdigest()
    rows.append(row)
for role in ('spill-noopt', 'IdealDir'):
    pair = [r for r in rows if r['key'].startswith('2n1s-'+role+'-')]
    assert len(pair) == 2 and pair[0]['elf_sha256'] == pair[1]['elf_sha256']
(C / 'startup-audit.json').write_text(json.dumps(rows, indent=2) + '\n')
print(json.dumps(rows, indent=2))
