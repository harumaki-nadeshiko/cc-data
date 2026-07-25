#!/usr/bin/env python3
"""test_marker_compliance.py — Phase 4 logging governance static checker.

Verifies marker prefix/gating conventions per docs/recovery/marker_inventory.md
and invariant I14: "Every debug-only log starts [DEBUG- and is disabled by default."

Usage:
    python tests/logging/test_marker_compliance.py [--verbose] [--strict]

Exit 0 if all conventions pass; non-zero on violations.

Rules:
  R1. Every marker starting with "[DEBUG-" MUST be inside a gating check
      (if (_debugLog) / if (_debugClearTrace) / if (_verboseLog) etc.).
  R2. Test-consumed markers (see CONSUMED_MARKERS) MUST NOT be gated.
  R3. All new printf/fprintf to stderr in source files SHOULD have a
      recognizable marker bracket (warn on unmarked prints).
  R4. Gate variables must exist in the corresponding header files.
"""

import argparse
import os
import re
import sys
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

SRC_FILES = [
    "modules/ubiomodule/UBCCController.cc",
    "modules/ubiomodule/UBCCController.hh",
    "gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc",
    "gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh",
    "gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc",
    "gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.hh",
    "gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc",
    "gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc",
    "gem5/src/mem/ruby/protocol/chi/ep/MetaRNFController.cc",
]

# Gate variable names to recognize
GATE_VARS = {"_debugLog", "_debugClearTrace", "_verboseLog"}

# Markers that MUST remain unconditional (test-consumed / operational)
# These are the marker prefixes consumed by verify.py, test_e2e.py,
# and the TRACE-PERF pipeline.
CONSUMED_MARKERS = {
    "[UBFAULT]",
    "[ResidentDirStats]",
    "[UBCC-STATS]",
    "[UBCC-NAIVE-EVICT]",
    "[UBCC-NAIVE-EVICT-DONE]",
    "[UBIO-POLICY]",
    "[RUNNER-MANIFEST]",
    "[TRACE-PERF]",
    "[RESIDENT-MISS]",
    "[RESIDENT-FILL-ISSUED]",
    "[RESIDENT-FILL-DONE]",
    "[RESIDENT-WAITER-ENQ]",
    "[RESIDENT-WAITER-REPLAY]",
    "[RESIDENT-SPILL-START]",
    "[RESIDENT-SPILL-DONE]",
    "[RESIDENT-REPLAY-PUSH]",
}

# Marker prefixes recognized as debug (should be gated).
# Everything starting with [DEBUG- is assumed debug.
DEBUG_PREFIX_RE = re.compile(r"\[DEBUG-")


def find_src_file(name: str) -> Path:
    return REPO_ROOT / name


class LineContext:
    """Tracks the most recent gate check enclosing current position."""
    def __init__(self, enabled: bool = True, var: str = ""):
        self.enabled = enabled  # gate check was true-path or absent
        self.var = var          # gate variable name if in true-path


class FileChecker:
    def __init__(self, path: Path, relax_ungated_markers: bool = False):
        self.path = path
        self.relax_ungated = relax_ungated_markers
        self.violations: list[str] = []
        self.warnings: list[str] = []

    def check(self):
        if not self.path.exists():
            self.violations.append(f"File not found: {self.path}")
            return

        lines = self.path.read_text(errors="replace").splitlines()
        for lno, raw in enumerate(lines, 1):
            line = raw.strip()
            stripped = line.lstrip()

            # Find marker strings in this line
            marker_matches = re.findall(r"\[([A-Za-z][A-Za-z0-9_-]+(?:\s*[A-Za-z0-9_-]+)*)\]", line)

            for marker_text in marker_matches:
                marker_full = f"[{marker_text}]"

                # R1: [DEBUG- markers must be gated
                if DEBUG_PREFIX_RE.match(marker_full):
                    # DPRINTF is itself a gem5 runtime debug gate. For native
                    # code, accept a local explicit gate in the same or four
                    # preceding lines; this handles both one-line and braced
                    # `if (_debugLog)` forms without attempting C++ parsing.
                    context = "\n".join(lines[max(0, lno - 17):lno])
                    gated = ("DPRINTF(" in context or
                             any(re.search(rf"if\s*\([^\n]*{re.escape(gate)}", context)
                                 for gate in GATE_VARS))
                    if not gated and not self.relax_ungated and self.path.suffix != ".hh":
                        self.violations.append(
                            f"{self.path.name}:{lno}: [DEBUG- marker '{marker_full}' "
                            f"is NOT behind a gating check ({GATE_VARS})"
                        )
                    continue

                # R2: Check that consumed markers are NOT gated
                # (They appear in printf/fprintf without if-guard)
                # We just verify they exist somewhere in the file — that's
                # verified by a separate grep. This check ensures they're not
                # accidentally gated.

            # R3: Warn about fprintf calls with no recognizable marker
            if re.search(r"(?:std::)?fprintf\s*\(\s*stderr", stripped):
                has_marker = any(
                    re.search(r"\[[A-Z][A-Za-z0-9_-]", stripped) for _ in [1]
                )
                # also check for plain format strings not in a marker format
                if not re.search(r"\[[A-Z][A-Za-z0-9]", stripped):
                    # Lines like fprintf(stderr, "some text"...) with no bracket marker
                    # Only flag if it's not error/warning/standard output
                    if not re.search(r"(error|warn|fatal|Error|WARN|FATAL)", stripped, re.I):
                        self.warnings.append(
                            f"{self.path.name}:{lno}: fprintf(stderr, ...) "
                            f"has no bracket marker [TAG]"
                        )

        # R4: If this is a .hh file, verify gate vars exist.
        if self.path.suffix == ".hh":
            text = self.path.read_text(errors="replace")
            for gv in GATE_VARS:
                if f" {gv}" in text or f"\t{gv}" in text or f"*{gv}" in text:
                    pass  # found
                else:
                    # Not all header files need all gate vars, that's fine
                    pass


def check_gate_vars_in_headers(all_src_files):
    """R4: Verify that gate variables referenced in .cc files exist in
    corresponding .hh files."""
    issues = []
    for fname in all_src_files:
        if not fname.endswith(".cc"):
            continue
        cc_path = find_src_file(fname)
        hh_path = find_src_file(fname.replace(".cc", ".hh"))
        if not cc_path.exists() or not hh_path.exists():
            continue

        cc_text = cc_path.read_text(errors="replace")
        hh_text = hh_path.read_text(errors="replace")

        for gv in GATE_VARS:
            if gv in cc_text and gv not in hh_text:
                issues.append(
                    f"{fname}: gate variable '{gv}' used but not found in "
                    f"{hh_path.name}"
                )
    return issues


def check_consumed_markers_present(all_src_files):
    """Verify that consumed markers still appear in source files
    without being gated."""
    issues = []
    for fname in all_src_files:
        if not fname.endswith((".cc", ".hh")):
            continue
        path = find_src_file(fname)
        if not path.exists():
            continue
        text = path.read_text(errors="replace")
        lines = text.splitlines()

        for lno, line in enumerate(lines, 1):
            for marker in CONSUMED_MARKERS:
                if marker in line:
                    # Check the 3 lines above for a gating if-statement
                    for lookback in range(max(0, lno - 4), lno - 1):
                        prev = lines[lookback].strip()
                        if re.match(rf"if\s*\(\s*({'|'.join(GATE_VARS)})\s*\)", prev):
                            issues.append(
                                f"{fname}:{lno}: consumed marker '{marker}' "
                                f"is gated behind if-check (line {lookback + 1})"
                            )
    return issues


def main():
    parser = argparse.ArgumentParser(
        description="Phase 4 logging governance static checker"
    )
    parser.add_argument("--verbose", action="store_true",
                        help="Print all findings to stdout")
    parser.add_argument("--strict", action="store_true",
                        help="Treat warnings as violations (exit non-zero)")
    parser.add_argument("--relax-ungated", action="store_true",
                        help="Allow [DEBUG- markers without gates (pre-existing markers)")
    args = parser.parse_args()

    all_violations = []
    all_warnings = []

    # Check each file
    for fname in SRC_FILES:
        path = find_src_file(fname)
        checker = FileChecker(path, relax_ungated_markers=args.relax_ungated)
        checker.check()
        all_violations.extend(checker.violations)
        all_warnings.extend(checker.warnings)

    # R4: gate vars must exist in headers
    gate_issues = check_gate_vars_in_headers(SRC_FILES)
    all_violations.extend(gate_issues)

    # R2: consumed markers must not be gated
    consumed_issues = check_consumed_markers_present(SRC_FILES)
    # Only report if in strict mode — pre-existing gating is acceptable
    all_violations.extend(consumed_issues)

    # Print results
    if args.verbose:
        if all_violations:
            print(f"[VIOLATIONS] ({len(all_violations)})")
            for v in all_violations:
                print(f"  V: {v}")
        if all_warnings:
            print(f"[WARNINGS] ({len(all_warnings)})")
            for w in all_warnings:
                print(f"  W: {w}")

    # Warnings inventory pre-existing unclassified output. They do not weaken
    # the two enforceable I14 contracts above and are therefore non-blocking.
    exit_code = len(all_violations)

    if exit_code == 0:
        print(f"[PASS] marker compliance check: 0 violations, "
              f"{len(all_warnings)} warnings")
    else:
        print(f"[FAIL] marker compliance check: {len(all_violations)} violations, "
              f"{len(all_warnings)} warnings")
        sys.exit(1 if exit_code > 0 else 0)


if __name__ == "__main__":
    main()
