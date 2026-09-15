#!/usr/bin/env python3
"""Deterministic legal-stage API plans for parent production adapter.

Adapter owns actual topology/config copy and real scheduling. No runtime hooks
are invented here: absent adapter/features is DEFERRED, never model-backed PASS.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

PLANS = [
    dict(id='C306', stage='nativeAdmissionBeforeEP', sockets=[0, 1],
         operation='ReadShared', old=0x30600001, new=0x30600002,
         schedule=['seedOld', 'admitHNBothSocketsSameK', 'remoteInvalidate',
                   'attachEP', 'drainOld', 'localCompleteBoth', 'publishNew', 'resume', 'readNew']),
    dict(id='C307', stage='nativeDoneBeforePark', sockets=[0, 1],
         operation='ReadShared', old=0x30700001, new=0x30700002,
         schedule=['seedOld', 'readOldBoth', 'nativeDoneBoth', 'remoteInvalidate',
                   'lateParkBoth', 'localCompleteBoth', 'publishNew', 'resume', 'readNew']),
    dict(id='C308', stage='partialReadShared', sockets=[0, 1],
         operation='ReadShared', old=0x30800001, new=0x30800002,
         schedule=['seedOld', 'admitHNBothSocketsSameK', 'oldBeat0', 'remoteInvalidate',
                   'parkBoth', 'oldBeat1', 'drainOld', 'localCompleteBoth',
                   'publishNew', 'resume', 'newIncarnationReadNew']),
    dict(id='C309', stage='storeMissHA1Mutation', sockets=[0], profiles=['UBCC', 'HA1'],
         operation='StoreMiss', old=0x30900001, new=0x30900002,
         schedule=['seedOld', 'storeMissAcquire', 'oldBeat0', 'remoteInvalidate',
                   'park', 'oldBeat1', 'drainOld', 'localComplete', 'resume',
                   'newIncarnationAcquire', 'authorizeStore', 'mutateOnce', 'readNew']),
    dict(id='C310', stage='fullQueues', sockets=[0, 1],
         operation='ReadShared', old=0x31000001, new=0x31000002,
         schedule=['seedOld', 'fillOrdinaryQueue', 'fillNative32', 'fillDataOutput',
                   'admitHNBothSocketsSameK', 'remoteInvalidateTwoLinesTwoNodes',
                   'parkBoth', 'drainOld', 'localReleaseBeforeHomeBarrier',
                   'globalBarrier', 'publishNew', 'resume', 'readNew'])
]


def validate(plan, evidence):
    assert evidence['execution_kind'] == 'REAL_PRODUCTION_PARK'
    assert evidence['test_shortcuts'] == []
    assert evidence['plan_id'] == plan['id'] and evidence['completed_at']
    assert evidence['old_value'] == plan['old'] and evidence['new_value'] == plan['new']
    assert plan['old'] != plan['new']
    assert evidence['all_reads_match'] is True
    assert evidence['exit_ack_count'] == evidence['nodes'] * evidence['sockets']
    assert evidence['barrier_mask'] == (1 << (evidence['nodes'] * evidence['sockets'])) - 1
    assert evidence['reached_stages'] == plan['schedule']
    assert set(evidence['park_sockets']) == set(plan['sockets'])
    assert evidence['canonical_K_count'] == 1
    assert evidence['publication_before_global_barrier'] == 0
    assert evidence['parent_mismatch'] == 0 and evidence['stale_data_publications'] == 0
    if plan['operation'] == 'StoreMiss':
        assert evidence['store_mutations'] == 1
        assert evidence['store_authorization_before_mutation'] is True
    if plan['stage'] == 'partialReadShared':
        assert evidence['old_incarnation'] != evidence['new_incarnation']
        assert evidence['old_outer_request'] != evidence['new_outer_request']
    if plan['stage'] == 'fullQueues':
        assert evidence['native_high_water'] == 32
        assert evidence['ordinary_full_observed'] and evidence['data_output_full_observed']
    traces = evidence['raw_traces']
    assert traces
    for trace in traces:
        assert hashlib.sha256(Path(trace['path']).read_bytes()).hexdigest() == trace['sha256']


def main():
    assert Path('/.dockerenv').exists(), 'Docker only'
    parser = argparse.ArgumentParser()
    parser.add_argument('--adapter', type=Path)
    parser.add_argument('--home', type=Path, required=True)
    args = parser.parse_args()
    if not args.adapter:
        print(json.dumps({'state': 'DEFERRED_REAL_RUNTIME_ADAPTER', 'plans': PLANS}, indent=2))
        return
    home = args.home.resolve()
    assert str(home).startswith(('/mnt/data2/cgc/cc-ep-boundary-serial-20260913/', '/mnt/data-xfs/cgc/park-validation-'))
    home.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location('parent_production_adapter', args.adapter)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    for plan in PLANS:
        for profile in plan.get('profiles', ['UBCC']):
            evidence = adapter.run_real_park_plan(plan=plan, profile=profile, output=home)
            validate(plan, evidence)
            (home / (plan['id'] + '-' + profile + '.json')).write_text(json.dumps(evidence, indent=2))
    print('REAL_ADAPTER_EVIDENCE_VALIDATED; parent must independently audit raw traces')


if __name__ == '__main__':
    main()
