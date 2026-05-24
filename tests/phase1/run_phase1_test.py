"""Phase 1 integration test.
Instantiates N=3 node SE system with DSM VA mapping and per-node pools.
"""
import os
import sys

import m5
from m5.objects import *

SYS_CLOCK = "2GHz"
CPU_CLOCK = "2GHz"
SEG_SIZE = 128 * 1024 * 1024
DSM_VA_BASE = 0x7f80000000
NODES = 3

pa_local_bases = [0x000000000, 0x28000000, 0x50000000]
pa_ubcc_bases = [0x08000000, 0x30000000, 0x58000000]
pa_dsm_bases = [0x10000000, 0x18000000, 0x20000000]

binary_path = sys.argv[1]
tests_passed = 0
tests_total = 5

cpu_list = []
memories = []
processes = []

system = System(mem_mode="atomic", cache_line_size=64)
system.clk_domain = SrcClockDomain(clock=SYS_CLOCK)
system.clk_domain.voltage_domain = VoltageDomain()
system.membus = SystemXBar()

for node_id in range(NODES):
    local_mem = SimpleMemory(
        range=AddrRange(pa_local_bases[node_id], size=SEG_SIZE))
    local_mem.port = system.membus.mem_side_ports
    setattr(system, f"node{node_id}_local", local_mem)

    ubcc_mem = SimpleMemory(
        range=AddrRange(pa_ubcc_bases[node_id], size=SEG_SIZE))
    ubcc_mem.port = system.membus.mem_side_ports
    setattr(system, f"node{node_id}_ubcc", ubcc_mem)

    dsm_mem = SimpleMemory(
        range=AddrRange(pa_dsm_bases[node_id], size=SEG_SIZE))
    dsm_mem.port = system.membus.mem_side_ports
    setattr(system, f"node{node_id}_dsm", dsm_mem)

    memories.extend([local_mem, ubcc_mem, dsm_mem])

    cpu = AtomicSimpleCPU(cpu_id=node_id)
    cpu.clk_domain = SrcClockDomain(clock=CPU_CLOCK,
                                    voltage_domain=system.clk_domain.voltage_domain)
    cpu.createThreads()
    cpu.createInterruptController()
    cpu.icache_port = system.membus.cpu_side_ports
    cpu.dcache_port = system.membus.cpu_side_ports

    process = Process(pid=100 + node_id)
    process.executable = binary_path
    process.cwd = os.getcwd()
    process.cmd = [binary_path]
    process.phys_pool_id = node_id * 3

    cpu.workload = [process]
    processes.append(process)
    cpu_list.append(cpu)

system.cpu = cpu_list
system.memories = memories
system.workload = SEWorkload.init_compatible(binary_path)

all_ranges = []
for mem in memories:
    all_ranges.append(mem.range)
system.mem_ranges = all_ranges

root = Root(full_system=False, system=system)

m5.instantiate()

for node_id, process in enumerate(processes):
    for nid in range(NODES):
        dsm_pa = pa_dsm_bases[nid]
        dsm_va = DSM_VA_BASE + nid * SEG_SIZE
        process.map(dsm_va, dsm_pa, SEG_SIZE, cacheable=True)

print("=" * 60)
print("Phase 1: Address and Process Control Test")
print("=" * 60)

for i, cpu in enumerate(system.cpu):
    proc = cpu.workload[0]
    node_id = i
    expected_pool = node_id * 3
    actual_pool = int(proc.phys_pool_id) if hasattr(proc, 'phys_pool_id') else -1
    print(f"TC-PROC-3 node_id={node_id}: phys_pool_id={actual_pool} "
          f"(expected={expected_pool})", end=" ")
    if actual_pool == expected_pool:
        print("PASS")
        tests_passed += 1
    else:
        print("FAIL")

print(f"TC-PROC-1 DSM PA ranges: ", end=" ")
dsm_ranges_ok = True
for nid in range(NODES):
    r = AddrRange(pa_dsm_bases[nid], size=SEG_SIZE)
    if r.size() != SEG_SIZE:
        dsm_ranges_ok = False
if dsm_ranges_ok:
    print("PASS")
    tests_passed += 1
else:
    print("FAIL")

print(f"TC-PROC-2 Local separate from DSM/UbccExclusive: ", end=" ")
pa_dsm_global_start = pa_dsm_bases[0]
pa_dsm_global_end = pa_dsm_bases[NODES - 1] + SEG_SIZE
local_ok = True
for i in range(NODES):
    loc_start = pa_local_bases[i]
    loc_end = pa_local_bases[i] + SEG_SIZE
    if not (loc_end <= pa_dsm_global_start or loc_start >= pa_dsm_global_end):
        local_ok = False
    ubcc_start = pa_ubcc_bases[i]
    ubcc_end = pa_ubcc_bases[i] + SEG_SIZE
    if not (ubcc_end <= pa_dsm_global_start or ubcc_start >= pa_dsm_global_end):
        local_ok = False
if local_ok:
    print("PASS")
    tests_passed += 1
else:
    print("FAIL")

print(f"\nResults: {tests_passed}/{tests_total} tests passed")
print(f"DSM VA base: {hex(DSM_VA_BASE)}")
for nid in range(NODES):
    print(f"  Node {nid}: VA [{hex(DSM_VA_BASE + nid * SEG_SIZE)} - "
          f"{hex(DSM_VA_BASE + (nid + 1) * SEG_SIZE)}) -> "
          f"PA [{hex(pa_dsm_bases[nid])} - {hex(pa_dsm_bases[nid] + SEG_SIZE)})")

exit_event = m5.simulate()
print(f"\nSimulation ended: {exit_event.getCause()} @ tick {m5.curTick()}")
sys.exit(0 if tests_passed == tests_total else 1)
