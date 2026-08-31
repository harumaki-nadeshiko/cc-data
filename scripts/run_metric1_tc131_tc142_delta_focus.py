#!/usr/bin/env python3
"""Run focused TC131/TC142 spill-vs-ideal delta pairs."""

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


TOPOLOGIES = ("3n1s", "3n2s")
TEST_CASES = (131, 142)
ROLES = ("spill", "ideal")
CPU_SLOTS = ("0-15", "16-31")


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def summarize_pair(root, topology, tc):
    roles = {}
    for role in ROLES:
        case = root / "cases" / topology / f"tc{tc}" / role
        result = json.loads((case / "result.json").read_text())
        roles[role] = {
            "run": result,
            "outer": matrix.extract_outer(case),
            "capacity": matrix.extract_capacity(case),
        }
    spill = roles["spill"]["outer"]["mean_ns"]
    ideal = roles["ideal"]["outer"]["mean_ns"]
    status = "PASS" if all(roles[role]["run"].get("status") == "PASS"
                           for role in ROLES) else "FAIL"
    return {
        "topology": topology,
        "tc": tc,
        "status": status,
        "pressure_pct": 175 if tc == 142 else None,
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
    args = parser.parse_args(argv)
    root = args.output_root.expanduser().resolve()
    try:
        root.relative_to(ROOT)
    except ValueError:
        parser.error("--output-root must be inside the workspace")
    root.mkdir(parents=True, exist_ok=True)

    matrix.PRESSURE_PCT = 175
    matrix.TARGET_TOTAL = matrix.NAIVE_CAPACITY * 175 // 100
    jobs = [(topology, tc, role)
            for tc in TEST_CASES for topology in TOPOLOGIES for role in ROLES]
    progress = {
        "phase": "matrix", "completed_runs": 0,
        "total_planned_runs": len(jobs), "pass": 0, "fail": 0,
        "running": {},
    }
    atomic_json(root / "progress.json", progress)

    def run(job, cpuset):
        topology, tc, role = job
        return matrix.run_case(root, topology, tc, role, cpuset=cpuset)

    pending = iter(jobs)
    futures = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for cpuset in CPU_SLOTS:
            try:
                job = next(pending)
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

                topology, tc, _ = job
                pair_paths = [root / "cases" / topology / f"tc{tc}" / role /
                              "result.json" for role in ROLES]
                if all(path.is_file() for path in pair_paths):
                    pair = summarize_pair(root, topology, tc)
                    atomic_json(root / "cases" / topology / f"tc{tc}" /
                                "delta_summary.json", pair)

                try:
                    next_job = next(pending)
                except StopIteration:
                    next_job = None
                if next_job is not None:
                    next_future = pool.submit(run, next_job, cpuset)
                    futures[next_future] = (next_job, cpuset)
                    progress["running"][cpuset] = "/".join(
                        map(str, next_job))
                atomic_json(root / "progress.json", progress)

    rows = []
    for tc in TEST_CASES:
        for topology in TOPOLOGIES:
            path = root / "cases" / topology / f"tc{tc}" / "delta_summary.json"
            if path.is_file():
                rows.append(json.loads(path.read_text()))
    deltas = [row["outer_delta_ns"] for row in rows
              if row.get("outer_delta_ns") is not None]
    summary = {
        "definition": "spill completed-Outer mean - ideal completed-Outer mean",
        "dsm_data_delay_ps": matrix.DSM_DATA_DELAY_PS,
        "rows": rows,
        "aggregate": {
            "samples": len(deltas),
            "mean_delta_ns": statistics.mean(deltas) if deltas else None,
            "median_delta_ns": statistics.median(deltas) if deltas else None,
        },
        "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    atomic_json(root / "delta_summary.json", summary)
    progress["phase"] = "done"
    atomic_json(root / "progress.json", progress)
    return 0 if progress["fail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
