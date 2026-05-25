"""Verifies topology objects were created and wired correctly (Python level).
Does NOT require m5.instantiate() - validates object relationships.
"""
import sys, os
import m5
from m5.objects import *
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../gem5/configs/'))
from ruby.CHI_basic_framework_config import (
    NodeConfig, NodeAddressMap, ClusterCHI_RNF, EPNodeWrapper, HNNodeWrapper,
    DEFAULT_N, DEFAULT_L, DEFAULT_D, DEFAULT_SEG_SIZE,
)
from ruby import CHI_config as chi_defs

NUM = DEFAULT_N; SEG = DEFAULT_SEG_SIZE; CL = NUM * DEFAULT_L * DEFAULT_D

class HNFCache(RubyCache):
    dataAccessLatency = 10; tagAccessLatency = 2; size = "256kB"; assoc = 16

ruby = RubySystem(num_of_sequencers=CL, number_of_virtual_networks=4)
ruby.network = SimpleNetwork()
ruby.clk_domain = SrcClockDomain(clock="2GHz")
ruby.clk_domain.voltage_domain = VoltageDomain()

system = System(mem_mode="timing", cache_line_size=64)
system.clk_domain = SrcClockDomain(clock="2GHz")
system.clk_domain.voltage_domain = VoltageDomain()
system.cpu = [TimingSimpleCPU(cpu_id=i) for i in range(CL)]
for cpu in system.cpu:
    cpu.clk_domain = SrcClockDomain(clock="2GHz", voltage_domain=system.clk_domain.voltage_domain)
    cpu.createThreads(); cpu.createInterruptController()
system.workload = SEWorkload.init_compatible(sys.argv[1])

per_node = {}
for nid in range(NUM):
    nd = {}; cfg = NodeConfig(nid, NUM, SEG); nd['cfg'] = cfg
    nd['hnf_c'] = chi_defs.CHI_HNFController(ruby, HNFCache(), NULL, [cfg.local_private_range])
    nd['hnf_w'] = HNNodeWrapper(ruby); nd['hnf_w'].setController(nd['hnf_c']); nd['hnf_w'].connectController(nd['hnf_c'])
    setattr(ruby, f"hnf_{nid}", nd['hnf_w'])

    nd['l_snf'] = chi_defs.CHI_SNF_MainMem(ruby, None, None); nd['l_snf']._cntrl.addr_ranges = [cfg.local_private_range]
    setattr(ruby, f"l_snf_{nid}", nd['l_snf'])
    nd['dl_snf'] = chi_defs.CHI_SNF_MainMem(ruby, None, None); nd['dl_snf']._cntrl.addr_ranges = [NodeConfig.dsm_range_for(nid, SEG)]
    setattr(ruby, f"dl_snf_{nid}", nd['dl_snf'])

    eb = EPBackend(node_id=nid)
    nd['ep_rnf_c'] = EPRNFController(ruby_system=ruby, node_id=nid, data_channel_size=32, ep_backend=eb)
    nd['ep_rnf_w'] = EPNodeWrapper(ruby); nd['ep_rnf_w'].setController(nd['ep_rnf_c']); nd['ep_rnf_w'].connectController(nd['ep_rnf_c'])
    setattr(ruby, f"ep_rnf_{nid}", nd['ep_rnf_w'])
    nd['ep_snf_c'] = EPSNFController(ruby_system=ruby, node_id=nid, data_channel_size=32, ep_backend=eb)
    nd['ep_snf_w'] = EPNodeWrapper(ruby); nd['ep_snf_w'].setController(nd['ep_snf_c']); nd['ep_snf_w'].connectController(nd['ep_snf_c'])
    setattr(ruby, f"ep_snf_{nid}", nd['ep_snf_w'])

    nd['clusters'] = []
    for ci in range(DEFAULT_D):
        base = nid * DEFAULT_D * DEFAULT_L + ci * DEFAULT_L; cpus = system.cpu[base:base + DEFAULT_L]
        cl = ClusterCHI_RNF(cpus, ruby, 64, l1i_assoc=2, l1d_assoc=2, l1i_size="32kB", l1d_size="32kB", l2_assoc=8, l2_size="256kB")
        cl.addPrivL2Cache(); setattr(ruby, f"cl_n{nid}_c{ci}", cl); nd['clusters'].append(cl)
    per_node[nid] = nd

for nid in range(NUM):
    nd = per_node[nid]
    for cl in nd['clusters']:
        cl.setDownstream([nd['hnf_c']])
for nid in range(NUM):
    nd = per_node[nid]
    sd = list(nd['l_snf'].getAllControllers()) + list(nd['dl_snf'].getAllControllers()) + [nd['ep_snf_c']]
    nd['hnf_w'].setDownstream(sd)

all_c = []
for nid in range(NUM):
    nd = per_node[nid]
    all_c.append(nd['hnf_c']); all_c.extend(nd['l_snf'].getAllControllers()); all_c.extend(nd['dl_snf'].getAllControllers())
    all_c.append(nd['ep_rnf_c']); all_c.append(nd['ep_snf_c'])
    for cl in nd['clusters']: all_c.extend(cl.getAllControllers())
for c in all_c: c.data_channel_size = 32

addr_map = NodeAddressMap(NUM, SEG)

import sys as _sys
tests = 0; passed = 0
def check(name, cond):
    global tests, passed
    tests += 1
    if cond: passed += 1; print(f"  {name}: PASS")
    else: print(f"  {name}: FAIL")

print("=" * 60)
print("TC-TOPO-1: Full-scale object count")
hn_count = sum(1 for n in range(NUM) if hasattr(ruby, f"hnf_{n}"))
cluster_count = sum(1 for n in range(NUM) for c in range(DEFAULT_D)
                    if hasattr(ruby, f"cl_n{n}_c{c}"))
ep_rnf_count = sum(1 for n in range(NUM) if hasattr(ruby, f"ep_rnf_{n}"))
l_snf_count = sum(1 for n in range(NUM) if hasattr(ruby, f"l_snf_{n}"))
dl_snf_count = sum(1 for n in range(NUM) if hasattr(ruby, f"dl_snf_{n}"))
ep_snf_count = sum(1 for n in range(NUM) if hasattr(ruby, f"ep_snf_{n}"))
check("3 HN", hn_count == 3)
check("6 cluster RN-F", cluster_count == 6)
check("3 EP_RNF", ep_rnf_count == 3)
check("3 L_SNF", l_snf_count == 3)
check("3 DL_SNF", dl_snf_count == 3)
check("3 EP_SNF", ep_snf_count == 3)
check("12 total CPUs", len(system.cpu) == 12)

print(f"\nTC-TOPO-2: RN-F same-node downstream")
for nid in range(NUM):
    nd = per_node[nid]
    for ci, cl in enumerate(nd['clusters']):
        for ctrl in cl._ll_cntrls:
            dests = getattr(ctrl, 'downstream_destinations', [])
            ok = len(dests) == 1 and dests[0] is nd['hnf_c']
            check(f"CL_{{{nid},{ci}}} downstream -> HN_{nid}", ok)

print(f"\nTC-TOPO-3: Address classification")
dsm_b = addr_map.dsmLocalBase(0)
for nid in range(NUM):
    pa = dsm_b + nid * SEG
    check(f"DSM_{nid} homeNode={nid} (view=0)", addr_map.homeNode(0, pa) == nid)
    check(f"DSM_{nid} isDsm (view=0)", addr_map.isDsm(0, pa))
for nid in range(NUM):
    cfg = NodeConfig(nid, NUM, SEG)
    check(f"Node{nid} LocalPrivate not DSM", not addr_map.isDsm(nid, cfg.local_private_base))

print(f"\nTC-TOPO-4: HN downstream includes per-node SNF/EP")
for nid in range(NUM):
    nd = per_node[nid]
    dests = getattr(nd['hnf_w']._cntrl, 'downstream_destinations', [])
    ls = nd['l_snf'].getAllControllers(); ds = nd['dl_snf'].getAllControllers()
    check(f"HN_{nid} -> L_SNF", any(d in dests for d in ls))
    check(f"HN_{nid} -> DL_SNF", any(d in dests for d in ds))
    check(f"HN_{nid} -> EP_SNF", nd['ep_snf_c'] in dests)
    local_dests = list(ls) + list(ds) + [nd['ep_snf_c']]
    check(f"HN_{nid} ONLY local downstream",
          len(dests) == len(local_dests) and all(d in local_dests for d in dests))

print(f"\nTC-EP-1: EP creation and node_id")
for nid in range(NUM):
    nd = per_node[nid]
    check(f"EP_RNF_{nid} has_parent", nd['ep_rnf_c'].has_parent())
    check(f"EP_SNF_{nid} has_parent", nd['ep_snf_c'].has_parent())

print(f"\nTC-EP-2: EP wiring - message buffers present")
for nid in range(NUM):
    nd = per_node[nid]
    for name, c in [("EP_RNF", nd['ep_rnf_c']), ("EP_SNF", nd['ep_snf_c'])]:
        for port in ['reqOut','snpOut','rspOut','datOut','reqIn','snpIn','rspIn','datIn']:
            check(f"{name}_{nid}.{port}", hasattr(c, port) and getattr(c, port) is not None)

print(f"\nTC-G-3: Full scale preserved")
check("N=3", NUM == 3); check("L=2", DEFAULT_L == 2); check("D=2", DEFAULT_D == 2)
check("Total CPUs=12", CL == 12)

print(f"\n{'='*60}")
print(f"TOTAL: {passed}/{tests} tests passed")
_sys.exit(0 if passed == tests else 1)
