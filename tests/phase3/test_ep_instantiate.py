"""Minimal EP controller instantiation with Ruby network wiring.
Uses two EP controllers so Crossbar topology has >1 network node.
"""
import sys, os
import m5
from m5.objects import *
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../gem5/configs/'))
from ruby.CHI_basic_framework_config import EPNodeWrapper
from ruby.Ruby import create_topology

binary = sys.argv[1]

system = System(mem_mode="timing", cache_line_size=64)
system.clk_domain = SrcClockDomain(clock="2GHz")
system.clk_domain.voltage_domain = VoltageDomain()
system.membus = SystemXBar()

cpu = TimingSimpleCPU(cpu_id=0)
cpu.clk_domain = SrcClockDomain(clock="2GHz", voltage_domain=system.clk_domain.voltage_domain)
cpu.createThreads(); cpu.createInterruptController()
cpu.icache_port = system.membus.cpu_side_ports
cpu.dcache_port = system.membus.cpu_side_ports
proc = Process(pid=100); proc.executable=binary; proc.cmd=[binary]; proc.cwd=os.getcwd()
cpu.workload=[proc]
system.cpu=[cpu]
system.workload=SEWorkload.init_compatible(binary)

mem=SimpleMemory(range=AddrRange(0,size="128MB"))
mem.port=system.membus.mem_side_ports
system.memories=[mem]; system.mem_ranges=[mem.range]

ruby = RubySystem(num_of_sequencers=1, number_of_virtual_networks=4)
ruby.clk_domain = SrcClockDomain(clock="2GHz", voltage_domain=system.clk_domain.voltage_domain)

system.ruby = ruby

from network.Network import create_network
net_opts = type('O',(),{'simple_physical_channels':[],'network':'simple',
    'router_latency':1,'link_latency':1,'topology':'Crossbar',
    'link_width_bits':128,'vcs_per_vnet':1,'mesh_rows':1,
    'routing_algorithm':0,'garnet_deadlock_threshold':50000})()
network, IntLink, ExtLink, Router, Interface = create_network(net_opts, ruby)
ruby.network = network
network.number_of_virtual_networks = 4

eb0 = EPBackend(node_id=0)
ep_rnf = EPRNFController(version=0, ruby_system=ruby, node_id=0, data_channel_size=32, ep_backend=eb0)
w0 = EPNodeWrapper(ruby)
w0.setController(ep_rnf)
w0.connectController(ep_rnf)
setattr(ruby, 'ep_rnf_test', w0)

eb1 = EPBackend(node_id=1)
ep_snf = EPSNFController(version=1, ruby_system=ruby, node_id=1, data_channel_size=32, ep_backend=eb1)
w1 = EPNodeWrapper(ruby)
w1.setController(ep_snf)
w1.connectController(ep_snf)
setattr(ruby, 'ep_snf_test', w1)

topo_opts = type('O',(),{'topology':'Crossbar','cross_links':[],'cross_link_latency':0,
    'router_latency':1,'router_link_latency':1,'node_link_latency':1,
    'link_latency':1,'link_width_bits':128,'vcs_per_vnet':1,'mesh_rows':1,
    'routing_algorithm':0,'garnet_deadlock_threshold':50000,
    'network':'simple','simple_physical_channels':[],'network_fault_model':False})()
topology = create_topology([ep_rnf, ep_snf], topo_opts)
topology.makeTopology(topo_opts, network, IntLink, ExtLink, Router)

from network.Network import init_network
init_network(topo_opts, network, Interface)

root = Root(full_system=False, system=system)

m5.instantiate()
print("INSTANTIATE OK: EP_RNF and EP_SNF within Ruby")
print(f"EP_RNF node_id={ep_rnf.node_id}")
print(f"EP_SNF node_id={ep_snf.node_id}")
sys.exit(0)
