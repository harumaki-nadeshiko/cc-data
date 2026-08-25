#!/usr/bin/env python3
"""Extract unified Metric 1/2/3 evidence directly from explicit raw-log runs.

Only the Python standard library is required.  Pairing is identity-only and the
tool deliberately emits no confidence interval, t-test, or p-value.
"""

import argparse
import copy
import csv
import gzip
import json
import pathlib
import re
import statistics
import sys
import tempfile
from collections import defaultdict


PROFILES = ("naive", "spill-noopt", "optimized")
M2 = {
    135: ("preserved_sharer_first_load", "3n1s", 1, 24),
    136: ("preserved_owner_store_complete", "3n1s", 1, 24),
    137: ("new_requester_first_load", "3n1s", 2, 24),
    138: ("dirty_owner_handoff_store", "3n1s", 2, 24),
    139: ("mixed_batch_16ops", "3n1s", 1, 16),
    140: ("cross_l2_owner_store", "3n1s", 0, 24),
    217: ("ha10_catalog_batch_16ops", "2n1s", 1, 8),
}
M3 = {
    228: {"remote_read": ("timer", "topology_remote_read", "aggregate")},
    229: {"ownership_handoff": ("timer", "topology_ownership_handoff", "aggregate")},
    230: {"shared_to_writer": ("timer", "topology_all_sharer_to_writer", "aggregate")},
    231: {"clean_shared_control": ("timer", "clean_shared_read_service", "aggregate")},
    232: {
        "hot_key_read": ("timer", "hot_key_read_service", "aggregate"),
        "hot_key_write": ("timer", "hot_key_write_service", "aggregate"),
    },
    233: {
        "producer_consumer_load": ("latency", "producer_consumer_load", "aggregate"),
        "producer_consumer_service": ("timer", "producer_consumer_service", "aggregate"),
    },
    234: {
        "queued_token_end_to_end": ("timer", "queued_token_end_to_end", "aggregate"),
        "queued_token_store": ("timer", "queued_token_store", "aggregate"),
    },
    235: {
        "catalog_kv_end_to_end": ("timer", "catalog_kv_end_to_end", "max"),
        "catalog_kv_service": ("timer", "catalog_kv_service", "aggregate"),
    },
}
M3_PRIMARY = {
    228: {"remote_read": 1.0}, 229: {"ownership_handoff": 1.0},
    230: {"shared_to_writer": 1.0}, 231: {"clean_shared_control": 1.0},
    232: {"hot_key_read": 2 / 3, "hot_key_write": 1 / 3},
    233: {"producer_consumer_service": 1.0},
    234: {"queued_token_end_to_end": 1.0},
    235: {"catalog_kv_end_to_end": 1.0},
}
M3_AGGREGATES = {
    "core_equal_weight": {(228, "remote_read"): 1 / 3,
                          (229, "ownership_handoff"): 1 / 3,
                          (230, "shared_to_writer"): 1 / 3},
    "representative_equal_weight": {(231, "clean_shared_control"): 1 / 5,
                                    (232, "hot_key_read"): 2 / 15,
                                    (232, "hot_key_write"): 1 / 15,
                                    (233, "producer_consumer_service"): 1 / 5,
                                    (234, "queued_token_end_to_end"): 1 / 5,
                                    (235, "catalog_kv_end_to_end"): 1 / 5},
}
STATE_RE = re.compile(r"\[UBCC-STATE\].*capacity=(\d+).*policy=([A-Za-z0-9_-]+)")
POLICY_RE = re.compile(r"\[UBIO-POLICY\].*?effective=([A-Za-z0-9_-]+)")
TIMER_RE = re.compile(r"\[GUEST-TIMER\]\s+node=(\d+)\s+phase=(\S+)\s+operations=(\d+)\s+counter_ticks=(\d+)\s+counter_frequency_hz=(\d+)\s+source=(\S+)\s+unit=(\S+)")
LAT_RE = re.compile(r"\[PERF-LATENCY\]\s+node=(\d+)\s+phase=(\S+)\s+samples=(\d+)\s+min=(\d+)\s+p50=(\d+)\s+p95=(\d+)\s+p99=(\d+)\s+max=(\d+)\s+mean=(\d+)\s+counter_frequency_hz=(\d+)\s+source=(\S+)\s+unit=(\S+)")
SIMOUT_PATTERNS = (
    re.compile(r"simout_tc(\d+)_node(\d+)\.log(?:\.gz)?$"),
    re.compile(r"simout_n(\d+)(?:\.log)?(?:\.gz)?$"),
)
UBIO_DIR_RE = re.compile(r"ubio(?:_tc(\d+))?_n(\d+)_s(\d+)$")
PROCESS_MANIFEST = "[PROCESS-MANIFEST]"


class ExtractError(Exception):
    pass


def open_text(path):
    return gzip.open(path, "rt", errors="replace") if path.suffix == ".gz" else path.open(errors="replace")


def resolve(base, value):
    path = pathlib.Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def norm_profile(value):
    aliases = {"spill": "spill-noopt", "spill_noopt": "spill-noopt",
               "spill-opt": "optimized", "spill_opt": "optimized"}
    value = aliases.get(str(value).lower(), str(value).lower())
    if value not in PROFILES:
        raise ExtractError(f"unknown profile {value!r}")
    return value


def topology_size(value):
    match = re.fullmatch(r"(\d+)n(\d+)s", str(value).lower())
    if not match:
        raise ExtractError(f"invalid topology {value!r}; expected e.g. 8n1s")
    return tuple(map(int, match.groups()))


def discover_simouts(root, tc):
    by_node = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        node = None
        match = SIMOUT_PATTERNS[0].fullmatch(path.name)
        if match:
            if int(match.group(1)) != tc:
                continue
            node = int(match.group(2))
        else:
            match = SIMOUT_PATTERNS[1].fullmatch(path.name)
            if match:
                node = int(match.group(1))
                parent = re.fullmatch(r"node(\d+)", path.parent.name)
                if parent and int(parent.group(1)) != node:
                    raise ExtractError(f"simout node mismatch: {path}")
        if node is None:
            continue
        if node in by_node:
            raise ExtractError(f"duplicate simout node {node}: {by_node[node]} and {path}")
        by_node[node] = path.resolve()
    if not by_node:
        raise ExtractError(f"no supported simout files below {root}")
    return dict(sorted(by_node.items()))


def all_log_files(root):
    return [path for path in sorted(root.rglob("*"))
             if path.is_file() and (path.suffix in (".log", ".out", ".txt", ".gz") or path.name in ("stdout", "stderr"))]


def warning(code, run_id, message):
    return {"severity": "WARNING", "code": code, "run_id": str(run_id),
            "message": message}


def process_manifests(path):
    rows = []
    with open_text(path) as stream:
        for line in stream:
            if PROCESS_MANIFEST not in line:
                continue
            try:
                rows.append(json.loads(line.split(PROCESS_MANIFEST, 1)[1].strip()))
            except (ValueError, TypeError):
                continue
    return rows


def _logical_logs(root, evidence_file):
    """Return all streams belonging to a directory process, or one root stream."""
    if evidence_file.parent == root:
        return [evidence_file.resolve()]
    return [p.resolve() for p in all_log_files(evidence_file.parent)]


def discover_home_ubio_logs(root, tc, node=0, socket=0, run_id="",
                            explicit_logs=()):
    """Discover home UBIO evidence and return ``(logs, warnings)``.

    Directory identity is preferred, then PROCESS-MANIFEST identity, and finally
    capacity-bearing streams.  Multiple stdout/stderr files in one process
    directory are one logical source rather than duplicate UBIO processes.
    """
    if explicit_logs:
        logs = []
        for path in explicit_logs:
            path = pathlib.Path(path).resolve()
            if path.is_dir():
                logs.extend(all_log_files(path))
            elif path.is_file():
                logs.append(path)
            else:
                raise ExtractError(f"explicit home UBIO path does not exist: {path}")
        logs = sorted(set(logs), key=str)
        if not logs:
            raise ExtractError("explicit home UBIO paths contain no readable logs")
        parse_capacity(logs)
        return logs, [warning(
            "HOME_UBIO_EXPLICIT", run_id,
            "home UBIO selected from explicit paths: " +
            ", ".join(str(path) for path in logs))]

    current, legacy = set(), set()
    for path in root.rglob("ubio*_n*_s*"):
        if not path.is_dir():
            continue
        match = UBIO_DIR_RE.fullmatch(path.name)
        if not match:
            continue
        tc_text, node_text, socket_text = match.groups()
        if (int(node_text), int(socket_text)) != (node, socket):
            continue
        if tc_text is None:
            legacy.add(path.resolve())
        elif int(tc_text) == tc:
            current.add(path.resolve())
    candidates = current or legacy
    if len(candidates) == 1:
        logs = all_log_files(next(iter(candidates)))
        if logs:
            try:
                parse_capacity(logs)
                return logs, []
            except (ExtractError, OSError, ValueError, TypeError):
                pass
    if len(candidates) > 1:
        recognized = {}
        for directory in sorted(candidates):
            logs = all_log_files(directory)
            try:
                parsed = parse_capacity(logs)
            except (ExtractError, OSError, ValueError, TypeError) as error:
                raise ExtractError(f"recognized home UBIO source {directory} invalid: {error}")
            recognized[str(directory)] = ((parsed["resident_capacity"], parsed["policy"],
                                           parsed["effective_unique"]), logs)
        if len({item[0] for item in recognized.values()}) > 1:
            raise ExtractError("ambiguous recognized home UBIO directories disagree: " +
                               ", ".join(f"{key}={item[0]}" for key, item in recognized.items()))
        logs = sorted({path for item in recognized.values() for path in item[1]})
        return logs, [warning("HOME_UBIO_IDENTICAL_MULTIPLE", run_id,
                              "multiple recognized home UBIO directories are identical; "
                              "deterministic union used: " + ", ".join(recognized))]

    files = all_log_files(root)
    manifest_sources = {}
    for path in files:
        for row in process_manifests(path):
            row_tc = row.get("tc")
            if (row.get("component") == "ubio" and int(row.get("node", -1)) == node and
                    int(row.get("socket", -1)) == socket and
                    (row_tc is None or int(row_tc) == tc)):
                key = str(path.parent.resolve()) if path.parent != root else str(path.resolve())
                manifest_sources[key] = _logical_logs(root, path)
    if manifest_sources:
        parsed_sources = {}
        for key, logs in manifest_sources.items():
            try:
                item = parse_capacity(logs)
                parsed_sources[key] = (item["resident_capacity"], item["policy"],
                                       item["effective_unique"])
            except (ExtractError, OSError, ValueError, TypeError):
                parsed_sources[key] = None
        usable = {key: value for key, value in parsed_sources.items() if value is not None}
        if len(usable) > 1 and len(set(usable.values())) > 1:
            raise ExtractError("ambiguous PROCESS-MANIFEST home UBIO sources disagree: " +
                                ", ".join(f"{k}={v}" for k, v in sorted(parsed_sources.items())))
        if usable:
            logs = sorted({p for key, group in manifest_sources.items()
                           if key in usable for p in group})
            code = "HOME_UBIO_IDENTICAL_MULTIPLE" if len(usable) > 1 else "HOME_UBIO_FALLBACK"
            return logs, [warning(code, run_id,
                                  "home UBIO selected by PROCESS-MANIFEST: " +
                                  ", ".join(sorted(usable)))]

    capacity_sources = {}
    for path in files:
        try:
            parsed = parse_capacity([path])
        except (ExtractError, OSError, ValueError, TypeError):
            continue
        key = str(path.parent.resolve()) if path.parent != root else str(path.resolve())
        capacity_sources.setdefault(key, {"logs": _logical_logs(root, path), "values": []})
        capacity_sources[key]["values"].append(parsed)
    normalized = {}
    for key, source in capacity_sources.items():
        try:
            parsed = parse_capacity(source["logs"])
        except (ExtractError, OSError, ValueError, TypeError):
            parsed = source["values"][0]
        normalized[key] = (parsed["resident_capacity"], parsed["policy"],
                           parsed["effective_unique"])
    if not normalized:
        raise ExtractError(f"Metric1 found no home UBIO identity or capacity-bearing source below {root}")
    values = set(normalized.values())
    details = ", ".join(f"{key}={value}" for key, value in sorted(normalized.items()))
    if len(values) > 1:
        raise ExtractError("ambiguous capacity-bearing UBIO sources disagree: " + details)
    logs = sorted({p for source in capacity_sources.values() for p in source["logs"]})
    code = "HOME_UBIO_IDENTICAL_MULTIPLE" if len(capacity_sources) > 1 else "HOME_UBIO_FALLBACK"
    message = ("multiple unidentified capacity sources are identical; deterministic union used: "
               if len(capacity_sources) > 1 else
               "only capacity-bearing source used without home identity: ") + details
    return logs, [warning(code, run_id, message)]


def marker_rows(paths, kind, phase):
    regex = TIMER_RE if kind == "timer" else LAT_RE
    rows = []
    for path in paths:
        with open_text(path) as stream:
            for line_no, line in enumerate(stream, 1):
                match = regex.search(line)
                if not match or match.group(2) != phase:
                    continue
                if kind == "timer":
                    node, count, ticks, freq, source, unit = (int(match.group(1)), int(match.group(3)),
                        int(match.group(4)), int(match.group(5)), match.group(6), match.group(7))
                else:
                    node, count, ticks, freq, source, unit = (int(match.group(1)), int(match.group(3)),
                        int(match.group(9)), int(match.group(10)), match.group(11), match.group(12))
                    ordered = list(map(int, match.group(4, 5, 6, 7, 8)))
                    if ordered != sorted(ordered) or not ordered[0] <= ticks <= ordered[-1]:
                        raise ExtractError(f"invalid PERF-LATENCY ordering in {path}:{line_no}")
                if count <= 0 or ticks <= 0 or freq <= 0 or source != "arm_cntvct_el0" or unit != "counter_ticks":
                    raise ExtractError(f"invalid {kind} marker in {path}:{line_no}")
                rows.append({"file": str(path), "line": line_no, "node": node,
                             "count": count, "ticks": ticks, "frequency_hz": freq})
    return rows


def correctness(run, simulator_dir, policy):
    tc = run["tc"]
    verifiers = [p for p in simulator_dir.rglob(f"verify_tc{tc}.log*") if p.is_file()]
    exit_dirs = [p for p in simulator_dir.rglob(f"child_status_tc{tc}") if p.is_dir()]
    present = bool(verifiers or exit_dirs)
    required = policy in ("strict", "required")
    if not present and policy == "optional":
        return {"policy": policy, "status": "NOT_PRESENT_OPTIONAL", "verifier": None, "child_exits": []}
    if len(verifiers) != 1:
        raise ExtractError(f"correctness requires exactly one verifier, found {len(verifiers)}")
    with open_text(verifiers[0]) as stream:
        lines = [line.strip() for line in stream if line.strip()]
    if not lines or lines[-1] != f">>> TC{tc} PASSED <<<":
        raise ExtractError("verifier final non-empty line is not PASS")
    if len(exit_dirs) != 1:
        raise ExtractError(f"correctness requires exactly one child-status directory, found {len(exit_dirs)}")
    exits = {p.name: p for p in exit_dirs[0].glob("*.exit") if p.is_file()}
    if not exits:
        raise ExtractError("child-status directory contains no .exit files")
    values = {name: path.read_text(errors="replace").strip() for name, path in exits.items()}
    bad = {name: value for name, value in values.items() if value != "0"}
    if bad:
        raise ExtractError(f"nonzero/invalid child exits: {bad}")
    nodes, sockets = topology_size(run["topology"])
    expected = {f"gem5_node{n}.exit" for n in range(nodes)}
    expected |= {f"ubio_n{n}_s{s}.exit" for n in range(nodes) for s in range(sockets)}
    expected.add("networksim.exit")
    if set(exits) != expected:
        raise ExtractError(f"child exit identity mismatch missing={sorted(expected-set(exits))} extra={sorted(set(exits)-expected)}")
    return {"policy": policy, "status": "PASS", "verifier": str(verifiers[0]),
            "child_exits": [{"path": str(exits[name]), "value": values[name]} for name in sorted(exits)],
            "required": required}


def parse_capacity(paths):
    capacity, exact, policies, fallback_policies = 0, None, set(), set()
    sources = []
    for path in paths:
        with open_text(path) as stream:
            for line_no, line in enumerate(stream, 1):
                match = STATE_RE.search(line)
                if match:
                    capacity = max(capacity, int(match.group(1)))
                    policies.add(match.group(2))
                    sources.append(f"{path}:{line_no}")
                policy_match = POLICY_RE.search(line)
                if policy_match:
                    policies.add(policy_match.group(1))
                if PROCESS_MANIFEST in line:
                    try:
                        payload = json.loads(line.split(PROCESS_MANIFEST, 1)[1].strip())
                    except (TypeError, ValueError):
                        payload = {}
                    if payload.get("component") == "ubio":
                        manifest_policy = payload.get("overflow_policy")
                        if manifest_policy:
                            fallback_policies.add(str(manifest_policy))
                if "[UBCC-STATS]" not in line or "{" not in line:
                    continue
                try:
                    payload = json.loads(line[line.index("{"):])
                except ValueError:
                    continue
                if payload.get("residentCapacity") is not None:
                    capacity = max(capacity, int(payload["residentCapacity"]))
                if int(payload.get("h64ExactLiveKnown", 0)) == 1:
                    exact = max(exact or 0, int(payload.get("h64ExactLiveCount", 0)))
    if not policies:
        policies = fallback_policies
    if not capacity or len(policies) != 1:
        raise ExtractError(f"UBCC capacity/policy invalid: capacity={capacity} policies={sorted(policies)}")
    policy = next(iter(policies))
    if policy != "naive" and exact is None:
        raise ExtractError("spill policy lacks validated H64 exact-live marker")
    return {"policy": policy, "resident_capacity": capacity, "h64_exact_live": exact,
            "effective_unique": capacity if policy == "naive" else max(capacity, exact),
            "sources": sources}


def extract_run(run, base, policy):
    required = {"metric", "tc", "repetition", "topology", "simulator_log_dir", "simout_dir"}
    missing = required - set(run)
    if missing:
        raise ExtractError(f"missing fields {sorted(missing)}")
    out = dict(run)
    if "id" not in run:
        raise ExtractError("internal run id was not assigned")
    out["id"], out["metric"], out["tc"] = str(run["id"]), int(run["metric"]), int(run["tc"])
    out["repetition"], out["topology"] = str(run["repetition"]), str(run["topology"]).lower()
    if out["metric"] not in (1, 2, 3):
        raise ExtractError("metric must be 1, 2, or 3")
    simulator = resolve(base, run["simulator_log_dir"])
    simout = resolve(base, run["simout_dir"])
    if not simulator.is_dir() or not simout.is_dir():
        raise ExtractError(f"input directory missing simulator={simulator} simout={simout}")
    out["simulator_log_dir"], out["simout_dir"] = str(simulator), str(simout)
    out["simout_by_node"] = {str(k): str(v) for k, v in discover_simouts(simout, out["tc"]).items()}
    paths = [pathlib.Path(p) for p in out["simout_by_node"].values()]
    out["correctness"] = correctness(out, simulator, policy)
    out["contract_warnings"] = list(run.get("_contract_warnings", []))
    if out["metric"] in (1, 2):
        out["profile"] = norm_profile(run.get("profile"))
    if out["metric"] == 1:
        phase = str(run.get("phase", "post_pressure_catalog_reuse"))
        rows = marker_rows(paths, "timer", phase)
        by_node = defaultdict(list)
        for row in rows:
            by_node[row["node"]].append(row)
        expected_nodes = tuple(map(int, run.get("timer_nodes", [1, 2])))
        if set(by_node) != set(expected_nodes) or any(len(by_node[n]) != 1 for n in expected_nodes):
            raise ExtractError(f"Metric1 timer duplicate/missing expected={expected_nodes} counts={dict((n,len(v)) for n,v in by_node.items())}")
        if len({row["frequency_hz"] for row in rows}) != 1:
            raise ExtractError("Metric1 timer frequency mismatch")
        timer = [{**row, "ticks_per_operation": row["ticks"] / row["count"],
                  "ns_per_operation": row["ticks"] * 1e9 / row["frequency_hz"] / row["count"]} for row in rows]
        home_node = int(run.get("home_node", 0))
        home_socket = int(run.get("home_socket", 0))
        explicit_home = []
        if run.get("home_ubio_log_dir"):
            explicit_home.append(resolve(base, run["home_ubio_log_dir"]))
        raw_home_logs = run.get("home_ubio_logs", [])
        if isinstance(raw_home_logs, (str, pathlib.Path)):
            raw_home_logs = [raw_home_logs]
        explicit_home.extend(resolve(base, value) for value in raw_home_logs)
        capacity_logs, discovery_warnings = discover_home_ubio_logs(
            simulator, out["tc"], home_node, home_socket, out["id"],
            explicit_home)
        out["contract_warnings"].extend(discovery_warnings)
        out["home_ubio_logs"] = [str(path) for path in capacity_logs]
        out["metrics"] = {"capacity": parse_capacity(capacity_logs), "timers": timer, "phase": phase,
                           "mean_ns_per_operation": statistics.mean(x["ns_per_operation"] for x in timer)}
        expected_policy = "naive" if out["profile"] == "naive" else "spill"
        standard = (out["tc"] == 131 and out["topology"] == "8n1s" and
                    phase == "post_pressure_catalog_reuse" and expected_nodes == (1, 2) and
                    home_node == 0 and home_socket == 0 and
                    out["metrics"]["capacity"]["policy"] == expected_policy)
        if not standard:
            out["contract_warnings"].append(warning(
                "NONSTANDARD_CONTRACT", out["id"],
                f"Metric1 descriptive extension tc={out['tc']} topology={out['topology']} "
                f"phase={phase} timer_nodes={list(expected_nodes)} home={home_node}/{home_socket} "
                f"policy={out['metrics']['capacity']['policy']} expected_policy={expected_policy}"))
    elif out["metric"] == 2:
        registered = out["tc"] in M2
        if registered:
            default_phase, official_topology, default_node, default_samples = M2[out["tc"]]
            phase = str(run.get("phase", default_phase))
            node = int(run.get("expected_node", default_node))
            samples = int(run.get("expected_samples", default_samples))
        else:
            if "phase" not in run:
                raise ExtractError("Metric2 unknown TC requires explicit phase")
            phase, official_topology = str(run["phase"]), None
            node = int(run["expected_node"]) if "expected_node" in run else None
            samples = int(run["expected_samples"]) if "expected_samples" in run else None
        rows = marker_rows(paths, "latency", phase)
        if len(rows) != 1:
            raise ExtractError(f"Metric2 expected exactly one phase={phase}, got {len(rows)}")
        row = rows[0]
        node = row["node"] if node is None else node
        samples = row["count"] if samples is None else samples
        if row["node"] != node or row["count"] != samples:
            raise ExtractError(f"TC{out['tc']} marker contract requires node={node} samples={samples}, got node={row['node']} samples={row['count']}")
        out["metrics"] = {"phase": phase, "node": node, "samples": samples,
                          "mean_ticks": row["ticks"], "frequency_hz": row["frequency_hz"],
                           "mean_ns": row["ticks"] * 1e9 / row["frequency_hz"], "source": row}
        standard = (registered and out["topology"] == official_topology and phase == default_phase and
                    node == default_node and samples == default_samples)
        if not standard:
            out["contract_warnings"].append(warning(
                "NONSTANDARD_CONTRACT", out["id"],
                f"Metric2 descriptive extension tc={out['tc']} topology={out['topology']} "
                f"phase={phase} node={node} samples={samples}"))
    else:
        if run.get("arm") not in ("ourcc", "ha-vi"):
            raise ExtractError("Metric3 requires arm ourcc/ha-vi")
        if "pair" not in run or run.get("order") not in ("AB", "BA"):
            raise ExtractError("Metric3 requires explicit pair and order=AB/BA")
        out["arm"], out["pair"], out["order"] = run["arm"], str(run["pair"]), run["order"]
        registered = out["tc"] in M3
        specs = M3.get(out["tc"])
        if specs is None:
            raw_specs = run.get("metric_specs")
            if not isinstance(raw_specs, dict) or not raw_specs:
                raise ExtractError("PARSER_SPEC_REQUIRED: unknown Metric3 TC requires metric_specs")
            specs = {}
            for name, spec in raw_specs.items():
                if not isinstance(spec, dict) or spec.get("kind") not in ("timer", "latency") or not spec.get("phase") or spec.get("reduction") not in ("aggregate", "max"):
                    raise ExtractError(f"PARSER_SPEC_REQUIRED: invalid metric_specs[{name!r}]")
                specs[str(name)] = (spec["kind"], str(spec["phase"]), spec["reduction"])
        metrics = {}
        for name, (kind, phase, reduction) in specs.items():
            rows = marker_rows(paths, kind, phase)
            if not rows:
                raise ExtractError(f"Metric3 missing {kind} phase={phase}")
            freqs = {row["frequency_hz"] for row in rows}
            if len(freqs) != 1:
                raise ExtractError(f"Metric3 frequency mismatch phase={phase}")
            if reduction == "max":
                value = max(row["ticks"] / row["count"] for row in rows)
            elif kind == "latency":
                value = sum(row["ticks"] * row["count"] for row in rows) / sum(
                    row["count"] for row in rows)
            else:
                value = sum(row["ticks"] for row in rows) / sum(row["count"] for row in rows)
            frequency = next(iter(freqs))
            metrics[name] = {"ticks_per_operation": value, "counter_frequency_hz": frequency,
                             "ns_per_operation": value * 1e9 / frequency, "sources": rows}
        out["metrics"] = metrics
        standard = registered and out["topology"] == "2n1s"
        if not standard:
            out["contract_warnings"].append(warning(
                "NONSTANDARD_CONTRACT", out["id"],
                f"Metric3 descriptive extension tc={out['tc']} topology={out['topology']}"))
    out["standard_contract"] = bool(standard)
    out["contract_class"] = "standard" if standard else "extension"
    out["status"] = "VALID"
    return out


def logical_slot(run):
    """Identity of one experiment coordinate, including extension dimensions."""
    if run["metric"] == 1:
        return (1, run["repetition"], run["tc"], run["topology"], run["profile"],
                run["metrics"].get("phase"),
                tuple(item["node"] for item in run["metrics"].get("timers", [])),
                int(run.get("home_node", 0)), int(run.get("home_socket", 0)))
    if run["metric"] == 2:
        return (2, run["repetition"], run["tc"], run["topology"], run["profile"],
                run["metrics"].get("phase"), run["metrics"].get("node"),
                run["metrics"].get("samples"))
    specs = tuple(sorted((name, value.get("ticks_per_operation"),
                          value.get("counter_frequency_hz"))
                         for name, value in run["metrics"].items()))
    return (3, run["pair"], run["tc"], run["topology"], run["order"],
            run["arm"], tuple(name for name, _, _ in specs))


def requirement(manifest, name, default):
    value = manifest.get("requirements", {}).get(name, default)
    return value if isinstance(value, dict) else default


def descriptive_view(runs):
    """Small contract-neutral matrix and useful profile/arm comparisons."""
    matrix, groups = [], defaultdict(list)
    for run in runs:
        value = run["metrics"].get("mean_ns_per_operation", run["metrics"].get("mean_ns"))
        if value is None and run["metric"] == 3:
            value = statistics.mean(x["ns_per_operation"] for x in run["metrics"].values())
        row = {"metric": f"Metric{run['metric']}", "level": "run", "identity": run["id"],
               "tc": f"TC{run['tc']}", "value": value, "unit": "ns/op",
               "status": "DESCRIPTIVE", "detail": run["contract_class"]}
        matrix.append(row)
        key = (run["metric"], run["tc"], run["topology"], run.get("repetition"),
               run.get("profile", run.get("arm", "")))
        groups[key].append(value)
    summaries = []
    for key, values in sorted(groups.items(), key=lambda x: tuple(map(str, x[0]))):
        summaries.append({"metric": key[0], "tc": key[1], "topology": key[2],
                          "repetition": key[3], "profile_or_arm": key[4],
                          "runs": len(values), "mean_ns_per_operation": statistics.mean(values)})

    comparisons = []
    by_profile = defaultdict(dict)
    for run in runs:
        if run["metric"] in (1, 2):
            by_profile[(run["metric"], run["tc"], run["topology"],
                        run["repetition"])][run["profile"]] = run
    for key, profiles in sorted(by_profile.items(), key=lambda x: tuple(map(str, x[0]))):
        if all(p in profiles for p in PROFILES):
            naive, optimized = profiles["naive"], profiles["optimized"]
            if key[0] == 1:
                comparisons.append({"metric": 1, "tc": key[1], "topology": key[2],
                                    "repetition": key[3],
                                    "capacity_ratio_spill_to_naive": profiles["spill-noopt"]["metrics"]["capacity"]["effective_unique"] / naive["metrics"]["capacity"]["effective_unique"],
                                    "optimized_delta_ns": optimized["metrics"]["mean_ns_per_operation"] - naive["metrics"]["mean_ns_per_operation"]})
            else:
                comparisons.append({"metric": 2, "tc": key[1], "topology": key[2],
                                    "repetition": key[3],
                                    "optimized_reduction_pct": (naive["metrics"]["mean_ns"] - optimized["metrics"]["mean_ns"]) / naive["metrics"]["mean_ns"] * 100})
    m3_pairs = []
    pair_groups = defaultdict(dict)
    for run in runs:
        if run["metric"] == 3:
            pair_groups[(run.get("pair"), run["tc"], run.get("order"),
                         run["topology"])][run["arm"]] = run
    for key, arms in sorted(pair_groups.items(), key=lambda x: tuple(map(str, x[0]))):
        if set(arms) != {"ourcc", "ha-vi"}:
            continue
        common = sorted(set(arms["ourcc"]["metrics"]) & set(arms["ha-vi"]["metrics"]))
        m3_pairs.append({"pair": key[0], "tc": key[1], "order": key[2],
                         "topology": key[3],
                         "metrics": {name: {
                             "ourcc_ticks_per_operation": arms["ourcc"]["metrics"][name]["ticks_per_operation"],
                             "ha_vi_ticks_per_operation": arms["ha-vi"]["metrics"][name]["ticks_per_operation"],
                             "delta_ticks": arms["ha-vi"]["metrics"][name]["ticks_per_operation"] - arms["ourcc"]["metrics"][name]["ticks_per_operation"]}
                             for name in common}})
    return {"runs": len(runs), "summaries": summaries,
            "comparisons": comparisons, "metric3_pairs": m3_pairs, "matrix": matrix}


def aggregate_results(data, resolved, ingestion_issues, output_dir=None,
                      manifest=None, ingestion=None):
    """Apply the frozen formulas to already parsed, in-memory run records."""
    issues = list(ingestion_issues)
    # Duplicate logical slots are never silently selected.
    slots = defaultdict(list)
    for run in resolved:
        key = logical_slot(run)
        slots[key].append(run)
    bad_ids = set()
    for key, rows in slots.items():
        if len(rows) > 1:
            bad_ids.update(row["id"] for row in rows)
            issues.append({"severity": "ERROR", "code": "DUPLICATE_SLOT", "run_id": ",".join(row["id"] for row in rows),
                           "message": f"duplicate logical slot {key}"})
    resolved = [row for row in resolved if row["id"] not in bad_ids]
    all_resolved = list(resolved)
    standard_resolved = [row for row in all_resolved if row.get("standard_contract", True)]
    extension_resolved = [row for row in all_resolved if not row.get("standard_contract", True)]
    resolved = standard_resolved

    matrix, per_run = [], []
    for run in resolved:
        value = run["metrics"].get("mean_ns_per_operation", run["metrics"].get("mean_ns"))
        per_run.append({"run_id": run["id"], "metric": run["metric"], "tc": run["tc"],
                        "repetition": run["repetition"], "profile": run.get("profile", ""),
                        "arm": run.get("arm", ""), "pair": run.get("pair", ""), "order": run.get("order", ""),
                        "value": value if value is not None else "MULTI", "unit": "ns/op" if value is not None else "ticks/op",
                        "status": run["status"]})
        matrix.append({"metric": f"Metric{run['metric']}", "level": "run", "identity": run["id"],
                       "tc": f"TC{run['tc']}", "value": value, "unit": "ns/op" if value is not None else "multiple",
                       "status": run["status"], "detail": run.get("profile", run.get("arm", ""))})

    # Metric 1 comparisons and completeness.
    m1 = [r for r in resolved if r["metric"] == 1]
    m1_req = requirement(data, "metric1", {"repetitions": sorted({r["repetition"] for r in m1})})
    m1_reps = [str(x) for x in m1_req.get("repetitions", [])]
    m1_by = {(r["repetition"], r["profile"]): r for r in m1}
    m1_missing = [(rep, p) for rep in m1_reps for p in PROFILES if (rep, p) not in m1_by]
    m1_comp = []
    for rep in m1_reps:
        if all((rep, p) in m1_by for p in PROFILES):
            naive, spill = m1_by[rep, "naive"], m1_by[rep, "spill-noopt"]
            ratio = spill["metrics"]["capacity"]["effective_unique"] / naive["metrics"]["capacity"]["effective_unique"]
            delta_ns = spill["metrics"]["mean_ns_per_operation"] - naive["metrics"]["mean_ns_per_operation"]
            row = {"repetition": rep, "capacity_ratio": ratio, "guest_delta_ns_per_operation": delta_ns,
                   "guest_delta_cycles": delta_ns * 2.0, "pass": ratio >= 1.5 and delta_ns * 2.0 < 50}
            m1_comp.append(row)
            matrix.append({"metric": "Metric1", "level": "repetition", "identity": rep, "tc": "TC131",
                           "value": ratio, "unit": "capacity-ratio", "status": "PASS" if row["pass"] else "FAIL",
                           "detail": f"guest_delta_ns={delta_ns:.9g}"})
    m1_status = ("NOT_REQUESTED" if not m1_reps and not m1 else
                 "INCOMPLETE" if m1_missing or not m1_reps else
                 "PASS" if all(r["pass"] for r in m1_comp) else "FAIL")

    # Metric 2 per repetition/case and equal-weight aggregate.
    m2 = [r for r in resolved if r["metric"] == 2]
    m2_req = requirement(data, "metric2", {"repetitions": sorted({r["repetition"] for r in m2}),
                                             "testcases": sorted({r["tc"] for r in m2})})
    m2_reps, m2_tcs = [str(x) for x in m2_req.get("repetitions", [])], [int(x) for x in m2_req.get("testcases", [])]
    m2_official_set = set(m2_tcs) == set(M2)
    m2_by = {(r["repetition"], r["tc"], r["profile"]): r for r in m2}
    m2_missing = [(rep, tc, p) for rep in m2_reps for tc in m2_tcs for p in PROFILES if (rep, tc, p) not in m2_by]
    m2_cases, rep_means, applicable_sets = [], defaultdict(list), {}
    for rep in m2_reps:
        for tc in m2_tcs:
            if all((rep, tc, p) in m2_by for p in PROFILES):
                means = {p: m2_by[rep, tc, p]["metrics"]["mean_ns"] for p in PROFILES}
                reduction = (means["naive"] - means["optimized"]) / means["naive"] * 100
                applicable = means["naive"] >= 500
                row = {"repetition": rep, "tc": tc, "means_ns": means,
                       "optimized_reduction_pct": reduction, "applicable": applicable}
                m2_cases.append(row)
                if applicable:
                    rep_means[rep].append(reduction)
                matrix.append({"metric": "Metric2", "level": "TC", "identity": rep, "tc": f"TC{tc}",
                               "value": reduction, "unit": "%", "status": "APPLICABLE" if applicable else "NOT_APPLICABLE",
                               "detail": f"naive_ns={means['naive']:.9g}"})
        applicable_sets[rep] = tuple(
            row["tc"] for row in m2_cases
            if row["repetition"] == rep and row["applicable"])
    empty_applicable = [rep for rep in m2_reps if not rep_means.get(rep)]
    m2_rep = [{"repetition": rep, "mean_reduction_pct": statistics.mean(rep_means[rep]),
               "cases": len(rep_means[rep]),
               "pass": statistics.mean(rep_means[rep]) >= 10}
              for rep in m2_reps if rep_means.get(rep)]
    m2_value = statistics.mean(row["mean_reduction_pct"] for row in m2_rep) if m2_rep else None
    applicable_stable = (not m2_reps or
                         len({applicable_sets.get(rep, ()) for rep in m2_reps}) == 1)
    m2_status = ("NOT_REQUESTED" if not m2_reps and not m2_tcs and not m2 else
                  "INCOMPLETE" if m2_missing or not m2_reps or not m2_tcs or
                  not m2_official_set else
                  "FAIL" if empty_applicable or not applicable_stable else
                  "PASS" if m2_rep and all(row["pass"] for row in m2_rep) else "FAIL")
    if m2_value is not None:
        matrix.append({"metric": "Metric2", "level": "aggregate", "identity": "applicable-case equal weight",
                       "tc": "ALL", "value": m2_value, "unit": "%", "status": m2_status, "detail": "no sample weighting"})

    # Metric 3 exact pairing, standard arm evidence tree, TC and frozen aggregate rows.
    m3 = [r for r in resolved if r["metric"] == 3]
    m3_req = requirement(data, "metric3", {"pairs": sorted({r.get("pair") for r in m3}),
                                             "testcases": sorted({r["tc"] for r in m3})})
    pairs_req, tcs_req = [str(x) for x in m3_req.get("pairs", [])], [int(x) for x in m3_req.get("testcases", [])]
    groups = defaultdict(dict)
    for row in m3:
        groups[(row["pair"], row["tc"], row["order"])][row["arm"]] = row
    pair_tc_orders = defaultdict(set)
    for pair, tc, order in groups:
        pair_tc_orders[pair, tc].add(order)
    conflicting_orders = {key: sorted(orders) for key, orders in pair_tc_orders.items()
                          if len(orders) > 1}
    for (pair, tc), orders in sorted(conflicting_orders.items()):
        issues.append({"severity": "ERROR", "code": "M3_ORDER_CONFLICT",
                       "run_id": f"{pair}/TC{tc}",
                       "message": f"paired arms declare multiple orders: {orders}"})
    samples, incomplete_pairs = [], []
    evidence_root = output_dir / "evidence" / "metric3" if output_dir is not None else None
    for pair in pairs_req:
        for tc in tcs_req:
            if (pair, tc) in conflicting_orders:
                continue
            candidates = [(key, arms) for key, arms in groups.items() if key[0] == pair and key[1] == tc]
            if len(candidates) != 1 or set(candidates[0][1]) != {"ourcc", "ha-vi"}:
                incomplete_pairs.append({"pair": pair, "tc": tc, "candidates": len(candidates),
                                         "present_arms": sorted(candidates[0][1]) if len(candidates) == 1 else []})
                continue
            (key, arms) = candidates[0]
            order = key[2]
            if evidence_root is not None:
                for arm, source in arms.items():
                    arm_dir = evidence_root / f"pair-{pair}" / f"TC{tc}" / arm
                    arm_dir.mkdir(parents=True, exist_ok=True)
                    result = {"pair": pair, "pair_id": f"pair-{pair}-tc{tc}", "tc": tc, "order": order,
                              "arm": arm, "status": "PASS", "return_code": 0, "metrics": source["metrics"],
                              "raw_run_id": source["id"]}
                    (arm_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
            for name in M3[tc]:
                left, right = arms["ourcc"]["metrics"][name], arms["ha-vi"]["metrics"][name]
                if left["counter_frequency_hz"] != right["counter_frequency_hz"]:
                    issues.append({"severity": "ERROR", "code": "M3_FREQUENCY_MISMATCH", "run_id": f"{pair}/TC{tc}", "message": name})
                    continue
                delta = right["ticks_per_operation"] - left["ticks_per_operation"]
                samples.append({"pair": pair, "tc": tc, "order": order, "metric": name,
                                "ourcc_ticks": left["ticks_per_operation"], "ha_vi_ticks": right["ticks_per_operation"],
                                "delta_ticks": delta, "frequency_hz": left["counter_frequency_hz"]})
                matrix.append({"metric": "Metric3", "level": "pair", "identity": pair, "tc": f"TC{tc}",
                               "value": delta, "unit": f"ticks/op:{name}", "status": "OURCC_FASTER" if delta > 0 else "FAIL",
                               "detail": f"order={order}; delta=HA-VI-OurCC"})
    summaries = []
    for (tc, name), rows in sorted(defaultdict(list, {key: [r for r in samples if (r["tc"], r["metric"]) == key]
                                                        for key in {(r["tc"], r["metric"]) for r in samples}}).items()):
        summaries.append({"tc": tc, "metric": name, "pairs": len(rows),
                          "ourcc_mean_ticks": statistics.mean(r["ourcc_ticks"] for r in rows),
                          "ha_vi_mean_ticks": statistics.mean(r["ha_vi_ticks"] for r in rows),
                          "delta_mean_ticks": statistics.mean(r["delta_ticks"] for r in rows)})
    by_summary = {(r["tc"], r["metric"]): r for r in summaries}
    primary = []
    for tc in tcs_req:
        definition = M3_PRIMARY.get(tc, {})
        if definition and all((tc, name) in by_summary for name in definition):
            ourcc = sum(weight * by_summary[tc, name]["ourcc_mean_ticks"] for name, weight in definition.items())
            havi = sum(weight * by_summary[tc, name]["ha_vi_mean_ticks"] for name, weight in definition.items())
            primary.append({"tc": tc, "ourcc_mean_ticks": ourcc, "ha_vi_mean_ticks": havi, "delta_mean_ticks": havi-ourcc})
            matrix.append({"metric": "Metric3", "level": "TC", "identity": "paired mean", "tc": f"TC{tc}",
                           "value": havi-ourcc, "unit": "ticks/op", "status": "OURCC_FASTER" if havi > ourcc else "FAIL",
                           "detail": "frozen primary-value formula"})
    aggregates = []
    for name, weights in M3_AGGREGATES.items():
        if all(key in by_summary for key in weights):
            delta = sum(weight * by_summary[key]["delta_mean_ticks"] for key, weight in weights.items())
            aggregates.append({"name": name, "delta_ticks": delta,
                               "status": "PASS (EXECUTABLE-REFERENCE-MODEL SCOPE)" if delta > 0 else "FAIL (EXECUTABLE-REFERENCE-MODEL SCOPE)"})
            matrix.append({"metric": "Metric3", "level": "aggregate", "identity": name, "tc": "ALL",
                           "value": delta, "unit": "ticks/op", "status": aggregates[-1]["status"],
                           "detail": "frozen weights; strict GT 0"})
    m3_requested = bool(pairs_req or tcs_req or m3)
    official_m3_tcs = set(M3)
    m3_complete = (bool(pairs_req and tcs_req) and not incomplete_pairs and
                   not conflicting_orders and set(tcs_req) == official_m3_tcs)
    m3_status = ("NOT_REQUESTED" if not m3_requested else "INCOMPLETE" if not m3_complete else
                 "PASS (EXECUTABLE-REFERENCE-MODEL SCOPE)" if len(aggregates) == 2 and all(a["delta_ticks"] > 0 for a in aggregates)
                 else "FAIL (EXECUTABLE-REFERENCE-MODEL SCOPE)")

    has_errors = any(i["severity"] == "ERROR" and
                     i.get("contract_class", "standard") == "standard"
                     for i in issues)
    incomplete = m1_status == "INCOMPLETE" or m2_status == "INCOMPLETE" or m3_status == "INCOMPLETE"
    failed = m1_status == "FAIL" or m2_status == "FAIL" or m3_status.startswith("FAIL")
    overall, code = (("INVALID", 2) if has_errors else ("INCOMPLETE", 3) if incomplete else ("FAIL", 1) if failed else ("PASS", 0))
    report = {"schema_version": 1, "manifest": str(manifest) if manifest is not None else None,
              "correctness_policy": data.get("correctness_policy", "strict"),
              "overall_status": overall, "exit_code": code,
              "metric1": {"status": m1_status, "missing_slots": m1_missing, "comparisons": m1_comp},
              "metric2": {"status": m2_status, "missing_slots": m2_missing, "cases": m2_cases,
                           "repetition_equal_weight": m2_rep,
                           "applicable_cases_by_repetition": applicable_sets,
                           "applicable_set_stable": applicable_stable,
                           "official_testcase_set_complete": m2_official_set,
                           "repetitions_without_applicable_cases": empty_applicable,
                           "aggregate_reduction_pct": m2_value},
              "metric3": {"status": m3_status, "executable_reference_model": True,
                          "pairing_policy": "pair/tc/order identity-only; never Cartesian",
                          "incomplete_pairs": incomplete_pairs, "samples": samples, "metric_summaries": summaries,
                          "primary_values": primary, "aggregates": aggregates,
                          "inference": {"t_test": None, "pvalue": None}},
               "views": {"standard": {"runs": len(standard_resolved), "formal": True},
                         "all": descriptive_view(all_resolved),
                         "extension": descriptive_view(extension_resolved)},
               "issues": issues,
               "ingestion": ingestion or {"attempted": len(resolved), "added": len(resolved),
                                            "rejected": 0, "duplicate_conflicted": len(bad_ids),
                                             "add_results": []}}
    report["views"]["standard"].update({
        "matrix": matrix,
        "metric1": report["metric1"],
        "metric2": report["metric2"],
        "metric3": report["metric3"],
    })
    # Standard TSV/report compatibility is preserved, but per-run diagnostics
    # expose every successfully parsed, non-conflicted run.
    per_run = []
    for run in all_resolved:
        value = run["metrics"].get("mean_ns_per_operation", run["metrics"].get("mean_ns"))
        per_run.append({"run_id": run["id"], "metric": run["metric"], "tc": run["tc"],
                        "repetition": run["repetition"], "profile": run.get("profile", ""),
                        "arm": run.get("arm", ""), "pair": run.get("pair", ""),
                        "order": run.get("order", ""),
                        "value": value if value is not None else "MULTI",
                        "unit": "ns/op" if value is not None else "ticks/op",
                        "status": run["status"]})
    return report, all_resolved, matrix, per_run, issues, code


class Metric123RawLogMatrix:
    """Incrementally ingest raw runs, then aggregate exclusively from memory.

    ``add`` synchronously reads and closes every input used by a run.  Therefore
    the input trees may be removed as soon as it returns.  ``finalize`` never
    reads or stats them; an ``output_dir`` only controls generated report files.
    Adding after finalization is supported and subsequent finalizations include
    the new attempt.

    If ``requirements`` is omitted, expected repetitions/testcases/pairs are
    inferred from every add attempt whose identity fields can be normalized,
    including attempts later rejected because their evidence is invalid.
    """

    def __init__(self, requirements=None, correctness_policy="strict", base_dir=None):
        if correctness_policy not in ("strict", "required", "optional"):
            raise ExtractError("correctness_policy must be strict|required|optional")
        if requirements is not None and not isinstance(requirements, dict):
            raise ExtractError("requirements must be a mapping or None")
        self.correctness_policy = correctness_policy
        self.base_dir = pathlib.Path(base_dir or ".").expanduser().resolve()
        self._explicit_requirements = requirements is not None
        self._requirements = json.loads(json.dumps(requirements or {}))
        self._inferred = {"metric1": {"repetitions": set()},
                          "metric2": {"repetitions": set(), "testcases": set()},
                          "metric3": {"pairs": set(), "testcases": set()}}
        self._resolved = []
        self._issues = []
        self._ids = set()
        self._add_results = []
        self._slot_ids = defaultdict(list)

    @staticmethod
    def _identity(raw):
        """Best-effort normalized identity for coverage and status reporting."""
        if not isinstance(raw, dict):
            return None, None
        run_id = str(raw["id"]) if "id" in raw else None
        try:
            metric, tc = int(raw["metric"]), int(raw["tc"])
            repetition = str(raw["repetition"])
            if metric in (1, 2):
                profile = norm_profile(raw.get("profile"))
                return run_id, (metric, repetition, tc, profile)
            if metric == 3:
                pair, order, arm = str(raw["pair"]), raw["order"], raw["arm"]
                if order not in ("AB", "BA") or arm not in ("ourcc", "ha-vi"):
                    return run_id, None
                return run_id, (3, pair, tc, order, arm)
        except (KeyError, ExtractError, TypeError, ValueError):
            pass
        return run_id, None

    def _infer(self, slot):
        if self._explicit_requirements or slot is None:
            return
        if slot[0] == 1:
            self._inferred["metric1"]["repetitions"].add(slot[1])
        elif slot[0] == 2:
            self._inferred["metric2"]["repetitions"].add(slot[1])
            self._inferred["metric2"]["testcases"].add(slot[2])
        else:
            self._inferred["metric3"]["pairs"].add(slot[1])
            self._inferred["metric3"]["testcases"].add(slot[2])

    @staticmethod
    def _official_requirement_candidate(raw, slot):
        """Do not let an explicitly nonstandard extension expand formal coverage."""
        if slot is None:
            return False
        topology = str(raw.get("topology", "")).lower()
        if slot[0] == 1:
            return (slot[2] == 131 and topology == "8n1s" and
                    str(raw.get("phase", "post_pressure_catalog_reuse")) == "post_pressure_catalog_reuse" and
                    tuple(map(int, raw.get("timer_nodes", [1, 2]))) == (1, 2) and
                    int(raw.get("home_node", 0)) == 0 and int(raw.get("home_socket", 0)) == 0)
        if slot[0] == 2:
            if slot[2] not in M2:
                return False
            phase, official_topology, node, samples = M2[slot[2]]
            return (topology == official_topology and str(raw.get("phase", phase)) == phase and
                    int(raw.get("expected_node", node)) == node and
                    int(raw.get("expected_samples", samples)) == samples)
        return slot[2] in M3 and topology == "2n1s"

    def add(self, run=None, **fields):
        """Parse one run immediately and return an ADDED/REJECTED status dict."""
        if run is not None and fields:
            raise TypeError("add accepts either one run dict or keyword fields, not both")
        if run is None:
            raw = dict(fields)
        elif isinstance(run, dict):
            raw = dict(run)
        else:
            raise TypeError("add positional argument must be a run dict")
        requested_id = str(raw["id"]) if raw.get("id") not in (None, "") else None
        if requested_id is None:
            candidate = f"run-{len(self._add_results) + 1:06d}"
        else:
            candidate = requested_id
        if candidate in self._ids:
            suffix = 2
            while f"{candidate}-{suffix}" in self._ids:
                suffix += 1
            effective_id = f"{candidate}-{suffix}"
            rename_warning = warning(
                "DUPLICATE_RUN_ID_RENAMED", effective_id,
                f"requested run id {candidate!r} already exists; renamed to {effective_id!r}")
        else:
            effective_id, rename_warning = candidate, None
        raw["id"] = effective_id
        run_id, slot = self._identity(raw)
        official_candidate = self._official_requirement_candidate(raw, slot)
        if official_candidate:
            self._infer(slot)
        fallback_id = run_id
        result = {"status": "REJECTED", "requested_id": requested_id,
                  "run_id": fallback_id, "slot": list(slot) if slot else None}
        self._ids.add(run_id)
        if rename_warning:
            raw.setdefault("_contract_warnings", []).append(rename_warning)
        try:
            parsed = extract_run(raw, self.base_dir, self.correctness_policy)
        except (ExtractError, OSError, ValueError, TypeError) as error:
            code = "EVIDENCE_INVALID" if slot is not None else "RUN_SCHEMA_INVALID"
            issue = {"severity": "ERROR", "code": code, "run_id": fallback_id,
                     "contract_class": "standard" if official_candidate else "extension",
                     "message": str(error)}
            result["issue"] = issue
            self._issues.append(issue)
            self._add_results.append(result)
            return json.loads(json.dumps(result))
        parsed_slot = logical_slot(parsed)
        self._issues.extend(copy.deepcopy(parsed.get("contract_warnings", [])))
        self._resolved.append(parsed)
        prior_ids = list(self._slot_ids[parsed_slot])
        self._slot_ids[parsed_slot].append(parsed["id"])
        if prior_ids:
            result = {
                "status": "REJECTED",
                "run_id": parsed["id"],
                "slot": list(parsed_slot),
                "issue": {
                    "severity": "ERROR",
                    "code": "DUPLICATE_SLOT",
                    "run_id": ",".join(prior_ids + [parsed["id"]]),
                    "message": f"logical slot already claimed: {parsed_slot}",
                },
            }
        else:
            result = {"status": "ADDED", "requested_id": requested_id,
                      "run_id": parsed["id"],
                      "slot": list(parsed_slot)}
            if rename_warning:
                result["warning"] = rename_warning
        self._add_results.append(result)
        return json.loads(json.dumps(result))

    @staticmethod
    def _union_requirements(left, right):
        result = {}
        for metric in sorted(set(left) | set(right)):
            result[metric] = {}
            lfields, rfields = left.get(metric, {}), right.get(metric, {})
            for field in sorted(set(lfields) | set(rfields)):
                lv, rv = lfields.get(field, []), rfields.get(field, [])
                if isinstance(lv, list) and isinstance(rv, list):
                    result[metric][field] = sorted(set(lv) | set(rv), key=str)
                else:
                    result[metric][field] = copy.deepcopy(rv if field in rfields else lv)
        return result

    def __add__(self, other):
        if not isinstance(other, Metric123RawLogMatrix):
            return NotImplemented
        rank = {"optional": 0, "required": 1, "strict": 2}
        requirements = self._union_requirements(
            self._data().get("requirements", {}), other._data().get("requirements", {}))
        merged = Metric123RawLogMatrix(
            requirements=requirements,
            correctness_policy=max((self.correctness_policy, other.correctness_policy),
                                   key=lambda x: rank[x]), base_dir=".")
        seen_snapshots = set()
        for source in (self, other):
            for record in source._resolved:
                fingerprint = json.dumps(record, sort_keys=True)
                if fingerprint in seen_snapshots:
                    continue
                seen_snapshots.add(fingerprint)
                row = copy.deepcopy(record)
                requested = row["id"]
                effective = requested
                if effective in merged._ids:
                    suffix = 2
                    while f"{requested}-{suffix}" in merged._ids:
                        suffix += 1
                    effective = f"{requested}-{suffix}"
                    item = warning("DUPLICATE_RUN_ID_RENAMED", effective,
                                   f"merged run id {requested!r} renamed to {effective!r}")
                    row["id"] = effective
                    row.setdefault("contract_warnings", []).append(item)
                    merged._issues.append(item)
                merged._ids.add(effective)
                slot = logical_slot(row)
                merged._infer(slot)
                merged._resolved.append(row)
                prior = list(merged._slot_ids[slot])
                merged._slot_ids[slot].append(effective)
                result = {"status": "REJECTED" if prior else "ADDED",
                          "requested_id": requested, "run_id": effective,
                          "slot": list(slot)}
                if prior:
                    result["issue"] = {"severity": "ERROR", "code": "DUPLICATE_SLOT",
                                       "run_id": ",".join(prior + [effective]),
                                       "message": f"logical slot already claimed: {slot}"}
                merged._add_results.append(result)
        # Preserve rejected attempts and non-run warnings/errors without rereading inputs.
        for source in (self, other):
            for issue in source._issues:
                if issue not in merged._issues:
                    merged._issues.append(copy.deepcopy(issue))
            for result in source._add_results:
                if result.get("status") == "REJECTED" and result.get("issue", {}).get("code") != "DUPLICATE_SLOT":
                    merged._add_results.append(copy.deepcopy(result))
        return merged

    def _data(self):
        if self._explicit_requirements:
            requirements = json.loads(json.dumps(self._requirements))
        else:
            requirements = {
                name: {key: sorted(values) for key, values in fields.items()}
                for name, fields in self._inferred.items()
            }
        return {"schema_version": 1, "correctness_policy": self.correctness_policy,
                "requirements": requirements}

    def finalize(self, output_dir=None):
        """Return report plus matrices; optionally emit the normal output tree."""
        output = pathlib.Path(output_dir).expanduser().resolve() if output_dir is not None else None
        slot_counts = defaultdict(int)
        for row in self._resolved:
            slot_counts[logical_slot(row)] += 1
        duplicate_conflicted = sum(count for count in slot_counts.values() if count > 1)
        ingestion = {"attempted": len(self._add_results),
                     "added": sum(x["status"] == "ADDED" for x in self._add_results),
                     "rejected": sum(x["status"] == "REJECTED" for x in self._add_results),
                     "duplicate_conflicted": duplicate_conflicted,
                     "add_results": json.loads(json.dumps(self._add_results))}
        # Isolate the retained ingestion state from callers mutating finalize's result.
        parsed_snapshot = json.loads(json.dumps(self._resolved))
        issue_snapshot = json.loads(json.dumps(self._issues))
        values = aggregate_results(self._data(), parsed_snapshot, issue_snapshot,
                                   output, ingestion=ingestion)
        report, resolved, matrix, per_run, issues, code = values
        if output is not None:
            write_outputs(output, report, resolved, matrix, per_run, issues)
        return {"report": report, "resolved_runs": resolved, "matrix": matrix,
                "matrices": {"standard": matrix,
                             "all": report["views"]["all"]["matrix"],
                             "extension": report["views"]["extension"]["matrix"]},
                "per_run_metrics": per_run, "issues": issues, "exit_code": code}


def analyze(manifest_path, output_dir):
    """Manifest-compatible wrapper implemented through Metric123RawLogMatrix."""
    manifest_path = pathlib.Path(manifest_path)
    data = json.loads(manifest_path.read_text())
    if not isinstance(data, dict) or data.get("schema_version") not in (1, "1") or not isinstance(data.get("runs"), list):
        raise ExtractError("manifest requires schema_version=1 and runs array")
    matrix = Metric123RawLogMatrix(data.get("requirements"),
                                   data.get("correctness_policy", "strict"),
                                   manifest_path.parent)
    for raw in data["runs"]:
        matrix.add(raw)
    output = pathlib.Path(output_dir) if output_dir is not None else None
    slot_counts = defaultdict(int)
    for row in matrix._resolved:
        slot_counts[logical_slot(row)] += 1
    result = aggregate_results(matrix._data(), list(matrix._resolved), list(matrix._issues),
                               output, manifest=manifest_path,
                               ingestion={"attempted": len(matrix._add_results),
                                          "added": sum(x["status"] == "ADDED" for x in matrix._add_results),
                                          "rejected": sum(x["status"] == "REJECTED" for x in matrix._add_results),
                                          "duplicate_conflicted": sum(
                                              count for count in slot_counts.values() if count > 1),
                                          "add_results": matrix._add_results})
    # Preserve analyze's historical tuple and its division of writing responsibility.
    return result


def write_tsv(path, rows, fields):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def write_outputs(output_dir, report, resolved, matrix, per_run, issues):
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    (output_dir / "resolved_runs.json").write_text(json.dumps(resolved, indent=2, sort_keys=True) + "\n")
    matrix_fields = ["metric", "level", "identity", "tc", "value", "unit", "status", "detail"]
    write_tsv(output_dir / "metric_matrix.tsv", matrix, matrix_fields)
    write_tsv(output_dir / "metric_matrix_standard.tsv", matrix, matrix_fields)
    write_tsv(output_dir / "metric_matrix_all.tsv",
              report["views"]["all"]["matrix"], matrix_fields)
    write_tsv(output_dir / "metric_matrix_extension.tsv",
              report["views"]["extension"]["matrix"], matrix_fields)
    write_tsv(output_dir / "per-run_metrics.tsv", per_run,
              ["run_id", "metric", "tc", "repetition", "profile", "arm", "pair", "order", "value", "unit", "status"])
    write_tsv(output_dir / "issues.tsv", issues,
              ["severity", "code", "run_id", "contract_class", "message"])
    lines = ["# Metric 1/2/3 原始日志统一报告", "", f"总体状态：**{report['overall_status']}**", "",
             "| 指标 | 状态 |", "|---|---|", f"| Metric1 | {report['metric1']['status']} |",
             f"| Metric2 | {report['metric2']['status']} |", f"| Metric3 | {report['metric3']['status']} |", "",
             "Metric3 仅表示冻结可执行参考模型范围；delta = HA-VI - OurCC，严格大于 0 才通过。",
             "不执行 t-test，不生成 p-value，不做笛卡尔配对。", "",
             "## 视图", "",
             f"- Standard runs: {report['views']['standard']['runs']}",
             f"- All parsed runs: {report['views']['all']['runs']}",
             f"- Extension runs: {report['views']['extension']['runs']}",
             "", "## 标准矩阵", "",
             "| Metric | Level | Identity | TC | Value | Unit | Status |", "|---|---|---|---|---:|---|---|"]
    for row in matrix:
        value = "N/A" if row["value"] is None else f"{row['value']:.9g}" if isinstance(row["value"], (int, float)) else str(row["value"])
        lines.append(f"| {row['metric']} | {row['level']} | {row['identity']} | {row['tc']} | {value} | {row['unit']} | {row['status']} |")
    if issues:
        lines += ["", "## 问题"] + [f"- `{x['code']}` {x['run_id']}: {x['message']}" for x in issues]
    (output_dir / "report.md").write_text("\n".join(lines) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=pathlib.Path)
    parser.add_argument("--output-dir", default=pathlib.Path("output"), type=pathlib.Path)
    args = parser.parse_args(argv)
    manifest, output = args.manifest.expanduser().resolve(), args.output_dir.expanduser().resolve()
    try:
        report, resolved, matrix, per_run, issues, code = analyze(manifest, output)
    except (ExtractError, OSError, ValueError) as error:
        report = {"schema_version": 1, "overall_status": "INVALID", "exit_code": 2,
                  "metric1": {"status": "INVALID"}, "metric2": {"status": "INVALID"},
                  "metric3": {"status": "INVALID", "executable_reference_model": True},
                  "issues": [{"severity": "ERROR", "code": "MANIFEST_INVALID", "run_id": "", "message": str(error)}]}
        resolved, matrix, per_run, issues, code = [], [], [], report["issues"], 2
    write_outputs(output, report, resolved, matrix, per_run, issues)
    print(report["overall_status"])
    return code


if __name__ == "__main__":
    sys.exit(main())
