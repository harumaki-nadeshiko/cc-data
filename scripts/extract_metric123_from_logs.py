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
import math
import numbers
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
ARM_ALIASES = {
    "ourcc": "ourcc", "ubcc": "ourcc", "ubcc-lossless": "ourcc",
    "lossless-oneway": "ourcc", "ha-vi": "ha-vi", "havi": "ha-vi",
    "ha_vi": "ha-vi",
}
METRIC1_ROLE_ALIASES = {
    "naive": "naive", "baseline": "naive",
    "spill": "spill", "spill-512k": "spill", "actual": "spill",
    "ideal": "ideal", "ideal-dir": "ideal", "infinite": "ideal",
}
OUTER_RE = re.compile(r"\[EP-PERF\]\s+kind=outer(?:\s|$).*?latency_ps=(\d+)")
FILL_DONE_RE = re.compile(r"\[RESIDENT-FILL-DONE\].*?\bfound=(\d+)")


class ExtractError(Exception):
    pass


def finite_number(value):
    """Return value when it is a finite arithmetic scalar, otherwise None."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return None
    return value if math.isfinite(value) else None


def safe_subtract(left, right):
    left, right = finite_number(left), finite_number(right)
    return None if left is None or right is None else left - right


def safe_divide(numerator, denominator):
    numerator, denominator = finite_number(numerator), finite_number(denominator)
    return (None if numerator is None or denominator in (None, 0) else
            numerator / denominator)


def safe_mean(values):
    values = list(values)
    return (statistics.mean(values) if values and
            all(finite_number(value) is not None for value in values) else None)


def safe_stdev(values):
    values = list(values)
    if not values or any(finite_number(value) is None for value in values):
        return None
    return statistics.stdev(values) if len(values) > 1 else 0.0


def safe_weighted_sum(weighted_values):
    weighted_values = list(weighted_values)
    if not weighted_values or any(finite_number(value) is None or
                                  finite_number(weight) is None
                                  for weight, value in weighted_values):
        return None
    return sum(weight * value for weight, value in weighted_values)


def json_ready(value):
    """Recursively map non-finite numeric leaves to JSON null."""
    if isinstance(value, dict):
        return {key: json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        return value if math.isfinite(value) else None
    return value


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


def norm_arm(value):
    normalized = ARM_ALIASES.get(str(value).lower())
    if normalized is None:
        raise ExtractError(f"unknown Metric3 arm {value!r}")
    return normalized


def norm_metric1_role(value):
    normalized = METRIC1_ROLE_ALIASES.get(str(value).lower())
    if normalized is None:
        raise ExtractError(f"unknown Metric1 role {value!r}")
    return normalized


def metric1_outer_latency(root):
    """Parse every completed Outer, preferring canonical gem5 stderr streams."""
    preferred = sorted({p.resolve() for pattern in ("gem5_tc*_node*/stderr.log",
                                                     "gem5_tc*_node*/stderr.log.gz")
                        for p in root.rglob(pattern) if p.is_file()}, key=str)
    candidates = preferred or [p.resolve() for p in all_log_files(root)
                               if p.suffix in (".log", ".gz")]
    first_source, rows = {}, []
    for path in sorted(candidates, key=str):
        with open_text(path) as stream:
            for line_no, line in enumerate(stream, 1):
                match = OUTER_RE.search(line)
                if not match:
                    continue
                exact_line = line.rstrip("\r\n")
                if exact_line in first_source and first_source[exact_line] != str(path):
                    continue
                first_source.setdefault(exact_line, str(path))
                rows.append({"file": str(path), "line": line_no,
                             "latency_ps": int(match.group(1))})
    values = sorted(row["latency_ps"] for row in rows)
    if not values:
        return {"source_files": [], "sources": [], "samples": 0, "mean_ns": None,
                "p50_ns": None, "p95_ns": None, "p99_ns": None, "max_ns": None}
    percentile = lambda q: values[int(q * (len(values) - 1))] / 1000.0
    return {"source_files": sorted({row["file"] for row in rows}), "sources": rows,
            "samples": len(values), "mean_ns": statistics.mean(values) / 1000.0,
            "p50_ns": statistics.median(values) / 1000.0,
            "p95_ns": percentile(0.95), "p99_ns": percentile(0.99),
            "max_ns": values[-1] / 1000.0}


def detect_metric3_arm(root):
    """Return a consistent arm identity plus every simulator-log evidence row."""
    evidence = []
    for path in all_log_files(root):
        with open_text(path) as stream:
            for line_no, line in enumerate(stream, 1):
                profile = re.search(r"\[EPBACKEND-PROFILE\].*?ha_endpoint_profile=(\S+)", line)
                if profile and profile.group(1).lower() in ("ubcc", "ha-vi"):
                    arm = "ourcc" if profile.group(1).lower() == "ubcc" else "ha-vi"
                    evidence.append({"arm": arm, "kind": "EPBACKEND-PROFILE",
                                     "file": str(path), "line": line_no,
                                     "value": profile.group(1)})
                if PROCESS_MANIFEST in line:
                    try:
                        payload = json.loads(line.split(PROCESS_MANIFEST, 1)[1].strip())
                    except (TypeError, ValueError):
                        payload = {}
                    controller = str(payload.get("home_controller", "")).lower()
                    if payload.get("component") == "ubio" and controller in ("ubcc", "ha-vi"):
                        evidence.append({"arm": "ourcc" if controller == "ubcc" else "ha-vi",
                                         "kind": "PROCESS-MANIFEST", "file": str(path),
                                         "line": line_no, "value": controller})
                manifest = re.search(r"\[UBIO-HA-MANIFEST\].*?controller=(\S+)", line)
                if manifest and manifest.group(1).lower() == "ha-vi":
                    evidence.append({"arm": "ha-vi", "kind": "UBIO-HA-MANIFEST",
                                     "file": str(path), "line": line_no,
                                     "value": manifest.group(1)})
    identities = sorted({row["arm"] for row in evidence})
    if not identities:
        raise ExtractError("ARM_IDENTITY_MISSING: no EPBACKEND-PROFILE, UBIO PROCESS-MANIFEST, or UBIO-HA-MANIFEST arm identity found")
    if len(identities) != 1:
        details = ", ".join(f"{row['arm']}@{row['file']}:{row['line']}({row['kind']})"
                            for row in evidence)
        raise ExtractError(f"ARM_IDENTITY_CONFLICT: simulator logs identify multiple arms {identities}: {details}")
    return identities[0], evidence


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


def validate_optional_process_tc(root, expected_tc):
    """Reject conflicting nonzero process hints; missing/zero hints are valid."""
    conflicts = []
    for path in all_log_files(root):
        for row in process_manifests(path):
            if row.get("component") not in ("ubio", "gem5-config", "gem5"):
                continue
            raw_tc = row.get("tc")
            if raw_tc in (None, "", 0, "0"):
                continue
            try:
                actual = int(raw_tc)
            except (TypeError, ValueError):
                conflicts.append(f"{path}: invalid tc={raw_tc!r}")
                continue
            if actual != expected_tc:
                conflicts.append(f"{path}: tc={actual}")
    if conflicts:
        raise ExtractError(
            f"process testcase hint conflicts with manifest tc={expected_tc}: " +
            ", ".join(conflicts))


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
                    (row_tc in (None, "", 0, "0") or int(row_tc) == tc)):
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


def marker_rows(paths, kind, phase=None):
    regex = TIMER_RE if kind == "timer" else LAT_RE
    rows = []
    for path in paths:
        with open_text(path) as stream:
            for line_no, line in enumerate(stream, 1):
                match = regex.search(line)
                if not match or (phase is not None and match.group(2) != phase):
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
                             "phase": match.group(2),
                             "count": count, "ticks": ticks, "frequency_hz": freq})
    return rows


def aggregate_latency_phase(rows, expected_node=None, expected_samples=None):
    if not rows:
        raise ExtractError("Metric2 latency phase has no records")
    frequencies = {row["frequency_hz"] for row in rows}
    if len(frequencies) != 1:
        raise ExtractError("Metric2 latency counter frequency mismatch")
    if expected_node is not None and any(row["node"] != expected_node for row in rows):
        raise ExtractError(f"Metric2 marker contract requires every record node={expected_node}, got nodes={sorted({r['node'] for r in rows})}")
    total = sum(row["count"] for row in rows)
    if expected_samples is not None and total != expected_samples:
        raise ExtractError(f"Metric2 marker contract requires total samples={expected_samples}, got {total}")
    frequency = next(iter(frequencies))
    mean_ticks = sum(row["ticks"] * row["count"] for row in rows) / total
    nodes = sorted({row["node"] for row in rows})
    return {"phase": rows[0]["phase"], "node": nodes[0] if len(nodes) == 1 else None,
            "nodes": nodes, "samples": total, "records": len(rows),
            "mean_ticks": mean_ticks, "frequency_hz": frequency,
            "mean_ns": mean_ticks * 1e9 / frequency, "sources": rows,
            "source": rows[0] if len(rows) == 1 else None}


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
    capacity, exact, exact_known = 0, None, None
    policies, fallback_policies = set(), set()
    oversized_values, found_fills = set(), 0
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
                        if payload.get("experimental_oversized_resident_dir") is not None:
                            oversized_values.add(int(payload["experimental_oversized_resident_dir"]))
                        resident = payload.get("resident_dir")
                        if isinstance(resident, dict) and resident.get("capacity") is not None:
                            capacity = max(capacity, int(resident["capacity"]))
                fill_match = FILL_DONE_RE.search(line)
                if fill_match and int(fill_match.group(1)) == 1:
                    found_fills += 1
                if "[UBCC-STATS]" not in line or "{" not in line:
                    continue
                try:
                    payload = json.loads(line[line.index("{"):])
                except ValueError:
                    continue
                if payload.get("residentCapacity") is not None:
                    capacity = max(capacity, int(payload["residentCapacity"]))
                if payload.get("h64ExactLiveKnown") is not None:
                    known = int(payload.get("h64ExactLiveKnown", 0))
                    count = int(payload.get("h64ExactLiveCount", 0))
                    exact_known = max(exact_known or 0, known)
                    exact = max(exact or 0, count)
    if not policies:
        policies = fallback_policies
    if not capacity or len(policies) != 1:
        raise ExtractError(f"UBCC capacity/policy invalid: capacity={capacity} policies={sorted(policies)}")
    policy = next(iter(policies))
    if len(oversized_values) > 1:
        raise ExtractError(f"conflicting experimental_oversized_resident_dir values: {sorted(oversized_values)}")
    return {"policy": policy, "resident_capacity": capacity,
            "h64_exact_live_known": exact_known, "h64_exact_live": exact,
            "effective_unique": (capacity if policy == "naive" or exact is None
                                 else max(capacity, exact)),
            "experimental_oversized_resident_dir": (next(iter(oversized_values))
                                                       if oversized_values else None),
            "backstore_found_fills": found_fills, "sources": sources}


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
    validate_optional_process_tc(simulator, out["tc"])
    try:
        discovered_simouts = discover_simouts(simout, out["tc"])
    except ExtractError as error:
        if out["metric"] != 1 or "no supported simout files" not in str(error):
            raise
        discovered_simouts = {}
    out["simout_by_node"] = {str(k): str(v) for k, v in discovered_simouts.items()}
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
        timer_complete = (set(by_node) == set(expected_nodes) and
                          all(len(by_node[n]) == 1 for n in expected_nodes))
        if timer_complete and len({row["frequency_hz"] for row in rows}) != 1:
            raise ExtractError("Metric1 timer frequency mismatch")
        timer = ([{**row, "ticks_per_operation": row["ticks"] / row["count"],
                   "ns_per_operation": row["ticks"] * 1e9 / row["frequency_hz"] / row["count"]}
                  for row in rows] if timer_complete else [])
        if not timer_complete:
            out["contract_warnings"].append(warning(
                "METRIC1_GUEST_TIMER_MISSING", out["id"],
                f"descriptive guest timer absent/partial expected={expected_nodes} "
                f"counts={dict((n, len(v)) for n, v in by_node.items())}"))
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
        capacity = parse_capacity(capacity_logs)
        if run.get("metric1_role") is not None:
            out["metric1_role"] = norm_metric1_role(run["metric1_role"])
            out["role_source"] = "explicit"
        else:
            if out["profile"] == "naive":
                out["metric1_role"] = "naive"
            elif capacity["experimental_oversized_resident_dir"] == 1:
                out["metric1_role"] = "ideal"
            elif out["profile"] == "spill-noopt":
                out["metric1_role"] = "spill"
            else:
                out["metric1_role"] = "support"
            out["role_source"] = "auto"
            out["contract_warnings"].append(warning(
                "METRIC1_ROLE_AUTO_DETECTED", out["id"],
                f"Metric1 role auto-detected as {out['metric1_role']}"))
        outer = metric1_outer_latency(simulator)
        guest_mean = (statistics.mean(x["ns_per_operation"] for x in timer)
                      if timer else None)
        out["metrics"] = {"capacity": capacity, "outer_latency": outer,
                          "timers": timer if timer_complete else None, "phase": phase,
                          "mean_ns_per_operation": guest_mean,
                          "guest_timer_complete": timer_complete}
        role = out["metric1_role"]
        common = (out["tc"] == 131 and out["topology"] == "8n1s" and
                  home_node == 0 and home_socket == 0)
        if role == "naive":
            qualified = (out["profile"] == "naive" and capacity["policy"] == "naive" and
                         capacity["experimental_oversized_resident_dir"] in (None, 0))
        elif role == "spill":
            qualified = (out["profile"] == "spill-noopt" and capacity["policy"] == "spill" and
                          capacity["experimental_oversized_resident_dir"] in (None, 0) and
                          capacity["h64_exact_live_known"] == 1 and
                          outer["samples"] >= 1)
        elif role == "ideal":
            ideal_min = int(run.get("ideal_min_capacity", 102656))
            out["ideal_min_capacity"] = ideal_min
            qualified = (out["profile"] == "spill-noopt" and capacity["policy"] == "spill" and
                          capacity["experimental_oversized_resident_dir"] == 1 and
                          capacity["resident_capacity"] >= ideal_min and
                          capacity["backstore_found_fills"] == 0 and
                          capacity["h64_exact_live"] == 0 and
                          capacity["h64_exact_live_known"] in (0, 1) and
                          outer["samples"] >= 1)
        else:
            qualified = False
        standard = common and qualified
        if not standard:
            out["contract_warnings"].append(warning(
                "NONSTANDARD_CONTRACT", out["id"],
                f"Metric1 descriptive extension role={role} tc={out['tc']} topology={out['topology']} "
                f"profile={out['profile']} home={home_node}/{home_socket} capacity={capacity}"))
    elif out["metric"] == 2:
        registered = out["tc"] in M2
        all_rows = marker_rows(paths, "latency")
        by_phase = defaultdict(list)
        for row in all_rows:
            by_phase[row["phase"]].append(row)
        if registered:
            default_phase, official_topology, default_node, default_samples = M2[out["tc"]]
            phase = str(run.get("phase", default_phase))
            node = int(run.get("expected_node", default_node))
            samples = int(run.get("expected_samples", default_samples))
            rows = by_phase.get(phase, [])
            official_contract = (phase == default_phase and node == default_node and
                                 samples == default_samples)
            if official_contract:
                if len(rows) != 1:
                    raise ExtractError(f"Metric2 expected exactly one phase={phase}, got {len(rows)}")
                if rows[0]["node"] != node or rows[0]["count"] != samples:
                    raise ExtractError(f"TC{out['tc']} marker contract requires node={node} samples={samples}, got node={rows[0]['node']} samples={rows[0]['count']}")
            selected = aggregate_latency_phase(rows, node, samples)
        else:
            official_topology = None
            node = int(run["expected_node"]) if "expected_node" in run else None
            samples = int(run["expected_samples"]) if "expected_samples" in run else None
            if "phase" in run:
                phase = str(run["phase"])
                selected = aggregate_latency_phase(by_phase.get(phase, []), node, samples)
            elif len(by_phase) == 1:
                phase = next(iter(by_phase))
                selected = aggregate_latency_phase(by_phase[phase], node, samples)
                out["contract_warnings"].append(warning(
                    "METRIC2_PHASE_AUTO_DETECTED", out["id"],
                    f"Metric2 phase auto-detected as {phase!r}"))
            elif len(by_phase) > 1:
                phase, selected = None, None
                out["contract_warnings"].append(warning(
                    "METRIC2_MULTIPLE_PHASES", out["id"],
                    f"Metric2 retained multiple independent phases: {sorted(by_phase)}"))
            else:
                raise ExtractError("Metric2 found no PERF-LATENCY records")
        latency_phases = {name: aggregate_latency_phase(
                              rows, node if not registered else None,
                              samples if not registered and len(by_phase) == 1 else None)
                          for name, rows in sorted(by_phase.items())}
        if registered:
            extras = sorted(set(by_phase) - {phase})
            if extras:
                out["contract_warnings"].append(warning(
                    "METRIC2_ADDITIONAL_PHASES", out["id"],
                    f"additional descriptive PERF-LATENCY phases retained: {extras}"))
        out["metrics"] = {"latency_phases": latency_phases}
        if selected is not None:
            out["metrics"].update(selected)
        standard = (registered and out["topology"] == official_topology and phase == default_phase and
                     node == default_node and samples == default_samples)
        if not standard:
            out["contract_warnings"].append(warning(
                "NONSTANDARD_CONTRACT", out["id"],
                f"Metric2 descriptive extension tc={out['tc']} topology={out['topology']} "
                f"phase={phase} node={node} samples={samples}"))
    else:
        if run.get("arm") is None:
            out["arm"], evidence = detect_metric3_arm(simulator)
            out["arm_source"], out["arm_evidence"] = "auto", evidence
            out["contract_warnings"].append(warning(
                "ARM_AUTO_DETECTED", out["id"],
                f"Metric3 arm auto-detected as {out['arm']} from {len(evidence)} simulator-log marker(s)"))
        else:
            out["arm"] = norm_arm(run["arm"])
            out["arm_source"] = "explicit"
            out["arm_evidence"] = [{"kind": "manifest", "value": str(run["arm"]),
                                     "arm": out["arm"]}]
        if "pair" in run:
            out["pair"] = str(run["pair"])
        if "order" in run:
            if run["order"] not in ("AB", "BA"):
                raise ExtractError("Metric3 order must be AB/BA")
            out["order"] = run["order"]
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
        return (1, run["repetition"], run["tc"], run["topology"],
                run.get("metric1_role"), run["profile"],
                run["metrics"].get("phase"),
                tuple(item["node"] for item in (run["metrics"].get("timers") or [])),
                int(run.get("home_node", 0)), int(run.get("home_socket", 0)))
    if run["metric"] == 2:
        return (2, run["repetition"], run["tc"], run["topology"], run["profile"],
                run["metrics"].get("phase"), run["metrics"].get("node"),
                run["metrics"].get("samples"))
    names = tuple(sorted(run["metrics"]))
    if run.get("comparison_mode") == "paired":
        return (3, "paired", run.get("pair"), run["tc"], run["topology"],
                run.get("order"), run["arm"], names)
    return (3, "independent", run["repetition"], run["tc"], run["topology"],
            run["arm"], names)


def requirement(manifest, name, default):
    value = manifest.get("requirements", {}).get(name, default)
    return value if isinstance(value, dict) else default


def descriptive_view(runs):
    """Small contract-neutral matrix and useful profile/arm comparisons."""
    matrix, groups = [], defaultdict(list)
    for run in runs:
        value = run["metrics"].get("mean_ns_per_operation", run["metrics"].get("mean_ns"))
        if value is None and run["metric"] == 3:
            value = safe_mean(x.get("ns_per_operation") for x in run["metrics"].values())
        value = finite_number(value)
        if run["metric"] == 2 and run["metrics"].get("latency_phases"):
            for phase, phase_data in run["metrics"]["latency_phases"].items():
                matrix.append({"metric": "Metric2", "level": "phase", "identity": run["id"],
                               "tc": f"TC{run['tc']}", "value": finite_number(phase_data.get("mean_ns")),
                               "unit": "ns/op", "status": "DESCRIPTIVE",
                               "detail": f"{run['contract_class']}; phase={phase}"})
        else:
            matrix.append({"metric": f"Metric{run['metric']}", "level": "run", "identity": run["id"],
                           "tc": f"TC{run['tc']}", "value": value, "unit": "ns/op",
                           "status": "DESCRIPTIVE", "detail": run["contract_class"]})
        dimension = (run.get("metric1_role") if run["metric"] == 1 else
                     run.get("profile", run.get("arm", "")))
        key = (run["metric"], run["tc"], run["topology"], run.get("repetition"),
               dimension)
        if finite_number(value) is not None:
            groups[key].append(value)
    summaries = []
    for key, values in sorted(groups.items(), key=lambda x: tuple(map(str, x[0]))):
        summaries.append({"metric": key[0], "tc": key[1], "topology": key[2],
                          "repetition": key[3], "profile_or_arm": key[4],
                          "runs": len(values), "mean_ns_per_operation": safe_mean(values)})

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
                                    "capacity_ratio_spill_to_naive": safe_divide(
                                        profiles["spill-noopt"]["metrics"]["capacity"].get("effective_unique"),
                                        naive["metrics"]["capacity"].get("effective_unique")),
                                    "optimized_delta_ns": safe_subtract(
                                        optimized["metrics"].get("mean_ns_per_operation"),
                                        naive["metrics"].get("mean_ns_per_operation"))})
            else:
                naive_mean = naive["metrics"].get("mean_ns")
                optimized_mean = optimized["metrics"].get("mean_ns")
                comparisons.append({"metric": 2, "tc": key[1], "topology": key[2],
                                    "repetition": key[3],
                                    "optimized_reduction_pct": safe_divide(
                                        safe_subtract(naive_mean, optimized_mean), naive_mean)})
                if comparisons[-1]["optimized_reduction_pct"] is not None:
                    comparisons[-1]["optimized_reduction_pct"] *= 100
    m3_pairs = []
    pair_groups = defaultdict(dict)
    for run in runs:
        if run["metric"] == 3 and run.get("pair") is not None and run.get("order") is not None:
            pair_groups[(run.get("pair"), run["tc"], run.get("order"),
                         run["topology"])][run["arm"]] = run
    for key, arms in sorted(pair_groups.items(), key=lambda x: tuple(map(str, x[0]))):
        if set(arms) != {"ourcc", "ha-vi"}:
            continue
        common = sorted(set(arms["ourcc"]["metrics"]) & set(arms["ha-vi"]["metrics"]))
        m3_pairs.append({"pair": key[0], "tc": key[1], "order": key[2],
                         "topology": key[3],
                         "metrics": {name: {
                             "ourcc_ticks_per_operation": finite_number(
                                 arms["ourcc"]["metrics"][name].get("ticks_per_operation")),
                             "ha_vi_ticks_per_operation": finite_number(
                                 arms["ha-vi"]["metrics"][name].get("ticks_per_operation")),
                             "delta_ticks": safe_subtract(
                                 arms["ha-vi"]["metrics"][name].get("ticks_per_operation"),
                                 arms["ourcc"]["metrics"][name].get("ticks_per_operation"))}
                             for name in common}})
    arm_groups = defaultdict(list)
    for run in runs:
        if run["metric"] == 3:
            for name, metric in run["metrics"].items():
                value = finite_number(metric.get("ticks_per_operation"))
                if value is not None:
                    arm_groups[(run["tc"], run["topology"], name, run["arm"])].append(value)
    arm_comparisons = []
    coordinates = sorted({key[:3] for key in arm_groups}, key=lambda x: tuple(map(str, x)))
    for tc, topology, name in coordinates:
        arms = {}
        for arm in ("ourcc", "ha-vi"):
            values = arm_groups.get((tc, topology, name, arm), [])
            if values:
                arms[arm] = {"count": len(values), "mean_ticks_per_operation": safe_mean(values),
                             "stdev_ticks_per_operation": safe_stdev(values)}
        arm_comparisons.append({"tc": tc, "topology": topology, "metric": name,
                                "arms": arms,
                                "delta_ticks": safe_subtract(
                                                arms["ha-vi"]["mean_ticks_per_operation"],
                                                arms["ourcc"]["mean_ticks_per_operation"])
                                if set(arms) == {"ourcc", "ha-vi"} else None})
    m1_role_comparisons = []
    role_groups = defaultdict(dict)
    for run in runs:
        if run["metric"] == 1:
            role_groups[(run["repetition"], run["tc"], run["topology"])][
                run.get("metric1_role", run["profile"])] = run
    for key, roles in sorted(role_groups.items(), key=lambda x: tuple(map(str, x[0]))):
        m1_role_comparisons.append({"repetition": key[0], "tc": key[1],
                                    "topology": key[2], "roles": sorted(roles),
                                    "capacity_ratio": safe_divide(
                                                       roles["spill"]["metrics"]["capacity"].get("effective_unique"),
                                                       roles["naive"]["metrics"]["capacity"].get("effective_unique"))
                                    if "naive" in roles and "spill" in roles else None,
                                    "outer_delta_ns": safe_subtract(
                                                       roles["spill"]["metrics"]["outer_latency"].get("mean_ns"),
                                                       roles["ideal"]["metrics"]["outer_latency"].get("mean_ns"))
                                    if "spill" in roles and "ideal" in roles else None})
    return {"runs": len(runs), "summaries": summaries,
            "comparisons": comparisons, "metric3_pairs": m3_pairs,
            "metric1_role_comparisons": m1_role_comparisons,
            "metric3_arm_comparisons": arm_comparisons, "matrix": matrix}


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
        value = finite_number(run["metrics"].get(
            "mean_ns_per_operation", run["metrics"].get("mean_ns")))
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
    m1_req = requirement(data, "metric1", {"repetitions": sorted({r["repetition"] for r in m1}),
                                             "roles": ["naive", "spill", "ideal"],
                                             "ideal_min_capacity": 102656})
    m1_reps = [str(x) for x in m1_req.get("repetitions", [])]
    m1_roles = [norm_metric1_role(x) for x in m1_req.get("roles", ["naive", "spill", "ideal"])]
    m1_by = {(r["repetition"], r["metric1_role"]): r for r in m1}
    m1_missing = [(rep, role) for rep in m1_reps for role in m1_roles
                  if (rep, role) not in m1_by]
    m1_comp = []
    for rep in m1_reps:
        if all((rep, role) in m1_by for role in ("naive", "spill", "ideal")):
            naive, spill, ideal = (m1_by[rep, role] for role in ("naive", "spill", "ideal"))
            ratio = safe_divide(spill["metrics"]["capacity"].get("effective_unique"),
                                naive["metrics"]["capacity"].get("effective_unique"))
            spill_outer = spill["metrics"]["outer_latency"]
            ideal_outer = ideal["metrics"]["outer_latency"]
            delta_ns = safe_subtract(spill_outer.get("mean_ns"), ideal_outer.get("mean_ns"))
            if ratio is None or delta_ns is None:
                m1_missing.append((rep, "required_metrics"))
                continue
            capacity_pass, latency_pass = ratio >= 1.5, delta_ns * 2.0 < 50
            guest_values = {role: finite_number(
                                m1_by[rep, role]["metrics"].get("mean_ns_per_operation"))
                            for role in ("naive", "spill", "ideal")}
            row = {"repetition": rep, "capacity_ratio": ratio,
                   "spill_outer_samples": spill_outer["samples"],
                   "ideal_outer_samples": ideal_outer["samples"],
                   "spill_outer_mean_ns": spill_outer["mean_ns"],
                   "ideal_outer_mean_ns": ideal_outer["mean_ns"],
                   "outer_delta_ns": delta_ns, "outer_delta_cycles": delta_ns * 2.0,
                   "capacity_pass": capacity_pass, "latency_pass": latency_pass,
                   "pass": capacity_pass and latency_pass,
                   "legacy_guest_descriptive": {"means_ns_per_operation": guest_values,
                                                  "spill_minus_naive_ns":
                                                  (guest_values["spill"] - guest_values["naive"])
                                                  if guest_values["spill"] is not None and
                                                  guest_values["naive"] is not None else None}}
            m1_comp.append(row)
            matrix.append({"metric": "Metric1", "level": "repetition", "identity": rep, "tc": "TC131",
                            "value": ratio, "unit": "capacity-ratio", "status": "PASS" if row["pass"] else "FAIL",
                            "detail": f"outer_delta_ns={delta_ns:.9g}; roles=naive/spill/ideal"})
    ratios = [row["capacity_ratio"] for row in m1_comp]
    deltas = [row["outer_delta_ns"] for row in m1_comp]
    def distribution(values):
        values = [value for value in values if finite_number(value) is not None]
        mean = safe_mean(values)
        stdev = safe_stdev(values)
        return {"count": len(values), "mean": mean, "stdev": stdev,
                "cv": safe_divide(stdev, abs(mean) if mean is not None else None)}
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
                means = {p: finite_number(m2_by[rep, tc, p]["metrics"].get("mean_ns"))
                         for p in PROFILES}
                reduction = safe_divide(safe_subtract(means["naive"], means["optimized"]),
                                        means["naive"])
                if reduction is None:
                    m2_missing.append((rep, tc, "required_metrics"))
                    m2_cases.append({"repetition": rep, "tc": tc, "means_ns": means,
                                     "optimized_reduction_pct": None, "applicable": None})
                    matrix.append({"metric": "Metric2", "level": "TC", "identity": rep,
                                   "tc": f"TC{tc}", "value": None, "unit": "%",
                                   "status": "INCOMPLETE", "detail": "required mean unavailable"})
                    continue
                reduction *= 100
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
    m2_rep = [{"repetition": rep, "mean_reduction_pct": safe_mean(rep_means[rep]),
               "cases": len(rep_means[rep]),
               "pass": safe_mean(rep_means[rep]) >= 10}
              for rep in m2_reps if rep_means.get(rep)]
    m2_value = safe_mean(row["mean_reduction_pct"] for row in m2_rep)
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

    # Metric 3 supports independent repeats by default and strict legacy pairing.
    m3 = [r for r in resolved if r["metric"] == 3]
    default_req = {"repetitions": sorted({r["repetition"] for r in m3}),
                   "testcases": sorted({r["tc"] for r in m3})}
    m3_req = requirement(data, "metric3", default_req)
    paired_mode = (m3_req.get("mode") == "paired" or
                   ("mode" not in m3_req and bool(m3_req.get("pairs"))))
    comparison_mode = "paired" if paired_mode else "independent"
    tcs_req = [int(x) for x in m3_req.get("testcases", [])]
    samples, incomplete_pairs, missing_m3 = [], [], []
    m3_metric_incomplete = False
    conflicting_orders = {}
    counts_by_tc_arm = {}
    if paired_mode:
        pairs_req = [str(x) for x in m3_req.get("pairs", [])]
        groups = defaultdict(dict)
        for row in m3:
            if row.get("pair") is not None and row.get("order") is not None:
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
        evidence_root = output_dir / "evidence" / "metric3" if output_dir is not None else None
        for pair in pairs_req:
            for tc in tcs_req:
                if (pair, tc) in conflicting_orders:
                    continue
                candidates = [(key, arms) for key, arms in groups.items()
                              if key[0] == pair and key[1] == tc]
                if len(candidates) != 1 or set(candidates[0][1]) != {"ourcc", "ha-vi"}:
                    incomplete_pairs.append({"pair": pair, "tc": tc, "candidates": len(candidates),
                                             "present_arms": sorted(candidates[0][1]) if len(candidates) == 1 else []})
                    continue
                key, arms = candidates[0]
                order = key[2]
                if evidence_root is not None:
                    for arm, source in arms.items():
                        arm_dir = evidence_root / f"pair-{pair}" / f"TC{tc}" / arm
                        arm_dir.mkdir(parents=True, exist_ok=True)
                        result = {"pair": pair, "pair_id": f"pair-{pair}-tc{tc}", "tc": tc,
                                  "order": order, "arm": arm, "status": "PASS", "return_code": 0,
                            "metrics": source["metrics"], "raw_run_id": source["id"]}
                        (arm_dir / "result.json").write_text(json.dumps(
                            json_ready(result), indent=2, sort_keys=True,
                            allow_nan=False) + "\n")
                for name in M3[tc]:
                    left, right = arms["ourcc"]["metrics"][name], arms["ha-vi"]["metrics"][name]
                    if left["counter_frequency_hz"] != right["counter_frequency_hz"]:
                        issues.append({"severity": "ERROR", "code": "M3_FREQUENCY_MISMATCH",
                                       "run_id": f"{pair}/TC{tc}", "message": name})
                        continue
                    left_value = finite_number(left.get("ticks_per_operation"))
                    right_value = finite_number(right.get("ticks_per_operation"))
                    delta = safe_subtract(right_value, left_value)
                    if delta is None:
                        m3_metric_incomplete = True
                        missing_m3.append((pair, tc, name, "required_metrics"))
                        continue
                    samples.append({"pair": pair, "tc": tc, "order": order, "metric": name,
                                    "ourcc_ticks": left_value,
                                    "ha_vi_ticks": right_value,
                                    "delta_ticks": delta, "frequency_hz": left["counter_frequency_hz"]})
                    matrix.append({"metric": "Metric3", "level": "pair", "identity": pair,
                                   "tc": f"TC{tc}", "value": delta, "unit": f"ticks/op:{name}",
                                   "status": "OURCC_FASTER" if delta > 0 else "FAIL",
                                   "detail": f"order={order}; delta=HA-VI-OurCC"})
        summaries = []
        for key in sorted({(r["tc"], r["metric"]) for r in samples}):
            rows = [r for r in samples if (r["tc"], r["metric"]) == key]
            summaries.append({"tc": key[0], "metric": key[1], "pairs": len(rows),
                              "ourcc_mean_ticks": safe_mean(r["ourcc_ticks"] for r in rows),
                              "ha_vi_mean_ticks": safe_mean(r["ha_vi_ticks"] for r in rows),
                              "delta_mean_ticks": safe_mean(r["delta_ticks"] for r in rows)})
        m3_complete = (bool(pairs_req and tcs_req) and not incomplete_pairs and
                       not conflicting_orders and not m3_metric_incomplete and
                       set(tcs_req) == set(M3))
    else:
        repetitions = [str(x) for x in m3_req.get("repetitions", [])]
        min_repetitions = int(m3_req.get("min_repetitions", 0) or 0)
        arms_req = [norm_arm(x) for x in m3_req.get("arms", ["ourcc", "ha-vi"])]
        values = defaultdict(list)
        present = {(r["repetition"], r["tc"], r["arm"]): r for r in m3}
        if repetitions:
            missing_m3 = [(rep, tc, arm) for rep in repetitions for tc in tcs_req
                          for arm in arms_req if (rep, tc, arm) not in present]
        for run in m3:
            for name, metric in run["metrics"].items():
                value = finite_number(metric.get("ticks_per_operation"))
                if value is None:
                    m3_metric_incomplete = True
                    missing_m3.append((run["repetition"], run["tc"], run["arm"],
                                       name, "required_metrics"))
                    continue
                values[(run["tc"], name, run["arm"])].append(value)
                samples.append({"run_id": run["id"], "repetition": run["repetition"],
                                "tc": run["tc"], "arm": run["arm"], "metric": name,
                                "ticks_per_operation": value,
                                "frequency_hz": metric["counter_frequency_hz"]})
        summaries = []
        for tc, name in sorted({key[:2] for key in values}):
            arm_stats = {}
            for arm in ("ourcc", "ha-vi"):
                rows = values.get((tc, name, arm), [])
                if rows:
                    arm_stats[arm] = {"count": len(rows), "mean_ticks": safe_mean(rows),
                                      "stdev_ticks": safe_stdev(rows)}
            if set(arm_stats) == {"ourcc", "ha-vi"}:
                summaries.append({"tc": tc, "metric": name,
                                  "ourcc_count": arm_stats["ourcc"]["count"],
                                  "ha_vi_count": arm_stats["ha-vi"]["count"],
                                  "ourcc_stdev_ticks": arm_stats["ourcc"]["stdev_ticks"],
                                  "ha_vi_stdev_ticks": arm_stats["ha-vi"]["stdev_ticks"],
                                  "ourcc_mean_ticks": arm_stats["ourcc"]["mean_ticks"],
                                  "ha_vi_mean_ticks": arm_stats["ha-vi"]["mean_ticks"],
                                  "delta_mean_ticks": safe_subtract(
                                      arm_stats["ha-vi"]["mean_ticks"],
                                      arm_stats["ourcc"]["mean_ticks"])})
        counts_by_tc_arm = {str(tc): {arm: sum(r["tc"] == tc and r["arm"] == arm for r in m3)
                                      for arm in arms_req} for tc in tcs_req}
        insufficient = [(tc, arm, counts_by_tc_arm[str(tc)][arm], min_repetitions)
                        for tc in tcs_req for arm in arms_req
                        if min_repetitions and counts_by_tc_arm[str(tc)][arm] < min_repetitions]
        imbalanced = [{"tc": tc, "counts": counts_by_tc_arm[str(tc)]} for tc in tcs_req
                      if len(set(counts_by_tc_arm[str(tc)].values())) > 1]
        if imbalanced:
            issues.append(warning("M3_ARM_COUNT_IMBALANCE", "metric3",
                                  f"independent arm counts are imbalanced: {imbalanced}"))
        missing_m3.extend(insufficient)
        m3_complete = (bool(tcs_req) and not missing_m3 and not imbalanced and
                       not m3_metric_incomplete and set(tcs_req) == set(M3) and
                       set(arms_req) == {"ourcc", "ha-vi"})
    by_summary = {(r["tc"], r["metric"]): r for r in summaries}
    primary = []
    for tc in tcs_req:
        definition = M3_PRIMARY.get(tc, {})
        if definition and all((tc, name) in by_summary for name in definition):
            ourcc = safe_weighted_sum((weight, by_summary[tc, name].get("ourcc_mean_ticks"))
                                      for name, weight in definition.items())
            havi = safe_weighted_sum((weight, by_summary[tc, name].get("ha_vi_mean_ticks"))
                                     for name, weight in definition.items())
            delta = safe_subtract(havi, ourcc)
            if delta is None:
                m3_complete = False
                missing_m3.append((tc, "primary_required_metrics"))
                continue
            primary.append({"tc": tc, "ourcc_mean_ticks": ourcc, "ha_vi_mean_ticks": havi, "delta_mean_ticks": delta})
            matrix.append({"metric": "Metric3", "level": "TC", "identity": f"{comparison_mode} arm mean", "tc": f"TC{tc}",
                           "value": delta, "unit": "ticks/op", "status": "OURCC_FASTER" if delta > 0 else "FAIL",
                           "detail": "frozen primary-value formula"})
    aggregates = []
    for name, weights in M3_AGGREGATES.items():
        if all(key in by_summary for key in weights):
            delta = safe_weighted_sum((weight, by_summary[key].get("delta_mean_ticks"))
                                      for key, weight in weights.items())
            if delta is None:
                m3_complete = False
                missing_m3.append((name, "aggregate_required_metrics"))
                continue
            aggregates.append({"name": name, "delta_ticks": delta,
                               "status": "PASS (EXECUTABLE-REFERENCE-MODEL SCOPE)" if delta > 0 else "FAIL (EXECUTABLE-REFERENCE-MODEL SCOPE)"})
            matrix.append({"metric": "Metric3", "level": "aggregate", "identity": name, "tc": "ALL",
                           "value": delta, "unit": "ticks/op", "status": aggregates[-1]["status"],
                           "detail": "frozen weights; strict GT 0"})
    m3_requested = bool(tcs_req or m3)
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
              "metric1": {"status": m1_status, "definition": {
                                "capacity_ratio": "spill effective_unique / naive effective_unique",
                                "outer_delta_ns": "mean(spill completed Outer) - mean(ideal completed Outer)",
                                "cycles_per_ns": 2.0, "capacity_threshold": 1.5,
                                "outer_delta_cycles_strict_max": 50.0,
                                "guest_timer": "deprecated descriptive only"},
                           "roles": m1_roles, "ideal_min_capacity": int(m1_req.get("ideal_min_capacity", 102656)),
                           "missing_slots": m1_missing, "comparisons": m1_comp,
                           "aggregate": {"capacity_ratio": distribution(ratios),
                                         "outer_delta_ns": distribution(deltas),
                                         "outer_delta_cycles": distribution([x * 2.0 for x in deltas]),
                                         "pass_policy": "every repetition passes"}},
              "metric2": {"status": m2_status, "missing_slots": m2_missing, "cases": m2_cases,
                           "repetition_equal_weight": m2_rep,
                           "applicable_cases_by_repetition": applicable_sets,
                           "applicable_set_stable": applicable_stable,
                           "official_testcase_set_complete": m2_official_set,
                           "repetitions_without_applicable_cases": empty_applicable,
                           "aggregate_reduction_pct": m2_value},
               "metric3": {"status": m3_status, "executable_reference_model": True,
                           "comparison_mode": comparison_mode,
                           "comparison_policy": ("pair/tc/order identity-only; never Cartesian" if paired_mode else
                                                 "arm means; no pairing/ABBA"),
                           "pairing_policy": "pair/tc/order identity-only; never Cartesian" if paired_mode else None,
                           "counts_by_tc_arm": counts_by_tc_arm,
                           "missing_slots": missing_m3,
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
        value = finite_number(run["metrics"].get(
            "mean_ns_per_operation", run["metrics"].get("mean_ns")))
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
        self._inferred = {"metric1": {"repetitions": set(),
                                       "roles": {"naive", "spill", "ideal"},
                                       "ideal_min_capacity": 102656},
                          "metric2": {"repetitions": set(), "testcases": set()},
                          "metric3": {"mode": "independent", "repetitions": set(),
                                      "testcases": set(), "arms": {"ourcc", "ha-vi"}}}
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
                if metric == 1:
                    role = norm_metric1_role(raw["metric1_role"]) if raw.get("metric1_role") else None
                    return run_id, (metric, repetition, tc, role, profile)
                return run_id, (metric, repetition, tc, profile)
            if metric == 3:
                arm = norm_arm(raw["arm"])
                return run_id, (3, repetition, tc, arm)
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
            self._inferred["metric3"]["repetitions"].add(slot[1])
            self._inferred["metric3"]["testcases"].add(slot[2])
            self._inferred["metric3"]["arms"].add(slot[3])

    @staticmethod
    def _official_requirement_candidate(raw, slot):
        """Do not let an explicitly nonstandard extension expand formal coverage."""
        if slot is None:
            return False
        topology = str(raw.get("topology", "")).lower()
        if slot[0] == 1:
            return (slot[2] == 131 and topology == "8n1s" and
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
        if int(raw.get("metric", 0) or 0) == 1 and self._explicit_requirements:
            raw.setdefault("ideal_min_capacity", int(
                self._requirements.get("metric1", {}).get("ideal_min_capacity", 102656)))
        if int(raw.get("metric", 0) or 0) == 3:
            req = self._requirements.get("metric3", {}) if self._explicit_requirements else {}
            raw["comparison_mode"] = ("paired" if req.get("mode") == "paired" or
                                      ("mode" not in req and bool(req.get("pairs"))) else
                                      "independent")
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
            message = str(error)
            if message.startswith("ARM_IDENTITY_MISSING"):
                code = "ARM_IDENTITY_MISSING"
            elif message.startswith("ARM_IDENTITY_CONFLICT"):
                code = "ARM_IDENTITY_CONFLICT"
            else:
                code = "EVIDENCE_INVALID" if slot is not None else "RUN_SCHEMA_INVALID"
            issue = {"severity": "ERROR", "code": code, "run_id": fallback_id,
                      "contract_class": "standard" if official_candidate else "extension",
                      "message": message}
            result["issue"] = issue
            self._issues.append(issue)
            self._add_results.append(result)
            return json.loads(json.dumps(result))
        parsed_slot = logical_slot(parsed)
        if (not self._explicit_requirements and parsed["metric"] == 1 and
                parsed.get("standard_contract")):
            self._inferred["metric1"]["repetitions"].add(parsed["repetition"])
        if not self._explicit_requirements and parsed["metric"] == 3:
            self._inferred["metric3"]["repetitions"].add(parsed["repetition"])
            self._inferred["metric3"]["testcases"].add(parsed["tc"])
            self._inferred["metric3"]["arms"].add(parsed["arm"])
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
            requirements = {}
            for name, fields in self._inferred.items():
                requirements[name] = {key: (sorted(values) if isinstance(values, set) else values)
                                      for key, values in fields.items()}
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
        writer.writeheader()
        writer.writerows({key: ("N/A" if value is None else value)
                          for key, value in row.items()} for row in rows)


def write_outputs(output_dir, report, resolved, matrix, per_run, issues):
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(json.dumps(
        json_ready(report), indent=2, sort_keys=True, allow_nan=False) + "\n")
    (output_dir / "resolved_runs.json").write_text(json.dumps(
        json_ready(resolved), indent=2, sort_keys=True, allow_nan=False) + "\n")
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
