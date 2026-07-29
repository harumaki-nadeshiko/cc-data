#!/usr/bin/env python3
"""Independent E2E verification script.

Reads per-node simout files (+ optional ubio fault logs), aggregates them, and
applies the same per-TC verify functions defined in tests/e2e/test_e2e.py.

usage:
    python3 tests/e2e/verify.py --tc <N> --simout f1 [f2 ...]
                                 [--fault-log g1 ...]

Output:
    Prints progress lines and a final sentinel line
        >>> TC<N> PASSED <<<   or   >>> TC<N> FAILED <<<
    Exit code 0 on PASS, non-zero on FAIL.

Caller (wrapper shell) reads the sentinel line and/or exit code to decide
PASSED/FAILED.
"""
import sys, os, argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from test_e2e import (
    verify_testcase,
    parse_read_vals,
)


def main():
    p = argparse.ArgumentParser(description="E2E single-TC verifier (aggregated)")
    p.add_argument("--tc", type=int, required=True)
    p.add_argument("--simout", nargs="+", default=[],
                   help="per-node simout files to aggregate")
    p.add_argument("--fault-log", nargs="*", default=[],
                   help="ubio stderr logs to scan for [UBFAULT] evidence")
    args = p.parse_args()

    raw_lines = []
    found = 0
    for path in args.simout:
        if os.path.exists(path):
            found += 1
            with open(path) as f:
                raw_lines.extend(line.rstrip("\n") for line in f)
    expected = len(args.simout)

    for path in args.fault_log:
        if os.path.exists(path):
            with open(path, errors="replace") as f:
                for line in f:
                    if ("[UBFAULT]" in line or
                        "[ResidentDirStats]" in line or
                        "[UBCC-STATS]" in line or
                        "[UBCC-NAIVE-EVICT]" in line or
                        "[UBCC-NAIVE-EVICT-DONE]" in line or
                        "[UBCC-NAIVE-DIRTY-RECALL-PAYLOAD]" in line or
                        "[UBIO-POLICY]" in line or
                        "[RUNNER-MANIFEST]" in line or
                        "BATCH-RS" in line or
                        "SILENT" in line or
                        "C4" in line or
                        "DIRECT-FWD" in line or
                        "RESIDENT-WAITER" in line or
                        "RESIDENT-BACKSTORE-KNOWN" in line or
                         "RESIDENT-SPILL-START" in line or
                         "RESIDENT-SPILL-DONE" in line or
                         "UBCC-SPILL-DIRTY-PERSIST" in line or
                         "RESIDENT-FILL-ISSUED" in line or
                        "RESIDENT-FILL-DONE" in line or
                        "RESIDENT-MISS" in line or
                        "RESIDENT-REPLAY" in line or
                        "UBCC-UPGRADE-COMMIT" in line or
                        "upgrade committed" in line or
                        "UBCC-OUTER-REQ" in line or
                        "UBCC-WB-REQ" in line or
                        "UBCC-UPGRADE" in line or
                        "UBCC-CLEAR" in line or
                        "WB-DATA-PERSIST" in line or
                        "BACKSTORE-WRITE" in line or
                        "BACKSTORE-READ" in line or
                         "EvictReq" in line or
                         "UBCC-SHARED-RELEASE" in line or
                         "RESIDENT-WAITER-UPGRADE-DROP-NOT-SHARER" in line):
                        raw_lines.append(line.rstrip("\n"))

    print(f"[verify] TC{args.tc}: aggregated {found}/{expected} simout files, "
          f"{len(raw_lines)} lines", flush=True)

    if found != expected:
        msg = (f"TC{args.tc} FAILED: missing simout files ({found}/{expected})")
        print(f"  {msg}", flush=True)
        print(f">>> TC{args.tc} FAILED <<<", flush=True)
        sys.exit(1)

    reads = parse_read_vals(raw_lines)

    # ── Phase A1: Naive-policy configuration validation for TC131-134 ──
    # When UBCC_POLICY=naive is selected (detected from [UBIO-POLICY] or
    # [RUNNER-MANIFEST] markers), the run must NOT produce any spill or
    # backstore activity.  Fail early if forbidden markers are found.
    if args.tc in (131, 132, 133, 134):
        is_naive = any("[UBIO-POLICY] effective=naive" in line
                       for line in raw_lines)
        if not is_naive:
            is_naive = any("[RUNNER-MANIFEST]" in line and "policy=naive" in line
                           for line in raw_lines)
        if is_naive:
            forbidden = []
            for line in raw_lines:
                if "BACKSTORE-WRITE" in line:
                    forbidden.append(("BACKSTORE-WRITE", line))
                elif "BACKSTORE-READ" in line:
                    forbidden.append(("BACKSTORE-READ", line))
                elif "RESIDENT-SPILL-" in line:
                    forbidden.append(("RESIDENT-SPILL-", line))
                elif "[UBCC-STATS]" in line and '"backstoreIndex"' in line:
                    # Extract backstoreIndex value; nonzero is forbidden
                    import re as _re
                    m = _re.search(r'"backstoreIndex":(\d+)', line)
                    if m and int(m.group(1)) != 0:
                        forbidden.append(("nonzero backstoreIndex", line))
            if forbidden:
                msg = (f"TC{args.tc} FAILED: naive-policy invariant violation "
                       f"({len(forbidden)} occurrences): " +
                       ", ".join(kind for kind, _ in forbidden[:5]))
                print(f"  {msg}", flush=True)
                for kind, line in forbidden[:5]:
                    print(f"    FORBIDDEN [{kind}]: {line[:120]}", flush=True)
                print(f">>> TC{args.tc} FAILED <<<", flush=True)
                sys.exit(1)

    passed, msg, failures = verify_testcase(args.tc, reads, raw_lines)
    print(f"  {msg}", flush=True)
    for f in failures:
        print(f"    MISMATCH: {f['raw']}", flush=True)
    if passed:
        print(f">>> TC{args.tc} PASSED <<<", flush=True)
        sys.exit(0)
    else:
        print(f">>> TC{args.tc} FAILED <<<", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
