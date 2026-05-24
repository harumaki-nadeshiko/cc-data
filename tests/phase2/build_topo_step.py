import sys, os
import m5
from m5.objects import *
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../gem5/configs/'))
from ruby.CHI_basic_framework_config import (
    NodeConfig, NodeAddressMap, ClusterCHI_RNF, EPNodeWrapper, HNNodeWrapper,
    DEFAULT_N, DEFAULT_L, DEFAULT_D, DEFAULT_SEG_SIZE,
)
from ruby import CHI_config as chi_defs

print("START")
NUM = DEFAULT_N
SEG = DEFAULT_SEG_SIZE
CL = DEFAULT_L * DEFAULT_D * NUM

class HNFCache(RubyCache):
    dataAccessLatency = 10; tagAccessLatency = 2
    size = "256kB"; assoc = 16

ruby = RubySystem(num_of_sequencers=CL, number_of_virtual_networks=4)
ruby.network = SimpleNetwork()
ruby.clk_domain = SrcClockDomain(clock="2GHz")
ruby.clk_domain.voltage_domain = VoltageDomain()
print(f"ruby OK, network set")

system = System(mem_mode="timing", cache_line_size=64)
system.clk_domain = SrcClockDomain(clock="2GHz")
system.clk_domain.voltage_domain = VoltageDomain()
system.cpu = [TimingSimpleCPU(cpu_id=i) for i in range(CL)]
for i, cpu in enumerate(system.cpu):
    cpu.clk_domain = SrcClockDomain(clock="2GHz",
        voltage_domain=system.clk_domain.voltage_domain)
    cpu.createThreads()
    cpu.createInterruptController()
print(f"created {CL} CPUs")

system.workload = SEWorkload.init_compatible(sys.argv[1])
print(f"workload set")

node_configs = {}
per_node = {}
for nid in range(NUM):
    nd = {}
    cfg = NodeConfig(nid, NUM, SEG)
    nd['cfg'] = cfg

    hnf_cntrl = chi_defs.CHI_HNFController(
        ruby, HNFCache(), NULL, [cfg.local_private_range])
    hnf_w = HNNodeWrapper(ruby)
    hnf_w.setController(hnf_cntrl)
    hnf_w.connectController(hnf_cntrl)
    setattr(ruby, f"hnf_{nid}", hnf_w)
    nd['hnf_w'] = hnf_w
    nd['hnf_c'] = hnf_cntrl
    print(f"  hn_{nid} OK")

    l_snf = chi_defs.CHI_SNF_MainMem(ruby, None, None)
    l_snf._cntrl.addr_ranges = [cfg.local_private_range]
    setattr(ruby, f"l_snf_{nid}", l_snf)
    nd['l_snf'] = l_snf
    print(f"  l_snf_{nid} OK")

    dl_snf = chi_defs.CHI_SNF_MainMem(ruby, None, None)
    dl_snf._cntrl.addr_ranges = [NodeConfig.dsm_range_for(nid, SEG)]
    setattr(ruby, f"dl_snf_{nid}", dl_snf)
    nd['dl_snf'] = dl_snf
    print(f"  dl_snf_{nid} OK")

    eb = EPBackend(node_id=nid)
    ep_rnf = EPRNFController(ruby_system=ruby, node_id=nid,
                              data_channel_size=32, ep_backend=eb)
    ep_rnf_w = EPNodeWrapper(ruby)
    ep_rnf_w.setController(ep_rnf)
    ep_rnf_w.connectController(ep_rnf)
    setattr(ruby, f"ep_rnf_{nid}", ep_rnf_w)
    nd['ep_rnf_w'] = ep_rnf_w
    nd['ep_rnf_c'] = ep_rnf
    print(f"  ep_rnf_{nid} OK")

    ep_snf = EPSNFController(ruby_system=ruby, node_id=nid,
                              data_channel_size=32, ep_backend=eb)
    ep_snf_w = EPNodeWrapper(ruby)
    ep_snf_w.setController(ep_snf)
    ep_snf_w.connectController(ep_snf)
    setattr(ruby, f"ep_snf_{nid}", ep_snf_w)
    nd['ep_snf_w'] = ep_snf_w
    nd['ep_snf_c'] = ep_snf
    print(f"  ep_snf_{nid} OK")

    nd['clusters'] = []
    for ci in range(DEFAULT_D):
        cpus_base = nid * DEFAULT_D * DEFAULT_L + ci * DEFAULT_L
        cpus = system.cpu[cpus_base:cpus_base + DEFAULT_L]
        cl = ClusterCHI_RNF(cpus, ruby, 64,
            l1i_assoc=2, l1d_assoc=2, l1i_size="32kB", l1d_size="32kB",
            l2_assoc=8, l2_size="256kB")
        cl.addPrivL2Cache()
        setattr(ruby, f"cl_n{nid}_c{ci}", cl)
        nd['clusters'].append(cl)
    print(f"  {DEFAULT_D} clusters OK")

    per_node[nid] = nd

for nid in range(NUM):
    nd = per_node[nid]
    hnf_c_list = [nd['hnf_c']]
    for cluster in nd['clusters']:
        cluster.setDownstream(hnf_c_list)
print("downstream set")

for nid in range(NUM):
    nd = per_node[nid]
    snf_dests = list(nd['l_snf'].getAllControllers())
    snf_dests.extend(nd['dl_snf'].getAllControllers())
    snf_dests.append(nd['ep_snf_c'])
    nd['hnf_w'].setDownstream(snf_dests)
print("HN downstream set")

all_cntrls = []
for nid in range(NUM):
    nd = per_node[nid]
    all_cntrls.append(nd['hnf_c'])
    all_cntrls.extend(nd['l_snf'].getAllControllers())
    all_cntrls.extend(nd['dl_snf'].getAllControllers())
    all_cntrls.append(nd['ep_rnf_c'])
    all_cntrls.append(nd['ep_snf_c'])
    for cl in nd['clusters']:
        all_cntrls.extend(cl.getAllControllers())

for c in all_cntrls:
    c.data_channel_size = 32
print("data_channel_size set")

ruby.network.number_of_virtual_networks = 4
ruby.network.control_msg_size = 8
ruby.network.data_msg_size = 32

from ruby.Ruby import create_topology
network_cntrls = []
for nid in range(NUM):
    nd = per_node[nid]
    for cl in nd['clusters']:
        network_cntrls.extend(cl.getNetworkSideControllers())
    network_cntrls.append(nd['hnf_c'])
    network_cntrls.extend(nd['l_snf'].getNetworkSideControllers())
    network_cntrls.extend(nd['dl_snf'].getNetworkSideControllers())
    network_cntrls.append(nd['ep_rnf_c'])
    network_cntrls.append(nd['ep_snf_c'])

opts = type('O',(),{
    'topology':'Crossbar','cross_links':[],'cross_link_latency':0})()
topology = create_topology(network_cntrls, opts)
print("topology OK")

print(f"\nALL CREATED: {NUM} nodes, {NUM*DEFAULT_D} clusters, {CL} CPUs")
print(f"Controllers: {len(all_cntrls)} total")

root = Root(full_system=False, system=system)
system.ruby = ruby

m5.instantiate()
print("INSTANTIATE OK")
sys.exit(0)
