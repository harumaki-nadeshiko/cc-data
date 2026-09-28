"""Docker-only compact evidence, retaining original full trace files."""
from pathlib import Path
import json
import re
import sys

root = Path(sys.argv[1])
out = Path(sys.argv[2])
selected = []
for p in sorted(root.glob('gem5_tc232_node*/gem5_debug.log')):
    with p.open(errors='replace') as f:
        for line in f:
            if re.search(r'(?:addr[:=] ?|PA=)0x10900000\b', line):
                selected.append(dict(file=str(p), line=line.rstrip()))
out.write_text(json.dumps(selected, indent=2)+'\n')
print(json.dumps(dict(records=len(selected), output=str(out))))
