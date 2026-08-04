#!/usr/bin/env python3
"""Summarize EPBackend protocol-latency evidence from E2E process logs."""
import argparse
import os
import re
import statistics
import sys


PERF_RE = re.compile(
    r"\[EP-PERF\] kind=(\w+) node=(\d+) pa=0x([0-9a-f]+).*latency_ps=(\d+)")
LOG_BASENAMES = {"stdout.log", "stderr.log"}


def samples(log_dir, kind):
    values = []
    for root, _, names in os.walk(log_dir):
        for name in names:
            if name not in LOG_BASENAMES:
                continue
            with open(os.path.join(root, name), errors="replace") as stream:
                for line in stream:
                    match = PERF_RE.search(line)
                    if match and match.group(1) == kind:
                        values.append(int(match.group(4)))
    return values


def describe(values):
    if not values:
        return {"samples": 0}
    return {"samples": len(values), "mean_ns": statistics.mean(values) / 1000.0,
            "p50_ns": statistics.median(values) / 1000.0,
            "min_ns": min(values) / 1000.0, "max_ns": max(values) / 1000.0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline")
    parser.add_argument("optimized")
    parser.add_argument("--kind", default="outer")
    args = parser.parse_args()
    baseline = samples(args.baseline, args.kind)
    optimized = samples(args.optimized, args.kind)
    print(f"kind={args.kind}")
    print(f"baseline={describe(baseline)}")
    print(f"optimized={describe(optimized)}")
    if baseline and optimized:
        delta_ns = statistics.mean(optimized) / 1000.0 - statistics.mean(baseline) / 1000.0
        print(f"mean_delta_ns={delta_ns:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
