import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SYNC_WAIT = ROOT / "gem5/src/sim/sync_wait.cc"
ARM_SE = ROOT / "gem5/src/arch/arm/linux/se_workload.cc"


class SyncWaitEarlyReleaseContractTest(unittest.TestCase):
    def test_remote_release_waits_for_all_local_socket_threads(self):
        source = SYNC_WAIT.read_text(encoding="utf-8")
        block = source[source.index("if (bs.remoteReleased)"):
                       source.index("// Only fire BarrierReached")]
        self.assertIn("bs.waiting.size() >= bs.localExpected", block)
        self.assertIn("tc->suspend()", block)
        self.assertLess(
            block.index("bs.waiting.size() >= bs.localExpected"),
            block.index("bs.generation++"),
        )

    def test_matching_release_also_waits_for_all_local_threads(self):
        source = SYNC_WAIT.read_text(encoding="utf-8")
        block = source[source.index("void\nSyncWaitManager::releaseBarrier"):
                       source.index("} // namespace gem5")]
        self.assertIn("bs.remoteReleased = true", block)
        self.assertIn("bs.waiting.size() >= bs.localExpected", block)
        self.assertLess(
            block.index("bs.waiting.size() >= bs.localExpected"),
            block.index("bs.generation++"),
        )

    def test_arm_se_switch_syscall_exits_to_python(self):
        source = ARM_SE.read_text(encoding="utf-8")
        self.assertIn('exitSimLoopNow("switchcpu")', source)
        self.assertIn("[HYBRID-SWITCH-SYSCALL]", source)
        self.assertIn('{base + 437, "switch_cpu", switchCpuFunc}', source)
