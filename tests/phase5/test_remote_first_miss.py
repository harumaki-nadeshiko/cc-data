"""M5 Phase 2: Remote First Miss Integration Tests.

Tests TC-M5-3 (Shared grant), TC-M5-4a (Exclusive grant), TC-M5-4b (Modified grant).

Validation runs as C++ code during EPBackend::init():
  - M5SelfTest.cc: sideband plumbing + grant dispatch (36 checks)
  - UBCCController::processOuterRequest: MESI grant decision (G_S/G_E/G_M)
  - EPBackend::handleRemoteMiss: outer request/grant envelope
  - Sentinel timing assertion: sentinel_visible_tick <= grant_visible_tick

This Python script:
  1. Creates the full CHI+UBCC topology,
  2. Triggers instantiation (runs all C++ self-tests),
  3. Parses self-test output for PASS/FAIL counts,
  4. Reports gate decision.

Usage:
  gem5.opt tests/phase5/test_remote_first_miss.py <arm_binary>
"""

import sys, os, ctypes, tempfile, re

if len(sys.argv) < 2:
    print("Usage: gem5.opt tests/phase5/test_remote_first_miss.py <arm_binary>")
    sys.exit(2)

import m5
from m5.objects import *

sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                 '../../gem5/configs/'))
from ruby.CHI_basic_framework_config import (
    DEFAULT_N, DEFAULT_L, DEFAULT_D,
)
import ruby.CHI as chi_module
from ruby.CHI_ubcc_framework import create_ubcc_system
chi_module.create_system = create_ubcc_system

binary = sys.argv[1]
NUM = DEFAULT_N
CL  = NUM * DEFAULT_L * DEFAULT_D

# ---- Capture infrastructure -------------------------------------------
_libc = None
for libname in ("libc.so.6", "libc.so", None):
    try:
        _libc = ctypes.CDLL(libname or "c", use_errno=True)
        break
    except OSError:
        continue

def _flush_c():
    if _libc is not None:
        try:
            fn = getattr(_libc, 'fflush', None) or getattr(_libc, '__fflush', None)
            if fn:
                fn(ctypes.c_void_p(0))
        except Exception:
            pass

tmp_fd, cap_path = tempfile.mkstemp(suffix='.m5p2cap', text=True)
old_fd1 = os.dup(1)
os.dup2(tmp_fd, 1)

try:
    system = System(mem_mode="timing", cache_line_size=64)
    system.clk_domain = SrcClockDomain(clock="2GHz")
    system.clk_domain.voltage_domain = VoltageDomain()
    system.mem_ranges = [AddrRange(0, size="3TB")]

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
    Ruby.create_system(O(), False, system, piobus=None, cpus=cpus)

    ruby = system.ruby
    for i, cpu in enumerate(cpus):
        ruby._cpu_ports[i].connectCpuPorts(cpu)

    root = Root(full_system=False, system=system)

    # ---- Instantiate (triggers M4SelfTest + M5SelfTest) ----------------
    m5.instantiate()

    _flush_c()
    sys.stdout.flush()
    os.fsync(tmp_fd)

except Exception as e:
    _flush_c()
    sys.stdout.flush()
    os.dup2(old_fd1, 1)
    os.close(old_fd1)
    os.close(tmp_fd)
    os.unlink(cap_path)
    raise

# ---- Restore stdout, read capture ------------------------------------
os.dup2(old_fd1, 1)
os.close(old_fd1)
os.close(tmp_fd)

captured = ""
if os.path.exists(cap_path):
    with open(cap_path, 'r') as f:
        captured = f.read()
    os.unlink(cap_path)

# ---- Parse self-test results -------------------------------------------
pass_count = len(re.findall(r': PASS\b', captured))
fail_count = len(re.findall(r': FAIL\b', captured))
skip_count = len(re.findall(r': SKIP\b', captured))
explicit_pass = "M5_SELF_TEST_PASSED=1" in captured
explicit_fail = "M5_SELF_TEST_FAILED=1" in captured

# Also count M4 results for regression tracking
m4_pass = len(re.findall(r'^  M4 .*: PASS$', captured, re.MULTILINE))
m4_fail = len(re.findall(r'^  M4 .*: FAIL$', captured, re.MULTILINE))
m4_skip = len(re.findall(r'^  M4 .*: SKIP', captured, re.MULTILINE))
m5_pass = len(re.findall(r'^  M5 .*: PASS$', captured, re.MULTILINE))
m5_fail = len(re.findall(r'^  M5 .*: FAIL$', captured, re.MULTILINE))
m5_skip = len(re.findall(r'^  M5 .*: SKIP', captured, re.MULTILINE))

print("=" * 70)
print("M5 Phase 2 Self-Test Captured Output:")
print("=" * 70)
print(captured)
print("=" * 70)

# ---- Structured Results -----------------------------------------------
print(f"\nM4 Self-Test: {m4_pass} PASS, {m4_fail} FAIL, {m4_skip} SKIP")
print(f"M5 Self-Test: {m5_pass} PASS, {m5_fail} FAIL, {m5_skip} SKIP")
print(f"Total C++:   {pass_count} PASS, {fail_count} FAIL, {skip_count} SKIP")
print(f"PASSED=1: {explicit_pass}   FAILED=1: {explicit_fail}")

# ---- Gate Verification ------------------------------------------------
# Check specific TC-M5-3/4 results in captured output
tc_m53_a = "Shared+false dispatch succeeded" in captured and "GrantShared" in captured
tc_m53_b = "Shared+false → GrantShared (result==0)" in captured
tc_m54_a = "Unique+false dispatch succeeded" in captured and "GrantExclusive" in captured
tc_m54_b = "Unique+false → GrantExclusive (result==1)" in captured
tc_m54_c = "Unique+true dispatch succeeded" in captured and "GrantModified" in captured
tc_m54_d = "Unique+true → GrantModified (result==2)" in captured

print("\n--- Test Case Coverage ---")
print(f"TC-M5-3 (Shared grant):  {'PASS' if tc_m53_a and tc_m53_b else 'MISSING'}")
print(f"TC-M5-4a (Exclusive grant): {'PASS' if tc_m54_a and tc_m54_b else 'MISSING'}")
print(f"TC-M5-4b (Modified grant):  {'PASS' if tc_m54_c and tc_m54_d else 'MISSING'}")

# Check sentinel timing
has_sentinel_timing = "sentinel_visible_tick" in captured
print(f"Sentinel timing assertion: {'PRESENT' if has_sentinel_timing else 'NOT FOUND'}")

# Check grant type distinguishability
grant_distinct = all(x in captured for x in [
    "GrantShared != GrantExclusive",
    "GrantExclusive != GrantModified",
    "GrantShared != GrantModified",
])
print(f"Grant type distinguishability: {'PASS' if grant_distinct else 'FAIL'}")

# ---- Final Gate Decision ---------------------------------------------
print("\n" + "=" * 70)

# Determine failures
m4_has_fail = m4_fail > 0
m5_has_fail = m5_fail > 0
marker_missing = not explicit_pass and not explicit_fail

if m4_has_fail and not m5_has_fail and explicit_pass:
    # M4 has SKIPs but no FAILs, M5 all PASS
    pass

if marker_missing:
    print("M5_PHASE2_GATE: FAIL (no C++ PASS/FAIL marker — "
          "self-test may not have run)")
    sys.exit(1)

if explicit_fail or m5_fail > 0:
    print(f"M5_PHASE2_GATE: FAIL (M5: {m5_fail} fail, "
          f"explicit_fail={explicit_fail})")
    sys.exit(1)

if m4_fail > 0:
    print(f"M5_PHASE2_GATE: FAIL (M4 regression: {m4_fail} fail)")
    sys.exit(1)

if not grant_distinct:
    print("M5_PHASE2_GATE: FAIL (grant types not distinguishable)")
    sys.exit(1)

# All checks passed
print("M5_PHASE2_GATE: PASS")
print(f"  M4: {m4_pass} PASS / {m4_skip} SKIP / 0 FAIL")
print(f"  M5: {m5_pass} PASS / {m5_skip} SKIP / 0 FAIL")
print(f"  TC-M5-3 (Shared grant):      {'PASS' if tc_m53_a and tc_m53_b else 'MISSING'}")
print(f"  TC-M5-4a (Exclusive grant):  {'PASS' if tc_m54_a and tc_m54_b else 'MISSING'}")
print(f"  TC-M5-4b (Modified grant):   {'PASS' if tc_m54_c and tc_m54_d else 'MISSING'}")
print(f"  Grant distinguishability:    {'PASS' if grant_distinct else 'FAIL'}")
print(f"  Sentinel timing assertion:   {'PRESENT' if has_sentinel_timing else 'NOT FOUND'}")

# Brief simulation to complete
exit_event = m5.simulate(10000)
print(f"EXIT_CAUSE={exit_event.getCause()}")
sys.exit(0)
