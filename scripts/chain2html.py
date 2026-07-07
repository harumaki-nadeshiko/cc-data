#!/usr/bin/env python3
"""Convert trace2chain JSON to interactive HTML timeline.

Usage:
    python3 scripts/chain2html.py /tmp/tc5_chains.json > /tmp/tc5.html
    python3 scripts/chain2html.py --target-ns 415 /tmp/tc5_chains.json > /tmp/tc5.html
"""
import sys, os, json, argparse

TICK2NS = 1e-6  # ps -> ns

# Bright saturated colors for segments
SEG_COLORS = {
    "gem5→ubio":  "#3b82f6",  # bright blue
    "ubio→gem5":  "#22c55e",  # bright green
    "ubio→nsim":  "#f97316",  # bright orange
    "nsim→ubio":  "#8b5cf6",  # bright purple
    "nsim_fifo":  "#71717a",  # gray
    "ubio_proc":  "#facc15",  # yellow
    "other":      "#94a3b8",  # light gray
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

    # Build lightweight chain list sorted by first_tick
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
                "tick": e["tick"],
                "comp": e["comp"],
                "event": e["event"],
                "node": e["node"],
                "extra": e["extra"],
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
             border-bottom: 2px solid #e2e8f0; z-index: 10; }}
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
  .swimlane-label {{ flex: 0 0 420px; padding: 4px 8px; font-size: 11px; cursor: pointer;
                     white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
                     border-right: 1px solid #f1f5f9; user-select: none; }}
  .swimlane-label .rid {{ color: #3b82f6; font-weight: 600; }}
  .swimlane-label .type {{ display: inline-block; padding: 0 4px; border-radius: 3px;
                           font-size: 10px; font-weight: 600; margin-right: 4px; }}
  .swimlane-label .dur {{ color: #f59e0b; font-weight: 600; }}
  .swimlane-canvas {{ flex: 1; position: relative; min-width: 200px; margin: 4px 8px 4px 0; }}
  .seg {{ position: absolute; height: 18px; top: 1px; border-radius: 3px; cursor: pointer;
          min-width: 3px; opacity: 0.85; }}
  .seg:hover {{ opacity: 1; outline: 2px solid #1e293b; z-index: 5; }}
  .target-line {{ position: absolute; top: 0; bottom: 0; border-left: 2px dashed #ef4444;
                  z-index: 4; pointer-events: none; }}
  .target-label {{ position: absolute; top: -2px; font-size: 9px; color: #ef4444;
                   font-weight: 600; white-space: nowrap; }}

  .expanded {{ padding: 6px 12px 6px 420px; font-size: 11px; color: #475569;
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
  <button onclick="toggleAll()">expand/collapse all</button>
  <div id="stats"></div>
</div>
<div id="chains"></div>
<div id="tooltip"></div>

<div style="margin:20px 14px; padding:14px; background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; font-size:12px; line-height:1.8; max-width:900px;">
<b style="font-size:14px">Legend</b>

<div style="display:flex; flex-wrap:wrap; gap:16px; margin-top:8px">
<div style="flex:0 0 280px">
<b>Swimlane label (left side of each row):</b><br>
<span style="background:#3b82f6;color:#fff;padding:0 4px;border-radius:3px;font-size:10px">ReadReq</span>
<span style="color:#3b82f6;font-weight:600">rid=72057594037927937</span>
<span style="color:#f59e0b;font-weight:600">2401ns</span>
pa=0x10018000000 ev=44 hops=19<br>
&nbsp;&nbsp;type badge = coherence request type |
rid = unique request id |
duration = total end-to-end latency |
ev = event count |
hops = number of Tq (ZMQ) hops
</div>

<div style="flex:0 0 280px">
<b>Color-coded segments (click swimlane label to expand):</b><br>
<div style="display:flex;align-items:center;gap:6px;margin:2px 0">
  <span style="display:inline-block;width:40px;height:12px;background:#3b82f6;border-radius:2px"></span>
  <b>Blue</b> = gem5 &rarr; ubio (Tq ZMQ IPC, ~100ns)
</div>
<div style="display:flex;align-items:center;gap:6px;margin:2px 0">
  <span style="display:inline-block;width:40px;height:12px;background:#22c55e;border-radius:2px"></span>
  <b>Green</b> = ubio &rarr; gem5 (Tq ZMQ IPC, ~100ns)
</div>
<div style="display:flex;align-items:center;gap:6px;margin:2px 0">
  <span style="display:inline-block;width:40px;height:12px;background:#f97316;border-radius:2px"></span>
  <b>Orange</b> = ubio &rarr; nsim (Tq ZMQ IPC, ~100ns)
</div>
<div style="display:flex;align-items:center;gap:6px;margin:2px 0">
  <span style="display:inline-block;width:40px;height:12px;background:#8b5cf6;border-radius:2px"></span>
  <b>Purple</b> = nsim &rarr; ubio (Tq ZMQ IPC, ~100ns)
</div>
<div style="display:flex;align-items:center;gap:6px;margin:2px 0">
  <span style="display:inline-block;width:40px;height:12px;background:#71717a;border-radius:2px"></span>
  <b>Gray</b> = nsim FIFO forwarding delay
</div>
<div style="display:flex;align-items:center;gap:6px;margin:2px 0">
  <span style="display:inline-block;width:40px;height:12px;background:#facc15;border-radius:2px"></span>
  <b>Yellow</b> = ubio local processing
</div>
</div>

<div style="flex:0 0 280px">
<b>Dashed red vertical line:</b><br>
&nbsp;&nbsp;Target latency reference (default 415ns)<br>
<b>Hover on a colored segment:</b><br>
&nbsp;&nbsp;Shows segment type, duration, from &rarr; to event<br>
<b>Click swimlane label:</b><br>
&nbsp;&nbsp;Expand to see every event with per-hop delta time<br>
<b>Filter inputs at top:</b><br>
&nbsp;&nbsp;Filter by PA (hex), rid prefix, or minimum Tq hops<br>
<b>Min hops filter:</b><br>
&nbsp;&nbsp;Set to 1 to see all chains (incl. local-only); set to 3+ to see only multi-node
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
var TARGET_PS = {int(target_ns * 1000) if target_ns else 0};

var expanded = {{}};

function ns(n) {{ return n < 10 ? n.toFixed(1)+"ns" : n < 1000 ? n.toFixed(0)+"ns" : (n/1000).toFixed(1)+"us"; }}

// === event delegation: click on swimlane labels ===
document.getElementById("chains").addEventListener("click", function(e) {{
    var el = e.target.closest("[data-rid]");
    if (!el) return;
    var rid = el.getAttribute("data-rid");
    expanded[rid] = !expanded[rid];
    render();
}});

// === event delegation: hover on segments ===
document.getElementById("chains").addEventListener("mouseover", function(e) {{
    var el = e.target.closest("[data-tip]");
    if (!el) return;
    var tip = document.getElementById("tooltip");
    tip.style.display = "block";
    tip.style.left = (e.clientX + 12) + "px";
    tip.style.top = (e.clientY - 10) + "px";
    tip.innerHTML = el.getAttribute("data-tip").replace(/\|/g, "<br>");
}});
document.getElementById("chains").addEventListener("mouseout", function(e) {{
    if (!e.target.closest("[data-tip]")) return;
    document.getElementById("tooltip").style.display = "none";
}});

function render() {{
    var fpa = document.getElementById("f-pa").value.toLowerCase();
    var frid = document.getElementById("f-rid").value;
    var mh = parseInt(document.getElementById("f-hops").value) || 2;
    var div = document.getElementById("chains");
    div.innerHTML = "";
    var cw = Math.min(window.innerWidth - 460, 1400);
    var vis = 0, evs = 0;

    for (var i = 0; i < CHAINS.length; i++) {{
        var ch = CHAINS[i];
        if (fpa && (ch.pa || "").toLowerCase().indexOf(fpa) < 0) continue;
        if (frid && String(ch.rid).indexOf(frid) !== 0) continue;
        if (ch.tq_hops < mh) continue;
        vis++; evs += ch.ev_count;

        var ptype = ch.primary_type || "?";
        var tc = TYPE_COLORS[ptype] || "#94a3b8";
        var ridStr = String(ch.rid);

        // ----- swimlane row -----
        var row = document.createElement("div");
        row.className = "swimlane";

        var label = document.createElement("div");
        label.className = "swimlane-label";
        label.setAttribute("data-rid", ridStr);
        label.title = "click to expand";

        var typeBadge = document.createElement("span");
        typeBadge.className = "type";
        typeBadge.style.cssText = "background:" + tc + ";color:#fff";
        typeBadge.textContent = ptype;
        label.appendChild(typeBadge);

        var ridSpan = document.createElement("span");
        ridSpan.className = "rid";
        ridSpan.textContent = "rid=" + ridStr;
        label.appendChild(ridSpan);

        label.appendChild(document.createTextNode(" "));
        var durSpan = document.createElement("span");
        durSpan.className = "dur";
        durSpan.textContent = ns(ch.dur_ns);
        label.appendChild(durSpan);

        label.appendChild(document.createTextNode(
            " pa=" + (ch.pa || "?") + " ev=" + ch.ev_count + " hops=" + ch.tq_hops));
        row.appendChild(label);

        // ----- canvas -----
        var canvas = document.createElement("div");
        canvas.className = "swimlane-canvas";
        canvas.style.width = cw + "px";

        for (var j = 0; j < ch.segments.length; j++) {{
            var s = ch.segments[j];
            var lpx = (s.from_tick - T_MIN) / SPAN * cw;
            var wpx = Math.max(s.dt_ps / SPAN * cw, 3);
            if (wpx < 0.5) continue;

            var seg = document.createElement("div");
            seg.className = "seg";
            seg.style.cssText = "left:" + lpx.toFixed(1) + "px; width:" + wpx.toFixed(1) +
                "px; background:" + (COLORS[s.type] || "#aaa");
            // Store tooltip data using | as separator (rendered as <br>)
            seg.setAttribute("data-tip", s.type + "|" + ns(s.dt_ns) + "|" + s.from_ev + " -> " + s.to_ev);
            canvas.appendChild(seg);
        }}

        if (TARGET_PS > 0) {{
            var tx = TARGET_PS / SPAN * cw;
            var tLine = document.createElement("div");
            tLine.className = "target-line";
            tLine.style.left = tx.toFixed(1) + "px";
            canvas.appendChild(tLine);
            var tLabel = document.createElement("div");
            tLabel.className = "target-label";
            tLabel.style.left = (tx + 4).toFixed(1) + "px";
            tLabel.textContent = "target=" + ns(TARGET_PS * 1e-6);
            canvas.appendChild(tLabel);
        }}
        row.appendChild(canvas);
        div.appendChild(row);

        // ----- expanded detail -----
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
                    dtStr = "+" + ns(dps * 1e-6);
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
        " | range: " + ns(T_MIN*1e-6) + " \\u2013 " + ns(T_MAX*1e-6);
}}

function toggleAll() {{
    var any = false;
    for (var i = 0; i < CHAINS.length; i++) if (expanded[String(CHAINS[i].rid)]) {{ any = true; break; }}
    for (var i = 0; i < CHAINS.length; i++) expanded[String(CHAINS[i].rid)] = !any;
    render();
}}

window.addEventListener("resize", render);
render();
</script>
</body>
</html>"""


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
