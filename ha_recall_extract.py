"""Docker-only final TC228 evidence extraction; no baseline artifact mutation."""
import hashlib
import json
from pathlib import Path
import re
import statistics
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'scripts'))
import extract_metric123_from_logs as extract

output = ROOT / 'ha-evidence/recall-fix-results.json'
results = {'runs': [], 'source_sha256': {}, 'old': json.loads(
    (ROOT / 'ha-evidence/paired8-final-2n1s-p100/results.json').read_text())['results']['228']}
for tag in ('recall-fix-final-p100', 'recall-fix-final-p0', 'recall-fix-final-2n2s'):
    manifest = json.loads((ROOT / 'ha-evidence' / tag / 'manifest.json').read_text())
    assert len(manifest['jobs']) == 2
    for job in manifest['jobs']:
        assert job['passed'] and job['returncode'] == 0
        folder = Path(job['log_dir'])
        parsed = extract.extract_run(dict(id=tag + job['arm'], metric=3, tc=228,
            topology=manifest['topology'], simulator_log_dir=str(folder),
            simout_dir=str(folder), arm=job['arm'], repetition='1',
            pair=tag, order=job['order']), ROOT, 'strict')
        nodes = {}
        for simout in sorted(folder.glob('simout_tc228_node*.log')):
            node = int(re.search(r'node(\d+)', simout.name)[1])
            text = simout.read_text()
            assert 'MISMATCH' not in text
            latencies = []
            for line in text.splitlines():
                if '[PERF-LATENCY]' in line and 'phase=topology_remote_read' in line:
                    fields = dict(re.findall(r'(\w+)=(\d+)', line))
                    assert int(fields['counter_frequency_hz']) == 25165824
                    latencies.append({key + '_ns': int(fields[key]) * 1e9 / 25165824
                                      for key in ('min', 'p50', 'max')})
            stderr = (folder / f'gem5_tc228_node{node}/stderr.log').read_text()
            outer = [int(v) / 1000 for v in re.findall(
                r'\[EP-PERF\].*op=read_shared .*latency_ps=(\d+)', stderr)]
            nodes[node] = dict(matches=text.count(' MATCH'), guest_latency=latencies,
                outer_read_count=len(outer), outer_read_ns=outer,
                outer_mean_ns=statistics.mean(outer) if outer else None)
        ubio = '\n'.join(p.read_text(errors='replace') for p in folder.glob('ubio*/**/*.log'))
        assert ubio
        retries = len(re.findall(r'retrying timed-out recall', ubio))
        assert retries == 0, (tag, job['arm'], retries)
        results['runs'].append(dict(tag=tag, arm=job['arm'], parsed=parsed,
            nodes=nodes, recall_timeout_log_matches=retries, binaries=manifest['binaries']))
for path in ('modules/ubiomodule/UBCCController.cc', 'modules/ubiomodule/UBCCController.hh',
             'modules/ubiomodule/ubio_main.cc', 'tools/capacity_waiter_liveness_test.cc',
             'tests/scripts/test_recall_shared_contract.py',
             'gem5/src/mem/ruby/protocol/chi/CHI-msg.sm',
             'gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm',
             'gem5/src/mem/ruby/protocol/chi/CHI-cache-funcs.sm',
             'gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc',
             'gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc',
             'gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh'):
    results['source_sha256'][path] = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
results['traces'] = []
for pressure, valid in ((100, 0), (0, 1)):
    folder = ROOT / f'ha-evidence/recall-fix-trace-p{pressure}/tc228-ubcc'
    for node in range(2):
        text = (folder / f'gem5_tc228_node{node}/gem5_debug.log').read_text()
        starts = {int(req): int(tick) for req, tick in re.findall(
            r'\[RECALL-START-ERR\].*reqId=(\d+) curT=(\d+)', text)}
        callbacks = re.findall(r'\[RECALL-CB-ERR\].*reqId=(\d+) success=(\d+) valid=(\d+) curT=(\d+)', text)
        assert len(starts) == len(callbacks) == 16
        assert all(int(ok) == 1 and int(data) == valid for _, ok, data, _ in callbacks)
        elapsed = [(int(tick) - starts[int(req)]) / 1000 for req, _, _, tick in callbacks]
        outer = re.findall(r'\[EP-PERF\].*op=read_shared .*latency_ps=(\d+)', text)
        # EP-PERF inform output is in stderr, not necessarily debug-file.
        stderr = (folder / f'gem5_tc228_node{node}/stderr.log').read_text()
        outer = re.findall(r'\[EP-PERF\].*op=read_shared .*latency_ps=(\d+)', stderr)
        assert len(outer) == 16, 'nested outer demand read re-entry'
        publications = text.count('[EPSNF-WRITE-DBID]')
        if pressure:
            assert publications == 16
            assert text.count('coh_type=7 reqId=') == 16
        results['traces'].append(dict(pressure=pressure, node=node,
            callbacks=16, valid=valid, local_recall_ns=elapsed,
            outer_reads=len(outer), publication_requests=publications))
output.write_text(json.dumps(results, indent=2) + '\n')
for run in results['runs']:
    metric = run['parsed']['metrics']['remote_read']['ns_per_operation']
    print(run['tag'], run['arm'], f'{metric:.6f} ns/op', run['nodes'])
