"""M5: Remote Miss With Permission Sideband — Phase 1 Test Runner.

The structural tests run as a C++ self-test during EPBackend::init()
(implemented in M5SelfTest.cc). This Python script creates the full
CHI+UBCC topology, triggers instantiation, and parses the self-test output.

Gate logic: Parse the C++ self-test output for markers.
Uses a subprocess wrapper that invokes gem5 and captures its output.
"""
import sys, os, re, subprocess, tempfile, shutil

# If run directly, wrap gem5 as subprocess to capture C++ stdout
# (fd capture from within gem5 Python is unreliable)
if len(sys.argv) > 1 and not sys.argv[0].endswith('gem5.opt'):
    binary_arg = os.path.abspath(sys.argv[1])
    script_path = os.path.abspath(__file__)
    gem5_exe = os.path.join(os.path.dirname(__file__),
                            '../../gem5/build/ARM/gem5.opt')

    outdir = tempfile.mkdtemp(prefix='m5_gate_')
    cmd = [gem5_exe, '--outdir=' + outdir, script_path, binary_arg]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    output = result.stdout + result.stderr

    explicit_pass = "M5_SELF_TEST_PASSED=1" in output
    explicit_fail = "M5_SELF_TEST_FAILED=1" in output
    pass_count = len(re.findall(r': PASS\b', output))
    fail_count = len(re.findall(r': FAIL\b', output))

    print("=== M5 Phase 1 Gate ===")
    print("PASS=%d  FAIL=%d" % (pass_count, fail_count))
    print("explicit_pass=%s  explicit_fail=%s" % (explicit_pass, explicit_fail))
    print("subprocess exit=%d" % result.returncode)
    print("")

    if explicit_fail:
        print("GATE: FAIL (explicit FAIL marker)")
        shutil.rmtree(outdir, ignore_errors=True)
        sys.exit(1)
    if not explicit_pass and not explicit_fail:
        print("GATE: FAIL (no marker found)")
        shutil.rmtree(outdir, ignore_errors=True)
        sys.exit(1)
    if explicit_pass and fail_count > 0:
        print("GATE: FAIL (PASS marker but FAIL>0)")
        shutil.rmtree(outdir, ignore_errors=True)
        sys.exit(1)
    print("GATE: PASS")
    shutil.rmtree(outdir, ignore_errors=True)
    sys.exit(0)

# === Below: runs INSIDE gem5 as Python workload ===
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

m5.instantiate()
exit_event = m5.simulate()
# gem5 process exits

explicit_fail = "M5_SELF_TEST_FAILED=1" in captured
explicit_pass = "M5_SELF_TEST_PASSED=1" in captured

# Count PASS/FAIL/SKIP lines
pass_count = len(re.findall(r': PASS\b', captured))
fail_count = len(re.findall(r': FAIL\b', captured))

print("M5_PYTHON: PASS=%d FAIL=%d  explicit_fail=%s explicit_pass=%s" % (
    pass_count, fail_count, explicit_fail, explicit_pass), flush=True)

if explicit_fail:
    print("M5_PYTHON: explicit FAIL marker found, treating as fail", flush=True)
    sys.exit(1)
if not explicit_pass and not explicit_fail:
    print("M5_PYTHON: FATAL — neither M5_SELF_TEST_PASSED=1 nor "
          "M5_SELF_TEST_FAILED=1 marker found", flush=True)
    sys.exit(1)
if explicit_pass and fail_count > 0:
    print("M5_PYTHON: PASSED marker but FAIL count > 0, contradiction",
          flush=True)
    sys.exit(1)
print("M5_PYTHON_TEST_HARNESS: DONE — all executed checks passed", flush=True)
sys.exit(0)
