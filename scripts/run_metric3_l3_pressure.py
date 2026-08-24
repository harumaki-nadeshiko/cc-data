#!/usr/bin/env python3
"""Resumable staged Metric3 L3-pressure AB/BA experiment.

Stage 1 executes repeat/pair 1 only. Stage 2 is a separate explicit invocation,
requires a CONTINUE gate JSON, and executes repeats/pairs 2 through 5 only.
All simulator/build activity is delegated to run_multi.sh inside the required
ubcc-dev:ubuntu20.04 container.
"""

import argparse
import concurrent.futures
import hashlib
import json
import pathlib
import subprocess
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
IMAGE = "ubcc-dev:ubuntu20.04"
TCS = tuple(range(228, 236))
ARMS = ("ourcc", "ha-vi")


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


def result_complete(path, expected):
    if not path.is_file():
        return False
    try:
        row = json.loads(path.read_text())
        return (row.get("status") == "PASS" and row.get("return_code") == 0 and
                all(row.get(key) == value for key, value in expected.items()))
    except (OSError, ValueError):
        return False


def marker_rows(log_dir, prefix, phase):
    rows = []
    for path in sorted(log_dir.glob("simout_tc*_node*.log")):
        for line in path.read_text(errors="replace").splitlines():
            if not line.startswith(prefix):
                continue
            values = {atom.split("=", 1)[0]: atom.split("=", 1)[1].rstrip(",")
                      for atom in line.split() if "=" in atom}
            if values.get("phase") == phase:
                rows.append(values)
    return rows


def aggregate(log_dir, prefix, phase, value_name):
    rows = marker_rows(log_dir, prefix, phase)
    if not rows:
        raise RuntimeError(f"missing {prefix} phase={phase}")
    frequency = {int(row["counter_frequency_hz"]) for row in rows}
    if len(frequency) != 1:
        raise RuntimeError(f"frequency mismatch phase={phase}")
    count_name = "operations" if prefix == "[GUEST-TIMER]" else "samples"
    count = sum(int(row[count_name]) for row in rows)
    if value_name == "counter_ticks":
        value = sum(int(row[value_name]) for row in rows) / count
    else:
        value = sum(float(row[value_name]) * int(row[count_name]) for row in rows) / count
    hz = frequency.pop()
    return {"ticks_per_operation": value, "counter_frequency_hz": hz,
            "ns_per_operation": value * 1.0e9 / hz}


def max_timer(log_dir, phase):
    rows = marker_rows(log_dir, "[GUEST-TIMER]", phase)
    if not rows:
        raise RuntimeError(f"missing [GUEST-TIMER] phase={phase}")
    frequencies = {int(row["counter_frequency_hz"]) for row in rows}
    if len(frequencies) != 1:
        raise RuntimeError(f"frequency mismatch phase={phase}")
    value = max(int(row["counter_ticks"]) / int(row["operations"])
                for row in rows)
    hz = frequencies.pop()
    return {"ticks_per_operation": value, "counter_frequency_hz": hz,
            "ns_per_operation": value * 1.0e9 / hz}


def metrics(log_dir, tc):
    registry = {
        228: (("remote_read", "[GUEST-TIMER]", "topology_remote_read", "counter_ticks"),),
        229: (("ownership_handoff", "[GUEST-TIMER]", "topology_ownership_handoff", "counter_ticks"),),
        230: (("shared_to_writer", "[GUEST-TIMER]", "topology_all_sharer_to_writer", "counter_ticks"),),
        231: (("clean_shared_control", "[GUEST-TIMER]", "clean_shared_read_service", "counter_ticks"),),
        232: (("hot_key_read", "[GUEST-TIMER]", "hot_key_read_service", "counter_ticks"),
              ("hot_key_write", "[GUEST-TIMER]", "hot_key_write_service", "counter_ticks")),
        233: (("producer_consumer_load", "[PERF-LATENCY]", "producer_consumer_load", "mean"),
              ("producer_consumer_service", "[GUEST-TIMER]", "producer_consumer_service", "counter_ticks")),
        234: (("queued_token_end_to_end", "[GUEST-TIMER]", "queued_token_end_to_end", "counter_ticks"),
              ("queued_token_store", "[GUEST-TIMER]", "queued_token_store", "counter_ticks")),
        235: (("catalog_kv_service", "[GUEST-TIMER]", "catalog_kv_service", "counter_ticks"),),
    }
    output = {name: aggregate(log_dir, prefix, phase, field)
              for name, prefix, phase, field in registry[tc]}
    if tc == 235:
        output["catalog_kv_end_to_end"] = max_timer(
            log_dir, "catalog_kv_end_to_end")
    return output


def run_arm(root, pair, tc, level, order, arm, timeout, seed, l3_size,
            l3_assoc, experiment_mode, directory_pressure_lines, cpuset):
    pair_id = f"r{pair:02d}_p{level}_tc{tc}"
    log_dir = root / "cases" / pair_id / arm
    result_path = log_dir / "result.json"
    expected = {"pair": pair, "tc": tc, "pressure_level": level,
                "arm": arm, "l3_size": l3_size, "l3_assoc": l3_assoc,
                "seed": seed, "experiment_mode": experiment_mode,
                "directory_pressure_lines": directory_pressure_lines}
    if result_complete(result_path, expected):
        return json.loads(result_path.read_text())
    run_id = "m3l3_" + hashlib.sha256(f"{root.name}:{pair_id}:{arm}".encode()).hexdigest()[:16]
    profile = "ubcc" if arm == "ourcc" else "ha-vi"
    clear = "lossless-oneway" if arm == "ourcc" else "ack"
    container_log = "/workspace/" + str(log_dir.relative_to(ROOT))
    env = [
        f"E2E_RUN_ID={run_id}", f"LOG_BASE={container_log}", f"TIMEOUT_SEC={timeout}",
        "STALL_TIMEOUT_SEC=1800", "EP_CPU_MODEL=o3", "EP_SEQUENCER_MAX_OUTSTANDING=16",
        "EP_TRACE_PERF=off", "EP_PERF_PROFILE=spill-noopt", "UBCC_POLICY=spill",
        "UBCC_OPTS=--dir-overflow-policy=spill", "EP_GEM5_OPTS=--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0",
        f"EP_HA_PROFILE={profile}", f"OURCC_CLEAR_PROFILE={clear}",
        f"EP_L3_SIZE={l3_size}", f"EP_L3_ASSOC={l3_assoc}", f"L3_PRESSURE_LEVEL={level}",
        f"METRIC3_L3_SEED={seed}", "HA_EXACT_BYTES=134217728", "HA_MAX_ACTIVE=256",
        f"METRIC3_L3_EXPERIMENT_MODE={experiment_mode}",
        f"L3_DIRECTORY_PRESSURE_LINES={directory_pressure_lines}",
        "EP_TRACK_L3_OCCUPANCY=1",
        "GEM5_DEBUG_FLAGS=RubyCHIGeneric",
        "HA_MAX_QUEUE=8", "EP_LINK_LATENCY_PS=2500", "EP_SYNC_INTERVAL_PS=2500",
        "EP_PORT_HWM=8192", "EP_NSIM_MAX_PENDING=65536",
        "LD_LIBRARY_PATH=/workspace/thirdparty/zeromq/lib",
    ]
    command = ["docker", "run", "--rm", "--network", "none",
               f"--cpuset-cpus={cpuset}",
               "-v", f"{ROOT}:/workspace", "-v", f"{ROOT / 'gem5/gem5'}:/workspace/gem5",
               "-v", "/mnt/data2/cgc/.local/lib:/workspace/thirdparty/zeromq/lib:ro",
               "-w", "/workspace", IMAGE, "env", *env,
               "bash", "tests/e2e/run_multi.sh", "--2n1s", str(tc)]
    log_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with (log_dir / "coordinator.log").open("w") as stream:
        status = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT).returncode
    verifier = log_dir / f"verify_tc{tc}.log"
    verifier_pass = verifier.is_file() and verifier.read_text(errors="replace").rstrip().endswith(
        f">>> TC{tc} PASSED <<<")
    elf = ROOT / "build/runs" / run_id / "workload.elf"
    row = {"pair_id": pair_id, "pair": pair, "tc": tc, "pressure_level": level,
           "order": order, "arm": arm, "ha_profile": profile, "clear_profile": clear,
           "run_id": run_id, "return_code": status, "verifier_pass": verifier_pass,
           "status": "PASS" if status == 0 and verifier_pass else "FAIL",
           "elapsed_sec": time.time() - started, "log_dir": str(log_dir),
           "l3_size": l3_size, "l3_assoc": l3_assoc, "seed": seed,
           "experiment_mode": experiment_mode,
           "directory_pressure_lines": directory_pressure_lines,
           "workload_elf": str(elf), "workload_elf_sha256": sha256(elf) if elf.is_file() else None}
    if row["status"] == "PASS":
        try:
            row["metrics"] = metrics(log_dir, tc)
        except Exception as error:
            row["status"] = "FAIL"
            row["reason"] = str(error)
    atomic_json(result_path, row)
    return row


def run_pair_job(root, pair, level, tc, index, timeout, base_seed, l3_size,
                 l3_assoc, experiment_mode, directory_pressure_lines, cpuset):
    order = "AB" if (pair + index + (level == 150)) % 2 else "BA"
    sequence = ARMS if order == "AB" else tuple(reversed(ARMS))
    pair_seed = base_seed + pair * 10000 + level * 10 + tc
    rows = [run_arm(root, pair, tc, level, order, arm, timeout, pair_seed,
                    l3_size, l3_assoc, experiment_mode,
                    directory_pressure_lines, cpuset)
            for arm in sequence]
    hashes = {row.get("workload_elf_sha256") for row in rows}
    if None in hashes or len(hashes) != 1:
        raise RuntimeError(f"ELF identity mismatch r{pair} p{level} TC{tc}")
    return rows


def make_manifest(root, pairs, levels, testcases, l3_size, l3_assoc, seed,
                  experiment_mode, directory_pressure_lines):
    pairs = tuple(pairs)
    samples = []
    for pair in pairs:
        for level in levels:
            for index, tc in enumerate(testcases):
                order = "AB" if (pair + index + (level == 150)) % 2 else "BA"
                pair_id = f"r{pair:02d}_p{level}_tc{tc}"
                arms = {}
                for arm in ARMS:
                    result = root / "cases" / pair_id / arm / "result.json"
                    arms[arm] = {"result": str(result.relative_to(root)),
                                 "log_dir": str(result.parent.relative_to(root))}
                pair_seed = seed + pair * 10000 + level * 10 + tc
                samples.append({"sample_id": pair_id, "pair_id": pair_id, "pair": pair,
                                 "tc": tc, "order": order, "pressure_level": level,
                                 "seed": pair_seed,
                                 "arms": arms})
    return {"schema_version": 2, "experiment_id": root.name,
            "expected_repeats": len(pairs), "testcases": list(testcases),
            "l3": {"size": l3_size, "assoc": l3_assoc}, "pressure_levels": list(levels),
            "seed": seed, "experiment_mode": experiment_mode,
            "directory_pressure_lines": directory_pressure_lines,
            "samples": samples}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("stage1", "stage2"), required=True)
    parser.add_argument("--output-root", type=pathlib.Path, required=True)
    parser.add_argument("--stage1-gate", type=pathlib.Path)
    parser.add_argument("--pressure-levels", default="100,150")
    parser.add_argument("--l3-size", default="256kB")
    parser.add_argument("--l3-assoc", type=int, default=16)
    parser.add_argument("--seed", type=int, default=228235)
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--experiment-mode", choices=("l3-only", "l3-offload"),
                        default="l3-only")
    parser.add_argument("--directory-pressure-lines", type=int, default=61440)
    parser.add_argument("--max-parallel", type=int, default=3)
    parser.add_argument("--cpu-sets", default="0-9,10-19,20-29")
    parser.add_argument("--tc-list", default=",".join(str(tc) for tc in TCS))
    args = parser.parse_args()
    root = args.output_root.resolve()
    try: root.relative_to(ROOT)
    except ValueError: parser.error("--output-root must be inside the workspace")
    levels = tuple(int(item) for item in args.pressure_levels.split(","))
    if not levels or any(item not in (100, 150) for item in levels):
        parser.error("--pressure-levels must contain only 100 and/or 150")
    directory_pressure_lines = (0 if args.experiment_mode == "l3-only"
                                else args.directory_pressure_lines)
    if args.experiment_mode == "l3-offload" and directory_pressure_lines <= 57344:
        parser.error("l3-offload requires --directory-pressure-lines > 57344")
    try:
        testcases = tuple(int(item) for item in args.tc_list.split(",") if item)
    except ValueError:
        parser.error("--tc-list must be comma-separated integers")
    if not testcases or any(tc not in TCS for tc in testcases) or \
            len(set(testcases)) != len(testcases):
        parser.error("--tc-list must be unique TC228-TC235 values")
    expected_gate_identity = {
        "experiment_root": str(root),
        "l3_size": args.l3_size,
        "l3_assoc": args.l3_assoc,
        "pressure_levels": list(levels),
        "base_seed": args.seed,
        "experiment_mode": args.experiment_mode,
        "directory_pressure_lines": directory_pressure_lines,
        "testcases": list(testcases),
    }
    if args.stage == "stage2":
        if not args.stage1_gate or not args.stage1_gate.is_file():
            parser.error("stage2 requires --stage1-gate")
        gate = json.loads(args.stage1_gate.read_text())
        if gate.get("decision") != "CONTINUE":
            parser.error("stage1 gate decision is not CONTINUE")
        if gate.get("experiment_identity") != expected_gate_identity:
            parser.error("stage1 gate identity does not match this stage2 experiment")
        stage1_manifest = root / "stage1_manifest.json"
        if gate.get("manifest") != str(stage1_manifest.resolve()) or \
                gate.get("manifest_sha256") != sha256(stage1_manifest):
            parser.error("stage1 gate is not bound to the current stage1 manifest")
        evidence_hashes = gate.get("evidence_sha256")
        if not isinstance(evidence_hashes, dict) or any(
                not pathlib.Path(path).is_file() or sha256(pathlib.Path(path)) != digest
                for path, digest in evidence_hashes.items()):
            parser.error("stage1 evidence changed after gate generation")
        pairs = range(2, 6)
    else:
        pairs = range(1, 2)
    cpu_sets = tuple(item.strip() for item in args.cpu_sets.split(",") if item.strip())
    if args.max_parallel < 1 or len(cpu_sets) < args.max_parallel:
        parser.error("--cpu-sets must provide at least --max-parallel entries")
    jobs = [(pair, level, tc, index)
            for pair in pairs for level in levels
            for index, tc in enumerate(testcases)]
    results = []
    progress_lock = threading.Lock()

    def write_progress(last, running):
        with progress_lock:
            atomic_json(root / f"{args.stage}_progress.json", {
                "stage": args.stage, "pair_jobs_total": len(jobs),
                "pair_jobs_completed": len(results) // 2,
                "completed_arms": len(results), "running": running,
                "last": last,
                "pass": sum(row["status"] == "PASS" for row in results),
                "fail": sum(row["status"] != "PASS" for row in results),
                "max_parallel": args.max_parallel,
                "cpu_sets": list(cpu_sets[:args.max_parallel]),
            })

    pending = list(jobs)
    active = {}
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=args.max_parallel) as pool:
        while pending or active:
            used_slots = {slot for _, slot in active.values()}
            while pending and len(active) < args.max_parallel:
                slot = next(i for i in range(args.max_parallel)
                            if i not in used_slots)
                used_slots.add(slot)
                pair, level, tc, index = pending.pop(0)
                future = pool.submit(
                    run_pair_job, root, pair, level, tc, index, args.timeout,
                    args.seed, args.l3_size, args.l3_assoc,
                    args.experiment_mode, directory_pressure_lines,
                    cpu_sets[slot])
                active[future] = ((pair, level, tc), slot)
            running = [{"pair": job[0], "pressure_level": job[1], "tc": job[2],
                        "slot": slot, "cpuset": cpu_sets[slot]}
                       for job, slot in active.values()]
            write_progress(None, running)
            done, _ = concurrent.futures.wait(
                active, timeout=30,
                return_when=concurrent.futures.FIRST_COMPLETED)
            if not done:
                continue
            for future in done:
                job, slot = active.pop(future)
                try:
                    results.extend(future.result())
                except Exception as error:
                    pair, level, tc = job
                    results.append({
                        "pair": pair, "pressure_level": level, "tc": tc,
                        "arm": "pair-job", "status": "FAIL", "return_code": 1,
                        "reason": repr(error), "cpuset": cpu_sets[slot],
                    })
                running = [{"pair": item[0], "pressure_level": item[1],
                            "tc": item[2], "slot": active_slot,
                            "cpuset": cpu_sets[active_slot]}
                           for item, active_slot in active.values()]
                write_progress({"pair": job[0], "pressure_level": job[1],
                                "tc": job[2]}, running)
    stage_manifest = make_manifest(
        root, pairs, levels, testcases, args.l3_size, args.l3_assoc, args.seed,
        args.experiment_mode, directory_pressure_lines)
    atomic_json(root / f"{args.stage}_manifest.json", stage_manifest)
    if args.stage == "stage2":
        combined = make_manifest(
            root, range(1, 6), levels, testcases, args.l3_size, args.l3_assoc,
            args.seed,
            args.experiment_mode, directory_pressure_lines)
        combined_path = root / "metric3_l3_pressure_manifest.json"
        atomic_json(combined_path, combined)
        report_cmd = [sys.executable, str(ROOT / "scripts/analyze_metric3_l3_pressure.py"),
                      "--manifest", str(combined_path), "--output-dir",
                      str(root / "metric3_l3_pressure_report"), "--weights",
                      str(ROOT / "scripts/metric3_weights.frozen.json")]
        report_status = subprocess.run(report_cmd).returncode
        if report_status != 0:
            raise SystemExit(f"combined Metric3 analysis failed with status {report_status}")
    atomic_json(root / f"{args.stage}_run_summary.json", {"stage": args.stage,
                "total": len(results), "pass": sum(r["status"] == "PASS" for r in results),
                "fail": sum(r["status"] != "PASS" for r in results), "results": results})
    return 0 if all(row["status"] == "PASS" for row in results) else 1


if __name__ == "__main__":
    sys.exit(main())
