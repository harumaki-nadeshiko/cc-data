#!/usr/bin/env python3
"""Extract a small, shareable UBCC fault-state report from remote logs."""

import argparse
import gzip
import os
import pathlib
import sys
from collections import Counter


MARKERS = (
    ("build", "[UBCC-PROTOCOL-BUILD]"),
    ("tuple", "[UBCC-TUPLE-STATE]"),
    ("unknown_clear", "[UBCC-UNKNOWN-CLEAR-STATE]"),
    ("stale_eviction", "[UBCC-EVICTION-ACK-STALE]"),
)


def open_text(path):
    if path.name.endswith(".gz"):
        return gzip.open(path, "rt", errors="replace")
    return path.open(errors="replace")


def iter_logs(root):
    seen = set()
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            name for name in dirnames
            if not pathlib.Path(directory, name).is_symlink())
        for name in sorted(filenames):
            if not name.endswith((".log", ".txt", ".out", ".log.gz", ".txt.gz")):
                continue
            path = pathlib.Path(directory, name)
            if path.is_symlink() or not path.is_file():
                continue
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino)
            if identity in seen:
                continue
            seen.add(identity)
            yield path


def extract(root, limit):
    samples = {kind: [] for kind, _ in MARKERS}
    totals = Counter()
    files_scanned = 0
    lines_scanned = 0
    errors = []
    for path in iter_logs(root):
        files_scanned += 1
        try:
            with open_text(path) as stream:
                for line_number, line in enumerate(stream, 1):
                    lines_scanned += 1
                    for kind, marker in MARKERS:
                        if marker not in line:
                            continue
                        totals[kind] += 1
                        if len(samples[kind]) < limit:
                            samples[kind].append({
                                "file": str(path),
                                "line": line_number,
                                "text": line.strip()[:4000],
                            })
        except (OSError, EOFError, gzip.BadGzipFile) as error:
            errors.append(f"{path}: {error}")
    return {
        "root": str(root),
        "files_scanned": files_scanned,
        "lines_scanned": lines_scanned,
        "totals": totals,
        "samples": samples,
        "errors": errors,
    }


def format_report(report):
    totals = report["totals"]
    lines = [
        f"ROOT {report['root']}",
        f"SCAN files={report['files_scanned']} lines={report['lines_scanned']} "
        f"errors={len(report['errors'])}",
        "TOTALS " + " ".join(
            f"{kind}={totals[kind]}" for kind, _ in MARKERS),
    ]
    for kind, _ in MARKERS:
        label = kind.upper()
        rows = report["samples"][kind]
        if not rows:
            lines.append(f"{label} none")
            continue
        for row in rows:
            lines.append(
                f"{label} {row['file']}:{row['line']} {row['text']}")
    for error in report["errors"]:
        lines.append(f"ERROR {error}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(
        description="Extract UBCC build and fault-state markers")
    parser.add_argument("log_dir")
    parser.add_argument("--limit", type=int, default=8,
                        help="maximum samples per marker kind")
    parser.add_argument("--output", help="write report to this path")
    args = parser.parse_args()
    root = pathlib.Path(args.log_dir).resolve()
    if not root.is_dir():
        parser.error(f"not a directory: {root}")
    if args.limit <= 0:
        parser.error("--limit must be positive")
    text = format_report(extract(root, args.limit))
    if args.output:
        pathlib.Path(args.output).write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
