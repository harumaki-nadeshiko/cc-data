#!/usr/bin/env python3
"""Run a resumable Metric1 portable workload matrix."""

import argparse
import datetime
import json
import pathlib
import re
import statistics
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
IMAGE = "ubcc-dev:ubuntu20.04"
TARGET_TOTAL = 131072
NAIVE_CAPACITY = 65536
PRESSURE_PCT = 200
HOT = {142: 32, 143: 137, 144: 192, 145: 136, 146: 192, 147: 136}
TOPOLOGIES = {
    "2n1s": ("--2n1s", 2, 1, 4, "8g", 21600),
    "3n1s": ("--3n1s", 3, 1, 6, "12g", 21600),
    "3n2s": ("--3n2s", 3, 2, 12, "24g", 21600),
    "8n1s": ("--8n1s", 8, 1, 16, "32g", 10800),
    "8n2s": ("--8n2s", 8, 2, 32, "64g", 21600),
    "16n1s": ("--16n1s", 16, 1, 32, "64g", 21600),
}
ROLES = ("naive", "spill", "ideal")
TEST_CASES = tuple(range(142, 148))
OUTER_NODE_RE = re.compile(
    r"\[EP-PERF\] kind=outer node=(\d+).*?latency_ps=(\d+)")
DSM_DATA_DELAY_PS = 68000


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def run_case(root, topology, tc, role, cpuset=None):
    flag, nodes, sockets, cpus, memory, timeout = TOPOLOGIES[topology]
    case = root / "cases" / topology / f"tc{tc}" / role
    result_path = case / "result.json"
    if result_path.is_file():
        result = json.loads(result_path.read_text())
        if result.get("status") == "PASS":
            return result
    case.mkdir(parents=True, exist_ok=True)
    portable = tc in HOT
    pressure = TARGET_TOTAL - HOT[tc] * nodes * sockets if portable else None
    profile = "naive" if role == "naive" else "spill-noopt"
    policy = "naive" if role == "naive" else "spill"
    opts = "--dir-overflow-policy=" + policy
    if role == "ideal":
        opts = ("--dir-overflow-policy=spill --bloom-bytes=61440 "
                "--sram-bytes=2097152 --ways=32 --set-bits=0 "
                "--allow-oversized-resident-dir-for-test --batch-rs=0")
    run_id = f"m1d68p{PRESSURE_PCT}_{topology}_tc{tc}_{role}"
    macros = (f"-DPORTABLE_PRESSURE_LINES={pressure} "
              f"-DPORTABLE_TARGET_FOOTPRINT_LINES={TARGET_TOTAL} "
              f"-DPORTABLE_NAIVE_CAPACITY_LINES={NAIVE_CAPACITY} "
              f"-DPORTABLE_PRESSURE_LEVEL_PCT={PRESSURE_PCT} "
              "-DPORTABLE_BATCHES=32") if portable else None
    command = [
        "docker", "run", "--rm", "--name", run_id,
        "--network", "none",
    ]
    if cpuset:
        command.extend(["--cpuset-cpus", cpuset])
    else:
        command.extend(["--cpus", str(cpus)])
    command.extend([
        "--memory", memory,
        "-v", f"{ROOT}:/workspace", "-w", "/workspace", IMAGE, "env",
        f"E2E_RUN_ID={run_id}",
        f"EP_DOCKER_CPUSET={cpuset or 'quota'}",
        f"LOG_BASE=/workspace/{case.relative_to(ROOT)}",
        f"TIMEOUT_SEC={timeout}", "EP_SUPERVISOR=1",
        "EP_SUPERVISOR_PROGRESS_STALL_SEC=1800", "EP_CPU_MODEL=o3",
        "EP_SEQUENCER_MAX_OUTSTANDING=16", "EP_TRACE_PERF=off",
        f"EP_DSM_DATA_DELAY_PS={DSM_DATA_DELAY_PS}",
        "EP_HA_PROFILE=ubcc", "OURCC_CLEAR_PROFILE=ack",
        f"EP_PERF_PROFILE={profile}", f"UBCC_POLICY={policy}",
        f"UBCC_OPTS={opts}",
        "EP_GEM5_OPTS=--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0",
    ])
    if portable:
        command.extend(["PORTABLE_512K_DIR=1", f"WORKLOAD_CFLAGS={macros}"])
    command.extend(["bash", "tests/e2e/run_multi.sh", flag, str(tc)])
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
    ubio_manifests = []
    for path in case.glob("ubio_tc*_n*_s*/stdout.log"):
        first = path.read_text(errors="replace").splitlines()[:1]
        if first and "[PROCESS-MANIFEST]" in first[0]:
            ubio_manifests.append(json.loads(
                first[0].split("[PROCESS-MANIFEST]", 1)[1].strip()))
    delay_pass = (len(ubio_manifests) == nodes * sockets and all(
        item.get("dsm_data_delay_ps") == DSM_DATA_DELAY_PS
        for item in ubio_manifests))
    result = {"topology": topology, "tc": tc, "role": role,
              "pressure_lines": pressure, "target_total": TARGET_TOTAL,
              "pressure_pct": PRESSURE_PCT if portable else None,
              "dsm_data_delay_ps": DSM_DATA_DELAY_PS,
              "delay_manifest_pass": delay_pass,
              "status": "PASS" if return_code == 0 and verifier_pass and child_pass and delay_pass else "FAIL",
              "return_code": return_code, "verifier_pass": verifier_pass,
              "child_count": len(exits), "expected_child_count": expected,
              "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    atomic_json(result_path, result)
    return result


def extract_capacity(case):
    sys.path.insert(0, str(ROOT / "scripts"))
    import extract_metric123_from_logs as extractor
    logs = [path for path in case.glob("ubio_tc*_n0_s0/stdout.log*")]
    capacity = extractor.parse_capacity(logs)
    capacity.pop("sources", None)
    return capacity


def extract_outer(case):
    values = []
    by_node = {}
    for path in sorted(case.glob("gem5_tc*_node*/stderr.log")):
        for line in path.read_text(errors="replace").splitlines():
            match = OUTER_NODE_RE.search(line)
            if not match:
                continue
            node = int(match.group(1))
            value = int(match.group(2)) / 1000.0
            values.append(value)
            by_node.setdefault(node, []).append(value)

    def stats(samples):
        ordered = sorted(samples)
        if not ordered:
            return {"samples": 0, "mean_ns": None, "p50_ns": None,
                    "p95_ns": None, "p99_ns": None, "max_ns": None}
        percentile = lambda q: ordered[int(q * (len(ordered) - 1))]
        return {"samples": len(ordered), "mean_ns": statistics.mean(ordered),
                "p50_ns": statistics.median(ordered),
                "p95_ns": percentile(0.95), "p99_ns": percentile(0.99),
                "max_ns": ordered[-1]}

    result = stats(values)
    result["by_requester_node"] = {
        str(node): stats(samples) for node, samples in sorted(by_node.items())}
    return result


def summarize_coordinate(root, topology, tc):
    cases = {role: root / "cases" / topology / f"tc{tc}" / role
             for role in ROLES}
    results = {role: json.loads((case / "result.json").read_text())
               for role, case in cases.items()}
    if not all(result.get("status") == "PASS" for result in results.values()):
        return {"topology": topology, "tc": tc, "status": "FAIL",
                "runs": results}
    capacity = {role: extract_capacity(case) for role, case in cases.items()}
    outer = {role: extract_outer(case) for role, case in cases.items()}
    naive_lines = capacity["naive"]["effective_unique"]
    spill_lines = capacity["spill"]["effective_unique"]
    spill_mean = outer["spill"]["mean_ns"]
    ideal_mean = outer["ideal"]["mean_ns"]
    return {
        "topology": topology, "tc": tc, "status": "PASS",
        "pressure_pct": results["naive"].get("pressure_pct"),
        "pressure_lines": results["naive"]["pressure_lines"],
        "capacity": capacity,
        "outer": outer,
        "capacity_ratio": spill_lines / naive_lines if naive_lines else None,
        "capacity_gain_pct": ((spill_lines / naive_lines - 1.0) * 100.0
                              if naive_lines else None),
        "outer_delta_ns": (spill_mean - ideal_mean
                           if spill_mean is not None and ideal_mean is not None
                           else None),
        "outer_delta_cycles_2ghz": ((spill_mean - ideal_mean) * 2.0
                                    if spill_mean is not None and
                                    ideal_mean is not None else None),
    }


def coordinate_summary_path(root, topology, tc):
    return root / "cases" / topology / f"tc{tc}" / "coordinate_summary.json"


def write_coordinate_summary(root, topology, tc):
    summary = summarize_coordinate(root, topology, tc)
    atomic_json(coordinate_summary_path(root, topology, tc), summary)
    return summary


def refresh_summary(root, rebuild_coordinates=False):
    rows = []
    for topology in TOPOLOGIES:
        for tc in TEST_CASES:
            result_paths = [root / "cases" / topology / f"tc{tc}" / role /
                            "result.json" for role in ROLES]
            if not all(path.is_file() for path in result_paths):
                continue
            path = coordinate_summary_path(root, topology, tc)
            if rebuild_coordinates or not path.is_file():
                rows.append(write_coordinate_summary(root, topology, tc))
            else:
                rows.append(json.loads(path.read_text()))
    summary = {
        "definition": {
            "capacity_ratio": "spill effective_unique / naive effective_unique",
            "outer_delta_ns": "spill completed-Outer mean - ideal completed-Outer mean",
        },
        "pressure_pct": PRESSURE_PCT,
        "target_total_lines": TARGET_TOTAL,
        "coordinates": rows,
    }
    atomic_json(root / "metric1_summary.json", summary)
    return summary


def main(argv=None):
    global PRESSURE_PCT, TARGET_TOTAL, TEST_CASES
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", required=True, type=pathlib.Path)
    parser.add_argument("--pressure-pct", type=int, default=PRESSURE_PCT)
    parser.add_argument("--test-cases", type=int, nargs="+",
                        default=list(TEST_CASES))
    args = parser.parse_args(argv)
    if args.pressure_pct <= 100:
        parser.error("--pressure-pct must be greater than 100")
    if NAIVE_CAPACITY * args.pressure_pct % 100:
        parser.error("--pressure-pct must produce an integral target footprint")
    unknown = sorted(set(args.test_cases) - set(HOT))
    if unknown:
        parser.error(f"unsupported portable test cases: {unknown}")
    PRESSURE_PCT = args.pressure_pct
    TARGET_TOTAL = NAIVE_CAPACITY * PRESSURE_PCT // 100
    TEST_CASES = tuple(dict.fromkeys(args.test_cases))
    root = args.output_root.expanduser().resolve()
    try:
        root.relative_to(ROOT)
    except ValueError:
        parser.error("--output-root must be inside the workspace")
    root.mkdir(parents=True, exist_ok=True)
    progress_path = root / "progress.json"
    completed = sum(1 for topology in TOPOLOGIES for tc in TEST_CASES
                    for role in ROLES
                    if (root / "cases" / topology / f"tc{tc}" / role /
                        "result.json").is_file())
    progress = {"phase": "matrix", "completed_runs": completed,
                "total_planned_runs": (len(TOPOLOGIES) * len(TEST_CASES) *
                                       len(ROLES)),
                "pressure_pct": PRESSURE_PCT,
                "topologies": list(TOPOLOGIES),
                "test_cases": list(TEST_CASES),
                "roles": list(ROLES)}
    atomic_json(root / "progress.json", progress)
    for topology in TOPOLOGIES:
        for tc in TEST_CASES:
            result_paths = [root / "cases" / topology / f"tc{tc}" / role /
                            "result.json" for role in ROLES]
            coordinate_path = coordinate_summary_path(root, topology, tc)
            if coordinate_path.is_file() and all(path.is_file()
                                                 for path in result_paths):
                results = [json.loads(path.read_text()) for path in result_paths]
                if all(result.get("status") == "PASS" for result in results):
                    continue
            for role in ROLES:
                result = run_case(root, topology, tc, role)
                progress["completed_runs"] = sum(
                    1 for candidate in root.glob(
                        "cases/*/tc*/*/result.json") if candidate.is_file())
                progress["last_result"] = result
                atomic_json(root / "progress.json", progress)
            write_coordinate_summary(root, topology, tc)
            refresh_summary(root)
    progress["phase"] = "done"
    atomic_json(root / "progress.json", progress)
    summary = refresh_summary(root)
    return 0 if all(row.get("status") == "PASS"
                    for row in summary["coordinates"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
