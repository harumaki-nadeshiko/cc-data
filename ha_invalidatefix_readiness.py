"""Docker-only final deployment identity, geometry and controller checks."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

C=Path('/campaign'); W=Path('/workspace')
spec=importlib.util.spec_from_file_location('a',C/'auditor.py')
a=importlib.util.module_from_spec(spec); spec.loader.exec_module(a)
inventory=json.loads((C/'inventory.json').read_text())
for rel,h in inventory.items():
    assert a.sha(W/rel)==h,rel
assert not inventory['build/bin/ubio'].startswith('505c')
assert not inventory['gem5/build/ARM/gem5.opt'].startswith('6e743')
ast.parse((C/'ha_invalidatefix_campaign.py').read_text())
q=a.queue(); assert len(q)==len({j['key'] for j in q})==240
geometry=[]
for topo,n,s,b in a.TOPOS:
    planes=n*s; cov=a.coverage(n)
    bounds={228:0x700000+(planes-1)*0x10000+16*64,
            229:0x700000+(planes-1)*0x10000+16*64,230:0x700000+16*64,
            231:0x900000+32*64,232:0x900000+8*64,
            233:0x900000+(planes-1)*0x10000+16*64,
            234:0x910000+planes*64,235:0x920000+(planes-1)*0x10000+8*64}
    assert max(bounds.values())<=cov
    assert ((cov//64*n)+7)//8<=524288
    geometry.append(dict(topology=topo,coverage=cov,nodes=n,sockets=s,bounds=bounds))
libs={}
for rel in ['gem5/build/ARM/gem5.opt','build/bin/ubio','build/bin/networksim']:
    run=subprocess.run(['ldd',str(W/rel)],capture_output=True,text=True,
                       env={'PATH':'/usr/bin:/bin','LD_LIBRARY_PATH':'/workspace/thirdparty/zeromq/lib'})
    assert run.returncode==0 and 'not found' not in run.stdout,run.stdout
    libs[rel]=run.stdout
a.write(C/'readiness.json',dict(inventory_files=len(inventory),geometry=geometry,
        queue=240,reuse=0,ldd=libs,controller_sha256=a.sha(C/'ha_invalidatefix_campaign.py')))
print('READINESS PASS',len(inventory),'files; 240 fresh jobs; geometry; dynamic libraries')
