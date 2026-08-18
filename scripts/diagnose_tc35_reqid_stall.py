#!/usr/bin/env python3
"""Extract TC35 Grant/Clear reqId stall evidence from an existing log tree."""

import argparse
import gzip
import json
import pathlib
import re
import sys


MISMATCH_RE = re.compile(
    r"\[UBCC-GRANT-RETRY-TUPLE-MISMATCH\].*?home=(\d+).*?"
    r"pa=0x([0-9a-fA-F]+).*?requester=(\d+).*?"
    r"incomingSocket=(\d+).*?incomingReqId=(\d+).*?"
    r"outstandingSocket=(\d+).*?outstandingReqId=(\d+)"
)

EVENT_PATTERNS = (
    ("profile", re.compile(r"EPBACKEND-PROFILE|HA_PROFILE=|OURCC_CLEAR_PROFILE=")),
    ("backend_manifest", re.compile(r"EPBACKEND-MANIFEST|UBADAPTER-STARTUP")),
    ("outer_request", re.compile(r"UBCC-OUTER-REQ|outer request envelope|sendReadReq homePa=")),
    ("grant", re.compile(r"UBCC-GRANT-READY|PUSH-GRANT|RECALL-TO-GRANT|UBCC-INV-TO-GRANT")),
    ("read_response", re.compile(r"ADAPTER-GOT-RESP.*ReadResp|RSP-WIRED.*ReadResp")),
    ("pending_grant", re.compile(r"savePendingGrantTxn")),
    ("requester_state", re.compile(r"line 0x[0-9a-fA-F]+ -> R_|invalidating stale requester state")),
    ("clear_send", re.compile(r"CLEAR-SEND|CLR-CACHE-MISS|CLR-TX")),
    ("clear_home", re.compile(r"HOME-CLEAR-COMMIT|processClear|UBST.*action=COMMIT")),
    ("clear_response", re.compile(r"CLEAR-RESP|CLR-CACHE-HIT")),
    ("epsnf_retry", re.compile(r"grant BUSY.*queuing retry|DEBUG-EP-SNF|recvRequestMsg.*addr=")),
    ("progress", re.compile(r"TC35_PROGRESS|TC35 PASSED|TC35 FAILED")),
)


def open_text(path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", errors="replace")
    return path.open(errors="replace")


def iter_logs(root):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix in {".log", ".txt"} or path.name.endswith(".log.gz"):
            yield path


def event_kind(line):
    for kind, pattern in EVENT_PATTERNS:
        if pattern.search(line):
            return kind
    return None


def relation(old, new):
    if new == old:
        return "same_reqid_different_tuple"
    if new == old + 1:
        return "consecutive_new_reqid"
    return "different_reqid"


def reqid_in_line(text, reqid):
    decimal = re.compile(rf"(?<![0-9]){reqid}(?![0-9])")
    hexadecimal = re.compile(rf"(?<![0-9a-fA-F]){re.escape(hex(reqid))}(?![0-9a-fA-F])",
                             re.IGNORECASE)
    return bool(decimal.search(text) or hexadecimal.search(text))


def chain_summary(records, reqid, pa, context_limit):
    evidence = []
    counts = {}
    for path, path_records in records.items():
        for line_number, kind, text in path_records:
            if not reqid_in_line(text, reqid):
                continue
            counts[kind] = counts.get(kind, 0) + 1
            if len(evidence) < context_limit:
                evidence.append({
                    "file": str(path), "line": line_number,
                    "kind": kind, "text": text,
                })
    return {
        "reqid": reqid,
        "pa_hex": pa,
        "event_counts": counts,
        "hints": {
            "outer_request_seen": counts.get("outer_request", 0) > 0,
            "grant_seen": counts.get("grant", 0) > 0,
            "read_response_seen": counts.get("read_response", 0) > 0,
            "pending_grant_seen": counts.get("pending_grant", 0) > 0,
            "clear_send_seen": counts.get("clear_send", 0) > 0,
            "home_clear_seen": counts.get("clear_home", 0) > 0,
            "clear_response_seen": counts.get("clear_response", 0) > 0,
            "epsnf_retry_seen": counts.get("epsnf_retry", 0) > 0,
        },
        "evidence": evidence,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("log_root", type=pathlib.Path)
    parser.add_argument("--json", dest="json_out", type=pathlib.Path)
    parser.add_argument("--context-limit", type=int, default=400)
    args = parser.parse_args()

    root = args.log_root.resolve()
    if not root.is_dir():
        parser.error(f"not a directory: {root}")

    files = list(iter_logs(root))
    mismatches = []
    profiles = []
    progress = []
    cached_lines = {}
    for path in files:
        records = []
        with open_text(path) as stream:
            for line_number, line in enumerate(stream, 1):
                text = line.rstrip("\n")
                match = MISMATCH_RE.search(text)
                if match:
                    old_req = int(match.group(7))
                    new_req = int(match.group(5))
                    mismatches.append({
                        "file": str(path),
                        "line": line_number,
                        "home": int(match.group(1)),
                        "pa": int(match.group(2), 16),
                        "pa_hex": "0x" + match.group(2).lower(),
                        "requester": int(match.group(3)),
                        "incoming_socket": int(match.group(4)),
                        "incoming_reqid": new_req,
                        "outstanding_socket": int(match.group(6)),
                        "outstanding_reqid": old_req,
                        "relation": relation(old_req, new_req),
                        "text": text,
                    })
                kind = event_kind(text)
                if kind:
                    records.append((line_number, kind, text))
                    if kind == "profile":
                        profiles.append({"file": str(path), "line": line_number, "text": text})
                    elif kind == "progress":
                        progress.append({"file": str(path), "line": line_number, "text": text})
        cached_lines[path] = records

    report = {
        "log_root": str(root),
        "files_scanned": len(files),
        "profiles": profiles,
        "progress": progress,
        "mismatches": [],
    }

    for mismatch in mismatches:
        pa = mismatch["pa_hex"]
        item = dict(mismatch)
        item["old_chain"] = chain_summary(
            cached_lines, mismatch["outstanding_reqid"], pa,
            args.context_limit)
        item["new_chain"] = chain_summary(
            cached_lines, mismatch["incoming_reqid"], pa,
            args.context_limit)
        report["mismatches"].append(item)

    if args.json_out:
        args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    print(f"log_root={root}")
    print(f"files_scanned={len(files)} mismatches={len(mismatches)}")
    for index, item in enumerate(report["mismatches"], 1):
        print(
            f"mismatch[{index}] pa={item['pa_hex']} requester={item['requester']} "
            f"old=({item['outstanding_socket']},{item['outstanding_reqid']}) "
            f"new=({item['incoming_socket']},{item['incoming_reqid']}) "
            f"relation={item['relation']}")
        for label in ("old_chain", "new_chain"):
            chain = item[label]
            print(f"  {label} " + " ".join(
                f"{key}={int(value)}" for key, value in chain["hints"].items()))
            print(f"  {label}_counts " +
                  json.dumps(chain["event_counts"], sort_keys=True))
            for event in chain["evidence"]:
                print(f"  {label} {event['kind']} {event['file']}:"
                      f"{event['line']}: {event['text']}")

    if not mismatches:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)
