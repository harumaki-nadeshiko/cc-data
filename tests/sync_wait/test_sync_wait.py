"""T0 Sync_Wait Test Runner.

Runs all T0 test cases (TC-T0-1 through TC-T0-7) as separate gem5
invocations to avoid multi-instantiation limitations.

Each test case is its own gem5 simulation with its own system.
Results are collected from process stdout and file outputs.

Workload binaries are auto-compiled from .c sources if missing or stale.

Usage:
  python3 tests/sync_wait/test_sync_wait.py
"""

import sys
import os
import tempfile
import shutil
import subprocess


GEM5_BIN = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "../../gem5/build/ARM/gem5.opt",
)

WORK_DIR = os.path.dirname(os.path.abspath(__file__))

tests_passed = 0
tests_total = 0


def check(name, cond):
    global tests_passed
    label = f"  {name}: {'PASS' if cond else 'FAIL'}"
    print(label, flush=True)
    if cond:
        tests_passed += 1


def run_gem5(config_path):
    """Run gem5 with the given config script. Returns (exit_code, stdout)."""
    cmd = [GEM5_BIN, config_path]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
        cwd=os.path.dirname(GEM5_BIN),
    )
    return proc.returncode, proc.stdout + "\n" + proc.stderr


def collect_output(paths):
    """Read output files; return list of (index, line) tuples."""
    lines = []
    for idx, path in enumerate(paths):
        if os.path.exists(path):
            with open(path, "r") as f:
                for line in f.readlines():
                    line = line.strip()
                    if line:
                        lines.append((idx, line))
    return lines


def compile_arm64(src_path, out_path, extra_flags=None):
    """Cross-compile a single .c file to static ARM64 binary.

    Returns True on success, False on failure.
    """
    cc = "aarch64-linux-gnu-gcc"
    # Check if cross-compiler exists
    if shutil.which(cc) is None:
        print(f"  WARNING: {cc} not found, cannot compile {src_path}",
              flush=True)
        return False

    # Only recompile if source is newer than output
    if os.path.exists(out_path):
        src_mtime = os.path.getmtime(src_path)
        out_mtime = os.path.getmtime(out_path)
        if src_mtime <= out_mtime:
            # Already up to date
            return True

    flags = [cc, "-static", "-o", out_path, src_path]
    if extra_flags:
        flags.extend(extra_flags)
    print(f"  Compiling: {' '.join(flags)}", flush=True)
    proc = subprocess.run(flags, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"  Compilation FAILED: {proc.stderr}", flush=True)
        return False
    return True


def ensure_binary(basename, src_name, extra_flags=None):
    """Ensure binary 'basename' is compiled from 'src_name'."""
    binary = os.path.join(WORK_DIR, basename)
    src = os.path.join(WORK_DIR, src_name)
    if not os.path.exists(src):
        print(f"  ERROR: source file {src} not found", flush=True)
        return None
    if compile_arm64(src, binary, extra_flags):
        return binary
    return None


# ─── Single-CPU Config (for negative tests TC-T0-5/6/7) ──────────
def make_single_cpu_config(tmpdir, test_name, binary):
    """Write gem5 config for a single-CPU test and return the path."""
    output = os.path.join(tmpdir, f"{test_name}.out")

    config = f"""
import sys, os
import m5
from m5.objects import *

SYS_CLOCK = "2GHz"
CPU_CLOCK = "2GHz"
TMPDIR = "{tmpdir}"
BINARY = "{binary}"
OUTPUT = "{output}"

system = System(mem_mode="atomic", cache_line_size=64)
system.clk_domain = SrcClockDomain(clock=SYS_CLOCK)
system.clk_domain.voltage_domain = VoltageDomain()
system.membus = SystemXBar()

mem = SimpleMemory(range=AddrRange(0, size="512MB"))
mem.port = system.membus.mem_side_ports
system.memories = [mem]
system.mem_ranges = [mem.range]

cpu = AtomicSimpleCPU(cpu_id=0)
cpu.clk_domain = SrcClockDomain(clock=CPU_CLOCK, voltage_domain=system.clk_domain.voltage_domain)
cpu.createThreads()
cpu.createInterruptController()
cpu.icache_port = system.membus.cpu_side_ports
cpu.dcache_port = system.membus.cpu_side_ports
proc = Process(pid=100)
proc.executable = BINARY
proc.cmd = [BINARY]
proc.cwd = os.getcwd()
proc.output = OUTPUT
proc.errout = OUTPUT
cpu.workload = [proc]

system.cpu = [cpu]
system.workload = SEWorkload.init_compatible(BINARY)
system.memories = [mem]
system.mem_ranges = [mem.range]

root = Root(full_system=False, system=system)
m5.instantiate()
exit_event = m5.simulate()
cause = exit_event.getCause()
print("SIM_CAUSE=" + cause, flush=True)
"""
    path = os.path.join(tmpdir, f"{test_name}_cfg.py")
    with open(path, "w") as f:
        f.write(config)
    return path, [output]


# ─── TC-T0-1 Config ───────────────────────────────────────────────
def make_tc_t0_1_config(tmpdir, binary):
    """Write gem5 config for TC-T0-1 and return the path."""
    outputs = [os.path.join(tmpdir, f"t1_node{i}.out") for i in range(3)]

    config = f"""
import sys, os
import m5
from m5.objects import *

SYS_CLOCK = "2GHz"
CPU_CLOCK = "2GHz"
TMPDIR = "{tmpdir}"
BINARY = "{binary}"
OUTPUTS = {repr(outputs)}

system = System(mem_mode="atomic", cache_line_size=64)
system.clk_domain = SrcClockDomain(clock=SYS_CLOCK)
system.clk_domain.voltage_domain = VoltageDomain()
system.membus = SystemXBar()

mem = SimpleMemory(range=AddrRange(0, size="512MB"))
mem.port = system.membus.mem_side_ports
system.memories = [mem]
system.mem_ranges = [mem.range]

cpus = []
for i in range(3):
    cpu = AtomicSimpleCPU(cpu_id=i)
    cpu.clk_domain = SrcClockDomain(clock=CPU_CLOCK, voltage_domain=system.clk_domain.voltage_domain)
    cpu.createThreads()
    cpu.createInterruptController()
    cpu.icache_port = system.membus.cpu_side_ports
    cpu.dcache_port = system.membus.cpu_side_ports
    proc = Process(pid=100 + i)
    proc.executable = BINARY
    proc.cmd = [BINARY, str(i)]
    proc.cwd = os.getcwd()
    proc.output = OUTPUTS[i]
    proc.errout = OUTPUTS[i]
    cpu.workload = [proc]
    cpus.append(cpu)

system.cpu = cpus
system.workload = SEWorkload.init_compatible(BINARY)
system.memories = [mem]
system.mem_ranges = [mem.range]

root = Root(full_system=False, system=system)
m5.instantiate()
exit_event = m5.simulate()
cause = exit_event.getCause()
print("SIM_CAUSE=" + cause, flush=True)
"""
    path = os.path.join(tmpdir, "tc_t0_1_cfg.py")
    with open(path, "w") as f:
        f.write(config)
    return path, outputs


# ─── TC-T0-2 Config ───────────────────────────────────────────────
def make_tc_t0_2_config(tmpdir, binary):
    outputs = [os.path.join(tmpdir, f"t2_node{i}.out") for i in range(3)]

    config = f"""
import sys, os
import m5
from m5.objects import *

SYS_CLOCK = "2GHz"
CPU_CLOCK = "2GHz"
TMPDIR = "{tmpdir}"
BINARY = "{binary}"
OUTPUTS = {repr(outputs)}

system = System(mem_mode="atomic", cache_line_size=64)
system.clk_domain = SrcClockDomain(clock=SYS_CLOCK)
system.clk_domain.voltage_domain = VoltageDomain()
system.membus = SystemXBar()

mem = SimpleMemory(range=AddrRange(0, size="512MB"))
mem.port = system.membus.mem_side_ports
system.memories = [mem]
system.mem_ranges = [mem.range]

cpus = []
for i in range(3):
    cpu = AtomicSimpleCPU(cpu_id=i)
    cpu.clk_domain = SrcClockDomain(clock=CPU_CLOCK, voltage_domain=system.clk_domain.voltage_domain)
    cpu.createThreads()
    cpu.createInterruptController()
    cpu.icache_port = system.membus.cpu_side_ports
    cpu.dcache_port = system.membus.cpu_side_ports
    proc = Process(pid=100 + i)
    proc.executable = BINARY
    proc.cmd = [BINARY, str(i)]
    proc.cwd = os.getcwd()
    proc.output = OUTPUTS[i]
    proc.errout = OUTPUTS[i]
    cpu.workload = [proc]
    cpus.append(cpu)

system.cpu = cpus
system.workload = SEWorkload.init_compatible(BINARY)
system.memories = [mem]
system.mem_ranges = [mem.range]

root = Root(full_system=False, system=system)
m5.instantiate()
exit_event = m5.simulate()
cause = exit_event.getCause()
print("SIM_CAUSE=" + cause, flush=True)
"""
    path = os.path.join(tmpdir, "tc_t0_2_cfg.py")
    with open(path, "w") as f:
        f.write(config)
    return path, outputs


# ─── TC-T0-3 Config ───────────────────────────────────────────────
def make_tc_t0_3_config(tmpdir, binary_caller, binary_noncaller):
    num_cpus = 4
    outputs = [os.path.join(tmpdir, f"t3_cpu{i}.out") for i in range(num_cpus)]

    config = f"""
import sys, os
import m5
from m5.objects import *

SYS_CLOCK = "2GHz"
CPU_CLOCK = "2GHz"
TMPDIR = "{tmpdir}"
BINARY_C = "{binary_caller}"
BINARY_NC = "{binary_noncaller}"
OUTPUTS = {repr(outputs)}

system = System(mem_mode="atomic", cache_line_size=64)
system.clk_domain = SrcClockDomain(clock=SYS_CLOCK)
system.clk_domain.voltage_domain = VoltageDomain()
system.membus = SystemXBar()

mem = SimpleMemory(range=AddrRange(0, size="512MB"))
mem.port = system.membus.mem_side_ports
system.memories = [mem]
system.mem_ranges = [mem.range]

cpus = []
# CPU 0: Node0 caller
cpu = AtomicSimpleCPU(cpu_id=0)
cpu.clk_domain = SrcClockDomain(clock=CPU_CLOCK, voltage_domain=system.clk_domain.voltage_domain)
cpu.createThreads(); cpu.createInterruptController()
cpu.icache_port = system.membus.cpu_side_ports
cpu.dcache_port = system.membus.cpu_side_ports
proc = Process(pid=100)
proc.executable = BINARY_C
proc.cmd = [BINARY_C, "0"]
proc.cwd = os.getcwd()
proc.output = OUTPUTS[0]; proc.errout = OUTPUTS[0]
cpu.workload = [proc]
cpus.append(cpu)

# CPU 1: Node0 non-caller
cpu = AtomicSimpleCPU(cpu_id=1)
cpu.clk_domain = SrcClockDomain(clock=CPU_CLOCK, voltage_domain=system.clk_domain.voltage_domain)
cpu.createThreads(); cpu.createInterruptController()
cpu.icache_port = system.membus.cpu_side_ports
cpu.dcache_port = system.membus.cpu_side_ports
proc = Process(pid=101)
proc.executable = BINARY_NC
proc.cmd = [BINARY_NC, "0"]
proc.cwd = os.getcwd()
proc.output = OUTPUTS[1]; proc.errout = OUTPUTS[1]
cpu.workload = [proc]
cpus.append(cpu)

# CPU 2: Node1 caller
cpu = AtomicSimpleCPU(cpu_id=2)
cpu.clk_domain = SrcClockDomain(clock=CPU_CLOCK, voltage_domain=system.clk_domain.voltage_domain)
cpu.createThreads(); cpu.createInterruptController()
cpu.icache_port = system.membus.cpu_side_ports
cpu.dcache_port = system.membus.cpu_side_ports
proc = Process(pid=102)
proc.executable = BINARY_C
proc.cmd = [BINARY_C, "1"]
proc.cwd = os.getcwd()
proc.output = OUTPUTS[2]; proc.errout = OUTPUTS[2]
cpu.workload = [proc]
cpus.append(cpu)

# CPU 3: Node2 caller
cpu = AtomicSimpleCPU(cpu_id=3)
cpu.clk_domain = SrcClockDomain(clock=CPU_CLOCK, voltage_domain=system.clk_domain.voltage_domain)
cpu.createThreads(); cpu.createInterruptController()
cpu.icache_port = system.membus.cpu_side_ports
cpu.dcache_port = system.membus.cpu_side_ports
proc = Process(pid=103)
proc.executable = BINARY_C
proc.cmd = [BINARY_C, "2"]
proc.cwd = os.getcwd()
proc.output = OUTPUTS[3]; proc.errout = OUTPUTS[3]
cpu.workload = [proc]
cpus.append(cpu)

system.cpu = cpus
system.workload = SEWorkload.init_compatible(BINARY_C)
system.memories = [mem]
system.mem_ranges = [mem.range]

root = Root(full_system=False, system=system)
m5.instantiate()
exit_event = m5.simulate()
cause = exit_event.getCause()
print("SIM_CAUSE=" + cause, flush=True)
"""
    path = os.path.join(tmpdir, "tc_t0_3_cfg.py")
    with open(path, "w") as f:
        f.write(config)
    return path, outputs


# ─── TC-T0-4 Config ───────────────────────────────────────────────
def make_tc_t0_4_config(tmpdir, binary):
    outputs = [os.path.join(tmpdir, f"t4_node{i}.out") for i in range(3)]

    config = f"""
import sys, os
import m5
from m5.objects import *

SYS_CLOCK = "2GHz"
CPU_CLOCK = "2GHz"
TMPDIR = "{tmpdir}"
BINARY = "{binary}"
OUTPUTS = {repr(outputs)}

system = System(mem_mode="atomic", cache_line_size=64)
system.clk_domain = SrcClockDomain(clock=SYS_CLOCK)
system.clk_domain.voltage_domain = VoltageDomain()
system.membus = SystemXBar()

mem = SimpleMemory(range=AddrRange(0, size="512MB"))
mem.port = system.membus.mem_side_ports
system.memories = [mem]
system.mem_ranges = [mem.range]

cpus = []
for i in range(3):
    cpu = AtomicSimpleCPU(cpu_id=i)
    cpu.clk_domain = SrcClockDomain(clock=CPU_CLOCK, voltage_domain=system.clk_domain.voltage_domain)
    cpu.createThreads()
    cpu.createInterruptController()
    cpu.icache_port = system.membus.cpu_side_ports
    cpu.dcache_port = system.membus.cpu_side_ports
    proc = Process(pid=100 + i)
    proc.executable = BINARY
    proc.cmd = [BINARY, str(i)]
    proc.cwd = os.getcwd()
    proc.output = OUTPUTS[i]
    proc.errout = OUTPUTS[i]
    cpu.workload = [proc]
    cpus.append(cpu)

system.cpu = cpus
system.workload = SEWorkload.init_compatible(BINARY)
system.memories = [mem]
system.mem_ranges = [mem.range]

root = Root(full_system=False, system=system)
m5.instantiate()
exit_event = m5.simulate()
cause = exit_event.getCause()
print("SIM_CAUSE=" + cause, flush=True)
"""
    path = os.path.join(tmpdir, "tc_t0_4_cfg.py")
    with open(path, "w") as f:
        f.write(config)
    return path, outputs


# ─── Tests ─────────────────────────────────────────────────────────
def test_tc_t0_1(tmpdir, binary):
    global tests_total
    tests_total += 10  # 10 assertions: 2 gem5 + 6 per-node + 2 counts
    print("\n--- TC-T0-1: Barrier Basic Release ---", flush=True)

    cfg_path, outputs = make_tc_t0_1_config(tmpdir, binary)
    rc, stdout = run_gem5(cfg_path)

    check("TC-T0-1 gem5 exit 0", rc == 0)
    check("TC-T0-1 sim completed",
          "exiting with last active thread context" in stdout)

    lines = collect_output(outputs)

    # Per-node collections
    for nid in range(3):
        node_lines = [l for nn, l in lines if nn == nid]
        has_before = any("BEFORE_BARRIER" in l for l in node_lines)
        has_after = any("AFTER_BARRIER" in l for l in node_lines)
        check(f"TC-T0-1 node{nid} BEFORE+AFTER", has_before and has_after)

        # P1 strengthened assertion: BEFORE before AFTER within each node
        before_idx = next((i for i, l in enumerate(node_lines)
                           if "BEFORE_BARRIER" in l), -1)
        after_idx = next((i for i, l in enumerate(node_lines)
                          if "AFTER_BARRIER" in l), -1)
        check(f"TC-T0-1 node{nid} BEFORE < AFTER (intra-node ordering)",
              before_idx >= 0 and after_idx >= 0 and before_idx < after_idx)

    # Count assertions across all nodes
    before_count = sum(1 for _n, l in lines if "BEFORE_BARRIER" in l)
    after_count = sum(1 for _n, l in lines if "AFTER_BARRIER" in l)
    check("TC-T0-1 3 BEFORE lines exactly", before_count == 3)
    check("TC-T0-1 3 AFTER lines exactly", after_count == 3)

    for nid, line in lines:
        print(f"    [cpu{nid}] {line}", flush=True)


def test_tc_t0_2(tmpdir, binary):
    global tests_total
    tests_total += 10  # 10 assertions: 2 gem5 + 6 per-node + 2 mask checks
    print("\n--- TC-T0-2: Barrier Isolation ---", flush=True)

    cfg_path, outputs = make_tc_t0_2_config(tmpdir, binary)
    rc, stdout = run_gem5(cfg_path)

    check("TC-T0-2 gem5 exit 0", rc == 0)
    check("TC-T0-2 sim completed",
          "exiting with last active thread context" in stdout)

    lines = collect_output(outputs)

    for nid in range(3):
        node_lines = [l for nn, l in lines if nn == nid]
        has_after = any("AFTER_BARRIER" in l for l in node_lines)
        check(f"TC-T0-2 node{nid} passed barrier", has_after)

        # Intra-node ordering: BEFORE before AFTER
        before_idx = next((i for i, l in enumerate(node_lines)
                           if "BEFORE_BARRIER" in l), -1)
        after_idx = next((i for i, l in enumerate(node_lines)
                          if "AFTER_BARRIER" in l), -1)
        check(f"TC-T0-2 node{nid} BEFORE < AFTER",
              before_idx >= 0 and after_idx >= 0 and before_idx < after_idx)

    # Node2 (mask 0b100 = 4) completes independently — popcount=1, no wait
    node2_lines = [l for nn, l in lines if nn == 2]
    check("TC-T0-2 node2 independent (mask=4)",
          any("mask=4" in l for l in node2_lines))

    # Node0/1 (mask 0b011 = 3)
    check("TC-T0-2 node0+node1 mask=3",
          sum(1 for _nn, l in lines if "mask=3" in l) >= 2)

    for nid, line in lines:
        print(f"    [cpu{nid}] {line}", flush=True)


def test_tc_t0_3(tmpdir, binary_caller, binary_noncaller):
    global tests_total
    tests_total += 8  # 8 assertions
    print("\n--- TC-T0-3: Multi-Thread Count ---", flush=True)

    cfg_path, outputs = make_tc_t0_3_config(tmpdir,
                                             binary_caller,
                                             binary_noncaller)
    rc, stdout = run_gem5(cfg_path)

    check("TC-T0-3 gem5 exit 0", rc == 0)
    check("TC-T0-3 sim completed",
          "exiting with last active thread context" in stdout)

    lines = collect_output(outputs)

    # CPU 1 is the non-caller
    nc_lines = [l for nn, l in lines if nn == 1]
    check("TC-T0-3 non-caller printed",
          any("NON_CALLER" in l for l in nc_lines))
    check("TC-T0-3 non-caller done",
          any("NON_CALLER_DONE" in l for l in nc_lines))

    # Callers (CPU 0, 2, 3) should pass barrier
    for cid in [0, 2, 3]:
        cpu_lines = [l for nn, l in lines if nn == cid]
        check(f"TC-T0-3 caller CPU{cid} passed",
              any("AFTER_BARRIER" in l for l in cpu_lines))

    check("TC-T0-3 non-caller no AFTER_BARRIER",
          not any("AFTER_BARRIER" in l for l in nc_lines))

    for nid, line in lines:
        print(f"    [cpu{nid}] {line}", flush=True)


def test_tc_t0_4(tmpdir, binary):
    global tests_total
    tests_total += 15  # 15 assertions: 2 gem5 + 4 totals + 6 per-node + 3 ordering
    print("\n--- TC-T0-4: Reusable Barrier ---", flush=True)

    cfg_path, outputs = make_tc_t0_4_config(tmpdir, binary)
    rc, stdout = run_gem5(cfg_path)

    check("TC-T0-4 gem5 exit 0", rc == 0)
    check("TC-T0-4 sim completed",
          "exiting with last active thread context" in stdout)

    lines = collect_output(outputs)

    # P1 strengthened: assert exact counts for each marker across all nodes
    before_r1_total = sum(1 for _n, l in lines if "BEFORE_BARRIER_R1" in l)
    after_r1_total = sum(1 for _n, l in lines if "AFTER_BARRIER_R1" in l)
    before_r2_total = sum(1 for _n, l in lines if "BEFORE_BARRIER_R2" in l)
    after_r2_total = sum(1 for _n, l in lines if "AFTER_BARRIER_R2" in l)
    check("TC-T0-4 3x BEFORE_R1 total", before_r1_total == 3)
    check("TC-T0-4 3x AFTER_R1 total", after_r1_total == 3)
    check("TC-T0-4 3x BEFORE_R2 total", before_r2_total == 3)
    check("TC-T0-4 3x AFTER_R2 total", after_r2_total == 3)

    # Per-node checks
    for nid in range(3):
        node_lines = [l for nn, l in lines if nn == nid]
        r1_before = sum(1 for l in node_lines if "BEFORE_BARRIER_R1" in l)
        r1_after = sum(1 for l in node_lines if "AFTER_BARRIER_R1" in l)
        r2_before = sum(1 for l in node_lines if "BEFORE_BARRIER_R2" in l)
        r2_after = sum(1 for l in node_lines if "AFTER_BARRIER_R2" in l)
        check(f"TC-T0-4 node{nid} R1 complete",
              r1_before == 1 and r1_after == 1)
        check(f"TC-T0-4 node{nid} R2 complete",
              r2_before == 1 and r2_after == 1)

    # Per-node intra-file ordering: R1_BEFORE < R1_AFTER < R2_BEFORE < R2_AFTER
    for nid in range(3):
        node_lines = [l for nn, l in lines if nn == nid]
        r1_after_idx = next((i for i, ll in enumerate(node_lines)
                             if "AFTER_BARRIER_R1" in ll), -1)
        r2_before_idx = next((i for i, ll in enumerate(node_lines)
                              if "BEFORE_BARRIER_R2" in ll), -1)
        if r1_after_idx >= 0 and r2_before_idx >= 0:
            check(f"TC-T0-4 node{nid} R1 -> R2 order",
                  r1_after_idx < r2_before_idx)

    for nid, line in lines:
        print(f"    [cpu{nid}] {line}", flush=True)


# ─── TC-T0-5: mask=0 → -EINVAL ────────────────────────────────────
def test_tc_t0_5(tmpdir, binary):
    global tests_total
    tests_total += 5
    print("\n--- TC-T0-5: mask=0 Invalid ---", flush=True)

    cfg_path, outputs = make_single_cpu_config(tmpdir, "tc_t0_5", binary)
    rc, stdout = run_gem5(cfg_path)

    check("TC-T0-5 gem5 exit 0", rc == 0)
    check("TC-T0-5 sim completed",
          "exiting with last active thread context" in stdout)

    lines = collect_output(outputs)
    all_text = " ".join(l for _n, l in lines)

    check("TC-T0-5 START seen", "TC_T0_5_START" in all_text)
    check("TC-T0-5 error returned (RET < 0)",
          any("TC_T0_5_PASS_ERROR_RETURNED" in l for _n, l in lines))
    check("TC-T0-5 no FAIL",
          not any("FAIL" in l for _n, l in lines))

    for nid, line in lines:
        print(f"    [cpu{nid}] {line}", flush=True)


# ─── TC-T0-6: high 32 bits → -EINVAL ─────────────────────────────
def test_tc_t0_6(tmpdir, binary):
    global tests_total
    tests_total += 5
    print("\n--- TC-T0-6: High-32-Bits Invalid ---", flush=True)

    cfg_path, outputs = make_single_cpu_config(tmpdir, "tc_t0_6", binary)
    rc, stdout = run_gem5(cfg_path)

    check("TC-T0-6 gem5 exit 0", rc == 0)
    check("TC-T0-6 sim completed",
          "exiting with last active thread context" in stdout)

    lines = collect_output(outputs)
    all_text = " ".join(l for _n, l in lines)

    check("TC-T0-6 START seen", "TC_T0_6_START" in all_text)
    check("TC-T0-6 error returned (RET < 0)",
          any("TC_T0_6_PASS_ERROR_RETURNED" in l for _n, l in lines))
    check("TC-T0-6 no FAIL",
          not any("FAIL" in l for _n, l in lines))

    for nid, line in lines:
        print(f"    [cpu{nid}] {line}", flush=True)


# ─── TC-T0-7: bits beyond N=3 → -EINVAL ──────────────────────────
def test_tc_t0_7(tmpdir, binary):
    global tests_total
    tests_total += 5
    print("\n--- TC-T0-7: Bits Beyond N=3 Invalid ---", flush=True)

    cfg_path, outputs = make_single_cpu_config(tmpdir, "tc_t0_7", binary)
    rc, stdout = run_gem5(cfg_path)

    check("TC-T0-7 gem5 exit 0", rc == 0)
    check("TC-T0-7 sim completed",
          "exiting with last active thread context" in stdout)

    lines = collect_output(outputs)
    all_text = " ".join(l for _n, l in lines)

    check("TC-T0-7 START seen", "TC_T0_7_START" in all_text)
    check("TC-T0-7 error returned (RET < 0)",
          any("TC_T0_7_PASS_ERROR_RETURNED" in l for _n, l in lines))
    check("TC-T0-7 no FAIL",
          not any("FAIL" in l for _n, l in lines))

    for nid, line in lines:
        print(f"    [cpu{nid}] {line}", flush=True)


# ─── Main ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 64, flush=True)
    print("T0 Sync_Wait Test Suite", flush=True)
    print(f"gem5 binary: {GEM5_BIN}", flush=True)
    print(f"Work dir: {WORK_DIR}", flush=True)
    print("=" * 64, flush=True)

    # ── Auto-compile all workloads ──
    print("\n--- Compiling workloads ---", flush=True)

    bin_t1 = ensure_binary("tc_t0_1", "tc_t0_1.c")
    bin_t2 = ensure_binary("tc_t0_2", "tc_t0_2.c")
    bin_t3_c = ensure_binary("tc_t0_3_caller", "tc_t0_3.c",
                              ["-DCALLER=1"])
    bin_t3_nc = ensure_binary("tc_t0_3_noncaller", "tc_t0_3.c",
                               ["-DCALLER=0"])
    bin_t4 = ensure_binary("tc_t0_4", "tc_t0_4.c")
    bin_t5 = ensure_binary("tc_t0_5", "tc_t0_5.c")
    bin_t6 = ensure_binary("tc_t0_6", "tc_t0_6.c")
    bin_t7 = ensure_binary("tc_t0_7", "tc_t0_7.c")

    all_binaries = [bin_t1, bin_t2, bin_t3_c, bin_t3_nc,
                    bin_t4, bin_t5, bin_t6, bin_t7]
    if any(b is None for b in all_binaries):
        print("ERROR: One or more workloads failed to compile. Aborting.",
              flush=True)
        sys.exit(1)

    print("All workloads compiled successfully.\n", flush=True)

    tmpdir = tempfile.mkdtemp(prefix="sync_wait_t0_")
    print(f"Output dir: {tmpdir}", flush=True)

    try:
        test_tc_t0_1(tmpdir, bin_t1)
        test_tc_t0_2(tmpdir, bin_t2)
        test_tc_t0_3(tmpdir, bin_t3_c, bin_t3_nc)
        test_tc_t0_4(tmpdir, bin_t4)
        test_tc_t0_5(tmpdir, bin_t5)
        test_tc_t0_6(tmpdir, bin_t6)
        test_tc_t0_7(tmpdir, bin_t7)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print(f"\n{'=' * 64}", flush=True)
    print(f"Results: {tests_passed}/{tests_total} tests passed", flush=True)
    print(f"{'=' * 64}", flush=True)

    sys.exit(0 if tests_passed == tests_total else 1)
