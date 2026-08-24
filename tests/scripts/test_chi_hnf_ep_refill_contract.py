import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ACTIONS = (
    ROOT
    / "gem5/gem5/src/mem/ruby/protocol/chi/CHI-cache-actions.sm"
)
FUNCS = ROOT / "gem5/gem5/src/mem/ruby/protocol/chi/CHI-cache-funcs.sm"
EPSNF = ROOT / "gem5/gem5/src/mem/ruby/protocol/chi/ep/EPSNFController.cc"


def action_body(source, name):
    """Return one SLICC action using balanced braces, not formatting lines."""
    match = re.search(r"\baction\s*\(\s*" + re.escape(name) + r"\s*,", source)
    if not match:
        raise AssertionError("missing SLICC action: " + name)
    start = source.find("{", match.end())
    if start < 0:
        raise AssertionError("action has no body: " + name)

    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start + 1:index]
    raise AssertionError("unterminated SLICC action: " + name)


class ChiHnfEpRefillContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.body = action_body(
            ACTIONS.read_text(encoding="utf-8"),
            "Initiate_ReadUnique_HitUpstream",
        )

    def test_sole_ep_rnf_is_recognized_as_metadata_only(self):
        compact = re.sub(r"\s+", " ", self.body)
        self.assertRegex(
            compact,
            r"dir_sharers\.count\(\)\s*==\s*1.*"
            r"dir_sharers\.isElement\(tbe\.epRnfMachineID\)",
        )
        self.assertRegex(compact, r"is_HN\s*&&\s*!tbe\.dataValid")
        self.assertRegex(compact, r"!tbe\.dir_ownerExists")
        self.assertIn("[HNF-EP-UNIQUE-MISS-FALLBACK]", self.body)
        self.assertRegex(
            compact,
            r"dir_sharers\.remove\(\s*tbe\.epRnfMachineID\s*\)",
        )

    def test_fallback_fetches_shared_data_then_upgrades_before_completion(self):
        compact = re.sub(r"\s+", " ", self.body)
        self.assertRegex(
            compact,
            r"dir_sharers\.remove\(\s*tbe\.epRnfMachineID\s*\).*"
            r"actions\.push\(Event:ReadMissPipe\).*"
            r"actions\.push\(Event:SendReadNoSnpSharedData\).*"
            r"actions\.push\(Event:SendSnpCleanInvalid\).*"
            r"actions\.push\(Event:CompleteEPRNFReadUniqueUpgrade\).*"
            r"tbe\.actions\.push\(Event:WaitCompAck\);\s*"
            r"tbe\.actions\.pushNB\(Event:SendCompData\);.*"
            r"tbe\.actions\.push\(Event:CheckCacheFill\);",
        )

    def test_fallback_discards_stale_permission_metadata(self):
        compact = re.sub(r"\s+", " ", self.body)
        self.assertRegex(
            compact,
            r"dir_sharers\.remove\(\s*tbe\.epRnfMachineID\s*\).*"
            r"dataUnique\s*:=\s*false;.*"
            r"dataMaybeDirtyUpstream\s*:=\s*false;.*"
            r"actions\.push\(Event:ReadMissPipe\)",
        )

    def test_shared_fetch_sideband_is_preserved_on_retry(self):
        source = ACTIONS.read_text(encoding="utf-8")
        body = action_body(source, "Send_ReadNoSnpSharedData")
        compact = re.sub(r"\s+", " ", body)
        self.assertRegex(compact, r"ubcc_needed_perm\s*:=\s*0")
        self.assertRegex(compact, r"ubcc_write_intent\s*:=\s*false")
        self.assertRegex(compact, r"ubcc_publish_on_data\s*:=\s*true")
        self.assertRegex(compact, r"allowRequestRetry\(tbe, out_msg\)")

        funcs = FUNCS.read_text(encoding="utf-8")
        retry = re.search(
            r"void\s+prepareRequestRetry\s*\([^)]*\)\s*\{(.*?)\n\}",
            funcs,
            re.DOTALL,
        )
        self.assertIsNotNone(retry)
        retry_compact = re.sub(r"\s+", " ", retry.group(1))
        self.assertIn(
            "out_msg.ubcc_needed_perm := tbe.pendReqUbccNeededPerm;",
            retry_compact,
        )
        self.assertIn(
            "out_msg.ubcc_write_intent := tbe.pendReqUbccWriteIntent;",
            retry_compact,
        )
        self.assertIn(
            "out_msg.ubcc_publish_on_data := tbe.pendReqUbccPublishOnData;",
            retry_compact,
        )

    def test_ep_snf_publishes_after_final_data_beat(self):
        source = EPSNF.read_text(encoding="utf-8")
        compact = re.sub(r"\s+", " ", source)
        self.assertRegex(
            compact,
            r"i == dataMsgsPerLine - 1.*m_ubcc_publish_on_data.*"
            r"notifyLocalLinePublished\(linePa, _socketId\)",
        )
        self.assertRegex(
            compact,
            r"publishOnData = msg->m_ubcc_publish_on_data.*"
            r"notifyLocalLinePublished\(linePa, _socketId\)",
        )

    def test_upgrade_completion_handles_stale_without_granting_unique(self):
        source = ACTIONS.read_text(encoding="utf-8")
        body = action_body(source, "CompleteEPRNFReadUniqueUpgrade")
        compact = re.sub(r"\s+", " ", body)
        stale_branch, success_branch = compact.split("} else {", 1)
        self.assertIn("if (tbe.is_stale)", stale_branch)
        self.assertIn("tbe.dataValid := false", stale_branch)
        self.assertIn("pushFront(Event:SendReadNoSnp)", stale_branch)
        self.assertNotIn("tbe.dataUnique := true", stale_branch)
        self.assertIn("assert(tbe.dataValid)", success_branch)
        self.assertIn("assert(tbe.dataBlkValid.isFull())", success_branch)
        self.assertIn("tbe.dataUnique := true", success_branch)

    def test_completion_requires_a_complete_valid_line(self):
        source = ACTIONS.read_text(encoding="utf-8")
        body = action_body(source, "Send_CompData")
        compact = re.sub(r"\s+", " ", body)
        self.assertRegex(compact, r"assert\(tbe\.dataValid\)")
        self.assertRegex(compact, r"assert\(tbe\.dataBlkValid\.isFull\(\)\)")

    def test_recall_unique_completion_is_nonblocking(self):
        compact = re.sub(r"\s+", " ", self.body)
        self.assertRegex(
            compact,
            r"epProxyOp\s*==\s*EpProxyOp:RecallUnique\s*\)\s*\{\s*"
            r"tbe\.actions\.push\(Event:WaitCompAck\);\s*"
            r"tbe\.actions\.pushNB\(Event:SendCompUCResp\);",
        )

    def test_fallback_forces_non_dmt_operation(self):
        compact = re.sub(r"\s+", " ", self.body)
        self.assertRegex(compact, r"tbe\.use_DMT\s*:=\s*false")
        self.assertNotIn("SendReadNoSnpDMT", self.body)

    def test_read_unique_miss_recall_completion_is_also_nonblocking(self):
        source = ACTIONS.read_text(encoding="utf-8")
        body = action_body(source, "Initiate_ReadUnique_Miss")
        compact = re.sub(r"\s+", " ", body)
        self.assertRegex(
            compact,
            r"epProxyOp\s*==\s*EpProxyOp:RecallUnique\s*\)\s*\{\s*"
            r"tbe\.actions\.push\(Event:WaitCompAck\);\s*"
            r"tbe\.actions\.pushNB\(Event:SendCompUCResp\);",
        )


if __name__ == "__main__":
    unittest.main()
