#!/usr/bin/env python3
"""Focused, resumable TC143 3n2s/P175 runner.

The runner only orchestrates Docker.  Compilation performed by run_multi.sh is
therefore inside the per-role container; this file never builds on the host.
"""

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import threading
import time


ROOT = pathlib.Path(__file__).resolve().parents[1]
IMAGE = "ubcc-dev:ubuntu20.04"
ROLES = ("naive", "spill", "ideal")
PRESSURE = 113866
TARGET = 114688
CAPACITY = 65536
PCT = 175
BATCHES = 32
INNER_TIMEOUT = 3480
OUTER_TIMEOUT = 3570
HARD_WALL = 3600
POLL_SEC = 30
USEFUL_STALL_SEC = 900
MEMORY = "24g"
MACROS = (f"-DPORTABLE_PRESSURE_LINES={PRESSURE} "
          f"-DPORTABLE_TARGET_FOOTPRINT_LINES={TARGET} "
          f"-DPORTABLE_NAIVE_CAPACITY_LINES={CAPACITY} "
          f"-DPORTABLE_PRESSURE_LEVEL_PCT={PCT} "
          f"-DPORTABLE_BATCHES={BATCHES}")
FINGERPRINT_FILES = (
    "scripts/run_tc143_3n2s_p175.py",
    "scripts/compile_workload.sh",
    "tests/e2e/run_multi.sh",
    "tests/e2e/test_e2e.py",
    "tests/e2e/workloads/e2e_common.h",
    "tests/e2e/workloads/portable_large_workload.h",
    "tests/e2e/workloads/e2e_tc143_db_btree_traversal.c",
    "modules/ubiomodule/UBCCController.cc",
    "modules/ubiomodule/UBCCController.hh",
    "modules/ubiomodule/ubio_main.cc",
    "gem5/src/arch/arm/linux/se_workload.cc",
    "gem5/src/mem/ruby/protocol/chi/ep/EPRNFController.cc",
    "gem5/src/sim/sync_wait.cc",
)
PRESSURE_RE = re.compile(
    r"\[PORTABLE-PRESSURE\].*?pressure_lines=(\d+).*?"
    r"total_unique_lines=(\d+).*?naive_capacity_lines=(\d+).*?"
    r"target_footprint_lines=(\d+).*?pressure_level_pct=(\d+).*?batches=(\d+)")
PROGRESS_RE = re.compile(
    r"\[WORKLOAD-PROGRESS\].*?node=(\d+).*?event=(\S+).*?"
    r"completed=(\d+) target=(\d+) milestone=(\d+)")
BAD_RE = re.compile(r"(?:fatal|panic|invalid transition)", re.IGNORECASE)
OUTER_RE = re.compile(r"\[EP-PERF\] kind=outer node=(\d+).*?latency_ps=(\d+)")


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def git_sha(path):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=path,
                          check=True, text=True, capture_output=True).stdout.strip()


def source_digest():
    digest = hashlib.sha256()
    for relative in FINGERPRINT_FILES:
        path = ROOT / relative
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def role_config(role):
    if role == "naive":
        return {"profile": "naive", "policy": "naive", "opts": ""}
    if role == "spill":
        # PORTABLE_512K_DIR supplies the exact 512 KiB/60 KiB/no-batch options.
        return {"profile": "spill-noopt", "policy": "spill", "opts": ""}
    return {
        "profile": "spill-noopt", "policy": "spill",
        # run_multi's portable defaults precede UBCC_OPTS; command-line parsers
        # use the final occurrence.  Record and verify that effective contract.
        "opts": ("--bloom-bytes=61440 --sram-bytes=2097152 --ways=32 "
                 "--set-bits=0 --dir-overflow-policy=spill --batch-rs=0 "
                 "--allow-oversized-resident-dir-for-test"),
    }


def fingerprint(role):
    value = {
        "repo_sha": git_sha(ROOT), "gem5_sha": git_sha(ROOT / "gem5"),
        "image": IMAGE, "tc": 143, "topology": "3n2s", "role": role,
        "cpu_model": "hybrid", "sequencer_outstanding": 16,
        "dsm_delay_ps": 68000, "macros": MACROS,
        "role_config": role_config(role), "inner_timeout": INNER_TIMEOUT,
        "source_digest": source_digest(),
    }
    value["digest"] = hashlib.sha256(
        json.dumps(value, sort_keys=True).encode()).hexdigest()
    return value


def parse_slots(values):
    if values:
        if len(values) > 3:
            raise ValueError("at most three --cpu-slots may be supplied")
        slots = values
    else:
        allowed = sorted(os.sched_getaffinity(0))
        # 3n2s launches 12 substantial children. Give each role 12 CPUs and,
        # on a 109-GiB host, conservatively admit at most two 24-GiB roles.
        if len(allowed) < 24:
            raise ValueError("default scheduling needs at least 24 available CPUs; supply --cpu-slots")
        slots = [compress_cpus(allowed[:12]), compress_cpus(allowed[12:24])]
    expanded = [expand_cpuset(slot) for slot in slots]
    if any(not item for item in expanded):
        raise ValueError("CPU slots must not be empty")
    for index, left in enumerate(expanded):
        for right in expanded[index + 1:]:
            if left & right:
                raise ValueError("CPU slots must not overlap")
    return slots


def expand_cpuset(text):
    result = set()
    for part in text.split(","):
        bounds = part.strip().split("-", 1)
        lo = int(bounds[0]); hi = int(bounds[-1])
        if lo < 0 or hi < lo:
            raise ValueError(f"invalid CPU slot: {text}")
        result.update(range(lo, hi + 1))
    return result


def compress_cpus(cpus):
    runs = []
    for cpu in cpus:
        if not runs or cpu != runs[-1][-1] + 1:
            runs.append([cpu])
        else:
            runs[-1].append(cpu)
    return ",".join(str(run[0]) if len(run) == 1 else f"{run[0]}-{run[-1]}"
                    for run in runs)


def run_simout_paths(case):
    manifest = case / "role_manifest.json"
    if not manifest.is_file():
        return []
    try:
        run_id = json.loads(manifest.read_text()).get("container_name")
    except (OSError, json.JSONDecodeError):
        return []
    if not run_id:
        return []
    return sorted((ROOT / "build" / "runs" / run_id / "tc143" /
                   "m5out").glob("node*/simout_n*"))


def latest_workload_progress(case):
    planes = {}
    for path in run_simout_paths(case):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        matches = PROGRESS_RE.findall(text)
        for node, event, completed, target, milestone in matches:
            planes[int(node)] = {
                "event": event, "completed": int(completed),
                "target": int(target),
                "milestone": int(milestone), "source": str(path),
            }
    if not planes:
        return None
    measuring = [item for item in planes.values()
                 if item.get("event") == "measure_batch_done"]
    considered = measuring if measuring else list(planes.values())
    slow = min(considered, key=lambda item: (
        item["completed"] / item["target"] if item["target"] else 1.0,
        item["milestone"],
    ))
    return {**slow, "phase": "measurement" if measuring else "setup",
            "reporting_planes": len(considered), "planes": planes}


def inspect_container(name):
    try:
        proc = subprocess.run(
            ["docker", "inspect", "--format", "{{json .State}}", name],
            text=True, capture_output=True, timeout=10)
    except subprocess.TimeoutExpired:
        return {"exists": None, "error": "docker inspect timeout"}
    if proc.returncode:
        return {"exists": False}
    try:
        state = json.loads(proc.stdout)
    except json.JSONDecodeError:
        state = {"raw": proc.stdout.strip()}
    state["exists"] = True
    return state


def cleanup(name):
    for command in (["docker", "stop", "--time", "10", name],
                    ["docker", "rm", "-f", name]):
        try:
            subprocess.run(command, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=15)
        except subprocess.TimeoutExpired:
            pass
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if not inspect_container(name).get("exists"):
            return
        time.sleep(0.2)


def read_json_lines(path):
    records = []
    if path.is_file():
        for line in path.read_text(errors="replace").splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


def verify(case, role, fp, return_code):
    errors = []
    verifier = case / "verify_tc143.log"
    if not (verifier.is_file() and verifier.read_text(errors="replace").rstrip().endswith(
            ">>> TC143 PASSED <<<")):
        errors.append("missing verifier PASS sentinel")
    exits = sorted((case / "child_status_tc143").glob("*.exit"))
    if len(exits) != 10 or any(path.read_text().strip() != "0" for path in exits):
        errors.append("expected exactly 10 zero child exits")
    ubio = []
    all_text = []
    for path in case.glob("ubio_tc143_n*_s*/stdout.log"):
        text = path.read_text(errors="replace"); all_text.append(text)
        first = text.splitlines()[:1]
        if first and "[PROCESS-MANIFEST]" in first[0]:
            try: ubio.append(json.loads(first[0].split("[PROCESS-MANIFEST]", 1)[1]))
            except json.JSONDecodeError: pass
    if len(ubio) != 6 or any(item.get("dsm_data_delay_ps") != 68000 for item in ubio):
        errors.append("expected six UBIO manifests with DSM delay 68000")
    pressure = []
    for path in run_simout_paths(case):
        text = path.read_text(errors="replace"); all_text.append(text)
        pressure.extend(PRESSURE_RE.findall(text))
    expected_pressure = (str(PRESSURE), str(TARGET), str(CAPACITY),
                         str(TARGET), str(PCT), str(BATCHES))
    if len(pressure) != 6 or any(tuple(item) != expected_pressure for item in pressure):
        errors.append("expected six exact portable pressure records")
    launches = read_json_lines(case / "launch_commands_tc143.jsonl")
    gems = [row for row in launches if row.get("component") == "gem5"]
    ubio_launches = [row for row in launches if row.get("component") == "ubio"]
    expected_options = {
        "naive": {"--bloom-bytes": "0", "--sram-bytes": "524288",
                  "--ways": "0", "--dir-overflow-policy": "naive", "--batch-rs": "0"},
        "spill": {"--bloom-bytes": "61440", "--sram-bytes": "524288",
                  "--ways": "0", "--dir-overflow-policy": "spill", "--batch-rs": "0"},
        "ideal": {"--bloom-bytes": "61440", "--sram-bytes": "2097152",
                  "--ways": "32", "--dir-overflow-policy": "spill", "--batch-rs": "0"},
    }[role]
    def effective_options(argv):
        found = {}
        for token in argv:
            for key in expected_options:
                if token.startswith(key + "="):
                    found[key] = token.split("=", 1)[1]
        return found
    if len(ubio_launches) != 6 or any(effective_options(row.get("argv", [])) != expected_options
                                      for row in ubio_launches):
        errors.append("six UBIO launches do not have exact effective role options")
    manifests = []
    for path in case.glob("gem5_tc143_node*/stdout.log"):
        for line in path.read_text(errors="replace").splitlines():
            if line.startswith("[PROCESS-MANIFEST] "):
                try: manifests.append(json.loads(line.split("[PROCESS-MANIFEST]", 1)[1]))
                except json.JSONDecodeError: pass
                break
    def hybrid_ok(item):
        hybrid = item.get("hybrid", {})
        return (item.get("cpu_model") == "hybrid" and
                item.get("setup_cpu_model", item.get("setup_model", hybrid.get("setup"))) == "timing" and
                item.get("measurement_cpu_model", item.get("measurement_model", hybrid.get("measurement"))) == "o3" and
                item.get("sequencer_max_outstanding") == 16 and
                item.get("switches_expected") == 1 and
                item.get("switches_observed") == 1 and
                item.get("hybrid_switches_complete") is True and
                item.get("final_cpu") == "o3")
    if len(manifests) != 3 or any(not hybrid_ok(item) for item in manifests):
        errors.append("expected three hybrid gem5 manifests (setup timing, measurement o3)")
    cfg = role_config(role)
    role_manifest = case / "role_manifest.json"
    if not role_manifest.is_file() or json.loads(role_manifest.read_text()).get("fingerprint") != fp:
        errors.append("role manifest/fingerprint mismatch")
    combined = "\n".join(all_text + [(case / "coordinator.log").read_text(errors="replace")])
    if BAD_RE.search(combined):
        errors.append("fatal/panic/invalid transition found")
    # Capacity is extracted by the established parser, not inferred from config.
    try:
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        import extract_metric123_from_logs as extractor
        capacity = extractor.parse_capacity(list(case.glob("ubio_tc143_n0_s0/stdout.log*")))
        effective = capacity.get("effective_unique")
        if not isinstance(effective, (int, float)) or effective <= 0:
            errors.append("missing capacity evidence")
        elif role == "naive" and effective > CAPACITY:
            errors.append("naive capacity exceeds configured capacity")
        elif role != "naive" and effective < TARGET:
            errors.append("spill/ideal capacity below target footprint")
    except Exception as exc:  # verifier must turn malformed evidence into FAIL
        capacity = {"error": str(exc)}; errors.append("capacity extraction failed")
    if return_code != 0:
        errors.append(f"docker command returned {return_code}")
    return {"status": "PASS" if not errors else "FAIL", "errors": errors,
            "capacity": capacity, "gem5_launch_count": len(gems),
            "ubio_launch_count": len(ubio_launches),
            "child_count": len(exits), "pressure_record_count": len(pressure),
            "ubio_manifest_count": len(ubio)}


def extract_outer(case):
    values = []
    for path in case.glob("gem5_tc143_node*/stderr.log"):
        for match in OUTER_RE.finditer(path.read_text(errors="replace")):
            values.append(int(match.group(2)) / 1000.0)
    return {"samples": len(values),
            "mean_ns": sum(values) / len(values) if values else None}


def run_role(output, role, slot, update):
    hard_start = time.monotonic()
    case = output / "cases" / role
    fp = fingerprint(role); result_path = case / "result.json"
    if result_path.is_file():
        old = json.loads(result_path.read_text())
        if old.get("status") == "PASS" and old.get("fingerprint") == fp:
            update(role, {"state": "resumed", "slot": slot}); return old
    if case.exists():
        archive_root = output / "attempts" / role
        archive_root.mkdir(parents=True, exist_ok=True)
        archive = archive_root / dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        shutil.move(str(case), str(archive))
    case.mkdir(parents=True, exist_ok=True)
    name = f"tc143-3n2s-p175-{role}-{fp['digest'][:10]}"
    cleanup(name)
    cfg = role_config(role)
    role_record = {"role": role, "fingerprint": fp, "effective": cfg,
                   "container_name": name, "cpu_slot": slot}
    atomic_json(case / "role_manifest.json", role_record)
    command = ["docker", "run", "--name", name, "--network", "none",
               "--cpuset-cpus", slot, "--memory", MEMORY,
               "-v", f"{ROOT}:/workspace", "-w", "/workspace", IMAGE, "env",
               f"E2E_RUN_ID={name}", f"EP_DOCKER_CPUSET={slot}",
               f"LOG_BASE=/workspace/{case.relative_to(ROOT)}",
               f"TIMEOUT_SEC={INNER_TIMEOUT}", "EP_SUPERVISOR=1",
               f"EP_SUPERVISOR_INTERVAL={POLL_SEC}",
               "EP_SUPERVISOR_PROGRESS_STALL_SEC=900",
               "EP_SUPERVISOR_STARTUP_STALL_SEC=180",
               "EP_SUPERVISOR_ETA_CALIBRATION_SEC=300",
                "EP_SUPERVISOR_ETA_BUDGET_PCT=125",
               "EP_CPU_MODEL=hybrid", "EP_SEQUENCER_MAX_OUTSTANDING=16",
               "EP_DSM_DATA_DELAY_PS=68000", "EP_TRACE_PERF=off",
               "EP_HA_PROFILE=ubcc", "OURCC_CLEAR_PROFILE=ack",
               f"EP_PERF_PROFILE={cfg['profile']}", f"UBCC_POLICY={cfg['policy']}",
               f"UBCC_OPTS={cfg['opts']}", "PORTABLE_512K_DIR=1",
               f"WORKLOAD_CFLAGS={MACROS}",
               "EP_GEM5_OPTS=--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0",
               "bash", "tests/e2e/run_multi.sh", "--3n2s", "143"]
    start = time.monotonic(); last_useful = start; last_pair = None
    # Reserve 30 seconds from the absolute 60-minute wall for exact-name
    # docker stop/rm. Initial stale-container cleanup is included as well.
    process_budget = min(OUTER_TIMEOUT,
                         max(1.0, HARD_WALL - (start - hard_start) - 30.0))
    with (case / "coordinator.log").open("w") as log:
        proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        rc = None
        while rc is None:
            rc = proc.poll()
            progress = latest_workload_progress(case)
            pair = ((progress or {}).get("milestone"),
                    (progress or {}).get("reporting_planes"))
            if progress and pair != last_pair:
                last_useful = time.monotonic(); last_pair = pair
            child_failure = None
            for path in (case / "child_status_tc143").glob("*.exit"):
                value = path.read_text().strip()
                if value != "0": child_failure = f"{path.name}={value}"; break
            elapsed = time.monotonic() - start
            update(role, {"state": "running" if rc is None else "exited",
                          "slot": slot, "elapsed_sec": round(elapsed, 1),
                          "latest_workload_progress": progress,
                          "last_useful_progress_age_sec": round(time.monotonic()-last_useful, 1),
                          "container": inspect_container(name),
                          "child_failure": child_failure})
            if rc is not None: break
            useful_stall = (progress is not None and
                            time.monotonic() - last_useful >= USEFUL_STALL_SEC and
                            progress["completed"] < progress["target"])
            if child_failure or useful_stall or elapsed >= process_budget:
                cleanup(name)
                try: rc = proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill(); rc = proc.wait()
                if elapsed >= process_budget: rc = 124
                elif useful_stall: rc = 125
                break
            time.sleep(min(POLL_SEC, max(0.1, process_budget - elapsed)))
    cleanup(name)
    checked = verify(case, role, fp, rc)
    result = {**checked, "role": role, "fingerprint": fp,
              "outer": extract_outer(case),
              "return_code": rc, "elapsed_sec": round(time.monotonic()-start, 1),
              "hard_wall_elapsed_sec": round(time.monotonic()-hard_start, 1),
              "finished_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    atomic_json(result_path, result); update(role, {"state": "finished", "result": result})
    return result


def summarize(output, results):
    summary = {"status": "PASS" if all(
                   r in results and results[r]["status"] == "PASS"
                   for r in ROLES) else "FAIL",
               "roles": results, "capacity_ratio": None, "spill_ideal_delta": None}
    if summary["status"] == "PASS":
        values = {r: results[r]["capacity"]["effective_unique"] for r in ROLES}
        summary["capacity_ratio"] = values["spill"] / values["naive"] if values["naive"] else None
        spill_mean = results["spill"]["outer"]["mean_ns"]
        ideal_mean = results["ideal"]["outer"]["mean_ns"]
        summary["spill_ideal_delta"] = {
            "capacity_lines": values["spill"] - values["ideal"],
            "outer_mean_ns": (spill_mean - ideal_mean
                              if spill_mean is not None and ideal_mean is not None else None),
        }
    atomic_json(output / "summary.json", summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=pathlib.Path, required=True)
    parser.add_argument("--cpu-slots", nargs="+", metavar="CPUSET")
    parser.add_argument("--roles", nargs="+", choices=ROLES,
                        default=list(ROLES))
    args = parser.parse_args(argv)
    output = args.output_root.expanduser().resolve()
    try: output.relative_to(ROOT)
    except ValueError: parser.error("--output-root must be inside the workspace")
    try: slots = parse_slots(args.cpu_slots)
    except (ValueError, OSError) as exc: parser.error(str(exc))
    output.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock(); states = {}
    def update(role, state):
        with lock:
            states[role] = state
            atomic_json(output / "progress.json", {
                "phase": "running", "poll_interval_sec": POLL_SEC,
                "roles": states, "updated_at": dt.datetime.now(dt.timezone.utc).isoformat()})
    results = {}

    requested = tuple(dict.fromkeys(args.roles))
    if requested != ROLES:
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(len(slots), len(requested))) as pool:
            pending_roles = iter(requested)
            active = {}
            for slot in slots:
                try: role = next(pending_roles)
                except StopIteration: break
                active[pool.submit(run_role, output, role, slot, update)] = slot
            while active:
                done, _ = concurrent.futures.wait(
                    active, return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    slot = active.pop(future)
                    result = future.result()
                    results[result["role"]] = result
                    try: role = next(pending_roles)
                    except StopIteration: continue
                    active[pool.submit(run_role, output, role, slot, update)] = slot
        status = "PASS" if all(result["status"] == "PASS"
                               for result in results.values()) else "FAIL"
        atomic_json(output / "progress.json", {
            "phase": "done", "roles": states, "summary_status": status})
        return 0 if status == "PASS" else 1

    naive = run_role(output, "naive", slots[0], update)
    results["naive"] = naive
    if naive["status"] != "PASS":
        summary = summarize(output, results)
        atomic_json(output / "progress.json", {
            "phase": "done", "roles": states,
            "summary_status": summary["status"]})
        return 1

    # Spill and ideal are independent once the destructive naive path passes.
    # With two slots they run in parallel; with one, FIRST_COMPLETED immediately
    # backfills ideal after spill.
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(slots)) as pool:
        pending_roles = iter(("spill", "ideal"))
        active = {}
        for slot in slots:
            try: role = next(pending_roles)
            except StopIteration: break
            active[pool.submit(run_role, output, role, slot, update)] = slot
        while active:
            done, _ = concurrent.futures.wait(active,
                return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                slot = active.pop(future); result = future.result()
                results[result["role"]] = result
                try: role = next(pending_roles)
                except StopIteration: continue
                active[pool.submit(run_role, output, role, slot, update)] = slot
    summary = summarize(output, results)
    atomic_json(output / "progress.json", {"phase": "done", "roles": states,
                                            "summary_status": summary["status"]})
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
