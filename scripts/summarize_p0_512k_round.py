#!/usr/bin/env python3
"""Create a compact P0 round summary suitable for ntfy."""

import json
import pathlib
import re
import sys


root = pathlib.Path(sys.argv[1])
round_name = sys.argv[2]
wall_clock = int(sys.argv[3])
timer_re = re.compile(
    r"\[GUEST-TIMER\] node=(\d+) phase=(\S+) operations=(\d+) "
    r"counter_ticks=(\d+) counter_frequency_hz=(\d+)")
phase_by_tc = {
    131: "post_pressure_catalog_reuse",
    132: "post_pressure_checkpoint_recover",
    133: "post_pressure_frontier_reuse",
    134: "post_pressure_window_reuse",
    142: "db_oltp_service",
    143: "db_btree_service",
    144: "db_wal_service",
    145: "faas_service",
    146: "graph_service",
    147: "feature_service",
}


results = []
metrics = {}
for result_path in sorted((root / "cases").glob("*/result.json")):
    result = json.loads(result_path.read_text())
    results.append(result)
    tc = int(result["tc"])
    phase = phase_by_tc.get(tc)
    if result.get("status") != "PASS" or not phase:
        continue
    values = []
    case_dir = pathlib.Path(result["log_dir"])
    for simout in case_dir.glob(f"simout_tc{tc}_node*.log"):
        for line in simout.read_text(errors="replace").splitlines():
            match = timer_re.search(line)
            if not match or match.group(2) != phase:
                continue
            operations = int(match.group(3))
            ticks = int(match.group(4))
            frequency = int(match.group(5))
            if operations > 0 and frequency > 0:
                values.append(ticks * 1.0e9 / frequency / operations)
    if values:
        key = (tc, result["topology"], int(result.get("level", 0)))
        metrics.setdefault(key, {})[result["profile"]] = sum(values) / len(values)


passed = sum(result.get("status") == "PASS" for result in results)
failed = len(results) - passed
lines = [
    f"{round_name} complete: {passed}/{len(results)} PASS, {failed} FAIL, "
    f"wall={wall_clock}s",
]
for key in sorted(metrics):
    profiles = metrics[key]
    baseline = profiles.get("naive")
    tc, topology, level = key
    label = f"TC{tc}/{topology}" + (f"/{level}%" if level else "")
    pieces = []
    for profile in ("spill-noopt", "optimized"):
        value = profiles.get(profile)
        if baseline and value is not None:
            pieces.append(f"{profile}={(value / baseline - 1.0) * 100:+.1f}%")
    if pieces:
        lines.append(label + " " + " ".join(pieces))
for result in results:
    if result.get("status") != "PASS":
        lines.append(f"FAIL {result['case_key']}: {result.get('reason', 'unknown')}")

message = "\n".join(lines)
print(message)
(root / "round_summary.txt").write_text(message + "\n")
