"""Run only in the isolated, network-disabled Docker container."""
import hashlib
import json
from pathlib import Path
import subprocess

SOURCE = Path('/mnt/data2/cgc/cc-ep')
DEST = Path('/mnt/data2/cgc/cc-ep-ha-node-20260909')
OUT = DEST / 'ha-evidence'
OUT.mkdir(exist_ok=True)

def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])

manifest = {'baseline_policy': 'Current main and exact recorded gem5 plus inspected dirty source dependencies; published b070/0cc is not mixed in.', 'repositories': []}
for suffix in ('', 'gem5'):
    source, dest = SOURCE / suffix, DEST / suffix
    label = suffix or 'main'
    patch = git(source, 'diff', 'HEAD', '--binary')
    (OUT / (label + '-import.patch')).write_bytes(patch)
    subprocess.run(['git', '-C', str(dest), 'apply', '--check', '-'], input=patch, check=True)
    subprocess.run(['git', '-C', str(dest), 'apply', '-'], input=patch, check=True)
    record = {'source': str(source), 'destination': str(dest),
              'head': git(source, 'rev-parse', 'HEAD').decode().strip(),
              'patch_sha256': hashlib.sha256(patch).hexdigest(),
              'status': git(source, 'status', '--porcelain=v1').decode(),
              'untracked': [], 'files': []}
    for name in git(source, 'ls-files', '--others', '--exclude-standard', '-z').decode().split('\0'):
        if not name:
            continue
        path = source / name
        included = name.startswith(('tests/', 'scripts/'))
        item = {'path': name, 'imported': included, 'reason': 'source/test dependency' if included else 'non-runtime untracked document excluded'}
        if path.is_file():
            data = path.read_bytes()
            item['sha256'] = hashlib.sha256(data).hexdigest()
            if included:
                target = dest / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                target.chmod(path.stat().st_mode)
        record['untracked'].append(item)
    for name in git(dest, 'ls-files', '-z').decode().split('\0'):
        path = dest / name
        if name and path.is_file():
            record['files'].append({'path': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest['repositories'].append(record)
(OUT / 'import-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps([{k: r[k] for k in ('source', 'head', 'patch_sha256')} for r in manifest['repositories']], indent=2))
