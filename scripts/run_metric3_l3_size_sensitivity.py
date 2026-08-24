#!/usr/bin/env python3
"""Run a one-pair fixed-workset Metric3 L3-size sensitivity screening.

All build and simulation work is executed in ubcc-dev:ubuntu20.04.
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

from run_metric3_l3_pressure import atomic_json, metrics, sha256


ROOT = pathlib.Path(__file__).resolve().parents[1]
IMAGE = "ubcc-dev:ubuntu20.04"
ARMS = ("ourcc", "ha-vi")


def parse_size(value):
    suffixes = (("KiB", 1024), ("kB", 1024), ("MiB", 1024 * 1024),
                ("MB", 1024 * 1024), ("B", 1))
    for suffix, multiplier in suffixes:
        if value.endswith(suffix):
            magnitude = value[:-len(suffix)]
            if magnitude.isdigit() and int(magnitude) > 0:
                return int(magnitude) * multiplier
    raise ValueError("L3 size must use B, kB/KiB, or MB/MiB")


def result_complete(path, expected):
    if not path.is_file():
        return False
    try:
        row = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    return (row.get("status") == "PASS" and row.get("return_code") == 0 and
            all(row.get(key) == value for key, value in expected.items()))


def point_id(size, target, tc):
    normalized = size.replace("KiB", "k").replace("kB", "k")
    normalized = normalized.replace("MiB", "m").replace("MB", "m")
    return f"l3_{normalized}_t{target:05d}_tc{tc}"


def run_arm(root, size, size_bytes, target, tc, index, arm, timeout, seed,
            l3_assoc, cpuset):
    identity = point_id(size, target, tc)
    order = "AB" if index % 2 == 0 else "BA"
    log_dir = root / "cases" / identity / arm
    result_path = log_dir / "result.json"
    runtime_sha256 = {
        "gem5": sha256(ROOT / "gem5/gem5/build/ARM/gem5.opt"),
        "ubio": sha256(ROOT / "build/bin/ubio"),
        "networksim": sha256(ROOT / "build/bin/networksim"),
    }
    expected = {
        "point_id": identity,
        "pair": 1,
        "tc": tc,
        "target_lines": target,
        "arm": arm,
        "l3_size": size,
        "l3_assoc": l3_assoc,
        "seed": seed,
        "order": order,
        "runtime_sha256": runtime_sha256,
    }
    if result_complete(result_path, expected):
        return json.loads(result_path.read_text())

    profile = "ubcc" if arm == "ourcc" else "ha-vi"
    clear = "lossless-oneway" if arm == "ourcc" else "ack"
    run_id = "m3size_" + hashlib.sha256(
        f"{root.name}:{identity}:{arm}".encode()).hexdigest()[:16]
    container_log = "/workspace/" + str(log_dir.relative_to(ROOT))
    env = [
        f"E2E_RUN_ID={run_id}", f"LOG_BASE={container_log}",
        f"TIMEOUT_SEC={timeout}", "STALL_TIMEOUT_SEC=1800",
        "EP_CPU_MODEL=o3", "EP_SEQUENCER_MAX_OUTSTANDING=16",
        "EP_TRACE_PERF=off", "EP_PERF_PROFILE=spill-noopt",
        "UBCC_POLICY=spill", "UBCC_OPTS=--dir-overflow-policy=spill",
        "EP_GEM5_OPTS=--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0",
        f"EP_HA_PROFILE={profile}", f"OURCC_CLEAR_PROFILE={clear}",
        f"EP_L3_SIZE={size}", f"EP_L3_ASSOC={l3_assoc}",
        "L3_PRESSURE_LEVEL=0", f"L3_PRESSURE_TARGET_LINES={target}",
        f"METRIC3_L3_SEED={seed}", "METRIC3_L3_EXPERIMENT_MODE=l3-only",
        "L3_DIRECTORY_PRESSURE_LINES=0", "EP_TRACK_L3_OCCUPANCY=1",
        "GEM5_DEBUG_FLAGS=RubyCHIGeneric", "HA_EXACT_BYTES=134217728",
        "HA_MAX_ACTIVE=256", "HA_MAX_QUEUE=8", "EP_LINK_LATENCY_PS=2500",
        "EP_SYNC_INTERVAL_PS=2500", "EP_PORT_HWM=8192",
        "EP_NSIM_MAX_PENDING=65536",
        "LD_LIBRARY_PATH=/workspace/thirdparty/zeromq/lib",
    ]
    command = [
        "docker", "run", "--rm", "--network", "none",
        f"--cpuset-cpus={cpuset}",
        "-v", f"{ROOT}:/workspace",
        "-v", f"{ROOT / 'gem5/gem5'}:/workspace/gem5",
        "-v", "/mnt/data2/cgc/.local/lib:/workspace/thirdparty/zeromq/lib:ro",
        "-w", "/workspace", IMAGE, "env", *env,
        "bash", "tests/e2e/run_multi.sh", "--2n1s", str(tc),
    ]
    log_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with (log_dir / "coordinator.log").open("w") as stream:
        status = subprocess.run(
            command, stdout=stream, stderr=subprocess.STDOUT).returncode
    verifier = log_dir / f"verify_tc{tc}.log"
    verifier_pass = verifier.is_file() and verifier.read_text(
        errors="replace").rstrip().endswith(f">>> TC{tc} PASSED <<<")
    elf = ROOT / "build/runs" / run_id / "workload.elf"
    capacity_lines = size_bytes // 64
    row = {
        **expected,
        "effective_pressure_pct": target * 100.0 / capacity_lines,
        "capacity_lines": capacity_lines,
        "ha_profile": profile,
        "clear_profile": clear,
        "run_id": run_id,
        "return_code": status,
        "verifier_pass": verifier_pass,
        "status": "PASS" if status == 0 and verifier_pass else "FAIL",
        "elapsed_sec": time.time() - started,
        "log_dir": str(log_dir),
        "workload_elf": str(elf),
        "workload_elf_sha256": sha256(elf) if elf.is_file() else None,
    }
    if row["status"] == "PASS":
        try:
            row["metrics"] = metrics(log_dir, tc)
        except Exception as error:
            row["status"] = "FAIL"
            row["reason"] = str(error)
    atomic_json(result_path, row)
    return row


def run_point(root, size, size_bytes, target, tc, index, timeout, base_seed,
              l3_assoc, cpuset):
    order = "AB" if index % 2 == 0 else "BA"
    sequence = ARMS if order == "AB" else tuple(reversed(ARMS))
    seed = base_seed + index * 1000 + tc
    rows = [run_arm(root, size, size_bytes, target, tc, index, arm, timeout,
                    seed, l3_assoc, cpuset) for arm in sequence]
    hashes = {row.get("workload_elf_sha256") for row in rows}
    if None in hashes or len(hashes) != 1:
        raise RuntimeError(f"ELF identity mismatch {point_id(size, target, tc)}")
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=pathlib.Path, required=True)
    parser.add_argument("--l3-sizes", default="128kB,256kB,512kB,1MiB")
    parser.add_argument("--target-lines", default="0,4096,6144")
    parser.add_argument("--tc-list", default="228,229,230,231,232,233,234,235")
    parser.add_argument("--l3-assoc", type=int, default=16)
    parser.add_argument("--seed", type=int, default=328235)
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--max-parallel", type=int, default=3)
    parser.add_argument("--cpu-sets", default="0-9,10-19,20-29")
    args = parser.parse_args()

    root = args.output_root.resolve()
    try:
        root.relative_to(ROOT)
    except ValueError:
        parser.error("--output-root must be inside the workspace")
    sizes = tuple(item.strip() for item in args.l3_sizes.split(",") if item.strip())
    try:
        size_rows = tuple((size, parse_size(size)) for size in sizes)
        targets = tuple(int(item) for item in args.target_lines.split(",") if item)
        testcases = tuple(int(item) for item in args.tc_list.split(",") if item)
    except ValueError as error:
        parser.error(str(error))
    if not size_rows or not targets or any(target < 0 for target in targets):
        parser.error("sizes and non-negative target lines are required")
    if not testcases or any(tc not in range(228, 236) for tc in testcases):
        parser.error("--tc-list must contain TC228-TC235")
    for size, size_bytes in size_rows:
        if size_bytes % (64 * args.l3_assoc) != 0:
            parser.error(f"{size} is not divisible by 64*associativity")
        sets = size_bytes // 64 // args.l3_assoc
        if sets <= 1 or sets & (sets - 1):
            parser.error(f"{size} does not produce a power-of-two set count")

    cpu_sets = tuple(item.strip() for item in args.cpu_sets.split(",") if item.strip())
    if args.max_parallel < 1 or len(cpu_sets) < args.max_parallel:
        parser.error("--cpu-sets must provide at least --max-parallel entries")
    points = [(size, size_bytes, target, tc)
              for size, size_bytes in size_rows
              for target in targets for tc in testcases]
    results = []
    lock = threading.Lock()

    def progress(running, last=None):
        with lock:
            atomic_json(root / "progress.json", {
                "points_total": len(points),
                "points_completed": len(results) // 2,
                "arms_completed": len(results),
                "pass": sum(row.get("status") == "PASS" for row in results),
                "fail": sum(row.get("status") != "PASS" for row in results),
                "running": running,
                "last": last,
            })

    pending = list(enumerate(points))
    active = {}
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=args.max_parallel) as pool:
        while pending or active:
            used = {slot for _, slot in active.values()}
            while pending and len(active) < args.max_parallel:
                index, point = pending.pop(0)
                slot = next(item for item in range(args.max_parallel)
                            if item not in used)
                used.add(slot)
                size, size_bytes, target, tc = point
                future = pool.submit(
                    run_point, root, size, size_bytes, target, tc, index,
                    args.timeout, args.seed, args.l3_assoc, cpu_sets[slot])
                active[future] = ((size, target, tc), slot)
            progress([{"l3_size": point[0], "target_lines": point[1],
                       "tc": point[2], "slot": slot, "cpuset": cpu_sets[slot]}
                      for point, slot in active.values()])
            done, _ = concurrent.futures.wait(
                active, timeout=30,
                return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                point, slot = active.pop(future)
                try:
                    results.extend(future.result())
                except Exception as error:
                    results.append({"l3_size": point[0],
                                    "target_lines": point[1], "tc": point[2],
                                    "arm": "point-job", "status": "FAIL",
                                    "return_code": 1, "reason": repr(error)})
                progress([], {"l3_size": point[0],
                              "target_lines": point[1], "tc": point[2]})

    manifest = {
        "schema_version": 1,
        "experiment_id": root.name,
        "l3_sizes": [{"size": size, "bytes": size_bytes,
                      "capacity_lines": size_bytes // 64}
                     for size, size_bytes in size_rows],
        "target_lines": list(targets),
        "testcases": list(testcases),
        "l3_assoc": args.l3_assoc,
        "base_seed": args.seed,
        "pair_count": 1,
        "points": len(points),
    }
    atomic_json(root / "manifest.json", manifest)
    atomic_json(root / "run_summary.json", {
        "total": len(results),
        "pass": sum(row.get("status") == "PASS" for row in results),
        "fail": sum(row.get("status") != "PASS" for row in results),
        "results": results,
    })
    analyze = subprocess.run([
        sys.executable, str(ROOT / "scripts/analyze_metric3_l3_size_sensitivity.py"),
        "--result-root", str(root),
    ]).returncode
    return 0 if analyze == 0 and all(
        row.get("status") == "PASS" for row in results) else 1


if __name__ == "__main__":
    sys.exit(main())
