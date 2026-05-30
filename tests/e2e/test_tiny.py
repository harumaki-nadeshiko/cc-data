"""Minimal: one CPU, no EP, no UBCC, just Ruby+CHI+TimingSimpleCPU."""
import sys, os
sys.setrecursionlimit(20000)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../gem5/configs/'))

import m5
from m5.objects import *
m5.util.addToPath('../../gem5/configs/')

binary = os.path.join(os.path.dirname(__file__), "workloads/e2e_tc_minimal.elf")

system = System(mem_mode="timing", cache_line_size=64)
root = Root(full_system=False, system=system)
system.clk_domain = SrcClockDomain(clock="2GHz")
system.clk_domain.voltage_domain = VoltageDomain()

cpu = TimingSimpleCPU(cpu_id=0)
cpu.clk_domain = SrcClockDomain(clock="2GHz", voltage_domain=system.clk_domain.voltage_domain)
cpu.createThreads()
cpu.createInterruptController()
system.cpu = [cpu]
system.workload = SEWorkload.init_compatible(binary)
proc = Process(pid=100)
proc.executable = binary
proc.cmd = [binary]
cpu.workload = [proc]

class O: pass
o = O()
o.num_cpus = 1
o.num_dirs = 1
o.num_l3caches = 1
o.l3_size = "256kB"
o.l3_assoc = 16
o.cacheline_size = 64
o.topology = "Crossbar"
o.network = "simple"
o.router_latency = 1
o.router_link_latency = 1
o.link_latency = 1
o.link_width_bits = 128
o.node_link_latency = 1
o.enable_dvm = False
o.chi_config = None
o.access_backing_store = True
o.enable_dram_powerdown = False
o.protocol = "CHI"
o.cpu_type = "TimingSimpleCPU"
o.mem_type = "SimpleMemory"
o.mem_channels = 1
o.mem_channels_intlv = 128
o.numa_high_bit = 0
o.vcs_per_vnet = 1
o.mesh_rows = 1
o.routing_algorithm = 0
o.garnet_deadlock_threshold = 50000
o.xor_low_bit = 0
o.network_fault_model = False
o.cross_links = []
o.cross_link_latency = 0
o.simple_physical_channels = []
o.l1i_size = "32kB"; o.l1i_assoc = 2
o.l1d_size = "32kB"; o.l1d_assoc = 2
o.l2_size = "256kB"; o.l2_assoc = 8

import common.FileSystemConfig as _fsc
_fsc.config_filesystem = lambda *a, **kw: None

from ruby import Ruby
Ruby.create_system(o, False, system, None, system.cpu)
ruby = system.ruby

# Minimal memory ranges
system.mem_ranges = [AddrRange(0, size="1TB")]
system.memories = [obj for obj in system.descendants() if isinstance(obj, AbstractMemory)]

ruby._cpu_ports[0].connectCpuPorts(cpu)

print("[TINY] instantiate...", flush=True)
m5.instantiate()
print("[TINY] simulate...", flush=True)
exit_event = m5.simulate()
print(f"[TINY] SIM_CAUSE={exit_event.getCause()}", flush=True)
