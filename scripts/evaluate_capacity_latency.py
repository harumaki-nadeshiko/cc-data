#!/usr/bin/env python3
"""Evaluate the TC131 capacity and end-to-end latency acceptance criteria.

The baseline run must be pure naive/no-latency-optimization.  Capacity is the
largest exact set of metadata records known resident or persisted at a home;
the persisted index already includes resident duplicates, so the two counts
must never be added.
"""
import argparse
import json
import os
import re
import statistics
import sys


STATE_RE = re.compile(r"\[UBCC-STATE\].*capacity=(\d+).*policy=(\w+)")
STATS_RE = re.compile(r"\[UBCC-STATS\] \{.*\"residentCapacity\":(\d+).*")
H64_EXACT_RE = re.compile(
    r"\[UBCC-STATS\] \{\"h64ExactLiveKnown\":(\d+),\"h64ExactLiveCount\":(\d+)\}")
PERF_RE = re.compile(r"\[EP-PERF\] kind=(\w+) node=\d+ pa=0x[0-9a-f]+.*latency_ps=(\d+)")


def coverage(log_dir):
    exact_live = None
    capacity = 0
    policy = None
    for root, _, files in os.walk(log_dir):
        for name in files:
            if name != "stderr.log":
                continue
            with open(os.path.join(root, name), errors="replace") as stream:
                for line in stream:
                    match = STATE_RE.search(line)
                    if match:
                        capacity = max(capacity, int(match.group(1)))
                        policy = match.group(2)
                    match = STATS_RE.search(line)
                    if match:
                        capacity = max(capacity, int(match.group(1)))
                    match = H64_EXACT_RE.search(line)
                    if match and int(match.group(1)) == 1:
                        exact_live = max(exact_live or 0, int(match.group(2)))
    if not capacity:
        raise ValueError(f"no UBCC-STATE coverage record in {log_dir}")
    if policy == "naive":
        # Naive has no metadata backstore by contract. Its exact capacity is
        # the fixed resident directory capacity, not an inferred traffic count.
        return {"policy": policy, "resident_capacity": capacity,
                "h64_exact_live": None,
                "effective_unique_lower_bound": capacity}
    if exact_live is None:
        raise ValueError(f"no validated H64 exact LIVE coverage in {log_dir}")
    return {"policy": policy, "resident_capacity": capacity,
            "h64_exact_live": exact_live,
            "effective_unique_lower_bound": max(capacity, exact_live)}


def mean_protocol_latency(log_dir, kind="outer"):
    values = []
    for root, _, files in os.walk(log_dir):
        for name in files:
            if name != "stderr.log":
                continue
            with open(os.path.join(root, name), errors="replace") as stream:
                for line in stream:
                    match = PERF_RE.search(line)
                    if match and match.group(1) == kind:
                        values.append(int(match.group(2)))
    if not values:
        raise ValueError(f"no completed {kind} protocol samples in {log_dir}")
    return {"samples": len(values), "mean_ps": statistics.mean(values),
            "mean_ns": statistics.mean(values) / 1000.0,
            "p50_ns": statistics.median(values) / 1000.0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-log-dir", required=True)
    parser.add_argument("--spill-no-opt-log-dir", required=True)
    parser.add_argument("--optimized-log-dir", required=True)
    args = parser.parse_args()

    baseline = coverage(args.baseline_log_dir)
    spill = coverage(args.spill_no_opt_log_dir)
    optimized = coverage(args.optimized_log_dir)
    base_lat = mean_protocol_latency(args.baseline_log_dir)
    spill_lat = mean_protocol_latency(args.spill_no_opt_log_dir)
    opt_lat = mean_protocol_latency(args.optimized_log_dir)
    required = baseline["resident_capacity"] * 1.5
    if baseline["policy"] != "naive":
        raise ValueError("baseline log is not a naive-policy run")
    if spill["policy"] != "spill":
        raise ValueError("spill/no-opt log is not a spill-policy run")
    if optimized["policy"] != "spill":
        raise ValueError("optimized log is not a spill-policy run")
    capacity_pass = spill["effective_unique_lower_bound"] >= required
    capacity_latency_delta_ns = spill_lat["mean_ns"] - base_lat["mean_ns"]
    capacity_latency_pass = capacity_latency_delta_ns <= 25.0
    latency_reduction_pct = ((base_lat["mean_ns"] - opt_lat["mean_ns"])
                              / base_lat["mean_ns"] * 100.0)
    report = {"baseline": baseline, "spill_no_latency_optimization": spill,
              "capacity_required_unique": required,
              "capacity_pass": capacity_pass,
              "baseline_outer_protocol": base_lat,
              "spill_no_opt_outer_protocol": spill_lat,
               "optimized_outer_protocol": opt_lat,
               "capacity_latency_delta_ns": capacity_latency_delta_ns,
               "capacity_latency_pass": capacity_latency_pass,
               "latency_reduction_pct": latency_reduction_pct,
               "outer_protocol_diagnostic_only": True,
               "guest_visible_latency_status": "not_measured"}
    print(json.dumps(report, indent=2))
    return 0 if capacity_pass and capacity_latency_pass else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)
