#!/usr/bin/env python3
"""Run the complete 512 KiB P0 performance matrix within a CPU budget."""

import concurrent.futures
import datetime
import fcntl
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import zlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
RUN_TAG = os.environ.get(
    "RUN_TAG", datetime.datetime.now().strftime("p0_512k_%Y%m%d_%H%M%S"))
LOG_ROOT = pathlib.Path(os.environ.get("LOG_ROOT", ROOT / "logs" / RUN_TAG))
MAX_PARALLEL = int(os.environ.get("MAX_PARALLEL", "5"))
CASE_TIMEOUT = int(os.environ.get("CASE_TIMEOUT_SEC", "10800"))
STALL_TIMEOUT = int(os.environ.get("STALL_TIMEOUT_SEC", "1800"))
DISK_FLOOR_GB = int(os.environ.get("DISK_FLOOR_GB", "80"))
CPU_SETS = os.environ.get("CPU_SETS", "").split()
PROFILES = os.environ.get(
    "PROFILE_LIST", "naive spill-noopt optimized").split()
LEVELS = [int(value) for value in os.environ.get(
    "PRESSURE_LEVELS", "150").split()]
PORTABLE_TCS = [int(value) for value in os.environ.get(
    "PORTABLE_TC_LIST", "142 143 144 145 146 147").split()]
MULTI_TOPOLOGIES = os.environ.get(
    "MULTI_TOPOLOGY_LIST", "2n1s 3n2s 8n1s 8n2s").split()
INCLUDE_3N1S = os.environ.get("INCLUDE_3N1S", "1") == "1"
NORMAL_CAPACITY = 65536
PORTABLE_BATCHES = 32
HOT_PER_PLANE = {142: 32, 143: 137, 144: 192, 145: 136, 146: 192, 147: 136}
TOPOLOGY = {
    "2n1s": ("2n1s", 2),
    "3n1s": ("1s", 3),
    "3n2s": ("2s", 6),
    "8n1s": ("8n1s", 8),
    "8n2s": ("8n2s", 16),
}
LEGACY_TOPOLOGIES = {
    131: "8n1s",
    132: "3n1s",
    133: "8n1s",
    134: "8n2s",
}
LEGACY_TCS = [int(value) for value in os.environ.get(
    "LEGACY_TC_LIST", "131 132 133 134").split()]


if CPU_SETS and len(CPU_SETS) < MAX_PARALLEL:
    raise SystemExit("CPU_SETS has fewer entries than MAX_PARALLEL")

LOG_ROOT.mkdir(parents=True, exist_ok=True)
CASES_ROOT = LOG_ROOT / "cases"
CASES_ROOT.mkdir(exist_ok=True)
LOCK_PATH = LOG_ROOT / "runner.lock"
HEARTBEAT = LOG_ROOT / "runner_heartbeat.log"
PROGRESS = LOG_ROOT / "progress.json"
MATRIX = LOG_ROOT / "matrix.tsv"
MANIFEST = LOG_ROOT / "manifest.json"


def profile_settings(profile):
    if profile == "naive":
        return "naive", "naive", "--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0"
    if profile == "spill-noopt":
        return "spill", "spill-noopt", "--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0"
    if profile == "optimized":
        return "spill", "optimized", "--silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1"
    raise ValueError(profile)


def case_key(case):
    if case["group"] == "legacy":
        return f"tc{case['tc']}_{case['topology']}_{case['profile']}_legacy"
    return (f"tc{case['tc']}_{case['topology']}_{case['profile']}_"
            f"p{case['level']}")


def build_cases():
    cases = []
    for tc in LEGACY_TCS:
        topology = LEGACY_TOPOLOGIES[tc]
        for profile in PROFILES:
            cases.append({"group": "legacy", "tc": tc,
                          "topology": topology, "profile": profile, "level": 0})
    if INCLUDE_3N1S:
        for tc in PORTABLE_TCS:
            for level in LEVELS:
                for profile in PROFILES:
                    cases.append({"group": "portable", "tc": tc,
                                  "topology": "3n1s", "profile": profile,
                                  "level": level})
    for topology in MULTI_TOPOLOGIES:
        for tc in PORTABLE_TCS:
            for profile in PROFILES:
                cases.append({"group": "portable", "tc": tc,
                              "topology": topology, "profile": profile,
                              "level": 150})
    return cases


CASES = build_cases()
CASE_FILTER = os.environ.get("CASE_FILTER", "")
PRIORITY_CASE = os.environ.get("PRIORITY_CASE", "")
PRE_CASE_BUILD = os.environ.get("PRE_CASE_BUILD", "")


def atomic_json(path, payload):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    tmp.replace(path)


def read_result(case):
    path = CASES_ROOT / case_key(case) / "result.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def write_matrix():
    rows = []
    for case in CASES:
        result = read_result(case)
        status = result.get("status", "PENDING") if result else "PENDING"
        elapsed = result.get("elapsed_sec", 0) if result else 0
        reason = result.get("reason", "") if result else ""
        log_dir = result.get("log_dir", "") if result else ""
        rows.append((status, case["group"], case["tc"], case["topology"],
                     case["profile"], case["level"], elapsed, log_dir, reason))
    text = ("status\tgroup\ttc\ttopology\tprofile\tlevel_pct\telapsed_sec\t"
            "log_dir\treason\n")
    text += "".join("\t".join(str(value) for value in row) + "\n" for row in rows)
    tmp = MATRIX.with_suffix(".tmp")
    tmp.write_text(text)
    tmp.replace(MATRIX)


def write_progress(running):
    counts = {}
    for case in CASES:
        result = read_result(case)
        status = result.get("status", "PENDING") if result else "PENDING"
        counts[status] = counts.get(status, 0) + 1
    atomic_json(PROGRESS, {
        "timestamp": datetime.datetime.now().astimezone().isoformat(),
        "run_tag": RUN_TAG,
        "total": len(CASES),
        "counts": counts,
        "running": running,
    })
    write_matrix()


def disk_ready():
    return shutil.disk_usage(ROOT).free >= DISK_FLOOR_GB * 1024 ** 3


def pressure_flags(case):
    planes = TOPOLOGY[case["topology"]][1]
    hot_lines = HOT_PER_PLANE[case["tc"]] * planes
    target = NORMAL_CAPACITY * case["level"] // 100
    pressure = target - hot_lines
    if pressure <= 0:
        raise ValueError(f"non-positive pressure for {case}")
    return (f"-DPORTABLE_PRESSURE_LINES={pressure} "
            f"-DPORTABLE_TARGET_FOOTPRINT_LINES={target} "
            f"-DPORTABLE_NAIVE_CAPACITY_LINES={NORMAL_CAPACITY} "
            f"-DPORTABLE_PRESSURE_LEVEL_PCT={case['level']} "
            f"-DPORTABLE_BATCHES={PORTABLE_BATCHES}")


def verify_pressure(case, case_dir):
    if case["group"] != "portable":
        return True, ""
    records = []
    for path in case_dir.glob(f"simout_tc{case['tc']}_node*.log"):
        for line in path.read_text(errors="replace").splitlines():
            if not line.startswith("[PORTABLE-PRESSURE]"):
                continue
            fields = {}
            for atom in line.split()[1:]:
                if "=" in atom:
                    key, value = atom.split("=", 1)
                    fields[key] = int(value)
            records.append(fields)
    planes = TOPOLOGY[case["topology"]][1]
    target = NORMAL_CAPACITY * case["level"] // 100
    expected_pressure = target - HOT_PER_PLANE[case["tc"]] * planes
    valid = (len(records) == planes and
             sorted(record.get("node") for record in records) == list(range(planes)) and
             all(record.get("planes") == planes and
                 record.get("pressure_lines") == expected_pressure and
                 record.get("total_unique_lines") == target and
                 record.get("target_footprint_lines") == target and
                 record.get("naive_capacity_lines") == NORMAL_CAPACITY and
                 record.get("pressure_level_pct") == case["level"]
                 for record in records))
    return valid, "" if valid else "pressure_manifest_mismatch"


def stop_container(name):
    subprocess.run(["docker", "stop", "--time", "30", name],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["docker", "rm", "-f", name],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_case(case, slot):
    key = case_key(case)
    case_dir = CASES_ROOT / key
    case_dir.mkdir(parents=True, exist_ok=True)
    existing = read_result(case)
    if existing and existing.get("status") == "PASS":
        return existing

    if PRE_CASE_BUILD and key == PRIORITY_CASE:
        build = subprocess.run(PRE_CASE_BUILD, cwd=ROOT, shell=True)
        if build.returncode != 0:
            raise RuntimeError(
                f"pre-case build failed for {key}: exit={build.returncode}")

    policy, perf_profile, gem5_opts = profile_settings(case["profile"])
    topology_flag = TOPOLOGY[case["topology"]][0]
    token = zlib.crc32(f"{RUN_TAG}_{key}".encode()) & 0xffffffff
    run_id = f"p0_{token:08x}_{case['tc']}"
    container = f"p0-{token:08x}-{case['tc']}"
    stop_container(container)
    env = [
        f"E2E_RUN_ID={run_id}",
        f"LOG_BASE={case_dir.relative_to(ROOT)}",
        f"TIMEOUT_SEC={CASE_TIMEOUT}",
        f"E2E_STALL_TIMEOUT_SEC={STALL_TIMEOUT}",
        "EP_SUPERVISOR=1",
        "EP_SUPERVISOR_INTERVAL=60",
        f"EP_SUPERVISOR_PROGRESS_STALL_SEC={STALL_TIMEOUT}",
        "EP_SUPERVISOR_DISK_FREE_GB=20",
        "EP_TRACE_PERF=off",
        f"EP_PERF_PROFILE={perf_profile}",
        f"UBCC_POLICY={policy}",
        f"UBCC_OPTS=--dir-overflow-policy={policy}",
        f"EP_GEM5_OPTS={gem5_opts}",
    ]
    if case["group"] == "portable":
        env.extend(["PORTABLE_512K_DIR=1",
                    f"WORKLOAD_CFLAGS={pressure_flags(case)}"])

    command = [
        "docker", "run", "--name", container, "--network", "none",
        "--cpuset-mems", "0",
        "--init", "-v", f"{ROOT}:/workspace", "-w", "/workspace",
        "ubcc-dev:ubuntu20.04", "env", *env,
        "bash", "tests/e2e/run_multi.sh", f"--{topology_flag}", str(case["tc"]),
    ]
    if CPU_SETS:
        command[6:6] = ["--cpuset-cpus", CPU_SETS[slot]]
    started = time.monotonic()
    status = "FAIL"
    reason = "unknown"
    return_code = None
    with (case_dir / "runner_stdout.log").open("w") as out:
        try:
            result = subprocess.run(command, cwd=ROOT, stdout=out,
                                    stderr=subprocess.STDOUT,
                                    timeout=CASE_TIMEOUT + 300)
            return_code = result.returncode
            reason = f"exit={return_code}"
        except subprocess.TimeoutExpired:
            reason = "outer_timeout"
            return_code = 124
        finally:
            stop_container(container)
    elapsed = int(time.monotonic() - started)
    verify = case_dir / f"verify_tc{case['tc']}.log"
    passed = False
    if return_code == 0 and verify.exists():
        lines = verify.read_text(errors="replace").splitlines()
        passed = bool(lines and lines[-1] == f">>> TC{case['tc']} PASSED <<<")
    manifest_ok, manifest_reason = True, ""
    if passed:
        manifest_ok, manifest_reason = verify_pressure(case, case_dir)
    if passed and manifest_ok:
        status = "PASS"
    elif manifest_reason:
        reason += f";{manifest_reason}"
    result = {
        **case,
        "case_key": key,
        "status": status,
        "reason": reason,
        "elapsed_sec": elapsed,
        "return_code": return_code,
        "cpuset": CPU_SETS[slot] if CPU_SETS else "unrestricted",
        "log_dir": str(case_dir),
        "timestamp": datetime.datetime.now().astimezone().isoformat(),
    }
    atomic_json(case_dir / "result.json", result)
    return result


def schedule_priority(case):
    topology_weight = {"8n2s": 0, "8n1s": 1, "3n2s": 2,
                       "3n1s": 3, "2n1s": 4}[case["topology"]]
    profile_weight = {"naive": 0, "spill-noopt": 1, "optimized": 2}[case["profile"]]
    return (topology_weight, profile_weight, -case["level"], case["tc"])


def can_admit(case, active_cases):
    topology = case["topology"]
    counts = {}
    for active in active_cases:
        counts[active["topology"]] = counts.get(active["topology"], 0) + 1
    if topology == "8n2s" and counts.get("8n2s", 0) >= 2:
        return False
    if topology == "8n1s" and counts.get("8n1s", 0) >= 3:
        return False
    return True


manifest_payload = {
    "run_tag": RUN_TAG,
    "created": datetime.datetime.now().astimezone().isoformat(),
    "total_cases": len(CASES),
    "max_parallel": MAX_PARALLEL,
    "cpu_sets": CPU_SETS[:MAX_PARALLEL] if CPU_SETS else ["unrestricted"],
    "case_timeout_sec": CASE_TIMEOUT,
    "stall_timeout_sec": STALL_TIMEOUT,
    "normal_naive_capacity_lines": NORMAL_CAPACITY,
    "sram_budget_bytes": 524288,
    "cases": CASES,
    "note": "Parallel qualification metrics share host resources; serialize formal latency reruns.",
}
if MANIFEST.exists():
    old = json.loads(MANIFEST.read_text())
    if old.get("cases") != CASES:
        raise SystemExit("existing manifest has a different case matrix")
else:
    atomic_json(MANIFEST, manifest_payload)


with LOCK_PATH.open("w") as lock:
    try:
        lock_flags = fcntl.LOCK_EX
        if os.environ.get("LOCK_NONBLOCK", "0") == "1":
            lock_flags |= fcntl.LOCK_NB
        fcntl.flock(lock, lock_flags)
    except BlockingIOError:
        raise SystemExit(f"another coordinator holds {LOCK_PATH}")

    pending = [case for case in CASES
               if not (read_result(case) and read_result(case).get("status") == "PASS")]
    if CASE_FILTER:
        case_filter = re.compile(CASE_FILTER)
        pending = [case for case in pending if case_filter.fullmatch(case_key(case))]
    pending.sort(key=schedule_priority)
    if PRIORITY_CASE:
        pending.sort(key=lambda case: case_key(case) != PRIORITY_CASE)
    running = {}
    completed = []
    write_progress([])

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        futures = {}
        while pending or futures:
            while pending and len(futures) < MAX_PARALLEL and disk_ready():
                active_cases = [case for case, _ in futures.values()]
                pending_index = next(
                    (index for index, candidate in enumerate(pending)
                     if can_admit(candidate, active_cases)), None)
                if pending_index is None:
                    break
                case = pending.pop(pending_index)
                used = {slot for _, slot in futures.values()}
                slot = next(index for index in range(MAX_PARALLEL) if index not in used)
                future = pool.submit(run_case, case, slot)
                futures[future] = (case, slot)
                with HEARTBEAT.open("a") as out:
                    out.write(f"{datetime.datetime.now().astimezone().isoformat()} START "
                              f"case={case_key(case)} slot={slot} "
                              f"cpus={CPU_SETS[slot] if CPU_SETS else 'unrestricted'}\n")

            if not futures:
                if pending and not disk_ready():
                    with HEARTBEAT.open("a") as out:
                        out.write(f"{datetime.datetime.now().astimezone().isoformat()} "
                                  "PAUSE disk_floor\n")
                    time.sleep(60)
                    continue
                break

            done, _ = concurrent.futures.wait(
                futures, timeout=30,
                return_when=concurrent.futures.FIRST_COMPLETED)
            if not done:
                running = [{"case": case_key(case), "slot": slot,
                            "cpuset": CPU_SETS[slot] if CPU_SETS else "unrestricted"}
                           for case, slot in futures.values()]
                write_progress(running)
                continue

            for future in done:
                case, slot = futures.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    result = {**case, "case_key": case_key(case),
                              "status": "FAIL", "reason": repr(exc),
                              "elapsed_sec": 0, "log_dir": ""}
                    case_dir = CASES_ROOT / case_key(case)
                    case_dir.mkdir(parents=True, exist_ok=True)
                    atomic_json(case_dir / "result.json", result)
                completed.append(result)
                with HEARTBEAT.open("a") as out:
                    out.write(f"{datetime.datetime.now().astimezone().isoformat()} DONE "
                              f"case={case_key(case)} status={result['status']} "
                              f"elapsed={result.get('elapsed_sec', 0)}\n")
            running = [{"case": case_key(case), "slot": slot,
                        "cpuset": CPU_SETS[slot] if CPU_SETS else "unrestricted"}
                       for case, slot in futures.values()]
            write_progress(running)

    write_progress([])
    failures = sum(1 for case in CASES
                   if not read_result(case) or read_result(case).get("status") != "PASS")
    print(f"P0 512KiB matrix complete: total={len(CASES)} non_pass={failures}")
    sys.exit(1 if failures else 0)
