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
UBIO_FLAT_RE = re.compile(r"(?:^|[-_.])ubio[-_.](\d+)[-_.](\d+)(?:[-_.]|$)")
GEM5_PATH_RE = re.compile(r"gem5(?:_tc\d+)?_node(\d+)")
GEM5_FLAT_RE = re.compile(r"(?:^|[-_.])gem5[-_.](\d+)(?:[-_.]|$)")
COMPLETED_RECORD = "[UBCC-COMPLETED-READ-RECORD]"
COMPLETED_DUPLICATE = "[UBCC-COMPLETED-READ-DUPLICATE]"
CLEAR_RESULT = "[HOME-CLEAR-RESULT]"
UBIO_START_MARKER = "initialized with epoch_bits="


STAGE_ORDER = (
    "HRR", "HG", "HUSN.RR", "NR.RR", "NF.RR", "RURN.RR",
    "RUSG.RR", "AR", "PG", "CS", "RURG.CQ", "RUSN.CQ",
    "NR.CQ", "NF.CQ", "HURN.CQ", "HI", "HC", "HJ", "HUSN.CR", "NR.CR",
    "NF.CR", "RURN.CR", "RUSG.CR", "CR", "CH", "CJ", "ESR",
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
    "HI": "Home Clear ingress marker (legacy COMMIT marker was pre-dispatch)",
    "HC": "Home accepted and committed Clear",
    "HJ": "Home rejected Clear",
    "HUSN.CR": "Home UBIO sent ClearResp to network",
    "NR.CR": "networksim received ClearResp",
    "NF.CR": "networksim forwarded ClearResp",
    "RURN.CR": "requester UBIO received ClearResp",
    "RUSG.CR": "requester UBIO sent ClearResp to gem5",
    "CR": "requester adapter received ClearResp",
    "CH": "requester consumed cached ClearResp",
    "CJ": "requester consumed rejected ClearResp",
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
    if new == old + 2:
        return "new_reqid_after_one_intervening_txn"
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
    match = UBIO_FLAT_RE.search(pathlib.Path(path).name)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = GEM5_PATH_RE.search(text)
    if match:
        return int(match.group(1)), None
    match = GEM5_FLAT_RE.search(pathlib.Path(path).name)
    if match:
        return int(match.group(1)), None
    return None, None


def completed_identity_event(path, line_number, text):
    if COMPLETED_RECORD not in text and COMPLETED_DUPLICATE not in text:
        return None
    fields = dict(FIELD_RE.findall(text))
    required = ("home", "pa", "requester", "reqId")
    if any(key not in fields for key in required):
        return None
    requester = fields["requester"].split(":", 1)
    if len(requester) != 2:
        return None
    home = fields["home"].split(":", 1)[0]
    process_node, process_socket = process_identity(path)
    return {
        "kind": "record" if COMPLETED_RECORD in text else "duplicate",
        "home": parse_int(home),
        "home_socket": process_socket if process_socket is not None else -1,
        "pa": parse_int(fields["pa"]),
        "requester": parse_int(requester[0]),
        "requester_socket": parse_int(requester[1]),
        "reqid": parse_int(fields["reqId"]),
        "file": str(path),
        "line": line_number,
        "text": text,
        "process_node": process_node,
    }


def clear_result_event(path, line_number, text):
    if CLEAR_RESULT not in text:
        return None
    fields = dict(FIELD_RE.findall(text))
    required = ("home", "src", "pa", "reqId", "accepted")
    if any(key not in fields for key in required):
        return None
    home = fields["home"].split(":", 1)
    source = fields["src"].split(":", 1)
    if len(home) != 2 or len(source) != 2:
        return None
    return {
        "home": parse_int(home[0]),
        "home_socket": parse_int(home[1]),
        "pa": parse_int(fields["pa"]),
        "requester": parse_int(source[0]),
        "requester_socket": parse_int(source[1]),
        "reqid": parse_int(fields["reqId"]),
        "accepted": parse_int(fields["accepted"]),
        "file": str(path),
        "line": line_number,
        "text": text,
    }


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
    if "HOME-CLEAR-INGRESS" in text or "HOME-CLEAR-COMMIT" in text:
        stages.append("HI")
    if (("HOME-CLEAR-RESULT" in text and "accepted=1" in text) or
            ("UBST" in text and "action=COMMIT" in text) or
            ("DEBUG-UBCC-CLEAR" in text and " accept " in text)):
        stages.append("HC")
    if "HOME-CLEAR-RESULT" in text and "accepted=0" in text:
        stages.append("HJ")
    if "CLEAR-RESP" in text:
        stages.append("CR")
    if "CLR-CACHE-HIT" in text and "accepted=0" not in text:
        stages.append("CH")
    if "CLR-CACHE-HIT" in text and "accepted=0" in text:
        stages.append("CJ")
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
    if summaries["CJ"].count:
        return "clear_response_rejected"
    if summaries["CH"].count:
        return "through_accepted_clear_response_consumption"
    counts = chain_counts(summaries)
    ordered = ("HRR", "HG", "HUSN.RR", "NR.RR", "NF.RR", "RURN.RR",
               "RUSG.RR", "AR", "PG", "CS", "RURG.CQ", "RUSN.CQ",
               "NR.CQ", "NF.CQ", "HURN.CQ", "HC", "HUSN.CR", "NR.CR",
               "NF.CR", "RURN.CR", "RUSG.CR", "CR")
    if not counts[ordered[0]]:
        return "no_old_transaction_events"
    for previous, current in zip(ordered, ordered[1:]):
        if counts[previous] and not counts[current]:
            return f"after_{previous}_before_{current}"
        if not counts[previous]:
            return f"before_{previous}"
    return "after_clear_response_before_cache_consumption"


def compact_counts(summaries):
    return ",".join(str(summaries[stage].count) for stage in STAGE_ORDER)


def clear_resolution(mismatch, summaries):
    if summaries["CJ"].count or summaries["HJ"].count:
        return "CLEAR_REJECTED"
    if not summaries["CH"].count and not summaries["HC"].count:
        return "CLEAR_NOT_PROVEN_ACCEPTED"
    clear_accept = summaries["HC"]
    if clear_accept.count:
        if (clear_accept.first and clear_accept.last and
                clear_accept.first.file == mismatch.last_file and
                clear_accept.last.file == mismatch.last_file):
            if mismatch.last_line < clear_accept.first.line:
                return "TRANSIENT_RESOLVED_AFTER_MISMATCH"
            if mismatch.first_line > clear_accept.last.line:
                return "POST_ACCEPT_MISMATCH_RECREATED_OLD_TUPLE"
            return "MISMATCH_SPANS_CLEAR_ACCEPT"
        return "CLEAR_ACCEPTED_ORDER_UNKNOWN"
    ingress = summaries["HI"].first
    if ingress and ingress.file == mismatch.last_file:
        if mismatch.last_line < ingress.line:
            return "TRANSIENT_RESOLVED_AFTER_MISMATCH"
        if mismatch.last_line > ingress.line:
            return "POST_CLEAR_INGRESS_MISMATCH_OR_REPLAY"
    return "CLEAR_ACCEPTED_ORDER_UNKNOWN"


def post_clear_recovery(old_summaries, new_summaries):
    clear_accept = old_summaries["HC"].last
    if clear_accept is None:
        return "CLEAR_ACCEPT_NOT_LOCATED"

    new_grant = new_summaries["HG"].last
    if new_grant and new_grant.file == clear_accept.file:
        if new_grant.line > clear_accept.line:
            return "RECOVERED_WITH_NEW_GRANT_AFTER_CLEAR"
        return "NEW_GRANT_ONLY_BEFORE_CLEAR"

    new_request = new_summaries["HRR"].last
    if new_request and new_request.file == clear_accept.file:
        if new_request.line > clear_accept.line:
            return "RETRIED_AFTER_CLEAR_WITHOUT_NEW_GRANT"
        return "REQUESTER_DID_NOT_RETRY_AFTER_CLEAR"

    return "POST_CLEAR_RECOVERY_ORDER_UNKNOWN"


def identity_resolution(mismatch, events, clear_results, completed_record_total,
                        ubio_file_count, ubio_files_by_plane,
                        records_by_plane, start_markers_by_file):
    matching = [event for event in events
                if event["home"] == mismatch.home and
                event["pa"] == mismatch.pa and
                event["requester"] == mismatch.requester and
                event["requester_socket"] == mismatch.outstanding_socket and
                event["reqid"] == mismatch.outstanding_reqid and
                (event["home_socket"] < 0 or mismatch.home_socket < 0 or
                 event["home_socket"] == mismatch.home_socket)]
    records = [event for event in matching if event["kind"] == "record"]
    duplicates = [event for event in matching if event["kind"] == "duplicate"]
    plane = (mismatch.home, mismatch.home_socket)
    home_plane_files = ubio_files_by_plane.get(plane, set())
    home_plane_records = records_by_plane.get(plane, 0)
    home_file_start_markers = sum(
        start_markers_by_file.get(path, 0) for path in home_plane_files)
    mixed_run_files = sum(
        1 for path in home_plane_files
        if start_markers_by_file.get(path, 0) > 1)

    exact_clear_accepts = [event for event in clear_results
                           if event["accepted"] == 1 and
                           event["home"] == mismatch.home and
                           event["home_socket"] == mismatch.home_socket and
                           event["pa"] == mismatch.pa and
                           event["requester"] == mismatch.requester and
                           event["requester_socket"] == mismatch.outstanding_socket and
                           event["reqid"] == mismatch.outstanding_reqid]
    same_reqid_clear_accepts = [event for event in clear_results
                                if event["accepted"] == 1 and
                                event["home"] == mismatch.home and
                                event["home_socket"] == mismatch.home_socket and
                                event["reqid"] == mismatch.outstanding_reqid]
    record_before = sum(
        1 for event in records
        if event["file"] == mismatch.file and event["line"] < mismatch.first_line)
    record_after = sum(
        1 for event in records
        if event["file"] == mismatch.last_file and event["line"] > mismatch.last_line)
    record_between = sum(
        1 for event in records
        if event["file"] == mismatch.file == mismatch.last_file and
        mismatch.first_line <= event["line"] <= mismatch.last_line)
    duplicate_after_record = 0
    for duplicate in duplicates:
        if any(record["file"] == duplicate["file"] and
               record["line"] < duplicate["line"] for record in records):
            duplicate_after_record += 1

    if not records:
        if not home_plane_files and ubio_file_count == 0:
            resolution = "RECORD_FILE_NOT_SCANNED"
        elif not home_plane_files:
            resolution = "HOME_PLANE_FILES_NOT_SCANNED"
        elif mixed_run_files:
            resolution = "MIXED_RUN_LOG"
        elif home_plane_records == 0:
            resolution = "HOME_PLANE_HAS_NO_FEATURE_MARKER"
        elif exact_clear_accepts:
            resolution = "EXACT_CLEAR_ACCEPT_WITHOUT_RECORD"
        elif same_reqid_clear_accepts:
            resolution = "CLEAR_ACCEPT_TUPLE_DIFFERS"
        elif completed_record_total == 0:
            resolution = "FEATURE_NOT_PRESENT_IN_LOGS"
        else:
            resolution = "NO_EXACT_CLEAR_ACCEPT"
    elif record_before and not record_after and not record_between:
        resolution = "RECORD_BEFORE_ALL_MISMATCHES"
    elif record_after and not record_before and not record_between:
        resolution = "RECORD_AFTER_ALL_MISMATCHES"
    elif record_between or (record_before and record_after):
        resolution = "MISMATCHES_SPAN_RECORD"
    else:
        resolution = "RECORD_ORDER_UNKNOWN"
    if duplicate_after_record and resolution == "RECORD_AFTER_ALL_MISMATCHES":
        resolution = "DUPLICATES_FILTERED_AFTER_RECORD"
    return {
        "resolution": resolution,
        "record_count": len(records),
        "duplicate_drop_count": len(duplicates),
        "record_before_mismatch": record_before,
        "record_within_mismatch_span": record_between,
        "record_after_mismatch": record_after,
        "duplicate_after_record": duplicate_after_record,
        "home_plane_files": len(home_plane_files),
        "home_plane_records": home_plane_records,
        "home_plane_start_markers": home_file_start_markers,
        "mixed_run_files": mixed_run_files,
        "exact_clear_accepts": len(exact_clear_accepts),
        "same_reqid_clear_accepts": len(same_reqid_clear_accepts),
    }


def scan(root, args):
    files = iter_logs(root, args.max_files)
    events_by_reqid = defaultdict(list)
    mismatch_map = {}
    profiles = []
    progress = []
    completed_identity_events = []
    clear_result_events = []
    ubio_files = set()
    ubio_files_by_plane = defaultdict(set)
    start_markers_by_file = defaultdict(int)
    file_errors = []
    lines_scanned = 0
    bytes_scanned = 0
    truncated_lines = 0
    events_indexed = 0
    max_socket = 0
    for path in files:
        try:
            process_node, process_socket = process_identity(path)
            if process_node is not None and process_socket is not None and \
                    ("ubio" in path.name.lower() or
                     "ubio" in str(path.parent).lower()):
                ubio_files.add(str(path))
                ubio_files_by_plane[(process_node, process_socket)].add(str(path))
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
                identity_event = completed_identity_event(path, line_number, text)
                if identity_event:
                    completed_identity_events.append(identity_event)
                clear_event = clear_result_event(path, line_number, text)
                if clear_event:
                    clear_result_events.append(clear_event)
                if UBIO_START_MARKER in text:
                    start_markers_by_file[str(path)] += 1
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
        completed_record_total = sum(
            1 for event in completed_identity_events if event["kind"] == "record")
        records_by_plane = defaultdict(int)
        for event in completed_identity_events:
            if event["kind"] == "record":
                records_by_plane[(event["home"], event["home_socket"])] += 1
        identity = identity_resolution(
            mismatch, completed_identity_events, clear_result_events,
            completed_record_total, len(ubio_files), ubio_files_by_plane,
            records_by_plane, start_markers_by_file)
        items.append({
            "mismatch": asdict(mismatch),
            "relation": relation(mismatch.outstanding_reqid,
                                 mismatch.incoming_reqid),
            "likely_break": likely_break(old_chain),
            "clear_resolution": clear_resolution(mismatch, old_chain),
            "post_clear_recovery": post_clear_recovery(old_chain, new_chain),
            "completed_identity": identity,
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
        "ubio_files_scanned": len(ubio_files),
        "completed_read_records": sum(
            1 for event in completed_identity_events if event["kind"] == "record"),
        "completed_read_duplicate_drops": sum(
            1 for event in completed_identity_events
            if event["kind"] == "duplicate"),
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
          "truncated={truncated} errors={errors} mismatches={mismatches} "
          "ubioFiles={ubio_files} completedRecords={records} "
          "completedDuplicateDrops={duplicates}".format(
              files=report["files_scanned"], lines=report["lines_scanned"],
              bytes_=report["decoded_bytes_scanned"],
              events=report["events_indexed"],
              truncated=report["truncated_lines"],
              errors=len(report["file_errors"]),
              mismatches=len(report["mismatches"]),
              ubio_files=report["ubio_files_scanned"],
              records=report["completed_read_records"],
              duplicates=report["completed_read_duplicate_drops"]))
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
        print(
            "TC35COMPACT "
            f"old={mismatch['outstanding_reqid']} "
            f"middle={mismatch['outstanding_reqid'] + 1} "
            f"new={mismatch['incoming_reqid']} "
            f"sameSocket={int(mismatch['incoming_socket'] == mismatch['outstanding_socket'])} "
            f"relation={item['relation']} break={item['likely_break']} "
            f"resolution={item['clear_resolution']} "
            f"postClear={item['post_clear_recovery']} "
            f"identity={item['completed_identity']['resolution']} "
            f"recordCount={item['completed_identity']['record_count']} "
            f"duplicateDropCount={item['completed_identity']['duplicate_drop_count']} "
            f"recordBeforeMismatch={item['completed_identity']['record_before_mismatch']} "
            f"recordWithinMismatch={item['completed_identity']['record_within_mismatch_span']} "
            f"recordAfterMismatch={item['completed_identity']['record_after_mismatch']} "
            f"homePlaneFiles={item['completed_identity']['home_plane_files']} "
            f"homePlaneRecords={item['completed_identity']['home_plane_records']} "
            f"exactClearAccepts={item['completed_identity']['exact_clear_accepts']} "
            f"sameReqIdClearAccepts={item['completed_identity']['same_reqid_clear_accepts']} "
            f"mixedRunFiles={item['completed_identity']['mixed_run_files']} "
            f"homeAccept={old_summary['HC'].count} "
            f"homeReject={old_summary['HJ'].count} "
            f"cacheAccept={old_summary['CH'].count} "
            f"cacheReject={old_summary['CJ'].count} "
            f"oldCounts={compact_counts(old_summary)} "
            f"newCounts={compact_counts(new_summary)}")
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
