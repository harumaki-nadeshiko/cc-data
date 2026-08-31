#!/usr/bin/env python3
"""Run a resumable spill-vs-ideal Metric1 matrix with dynamic CPU slots."""

import argparse
import concurrent.futures
import datetime
import json
import pathlib
import statistics
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_metric1_portable_calibrated_matrix as matrix


ROLES = ("spill", "ideal")


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def summarize_pair(root, topology, tc, pressure_pct):
    roles = {}
    for role in ROLES:
        case = root / "cases" / topology / f"tc{tc}" / role
        roles[role] = {
            "run": json.loads((case / "result.json").read_text()),
            "outer": matrix.extract_outer(case),
            "capacity": matrix.extract_capacity(case),
        }
    spill = roles["spill"]["outer"]["mean_ns"]
    ideal = roles["ideal"]["outer"]["mean_ns"]
    return {
        "topology": topology, "tc": tc,
        "status": ("PASS" if all(roles[role]["run"].get("status") == "PASS"
                                 for role in ROLES) else "FAIL"),
        "pressure_pct": pressure_pct if tc in matrix.HOT else None,
        "roles": roles,
        "outer_delta_ns": (spill - ideal
                           if spill is not None and ideal is not None else None),
        "outer_delta_cycles_2ghz": ((spill - ideal) * 2
                                    if spill is not None and ideal is not None
                                    else None),
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", required=True, type=pathlib.Path)
    parser.add_argument("--topologies", nargs="+", required=True,
                        choices=sorted(matrix.TOPOLOGIES))
    parser.add_argument("--test-cases", nargs="+", required=True, type=int)
    parser.add_argument("--cpu-slots", nargs="+", required=True)
    parser.add_argument("--pressure-pct", type=int, default=175)
    args = parser.parse_args(argv)
    unknown = sorted(set(args.test_cases) - ({131} | set(matrix.HOT)))
    if unknown:
        parser.error(f"unsupported test cases: {unknown}")
    if args.pressure_pct <= 100 or matrix.NAIVE_CAPACITY * args.pressure_pct % 100:
        parser.error("--pressure-pct must produce an integral footprint above 100%")

    root = args.output_root.expanduser().resolve()
    try:
        root.relative_to(ROOT)
    except ValueError:
        parser.error("--output-root must be inside the workspace")
    root.mkdir(parents=True, exist_ok=True)
    matrix.PRESSURE_PCT = args.pressure_pct
    matrix.TARGET_TOTAL = matrix.NAIVE_CAPACITY * args.pressure_pct // 100

    jobs = [(topology, tc, role) for tc in args.test_cases
            for topology in args.topologies for role in ROLES]
    pending = []
    for job in jobs:
        path = root / "cases" / job[0] / f"tc{job[1]}" / job[2] / "result.json"
        if path.is_file() and json.loads(path.read_text()).get("status") == "PASS":
            continue
        pending.append(job)

    progress = {
        "phase": "matrix", "completed_runs": len(jobs) - len(pending),
        "total_planned_runs": len(jobs), "pass": len(jobs) - len(pending),
        "fail": 0, "running": {}, "topologies": args.topologies,
        "test_cases": args.test_cases, "cpu_slots": args.cpu_slots,
        "pressure_pct": args.pressure_pct,
    }
    atomic_json(root / "progress.json", progress)
    pending_iter = iter(pending)

    def run(job, cpuset):
        return matrix.run_case(root, job[0], job[1], job[2], cpuset=cpuset)

    futures = {}
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(args.cpu_slots)) as pool:
        for cpuset in args.cpu_slots:
            try:
                job = next(pending_iter)
            except StopIteration:
                break
            future = pool.submit(run, job, cpuset)
            futures[future] = (job, cpuset)
            progress["running"][cpuset] = "/".join(map(str, job))
        atomic_json(root / "progress.json", progress)

        while futures:
            done, _ = concurrent.futures.wait(
                futures, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                job, cpuset = futures.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    result = {"topology": job[0], "tc": job[1],
                              "role": job[2], "status": "FAIL",
                              "error": repr(exc)}
                progress["completed_runs"] += 1
                progress["pass" if result.get("status") == "PASS" else
                         "fail"] += 1
                progress["last_result"] = result
                progress["running"].pop(cpuset, None)
                paths = [root / "cases" / job[0] / f"tc{job[1]}" / role /
                         "result.json" for role in ROLES]
                if all(path.is_file() for path in paths):
                    pair = summarize_pair(root, job[0], job[1],
                                          args.pressure_pct)
                    atomic_json(root / "cases" / job[0] / f"tc{job[1]}" /
                                "delta_summary.json", pair)
                try:
                    next_job = next(pending_iter)
                except StopIteration:
                    next_job = None
                if next_job:
                    next_future = pool.submit(run, next_job, cpuset)
                    futures[next_future] = (next_job, cpuset)
                    progress["running"][cpuset] = "/".join(
                        map(str, next_job))
                atomic_json(root / "progress.json", progress)

    rows = []
    for tc in args.test_cases:
        for topology in args.topologies:
            path = root / "cases" / topology / f"tc{tc}" / "delta_summary.json"
            if path.is_file():
                rows.append(json.loads(path.read_text()))
    deltas = [row["outer_delta_ns"] for row in rows
              if row.get("outer_delta_ns") is not None]
    atomic_json(root / "delta_summary.json", {
        "definition": "spill completed-Outer mean - ideal completed-Outer mean",
        "dsm_data_delay_ps": matrix.DSM_DATA_DELAY_PS,
        "rows": rows,
        "aggregate": {
            "samples": len(deltas),
            "mean_delta_ns": statistics.mean(deltas) if deltas else None,
            "median_delta_ns": statistics.median(deltas) if deltas else None,
        },
        "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })
    progress["phase"] = "done"
    atomic_json(root / "progress.json", progress)
    return 0 if progress["fail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
