"""Structural safety guards; integration gates establish actual behavior."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
CHI = ROOT / 'gem5/src/mem/ruby/protocol/chi'


class InvalidateOnlyContract(unittest.TestCase):
    def test_precise_port_guard_retains_cpu_stale(self):
        text = (CHI/'CHI-cache-ports.sm').read_text()
        guard = text.split('} else if (in_msg.type == CHIRequestType:CleanUnique) {')[1].split('// Normal request path')[0]
        for item in ['epRnfMachineVersion >= 0', 'in_msg.requestor == createMachineID',
                     'in_msg.ep_proxy_op == EpProxyOp:InvalidateOnly',
                     'Event:CleanUnique_Stale', 'dir_entry.sharers.isElement']:
            self.assertIn(item, guard)

    def test_snoops_and_persistence_before_comp(self):
        text = (CHI/'CHI-cache-actions.sm').read_text()
        finish = text.split('action(Finish_CleanUnique,')[1].split('action(Complete_InvalidateOnly,')[0]
        proxy = finish.split('// everyone may have been hit')[0]
        self.assertNotIn('dir_sharers.clear()', proxy)
        self.assertIn('assert(tbe.dir_sharers.isEmpty())', proxy)
        self.assertIn('assert(tbe.dataBlkValid.isFull())', proxy)
        self.assertLess(proxy.index('Event:SendWBData'), proxy.index('Event:CompleteInvalidateOnly'))
        complete = text.split('action(Complete_InvalidateOnly,')[1].split('action(Initiate_LoadHit,')[0]
        self.assertLess(complete.index('tbe.dataValid := false'), complete.index('Event:SendCompUCResp'))
        transitions = (CHI/'CHI-cache-transitions.sm').read_text()
        wb = transitions.split('transition(BUSY_BLKD, SendWBData)')[1].split('}')[0]
        self.assertIn('CheckWUComp;', wb)

    def test_only_configured_ep_bookkeeping_is_retired(self):
        text = (CHI/'CHI-cache-actions.sm').read_text()
        proxy = text.split('action(Finish_CleanUnique,')[1].split('// everyone may have been hit')[0]
        self.assertIn('real_sharers.remove(tbe.epRnfMachineID)', proxy)
        retire = proxy.index('tbe.dir_sharers.remove(tbe.epRnfMachineID)')
        for check in ('assert(!tbe.expected_snp_resp.hasExpected())',
                      'assert(real_sharers.isEmpty())',
                      'assert(!tbe.dir_ownerExists)',
                      'assert(!tbe.dataMaybeDirtyUpstream)'):
            self.assertLess(proxy.index(check), retire)

    def test_hn_forwarding_lifetime_is_profile_neutral(self):
        text = (CHI/'CHI-cache-actions.sm').read_text()
        branch = text.split('// just tag update since data any data would become stale')[1].split('action(Initiate_ReadUnique_Hit_InvUpstream,')[0]
        self.assertIn('if (is_HN && is_invalid(cache_entry))', branch)
        self.assertNotIn('ha_node_observer', branch)
        self.assertIn('tbe.dataToBeInvalid := true', branch)
        self.assertNotIn('tbe.dataValid := false', branch)
        self.assertNotIn('dataBlkValid.clear', branch)

    def test_ep_failure_handshake_and_callback_retention(self):
        text = (CHI/'ep/EPRNFController.cc').read_text()
        self.assertIn('!msg->m_stale && msg->m_type == CHIResponseType_Comp_UC', text)
        self.assertIn('finishChiTxn(linePa, completionOk)', text)
        self.assertIn('_deferredInvalidations[linePa].push_back(std::move(onComplete))', text)
        unique = text.split('EPRNFController::handleSnpUnique(')[1].split('EPRNFController::handleSnpOnce(')[0]
        self.assertIn('msg->m_ep_proxy_op == EpProxyOp_NoProxyOp', unique)
        self.assertIn('backend->isDsmAddrCrossNode(msg->m_addr)', unique)
        self.assertLess(unique.index('return handleSnpCleanInvalid(msg)'),
                        unique.index('sendResponseReliable(rsp)'))
        backend = (CHI/'ep/EPBackend.cc').read_text()
        self.assertIn('fatal_if(!ok,\n                         "InvalidateOnly failed; refusing outer ACK', backend)


if __name__ == '__main__':
    unittest.main()
