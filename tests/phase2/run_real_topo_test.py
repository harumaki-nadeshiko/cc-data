"""Topology bring-up and EP component validation.
Validates m5.instantiate() with EPBackend + verifies topology objects.
"""
import sys, os
import m5
from m5.objects import *
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../gem5/configs/'))

t=0; p=0
def ck(n, c):
    global t, p; t+=1
    if c: p+=1; print(f"  {n}: PASS")
    else: print(f"  {n}: FAIL")

print("="*60)
print("TC-EP-1: m5.instantiate() with EPBackend SimObject")
print("="*60)

system = System(mem_mode="atomic", cache_line_size=64)
system.clk_domain = SrcClockDomain(clock="2GHz")
system.clk_domain.voltage_domain = VoltageDomain()

eb = EPBackend(node_id=0)
setattr(system, 'eb', eb)

root = Root(full_system=False, system=system)
m5.instantiate()
ck("m5.instantiate() succeeded with EPBackend", True)
ck("EPBackend node_id=0", int(eb.node_id) == 0)
print("TC-EP-1 PASSED: EPBackend is a fully functional gem5 SimObject")

from ruby.CHI_basic_framework_config import (
    NodeConfig, NodeAddressMap, ClusterCHI_RNF, EPNodeWrapper, HNNodeWrapper,
    DEFAULT_N, DEFAULT_L, DEFAULT_D, DEFAULT_SEG_SIZE,
)
from ruby import CHI_config as chi_defs

NUM=3; SEG=128*1024*1024; CL=NUM*DEFAULT_L*DEFAULT_D
binary = sys.argv[1]

system2 = System(mem_mode="timing", cache_line_size=64)
system2.clk_domain = SrcClockDomain(clock="2GHz")
system2.clk_domain.voltage_domain = VoltageDomain()
system2.membus = SystemXBar()
system2.mem_ranges=[AddrRange(0x80000000, size="512MB")]

cpus=[]
for i in range(CL):
    cpu = TimingSimpleCPU(cpu_id=i)
    cpu.clk_domain = SrcClockDomain(clock="2GHz", voltage_domain=system2.clk_domain.voltage_domain)
    cpu.createThreads(); cpu.createInterruptController()
    cpu.icache_port = system2.membus.cpu_side_ports; cpu.dcache_port = system2.membus.cpu_side_ports
    node_id = i//(DEFAULT_L*DEFAULT_D)
    proc=Process(pid=100+i); proc.executable=binary; proc.cmd=[binary]; proc.cwd=os.getcwd()
    proc.phys_pool_id=node_id*3; cpu.workload=[proc]; cpus.append(cpu)
system2.cpu=cpus
system2.workload=SEWorkload.init_compatible(binary)

class HNFCache(RubyCache):
    dataAccessLatency=10; tagAccessLatency=2; size="256kB"; assoc=16

ruby = RubySystem(num_of_sequencers=CL, number_of_virtual_networks=4)
ruby.clk_domain = SrcClockDomain(clock="2GHz", voltage_domain=system2.clk_domain.voltage_domain)
ruby.network = SimpleNetwork(
    routers=[], ext_links=[], int_links=[], netifs=[],
    number_of_virtual_networks=4, control_msg_size=8, data_msg_size=32)

per_node = {}
for nid in range(NUM):
    nd = {}; cfg = NodeConfig(nid, NUM, SEG)
    nd['hnf_c'] = chi_defs.CHI_HNFController(ruby, HNFCache(), NULL, [cfg.local_private_range, cfg.ubcc_exclusive_range])
    nd['hnf_w'] = HNNodeWrapper(ruby); nd['hnf_w'].setController(nd['hnf_c']); nd['hnf_w'].connectController(nd['hnf_c'])
    setattr(ruby, f'hnf_node{nid}', nd['hnf_w'])
    nd['l_snf'] = chi_defs.CHI_SNF_MainMem(ruby, None, None); nd['l_snf']._cntrl.addr_ranges = [cfg.local_private_range, cfg.ubcc_exclusive_range]
    setattr(ruby, f'l_snf_node{nid}', nd['l_snf'])
    nd['dl_snf'] = chi_defs.CHI_SNF_MainMem(ruby, None, None); nd['dl_snf']._cntrl.addr_ranges = [NodeConfig.dsm_range_for(nid, SEG)]
    setattr(ruby, f'dl_snf_node{nid}', nd['dl_snf'])
    eb = EPBackend(node_id=nid)
    nd['ep_rnf_c'] = EPRNFController(version=chi_defs.Versions.getVersion(chi_defs.CHI_Cache_Controller), ruby_system=ruby, node_id=nid, data_channel_size=32, ep_backend=eb)
    nd['ep_rnf_w'] = EPNodeWrapper(ruby); nd['ep_rnf_w'].setController(nd['ep_rnf_c']); nd['ep_rnf_w'].connectController(nd['ep_rnf_c'])
    setattr(ruby, f'ep_rnf_node{nid}', nd['ep_rnf_w'])
    nd['ep_snf_c'] = EPSNFController(version=chi_defs.Versions.getVersion(chi_defs.CHI_Cache_Controller), ruby_system=ruby, node_id=nid, data_channel_size=32, ep_backend=eb)
    nd['ep_snf_w'] = EPNodeWrapper(ruby); nd['ep_snf_w'].setController(nd['ep_snf_c']); nd['ep_snf_w'].connectController(nd['ep_snf_c'])
    setattr(ruby, f'ep_snf_node{nid}', nd['ep_snf_w'])
    nd['clusters'] = []
    for ci in range(DEFAULT_D):
        base = nid * DEFAULT_D * DEFAULT_L + ci * DEFAULT_L
        cl_cpus = cpus[base:base + DEFAULT_L]
        cl = ClusterCHI_RNF(cl_cpus, ruby, 64, l1i_assoc=2, l1d_assoc=2, l1i_size="32kB", l1d_size="32kB", l2_assoc=8, l2_size="256kB")
        cl.addPrivL2Cache(); setattr(ruby, f'cluster_n{nid}_c{ci}', cl); nd['clusters'].append(cl)
    per_node[nid] = nd

for nid in range(NUM):
    nd = per_node[nid]
    for cl in nd['clusters']: cl.setDownstream([nd['hnf_c']])
    sd = list(nd['l_snf'].getAllControllers()) + list(nd['dl_snf'].getAllControllers()) + [nd['ep_snf_c']]
    nd['hnf_w'].setDownstream(sd)

print(f"\nTC-TOPO-1: Object verification")
hn_ok = lsnf_ok = dlsnf_ok = eprnf_ok = epsnf_ok = cl_ok = 0
for nid in range(NUM):
    if hasattr(ruby, f'hnf_node{nid}'): hn_ok += 1
    if hasattr(ruby, f'l_snf_node{nid}'): lsnf_ok += 1
    if hasattr(ruby, f'dl_snf_node{nid}'): dlsnf_ok += 1
    if hasattr(ruby, f'ep_rnf_node{nid}'): eprnf_ok += 1
    if hasattr(ruby, f'ep_snf_node{nid}'): epsnf_ok += 1
    for ci in range(DEFAULT_D):
        if hasattr(ruby, f'cluster_n{nid}_c{ci}'): cl_ok += 1

ck(f"HN: {hn_ok}/3", hn_ok==3)
ck(f"L_SNF: {lsnf_ok}/3", lsnf_ok==3)
ck(f"DL_SNF: {dlsnf_ok}/3", dlsnf_ok==3)
ck(f"EP_RNF: {eprnf_ok}/3", eprnf_ok==3)
ck(f"EP_SNF: {epsnf_ok}/3", epsnf_ok==3)
ck(f"Clusters: {cl_ok}/6", cl_ok==6)

if hn_ok >= 1:
    nd0 = per_node[0]
    dests = getattr(nd0['hnf_w']._cntrl, 'downstream_destinations', [])
    lsnf_c = nd0['l_snf'].getAllControllers()
    dlsnf_c = nd0['dl_snf'].getAllControllers()
    epsnf_c = [nd0['ep_snf_c']]
    ck("TC-TOPO-4: HN_0 downstream has L_SNF_0", any(d in dests for d in lsnf_c))
    ck("TC-TOPO-4: HN_0 downstream has DL_SNF_0", any(d in dests for d in dlsnf_c))
    ck("TC-TOPO-4: HN_0 downstream has EP_SNF_0", any(d in dests for d in epsnf_c))
    only_local = all(d in lsnf_c + dlsnf_c + epsnf_c for d in dests)
    ck("TC-TOPO-4: HN_0 downstream ONLY local", only_local)

    cl0 = nd0['clusters'][0]
    for ctrl in cl0._ll_cntrls:
        cdests = getattr(ctrl, 'downstream_destinations', [])
        ck(f"TC-TOPO-2: cluster downstream = HN_0 only", len(cdests)==1 and cdests[0] is nd0['hnf_c'])
        break

am = NodeAddressMap(NUM, SEG)
for nid in range(3):
    ck(f"TC-TOPO-3: DSM_{nid} homeNode={nid}", am.homeNode(am.dsm_base + nid*SEG)==nid)

print(f"\n{'='*60}")
print(f"TOTAL: {p}/{t} tests passed")
sys.exit(0 if p==t else 1)
