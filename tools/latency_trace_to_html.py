#!/usr/bin/env python3
"""Latency Trace to HTML Timeline Generator.

Reads a combined UBLatency + UBST debug log, groups events by physical
address (PA), and renders an interactive HTML timeline with:

  - Horizontal lanes per node/socket.
  - Arrows between lanes for cross-node message ENQUEUE/DELIVER pairs.
  - Color-coded bars by message type.
  - Tooltips showing epoch, reqId, and latency.
  - Collapsible per-PA sections.

Usage:
    python3 tools/latency_trace_to_html.py \
        --log m5out/e2e/tc1/debug.log \
        --out m5out/e2e/tc1/latency_trace.html
"""
import argparse
import re
import sys
import os
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

# ═══════════════════════════════════════════════════════════════════
# Parsing
# ═══════════════════════════════════════════════════════════════════

_RE_UBLAT = re.compile(
    r"\[UBLAT\]\s+tick=(\d+)\s+src=(\d+),(\d+)\s+dst=(\d+),(\d+)\s+"
    r"type=(\S+)\s+pa=(\S+)\s+epoch=(\d+)\s+reqId=(\d+)\s+"
    r"action=(\S+)"
)

_RE_UBST = re.compile(
    r"\[UBST\]\s+tick=(\d+)\s+home=(\d+),(\d+)\s+pa=(\S+)\s+"
    r"old=(\S+)\s+new=(\S+)\s+epoch=(\d+)\s+sharers=(\S+)\s+"
    r"action=(\S+)"
)


def parse_log(filepath: str) -> Tuple[List[dict], List[dict]]:
    """Parse a combined UBLatency/UBST debug log.

    Returns (ublat_events, ubst_events).
    """
    ublat_events: List[dict] = []
    ubst_events: List[dict] = []

    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            m_ublat = _RE_UBLAT.search(line)
            if m_ublat:
                ublat_events.append({
                    "tick": int(m_ublat.group(1)),
                    "src_node": int(m_ublat.group(2)),
                    "src_socket": int(m_ublat.group(3)),
                    "dst_node": int(m_ublat.group(4)),
                    "dst_socket": int(m_ublat.group(5)),
                    "type": m_ublat.group(6),
                    "pa": int(m_ublat.group(7), 0),   # auto-detect 0x hex
                    "epoch": int(m_ublat.group(8)),
                    "reqId": int(m_ublat.group(9)),
                    "action": m_ublat.group(10),
                })
                continue

            m_ubst = _RE_UBST.search(line)
            if m_ubst:
                ubst_events.append({
                    "tick": int(m_ubst.group(1)),
                    "home_node": int(m_ubst.group(2)),
                    "home_socket": int(m_ubst.group(3)),
                    "pa": int(m_ubst.group(4), 0),     # auto-detect 0x hex
                    "old_state": m_ubst.group(5),
                    "new_state": m_ubst.group(6),
                    "epoch": int(m_ubst.group(7)),
                    "sharers": int(m_ubst.group(8), 0), # auto-detect 0x hex
                    "action": m_ubst.group(9),
                })

    return ublat_events, ubst_events


# ═══════════════════════════════════════════════════════════════════
# Colour / layout constants
# ═══════════════════════════════════════════════════════════════════

MSG_COLORS: Dict[str, str] = {
    "ReadReq":          "#1f77b4",
    "ReadResp":         "#2ca02c",
    "RecallReq":        "#d62728",
    "RecallResp":       "#ff7f0e",
    "InvalidateReq":    "#9467bd",
    "InvalidateAck":    "#8c564b",
    "WritebackReq":     "#e377c2",
    "WritebackResp":    "#7f7f7f",
    "EvictReq":         "#bcbd22",
    "EvictResp":        "#17becf",
    "UpgradeReq":       "#aec7e8",
    "UpgradeResp":      "#ffbb78",
    "UpgradeDoneReq":   "#98df8a",
    "UpgradeDoneResp":  "#c5b0d5",
    "ClearReq":         "#c49c94",
    "ClearResp":        "#f7b6d2",
    "UpgradeAckNotify": "#dbdb8d",
    "QueryLineMetaReq": "#9edae5",
    "QueryLineMetaResp":"#e5e5e5",
    "HomeWritebackNotify": "#f0e442",
}

STATE_COLORS: Dict[str, str] = {
    "G_I": "#e0e0e0",
    "G_S": "#72bcd4",
    "G_E": "#ffcc00",
    "G_M": "#ff4444",
    "Tombstone": "#cccccc",
}

ACTION_COLORS: Dict[str, str] = {
    "OUTSTANDING": "#fff2cc",
    "COMMIT":      "#d5e8d4",
    "RETIRE":      "#dae8fc",
}


def _node_key(node: int, socket: int) -> str:
    return f"N{node}S{socket}"


def _msg_color(msg_type: str) -> str:
    return MSG_COLORS.get(msg_type, "#999999")


def _state_color(state: str) -> str:
    return STATE_COLORS.get(state, "#999999")


# ═══════════════════════════════════════════════════════════════════
# HTML generator
# ═══════════════════════════════════════════════════════════════════

def generate_html(ublat_events: List[dict],
                  ubst_events: List[dict],
                  title: str = "UB Latency Trace") -> str:
    """Generate a standalone interactive HTML timeline."""

    # Collect all (node, socket) pairs and determine global tick range
    all_nodes: set = set()
    min_tick = float("inf")
    max_tick = 0
    for ev in ublat_events:
        all_nodes.add((ev["src_node"], ev["src_socket"]))
        all_nodes.add((ev["dst_node"], ev["dst_socket"]))
        t = ev["tick"]
        if t < min_tick:
            min_tick = t
        if t > max_tick:
            max_tick = t
    for ev in ubst_events:
        all_nodes.add((ev["home_node"], ev["home_socket"]))
        t = ev["tick"]
        if t < min_tick:
            min_tick = t
        if t > max_tick:
            max_tick = t

    if min_tick == float("inf"):
        min_tick = 0
    tick_range = max_tick - min_tick or 1

    node_list = sorted(all_nodes, key=lambda x: (x[0], x[1]))
    lane_count = len(node_list)
    lane_height = 40
    header_height = 30
    lane_map = {key: idx for idx, key in enumerate(node_list)}

    # Group events by PA
    pa_groups: defaultdict = defaultdict(lambda: {"ublat": [], "ubst": []})
    for ev in ublat_events:
        pa_groups[ev["pa"]]["ublat"].append(ev)
    for ev in ubst_events:
        pa_groups[ev["pa"]]["ubst"].append(ev)

    # ── Build HTML ─────────────────────────────────────────────────

    # Styles
    styles = f"""
    <style>
        body {{ font-family: -apple-system, 'Segoe UI', sans-serif;
               margin: 0; padding: 16px; background: #f8f9fa; }}
        h1 {{ margin: 0 0 4px 0; font-size: 18px; }}
        .legend {{ display: flex; flex-wrap: wrap; gap: 4px 12px;
                   margin: 8px 0 16px; font-size: 11px; }}
        .legend-item {{ display: inline-flex; align-items: center; gap: 4px; }}
        .legend-swatch {{ width: 12px; height: 12px; border-radius: 2px;
                          border: 1px solid #888; }}
        .pa-group {{ margin-bottom: 24px; border: 1px solid #ccc;
                     border-radius: 6px; overflow: hidden; background: #fff; }}
        .pa-header {{ padding: 6px 12px; background: #e9ecef;
                      font-weight: 600; font-size: 13px;
                      cursor: pointer; user-select: none; }}
        .pa-header:hover {{ background: #dee2e6; }}
        .pa-body {{ display: block; }}
        .pa-body.collapsed {{ display: none; }}
        .timeline-svg {{ width: 100%; overflow-x: auto; }}
        .tooltip {{ position: absolute; background: rgba(0,0,0,0.85);
                    color: #fff; padding: 4px 8px; border-radius: 4px;
                    font-size: 11px; pointer-events: none; z-index: 999;
                    white-space: nowrap; display: none; }}
        .lane-label {{ font-size: 10px; fill: #333; }}
        .axis-label {{ font-size: 9px; fill: #999; }}
        /* state-change marker */
        .state-marker {{ cursor: pointer; }}
    </style>
    """

    # JavaScript for tooltips and collapsible sections
    script = """
    <script>
    function togglePaGroup(header) {
        const body = header.nextElementSibling;
        body.classList.toggle('collapsed');
    }
    const tooltip = document.createElement('div');
    tooltip.className = 'tooltip';
    document.body.appendChild(tooltip);
    document.addEventListener('mousemove', function(e) {
        tooltip.style.left = (e.pageX + 12) + 'px';
        tooltip.style.top = (e.pageY - 8) + 'px';
    });
    function showTooltip(ev, text) {
        tooltip.textContent = text;
        tooltip.style.display = 'block';
    }
    function hideTooltip(ev) {
        tooltip.style.display = 'none';
    }
    </script>
    """

    # Legend
    legend_html = '<div class="legend"><b>Msg types:</b>'
    for mtype in sorted(MSG_COLORS.keys()):
        legend_html += (
            f'<span class="legend-item">'
            f'<span class="legend-swatch" style="background:{MSG_COLORS[mtype]}"></span>'
            f'{mtype}</span>'
        )
    legend_html += ' | <b>States:</b>'
    for sname in sorted(STATE_COLORS.keys()):
        legend_html += (
            f'<span class="legend-item">'
            f'<span class="legend-swatch" style="background:{STATE_COLORS[sname]}"></span>'
            f'{sname}</span>'
        )
    legend_html += '</div>'

    parts = [f"<html><head><meta charset='UTF-8'><title>{title}</title>{styles}</head><body>",
             f"<h1>{title}</h1>",
             legend_html]

    # Per-PA groups
    for pa, group in sorted(pa_groups.items()):
        ue = group["ublat"]
        se = group["ubst"]

        pa_min_tick = min((ev["tick"] for ev in ue), default=min_tick)
        pa_max_tick = max((ev["tick"] for ev in ue), default=max_tick)
        pa_range = pa_max_tick - pa_min_tick or 1

        svg_total_width = 1600
        svg_total_height = lane_count * lane_height + header_height + 20

        svg_parts = [f'<svg width="{svg_total_width}" height="{svg_total_height}" '
                     f'viewBox="0 0 {svg_total_width} {svg_total_height}" '
                     f'xmlns="http://www.w3.org/2000/svg">']

        # Horizontal grid lines per lane
        for idx in range(lane_count):
            y = header_height + idx * lane_height + lane_height // 2
            svg_parts.append(
                f'<line x1="0" y1="{y}" x2="{svg_total_width}" y2="{y}" '
                f'stroke="#ddd" stroke-width="1"/>'
            )
            node, sock = node_list[idx]
            svg_parts.append(
                f'<text x="4" y="{header_height + idx * lane_height + 14}" '
                f'class="lane-label">'
                f'N{node}S{sock}</text>'
            )

        def tick_to_x(t: int) -> float:
            rel = (t - pa_min_tick) / pa_range
            return 10 + rel * (svg_total_width - 20)

        # State change markers (UBST)
        for sev in se:
            t = sev["tick"]
            ns = _node_key(sev["home_node"], sev["home_socket"])
            idx = lane_map.get((sev["home_node"], sev["home_socket"]))
            if idx is None:
                continue
            x = tick_to_x(t)
            y = header_height + idx * lane_height + lane_height // 2
            color = ACTION_COLORS.get(sev["action"], "#ccc")
            shape = "rect"
            w = 8
            h = 16
            tt_text = (f'{sev["action"]} {sev["old_state"]}->{sev["new_state"]} '
                       f'epoch={sev["epoch"]} sharers={sev["sharers"]:#x}')
            svg_parts.append(
                f'<{shape} x="{x - w/2:.1f}" y="{y - h/2:.1f}" width="{w}" height="{h}" '
                f'fill="{color}" stroke="#333" stroke-width="1" class="state-marker" '
                f'onmouseover="showTooltip(event,&#39;{tt_text}&#39;)" '
                f'onmouseout="hideTooltip(event)"/>'
            )

        # ENQUEUE events — draw message rectangles
        for ev in ue:
            if ev["action"] != "ENQUEUE":
                continue
            src_key = (ev["src_node"], ev["src_socket"])
            src_idx = lane_map.get(src_key)
            if src_idx is None:
                continue
            x = tick_to_x(ev["tick"])
            y = header_height + src_idx * lane_height + lane_height // 2
            w = max(6, tick_to_x(ev["tick"] + 1) - x)
            h = 10
            color = _msg_color(ev["type"])
            tt_text = (f'{ev["type"]} ENQUEUE tick={ev["tick"]} '
                       f'pa={ev["pa"]:#x} epoch={ev["epoch"]} reqId={ev["reqId"]}')
            svg_parts.append(
                f'<rect x="{x - w/2:.1f}" y="{y - h/2:.1f}" width="{w}" height="{h}" '
                f'fill="{color}" stroke="#555" stroke-width="1" rx="2" '
                f'onmouseover="showTooltip(event,&#39;{tt_text}&#39;)" '
                f'onmouseout="hideTooltip(event)"/>'
            )

        # ENQUEUE→DEQUEUE arrows (draw line from enqueue src to dequeue router)
        # We match ENQUEUE+DEQUEUE by (src_node,src_socket,dst_node,dst_socket,type,pa,epoch,reqId)
        enq_map: dict = {}
        for ev in ue:
            if ev["action"] != "ENQUEUE":
                continue
            key = (ev["src_node"], ev["src_socket"],
                   ev["dst_node"], ev["dst_socket"],
                   ev["type"], ev["pa"], ev["epoch"], ev["reqId"])
            enq_map[key] = ev

        for ev in ue:
            if ev["action"] != "DEQUEUE":
                continue
            key = (ev["src_node"], ev["src_socket"],
                   ev["dst_node"], ev["dst_socket"],
                   ev["type"], ev["pa"], ev["epoch"], ev["reqId"])
            enq_ev = enq_map.get(key)
            if enq_ev is None:
                continue
            src_idx = lane_map.get((enq_ev["src_node"], enq_ev["src_socket"]))
            dst_idx = lane_map.get((ev["dst_node"], ev["dst_socket"]))
            if src_idx is None or dst_idx is None:
                continue
            x1 = tick_to_x(enq_ev["tick"])
            y1 = header_height + src_idx * lane_height + lane_height // 2
            x2 = tick_to_x(ev["tick"])
            y2 = header_height + dst_idx * lane_height + lane_height // 2
            color = _msg_color(ev["type"])
            lat = ev["tick"] - enq_ev["tick"]
            tt_text = (f'{ev["type"]} enq_tick={enq_ev["tick"]} '
                       f'deq_tick={ev["tick"]} lat={lat}')
            svg_parts.append(
                f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                f'stroke="{color}" stroke-width="1.5" stroke-opacity="0.7" '
                f'marker-end="url(#arrow-{pa})" '
                f'onmouseover="showTooltip(event,&#39;{tt_text}&#39;)" '
                f'onmouseout="hideTooltip(event)"/>'
            )

        # Arrow marker definition (one per PA group to avoid re-use issues)
        svg_parts.insert(1, (
            f'<defs><marker id="arrow-{pa}" markerWidth="6" markerHeight="6" '
            f'refX="5" refY="3" orient="auto">'
            f'<path d="M0,0 L6,3 L0,6 Z" fill="#555"/></marker></defs>'
        ))

        svg_parts.append("</svg>")

        pa_hex = f"0x{pa:x}"
        parts.append(
            f'<div class="pa-group">'
            f'<div class="pa-header" onclick="togglePaGroup(this)">'
            f'▶ PA {pa_hex} — {len(ue)} msg events, {len(se)} state changes'
            f' (tick {pa_min_tick}–{pa_max_tick})'
            f'</div>'
            f'<div class="pa-body">{"".join(svg_parts)}</div>'
            f'</div>'
        )

    parts.extend([script, "</body></html>"])
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Convert UBLatency/UBST debug log to HTML timeline.")
    parser.add_argument("--log", required=True,
                        help="Path to combined UBLatency/UBST debug log.")
    parser.add_argument("--out", default="latency_trace.html",
                        help="Output HTML file path.")
    args = parser.parse_args()

    if not os.path.exists(args.log):
        print(f"ERROR: log file not found: {args.log}", file=sys.stderr)
        sys.exit(1)

    ublat, ubst = parse_log(args.log)
    print(f"Parsed {len(ublat)} UBLAT events, {len(ubst)} UBST events.",
          flush=True)

    if not ublat and not ubst:
        print("WARNING: no events found. Empty HTML will be generated.",
              file=sys.stderr)

    html = generate_html(ublat, ubst,
                         title=f"Latency Trace — {os.path.basename(args.log)}")

    with open(args.out, "w") as f:
        f.write(html)
    print(f"HTML written to {args.out} ({len(html)} bytes).", flush=True)


if __name__ == "__main__":
    main()
