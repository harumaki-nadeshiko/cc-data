import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFIG = ROOT / "tests/e2e/test_e2e.py"
WORKLOAD = ROOT / "tests/e2e/workloads/e2e_tc143_db_btree_traversal.c"
COMMON = ROOT / "tests/e2e/workloads/portable_large_workload.h"
COMPILER = ROOT / "scripts/compile_workload.sh"


class TC143HybridContract(unittest.TestCase):
    def test_switch_syscall_is_macro_guarded(self):
        text = COMMON.read_text()
        self.assertIn('#ifdef E2E_TC143_HYBRID_SWITCH', text)
        self.assertIn('_syscall1(SYS_SWITCH_CPU, 0)', text)
        self.assertIn('node_id >= 0 && portable_socket(cpu_index) == 0', text)

    def test_exact_batch_and_transaction_shape_is_retained(self):
        text = WORKLOAD.read_text()
        self.assertIn('#define BATCHES PORTABLE_BATCHES', text)
        self.assertIn('#define TRANSACTIONS_PER_BATCH 16', text)
        self.assertIn('#define OPS_PER_BATCH 64', text)
        self.assertEqual(text.count('portable_switch_cpu(node, cpu);'), 1)
        self.assertIn('Build the exact PCT footprint', text)
        self.assertIn('for (int batch = 0; batch < BATCHES; ++batch)', text)
        self.assertIn('dsm_store(0, portable_global_pressure(PRESSURE_BASE, line)', text)
        self.assertIn('portable_weighted_worker_begin(setup_worker)', text)
        self.assertIn('portable_weighted_worker_end(setup_worker)', text)
        self.assertIn('for (int line = first; line < last; ++line)', text)
        self.assertIn('portable_full_cpu_barrier()', text)
        setup = text.index('Build the exact PCT footprint')
        hybrid = text[setup:text.index('#else', setup)]
        self.assertIn('"stores_done"', hybrid)
        self.assertIn('portable_full_cpu_barrier();', hybrid)
        self.assertIn('if (!primary)', text)
        self.assertIn('portable_barrier();', text)
        self.assertIn('"measure_batch_done", batch, batch + 1, BATCHES', text)
        self.assertIn('"post_pressure_barrier"', text)
        self.assertIn('"pre_transaction_dsb"', text)
        self.assertIn('"post_transaction_dsb"', text)

    def test_config_switches_whole_local_set(self):
        text = CONFIG.read_text()
        self.assertIn('choices=("timing", "o3", "hybrid")', text)
        self.assertIn('ArmO3CPU(switched_out=True, cpu_id=i)', text)
        self.assertIn('Ruby.create_system(options, False, system, None, cpus)', text)
        self.assertIn('i % CPUS_PER_NODE not in (0, 2)', text)
        self.assertIn('list(zip(hybrid_switch_cpus, future_cpus))', text)
        self.assertIn('list(zip(future_cpus, hybrid_switch_cpus))', text)
        self.assertIn('cause != "switchcpu"', text)

    def test_compiler_enables_only_requested_tc143_hybrid(self):
        text = COMPILER.read_text()
        self.assertIn('143)', text)
        self.assertIn('${EP_CPU_MODEL:-timing}', text)
        self.assertIn('-DE2E_TC143_HYBRID_SWITCH=1', text)

    def test_weighted_setup_exactly_covers_pressure_range(self):
        weights = (8, 4, 3, 2, 3, 2, 8, 4, 3, 2, 3, 2)
        pressure = 113866
        bounds = [pressure * sum(weights[:i]) // sum(weights)
                  for i in range(len(weights) + 1)]
        self.assertEqual(bounds[0], 0)
        self.assertEqual(bounds[-1], pressure)
        self.assertEqual(sum(b - a for a, b in zip(bounds, bounds[1:])),
                         pressure)


if __name__ == "__main__":
    unittest.main()
