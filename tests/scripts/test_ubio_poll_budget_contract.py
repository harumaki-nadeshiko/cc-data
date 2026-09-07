#!/usr/bin/env python3
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "modules/ubiomodule/ubio_main.cc"


class UbioPollBudgetContractTest(unittest.TestCase):
    def test_budget_is_checked_before_receiving_next_message(self):
        source = SOURCE.read_text(encoding="utf-8")
        start = source.index("auto pollAndProcess")
        end = source.index("LogInfo(\"UBIO\", \"[UBIO-RUNLOOP-READY]", start)
        block = source[start:end]

        self.assertIn("if (drain_cnt >= 200)", block)
        self.assertIn("const Message *m = receiveNext();", block)
        self.assertNotIn("if (++drain_cnt > 200)", block)
        self.assertEqual(block.count("ReceiveMessage(port, tick, &st)"), 1)


if __name__ == "__main__":
    unittest.main()
