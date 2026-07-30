#!/usr/bin/env python3
"""Summarize portable large-workload topology/profile matrices."""
import json
import pathlib
import re
import statistics
import sys


ROOT = pathlib.Path(sys.argv[1])
PROFILES = ("naive", "spill-noopt", "spill-opt")
TOPOLOGIES = ("1s", "2s", "8n1s", "8n2s")
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


def pct_change(candidate, baseline):
    if candidate is None or baseline in (None, 0):
        return None
    return (candidate / baseline - 1.0) * 100.0


def read_case(tc, topology, profile):
    case_dir = ROOT / topology / profile / f"tc{tc}"
    timer_samples = {CASES[tc][0]: [], CASES[tc][1]: []}
    latencies = []
    warnings = []
    for path in case_dir.glob("simout_tc*_node*.log"):
        for line in path.read_text(errors="replace").splitlines():
            match = TIMER_RE.search(line)
            if match and match.group(2) in timer_samples:
                sample = {
                    "plane": int(match.group(1)),
                    "operations": int(match.group(3)),
                    "ticks": int(match.group(4)),
                    "frequency_hz": int(match.group(5)),
                }
                if min(sample["operations"], sample["ticks"],
                       sample["frequency_hz"]) <= 0:
                    warnings.append(f"{match.group(2)} plane{sample['plane']}: invalid timer")
                else:
                    timer_samples[match.group(2)].append(sample)
            match = LATENCY_RE.search(line)
            if match and match.group(2) == CASES[tc][2]:
                frequency = int(match.group(10))
                if frequency <= 0:
                    warnings.append(f"{match.group(2)}: invalid frequency")
                else:
                    latencies.append({
                        "plane": int(match.group(1)),
                        "samples": int(match.group(3)),
                        "mean_ns": int(match.group(9)) * 1.0e9 / frequency,
                        "p50_ns": int(match.group(5)) * 1.0e9 / frequency,
                        "p95_ns": int(match.group(6)) * 1.0e9 / frequency,
                        "p99_ns": int(match.group(7)) * 1.0e9 / frequency,
                    })

    timers = {}
    for phase, samples in timer_samples.items():
        if not samples:
            continue
        plane_ids = [sample["plane"] for sample in samples]
        if len(set(plane_ids)) != len(plane_ids):
            warnings.append(f"{phase}: duplicate plane timer")
            continue
        total_operations = sum(sample["operations"] for sample in samples)
        critical_sample = max(samples,
                              key=lambda sample: sample["ticks"] /
                              sample["frequency_hz"])
        critical_seconds = (critical_sample["ticks"] /
                            critical_sample["frequency_hz"])
        plane_ns_per_op = [sample["ticks"] * 1.0e9 /
                           sample["frequency_hz"] / sample["operations"]
                           for sample in samples]
        timers[phase] = {
            "planes": len(samples),
            "total_operations": total_operations,
            "critical_ticks": critical_sample["ticks"],
            "critical_frequency_hz": critical_sample["frequency_hz"],
            "mean_plane_ns_per_operation": statistics.mean(plane_ns_per_op),
            "max_plane_ns_per_operation": max(plane_ns_per_op),
            "aggregate_throughput_ops_s": total_operations / critical_seconds,
        }

    verify = case_dir / f"verify_tc{tc}.log"
    passed = False
    if verify.exists():
        lines = verify.read_text(errors="replace").splitlines()
        passed = bool(lines and lines[-1] == f">>> TC{tc} PASSED <<<")
    batch_latency = None
    if latencies:
        plane_ids = [latency["plane"] for latency in latencies]
        if len(set(plane_ids)) != len(plane_ids):
            warnings.append(f"{CASES[tc][2]}: duplicate plane latency")
            latencies = []
    if latencies:
        batch_latency = {
            "planes": len(latencies),
            "mean_of_plane_means_ns": statistics.mean(x["mean_ns"] for x in latencies),
            "max_plane_p99_ns": max(x["p99_ns"] for x in latencies),
        }
    return {"passed": passed, "timers": timers,
            "batch_latency": batch_latency, "warnings": warnings}


report = {"log_root": str(ROOT), "testcases": {}, "comparisons": {}}
for topology in TOPOLOGIES:
    if not (ROOT / topology).exists():
        continue
    topo_cases = report["testcases"].setdefault(topology, {})
    topo_comparisons = report["comparisons"].setdefault(topology, {})
    for tc in CASES:
        cases = {profile: read_case(tc, topology, profile) for profile in PROFILES}
        topo_cases[str(tc)] = cases
        baseline = cases["naive"]["timers"]
        comparison = {}
        for profile in ("spill-noopt", "spill-opt"):
            candidate = cases[profile]["timers"]
            comparison[profile] = {}
            for phase in CASES[tc][:2]:
                base_metric = baseline.get(phase, {})
                candidate_metric = candidate.get(phase, {})
                comparison[profile][phase] = {
                    "mean_plane_latency_change_pct": pct_change(
                        candidate_metric.get("mean_plane_ns_per_operation"),
                        base_metric.get("mean_plane_ns_per_operation")),
                    "aggregate_throughput_change_pct": pct_change(
                        candidate_metric.get("aggregate_throughput_ops_s"),
                        base_metric.get("aggregate_throughput_ops_s")),
                }
        topo_comparisons[str(tc)] = comparison

print(json.dumps(report, indent=2, sort_keys=True))
