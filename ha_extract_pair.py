"""Use canonical Metric3 parser/reductions; execute only in Docker."""
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'scripts'))
import extract_metric123_from_logs as extract

folder = ROOT / 'ha-evidence/paired8-final-2n1s-p100'
manifest = json.loads((folder / 'manifest.json').read_text())
assert len(manifest['jobs']) == 16, 'Pair incomplete; no performance qualification'
results = {}
raw = []
for job in manifest['jobs']:
    assert job['passed'] and job['returncode'] == 0
    tc, arm = job['tc'], job['arm']
    path = Path(job['log_dir'])
    run = dict(id=f'tc{tc}-{arm}', metric=3, tc=tc, topology='2n1s',
               simulator_log_dir=str(path), simout_dir=str(path),
               arm=arm,
               repetition='1', pair=f'tc{tc}', order=job['order'])
    parsed = extract.extract_run(run, ROOT, 'strict')
    for metric in parsed['metrics'].values():
        assert metric['counter_frequency_hz'] == 25165824
    value = sum(parsed['metrics'][name]['ns_per_operation'] * weight
                for name, weight in extract.metric3_primary_weights(tc).items())
    results.setdefault(tc, {})[arm] = value
    raw.append(parsed)
rows = ['| TC | HA-VI ns/op | UBCC-lossless ns/op | HA/UBCC |',
        '|---|---:|---:|---:|']
for tc, values in sorted(results.items()):
    ha, ubcc = values['ha-vi'], values['ubcc']
    rows.append(f'| {tc} | {ha:.3f} | {ubcc:.3f} | {ha/ubcc:.4f} |')
groups = {}
for name, tcs in [('core3', range(228, 231)), ('representative5', range(231, 236))]:
    ha = statistics.mean(results[tc]['ha-vi'] for tc in tcs)
    ubcc = statistics.mean(results[tc]['ubcc'] for tc in tcs)
    groups[name] = dict(ha_ns=ha, ubcc_ns=ubcc, ratio=ha/ubcc,
                        ha_strictly_lower=ha < ubcc)
    rows.append(f'| {name} 等权均值 | {ha:.3f} | {ubcc:.3f} | {ha/ubcc:.4f} |')
(folder / 'results.json').write_text(json.dumps(dict(results=results, groups=groups,
                                                    raw=raw), indent=2) + '\n')
(folder / 'results.md').write_text('\n'.join(rows) + '\n')
print('\n'.join(rows))
