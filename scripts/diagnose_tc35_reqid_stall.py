#!/usr/bin/env python3
"""Extract bounded, strict TC35 Grant/ReadResp/Clear reqId stall evidence."""

import argparse
import gzip
import json
import os
import pathlib
import re
import sys
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field


MISMATCH_MARKER = "[UBCC-GRANT-RETRY-TUPLE-MISMATCH]"
FIELD_RE = re.compile(r"([A-Za-z][A-Za-z0-9_]*)=([^\s,]+)")
REQID_FIELD_RE = re.compile(
    r"(?:reqId|requestId|incomingReqId|outstandingReqId)=([0-9]+)")
PA_FIELD_RE = re.compile(
    r"(?:pa|PA|homePa|homePA|homeLinePa|keyPA)=0x([0-9a-fA-F]+)")
UBIO_TRACE_RE = re.compile(
    r"\[TRACE-PERF\]\s+([0-9]+)\|([0-9]+)\|ubio\|([0-9]+)\|"
    r"(0x[0-9a-fA-F]+)\|(SEND_NET|RECV_NET|SEND_GEM5|RECV_GEM5)\|([A-Za-z0-9_]+)")
NSIM_TRACE_RE = re.compile(
    r"\[TRACE-PERF\]\s+([0-9]+)\|([0-9]+)\|nsim\|([0-9]+)\|"
    r"0x0\|(RECV|FWD)\|(.+)$")
UBIO_PATH_RE = re.compile(r"ubio(?:_tc\d+)?_n(\d+)_s(\d+)")
GEM5_PATH_RE = re.compile(r"gem5(?:_tc\d+)?_node(\d+)")


STAGE_ORDER = (
    "HRR", "HG", "HUSN.RR", "NR.RR", "NF.RR", "RURN.RR",
    "RUSG.RR", "AR", "PG", "CS", "RURG.CQ", "RUSN.CQ",
    "NR.CQ", "NF.CQ", "HURN.CQ", "HC", "HUSN.CR", "NR.CR",
    "NF.CR", "RURN.CR", "RUSG.CR", "CR", "CH", "ESR",
)

STAGE_DESCRIPTIONS = {
    "HRR": "Home accepted/read outer request",
    "HG": "Home produced grant",
    "HUSN.RR": "Home UBIO sent ReadResp to network",
    "NR.RR": "networksim received ReadResp",
    "NF.RR": "networksim forwarded ReadResp",
    "RURN.RR": "requester UBIO received ReadResp from network",
    "RUSG.RR": "requester UBIO sent ReadResp to gem5",
    "AR": "requester adapter received ReadResp",
    "PG": "requester saved pending grant tuple",
    "CS": "requester initiated Clear",
    "RURG.CQ": "requester UBIO received ClearReq from gem5",
    "RUSN.CQ": "requester UBIO sent ClearReq to network",
    "NR.CQ": "networksim received ClearReq",
    "NF.CQ": "networksim forwarded ClearReq",
    "HURN.CQ": "Home UBIO received ClearReq",
    "HC": "Home committed Clear",
    "HUSN.CR": "Home UBIO sent ClearResp to network",
    "NR.CR": "networksim received ClearResp",
    "NF.CR": "networksim forwarded ClearResp",
    "RURN.CR": "requester UBIO received ClearResp",
    "RUSG.CR": "requester UBIO sent ClearResp to gem5",
    "CR": "requester adapter received ClearResp",
    "CH": "requester consumed cached ClearResp",
    "ESR": "EP-SNF retry/request activity",
}


@dataclass
class Event:
    reqid: int
    stage: str
    file: str
    line: int
    text: str
    pa: int = None
    tick: int = None
    process_node: int = None
    process_socket: int = None
    source: int = None
    target: int = None


@dataclass
class StageSummary:
    count: int = 0
    first: Event = None
    last: Event = None
    samples: list = field(default_factory=list)

    def add(self, event, sample_limit):
        self.count += 1
        if self.first is None:
            self.first = event
        self.last = event
        if len(self.samples) < sample_limit:
            self.samples.append(event)


@dataclass
class Mismatch:
    file: str
    last_file: str
    first_line: int
    last_line: int
    occurrences: int
    home: int
    home_socket: int
    pa: int
    requester: int
    incoming_socket: int
    incoming_reqid: int
    outstanding_socket: int
    outstanding_reqid: int

    @property
    def key(self):
        return (self.home, self.home_socket, self.pa, self.requester,
                self.incoming_socket,
                self.incoming_reqid, self.outstanding_socket,
                self.outstanding_reqid)


def relation(old, new):
    if new == old:
        return "same_reqid_different_tuple"
    if new == old + 1:
        return "consecutive_new_reqid"
    return "different_reqid"


def parse_int(value):
    return int(value, 16) if value.lower().startswith("0x") else int(value)


def short_text(text, limit):
    text = text.replace("\t", " ").strip()
    return text if len(text) <= limit else text[:limit] + "..."


def process_identity(path):
    text = str(path)
    match = UBIO_PATH_RE.search(text)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = GEM5_PATH_RE.search(text)
    if match:
        return int(match.group(1)), None
    return None, None


def open_binary(path):
    return gzip.open(path, "rb") if path.name.endswith(".gz") else path.open("rb")


def bounded_lines_with_budget(path, max_line_bytes, byte_budget):
    with open_binary(path) as stream:
        line_number = 0
        bytes_read = 0
        while True:
            first = stream.readline(max_line_bytes + 1)
            if not first:
                return
            line_number += 1
            bytes_read += len(first)
            if bytes_read > byte_budget:
                raise ValueError("decoded log bytes exceed configured total budget")
            truncated = len(first) > max_line_bytes and not first.endswith(b"\n")
            if truncated:
                raw = first
                while raw and not raw.endswith(b"\n"):
                    raw = stream.readline(max_line_bytes + 1)
                    bytes_read += len(raw)
                    if bytes_read > byte_budget:
                        raise ValueError(
                            "decoded log bytes exceed configured total budget")
            text = first[:max_line_bytes].decode("utf-8", errors="replace")
            if truncated:
                text += " [TRUNCATED-LINE]"
            yield line_number, text.rstrip("\r\n"), truncated, bytes_read
            byte_budget -= bytes_read
            bytes_read = 0


def iter_logs(root, max_files):
    seen = set()
    selected = []
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(name for name in dirnames
                             if not pathlib.Path(directory, name).is_symlink())
        for name in sorted(filenames):
            if not (name.endswith(".log") or name.endswith(".txt") or
                    name.endswith(".log.gz")):
                continue
            path = pathlib.Path(directory, name)
            if path.is_symlink() or not path.is_file():
                continue
            stat = path.stat()
            inode = (stat.st_dev, stat.st_ino)
            if inode in seen:
                continue
            seen.add(inode)
            selected.append(path)
            if len(selected) > max_files:
                raise ValueError(f"log file count exceeds --max-files={max_files}")
    return selected


def parse_mismatch(path, line_number, text):
    if MISMATCH_MARKER not in text:
        return None
    fields = dict(FIELD_RE.findall(text))
    required = ("home", "pa", "requester", "incomingSocket", "incomingReqId",
                "outstandingSocket", "outstandingReqId")
    if any(key not in fields for key in required):
        return None
    process_node, process_socket = process_identity(path)
    return Mismatch(
        file=str(path), last_file=str(path), first_line=line_number,
        last_line=line_number,
        occurrences=1, home=parse_int(fields["home"]),
        home_socket=process_socket if process_socket is not None else -1,
        pa=parse_int(fields["pa"]),
        requester=parse_int(fields["requester"]),
        incoming_socket=parse_int(fields["incomingSocket"]),
        incoming_reqid=parse_int(fields["incomingReqId"]),
        outstanding_socket=parse_int(fields["outstandingSocket"]),
        outstanding_reqid=parse_int(fields["outstandingReqId"]),
    )


def explicit_reqid(text):
    match = re.search(r"(?:^|[\s,])(?:reqId|requestId)=([0-9]+)", text)
    return int(match.group(1)) if match else None


def explicit_pa(text):
    match = PA_FIELD_RE.search(text)
    return int(match.group(1), 16) if match else None


def classify_trace(path, line_number, text):
    node, socket = process_identity(path)
    match = UBIO_TRACE_RE.search(text)
    if match:
        tick, route, reqid, pa_text, action, message_type = match.groups()
        stage = {
            ("SEND_NET", "ReadResp"): "HUSN.RR",
            ("RECV_NET", "ReadResp"): "RURN.RR",
            ("SEND_GEM5", "ReadResp"): "RUSG.RR",
            ("RECV_GEM5", "ClearReq"): "RURG.CQ",
            ("SEND_NET", "ClearReq"): "RUSN.CQ",
            ("RECV_NET", "ClearReq"): "HURN.CQ",
            ("SEND_NET", "ClearResp"): "HUSN.CR",
            ("RECV_NET", "ClearResp"): "RURN.CR",
            ("SEND_GEM5", "ClearResp"): "RUSG.CR",
        }.get((action, message_type))
        if stage:
            return [Event(
                reqid=int(reqid), stage=stage, file=str(path), line=line_number,
                text=text, pa=int(pa_text, 16), tick=int(tick),
                process_node=node, process_socket=socket,
                target=int(route) if action.startswith("SEND") else None,
            )]
        return []
    match = NSIM_TRACE_RE.search(text)
    if match:
        tick, module, reqid, action, detail = match.groups()
        # networksim does not log CoherenceMessageType. The reqId is indexed
        # here and later correlated with typed UBIO stages for that reqId.
        source_match = re.search(r"src=(\d+)", detail)
        target_match = re.search(r"(?:dst=)(\d+)", detail)
        stage = "NSIM.RECV" if action == "RECV" else "NSIM.FWD"
        return [Event(
            reqid=int(reqid), stage=stage, file=str(path), line=line_number,
            text=text, tick=int(tick), process_node=int(module),
            source=int(source_match.group(1)) if source_match else None,
            target=int(target_match.group(1)) if target_match else int(module),
        )]
    return []


def classify_text(path, line_number, text):
    events = classify_trace(path, line_number, text)
    if events:
        return events
    reqid = explicit_reqid(text)
    if reqid is None:
        return []
    pa = explicit_pa(text)
    node, socket = process_identity(path)
    stages = []
    if "UBCC-OUTER-REQ" in text or "sendReadReq homePa=" in text:
        stages.append("HRR")
    if any(marker in text for marker in
           ("UBCC-GRANT-READY", "PUSH-GRANT", "RECALL-TO-GRANT",
            "UBCC-INV-TO-GRANT")):
        stages.append("HG")
    if "ADAPTER-GOT-RESP" in text and "ReadResp" in text:
        stages.append("AR")
    if "savePendingGrantTxn" in text:
        stages.append("PG")
    if any(marker in text for marker in
           ("CLEAR-SEND", "CLR-CACHE-MISS", "CLR-TX")):
        stages.append("CS")
    if "HOME-CLEAR-COMMIT" in text or "UBST" in text and "action=COMMIT" in text:
        stages.append("HC")
    if "CLEAR-RESP" in text:
        stages.append("CR")
    if "CLR-CACHE-HIT" in text:
        stages.append("CH")
    if any(marker in text for marker in
           ("grant BUSY", "DEBUG-EP-SNF", "recvRequestMsg")):
        stages.append("ESR")
    return [Event(reqid=reqid, stage=stage, file=str(path), line=line_number,
                  text=text, pa=pa, process_node=node, process_socket=socket)
            for stage in stages]


def annotate_nsim(events_by_reqid):
    """Correlate nsim events to typed UBIO sends once, using exact tick/target."""
    for events in events_by_reqid.values():
        sends = defaultdict(deque)
        receives = defaultdict(deque)
        ordered = sorted(events, key=lambda item: (
            item.tick if item.tick is not None else -1, item.file, item.line))
        for event in ordered:
            if event.stage in {"HUSN.RR", "RUSN.CQ", "HUSN.CR"}:
                sends[(event.tick, event.target)].append(event)
        for event in ordered:
            if event.stage == "NSIM.RECV":
                key = (event.tick, event.target)
                if sends[key]:
                    sent = sends[key].popleft()
                    kind = sent.stage.rsplit(".", 1)[1]
                    event.stage = f"NR.{kind}"
                    event.pa = sent.pa
                    receives[(event.target, kind)].append(event)
        for event in ordered:
            if event.stage == "NSIM.FWD":
                candidates = [(key, queue) for key, queue in receives.items()
                              if key[0] == event.target and queue]
                if candidates:
                    key, queue = min(
                        candidates,
                        key=lambda item: item[1][0].tick
                        if item[1][0].tick is not None else -1)
                    received = queue.popleft()
                    event.stage = f"NF.{key[1]}"
                    event.pa = received.pa


def annotate_unique_process_pa(events_by_reqid):
    for events in events_by_reqid.values():
        known = defaultdict(set)
        for event in events:
            if event.pa not in (None, 0):
                known[(event.process_node, event.process_socket)].add(event.pa)
        for event in events:
            if event.pa is None:
                values = known[(event.process_node, event.process_socket)]
                if len(values) == 1:
                    event.pa = next(iter(values))


def endpoint_matches(event, stage, mismatch, requester_socket, num_sockets):
    if event.process_node is None:
        return True
    home_socket = mismatch["home_socket"]
    if home_socket < 0:
        home_socket = None
    if stage in {"HRR", "HG", "HUSN.RR", "HURN.CQ", "HC", "HUSN.CR"}:
        return event.process_node == mismatch["home"] and (
            home_socket is None or event.process_socket in (None, home_socket))
    if stage in {"RURN.RR", "RUSG.RR", "AR", "PG", "CS", "RURG.CQ",
                 "RUSN.CQ", "RURN.CR", "RUSG.CR", "CR", "CH", "ESR"}:
        return event.process_node == mismatch["requester"] and (
            event.process_socket in (None, requester_socket))
    if stage.startswith("NR.") or stage.startswith("NF."):
        home_gid = (mismatch["home"] * num_sockets + home_socket
                    if home_socket is not None else None)
        requester_gid = mismatch["requester"] * num_sockets + requester_socket
        kind = stage.rsplit(".", 1)[1]
        expected = ((home_gid, requester_gid) if kind in {"RR", "CR"}
                    else (requester_gid, home_gid))
        return ((expected[0] is None or event.source in (None, expected[0])) and
                (expected[1] is None or event.target in (None, expected[1])))
    return True


def chain_for(events_by_reqid, reqid, mismatch, requester_socket,
              num_sockets, sample_limit):
    summaries = {stage: StageSummary() for stage in STAGE_ORDER}
    raw = events_by_reqid.get(reqid, ())
    for event in raw:
        if event.stage not in summaries:
            continue
        if event.pa != mismatch["pa"]:
            continue
        if not endpoint_matches(event, event.stage, mismatch,
                                requester_socket, num_sockets):
            continue
        summaries[event.stage].add(event, sample_limit)
    return summaries


def stage_value(summary):
    if summary.count == 0:
        return "0"
    first = summary.first
    last = summary.last
    if first.tick is not None and last.tick is not None:
        location = (str(first.tick) if first.tick == last.tick else
                    f"{first.tick}..{last.tick}")
    else:
        location = (f"{pathlib.Path(first.file).name}:{first.line}" if
                    first.file == last.file and first.line == last.line else
                    f"{pathlib.Path(first.file).name}:{first.line}.."
                    f"{pathlib.Path(last.file).name}:{last.line}")
    return f"{summary.count}@{location}"


def chain_counts(summaries):
    return {stage: summaries[stage].count for stage in STAGE_ORDER}


def chain_line(label, summaries):
    return label + "{" + ";".join(
        f"{stage}={stage_value(summaries[stage])}" for stage in STAGE_ORDER) + "}"


def likely_break(summaries):
    counts = chain_counts(summaries)
    ordered = ("HRR", "HG", "HUSN.RR", "NR.RR", "NF.RR", "RURN.RR",
               "RUSG.RR", "AR", "PG", "CS", "RURG.CQ", "RUSN.CQ",
               "NR.CQ", "NF.CQ", "HURN.CQ", "HC", "HUSN.CR", "NR.CR",
               "NF.CR", "RURN.CR", "RUSG.CR", "CR", "CH")
    if not counts[ordered[0]]:
        return "no_old_transaction_events"
    for previous, current in zip(ordered, ordered[1:]):
        if counts[previous] and not counts[current]:
            return f"after_{previous}_before_{current}"
        if not counts[previous]:
            return f"before_{previous}"
    return "through_clear_response_consumption"


def scan(root, args):
    files = iter_logs(root, args.max_files)
    events_by_reqid = defaultdict(list)
    mismatch_map = {}
    profiles = []
    progress = []
    file_errors = []
    lines_scanned = 0
    bytes_scanned = 0
    truncated_lines = 0
    events_indexed = 0
    max_socket = 0
    for path in files:
        try:
            remaining = args.max_total_bytes - bytes_scanned
            for line_number, text, truncated, bytes_read in bounded_lines_with_budget(
                    path, args.max_line_bytes, remaining):
                lines_scanned += 1
                bytes_scanned += bytes_read
                truncated_lines += int(truncated)
                _, process_socket = process_identity(path)
                if process_socket is not None:
                    max_socket = max(max_socket, process_socket)
                mismatch = parse_mismatch(path, line_number, text)
                if mismatch:
                    old = mismatch_map.get(mismatch.key)
                    if old:
                        old.occurrences += 1
                        old.last_line = line_number
                        old.last_file = str(path)
                    elif len(mismatch_map) < args.max_mismatches:
                        mismatch_map[mismatch.key] = mismatch
                if "EPBACKEND-PROFILE" in text or "HA_PROFILE=" in text or \
                        "OURCC_CLEAR_PROFILE=" in text:
                    if len(profiles) < args.sample_limit:
                        profiles.append({"file": str(path), "line": line_number,
                                         "text": short_text(text, args.excerpt_bytes)})
                if "TC35_PROGRESS" in text or "TC35 PASSED" in text or \
                        "TC35 FAILED" in text:
                    if len(progress) < args.sample_limit:
                        progress.append({"file": str(path), "line": line_number,
                                         "text": short_text(text, args.excerpt_bytes)})
                for event in classify_text(path, line_number, text):
                    events_indexed += 1
                    if events_indexed > args.max_events:
                        raise ValueError(
                            f"indexed events exceed --max-events={args.max_events}")
                    event.text = short_text(event.text, args.excerpt_bytes)
                    events_by_reqid[event.reqid].append(event)
        except (OSError, EOFError, gzip.BadGzipFile) as error:
            file_errors.append({"file": str(path), "error": str(error)})
            if not args.best_effort_io:
                raise
    annotate_nsim(events_by_reqid)
    annotate_unique_process_pa(events_by_reqid)
    num_sockets = max_socket + 1
    items = []
    chain_cache = {}
    for mismatch in mismatch_map.values():
        mismatch_dict = asdict(mismatch)
        old_key = (mismatch.outstanding_reqid, mismatch.pa, mismatch.home,
                   mismatch.home_socket, mismatch.requester,
                   mismatch.outstanding_socket, num_sockets)
        new_key = (mismatch.incoming_reqid, mismatch.pa, mismatch.home,
                   mismatch.home_socket, mismatch.requester,
                   mismatch.incoming_socket, num_sockets)
        if old_key not in chain_cache:
            chain_cache[old_key] = chain_for(
                events_by_reqid, mismatch.outstanding_reqid, mismatch_dict,
                mismatch.outstanding_socket, num_sockets, args.sample_limit)
        if new_key not in chain_cache:
            chain_cache[new_key] = chain_for(
                events_by_reqid, mismatch.incoming_reqid, mismatch_dict,
                mismatch.incoming_socket, num_sockets, args.sample_limit)
        old_chain = chain_cache[old_key]
        new_chain = chain_cache[new_key]
        items.append({
            "mismatch": asdict(mismatch),
            "relation": relation(mismatch.outstanding_reqid,
                                 mismatch.incoming_reqid),
            "likely_break": likely_break(old_chain),
            "old_chain": {
                "reqid": mismatch.outstanding_reqid,
                "counts": chain_counts(old_chain),
                "samples": {stage: [asdict(event) for event in summary.samples]
                            for stage, summary in old_chain.items() if summary.samples},
            },
            "new_chain": {
                "reqid": mismatch.incoming_reqid,
                "counts": chain_counts(new_chain),
                "samples": {stage: [asdict(event) for event in summary.samples]
                            for stage, summary in new_chain.items() if summary.samples},
            },
            "_old_summary": old_chain,
            "_new_summary": new_chain,
        })
    return {
        "log_root": str(root),
        "files_scanned": len(files),
        "lines_scanned": lines_scanned,
        "decoded_bytes_scanned": bytes_scanned,
        "truncated_lines": truncated_lines,
        "events_indexed": events_indexed,
        "file_errors": file_errors,
        "profiles": profiles,
        "progress": progress,
        "stage_descriptions": STAGE_DESCRIPTIONS,
        "mismatches": items,
    }


def json_report(report):
    clean = dict(report)
    clean["mismatches"] = []
    for item in report["mismatches"]:
        clean["mismatches"].append({key: value for key, value in item.items()
                                    if not key.startswith("_")})
    return clean


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Scan TC35 logs once, deduplicate reqId tuple mismatches, and emit "
            "a bounded Home->networksim->requester ReadResp/Clear chain."))
    parser.add_argument("log_root", type=pathlib.Path)
    parser.add_argument("--json", dest="json_out", type=pathlib.Path,
                        help="write bounded structured evidence JSON")
    parser.add_argument("--verbose", action="store_true",
                        help="print bounded evidence samples after each summary")
    parser.add_argument(
        "--fail-on-mismatch", action="store_true",
        help="return 1 when mismatches are found; default scan success is 0")
    parser.add_argument("--max-files", type=int, default=2000)
    parser.add_argument("--max-total-bytes", type=int, default=512 * 1024 * 1024)
    parser.add_argument("--max-line-bytes", type=int, default=256 * 1024)
    parser.add_argument("--max-mismatches", type=int, default=100)
    parser.add_argument("--max-events", type=int, default=2000000)
    parser.add_argument("--sample-limit", type=int, default=3)
    parser.add_argument("--excerpt-bytes", type=int, default=320)
    parser.add_argument(
        "--best-effort-io", action="store_true",
        help="continue after unreadable/corrupt files; default is strict")
    args = parser.parse_args()
    if min(args.max_files, args.max_total_bytes, args.max_line_bytes,
           args.max_mismatches, args.max_events, args.sample_limit,
           args.excerpt_bytes) < 1:
        parser.error("all bounds must be positive")
    root = args.log_root.resolve()
    if not root.is_dir():
        parser.error(f"not a directory: {root}")
    report = scan(root, args)
    if args.json_out:
        args.json_out.write_text(
            json.dumps(json_report(report), indent=2, sort_keys=True) + "\n")
    print(f"log_root={root}")
    print("scan files={files} lines={lines} bytes={bytes_} events={events} "
          "truncated={truncated} errors={errors} mismatches={mismatches}".format(
              files=report["files_scanned"], lines=report["lines_scanned"],
              bytes_=report["decoded_bytes_scanned"],
              events=report["events_indexed"],
              truncated=report["truncated_lines"],
              errors=len(report["file_errors"]),
              mismatches=len(report["mismatches"])))
    for index, item in enumerate(report["mismatches"], 1):
        mismatch = item["mismatch"]
        old_summary = item["_old_summary"]
        new_summary = item["_new_summary"]
        print(
            f"TC35STALL[{index}] n={mismatch['occurrences']} "
            f"at={pathlib.Path(mismatch['file']).name}:{mismatch['first_line']}.."
            f"{pathlib.Path(mismatch['last_file']).name}:{mismatch['last_line']} "
            f"H={mismatch['home']}:{mismatch['home_socket']} "
            f"PA=0x{mismatch['pa']:x} "
            f"RQ={mismatch['requester']}:{mismatch['incoming_socket']} "
            f"old={mismatch['outstanding_reqid']} "
            f"new={mismatch['incoming_reqid']} rel={item['relation']} "
            f"break={item['likely_break']} | "
            f"{chain_line('O', old_summary)} | {chain_line('N', new_summary)}")
        if args.verbose:
            for label, summary in (("old", old_summary), ("new", new_summary)):
                for stage in STAGE_ORDER:
                    for event in summary[stage].samples:
                        print(f"  {label} {stage} {event.file}:{event.line}: "
                              f"{event.text}")
    if args.fail_on_mismatch and report["mismatches"]:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)
