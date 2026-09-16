"""Read-only frozen-source and failure-chain inventory (Docker only)."""
import collections
import hashlib
import json
from pathlib import Path

assert Path('/.dockerenv').exists()
root = Path('/e')
rel = 'gem5/src/mem/ruby/protocol/chi/ep/'
for name in ('EPBackend.cc', 'EPBackend.hh', 'UBAdapter.cc', 'EPRNFController.cc'):
    p = root / 'source' / rel / name
    b = p.read_bytes()
    print('SOURCE', name, hashlib.sha256(b).hexdigest())
    if name == 'EPBackend.cc':
        text = b.decode()
        start = text.index('void EPBackend::progressAuthorityRelease()')
        print(text[start:text.index('bool EPBackend::holdAuthority', start)])
for result in sorted((root / 'history-after-publication').glob('hist*/result.json')):
    record = json.loads(result.read_text())
    if not any('/tc%d/' % tc in record['key'] for tc in (143, 147, 230)):
        continue
    audit = json.loads((result.parent / 'profile-audit.json').read_text())
    if audit['state'] == 'PASS':
        continue
    print('CASE', record['key'], result.parent.name, record['state'])
    for p in sorted((result.parent / 'logs').rglob('*.log')):
        if not (p.name == 'stdout.log' or p.name.startswith('simout_tc') or p.name == 'stderr.log'):
            continue
        counts = collections.Counter()
        pending = {}
        last = collections.deque(maxlen=5)
        import re
        with p.open(errors='replace') as f:
            for line in f:
                if '[EP-AUTHORITY-RELEASE]' in line:
                    phase = re.search(r'phase=(\w+)', line)
                    ident = re.search(r'id=(\d+)', line)
                    if phase and ident:
                        counts[phase[1]] += 1
                        if phase[1] == 'native': pending[ident[1]] = line.strip()
                        elif phase[1] == 'retired': pending.pop(ident[1], None)
                if 'neither owner' in line:
                    counts['not_holder'] += 1
                    last.append(line.strip())
                if 'EPRNF-UPGRADE-TERMINAL' in line or 'EP-RETIREMENT-' in line:
                    counts['terminal_or_proof'] += 1
                    last.append(line.strip())
        if counts:
            print(str(p.relative_to(root)), dict(counts), 'PENDING', list(pending.values())[-3:], 'LAST', list(last))
