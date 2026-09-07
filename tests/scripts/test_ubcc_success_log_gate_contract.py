#!/usr/bin/env python3
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "modules/ubiomodule/UBCCController.cc"


class UbccSuccessLogGateContractTest(unittest.TestCase):
    def test_high_frequency_success_markers_are_diagnostic_only(self):
        source = SOURCE.read_text(encoding="utf-8")
        markers = (
            "[UBCC-SPILL-DIRTY-PERSIST]",
            "[RESIDENT-REPLAY-PUSH]",
            "[RESIDENT-CAPACITY-REPLAY]",
            "[UBINV-INFO]",
            "[C4-PUSHGRANT]",
            "grant hit PA=",
            "tombstone HIT for PA=",
            "checkTombstone HIT PA=",
        )
        for marker in markers:
            pos = source.index(marker)
            self.assertIn(
                "if (_evidenceEvents || _verboseLog)",
                source[max(0, pos - 800):pos],
            )


if __name__ == "__main__":
    unittest.main()
