#!/usr/bin/env python3
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HEADER = ROOT / "modules/ubiomodule/ResidentDir.hh"
SOURCE = ROOT / "modules/ubiomodule/ResidentDir.cc"
CONTROLLER = ROOT / "modules/ubiomodule/UBCCController.cc"


class ResidentDirHostIndexContractTest(unittest.TestCase):
    def test_dirty_index_preserves_lowest_slot_scan_order(self):
        header = HEADER.read_text(encoding="utf-8")
        source = SOURCE.read_text(encoding="utf-8")
        controller = CONTROLLER.read_text(encoding="utf-8")

        self.assertIn("std::set<size_t> _dirtySlotsHostIndex", header)
        self.assertIn("dirtySlotsHostIndex() const", header)
        self.assertIn("_dirtySlotsHostIndex.insert(slot)", source)
        self.assertIn("_dirtySlotsHostIndex.erase(slot)", source)
        self.assertIn("dirtySlots.begin()", controller)

    def test_bit_access_is_byte_windowed(self):
        source = SOURCE.read_text(encoding="utf-8")
        start = source.index("ResidentDir::writeBits")
        end = source.index("// Set/way addressing", start)
        block = source[start:end]

        self.assertIn("unsigned __int128 window", block)
        self.assertIn("byteCount", block)
        self.assertNotIn("bitOffset + i) / 8", block)


if __name__ == "__main__":
    unittest.main()
