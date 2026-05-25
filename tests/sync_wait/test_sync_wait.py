"""T0 Sync_Wait Test Runner.

Runs all T0 test cases (TC-T0-1 through TC-T0-4) as separate gem5
invocations to avoid multi-instantiation limitations.

Each test case is its own gem5 simulation with its own system.
Results are collected from process stdout and file outputs.

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


# ─── TC-T0-1 Config ───────────────────────────────────────────────
def make_tc_t0_1_config(tmpdir):
    """Write gem5 config for TC-T0-1 and return the path."""
    outputs = [os.path.join(tmpdir, f"t1_node{i}.out") for i in range(3)]
    binary = os.path.join(WORK_DIR, "tc_t0_1")

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
def make_tc_t0_2_config(tmpdir):
    outputs = [os.path.join(tmpdir, f"t2_node{i}.out") for i in range(3)]
    binary = os.path.join(WORK_DIR, "tc_t0_2")

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
def make_tc_t0_3_config(tmpdir):
    num_cpus = 4
    outputs = [os.path.join(tmpdir, f"t3_cpu{i}.out") for i in range(num_cpus)]
    binary_caller = os.path.join(WORK_DIR, "tc_t0_3_caller")
    binary_noncaller = os.path.join(WORK_DIR, "tc_t0_3_noncaller")

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
def make_tc_t0_4_config(tmpdir):
    outputs = [os.path.join(tmpdir, f"t4_node{i}.out") for i in range(3)]
    binary = os.path.join(WORK_DIR, "tc_t0_4")

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
def test_tc_t0_1(tmpdir):
    global tests_total
    tests_total += 8  # 8 assertions
    print("\n--- TC-T0-1: Barrier Basic Release ---", flush=True)

    cfg_path, outputs = make_tc_t0_1_config(tmpdir)
    rc, stdout = run_gem5(cfg_path)

    check("TC-T0-1 gem5 exit 0", rc == 0)
    check("TC-T0-1 sim completed",
          "exiting with last active thread context" in stdout)

    lines = collect_output(outputs)
    before = [(n, l) for n, l in lines if "BEFORE_BARRIER" in l]
    after = [(n, l) for n, l in lines if "AFTER_BARRIER" in l]
    check("TC-T0-1 3 BEFORE lines", len(before) == 3)
    check("TC-T0-1 3 AFTER lines", len(after) == 3)

    for nid in range(3):
        node_lines = [l for nn, l in lines if nn == nid]
        has_before = any("BEFORE_BARRIER" in l for l in node_lines)
        has_after = any("AFTER_BARRIER" in l for l in node_lines)
        check(f"TC-T0-1 node{nid} BEFORE+AFTER", has_before and has_after)

    check("TC-T0-1 total lines >= 6", len(lines) >= 6)

    for nid, line in lines:
        print(f"    [cpu{nid}] {line}", flush=True)


def test_tc_t0_2(tmpdir):
    global tests_total
    tests_total += 7  # 7 assertions
    print("\n--- TC-T0-2: Barrier Isolation ---", flush=True)

    cfg_path, outputs = make_tc_t0_2_config(tmpdir)
    rc, stdout = run_gem5(cfg_path)

    check("TC-T0-2 gem5 exit 0", rc == 0)
    check("TC-T0-2 sim completed",
          "exiting with last active thread context" in stdout)

    lines = collect_output(outputs)

    for nid in range(3):
        node_lines = [l for nn, l in lines if nn == nid]
        has_after = any("AFTER_BARRIER" in l for l in node_lines)
        check(f"TC-T0-2 node{nid} passed barrier", has_after)

    # Node2 (mask 0b100 = 4) completes independently
    node2_lines = [l for nn, l in lines if nn == 2]
    check("TC-T0-2 node2 independent (mask=4)",
          any("mask=4" in l for l in node2_lines))

    # Node0/1 (mask 0b011 = 3)
    check("TC-T0-2 node0+node1 mask=3",
          sum(1 for _nn, l in lines if "mask=3" in l) >= 2)

    for nid, line in lines:
        print(f"    [cpu{nid}] {line}", flush=True)


def test_tc_t0_3(tmpdir):
    global tests_total
    tests_total += 8  # 8 assertions
    print("\n--- TC-T0-3: Multi-Thread Count ---", flush=True)

    cfg_path, outputs = make_tc_t0_3_config(tmpdir)
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


def test_tc_t0_4(tmpdir):
    global tests_total
    tests_total += 11  # 11 assertions
    print("\n--- TC-T0-4: Reusable Barrier ---", flush=True)

    cfg_path, outputs = make_tc_t0_4_config(tmpdir)
    rc, stdout = run_gem5(cfg_path)

    check("TC-T0-4 gem5 exit 0", rc == 0)
    check("TC-T0-4 sim completed",
          "exiting with last active thread context" in stdout)

    lines = collect_output(outputs)

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


# ─── Main ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 64, flush=True)
    print("T0 Sync_Wait Test Suite", flush=True)
    print(f"gem5 binary: {GEM5_BIN}", flush=True)
    print(f"Work dir: {WORK_DIR}", flush=True)
    print("=" * 64, flush=True)

    tmpdir = tempfile.mkdtemp(prefix="sync_wait_t0_")
    print(f"Output dir: {tmpdir}", flush=True)

    try:
        test_tc_t0_1(tmpdir)
        test_tc_t0_2(tmpdir)
        test_tc_t0_3(tmpdir)
        test_tc_t0_4(tmpdir)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print(f"\n{'=' * 64}", flush=True)
    print(f"Results: {tests_passed}/{tests_total} tests passed", flush=True)
    print(f"{'=' * 64}", flush=True)

    sys.exit(0 if tests_passed == tests_total else 1)
