import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "tests/e2e/run_multi.sh"
TC143 = ROOT / "tests/e2e/workloads/e2e_tc143_db_btree_traversal.c"
PORTABLE = ROOT / "tests/e2e/workloads/portable_large_workload.h"
UBCC = ROOT / "modules/ubiomodule/UBCCController.cc"


class E2EProgressWatchdogContractTest(unittest.TestCase):
    def test_tc143_reports_bounded_pressure_progress(self):
        source = TC143.read_text(encoding="utf-8")
        compact = " ".join(source.split())
        self.assertIn('"batch_begin"', source)
        self.assertIn('"stores_progress"', source)
        self.assertIn('"stores_done"', source)
        self.assertIn('"batch_done"', source)
        self.assertRegex(compact, r"batch_completed == 64.*batch_completed == 128")
        self.assertRegex(compact, r"batch_completed == 256.*batch_completed % 512")

    def test_progress_marker_has_completed_and_target(self):
        source = PORTABLE.read_text(encoding="utf-8")
        self.assertIn("[WORKLOAD-PROGRESS]", source)
        self.assertIn('PORTABLE_PROGRESS_TEXT(" completed=")', source)
        self.assertIn('PORTABLE_PROGRESS_TEXT(" target=")', source)
        self.assertIn("portable_pressure_plane_target", source)

    def test_supervisor_uses_slowest_reporting_node(self):
        source = RUNNER.read_text(encoding="utf-8")
        body = source[source.index("_aggregate_workload_progress()"):
                      source.index("# ── Supervisor", source.index(
                          "_aggregate_workload_progress()"))]
        self.assertIn("slow_completed", body)
        self.assertIn("node_completed * slow_target", body)
        self.assertIn("slow_completed * node_target", body)
        self.assertNotIn("completed=$((completed + node_completed))", body)

    def test_heartbeat_does_not_reset_workload_stall(self):
        source = RUNNER.read_text(encoding="utf-8")
        workload_block = source[source.index(
            'if [ "$workload_reporting" -gt 0 ]'):
            source.index('prev_guest_progress=', source.index(
                'if [ "$workload_reporting" -gt 0 ]'))]
        self.assertIn('if [ "$workload_changed" -eq 1 ]', workload_block)
        self.assertNotIn("protocol_changed", workload_block)
        self.assertIn("generic_stall_count", source)
        self.assertIn(
            'if [ "$guest_changed" -eq 1 ] || [ "$protocol_changed" -eq 1 ]',
            source,
        )

    def test_completed_workload_disables_pressure_stall(self):
        source = RUNNER.read_text(encoding="utf-8")
        self.assertIn(
            '[ "$workload_completed" -lt "$workload_target" ]', source
        )
        self.assertIn("useful_stall_count=0", source)

    def test_eta_is_calibrated_from_workload_start(self):
        source = RUNNER.read_text(encoding="utf-8")
        self.assertIn("workload_start_wall", source)
        self.assertIn("workload_start_completed", source)
        self.assertIn("EP_SUPERVISOR_ETA_CALIBRATION_SEC", source)
        self.assertIn("eta_over_budget_count", source)
        self.assertIn('eta_over_budget_count" -ge 2', source)
        self.assertIn("infeasible_eta", source)

    def test_protocol_tick_compared_before_previous_value_update(self):
        source = RUNNER.read_text(encoding="utf-8")
        compare = source.index(
            '[ "$current_protocol_tick" -gt "$prev_protocol_tick" ]')
        update = source.index('prev_protocol_tick="$current_protocol_tick"')
        self.assertLess(compare, update)

    def test_successful_async_writeback_logging_is_sampled(self):
        source = UBCC.read_text(encoding="utf-8")
        self.assertIn("_asyncWbCount <= 16", source)
        self.assertIn("(_asyncWbCount % 1024) == 0", source)
        self.assertIn("dirty kept (entry modified)", source)

    def test_supervisor_faults_kill_full_pid_trees(self):
        source = RUNNER.read_text(encoding="utf-8")
        supervisor = source[source.index("_supervisor_start()"):
                            source.index("_supervisor_stop()")]
        self.assertIn('_kill_pid_tree "$pid"', supervisor)
        self.assertNotIn('kill -9 "$pid"', supervisor)


if __name__ == "__main__":
    unittest.main()
