#!/usr/bin/env python3
"""Run the post-fix heavy E2E regression with pollable progress.

Every simulator invocation runs in ubcc-dev:ubuntu20.04. The host process only
orchestrates Docker and records progress/evidence.
"""

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time


ROOT = pathlib.Path(__file__).resolve().parents[1]
IMAGE = "ubcc-dev:ubuntu20.04"
CASES = (
    {"tc": 98, "topology": "--8n2s", "cpu": "o3", "timeout": 21600},
    {"tc": 128, "topology": "--1s", "cpu": "timing", "timeout": 3600},
    {"tc": 131, "topology": "--8n1s", "cpu": "timing", "timeout": 10800},
    {"tc": 132, "topology": "--1s", "cpu": "timing", "timeout": 10800},
    {"tc": 133, "topology": "--8n1s", "cpu": "timing", "timeout": 10800},
    {"tc": 134, "topology": "--8n2s", "cpu": "o3", "timeout": 14400},
)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_rows(path):
    if not path.is_file():
        return []
    try:
        return json.loads(path.read_text()).get("results", [])
    except (OSError, ValueError):
        return []


def run(args):
    output = args.output_root.resolve()
    try:
        output.relative_to(ROOT)
    except ValueError:
        raise SystemExit("--output-root must be inside the workspace")
    output.mkdir(parents=True, exist_ok=True)
    results_path = output / "run_summary.json"
    results = load_rows(results_path)
    passed = {row["tc"] for row in results if row.get("status") == "PASS"}
    runtime = {
        "gem5": sha256(ROOT / "gem5/gem5/build/ARM/gem5.opt"),
        "ubio": sha256(ROOT / "build/bin/ubio"),
        "networksim": sha256(ROOT / "build/bin/networksim"),
    }
    started_all = time.time()

    for index, case in enumerate(CASES):
        tc = case["tc"]
        if tc in passed:
            continue
        log_dir = output / f"tc{tc}"
        log_dir.mkdir(parents=True, exist_ok=True)
        run_id = f"heavy_postfix_tc{tc}"
        env = [
            f"E2E_RUN_ID={run_id}",
            f"LOG_BASE=/workspace/{log_dir.relative_to(ROOT)}",
            f"TIMEOUT_SEC={case['timeout']}",
            "STALL_TIMEOUT_SEC=1800",
            f"EP_CPU_MODEL={case['cpu']}",
            "EP_TRACE_PERF=sample",
            "EP_PERF_PROFILE=spill-noopt",
            "UBCC_POLICY=spill",
            "UBCC_OPTS=--dir-overflow-policy=spill",
            "EP_GEM5_OPTS=--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0",
            "LD_LIBRARY_PATH=/workspace/thirdparty/zeromq/lib",
        ]
        if case["cpu"] == "o3":
            env.append("EP_SEQUENCER_MAX_OUTSTANDING=16")
        command = [
            "docker", "run", "--rm", "--network", "none",
            "--cpuset-cpus=0-29",
            "-v", f"{ROOT}:/workspace",
            "-v", f"{ROOT / 'gem5/gem5'}:/workspace/gem5",
            "-v", "/mnt/data2/cgc/.local/lib:/workspace/thirdparty/zeromq/lib:ro",
            "-w", "/workspace", IMAGE, "env", *env,
            "bash", "tests/e2e/run_multi.sh", case["topology"], str(tc),
        ]
        started = time.time()
        with (log_dir / "driver.log").open("w") as stream:
            process = subprocess.Popen(
                command, stdout=stream, stderr=subprocess.STDOUT)
            while process.poll() is None:
                elapsed = time.time() - started
                atomic_json(output / "progress.json", {
                    "total_cases": len(CASES),
                    "completed_cases": len(results),
                    "pass": sum(row.get("status") == "PASS" for row in results),
                    "fail": sum(row.get("status") != "PASS" for row in results),
                    "current": {
                        "tc": tc,
                        "topology": case["topology"],
                        "cpu": case["cpu"],
                        "elapsed_sec": elapsed,
                        "timeout_sec": case["timeout"],
                        "case_index": index + 1,
                    },
                    "runtime_sha256": runtime,
                })
                time.sleep(30)
            status = process.returncode
        verifier = log_dir / f"verify_tc{tc}.log"
        verifier_pass = verifier.is_file() and verifier.read_text(
            errors="replace").rstrip().endswith(f">>> TC{tc} PASSED <<<")
        row = {
            **case,
            "status": "PASS" if status == 0 and verifier_pass else "FAIL",
            "return_code": status,
            "verifier_pass": verifier_pass,
            "elapsed_sec": time.time() - started,
            "log_dir": str(log_dir),
            "runtime_sha256": runtime,
        }
        results = [old for old in results if old.get("tc") != tc]
        results.append(row)
        results.sort(key=lambda item: item["tc"])
        atomic_json(results_path, {
            "total": len(results),
            "pass": sum(item.get("status") == "PASS" for item in results),
            "fail": sum(item.get("status") != "PASS" for item in results),
            "results": results,
        })
        atomic_json(output / "progress.json", {
            "total_cases": len(CASES),
            "completed_cases": len(results),
            "pass": sum(item.get("status") == "PASS" for item in results),
            "fail": sum(item.get("status") != "PASS" for item in results),
            "current": None,
            "last": row,
            "elapsed_sec": time.time() - started_all,
            "runtime_sha256": runtime,
        })
        if row["status"] != "PASS" and not args.keep_going:
            break

    complete = len(results) == len(CASES)
    failures = sum(row.get("status") != "PASS" for row in results)
    atomic_json(output / "progress.json", {
        "total_cases": len(CASES),
        "completed_cases": len(results),
        "pass": sum(row.get("status") == "PASS" for row in results),
        "fail": failures,
        "current": None,
        "complete": complete,
        "elapsed_sec": time.time() - started_all,
        "runtime_sha256": runtime,
    })
    return 0 if complete and failures == 0 else 1


def daemonize(args):
    output = args.output_root.resolve()
    output.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(pathlib.Path(__file__).resolve()),
               "--output-root", str(args.output_root)]
    if args.keep_going:
        command.append("--keep-going")
    with (output / "orchestrator.log").open("a") as stream:
        process = subprocess.Popen(
            command, stdout=stream, stderr=subprocess.STDOUT,
            start_new_session=True, cwd=ROOT)
    atomic_json(output / "daemon.json", {"pid": process.pid,
                "command": command, "started": time.time()})
    print(process.pid)
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=pathlib.Path, required=True)
    parser.add_argument("--keep-going", action="store_true")
    parser.add_argument("--daemon", action="store_true")
    args = parser.parse_args()
    return daemonize(args) if args.daemon else run(args)


if __name__ == "__main__":
    sys.exit(main())
