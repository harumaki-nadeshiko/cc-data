"""Docker-only campaign tick and process snapshot, bounded log scanning."""
import json
from pathlib import Path
import re
import subprocess
import time

C=Path('/campaign')
progress=json.loads((C/'state/progress.json').read_text())
ticks=[]
for item in progress.get('active',[]):
    key=item['job']['key']
    folder=C/'results'/key
    newest=[]
    for p in folder.rglob('*.log'):
        with p.open('rb') as f:
            size=p.stat().st_size
            f.seek(max(0,size-65536))
            text=f.read().decode(errors='replace')
        values=re.findall(r'(?:tick[=: ]+|^)(\d{6,})',text,re.M)
        if values:
            newest.append(dict(file=p.name,last_tick=int(values[-1]),bytes=size))
    ticks.append(dict(key=key,logs=newest))
row=dict(time=time.time(),progress=progress,tick_samples=ticks,
         processes=subprocess.check_output(['ps','-eo','pid,psr,pcpu,comm'],text=True))
path=C/f'observe-{int(time.time())}.json'
path.write_text(json.dumps(row,indent=2)+'\n')
print(json.dumps(dict(snapshot=str(path),phase=progress['phase'],
      passed=progress['completed'],failed=len(progress['failed']),
      running=len(progress['active']),pending=progress['pending'],
      allocated=progress['allocated_logical_cpus'],
      sampled_runs=sum(bool(r['logs']) for r in ticks)),indent=2))
