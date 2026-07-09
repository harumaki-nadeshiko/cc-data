#!/usr/bin/env python3
"""Parse TRACE-PERF lines into per-reqId chains (JSON).

Usage:
    grep -h 'TRACE-PERF' logs/*/gem5_tc*_node*/stderr.log \\
                              logs/*/ubio_n*/stderr.log \\
                              logs/*/nsim_tc*.log \\
      | sort -t'|' -k1 -n | python3 scripts/trace2chain.py > /tmp/tc5_chains.json

    # Or pass log dirs as args:
    python3 scripts/trace2chain.py logs/20260707_084333_1s > /tmp/tc5_chains.json

Output JSON:
    {
      "meta": {"total_events": N, "total_reqIds": M, "tick_range": [min, max]},
      "chains": {
        "<reqId>": {
          "reqId": 123,
          "pa": "0x10018000000",
          "events": [
            {"tick": 12345, "node": 0, "comp": "gem5", "event": "SEND", "extra": "ReadReq|dst=1"},
            ...
          ],
          "summary": "gem5_0 → ubio_0 → nsim → ubio_1 → gem5_1"
        },
        ...
      }
    }
"""
import sys, os, re, json

TRACE_RE = re.compile(
    r'\[TRACE-PERF\]\s+(\d+)\|(\d+)\|(\w+)\|(\d+)\|([0-9a-fx]+)\|(\w+)\|(.+)')


def parse_line(line: str):
    m = TRACE_RE.search(line)
    if not m:
        return None
    tick = int(m.group(1))
    node = int(m.group(2))
    comp = m.group(3)
    req_id = int(m.group(4))
    pa = m.group(5)
    event = m.group(6)
    extra = m.group(7).strip()
    return {
        "tick": tick,
        "node": node,
        "comp": comp,
        "reqId": req_id,
        "pa": pa,
        "event": event,
        "extra": extra,
    }


def collect_events(lines):
    """Parse lines, return list of event dicts."""
    events = []
    for line in lines:
        ev = parse_line(line)
        if ev:
            events.append(ev)
    return events


# A "request lifecycle" starts when the requester (gem5) issues one of these
# on the network — this is the natural boundary that lets us split reused
# reqIds (small internal ids like 2/3 are recycled across transactions on the
# same PA) into distinct chains, so each chain = one real request.
LIFECYCLE_START_TYPES = {"ReadReq", "UpgradeReq", "WriteReq", "Writeback",
                         "EvictReq", "CleanUnique"}


def _is_lifecycle_start(ev):
    """A gem5 SEND of a request type marks the start of a new request instance."""
    if ev["comp"] != "gem5" or ev["event"] != "SEND":
        return False
    primary = ev["extra"].split("|")[0]
    return primary in LIFECYCLE_START_TYPES


def build_chains(events, min_req_id=0, exclude_req_ids=None):
    """Group events into per-request-instance chains.

    reqIds are NOT unique: small internal ids (e.g. 2, 3) are recycled across
    independent transactions on the same PA. Keying purely by (reqId, PA) glues
    those unrelated transactions into one bogus chain whose duration mixes
    multiple requests plus idle gaps. Instead we open a NEW chain instance every
    time the requester (gem5) issues a fresh request (SEND ReadReq/UpgradeReq/...)
    for a given (reqId, PA), and route subsequent events for that (reqId, PA) into
    the currently-open instance. This makes "one chain == one request lifecycle
    (issue -> commit)".

    Args:
        events: list of event dicts (assumed globally tick-sorted by caller)
        min_req_id: skip reqIds below this value (e.g. 8 to skip internal ops)
        exclude_req_ids: set of reqIds to exclude (e.g. barrier reqIds)
    """
    if exclude_req_ids is None:
        exclude_req_ids = set()

    chains = {}
    # (reqId, pa) -> currently-open chain key, so mid-lifecycle events attach to
    # the right instance and a later re-issue opens a fresh one.
    open_key = {}
    inst_counter = {}

    for ev in events:
        rid = ev["reqId"]
        if rid < min_req_id or rid in exclude_req_ids:
            continue
        pa = ev["pa"]
        group = (rid, pa if pa != "0x0" else "?")

        # The tracer emits some gem5 SEND lines twice (exact duplicate at the
        # same tick). A duplicate lifecycle-start must NOT open a spurious new
        # instance — only a genuinely new issue (different tick, or after the
        # current instance already has downstream events) starts a new chain.
        dup_start = False
        if _is_lifecycle_start(ev) and group in open_key:
            cur = chains[open_key[group]]["events"]
            if cur and cur[-1]["tick"] == ev["tick"] \
                    and cur[-1]["comp"] == "gem5" \
                    and cur[-1]["event"] == "SEND" \
                    and cur[-1]["extra"] == ev["extra"]:
                dup_start = True

        if (_is_lifecycle_start(ev) and not dup_start) or group not in open_key:
            # Open a new instance. Suffix with an instance index so reused
            # (reqId, PA) pairs become distinct, stable chain keys.
            idx = inst_counter.get(group, 0)
            inst_counter[group] = idx + 1
            base = f"{rid}:{group[1]}"
            key = base if idx == 0 else f"{base}#{idx}"
            open_key[group] = key
            chains[key] = {"reqId": rid, "pa": None, "events": [],
                           "instance": idx}

        key = open_key[group]
        if ev["pa"] != "0x0":
            chains[key]["pa"] = ev["pa"]
        chains[key]["events"].append(ev)

    # Sort events within each chain by tick, then build summary
    for rid, ch in chains.items():
        ch["events"].sort(key=lambda e: e["tick"])
        if ch["events"]:
            first = ch["events"][0]
            last = ch["events"][-1]
            ch["first_tick"] = first["tick"]
            ch["last_tick"] = last["tick"]
            ch["duration_ps"] = last["tick"] - first["tick"]
            # Extract primary message type from first gem5/ubio event (skip nsim)
            primary_type = "?"
            for e in ch["events"]:
                if e["comp"] in ("gem5", "ubio"):
                    primary_type = e["extra"].split("|")[0]
                    break
            ch["primary_type"] = primary_type
            # Compact summary
            hops = []
            for e in ch["events"]:
                hops.append(f"{e['comp']}_{e['node']}")
            deduped = []
            for h in hops:
                if not deduped or deduped[-1] != h:
                    deduped.append(h)
            ch["summary"] = " → ".join(deduped)
            if ch["pa"]:
                ch["summary"] = f"pa={ch['pa']} | " + ch["summary"]

    return chains


def main():
    args = sys.argv[1:]

    if args:
        # Collect from log directories
        all_lines = []
        for path in args:
            if os.path.isdir(path):
                for root, dirs, files in os.walk(path):
                    for f in files:
                        if f.endswith(".log") or f == "stderr.log":
                            fpath = os.path.join(root, f)
                            try:
                                with open(fpath, errors="replace") as fh:
                                    for line in fh:
                                        if "TRACE-PERF" in line:
                                            all_lines.append(line.rstrip("\n"))
                            except Exception:
                                pass
            elif os.path.isfile(path):
                with open(path, errors="replace") as fh:
                    for line in fh:
                        if "TRACE-PERF" in line:
                            all_lines.append(line.rstrip("\n"))
        events = collect_events(all_lines)
    else:
        # Read from stdin
        events = collect_events(sys.stdin)

    events.sort(key=lambda e: e["tick"])

    # Exclude internal/system reqIds: 1=gem5 internal, 7=barrier
    exclude = {1, 7}
    chains = build_chains(events, min_req_id=2, exclude_req_ids=exclude)

    result = {
        "meta": {
            "total_events": len(events),
            "total_reqIds": len(chains),
            "tick_min": events[0]["tick"] if events else 0,
            "tick_max": events[-1]["tick"] if events else 0,
        },
        "chains": chains,
    }

    json.dump(result, sys.stdout, indent=2, default=str)
    print()  # trailing newline


if __name__ == "__main__":
    main()
