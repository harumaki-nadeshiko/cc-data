#!/usr/bin/env python3
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "modules/ubiomodule/ubio_main.cc"


class BarrierForwardReliabilityContractTest(unittest.TestCase):
    def test_nonleader_arrival_uses_reliable_network_queue(self):
        source = SOURCE.read_text(encoding="utf-8")
        start = source.index(
            "if (coh->h.type == CoherenceMessageType::BarrierReached)"
        )
        end = source.index('LogDebug("UBIO", "[ubio:{}]', start)
        block = source[start:end]

        self.assertIn(
            "sendNetworkResponse(\n                            *coh, "
            "gidOf(leaderNode, leaderSocket));",
            block,
        )
        self.assertNotIn("AllocateSendMessage(netPort", block)
        self.assertNotIn("SendMessage(netPort", block)

    def test_reliable_queue_retries_until_send_succeeds(self):
        source = SOURCE.read_text(encoding="utf-8")
        start = source.index("auto sendNetworkResponse")
        end = source.index("using BarrierKey", start)
        block = source[start:end]

        self.assertIn(
            "reliableResponses.push_back({response, targetGid, 1});", block
        )
        self.assertIn("auto drainReliableResponses", block)
        self.assertIn("reliableResponses.pop_front();", block)
        self.assertIn(
            "if (netPort && !netDone) drainReliableResponses();", source
        )


if __name__ == "__main__":
    unittest.main()
