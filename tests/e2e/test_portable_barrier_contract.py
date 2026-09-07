import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tests/e2e/workloads/portable_large_workload.h"


class PortableBarrierContractTest(unittest.TestCase):
    def test_barrier_has_no_artificial_guest_work(self):
        source = SOURCE.read_text(encoding="utf-8")
        start = source.index("static inline void portable_barrier")
        block = source[start:source.index("#define PORTABLE_SERIAL", start)]
        self.assertIn("_syscall3(SYS_SYNC_WAIT", block)
        self.assertNotIn("coherence_settle", block)
        self.assertNotIn("dsb sy", block)


if __name__ == "__main__":
    unittest.main()
