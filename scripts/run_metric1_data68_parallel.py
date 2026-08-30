#!/usr/bin/env python3
"""Run the TC131/TC142-TC147 Metric1 matrix on four dynamic CPU slots."""

import argparse
import datetime
import hashlib
import json
import pathlib
import queue
import sys
import threading


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_metric1_portable_calibrated_matrix as matrix


TOPOLOGIES = ("2n1s", "3n1s")
TEST_CASES = (131, 142, 143, 144, 145, 146, 147)
ROLES = matrix.ROLES
CPU_SLOTS = ("0-7", "8-15", "16-23", "24-31")


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", required=True, type=pathlib.Path)
    parser.add_argument("--pressure-pct", type=int, default=175)
    args = parser.parse_args(argv)
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
    matrix.TEST_CASES = TEST_CASES
    matrix.TOPOLOGIES = {name: matrix.TOPOLOGIES[name] for name in TOPOLOGIES}

    jobs = [(topology, tc, role)
            for topology in TOPOLOGIES
            for tc in TEST_CASES
            for role in ROLES]
    pending = queue.Queue()
    completed = queue.Queue()
    for job in jobs:
        result_path = (root / "cases" / job[0] / f"tc{job[1]}" / job[2] /
                       "result.json")
        if result_path.is_file():
            result = json.loads(result_path.read_text())
            if result.get("status") == "PASS":
                continue
        pending.put(job)

    manifest = {
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "pressure_pct": args.pressure_pct,
        "portable_target_total_lines": matrix.TARGET_TOTAL,
        "dsm_data_delay_ps": matrix.DSM_DATA_DELAY_PS,
        "topologies": list(TOPOLOGIES),
        "test_cases": list(TEST_CASES),
        "roles": list(ROLES),
        "cpu_slots": list(CPU_SLOTS),
        "total_planned_runs": len(jobs),
        "binaries": {
            "gem5": sha256(ROOT / "gem5/build/ARM/gem5.opt"),
            "ubio": sha256(ROOT / "build/bin/ubio"),
            "networksim": sha256(ROOT / "build/bin/networksim"),
        },
    }
    atomic_json(root / "matrix_manifest.json", manifest)

    lock = threading.Lock()
    state = {
        "phase": "matrix",
        "completed_runs": len(jobs) - pending.qsize(),
        "total_planned_runs": len(jobs),
        "running": {},
        "pass": 0,
        "fail": 0,
    }

    def publish():
        with lock:
            atomic_json(root / "progress.json", state)

    def worker(slot, cpuset):
        while True:
            try:
                topology, tc, role = pending.get_nowait()
            except queue.Empty:
                return
            job_name = f"{topology}/tc{tc}/{role}"
            with lock:
                state["running"][slot] = {
                    "job": job_name, "cpuset": cpuset,
                    "started_at": datetime.datetime.now(
                        datetime.timezone.utc).isoformat(),
                }
                atomic_json(root / "progress.json", state)
            try:
                result = matrix.run_case(root, topology, tc, role, cpuset=cpuset)
            except Exception as exc:
                result = {"topology": topology, "tc": tc, "role": role,
                          "status": "FAIL", "error": repr(exc)}
            completed.put((slot, result))
            pending.task_done()

    threads = [threading.Thread(target=worker, args=(f"slot{i}", cpuset),
                                daemon=False)
               for i, cpuset in enumerate(CPU_SLOTS)]
    for thread in threads:
        thread.start()

    remaining_workers = len(threads)
    while remaining_workers:
        try:
            slot, result = completed.get(timeout=1)
        except queue.Empty:
            remaining_workers = sum(thread.is_alive() for thread in threads)
            continue
        with lock:
            state["running"].pop(slot, None)
            state["completed_runs"] += 1
            state["pass" if result.get("status") == "PASS" else "fail"] += 1
            state["last_result"] = result
            atomic_json(root / "progress.json", state)
        topology, tc = result.get("topology"), result.get("tc")
        if topology in TOPOLOGIES and tc in TEST_CASES:
            paths = [root / "cases" / topology / f"tc{tc}" / role /
                     "result.json" for role in ROLES]
            if all(path.is_file() for path in paths):
                matrix.write_coordinate_summary(root, topology, tc)
                matrix.refresh_summary(root)
        completed.task_done()

    for thread in threads:
        thread.join()
    with lock:
        state["phase"] = "done"
        atomic_json(root / "progress.json", state)
    matrix.refresh_summary(root)
    return 0 if state["fail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
