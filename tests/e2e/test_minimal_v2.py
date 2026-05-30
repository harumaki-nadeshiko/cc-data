"""Ultra-minimal test: just boot one TimingSimpleCPU with Ruby/CHI."""
import sys, os
sys.setrecursionlimit(20000)

import m5
from m5.objects import *
m5.util.addToPath('../../gem5/configs/')

system = System(mem_mode="timing", cache_line_size=64)
root = Root(full_system=False, system=system)
system.clk_domain = SrcClockDomain(clock="2GHz")
system.clk_domain.voltage_domain = VoltageDomain()

binary = os.path.join(os.path.dirname(__file__), "workloads/e2e_tc_minimal.elf")
if not os.path.exists(binary):
    src = os.path.join(os.path.dirname(__file__), "workloads/e2e_tc_minimal.c")
    os.system(f"aarch64-linux-gnu-gcc -static -O0 -nostartfiles -o {binary} {src}")

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

# Minimal Ruby params
class O: pass
opts = O()
opts.num_cpus = 1
opts.num_dirs = 1
opts.num_l3caches = 1
opts.l3_size = "256kB"
opts.l3_assoc = 16
opts.cacheline_size = 64
opts.topology = "Crossbar"
opts.network = "simple"
opts.router_latency = 1
opts.router_link_latency = 1
opts.node_link_latency = 1
opts.link_latency = 1
opts.link_width_bits = 128
opts.enable_dvm = False
opts.chi_config = None
opts.access_backing_store = True
opts.enable_dram_powerdown = False
opts.protocol = "CHI"
opts.cpu_type = "TimingSimpleCPU"
opts.mem_type = "SimpleMemory"
opts.mem_channels = 1
opts.mem_channels_intlv = 128
opts.numa_high_bit = 0
opts.vcs_per_vnet = 1
opts.mesh_rows = 1
opts.routing_algorithm = 0
opts.garnet_deadlock_threshold = 50000
opts.xor_low_bit = 0
opts.network_fault_model = False
opts.cross_links = []
opts.cross_link_latency = 0
opts.simple_physical_channels = []

# Patch config_filesystem
import common.FileSystemConfig as _fsc
_fsc.config_filesystem = lambda *a, **kw: None

from ruby import Ruby
Ruby.create_system(opts, False, system, None, system.cpu)
ruby = system.ruby

system.mem_ranges = [AddrRange(0, size="1TB")]
from m5.objects import AbstractMemory
system.memories = [obj for obj in system.descendants() if isinstance(obj, AbstractMemory)]

ruby._cpu_ports[0].connectCpuPorts(cpu)

m5.instantiate()
print("=== Starting simulation ===", flush=True)
exit_event = m5.simulate()
print(f"SIM_CAUSE={exit_event.getCause()}", flush=True)
print("=== Done ===", flush=True)
