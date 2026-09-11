"""Structural guards, not a substitute for the real multi-process smoke."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class BloomReadyContract(unittest.TestCase):
    def test_all_six_call_before_seed(self):
        folder = ROOT / 'tests/e2e/workloads'
        for tc in range(142, 148):
            text = next(folder.glob(f'e2e_tc{tc}_*.c')).read_text()
            self.assertEqual(text.count('portable_wait_ready();'), 1)
            self.assertLess(text.index('portable_wait_ready();'), text.index('dsm_store'))

    def test_hybrid_participants_and_syscall(self):
        text = (ROOT / 'tests/e2e/workloads/portable_large_workload.h').read_text()
        block = text.split('static inline void portable_wait_ready(void)', 1)[1].split('static inline', 1)[0]
        self.assertIn('2 * NUM_SOCKETS', block)
        self.assertIn('SYS_SYNC_WAIT', block)
        self.assertIn('0x80000000u', block)
        self.assertNotIn('read_counter', block)

    def test_namespace_and_local_threads(self):
        text = (ROOT / 'gem5/src/sim/sync_wait.cc').read_text()
        self.assertIn('planeMask = mask & ~0x80000000u', text)
        self.assertIn('_barriers[mask]', text)
        self.assertIn('bs.waiting.size() >= bs.localExpected', text)
        self.assertIn('tc->suspend()', text)

    def test_gate_pumps_and_is_bounded(self):
        text = (ROOT / 'modules/ubiomodule/ubio_main.cc').read_text()
        self.assertIn('!fromNetwork && (mask & startupTag)', text)
        self.assertIn('ubcc->allH64BloomSlicesValid()', text)
        self.assertIn('ResidentOverflowPolicy::NaiveEvict', text)
        self.assertIn('peerExitNowMs() - startupStartMs >= startupBudgetMs', text)
        self.assertIn('host->_metaRNF.drainDeferred()', text)
        self.assertIn('if (ubcc) ubcc->wakeup()', text)
        self.assertIn('admitBarrier(startupMask, startupSeq', text)


if __name__ == '__main__':
    unittest.main()
