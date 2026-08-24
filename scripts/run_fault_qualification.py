#!/usr/bin/env python3
"""Resumable Docker-only Q1-Q5 fault qualification orchestrator."""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "e2e"))
from fault_qualification_matrix import MATRIX, resolved_manifest  # noqa: E402

IMAGE = "ubcc-dev:ubuntu20.04"
CPU_LANES = ("0-7", "8-15", "16-23", "24-31")
WRITE_LOCK = threading.Lock()


def run_checked(argv):
    return subprocess.run(argv, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, check=True).stdout.strip()


def source_fingerprint():
    head = run_checked(["git", "rev-parse", "HEAD"])
    status = run_checked(["git", "status", "--porcelain=v1", "-uall"])
    diff = subprocess.run(["git", "diff", "--binary", "HEAD"], cwd=ROOT,
                          stdout=subprocess.PIPE, check=True).stdout
    untracked_hashes = []
    relevant_status = []
    ignored_prefixes = ("logs/", "results/", "build/", "gem5/shared_ipc/")
    for line in status.splitlines():
        name = line[3:]
        if name.startswith(ignored_prefixes):
            continue
        relevant_status.append(line)
        if line.startswith("?? "):
            path = ROOT / name
            if path.is_file():
                untracked_hashes.append((name, hashlib.sha256(path.read_bytes()).hexdigest()))
    payload = json.dumps({"head": head, "status": relevant_status,
                          "diff_sha256": hashlib.sha256(diff).hexdigest(),
                          "untracked": untracked_hashes}, sort_keys=True).encode()
    return {"git_head": head, "dirty": bool(relevant_status),
            "workspace_sha256": hashlib.sha256(payload).hexdigest(),
            "changed_path_count": len(relevant_status),
            "changed_paths_sha256": hashlib.sha256(
                "\n".join(relevant_status).encode()).hexdigest()}


def runtime_fingerprint():
    inspect = run_checked(["docker", "image", "inspect", IMAGE,
                           "--format", "{{.Id}} {{.RepoDigests}}"])
    return {"image": IMAGE, "image_identity": inspect,
            "docker_version": run_checked(["docker", "version", "--format",
                                             "{{.Server.Version}}"])}


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    temp.replace(path)


def load_result(case_dir):
    path = case_dir / "result.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def coverage(cases, results):
    def bucket(key, values):
        out = {}
        for value in sorted(values):
            selected = [c["id"] for c in cases if value in key(c)]
            statuses = [results.get(case_id, {}).get("status", "MISSING")
                        for case_id in selected]
            out[str(value)] = {"planned": len(selected), "pass": statuses.count("PASS"),
                               "fail": statuses.count("FAIL"),
                               "missing": statuses.count("MISSING"),
                               "not_supported": statuses.count("NOT_SUPPORTED")}
        return out
    messages = {r["message"] for c in cases for r in c["rules"]}
    actions = {r["action"] for c in cases for r in c["rules"]}
    topologies = {c["topology"] for c in cases}
    return {"message": bucket(lambda c: {r["message"] for r in c["rules"]}, messages),
            "action": bucket(lambda c: {r["action"] for r in c["rules"]}, actions),
            "topology": bucket(lambda c: {c["topology"]}, topologies)}


def write_rollups(run_root, cases, fingerprints):
    results = {c["id"]: load_result(run_root / "cases" / c["id"]) or {}
               for c in cases}
    rows = ["case_id\tqualification\ttc\ttopology\tplanned\texecuted\tstatus\tseconds\tevidence"]
    for c in cases:
        result = results[c["id"]]
        status = result.get("status", "MISSING")
        rows.append("\t".join((c["id"], c["qualification"], str(c["tc"]),
                               c["topology"], "1", "1" if result else "0", status,
                               str(result.get("duration_seconds", "")),
                               result.get("evidence", ""))))
    (run_root / "matrix.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    statuses = [results[c["id"]].get("status", "MISSING") for c in cases]
    summary = {"schema": 1, "run_id": run_root.name, "updated_at": dt.datetime.now(
        dt.timezone.utc).isoformat(), "fingerprints": fingerprints,
        "counts": {"planned": len(cases), "executed": sum(bool(results[c["id"]]) for c in cases),
                   "pass": statuses.count("PASS"), "fail": statuses.count("FAIL"),
                   "missing": statuses.count("MISSING"),
                   "not_supported": statuses.count("NOT_SUPPORTED")},
        "coverage": coverage(cases, results)}
    atomic_json(run_root / "summary.json", summary)
    atomic_json(run_root / "progress.json", {"run_id": run_root.name,
        "updated_at": summary["updated_at"], "counts": summary["counts"],
        "cases": {case_id: result.get("status", "MISSING")
                  for case_id, result in results.items()}})


def topology_arg(topology):
    return "--" + topology


def execute_case(entry, lane, run_root, fingerprints, timeout):
    case_dir = run_root / "cases" / entry["id"]
    case_dir.mkdir(parents=True, exist_ok=True)
    existing = load_result(case_dir)
    if existing and existing.get("status") in ("PASS", "NOT_SUPPORTED"):
        return existing
    if not entry["supported"]:
        result = {"case_id": entry["id"], "status": "NOT_SUPPORTED",
                  "reason": entry["reason"], "fingerprints": fingerprints,
                  "evidence": str(case_dir.relative_to(ROOT))}
        atomic_json(case_dir / "result.json", result)
        return result
    manifest = resolved_manifest(entry)
    atomic_json(case_dir / "resolved_manifest.json", manifest)
    rules = ";".join(r["text"] for r in entry["rules"])
    log_dir = case_dir / "logs"
    env_args = {"E2E_RUN_ID": f"fq-{run_root.name}-{entry['id']}",
                "LOG_BASE": f"/workspace/{log_dir.relative_to(ROOT)}",
                "E2E_FAULT_RULES_OVERRIDE": rules,
                "E2E_FAULT_MANIFEST": f"/workspace/{(case_dir / 'resolved_manifest.json').relative_to(ROOT)}",
                "TIMEOUT_SEC": str(timeout), "EP_SUPERVISOR": "1",
                 "EP_SUPERVISOR_INTERVAL": "30",
                 "EP_SUPERVISOR_PROGRESS_STALL_SEC": "600",
                 "EP_SUPERVISOR_DISK_FREE_GB": "5",
                 "EP_DOCKER_CPUSET": lane,
                 "LD_LIBRARY_PATH": "/workspace/thirdparty/zeromq/lib"}
    env_args.update(entry["env"])
    name_hash = hashlib.sha1(f"{run_root.name}-{entry['id']}".encode()).hexdigest()[:12]
    command = ["docker", "run", "--rm", "--name", f"ubcc-fq-{name_hash}",
                "--network", "none", "--cpuset-cpus", lane, "--cpuset-mems", "0",
                "--init", "-v", f"{ROOT}:/workspace",
                "-v", f"{ROOT / 'gem5/gem5'}:/workspace/gem5",
                "-v", "/mnt/data2/cgc/.local/lib:/workspace/thirdparty/zeromq/lib:ro",
                "-w", "/workspace", IMAGE,
               "env", *(f"{key}={value}" for key, value in sorted(env_args.items())),
               "bash", "tests/e2e/run_multi.sh", topology_arg(entry["topology"]),
               str(entry["tc"])]
    (case_dir / "command.txt").write_text(shlex.join(command) + "\n", encoding="utf-8")
    started = time.monotonic()
    started_at = dt.datetime.now(dt.timezone.utc).isoformat()
    with (case_dir / "runner.log").open("w", encoding="utf-8") as output:
        proc = subprocess.run(command, cwd=ROOT, stdout=output,
                              stderr=subprocess.STDOUT)
    duration = round(time.monotonic() - started, 3)
    result = {"case_id": entry["id"], "qualification": entry["qualification"],
              "tc": entry["tc"], "topology": entry["topology"], "lane": lane,
              "status": "PASS" if proc.returncode == 0 else "FAIL",
              "returncode": proc.returncode, "started_at": started_at,
              "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "duration_seconds": duration, "fingerprints": fingerprints,
              "evidence": str(case_dir.relative_to(ROOT)),
              "manifest": str((case_dir / "resolved_manifest.json").relative_to(ROOT))}
    atomic_json(case_dir / "result.json", result)
    return result


def execute_lane(entries, lane, run_root, fingerprints, timeout, all_cases):
    results = []
    for entry in entries:
        try:
            results.append(execute_case(entry, lane, run_root, fingerprints, timeout))
        except Exception as exc:
            case_dir = run_root / "cases" / entry["id"]
            result = {"case_id": entry["id"], "status": "FAIL",
                      "reason": repr(exc), "fingerprints": fingerprints,
                      "evidence": str(case_dir.relative_to(ROOT))}
            atomic_json(case_dir / "result.json", result)
            results.append(result)
        with WRITE_LOCK:
            write_rollups(run_root, all_cases, fingerprints)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--qualification", action="append", default=[])
    parser.add_argument("--jobs", type=int, default=4, choices=range(1, 5))
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    cases = [c for c in MATRIX if (not args.case or c["id"] in args.case) and
             (not args.qualification or c["qualification"] in args.qualification)]
    if args.list:
        for c in cases: print(c["id"], c["qualification"], c["tc"], c["topology"])
        return 0
    if not cases:
        parser.error("selection is empty")
    # Mandatory pre-execution daemon check.  Never fall back to host execution.
    try:
        subprocess.run(["docker", "ps"], cwd=ROOT, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Docker unavailable: {exc}", file=sys.stderr)
        return 2
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = (args.run_root or ROOT / "logs" / "fault_qualification" /
                f"fq-{stamp}-{os.getpid()}").resolve()
    try:
        run_root.relative_to(ROOT)
    except ValueError:
        parser.error("--run-root must be inside the workspace so Docker can persist evidence")
    run_root.mkdir(parents=True, exist_ok=True)
    fingerprints = {"source": source_fingerprint(), "runtime": runtime_fingerprint()}
    atomic_json(run_root / "run.json", {"schema": 1, "run_id": run_root.name,
                "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "selection": [c["id"] for c in cases], "fingerprints": fingerprints,
                "cpu_budget": "0-31", "lanes": list(CPU_LANES)})
    write_rollups(run_root, cases, fingerprints)

    normal = [c for c in cases if not c["exclusive"]]
    exclusive = [c for c in cases if c["exclusive"]]
    lane_entries = [[] for _ in range(args.jobs)]
    for index, entry in enumerate(normal):
        lane_entries[index % args.jobs].append(entry)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(execute_lane, entries, CPU_LANES[index], run_root,
                               fingerprints, args.timeout, cases)
                   for index, entries in enumerate(lane_entries) if entries]
        for future in concurrent.futures.as_completed(futures):
            future.result()
    # Large 8N2S/16N1S cases are deliberately serialized and own all CPUs.
    for entry in exclusive:
        execute_case(entry, "0-31", run_root, fingerprints, args.timeout)
        write_rollups(run_root, cases, fingerprints)
    summary = json.loads((run_root / "summary.json").read_text(encoding="utf-8"))
    print(json.dumps(summary["counts"], sort_keys=True))
    print(f"Evidence: {run_root}")
    return 0 if summary["counts"]["fail"] == 0 and summary["counts"]["missing"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
