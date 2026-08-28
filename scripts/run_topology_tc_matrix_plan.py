#!/usr/bin/env python3
"""Execute one generated topology-matrix tier with resumable status files."""

import argparse
import datetime
import json
import os
import pathlib
import subprocess
import sys


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def host_result_dir(result_root, job):
    prefix = "/results/"
    value = job["result_path"]
    if not value.startswith(prefix):
        raise ValueError(f"job {job['job_id']} result_path must start with {prefix}")
    return result_root / value[len(prefix):]


def completed_result(result_dir):
    path = result_dir / "_plan_result.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def verify_job(job, result_dir, return_code):
    tc = int(job["tc"])
    verifier = result_dir / f"verify_tc{tc}.log"
    verifier_pass = False
    if verifier.is_file():
        lines = [line.strip() for line in verifier.read_text(errors="replace").splitlines()
                 if line.strip()]
        verifier_pass = bool(lines and lines[-1] == f">>> TC{tc} PASSED <<<")
    child_dir = result_dir / f"child_status_tc{tc}"
    exits = sorted(child_dir.glob("*.exit")) if child_dir.is_dir() else []
    values = {path.name: path.read_text(errors="replace").strip()
              for path in exits}
    expected_count = int(job["expected_child_exit_count"])
    child_pass = len(exits) == expected_count and all(value == "0"
                                                        for value in values.values())
    status = "PASS" if return_code == 0 and verifier_pass and child_pass else "FAIL"
    return {"schema_version": 1, "job_id": job["job_id"], "tier": job["tier"],
            "topology": job["topology"], "tc": tc, "status": status,
            "return_code": return_code, "verifier_pass": verifier_pass,
            "expected_child_exit_count": expected_count,
            "observed_child_exit_count": len(exits), "child_exits": values,
            "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}


def command_argv(job, workspace, result_root):
    replacements = {"${WORKSPACE:?set WORKSPACE}": str(workspace),
                    "${RESULT_ROOT:?set RESULT_ROOT}": str(result_root)}
    output = []
    for value in job["command_argv"]:
        replaced = value
        for source, target in replacements.items():
            replaced = replaced.replace(source, target)
        output.append(replaced)
    expected_log_base = f"LOG_BASE={job['result_path']}"
    output = [expected_log_base if value.startswith("LOG_BASE=") else value
              for value in output]
    return output


def run_manifest(manifest_path, workspace, result_root, stop_on_failure=False):
    manifest = json.loads(manifest_path.read_text())
    tier = manifest.get("tier")
    if tier not in ("smoke", "qualification"):
        raise ValueError("executor only permits smoke or qualification manifests")
    jobs = manifest.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError("manifest jobs must be an array")
    progress_path = result_root / tier / "progress.json"
    coordinator = result_root / tier / "coordinator.log"
    results = []
    for index, job in enumerate(jobs):
        result_dir = host_result_dir(result_root, job)
        previous = completed_result(result_dir)
        if previous and previous.get("status") == "PASS":
            results.append(previous)
            continue
        result_dir.mkdir(parents=True, exist_ok=True)
        atomic_json(progress_path, {
            "schema_version": 1, "tier": tier, "total": len(jobs),
            "completed": len(results), "running": job["job_id"],
            "pass": sum(row.get("status") == "PASS" for row in results),
            "fail": sum(row.get("status") == "FAIL" for row in results),
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })
        argv = command_argv(job, workspace, result_root)
        with coordinator.open("a") as stream:
            stream.write(f"START {job['job_id']}\n")
            stream.flush()
            status = subprocess.run(argv, stdout=stream,
                                    stderr=subprocess.STDOUT).returncode
            stream.write(f"DONE {job['job_id']} return_code={status}\n")
        result = verify_job(job, result_dir, status)
        atomic_json(result_dir / "_plan_result.json", result)
        results.append(result)
        if result["status"] != "PASS" and stop_on_failure:
            break
    payload = {
        "schema_version": 1, "tier": tier, "total": len(jobs),
        "completed": len(results), "running": None,
        "pass": sum(row.get("status") == "PASS" for row in results),
        "fail": sum(row.get("status") == "FAIL" for row in results),
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    atomic_json(progress_path, payload)
    return 0 if payload["completed"] == payload["total"] and not payload["fail"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=pathlib.Path)
    parser.add_argument("--workspace", type=pathlib.Path,
                        default=pathlib.Path(__file__).resolve().parents[1])
    parser.add_argument("--result-root", required=True, type=pathlib.Path)
    parser.add_argument("--stop-on-failure", action="store_true")
    args = parser.parse_args(argv)
    return run_manifest(args.manifest.resolve(), args.workspace.resolve(),
                        args.result_root.resolve(), args.stop_on_failure)


if __name__ == "__main__":
    sys.exit(main())
