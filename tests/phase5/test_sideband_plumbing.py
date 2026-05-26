"""M5: Remote Miss With Permission Sideband — Phase 1 Test Runner.

The structural tests run as a C++ self-test during EPBackend::init()
(implemented in M5SelfTest.cc). This Python script creates the full
CHI+UBCC topology, triggers instantiation, and parses the self-test output.

USAGE: run via gem5 directly:
  gem5.opt tests/phase5/test_sideband_plumbing.py <arm_binary>

Gate logic: Use fd redirect to capture C++ stdout during m5.instantiate(),
parse for M5_SELF_TEST_PASSED=1 / FAILED=1 markers, and exit accordingly.
"""
import sys, os, re, ctypes, tempfile, traceback

if len(sys.argv) < 2:
    print("Usage: gem5.opt tests/phase5/test_sideband_plumbing.py <arm_binary>")
    sys.exit(2)

import m5
from m5.objects import *

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../gem5/configs/'))
from ruby.CHI_basic_framework_config import (
    DEFAULT_N, DEFAULT_L, DEFAULT_D,
)
import ruby.CHI as chi_module
from ruby.CHI_ubcc_framework import create_ubcc_system
chi_module.create_system = create_ubcc_system

binary = sys.argv[1]
NUM = DEFAULT_N
CL = NUM * DEFAULT_L * DEFAULT_D

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

# ---- Capture C++ self-test output ----
# The self-test runs during m5.instantiate(). We redirect stdout to
# a temp file before calling it, then restore and read the file.
libc = ctypes.CDLL(None)
libc.fflush(None)

sav = os.dup(1)
capture_path = None
try:
    capture_f = tempfile.NamedTemporaryFile(mode='w+', delete=False)
    capture_path = capture_f.name
    os.dup2(capture_f.fileno(), 1)
    capture_f.close()

    # This is where C++ self-test output goes → captured to file
    m5.instantiate()

    libc.fflush(None)
finally:
    os.dup2(sav, 1)
    os.close(sav)

    # Read captured output
    if capture_path and os.path.exists(capture_path):
        with open(capture_path, 'r') as f:
            captured = f.read()
        os.unlink(capture_path)
    else:
        captured = ""

# ---- Gate decision ----
explicit_pass = "M5_SELF_TEST_PASSED=1" in captured
explicit_fail = "M5_SELF_TEST_FAILED=1" in captured
pass_count = len(re.findall(r': PASS\b', captured))
fail_count = len(re.findall(r': FAIL\b', captured))

print("\n=== M5 Phase 1 Gate ===")
print("C++ checks: PASS=%d FAIL=%d" % (pass_count, fail_count))
print("explicit_pass=%s explicit_fail=%s" % (explicit_pass, explicit_fail))

if explicit_fail:
    print("GATE: FAIL (FAIL marker)")
    sys.exit(1)
elif not explicit_pass and not explicit_fail:
    print("GATE: FAIL (no marker)")
    sys.exit(1)
elif explicit_pass and fail_count > 0:
    print("GATE: FAIL (PASS marker + FAIL count)")
    sys.exit(1)
else:
    print("GATE: PASS")

# Simulate briefly to complete simulation
exit_event = m5.simulate(100000)
print("EXIT_CAUSE=" + exit_event.getCause())
