#!/usr/bin/env python3
"""Reconstruct terminal UpgradeReq/UpgradeResp transactions across components."""

import argparse
import gzip
import json
import pathlib
import re
import sys
from collections import Counter, defaultdict


TERMINAL_RE = re.compile(
    r"\[EPRNF-UPGRADE-TERMINAL\].*node=(\d+).*pa=0x([0-9a-fA-F]+).*"
    r"sourceSocket=(\d+).*homeNode=(\d+).*epoch=(\d+).*reqId=(\d+).*"
    r"reason=([A-Z0-9_]+).*resends=(\d+).*homeAccepted=(\d+)")
FIELD_RE = re.compile(r"([A-Za-z][A-Za-z0-9_]*)=([^\s,]+)")
FORENSIC_MARKER = "[UPGRADE-FORENSIC]"

STAGES = (
    ("GEM5_REQ_SEND", "UpgradeReq left requester gem5"),
    ("UBIO_GEM5_RECV:UpgradeReq", "requester UBIO received UpgradeReq"),
    ("UBIO_NET_SEND:UpgradeReq", "requester UBIO handed UpgradeReq to network Port"),
    ("NSIM_RECV:UpgradeReq", "networksim received UpgradeReq"),
    ("NSIM_FWD:UpgradeReq", "networksim forwarded UpgradeReq to Home"),
    ("UBIO_NET_RECV:UpgradeReq", "Home UBIO received UpgradeReq"),
    ("HOME_REQ_RESULT", "Home controller classified UpgradeReq"),
    ("UBIO_NET_SEND:UpgradeResp", "Home UBIO handed UpgradeResp to network Port"),
    ("NSIM_RECV:UpgradeResp", "networksim received UpgradeResp"),
    ("NSIM_FWD:UpgradeResp", "networksim forwarded UpgradeResp"),
    ("UBIO_NET_RECV:UpgradeResp", "requester UBIO received UpgradeResp"),
    ("UBIO_GEM5_SEND:UpgradeResp", "requester UBIO handed UpgradeResp to gem5 Port"),
    ("GEM5_RESP_RECV", "requester UBAdapter received UpgradeResp"),
    ("GEM5_RESP_CONSUME", "requester consumed cached UpgradeResp"),
)


def open_text(path):
    return (gzip.open(path, "rt", errors="replace") if path.suffix == ".gz"
            else path.open(errors="replace"))


def files(root):
    for path in root.rglob("*"):
        if path.is_file() and (path.suffix in {".log", ".txt"} or
                               path.name.endswith(".log.gz")):
            yield path


def parse_int(value):
    try:
        return int(value, 0)
    except (TypeError, ValueError):
        return None


def normalized_stage(fields):
    stage = fields.get("stage")
    message_type = fields.get("type")
    if stage in {"UBIO_GEM5_RECV", "UBIO_NET_SEND", "NSIM_RECV", "NSIM_FWD",
                 "UBIO_NET_RECV", "UBIO_GEM5_SEND"} and message_type:
        return f"{stage}:{message_type}"
    return stage


def diagnose(root, explicit_reqids=(), sample_limit=2):
    terminals = {}
    events = defaultdict(lambda: defaultdict(list))
    scanned_files = 0
    scanned_lines = 0
    paths = sorted(files(root))
    for path in paths:
        scanned_files += 1
        with open_text(path) as stream:
            for line_number, line in enumerate(stream, 1):
                scanned_lines += 1
                terminal = TERMINAL_RE.search(line)
                if terminal:
                    reqid = int(terminal.group(6))
                    terminals[reqid] = {
                        "node": int(terminal.group(1)),
                        "pa": f"0x{int(terminal.group(2), 16):x}",
                        "source_socket": int(terminal.group(3)),
                        "home_node": int(terminal.group(4)),
                        "epoch": int(terminal.group(5)),
                        "reqid": reqid,
                        "reason": terminal.group(7),
                        "resends": int(terminal.group(8)),
                        "home_accepted": bool(int(terminal.group(9))),
                        "file": str(path), "line": line_number,
                    }
                if FORENSIC_MARKER not in line:
                    continue
                fields = dict(FIELD_RE.findall(line))
                reqid = parse_int(fields.get("reqId"))
                stage = normalized_stage(fields)
                if reqid is None or not stage:
                    continue
                item = {"file": str(path), "line": line_number,
                        "stage": stage, "fields": fields,
                        "text": line.strip()[:600]}
                events[reqid][stage].append(item)

    requested = {parse_int(value) for value in explicit_reqids}
    requested.discard(None)
    reqids = sorted(set(terminals) | requested)
    reports = []
    for reqid in reqids:
        counts = {stage: len(events[reqid].get(stage, ())) for stage, _ in STAGES}
        last_present = None
        first_missing = None
        for stage, description in STAGES:
            if counts[stage]:
                last_present = {"stage": stage, "description": description}
            elif first_missing is None:
                first_missing = {"stage": stage, "description": description}

        if not counts["GEM5_REQ_SEND"]:
            diagnosis = "REQUESTER_DID_NOT_SEND_UPGRADE_REQ"
        elif not counts["UBIO_GEM5_RECV:UpgradeReq"]:
            diagnosis = "REQUESTER_GEM5_TO_UBIO_BREAK"
        elif not counts["UBIO_NET_SEND:UpgradeReq"]:
            diagnosis = "REQUESTER_UBIO_DID_NOT_HANDOFF_REQ"
        elif not counts["NSIM_RECV:UpgradeReq"]:
            diagnosis = "REQUESTER_FRAMEWORK_OR_NETWORK_INGRESS_BREAK"
        elif not counts["NSIM_FWD:UpgradeReq"]:
            diagnosis = "NETWORKSIM_DID_NOT_FORWARD_REQ"
        elif not counts["UBIO_NET_RECV:UpgradeReq"]:
            diagnosis = "HOME_FRAMEWORK_RECEIVE_BREAK"
        elif not counts["HOME_REQ_RESULT"]:
            diagnosis = "HOME_UBIO_DID_NOT_DISPATCH_REQ"
        elif not counts["UBIO_NET_SEND:UpgradeResp"]:
            diagnosis = "HOME_DID_NOT_HANDOFF_RESP"
        elif not counts["NSIM_RECV:UpgradeResp"]:
            diagnosis = "HOME_FRAMEWORK_SEND_BREAK_OR_BLOCK"
        elif not counts["NSIM_FWD:UpgradeResp"]:
            diagnosis = "NETWORKSIM_DID_NOT_FORWARD_RESP"
        elif not counts["UBIO_NET_RECV:UpgradeResp"]:
            diagnosis = "REQUESTER_FRAMEWORK_RECEIVE_BREAK"
        elif not counts["UBIO_GEM5_SEND:UpgradeResp"]:
            diagnosis = "REQUESTER_UBIO_DID_NOT_HANDOFF_RESP"
        elif not counts["GEM5_RESP_RECV"]:
            diagnosis = "REQUESTER_PORT_PENDING_OR_DELIVERY_BREAK"
        elif not counts["GEM5_RESP_CONSUME"]:
            diagnosis = "REQUESTER_RESPONSE_CACHE_OR_WAKEUP_BREAK"
        else:
            diagnosis = "RESPONSE_CONSUMED_CHECK_EPRNF_STATE_UPDATE"

        samples = {}
        for stage, _ in STAGES:
            if events[reqid].get(stage):
                rows = events[reqid][stage]
                samples[stage] = rows[:sample_limit] + (
                    rows[-1:] if len(rows) > sample_limit else [])
        reports.append({
            "reqid": reqid,
            "terminal": terminals.get(reqid),
            "diagnosis": diagnosis,
            "last_present": last_present,
            "first_missing": first_missing,
            "stage_counts": counts,
            "samples": samples,
        })
    return {"schema_version": 1, "log_dir": str(root),
            "scanned_files": scanned_files, "scanned_lines": scanned_lines,
            "terminal_count": len(terminals), "transactions": reports}


def print_human(report):
    print(f"UPGRADE TERMINALS: {report['terminal_count']}")
    for txn in report["transactions"]:
        terminal = txn.get("terminal") or {}
        print(
            f"reqId={txn['reqid']} node={terminal.get('node', '?')} "
            f"pa={terminal.get('pa', '?')} diagnosis={txn['diagnosis']}")
        print("  stages: " + " ".join(
            f"{stage}={count}" for stage, count in txn["stage_counts"].items()))
        last = txn.get("last_present")
        missing = txn.get("first_missing")
        if last:
            print(f"  last: {last['stage']} - {last['description']}")
        if missing:
            print(f"  first-missing: {missing['stage']} - {missing['description']}")
        for stage, samples in txn["samples"].items():
            for sample in samples:
                print(f"  sample[{stage}]: {sample['file']}:{sample['line']}: "
                      f"{sample['text']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("log_dir")
    parser.add_argument("--reqid", action="append", default=[])
    parser.add_argument("--sample-limit", type=int, default=2)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = diagnose(pathlib.Path(args.log_dir).resolve(), args.reqid,
                      args.sample_limit)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    return 0 if report["transactions"] else 1


if __name__ == "__main__":
    sys.exit(main())
