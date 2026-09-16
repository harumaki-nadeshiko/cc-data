"""Preserve raw audits and publish a separate stop-aware selected history view."""
import json
from pathlib import Path

assert Path('/.dockerenv').exists()
root = Path('/mnt/data-xfs/cgc/park-validation-resume-20260915/history-after-publication')
job = root / 'hist3f273c0370dd'
evidence = sorted(p for p in job.glob('early-stop-evidence-*') if (p / 'decision.json').exists())[-1]
decision = json.loads((evidence / 'decision.json').read_text())
assert decision['state'] == 'EARLY_STOP_NO_PROGRESS'
original = json.loads((job / 'result.json').read_text())
assert original['exit_code'] == 143
results = []
for path in sorted(root.glob('hist*/profile-audit.json')):
    result = json.loads(path.read_text())
    if path.parent == job:
        result.update(state='EARLY_STOP_NO_PROGRESS', evidence=str(evidence),
                      original_audit_preserved=True, reason=decision['reason'])
    results.append(result)
assert len(results) == 66, len(results)
counts = {s: sum(r['state'] == s for r in results) for s in sorted({r['state'] for r in results})}
assert counts == {'PASS': 58, 'FAIL': 7, 'EARLY_STOP_NO_PROGRESS': 1}, counts
report = dict(total=66, counts=counts, results=results,
              raw_audits_preserved=True, classification='exact-profile plus explicit operator stop')
(root / 'selected-history-complete.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(counts))
for path in sorted((job / 'logs').glob('ubio*/stdout.log')):
    with path.open('rb') as stream:
        stream.seek(max(0, path.stat().st_size - 32768))
        lines = stream.read().decode(errors='replace').splitlines()
    selected = [line for line in lines if 'processEvict' in line or 'rejected' in line]
    if selected:
        print(str(path), '\n', '\n'.join(selected[-4:]))
for path in sorted((job / 'logs').glob('gem5*/stdout.log')):
    with path.open('rb') as stream:
        stream.seek(max(0, path.stat().st_size - 262144))
        lines = stream.read().decode(errors='replace').splitlines()
    selected = [line for line in lines if 'AUTHORITY-RELEASE' in line or 'RETIREMENT-PROOF' in line]
    if selected:
        print(str(path), '\n', '\n'.join(selected[-6:]))
