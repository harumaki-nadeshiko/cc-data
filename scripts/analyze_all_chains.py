#!/usr/bin/env python3
"""Analyze all chain JSONs in build/run/chains/ and produce latency summary."""
import json, os, sys

def analyze_chains(path):
    with open(path) as f:
        data = json.load(f)
    chains = data.get("chains", {})
    meta = data.get("meta", {})
    total = len(chains)

    latencies = []
    for rid, chain in chains.items():
        events = chain.get("events", [])
        if len(events) >= 2:
            first_tick = events[0].get("tick", 0)
            last_tick = events[-1].get("tick", 0)
            lat_ns = (last_tick - first_tick) * 0.001
            latencies.append(lat_ns)
        elif len(events) == 1:
            latencies.append(0)

    latencies.sort()
    n = len(latencies)

    ge_500 = sum(1 for l in latencies if l >= 500)

    if n == 0:
        p50 = p99 = mean = 0
    else:
        p50 = latencies[min(int(n * 0.50), n-1)] if n > 0 else 0
        p99 = latencies[min(int(n * 0.99), n-1)] if n > 0 else latencies[-1]
        mean = sum(latencies) / n

    return {
        "file": os.path.basename(path),
        "total_chains": total,
        "total_events": meta.get("total_events", 0),
        "ge_500ns": ge_500,
        "ge_500_pct": (ge_500 / n * 100) if n > 0 else 0,
        "p50_ns": p50,
        "p99_ns": p99,
        "mean_ns": mean,
    }

def main():
    base = "/workspace/build/run/chains"
    if not os.path.isdir(base):
        print(f"ERROR: directory not found: {base}")
        sys.exit(1)
    files = sorted([f for f in os.listdir(base) if f.endswith(".json")])

    # Header
    header = f"{'File':<42} {'Chains':>7} {'Events':>8} {'>=500ns':>8} {'%>=500':>8} {'P50ns':>10} {'P99ns':>10} {'AvgNs':>10}"
    print(header)
    print("-" * len(header))

    results = {}
    for f in files:
        path = os.path.join(base, f)
        try:
            r = analyze_chains(path)
            results[f] = r
            print(f"{r['file']:<42} {r['total_chains']:>7} {r['total_events']:>8} "
                  f"{r['ge_500ns']:>8} {r['ge_500_pct']:>7.1f}% "
                  f"{r['p50_ns']:>9.1f} {r['p99_ns']:>9.1f} {r['mean_ns']:>9.1f}")
        except Exception as e:
            print(f"{f:<42} ERROR: {e}")

    # Comparison pairs
    print("\n=== Baseline vs Optimized Comparison ===")
    pairs = [
        ("tc29_baseline_chains.json", "tc29_optimized_chains.json", "TC29"),
        ("tc101_baseline_v4_chains.json", "tc101_optimized_v4_chains.json", "TC101"),
        ("tc97_baseline_v4_chains.json", "tc97_optimized_v4_chains.json", "TC97"),
    ]

    for bfile, ofile, label in pairs:
        if bfile not in results or ofile not in results:
            print(f"  {label}: MISSING files ({bfile}/{ofile})")
            continue

        br = results[bfile]
        oR = results[ofile]

        dP50 = (1 - oR["p50_ns"] / br["p50_ns"]) * 100 if br["p50_ns"] > 0 else 0
        dP99 = (1 - oR["p99_ns"] / br["p99_ns"]) * 100 if br["p99_ns"] > 0 else 0
        dMean = (1 - oR["mean_ns"] / br["mean_ns"]) * 100 if br["mean_ns"] > 0 else 0

        print(f"  {label}:")
        print(f"    Baseline: {br['total_chains']} chains, P50={br['p50_ns']:.1f}ns, P99={br['p99_ns']:.1f}ns, Mean={br['mean_ns']:.1f}ns")
        print(f"    Optimized: {oR['total_chains']} chains, P50={oR['p50_ns']:.1f}ns, P99={oR['p99_ns']:.1f}ns, Mean={oR['mean_ns']:.1f}ns")
        print(f"    Reduction: dP50={dP50:.1f}%, dP99={dP99:.1f}%, dMean={dMean:.1f}%")

if __name__ == "__main__":
    main()
