"""E2E Test Driver for Qi Phase 2.
USAGE (gem5 config mode):
    gem5.opt tests/e2e/test_e2e.py --tc <N>          # Run single test case
    gem5.opt tests/e2e/test_e2e.py --all             # Run TC1-TC4 combined

USAGE (Python runner mode):
    python3 tests/e2e/test_e2e.py --all              # Run all TCs
    python3 tests/e2e/test_e2e.py --tc <N>           # Run single TC
"""

import sys, os, re, subprocess, argparse, tempfile, shutil

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
}

# ── Output parser ─────────────────────────────────────────────────
_RE_READ_VAL = re.compile(
    r"\[READ_VAL\]\s+node=(\d+)\s+home=(\d+)\s+offset=\w+\s+"
    r"expected=(\w+)\s+actual=(\w+)\s+(MATCH|MISMATCH)"
)
_RE_E2E_META = re.compile(r"\[E2E_META\]\s+node=(\d+)\s+test=(\S+)")

def parse_read_vals(lines):
    reads = []
    for line in lines:
        m = _RE_READ_VAL.search(line)
        if m:
            reads.append({
                "node": int(m.group(1)), "home": int(m.group(2)),
                "expected": m.group(3), "actual": m.group(4),
                "verdict": m.group(5), "raw": line.strip(),
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
    if len(reads) != 6:
        return False, f"TC3 FAILED: expected 6 READ_VAL, got {len(reads)}", reads
    mismatches = [r for r in reads if r["verdict"] != "MATCH"]
    if mismatches:
        return False, f"TC3 FAILED: {len(mismatches)} MISMATCH(es)", mismatches
    return True, "TC3 PASSED: 6 reads all MATCH", []

def verify_tc4(reads, lines):
    if len(reads) != 4:
        return False, f"TC4 FAILED: expected 4 READ_VAL, got {len(reads)}", reads
    last = reads[-1]
    if last["node"] != 0:
        return False, f"TC4 FAILED: final read expected Node0, got Node{last['node']}", [last]
    actual = int(last["actual"], 16)
    if actual != 0x3:
        return False, f"TC4 FAILED: final read expected 0x3, got 0x{actual:X}", [last]
    expected_sequence = [(0, 0x1), (1, 0x2), (2, 0x3), (0, 0x3)]
    for i, (r, (exp_n, exp_v)) in enumerate(zip(reads, expected_sequence)):
        if r["node"] != exp_n or int(r["actual"], 16) != exp_v:
            return False, f"TC4 FAILED: step {i+1} mismatch", [r]
    return True, "TC4 PASSED: 4-step ring, final Node0 read 0x3", []


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
    """TC9: Negative test — must detect [FATAL] and produce NO [READ_VAL]."""
    if len(reads) > 0:
        return False, "TC9 FAILED: unexpected [READ_VAL] in negative test", reads
    has_fatal = any("[FATAL]" in line for line in lines)
    if has_fatal:
        return True, "TC9 PASSED: expected [FATAL] detected", []
    return False, "TC9 FAILED: no [FATAL] or rejection signal detected", []


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

VERIFIERS = {
    1: verify_tc1, 2: verify_tc2, 3: verify_tc3, 4: verify_tc4,
    5: verify_tc5, 6: verify_tc6, 7: verify_tc7, 8: verify_tc8,
    9: verify_tc9, 10: verify_tc10,
}

def verify_testcase(tc_id, reads, lines):
    if tc_id in VERIFIERS:
        return VERIFIERS[tc_id](reads, lines)
    return False, f"FAILED: unknown test case TC{tc_id}", []

# ── Compilation ───────────────────────────────────────────────────
def compile_workload(tc_name):
    elf_path = os.path.join(WORKLOAD_DIR, tc_name + ".elf")
    src_path = os.path.join(WORKLOAD_DIR, tc_name + ".c")
    if not os.path.exists(src_path):
        print(f"ERROR: source not found: {src_path}", flush=True)
        return None
    if os.path.exists(elf_path) and os.path.getmtime(src_path) <= os.path.getmtime(elf_path):
        return elf_path
    cc = "aarch64-linux-gnu-gcc"
    cmd = [cc, "-static", "-O0", "-g", "-I", WORKLOAD_DIR, "-o", elf_path, src_path]
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
    _parser.add_argument("--tc", type=int, default=0)
    _parser.add_argument("--all", action="store_true")
    _args, _ = _parser.parse_known_args()

    if _args.tc in TESTCASES:
        tc_name = TESTCASES[_args.tc]
    elif _args.all:
        tc_name = "e2e_tc1_dsm_local"  # Combined mode: use TC1 as base
    else:
        print(f"ERROR: invalid --tc={_args.tc}. Must be 1-10.", flush=True)
        sys.exit(1)

    binary = compile_workload(tc_name)
    if not binary:
        sys.exit(1)

    # ── Build gem5 system ──────────────────────────────────────────
    import m5
    from m5.objects import (
        System, SrcClockDomain, VoltageDomain, RubySystem,
        TimingSimpleCPU, Process, SEWorkload, Root, AddrRange, ArmEmuLinux,
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

    NODES = DEFAULT_N
    TOTAL_CPUS = NODES * DEFAULT_L * DEFAULT_D

    # v25.1: Create Root first so System has parent for proxy resolution.
    root = Root(full_system=False)
    system = System(mem_mode="timing", cache_line_size=64)
    root.system = system
    system.clk_domain = SrcClockDomain(clock="2GHz")
    system.clk_domain.voltage_domain = VoltageDomain()

    # RubySystem is created inside Ruby.create_system — don't pre-create.
    # ruby_system = RubySystem()  # REMOVED

    cpus = []
    for i in range(TOTAL_CPUS):
        cpu = TimingSimpleCPU(cpu_id=i)
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
        node_id = i // (DEFAULT_L * DEFAULT_D)
        # Q2: phys_pool_id selects which MemPool to allocate from.
        # Pool 0,1,2 cover [0,1,2]TiB + 256MiB (node LP+UE ranges).
        # Each process allocates stack/heap from its own node's pool,
        # ensuring the PA is within the node's address space.
        proc = Process(pid=100 + i, phys_pool_id=node_id)
        proc.executable = binary
        proc.cwd = os.getcwd()
        proc.cmd = [binary, str(node_id)]
        cpu.workload = [proc]

    # ── Q2 FIX: Pre-seed system.memories with empty list ────────
    # _early_unproxy_all resolves Self.all for system.memories to []
    # since no DRAM objects exist yet.  Pre-set to empty list so
    # Self.all proxy is not consumed.  The real list is rebuilt after
    # Ruby.create_system().
    system.memories = []
    print(f"[E2E-Q2] Pre-seeded system.memories = [] (bypass Self.all)", flush=True)

    # ── Early proxy resolution (v25.1 workaround) ───────────────
    _early_unproxy_all(root)

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
    options.cpu_type = "TimingSimpleCPU"
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

    # ── Patch v25.1: Skip config_filesystem proxy-triggering call ──
    import common.FileSystemConfig as _fsc
    _fsc.config_filesystem = lambda *a, **kw: None

    # ── Pre-set mem_ranges so Ruby.create_system creates phys_mem ──
    # Ruby.py uses system.mem_ranges[0] to create the backing-store
    # SimpleMemory.  Must cover ALL nodes' address spaces (up to
    # Node2 base + 5*SEG ≈ 2.2 TB) so that self-tests and grant-data
    # population can functional-read any PA.
    _max_pa = (NODES - 1) * (1 << 40) + 5 * DEFAULT_SEG_SIZE
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

    cpu_sequencers = ruby_system._cpu_ports

    for i, seq in enumerate(cpu_sequencers):
        seq.connectCpuPorts(cpus[i])

    # Build proper per-node mem_ranges
    all_ranges = []
    for nid in range(NODES):
        cfg = NodeConfig(nid, NODES, DEFAULT_SEG_SIZE)
        all_ranges.append(cfg.local_private_range)
        all_ranges.append(cfg.ubcc_exclusive_range)
        for hn in range(NODES):
            all_ranges.append(NodeConfig.dsm_range_for(hn, DEFAULT_SEG_SIZE, cfg.phy_base))
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

    # Per-node physical page counters (start at 1 MiB offset)
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

    # Map binary segments: each CPU gets its own copy in its node's PA space
    for _cpu_idx, _cpu in enumerate(cpus):
        _node_id = _cpu_idx // _CPUS_PER_NODE
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

    m5.instantiate()

    print("=" * 60, flush=True)
    print(f"E2E Test: {tc_name}  (nodes={NODES}, CPUs={TOTAL_CPUS})", flush=True)
    print(f"Workload: {binary}", flush=True)
    print("=" * 60, flush=True)

    exit_event = m5.simulate()
    cause = exit_event.getCause()
    print(f"SIM_CAUSE={cause}", flush=True)

    # ── Collect output ─────────────────────────────────────────────
    simout_path = os.path.join(m5.options.outdir, "simout")
    raw_lines = []
    if os.path.exists(simout_path):
        with open(simout_path, "r") as f:
            raw_lines = [line.rstrip("\n") for line in f]

    simerr_path = os.path.join(m5.options.outdir, "simerr")
    if os.path.exists(simerr_path):
        with open(simerr_path, "r") as f:
            raw_lines.extend(line.rstrip("\n") for line in f)

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
    args = parser.parse_args()

    if args.tc:
        tc_list = [args.tc]
    elif args.all:
        tc_list = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
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

        reads = parse_read_vals(raw_lines)
        passed, msg, failures = verify_testcase(tc_id, reads, raw_lines)

        print(f"  {msg}", flush=True)
        for f in failures:
            print(f"    MISMATCH: {f['raw']}", flush=True)
        results[tc_id] = passed

    print(f"\n{'='*60}")
    passed_cnt = sum(1 for v in results.values() if v)
    print(f"Results: {passed_cnt}/{len(results)} test cases passed")
    for tc_id, ok in results.items():
        print(f"  TC{tc_id}: {'PASS' if ok else 'FAIL'}")
    print(f"{'='*60}")
    sys.exit(0 if passed_cnt == len(results) else 1)


# ═══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__m5_main__":
    gem5_config_main()
elif __name__ == "__main__":
    runner_main()
