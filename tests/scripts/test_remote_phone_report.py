import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "remote_phone_report.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("remote_phone_report", str(SCRIPT))
reporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reporter)


class RemotePhoneReportTests(unittest.TestCase):
    def test_named_paths_preserve_spaces(self):
        self.assertEqual(reporter.named_path("app=/tmp/a path/app"),
                         ("app", "/tmp/a path/app"))
        self.assertEqual(reporter.named_path("/tmp/a path/file.bin"),
                         ("file.bin", "/tmp/a path/file.bin"))

    def test_text_output_is_three_ascii_lines_with_comparisons(self):
        report = {
            "environment": {"arch": "arm64", "kernel": "6.1", "libc": "glibc-2.36",
                            "python": "3.11", "compiler": "gcc-12.2", "libzmq": "4.3.5"},
            "baseline": "/base.json",
            "baseline_environment": {"arch": "x86_64", "kernel": "6.1", "libc": "glibc-2.31",
                                     "python": "3.11", "compiler": "gcc-9.4", "libzmq": "4.3.5"},
            "paths": [{"name": "my app", "exists": True, "sha256": "a" * 64,
                       "baseline_sha256": "b" * 64, "baseline_status": "different"}],
            "ldd": [{"name": "my app", "status": "ok", "missing_count": 1,
                     "missing": ["libx.so"]}],
        }
        lines = reporter.text_lines(report)
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(line.isascii() for line in lines))
        self.assertIn("arch=arm64[base=x86_64]", lines[0])
        self.assertIn("python=3.11[=base]", lines[0])
        self.assertIn("my_app=aaaaaaaaaaaa[base=bbbbbbbbbbbb]", lines[1])
        self.assertIn("my_app=1:libx.so", lines[2])

    def test_collect_paths_hashes_file_with_spaces_and_marks_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            present = Path(directory) / "an artifact.txt"
            present.write_bytes(b"hello")
            paths, ldd = reporter.collect_paths(
                [("gone", str(Path(directory) / "missing binary"))],
                [("note", str(present))], None)
        self.assertEqual(paths[0]["exists"], False)
        self.assertEqual(paths[1]["sha256"][:12], "2cf24dba5fb0")
        self.assertEqual(ldd[0]["status"], "binary-missing")

    def test_baseline_hash_matches_by_named_basename(self):
        baseline = {"binaries": [{"path": "/workspace/bin/tool", "sha256": "abc"}]}
        with mock.patch.object(reporter.fingerprint, "binary_info", return_value={
                "path": "/remote/different", "exists": True, "sha256": "abc",
                "ldd": {"available": True, "dependencies": [], "missing": []}}):
            paths, _ = reporter.collect_paths([("tool", "/remote/different")], [], baseline)
        self.assertEqual(paths[0]["baseline_status"], "same")

    def test_json_mode_is_single_line(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "file with spaces"
            artifact.write_text("data", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--artifact", "data=%s" % artifact, "--json"],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        parsed = json.loads(result.stdout)
        self.assertEqual(parsed["paths"][0]["name"], "data")


if __name__ == "__main__":
    unittest.main()
