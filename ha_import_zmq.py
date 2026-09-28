"""Import the unchanged baseline transport archive, not simulator binaries.

Execute only inside network-disabled project Docker.
"""
import hashlib
import json
from pathlib import Path
import shutil

source = Path('/mnt/data2/cgc/cc-ep/thirdparty/zeromq/lib')
target = Path('thirdparty/zeromq/lib')
target.mkdir(exist_ok=True)
records = []
for path in sorted(source.glob('*')):
    if not path.is_file():
        continue
    dest = target / path.name
    shutil.copy2(path, dest)
    records.append(dict(source=str(path), target=str(dest),
                        sha256=hashlib.sha256(dest.read_bytes()).hexdigest(),
                        reason='Unchanged baseline ZeroMQ transport dependency; '
                               'not HA/UBCC/gem5 implementation or executable'))
Path('ha-evidence/zmq-import.json').write_text(json.dumps(records, indent=2) + '\n')
print(json.dumps(records, indent=2))
