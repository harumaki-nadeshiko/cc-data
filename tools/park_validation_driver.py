#!/usr/bin/env python3
"""Explicit-launch Docker controller. Fail closed, no polling/relaunch daemon.

Docker socket is used only to create this candidate's new containers. No stop,
kill, prune or historical-campaign operation exists in this module.
"""
import argparse
import concurrent.futures
import datetime
import hashlib
import http.client
import json
import math
import os
import re
from pathlib import Path
import socket
import time

from park_validation_stage import stage
from park_validation_audit import audit

FEATURES = {'sameKTwoSockets', 'lateParkAfterNativeDone', 'partialReadShared',
            'storeMissHA1Mutation', 'fullQueues', 'boundedAuthority',
            'nativeAdmissionBeforeEP', 'lateBodyParentIdentity'}


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    temp = Path(str(path) + '.new')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.replace(path)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def candidate_gate(root):
    manifest_path = root / 'manifest.json'
    manifest = load(manifest_path)
    ready = load(root / 'FINALCANDIDATEREADY.json')
    assert ready['state'] == 'COMPLETE'
    assert FEATURES <= set(ready['features'])
    assert ready['manifest_sha256'] == sha(manifest_path)
    assert ready['source_version'] == manifest['source_version']
    assert ready['integrated_build_verified'] is True
    assert ready['real_microplans_verified'] is True
    assert ready['validated_source_commits'] == manifest['validated_source_commits']
    assert manifest['source_version'] == hashlib.sha256(json.dumps(manifest['files'], sort_keys=True).encode()).hexdigest()
    source = root / 'source'
    for name, expected in manifest['files'].items():
        path = (source / name).resolve()
        assert source.resolve() in path.parents and path.is_file()
        assert sha(path) == expected, name
    for required in ('gem5/build/ARM/gem5.opt', 'build/bin/ubio', 'build/bin/networksim',
                     'tests/e2e/verify.py', 'tests/e2e/test_e2e.py', 'tests/e2e/run_multi.sh'):
        assert required in manifest['files'], required
    # Manifest must cover the complete deployed source, not just selected hashes.
    actual = {str(p.relative_to(source)) for p in source.rglob('*') if p.is_file()}
    assert actual == set(manifest['files']), 'unmanifested candidate files'
    assert manifest['queue'] == stage(source), 'queue/registry drift'
    return manifest


def publication_gate(root, manifest, results):
    regression = manifest['queue']['jobs']
    assert len(regression) == 72
    for job in regression:
        result = results[job['key']]
        assert result['state'] == 'PASS' and result['completed_at']
        assert result['source_version'] == manifest['source_version']
    release = load(root / 'release.json')
    assert release['manifest_sha256'] == sha(root / 'manifest.json')
    assert release['source_version'] == manifest['source_version']
    assert release['verified_by_parent'] is True
    assert release['protocol'] == 'host-github-ls-remote-after-72-pass-v1'
    for repo in ('main', 'gem5'):
        record = release[repo]
        assert record['github_remote_commit'] == record['validated_commit']
        assert re.fullmatch('[0-9a-f]{40}', record['validated_commit'])
        assert record['validated_commit'] == manifest['validated_source_commits'][repo]
        assert record['branch'] == manifest['publication_branches'][repo]
    return release


class Docker(http.client.HTTPConnection):
    def __init__(self):
        super().__init__('localhost', timeout=7200)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect('/var/run/docker.sock')


def api(method, route, data=None, raw=False):
    connection = Docker()
    body = None if data is None else json.dumps(data).encode()
    connection.request(method, '/v1.40' + route, body,
                       {'Content-Type': 'application/json'})
    response = connection.getresponse()
    content = response.read()
    connection.close()
    if response.status >= 300:
        raise RuntimeError((response.status, content.decode(errors='replace')))
    return content if raw else (json.loads(content) if content else {})


def cpu_pools():
    # Explicit per-NUMA CPU pools from the actual host sysfs, not assumed ranges.
    pools = {}
    for node in sorted(Path('/sys/devices/system/node').glob('node[0-9]*')):
        cpus = []
        for part in (node / 'cpulist').read_text().strip().split(','):
            ends = list(map(int, part.split('-')))
            cpus.extend(range(ends[0], ends[-1] + 1))
        pools[node.name[4:]] = cpus
    assert pools
    return pools


def run_job(root, manifest, job, node, cpus, memory_gib):
    version = manifest['source_version']
    short = hashlib.sha256((version + job['key']).encode()).hexdigest()[:16]
    out = root / 'runs' / short
    out.mkdir(parents=True, exist_ok=False)  # no old-result reuse or duplicate job
    for name in ('run', 'logs', 'ipc'):
        (out / name).mkdir()
    (out / 'run' / 'tmp').mkdir()
    result = dict(key=job['key'], role=job['role'], source_version=version,
                  state='RUNNING', started_at=now(), completed_at=None,
                  elapsed_seconds=None, raw_stdout=str(out / 'stdout.raw'),
                  cancelLog=None, numa=node, cpus=cpus)
    save(out / 'result.json', result)
    start = time.monotonic()
    source = root / 'source'
    # Overlay the existing directory, not a missing child of the read-only
    # source bind (Docker cannot mkdir that child while mounting).
    binds = [str(source) + ':/candidate:ro', str(out / 'run') + ':/candidate/build/runs',
             str(out / 'logs') + ':/evidence', str(out / 'ipc') + ':/ipc']
    config = dict(Image='ubcc-dev:ubuntu20.04', WorkingDir='/candidate',
                  Cmd=['bash', 'tests/e2e/run_multi.sh', '--' + job['topology'], str(job['tc'])],
                  Env=['E2E_RUN_ID=' + short, 'E2E_IPC_DIR=/ipc', 'LOG_BASE=/evidence',
                        'TMPDIR=/candidate/build/runs/tmp',
                       'PYTHONDONTWRITEBYTECODE=1',
                       'TIMEOUT_SEC=' + str(job['budget']), 'EP_CPU_MODEL=timing',
                       'EP_PARK_MICROTEST=0', 'EP_PARK_MICROTEST_PARTIAL=0'],
                  HostConfig=dict(NetworkMode='none', Binds=binds,
                                  CpusetCpus=','.join(map(str, cpus)), CpusetMems=node,
                                  NanoCpus=len(cpus) * 1000000000,
                                  Memory=memory_gib * 1024**3, MemorySwap=memory_gib * 1024**3,
                                  LogConfig={'Type': 'json-file', 'Config': {}}))
    try:
        cid = api('POST', '/containers/create?name=pc' + short, config)['Id']
        result['container_id'] = cid
        save(out / 'result.json', result)
        api('POST', '/containers/' + cid + '/start')
        status = api('POST', '/containers/' + cid + '/wait?condition=not-running')
        raw = api('GET', '/containers/' + cid + '/logs?stdout=1&stderr=1', raw=True)
        # Docker multiplex framing: preserve payload bytes, no UTF-8 rewriting.
        with (out / 'stdout.raw').open('wb') as stream:
            offset = 0
            while offset < len(raw):
                size = int.from_bytes(raw[offset + 4:offset + 8], 'big')
                assert offset + 8 + size <= len(raw)
                stream.write(raw[offset + 8:offset + 8 + size])
                offset += 8 + size
        result['exit_code'] = status['StatusCode']
        result['state'] = 'WAIT_AUDIT'
        verified = audit(source, out, job, result, manifest)
        verified['completed_at'] = now()
        save(out / 'audit.json', verified)
        result['state'] = verified['state']
    except Exception as error:
        result['state'] = 'INFRA_ERROR'
        result['error'] = repr(error)
    result.update(completed_at=now(), elapsed_seconds=time.monotonic() - start)
    save(out / 'result.json', result)
    return result


def summary(root, manifest):
    results = {}
    for path in (root / 'runs').glob('*/result.json'):
        result = load(path)
        assert result['source_version'] == manifest['source_version']
        assert result['key'] not in results
        # Audit is derived from raw child statuses and re-executed verifiers.
        audit_path = path.parent / 'audit.json'
        if audit_path.exists():
            audit = load(audit_path)
            assert audit['source_version'] == result['source_version']
            assert audit['key'] == result['key'] and audit['completed_at']
            assert audit['raw_stdout_sha256'] == sha(path.parent / 'stdout.raw')
            assert audit['verifier_sha256'] == manifest['queue']['verifier_sha256']
            assert audit['state'] in ('PASS', 'FAIL')
            result['state'] = audit['state']
            result['audit_completed_at'] = audit['completed_at']
        results[result['key']] = result
    return results


def dashboard_view(manifest, results):
    """Read-only adapter: exact candidate/role counts; absent is pending, not PASS."""
    roles = {}
    rows = []
    for job in manifest['queue']['jobs']:
        result = results.get(job['key'])
        if result:
            assert result['source_version'] == manifest['source_version']
            assert result['role'] == job['role']
            if result['state'] in ('PASS', 'FAIL'):
                assert result['completed_at'] and result['elapsed_seconds'] is not None
        state = result['state'] if result else 'PENDING'
        counts = roles.setdefault(job['role'], {})
        counts[state] = counts.get(state, 0) + 1
        rows.append(dict(job=job, result=result, state=state))
    return dict(source_version=manifest['source_version'], roles=roles, rows=rows,
                publish_to_existing_dashboard=bool(results),
                history_chapter='WAIT_PUBLISH', fold_state='preserve_existing')


def main():
    assert Path('/.dockerenv').exists(), 'Docker only'
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--launch-final72', action='store_true')
    parser.add_argument('--check-publication', action='store_true')
    parser.add_argument('--workers', type=int, default=16)
    parser.add_argument('--memory-gib', type=int, default=12)
    parser.add_argument('--io-workers', type=int, default=16)
    args = parser.parse_args()
    root = args.root.resolve()
    assert str(root).startswith('/mnt/data-xfs/cgc/park-validation-')
    try:
        manifest = candidate_gate(root)
    except FileNotFoundError as error:
        print(json.dumps({'state': 'WAIT_FINALCANDIDATE', 'missing': str(error)}))
        return
    results = summary(root, manifest)
    if args.check_publication:
        try:
            publication_gate(root, manifest, results)
        except (FileNotFoundError, KeyError, AssertionError) as error:
            print(json.dumps({'state': 'WAIT_PUBLISH', 'create': str(root / 'release.json'), 'reason': str(error)}))
            return
        print(json.dumps({'state': 'PUBLISHED_HISTORY_ELIGIBLE', 'source_version': manifest['source_version']}))
        return
    if not args.launch_final72:
        print(json.dumps({'state': 'STAGED', 'view': dashboard_view(manifest, results)}, indent=2))
        return
    assert not results, 'one-shot launch only: inspect existing runs, do not duplicate'
    assert 1 <= args.workers <= 64 and args.memory_gib > 0 and args.io_workers > 0
    mem_kib = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))
    workers = min(args.workers, args.io_workers, int(mem_kib * 0.75 / (args.memory_gib * 1024**2)))
    assert workers > 0
    assert os.statvfs(root).f_bavail * os.statvfs(root).f_frsize > 100 * 1024**3
    pools = cpu_pools()
    pending = list(manifest['queue']['jobs'])
    running = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        while pending or running:
            for job in list(pending):
                if len(running) >= workers:
                    break
                need = math.floor(1.5 * (1 + job['n'] + job['n'] * job['s']))
                node = next((n for n, cpus in pools.items() if len(cpus) >= need), None)
                if node is None:
                    continue
                cpus, pools[node] = pools[node][:need], pools[node][need:]
                future = executor.submit(run_job, root, manifest, job, node, cpus, args.memory_gib)
                running[future] = (node, cpus)
                pending.remove(job)
            assert running, 'no NUMA-local capacity for next job'
            done, _ = concurrent.futures.wait(running, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                node, cpus = running.pop(future)
                pools[node].extend(cpus)
                print(json.dumps(future.result()), flush=True)
    print(json.dumps({'state': 'WAIT_AUDIT', 'next': 'parent frozen verifier audits, then --check-publication'}))


if __name__ == '__main__':
    main()
