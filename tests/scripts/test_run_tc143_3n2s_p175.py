import importlib.util
import json
import pathlib
import tempfile
import unittest


PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "run_tc143_3n2s_p175.py"
SPEC = importlib.util.spec_from_file_location("tc143_runner", PATH)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class FocusedRunnerTests(unittest.TestCase):
    def test_exact_contract(self):
        self.assertEqual((RUNNER.PRESSURE, RUNNER.TARGET, RUNNER.CAPACITY,
                          RUNNER.PCT, RUNNER.BATCHES),
                         (113866, 114688, 65536, 175, 32))
        self.assertLessEqual(RUNNER.OUTER_TIMEOUT, 3570)
        self.assertLessEqual(RUNNER.HARD_WALL, 3600)
        self.assertLess(RUNNER.INNER_TIMEOUT, RUNNER.OUTER_TIMEOUT)
        self.assertEqual(len(RUNNER.source_digest()), 64)
        self.assertIn("gem5/src/sim/sync_wait.cc", RUNNER.FINGERPRINT_FILES)

    def test_slots_reject_overlap(self):
        with self.assertRaises(ValueError):
            RUNNER.parse_slots(["0-11", "11-22"])
        self.assertEqual(RUNNER.parse_slots(["0-11", "12-23", "24-35"]),
                         ["0-11", "12-23", "24-35"])

    def test_role_options(self):
        self.assertEqual(RUNNER.role_config("naive")["policy"], "naive")
        self.assertEqual(RUNNER.role_config("spill")["opts"], "")
        ideal = RUNNER.role_config("ideal")["opts"]
        self.assertIn("--sram-bytes=2097152", ideal)
        self.assertIn("--ways=32", ideal)
        self.assertIn("--allow-oversized-resident-dir-for-test", ideal)

    def test_naive_runs_before_parallel_spill_and_ideal(self):
        source = PATH.read_text()
        self.assertIn('run_role(output, "naive", slots[0], update)', source)
        self.assertIn('iter(("spill", "ideal"))', source)
        self.assertIn('parser.add_argument("--roles"', source)

    def test_pressure_parser_exact_shape(self):
        line = ("[PORTABLE-PRESSURE] node=0 planes=6 hot_lines=822 "
                "pressure_lines=113866 total_unique_lines=114688 "
                "naive_capacity_lines=65536 target_footprint_lines=114688 "
                "pressure_level_pct=175 batches=32")
        self.assertEqual(RUNNER.PRESSURE_RE.findall(line)[0],
                         ("113866", "114688", "65536", "114688", "175", "32"))

    def test_progress_reads_run_private_simout(self):
        with tempfile.TemporaryDirectory(dir=RUNNER.ROOT / "build" / "runs") as tmp:
            run_dir = pathlib.Path(tmp)
            run_id = run_dir.name
            case = RUNNER.ROOT / "build" / "test-tc143-runner-case"
            try:
                case.mkdir(parents=True, exist_ok=True)
                (case / "role_manifest.json").write_text(json.dumps({
                    "container_name": run_id,
                }))
                simout = run_dir / "tc143/m5out/node0/simout_n0"
                simout.parent.mkdir(parents=True)
                simout.write_text(
                    "[WORKLOAD-PROGRESS] node=0 phase=pressure "
                    "event=stores_progress batch=0 batches=32 "
                    "completed=64 target=18978 milestone=2\n"
                )
                self.assertEqual(
                    RUNNER.latest_workload_progress(case)["completed"], 64
                )
                self.assertEqual(
                    RUNNER.latest_workload_progress(case)["event"],
                    "stores_progress",
                )
            finally:
                if case.exists():
                    for path in sorted(case.rglob("*"), reverse=True):
                        if path.is_file():
                            path.unlink()
                        elif path.is_dir():
                            path.rmdir()
                    case.rmdir()

    def test_retry_archives_stale_runtime_directory(self):
        source = PATH.read_text()
        self.assertIn('output / "attempts" / role', source)
        self.assertIn("shutil.move(str(case), str(archive))", source)
        self.assertIn("while time.monotonic() < deadline", source)


if __name__ == "__main__":
    unittest.main()
