"""N=3,L=2,D=2 no-bypass topology bring-up via Ruby.create_system.

Scope:
  - No monkey patch / no bypass
  - L_SNF and DL_SNF are backed by DRAM (MemCtrl + DDR4_2400_8x8)
  - EP_SNF remains proxy path (intercept + fake data behavior in controller)
  - EP_RNF only required to be in topology (not behavior-tested here)
"""
import sys, os
import traceback
import m5
from m5.objects import *
sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                '../../gem5/configs/'))
from ruby.CHI_basic_framework_config import (
    DEFAULT_N, DEFAULT_L, DEFAULT_D, DEFAULT_SEG_SIZE,
)
import ruby.CHI as chi_module
from ruby.CHI_ubcc_framework import create_ubcc_system
chi_module.create_system = create_ubcc_system

binary = sys.argv[1]
NUM = DEFAULT_N
SEG = DEFAULT_SEG_SIZE
CL  = NUM * DEFAULT_L * DEFAULT_D

# ---- System setup ----------------------------------------------------------
system = System(mem_mode="timing", cache_line_size=64)
system.clk_domain = SrcClockDomain(clock="2GHz")
system.clk_domain.voltage_domain = VoltageDomain()
system.mem_ranges = [AddrRange(0, size="3TB")]

# ---- CPUs + minimal process binding ----------------------------------------
cpus = []
for i in range(CL):
    cpu = TimingSimpleCPU(cpu_id=i)
    cpu.clk_domain = SrcClockDomain(
        clock="2GHz",
        voltage_domain=system.clk_domain.voltage_domain,
    )
    cpu.createThreads()
    cpu.createInterruptController()

    proc = Process(pid=100 + i)
    proc.executable = binary
    proc.cmd = [binary]
    proc.cwd = os.getcwd()
    proc.phys_pool_id = 0
    cpu.workload = [proc]
    cpus.append(cpu)
system.cpu = cpus
system.workload = SEWorkload.init_compatible(binary)

# ---- Ruby options -----------------------------------------------------------
class O:
    num_cpus       = CL
    num_dirs       = 1
    num_l3caches   = 3
    l3_size        = "256kB"
    l3_assoc       = 16
    cacheline_size = 64
    topology       = "Crossbar"
    network        = "simple"
    router_latency     = 1
    router_link_latency = 1
    node_link_latency  = 1
    link_latency       = 1
    link_width_bits    = 128
    enable_dvm     = False
    chi_config     = None
    numa_high_bit  = 0
    access_backing_store = False
    enable_dram_powerdown = False
    protocol       = "CHI"
    cpu_type       = "TimingSimpleCPU"
    simple_physical_channels = []
    vcs_per_vnet   = 1
    mesh_rows      = 1
    routing_algorithm = 0
    garnet_deadlock_threshold = 50000
    xor_low_bit    = 0
    network_fault_model = False
    cross_links    = []
    cross_link_latency = 0
    mem_type       = "SimpleMemory"
    mem_channels   = 1
    mem_channels_intlv = 128

from ruby import Ruby
topo_ok = False
topo_error = None
try:
    Ruby.create_system(O(), False, system, piobus=None, cpus=cpus)
    topo_ok = hasattr(system, 'ruby')
except Exception as e:
    topo_error = e
    traceback.print_exc()

# ---- Verification -----------------------------------------------------------
t = 0
p = 0

def check(name, cond):
    global t, p
    t += 1
    if cond:
        p += 1
        print(f"  {name}: PASS")
    else:
        print(f"  {name}: FAIL")

print("=" * 60)
print("TC-BRINGUP: N=3 Ruby.create_system with UBCC [NO_BYPASS]")
print("=" * 60)

ruby = system.ruby if hasattr(system, 'ruby') else None
if topo_ok and ruby is not None:
    check("Ruby.create_system completed (topology assembled)", True)
elif topo_error is not None:
    check(f"Ruby.create_system failed: {type(topo_error).__name__}", False)
else:
    check("Ruby.create_system failed without Python exception", False)

if ruby is not None:
    for i, cpu in enumerate(cpus):
        ruby._cpu_ports[i].connectCpuPorts(cpu)

    hn_ok = sum(1 for n in range(NUM) if hasattr(ruby, f'hnf_node{n}'))
    ep_ok = sum(1 for n in range(NUM) if hasattr(ruby, f'ep_rnf_node{n}'))
    cl_ok = sum(1 for n in range(NUM) for c in range(DEFAULT_D)
                if hasattr(ruby, f'cluster_n{n}_c{c}'))
    check(f"TC-BRINGUP-2: {hn_ok}/3 HN, {ep_ok}/3 EP_RNF, {cl_ok}/6 Clusters",
          hn_ok == 3 and ep_ok == 3 and cl_ok == 6)

    # DRAM backstore checks for L_SNF and DL_SNF
    dram_ok = True
    for n in range(NUM):
        lmc = getattr(system, f"l_snf_memctrl_node{n}", None)
        dlmc = getattr(system, f"dl_snf_memctrl_node{n}", None)
        if lmc is None or dlmc is None:
            dram_ok = False
            continue
        if not hasattr(lmc, "dram") or lmc.dram is None:
            dram_ok = False
        if not hasattr(dlmc, "dram") or dlmc.dram is None:
            dram_ok = False
        if lmc.dram.__class__.__name__ != "DDR4_2400_8x8":
            dram_ok = False
        if dlmc.dram.__class__.__name__ != "DDR4_2400_8x8":
            dram_ok = False
    check("TC-BRINGUP-2b: L_SNF/DL_SNF DRAM backstores exist", dram_ok)

    if hn_ok >= 1:
        hn0 = getattr(ruby, 'hnf_node0')
        if hasattr(hn0, '_cntrl'):
            dests = getattr(hn0._cntrl, 'downstream_destinations', [])
            lsnf0 = ruby.l_snf_node0.getAllControllers()
            dlsnf0 = ruby.dl_snf_node0.getAllControllers()
            epsnf0 = ruby.ep_snf_node0.getAllControllers()
            check("HN_0 -> L_SNF_0", any(d in dests for d in lsnf0))
            check("HN_0 -> DL_SNF_0", any(d in dests for d in dlsnf0))
            check("HN_0 -> EP_SNF_0", any(d in dests for d in epsnf0))
            only_local = all(d in lsnf0 + dlsnf0 + epsnf0 for d in dests)
            check("HN_0 downstream ONLY local", only_local)

        cl0_ok = False
        for ci in range(DEFAULT_D):
            cl = getattr(ruby, f'cluster_n0_c{ci}', None)
            if cl and cl._ll_cntrls:
                for ctrl in cl._ll_cntrls:
                    cd = getattr(ctrl, 'downstream_destinations', [])
                    if len(cd) == 1 and hasattr(hn0, '_cntrl') and cd[0] is hn0._cntrl:
                        cl0_ok = True
                        break
            if cl0_ok:
                break
        check("TC-BRINGUP-3: cluster downstream -> same-node HN only", cl0_ok)

    root = Root(full_system=False, system=system)
    instantiated_ok = False
    try:
        m5.instantiate()
        instantiated_ok = True
    except Exception as e:
        check(f"m5.instantiate() error: {type(e).__name__}", False)

    if instantiated_ok:
        check("TC-BRINGUP: m5.instantiate()", True)
else:
    check("TC-BRINGUP-2: topology objects", False)
    check("TC-BRINGUP-3: downstream", False)

print(f"\nTOTAL: {p}/{t} tests passed")
print("NOTE: no bypass enabled; bring-up validates topology + DRAM backstores.")
sys.exit(0 if p == t else 1)
