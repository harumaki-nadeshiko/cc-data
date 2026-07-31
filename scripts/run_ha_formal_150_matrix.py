#!/usr/bin/env python3
"""Run the 2N1S HA01-HA12 150%-capacity matrix with bounded parallelism."""
import concurrent.futures
import datetime
import json
import os
import pathlib
import subprocess
import sys
import time
import zlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
RUN_TAG = os.environ.get("RUN_TAG", "ha_formal150_20260731")
LOG_ROOT = pathlib.Path(os.environ.get("LOG_ROOT", ROOT / "logs" / RUN_TAG))
PROFILES = os.environ.get("PROFILE_LIST", "naive spill-noopt optimized").split()
TCS = [int(value) for value in os.environ.get(
    "TC_LIST", "210 211 212 213 214 215 216 217 218 219 220 221").split()]
MAX_PARALLEL = int(os.environ.get("MAX_PARALLEL", "5"))
CASE_TIMEOUT = int(os.environ.get("CASE_TIMEOUT_SEC", "10800"))
DEADLINE_TEXT = os.environ.get("DEADLINE_CST", "2026-07-31 11:00:00")
DEADLINE = datetime.datetime.strptime(DEADLINE_TEXT, "%Y-%m-%d %H:%M:%S").replace(
    tzinfo=datetime.timezone(datetime.timedelta(hours=8))).timestamp()
CPU_SETS = os.environ.get(
    "CPU_SETS", "0-5 6-11 12-17 18-23 24-29").split()

if len(CPU_SETS) < MAX_PARALLEL:
    raise SystemExit("CPU_SETS has fewer entries than MAX_PARALLEL")

LOG_ROOT.mkdir(parents=True, exist_ok=True)
MATRIX = LOG_ROOT / "matrix.tsv"
PROGRESS = LOG_ROOT / "progress.json"
HEARTBEAT = LOG_ROOT / "runner_heartbeat.log"
MATRIX.write_text("status\ttc\tprofile\telapsed_sec\tlog_dir\treason\n")


def write_progress(pending, running, completed):
    payload = {
        "timestamp": datetime.datetime.now().astimezone().isoformat(),
        "deadline_cst": DEADLINE_TEXT,
        "pending": pending,
        "running": running,
        "completed": completed,
        "total": len(TCS) * len(PROFILES),
    }
    tmp = PROGRESS.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    tmp.replace(PROGRESS)


def profile_settings(profile):
    if profile == "naive":
        return "naive", "naive", "--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0"
    if profile == "spill-noopt":
        return "spill", "spill-noopt", "--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0"
    if profile == "optimized":
        return "spill", "optimized", "--silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1"
    raise ValueError(profile)


def run_case(case, slot):
    tc, profile = case
    now = time.time()
    remaining = int(DEADLINE - now)
    if remaining <= 120:
        return case, "SKIP", 0, "deadline", ""
    timeout_sec = min(CASE_TIMEOUT, remaining - 60)
    policy, perf_profile, gem5_opts = profile_settings(profile)
    case_dir = LOG_ROOT / profile / f"tc{tc}"
    case_dir.mkdir(parents=True, exist_ok=True)
    token = zlib.crc32(f"{RUN_TAG}_{profile}_{tc}".encode()) & 0xffffffff
    run_id = f"hf_{token}_{profile[0]}_{tc}"
    command = [
        "docker", "run", "--rm", "--network", "none",
        "--cpuset-cpus", CPU_SETS[slot],
        "-v", f"{ROOT}:/workspace", "-w", "/workspace",
        "ubcc-dev:ubuntu20.04", "env",
        f"E2E_RUN_ID={run_id}",
        f"LOG_BASE={case_dir.relative_to(ROOT)}",
        f"TIMEOUT_SEC={timeout_sec}",
        "EP_SUPERVISOR=1", "EP_SUPERVISOR_INTERVAL=60",
        "EP_SUPERVISOR_PROGRESS_STALL_SEC=600",
        "EP_SUPERVISOR_DISK_FREE_GB=50",
        "EP_TRACE_PERF=off",
        f"EP_PERF_PROFILE={perf_profile}",
        f"UBCC_POLICY={policy}",
        f"UBCC_OPTS=--dir-overflow-policy={policy}",
        f"EP_GEM5_OPTS={gem5_opts}",
        "WORKLOAD_CFLAGS=-DHA_FORMAL_CAPACITY_LINES=768",
        "bash", "tests/e2e/run_multi.sh", "--2n1s", str(tc),
    ]
    start = time.monotonic()
    with (case_dir / "runner_stdout.log").open("w") as out:
        try:
            result = subprocess.run(command, cwd=ROOT, stdout=out,
                                    stderr=subprocess.STDOUT,
                                    timeout=timeout_sec + 120)
            return_code = result.returncode
            reason = f"exit={return_code}"
        except subprocess.TimeoutExpired:
            return_code = 124
            reason = "outer_timeout"
    elapsed = int(time.monotonic() - start)
    verify = case_dir / f"verify_tc{tc}.log"
    passed = False
    if return_code == 0 and verify.exists():
        lines = verify.read_text(errors="replace").splitlines()
        passed = bool(lines and lines[-1] == f">>> TC{tc} PASSED <<<")
    if passed:
        formal = []
        for path in case_dir.glob("simout_tc*_node*.log"):
            for line in path.read_text(errors="replace").splitlines():
                if not line.startswith("{"):
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                expected_scenario = "FORMAL150" if tc <= 219 else f"HA{tc - 209:02d}"
                if (record.get("kind") == "capacity" and
                        record.get("scenario") == expected_scenario and
                        record.get("formal_capacity") is True):
                    formal.append(record)
        passed = (len(formal) == 1 and
                  formal[0].get("resident_capacity") == 512 and
                  formal[0].get("unique_lines") == 768 and
                  formal[0].get("capacity_ratio") == 1.5)
        if not passed:
            reason += ";missing_formal150"
    return case, "PASS" if passed else "FAIL", elapsed, reason, str(case_dir)


cases = [(tc, profile) for profile in PROFILES for tc in TCS]
pending = list(cases)
running = {}
completed = []
write_progress(len(pending), [], completed)

with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
    futures = {}
    while pending or futures:
        if pending and not futures and time.time() >= DEADLINE - 120:
            break
        while pending and len(futures) < MAX_PARALLEL and time.time() < DEADLINE - 120:
            case = pending.pop(0)
            used = {slot for _, slot in futures.values()}
            slot = next(slot for slot in range(MAX_PARALLEL) if slot not in used)
            future = pool.submit(run_case, case, slot)
            futures[future] = (case, slot)
            with HEARTBEAT.open("a") as out:
                out.write(f"{datetime.datetime.now().astimezone().isoformat()} START "
                          f"tc={case[0]} profile={case[1]} slot={slot} "
                          f"cpus={CPU_SETS[slot]}\n")

        done, _ = concurrent.futures.wait(
            futures, timeout=30, return_when=concurrent.futures.FIRST_COMPLETED)
        if not done:
            running = [{"tc": case[0], "profile": case[1], "slot": slot}
                       for case, slot in futures.values()]
            write_progress(len(pending), running, completed)
            with HEARTBEAT.open("a") as out:
                out.write(f"{datetime.datetime.now().astimezone().isoformat()} POLL "
                          f"pending={len(pending)} running={len(futures)} "
                          f"completed={len(completed)}\n")
            continue

        for future in done:
            case, slot = futures.pop(future)
            try:
                _, status, elapsed, reason, log_dir = future.result()
            except Exception as exc:
                status, elapsed, reason, log_dir = "FAIL", 0, repr(exc), ""
            row = {
                "tc": case[0], "profile": case[1], "status": status,
                "elapsed_sec": elapsed, "reason": reason, "log_dir": log_dir,
            }
            completed.append(row)
            with MATRIX.open("a") as out:
                out.write(f"{status}\t{case[0]}\t{case[1]}\t{elapsed}\t"
                          f"{log_dir}\t{reason}\n")
            with HEARTBEAT.open("a") as out:
                out.write(f"{datetime.datetime.now().astimezone().isoformat()} DONE "
                          f"tc={case[0]} profile={case[1]} status={status} "
                          f"elapsed={elapsed}\n")
        running = [{"tc": case[0], "profile": case[1], "slot": slot}
                   for case, slot in futures.values()]
        write_progress(len(pending), running, completed)

if pending:
    for tc, profile in pending:
        completed.append({"tc": tc, "profile": profile, "status": "SKIP",
                          "elapsed_sec": 0, "reason": "deadline",
                          "log_dir": ""})
        with MATRIX.open("a") as out:
            out.write(f"SKIP\t{tc}\t{profile}\t0\t\tdeadline\n")

write_progress(0, [], completed)
failures = sum(row["status"] == "FAIL" for row in completed)
print(f"HA formal matrix complete: {len(completed)} cases, failures={failures}")
sys.exit(1 if failures else 0)
