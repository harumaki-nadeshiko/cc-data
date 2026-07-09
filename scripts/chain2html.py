#!/usr/bin/env python3
"""Convert trace2chain JSON to interactive HTML timeline.

Usage:
    python3 scripts/chain2html.py /tmp/tc5_chains.json > /tmp/tc5.html
    python3 scripts/chain2html.py --target-ns 415 /tmp/tc5_chains.json > /tmp/tc5.html

F.1: P0 visual enhancements:
  - Time-axis ruler with ns grid lines
  - Over-target highlighting (red bar + OVER badge)
  - Inline duration labels on wide segments
  - Per-type aggregate statistics table
  - CSV export of visible chains
"""
import sys, os, json, argparse

TICK2NS = 1e-3  # ps -> ns — single conversion function

SEG_COLORS = {
    "gem5→ubio":  "#3b82f6",
    "ubio→gem5":  "#22c55e",
    "ubio→nsim":  "#f97316",
    "nsim→ubio":  "#8b5cf6",
    "nsim_fifo":  "#71717a",
    "ubio_proc":  "#facc15",
    "other":      "#94a3b8",
}

TYPE_COLORS = {
    "ReadReq":    "#3b82f6",
    "UpgradeReq": "#f59e0b",
    "RecallReq":  "#f97316",
    "RecallResp": "#f97316",
    "Writeback":  "#22c55e",
    "ClearReq":   "#94a3b8",
    "ClearResp":  "#94a3b8",
    "ReadResp":   "#3b82f6",
}


def classify_segment(from_ev, to_ev):
    fc = from_ev["comp"]
    tc = to_ev["comp"]
    if fc == "gem5" and tc == "ubio": return "gem5→ubio"
    if fc == "ubio" and tc == "gem5": return "ubio→gem5"
    if fc == "ubio" and tc == "nsim": return "ubio→nsim"
    if fc == "nsim" and tc == "ubio": return "nsim→ubio"
    if fc == "nsim" and tc == "nsim": return "nsim_fifo"
    if fc == "ubio" and tc == "ubio": return "ubio_proc"
    return "other"


def build_segments(events):
    segs = []
    for i in range(len(events) - 1):
        a, b = events[i], events[i + 1]
        dt_ps = b["tick"] - a["tick"]
        if dt_ps > 0:
            segs.append({
                "from_tick": a["tick"],
                "to_tick": b["tick"],
                "dt_ps": dt_ps,
                "dt_ns": round(dt_ps * TICK2NS, 1),
                "type": classify_segment(a, b),
                "from_ev": f"{a['comp']}:{a['event']}",
                "to_ev": f"{b['comp']}:{b['event']}",
            })
    return segs


def make_html(data, target_ns=None):
    chains = data.get("chains", {})
    meta = data.get("meta", {})
    t_min = meta.get("tick_min", 0)
    t_max = meta.get("tick_max", 1)
    span_ps = t_max - t_min or 1

    chain_list = []
    for rid, ch in sorted(chains.items(), key=lambda kv: kv[1].get("first_tick", 0)):
        segs = build_segments(ch["events"])
        tq_hops = sum(1 for s in segs if s["type"] in
                      ("gem5→ubio", "ubio→gem5", "ubio→nsim", "nsim→ubio"))
        chain_list.append({
            "rid": rid,
            "pa": ch.get("pa", "?"),
            "primary_type": ch.get("primary_type", "?"),
            "first_tick": ch.get("first_tick", 0),
            "last_tick": ch.get("last_tick", 0),
            "dur_ns": round(ch.get("duration_ps", 0) * TICK2NS, 1),
            "dur_ps": ch.get("duration_ps", 0),
            "ev_count": len(ch["events"]),
            "tq_hops": tq_hops,
            "segments": segs,
            "events": [{
                "tick": e["tick"], "comp": e["comp"], "event": e["event"],
                "node": e["node"], "extra": e["extra"],
            } for e in ch["events"]],
        })

    data_json = json.dumps(chain_list)
    colors_json = json.dumps(SEG_COLORS)
    type_colors_json = json.dumps(TYPE_COLORS)

    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>TRACE-PERF Chains</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, "Helvetica Neue", sans-serif; margin: 0;
         background: #fff; color: #1e293b; font-size: 13px; }}
  #header {{ position: sticky; top: 0; background: #fff; padding: 10px 14px;
             border-bottom: 2px solid #e2e8f0; z-index: 12; }}
  #header b {{ font-size: 16px; }}
  #header input, #header select {{ border: 1px solid #cbd5e1; padding: 4px 8px;
         margin: 0 4px; border-radius: 4px; font-size: 12px; }}
  #header label {{ margin: 0 6px 0 2px; font-size: 12px; color: #64748b; }}
  #header button {{ background: #3b82f6; color: #fff; border: none; padding: 4px 12px;
         border-radius: 4px; cursor: pointer; font-size: 12px; }}
  #stats {{ font-size: 11px; color: #94a3b8; margin-top: 4px; }}

  .swimlane {{ border-bottom: 1px solid #f1f5f9; display: flex; align-items: stretch;
               min-height: 28px; }}
  .swimlane:hover {{ background: #f8fafc; }}
  .swimlane.over-target {{ border-left: 3px solid #ef4444; }}
  .swimlane-label {{ flex: 0 0 360px; padding: 4px 8px; font-size: 11px; cursor: pointer;
                     white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
                     border-right: 1px solid #f1f5f9; user-select: none;
                     background: #fff; position: relative; z-index: 1; }}
  .swimlane-label .rid {{ color: #3b82f6; font-weight: 600; }}
  .swimlane-label .type {{ display: inline-block; padding: 0 4px; border-radius: 3px;
                           font-size: 10px; font-weight: 600; margin-right: 4px; }}
  .swimlane-label .dur {{ color: #f59e0b; font-weight: 600; }}
  .swimlane-label .over-tag {{ display: inline-block; background: #ef4444; color: #fff;
                               padding: 0 3px; border-radius: 2px; font-size: 9px;
                               font-weight: 700; margin-left: 2px; }}
  .swimlane-canvas {{ flex: 1; position: relative; min-width: 200px; margin: 4px 8px 4px 0; }}
  .seg {{ position: absolute; height: 18px; top: 1px; border-radius: 3px; cursor: pointer;
          min-width: 3px; opacity: 0.85; }}
  .seg:hover {{ opacity: 1; outline: 2px solid #1e293b; z-index: 5; }}
  .target-line {{ position: absolute; top: 0; bottom: -20px; border-left: 2px dashed #ef4444;
                  z-index: 4; pointer-events: none; }}
  .target-label {{ position: absolute; font-size: 9px; color: #ef4444; font-weight: 600;
                   white-space: nowrap; z-index: 5; }}

  /* Time ruler — sticky at very top, above swimlane labels */
  #ruler {{ position: sticky; top: 0; background: #f8fafc; z-index: 11;
            border-bottom: 1px solid #cbd5e1; height: 32px; overflow: hidden; }}
  #ruler-title {{ position: absolute; left: 8px; top: 8px; font-size: 10px;
                  font-weight: 600; color: #475569; width: 344px; }}
  #ruler-inner {{ position: relative; height: 100%; margin-left: 360px; }}
  .ruler-mark {{ position: absolute; bottom: 0; border-left: 1px solid #cbd5e1; height: 8px;
                 pointer-events: none; }}
  .ruler-label {{ position: absolute; top: 6px; font-size: 10px; color: #334155;
                  transform: translateX(-50%); white-space: nowrap; pointer-events: none; }}

  .expanded {{ padding: 6px 12px 6px 360px; font-size: 11px; color: #475569;
              border-bottom: 1px solid #f1f5f9; background: #fafbfc; }}
  .expanded table {{ border-collapse: collapse; width: 100%; }}
  .expanded td {{ padding: 1px 8px; }}
  .expanded td.tick {{ color: #3b82f6; font-weight: 600; }}
  .expanded td.dt-tq {{ color: #3b82f6; }}
  .expanded td.dt-nsim {{ color: #f97316; }}
  .expanded tr.summary td {{ border-top: 1px solid #e2e8f0; padding-top: 4px; color: #f59e0b; }}

  #tooltip {{ display: none; position: fixed; background: #1e293b; color: #f8fafc;
              padding: 6px 10px; border-radius: 6px; font-size: 11px; z-index: 100;
              pointer-events: none; max-width: 360px; line-height: 1.5; }}

  /* Stats table */
  #agg-table {{ margin: 20px 14px; padding: 12px; background: #f8fafc;
               border: 1px solid #e2e8f0; border-radius: 8px; font-size: 12px; }}
  #agg-table table {{ border-collapse: collapse; width: 100%; max-width: 700px; }}
  #agg-table th {{ text-align: left; padding: 3px 10px; border-bottom: 1px solid #cbd5e1;
                   color: #475569; font-weight: 600; }}
  #agg-table td {{ padding: 2px 10px; }}
</style>
</head>
<body>

<div id="header">
  <b>TRACE-PERF Chains</b>
  <label>Filter PA:</label>
  <input id="f-pa" placeholder="0x10018000000" size=16 oninput="render()">
  <label>rid:</label>
  <input id="f-rid" placeholder="7205759..." size=18 oninput="render()">
  <label>Min hops:</label>
  <input id="f-hops" type="number" value="2" min="1" style="width:45px" oninput="render()">
  <label>Min ev:</label>
  <input id="f-ev" type="number" value="10" min="1" style="width:45px" oninput="render()">
  <label>Zoom:</label>
  <input id="f-zoom" type="range" min="0.1" max="100" step="0.1" value="1" style="width:100px" oninput="render()">
  <span id="zoom-val" style="font-size:11px;color:#64748b;min-width:30px;display:inline-block">1.0x</span>
  <button onclick="toggleAll()">expand/collapse</button>
  <button onclick="exportCSV()">export CSV</button>
  <div id="stats"></div>
</div>
<div id="ruler"><div id="ruler-title">relative time (each lane from t=0) →</div><div id="ruler-inner"></div></div>
<div id="chains"></div>
<div id="tooltip"></div>

<div id="agg-table"></div>

<div style="margin:20px 14px; padding:14px; background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; font-size:12px; line-height:1.8; max-width:900px;">
<b style="font-size:14px">Legend</b>
<div style="display:flex; flex-wrap:wrap; gap:16px; margin-top:8px">
<div style="flex:0 0 280px">
<b>Swimlane label:</b><br>
<span style="background:#3b82f6;color:#fff;padding:0 4px;border-radius:3px;font-size:10px">ReadReq</span>
<span style="color:#3b82f6;font-weight:600">rid=72057...</span>
<span style="color:#f59e0b;font-weight:600">2401ns</span>
<span style="background:#ef4444;color:#fff;padding:0 3px;border-radius:2px;font-size:9px">OVER</span>
pa=... ev=44 hops=19<br>
  type badge | rid | duration | OVER tag if dur > target<br>
  pa = physical address | ev = event count | hops = ZMQ Tq hops
</div>
<div style="flex:0 0 280px">
<b>Segment colors:</b><br>
<span style="display:inline-block;width:40px;height:12px;background:#3b82f6;border-radius:2px"></span> Blue = gem5→ubio<br>
<span style="display:inline-block;width:40px;height:12px;background:#22c55e;border-radius:2px"></span> Green = ubio→gem5<br>
<span style="display:inline-block;width:40px;height:12px;background:#f97316;border-radius:2px"></span> Orange = ubio→nsim<br>
<span style="display:inline-block;width:40px;height:12px;background:#8b5cf6;border-radius:2px"></span> Purple = nsim→ubio<br>
<span style="display:inline-block;width:40px;height:12px;background:#71717a;border-radius:2px"></span> Gray = nsim FIFO delay<br>
<span style="display:inline-block;width:40px;height:12px;background:#facc15;border-radius:2px"></span> Yellow = ubio processing
</div>
<div style="flex:0 0 280px">
<b>Dashed red line:</b> target latency<br>
<b>Hover segment:</b> type, duration, events<br>
<b>Click label:</b> expand per-event detail<br>
<b>Top ruler:</b> ns tick marks + grid lines
</div>
</div>
</div>

<script id="chain-data" type="application/json">{data_json}</script>
<script>
var CHAINS = JSON.parse(document.getElementById("chain-data").textContent);
var COLORS = {colors_json};
var TYPE_COLORS = {type_colors_json};
var T_MIN = {t_min};
var T_MAX = {t_max};
var SPAN = {span_ps};
var TARGET_NS = {json.dumps(target_ns)};
var TARGET_PS = TARGET_NS ? TARGET_NS * 1000 : 0;

var expanded = {{}};

function ns(n) {{ return n < 10 ? n.toFixed(1)+"ns" : n < 1000 ? n.toFixed(0)+"ns" : (n/1000).toFixed(1)+"us"; }}
function psToNs(p) {{ return p * 1e-3; }}
function clamp(v, lo, hi) {{ return Math.max(lo, Math.min(hi, v)); }}

// event delegation: swimlane label click
document.getElementById("chains").addEventListener("click", function(e) {{
    var el = e.target.closest("[data-rid]");
    if (!el) return; var rid = el.getAttribute("data-rid");
    expanded[rid] = !expanded[rid]; render();
}});

// event delegation: segment hover
document.getElementById("chains").addEventListener("mouseover", function(e) {{
    var el = e.target.closest("[data-tip]");
    if (!el) return; var tip = document.getElementById("tooltip");
    tip.style.display = "block";
    tip.style.left = (e.clientX + 12) + "px";
    tip.style.top = (e.clientY - 10) + "px";
    tip.innerHTML = el.getAttribute("data-tip").replace(/\\|/g, "<br>");
}});
document.getElementById("chains").addEventListener("mouseout", function(e) {{
    if (!e.target.closest("[data-tip]")) return;
    document.getElementById("tooltip").style.display = "none";
}});

function render() {{
    var fpa = document.getElementById("f-pa").value.toLowerCase();
    var frid = document.getElementById("f-rid").value;
    var mh = parseInt(document.getElementById("f-hops").value) || 2;
    var mev = parseInt(document.getElementById("f-ev").value) || 10;
    var zoom = clamp(parseFloat(document.getElementById("f-zoom").value) || 1, 0.5, 5);
    document.getElementById("zoom-val").textContent = zoom.toFixed(1) + "x";
    var div = document.getElementById("chains");
    div.innerHTML = "";
    var baseW = Math.min(window.innerWidth - 400, 1400);
    var vis = 0, evs = 0;

    // Aggregate stats for visible chains
    var agg = {{}};

    // ── Absolute time axis ──────────────────────────────────────────
    // All chains share the global timeline from T_MIN to T_MAX. Each chain's
    // segments appear at their absolute position, so you can see the
    // chronological order of different requests.

    for (var i = 0; i < CHAINS.length; i++) {{
        var ch = CHAINS[i];
        if (fpa && (ch.pa || "").toLowerCase().indexOf(fpa) < 0) continue;
        if (frid && String(ch.rid).indexOf(frid) !== 0) continue;
        if (ch.tq_hops < mh) continue;
        vis++; evs += ch.ev_count;

        var ptype = ch.primary_type || "?";
        var tc = TYPE_COLORS[ptype] || "#94a3b8";
        var ridStr = String(ch.rid);
        if (ch.ev_count < mev) continue;

        var row = document.createElement("div");
        row.className = "swimlane" + (ch.dur_ns > TARGET_NS && TARGET_NS > 0 ? " over-target" : "");

        var label = document.createElement("div");
        label.className = "swimlane-label";
        label.setAttribute("data-rid", ridStr);
        label.title = "click to expand";

        var typeBadge = document.createElement("span");
        typeBadge.className = "type";
        typeBadge.style.cssText = "background:" + tc + ";color:#fff";
        typeBadge.textContent = ptype;
        label.appendChild(typeBadge);

        if (TARGET_NS > 0 && ch.dur_ns > TARGET_NS) {{
            var overTag = document.createElement("span");
            overTag.className = "over-tag"; overTag.textContent = "OVER";
            label.appendChild(overTag);
        }}

        var ridSpan = document.createElement("span");
        ridSpan.className = "rid"; ridSpan.textContent = "rid=" + ridStr;
        label.appendChild(ridSpan);

        label.appendChild(document.createTextNode(" "));
        var durSpan = document.createElement("span");
        durSpan.className = "dur"; durSpan.textContent = ns(ch.dur_ns);
        label.appendChild(durSpan);

        label.appendChild(document.createTextNode(
            " pa=" + (ch.pa || "?") + " ev=" + ch.ev_count + " hops=" + ch.tq_hops));
        row.appendChild(label);

        var canvas = document.createElement("div");
        canvas.className = "swimlane-canvas";
        var cw = baseW * zoom;
        canvas.style.width = cw + "px";

        for (var j = 0; j < ch.segments.length; j++) {{
            var s = ch.segments[j];
            // Per-chain relative position: offset from THIS chain's first event.
            var lpx = (s.from_tick - T_MIN) / SPAN * cw;
            var wpx = Math.max(s.dt_ps / SPAN * cw, 2);
            if (wpx < 0.5) continue;

            var seg = document.createElement("div");
            seg.className = "seg";
            seg.style.cssText = "left:" + lpx.toFixed(1) + "px; width:" + wpx.toFixed(1) +
                "px; background:" + (COLORS[s.type] || "#aaa");

            // F.1.3: inline label on wide segments
            if (wpx > 40) {{
                seg.textContent = "+" + ns(s.dt_ns);
                seg.style.fontSize = "9px"; seg.style.color = "#fff";
                seg.style.fontWeight = "600"; seg.style.paddingLeft = "3px";
                seg.style.lineHeight = "18px"; seg.style.overflow = "hidden";
            }}

            seg.setAttribute("data-tip", s.type + "|" + ns(s.dt_ns) + "|" + s.from_ev + " -> " + s.to_ev);
            // Large idle gaps (>10us) are between unrelated requests, not real delays.
            if (s.dt_ns > 10000) seg.style.opacity = "0.12";
            canvas.appendChild(seg);

            // aggregate: only count segments <10us for meaningful stats
            var key = s.type;
            if (!agg[key]) agg[key] = {{ vals: [], large: 0 }};
            if (s.dt_ns > 10000)
                agg[key].large++;
            else
                agg[key].vals.push(s.dt_ns);
        }}

        if (TARGET_PS > 0) {{
            var tx = TARGET_PS / SPAN * cw;
            var tLine = document.createElement("div");
            tLine.className = "target-line";
            tLine.style.left = tx.toFixed(1) + "px";
            canvas.appendChild(tLine);
        }}
        row.appendChild(canvas);
        div.appendChild(row);

        if (expanded[ridStr]) {{
            var expDiv = document.createElement("div");
            expDiv.className = "expanded";
            var tbl = document.createElement("table");
            var lastTick = null, tqSum = 0;
            for (var k = 0; k < ch.events.length; k++) {{
                var ev = ch.events[k];
                var tr = document.createElement("tr");
                var dtStr = "", dtCls = "";
                if (lastTick !== null) {{
                    var dps = ev.tick - lastTick;
                    dtStr = "+" + ns(psToNs(dps));
                    var isTq = (ev.event === "RECV_GEM5" || ev.event === "RECV_NET" || ev.event === "RECV");
                    dtCls = isTq ? "dt-tq" : "dt-nsim";
                    if (isTq) tqSum += dps;
                }}
                [ev.tick, ev.comp + ":" + ev.event, "n" + ev.node,
                 ev.extra || "", dtStr].forEach(function(v, ci) {{
                    var td = tr.appendChild(document.createElement("td"));
                    td.textContent = v;
                    if (ci === 0) td.className = "tick";
                    if (ci === 4 && dtCls) td.className = dtCls;
                }});
                tbl.appendChild(tr);
                lastTick = ev.tick;
            }}
            var sumRow = tbl.insertRow();
            sumRow.className = "summary";
            var sumCell = sumRow.insertCell();
            sumCell.colSpan = 5;
            sumCell.textContent = "total: " + ns(ch.dur_ns) + " | events: " + ch.ev_count + " | Tq hops: " + ch.tq_hops;
            expDiv.appendChild(tbl);
            div.appendChild(expDiv);
        }}
    }}

    document.getElementById("stats").innerHTML =
        "Showing " + vis + "/" + CHAINS.length + " chains | events: " + evs +
        " | absolute timeline: " + ns(psToNs(T_MIN)) + " \\u2013 " + ns(psToNs(T_MAX));

    // Time ruler — absolute axis (T_MIN to T_MAX).
    var rulerDiv = document.getElementById("ruler-inner");
    rulerDiv.innerHTML = "";
    var cw = baseW * zoom;
    rulerDiv.style.width = cw + "px";
    var spanPs = T_MAX - T_MIN;
    var spanNs = psToNs(spanPs);
    var targetMarks = Math.max(6, Math.round(10 * zoom));
    var rawStep = spanPs / targetMarks;
    var mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
    var residual = rawStep / mag;
    var stepPs;
    if (residual < 1.5) stepPs = 1 * mag;
    else if (residual < 3.5) stepPs = 2 * mag;
    else if (residual < 7.5) stepPs = 5 * mag;
    else stepPs = 10 * mag;
    stepPs = Math.max(stepPs, 1);
    for (var tPs = T_MIN; tPs <= T_MAX + stepPs; tPs += stepPs) {{
        var px = (tPs - T_MIN) / spanPs * cw;
        if (px > cw + 1) break;
        var mark = document.createElement("div");
        mark.className = "ruler-mark"; mark.style.left = px + "px"; rulerDiv.appendChild(mark);
        var lbl = document.createElement("div");
        lbl.className = "ruler-label"; lbl.style.left = px + "px";
        lbl.textContent = ns(psToNs(tPs));
        rulerDiv.appendChild(lbl);
    }}
    if (TARGET_NS > 0) {{
        var tpx = TARGET_PS / SPAN * cw;
        var tlbl = document.createElement("div");
        tlbl.className = "target-label";
        tlbl.style.left = (tpx + 4) + "px"; tlbl.style.top = "0px";
        tlbl.textContent = "target=" + ns(TARGET_NS);
        rulerDiv.appendChild(tlbl);
    }}

    // F.1.4: aggregate stats table
    var atbl = document.getElementById("agg-table");
    var segOrder = ["gem5→ubio","ubio→nsim","nsim_fifo","nsim→ubio","ubio→gem5","ubio_proc","other"];
    var html = "<b>Segment Statistics</b> <span style='color:#94a3b8;font-size:11px'>(gaps >10us excluded; hover on bars to see raw values)</span>" +
               "<table><tr><th>Type</th><th>Count</th><th>P50(ns)</th><th>Avg(ns)</th><th>P99(ns)</th><th>Large gaps</th></tr>";
    for (var si = 0; si < segOrder.length; si++) {{
        var stype = segOrder[si];
        var a = agg[stype];
        if (!a || (a.vals.length === 0 && a.large === 0)) continue;
        a.vals.sort(function(x,y){{return x-y;}});
        var n = a.vals.length;
        var avg = n > 0 ? a.vals.reduce(function(s,v){{return s+v;}},0) / n : 0;
        var p50 = n > 0 ? a.vals[Math.floor(n*0.5)] : 0;
        var p99 = n > 0 ? a.vals[Math.floor(n*0.99)] : 0;
        html += "<tr><td style='font-weight:600'><span style='display:inline-block;width:12px;height:12px;background:" +
                (COLORS[stype]||"#aaa") + ";border-radius:2px;margin-right:4px'></span>" + stype +
                "</td><td>" + n + "</td><td style='font-weight:600;color:#334155'>" + p50.toFixed(1) + "</td><td>" + (n>0?avg.toFixed(1):"-") + "</td><td>" + p99.toFixed(1) + "</td><td style='color:#94a3b8'>" + (a.large>0?"+":"") + a.large + "</td></tr>";
    }}
    html += "</table>";
    if (vis === 0) html = "<b>Segment Statistics</b><p style='color:#94a3b8'>No chains match current filter</p>";
    atbl.innerHTML = html;
}}

function toggleAll() {{
    var any = false;
    for (var i = 0; i < CHAINS.length; i++) if (expanded[String(CHAINS[i].rid)]) {{ any = true; break; }}
    for (var i = 0; i < CHAINS.length; i++) expanded[String(CHAINS[i].rid)] = !any;
    render();
}}

// F.1.5: CSV export
function exportCSV() {{
    var fpa = document.getElementById("f-pa").value.toLowerCase();
    var frid = document.getElementById("f-rid").value;
    var mh = parseInt(document.getElementById("f-hops").value) || 2;
    var mev = parseInt(document.getElementById("f-ev").value) || 10;
    var lines = ["rid,pa,type,dur_ns,tq_hops,ev_count"];
    for (var i = 0; i < CHAINS.length; i++) {{
        var ch = CHAINS[i];
        if (fpa && (ch.pa || "").toLowerCase().indexOf(fpa) < 0) continue;
        if (frid && String(ch.rid).indexOf(frid) !== 0) continue;
        if (ch.tq_hops < mh) continue;
        lines.push([ch.rid, ch.pa||"", ch.primary_type, ch.dur_ns, ch.tq_hops, ch.ev_count].join(","));
    }}
    var blob = new Blob([lines.join("\\n")], {{type:"text/csv"}});
    var a = document.createElement("a"); a.href = URL.createObjectURL(blob);
    a.download = "trace_chains.csv"; a.click();
}}

window.addEventListener("resize", render);
render();
</script>
</body>
</html>"""
    # No target-label on each row — now it's on the ruler


def main():
    ap = argparse.ArgumentParser(description="Render trace chain JSON as HTML")
    ap.add_argument("input", nargs="?", help="JSON file from trace2chain (or stdin)")
    ap.add_argument("--target-ns", type=float, default=415,
                    help="Target latency in ns (dashed line overlay)")
    args = ap.parse_args()

    if args.input and os.path.isfile(args.input):
        with open(args.input) as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)

    print(make_html(data, target_ns=args.target_ns))


if __name__ == "__main__":
    main()
