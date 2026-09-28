"""Run only in network-disabled Docker after the build completes."""
import hashlib
import json
import os
from pathlib import Path
import tarfile

root = Path(__file__).resolve().parent
out = root / 'ha-evidence' / os.environ.get('DEPLOY_NAME', 'bloomready-deploy')
out.mkdir(exist_ok=False)
inventory = {}
exclude = {'.git', '__pycache__', '.pytest_cache', 'ha-evidence', '.opencode'}
with tarfile.open(out / 'source.tar', 'w') as tar:
    def add(p, rel):
        if p.is_dir():
            for child in sorted(p.iterdir()):
                if child.name not in exclude:
                    add(child, rel / child.name)
        elif p.is_file():
            inventory[str(rel)] = hashlib.sha256(p.read_bytes()).hexdigest()
            tar.add(p.resolve(), arcname=str(Path('source') / rel), recursive=False)
    for p in sorted(root.iterdir()):
        if p.name not in exclude | {'build', 'gem5', 'thirdparty'}:
            add(p, Path(p.name))
    for p in sorted((root / 'gem5').iterdir()):
        if p.name not in exclude | {'build', 'gem5', 'shared_ipc', 'm5out', '.sconsign.dblite'}:
            add(p, Path('gem5') / p.name)
    for rel in ['build/bin', 'build/framework', 'thirdparty/zeromq',
                'gem5/build/ARM/gem5.opt']:
        add(root / rel, Path(rel))
(out / 'inventory.json').write_text(json.dumps(inventory, indent=2) + '\n')
(out / 'provenance.json').write_text(json.dumps(dict(
    baseline='current isolated InvalidateOnly + NodeHA + RecallShared; not old nonHA binary',
    startup='mask bit31 explicit namespace; unchanged syscall/message/body; gate ON/OFF same ELF',
    binaries={k: v for k, v in inventory.items()
              if k.endswith('gem5.opt') or k.startswith('build/bin/')},
    inventory_sha256=hashlib.sha256((out / 'inventory.json').read_bytes()).hexdigest(),
    reuse=0), indent=2) + '\n')
print(out, len(inventory), flush=True)
