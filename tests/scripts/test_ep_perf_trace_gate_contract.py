#!/usr/bin/env python3
import unittest
from pathlib import Path


GEM5 = Path("/workspace/gem5")
SOURCE = GEM5 / "src/mem/ruby/protocol/chi/ep/EPBackend.cc"


class EpPerfTraceGateContractTest(unittest.TestCase):
    def test_success_traces_obey_trace_policy(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn('#include "protocol/TracePerfPolicy.hh"', source)
        self.assertEqual(source.count("[EP-PERF]"), 4)
        self.assertGreaterEqual(
            source.count('TracePerfPolicy::get().shouldEmit("gem5")'), 4
        )


if __name__ == "__main__":
    unittest.main()
