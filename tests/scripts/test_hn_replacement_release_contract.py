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
        source = (CHI / 'ep/EPBackend.cc').read_text()
        block = source.split('EPBackend::registerHnPersistence(', 1)[1].split('EPBackend::completeHnPersistence', 1)[0]
        for guard in ('replacement && fullLine', '_requesterLines.find(linePa)',
                      'RequesterLineState::R_M', 'HnPersistenceKind::OwnerDrop',
                      'context.epoch = requester->second.epoch'):
            self.assertIn(guard, block)
        self.assertNotIn('QueryLineMeta', block)
        resolve = source.split('EPBackend::resolveWritePersistence(', 1)[1].split('EPBackend::registerHnPersistence', 1)[0]
        self.assertIn('requesterNode = _nodeId', resolve)
        self.assertIn('permissionEpoch = registered.epoch', resolve)

    def test_complete_payload_and_success_before_comp(self):
        source = (CHI / 'ep/EPSNFController.cc').read_text()
        body = source.split('EPSNFController::processPendingHAWrites()', 1)[1].split('EPSNFController::publishHAWrite', 1)[0]
        self.assertLess(body.index('!pending.dataComplete'), body.index('handleWritebackWithMeta'))
        self.assertLess(body.index('fatal_if(result != 1'), body.index('publishHAWrite(transactionId'))
        source = (CHI / 'ep/EPSNFController.cc').read_text()
        self.assertIn('pending.internalPublication = !_backend->haEndpointEnabled() &&\n            !pending.ownerWriteback;', source)
        self.assertIn('result == 0 && pending.internalPublication', body)
        self.assertNotIn('notifyHomeWritebackComplete', body)


if __name__ == '__main__':
    unittest.main()
