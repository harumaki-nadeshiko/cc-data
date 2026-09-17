"""Structural guards, not a substitute for executable protocol tests."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
CHI = ROOT / 'gem5/src/mem/ruby/protocol/chi'


class ReplacementReleaseContract(unittest.TestCase):
    def test_only_full_replacement_without_live_holders(self):
        source = (CHI / 'CHI-cache-actions.sm').read_text()
        full = source.split('action(Send_WriteNoSnp,', 1)[1].split('action(Send_WriteNoSnp_Partial,', 1)[0]
        for guard in ('tbe.is_repl_tbe', '!tbe.dir_ownerExists',
                      '!tbe.expected_snp_resp.hasExpected()',
                      '!tbe.dataMaybeDirtyUpstream', 'holders.isEmpty()'):
            self.assertIn(guard, full)
        partial = source.split('action(Send_WriteNoSnp_Partial,', 1)[1].split('action(Send_WriteUnique,', 1)[0]
        self.assertNotIn('ubccWriteDisposition := 1', partial)

    def test_snapshot_is_local_permission_not_qlm(self):
        source = (CHI / 'ep/EPSNFController.cc').read_text()
        block = source.split('// The replacement TBE proves', 1)[1].split('if (_backend->haEndpointEnabled()', 1)[0]
        for guard in ('inspectRequesterState', 'permission.valid',
                      'RequesterLineState::R_M',
                      'pending.releaseRequester = stable ? stable->owner : _nodeId',
                      'pending.releaseEpoch = stable ? stable->epoch : permission.epoch'):
            self.assertIn(guard, block)
        self.assertNotIn('QueryLineMeta', block)

    def test_release_consumes_only_persisted_exact_writeback(self):
        source = (CHI / 'ep/EPBackend.cc').read_text()
        callback = source.split('auto launch =', 1)[1].split('unsigned branches', 1)[0]
        for guard in ('joined->writeId', 'joined->epoch == r.identity',
                      'joined->owner == _nodeId', '!writebackOwnsCustody'):
            self.assertIn(guard, callback)
        progress = source.split('void EPBackend::progressAuthorityRelease()', 1)[1].split(
            'bool EPBackend::holdAuthority', 1)[0]
        self.assertIn('if (joinedWrite && !joined->persisted) return;', progress)
        self.assertIn('const bool joinedRecall = joinedWrite && joined->mergedRecallId;', progress)
        self.assertIn('r.access == RequesterLineState::R_M && !joinedRecall', progress)
        self.assertLess(progress.index('if (joinedWrite && !joined->persisted) return;'),
                        progress.index('homeDone = true;'))

    def test_complete_payload_and_success_before_comp(self):
        source = (CHI / 'ep/EPSNFController.cc').read_text()
        body = source.split('EPSNFController::processPendingHAWrites()', 1)[1].split('EPSNFController::publishHAWrite', 1)[0]
        self.assertLess(body.index('!pending.dataComplete'), body.index('handleWritebackWithMeta'))
        self.assertLess(body.index('fatal_if(result != 1'), body.index('publishHAWrite(transactionId'))
        self.assertIn('!pending.replacementOwnerRelease', body)
        self.assertNotIn('notifyHomeWritebackComplete', body)


if __name__ == '__main__':
    unittest.main()
