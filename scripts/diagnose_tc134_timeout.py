#!/usr/bin/env python3
"""Diagnose TC134 stalls from guest, UBIO, networksim, and supervisor logs."""

import argparse
import gzip
import json
import pathlib
import re
import sys
from collections import Counter, defaultdict


NODES = 8
SOCKETS = 2
PLANES = NODES * SOCKETS
HOT = 4096
WRITER_LINES = 8192
PROGRESS_STEP = 1024
SEG_SIZE = 0x8000000
WINDOW_BASE = 0x000000
STREAM_BASE = 0x1000000

PHASE_RE = re.compile(
    r"\[PHASE\]\s+node=(\d+)\s+phase=([^\s]+)\s+status=done")
PROGRESS_RE = re.compile(
    r"\[PROGRESS\]\s+node=(\d+)\s+phase=([^\s]+)\s+iter=(\d+)")
TRACE_RE = re.compile(
    r"\[TRACE-PERF\]\s+(\d+)\|(\d+)\|(\w+)\|(\d+)\|"
    r"(0x[0-9a-fA-F]+|\d+)\|(\w+)\|([^\s|]+)")
FIELD_RE = re.compile(r"([A-Za-z][A-Za-z0-9_]*)=([^\s,]+)")
TICK_RE = re.compile(r"\btick=(\d+)")
PA_RE = re.compile(r"\b(?:pa|victim)=0x([0-9a-fA-F]+)")
REQID_RE = re.compile(r"\breqId=(\d+)")

FILL_ISSUED = "RESIDENT-FILL-ISSUED"
FILL_DONE = "RESIDENT-FILL-DONE"
SPILL_START = "RESIDENT-SPILL-START"
SPILL_DONE = "RESIDENT-SPILL-DONE"
WAITER_ENQ = "RESIDENT-WAITER-ENQ"
WAITER_REPLAY = "RESIDENT-WAITER-REPLAY"
WAITER_RETIRE = "RESIDENT-WAITER-RETIRE-COMMITTED"


def open_text(path):
    if path.name.endswith(".gz"):
        return gzip.open(path, "rt", errors="replace")
    return path.open(errors="replace")


def log_files(root):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        if (path.suffix in {".log", ".txt", ".out"} or
                name.endswith((".log.gz", ".txt.gz")) or
                name.startswith("simout")):
            yield path


def parse_int(value):
    try:
        return int(value, 0)
    except (TypeError, ValueError):
        return None


def plane_identity(plane):
    return {"plane": plane, "physical_node": plane // SOCKETS,
            "socket": plane % SOCKETS,
            "role": "writer" if plane % SOCKETS == 0 else "sharer"}


def stream_offset(node, line):
    return STREAM_BASE + (node * WRITER_LINES + line) * 64


def offset_in_segment(pa):
    return pa % SEG_SIZE


def in_offset_range(pa, start, end):
    offset = offset_in_segment(pa)
    return start <= offset < end


def event(path, line_number, line, **extra):
    item = {"file": str(path), "line": line_number,
            "text": line.strip()[:800]}
    item.update(extra)
    return item


def analyze(root, sample_limit=8, progress_step=PROGRESS_STEP):
    phases = defaultdict(set)
    progress = defaultdict(dict)
    traces = defaultdict(list)
    generic_by_reqid = defaultdict(list)
    fills = defaultdict(lambda: {"issued": [], "done": []})
    spills = defaultdict(lambda: {"start": [], "done": []})
    waiters = defaultdict(lambda: {"enq": [], "replay": [], "retire": []})
    component_ticks = defaultdict(int)
    marker_counts = Counter()
    supervisor = []
    scanned_files = 0
    scanned_lines = 0
    tc134_evidence = 0

    paths = sorted(log_files(root))
    for path in paths:
        scanned_files += 1
        with open_text(path) as stream:
            for line_number, line in enumerate(stream, 1):
                scanned_lines += 1
                if "test=TC134" in line or "window_share" in line or \
                        "window_pressure" in line or "window_reuse" in line:
                    tc134_evidence += 1
                match = PHASE_RE.search(line)
                if match:
                    plane = int(match.group(1))
                    phases[plane].add(match.group(2))
                match = PROGRESS_RE.search(line)
                if match:
                    plane = int(match.group(1))
                    phase = match.group(2)
                    iteration = int(match.group(3))
                    progress[plane][phase] = max(
                        iteration, progress[plane].get(phase, 0))

                match = TRACE_RE.search(line)
                if match:
                    timestamp = int(match.group(1))
                    reqid = int(match.group(4))
                    pa = int(match.group(5), 0)
                    trace = event(
                        path, line_number, line, timestamp=timestamp,
                        actor=int(match.group(2)), component=match.group(3),
                        reqid=reqid, pa=pa, stage=match.group(6),
                        message_type=match.group(7))
                    traces[reqid].append(trace)
                    component_ticks[match.group(3)] = max(
                        component_ticks[match.group(3)], timestamp)

                tick_matches = list(TICK_RE.finditer(line))
                if tick_matches:
                    component = "networksim" if "NetworkSim" in line or "NSIM" in line else (
                        "ubio" if "UBIO" in line or "UBCC" in line or
                        "RESIDENT-" in line else "other")
                    component_ticks[component] = max(
                        component_ticks[component], int(tick_matches[-1].group(1)))

                pa_match = PA_RE.search(line)
                reqid_match = REQID_RE.search(line)
                pa = int(pa_match.group(1), 16) if pa_match else None
                reqid = int(reqid_match.group(1)) if reqid_match else None
                if reqid is not None and pa is not None:
                    generic_by_reqid[reqid].append(
                        event(path, line_number, line, reqid=reqid, pa=pa))

                if pa is not None:
                    if FILL_ISSUED in line:
                        fills[pa]["issued"].append(event(path, line_number, line))
                        marker_counts[FILL_ISSUED] += 1
                    if FILL_DONE in line:
                        fills[pa]["done"].append(event(path, line_number, line))
                        marker_counts[FILL_DONE] += 1
                    if SPILL_START in line:
                        spills[pa]["start"].append(event(path, line_number, line))
                        marker_counts[SPILL_START] += 1
                    if SPILL_DONE in line:
                        spills[pa]["done"].append(event(path, line_number, line))
                        marker_counts[SPILL_DONE] += 1
                    if WAITER_ENQ in line:
                        waiters[pa]["enq"].append(event(path, line_number, line))
                        marker_counts[WAITER_ENQ] += 1
                    if WAITER_REPLAY in line:
                        waiters[pa]["replay"].append(event(path, line_number, line))
                        marker_counts[WAITER_REPLAY] += 1
                    if WAITER_RETIRE in line:
                        waiters[pa]["retire"].append(event(path, line_number, line))
                        marker_counts[WAITER_RETIRE] += 1

                if "supervisor" in path.name or "progress_stall:" in line or \
                        "outer_timeout" in line:
                    if "FAULT" in line or "timeout" in line.lower() or \
                            line.startswith("OK "):
                        supervisor.append(event(path, line_number, line))

    if tc134_evidence == 0:
        return {
            "schema_version": 1,
            "log_dir": str(root),
            "scanned_files": scanned_files,
            "scanned_lines": scanned_lines,
            "tc134_evidence": 0,
            "identity_note": (
                "Guest marker node is a plane ID: plane=physical_node*2+socket. "
                "Odd plane 3 means physical node 1 socket 1, not physical node 3."),
            "summary_diagnosis": "NO_TC134_EVIDENCE",
            "all_sharers_done": False,
            "completed_writers": 0,
            "expected_writers": NODES,
            "planes": [],
            "suspects": [],
            "resident_marker_counts": dict(marker_counts),
            "component_max_ticks": dict(component_ticks),
            "supervisor_tail": supervisor[-sample_limit:],
            "trace_available": bool(traces),
            "recommendation": "Point the tool at the TC134 LOG_BASE directory.",
        }

    plane_rows = []
    incomplete_writers = []
    for plane in range(PLANES):
        identity = plane_identity(plane)
        row = dict(identity)
        row["phases"] = sorted(phases.get(plane, set()))
        row["window_pressure_iter"] = progress.get(plane, {}).get(
            "window_pressure", 0)
        row["window_pressure_started"] = (
            "window_pressure" in progress.get(plane, {}))
        row["share_barrier_entered"] = (
            "window_share_barrier_enter" in progress.get(plane, {}))
        row["window_reuse_iter"] = progress.get(plane, {}).get(
            "window_reuse", 0)
        if identity["role"] == "writer":
            row["pressure_complete"] = row["window_pressure_iter"] >= WRITER_LINES
            if not row["pressure_complete"]:
                incomplete_writers.append(row)
        else:
            row["share_complete"] = "window_share" in phases.get(plane, set())
        plane_rows.append(row)

    suspects = []
    for writer in incomplete_writers:
        node = writer["physical_node"]
        completed = min(writer["window_pressure_iter"], WRITER_LINES)
        interval_end = min(
            WRITER_LINES,
            ((completed // progress_step) + 1) * progress_step)
        if completed == WRITER_LINES:
            interval_end = WRITER_LINES
        start_offset = stream_offset(node, completed)
        end_offset = stream_offset(node, interval_end)
        matching_reqids = set()
        matching_events = []
        for reqid, req_events in traces.items():
            for trace in req_events:
                if trace["pa"] and in_offset_range(
                        trace["pa"], start_offset, end_offset):
                    matching_reqids.add(reqid)
                    matching_events.append(trace)
        for reqid, req_events in generic_by_reqid.items():
            for item in req_events:
                if in_offset_range(item["pa"], start_offset, end_offset):
                    matching_reqids.add(reqid)
                    matching_events.append(item)

        unresolved_fills = []
        for pa, records in fills.items():
            if in_offset_range(pa, start_offset, end_offset) and \
                    len(records["issued"]) > len(records["done"]):
                unresolved_fills.append({
                    "pa": f"0x{pa:x}", "offset": f"0x{offset_in_segment(pa):x}",
                    "issued": len(records["issued"]), "done": len(records["done"]),
                    "last": (records["issued"] + records["done"])[-1]})
        unresolved_spills = []
        for pa, records in spills.items():
            if in_offset_range(pa, start_offset, end_offset) and \
                    len(records["start"]) > len(records["done"]):
                unresolved_spills.append({
                    "pa": f"0x{pa:x}", "offset": f"0x{offset_in_segment(pa):x}",
                    "start": len(records["start"]), "done": len(records["done"]),
                    "last": (records["start"] + records["done"])[-1]})
        unresolved_waiters = []
        for pa, records in waiters.items():
            resolved = len(records["replay"]) + len(records["retire"])
            if in_offset_range(pa, start_offset, end_offset) and \
                    len(records["enq"]) > resolved:
                unresolved_waiters.append({
                    "pa": f"0x{pa:x}", "offset": f"0x{offset_in_segment(pa):x}",
                    "enq": len(records["enq"]), "resolved_markers": resolved,
                    "last": (records["enq"] + records["replay"] +
                             records["retire"])[-1]})

        req_summaries = []
        for reqid in sorted(matching_reqids):
            req_traces = sorted(traces.get(reqid, []),
                                key=lambda item: (item["timestamp"], item["file"],
                                                  item["line"]))
            req_generic = generic_by_reqid.get(reqid, [])
            req_summaries.append({
                "reqid": reqid,
                "trace_event_count": len(req_traces),
                "trace_stages": [
                    f"{item['component']}:{item['stage']}:{item['message_type']}"
                    for item in req_traces],
                "last_trace": req_traces[-1] if req_traces else None,
                "last_generic": req_generic[-1] if req_generic else None,
            })

        if writer["share_barrier_entered"] and not writer["window_pressure_started"]:
            diagnosis = "GUEST_STUCK_AT_POST_SHARE_BARRIER"
        elif not writer["share_barrier_entered"] and not writer["window_pressure_started"]:
            diagnosis = "LEGACY_LOG_CANNOT_SEPARATE_BARRIER_FROM_FIRST_CHUNK"
        elif unresolved_fills:
            diagnosis = "CANDIDATE_HOME_RESIDENT_FILL_NOT_COMPLETED"
        elif unresolved_spills:
            diagnosis = "CANDIDATE_HOME_RESIDENT_SPILL_NOT_COMPLETED"
        elif unresolved_waiters:
            diagnosis = "CANDIDATE_HOME_RESIDENT_WAITER_NOT_REPLAYED"
        elif req_summaries and any(summary["trace_event_count"] for summary in req_summaries):
            diagnosis = "WRITER_TRANSACTION_STALL_SEE_LAST_TRACE_STAGE"
        else:
            diagnosis = "EVIDENCE_GAP_ENABLE_TRANSACTION_TRACE_FOR_SUSPECT_RANGE"

        suspects.append({
            "writer_plane": writer["plane"],
            "physical_node": node,
            "socket": 0,
            "paired_sharer_plane": writer["plane"] + 1,
            "paired_sharer_share_complete":
                "window_share" in phases.get(writer["plane"] + 1, set()),
            "completed_lines": completed,
            "suspect_line_begin": completed,
            "suspect_line_end_exclusive": interval_end,
            "suspect_offset_begin": f"0x{start_offset:x}",
            "suspect_offset_end_exclusive": f"0x{end_offset:x}",
            "diagnosis": diagnosis,
            "matching_reqids": req_summaries[-sample_limit:],
            "matching_event_samples": matching_events[-sample_limit:],
            "unresolved_fills": unresolved_fills[-sample_limit:],
            "unresolved_spills": unresolved_spills[-sample_limit:],
            "unresolved_waiters": unresolved_waiters[-sample_limit:],
        })

    all_sharers_done = all(
        "window_share" in phases.get(plane, set()) for plane in range(1, PLANES, 2))
    completed_writer_count = sum(
        1 for plane in range(0, PLANES, 2)
        if progress.get(plane, {}).get("window_pressure", 0) >= WRITER_LINES)
    summary_diagnosis = ""
    if len(incomplete_writers) == 1 and all_sharers_done:
        summary_diagnosis = "ONE_WRITER_BLOCKS_GLOBAL_POST_SHARE_BARRIER"
    elif incomplete_writers and all_sharers_done:
        summary_diagnosis = "MULTIPLE_WRITERS_BLOCK_GLOBAL_POST_SHARE_BARRIER"
    elif not all_sharers_done:
        summary_diagnosis = "WINDOW_SHARE_PHASE_INCOMPLETE"
    elif completed_writer_count == NODES:
        summary_diagnosis = "PRESSURE_COMPLETE_CHECK_POST_PRESSURE_BARRIER_OR_REUSE"
    else:
        summary_diagnosis = "INSUFFICIENT_GUEST_PROGRESS_EVIDENCE"

    return {
        "schema_version": 1,
        "log_dir": str(root),
        "scanned_files": scanned_files,
        "scanned_lines": scanned_lines,
        "tc134_evidence": tc134_evidence,
        "identity_note": (
            "Guest marker node is a plane ID: plane=physical_node*2+socket. "
            "Odd plane 3 means physical node 1 socket 1, not physical node 3."),
        "summary_diagnosis": summary_diagnosis,
        "all_sharers_done": all_sharers_done,
        "completed_writers": completed_writer_count,
        "expected_writers": NODES,
        "planes": plane_rows,
        "suspects": suspects,
        "resident_marker_counts": dict(marker_counts),
        "component_max_ticks": dict(component_ticks),
        "supervisor_tail": supervisor[-sample_limit:],
        "trace_available": bool(traces),
        "progress_step": progress_step,
        "recommendation": (
            "If diagnosis is EVIDENCE_GAP, rerun with bounded TRACE-PERF or add "
            "unconditional ReadReq/ReadResp forensic markers for the reported "
            "offset interval; do not change HWM based on window_pressure iter=8192."),
    }


def print_human(report):
    print(f"TC134 diagnosis: {report['summary_diagnosis']}")
    print(f"identity: {report['identity_note']}")
    if report["summary_diagnosis"] == "NO_TC134_EVIDENCE":
        print(f"next: {report['recommendation']}")
        return
    print(f"window sharers complete: {report['all_sharers_done']}")
    print(f"writers at 8192: {report['completed_writers']}/"
          f"{report['expected_writers']}")
    print("plane matrix:")
    for row in report["planes"]:
        if row["role"] == "writer":
            state = (f"barrier_enter={int(row['share_barrier_entered'])} "
                     f"pressure_started={int(row['window_pressure_started'])} "
                     f"pressure={row['window_pressure_iter']}/8192")
        else:
            state = f"share_done={int(row.get('share_complete', False))}"
        phases = ",".join(row["phases"]) or "-"
        print(f"  plane={row['plane']:2d} physical_node={row['physical_node']} "
              f"socket={row['socket']} role={row['role']} {state} phases={phases}")
    for suspect in report["suspects"]:
        print(
            f"suspect writer: plane={suspect['writer_plane']} "
            f"physical_node={suspect['physical_node']} socket=0 "
            f"completed={suspect['completed_lines']} "
            f"line_range=[{suspect['suspect_line_begin']},"
            f"{suspect['suspect_line_end_exclusive']}) "
            f"offset_range=[{suspect['suspect_offset_begin']},"
            f"{suspect['suspect_offset_end_exclusive']}) "
            f"diagnosis={suspect['diagnosis']}")
        print(f"  paired sharer plane={suspect['paired_sharer_plane']} "
              f"share_done={int(suspect['paired_sharer_share_complete'])}")
        for kind in ("unresolved_fills", "unresolved_spills", "unresolved_waiters"):
            for item in suspect[kind]:
                print(f"  {kind}: {json.dumps(item, sort_keys=True)}")
        for req in suspect["matching_reqids"]:
            print(f"  reqId={req['reqid']} trace_events={req['trace_event_count']} "
                  f"stages={','.join(req['trace_stages']) or '-'}")
    ticks = " ".join(f"{key}={value}" for key, value in
                     sorted(report["component_max_ticks"].items())) or "none"
    print(f"component max ticks: {ticks}")
    print(f"transaction trace available: {report['trace_available']}")
    print(f"next: {report['recommendation']}")


def main():
    parser = argparse.ArgumentParser(
        description="Diagnose an 8-node, 2-socket TC134 timeout")
    parser.add_argument("log_dir")
    parser.add_argument("--sample-limit", type=int, default=8)
    parser.add_argument("--progress-step", type=int, default=PROGRESS_STEP,
                        help="TC134_PROGRESS_STEP used to build the workload")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = pathlib.Path(args.log_dir).resolve()
    if not root.is_dir():
        parser.error(f"not a directory: {root}")
    if args.progress_step <= 0:
        parser.error("--progress-step must be positive")
    report = analyze(root, args.sample_limit, args.progress_step)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
