#!/usr/bin/env python3
"""Summarize guest-visible HA workload JSONL samples."""
import argparse
import json
import statistics
import sys


def percentile(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int((len(values) - 1) * q))]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", nargs="+", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    records = []
    for path in args.input:
        with open(path, errors="replace") as stream:
            for line in stream:
                if line.startswith("{"):
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

    manifests = [r for r in records if r.get("kind") == "manifest"]
    samples = [r for r in records if r.get("kind") == "sample" and
               r.get("measurement_source") == "guest_cntvct"]
    validations = [r for r in records if r.get("kind") == "validation"]
    if not manifests or not samples or any(r.get("errors") != 0 for r in validations):
        raise ValueError("missing guest samples or successful validation")

    scenario = manifests[0]["scenario"]
    with open(args.output, "w") as output:
        for record in manifests:
            output.write(json.dumps(record) + "\n")
        for record in samples:
            output.write(json.dumps(record) + "\n")
        for phase in sorted({r["phase"] for r in samples}):
            values = [r["latency_ticks"] / r.get("operations", 1)
                      for r in samples if r["phase"] == phase]
            output.write(json.dumps({
                "kind": "summary", "scenario": scenario, "phase": phase,
                "measurement_source": "guest_cntvct", "unit": "ticks",
                "samples": len(values), "mean_ticks": statistics.mean(values),
                "p50_ticks": percentile(values, .50),
                "p95_ticks": percentile(values, .95),
                "p99_ticks": percentile(values, .99),
                "timer_resolution_limited": not any(values),
                "guest_visible": True,
            }) + "\n")
        for record in validations:
            output.write(json.dumps(record) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)
