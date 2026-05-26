"""M4: Sentinel Registration test suite.
Harness: PY_INJECT
Tests TC-M4-1 through TC-M4-5.

The actual test logic runs as a C++ self-test during EPBackend::init()
(implemented in M4SelfTest.cc). This Python script creates the full
topology, triggers instantiation (which runs the self-test), and
parses the self-test output to verify results.
"""
import sys, os, re, tempfile

# The test runs during m5.instantiate() -> EPBackend::init() -> M4SelfTest.
# We need to capture stdout to parse the self-test PASS/FAIL output.
#
# After instantiation, we parse the captured output for:
#   "M4 <testname>: PASS" — counts as pass
#   "M4 <testname>: FAIL" — counts as fail
#   "M4_SELF_TEST_FAILED=1" — overall failure marker
#   "M4_SELF_TEST_PASSED=1" — overall pass marker
#
# If any FAIL is detected, sys.exit(1).

import m5
from m5.objects import *

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../gem5/configs/'))
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

# ---- Capture setup ----------------------------------------------------------
# Redirect fd 1 (C++ printf stdout) to a temp file for the entire
# gem5 run. The M4SelfTest C++ output may be buffered and appear
# at any time during the simulation lifecycle.
import os as _os

# Create a temp file to capture C++ stdout
tmp_fd, tmp_path = tempfile.mkstemp(suffix='.m4capture', text=True)
old_fd1 = _os.dup(1)   # save original stdout fd
_os.dup2(tmp_fd, 1)     # redirect fd 1 to temp file

try:
    # ---- System setup ----------------------------------------------------------
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
    # Connect CPU ports to ruby sequencers
    for i, cpu in enumerate(cpus):
        ruby._cpu_ports[i].connectCpuPorts(cpu)

    root = Root(full_system=False, system=system)

    # ---- Run instantiation (triggers M4SelfTest) ---------------------------
    m5.instantiate()
    sys.stdout.flush()  # flush Python stdout to the temp file
    _os.fsync(tmp_fd)   # ensure all data is written to disk

    # ---- Restore stdout ----------------------------------------------------
    _os.dup2(old_fd1, 1)
    _os.close(old_fd1)
    _os.close(tmp_fd)

    # ---- Read captured output ----------------------------------------------
    with open(tmp_path, 'r') as f:
        captured = f.read()
    _os.unlink(tmp_path)

except Exception as e:
    # Restore stdout before re-raising
    _os.dup2(old_fd1, 1)
    _os.close(old_fd1)
    _os.close(tmp_fd)
    _os.unlink(tmp_path)
    raise

# ---- Parse the self-test output --------------------------------------------
# Ternary scoring: PASS, FAIL, SKIP
pass_matches = re.findall(r'^\s*M4\s+.*:\s*PASS', captured, re.MULTILINE)
fail_matches = re.findall(r'^\s*M4\s+.*:\s*FAIL', captured, re.MULTILINE)
skip_matches = re.findall(r'^\s*M4\s+.*:\s*SKIP', captured, re.MULTILINE)

pass_count = len(pass_matches)
fail_count = len(fail_matches)
skip_count = len(skip_matches)
total_count = pass_count + fail_count + skip_count

explicit_fail = "M4_SELF_TEST_FAILED=1" in captured
explicit_pass = "M4_SELF_TEST_PASSED=1" in captured

# Print captured output so CI logs are complete
print("=" * 70)
print("M4 Self-Test Captured Output:")
print("=" * 70)
print(captured)
print("=" * 70)

print(f"\nM4 Self-Test: {pass_count}/{total_count} PASS, "
      f"{fail_count} FAIL, {skip_count} SKIP")

if fail_count > 0 or explicit_fail:
    print("M4_PYTHON: SELF-TEST DETECTED FAILURES")
    sys.exit(1)
elif total_count == 0:
    print("M4_PYTHON: WARNING — no PASS/FAIL/SKIP lines found in self-test output")
    print("M4_PYTHON: This may indicate the self-test did not run.")
    if explicit_pass:
        print("M4_PYTHON: explicit PASS marker found, treating as pass")
    elif explicit_fail:
        print("M4_PYTHON: explicit FAIL marker found, treating as fail")
        sys.exit(1)
    else:
        print("M4_PYTHON: treating as FAIL (no test results)")
        sys.exit(1)
else:
    if skip_count > 0:
        print(f"M4_PYTHON: {skip_count} checks SKIPPED (may require M5+ infrastructure)")
    print("M4_PYTHON_TEST_HARNESS: DONE — all executed checks passed")
    sys.exit(0)
