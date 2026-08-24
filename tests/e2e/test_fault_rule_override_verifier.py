#!/usr/bin/env python3

import os
import unittest
from unittest.mock import patch

from tests.e2e.test_e2e import (
    _fault_manifest_from_rules,
    _verify_fault_events,
)


class FaultRuleOverrideVerifierTest(unittest.TestCase):
    RULE = (
        "tc148_q2_clear_drop_first_2:ClearReq:0:1:"
        "0x10018014800:drop::2"
    )

    def test_q2_rule_builds_compact_expectation(self):
        manifest = _fault_manifest_from_rules(148, self.RULE)
        self.assertEqual(2, manifest["rules"][0]["trigger_count"])
        self.assertEqual(0, manifest["rules"][0]["delivery_count"])
        self.assertTrue(manifest["checks"]["stable_reqid_per_rule"])

    def test_effective_q2_rule_replaces_tc148_default_matrix(self):
        lines = [
            "[UBFAULT-TRIGGER] rule='tc148_q2_clear_drop_first_2' "
            "action=Drop reqId=77 firedCount=1",
            "[UBFAULT-TRIGGER] rule='tc148_q2_clear_drop_first_2' "
            "action=Drop reqId=77 firedCount=2",
        ]
        with patch.dict(os.environ, {
                "E2E_EFFECTIVE_FAULT_RULES": self.RULE,
                "E2E_FAULT_MANIFEST": "",
        }, clear=False):
            passed, message = _verify_fault_events(
                148, lines, {"tc148_drop_0": "Drop"})
        self.assertTrue(passed, message)
        self.assertEqual(
            "trigger_count=2/2 delivery_count=0/0", message)

    def test_runner_override_environment_is_accepted_directly(self):
        lines = [
            "[UBFAULT-TRIGGER] rule='tc148_q2_clear_drop_first_2' "
            "action=Drop reqId=91 firedCount=1",
            "[UBFAULT-TRIGGER] rule='tc148_q2_clear_drop_first_2' "
            "action=Drop reqId=91 firedCount=2",
        ]
        with patch.dict(os.environ, {
                "E2E_EFFECTIVE_FAULT_RULES": "",
                "E2E_FAULT_RULES_OVERRIDE": self.RULE,
                "E2E_FAULT_MANIFEST": "",
        }, clear=False):
            passed, message = _verify_fault_events(148, lines, {})
        self.assertTrue(passed, message)

    def test_delay_count_requires_matching_deliveries(self):
        rule = "tc148_q3_clear_resp:ClearResp:1:0:0x10018014800:delay:20000:2"
        lines = [
            "[UBFAULT-TRIGGER] rule='tc148_q3_clear_resp' action=Delay",
            "[UBFAULT-TRIGGER] rule='tc148_q3_clear_resp' action=Delay",
            "[UBFAULT-DELIVER] rule='tc148_q3_clear_resp' action=Delay",
            "[UBFAULT-DELIVER] rule='tc148_q3_clear_resp' action=Delay",
        ]
        with patch.dict(os.environ, {
                "E2E_EFFECTIVE_FAULT_RULES": rule,
                "E2E_FAULT_MANIFEST": "",
        }, clear=False):
            passed, message = _verify_fault_events(148, lines, {})
        self.assertTrue(passed, message)


if __name__ == "__main__":
    unittest.main()
