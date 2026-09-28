"""Independent fixed 22-case audit; Docker --network none only."""
from pathlib import Path
import json
import re
import os

root=Path('ha-evidence')
rows=[]
version=os.environ.get('HNRELEASE_VERSION','v2')
for arm in ('ubcc','ha-vi'):
    cases=[('3n1s',p,230) for p in (0,50,100)]
    cases += [('3n2s',50,230)]
    cases += [(t,p,tc) for tc in (229,228) for t,p in [('3n1s',0),('3n2s',50)]]
    cases += [('2n1s',p,228) for p in (0,100)] + [('8n1s',0,229)]
    for topo,pct,tc in cases:
        n,s=map(int,re.fullmatch(r'(\d+)n(\d+)s',topo).groups())
        d=root/f'hnrelease-{version}-{topo}-p{pct}-{arm}-tc{tc}'
        assert (d/f'verify_tc{tc}.log').read_text().rstrip().endswith(f'>>> TC{tc} PASSED <<<')
        exits={p.name:p.read_text().strip() for p in (d/f'child_status_tc{tc}').glob('*.exit')}
        expected={'networksim.exit'}|{f'gem5_node{i}.exit' for i in range(n)}|{f'ubio_n{i}_s{k}.exit' for i in range(n) for k in range(s)}
        assert set(exits)==expected and set(exits.values())=={'0'},(d,exits)
        for p in d.rglob('*.log'):
            for line in p.open(errors='replace'):
                assert not re.search(r'panic:|fatal:|assert failure|Assertion .*failed',line,re.I),(p,line)
        rows.append(dict(case=d.name,status='PASS',child_exits=exits))
assert len(rows)==22
(root/f'hnrelease-{version}-local-gates.json').write_text(json.dumps(rows,indent=2)+'\n')
print('22/22 independent audit PASS')
