"""Minimal CPU execution test — verifies TimingSimpleCPU can execute
at least one instruction when connected to Ruby CHI sequencers.

Usage:
  gem5.opt --debug-flags=SimpleCPU tests/e2e/test_minimal.py
"""

import sys, os, re, subprocess

sys.setrecursionlimit(20000)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GEM5_BIN = os.path.join(SCRIPT_DIR, "../../gem5/build/ARM/gem5.opt")
WORKLOAD_DIR = os.path.join(SCRIPT_DIR, "workloads")

if __name__ == "__m5_main__":
    import m5
    from m5.objects import (
        System, SrcClockDomain, VoltageDomain, RubySystem,
        TimingSimpleCPU, Process, SEWorkload, Root, AddrRange,
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

    binary = os.path.join(WORKLOAD_DIR, "e2e_tc_minimal.elf")
    if not os.path.exists(binary):
        # compile it
        src = os.path.join(WORKLOAD_DIR, "e2e_tc_minimal.c")
        cmd = ["aarch64-linux-gnu-gcc", "-static", "-O0", "-g",
               "-nostartfiles", "-o", binary, src]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"FATAL: compile failed: {proc.stderr}")
            sys.exit(1)

    root = Root(full_system=False)
    system = System(mem_mode="timing", cache_line_size=64)
    root.system = system
    system.mmap_using_noreserve = True
    system.clk_domain = SrcClockDomain(clock="2GHz")
    system.clk_domain.voltage_domain = VoltageDomain()

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
    system.workload = SEWorkload.init_compatible(binary)

    for i, cpu in enumerate(cpus):
        node_id = i // (DEFAULT_L * DEFAULT_D)
        proc = Process(pid=100 + i, phys_pool_id=node_id)
        proc.executable = binary
        proc.cwd = os.getcwd()
        proc.cmd = [binary]
        cpu.process = proc
        cpu.workload = [cpu.process]

    # NOTE: Do NOT pre-seed system.memories = [] before Ruby.create_system().
    # The default Self.all proxy auto-collects AbstractMemory objects at
    # instantiation time.  Pre-seeding with [] breaks _system pointer
    # assignment in C++ System::System() for memories created later
    # (e.g., Ruby's phys_mem).

    class O:
        pass
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

    # Patch config_filesystem
    import common.FileSystemConfig as _fsc
    _fsc.config_filesystem = lambda *a, **kw: None

    _max_pa = (NODES - 1) * (1 << 40) + 5 * DEFAULT_SEG_SIZE
    system.mem_ranges = [AddrRange(0, size=_max_pa)]

    print(f"[MINIMAL] Creating Ruby system...", flush=True)

    from ruby import Ruby
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
        all_ranges.extend(cfg.all_local_private_ranges())
        all_ranges.extend(cfg.all_metadata_private_ranges())
        all_ranges.extend(cfg.all_metadata_backstore_ranges())
        for hn in range(NODES):
            all_ranges.append(NodeConfig.dsm_range_for(hn, DEFAULT_SEG_SIZE, cfg.phy_base))
    system.mem_ranges = all_ranges

    # Pre-map binary pages for all CPUs
    import struct as _struct
    _page_size = 4096
    _NODE_ADDR_SHIFT = 40
    _CPUS_PER_NODE = DEFAULT_L * DEFAULT_D

    _node_pa = {}
    for _nid in range(NODES):
        _node_pa[_nid] = (_nid << _NODE_ADDR_SHIFT) + 0x100000

    _elf_segments = []
    with open(binary, 'rb') as _f:
        _e_hdr = _f.read(64)
        _e_phoff = _struct.unpack_from('<Q', _e_hdr, 32)[0]
        _e_phentsize = _struct.unpack_from('<H', _e_hdr, 54)[0]
        _e_phnum = _struct.unpack_from('<H', _e_hdr, 56)[0]
        for _i in range(_e_phnum):
            _f.seek(_e_phoff + _i * _e_phentsize)
            _phdr = _f.read(_e_phentsize)
            _p_type = _struct.unpack_from('<I', _phdr, 0)[0]
            if _p_type == 1:  # PT_LOAD
                _p_vaddr = _struct.unpack_from('<Q', _phdr, 16)[0]
                _p_memsz = _struct.unpack_from('<Q', _phdr, 40)[0]
                if _p_memsz > 0:
                    _elf_segments.append({'va': _p_vaddr, 'memsz': _p_memsz})

    _total_pages = 0
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
                    _proc.defer_map(_va, _pa, _page_size, cacheable=True)
                    _total_pages += 1

    print(f"[MINIMAL] Pre-mapped {_total_pages} pages", flush=True)

    # Collect memories — must explicitly populate system.memories
    # BEFORE m5.instantiate() so that C++ System::physmem receives
    # the correct AbstractMemory list at construction time.
    from m5.objects import AbstractMemory
    _all_memories = [obj for obj in system.descendants()
                      if isinstance(obj, AbstractMemory)]
    if hasattr(ruby_system, 'phys_mem') and ruby_system.phys_mem is not None:
        if ruby_system.phys_mem not in _all_memories:
            _all_memories.append(ruby_system.phys_mem)
    system.memories = _all_memories
    print(f"[MINIMAL] system.memories: {len(system.memories)} objects", flush=True)
    for m in system.memories:
        try:
            mr = m.range
        except Exception:
            mr = "<error>"
        print(f"  MEM: {m} range={mr} in_addr_map={m.in_addr_map}", flush=True)
    print(f"[MINIMAL] system.mem_ranges={system.mem_ranges}", flush=True)

    if hasattr(ruby_system, 'phys_mem') and ruby_system.phys_mem:
        pm = ruby_system.phys_mem
        print(f"[MINIMAL] Ruby phys_mem: {pm} range={pm.range}", flush=True)

    print(f"[MINIMAL] Instantiating...", flush=True)
    import faulthandler
    faulthandler.enable()
    m5.instantiate()
    print(f"[MINIMAL] Instantiate done!", flush=True)

    print("=" * 60, flush=True)
    print("MINIMAL CPU TEST (SimpleCPU debug)", flush=True)
    print("=" * 60, flush=True)

    exit_event = m5.simulate()
    cause = exit_event.getCause()
    print(f"SIM_CAUSE={cause}", flush=True)

    # Collect output
    simout_path = os.path.join(m5.options.outdir, "simout")
    raw_lines = []
    if os.path.exists(simout_path):
        with open(simout_path, "r") as f:
            raw_lines = [line.rstrip("\n") for line in f]
            for line in raw_lines:
                print(f"  SIMOUT: {line}", flush=True)

    simerr_path = os.path.join(m5.options.outdir, "simerr")
    if os.path.exists(simerr_path):
        with open(simerr_path, "r") as f:
            for line in f:
                line = line.rstrip("\n")
                if "SENTINEL" in line or "SimpleCPU" in line or "ERROR" in line.upper() or "fatal" in line.lower():
                    print(f"  SIMERR: {line}", flush=True)

    # Also dump stat
    for cpu in cpus:
        try:
            print(f"  CPU{cpu.cpu_id}: numCycles={cpu.numCycles} numInsts={cpu.numInsts}",
                  flush=True)
        except Exception:
            pass

    print(f"\n>>> MINIMAL TEST COMPLETE <<<", flush=True)
    sys.exit(0)
