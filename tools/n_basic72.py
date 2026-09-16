"""Run canonical registered basic72 on the frozen N deployment; no publication."""
import concurrent.futures
import json
import os
from pathlib import Path
from park_validation_driver import run_job, save
from park_validation_stage import stage
assert Path('/.dockerenv').exists()
os.umask(0)
root=Path('/mnt/data-xfs/cgc/protocol-retirement-fix-20260915-n')
manifest=json.loads((root/'candidate-manifest.json').read_text())
manifest['queue']=stage(root/'source')
(root/'runs').mkdir(exist_ok=True)
# Disjoint from all eight historical CPU assignments (0-48,64-112,...).
pools=[list(range(49,64)),list(range(113,128)),list(range(177,192)),
       list(range(229,244)),list(range(293,308)),list(range(357,372)),
       list(range(421,436)),list(range(485,500))]
jobs=manifest['queue']['jobs']
def worker(index):
    results=[]
    for job in jobs[index::len(pools)]:
        result=run_job(root,manifest,job,'0,1',pools[index],24)
        results.append(result)
        print(json.dumps(dict(key=job['key'],state=result['state'])),flush=True)
    return results
with concurrent.futures.ThreadPoolExecutor(max_workers=len(pools)) as executor:
    results=[r for group in executor.map(worker,range(len(pools))) for r in group]
save(root/'basic72-complete.json',dict(total=len(results),
    counts={s:sum(r['state']==s for r in results) for s in {r['state'] for r in results}},
    source_version=manifest['source_version'],publication_eligible=False))
