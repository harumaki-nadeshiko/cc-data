#!/usr/bin/env python3
"""Machine-checkable registry gate for protocol dynamic-state containers."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


KINDS = {"map", "set", "unordered_map", "deque", "vector", "zmq-socket-queue"}
CLASSIFICATIONS = {"hard", "indirect", "unbounded", "host-only"}
DECL_RE = re.compile(
    r"\bstd::(?P<kind>map|set|unordered_map|deque|vector)\s*<.*>\s*"
    r"(?P<symbol>[A-Za-z_]\w*)\s*(?:\{[^;]*\}|=[^;]*)?;"
)


def _error(errors: list[str], message: str) -> None:
    errors.append(message)


def load_and_validate(root: Path, manifest_path: Path) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"cannot read manifest {manifest_path}: {exc}"]

    if not isinstance(data, dict) or data.get("schema_version") != 1:
        _error(errors, "schema_version must be 1")
    entries = data.get("entries")
    exclusions = data.get("exclusions", [])
    targets = data.get("scan_targets")
    if not isinstance(entries, list):
        _error(errors, "entries must be an array")
        entries = []
    if not isinstance(exclusions, list):
        _error(errors, "exclusions must be an array")
        exclusions = []
    if not isinstance(targets, list) or not targets:
        _error(errors, "scan_targets must be a non-empty array")
        targets = []

    required = {
        "id", "file", "symbol", "kind", "classification", "capacity",
        "owner", "rationale", "target_replacement",
    }
    seen_ids: set[str] = set()
    seen_locations: set[tuple[str, str]] = set()
    for index, entry in enumerate(entries):
        where = f"entries[{index}]"
        if not isinstance(entry, dict):
            _error(errors, f"{where} must be an object")
            continue
        missing = sorted(required - entry.keys())
        if missing:
            _error(errors, f"{where} missing fields: {', '.join(missing)}")
            continue
        if not all(isinstance(entry[k], str) and entry[k] for k in
                   ("id", "file", "symbol", "owner", "rationale", "target_replacement")):
            _error(errors, f"{where} string fields must be non-empty")
        if entry["id"] in seen_ids:
            _error(errors, f"duplicate id: {entry['id']}")
        seen_ids.add(entry["id"])
        location = (entry["file"], entry["symbol"])
        if location in seen_locations:
            _error(errors, f"duplicate file/symbol: {entry['file']}:{entry['symbol']}")
        seen_locations.add(location)
        if entry["kind"] not in KINDS:
            _error(errors, f"{where}.kind is invalid: {entry['kind']}")
        if entry["classification"] not in CLASSIFICATIONS:
            _error(errors, f"{where}.classification is invalid: {entry['classification']}")
        capacity = entry["capacity"]
        if capacity is not None and not isinstance(capacity, (int, str)):
            _error(errors, f"{where}.capacity must be integer, string, or null")
        if entry["classification"] == "hard" and capacity in (None, ""):
            _error(errors, f"{where} hard entry requires capacity")
        source = root / entry["file"]
        if not source.is_file():
            _error(errors, f"registered file does not exist: {entry['file']}")
            continue
        text = source.read_text(encoding="utf-8", errors="replace")
        if not re.search(rf"\b{re.escape(entry['symbol'])}\b", text):
            _error(errors, f"registered symbol not found: {entry['file']}:{entry['symbol']}")
        declaration = next((d for d in scan_file(source) if d[1] == entry["symbol"]), None)
        if declaration and declaration[0] != entry["kind"]:
            _error(errors, f"kind mismatch for {entry['file']}:{entry['symbol']}: "
                           f"manifest={entry['kind']} source={declaration[0]}")

    exclusion_locations: set[tuple[str, str]] = set()
    for index, exclusion in enumerate(exclusions):
        where = f"exclusions[{index}]"
        if not isinstance(exclusion, dict) or set(("file", "symbol", "reason")) - exclusion.keys():
            _error(errors, f"{where} requires file, symbol, reason")
            continue
        if exclusion.get("category") not in ("immutable", "config"):
            _error(errors, f"{where}.category must be immutable or config")
        location = (exclusion["file"], exclusion["symbol"])
        if location in exclusion_locations or location in seen_locations:
            _error(errors, f"duplicate/existing exclusion: {location[0]}:{location[1]}")
        exclusion_locations.add(location)
        source = root / exclusion["file"]
        if not source.is_file():
            _error(errors, f"excluded file does not exist: {exclusion['file']}")
        elif not re.search(rf"\b{re.escape(exclusion['symbol'])}\b",
                           source.read_text(encoding="utf-8", errors="replace")):
            _error(errors, f"excluded symbol not found: {exclusion['file']}:{exclusion['symbol']}")
    return data, errors


def scan_file(path: Path) -> list[tuple[str, str, int]]:
    declarations: list[tuple[str, str, int]] = []
    brace_depth = 0
    class_depths: list[int] = []
    pending = ""
    pending_line = 0
    for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        code = line.split("//", 1)[0]
        if re.search(r"\b(?:class|struct)\s+\w+[^;{]*\{", code):
            class_depths.append(brace_depth + 1)
        stripped = line.strip()
        direct_class_member = bool(class_depths and brace_depth == class_depths[-1])
        persistent_scope = brace_depth == 0 or direct_class_member
        if pending:
            pending += " " + code.strip()
            if ";" in code:
                match = DECL_RE.search(pending)
                if match:
                    declarations.append((match.group("kind"), match.group("symbol"), pending_line))
                pending = ""
                pending_line = 0
        elif (persistent_scope and "std::" in code
              and not stripped.startswith(("using ", "typedef "))):
            if ";" in code:
                match = DECL_RE.search(code)
                if match:
                    declarations.append((match.group("kind"), match.group("symbol"), line_no))
            else:
                pending = code.strip()
                pending_line = line_no
        brace_depth += code.count("{") - code.count("}")
        while class_depths and brace_depth < class_depths[-1]:
            class_depths.pop()
    return declarations


def target_files(root: Path, targets: list[str]) -> list[Path]:
    files: set[Path] = set()
    for target in targets:
        path = root / target
        if path.is_file():
            files.add(path)
        elif path.is_dir():
            for suffix in ("*.h", "*.hh", "*.hpp", "*.cc", "*.cpp", "*.cxx"):
                files.update(path.rglob(suffix))
    return sorted(files)


def audit(root: Path, manifest_path: Path, fail_on_unbounded: bool) -> dict[str, Any]:
    data, errors = load_and_validate(root, manifest_path)
    entries = data.get("entries", []) if isinstance(data, dict) else []
    registered = {(e.get("file"), e.get("symbol")) for e in entries if isinstance(e, dict)}
    excluded = {(e.get("file"), e.get("symbol")) for e in data.get("exclusions", [])
                if isinstance(e, dict)}
    unknown: list[dict[str, Any]] = []
    for source in target_files(root, data.get("scan_targets", [])):
        relative = source.relative_to(root).as_posix()
        for kind, symbol, line in scan_file(source):
            if (relative, symbol) not in registered and (relative, symbol) not in excluded:
                unknown.append({"file": relative, "line": line, "symbol": symbol, "kind": kind})

    classifications = Counter(e.get("classification") for e in entries if isinstance(e, dict))
    kinds = Counter(e.get("kind") for e in entries if isinstance(e, dict))
    unbounded = [e for e in entries if isinstance(e, dict)
                 and e.get("classification") == "unbounded"]
    # Classification is singular: host-only records are not part of the unbounded
    # protocol-debt list, while every registered unbounded record is strict-failing.
    strict_debt = list(unbounded)
    ok = not errors and not unknown and not (fail_on_unbounded and strict_debt)
    return {
        "ok": ok,
        "mode": "strict" if fail_on_unbounded else "registry",
        "summary": {
            "entries": len(entries), "exclusions": len(excluded),
            "unknown": len(unknown), "errors": len(errors),
            "unbounded": len(unbounded), "strict_unbounded": len(strict_debt),
            "classifications": dict(sorted(classifications.items())),
            "kinds": dict(sorted(kinds.items())),
        },
        "errors": errors,
        "unknown": unknown,
        "unbounded": [{"id": e["id"], "file": e["file"], "symbol": e["symbol"]}
                      for e in strict_debt],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--fail-on-unbounded", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    manifest = args.manifest or root / "configs/protocol_state_capacity_manifest.json"
    result = audit(root, manifest.resolve(), args.fail_on_unbounded)
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        s = result["summary"]
        print(f"protocol-state-capacity[{result['mode']}]: "
              f"entries={s['entries']} exclusions={s['exclusions']} "
              f"unknown={s['unknown']} unbounded={s['unbounded']} errors={s['errors']} "
              f"result={'PASS' if result['ok'] else 'FAIL'}")
        for error in result["errors"]:
            print(f"ERROR: {error}", file=sys.stderr)
        for item in result["unknown"]:
            print(f"UNKNOWN: {item['file']}:{item['line']} {item['kind']} {item['symbol']}",
                  file=sys.stderr)
        if args.fail_on_unbounded:
            for item in result["unbounded"]:
                print(f"UNBOUNDED: {item['id']} {item['file']}:{item['symbol']}", file=sys.stderr)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
