#!/usr/bin/env python3
"""Wait for the focused sweep, then extend Metric1 in resource-safe stages."""

import argparse
import datetime
import json
import os
import pathlib
import subprocess
import time


ROOT = pathlib.Path(__file__).resolve().parents[1]


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def process_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-pid", required=True, type=int)
    parser.add_argument("--output-root", required=True, type=pathlib.Path)
    args = parser.parse_args(argv)
    root = args.output_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / "progress.json"
    state = {"phase": "waiting", "wait_pid": args.wait_pid, "stages": []}
    atomic_json(state_path, state)
    while process_alive(args.wait_pid):
        time.sleep(60)

    stages = [
        ("tc143_tc147_3n", ["3n1s", "3n2s"], list(range(143, 148)),
         ["0-15", "16-31"]),
        ("tc131_tc147_8n1s", ["8n1s"], [131] + list(range(142, 148)),
         ["0-15", "16-31"]),
        ("tc131_tc147_8n2s", ["8n2s"], [131] + list(range(142, 148)),
         ["0-31"]),
        ("tc131_tc147_16n1s", ["16n1s"], [131] + list(range(142, 148)),
         ["0-31"]),
    ]
    for name, topologies, test_cases, cpu_slots in stages:
        stage_root = root / name
        command = [
            "python3", "scripts/run_metric1_delta_matrix.py",
            "--output-root", str(stage_root.relative_to(ROOT)),
            "--topologies", *topologies,
            "--test-cases", *map(str, test_cases),
            "--cpu-slots", *cpu_slots,
            "--pressure-pct", "175",
        ]
        record = {"name": name, "command": command,
                  "started_at": datetime.datetime.now(
                      datetime.timezone.utc).isoformat(), "status": "RUNNING"}
        state["phase"] = name
        state["stages"].append(record)
        atomic_json(state_path, state)
        with (stage_root.parent / f"{name}.log").open("a") as stream:
            rc = subprocess.run(command, cwd=ROOT, stdout=stream,
                                stderr=subprocess.STDOUT).returncode
        record["return_code"] = rc
        record["status"] = "PASS" if rc == 0 else "FAIL"
        record["finished_at"] = datetime.datetime.now(
            datetime.timezone.utc).isoformat()
        atomic_json(state_path, state)
    state["phase"] = "done"
    atomic_json(state_path, state)
    return 0 if all(stage["status"] == "PASS" for stage in state["stages"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
