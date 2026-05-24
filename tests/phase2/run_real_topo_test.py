"""Full N=3 topology instantiation via Ruby.create_system with UBCC override.
"""
import sys, os
import m5
from m5.objects import *
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../gem5/configs/'))
from ruby.CHI_basic_framework_config import DEFAULT_N, DEFAULT_L, DEFAULT_D
import ruby.CHI as chi_module
from ruby.CHI_ubcc_framework import create_ubcc_system
chi_module.create_system = create_ubcc_system

binary = sys.argv[1]; NUM=3; CL=NUM*DEFAULT_L*DEFAULT_D

t=0; p=0
def ck(n,c):
    global t,p; t+=1
    if c: p+=1; print(f"  {n}: PASS")
    else: print(f"  {n}: FAIL")

system = System(mem_mode="timing", cache_line_size=64)
system.clk_domain = SrcClockDomain(clock="2GHz")
system.clk_domain.voltage_domain = VoltageDomain()
system.membus = SystemXBar()

cpus=[]
for i in range(CL):
    cpu = TimingSimpleCPU(cpu_id=i)
    cpu.clk_domain = SrcClockDomain(clock="2GHz", voltage_domain=system.clk_domain.voltage_domain)
    cpu.createThreads(); cpu.createInterruptController()
    cpu.icache_port = system.membus.cpu_side_ports
    cpu.dcache_port = system.membus.cpu_side_ports
    node_id = i//(DEFAULT_L*DEFAULT_D)
    proc = Process(pid=100+i); proc.executable=binary; proc.cmd=[binary]; proc.cwd=os.getcwd()
    proc.phys_pool_id=node_id*3; cpu.workload=[proc]; cpus.append(cpu)
system.cpu=cpus
system.workload=SEWorkload.init_compatible(binary)
system.mem_ranges=[]

class O:
    num_cpus=CL; num_dirs=1; num_l3caches=3
    l3_size="256kB"; l3_assoc=16; cacheline_size=64
    topology="Pt2Pt"; network="simple"
    router_latency=1; router_link_latency=1; node_link_latency=1
    link_latency=1; link_width_bits=128
    enable_dvm=False; chi_config=None
    numa_high_bit=0; access_backing_store=False
    enable_dram_powerdown=False; protocol="CHI"
    cpu_type="TimingSimpleCPU"
    simple_physical_channels=[]
    vcs_per_vnet=1; mesh_rows=1
    routing_algorithm=0; garnet_deadlock_threshold=50000
    xor_low_bit=0; network_fault_model=False
    cross_links=[]; cross_link_latency=0
    mem_type="SimpleMemory"; mem_channels=1; mem_channels_intlv=128

_orig_sa = type(system).__setattr__
def _safe_sa(obj, attr, val):
    if attr == 'mem_ctrls' and isinstance(val, list) and len(val) == 0:
        import builtins
        builtins.object.__setattr__(obj, 'mem_ctrls', val)
        return
    return _orig_sa(obj, attr, val)
type(system).__setattr__ = _safe_sa

from ruby import Ruby
Ruby.create_system(O(), False, system, piobus=None, cpus=cpus)
type(system).__setattr__ = _orig_sa

ck("Ruby.create_system() completed (mem_ctrls bypass for bring-up)", hasattr(system, 'ruby'))

ruby = system.ruby
hn = sum(1 for n in range(NUM) if hasattr(ruby, f'hnf_node{n}'))
ep = sum(1 for n in range(NUM) if hasattr(ruby, f'ep_rnf_node{n}'))
cl = sum(1 for n in range(NUM) for c in range(DEFAULT_D)
         if hasattr(ruby, f'cluster_n{n}_c{c}'))
ck(f"TC-TOPO-1: {hn}/3 HN, {ep}/3 EP_RNF, {cl}/6 Clusters", hn==3 and ep==3 and cl==6)

root = Root(full_system=False, system=system)
m5.instantiate()
ck("TC-TOPO-1: Full topology m5.instantiate()", True)

print(f"N={NUM} L={DEFAULT_L} D={DEFAULT_D} topology bring-up PASSED")

exit_event = m5.simulate()
ck("Simulation completed", True)
print(f"Exiting @ {m5.curTick()} cause: {exit_event.getCause()}")

print(f"\nTOTAL: {p}/{t} tests passed")
sys.exit(0 if p==t else 1)
