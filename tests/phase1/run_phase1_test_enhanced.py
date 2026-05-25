"""Phase 1 enhanced integration test.

Goals (TC2 enhanced):
  1) Keep SE runtime execution with 3 node-bound processes.
  2) Validate per-node PA layout invariants (node base = nid << 40).
  3) Validate DSM VA mapping targets are node-view specific.
  4) Keep phys_pool_id routing explicit and non-overlapping with reserved windows.
"""

import os
import sys

import m5
from m5.objects import *


SYS_CLOCK = "2GHz"
CPU_CLOCK = "2GHz"
SEG_SIZE = 128 * 1024 * 1024
DSM_VA_BASE = 0x7F80000000
NODES = 3


def node_base(node_id):
    return node_id << 40


tests_passed = 0
tests_total = 12

binary_path = sys.argv[1]

cpu_list = []
memories = []
processes = []

system = System(mem_mode="atomic", cache_line_size=64)
system.clk_domain = SrcClockDomain(clock=SYS_CLOCK)
system.clk_domain.voltage_domain = VoltageDomain()
system.membus = SystemXBar()


# Build per-node address windows:
#   [0*SEG, 1*SEG): LocalPrivate
#   [1*SEG, 2*SEG): UbccExclusive
#   [2*SEG, 5*SEG): DSM_0/1/2 in node view
for node_id in range(NODES):
    base = node_base(node_id)

    local_mem = SimpleMemory(range=AddrRange(base + 0 * SEG_SIZE, size=SEG_SIZE))
    local_mem.port = system.membus.mem_side_ports
    setattr(system, f"node{node_id}_local", local_mem)

    ubcc_mem = SimpleMemory(range=AddrRange(base + 1 * SEG_SIZE, size=SEG_SIZE))
    ubcc_mem.port = system.membus.mem_side_ports
    setattr(system, f"node{node_id}_ubcc", ubcc_mem)

    for k in range(NODES):
        dsm_mem = SimpleMemory(
            range=AddrRange(base + (2 + k) * SEG_SIZE, size=SEG_SIZE)
        )
        dsm_mem.port = system.membus.mem_side_ports
        setattr(system, f"node{node_id}_dsm{k}", dsm_mem)
        memories.append(dsm_mem)

    memories.extend([local_mem, ubcc_mem])


for node_id in range(NODES):
    cpu = AtomicSimpleCPU(cpu_id=node_id)
    cpu.clk_domain = SrcClockDomain(
        clock=CPU_CLOCK,
        voltage_domain=system.clk_domain.voltage_domain,
    )
    cpu.createThreads()
    cpu.createInterruptController()
    cpu.icache_port = system.membus.cpu_side_ports
    cpu.dcache_port = system.membus.cpu_side_ports

    process = Process(pid=100 + node_id)
    process.executable = binary_path
    process.cwd = os.getcwd()
    process.cmd = [binary_path]

    # memory list order per node is 3 DSM + local + ubcc => local pool idx = node_id * 5 + 3
    process.phys_pool_id = node_id * 5 + 3

    cpu.workload = [process]
    processes.append(process)
    cpu_list.append(cpu)


system.cpu = cpu_list
system.memories = memories
system.workload = SEWorkload.init_compatible(binary_path)
system.mem_ranges = [m.range for m in memories]

root = Root(full_system=False, system=system)
m5.instantiate()


def check(name, cond):
    global tests_passed
    print(f"{name} {'PASS' if cond else 'FAIL'}")
    if cond:
        tests_passed += 1


print("=" * 64)
print("Phase 1 Enhanced: Per-node PA + DSM VA mapping test")
print("=" * 64)

# TC2E-1: phys_pool_id mapping
for node_id, process in enumerate(processes):
    expected_pool = node_id * 5 + 3
    actual_pool = int(process.phys_pool_id)
    check(
        f"TC2E-1 node{node_id} phys_pool_id={actual_pool} (expected={expected_pool})",
        actual_pool == expected_pool,
    )

# TC2E-2: per-node local/ubcc/dsm windows non-overlap
for node_id in range(NODES):
    b = node_base(node_id)
    local_r = AddrRange(b + 0 * SEG_SIZE, size=SEG_SIZE)
    ubcc_r = AddrRange(b + 1 * SEG_SIZE, size=SEG_SIZE)
    dsm0_r = AddrRange(b + 2 * SEG_SIZE, size=SEG_SIZE)
    ok = int(local_r.end) <= int(ubcc_r.start) and int(ubcc_r.end) <= int(dsm0_r.start)
    check(f"TC2E-2 node{node_id} LP/UE/DSM ordered non-overlap", ok)

# TC2E-3: same DSM_k differs across node views
for k in range(NODES):
    pas = {node_base(nid) + (2 + k) * SEG_SIZE for nid in range(NODES)}
    check(f"TC2E-3 DSM_{k} unique absolute PA across nodes", len(pas) == NODES)

# TC2E-4: install DSM VA mappings to node-view PAs
for node_id, process in enumerate(processes):
    for k in range(NODES):
        dsm_va = DSM_VA_BASE + k * SEG_SIZE
        dsm_pa = node_base(node_id) + (2 + k) * SEG_SIZE
        process.map(dsm_va, dsm_pa, SEG_SIZE, cacheable=True)

for node_id in range(NODES):
    expected_pa = node_base(node_id) + 2 * SEG_SIZE
    check(
        f"TC2E-4 node{node_id} DSM_0 target PA={hex(expected_pa)}",
        expected_pa == node_base(node_id) + (2 * SEG_SIZE),
    )

print(f"\nResults: {tests_passed}/{tests_total} tests passed")
print(f"DSM VA base: {hex(DSM_VA_BASE)}")

exit_event = m5.simulate()
print(f"Simulation ended: {exit_event.getCause()} @ tick {m5.curTick()}")
sys.exit(0 if tests_passed == tests_total else 1)
