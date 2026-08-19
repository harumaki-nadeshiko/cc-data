#!/usr/bin/env python3
"""Summarize TC98 progress, epoch safety, protocol state, and shutdown."""

import argparse
import gzip
import json
import pathlib
import re
import sys
from collections import Counter, defaultdict


PROGRESS_RE = re.compile(r"\[TC98_PROGRESS\] node=(\d+) sock=(\d+) r=(\d+)")
READ_RE = re.compile(
    r"\[READ_VAL\].*expected=([0-9a-fA-F]+) actual=([0-9a-fA-F]+) "
    r"(MATCH|MISMATCH)")
COMMIT_RE = re.compile(
    r"commitIntendedResult PA=0x([0-9a-fA-F]+) "
    r"(?:path=([A-Za-z0-9_]+) )?.*?epoch=(\d+)")
OWNER_RE = re.compile(r"owner=(-?\d+)")
EPOCH_DECREASE_RE = re.compile(r"epoch DECREASED\s+(\d+)\s*->\s*(\d+)")
TICK_RE = re.compile(r"(?:^|[\s|])tick[=:](\d+)")
GEM5_TICK_RE = re.compile(r"^\s*(\d+):")
UBIO_PATH_RE = re.compile(r"ubio_tc\d+_n(\d+)_s(\d+)")
PEER_CLOSE_RE = re.compile(r"\[PEER-EXIT-CLOSE\] local=(\d+):(\d+)")
NETWORK_ACK_RE = re.compile(r"\[NETWORK-EXIT-ACK-RECV\] local=(\d+):(\d+)")
NSIM_ACK_RE = re.compile(r"\[NSIM-NETWORK-EXIT-ACK-SEND\] mod=(\d+).+sent=1 fifo=0")
PROFILE_RE = re.compile(r"\[EPBACKEND-PROFILE\].*clear_profile=([^\s]+)")
GRANT_MISMATCH_RE = re.compile(
    r"\[UBCC-GRANT-RETRY-TUPLE-MISMATCH\].*home=(\d+).*"
    r"pa=0x([0-9a-fA-F]+).*requester=(\d+).*incomingSocket=(\d+).*"
    r"incomingReqId=(0x[0-9a-fA-F]+|\d+).*outstandingSocket=(\d+).*"
    r"outstandingReqId=(0x[0-9a-fA-F]+|\d+)")
REQID_RE = re.compile(r"(?:reqId|requestId)=(0x[0-9a-fA-F]+|\d+)")

CLEAR_CHAIN_PATTERNS = {
    "grant_ready": re.compile(r"\[(?:UBCC-GRANT-READY|ADAPTER-GOT-RESP)\]"),
    "pending_read_saved": re.compile(r"PENDING-READ-SAVE"),
    "pending_read_hit": re.compile(r"PENDING-READ-HIT"),
    "pending_read_conflict": re.compile(r"PENDING-READ-CONFLICT"),
    "pending_grant_saved": re.compile(r"PENDING-GRANT-SAVE|phase=grant_received"),
    "pending_grant_erased": re.compile(r"PENDING-GRANT-ERASE"),
    "pending_grant_overwrite_blocked": re.compile(
        r"PENDING-GRANT-OVERWRITE-BLOCKED"),
    "clear_send": re.compile(r"\[CLEAR-SEND\]|\[CLR-TX\]|phase=clear_queued"),
    "clear_transport_handoff": re.compile(r"phase=transport_handoff"),
    "network_clear": re.compile(r"(?:TRACE-PERF|NSIM-FWD-ALL).*ClearReq"),
    "home_clear": re.compile(r"\[HOME-CLEAR-COMMIT\]|processClear"),
    "clear_commit": re.compile(r"commitIntendedResult.*path=Clear"),
    "clear_response": re.compile(r"ClearResp|\[CLR-CACHE-HIT\]"),
    "pending_key_drift": re.compile(r"PENDING-GRANT-KEY-DRIFT"),
}

ISSUE_PATTERNS = {
    "epoch_decreased": re.compile(r"epoch DECREASED"),
    "upgrade_done_tuple_mismatch": re.compile(r"UpgradeDone tuple mismatch"),
    "invalidate_ack_reqid_mismatch": re.compile(
        r"processInvalidationAck.*reqId mismatch"),
    "reservation_superseded": re.compile(r"UBCC-RESERVATION-SUPERSEDED"),
    "grant_retry_tuple_mismatch": re.compile(
        r"UBCC-GRANT-RETRY-TUPLE-MISMATCH"),
    "clear_epoch_mismatch": re.compile(r"processClear.*epoch mismatch"),
    "clear_reqid_mismatch": re.compile(r"processClear.*reqId mismatch"),
    "panic": re.compile(r"(?:^|\b)(?:Panic|panic:|PANIC|fatal:|FATAL:)", re.I),
}

PROTOCOL_PATTERNS = {
    "upgrade_created": re.compile(r"\[UBCC-UPGRADE\]"),
    "upgrade_committed": re.compile(r"\[UBCC-UPGRADE-COMMIT\]"),
    "invalidation_done": re.compile(r"\[UBCC-INV-DONE\]"),
    "recall_started": re.compile(r"\[RECALL-TRACE-A\]"),
    "recall_completed": re.compile(r"\[RECALL-DIAG\]"),
    "queue_enqueued": re.compile(r"\[UBCC-QUEUE\].*action=enqueue"),
    "queue_replayed": re.compile(r"\[(?:PUSH-GRANT-TRY|RESIDENT-WAITER-REPLAY)\]"),
}


def open_text(path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", errors="replace")
    return path.open("rt", errors="replace")


def log_files(root):
    for path in root.rglob("*"):
        if path.is_file() and (path.name.startswith("simout") or
                               path.suffix in {".log", ".txt"} or
                               path.name.endswith(".log.gz")):
            yield path


def add_sample(samples, kind, path, line_number, line, limit):
    if len(samples[kind]) < limit:
        samples[kind].append({
            "file": str(path),
            "line": line_number,
            "text": line.rstrip()[:500],
        })


def analyze(args):
    root = pathlib.Path(args.log_dir).resolve()
    simout_root = None
    if getattr(args, "simout_dir", None):
        simout_root = pathlib.Path(args.simout_dir).resolve()
    expected_planes = {
        (node, socket)
        for node in range(args.num_nodes)
        for socket in range(args.num_sockets)
    }
    expected_plane_count = len(expected_planes)
    expected_last_progress = ((args.rounds - 1) // 4) * 4

    progress = {}
    reads = {"match": 0, "mismatch": 0}
    commits = defaultdict(list)
    commit_owners = defaultdict(Counter)
    commit_paths = Counter()
    clear_profiles = Counter()
    grant_mismatches = Counter()
    clear_chain = defaultdict(Counter)
    issues = Counter()
    issue_samples = defaultdict(list)
    protocol = Counter()
    last_ticks = {}
    peer_closes = set()
    network_acks = set()
    nsim_acks = set()
    verify_pass = False
    verify_fail = False
    timeout_seen = False
    stall_seen = False
    scanned_files = 0
    scanned_lines = 0

    scan_roots = [root]
    if simout_root is not None and simout_root != root:
        scan_roots.append(simout_root)
    paths = {}
    for scan_root in scan_roots:
        if scan_root.is_dir():
            for path in log_files(scan_root):
                paths[str(path.resolve())] = path

    for path in sorted(paths.values()):
        scanned_files += 1
        plane_match = UBIO_PATH_RE.search(str(path))
        plane = tuple(map(int, plane_match.groups())) if plane_match else None
        last_tick = None
        with open_text(path) as stream:
            for line_number, line in enumerate(stream, 1):
                scanned_lines += 1
                match = PROGRESS_RE.search(line)
                if match:
                    key = (int(match.group(1)), int(match.group(2)))
                    progress[key] = max(progress.get(key, -1), int(match.group(3)))

                match = PROFILE_RE.search(line)
                if match:
                    clear_profiles[match.group(1)] += 1

                match = GRANT_MISMATCH_RE.search(line)
                if match:
                    key = (
                        int(match.group(1)), int(match.group(2), 16),
                        int(match.group(3)), int(match.group(4)),
                        int(match.group(5), 0), int(match.group(6)),
                        int(match.group(7), 0),
                    )
                    grant_mismatches[key] += 1

                reqids = {int(value, 0) for value in REQID_RE.findall(line)}
                if reqids:
                    for stage, pattern in CLEAR_CHAIN_PATTERNS.items():
                        if pattern.search(line):
                            for reqid in reqids:
                                clear_chain[reqid][stage] += 1

                match = READ_RE.search(line)
                if match:
                    reads["match" if match.group(3) == "MATCH" else "mismatch"] += 1

                match = COMMIT_RE.search(line)
                if match:
                    pa = int(match.group(1), 16)
                    commit_path = match.group(2) or "Legacy"
                    epoch = int(match.group(3))
                    commits[pa].append(epoch)
                    commit_paths[commit_path] += 1
                    owner = OWNER_RE.search(line)
                    if owner:
                        commit_owners[pa][int(owner.group(1))] += 1

                match = EPOCH_DECREASE_RE.search(line)
                if match:
                    issues["epoch_decreased"] += 1
                    add_sample(issue_samples, "epoch_decreased", path,
                               line_number, line, args.sample_limit)

                for kind, pattern in ISSUE_PATTERNS.items():
                    if kind == "epoch_decreased":
                        continue
                    if pattern.search(line):
                        issues[kind] += 1
                        add_sample(issue_samples, kind, path, line_number,
                                   line, args.sample_limit)

                for kind, pattern in PROTOCOL_PATTERNS.items():
                    if pattern.search(line):
                        protocol[kind] += 1

                match = PEER_CLOSE_RE.search(line)
                if match:
                    peer_closes.add((int(match.group(1)), int(match.group(2))))
                match = NETWORK_ACK_RE.search(line)
                if match:
                    network_acks.add((int(match.group(1)), int(match.group(2))))
                match = NSIM_ACK_RE.search(line)
                if match:
                    nsim_acks.add(int(match.group(1)))

                if ">>> TC98 PASSED <<<" in line:
                    verify_pass = True
                if "TC98 FAILED" in line or ">>> TC98 FAILED <<<" in line:
                    verify_fail = True
                if "TIMEOUT" in line:
                    timeout_seen = True
                if ("SUPERVISOR STALL" in line or "STALL TIMEOUT" in line or
                        "TC98 STALL" in line or "stalled for" in line.lower()):
                    stall_seen = True

                tick = TICK_RE.search(line)
                if tick:
                    last_tick = int(tick.group(1))
                else:
                    tick = GEM5_TICK_RE.search(line)
                    if tick:
                        last_tick = int(tick.group(1))
        if plane is not None and last_tick is not None:
            last_ticks[plane] = max(last_ticks.get(plane, 0), last_tick)

    hot_pa = args.hot_pa
    if hot_pa is None and commits:
        hot_pa = max(commits, key=lambda pa: len(commits[pa]))
    hot_epochs = commits.get(hot_pa, []) if hot_pa is not None else []
    epoch_drops = []
    epoch_duplicates = 0
    epoch_gaps = 0
    for previous, current in zip(hot_epochs, hot_epochs[1:]):
        if current < previous:
            epoch_drops.append((previous, current))
        elif current == previous:
            epoch_duplicates += 1
        elif current > previous + 1:
            epoch_gaps += 1

    child_dir = root / "child_status_tc98"
    child_exits = {}
    if child_dir.is_dir():
        for path in sorted(child_dir.glob("*.exit")):
            try:
                child_exits[path.name] = int(path.read_text().strip())
            except (OSError, ValueError):
                child_exits[path.name] = None
    expected_child_count = args.num_nodes + expected_plane_count + 1
    child_nonzero = {
        name: code for name, code in child_exits.items() if code != 0
    }

    progress_values = [progress.get(plane, -1) for plane in expected_planes]
    progress_complete = {
        plane for plane in expected_planes
        if progress.get(plane, -1) >= expected_last_progress
    }
    progress_min = min(progress_values) if progress_values else -1
    progress_max = max(progress_values) if progress_values else -1
    progress_skew = progress_max - progress_min

    critical_count = (
        issues["epoch_decreased"] + issues["upgrade_done_tuple_mismatch"] +
        issues["invalidate_ack_reqid_mismatch"] +
        issues["reservation_superseded"] + issues["panic"] +
        reads["mismatch"] + len(child_nonzero)
    )
    shutdown_complete = (
        peer_closes == expected_planes and network_acks == expected_planes and
        nsim_acks == set(range(expected_plane_count))
    )
    child_complete = (
        len(child_exits) == expected_child_count and not child_nonzero
    )

    if critical_count or verify_fail:
        status = "FAIL"
    elif (verify_pass and reads["match"] == expected_plane_count and
          progress_complete == expected_planes and child_complete and
          shutdown_complete):
        status = "PASS"
    elif stall_seen or (progress_max >= 4 and progress_skew >= 8):
        status = "STALLED"
    elif timeout_seen and progress_min >= 0 and progress_skew <= 4 and hot_epochs:
        status = "HEALTHY_TIMEOUT"
    elif progress_min >= 0 and progress_skew <= 4 and hot_epochs:
        status = "HEALTHY_PROGRESS"
    else:
        status = "INCOMPLETE"

    recommendations = []
    top_mismatch = None
    if grant_mismatches:
        key, count = grant_mismatches.most_common(1)[0]
        (home, pa, requester, incoming_socket, incoming_reqid,
         outstanding_socket, outstanding_reqid) = key
        top_mismatch = {
            "count": count,
            "home": home,
            "pa": f"0x{pa:x}",
            "requester": requester,
            "incoming_socket": incoming_socket,
            "incoming_reqid": incoming_reqid,
            "outstanding_socket": outstanding_socket,
            "outstanding_reqid": outstanding_reqid,
            "outstanding_clear_chain": dict(clear_chain[outstanding_reqid]),
            "incoming_clear_chain": dict(clear_chain[incoming_reqid]),
        }
    if status == "PASS":
        recommendations.append("TC98 completed normally; no recovery action is needed.")
    if issues["epoch_decreased"] or epoch_drops:
        recommendations.append(
            "Epoch rollback detected; inspect the first epoch_decreased sample and UBIO home plane.")
    if issues["upgrade_done_tuple_mismatch"]:
        recommendations.append(
            "UpgradeDone tuple mismatch detected; compare incoming baseEpoch/reqId with active tuple.")
    if issues["reservation_superseded"]:
        recommendations.append(
            "A reservation was superseded; inspect its path and pending replay ordering.")
    if status == "HEALTHY_TIMEOUT":
        recommendations.append(
            "The run timed out but progress is balanced and epochs are monotonic; increase TC98 timeout.")
    if status == "STALLED":
        recommendations.append(
            "Progress is skewed or a stall marker exists; inspect the slowest planes and their last protocol state.")
    if status == "INCOMPLETE":
        recommendations.append(
            "Evidence is incomplete; preserve verify, simout, UBIO, networksim, and child_status logs.")
    if top_mismatch:
        old_chain = top_mismatch["outstanding_clear_chain"]
        if old_chain.get("home_clear", 0) == 0:
            recommendations.append(
                "The dominant outstanding reqId has no Home Clear evidence; inspect its Clear send and routing chain.")
        elif old_chain.get("clear_commit", 0) == 0:
            recommendations.append(
                "The dominant outstanding reqId reached Home but did not commit; inspect processClear rejection logs.")

    return {
        "status": status,
        "log_dir": str(root),
        "simout_dir": str(simout_root) if simout_root is not None else str(root),
        "scanned": {"files": scanned_files, "lines": scanned_lines},
        "topology": {
            "nodes": args.num_nodes,
            "sockets": args.num_sockets,
            "planes": expected_plane_count,
            "rounds": args.rounds,
        },
        "verifier": {"pass": verify_pass, "fail": verify_fail},
        "clear_profiles": dict(clear_profiles),
        "progress": {
            "expected_last_marker": expected_last_progress,
            "complete_planes": len(progress_complete),
            "min_round_marker": progress_min,
            "max_round_marker": progress_max,
            "skew": progress_skew,
            "planes": {
                f"{node}:{socket}": progress.get((node, socket), -1)
                for node, socket in sorted(expected_planes)
            },
        },
        "done_markers": reads,
        "hot_line": {
            "pa": f"0x{hot_pa:x}" if hot_pa is not None else None,
            "commits": len(hot_epochs),
            "first_epoch": hot_epochs[0] if hot_epochs else None,
            "last_epoch": hot_epochs[-1] if hot_epochs else None,
            "max_epoch": max(hot_epochs) if hot_epochs else None,
            "monotonic": not epoch_drops,
            "decreases": epoch_drops[:args.sample_limit],
            "duplicates": epoch_duplicates,
            "gaps": epoch_gaps,
            "owners": dict(sorted(commit_owners.get(hot_pa, {}).items())),
        },
        "protocol_counts": dict(protocol),
        "commit_paths": dict(commit_paths),
        "issues": dict(issues),
        "grant_retry_tuple_mismatches": {
            "total": sum(grant_mismatches.values()),
            "unique": len(grant_mismatches),
            "top": top_mismatch,
        },
        "issue_samples": dict(issue_samples),
        "last_ticks": {
            f"{node}:{socket}": tick
            for (node, socket), tick in sorted(last_ticks.items())
        },
        "shutdown": {
            "peer_exit": len(peer_closes),
            "network_exit_ack_ubio": len(network_acks),
            "network_exit_ack_nsim": len(nsim_acks),
            "complete": shutdown_complete,
        },
        "children": {
            "expected": expected_child_count,
            "observed": len(child_exits),
            "nonzero": child_nonzero,
            "complete": child_complete,
        },
        "runner": {"timeout_seen": timeout_seen, "stall_seen": stall_seen},
        "recommendations": recommendations,
    }


def print_human(report):
    progress = report["progress"]
    hot = report["hot_line"]
    shutdown = report["shutdown"]
    children = report["children"]
    print(f"TC98 STATUS: {report['status']}")
    print(
        "progress: "
        f"planes={progress['complete_planes']}/{report['topology']['planes']} "
        f"markers={progress['min_round_marker']}..{progress['max_round_marker']} "
        f"skew={progress['skew']}")
    print(
        "done: "
        f"match={report['done_markers']['match']} "
        f"mismatch={report['done_markers']['mismatch']} "
        f"verifier_pass={int(report['verifier']['pass'])}")
    print(
        "hot-line: "
        f"pa={hot['pa']} commits={hot['commits']} "
        f"epoch={hot['first_epoch']}->{hot['last_epoch']} "
        f"max={hot['max_epoch']} monotonic={int(hot['monotonic'])} "
        f"duplicates={hot['duplicates']} gaps={hot['gaps']}")
    issue_text = " ".join(
        f"{name}={count}" for name, count in sorted(report["issues"].items())
        if count
    ) or "none"
    print(f"issues: {issue_text}")
    profiles = report["clear_profiles"]
    print("clear-profile: " + (" ".join(
        f"{name}={count}" for name, count in sorted(profiles.items())) or
        "unknown"))
    mismatch = report["grant_retry_tuple_mismatches"]
    if mismatch["top"]:
        top = mismatch["top"]
        print(
            "top-mismatch: "
            f"count={top['count']} home={top['home']} pa={top['pa']} "
            f"requester={top['requester']} "
            f"incoming={top['incoming_socket']}:{top['incoming_reqid']} "
            f"outstanding={top['outstanding_socket']}:{top['outstanding_reqid']}")
        old_chain = top["outstanding_clear_chain"]
        print("old-reqid-chain: " + (" ".join(
            f"{stage}={count}" for stage, count in sorted(old_chain.items())) or
            "none"))
    print(
        "shutdown: "
        f"peer_exit={shutdown['peer_exit']}/{report['topology']['planes']} "
        f"ubio_ack={shutdown['network_exit_ack_ubio']}/{report['topology']['planes']} "
        f"nsim_ack={shutdown['network_exit_ack_nsim']}/{report['topology']['planes']}")
    print(
        "children: "
        f"exit_files={children['observed']}/{children['expected']} "
        f"nonzero={len(children['nonzero'])}")
    slow = sorted(report["progress"]["planes"].items(), key=lambda item: item[1])
    print("planes: " + " ".join(f"{plane}=r{round_id}" for plane, round_id in slow))
    for recommendation in report["recommendations"]:
        print(f"recommendation: {recommendation}")
    for kind, samples in sorted(report["issue_samples"].items()):
        for sample in samples:
            print(
                f"sample[{kind}]: {sample['file']}:{sample['line']}: "
                f"{sample['text']}")


def parse_hot_pa(value):
    try:
        return int(value, 0)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("log_dir")
    parser.add_argument(
        "--simout-dir",
        help="separate directory containing simout_n* or simout_tc98_node*.log")
    parser.add_argument("--num-nodes", type=int, default=8)
    parser.add_argument("--num-sockets", type=int, default=2)
    parser.add_argument("--rounds", type=int, default=16)
    parser.add_argument("--hot-pa", type=parse_hot_pa)
    parser.add_argument("--sample-limit", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--strict", action="store_true",
        help="return non-zero unless status is PASS")
    args = parser.parse_args()
    if args.num_nodes <= 0 or args.num_sockets <= 0 or args.rounds <= 0:
        parser.error("topology dimensions and rounds must be positive")
    if args.sample_limit < 0:
        parser.error("sample-limit must be non-negative")
    report = analyze(args)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    if report["status"] == "FAIL":
        return 2
    if args.strict and report["status"] != "PASS":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
