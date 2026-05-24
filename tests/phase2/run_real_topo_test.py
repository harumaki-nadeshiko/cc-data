"""Real topology instantiation via proper Ruby flow.
"""
import sys, os
import m5
from m5.objects import *
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../gem5/configs/'))
from ruby.CHI_basic_framework_config import DEFAULT_N, DEFAULT_L, DEFAULT_D, DEFAULT_SEG_SIZE

NUM=3; SEG=128*1024*1024; CL=NUM*DEFAULT_L*DEFAULT_D
binary = sys.argv[1]

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

mem=SimpleMemory(range=AddrRange(0,size="384MB"))
mem.port=system.membus.mem_side_ports
system.memories=[mem]; system.mem_ranges=[mem.range]

import ruby.CHI as chi_module
from ruby.CHI_ubcc_framework import create_ubcc_system
chi_module.create_system = create_ubcc_system

from ruby import Ruby

class O:
    num_cpus=CL; num_dirs=1; num_l3caches=3
    l3_size="256kB"; l3_assoc=16; cacheline_size=64
    topology="Crossbar"; network="simple"
    router_latency=1; router_link_latency=1; node_link_latency=1
    link_latency=1; enable_dvm=False; chi_config=None
    numa_high_bit=0; mem_type="SimpleMemory"; access_backing_store=False
    enable_dram_powerdown=False; protocol="CHI"
    cpu_type="TimingSimpleCPU"; tlm_data=""; tlm_errors=""

Ruby.create_system(O(), False, system, [], None, cpus)

ruby = system.ruby

root = Root(full_system=False, system=system)

m5.instantiate()
print(f"INSTANTIATE OK: N={NUM} L={DEFAULT_L} D={DEFAULT_D}")
sys.exit(0)
