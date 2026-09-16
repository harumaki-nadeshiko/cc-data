import hashlib
from pathlib import Path
assert Path('/.dockerenv').exists()
root = Path('/e')
for name in ('EPBackend.cc', 'EPBackend.hh', 'UBAdapter.cc', 'EPRNFController.cc'):
    p = Path('/home-source/gem5/src/mem/ruby/protocol/chi/ep') / name
    if p.exists():
        print('HOME_SHA', name, hashlib.sha256(p.read_bytes()).hexdigest())
cases = [('hist3f273c0370dd', '0x10c01440'), ('hist079c009f9dde', '0x104c4700'),
         ('hist78fe7a475f5d', '0x10700000')]
for case, key in cases:
    print('CHAIN', case, key)
    logs = root / 'history-after-publication' / case / 'logs'
    for p in sorted(logs.glob('ubio*_n0_s0/stdout.log')):
        count = 0
        with p.open(errors='replace') as f:
            for n, line in enumerate(f, 1):
                if key in line:
                    print(n, line.rstrip())
                    count += 1
                    if count >= 100: break
    if '78fe' in case:
        for p in logs.glob('gem5*_node15/stderr.log'):
            print(p.read_text()[-14000:])
