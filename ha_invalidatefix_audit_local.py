"""Docker-only independent audit of completed local gates."""
import importlib.util
import json
from pathlib import Path
import re

root=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('audit',root/'ha-evidence/invalidatefix-final-deploy/auditor.py')
a=importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
a.W=root
rows=[]
for out in sorted((root/'ha-evidence').glob('invalidatefix-*n*s-p*-*-tc*')):
    m=re.fullmatch(r'invalidatefix-(\d+n\d+s)-p(\d+)-(ha-vi|ubcc)-tc(\d+)',out.name)
    if not m:
        continue
    topo,pct,arm,tc=m.groups()
    n,s=map(int,re.findall(r'\d+',topo))
    j=dict(tc=int(tc),n=n,s=s,topology=topo,pct=int(pct),arm=arm,order='AB' if int(tc)%2==0 else 'BA')
    rid=f'if{topo}p{pct}{arm[0]}t{tc}'
    assert f'>>> TC{tc} PASSED <<<' in (out/'runner.log').read_text(),out
    row=dict(j,**a.audit(j,out,rid,0),status='PASS')
    a.write(out/'strict-result.json',row)
    rows.append(dict(directory=out.name,tc=int(tc),topology=topo,pressure=int(pct),arm=arm,status='PASS',child_exits=row['child_exits']))
    print(out.name,'STRICT PASS',flush=True)
a.write(root/'ha-evidence/invalidatefix-local-gates.json',rows)
