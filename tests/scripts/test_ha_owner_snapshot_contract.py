import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "gem5/gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc"


class HaOwnerSnapshotContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = BACKEND.read_text(encoding="utf-8")

    def test_recall_snapshot_uses_registered_ha_data_cache(self):
        start = self.source.index("EPBackend::handleRecallRequest")
        end = self.source.index("EPBackend::sendRecallResponse", start)
        body = self.source[start:end]
        self.assertIn("readHADataCacheLine(recallMsg.sourceSocket", body)
        self.assertNotIn("_ruby_system->functionalRead", body)

    def test_snapshot_rejects_conflicting_readable_l1_copies(self):
        match = re.search(
            r"EPBackend::readHADataCacheLine\([^)]*\)\s*const\s*\{(.*?)\n\}",
            self.source,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        body = re.sub(r"\s+", " ", match.group(1))
        self.assertIn("AccessPermission_Read_Only", body)
        self.assertIn("AccessPermission_Read_Write", body)
        self.assertIn("entry->getDataBlk()", body)
        self.assertIn("conflicting HA L1 copies", body)


if __name__ == "__main__":
    unittest.main()
