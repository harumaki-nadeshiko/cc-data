#!/usr/bin/env python3
"""Summarize guest-visible timer markers from the TC90+ profile matrix."""
import json
import pathlib
import re
import statistics
import sys

ROOT = pathlib.Path(sys.argv[1])
RE = re.compile(
    r"\[GUEST-TIMER\] node=(\d+) phase=(\S+) operations=(\d+) "
    r"counter_ticks=(\d+) counter_frequency_hz=(\d+) "
    r"source=arm_cntvct_el0 unit=counter_ticks")

data = {}
for profile_dir in ROOT.iterdir() if ROOT.exists() else []:
    if not profile_dir.is_dir() or profile_dir.name not in {"naive", "spill-noopt", "spill-opt"}:
        continue
    for tc_dir in profile_dir.glob("tc*"):
        try:
            tc = int(tc_dir.name[2:])
        except ValueError:
            continue
        phases = data.setdefault(str(tc), {}).setdefault(profile_dir.name, {})
        for log in tc_dir.glob("simout_tc*_node*.log"):
            for line in log.read_text(errors="replace").splitlines():
                match = RE.search(line)
                if not match or match.group(2) == "timer_selftest":
                    continue
                phase = match.group(2)
                phases.setdefault(phase, []).append({
                    "node": int(match.group(1)),
                    "operations": int(match.group(3)),
                    "ticks": int(match.group(4)),
                    "frequency_hz": int(match.group(5)),
                })

report = {}
for tc, profiles in sorted(data.items(), key=lambda item: int(item[0])):
    tc_report = report.setdefault(tc, {})
    all_phases = sorted({phase for profile in profiles.values() for phase in profile})
    for phase in all_phases:
        phase_report = tc_report.setdefault(phase, {})
        for profile, phase_map in profiles.items():
            samples = phase_map.get(phase, [])
            if not samples:
                continue
            ticks = [sample["ticks"] for sample in samples]
            operations = samples[0]["operations"]
            mean_ticks = statistics.mean(ticks)
            phase_report[profile] = {
                "samples": len(samples),
                "operations": operations,
                "mean_counter_ticks": mean_ticks,
                "mean_ticks_per_operation": mean_ticks / operations,
                "frequency_hz": samples[0]["frequency_hz"],
            }
        naive = phase_report.get("naive")
        for profile in ("spill-noopt", "spill-opt"):
            candidate = phase_report.get(profile)
            if not naive or not candidate:
                continue
            ratio = (candidate["mean_ticks_per_operation"] /
                     naive["mean_ticks_per_operation"])
            candidate["vs_naive_latency_change_pct"] = (ratio - 1.0) * 100.0
            candidate["vs_naive_throughput_change_pct"] = ((1.0 / ratio) - 1.0) * 100.0
print(json.dumps(report, indent=2, sort_keys=True))
