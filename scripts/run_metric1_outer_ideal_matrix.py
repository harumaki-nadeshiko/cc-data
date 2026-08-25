#!/usr/bin/env python3
"""Run resumable TC131 spill-vs-ideal completed-Outer latency pairs."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import statistics
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]
IMAGE = "ubcc-dev:ubuntu20.04"
OUTER_RE = re.compile(r"\[EP-PERF\] kind=outer .*?latency_ps=(\d+)")
CAPACITY_RE = re.compile(r"resident_capacity=(\d+) entries")
FILL_RE = re.compile(r"\[RESIDENT-FILL-DONE\].*?found=(\d+)")
H64_RE = re.compile(r'"h64ExactLiveKnown":(\d+),"h64ExactLiveCount":(\d+)')


def atomic_json(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temp.replace(path)


def analyze(log_dir, label):
    values = []
    for path in sorted(log_dir.glob("gem5_tc131_node*/stderr.log")):
        for line in path.read_text(errors="replace").splitlines():
            match = OUTER_RE.search(line)
            if match:
                values.append(int(match.group(1)))
    if not values:
        raise RuntimeError(f"{label}: no completed Outer samples")
    values.sort()
    capacity = None
    found_fills = 0
    exact_live = []
    for path in sorted(log_dir.glob("ubio_tc131_n*_s0/stdout.log")):
        text = path.read_text(errors="replace")
        match = CAPACITY_RE.search(text)
        if match and path.name == "stdout.log" and "_n0_s0" in str(path.parent):
            capacity = int(match.group(1))
        found_fills += sum(int(value) for value in FILL_RE.findall(text))
        exact_live.extend(int(count) for known, count in H64_RE.findall(text)
                          if int(known) == 1)
    if capacity is None:
        raise RuntimeError(f"{label}: no home capacity marker")
    count = len(values)
    percentile = lambda q: values[int(q * (count - 1))] / 1000.0
    return {
        "outer_samples": count,
        "outer_mean_ns": statistics.mean(values) / 1000.0,
        "outer_p50_ns": statistics.median(values) / 1000.0,
        "outer_p95_ns": percentile(0.95),
        "outer_p99_ns": percentile(0.99),
        "outer_max_ns": max(values) / 1000.0,
        "resident_capacity": capacity,
        "backstore_found_fills": found_fills,
        "h64_exact_live_max": max(exact_live or [0]),
    }


def run_case(root, repeat, arm, timeout):
    case_dir = root / "cases" / f"r{repeat:02d}" / arm
    result_path = case_dir / "result.json"
    if result_path.is_file():
        previous = json.loads(result_path.read_text())
        if previous.get("status") == "PASS":
            return previous
    case_dir.mkdir(parents=True, exist_ok=True)
    ideal = arm == "ideal"
    opts = "--dir-overflow-policy=spill"
    if ideal:
        opts += (" --bloom-bytes=61440 --sram-bytes=2097152 --ways=32 "
                 "--set-bits=0 --allow-oversized-resident-dir-for-test")
    run_id = "m1ideal_" + hashlib.sha256(
        f"{root.name}:r{repeat}:{arm}".encode()).hexdigest()[:16]
    env = [
        f"E2E_RUN_ID={run_id}",
        f"LOG_BASE=/workspace/{case_dir.relative_to(ROOT)}",
        f"TIMEOUT_SEC={timeout}", "STALL_TIMEOUT_SEC=1800",
        "EP_CPU_MODEL=o3", "EP_SEQUENCER_MAX_OUTSTANDING=16",
        "EP_TRACE_PERF=full", "EP_PERF_PROFILE=spill-noopt",
        "UBCC_POLICY=spill", f"UBCC_OPTS={opts}",
        "EP_GEM5_OPTS=--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0",
        "EP_HA_PROFILE=ubcc", "OURCC_CLEAR_PROFILE=ack",
        "HA_EXACT_BYTES=134217728", "HA_MAX_ACTIVE=256", "HA_MAX_QUEUE=8",
        "EP_LINK_LATENCY_PS=2500", "EP_SYNC_INTERVAL_PS=2500",
        "EP_PORT_HWM=8192", "EP_NSIM_MAX_PENDING=65536",
        "LD_LIBRARY_PATH=/workspace/thirdparty/zeromq/lib",
    ]
    command = [
        "docker", "run", "--rm", "--network", "none", "--cpuset-cpus=0-31",
        "-v", f"{ROOT}:/workspace",
        "-v", f"{ROOT / 'gem5/gem5'}:/workspace/gem5",
        "-v", "/mnt/data2/cgc/.local/lib:/workspace/thirdparty/zeromq/lib:ro",
        "-w", "/workspace", IMAGE, "env", *env,
        "bash", "tests/e2e/run_multi.sh", "--8n1s", "131",
    ]
    started = time.time()
    with (case_dir / "coordinator.log").open("w") as stream:
        return_code = subprocess.run(
            command, stdout=stream, stderr=subprocess.STDOUT).returncode
    verifier = case_dir / "verify_tc131.log"
    verifier_pass = verifier.is_file() and verifier.read_text(
        errors="replace").rstrip().endswith(">>> TC131 PASSED <<<")
    row = {
        "repeat": repeat, "arm": arm, "run_id": run_id,
        "return_code": return_code, "verifier_pass": verifier_pass,
        "elapsed_seconds": time.time() - started,
        "status": "PASS" if return_code == 0 and verifier_pass else "FAIL",
        "log_dir": str(case_dir.relative_to(ROOT)),
    }
    if row["status"] == "PASS":
        try:
            row["analysis"] = analyze(case_dir, f"r{repeat} {arm}")
            if ideal and (row["analysis"]["resident_capacity"] < 102656 or
                          row["analysis"]["backstore_found_fills"] != 0 or
                          row["analysis"]["h64_exact_live_max"] != 0):
                row["status"] = "FAIL"
                row["reason"] = "ideal directory experienced capacity offload"
        except Exception as error:
            row["status"] = "FAIL"
            row["reason"] = str(error)
    atomic_json(result_path, row)
    return row


def write_summary(root, rows):
    by_repeat = {}
    for repeat in sorted({row["repeat"] for row in rows}):
        arms = {row["arm"]: row for row in rows if row["repeat"] == repeat}
        if set(arms) != {"spill", "ideal"} or any(
                row["status"] != "PASS" for row in arms.values()):
            continue
        spill = arms["spill"]["analysis"]
        ideal = arms["ideal"]["analysis"]
        delta_ns = spill["outer_mean_ns"] - ideal["outer_mean_ns"]
        by_repeat[str(repeat)] = {
            "spill": spill, "ideal": ideal,
            "delta_outer_mean_ns": delta_ns,
            "delta_outer_mean_cycles_2ghz": delta_ns * 2.0,
        }
    deltas = [item["delta_outer_mean_ns"] for item in by_repeat.values()]
    summary = {
        "schema_version": 1,
        "definition": "mean(all completed spill Outer) - mean(all completed ideal Outer)",
        "rows": rows,
        "repeats": by_repeat,
        "complete_repeats": len(by_repeat),
        "delta_mean_ns": statistics.mean(deltas) if deltas else None,
        "delta_mean_cycles_2ghz": statistics.mean(deltas) * 2.0 if deltas else None,
        "delta_stdev_ns": statistics.stdev(deltas) if len(deltas) > 1 else 0.0,
        "status": "PASS" if len(by_repeat) == 3 and all(
            value * 2.0 < 50.0 for value in deltas) else "INCOMPLETE",
    }
    atomic_json(root / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=2400)
    args = parser.parse_args()
    root = args.output_root.resolve()
    try:
        root.relative_to(ROOT)
    except ValueError:
        parser.error("--output-root must be inside the workspace")
    root.mkdir(parents=True, exist_ok=True)
    orders = {1: ("spill", "ideal"), 2: ("ideal", "spill"),
              3: ("spill", "ideal")}
    rows = []
    for repeat in (1, 2, 3):
        for arm in orders[repeat]:
            row = run_case(root, repeat, arm, args.timeout)
            rows.append(row)
            write_summary(root, rows)
            print(json.dumps(row, sort_keys=True), flush=True)
            if row["status"] != "PASS":
                return 1
    return 0 if write_summary(root, rows)["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
