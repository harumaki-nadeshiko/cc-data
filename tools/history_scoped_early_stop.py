"""Preserve evidence before stopping one explicitly named historical container.

Run in a network-disabled Docker container with the evidence root, docker.sock,
and host /proc (read-only) mounted. No discovery-based or process-group killing.
"""
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import sys
import time

assert Path('/.dockerenv').exists()
ROOT = Path('/mnt/data-xfs/cgc/park-validation-resume-20260915')
JOB = ROOT / 'history-after-publication/hist3f273c0370dd'
NAME = 'hist3f273c0370dd-published'
OUT = JOB / ('early-stop-evidence-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))


class Connection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect('/var/run/docker.sock')


def api(method, path):
    conn = Connection('localhost', timeout=120)
    conn.request(method, path)
    resp = conn.getresponse()
    body = resp.read()
    if resp.status >= 300:
        raise RuntimeError((resp.status, body[:1024]))
    return json.loads(body) if body else None


def save(name, data):
    (OUT / name).write_text(json.dumps(data, indent=2) + '\n')


def sample(index, cid):
    inspect = api('GET', '/containers/' + cid + '/json')
    top = (api('GET', '/containers/' + cid + '/top?ps_args=aux')
           if inspect['State']['Running'] else dict(Titles=['PID'], Processes=[]))
    save('inspect-%d.json' % index, inspect)
    save('processes-%d.json' % index, top)
    proc = {}
    pid_column = top['Titles'].index('PID')
    for row in top['Processes']:
        pid = row[pid_column]
        proc[pid] = {}
        for field in ('stat', 'status', 'wchan', 'io', 'cmdline'):
            try:
                proc[pid][field] = (Path('/hostproc') / pid / field).read_text().replace('\0', ' ')
            except OSError as exc:
                proc[pid][field] = str(exc)
    save('proc-%d.json' % index, proc)
    files = []
    for base in (JOB / 'logs', JOB / 'run'):
        for path in sorted(base.rglob('*')):
            if not path.is_file():
                continue
            st = path.stat()
            record = dict(path=str(path), size=st.st_size, mtime_ns=st.st_mtime_ns,
                          inode=st.st_ino, mode=st.st_mode)
            if path.suffix in ('.log', '.txt') or 'stdout' in path.name:
                with path.open('rb') as stream:
                    offset = max(0, st.st_size - 1024 * 1024)
                    stream.seek(offset)
                    raw = stream.read(1024 * 1024)
                name = '%d-%s.raw' % (index, hashlib.sha256(str(path).encode()).hexdigest()[:20])
                (OUT / name).write_bytes(raw)
                record.update(snapshot=name, byte_offset=offset,
                              sha256=hashlib.sha256(raw).hexdigest())
            files.append(record)
    save('files-%d.json' % index, dict(wall_time=time.time(), files=files))


OUT.mkdir()
info = api('GET', '/containers/' + NAME + '/json')
assert info['Name'] == '/' + NAME
assert any(m['Source'] == str(JOB / 'logs') for m in info['Mounts'])
record = json.loads((JOB / 'result.json').read_text())
assert '147' in record['key'] and record['state'] == 'RUNNING', record
cid = info['Id']
save('original-result.json', record)
for name in ('manifest.json', 'release.json'):
    (OUT / name).write_bytes((ROOT / name).read_bytes())
sample(0, cid)
if '--stop' not in sys.argv:
    print(str(OUT), flush=True)
    sys.exit(0)
time.sleep(60)
sample(1, cid)
# Explicit operator-authorized stop of this historical job, not an automatic
# inference from log growth. Raw logs stay at their original paths, unmodified.
save('decision.json', dict(state='EARLY_STOP_NO_PROGRESS',
     reason='Operator-authorized historical TC147 stop: prolonged no successful workload progress and repeated rejected eviction; not PASS or generic TIMEOUT.',
     sampling_horizon_seconds=60, container_id=cid, raw_logs_preserved_in_place=True,
     last_success='Not inferred from log byte growth; inspect raw snapshots and preserved logs.'))
api('POST', '/containers/' + cid + '/stop?t=30')
sample(2, cid)
save('stopped.json', api('GET', '/containers/' + cid + '/json')['State'])
print(str(OUT), flush=True)
