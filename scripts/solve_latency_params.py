#!/usr/bin/env python3
"""Latency parameter solver for UBCC CHI split-mode simulation.

Given a value of X (ZMQ linkLatency = syncInterval in ns), checks
feasibility against all constraints and solves for the remaining
free parameters.

Usage:
    python3 scripts/solve_latency_params.py --x-ns 20
    python3 scripts/solve_latency_params.py --x-ns 100
    python3 scripts/solve_latency_params.py --x-ns 20 --ubio-target 225

All equations and constraints are documented in:
    docs/measure/latency_tuning_constraints.md

Units:
    - X is input in ns (ZMQ linkLatency)
    - L_node, L_sock are output in ps (for gen_topo.py)
    - T_mem is output in cycles (for CHI_config.py)
    - T_ubio_dram is output in ns and ps (for ubio env var)
    - delta_noc is output in cycles and ns (for NoC config)
"""
import argparse
import sys

# ── Fixed constants ──────────────────────────────────────────────────
CYCLE_NS = 0.5          # 1 cycle @2GHz
T_CL = 13.75            # DDR4_2400 CAS latency (ns, fixed)
T_RCD = 13.75           # DDR4_2400 RAS→CAS delay (ns, fixed)
N_NOC_HOPS = 2          # typical CPU→HN-F NoC router hops

# ── Current cache parameters (cycles) ────────────────────────────────
L1D_TAG = 1
L1D_DATA = 2
L2_TAG = 2
L2_DATA = 6
L3_TAG = 4
L3_DATA = 10

# ── Current NoC parameters (cycles) ──────────────────────────────────
R_LAT = 1
RL_LAT = 1
NL_LAT = 1

# ── SN-F parameters (cycles) ─────────────────────────────────────────
R_RESP = 2
D_RESP = 1

# ── Ground truth targets (ns) ────────────────────────────────────────
T_L3 = 15.0             # core → Local Soc L3 hit
T_DRAM_LOCAL = 100.0    # core → Local DRAM (same socket)
T_DRAM_NUMA = 110.0     # core → NUMA DRAM (cross-socket same node)
T_EDGE = 415.0          # Inter-Node UBIO-UBIO
T_UBIO_LO = 210.0       # Intra-Node UBIO-UBIO lower bound
T_UBIO_HI = 240.0       # Intra-Node UBIO-UBIO upper bound
T_UBIO_MID = (T_UBIO_LO + T_UBIO_HI) / 2  # 225ns midpoint

# ── Derived constants ────────────────────────────────────────────────
CACHE_CHAIN_ONEWAY = (L1D_TAG + L1D_DATA + L2_TAG + L2_DATA + L3_TAG + L3_DATA) * CYCLE_NS  # 12.5ns
CACHE_CHAIN_ROUNDTRIP = 2 * CACHE_CHAIN_ONEWAY  # 25ns
NOC_ONEWAY = N_NOC_HOPS * (R_LAT + NL_LAT) * CYCLE_NS  # 2ns
DDR4_DEVICE = T_RCD + T_CL  # 27.5ns
SNF_RETURN = (R_RESP + D_RESP) * CYCLE_NS  # 1.5ns


def solve(x_ns: float, ubio_target: float = T_UBIO_MID) -> dict:
    """Solve all parameters given X (ZMQ latency in ns).

    Returns dict of all solved parameters, or raises ValueError if
    constraints are violated.
    """
    errors = []
    warnings = []

    # ── E6: 2*X + L_node = 415ns ─────────────────────────────────────
    l_node_ns = T_EDGE - 2 * x_ns
    l_node_ps = int(l_node_ns * 1000)

    # ── E7: 2*X + L_sock = ubio_target ns ────────────────────────────
    l_sock_ns = ubio_target - 2 * x_ns
    l_sock_ps = int(l_sock_ns * 1000)

    # ── E8: D4 merged = 2*X + L_node + L_sock ────────────────────────
    l_node_sock_ns = l_node_ns + l_sock_ns
    l_node_sock_ps = int(l_node_sock_ns * 1000)

    # ── E1: L3 hit = 15ns ────────────────────────────────────────────
    l3_hit = CACHE_CHAIN_ONEWAY + NOC_ONEWAY
    l3_hit_ok = abs(l3_hit - T_L3) < 1.0  # within 1ns tolerance

    # ── E2: local DRAM = 100ns → solve T_mem ─────────────────────────
    # 100 = cache_roundtrip + T_mem*0.5 + DDR4 + SNF_return + NoC
    t_mem_ns = T_DRAM_LOCAL - CACHE_CHAIN_ROUNDTRIP - DDR4_DEVICE - SNF_RETURN - NOC_ONEWAY
    t_mem_cy = int(round(t_mem_ns / CYCLE_NS))

    # ── E3/E5: Δ_noc = 10ns ──────────────────────────────────────────
    delta_noc_ns = T_DRAM_NUMA - T_DRAM_LOCAL  # 10ns
    delta_noc_cy = int(round(delta_noc_ns / CYCLE_NS))  # 20 cy

    # ── E4: DSM same-socket = 100ns → solve T_ubio_dram ──────────────
    # 100 = cache_roundtrip + NoC + 2*X + T_ubio_dram
    t_ubio_dram_ns = T_DRAM_LOCAL - CACHE_CHAIN_ROUNDTRIP - NOC_ONEWAY - 2 * x_ns
    t_ubio_dram_ps = int(round(t_ubio_dram_ns * 1000))

    # ── Constraint checks ────────────────────────────────────────────
    if x_ns < 0:
        errors.append(f"C2 violated: X={x_ns}ns < 0")
    if l_node_ns < 0:
        errors.append(f"C3 violated: L_node={l_node_ns}ns < 0 (need X <= {(T_EDGE/2):.1f}ns)")
    if l_sock_ns < 0:
        errors.append(f"C4 violated: L_sock={l_sock_ns}ns < 0 (need X <= {(ubio_target/2):.1f}ns)")
    if t_mem_cy < 0:
        errors.append(f"C9 violated: T_mem={t_mem_cy}cy < 0")
    if t_ubio_dram_ns < 0:
        errors.append(f"C12 violated: T_ubio_dram={t_ubio_dram_ns}ns < 0 (need X <= {((T_DRAM_LOCAL - CACHE_CHAIN_ROUNDTRIP - NOC_ONEWAY)/2):.1f}ns)")
    if not l3_hit_ok:
        warnings.append(f"E1: L3 hit={l3_hit:.1f}ns != {T_L3}ns (cache params may need adjustment)")

    # UBIO-UBIO range check
    ubio_node = 2 * x_ns + l_node_ns
    ubio_sock = 2 * x_ns + l_sock_ns
    if not (T_UBIO_LO <= ubio_sock <= T_UBIO_HI):
        warnings.append(f"E7: Intra-Node UBIO-UBIO={ubio_sock:.1f}ns outside [{T_UBIO_LO}, {T_UBIO_HI}]")

    return {
        "x_ns": x_ns,
        "x_ps": int(x_ns * 1000),
        "l_node_ns": l_node_ns,
        "l_node_ps": l_node_ps,
        "l_sock_ns": l_sock_ns,
        "l_sock_ps": l_sock_ps,
        "l_node_sock_ns": l_node_sock_ns,
        "l_node_sock_ps": l_node_sock_ps,
        "l3_hit_ns": l3_hit,
        "l3_hit_ok": l3_hit_ok,
        "t_mem_cy": t_mem_cy,
        "t_mem_ns": t_mem_cy * CYCLE_NS,
        "delta_noc_ns": delta_noc_ns,
        "delta_noc_cy": delta_noc_cy,
        "t_ubio_dram_ns": t_ubio_dram_ns,
        "t_ubio_dram_ps": t_ubio_dram_ps,
        "ubio_node_ns": ubio_node,
        "ubio_sock_ns": ubio_sock,
        "errors": errors,
        "warnings": warnings,
        "all_ok": len(errors) == 0,
    }


def print_result(r: dict):
    ok = "✅" if r["all_ok"] else "❌"
    print(f"\n{'='*60}")
    print(f"  X = {r['x_ns']} ns  {ok}")
    print(f"{'='*60}\n")

    if r["errors"]:
        print("ERRORS (constraints violated):")
        for e in r["errors"]:
            print(f"  ❌ {e}")
        print()

    if r["warnings"]:
        print("WARNINGS:")
        for w in r["warnings"]:
            print(f"  ⚠️  {w}")
        print()

    print("─── nsim link latencies (gen_topo.py) ───")
    print(f"  --cross-node-latency      = {r['l_node_ps']:>8} ps  ({r['l_node_ns']:.1f} ns)")
    print(f"  --cross-socket-latency    = {r['l_sock_ps']:>8} ps  ({r['l_sock_ns']:.1f} ns)")
    print(f"  cross-node+socket (D4)    = {r['l_node_sock_ps']:>8} ps  ({r['l_node_sock_ns']:.1f} ns)")
    print()

    print("─── ZMQ (framework/Port.hh) ───")
    print(f"  kDefaultLinkLatency       = {r['x_ps']:>8} ps  ({r['x_ns']:.1f} ns)")
    print(f"  kDefaultSyncInterval      = {r['x_ps']:>8} ps  ({r['x_ns']:.1f} ns)")
    print()

    print("─── gem5 DRAM (CHI_config.py) ───")
    print(f"  to_memory_controller_latency = {r['t_mem_cy']:>4} cy  ({r['t_mem_ns']:.1f} ns)")
    print()

    print("─── cross-socket NoC delay (new param) ───")
    print(f"  delta_noc                  = {r['delta_noc_cy']:>4} cy  ({r['delta_noc_ns']:.1f} ns)")
    print()

    print("─── ubio backstore delay (new, env UBIO_DRAM_DELAY_PS) ───")
    print(f"  T_ubio_dram                = {r['t_ubio_dram_ps']:>8} ps  ({r['t_ubio_dram_ns']:.1f} ns)")
    print()

    print("─── L3 hit check (E1) ───")
    print(f"  L3 hit latency             = {r['l3_hit_ns']:.1f} ns  (target {T_L3} ns)  {'✅' if r['l3_hit_ok'] else '⚠️'}")
    print()

    print("─── UBIO-UBIO end-to-end checks ───")
    print(f"  Inter-Node  (2X + L_node)  = {r['ubio_node_ns']:.1f} ns  (target {T_EDGE} ns)  {'✅' if abs(r['ubio_node_ns']-T_EDGE)<0.1 else '❌'}")
    print(f"  Intra-Node  (2X + L_sock)  = {r['ubio_sock_ns']:.1f} ns  (target {T_UBIO_LO}~{T_UBIO_HI} ns)  {'✅' if T_UBIO_LO<=r['ubio_sock_ns']<=T_UBIO_HI else '⚠️'}")
    print()

    print("─── DRAM end-to-end checks ───")
    local_dram = CACHE_CHAIN_ROUNDTRIP + r['t_mem_ns'] + DDR4_DEVICE + SNF_RETURN + NOC_ONEWAY
    numa_dram = local_dram + r['delta_noc_ns']
    dsm_local = CACHE_CHAIN_ROUNDTRIP + NOC_ONEWAY + 2*r['x_ns'] + r['t_ubio_dram_ns']
    dsm_numa = dsm_local + r['delta_noc_ns']
    print(f"  Local DRAM  (E2)           = {local_dram:.1f} ns  (target {T_DRAM_LOCAL} ns)  {'✅' if abs(local_dram-T_DRAM_LOCAL)<0.5 else '❌'}")
    print(f"  NUMA DRAM   (E3)           = {numa_dram:.1f} ns  (target {T_DRAM_NUMA} ns)  {'✅' if abs(numa_dram-T_DRAM_NUMA)<0.5 else '❌'}")
    print(f"  DSM local   (E4)           = {dsm_local:.1f} ns  (target {T_DRAM_LOCAL} ns)  {'✅' if abs(dsm_local-T_DRAM_LOCAL)<0.5 else '❌'}")
    print(f"  DSM NUMA    (E5)           = {dsm_numa:.1f} ns  (target {T_DRAM_NUMA} ns)  {'✅' if abs(dsm_numa-T_DRAM_NUMA)<0.5 else '❌'}")

    print(f"\n{'='*60}")
    if r["all_ok"]:
        print("  ALL CONSTRAINTS SATISFIED ✅")
    else:
        print("  CONSTRAINTS VIOLATED ❌ — see ERRORS above")
    print(f"{'='*60}\n")


def main():
    ap = argparse.ArgumentParser(description="Solve latency parameters given X (ZMQ latency)")
    ap.add_argument("--x-ns", type=float, required=True,
                    help="ZMQ linkLatency in ns (e.g. 20, 50, 100)")
    ap.add_argument("--ubio-target", type=float, default=T_UBIO_MID,
                    help=f"Intra-Node UBIO-UBIO target in ns (default {T_UBIO_MID}, range {T_UBIO_LO}~{T_UBIO_HI})")
    args = ap.parse_args()

    if not (T_UBIO_LO <= args.ubio_target <= T_UBIO_HI):
        print(f"ERROR: --ubio-target {args.ubio_target} outside [{T_UBIO_LO}, {T_UBIO_HI}]")
        sys.exit(1)

    r = solve(args.x_ns, args.ubio_target)
    print_result(r)
    sys.exit(0 if r["all_ok"] else 1)


if __name__ == "__main__":
    main()
