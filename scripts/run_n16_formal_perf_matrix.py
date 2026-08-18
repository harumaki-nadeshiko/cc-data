#!/usr/bin/env python3
"""Run the report-grade 16N1S O3 portable p150 matrix serially."""

import datetime
import fcntl
import gzip
import hashlib
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import time
import zlib


ROOT = pathlib.Path(__file__).resolve().parents[1]


def parse_profiles():
    values = os.environ.get(
        "PROFILE_LIST", "naive spill-noopt optimized").split()
    valid = {"naive", "spill-noopt", "optimized"}
    if not values or len(values) != len(set(values)) or not set(values) <= valid:
        raise SystemExit(f"invalid PROFILE_LIST: {values}")
    return values


RUN_TAG = os.environ.get(
    "RUN_TAG", datetime.datetime.now().strftime("n16_formal_%Y%m%d_%H%M%S"))
LOG_ROOT = pathlib.Path(os.environ.get(
    "LOG_ROOT", f"/mnt/data2/cgc/cc-ep-v5-o3-n16-formal/{RUN_TAG}"))
GEM5_BUILD = pathlib.Path(os.environ.get(
    "GEM5_BUILD", "/mnt/data2/cgc/cc-ep-v5-o3-n16-gem5-build"))
ZMQ_LIB = pathlib.Path(os.environ.get(
    "ZMQ_LIB", "/mnt/data2/cgc/.local/lib"))
CPUSET = os.environ.get("CPUSET", "0-31")
REPEATS = int(os.environ.get("REPEATS", "3"))
PROFILES = parse_profiles()
TCS = [int(value) for value in os.environ.get(
    "TC_LIST", "142 143 144 145 146 147").split()]
CASE_TIMEOUT = int(os.environ.get("CASE_TIMEOUT_SEC", "21600"))
STALL_TIMEOUT = int(os.environ.get("STALL_TIMEOUT_SEC", "1800"))
DISK_FLOOR_GB = int(os.environ.get("DISK_FLOOR_GB", "10"))
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
COMPRESS_LOGS = os.environ.get("COMPRESS_LOGS", "1") == "1"
TARGET_LINES = 98304
NAIVE_CAPACITY = 65536
HOT_PER_PLANE = {142: 32, 143: 137, 144: 192, 145: 136, 146: 192, 147: 136}


def run_output(command, cwd=ROOT):
    return subprocess.check_output(command, cwd=cwd, text=True).strip()


def atomic_json(path, payload):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    tmp.replace(path)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def repository_fingerprint(path):
    status = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=path)
    diff = subprocess.check_output(["git", "diff", "--binary", "HEAD"],
                                   cwd=path)
    untracked = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=path).split(b"\0")
    untracked_files = {}
    for encoded in untracked:
        if not encoded:
            continue
        relative = encoded.decode()
        untracked_files[relative] = sha256(path / relative)
    return {
        "head": run_output(["git", "rev-parse", "HEAD"], cwd=path),
        "status_sha256": hashlib.sha256(status).hexdigest(),
        "diff_sha256": hashlib.sha256(diff).hexdigest(),
        "untracked_files": untracked_files,
    }


def source_fingerprint():
    return {
        "main": repository_fingerprint(ROOT),
        "gem5": repository_fingerprint(ROOT / "gem5"),
        "ubio_sha256": sha256(ROOT / "build/bin/ubio"),
        "networksim_sha256": sha256(ROOT / "build/bin/networksim"),
        "gem5_sha256": sha256(GEM5_BUILD / "ARM/gem5.opt"),
    }


def profile_settings(profile):
    if profile == "naive":
        return "naive", "naive", "--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0"
    if profile == "spill-noopt":
        return "spill", "spill-noopt", "--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0"
    if profile == "optimized":
        return "spill", "optimized", "--silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1"
    raise ValueError(f"unknown profile: {profile}")


def case_key(case):
    return f"r{case['repeat']:02d}_tc{case['tc']}_{case['profile']}"


def build_cases():
    cases = []
    for repeat in range(1, REPEATS + 1):
        for tc_index, tc in enumerate(TCS):
            offset = (repeat - 1 + tc_index) % len(PROFILES)
            ordered = PROFILES[offset:] + PROFILES[:offset]
            for profile in ordered:
                cases.append({"repeat": repeat, "tc": tc, "profile": profile})
    return cases


CASES = build_cases()
CASES_ROOT = LOG_ROOT / "cases"
MATRIX = LOG_ROOT / "matrix.tsv"
PROGRESS = LOG_ROOT / "progress.json"
MANIFEST = LOG_ROOT / "manifest.json"
LOCK = LOG_ROOT / "runner.lock"


def pressure_flags(tc):
    pressure = TARGET_LINES - HOT_PER_PLANE[tc] * 16
    return (f"-DPORTABLE_PRESSURE_LINES={pressure} "
            f"-DPORTABLE_TARGET_FOOTPRINT_LINES={TARGET_LINES} "
            f"-DPORTABLE_NAIVE_CAPACITY_LINES={NAIVE_CAPACITY} "
            "-DPORTABLE_PRESSURE_LEVEL_PCT=150 -DPORTABLE_BATCHES=32")


def read_result(case):
    path = CASES_ROOT / case_key(case) / "result.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def write_progress(running=None):
    rows = []
    counts = {}
    for case in CASES:
        result = read_result(case)
        status = result.get("status", "PENDING") if result else "PENDING"
        counts[status] = counts.get(status, 0) + 1
        rows.append((status, case["repeat"], case["tc"], case["profile"],
                     result.get("elapsed_sec", 0) if result else 0,
                     result.get("log_dir", "") if result else "",
                     result.get("reason", "") if result else ""))
    text = "status\trepeat\ttc\tprofile\telapsed_sec\tlog_dir\treason\n"
    text += "".join("\t".join(str(value) for value in row) + "\n" for row in rows)
    MATRIX.write_text(text)
    atomic_json(PROGRESS, {
        "timestamp": datetime.datetime.now().astimezone().isoformat(),
        "counts": counts,
        "running": running,
        "total": len(CASES),
    })


def verify_pressure(case_dir, tc):
    expected = TARGET_LINES - HOT_PER_PLANE[tc] * 16
    records = []
    for path in case_dir.glob(f"simout_tc{tc}_node*.log"):
        for line in path.read_text(errors="replace").splitlines():
            if not line.startswith("[PORTABLE-PRESSURE]"):
                continue
            fields = {}
            for atom in line.split()[1:]:
                if "=" in atom:
                    key, value = atom.split("=", 1)
                    fields[key] = int(value)
            records.append(fields)
    return (len(records) == 16 and
            sorted(record.get("node") for record in records) == list(range(16)) and
            all(record.get("planes") == 16 and
                record.get("pressure_lines") == expected and
                record.get("total_unique_lines") == TARGET_LINES and
                record.get("target_footprint_lines") == TARGET_LINES and
                record.get("naive_capacity_lines") == NAIVE_CAPACITY and
                record.get("pressure_level_pct") == 150 and
                record.get("batches") == 32 for record in records))


def compress_logs(case_dir):
    if not COMPRESS_LOGS:
        return
    for path in case_dir.rglob("*.log"):
        compressed = path.with_suffix(path.suffix + ".gz")
        if compressed.exists():
            continue
        with path.open("rb") as source, gzip.open(compressed, "wb", 6) as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
        path.unlink()


def run_case(case, fingerprint):
    if source_fingerprint() != fingerprint:
        raise RuntimeError("source or binary fingerprint changed during matrix")
    key = case_key(case)
    case_dir = CASES_ROOT / key
    case_dir.mkdir(parents=True, exist_ok=True)
    policy, perf_profile, gem5_opts = profile_settings(case["profile"])
    token = zlib.crc32(f"{RUN_TAG}_{key}".encode()) & 0xffffffff
    run_id = f"n16f_{token:08x}_{case['tc']}"
    container = f"n16f-{token:08x}"
    env = [
        f"E2E_RUN_ID={run_id}",
        f"LOG_BASE=/perf-logs/cases/{key}",
        f"TIMEOUT_SEC={CASE_TIMEOUT}",
        "EP_CPU_MODEL=o3",
        "EP_SEQUENCER_MAX_OUTSTANDING=16",
        "EP_TRACE_PERF=off",
        "EP_SUPERVISOR=1",
        "EP_SUPERVISOR_INTERVAL=60",
        f"EP_SUPERVISOR_PROGRESS_STALL_SEC={STALL_TIMEOUT}",
        "EP_SUPERVISOR_LOG_CEIL_GB=8",
        f"EP_SUPERVISOR_DISK_FREE_GB={DISK_FLOOR_GB}",
        "PORTABLE_512K_DIR=1",
        f"WORKLOAD_CFLAGS={pressure_flags(case['tc'])}",
        f"EP_PERF_PROFILE={perf_profile}",
        f"UBCC_POLICY={policy}",
        f"UBCC_OPTS=--dir-overflow-policy={policy}",
        f"EP_GEM5_OPTS={gem5_opts}",
        "LD_LIBRARY_PATH=/workspace/thirdparty/zeromq/lib",
        "PYTHONDONTWRITEBYTECODE=1",
    ]
    command = [
        "docker", "run", "--rm", "--name", container, "--network", "none",
        "--cpuset-cpus", CPUSET,
        "-v", f"{ROOT}:/workspace",
        "-v", f"{GEM5_BUILD}:/gem5-build",
        "-v", f"{ZMQ_LIB}:/workspace/thirdparty/zeromq/lib:ro",
        "-v", f"{LOG_ROOT}:/perf-logs",
        "-w", "/workspace", "ubcc-dev:ubuntu20.04", "env", *env,
        "bash", "tests/e2e/run_multi.sh", "--16n1s", str(case["tc"]),
    ]
    if DRY_RUN:
        return {**case, "case_key": key, "status": "DRY_RUN", "reason": "",
                "elapsed_sec": 0, "return_code": None, "log_dir": str(case_dir),
                "command": shlex.join(command)}
    while shutil.disk_usage(LOG_ROOT).free < DISK_FLOOR_GB * 1024 ** 3:
        time.sleep(60)
    subprocess.run(["docker", "rm", "-f", container],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    started = time.monotonic()
    with (case_dir / "runner_stdout.log").open("w") as output:
        try:
            completed = subprocess.run(
                command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                timeout=CASE_TIMEOUT + 600)
            return_code = completed.returncode
            reason = f"exit={return_code}"
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "rm", "-f", container],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return_code = 124
            reason = "outer_timeout"
    elapsed = int(time.monotonic() - started)
    verify = case_dir / f"verify_tc{case['tc']}.log"
    passed = False
    if return_code == 0 and verify.exists():
        lines = verify.read_text(errors="replace").splitlines()
        passed = bool(lines and lines[-1] == f">>> TC{case['tc']} PASSED <<<")
    child_statuses = list((case_dir / f"child_status_tc{case['tc']}").glob("*.exit"))
    children_ok = (len(child_statuses) == 33 and
                   all(path.read_text().strip() == "0" for path in child_statuses))
    pressure_ok = verify_pressure(case_dir, case["tc"]) if passed else False
    source_unchanged = source_fingerprint() == fingerprint
    status = ("PASS" if passed and children_ok and pressure_ok and source_unchanged
              else "FAIL")
    if passed and not children_ok:
        reason += ";child_exit_mismatch"
    if passed and not pressure_ok:
        reason += ";pressure_manifest_mismatch"
    if not source_unchanged:
        reason += ";source_or_binary_changed"
    result = {
        **case,
        "case_key": key,
        "status": status,
        "reason": reason,
        "elapsed_sec": elapsed,
        "return_code": return_code,
        "cpuset": CPUSET,
        "log_dir": str(case_dir),
        "timestamp": datetime.datetime.now().astimezone().isoformat(),
        "pressure_lines": TARGET_LINES - HOT_PER_PLANE[case["tc"]] * 16,
        "source_fingerprint": fingerprint,
    }
    atomic_json(case_dir / "result.json", result)
    compress_logs(case_dir)
    return result


for required in (GEM5_BUILD / "ARM/gem5.opt", ROOT / "build/bin/ubio",
                 ROOT / "build/bin/networksim", ZMQ_LIB):
    if not required.exists():
        raise SystemExit(f"required path missing: {required}")

LOG_ROOT.mkdir(parents=True, exist_ok=True)
CASES_ROOT.mkdir(exist_ok=True)
fingerprint = source_fingerprint()
manifest = {
    "run_tag": RUN_TAG,
    "created": datetime.datetime.now().astimezone().isoformat(),
    "topology": "16 nodes x 1 socket",
    "cpu_model": "o3",
    "sequencer_max_outstanding": 16,
    "cpuset": CPUSET,
    "profiles": PROFILES,
    "repeats": REPEATS,
    "testcases": TCS,
    "total_cases": len(CASES),
    "target_footprint_lines": TARGET_LINES,
    "naive_capacity_lines": NAIVE_CAPACITY,
    "pressure_level_pct": 150,
    "case_timeout_sec": CASE_TIMEOUT,
    "source_fingerprint": fingerprint,
    "cases": [{**case, "case_key": case_key(case),
               "pressure_lines": TARGET_LINES - HOT_PER_PLANE[case["tc"]] * 16}
              for case in CASES],
}
if MANIFEST.exists():
    previous = json.loads(MANIFEST.read_text())
    if previous.get("source_fingerprint") != fingerprint or previous.get("cases") != manifest["cases"]:
        raise SystemExit("existing matrix manifest differs from current source or case list")
else:
    atomic_json(MANIFEST, manifest)

with LOCK.open("w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    write_progress()
    if DRY_RUN:
        dry_cases = [run_case(case, fingerprint) for case in CASES]
        print(json.dumps({**manifest, "dry_run_cases": dry_cases},
                         indent=2, sort_keys=True))
        sys.exit(0)
    for case in CASES:
        existing = read_result(case)
        if existing and existing.get("status") == "PASS":
            continue
        write_progress(case_key(case))
        result = run_case(case, fingerprint)
        write_progress()
        summary_path = LOG_ROOT / "summary.json"
        with summary_path.open("w") as output:
            subprocess.run(
                [sys.executable, str(ROOT / "scripts/summarize_n16_formal_perf.py"),
                 str(LOG_ROOT)], stdout=output, check=True)
        if result["status"] != "PASS" and os.environ.get("CONTINUE_ON_FAIL", "0") != "1":
            raise SystemExit(f"matrix stopped after {case_key(case)}: {result['reason']}")

failures = sum(1 for case in CASES
               if not read_result(case) or read_result(case).get("status") != "PASS")
print(f"16N1S formal matrix complete: total={len(CASES)} non_pass={failures}")
sys.exit(1 if failures else 0)
