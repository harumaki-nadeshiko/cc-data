"""Minimal test: direct port binding using connectAllPorts on data_seq.
Tests if basic CPU->RubySequencer port binding works in v25.1.
Uses the test_e2e.py framework but swaps port binding.
"""
import sys, os, re, subprocess
sys.setrecursionlimit(20000)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKLOAD_DIR = os.path.join(SCRIPT_DIR, "workloads")
GEM5_BIN = os.path.join(SCRIPT_DIR, "../../gem5/build/ARM/gem5.opt")

if __name__ == "__m5_main__":
    import m5
    from m5.objects import (
        System, SrcClockDomain, VoltageDomain,
        TimingSimpleCPU, Process, SEWorkload, Root, AddrRange,
    )
    from m5.SimObject import SimObject
    from m5.proxy import isproxy

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
    TOTAL_CPUS = NODES * DEFAULT_L * DEFAULT_D  # 12

    tc_name = "e2e_tc_minimal"
    binary = os.path.join(WORKLOAD_DIR, tc_name + ".elf")

    root = Root(full_system=False)
    system = System(mem_mode="timing", cache_line_size=64)
    root.system = system
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
        cpu.workload = [proc]

    system.memories = []

    # Early proxy resolution (MUST do this for Process parenting)
    stack = [(root, 0)]
    while stack:
        obj, _ = stack.pop()
        if not isinstance(obj, SimObject):
            continue
        try:
            obj.unproxyParams()
        except Exception:
            pass
        for name in sorted(obj._children.keys(), reverse=True):
            child = obj._children[name]
            if hasattr(child, '__iter__') and not isinstance(child, (str, bytes)):
                for c in reversed(list(child)):
                    if isinstance(c, SimObject):
                        stack.append((c, 0))
            elif isinstance(child, SimObject):
                stack.append((child, 0))

    class O:
        pass
    options = O()
    options.num_cpus = TOTAL_CPUS; options.num_dirs = 1
    options.num_l3caches = NODES; options.l3_size = "256kB"
    options.l3_assoc = 16; options.cacheline_size = 64
    options.topology = "Crossbar"; options.network = "simple"
    options.router_latency = 1; options.router_link_latency = 1
    options.node_link_latency = 1; options.enable_dvm = False
    options.chi_config = None; options.access_backing_store = True
    options.enable_dram_powerdown = False; options.protocol = "CHI"
    options.cpu_type = "TimingSimpleCPU"
    options.simple_physical_channels = []; options.vcs_per_vnet = 1
    options.mesh_rows = 1; options.routing_algorithm = 0
    options.garnet_deadlock_threshold = 50000; options.xor_low_bit = 0
    options.network_fault_model = False; options.cross_links = []
    options.cross_link_latency = 0; options.mem_type = "SimpleMemory"
    options.mem_channels = 1; options.mem_channels_intlv = 128
    options.link_latency = 1; options.link_width_bits = 128
    options.numa_high_bit = 0

    import common.FileSystemConfig as _fsc
    _fsc.config_filesystem = lambda *a, **kw: None

    _max_pa = (NODES - 1) * (1 << 40) + 5 * DEFAULT_SEG_SIZE
    system.mem_ranges = [AddrRange(0, size=_max_pa)]

    from ruby import Ruby
    Ruby.create_system(options, False, system, None, cpus)
    ruby_system = system.ruby

    cpu_sequencers = ruby_system._cpu_ports

    # === CRITICAL: Use connectAllPorts directly on data_seq ===
    # This bypasses CPUSequencerWrapper.connectCpuPorts
    # and tests if basic port binding works.
    for i, seq in enumerate(cpu_sequencers):
        dseq = seq.data_seq
        # Connect ALL CPU cached/uncached ports to the data sequencer
        cpus[i].connectAllPorts(
            dseq.in_ports, dseq.in_ports, dseq.interrupt_out_port
        )
        print(f"[DIRECT-BIND] CPU{i} → data_seq all ports bound", flush=True)

    all_ranges = []
    for nid in range(NODES):
        cfg = NodeConfig(nid, NODES, DEFAULT_SEG_SIZE)
        all_ranges.append(cfg.local_private_range)
        all_ranges.append(cfg.ubcc_exclusive_range)
        for hn in range(NODES):
            all_ranges.append(NodeConfig.dsm_range_for(hn, DEFAULT_SEG_SIZE, cfg.phy_base))
    system.mem_ranges = all_ranges

    import struct as _struct
    _page_size = 4096; _NODE_ADDR_SHIFT = 40
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
            if _p_type == 1:
                _p_vaddr = _struct.unpack_from('<Q', _phdr, 16)[0]
                _p_memsz = _struct.unpack_from('<Q', _phdr, 40)[0]
                if _p_memsz > 0:
                    _elf_segments.append({'va': _p_vaddr, 'memsz': _p_memsz})

    _total_pages = 0
    for _cpu_idx, _cpu in enumerate(cpus):
        _node_id = _cpu_idx // _CPUS_PER_NODE
        for _proc in _cpu.workload:
            if _proc is None: continue
            for _seg in _elf_segments:
                _va_start = _seg['va'] & ~(_page_size - 1)
                _va_end = (_seg['va'] + _seg['memsz'] + _page_size - 1) & ~(_page_size - 1)
                for _va in range(_va_start, _va_end, _page_size):
                    _pa = _node_pa[_node_id]
                    _node_pa[_node_id] += _page_size
                    _proc.map(_va, _pa, _page_size, cacheable=True)
                    _total_pages += 1

    print(f"[DIRECT-BIND] Pre-mapped {_total_pages} pages", flush=True)

    from m5.objects import AbstractMemory
    _all_memories = [obj for obj in system.descendants()
                      if isinstance(obj, AbstractMemory)]
    if hasattr(ruby_system, 'phys_mem') and ruby_system.phys_mem:
        if ruby_system.phys_mem not in _all_memories:
            _all_memories.append(ruby_system.phys_mem)
    system.memories = _all_memories
    system._values['memories'] = _all_memories

    m5.instantiate()

    print("=" * 60, flush=True)
    print("DIRECT-BIND CPU TEST", flush=True)
    print("=" * 60, flush=True)

    exit_event = m5.simulate()
    cause = exit_event.getCause()
    print(f"SIM_CAUSE={cause}", flush=True)

    for cpu in cpus:
        try:
            print(f"  CPU{cpu.cpu_id}: numCycles={cpu.numCycles} numInsts={cpu.numInsts}",
                  flush=True)
        except Exception:
            pass

    sys.exit(0)
