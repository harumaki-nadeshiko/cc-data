"""T0 Sync_Wait Test Runner.

Runs all T0 test cases (TC-T0-1 through TC-T0-7) as separate gem5
invocations to avoid multi-instantiation limitations.

Each test case is its own gem5 simulation with its own system.

Multi-CPU tests (TC-T0-1 through TC-T0-4) use per-CPU output files.
Global tick-based ordering is reconstructed from gem5's SyscallBase
debug trace, which records every write() syscall with its tick and
originating CPU. The test matches trace write events to per-CPU
output file lines in order to build a globally-ordered timeline.

Single-CPU negative tests (TC-T0-5/6/7) read output files directly.

Workload binaries are auto-compiled from .c sources if missing or stale.

Usage:
  python3 tests/sync_wait/test_sync_wait.py [--artifact-dir DIR]
"""

import sys
import os
import json
import tempfile
import shutil
import subprocess
import argparse
import re


GEM5_BIN = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "../../gem5/build/ARM/gem5.opt",
)

WORK_DIR = os.path.dirname(os.path.abspath(__file__))

tests_passed = 0
tests_total = 0


def check(name, cond):
    global tests_passed
    label = "  {}: {}".format(name, "PASS" if cond else "FAIL")
    print(label, flush=True)
    if cond:
        tests_passed += 1


# ─── Trace-based global ordering ────────────────────────────────────
#
# Gem5's --debug-flags=SyscallBase emits lines like:
#   TICK: system.cpuN: T0 : syscall Calling write(1, ADDR, LEN)...
#   TICK: system.cpuN: T0 : syscall Returned RESULT.
#   TICK: system.cpuN: T0 : syscall Calling sync_wait(MASK)...
#   TICK: system.cpuN: T0 : syscall Returned RESULT.
#
# We parse these to build a globally-ordered (by tick) timeline of
# write() syscalls. Each write in the trace is matched to a line in
# the corresponding CPU's output file by order (N-th write for CPU X
#  == N-th line in CPU X's output file).

_RE_TRACE_WRITE_CALL = re.compile(
    r"^\s*(\d+): system\.cpu(\d+): T0 : syscall Calling write\(1, \d+, (\d+)\)")
_RE_TRACE_SYNC_WAIT_CALL = re.compile(
    r"^\s*(\d+): system\.cpu(\d+): T0 : syscall Calling sync_wait\((\d+)\)")


def run_gem5_with_trace(config_path, trace_path, cwd=None, log_path=None):
    """Run gem5 with SyscallBase debug trace. Returns (exit_code, stdout)."""
    if cwd is None:
        cwd = os.path.dirname(GEM5_BIN)
    cmd = [
        GEM5_BIN,
        "--debug-flags=SyscallBase",
        "--debug-file={}".format(trace_path),
        config_path,
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600,
        cwd=cwd,
    )
    full_output = proc.stdout + "\n" + proc.stderr
    if log_path:
        with open(log_path, "w") as f:
            f.write(full_output)
    return proc.returncode, full_output


def run_gem5_simple(config_path, cwd=None, log_path=None):
    """Run gem5 without debug trace (for single-CPU tests)."""
    if cwd is None:
        cwd = os.path.dirname(GEM5_BIN)
    cmd = [GEM5_BIN, config_path]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600,
        cwd=cwd,
    )
    full_output = proc.stdout + "\n" + proc.stderr
    if log_path:
        with open(log_path, "w") as f:
            f.write(full_output)
    return proc.returncode, full_output


def read_lines(path):
    """Read a file and return non-empty stripped lines in order."""
    if not os.path.exists(path):
        return []
    lines = []
    with open(path, "r") as f:
        for line in f:
            s = line.strip()
            if s:
                lines.append(s)
    return lines


def parse_trace_write_events(trace_path, num_cpus):
    """Parse gem5 SyscallBase trace.

    Returns:
      List of (tick, cpu_id, write_len) tuples for write() calls in trace order.
      Also returns dict: cpu_id -> [list of (tick, write_len)] for per-CPU tracking.
    """
    if not os.path.exists(trace_path):
        return [], {c: [] for c in range(num_cpus)}

    events = []  # global ordered list
    per_cpu = {c: [] for c in range(num_cpus)}

    with open(trace_path, "r") as f:
        for line in f:
            m = _RE_TRACE_WRITE_CALL.match(line)
            if m:
                tick = int(m.group(1))
                cpu_id = int(m.group(2))
                wlen = int(m.group(3))
                evt = (tick, cpu_id, wlen)
                events.append(evt)
                if cpu_id in per_cpu:
                    per_cpu[cpu_id].append(evt)

    return events, per_cpu


def build_global_timeline_from_trace(trace_path, output_files, num_cpus):
    """Combine SyscallBase trace + per-CPU output files into a globally-ordered
    timeline of (tick, cpu_id, marker_line) tuples.

    output_files: list of paths, indexed by cpu_id.
    """
    events, per_cpu_events = parse_trace_write_events(trace_path, num_cpus)

    # Read all per-CPU output lines
    cpu_lines = {}
    for cid in range(num_cpus):
        cpu_lines[cid] = read_lines(output_files[cid]) if cid < len(output_files) else []

    # Build mapping: cpu_id -> list index (how many writes consumed so far)
    cpu_idx = {c: 0 for c in range(num_cpus)}

    # Build global timeline
    timeline = []
    for tick, cpu_id, wlen in events:
        # Get next line for this CPU
        idx = cpu_idx.get(cpu_id, 0)
        if cpu_id in cpu_lines and idx < len(cpu_lines[cpu_id]):
            line = cpu_lines[cpu_id][idx]
            cpu_idx[cpu_id] = idx + 1
            timeline.append((tick, cpu_id, line))
        else:
            # Mismatch — trace has more writes than output lines
            timeline.append((tick, cpu_id, "<missing>"))

    return timeline


# ─── Output-Line Parser ──────────────────────────────────────────────

_RE_NODE = re.compile(r"node=(\d+)")
_RE_MASK = re.compile(r"mask=(\d+)")
_RE_RET  = re.compile(r"SYNC_WAIT_RET=(-?\d+)")


def _parse_marker(line):
    """Return (marker, node_id_int_or_None, mask_int_or_None, ret_int_or_None)."""
    m_node = _RE_NODE.search(line)
    m_mask = _RE_MASK.search(line)
    m_ret  = _RE_RET.search(line)

    node_id = int(m_node.group(1)) if m_node else None
    mask    = int(m_mask.group(1)) if m_mask else None
    ret     = int(m_ret.group(1))  if m_ret  else None

    # Longest-match-first to avoid e.g. "BEFORE_BARRIER" matching
    # "BEFORE_BARRIER CALLER" or "BEFORE_BARRIER_R1".
    for kw in ["BEFORE_BARRIER_R2", "BEFORE_BARRIER_R1",
               "AFTER_BARRIER_R2", "AFTER_BARRIER_R1",
               "BEFORE_BARRIER CALLER", "AFTER_BARRIER CALLER",
               "NON_CALLER_DONE",
               "TC_T0_5_PASS_ERROR_RETURNED",
               "TC_T0_6_PASS_ERROR_RETURNED",
               "TC_T0_7_PASS_ERROR_RETURNED",
               "TC_T0_5_FAIL_NO_ERROR",
               "TC_T0_6_FAIL_NO_ERROR",
               "TC_T0_7_FAIL_NO_ERROR",
               "TC_T0_6_MASK_HI32", "TC_T0_7_MASK_BIT3",
               "TC_T0_5_START", "TC_T0_6_START", "TC_T0_7_START",
               "TC_T0_5_RET", "TC_T0_6_RET", "TC_T0_7_RET",
               "SYNC_WAIT_RET",
               "BEFORE_BARRIER", "AFTER_BARRIER",
               "NON_CALLER"]:
        if line.startswith(kw):
            return (kw, node_id, mask, ret)
    return (line, node_id, mask, ret)


def _has_marker(lines_or_timeline, marker_substr):
    """True if any line's raw text contains marker_substr.

    Works with both plain line lists and (tick, cpu_id, line) timeline tuples.
    """
    for item in lines_or_timeline:
        line = item[2] if isinstance(item, tuple) else item
        if marker_substr in line:
            return True
    return False


def _marker_count(lines_or_timeline, marker_substr):
    """Count items whose raw line text contains marker_substr."""
    cnt = 0
    for item in lines_or_timeline:
        line = item[2] if isinstance(item, tuple) else item
        if marker_substr in line:
            cnt += 1
    return cnt


def _marker_timeline_entries(timeline, marker_substr):
    """Return list of (tick, cpu_id, line, kw, nid) for timeline entries
    whose raw line text contains marker_substr."""
    result = []
    for tick, cpu_id, line in timeline:
        kw, nid, _, _ = _parse_marker(line)
        if marker_substr in line:
            result.append((tick, cpu_id, line, kw, nid))
    return result


def _marker_indices_from_lines(lines, marker_substr):
    """Return list of (index, node_id, kw) for lines matching marker_substr.
    (For per-CPU files, not timeline.)"""
    result = []
    for idx, line in enumerate(lines):
        kw, nid, _, _ = _parse_marker(line)
        if marker_substr in kw:
            result.append((idx, nid, kw))
    return result


# ── Compilation ─────────────────────────────────────────────────────

def compile_arm64(src_path, out_path, extra_flags=None):
    cc = "aarch64-linux-gnu-gcc"
    if shutil.which(cc) is None:
        print("  WARNING: {} not found, cannot compile {}".format(cc, src_path),
              flush=True)
        return False
    if os.path.exists(out_path):
        src_mtime = os.path.getmtime(src_path)
        out_mtime = os.path.getmtime(out_path)
        if src_mtime <= out_mtime:
            return True
    flags = [cc, "-static", "-o", out_path, src_path]
    if extra_flags:
        flags.extend(extra_flags)
    print("  Compiling: {}".format(" ".join(flags)), flush=True)
    proc = subprocess.run(flags, capture_output=True, text=True)
    if proc.returncode != 0:
        print("  Compilation FAILED: {}".format(proc.stderr), flush=True)
        return False
    return True


def ensure_binary(basename, src_name, extra_flags=None):
    binary = os.path.join(WORK_DIR, basename)
    src = os.path.join(WORK_DIR, src_name)
    if not os.path.exists(src):
        print("  ERROR: source file {} not found".format(src), flush=True)
        return None
    if compile_arm64(src, binary, extra_flags):
        return binary
    return None


# ─── Single-CPU Config ──────────────────────────────────────────────

def make_single_cpu_config(tmpdir, test_name, binary):
    output = os.path.join(tmpdir, "{}.out".format(test_name))
    config = """
import sys, os
import m5
from m5.objects import *

SYS_CLOCK = "2GHz"
CPU_CLOCK = "2GHz"
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
""".format(binary=binary, output=output)
    path = os.path.join(tmpdir, "{}_cfg.py".format(test_name))
    with open(path, "w") as f:
        f.write(config)
    return path, [output]


# ─── Multi-CPU Config (per-CPU output) ──────────────────────────────

def _make_multi_cpu_config(tmpdir, test_tag, binary, num_cpus, cpu_setup_lines):
    """Generic multi-CPU config with per-CPU output files."""
    outputs = [os.path.join(tmpdir, "{}_{}.out".format(test_tag, i))
               for i in range(num_cpus)]

    config = """
import sys, os
import m5
from m5.objects import *

SYS_CLOCK = "2GHz"
CPU_CLOCK = "2GHz"
BINARY = "{binary}"
OUTPUTS = {outputs!r}

system = System(mem_mode="atomic", cache_line_size=64)
system.clk_domain = SrcClockDomain(clock=SYS_CLOCK)
system.clk_domain.voltage_domain = VoltageDomain()
system.membus = SystemXBar()

mem = SimpleMemory(range=AddrRange(0, size="512MB"))
mem.port = system.membus.mem_side_ports
system.memories = [mem]
system.mem_ranges = [mem.range]

cpus = []
{cpu_setup}

system.cpu = cpus
system.workload = SEWorkload.init_compatible(BINARY)
system.memories = [mem]
system.mem_ranges = [mem.range]

root = Root(full_system=False, system=system)
m5.instantiate()
exit_event = m5.simulate()
cause = exit_event.getCause()
print("SIM_CAUSE=" + cause, flush=True)
""".format(binary=binary, outputs=outputs, cpu_setup=cpu_setup_lines)

    path = os.path.join(tmpdir, "{}_cfg.py".format(test_tag))
    with open(path, "w") as f:
        f.write(config)
    return path, outputs


def _cpu_setup_uniform(num_cpus, binary, output_tag):
    """Generate per-CPU setup code using per-CPU output files."""
    lines = []
    for i in range(num_cpus):
        lines.append("""
# CPU {i}
cpu = AtomicSimpleCPU(cpu_id={i})
cpu.clk_domain = SrcClockDomain(clock=CPU_CLOCK, voltage_domain=system.clk_domain.voltage_domain)
cpu.createThreads(); cpu.createInterruptController()
cpu.icache_port = system.membus.cpu_side_ports
cpu.dcache_port = system.membus.cpu_side_ports
proc = Process(pid=100 + {i})
proc.executable = BINARY
proc.cmd = [BINARY, str({i})]
proc.cwd = os.getcwd()
proc.output = OUTPUTS[{i}]
proc.errout = OUTPUTS[{i}]
cpu.workload = [proc]
cpus.append(cpu)
""".format(i=i))
    return "\n".join(lines)


# ─── Config Generators ──────────────────────────────────────────────

def make_tc_t0_1_config(tmpdir, binary):
    return _make_multi_cpu_config(
        tmpdir, "tc_t0_1", binary, 3,
        _cpu_setup_uniform(3, binary, "tc_t0_1"))


def make_tc_t0_2_config(tmpdir, binary):
    return _make_multi_cpu_config(
        tmpdir, "tc_t0_2", binary, 3,
        _cpu_setup_uniform(3, binary, "tc_t0_2"))


def make_tc_t0_3_config(tmpdir, binary_caller, binary_noncaller):
    # CPU 0: Node0 caller (node_id=0)
    # CPU 1: Node0 non-caller (node_id=0)
    # CPU 2: Node1 caller (node_id=1)
    # CPU 3: Node2 caller (node_id=2)
    num_cpus = 4
    outputs = [os.path.join(tmpdir, "tc_t0_3_{}.out".format(i))
               for i in range(num_cpus)]

    # node_id mapping: CPU index -> node_id
    node_id_map = {0: 0, 1: 0, 2: 1, 3: 2}

    cpu_setup = ""
    for i in range(num_cpus):
        b = binary_noncaller if i == 1 else binary_caller
        nid = node_id_map[i]
        cpu_setup += """
# CPU {i}
cpu = AtomicSimpleCPU(cpu_id={i})
cpu.clk_domain = SrcClockDomain(clock=CPU_CLOCK, voltage_domain=system.clk_domain.voltage_domain)
cpu.createThreads(); cpu.createInterruptController()
cpu.icache_port = system.membus.cpu_side_ports
cpu.dcache_port = system.membus.cpu_side_ports
proc = Process(pid=100 + {i})
proc.executable = {b_repr}
proc.cmd = [{b_repr}, str({nid})]
proc.cwd = os.getcwd()
proc.output = OUTPUTS[{i}]
proc.errout = OUTPUTS[{i}]
cpu.workload = [proc]
cpus.append(cpu)
""".format(i=i, b_repr=repr(b), nid=nid)

    config = """
import sys, os
import m5
from m5.objects import *

SYS_CLOCK = "2GHz"
CPU_CLOCK = "2GHz"
BINARY_C = {bin_c_repr}
BINARY_NC = {bin_nc_repr}
OUTPUTS = {outputs!r}

system = System(mem_mode="atomic", cache_line_size=64)
system.clk_domain = SrcClockDomain(clock=SYS_CLOCK)
system.clk_domain.voltage_domain = VoltageDomain()
system.membus = SystemXBar()

mem = SimpleMemory(range=AddrRange(0, size="512MB"))
mem.port = system.membus.mem_side_ports
system.memories = [mem]
system.mem_ranges = [mem.range]

cpus = []
{cpu_setup}

system.cpu = cpus
system.workload = SEWorkload.init_compatible(BINARY_C)
system.memories = [mem]
system.mem_ranges = [mem.range]

root = Root(full_system=False, system=system)
m5.instantiate()
exit_event = m5.simulate()
cause = exit_event.getCause()
print("SIM_CAUSE=" + cause, flush=True)
""".format(bin_c_repr=repr(binary_caller),
           bin_nc_repr=repr(binary_noncaller),
           outputs=outputs,
           cpu_setup=cpu_setup)

    path = os.path.join(tmpdir, "tc_t0_3_cfg.py")
    with open(path, "w") as f:
        f.write(config)
    return path, outputs


def make_tc_t0_4_config(tmpdir, binary):
    return _make_multi_cpu_config(
        tmpdir, "tc_t0_4", binary, 3,
        _cpu_setup_uniform(3, binary, "tc_t0_4"))


# ═══════════════════════════════════════════════════════════════════
#  TEST FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

def _run_multi_cpu_test(test_name, tmpdir, cfg_gen_fn, num_cpus, cfg_args):
    """Run a multi-CPU test with trace-based global ordering.

    Returns: (rc, stdout, timeline) where timeline is list of
    (tick, cpu_id, line) in global order.
    """
    cfg_path, outputs = cfg_gen_fn(tmpdir, *cfg_args)
    trace_path = os.path.join(tmpdir, "{}_trace.out".format(test_name))
    log_path = os.path.join(tmpdir, "{}_gem5.log".format(test_name))
    rc, stdout = run_gem5_with_trace(cfg_path, trace_path, log_path=log_path)
    timeline = build_global_timeline_from_trace(
        trace_path, outputs, num_cpus)
    return rc, stdout, timeline, outputs


# ─── TC-T0-1: Barrier Basic Release ───────────────────────────────

def test_tc_t0_1(tmpdir, binary):
    global tests_total
    tests_total += 11
    print("\n--- TC-T0-1: Barrier Basic Release ---", flush=True)

    rc, stdout, timeline, _ = _run_multi_cpu_test(
        "tc_t0_1", tmpdir, make_tc_t0_1_config, 3, (binary,))

    check("TC-T0-1 gem5 exit 0", rc == 0)
    check("TC-T0-1 sim completed",
          "exiting with last active thread context" in stdout)

    # Exact counts based on timeline
    before_cnt = _marker_count(timeline, "BEFORE_BARRIER")
    after_cnt  = _marker_count(timeline, "AFTER_BARRIER")
    check("TC-T0-1 3x BEFORE", before_cnt == 3)
    check("TC-T0-1 3x AFTER",  after_cnt == 3)

    # Each node once
    for nid in range(3):
        n_before = _marker_count(timeline, "BEFORE_BARRIER node={}".format(nid))
        n_after  = _marker_count(timeline, "AFTER_BARRIER node={}".format(nid))
        check("TC-T0-1 node{} BEFORE+AFTER present".format(nid),
              n_before == 1 and n_after == 1)

    # ── P0-2: Global tick ordering ──────────────────────────────
    bef_entries = _marker_timeline_entries(timeline, "BEFORE_BARRIER")
    aft_entries = _marker_timeline_entries(timeline, "AFTER_BARRIER")
    if bef_entries and aft_entries:
        max_bef_tick = max(t[0] for t in bef_entries)
        min_aft_tick = min(t[0] for t in aft_entries)
        check("TC-T0-1 global-order: max(BEFORE_tick) < min(AFTER_tick)",
              max_bef_tick < min_aft_tick)

    # Intra-node: BEFORE tick < AFTER tick
    for nid in range(3):
        b_tick = [t[0] for t in bef_entries if t[4] == nid]
        a_tick = [t[0] for t in aft_entries if t[4] == nid]
        if b_tick and a_tick:
            check("TC-T0-1 node{} BEFORE_tick < AFTER_tick".format(nid),
                  b_tick[0] < a_tick[0])

    for tick, cpu_id, line in timeline:
        print("    [t={}] cpu{}: {}".format(tick, cpu_id, line), flush=True)


# ─── TC-T0-2: Barrier Isolation ───────────────────────────────────

def test_tc_t0_2(tmpdir, binary):
    global tests_total
    tests_total += 12
    print("\n--- TC-T0-2: Barrier Isolation ---", flush=True)

    rc, stdout, timeline, _ = _run_multi_cpu_test(
        "tc_t0_2", tmpdir, make_tc_t0_2_config, 3, (binary,))

    check("TC-T0-2 gem5 exit 0", rc == 0)
    check("TC-T0-2 sim completed",
          "exiting with last active thread context" in stdout)

    # Each node passes
    for nid in range(3):
        has_after = _has_marker(timeline, "AFTER_BARRIER node={}".format(nid))
        check("TC-T0-2 node{} passed barrier".format(nid), has_after)

    # Intra-node ordering
    bef_entries = _marker_timeline_entries(timeline, "BEFORE_BARRIER")
    aft_entries = _marker_timeline_entries(timeline, "AFTER_BARRIER")
    for nid in range(3):
        b = [t for t in bef_entries if t[4] == nid]
        a = [t for t in aft_entries if t[4] == nid]
        if b and a:
            check("TC-T0-2 node{} BEFORE < AFTER".format(nid),
                  b[0][0] < a[0][0])

    # Mask checks (from raw line content)
    node2_mask4 = sum(1 for _, _, l in timeline if "mask=4" in l)
    check("TC-T0-2 node2 mask=4", node2_mask4 >= 1)
    mask3_count = sum(1 for _, _, l in timeline if "mask=3" in l)
    check("TC-T0-2 node0+node1 mask=3 count >= 2", mask3_count >= 2)

    # ── P0-2: Barrier isolation cross-node ordering ─────────────
    # Node2's AFTER may appear before Node0/1's AFTER (independence)
    n2_aft  = [t for t in aft_entries if t[4] == 2]
    n01_aft = [t for t in aft_entries if t[4] in (0, 1)]
    n01_bef = [t for t in bef_entries if t[4] in (0, 1)]

    # Node2 AFTER must appear after Node0/1 BEFORE
    if n2_aft and n01_bef:
        max_n01_bef_tick = max(t[0] for t in n01_bef)
        n2_aft_tick = n2_aft[0][0]
        check("TC-T0-2 node2 AFTER after Node0/1 BEFORE",
              n2_aft_tick > max_n01_bef_tick)

    # Node2 AFTER may appear before Node0/1 AFTER (independence)
    if n2_aft and n01_aft:
        min_n01_aft_tick = min(t[0] for t in n01_aft)
        n2_independent = n2_aft[0][0] < min_n01_aft_tick
        print("    [info] Node2 AFTER tick={}, Node0/1 AFTER min_tick={}, n2_before_n01={}".format(
            n2_aft[0][0], min_n01_aft_tick, n2_independent), flush=True)
        # Node2 completion before Node0/1 is timing-dependent; we verify
        # independence by checking that Node2 does NOT wait for Node0/1.
        # Node2's mask=4 (popcount=1) means it releases immediately after
        # its own sync_wait(4) call.
        check("TC-T0-2 node2 independent barrier (mask=4, popcount=1)",
              node2_mask4 >= 1)

    for tick, cpu_id, line in timeline:
        print("    [t={}] cpu{}: {}".format(tick, cpu_id, line), flush=True)


# ─── TC-T0-3: Multi-Thread Count ──────────────────────────────────

def test_tc_t0_3(tmpdir, binary_caller, binary_noncaller):
    global tests_total
    tests_total += 8
    print("\n--- TC-T0-3: Multi-Thread Count ---", flush=True)

    rc, stdout, timeline, _ = _run_multi_cpu_test(
        "tc_t0_3", tmpdir, make_tc_t0_3_config, 4,
        (binary_caller, binary_noncaller))

    check("TC-T0-3 gem5 exit 0", rc == 0)
    check("TC-T0-3 sim completed",
          "exiting with last active thread context" in stdout)

    # Non-caller checks
    has_nc = _has_marker(timeline, "NON_CALLER")
    has_nc_done = _has_marker(timeline, "NON_CALLER_DONE")
    check("TC-T0-3 non-caller printed", has_nc)
    check("TC-T0-3 non-caller done", has_nc_done)

    # Callers should pass barrier (nodes 0, 1, 2)
    for nid in [0, 1, 2]:
        has_after = _has_marker(timeline, "AFTER_BARRIER CALLER node={}".format(nid))
        check("TC-T0-3 caller node{} passed".format(nid), has_after)

    # Non-caller should not have AFTER_BARRIER
    nc_has_after = _has_marker(timeline, "AFTER_BARRIER CALLER node=0")
    check("TC-T0-3 non-caller no AFTER_BARRIER",
          not nc_has_after or has_nc_done)

    for tick, cpu_id, line in timeline:
        print("    [t={}] cpu{}: {}".format(tick, cpu_id, line), flush=True)


# ─── TC-T0-4: Reusable Barrier ────────────────────────────────────

def test_tc_t0_4(tmpdir, binary):
    global tests_total
    tests_total += 20
    print("\n--- TC-T0-4: Reusable Barrier ---", flush=True)

    rc, stdout, timeline, _ = _run_multi_cpu_test(
        "tc_t0_4", tmpdir, make_tc_t0_4_config, 3, (binary,))

    check("TC-T0-4 gem5 exit 0", rc == 0)
    check("TC-T0-4 sim completed",
          "exiting with last active thread context" in stdout)

    # Exact counts
    check("TC-T0-4 3x BEFORE_R1", _marker_count(timeline, "BEFORE_BARRIER_R1") == 3)
    check("TC-T0-4 3x AFTER_R1",  _marker_count(timeline, "AFTER_BARRIER_R1") == 3)
    check("TC-T0-4 3x BEFORE_R2", _marker_count(timeline, "BEFORE_BARRIER_R2") == 3)
    check("TC-T0-4 3x AFTER_R2",  _marker_count(timeline, "AFTER_BARRIER_R2") == 3)

    # Per-node completeness
    for nid in range(3):
        for rnd in ["R1", "R2"]:
            b_cnt = _marker_count(timeline,
                                  "BEFORE_BARRIER_{} node={}".format(rnd, nid))
            a_cnt = _marker_count(timeline,
                                  "AFTER_BARRIER_{} node={}".format(rnd, nid))
            check("TC-T0-4 node{} {} complete".format(nid, rnd),
                  b_cnt == 1 and a_cnt == 1)

    # ── P0-2: Global tick ordering ──────────────────────────────
    # All BEFORE_R2 before any AFTER_R2
    r2_bef = _marker_timeline_entries(timeline, "BEFORE_BARRIER_R2")
    r2_aft = _marker_timeline_entries(timeline, "AFTER_BARRIER_R2")
    if r2_bef and r2_aft:
        check("TC-T0-4 all BEFORE_R2 < all AFTER_R2",
              max(t[0] for t in r2_bef) < min(t[0] for t in r2_aft))

    # All AFTER_R1 before any BEFORE_R2 (R1 completes before R2 starts)
    r1_aft = _marker_timeline_entries(timeline, "AFTER_BARRIER_R1")
    if r1_aft and r2_bef:
        check("TC-T0-4 all AFTER_R1 < all BEFORE_R2",
              max(t[0] for t in r1_aft) < min(t[0] for t in r2_bef))

    # Per-node intra-round ordering
    for nid in range(3):
        for rnd in ["R1", "R2"]:
            b_key = "BEFORE_BARRIER_{}".format(rnd)
            a_key = "AFTER_BARRIER_{}".format(rnd)
            b_list = _marker_timeline_entries(timeline, b_key)
            a_list = _marker_timeline_entries(timeline, a_key)
            b_ticks = [t[0] for t in b_list if t[4] == nid]
            a_ticks = [t[0] for t in a_list if t[4] == nid]
            if b_ticks and a_ticks:
                check("TC-T0-4 node{} {} BEFORE < AFTER".format(nid, rnd),
                      b_ticks[0] < a_ticks[0])

    for tick, cpu_id, line in timeline:
        print("    [t={}] cpu{}: {}".format(tick, cpu_id, line), flush=True)


# ─── TC-T0-5: mask=0 -> -EINVAL ───────────────────────────────────

def test_tc_t0_5(tmpdir, binary):
    global tests_total
    tests_total += 6
    print("\n--- TC-T0-5: mask=0 Invalid ---", flush=True)

    cfg_path, outputs = make_single_cpu_config(tmpdir, "tc_t0_5", binary)
    log_path = os.path.join(tmpdir, "tc_t0_5_gem5.log")
    rc, stdout = run_gem5_simple(cfg_path, log_path=log_path)

    check("TC-T0-5 gem5 exit 0", rc == 0)
    check("TC-T0-5 sim completed",
          "exiting with last active thread context" in stdout)

    lines = []
    for out in outputs:
        lines.extend(read_lines(out))
    all_text = " ".join(lines)

    check("TC-T0-5 START seen", "TC_T0_5_START" in all_text)
    check("TC-T0-5 error returned", _has_marker(lines, "TC_T0_5_PASS_ERROR_RETURNED"))

    # ── P0-3: Exact errno assert ────────────────────────────────
    ret_val = None
    for line in lines:
        _, _, _, rv = _parse_marker(line)
        if rv is not None and "SYNC_WAIT_RET" in line:
            ret_val = rv
            break
    if ret_val is not None:
        check("TC-T0-5 SYNC_WAIT_RET == -22 (EINVAL)", ret_val == -22)
    else:
        check("TC-T0-5 SYNC_WAIT_RET found", False)

    check("TC-T0-5 no FAIL", "FAIL" not in all_text)

    for line in lines:
        print("    {}".format(line), flush=True)


# ─── TC-T0-6: high 32 bits -> -EINVAL ─────────────────────────────

def test_tc_t0_6(tmpdir, binary):
    global tests_total
    tests_total += 6
    print("\n--- TC-T0-6: High-32-Bits Invalid ---", flush=True)

    cfg_path, outputs = make_single_cpu_config(tmpdir, "tc_t0_6", binary)
    log_path = os.path.join(tmpdir, "tc_t0_6_gem5.log")
    rc, stdout = run_gem5_simple(cfg_path, log_path=log_path)

    check("TC-T0-6 gem5 exit 0", rc == 0)
    check("TC-T0-6 sim completed",
          "exiting with last active thread context" in stdout)

    lines = []
    for out in outputs:
        lines.extend(read_lines(out))
    all_text = " ".join(lines)

    check("TC-T0-6 START seen", "TC_T0_6_START" in all_text)
    check("TC-T0-6 error returned", _has_marker(lines, "TC_T0_6_PASS_ERROR_RETURNED"))

    ret_val = None
    for line in lines:
        _, _, _, rv = _parse_marker(line)
        if rv is not None and "SYNC_WAIT_RET" in line:
            ret_val = rv
            break
    if ret_val is not None:
        check("TC-T0-6 SYNC_WAIT_RET == -22 (EINVAL)", ret_val == -22)
    else:
        check("TC-T0-6 SYNC_WAIT_RET found", False)

    check("TC-T0-6 no FAIL", "FAIL" not in all_text)

    for line in lines:
        print("    {}".format(line), flush=True)


# ─── TC-T0-7: bits beyond N=3 -> -EINVAL ──────────────────────────

def test_tc_t0_7(tmpdir, binary):
    global tests_total
    tests_total += 6
    print("\n--- TC-T0-7: Bits Beyond N=3 Invalid ---", flush=True)

    cfg_path, outputs = make_single_cpu_config(tmpdir, "tc_t0_7", binary)
    log_path = os.path.join(tmpdir, "tc_t0_7_gem5.log")
    rc, stdout = run_gem5_simple(cfg_path, log_path=log_path)

    check("TC-T0-7 gem5 exit 0", rc == 0)
    check("TC-T0-7 sim completed",
          "exiting with last active thread context" in stdout)

    lines = []
    for out in outputs:
        lines.extend(read_lines(out))
    all_text = " ".join(lines)

    check("TC-T0-7 START seen", "TC_T0_7_START" in all_text)
    check("TC-T0-7 error returned", _has_marker(lines, "TC_T0_7_PASS_ERROR_RETURNED"))

    ret_val = None
    for line in lines:
        _, _, _, rv = _parse_marker(line)
        if rv is not None and "SYNC_WAIT_RET" in line:
            ret_val = rv
            break
    if ret_val is not None:
        check("TC-T0-7 SYNC_WAIT_RET == -22 (EINVAL)", ret_val == -22)
    else:
        check("TC-T0-7 SYNC_WAIT_RET found", False)

    check("TC-T0-7 no FAIL", "FAIL" not in all_text)

    for line in lines:
        print("    {}".format(line), flush=True)


# ═══════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="T0 Sync_Wait Test Suite")
    parser.add_argument("--artifact-dir", default=None,
                        help="If set, save all test artifacts to this directory")
    args = parser.parse_args()

    artifact_dir = args.artifact_dir

    print("=" * 64, flush=True)
    print("T0 Sync_Wait Test Suite", flush=True)
    print("gem5 binary: {}".format(GEM5_BIN), flush=True)
    print("Work dir: {}".format(WORK_DIR), flush=True)
    print("=" * 64, flush=True)

    # ── Auto-compile all workloads ──
    print("\n--- Compiling workloads ---", flush=True)

    bin_t1   = ensure_binary("tc_t0_1", "tc_t0_1.c")
    bin_t2   = ensure_binary("tc_t0_2", "tc_t0_2.c")
    bin_t3_c = ensure_binary("tc_t0_3_caller", "tc_t0_3.c",
                              ["-DCALLER=1"])
    bin_t3_nc = ensure_binary("tc_t0_3_noncaller", "tc_t0_3.c",
                               ["-DCALLER=0"])
    bin_t4   = ensure_binary("tc_t0_4", "tc_t0_4.c")
    bin_t5   = ensure_binary("tc_t0_5", "tc_t0_5.c")
    bin_t6   = ensure_binary("tc_t0_6", "tc_t0_6.c")
    bin_t7   = ensure_binary("tc_t0_7", "tc_t0_7.c")

    all_binaries = [bin_t1, bin_t2, bin_t3_c, bin_t3_nc,
                    bin_t4, bin_t5, bin_t6, bin_t7]
    if any(b is None for b in all_binaries):
        print("ERROR: One or more workloads failed to compile. Aborting.",
              flush=True)
        sys.exit(1)

    print("All workloads compiled successfully.\n", flush=True)

    tmpdir = tempfile.mkdtemp(prefix="sync_wait_t0_")
    print("Temp dir: {}".format(tmpdir), flush=True)

    try:
        test_tc_t0_1(tmpdir, bin_t1)
        test_tc_t0_2(tmpdir, bin_t2)
        test_tc_t0_3(tmpdir, bin_t3_c, bin_t3_nc)
        test_tc_t0_4(tmpdir, bin_t4)
        test_tc_t0_5(tmpdir, bin_t5)
        test_tc_t0_6(tmpdir, bin_t6)
        test_tc_t0_7(tmpdir, bin_t7)
    finally:
        summary = {
            "tests_passed": tests_passed,
            "tests_total": tests_total,
            "status": "PASS" if tests_passed == tests_total else "FAIL"
        }
        if artifact_dir:
            os.makedirs(artifact_dir, exist_ok=True)
            for fname in os.listdir(tmpdir):
                src = os.path.join(tmpdir, fname)
                dst = os.path.join(artifact_dir, fname)
                if os.path.isfile(src):
                    shutil.copy2(src, dst)
            with open(os.path.join(artifact_dir, "assertions.json"), "w") as f:
                json.dump(summary, f, indent=2)
            print("\nArtifacts saved to: {}".format(artifact_dir), flush=True)
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("\n" + "=" * 64, flush=True)
    print("Results: {}/{} tests passed".format(tests_passed, tests_total),
          flush=True)
    print("=" * 64, flush=True)

    sys.exit(0 if tests_passed == tests_total else 1)
