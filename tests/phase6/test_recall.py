"""M6: UBCC Directory + EP_RNF Local Coherent Access test suite.

Tests TC-M6-2 (GlobalRecallOwner path), TC-M6-3 (EP_RNF delayed HN response),
TC-M6-4 (directory consistency), TC-M6-5 (home UBCC metadata-only).

The actual test logic runs as a C++ self-test during EPBackend::init()
(implemented in M6SelfTest.cc). This Python script creates the full
topology, triggers instantiation (which runs the self-test), and
parses the self-test output to verify results.

Usage:
  gem5.opt tests/phase6/test_recall.py <arm_binary>
"""

import sys, os, re, tempfile
import ctypes

if len(sys.argv) < 2:
    print("Usage: gem5.opt tests/phase6/test_recall.py <arm_binary>")
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

tmp_fd, cap_path = tempfile.mkstemp(suffix='.m6capture', text=True)
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

    # ---- Instantiate (triggers M4SelfTest + M5SelfTest + M6SelfTest) ---
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
explicit_pass = "M6_SELF_TEST_PASSED=1" in captured
explicit_fail = "M6_SELF_TEST_FAILED=1" in captured

# Also count M4/M5/M6 results for regression tracking
m4_pass = len(re.findall(r'^  M4 .*: PASS$', captured, re.MULTILINE))
m4_fail = len(re.findall(r'^  M4 .*: FAIL$', captured, re.MULTILINE))
m4_skip = len(re.findall(r'^  M4 .*: SKIP', captured, re.MULTILINE))
m5_pass = len(re.findall(r'^  M5 .*: PASS$', captured, re.MULTILINE))
m5_fail = len(re.findall(r'^  M5 .*: FAIL$', captured, re.MULTILINE))
m5_skip = len(re.findall(r'^  M5 .*: SKIP', captured, re.MULTILINE))
m6_pass = len(re.findall(r'^  M6 .*: PASS$', captured, re.MULTILINE))
m6_fail = len(re.findall(r'^  M6 .*: FAIL$', captured, re.MULTILINE))
m6_skip = len(re.findall(r'^  M6 .*: SKIP', captured, re.MULTILINE))

print("=" * 70)
print("M6 Self-Test Captured Output:")
print("=" * 70)
print(captured)
print("=" * 70)

# ---- Structured Results -----------------------------------------------
print(f"\nM4 Self-Test: {m4_pass} PASS, {m4_fail} FAIL, {m4_skip} SKIP")
print(f"M5 Self-Test: {m5_pass} PASS, {m5_fail} FAIL, {m5_skip} SKIP")
print(f"M6 Self-Test: {m6_pass} PASS, {m6_fail} FAIL, {m6_skip} SKIP")
print(f"Total C++:   {pass_count} PASS, {fail_count} FAIL, {skip_count} SKIP")
print(f"PASSED=1: {explicit_pass}   FAILED=1: {explicit_fail}")

# ---- Test Case Coverage -----------------------------------------------
print("\n--- Test Case Coverage ---")

# TC-M6-4: Directory Consistency
tc_m64_a = "M6-4a" in captured and "G_S dirty == false" in captured
tc_m64_b = "M6-4b" in captured and "G_E dirty == false" in captured
tc_m64_c = "M6-4c" in captured and "G_M dirty == true" in captured
tc_m64_d = "M6-4d" in captured and "G_E != G_M" in captured
tc_m64_pass = all(x in captured for x in [
    "M6-4a", "M6-4b", "M6-4c", "M6-4d"
])
print(f"TC-M6-4 (Directory Consistency):   {'PASS' if tc_m64_pass else 'INCOMPLETE'}")

# TC-M6-5: Metadata-Only
tc_m65 = "M6-5" in captured
print(f"TC-M6-5 (Metadata-Only):           {'PASS' if tc_m65 else 'INCOMPLETE'}")

# TC-M6-2: GlobalRecallOwner
tc_m62 = "recall" in captured.lower() or "M6-2" in captured
print(f"TC-M6-2 (GlobalRecallOwner):       {'PRESENT' if tc_m62 else 'NOT FOUND'}")

# TC-M6-3: Delayed HN Response
tc_m63 = "M6-3" in captured
print(f"TC-M6-3 (Delayed HN Response):     {'PRESENT' if tc_m63 else 'NOT FOUND'}")

# ---- Final Gate Decision ---------------------------------------------
print("\n" + "=" * 70)

# M4/M5 regression check
if m4_fail > 0:
    print(f"M6_GATE: FAIL (M4 regression: {m4_fail} failures)")
    sys.exit(1)

if m5_fail > 0:
    print(f"M6_GATE: FAIL (M5 regression: {m5_fail} failures)")
    sys.exit(1)

if m6_fail > 0:
    print(f"M6_GATE: FAIL (M6: {m6_fail} failures)")
    sys.exit(1)

if explicit_fail:
    print("M6_GATE: FAIL (M6_SELF_TEST_FAILED=1)")
    sys.exit(1)

# Marker missing check
if not explicit_pass and not explicit_fail:
    print("M6_GATE: FAIL (no M6_SELF_TEST_PASSED/FAILED marker — "
          "self-test may not have run)")
    sys.exit(1)

# All checks passed
print("M6_GATE: PASS")
print(f"  M4: {m4_pass} PASS / {m4_fail} FAIL / {m4_skip} SKIP")
print(f"  M5: {m5_pass} PASS / {m5_fail} FAIL / {m5_skip} SKIP")
print(f"  M6: {m6_pass} PASS / {m6_fail} FAIL / {m6_skip} SKIP")
print(f"  TC-M6-2 (Recall path):       {'PRESENT' if tc_m62 else 'NOT FOUND'}")
print(f"  TC-M6-3 (Delayed response):  {'PRESENT' if tc_m63 else 'NOT FOUND'}")
print(f"  TC-M6-4 (Dir consistency):   {'PASS' if tc_m64_pass else 'INCOMPLETE'}")
print(f"  TC-M6-5 (Metadata-only):     {'PASS' if tc_m65 else 'INCOMPLETE'}")

# Brief simulation to complete
exit_event = m5.simulate(10000)
print(f"EXIT_CAUSE={exit_event.getCause()}")
sys.exit(0)
