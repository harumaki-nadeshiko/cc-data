"""Source guards supplement (not replace) TC228 real CHI/data validation."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHI = ROOT / 'gem5/src/mem/ruby/protocol/chi'


class RecallSharedContract(unittest.TestCase):
    def test_shared_recall_compack_registers_external_sharer(self):
        text = (CHI / 'CHI-cache-actions.sm').read_text()
        block = text.split('action(UpdateDirState_FromReqResp,')[1].split('\naction(')[0]
        guard = block.split('bool is_ep_proxy_special :=')[1].split(';')[0]
        self.assertIn('tbe.epProxyOp != EpProxyOp:RecallShared', guard)
        self.assertIn('tbe.dir_sharers.add(in_msg.responder)', block)
        self.assertIn('assert(!tbe.requestorToBeExclusiveOwner)', block)
        self.assertIn('[RECALL-SHARED-REGISTER]', block)

    def test_miss_and_proxy_only_have_no_fetch_branch(self):
        text = (CHI / 'CHI-cache-actions.sm').read_text()
        for name in ('Initiate_ReadShared_Miss',
                     'Initiate_ReadShared_HitUpstream_NoOwner'):
            block = text.split('action(' + name + ',')[1].split('\naction(')[0]
            branch = block.split('tbe.epProxyOp == EpProxyOp:RecallShared')[1]
            branch = branch.split('} else')[0]
            self.assertIn('SendCompUCResp', branch)
            self.assertNotIn('SendRead', branch)

    def test_read_recall_keeps_local_shared_custody(self):
        for path, marker in (('CHI-cache-actions.sm', 'action(ScrubEPRNF_ToI'),
                             ('CHI-cache-funcs.sm', 'State makeFinalState(TBE')):
            block = (CHI / path).read_text().split(marker)[1].split('}', 1)[0]
            self.assertIn('tbe.epProxyOp != EpProxyOp:RecallShared', block)

    def test_failed_or_malformed_wire_cannot_enter_controller(self):
        text = (ROOT / 'modules/ubiomodule/ubio_main.cc').read_text()
        block = text.split('case CoherenceMessageType::RecallResp: {')[-1]
        block = block.split('case CoherenceMessageType::InvalidateAck')[0]
        guard = block.index('if (!ackReceived || dataReturned != hasData)')
        self.assertLess(guard, block.index('ubcc.processRecallResponse'))
        self.assertIn('return true;', block[guard:block.index('ubcc.processRecallResponse')])
        self.assertIn('CFLAG_ACCEPTED', block)
        self.assertIn('recall-failed', block)
        ha = text.split('case CoherenceMessageType::RecallResp: {')[1]
        self.assertLess(ha.index('if (!ackReceived || dataReturned != hasRecallData)'),
                        ha.index('payload.valid = true'))

    def test_duplicate_identity_and_full_data(self):
        backend = (CHI / 'ep/EPBackend.cc').read_text()
        self.assertIn('_pendingRecalls.emplace(recallMsg.linePa, recallMsg.homeNode,', backend)
        self.assertIn('recallMsg.epoch, recallMsg.reqId,', backend)
        self.assertIn('_pendingRecalls.erase', backend)
        rnf = (CHI / 'ep/EPRNFController.cc').read_text()
        self.assertIn('txn.proxyOp = EpProxyOp_RecallShared', rnf)
        self.assertIn('ReadShared final beat did not', rnf)
        self.assertIn('duplicate ReadShared data bytes', rnf)
        self.assertNotIn('_haDirtyData', backend)

    def test_publication_and_timeout_unchanged(self):
        ubcc = (ROOT / 'modules/ubiomodule/UBCCController.cc').read_text()
        self.assertIn('active->second.opType == OpType::RECALL', ubcc)
        self.assertIn('if (dataReceived && !dataBlk)', ubcc)
        host = (ROOT / 'modules/ubiomodule/ubio_main.cc').read_text()
        self.assertIn('dirty recall absence without persisted backing', host)
        self.assertIn('Tick _recallTimeout = 100000000;',
                      (ROOT / 'modules/ubiomodule/UBCCController.hh').read_text())


if __name__ == '__main__':
    unittest.main()
