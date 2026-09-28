"""Execute inside Docker --network none only."""
from pathlib import Path
import hashlib
import json

root = Path('ha-evidence')
rows = []
dirs = sorted(root.glob('chi-finalfix-v2all-*'))
assert len(dirs) == 22
for directory in dirs:
    verify = next(directory.glob('verify_*.log')).read_text()
    exits = [p.read_text().strip() for p in directory.glob('child_status*/*.exit')]
    assert 'PASSED <<<' in verify
    assert exits and set(exits) == {'0'}
    for path in directory.rglob('*.log'):
        with path.open(errors='replace') as stream:
            for line in stream:
                assert not any(word in line.lower() for word in
                               ('panic:', 'fatal:', 'assert failure')), (path, line)
    rows.append(dict(case=directory.name, status='PASS', child_count=len(exits)))
result = dict(gem5=hashlib.sha256(Path('build/ARM/gem5.opt').read_bytes()).hexdigest(), rows=rows)
(root/'chi-finalfix-v2-local-gates.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result, indent=2))
