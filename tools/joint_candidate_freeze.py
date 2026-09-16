"""Hash the diagnostic deployment; explicitly not a publication gate."""
import hashlib
import json
import os
from pathlib import Path
assert Path('/.dockerenv').exists()
root = Path(os.environ.get('JOINT_ROOT', '/mnt/data-xfs/cgc/protocol-retirement-fix-20260915-f'))
files = {}
for p in sorted((root/'source').rglob('*')):
    if not p.is_file(): continue
    h = hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''): h.update(block)
    files[str(p.relative_to(root/'source'))] = h.hexdigest()
record = dict(state='DIAGNOSTIC_NOT_RELEASE', files=files,
    source_version=hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
    baseline='published f82230ab/fee5ff4c',
    warning='No all72 or full eight-failure-config gate completed')
(root/'candidate-manifest.json').write_text(json.dumps(record, indent=2)+'\n')
print(record['source_version'], files['gem5/build/ARM/gem5.opt'])
