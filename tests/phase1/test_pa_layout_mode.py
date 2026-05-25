"""PA layout verification test.
Validates the per-node physical address scheme defined in
  docs/multi-node-pa-layout.md

Key invariants:
  - PHY_BASE_i = i << 40
  - LocalPrivate, UbccExclusive, DSM_k ranges do not overlap within a node
  - Same DSM_k at different nodes maps to different absolute PAs
  - Node i's DSM window starts at PHY_BASE_i + 2*SEG
"""
import sys
import m5
from m5.objects import *
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                '../../gem5/configs/'))
from ruby.CHI_basic_framework_config import (
    NodeConfig, NodeAddressMap, NODE_ADDR_SHIFT,
    DEFAULT_N, DEFAULT_SEG_SIZE,
)

NUM = 3
SEG = DEFAULT_SEG_SIZE

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
print("TC-PA-1: Physical address layout")
print("=" * 60)

for nid in range(NUM):
    check(f"PHY_BASE_{nid} = {nid}<<40",
          NodeConfig(nid).phy_base == (nid << 40))

for nid in range(NUM):
    cfg = NodeConfig(nid)
    check(f"Node{nid} local_private starts at PHY_BASE_{nid}+0",
          cfg.local_private_base == cfg.phy_base)
    check(f"Node{nid} ubcc_exclusive starts at PHY_BASE_{nid}+SEG",
          cfg.ubcc_exclusive_base == cfg.phy_base + SEG)

print("\nTC-PA-2: DSM_k uniqueness across nodes")
for k in range(NUM):
    pas = set()
    for nid in range(NUM):
        pa = NodeConfig.dsm_range_for(k, SEG, NodeConfig(nid).phy_base).start
        pas.add(pa)
    check(f"DSM_{k} has {NUM} distinct PAs across nodes", len(pas) == NUM)

print("\nTC-PA-3: No overlap within a node")
for nid in range(NUM):
    cfg = NodeConfig(nid)
    r_lp = cfg.local_private_range
    r_ue = cfg.ubcc_exclusive_range
    r_d0 = NodeConfig.dsm_range_for(0, SEG, cfg.phy_base)
    r_d1 = NodeConfig.dsm_range_for(1, SEG, cfg.phy_base)
    r_d2 = NodeConfig.dsm_range_for(2, SEG, cfg.phy_base)

    lp_end = int(r_lp.end) if hasattr(r_lp, 'end') else r_lp.size() + int(r_lp.start)
    ue_end = int(r_ue.end) if hasattr(r_ue, 'end') else r_ue.size() + int(r_ue.start)
    d0_s = int(r_d0.start); d0_e = int(r_d0.end)
    d1_s = int(r_d1.start); d1_e = int(r_d1.end)
    d2_s = int(r_d2.start); d2_e = int(r_d2.end)
    lp_s = int(r_lp.start); ue_s = int(r_ue.start)

    check(f"Node{nid} LP vs UE non-overlap",
          lp_end <= ue_s or ue_end <= lp_s)
    check(f"Node{nid} LP vs DSM_0 non-overlap",
          lp_end <= d0_s)
    check(f"Node{nid} UE vs DSM_0 non-overlap",
          ue_end <= d0_s)
    check(f"Node{nid} DSM_0/1/2 non-overlap",
          d0_e <= d1_s and d1_e <= d2_s)

print("\nTC-PA-4: NodeAddressMap classification")
am = NodeAddressMap(NUM, SEG)
for nid in range(NUM):
    check(f"Node{nid} LP not isDsm",
          not am.isDsm(nid, NodeConfig(nid).local_private_base))
    check(f"Node{nid} UE not isDsm",
          not am.isDsm(nid, NodeConfig(nid).ubcc_exclusive_base))
    for k in range(NUM):
        pa = am.dsmLocalBase(nid) + k * SEG
        check(f"Node{nid} DSM_{k} isDsm", am.isDsm(nid, pa))
        check(f"Node{nid} DSM_{k} homeNode={k}", am.homeNode(nid, pa) == k)

print(f"\nTOTAL: {p}/{t} tests passed")
sys.exit(0 if p == t else 1)
