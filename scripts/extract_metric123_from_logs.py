#!/usr/bin/env python3
"""Extract unified Metric 1/2/3 evidence directly from explicit raw-log runs.

Only the Python standard library is required.  Pairing is identity-only and the
tool deliberately emits no confidence interval, t-test, or p-value.
"""

import argparse
import concurrent.futures
import copy
import csv
import gzip
import hashlib
import html
import json
import math
import multiprocessing
import numbers
import pathlib
import re
import statistics
import sys
import tempfile
from collections import defaultdict


_MERGE_FINGERPRINT_RECORDS = None
_AGGREGATE_PARALLEL_CONTEXT = None


PROFILES = ("naive", "spill-noopt", "optimized")
STANDARD_CONTRACT_ID = "standard"
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


def metric3_primary_weights(tc, topology="2n1s"):
    """Return TC primary weights, preserving the frozen 2N1S definition."""
    if tc != 232:
        return M3_PRIMARY[tc]
    nodes, sockets = topology_size(topology)
    planes = nodes * sockets
    return {"hot_key_read": planes / (planes + 1),
            "hot_key_write": 1 / (planes + 1)}


def metric3_aggregate_weights(topology="2n1s"):
    """Return equal-TC tier weights with topology-correct TC232 composition."""
    result = {name: dict(weights) for name, weights in M3_AGGREGATES.items()}
    tc232 = metric3_primary_weights(232, topology)
    representative = result["representative_equal_weight"]
    representative[(232, "hot_key_read")] = tc232["hot_key_read"] / 5
    representative[(232, "hot_key_write")] = tc232["hot_key_write"] / 5
    return result
STATE_RE = re.compile(r"\[UBCC-STATE\].*capacity=(\d+).*policy=([A-Za-z0-9_-]+)")
POLICY_RE = re.compile(r"\[UBIO-POLICY\].*?effective=([A-Za-z0-9_-]+)")
MARKER_FIELD_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)=([^\s,]+)")
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


def metric3_direction(delta):
    """Classify a real Metric3 delta without turning direction into a gate."""
    value = finite_number(delta)
    if value is None:
        return "UNAVAILABLE"
    return "OURCC_FASTER" if value > 0 else "HA_VI_FASTER" if value < 0 else "TIE"


def minimum_samples(requirements):
    """Resolve pooled sample count, accepting old repetition-based contracts."""
    repetitions = requirements.get("repetitions", [])
    legacy = requirements.get("min_repetitions", 0)
    requested = requirements.get("min_samples", legacy)
    return max(1, int(requested or len(repetitions) or 1))


def pooled_outer_latency(runs):
    """Pool completed Outer observations across runs without reopening logs."""
    values = [finite_number(source.get("latency_ps"))
              for run in runs
              for source in run["metrics"]["outer_latency"].get("sources", [])]
    values = [value for value in values if value is not None]
    return {"samples": len(values),
            "mean_ns": safe_mean(value / 1000.0 for value in values)}


def json_ready(value):
    """Recursively map non-finite numeric leaves to JSON null."""
    if isinstance(value, dict):
        return {key: json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        return value if math.isfinite(value) else None
    return value


def normalize_workers(workers):
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be an integer >= 1")
    return workers


def process_pool(max_workers, initializer=None, initargs=()):
    """Create the Linux fork pool required by snapshot-heavy parallel tasks."""
    try:
        context = multiprocessing.get_context("fork")
    except ValueError as error:
        raise ExtractError("workers > 1 requires multiprocessing fork support") from error
    return concurrent.futures.ProcessPoolExecutor(
        max_workers=max_workers, mp_context=context,
        initializer=initializer, initargs=initargs)


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


def normalize_qualification_sets(requirements):
    """Validate and canonicalize the explicit formal-contract registry."""
    raw_sets = (requirements or {}).get("qualification_sets", [])
    if raw_sets is None:
        raw_sets = []
    if not isinstance(raw_sets, list):
        raise ExtractError("requirements.qualification_sets must be an array")
    result, ids = [], set()
    for raw in raw_sets:
        if not isinstance(raw, dict) or not raw.get("id"):
            raise ExtractError("each qualification set requires a non-empty id")
        item = copy.deepcopy(raw)
        item["id"] = str(item["id"])
        if item["id"] == STANDARD_CONTRACT_ID or item["id"] in ids:
            raise ExtractError(f"duplicate/reserved qualification id {item['id']!r}")
        ids.add(item["id"])
        try:
            item["metric"] = int(item["metric"])
        except (KeyError, TypeError, ValueError) as error:
            raise ExtractError(f"qualification {item['id']!r} requires metric=1|2|3") from error
        if item["metric"] not in (1, 2, 3):
            raise ExtractError(f"qualification {item['id']!r} requires metric=1|2|3")
        coordinates = item.get("coordinates")
        if item["metric"] in (1, 2):
            if not isinstance(coordinates, list) or not coordinates:
                raise ExtractError(f"qualification {item['id']!r} requires coordinates")
            normalized = []
            for coordinate in coordinates:
                if not isinstance(coordinate, dict):
                    raise ExtractError(f"qualification {item['id']!r} has invalid coordinate")
                coordinate = copy.deepcopy(coordinate)
                try:
                    coordinate["tc"] = int(coordinate["tc"])
                    coordinate["topology"] = str(coordinate["topology"]).lower()
                    if item["metric"] == 1:
                        coordinate["home_node"] = int(coordinate["home_node"])
                        coordinate["home_socket"] = int(coordinate["home_socket"])
                    else:
                        coordinate["phase"] = str(coordinate["phase"])
                        coordinate["kind"] = str(coordinate.get("kind", "latency"))
                        coordinate["reduction"] = str(coordinate.get("reduction", "aggregate"))
                        if coordinate["kind"] not in ("timer", "latency"):
                            raise ValueError("kind must be timer|latency")
                        if coordinate["reduction"] not in ("aggregate", "max"):
                            raise ValueError("reduction must be aggregate|max")
                        raw_nodes = coordinate.get("expected_nodes")
                        if raw_nodes is None:
                            raw_nodes = [coordinate["expected_node"]]
                        coordinate["expected_nodes"] = sorted({int(node) for node in raw_nodes})
                        if not coordinate["expected_nodes"]:
                            raise ValueError("expected_nodes must not be empty")
                        coordinate["expected_count"] = int(coordinate.get(
                            "expected_count", coordinate.get("expected_samples")))
                        # Retain legacy aliases in the normalized definition.
                        coordinate["expected_node"] = (coordinate["expected_nodes"][0]
                                                       if len(coordinate["expected_nodes"]) == 1
                                                       else None)
                        coordinate["expected_samples"] = coordinate["expected_count"]
                except (KeyError, TypeError, ValueError) as error:
                    raise ExtractError(f"qualification {item['id']!r} has incomplete coordinate") from error
                normalized.append(coordinate)
            item["coordinates"] = normalized
            item["repetitions"] = [str(x) for x in item.get("repetitions", [])]
            item["min_samples"] = minimum_samples(item)
        thresholds = item.get("thresholds", {})
        if not isinstance(thresholds, dict):
            raise ExtractError(f"qualification {item['id']!r} thresholds must be an object")
        if item["metric"] == 1:
            item["ideal_min_capacity"] = int(item.get("ideal_min_capacity", 102656))
            item["thresholds"] = {
                "capacity_ratio_min": float(thresholds.get("capacity_ratio_min", 1.5)),
                "outer_delta_cycles_strict_max": float(
                    thresholds.get("outer_delta_cycles_strict_max", 50.0)),
                "cycles_per_ns": float(thresholds.get("cycles_per_ns", 2.0)),
            }
        elif item["metric"] == 2:
            item["profiles"] = [norm_profile(x) for x in item.get("profiles", PROFILES)]
            item["baseline_profile"] = norm_profile(item.get("baseline_profile", "naive"))
            item["result_profile"] = norm_profile(item.get("result_profile", "optimized"))
            if item["baseline_profile"] not in item["profiles"] or item["result_profile"] not in item["profiles"]:
                raise ExtractError(f"qualification {item['id']!r} baseline/result must be in profiles")
            item["thresholds"] = {
                "baseline_applicable_min_ns": float(
                    thresholds.get("baseline_applicable_min_ns", 500.0)),
                "reduction_pct_min": float(thresholds.get("reduction_pct_min", 10.0)),
            }
        else:
            item["mode"] = str(item.get("mode", "independent"))
            if item["mode"] not in ("paired", "independent"):
                raise ExtractError(f"qualification {item['id']!r} mode must be paired|independent")
            item["topologies"] = [str(x).lower() for x in item.get("topologies", [])]
            item["testcases"] = [int(x) for x in item.get("testcases", [])]
            if not item["topologies"] or not item["testcases"] or not set(item["testcases"]) <= set(M3):
                raise ExtractError(f"qualification {item['id']!r} requires topologies and builtin TC228-235 testcases")
            item["arms"] = [norm_arm(x) for x in item.get("arms", ("ourcc", "ha-vi"))]
            if set(item["arms"]) != {"ourcc", "ha-vi"} or len(item["arms"]) != 2:
                raise ExtractError(
                    f"qualification {item['id']!r} Metric3 arms must be exactly ourcc and ha-vi")
            item["thresholds"] = {"delta_ticks_strict_min": float(
                thresholds.get("delta_ticks_strict_min", 0.0))}
            if item["mode"] == "paired":
                item["pairs"] = [str(x) for x in item.get("pairs", [])]
                if not item["pairs"]:
                    raise ExtractError(f"qualification {item['id']!r} paired mode requires pairs")
            else:
                item["repetitions"] = [str(x) for x in item.get("repetitions", [])]
                item["min_samples"] = minimum_samples(item)
                item["min_repetitions"] = item["min_samples"]
        result.append(item)
    return result


def qualification_candidates(raw, qualification_sets):
    """Return only registry entries whose declared coordinate matches a run."""
    try:
        metric, tc = int(raw.get("metric")), int(raw.get("tc"))
        topology = str(raw.get("topology", "")).lower()
    except (TypeError, ValueError):
        return []
    matches = []
    for item in qualification_sets:
        if item["metric"] != metric:
            continue
        if metric == 3:
            if tc in item["testcases"] and topology in item["topologies"]:
                matches.append(item)
            continue
        for index, coordinate in enumerate(item["coordinates"]):
            if coordinate["tc"] != tc or coordinate["topology"] != topology:
                continue
            if (metric == 2 and raw.get("phase") is not None and
                    str(raw["phase"]) != coordinate["phase"]):
                continue
            if metric == 1 and (int(raw.get("home_node", 0)) != coordinate["home_node"] or
                                int(raw.get("home_socket", 0)) != coordinate["home_socket"]):
                continue
            matches.append({**item, "_coordinate_index": index, "_coordinate": coordinate})
    return matches


def qualified_contract_ids(run, candidates):
    """Return qualification IDs passed by one fully parsed run snapshot."""
    qualified = []
    for candidate in candidates:
        metric = candidate["metric"]
        passed = False
        if metric == 1:
            coordinate = candidate["_coordinate"]
            role = run.get("metric1_role")
            capacity = run["metrics"]["capacity"]
            outer = run["metrics"]["outer_latency"]
            common = (run["tc"] == coordinate["tc"] and
                      run["topology"] == coordinate["topology"] and
                      int(run.get("home_node", 0)) == coordinate["home_node"] and
                      int(run.get("home_socket", 0)) == coordinate["home_socket"])
            if role == "naive":
                passed = (common and run["profile"] == "naive" and
                          capacity["policy"] == "naive" and
                          capacity["experimental_oversized_resident_dir"] in (None, 0))
            elif role == "spill":
                passed = (common and run["profile"] == "spill-noopt" and
                          capacity["policy"] == "spill" and
                          capacity["experimental_oversized_resident_dir"] in (None, 0) and
                          capacity["h64_exact_live_known"] == 1 and outer["samples"] >= 1)
            elif role == "ideal":
                passed = (common and run["profile"] == "spill-noopt" and
                          capacity["policy"] == "spill" and
                          capacity["experimental_oversized_resident_dir"] == 1 and
                          capacity["resident_capacity"] >= candidate["ideal_min_capacity"] and
                          capacity["backstore_found_fills"] == 0 and
                          capacity["h64_exact_live"] == 0 and
                          capacity["h64_exact_live_known"] in (0, 1) and
                          outer["samples"] >= 1)
        elif metric == 2:
            coordinate = candidate["_coordinate"]
            metrics = run["metrics"]
            passed = (run["tc"] == coordinate["tc"] and
                      run["topology"] == coordinate["topology"] and
                      metrics.get("phase") == coordinate["phase"] and
                      metrics.get("kind", "latency") == coordinate["kind"] and
                      metrics.get("reduction", "aggregate") == coordinate["reduction"] and
                      metrics.get("nodes") == coordinate["expected_nodes"] and
                      metrics.get("samples") == coordinate["expected_count"] and
                      run["profile"] in candidate["profiles"])
        else:
            passed = (run["tc"] in candidate["testcases"] and
                      run["topology"] in candidate["topologies"] and
                      run["arm"] in candidate["arms"])
        if passed:
            qualified.append(candidate["id"])
    return sorted(set(qualified))


def normalize_legacy_metric1_profile(run):
    """Translate the old Metric1 ``profile=ideal`` vocabulary to role form."""
    out = dict(run)
    try:
        metric = int(out.get("metric", 0) or 0)
    except (TypeError, ValueError):
        return out
    raw_profile = str(out.get("profile", "")).lower()
    if metric != 1 or raw_profile not in ("ideal", "ideal-dir", "infinite"):
        return out
    if (out.get("metric1_role") is not None and
            norm_metric1_role(out["metric1_role"]) != "ideal"):
        raise ExtractError(
            f"legacy Metric1 profile {raw_profile!r} conflicts with "
            f"metric1_role={out['metric1_role']!r}")
    out["profile"] = "spill-noopt"
    out["metric1_role"] = "ideal"
    warnings = list(out.get("_contract_warnings", []))
    if not any(isinstance(item, dict) and
               item.get("code") == "LEGACY_METRIC1_PROFILE_NORMALIZED"
               for item in warnings):
        warnings.append(warning(
            "LEGACY_METRIC1_PROFILE_NORMALIZED", out.get("id", ""),
            f"legacy profile={raw_profile!r} normalized to "
            "profile='spill-noopt', metric1_role='ideal'"))
    out["_contract_warnings"] = warnings
    return out


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


def warning(code, run_id, message, **details):
    return {"severity": "WARNING", "code": code, "run_id": str(run_id),
            "message": message, **details}


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


def scan_marker_rows(paths, kind, phase=None):
    """Parse markers independent of field order and return diagnostics."""
    marker = "[GUEST-TIMER]" if kind == "timer" else "[PERF-LATENCY]"
    required = ({"node", "phase", "operations", "counter_ticks",
                 "counter_frequency_hz", "source", "unit"}
                if kind == "timer" else
                {"node", "phase", "samples", "min", "p50", "p95", "p99",
                 "max", "mean", "counter_frequency_hz", "source", "unit"})
    rows = []
    diagnostics = {"files": [str(path) for path in paths], "marker_lines": 0,
                   "available_phases": set(), "malformed": []}
    for path in paths:
        with open_text(path) as stream:
            for line_no, line in enumerate(stream, 1):
                if marker not in line:
                    continue
                diagnostics["marker_lines"] += 1
                fields = dict(MARKER_FIELD_RE.findall(line.split(marker, 1)[1]))
                marker_phase = fields.get("phase")
                if marker_phase:
                    diagnostics["available_phases"].add(marker_phase)
                missing = sorted(required - set(fields))
                if missing:
                    diagnostics["malformed"].append(
                        {"file": str(path), "line": line_no,
                         "missing_fields": missing})
                    continue
                if phase is not None and marker_phase != phase:
                    continue
                if kind == "timer":
                    node, count, ticks, freq = (int(fields["node"]),
                        int(fields["operations"]), int(fields["counter_ticks"]),
                        int(fields["counter_frequency_hz"]))
                    source, unit = fields["source"], fields["unit"]
                else:
                    node, count, ticks, freq = (int(fields["node"]),
                        int(fields["samples"]), int(fields["mean"]),
                        int(fields["counter_frequency_hz"]))
                    source, unit = fields["source"], fields["unit"]
                    ordered = [int(fields[name]) for name in
                               ("min", "p50", "p95", "p99", "max")]
                    if ordered != sorted(ordered) or not ordered[0] <= ticks <= ordered[-1]:
                        raise ExtractError(f"invalid PERF-LATENCY ordering in {path}:{line_no}")
                if count <= 0 or ticks <= 0 or freq <= 0 or source != "arm_cntvct_el0" or unit != "counter_ticks":
                    raise ExtractError(f"invalid {kind} marker in {path}:{line_no}")
                rows.append({"file": str(path), "line": line_no, "node": node,
                             "phase": marker_phase, "count": count,
                             "ticks": ticks, "frequency_hz": freq})
    diagnostics["available_phases"] = sorted(diagnostics["available_phases"])
    return rows, diagnostics


def marker_rows(paths, kind, phase=None):
    return scan_marker_rows(paths, kind, phase)[0]


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


def aggregate_configured_metric2(rows, kind, reduction, expected_nodes,
                                 expected_count):
    if not rows:
        raise ExtractError("configured Metric2 phase has no records")
    frequencies = {row["frequency_hz"] for row in rows}
    if len(frequencies) != 1:
        raise ExtractError("configured Metric2 counter frequency mismatch")
    nodes = sorted({row["node"] for row in rows})
    if nodes != sorted(expected_nodes):
        raise ExtractError(
            f"configured Metric2 requires nodes={sorted(expected_nodes)}, got {nodes}")
    total = sum(row["count"] for row in rows)
    if total != expected_count:
        raise ExtractError(
            f"configured Metric2 requires total count={expected_count}, got {total}")
    if reduction == "max":
        value = max((row["ticks"] / row["count"] if kind == "timer"
                     else row["ticks"]) for row in rows)
    elif kind == "timer":
        value = sum(row["ticks"] for row in rows) / total
    else:
        value = sum(row["ticks"] * row["count"] for row in rows) / total
    frequency = next(iter(frequencies))
    return {"phase": rows[0]["phase"], "kind": kind, "reduction": reduction,
            "node": nodes[0] if len(nodes) == 1 else None, "nodes": nodes,
            "samples": total, "records": len(rows), "mean_ticks": value,
            "frequency_hz": frequency, "mean_ns": value * 1e9 / frequency,
            "sources": rows, "source": rows[0] if len(rows) == 1 else None}


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
    run = normalize_legacy_metric1_profile(run)
    try:
        metric = int(run.get("metric", 0) or 0)
    except (TypeError, ValueError):
        metric = 0
    required = {"metric", "tc", "topology", "simulator_log_dir", "simout_dir"}
    if metric in (1, 2):
        required.add("repetition")
    missing = required - set(run)
    if missing:
        raise ExtractError(f"missing fields {sorted(missing)}")
    out = dict(run)
    if "id" not in run:
        raise ExtractError("internal run id was not assigned")
    out["id"], out["metric"], out["tc"] = str(run["id"]), int(run["metric"]), int(run["tc"])
    if out["metric"] == 3 and run.get("repetition") in (None, ""):
        out["repetition"] = out["id"]
        out["repetition_source"] = "auto-run-id"
    else:
        out["repetition"] = str(run["repetition"])
    out["topology"] = str(run["topology"]).lower()
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
        rows, timer_diagnostics = scan_marker_rows(paths, "timer", phase)
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
                f"counts={dict((n, len(v)) for n, v in by_node.items())} "
                f"simout_files={len(timer_diagnostics['files'])} "
                f"marker_lines={timer_diagnostics['marker_lines']} "
                f"available_phases={timer_diagnostics['available_phases']} "
                f"malformed={timer_diagnostics['malformed'][:3]}"))
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
        failed_gates = []
        if out["tc"] != 131:
            failed_gates.append(f"tc={out['tc']} expected=131")
        if out["topology"] != "8n1s":
            failed_gates.append(f"topology={out['topology']} expected=8n1s")
        if home_node != 0 or home_socket != 0:
            failed_gates.append(f"home={home_node}/{home_socket} expected=0/0")
        if role == "naive":
            qualified = (out["profile"] == "naive" and capacity["policy"] == "naive" and
                         capacity["experimental_oversized_resident_dir"] in (None, 0))
            if out["profile"] != "naive": failed_gates.append("profile must be naive")
            if capacity["policy"] != "naive": failed_gates.append("UBIO policy must be naive")
            if capacity["experimental_oversized_resident_dir"] not in (None, 0):
                failed_gates.append("oversized ResidentDir must be disabled")
        elif role == "spill":
            qualified = (out["profile"] == "spill-noopt" and capacity["policy"] == "spill" and
                          capacity["experimental_oversized_resident_dir"] in (None, 0) and
                          capacity["h64_exact_live_known"] == 1 and
                          outer["samples"] >= 1)
            if out["profile"] != "spill-noopt": failed_gates.append("profile must be spill-noopt")
            if capacity["policy"] != "spill": failed_gates.append("UBIO policy must be spill")
            if capacity["experimental_oversized_resident_dir"] not in (None, 0):
                failed_gates.append("oversized ResidentDir must be disabled")
            if capacity["h64_exact_live_known"] != 1:
                failed_gates.append("h64ExactLiveKnown must equal 1")
            if outer["samples"] < 1:
                failed_gates.append("completed Outer samples must be >=1")
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
            if out["profile"] != "spill-noopt": failed_gates.append("profile must be spill-noopt")
            if capacity["policy"] != "spill": failed_gates.append("UBIO policy must be spill")
            if capacity["experimental_oversized_resident_dir"] != 1:
                failed_gates.append("experimental oversized ResidentDir must be enabled")
            if capacity["resident_capacity"] < ideal_min:
                failed_gates.append(
                    f"resident capacity {capacity['resident_capacity']} < {ideal_min}")
            if capacity["backstore_found_fills"] != 0:
                failed_gates.append("Backstore found fills must equal 0")
            if capacity["h64_exact_live"] != 0:
                failed_gates.append("h64ExactLiveCount must equal 0")
            if capacity["h64_exact_live_known"] not in (0, 1):
                failed_gates.append("h64ExactLiveKnown must be 0 or 1")
            if outer["samples"] < 1:
                failed_gates.append("completed Outer samples must be >=1")
        else:
            qualified = False
            failed_gates.append(f"role={role} is descriptive support only")
        standard = common and qualified
        if not standard:
            out["contract_warnings"].append(warning(
                "NONSTANDARD_CONTRACT", out["id"],
                f"Metric1 descriptive extension role={role} tc={out['tc']} topology={out['topology']} "
                f"profile={out['profile']} home={home_node}/{home_socket} "
                f"failed_gates={failed_gates}", failed_gates=failed_gates))
    elif out["metric"] == 2:
        registered = out["tc"] in M2
        metric2_candidates = [candidate for candidate in
                              run.get("_qualification_candidates", [])
                              if candidate["metric"] == 2]
        candidate_specs = {(candidate["_coordinate"]["kind"],
                            candidate["_coordinate"]["phase"],
                            candidate["_coordinate"]["reduction"],
                            tuple(candidate["_coordinate"]["expected_nodes"]),
                            candidate["_coordinate"]["expected_count"])
                           for candidate in metric2_candidates}
        if len(candidate_specs) > 1:
            raise ExtractError(
                f"Metric2 qualification contracts disagree for run {out['id']}: "
                f"{sorted(candidate_specs, key=str)}")
        configured_spec = next(iter(candidate_specs)) if candidate_specs else None
        frozen_contract_requested = False
        if registered:
            frozen_phase, frozen_topology, frozen_node, frozen_samples = M2[out["tc"]]
            frozen_contract_requested = (
                out["topology"] == frozen_topology and
                str(run.get("phase", frozen_phase)) == frozen_phase and
                int(run.get("expected_node", frozen_node)) == frozen_node and
                int(run.get("expected_samples", frozen_samples)) == frozen_samples)
        if frozen_contract_requested:
            configured_spec = None
        parse_kind = configured_spec[0] if configured_spec else "latency"
        all_rows = marker_rows(paths, parse_kind)
        by_phase = defaultdict(list)
        for row in all_rows:
            by_phase[row["phase"]].append(row)
        if configured_spec is not None:
            kind, phase, reduction, expected_nodes, expected_count = configured_spec
            rows = by_phase.get(phase, [])
            selected = aggregate_configured_metric2(
                rows, kind, reduction, expected_nodes, expected_count)
            official_topology = M2[out["tc"]][1] if registered else None
            default_phase, default_node, default_samples = (
                (M2[out["tc"]][0], M2[out["tc"]][2], M2[out["tc"]][3])
                if registered else (None, None, None))
            node = selected["node"]
            samples = selected["samples"]
        elif registered:
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
        latency_phases = {
            name: (aggregate_configured_metric2(
                       rows, configured_spec[0], configured_spec[2],
                       configured_spec[3], configured_spec[4])
                   if configured_spec is not None and name == configured_spec[1]
                   else aggregate_latency_phase(
                       rows, node if not registered and configured_spec is None else None,
                       samples if not registered and configured_spec is None and
                       len(by_phase) == 1 else None))
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
        standard = (registered and configured_spec is None and
                    out["topology"] == official_topology and phase == default_phase and
                    node == default_node and samples == default_samples)
        if not standard:
            failed_gates = []
            if not registered:
                failed_gates.append(f"TC{out['tc']} is not in the standard Metric2 registry")
            else:
                if out["topology"] != official_topology:
                    failed_gates.append(f"topology={out['topology']} expected={official_topology}")
                if phase != default_phase:
                    failed_gates.append(f"phase={phase} expected={default_phase}")
                if node != default_node:
                    failed_gates.append(f"node={node} expected={default_node}")
                if samples != default_samples:
                    failed_gates.append(f"samples={samples} expected={default_samples}")
            out["contract_warnings"].append(warning(
                "NONSTANDARD_CONTRACT", out["id"],
                f"Metric2 descriptive extension tc={out['tc']} topology={out['topology']} "
                f"phase={phase} node={node} samples={samples} "
                f"failed_gates={failed_gates}", failed_gates=failed_gates))
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
            failed_gates = []
            if not registered:
                failed_gates.append(f"TC{out['tc']} is not in the standard Metric3 registry")
            if out["topology"] != "2n1s":
                failed_gates.append(f"topology={out['topology']} expected=2n1s")
            out["contract_warnings"].append(warning(
                "NONSTANDARD_CONTRACT", out["id"],
                f"Metric3 descriptive extension tc={out['tc']} topology={out['topology']} "
                f"failed_gates={failed_gates}", failed_gates=failed_gates))
    qualified_contracts = qualified_contract_ids(
        out, run.get("_qualification_candidates", []))
    out["standard_contract"] = bool(standard)
    out["qualified_contracts"] = sorted(set(qualified_contracts))
    out["formal_contract"] = bool(standard or qualified_contracts)
    out["contract_class"] = "standard" if standard else "extension"
    out.pop("_qualification_candidates", None)
    out["status"] = "VALID"
    return out


def logical_slot(run):
    """Identity of one experiment coordinate, including extension dimensions."""
    if run["metric"] == 1:
        return (1, run["id"], run["tc"], run["topology"],
                run.get("metric1_role"), run["profile"],
                run["metrics"].get("phase"),
                tuple(item["node"] for item in (run["metrics"].get("timers") or [])),
                int(run.get("home_node", 0)), int(run.get("home_socket", 0)))
    if run["metric"] == 2:
        return (2, run["id"], run["tc"], run["topology"], run["profile"],
                run["metrics"].get("phase"), run["metrics"].get("node"),
                run["metrics"].get("samples"))
    names = tuple(sorted(run["metrics"]))
    if run.get("comparison_mode") == "paired":
        return (3, "paired", run.get("pair"), run["tc"], run["topology"],
                run.get("order"), run["arm"], names)
    return (3, "independent", run["id"], run["tc"], run["topology"],
            run["arm"], names)


def requirement(manifest, name, default):
    value = manifest.get("requirements", {}).get(name, default)
    return value if isinstance(value, dict) else default


def integer_requirement_list(requirements, metric, field="testcases"):
    """Normalize an integer requirement list with a precise contract error."""
    values = requirements.get(field, [])
    if not isinstance(values, list):
        raise ExtractError(f"requirements.{metric}.{field} must be an array")
    result = []
    for index, value in enumerate(values):
        try:
            result.append(int(value))
        except (TypeError, ValueError) as error:
            raise ExtractError(
                f"requirements.{metric}.{field}[{index}] must be an integer, "
                f"got {value!r}") from error
    return result


def descriptive_view(runs):
    """Small contract-neutral matrix and useful profile/arm comparisons."""
    matrix, groups = [], defaultdict(list)
    for run in runs:
        value = run["metrics"].get("mean_ns_per_operation", run["metrics"].get("mean_ns"))
        if value is None and run["metric"] == 3:
            value = safe_mean(x.get("ns_per_operation") for x in run["metrics"].values())
        value = finite_number(value)
        if run["metric"] == 1:
            capacity = finite_number(run["metrics"]["capacity"].get("effective_unique"))
            outer = finite_number(run["metrics"]["outer_latency"].get("mean_ns"))
            matrix.extend([
                {"metric": "Metric1", "level": "run-capacity", "identity": run["id"],
                 "tc": f"TC{run['tc']}", "value": capacity, "unit": "lines",
                 "status": "DESCRIPTIVE",
                 "detail": f"{run['contract_class']}; role={run.get('metric1_role', '')}"},
                {"metric": "Metric1", "level": "run-outer", "identity": run["id"],
                 "tc": f"TC{run['tc']}", "value": outer, "unit": "ns",
                 "status": "DESCRIPTIVE",
                 "detail": f"{run['contract_class']}; role={run.get('metric1_role', '')}"},
            ])
        elif run["metric"] == 2 and run["metrics"].get("latency_phases"):
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
        if run["metric"] != 1 and finite_number(value) is not None:
            groups[key].append(value)
    summaries = []
    for key, values in sorted(groups.items(), key=lambda x: tuple(map(str, x[0]))):
        summaries.append({"metric": key[0], "tc": key[1], "topology": key[2],
                          "repetition": key[3], "profile_or_arm": key[4],
                          "runs": len(values), "mean_ns_per_operation": safe_mean(values)})

    comparisons = []
    by_profile = defaultdict(dict)
    for run in runs:
        if run["metric"] == 2:
            by_profile[(run["metric"], run["tc"], run["topology"],
                         run["repetition"])][run["profile"]] = run
    for key, profiles in sorted(by_profile.items(), key=lambda x: tuple(map(str, x[0]))):
        if all(p in profiles for p in PROFILES):
            naive, optimized = profiles["naive"], profiles["optimized"]
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
                                  arms["ourcc"]["metrics"][name].get("ticks_per_operation")),
                              "ourcc_ns_per_operation": finite_number(
                                  arms["ourcc"]["metrics"][name].get("ns_per_operation")),
                              "ha_vi_ns_per_operation": finite_number(
                                  arms["ha-vi"]["metrics"][name].get("ns_per_operation")),
                              "delta_ns": safe_subtract(
                                  arms["ha-vi"]["metrics"][name].get("ns_per_operation"),
                                  arms["ourcc"]["metrics"][name].get("ns_per_operation"))}
                              for name in common}})
    arm_groups = defaultdict(list)
    for run in runs:
        if run["metric"] == 3:
            for name, metric in run["metrics"].items():
                value = finite_number(metric.get("ns_per_operation"))
                if value is not None:
                    arm_groups[(run["tc"], run["topology"], name, run["arm"])].append(value)
    arm_comparisons = []
    coordinates = sorted({key[:3] for key in arm_groups}, key=lambda x: tuple(map(str, x)))
    for tc, topology, name in coordinates:
        arms = {}
        for arm in ("ourcc", "ha-vi"):
            values = arm_groups.get((tc, topology, name, arm), [])
            if values:
                arms[arm] = {"count": len(values), "mean_ns_per_operation": safe_mean(values),
                             "stdev_ns_per_operation": safe_stdev(values)}
        arm_comparisons.append({"tc": tc, "topology": topology, "metric": name,
                                "arms": arms,
                                "delta_ns": safe_subtract(
                                                arms["ha-vi"]["mean_ns_per_operation"],
                                                arms["ourcc"]["mean_ns_per_operation"])
                                if set(arms) == {"ourcc", "ha-vi"} else None})
    m1_role_comparisons = []
    role_groups = defaultdict(lambda: defaultdict(list))
    for run in runs:
        if run["metric"] == 1:
            role_groups[(run["tc"], run["topology"])][
                run.get("metric1_role", run["profile"])].append(run)
    for key, roles in sorted(role_groups.items(), key=lambda x: tuple(map(str, x[0]))):
        naive_mean = safe_mean(run["metrics"]["capacity"].get("effective_unique")
                               for run in roles.get("naive", []))
        spill_mean = safe_mean(run["metrics"]["capacity"].get("effective_unique")
                               for run in roles.get("spill", []))
        spill_outer = pooled_outer_latency(roles.get("spill", []))
        ideal_outer = pooled_outer_latency(roles.get("ideal", []))
        m1_role_comparisons.append({"aggregation_id": "pooled", "repetition": None,
                                    "tc": key[0], "topology": key[1],
                                    "roles": sorted(roles),
                                    "capacity_ratio": safe_divide(spill_mean, naive_mean),
                                    "outer_delta_ns": safe_subtract(
                                        spill_outer["mean_ns"], ideal_outer["mean_ns"])})
    return {"runs": len(runs), "summaries": summaries,
            "comparisons": comparisons, "metric3_pairs": m3_pairs,
            "metric1_role_comparisons": m1_role_comparisons,
            "metric3_arm_comparisons": arm_comparisons, "matrix": matrix}


def source_inventory(runs):
    """Separate logical runs, unique files, and per-marker source references."""
    files = set()
    source_references = 0

    def add_file(value):
        if value in (None, ""):
            return
        text = str(value)
        match = re.fullmatch(r"(.+):\d+", text)
        files.add(match.group(1) if match else text)

    def walk(value, key=None):
        nonlocal source_references
        if isinstance(value, dict):
            if value.get("file"):
                add_file(value["file"])
            for child_key, child in value.items():
                if child_key != "file":
                    walk(child, child_key)
        elif isinstance(value, list):
            if key == "sources":
                source_references += len(value)
            for child in value:
                if key in ("sources", "source_files") and isinstance(child, str):
                    add_file(child)
                walk(child)
        elif key in ("verifier", "path"):
            add_file(value)

    for run in runs:
        for path in run.get("simout_by_node", {}).values():
            add_file(path)
        for path in run.get("home_ubio_logs", []):
            add_file(path)
        correctness_data = run.get("correctness", {})
        add_file(correctness_data.get("verifier"))
        for child in correctness_data.get("child_exits", []):
            add_file(child.get("path"))
        walk(run.get("metrics", {}))
    return {"logical_runs": len(runs), "unique_files": len(files),
            "source_references": source_references,
            "note": "source references are evidence rows/markers, not logical runs"}


def aggregate_qualification(item, runs, issues):
    """Aggregate one opt-in contract without feeding it into frozen standards."""
    selected = [run for run in runs if item["id"] in run.get("qualified_contracts", [])]
    registry_errors = [issue for issue in issues
                       if item["id"] in issue.get("qualification_contracts", [])]
    missing, results = [], []
    status = "INCOMPLETE"
    if item["metric"] == 1:
        thresholds = item["thresholds"]
        for coordinate in item["coordinates"]:
            key0 = (coordinate["tc"], coordinate["topology"],
                    coordinate["home_node"], coordinate["home_socket"])
            coordinate_runs = [run for run in selected
                               if (run["tc"], run["topology"],
                                   int(run.get("home_node", 0)),
                                   int(run.get("home_socket", 0))) == key0]
            groups = {role: [run for run in coordinate_runs
                             if run.get("metric1_role") == role]
                      for role in ("naive", "spill", "ideal")}
            coordinate_missing = [
                {"kind": "minimum_samples", "dimension": "role", "role": role,
                 "observed_samples": len(groups[role]),
                 "required_min_samples": item["min_samples"]}
                for role in groups if len(groups[role]) < item["min_samples"]]
            comparisons = []
            if not coordinate_missing:
                naive_mean = safe_mean(run["metrics"]["capacity"].get("effective_unique")
                                       for run in groups["naive"])
                spill_mean = safe_mean(run["metrics"]["capacity"].get("effective_unique")
                                       for run in groups["spill"])
                ratio = safe_divide(spill_mean, naive_mean)
                spill_outer, ideal_outer = (pooled_outer_latency(groups[role])
                                             for role in ("spill", "ideal"))
                delta_ns = safe_subtract(spill_outer["mean_ns"], ideal_outer["mean_ns"])
                cycles = (delta_ns * thresholds["cycles_per_ns"]
                          if delta_ns is not None else None)
                passed = (ratio is not None and cycles is not None and
                          ratio >= thresholds["capacity_ratio_min"] and
                          cycles < thresholds["outer_delta_cycles_strict_max"])
                comparisons.append({"aggregation_id": "pooled", "repetition": None,
                                    "sample_counts": {role: len(rows)
                                                      for role, rows in groups.items()},
                                    "capacity_ratio": ratio, "outer_delta_ns": delta_ns,
                                    "outer_delta_cycles": cycles, "pass": passed})
            missing.extend([{**coordinate, **slot} for slot in coordinate_missing])
            results.append({"coordinate": coordinate, "missing_slots": coordinate_missing,
                            "comparisons": comparisons,
                            "status": ("INCOMPLETE" if coordinate_missing else
                                       "PASS" if comparisons and all(x["pass"] for x in comparisons)
                                       else "FAIL")})
    elif item["metric"] == 2:
        thresholds = item["thresholds"]
        for coordinate in item["coordinates"]:
            key0 = (coordinate["tc"], coordinate["topology"], coordinate["phase"],
                    coordinate["kind"], coordinate["reduction"],
                    tuple(coordinate["expected_nodes"]), coordinate["expected_count"])
            coordinate_runs = [run for run in selected if (
                run["tc"], run["topology"], run["metrics"].get("phase"),
                run["metrics"].get("kind", "latency"),
                run["metrics"].get("reduction", "aggregate"),
                tuple(run["metrics"].get("nodes", [])),
                run["metrics"].get("samples")) == key0]
            groups = {profile: [run for run in coordinate_runs
                                if run["profile"] == profile]
                      for profile in item["profiles"]}
            coordinate_missing = [
                {"kind": "minimum_samples", "dimension": "profile",
                 "profile": profile, "observed_samples": len(groups[profile]),
                 "required_min_samples": item["min_samples"]}
                for profile in groups if len(groups[profile]) < item["min_samples"]]
            cases = []
            if not coordinate_missing:
                baseline = safe_mean(run["metrics"].get("mean_ns")
                                     for run in groups[item["baseline_profile"]])
                result = safe_mean(run["metrics"].get("mean_ns")
                                   for run in groups[item["result_profile"]])
                reduction = safe_divide(safe_subtract(baseline, result), baseline)
                reduction = reduction * 100 if reduction is not None else None
                applicable = (finite_number(baseline) is not None and
                              baseline >= thresholds["baseline_applicable_min_ns"])
                cases.append({"aggregation_id": "pooled", "repetition": None,
                              "sample_counts": {profile: len(rows)
                                                for profile, rows in groups.items()},
                              "baseline_mean_ns": baseline,
                              "result_mean_ns": result, "reduction_pct": reduction,
                              "applicable": applicable,
                              "pass": bool(applicable and reduction is not None and
                                           reduction >= thresholds["reduction_pct_min"])})
            missing.extend([{**coordinate, **slot} for slot in coordinate_missing])
            results.append({"coordinate": coordinate, "missing_slots": coordinate_missing,
                            "cases": cases,
                            "status": ("INCOMPLETE" if coordinate_missing else
                                       "PASS" if cases and all(x["pass"] for x in cases)
                                       else "FAIL")})
    else:
        for topology in item["topologies"]:
            topology_runs = [run for run in selected if run["topology"] == topology]
            samples, topology_missing = [], []
            if item["mode"] == "paired":
                by = defaultdict(dict)
                for run in topology_runs:
                    if run.get("pair") is not None and run.get("order") is not None:
                        by[(run["pair"], run["tc"], run["order"])][run["arm"]] = run
                for pair in item["pairs"]:
                    for tc in item["testcases"]:
                        candidates = [(key, arms) for key, arms in by.items()
                                      if key[0] == pair and key[1] == tc]
                        if len(candidates) != 1 or set(candidates[0][1]) != set(item["arms"]):
                            topology_missing.append((pair, tc, "pair"))
                            continue
                        _, arms = candidates[0]
                        for name in M3[tc]:
                            delta = safe_subtract(arms["ha-vi"]["metrics"][name].get("ticks_per_operation"),
                                                  arms["ourcc"]["metrics"][name].get("ticks_per_operation"))
                            delta_ns = safe_subtract(arms["ha-vi"]["metrics"][name].get("ns_per_operation"),
                                                     arms["ourcc"]["metrics"][name].get("ns_per_operation"))
                            samples.append({"pair": pair, "tc": tc, "metric": name,
                                            "delta_ticks": delta, "delta_ns": delta_ns})
            else:
                for tc in item["testcases"]:
                    for arm in item["arms"]:
                        count = sum(run["tc"] == tc and run["arm"] == arm
                                    for run in topology_runs)
                        if count < item["min_samples"]:
                            topology_missing.append({
                                "kind": "minimum_samples", "tc": tc,
                                "arm": arm, "observed_samples": count,
                                "required_min_samples": item["min_samples"]})
                values = defaultdict(list)
                for run in topology_runs:
                    for name, metric in run["metrics"].items():
                        values[(run["tc"], name, run["arm"])].append({
                            "ticks": metric.get("ticks_per_operation"),
                            "ns": metric.get("ns_per_operation")})
                for tc in item["testcases"]:
                    for name in M3[tc]:
                        left, right = values[(tc, name, "ourcc")], values[(tc, name, "ha-vi")]
                        if left and right:
                            samples.append({"tc": tc, "metric": name,
                                            "delta_ticks": safe_subtract(
                                                safe_mean(item["ticks"] for item in right),
                                                safe_mean(item["ticks"] for item in left)),
                                            "delta_ns": safe_subtract(
                                                safe_mean(item["ns"] for item in right),
                                                safe_mean(item["ns"] for item in left))})
            summary_groups = defaultdict(list)
            for row in samples:
                summary_groups[row["tc"], row["metric"]].append(row)
            summaries = {(tc, metric): {"tc": tc, "metric": metric,
                                        "delta_ticks": safe_mean(item["delta_ticks"] for item in values),
                                        "delta_ns": safe_mean(item["delta_ns"] for item in values)}
                         for (tc, metric), values in summary_groups.items()}
            primary = []
            for tc in item["testcases"]:
                definition = metric3_primary_weights(tc, topology)
                if all((tc, name) in summaries for name in definition):
                    delta = safe_weighted_sum((weight, summaries[tc, name]["delta_ticks"])
                                              for name, weight in definition.items())
                    delta_ns = safe_weighted_sum((weight, summaries[tc, name]["delta_ns"])
                                                 for name, weight in definition.items())
                    primary.append({"tc": tc, "delta_mean_ticks": delta,
                                    "delta_mean_ns": delta_ns,
                                    "direction": metric3_direction(delta_ns)})
                else:
                    topology_missing.append((tc, "primary"))
            aggregates = []
            for aggregate_name, weights in metric3_aggregate_weights(topology).items():
                if all(key in summaries for key in weights):
                    delta = safe_weighted_sum(
                        (weight, summaries[key]["delta_ticks"])
                        for key, weight in weights.items())
                    delta_ns = safe_weighted_sum(
                        (weight, summaries[key]["delta_ns"])
                        for key, weight in weights.items())
                    aggregates.append({"name": aggregate_name, "delta_ticks": delta,
                                       "delta_ns": delta_ns,
                                       "direction": metric3_direction(delta_ns)})
            missing.extend([{"topology": topology, "slot": slot}
                            for slot in topology_missing])
            results.append({"topology": topology, "missing_slots": topology_missing,
                            "samples": samples, "primary_values": primary,
                            "aggregates": aggregates,
                            "status": "INCOMPLETE" if topology_missing else "PASS"})
    if registry_errors:
        status = "INVALID"
    elif any(row["status"] == "INCOMPLETE" for row in results):
        status = "INCOMPLETE"
    elif any(row["status"] == "FAIL" for row in results):
        status = "FAIL"
    elif results and all(row["status"] == "PASS" for row in results):
        status = "PASS"
    return {"id": item["id"], "metric": item["metric"], "status": status,
            "definition": item, "runs": len(selected), "missing_slots": missing,
            "results": results, "errors": registry_errors}


def _aggregate_parallel_task(task):
    kind, value = task
    context = _AGGREGATE_PARALLEL_CONTEXT
    if kind == "view":
        return descriptive_view(context[value])
    if kind == "inventory":
        return source_inventory(context["all"])
    if kind == "qualification":
        return aggregate_qualification(
            context["qualification_sets"][value], context["all"], context["issues"])
    raise ValueError(f"unknown aggregate parallel task {kind!r}")


def _init_aggregate_worker(context):
    global _AGGREGATE_PARALLEL_CONTEXT
    _AGGREGATE_PARALLEL_CONTEXT = context


def aggregate_results(data, resolved, ingestion_issues, output_dir=None,
                      manifest=None, ingestion=None, require_qualifications=None,
                      *, workers=1):
    """Apply the frozen formulas to already parsed, in-memory run records."""
    workers = normalize_workers(workers)
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
            issue = {"severity": "ERROR", "code": "DUPLICATE_SLOT",
                     "run_id": ",".join(row["id"] for row in rows),
                     "message": f"duplicate logical slot {key}"}
            qualification_ids = sorted({item for row in rows
                                        for item in row.get("qualified_contracts", [])})
            if qualification_ids:
                issue["qualification_contracts"] = qualification_ids
                issue["contract_class"] = "extension"
            issues.append(issue)
    resolved = [row for row in resolved if row["id"] not in bad_ids]
    all_resolved = list(resolved)
    standard_resolved = [row for row in all_resolved if row.get("standard_contract", True)]
    formal_resolved = [row for row in all_resolved if row.get("formal_contract",
                                                               row.get("standard_contract", True))]
    extension_resolved = [row for row in all_resolved
                          if not row.get("standard_contract", True) and
                          not row.get("qualified_contracts")]
    resolved = standard_resolved

    matrix, per_run = [], []
    for run in resolved:
        value = finite_number(run["metrics"].get(
            "mean_ns_per_operation", run["metrics"].get("mean_ns")))
        per_run.append({"run_id": run["id"], "metric": run["metric"], "tc": run["tc"],
                        "repetition": run["repetition"], "profile": run.get("profile", ""),
                        "arm": run.get("arm", ""), "pair": run.get("pair", ""), "order": run.get("order", ""),
                        "value": value if value is not None else "MULTI", "unit": "ns/op" if value is not None else "multiple",
                        "status": run["status"]})
        matrix.append({"metric": f"Metric{run['metric']}", "level": "run", "identity": run["id"],
                       "tc": f"TC{run['tc']}", "value": value, "unit": "ns/op" if value is not None else "multiple",
                       "status": run["status"], "detail": run.get("profile", run.get("arm", ""))})

    # Metric 1 pools independent samples by role; repetition is audit-only.
    m1 = [r for r in resolved if r["metric"] == 1]
    m1_req = requirement(data, "metric1", {
        "min_samples": 1, "roles": ["naive", "spill", "ideal"],
        "ideal_min_capacity": 102656})
    m1_min_samples = minimum_samples(m1_req)
    m1_roles = [norm_metric1_role(x) for x in m1_req.get("roles", ["naive", "spill", "ideal"])]
    m1_groups = {role: [run for run in m1 if run.get("metric1_role") == role]
                 for role in m1_roles}
    m1_counts = {role: len(rows) for role, rows in m1_groups.items()}
    m1_missing = [{"kind": "minimum_samples", "dimension": "role",
                   "role": role, "observed_samples": m1_counts[role],
                   "required_min_samples": m1_min_samples}
                  for role in m1_roles if m1_counts[role] < m1_min_samples]
    m1_comp = []
    if not m1_missing and all(role in m1_groups for role in ("naive", "spill", "ideal")):
        capacity_means = {role: safe_mean(
            finite_number(run["metrics"]["capacity"].get("effective_unique"))
            for run in m1_groups[role]) for role in ("naive", "spill")}
        ratio = safe_divide(capacity_means["spill"], capacity_means["naive"])
        spill_outer = pooled_outer_latency(m1_groups["spill"])
        ideal_outer = pooled_outer_latency(m1_groups["ideal"])
        delta_ns = safe_subtract(spill_outer["mean_ns"], ideal_outer["mean_ns"])
        if ratio is None or delta_ns is None:
            m1_missing.append({"kind": "required_metrics",
                               "message": "pooled capacity or Outer metric unavailable"})
        else:
            capacity_pass, latency_pass = ratio >= 1.5, delta_ns * 2.0 < 50
            row = {"aggregation_id": "pooled", "repetition": None,
                   "sample_counts": m1_counts, "capacity_role_means": capacity_means,
                   "capacity_ratio": ratio,
                   "spill_outer_samples": spill_outer["samples"],
                   "ideal_outer_samples": ideal_outer["samples"],
                   "spill_outer_mean_ns": spill_outer["mean_ns"],
                   "ideal_outer_mean_ns": ideal_outer["mean_ns"],
                   "outer_delta_ns": delta_ns, "outer_delta_cycles": delta_ns * 2.0,
                   "capacity_pass": capacity_pass, "latency_pass": latency_pass,
                   "pass": capacity_pass and latency_pass}
            m1_comp.append(row)
            matrix.append({"metric": "Metric1", "level": "pooled", "identity": "all samples", "tc": "TC131",
                           "value": ratio, "unit": "capacity-ratio",
                           "status": "PASS" if row["pass"] else "FAIL",
                           "detail": f"outer_delta_ns={delta_ns:.9g}; roles=naive/spill/ideal"})
    ratios = [row["capacity_ratio"] for row in m1_comp]
    deltas = [row["outer_delta_ns"] for row in m1_comp]
    def distribution(values):
        values = [value for value in values if finite_number(value) is not None]
        mean = safe_mean(values)
        stdev = safe_stdev(values)
        return {"count": len(values), "mean": mean, "stdev": stdev,
                "cv": safe_divide(stdev, abs(mean) if mean is not None else None)}
    m1_status = ("NOT_REQUESTED" if not m1 else
                  "INCOMPLETE" if m1_missing else
                  "PASS" if all(r["pass"] for r in m1_comp) else "FAIL")

    # Metric 2 pools independent runs by TC/profile, then equal-weights TCs.
    m2 = [r for r in resolved if r["metric"] == 2]
    m2_req = requirement(data, "metric2", {"min_samples": 1,
                                             "testcases": sorted({r["tc"] for r in m2})})
    m2_min_samples = minimum_samples(m2_req)
    requested_m2_tcs = integer_requirement_list(m2_req, "metric2")
    m2_tcs = sorted(M2) if requested_m2_tcs or m2 else []
    m2_official_set = not m2_tcs or set(m2_tcs) == set(M2)
    m2_groups = {(tc, profile): [run for run in m2
                                 if run["tc"] == tc and run["profile"] == profile]
                 for tc in m2_tcs for profile in PROFILES}
    m2_counts = {str(tc): {profile: len(m2_groups[tc, profile]) for profile in PROFILES}
                 for tc in m2_tcs}
    m2_missing = [{"kind": "minimum_samples", "dimension": "profile",
                   "tc": tc, "profile": profile,
                   "observed_samples": len(m2_groups[tc, profile]),
                   "required_min_samples": m2_min_samples}
                  for tc in m2_tcs for profile in PROFILES
                  if len(m2_groups[tc, profile]) < m2_min_samples]
    m2_cases, applicable_values = [], []
    for tc in m2_tcs:
        if any(len(m2_groups[tc, profile]) < m2_min_samples for profile in PROFILES):
            continue
        means = {profile: safe_mean(
            finite_number(run["metrics"].get("mean_ns"))
            for run in m2_groups[tc, profile]) for profile in PROFILES}
        reduction = safe_divide(safe_subtract(means["naive"], means["optimized"]), means["naive"])
        if reduction is None:
            m2_missing.append({"kind": "required_metrics", "tc": tc,
                               "message": "pooled profile mean unavailable"})
            m2_cases.append({"aggregation_id": "pooled", "repetition": None,
                             "tc": tc, "means_ns": means,
                             "sample_counts": m2_counts[str(tc)],
                             "optimized_reduction_pct": None,
                             "applicable": None, "status": "INCOMPLETE",
                             "reason": "pooled profile mean unavailable"})
            matrix.append({"metric": "Metric2", "level": "TC",
                           "identity": "all samples", "tc": f"TC{tc}",
                           "value": None, "unit": "%", "status": "INCOMPLETE",
                           "detail": "pooled profile mean unavailable"})
            continue
        reduction *= 100
        applicable = means["naive"] >= 500
        row = {"aggregation_id": "pooled", "repetition": None, "tc": tc,
               "means_ns": means, "sample_counts": m2_counts[str(tc)],
               "optimized_reduction_pct": reduction, "applicable": applicable}
        m2_cases.append(row)
        if applicable:
            applicable_values.append(reduction)
        matrix.append({"metric": "Metric2", "level": "TC", "identity": "all samples",
                       "tc": f"TC{tc}", "value": reduction, "unit": "%",
                       "status": "APPLICABLE" if applicable else "NOT_APPLICABLE",
                       "detail": f"pooled naive_ns={means['naive']:.9g}"})
    m2_value = safe_mean(applicable_values)
    empty_applicable = bool(m2_tcs and not applicable_values)
    m2_status = ("NOT_REQUESTED" if not m2_tcs and not m2 else
                 "INCOMPLETE" if m2_missing or not m2_tcs or not m2_official_set else
                 "FAIL" if empty_applicable or m2_value is None or m2_value < 10 else "PASS")
    if m2_value is not None:
        matrix.append({"metric": "Metric2", "level": "aggregate", "identity": "applicable-case equal weight",
                       "tc": "ALL", "value": m2_value, "unit": "%", "status": m2_status, "detail": "no sample weighting"})

    # Metric 3 supports independent repeats by default and strict legacy pairing.
    m3 = [r for r in resolved if r["metric"] == 3]
    default_req = {"min_samples": 1, "testcases": sorted({r["tc"] for r in m3})}
    m3_req = requirement(data, "metric3", default_req)
    paired_mode = (m3_req.get("mode") == "paired" or
                   ("mode" not in m3_req and bool(m3_req.get("pairs"))))
    comparison_mode = "paired" if paired_mode else "independent"
    requested_m3_tcs = integer_requirement_list(m3_req, "metric3")
    tcs_req = sorted(M3) if requested_m3_tcs or m3 else []
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
                    left_ns = finite_number(left.get("ns_per_operation"))
                    right_ns = finite_number(right.get("ns_per_operation"))
                    delta = safe_subtract(right_value, left_value)
                    delta_ns = safe_subtract(right_ns, left_ns)
                    if delta is None or delta_ns is None:
                        m3_metric_incomplete = True
                        missing_m3.append((pair, tc, name, "required_metrics"))
                        continue
                    samples.append({"pair": pair, "tc": tc, "order": order, "metric": name,
                                    "ourcc_ticks": left_value,
                                    "ha_vi_ticks": right_value,
                                    "delta_ticks": delta,
                                    "ourcc_ns": left_ns, "ha_vi_ns": right_ns,
                                    "delta_ns": delta_ns,
                                    "frequency_hz": left["counter_frequency_hz"]})
                    matrix.append({"metric": "Metric3", "level": "pair", "identity": pair,
                                   "tc": f"TC{tc}", "value": delta_ns, "unit": f"ns/op:{name}",
                                   "status": metric3_direction(delta_ns),
                                   "detail": f"order={order}; delta=HA-VI-OurCC"})
        summaries = []
        for key in sorted({(r["tc"], r["metric"]) for r in samples}):
            rows = [r for r in samples if (r["tc"], r["metric"]) == key]
            summaries.append({"tc": key[0], "metric": key[1], "pairs": len(rows),
                              "ourcc_mean_ticks": safe_mean(r["ourcc_ticks"] for r in rows),
                              "ha_vi_mean_ticks": safe_mean(r["ha_vi_ticks"] for r in rows),
                              "delta_mean_ticks": safe_mean(r["delta_ticks"] for r in rows),
                              "ourcc_mean_ns": safe_mean(r["ourcc_ns"] for r in rows),
                              "ha_vi_mean_ns": safe_mean(r["ha_vi_ns"] for r in rows),
                              "delta_mean_ns": safe_mean(r["delta_ns"] for r in rows)})
        m3_complete = (bool(pairs_req and tcs_req) and not incomplete_pairs and
                       not conflicting_orders and not m3_metric_incomplete and
                       set(tcs_req) == set(M3))
    else:
        min_repetitions = minimum_samples(m3_req)
        arms_req = [norm_arm(x) for x in m3_req.get("arms", ["ourcc", "ha-vi"])]
        values = defaultdict(list)
        for run in m3:
            for name, metric in run["metrics"].items():
                tick_value = finite_number(metric.get("ticks_per_operation"))
                ns_value = finite_number(metric.get("ns_per_operation"))
                if tick_value is None or ns_value is None:
                    m3_metric_incomplete = True
                    missing_m3.append((run["repetition"], run["tc"], run["arm"],
                                       name, "required_metrics"))
                    continue
                values[(run["tc"], name, run["arm"])].append({
                    "ticks": tick_value, "ns": ns_value})
                samples.append({"run_id": run["id"], "repetition": run["repetition"],
                                "tc": run["tc"], "arm": run["arm"], "metric": name,
                                "ticks_per_operation": tick_value,
                                "ns_per_operation": ns_value,
                                "frequency_hz": metric["counter_frequency_hz"]})
        summaries = []
        for tc, name in sorted({key[:2] for key in values}):
            arm_stats = {}
            for arm in ("ourcc", "ha-vi"):
                rows = values.get((tc, name, arm), [])
                if rows:
                    arm_stats[arm] = {"count": len(rows),
                                      "mean_ticks": safe_mean(item["ticks"] for item in rows),
                                      "stdev_ticks": safe_stdev(item["ticks"] for item in rows),
                                      "mean_ns": safe_mean(item["ns"] for item in rows),
                                      "stdev_ns": safe_stdev(item["ns"] for item in rows)}
            if set(arm_stats) == {"ourcc", "ha-vi"}:
                summaries.append({"tc": tc, "metric": name,
                                  "ourcc_count": arm_stats["ourcc"]["count"],
                                  "ha_vi_count": arm_stats["ha-vi"]["count"],
                                  "ourcc_stdev_ticks": arm_stats["ourcc"]["stdev_ticks"],
                                  "ha_vi_stdev_ticks": arm_stats["ha-vi"]["stdev_ticks"],
                                  "ourcc_mean_ticks": arm_stats["ourcc"]["mean_ticks"],
                                  "ha_vi_mean_ticks": arm_stats["ha-vi"]["mean_ticks"],
                                  "ourcc_mean_ns": arm_stats["ourcc"]["mean_ns"],
                                  "ha_vi_mean_ns": arm_stats["ha-vi"]["mean_ns"],
                                  "ourcc_stdev_ns": arm_stats["ourcc"]["stdev_ns"],
                                  "ha_vi_stdev_ns": arm_stats["ha-vi"]["stdev_ns"],
                                  "delta_mean_ticks": safe_subtract(
                                      arm_stats["ha-vi"]["mean_ticks"],
                                      arm_stats["ourcc"]["mean_ticks"]),
                                  "delta_mean_ns": safe_subtract(
                                      arm_stats["ha-vi"]["mean_ns"],
                                      arm_stats["ourcc"]["mean_ns"])})
        counts_by_tc_arm = {str(tc): {arm: sum(r["tc"] == tc and r["arm"] == arm for r in m3)
                                      for arm in arms_req} for tc in tcs_req}
        insufficient = [
            {"kind": "minimum_samples", "tc": tc, "arm": arm,
             "observed_samples": counts_by_tc_arm[str(tc)][arm],
             "required_min_samples": min_repetitions}
            for tc in tcs_req for arm in arms_req
            if min_repetitions and counts_by_tc_arm[str(tc)][arm] < min_repetitions]
        imbalanced = [{"tc": tc, "counts": counts_by_tc_arm[str(tc)]} for tc in tcs_req
                      if len(set(counts_by_tc_arm[str(tc)].values())) > 1]
        if imbalanced:
            issues.append(warning("M3_ARM_COUNT_IMBALANCE", "metric3",
                                  f"independent arm counts are imbalanced: {imbalanced}"))
        missing_m3.extend(insufficient)
        m3_complete = (bool(tcs_req) and not missing_m3 and
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
            ourcc_ns = safe_weighted_sum((weight, by_summary[tc, name].get("ourcc_mean_ns"))
                                         for name, weight in definition.items())
            havi_ns = safe_weighted_sum((weight, by_summary[tc, name].get("ha_vi_mean_ns"))
                                        for name, weight in definition.items())
            delta = safe_subtract(havi, ourcc)
            delta_ns = safe_subtract(havi_ns, ourcc_ns)
            if delta is None or delta_ns is None:
                m3_complete = False
                missing_m3.append((tc, "primary_required_metrics"))
                continue
            primary.append({"tc": tc, "ourcc_mean_ticks": ourcc,
                            "ha_vi_mean_ticks": havi, "delta_mean_ticks": delta,
                            "ourcc_mean_ns": ourcc_ns, "ha_vi_mean_ns": havi_ns,
                            "delta_mean_ns": delta_ns,
                            "direction": metric3_direction(delta_ns)})
            matrix.append({"metric": "Metric3", "level": "TC", "identity": f"{comparison_mode} arm mean", "tc": f"TC{tc}",
                           "value": delta_ns, "unit": "ns/op", "status": metric3_direction(delta_ns),
                           "detail": "frozen primary-value formula"})
    aggregates = []
    for name, weights in M3_AGGREGATES.items():
        if all(key in by_summary for key in weights):
            delta = safe_weighted_sum((weight, by_summary[key].get("delta_mean_ticks"))
                                      for key, weight in weights.items())
            delta_ns = safe_weighted_sum((weight, by_summary[key].get("delta_mean_ns"))
                                         for key, weight in weights.items())
            if delta is None or delta_ns is None:
                m3_complete = False
                missing_m3.append((name, "aggregate_required_metrics"))
                continue
            aggregates.append({"name": name, "delta_ticks": delta,
                               "delta_ns": delta_ns,
                               "direction": metric3_direction(delta_ns),
                               "status": "COMPLETE"})
            matrix.append({"metric": "Metric3", "level": "aggregate", "identity": name, "tc": "ALL",
                           "value": delta_ns, "unit": "ns/op", "status": aggregates[-1]["status"],
                           "detail": f"frozen weights; direction={aggregates[-1]['direction']}"})
    m3_requested = bool(tcs_req or m3)
    m3_status = ("NOT_REQUESTED" if not m3_requested else
                 "INCOMPLETE" if not m3_complete else "COMPLETE")

    qualification_sets = normalize_qualification_sets(data.get("requirements", {}))
    parallel_tasks = [("view", "formal"), ("view", "all"),
                      ("view", "extension"), ("inventory", None)]
    parallel_tasks.extend(("qualification", index)
                          for index in range(len(qualification_sets)))
    if workers > 1 and len(parallel_tasks) > 1:
        parallel_context = {
            "formal": formal_resolved, "all": all_resolved,
            "extension": extension_resolved, "issues": issues,
            "qualification_sets": qualification_sets}
        with process_pool(min(workers, len(parallel_tasks)),
                          _init_aggregate_worker, (parallel_context,)) as pool:
            parallel_results = list(pool.map(_aggregate_parallel_task, parallel_tasks))
        formal_view, all_view, extension_view, inventory = parallel_results[:4]
        qualifications = parallel_results[4:]
    else:
        formal_view = descriptive_view(formal_resolved)
        all_view = descriptive_view(all_resolved)
        extension_view = descriptive_view(extension_resolved)
        inventory = source_inventory(all_resolved)
        qualifications = [aggregate_qualification(item, all_resolved, issues)
                          for item in qualification_sets]
    has_errors = any(i["severity"] == "ERROR" and
                      i.get("contract_class", "standard") == "standard"
                     for i in issues)
    incomplete = m1_status == "INCOMPLETE" or m2_status == "INCOMPLETE" or m3_status == "INCOMPLETE"
    failed = m1_status == "FAIL" or m2_status == "FAIL" or m3_status.startswith("FAIL")
    overall, code = (("INVALID", 2) if has_errors else ("INCOMPLETE", 3) if incomplete else ("FAIL", 1) if failed else ("PASS", 0))
    report = {"schema_version": 1, "manifest": str(manifest) if manifest is not None else None,
              "correctness_policy": data.get("correctness_policy", "strict"),
              "overall_status": overall, "exit_code": code,
              "metric1": {"status": m1_status, "aggregation_mode": "pooled-samples",
                            "min_samples": m1_min_samples, "sample_counts": m1_counts,
                            "coverage": [{"role": role,
                                          "observed_samples": m1_counts[role],
                                          "required_min_samples": m1_min_samples,
                                          "complete": m1_counts[role] >= m1_min_samples}
                                         for role in m1_roles],
                            "definition": {
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
                                          "pass_policy": "single pooled estimate passes"}},
              "metric2": {"status": m2_status, "aggregation_mode": "pooled-samples",
                            "min_samples": m2_min_samples,
                            "sample_counts_by_tc_profile": m2_counts,
                            "coverage": [{"tc": tc, "profile": profile,
                                          "observed_samples": m2_counts[str(tc)][profile],
                                          "required_min_samples": m2_min_samples,
                                          "complete": m2_counts[str(tc)][profile] >= m2_min_samples}
                                         for tc in m2_tcs for profile in PROFILES],
                            "missing_slots": m2_missing, "cases": m2_cases,
                            "repetition_equal_weight": [],
                            "applicable_cases_by_repetition": {},
                            "applicable_set_stable": None,
                            "official_testcase_set_complete": m2_official_set,
                            "repetitions_without_applicable_cases": [],
                            "aggregate_reduction_pct": m2_value},
               "metric3": {"status": m3_status, "aggregation_mode": (
                                "paired" if paired_mode else "pooled-samples"),
                            "min_samples": None if paired_mode else min_repetitions,
                            "coverage": ([] if paired_mode else [
                                {"tc": tc, "arm": arm,
                                 "observed_samples": counts_by_tc_arm[str(tc)][arm],
                                 "required_min_samples": min_repetitions,
                                 "complete": counts_by_tc_arm[str(tc)][arm] >= min_repetitions}
                                for tc in tcs_req for arm in arms_req]),
                            "executable_reference_model": True,
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
                            "formal": formal_view,
                            "all": all_view,
                            "extension": extension_view},
               "qualifications": qualifications,
               "source_inventory": inventory,
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
    required_ids = list(require_qualifications or [])
    if required_ids:
        by_id = {item["id"]: item for item in qualifications}
        required_statuses = {item_id: (by_id[item_id]["status"] if item_id in by_id
                                      else "UNREGISTERED") for item_id in required_ids}
        report["required_qualifications"] = required_statuses
        if any(status != "PASS" for status in required_statuses.values()):
            rank = {0: 0, 1: 1, 3: 2, 2: 3}
            qcode = (2 if any(status in ("INVALID", "UNREGISTERED")
                              for status in required_statuses.values()) else
                     3 if any(status == "INCOMPLETE" for status in required_statuses.values()) else 1)
            code = max((code, qcode), key=lambda value: rank[value])
            report["exit_code"] = code
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
                        "unit": "ns/op" if value is not None else "multiple",
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

    _PICKLE_STATE_VERSION = 2

    def __init__(self, requirements=None, correctness_policy="strict", base_dir=None):
        if correctness_policy not in ("strict", "required", "optional"):
            raise ExtractError("correctness_policy must be strict|required|optional")
        if requirements is not None and not isinstance(requirements, dict):
            raise ExtractError("requirements must be a mapping or None")
        self.correctness_policy = correctness_policy
        self.base_dir = pathlib.Path(base_dir or ".").expanduser().resolve()
        self._explicit_requirements = requirements is not None
        self._requirements = json.loads(json.dumps(requirements or {}))
        self._qualification_sets = normalize_qualification_sets(self._requirements)
        self._inferred = {"metric1": {"repetitions": set(),
                                       "min_samples": 1,
                                       "roles": {"naive", "spill", "ideal"},
                                       "ideal_min_capacity": 102656},
                          "metric2": {"repetitions": set(), "min_samples": 1,
                                      "testcases": set()},
                          "metric3": {"mode": "independent", "repetitions": set(),
                                      "min_repetitions": 1,
                                      "testcases": set(), "arms": {"ourcc", "ha-vi"}}}
        self._resolved = []
        self._issues = []
        self._ids = set()
        self._add_results = []
        self._slot_ids = defaultdict(list)

    def __getstate__(self):
        """Return a versioned, path-independent snapshot for ``pickle``."""
        return {"version": self._PICKLE_STATE_VERSION,
                "correctness_policy": self.correctness_policy,
                "base_dir": str(self.base_dir),
                "explicit_requirements": self._explicit_requirements,
                "requirements": copy.deepcopy(self._requirements),
                "qualification_sets": copy.deepcopy(self._qualification_sets),
                "inferred": copy.deepcopy(self._inferred),
                "resolved": copy.deepcopy(self._resolved),
                "issues": copy.deepcopy(self._issues),
                "ids": set(self._ids),
                "add_results": copy.deepcopy(self._add_results)}

    def __setstate__(self, state):
        """Restore a snapshot without reopening any source log path."""
        if not isinstance(state, dict) or state.get("version") not in (1, 2):
            raise ValueError("unsupported Metric123RawLogMatrix pickle state")
        policy = state.get("correctness_policy")
        if policy not in ("strict", "required", "optional"):
            raise ValueError("invalid correctness policy in Metric123RawLogMatrix pickle")
        self.correctness_policy = policy
        self.base_dir = pathlib.Path(state["base_dir"]).expanduser().resolve()
        self._explicit_requirements = bool(state["explicit_requirements"])
        self._requirements = copy.deepcopy(state["requirements"])
        # v1 had no compiled registry.  Rebuild exclusively from the snapshot's
        # requirements; never consult source paths during unpickling.
        self._qualification_sets = normalize_qualification_sets(self._requirements)
        self._inferred = copy.deepcopy(state["inferred"])
        self._resolved = copy.deepcopy(state["resolved"])
        self._issues = copy.deepcopy(state["issues"])
        self._ids = set(state["ids"])
        self._add_results = copy.deepcopy(state["add_results"])
        self._sanitize_inferred_metric2()
        self._sanitize_inferred_metric3()
        self._slot_ids = defaultdict(list)
        for row in self._resolved:
            self._slot_ids[logical_slot(row)].append(row["id"])

    def _sanitize_inferred_metric2(self):
        """Keep inferred standard coverage separate from Metric2 extensions."""
        if self._explicit_requirements:
            return
        inferred = self._inferred.setdefault("metric2", {})
        old_testcases = {int(value) for value in inferred.get("testcases", set())
                         if str(value).lstrip("-").isdigit()}
        repetitions = set()
        testcases = old_testcases & set(M2)
        for row in self._resolved:
            if row.get("metric") == 2 and row.get("standard_contract"):
                repetitions.add(row["repetition"])
                testcases.update(M2)
        removed = sorted(old_testcases - testcases)
        inferred.update({"repetitions": set(), "min_samples": max(
            1, int(inferred.get("min_samples", 1) or 1)), "testcases": testcases})
        if removed:
            item = warning(
                "LEGACY_METRIC2_INFERENCE_REPAIRED", "matrix",
                "removed nonstandard testcases from inferred standard Metric2 "
                f"requirements: {removed}", removed_testcases=removed)
            if item not in self._issues:
                self._issues.append(item)

    def _sanitize_inferred_metric3(self):
        """Migrate Metric3 inference polluted by the pre-05b3446 slot bug."""
        if self._explicit_requirements:
            return
        inferred = self._inferred.setdefault("metric3", {})
        raw_testcases = inferred.get("testcases", set())
        if not isinstance(raw_testcases, (set, list, tuple)):
            raw_testcases = [raw_testcases]
        testcases, invalid = set(), []
        for value in raw_testcases:
            try:
                testcases.add(int(value))
            except (TypeError, ValueError):
                invalid.append(value)
        arms = set(inferred.get("arms", ("ourcc", "ha-vi")))
        standard_present = False
        for row in self._resolved:
            if row.get("metric") == 3 and row.get("standard_contract"):
                standard_present = True
                arms.add(row["arm"])
        testcases = set(M3) if standard_present or testcases & set(M3) else set()
        inferred.update({"mode": inferred.get("mode", "independent"),
                         "repetitions": set(), "min_repetitions": max(
                             1, int(inferred.get("min_repetitions", 1) or 1)),
                         "testcases": testcases, "arms": arms})
        if invalid:
            item = warning(
                "LEGACY_METRIC3_INFERENCE_REPAIRED", "matrix",
                "removed non-integer values from inferred Metric3 testcases "
                f"created by the legacy slot-index bug: {sorted(map(str, invalid))}",
                removed_values=sorted(map(str, invalid)))
            if item not in self._issues:
                self._issues.append(item)

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
            self._inferred["metric1"]["repetitions"].clear()
        elif slot[0] == 2:
            self._inferred["metric2"]["repetitions"].clear()
            self._inferred["metric2"]["testcases"].update(M2)
        else:
            # _identity() uses (3, repetition, tc, arm), while logical_slot()
            # includes the comparison mode before repetition. Metric3 inference
            # is updated from normalized raw/parsed fields at the call sites.
            return

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
        if raw.get("repetition") in (None, ""):
            raw["repetition"] = effective_id
            raw["repetition_source"] = "auto-run-id"
            raw.setdefault("_contract_warnings", []).append(warning(
                "REPETITION_AUTO_ASSIGNED", effective_id,
                f"sample identity auto-assigned from run id {effective_id!r}"))
        else:
            raw.setdefault("repetition_source", "explicit-audit-only")
        raw["_qualification_candidates"] = qualification_candidates(
            raw, self._qualification_sets)
        if int(raw.get("metric", 0) or 0) == 1 and self._explicit_requirements:
            standard_coordinate = (int(raw.get("tc", 0)) == 131 and
                                   str(raw.get("topology", "")).lower() == "8n1s" and
                                   int(raw.get("home_node", 0)) == 0 and
                                   int(raw.get("home_socket", 0)) == 0)
            m1_candidates = [item for item in raw["_qualification_candidates"]
                             if item["metric"] == 1]
            if standard_coordinate:
                raw.setdefault("ideal_min_capacity", int(
                    self._requirements.get("metric1", {}).get("ideal_min_capacity", 102656)))
            elif m1_candidates:
                raw.setdefault("ideal_min_capacity", min(
                    item["ideal_min_capacity"] for item in m1_candidates))
        if int(raw.get("metric", 0) or 0) == 3:
            req = self._requirements.get("metric3", {}) if self._explicit_requirements else {}
            raw["comparison_mode"] = ("paired" if req.get("mode") == "paired" or
                                      ("mode" not in req and bool(req.get("pairs"))) else
                                      "independent")
        normalization_error = None
        legacy_identity = None
        try:
            raw = normalize_legacy_metric1_profile(raw)
        except (ExtractError, TypeError, ValueError) as error:
            normalization_error = error
            try:
                if (int(raw.get("metric", 0) or 0) == 1 and
                        str(raw.get("profile", "")).lower() in
                        ("ideal", "ideal-dir", "infinite")):
                    legacy_identity = (1, str(raw["repetition"]), int(raw["tc"]),
                                       "ideal", "spill-noopt")
            except (KeyError, TypeError, ValueError):
                pass
        run_id, slot = self._identity(raw)
        slot = slot or legacy_identity
        official_candidate = self._official_requirement_candidate(raw, slot)
        if official_candidate:
            self._infer(slot)
            if slot[0] == 3:
                self._inferred["metric3"]["testcases"].add(int(raw["tc"]))
                try:
                    self._inferred["metric3"]["arms"].add(norm_arm(raw["arm"]))
                except (KeyError, ExtractError):
                    pass
        fallback_id = run_id
        result = {"status": "REJECTED", "requested_id": requested_id,
                  "run_id": fallback_id, "slot": list(slot) if slot else None}
        self._ids.add(run_id)
        if rename_warning:
            raw.setdefault("_contract_warnings", []).append(rename_warning)
        try:
            if normalization_error is not None:
                raise normalization_error
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
            if raw.get("_qualification_candidates"):
                issue["qualification_contracts"] = sorted(
                    {item["id"] for item in raw["_qualification_candidates"]})
            result["issue"] = issue
            self._issues.append(issue)
            self._add_results.append(result)
            return json.loads(json.dumps(result))
        parsed_slot = logical_slot(parsed)
        if (not self._explicit_requirements and parsed["metric"] == 1 and
                parsed.get("standard_contract")):
            self._inferred["metric1"]["repetitions"].clear()
        if not self._explicit_requirements and parsed["metric"] == 3:
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
        left_sets = {item["id"]: item for item in normalize_qualification_sets(left)}
        right_sets = {item["id"]: item for item in normalize_qualification_sets(right)}
        for item_id in set(left_sets) & set(right_sets):
            if left_sets[item_id] != right_sets[item_id]:
                raise ExtractError(f"conflicting qualification definition for id {item_id!r}")
        result = {}
        for metric in sorted((set(left) | set(right)) - {"qualification_sets"}):
            result[metric] = {}
            lfields, rfields = left.get(metric, {}), right.get(metric, {})
            for field in sorted(set(lfields) | set(rfields)):
                lv, rv = lfields.get(field, []), rfields.get(field, [])
                if isinstance(lv, list) and isinstance(rv, list):
                    result[metric][field] = sorted(set(lv) | set(rv), key=str)
                else:
                    result[metric][field] = copy.deepcopy(rv if field in rfields else lv)
        result["qualification_sets"] = [copy.deepcopy(
            (right_sets.get(item_id) or left_sets[item_id]))
            for item_id in sorted(set(left_sets) | set(right_sets))]
        return result

    def __add__(self, other):
        if not isinstance(other, Metric123RawLogMatrix):
            return NotImplemented
        return merge((self, other))

    def _data(self):
        if self._explicit_requirements:
            requirements = json.loads(json.dumps(self._requirements))
        else:
            self._sanitize_inferred_metric2()
            self._sanitize_inferred_metric3()
            requirements = {}
            for name, fields in self._inferred.items():
                requirements[name] = {key: (sorted(values, key=str)
                                             if isinstance(values, set) else values)
                                      for key, values in fields.items()}
            requirements["metric1"]["repetitions"] = []
            requirements["metric1"]["min_samples"] = max(
                1, int(requirements["metric1"].get("min_samples", 1)))
            requirements["metric2"]["repetitions"] = []
            requirements["metric2"]["min_samples"] = max(
                1, int(requirements["metric2"].get("min_samples", 1)))
            requirements["metric3"]["repetitions"] = []
            requirements["metric3"]["min_repetitions"] = max(
                1, int(requirements["metric3"].get("min_repetitions", 1)))
        return {"schema_version": 1, "correctness_policy": self.correctness_policy,
                "requirements": requirements}

    def finalize(self, output_dir=None, require_qualifications=None, *, workers=1):
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
                                   output, ingestion=ingestion,
                                   require_qualifications=require_qualifications,
                                   workers=workers)
        report, resolved, matrix, per_run, issues, code = values
        if output is not None:
            write_outputs(output, report, resolved, matrix, per_run, issues)
        return {"report": report, "resolved_runs": resolved, "matrix": matrix,
                "matrices": {"standard": matrix,
                              "formal": report["views"]["formal"]["matrix"],
                              "all": report["views"]["all"]["matrix"],
                             "extension": report["views"]["extension"]["matrix"]},
                "per_run_metrics": per_run, "issues": issues, "exit_code": code}


def _merge_snapshot_fingerprint(record):
    """Fingerprint source evidence while excluding registry-derived labels."""
    value = dict(record)
    value.pop("qualified_contracts", None)
    value.pop("formal_contract", None)
    if isinstance(value.get("correctness"), dict):
        value["correctness"] = dict(value["correctness"])
        value["correctness"].pop("policy", None)
        value["correctness"].pop("required", None)
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _merge_fingerprint_digest(record):
    canonical = _merge_snapshot_fingerprint(record)
    return hashlib.sha256(canonical.encode("utf-8")).digest()


def _merge_fingerprint_batch(indexes):
    return [(index, _merge_fingerprint_digest(_MERGE_FINGERPRINT_RECORDS[index]))
            for index in indexes]


def _init_merge_worker(records):
    global _MERGE_FINGERPRINT_RECORDS
    _MERGE_FINGERPRINT_RECORDS = records


def merge(matrices, *, workers=1):
    """Merge Matrix snapshots in one pass without reopening source logs.

    Input order is preserved for scalar requirement precedence and deterministic
    ID renaming.  Empty iterables are rejected; a single input returns an
    isolated clone.
    """
    workers = normalize_workers(workers)
    iterator = iter(matrices)
    try:
        first = next(iterator)
    except StopIteration as error:
        raise ValueError("merge() requires at least one Metric123RawLogMatrix") from error
    if not isinstance(first, Metric123RawLogMatrix):
        raise TypeError("merge() item 0 is not a Metric123RawLogMatrix")
    sources = [first]
    for index, item in enumerate(iterator, 1):
        if not isinstance(item, Metric123RawLogMatrix):
            raise TypeError(f"merge() item {index} is not a Metric123RawLogMatrix")
        sources.append(item)

    if len(sources) == 1:
        cloned = object.__new__(Metric123RawLogMatrix)
        cloned.__setstate__(first.__getstate__())
        return cloned

    requirement_fields = {}
    qualification_sets = {}
    inferred_requirement_issues = []
    for source_index, source in enumerate(sources):
        source_requirements = source._data().get("requirements", {})
        for item in source._qualification_sets:
            previous = qualification_sets.get(item["id"])
            if previous is not None and previous != item:
                raise ExtractError(
                    f"conflicting qualification definition for id {item['id']!r}")
            qualification_sets[item["id"]] = item
        for metric, fields in source_requirements.items():
            if metric == "qualification_sets":
                continue
            target = requirement_fields.setdefault(metric, {})
            for field, value in fields.items():
                if isinstance(value, list):
                    if field == "testcases":
                        normalized = []
                        for value_index, item in enumerate(value):
                            try:
                                normalized.append(int(item))
                            except (TypeError, ValueError) as error:
                                path = (f"merge() item {source_index} requirements."
                                        f"{metric}.{field}[{value_index}]")
                                if source._explicit_requirements:
                                    raise ExtractError(
                                        f"{path} must be an integer, got {item!r}") from error
                                inferred_requirement_issues.append(warning(
                                    "INVALID_INFERRED_REQUIREMENT_IGNORED", "merge",
                                    f"{path} ignored because it is not an integer: {item!r}"))
                        value = normalized
                    if target.get(field, (None,))[0] == "list":
                        target[field][1].update(value)
                    else:
                        target[field] = ["list", set(value)]
                else:
                    target[field] = ["scalar", copy.deepcopy(value)]
    requirements = {
        metric: {
            field: (sorted(value, key=str) if kind == "list" else value)
            for field, (kind, value) in sorted(fields.items())
        }
        for metric, fields in sorted(requirement_fields.items())
    }
    requirements["qualification_sets"] = [
        copy.deepcopy(qualification_sets[item_id])
        for item_id in sorted(qualification_sets)]
    rank = {"optional": 0, "required": 1, "strict": 2}
    policy = max((source.correctness_policy for source in sources),
                 key=lambda value: rank[value])
    all_inferred = all(not source._explicit_requirements for source in sources)
    merged = Metric123RawLogMatrix(
        requirements=None if all_inferred else requirements,
        correctness_policy=policy,
        base_dir=sources[0].base_dir if len(sources) == 1 else ".")
    if all_inferred:
        merged._inferred = {
            "metric1": {"repetitions": set(), "min_samples": minimum_samples(
                            requirements.get("metric1", {})),
                        "roles": set(requirements.get("metric1", {}).get("roles", ("naive", "spill", "ideal"))),
                        "ideal_min_capacity": int(requirements.get("metric1", {}).get("ideal_min_capacity", 102656))},
            "metric2": {"repetitions": set(), "min_samples": minimum_samples(
                            requirements.get("metric2", {})),
                        "testcases": set(requirements.get("metric2", {}).get("testcases", []))},
            "metric3": {"mode": requirements.get("metric3", {}).get("mode", "independent"),
                        "repetitions": set(),
                        "min_repetitions": max(
                            1, int(requirements.get("metric3", {}).get("min_repetitions", 1))),
                        "testcases": set(requirements.get("metric3", {}).get("testcases", [])),
                        "arms": set(requirements.get("metric3", {}).get("arms", ("ourcc", "ha-vi")))},
        }
        merged._requirements = {}
        merged._qualification_sets = []

    records = [record for source in sources for record in source._resolved]
    digests = None
    if workers > 1 and len(records) > 1:
        chunk_size = max(1, (len(records) + workers * 4 - 1) // (workers * 4))
        chunks = [range(start, min(start + chunk_size, len(records)))
                  for start in range(0, len(records), chunk_size)]
        with process_pool(min(workers, len(chunks)),
                          _init_merge_worker, (records,)) as pool:
            batches = pool.map(_merge_fingerprint_batch, chunks)
            digests = [None] * len(records)
            for batch in batches:
                for index, digest in batch:
                    digests[index] = digest

    seen_snapshot_strings = set()
    seen_snapshot_digests = defaultdict(list)
    seen_issues = set()
    next_suffix = {}

    def add_issue(item):
        fingerprint = json.dumps(item, sort_keys=True, separators=(",", ":"))
        if fingerprint not in seen_issues:
            seen_issues.add(fingerprint)
            merged._issues.append(copy.deepcopy(item))

    for item in inferred_requirement_issues:
        add_issue(item)

    def allocate_id(requested):
        effective = requested
        if effective in merged._ids:
            suffix = next_suffix.get(requested, 2)
            while f"{requested}-{suffix}" in merged._ids:
                suffix += 1
            effective = f"{requested}-{suffix}"
            next_suffix[requested] = suffix + 1
        else:
            next_suffix.setdefault(requested, 2)
        merged._ids.add(effective)
        return effective

    record_index = 0
    for source in sources:
        for record in source._resolved:
            digest = digests[record_index] if digests is not None else None
            record_index += 1
            if digest is None:
                fingerprint = _merge_snapshot_fingerprint(record)
                if fingerprint in seen_snapshot_strings:
                    continue
                seen_snapshot_strings.add(fingerprint)
            else:
                bucket = seen_snapshot_digests[digest]
                if bucket:
                    fingerprint = _merge_snapshot_fingerprint(record)
                    if any(fingerprint == _merge_snapshot_fingerprint(prior)
                           for prior in bucket):
                        continue
                bucket.append(record)
            row = copy.deepcopy(record)
            requested = row["id"]
            effective = allocate_id(requested)
            if effective != requested:
                item = warning("DUPLICATE_RUN_ID_RENAMED", effective,
                               f"merged run id {requested!r} renamed to {effective!r}")
                row["id"] = effective
                if row.get("repetition_source") == "auto-run-id":
                    row["repetition"] = effective
                row.setdefault("contract_warnings", []).append(item)
                add_issue(item)
            correctness_status = row.get("correctness", {}).get("status")
            if policy in ("strict", "required") and correctness_status != "PASS":
                issue = {"severity": "ERROR", "code": "EVIDENCE_INVALID",
                         "run_id": effective,
                         "contract_class": ("standard" if row.get("standard_contract")
                                            else "extension"),
                         "message": (f"merged correctness policy {policy} requires PASS; "
                                     f"snapshot status={correctness_status}")}
                add_issue(issue)
                merged._add_results.append({"status": "REJECTED",
                                            "requested_id": requested,
                                            "run_id": effective, "slot": None,
                                            "issue": issue})
                continue
            row["correctness"]["policy"] = policy
            row["correctness"]["required"] = policy in ("strict", "required")
            row["qualified_contracts"] = qualified_contract_ids(
                row, qualification_candidates(row, merged._qualification_sets))
            row["formal_contract"] = bool(row.get("standard_contract") or
                                          row["qualified_contracts"])
            slot = logical_slot(row)
            if not merged._explicit_requirements:
                if row["metric"] == 1 and row.get("standard_contract"):
                    merged._inferred["metric1"]["repetitions"].clear()
                elif row["metric"] == 2 and row.get("standard_contract"):
                    merged._inferred["metric2"]["repetitions"].clear()
                    merged._inferred["metric2"]["testcases"].update(M2)
                elif row["metric"] == 3:
                    if row.get("standard_contract"):
                        merged._inferred["metric3"]["testcases"].update(M3)
                        merged._inferred["metric3"]["arms"].add(row["arm"])
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
        for issue in source._issues:
            if issue.get("code") != "DUPLICATE_RUN_ID_RENAMED":
                add_issue(issue)
        resolved_ids = {record["id"] for record in source._resolved}
        for result in source._add_results:
            if (result.get("status") != "REJECTED" or
                    result.get("issue", {}).get("code") == "DUPLICATE_SLOT" or
                    result.get("run_id") in resolved_ids):
                continue
            copied = copy.deepcopy(result)
            requested = copied.get("run_id") or copied.get("requested_id") or "run"
            effective = allocate_id(str(requested))
            if effective != requested:
                copied["run_id"] = effective
                if copied.get("issue"):
                    copied["issue"]["run_id"] = effective
                copied["warning"] = warning(
                    "DUPLICATE_RUN_ID_RENAMED", effective,
                    f"merged rejected run id {requested!r} renamed to {effective!r}")
            merged._add_results.append(copied)
    return merged


def analyze(manifest_path, output_dir, require_qualifications=None, *, workers=1):
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
                               require_qualifications=require_qualifications,
                               ingestion={"attempted": len(matrix._add_results),
                                          "added": sum(x["status"] == "ADDED" for x in matrix._add_results),
                                          "rejected": sum(x["status"] == "REJECTED" for x in matrix._add_results),
                                          "duplicate_conflicted": sum(
                                              count for count in slot_counts.values() if count > 1),
                                          "add_results": matrix._add_results},
                               workers=workers)
    # Preserve analyze's historical tuple and its division of writing responsibility.
    return result


def write_tsv(path, rows, fields):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows({key: ("N/A" if value is None else value)
                          for key, value in row.items()} for row in rows)


def markdown_cell(value):
    """Render one compact, table-safe Markdown value."""
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(json_ready(value), ensure_ascii=False,
                           sort_keys=True, separators=(",", ":"))
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def run_contract_diagnostic(run, qualification_sets_configured):
    """Explain one parsed run's standard/formal/extension classification."""
    qualified = run.get("qualified_contracts", [])
    failed_gates = []
    other_warnings = []
    for item in run.get("contract_warnings", []):
        if item.get("code") == "NONSTANDARD_CONTRACT":
            failed_gates.extend(item.get("failed_gates", []))
        else:
            other_warnings.append(f"{item.get('code', 'WARNING')}: {item.get('message', '')}")
    if run.get("standard_contract"):
        classification = "标准（Standard）"
        reason = "满足冻结 standard 合同"
    elif qualified:
        classification = "资格正式（Formal qualification）"
        reason = ("未满足冻结 standard 合同，但命中资格合同：" +
                  ", ".join(qualified))
    else:
        classification = "扩展（Extension）"
        reason = ("未满足冻结 standard 合同，且未命中已配置 qualification_sets"
                  if qualification_sets_configured else
                  "未满足冻结 standard 合同；当前未配置 qualification_sets")
    if failed_gates:
        reason += "；冻结合同未通过：" + "；".join(map(str, failed_gates))
    return {"classification": classification, "reason": reason,
            "warnings": other_warnings}


def format_missing_slots(missing):
    """Render named missing-slot records as concise Chinese diagnostics."""
    if not missing:
        return "无"
    rows = []
    for item in missing:
        if isinstance(item, dict) and item.get("kind") == "minimum_samples":
            coordinate = []
            if item.get("topology") is not None:
                coordinate.append(f"topology={item['topology']}")
            if item.get("tc") is not None:
                coordinate.append(f"TC{item['tc']}")
            if item.get("role") is not None:
                coordinate.append(f"role={item['role']}")
            if item.get("profile") is not None:
                coordinate.append(f"profile={item['profile']}")
            if item.get("arm") is not None:
                coordinate.append(f"arm={item['arm']}")
            rows.append(
                f"{' / '.join(coordinate)}：实际样本 {item['observed_samples']}，"
                f"最低要求 {item['required_min_samples']}")
        elif isinstance(item, dict) and item.get("kind") == "exact_repetition":
            rows.append(
                f"repetition={item['repetition']} / TC{item['tc']} / "
                f"arm={item['arm']}：未找到样本")
        elif isinstance(item, dict) and item.get("kind") == "required_metrics":
            coordinate = f"TC{item['tc']} / " if item.get("tc") is not None else ""
            rows.append(f"{coordinate}必需指标不可用：{item.get('message', '未说明')}")
        else:
            rows.append(markdown_cell(item))
    return "；".join(rows)


def brief_number(value, suffix=""):
    value = finite_number(value)
    return "N/A" if value is None else f"{value:.3f}{suffix}"


def brief_metric_values(report):
    m1 = report["metric1"]["aggregate"]
    m3 = {row["name"]: row for row in report["metric3"].get("aggregates", [])}
    return {
        "metric1_capacity_ratio": m1["capacity_ratio"].get("mean"),
        "metric1_outer_delta_cycles": m1["outer_delta_cycles"].get("mean"),
        "metric2_reduction_pct": report["metric2"].get("aggregate_reduction_pct"),
        "metric3_core_delta_ns": m3.get("core", {}).get("delta_ns"),
        "metric3_representative_delta_ns": m3.get("representative", {}).get("delta_ns"),
    }


def detail_scope(runs):
    if any(run.get("standard_contract") for run in runs):
        return "Standard", STANDARD_CONTRACT_ID
    contracts = sorted({item for run in runs
                        for item in run.get("qualified_contracts", [])})
    return (("Formal qualification", ",".join(contracts)) if contracts else
            ("Extension descriptive", ""))


def build_metric_details(resolved, report=None):
    details = {"metric1": [], "metric2": [], "metric3": []}
    m1_coordinates = sorted({(run["tc"], run["topology"])
                             for run in resolved if run["metric"] == 1})
    for tc, topology in m1_coordinates:
        runs = [run for run in resolved if run["metric"] == 1 and
                run["tc"] == tc and run["topology"] == topology]
        role_runs = {role: [run for run in runs if run.get("metric1_role") == role]
                     for role in ("naive", "spill", "ideal")}
        contract_sets = []
        for role in ("naive", "spill", "ideal"):
            tokens = set()
            for run in role_runs[role]:
                if run.get("standard_contract"):
                    tokens.add(STANDARD_CONTRACT_ID)
                tokens.update(run.get("qualified_contracts", []))
            contract_sets.append(tokens)
        common_contracts = set.intersection(*contract_sets) if all(contract_sets) else set()
        contract = (STANDARD_CONTRACT_ID if STANDARD_CONTRACT_ID in common_contracts else
                    sorted(common_contracts)[0] if common_contracts else "")
        groups = {role: [run for run in role_runs[role]
                         if ((contract == STANDARD_CONTRACT_ID and run.get("standard_contract")) or
                             contract in run.get("qualified_contracts", []))]
                  for role in role_runs}
        complete_contract = bool(contract)
        if complete_contract and report is not None:
            if contract == STANDARD_CONTRACT_ID:
                complete_contract = report["metric1"]["status"] in ("PASS", "FAIL")
            else:
                qualification = next((item for item in report.get("qualifications", [])
                                      if item["id"] == contract), None)
                coordinate_result = next((item for item in (qualification or {}).get("results", [])
                                          if item.get("coordinate", {}).get("tc") == tc and
                                          item.get("coordinate", {}).get("topology") == topology), None)
                complete_contract = bool(coordinate_result and
                                         coordinate_result.get("status") in ("PASS", "FAIL"))
        naive = safe_mean(run["metrics"]["capacity"].get("effective_unique")
                          for run in groups["naive"])
        spill = safe_mean(run["metrics"]["capacity"].get("effective_unique")
                          for run in groups["spill"])
        spill_outer = pooled_outer_latency(groups["spill"])
        ideal_outer = pooled_outer_latency(groups["ideal"])
        delta_ns = safe_subtract(spill_outer["mean_ns"], ideal_outer["mean_ns"])
        scope = ("Standard" if contract == STANDARD_CONTRACT_ID else
                 "Formal qualification" if contract else "Extension descriptive")
        reasons = []
        for role, rows in groups.items():
            if not rows:
                reasons.append(f"missing role={role}")
        if groups["naive"] and naive is None:
            reasons.append("naive capacity unavailable")
        if groups["spill"] and spill is None:
            reasons.append("spill capacity unavailable")
        if groups["spill"] and spill_outer["samples"] == 0:
            reasons.append("spill completed Outer missing")
        if groups["ideal"] and ideal_outer["samples"] == 0:
            reasons.append("ideal completed Outer missing")
        if not contract:
            reasons.append("naive/spill/ideal do not share one formal contract")
        elif not complete_contract:
            reasons.append("formal coordinate is incomplete")
        details["metric1"].append({
            "scope": scope, "contract": contract, "topology": topology,
            "tc": tc, "capacity_ratio": (safe_divide(spill, naive)
                                           if complete_contract else None),
            "outer_delta_cycles": (delta_ns * 2.0
                                     if complete_contract and delta_ns is not None else None),
            "sample_counts": {role: len(rows) for role, rows in groups.items()},
            "role_details": {
                role: [{"run_id": run["id"], "profile": run["profile"],
                        "metric1_role": run.get("metric1_role"),
                        "role_source": run.get("role_source"),
                        "capacity_policy": run["metrics"]["capacity"].get("policy"),
                        "effective_unique_lines": run["metrics"]["capacity"].get("effective_unique"),
                        "resident_capacity": run["metrics"]["capacity"].get("resident_capacity"),
                        "h64_exact_live_known": run["metrics"]["capacity"].get("h64_exact_live_known"),
                        "h64_exact_live": run["metrics"]["capacity"].get("h64_exact_live"),
                        "oversized": run["metrics"]["capacity"].get(
                            "experimental_oversized_resident_dir"),
                        "backstore_found_fills": run["metrics"]["capacity"].get(
                            "backstore_found_fills"),
                        "outer_samples": run["metrics"]["outer_latency"].get("samples"),
                        "outer_mean_ns": run["metrics"]["outer_latency"].get("mean_ns"),
                        "qualified_contracts": run.get("qualified_contracts", []),
                        "contract_warnings": run.get("contract_warnings", [])}
                       for run in role_runs[role]]
                for role in ("naive", "spill", "ideal")},
            "reason": "; ".join(reasons),
        })

    m2_coordinates = sorted({(run["tc"], run["topology"],
                              run["metrics"].get("phase"))
                             for run in resolved if run["metric"] == 2}, key=str)
    for tc, topology, phase in m2_coordinates:
        runs = [run for run in resolved if run["metric"] == 2 and
                run["tc"] == tc and run["topology"] == topology and
                run["metrics"].get("phase") == phase]
        groups = {profile: [run for run in runs if run.get("profile") == profile]
                  for profile in PROFILES}
        means = {profile: safe_mean(run["metrics"].get("mean_ns")
                                    for run in groups[profile])
                 for profile in PROFILES}
        reduction = safe_divide(safe_subtract(means["naive"], means["optimized"]),
                                means["naive"])
        scope, contract = detail_scope(runs)
        reasons = [f"missing profile={profile}" for profile, rows in groups.items()
                   if not rows]
        reasons.extend(f"{profile} mean unavailable" for profile, value in means.items()
                       if groups[profile] and value is None)
        details["metric2"].append({
            "scope": scope, "contract": contract, "topology": topology,
            "tc": tc, "phase": phase,
            "reduction_pct": reduction * 100 if reduction is not None else None,
            "means_ns": means,
            "sample_counts": {profile: len(rows) for profile, rows in groups.items()},
            "reason": "; ".join(reasons),
        })

    m3_coordinates = sorted({(run["tc"], run["topology"])
                             for run in resolved if run["metric"] == 3})
    for tc, topology in m3_coordinates:
        runs = [run for run in resolved if run["metric"] == 3 and
                run["tc"] == tc and run["topology"] == topology]
        values = defaultdict(list)
        for run in runs:
            for name, metric in run["metrics"].items():
                value = finite_number(metric.get("ns_per_operation"))
                if value is not None:
                    values[name, run["arm"]].append(value)
        metric_deltas = {}
        for name in M3.get(tc, {}):
            left, right = values[name, "ourcc"], values[name, "ha-vi"]
            metric_deltas[name] = (safe_subtract(safe_mean(right), safe_mean(left))
                                   if left and right else None)
        weights = metric3_primary_weights(tc, topology) if tc in M3 else {}
        primary = (safe_weighted_sum((weight, metric_deltas.get(name))
                                     for name, weight in weights.items())
                   if weights else None)
        scope, contract = detail_scope(runs)
        counts = {arm: sum(run.get("arm") == arm for run in runs)
                  for arm in ("ourcc", "ha-vi")}
        reasons = [f"missing arm={arm}" for arm, count in counts.items() if count == 0]
        reasons.extend(f"metric={name} delta unavailable" for name, value in metric_deltas.items()
                       if value is None)
        details["metric3"].append({
            "scope": scope, "contract": contract, "topology": topology,
            "tc": tc, "primary_delta_ns": primary,
            "direction": metric3_direction(primary), "metric_deltas": metric_deltas,
            "sample_counts": counts, "reason": "; ".join(reasons),
        })
    return details


def render_detail_markdown(details):
    lines = ["# Metric 1/2/3 按拓扑与 TC 明细", "",
             "`Extension descriptive`表示日志指标已提取，但未进入冻结 Standard 或显式 qualification。", "",
             "## Metric1", "",
             "| Scope | Contract | Topology | TC | Capacity ratio | Outer delta cycles | Samples naive/spill/ideal | Reason |",
             "|---|---|---|---:|---:|---:|---|---|"]
    for row in details["metric1"]:
        counts = row["sample_counts"]
        lines.append(
            f"| {row['scope']} | {row['contract'] or '-'} | {row['topology']} | TC{row['tc']} | "
            f"{brief_number(row['capacity_ratio'])} | {brief_number(row['outer_delta_cycles'])} | "
            f"{counts['naive']}/{counts['spill']}/{counts['ideal']} | {row['reason'] or '-'} |")
        for role in ("naive", "spill", "ideal"):
            for item in row["role_details"][role]:
                lines.append(
                    f"| ↳ {role} | {item['run_id']} | profile={item['profile']} | - | "
                    f"capacity={brief_number(item['effective_unique_lines'])} lines | "
                    f"Outer={brief_number(item['outer_mean_ns'])} ns | "
                    f"policy={item['capacity_policy']}, oversized={item['oversized']}, "
                    f"exact={item['h64_exact_live_known']}/{item['h64_exact_live']}, "
                    f"samples={item['outer_samples']} | "
                    f"qualified={item['qualified_contracts']} |")
    if not details["metric1"]:
        lines.append("| - | - | - | - | N/A | N/A | - | no Metric1 runs |")
    lines += ["", "## Metric2", "",
              "| Scope | Contract | Topology | TC | Phase | Reduction % | Naive/Optimized ns | Samples naive/spill/optimized | Reason |",
              "|---|---|---|---:|---|---:|---|---|---|"]
    for row in details["metric2"]:
        counts, means = row["sample_counts"], row["means_ns"]
        lines.append(
            f"| {row['scope']} | {row['contract'] or '-'} | {row['topology']} | TC{row['tc']} | "
            f"{row['phase']} | {brief_number(row['reduction_pct'])} | "
            f"{brief_number(means['naive'])}/{brief_number(means['optimized'])} | "
            f"{counts['naive']}/{counts['spill-noopt']}/{counts['optimized']} | {row['reason'] or '-'} |")
    if not details["metric2"]:
        lines.append("| - | - | - | - | - | N/A | N/A | - | no Metric2 runs |")
    lines += ["", "## Metric3", "",
              "| Scope | Contract | Topology | TC | Primary delta ns/op | Direction | Samples OurCC/HA-VI | Metric deltas ns/op | Reason |",
              "|---|---|---|---:|---:|---|---|---|---|"]
    for row in details["metric3"]:
        counts = row["sample_counts"]
        lines.append(
            f"| {row['scope']} | {row['contract'] or '-'} | {row['topology']} | TC{row['tc']} | "
            f"{brief_number(row['primary_delta_ns'])} | {row['direction']} | "
            f"{counts['ourcc']}/{counts['ha-vi']} | {markdown_cell(row['metric_deltas'])} | "
            f"{row['reason'] or '-'} |")
    if not details["metric3"]:
        lines.append("| - | - | - | - | N/A | UNAVAILABLE | - | - | no Metric3 runs |")
    return "\n".join(lines) + "\n"


def render_brief_markdown(report, details=None):
    details = details or {"metric1": [], "metric2": [], "metric3": []}
    values = brief_metric_values(report)
    missing = {name: [point for point in report[name].get("coverage", [])
                      if not point.get("complete", False)]
               for name in ("metric1", "metric2", "metric3")}
    extra_reasons = []
    extra_missing_counts = {name: 0 for name in ("metric1", "metric2", "metric3")}
    for name in ("metric1", "metric2", "metric3"):
        for item in report[name].get("missing_slots", []):
            if isinstance(item, dict) and item.get("kind") == "required_metrics":
                extra_missing_counts[name] += 1
                extra_reasons.append(
                    f"- **{name.upper()}** 必需指标不可用：{item.get('message', item)}。")
    for item in report["metric3"].get("incomplete_pairs", []):
        extra_missing_counts["metric3"] += 1
        extra_reasons.append(
            f"- **METRIC3** pair={item.get('pair')} / TC{item.get('tc')}："
            f"当前 arm={item.get('present_arms', [])}。")
    for item in report.get("issues", []):
        if item.get("severity") == "ERROR":
            extra_reasons.append(
                f"- **{item.get('code', 'ERROR')}** {item.get('run_id', '')}: "
                f"{item.get('message', '')}")
    lines = ["# Metric 1/2/3 结果摘要", "",
             f"总体状态：**{report['overall_status']}**", "",
             "| 指标 | 状态 | 关键结果 | 缺失点 |", "|---|---|---|---:|",
             f"| Metric1 | {report['metric1']['status']} | 容量比 "
             f"{brief_number(values['metric1_capacity_ratio'])}；Outer delta "
             f"{brief_number(values['metric1_outer_delta_cycles'], ' cycles')} | "
             f"{len(missing['metric1']) + extra_missing_counts['metric1']} |",
             f"| Metric2 | {report['metric2']['status']} | 适用 TC 等权降幅 "
             f"{brief_number(values['metric2_reduction_pct'], '%')} | "
             f"{len(missing['metric2']) + extra_missing_counts['metric2']} |",
             f"| Metric3 | {report['metric3']['status']} | Core delta "
             f"{brief_number(values['metric3_core_delta_ns'], ' ns/op')}；Representative delta "
             f"{brief_number(values['metric3_representative_delta_ns'], ' ns/op')} | "
             f"{len(missing['metric3']) + extra_missing_counts['metric3']} |", "",
             "Metric3 定义 `delta = HA-VI - OurCC`：正值表示 OurCC 更快，负值表示 HA-VI 更快；"
             "正负方向均保留。", "",
             "## 读图规则", "",
             "- Metric1 Capacity ratio：越大越好，参考门槛 `>= 1.5`。",
             "- Metric1 Outer delta：`spill - ideal`，越小越好，参考门槛 `< 50 cycles`；"
             "负值表示 spill 的 completed-Outer mean 低于 IdealDir，不是负延迟。",
             "- Metric2 Reduction：`(naive - optimized) / naive`，越大越好；负值表示 optimized 更慢。",
             "- Metric3 Delta：`HA-VI - OurCC`，正值表示 OurCC 更快，负值表示 HA-VI 更快；"
             "不以正负作为统一 PASS 门槛。", "",
             "![Metric summary](metric_summary_bar_chart.svg)"]
    detail_counts = {name: len(details[name]) for name in details}
    lines += ["", "## 按拓扑/TC 已提取点", "",
              f"- Metric1: {detail_counts['metric1']} 个点。",
              f"- Metric2: {detail_counts['metric2']} 个点。",
              f"- Metric3: {detail_counts['metric3']} 个点。",
              "- 详细数值和图见 `report_detail_by_tc_topology_zh.md` 与 "
              "`metric_detail_by_tc_topology.svg`。"]
    if values["metric1_capacity_ratio"] is None and details["metric1"]:
        lines.append(
            "- Metric1 Standard 为 N/A，但已提取其他 TC/topology 点；请查看其 scope。"
            "`Formal qualification`可作为已配置资格结果，`Extension descriptive`仅作描述。")
    if (values["metric3_core_delta_ns"] is None or
            values["metric3_representative_delta_ns"] is None) and details["metric3"]:
        lines.append(
            "- Metric3 Core/Representative Standard aggregate 为 N/A，但已有 TC/topology 明细；"
            "通常表示冻结 TC228-235 全套 aggregate 尚未形成，或数据属于额外 topology qualification。")
    reasons = []
    for name in ("metric1", "metric2", "metric3"):
        for point in missing[name]:
            coordinate = ", ".join(f"{key}={point[key]}" for key in
                                   ("tc", "role", "profile", "arm")
                                   if point.get(key) is not None)
            reasons.append(
                f"- **{name.upper()}** {coordinate}: 实际样本 "
                f"{point['observed_samples']}，最低要求 {point['required_min_samples']}。")
    reasons.extend(extra_reasons)
    if reasons:
        lines += ["", "## 主要缺失原因", ""] + reasons[:5]
        if len(reasons) > 5:
            lines.append(f"- 其余 {len(reasons) - 5} 个缺失点见 `report.md`。")
    return "\n".join(lines) + "\n"


def detail_chart_series(details):
    return [
        ("Metric1 capacity ratio", details["metric1"], "capacity_ratio", 1.5),
        ("Metric1 Outer delta cycles", details["metric1"], "outer_delta_cycles", 50.0),
        ("Metric2 reduction %", details["metric2"], "reduction_pct", 10.0),
        ("Metric3 primary delta ns/op", details["metric3"], "primary_delta_ns", 0.0),
    ]


def write_detail_svg(path, details):
    series = detail_chart_series(details)
    rows_per_panel = [max(1, len(rows)) for _, rows, _, _ in series]
    panel_heights = [85 + count * 34 for count in rows_per_panel]
    width, height = 1420, 70 + sum(panel_heights)
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
           '<rect width="100%" height="100%" fill="#f7f4ed"/>',
           '<style>text{font-family:DejaVu Sans,Arial,sans-serif;fill:#17232b}.title{font-size:22px;font-weight:700}.label{font-size:12px}.value{font-size:11px;font-weight:700}.note{font-size:11px;fill:#53636d}</style>',
           '<text x="30" y="38" class="title">Metric Detail by Topology / TC</text>']
    y0 = 60
    scope_colors = {"Standard": "#247ba0", "Formal qualification": "#2a9d66",
                    "Extension descriptive": "#9aa1a6"}
    for (title, rows, field, reference), panel_height in zip(series, panel_heights):
        svg.append(f'<rect x="25" y="{y0}" width="1370" height="{panel_height - 8}" rx="10" fill="#ffffff" stroke="#d8d3c8"/>')
        svg.append(f'<text x="45" y="{y0 + 28}" class="title">{html.escape(title)}</text>')
        values = [abs(row[field]) for row in rows if finite_number(row.get(field)) is not None]
        scale = max(values + [abs(reference), 1.0]) * 1.15
        zero = 660 if title.startswith("Metric3") else 380
        span = 650 if title.startswith("Metric3") else 930
        display_rows = rows or [{"topology": "-", "tc": "-", "scope": "-", field: None}]
        for index, row in enumerate(display_rows):
            y = y0 + 52 + index * 34
            label = f"{row.get('topology', '-')} / TC{row.get('tc', '-')} / {row.get('scope', '-')}"
            svg.append(f'<text x="45" y="{y + 19}" class="label">{html.escape(label)}</text>')
            value = row.get(field)
            if finite_number(value) is None:
                svg.append(f'<rect x="{zero}" y="{y}" width="110" height="22" rx="4" fill="#e6e2d9"/>')
                svg.append(f'<text x="{zero + 8}" y="{y + 16}" class="value">N/A</text>')
                continue
            color = scope_colors.get(row.get("scope"), "#9aa1a6")
            if title.startswith("Metric3"):
                bar_width = abs(value) / scale * span / 2
                bar_x = zero if value >= 0 else zero - bar_width
                svg.append(f'<line x1="{zero}" y1="{y - 3}" x2="{zero}" y2="{y + 25}" stroke="#495057"/>')
            else:
                bar_width = max(2, value / scale * span)
                bar_x = zero
                ref_x = zero + reference / scale * span
                svg.append(f'<line x1="{ref_x:.2f}" y1="{y - 3}" x2="{ref_x:.2f}" y2="{y + 25}" stroke="#c65d3b" stroke-width="2"/>')
            svg.append(f'<rect x="{bar_x:.2f}" y="{y}" width="{bar_width:.2f}" height="22" rx="4" fill="{color}"/>')
            value_x = max(zero + 5, bar_x + bar_width + 6)
            svg.append(f'<text x="{value_x:.2f}" y="{y + 16}" class="value">{value:.3f}</text>')
        y0 += panel_height
    svg.append('</svg>')
    path.write_text("\n".join(svg) + "\n")


def write_detail_png(path, details):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except (ImportError, OSError):
        path.unlink(missing_ok=True)
        return False
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        series = detail_chart_series(details)
        figure, axes = plt.subplots(4, 1, figsize=(15, max(10, sum(
            max(1, len(rows)) for _, rows, _, _ in series) * 0.38 + 5)))
        colors = {"Standard": "#247ba0", "Formal qualification": "#2a9d66",
                  "Extension descriptive": "#9aa1a6"}
        for axis, (title, rows, field, reference) in zip(axes, series):
            display = rows or [{"topology": "-", "tc": "-", "scope": "-", field: None}]
            labels = [f"{row.get('topology', '-')} / TC{row.get('tc', '-')} / {row.get('scope', '-')}"
                      for row in display]
            values = [0 if finite_number(row.get(field)) is None else row[field] for row in display]
            bar_colors = ["#d8d3c8" if finite_number(row.get(field)) is None else
                          colors.get(row.get("scope"), "#9aa1a6") for row in display]
            bars = axis.barh(labels, values, color=bar_colors)
            axis.axvline(0, color="#495057", linewidth=0.8)
            if not title.startswith("Metric3"):
                axis.axvline(reference, color="#c65d3b", linewidth=1.5)
            axis.set_title(title, fontweight="bold")
            axis.grid(axis="x", alpha=0.2)
            axis.invert_yaxis()
            for bar, row in zip(bars, display):
                raw = row.get(field)
                text = "N/A" if finite_number(raw) is None else f"{raw:.3f}"
                axis.text(bar.get_width(), bar.get_y() + bar.get_height() / 2,
                          text, va="center", ha="left" if bar.get_width() >= 0 else "right",
                          fontsize=8)
        figure.suptitle("Metric Detail by Topology / TC", fontsize=16, fontweight="bold")
        figure.tight_layout()
        figure.savefig(temporary, format="png", dpi=180, bbox_inches="tight")
        plt.close(figure)
        temporary.replace(path)
        return True
    except Exception:
        try:
            plt.close("all")
        except Exception:
            pass
        temporary.unlink(missing_ok=True)
        path.unlink(missing_ok=True)
        return False


def write_summary_svg(path, report):
    values = brief_metric_values(report)
    panels = [
        ("Metric1", [("Capacity ratio", values["metric1_capacity_ratio"], 1.5),
                     ("Outer delta cycles", values["metric1_outer_delta_cycles"], 50.0)]),
        ("Metric2", [("Reduction %", values["metric2_reduction_pct"], 10.0)]),
        ("Metric3", [("Core delta ns/op", values["metric3_core_delta_ns"], 0.0),
                     ("Representative delta ns/op", values["metric3_representative_delta_ns"], 0.0)]),
    ]
    width, height = 1080, 560
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
           '<rect width="100%" height="100%" fill="#f7f4ed"/>',
           '<style>text{font-family:DejaVu Sans,Arial,sans-serif;fill:#17232b}.title{font-size:24px;font-weight:700}.label{font-size:14px}.value{font-size:13px;font-weight:700}.note{font-size:12px;fill:#53636d}</style>',
           '<text x="40" y="42" class="title">Metric 1/2/3 Summary</text>']
    panel_width = 320
    for panel_index, (title, rows) in enumerate(panels):
        x0, y0 = 40 + panel_index * 345, 75
        svg.append(f'<rect x="{x0}" y="{y0}" width="{panel_width}" height="420" rx="12" fill="#ffffff" stroke="#d8d3c8"/>')
        svg.append(f'<text x="{x0 + 18}" y="{y0 + 32}" class="title">{title}</text>')
        metric3_values = [abs(value) for _, value, _ in rows
                          if finite_number(value) is not None]
        metric3_scale = max(metric3_values + [1.0]) * 1.25
        for row_index, (label, value, reference) in enumerate(rows):
            y = y0 + 95 + row_index * 145
            svg.append(f'<text x="{x0 + 18}" y="{y - 20}" class="label">{label}</text>')
            if finite_number(value) is None:
                svg.append(f'<rect x="{x0 + 18}" y="{y}" width="284" height="34" rx="5" fill="#e6e2d9"/>')
                svg.append(f'<text x="{x0 + 28}" y="{y + 23}" class="value">N/A</text>')
                continue
            if title == "Metric3":
                zero = x0 + 160
                bar_width = abs(value) / metric3_scale * 130
                bar_x = zero if value >= 0 else zero - bar_width
                color = "#247ba0" if value >= 0 else "#c65d3b"
                svg.append(f'<line x1="{zero}" y1="{y - 8}" x2="{zero}" y2="{y + 42}" stroke="#495057"/>')
                svg.append(f'<rect x="{bar_x:.2f}" y="{y}" width="{bar_width:.2f}" height="34" rx="5" fill="{color}"/>')
            else:
                scale = max(abs(value), abs(reference), 1.0) * 1.2
                bar_width = max(2, value / scale * 270)
                svg.append(f'<rect x="{x0 + 18}" y="{y}" width="{bar_width:.2f}" height="34" rx="5" fill="#247ba0"/>')
                ref_x = x0 + 18 + reference / scale * 270
                svg.append(f'<line x1="{ref_x:.2f}" y1="{y - 8}" x2="{ref_x:.2f}" y2="{y + 42}" stroke="#c65d3b" stroke-width="3"/>')
            svg.append(f'<text x="{x0 + 18}" y="{y + 58}" class="value">{value:.3f}</text>')
        if title == "Metric3":
            svg.append(f'<text x="{x0 + 18}" y="{y0 + 390}" class="note">positive: OurCC faster; negative: HA-VI faster</text>')
        else:
            svg.append(f'<text x="{x0 + 18}" y="{y0 + 390}" class="note">red marker: reference threshold</text>')
    svg.append('</svg>')
    path.write_text("\n".join(svg) + "\n")


def write_summary_png(path, report):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except (ImportError, OSError):
        path.unlink(missing_ok=True)
        return False
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        values = brief_metric_values(report)
        figure, axes = plt.subplots(1, 3, figsize=(13.5, 4.8))
        panels = [
            ("Metric1", ["Capacity ratio", "Outer delta cycles"],
             [values["metric1_capacity_ratio"], values["metric1_outer_delta_cycles"]],
             [1.5, 50.0]),
            ("Metric2", ["Reduction %"], [values["metric2_reduction_pct"]], [10.0]),
            ("Metric3", ["Core delta", "Representative delta"],
             [values["metric3_core_delta_ns"], values["metric3_representative_delta_ns"]],
             [0.0, 0.0]),
        ]
        for axis, (title, labels, raw_values, references) in zip(axes, panels):
            plot_values = [0 if finite_number(value) is None else value for value in raw_values]
            colors = [("#9aa1a6" if finite_number(raw) is None else
                       "#247ba0" if value >= 0 else "#c65d3b")
                      for raw, value in zip(raw_values, plot_values)]
            bars = axis.bar(labels, plot_values, color=colors)
            axis.axhline(0, color="#495057", linewidth=0.8)
            for reference in references:
                if reference:
                    axis.axhline(reference, color="#c65d3b", linewidth=1.2)
            axis.set_title(title, fontweight="bold")
            axis.tick_params(axis="x", rotation=20)
            axis.grid(axis="y", alpha=0.2)
            for bar, raw in zip(bars, raw_values):
                text = "N/A" if finite_number(raw) is None else f"{raw:.3f}"
                axis.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), text,
                          ha="center", va="bottom" if bar.get_height() >= 0 else "top", fontsize=9)
        figure.suptitle("Metric 1/2/3 Summary", fontsize=16, fontweight="bold")
        figure.tight_layout()
        figure.savefig(temporary, format="png", dpi=180, bbox_inches="tight")
        plt.close(figure)
        temporary.replace(path)
        return True
    except Exception:
        try:
            plt.close("all")
        except Exception:
            pass
        temporary.unlink(missing_ok=True)
        path.unlink(missing_ok=True)
        return False


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
    write_tsv(output_dir / "metric_matrix_formal.tsv",
              report["views"].get("formal", {"matrix": []})["matrix"], matrix_fields)
    write_tsv(output_dir / "per-run_metrics.tsv", per_run,
              ["run_id", "metric", "tc", "repetition", "profile", "arm", "pair", "order", "value", "unit", "status"])
    write_tsv(output_dir / "issues.tsv", issues,
              ["severity", "code", "run_id", "contract_class", "message"])
    details = build_metric_details(resolved, report)
    (output_dir / "metric_detail_by_tc_topology.json").write_text(json.dumps(
        json_ready(details), indent=2, sort_keys=True, allow_nan=False) + "\n")
    (output_dir / "report_detail_by_tc_topology_zh.md").write_text(
        render_detail_markdown(details))
    (output_dir / "report_brief_zh.md").write_text(
        render_brief_markdown(report, details))
    write_summary_svg(output_dir / "metric_summary_bar_chart.svg", report)
    write_summary_png(output_dir / "metric_summary_bar_chart.png", report)
    write_detail_svg(output_dir / "metric_detail_by_tc_topology.svg", details)
    write_detail_png(output_dir / "metric_detail_by_tc_topology.png", details)
    lines = ["# Metric 1/2/3 原始日志统一报告", "", f"总体状态：**{report['overall_status']}**", "",
             "| 指标 | 状态 |", "|---|---|", f"| Metric1 | {report['metric1']['status']} |",
             f"| Metric2 | {report['metric2']['status']} |", f"| Metric3 | {report['metric3']['status']} |", "",
              "Metric3 仅表示冻结可执行参考模型范围；delta = HA-VI - OurCC，正负方向均如实保留。",
              "Metric3 的 COMPLETE 只表示证据与矩阵完整，不表示所有场景均强于 HA-VI。",
             "不执行 t-test，不生成 p-value，不做笛卡尔配对。", "",
              "## 视图", "",
               f"- Standard runs: {report['views']['standard']['runs']}",
              f"- Formal runs (standard + configured qualifications): "
              f"{report['views'].get('formal', {}).get('runs', 0)}",
              f"- All parsed runs: {report['views']['all']['runs']}",
              f"- Extension runs: {report['views']['extension']['runs']}",
              f"- Unique evidence files: {report['source_inventory']['unique_files']}",
              f"- Source references/marker rows: {report['source_inventory']['source_references']}",
              "- Source references are not logical runs and must not be used as a parsed-run count.",
              "", "## 资格合同", ""]
    if report.get("qualifications"):
        lines += ["| Qualification | Metric | Status | Runs | Missing |",
                  "|---|---:|---|---:|---:|"]
        for item in report["qualifications"]:
            lines.append(
                f"| {item['id']} | {item['metric']} | {item['status']} | "
                f"{item['runs']} | {len(item['missing_slots'])} |")
    else:
        lines.append("未配置 qualification_sets。")
    lines += ["", "## 逐测试诊断", "",
              "该表用于判断每条已解析 run 为什么属于标准、资格正式或扩展数据。",
              "资格正式表示它仍保留 `contract_class=extension`，但已通过显式资格合同并进入正式视图。",
              "", "| Run | Metric/TC | 坐标 | 归类 | 具体原因 | 其他告警 |",
              "|---|---|---|---|---|---|"]
    qualification_sets_configured = bool(report.get("qualifications"))
    for run in resolved:
        diagnostic = run_contract_diagnostic(run, qualification_sets_configured)
        coordinate = {
            "repetition": run.get("repetition"), "topology": run.get("topology"),
            "profile": run.get("profile"), "role": run.get("metric1_role"),
            "arm": run.get("arm"), "pair": run.get("pair"),
            "order": run.get("order")}
        coordinate = {key: value for key, value in coordinate.items()
                      if value not in (None, "")}
        lines.append(
            f"| `{markdown_cell(run['id'])}` | Metric{run['metric']}/TC{run['tc']} | "
            f"{markdown_cell(coordinate)} | {diagnostic['classification']} | "
            f"{markdown_cell(diagnostic['reason'])} | "
            f"{markdown_cell(diagnostic['warnings'] or '无')} |")
    if not resolved:
        lines.append("| - | - | - | - | 没有成功解析且无 slot 冲突的 run | - |")

    rejected = [item for item in report.get("ingestion", {}).get("add_results", [])
                if item.get("status") == "REJECTED"]
    lines += ["", "## 未接纳的测试", ""]
    if rejected:
        lines += ["| Run | Slot | 错误码 | 具体原因 |", "|---|---|---|---|"]
        for item in rejected:
            issue = item.get("issue", {})
            lines.append(
                f"| `{markdown_cell(item.get('run_id') or item.get('requested_id') or '')}` | "
                f"{markdown_cell(item.get('slot'))} | `{markdown_cell(issue.get('code', 'REJECTED'))}` | "
                f"{markdown_cell(issue.get('message', '未提供原因'))} |")
    else:
        lines.append("没有被拒绝的 add 尝试。")

    lines += ["", "## 未满足的矩阵要求", ""]
    missing_sections = [
        ("Metric1", report["metric1"]["status"], report["metric1"].get("missing_slots", [])),
        ("Metric2", report["metric2"]["status"], report["metric2"].get("missing_slots", [])),
        ("Metric3", report["metric3"]["status"], report["metric3"].get("missing_slots", [])),
    ]
    for name, status, missing in missing_sections:
        lines.append(
            f"- **{name}**：状态 `{status}`；缺失槽位："
            f"{format_missing_slots(missing)}。")
    incomplete_points = []
    for name in ("metric1", "metric2", "metric3"):
        for point in report[name].get("coverage", []):
            if not point.get("complete", False):
                incomplete_points.append({"metric": name.upper(), **point})
    if incomplete_points:
        lines += ["", "### INCOMPLETE 直接原因", ""]
        for point in incomplete_points:
            coordinate = []
            for key in ("topology", "tc", "role", "profile", "arm"):
                if point.get(key) is not None:
                    coordinate.append(f"{key}={point[key]}")
            lines.append(
                f"- **{point['metric']}** {' / '.join(coordinate)}：实际样本 "
                f"{point['observed_samples']}，最低要求 {point['required_min_samples']}。")
    failed_m1 = [row for row in report["metric1"].get("comparisons", [])
                 if not row.get("pass")]
    failed_m2 = [row for row in report["metric2"].get("repetition_equal_weight", [])
                 if not row.get("pass")]
    if failed_m1:
        lines.append(
            "- **Metric1 门槛失败**：" + markdown_cell(failed_m1) +
            "。要求 capacity_ratio >= 1.5 且 outer_delta_cycles < 50。")
    if failed_m2:
        lines.append(
            "- **Metric2 门槛失败**：" + markdown_cell(failed_m2) +
            "。每轮适用 TC 的等权平均降幅要求 >= 10%。")
    if report["metric2"].get("repetitions_without_applicable_cases"):
        lines.append(
            "- **Metric2 无适用 case 的轮次**：" + markdown_cell(
                report["metric2"]["repetitions_without_applicable_cases"]) +
            "。baseline 必须 >= 500 ns 才进入降幅判定。")
    if not report["metric2"].get("applicable_set_stable", True):
        lines.append("- **Metric2 适用集合不稳定**：不同 repetition 的 applicable TC 集合不一致。")
    incomplete_pairs = report["metric3"].get("incomplete_pairs", [])
    if incomplete_pairs:
        lines.append(f"- **Metric3 不完整 pair**：{markdown_cell(incomplete_pairs)}。")
    for item in report.get("qualifications", []):
        if item["status"] == "PASS":
            continue
        failed_results = [row for row in item.get("results", [])
                          if row.get("status") != "PASS"]
        lines.append(
            f"- **资格合同 `{markdown_cell(item['id'])}`**：状态 `{item['status']}`；"
            f"缺失槽位：{markdown_cell(item.get('missing_slots') or '无')}；"
            f"未通过结果：{markdown_cell(failed_results or '无')}；"
            f"registry 错误：{markdown_cell(item.get('errors') or '无')}。")
    lines += ["", "## 标准矩阵", "",
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
    parser.add_argument("--require-qualification", action="append", default=[], metavar="ID",
                        help="also require the named opt-in qualification set to PASS")
    parser.add_argument("--workers", type=int, default=1, metavar="N",
                        help="processes for merge/report CPU work (default: 1)")
    args = parser.parse_args(argv)
    manifest, output = args.manifest.expanduser().resolve(), args.output_dir.expanduser().resolve()
    try:
        report, resolved, matrix, per_run, issues, code = analyze(
            manifest, output, args.require_qualification, workers=args.workers)
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
