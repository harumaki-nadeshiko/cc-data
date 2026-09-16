"""Stop the single verified stalled F job, preserving raw diagnostics first."""
import json
from pathlib import Path
from park_validation_driver import api, save
assert Path('/.dockerenv').exists()
root=Path('/mnt/data-xfs/cgc/protocol-retirement-fix-20260915-f')
job=root/'hist079c009f9dde'
r=json.loads((job/'result.json').read_text())
assert r['key']=='m2/8n2s/tc143/p200/naive'
p=json.loads((job/'progress-observation.json').read_text())
assert p['no_success_seconds']>180 and p['rejects']>1000
cid=r['container_id']; info=api('GET','/containers/'+cid+'/json')
assert info['Name']=='/protocol-retirement-fix-20260915-f-hist079c009f9dde'
out=job/'scientific-stop'; out.mkdir(exist_ok=True)
save(out/'inspect.json',info)
save(out/'processes.json',api('GET','/containers/'+cid+'/top?ps_args=aux'))
save(out/'progress.json',p)
snap=[]
for path in (job/'logs').rglob('*.log'):
    st=path.stat(); offset=max(0,st.st_size-65536)
    with path.open('rb') as f:
        f.seek(offset); data=f.read()
    name='log-%d.raw'%len(snap); (out/name).write_bytes(data)
    snap.append(dict(path=str(path),size=st.st_size,mtime_ns=st.st_mtime_ns,
                     byte_offset=offset,snapshot=name))
save(out/'logs.json',snap)
save(out/'decision.json',dict(state='EARLY_STOP_NO_PROGRESS',
    reason='Operator scoped stop: >180s no successful completion, >1000 accumulated protocol rejections; per-window threshold missed slower repeat loop',
    no_success_seconds=p['no_success_seconds'],raw_logs_preserved=True))
api('POST','/containers/'+cid+'/stop?t=30')
print('EARLY_STOP_NO_PROGRESS',cid)
