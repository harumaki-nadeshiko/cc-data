#!/usr/bin/env python3

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class OversizedResidentDirContractTest(unittest.TestCase):
    def test_counterfactual_override_is_explicit_and_default_off(self):
        header = (ROOT / "modules/ubiomodule/ResidentDir.hh").read_text()
        source = (ROOT / "modules/ubiomodule/ResidentDir.cc").read_text()
        main = (ROOT / "modules/ubiomodule/ubio_main.cc").read_text()

        self.assertIn("bool   allow_oversized_for_test = false;", header)
        self.assertIn("--allow-oversized-resident-dir-for-test", main)
        self.assertIn("g_rdcfg.allow_oversized_for_test = true", main)
        self.assertIn("experimental_oversized_resident_dir", main)
        self.assertIn("experimental_oversized={}", source)

        self.assertGreaterEqual(
            source.count("!cfg.allow_oversized_for_test"), 2)
        self.assertIn("!g_rdcfg.allow_oversized_for_test", main)


if __name__ == "__main__":
    unittest.main()
