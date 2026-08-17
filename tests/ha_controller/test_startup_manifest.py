import json
import os
import subprocess
import unittest


class StartupManifestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary = os.environ["HA_CONTROLLER_MANIFEST_BIN"]

    def invoke(self, base, size, line, nodes, success=True):
        result = subprocess.run(
            [self.binary, str(base), str(size), str(line), str(nodes)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        return result

    def test_exact_budget_profiles(self):
        budget_bits = 512 * 1024 * 8
        for nodes in (2, 3, 4, 6, 8, 16):
            with self.subTest(nodes=nodes):
                lines = budget_bits // nodes
                manifest = self.invoke(0, lines * 64, 64, nodes)
                self.assertEqual(manifest["component"], "FlatBitmapDirectory")
                self.assertEqual(manifest["bits_per_line"], nodes)
                self.assertEqual(manifest["payload_bits"], lines * nodes)
                self.assertLessEqual(manifest["payload_bytes_allocated"], 512 * 1024)
                self.assertTrue(manifest["within_budget"])

    def test_manifest_range_and_alignment(self):
        manifest = self.invoke(0x80000000, 128 * 1024 * 1024, 64, 2)
        self.assertEqual(manifest["base"], 0x80000000)
        self.assertEqual(manifest["range_bytes"], 128 * 1024 * 1024)
        self.invoke(1, 64, 64, 2, success=False)
        self.invoke(0, 65, 64, 2, success=False)
        self.invoke(0, 64, 64, 0, success=False)
        self.invoke(0, 64, 64, 65, success=False)

    def test_over_budget_rejected(self):
        budget_bits = 512 * 1024 * 8
        self.invoke(0, (budget_bits // 2 + 1) * 64, 64, 2, success=False)


if __name__ == "__main__":
    unittest.main()
