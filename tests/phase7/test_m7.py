"""M7: Writeback / Evict / Owner Transfer test suite.

Tests all M7 test cases via C++ self-test embedded in EPBackend::init().
The self-test runs during instantiation and prints results to stdout.

Test Cases:
  TC-M7-1: Dirty Writeback (ARM_SYNC)
  TC-M7-2: Clean Evict (PY_INJECT)
  TC-M7-3: Single Global Owner (ARM_SYNC)
  TC-M7-4: Stale Epoch Rejected (PY_INJECT)
  TC-M7-5: Metadata-Only Home (PY_INJECT)
  TC-M7-6: Recall Result Split (ARM_SYNC)

Usage:
  gem5.opt tests/phase7/test_m7.py <arm_binary>
"""

import sys, os, re, tempfile
import ctypes

if len(sys.argv) < 2:
    print("Usage: gem5.opt tests/phase7/test_m7.py <arm_binary>")
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

tmp_fd, cap_path = tempfile.mkstemp(suffix='.m7capture', text=True)
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

    # ---- Instantiate (triggers M4/M5/M6/M7 all self-tests) ---
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
explicit_pass = "M7_SELF_TEST_PASSED=1" in captured
explicit_fail = "M7_SELF_TEST_FAILED=1" in captured

# Also count M4/M5/M6/M7 results for regression tracking
m4_pass = len(re.findall(r'^  M4 .*: PASS$', captured, re.MULTILINE))
m4_fail = len(re.findall(r'^  M4 .*: FAIL$', captured, re.MULTILINE))
m5_pass = len(re.findall(r'^  M5 .*: PASS$', captured, re.MULTILINE))
m5_fail = len(re.findall(r'^  M5 .*: FAIL$', captured, re.MULTILINE))
m6_pass = len(re.findall(r'^  M6 .*: PASS$', captured, re.MULTILINE))
m6_fail = len(re.findall(r'^  M6 .*: FAIL$', captured, re.MULTILINE))
m7_pass = len(re.findall(r'^  M7 .*: PASS$', captured, re.MULTILINE))
m7_fail = len(re.findall(r'^  M7 .*: FAIL$', captured, re.MULTILINE))

print("=" * 70)
print("M7 Self-Test Captured Output:")
print("=" * 70)
print(captured)
print("=" * 70)

# ---- Regression Check ----
if m4_fail > 0:
    print(f"REGRESSION: M4 has {m4_fail} FAIL(s)")
if m5_fail > 0:
    print(f"REGRESSION: M5 has {m5_fail} FAIL(s)")
if m6_fail > 0:
    print(f"REGRESSION: M6 has {m6_fail} FAIL(s)")

# ---- Structured Results -----------------------------------------------
print(f"\nM4 Self-Test: {m4_pass} PASS, {m4_fail} FAIL")
print(f"M5 Self-Test: {m5_pass} PASS, {m5_fail} FAIL")
print(f"M6 Self-Test: {m6_pass} PASS, {m6_fail} FAIL")
print(f"M7 Self-Test: {m7_pass} PASS, {m7_fail} FAIL")
print(f"Total C++:   {pass_count} PASS, {fail_count} FAIL, {skip_count} SKIP")
print(f"M7 PASSED=1: {explicit_pass}   M7 FAILED=1: {explicit_fail}")

# ---- Test Case Coverage -----------------------------------------------
print("\n--- M7 Test Case Coverage ---")

# TC-M7-1: Dirty Writeback
tc_m71 = all(x in captured for x in ["M7-1-1", "M7-1-2", "M7-1-3",
                                      "M7-1-4", "M7-1-5", "M7-1-6"])
print(f"TC-M7-1 (Dirty Writeback):               {'PASS' if tc_m71 else 'INCOMPLETE'}")

# TC-M7-2: Clean Evict
tc_m72 = all(x in captured for x in ["M7-2-1", "M7-2-2", "M7-2-3",
                                      "M7-2-4", "M7-2-5", "M7-2-ext"])
print(f"TC-M7-2 (Clean Evict):                   {'PASS' if tc_m72 else 'INCOMPLETE'}")

# TC-M7-3: Single Global Owner
tc_m73 = all(x in captured for x in ["M7-3-1", "M7-3-2", "M7-3-3",
                                      "M7-3-4", "M7-3-5", "M7-3-6"])
print(f"TC-M7-3 (Single Global Owner):           {'PASS' if tc_m73 else 'INCOMPLETE'}")

# TC-M7-4: Stale Epoch Rejected
tc_m74 = all(x in captured for x in ["M7-4-1", "M7-4-2", "M7-4-3",
                                      "M7-4-4", "M7-4-5", "M7-4-6",
                                      "M7-4-7", "M7-4-8"])
print(f"TC-M7-4 (Stale Epoch Rejected):          {'PASS' if tc_m74 else 'INCOMPLETE'}")

# TC-M7-5: Metadata-Only Home
tc_m75 = all(x in captured for x in ["M7-5-1", "M7-5-2", "M7-5-3", "M7-5-4"])
print(f"TC-M7-5 (Metadata-Only Home):            {'PASS' if tc_m75 else 'INCOMPLETE'}")

# TC-M7-6: Recall Result Split
tc_m76 = all(x in captured for x in ["M7-6a-1", "M7-6a-2", "M7-6a-3",
                                      "M7-6a-4", "M7-6a-5",
                                      "M7-6b-1", "M7-6b-2", "M7-6b-3",
                                      "M7-6b-4", "M7-6b-5"])
print(f"TC-M7-6 (Recall Result Split):           {'PASS' if tc_m76 else 'INCOMPLETE'}")

# ---- Final Gate Decision ---------------------------------------------
print("\n" + "=" * 70)

# Regression check: M4/M5/M6 must be clean
if m4_fail > 0:
    print(f"M7_GATE: FAIL (M4 regression: {m4_fail} failures)")
    sys.exit(1)

if m5_fail > 0:
    print(f"M7_GATE: FAIL (M5 regression: {m5_fail} failures)")
    sys.exit(1)

if m6_fail > 0:
    print(f"M7_GATE: FAIL (M6 regression: {m6_fail} failures)")
    sys.exit(1)

# M7-specific checks
if m7_fail > 0:
    print(f"M7_GATE: FAIL (M7: {m7_fail} failures)")
    sys.exit(1)

if explicit_fail:
    print("M7_GATE: FAIL (M7_SELF_TEST_FAILED=1)")
    sys.exit(1)

# Marker missing check
if not explicit_pass and not explicit_fail:
    print("M7_GATE: FAIL (no M7_SELF_TEST_PASSED/FAILED marker — "
          "self-test may not have run)")
    sys.exit(1)

# All checks passed
print("M7_GATE: PASS")
print(f"  M4: {m4_pass} PASS / {m4_fail} FAIL")
print(f"  M5: {m5_pass} PASS / {m5_fail} FAIL")
print(f"  M6: {m6_pass} PASS / {m6_fail} FAIL")
print(f"  M7: {m7_pass} PASS / {m7_fail} FAIL")
print(f"  TC-M7-1 (Dirty Writeback):         {'PASS' if tc_m71 else 'INCOMPLETE'}")
print(f"  TC-M7-2 (Clean Evict):             {'PASS' if tc_m72 else 'INCOMPLETE'}")
print(f"  TC-M7-3 (Single Global Owner):     {'PASS' if tc_m73 else 'INCOMPLETE'}")
print(f"  TC-M7-4 (Stale Epoch):             {'PASS' if tc_m74 else 'INCOMPLETE'}")
print(f"  TC-M7-5 (Metadata-Only):           {'PASS' if tc_m75 else 'INCOMPLETE'}")
print(f"  TC-M7-6 (Recall Result Split):     {'PASS' if tc_m76 else 'INCOMPLETE'}")

# Brief simulation to complete
exit_event = m5.simulate(10000)
print(f"EXIT_CAUSE={exit_event.getCause()}")
sys.exit(0)
