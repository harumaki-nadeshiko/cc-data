"""Final port connectivity diagnostic: directly checks if CPU↔RubyPort
ports are properly bound at the C++ level after m5.instantiate().
"""
import sys, os
sys.setrecursionlimit(20000)

if __name__ == "__m5_main__":
    import m5
    from m5.objects import (
        System, SrcClockDomain, VoltageDomain,
        TimingSimpleCPU, Process, SEWorkload, Root, AddrRange, RubySystem,
    )
    from m5.SimObject import SimObject

    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    binary = os.path.join(SCRIPT_DIR, "workloads", "e2e_tc_minimal.elf")

    root = Root(full_system=False)
    system = System(mem_mode="timing", cache_line_size=64)
    root.system = system
    system.clk_domain = SrcClockDomain(clock="2GHz")
    system.clk_domain.voltage_domain = VoltageDomain()

    cpu = TimingSimpleCPU(cpu_id=0)
    cpu.clk_domain = SrcClockDomain(clock="2GHz",
        voltage_domain=system.clk_domain.voltage_domain)
    cpu.createThreads()
    cpu.createInterruptController()
    system.cpu = [cpu]
    system.workload = SEWorkload.init_compatible(binary)

    proc = Process(pid=100)
    proc.executable = binary
    proc.cwd = os.getcwd()
    proc.cmd = [binary]
    cpu.workload = [proc]
    system.memories = []

    # Early proxy resolution
    stack = [(root, 0)]
    while stack:
        obj, _ = stack.pop()
        if not isinstance(obj, SimObject): continue
        try: obj.unproxyParams()
        except Exception: pass
        for name in sorted(obj._children.keys(), reverse=True):
            child = obj._children[name]
            if hasattr(child, '__iter__') and not isinstance(child, (str, bytes)):
                for c in reversed(list(child)):
                    if isinstance(c, SimObject): stack.append((c, 0))
            elif isinstance(child, SimObject): stack.append((child, 0))

    # Manual Ruby — just create a minimal Ruby system with one sequencer
    ruby = RubySystem()
    system.ruby = ruby
    ruby.clk_domain = system.clk_domain

    # Create a single RubySequencer
    from m5.objects import RubySequencer
    seq = RubySequencer(version=0, ruby_system=ruby, system=system)
    cpu.data_sequencer = seq
    
    # Pre-map binary pages
    import struct as _struct
    _page_size, _pa_cur = 4096, 0x100000
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
    for _seg in _elf_segments:
        _va_start = _seg['va'] & ~(_page_size - 1)
        _va_end = (_seg['va'] + _seg['memsz'] + _page_size - 1) & ~(_page_size - 1)
        for _va in range(_va_start, _va_end, _page_size):
            proc.map(_va, _pa_cur, _page_size, cacheable=True)
            _pa_cur += _page_size

    # Set mem_ranges to cover the fetch address
    system.mem_ranges = [AddrRange(0, size="4GiB")]

    # Explicit memory list
    ruby.access_backing_store = True
    ruby.phys_mem = __import__('m5.objects', fromlist=['SimpleMemory']).SimpleMemory(
        range=AddrRange(0, size="4GiB"), in_addr_map=False)
    system.memories = [ruby.phys_mem]
    system._values['memories'] = system.memories

    # Connect CPU ports to sequencer using connectAllPorts
    cpu.connectAllPorts(seq.in_ports, seq.in_ports, seq.interrupt_out_port)
    print(f"[PORT-TEST] Ports connected via connectAllPorts", flush=True)

    m5.instantiate()

    print(f"[PORT-TEST] After instantiate", flush=True)

    # Check port connectivity
    import m5.objects as _mo
    # Get the C++ port objects
    try:
        cpu_port = cpu.getPort("icache_port")
        print(f"[PORT-TEST] cpu.getPort('icache_port') = {cpu_port}", flush=True)
        print(f"[PORT-TEST] cpu.icache_port.isConnected() = {cpu_port.isConnected()}", flush=True)
        seq_port = seq.getPort("in_ports", 0)
        print(f"[PORT-TEST] seq.getPort('in_ports',0) = {seq_port}", flush=True)
        print(f"[PORT-TEST] seq port isConnected() = {seq_port.isConnected()}", flush=True)
    except Exception as e:
        print(f"[PORT-TEST] ERROR getting ports: {e}", flush=True)
        import traceback
        traceback.print_exc()

    print(f"[PORT-TEST] Simulating...", flush=True)
    exit_event = m5.simulate()
    print(f"SIM_CAUSE={exit_event.getCause()}", flush=True)
    try:
        print(f"  CPU0: numCycles={cpu.numCycles} numInsts={cpu.numInsts}", flush=True)
    except Exception as e:
        print(f"  CPU0: stats error: {e}", flush=True)

    sys.exit(0)
