"""Freeze all deployed source and binaries inside networkless Docker."""
import hashlib
import json
from pathlib import Path
import tarfile

root = Path(__file__).resolve().parent
out = root/'ha-evidence/tc232-shared-v1-deploy'
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
        if p.name not in exclude | {'build', 'gem5', 'thirdparty'}:
            add(p, Path(p.name))
    for p in sorted((root/'gem5').iterdir()):
        if p.name not in exclude | {'build', 'gem5', 'shared_ipc', 'm5out', '.sconsign.dblite'}:
            add(p, Path('gem5')/p.name)
    for rel in ['build/bin', 'build/framework', 'thirdparty/zeromq', 'gem5/build/ARM/gem5.opt']:
        add(root/rel, Path(rel))
(out/'inventory.json').write_text(json.dumps(inventory, indent=2)+'\n')
(out/'provenance.json').write_text(json.dumps(dict(revision='tc232-shared-v1', reuse_m3=0,
    binaries={k:v for k,v in inventory.items() if k.endswith('gem5.opt') or k.startswith('build/bin/')},
    inventory_sha256=hashlib.sha256((out/'inventory.json').read_bytes()).hexdigest()), indent=2)+'\n')
print(json.dumps(dict(files=len(inventory), output=str(out))), flush=True)
