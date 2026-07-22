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
import sys


STATE_RE = re.compile(r"\[UBCC-STATE\].*backstore_index=(\d+).*capacity=(\d+).*policy=(\w+)")
STATS_RE = re.compile(r"\[UBCC-STATS\] \{.*\"residentCapacity\":(\d+).*\"backstoreIndex\":(\d+).*")
LATENCY_RE = re.compile(r"\[LATENCY\] node=\d+ phase=(\w+) iter=\d+ cycles=(\d+)")


def coverage(log_dir):
    best_index = 0
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
                        best_index = max(best_index, int(match.group(1)))
                        capacity = max(capacity, int(match.group(2)))
                        policy = match.group(3)
                    match = STATS_RE.search(line)
                    if match:
                        capacity = max(capacity, int(match.group(1)))
                        best_index = max(best_index, int(match.group(2)))
    if not capacity:
        raise ValueError(f"no UBCC-STATE coverage record in {log_dir}")
    # backstore_index is an exact set, not a traffic counter.  It overlaps
    # ResidentDir, therefore max is a conservative union lower bound.
    return {"policy": policy, "resident_capacity": capacity,
            "backstore_unique": best_index,
            "effective_unique_lower_bound": max(capacity, best_index)}


def mean_e2e(chain_path):
    with open(chain_path) as stream:
        chains = json.load(stream).get("chains", {}).values()
    values = [chain.get("e2e_latency_ps") for chain in chains
              if chain.get("e2e_latency_ps", 0) > 0]
    if not values:
        raise ValueError(f"no completed end-to-end chains in {chain_path}")
    return {"samples": len(values), "mean_ps": sum(values) / len(values),
            "mean_ns": sum(values) / len(values) / 1000.0}


def mean_guest_cycles(log_dir, phase):
    values = []
    for root, _, files in os.walk(log_dir):
        for name in files:
            if not name.startswith("simout_tc131_node"):
                continue
            with open(os.path.join(root, name), errors="replace") as stream:
                for line in stream:
                    match = LATENCY_RE.search(line)
                    if match and match.group(1) == phase:
                        values.append(int(match.group(2)))
    if not values:
        raise ValueError(f"no {phase} guest latency samples in {log_dir}")
    return {"samples": len(values), "mean_cycles": sum(values) / len(values),
            "mean_ns": sum(values) / len(values) / 2.0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-log-dir", required=True)
    parser.add_argument("--spill-no-opt-log-dir", required=True)
    parser.add_argument("--baseline-chains", required=True)
    parser.add_argument("--spill-no-opt-chains", required=True)
    parser.add_argument("--optimized-chains", required=True)
    args = parser.parse_args()

    baseline = coverage(args.baseline_log_dir)
    spill = coverage(args.spill_no_opt_log_dir)
    base_trace_lat = mean_e2e(args.baseline_chains)
    spill_trace_lat = mean_e2e(args.spill_no_opt_chains)
    # Silent upgrades do not emit an outer request chain. Guest issue-to-store
    # completion is therefore the common end-to-end boundary for metric 2.
    base_reuse = mean_guest_cycles(args.baseline_log_dir, "catalog_reuse")
    spill_reuse = mean_guest_cycles(args.spill_no_opt_log_dir, "catalog_reuse")
    base_upgrade = mean_guest_cycles(args.baseline_log_dir, "exclusive_upgrade")
    opt_upgrade = mean_guest_cycles(os.path.dirname(args.optimized_chains), "exclusive_upgrade")
    required = baseline["resident_capacity"] * 1.5
    if baseline["policy"] != "naive":
        raise ValueError("baseline log is not a naive-policy run")
    if spill["policy"] != "spill":
        raise ValueError("spill/no-opt log is not a spill-policy run")
    capacity_pass = spill["effective_unique_lower_bound"] >= required
    capacity_latency_delta_ns = spill_reuse["mean_ns"] - base_reuse["mean_ns"]
    capacity_latency_pass = capacity_latency_delta_ns <= 25.0
    latency_reduction_pct = ((base_upgrade["mean_cycles"] - opt_upgrade["mean_cycles"])
                             / base_upgrade["mean_cycles"] * 100.0)
    latency_pass = latency_reduction_pct >= 10.0
    report = {"baseline": baseline, "spill_no_latency_optimization": spill,
              "capacity_required_unique": required,
              "capacity_pass": capacity_pass,
              "baseline_trace_e2e": base_trace_lat,
              "spill_no_opt_trace_e2e": spill_trace_lat,
              "baseline_catalog_reuse": base_reuse,
              "spill_no_opt_catalog_reuse": spill_reuse,
              "capacity_latency_delta_ns": capacity_latency_delta_ns,
              "capacity_latency_pass": capacity_latency_pass,
              "baseline_exclusive_upgrade": base_upgrade,
              "optimized_exclusive_upgrade": opt_upgrade,
              "latency_reduction_pct": latency_reduction_pct,
              "latency_pass": latency_pass}
    print(json.dumps(report, indent=2))
    return 0 if capacity_pass and capacity_latency_pass and latency_pass else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)
