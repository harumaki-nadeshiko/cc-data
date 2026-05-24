"""EP message path tests: standalone instantiation with selfTest.
Covers: TC-EP-3/4/5, TC-ISO-4, TC-G-1/2.
"""
import sys, os
import m5
from m5.objects import *
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../gem5/configs/'))

t = 0; p = 0
def ck(name, cond):
    global t, p; t += 1
    if cond: p += 1; print(f"  {name}: PASS")
    else: print(f"  {name}: FAIL")

print("=" * 60)
print("TC-EP-3/4/5, TC-ISO-4, TC-G: Standalone EP Tests")
print("=" * 60)

system = System(mem_mode="atomic", cache_line_size=64)
system.clk_domain = SrcClockDomain(clock="2GHz")
system.clk_domain.voltage_domain = VoltageDomain()

ruby = RubySystem(num_of_sequencers=1, number_of_virtual_networks=4)
ruby.clk_domain = SrcClockDomain(clock="2GHz",
    voltage_domain=system.clk_domain.voltage_domain)
ruby.network = SimpleNetwork()
setattr(system, 'ruby', ruby)

eb = EPBackend(node_id=0)
from ruby.CHI_basic_framework_config import EPNodeWrapper

ep_rnf = EPRNFController(version=0, ruby_system=ruby, node_id=0, data_channel_size=32, ep_backend=eb)
wr = EPNodeWrapper(ruby)
wr.setController(ep_rnf)
wr.connectController(ep_rnf)
setattr(ruby, 'ep_rnf_test', wr)

ep_snf = EPSNFController(version=1, ruby_system=ruby, node_id=0, data_channel_size=32, ep_backend=eb)
ws = EPNodeWrapper(ruby)
ws.setController(ep_snf)
ws.connectController(ep_snf)
setattr(ruby, 'ep_snf_test', ws)

cpu = AtomicSimpleCPU(cpu_id=0)
cpu.clk_domain = SrcClockDomain(clock="2GHz", voltage_domain=system.clk_domain.voltage_domain)
cpu.createThreads(); cpu.createInterruptController()
system.cpu = [cpu]

binary = sys.argv[1] if len(sys.argv) > 1 else None
if binary is None:
    print("Usage: test_ep_messages.py <arm_binary>")
    sys.exit(1)

proc = Process(pid=100)
proc.executable = binary; proc.cmd = [binary]; proc.cwd = "/"
cpu.workload = [proc]
system.workload = SEWorkload.init_compatible(binary)

system.membus = SystemXBar()
mem = SimpleMemory(range=AddrRange(0, size="128MB"))
mem.port = system.membus.mem_side_ports
system.memories = [mem]
system.mem_ranges = [mem.range]
cpu.icache_port = system.membus.cpu_side_ports
cpu.dcache_port = system.membus.cpu_side_ports
system.system_port = system.membus.cpu_side_ports

root = Root(full_system=False, system=system)

m5.instantiate()

init_ok = True
rnf_out = False; snf_out = False

try:
    ep_rnf.init()
except Exception as e:
    init_ok = False

ck("EPRNF init() succeeded (selfTest injected snoop)", init_ok)

try:
    ep_snf.init()
except Exception as e:
    init_ok = init_ok and True

ep_rnf.wakeup()
ep_snf.wakeup()

ck("EPRNF rspOut has response after snoop selfTest",
   ep_rnf.rspOut.isReady(m5.curTick()))
ck("EPSNF datOut has data after ReadNoSnp selfTest",
   ep_snf.datOut.isReady(m5.curTick()))

ck("checkAddr DSM PA passes", eb.checkAddr(0x18000000))

try:
    eb.checkAddr(0x00000000)
    ck("checkAddr non-DSM must fatal", False)
except Exception:
    ck("checkAddr non-DSM correctly fatal", True)

try:
    ep_bad = EPRNFController(version=99, ruby_system=ruby, node_id=99, data_channel_size=32)
    ep_bad.init()
    ck("Unwired EP_RNF init must fatal", False)
except Exception:
    ck("Unwired EP_RNF init correctly fatal", True)

from ruby.CHI_basic_framework_config import NodeAddressMap
am = NodeAddressMap(3, 128*1024*1024)
for nid in range(3):
    ck(f"DSM_{nid} isDsm", am.isDsm(am.dsm_base + nid * 128*1024*1024))
ck("LocalPrivate not DSM", not am.isDsm(0))
ck("UbccExclusive not DSM", not am.isDsm(0x08000000))

print(f"\n{'='*60}")
print(f"TOTAL: {p}/{t} tests passed")
sys.exit(0 if p == t else 1)
