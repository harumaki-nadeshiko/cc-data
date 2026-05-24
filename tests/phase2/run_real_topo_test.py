"""N=3 topology m5.instantiate() test via standard Ruby flow.
"""
import sys, os
import m5
from m5.objects import *
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../gem5/configs/'))
from ruby.CHI_basic_framework_config import DEFAULT_N, DEFAULT_L, DEFAULT_D
import ruby.CHI as chi_module
from ruby.CHI_ubcc_framework import create_ubcc_system
chi_module.create_system = create_ubcc_system

binary = sys.argv[1]
NUM=3; CL=NUM*DEFAULT_L*DEFAULT_D

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
    node_id=i//(DEFAULT_L*DEFAULT_D)
    proc=Process(pid=100+i); proc.executable=binary; proc.cmd=[binary]; proc.cwd=os.getcwd()
    proc.phys_pool_id=node_id*3; cpu.workload=[proc]; cpus.append(cpu)
system.cpu=cpus
system.workload=SEWorkload.init_compatible(binary)

mem=SimpleMemory(range=AddrRange(0x80000000,size="512MB"))
mem.port=system.membus.mem_side_ports
system.memories=[mem]
system.mem_ranges=[mem.range]

class O:
    num_cpus=CL; num_dirs=3; num_l3caches=3
    l3_size="256kB"; l3_assoc=16; cacheline_size=64
    topology="Crossbar"; network="simple"
    router_latency=1; router_link_latency=1; node_link_latency=1
    link_latency=1; link_width_bits=128
    enable_dvm=False; chi_config=None
    numa_high_bit=0; mem_type="SimpleMemory"; access_backing_store=False
    enable_dram_powerdown=False; protocol="CHI"
    cpu_type="TimingSimpleCPU"
    simple_physical_channels=[]
    vcs_per_vnet=1; mesh_rows=1
    routing_algorithm=0; garnet_deadlock_threshold=50000
    xor_low_bit=0; network_fault_model=None
    cross_links=[]; cross_link_latency=0

import ruby.Ruby as rubyrb
_orig_cs = rubyrb.create_system
def _cs_fix(opts, fs, sys, *a, **kw):
    sys._mem_ctrls_skip = True
    return _orig_cs(opts, fs, sys, *a, **kw)
rubyrb.create_system = _cs_fix

_trap = type(system).__setattr__
def _safe_setattr(obj, attr, val):
    if attr == 'mem_ctrls' and getattr(obj, '_mem_ctrls_skip', False):
        obj.__dict__['mem_ctrls'] = val
        return
    _trap(obj, attr, val)
type(system).__setattr__ = _safe_setattr

from ruby import Ruby
Ruby.create_system(O(), False, system, piobus=None, cpus=cpus)
type(system).__setattr__ = _trap
rubyrb.create_system = _orig_cs

root = Root(full_system=False, system=system)
m5.instantiate()

print(f"INSTANTIATE OK: N={NUM} L={DEFAULT_L} D={DEFAULT_D} topology bring-up PASSED")
exit_event = m5.simulate()
print(f"Simulation ended: {exit_event.getCause()} @ {m5.curTick()}")
sys.exit(0)
