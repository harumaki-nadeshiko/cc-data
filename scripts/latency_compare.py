#!/usr/bin/env python3
"""Compare two chain JSONs (baseline vs optimized) and produce a latency
comparison table.

Usage:
    # From two trace2chain.py output JSONs:
    python3 scripts/latency_compare.py baseline_chains.json optimized_chains.json

    # Output to CSV file:
    python3 scripts/latency_compare.py baseline.json optimized.json --csv out.csv

    # Specify tick-to-ns conversion factor (default: 1 ps per tick, i.e. 1e-3 ns)
    # gem5 uses ps as tick unit; 1000 ps = 1 ns
    python3 scripts/latency_compare.py baseline.json optimized.json --tick-ns-factor 0.001

Output:
    A table (text) and optional CSV showing per-category latency stats.
"""

import sys, os, json, csv, math


def load_chains(path):
    with open(path) as f:
        data = json.load(f)
    return data.get("chains", {})


def category_from_chain(chain):
    """Determine category from chain data.

    Priority:
    1. Explicit 'category' field (from Phase 1.4)
    2. First token of first gem5 SEND event's 'extra' field
    3. primary_type field
    """
    if "category" in chain:
        return chain["category"]

    events = chain.get("events", [])
    for ev in events:
        if ev.get("comp") == "gem5" and ev.get("event") == "SEND":
            extra = ev.get("extra", "")
            # First token before '|'
            primary = extra.split("|")[0] if extra else "?"
            # Sub-categorize ReadReq by write= field
            if primary == "ReadReq" and "write=1" in extra:
                return "ReadReq(write=1)"
            if primary == "ReadReq" and "write=0" in extra:
                return "ReadReq(write=0)"
            return primary

    # Fallback to primary_type
    return chain.get("primary_type", "?")


def compute_latencies(chains, tick_ns_factor=0.001):
    """Compute per-category latency stats.

    Returns dict: {category: [latencies_in_ns, ...]}
    """
    cat_lats = {}
    for cid, chain in chains.items():
        cat = category_from_chain(chain)
        if cat not in cat_lats:
            cat_lats[cat] = []
        # Prefer issue-to-first-response latency.  Fall back for legacy traces.
        dur_ps = chain.get("e2e_latency_ps", chain.get("duration_ps", 0))
        dur_ns = dur_ps * tick_ns_factor
        cat_lats[cat].append(dur_ns)
    return cat_lats


def percentile(sorted_vals, p):
    """Return p-th percentile (0-100) from sorted list."""
    if not sorted_vals:
        return 0.0
    k = (p / 100.0) * (len(sorted_vals) - 1)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[int(f)] * (c - k) + sorted_vals[int(c)] * (k - f)


def build_table(baseline_cats, opt_cats):
    """Build comparison table.

    Returns list of dicts, one per category.
    """
    all_cats = sorted(set(list(baseline_cats.keys()) + list(opt_cats.keys())))
    rows = []
    for cat in all_cats:
        bl = sorted(baseline_cats.get(cat, []))
        op = sorted(opt_cats.get(cat, []))
        row = {
            "category": cat,
            "baseline_count": len(bl),
            "optimized_count": len(op),
            "baseline_p50": percentile(bl, 50),
            "baseline_p99": percentile(bl, 99),
            "optimized_p50": percentile(op, 50),
            "optimized_p99": percentile(op, 99),
        }
        # Reduction %
        if row["baseline_p50"] > 0:
            row["p50_reduction_pct"] = (
                (row["baseline_p50"] - row["optimized_p50"])
                / row["baseline_p50"] * 100.0
            )
        else:
            row["p50_reduction_pct"] = 0.0
        if row["baseline_p99"] > 0:
            row["p99_reduction_pct"] = (
                (row["baseline_p99"] - row["optimized_p99"])
                / row["baseline_p99"] * 100.0
            )
        else:
            row["p99_reduction_pct"] = 0.0
        rows.append(row)
    return rows


def print_table(rows):
    """Print a human-readable table."""
    header = (
        f"{'Category':<20s} {'CntB':>6s} {'CntO':>6s} "
        f"{'B_P50ns':>10s} {'B_P99ns':>10s} "
        f"{'O_P50ns':>10s} {'O_P99ns':>10s} "
        f"{'dP50%':>8s} {'dP99%':>8s}"
    )
    sep = "-" * len(header)
    print(sep)
    print(header)
    print(sep)
    for r in rows:
        print(
            f"{r['category']:<20s} {r['baseline_count']:>6d} {r['optimized_count']:>6d} "
            f"{r['baseline_p50']:>10.2f} {r['baseline_p99']:>10.2f} "
            f"{r['optimized_p50']:>10.2f} {r['optimized_p99']:>10.2f} "
            f"{r['p50_reduction_pct']:>7.1f}% {'+' + str(r['p99_reduction_pct']):>7s}%"
            if r['p99_reduction_pct'] >= 0
            else f"{r['p99_reduction_pct']:>7.1f}%"
        )
    print(sep)

    # Total summary
    total_bl_p50 = sum(r["baseline_p50"] * r["baseline_count"] for r in rows)
    total_bl_cnt = sum(r["baseline_count"] for r in rows)
    total_op_p50 = sum(r["optimized_p50"] * r["optimized_count"] for r in rows)
    total_op_cnt = sum(r["optimized_count"] for r in rows)

    avg_bl_p50 = total_bl_p50 / total_bl_cnt if total_bl_cnt > 0 else 0
    avg_op_p50 = total_op_p50 / total_op_cnt if total_op_cnt > 0 else 0
    reduction = ((avg_bl_p50 - avg_op_p50) / avg_bl_p50 * 100.0) if avg_bl_p50 > 0 else 0

    print(f"  Weighted avg P50: baseline={avg_bl_p50:.2f}ns  "
          f"optimized={avg_op_p50:.2f}ns  reduction={reduction:.1f}%")


def write_csv(rows, path):
    """Write comparison table to CSV."""
    fieldnames = [
        "category",
        "baseline_count", "optimized_count",
        "baseline_p50", "baseline_p99",
        "optimized_p50", "optimized_p99",
        "p50_reduction_pct", "p99_reduction_pct",
    ]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"[latency_compare] CSV written to {path}")


def main():
    args = sys.argv[1:]

    tick_ns_factor = 0.001  # ps -> ns (gem5 ticks are ps)
    csv_out = None

    # Parse optional flags
    positional = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--tick-ns-factor":
            i += 1
            tick_ns_factor = float(args[i])
        elif a == "--csv":
            i += 1
            csv_out = args[i]
        elif a.startswith("--tick-ns-factor="):
            tick_ns_factor = float(a.split("=", 1)[1])
        elif a == "--help" or a == "-h":
            print(__doc__)
            return 0
        else:
            positional.append(a)
        i += 1

    if len(positional) < 2:
        print("ERROR: need two chain JSON paths (baseline optimized)", file=sys.stderr)
        print(__doc__)
        return 1

    baseline_path = positional[0]
    opt_path = positional[1]

    print(f"[latency_compare] baseline: {baseline_path}")
    print(f"[latency_compare] optimized: {opt_path}")
    print(f"[latency_compare] tick->ns factor: {tick_ns_factor}")

    bl_chains = load_chains(baseline_path)
    op_chains = load_chains(opt_path)

    print(f"[latency_compare] baseline chains: {len(bl_chains)}")
    print(f"[latency_compare] optimized chains: {len(op_chains)}")

    bl_cats = compute_latencies(bl_chains, tick_ns_factor)
    op_cats = compute_latencies(op_chains, tick_ns_factor)

    rows = build_table(bl_cats, op_cats)
    print_table(rows)

    if csv_out:
        write_csv(rows, csv_out)

    return 0


if __name__ == "__main__":
    sys.exit(main())
