"""Independent strict audits using identical immutable deployed binaries."""
import importlib.util
import json
from pathlib import Path
import subprocess

C=Path('/campaign')
spec=importlib.util.spec_from_file_location('audit', C/'auditor.py')
a=importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
rows=[]
for topo,pct,arm in [('3n1s',0,'ha-vi'),('3n2s',50,'ha-vi'),
                     ('3n1s',0,'ubcc'),('3n2s',50,'ubcc')]:
    j=next(j for j in a.queue() if j['tc']==229 and j['topology']==topo
           and j['pct']==pct and j['arm']==arm)
    out=C/'remote-gates'/j['key']
    out.mkdir(parents=True,exist_ok=False)
    rid=f'rg{topo}p{pct}{arm[0]}'
    env=a.env_for(j,out,rid,list(range(32)))
    if topo=='3n1s' and arm=='ha-vi':
        env['GEM5_DEBUG_FLAGS']='RubyGenerated,RubyCHIGeneric'
    with (out/'runner.log').open('w') as log:
        rc=subprocess.run(['bash','tests/e2e/run_multi.sh','--'+topo,'229'],
                          cwd='/workspace',env=env,stdout=log,stderr=subprocess.STDOUT).returncode
    row=dict(j,**a.audit(j,out,rid,rc),status='PASS')
    a.write(out/'result.json',row)
    rows.append(row)
    a.write(C/'remote-gates.json',rows)
    print(j['key'],'STRICT PASS',flush=True)
