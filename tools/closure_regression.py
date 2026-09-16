#!/usr/bin/env python3
"""Immutable diagnostic full registry; same C scheduler/audit, no readiness fiction."""
import concurrent.futures
import hashlib
import json
import math
from pathlib import Path
import sys
from park_validation_driver import cpu_pools, run_job, save
from park_validation_stage import stage

assert Path('/.dockerenv').exists()
root = Path(sys.argv[1]).resolve()
source = root / 'source'
files = {}
for path in sorted(source.rglob('*')):
    if path.is_file():
        h = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(block)
        files[str(path.relative_to(source))] = h.hexdigest()
manifest = dict(files=files, source_version=hashlib.sha256(
    json.dumps(files, sort_keys=True).encode()).hexdigest(), queue=stage(source),
    qualification='diagnostic-all72-not-publication-ready')
save(root / 'manifest.json', manifest)
pending = list(manifest['queue']['jobs'])
pools = cpu_pools()
running = {}
results = []
with concurrent.futures.ThreadPoolExecutor(max_workers=32) as executor:
    while pending or running:
        for job in list(pending):
            if len(running) >= 32: break
            need = job['cpu_floor']
            node = next((n for n, cpus in pools.items() if len(cpus) >= need), None)
            if node is None: continue
            cpus, pools[node] = pools[node][:need], pools[node][need:]
            future = executor.submit(run_job, root, manifest, job, node, cpus, 12)
            running[future] = (node, cpus)
            pending.remove(job)
        assert running
        done, _ = concurrent.futures.wait(running,
            return_when=concurrent.futures.FIRST_COMPLETED)
        for future in done:
            node, cpus = running.pop(future)
            pools[node].extend(cpus)
            result = future.result()
            results.append(result)
            save(root / 'results.json', results)
            print(json.dumps(result), flush=True)
save(root / 'complete.json', dict(source_version=manifest['source_version'],
    counts={state: sum(r['state'] == state for r in results)
            for state in sorted({r['state'] for r in results})}, total=len(results)))
