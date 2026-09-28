"""Freeze a standalone source/build inventory, inside disabled-network Docker."""
import hashlib
import json
from pathlib import Path
import tarfile

root = Path(__file__).resolve().parent
out = root / 'ha-evidence/invalidatefix-final-deploy'
out.mkdir(exist_ok=False)
inventory = {}
exclude = {'.git', '__pycache__', '.pytest_cache', 'ha-evidence', '.opencode'}
with tarfile.open(out/'source.tar', 'w') as tar:
    def add(p, rel):
        if p.is_dir():
            for child in sorted(p.iterdir()):
                if child.name not in exclude:
                    add(child, rel/child.name)
        elif p.is_file():
            h = hashlib.sha256()
            with p.open('rb') as f:
                for b in iter(lambda: f.read(1048576), b''):
                    h.update(b)
            inventory[str(rel)] = h.hexdigest()
            tar.add(p.resolve(), arcname=str(Path('source')/rel), recursive=False)
    for p in sorted(root.iterdir()):
        if p.name in exclude or p.name in {'build', 'gem5', 'thirdparty'}:
            continue
        add(p, Path(p.name))
    for p in sorted((root/'gem5').iterdir()):
        if p.name not in exclude | {'build', 'gem5', 'shared_ipc', 'm5out', '.sconsign.dblite'}:
            add(p, Path('gem5')/p.name)
    for rel in ['build/bin', 'build/framework', 'thirdparty/zeromq',
                'gem5/build/ARM/gem5.opt']:
        add(root/rel, Path(rel))
    for name in ['main-import.patch', 'gem5-import.patch', 'import-manifest.json',
                 'zmq-import.json']:
        add(root/'ha-evidence'/name, Path('import-records')/name)
    for name in ['main-final.patch', 'gem5-final.patch']:
        add(root/'ha-evidence/invalidatefix-deploy'/name, Path('import-records')/name)
(out/'inventory.json').write_text(json.dumps(inventory, indent=2)+'\n')
(out/'provenance.json').write_text(json.dumps(dict(
    revision='invalidateonly-persist-before-comp-20260910', reuse=0,
    gem5_base='e604f261585bf02df8e1e714a67c03e72352d87a',
    inventory_sha256=hashlib.sha256((out/'inventory.json').read_bytes()).hexdigest(),
    binaries={k:v for k,v in inventory.items() if k.endswith('gem5.opt') or k.startswith('build/bin/')},
    source_identity='full inventory plus preserved import records; no commit'), indent=2)+'\n')
print(json.dumps(dict(files=len(inventory), output=str(out))), flush=True)
