import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc"
PORTS = ROOT / "gem5/src/mem/ruby/protocol/chi/CHI-cache-ports.sm"
ACTIONS = ROOT / "gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm"
TRANSITIONS = ROOT / "gem5/src/mem/ruby/protocol/chi/CHI-cache-transitions.sm"
UBCC = ROOT / "modules/ubiomodule/UBCCController.hh"
UBCC_CC = ROOT / "modules/ubiomodule/UBCCController.cc"


class EPRNFProxyRetryContractTest(unittest.TestCase):
    def test_proxy_requests_do_not_advertise_unimplemented_retry(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("req->m_allowRetry = proxyOp == EpProxyOp_NoProxyOp;", source)
        self.assertIn("req->m_allowRetry = d.proxyOp == EpProxyOp_NoProxyOp;", source)
        block = source[source.index("EPRNFController::sendChiRequest"):]
        self.assertNotIn("req->m_allowRetry = true;", block)

    def test_hnf_proxy_admission_stalls_instead_of_retrying(self):
        ports = PORTS.read_text(encoding="utf-8")
        actions = ACTIONS.read_text(encoding="utf-8")
        self.assertIn("in_msg.ep_proxy_op != EpProxyOp:NoProxyOp", ports)
        self.assertIn("Event:AllocProxyRequest", ports)
        self.assertIn("action(AllocateTBE_ProxyRequest", actions)
        self.assertIn("check_allocate(storTBEs)", actions)
        block = actions[actions.index("action(AllocateTBE_ProxyRequest"):]
        block = block[:block.index("action(", 10)]
        self.assertNotIn("SendRetryAck", block)

    def test_dirty_timeout_proxy_cancels_timeout_before_admission(self):
        source = TRANSITIONS.read_text(encoding="utf-8")
        marker = "transition(UD_T, AllocProxyRequest, UD)"
        block = source[source.index(marker):]
        block = block[:block.index("}")]
        self.assertLess(block.index("Unset_Timeout_Cache"),
                        block.index("AllocateTBE_ProxyRequest"))

    def test_recall_timeout_covers_loaded_dual_socket_round_trip(self):
        source = UBCC.read_text(encoding="utf-8")
        self.assertIn("Tick _recallTimeout = 100000000;", source)

    def test_internal_publication_can_break_recall_replacement_cycle(self):
        source = UBCC_CC.read_text(encoding="utf-8")
        self.assertIn("active->second.opType == OpType::RECALL", source)
        self.assertIn(
            "active->second.opType == OpType::NAIVE_EVICT_INVALIDATE", source
        )

    def test_exact_reservation_remains_authoritative_until_release(self):
        source = UBCC_CC.read_text(encoding="utf-8")
        start = source.index("UBCCController::validateWritebackPersistence")
        branch = source.index("if (reservation != _writeReservations.end())", start)
        end = source.index("    // An HN-internal publication", branch)
        block = source[branch:end]
        self.assertIn("return r.requesterNode == requesterNode", block)
        self.assertIn("r.reqId == reqId", block)
        self.assertNotIn("_outstandingReqs", block)


if __name__ == "__main__":
    unittest.main()
