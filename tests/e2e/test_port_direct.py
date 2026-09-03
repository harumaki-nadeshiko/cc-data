"""Direct port binding test - use base Sequencer.connectCpuPorts
to verify if the port binding mechanism works at all.
Creates 1 CPU connected to both inst and data sequencers.
"""
import sys, os
sys.setrecursionlimit(20000)

if __name__ == "__m5_main__":
    import m5
    from m5.objects import (
        System, SrcClockDomain, VoltageDomain,
        TimingSimpleCPU, Process, SEWorkload, Root, AddrRange,
    )

    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    binary = os.path.join(SCRIPT_DIR, "workloads", "e2e_tc_minimal.elf")

    root = Root(full_system=False)
    system = System(cache_line_size=64)  # Use default mem_mode=atomic
    root.system = system
    system.mmap_using_noreserve = True
    system.clk_domain = SrcClockDomain(clock="2GHz")
    system.clk_domain.voltage_domain = VoltageDomain()

    cpu = TimingSimpleCPU(cpu_id=0)
    cpu.clk_domain = SrcClockDomain(
        clock="2GHz",
        voltage_domain=system.clk_domain.voltage_domain)
    cpu.createThreads()
    cpu.createInterruptController()
    system.cpu = [cpu]

    system.workload = SEWorkload.init_compatible(binary)
    proc = Process(pid=100)
    proc.executable = binary
    proc.cwd = os.getcwd()
    proc.cmd = [binary]
    cpu.process = proc
    cpu.workload = [cpu.process]
    # Q2: Ensure Process is a proper child of CPU (v25.1 requirement)
    if not proc.has_parent():
        cpu.add_child("process", proc)

    system.mem_ranges = [AddrRange(0, size="4GiB")]

    # Pre-map binary pages
    import struct as _struct
    _page_size = 4096
    _pa_cur = 0x100000  # 1 MiB offset
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
            proc.defer_map(_va, _pa_cur, _page_size, cacheable=True)
            _pa_cur += _page_size
    print(f"[DIRECT] Pre-mapped pages, final PA=0x{_pa_cur:x}", flush=True)

    m5.instantiate()

    print("=" * 50, flush=True)
    print("DIRECT CPU TEST (no Ruby, atomic mode)", flush=True)
    print("=" * 50, flush=True)

    exit_event = m5.simulate()
    cause = exit_event.getCause()
    print(f"SIM_CAUSE={cause}", flush=True)

    try:
        print(f"  CPU0: numCycles={cpu.numCycles} numInsts={cpu.numInsts}",
              flush=True)
    except Exception as e:
        print(f"  CPU0: stats error: {e}", flush=True)

    simout_path = os.path.join(m5.options.outdir, "simout")
    if os.path.exists(simout_path):
        with open(simout_path) as f:
            for line in f:
                if "SENTINEL" in line:
                    print(f"  SENTINEL FOUND: {line.strip()}", flush=True)

    sys.exit(0)
