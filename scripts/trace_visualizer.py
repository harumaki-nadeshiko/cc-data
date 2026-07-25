#!/usr/bin/env python3
"""Build an interactive, transaction-accurate TRACE-PERF visualizer.

The log payload's node column is not reliable for UBIO SEND_NET/SEND_GEM5:
it records the message's destination module.  This tool derives a process's
physical node from its log path and derives network endpoints from src/dst.
"""
import argparse
import html
import json
import os
import re
import sys
from collections import defaultdict

TICK2NS = 1e-3
TRACE_RE = re.compile(
    r"\[TRACE-PERF\]\s+(\d+)\|(\d+)\|(\w+)\|(\d+)\|([0-9a-fx]+)\|(\w+)\|(.+)"
)
PATH_NODE_RE = re.compile(r"(?:gem5_tc\d+_node|ubio_n)(\d+)(?:_s\d+)?")
NET_RE = re.compile(r"\bsrc=(\d+)\s+dst=(\d+)")
RESIDENT_RE = re.compile(r"\[(RESIDENT-[A-Z_-]+)\]\s+(.*)")
FIELD_RE = re.compile(r"(\w+)=([^\s]+)")
START_TYPES = {"ReadReq", "UpgradeReq", "WriteReq", "Writeback", "EvictReq", "CleanUnique", "ClearReq"}
RESPONSE_TYPES = {
    "ReadReq": "ReadResp", "UpgradeReq": "UpgradeResp", "WriteReq": "WriteResp",
    "Writeback": "WritebackResp", "EvictReq": "EvictResp",
    "CleanUnique": "CleanUniqueResp", "ClearReq": "ClearResp",
}


def message_type(extra):
    return extra.split("|", 1)[0].strip() if extra else "?"


def path_node(path):
    match = PATH_NODE_RE.search(path)
    return int(match.group(1)) if match else None


def parse_event(line, source_path):
    match = TRACE_RE.search(line)
    if not match:
        return None
    tick, logged_node, comp, req_id, pa, event, extra = match.groups()
    source_node = path_node(source_path)
    event = {
        "tick": int(tick), "logged_node": int(logged_node), "comp": comp,
        "reqId": int(req_id), "pa": pa.lower(), "event": event,
        "extra": extra.strip(), "type": message_type(extra), "path_node": source_node,
    }
    network = NET_RE.search(extra)
    if comp == "nsim" and network:
        event["src"], event["dst"] = map(int, network.groups())
        event["node"] = event["src"] if event["event"] == "RECV" else event["dst"]
    elif comp in ("gem5", "ubio") and source_node is not None:
        # The process path, not the trace payload, identifies the sender/receiver.
        event["node"] = source_node
    else:
        event["node"] = int(logged_node)
    return event


def parse_resident_event(line, source_path):
    match = RESIDENT_RE.search(line)
    if not match:
        return None
    kind, payload = match.groups()
    fields = dict(FIELD_RE.findall(payload))
    if "tick" not in fields or "home" not in fields:
        return None
    pa = fields.get("pa", fields.get("victim", "0x0")).lower()
    return {
        "tick": int(fields["tick"]), "logged_node": int(fields["home"]),
        "node": int(fields["home"]), "comp": "resident", "event": kind,
        "type": "ResidentDir", "reqId": int(fields.get("reqId", "0")),
        "pa": pa, "extra": payload, "fields": fields,
    }


def collect_events(inputs):
    events = []
    for input_path in inputs:
        if os.path.isdir(input_path):
            paths = []
            for root, _, files in os.walk(input_path):
                paths.extend(os.path.join(root, name) for name in files
                             if name.endswith(".log") or name == "stderr.log")
        else:
            paths = [input_path]
        for log_path in paths:
            try:
                with open(log_path, errors="replace") as stream:
                    for line in stream:
                        event = parse_event(line, log_path) or parse_resident_event(line, log_path)
                        if event:
                            events.append(event)
            except OSError:
                pass
    # Remove duplicated logger emissions while retaining same-tick causal events.
    unique = {}
    for event in events:
        key = (event["tick"], event["comp"], event["node"], event["reqId"],
               event["pa"], event["event"], event["extra"])
        unique[key] = event
    order = {("gem5", "SEND"): 0, ("ubio", "RECV_GEM5"): 1,
             ("ubio", "SEND_NET"): 2, ("nsim", "RECV"): 3,
             ("nsim", "FWD"): 4, ("ubio", "RECV_NET"): 5,
             ("ubio", "SEND_GEM5"): 6, ("gem5", "RECV"): 7,
             ("resident", "RESIDENT-FILL-ISSUED"): 6,
             ("resident", "RESIDENT-FILL-DONE"): 6}
    return sorted(unique.values(), key=lambda e: (e["tick"], order.get((e["comp"], e["event"]), 9)))


def is_start(event):
    return event["comp"] == "gem5" and event["event"] == "SEND" and event["type"] in START_TYPES


def segment_type(a, b):
    if a["comp"] == "resident" or b["comp"] == "resident":
        return "ResidentDir"
    if a["comp"] == "nsim" and b["comp"] == "nsim":
        return "network" if a["event"] == "RECV" and b["event"] == "FWD" else "network queue"
    if a["comp"] == "gem5" and b["comp"] == "ubio":
        return "gem5 IPC"
    if a["comp"] == "ubio" and b["comp"] == "gem5":
        return "gem5 IPC"
    if a["comp"] == "ubio" and b["comp"] == "nsim":
        return "network injection"
    if a["comp"] == "nsim" and b["comp"] == "ubio":
        return "PDES alignment"
    if a["comp"] == "ubio" and b["comp"] == "ubio":
        return "UBIO / home processing"
    if a["comp"] == "gem5" and b["comp"] == "gem5":
        return "gem5 processing"
    return "other"


def endpoint(event):
    return f"{event['comp']}_{event['node']}"


def build_transactions(events):
    starts = [event for event in events if is_start(event)]
    result = []
    for index, start in enumerate(starts):
        response = RESPONSE_TYPES[start["type"]]
        requester = start["node"]
        # A next gem5 request with the same reqId/PA ends this lifecycle. This
        # makes ClearReq a separate complete transaction rather than fragments.
        next_start_tick = None
        for later in starts[index + 1:]:
            if later["reqId"] == start["reqId"] and later["pa"] == start["pa"]:
                next_start_tick = later["tick"]
                break
        # nsim deliberately logs pa=0x0.  Keep it in this request-ID/window
        # candidate set; concrete-component traffic must still match the PA.
        candidates = [event for event in events if event["reqId"] == start["reqId"]
                      and (event["comp"] == "nsim" or event["pa"] == start["pa"])
                      and event["tick"] >= start["tick"]
                      and (next_start_tick is None or event["tick"] < next_start_tick)]
        delivery = next((event for event in candidates
                         if event["comp"] == "ubio" and event["node"] == requester
                         and event["event"] == "SEND_GEM5" and event["type"] == response), None)
        if not delivery:
            continue
        selected = [event for event in candidates if event["tick"] <= delivery["tick"]]
        # Keep only causally relevant message types plus nsim's associated hops.
        selected = [event for event in selected if event["comp"] in ("nsim", "resident") or
                    event["type"] in (start["type"], response)]
        # Resident diagnostics do not always carry reqId (fill/spill is issued
        # below the request envelope). Bind them to the matching Home window.
        home_arrival = next((event for event in selected
                             if event["comp"] == "ubio" and event["event"] == "RECV_NET"
                             and event["type"] == start["type"]), None)
        home_response = next((event for event in selected
                              if event["comp"] == "ubio" and event["event"] == "SEND_NET"
                              and event["type"] == response), None)
        if home_arrival and home_response:
            home = home_arrival["node"]
            resident_window = [event for event in events
                               if event["comp"] == "resident" and event["node"] == home
                               and home_arrival["tick"] <= event["tick"] <= home_response["tick"]]
            spill_victims = {event["pa"] for event in resident_window
                             if event["event"] == "RESIDENT-SPILL-START"}
            for event in resident_window:
                # A spill's victim differs from the request PA, but it is part
                # of this request's capacity-resolution causal window.
                if event["pa"] == start["pa"] or event["pa"] in spill_victims:
                    selected.append(event)
        if not any(event["comp"] == "nsim" for event in selected):
            continue  # This is a local transaction, intentionally excluded by default.
        # Add reqId-less ResidentDir diagnostics, then restore simulated-time
        # order before deriving spans and endpoint transitions.
        selected = list({(event["tick"], event["comp"], event["event"], event["pa"],
                          event["extra"]): event for event in selected}.values())
        selected.sort(key=lambda event: event["tick"])
        segments = []
        for a, b in zip(selected, selected[1:]):
            dt = b["tick"] - a["tick"]
            if dt >= 0:
                segments.append({"from": a["tick"], "to": b["tick"], "dt": dt,
                                 "kind": segment_type(a, b), "from_ep": endpoint(a),
                                 "to_ep": endpoint(b)})
        net_ps = sum(segment["dt"] for segment in segments if segment["kind"] == "network")
        resident = [event for event in selected if event["comp"] == "resident"]
        fills = []
        spills = []
        issued = {}
        spill_issued = {}
        for event in resident:
            if event["event"] == "RESIDENT-FILL-ISSUED":
                issued[event["pa"]] = event
            elif event["event"] == "RESIDENT-FILL-DONE" and event["pa"] in issued:
                fills.append({"pa": event["pa"], "start": issued[event["pa"]]["tick"],
                              "end": event["tick"], "dur_ps": event["tick"] - issued[event["pa"]]["tick"]})
            elif event["event"] == "RESIDENT-SPILL-START":
                spill_issued[event["pa"]] = event
            elif event["event"] == "RESIDENT-SPILL-DONE" and event["pa"] in spill_issued:
                spills.append({"pa": event["pa"], "start": spill_issued[event["pa"]]["tick"],
                               "end": event["tick"], "dur_ps": event["tick"] - spill_issued[event["pa"]]["tick"]})
        flow = []
        for a, b in zip(selected, selected[1:]):
            pair = (endpoint(a), endpoint(b))
            if not flow or flow[-1] != pair:
                flow.append(pair)
        result.append({
            "key": f"{start['reqId']}:{start['pa']}:{start['type']}:{start['tick']}",
            "reqId": start["reqId"], "pa": start["pa"], "type": start["type"],
            "requester": requester, "first": start["tick"], "delivery": delivery["tick"],
            "dur_ps": delivery["tick"] - start["tick"], "net_ps": net_ps,
            "events": selected, "segments": segments, "flow": flow,
            "resident": resident, "fills": fills, "spills": spills,
        })
    return result


def html_page(transactions, target_ns):
    data = json.dumps(transactions).replace("</", "<\\/")
    return f'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TRACE-PERF Transaction Explorer</title>
<style>
:root{{color-scheme:dark;--bg:#10151e;--panel:#18212e;--line:#2b3a4d;--ink:#e8eef8;--muted:#9aabc0;--blue:#64a6ff;--violet:#ad7cff;--gold:#ffd166;--green:#62d6a7;--orange:#ff9f5a}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.4 ui-sans-serif,system-ui,sans-serif}} header{{padding:18px 24px 14px;background:linear-gradient(135deg,#1d2a3b,#152030);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:3}} h1{{font-size:20px;margin:0 0 5px}} .sub{{color:var(--muted);font-size:12px}} .controls{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:14px}}input,select,button{{background:#101923;color:var(--ink);border:1px solid #3b5068;border-radius:6px;padding:7px 9px}}button{{cursor:pointer;background:#2866b5;border:0;font-weight:650}}main{{display:grid;grid-template-columns:minmax(330px,38%) 1fr;min-height:calc(100vh - 125px)}}aside{{border-right:1px solid var(--line);overflow:auto;max-height:calc(100vh - 125px)}}.summary{{padding:12px 16px;border-bottom:1px solid var(--line);color:var(--muted)}}.tx{{padding:11px 15px;border-bottom:1px solid #263547;cursor:pointer}}.tx:hover,.tx.active{{background:#203147}}.badge{{padding:2px 6px;border-radius:4px;background:#315e99;font-size:11px;font-weight:700}}.dur{{float:right;color:var(--gold);font-weight:700}}.meta{{font:11px ui-monospace,monospace;color:var(--muted);margin-top:4px}}section{{padding:22px;overflow:auto}}.empty{{color:var(--muted);margin:30px}}.headline{{display:flex;gap:18px;flex-wrap:wrap;margin-bottom:18px}}.metric{{background:var(--panel);border:1px solid var(--line);padding:11px 14px;border-radius:8px;min-width:135px}}.metric b{{display:block;font-size:19px;color:var(--gold)}}.metric span{{color:var(--muted);font-size:11px}}.flow{{display:flex;align-items:center;gap:7px;flex-wrap:wrap;background:var(--panel);border:1px solid var(--line);padding:15px;border-radius:8px;margin:12px 0}}.node{{border-left:4px solid var(--blue);background:#24354a;padding:6px 9px;border-radius:4px;font:12px ui-monospace,monospace}}.node:nth-child(3n){{border-color:var(--violet)}}.arrow{{color:var(--muted)}}.timeline{{background:var(--panel);border:1px solid var(--line);padding:14px;border-radius:8px;margin:12px 0}}.bar{{height:34px;display:flex;border-radius:5px;overflow:hidden;background:#111923}}.seg{{min-width:2px;border-right:1px solid rgba(255,255,255,.2);padding:8px 4px;white-space:nowrap;overflow:hidden;font-size:11px;font-weight:700}}.network{{background:#7753cf}}.gem5_IPC{{background:#367fd4}}.PDES_alignment{{background:#5f7189}}.UBIO___home_processing{{background:#d39a36}}.network_injection{{background:#c76a35}}.other,.gem5_processing,.network_queue{{background:#426c61}}table{{width:100%;border-collapse:collapse;font:12px ui-monospace,monospace}}th,td{{text-align:left;padding:7px;border-bottom:1px solid var(--line)}}th{{color:var(--muted);font-weight:600}}.event-type{{color:var(--green)}}@media(max-width:850px){{main{{grid-template-columns:1fr}}aside{{max-height:38vh;border-right:0;border-bottom:1px solid var(--line)}}}}
</style><body><header><h1>TRACE-PERF Transaction Explorer</h1><div class="sub">Only complete cross-node request lifecycles are shown. UBIO node identity comes from the process log path; network endpoints come from nsim src/dst.</div><div class="controls"><label>Type <select id="type"><option value="">All demand + Clear</option></select></label><label>PA <input id="pa" placeholder="0x10000000"></label><label>reqId <input id="rid" placeholder="prefix"></label><label><input id="clear" type="checkbox" checked> Include Clear</label><button id="reset">Reset</button></div></header><main><aside><div class="summary" id="summary"></div><div id="list"></div></aside><section id="detail"></section></main><script>const TX={data}; const TARGET_NS={json.dumps(target_ns)};
const ns=p=>p<1000?p.toFixed(p<10?1:0)+' ns':(p/1000).toFixed(2)+' us'; const esc=s=>String(s).replace(/[&<>]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c]));
let selected=null; const types=[...new Set(TX.map(x=>x.type))].sort(); document.querySelector('#type').innerHTML+='<option>'+types.join('</option><option>')+'</option>';
function filtered(){{let type=typeEl.value,pa=paEl.value.toLowerCase(),rid=ridEl.value,clear=clearEl.checked;return TX.filter(x=>(!type||x.type===type)&&(!pa||x.pa.includes(pa))&&(!rid||String(x.reqId).startsWith(rid))&&(clear||x.type!=='ClearReq'));}} const typeEl=document.querySelector('#type'),paEl=document.querySelector('#pa'),ridEl=document.querySelector('#rid'),clearEl=document.querySelector('#clear');
function stats(rows){{let v=rows.map(x=>x.dur_ps*.001).sort((a,b)=>a-b),mean=v.reduce((a,b)=>a+b,0)/(v.length||1);return `Complete transactions: <b>${{rows.length}}</b> / ${{TX.length}} | P50 ${{ns(v[Math.floor(v.length*.5)]||0)}} | P99 ${{ns(v[Math.floor(v.length*.99)]||0)}} | mean ${{ns(mean)}}`;}}
function render(){{let rows=filtered(); if(!rows.some(x=>x.key===selected))selected=rows[0]?.key||null;summary.innerHTML=stats(rows);list.innerHTML=rows.map(x=>`<div class="tx ${{x.key===selected?'active':''}}" data-k="${{x.key}}"><span class="badge">${{x.type}}</span><span class="dur">${{ns(x.dur_ps*.001)}}</span><div class="meta">rid=${{x.reqId}} n${{x.requester}} pa=${{x.pa}}<br>network ${{ns(x.net_ps*.001)}} | ${{x.events.length}} causal events${{x.resident.length?' | ResidentDir '+x.resident.length:''}}</div></div>`).join('')||'<div class="empty">No complete transaction matches.</div>';document.querySelectorAll('.tx').forEach(e=>e.onclick=()=>{{selected=e.dataset.k;render()}});detail(rows.find(x=>x.key===selected));}}
function detail(x){{if(!x){{detailEl.innerHTML='<div class="empty">Select a transaction.</div>';return}}let dur=x.dur_ps||1;let bar=x.segments.map(s=>{{let pct=s.dt/dur*100,cl=s.kind.replaceAll(' ','_').replaceAll('/','_');return `<div class="seg ${{cl}}" style="width:${{pct}}%" title="${{esc(s.kind)}}: ${{ns(s.dt*.001)}} (${{esc(s.from_ep)}} -> ${{esc(s.to_ep)}})">${{pct>10?esc(s.kind)+' '+ns(s.dt*.001):''}}</div>`}}).join('');let nodes=[];x.flow.flat().forEach(n=>{{if(nodes.at(-1)!==n)nodes.push(n)}});let flow=nodes.map((n,i)=>`<span class="node">${{esc(n)}}</span>${{i<nodes.length-1?'<span class="arrow">→</span>':''}}`).join('');let target=TARGET_NS?`<div class="meta">Target: ${{ns(TARGET_NS)}}. The red marker is relative to this transaction's start.</div>`:'';let marker=TARGET_NS?`<i style="position:absolute;z-index:2;top:-4px;bottom:-4px;border-left:2px dashed #ff6464;left:${{Math.min(TARGET_NS/(dur*.001)*100,100)}}%" title="target ${{ns(TARGET_NS)}}"></i>`:'';let resident=x.resident.length?`<div class="timeline"><div class="meta"><b>ResidentDir activity at Home</b></div>${{x.resident.map(e=>`<div class="meta">+${{ns((e.tick-x.first)*.001)}} <b>${{esc(e.event)}}</b> ${{esc(e.extra)}}</div>`).join('')}}${{x.fills.map(f=>`<div class="meta"><b>Backstore load pa=${{f.pa}}: ${{ns(f.dur_ps*.001)}}</b></div>`).join('')}}${{x.spills.map(s=>`<div class="meta"><b>Backstore spill pa=${{s.pa}}: ${{ns(s.dur_ps*.001)}}</b></div>`).join('')}}</div>`:'';detailEl.innerHTML=`<div class="headline"><div class="metric"><b>${{ns(x.dur_ps*.001)}}</b><span>gem5 SEND to requester UBIO SEND_GEM5</span></div><div class="metric"><b>${{ns(x.net_ps*.001)}}</b><span>network link time</span></div><div class="metric"><b>n${{x.requester}}</b><span>requester</span></div></div><h2>${{x.type}} <span class="meta">rid=${{x.reqId}} pa=${{x.pa}}</span></h2><div class="flow">${{flow}}<span class="arrow">→</span><span class="node">gem5_${{x.requester}}</span></div><div class="timeline"><div class="meta">Relative duration breakdown. Hover a segment for exact endpoints and time.</div>${{target}}<div class="bar" style="position:relative">${{bar}}${{marker}}</div></div>${{resident}}<table><thead><tr><th>relative</th><th>tick</th><th>endpoint</th><th>event</th><th>message</th><th>details</th></tr></thead><tbody>${{x.events.map(e=>`<tr><td>+${{ns((e.tick-x.first)*.001)}}</td><td>${{e.tick}}</td><td>${{esc(e.comp+'_'+e.node)}}</td><td>${{esc(e.event)}}</td><td class="event-type">${{esc(e.type)}}</td><td>${{esc(e.extra)}}</td></tr>`).join('')}}</tbody></table>`;}} const detailEl=document.querySelector('#detail');[typeEl,paEl,ridEl,clearEl].forEach(e=>e.oninput=render);document.querySelector('#reset').onclick=()=>{{typeEl.value=paEl.value=ridEl.value='';clearEl.checked=true;render()}};render();</script></body></html>'''


def main():
    parser = argparse.ArgumentParser(description="Visualize complete TRACE-PERF transactions")
    parser.add_argument("input", nargs="+", help="log directories or files")
    parser.add_argument("--target-ns", type=float, help="accepted for compatibility; use the relative breakdown")
    parser.add_argument("--filter-pa", help="emit only a physical-address prefix")
    parser.add_argument("--min-req-id", type=int, default=0, help="minimum request ID")
    parser.add_argument("--exclude-req-ids", default="", help="comma-separated IDs to omit")
    args = parser.parse_args()
    excluded = {int(value) for value in args.exclude_req_ids.split(",") if value}
    tx = build_transactions(collect_events(args.input))
    tx = [item for item in tx if item["reqId"] >= args.min_req_id and item["reqId"] not in excluded
          and (not args.filter_pa or item["pa"].startswith(args.filter_pa.lower()))]
    print(html_page(tx, args.target_ns))


if __name__ == "__main__":
    main()
