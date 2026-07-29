#!/usr/bin/env python3
"""Summarize TC135-TC140 guest and protocol performance metrics."""
import json
import pathlib
import re
import statistics
import sys


ROOT = pathlib.Path(sys.argv[1])
PROFILES = ("naive", "spill-noopt", "spill-opt")
LATENCY_RE = re.compile(
    r"\[PERF-LATENCY\] node=(\d+) phase=(\S+) samples=(\d+) "
    r"min=(\d+) p50=(\d+) p95=(\d+) p99=(\d+) max=(\d+) mean=(\d+) "
    r"counter_frequency_hz=(\d+) source=arm_cntvct_el0 unit=counter_ticks")
TIMER_RE = re.compile(
    r"\[GUEST-TIMER\] node=(\d+) phase=(\S+) operations=(\d+) "
    r"counter_ticks=(\d+) counter_frequency_hz=(\d+) "
    r"source=arm_cntvct_el0 unit=counter_ticks")
OUTER_RE = re.compile(
    r"\[EP-PERF\] kind=outer node=(\d+) pa=0x[0-9a-f]+ reqId=(\d+) "
    r"start=(\d+) end=(\d+) latency_ps=(\d+)")


def percentile(values, quantile):
    ordered = sorted(values)
    rank = max(1, (len(ordered) * quantile + 99) // 100)
    return ordered[min(len(ordered), rank) - 1]


def pct_change(candidate, baseline):
    if baseline in (None, 0) or candidate is None:
        return None
    return (candidate / baseline - 1.0) * 100.0


def read_statuses():
    statuses = {}
    matrix = ROOT / "matrix.tsv"
    if not matrix.exists():
        return statuses
    for line in matrix.read_text(errors="replace").splitlines()[1:]:
        fields = line.split("\t")
        if len(fields) >= 6 and fields[0] in {"PASS", "FAIL"}:
            statuses[(int(fields[1]), fields[2])] = fields[4]
    return statuses


def summarize_case(tc, profile, status):
    case_dir = ROOT / profile / f"tc{tc}"
    latency = []
    timers = []
    outer_ps = []
    text_lines = []

    for path in case_dir.glob("simout_tc*_node*.log"):
        for line in path.read_text(errors="replace").splitlines():
            match = LATENCY_RE.search(line)
            if match:
                frequency = int(match.group(10))
                ticks = {
                    "min": int(match.group(4)),
                    "p50": int(match.group(5)),
                    "p95": int(match.group(6)),
                    "p99": int(match.group(7)),
                    "max": int(match.group(8)),
                    "mean": int(match.group(9)),
                }
                latency.append({
                    "node": int(match.group(1)),
                    "phase": match.group(2),
                    "samples": int(match.group(3)),
                    "frequency_hz": frequency,
                    "ticks": ticks,
                    "ns": {key: value * 1.0e9 / frequency
                           for key, value in ticks.items()},
                })
            match = TIMER_RE.search(line)
            if match and match.group(2) != "timer_selftest":
                operations = int(match.group(3))
                ticks = int(match.group(4))
                frequency = int(match.group(5))
                timers.append({
                    "node": int(match.group(1)),
                    "phase": match.group(2),
                    "operations": operations,
                    "ticks": ticks,
                    "frequency_hz": frequency,
                    "ticks_per_operation": ticks / operations,
                    "throughput_ops_s": operations * frequency / ticks if ticks else None,
                })

    for path in case_dir.glob("gem5_tc*_node*/stderr.log"):
        for line in path.read_text(errors="replace").splitlines():
            match = OUTER_RE.search(line)
            if match:
                outer_ps.append(int(match.group(5)))
            if ("SILENT" in line or "BATCH-RS" in line or
                    "DIRECT-FWD" in line):
                text_lines.append(line)

    stats_text = ""
    for stream in ("stderr.log", "stdout.log"):
        for path in case_dir.glob(f"ubio_n*_s*/{stream}"):
            stats_text += path.read_text(errors="replace")

    outer = None
    if outer_ps:
        outer_ns = [value / 1000.0 for value in outer_ps]
        outer = {
            "samples": len(outer_ns),
            "mean_ns": statistics.mean(outer_ns),
            "p50_ns": percentile(outer_ns, 50),
            "p95_ns": percentile(outer_ns, 95),
            "p99_ns": percentile(outer_ns, 99),
            "max_ns": max(outer_ns),
        }

    return {
        "status": status,
        "latency": latency,
        "guest_timers": timers,
        "outer_protocol": outer,
        "activity": {
            "naive_evictions": stats_text.count("[UBCC-NAIVE-EVICT]"),
            "spill_starts": stats_text.count("RESIDENT-SPILL-START"),
            "spill_completions": stats_text.count("RESIDENT-SPILL-DONE"),
            "fill_issued": stats_text.count("RESIDENT-FILL-ISSUED"),
            "silent_markers": sum("SILENT" in line for line in text_lines),
            "batch_rs_markers": sum("BATCH-RS" in line for line in text_lines),
            "direct_fwd_markers": sum("DIRECT-FWD" in line for line in text_lines),
        },
    }


statuses = read_statuses()
report = {"log_root": str(ROOT), "testcases": {}, "comparisons": {}}
for tc in range(135, 141):
    tc_report = report["testcases"].setdefault(str(tc), {})
    for profile in PROFILES:
        tc_report[profile] = summarize_case(
            tc, profile, statuses.get((tc, profile), "MISSING"))

    comparison = report["comparisons"].setdefault(str(tc), {})
    naive = tc_report["naive"]
    naive_latency = naive["latency"][0]["ns"]["mean"] if naive["latency"] else None
    naive_outer = (naive["outer_protocol"]["mean_ns"]
                   if naive["outer_protocol"] else None)
    naive_throughput = next((timer["throughput_ops_s"]
                             for timer in naive["guest_timers"]
                             if timer["phase"] == "mixed_batch_throughput"), None)
    for profile in ("spill-noopt", "spill-opt"):
        candidate = tc_report[profile]
        candidate_latency = (candidate["latency"][0]["ns"]["mean"]
                             if candidate["latency"] else None)
        candidate_outer = (candidate["outer_protocol"]["mean_ns"]
                           if candidate["outer_protocol"] else None)
        candidate_throughput = next((timer["throughput_ops_s"]
                                     for timer in candidate["guest_timers"]
                                     if timer["phase"] == "mixed_batch_throughput"), None)
        comparison[profile] = {
            "guest_mean_latency_change_pct": pct_change(candidate_latency, naive_latency),
            "outer_mean_latency_change_pct": pct_change(candidate_outer, naive_outer),
            "mixed_throughput_change_pct": pct_change(candidate_throughput, naive_throughput),
        }

print(json.dumps(report, indent=2, sort_keys=True))
