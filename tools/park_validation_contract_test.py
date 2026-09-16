#!/usr/bin/env python3
"""C-only model/orchestration tests; NOT production runtime tests."""
import ast
import copy
import pathlib
import unittest

from park_validation_models import native_callbacks, resources
from park_validation_stage import stage
from park_validation_microplans import PLANS, validate
from park_validation_driver import dashboard_view, candidate_gate


class Contracts(unittest.TestCase):
    def test_native_admission_and_callbacks(self):
        self.assertEqual(native_callbacks()['deadlocks'], 0)

    def test_resource_policy_counterexample_and_barrier(self):
        self.assertEqual(resources(False)['deadlocks'], 2)
        self.assertEqual(resources(True)['deadlocks'], 0)

    def test_actual_registry(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        queue = stage(root)
        self.assertEqual(len(queue['jobs']), 72)
        self.assertEqual(len(queue['absent_ids']), 28)
        jobs = {j['tc']: j for j in queue['jobs']}
        self.assertEqual(jobs[27]['budget'], 3000)
        self.assertEqual(jobs[43]['budget'], 2400)
        self.assertEqual((jobs[35]['n'], jobs[35]['s']), (3, 2))
        self.assertEqual(jobs[36]['topology'], '1s')

    def test_frozen_tc9_exact_contract(self):
        # Execute ONLY the AST-extracted verifier function, no config imports.
        root = pathlib.Path(__file__).resolve().parents[1]
        tree = ast.parse((root / 'tests/e2e/test_e2e.py').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'verify_tc9')
        module = ast.Module(body=[function], type_ignores=[])
        namespace = {}
        exec(compile(module, 'frozen_verify_tc9_AST', 'exec'), namespace)
        verify = namespace['verify_tc9']
        exact = 'Page table fault when accessing virtual address 0xfffff8000000'
        self.assertTrue(verify([], [exact])[0])
        self.assertFalse(verify([], ['panic: arbitrary crash'])[0])
        self.assertFalse(verify([], ['[FATAL]'])[0])
        self.assertFalse(verify([{'actual': '0'}], [exact])[0])

    def test_no_model_can_claim_runtime_pass(self):
        for plan in PLANS:
            self.assertNotEqual(plan['old'], plan['new'])
            with self.assertRaises(AssertionError):
                validate(plan, {'execution_kind': 'OFFLINE_MODEL'})

    def test_docker_only_sources_parse(self):
        for path in pathlib.Path(__file__).parent.glob('park_validation_*.py'):
            ast.parse(path.read_text())

    def test_dashboard_pending_is_not_evidence(self):
        manifest = {'source_version': 'new', 'queue': {'jobs': [
            {'key': 'final72/tc1', 'role': 'regression'}]}}
        view = dashboard_view(manifest, {})
        self.assertEqual(view['roles'], {'regression': {'PENDING': 1}})
        self.assertFalse(view['publish_to_existing_dashboard'])
        result = dict(source_version='old', role='regression', state='PASS')
        with self.assertRaises(AssertionError):
            dashboard_view(manifest, {'final72/tc1': result})
        result.update(source_version='new', completed_at=None, elapsed_seconds=None)
        with self.assertRaises(AssertionError):
            dashboard_view(manifest, {'final72/tc1': result})

    def test_missing_candidate_cannot_launch(self):
        with self.assertRaises(FileNotFoundError):
            candidate_gate(pathlib.Path('/nonexistent-park-C-contract'))


if __name__ == '__main__':
    assert pathlib.Path('/.dockerenv').exists(), 'Docker only'
    unittest.main(verbosity=2)
