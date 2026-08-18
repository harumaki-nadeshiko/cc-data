#!/usr/bin/env python3
"""Summarize repeated 16N1S O3 portable performance runs."""

import gzip
import json
import pathlib
import re
import statistics
import sys


ROOT = pathlib.Path(sys.argv[1])
PROFILES = ("naive", "spill-noopt", "optimized")
CASES = {
    142: ("db_oltp_service", "db_oltp_end_to_end", "db_oltp_batch_32ops"),
    143: ("db_btree_service", "db_btree_end_to_end", "db_btree_batch_64ops"),
    144: ("db_wal_service", "db_wal_end_to_end", "db_wal_batch_32ops"),
    145: ("faas_service", "faas_end_to_end", "faas_batch_64ops"),
    146: ("graph_service", "graph_end_to_end", "graph_batch_64ops"),
    147: ("feature_service", "feature_end_to_end", "feature_batch_64ops"),
}
TIMER_RE = re.compile(
    r"\[GUEST-TIMER\] node=(\d+) phase=(\S+) operations=(\d+) "
    r"counter_ticks=(\d+) counter_frequency_hz=(\d+) "
    r"source=arm_cntvct_el0 unit=counter_ticks")
LATENCY_RE = re.compile(
    r"\[PERF-LATENCY\] node=(\d+) phase=(\S+) samples=(\d+) "
    r"min=(\d+) p50=(\d+) p95=(\d+) p99=(\d+) max=(\d+) mean=(\d+) "
    r"counter_frequency_hz=(\d+) source=arm_cntvct_el0 unit=counter_ticks")


def read_lines(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", errors="replace") as stream:
        yield from stream


def mean_stdev_cv(values):
    if not values:
        return None
    mean = statistics.mean(values)
    stdev = statistics.stdev(values) if len(values) > 1 else 0.0
    return {
        "samples": len(values),
        "mean": mean,
        "stdev": stdev,
        "cv_pct": stdev / mean * 100.0 if mean else None,
        "min": min(values),
        "max": max(values),
    }


def pct_change(candidate, baseline):
    if candidate is None or baseline in (None, 0):
        return None
    return (candidate / baseline - 1.0) * 100.0


def summarize_run(case_dir, tc):
    timer_samples = {CASES[tc][0]: [], CASES[tc][1]: []}
    latencies = []
    paths = list(case_dir.glob(f"simout_tc{tc}_node*.log"))
    paths += list(case_dir.glob(f"simout_tc{tc}_node*.log.gz"))
    for path in paths:
        for line in read_lines(path):
            match = TIMER_RE.search(line)
            if match and match.group(2) in timer_samples:
                timer_samples[match.group(2)].append({
                    "plane": int(match.group(1)),
                    "operations": int(match.group(3)),
                    "ticks": int(match.group(4)),
                    "frequency_hz": int(match.group(5)),
                })
            match = LATENCY_RE.search(line)
            if match and match.group(2) == CASES[tc][2]:
                frequency = int(match.group(10))
                latencies.append({
                    "plane": int(match.group(1)),
                    "samples": int(match.group(3)),
                    "mean_ns": int(match.group(9)) * 1.0e9 / frequency,
                    "p50_ns": int(match.group(5)) * 1.0e9 / frequency,
                    "p95_ns": int(match.group(6)) * 1.0e9 / frequency,
                    "p99_ns": int(match.group(7)) * 1.0e9 / frequency,
                })

    metrics = {}
    for phase, samples in timer_samples.items():
        if len(samples) != 16 or len({sample["plane"] for sample in samples}) != 16:
            continue
        plane_ns_per_op = [
            sample["ticks"] * 1.0e9 / sample["frequency_hz"] /
            sample["operations"] for sample in samples
        ]
        critical_seconds = max(
            sample["ticks"] / sample["frequency_hz"] for sample in samples)
        total_operations = sum(sample["operations"] for sample in samples)
        metrics[phase] = {
            "planes": 16,
            "total_operations": total_operations,
            "mean_plane_ns_per_operation": statistics.mean(plane_ns_per_op),
            "max_plane_ns_per_operation": max(plane_ns_per_op),
            "aggregate_throughput_ops_s": total_operations / critical_seconds,
        }
    if len(latencies) == 16 and len({item["plane"] for item in latencies}) == 16:
        metrics[CASES[tc][2]] = {
            "planes": 16,
            "mean_of_plane_means_ns": statistics.mean(
                item["mean_ns"] for item in latencies),
            "max_plane_p99_ns": max(item["p99_ns"] for item in latencies),
        }
    return metrics


report = {"log_root": str(ROOT), "runs": {}, "aggregate": {}, "comparisons": {}}
results = []
for path in sorted((ROOT / "cases").glob("*/result.json")):
    try:
        result = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        continue
    tc = int(result["tc"])
    result["metrics"] = summarize_run(path.parent, tc)
    results.append(result)
    report["runs"][result["case_key"]] = result

for tc in CASES:
    tc_key = str(tc)
    report["aggregate"][tc_key] = {}
    by_repeat = {}
    for profile in PROFILES:
        runs = [item for item in results if item["tc"] == tc and
                item["profile"] == profile and item["status"] == "PASS"]
        by_repeat[profile] = {item["repeat"]: item for item in runs}
        profile_report = {"pass_runs": len(runs), "metrics": {}}
        for phase in CASES[tc][:2]:
            phase_runs = [item["metrics"].get(phase, {}) for item in runs]
            profile_report["metrics"][phase] = {
                "mean_plane_ns_per_operation": mean_stdev_cv([
                    item["mean_plane_ns_per_operation"] for item in phase_runs
                    if "mean_plane_ns_per_operation" in item]),
                "aggregate_throughput_ops_s": mean_stdev_cv([
                    item["aggregate_throughput_ops_s"] for item in phase_runs
                    if "aggregate_throughput_ops_s" in item]),
            }
        latency_runs = [item["metrics"].get(CASES[tc][2], {}) for item in runs]
        profile_report["metrics"][CASES[tc][2]] = {
            "mean_of_plane_means_ns": mean_stdev_cv([
                item["mean_of_plane_means_ns"] for item in latency_runs
                if "mean_of_plane_means_ns" in item]),
            "max_plane_p99_ns": mean_stdev_cv([
                item["max_plane_p99_ns"] for item in latency_runs
                if "max_plane_p99_ns" in item]),
        }
        report["aggregate"][tc_key][profile] = profile_report

    comparison = {}
    for profile in ("spill-noopt", "optimized"):
        common = sorted(set(by_repeat["naive"]) & set(by_repeat[profile]))
        profile_comparison = {}
        for phase in CASES[tc][:2]:
            latency_changes = []
            throughput_changes = []
            for repeat in common:
                baseline = by_repeat["naive"][repeat]["metrics"].get(phase, {})
                candidate = by_repeat[profile][repeat]["metrics"].get(phase, {})
                latency = pct_change(
                    candidate.get("mean_plane_ns_per_operation"),
                    baseline.get("mean_plane_ns_per_operation"))
                throughput = pct_change(
                    candidate.get("aggregate_throughput_ops_s"),
                    baseline.get("aggregate_throughput_ops_s"))
                if latency is not None:
                    latency_changes.append(latency)
                if throughput is not None:
                    throughput_changes.append(throughput)
            profile_comparison[phase] = {
                "latency_change_pct": mean_stdev_cv(latency_changes),
                "throughput_change_pct": mean_stdev_cv(throughput_changes),
            }
        comparison[profile] = profile_comparison
    report["comparisons"][tc_key] = comparison

print(json.dumps(report, indent=2, sort_keys=True))
