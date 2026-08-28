#!/usr/bin/env python3

import hashlib
import pathlib
import unittest
import zipfile
from email.parser import Parser


ROOT = pathlib.Path(__file__).resolve().parents[2]
WHEELS = ROOT / "tools/wheels/aarch64-cp311"


class Aarch64MatplotlibWheelsTest(unittest.TestCase):
    def test_wheel_set_matches_hash_manifest_and_target_architecture(self):
        expected = {}
        for line in (WHEELS / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split(None, 1)
            expected[name.strip()] = digest
        actual = {path.name: path for path in WHEELS.glob("*.whl")}
        self.assertEqual(set(actual), set(expected))
        for name, path in actual.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                             expected[name])
            self.assertNotIn("x86_64", name)
            if "none-any" not in name:
                self.assertIn("aarch64", name)
            with zipfile.ZipFile(path) as archive:
                wheel_name = next(item for item in archive.namelist()
                                  if item.endswith(".dist-info/WHEEL"))
                metadata_name = next(item for item in archive.namelist()
                                     if item.endswith(".dist-info/METADATA"))
                wheel = Parser().parsestr(archive.read(wheel_name).decode())
                metadata = Parser().parsestr(archive.read(metadata_name).decode())
                tags = wheel.get_all("Tag", [])
                if "none-any" in name:
                    self.assertTrue(any(tag.endswith("-none-any") for tag in tags))
                else:
                    self.assertTrue(all("aarch64" in tag for tag in tags), tags)
                for member in archive.namelist():
                    if not member.endswith(".so"):
                        continue
                    elf = archive.read(member)
                    self.assertEqual(elf[:4], b"\x7fELF")
                    byteorder = "little" if elf[5] == 1 else "big"
                    self.assertEqual(int.from_bytes(elf[18:20], byteorder), 183,
                                     f"{name}:{member} is not AArch64")
                actual[name] = (path, metadata["Name"].lower().replace("_", "-"),
                                metadata["Version"])

    def test_requirements_lock_contains_matplotlib(self):
        requirements = (WHEELS / "requirements.txt").read_text().splitlines()
        self.assertIn("matplotlib==3.10.3", requirements)
        self.assertIn("numpy==2.2.6", requirements)
        locked = {line.split("==", 1)[0].lower().replace("_", "-"):
                  line.split("==", 1)[1] for line in requirements if "==" in line}
        metadata = {}
        for path in WHEELS.glob("*.whl"):
            with zipfile.ZipFile(path) as archive:
                item = next(name for name in archive.namelist()
                            if name.endswith(".dist-info/METADATA"))
                parsed = Parser().parsestr(archive.read(item).decode())
                metadata[parsed["Name"].lower().replace("_", "-")] = parsed["Version"]
        self.assertEqual(metadata, locked)


if __name__ == "__main__":
    unittest.main()
