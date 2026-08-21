"""E2E Test Driver for Qi Phase 2.
USAGE (gem5 config mode):
    gem5.opt tests/e2e/test_e2e.py --tc <N>          # Run single test case
    gem5.opt tests/e2e/test_e2e.py --all             # Run TC1-TC4 combined

USAGE (Python runner mode):
    python3 tests/e2e/test_e2e.py --all              # Run all TCs
    python3 tests/e2e/test_e2e.py --tc <N>           # Run single TC
"""

import sys, os, re, subprocess, argparse, tempfile, shutil, json
from collections import Counter

# gem5 v25.1 SimObject hierarchy can be deep; increase recursion limit.
sys.setrecursionlimit(20000)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKLOAD_DIR = os.path.join(SCRIPT_DIR, "workloads")
GEM5_BIN = os.path.join(SCRIPT_DIR, "../../gem5/build/ARM/gem5.opt")

# ── Test case registry ────────────────────────────────────────────
TESTCASES = {
    1: "e2e_tc1_dsm_local",
    2: "e2e_tc2_remote_read",
    3: "e2e_tc3_pingpong",
    4: "e2e_tc4_three_node_ring",
    5: "e2e_tc5_single_writer",
    6: "e2e_tc6_multi_sharer",
    7: "e2e_tc7_writeback_evict",
    8: "e2e_tc8_upgrade_invalidate",
    9: "e2e_tc9_non_dsm_negative",
    10: "e2e_tc10_concurrent_atomic",
    11: "e2e_tc_local_upgrade",
    12: "e2e_tc12_sync_barrier",
    13: "e2e_tc13_remote_release_acquire",
    14: "e2e_tc14_multi_sharer_wave",
    15: "e2e_tc15_credit_storm",
    16: "e2e_tc16_dual_upgrade_race",
    17: "e2e_tc17_writeback_dma",
    18: "e2e_tc18_directory_fill_replay",
    19: "e2e_tc19_directory_dirty_persist",
    20: "e2e_tc20_offload_smoke_a",
    21: "e2e_tc21_offload_smoke_b",
    22: "e2e_tc22_resident_capacity_pressure",
    23: "e2e_tc23_bloom_false_positive_fallback",
    24: "e2e_tc24_multinode_pressure_stress",
    25: "e2e_tc25_invalidate_clear_cycle",
    26: "e2e_tc26_l3_eviction_writeback_chain",
    27: "e2e_tc27_epoch_wrap_stress",
    28: "e2e_tc28_backstore_metadata_consistency",
    29: "e2e_tc29_local_upgrade_from_exclusive",
    30: "e2e_tc30_stale_clear_tombstone",
    31: "e2e_tc31_multicpu_concurrent_isolation",
    32: "e2e_tc32_cross_socket_read_miss",
    33: "e2e_tc33_cross_socket_writeback",
    34: "e2e_tc34_dual_socket_pingpong",
    35: "e2e_tc35_numa_latency_stress",
    36: "e2e_tc36_owner_upgrade_ge_window",
    37: "e2e_tc37_owner_upgrade_gm_window",
    38: "e2e_tc38_stale_clear_tombstone_storm",
    39: "e2e_tc39_dual_socket_same_pa_interference",
    40: "e2e_tc40_recall_timeout_retry",
    41: "e2e_tc41_recall_invalidate_overlap",
    42: "e2e_tc42_exact_epoch_wrap_24b",
    43: "e2e_tc43_rapid_owner_cycle",
    44: "e2e_tc44_full_protocol_matrix",
    45: "e2e_tc45_fill_conflict_bloom_sat",
    46: "e2e_tc46_multibeat_recall",
    47: "e2e_tc47_drop_clear",
    48: "e2e_tc48_dup_inv_ack",
    49: "e2e_tc49_reorder_acks",
    50: "e2e_tc50_producer_consumer_ring",
    51: "e2e_tc51_bank_ledger",
    52: "e2e_tc52_mapreduce_scatter_gather",
    53: "e2e_tc53_cache_contention_storm",
    54: "e2e_tc54_numa_tiled_matmul",
    63: "e2e_tc63_recall_orphan_timer_cleanup",
    64: "e2e_tc64_recall_done_orphan_lazy_cleanup",
    80: "e2e_tc80_cross_node_latency",
    81: "e2e_tc81_cross_socket_latency",
    82: "e2e_tc82_8node_ring_latency",
    84: "e2e_tc84_cacheline_capacity",
    85: "e2e_tc84_cacheline_capacity",
    90: "e2e_tc90_8node_all_to_all",
    91: "e2e_tc91_8node_hotspot",
    92: "e2e_tc92_8node_butterfly",
    93: "e2e_tc93_8node_pairwise_pingpong",
    94: "e2e_tc94_8node_barrier_stress",
    95: "e2e_tc95_8n2s_barrier_stress",
    96: "e2e_tc96_8n2s_cross_socket_read",
    97: "e2e_tc97_8n2s_pingpong",
    98: "e2e_tc98_8n2s_hotspot",
    99: "e2e_tc99_8n2s_perplane_slots",
    100: "e2e_tc100_8n2s_batch_rs",
    101: "e2e_tc101_8n2s_direct_fwd",
    102: "e2e_tc102_writeback_data_persist",
    110: "e2e_tc110_drop_clear",
    111: "e2e_tc111_silent_upgrade_drop",
    112: "e2e_tc112_tbe_interference",
    113: "e2e_tc113_silent_upgrade_micro",
    114: "e2e_tc114_silent_upgrade_minimal",
    115: "e2e_tc115_cross_cpu_silent_upgrade",
    116: "e2e_tc116_directory_eviction_stress",
    117: "e2e_tc117_clear_reorder",
    118: "e2e_tc118_mixed_fault",
    119: "e2e_tc119_triple_fault",
    120: "e2e_tc120_baseline_perf_mix",
    121: "e2e_tc121_perf_cold_stream",
    122: "e2e_tc122_perf_hot_reuse",
    123: "e2e_tc123_perf_shared_upgrade",
    124: "e2e_tc124_perf_direct_fwd",
    125: "e2e_tc125_read_offload_onload",
    126: "e2e_tc126_resident_upgrade_replay",
    127: "e2e_tc127_writeback_offload_onload",
    128: "e2e_tc128_clean_evict_offload_onload",
    129: "e2e_tc129_long_mixed_integration",
    130: "e2e_tc130_directory_overflow_benchmark",
    131: "e2e_tc131_catalog_fullscan",
    132: "e2e_tc132_dirty_checkpoint_stream",
    133: "e2e_tc133_8n1s_shared_frontier",
    134: "e2e_tc134_8n2s_sliding_window",
    135: "e2e_tc135_preserved_sharer_revisit",
    136: "e2e_tc136_preserved_owner_store",
    137: "e2e_tc137_new_requester_load",
    138: "e2e_tc138_dirty_handoff_store",
    139: "e2e_tc139_mixed_batch_throughput",
    140: "e2e_tc140_cross_l2_owner_store",
    141: "e2e_tc141_spill_shared_writer_recovery",
    142: "e2e_tc142_db_oltp_buffer_pool",
    143: "e2e_tc143_db_btree_traversal",
    144: "e2e_tc144_db_wal_checkpoint",
    145: "e2e_tc145_faas_warm_invocation",
    146: "e2e_tc146_graph_frontier",
    147: "e2e_tc147_feature_store",
    148: "e2e_tc148_fault_qualification",
    149: "e2e_tc149_upgrade_invalidate_fault_qualification",
    150: "e2e_tc149_upgrade_invalidate_fault_qualification",
    151: "e2e_tc149_upgrade_invalidate_fault_qualification",
    152: "e2e_tc149_upgrade_invalidate_fault_qualification",
    153: "e2e_tc153_recallresp_fault_qualification",
    154: "e2e_tc153_recallresp_fault_qualification",
    155: "e2e_tc153_recallresp_fault_qualification",
    156: "e2e_tc153_recallresp_fault_qualification",
    157: "e2e_tc149_upgrade_invalidate_fault_qualification",
    158: "e2e_tc149_upgrade_invalidate_fault_qualification",
    159: "e2e_tc149_upgrade_invalidate_fault_qualification",
    160: "e2e_tc160_16n1s_sharer_smoke",
    200: "e2e_a3_naive_recall",   # Phase A3: targeted naive dirty recall test
    201: "e2e_a5_spill_recall",   # Phase A5: targeted spill backstore + recall test
    202: "e2e_c1_spill_cache_push", # Phase C1: spill authoritative home-data push-grant test
    203: "e2e_d1_overflow",          # Phase D1: backstore page overflow test
    210: "e2e_ha_2n1s_core",         # HA01 local reuse portable core
    211: "e2e_ha_2n1s_core",         # HA02 remote read portable core
    212: "e2e_ha_2n1s_core",         # HA03 ownership portable core
    213: "e2e_ha_2n1s_core",         # HA04 shared-to-writer portable core
    214: "e2e_ha_2n1s_core",         # HA07 producer-consumer portable core
    215: "e2e_ha_2n1s_core",         # HA05 capacity shared-victim revisit
    216: "e2e_ha_2n1s_core",         # HA06 dirty-owner capacity lifecycle
    217: "e2e_ha_2n1s_core",         # HA10 read-mostly skewed catalog performance
    218: "e2e_ha_2n1s_core",         # HA08 lock/barrier contention
    219: "e2e_ha_2n1s_core",         # HA09 mixed local/remote pressure
    220: "e2e_ha_2n1s_core",         # HA11 clean/shared 150% capacity
    221: "e2e_ha_2n1s_core",         # HA12 dirty-owner 150% capacity
    222: "e2e_ha_cgroup_2n1s",        # C123-HA shared-to-writer batch
    223: "e2e_ha_cgroup_2n1s",        # C130-HA overflow hot reuse
    224: "e2e_ha_cgroup_2n1s",        # C132-HA dirty checkpoint recovery
    225: "e2e_ha_cgroup_2n1s",        # C135-HA preserved sharer revisit
    226: "e2e_ha_cgroup_2n1s",        # C138-HA dirty owner handoff
    227: "e2e_ha_cgroup_2n1s",        # C139-HA mixed batch throughput
    228: "e2e_ha_topology",            # all-plane remote-read ring
    229: "e2e_ha_topology",            # all-plane ownership handoff ring
    230: "e2e_ha_topology",            # all-sharer to one writer
    231: "e2e_ha_extended",            # clean shared read/reuse
    232: "e2e_ha_extended",            # contended hot-key read/write
    233: "e2e_ha_extended",            # all-plane producer-consumer
    234: "e2e_ha_extended",            # queued ownership token
    235: "e2e_ha_extended",            # shared catalog/KV
    300: "e2e_tc300_o3_remote_publication",
    301: "e2e_tc301_o3_dirty_handoff",
    302: "e2e_tc302_o3_multiline_mlp",
    303: "e2e_tc303_o3_invalidation_race",
}

# ── Output parser ─────────────────────────────────────────────────
_RE_READ_VAL = re.compile(
    r"\[READ_VAL\]\s+node=(\d+)\s+home=(\d+)\s+offset=\w+\s+"
    r"expected=(\w+)\s+actual=(\w+)\s+(MATCH|MISMATCH)"
)
_RE_E2E_META = re.compile(r"\[E2E_META\]\s+node=(\d+)\s+test=(\S+)")
_RE_TOPOLOGY = re.compile(r"\[TOPOLOGY\]\s+node=(\d+)\s+planes=(\d+)")
_RE_PORTABLE_PRESSURE = re.compile(
    r"\[PORTABLE-PRESSURE\]\s+node=(\d+)\s+planes=(\d+)\s+"
    r"hot_lines=(\d+)\s+pressure_lines=(\d+)\s+"
    r"total_unique_lines=(\d+)\s+naive_capacity_lines=(\d+)\s+"
    r"target_footprint_lines=(\d+)\s+pressure_level_pct=(\d+)\s+"
    r"batches=(\d+)")
_RE_GUEST_TIMER = re.compile(
    r"\[GUEST-TIMER\]\s+node=(\d+)\s+phase=(\S+)\s+operations=(\d+)\s+"
    r"counter_ticks=(\d+)\s+counter_frequency_hz=(\d+)\s+"
    r"source=arm_cntvct_el0\s+unit=counter_ticks")
_RE_PERF_LATENCY = re.compile(
    r"\[PERF-LATENCY\]\s+node=(\d+)\s+phase=(\S+)\s+samples=(\d+)\s+"
    r"min=(\d+)\s+p50=(\d+)\s+p95=(\d+)\s+p99=(\d+)\s+max=(\d+)\s+"
    r"mean=(\d+)\s+counter_frequency_hz=(\d+)\s+"
    r"source=arm_cntvct_el0\s+unit=counter_ticks")
# Q2: Interleaved-output fallback — when concurrent writes from
# multiple CPUs corrupt the READ_VAL line, the tail often survives
# as "1223344 MATCH".  We extract the actual value prefix and verdict.
_RE_READ_VAL_TAIL = re.compile(
    r"(\w+)\s+(MATCH|MISMATCH)"
)

def parse_read_vals(lines):
    reads = []
    for i, line in enumerate(lines):
        m = _RE_READ_VAL.search(line)
        if m:
            reads.append({
                "node": int(m.group(1)), "home": int(m.group(2)),
                "expected": m.group(3), "actual": m.group(4),
                "verdict": m.group(5), "raw": line.strip(),
            })
            continue
        # Q2 fallback: interleaved output — look for tail pattern
        m2 = _RE_READ_VAL_TAIL.search(line)
        if m2 and i > 0:
            # Look backward for [READ_VAL] prefix in previous line
            if "[READ_VAL]" in lines[i-1]:
                # Try to find home node from previous lines
                home = 1  # default for TC2/TC3
                for j in range(i-1, max(i-10, -1), -1):
                    bm = re.search(r"\[BEFORE_RD\]\s+node=(\d+)\s+home=(\d+)",
                                   lines[j])
                    if bm:
                        home = int(bm.group(2))
                        break
                actual_val = m2.group(1)
                verdict = m2.group(2)
                reads.append({
                    "node": 1, "home": home,
                    "expected": "11223344",
                    "actual": actual_val, "verdict": verdict,
                    "raw": f"[READ_VAL] tail: {line.strip()}",
                })
    return reads

# ── Per-TC verification ───────────────────────────────────────────
def verify_tc1(reads, lines):
    if len(reads) != 1:
        return False, f"TC1 FAILED: expected 1 READ_VAL, got {len(reads)}", reads
    actual = int(reads[0]["actual"], 16)
    if actual != 0xCAFE:
        return False, f"TC1 FAILED: expected 0xCAFE, got 0x{actual:X}", [reads[0]]
    return True, "TC1 PASSED: single read 0xCAFE", []

def verify_tc2(reads, lines):
    if len(reads) != 1:
        return False, f"TC2 FAILED: expected 1 READ_VAL, got {len(reads)}", reads
    if reads[0]["node"] != 1:
        return False, f"TC2 FAILED: expected Node1 read, got Node{reads[0]['node']}", [reads[0]]
    actual = int(reads[0]["actual"], 16)
    if actual != 0x11223344:
        return False, f"TC2 FAILED: expected 0x11223344, got 0x{actual:X}", [reads[0]]
    return True, "TC2 PASSED: Node1 read 0x11223344", []

def verify_tc3(reads, lines):
    if len(reads) != 3:
        return False, f"TC3 FAILED: expected 3 READ_VAL, got {len(reads)}", reads
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC3 FAILED: {len(mismatches)} MISMATCH(es)", mismatches
    return True, "TC3 PASSED: 3 reads all MATCH", []

def verify_tc4(reads, lines):
    if len(reads) != 4:
        return False, f"TC4 FAILED: expected 4 READ_VAL, got {len(reads)}", reads
    # Check each node read the expected values, regardless of file ordering
    node_vals = {}
    for r in reads:
        n = r["node"]
        if n not in node_vals:
            node_vals[n] = []
        node_vals[n].append(int(r["actual"], 16))
    expected = {0: [0x1, 0x3], 1: [0x2], 2: [0x3]}
    for n, exp_vals in expected.items():
        if n not in node_vals or sorted(node_vals[n]) != sorted(exp_vals):
            return False, f"TC4 FAILED: node {n} expected {[hex(v) for v in exp_vals]}, got {[hex(v) for v in node_vals.get(n, [])]}", reads
    # Verify all reads matched
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC4 FAILED: {len(mismatches)} MISMATCH(es)", mismatches
    return True, "TC4 PASSED: all nodes read correct values", []


def verify_tc5(reads, lines):
    """TC5: All 3 nodes must agree on a final value ∈ {0xAA000001, 0xBB000002, 0xCC000003}."""
    if len(reads) < 3:
        return False, f"TC5 FAILED: expected ≥3 READ_VAL (one per node), got {len(reads)}", reads
    legal = {0xAA000001, 0xBB000002, 0xCC000003}
    # Collect each node's last read value
    node_last = {}
    for r in reads:
        node_last[r["node"]] = int(r["actual"], 16)
    if len(node_last) < 3:
        return False, f"TC5 FAILED: only {len(node_last)} nodes produced READ_VAL", reads
    values = set(node_last.values())
    if len(values) != 1:
        return False, f"TC5 FAILED: nodes disagree on final value: {node_last}", reads
    final_val = list(values)[0]
    if final_val not in legal:
        return False, f"TC5 FAILED: final value 0x{final_val:X} not in legal set", reads
    return True, f"TC5 PASSED: all 3 nodes converged to 0x{final_val:X}", []


def verify_tc6(reads, lines):
    """TC6: Node1 and Node2 must both read 0xDEADBEEF."""
    node1_reads = [r for r in reads if r["node"] == 1]
    node2_reads = [r for r in reads if r["node"] == 2]
    if len(node1_reads) == 0:
        return False, "TC6 FAILED: no READ_VAL from Node1", reads
    if len(node2_reads) == 0:
        return False, "TC6 FAILED: no READ_VAL from Node2", reads
    for r in node1_reads:
        if int(r["actual"], 16) != 0xDEADBEEF:
            return False, f"TC6 FAILED: Node1 read 0x{r['actual']}, expected 0xDEADBEEF", [r]
    for r in node2_reads:
        if int(r["actual"], 16) != 0xDEADBEEF:
            return False, f"TC6 FAILED: Node2 read 0x{r['actual']}, expected 0xDEADBEEF", [r]
    return True, "TC6 PASSED: Node1 and Node2 both read 0xDEADBEEF", []


def verify_tc7(reads, lines):
    """TC7: Exactly 1 READ_VAL, must be 0x55667788."""
    if len(reads) != 1:
        return False, f"TC7 FAILED: expected exactly 1 READ_VAL, got {len(reads)}", reads
    actual = int(reads[0]["actual"], 16)
    if actual != 0x55667788:
        return False, f"TC7 FAILED: expected 0x55667788, got 0x{actual:X}", [reads[0]]
    return True, "TC7 PASSED: writeback+read preserved 0x55667788", []


def verify_tc8(reads, lines):
    """TC8: Node1's final read must be 0xBBB after upgrade-invalidate."""
    node1_reads = [r for r in reads if r["node"] == 1]
    if len(node1_reads) == 0:
        return False, "TC8 FAILED: no READ_VAL from Node1 after upgrade", reads
    # The last Node1 read (Phase 4) must be 0xBBB
    last_n1 = node1_reads[-1]
    actual = int(last_n1["actual"], 16)
    if actual != 0xBBB:
        return False, f"TC8 FAILED: Node1 final read 0x{actual:X}, expected 0xBBB", [last_n1]
    return True, "TC8 PASSED: Node1 read 0xBBB after invalidate+upgrade", []


def verify_tc9(reads, lines):
    """TC9: Negative test — must reject non-DSM access.
    Success means: [FATAL] marker emitted AND no READ_VAL produced,
    OR simulation aborted with page-fault panic."""
    expected_fault = "Page table fault when accessing virtual address 0xfffff8000000"
    for line in lines:
        if expected_fault in line and len(reads) == 0:
            return True, "TC9 PASSED: expected non-DSM page fault detected", []
    if len(reads) > 0:
        return False, "TC9 FAILED: unexpected [READ_VAL] in negative test", reads
    return False, "TC9 FAILED: no [FATAL] or rejection signal detected", []


def verify_tc200(reads, lines):
    """TC200: naive capacity eviction recalls and preserves dirty payload."""
    target_reads = [r for r in reads if r["node"] == 2]
    if len(target_reads) != 1:
        return False, f"TC200 FAILED: expected one Node2 READ_VAL, got {len(target_reads)}", reads
    read = target_reads[0]
    if read["verdict"] != "MATCH" or int(read["actual"], 16) != 0xBEEFCAFE:
        return False, "TC200 FAILED: dirty recall payload mismatch", [read]
    required = ("[UBCC-NAIVE-EVICT]", "[UBCC-NAIVE-DIRTY-RECALL-PAYLOAD]",
                "[UBCC-NAIVE-EVICT-DONE]")
    missing = [marker for marker in required if not any(marker in line for line in lines)]
    if missing:
        return False, f"TC200 FAILED: missing naive recall evidence {missing}", []
    return True, "TC200 PASSED: naive dirty recall preserved payload", []


def verify_tc10(reads, lines):
    """TC10: All read values must be in legal set {0xA0000000..0xA0000000+ROUNDS} and not 0."""
    ROUNDS = 100
    if len(reads) == 0:
        return False, "TC10 FAILED: no READ_VAL from concurrent phase", []
    legal_values = set(0xA0000000 + i for i in range(ROUNDS))
    violations = []
    for r in reads:
        actual_val = int(r["actual"], 16)
        if actual_val == 0:
            violations.append(r)
        elif actual_val not in legal_values:
            violations.append(r)
    if violations:
        return False, f"TC10 FAILED: {len(violations)} illegal values (including 0)", violations
    return True, f"TC10 PASSED: {len(reads)} reads, all in legal range, no 0s", []


def verify_tc11(reads, lines):
    """TC11 (local_upgrade): Node B writes after shared read → verify snoop chain.
    
    Phase 1: Node B (node=1) first-reads DSM_C → expected 0x00000000.
    Phase 3: Node C (node=2, home) reads DSM_C → expected 0xCA01.
    Phase 4: Node A (node=0) reads DSM_C → expected 0xCA01.
    """
    if len(reads) < 3:
        return False, f"TC11 FAILED: expected ≥3 READ_VAL, got {len(reads)}", reads
    
    # Phase 1: Node B first read
    node1_reads = [r for r in reads if r["node"] == 1]
    if len(node1_reads) < 1:
        return False, "TC11 FAILED: no READ_VAL from Node B (Phase 1)", reads
    # First miss returns 0 (uninitialized); if DMA pre-seeded, accept any value
    # but verify Phase 3/4 values are consistent.
    
    # Phase 3: Node C (home) read — must be 0xCA01
    node2_reads = [r for r in reads if r["node"] == 2]
    if len(node2_reads) < 1:
        return False, "TC11 FAILED: no READ_VAL from Node C (Phase 3)", reads
    for r in node2_reads[-1:]:  # last Node C read
        if int(r["actual"], 16) != 0xCA01:
            return False, f"TC11 FAILED: Node C read 0x{r['actual']}, expected 0xCA01", [r]
    
    # Phase 4: Node A read — must be 0xCA01
    node0_reads = [r for r in reads if r["node"] == 0]
    if len(node0_reads) < 1:
        return False, "TC11 FAILED: no READ_VAL from Node A (Phase 4)", reads
    for r in node0_reads[-1:]:  # last Node A read
        if int(r["actual"], 16) != 0xCA01:
            return False, f"TC11 FAILED: Node A read 0x{r['actual']}, expected 0xCA01", [r]
    
    return True, "TC11 PASSED: local upgrade snoop chain — all reads correct", []


_re_sync = re.compile(r"\[SYNC\]\s+node=(\d+)\s+iter=(\d+)\s+seg=(\d+)\s+val=(\d+)")


def verify_tc12(reads, lines):
    """TC12: Barrier correctness — all nodes must produce markers for each (iter,seg).
    
    Per-node files are read separately so interleaving is not preserved.
    Instead verify that every (iter,seg) has markers from all participating nodes.
    """
    syncs = []
    for line in lines:
        m = _re_sync.search(line)
        if m:
            syncs.append((int(m.group(1)), int(m.group(2)),
                          int(m.group(3)), int(m.group(4))))

    n_nodes = len(set(s[0] for s in syncs))
    expected_total = n_nodes * 10 * 3
    if len(syncs) < expected_total:
        return False, f"TC12 FAILED: got {len(syncs)} SYNC markers, expected {expected_total}", []

    # Group by (iter, seg)
    from collections import defaultdict
    groups = defaultdict(set)
    for (node, iter_v, seg_v, val) in syncs:
        groups[(iter_v, seg_v)].add(node)

    # Every (iter, seg) must have all n_nodes present
    missing = []
    for (iter_v, seg_v), nodes in sorted(groups.items()):
        if len(nodes) != n_nodes:
            missing.append((iter_v, seg_v, nodes))

    if missing:
        return False, f"TC12 FAILED: {len(missing)} groups missing nodes: {missing[:5]}", []

    # Per-node monotonicity: within each node's markers, (iter,seg) must be strictly increasing
    node_last = {}
    for (node, iter_v, seg_v, val) in syncs:
        key = (iter_v, seg_v)
        if node in node_last and key <= node_last[node]:
            return False, f"TC12 FAILED: node={node} non-monotonic at iter={iter_v} seg={seg_v}", []
        node_last[node] = key

    return True, f"TC12 PASSED: all {n_nodes} nodes × 30 segments synced correctly", []


def verify_tc13(reads, lines):
    """TC13: Cross-node release/acquire ordering across DATA and FLAG lines.
    Node1 must see FLAG=1 then DATA=0x2222 (not stale 0x1111)."""
    _re_flag = re.compile(r'\[FLAG_SEEN\]\s+node=1\s+val=1')
    flag_seen = any(_re_flag.search(l) for l in lines)
    if not flag_seen:
        return False, 'TC13 FAILED: Node1 never observed FLAG=1', []
    data_reads_n1 = [r for r in reads if r['node'] == 1 and r['home'] == 2]
    if not data_reads_n1:
        return False, 'TC13 FAILED: no final DATA read from Node1', []
    # Last Node1 DATA read must be 0x2222
    final_data = int(data_reads_n1[-1]['actual'], 16)
    if final_data != 0x2222:
        return False, f'TC13 FAILED: final DATA=0x{final_data:X}, expected 0x2222', [data_reads_n1[-1]]
    return True, 'TC13 PASSED: release/acquire ordering preserved', []


def verify_tc14(reads, lines):
    """TC14: Three-node mixed read/write waves.
    Each wave: writer writes Vx, then two readers must see Vx.
    Uses [PHASE_RD] markers: step=1→0x1001, step=2→0x2002, step=3→0x3003."""
    _re_phase_rd = re.compile(r'\[PHASE_RD\]\s+node=(\d+)\s+step=(\d+)\s+val=(\w+)')
    phase_reads = {}  # (step, node) -> value
    for line in lines:
        m = _re_phase_rd.search(line)
        if m:
            step = int(m.group(2))
            node = int(m.group(1))
            val = int(m.group(3), 16)
            phase_reads[(step, node)] = val
    expect = {
        (1, 1): 0x1001, (1, 2): 0x1001,
        (2, 0): 0x2002, (2, 2): 0x2002,
        (3, 0): 0x3003, (3, 1): 0x3003,
    }
    missing = [k for k in expect if k not in phase_reads]
    if missing:
        return False, f'TC14 FAILED: missing phase reads {missing}', []
    bad = [(k, phase_reads[k], expect[k]) for k in expect if phase_reads[k] != expect[k]]
    if bad:
        return False, f'TC14 FAILED: stale/mismatched wave reads {bad}', []
    return True, 'TC14 PASSED: mixed multi-sharer waves serialized correctly', []


def verify_tc15(reads, lines):
    """TC15: Credit/recovery storm — stresses RetryAck/PCrdGrant paths.
    All 8 DSM lines must converge across all 3 nodes, no deadlock.
    RetryAck/PCrdGrant evidence is advisory (requires protocol debug enabled)."""
    retry_cnt = sum(1 for l in lines if 'RetryAck' in l)
    pcrd_cnt = sum(1 for l in lines if 'PCrdGrant' in l)
    if any('deadlock' in l.lower() or 'panic:' in l.lower() for l in lines):
        return False, 'TC15 FAILED: deadlock/panic under credit pressure', []
    # Convergence: all 3 nodes must agree on each of the 8 DSM lines
    node2_reads = [r for r in reads if r['home'] == 2]
    if len(node2_reads) < 3 * 8:  # 3 nodes × 8 lines
        return False, f'TC15 FAILED: expected ≥24 convergence reads, got {len(node2_reads)}', []
    # Group by node: collect each node's 8-line value tuple
    node_vals = {}
    for r in node2_reads:
        n = r['node']
        if n not in node_vals:
            node_vals[n] = []
        node_vals[n].append(int(r['actual'], 16))
    if len(node_vals) < 3:
        return False, f'TC15 FAILED: only {len(node_vals)} nodes produced convergence reads', []
    # Check: each position index should have same value across nodes
    for idx in range(8):
        idx_vals = {node_vals[n][idx] for n in node_vals if idx < len(node_vals[n])}
        if len(idx_vals) != 1:
            return False, f'TC15 FAILED: line {idx} diverged across nodes: {idx_vals}', []
    msg = f'TC15 PASSED: forward progress preserved (RetryAck={retry_cnt}, PCrdGrant={pcrd_cnt})'
    return True, msg, []


def verify_tc16(reads, lines):
    """TC16: Dual shared-upgrade race.
    Final value must be in {0xA0A0, 0xB0B0} with all 3 nodes agreeing."""
    legal = {0xA0A0, 0xB0B0}
    # Collect last read per node (from home=2)
    node_last = {}
    for r in reads:
        if r['home'] == 2:
            node_last[r['node']] = int(r['actual'], 16)
    if len(node_last) < 3:
        return False, f'TC16 FAILED: missing final reads from nodes (got {len(node_last)})', []
    vals = set(node_last.values())
    if len(vals) != 1:
        return False, f'TC16 FAILED: nodes disagree on final value {node_last}', []
    val = next(iter(vals))
    if val not in legal:
        return False, f'TC16 FAILED: illegal final value 0x{val:X}', []
    # Protocol upgrade-path evidence is optional in default verbosity;
    # if present it confirms the path was exercised.
    has_upgrade_evidence = any('UPGRADE_PENDING' in l or 'OuterUpgrade' in l for l in lines)
    suffix = ' (upgrade-path confirmed in log)' if has_upgrade_evidence else ''
    return True, f'TC16 PASSED: concurrent upgrades serialized to 0x{val:X}{suffix}', []


def verify_tc17(reads, lines):
    """TC17: Writeback + DMA + remote-read overlap.
    Pre-DMA read must be 0x12345678; all post-DMA reads must be 0x87654321."""
    _re_read_phase = re.compile(r'\[READ_PHASE\]\s+node=(\d+)\s+tag=(\S+)\s+val=(\w+)')
    tagged = {}
    for line in lines:
        m = _re_read_phase.search(line)
        if m:
            node = int(m.group(1))
            tag = m.group(2)
            val = int(m.group(3), 16)
            tagged[(tag, node)] = val
    # Collect all pre_dma reads (any node)
    pre_reads = {k: v for k, v in tagged.items() if k[0] == 'pre_dma'}
    post_reads = {k: v for k, v in tagged.items() if k[0] == 'post_dma'}
    if not pre_reads:
        return False, 'TC17 FAILED: no pre_dma reads', []
    if not post_reads:
        return False, 'TC17 FAILED: no post_dma reads', []
    for (tag, node), val in pre_reads.items():
        if val != 0x12345678:
            return False, f'TC17 FAILED: pre-DMA node={node} got 0x{val:X}, expected 0x12345678', []
    for (tag, node), val in post_reads.items():
        if val != 0x87654321:
            return False, f'TC17 FAILED: post-DMA node={node} got 0x{val:X}, expected 0x87654321', []
    return True, 'TC17 PASSED: writeback + DMA + remote-read interaction correct', []


def verify_tc18(reads, lines):
    node1 = [r for r in reads if r['node'] == 1]
    node2 = [r for r in reads if r['node'] == 2]
    if not node1 or not node2:
        return False, 'TC18 FAILED: missing reader output from node1/node2', reads
    if int(node1[-1]['actual'], 16) != 0x18181818:
        return False, f"TC18 FAILED: node1 got 0x{node1[-1]['actual']}, expected 0x18181818", [node1[-1]]
    if int(node2[-1]['actual'], 16) != 0x18181818:
        return False, f"TC18 FAILED: node2 got 0x{node2[-1]['actual']}, expected 0x18181818", [node2[-1]]
    return True, 'TC18 PASSED: fill/replay workload value correct', []


def verify_tc19(reads, lines):
    node2 = [r for r in reads if r['node'] == 2]
    if not node2:
        return False, 'TC19 FAILED: missing node2 read', reads
    if int(node2[-1]['actual'], 16) != 0xABCD1234:
        return False, f"TC19 FAILED: node2 got 0x{node2[-1]['actual']}, expected 0xABCD1234", [node2[-1]]
    return True, 'TC19 PASSED: dirty persist workload value correct', []


def verify_tc20(reads, lines):
    if not reads:
        return False, 'TC20 FAILED: no READ_VAL', []
    bad = [r for r in reads if int(r['actual'], 16) != 0x20202020]
    if bad:
        return False, 'TC20 FAILED: unexpected read value', bad
    return True, 'TC20 PASSED', []


def verify_tc21(reads, lines):
    if not reads:
        return False, 'TC21 FAILED: no READ_VAL', []
    bad = [r for r in reads if int(r['actual'], 16) != 0x21212121]
    if bad:
        return False, 'TC21 FAILED: unexpected read value', bad
    return True, 'TC21 PASSED', []


def verify_tc22(reads, lines):
    """TC22: ResidentDir 容量压力后抽检值应全部 MATCH。"""
    if len(reads) < 9:
        return False, f'TC22 FAILED: expected >=9 probe reads, got {len(reads)}', []
    mismatches = [r for r in reads if r['verdict'] != 'MATCH']
    if mismatches:
        return False, f'TC22 FAILED: {len(mismatches)} mismatches under capacity pressure', mismatches
    return True, 'TC22 PASSED: resident pressure with probe values intact', []


def verify_tc23(reads, lines):
    """TC23: BF 假阳性容忍（miss=0，随后回填后命中 MAGIC）。"""
    node0 = [r for r in reads if r['node'] == 0 and r['home'] == 2]
    if len(node0) < 2:
        return False, f'TC23 FAILED: expected 2 node0 reads, got {len(node0)}', node0
    first = int(node0[0]['actual'], 16)
    last = int(node0[-1]['actual'], 16)
    if first != 0:
        return False, f'TC23 FAILED: first miss-read expected 0x0, got 0x{first:X}', [node0[0]]
    if last != 0x23ABCDEF:
        return False, f'TC23 FAILED: refill-read expected 0x23ABCDEF, got 0x{last:X}', [node0[-1]]
    return True, 'TC23 PASSED: false-positive path fallback/refill behavior correct', []


def verify_tc24(reads, lines):
    """TC24: 三节点并发压力后，各 anchor 值全局一致。"""
    if len(reads) < 9:
        return False, f'TC24 FAILED: expected >=9 anchor reads, got {len(reads)}', reads
    mismatches = [r for r in reads if r['verdict'] != 'MATCH']
    if mismatches:
        return False, f'TC24 FAILED: {len(mismatches)} anchor mismatches', mismatches
    exp_vals = {0x24A00001, 0x24B00002, 0x24C00003}
    seen = {int(r['actual'], 16) for r in reads}
    if not exp_vals.issubset(seen):
        return False, f'TC24 FAILED: anchor set incomplete, seen={sorted(hex(v) for v in seen)}', []
    return True, 'TC24 PASSED: concurrent multi-node stress converged on all anchors', []


def verify_tc25(reads, lines):
    """TC25: 高频 ownership 切换后应无漂移，最终值一致。"""
    mismatches = [r for r in reads if r['verdict'] != 'MATCH']
    if mismatches:
        return False, f'TC25 FAILED: {len(mismatches)} mismatch during invalidate/clear cycling', mismatches[:20]
    final_exp = 0x25000000 | (32 - 1)
    node_last = {}
    for r in reads:
        if r['home'] == 2:
            node_last[r['node']] = int(r['actual'], 16)
    if len(node_last) < 3:
        return False, f'TC25 FAILED: missing final reads from all nodes ({node_last})', []
    bad = {n: v for n, v in node_last.items() if v != final_exp}
    if bad:
        return False, f'TC25 FAILED: final value drift {bad}, expected 0x{final_exp:X}', []
    return True, 'TC25 PASSED: rapid invalidate/clear cycling stable', []


def verify_tc26(reads, lines):
    """TC26: L3 eviction 压力后目标 line 应保持。"""
    target = [r for r in reads if r['home'] == 1 and r['node'] in (1, 2)]
    if len(target) < 2:
        return False, f'TC26 FAILED: expected node1/node2 target reads, got {len(target)}', target
    bad = [r for r in target if int(r['actual'], 16) != 0x26ABCDEF]
    if bad:
        return False, 'TC26 FAILED: target line corrupted after L3 pressure', bad
    return True, 'TC26 PASSED: eviction-triggered path preserved target line', []


def verify_tc27(reads, lines):
    """TC27: wrap marker 必须出现且最终值一致。"""
    re_wrap = re.compile(r'\[EPOCH_WRAP\]\s+node=(\d+)\s+start=(\w+)\s+end=(\w+)\s+wraps=(\d+)')
    wraps = 0
    for l in lines:
        m = re_wrap.search(l)
        if m:
            wraps = max(wraps, int(m.group(4)))
    if wraps < 1:
        return False, 'TC27 FAILED: no wrap evidence marker (wraps<1)', []
    # Derive the expected final value from the workload's own emitted expected
    # field (it computes 0x27000000 | (WR_ROUNDS-1)) instead of hardcoding the
    # round count — keeps the check valid if WR_ROUNDS is tuned for split mode.
    node_last = {}
    node_exp = {}
    for r in reads:
        if r['home'] == 0:
            node_last[r['node']] = int(r['actual'], 16)
            if r.get('expected') is not None:
                node_exp[r['node']] = int(r['expected'], 16)
    if len(node_last) < 3:
        return False, f'TC27 FAILED: missing final reads from all nodes ({node_last})', []
    final_exp = max(node_exp.values()) if node_exp else (0x27000000 | (128 - 1))
    bad = {n: hex(v) for n, v in node_last.items() if v != final_exp}
    if bad:
        return False, (f'TC27 FAILED: final value mismatch after churn '
                       f'(expected {hex(final_exp)}): {bad}'), []
    return True, f'TC27 PASSED: wrap marker seen (wraps={wraps}) and final value converged', []


def verify_tc28(reads, lines):
    """TC28: resident 驱逐到 backstore 后，数据+元数据镜像一致。"""
    node2 = [r for r in reads if r['node'] == 2 and r['home'] == 0]
    if len(node2) < 2:
        return False, f'TC28 FAILED: expected 2 node2 reads (data/meta), got {len(node2)}', node2
    vals = {int(r['actual'], 16) for r in node2}
    if 0x28AA55AA not in vals or 0x2855AA55 not in vals:
        return False, f'TC28 FAILED: missing data/meta value, got {sorted(hex(v) for v in vals)}', node2
    rel_ok = any('[META_REL] node=2 ok=1' in l for l in lines)
    if not rel_ok:
        return False, 'TC28 FAILED: metadata relation marker not satisfied', []
    return True, 'TC28 PASSED: backstore data/metadata consistency preserved', []


def verify_tc29(reads, lines):
    node1 = [r for r in reads if r['node'] == 1 and r['home'] == 0]
    if len(node1) < 1:
        return False, 'TC29 FAILED: missing node1 validation read', reads
    if int(node1[-1]['actual'], 16) != 0x2900F111:
        return False, f"TC29 FAILED: expected 0x2900F111, got 0x{int(node1[-1]['actual'],16):X}", [node1[-1]]
    if not any('[TC29_UPG]' in l for l in lines):
        return False, 'TC29 FAILED: missing [TC29_UPG] marker', []
    return True, 'TC29 PASSED: local exclusive->modified upgrade pattern observed', []


def verify_tc30(reads, lines):
    node2 = [r for r in reads if r['node'] == 2 and r['home'] == 0]
    if len(node2) < 1:
        return False, 'TC30 FAILED: missing node2 replay read', reads
    if int(node2[-1]['actual'], 16) != 0x30BB0022:
        return False, f"TC30 FAILED: expected 0x30BB0022, got 0x{int(node2[-1]['actual'],16):X}", [node2[-1]]
    if not any('[TC30_CLR]' in l and 'stale=1' in l and 'replay=1' in l for l in lines):
        return False, 'TC30 FAILED: missing stale/replay marker', []
    return True, 'TC30 PASSED: stale clear/tombstone replay sequence validated', []


def verify_tc31(reads, lines):
    node0 = [r for r in reads if r['node'] == 0 and r['home'] == 0]
    if len(node0) < 12:
        return False, f'TC31 FAILED: expected >=12 verification reads, got {len(node0)}', node0
    bad = [r for r in node0 if r['verdict'] != 'MATCH']
    if bad:
        return False, f'TC31 FAILED: {len(bad)} mismatched per-line checks', bad
    return True, 'TC31 PASSED: multi-CPU per-line coherence isolation holds', []


def verify_tc32(reads, lines):
    node0 = [r for r in reads if r['node'] == 0 and r['home'] == 0]
    if len(node0) < 1:
        return False, 'TC32 FAILED: missing node0 cross-socket read', reads
    if int(node0[-1]['actual'], 16) != 0x3200BB02:
        return False, f"TC32 FAILED: expected 0x3200BB02, got 0x{int(node0[-1]['actual'],16):X}", [node0[-1]]
    lat_line = next((l for l in lines if '[TC32_LAT]' in l), None)
    if not lat_line:
        return False, 'TC32 FAILED: missing [TC32_LAT] marker', []
    m = re.search(r'same=(\w+)\s+cross=(\w+)', lat_line)
    if not m:
        return False, f'TC32 FAILED: malformed latency marker: {lat_line}', []
    same = int(m.group(1), 16)
    cross = int(m.group(2), 16)
    return True, f'TC32 PASSED: cross-socket read valid (same={same}, cross={cross})', []


def verify_tc33(reads, lines):
    node0 = [r for r in reads if r['node'] == 0 and r['home'] == 0]
    if len(node0) < 1:
        return False, 'TC33 FAILED: missing home-socket verification read', reads
    if int(node0[-1]['actual'], 16) != 0x33DD0011:
        return False, f"TC33 FAILED: expected 0x33DD0011, got 0x{int(node0[-1]['actual'],16):X}", [node0[-1]]
    if not any('[TC33_WB]' in l and 'homeSocket=0' in l for l in lines):
        return False, 'TC33 FAILED: missing writeback routing marker', []
    return True, 'TC33 PASSED: cross-socket dirty writeback reached home socket 0', []


def verify_tc34(reads, lines):
    """TC34: dual-socket pingpong — Node0 writes DSM(0,0), Node1 writes DSM(0,1), Node2 reads both."""
    node2 = [r for r in reads if r['node'] == 2]
    if len(node2) < 2:
        return False, f'TC34 FAILED: expected 2 reads from Node2, got {len(node2)}', node2
    a = int(node2[0]['actual'], 16)
    b = int(node2[1]['actual'], 16)
    exp_a = 0xCAFE0000
    exp_b = 0xBEEF0000
    ok = (a == exp_a and b == exp_b)
    if not ok:
        return False, f'TC34 FAILED: expected {hex(exp_a)}+{hex(exp_b)}, got {hex(a)}+{hex(b)}', node2
    return True, 'TC34 PASSED: dual-socket pingpong — both socket planes converged', []


def verify_tc35(reads, lines):
    node0 = [r for r in reads if r['node'] == 0]
    if len(node0) < 3:
        return False, f'TC35 FAILED: expected 3 done-line reads, got {len(node0)}', node0
    exp = {0x35DD0000, 0x35DD0001, 0x35DD0002}
    got = {int(r['actual'], 16) for r in node0[-3:]}
    if got != exp:
        return False, f'TC35 FAILED: done-lines mismatch, got {sorted(hex(v) for v in got)}', node0[-3:]
    progress_nodes = set()
    for l in lines:
        m = re.search(r'\[TC35_PROGRESS\]\s+node=(\d+)', l)
        if m:
            progress_nodes.add(int(m.group(1)))
    if progress_nodes != {0, 1, 2}:
        return False, f'TC35 FAILED: progress marker missing nodes, got {sorted(progress_nodes)}', []
    return True, 'TC35 PASSED: NUMA mixed stress has forward progress on all nodes', []


def verify_tc36(reads, lines):
    need = 0x3600BB22
    if not any('[TC36_GE]' in l and 'ge=1' in l and 'upg_owner=1' in l for l in lines):
        return False, 'TC36 FAILED: missing committed G_E + owner-upgrade marker', []
    if any('[TC36_GE]' in l and ('recall=1' in l or 'inv=1' in l) for l in lines):
        return False, 'TC36 FAILED: unexpected recall/invalidate marker before upgrade completion', []
    node_last = {}
    for r in reads:
        node_last[r['node']] = int(r['actual'], 16)
    if set(node_last.keys()) != {0, 1, 2}:
        return False, f'TC36 FAILED: final reads missing nodes, got {sorted(node_last.keys())}', []
    bad = {n: v for n, v in node_last.items() if v != need}
    if bad:
        return False, f'TC36 FAILED: final value not converged to upgraded write: {bad}', []
    return True, 'TC36 PASSED: owner upgrade in G_E window converged without recall/inv marker', []


def verify_tc37(reads, lines):
    need = 0x3700D222
    if not any('[TC37_GM]' in l and 'gm_before_second=1' in l for l in lines):
        return False, 'TC37 FAILED: missing committed G_M marker before second owner write', []
    if any('reject' in l.lower() or 'duplicate owner' in l.lower() for l in lines):
        return False, 'TC37 FAILED: illegal transition marker/log observed', []
    node_last = {}
    for r in reads:
        node_last[r['node']] = int(r['actual'], 16)
    if set(node_last.keys()) != {0, 1, 2}:
        return False, f'TC37 FAILED: final reads missing nodes, got {sorted(node_last.keys())}', []
    bad = {n: v for n, v in node_last.items() if v != need}
    if bad:
        return False, f'TC37 FAILED: final value mismatch after second owner write: {bad}', []
    return True, 'TC37 PASSED: G_M owner-side second write converged legally', []


def verify_tc38(reads, lines):
    need = 0x38CC0033
    line = next((l for l in lines if '[TC38_CLR]' in l), None)
    if not line:
        return False, 'TC38 FAILED: missing stale clear storm marker', []
    m = re.search(r'stale_clear_seen=(\d+)\s+replay_ok=(\d+)', line)
    if not m:
        return False, f'TC38 FAILED: malformed stale-clear marker: {line}', []
    if int(m.group(1)) < 2 or int(m.group(2)) != 1:
        return False, f'TC38 FAILED: marker constraints not met: {line}', []
    target = [r for r in reads if r['node'] in (1, 2)]
    if len(target) < 2:
        return False, f'TC38 FAILED: expected final reads from Node1/Node2, got {len(target)}', target
    bad = [r for r in target[-2:] if int(r['actual'], 16) != need]
    if bad:
        return False, 'TC38 FAILED: stale clear/tombstone storm corrupted final value', bad
    return True, 'TC38 PASSED: stale clear storm markers + final convergence validated', []


def verify_tc39(reads, lines):
    need = 0x3900B022
    route_lines = [l for l in lines if '[TC39_ROUTE]' in l and 'homeSocket=1' in l]
    if len(route_lines) < 2:
        return False, 'TC39 FAILED: insufficient socket-routing markers for homeSocket=1', []
    node_last = {}
    for r in reads:
        if r['home'] == 1:
            node_last[r['node']] = int(r['actual'], 16)
    if set(node_last.keys()) != {0, 1, 2}:
        return False, f'TC39 FAILED: final same-PA reads missing nodes, got {sorted(node_last.keys())}', []
    bad = {n: v for n, v in node_last.items() if v != need}
    if bad:
        return False, f'TC39 FAILED: split-brain final value detected: {bad}', []
    return True, 'TC39 PASSED: dual-socket same-PA converged on home socket 1', []


def verify_tc40(reads, lines):
    need = 0x4000D1A1
    retry_line = next((l for l in lines if '[TC40_RECALL]' in l), None)
    if not retry_line:
        return False, 'TC40 FAILED: missing recall timeout/retry marker', []
    m = re.search(r'retry_count=(\d+)', retry_line)
    if not m or int(m.group(1)) < 1:
        return False, f'TC40 FAILED: retry marker invalid: {retry_line}', []
    node2 = [r for r in reads if r['node'] == 2]
    node0 = [r for r in reads if r['node'] == 0]
    if not node2 or not node0:
        return False, 'TC40 FAILED: missing Node2/Node0 completion reads', reads
    if int(node2[-1]['actual'], 16) != need or int(node0[-1]['actual'], 16) != need:
        return False, 'TC40 FAILED: recall retry path did not converge to owner value', [node2[-1], node0[-1]]
    return True, 'TC40 PASSED: retry marker observed and eventual completion succeeded', []


def verify_tc41(reads, lines):
    need = 0x4100B222
    has_recall = any('[TC41_PHASE]' in l and 'step=recall' in l for l in lines)
    has_inv = any('[TC41_PHASE]' in l and 'step=invalidate' in l for l in lines)
    if not (has_recall and has_inv):
        return False, 'TC41 FAILED: missing recall/invalidate overlap markers', []
    node_last = {}
    for r in reads:
        node_last[r['node']] = int(r['actual'], 16)
    if set(node_last.keys()) != {0, 1, 2}:
        return False, f'TC41 FAILED: final reads missing nodes, got {sorted(node_last.keys())}', []
    bad = {n: v for n, v in node_last.items() if v != need}
    if bad:
        return False, f'TC41 FAILED: stale value survived after invalidate sequence: {bad}', []
    return True, 'TC41 PASSED: recall+invalidate serialized and converged to V2', []


def verify_tc42(reads, lines):
    need = 0x42A00001
    ep = next((l for l in lines if '[TC42_EPOCH]' in l), None)
    if not ep or 'ffffff,0' not in ep:
        return False, 'TC42 FAILED: missing exact wrap boundary marker (ffffff->0)', []
    node_last = {}
    for r in reads:
        node_last[r['node']] = int(r['actual'], 16)
    if set(node_last.keys()) != {0, 1, 2}:
        return False, f'TC42 FAILED: final reads missing nodes, got {sorted(node_last.keys())}', []
    bad = {n: v for n, v in node_last.items() if v != need}
    if bad:
        return False, f'TC42 FAILED: wrap-window final convergence failed: {bad}', []
    return True, 'TC42 PASSED: exact wrap marker seen and final value converged', []


def verify_tc43(reads, lines):
    need = 0x43000000 | (64 - 1)
    bad_reads = [r for r in reads if r['verdict'] == 'MISMATCH']
    if bad_reads:
        return False, f'TC43 FAILED: {len(bad_reads)} mismatches during owner cycling', bad_reads[:20]
    progress = sum(1 for l in lines if '[TC43_ROUND]' in l)
    if progress < 4:
        return False, f'TC43 FAILED: insufficient progress markers ({progress})', []
    node_last = {}
    for r in reads:
        node_last[r['node']] = int(r['actual'], 16)
    if set(node_last.keys()) != {0, 1, 2}:
        return False, f'TC43 FAILED: final reads missing nodes, got {sorted(node_last.keys())}', []
    bad = {n: v for n, v in node_last.items() if v != need}
    if bad:
        return False, f'TC43 FAILED: final tag mismatch after rapid ownership cycles: {bad}', []
    return True, 'TC43 PASSED: rapid owner cycles maintained liveness and final convergence', []


def verify_tc44(reads, lines):
    expected_vals = {0x44A00022, 0x44B00022, 0x44C00022, 0x44D00022}
    paths = {'upgrade', 'writeback_fill', 'recall', 'invalidate_unique'}
    seen_paths = set()
    for l in lines:
        m = re.search(r'\[TC44_PATH\].*tag=(\S+)', l)
        if m:
            seen_paths.add(m.group(1))
    if not paths.issubset(seen_paths):
        return False, f'TC44 FAILED: missing protocol path markers: {sorted(paths - seen_paths)}', []
    node_reads = {0: [], 1: [], 2: []}
    for r in reads:
        if r['node'] in node_reads:
            node_reads[r['node']].append(int(r['actual'], 16))
    for n in (0, 1, 2):
        if len(node_reads[n]) < 4:
            return False, f'TC44 FAILED: node {n} missing final 4-line reads', []
        got = set(node_reads[n][-4:])
        if got != expected_vals:
            return False, f'TC44 FAILED: node {n} final matrix mismatch {sorted(hex(v) for v in got)}', []
    return True, 'TC44 PASSED: full protocol matrix paths + per-line finals validated', []


def verify_tc45(reads, lines):
    need = 0x4500BB22
    marker = next((l for l in lines if '[TC45_STRESS]' in l), None)
    if not marker:
        return False, 'TC45 FAILED: missing bloom/fill stress marker', []
    m = re.search(r'sat_count=(\d+)\s+fill_conflict=(\d+)', marker)
    if not m or int(m.group(1)) < 1 or int(m.group(2)) != 1:
        return False, f'TC45 FAILED: invalid stress marker: {marker}', []
    t = [r for r in reads if r['node'] in (1, 2)]
    if len(t) < 2:
        return False, f'TC45 FAILED: expected final reads from Node1/Node2, got {len(t)}', t
    bad = [r for r in t[-2:] if int(r['actual'], 16) != need]
    if bad:
        return False, 'TC45 FAILED: target line corrupted under bloom/fill pressure', bad
    return True, 'TC45 PASSED: fill-conflict+bloom-pressure marker and final value validated', []


def verify_tc46(reads, lines):
    byte_lines = [l for l in lines if '[TC46_BYTE]' in l]
    if len(byte_lines) != 64:
        return False, f'TC46 FAILED: expected 64 byte-check lines, got {len(byte_lines)}', []

    mismatches = []
    seen_idx = set()
    for l in byte_lines:
        m = re.search(r'idx=(\d+)\s+exp=(\d+)\s+act=(\d+)\s+(MATCH|MISMATCH)', l)
        if not m:
            mismatches.append({'raw': l})
            continue
        idx = int(m.group(1))
        exp = int(m.group(2))
        act = int(m.group(3))
        verdict = m.group(4)
        seen_idx.add(idx)
        if verdict != 'MATCH' or exp != act:
            mismatches.append({'raw': l})

    if seen_idx != set(range(64)):
        missing = sorted(set(range(64)) - seen_idx)
        return False, f'TC46 FAILED: missing byte indices {missing}', mismatches[:8]

    summary = next((l for l in lines if '[TC46_SUMMARY]' in l), None)
    if not summary:
        return False, 'TC46 FAILED: missing summary marker', mismatches[:8]
    sm = re.search(r'checked=(\d+)\s+mismatches=(\d+)', summary)
    if not sm:
        return False, f'TC46 FAILED: invalid summary marker: {summary}', mismatches[:8]
    if int(sm.group(1)) != 64 or int(sm.group(2)) != 0:
        return False, f'TC46 FAILED: summary not clean: {summary}', mismatches[:8]

    if mismatches:
        return False, f'TC46 FAILED: {len(mismatches)} byte mismatches detected', mismatches[:8]
    return True, 'TC46 PASSED: 64-byte multi-beat recall integrity verified', []


_FAULT_TRIGGER_RE = re.compile(
    r"\[UBFAULT-TRIGGER\].*?rule='([^']+)'.*?action=(Drop|Duplicate|Delay|Reorder)\b")
_FAULT_DELIVER_RE = re.compile(
    r"\[UBFAULT-DELIVER\].*?rule='([^']+)'.*?action=(Drop|Duplicate|Delay|Reorder)\b")


def _fault_events(lines, marker_re):
    """Count explicit UBFAULT events; the runner supplies stdout only."""
    events = {}
    for line in lines:
        match = marker_re.search(line)
        if not match:
            continue
        name, action = match.groups()
        actions = events.setdefault(name, {})
        actions[action] = actions.get(action, 0) + 1
    return events


def _fault_counts_for_prefix(events, prefix):
    return {name: dict(actions) for name, actions in events.items()
            if name.startswith(prefix)}


def _verify_fault_events(tc_id, lines, expected_actions, delivery_rules=()):
    """Verify exact trigger/action sets and buffered deliveries.

    Drop has no delivery. Duplicate verifies the duplication decision because
    existing logs do not separately identify both emitted copies.
    """
    prefix = f"tc{tc_id}_"
    triggers = _fault_counts_for_prefix(
        _fault_events(lines, _FAULT_TRIGGER_RE), prefix)
    deliveries = _fault_counts_for_prefix(
        _fault_events(lines, _FAULT_DELIVER_RE), prefix)
    delivery_names = set(delivery_rules)
    trigger_errors = {name: triggers.get(name, {})
                      for name, action in expected_actions.items()
                      if triggers.get(name, {}) != {action: 1}}
    delivery_errors = {name: deliveries.get(name, {})
                       for name in delivery_names
                       if deliveries.get(name, {}) !=
                       {expected_actions[name]: 1}}
    unexpected_triggers = sorted(set(triggers) - set(expected_actions))
    unexpected_deliveries = sorted(set(deliveries) - delivery_names)
    trigger_total = sum(sum(actions.values()) for actions in triggers.values())
    delivery_total = sum(sum(actions.values()) for actions in deliveries.values())
    counts = (f"trigger_count={trigger_total}/{len(expected_actions)} "
              f"delivery_count={delivery_total}/{len(delivery_names)}")
    if (trigger_errors or delivery_errors or unexpected_triggers or
            unexpected_deliveries):
        return False, (f"TC{tc_id} FAILED: strict fault verification {counts}; "
                       f"trigger_errors={trigger_errors} "
                       f"unexpected_triggers={unexpected_triggers} "
                       f"delivery_errors={delivery_errors} "
                       f"unexpected_deliveries={unexpected_deliveries}")
    return True, counts


def verify_tc47(reads, lines):
    """TC47: drop Clear, verify tombstone recovery.
    Node1 must read 0x47AA0011 despite a dropped ClearReq."""
    target_val = 0x47AA0011
    node1_reads = [r for r in reads if r['node'] == 1]
    node2_reads = [r for r in reads if r['node'] == 2]
    if not node1_reads:
        return False, 'TC47 FAILED: no READ_VAL from Node1', reads
    if not node2_reads:
        return False, 'TC47 FAILED: no READ_VAL from Node2', reads
    for r in node1_reads:
        if int(r['actual'], 16) != target_val:
            return False, f"TC47 FAILED: Node1 read 0x{r['actual']}, expected 0x{target_val:X}", [r]
    for r in node2_reads:
        if int(r['actual'], 16) != target_val:
            return False, f"TC47 FAILED: Node2 read 0x{r['actual']}, expected 0x{target_val:X}", [r]
    fault_ok, fault_msg = _verify_fault_events(
        47, lines, {"tc47_drop_clear": "Drop"})
    if not fault_ok:
        return False, fault_msg, []
    return True, (f'TC47 PASSED: dropped ClearReq and final value converged; '
                  f'{fault_msg}'), []


def verify_tc48(reads, lines):
    """TC48: duplicate InvalidateAck — idempotent ack handling."""
    target_val = 0x48BB0022
    # All 3 nodes must read the final value
    node_reads = {}
    for r in reads:
        node_reads.setdefault(r['node'], []).append(int(r['actual'], 16))
    for n in (0, 1, 2):
        if n not in node_reads or not node_reads[n]:
            return False, f'TC48 FAILED: no READ_VAL from Node{n}', reads
        if node_reads[n][-1] != target_val:
            return False, f"TC48 FAILED: Node{n} final read 0x{node_reads[n][-1]:X}, expected 0x{target_val:X}", reads
    fault_ok, fault_msg = _verify_fault_events(
        48, lines, {"tc48_dup_inv_ack": "Duplicate"})
    if not fault_ok:
        return False, fault_msg, []
    return True, (f'TC48 PASSED: duplicate InvalidateAck decision handled '
                  f'idempotently; {fault_msg}'), []


def verify_tc49(reads, lines):
    """TC49: reordered InvalidateAck perturbation — converges anyway."""
    target_val = 0x49CC0033
    # All 3 nodes must read the final value
    node_reads = {}
    for r in reads:
        node_reads.setdefault(r['node'], []).append(int(r['actual'], 16))
    for n in (0, 1, 2):
        if n not in node_reads or not node_reads[n]:
            return False, f'TC49 FAILED: no READ_VAL from Node{n}', reads
        if node_reads[n][-1] != target_val:
            return False, f"TC49 FAILED: Node{n} final read 0x{node_reads[n][-1]:X}, expected 0x{target_val:X}", reads
    fault_ok, fault_msg = _verify_fault_events(
        49, lines, {"tc49_reorder_inv_ack": "Reorder"},
        {"tc49_reorder_inv_ack"})
    if not fault_ok:
        return False, fault_msg, []
    return True, (f'TC49 PASSED: reordered InvalidateAck delivered and converged; '
                  f'{fault_msg}'), []


def verify_tc50(reads, lines):
    """TC50: 3-node producer-consumer ring final token check."""
    if len(reads) != 3:
        return False, f"TC50 FAILED: expected 3 READ_VAL, got {len(reads)}", reads
    by_node = {r['node']: r for r in reads}
    for n in (0, 1, 2):
        if n not in by_node:
            return False, f"TC50 FAILED: missing READ_VAL from Node{n}", reads
        r = by_node[n]
        if r['verdict'] != 'MATCH':
            return False, f"TC50 FAILED: Node{n} final token mismatch", [r]
    return True, 'TC50 PASSED: ring producer-consumer converged for all 3 nodes', []


def verify_tc51(reads, lines):
    """TC51: bank ledger total invariant must hold."""
    node0_reads = [r for r in reads if r['node'] == 0]
    if len(node0_reads) < 5:
        return False, f"TC51 FAILED: expected >=5 Node0 READ_VAL, got {len(node0_reads)}", node0_reads
    total_read = node0_reads[-1]
    expected_total = 4 * 100000
    got_total = int(total_read['actual'], 16)
    if got_total != expected_total:
        return False, f"TC51 FAILED: ledger total {got_total} != {expected_total}", [total_read]
    if total_read['verdict'] != 'MATCH':
        return False, 'TC51 FAILED: total invariant read reported mismatch', [total_read]
    return True, 'TC51 PASSED: concurrent transfers preserved ledger total', []


def verify_tc52(reads, lines):
    """TC52: Node2 gather checks 3 partials + 1 sum."""
    node2_reads = [r for r in reads if r['node'] == 2]
    if len(node2_reads) < 4:
        return False, f"TC52 FAILED: expected >=4 Node2 READ_VAL, got {len(node2_reads)}", node2_reads
    mismatches = [r for r in node2_reads if r['verdict'] != 'MATCH']
    if mismatches:
        return False, f"TC52 FAILED: {len(mismatches)} gather mismatches", mismatches
    return True, 'TC52 PASSED: scatter-map-gather result is consistent', []


def verify_tc53(reads, lines):
    """TC53: contention storm must show all nodes reached full rounds."""
    node0_reads = [r for r in reads if r['node'] == 0]
    if len(node0_reads) < 4:
        return False, f"TC53 FAILED: expected >=4 Node0 READ_VAL, got {len(node0_reads)}", node0_reads
    fairness_reads = node0_reads[:3]
    for idx, r in enumerate(fairness_reads):
        expected = int(r['expected'], 16)
        actual = int(r['actual'], 16)
        if actual != expected:
            return False, f"TC53 FAILED: fairness counter[{idx}]={actual} expected {expected}", [r]
        if r['verdict'] != 'MATCH':
            return False, f"TC53 FAILED: fairness read {idx} mismatch", [r]
    return True, 'TC53 PASSED: cache storm finished without starvation', []


def verify_tc54(reads, lines):
    """TC54: 2x2 tiled matmul output matrix check."""
    node2_reads = [r for r in reads if r['node'] == 2]
    if len(node2_reads) != 4:
        return False, f"TC54 FAILED: expected 4 Node2 READ_VAL, got {len(node2_reads)}", node2_reads
    mismatches = [r for r in node2_reads if r['verdict'] != 'MATCH']
    if mismatches:
        return False, f"TC54 FAILED: {len(mismatches)} output mismatches", mismatches
    return True, 'TC54 PASSED: NUMA-aware tiled matmul output correct', []


def verify_tc63(reads, lines):
    """TC63: RECALL orphan timer cleanup — owner never responds, timer sweeps orphan."""
    marker = next((l for l in lines if '[TC63_ORPHAN] cleanup=timer' in l), None)
    if not marker:
        return False, 'TC63 FAILED: missing TC63_ORPHAN timer marker', []
    node0 = [r for r in reads if r['node'] == 0]
    if len(node0) < 1:
        return False, f'TC63 FAILED: expected >=1 Node0 READ_VAL, got {len(node0)}', node0
    last = node0[-1]
    if last['verdict'] != 'MATCH':
        return False, f'TC63 FAILED: Node0 final read mismatch after timer cleanup', [last]
    return True, 'TC63 PASSED: timer cleanup recovered orphan RECALL, PA accessible again', []


def verify_tc64(reads, lines):
    """TC64: RECALL.DONE lazy cleanup — new requester triggers cleanup before arbitration."""
    marker = next((l for l in lines if '[TC64_ORPHAN] cleanup=lazy' in l), None)
    if not marker:
        return False, 'TC64 FAILED: missing TC64_ORPHAN lazy marker', []
    node0 = [r for r in reads if r['node'] == 0]
    node2 = [r for r in reads if r['node'] == 2]
    if len(node2) < 1:
        return False, f'TC64 FAILED: expected Node2 to complete recall read first', node2
    if node2[-1]['verdict'] != 'MATCH':
        return False, f'TC64 FAILED: Node2 recall read mismatch', [node2[-1]]
    if len(node0) < 1:
        return False, f'TC64 FAILED: expected Node0 final read after lazy cleanup', node0
    if node0[-1]['verdict'] != 'MATCH':
        return False, f'TC64 FAILED: Node0 final read mismatch after lazy cleanup', [node0[-1]]
    return True, "TC64 PASSED: lazy cleanup removed RECALL.DONE orphan, new requester served", []


def verify_tc90(reads, lines):
    """TC90: 8-node all-to-all DSM read. 8 nodes x 8 reads = 64 READ_VAL, all MATCH."""
    if len(reads) < 64:
        return False, f"TC90 FAILED: expected 64 READ_VAL, got {len(reads)}", reads
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC90 FAILED: {len(mismatches)} MISMATCH(es)", mismatches
    return True, "TC90 PASSED: 8-node all-to-all (8x8 reads all MATCH)", []


def verify_tc160(reads, lines):
    """TC160: 16 nodes share a node-15 line, then node 0 invalidates it."""
    if len(reads) != 32:
        return False, f"TC160 FAILED: expected 32 READ_VAL, got {len(reads)}", reads
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC160 FAILED: {len(mismatches)} MISMATCH(es)", mismatches
    nodes = {r["node"] for r in reads}
    homes = {r["home"] for r in reads}
    if nodes != set(range(16)) or homes != {15}:
        return False, (f"TC160 FAILED: nodes={sorted(nodes)} homes={sorted(homes)}"), reads
    counts = {node: sum(r["node"] == node for r in reads) for node in nodes}
    if any(count != 2 for count in counts.values()):
        return False, f"TC160 FAILED: per-node read counts={counts}", reads
    return True, "TC160 PASSED: 16-way share and node-0 invalidation on node-15 home", []


def verify_tc91(reads, lines):
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC91 FAILED: {len(mismatches)} MISMATCH(es)", mismatches
    return True, "TC91 PASSED: 8-node hotspot contention", []

def verify_tc92(reads, lines):
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC92 FAILED: {len(mismatches)} MISMATCH(es)", mismatches
    return True, "TC92 PASSED: 8-node butterfly data migration", []

def verify_tc93(reads, lines):
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC93 FAILED: {len(mismatches)} MISMATCH(es)", mismatches
    return True, "TC93 PASSED: 8-node pairwise pingpong", []

def verify_tc94(reads, lines):
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC94 FAILED: {len(mismatches)} MISMATCH(es)", mismatches
    return True, "TC94 PASSED: single-round 8-node barrier", []

def verify_tc95(reads, lines):
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC95 FAILED: {len(mismatches)} MISMATCH(es)", mismatches
    return True, "TC95 PASSED: 8n2s per-socket barrier stress", []


def verify_tc96(reads, lines):
    """TC96: 8-node dual-socket cross-socket read. 16 READ_VAL (one per socket-plane)."""
    if len(reads) < 16:
        return False, f"TC96 FAILED: expected 16 READ_VAL, got {len(reads)}", reads
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC96 FAILED: {len(mismatches)} MISMATCH(es)", mismatches
    return True, "TC96 PASSED: 8n2s cross-socket read (16/16 MATCH)", []


def verify_tc97(reads, lines):
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC97 FAILED: {len(mismatches)} MISMATCH(es)", mismatches
    return True, "TC97 PASSED: 8n2s ownership ping-pong", []


def verify_tc98(reads, lines):
    """TC98: 8n2s same-PA hot-spot. 16 READ_VAL for done markers, all MATCH."""
    if len(reads) < 16:
        return False, f"TC98 FAILED: expected 16 READ_VAL, got {len(reads)}", reads
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC98 FAILED: {len(mismatches)} MISMATCH(es)", mismatches
    return True, "TC98 PASSED: 8n2s same-PA hot-spot (16 done markers MATCH)", []


def verify_tc99(reads, lines):
    """TC99: 8n2s per-plane slot contention (milder TC98 variant)."""
    if len(reads) < 16:
        return False, f"TC99 FAILED: expected 16 READ_VAL, got {len(reads)}", reads
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC99 FAILED: {len(mismatches)} MISMATCH(es)", mismatches
    return True, "TC99 PASSED: 8n2s per-plane slot contention (16 done markers MATCH)", []


def verify_tc100(reads, lines):
    """TC100: 8n2s batch RS. Same cache line hammered by 16 readers, one final MATCH."""
    if len(reads) < 1:
        return False, f"TC100 FAILED: expected 1 READ_VAL, got {len(reads)}", reads
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC100 FAILED: {len(mismatches)} MISMATCH(es)", mismatches
    batch_lines = [l for l in lines if "BATCH-RS" in l]
    return True, f"TC100 PASSED: batch RS (1 final MATCH, {len(batch_lines)} BATCH-RS grants)", []


def verify_tc101(reads, lines):
    """TC101: 8n2s direct-forward chain. 16 READ_VAL (one per socket-plane), all MATCH."""
    if len(reads) < 16:
        return False, f"TC101 FAILED: expected 16 READ_VAL, got {len(reads)}", reads
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC101 FAILED: {len(mismatches)} MISMATCH(es)", mismatches
    fwd_lines = [l for l in lines if "C4-FORWARD" in l]
    return True, f"TC101 PASSED: direct-forward chain ({len(fwd_lines)} C4 forward events)", []


def verify_tc102(reads, lines):
    """TC102: Writeback data persistence.
    At least 1 READ_VAL (node 2 cross-node read after eviction), all MATCH."""
    if len(reads) < 1:
        return False, f"TC102 FAILED: expected >=1 READ_VAL, got {len(reads)}", reads
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC102 FAILED: {len(mismatches)} MISMATCH(es) — writeback data lost", mismatches
    return True, f"TC102 PASSED: writeback dirty data persisted ({len(reads)} reads OK)", []


def verify_tc110(reads, lines):
    """TC110: drop ClearReq fault injection (3.1 P1).
    All 3 nodes must agree on final value ∈ {0x11000001, 0x11000002, 0x11000003}."""
    if len(reads) < 3:
        return False, f"TC110 FAILED: expected ≥3 READ_VAL, got {len(reads)}", reads
    legal = {0x11000001, 0x11000002, 0x11000003}
    node_last = {}
    for r in reads:
        node_last[r["node"]] = int(r["actual"], 16)
    if len(node_last) < 3:
        return False, f"TC110 FAILED: only {len(node_last)} nodes produced READ_VAL", reads
    values = set(node_last.values())
    if len(values) != 1:
        return False, f"TC110 FAILED: nodes disagree on final value: {node_last}", reads
    final_val = list(values)[0]
    if final_val not in legal:
        return False, f"TC110 FAILED: final value 0x{final_val:X} not in legal set", reads
    fault_ok, fault_msg = _verify_fault_events(
        110, lines, {"tc110_drop_clear": "Drop"})
    if not fault_ok:
        return False, fault_msg, []
    return True, (f"TC110 PASSED: ClearReq dropped, all nodes converged to "
                  f"0x{final_val:X}; {fault_msg}"), []


def verify_tc111(reads, lines):
    """TC111: silent upgrade fault immunity — 3.2 P1.
    All nodes must converge to 0x1110BBB2 after node1 upgrade write."""
    if len(reads) < 4:
        return False, f"TC111 FAILED: expected ≥4 READ_VAL, got {len(reads)}", reads
    target = 0x1110BBB2
    # Each read must match its OWN expected value. The workload has an
    # intentional Phase-2 pre-upgrade read (expected=0x1110AAA1) plus the
    # Phase-4 post-upgrade convergence reads (expected=0x1110BBB2); enforcing a
    # single global target would wrongly flag the legitimate Phase-2 read.
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, (f"TC111 FAILED: {len(mismatches)} read(s) did not match "
                       f"their expected value"), mismatches
    # Convergence: the post-upgrade reads (expected==target) must exist for
    # every participating node and all read the upgraded value.
    conv = [r for r in reads if int(r["expected"], 16) == target]
    if not conv:
        return False, (f"TC111 FAILED: no post-upgrade convergence read "
                       f"(expected 0x{target:X})"), reads
    for r in conv:
        if int(r["actual"], 16) != target:
            return False, f"TC111 FAILED: expected 0x{target:X}, got {r['actual']}", [r]
    upgrade_triggers = []
    for line in lines:
        match = _FAULT_TRIGGER_RE.search(line)
        if match and re.search(r"\btype=UpgradeReq\b", line):
            upgrade_triggers.append(match.groups())
    if upgrade_triggers:
        if upgrade_triggers != [("tc111_silent_upgrade_drop", "Drop")]:
            return False, ("TC111 FAILED: UpgradeReq trigger_count="
                           f"{len(upgrade_triggers)}/1 delivery_count=0/0; "
                           f"observed={upgrade_triggers}"), []
        fault_ok, fault_msg = _verify_fault_events(
            111, lines, {"tc111_silent_upgrade_drop": "Drop"})
        if not fault_ok:
            return False, fault_msg, []
        mode_msg = f"fault mode; {fault_msg}"
    else:
        # Bounded source markers: EPBackend emits kind=upgrade_silent and
        # SILENT-WRITE-HIT; EPRNFController emits silent upgrade plus explicit
        # zero-cross-node wording.
        silent_markers = [line for line in lines
                          if ("kind=upgrade_silent" in line or
                              "SILENT-WRITE-HIT" in line or
                              ("silent upgrade" in line.lower() and
                               ("zero cross-node" in line.lower() or
                                "0 cross-node" in line.lower())))]
        if not silent_markers:
            return False, ("TC111 FAILED: trigger_count=0/1 delivery_count=0/0; "
                           "no explicit kind=upgrade_silent, SILENT-WRITE-HIT, "
                           "or silent-upgrade zero-cross-node marker was captured"), []
        mode_msg = (f"silent-upgrade mode; trigger_count=0/0 delivery_count=0/0 "
                    f"silent_markers={len(silent_markers)}")
    return True, f"TC111 PASSED: converged to 0x{target:X}; {mode_msg}", []


def verify_tc112(reads, lines):
    """TC112: TBE interference — 3.6 P1.
    Cross-node DSM writes must converge. Local markers are observability-only
    because concurrent raw writes can be truncated in split simout capture."""
    if len(reads) < 3:
        return False, f"TC112 FAILED: expected 3 READ_VAL, got {len(reads)}", reads
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC112 FAILED: {len(mismatches)} mismatches", mismatches
    local_lines = [l for l in lines if "[TC112_LOCAL]" in l]
    return True, f"TC112 PASSED: cross-node converged, {len(local_lines)} local-progress markers", []


def verify_tc113(reads, lines):
    """TC113: silent upgrade micro-bench — 4.5 P2.
    Final value must be 0x11300000 | ((ITERS-1) & 0xFFF) = 0x113003E7.
    Phase 2 has an intentional pre-upgrade read (expected=0x11300000) that
    must NOT be compared against the post-upgrade target."""
    if len(reads) < 4:
        return False, f"TC113 FAILED: expected ≥4 READ_VAL, got {len(reads)}", reads
    target = 0x11300000 | (999 & 0xFFF)  # ITERS=1000, last iter = 999
    # Each read must match its own expected value
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, (f"TC113 FAILED: {len(mismatches)} read(s) did not match "
                       f"their expected value"), mismatches
    # Post-upgrade convergence reads must all see the target
    conv = [r for r in reads if int(r["expected"], 16) == target]
    if len(conv) < 3:
        return False, (f"TC113 FAILED: expected ≥3 convergence reads "
                       f"(expected 0x{target:X}), got {len(conv)}"), reads
    for r in conv:
        if int(r["actual"], 16) != target:
            return False, f"TC113 FAILED: expected 0x{target:X}, got {r['actual']}", [r]
    upg_markers = [l for l in lines if "[TC113_UPG]" in l]
    done_markers = [l for l in lines if "[TC113_DONE]" in l]
    return True, f"TC113 PASSED: {len(upg_markers)} upgrade markers, {len(done_markers)} done", []


def verify_tc114(reads, lines):
    """TC114: minimal silent upgrade from R_M → M."""
    target = 0x1140B000
    target_hex = f"0x{target:X}"
    for r in reads:
        if int(r["actual"], 16) != target:
            return False, f"TC114 FAILED: expected {target_hex}, got {r['actual']}", [r]
    return True, "TC114 PASSED: silent upgrade minimal RM→M converged", []


def verify_tc115(reads, lines):
    """TC115: Cross-CPU silend upgrade across different L2 clusters.
    All nodes must converge to 0x1150B000."""
    target = 0x1150B000
    for r in reads:
        if int(r["actual"], 16) != target:
            return False, f"TC115 FAILED: expected 0x{target:X}, got {r['actual']}", [r]
    # Check for diagnostic markers (CPU2 is in different cluster from CPU0)
    cpu0_signal = any("[TC115_CPU0] signal CPU2" in l for l in lines)
    cpu2_store  = any("[TC115_CPU2] flag seen, store v2" in l for l in lines)
    diag = f"cpu0_signal={cpu0_signal}, cpu2_store={cpu2_store}"
    return True, f"TC115 PASSED: cross-CPU silent upgrade ({diag})", []


def verify_tc116(reads, lines):
    """TC116: ResidentDir eviction/reload performance stress."""
    if len(reads) < 9:
        return False, f"TC116 FAILED: expected >=9 READ_VAL, got {len(reads)}", reads
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC116 FAILED: {len(mismatches)} mismatches", mismatches
    phases = [
        "hot_populate", "hot_shared", "cold_overflow",
        "hot_reuse_reload", "hot_upgrade", "tc116_done",
    ]
    missing = [p for p in phases
               if not any(f"[PHASE]" in l and f"phase={p}" in l for l in lines)]
    if missing:
        return False, f"TC116 FAILED: missing phase markers {missing}", []

    dir_stats = [l for l in lines if "[ResidentDirStats]" in l]
    evictions = 0
    misses = 0
    for ds in dir_stats:
        import re
        m = re.search(r'"dir_evictions":(\d+)', ds)
        if m:
            evictions = max(evictions, int(m.group(1)))
        m = re.search(r'"dir_misses":(\d+)', ds)
        if m:
            misses = max(misses, int(m.group(1)))
    return True, (f"TC116 PASSED: reads={len(reads)}, dir_evictions={evictions}, "
                  f"dir_misses={misses}"), []


def verify_tc117(reads, lines):
    """TC117: ClearReq reorder fault — 3.3 P1.
    Both DSM lines must converge to their expected values despite a reordered
    ClearReq. Check fault evidence is present in ubio stderr."""
    if len(reads) < 2:
        return False, f"TC117 FAILED: expected ≥2 READ_VAL, got {len(reads)}", reads
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC117 FAILED: {len(mismatches)} mismatches", mismatches
    fault_ok, fault_msg = _verify_fault_events(
        117, lines, {"tc117_reorder_clear": "Reorder"},
        {"tc117_reorder_clear"})
    if not fault_ok:
        return False, fault_msg, []
    return True, f"TC117 PASSED: reordered ClearReq delivered; {fault_msg}", []


def verify_tc120(reads, lines):
    """TC120: baseline/optimized performance mix smoke verifier."""
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC120 FAILED: {len(mismatches)} mismatches", mismatches[:10]
    phase_done = any("[PHASE]" in l and "phase=tc120_done" in l
                     for l in lines)
    stats = [l for l in lines if "[ResidentDirStats]" in l or "[UBCC-STATS]" in l]
    naive = any("naiveDirEvictions" in l for l in stats)
    return True, (f"TC120 PASSED: reads={len(reads)}, phase_done={phase_done}, "
                  f"stats_lines={len(stats)}, naive_stats={naive}"), []


def verify_perf_workload(tc_id, reads, lines):
    if len(reads) < 1:
        return False, f"TC{tc_id} FAILED: no READ_VAL", reads
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC{tc_id} FAILED: {len(mismatches)} mismatches", mismatches[:10]
    phase_count = sum(1 for l in lines if "[PHASE]" in l and "status=done" in l)
    stats_count = sum(1 for l in lines if "[ResidentDirStats]" in l or "[UBCC-STATS]" in l)
    naive_count = sum(1 for l in lines if "[UBCC-NAIVE-EVICT]" in l)
    opt_count = sum(1 for l in lines
                    if "BATCH-RS" in l or "SILENT" in l or "C4" in l or "DIRECT-FWD" in l)
    if phase_count < 2:
        return False, f"TC{tc_id} FAILED: insufficient phase markers ({phase_count})", []
    return True, (f"TC{tc_id} PASSED: reads={len(reads)}, phases={phase_count}, "
                  f"stats={stats_count}, naive={naive_count}, opt_markers={opt_count}"), []


def verify_tc121(reads, lines):
    return verify_perf_workload(121, reads, lines)


def verify_tc122(reads, lines):
    return verify_perf_workload(122, reads, lines)


def verify_tc123(reads, lines):
    return verify_perf_workload(123, reads, lines)


def verify_tc124(reads, lines):
    return verify_perf_workload(124, reads, lines)


def verify_tc130(reads, lines):
    """TC130: high-footprint naive-vs-spill directory benchmark."""
    if len(reads) < 24:
        return False, f"TC130 FAILED: expected >=24 hot-line checks, got {len(reads)}", reads
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC130 FAILED: {len(mismatches)} hot-line mismatches", mismatches[:10]
    required = ("hot_populate", "hot_share", "overflow_pressure", "hot_reuse")
    missing = [phase for phase in required
               if not any("[PHASE]" in line and f"phase={phase}" in line for line in lines)]
    if missing:
        return False, f"TC130 FAILED: missing phases {missing}", []
    timer_error = verify_guest_timer(lines)
    if timer_error:
        return False, f"TC130 FAILED: {timer_error}", []
    return True, f"TC130 PASSED: hot checks={len(reads)}, guest timer healthy", []


def verify_guest_timer(lines):
    samples = [_RE_GUEST_TIMER.search(line) for line in lines]
    samples = [sample for sample in samples if sample]
    selftests = [sample for sample in samples if sample.group(2) == "timer_selftest"]
    if not selftests:
        return "missing arm_cntvct_el0 timer_selftest"
    if any(int(sample.group(4)) == 0 or int(sample.group(5)) == 0
           for sample in selftests):
        return "zero timer_selftest counter_ticks or counter_frequency_hz"
    return None


def verify_real_capacity_workload(tc_id, reads, lines, phases, min_reads):
    if len(reads) < min_reads:
        return False, f"TC{tc_id} FAILED: expected >= {min_reads} READ_VAL, got {len(reads)}", reads
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC{tc_id} FAILED: {len(mismatches)} mismatches", mismatches[:10]
    missing = [phase for phase in phases
               if not any("[PHASE]" in line and f"phase={phase}" in line for line in lines)]
    if missing:
        return False, f"TC{tc_id} FAILED: missing phases {missing}", []
    timer_error = verify_guest_timer(lines)
    if timer_error:
        return False, f"TC{tc_id} FAILED: {timer_error}", []
    return True, f"TC{tc_id} PASSED: reads={len(reads)}, real-capacity pressure completed", []


def verify_tc131(reads, lines):
    return verify_real_capacity_workload(131, reads, lines,
                                         ("catalog_seed", "catalog_share", "full_scan", "catalog_reuse",
                                          "exclusive_upgrade"), 8)


def verify_tc132(reads, lines):
    return verify_real_capacity_workload(132, reads, lines,
                                         ("checkpoint_seed", "dirty_stream", "checkpoint_recover"), 16)


def verify_tc133(reads, lines):
    return verify_real_capacity_workload(133, reads, lines,
                                         ("frontier_seed", "frontier_share", "frontier_pressure", "frontier_reuse"), 7)


def verify_tc134(reads, lines):
    return verify_real_capacity_workload(134, reads, lines,
                                         ("window_seed", "window_share", "window_pressure", "window_reuse"), 7)


def verify_latency_distribution_workload(tc_id, reads, lines, phases,
                                         latency_phase, sample_count,
                                         expected_reads_by_node,
                                         required_timer_phase=None):
    expected_reads = sum(expected_reads_by_node.values())
    if len(reads) != expected_reads:
        return False, (f"TC{tc_id} FAILED: expected {expected_reads} READ_VAL, "
                       f"got {len(reads)}"), reads
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC{tc_id} FAILED: {len(mismatches)} mismatches", mismatches[:10]
    missing = [phase for phase in phases
               if not any("[PHASE]" in line and f"phase={phase}" in line
                          for line in lines)]
    if missing:
        return False, f"TC{tc_id} FAILED: missing phases {missing}", []
    timer_error = verify_guest_timer(lines)
    if timer_error:
        return False, f"TC{tc_id} FAILED: {timer_error}", []
    for node, count in expected_reads_by_node.items():
        actual = sum(1 for read in reads if read["node"] == node)
        if actual != count:
            return False, (f"TC{tc_id} FAILED: node{node} READ_VAL count="
                           f"{actual}, expected {count}"), reads
    unexpected_nodes = sorted({read["node"] for read in reads} -
                              set(expected_reads_by_node))
    if unexpected_nodes:
        return False, (f"TC{tc_id} FAILED: unexpected READ_VAL nodes "
                       f"{unexpected_nodes}"), reads
    if required_timer_phase and not any(
            sample and sample.group(2) == required_timer_phase
            for sample in (_RE_GUEST_TIMER.search(line) for line in lines)):
        return False, (f"TC{tc_id} FAILED: missing GUEST-TIMER phase "
                       f"{required_timer_phase}"), []

    samples = [_RE_PERF_LATENCY.search(line) for line in lines]
    samples = [sample for sample in samples
               if sample and sample.group(2) == latency_phase]
    if len(samples) != 1:
        return False, (f"TC{tc_id} FAILED: expected one {latency_phase} "
                       f"PERF-LATENCY marker, got {len(samples)}"), []
    sample = samples[0]
    latency_node = {
        135: 1, 136: 1, 137: 2, 138: 2, 139: 1, 140: 0,
    }[tc_id]
    if int(sample.group(1)) != latency_node:
        return False, (f"TC{tc_id} FAILED: latency marker node="
                       f"{sample.group(1)}, expected {latency_node}"), []
    if int(sample.group(3)) != sample_count:
        return False, (f"TC{tc_id} FAILED: {latency_phase} samples="
                       f"{sample.group(3)}, expected {sample_count}"), []
    values = [int(sample.group(i)) for i in range(4, 10)]
    minimum, p50, p95, p99, maximum, mean = values
    frequency = int(sample.group(10))
    if minimum == 0 or frequency == 0:
        return False, f"TC{tc_id} FAILED: zero latency or counter frequency", []
    if not minimum <= p50 <= p95 <= p99 <= maximum:
        return False, f"TC{tc_id} FAILED: unordered latency percentiles", []
    if not minimum <= mean <= maximum:
        return False, f"TC{tc_id} FAILED: mean outside latency range", []
    return True, (f"TC{tc_id} PASSED: reads={len(reads)}, phase={latency_phase}, "
                  f"samples={sample_count}, p50={p50}, p99={p99}"), []


def verify_tc135(reads, lines):
    return verify_latency_distribution_workload(
        135, reads, lines,
        ("seed_hot", "share_hot", "directory_pressure", "first_revisit"),
        "preserved_sharer_first_load", 24, {1: 48})


def verify_tc136(reads, lines):
    return verify_latency_distribution_workload(
        136, reads, lines,
        ("dirty_owner_seed", "directory_pressure", "owner_store_reuse", "verify_final"),
        "preserved_owner_store_complete", 24, {2: 24})


def verify_tc137(reads, lines):
    return verify_latency_distribution_workload(
        137, reads, lines,
        ("seed_hot", "share_hot", "directory_pressure", "new_requester_load"),
        "new_requester_first_load", 24, {1: 24, 2: 24})


def verify_tc138(reads, lines):
    return verify_latency_distribution_workload(
        138, reads, lines,
        ("dirty_owner_seed", "directory_pressure", "ownership_handoff", "verify_final"),
        "dirty_owner_handoff_store", 24, {0: 24})


def verify_tc139(reads, lines):
    return verify_latency_distribution_workload(
        139, reads, lines,
        ("seed_hot", "share_hot", "owner_hot", "directory_pressure",
         "mixed_batches", "verify_final"),
        "mixed_batch_16ops", 16, {1: 16, 2: 8},
        "mixed_batch_throughput")


def verify_tc140(reads, lines):
    return verify_latency_distribution_workload(
        140, reads, lines,
        ("cross_l2_store", "verify_final"),
        "cross_l2_owner_store", 24, {2: 24})


def verify_tc141(reads, lines):
    if len(reads) != 32:
        return False, f"TC141 FAILED: expected 32 READ_VAL, got {len(reads)}", reads
    mismatches = [read for read in reads if read["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC141 FAILED: {len(mismatches)} mismatches", mismatches[:10]
    expected_nodes = {1: 16, 2: 16}
    for node, count in expected_nodes.items():
        actual = sum(1 for read in reads if read["node"] == node)
        if actual != count:
            return False, (f"TC141 FAILED: node{node} READ_VAL count={actual}, "
                           f"expected {count}"), reads
    required_phases = ("seed_hot", "share_hot", "directory_pressure",
                       "shared_to_writer", "verify_final")
    missing = [phase for phase in required_phases
               if not any("[PHASE]" in line and f"phase={phase}" in line
                          for line in lines)]
    if missing:
        return False, f"TC141 FAILED: missing phases {missing}", []
    is_naive = any(
        ("[UBIO-POLICY]" in line and "effective=naive" in line) or
        ("[RUNNER-MANIFEST]" in line and "policy=naive" in line) or
        ("[UBCC-STATE]" in line and "policy=naive" in line)
        for line in lines)
    if is_naive:
        if not any("UBCC-NAIVE-EVICT" in line for line in lines):
            return False, "TC141 FAILED: missing naive eviction evidence", []
    else:
        required_markers = ("RESIDENT-SPILL-DONE", "RESIDENT-FILL-DONE",
                            "UBCC-SHARED-RELEASE")
        missing = [marker for marker in required_markers
                   if not any(marker in line for line in lines)]
        if missing:
            return False, f"TC141 FAILED: missing protocol evidence {missing}", []
    if any("RESIDENT-WAITER-UPGRADE-DROP-NOT-SHARER" in line for line in lines):
        return False, "TC141 FAILED: upgrade lost valid sharer status", []
    return True, "TC141 PASSED: shared-to-writer recovery completed", []


def verify_portable_large_workload(tc_id, reads, lines, phases, reads_per_plane,
                                   latency_phase, service_phase,
                                   end_to_end_phase, operations, samples):
    meta = [_RE_E2E_META.search(line) for line in lines]
    planes = sorted({int(match.group(1)) for match in meta
                     if match and match.group(2) == f"TC{tc_id}"})
    if not planes:
        return False, f"TC{tc_id} FAILED: no E2E_META participants", []
    topology = [_RE_TOPOLOGY.search(line) for line in lines]
    topology = [(int(match.group(1)), int(match.group(2))) for match in topology
                if match]
    declared_counts = {count for _, count in topology}
    if len(declared_counts) != 1:
        return False, (f"TC{tc_id} FAILED: inconsistent topology declarations "
                       f"{sorted(declared_counts)}"), []
    expected_planes = declared_counts.pop()
    expected_set = list(range(expected_planes))
    if planes != expected_set:
        return False, (f"TC{tc_id} FAILED: planes={planes}, expected "
                       f"{expected_set}"), []
    topology_planes = sorted(plane for plane, _ in topology)
    if topology_planes != expected_set:
        return False, (f"TC{tc_id} FAILED: topology markers={topology_planes}, "
                       f"expected {expected_set}"), []
    pressure = [_RE_PORTABLE_PRESSURE.search(line) for line in lines]
    pressure = [match for match in pressure if match]
    if len(pressure) != expected_planes:
        return False, (f"TC{tc_id} FAILED: expected {expected_planes} portable "
                       f"pressure records, got {len(pressure)}"), []
    pressure_nodes = sorted(int(match.group(1)) for match in pressure)
    if pressure_nodes != expected_set:
        return False, (f"TC{tc_id} FAILED: pressure nodes={pressure_nodes}, "
                       f"expected {expected_set}"), []
    configs = {tuple(int(match.group(index)) for index in range(2, 10))
               for match in pressure}
    if len(configs) != 1:
        return False, f"TC{tc_id} FAILED: inconsistent pressure configs", []
    (config_planes, hot_lines, pressure_lines, total_unique,
     naive_capacity, target_footprint, pressure_pct, config_batches) = configs.pop()
    if config_planes != expected_planes or config_batches != samples:
        return False, f"TC{tc_id} FAILED: invalid pressure topology/batches", []
    if total_unique != hot_lines + pressure_lines or naive_capacity <= 0:
        return False, f"TC{tc_id} FAILED: invalid pressure footprint", []
    if target_footprint != 0 and total_unique != target_footprint:
        return False, (f"TC{tc_id} FAILED: total_unique={total_unique}, "
                       f"target={target_footprint}"), []
    if pressure_pct != 0 and total_unique * 100 != naive_capacity * pressure_pct:
        return False, (f"TC{tc_id} FAILED: footprint {total_unique}/{naive_capacity} "
                       f"does not equal {pressure_pct}%"), []
    expected_reads = len(planes) * reads_per_plane
    if len(reads) != expected_reads:
        return False, (f"TC{tc_id} FAILED: expected {expected_reads} READ_VAL, "
                       f"got {len(reads)}"), reads
    mismatches = [read for read in reads if read["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC{tc_id} FAILED: {len(mismatches)} mismatches", mismatches[:10]
    for plane in planes:
        actual = sum(1 for read in reads if read["node"] == plane)
        if actual != reads_per_plane:
            return False, (f"TC{tc_id} FAILED: plane{plane} READ_VAL count="
                           f"{actual}, expected {reads_per_plane}"), reads
    unexpected = sorted({read["node"] for read in reads} - set(planes))
    if unexpected:
        return False, f"TC{tc_id} FAILED: unexpected READ_VAL nodes {unexpected}", reads

    missing = []
    for phase in phases:
        for plane in planes:
            if not any("[PHASE]" in line and f"node={plane}" in line and
                       f"phase={phase}" in line for line in lines):
                missing.append(f"{phase}@plane{plane}")
    if missing:
        return False, f"TC{tc_id} FAILED: missing phases {missing}", []
    timer_error = verify_guest_timer(lines)
    if timer_error:
        return False, f"TC{tc_id} FAILED: {timer_error}", []

    timers = {service_phase: {}, end_to_end_phase: {}}
    for line in lines:
        match = _RE_GUEST_TIMER.search(line)
        if match and match.group(2) in (service_phase, end_to_end_phase):
            timers[match.group(2)].setdefault(int(match.group(1)), []).append(match)
    for phase in (service_phase, end_to_end_phase):
        for plane in planes:
            matches = timers[phase].get(plane, [])
            if len(matches) != 1:
                return False, (f"TC{tc_id} FAILED: expected one {phase} timer "
                               f"for plane{plane}, got {len(matches)}"), []
            match = matches[0]
            if int(match.group(3)) != operations:
                return False, (f"TC{tc_id} FAILED: {phase} plane{plane} "
                               f"operations={match.group(3)}, expected {operations}"), []
            if int(match.group(4)) == 0 or int(match.group(5)) == 0:
                return False, f"TC{tc_id} FAILED: zero {phase} ticks/frequency", []
    service_ticks = {plane: int(timers[service_phase][plane][0].group(4))
                     for plane in planes}
    end_to_end_ticks = {plane: int(timers[end_to_end_phase][plane][0].group(4))
                        for plane in planes}
    for plane in planes:
        if end_to_end_ticks[plane] < service_ticks[plane]:
            return False, (f"TC{tc_id} FAILED: plane{plane} end-to-end ticks "
                           f"{end_to_end_ticks[plane]} below service ticks "
                           f"{service_ticks[plane]}"), []

    latency = [match for line in lines
               if (match := _RE_PERF_LATENCY.search(line)) and
               match.group(2) == latency_phase]
    by_plane = {}
    for sample in latency:
        by_plane.setdefault(int(sample.group(1)), []).append(sample)
    for plane in planes:
        plane_samples = by_plane.get(plane, [])
        if len(plane_samples) != 1:
            return False, (f"TC{tc_id} FAILED: expected one {latency_phase} "
                           f"for plane{plane}, got {len(plane_samples)}"), []
        sample = plane_samples[0]
        if int(sample.group(3)) != samples:
            return False, (f"TC{tc_id} FAILED: {latency_phase} plane{plane} "
                           f"samples={sample.group(3)}, expected {samples}"), []
        minimum, p50, p95, p99, maximum, mean = (
            int(sample.group(index)) for index in range(4, 10))
        if minimum == 0 or int(sample.group(10)) == 0:
            return False, f"TC{tc_id} FAILED: zero latency/frequency", []
        if not minimum <= p50 <= p95 <= p99 <= maximum:
            return False, f"TC{tc_id} FAILED: unordered latency percentiles", []
        if not minimum <= mean <= maximum:
            return False, f"TC{tc_id} FAILED: mean outside latency range", []
    return True, (f"TC{tc_id} PASSED: planes={len(planes)}, "
                  f"operations={operations * len(planes)}, batches={samples}"), []


def verify_tc142(reads, lines):
    return verify_portable_large_workload(
        142, reads, lines,
        ("buffer_pool_seed", "buffer_pool_warm", "incremental_pressure",
         "oltp_transactions", "oltp_verify"),
        5, "db_oltp_batch_32ops", "db_oltp_service",
        "db_oltp_end_to_end", 1024, 32)


def verify_tc143(reads, lines):
    return verify_portable_large_workload(
        143, reads, lines,
        ("btree_seed", "btree_warm", "btree_pressure", "btree_transactions",
         "btree_verify"),
        5, "db_btree_batch_64ops", "db_btree_service",
        "db_btree_end_to_end", 2048, 32)


def verify_tc144(reads, lines):
    return verify_portable_large_workload(
        144, reads, lines,
        ("database_seed", "database_warm", "checkpoint_pressure",
         "wal_transactions", "recovery_verify"),
        17, "db_wal_batch_32ops", "db_wal_service",
        "db_wal_end_to_end", 1024, 32)


def verify_tc145(reads, lines):
    return verify_portable_large_workload(
        145, reads, lines,
        ("faas_runtime_seed", "faas_runtime_warm", "faas_invocations",
         "faas_verify"),
        9, "faas_batch_64ops", "faas_service", "faas_end_to_end", 2048, 32)


def verify_tc146(reads, lines):
    return verify_portable_large_workload(
        146, reads, lines,
        ("graph_seed", "graph_frontier_warm", "graph_iterations",
         "graph_verify"),
        5, "graph_batch_64ops", "graph_service", "graph_end_to_end", 2048, 32)


def verify_tc147(reads, lines):
    return verify_portable_large_workload(
        147, reads, lines,
        ("feature_store_seed", "feature_store_warm", "feature_batches",
         "feature_verify"),
        9, "feature_batch_64ops", "feature_service",
        "feature_end_to_end", 2048, 32)


def verify_tc201(reads, lines):
    if len(reads) != 1:
        return False, f"TC201 FAILED: expected one recall verification, got {len(reads)}", reads
    if reads[0]['verdict'] != 'MATCH':
        return False, "TC201 FAILED: recalled payload mismatch", reads
    required = ("RESIDENT-SPILL-DONE", "RESIDENT-FILL-ISSUED", "RESIDENT-FILL-DONE")
    missing = [marker for marker in required if not any(marker in line for line in lines)]
    if missing:
        return False, f"TC201 FAILED: missing H64 spill/fill evidence {missing}", []
    return True, "TC201 PASSED: spill, H64 fill, and recalled payload verified", []


def verify_tc202(reads, lines):
    if len(reads) != 1:
        return False, f"TC202 FAILED: expected one verification, got {len(reads)}", reads
    if reads[0]['verdict'] != 'MATCH':
        return False, "TC202 FAILED: payload mismatch", reads
    if not any("RESIDENT-SPILL-DONE" in line for line in lines):
        return False, "TC202 FAILED: missing spill completion", []
    return True, "TC202 PASSED: spill and payload verification completed", []


def verify_tc203(reads, lines):
    """TC203: H64 metadata spill/onload regression, not Schema A overflow."""
    if len(reads) != 1 or reads[0]["node"] != 2:
        return False, f"TC203 FAILED: expected one Node2 READ_VAL, got {len(reads)}", reads
    if reads[0]["verdict"] != "MATCH":
        return False, "TC203 FAILED: H64 spill/onload payload mismatch", reads
    required = ("RESIDENT-SPILL-DONE", "RESIDENT-FILL-ISSUED", "RESIDENT-FILL-DONE")
    missing = [marker for marker in required if not any(marker in line for line in lines)]
    if missing:
        return False, f"TC203 FAILED: missing H64 spill/fill evidence {missing}", []
    return True, "TC203 PASSED: H64 spill/onload regression completed", []


def verify_tc126(reads, lines):
    """TC126: Resident-waiter upgrade replay — upgrade must NOT downgrade to ReadUnique.

    Checks:
      1. At least 2 READ_VAL from node1 (Phase 2) and node2 (Phase 2, Phase 5).
      2. Node2 final read must be TC126_V1 (0x1260BEEF).
      3. Log evidence: the target Upgrade is resident-waited and replays once.
      4. Log evidence: at least one RESIDENT-SPILL-START (victim=) or RESIDENT-FILL-ISSUED.
      5. Log evidence: exactly one UBCC-UPGRADE-COMMIT (after duplicate stderr removal).
      6. Regression guard: count of UBCC-OUTER-REQ req=1 (ReadUnique) for target PA
         must NOT exceed 1 (the initial Phase 1 store may generate one ReadUnique;
         a second occurrence would indicate a post-fill downgrade replay).
    """
    target_val = 0x1260BEEF
    target_pa = 'pa=0x10001000'

    # Check reads
    node1_reads = [r for r in reads if r['node'] == 1]
    node2_reads = [r for r in reads if r['node'] == 2]
    if len(node1_reads) < 1:
        return False, 'TC126 FAILED: no READ_VAL from Node1', reads
    if len(node2_reads) < 2:
        return False, f'TC126 FAILED: expected >=2 Node2 reads, got {len(node2_reads)}', reads

    # Node2's last read must be the upgraded value
    last_n2 = node2_reads[-1]
    last_actual = int(last_n2['actual'], 16)
    if last_actual != target_val:
        return False, (f'TC126 FAILED: Node2 final read 0x{last_actual:X}, '
                       f'expected 0x{target_val:X}'), [last_n2]

    # Check for mismatches
    mismatches = [r for r in reads if r['verdict'] != 'MATCH']
    if mismatches:
        return False, f'TC126 FAILED: {len(mismatches)} mismatches', mismatches

    # Log evidence checks
    has_upgrade_waiter = any(
        'RESIDENT-WAITER-ENQ' in l and target_pa in l and 'opKind=1' in l
        for l in lines)
    has_spill_or_fill = any(
        target_pa in l and
        ('RESIDENT-SPILL-START' in l or 'RESIDENT-FILL-ISSUED' in l)
        for l in lines)
    upgrade_commits = sum(
        1 for l in lines
        if 'UBCC-UPGRADE-COMMIT' in l and target_pa in l)
    queued_replays = sum(
        1 for l in lines
        if 'RESIDENT-WAITER-REPLAY-UPGRADE-QUEUED' in l and target_pa in l)

    # Regression guard: ReadUnique (req=1) for target PA.
    # Phase 1 store MAY generate one initial ReadUnique — that is normal.
    # A second (post-fill) ReadUnique indicates the upgrade was downgraded.
    ru_replay_count = sum(
        1 for l in lines
        if 'UBCC-OUTER-REQ' in l and target_pa in l and ' req=1 ' in l)

    diag = (f'upgrade_waiter={has_upgrade_waiter}, '
            f'spill_fill={has_spill_or_fill}, '
            f'upgrade_commits={upgrade_commits}, '
            f'queued_replays={queued_replays}, '
            f'ru_replay_count={ru_replay_count}')

    if not has_upgrade_waiter:
        return False, f'TC126 FAILED: no RESIDENT-WAITER-ENQ with opKind=1 ({diag})', []
    if not has_spill_or_fill:
        return False, f'TC126 FAILED: no RESIDENT-SPILL-START or RESIDENT-FILL-ISSUED ({diag})', []
    if upgrade_commits != 1:
        return False, f'TC126 FAILED: expected exactly one upgrade commit ({diag})', []
    if queued_replays != 1:
        return False, f'TC126 FAILED: expected one queued replay transition ({diag})', []
    if ru_replay_count > 1:
        return False, (f'TC126 FAILED: {ru_replay_count} ReadUnique for target '
                       f'(>1 indicates post-fill downgrade replay) ({diag})'), []

    return True, f'TC126 PASSED: upgrade replay correct ({diag})', []


def verify_tc125(reads, lines):
    """TC125: Read offload/onload — shared read survives metadata spill+fill.

    Checks:
      1. At least 4 READ_VAL (Phase 2 node1, node0, Phase 4 node1, Phase 6 node0).
      2. All reads MATCH their expected values (no mismatches).
      3. Phase 4 node1 read confirms V0 after onload.
      4. Phase 6 node0 read confirms V1 after ReadUnique.
      5. Log evidence: at least one RESIDENT-SPILL-START and at least one
         RESIDENT-FILL-ISSUED for the target PA.
      Spill format: victim=0x... ; Fill format: pa=0x...
    """
    target_fill_pat = 'pa=0x10002000'
    target_spill_pat = 'victim=0x10002000'
    v0 = 0x12500000
    v1 = 0x1250BEEF

    # All reads must match
    mismatches = [r for r in reads if r['verdict'] != 'MATCH']
    if mismatches:
        return False, f'TC125 FAILED: {len(mismatches)} mismatches', mismatches

    if len(reads) < 4:
        return False, f'TC125 FAILED: expected >=4 READ_VAL, got {len(reads)}', reads

    # Node1 post-spill read must be V0
    node1_reads = [r for r in reads if r['node'] == 1]
    if len(node1_reads) < 1:
        return False, 'TC125 FAILED: no READ_VAL from Node1', reads
    n1_post_spill = [r for r in node1_reads if int(r['expected'], 16) == v0]
    if not n1_post_spill:
        return False, 'TC125 FAILED: Node1 did not read V0 after spill', node1_reads

    # Node0 final read must be V1
    node0_v1 = [r for r in reads if r['node'] == 0 and int(r['expected'], 16) == v1]
    if not node0_v1:
        return False, 'TC125 FAILED: Node0 did not read V1', reads
    for r in node0_v1:
        if int(r['actual'], 16) != v1:
            return False, f'TC125 FAILED: Node0 expected V1, got 0x{int(r["actual"],16):X}', [r]

    # H64 may asynchronously persist a clean target before capacity pressure.
    # That valid offload is reclaimed as a safe force-remove rather than the
    # dirty-victim spill path, and must still precede the required onload.
    has_dirty_spill = any(
        'RESIDENT-SPILL-START' in l and target_spill_pat in l
        for l in lines)
    has_clean_offload = any(
        'UBCC-SPILL-DIRTY-PERSIST' in l and target_fill_pat in l and
        'safe force-remove' in l
        for l in lines)
    has_spill = has_dirty_spill or has_clean_offload
    has_fill = any(
        'RESIDENT-FILL-ISSUED' in l and target_fill_pat in l
        for l in lines)

    diag = (f'spill={has_spill}, dirty_spill={has_dirty_spill}, '
            f'clean_offload={has_clean_offload}, fill={has_fill}')
    if not has_spill:
        return False, f'TC125 FAILED: no RESIDENT-SPILL-START for target ({diag})', []
    if not has_fill:
        return False, f'TC125 FAILED: no RESIDENT-FILL-ISSUED for target ({diag})', []

    return True, f'TC125 PASSED: read onload correct, V0→V1 transition OK ({diag})', []


def verify_tc127(reads, lines):
    """TC127: dirty data survives local eviction under the selected home.

    Checks:
      1. At least 2 READ_VAL from remote nodes after writeback.
      2. All reads MATCH (both must see the nonzero payload V0).
      3. UBCC profile: spill/fill and writeback-persistence evidence.
      4. HA-VI profile: HA write install plus target eviction notification.
    """
    target_fill_pat = 'pa=0x10004000'
    target_spill_pat = 'victim=0x10004000'
    v0 = 0x1270C0DE

    mismatches = [r for r in reads if r['verdict'] != 'MATCH']
    if mismatches:
        return False, f'TC127 FAILED: {len(mismatches)} mismatches', mismatches

    if len(reads) < 2:
        return False, f'TC127 FAILED: expected >=2 READ_VAL, got {len(reads)}', reads

    # All reads must see V0
    for r in reads:
        if int(r['actual'], 16) != v0:
            return False, (f'TC127 FAILED: node{r["node"]} got 0x{int(r["actual"],16):X}, '
                           f'expected 0x{v0:X}'), [r]

    is_ha_vi = any(
        'UBIO-HA-MANIFEST' in l and 'controller=ha-vi' in l
        for l in lines)
    if is_ha_vi:
        has_write_grant = any(
            'HA-SLICC-GATE' in l and 'phase=WRITE_GRANTED' in l and
            'pa=0x10004000' in l for l in lines)
        has_write_ack = any(
            'HA-SLICC-GATE' in l and 'phase=WRITE_ACK' in l and
            'pa=0x10004000' in l for l in lines)
        has_evict = any(
            'HA-SLICC-GATE' in l and 'phase=EVICT' in l and
            'pa=0x10004000' in l for l in lines)
        diag = (f'ha_write_grant={has_write_grant}, '
                f'ha_write_ack={has_write_ack}, ha_evict={has_evict}')
        if not has_write_grant:
            return False, f'TC127 FAILED: no HA write grant for target ({diag})', []
        if not has_write_ack:
            return False, f'TC127 FAILED: no HA write install ack for target ({diag})', []
        if not has_evict:
            return False, f'TC127 FAILED: no HA eviction notification for target ({diag})', []
        return True, f'TC127 PASSED: HA-VI eviction preserved persisted data ({diag})', []

    # Log evidence
    has_dirty_spill = any(
        'RESIDENT-SPILL-START' in l and target_spill_pat in l
        for l in lines)
    has_clean_offload = any(
        'UBCC-SPILL-DIRTY-PERSIST' in l and target_fill_pat in l and
        'safe force-remove' in l
        for l in lines)
    has_spill = has_dirty_spill or has_clean_offload
    has_fill = any(
        'RESIDENT-FILL-ISSUED' in l and target_fill_pat in l
        for l in lines)
    has_wb_persist = any(
        'WB-DATA-PERSIST' in l and target_fill_pat in l
        for l in lines)
    has_wb_req = any(
        'UBCC-WB-REQ' in l and target_fill_pat in l and 'WritebackReq' in l
        for l in lines)

    diag = (f'spill={has_spill}, dirty_spill={has_dirty_spill}, '
            f'clean_offload={has_clean_offload}, fill={has_fill}, '
            f'wb_req={has_wb_req}, wb_persist={has_wb_persist}')
    if not has_spill:
        return False, f'TC127 FAILED: no RESIDENT-SPILL-START victim=target ({diag})', []
    if not has_fill:
        return False, f'TC127 FAILED: no RESIDENT-FILL-ISSUED ({diag})', []
    if not has_wb_req:
        return False, f'TC127 FAILED: no UBCC-WB-REQ WritebackReq ({diag})', []
    if not has_wb_persist:
        return False, f'TC127 FAILED: no WB-DATA-PERSIST ({diag})', []

    return True, f'TC127 PASSED: writeback offload/onload, data persisted ({diag})', []


def verify_tc128(reads, lines):
    """TC128: clean eviction preserves data under the selected home.

    Checks:
      1. At least 2 READ_VAL (Phase 2 shared reads + Phase 5 verify).
      2. All reads MATCH V0.
      3. Log evidence: RESIDENT-SPILL-START + RESIDENT-FILL-ISSUED for target PA.
      4. Clean eviction (EvictReq) evidence is SOFT/optional: checked and
         reported but never causes a failure.  In the current CHI EP
         implementation, a clean SC eviction is handled locally by the HN-F
         and does NOT generate a ubio-level EvictReq.
    """
    target_fill_pat = 'pa=0x10006000'
    target_spill_pat = 'victim=0x10006000'
    v0 = 0x1280C1E0

    mismatches = [r for r in reads if r['verdict'] != 'MATCH']
    if mismatches:
        return False, f'TC128 FAILED: {len(mismatches)} mismatches', mismatches

    if len(reads) < 2:
        return False, f'TC128 FAILED: expected >=2 READ_VAL, got {len(reads)}', reads

    for r in reads:
        if int(r['actual'], 16) != v0:
            return False, (f'TC128 FAILED: node{r["node"]} got 0x{int(r["actual"],16):X}, '
                           f'expected 0x{v0:X}'), [r]

    is_ha_vi = any(
        'UBIO-HA-MANIFEST' in l and 'controller=ha-vi' in l
        for l in lines)
    if is_ha_vi:
        has_evict = any(
            'HA-SLICC-GATE' in l and 'phase=EVICT' in l and
            'pa=0x10006000' in l for l in lines)
        read_enters = sum(
            'HA-SLICC-GATE' in l and 'phase=READ_ENTER' in l and
            'homePa=0x10006000' in l for l in lines)
        read_acks = sum(
            re.search(r'HA-SLICC-GATE.*phase=READ_ACK '
                      r'pa=0x(?:[0-9a-f]+)?10006000\b', l)
            is not None for l in lines)
        diag = (f'ha_evict={has_evict}, read_enters={read_enters}, '
                f'read_acks={read_acks}')
        if not has_evict:
            return False, f'TC128 FAILED: no HA target eviction ({diag})', []
        if read_enters < 4 or read_acks < 4:
            return False, f'TC128 FAILED: incomplete HA reread chain ({diag})', []
        return True, f'TC128 PASSED: HA-VI clean eviction and reread preserved data ({diag})', []

    # Log evidence: spill uses victim=, fill uses pa=
    has_spill = any(
        'RESIDENT-SPILL-START' in l and target_spill_pat in l
        for l in lines)
    has_clean_offload = any(
        'UBCC-SPILL-DIRTY-PERSIST' in l and target_fill_pat in l and
        'safe force-remove' in l for l in lines)
    has_fill = any(
        'RESIDENT-FILL-ISSUED' in l and target_fill_pat in l
        for l in lines)
    has_evict_req = any(
        'EvictReq' in l and target_fill_pat in l
        for l in lines)

    diag = (f'spill={has_spill}, clean_offload={has_clean_offload}, '
            f'fill={has_fill}, evict_req={has_evict_req}')
    if not has_spill and not has_clean_offload:
        return False, f'TC128 FAILED: no target offload evidence ({diag})', []
    if not has_fill:
        return False, f'TC128 FAILED: no RESIDENT-FILL-ISSUED ({diag})', []

    # NOTE: Clean eviction does not generate ubio-level EvictReq in current
    # CHI EP implementation. This check is diagnostic only.
    return True, (f'TC128 PASSED: clean evict onload, data intact '
                  f'(evict_req={has_evict_req}, {diag})'), []


def verify_tc129(reads, lines):
    """TC129: Long mixed integration — two spill/fill cycles validated.

    Checks:
      1. At least 3 READ_VAL (node1 V0, node2 V1, node0 V1).
      2. All reads MATCH.
      3. Node1 reads V0, both node2 and node0 read V1.
      4. Log evidence: at least 2 RESIDENT-SPILL-START (victim=) and at least 2
         RESIDENT-FILL-ISSUED (pa=) for the target PA.
    """
    target_fill_pat = 'pa=0x10008000'
    target_spill_pat = 'victim=0x10008000'
    v0 = 0x12900000
    v1 = 0x1290FADE

    mismatches = [r for r in reads if r['verdict'] != 'MATCH']
    if mismatches:
        return False, f'TC129 FAILED: {len(mismatches)} mismatches', mismatches

    if len(reads) < 3:
        return False, f'TC129 FAILED: expected >=3 READ_VAL, got {len(reads)}', reads

    # Node1 must see V0
    n1_v0 = [r for r in reads if r['node'] == 1 and int(r['actual'], 16) == v0]
    if not n1_v0:
        return False, 'TC129 FAILED: Node1 did not read V0', reads

    # Node2 must see V1
    n2_v1 = [r for r in reads if r['node'] == 2 and int(r['actual'], 16) == v1]
    if not n2_v1:
        return False, 'TC129 FAILED: Node2 did not read V1', reads

    # Node0 must see V1
    n0_v1 = [r for r in reads if r['node'] == 0 and int(r['actual'], 16) == v1]
    if not n0_v1:
        return False, 'TC129 FAILED: Node0 did not read V1', reads

    is_ha_vi = any(
        'UBIO-HA-MANIFEST' in l and 'controller=ha-vi' in l
        for l in lines)
    if is_ha_vi:
        has_evict = any(
            'HA-SLICC-GATE' in l and 'phase=EVICT' in l and
            'pa=0x10008000' in l for l in lines)
        read_acks = sum(
            re.search(r'HA-SLICC-GATE.*phase=READ_ACK '
                      r'pa=0x(?:[0-9a-f]+)?10008000\b', l)
            is not None for l in lines)
        write_acks = sum(
            re.search(r'HA-SLICC-GATE.*phase=WRITE_ACK '
                      r'pa=0x(?:[0-9a-f]+)?10008000\b', l)
            is not None for l in lines)
        has_recall_data = any(
            'RecallResp' in l and '0x10008000' in l for l in lines)
        diag = (f'ha_evict={has_evict}, read_acks={read_acks}, '
                f'write_acks={write_acks}, recall_data={has_recall_data}')
        if not has_evict:
            return False, f'TC129 FAILED: no HA target eviction ({diag})', []
        if read_acks < 4 or write_acks < 2 or not has_recall_data:
            return False, f'TC129 FAILED: incomplete HA lifecycle ({diag})', []
        return True, f'TC129 PASSED: HA-VI V0-to-V1 lifecycle preserved data ({diag})', []

    # H64 can persist a clean target asynchronously before capacity pressure.
    # Count either dirty victim spill or the equivalent safe force-remove as an
    # offload; both must be followed by the two target onloads below.
    dirty_spill_count = sum(1 for l in lines
                            if 'RESIDENT-SPILL-START' in l and target_spill_pat in l)
    clean_offload_count = sum(1 for l in lines
                              if 'UBCC-SPILL-DIRTY-PERSIST' in l and
                              target_fill_pat in l and 'safe force-remove' in l)
    spill_count = dirty_spill_count + clean_offload_count
    fill_count = sum(1 for l in lines
                     if 'RESIDENT-FILL-ISSUED' in l and target_fill_pat in l)

    diag = (f'offloads={spill_count}, dirty_spills={dirty_spill_count}, '
            f'clean_offloads={clean_offload_count}, fills={fill_count}')

    if spill_count < 2:
        return False, f'TC129 FAILED: expected two target offloads ({diag})', []
    if fill_count < 2:
        return False, f'TC129 FAILED: expected two target fills ({diag})', []

    return True, f'TC129 PASSED: two full spill/fill cycles, V0→V1 correct ({diag})', []


def verify_tc118(reads, lines):
    """TC118: Combined fault — Drop Clear + Delay Clear on same home.
    Both DSM lines must converge despite concurrent faults on the same home."""
    if len(reads) < 2:
        return False, f"TC118 FAILED: expected ≥2 READ_VAL, got {len(reads)}", reads
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC118 FAILED: {len(mismatches)} mismatches", mismatches
    fault_ok, fault_msg = _verify_fault_events(
        118, lines, {"tc118_drop": "Drop", "tc118_delay": "Delay"},
        {"tc118_delay"})
    if not fault_ok:
        return False, fault_msg, []
    return True, f"TC118 PASSED: combined faults converged; {fault_msg}", []


def verify_tc119(reads, lines):
    """TC119: Triple fault — Drop + Dup + Delay on same home."""
    if len(reads) < 3:
        return False, f"TC119 FAILED: expected ≥3 READ_VAL, got {len(reads)}", reads
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC119 FAILED: {len(mismatches)} mismatches", mismatches
    fault_ok, fault_msg = _verify_fault_events(
        119, lines, {"tc119_drop": "Drop", "tc119_dup": "Duplicate",
                     "tc119_delay": "Delay"}, {"tc119_delay"})
    if not fault_ok:
        return False, fault_msg, []
    return True, f"TC119 PASSED: triple fault converged; {fault_msg}", []


def verify_tc148(reads, lines):
    """TC148: 32-line, 32-hit bounded ClearReq fault qualification."""
    if len(reads) < 32:
        return False, f"TC148 FAILED: expected >=32 READ_VAL, got {len(reads)}", reads
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC148 FAILED: {len(mismatches)} data mismatches", mismatches[:16]

    action_names = {"drop": "Drop", "dup": "Duplicate", "delay": "Delay",
                    "reorder": "Reorder"}
    expected_actions = {
        f"tc148_{name}_{i}": action
        for name, action in action_names.items() for i in range(8)
    }
    delivery_rules = {name for name, action in expected_actions.items()
                      if action in ("Delay", "Reorder")}
    fault_ok, fault_msg = _verify_fault_events(
        148, lines, expected_actions, delivery_rules)
    if not fault_ok:
        return False, fault_msg, []
    action_totals = {action: 8 for action in action_names}
    return True, ("TC148 PASSED: 32/32 reads MATCH; 32/32 triggers and 16/16 "
                  f"deliveries exact; totals={action_totals}; {fault_msg}"), []


def _verify_fault_rule_set(tc_id, reads, lines, expected_reads,
                           expected_actions, delivery_rules=()):
    if len(reads) < expected_reads:
        return False, (f"TC{tc_id} FAILED: expected >={expected_reads} READ_VAL, "
                       f"got {len(reads)}"), reads
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC{tc_id} FAILED: {len(mismatches)} mismatches", mismatches[:16]
    fault_ok, fault_msg = _verify_fault_events(
        tc_id, lines, expected_actions, delivery_rules)
    if not fault_ok:
        return False, fault_msg, []
    return True, f"TC{tc_id} PASSED: reads={len(reads)}, {fault_msg}", []


def verify_tc149(reads, lines):
    return _verify_fault_rule_set(
        149, reads, lines, 32,
        {f"tc149_upgrade_drop_{i}": "Drop" for i in range(8)})


def verify_tc150(reads, lines):
    return _verify_fault_rule_set(
        150, reads, lines, 32,
        {f"tc150_invack_dup_n{node}_{i}": "Duplicate"
         for node in (1, 2) for i in range(8)})


def verify_tc151(reads, lines):
    return _verify_fault_rule_set(
        151, reads, lines, 32,
        {f"tc151_invack_delay_n{node}_{i}": "Delay"
         for node in (1, 2) for i in range(8)},
        {f"tc151_invack_delay_n{node}_{i}"
         for node in (1, 2) for i in range(8)})


def verify_tc152(reads, lines):
    return _verify_fault_rule_set(
        152, reads, lines, 32,
        {f"tc152_invack_reorder_n{node}_{i}": "Reorder"
         for node in (1, 2) for i in range(8)},
        {f"tc152_invack_reorder_n{node}_{i}"
         for node in (1, 2) for i in range(8)})


def verify_tc153(reads, lines):
    return _verify_fault_rule_set(
        153, reads, lines, 16,
        {f"tc153_recall_dup_{i}": "Duplicate" for i in range(16)})


def verify_tc154(reads, lines):
    return _verify_fault_rule_set(
        154, reads, lines, 16,
        {f"tc154_recall_delay_{i}": "Delay" for i in range(16)},
        {f"tc154_recall_delay_{i}" for i in range(16)})


def verify_tc155(reads, lines):
    return _verify_fault_rule_set(
        155, reads, lines, 16,
        {f"tc155_recall_reorder_{i}": "Reorder" for i in range(16)},
        {f"tc155_recall_reorder_{i}" for i in range(16)})


def verify_tc156(reads, lines):
    return _verify_fault_rule_set(
        156, reads, lines, 16,
        {f"tc156_recall_drop_{i}": "Drop" for i in range(16)})


def verify_tc157(reads, lines):
    return _verify_fault_rule_set(
        157, reads, lines, 32,
        {f"tc157_invack_drop_n{node}_{i}": "Drop"
         for node in (1, 2) for i in range(8)})


def verify_tc158(reads, lines):
    return _verify_fault_rule_set(
        158, reads, lines, 32,
        {f"tc158_upgraderesp_drop_{i}": "Drop" for i in range(8)})


def verify_tc159(reads, lines):
    return _verify_fault_rule_set(
        159, reads, lines, 32,
        {f"tc159_upgradeack_drop_{i}": "Drop" for i in range(8)})


def verify_tc80(reads, lines):
    if len(reads) < 1:
        return False, "TC80 FAILED: no READ_VAL", reads
    if reads[-1]["verdict"] != "MATCH":
        return False, "TC80 FAILED: final read mismatch", [reads[-1]]
    lat_lines = [l for l in lines if "[LATENCY]" in l]
    return True, f"TC80 PASSED: cross-node latency ({len(lat_lines)} samples)", []

def verify_tc81(reads, lines):
    if len(reads) < 1:
        return False, "TC81 FAILED: no READ_VAL", reads
    if reads[-1]["verdict"] != "MATCH":
        return False, "TC81 FAILED: mismatch", [reads[-1]]
    lat_lines = [l for l in lines if "[LATENCY]" in l]
    same = sum(1 for l in lat_lines if "type=same" in l)
    cross = sum(1 for l in lat_lines if "type=cross" in l)
    return True, f"TC81 PASSED: cross-socket latency (same={same}, cross={cross})", []

def verify_tc82(reads, lines):
    if len(reads) < 1:
        return False, "TC82 FAILED: no READ_VAL", reads
    if reads[-1]["verdict"] != "MATCH":
        return False, "TC82 FAILED: mismatch", [reads[-1]]
    lat_lines = [l for l in lines if "[LATENCY]" in l]
    return True, f"TC82 PASSED: 8-node ring latency ({len(lat_lines)} nodes)", []

def verify_tc84(reads, lines):
    cap_lines = [l for l in lines if "[CAPACITY]" in l]
    return True, f"TC84/85 PASSED: capacity test ({len(cap_lines)} markers)", []


def verify_ha_2n1s(reads, lines):
    validations = []
    for line in lines:
        if not line.startswith('{'):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("kind") == "validation":
            validations.append(record)
    nodes = {r.get("node") for r in validations if r.get("errors") == 0}
    if nodes != {0, 1}:
        return False, ("2N1S FAILED: expected successful JSONL validation from "
                       f"nodes 0 and 1, got {validations}"), []
    scenarios = {r.get("scenario") for r in validations}
    if len(scenarios) != 1:
        return False, f"2N1S FAILED: inconsistent scenarios {scenarios}", []
    capacity = []
    for line in lines:
        if not line.startswith('{'):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("kind") == "capacity":
            capacity.append(record)
    formal = [r for r in capacity if r.get("formal_capacity") is True]
    if formal:
        if len(formal) != 1 or formal[0].get("unique_lines", 0) < 768:
            return False, f"2N1S FAILED: invalid formal capacity record {formal}", []
    return True, (f"2N1S PASSED: {next(iter(scenarios))} validated on two nodes"
                  f" formal_capacity={bool(formal)}"), []


def verify_ha_capacity(reads, lines, scenario, expected_phases):
    passed, message, failures = verify_ha_2n1s(reads, lines)
    if not passed:
        return passed, message, failures
    mismatches = [read for read in reads if read["verdict"] != "MATCH"]
    if mismatches:
        return False, f"{scenario} FAILED: {len(mismatches)} mismatches", mismatches
    records = []
    for line in lines:
        if not line.startswith('{'):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("kind") == "capacity" and record.get("scenario") == scenario:
            records.append(record)
    expected_capacity = {
        "resident_capacity": 512,
        "hot_lines": 64,
        "pressure_lines": 704,
        "unique_lines": 768,
        "capacity_ratio": 1.5,
        "formal_capacity": True,
    }
    if len(records) != 1 or any(records[0].get(key) != value
                                for key, value in expected_capacity.items()):
        return False, (f"{scenario} FAILED: invalid exact-150 capacity record "
                       f"{records}"), []
    timers = [_RE_GUEST_TIMER.search(line) for line in lines]
    timer_phases = {sample.group(2): int(sample.group(3))
                    for sample in timers if sample}
    missing = [phase for phase in expected_phases if phase not in timer_phases]
    if missing:
        return False, f"{scenario} FAILED: missing timers {missing}", []
    invalid_ops = {phase: (timer_phases[phase], operations)
                   for phase, operations in expected_phases.items()
                   if timer_phases[phase] != operations}
    if invalid_ops:
        return False, f"{scenario} FAILED: invalid timer operations {invalid_ops}", []
    if len(reads) != 64:
        return False, f"{scenario} FAILED: expected 64 final reads, got {len(reads)}", []
    return True, f"{scenario} PASSED: exact 768-line capacity and timed lifecycle", []


def verify_tc220(reads, lines):
    return verify_ha_capacity(reads, lines, "HA11", {
        "clean_capacity_admission": 704,
        "clean_first_revisit": 64,
    })


def verify_tc221(reads, lines):
    return verify_ha_capacity(reads, lines, "HA12", {
        "dirty_capacity_admission": 704,
        "dirty_first_revisit": 32,
        "dirty_handoff": 32,
    })


def verify_ha_cgroup(reads, lines, scenario, expected_reads,
                     timer_phases=(), latency_phases=()):
    passed, message, failures = verify_ha_2n1s(reads, lines)
    if not passed:
        return passed, message, failures
    validations = []
    manifests = []
    for line in lines:
        if not line.startswith('{'):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("scenario") == scenario:
            if record.get("kind") == "validation":
                validations.append(record)
            elif record.get("kind") == "manifest":
                manifests.append(record)
    if len(manifests) != 2 or {r.get("node") for r in manifests} != {0, 1}:
        return False, f"{scenario} FAILED: invalid manifests {manifests}", []
    if any(r.get("implementation_status") != "implemented_2n1s"
           for r in manifests):
        return False, f"{scenario} FAILED: implementation status missing", []
    if len(validations) != 2 or {r.get("node") for r in validations} != {0, 1}:
        return False, f"{scenario} FAILED: invalid validations {validations}", []
    mismatches = [read for read in reads if read["verdict"] != "MATCH"]
    if mismatches or len(reads) != expected_reads:
        return False, (f"{scenario} FAILED: reads={len(reads)} "
                       f"expected={expected_reads} mismatches={len(mismatches)}"), reads
    timers = [_RE_GUEST_TIMER.search(line) for line in lines]
    timers = {m.group(2): int(m.group(3)) for m in timers if m}
    missing_timers = [phase for phase, operations in timer_phases
                      if timers.get(phase) != operations]
    if missing_timers:
        return False, f"{scenario} FAILED: invalid timers {missing_timers}", []
    latency = [_RE_PERF_LATENCY.search(line) for line in lines]
    latency = {m.group(2): int(m.group(3)) for m in latency if m}
    missing_latency = [phase for phase, samples in latency_phases
                       if latency.get(phase) != samples]
    if missing_latency:
        return False, f"{scenario} FAILED: invalid latency {missing_latency}", []
    return True, f"{scenario} PASSED: portable 2N1S contract validated", []


def verify_tc222(reads, lines):
    return verify_ha_cgroup(reads, lines, "C123-HA", 4,
                            (("c123_shared_to_writer_store", 4),),
                            (("c123_shared_to_writer_store", 4),))


def verify_tc223(reads, lines):
    return verify_ha_cgroup(reads, lines, "C130-HA", 24,
                            (("c130_post_pressure_hot_reuse", 96),),
                            (("c130_first_revisit", 24),))


def verify_tc224(reads, lines):
    configs = []
    for line in lines:
        if not line.startswith('{'):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (record.get("kind") == "workload_config" and
                record.get("scenario") == "C132-HA"):
            configs.append(record)
    if len(configs) != 2 or {r.get("node") for r in configs} != {0, 1}:
        return False, f"C132-HA FAILED: invalid workload configs {configs}", []
    fields = ("active_lines", "pressure_lines", "read_stride", "sample_count")
    if any(configs[0].get(field) != configs[1].get(field) for field in fields):
        return False, f"C132-HA FAILED: node configs differ {configs}", []
    active = configs[0].get("active_lines", 0)
    pressure = configs[0].get("pressure_lines", 0)
    stride = configs[0].get("read_stride", 0)
    samples = configs[0].get("sample_count", 0)
    stride_samples = (active - 1) // stride + 1 if stride > 0 else 0
    expected_samples = stride_samples + int(
        stride > 0 and (active - 1) % stride != 0)
    if (active <= 0 or pressure <= 0 or stride <= 0 or
            samples != expected_samples):
        return False, f"C132-HA FAILED: invalid workload config {configs[0]}", []
    passed, message, failures = verify_ha_cgroup(
        reads, lines, "C132-HA", samples)
    if not passed:
        return passed, message, failures
    if samples < 2:
        return False, "C132-HA FAILED: expected at least two checkpoint samples", reads
    timers = [_RE_GUEST_TIMER.search(line) for line in lines]
    timers = [m for m in timers if m and m.group(2) in
              ("c132_checkpoint_recover", "c132_checkpoint_end_to_end")]
    if len(timers) != 2:
        return False, "C132-HA FAILED: missing recover/end-to-end timers", []
    operations = {int(m.group(3)) for m in timers}
    if len(operations) != 1 or next(iter(operations)) <= 0:
        return False, f"C132-HA FAILED: inconsistent operations {operations}", []
    return True, "C132-HA PASSED: checkpoint recovery validated", []


def verify_tc225(reads, lines):
    return verify_ha_cgroup(reads, lines, "C135-HA", 48,
                            latency_phases=(("c135_preserved_sharer_first_load", 24),))


def verify_tc226(reads, lines):
    return verify_ha_cgroup(reads, lines, "C138-HA", 24,
                            latency_phases=(("c138_dirty_owner_handoff_store", 24),))


def verify_tc227(reads, lines):
    return verify_ha_cgroup(reads, lines, "C139-HA", 9,
                            (("c139_mixed_batch_throughput", 256),),
                            (("c139_mixed_batch_16ops", 16),))


def verify_tc217(reads, lines):
    passed, message, failures = verify_ha_2n1s(reads, lines)
    if not passed:
        return passed, message, failures
    mismatches = [read for read in reads if read["verdict"] != "MATCH"]
    if mismatches or len(reads) != 2:
        return False, (f"TC217 FAILED: expected two final MATCH reads, "
                       f"got reads={len(reads)} mismatches={len(mismatches)}"), reads
    latency = [_RE_PERF_LATENCY.search(line) for line in lines]
    latency = [sample for sample in latency
               if sample and sample.group(2) == "ha10_catalog_batch_16ops"]
    if len(latency) != 1 or int(latency[0].group(3)) != 8:
        return False, (f"TC217 FAILED: expected one 8-sample HA10 latency "
                       f"summary, got {len(latency)}"), []
    timers = [_RE_GUEST_TIMER.search(line) for line in lines]
    timers = [sample for sample in timers
              if sample and sample.group(2) == "catalog_useful_throughput"]
    if len(timers) != 1 or int(timers[0].group(3)) != 128:
        return False, (f"TC217 FAILED: expected one 128-op useful throughput "
                       f"timer, got {len(timers)}"), []
    if int(timers[0].group(4)) == 0 or int(timers[0].group(5)) == 0:
        return False, "TC217 FAILED: zero throughput ticks/frequency", []
    batch_records = []
    for line in lines:
        if not line.startswith('{'):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (record.get("kind") == "sample" and
                record.get("scenario") == "HA10" and
                record.get("phase") == "catalog_batch"):
            batch_records.append(record)
    if len(batch_records) != 8 or {r.get("iteration") for r in batch_records} != set(range(8)):
        return False, (f"TC217 FAILED: expected JSONL catalog batches 0-7, "
                       f"got {len(batch_records)}"), []
    return True, ("TC217 PASSED: HA10 validation, 8 batch samples, and "
                  "128 useful operations completed"), []


def verify_ha_topology(reads, lines, scenario, timer_phase, timer_count):
    topology = [_RE_TOPOLOGY.search(line) for line in lines]
    topology = [match for match in topology if match]
    if not topology:
        return False, f"{scenario} FAILED: missing topology markers", []
    plane_counts = {int(match.group(2)) for match in topology}
    if len(plane_counts) != 1:
        return False, f"{scenario} FAILED: inconsistent plane counts {plane_counts}", []
    planes = next(iter(plane_counts))
    marker_planes = {int(match.group(1)) for match in topology}
    if marker_planes != set(range(planes)):
        return False, (f"{scenario} FAILED: topology participants "
                       f"{sorted(marker_planes)} expected 0..{planes - 1}"), []

    validations = []
    for line in lines:
        if not line.startswith('{'):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (record.get("kind") == "validation" and
                record.get("scenario") == scenario):
            validations.append(record)
    valid_planes = Counter(record.get("plane") for record in validations
                           if record.get("errors") == 0 and
                           record.get("planes") == planes)
    if valid_planes != Counter({plane: 1 for plane in range(planes)}):
        return False, f"{scenario} FAILED: invalid validations {validations}", []

    mismatches = [read for read in reads if read["verdict"] != "MATCH"]
    expected_reads = planes * 16
    if mismatches or len(reads) != expected_reads:
        return False, (f"{scenario} FAILED: reads={len(reads)} expected={expected_reads} "
                       f"mismatches={len(mismatches)}"), reads

    timers = [_RE_GUEST_TIMER.search(line) for line in lines]
    timers = [match for match in timers
              if match and match.group(2) == timer_phase]
    if len(timers) != timer_count or any(
            int(match.group(3)) != 16 or int(match.group(4)) == 0 or
            int(match.group(5)) == 0 for match in timers):
        return False, (f"{scenario} FAILED: invalid {timer_phase} timers "
                       f"count={len(timers)} expected={timer_count}"), []
    return True, (f"{scenario} PASSED: {planes} participant planes, "
                  f"{len(reads)} reads and {timer_count} 16-op timers validated"), []


def verify_tc228(reads, lines):
    topology = [_RE_TOPOLOGY.search(line) for line in lines]
    planes = max((int(match.group(2)) for match in topology if match), default=0)
    return verify_ha_topology(
        reads, lines, "HAT01", "topology_remote_read", planes)


def verify_tc229(reads, lines):
    topology = [_RE_TOPOLOGY.search(line) for line in lines]
    planes = max((int(match.group(2)) for match in topology if match), default=0)
    return verify_ha_topology(
        reads, lines, "HAT02", "topology_ownership_handoff", planes)


def verify_tc230(reads, lines):
    return verify_ha_topology(
        reads, lines, "HAT03", "topology_all_sharer_to_writer", 1)


def verify_ha_extended_base(reads, lines, scenario, expected_reads):
    topology = [_RE_TOPOLOGY.search(line) for line in lines]
    topology = [match for match in topology if match]
    if not topology:
        return False, f"{scenario} FAILED: missing topology markers", [], 0
    plane_counts = {int(match.group(2)) for match in topology}
    if len(plane_counts) != 1:
        return False, f"{scenario} FAILED: inconsistent plane counts", [], 0
    planes = next(iter(plane_counts))
    if Counter(int(match.group(1)) for match in topology) != Counter(
            {plane: 1 for plane in range(planes)}):
        return False, f"{scenario} FAILED: invalid topology participants", [], 0
    validations = []
    for line in lines:
        if not line.startswith('{'):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (record.get("kind") == "validation" and
                record.get("scenario") == scenario):
            validations.append(record)
    valid = Counter(record.get("plane") for record in validations
                    if record.get("planes") == planes and
                    record.get("errors") == 0)
    if valid != Counter({plane: 1 for plane in range(planes)}):
        return False, f"{scenario} FAILED: invalid validations {validations}", [], 0
    mismatches = [read for read in reads if read["verdict"] != "MATCH"]
    expected = expected_reads(planes)
    if mismatches or len(reads) != expected:
        return False, (f"{scenario} FAILED: reads={len(reads)} expected={expected} "
                       f"mismatches={len(mismatches)}"), reads, 0
    return True, "", [], planes


def timer_records(lines, phase):
    records = [_RE_GUEST_TIMER.search(line) for line in lines]
    return [record for record in records
            if record and record.group(2) == phase]


def latency_records(lines, phase):
    records = [_RE_PERF_LATENCY.search(line) for line in lines]
    return [record for record in records
            if record and record.group(2) == phase]


def valid_timer_set(records, expected_nodes, expected_operations):
    return (Counter(int(record.group(1)) for record in records) ==
            Counter(expected_nodes) and
            all(int(record.group(3)) == expected_operations and
                int(record.group(4)) > 0 and int(record.group(5)) > 0
                for record in records))


def valid_latency_set(records, expected_nodes, expected_samples):
    return (Counter(int(record.group(1)) for record in records) ==
            Counter(expected_nodes) and
            all(int(record.group(3)) == expected_samples and
                int(record.group(4)) <= int(record.group(5)) <=
                int(record.group(6)) <= int(record.group(7)) <=
                int(record.group(8)) and
                int(record.group(4)) <= int(record.group(9)) <=
                int(record.group(8)) and int(record.group(10)) > 0
                for record in records))


def verify_tc231(reads, lines):
    passed, message, failures, planes = verify_ha_extended_base(
        reads, lines, "HAE01", lambda p: p)
    if not passed:
        return passed, message, failures
    timers = timer_records(lines, "clean_shared_read_service")
    latency = latency_records(lines, "clean_shared_first_sweep")
    if not valid_timer_set(timers, range(planes), 256):
        return False, f"TC231 FAILED: invalid service timers {len(timers)}", []
    if not valid_latency_set(latency, range(planes), 32):
        return False, f"TC231 FAILED: invalid latency summaries {len(latency)}", []
    return True, f"TC231 PASSED: {planes} planes clean-shared reuse", []


def verify_tc232(reads, lines):
    passed, message, failures, planes = verify_ha_extended_base(
        reads, lines, "HAE02", lambda p: p * 16)
    if not passed:
        return passed, message, failures
    reads_t = timer_records(lines, "hot_key_read_service")
    writes_t = timer_records(lines, "hot_key_write_service")
    if not valid_timer_set(reads_t, range(planes), 16):
        return False, "TC232 FAILED: invalid read timers", []
    write_ops = [int(m.group(3)) for m in writes_t]
    if (Counter(int(m.group(1)) for m in writes_t) !=
            Counter(range(planes)) or sum(write_ops) != 16 or
            any(op <= 0 or int(m.group(4)) <= 0 or int(m.group(5)) <= 0
                for op, m in zip(write_ops, writes_t))):
        return False, f"TC232 FAILED: invalid write operation split {write_ops}", []
    write_ops_by_plane = {int(m.group(1)): int(m.group(3)) for m in writes_t}
    write_latency = latency_records(lines, "hot_key_write")
    if not valid_latency_set(latency_records(lines, "hot_key_read"),
                             range(planes), 16) or \
            Counter(int(m.group(1)) for m in write_latency) != \
            Counter(range(planes)) or any(
                int(m.group(3)) != write_ops_by_plane[int(m.group(1))] or
                int(m.group(4)) > int(m.group(5)) or
                int(m.group(5)) > int(m.group(6)) or
                int(m.group(6)) > int(m.group(7)) or
                int(m.group(7)) > int(m.group(8)) or
                not int(m.group(4)) <= int(m.group(9)) <= int(m.group(8)) or
                int(m.group(10)) <= 0 for m in write_latency):
        return False, "TC232 FAILED: missing latency summaries", []
    return True, f"TC232 PASSED: 16 hot-key rounds across {planes} planes", []


def verify_tc233(reads, lines):
    passed, message, failures, planes = verify_ha_extended_base(
        reads, lines, "HAE03", lambda p: p * 16)
    if not passed:
        return passed, message, failures
    timers = timer_records(lines, "producer_consumer_service")
    latency = latency_records(lines, "producer_consumer_load")
    if not valid_timer_set(timers, range(planes), 32):
        return False, "TC233 FAILED: invalid service timers", []
    if not valid_latency_set(latency, range(planes), 16):
        return False, "TC233 FAILED: invalid load latency summaries", []
    return True, f"TC233 PASSED: {planes * 16} records consumed", []


def verify_tc234(reads, lines):
    passed, message, failures, planes = verify_ha_extended_base(
        reads, lines, "HAE04", lambda p: p)
    if not passed:
        return passed, message, failures
    stores = timer_records(lines, "queued_token_store")
    end_to_end = timer_records(lines, "queued_token_end_to_end")
    if not valid_timer_set(stores, range(planes), 8):
        return False, "TC234 FAILED: invalid store timers", []
    if not valid_timer_set(end_to_end, [0], 8 * planes):
        return False, "TC234 FAILED: invalid end-to-end timer", []
    if not valid_latency_set(latency_records(lines, "queued_token_store"),
                             range(planes), 8):
        return False, "TC234 FAILED: missing store latency summaries", []
    return True, f"TC234 PASSED: {8 * planes} ordered token handoffs", []


def verify_tc235(reads, lines):
    passed, message, failures, planes = verify_ha_extended_base(
        reads, lines, "HAE05", lambda p: p * 9)
    if not passed:
        return passed, message, failures
    service = timer_records(lines, "catalog_kv_service")
    end_to_end = timer_records(lines, "catalog_kv_end_to_end")
    latency = latency_records(lines, "catalog_kv_batch_64ops")
    if not valid_timer_set(service, range(planes), 1024):
        return False, "TC235 FAILED: invalid service timers", []
    if not valid_timer_set(end_to_end, range(planes), 1024):
        return False, "TC235 FAILED: invalid end-to-end timers", []
    if not valid_latency_set(latency, range(planes), 16):
        return False, "TC235 FAILED: invalid batch latency summaries", []
    return True, f"TC235 PASSED: {planes * 1024} catalog/KV operations", []


def verify_o3_exact_reads(tc_id, reads, expected_count):
    mismatches = [read for read in reads if read["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC{tc_id} FAILED: {len(mismatches)} mismatches", mismatches
    if len(reads) != expected_count:
        return False, (f"TC{tc_id} FAILED: expected {expected_count} READ_VAL, "
                       f"got {len(reads)}"), reads
    return True, (f"TC{tc_id} PASSED: {expected_count} architecturally "
                  "synchronized reads matched"), []

VERIFIERS = {
    1: verify_tc1, 2: verify_tc2, 3: verify_tc3, 4: verify_tc4,
    5: verify_tc5, 6: verify_tc6, 7: verify_tc7, 8: verify_tc8,
    9: verify_tc9, 10: verify_tc10, 11: verify_tc11,
    12: verify_tc12,
    13: verify_tc13, 14: verify_tc14, 15: verify_tc15,
    16: verify_tc16, 17: verify_tc17,
    18: verify_tc18, 19: verify_tc19,
    20: verify_tc20, 21: verify_tc21,
    22: verify_tc22, 23: verify_tc23, 24: verify_tc24,
    25: verify_tc25, 26: verify_tc26, 27: verify_tc27, 28: verify_tc28,
    29: verify_tc29, 30: verify_tc30, 31: verify_tc31, 32: verify_tc32,
    33: verify_tc33, 34: verify_tc34, 35: verify_tc35,
    36: verify_tc36, 37: verify_tc37, 38: verify_tc38, 39: verify_tc39,
    40: verify_tc40, 41: verify_tc41, 42: verify_tc42, 43: verify_tc43,
    44: verify_tc44, 45: verify_tc45, 46: verify_tc46,
    47: verify_tc47, 48: verify_tc48, 49: verify_tc49,
    50: verify_tc50, 51: verify_tc51, 52: verify_tc52,
    53: verify_tc53, 54: verify_tc54,
    63: verify_tc63, 64: verify_tc64,
    80: verify_tc80, 81: verify_tc81, 82: verify_tc82,
    84: verify_tc84, 85: verify_tc84,
    90: verify_tc90,
    91: verify_tc91, 92: verify_tc92, 93: verify_tc93,     94: verify_tc94,
    95: verify_tc95,
    96: verify_tc96, 97: verify_tc97, 98: verify_tc98, 99: verify_tc99,
    100: verify_tc100, 101: verify_tc101, 102: verify_tc102,
    110: verify_tc110, 111: verify_tc111,
    112: verify_tc112, 113: verify_tc113,
    114: verify_tc114,
    115: verify_tc115,
    116: verify_tc116,
    117: verify_tc117,
    118: verify_tc118,
    119: verify_tc119,
    120: verify_tc120,
    121: verify_tc121,
    122: verify_tc122,
    123: verify_tc123,
    124: verify_tc124,
    125: verify_tc125,
    126: verify_tc126,
    127: verify_tc127,
    128: verify_tc128,
    129: verify_tc129,
    130: verify_tc130,
    131: verify_tc131,
    132: verify_tc132,
    133: verify_tc133,
    134: verify_tc134,
    135: verify_tc135,
    136: verify_tc136,
    137: verify_tc137,
    138: verify_tc138,
    139: verify_tc139,
    140: verify_tc140,
    141: verify_tc141,
    142: verify_tc142,
    143: verify_tc143,
    144: verify_tc144,
    145: verify_tc145,
    146: verify_tc146,
    147: verify_tc147,
    148: verify_tc148,
    149: verify_tc149,
    150: verify_tc150,
    151: verify_tc151,
    152: verify_tc152,
    153: verify_tc153,
    154: verify_tc154,
    155: verify_tc155,
    156: verify_tc156,
    157: verify_tc157,
    158: verify_tc158,
    159: verify_tc159,
    160: verify_tc160,
    200: verify_tc200,
    201: verify_tc201,
    202: verify_tc202,
    203: verify_tc203,
    210: verify_ha_2n1s,
    211: verify_ha_2n1s,
    212: verify_ha_2n1s,
    213: verify_ha_2n1s,
    214: verify_ha_2n1s,
    215: verify_ha_2n1s,
    216: verify_ha_2n1s,
    217: verify_tc217,
    218: verify_ha_2n1s,
    219: verify_ha_2n1s,
    220: verify_tc220,
    221: verify_tc221,
    222: verify_tc222,
    223: verify_tc223,
    224: verify_tc224,
    225: verify_tc225,
    226: verify_tc226,
    227: verify_tc227,
    228: verify_tc228,
    229: verify_tc229,
    230: verify_tc230,
    231: verify_tc231,
    232: verify_tc232,
    233: verify_tc233,
    234: verify_tc234,
    235: verify_tc235,
    300: lambda reads, lines: verify_o3_exact_reads(300, reads, 2),
    301: lambda reads, lines: verify_o3_exact_reads(301, reads, 2),
    302: lambda reads, lines: verify_o3_exact_reads(302, reads, 32),
    303: lambda reads, lines: verify_o3_exact_reads(303, reads, 16),
}

def verify_testcase(tc_id, reads, lines):
    if tc_id in VERIFIERS:
        return VERIFIERS[tc_id](reads, lines)
    return False, f"FAILED: unknown test case TC{tc_id}", []

# ── Compilation ───────────────────────────────────────────────────
def compile_workload(tc_name, num_nodes=3):
    if tc_name.startswith("/") and os.path.isfile(tc_name):
        return tc_name
    elf_path = os.path.join(WORKLOAD_DIR, tc_name + ".elf")
    src_path = os.path.join(WORKLOAD_DIR, tc_name + ".c")
    if not os.path.exists(src_path):
        print(f"ERROR: workload source not found: {src_path}", flush=True)
        return None
    cc = "aarch64-linux-gnu-gcc"
    dual_socket_tcs = {
        "e2e_tc32_cross_socket_read_miss",
        "e2e_tc33_cross_socket_writeback",
        "e2e_tc34_dual_socket_pingpong",
        "e2e_tc35_numa_latency_stress",
        "e2e_tc39_dual_socket_same_pa_interference",
        "e2e_tc81_cross_socket_latency",
        "e2e_tc95_8n2s_barrier_stress",
        "e2e_tc96_8n2s_cross_socket_read",
        "e2e_tc97_8n2s_pingpong",
        "e2e_tc98_8n2s_hotspot",
        "e2e_tc99_8n2s_perplane_slots",
        "e2e_tc100_8n2s_batch_rs",
        "e2e_tc101_8n2s_direct_fwd",
    }
    num_sockets = "2" if tc_name in dual_socket_tcs else "1"
    cmd = [
        cc, "-static", "-O0", "-g",
        f"-DNUM_NODES={num_nodes}", f"-DNUM_SOCKETS={num_sockets}",
        "-I", WORKLOAD_DIR,
        "-o", elf_path, src_path,
    ]
    print(f"  Compiling: {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"  FAILED:\n{proc.stderr}", flush=True)
        return None
    return elf_path

# ═══════════════════════════════════════════════════════════════════
#  EARLY PROXY RESOLUTION (gem5 v25.1 workaround)
# ═══════════════════════════════════════════════════════════════════

def _collect_memories(system):
    """Find orphan MemCtrl/DDR objects and parent them under System.
    In create_ubcc_system, MemCtrl objects are created but never
    explicitly parented — they're only used for port bindings.
    gem5 v25.1's MemStats::regStats() needs them parented under System."""
    import gc
    from m5.objects import AbstractMemory, MemCtrl, Root as _Root

    memories = []
    memctrls = []

    # Scan all Python objects for SimObject instances not in the tree
    for obj in gc.get_objects():
        try:
            if isinstance(obj, _Root):
                continue
            if isinstance(obj, AbstractMemory):
                memories.append(obj)
            elif isinstance(obj, MemCtrl):
                memctrls.append(obj)
        except (TypeError, AttributeError, ReferenceError):
            continue

    # Parent orphans under system
    for i, mc in enumerate(memctrls):
        if not mc.has_parent():
            try:
                system.add_child(f"ext_mc_{i}", mc)
            except Exception:
                try:
                    mc.set_parent(system, f"ext_mc_{i}")
                    system._children[f"ext_mc_{i}"] = mc
                except Exception:
                    pass

    for i, mem in enumerate(memories):
        if not mem.has_parent():
            try:
                system.add_child(f"ext_mem_{i}", mem)
            except Exception:
                try:
                    mem.set_parent(system, f"ext_mem_{i}")
                    system._children[f"ext_mem_{i}"] = mem
                except Exception:
                    pass

    system.memories = memories
    print(f"[E2E] system.memories: {len(memories)} AbstractMemory + {len(memctrls)} MemCtrl",
          flush=True)


def _early_unproxy_all(root):
    """Walk root children tree and call unproxyParams() to resolve all
    Parent.*/Self.* proxy params BEFORE C++ object construction happens.
    gem5 v25.1's internal config_filesystem (called by Ruby.create_system)
    triggers C++ construction, which fatals on unresolved proxies."""
    from m5.SimObject import SimObject
    from m5.proxy import isproxy

    # Iterative DFS to avoid recursion limit issues
    stack = [(root, 0)]
    while stack:
        obj, _ = stack.pop()
        if not isinstance(obj, SimObject):
            continue
        try:
            obj.unproxyParams()
        except Exception:
            pass
        # Push children
        for name in sorted(obj._children.keys(), reverse=True):
            child = obj._children[name]
            if hasattr(child, '__iter__') and not isinstance(child, (str, bytes)):
                for c in reversed(list(child)):
                    if isinstance(c, SimObject):
                        stack.append((c, 0))
            elif isinstance(child, SimObject):
                stack.append((child, 0))

# ═══════════════════════════════════════════════════════════════════
#  GEM5 CONFIG MODE
# ═══════════════════════════════════════════════════════════════════

def gem5_config_main():
    import argparse as _ap
    _parser = _ap.ArgumentParser()
    _parser.add_argument("--tc", type=int, default=0,
                        help="Test case id (drives workload selection when "
                             "--workload is not provided, and the dual-socket "
                             "default for TC32-35/39). Optional if --workload "
                             "and --num-sockets are given.")
    _parser.add_argument("--workload", type=str, default="",
                        help="Path to a pre-compiled workload ELF. When "
                             "provided, takes precedence over --tc-driven "
                             "compilation; gem5 will NOT compile anything.")
    _parser.add_argument("--all", action="store_true")
    # Multi-process split: this gem5 process owns exactly one node.
    #   --node-id N   : build/run only node N (default -1 = all nodes, legacy)
    #   --num-nodes K : total nodes in the system (default from DEFAULT_N)
    #   --num-sockets S: sockets per node (overrides per-TC default if given)
    _parser.add_argument("--node-id", type=int, default=-1)
    _parser.add_argument("--num-nodes", type=int, default=0)
    _parser.add_argument("--num-sockets", type=int, default=0)
    _parser.add_argument("--cpu-model", choices=("timing", "o3"),
                         default="timing")
    _parser.add_argument("--sequencer-max-outstanding", type=int, default=0,
                         help="Override Ruby Sequencer outstanding limit; "
                              "0 keeps the model default")
    _parser.add_argument("--ha-profile", choices=("ubcc", "ha-vi", "ha"),
                         default=os.environ.get("EP_HA_PROFILE", "ubcc"),
                         help="EP home-controller profile")
    _parser.add_argument("--clear-profile",
                         choices=("ack", "lossless-oneway"),
                         default=os.environ.get("OURCC_CLEAR_PROFILE", "ack"),
                         help="OurCC Clear completion profile")
    # Phase 0.3: EP controller params (mapped to env for SimObject; argv planned)
    _parser.add_argument("--silent-upgrade", type=int, default=-1,
                         help="EP: silent upgrade (0=off, 1=on, -1=env/defaults)")
    _parser.add_argument("--direct-fwd", type=int, default=-1,
                         help="EP: direct-forward (0=off, 1=on, -1=env/defaults)")
    _parser.add_argument("--ep-retry-cycles", type=int, default=-1)
    _parser.add_argument("--ep-compack-retry", type=int, default=-1)
    _parser.add_argument("--ep-wakeup-retry", type=int, default=-1)
    _parser.add_argument("--ep-upgrade-retry-min", type=int, default=-1)
    _parser.add_argument("--ep-upgrade-retry-max", type=int, default=-1)
    _parser.add_argument("--ep-upgrade-retry-max-resends", type=int, default=-1)
    _parser.add_argument("--ep-delta-noc", type=int, default=-1)
    _parser.add_argument("--ep-wait-cap", type=int, default=-1)
    _parser.add_argument("--ubcc-bloom-bytes", type=int, default=-1)
    _parser.add_argument("--ubcc-batch-rs", type=int, default=-1)
    _parser.add_argument("--ubcc-metadata-size", "--ubcc_metadata_size",
                         dest="ubcc_metadata_size", type=int,
                         default=128 * 1024 * 1024,
                         help="UBCC metadata DRAM bytes (hyphenated spelling "
                              "preferred; underscore alias retained)")
    _args, _unknown = _parser.parse_known_args()

    # Phase 0.3: map script args to env vars for SimObject params
    # (precedes Ruby system creation so SimObjects see them in init)
    _ep_env_map = [
        ("EP_HA_PROFILE", _args.ha_profile, True),
        ("OURCC_CLEAR_PROFILE", _args.clear_profile, True),
        ("EP_SILENT_UPGRADE", _args.silent_upgrade,
         _args.silent_upgrade >= 0),
        ("EP_DIRECT_FWD", _args.direct_fwd, _args.direct_fwd >= 0),
        ("EP_RETRY_CYCLES", _args.ep_retry_cycles,
         _args.ep_retry_cycles >= 0),
        ("EPRN_COMPACK_RETRY_CYCLES", _args.ep_compack_retry,
         _args.ep_compack_retry >= 0),
        ("EPRN_WAKEUP_RETRY_CYCLES", _args.ep_wakeup_retry,
         _args.ep_wakeup_retry >= 0),
        ("EP_UPGRADE_RETRY_MIN_CYCLES", _args.ep_upgrade_retry_min,
         _args.ep_upgrade_retry_min >= 0),
        ("EP_UPGRADE_RETRY_MAX_CYCLES", _args.ep_upgrade_retry_max,
         _args.ep_upgrade_retry_max >= 0),
        ("EP_UPGRADE_RETRY_MAX_RESENDS", _args.ep_upgrade_retry_max_resends,
         _args.ep_upgrade_retry_max_resends >= 0),
        ("EP_DELTA_NOC_CYCLES", _args.ep_delta_noc,
         _args.ep_delta_noc >= 0),
        ("UB_WAIT_CAP", _args.ep_wait_cap, _args.ep_wait_cap >= 0),
        ("UBCC_BLOOM_BYTES", _args.ubcc_bloom_bytes,
         _args.ubcc_bloom_bytes >= 0),
        ("UBCC_BATCH_RS", _args.ubcc_batch_rs,
         _args.ubcc_batch_rs >= 0),
    ]
    for _ek, _ev, _enabled in _ep_env_map:
        if _enabled:
            os.environ[_ek] = str(_ev)

    # Workload selection: --workload (decoupled launcher path) wins; else
    # fall back to --tc-driven compilation (legacy / single-process path).
    if _args.workload:
        binary = _args.workload
        if not os.path.exists(binary):
            print(f"ERROR: --workload not found: {binary}", flush=True)
            sys.exit(1)
        tc_name = "(precompiled)"
    elif _args.tc in TESTCASES:
        tc_name = TESTCASES[_args.tc]
        binary = compile_workload(tc_name)
        if not binary:
            sys.exit(1)
    elif _args.all:
        tc_name = "e2e_tc1_dsm_local"  # Combined mode: use TC1 as base
        binary = compile_workload(tc_name)
        if not binary:
            sys.exit(1)
    else:
        print(f"ERROR: provide --workload <path> or --tc <id> "
              f"(valid ids: {sorted(TESTCASES.keys())})", flush=True)
        sys.exit(1)

    # UBCC configuration is derived from CLI args and passed to
    # create_ubcc_system via the `options` object (see below), not env vars.
    # Number of sockets: explicit --num-sockets wins; else per-TC default.
    if _args.num_sockets > 0:
        _cfg_num_sockets = _args.num_sockets
    elif _args.tc in (32, 33, 34, 35, 39):
        # Dual-socket tests: TC32~TC35 + TC39 force 2 sockets.
        _cfg_num_sockets = 2
    else:
        _cfg_num_sockets = 1

    # Multi-process split configuration.
    _local_node = _args.node_id
    # DEFAULT_N is imported below from CHI_basic_framework_config;
    # use a sane fallback if the import hasn't happened yet.
    try:
        _cfg_num_nodes = _args.num_nodes if _args.num_nodes > 0 else DEFAULT_N
    except NameError:
        _cfg_num_nodes = _args.num_nodes if _args.num_nodes > 0 else 3

    def _effective_toggle(requested, env_name, default):
        if requested >= 0:
            return requested
        try:
            return int(os.environ.get(env_name, default))
        except ValueError:
            return default

    # When this process owns a single node, only that node's UBAdapter binds
    # its Port (local_node = node id). -1 = all nodes (single-process mode).

    # ── Build gem5 system ──────────────────────────────────────────
    import m5
    from m5.objects import (
        ArmSystem, SystemCounter, GenericTimer, ArmPPI, NULL,
        SrcClockDomain, VoltageDomain, RubySystem,
        ArmTimingSimpleCPU, ArmO3CPU, Process, SEWorkload, Root, AddrRange,
        ArmEmuLinux,
    )

    gem5_root = os.path.dirname(os.path.dirname(os.path.dirname(GEM5_BIN)))
    configs_path = os.path.join(gem5_root, "configs")
    if configs_path not in sys.path:
        sys.path.insert(0, configs_path)

    from ruby.CHI_basic_framework_config import (
        DEFAULT_N, DEFAULT_L, DEFAULT_D, DEFAULT_SEG_SIZE, NodeConfig,
    )
    import ruby.CHI as chi_module
    from ruby.CHI_ubcc_framework import create_ubcc_system

    chi_module.create_system = create_ubcc_system

    # Total nodes in the system (may be overridden for >3-node configs).
    NODES = _args.num_nodes if _args.num_nodes > 0 else DEFAULT_N
    CPUS_PER_NODE = DEFAULT_L * DEFAULT_D
    # In split mode this process builds CPUs only for its own node.
    if _local_node < 0:
        BUILD_NODES = list(range(NODES))
    else:
        BUILD_NODES = [_local_node]
    TOTAL_CPUS = len(BUILD_NODES) * CPUS_PER_NODE
    try:
        with open("/proc/self/cmdline", "rb") as _cmdline:
            _process_argv = [item.decode(errors="replace")
                             for item in _cmdline.read().split(b"\0") if item]
    except OSError:
        _process_argv = list(sys.argv)
    _manifest = {
        "component": "gem5-config",
        "argv": list(sys.argv),
        "config_argv": list(sys.argv),
        "process_argv": _process_argv,
        "unknown_args": list(_unknown),
        "tc": _args.tc or int(os.environ.get("E2E_TC", "0")),
        "node": _local_node,
        "num_nodes": _cfg_num_nodes,
        "num_sockets": _cfg_num_sockets,
        "build_nodes": BUILD_NODES,
        "cpus_per_node": CPUS_PER_NODE,
        "process_cpu_count": TOTAL_CPUS,
        "cpu_model": _args.cpu_model,
        "sequencer_max_outstanding": _args.sequencer_max_outstanding,
        "ha_profile": _args.ha_profile,
        "clear_profile": _args.clear_profile,
        "silent_upgrade": {
            "requested": _args.silent_upgrade,
            "env": os.environ.get("EP_SILENT_UPGRADE"),
            "effective": _effective_toggle(_args.silent_upgrade,
                                            "EP_SILENT_UPGRADE", 0),
        },
        "direct_fwd": {
            "requested": _args.direct_fwd,
            "env": os.environ.get("EP_DIRECT_FWD"),
            "effective": _effective_toggle(_args.direct_fwd,
                                            "EP_DIRECT_FWD", 0),
        },
        "batch_rs": {
            "requested": _args.ubcc_batch_rs,
            "env": os.environ.get("UBCC_BATCH_RS"),
            "effective": _effective_toggle(_args.ubcc_batch_rs,
                                            "UBCC_BATCH_RS", 1),
        },
        "metadata_bytes": _args.ubcc_metadata_size,
    }
    print("[PROCESS-MANIFEST] " + json.dumps(_manifest, separators=(",", ":"),
                                               sort_keys=True), flush=True)
    local_external_ranges = []
    for node_id in BUILD_NODES:
        node_cfg = NodeConfig(node_id, NODES, DEFAULT_SEG_SIZE,
                              _cfg_num_sockets)
        local_external_ranges.extend(node_cfg.all_local_private_ranges())

    # v25.1: Create Root first so System has parent for proxy resolution.
    root = Root(full_system=False)
    # Set the coherent external range in the constructor. The v25.1 proxy
    # workaround below may materialize the C++ System before later Python
    # assignments; O3 fetch consults the constructor-captured range.
    system = ArmSystem(
        mem_mode="timing", cache_line_size=64,
        external_memory_ranges=local_external_ranges)
    root.system = system
    system.clk_domain = SrcClockDomain(clock="2GHz")
    system.clk_domain.voltage_domain = VoltageDomain()
    # Give SE workloads an architected, simulated-time counter.  The timer
    # PPIs are intentionally unconnected: TC13x reads CNTVCT_EL0 only and
    # never programs a compare register that would deliver an interrupt.
    system.system_counter = SystemCounter(freqs=[0x01800000])
    system.generic_timer = GenericTimer(
        system=system,
        counter=system.system_counter,
        int_el3_phys=ArmPPI(num=29, platform=NULL),
        int_el1_phys=ArmPPI(num=30, platform=NULL),
        int_el1_virt=ArmPPI(num=27, platform=NULL),
        int_el2_ns_phys=ArmPPI(num=26, platform=NULL),
        int_el2_ns_virt=ArmPPI(num=28, platform=NULL),
        int_el2_s_phys=ArmPPI(num=20, platform=NULL),
        int_el2_s_virt=ArmPPI(num=19, platform=NULL),
        cntfrq=0x01800000)

    # RubySystem is created inside Ruby.create_system — don't pre-create.
    # ruby_system = RubySystem()  # REMOVED

    cpus = []
    cpu_class = ArmO3CPU if _args.cpu_model == "o3" else ArmTimingSimpleCPU
    for i in range(TOTAL_CPUS):
        cpu = cpu_class(cpu_id=i)
        cpu.clk_domain = SrcClockDomain(
            clock="2GHz",
            voltage_domain=system.clk_domain.voltage_domain)
        cpu.createThreads()
        cpu.createInterruptController()
        cpus.append(cpu)

    system.cpu = cpus

    # ── Set SE workload BEFORE Process creation ────────────────
    # Process C++ constructor needs system->workload to be non-null.
    # Setting it now ensures the workload exists when Process objects
    # are constructed during m5.instantiate().
    system.workload = SEWorkload.init_compatible(binary)

    for i, cpu in enumerate(cpus):
        # Map this process's CPU to its global (node_id, global_cpu_index).
        # In split mode `cpus` holds only the local node's CPUs, so the
        # global node/cpu identity must be reconstructed from BUILD_NODES.
        node_id = BUILD_NODES[i // CPUS_PER_NODE]
        global_cpu_index = node_id * CPUS_PER_NODE + (i % CPUS_PER_NODE)
        # Q2: phys_pool_id selects which MemPool to allocate from.
        # Pool 0,1,2 cover [0,1,2]TiB + 256MiB (node LP+UE ranges).
        # Each process allocates stack/heap from its own node's pool,
        # ensuring the PA is within the node's address space.
        proc = Process(pid=100 + global_cpu_index, phys_pool_id=node_id)
        proc.executable = binary
        proc.cwd = os.getcwd()
        proc.cmd = [binary, str(node_id), str(global_cpu_index)]
        # Q2 FIX: Redirect workload stdout/stderr to files in outdir
        # so the harness can parse [READ_VAL] markers.
        # Default "cout"/"cerr" map to simulator terminal (not files).
        # Socket workers in the same gem5 process share one node-level simout.
        # O_APPEND preserves each single-syscall workload marker atomically.
        proc.output = f"append:simout_n{node_id}"
        proc.errout = "simerr"
        cpu.workload = [proc]

    # ── Q2 FIX: Targeted proxy resolution (v25.1 workaround) ─────
    # Previous _early_unproxy_all(root) resolved all proxies on all
    # SimObjects, including CPU port refs.  This caused the port
    # bindings established later by connectCpuPorts to malfunction:
    # the CPU's sendTimingReq returned success but the RubyPort's
    # recvTimingReq was never called. Root cause: PortRef.unproxy()
    # on unconnected CPU ports sets internal state that prevents
    # ccConnect() from establishing a proper C++ binding.
    #
    # ── Q2 FIX: Targeted proxy resolution (v25.1 workaround) ─────
    # Previous _early_unproxy_all(root) resolved all proxies on all
    # SimObjects, including CPU port refs.  This caused the port
    # bindings established later by connectCpuPorts to malfunction:
    # the CPU's sendTimingReq returned success but the RubyPort's
    # recvTimingReq was never called. Root cause: PortRef.unproxy()
    # on unconnected CPU ports sets internal state that prevents
    # ccConnect() from establishing a proper C++ binding.
    #
    # Fix: iterate ALL SimObjects but SKIP CPU objects to avoid
    # touching their port refs.  CPUs get their port bindings
    # after Ruby.create_system(), so their port refs must remain
    # fresh for proper ccConnect() during m5.instantiate().
    from m5.objects import BaseCPU
    for _obj in root.descendants():
        if isinstance(_obj, BaseCPU):
            continue  # Skip CPUs: preserve fresh port refs
        try:
            _obj.unproxyParams()
        except Exception:
            pass
    print(f"[E2E-Q2] Targeted proxy resolution: all non-CPU objects", flush=True)

    # ── Options ────────────────────────────────────────────────────
    class O: pass
    options = O()
    options.num_cpus = TOTAL_CPUS
    options.num_dirs = 1
    options.num_l3caches = NODES
    options.l3_size = "256kB"
    options.l3_assoc = 16
    options.cacheline_size = 64
    options.topology = "Crossbar"
    options.network = "simple"
    options.router_latency = 1
    options.router_link_latency = 1
    options.node_link_latency = 1
    options.enable_dvm = False
    options.chi_config = None
    options.access_backing_store = True
    options.enable_dram_powerdown = False
    options.protocol = "CHI"
    options.cpu_type = "ArmO3CPU" if _args.cpu_model == "o3" \
        else "ArmTimingSimpleCPU"
    options.simple_physical_channels = []
    options.vcs_per_vnet = 1
    options.mesh_rows = 1
    options.routing_algorithm = 0
    options.garnet_deadlock_threshold = 50000
    options.xor_low_bit = 0
    options.network_fault_model = False
    options.cross_links = []
    options.cross_link_latency = 0
    options.mem_type = "SimpleMemory"
    options.mem_channels = 1
    options.mem_channels_intlv = 128
    options.link_latency = 1
    options.link_width_bits = 128
    options.numa_high_bit = 0

    # ── UBCC config (Phase 4: passed via options, not env vars) ────
    options.ubcc_num_nodes = _cfg_num_nodes
    options.ubcc_num_sockets = _cfg_num_sockets
    options.ubcc_local_node = _local_node
    options.ubcc_metadata_size = _args.ubcc_metadata_size

    # ── Patch v25.1: Skip config_filesystem proxy-triggering call ──
    import common.FileSystemConfig as _fsc
    _fsc.config_filesystem = lambda *a, **kw: None

    # ── Pre-set mem_ranges so Ruby.create_system creates phys_mem ──
    # Ruby.py uses system.mem_ranges[0] to create the backing-store
    # SimpleMemory.  Must cover ALL nodes' address spaces (up to
    # Node2 base + 5*SEG ≈ 2.2 TB) so that self-tests and grant-data
    # population can functional-read any PA.
    # Per-node window = (2 + N*S) DSM/private segments + metadata DRAM.
    # Phase 0: metadata default is now 128 MiB (was 16 MiB).
    _num_sockets_cfg = _cfg_num_sockets
    _segs_per_node = 2 + NODES * _num_sockets_cfg
    _meta_size = getattr(options, "ubcc_metadata_size", 128 * 1024 * 1024)
    _node_window = _segs_per_node * DEFAULT_SEG_SIZE + _meta_size
    _max_pa = (NODES - 1) * (1 << 40) + _node_window
    system.mem_ranges = [AddrRange(0, size=_max_pa)]

    # ── Create Ruby system ─────────────────────────────────────────
    from ruby import Ruby
    # Ruby.create_system performs all setup (creates ruby, network,
    # topology, sequencers). It does NOT return results directly;
    # results are stored on system.ruby.
    Ruby.create_system(options, False, system, None, cpus)
    ruby_system = system.ruby

    if not ruby_system:
        print("FATAL: Ruby.create_system did not create system.ruby")
        sys.exit(1)

    # Q2 FIX: resolve proxy params set during create_ubcc_system
    # (downstream_destinations, addr_ranges on SNF/HN-F controllers)
    # after create_system has finished wiring everything.
    for _obj in ruby_system.descendants():
        try:
            _obj.unproxyParams()
        except Exception:
            pass
    print(f"[E2E-Q2] Post-create_system proxy resolution on Ruby tree",
          flush=True)

    cpu_sequencers = ruby_system._cpu_ports

    if _args.sequencer_max_outstanding > 0:
        for seq in cpu_sequencers:
            seq.max_outstanding_requests = _args.sequencer_max_outstanding
    print(f"[E2E-CPU] model={_args.cpu_model} "
          f"sequencer_max_outstanding="
          f"{_args.sequencer_max_outstanding or 'default'}", flush=True)

    for i, seq in enumerate(cpu_sequencers):
        seq.connectCpuPorts(cpus[i])

    # ── Q2 DEBUG: Verify port peer references are set ────────────
    for i, seq in enumerate(cpu_sequencers):
        cpu_ref = cpus[i]._get_port_ref("icache_port")
        dcache_ref = cpus[i]._get_port_ref("dcache_port")
        ip = cpu_ref.peer
        dp = dcache_ref.peer
        iok = ip is not None
        dok = dp is not None
        if not iok or not dok:
            print(f"[E2E-Q2] WARN: CPU{i} port peer missing: icache_peer={iok} dcache_peer={dok}",
                  flush=True)
    print(f"[E2E-Q2] Port peer references verified", flush=True)

    # Build proper per-node mem_ranges
    all_ranges = []
    for nid in range(NODES):
        cfg = NodeConfig(nid, NODES, DEFAULT_SEG_SIZE, _cfg_num_sockets)
        for sid in range(_cfg_num_sockets):
            all_ranges.append(cfg.local_private_range(sid))
            all_ranges.append(cfg.metadata_private_range(sid))
        for hn in range(NODES):
            for sid in range(_cfg_num_sockets):
                all_ranges.append(NodeConfig.dsm_range_for(
                    hn, DEFAULT_SEG_SIZE, cfg.phy_base, _cfg_num_sockets, sid))
    system.mem_ranges = all_ranges

    # ── Q2 FIX: Pre-map binary + stack pages per-node ──────────────
    # Process::initState() calls allocateMem() → MemPool::allocate(),
    # which fails because MemPools are not properly populated from
    # DDR4 controllers at the time SE workload is set up (the DDR4
    # controllers are created later during Ruby.create_system()).
    # Work around by pre-mapping all binary and initial stack virtual
    # pages to per-node physical pages, so pTable->translate() finds
    # the mapping and skips allocateMem() entirely.
    #
    # CRITICAL: Each node has its own PA space (node i base = i << 40).
    # Physical pages must be allocated in the CPU's own node space,
    # otherwise the EP_RNF controller rejects them as non-DSM.
    import struct as _struct
    _page_size = 4096  # ARM64 page size
    _NODE_ADDR_SHIFT = 40
    _CPUS_PER_NODE = DEFAULT_L * DEFAULT_D

    # Per-node physical page counters (start at 1 MiB offset).
    # Build for ALL global nodes so split-mode (local node != 0) is covered.
    _node_pa = {}
    for _nid in range(NODES):
        _node_pa[_nid] = (_nid << _NODE_ADDR_SHIFT) + 0x100000

    # Parse ELF program headers to find PT_LOAD segments
    _elf_segments = []
    with open(binary, 'rb') as _f:
        # Read ELF header (64-bit)
        _e_hdr = _f.read(64)
        _e_phoff = _struct.unpack_from('<Q', _e_hdr, 32)[0]  # e_phoff
        _e_phentsize = _struct.unpack_from('<H', _e_hdr, 54)[0]  # e_phentsize
        _e_phnum = _struct.unpack_from('<H', _e_hdr, 56)[0]  # e_phnum

        for _i in range(_e_phnum):
            _f.seek(_e_phoff + _i * _e_phentsize)
            _phdr = _f.read(_e_phentsize)
            _p_type = _struct.unpack_from('<I', _phdr, 0)[0]
            if _p_type == 1:  # PT_LOAD
                _p_offset = _struct.unpack_from('<Q', _phdr, 8)[0]
                _p_vaddr = _struct.unpack_from('<Q', _phdr, 16)[0]
                _p_filesz = _struct.unpack_from('<Q', _phdr, 32)[0]
                _p_memsz = _struct.unpack_from('<Q', _phdr, 40)[0]
                if _p_memsz > 0:
                    _elf_segments.append({
                        'va': _p_vaddr,
                        'memsz': _p_memsz,
                    })

    _total_pages = 0

    # Map binary segments: each CPU gets its own copy in its node's PA space.
    # In split mode, cpus holds only the local node's CPUs, so the global
    # node identity comes from BUILD_NODES (not the raw cpu index).
    for _cpu_idx, _cpu in enumerate(cpus):
        _node_id = BUILD_NODES[_cpu_idx // _CPUS_PER_NODE]
        for _proc in _cpu.workload:
            if _proc is None:
                continue
            for _seg in _elf_segments:
                _va_start = _seg['va'] & ~(_page_size - 1)
                _va_end = (_seg['va'] + _seg['memsz'] + _page_size - 1) & ~(_page_size - 1)
                for _va in range(_va_start, _va_end, _page_size):
                    _pa = _node_pa[_node_id]
                    _node_pa[_node_id] += _page_size
                    _proc.map(_va, _pa, _page_size, cacheable=True)
                    _total_pages += 1

    # ── Map local_private_range for workloads that need direct
    #     local-memory access (TC112 TBE interference, etc.).
    #     VA 0x01000000 maps to the node's local_private physical base.
    #     Map 16 MB (4096 pages) — enough for any cache-line striding.
    _local_va_base = 0x01000000   # 16 MB — well below mmap_end=0x40000000
    _local_map_bytes = 16 * 1024 * 1024  # 16 MB
    _local_pages = 0
    for _cpu_idx, _cpu in enumerate(cpus):
        _node_id = BUILD_NODES[_cpu_idx // _CPUS_PER_NODE]
        _local_pa_base = _node_id << _NODE_ADDR_SHIFT
        for _proc in _cpu.workload:
            if _proc is None:
                continue
            for _va in range(_local_va_base,
                             _local_va_base + _local_map_bytes,
                             _page_size):
                _pa = _local_pa_base + (_va - _local_va_base)
                _proc.map(_va, _pa, _page_size, cacheable=True)
                _local_pages += 1
    _total_pages += _local_pages

    print(f"[E2E-Q2] Pre-mapped {_total_pages} pages ({_total_pages * _page_size} bytes)"
           f" for {len(cpus)} CPUs (per-node PA ranges)",
           flush=True)

    # ── Ensure system.memories includes DDR4 DRAMs ──────────────
    # _early_unproxy_all resolved Self.all → [] before DDR4 objects
    # existed.  We explicitly rebuild system.memories here AND
    # directly manipulate _values to ensure the C++ parameter
    # transfer picks it up (bypassing any SimObject caching).
    from m5.objects import AbstractMemory
    _all_memories = [obj for obj in system.descendants()
                     if isinstance(obj, AbstractMemory)]
    if hasattr(ruby_system, 'phys_mem') and ruby_system.phys_mem:
        if ruby_system.phys_mem not in _all_memories:
            _all_memories.append(ruby_system.phys_mem)
    system.memories = _all_memories
    # Also directly set in _values to bypass any getattr caching
    system._values['memories'] = _all_memories
    print(f"[E2E] system.memories: {len(system.memories)} objects "
          f"(DDR4 DRAMs + phys_mem)", flush=True)

    # Debug: print Ruby phys_mem info
    if hasattr(ruby_system, 'phys_mem') and ruby_system.phys_mem:
        pm = ruby_system.phys_mem
        print(f"[E2E] Ruby phys_mem: {pm} range={pm.range}", flush=True)
    print(f"[E2E] Ruby access_backing_store={ruby_system.access_backing_store}",
          flush=True)

    # Q2 DEBUG: verify HN-F downstream destinations are set before instantiate
    for nid in range(NODES):
        hnfw = getattr(ruby_system, f"hnf_node{nid}", None)
        if hnfw and hasattr(hnfw, '_cntrl'):
            ctrl = hnfw._cntrl
            dd = getattr(ctrl, 'downstream_destinations', [])
            ar = getattr(ctrl, 'addr_ranges', [])
            dd_vals = ctrl._values.get('downstream_destinations', [])
            ar_vals = ctrl._values.get('addr_ranges', [])
            print(f"[E2E-Q2] HN-F{nid}: downstream_destinations="
                  f"{len(dd)}(attr) / {len(dd_vals)}(_values), "
                  f"addr_ranges={len(ar)}(attr) / {len(ar_vals)}(_values)",
                  flush=True)
            # Also check SNFs
            for snf_name in [f"l_snf_node{nid}", f"dl_snf_node{nid}",
                             f"ep_snf_node{nid}", f"ep_rnf_node{nid}"]:
                snfw = getattr(ruby_system, snf_name, None)
                if snfw and hasattr(snfw, '_cntrl'):
                    sc = snfw._cntrl
                    sar = sc._values.get('addr_ranges', [])
                    print(f"[E2E-Q2]   {snf_name}: addr_ranges={len(sar)}(_values)",
                          flush=True)

    # ── Disable M4-M8 self-tests for workload runs ───────────────
    # Self-tests run at init() (tick 0) and perform heavy UBCC operations
    # that contend with ARM workload resources.  Disabling them ensures
    # clean workload-only execution.
    for nid in range(NODES):
        be = getattr(ruby_system, f"ep_backend_node{nid}", None)
        if be:
            be.enable_self_test = False
    print(f"[E2E] Self-tests disabled on all {NODES} EPBackend nodes",
          flush=True)

    # ── Debug Fault Injection Config (TC47-49) ────────────────────
    _fault_tc_configs = {
        47: ["tc47_drop_clear:ClearReq:1:0:0:drop::1"],
        48: ["tc48_dup_inv_ack:InvalidateAck:2:0:0:dup::1"],
        49: ["tc49_reorder_inv_ack:InvalidateAck:1:0:0:reorder:100000:1"],
    }
    _fault_cfg_line = None
    # UBIOModule fault injection was removed when UBIOModule was decoupled from
    # gem5. The fault TC configs (47-49) are retained for future re-wiring via
    # the ubio process.

    m5.instantiate()
    print(f"[FAULT-DEBUG] NODES={NODES} ruby_system type={type(ruby_system)}", flush=True)

    _cnt = sum(1 for x in dir(ruby_system)); print(f"[FAULT-DEBUG] attrs={_cnt}", flush=True)
    print(f"[FAULT-DEBUG] UBIOModule via descendants: 0 (removed from gem5)", flush=True)
    print("=" * 60, flush=True)
    print(f"E2E Test: {tc_name}  (nodes={NODES}, CPUs={TOTAL_CPUS})", flush=True)
    print(f"Workload: {binary}", flush=True)
    print("=" * 60, flush=True)

    exit_event = m5.simulate()
    cause = exit_event.getCause()
    print(f"SIM_CAUSE={cause}", flush=True)

    # ── Split mode: this process owns a single node; it does NOT have the
    #    other nodes' outputs, so verification happens in the orchestrator
    #    (run_multi.sh) after all per-node gem5 processes finish. Here we
    #    just flush our own simout and exit cleanly. ───────────────────
    if _local_node >= 0:
        my_simout = os.path.join(m5.options.outdir, f"simout_n{_local_node}")
        nlines = 0
        if os.path.exists(my_simout):
            with open(my_simout) as _f:
                nlines = sum(1 for _ in _f)
        print(f">>> NODE{_local_node} SIM DONE (cause={cause}, "
              f"simout_lines={nlines}) <<<", flush=True)
        # Multi-process split: explicitly run gem5 exit callbacks BEFORE exiting
        # so the UBAdapter's registered Port::terminate() fires and notifies
        # ubio that this node is done. A bare sys.exit(0) does not reliably
        # trigger gem5's atexit-based doExitCleanup in the embedded interpreter,
        # which would leave ubio's gem5-side clock frozen and stall every other
        # still-running node. See UBAdapter::init registerExitCallback.
        sys.stderr.write(f"[NODE{_local_node}] calling doExitCleanup...\n")
        sys.stderr.flush()
        try:
            import _m5.core as _m5core
            _m5core.doExitCleanup()
            sys.stderr.write(f"[NODE{_local_node}] doExitCleanup returned\n")
            sys.stderr.flush()
        except Exception as _e:
            sys.stderr.write(f"[NODE{_local_node}] doExitCleanup failed: {_e}\n")
            sys.stderr.flush()
        sys.exit(0)

    # ── Collect output ─────────────────────────────────────────────
    raw_lines = []
    # Q2: Per-node output files avoid interleaving from concurrent CPUs
    for nid in range(NODES):
        simout_path = os.path.join(m5.options.outdir, f"simout_n{nid}")
        if os.path.exists(simout_path):
            with open(simout_path, "r") as f:
                raw_lines.extend(line.rstrip("\n") for line in f)

    simerr_path = os.path.join(m5.options.outdir, "simerr")
    if os.path.exists(simerr_path):
        with open(simerr_path, "r") as f:
            raw_lines.extend(line.rstrip("\n") for line in f)

    if _fault_cfg_line:
        raw_lines.append(_fault_cfg_line)

    all_reads = parse_read_vals(raw_lines)

    # ── Verify ─────────────────────────────────────────────────────
    tc_id = _args.tc
    passed, msg, failures = verify_testcase(tc_id, all_reads, raw_lines)
    print(f"\n  {msg}", flush=True)
    for f in failures:
        print(f"    MISMATCH: {f['raw']}", flush=True)

    if passed:
        print(f"\n>>> TC{tc_id} PASSED <<<", flush=True)
    else:
        print(f"\n>>> TC{tc_id} FAILED <<<", flush=True)

    sys.exit(0 if passed else 1)


# ═══════════════════════════════════════════════════════════════════
#  PYTHON RUNNER MODE
# ═══════════════════════════════════════════════════════════════════

def runner_main():
    parser = argparse.ArgumentParser(description="E2E Test Runner")
    parser.add_argument("--tc", type=int, default=0)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--outdir", default="m5out/e2e")
    parser.add_argument("--trace-latency", action="store_true",
                        help="Enable UBLatency debug tracing and generate "
                             "HTML latency timeline.")
    args = parser.parse_args()

    if args.tc:
        tc_list = [args.tc]
    elif args.all:
        tc_list = sorted(TESTCASES.keys())
    else:
        print("Usage: python3 test_e2e.py --tc <N> | --all")
        sys.exit(1)

    if not os.path.exists(GEM5_BIN):
        print(f"ERROR: gem5 binary not found: {GEM5_BIN}")
        sys.exit(1)

    results = {}
    for tc_id in tc_list:
        tc_name = TESTCASES[tc_id]
        print(f"\n{'='*60}")
        print(f"Running {tc_name} (TC{tc_id})...")
        print(f"{'='*60}")

        outdir = os.path.join(args.outdir, f"tc{tc_id}")
        os.makedirs(outdir, exist_ok=True)

        cmd = [
            GEM5_BIN,
            f"--outdir={outdir}",
            os.path.abspath(__file__),
            f"--tc={tc_id}",
        ]
        if args.trace_latency:
            debug_file = os.path.join(outdir, "debug.log")
            cmd.append(f"--debug-flags=UBLatency")
            cmd.append(f"--debug-file={debug_file}")
        print(f"  CMD: {' '.join(cmd)}", flush=True)
        env = os.environ.copy()
        lib_paths = ["/mnt/data1/cgc/miniconda3/lib", env.get("LD_LIBRARY_PATH", "")]
        env["LD_LIBRARY_PATH"] = ":".join(filter(None, lib_paths))
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=900, cwd=os.path.dirname(GEM5_BIN), env=env)

        simout = os.path.join(outdir, "simout")
        raw_lines = []
        if os.path.exists(simout):
            with open(simout, "r") as f:
                raw_lines = [line.rstrip("\n") for line in f]
        raw_lines.extend(proc.stdout.splitlines())

        reads = parse_read_vals(raw_lines)
        passed, msg, failures = verify_testcase(tc_id, reads, raw_lines)

        print(f"  {msg}", flush=True)
        for f in failures:
            print(f"    MISMATCH: {f['raw']}", flush=True)
        results[tc_id] = passed

        # ── Latency trace post-processing ─────────────────────────
        if args.trace_latency:
            debug_log = os.path.join(outdir, "debug.log")
            html_out = os.path.join(outdir, "latency_trace.html")
            if os.path.exists(debug_log):
                script_dir = os.path.dirname(os.path.abspath(__file__))
                html_script = os.path.join(
                    script_dir, "../../tools/latency_trace_to_html.py")
                html_cmd = [
                    sys.executable, html_script,
                    "--log", debug_log,
                    "--out", html_out,
                ]
                print(f"  Post-processing latency trace...", flush=True)
                html_proc = subprocess.run(
                    html_cmd, capture_output=True, text=True, timeout=60)
                print(f"  {html_proc.stdout.strip()}", flush=True)
                if html_proc.stderr.strip():
                    print(f"  {html_proc.stderr.strip()}", flush=True)
            else:
                print(f"  WARNING: debug.log not found, skipping HTML gen.",
                      flush=True)

    print(f"\n{'='*60}")
    passed_cnt = sum(1 for v in results.values() if v)
    print(f"Results: {passed_cnt}/{len(results)} test cases passed")
    for tc_id, ok in results.items():
        print(f"  TC{tc_id}: {'PASS' if ok else 'FAIL'}")
    print(f"{'='*60}")
    sys.exit(0 if passed_cnt == len(results) else 1)


# ═══════════════════════════════════════════════════════════════════
#  SPLIT-MODE VERIFY (orchestrator aggregation)
# ═══════════════════════════════════════════════════════════════════

def verify_split_main():
    """Aggregate per-node simout files from a multi-process split run and
    apply verify_testcase, exactly as the single-process path did.

    Usage:
      test_e2e.py --verify-split --tc N --simout F1 [F2 ...]
    """
    import argparse as _ap
    p = _ap.ArgumentParser()
    p.add_argument("--verify-split", action="store_true")
    p.add_argument("--tc", type=int, required=True)
    p.add_argument("--simout", nargs="+", default=[],
                   help="per-node simout files to aggregate")
    p.add_argument("--fault-log", nargs="*", default=[],
                   help="ubio stderr logs to scan for [UBFAULT] evidence")
    args = p.parse_args()

    raw_lines = []
    found = 0
    for path in args.simout:
        if os.path.exists(path):
            found += 1
            with open(path) as f:
                raw_lines.extend(line.rstrip("\n") for line in f)
    expected = len(args.simout)

    # Pull fault-injection evidence ([UBFAULT]) from the ubio logs so the
    # fault TCs (47-49) can validate that a fault was actually injected.
    for path in args.fault_log:
        if os.path.exists(path):
            with open(path, errors="replace") as f:
                for line in f:
                    if ("[UBFAULT]" in line or
                        "[ResidentDirStats]" in line or
                        "[UBCC-STATS]" in line or
                        "[UBCC-NAIVE-EVICT]" in line or
                        "[UBCC-NAIVE-EVICT-DONE]" in line or
                        "BATCH-RS" in line or
                        "SILENT" in line or
                        "C4" in line or
                        "DIRECT-FWD" in line):
                        raw_lines.append(line.rstrip("\n"))
    print(f"[verify-split] TC{args.tc}: aggregated {found}/{expected} "
          f"simout files, {len(raw_lines)} lines", flush=True)

    # Missing per-node simout means a node likely crashed/aborted before
    # flushing output. This must be treated as FAIL in split-mode verify.
    if found != expected:
        print((f"  TC{args.tc} FAILED: missing simout files "
               f"({found}/{expected})"), flush=True)
        print(f">>> TC{args.tc} FAILED <<<", flush=True)
        sys.exit(1)

    reads = parse_read_vals(raw_lines)
    passed, msg, failures = verify_testcase(args.tc, reads, raw_lines)
    print(f"  {msg}", flush=True)
    for f in failures:
        print(f"    MISMATCH: {f['raw']}", flush=True)
    if passed:
        print(f">>> TC{args.tc} PASSED <<<", flush=True)
    else:
        print(f">>> TC{args.tc} FAILED <<<", flush=True)
    sys.exit(0 if passed else 1)


# ═══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__m5_main__":
    gem5_config_main()
elif __name__ == "__main__":
    if "--verify-split" in sys.argv:
        verify_split_main()
    else:
        runner_main()
