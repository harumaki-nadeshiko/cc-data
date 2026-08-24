#!/usr/bin/env python3
"""Run and analyze an independent optimized Clear-semantics experiment.

The physical matrix is 8 test cases x 3 pairs x 2 arms = 48 runs.  Both arms
use the UBCC HA endpoint and the optimized performance profile; only the Clear
profile differs.  Build/simulation is delegated to run_multi.sh in the
ubcc-dev:ubuntu20.04 container.
"""

import argparse
import concurrent.futures
import hashlib
import json
import pathlib
import re
import statistics
import subprocess
import sys
import threading
import time


ROOT = pathlib.Path(__file__).resolve().parents[1]
IMAGE = "ubcc-dev:ubuntu20.04"
TCS = (131, 135, 136, 137, 138, 139, 140, 217)
ARMS = ("ack", "lossless-oneway")
LANES = ("0-7", "8-15", "16-23", "24-31")
FULL_CPUSET = "0-31"
PAIR_COUNT = 3
PHASES = {
    131: ("timer", "post_pressure_catalog_reuse"),
    135: ("latency", "preserved_sharer_first_load"),
    136: ("latency", "preserved_owner_store_complete"),
    137: ("latency", "new_requester_first_load"),
    138: ("latency", "dirty_owner_handoff_store"),
    139: ("latency", "mixed_batch_16ops"),
    140: ("latency", "cross_l2_owner_store"),
    217: ("latency", "ha10_catalog_batch_16ops"),
}
LATENCY_CONTRACT = {
    135: (1, 24), 136: (1, 24), 137: (2, 24), 138: (2, 24),
    139: (1, 16), 140: (0, 24), 217: (1, 8),
}
TOPOLOGY = {tc: ("8n1s", "8n1s", 8) if tc == 131 else
            ("2n1s", "2n1s", 2) if tc == 217 else
            ("3n1s", "1s", 3) for tc in TCS}
PROFILE_MARKER_RE = re.compile(
    r"\[EPBACKEND-PROFILE\].*node=(\d+).*ha_endpoint_profile=(\S+) "
    r"clear_profile=(\S+)")


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def command_output(command, cwd=ROOT):
    return subprocess.check_output(command, cwd=str(cwd), text=True).strip()


def planned_order(pair, tc):
    """Alternate AB/BA by both pair and testcase index."""
    index = TCS.index(tc)
    return "AB" if ((pair - 1) + index) % 2 == 0 else "BA"


def arm_sequence(order):
    return ARMS if order == "AB" else tuple(reversed(ARMS))


def pair_id(pair, tc):
    return f"r{pair:02d}_tc{tc}"


def run_id_for(root, pair, tc, arm):
    token = f"{root.resolve()}:{pair_id(pair, tc)}:{arm}"
    return "clearperf_" + hashlib.sha256(token.encode()).hexdigest()[:20]


def runtime_hashes():
    files = {
        "framework": ROOT / "build/framework/lib/libframework_local.a",
        "ubio": ROOT / "build/bin/ubio",
        "networksim": ROOT / "build/bin/networksim",
        "gem5": ROOT / "gem5/gem5/build/ARM/gem5.opt",
    }
    missing = [str(path) for path in files.values() if not path.is_file()]
    if missing:
        raise RuntimeError("missing runtime artifacts: " + ", ".join(missing))
    return {name: {"path": str(path.relative_to(ROOT)), "sha256": sha256(path)}
            for name, path in files.items()}


def source_fingerprint():
    tracked_diff = subprocess.check_output(
        ["git", "diff", "--binary", "HEAD"], cwd=str(ROOT))
    status = command_output(["git", "status", "--porcelain=v1", "--untracked-files=all"])
    untracked = []
    source_prefixes = ("config/", "configs/", "framework/", "modules/",
                       "native-lib/", "protocol/", "scripts/", "tests/",
                       "tools/", "verification/")
    for line in status.splitlines():
        if line.startswith("?? "):
            relative = line[3:]
            path = ROOT / relative
            if relative.startswith(source_prefixes) and path.is_file():
                untracked.append({"path": relative, "sha256": sha256(path)})
    return {
        "main_commit": command_output(["git", "rev-parse", "HEAD"]),
        "main_tracked_diff_sha256": hashlib.sha256(tracked_diff).hexdigest(),
        "main_status_sha256": hashlib.sha256(status.encode()).hexdigest(),
        "untracked_files": untracked,
        "gem5_commit": command_output(["git", "rev-parse", "HEAD"], ROOT / "gem5/gem5"),
        "gem5_diff_sha256": hashlib.sha256(subprocess.check_output(
            ["git", "diff", "--binary", "HEAD"], cwd=str(ROOT / "gem5/gem5"))).hexdigest(),
    }


def evidence_fingerprint():
    return {
        "captured_unix": time.time(),
        "source": source_fingerprint(),
        "runtime": runtime_hashes(),
        "docker": {
            "image": IMAGE,
            "image_id": command_output(
                ["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"]),
        },
    }


def make_manifest(root, fingerprint):
    samples = []
    run_ids = set()
    log_dirs = set()
    for pair in range(1, PAIR_COUNT + 1):
        for tc in TCS:
            identity = pair_id(pair, tc)
            order = planned_order(pair, tc)
            arms = {}
            for arm in ARMS:
                log_dir = root / "cases" / identity / arm
                run_id = run_id_for(root, pair, tc, arm)
                if run_id in run_ids or str(log_dir) in log_dirs:
                    raise RuntimeError("non-unique planned run identity")
                run_ids.add(run_id)
                log_dirs.add(str(log_dir))
                arms[arm] = {
                    "clear_profile": arm,
                    "run_id": run_id,
                    "log_dir": str(log_dir.relative_to(root)),
                    "result": str((log_dir / "result.json").relative_to(root)),
                }
            topology, flag, nodes = TOPOLOGY[tc]
            samples.append({
                "pair_id": identity, "pair": pair, "tc": tc,
                "metric_phase": PHASES[tc][1], "order": order,
                "topology": topology, "topology_flag": flag,
                "node_count": nodes, "arms": arms,
            })
    return {
        "schema_version": 1,
        "experiment_kind": "independent-clear-semantics-performance",
        "experiment_id": root.name,
        "physical_runs": len(samples) * len(ARMS),
        "pair_count": PAIR_COUNT,
        "testcases": list(TCS),
        "arms": list(ARMS),
        "delta_definition": "ack - lossless-oneway; positive favors lossless-oneway",
        "common_settings": {
            "ha_profile": "ubcc", "performance_profile": "optimized",
            "ubcc_policy": "spill", "cpu_model": "o3",
            "sequencer_max_outstanding": 16,
            "gem5_options": "--silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1",
            "link_latency_ps": 2500, "sync_interval_ps": 2500,
            "port_hwm": 8192, "networksim_max_pending": 65536,
        },
        "cpu_policy": {
            "budget": FULL_CPUSET,
            "tc131_exclusive_cpuset": FULL_CPUSET,
            "smaller_case_disjoint_lanes": list(LANES),
            "smaller_max_parallel": 4,
        },
        "fingerprint": fingerprint,
        "samples": samples,
    }


def parse_fields(line):
    output = {}
    for atom in line.split():
        if "=" in atom:
            key, value = atom.split("=", 1)
            output[key] = value.rstrip(",")
    return output


def marker_rows(log_dir, prefix, phase):
    rows = []
    for path in sorted(log_dir.glob("simout_tc*_node*.log")):
        for line in path.read_text(errors="replace").splitlines():
            if line.startswith(prefix):
                values = parse_fields(line)
                if values.get("phase") == phase:
                    rows.append(values)
    return rows


def primary_metric(log_dir, tc):
    kind, phase = PHASES[tc]
    prefix = "[GUEST-TIMER]" if kind == "timer" else "[PERF-LATENCY]"
    rows = marker_rows(log_dir, prefix, phase)
    if not rows:
        raise RuntimeError(f"missing primary marker TC{tc} phase={phase}")
    frequencies = {int(row["counter_frequency_hz"]) for row in rows}
    if len(frequencies) != 1 or next(iter(frequencies)) <= 0:
        raise RuntimeError(f"invalid counter frequency TC{tc} phase={phase}")
    frequency = frequencies.pop()
    if kind == "timer":
        operations = sum(int(row["operations"]) for row in rows)
        ticks = sum(int(row["counter_ticks"]) for row in rows)
        if operations <= 0:
            raise RuntimeError(f"zero operations TC{tc} phase={phase}")
        value = ticks / operations
        aggregation = "aggregate_sum_ticks_div_sum_operations"
    else:
        expected_node, expected_samples = LATENCY_CONTRACT[tc]
        selected = [row for row in rows if int(row["node"]) == expected_node]
        if len(selected) != 1 or int(selected[0]["samples"]) != expected_samples:
            raise RuntimeError(
                f"TC{tc} marker contract expected node={expected_node} "
                f"samples={expected_samples}, got {rows}")
        value = float(selected[0]["mean"])
        aggregation = "reported_latency_mean"
    return {
        "phase": phase, "aggregation": aggregation,
        "ticks_per_operation": value,
        "counter_frequency_hz": frequency,
        "ns_per_operation": value * 1.0e9 / frequency,
    }


def verify_child_exits(log_dir, tc, topology):
    nodes, sockets = (int(value) for value in re.fullmatch(
        r"(\d+)n(\d+)s", topology).groups())
    expected = {f"gem5_node{node}.exit" for node in range(nodes)}
    expected |= {f"ubio_n{node}_s{socket}.exit" for node in range(nodes)
                 for socket in range(sockets)}
    expected.add("networksim.exit")
    child_dir = log_dir / f"child_status_tc{tc}"
    actual = {path.name: path for path in child_dir.glob("*.exit")} if child_dir.is_dir() else {}
    values = {name: path.read_text(errors="replace").strip()
              for name, path in actual.items()}
    valid = set(actual) == expected and all(value == "0" for value in values.values())
    return {"valid": valid, "expected": sorted(expected),
            "observed": values,
            "missing": sorted(expected - set(actual)),
            "extra": sorted(set(actual) - expected)}


def verify_profile_markers(log_dir, arm, node_count):
    observed = {}
    duplicates = []
    for node in range(node_count):
        matches = []
        # The directory name contains a TC unknown to this helper; glob exactly one.
        candidates = list(log_dir.glob(f"gem5_tc*_node{node}"))
        for candidate in candidates:
            for name in ("stderr.log", "stdout.log"):
                path = candidate / name
                if not path.is_file():
                    continue
                for line in path.read_text(errors="replace").splitlines():
                    match = PROFILE_MARKER_RE.search(line)
                    if match and int(match.group(1)) == node:
                        matches.append({"ha_profile": match.group(2),
                                        "clear_profile": match.group(3),
                                        "path": str(path)})
        if len(matches) != 1:
            duplicates.append({"node": node, "count": len(matches)})
        observed[str(node)] = matches
    valid = not duplicates and all(
        rows[0]["ha_profile"] == "ubcc" and rows[0]["clear_profile"] == arm
        for rows in observed.values())
    return {"valid": valid, "expected_ha_profile": "ubcc",
            "expected_clear_profile": arm, "observed": observed,
            "cardinality_errors": duplicates}


def verify_launch_identity(log_dir, arm):
    manifest = log_dir / "launch_manifest.txt"
    values = {}
    if manifest.is_file():
        for line in manifest.read_text(errors="replace").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
    expected = {
        "HA_PROFILE": "ubcc", "OURCC_CLEAR_PROFILE": arm,
        "EP_PERF_PROFILE": "optimized", "UBCC_POLICY": "spill",
        "EP_CPU_MODEL": "o3", "EP_SEQUENCER_MAX_OUTSTANDING": "16",
        "EP_LINK_LATENCY_PS": "2500", "EP_SYNC_INTERVAL_PS": "2500",
        "EP_GEM5_OPTS": "--silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1",
    }
    mismatch = {key: {"expected": value, "observed": values.get(key)}
                for key, value in expected.items() if values.get(key) != value}
    return {"valid": manifest.is_file() and not mismatch,
            "manifest": str(manifest), "mismatch": mismatch}


def inspect_evidence(log_dir, tc, topology, arm, node_count, return_code):
    verifier = log_dir / f"verify_tc{tc}.log"
    lines = ([line.strip() for line in verifier.read_text(errors="replace").splitlines()
              if line.strip()] if verifier.is_file() else [])
    sentinel = f">>> TC{tc} PASSED <<<"
    checks = {
        "coordinator_return_code_zero": return_code == 0,
        "verifier_final_sentinel": bool(lines) and lines[-1] == sentinel,
        "managed_child_exits": verify_child_exits(log_dir, tc, topology),
        "profile_markers": verify_profile_markers(log_dir, arm, node_count),
        "launch_identity": verify_launch_identity(log_dir, arm),
    }
    valid = (checks["coordinator_return_code_zero"] and
             checks["verifier_final_sentinel"] and
             checks["managed_child_exits"]["valid"] and
             checks["profile_markers"]["valid"] and
             checks["launch_identity"]["valid"])
    return valid, checks


def expected_result_fields(root, pair, tc, arm):
    topology, _, _ = TOPOLOGY[tc]
    return {
        "pair": pair, "pair_id": pair_id(pair, tc), "tc": tc,
        "arm": arm, "clear_profile": arm, "ha_profile": "ubcc",
        "performance_profile": "optimized", "topology": topology,
        "order": planned_order(pair, tc),
        "run_id": run_id_for(root, pair, tc, arm),
    }


def resumable_result(path, expected, frozen_runtime):
    if not path.is_file():
        return None
    try:
        row = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    allowed_cpuset = (FULL_CPUSET if expected["tc"] == 131 else LANES)
    cpuset_valid = (row.get("cpuset") == allowed_cpuset if isinstance(
        allowed_cpuset, str) else row.get("cpuset") in allowed_cpuset)
    if (row.get("return_code") != 0 or
            not cpuset_valid or
            row.get("runtime_hashes") != frozen_runtime or
            any(row.get(key) != value for key, value in expected.items())):
        return None
    valid, checks = inspect_evidence(
        path.parent, expected["tc"], expected["topology"], expected["arm"],
        TOPOLOGY[expected["tc"]][2], row["return_code"])
    if not valid:
        return None
    try:
        metric = primary_metric(path.parent, expected["tc"])
    except Exception:
        return None
    if row.get("primary_metric") != metric:
        row["primary_metric"] = metric
    row["evidence_checks"] = checks
    row["status"] = "PASS"
    row.pop("reason", None)
    atomic_json(path, row)
    return row


def docker_command(log_dir, run_id, tc, topology_flag, arm, timeout, cpuset):
    container_log = "/workspace/" + str(log_dir.relative_to(ROOT))
    env = [
        f"E2E_RUN_ID={run_id}", f"LOG_BASE={container_log}",
        f"TIMEOUT_SEC={timeout}", f"TIMEOUT_SEC_TC131={timeout}",
        "STALL_TIMEOUT_SEC=1800", "EP_CPU_MODEL=o3",
        "EP_SEQUENCER_MAX_OUTSTANDING=16", "EP_TRACE_PERF=off",
        "EP_PERF_PROFILE=optimized", "UBCC_POLICY=spill",
        "UBCC_OPTS=--dir-overflow-policy=spill",
        "EP_GEM5_OPTS=--silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1",
        "EP_HA_PROFILE=ubcc", f"OURCC_CLEAR_PROFILE={arm}",
        "HA_EXACT_BYTES=134217728", "HA_MAX_ACTIVE=256", "HA_MAX_QUEUE=8",
        "EP_LINK_LATENCY_PS=2500", "EP_SYNC_INTERVAL_PS=2500",
        "EP_PORT_HWM=8192", "EP_NSIM_MAX_PENDING=65536",
        f"EP_DOCKER_CPUSET={cpuset}",
        "LD_LIBRARY_PATH=/workspace/thirdparty/zeromq/lib",
    ]
    return [
        "docker", "run", "--rm", "--network", "none",
        f"--cpuset-cpus={cpuset}", "-v", f"{ROOT}:/workspace",
        "-v", f"{ROOT / 'gem5/gem5'}:/workspace/gem5",
        "-v", "/mnt/data2/cgc/.local/lib:/workspace/thirdparty/zeromq/lib:ro",
        "-w", "/workspace", IMAGE, "env", *env,
        "bash", "tests/e2e/run_multi.sh", f"--{topology_flag}", str(tc),
    ]


def execute_arm(root, pair, tc, arm, cpuset, timeout, frozen_runtime):
    identity = pair_id(pair, tc)
    log_dir = root / "cases" / identity / arm
    result_path = log_dir / "result.json"
    expected = expected_result_fields(root, pair, tc, arm)
    resumed = resumable_result(result_path, expected, frozen_runtime)
    if resumed is not None:
        resumed = dict(resumed)
        resumed["resumed"] = True
        return resumed
    current_runtime = runtime_hashes()
    if current_runtime != frozen_runtime:
        raise RuntimeError("runtime binaries changed after experiment fingerprint capture")
    log_dir.mkdir(parents=True, exist_ok=True)
    command = docker_command(log_dir, expected["run_id"], tc, TOPOLOGY[tc][1],
                             arm, timeout, cpuset)
    started = time.time()
    with (log_dir / "coordinator.log").open("w") as stream:
        return_code = subprocess.run(
            command, stdout=stream, stderr=subprocess.STDOUT).returncode
    row = {
        **expected, "cpuset": cpuset, "return_code": return_code,
        "elapsed_sec": time.time() - started, "log_dir": str(log_dir),
        "runtime_hashes": current_runtime, "resumed": False,
    }
    valid, checks = inspect_evidence(
        log_dir, tc, expected["topology"], arm, TOPOLOGY[tc][2], return_code)
    row["evidence_checks"] = checks
    elf = ROOT / "build/runs" / expected["run_id"] / "workload.elf"
    row["workload_elf"] = str(elf.relative_to(ROOT)) if elf.is_file() else None
    row["workload_elf_sha256"] = sha256(elf) if elf.is_file() else None
    if valid:
        try:
            row["primary_metric"] = primary_metric(log_dir, tc)
            row["status"] = "PASS"
        except Exception as error:
            row["status"] = "FAIL"
            row["reason"] = str(error)
    else:
        row["status"] = "FAIL"
        row["reason"] = "evidence contract validation failed"
    atomic_json(result_path, row)
    return row


def execute_pair(root, pair, tc, cpuset, timeout, frozen_runtime):
    rows = []
    for arm in arm_sequence(planned_order(pair, tc)):
        try:
            rows.append(execute_arm(
                root, pair, tc, arm, cpuset, timeout, frozen_runtime))
        except Exception as error:
            row = {**expected_result_fields(root, pair, tc, arm),
                   "cpuset": cpuset,
                   "status": "FAIL", "return_code": 1,
                   "reason": repr(error), "resumed": False,
                   "runtime_hashes": frozen_runtime}
            atomic_json(root / "cases" / pair_id(pair, tc) / arm / "result.json", row)
            rows.append(row)
    hashes = [row.get("workload_elf_sha256") for row in rows]
    if any(hashes) and (None in hashes or len(set(hashes)) != 1):
        for row in rows:
            row["status"] = "FAIL"
            row["pair_identity_error"] = "workload ELF identity mismatch within pair"
            path = root / "cases" / pair_id(pair, tc) / row["arm"] / "result.json"
            if path.is_file():
                atomic_json(path, row)
    return rows


def all_results(root, manifest):
    rows = []
    for sample in manifest["samples"]:
        for arm in ARMS:
            path = root / sample["arms"][arm]["result"]
            if path.is_file():
                try:
                    rows.append(json.loads(path.read_text()))
                except ValueError:
                    rows.append({"pair": sample["pair"], "tc": sample["tc"],
                                 "arm": arm, "status": "FAIL",
                                 "reason": "invalid result.json"})
    return rows


def write_summary(root, manifest, running=None, last=None):
    rows = all_results(root, manifest)
    payload = {
        "physical_runs_planned": manifest["physical_runs"],
        "physical_runs_with_result": len(rows),
        "pass": sum(row.get("status") == "PASS" for row in rows),
        "fail": sum(row.get("status") != "PASS" for row in rows),
        "running": running or [], "last": last,
    }
    atomic_json(root / "progress.json", payload)
    atomic_json(root / "run_summary.json", {**payload, "results": rows})


def analyze(root, manifest):
    per_tc = []
    pair_rows = []
    errors = []
    for sample in manifest["samples"]:
        arms = {}
        for arm in ARMS:
            path = root / sample["arms"][arm]["result"]
            if not path.is_file():
                errors.append(f"missing {path}")
                continue
            row = json.loads(path.read_text())
            if row.get("status") != "PASS":
                errors.append(f"non-PASS {path}")
            else:
                arms[arm] = row
        if len(arms) != 2:
            continue
        hashes = {arms[arm].get("workload_elf_sha256") for arm in ARMS}
        elf_identity = ("unavailable" if hashes == {None} else
                        "match" if None not in hashes and len(hashes) == 1 else "mismatch")
        if elf_identity == "mismatch":
            errors.append(f"ELF mismatch {sample['pair_id']}")
            continue
        ack = arms["ack"]["primary_metric"]
        lossless = arms["lossless-oneway"]["primary_metric"]
        pair_rows.append({
            "pair_id": sample["pair_id"], "pair": sample["pair"],
            "tc": sample["tc"], "phase": sample["metric_phase"],
            "order": sample["order"], "workload_elf_identity": elf_identity,
            "ack": ack, "lossless_oneway": lossless,
            "delta_ticks_per_operation": ack["ticks_per_operation"] - lossless["ticks_per_operation"],
            "delta_ns_per_operation": ack["ns_per_operation"] - lossless["ns_per_operation"],
            "delta_percent_of_ack": ((ack["ns_per_operation"] - lossless["ns_per_operation"])
                                     / ack["ns_per_operation"] * 100.0),
        })
    for tc in TCS:
        rows = [row for row in pair_rows if row["tc"] == tc]
        if len(rows) != PAIR_COUNT:
            errors.append(f"TC{tc} has {len(rows)} complete pairs, expected {PAIR_COUNT}")
            continue
        ack_ticks = [row["ack"]["ticks_per_operation"] for row in rows]
        loss_ticks = [row["lossless_oneway"]["ticks_per_operation"] for row in rows]
        ack_ns = [row["ack"]["ns_per_operation"] for row in rows]
        loss_ns = [row["lossless_oneway"]["ns_per_operation"] for row in rows]
        per_tc.append({
            "tc": tc, "phase": PHASES[tc][1], "paired_repetitions": len(rows),
            "ack_mean_ticks_per_operation": statistics.mean(ack_ticks),
            "lossless_oneway_mean_ticks_per_operation": statistics.mean(loss_ticks),
            "paired_mean_delta_ticks_per_operation": statistics.mean(
                row["delta_ticks_per_operation"] for row in rows),
            "ack_mean_ns_per_operation": statistics.mean(ack_ns),
            "lossless_oneway_mean_ns_per_operation": statistics.mean(loss_ns),
            "paired_mean_delta_ns_per_operation": statistics.mean(
                row["delta_ns_per_operation"] for row in rows),
            "paired_mean_delta_percent_of_ack": statistics.mean(
                row["delta_percent_of_ack"] for row in rows),
            "counter_frequencies_hz": sorted({
                metric["counter_frequency_hz"] for row in rows
                for metric in (row["ack"], row["lossless_oneway"])}),
        })
    overall = None
    if len(per_tc) == len(TCS):
        overall = {
            "description": (
                "Equal-weight arithmetic mean across the eight per-TC paired means; "
                "each TC contributes one eighth regardless of operation count or scale. "
                "This is descriptive only and is not a contract verdict."),
            "tc_count": len(per_tc),
            "equal_weight_mean_of_tc_delta_ns_per_operation": statistics.mean(
                row["paired_mean_delta_ns_per_operation"] for row in per_tc),
            "equal_weight_mean_of_tc_delta_percent_of_ack": statistics.mean(
                row["paired_mean_delta_percent_of_ack"] for row in per_tc),
            "delta_definition": "ack - lossless-oneway; positive favors lossless-oneway",
        }
    payload = {
        "schema_version": 1,
        "experiment_id": manifest["experiment_id"],
        "analysis_complete": not errors and len(per_tc) == len(TCS),
        "delta_definition": manifest["delta_definition"],
        "methods": "descriptive paired means only; no t-test or p-value",
        "errors": errors, "pairs": pair_rows, "per_tc": per_tc,
        "overall_equal_weight_summary": overall,
    }
    atomic_json(root / "analysis.json", payload)
    lines = [
        "# Independent optimized Clear-semantics performance analysis", "",
        "Delta is `ack - lossless-oneway`; positive values favor lossless-oneway.",
        "Results are descriptive paired means only; no contract verdict, t-test, or p-value.", "",
        "| TC | Primary phase | Ack mean ns/op | Lossless-oneway mean ns/op | Paired mean delta ns/op | Paired mean delta % of ack |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for row in per_tc:
        lines.append(
            f"| TC{row['tc']} | `{row['phase']}` | "
            f"{row['ack_mean_ns_per_operation']:.6f} | "
            f"{row['lossless_oneway_mean_ns_per_operation']:.6f} | "
            f"{row['paired_mean_delta_ns_per_operation']:+.6f} | "
            f"{row['paired_mean_delta_percent_of_ack']:+.6f}% |")
    lines.extend(["", "## Overall equal-weight descriptive summary", ""])
    if overall:
        lines.extend([
            overall["description"], "",
            f"- Equal-weight mean of per-TC delta ns/op: "
            f"{overall['equal_weight_mean_of_tc_delta_ns_per_operation']:+.6f}",
            f"- Equal-weight mean of per-TC delta percent of ack: "
            f"{overall['equal_weight_mean_of_tc_delta_percent_of_ack']:+.6f}%",
        ])
    else:
        lines.append("Incomplete: " + "; ".join(errors))
    (root / "analysis.md").write_text("\n".join(lines) + "\n")
    return payload


def preflight_docker():
    subprocess.check_call(["docker", "ps"], stdout=subprocess.DEVNULL)
    command_output(["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=pathlib.Path, required=True)
    parser.add_argument("--timeout", type=int, default=10800)
    parser.add_argument("--plan-only", action="store_true",
                        help="write manifest/progress without starting physical runs")
    parser.add_argument("--analyze-only", action="store_true",
                        help="analyze existing result.json evidence")
    args = parser.parse_args()
    root = args.output_root.expanduser().resolve()
    try:
        root.relative_to(ROOT)
    except ValueError:
        parser.error("--output-root must be inside the workspace")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    root.mkdir(parents=True, exist_ok=True)

    manifest_path = root / "manifest.json"
    if args.analyze_only:
        if not manifest_path.is_file():
            parser.error("--analyze-only requires an existing manifest.json")
        manifest = json.loads(manifest_path.read_text())
        result = analyze(root, manifest)
        return 0 if result["analysis_complete"] else 1

    preflight_docker()  # Required before any physical execution or planning.
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("physical_runs") != 48 or manifest.get("testcases") != list(TCS):
            parser.error("existing manifest does not match the fixed 48-run matrix")
    else:
        manifest = make_manifest(root, evidence_fingerprint())
        atomic_json(manifest_path, manifest)
    write_summary(root, manifest)
    if args.plan_only:
        return 0

    frozen_runtime = manifest["fingerprint"]["runtime"]
    # TC131 consumes all CPUs and therefore runs before any smaller-case job.
    for pair in range(1, PAIR_COUNT + 1):
        execute_pair(root, pair, 131, FULL_CPUSET, args.timeout, frozen_runtime)
        write_summary(root, manifest, last={"pair": pair, "tc": 131,
                                            "cpuset": FULL_CPUSET})

    jobs = [(pair, tc) for pair in range(1, PAIR_COUNT + 1) for tc in TCS if tc != 131]
    pending = list(jobs)
    active = {}
    lock = threading.Lock()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        while pending or active:
            used_slots = {slot for _, slot in active.values()}
            while pending and len(active) < 4:
                slot = next(index for index in range(4) if index not in used_slots)
                used_slots.add(slot)
                pair, tc = pending.pop(0)
                future = pool.submit(execute_pair, root, pair, tc, LANES[slot],
                                     args.timeout, frozen_runtime)
                active[future] = ((pair, tc), slot)
            running = [{"pair": job[0], "tc": job[1], "cpuset": LANES[slot]}
                       for job, slot in active.values()]
            with lock:
                write_summary(root, manifest, running=running)
            done, _ = concurrent.futures.wait(
                active, timeout=30,
                return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                job, slot = active.pop(future)
                try:
                    future.result()
                except Exception as error:
                    # execute_pair normally converts arm failures to result rows;
                    # preserve an orchestration failure in progress if it escapes.
                    atomic_json(root / "orchestration_failure.json", {
                        "pair": job[0], "tc": job[1], "cpuset": LANES[slot],
                        "reason": repr(error), "time": time.time(),
                    })
                with lock:
                    write_summary(root, manifest, last={
                        "pair": job[0], "tc": job[1], "cpuset": LANES[slot]})
    result = analyze(root, manifest)
    write_summary(root, manifest)
    return 0 if result["analysis_complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
