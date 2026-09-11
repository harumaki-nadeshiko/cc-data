"""Structural guards supplement, never replace, real gem5 correctness runs."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class HANodeRuntimeContract(unittest.TestCase):
    def test_no_shadow_data_source(self):
        ep = ROOT / 'gem5/src/mem/ruby/protocol/chi/ep'
        for name in ('EPBackend.cc', 'EPBackend.hh'):
            self.assertNotIn('_haDirtyData', (ep / name).read_text())

    def test_node_bitmap_and_endpoint_response(self):
        text = (ROOT / 'modules/ubiomodule/ubio_main.cc').read_text()
        self.assertIn('static_cast<uint32_t>(g_numNodes)}', text)
        self.assertIn('? context.requesterSocket : participantSocket(recipient)', text)
        self.assertIn('permissionResponse ? context.wireReqId : action.requestId', text)

    def test_wire_internal_publication_sentinel(self):
        text = (ROOT / 'modules/ubiomodule/ubio_main.cc').read_text()
        self.assertIn('msg.h.requesterNode == static_cast<uint16_t>(-1)', text)
        self.assertIn('UBWritebackKind::InternalPublication\n            ? -1 : static_cast<int>(request.h.requesterNode)', text)

    def test_l1_eviction_is_not_node_release(self):
        text = (ROOT / 'gem5/src/mem/ruby/system/Sequencer.cc').read_text()
        callback = text.split('Sequencer::evictionCallback(Addr address)', 1)[1].split('\n}', 1)[0]
        self.assertNotIn('handleEvict', callback)
        self.assertIn('llscClearMonitor(address)', callback)
        self.assertIn('ruby_eviction_callback(address)', callback)


if __name__ == '__main__':
    unittest.main()
