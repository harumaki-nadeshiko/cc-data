#!/usr/bin/env python3
"""Emit explicitly protocol-visible JSONL latency summaries for CC runs."""
import argparse
import json
import os
import re
import statistics
import sys

PERF_RE = re.compile(
    r"\[EP-PERF\] kind=(\w+) node=(\d+) pa=0x[0-9a-f]+.*latency_ps=(\d+)")


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int((len(values) - 1) * q))]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-dir", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    samples = []
    for root, _, files in os.walk(args.log_dir):
        for name in files:
            if name != "stderr.log":
                continue
            with open(os.path.join(root, name), errors="replace") as stream:
                for line in stream:
                    match = PERF_RE.search(line)
                    if match and match.group(1) == "outer":
                        samples.append((int(match.group(2)), int(match.group(3))))
    if not samples:
        raise ValueError("no completed outer protocol samples")

    values = [sample[1] / 1000.0 for sample in samples]
    with open(args.output, "w") as output:
        output.write(json.dumps({
            "kind": "manifest", "scenario": args.scenario, "mode": args.mode,
            "measurement_source": "cc_outer_protocol", "unit": "ns",
            "cross_platform_comparable": False,
            "guest_visible": False,
        }) + "\n")
        for index, (node, latency_ps) in enumerate(samples):
            output.write(json.dumps({
                "kind": "sample", "scenario": args.scenario, "mode": args.mode,
                "measurement_source": "cc_outer_protocol", "node": node,
                "iteration": index, "latency_ns": latency_ps / 1000.0,
            }) + "\n")
        output.write(json.dumps({
            "kind": "summary", "scenario": args.scenario, "mode": args.mode,
            "measurement_source": "cc_outer_protocol", "samples": len(values),
            "mean_ns": statistics.mean(values), "p50_ns": percentile(values, .50),
            "p95_ns": percentile(values, .95), "p99_ns": percentile(values, .99),
            "cross_platform_comparable": False,
        }) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)
