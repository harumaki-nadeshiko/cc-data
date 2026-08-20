import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "collect_runtime_fingerprint.py"
SPEC = importlib.util.spec_from_file_location("fingerprint", str(SCRIPT))
fingerprint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fingerprint)


class FingerprintTests(unittest.TestCase):
    def test_parse_key_values(self):
        parsed = fingerprint.parse_key_values('NAME="Ubuntu"\nID=ubuntu\n# ignored\nEMPTY=\n')
        self.assertEqual(parsed, {"EMPTY": "", "ID": "ubuntu", "NAME": "Ubuntu"})

    def test_parse_ldd_is_sorted_and_marks_not_found(self):
        parsed = fingerprint.parse_ldd(
            "libz.so => not found\nliba.so => /lib/liba.so (0x1)\n/lib64/ld.so (0x2)\n")
        self.assertEqual([item["name"] for item in parsed],
                         ["/lib64/ld.so", "liba.so", "libz.so"])
        self.assertIsNone(parsed[-1]["resolved"])

    def test_binary_hash_and_ldd(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample"
            path.write_bytes(b"fingerprint me")
            with mock.patch.object(fingerprint.shutil, "which", return_value="/usr/bin/ldd"), \
                    mock.patch.object(fingerprint, "run", return_value=(0, "libx.so => /x/libx.so (0x1)")):
                result = fingerprint.binary_info(str(path))
        self.assertEqual(result["sha256"],
                         "8ed69646bc6c582e671a7bf3e725ef70dd95c42fd6887db4c662627af0543d74")
        self.assertEqual(result["ldd"]["dependencies"][0]["resolved"], "/x/libx.so")

    def test_compare_classifies_host_and_artifact(self):
        baseline = {
            "label": "local", "host": {"machine": "x86_64"},
            "git": {"head": "aaa"}, "runtime": {"python": {"version": "3.8"}},
        }
        current = {
            "label": "remote", "host": {"machine": "aarch64"},
            "git": {"head": "bbb"}, "runtime": {"python": {"version": "3.9"}},
        }
        diff = fingerprint.compare(baseline, current)
        classes = {item["field"]: item["classification"] for item in diff}
        self.assertEqual(classes["host.machine"], "ignored")
        self.assertEqual(classes["label"], "ignored")
        self.assertEqual(classes["git.head"], "required")
        self.assertEqual(classes["runtime.python.version"], "required")

    def test_strict_host_and_custom_ignore(self):
        baseline = {"host": {"machine": "x"}, "git": {"dirty": False}}
        current = {"host": {"machine": "y"}, "git": {"dirty": True}}
        diff = fingerprint.compare(baseline, current, ["git.*"], strict_host=True)
        classes = {item["field"]: item["classification"] for item in diff}
        self.assertEqual(classes, {"git.dirty": "ignored", "host.machine": "required"})

    def test_binary_location_is_host_only_but_hash_is_required(self):
        baseline = {"binaries": [{"path": "/local/app", "sha256": "aaa"}]}
        current = {"binaries": [{"path": "/remote/app", "sha256": "bbb"}]}
        diff = fingerprint.compare(baseline, current)
        classes = {item["field"]: item["classification"] for item in diff}
        self.assertEqual(classes["binaries.0.path"], "ignored")
        self.assertEqual(classes["binaries.0.sha256"], "required")

    def test_zmq_version_calls_ctypes_api(self):
        class FakeFunction:
            def __call__(self, major, minor, patch):
                major._obj.value, minor._obj.value, patch._obj.value = 4, 3, 5

        fake_library = type("Library", (), {"zmq_version": FakeFunction()})()
        with mock.patch.object(fingerprint.ctypes, "CDLL", return_value=fake_library):
            result = fingerprint.zmq_version("libzmq-test.so")
        self.assertEqual(result["version"], "4.3.5")

    def test_git_info_falls_back_from_recursive_submodules(self):
        responses = iter([
            (0, "/repo"), (0, "abc"), (0, ""),
            (1, "recursive failed"),
            (0, " fee4f8f37e7862d7311c5c78555b337fd72ab492 gem5"),
        ])
        with mock.patch.object(fingerprint, "run", side_effect=lambda *a, **k: next(responses)):
            result = fingerprint.git_info("/repo")
        self.assertEqual(result["submodules"][0]["path"], "gem5")
        self.assertEqual(result["submodules"][0]["head"],
                         "fee4f8f37e7862d7311c5c78555b337fd72ab492")

    def test_main_compare_exit_codes(self):
        current = {"host": {"machine": "remote"}, "git": {"head": "same"}}
        with tempfile.TemporaryDirectory() as directory:
            baseline = Path(directory) / "baseline.json"
            baseline.write_text(json.dumps({"host": {"machine": "local"},
                                            "git": {"head": "same"}}))
            with mock.patch.object(fingerprint, "collect", return_value=current):
                self.assertEqual(fingerprint.main(["--compare", str(baseline)]), 0)
            baseline.write_text(json.dumps({"host": {"machine": "local"},
                                            "git": {"head": "different"}}))
            with mock.patch.object(fingerprint, "collect", return_value=current):
                self.assertEqual(fingerprint.main(["--compare", str(baseline)]), 1)

    def test_collected_json_is_deterministic(self):
        args = type("Args", (), {"label": "x", "libzmq": None,
                                  "container_image_id": "sha256:test", "repo": ".",
                                  "binary": ["z", "a"]})()
        uname = type("Uname", (), dict(system="Linux", node="n", release="r",
                                        version="v", machine="m"))()
        with mock.patch.object(fingerprint.platform, "uname", return_value=uname), \
                mock.patch.object(fingerprint, "command_version", return_value=None), \
                mock.patch.object(fingerprint, "cpu_info", return_value={"count": 1, "model": "cpu"}), \
                mock.patch.object(fingerprint, "read_text", return_value="ID=test\n"), \
                mock.patch.object(fingerprint, "zmq_version", return_value={"library": None, "version": None}), \
                mock.patch.object(fingerprint, "git_info", return_value={"available": False}), \
                mock.patch.object(fingerprint, "binary_info", side_effect=lambda p: {"path": p}), \
                mock.patch.dict(os.environ, {}, clear=True):
            first = fingerprint.collect(args)
            second = fingerprint.collect(args)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        self.assertEqual([item["path"] for item in first["binaries"]], ["a", "z"])
        self.assertEqual(first["runtime"]["container_image_id"], "sha256:test")


if __name__ == "__main__":
    unittest.main()
