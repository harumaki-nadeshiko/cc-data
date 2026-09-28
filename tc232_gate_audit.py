"""Validate all 36 local gates, then atomically publish; Docker only."""
import json
from pathlib import Path
import re

root=Path('ha-evidence')
dirs=sorted(root.glob('tc232-shared-v1-*'))
dirs=[d for d in dirs if (d/'runner.log').exists()]
assert len(dirs)==36, len(dirs)
rows=[]
for d in dirs:
    tc=int(d.name.rsplit('tc',1)[1])
    verify=(d/f'verify_tc{tc}.log').read_text()
    assert verify.rstrip().endswith(f'>>> TC{tc} PASSED <<<'), (str(d),verify)
    n,s=map(int,re.search(r'-(\d+)n(\d+)s-',d.name).groups())
    exits={p.name:p.read_text().strip() for p in (d/f'child_status_tc{tc}').glob('*.exit')}
    expected={'networksim.exit'}|{f'gem5_node{i}.exit' for i in range(n)}|{f'ubio_n{i}_s{k}.exit' for i in range(n) for k in range(s)}
    assert set(exits)==expected and set(exits.values())=={'0'},(d,exits)
    for p in d.rglob('*.log'):
        with p.open(errors='replace') as f:
            for line in f:
                assert not re.search(r'panic:|fatal:|Invalid transition|assert failure|Assertion .*failed',line,re.I),(str(p),line[:500])
    rows.append(dict(case=d.name,status='PASS',child_exits=exits))
p=root/'tc232-shared-local-gates.tmp'
p.write_text(json.dumps(rows,indent=2)+'\n')
p.replace(root/'tc232-shared-local-gates.json')
print(json.dumps(dict(passed=len(rows))))
