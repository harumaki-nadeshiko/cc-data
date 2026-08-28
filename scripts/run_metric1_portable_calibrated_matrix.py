#!/usr/bin/env python3
"""Run resumable Metric1 portable calibration, then gated TC143-TC147 rows."""

import argparse
import datetime
import json
import math
import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
IMAGE = "ubcc-dev:ubuntu20.04"
TARGET_TOTAL = 163840
NAIVE_CAPACITY = 65536
PRESSURE_PCT = 250
HOT = {142: 32, 143: 137, 144: 192, 145: 136, 146: 192, 147: 136}
TOPOLOGIES = {
    "2n1s": ("--2n1s", 2, 1, 4, "8g", 7200),
    "3n1s": ("--3n1s", 3, 1, 6, "12g", 7200),
    "3n2s": ("--3n2s", 3, 2, 12, "24g", 10800),
    "8n1s": ("--8n1s", 8, 1, 16, "32g", 10800),
    "8n2s": ("--8n2s", 8, 2, 32, "64g", 21600),
}
ROLES = ("naive", "spill", "ideal")


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def run_case(root, topology, tc, role):
    flag, nodes, sockets, cpus, memory, timeout = TOPOLOGIES[topology]
    case = root / "cases" / topology / f"tc{tc}" / role
    result_path = case / "result.json"
    if result_path.is_file():
        result = json.loads(result_path.read_text())
        if result.get("status") == "PASS":
            return result
    case.mkdir(parents=True, exist_ok=True)
    pressure = TARGET_TOTAL - HOT[tc] * nodes * sockets
    profile = "naive" if role == "naive" else "spill-noopt"
    policy = "naive" if role == "naive" else "spill"
    opts = "--dir-overflow-policy=" + policy
    if role == "ideal":
        opts = ("--dir-overflow-policy=spill --bloom-bytes=61440 "
                "--sram-bytes=2097152 --ways=32 --set-bits=0 "
                "--allow-oversized-resident-dir-for-test --batch-rs=0")
    run_id = f"m1cal_{topology}_tc{tc}_{role}"
    macros = (f"-DPORTABLE_PRESSURE_LINES={pressure} "
              f"-DPORTABLE_TARGET_FOOTPRINT_LINES={TARGET_TOTAL} "
              f"-DPORTABLE_NAIVE_CAPACITY_LINES={NAIVE_CAPACITY} "
              f"-DPORTABLE_PRESSURE_LEVEL_PCT={PRESSURE_PCT} "
              "-DPORTABLE_BATCHES=32")
    command = [
        "docker", "run", "--rm", "--name", run_id,
        "--network", "none", "--cpus", str(cpus), "--memory", memory,
        "-v", f"{ROOT}:/workspace", "-w", "/workspace", IMAGE, "env",
        f"E2E_RUN_ID={run_id}",
        f"LOG_BASE=/workspace/{case.relative_to(ROOT)}",
        f"TIMEOUT_SEC={timeout}", "EP_SUPERVISOR=1",
        "EP_SUPERVISOR_PROGRESS_STALL_SEC=1800", "EP_CPU_MODEL=o3",
        "EP_SEQUENCER_MAX_OUTSTANDING=16", "EP_TRACE_PERF=off",
        "EP_HA_PROFILE=ubcc", "OURCC_CLEAR_PROFILE=ack",
        f"EP_PERF_PROFILE={profile}", f"UBCC_POLICY={policy}",
        f"UBCC_OPTS={opts}",
        "EP_GEM5_OPTS=--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0",
        "PORTABLE_512K_DIR=1", f"WORKLOAD_CFLAGS={macros}",
        "bash", "tests/e2e/run_multi.sh", flag, str(tc),
    ]
    with (case / "coordinator.log").open("w") as stream:
        return_code = subprocess.run(command, stdout=stream,
                                     stderr=subprocess.STDOUT).returncode
    verifier = case / f"verify_tc{tc}.log"
    verifier_pass = verifier.is_file() and verifier.read_text(
        errors="replace").rstrip().endswith(f">>> TC{tc} PASSED <<<")
    exits = list((case / f"child_status_tc{tc}").glob("*.exit"))
    expected = nodes + nodes * sockets + 1
    child_pass = len(exits) == expected and all(
        path.read_text().strip() == "0" for path in exits)
    result = {"topology": topology, "tc": tc, "role": role,
              "pressure_lines": pressure, "target_total": TARGET_TOTAL,
              "status": "PASS" if return_code == 0 and verifier_pass and child_pass else "FAIL",
              "return_code": return_code, "verifier_pass": verifier_pass,
              "child_count": len(exits), "expected_child_count": expected,
              "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    atomic_json(result_path, result)
    return result


def extract_capacity(case):
    sys.path.insert(0, str(ROOT / "scripts"))
    import extract_metric123_from_logs as extractor
    logs = [path for path in case.glob("ubio_tc*_n0_s0/stdout.log*")]
    return extractor.parse_capacity(logs)


def gate_coordinate(root, topology, tc):
    data = {role: extract_capacity(root / "cases" / topology / f"tc{tc}" / role)
            for role in ROLES}
    ratio = data["spill"]["effective_unique"] / data["naive"]["effective_unique"]
    return {"topology": topology, "tc": tc, "capacity_ratio": ratio,
            "pass": ratio >= 1.5, "capacity": data,
            "recommended_next_target": (None if ratio >= 1.5 else
                math.ceil(TARGET_TOTAL * 1.5 / ratio))}


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
    progress = {"phase": "calibration", "completed_runs": 0,
                "total_planned_runs": 90, "gates": []}
    atomic_json(root / "progress.json", progress)
    passing = []
    for topology in TOPOLOGIES:
        rows = [run_case(root, topology, 142, role) for role in ROLES]
        if all(row["status"] == "PASS" for row in rows):
            gate = gate_coordinate(root, topology, 142)
        else:
            gate = {"topology": topology, "tc": 142, "pass": False,
                    "reason": "run failure"}
        progress["gates"].append(gate)
        progress["completed_runs"] += 3
        atomic_json(root / "progress.json", progress)
        if gate.get("pass"):
            passing.append(topology)
    progress["phase"] = "matrix"
    progress["passing_topologies"] = passing
    atomic_json(root / "progress.json", progress)
    for topology in passing:
        for tc in range(143, 148):
            for role in ROLES:
                run_case(root, topology, tc, role)
                progress["completed_runs"] += 1
                atomic_json(root / "progress.json", progress)
            gate = gate_coordinate(root, topology, tc)
            progress["gates"].append(gate)
            atomic_json(root / "progress.json", progress)
    progress["phase"] = "done"
    atomic_json(root / "progress.json", progress)
    return 0 if all(gate.get("pass") for gate in progress["gates"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
