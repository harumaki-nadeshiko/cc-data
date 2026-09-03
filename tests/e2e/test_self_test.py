"""Self-Test Runner — runs M4-M8 self-tests without ARM workload.

USAGE:
    gem5.opt tests/e2e/test_self_test.py

This config creates the full Ruby CHI system with EP endpoints but
does NOT run any ARM binary workload.  Self-tests execute during
m5.instantiate() → EPBackend::init() with enable_self_test=True
(the default).

The output is printed to stdout and can be parsed by CI to verify
M4-M8 self-test assertions pass.
"""

import sys, os, subprocess, argparse, re

# gem5 v25.1 SimObject hierarchy can be deep; increase recursion limit.
sys.setrecursionlimit(20000)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GEM5_BIN = os.path.join(SCRIPT_DIR, "../../gem5/build/ARM/gem5.opt")

# ── Self-test output markers (same as in M4-M8 SelfTest.cc) ────────
_RE_SELF_TEST = re.compile(
    r"\[SELF_TEST\]\s+(\S+)\s+(PASS|FAIL)(?:\s+(.*))?"
)
# Also match printf-based test output not using [SELF_TEST] tag
_RE_M4_MARKER = re.compile(r"M4.*(PASS|OK|FAIL)")
_RE_M5_MARKER = re.compile(r"M5.*(PASS|OK|FAIL)")
_RE_M6_MARKER = re.compile(r"M6.*(PASS|OK|FAIL)")
_RE_M7_MARKER = re.compile(r"M7.*(PASS|OK|FAIL)")
_RE_M8_MARKER = re.compile(r"M8.*(PASS|OK|FAIL)")


def gem5_config_main():
    import m5
    from m5.objects import (
        System, SrcClockDomain, VoltageDomain, RubySystem,
        Root, AddrRange,
    )

    gem5_root = os.path.dirname(os.path.dirname(os.path.dirname(GEM5_BIN)))
    configs_path = os.path.join(gem5_root, "configs")
    if configs_path not in sys.path:
        sys.path.insert(0, configs_path)

    from ruby.CHI_basic_framework_config import (
        DEFAULT_N, DEFAULT_L, DEFAULT_D, DEFAULT_SEG_SIZE, NodeConfig,
    )
    import ruby.CHI as chi_module
    from ruby.CHI_ubcc_framework import create_ubcc_system

    chi_module.create_system = create_ubcc_system

    NODES = DEFAULT_N
    TOTAL_CPUS = NODES * DEFAULT_L * DEFAULT_D

    # ── Minimal system for self-tests ───────────────────────────────
    root = Root(full_system=False)
    system = System(mem_mode="timing", cache_line_size=64)
    root.system = system
    system.clk_domain = SrcClockDomain(clock="2GHz")
    system.clk_domain.voltage_domain = VoltageDomain()

    # No CPUs needed — self-tests use C++ functional paths only.
    # But Ruby.create_system requires the cpu list for port binding.
    from m5.objects import TimingSimpleCPU
    cpus = []
    for i in range(TOTAL_CPUS):
        cpu = TimingSimpleCPU(cpu_id=i)
        cpu.clk_domain = SrcClockDomain(
            clock="2GHz",
            voltage_domain=system.clk_domain.voltage_domain)
        cpu.createThreads()
        cpu.createInterruptController()
        cpus.append(cpu)

    system.cpu = cpus

    # ── Options ──────────────────────────────────────────────────────
    class O: pass
    options = O()
    options.num_cpus = TOTAL_CPUS
    options.num_dirs = 1
    options.num_l3caches = NODES
    options.l3_size = "256kB"
    options.l3_assoc = 16
    options.cacheline_size = 64
    options.topology = "Crossbar"
    options.network = "simple"
    options.router_latency = 1
    options.router_link_latency = 1
    options.node_link_latency = 1
    options.enable_dvm = False
    options.chi_config = None
    options.access_backing_store = True
    options.enable_dram_powerdown = False
    options.protocol = "CHI"
    options.cpu_type = "TimingSimpleCPU"
    options.simple_physical_channels = []
    options.vcs_per_vnet = 1
    options.mesh_rows = 1
    options.routing_algorithm = 0
    options.garnet_deadlock_threshold = 50000
    options.xor_low_bit = 0
    options.network_fault_model = False
    options.cross_links = []
    options.cross_link_latency = 0
    options.mem_type = "SimpleMemory"
    options.mem_channels = 1
    options.mem_channels_intlv = 128
    options.link_latency = 1
    options.link_width_bits = 128
    options.numa_high_bit = 0

    # ── Skip FileSystemConfig (no workload I/O) ─────────────────────
    import common.FileSystemConfig as _fsc
    _fsc.config_filesystem = lambda *a, **kw: None

    # ── Pre-set mem_ranges ───────────────────────────────────────────
    _max_pa = (NODES - 1) * (1 << 40) + 5 * DEFAULT_SEG_SIZE
    system.mem_ranges = [AddrRange(0, size=_max_pa)]

    # ── Create Ruby system ───────────────────────────────────────────
    from ruby import Ruby
    Ruby.create_system(options, False, system, None, cpus)
    ruby_system = system.ruby

    if not ruby_system:
        print("FATAL: Ruby.create_system did not create system.ruby")
        sys.exit(1)

    cpu_sequencers = ruby_system._cpu_ports
    for i, seq in enumerate(cpu_sequencers):
        seq.connectCpuPorts(cpus[i])

    # ── Build per-node mem_ranges ────────────────────────────────────
    all_ranges = []
    for nid in range(NODES):
        cfg = NodeConfig(nid, NODES, DEFAULT_SEG_SIZE)
        all_ranges.extend(cfg.all_local_private_ranges())
        all_ranges.extend(cfg.all_metadata_private_ranges())
        all_ranges.extend(cfg.all_metadata_backstore_ranges())
        for hn in range(NODES):
            all_ranges.append(NodeConfig.dsm_range_for(hn, DEFAULT_SEG_SIZE, cfg.phy_base))
    system.mem_ranges = all_ranges

    # ── system.memories ──────────────────────────────────────────────
    from m5.objects import AbstractMemory
    _all_memories = [obj for obj in system.descendants()
                     if isinstance(obj, AbstractMemory)]
    if hasattr(ruby_system, 'phys_mem') and ruby_system.phys_mem:
        if ruby_system.phys_mem not in _all_memories:
            _all_memories.append(ruby_system.phys_mem)
    system.memories = _all_memories
    system._values['memories'] = _all_memories

    # ── Enable self-tests (default) ──────────────────────────────────
    # Self-tests are ON by default.  Explicitly verify for CI.
    for nid in range(NODES):
        be = getattr(ruby_system, f"ep_backend_node{nid}", None)
        if be:
            be.enable_self_test = True
    print("[SELF_TEST_RUNNER] enable_self_test = True on all EPBackend nodes",
          flush=True)

    print("=" * 60, flush=True)
    print("Self-Test Runner: M4-M8 self-tests (no workload)", flush=True)
    print(f"nodes={NODES}", flush=True)
    print("=" * 60, flush=True)

    # ── Instantiate — triggers EPBackend::init() → self-tests ──────
    m5.instantiate()

    # Self-tests execute during init().  No workload to simulate.
    # If self-tests fatal, we never reach here.
    print("[SELF_TEST_RUNNER] Instantiation complete.", flush=True)

    # Simulate 0 ticks just to flush any pending events
    exit_event = m5.simulate(0)
    cause = exit_event.getCause()
    print(f"[SELF_TEST_RUNNER] Simulate 0 ticks: cause={cause}", flush=True)

    # ── Parse self-test output ──────────────────────────────────────
    simout_path = os.path.join(m5.options.outdir, "simout")
    results = {"M4": False, "M5": False, "M6": False, "M7": False, "M8": False}
    failures = []

    if os.path.exists(simout_path):
        with open(simout_path, "r") as f:
            for line in f:
                line = line.strip()
                m = _RE_SELF_TEST.search(line)
                if m:
                    name = m.group(1)
                    status = m.group(2)
                    detail = m.group(3) or ""
                    if status == "PASS":
                        if "M4" in name:
                            results["M4"] = True
                        elif "M5" in name:
                            results["M5"] = True
                        elif "M6" in name:
                            results["M6"] = True
                        elif "M7" in name:
                            results["M7"] = True
                        elif "M8" in name:
                            results["M8"] = True
                        print(f"  [SELF_TEST] {name} PASS {detail}", flush=True)
                    else:
                        failures.append(f"{name}: {detail}")
                        print(f"  [SELF_TEST] {name} FAIL {detail}", flush=True)
                # Fallback: check marker patterns for older test output
                elif _RE_M4_MARKER.search(line):
                    if "FAIL" in line:
                        failures.append(f"M4: {line}")
                    else:
                        results["M4"] = True
                elif _RE_M5_MARKER.search(line):
                    if "FAIL" in line:
                        failures.append(f"M5: {line}")
                    else:
                        results["M5"] = True
                elif _RE_M6_MARKER.search(line):
                    if "FAIL" in line:
                        failures.append(f"M6: {line}")
                    else:
                        results["M6"] = True
                elif _RE_M7_MARKER.search(line):
                    if "FAIL" in line:
                        failures.append(f"M7: {line}")
                    else:
                        results["M7"] = True
                elif _RE_M8_MARKER.search(line):
                    if "FAIL" in line:
                        failures.append(f"M8: {line}")
                    else:
                        results["M8"] = True

    # ── Report ──────────────────────────────────────────────────────
    passed_cnt = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"\n{'='*60}")
    print(f"Self-Test Results: {passed_cnt}/{total} modules passed")
    for mod, ok in results.items():
        print(f"  {mod}: {'PASS' if ok else 'FAIL'}")
    if failures:
        print(f"\nFailures ({len(failures)}):")
        for f in failures:
            print(f"  {f}")
    print(f"{'='*60}")

    if passed_cnt == total and not failures:
        print("\n>>> ALL SELF-TESTS PASSED <<<")
        sys.exit(0)
    else:
        print("\n>>> SELF-TESTS FAILED <<<")
        sys.exit(1)


def runner_main():
    parser = argparse.ArgumentParser(description="EP Self-Test Runner")
    parser.add_argument("--outdir", default="m5out/self_test")
    args = parser.parse_args()

    if not os.path.exists(GEM5_BIN):
        print(f"ERROR: gem5 binary not found: {GEM5_BIN}")
        sys.exit(1)

    outdir = os.path.join(args.outdir)
    os.makedirs(outdir, exist_ok=True)

    cmd = [
        GEM5_BIN,
        f"--outdir={outdir}",
        os.path.abspath(__file__),
    ]
    print(f"  CMD: {' '.join(cmd)}", flush=True)
    env = os.environ.copy()
    lib_paths = ["/mnt/data1/cgc/miniconda3/lib", env.get("LD_LIBRARY_PATH", "")]
    env["LD_LIBRARY_PATH"] = ":".join(filter(None, lib_paths))
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=300, cwd=os.path.dirname(GEM5_BIN), env=env)

    # Print output for CI
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    for line in stdout.splitlines():
        print(f"[gem5] {line}", flush=True)
    if stderr:
        for line in stderr.splitlines():
            print(f"[gem5-err] {line}", flush=True)

    if proc.returncode == 0:
        print("\n>>> SELF-TEST RUNNER: PASS <<<")
    else:
        print(f"\n>>> SELF-TEST RUNNER: FAIL (exit={proc.returncode}) <<<")

    sys.exit(proc.returncode)


if __name__ == "__m5_main__":
    gem5_config_main()
elif __name__ == "__main__":
    runner_main()
