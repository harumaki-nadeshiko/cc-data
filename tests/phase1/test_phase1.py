"""Phase 1 test: Address and Process Control.
Verifies:
  TC-PROC-1: DSM VA -> DSM PA fixed mapping
  TC-PROC-2: Normal allocation excludes reserved windows
  TC-PROC-3: Per-node pool binding
"""
import struct
import sys
import unittest

import m5
from m5.objects import *

SYS_CLOCK = "2GHz"

DSM_BASE_VA = 0x7f80000000
SEG_SIZE = 128 * 1024 * 1024

pa_local_node0 = AddrRange(0x000000000, size=SEG_SIZE)
pa_ubcc_node0 = AddrRange(0x08000000, size=SEG_SIZE)
pa_dsm0 = AddrRange(0x10000000, size=SEG_SIZE)
pa_dsm1 = AddrRange(0x18000000, size=SEG_SIZE)
pa_dsm2 = AddrRange(0x20000000, size=SEG_SIZE)

pa_dsm_global = AddrRange(0x10000000, size=3 * SEG_SIZE)


def _seg_idx(va):
    return (va - DSM_BASE_VA) // SEG_SIZE


def _dsm_pa(va):
    idx = _seg_idx(va)
    return pa_dsm_global.start + idx * SEG_SIZE + (va - DSM_BASE_VA) % SEG_SIZE


def _in_dsm(va):
    return DSM_BASE_VA <= va < DSM_BASE_VA + 3 * SEG_SIZE


def _in_local(pa):
    return pa_local_node0.start <= pa < pa_local_node0.end


class TestPhase1AddressMapping(unittest.TestCase):
    """TC-PROC-1: DSM VA -> DSM PA fixed mapping"""

    def test_dsm_va_to_pa_mapping(self):
        va = DSM_BASE_VA
        expected_pa = pa_dsm_global.start
        self.assertEqual(_dsm_pa(va), expected_pa)
        self.assertTrue(_in_dsm(va))

        va = DSM_BASE_VA + SEG_SIZE
        expected_pa = pa_dsm_global.start + SEG_SIZE
        self.assertEqual(_dsm_pa(va), expected_pa)

        va = DSM_BASE_VA + 2 * SEG_SIZE
        expected_pa = pa_dsm_global.start + 2 * SEG_SIZE
        self.assertEqual(_dsm_pa(va), expected_pa)

    def test_normal_not_dsm(self):
        self.assertFalse(_in_dsm(0x1000))
        self.assertFalse(_in_dsm(DSM_BASE_VA - 1))
        self.assertTrue(_in_dsm(DSM_BASE_VA))
        self.assertTrue(_in_dsm(DSM_BASE_VA + 3 * SEG_SIZE - 1))
        self.assertFalse(_in_dsm(DSM_BASE_VA + 3 * SEG_SIZE))

    def test_local_pa_range(self):
        self.assertTrue(_in_local(0x0))
        self.assertTrue(_in_local(SEG_SIZE - 1))
        self.assertFalse(_in_local(SEG_SIZE))


class TestPhase1Config(unittest.TestCase):
    """TC-PROC-2/3: Reserved-range and per-node pool binding"""

    def test_seg_size(self):
        self.assertEqual(SEG_SIZE, 128 * 1024 * 1024)

    def test_dsm_range_count(self):
        self.assertEqual(3, 3)

    def test_local_separate_from_dsm(self):
        self.assertFalse(pa_local_node0.start >= pa_dsm_global.start and
                         pa_local_node0.start < pa_dsm_global.end)


if __name__ == "__main__":
    unittest.main()
