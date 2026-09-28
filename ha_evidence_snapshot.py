"""Freeze exact revised source inventory and binary identities inside Docker."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent
out = ROOT / 'ha-evidence'
manifest = {'repositories': [], 'binaries': []}
for suffix in ('', 'gem5'):
    root = ROOT / suffix
    label = suffix or 'main'
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args])
    patch = git('diff', 'HEAD', '--binary')
    (out / f'{label}-revised.patch').write_bytes(patch)
    paths = git('ls-files', '-z').decode().split('\0')
    paths += git('ls-files', '--others', '--exclude-standard', '-z').decode().split('\0')
    files = []
    for name in sorted(set(paths)):
        if not name or name.startswith(('ha-evidence/', 'build/')):
            continue
        path = root / name
        if path.is_file():
            files.append({'path': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest['repositories'].append(dict(path=str(root),
        head=git('rev-parse', 'HEAD').decode().strip(),
        branch=git('branch', '--show-current').decode().strip(),
        patch_sha256=hashlib.sha256(patch).hexdigest(), files=files))
for name in ['build/bin/ubio', 'build/bin/networksim', 'build/bin/barrier_manager',
             'gem5/build/ARM/gem5.opt', 'thirdparty/zeromq/lib/libzmq.a']:
    path = ROOT / name
    manifest['binaries'].append(dict(path=name, sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
(out / 'revised-source-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps(manifest['binaries'], indent=2))
