#!/usr/bin/env python3
"""Summarize one-pair Metric3 L3-pressure qualification evidence."""

import argparse
import json
import pathlib
import re


STAT = re.compile(r"occupancy(\S+)\s+(\d+)")


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def stats_for(root, row):
    output = {}
    run = root / "build/runs" / row["run_id"] / f"tc{row['tc']}" / "m5out"
    for path in run.glob("node*/stats.txt"):
        for line in path.read_text(errors="replace").splitlines():
            match = STAT.search(line)
            if match:
                output[match.group(1)] = max(
                    output.get(match.group(1), 0), int(match.group(2)))
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=pathlib.Path, required=True)
    parser.add_argument("--baseline-report", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    repo = pathlib.Path(__file__).resolve().parents[1]
    rows = [json.loads(path.read_text())
            for path in args.evidence_root.glob("cases/**/result.json")]
    baseline = json.loads(args.baseline_report.read_text())
    base = {item["metric_id"]: item for item in baseline["metric_summaries"]}
    grouped = {}
    for row in rows:
        grouped.setdefault((row["pressure_level"], row["tc"]), {})[
            row["arm"]] = row
        row["l3_occupancy"] = stats_for(repo, row)
    comparisons = []
    for (level, tc), arms in sorted(grouped.items()):
        item = {"pressure_level": level, "tc": tc,
                "arm_status": {arm: row["status"] for arm, row in arms.items()},
                "metrics": []}
        if all(arm in arms and arms[arm].get("status") == "PASS"
               for arm in ("ourcc", "ha-vi")):
            for metric in sorted(arms["ourcc"]["metrics"]):
                metric_id = f"TC{tc}_{metric}"
                old = base[metric_id]
                ourcc = arms["ourcc"]["metrics"][metric]["ticks_per_operation"]
                havi = arms["ha-vi"]["metrics"][metric]["ticks_per_operation"]
                item["metrics"].append({
                    "metric": metric,
                    "ourcc_ticks": ourcc,
                    "ha_vi_ticks": havi,
                    "delta_ha_vi_minus_ourcc": havi - ourcc,
                    "ourcc_vs_baseline_pct":
                        (ourcc / old["ourcc_mean_ticks"] - 1.0) * 100.0,
                    "ha_vi_vs_baseline_pct":
                        (havi / old["ha_vi_mean_ticks"] - 1.0) * 100.0,
                })
        item["occupancy"] = {
            arm: row.get("l3_occupancy", {}) for arm, row in arms.items()
        }
        comparisons.append(item)
    payload = {"schema_version": 1, "evidence_root": str(args.evidence_root),
               "arms": len(rows),
               "pass": sum(row["status"] == "PASS" for row in rows),
               "fail": sum(row["status"] != "PASS" for row in rows),
               "comparisons": comparisons}
    atomic_json(args.output, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
