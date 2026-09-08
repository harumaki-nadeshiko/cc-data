#!/usr/bin/env python3
"""Read-only recent evidence extraction; run inside a networkless Docker.

Output retains event histograms (not deduplicated text), per-process counts,
raw timer records, source checksums and selected result manifests. Mount the
remote results as --root and the approved local replacement as --local.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from publication_log_stream import canonical_log, open_log

TOPOLOGIES = ("3n1s", "3n2s", "8n1s", "8n2s", "16n1s")
PHASES = dict(zip(range(142, 148), (
    "db_oltp_end_to_end", "db_btree_end_to_end", "db_wal_end_to_end",
    "faas_end_to_end", "graph_end_to_end", "feature_end_to_end")))
REPLACEMENTS = {(200, "3n2s", 143, role) for role in ("naive", "spill", "ideal")}
REPLACEMENTS |= {(200, "3n2s", 144, role) for role in ("spill", "ideal")}
REPLACEMENTS.add((200, "8n2s", 143, "naive"))
OUTER = re.compile(r"\[EP-PERF\]\s+kind=outer\s.*?latency_ps=(\d+)")
TIMER = re.compile(r"\[GUEST-TIMER\]\s+node=(\d+)\s+phase=(\S+)\s+operations=(\d+)\s+counter_ticks=(\d+)\s+counter_frequency_hz=(\d+)")


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def extract(root, tc, topology):
    result = json.loads((root / "result.json").read_text())
    if result.get("status") != "PASS" or result.get("return_code", 0) != 0:
        raise ValueError(f"not PASS: {root}")
    n, sockets = map(int, re.fullmatch(r"(\d+)n(\d+)s", topology).groups())
    hist, timers, sources, processes = Counter(), [], [], []

    def record(path):
        rel = str(path.relative_to(root))
        sources.append({"file": rel, "sha256": digest(path)})
        return rel

    record(root / "result.json")
    for node in range(n):
        base = root / f"gem5_tc{tc}_node{node}"
        path = canonical_log(base / "stderr.log")
        rel = record(path)
        count = 0
        with open_log(path) as stream:
            for line in stream:
                match = OUTER.search(line)
                if match:
                    hist[int(match[1])] += 1
                    count += 1
        processes.append({"node": node, "file": rel, "outer_events": count})
        path = canonical_log(root / f"simout_tc{tc}_node{node}.log")
        rel = record(path)
        with open_log(path) as stream:
            for line_no, line in enumerate(stream, 1):
                match = TIMER.search(line)
                if match and match[2] == PHASES[tc]:
                    timers.append({"plane": int(match[1]), "phase": match[2],
                                   "operations": int(match[3]), "counter_ticks": int(match[4]),
                                   "counter_frequency_hz": int(match[5]), "file": rel, "line": line_no})
    if not hist:
        raise ValueError(f"no completed Outer: {root}")
    if len(timers) != n * sockets or len({r['plane'] for r in timers}) != n * sockets:
        raise ValueError(f"incomplete/duplicate E2E planes: {root}: {len(timers)} expected {n*sockets}")
    if any(r['operations'] <= 0 or r['counter_frequency_hz'] <= 0 or r['counter_ticks'] <= 0 for r in timers):
        raise ValueError(f"invalid timer: {root}")
    path = canonical_log(root / f"ubio_tc{tc}_n0_s0/stdout.log")
    rel = record(path)
    stats = []
    with open_log(path) as stream:
        for line_no, line in enumerate(stream, 1):
            if "[UBCC-STATS]" in line:
                payload = json.loads(line.split("[UBCC-STATS]", 1)[1])
                stats.append({"file": rel, "line": line_no, "value": payload})
    capacities = {r['value']['residentCapacity'] for r in stats if 'residentCapacity' in r['value']}
    exact = [r['value']['h64ExactLiveCount'] for r in stats if r['value'].get('h64ExactLiveKnown') == 1]
    if len(capacities) != 1 or (result.get('role') != 'naive' and not exact) or len(set(exact)) > 1:
        raise ValueError(f"ambiguous capacity/exact live: {root}")
    return {"result": result, "sources": sources, "outer_processes": processes,
            "outer_histogram_ps": sorted(hist.items()), "outer_count": sum(hist.values()),
            "outer_sum_ps": sum(k*v for k, v in hist.items()),
            "timers": timers, "home0_socket0_stats": stats,
            "resident_capacity": capacities.pop(), "exact_live": exact[-1] if exact else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--local', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for pressure in (175, 200):
        for tc in range(142, 148):
            for topology in TOPOLOGIES:
                for role in ('naive', 'spill', 'ideal'):
                    key = (pressure, topology, tc, role)
                    name = f'p{pressure}_{topology}_tc{tc}_{role}'
                    if key == (175, '8n2s', 143, 'naive'):
                        if args.local is None:
                            continue  # Explicit separate local extraction, never substitute old failed arm.
                        root = args.local
                        source = 'tc143-rmbefore-replacement-local-20260907'
                    else:
                        batch = ('metric1-replacements-20260907' if key in REPLACEMENTS
                                 else 'traceperf-rootfix-p175-p200-20260907')
                        source = f'{batch}/metric1/{name}'
                        root = args.root / source
                    print(name, flush=True)
                    rows.append({"pressure_pct": pressure, "tc": tc, "topology": topology,
                                 "role": role, "source_id": source, **extract(root, tc, topology)})
    report = args.root / 'metric23-single-20260907/metric2_single_report.json'
    output = {"schema_version": 1, "selected_arms": rows,
              "metric2_original": json.loads(report.read_text()),
              "metric2_source": {"source_id": 'metric23-single-20260907/metric2_single_report.json',
                                 "sha256": digest(report)}}
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
