import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import o_after_n as c

class ChainContract(unittest.TestCase):
    def test_active_failed_group_cannot_launch(self):
        self.assertFalse(c.group_terminal(72,[{'state':'FAIL'}],['child'],False,'BLOCKED'))
    def test_failed_stopped_pending_can_launch(self):
        self.assertTrue(c.group_terminal(72,[{'state':'FAIL'}],[],False))
    def test_idle_complete_controller_is_terminal(self):
        self.assertTrue(c.group_terminal(8,[{'state':'PASS'}]*8,[],True))
    def test_live_controller_pending_is_not_terminal(self):
        self.assertFalse(c.group_terminal(72,[{'state':'PASS'}]*55,[],True))
    def test_both_groups_required(self):
        self.assertFalse(all([c.group_terminal(8,[{'state':'FAIL'}]*8,[],True),
                             c.group_terminal(72,[],['child'],True)]))
    def test_source_mismatch_blocks(self):
        import json
        with tempfile.TemporaryDirectory() as d, patch.object(c,'O',Path(d)):
            (Path(d)/'candidate-manifest.json').write_text(json.dumps(dict(build_verified=True,
                state='O_BUILD_PASS_NOT_RELEASE',files={},source_version='incorrect')))
            with self.assertRaises(AssertionError):c.verify_o()
    def test_duplicate_claim_blocks(self):
        with tempfile.TemporaryDirectory() as d, patch.object(c,'O',Path(d)):
            (Path(d)/'launch-claim.json').write_text('{}')
            with self.assertRaises(FileExistsError):c.run_all({'source_version':'test'}, {})

if __name__=='__main__':unittest.main()
