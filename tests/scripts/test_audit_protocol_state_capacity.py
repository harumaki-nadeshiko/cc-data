import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/audit_protocol_state_capacity.py"
SPEC = importlib.util.spec_from_file_location("capacity_audit", SCRIPT)
AUDIT = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(AUDIT)


def entry(classification="hard", capacity=4):
    return {
        "id": "fixture.queue", "file": "src/state.hh", "symbol": "queue_",
        "kind": "deque", "classification": classification, "capacity": capacity,
        "owner": "test", "rationale": "fixture", "target_replacement": "fixed ring",
    }


class CapacityAuditTest(unittest.TestCase):
    def fixture(self, declaration="std::deque<int> queue_;", classification="hard", capacity=4):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "src").mkdir()
        (root / "src/state.hh").write_text(
            "class S {\npublic:\n    " + declaration.replace("; ", ";\n    ") + "\n};\n",
            encoding="utf-8")
        manifest = {"schema_version": 1, "scan_targets": ["src"],
                    "entries": [entry(classification, capacity)], "exclusions": []}
        path = root / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return temp, root, path

    def test_registered_declaration_passes(self):
        temp, root, manifest = self.fixture()
        self.addCleanup(temp.cleanup)
        result = AUDIT.audit(root, manifest, False)
        self.assertTrue(result["ok"], result)

    def test_unknown_declaration_fails(self):
        temp, root, manifest = self.fixture("std::deque<int> queue_; std::map<int,int> leak_;")
        self.addCleanup(temp.cleanup)
        result = AUDIT.audit(root, manifest, False)
        self.assertFalse(result["ok"])
        self.assertEqual([x["symbol"] for x in result["unknown"]], ["leak_"])

    def test_multiline_unknown_declaration_fails(self):
        temp, root, manifest = self.fixture(
            "std::deque<int> queue_; std::map<int,\n        int> multiline_leak_;")
        self.addCleanup(temp.cleanup)
        result = AUDIT.audit(root, manifest, False)
        self.assertFalse(result["ok"])
        self.assertEqual([x["symbol"] for x in result["unknown"]],
                         ["multiline_leak_"])

    def test_unbounded_is_legacy_debt_only_in_default_mode(self):
        temp, root, manifest = self.fixture(classification="unbounded", capacity=None)
        self.addCleanup(temp.cleanup)
        self.assertTrue(AUDIT.audit(root, manifest, False)["ok"])
        self.assertFalse(AUDIT.audit(root, manifest, True)["ok"])

    def test_json_cli_is_machine_readable(self):
        temp, root, manifest = self.fixture()
        self.addCleanup(temp.cleanup)
        run = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(root), "--manifest", str(manifest), "--json"],
            check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(json.loads(run.stdout)["ok"])

    def test_duplicate_id_and_missing_symbol_fail_schema_validation(self):
        temp, root, manifest = self.fixture()
        self.addCleanup(temp.cleanup)
        data = json.loads(manifest.read_text())
        duplicate = dict(data["entries"][0], file="src/state.hh", symbol="missing_")
        data["entries"].append(duplicate)
        manifest.write_text(json.dumps(data))
        result = AUDIT.audit(root, manifest, False)
        self.assertFalse(result["ok"])
        self.assertTrue(any("duplicate id" in error for error in result["errors"]))
        self.assertTrue(any("symbol not found" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
