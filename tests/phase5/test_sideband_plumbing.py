"""M5: Remote Miss With Permission Sideband — Phase 1 Test Runner.

The structural tests run as a C++ self-test during EPBackend::init()
(implemented in M5SelfTest.cc). This Python script creates the full
CHI+UBCC topology, triggers instantiation, and parses the self-test output.

Gate logic: Check the M5_SELF_TEST_PASSED/FAILED markers by examining
the gem5 stats file after simulation, since fd redirect within gem5
is unreliable across all Python/C binding layers.
"""
import sys, os, re, glob

# If run as a standalone script (not inside gem5), tell user
if not any('gem5' in a for a in sys.argv[0].lower().split('/')):
    print("Usage: run via gem5: gem5.opt tests/phase5/test_sideband_plumbing.py <binary>")
    print("       The C++ self-test markers appear on gem5's stdout.")
    print("       Gate: check stdout for M5_SELF_TEST_PASSED=1 (pass) or FAILED=1 (fail).")
    sys.exit(0)

# === Below: runs INSIDE gem5 ===
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

# Capture C++ stdout during m5.instantiate() using fd redirect
# The C++ self-test runs during init() and outputs to stdout.
# We redirect stdout to a temp file BEFORE instantiate,
# restore AFTER, and then check the file.
import ctypes, tempfile

libc = ctypes.CDLL(None)
libc.fflush(None)  # flush Python's buffered output first

# Save original stdout fd
orig_stdout = os.dup(1)

# Create a temp file and redirect stdout to it
tmpf = tempfile.NamedTemporaryFile(mode='w+', delete=False)
capture_path = tmpf.name
os.dup2(tmpf.fileno(), 1)
tmpf.close()

# m5.instantiate() triggers C++ self-test → output goes to temp file
m5.instantiate()

libc.fflush(None)  # flush C stdio buffers

# Restore original stdout
os.dup2(orig_stdout, 1)
os.close(orig_stdout)

# Read captured output
with open(capture_path, 'r') as f:
    captured = f.read()
os.unlink(capture_path)

# Parse markers
explicit_pass = "M5_SELF_TEST_PASSED=1" in captured
explicit_fail = "M5_SELF_TEST_FAILED=1" in captured

# Print summary to restored stdout
print("")
print("=== M5 Phase 1 Gate ===")
if explicit_fail:
    print("GATE: FAIL (explicit FAIL marker)")
elif not explicit_pass and not explicit_fail:
    print("GATE: FAIL (no marker found)")
elif explicit_pass and "FAIL" in captured:
    print("GATE: FAIL (PASS marker but FAIL count > 0)")
else:
    print("GATE: PASS")

# Run simulation
exit_event = m5.simulate()
print("SIM_CAUSE=" + exit_event.getCause(), flush=True)
