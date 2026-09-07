#!/usr/bin/env python3
import unittest
from pathlib import Path


SOURCE = Path("/workspace/gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc")


class EpsnfEventDrivenWriteContractTest(unittest.TestCase):
    def test_inflight_writes_do_not_schedule_full_map_polling(self):
        source = SOURCE.read_text(encoding="utf-8")
        start = source.index("void\nEPSNFController::processPendingHAWrites()")
        end = source.index("void\nEPSNFController::publishHAWrite", start)
        block = source[start:end]

        incomplete = block[block.index("if (!pending.dataComplete"):
                           block.index("if (!pending.granted)")]
        self.assertNotIn("pendingWork", incomplete)

        inflight = block[block.index("if (result == -2)"):
                         block.index("if (result == 0", block.index("if (result == -2)"))]
        self.assertNotIn("pendingWork = true", inflight)
        self.assertIn("scheduleEvent(Cycles(epsnf_retry_cycles()))", block)


if __name__ == "__main__":
    unittest.main()
