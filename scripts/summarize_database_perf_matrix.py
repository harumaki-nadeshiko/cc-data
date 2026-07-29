#!/usr/bin/env python3
"""Summarize guest-visible service and end-to-end database workload metrics."""
import json
import pathlib
import re
import sys


ROOT = pathlib.Path(sys.argv[1])
PROFILES = ("naive", "spill-noopt", "spill-opt")
CASES = {
    142: ("db_oltp_service", "db_oltp_end_to_end", "db_oltp_batch_32ops"),
    143: ("db_btree_service", "db_btree_end_to_end", "db_btree_batch_64ops"),
    144: ("db_wal_service", "db_wal_end_to_end", "db_wal_batch_32ops"),
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


def read_case(tc, profile):
    case_dir = ROOT / profile / f"tc{tc}"
    timers = {}
    latency = None
    warnings = []
    for path in case_dir.glob("simout_tc*_node*.log"):
        for line in path.read_text(errors="replace").splitlines():
            match = TIMER_RE.search(line)
            if match and match.group(2) in CASES[tc][:2]:
                operations = int(match.group(3))
                ticks = int(match.group(4))
                frequency = int(match.group(5))
                valid = operations > 0 and ticks > 0 and frequency > 0
                timer = {
                    "operations": operations,
                    "ticks": ticks,
                    "frequency_hz": frequency,
                    "ns_per_operation": (ticks * 1.0e9 / frequency / operations
                                         if valid else None),
                    "throughput_ops_s": (operations * frequency / ticks
                                         if valid else None),
                }
                if not valid:
                    timer["invalid_reason"] = "non-positive operations/ticks/frequency"
                    warnings.append(f"{match.group(2)}: {timer['invalid_reason']}")
                timers[match.group(2)] = timer
            match = LATENCY_RE.search(line)
            if match and match.group(2) == CASES[tc][2]:
                frequency = int(match.group(10))
                valid = frequency > 0
                latency = {
                    "phase": match.group(2),
                    "samples": int(match.group(3)),
                    "mean_ns": (int(match.group(9)) * 1.0e9 / frequency
                                if valid else None),
                    "p50_ns": (int(match.group(5)) * 1.0e9 / frequency
                               if valid else None),
                    "p95_ns": (int(match.group(6)) * 1.0e9 / frequency
                               if valid else None),
                    "p99_ns": (int(match.group(7)) * 1.0e9 / frequency
                               if valid else None),
                }
                if not valid:
                    latency["invalid_reason"] = "non-positive counter frequency"
                    warnings.append(f"{match.group(2)}: {latency['invalid_reason']}")
    verify = case_dir / f"verify_tc{tc}.log"
    passed = False
    if verify.exists():
        lines = verify.read_text(errors="replace").splitlines()
        passed = bool(lines and lines[-1] == f">>> TC{tc} PASSED <<<")
    return {"passed": passed, "timers": timers, "batch_latency": latency,
            "warnings": warnings}


report = {"log_root": str(ROOT), "testcases": {}, "comparisons": {}}
for tc in CASES:
    cases = {profile: read_case(tc, profile) for profile in PROFILES}
    report["testcases"][str(tc)] = cases
    baseline = cases["naive"]["timers"]
    comparison = {}
    for profile in ("spill-noopt", "spill-opt"):
        candidate = cases[profile]["timers"]
        comparison[profile] = {}
        for phase in CASES[tc][:2]:
            base_metric = baseline.get(phase, {})
            candidate_metric = candidate.get(phase, {})
            comparison[profile][phase] = {
                "latency_change_pct": pct_change(
                    candidate_metric.get("ns_per_operation"),
                    base_metric.get("ns_per_operation")),
                "throughput_change_pct": pct_change(
                    candidate_metric.get("throughput_ops_s"),
                    base_metric.get("throughput_ops_s")),
            }
    report["comparisons"][str(tc)] = comparison

print(json.dumps(report, indent=2, sort_keys=True))
