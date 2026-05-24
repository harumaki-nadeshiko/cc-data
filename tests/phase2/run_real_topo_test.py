"""Real topology instantiation test.
Creates N=3, L=2, D=2 Ruby/CHI system and verifies:
  TC-TOPO-1: object count
  TC-TOPO-2: RN-F same-node downstream
  TC-TOPO-3: address classification
  TC-TOPO-4: snoop destinations
"""
import os
import sys

import m5
from m5.objects import *
from m5.util import addToPath

addToPath("../../gem5/configs/")
from ruby.CHI_basic_framework_config import (
    NodeConfig, NodeAddressMap,
    DEFAULT_N, DEFAULT_L, DEFAULT_D, DEFAULT_SEG_SIZE,
)
from ruby.CHI_ubcc_framework import create_ubcc_system

SYS_CLOCK = "2GHz"
NODES = DEFAULT_N
SEG_SIZE = DEFAULT_SEG_SIZE
TOTAL_CPUS = NODES * DEFAULT_L * DEFAULT_D

binary_path = sys.argv[1]

system = System(mem_mode="timing", cache_line_size=64)
system.clk_domain = SrcClockDomain(clock=SYS_CLOCK)
system.clk_domain.voltage_domain = VoltageDomain()

ruby_system = RubySystem(num_of_sequencers=TOTAL_CPUS,
                          number_of_virtual_networks=4)
ruby_system.clk_domain = SrcClockDomain(clock=SYS_CLOCK,
    voltage_domain=system.clk_domain.voltage_domain)
ruby_system.network = SimpleNetwork()

cpus = []
for i in range(TOTAL_CPUS):
    cpu = TimingSimpleCPU(cpu_id=i)
    cpu.clk_domain = SrcClockDomain(clock=SYS_CLOCK,
        voltage_domain=system.clk_domain.voltage_domain)
    cpu.createThreads()
    cpu.createInterruptController()
    node_id = i // (DEFAULT_L * DEFAULT_D)
    process = Process(pid=100 + i)
    process.executable = binary_path
    process.cwd = os.getcwd()
    process.cmd = [binary_path]
    process.phys_pool_id = node_id * 3
    cpu.workload = [process]
    cpus.append(cpu)

system.cpu = cpus
system.workload = SEWorkload.init_compatible(binary_path)

root = Root(full_system=False, system=system)

options = type('O', (), {})()
options.num_cpus = TOTAL_CPUS
options.num_dirs = 1
options.num_l3caches = 3
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

cpu_sequencers, mem_dests, topology = create_ubcc_system(
    options, False, system, [], None, ruby_system, cpus)

ruby_system._cpu_ports = cpu_sequencers
ruby_system._mem_ports = mem_dests
ruby_system._topology = topology

for i, seq in enumerate(cpu_sequencers):
    seq.connectCpuPorts(cpus[i])

system.ruby = ruby_system

pa_local = [0x000000000, 0x28000000, 0x50000000]
all_ranges = []
for nid in range(NODES):
    all_ranges.append(AddrRange(pa_local[nid], size=SEG_SIZE))
    all_ranges.append(AddrRange(nid * SEG_SIZE + 0x08000000, size=SEG_SIZE))
    all_ranges.append(AddrRange(0x10000000 + nid * SEG_SIZE, size=SEG_SIZE))
system.mem_ranges = all_ranges
system.system_port = system.membus.cpu_side_ports

root = Root(full_system=False, system=system)

MAX_PHYS_MEM_SIZE = 12 * SEG_SIZE

m5.instantiate()

print("=" * 60)
print("Real Topology Instantiation Test")
print("=" * 60)

hn_nodes = []
ep_rnf_nodes = []
l_snf_nodes = []
dl_snf_nodes = []
ep_snf_nodes = []
cluster_nodes = []

for nid in range(NODES):
    hn = getattr(ruby_system, f"hnf_node{nid}", None)
    hn_nodes.append(hn)
    ep_rnf = getattr(ruby_system, f"ep_rnf_node{nid}", None)
    ep_rnf_nodes.append(ep_rnf)
    l_snf = getattr(ruby_system, f"l_snf_node{nid}", None)
    l_snf_nodes.append(l_snf)
    dl_snf = getattr(ruby_system, f"dl_snf_node{nid}", None)
    dl_snf_nodes.append(dl_snf)
    ep_snf = getattr(ruby_system, f"ep_snf_node{nid}", None)
    ep_snf_nodes.append(ep_snf)
    for ci in range(DEFAULT_D):
        cl = getattr(ruby_system, f"cluster_n{nid}_c{ci}", None)
        cluster_nodes.append((nid, ci, cl))

print(f"TC-TOPO-1: HN={len(hn_nodes)} L_SNF={len(l_snf_nodes)} "
      f"DL_SNF={len(dl_snf_nodes)} EP_RNF={len(ep_rnf_nodes)} "
      f"EP_SNF={len(ep_snf_nodes)} Clusters={len(cluster_nodes)}")
assert len(hn_nodes) == NODES, f"Expected {NODES} HN, got {len(hn_nodes)}"
assert len(cluster_nodes) == NODES * DEFAULT_D, f"Expected {NODES*DEFAULT_D} clusters"

print(f"TC-TOPO-2: RN-F downstream check")
for nid, ci, cl in cluster_nodes:
    ll_cntrls = cl._ll_cntrls
    for c in ll_cntrls:
        dests = getattr(c, 'downstream_destinations', [])
        assert len(dests) == 1, \
            f"CL_{{{nid},{ci}}} downstream: expected 1, got {len(dests)}"
        hn_c = hn_nodes[nid]._cntrl
        assert dests[0] is hn_c, \
            f"CL_{{{nid},{ci}}} downstream not same-node HN"

print(f"TC-TOPO-2 PASS: all clusters downstream -> same-node HN only")

print(f"TC-TOPO-3: address classification")
addr_map = NodeAddressMap(NODES, SEG_SIZE)
for nid in range(NODES):
    assert addr_map.isDsm(addr_map.dsmBase() + nid * SEG_SIZE)
    assert addr_map.homeNode(addr_map.dsmBase() + nid * SEG_SIZE) == nid

print(f"TC-TOPO-3 PASS: homeNode correct for all DSM regions")

print(f"TC-TOPO-4: snoop destinations per node")
for nid in range(NODES):
    hn = hn_nodes[nid]._cntrl
    dests = getattr(hn, 'downstream_destinations', [])
    l_snf_c = l_snf_nodes[nid].getAllControllers()
    dl_snf_c = dl_snf_nodes[nid].getAllControllers()
    ep_snf_c = ep_snf_nodes[nid].getAllControllers()
    assert any(d in dests for d in l_snf_c), f"HN_{nid} missing L_SNF in downstream"
    assert any(d in dests for d in dl_snf_c), f"HN_{nid} missing DL_SNF in downstream"
    assert any(d in dests for d in ep_snf_c), f"HN_{nid} missing EP_SNF in downstream"

print(f"TC-TOPO-4 PASS: all HN downstream include L_SNF, DL_SNF, EP_SNF")

print(f"\nALL TOPO TESTS PASSED")
print(f"System instantiated with N={NODES} L={DEFAULT_L} D={DEFAULT_D}")

exit_event = m5.simulate()
print(f"Simulation ended: {exit_event.getCause()} @ {m5.curTick()}")
sys.exit(0)
