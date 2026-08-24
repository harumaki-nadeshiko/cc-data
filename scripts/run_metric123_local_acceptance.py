#!/usr/bin/env python3
"""Run resumable local Metric 1/2 and Metric 3 acceptance evidence."""

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
PROFILES = ("naive", "spill-noopt", "optimized")
METRIC2_CASES = (135, 136, 137, 138, 139, 140, 217)
METRIC3_CASES = tuple(range(228, 236))


def run_output(command, cwd=ROOT):
    return subprocess.check_output(command, cwd=cwd, text=True).strip()


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def profile_settings(profile):
    if profile == "naive":
        return "naive", "naive", "--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0"
    if profile == "spill-noopt":
        return "spill", "spill-noopt", "--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0"
    if profile == "optimized":
        return "spill", "optimized", "--silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1"
    raise ValueError(profile)


def docker_run(log_dir, run_id, topology_flag, tc, profile,
               *, ha_profile="ubcc", clear_profile="ack", timeout=7200):
    policy, perf_profile, gem5_opts = profile_settings(profile)
    try:
        container_log_dir = "/workspace/" + str(log_dir.relative_to(ROOT))
    except ValueError as error:
        raise ValueError("output root must be inside the workspace") from error
    command = [
        "docker", "run", "--rm", "--network", "none", "--cpuset-cpus=0-31",
        "-v", f"{ROOT}:/workspace",
        "-v", f"{ROOT / 'gem5/gem5'}:/workspace/gem5",
        "-v", "/mnt/data2/cgc/.local/lib:/workspace/thirdparty/zeromq/lib:ro",
        "-w", "/workspace", IMAGE, "env",
        f"E2E_RUN_ID={run_id}", f"LOG_BASE={container_log_dir}",
        f"TIMEOUT_SEC={timeout}", f"TIMEOUT_SEC_TC131={timeout}",
        "STALL_TIMEOUT_SEC=1800", "EP_CPU_MODEL=o3",
        "EP_SEQUENCER_MAX_OUTSTANDING=16", "EP_TRACE_PERF=off",
        f"EP_PERF_PROFILE={perf_profile}", f"UBCC_POLICY={policy}",
        f"UBCC_OPTS=--dir-overflow-policy={policy}",
        f"EP_GEM5_OPTS={gem5_opts}", f"EP_HA_PROFILE={ha_profile}",
        f"OURCC_CLEAR_PROFILE={clear_profile}",
        "HA_EXACT_BYTES=134217728", "HA_MAX_ACTIVE=256", "HA_MAX_QUEUE=8",
        "EP_LINK_LATENCY_PS=2500", "EP_SYNC_INTERVAL_PS=2500",
        "EP_PORT_HWM=8192", "EP_NSIM_MAX_PENDING=65536",
        "LD_LIBRARY_PATH=/workspace/thirdparty/zeromq/lib",
        "bash", "tests/e2e/run_multi.sh", f"--{topology_flag}", str(tc),
    ]
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "coordinator.log").open("w") as stream:
        return subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT).returncode


def workload_dir(run_id, tc):
    return ROOT / "build/runs" / run_id / f"tc{tc}" / "m5out"


def result_passed(path):
    if not path.is_file():
        return False
    try:
        result = json.loads(path.read_text())
        if result.get("status") != "PASS":
            return False
        tc = int(result["tc"])
        root = path.parent
        verifier = root / f"verify_tc{tc}.log"
        if not verifier.is_file():
            return False
        lines = [line.strip() for line in verifier.read_text(errors="replace").splitlines()
                 if line.strip()]
        if not lines or lines[-1] != f">>> TC{tc} PASSED <<<":
            return False
        child = root / f"child_status_tc{tc}"
        exits = list(child.glob("*.exit")) if child.is_dir() else []
        return bool(exits) and all(
            item.read_text(errors="replace").strip() == "0" for item in exits)
    except (OSError, ValueError):
        return False


def fields(line):
    output = {}
    for atom in line.split():
        if "=" in atom:
            key, value = atom.split("=", 1)
            output[key] = value.rstrip(",")
    return output


def marker_records(log_dir, prefix, phase):
    output = []
    for path in sorted(log_dir.glob("simout_tc*_node*.log")):
        for line in path.read_text(errors="replace").splitlines():
            if line.startswith(prefix):
                item = fields(line)
                if item.get("phase") == phase:
                    output.append(item)
    return output


def aggregate_timer(log_dir, phase):
    rows = marker_records(log_dir, "[GUEST-TIMER]", phase)
    if not rows:
        raise RuntimeError(f"missing timer phase={phase}")
    frequencies = {int(row["counter_frequency_hz"]) for row in rows}
    if len(frequencies) != 1:
        raise RuntimeError(f"timer frequency mismatch phase={phase}")
    operations = sum(int(row["operations"]) for row in rows)
    ticks = sum(int(row["counter_ticks"]) for row in rows)
    return ticks / operations, frequencies.pop()


def max_timer(log_dir, phase):
    rows = marker_records(log_dir, "[GUEST-TIMER]", phase)
    if not rows:
        raise RuntimeError(f"missing timer phase={phase}")
    frequencies = {int(row["counter_frequency_hz"]) for row in rows}
    if len(frequencies) != 1:
        raise RuntimeError(f"timer frequency mismatch phase={phase}")
    return max(int(row["counter_ticks"]) / int(row["operations"])
               for row in rows), frequencies.pop()


def aggregate_latency(log_dir, phase):
    rows = marker_records(log_dir, "[PERF-LATENCY]", phase)
    if not rows:
        raise RuntimeError(f"missing latency phase={phase}")
    frequencies = {int(row["counter_frequency_hz"]) for row in rows}
    if len(frequencies) != 1:
        raise RuntimeError(f"latency frequency mismatch phase={phase}")
    samples = sum(int(row["samples"]) for row in rows)
    weighted = sum(float(row["mean"]) * int(row["samples"])
                   for row in rows)
    return weighted / samples, frequencies.pop()


def metric3_values(log_dir, tc):
    if tc == 228:
        raw = {"remote_read": aggregate_timer(log_dir, "topology_remote_read")}
    elif tc == 229:
        raw = {"ownership_handoff": aggregate_timer(
            log_dir, "topology_ownership_handoff")}
    elif tc == 230:
        raw = {"shared_to_writer": aggregate_timer(
            log_dir, "topology_all_sharer_to_writer")}
    elif tc == 231:
        raw = {"clean_shared_control": aggregate_timer(
            log_dir, "clean_shared_read_service")}
    elif tc == 232:
        raw = {
            "hot_key_read": aggregate_timer(log_dir, "hot_key_read_service"),
            "hot_key_write": aggregate_timer(log_dir, "hot_key_write_service"),
        }
    elif tc == 233:
        raw = {
            "producer_consumer_load": aggregate_latency(
                log_dir, "producer_consumer_load"),
            "producer_consumer_service": aggregate_timer(
                log_dir, "producer_consumer_service"),
        }
    elif tc == 234:
        raw = {
            "queued_token_end_to_end": aggregate_timer(
                log_dir, "queued_token_end_to_end"),
            "queued_token_store": aggregate_timer(log_dir, "queued_token_store"),
        }
    elif tc == 235:
        raw = {
            "catalog_kv_end_to_end": max_timer(log_dir, "catalog_kv_end_to_end"),
            "catalog_kv_service": aggregate_timer(log_dir, "catalog_kv_service"),
        }
    else:
        raise RuntimeError(f"unsupported metric3 TC{tc}")
    return {
        name: {"ticks_per_operation": value,
               "counter_frequency_hz": frequency,
               "ns_per_operation": value * 1.0e9 / frequency}
        for name, (value, frequency) in raw.items()
    }


def execute_run(root, key, tc, topology, topology_flag, profile, repetition,
                *, ha_profile="ubcc", clear_profile="ack", timeout=7200,
                metric=None, order=None, arm=None):
    case_root = root / "cases" / key
    result_path = case_root / "result.json"
    if result_passed(result_path):
        return json.loads(result_path.read_text())
    run_id = "m123_" + hashlib.sha256(
        f"{root.name}:{key}".encode()).hexdigest()[:16]
    started = time.time()
    status = docker_run(case_root, run_id, topology_flag, tc, profile,
                        ha_profile=ha_profile, clear_profile=clear_profile,
                        timeout=timeout)
    verifier = case_root / f"verify_tc{tc}.log"
    verifier_pass = False
    if verifier.is_file():
        lines = [line.strip() for line in verifier.read_text(errors="replace").splitlines()
                 if line.strip()]
        verifier_pass = bool(lines) and lines[-1] == f">>> TC{tc} PASSED <<<"
    result = {
        "key": key, "tc": tc, "topology": topology, "profile": profile,
        "repetition": repetition, "run_id": run_id,
        "simulator_log_dir": str(case_root),
        "workload_output_dir": str(workload_dir(run_id, tc)),
        "return_code": status, "verifier_pass": verifier_pass,
        "elapsed_sec": time.time() - started,
        "status": "PASS" if status == 0 and verifier_pass else "FAIL",
        "log_dir": str(case_root),
    }
    if metric is not None:
        result["metric"] = metric
    if order is not None:
        result["order"] = order
    if arm is not None:
        result["arm"] = arm
        result["ha_profile"] = ha_profile
        result["clear_profile"] = clear_profile
        if result["status"] == "PASS":
            try:
                result["metrics"] = metric3_values(case_root, tc)
            except Exception as error:
                result["status"] = "FAIL"
                result["reason"] = str(error)
    atomic_json(result_path, result)
    return result


def provenance(root):
    image_id = run_output(["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"])
    files = {
        "framework": ROOT / "build/framework/lib/libframework_local.a",
        "ubio": ROOT / "build/bin/ubio",
        "networksim": ROOT / "build/bin/networksim",
        "gem5": ROOT / "gem5/gem5/build/ARM/gem5.opt",
    }
    payload = {
        "main_commit": run_output(["git", "rev-parse", "HEAD"]),
        "main_diff_sha256": hashlib.sha256(
            subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=ROOT)).hexdigest(),
        "gem5_commit": run_output(["git", "rev-parse", "HEAD"], ROOT / "gem5/gem5"),
        "gem5_diff_sha256": hashlib.sha256(subprocess.check_output(
            ["git", "diff", "--binary", "HEAD"], cwd=ROOT / "gem5/gem5")).hexdigest(),
        "docker_image": IMAGE, "docker_image_id": image_id,
        "cpu_model": "o3", "sequencer_max_outstanding": 16,
        "link_latency_ps": 2500, "sync_interval_ps": 2500,
        "binaries": {name: {"path": str(path), "sha256": sha256(path)}
                     for name, path in files.items()},
    }
    atomic_json(root / "provenance.json", payload)
    return payload


def metric12(args, root):
    results = []
    for repetition in range(1, args.repetitions + 1):
        for profile in PROFILES:
            key = f"metric1/r{repetition}/tc131/{profile}"
            results.append(execute_run(
                root, key, 131, "8n1s", "8n1s", profile, repetition,
                timeout=args.metric12_timeout, metric="metric1"))
        for tc in METRIC2_CASES:
            topology, flag = ("2n1s", "2n1s") if tc == 217 else ("3n1s", "1s")
            for profile in PROFILES:
                key = f"metric2/r{repetition}/tc{tc}/{profile}"
                results.append(execute_run(
                    root, key, tc, topology, flag, profile, repetition,
                    timeout=args.metric12_timeout, metric="metric2"))
    runs, uses = [], []
    for index, result in enumerate(results):
        run_id = f"physical-{index + 1:03d}"
        simulator_path = pathlib.Path(result["simulator_log_dir"])
        workload_path = pathlib.Path(result["workload_output_dir"])
        runs.append({
            "id": run_id,
            "simulator_log_dir": os.path.relpath(simulator_path, root),
            "workload_output_dir": os.path.relpath(workload_path, root),
        })
        uses.append({
            "id": f"use-{index + 1:03d}", "physical_run_id": run_id,
            "metric": result["metric"], "repetition": result["repetition"],
            "case": f"TC{result['tc']}", "topology": result["topology"],
            "profile": result["profile"],
        })
    manifest = {
        "schema_version": 2,
        "requirements": {
            "metric1": {"repetitions": list(range(1, args.repetitions + 1))},
            "metric2": {"repetitions": list(range(1, args.repetitions + 1))},
        },
        "policy": {"allow_reuse": False}, "runs": runs, "uses": uses,
    }
    atomic_json(root / "metric12_manifest.json", manifest)
    return results


def metric3(args, root):
    results = []
    for pair in range(1, args.pairs + 1):
        for tc_index, tc in enumerate(METRIC3_CASES):
            order = "AB" if (pair - 1 + tc_index) % 2 == 0 else "BA"
            arms = ("ourcc", "ha-vi") if order == "AB" else ("ha-vi", "ourcc")
            for arm in arms:
                profile = "ubcc" if arm == "ourcc" else "ha-vi"
                clear_profile = "lossless-oneway" if arm == "ourcc" else "ack"
                key = f"metric3/r{pair}/tc{tc}/{arm}"
                results.append(execute_run(
                    root, key, tc, "2n1s", "2n1s", "spill-noopt", pair,
                    ha_profile=profile, clear_profile=clear_profile,
                    timeout=args.metric3_timeout, metric="metric3",
                    order=order, arm=arm))
    manifest = {
        "schema_version": 2,
        "experiment_id": root.name, "expected_repeats": args.pairs,
        "testcases": list(METRIC3_CASES),
        "fingerprint": json.loads((root / "provenance.json").read_text()),
        "samples": [],
    }
    by_key = {(row["repetition"], row["tc"], row["arm"]): row for row in results}
    for pair in range(1, args.pairs + 1):
        for tc_index, tc in enumerate(METRIC3_CASES):
            order = "AB" if (pair - 1 + tc_index) % 2 == 0 else "BA"
            pair_id = f"r{pair:02d}_tc{tc}"
            sample = {"sample_id": pair_id, "pair_id": pair_id,
                      "pair": pair, "tc": tc, "order": order, "arms": {}}
            for arm in ("ourcc", "ha-vi"):
                row = by_key[(pair, tc, arm)]
                arm_root = pathlib.Path(row["simulator_log_dir"])
                sample["arms"][arm] = {
                    "result": str((arm_root / "result.json").relative_to(root)),
                    "log_dir": str(arm_root.relative_to(root)),
                    "fingerprint": manifest["fingerprint"],
                }
            manifest["samples"].append(sample)
    atomic_json(root / "metric3_manifest.json", manifest)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=pathlib.Path, required=True)
    parser.add_argument("--scope", choices=("metric12", "metric3", "all"), default="all")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--pairs", type=int, default=5)
    parser.add_argument("--metric12-timeout", type=int, default=10800)
    parser.add_argument("--metric3-timeout", type=int, default=1200)
    args = parser.parse_args()
    root = args.output_root.expanduser().resolve()
    try:
        root.relative_to(ROOT)
    except ValueError as error:
        parser.error("--output-root must be inside the workspace")
    root.mkdir(parents=True, exist_ok=True)
    provenance(root)
    all_results = []
    if args.scope in ("metric12", "all"):
        all_results.extend(metric12(args, root))
    if args.scope in ("metric3", "all"):
        all_results.extend(metric3(args, root))
    atomic_json(root / "run_summary.json", {
        "total": len(all_results),
        "pass": sum(row["status"] == "PASS" for row in all_results),
        "fail": sum(row["status"] != "PASS" for row in all_results),
        "results": all_results,
    })
    return 0 if all(row["status"] == "PASS" for row in all_results) else 1


if __name__ == "__main__":
    sys.exit(main())
