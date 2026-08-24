#!/usr/bin/env python3
"""Extract unified Metric 1/2/3 evidence directly from explicit raw-log runs.

Only the Python standard library is required.  Pairing is identity-only and the
tool deliberately emits no confidence interval, t-test, or p-value.
"""

import argparse
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
TIMER_RE = re.compile(r"\[GUEST-TIMER\]\s+node=(\d+)\s+phase=(\S+)\s+operations=(\d+)\s+counter_ticks=(\d+)\s+counter_frequency_hz=(\d+)\s+source=(\S+)\s+unit=(\S+)")
LAT_RE = re.compile(r"\[PERF-LATENCY\]\s+node=(\d+)\s+phase=(\S+)\s+samples=(\d+)\s+min=(\d+)\s+p50=(\d+)\s+p95=(\d+)\s+p99=(\d+)\s+max=(\d+)\s+mean=(\d+)\s+counter_frequency_hz=(\d+)\s+source=(\S+)\s+unit=(\S+)")
SIMOUT_PATTERNS = (
    re.compile(r"simout_tc(\d+)_node(\d+)\.log(?:\.gz)?$"),
    re.compile(r"simout_n(\d+)(?:\.log)?(?:\.gz)?$"),
)
UBIO_DIR_RE = re.compile(r"ubio(?:_tc(\d+))?_n(\d+)_s(\d+)$")


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


def discover_home_ubio_logs(root, tc, node=0, socket=0):
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
    if len(candidates) != 1:
        raise ExtractError(
            f"Metric1 requires exactly one home UBIO directory for TC{tc} "
            f"n{node}s{socket}, found {sorted(map(str, candidates))}")
    logs = all_log_files(next(iter(candidates)))
    if not logs:
        raise ExtractError("Metric1 home UBIO directory contains no readable logs")
    return logs


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
    capacity, exact, policies = 0, None, set()
    sources = []
    for path in paths:
        with open_text(path) as stream:
            for line_no, line in enumerate(stream, 1):
                match = STATE_RE.search(line)
                if match:
                    capacity = max(capacity, int(match.group(1)))
                    policies.add(match.group(2))
                    sources.append(f"{path}:{line_no}")
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
    if not capacity or len(policies) != 1:
        raise ExtractError(f"UBCC capacity/policy invalid: capacity={capacity} policies={sorted(policies)}")
    policy = next(iter(policies))
    if policy != "naive" and exact is None:
        raise ExtractError("spill policy lacks validated H64 exact-live marker")
    return {"policy": policy, "resident_capacity": capacity, "h64_exact_live": exact,
            "effective_unique": capacity if policy == "naive" else max(capacity, exact),
            "sources": sources}


def extract_run(run, base, policy):
    required = {"id", "metric", "tc", "repetition", "topology", "simulator_log_dir", "simout_dir"}
    missing = required - set(run)
    if missing:
        raise ExtractError(f"missing fields {sorted(missing)}")
    out = dict(run)
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
    if out["metric"] in (1, 2):
        out["profile"] = norm_profile(run.get("profile"))
    if out["metric"] == 1:
        if out["tc"] != 131 or out["topology"] != "8n1s":
            raise ExtractError("Metric1 requires TC131 topology 8n1s")
        rows = marker_rows(paths, "timer", "post_pressure_catalog_reuse")
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
        capacity_logs = discover_home_ubio_logs(
            simulator, out["tc"], home_node, home_socket)
        out["home_ubio_logs"] = [str(path) for path in capacity_logs]
        out["metrics"] = {"capacity": parse_capacity(capacity_logs), "timers": timer,
                           "mean_ns_per_operation": statistics.mean(x["ns_per_operation"] for x in timer)}
        expected_policy = "naive" if out["profile"] == "naive" else "spill"
        if out["metrics"]["capacity"]["policy"] != expected_policy:
            raise ExtractError(f"Metric1 profile {out['profile']} requires UBCC policy {expected_policy}, "
                               f"got {out['metrics']['capacity']['policy']}")
    elif out["metric"] == 2:
        if out["tc"] not in M2:
            raise ExtractError(f"Metric2 unsupported TC{out['tc']}")
        phase, topology, node, samples = M2[out["tc"]]
        if out["topology"] != topology:
            raise ExtractError(f"TC{out['tc']} requires topology {topology}")
        rows = marker_rows(paths, "latency", phase)
        if len(rows) != 1:
            raise ExtractError(f"Metric2 expected exactly one phase={phase}, got {len(rows)}")
        row = rows[0]
        if row["node"] != node or row["count"] != samples:
            raise ExtractError(f"TC{out['tc']} marker contract requires node={node} samples={samples}, got node={row['node']} samples={row['count']}")
        out["metrics"] = {"phase": phase, "node": node, "samples": samples,
                          "mean_ticks": row["ticks"], "frequency_hz": row["frequency_hz"],
                          "mean_ns": row["ticks"] * 1e9 / row["frequency_hz"], "source": row}
    else:
        if out["tc"] not in M3 or run.get("arm") not in ("ourcc", "ha-vi"):
            raise ExtractError("Metric3 requires TC228-235 and arm ourcc/ha-vi")
        if out["topology"] != "2n1s":
            raise ExtractError("Metric3 TC228-235 frozen contract requires topology 2n1s")
        if "pair" not in run or run.get("order") not in ("AB", "BA"):
            raise ExtractError("Metric3 requires explicit pair and order=AB/BA")
        out["arm"], out["pair"], out["order"] = run["arm"], str(run["pair"]), run["order"]
        metrics = {}
        for name, (kind, phase, reduction) in M3[out["tc"]].items():
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
    out["status"] = "VALID"
    return out


def requirement(manifest, name, default):
    value = manifest.get("requirements", {}).get(name, default)
    return value if isinstance(value, dict) else default


def aggregate_results(data, resolved, ingestion_issues, output_dir=None,
                      manifest=None, ingestion=None):
    """Apply the frozen formulas to already parsed, in-memory run records."""
    issues = list(ingestion_issues)
    # Duplicate logical slots are never silently selected.
    slots = defaultdict(list)
    for run in resolved:
        key = ((run["metric"], run["repetition"], run["tc"], run.get("profile")) if run["metric"] < 3 else
               (3, run["pair"], run["tc"], run["order"], run["arm"]))
        slots[key].append(run)
    bad_ids = set()
    for key, rows in slots.items():
        if len(rows) > 1:
            bad_ids.update(row["id"] for row in rows)
            issues.append({"severity": "ERROR", "code": "DUPLICATE_SLOT", "run_id": ",".join(row["id"] for row in rows),
                           "message": f"duplicate logical slot {key}"})
    resolved = [row for row in resolved if row["id"] not in bad_ids]

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

    has_errors = any(i["severity"] == "ERROR" for i in issues)
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
              "issues": issues,
              "ingestion": ingestion or {"attempted": len(resolved), "added": len(resolved),
                                           "rejected": 0, "duplicate_conflicted": len(bad_ids),
                                           "add_results": []}}
    return report, resolved, matrix, per_run, issues, code


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
        run_id, slot = self._identity(raw)
        self._infer(slot)
        fallback_id = run_id if run_id is not None else f"run-{len(self._add_results)}"
        result = {"status": "REJECTED", "run_id": fallback_id, "slot": list(slot) if slot else None}
        if run_id is not None and run_id in self._ids:
            issue = {"severity": "ERROR", "code": "DUPLICATE_RUN_ID", "run_id": run_id,
                     "message": "duplicate run id; original run retained"}
            result["issue"] = issue
            self._issues.append(issue)
            self._add_results.append(result)
            return json.loads(json.dumps(result))
        if run_id is not None:
            self._ids.add(run_id)
        try:
            parsed = extract_run(raw, self.base_dir, self.correctness_policy)
        except (ExtractError, OSError, ValueError, TypeError) as error:
            code = "EVIDENCE_INVALID" if slot is not None else "RUN_SCHEMA_INVALID"
            issue = {"severity": "ERROR", "code": code, "run_id": fallback_id,
                     "message": str(error)}
            result["issue"] = issue
            self._issues.append(issue)
            self._add_results.append(result)
            return json.loads(json.dumps(result))
        parsed_slot = self._identity(parsed)[1]
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
            result = {"status": "ADDED", "run_id": parsed["id"],
                      "slot": list(parsed_slot)}
        self._add_results.append(result)
        return json.loads(json.dumps(result))

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
            slot_counts[self._identity(row)[1]] += 1
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
        slot_counts[matrix._identity(row)[1]] += 1
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
    write_tsv(output_dir / "metric_matrix.tsv", matrix,
              ["metric", "level", "identity", "tc", "value", "unit", "status", "detail"])
    write_tsv(output_dir / "per-run_metrics.tsv", per_run,
              ["run_id", "metric", "tc", "repetition", "profile", "arm", "pair", "order", "value", "unit", "status"])
    write_tsv(output_dir / "issues.tsv", issues, ["severity", "code", "run_id", "message"])
    lines = ["# Metric 1/2/3 原始日志统一报告", "", f"总体状态：**{report['overall_status']}**", "",
             "| 指标 | 状态 |", "|---|---|", f"| Metric1 | {report['metric1']['status']} |",
             f"| Metric2 | {report['metric2']['status']} |", f"| Metric3 | {report['metric3']['status']} |", "",
             "Metric3 仅表示冻结可执行参考模型范围；delta = HA-VI - OurCC，严格大于 0 才通过。",
             "不执行 t-test，不生成 p-value，不做笛卡尔配对。", "", "## 矩阵", "",
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
