#!/usr/bin/env python3
"""AST-only registry freezer. No import/execution of changing production code."""
import ast
import hashlib
import json
import math
import pathlib
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def registry(root):
    tree = ast.parse((root / 'tests/e2e/test_e2e.py').read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'TESTCASES' for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('TESTCASES literal absent')


def stage(root):
    registered = registry(root)
    ids = sorted(tc for tc in registered if 0 < tc <= 100)
    absent = sorted(set(range(1, 101)) - set(ids))
    expected = list(range(55, 63)) + list(range(65, 80)) + [83] + list(range(86, 90))
    assert len(ids) == 72 and absent == expected, (len(ids), absent)
    old = json.loads((root / 'boundary-evidence/remote-launch/queue.json').read_text())['basic']
    frozen = {j['tc']: j for j in old}
    jobs = []
    for tc in ids:
        prior = frozen[tc]
        n, s, topology = prior['n'], prior['s'], prior['topology']
        if tc == 35:
            n, s, topology = 3, 2, '2s'
        # TC43 is a legitimate long-running ownership-wrap regression.  Prior
        # timing-CPU evidence completed in 1229s, so the generic 1200s budget
        # can kill an otherwise progressing run before verifier/exit closure.
        # Keep the workload and topology unchanged; only give this case a
        # case-specific controller wall-clock budget.
        budget = 3000 if tc == 27 else (2400 if tc == 43 else 1200)
        jobs.append(dict(key='final72/tc%d' % tc, tc=tc, role='regression', n=n, s=s,
                          topology=topology, workload=registered[tc],
                          budget=budget,
                           cpu_floor=math.floor(1.5 * (1 + n + n * s)),
                          verifier='frozen-exact-negative' if tc == 9 else 'frozen-positive'))
    return dict(schema=1, state='STAGED_NOT_STARTED', registry_sha256=digest(root / 'tests/e2e/test_e2e.py'),
                verifier_sha256=digest(root / 'tests/e2e/verify.py'),
                frozen_queue_sha256=digest(root / 'boundary-evidence/remote-launch/queue.json'),
                positive_id_count=72, absent_ids=absent, jobs=jobs,
                warning='Positive ID selection includes TC9 expected-crash contract; not 72 all-exit-zero tests.')


if __name__ == '__main__':
    assert pathlib.Path('/.dockerenv').exists(), 'Docker only'
    root = pathlib.Path(sys.argv[1]).resolve()
    print(json.dumps(stage(root), indent=2))
