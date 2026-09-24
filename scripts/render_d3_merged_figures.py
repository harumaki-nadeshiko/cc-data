"""Render D3 Metric-1 / Metric-2 extension figures from the 2026-09-23 rerun,
falling back to the previous extension values for coordinates not yet measured.
Bars that contain any estimated coordinate are hatched."""
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import generate_delivery_figures as api          # noqa: E402
import publication_extension_charts as P          # noqa: E402

TOPO5 = ["3n1s", "3n2s", "8n1s", "8n2s", "16n1s"]
TOPO3 = ["3n1s", "8n1s", "16n1s"]
EXTRACT = ROOT / "docs/design/m1m2_rerun_20260923_extract.json"


def build_coords():
    hist = json.load(open(ROOT / "docs/design/performance_extension_data.json"))
    hc = {(r["tc"], r["pressure_pct"], r["topology"]): r for r in P.coordinates(hist)}
    new = json.load(open(EXTRACT))
    nd = {(r["tc"], r["pct"], r["topo"], r["arm"]): r for r in new if "error" not in r}
    svc = json.load(open(ROOT / "docs/design/m1m2_rerun_20260923_service.json"))
    sd = {(r["tc"], r["pct"], r["topo"], r["arm"]): r for r in svc if "error" not in r}

    def mn(r):
        return r["outer_sum_ps"] / r["outer_count"] / 1000 if r.get("outer_count") else None

    coords = []
    for tc in range(142, 148):
        for pct in (175, 200):
            for t in TOPO5:
                n = nd.get((tc, pct, t, "naive"))
                s = nd.get((tc, pct, t, "spill-noopt"))
                i = nd.get((tc, pct, t, "IdealDir"))
                h = hc[(tc, pct, t)]
                est = set()
                if n and s and n.get("resident_capacity") and s.get("h64_exact_live_max") is not None:
                    cap = max(s["resident_capacity"], s["h64_exact_live_max"]) / n["resident_capacity"]
                else:
                    cap = h["capacity_ratio"]; est.add("cap")
                if s and i and mn(s) is not None and mn(i) is not None:
                    delta = mn(s) - mn(i)
                else:
                    delta = h["outer_delta_cycles_2ghz"] / 2; est.add("delta")
                if n and s and n.get("e2e_ns_per_op") and s.get("e2e_ns_per_op"):
                    red = 100 * (1 - s["e2e_ns_per_op"] / n["e2e_ns_per_op"])
                else:
                    red = h["reduction_pct"]; est.add("red")
                sn = sd.get((tc, pct, t, "naive")); ss = sd.get((tc, pct, t, "spill-noopt"))
                if sn and ss and sn.get("svc") and ss.get("svc"):
                    svc_red = 100 * (1 - ss["svc"] / sn["svc"])
                else:
                    svc_red = None
                coords.append(dict(tc=tc, pct=pct, topo=t, cap=cap, delta=delta, red=red,
                                   svc=svc_red, est=est))
    # Estimate any missing service reduction from the other topologies at the same (tc, pressure).
    for c in coords:
        if c["svc"] is None:
            peers = [o["svc"] for o in coords
                     if o["tc"] == c["tc"] and o["pct"] == c["pct"] and o["svc"] is not None]
            c["svc"] = sum(peers) / len(peers) if peers else 0.0
            c["est"].add("svc")
    return coords


def render():
    api.configure_plot()
    plt = api.plt
    coords = build_coords()

    def per_tc(field, topos):
        per, est = {}, {}
        for tc in range(142, 148):
            sel = [c for c in coords if c["tc"] == tc and c["topo"] in topos]
            vals = [c[field] for c in sel]
            per[tc] = P.geomean(vals) if field == "cap" else statistics.mean(vals)
            est[tc] = any(field in c["est"] for c in sel)
        tot = P.geomean(per.values()) if field == "cap" else statistics.mean(per.values())
        return per, tot, est

    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.0))
    for ax, field, ylabel in ((axes[0], "cap", "Capacity ratio"),
                              (axes[1], "delta", "Outer delta (ns)")):
        per, tot, est = per_tc(field, TOPO5)
        labels = [f"TC{tc}" for tc in range(142, 148)] + ["Mean"]
        vals = [per[tc] for tc in range(142, 148)] + [tot]
        flags = [est[tc] for tc in range(142, 148)] + [any(est.values())]
        colors = [api.BLUE] * 6 + [api.ORANGE]
        bars = ax.bar(range(7), vals, color=colors, width=.68)
        for b, hatch in zip(bars, flags):
            if hatch:
                b.set_hatch("//"); b.set_edgecolor("#404040"); b.set_linewidth(1.0)
        for ref in ([1.5] if field == "cap" else [0.0, 25.0]):
            ax.axhline(ref, color=api.ORANGE, ls="--", lw=.9)
        ax.set_xticks(range(7), labels, fontsize=10)
        ax.set_ylabel(ylabel, fontsize=11)
        ax.grid(axis="y", alpha=.18); ax.set_axisbelow(True)
        fmt = "{:.2f}" if field == "cap" else "{:.1f}"
        lo, hi = ax.get_ylim(); ax.set_ylim(lo, hi + (hi - lo) * .16)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v, fmt.format(v), ha="center", va="bottom", fontsize=9)
    fig.suptitle("Metric 1 extension: per-TC merge (last bar = Mean)", fontsize=12, color=api.NAVY)
    fig.tight_layout()
    api.save_chart(fig, "ubcc-metric1-extension-matrix")

    palette = [api.BLUE, api.TEAL, "#9986A8"]
    fig, axes = plt.subplots(2, 1, figsize=(7, 5.1))
    for pi, pressure in enumerate((175, 200)):
        ax = axes[pi]
        per = {}
        est = {}
        for tc in range(142, 148):
            sel = [c for c in coords if c["tc"] == tc and c["pct"] == pressure and c["topo"] in TOPO3]
            per[tc] = statistics.mean(c["svc"] for c in sel)
            est[tc] = any("svc" in c["est"] for c in sel)
        total = statistics.mean(per.values())
        labels = [f"TC{tc}" for tc in range(142, 148)] + ["Mean"]
        vals = [per[tc] for tc in range(142, 148)] + [total]
        flags = [est[tc] for tc in range(142, 148)] + [any(est.values())]
        bars = ax.bar(range(7), vals, color=[api.BLUE] * 6 + [api.ORANGE], width=.68)
        for b, hatch in zip(bars, flags):
            if hatch:
                b.set_hatch("//"); b.set_edgecolor("#404040"); b.set_linewidth(1.0)
        for ref in (0, 10):
            ax.axhline(ref, color=api.ORANGE, ls="--", lw=.9,
                       label="10% reduction" if ref == 10 else "_nolegend_")
        ax.set_xticks(range(7), labels, fontsize=10)
        ax.set_ylabel("Service reduction (%)", fontsize=11)
        ax.set_title(f"P{pressure}", fontsize=12)
        ax.tick_params(axis="y", labelsize=11)
        ax.grid(axis="y", alpha=.18); ax.set_axisbelow(True)
        ax.legend(loc="upper left", fontsize=10, frameon=False)
        lo, hi = ax.get_ylim(); ax.set_ylim(lo, hi + (hi - lo) * .16)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.1f}", ha="center", va="bottom", fontsize=9)
    fig.suptitle("Metric 2 extension: service latency, per-TC merge (last bar = Mean)", fontsize=12, color=api.NAVY)
    fig.tight_layout(h_pad=1.5)
    api.save_chart(fig, "ubcc-tc142-147-applications")

    pub = json.load(open(ROOT / "docs/design/performance_publication_data.json"))
    cases = [c for c in pub["metric2"]["cases"] if c["applicable"]]
    values = [c["naive_mean_ns"] / c["optimized_mean_ns"] for c in cases]
    fig, ax = plt.subplots(figsize=(7, 3.7))
    ax.bar([c["case"] for c in cases], values, width=.5, color=api.BLUE)
    ax.set_yscale("log")
    ax.axhline(1, color=api.GRAY, lw=.8)
    ax.axhline(1 / .9, color=api.TEAL, ls="--", label="10% reduction")
    ax.set_ylabel("naive / optimized", fontsize=11)
    ax.tick_params(labelsize=11)
    ax.legend(fontsize=11, frameon=False)
    ax.grid(axis="y", alpha=.18); ax.set_axisbelow(True)
    fig.tight_layout()
    api.save_chart(fig, "ubcc-metric2-reductions")

    per_c, tot_c, est_c = per_tc("cap", TOPO5)
    per_d, tot_d, est_d = per_tc("delta", TOPO5)
    per_r, tot_r, est_r = per_tc("svc", TOPO3)
    summary = {"cap": {str(k): round(v, 3) for k, v in per_c.items()}, "cap_mean": round(tot_c, 3),
               "cap_est": est_c, "delta": {str(k): round(v, 3) for k, v in per_d.items()},
               "delta_mean": round(tot_d, 3), "delta_est": est_d,
               "svc": {str(k): round(v, 3) for k, v in per_r.items()}, "svc_mean": round(tot_r, 3),
               "svc_est": est_r}
    (ROOT / "docs/design/d3_142_147_merged.json").write_text(
        json.dumps({"summary": summary, "coords": [{**c, "est": sorted(c["est"])} for c in coords]},
                   ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    render()
